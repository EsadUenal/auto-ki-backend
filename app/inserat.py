from __future__ import annotations

"""
Phase 4 — Seller / Inseratsoptimierung.

Zwei Bausteine, strikt getrennt:

1. DETERMINISTISCH (kein LLM): `build_listing_analyse` bewertet die Qualität des
   Inserats (Vollständigkeit als transparenter Zählwert, KEIN erfundener KI-Score),
   erkennt fehlende Angaben (kategorisiert), Vertrauensfaktoren, Verkaufsargumente,
   Widersprüche (Beschreibung vs. Strukturdaten) und leitet einen Titelvorschlag ab.

2. LLM (on-demand): `run_inserat_optimierung` erzeugt Titel + Beschreibung — der
   LLM ist AUSSCHLIESSLICH Textersteller. `pruefe_fakten` prüft das Ergebnis danach
   gegen die Eingangsdaten und entfernt/neutralisiert unbelegte Positiv-Behauptungen
   (unfallfrei, Scheckheft, TÜV neu, Vorbesitzer, nicht genannte Ausstattung). Bekannte
   Mängel bleiben ehrlich erhalten. Grundsatz: ENFAL fügt NIE eine positive Aussage
   hinzu, die der Nutzer nicht angegeben hat.
"""

import logging
import re
from datetime import date, datetime

from app.car_lookup import call_gemini_json
from app.claim_sicherheit import entschaerfe_verstaerkungen
from app.key_findings import _AUSSTATTUNG_WERTVOLL, _ausstattung_treffer, _kraftstoff_norm
from app.models import FehlendeAngabe, InseratOptimierung, ListingAnalyse, VerkaufsCheckRequest
from app.schreibstil import STILREGEL_GEDANKENSTRICHE

log = logging.getLogger(__name__)


# ══ Helfer ═══════════════════════════════════════════════════════════════════

def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _eur(n) -> str:
    try:
        return f"{round(n):,} €".replace(",", ".")
    except (TypeError, ValueError):
        return "—"


def _hat_text(req: VerkaufsCheckRequest) -> str:
    """Der vom Verkäufer eingegebene Beschreibungstext (Inserats-Freitext)."""
    return " ".join(filter(None, [getattr(req, "inserat_text", None),
                                   getattr(req, "beschreibung", None)]))


def _marktvergleich_insight(insights):
    for i in insights or []:
        if getattr(i, "kategorie", None) == "marktvergleich":
            return i
    return None


# ── Getriebe-/Kraftstoff-Normierung für die Widerspruchsprüfung ──────────────
_AUTOMATIK_WORTE = ("automatik", "dsg", "s-tronic", "s tronic", "tiptronic", "wandler",
                    "doppelkupplung", "pdk", "steptronic", "g-tronic", "7g", "9g")
_SCHALT_WORTE = ("schaltgetriebe", "schalter", "handschalt", "manuell", "gang schalt",
                 "gang-schalt", "handschaltung")


def _getriebe_norm(text: str | None) -> str | None:
    t = _norm(text)
    if not t:
        return None
    # Schalt-Signale zuerst prüfen ("6-Gang Schaltgetriebe" enthält kein Automatik-Wort).
    if any(w in t for w in _SCHALT_WORTE):
        return "schalt"
    if any(w in t for w in _AUTOMATIK_WORTE):
        return "automatik"
    return None


# ══ 0) GEMEINSAME STATUSAUSWERTUNG (VK-03/04/05) ═════════════════════════════
#
# Vollständigkeit, Transparenz, Wertminderer, beide Inseratspfade, der Fakten-Guard und die Verhandlung lesen
# dieselben Zustände. Unterschieden wird NICHT angegeben, ausdrücklich UNBEKANNT und die positive/negative Angabe.
# Eine ehrlich kommunizierte Unsicherheit ist kein Verschweigen, bleibt aber eine sachliche Unsicherheit. Die
# Strukturangabe ist kanonisch; eine eindeutig zuordenbare Aussage im Inseratstext füllt nur eine LEERE Struktur auf,
# ein Widerspruch wird gemeldet (`finde_widersprueche`) statt eine Seite still zu überschreiben.

UNFALL_JA, UNFALL_NEIN = "ja", "nein"
UNFALL_UNBEKANNT, UNFALL_NICHT_ANGEGEBEN = "unbekannt", "nicht_angegeben"
NACHWEIS_VOLLSTAENDIG, NACHWEIS_TEILWEISE, NACHWEIS_KEINE = "vollstaendig", "teilweise", "keine"
NACHWEIS_UNBEKANNT, NACHWEIS_NICHT_ANGEGEBEN = "unbekannt", "nicht_angegeben"

# Aussage ÜBER den Unfallstatus als Thema ("Unfallfreiheit", "Unfallstatus") ist noch keine Behauptung.
_UNFALL_THEMA = re.compile(r"unfallfreiheit|unfallstatus|unfallhistorie|unf[aä]lle?\b|unfallsch[aä]d\w*|unfallfrei|unfallwagen",
                           re.IGNORECASE)
# Positive Behauptung: das Fahrzeug SEI unfallfrei / hatte keinen Unfall.
_UNFALL_ANSPRUCH = re.compile(
    r"unfallfrei(?!heit)|unfallschadenfrei|kein(?:e|en|em|er|erlei)?\s+(?:\w+\s+)?unf[aä]ll?\w*"
    r"|ohne\s+(?:\w+\s+)?unf[aä]ll?\w*", re.IGNORECASE)
# Negative Tatsachenbehauptung: es GAB einen Unfall. Braucht ebenfalls eine Grundlage.
_UNFALL_NEGATIV = re.compile(
    r"nicht\s+unfallfrei|unfallsch[aä]d\w*\s+vorhanden|(?:hatte|hat|gab)\s+(?:\w+\s+)?(?:einen\s+)?unfall|unfall\s+gehabt"
    r"|unfallwagen|unfallfahrzeug", re.IGNORECASE)
# Ausdrückliche Unsicherheit des Verkäufers über den Status.
_UNSICHER = re.compile(
    r"keine\s+(?:sichere|verlässliche|belastbare|eindeutige|genaue)\w*\s+(?:angabe|aussage|auskunft)"
    r"|nicht\s+(?:sicher|bekannt|beurteilen|beantworten)|kann\s+(?:ich\s+)?(?:dazu\s+|hierzu\s+)?(?:keine|nicht)\b"
    r"|(?:ist|bleibt|sind)\s+(?:mir\s+)?(?:\w+\s+)?unbekannt|unsicher|unklar|wei(?:ß|ss)\s+ich\s+nicht",
    re.IGNORECASE)


def _saetze(text: str | None) -> list[str]:
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n+", text or "") if x.strip()]


def unfall_status(req) -> tuple[str, str | None]:
    """(Zustand, Quelle): ja | nein | unbekannt | nicht_angegeben; Quelle "angabe" (Formular) oder "inserat" (eindeutiger
    Unsicherheitssatz im Text, nur wenn das Formular leer ist). Aus `None` allein wird KEIN "unbekannt" gemacht."""
    roh = _norm(getattr(req, "unfallfrei", None))
    if roh in ("ja", "nein", "unbekannt"):
        return roh, "angabe"
    if roh and roh not in ("nicht angegeben", "keine angabe", "n/a", "-"):
        return UNFALL_UNBEKANNT, "angabe"
    for satz in _saetze(_hat_text(req)):
        if (_UNFALL_THEMA.search(satz) and _UNSICHER.search(satz)
                and not _UNFALL_ANSPRUCH.search(satz) and not _UNFALL_NEGATIV.search(satz)):
            return UNFALL_UNBEKANNT, "inserat"
    return UNFALL_NICHT_ANGEGEBEN, None


_WARTUNG_KEINE = re.compile(
    r"(?:keine|ohne)\s+(?:\w+\s+)?(?:wartungs|service)(?:nachweis|unterlagen|historie)\w*"
    r"|(?:wartungs|service)nachweise?\s+(?:sind\s+|ist\s+)?(?:nicht|keine)\s+vorhanden", re.IGNORECASE)
_WARTUNG_TEILWEISE = re.compile(
    r"(?:wartungs|service)nachweis\w*\s+(?:sind\s+|ist\s+)?(?:nur\s+)?(?:teilweise|lückenhaft|unvollständig)"
    r"|(?:teilweise|lückenhaft\w*)\s+(?:vorhandene?\w*\s+)?(?:wartungs|service)\w*"
    r"|nicht\s+vollständig\w*\s+(?:wartungs|service)\w*|(?:wartungs|service)\w*\s+(?:sind\s+)?nicht\s+vollständig",
    re.IGNORECASE)
_WARTUNG_VOLL = re.compile(r"(?:vollständige?[nrs]?|lückenlose?[nrs]?)\s+(?:wartungs|service)\w*", re.IGNORECASE)
_WARTUNG_UNBEKANNT = re.compile(r"(?:wartungs|service)\w*\s+(?:ist|sind)\s+(?:mir\s+)?unbekannt", re.IGNORECASE)


def wartung_text_aussage(text: str | None) -> str | None:
    """Eindeutige Aussage des Inseratstexts zu den Wartungsnachweisen oder None (keine/mehrdeutige Fundstelle)."""
    treffer: set[str] = set()
    for satz in _saetze(text):
        if _WARTUNG_KEINE.search(satz):
            treffer.add(NACHWEIS_KEINE)
        elif _WARTUNG_TEILWEISE.search(satz):
            treffer.add(NACHWEIS_TEILWEISE)
        elif _WARTUNG_UNBEKANNT.search(satz):
            treffer.add(NACHWEIS_UNBEKANNT)
        elif _WARTUNG_VOLL.search(satz):
            treffer.add(NACHWEIS_VOLLSTAENDIG)
    return next(iter(treffer)) if len(treffer) == 1 else None


def wartung_status(req) -> dict:
    """{"nachweise": vollstaendig|teilweise|keine|unbekannt|nicht_angegeben, "quelle": angabe|inserat|None,
    "scheckheft": True|False|None, "letzter_service": bool}.

    Die Nachweise sind die eigene Angabe des Verkäufers (keine Dokumentprüfung durch ENFAL). Der letzte Service ist eine
    EIGENE Angabe und kein Beleg vorhandener Nachweise. Ein nicht gesetztes Scheckheft ist keine Aussage über alle
    Wartungsnachweise."""
    roh = _norm(getattr(req, "wartungsnachweise", None))
    if roh in (NACHWEIS_VOLLSTAENDIG, NACHWEIS_TEILWEISE, NACHWEIS_KEINE):
        nachweise, quelle = roh, "angabe"
    elif roh:
        nachweise, quelle = NACHWEIS_UNBEKANNT, "angabe"
    else:
        text = wartung_text_aussage(_hat_text(req))
        nachweise, quelle = (text, "inserat") if text else (NACHWEIS_NICHT_ANGEGEBEN, None)
    return {"nachweise": nachweise, "quelle": quelle,
            "scheckheft": getattr(req, "scheckheftgepflegt", None),
            "letzter_service": bool(getattr(req, "letzter_service_datum", None)
                                    or getattr(req, "letzter_service_km", None))}


def wartung_angegeben(req) -> bool:
    """Hat der Verkäufer etwas zu Scheckheft ODER Wartungsnachweisen gesagt? (Service allein zählt nicht.)"""
    w = wartung_status(req)
    return w["nachweise"] != NACHWEIS_NICHT_ANGEGEBEN or w["scheckheft"] is not None


# ══ 1) DETERMINISTISCHE INSERATSANALYSE ══════════════════════════════════════

# Wichtige Angaben, die in die Vollständigkeit einfließen (fester Katalog -> der
# "x von n"-Wert ist transparent und reproduzierbar, KEIN 0–100-KI-Score).
def _pruefe_vollstaendigkeit(req: VerkaufsCheckRequest) -> tuple[int, int, list[FehlendeAngabe]]:
    text_da = bool(_hat_text(req))
    # (label, vorhanden?, wichtigkeit) — Reihenfolge = Anzeigereihenfolge.
    katalog: list[tuple[str, bool, str]] = [
        ("Baujahr",              bool(getattr(req, "baujahr", None)),        "kritisch"),
        ("Kilometerstand",       bool(getattr(req, "kilometerstand", None)), "kritisch"),
        ("Kraftstoff",           bool(getattr(req, "kraftstoff", None)),     "wichtig"),
        ("Getriebe",             bool(getattr(req, "getriebe", None)),       "wichtig"),
        ("Leistung / Motor",     bool(getattr(req, "motor", None)),          "optional"),
        ("TÜV/HU",               bool(getattr(req, "tuev_bis", None)),       "wichtig"),
        ("Unfallfreiheit",       unfall_status(req)[0] != UNFALL_NICHT_ANGEGEBEN, "wichtig"),
        ("Anzahl Vorbesitzer",   getattr(req, "vorbesitzer", None) is not None, "wichtig"),
        ("Scheckheft / Wartung", wartung_angegeben(req),                     "wichtig"),
        ("Ausstattung",          bool(getattr(req, "ausstattung", None)),    "wichtig"),
        ("Zustandsbeschreibung", text_da,                                    "wichtig"),
    ]
    vorhanden = sum(1 for _, da, _ in katalog if da)
    gesamt = len(katalog)
    fehlend = [FehlendeAngabe(feld=label, wichtigkeit=w) for label, da, w in katalog if not da]
    # Farbe zusätzlich als optionale Angabe führen (nicht im "x von n"-Zähler).
    if not getattr(req, "farbe", None):
        fehlend.append(FehlendeAngabe(feld="Farbe", wichtigkeit="optional"))
    return vorhanden, gesamt, fehlend


def _tuev_lange_gueltig(tuev_bis: str | None) -> bool:
    """True, wenn der TÜV/HU-Termin (MM/YYYY o.ä.) mehr als ~12 Monate in der Zukunft liegt."""
    if not tuev_bis:
        return False
    m = re.search(r"(0?[1-9]|1[0-2])\s*[/.\-]\s*((?:20)?\d{2})", tuev_bis)
    if not m:
        # Nur ein Jahr angegeben?
        j = re.search(r"\b(20\d{2})\b", tuev_bis)
        if not j:
            return False
        return int(j.group(1)) - date.today().year >= 1
    monat = int(m.group(1))
    jahr = int(m.group(2))
    if jahr < 100:
        jahr += 2000
    heute = date.today()
    monate_hin = (jahr - heute.year) * 12 + (monat - heute.month)
    return monate_hin >= 12


def _vertrauensfaktoren(req: VerkaufsCheckRequest) -> list[str]:
    """Angaben, die das Inserat glaubwürdiger machen — NUR wenn tatsächlich vorhanden."""
    out: list[str] = []
    if getattr(req, "scheckheftgepflegt", None) is True:
        out.append("Scheckheftgepflegt")
    if _norm(getattr(req, "unfallfrei", None)) == "ja":
        out.append("Als unfallfrei angegeben")
    vb = getattr(req, "vorbesitzer", None)
    if vb == 1:
        out.append("Aus erster Hand (1 Vorbesitzer)")
    elif isinstance(vb, int) and vb > 1:
        out.append(f"Anzahl Vorbesitzer klar angegeben ({vb})")
    tuev = getattr(req, "tuev_bis", None)
    if tuev:
        out.append(f"TÜV/HU angegeben ({tuev})" + (" (noch lange gültig)" if _tuev_lange_gueltig(tuev) else ""))
    if getattr(req, "maengel", None):
        out.append("Bekannte Mängel offen genannt")
    return out


def _verkaufsargumente(req: VerkaufsCheckRequest) -> list[str]:
    """Stärkste Verkaufsargumente aus Ausstattung + Fahrzeugdaten (max. 6).

    Nutzt die bestehende, LED-vs-Leder-sichere Ausstattungserkennung. KEINE
    erfundenen €-Wertsteigerungen.
    """
    args: list[str] = list(_ausstattung_treffer(getattr(req, "ausstattung", None) or []))
    # Geringe Laufleistung
    bj = getattr(req, "baujahr", None)
    km = getattr(req, "kilometerstand", None)
    if bj and km:
        alter = max(1, date.today().year - bj)
        if alter >= 2 and km / alter <= 10_000:
            args.append("Geringe Laufleistung")
    # Frischer/langer TÜV
    if _tuev_lange_gueltig(getattr(req, "tuev_bis", None)):
        args.append("TÜV/HU noch lange gültig")
    # Wartung: VerkaufsCheck RC1 — "scheckheftgepflegt" ist eine Angabe des
    # Verkäufers, KEINE nachgewiesene "vollständige Wartungshistorie".
    if getattr(req, "scheckheftgepflegt", None) is True and "Scheckheftgepflegt" not in args:
        args.append("Scheckheftgepflegt (laut Angabe)")
    # dedupliziert, max 6
    ausgabe: list[str] = []
    for a in args:
        if a not in ausgabe:
            ausgabe.append(a)
    return ausgabe[:6]


def finde_widersprueche(req: VerkaufsCheckRequest) -> list[str]:
    """Deterministische Widersprüche zwischen Beschreibungstext und Strukturangaben.

    Erkennt nur EINDEUTIGE Widersprüche (keine LLM-Interpretation): Kraftstoff,
    Getriebe, Unfallstatus. Kein Treffer -> leere Liste.
    """
    out: list[str] = []
    text = _hat_text(req)
    if not text:
        return out
    tnorm = _norm(text)

    # Kraftstoff: Beschreibung vs. Strukturfeld/Motor.
    text_kraft = _kraftstoff_norm(text)
    struk_kraft = _kraftstoff_norm(getattr(req, "kraftstoff", None)) \
        or _kraftstoff_norm(getattr(req, "motor", None))
    if text_kraft and struk_kraft and text_kraft != struk_kraft:
        out.append(f"Beschreibung deutet auf {text_kraft.capitalize()} hin, angegeben ist "
                   f"{struk_kraft.capitalize()}.")

    # Getriebe: Beschreibung vs. Strukturfeld.
    text_getr = _getriebe_norm(text)
    struk_getr = _getriebe_norm(getattr(req, "getriebe", None))
    if text_getr and struk_getr and text_getr != struk_getr:
        label = {"automatik": "Automatik", "schalt": "Schaltgetriebe"}
        out.append(f"Beschreibung nennt {label[text_getr]}, angegeben ist {label[struk_getr]}.")

    # Unfallstatus: Beschreibung behauptet unfallfrei, Angabe ist Unfallschaden.
    behauptet_unfallfrei = bool(re.search(r"unfallfrei|kein(?:e|erlei)?\s+unfall|ohne\s+unfall", tnorm))
    if behauptet_unfallfrei and _norm(getattr(req, "unfallfrei", None)) == "nein":
        out.append("Beschreibung sagt „unfallfrei“, angegeben ist ein Unfallschaden.")

    # VerkaufsCheck RC1 (§14): Zahlenangaben im Text gegen die Strukturfelder.
    # Gemeldet wird NUR ein echter Widerspruch; stimmen beide überein, entsteht
    # keine künstliche Warnung. Die Strukturangabe bleibt in jedem Fall kanonisch.
    for label, feldwert, muster, toleranz in (
        ("Kilometerstand", getattr(req, "kilometerstand", None),
         r"(\d{1,3}(?:[.\s]\d{3})+|\d{2,7})\s*km\b", 1),
        ("Baujahr", getattr(req, "baujahr", None), r"baujahr\s*:?\s*((?:19|20)\d{2})", 0),
        ("Vorbesitzer", getattr(req, "vorbesitzer", None),
         r"(\d{1,2})\s*(?:vorbesitzer\w*|halter\w*)\b", 0),
        ("Preisvorstellung", getattr(req, "preis_vorstellung", None),
         r"(\d{1,3}(?:[.\s]\d{3})+|\d{3,7})\s*(?:€|eur\b)", 1),
    ):
        if not feldwert:
            continue
        if label == "Kilometerstand":
            werte = _aktuelle_km_werte(tnorm, muster)
        else:
            werte = {int(re.sub(r"[.\s]", "", m)) for m in re.findall(muster, tnorm)}
        if werte and all(abs(w - int(feldwert)) > toleranz for w in werte):
            genannt = f"{sorted(werte)[0]:,}".replace(",", ".")
            angegeben = f"{int(feldwert):,}".replace(",", ".")
            out.append(f"Beschreibung nennt {label} {genannt}, angegeben ist {angegeben}.")

    tuev = _norm(getattr(req, "tuev_bis", None))
    if tuev:
        termine = _hu_termine_im_text(tnorm)
        if termine and tuev not in termine:
            out.append(f"Beschreibung nennt den Termin {sorted(termine)[0]}, angegeben ist "
                       f"HU bis {getattr(req, 'tuev_bis')}.")

    # Unfallstatus: Formular und Text widersprechen sich. Die Strukturangabe bleibt kanonisch, beide Seiten werden genannt.
    uf_struktur = _norm(getattr(req, "unfallfrei", None))
    if uf_struktur == "ja" and any(
            _UNFALL_THEMA.search(s) and _UNSICHER.search(s) and not _UNFALL_ANSPRUCH.search(s)
            and not _UNFALL_NEGATIV.search(s) for s in _saetze(text)):
        out.append("Beschreibung nennt Unsicherheit zum Unfallstatus, angegeben ist unfallfrei.")
    # Wartungsnachweise: Formular und Text widersprechen sich.
    nachweis_struktur = _norm(getattr(req, "wartungsnachweise", None))
    nachweis_text = wartung_text_aussage(text)
    if nachweis_struktur in (NACHWEIS_VOLLSTAENDIG, NACHWEIS_TEILWEISE, NACHWEIS_KEINE) and nachweis_text \
            and nachweis_text != nachweis_struktur:
        label = {NACHWEIS_VOLLSTAENDIG: "vollständig", NACHWEIS_TEILWEISE: "teilweise", NACHWEIS_KEINE: "keine",
                 NACHWEIS_UNBEKANNT: "unbekannt"}
        out.append(f"Beschreibung nennt Wartungsnachweise {label[nachweis_text]}, angegeben ist "
                   f"{label[nachweis_struktur]}.")

    return out


# Kilometerangabe und ihre Bedeutung (G1): "Letzter Service bei 177.000 km" oder "Zahnriemen bei 120.000 km gewechselt"
# ist ein historischer Stand, kein aktueller Kilometerstand. Entscheidend ist das NÄCHSTE Bedeutungswort vor (und, falls
# davor keines steht, kurz nach) der Zahl innerhalb derselben Aussage: nennt es eine Wartung/Reparatur, ist der Wert
# historisch und bildet keinen Konflikt zum aktuellen Stand; ein Wort wie "Kilometerstand"/"Laufleistung"/"gelaufen"
# macht ihn zum aktuellen Stand. Eine Zahl ohne jedes Bedeutungswort bleibt wie bisher eine Angabe zum Fahrzeug.
_KM_HISTORISCH = re.compile(
    r"service|inspektion|wartung|[öo]lwechsel|zahnriemen|reparatur|repariert|getauscht|gewechselt|erneuert|"
    r"bremsen\s+neu|kupplung\s+neu|scheckheft|serviceintervall|bei\s+der\s+(?:letzten\s+)?(?:wartung|inspektion)",
    re.IGNORECASE)
_KM_AKTUELL = re.compile(r"kilometerstand|laufleistung|gelaufen|tachostand|\btacho\b|aktuell|km-stand|km\s*stand",
                         re.IGNORECASE)
_AUSSAGEGRENZE = (". ", "\n", ";", "!", "?")


def _aktuelle_km_werte(tnorm: str, muster: str) -> set[int]:
    werte: set[int] = set()
    for m in re.finditer(muster, tnorm):
        vorn = tnorm[:m.start()]
        grenze = max(vorn.rfind(g) for g in _AUSSAGEGRENZE)
        fenster = vorn[grenze + 1:][-60:]
        hist = [x.start() for x in _KM_HISTORISCH.finditer(fenster)]
        aktuell = [x.start() for x in _KM_AKTUELL.finditer(fenster)]
        if hist or aktuell:
            historisch = bool(hist) and (not aktuell or max(hist) > max(aktuell))
        else:
            rest = tnorm[m.end():]
            enden = [rest.find(g) for g in _AUSSAGEGRENZE if rest.find(g) >= 0]
            hinten = rest[:min(enden) if enden else len(rest)][:35]
            historisch = bool(_KM_HISTORISCH.search(hinten)) and not _KM_AKTUELL.search(hinten)
        if not historisch:
            werte.add(int(re.sub(r"[.\s]", "", m.group(1))))
    return werte


# Datum und seine Bedeutung (VK-08): "08/2027" ist nur dann ein HU-Termin, wenn die Fundstelle ihn als HU/TÜV ausweist.
# Service-, Inspektions-, Erstzulassungs- oder Kaufdaten sind keine HU-Termine; ein Datum ohne eindeutigen Bezug (oder
# mit gleichzeitigem Service- und HU-Wort davor) ist mehrdeutig und erzeugt keine Konfliktbehauptung.
_HU_WORT = re.compile(r"\b(?:t[üu]v|hu|hauptuntersuchung|plakette)\b", re.IGNORECASE)
_SERVICE_WORT = re.compile(
    r"service|inspektion|wartung|[öo]lwechsel|erstzulassung|\bez\b|zugelassen|kaufdatum|gekauft|reparatur|zahnriemen",
    re.IGNORECASE)


def _hu_termine_im_text(tnorm: str) -> set[str]:
    termine: set[str] = set()
    for m in re.finditer(r"(0?[1-9]|1[0-2])\s*[/.]\s*(20\d{2})", tnorm):
        vorn = tnorm[:m.start()]
        grenze = max(vorn.rfind(". "), vorn.rfind("\n"), vorn.rfind(";"), vorn.rfind("!"), vorn.rfind("?"))
        klausel = vorn[grenze + 1:][-70:]
        hu = [h.end() for h in _HU_WORT.finditer(klausel)]
        service = [x.start() for x in _SERVICE_WORT.finditer(klausel)]
        if hu and (not service or max(hu) > max(service)):
            termine.add(f"{int(m.group(1)):02d}/{m.group(2)}")
    return termine


def baue_titel_vorschlag(req: VerkaufsCheckRequest, baureihe: dict | None = None,
                         motor_match: dict | None = None) -> str | None:
    """Professioneller, verkaufsstarker Titelvorschlag AUSSCHLIESSLICH aus vorhandenen
    Daten — keine erfundene Ausstattung, keine Superlative/Spam."""
    teile: list[str] = []
    basis = " ".join(filter(None, [getattr(req, "marke", None), getattr(req, "modell", None)])).strip()
    if not basis:
        return None
    # Motor nur anhängen, wenn er nicht ohnehin schon im Modellnamen steht.
    motor = (getattr(req, "motor", None) or "").strip()
    if motor and _norm(motor) not in _norm(basis):
        # nur die kompakte Kern-Bezeichnung (erstes "Wort"), keine langen Freitexte
        kern = motor.split(",")[0].strip()
        if kern and _norm(kern) not in _norm(basis) and len(kern) <= 40:
            basis = f"{basis} {kern}"
    teile.append(basis)

    # Karosserie aus DB-Profil (falls vorhanden).
    if baureihe and baureihe.get("karosserie"):
        teile.append(str(baureihe["karosserie"][0]))

    # Bis zu 3 wertige Ausstattungsmerkmale.
    for a in _ausstattung_treffer(getattr(req, "ausstattung", None) or [])[:3]:
        teile.append(a)

    # Vertrauens-Kürzel nur bei Beleg.
    if getattr(req, "scheckheftgepflegt", None) is True:
        teile.append("Scheckheft")

    return " | ".join(teile)


def _preis_hinweis(insights) -> tuple[str | None, list[str]]:
    """Verständliche Preiseinordnung aus der BESTEHENDEN Marktanalyse (keine neue
    Preislogik). Rückgabe: (hinweis, evidence_ids)."""
    mv = _marktvergleich_insight(insights)
    ma = getattr(mv, "marktanalyse", None) if mv else None
    if not ma or not ma.median_eur:
        return (None, [])
    ev = [mv.id]
    if ma.angebot_eur and ma.differenz_pct is not None:
        pct = ma.differenz_pct
        if pct >= 8:
            return (f"Deine Preisvorstellung ({_eur(ma.angebot_eur)}) liegt rund {abs(pct):.0f} % über dem "
                    f"Marktmedian ({_eur(ma.median_eur)}). Das kann die Verkaufszeit verlängern. "
                    f"Höher nur bei belegbaren Vorteilen ansetzen.", ev)
        if pct <= -8:
            return (f"Deine Preisvorstellung ({_eur(ma.angebot_eur)}) liegt rund {abs(pct):.0f} % unter dem "
                    f"Marktmedian ({_eur(ma.median_eur)}). Ein höherer Startpreis ist realistisch.", ev)
        return (f"Deine Preisvorstellung ({_eur(ma.angebot_eur)}) liegt nah am Marktmedian "
                f"({_eur(ma.median_eur)}), also realistisch angesetzt.", ev)
    return (f"Marktmedian vergleichbarer Fahrzeuge: {_eur(ma.median_eur)}. Ergänze eine "
            f"Preisvorstellung, um deine Position einzuordnen.", ev)


def _qualitaet_label(vorhanden: int, gesamt: int, hat_widerspruch: bool) -> str:
    ratio = vorhanden / gesamt if gesamt else 0.0
    if ratio >= 0.9:
        label = "sehr_gut"
    elif ratio >= 0.7:
        label = "gut"
    elif ratio >= 0.5:
        label = "verbesserbar"
    else:
        label = "unvollstaendig"
    # Ein Widerspruch ist ein ernstes Vertrauensproblem -> nie "sehr gut"/"gut".
    if hat_widerspruch and label in ("sehr_gut", "gut"):
        label = "verbesserbar"
    return label


def build_listing_analyse(req: VerkaufsCheckRequest, baureihe: dict | None,
                          motor_match: dict | None, insights) -> ListingAnalyse:
    """Deterministische Inseratsanalyse (kein LLM). Siehe Modul-Docstring."""
    vorhanden, gesamt, fehlend = _pruefe_vollstaendigkeit(req)
    staerken = _vertrauensfaktoren(req)
    argumente = _verkaufsargumente(req)
    probleme = finde_widersprueche(req)
    preis_hinweis, ev_ids = _preis_hinweis(insights)

    # Konkrete Verbesserungsempfehlungen — nur aus tatsächlich Vorhandenem/Fehlendem.
    verbesserungen: list[str] = []
    wichtig_fehlend = [f.feld for f in fehlend if f.wichtigkeit in ("kritisch", "wichtig")]
    if wichtig_fehlend:
        verbesserungen.append("Wichtige Angaben ergänzen: " + ", ".join(wichtig_fehlend[:5]) + ".")
    if not _hat_text(req):
        verbesserungen.append("Eine aussagekräftige Beschreibung hinzufügen (Zustand, Historie, Besonderheiten).")
    if probleme:
        verbesserungen.append("Widersprüche zwischen Beschreibung und Angaben auflösen.")
    if argumente:
        verbesserungen.append("Wertsteigernde Ausstattung sichtbar im Titel und oben in der Beschreibung hervorheben.")

    qualitaet = _qualitaet_label(vorhanden, gesamt, bool(probleme))

    return ListingAnalyse(
        qualitaet=qualitaet,
        vorhanden=vorhanden,
        gesamt=gesamt,
        staerken=staerken,
        verkaufsargumente=argumente,
        fehlende_angaben=fehlend,
        probleme=probleme,
        verbesserungen=verbesserungen[:4],
        preis_hinweis=preis_hinweis,
        titel_vorschlag=baue_titel_vorschlag(req, baureihe, motor_match),
        evidence_ids=ev_ids,
    )


# ══ 2) FAKTEN-SCHUTZ (deterministisch, vor jeder Rückgabe ans Frontend) ══════

def _ausstattung_backed(req: VerkaufsCheckRequest) -> set[str]:
    """Menge der Ausstattungs-Keywords, die der Nutzer TATSÄCHLICH angegeben hat
    (normalisierter Text der Ausstattungsliste)."""
    text = _norm(" ".join(getattr(req, "ausstattung", None) or []))
    backed: set[str] = set()
    for key in _AUSSTATTUNG_WERTVOLL:
        pat = rf"(?<![a-zäöüß]){re.escape(key)}"
        if key == "led":
            pat += r"(?!er)"     # 'led', aber nicht 'leder'
        if re.search(pat, text):
            backed.add(key)
    return backed


def _keyword_in(text_norm: str, key: str) -> bool:
    pat = rf"(?<![a-zäöüß]){re.escape(key)}"
    if key == "led":
        pat += r"(?!er)"
    return re.search(pat, text_norm) is not None


def _neutralize_tuev_qualifier(text: str) -> str:
    """Entfernt nicht verifizierbare 'neu/frisch'-Qualifier rund um TÜV/HU
    (der reine TÜV-Termin bleibt erhalten, falls belegt)."""
    text = re.sub(r"\b(nagel)?neuer?\s+(t[üu]v|hu|hauptuntersuchung)", r"\2", text, flags=re.IGNORECASE)
    text = re.sub(r"\bfrischer?\s+(t[üu]v|hu|hauptuntersuchung)", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(t[üu]v|hu|hauptuntersuchung)\s+(nagelneu|neu|frisch)\b", r"\1", text, flags=re.IGNORECASE)
    return text


# Konkrete Schadens-/Reparaturdetails in einer Unfallaussage. Das Unfallereignis selbst trägt `unfallfrei="nein"`;
# Schadensort, Reparatur und Reparaturqualität trägt es NICHT.
_UNFALL_SCHADENSBEZUG = re.compile(r"unfallsch[aä]d\w*|unf[aä]lle?\b", re.IGNORECASE)
_UNFALL_REPARATUR = re.compile(
    r"repar\w*|instandgesetzt|behoben|beseitigt|ausgebessert|fachgerecht\w*|lackier\w*|erneuert|ersetzt|getauscht|"
    r"ausgebeult|gerichtet|werkstatt\w*", re.IGNORECASE)
_UNFALL_ORT = re.compile(
    r"\b(?:an|am|im|auf|in)\s+(?:der|dem|den|einer|einem)?\s*([a-zäöüß]{5,})|\b(vorne|hinten|links|rechts|seitlich|front\w*|heck\w*)\b",
    re.IGNORECASE)
_UNFALL_ORT_IGNORIEREN = {"fahrzeug", "fahrzeugs", "wagen", "auto", "vorbesitzer", "vorbesitzers", "vergangenheit"}


def _stamm(wort: str) -> str:
    return wort.lower().replace("ß", "ss")[:6]


def _verkaeufer_detailtext(req) -> str:
    """Klauseln der Verkäuferangaben, die selbst von einem Unfall sprechen (und ihn nicht als unklar benennen).
    Eine bloße Nachlackierung/Reparaturangabe ohne Unfallbezug ist kein Beleg für Schaden oder Reparatur NACH einem Unfall."""
    teile = [getattr(req, f, None) for f in ("vorschaeden", "inserat_text", "beschreibung", "tuning")]
    for f in ("maengel", "technische_maengel", "optische_maengel"):
        teile.extend(getattr(req, f, None) or [])
    klauseln: list[str] = []
    for t in teile:
        for k in re.split(r"[.;!?\n]+", str(t or "")):
            if _UNFALL_THEMA.search(k) and not _UNSICHER.search(k) and not _UNFALL_ANSPRUCH.search(k):
                klauseln.append(k)
    return " ".join(klauseln).lower().replace("ß", "ss")


def _unfall_details_belegt(satz: str, req) -> bool:
    """Sind die konkreten Schadens-/Reparaturdetails des Satzes durch eigene Verkäuferangaben gedeckt?
    Reparaturaussagen brauchen eine Reparaturangabe, Ortsangaben (Bauteil, Seite) die Nennung desselben Orts.
    `unfallfrei="nein"` allein legitimiert beides nicht; Nachlackierung allein ist kein Unfallbeleg."""
    text = _verkaeufer_detailtext(req)
    for m in _UNFALL_REPARATUR.finditer(satz):
        if _stamm(m.group(0)) not in text and m.group(0).lower()[:5] not in text:
            return False
    for m in _UNFALL_ORT.finditer(satz):
        ort = m.group(1) or m.group(2)
        if ort.lower() in _UNFALL_ORT_IGNORIEREN or ort.lower().startswith("unfall"):
            continue
        if _stamm(ort) not in text:
            return False
    return True


def _unfall_satz_belegt(satz: str, req) -> bool:
    """Darf dieser Satz zum Unfallstatus stehen bleiben? Aussagenbezogen, nicht nach Negationswörtern.

    * Positive Behauptung ("unfallfrei", "kein Unfall", "ohne Unfall"): nur bei Angabe "ja". Ein Negations- oder
      Unsicherheitswort IRGENDWO im Satz ändert daran nichts ("Keine Mängel, unfallfrei" bleibt gesperrt).
    * Negative Tatsachenbehauptung ("nicht unfallfrei", "hatte einen Unfall", "Unfallschaden ..."): nur bei Angabe
      "nein" UND nur mit gedeckten Details. Schadensort und Reparatur ("an der Stoßstange repariert") müssen in den
      Verkäuferangaben stehen; das allgemeine Unfallereignis legitimiert sie nicht.
    * Reiner Unsicherheitssatz über das Thema ("Zur Unfallfreiheit kann ich keine sichere Angabe machen"): nur, wenn der
      Verkäufer die Unsicherheit selbst angibt (Formular "unklar" oder eindeutiger Satz im Inserat). Eine vom Modell
      erfundene Unsicherheit ist eine unbelegte Behauptung über das Wissen des Verkäufers.
    * Jede andere Aussage zur Unfallfreiheit ("Unfallfreiheit garantiert"): nur bei "ja"."""
    zustand = unfall_status(req)[0]
    if _UNFALL_NEGATIV.search(satz):
        return zustand == UNFALL_NEIN and _unfall_details_belegt(satz, req)
    if _UNFALL_ANSPRUCH.search(satz):
        return zustand == UNFALL_JA
    if _UNSICHER.search(satz):
        return zustand == UNFALL_UNBEKANNT
    if _UNFALL_SCHADENSBEZUG.search(satz):
        return zustand == UNFALL_NEIN and _unfall_details_belegt(satz, req)
    return zustand == UNFALL_JA


def _ist_belegt(belegt, satz: str) -> bool:
    return bool(belegt(satz)) if callable(belegt) else bool(belegt)


def _claim_rules(req: VerkaufsCheckRequest) -> list[tuple[str, re.Pattern, bool]]:
    """(menschlicher Name, Regex, ist_belegt) — trifft die Regex zu und ist NICHT
    belegt, wird der betroffene Satz/Aufzählungspunkt entfernt."""
    scheck_ok = getattr(req, "scheckheftgepflegt", None) is True
    erste_hand_ok = getattr(req, "vorbesitzer", None) == 1
    tuev_ok = bool(getattr(req, "tuev_bis", None))
    nachweise = wartung_status(req)["nachweise"]
    return [
        # Aussagenbezogen (VK-04): `_unfall_satz_belegt` prüft JEDEN Satz zum Unfallstatus einzeln.
        ("Unfallfreiheit",
         re.compile(_UNFALL_THEMA.pattern + "|" + _UNFALL_ANSPRUCH.pattern + "|" + _UNFALL_NEGATIV.pattern, re.IGNORECASE),
         lambda satz: _unfall_satz_belegt(satz, req)),
        ("Scheckheft",
         re.compile(r"scheckheft|lückenlos\w*\s+(?:wartung|service|historie)|serviceheft\s+gepflegt|voll\w*\s+scheckheft", re.IGNORECASE),
         scheck_ok),
        # "vollständige Wartungs-/Servicehistorie": nur belegt, wenn der Verkäufer vollständige Nachweise ANGIBT
        # (das ist seine Angabe, keine Dokumentprüfung) oder ein Scheckheft angibt. Teilweise/keine/unbekannt: gesperrt.
        ("Vollständige Wartungshistorie",
         re.compile(r"vollständige?[nrs]?\s+(?:wartungs|service)\w*", re.IGNORECASE),
         lambda satz: (nachweise == NACHWEIS_VOLLSTAENDIG or scheck_ok
                       or (nachweise in (NACHWEIS_TEILWEISE, NACHWEIS_KEINE, NACHWEIS_UNBEKANNT)
                           and bool(re.search(r"(?:nicht|kein\w*|ohne)\s+(?:\w+\s+)?vollständig", satz, re.IGNORECASE))))),
        ("Erste Hand / Vorbesitzer",
         re.compile(r"erste\s*hand|1\.?\s*hand|erstbesitz|aus\s+erster\s+hand", re.IGNORECASE),
         erste_hand_ok),
        ("TÜV/HU",
         re.compile(r"\b(t[üu]v|hu|hauptuntersuchung)\b", re.IGNORECASE),
         tuev_ok),
        ("Garantie",
         re.compile(r"\bgarantie\b|\bgewährleistung\b", re.IGNORECASE),
         False),   # ENFAL hat keine Garantie-Daten -> nie behaupten
    ]


def _satz_ok(satz: str, rules, ausst_backed: set[str], entfernt: list[str]) -> bool:
    """True, wenn der Satz/Punkt behalten werden darf. Prüft belegbare Positiv-
    Behauptungen und erfundene Ausstattung."""
    for name, rx, belegt in rules:
        if rx.search(satz) and not _ist_belegt(belegt, satz):
            entfernt.append(f"{name}: „{satz.strip()}“")
            return False
    # Erfundene (nicht angegebene) wertige Ausstattung.
    snorm = _norm(satz)
    for key, label in _AUSSTATTUNG_WERTVOLL.items():
        if key not in ausst_backed and _keyword_in(snorm, key):
            entfernt.append(f"Nicht angegebene Ausstattung ({label}): „{satz.strip()}“")
            return False
    return True


def _scrub_text(text: str, req: VerkaufsCheckRequest, entfernt: list[str]) -> str:
    """Zeilen-/satzweiser Scrub: entfernt Sätze/Aufzählungspunkte mit unbelegten
    Positiv-Behauptungen; erhält die Markdown-Struktur (Überschriften/Listen)."""
    text = _neutralize_tuev_qualifier(text)
    rules = _claim_rules(req)
    ausst_backed = _ausstattung_backed(req)
    out_lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        # Überschriften/leere Zeilen unangetastet lassen (enthalten keine Claims).
        if not stripped or stripped.startswith("#"):
            out_lines.append(line)
            continue
        # Aufzählungspunkt als Ganzes behandeln.
        if re.match(r"^\s*([-*•]|\d+\.)\s+", line):
            inhalt = re.sub(r"^\s*([-*•]|\d+\.)\s+", "", line)
            if _satz_ok(inhalt, rules, ausst_backed, entfernt):
                out_lines.append(line)
            continue
        # Prosa: in Sätze zerlegen und einzeln prüfen.
        saetze = re.split(r"(?<=[.!?])\s+", line)
        behalten = [s for s in saetze if _satz_ok(s, rules, ausst_backed, entfernt)]
        if behalten:
            out_lines.append(" ".join(behalten))
    # Mehrfache Leerzeilen zusammenfassen.
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)).strip()


def _scrub_titel(titel: str, req: VerkaufsCheckRequest, entfernt: list[str]) -> str:
    """Titel-Segmente (durch | getrennt) prüfen: unbelegte Ausstattung/Scheckheft/
    Unfallfrei-Segmente entfernen. Spam/übermäßige Ausrufezeichen bereinigen."""
    titel = _neutralize_tuev_qualifier(titel)
    titel = re.sub(r"!{2,}", "", titel)                       # "TOP!!!" -> "TOP"
    rules = _claim_rules(req)
    ausst_backed = _ausstattung_backed(req)
    segmente = [s.strip() for s in re.split(r"[|]", titel) if s.strip()]
    behalten: list[str] = []
    for seg in segmente:
        snorm = _norm(seg)
        drop = False
        for name, rx, belegt in rules:
            if rx.search(seg) and not _ist_belegt(belegt, seg):
                entfernt.append(f"Titel, {name}: „{seg}“")
                drop = True
                break
        if not drop:
            for key, label in _AUSSTATTUNG_WERTVOLL.items():
                if key not in ausst_backed and _keyword_in(snorm, key):
                    entfernt.append(f"Titel, nicht angegebene Ausstattung ({label}): „{seg}“")
                    drop = True
                    break
        if not drop:
            behalten.append(seg)
    return " | ".join(behalten)


def _maengel_ehrlich(beschreibung: str, req: VerkaufsCheckRequest) -> str:
    """Stellt sicher, dass angegebene bekannte Mängel im Text ehrlich erscheinen —
    sie dürfen nie verschwiegen (weggescrubbt) werden."""
    maengel = getattr(req, "maengel", None) or []
    if not maengel:
        return beschreibung
    if "bekannte mängel" in beschreibung.lower() or "bekannte maengel" in beschreibung.lower():
        return beschreibung
    block = "\n\n**Bekannte Mängel:**\n" + "\n".join(f"- {m}" for m in maengel)
    return beschreibung.rstrip() + block


def pruefe_fakten(titel: str, beschreibung: str, req: VerkaufsCheckRequest) -> tuple[str, str, list[str]]:
    """Fakten-Schutz: gibt (titel, beschreibung, entfernte_behauptungen) zurück.

    Entfernt unbelegte Positiv-Behauptungen und erfundene Ausstattung; garantiert,
    dass angegebene Mängel ehrlich enthalten bleiben. ENFAL fügt NIE eine positive
    Aussage hinzu, die der Nutzer nicht angegeben hat.
    """
    entfernt: list[str] = []
    titel_clean = _scrub_titel(titel or "", req, entfernt)
    beschr_clean = _scrub_text(beschreibung or "", req, entfernt)
    beschr_clean = _maengel_ehrlich(beschr_clean, req)
    # VerkaufsCheck RC1: Verstärkungen belegter Angaben abschwächen ("lückenloses
    # Scheckheft" -> "Scheckheft"). Der Scrub oben entfernt nur UNBELEGTE Aussagen;
    # eine belegte Angabe darf aber ebenfalls nicht stärker klingen als sie ist.
    titel_clean, t_schwach = entschaerfe_verstaerkungen(titel_clean, stimme="inserat")
    beschr_clean, b_schwach = entschaerfe_verstaerkungen(beschr_clean, stimme="inserat")
    entfernt += [f"Abgeschwächt: „{s}“" for s in t_schwach + b_schwach]
    # Fällt der Titel komplett weg, deterministischen Vorschlag verwenden.
    if not titel_clean.strip():
        titel_clean = baue_titel_vorschlag(req) or " ".join(
            filter(None, [getattr(req, "marke", None), getattr(req, "modell", None)])
        ).strip() or "Fahrzeug"
    return titel_clean.strip(), beschr_clean.strip(), entfernt


# ══ LLM-Textersteller (on-demand) ════════════════════════════════════════════

_OPT_SYSTEM = """\
Du bist ein erfahrener, seriöser KFZ-Verkaufstexter. Du formulierst aus GEPRÜFTEN
Fakten ein professionelles Gebrauchtwagen-Inserat (Titel + Beschreibung).

Du bekommst ausschließlich FAKTEN, die der Verkäufer angegeben hat. Deine EINZIGE
Aufgabe ist gutes Formulieren — NICHT Fakten hinzufügen.

STRIKT VERBOTEN (das erfindet niemals etwas):
- KEINE Ausstattung erfinden, die nicht in den Fakten steht.
- KEINE Unfallfreiheit, KEIN Scheckheft, KEIN „TÜV neu“, KEINE Vorbesitzer-Zahl,
  KEINE Garantie behaupten, wenn es nicht ausdrücklich in den Fakten steht.
- Zustand NICHT beschönigen, Mängel NICHT verschweigen.
- Als unbekannt oder nur teilweise vorhanden angegebene Punkte (z. B. Unfallstatus, Wartungsnachweise) genau so
  benennen: nicht verschweigen, nicht als sicher oder vollständig darstellen, nichts dazuerfinden.
- KEINE bereitliegenden Unterlagen oder Fotos behaupten, die nicht in den Fakten stehen.
- KEINE technischen Daten (Leistung, Kraftstoff, Getriebe) erfinden oder ändern.
- KEIN Händler-Slang, KEINE Superlative-Spam („TOP!!!“, „BESTPREIS“), keine
  Ausrufezeichen-Ketten.

AUSGABE: ausschließlich gültiges JSON:
{
  "titel": "<kompakter, sachlicher Titel, Merkmale mit | getrennt, keine Superlative>",
  "beschreibung": "<Markdown, Zeilenumbrüche als \\n>"
}

Aufbau der Beschreibung (nur mit vorhandenen Fakten füllen, leere Punkte weglassen):
1. Kurze, sachliche Einleitung (1–2 Sätze, keine Wertversprechen)
2. **Fahrzeugdaten** (Baujahr, Kilometerstand, Kraftstoff, Getriebe, Leistung, Farbe)
3. **Ausstattung** (nur die angegebene, als Liste)
4. **Zustand & Wartung** (nur angegebene Fakten: Zustand, Scheckheft, Wartungsnachweise, letzter Service, Unfallstatus,
   Vorschäden, TÜV, Vorbesitzer)
5. **Bekannte Mängel** (falls angegeben — ehrlich benennen)
6. Sachlicher Abschluss (z. B. Einladung zur Besichtigung/Probefahrt) — ohne Kontaktdaten zu erfinden

Schreibe auf Deutsch, professionell und vertrauenswürdig.
[[STILREGEL]]\
"""


def _fakten_block(req: VerkaufsCheckRequest) -> str:
    """Kompakter FAKTEN-Block für den LLM — NUR gesetzte Felder."""
    lines = ["ANGEGEBENE FAKTEN (nur diese verwenden):"]
    def add(label, val):
        if val:
            lines.append(f"- {label}: {val}")
    add("Marke", getattr(req, "marke", None))
    add("Modell", getattr(req, "modell", None))
    add("Baujahr", getattr(req, "baujahr", None))
    km = getattr(req, "kilometerstand", None)
    add("Kilometerstand", f"{km:,} km".replace(",", ".") if km else None)
    add("Motor/Leistung", getattr(req, "motor", None))
    add("Kraftstoff", getattr(req, "kraftstoff", None))
    add("Getriebe", getattr(req, "getriebe", None))
    add("Farbe", getattr(req, "farbe", None))
    if getattr(req, "ausstattung", None):
        add("Ausstattung", ", ".join(req.ausstattung))
    add("Zustand/Beschreibung", _hat_text(req) or None)
    uf, uf_quelle = unfall_status(req)
    if uf == UNFALL_JA:
        add("Unfallstatus", "unfallfrei (laut Verkäufer)")
    elif uf == UNFALL_NEIN:
        add("Unfallstatus", "Unfallschaden vorhanden")
    elif uf == UNFALL_UNBEKANNT:
        add("Unfallstatus", "dem Verkäufer unbekannt, keine sichere Angabe möglich (so benennen, nicht verschweigen, "
                            "nicht als unfallfrei darstellen)")
    vb = getattr(req, "vorbesitzer", None)
    if vb is not None:
        add("Vorbesitzer", vb)
    if getattr(req, "scheckheftgepflegt", None) is True:
        add("Wartung", "scheckheftgepflegt (laut Verkäufer)")
    w = wartung_status(req)
    nachweis_text = {NACHWEIS_VOLLSTAENDIG: "laut Verkäufer vollständig vorhanden",
                     NACHWEIS_TEILWEISE: "nur teilweise vorhanden (nicht als vollständige Historie darstellen)",
                     NACHWEIS_KEINE: "keine vorhanden",
                     NACHWEIS_UNBEKANNT: "dem Verkäufer unbekannt"}.get(w["nachweise"])
    add("Wartungsnachweise", nachweis_text)
    service = ", ".join(filter(None, [getattr(req, "letzter_service_datum", None),
                                      f"{req.letzter_service_km:,} km".replace(",", ".")
                                      if getattr(req, "letzter_service_km", None) else None]))
    add("Letzter Service (Angabe des Verkäufers, kein Beleg)", service or None)
    add("TÜV/HU bis", getattr(req, "tuev_bis", None))
    vorschaeden = (getattr(req, "vorschaeden", None) or "").strip()
    add("Vorschäden/Nachlackierungen", vorschaeden or None)
    tuning = (getattr(req, "tuning", None) or "").strip()
    if tuning and not re.match(r"^\s*(?:keine?|nein|nicht\s+vorhanden)\b", tuning, re.IGNORECASE):
        add("Tuning/Umbauten", tuning)
    maengel_alle: list[str] = []
    for m in [*(getattr(req, "maengel", None) or []), *(getattr(req, "technische_maengel", None) or []),
              *(getattr(req, "optische_maengel", None) or [])]:
        if m and m.strip() and m.strip() not in maengel_alle:
            maengel_alle.append(m.strip())
    if maengel_alle:
        add("Bekannte Mängel (ehrlich benennen)", ", ".join(maengel_alle))
    if getattr(req, "preis_vorstellung", None):
        add("Preisvorstellung", f"{req.preis_vorstellung:,} €".replace(",", "."))
    return "\n".join(lines)


async def run_inserat_optimierung(req: VerkaufsCheckRequest) -> InseratOptimierung:
    """Erzeugt on-demand eine optimierte Inseratsversion (Titel + Beschreibung).

    LLM = reiner Textersteller. Danach IMMER `pruefe_fakten`. Nutzt call_gemini_json
    (bestehende MAX_TOKENS-/JSON-Härtung, thinking_budget=0, Retry-dann-Fehler).
    """
    result = await call_gemini_json(_OPT_SYSTEM, _fakten_block(req))
    titel = (result.get("titel") or "").strip()
    beschreibung = (result.get("beschreibung") or "").strip()
    # bericht ist der Fallback-Key von _notfall_extraktion, falls das Modell kein
    # sauberes {titel,beschreibung}-JSON lieferte.
    if not beschreibung and result.get("bericht"):
        beschreibung = str(result["bericht"]).strip()

    titel, beschreibung, entfernt = pruefe_fakten(titel, beschreibung, req)
    return InseratOptimierung(
        titel=titel,
        beschreibung=beschreibung,
        generiert_am=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        entfernte_behauptungen=entfernt,
    )

# Gemeinsame Schreibstil-Regel (app/schreibstil.py) einsetzen.
# .replace statt f-String: die Prompts enthalten JSON-Klammern.
_OPT_SYSTEM = _OPT_SYSTEM.replace("[[STILREGEL]]", STILREGEL_GEDANKENSTRICHE)
