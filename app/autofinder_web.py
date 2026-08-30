from __future__ import annotations

"""
AutoFinder — kontrollierter Web-Fallback (Runde 4).

WARUM ES DAS GIBT
------------------
Die interne Fahrzeug-DB deckt 416 Baureihen aus 13 Marken ab. Renault, Peugeot,
Dacia, Mazda, Honda, Volvo, Tesla u.v.a. fehlen vollstaendig. Ein AutoFinder,
der ausschliesslich intern sucht, behauptet implizit, es gaebe nichts anderes.
Dieser Modul ergaenzt deshalb GEZIELT fehlende FAHRZEUGE — und ausschliesslich
Fahrzeuge.

WAS DIESER MODUL AUSDRUECKLICH NICHT TUT
-----------------------------------------
KEINE Marktpreise, KEINE Inserate, KEINE Preisaggregation, KEINE Bilder.
Marktplaetze (mobile.de, AutoScout24, Kleinanzeigen, AutoUncle) sind an ZWEI
Stellen ausgeschlossen: als `exclude_domains` schon bei der Tavily-Anfrage UND
noch einmal als harte Ablehnung jeder Beleg-URL im Validierungs-Gate. Die
bestehende Source-Policy (`app/web_search.py`) wird wiederverwendet, NICHT
durch eine laxere AutoFinder-eigene Allowlist umgangen.

DIE DREI SICHERUNGEN GEGEN PHANTOMFAHRZEUGE
---------------------------------------------
1. COVERAGE-GATE  — Web laeuft ueberhaupt nur, wenn die interne Abdeckung
   nachweislich nicht reicht (`braucht_web_fallback`). Gute DB-Abdeckung =>
   0 Tavily-Calls, 0 Gemini-Calls.
2. EVIDENZBINDUNG — Gemini bekommt die konkreten Suchtreffer nummeriert und
   MUSS je Kandidat die verwendeten Belegnummern nennen. Ein Kandidat ohne
   aufloesbare Belege wird verworfen.
3. BELEG-GEGENPROBE — jedes technische Kernfeld (Marke, Modell, Kraftstoff,
   Leistung) muss im Text der ZITIERTEN Belege tatsaechlich vorkommen. Ein aus
   dem Modellwissen ergaenzter Wert faellt hier durch, auch wenn Gemini eine
   gueltige Belegnummer angibt.

Bei Widerspruch oder fehlendem Kernfeld gilt: ABLEHNEN, nie raten (UNKNOWN
statt Vermutung).

KOSTENDECKEL (§14)
-------------------
Pro AutoFinder-Suche hoechstens 2 Tavily-Calls und 1 Discovery-Gemini-Call,
und beides nur bei ausgeloestem Coverage-Gate. Keine Extract-Ladder, keine
adaptive Mehrstufen-Recherche — das ist bewusst NICHT die Marktrecherche des
KaufChecks.

AUSFALLSICHERHEIT (§13)
------------------------
Jeder Schritt ist gekapselt. Tavily down, Gemini down, kaputtes JSON, keine
belastbaren Kandidaten — das Ergebnis ist immer eine (ggf. leere) Liste, nie
eine Exception. Der Web-Fallback ist optional; AutoFinder bleibt ohne ihn
vollstaendig funktionsfaehig.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.autofinder import erfuellt_harte_filter as _erfuellt_harte_filter
from app.autofinder import _datenqualitaet as _engine_datenqualitaet
from app.autofinder import _score_kandidat as _engine_score
from app.autofinder_norm import (
    GETRIEBE_KLASSEN,
    KAROSSERIE_KLASSEN,
    normalisiere_getriebe,
    normalisiere_karosserie,
)
from app.car_lookup import call_gemini_json
from app.gemini_retry import GeminiFehlgeschlagen
from app.web_search import (
    MARKTPLATZ_DOMAINS,
    CLASSIC_AUKTION_DOMAINS,
    ist_marktplatz_domain,
    score_domain,
    tavily_search_mit_status,
)

log = logging.getLogger(__name__)

# ── Kostendeckel (§5/§14) — harte Obergrenzen, keine adaptive Ladder ────────
MAX_TAVILY_CALLS = 2
MAX_DISCOVERY_GEMINI_CALLS = 1
_TAVILY_ERGEBNISSE_PRO_CALL = 8
_TAVILY_SEARCH_DEPTH = "basic"          # 1 Credit — der guenstigste sinnvolle Modus

# ── Coverage-Gate (§2) ──────────────────────────────────────────────────────
# Ab wie vielen internen Kandidaten die DB als ausreichend gilt. Bewusst
# derselbe Schwellenwert wie die bestehende Low-Coverage-Warnung im Router
# (_NIEDRIGE_COVERAGE_SCHWELLE) — eine Zahl, eine Bedeutung.
COVERAGE_MIN_INTERNE_KANDIDATEN = 3

GRUND_KEIN_INTERNER_TREFFER = "no_internal_match"
GRUND_GERINGE_COVERAGE = "geringe_interne_coverage"
GRUND_MARKE_NICHT_IM_BESTAND = "marke_nicht_im_bestand"

# ── Discovery-Confidence ────────────────────────────────────────────────────
CONF_HIGH = "HIGH"
CONF_MEDIUM = "MEDIUM"
CONF_LOW = "LOW"
CONF_UNKNOWN = "UNKNOWN"

# Ab dieser Domain-Bewertung (app/web_search.score_domain) gilt eine Quelle als
# Primaerquelle: 48 = Herstellerseite, 50 = amtlich (KBA/TUEV/DEKRA). Nur solche
# Quellen duerfen einen Kandidaten ALLEIN tragen (§7).
_PRIMAERQUELLE_MIN_SCORE = 48

# Der Web-Kandidat traegt bewusst NIE eine erfundene DB-ID (§8). Dieses Praefix
# macht schon an der ID unuebersehbar, dass er nicht aus der Fahrzeug-DB stammt.
WEB_ID_PREFIX = "web:"


# ══════════════════════════════════════════════════════════════════════════
# KANDIDATENMODELL
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class WebKandidat:
    """Ein web-entdeckter Fahrzeugkandidat.

    Traegt bewusst `baureihe_id = None` und `variante_id = None`: es gibt keine
    DB-Zeile zu diesem Fahrzeug, und eine erfundene ID waere genau die stille
    Vermischung, die dieser Modul verhindern soll (§8). Die kanonische Kennung
    ist `candidate_id` mit `web:`-Praefix.
    """
    candidate_id: str
    marke: str
    modell: str
    generation: str | None
    motor_bezeichnung: str
    baujahr_von: int | None
    baujahr_bis: int | None
    leistung_ps: int | None
    kraftstoff: str
    getriebe_klassen: list[str]
    antrieb: str | None
    karosserie_klassen: list[str]

    match_score: float = 0.0
    match_gruende: list[str] = field(default_factory=list)
    datenqualitaet: float = 0.0
    trade_offs: list[str] = field(default_factory=list)

    # ---- Herkunft (§8) ----
    source_type: str = "web_discovered"
    source_urls: list[str] = field(default_factory=list)
    evidence_count: int = 0
    discovery_confidence: str = CONF_UNKNOWN
    web_verified_fields: list[str] = field(default_factory=list)
    visual_key: str = ""

    # ---- Bewusst leer: es gibt keine DB-Zeile (§8) ----
    baureihe_id: None = None
    variante_id: None = None

    # ---- Markt bleibt unberuehrt (§16: keine Preise in dieser Runde) ----
    market_price_min: None = None
    market_price_max: None = None
    market_price_median: None = None
    market_data_quality: None = None
    market_sample_size: None = None


def kandidat_id(k: Any) -> str:
    """Kanonische Kandidaten-ID quer ueber beide Herkuenfte.

    Interne DB-Kandidaten (`app.autofinder.AutoFinderKandidat`) tragen
    `variante_id`; Web-Kandidaten tragen `candidate_id` und bewusst KEINE
    DB-ID. Eine Stelle, eine Regel — damit Budget-Schicht und Router nicht
    jeweils eigene Fallunterscheidungen bauen muessen.
    """
    return getattr(k, "candidate_id", None) or getattr(k, "variante_id", None) or ""


# ══════════════════════════════════════════════════════════════════════════
# COVERAGE-GATE (§2)
# ══════════════════════════════════════════════════════════════════════════

def braucht_web_fallback(interne_kandidaten: list, request: Any,
                          bekannte_marken: set[str]) -> tuple[bool, str | None]:
    """Deterministische Entscheidung, ob ueberhaupt Web gesucht werden darf.

    Gibt `(ja_nein, grund)` zurueck. Bewusst KEINE Heuristik "die DB ist nicht
    perfekt" — es braucht einen benennbaren, pruefbaren Mangel:

      1. gar kein interner Treffer,
      2. weniger als `COVERAGE_MIN_INTERNE_KANDIDATEN` interne Treffer,
      3. der Nutzer verlangt ausdruecklich eine Marke, die VIRA gar nicht fuehrt
         (dann bringt auch eine gefuellte Trefferliste anderer Marken nichts).
    """
    if not interne_kandidaten:
        return True, GRUND_KEIN_INTERNER_TREFFER

    gewuenscht = [m.strip().lower() for m in (getattr(request, "marken_bevorzugt", None) or [])
                  if m and m.strip()]
    if gewuenscht:
        fehlend = [m for m in gewuenscht if m not in bekannte_marken]
        if fehlend:
            log.info("AutoFinder-Web: gewuenschte Marke(n) nicht im internen Bestand: %s", fehlend)
            return True, GRUND_MARKE_NICHT_IM_BESTAND

    if len(interne_kandidaten) < COVERAGE_MIN_INTERNE_KANDIDATEN:
        return True, GRUND_GERINGE_COVERAGE

    return False, None


# ══════════════════════════════════════════════════════════════════════════
# QUERY-PLANUNG (§4) — technische Kriterien, KEINE Kaufempfehlungsfrage
# ══════════════════════════════════════════════════════════════════════════

_KAROSSERIE_WORT = {
    "kleinwagen": "Kleinwagen", "kompakt": "Kompaktwagen", "limousine": "Limousine",
    "kombi": "Kombi", "suv": "SUV", "van": "Van", "coupe": "Coupé",
    "cabrio": "Cabrio", "pickup": "Pick-up",
}
_GETRIEBE_WORT = {"automatik": "Automatik", "manuell": "Schaltgetriebe"}


def _baue_discovery_queries(request: Any) -> list[str]:
    """Hoechstens `MAX_TAVILY_CALLS` Suchanfragen, rein aus den Nutzerfiltern.

    Formuliert bewusst als Frage nach EXISTIERENDEN MODELLEN mit technischen
    Eigenschaften — nicht als "welches Auto soll ich kaufen". Letzteres wuerde
    Kaufberatungs-/Ranking-Seiten holen; gebraucht werden Modell-/Datenseiten.
    """
    karo = [_KAROSSERIE_WORT.get(k, k) for k in (request.karosserie or [])]
    getriebe = [_GETRIEBE_WORT.get(g, g) for g in (request.getriebe or [])]
    kraftstoff = list(request.kraftstoff or [])

    teile: list[str] = []
    teile.extend(karo)
    teile.extend(kraftstoff)
    teile.extend(getriebe)

    if request.leistung_min_ps and request.leistung_max_ps:
        teile.append(f"{request.leistung_min_ps} bis {request.leistung_max_ps} PS")
    elif request.leistung_min_ps:
        teile.append(f"ab {request.leistung_min_ps} PS")
    elif request.leistung_max_ps:
        teile.append(f"bis {request.leistung_max_ps} PS")

    if request.baujahr_von:
        teile.append(f"ab Baujahr {request.baujahr_von}")

    marken = [m for m in (request.marken_bevorzugt or []) if m and m.strip()]
    basis = " ".join(teile).strip()

    queries: list[str] = []
    if marken:
        # Ausdruecklich gewuenschte Marke: gezielt nach deren Modellpalette fragen.
        queries.append(f"{' '.join(marken)} {basis} Modelle technische Daten".strip())
    if basis:
        queries.append(f"{basis} Modelle Übersicht technische Daten".strip())
        queries.append(f"Welche Fahrzeugmodelle {basis} technische Daten Hersteller".strip())
    if not queries:
        # Ohne jeden technischen Filter gibt es nichts sinnvoll Einzugrenzendes —
        # dann lieber gar nicht suchen als eine Allerweltsanfrage abzusetzen.
        return []
    return queries[:MAX_TAVILY_CALLS]


# ══════════════════════════════════════════════════════════════════════════
# TAVILY-DISCOVERY (§5) — Marktplaetze ausgeschlossen
# ══════════════════════════════════════════════════════════════════════════

# Erste von zwei Sperren gegen Marktplaetze: gar nicht erst anfragen. Die zweite
# (harte Ablehnung jeder Beleg-URL) sitzt im Validierungs-Gate — bewusst doppelt,
# weil Tavily `exclude_domains` nicht garantiert vollstaendig durchsetzt.
_AUSGESCHLOSSENE_DOMAINS = sorted({*MARKTPLATZ_DOMAINS, *CLASSIC_AUKTION_DOMAINS})


async def _suche_evidenzen(queries: list[str]) -> tuple[list[dict], int, bool]:
    """Fuehrt bis zu `MAX_TAVILY_CALLS` Suchen aus und sammelt die Treffer.

    Gibt `(evidenzen, anzahl_calls, hatte_technischen_fehler)`. Bricht ab,
    sobald genug Material da ist — der zweite Call ist eine Reserve fuer duenne
    Ergebnisse, keine feste Pflichtstufe.
    """
    evidenzen: list[dict] = []
    gesehen: set[str] = set()
    calls = 0
    fehler = False

    for query in queries[:MAX_TAVILY_CALLS]:
        if len(evidenzen) >= _TAVILY_ERGEBNISSE_PRO_CALL:
            break   # genug Material — zweiten Call sparen
        try:
            treffer, hatte_fehler = await tavily_search_mit_status(
                query,
                count=_TAVILY_ERGEBNISSE_PRO_CALL,
                exclude_domains=list(_AUSGESCHLOSSENE_DOMAINS),
                search_depth=_TAVILY_SEARCH_DEPTH,
            )
        except Exception:
            log.exception("AutoFinder-Web: Tavily-Suche fehlgeschlagen (Query %r)", query[:80])
            calls += 1
            fehler = True
            continue
        calls += 1
        fehler = fehler or hatte_fehler
        for t in treffer or []:
            url = (t.get("url") or "").strip()
            if not url or url in gesehen:
                continue
            # Zweite Marktplatz-Sperre bereits hier, damit solche Treffer gar
            # nicht erst in den Gemini-Prompt gelangen.
            if ist_marktplatz_domain(url):
                continue
            gesehen.add(url)
            evidenzen.append({
                "url": url,
                "title": (t.get("title") or "").strip(),
                "content": (t.get("content") or "").strip(),
            })

    return evidenzen, calls, fehler


# ══════════════════════════════════════════════════════════════════════════
# GEMINI-STRUKTURIERUNG (§6) — extrahieren, nicht erfinden
# ══════════════════════════════════════════════════════════════════════════

_DISCOVERY_SYSTEM_PROMPT = """Du extrahierst real existierende Fahrzeugmodelle AUSSCHLIESSLICH aus den dir vorgelegten Suchtreffern.

STRENGE REGELN:
- Du darfst NUR Fahrzeuge nennen, die in den nummerierten Belegen tatsächlich vorkommen.
- Du darfst KEIN Modell aus deinem eigenen Wissen ergänzen, auch wenn es gut passen würde.
- Du darfst KEINE Generation, KEINE Motorisierung, KEINE Leistung und KEINEN Kraftstoff erfinden. Steht ein Wert nicht in den Belegen, lässt du das Feld weg (null).
- Für jedes Fahrzeug gibst du in "evidence" die Nummern der Belege an, aus denen die Angaben stammen.
- Nenne KEINE Preise, KEINE Angebote, KEINE Marktwerte.

Antworte AUSSCHLIESSLICH mit diesem JSON, ohne Erklärtext und ohne Markdown:
{"candidates": [{"marke": "...", "modell": "...", "generation": "... oder null", "motor": "... oder null", "baujahr_von": 2018, "baujahr_bis": null, "kraftstoff": "Benzin|Diesel|Elektro|Plug-in-Hybrid|Mild-Hybrid", "leistung_ps": 150, "getriebe": "automatik|manuell oder null", "karosserie": "kleinwagen|kompakt|limousine|kombi|suv|van|coupe|cabrio|pickup oder null", "antrieb": "Front|Heck|Allrad oder null", "evidence": [1, 3]}]}

Maximal 8 Fahrzeuge. Lieber wenige gut belegte als viele vermutete."""


def _baue_discovery_user_msg(request: Any, evidenzen: list[dict]) -> str:
    zeilen = ["Gesuchte technische Eigenschaften:"]
    if request.karosserie:
        zeilen.append(f"  Karosserie: {', '.join(request.karosserie)}")
    if request.kraftstoff:
        zeilen.append(f"  Kraftstoff: {', '.join(request.kraftstoff)}")
    if request.getriebe:
        zeilen.append(f"  Getriebe: {', '.join(request.getriebe)}")
    if request.leistung_min_ps or request.leistung_max_ps:
        zeilen.append(f"  Leistung: {request.leistung_min_ps or '?'}–{request.leistung_max_ps or '?'} PS")
    if request.baujahr_von or request.baujahr_bis:
        zeilen.append(f"  Baujahr: {request.baujahr_von or '?'}–{request.baujahr_bis or '?'}")
    if request.marken_bevorzugt:
        zeilen.append(f"  Gewünschte Marken: {', '.join(request.marken_bevorzugt)}")

    zeilen.append("")
    zeilen.append("Belege (NUR hieraus extrahieren):")
    for i, e in enumerate(evidenzen, start=1):
        zeilen.append(f"[{i}] {e['title']} | {e['url']}")
        if e["content"]:
            zeilen.append(f"    {e['content'][:600]}")
    return "\n".join(zeilen)


async def _strukturiere_mit_gemini(request: Any, evidenzen: list[dict]) -> tuple[list[dict], int, bool]:
    """GENAU EIN Gemini-Call. Gibt `(rohe_kandidaten, anzahl_calls, ausgefallen)`."""
    if not evidenzen:
        return [], 0, False
    try:
        roh = await call_gemini_json(
            _DISCOVERY_SYSTEM_PROMPT, _baue_discovery_user_msg(request, evidenzen))
    except GeminiFehlgeschlagen as exc:
        log.warning("AutoFinder-Web: Discovery-Gemini fehlgeschlagen: %s", exc)
        return [], 1, True
    except Exception:
        log.exception("AutoFinder-Web: unerwarteter Fehler im Discovery-Gemini-Call")
        return [], 1, True

    if not isinstance(roh, dict):
        log.warning("AutoFinder-Web: Discovery-Gemini lieferte kein Objekt — verworfen.")
        return [], 1, True
    kandidaten = roh.get("candidates")
    if not isinstance(kandidaten, list):
        log.warning("AutoFinder-Web: Discovery-Gemini ohne 'candidates'-Liste — verworfen.")
        return [], 1, True
    return [k for k in kandidaten if isinstance(k, dict)], 1, False


# ══════════════════════════════════════════════════════════════════════════
# VALIDIERUNGS-GATE (§7) — die eigentliche Sicherung
# ══════════════════════════════════════════════════════════════════════════

_KRAFTSTOFF_BELEGWORTE = {
    "Diesel": ("diesel", "tdi", "cdi", "hdi", "dci", "crdi", "bluetec", "d-4d"),
    "Benzin": ("benzin", "otto", "tsi", "tfsi", "gdi", "vti", "thp", "ecoboost", "vvt"),
    "Elektro": ("elektro", "electric", "ev", "bev", "e-tron", "kwh"),
    "Plug-in-Hybrid": ("plug-in", "plugin", "phev"),
    "Mild-Hybrid": ("mild-hybrid", "mildhybrid", "mhev", "48-volt", "48 v"),
}
_ANTRIEB_WERTE = {"front", "heck", "allrad"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")


def _stabile_web_id(marke: str, modell: str, generation: str | None, motor: str | None) -> str:
    teile = [_slug(t) for t in (marke, modell, generation, motor) if t and str(t).strip()]
    return WEB_ID_PREFIX + "--".join(t for t in teile if t)


def _belegtext(indices: list[int], evidenzen: list[dict]) -> str:
    """Zusammengefasster Text GENAU der zitierten Belege — Grundlage der
    Gegenprobe. Nicht der gesamte Suchlauf: ein Kandidat darf sich nicht auf
    Belege stuetzen, die er nicht genannt hat."""
    teile = []
    for i in indices:
        e = evidenzen[i - 1]
        teile.append(f"{e['title']} {e['content']}")
    return " ".join(teile).lower()


def _int_oder_none(wert: Any) -> int | None:
    if isinstance(wert, bool):
        return None
    if isinstance(wert, int):
        return wert
    if isinstance(wert, str) and wert.strip().isdigit():
        return int(wert.strip())
    return None


def _pruefe_kandidat(roh: dict, evidenzen: list[dict]) -> tuple[WebKandidat | None, str]:
    """Prueft EINEN rohen Gemini-Kandidaten. Gibt `(kandidat_oder_None, grund)`.

    Reihenfolge ist Absicht: erst die Belegkette (existieren die zitierten
    Quellen ueberhaupt, sind sie zulaessig?), dann die Pflichtfelder, dann die
    Gegenprobe gegen den Belegtext. So steht in `grund` immer der ERSTE,
    konkreteste Ablehnungsgrund statt eines Sammelurteils.
    """
    # ── 1. Belegkette ────────────────────────────────────────────────────────
    roh_indices = roh.get("evidence")
    if not isinstance(roh_indices, list) or not roh_indices:
        return None, "keine Belegnummern angegeben"
    indices: list[int] = []
    for i in roh_indices:
        n = _int_oder_none(i)
        if n is None or n < 1 or n > len(evidenzen):
            return None, f"Belegnummer {i!r} existiert nicht"
        if n not in indices:
            indices.append(n)

    urls = [evidenzen[i - 1]["url"] for i in indices]
    if any(ist_marktplatz_domain(u) for u in urls):
        return None, "Marktplatz-Quelle unzulaessig"

    domains = {re.sub(r"^www\.", "", (re.split(r"/+", u, maxsplit=3)[1] if "//" in u else u).lower())
               for u in urls}
    bester_score = max((score_domain(u) for u in urls), default=0)
    if len(domains) < 2 and bester_score < _PRIMAERQUELLE_MIN_SCORE:
        return None, "nur eine Quelle, und diese ist keine Primaerquelle"

    # ── 2. Pflichtfelder (§7) ────────────────────────────────────────────────
    marke = (roh.get("marke") or "").strip()
    modell = (roh.get("modell") or "").strip()
    if not marke or not modell:
        return None, "Marke oder Modell fehlt"

    generation = (roh.get("generation") or "").strip() or None
    baujahr_von = _int_oder_none(roh.get("baujahr_von"))
    baujahr_bis = _int_oder_none(roh.get("baujahr_bis"))
    if not generation and baujahr_von is None:
        return None, "weder Generation noch Produktionszeitraum belegt"

    kraftstoff = (roh.get("kraftstoff") or "").strip()
    if kraftstoff not in _KRAFTSTOFF_BELEGWORTE:
        return None, f"Kraftstoff {kraftstoff!r} fehlt oder ist unbekannt"

    leistung_ps = _int_oder_none(roh.get("leistung_ps"))
    motor = (roh.get("motor") or "").strip() or None
    if leistung_ps is None and not motor:
        return None, "weder Leistung noch Motorisierung belegt"

    # ── 3. Gegenprobe gegen den Text der ZITIERTEN Belege ────────────────────
    # Hier faellt durch, was Gemini aus eigenem Wissen ergaenzt hat, selbst wenn
    # es eine formal gueltige Belegnummer angibt.
    text = _belegtext(indices, evidenzen)
    verifiziert: list[str] = []

    if marke.lower() not in text:
        return None, "Marke kommt in den zitierten Belegen nicht vor"
    verifiziert.append("marke")

    if modell.lower() not in text:
        return None, "Modell kommt in den zitierten Belegen nicht vor"
    verifiziert.append("modell")

    if not any(w in text for w in _KRAFTSTOFF_BELEGWORTE[kraftstoff]):
        return None, "Kraftstoff ist in den zitierten Belegen nicht belegt"
    verifiziert.append("kraftstoff")

    if leistung_ps is not None:
        if str(leistung_ps) not in text:
            return None, "Leistungsangabe kommt in den zitierten Belegen nicht vor"
        verifiziert.append("leistung_ps")

    if baujahr_von is not None and str(baujahr_von) in text:
        verifiziert.append("baujahr_von")
    if generation and generation.lower() in text:
        verifiziert.append("generation")

    # ── 4. Normalisierung der weichen Felder ─────────────────────────────────
    getriebe_roh = (roh.get("getriebe") or "").strip().lower()
    getriebe = sorted(normalisiere_getriebe(json.dumps([getriebe_roh]))) if getriebe_roh else []
    if getriebe and any(w in text for w in ("automatik", "schalt", "manuell", "dsg", "dct")):
        verifiziert.append("getriebe")

    karosserie_roh = (roh.get("karosserie") or "").strip().lower()
    karosserie = [karosserie_roh] if karosserie_roh in KAROSSERIE_KLASSEN else []
    if not karosserie and karosserie_roh:
        karosserie = sorted(normalisiere_karosserie(json.dumps([karosserie_roh])))
    if karosserie:
        verifiziert.append("karosserie")

    antrieb_roh = (roh.get("antrieb") or "").strip()
    antrieb = antrieb_roh.capitalize() if antrieb_roh.lower() in _ANTRIEB_WERTE else None

    # ── 5. Confidence aus der tatsaechlichen Quellenlage ─────────────────────
    if len(domains) >= 2 and bester_score >= _PRIMAERQUELLE_MIN_SCORE:
        confidence = CONF_HIGH
    elif len(domains) >= 2:
        confidence = CONF_MEDIUM
    else:
        confidence = CONF_LOW

    kandidat = WebKandidat(
        candidate_id=_stabile_web_id(marke, modell, generation, motor),
        marke=marke,
        modell=modell,
        generation=generation,
        motor_bezeichnung=motor or (f"{leistung_ps} PS" if leistung_ps else ""),
        baujahr_von=baujahr_von,
        baujahr_bis=baujahr_bis,
        leistung_ps=leistung_ps,
        kraftstoff=kraftstoff,
        getriebe_klassen=getriebe,
        antrieb=antrieb,
        karosserie_klassen=karosserie,
        source_urls=urls,
        evidence_count=len(indices),
        discovery_confidence=confidence,
        web_verified_fields=verifiziert,
        visual_key=_slug(f"{marke}--{modell}--{generation or ''}"),
    )
    return kandidat, ""


def _entferne_widersprueche(kandidaten: list[WebKandidat]) -> list[WebKandidat]:
    """Zwei Eintraege zur SELBEN Fahrzeugidentitaet mit unterschiedlichen
    technischen Werten sind ein Widerspruch in der Quellenlage — dann ist
    unklar, welcher stimmt, und BEIDE fliegen raus (§7: bei Widerspruch
    ablehnen, nicht den bequemeren waehlen)."""
    nach_identitaet: dict[str, list[WebKandidat]] = {}
    for k in kandidaten:
        nach_identitaet.setdefault(k.candidate_id, []).append(k)

    ergebnis: list[WebKandidat] = []
    for cid, gruppe in nach_identitaet.items():
        if len(gruppe) == 1:
            ergebnis.append(gruppe[0])
            continue
        kraftstoffe = {g.kraftstoff for g in gruppe}
        leistungen = {g.leistung_ps for g in gruppe if g.leistung_ps is not None}
        if len(kraftstoffe) > 1 or len(leistungen) > 1:
            log.info("AutoFinder-Web: widerspruechliche Angaben zu %s — alle verworfen "
                     "(Kraftstoffe=%s, Leistungen=%s)", cid, kraftstoffe, leistungen)
            continue
        ergebnis.append(gruppe[0])
    return ergebnis


# ══════════════════════════════════════════════════════════════════════════
# HARD FILTER + SCORING — bewusst DIESELBE Logik wie intern (§9/§10)
# ══════════════════════════════════════════════════════════════════════════

def _als_engine_roh(k: WebKandidat) -> dict:
    """Uebersetzt einen Web-Kandidaten in die Form, die die Foundation-Funktionen
    lesen. Damit gelten fuer Web-Kandidaten EXAKT dieselben harten Filter, dieselbe
    Score-Logik und dieselbe Datenqualitaets-Rechnung wie fuer DB-Kandidaten —
    statt einer zweiten, womoeglich milderen Web-Variante.

    Alle in der DB vorhandenen, aber im Web nicht belegten Detailfelder stehen
    bewusst auf None: sie zaehlen dann als fehlend (nicht als gut) und druecken
    die Datenqualitaet ehrlich nach unten.
    """
    return {
        "baureihe_id": None,
        "marke": k.marke,
        "modell": k.modell,
        "generation": k.generation,
        "bauzeitraum_von": k.baujahr_von,
        "bauzeitraum_bis": k.baujahr_bis,
        "karosserie": None,
        "segment": None,
        "euro_ncap_sterne": None,
        "variante_id": k.candidate_id,
        "bezeichnung": k.motor_bezeichnung,
        "motorcode": None,
        "kraftstoff": k.kraftstoff,
        "leistung_ps": k.leistung_ps,
        "drehmoment_nm": None,
        "getriebe": ", ".join(k.getriebe_klassen) or None,
        "antrieb": k.antrieb,
        "beschleunigung_0_100": None,
        "verbrauch_wltp": None,
        "verbrauch_real": None,
        "_karo": frozenset(k.karosserie_klassen),
        "_getriebe": frozenset(k.getriebe_klassen),
        "_verbrauch": None,
    }


def _filtere_und_bewerte(kandidaten: list[WebKandidat], request: Any) -> list[WebKandidat]:
    """Wendet die Foundation-Hardfilter und den Foundation-Score an.

    §9: Ist ein Feld fuer den Nutzer ein HARTES Kriterium, der Web-Kandidat
    kennt es aber nicht, faellt er durch — genau das tut
    `erfuellt_harte_filter` bereits fuer DB-Kandidaten (None/leeres Set =>
    kein Treffer). Keine Sonderbehandlung, nur weil ein Kandidat aus dem Web
    kommt.
    """
    behalten: list[WebKandidat] = []
    for k in kandidaten:
        roh = _als_engine_roh(k)
        if not _erfuellt_harte_filter(roh, request):
            log.debug("AutoFinder-Web: %s faellt durch die harten Filter", k.candidate_id)
            continue
        score, gruende = _engine_score(roh, request)
        k.match_score = score
        k.match_gruende = gruende
        k.datenqualitaet = _engine_datenqualitaet(roh)
        behalten.append(k)
    return behalten


# ══════════════════════════════════════════════════════════════════════════
# ZUSAMMENFUEHRUNG (§11)
# ══════════════════════════════════════════════════════════════════════════

def _sortierschluessel(k: Any) -> tuple:
    """Dieselbe Reihenfolge wie `app.autofinder._sortierschluessel`: Score,
    Datenqualitaet, Aktualitaet, dann stabil die Kandidaten-ID."""
    return (-k.match_score, -k.datenqualitaet, -(k.baujahr_von or 0), kandidat_id(k))


def merge_und_diversifiziere(intern: list, web: list, *, k: int,
                              max_pro_marke: int = 2) -> list:
    """Fuehrt interne und Web-Kandidaten in EINE Rangliste zusammen.

    Kein pauschaler DB-Bonus (§11): ein Web-Kandidat, der die Nutzerfilter
    nachweislich besser trifft, steht vorne. Der faktische Vorteil der
    DB-Kandidaten entsteht ohne Zusatzregel von selbst — sie tragen mehr
    belegte Felder und damit hoehere Datenqualitaet, was im Tie-Break zaehlt.

    Die Diversitaetsgrenzen muessen hier NEU angewendet werden: die interne
    Liste war fuer sich genommen bereits diversitaetsgeprueft, aber ein
    hinzukommender Web-Kandidat derselben Marke kann die Marken-Obergrenze
    kippen. Baureihen-Identitaet ist fuer Web-Kandidaten die candidate_id —
    jeder ist damit seine eigene "Baureihe", was korrekt ist: es gibt keine
    zweite Motorvariante desselben Web-Fahrzeugs.
    """
    alle = sorted([*intern, *web], key=_sortierschluessel)

    ausgewaehlt: list = []
    marken: dict[str, int] = {}
    baureihen: set[str] = set()
    for kand in alle:
        if len(ausgewaehlt) >= k:
            break
        marke = getattr(kand, "marke", "") or ""
        gruppe = getattr(kand, "baureihe_id", None) or kandidat_id(kand)
        if marken.get(marke.lower(), 0) >= max_pro_marke:
            continue
        if gruppe in baureihen:
            continue
        ausgewaehlt.append(kand)
        marken[marke.lower()] = marken.get(marke.lower(), 0) + 1
        baureihen.add(gruppe)
    return ausgewaehlt


# ══════════════════════════════════════════════════════════════════════════
# ORCHESTRIERUNG
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class WebDiscoveryErgebnis:
    kandidaten: list[WebKandidat] = field(default_factory=list)
    tavily_calls: int = 0
    gemini_calls: int = 0
    tavily_fehler: bool = False
    gemini_fehler: bool = False
    abgelehnt: list[str] = field(default_factory=list)


async def entdecke_web_kandidaten(request: Any) -> WebDiscoveryErgebnis:
    """Kompletter Web-Fallback: Query -> Tavily -> Gemini -> Validierung ->
    Hardfilter -> Score.

    Wirft NIE (§13). Jeder Fehlschlag endet in einem Ergebnis mit leerer
    Kandidatenliste und gesetzten Diagnose-Flags; der Aufrufer liefert dann
    einfach nur die internen Treffer aus.
    """
    ergebnis = WebDiscoveryErgebnis()
    try:
        queries = _baue_discovery_queries(request)
        if not queries:
            return ergebnis

        evidenzen, calls, tav_fehler = await _suche_evidenzen(queries)
        ergebnis.tavily_calls = calls
        ergebnis.tavily_fehler = tav_fehler
        if not evidenzen:
            return ergebnis

        rohe, gem_calls, gem_fehler = await _strukturiere_mit_gemini(request, evidenzen)
        ergebnis.gemini_calls = gem_calls
        ergebnis.gemini_fehler = gem_fehler
        if not rohe:
            return ergebnis

        geprueft: list[WebKandidat] = []
        for r in rohe:
            kandidat, grund = _pruefe_kandidat(r, evidenzen)
            if kandidat is None:
                ergebnis.abgelehnt.append(f"{r.get('marke')} {r.get('modell')}: {grund}")
                continue
            geprueft.append(kandidat)

        geprueft = _entferne_widersprueche(geprueft)
        ergebnis.kandidaten = _filtere_und_bewerte(geprueft, request)
        return ergebnis
    except Exception:
        log.exception("AutoFinder-Web: unerwarteter Fehler im Web-Fallback — "
                      "liefere nur interne Ergebnisse")
        ergebnis.kandidaten = []
        return ergebnis
