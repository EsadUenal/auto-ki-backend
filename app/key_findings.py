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

from app.models import Insight, KeyFinding

log = logging.getLogger(__name__)

STUFE_KRITISCH = "kritisch"
STUFE_WARNUNG = "warnung"
STUFE_CHANCE = "chance"
STUFE_INFO = "info"

MAX_FINDINGS = 5

# ── Priorisierung (höher = wichtiger). Bewusst in Bänder gruppiert, damit die vom
# Produkt vorgegebene Reihenfolge deterministisch eingehalten wird. ──────────────
# Kauf: 1) Sicherheit/Betrug/Widerspruch  2) Preisabweichung  3) Rückruf
#       4) Schwachstelle  5) Vorteil
_P_BETRUG        = 1000
_P_WIDERSPRUCH   = 950
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
    rueckrufe = [i for i in insights if i.kategorie == "rueckruf"]
    relevant = [i for i in rueckrufe if i.applicability in ("exakt", "wahrscheinlich")]
    unklar = [i for i in rueckrufe if i.applicability == "unklar"]
    out: list[KeyFinding] = []

    if relevant:
        titel_liste = ", ".join(_mangel_kurz(i.titel) for i in relevant[:3])
        n = len(relevant)
        out.append(KeyFinding(
            id="", kategorie="rueckruf", stufe=STUFE_WARNUNG, icon="⚠️",
            titel=f"{n} relevante{'r' if n == 1 else ''} Rückruf" + ("" if n == 1 else "e"),
            beschreibung=titel_liste,
            aktion="Vor Kauf per FIN prüfen, ob die Rückrufaktion durchgeführt wurde.",
            evidence_ids=[i.id for i in relevant],
            prioritaet=_P_RUECKRUF,
        ))
    if unklar:
        # NIE als sicher betroffen darstellen (applicability=unklar).
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
                            insights: list[Insight]) -> list[KeyFinding]:
    findings: list[KeyFinding] = []

    # ── A) Preisabweichung / Betrugssignal (aus deterministischer Marktanalyse) ──
    mv = _marktvergleich_insight(insights)
    ma = mv.marktanalyse if mv else None
    preis_finding_erzeugt = False
    if ma and ma.median_eur and ma.angebot_eur and ma.differenz_pct is not None:
        pct = ma.differenz_pct                 # Angebot − Median (negativ = unter Markt)
        diff = ma.differenz_eur or 0
        gut = ma.datenqualitaet in ("mittel", "hoch")
        caveat = "" if gut else " (auf begrenzter Datenbasis)"
        ev = [mv.id]
        if pct <= -25 and gut:
            findings.append(KeyFinding(
                id="", kategorie="betrug", stufe=STUFE_KRITISCH, icon="🚩",
                titel="Auffällig niedriger Preis",
                beschreibung=f"Das Angebot liegt {_eur(abs(diff))} bzw. {_pct(pct)} unter dem "
                             f"Median vergleichbarer Fahrzeuge ({_eur(ma.median_eur)}). Ein "
                             f"ungewöhnlich niedriger Preis kann auf versteckte Mängel, "
                             f"Vorschäden oder einen Betrugsversuch hindeuten.",
                wert=f"↓ {_eur(abs(diff))} · {_pct(pct)}",
                aktion="Vor Zahlung besichtigen, Papiere und Fahrzeugzustand genau prüfen.",
                evidence_ids=ev, prioritaet=_P_BETRUG))
            preis_finding_erzeugt = True
        elif pct <= -8:
            findings.append(KeyFinding(
                id="", kategorie="preis", stufe=STUFE_CHANCE, icon="💰",
                titel="Deutlich unter Marktpreis",
                beschreibung=f"Das Angebot liegt rund {_eur(abs(diff))} bzw. {_pct(pct)} unter "
                             f"dem Median der verwendeten Vergleichspreise ({_eur(ma.median_eur)}).{caveat}",
                wert=f"↓ {_eur(abs(diff))} · {_pct(pct)}",
                evidence_ids=ev, prioritaet=_P_PREIS_UNTER))
            preis_finding_erzeugt = True
        elif pct >= 20:
            findings.append(KeyFinding(
                id="", kategorie="preis", stufe=STUFE_WARNUNG, icon="💸",
                titel="Deutlich über Marktpreis",
                beschreibung=f"Das Angebot liegt rund {_eur(abs(diff))} bzw. {_pct(pct)} über "
                             f"dem Median vergleichbarer Fahrzeuge ({_eur(ma.median_eur)}).{caveat}",
                wert=f"↑ {_eur(abs(diff))} · {_pct(pct)}",
                aktion="Preis nachverhandeln oder günstigere Alternativen prüfen.",
                evidence_ids=ev, prioritaet=_P_PREIS_UEBER + 10))
            preis_finding_erzeugt = True
        elif pct >= 8:
            findings.append(KeyFinding(
                id="", kategorie="preis", stufe=STUFE_WARNUNG, icon="💸",
                titel="Über Marktpreis",
                beschreibung=f"Das Angebot liegt rund {_eur(abs(diff))} bzw. {_pct(pct)} über "
                             f"dem Median vergleichbarer Fahrzeuge ({_eur(ma.median_eur)}).{caveat}",
                wert=f"↑ {_eur(abs(diff))} · {_pct(pct)}",
                aktion="Spielraum zum Nachverhandeln vorhanden.",
                evidence_ids=ev, prioritaet=_P_PREIS_UEBER))
            preis_finding_erzeugt = True
        else:
            findings.append(KeyFinding(
                id="", kategorie="preis", stufe=STUFE_INFO, icon="💰",
                titel="Preis marktgerecht",
                beschreibung=f"Das Angebot liegt nahe am Median vergleichbarer Fahrzeuge "
                             f"({_eur(ma.median_eur)}, {_pct(pct)} Abweichung).{caveat}",
                evidence_ids=ev, prioritaet=_P_PREIS_INFO))
            preis_finding_erzeugt = True

    # ── D) Inserat-Widersprüche (rein deterministisch, keine Evidence) ──────────
    findings += _widerspruch_findings(req, baureihe, motor_match)

    # ── B) Relevante Rückrufe ───────────────────────────────────────────────────
    findings += _rueckruf_findings(insights)

    # ── C) Motorproblem / hohe Schwachstelle / "Motor unauffällig" ──────────────
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
    elif motor_match and not motorprobleme:
        # Nur behaupten, wenn wir den Motor WIRKLICH erkannt haben (echte Datenbasis).
        findings.append(KeyFinding(
            id="", kategorie="vorteil", stufe=STUFE_CHANCE, icon="✅",
            titel="Motor in der VIRA-Datenbasis unauffällig",
            beschreibung=f"Für die erkannte Motorisierung ({motor_match.get('bezeichnung', 'Motor')}) "
                         f"sind keine spezifischen Schwachstellen hinterlegt.",
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
                               insights: list[Insight]) -> list[KeyFinding]:
    findings: list[KeyFinding] = []

    # ── A) Marktposition (Zielpreis vs. Median) ─────────────────────────────────
    mv = _marktvergleich_insight(insights)
    ma = mv.marktanalyse if mv else None
    if ma and ma.median_eur and ma.angebot_eur and ma.differenz_pct is not None:
        pct = ma.differenz_pct                 # Zielpreis − Median
        diff = ma.differenz_eur or 0
        caveat = "" if ma.datenqualitaet in ("mittel", "hoch") else " (auf begrenzter Datenbasis)"
        ev = [mv.id]
        if pct >= 8:
            findings.append(KeyFinding(
                id="", kategorie="marktposition", stufe=STUFE_WARNUNG, icon="📊",
                titel="Zielpreis über Marktniveau",
                beschreibung=f"Mit {_eur(ma.angebot_eur)} liegst du {_eur(abs(diff))} bzw. {_pct(pct)} "
                             f"über dem Median vergleichbarer Fahrzeuge ({_eur(ma.median_eur)}).{caveat}",
                wert=f"↑ {_pct(pct)} über Median",
                aktion="Höheren Preis nur bei belegbaren Vorteilen ansetzen — sonst verlängert er die Verkaufszeit.",
                evidence_ids=ev, prioritaet=_P_V_PREIS_UEBER))
        elif pct <= -8:
            findings.append(KeyFinding(
                id="", kategorie="marktposition", stufe=STUFE_CHANCE, icon="📊",
                titel="Zielpreis unter Marktniveau — Spielraum nach oben",
                beschreibung=f"Mit {_eur(ma.angebot_eur)} liegst du {_eur(abs(diff))} bzw. {_pct(pct)} "
                             f"unter dem Median ({_eur(ma.median_eur)}). Ein höherer Startpreis ist realistisch.{caveat}",
                wert=f"↓ {_pct(pct)} unter Median",
                evidence_ids=ev, prioritaet=_P_V_PREIS_POS))
        else:
            findings.append(KeyFinding(
                id="", kategorie="marktposition", stufe=STUFE_INFO, icon="📊",
                titel="Zielpreis nah am Marktmedian",
                beschreibung=f"Dein Zielpreis ({_eur(ma.angebot_eur)}) liegt sehr nah am Median "
                             f"vergleichbarer Fahrzeuge ({_eur(ma.median_eur)}, {_pct(pct)} Abweichung) — realistisch angesetzt.{caveat}",
                evidence_ids=ev, prioritaet=_P_V_PREIS_INFO))
    elif ma and ma.median_eur:
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
