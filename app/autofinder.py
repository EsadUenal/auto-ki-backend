from __future__ import annotations

"""
AutoFinder — Runde 1: deterministisches Ranking-Fundament.

"Welches Auto passt zu mir?" — kostenlos, ohne Tavily, ohne Gemini, ohne
Marktpreis-Provider. Diese Datei ist bewusst NUR die Engine (Filter → Dedupe
→ Score → Diversität → Anreicherung); es gibt in dieser Runde weder Router
noch Frontend (siehe Scope in der Produktspezifikation, Abschnitt 20).

GRUNDREGEL (§7/§8 der Produktspezifikation)
--------------------------------------------
"Missing != gut." Ein fehlender Wert gibt NIE einen Punktbonus — weder
direkt (z.B. fehlender Verbrauch) noch indirekt über Abwesenheit negativer
Fakten (keine Schwachstelle eingetragen heißt NICHT "zuverlässig"). Der
DATA-TRUTH-AUDIT einer früheren Sitzung hat belegt, dass 62 von 416
Baureihen schlicht KEINE Schwachstelle in der DB tragen — das ist ein
Pflegezustand, keine Eigenschaft des Fahrzeugs.

Deshalb gibt es KEIN Zuverlässigkeits-Ranking und KEIN Label wie "sehr
zuverlässig" (§8). Verifizierte Schwachstellen/Rückrufe erscheinen nur als
informative `trade_offs`, nie als Score-Faktor.

TRUST (§10)
-----------
Baureihenspezifische Fakten (Schwachstellen, Rückrufe) werden ausschließlich
über `app.database.get_baureihe()` gelesen — die einzige Leseroute, die
`sichtbare_fakten()` (entfernt `rejected`) und die Einzelfakt-Verifikation
(`_trust`) anwendet. Es gibt hier keine zweite, ungefilterte SQL-Abkürzung
auf `schwachstelle_baureihe`/`rueckruf`.

Kern-Fahrzeugdaten (Kraftstoff, Leistung, Getriebe, Karosserie, Baujahr) sind
KEINE Fakten mit Verifikationsstatus in diesem Sinn — sie tragen keinen
`fakt_verifikation`-Eintrag (siehe `db/schema.sql`) und werden deshalb direkt
aus `motorvariante`/`baureihe` gelesen.

VORBEREITET, ABER NICHT AUSGEWERTET (§4/§12/§13/§15)
------------------------------------------------------
`AutoFinderRequest` und `AutoFinderKandidat` enthalten bereits Felder für
Budget, Kilometerstand, Web-Ergänzung, Marktpreis und Bild-Identität — die
heutige DB trägt dafür keine belastbaren Fakten (kein Gebrauchtmarktpreis,
keine Kilometerangabe je Baureihe, keine Bildquelle). Diese Felder werden in
Runde 1 NICHT gefiltert, NICHT gescort und NICHT befüllt. Das ist bewusst
sichtbar dokumentiert statt still weggelassen, damit spätere Runden die
Architektur erweitern statt umbauen müssen.
"""

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Literal

from app.autofinder_norm import (
    normalisiere_getriebe,
    normalisiere_karosserie,
    normalisiere_segment,
)
from app.database import get_baureihe, get_conn

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# REQUEST — §4 der Produktspezifikation
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class AutoFinderRequest:
    """Nutzereingaben. Felder sind additiv vorbereitet — nicht jedes Feld
    wirkt bereits in Runde 1 (siehe Modul-Docstring)."""

    # ---- BASIS ----
    # Budget/Kilometer: die DB hat keinen Gebrauchtmarktpreis und keine
    # Kilometerangabe je Baureihe. NICHT gefiltert, NICHT gescort (§4/§13).
    budget_min: int | None = None
    budget_max: int | None = None
    baujahr_von: int | None = None
    baujahr_bis: int | None = None
    kilometer_max: int | None = None

    # ---- FAHRZEUG (harte Filter, §5) ----
    # Produktentscheidung: `marken_bevorzugt` wirkt in Runde 1 als HARTER
    # Include-Filter (§5 nennt "Marke include/exclude" explizit als Hard
    # Filter) — nicht als weicher Score-Bonus. Wird eine spätere Runde daraus
    # eine reine Präferenz machen wollen, ist das eine bewusste Änderung
    # dieses Verhaltens, kein Bugfix.
    marken_bevorzugt: list[str] = field(default_factory=list)
    marken_ausschliessen: list[str] = field(default_factory=list)
    karosserie: list[str] = field(default_factory=list)   # normalisierte Klassen, siehe autofinder_norm
    kraftstoff: list[str] = field(default_factory=list)   # DB-Vokabular: Benzin/Diesel/Elektro/Plug-in-Hybrid/Mild-Hybrid
    getriebe: list[str] = field(default_factory=list)     # "automatik" | "manuell"
    leistung_min_ps: int | None = None
    leistung_max_ps: int | None = None
    antrieb: list[str] = field(default_factory=list)      # DB-Vokabular: Front/Heck/Allrad

    # ---- NUTZUNG (steuert Score, §7 — kein Hard Filter) ----
    nutzung: Literal["stadt", "gemischt", "langstrecke"] | None = None
    # km_pro_jahr ist fürs Diesel-Kurzstrecke-Signal vorgesehen (P1-6 aus dem
    # Audit), wird in Runde 1 NICHT ausgewertet — reine Vorbereitung.
    km_pro_jahr: int | None = None

    # ---- PRIORITÄTEN (additive Score-Gewichte, §7) ----
    sportlich: bool = False
    sparsam: bool = False          # nur wirksam, wenn Verbrauch tatsächlich vorhanden (§7)
    fahranfaenger: bool = False
    # praktisch/komfortabel/familie: DB trägt weder Kofferraum- noch
    # Sitzplatz- noch Komfortfelder. Bewusst OHNE Wirkung in Runde 1 (§4) —
    # kein Rateersatz.
    praktisch: bool = False
    komfortabel: bool = False
    familie: bool = False


# ══════════════════════════════════════════════════════════════════════════
# ERGEBNIS — §16 der Produktspezifikation
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class AutoFinderKandidat:
    baureihe_id: str
    variante_id: str
    marke: str
    modell: str
    generation: str
    motor_bezeichnung: str
    baujahr_von: int | None
    baujahr_bis: int | None
    leistung_ps: int | None
    kraftstoff: str
    getriebe_klassen: list[str]
    antrieb: str | None
    karosserie_klassen: list[str]

    match_score: float
    match_gruende: list[str] = field(default_factory=list)
    # Getrennt vom match_score (§9) — Anteil befüllter Kernfelder, 0.0–1.0.
    datenqualitaet: float = 0.0

    trade_offs: list[str] = field(default_factory=list)

    # ---- Vorbereitung für spätere Runden (§12/§13/§15) — in Runde 1 immer
    # der hier gezeigte Leerzustand, nie befüllt oder ausgewertet. ----
    source_type: Literal["internal_db", "web_discovered"] = "internal_db"
    market_price_min: int | None = None
    market_price_max: int | None = None
    market_price_median: int | None = None
    market_data_quality: str | None = None
    market_sample_size: int | None = None
    # Stabiler Identifier für eine spätere Bildzuordnung — bewusst aus
    # Marke/Modell/Generation gebildet (nicht aus der AUTOINCREMENT-ID),
    # damit ein Re-Export der Baureihen-Seed-Daten (siehe
    # db/export_fahrzeug_seed.py) den Schlüssel nicht verschiebt.
    visual_key: str = ""


# ══════════════════════════════════════════════════════════════════════════
# KANDIDATENBASIS LADEN
# ══════════════════════════════════════════════════════════════════════════

_KERNFELDER_ANZAHL = 10  # siehe _datenqualitaet() — für Kommentar/Doku synchron halten


def _lade_rohkandidaten(conn: sqlite3.Connection) -> list[dict]:
    """EIN Join über baureihe+motorvariante — kein N+1 (§19 Performance).

    Enthält bewusst nur Spalten ohne eigenen Verifikationsstatus (siehe
    Modul-Docstring "TRUST"). Schwachstellen/Rückrufe kommen erst in
    `_reichere_an()` für die finalen Kandidaten über `get_baureihe()` dazu.
    """
    sql = """
        SELECT
            b.id AS baureihe_id, b.marke, b.modell, b.generation,
            b.bauzeitraum_von, b.bauzeitraum_bis, b.karosserie, b.segment,
            b.euro_ncap_sterne,
            m.variante_id, m.bezeichnung, m.motorcode, m.kraftstoff,
            m.leistung_ps, m.drehmoment_nm, m.getriebe, m.antrieb,
            m.beschleunigung_0_100, m.verbrauch_wltp, m.verbrauch_real
        FROM motorvariante m
        JOIN baureihe b ON b.id = m.baureihe_id
    """
    return [dict(r) for r in conn.execute(sql).fetchall()]


# Kurzlebiger In-Memory-Cache für die normalisierte Kandidatenbasis — dasselbe
# Muster wie `app.database._cached_alle` (dort für baureihe/motorvariante-Kurz-
# abfragen). Die Normalisierung (Karosserie/Getriebe/Segment über alle 3231
# Motorvarianten) ist der teuerste Teil einer Suche (~50-65ms); ein 60s-TTL
# spart das bei jeder Suche innerhalb desselben Fensters, ohne dass Nutzer je
# einen veralteten Stand sehen (Admin-Schreibvorgänge sollten zusätzlich
# `invalidate_kandidatenbasis_cache()` aufrufen, analog
# `database.invalidate_referenzdaten_cache()`).
_KANDIDATENBASIS_CACHE_TTL_S = 60.0
_kandidatenbasis_cache: tuple[float, list[dict]] | None = None


def _lade_kandidatenbasis_gecacht(conn: sqlite3.Connection) -> list[dict]:
    global _kandidatenbasis_cache
    now = time.monotonic()
    if _kandidatenbasis_cache is not None and (now - _kandidatenbasis_cache[0]) < _KANDIDATENBASIS_CACHE_TTL_S:
        return _kandidatenbasis_cache[1]
    roh = [_annotiere_normalisierung(r) for r in _lade_rohkandidaten(conn)]
    _kandidatenbasis_cache = (now, roh)
    return roh


def invalidate_kandidatenbasis_cache() -> None:
    """Nach Admin-Schreibvorgängen an Baureihen/Motorvarianten aufrufen, damit
    die nächste AutoFinder-Suche sofort den aktuellen Stand sieht statt bis zu
    60s zu warten. Analog `database.invalidate_referenzdaten_cache()`."""
    global _kandidatenbasis_cache
    _kandidatenbasis_cache = None


def _annotiere_normalisierung(roh: dict) -> dict:
    """Hängt die einmalig berechneten Normalisierungs-Sets an — wird
    NICHT persistiert, nur für die Dauer einer Suche im Speicher gehalten."""
    roh["_karo"] = normalisiere_karosserie(roh.get("karosserie"))
    roh["_segment"] = normalisiere_segment(roh.get("segment"))
    roh["_getriebe"] = normalisiere_getriebe(roh.get("getriebe"))
    v = roh.get("verbrauch_wltp")
    roh["_verbrauch"] = v if v is not None else roh.get("verbrauch_real")
    return roh


# ══════════════════════════════════════════════════════════════════════════
# HARTE FILTER — §5
# ══════════════════════════════════════════════════════════════════════════

def _baujahr_ueberschneidet(roh: dict, req: AutoFinderRequest) -> bool:
    cand_von = roh.get("bauzeitraum_von")
    if cand_von is None:
        return False  # kein Baujahr bekannt -> kein belastbarer Treffer
    cand_bis = roh.get("bauzeitraum_bis")
    cand_bis = cand_bis if cand_bis is not None else 9999  # None = noch aktuell
    req_von = req.baujahr_von if req.baujahr_von is not None else -9999
    req_bis = req.baujahr_bis if req.baujahr_bis is not None else 9999
    return cand_von <= req_bis and cand_bis >= req_von


def erfuellt_harte_filter(roh: dict, req: AutoFinderRequest) -> bool:
    """Alle Bedingungen sind UND-verknüpft. Ein leeres Filterfeld heißt
    "keine Einschränkung" — ein NICHT klassifizierbarer Kandidat (leeres
    Normalisierungs-Set) wird bei aktivem Filter ausgeschlossen, nie geraten
    (§3/§5)."""
    marke_l = (roh.get("marke") or "").strip().lower()

    if req.marken_ausschliessen and marke_l in {m.strip().lower() for m in req.marken_ausschliessen}:
        return False

    if req.marken_bevorzugt and marke_l not in {m.strip().lower() for m in req.marken_bevorzugt}:
        return False

    if req.kraftstoff:
        gewuenscht = {k.strip().lower() for k in req.kraftstoff}
        if (roh.get("kraftstoff") or "").strip().lower() not in gewuenscht:
            return False

    if req.getriebe:
        gewuenscht = {g.strip().lower() for g in req.getriebe}
        if not (roh["_getriebe"] & gewuenscht):
            return False

    if req.karosserie:
        gewuenscht = {k.strip().lower() for k in req.karosserie}
        if not (roh["_karo"] & gewuenscht):
            return False

    if req.antrieb:
        gewuenscht = {a.strip().lower() for a in req.antrieb}
        if (roh.get("antrieb") or "").strip().lower() not in gewuenscht:
            return False

    if req.leistung_min_ps is not None:
        ps = roh.get("leistung_ps")
        if ps is None or ps < req.leistung_min_ps:
            return False

    if req.leistung_max_ps is not None:
        ps = roh.get("leistung_ps")
        if ps is None or ps > req.leistung_max_ps:
            return False

    if req.baujahr_von is not None or req.baujahr_bis is not None:
        if not _baujahr_ueberschneidet(roh, req):
            return False

    return True


# ══════════════════════════════════════════════════════════════════════════
# DEDUPE — §6
# ══════════════════════════════════════════════════════════════════════════

def _dedupe_schluessel(roh: dict) -> tuple:
    """Gruppiert Ausstattungs-/Trimlinien derselben Motorisierung
    (z.B. "C220 d" / "C220 d AMG Line" / "C220 d Avantgarde") zu EINEM
    Kandidaten. Antrieb UND Getriebeklasse sind Teil des Schlüssels, damit
    z.B. "320d" und "320d xDrive" (unterschiedlicher Antrieb) NICHT
    zusammenfallen — das wäre eine fachlich unterschiedliche Variante."""
    return (
        roh["baureihe_id"], roh.get("leistung_ps"), roh.get("kraftstoff"),
        roh.get("antrieb"), tuple(sorted(roh["_getriebe"])),
    )


def dedupe_kandidaten(gefiltert: list[dict]) -> list[dict]:
    gruppen: dict[tuple, list[dict]] = {}
    for roh in gefiltert:
        gruppen.setdefault(_dedupe_schluessel(roh), []).append(roh)

    ergebnis = []
    for gruppe in gruppen.values():
        if len(gruppe) == 1:
            ergebnis.append(gruppe[0])
            continue
        # höchste Datenqualität gewinnt; stabiler Tie-Break über variante_id,
        # damit dieselbe Suche IMMER dieselbe Repräsentanten-Wahl trifft (§11 M).
        beste = sorted(
            gruppe,
            key=lambda r: (-_datenqualitaet(r), r["variante_id"]),
        )[0]
        ergebnis.append(beste)
    return ergebnis


# ══════════════════════════════════════════════════════════════════════════
# DATENQUALITÄT — §9 (getrennt vom Match-Score)
# ══════════════════════════════════════════════════════════════════════════

def _datenqualitaet(roh: dict) -> float:
    kernfelder = (
        roh.get("motorcode"),
        roh.get("leistung_ps"),
        roh.get("drehmoment_nm"),
        roh.get("antrieb"),
        roh.get("bauzeitraum_von"),
        roh.get("kraftstoff"),
        roh["_verbrauch"],
        roh.get("beschleunigung_0_100"),
        roh.get("getriebe") if roh["_getriebe"] else None,
        next(iter(roh["_karo"]), None),
    )
    assert len(kernfelder) == _KERNFELDER_ANZAHL
    befuellt = sum(1 for f in kernfelder if f not in (None, "", []))
    return befuellt / _KERNFELDER_ANZAHL


# ══════════════════════════════════════════════════════════════════════════
# MATCH-SCORE — §7 (additiv, nur aus vorhandenen Fakten, "Missing != gut")
# ══════════════════════════════════════════════════════════════════════════

def _score_kandidat(roh: dict, req: AutoFinderRequest) -> tuple[float, list[str]]:
    score = 0.0
    gruende: list[str] = []

    if req.nutzung == "langstrecke":
        if roh.get("kraftstoff") in ("Diesel", "Plug-in-Hybrid"):
            score += 2
            gruende.append(f"{roh['kraftstoff']} eignet sich für Langstrecke")
        drehmoment = roh.get("drehmoment_nm")
        if drehmoment is not None and drehmoment >= 350:
            score += 2
            gruende.append(f"{drehmoment} Nm Drehmoment — souverän auf der Langstrecke")
        if roh["_verbrauch"] is not None:
            score += 1
            gruende.append(f"Verbrauch belegt ({roh['_verbrauch']:.1f} l/100km)")
    elif req.nutzung == "stadt":
        if roh["_karo"] & {"kleinwagen", "kompakt"}:
            score += 2
            gruende.append("Kompakte Klasse — wendig im Stadtverkehr")
        if roh.get("kraftstoff") in ("Benzin", "Elektro", "Mild-Hybrid"):
            score += 1
            gruende.append(f"{roh['kraftstoff']} passend für überwiegend kurze Strecken")
    # "gemischt": bewusst neutral (0 Punkte) — keine erfundene Präferenz.

    if req.sportlich:
        ps = roh.get("leistung_ps")
        if ps is not None and ps >= 250:
            score += 2
            gruende.append(f"Hohe Leistung ({ps} PS)")
        b100 = roh.get("beschleunigung_0_100")
        if b100 is not None and b100 <= 6.5:
            score += 2
            gruende.append(f"0–100 km/h in {b100:.1f}s")
        drehmoment = roh.get("drehmoment_nm")
        if drehmoment is not None and drehmoment >= 400:
            score += 1
            gruende.append(f"Hohes Drehmoment ({drehmoment} Nm)")

    if req.sparsam:
        # §7: NUR wenn Verbrauch tatsächlich vorhanden — kein Bonus bei Lücke.
        if roh["_verbrauch"] is not None:
            if roh["_verbrauch"] <= 5.5:
                score += 3
                gruende.append(f"Niedriger Verbrauch ({roh['_verbrauch']:.1f} l/100km)")
            elif roh["_verbrauch"] <= 7.0:
                score += 1
                gruende.append(f"Moderater Verbrauch ({roh['_verbrauch']:.1f} l/100km)")

    if req.fahranfaenger:
        if roh["_karo"] & {"kleinwagen", "kompakt"}:
            score += 2
            gruende.append("Kompakte, gut überschaubare Klasse")
        ps = roh.get("leistung_ps")
        if ps is not None and ps <= 110:
            score += 2
            gruende.append(f"Moderate Leistung ({ps} PS) für den Einstieg")

    # praktisch/komfortabel/familie: bewusst OHNE Scoring-Effekt in Runde 1
    # (§4) — die DB trägt keine Kofferraum-/Sitzplatz-/Komfortfelder. Kein
    # stiller Fallback auf ein Proxy-Feld, das diese Frage nicht beantwortet.

    return score, gruende


# ══════════════════════════════════════════════════════════════════════════
# DIVERSITÄT + TIE-BREAK — §11
# ══════════════════════════════════════════════════════════════════════════

def _sortierschluessel(roh_mit_score: tuple[float, float, dict]) -> tuple:
    score, dq, roh = roh_mit_score
    # 1. Match-Score  2. Datenqualität  3. Aktualität (neueres Baujahr zuerst)
    # 4. stabiler, deterministischer Tie-Break über variante_id (nie Zufall).
    return (-score, -dq, -(roh.get("bauzeitraum_von") or 0), roh["variante_id"])


def diversifiziere(sortiert: list[dict], *, max_pro_marke: int = 2,
                    max_pro_baureihe: int = 1, k: int = 5) -> list[dict]:
    ausgewaehlt: list[dict] = []
    marken_anzahl: dict[str, int] = {}
    baureihen_anzahl: dict[str, int] = {}
    for roh in sortiert:
        if len(ausgewaehlt) >= k:
            break
        marke = roh["marke"]
        baureihe = roh["baureihe_id"]
        if marken_anzahl.get(marke, 0) >= max_pro_marke:
            continue
        if baureihen_anzahl.get(baureihe, 0) >= max_pro_baureihe:
            continue
        ausgewaehlt.append(roh)
        marken_anzahl[marke] = marken_anzahl.get(marke, 0) + 1
        baureihen_anzahl[baureihe] = baureihen_anzahl.get(baureihe, 0) + 1
    return ausgewaehlt


# ══════════════════════════════════════════════════════════════════════════
# ANREICHERUNG — Trade-offs aus verifizierten Fakten (§8/§10/§16)
# ══════════════════════════════════════════════════════════════════════════

_MAX_TRADE_OFFS = 3


def _trade_offs_fuer(roh: dict) -> list[str]:
    """Nur ECHTE, über `get_baureihe()` gelesene Fakten (rejected bereits
    entfernt). KEIN Zuverlässigkeits-Urteil (§8) — reine Transparenz, welche
    hoch eingestuften Schwachstellen bzw. verifizierten Rückrufe bekannt
    sind. Fehlt die Baureihe unerwartet (defensiv), bleibt die Liste leer
    statt den Kandidaten zu verwerfen."""
    try:
        baureihe = get_baureihe(roh["marke"], roh["modell"], roh["generation"])
    except Exception:
        log.exception("AutoFinder: get_baureihe fehlgeschlagen für %s %s %s",
                       roh["marke"], roh["modell"], roh["generation"])
        return []
    if not baureihe:
        return []

    ausgabe: list[str] = []
    for sb in baureihe.get("schwachstellen_baureihe", []):
        if sb.get("schweregrad") == "hoch":
            label = "(geprüft)" if sb.get("_trust") == "verified" else "(ungeprüft)"
            ausgabe.append(f"Bekannte Schwachstelle {label}: {sb.get('bauteil')}")

    for m in baureihe.get("motoren", []):
        if m.get("variante_id") != roh["variante_id"]:
            continue
        for sm in m.get("schwachstellen_motor", []):
            label = "(geprüft)" if sm.get("_trust") == "verified" else "(ungeprüft)"
            ausgabe.append(f"Bekanntes Motorproblem {label}: {sm.get('bauteil')}")

    verifizierte_rueckrufe = sum(
        1 for r in baureihe.get("rueckrufe", []) if r.get("_trust") == "verified"
    )
    if verifizierte_rueckrufe:
        plural = "e" if verifizierte_rueckrufe > 1 else ""
        ausgabe.append(f"{verifizierte_rueckrufe} verifizierte(r) KBA-Rückruf{plural} bekannt")

    return ausgabe[:_MAX_TRADE_OFFS]


def _visual_key(roh: dict) -> str:
    def _slug(s: str) -> str:
        return "".join(c.lower() if c.isalnum() else "-" for c in s).strip("-")
    return f"{_slug(roh['marke'])}--{_slug(roh['modell'])}--{_slug(roh['generation'])}"


def _zu_kandidat(roh: dict, score: float, gruende: list[str], dq: float) -> AutoFinderKandidat:
    return AutoFinderKandidat(
        baureihe_id=roh["baureihe_id"],
        variante_id=roh["variante_id"],
        marke=roh["marke"],
        modell=roh["modell"],
        generation=roh["generation"],
        motor_bezeichnung=roh["bezeichnung"],
        baujahr_von=roh.get("bauzeitraum_von"),
        baujahr_bis=roh.get("bauzeitraum_bis"),
        leistung_ps=roh.get("leistung_ps"),
        kraftstoff=roh.get("kraftstoff"),
        getriebe_klassen=sorted(roh["_getriebe"]),
        antrieb=roh.get("antrieb"),
        karosserie_klassen=sorted(roh["_karo"]),
        match_score=score,
        match_gruende=gruende,
        datenqualitaet=dq,
        trade_offs=_trade_offs_fuer(roh),
        visual_key=_visual_key(roh),
    )


# ══════════════════════════════════════════════════════════════════════════
# ORCHESTRIERUNG
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class AutoFinderErgebnis:
    kandidaten: list[AutoFinderKandidat]
    treffer_vor_diversitaet: int
    dauer_ms: float


def finde_fahrzeuge(request: AutoFinderRequest, *, k: int = 5,
                     conn: sqlite3.Connection | None = None) -> AutoFinderErgebnis:
    """Orchestriert Laden -> Hartfilter -> Dedupe -> Score -> Diversität ->
    Anreicherung. Rein lesend, keine externen Calls (§12/§14 explizit: in
    Runde 1 kein Tavily, kein Gemini)."""
    start = time.perf_counter()

    if conn is not None:
        roh = _lade_kandidatenbasis_gecacht(conn)
    else:
        with get_conn() as conn:
            roh = _lade_kandidatenbasis_gecacht(conn)

    gefiltert = [r for r in roh if erfuellt_harte_filter(r, request)]
    dedupliziert = dedupe_kandidaten(gefiltert)

    bewertet = []
    for r in dedupliziert:
        score, gruende = _score_kandidat(r, request)
        dq = _datenqualitaet(r)
        bewertet.append((score, dq, r, gruende))

    sortiert = sorted(bewertet, key=lambda t: _sortierschluessel((t[0], t[1], t[2])))
    sortierte_rohs = [t[2] for t in sortiert]
    top = diversifiziere(sortierte_rohs, k=k)

    score_by_id = {t[2]["variante_id"]: (t[0], t[3]) for t in sortiert}
    dq_by_id = {t[2]["variante_id"]: t[1] for t in sortiert}

    kandidaten = [
        _zu_kandidat(r, *score_by_id[r["variante_id"]], dq_by_id[r["variante_id"]])
        for r in top
    ]

    dauer_ms = (time.perf_counter() - start) * 1000
    return AutoFinderErgebnis(
        kandidaten=kandidaten,
        treffer_vor_diversitaet=len(dedupliziert),
        dauer_ms=dauer_ms,
    )
