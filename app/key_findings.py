from __future__ import annotations

"""
Phase 2 — Key Findings ("Das solltest du wissen").

Verdichtet die BEREITS in Phase 1 deterministisch abgeleiteten Daten (Marktanalyse,
Rückruf-Applicability, Schwachstellen/Motorprobleme, Inserat-Daten) zu maximal 3–5
sofort verständlichen Kern-Erkenntnissen. Grundsätze:

- KEIN LLM, KEINE neue Wahrheit, KEINE Fake-Statistik. Nur Regeln über vorhandene Felder.
- `evidence_ids` referenzieren ausschließlich EXISTIERENDE Insight-IDs.
- KEINE Angstmache: vier Stufen (kritisch/warnung/chance/info); ein geringer Mangel
  wird nie "kritisch".
- Prozentwerte bewusst gerundet ("ca. 11,4 %"), keine Scheinpräzision.
- Preisabweichung nur bei belastbarer Marktanalyse (echter Median vorhanden).
- Erfindet nichts, wenn nichts Auffälliges vorliegt (lieber wenige/keine Findings).
"""

import logging
import re
from datetime import date

from app.getriebe import (
    anzeige as getriebe_anzeige, aus_db as getriebe_aus_db,
    aus_text as getriebe_aus_text, normalisiere as getriebe_normalisiere,
)
from app.models import Insight, KeyFinding, PriceAssessment
from app.servicehistorie import (
    NICHT_VORHANDEN as SH_NICHT_VORHANDEN, TEILWEISE as SH_TEILWEISE,
    UMFANG_UNKLAR as SH_UMFANG_UNKLAR, VOLLSTAENDIG_ANGEGEBEN as SH_VOLLSTAENDIG,
    satz as servicehistorie_satz, status as servicehistorie_status,
)
from app.preisurteil import bewerte_preis

log = logging.getLogger(__name__)

STUFE_KRITISCH = "kritisch"
STUFE_WARNUNG = "warnung"
STUFE_CHANCE = "chance"
STUFE_INFO = "info"

MAX_FINDINGS = 5

# ── Priorisierung (höher = wichtiger). Bewusst in Bänder gruppiert, damit die vom
# Produkt vorgegebene Reihenfolge deterministisch eingehalten wird. ──────────────
# Kauf: 1) Widerspruch  2) Preisabweichung (auch "ungewöhnlich günstig")  3) Rückruf
#       4) Schwachstelle  5) Vorteil
# HINWEIS: Ein niedriger Preis ALLEIN ist KEIN Betrugssignal (kein "kritisch",
# kein Wort "Betrug"). Ein echter Betrugsverdacht bräuchte MEHRERE unabhängige
# Warnzeichen — das bauen wir hier bewusst NICHT.
_P_IDENTITAET    = 990   # unsichere Fahrzeugzuordnung schlaegt alles andere
_P_WIDERSPRUCH   = 950
_P_PREIS_UNGEWOEHNLICH = 850   # ungewöhnlich günstig -> starke, aber neutrale Warnung
_P_PREIS_UEBER   = 830   # zu teuer = Risiko -> etwas höher als "günstig"
_P_PREIS_UNTER   = 810
_P_RUECKRUF      = 700
_P_MOTORPROBLEM  = 640
_P_SCHWACH_HOCH  = 620
_P_RUECKRUF_UNKLAR = 360
_P_VORTEIL_PREIS = 500   # (nur falls kein eigenes Preis-Finding entstand)
_P_MOTOR_OK      = 400
# Servicehistorie-Pruefpunkt: ueber dem allgemeinen Vorteil (er ist konkret
# handlungsrelevant), aber klar unter Rueckruf und Schwachstelle — eine
# unbelegte Wartungshistorie ist eine Unsicherheit, kein Befund am Fahrzeug.
_P_SERVICEHISTORIE = 420
_P_VORTEIL       = 380
_P_PREIS_INFO    = 250

# Verkauf: 1) Preisposition 2) fehlende Angaben 3) Verkaufsargumente 4) Datenqualität
_P_V_PREIS_UEBER   = 830
_P_V_PREIS_POS     = 790
_P_V_ANGABEN       = 700
_P_V_PREIS_INFO    = 640
_P_V_AUSSTATTUNG   = 600
_P_V_DATENQUALI    = 500


# ── kleine Helfer ────────────────────────────────────────────────────────────

def _eur(n) -> str:
    try:
        return f"{round(n):,} €".replace(",", ".")
    except (TypeError, ValueError):
        return "—"


def _pct(p) -> str:
    """'ca. 11,4 %' — gerundet, deutsche Dezimalschreibweise, ohne Vorzeichen."""
    zahl = f"{abs(p):.1f}".replace(".", ",")   # nur die Dezimalstelle, nicht 'ca.'
    return f"ca. {zahl} %"


def _marktvergleich_insight(insights: list[Insight]) -> Insight | None:
    for i in insights:
        if i.kategorie == "marktvergleich":
            return i
    return None


def _pa_from_insights(insights: list[Insight], check_typ: str) -> PriceAssessment | None:
    """Fallback: kanonisches Preisurteil aus der Marktanalyse des Marktvergleich-
    Insights ableiten, falls der Aufrufer keins übergibt (Rückwärtskompatibilität)."""
    mv = _marktvergleich_insight(insights)
    ma = mv.marktanalyse if mv else None
    if not ma:
        return None
    return bewerte_preis(ma, ma.angebot_eur, check_typ=check_typ)


def _preis_finding_kauf(pa: PriceAssessment, ev_id: str) -> KeyFinding:
    """EIN Preis-Finding, abgeleitet aus dem KANONISCHEN Preisurteil (§6) — nie eine
    eigene, abweichende Bewertung derselben Zahlen.

    §Phase 12 (Reliability-Sprint 4): KEIN angehängter "Median vergleichbarer
    Fahrzeuge: X €"-Satz mehr — dieselbe Zahl steht bereits IMMER sichtbar oben in
    den Markt-Kennzahlen (MarketMetrics, ResultSummary.tsx) UND im ausklappbaren
    "Warum diese Preisbewertung?"-Beleg. `pa.begruendung` bleibt als kurze,
    NICHT-redundante Einordnung; Details/Zahlen -> "siehe oben" statt Wiederholung.
    """
    diff = abs(pa.difference_eur or 0)
    pct = pa.difference_percent or 0.0
    ev = [ev_id]
    v = pa.verdict
    if v == "deutlich_unter":
        return KeyFinding(id="", kategorie="preis", stufe=STUFE_WARNUNG, icon="💰",
            titel="Ungewöhnlich günstiger Preis",
            beschreibung=f"{pa.begruendung} Preis, Fahrzeughistorie und Verkäuferangaben "
                         f"besonders sorgfältig prüfen.",
            wert=f"↓ {_eur(diff)} · {_pct(pct)}", aktion=pa.recommendation,
            evidence_ids=ev, prioritaet=_P_PREIS_UNGEWOEHNLICH)
    if v == "unter":
        return KeyFinding(id="", kategorie="preis", stufe=STUFE_CHANCE, icon="💰",
            titel="Unter Marktpreis",
            beschreibung=pa.begruendung,
            wert=f"↓ {_eur(diff)} · {_pct(pct)}", evidence_ids=ev, prioritaet=_P_PREIS_UNTER)
    if v == "marktgerecht":
        return KeyFinding(id="", kategorie="preis", stufe=STUFE_INFO, icon="💰",
            titel="Preis marktgerecht",
            beschreibung=pa.begruendung,
            evidence_ids=ev, prioritaet=_P_PREIS_INFO)
    if v == "oberes_segment":
        return KeyFinding(id="", kategorie="preis", stufe=STUFE_WARNUNG, icon="💸",
            titel="Oberes Marktsegment",
            beschreibung=pa.begruendung,
            wert=f"↑ {_eur(diff)} · {_pct(pct)}", aktion=pa.recommendation,
            evidence_ids=ev, prioritaet=_P_PREIS_UEBER)
    if v == "ueber":
        return KeyFinding(id="", kategorie="preis", stufe=STUFE_WARNUNG, icon="💸",
            titel="Über Marktpreis",
            beschreibung=pa.begruendung,
            wert=f"↑ {_eur(diff)} · {_pct(pct)}", aktion=pa.recommendation,
            evidence_ids=ev, prioritaet=_P_PREIS_UEBER)
    # deutlich_ueber
    return KeyFinding(id="", kategorie="preis", stufe=STUFE_WARNUNG, icon="💸",
        titel="Deutlich über Marktpreis",
        beschreibung=pa.begruendung,
        wert=f"↑ {_eur(diff)} · {_pct(pct)}", aktion=pa.recommendation,
        evidence_ids=ev, prioritaet=_P_PREIS_UEBER + 10)


def _preis_finding_verkauf(pa: PriceAssessment, ev_id: str) -> KeyFinding:
    """Marktpositions-Finding (Verkauf) aus dem KANONISCHEN Preisurteil (§6)."""
    diff = abs(pa.difference_eur or 0)
    median = pa.median_eur
    pct = pa.difference_percent or 0.0
    ev = [ev_id]
    if pa.verdict in ("oberes_segment", "ueber", "deutlich_ueber"):
        return KeyFinding(id="", kategorie="marktposition", stufe=STUFE_WARNUNG, icon="📊",
            titel="Zielpreis über Marktniveau",
            beschreibung=f"Mit {_eur(pa.median_eur + (pa.difference_eur or 0))} liegst du {_eur(diff)} "
                         f"bzw. {_pct(pct)} über dem Median vergleichbarer Fahrzeuge ({_eur(median)}). "
                         f"{pa.begruendung}",
            wert=f"↑ {_pct(pct)} über Median", aktion=pa.recommendation,
            evidence_ids=ev, prioritaet=_P_V_PREIS_UEBER)
    if pa.verdict in ("unter", "deutlich_unter"):
        return KeyFinding(id="", kategorie="marktposition", stufe=STUFE_CHANCE, icon="📊",
            titel="Zielpreis unter Marktniveau: Spielraum nach oben",
            beschreibung=f"Du liegst {_eur(diff)} bzw. {_pct(pct)} unter dem Median "
                         f"({_eur(median)}). Ein höherer Startpreis ist realistisch.",
            wert=f"↓ {_pct(pct)} unter Median", evidence_ids=ev, prioritaet=_P_V_PREIS_POS)
    return KeyFinding(id="", kategorie="marktposition", stufe=STUFE_INFO, icon="📊",
        titel="Zielpreis nah am Marktmedian",
        beschreibung=f"Dein Zielpreis liegt sehr nah am Median vergleichbarer Fahrzeuge "
                     f"({_eur(median)}, {_pct(pct)} Abweichung): realistisch angesetzt.",
        evidence_ids=ev, prioritaet=_P_V_PREIS_INFO)


_PS_RE = re.compile(r"(\d{2,3})\s*ps\b", re.IGNORECASE)
_KRAFTSTOFF_HINTS = [
    ("diesel", ("diesel", "tdi", "cdi", "hdi", "dci", "bluetec")),
    ("hybrid", ("plug-in", "plugin", "phev")),
    ("elektro", ("elektro", "electric", " ev", "bev")),
    ("benzin", ("benzin", "tsi", "tfsi", "petrol", "otto")),
]


def _kraftstoff_norm(text: str | None) -> str | None:
    t = (text or "").lower()
    if not t:
        return None
    for norm, keys in _KRAFTSTOFF_HINTS:
        if any(k in t for k in keys):
            return norm
    return None


def _ps_aus_text(*teile: str | None) -> int | None:
    for t in teile:
        m = _PS_RE.search(t or "")
        if m:
            val = int(m.group(1))
            if 30 <= val <= 999:
                return val
    return None


def _mangel_kurz(insight) -> str:
    """Kurzer Rückruftitel — bevorzugt das strukturierte Feld `kurztitel`.

    KaufCheck RC1: vorher wurde der Insight-Titel am ersten ":" zerlegt und nach
    60 Zeichen mit "…" abgeschnitten ("Aufgrund fehlerhafter Auslegung kann es bei
    hohen Belastunge…"). Akzeptiert aus Kompatibilität auch einen Titel-String.
    """
    kurz = getattr(insight, "kurztitel", None)
    if kurz:
        return kurz
    titel = insight if isinstance(insight, str) else (getattr(insight, "titel", "") or "")
    teil = titel.split(":", 1)[-1].strip()
    return (teil[:60].rstrip() + "…") if len(teil) > 61 else teil


def _finalisiere(findings: list[KeyFinding]) -> list[KeyFinding]:
    """Stabil nach Priorität sortieren, auf MAX_FINDINGS kürzen, IDs vergeben."""
    findings.sort(key=lambda f: f.prioritaet, reverse=True)
    findings = findings[:MAX_FINDINGS]
    for n, f in enumerate(findings, 1):
        f.id = f"finding-{n}"
    return findings


# ── gemeinsame Regel-Bausteine ───────────────────────────────────────────────

def _rueckruf_findings(insights: list[Insight]) -> list[KeyFinding]:
    """Reliability-Sprint 3 (§27/§28): applicability-Werte umbenannt. "relevant"
    (confirmed_by_vin/variant_match/series_only) heißt jetzt bewusst NICHT mehr
    "relevant" im Titel — ohne VIN-Prüfung ist keine dieser Stufen sicher "relevant"
    im Sinne von gesichert betroffen; der Titel bleibt neutral ("zu prüfen")."""
    rueckrufe = [i for i in insights if i.kategorie == "rueckruf"]
    zu_pruefen = [i for i in rueckrufe
                 if i.applicability in ("confirmed_by_vin", "variant_match", "series_only")]
    unklar = [i for i in rueckrufe if i.applicability == "unclear"]
    out: list[KeyFinding] = []

    if zu_pruefen:
        titel_liste = "; ".join(_mangel_kurz(i) for i in zu_pruefen[:3])
        n = len(zu_pruefen)
        out.append(KeyFinding(
            id="", kategorie="rueckruf", stufe=STUFE_WARNUNG, icon="⚠️",
            titel=f"{n} Rückruf{'e' if n != 1 else ''} zu prüfen",
            beschreibung=titel_liste,
            aktion="Vor Kauf per FIN prüfen, ob die Rückrufaktion durchgeführt wurde.",
            evidence_ids=[i.id for i in zu_pruefen],
            prioritaet=_P_RUECKRUF,
        ))
    if unklar:
        # NIE als sicher betroffen darstellen (applicability=unclear).
        n = len(unklar)
        out.append(KeyFinding(
            id="", kategorie="rueckruf", stufe=STUFE_INFO, icon="🛈",
            titel=f"{n} Rückruf{'e' if n > 1 else ''} mit unklarer Betroffenheit",
            beschreibung="Für die Baureihe hinterlegt, betrifft aber bestimmte Varianten. "
                         + "; ".join(_mangel_kurz(i) for i in unklar[:3]),
            aktion="Ob dein Fahrzeug betroffen ist, anhand der FIN beim Hersteller/KBA prüfen.",
            evidence_ids=[i.id for i in unklar],
            prioritaet=_P_RUECKRUF_UNKLAR,
        ))
    return out


# ══ KAUFCHECK ════════════════════════════════════════════════════════════════

def _identitaets_finding(fehlende_angabe: str | None) -> KeyFinding:
    """Unsichere Fahrzeugzuordnung sichtbar machen (Identity-Trust-Gate).

    Nennt bewusst KEINE vermutete Baureihe: Die Zuordnung war ja gerade nicht
    belastbar — eine konkrete Nennung ("vermutlich BMW X7") wäre exakt der Fehler,
    den das Gate verhindern soll. Stattdessen steht dort, welche Angabe die
    Erkennung eindeutig machen würde.
    """
    fehlt = fehlende_angabe or "die genaue Modell- und Generationsbezeichnung"
    return KeyFinding(
        id="", kategorie="identitaet", stufe=STUFE_WARNUNG, icon="❓",
        titel="Baureihe nicht sicher erkannt",
        beschreibung="Die Angaben lassen sich keiner Baureihe eindeutig zuordnen. "
                     "Es werden deshalb keine fahrzeugspezifischen Schwachstellen, "
                     "Motorprobleme oder Rückrufe ausgegeben: die allgemeinen "
                     "Prüflisten gelten unverändert.",
        aktion=f"Für eine gezielte Analyse bitte {fehlt} nachtragen.",
        prioritaet=_P_IDENTITAET)


def build_key_findings_kauf(req, baureihe: dict | None, motor_match: dict | None,
                            insights: list[Insight],
                            price_assessment: PriceAssessment | None = None,
                            identitaet: dict | None = None) -> list[KeyFinding]:
    """`identitaet` (optional, Identity-Trust-Gate): Info-dict aus
    `car_lookup.find_baureihe_mit_vertrauen`. Ist die Zuordnung nicht belastbar,
    entsteht ein erklärendes Finding statt einer stillen Leerausgabe. Der Parameter
    ist additiv — ohne ihn verhält sich die Funktion exakt wie bisher."""
    findings: list[KeyFinding] = []

    if identitaet is not None and not identitaet.get("belastbar", True):
        findings.append(_identitaets_finding(identitaet.get("fehlende_angabe")))

    # ── A) Preis-Finding aus dem KANONISCHEN Preisurteil (§6) — genau EINE Bewertung ──
    mv = _marktvergleich_insight(insights)
    pa = price_assessment or _pa_from_insights(insights, "kauf")
    preis_finding_erzeugt = False
    if pa and pa.verdict != "unbekannt" and pa.median_eur and pa.difference_eur is not None and mv:
        findings.append(_preis_finding_kauf(pa, mv.id))
        preis_finding_erzeugt = True

    # ── D) Inserat-Widersprüche (rein deterministisch, keine Evidence) ──────────
    findings += _widerspruch_findings(req, baureihe, motor_match)

    # ── D2) Servicehistorie als Prüfpunkt (Angabe des Inserats, kein Befund) ────
    findings += _servicehistorie_finding(req)

    # ── B) Relevante Rückrufe ───────────────────────────────────────────────────
    findings += _rueckruf_findings(insights)

    # ── C) Motorproblem / hohe Schwachstelle / "keine schweren Motorprobleme" ───
    motorprobleme = [i for i in insights if i.kategorie == "motorproblem"]
    schwach_hoch = [i for i in insights if i.kategorie == "schwachstelle"
                    and (i.schweregrad or "").lower() in ("hoch", "kritisch", "sehr hoch")]
    if motorprobleme:
        m = motorprobleme[0]
        weitere = f" (+{len(motorprobleme) - 1} weitere)" if len(motorprobleme) > 1 else ""
        findings.append(KeyFinding(
            id="", kategorie="motorproblem", stufe=STUFE_WARNUNG, icon="🔧",
            titel="Bekanntes Motorproblem",
            beschreibung=(m.titel + weitere + ". " + (m.einfluss or "")).strip(),
            aktion="Bauteil bei der Werkstattprüfung gezielt kontrollieren lassen.",
            evidence_ids=[i.id for i in motorprobleme], prioritaet=_P_MOTORPROBLEM))
    if schwach_hoch:
        from app.evidence import titel_bauteil
        namen = ", ".join(titel_bauteil(i.titel) for i in schwach_hoch[:3])
        n = len(schwach_hoch)
        findings.append(KeyFinding(
            id="", kategorie="schwachstelle", stufe=STUFE_WARNUNG, icon="⚙️",
            titel=f"{n} bekannte Schwachstelle{'n' if n > 1 else ''} (hoher Schweregrad)",
            beschreibung=namen,
            aktion="Bei der Besichtigung gezielt prüfen.",
            evidence_ids=[i.id for i in schwach_hoch], prioritaet=_P_SCHWACH_HOCH))
    elif motor_match and not motorprobleme and motor_match.get("kritische_wartung"):
        # NUR wenn die Motorvariante eindeutig erkannt wurde UND für sie tatsächlich
        # motor-spezifische Daten geprüft vorliegen (hier: kritische_wartung als
        # Beleg editorieller Abdeckung) UND keine schwere Motor-Schwachstelle
        # existiert. "Keine Daten" darf NIE zu "unauffällig"/"keine Probleme" werden.
        # Bewusst zurückhaltend formuliert: Abwesenheit von Daten ≠ Fehlerfreiheit.
        findings.append(KeyFinding(
            id="", kategorie="vorteil", stufe=STUFE_CHANCE, icon="✅",
            titel="Keine schweren bekannten Motorprobleme gefunden",
            beschreibung=f"In der vorhandenen ENFAL-Datenbasis sind für die erkannte "
                         f"Motorvariante ({motor_match.get('bezeichnung', 'Motor')}) keine schweren "
                         f"Probleme hinterlegt. Das bedeutet nicht, dass keine Defekte auftreten können.",
            prioritaet=_P_MOTOR_OK))

    # ── E) Ungewöhnlich positive Punkte (deterministisch belegbar) ──────────────
    findings += _positive_findings_kauf(req, preis_finding_erzeugt)

    return _finalisiere(findings)


def _strukturiert_vs_inserat(req) -> list[KeyFinding]:
    """Strukturierte Auswahl gegen den Inseratstext.

    Die bisherigen Widerspruchsprüfungen verglichen die Eingabe immer gegen die
    DATENBANK. Der häufigere Fall ist ein Widerspruch INNERHALB der Eingabe:
    im Auswahlfeld steht "Benzin", im eingefügten Inseratstext steht "Diesel";
    im Auswahlfeld steht "Automatik", im Text steht "6-Gang Handschaltung".

    ENFAL löst das NICHT still auf. Die strukturierte Angabe bleibt die harte
    Eingabe (sie hat der Nutzer bewusst gesetzt), aber der Widerspruch wird
    sichtbar gemeldet und muss geklärt werden. Genau deshalb steht hier keine
    Korrektur, sondern ein Finding.
    """
    out: list[KeyFinding] = []
    text = " ".join(filter(None, [getattr(req, "beschreibung", None),
                                  getattr(req, "freitext", None)]))
    if not text.strip():
        return out

    feld_kraft = _kraftstoff_norm(getattr(req, "kraftstoff", None))
    text_kraft = _kraftstoff_norm(text)
    if feld_kraft and text_kraft and feld_kraft != text_kraft:
        out.append(KeyFinding(
            id="", kategorie="widerspruch", stufe=STUFE_WARNUNG, icon="❗",
            titel="Kraftstoff-Angabe widerspricht dem Inseratstext",
            beschreibung=f"Im Formular ist {feld_kraft.capitalize()} ausgewählt, der "
                         f"Inseratstext deutet auf {text_kraft.capitalize()} hin. Die "
                         f"Auswertung folgt der Auswahl im Formular.",
            wert=f"Auswahl: {feld_kraft.capitalize()} · Inseratstext: {text_kraft.capitalize()}",
            aktion="Kraftstoff im Inserat nachlesen und die Auswahl gegebenenfalls korrigieren.",
            prioritaet=_P_WIDERSPRUCH + 10))

    feld_getriebe = getriebe_normalisiere(getattr(req, "getriebe", None))
    text_getriebe = getriebe_aus_text(text)
    if feld_getriebe and text_getriebe and feld_getriebe != text_getriebe:
        out.append(KeyFinding(
            id="", kategorie="widerspruch", stufe=STUFE_WARNUNG, icon="❗",
            titel="Getriebe-Angabe widerspricht dem Inseratstext",
            beschreibung=f"Im Formular ist {getriebe_anzeige(feld_getriebe)} ausgewählt, der "
                         f"Inseratstext nennt {getriebe_anzeige(text_getriebe)}. Die "
                         f"Prüfhinweise zur Probefahrt folgen der Auswahl im Formular.",
            wert=f"Auswahl: {getriebe_anzeige(feld_getriebe)} · "
                 f"Inseratstext: {getriebe_anzeige(text_getriebe)}",
            aktion="Getriebeart im Inserat nachlesen und vor der Probefahrt klären.",
            prioritaet=_P_WIDERSPRUCH))
    return out


def _getriebe_widerspruch(req, motor_match: dict | None) -> list[KeyFinding]:
    """Strukturierte Getriebe-Auswahl gegen die erkannte Motorvariante.

    Wirkt nur, wenn die Variante EINDEUTIG eine Getriebeart anbietet. Varianten,
    die Schalter und Automatik anbieten, sagen über das konkrete Fahrzeug nichts
    und erzeugen hier bewusst kein Finding.
    """
    feld = getriebe_normalisiere(getattr(req, "getriebe", None))
    db = getriebe_aus_db(motor_match)
    if not feld or not db or feld == db:
        return []
    variante = (motor_match or {}).get("bezeichnung", "erkannte Motorisierung")
    return [KeyFinding(
        id="", kategorie="widerspruch", stufe=STUFE_WARNUNG, icon="❗",
        titel="Getriebeart passt nicht zur erkannten Motorisierung",
        beschreibung=f"Angegeben ist {getriebe_anzeige(feld)}; für {variante} ist im "
                     f"Datensatz nur {getriebe_anzeige(db)} hinterlegt. Entweder ist die "
                     f"Motorisierung eine andere, oder der Datensatz ist unvollständig.",
        wert=f"Angabe: {getriebe_anzeige(feld)} · Daten: {getriebe_anzeige(db)}",
        aktion="Genaue Motor- und Getriebevariante im Inserat prüfen.",
        prioritaet=_P_WIDERSPRUCH - 20)]


def _widerspruch_findings(req, baureihe: dict | None, motor_match: dict | None) -> list[KeyFinding]:
    out: list[KeyFinding] = []
    out += _strukturiert_vs_inserat(req)
    out += _getriebe_widerspruch(req, motor_match)

    # Kraftstoff-Widerspruch: Inserat-Kraftstoff vs. erkannte Motorisierung.
    ins_kraft = _kraftstoff_norm(getattr(req, "kraftstoff", None)) \
        or _kraftstoff_norm(getattr(req, "motor", None))
    mot_kraft = _kraftstoff_norm((motor_match or {}).get("kraftstoff"))
    if ins_kraft and mot_kraft and ins_kraft != mot_kraft:
        out.append(KeyFinding(
            id="", kategorie="widerspruch", stufe=STUFE_WARNUNG, icon="❗",
            titel="Kraftstoff passt nicht zusammen",
            beschreibung=f"Das Inserat deutet auf {ins_kraft.capitalize()} hin, die erkannte "
                         f"Motorisierung ist {(motor_match or {}).get('kraftstoff')}.",
            wert=f"Inserat: {ins_kraft.capitalize()} · Daten: {(motor_match or {}).get('kraftstoff')}",
            aktion="Motorisierung im Inserat klären.",
            prioritaet=_P_WIDERSPRUCH))

    # Leistungs-Widerspruch: nur klare Abweichung (>=12 PS UND >=8 %) melden.
    ins_ps = _ps_aus_text(getattr(req, "motor", None), getattr(req, "beschreibung", None),
                          getattr(req, "freitext", None))
    mot_ps = (motor_match or {}).get("leistung_ps")
    if ins_ps and mot_ps and abs(ins_ps - mot_ps) >= 12 and abs(ins_ps - mot_ps) / mot_ps >= 0.08:
        out.append(KeyFinding(
            id="", kategorie="widerspruch", stufe=STUFE_WARNUNG, icon="❗",
            titel="Leistungsangabe passt nicht zum erkannten Motor",
            beschreibung=f"Das Inserat nennt {ins_ps} PS, der erkannte Motor "
                         f"({(motor_match or {}).get('bezeichnung', 'Motor')}) hat üblicherweise {mot_ps} PS.",
            wert=f"Inserat: {ins_ps} PS · Daten: {mot_ps} PS",
            aktion="Genaue Motorvariante erfragen.",
            prioritaet=_P_WIDERSPRUCH - 10))

    # Baujahr außerhalb des Bauzeitraums der erkannten Generation.
    bj = getattr(req, "baujahr", None)
    if bj and baureihe:
        von = baureihe.get("bauzeitraum_von")
        bis = baureihe.get("bauzeitraum_bis") or date.today().year
        if von and (bj < von or bj > bis):
            out.append(KeyFinding(
                id="", kategorie="widerspruch", stufe=STUFE_KRITISCH, icon="❗",
                titel="Baujahr passt nicht zur Generation",
                beschreibung=f"Baujahr {bj} liegt außerhalb des Bauzeitraums der erkannten "
                             f"Generation {baureihe.get('generation', '')} ({von}–{bis}).",
                wert=f"Baujahr {bj} · {baureihe.get('generation', '')} {von}–{bis}",
                aktion="Baujahr oder Modell/Generation im Inserat prüfen.",
                prioritaet=_P_WIDERSPRUCH + 20))
    return out


def _positive_findings_kauf(req, preis_finding_erzeugt: bool) -> list[KeyFinding]:
    """Positive Key Findings — nur was tatsächlich belegt ist.

    Audit-Nachtrag (P2-5-Folgeaudit): Eine frühere Fassung erzeugte hier aus
    km/Jahr <= 10.000 (Phase 2, ohne zitierte fachliche Quelle) ein
    "Unterdurchschnittliche Laufleistung"-Finding mit den Stufen "vorteil" /
    STUFE_CHANCE — also eine objektiv klingende positive Bewertung allein aus
    einer unbelegten internen Schwelle. Entfernt, ohne Ersatz: km/Jahr bleibt
    als reiner Zahlenkontext im Laufleistungskontext (P2-5, app/laufleistung.py)
    verfügbar, erzeugt hier aber KEINE Wertung mehr.
    """
    out: list[KeyFinding] = []

    # Nur die BESTE Servicehistorie-Angabe ist ein (vorsichtiger) Vorteil. Die
    # drei anderen Zustände sind Prüfpunkte und stehen in `_servicehistorie_finding`.
    # RC1-Regel bleibt: keine Verstärkung der Verkäuferangabe. "Vollständig
    # angegeben" belegt weder Lückenlosigkeit noch den Umfang der Wartung —
    # deshalb bleibt die Prüfaufforderung im selben Satz stehen.
    if servicehistorie_status(req) == SH_VOLLSTAENDIG:
        out.append(KeyFinding(
            id="", kategorie="vorteil", stufe=STUFE_CHANCE, icon="✅",
            titel="Servicehistorie laut Inserat vollständig angegeben",
            beschreibung=servicehistorie_satz(SH_VOLLSTAENDIG) + " Belege und "
                         "Vollständigkeit vor dem Kauf prüfen: ENFAL hat keine Unterlagen "
                         "gesehen.",
            prioritaet=_P_VORTEIL))

    return out


def _servicehistorie_finding(req) -> list[KeyFinding]:
    """Servicehistorie-Angaben, die einen PRÜFPUNKT ergeben (nicht "vorteil").

    §9 des Auftrags: jeder Zustand verändert den Bericht anders — und keiner
    entfernt ein Risiko. "Nicht angegeben" erzeugt bewusst KEIN Finding: daraus
    eine Aussage zu machen wäre eine Behauptung über ein Inserat, das nichts
    behauptet hat. Diese Lücke ist schon als Verkäuferfrage abgedeckt
    (app/kaufaktionen.py).
    """
    status = servicehistorie_status(req)
    if status == SH_TEILWEISE:
        return [KeyFinding(
            id="", kategorie="angaben", stufe=STUFE_WARNUNG, icon="📋",
            titel="Servicehistorie laut Inserat nur teilweise vorhanden",
            beschreibung=servicehistorie_satz(SH_TEILWEISE) + " Welche Zeiträume und "
                         "Kilometerstände nicht belegt sind, lässt sich nur an den "
                         "Unterlagen selbst feststellen.",
            aktion="Vorhandene Einträge und Rechnungen chronologisch durchgehen und die "
                   "Lücken vor dem Kauf ansprechen.",
            prioritaet=_P_SERVICEHISTORIE)]
    if status == SH_UMFANG_UNKLAR:
        return [KeyFinding(
            id="", kategorie="angaben", stufe=STUFE_INFO, icon="📋",
            titel="Umfang der Servicehistorie laut Inserat offen",
            beschreibung=servicehistorie_satz(SH_UMFANG_UNKLAR),
            aktion="Vor der Besichtigung erfragen, welche Unterlagen konkret vorliegen.",
            prioritaet=_P_SERVICEHISTORIE - 20)]
    if status == SH_NICHT_VORHANDEN:
        return [KeyFinding(
            id="", kategorie="angaben", stufe=STUFE_WARNUNG, icon="📋",
            titel="Keine Servicehistorie laut Inserat",
            beschreibung=servicehistorie_satz(SH_NICHT_VORHANDEN) + " Der Wartungsstand "
                         "dieses Fahrzeugs ist damit nicht nachvollziehbar — das ist eine "
                         "Unsicherheit, kein festgestellter Mangel.",
            aktion="Nach einzelnen Werkstattrechnungen fragen und offene Wartungspunkte im "
                   "Kaufpreis berücksichtigen.",
            prioritaet=_P_SERVICEHISTORIE + 20)]
    return []


# ══ VERKAUFSCHECK ════════════════════════════════════════════════════════════

# Wertsteigernde Ausstattung — reine Keyword-Erkennung, KEINE €-Aufschläge.
_AUSSTATTUNG_WERTVOLL = {
    "leder": "Lederausstattung", "panorama": "Panoramadach", "standheizung": "Standheizung",
    "ahk": "Anhängerkupplung", "anhängerkupplung": "Anhängerkupplung", "navi": "Navigation",
    "matrix": "Matrix-LED", "led": "LED-Scheinwerfer", "head-up": "Head-up-Display",
    "distronic": "Abstandsregeltempomat", "acc": "Abstandsregeltempomat",
    "360": "360°-Kamera", "kamera": "Rückfahrkamera", "memory": "Memory-Sitze",
    "burmester": "Premium-Soundsystem", "harman": "Premium-Soundsystem",
    "sitzheizung": "Sitzheizung", "keyless": "Keyless-Go", "4matic": "Allradantrieb",
    "xdrive": "Allradantrieb", "quattro": "Allradantrieb", "allrad": "Allradantrieb",
    "sportpaket": "Sportpaket", "m sport": "M-Sportpaket", "amg line": "AMG-Line",
}


def _ausstattung_treffer(ausstattung: list[str]) -> list[str]:
    """Keyword-Erkennung mit linker Wortgrenze — deutsche Komposita bleiben treffbar
    (z.B. 'leder' -> 'Lederausstattung', 'panorama' -> 'Panoramadach'), aber ein
    kurzes Keyword darf NICHT in einem längeren Wort durchbluten und eine NICHT
    genannte Ausstattung erfinden (z.B. 'led' in 'Leder')."""
    text = " ".join(ausstattung or []).lower()
    gefunden: list[str] = []
    for key, label in _AUSSTATTUNG_WERTVOLL.items():
        pat = rf"(?<![a-zäöüß]){re.escape(key)}"
        if key == "led":
            pat += r"(?!er)"   # 'led'/'LED-Scheinwerfer', aber nicht 'leder'
        if re.search(pat, text) and label not in gefunden:
            gefunden.append(label)
    return gefunden


def _identitaets_finding_verkauf(fehlende_angabe: str | None) -> KeyFinding:
    """Unsichere Fahrzeugzuordnung im VerkaufsCheck sichtbar machen (P2-A).

    Gegenstück zu `_identitaets_finding` (Kaufcheck) mit derselben Kernregel —
    KEINE vermutete Baureihe nennen, denn genau diese Zuordnung war ja nicht
    belastbar. Eigene Funktion statt eines gemeinsamen Textes, weil der letzte
    Halbsatz produktspezifisch ist: der Kaufcheck verweist auf seine
    Prüflisten, der Verkaufscheck auf Inseratsanalyse und Verkaufstipps. Der
    gefreezte Kaufcheck-Text bleibt dadurch unangetastet.

    Bewusst STUFE_WARNUNG und kein harter Safety-Floor: es ist eine
    Transparenz-Information, kein Mangel am Fahrzeug. Formulierung sachlich,
    ohne Angst-/Fehlerton.
    """
    fehlt = fehlende_angabe or "die genaue Modell- und Generationsbezeichnung"
    return KeyFinding(
        id="", kategorie="identitaet", stufe=STUFE_WARNUNG, icon="❓",
        titel="Fahrzeug nicht eindeutig identifiziert",
        beschreibung="Die genaue Baureihe/Motorisierung konnte aus deinen Angaben nicht "
                     "sicher bestimmt werden. Fahrzeugspezifische Hinweise wurden deshalb "
                     "bewusst eingeschränkt. Inseratsanalyse, Verkaufstipps und die "
                     "Bewertung deiner Angaben gelten unverändert.",
        aktion=f"Für eine gezieltere Analyse bitte {fehlt} nachtragen.",
        prioritaet=_P_IDENTITAET)


def build_key_findings_verkauf(req, baureihe: dict | None, motor_match: dict | None,
                               insights: list[Insight],
                               price_assessment: PriceAssessment | None = None,
                               identitaet: dict | None = None) -> list[KeyFinding]:
    """`identitaet` (optional, P2-A): Info-dict aus
    `car_lookup.find_baureihe_mit_vertrauen`. Ist die Zuordnung nicht belastbar,
    entsteht ein erklärendes Finding statt einer stillen Leerausgabe. Der
    Parameter ist additiv — ohne ihn verhält sich die Funktion exakt wie bisher."""
    findings: list[KeyFinding] = []

    if identitaet is not None and not identitaet.get("belastbar", True):
        findings.append(_identitaets_finding_verkauf(identitaet.get("fehlende_angabe")))

    # ── A) Marktposition aus dem KANONISCHEN Preisurteil (§6) ────────────────────
    mv = _marktvergleich_insight(insights)
    ma = mv.marktanalyse if mv else None
    pa = price_assessment or _pa_from_insights(insights, "verkauf")
    if pa and pa.median_eur and pa.difference_eur is not None and mv:
        findings.append(_preis_finding_verkauf(pa, mv.id))
    elif ma and ma.median_eur and mv:
        findings.append(KeyFinding(
            id="", kategorie="marktposition", stufe=STUFE_INFO, icon="📊",
            titel="Aktueller Marktmedian",
            beschreibung=f"Vergleichbare Fahrzeuge liegen im Median bei {_eur(ma.median_eur)} "
                         f"(typischer Bereich {_eur(ma.spanne_min_eur)}–{_eur(ma.spanne_max_eur)}).",
            wert=_eur(ma.median_eur),
            evidence_ids=[mv.id], prioritaet=_P_V_PREIS_INFO))

    # ── D) Markt-Datenqualität als eigene Erkenntnis (schwache Basis = Erkenntnis) ─
    if ma and ma.median_eur is None and (mv is not None):
        findings.append(KeyFinding(
            id="", kategorie="datenqualitaet", stufe=STUFE_INFO, icon="🛈",
            titel="Marktdaten für belastbare Preisprognose zu dünn",
            beschreibung=(ma.methode or "Zu wenige eindeutig vergleichbare Web-Angebote "
                          "für einen belastbaren Marktwert."),
            aktion="Preis eher konservativ ansetzen und Marktbeobachtung fortsetzen.",
            evidence_ids=[mv.id] if mv else [], prioritaet=_P_V_DATENQUALI))

    # ── C) Fehlende Verkaufsangaben (sofort handlungsrelevant) ──────────────────
    fehlend: list[str] = []
    if not getattr(req, "tuev_bis", None):
        fehlend.append("TÜV/HU")
    if not getattr(req, "unfallfrei", None):
        fehlend.append("Unfallfreiheit")
    if getattr(req, "vorbesitzer", None) is None:
        fehlend.append("Anzahl Vorbesitzer")
    if getattr(req, "scheckheftgepflegt", None) is None:
        fehlend.append("Scheckheft")
    if not getattr(req, "ausstattung", None):
        fehlend.append("Ausstattung")
    if not (getattr(req, "beschreibung", None) or getattr(req, "freitext", None)):
        fehlend.append("Zustandsbeschreibung")
    if fehlend:
        n = len(fehlend)
        findings.append(KeyFinding(
            id="", kategorie="angaben", stufe=STUFE_WARNUNG if n >= 2 else STUFE_INFO, icon="📋",
            titel=f"{n} wichtige Angabe{'n' if n > 1 else ''} fehl{'en' if n > 1 else 't'} im Inserat",
            beschreibung=", ".join(fehlend),
            aktion="Ergänzen: vollständige Angaben schaffen Vertrauen und beschleunigen den Verkauf.",
            prioritaet=_P_V_ANGABEN))

    # ── B) Wertsteigernde Ausstattung prominent nennen ──────────────────────────
    treffer = _ausstattung_treffer(getattr(req, "ausstattung", None) or [])
    if treffer:
        n = len(treffer)
        findings.append(KeyFinding(
            id="", kategorie="ausstattung", stufe=STUFE_CHANCE, icon="✨",
            titel=f"{n} wertsteigernde Ausstattung{'en' if n > 1 else ''} im Inserat betonen",
            beschreibung=", ".join(treffer[:6]),
            aktion="Diese Ausstattung sichtbar im Inserat hervorheben.",
            prioritaet=_P_V_AUSSTATTUNG))

    return _finalisiere(findings)
