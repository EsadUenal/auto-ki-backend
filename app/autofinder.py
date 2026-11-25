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

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Literal

from app.autofinder_norm import (
    groessenklasse_anwenden,
    normalisiere_getriebe,
    normalisiere_karosserie,
    normalisiere_segment,
)
from app.autofinder_variante import (
    belegbare_karosserien,
    karosserie_aus_bezeichnung,
    loese_baujahre,
    loese_generationslabel,
    loese_getriebe,
    loese_karosserie,
    loese_motorbezeichnung,
    variantenseitig_kodierte_klassen,
)
from app.database import get_baureihe, get_conn
from app.motor_applicability import INKOMPATIBEL, UNKLAR, schwachstelle_applicability
from app.recall_filter import _baujahr_passt, _jahre

log = logging.getLogger(__name__)

# Kraftstoffwerte, mit denen ein Nutzer Gasfahrzeuge ausdruecklich waehlt (AF-10).
GAS_KRAFTSTOFFE = frozenset({"cng", "erdgas", "gas", "lpg", "autogas"})

# Kraftstoff/Antrieb-Vokabular — spiegelt exakt die CHECK-Constraints von
# `motorvariante.kraftstoff`/`motorvariante.antrieb` in db/schema.sql. Hier als
# EINE Quelle der Wahrheit definiert (Runde 2, app/models.py importiert sie für
# die HTTP-Eingabevalidierung), statt die fünf/drei Werte ein zweites Mal an
# anderer Stelle zu wiederholen und stillschweigend auseinanderlaufen zu lassen.
KRAFTSTOFF_WERTE = ("Benzin", "Diesel", "Elektro", "Plug-in-Hybrid", "Mild-Hybrid")
ANTRIEB_WERTE = ("Heck", "Front", "Allrad")


# ══════════════════════════════════════════════════════════════════════════
# REQUEST — §4 der Produktspezifikation
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class AutoFinderRequest:
    """Nutzereingaben. Felder sind additiv vorbereitet — nicht jedes Feld
    wirkt bereits in Runde 1 (siehe Modul-Docstring)."""

    # ---- BASIS ----
    # Budget: die DB hat keinen Gebrauchtmarktpreis. NICHT gefiltert (§4/§13) —
    # es steuert nur Reihenfolge und Preisorientierung.
    #
    # `kilometer_max` gibt es hier BEWUSST NICHT MEHR: es gab dafür nie eine
    # Datenquelle (keine Kilometerangabe je Baureihe, keine Gebrauchtwagen-
    # Angebote). Ein entgegengenommenes, aber nirgends ausgewertetes Feld sah
    # im UI wie ein wirksamer Filter aus. Die HTTP-Schicht nimmt den Wert aus
    # Kompatibilitätsgründen weiter an, reicht ihn aber nicht mehr hierher
    # durch — damit ist strukturell ausgeschlossen, dass er still in Filter
    # oder Score einfließt.
    budget_min: int | None = None
    budget_max: int | None = None
    baujahr_von: int | None = None
    baujahr_bis: int | None = None

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

    # ---- KONKRETE VARIANTE (app/autofinder_variante.py) ----
    # `karosserie_klassen` oben ist die für DIESE Variante BELEGBARE Menge
    # (nicht mehr die Sammelmenge der Baureihe). Die Felder hier sagen, was
    # davon als konkrete Empfehlung behauptet werden darf.
    karosserie_konkret: str | None = None
    karosserie_quelle: str = "mehrdeutig"
    karosserie_baureihe: list[str] = field(default_factory=list)
    getriebe_konkret: str | None = None
    getriebe_quelle: str = "mehrdeutig"
    # `baujahr_von`/`baujahr_bis` oben sind der für die Anfrage RELEVANTE
    # Ausschnitt; die Bauzeit der Generation bleibt hier sichtbar.
    generation_baujahr_von: int | None = None
    generation_baujahr_bis: int | None = None
    # Belegte Grenzen der MOTORbauzeit (motorvariante_geltung). None = nicht hinterlegt.
    motor_bauzeit_von: int | None = None
    motor_bauzeit_bis: int | None = None
    # Getriebeklassen, die der Datensatz fuehrt, die aber NICHT bestaetigt sind.
    getriebe_unbestaetigt: list[str] = field(default_factory=list)
    # Werkscode passend zur aufgelösten Karosserie, sofern geprüft hinterlegt.
    generation_label: str | None = None
    # True = die gepflegte Bezeichnung sagte nichts aus (nur eine
    # Leistungsangabe) und der Anzeigename wurde aus Hubraum/Kraftstoff
    # hergeleitet. Siehe app/autofinder_variante.loese_motorbezeichnung.
    motor_hergeleitet: bool = False

    match_gruende: list[str] = field(default_factory=list)
    # Getrennt vom match_score (§9) — Anteil befüllter Kernfelder, 0.0–1.0.
    datenqualitaet: float = 0.0

    trade_offs: list[str] = field(default_factory=list)
    # Strukturierte Fassung der DB-Fakten mit Prüfstatus, Motorbezug und
    # Zeitbezug (siehe `_bekannte_punkte_fuer`). Leer bei Web-Kandidaten.
    bekannte_punkte: list[dict] = field(default_factory=list)

    # Quality-Enrichment-Runde: rein informative Kennzahlen für den
    # nutzer-verständlichen Fit-Score (app/autofinder_fit.py) und das
    # Gemini-Enrichment. Kommen aus derselben DB-Zeile wie alles andere,
    # verändern WEDER Hard-Filter NOCH Ranking-Score.
    verbrauch_l_100km: float | None = None
    verbrauch_basis: str | None = None   # "wltp" (Spalte verbrauch_wltp) | "real" (Schaetzwert) | None
    # AF-10/12: Einheit/Zyklus/Gasantrieb. `verbrauch_l_100km` gilt NUR bei Einheit "l".
    verbrauch_wert: float | None = None      # der Wert wie hinterlegt (kann kg oder unbekannte Einheit sein)
    verbrauch_einheit: str = "l"             # "l" | "kg" | "unbekannt"
    verbrauch_zyklus: str | None = None      # "WLTP" nur wenn belegt
    antriebsgas: str | None = None           # None | "CNG" | "Gas"
    kraftstoff_hinweis: str | None = None
    verbrauch_bezug: str | None = None       # GAS-1: "CNG" | "LPG" | "Benzin" | None (Bezug eines Gas-Verbrauchswerts)
    beschleunigung_0_100_s: float | None = None
    drehmoment_nm: int | None = None

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
            b.euro_ncap_sterne, b.chassis_codes,
            m.variante_id, m.bezeichnung, m.motorcode, m.kraftstoff, m.hubraum_ccm,
            m.leistung_ps, m.drehmoment_nm, m.getriebe, m.antrieb,
            m.beschleunigung_0_100, m.verbrauch_wltp, m.verbrauch_real,
            {geltung}
        FROM motorvariante m
        JOIN baureihe b ON b.id = m.baureihe_id
        {join}
    """
    # Optionaler Geltungsbereich je Variante (belegte Bauzeit-Grenzen, nicht bestaetigte
    # Getriebe). Fehlt die Tabelle (aeltere/Teil-Schemata), gilt "keine Angabe" fuer alle.
    try:
        zeilen = conn.execute(sql.format(
            geltung="g.bauzeit_von AS motor_bauzeit_von, g.bauzeit_bis AS motor_bauzeit_bis, "
                    "g.getriebe_unbestaetigt AS getriebe_unbestaetigt, g.antriebsgas AS antriebsgas, "
                    "g.verbrauch_einheit AS verbrauch_einheit, g.verbrauch_zyklus AS verbrauch_zyklus, "
                    "g.kraftstoff_hinweis AS kraftstoff_hinweis, g.verbrauch_bezug AS verbrauch_bezug",
            join="LEFT JOIN motorvariante_geltung g ON g.variante_id = m.variante_id")).fetchall()
    except sqlite3.OperationalError:
        zeilen = conn.execute(sql.format(
            geltung="NULL AS motor_bauzeit_von, NULL AS motor_bauzeit_bis, NULL AS getriebe_unbestaetigt, "
                    "NULL AS antriebsgas, NULL AS verbrauch_einheit, NULL AS verbrauch_zyklus, "
                    "NULL AS kraftstoff_hinweis, NULL AS verbrauch_bezug",
            join="")).fetchall()
    return [dict(r) for r in zeilen]


# Kurzlebiger In-Memory-Cache für die normalisierte Kandidatenbasis — dasselbe
# Muster wie `app.database._cached_alle` (dort für baureihe/motorvariante-Kurz-
# abfragen). Die Normalisierung (Karosserie/Getriebe/Segment über alle 3230
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
    roh = _annotiere_basis(_lade_rohkandidaten(conn))
    _kandidatenbasis_cache = (now, roh)
    return roh


def invalidate_kandidatenbasis_cache() -> None:
    """Nach Admin-Schreibvorgängen an Baureihen/Motorvarianten aufrufen, damit
    die nächste AutoFinder-Suche sofort den aktuellen Stand sieht statt bis zu
    60s zu warten. Analog `database.invalidate_referenzdaten_cache()`."""
    global _kandidatenbasis_cache
    _kandidatenbasis_cache = None


def _bestaetigte_getriebe(roh: dict) -> str | None:
    """`motorvariante.getriebe` OHNE die in `motorvariante_geltung` als nicht bestaetigt
    gefuehrten Eintraege (JSON-Array in, JSON-Array out). Ohne Eintrag unveraendert."""
    unbest = roh.get("getriebe_unbestaetigt")
    if not unbest:
        return roh.get("getriebe")
    try:
        gesperrt = set(json.loads(unbest))
        liste = json.loads(roh.get("getriebe") or "[]")
    except (ValueError, TypeError):
        return roh.get("getriebe")
    return json.dumps([g for g in liste if g not in gesperrt], ensure_ascii=False)


def _annotiere_normalisierung(roh: dict) -> dict:
    """Hängt die einmalig berechneten Normalisierungs-Sets an — wird
    NICHT persistiert, nur für die Dauer einer Suche im Speicher gehalten.

    `_karo` ist hier die Karosseriemenge, die sich aus DIESER Zeile allein
    belegen lässt. Ohne die Schwestervarianten der Baureihe kann nichts
    abgezogen werden — `_annotiere_basis` schärft den Wert danach nach.
    """
    roh["_segment"] = normalisiere_segment(roh.get("segment"))
    # Größenklasse (Segment) statt Form: Kleinwagen vs. Kompakt, siehe
    # `groessenklasse_anwenden`. Filter, Endvalidierung und Karte lesen alle
    # diese eine Menge und meinen damit dasselbe.
    roh["_karo_baureihe"] = groessenklasse_anwenden(
        normalisiere_karosserie(roh.get("karosserie")), roh["_segment"])
    roh["_getriebe"] = normalisiere_getriebe(_bestaetigte_getriebe(roh))
    alle = normalisiere_getriebe(roh.get("getriebe"))
    roh["_getriebe_unbestaetigt"] = frozenset(alle - roh["_getriebe"])
    roh["_karo"] = belegbare_karosserien(
        roh["_karo_baureihe"], roh.get("bezeichnung"), frozenset())
    v = roh.get("verbrauch_wltp")
    wert = v if v is not None else roh.get("verbrauch_real")
    # Basis des Werts: Spalte `verbrauch_wltp` oder hinterlegter Real-/Schaetzwert
    # ("ca., aus Spritmonitor"). Beides sind Datenbankfelder, keine extern geprueften
    # Angaben; der ZYKLUS (WLTP) gilt nur, wo `verbrauch_zyklus` ihn belegt (siehe unten).
    roh["_verbrauch_basis"] = ("wltp" if v is not None
                               else "real" if roh.get("verbrauch_real") is not None else None)
    # Verbrauchseinheit (AF-10): Gasfahrzeuge fuehren ihren Verbrauch in der Regel in kg/100 km.
    # Ohne Gasmarkierung gelten Liter. Bei einem Gasfahrzeug mit unbekannter Einheit bleibt sie
    # UNBEKANNT; weder wird kg in Liter umgedeutet noch gegen Liter-Schwellen bewertet.
    if roh.get("antriebsgas"):
        roh["_verbrauch_einheit"] = roh.get("verbrauch_einheit") or "unbekannt"
    else:
        roh["_verbrauch_einheit"] = "l"
    roh["_verbrauch_anzeige"] = wert                       # der Wert, wie hinterlegt (Anzeige/Kontext)
    # Nur Liter eines Nicht-Gasfahrzeugs sind gegen die Liter-Schwellen bewertbar. GAS-1: auch ein belegter
    # Literwert eines Gasfahrzeugs (z. B. Benzinbetrieb) bewertet nicht dessen Sparsamkeit im Gasbetrieb.
    roh["_verbrauch"] = wert if roh["_verbrauch_einheit"] == "l" and not roh.get("antriebsgas") else None
    return roh


def _annotiere_basis(rohzeilen: list[dict]) -> list[dict]:
    """Zwei Durchgänge, weil die Karosserie einer Motorvariante nur im Kontext
    ihrer SCHWESTERVARIANTEN auflösbar ist.

    Durchgang 1 normalisiert jede Zeile für sich. Durchgang 2 bestimmt je
    Baureihe, welche Karosserien dort überhaupt über Variantennamen kodiert
    sind, und leitet daraus `_karo` ab — die für GENAU DIESE Motorvariante
    belegbare Karosseriemenge. Ab hier arbeitet die gesamte Engine (Hard-
    Filter, Score, Ausgabe) nur noch mit `_karo`; die Sammelmenge der Baureihe
    bleibt als `_karo_baureihe` nur noch Kontext.
    """
    zeilen = [_annotiere_normalisierung(r) for r in rohzeilen]

    bezeichnungen_je_baureihe: dict[str, list[str]] = {}
    for r in zeilen:
        bezeichnungen_je_baureihe.setdefault(r["baureihe_id"], []).append(
            r.get("bezeichnung") or "")
    kodiert_je_baureihe = {
        bid: variantenseitig_kodierte_klassen(bez)
        for bid, bez in bezeichnungen_je_baureihe.items()
    }

    for r in zeilen:
        r["_karo"] = belegbare_karosserien(
            r["_karo_baureihe"], r.get("bezeichnung"),
            kodiert_je_baureihe.get(r["baureihe_id"], frozenset()))
    return zeilen


# ══════════════════════════════════════════════════════════════════════════
# HARTE FILTER — §5
# ══════════════════════════════════════════════════════════════════════════

def _baujahr_ueberschneidet(roh: dict, req: AutoFinderRequest) -> bool:
    cand_von = roh.get("bauzeitraum_von")
    if cand_von is None:
        return False  # kein Baujahr bekannt -> kein belastbarer Treffer
    cand_bis = roh.get("bauzeitraum_bis")
    cand_bis = cand_bis if cand_bis is not None else 9999  # None = noch aktuell
    # Belegte Motor-Grenzen engen die Generationsbauzeit ein (nie aus).
    if roh.get("motor_bauzeit_von") is not None:
        cand_von = max(cand_von, roh["motor_bauzeit_von"])
    if roh.get("motor_bauzeit_bis") is not None:
        cand_bis = min(cand_bis, roh["motor_bauzeit_bis"])
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
        if roh.get("antriebsgas"):
            # Ein Gasfahrzeug (primaer CNG/LPG) ist kein Treffer einer reinen Benzinauswahl, auch wenn
            # die Spalte `kraftstoff` "Benzin" fuehrt. Es passt nur, wenn Gas gewaehlt wurde.
            if not (gewuenscht & GAS_KRAFTSTOFFE):
                return False
        elif (roh.get("kraftstoff") or "").strip().lower() not in gewuenscht:
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
    zusammenfallen — das wäre eine fachlich unterschiedliche Variante.

    Die belegbare KAROSSERIE gehört aus demselben Grund dazu: "GTD" und
    "GTD Variant" teilen Leistung, Kraftstoff, Antrieb und Getriebe und fielen
    sonst zu einem Kandidaten zusammen. Welcher der beiden ihn dann vertritt,
    entschiede über die angezeigte Karosserie — bei einer Suche ohne
    Karosseriefilter also faktisch zufällig."""
    return (
        roh["baureihe_id"], roh.get("leistung_ps"), roh.get("kraftstoff"),
        roh.get("antrieb"), tuple(sorted(roh["_getriebe"])),
        tuple(sorted(roh["_karo"])),
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
        roh.get("_verbrauch_anzeige"),
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

def _verbrauch_text(roh: dict) -> str:
    """Verbrauch MIT seiner tatsaechlichen Basis. Ein Datenbankfeld ist keine externe
    Verifikation. Der Zyklus (WLTP) wird nur genannt, wo er belegt ist (`verbrauch_zyklus`);
    die Spalte `verbrauch_wltp` allein belegt ihn nicht (Zeilen aus der NEFZ-Aera)."""
    if roh.get("_verbrauch_basis") == "wltp":
        basis = "WLTP" if roh.get("verbrauch_zyklus") == "WLTP" else "Angabe der Verbrauchsspalte, Zyklus nicht geprüft"
    else:
        basis = "geschätzter Realwert, kein WLTP"
    return f"{roh['_verbrauch']:.1f} l/100km, {basis}"


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
            gruende.append(f"{drehmoment} Nm Drehmoment, souverän auf der Langstrecke")
        if roh["_verbrauch"] is not None:
            score += 1
            gruende.append(f"Verbrauch laut Datensatz ({_verbrauch_text(roh)})")
    elif req.nutzung == "stadt":
        if roh["_karo"] & {"kleinwagen", "kompakt"}:
            score += 2
            gruende.append("Kompakte Klasse, wendig im Stadtverkehr")
        if roh.get("kraftstoff") in ("Benzin", "Elektro", "Mild-Hybrid"):
            score += 1
            gruende.append(f"{roh['kraftstoff']} passend für überwiegend kurze Strecken")
    # "gemischt": bewusst neutral (0 Punkte) — keine erfundene Präferenz.

    if req.sportlich:
        # "Sportlich" UND "für Fahranfänger" sind gleichzeitig wählbar und
        # widersprechen sich teilweise. Ohne Sonderbehandlung gewinnt hier
        # immer die rohe Leistung: die Vorauswahl bestand dann aus 250–306-PS-
        # Fahrzeugen, und moderat motorisierte, durchaus sportliche Autos
        # erreichten die Bewertungsstufe gar nicht erst. Fahranfänger-Eignung
        # ist aber keine Geschmacksfrage wie "sportlich", sondern eine Aussage
        # über den Fahrer (Versicherung, Fahrverhalten) — deshalb dürfen
        # genau die Merkmale, die ein Einsteigerfahrzeug ausschließen (sehr
        # hohe Leistung, sehr hohes Drehmoment), dann keinen Bonus mehr geben.
        # Beschleunigung bleibt: ein leichtes, spritziges Auto ist sportlich
        # UND einsteigertauglich.
        ps = roh.get("leistung_ps")
        if not req.fahranfaenger and ps is not None and ps >= 250:
            score += 2
            gruende.append(f"Hohe Leistung ({ps} PS)")
        b100 = roh.get("beschleunigung_0_100")
        if b100 is not None and b100 <= 6.5:
            score += 2
            gruende.append(f"0–100 km/h in {b100:.1f}s")
        drehmoment = roh.get("drehmoment_nm")
        if not req.fahranfaenger and drehmoment is not None and drehmoment >= 400:
            score += 1
            gruende.append(f"Hohes Drehmoment ({drehmoment} Nm)")

    if req.sparsam:
        # §7: NUR wenn Verbrauch tatsächlich vorhanden — kein Bonus bei Lücke.
        if roh["_verbrauch"] is not None:
            if roh["_verbrauch"] <= 5.5:
                score += 3
                gruende.append(f"Niedriger Verbrauch laut Datensatz ({_verbrauch_text(roh)})")
            elif roh["_verbrauch"] <= 7.0:
                score += 1
                gruende.append(f"Moderater Verbrauch laut Datensatz ({_verbrauch_text(roh)})")

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


# Zeitliche Geltung eines Fakts gegenüber dem Baujahrfenster der Karte.
Z_OK = "ok"                  # mindestens ein Jahr des Fensters liegt im Geltungsbereich
Z_AUSSERHALB = "ausserhalb"  # KEIN Jahr des Fensters liegt im Geltungsbereich
Z_UNKLAR = "unklar"          # Zeitangabe vorhanden, aber nicht eindeutig zuordenbar
Z_OHNE = "ohne_angabe"       # der Fakt macht keine Zeitangabe


def _zeit_status(baujahre_text: str | None, von: int | None, bis: int | None) -> str:
    """Gilt ein Fakt für das Baujahrfenster dieser Karte?

    Wiederverwendet die KaufCheck-Regel `_baujahr_passt` (app/recall_filter.py),
    JAHR FÜR JAHR über das Fenster: nur wenn jedes Jahr nachweislich außerhalb
    liegt, gilt der Fakt als außerhalb. Ein offenes Fensterende zählt bis zum
    laufenden Jahr. Fehlt das Fenster oder ist die Angabe unscharf, bleibt es
    ausdrücklich unklar, nie stillschweigend passend.
    """
    if not _jahre(baujahre_text):
        return Z_OHNE
    if von is None:
        return Z_UNKLAR
    ende = bis if bis is not None else max(von, time.gmtime().tm_year)
    ergebnisse = {_baujahr_passt(baujahre_text, y) for y in range(von, max(von, ende) + 1)}
    if True in ergebnisse:
        return Z_OK
    if ergebnisse == {False}:
        return Z_AUSSERHALB
    return Z_UNKLAR


def _bekannte_punkte_fuer(roh: dict, fenster: tuple[int | None, int | None] = (None, None)) -> list[dict]:
    """Strukturierte DB-Fakten (Schwachstellen/Rückrufe) dieser Variante.

    Nur ECHTE, über `get_baureihe()` gelesene Fakten (rejected bereits
    entfernt). Zusätzlich gelten dieselben Gates wie im KaufCheck:

      * Motor-Anwendbarkeit (`schwachstelle_applicability`): ein Baureihenfakt, der
        nachweislich eine ANDERE Motorisierung meint (Diesel-Hinweis am Benziner),
        entfällt. Unklare Anwendbarkeit bleibt und wird als unklar ausgewiesen.
      * Zeitliche Geltung (`_zeit_status`): liegt das gesamte Baujahrfenster der
        Karte außerhalb des Geltungszeitraums, entfällt der Fakt; ist der Bezug
        unklar, bleibt er mit entsprechendem Vorbehalt.

    Der Prüfstatus (`_trust`) bleibt am Fakt erhalten. KEIN Zuverlässigkeits-Urteil
    (§8) — reine Transparenz. Fehlt die Baureihe unerwartet, bleibt die Liste
    leer statt den Kandidaten zu verwerfen."""
    try:
        baureihe = get_baureihe(roh["marke"], roh["modell"], roh["generation"])
    except Exception:
        log.exception("AutoFinder: get_baureihe fehlgeschlagen für %s %s %s",
                       roh["marke"], roh["modell"], roh["generation"])
        return []
    if not baureihe:
        return []

    von, bis = fenster
    motor = next((m for m in baureihe.get("motoren", [])
                  if m.get("variante_id") == roh["variante_id"]), None)

    punkte: list[dict] = []
    for sb in baureihe.get("schwachstellen_baureihe", []):
        if sb.get("schweregrad") != "hoch":
            continue
        appl, _grund = schwachstelle_applicability(sb, motor, baureihe, motorcode_widerspruch=True)
        if appl == INKOMPATIBEL:
            continue
        zeit = _zeit_status(sb.get("betroffene_baujahre"), von, bis)
        if zeit == Z_AUSSERHALB:
            continue
        punkte.append({"art": "baureihe", "bauteil": sb.get("bauteil"),
                       "trust": sb.get("_trust") or "unverified_db",
                       "motor_unklar": appl == UNKLAR,
                       "zeit_unklar": zeit == Z_UNKLAR,
                       "zeitraum": sb.get("betroffene_baujahre")})

    for sm in (motor or {}).get("schwachstellen_motor", []):
        zeit = _zeit_status(sm.get("baujahre"), von, bis)
        if zeit == Z_AUSSERHALB:
            continue
        punkte.append({"art": "motor", "bauteil": sm.get("bauteil"),
                       "trust": sm.get("_trust") or "unverified_db",
                       "motor_unklar": False,
                       "zeit_unklar": zeit == Z_UNKLAR,
                       "zeitraum": sm.get("baujahre")})

    verifizierte_rueckrufe = sum(
        1 for r in baureihe.get("rueckrufe", []) if r.get("_trust") == "verified"
    )
    if verifizierte_rueckrufe:
        punkte.append({"art": "rueckruf", "anzahl": verifizierte_rueckrufe,
                       "trust": "verified"})

    return punkte[:_MAX_TRADE_OFFS]


def rueckruf_text(anzahl: int) -> str:
    """Die Rückrufanzahl gehört zur gesamten BAUREIHE. Daraus folgt nichts zu Motor,
    Zeitraum, FIN-Betroffenheit oder Durchführung dieses Fahrzeugs."""
    plural = "e" if anzahl > 1 else ""
    return (f"Zur Baureihe sind {anzahl} verifizierte KBA-Rückruf{plural} dokumentiert. "
            "Ob dieses Fahrzeug betroffen ist und ob ein Rückruf durchgeführt wurde, ist damit nicht gesagt "
            "(Motor, Zeitraum und FIN nicht geprüft)")


def _trade_offs_aus_punkten(punkte: list[dict]) -> list[str]:
    """Interne Textform der strukturierten Punkte (Prüf-Label in Klammern).

    Fit (`autofinder_fit`) und Fallback lesen daran nur, OB ein Fakt geprüft ist;
    die Nutzertexte entstehen aus den strukturierten Punkten (Router)."""
    ausgabe: list[str] = []
    for p in punkte:
        if p["art"] == "rueckruf":
            ausgabe.append(rueckruf_text(p["anzahl"]))
            continue
        label = "(geprüft)" if p["trust"] == "verified" else "(ungeprüft)"
        kopf = "Bekannte Schwachstelle" if p["art"] == "baureihe" else "Bekanntes Motorproblem"
        ausgabe.append(f"{kopf} {label}: {p['bauteil']}")
    return ausgabe


def _trade_offs_fuer(roh: dict, fenster: tuple[int | None, int | None] = (None, None)) -> list[str]:
    return _trade_offs_aus_punkten(_bekannte_punkte_fuer(roh, fenster))


def _visual_key(roh: dict) -> str:
    def _slug(s: str) -> str:
        return "".join(c.lower() if c.isalnum() else "-" for c in s).strip("-")
    return f"{_slug(roh['marke'])}--{_slug(roh['modell'])}--{_slug(roh['generation'])}"


def _zu_kandidat(roh: dict, score: float, gruende: list[str], dq: float,
                  req: AutoFinderRequest) -> AutoFinderKandidat:
    karo = loese_karosserie(roh["_karo_baureihe"], roh.get("bezeichnung"),
                            # `_karo` trägt das Ergebnis der Baureihen-Analyse
                            # bereits; die Kodierungsmenge muss dafür nicht
                            # erneut bestimmt werden.
                            roh["_karo_baureihe"] - roh["_karo"],
                            gewuenscht=list(req.karosserie))
    getr = loese_getriebe(sorted(roh["_getriebe"]), gewuenscht=list(req.getriebe))
    bj = loese_baujahre(roh.get("bauzeitraum_von"), roh.get("bauzeitraum_bis"),
                        req.baujahr_von, req.baujahr_bis,
                        motor_von=roh.get("motor_bauzeit_von"), motor_bis=roh.get("motor_bauzeit_bis"))
    motor_name, motor_hergeleitet = loese_motorbezeichnung(
        roh.get("bezeichnung"), roh.get("hubraum_ccm"), roh.get("kraftstoff"))
    punkte = _bekannte_punkte_fuer(roh, (bj.von, bj.bis))
    return AutoFinderKandidat(
        baureihe_id=roh["baureihe_id"],
        variante_id=roh["variante_id"],
        marke=roh["marke"],
        modell=roh["modell"],
        generation=roh["generation"],
        motor_bezeichnung=motor_name or "",
        baujahr_von=bj.von,
        baujahr_bis=bj.bis,
        leistung_ps=roh.get("leistung_ps"),
        kraftstoff=roh.get("kraftstoff"),
        getriebe_klassen=sorted(roh["_getriebe"]),
        antrieb=roh.get("antrieb"),
        karosserie_klassen=sorted(roh["_karo"]),
        karosserie_konkret=karo.konkret,
        karosserie_quelle=karo.quelle,
        karosserie_baureihe=sorted(roh["_karo_baureihe"]),
        getriebe_konkret=getr.konkret,
        getriebe_quelle=getr.quelle,
        generation_baujahr_von=bj.generation_von,
        generation_baujahr_bis=bj.generation_bis,
        motor_bauzeit_von=roh.get("motor_bauzeit_von"),
        motor_bauzeit_bis=roh.get("motor_bauzeit_bis"),
        getriebe_unbestaetigt=sorted(roh["_getriebe_unbestaetigt"]),
        generation_label=loese_generationslabel(
            roh["generation"], roh.get("chassis_codes"), karo.konkret),
        motor_hergeleitet=motor_hergeleitet,
        match_score=score,
        match_gruende=gruende,
        datenqualitaet=dq,
        trade_offs=_trade_offs_aus_punkten(punkte),
        bekannte_punkte=punkte,
        verbrauch_l_100km=roh.get("_verbrauch"),
        verbrauch_basis=roh.get("_verbrauch_basis"),
        verbrauch_wert=roh.get("_verbrauch_anzeige"),
        verbrauch_einheit=roh["_verbrauch_einheit"],
        verbrauch_zyklus=roh.get("verbrauch_zyklus"),
        antriebsgas=roh.get("antriebsgas"),
        kraftstoff_hinweis=roh.get("kraftstoff_hinweis"),
        verbrauch_bezug=roh.get("verbrauch_bezug"),
        beschleunigung_0_100_s=roh.get("beschleunigung_0_100"),
        drehmoment_nm=roh.get("drehmoment_nm"),
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
        _zu_kandidat(r, *score_by_id[r["variante_id"]], dq_by_id[r["variante_id"]], request)
        for r in top
    ]

    dauer_ms = (time.perf_counter() - start) * 1000
    return AutoFinderErgebnis(
        kandidaten=kandidaten,
        treffer_vor_diversitaet=len(dedupliziert),
        dauer_ms=dauer_ms,
    )
