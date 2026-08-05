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

from app.models import Insight, KeyFinding, PriceAssessment
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
            titel="Zielpreis unter Marktniveau — Spielraum nach oben",
            beschreibung=f"Du liegst {_eur(diff)} bzw. {_pct(pct)} unter dem Median "
                         f"({_eur(median)}). Ein höherer Startpreis ist realistisch.",
            wert=f"↓ {_pct(pct)} unter Median", evidence_ids=ev, prioritaet=_P_V_PREIS_POS)
    return KeyFinding(id="", kategorie="marktposition", stufe=STUFE_INFO, icon="📊",
        titel="Zielpreis nah am Marktmedian",
        beschreibung=f"Dein Zielpreis liegt sehr nah am Median vergleichbarer Fahrzeuge "
                     f"({_eur(median)}, {_pct(pct)} Abweichung) — realistisch angesetzt.",
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


def _mangel_kurz(insight_titel: str) -> str:
    """'KBA-Rückruf (Baureihe): Brandgefahr ...' -> 'Brandgefahr ...' (gekürzt)."""
    teil = insight_titel.split(":", 1)[-1].strip()
    return (teil[:60] + "…") if len(teil) > 61 else teil


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
        titel_liste = ", ".join(_mangel_kurz(i.titel) for i in zu_pruefen[:3])
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
                         + ", ".join(_mangel_kurz(i.titel) for i in unklar[:3]),
            aktion="Ob dein Fahrzeug betroffen ist, anhand der FIN beim Hersteller/KBA prüfen.",
            evidence_ids=[i.id for i in unklar],
            prioritaet=_P_RUECKRUF_UNKLAR,
        ))
    return out


# ══ KAUFCHECK ════════════════════════════════════════════════════════════════

def build_key_findings_kauf(req, baureihe: dict | None, motor_match: dict | None,
                            insights: list[Insight],
                            price_assessment: PriceAssessment | None = None) -> list[KeyFinding]:
    findings: list[KeyFinding] = []

    # ── A) Preis-Finding aus dem KANONISCHEN Preisurteil (§6) — genau EINE Bewertung ──
    mv = _marktvergleich_insight(insights)
    pa = price_assessment or _pa_from_insights(insights, "kauf")
    preis_finding_erzeugt = False
    if pa and pa.verdict != "unbekannt" and pa.median_eur and pa.difference_eur is not None and mv:
        findings.append(_preis_finding_kauf(pa, mv.id))
        preis_finding_erzeugt = True

    # ── D) Inserat-Widersprüche (rein deterministisch, keine Evidence) ──────────
    findings += _widerspruch_findings(req, baureihe, motor_match)

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
        namen = ", ".join(i.titel.split("—")[0].strip() for i in schwach_hoch[:3])
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
            beschreibung=f"In der vorhandenen VIRA-Datenbasis sind für die erkannte "
                         f"Motorvariante ({motor_match.get('bezeichnung', 'Motor')}) keine schweren "
                         f"Probleme hinterlegt. Das bedeutet nicht, dass keine Defekte auftreten können.",
            prioritaet=_P_MOTOR_OK))

    # ── E) Ungewöhnlich positive Punkte (deterministisch belegbar) ──────────────
    findings += _positive_findings_kauf(req, preis_finding_erzeugt)

    return _finalisiere(findings)


def _widerspruch_findings(req, baureihe: dict | None, motor_match: dict | None) -> list[KeyFinding]:
    out: list[KeyFinding] = []

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
    out: list[KeyFinding] = []

    if getattr(req, "scheckheftgepflegt", None) is True:
        out.append(KeyFinding(
            id="", kategorie="vorteil", stufe=STUFE_CHANCE, icon="✅",
            titel="Scheckheftgepflegt",
            beschreibung="Laut Inserat lückenlose Wartungshistorie — spricht für gute Pflege.",
            prioritaet=_P_VORTEIL))

    bj = getattr(req, "baujahr", None)
    km = getattr(req, "kilometerstand", None)
    if bj and km:
        alter = max(1, date.today().year - bj)
        if alter >= 2:
            pro_jahr = km / alter
            if pro_jahr <= 10_000:
                out.append(KeyFinding(
                    id="", kategorie="vorteil", stufe=STUFE_CHANCE, icon="✅",
                    titel="Unterdurchschnittliche Laufleistung",
                    beschreibung=f"Rund {_eur(pro_jahr).replace(' €', '')} km/Jahr — unter dem "
                                 f"Durchschnitt von ca. 15.000 km/Jahr.",
                    wert=f"≈ {round(pro_jahr / 100) * 100:,} km/Jahr".replace(",", "."),
                    prioritaet=_P_VORTEIL - 20))
    return out


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


def build_key_findings_verkauf(req, baureihe: dict | None, motor_match: dict | None,
                               insights: list[Insight],
                               price_assessment: PriceAssessment | None = None) -> list[KeyFinding]:
    findings: list[KeyFinding] = []

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
            aktion="Ergänzen — vollständige Angaben schaffen Vertrauen und beschleunigen den Verkauf.",
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
