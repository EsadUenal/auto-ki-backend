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
   Mängel bleiben ehrlich erhalten. Grundsatz: VIRA fügt NIE eine positive Aussage
   hinzu, die der Nutzer nicht angegeben hat.
"""

import logging
import re
from datetime import date, datetime

from app.car_lookup import call_gemini_json
from app.key_findings import _AUSSTATTUNG_WERTVOLL, _ausstattung_treffer, _kraftstoff_norm
from app.models import FehlendeAngabe, InseratOptimierung, ListingAnalyse, VerkaufsCheckRequest

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
        ("Unfallfreiheit",       bool(getattr(req, "unfallfrei", None)),     "wichtig"),
        ("Anzahl Vorbesitzer",   getattr(req, "vorbesitzer", None) is not None, "wichtig"),
        ("Scheckheft / Wartung", getattr(req, "scheckheftgepflegt", None) is not None, "wichtig"),
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
        out.append(f"TÜV/HU angegeben ({tuev})" + (" — noch lange gültig" if _tuev_lange_gueltig(tuev) else ""))
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
    # Lückenlose Wartung
    if getattr(req, "scheckheftgepflegt", None) is True and "Scheckheftgepflegt" not in args:
        args.append("Vollständige Wartungshistorie")
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

    return out


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
                    f"Marktmedian ({_eur(ma.median_eur)}) — das kann die Verkaufszeit verlängern. "
                    f"Höher nur bei belegbaren Vorteilen ansetzen.", ev)
        if pct <= -8:
            return (f"Deine Preisvorstellung ({_eur(ma.angebot_eur)}) liegt rund {abs(pct):.0f} % unter dem "
                    f"Marktmedian ({_eur(ma.median_eur)}) — ein höherer Startpreis ist realistisch.", ev)
        return (f"Deine Preisvorstellung ({_eur(ma.angebot_eur)}) liegt nah am Marktmedian "
                f"({_eur(ma.median_eur)}) — realistisch angesetzt.", ev)
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


def _claim_rules(req: VerkaufsCheckRequest) -> list[tuple[str, re.Pattern, bool]]:
    """(menschlicher Name, Regex, ist_belegt) — trifft die Regex zu und ist NICHT
    belegt, wird der betroffene Satz/Aufzählungspunkt entfernt."""
    unfall_ok = _norm(getattr(req, "unfallfrei", None)) == "ja"
    scheck_ok = getattr(req, "scheckheftgepflegt", None) is True
    erste_hand_ok = getattr(req, "vorbesitzer", None) == 1
    tuev_ok = bool(getattr(req, "tuev_bis", None))
    return [
        ("Unfallfreiheit",
         re.compile(r"unfallfrei|kein(?:e|erlei)?\s+unfall|ohne\s+unfall|unfallschadenfrei", re.IGNORECASE),
         unfall_ok),
        ("Scheckheft",
         re.compile(r"scheckheft|lückenlos\w*\s+(?:wartung|service|historie)|serviceheft\s+gepflegt|voll\w*\s+scheckheft", re.IGNORECASE),
         scheck_ok),
        ("Erste Hand / Vorbesitzer",
         re.compile(r"erste\s*hand|1\.?\s*hand|erstbesitz|aus\s+erster\s+hand", re.IGNORECASE),
         erste_hand_ok),
        ("TÜV/HU",
         re.compile(r"\b(t[üu]v|hu|hauptuntersuchung)\b", re.IGNORECASE),
         tuev_ok),
        ("Garantie",
         re.compile(r"\bgarantie\b|\bgewährleistung\b", re.IGNORECASE),
         False),   # VIRA hat keine Garantie-Daten -> nie behaupten
    ]


def _satz_ok(satz: str, rules, ausst_backed: set[str], entfernt: list[str]) -> bool:
    """True, wenn der Satz/Punkt behalten werden darf. Prüft belegbare Positiv-
    Behauptungen und erfundene Ausstattung."""
    for name, rx, belegt in rules:
        if rx.search(satz) and not belegt:
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
            if rx.search(seg) and not belegt:
                entfernt.append(f"Titel — {name}: „{seg}“")
                drop = True
                break
        if not drop:
            for key, label in _AUSSTATTUNG_WERTVOLL.items():
                if key not in ausst_backed and _keyword_in(snorm, key):
                    entfernt.append(f"Titel — nicht angegebene Ausstattung ({label}): „{seg}“")
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
    dass angegebene Mängel ehrlich enthalten bleiben. VIRA fügt NIE eine positive
    Aussage hinzu, die der Nutzer nicht angegeben hat.
    """
    entfernt: list[str] = []
    titel_clean = _scrub_titel(titel or "", req, entfernt)
    beschr_clean = _scrub_text(beschreibung or "", req, entfernt)
    beschr_clean = _maengel_ehrlich(beschr_clean, req)
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
4. **Zustand & Wartung** (nur angegebene Fakten: Zustand, Scheckheft, TÜV, Vorbesitzer)
5. **Bekannte Mängel** (falls angegeben — ehrlich benennen)
6. Sachlicher Abschluss (z. B. Einladung zur Besichtigung/Probefahrt) — ohne Kontaktdaten zu erfinden

Schreibe auf Deutsch, professionell und vertrauenswürdig.\
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
    uf = _norm(getattr(req, "unfallfrei", None))
    if uf == "ja":
        add("Unfallstatus", "unfallfrei (laut Verkäufer)")
    elif uf == "nein":
        add("Unfallstatus", "Unfallschaden vorhanden")
    vb = getattr(req, "vorbesitzer", None)
    if vb is not None:
        add("Vorbesitzer", vb)
    if getattr(req, "scheckheftgepflegt", None) is True:
        add("Wartung", "scheckheftgepflegt")
    add("TÜV/HU bis", getattr(req, "tuev_bis", None))
    if getattr(req, "maengel", None):
        add("Bekannte Mängel (ehrlich benennen)", ", ".join(req.maengel))
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
