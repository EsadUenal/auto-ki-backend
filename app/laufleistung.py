from __future__ import annotations

"""
P2-5 — Laufleistungs- und Wartungskontext.

Der Kaufcheck kennt Kilometerstand und Baujahr seit jeher, hat aber nie etwas
daraus gemacht. Dieses Modul ordnet beides ein und leitet daraus ab, WELCHE
hinterlegten Wartungspunkte bei genau dieser Laufleistung relevant sind.


═══ WAS NICHT BEKANNT IST (der Ausgangspunkt dieses Moduls) ═══════════════════

Vor der Umsetzung wurde der gesamte Datenbestand daraufhin geprüft, ob irgendwo
steht, WANN an diesem Fahrzeug zuletzt gearbeitet wurde. Ergebnis:

  KaufCheckRequest   marke, modell, baujahr, kilometerstand, motor, kraftstoff,
                     preis_eur, ausstattung, beschreibung, unfallfrei,
                     vorbesitzer, tuev_bis, scheckheftgepflegt, freitext, bild
                     -> KEIN Feld für letzten Service, letzten Ölwechsel,
                        Kilometerstand beim letzten Service oder Wartungsdatum.
  Tabelle baureihe   ... wartung_oel_km, wartung_hu_intervall ...
                     -> HERSTELLERINTERVALLE der Baureihe, keine Durchführung.
  kritische_wartung  variante_id, bauteil, intervall, hinweis
                     -> VORGESEHENES Intervall, keine Durchführung.
  motorvariante      -> keine Wartungsspalte.
  web_wartung        quellengebundene INTERVALL-Angaben aus der Webrecherche
                     -> ebenfalls Sollwerte, nie fahrzeugindividuelle Historie.
  scheckheftgepflegt bool — eine BEHAUPTUNG des Inserats, ohne Datum, ohne
                     Kilometerstand, ohne Beleg.

Damit steht fest: **Der letzte tatsächliche Service ist nicht bestimmbar.**
`Laufleistungskontext.letzter_service_bekannt` ist deshalb dauerhaft False.

Daraus folgt die zentrale Regel dieses Moduls, und sie gilt ausnahmslos:

    Aus einem Intervall + einem Kilometerstand folgt KEINE Fälligkeit.

Ein Intervall sagt, in welchem Abstand eine Arbeit vorgesehen ist. Ob sie
gemacht wurde, sagt es nicht. Ein Fahrzeug mit 118.000 km und einem
Zahnriemen-Intervall von 120.000 km kann den Riemen gerade neu haben oder nie
gewechselt bekommen haben — VIRA kann das nicht unterscheiden und behauptet es
deshalb nicht. Was VIRA leisten kann und hier auch leistet: dem Käufer sagen,
DASS dieser Punkt bei dieser Laufleistung relevant ist und dass er sich den
Nachweis zeigen lassen soll.

Ausdrücklich NICHT gebaut (§2): `kilometerstand % intervall`. Eine Modulo-
Rechnung tut so, als sei der Wartungszyklus lückenlos eingehalten worden — genau
die Annahme, die niemand belegen kann. Berechnet wird ausschließlich der Abstand
zum ERSTEN hinterlegten Wartungspunkt.


═══ VERTRAUENSSTUFEN DER WARTUNGSQUELLEN ══════════════════════════════════════

  A) `kritische_wartung` der ERKANNTEN Motorvariante        -> erzeugt Hinweise
     Konkretes Bauteil, konkretes Intervall, Motorbezug, und über
     `build_insights` eine echte Evidence-ID. Die Baujahres-Applicability kommt
     dabei vollständig aus der Fahrzeugerkennung: die Tabelle hat keine
     Baujahres-Spalte, aber ein Baujahr, das dem Bauzeitraum widerspricht,
     lässt `find_baureihe_mit_vertrauen` schon gar keine belastbare Baureihe
     liefern (P0-2) — dann existiert der Wartungs-Insight nicht und hier kann
     nichts entstehen. Es wird KEINE eigene Baujahreslogik erfunden.

  B) `web_wartung` aus dem technischen Web-Fallback         -> erzeugt Hinweise
     Quellengebunden (ohne URL entsteht dort kein Fakt), mit eigener Confidence
     und eigener Insight-ID. Der Text macht die Herkunft sichtbar.

  C) `wartung_oel_km` aus dem Fahrzeugkontext (P1-4)        -> KEINE Hinweise
     Der Trust-Audit hat gemessen: 62,7 % Coverage, keine Quelle, keine
     Verification, Baureihen- statt Motorebene, 81 % der Baureihen mit
     mehreren Kraftstoffarten unter EINEM Intervall. Zu dünn, um daraus eine
     Nutzerwarnung zu bauen. Das Feld bleibt genau das, was P1-4 daraus gemacht
     hat: ERGÄNZENDER Prompt-Kontext mit ausdrücklichem Fälligkeitsverbot
     (app/fahrzeugkontext.py::prompt_block). Dieses Modul liest es nicht.

     Damit ist §10 auch praktisch erfüllt: Eine quellengebundene Web-Angabe zum
     Ölwechsel wird über Pfad B zu einem echten, belegten Hinweis — der
     unverified DB-Wert über Pfad C dagegen nie. Die Webquelle wiegt also
     tatsächlich schwerer, ohne dass dafür ein künstliches Upgrade-Verfahren
     nötig wäre und ohne jede DB-Mutation.


═══ WAS DIESES MODUL NICHT TUT ═══════════════════════════════════════════════

  * Keine Preisaussage (§13). Es bekommt weder `marktanalyse` noch
    `price_assessment` noch `preis_eur` — eine "hohe Laufleistung, also
    günstiger"-Aussage ist strukturell nicht konstruierbar. Der Marktpreis
    bleibt vollständig beim Marktvergleich.
  * Keine Marktabhängigkeit (§14). Bei `completed_no_market` entsteht exakt
    derselbe Kontext wie mit Marktdaten.
  * Keine Erfindung bei DB-Miss (§15). Ohne Wartungs-Evidence entstehen null
    Wartungshinweise; Alter und km/Jahr bleiben trotzdem berechenbar, weil sie
    nur aus den Nutzerangaben stammen.
  * Keine Aussage über eine fehlende Servicehistorie (§11). Das Wort "fehlt"
    kommt in keinem erzeugten Text vor.
"""

import logging
import re
from datetime import date

from app.models import EvidenceQuelle, Insight, Laufleistungskontext, Wartungshinweis

log = logging.getLogger(__name__)


# ── Laufleistungs-Einordnung (§6) ─────────────────────────────────────────────
#
# KEINE neu erfundenen Schwellen. Der Bestand kannte bereits zwei Werte, beide in
# app/key_findings.py::_positive_findings_kauf: die Schwelle 10.000 km/Jahr für
# "unterdurchschnittlich" und den dort genannten Referenzwert von ca. 15.000
# km/Jahr. Sie stehen jetzt hier und werden von dort importiert — EINE Quelle der
# Wahrheit statt zweier Literale, die auseinanderlaufen können.
#
# `SCHWELLE_ERHOEHT` ist der einzige neue Wert und bewusst NICHT frei gewählt: er
# spiegelt den vorhandenen Abstand am Referenzwert (15.000 − 10.000 = 5.000)
# nach oben. Eine eigene, "wissenschaftlich" klingende Grenze wäre eine
# Behauptung ohne Grundlage.
SCHWELLE_NIEDRIG = 10_000
REFERENZ_DURCHSCHNITT = 15_000
SCHWELLE_ERHOEHT = REFERENZ_DURCHSCHNITT + (REFERENZ_DURCHSCHNITT - SCHWELLE_NIEDRIG)

EINORDNUNG_NIEDRIG = "niedrig"
EINORDNUNG_DURCHSCHNITTLICH = "durchschnittlich"
EINORDNUNG_ERHOEHT = "erhoeht"

# Unter zwei Jahren wird NICHT eingeordnet — dieselbe Grenze, die key_findings
# schon zog. Bei einem sehr jungen Fahrzeug schlägt der fehlende
# Erstzulassungsmonat voll durch: ein im Dezember zugelassener "Einjähriger"
# hätte rechnerisch die doppelte Jahresfahrleistung.
MIN_ALTER_EINORDNUNG = 2

# Obergrenzen gegen Tippfehler/Unsinn (1.500.000 km, Baujahr 1600). Verworfen
# wird still — ein unplausibler Wert erzeugt keinen Kontext, aber auch keinen
# Fehler.
MAX_PLAUSIBEL_KM = 2_000_000
MIN_PLAUSIBEL_BAUJAHR = 1900


# ── Wartungspunkt-Status (§8) ─────────────────────────────────────────────────

STATUS_ENTFERNT = "entfernt"
STATUS_NAEHERT_SICH = "naehert_sich"
STATUS_IM_BEREICH = "im_bereich"
STATUS_DARUEBER = "darueber"

# Das Toleranzfenster um einen Wartungspunkt ist bewusst PROPORTIONAL statt fest:
# bei einem 20.000-km-Punkt (Ventilspiel) sind 12.000 km Abstand eine andere
# Größenordnung als bei einem 210.000-km-Punkt. 10 % ist eine reine
# Darstellungsentscheidung darüber, ab wann ein Punkt "in der Nähe" heißt — sie
# behauptet nichts über das Fahrzeug. Die Klammer verhindert, dass das Fenster
# bei sehr kleinen oder sehr großen Intervallen unbrauchbar wird.
FENSTER_ANTEIL = 0.10
FENSTER_MIN_KM = 5_000
FENSTER_MAX_KM = 20_000

# Höchstzahl ausgegebener Wartungshinweise. Der Prüfplan soll nicht zulaufen —
# die Kaufaktionen begrenzen aus demselben Grund auf 6 je Bereich.
MAX_HINWEISE = 6


def _fenster(punkt_km: int) -> int:
    return int(min(FENSTER_MAX_KM, max(FENSTER_MIN_KM, round(punkt_km * FENSTER_ANTEIL))))


# ── Intervall-Parser ──────────────────────────────────────────────────────────
#
# Gemessen über alle 1.497 Zeilen der Tabelle `kritische_wartung`: 1.291 (86 %)
# tragen eine auswertbare Kilometerangabe. Die restlichen 206 sind zeit- oder
# zustandsbezogen ("Alle 2 Jahre", "Bei Geräuschen prüfen", "Nach Bedarf",
# "Kein fester Intervall") — für die entsteht korrekt KEIN Wartungshinweis. Eine
# Zeitangabe wird NIE gegen den Kilometerstand gerechnet; das ist derselbe
# Grundsatz, mit dem P1-4 die zeitgesteuerte HU von jeder km-Logik freihält.

_ZAHL = r"\d{1,3}(?:[.\s]\d{3})+|\d{2,7}"

# Spanne zuerst prüfen: "90.000 - 120.000 km", "~50-80 tkm", "150.000 bis 250.000 km".
_SPANNE = re.compile(rf"({_ZAHL})\s*(?:-|–|—|bis)\s*({_ZAHL})\s*(tkm|km)\b", re.IGNORECASE)
_EINZELN = re.compile(rf"({_ZAHL})\s*(tkm|km)\b", re.IGNORECASE)
# 15 Zeilen tragen die blanke Zahl ohne Einheit ("60000", "120000", "40000").
# Nur akzeptiert, wenn sie den GESAMTEN Wert ausmacht und in einem Bereich liegt,
# der als Jahresangabe ausgeschlossen ist.
_BLANK = re.compile(r"^\s*(\d{4,7})\s*$")
_BLANK_MIN_KM = 10_000
_MAX_PUNKT_KM = 500_000
_MIN_PUNKT_KM = 1_000


def _zu_km(zahl: str, einheit: str | None) -> int:
    wert = int(re.sub(r"[.\s]", "", zahl))
    if (einheit or "").lower() == "tkm":
        wert *= 1000
    return wert


def parse_wartungspunkt(text: str | None) -> tuple[int, int | None] | None:
    """Kilometer-Wartungspunkt aus einem Intervalltext — oder None.

    Rückgabe: `(von_km, bis_km|None)`. Bei einer Spanne ist `von_km` der UNTERE
    Wert: der Wartungspunkt beginnt dort relevant zu werden, und die konservative
    Lesart ist die frühere. Steht im Text mehr als eine Angabe ("120.000 km /
    180.000 km (je nach Motorcode)"), gewinnt aus demselben Grund die erste.

    Zeit- und zustandsbezogene Angaben liefern None — sie werden NIE in eine
    Kilometerrechnung übersetzt.
    """
    if not text:
        return None
    t = str(text).strip()

    m = _SPANNE.search(t)
    if m:
        von = _zu_km(m.group(1), m.group(3))
        bis = _zu_km(m.group(2), m.group(3))
        if von > bis:
            von, bis = bis, von
        return (von, bis) if _plausibel(von) else None

    m = _EINZELN.search(t)
    if m:
        von = _zu_km(m.group(1), m.group(2))
        return (von, None) if _plausibel(von) else None

    m = _BLANK.match(t)
    if m:
        von = int(m.group(1))
        if _BLANK_MIN_KM <= von <= _MAX_PUNKT_KM:
            return (von, None)
    return None


def _plausibel(km: int) -> bool:
    """Ein Wartungspunkt unter 1.000 km ist keine Laufleistungsangabe (meist eine
    Jahreszahl oder eine Literzahl), über 500.000 km keine sinnvolle Vorgabe."""
    return _MIN_PUNKT_KM <= km <= _MAX_PUNKT_KM


# ── Alter / km pro Jahr (§4, §5) ──────────────────────────────────────────────

def fahrzeugalter(baujahr: int | None, *, heute_jahr: int | None = None) -> int | None:
    """Ungefähres Fahrzeugalter in ganzen Jahren — oder None.

    Bewusst NUR aus dem Baujahr (§4): der Erstzulassungsmonat wird im gesamten
    System nirgends erfasst, eine monatsgenaue Angabe wäre erfunden. Ein 2020er
    Fahrzeug ist 2026 damit "ungefähr 6 Jahre" alt — mehr Genauigkeit gibt die
    Datenlage nicht her.
    """
    if not baujahr:
        return None
    try:
        bj = int(baujahr)
    except (TypeError, ValueError):
        return None
    jahr = heute_jahr if heute_jahr is not None else date.today().year
    if bj < MIN_PLAUSIBEL_BAUJAHR or bj > jahr:
        return None            # Zukunfts-/Unsinnsbaujahr: keine Angabe statt einer falschen
    return jahr - bj


def km_pro_jahr(kilometerstand: int | None, alter_jahre: int | None) -> int | None:
    """Durchschnittliche Jahresfahrleistung SEIT DEM BAUJAHR — oder None.

    Auf volle 100 km gerundet (§5): das Ergebnis ist ein Durchschnitt über die
    gesamte Fahrzeuglebensdauer, kein gemessener Jahreswert. "11.875 km/Jahr"
    würde eine Genauigkeit vortäuschen, die die Rechnung nicht hat. Die Rundung
    entspricht der Darstellung, die key_findings schon nutzt.

    Ein Fahrzeug im Baujahr selbst (Alter 0) bekommt keinen Wert: eine Division
    durch die angebrochene Erstperiode wäre reine Erfindung.
    """
    if not kilometerstand or not alter_jahre or alter_jahre < 1:
        return None
    if kilometerstand < 0 or kilometerstand > MAX_PLAUSIBEL_KM:
        return None
    return int(round(kilometerstand / alter_jahre / 100.0)) * 100


def einordnung(pro_jahr: int | None, alter_jahre: int | None) -> str | None:
    """Neutrale Einordnung der Jahresfahrleistung — oder None (§6).

    Drei Werte, KEIN Qualitätsurteil: "niedrig" ist nicht gut (Kurzstrecken- und
    Standschäden sind real), "erhoeht" ist nicht schlecht (Autobahnkilometer sind
    schonend). Der Kontext gehört in die Hand des Käufers, die Bewertung nicht in
    die des Systems.

    None bei zu junger Datenbasis — dann steht in der Antwort nur die Zahl.
    """
    if pro_jahr is None or alter_jahre is None or alter_jahre < MIN_ALTER_EINORDNUNG:
        return None
    if pro_jahr <= SCHWELLE_NIEDRIG:
        return EINORDNUNG_NIEDRIG
    if pro_jahr >= SCHWELLE_ERHOEHT:
        return EINORDNUNG_ERHOEHT
    return EINORDNUNG_DURCHSCHNITTLICH


# ── Wartungspunkt-Status + Formulierung ───────────────────────────────────────

def status_zu_punkt(kilometerstand: int, von_km: int, bis_km: int | None) -> str:
    """Lage des Kilometerstands zum hinterlegten Wartungspunkt (§8).

    Ausdrücklich KEINE Modulo-Rechnung: verglichen wird gegen den ERSTEN
    hinterlegten Punkt, nicht gegen "den nächsten Zyklus". Und "darueber" heißt
    NICHT "nicht gemacht" — es heißt, dass diese Laufleistung den vorgesehenen
    Punkt bereits passiert hat und der Nachweis deshalb besonders interessant ist.
    """
    fenster = _fenster(von_km)
    obergrenze = (bis_km if bis_km is not None else von_km) + fenster
    if kilometerstand > obergrenze:
        return STATUS_DARUEBER
    if kilometerstand >= von_km - fenster:
        return STATUS_IM_BEREICH
    if kilometerstand >= von_km - 2 * fenster:
        return STATUS_NAEHERT_SICH
    return STATUS_ENTFERNT


def _km(wert: int) -> str:
    return f"{wert:,} km".replace(",", ".")


def _punkt_text(von_km: int, bis_km: int | None) -> str:
    return _km(von_km) if bis_km is None else f"{_km(von_km)} bis {_km(bis_km)}"


# Der Herkunftszusatz steht im TEXT, nicht nur in einem Feld: die Prüflisten
# werden einzeln ausgedruckt (P1-3 §13) und stehen dann ohne jede Oberfläche da.
_HERKUNFT_ZUSATZ = {
    "db_wartung": "",
    "web_wartung": " Die Intervallangabe stammt aus der Webrecherche, nicht aus "
                   "der geprüften Fahrzeugdatenbank.",
}


def _formuliere(bauteil: str, status: str, kilometerstand: int,
                von_km: int, bis_km: int | None, herkunft: str) -> str:
    """Der ausformulierte Hinweis.

    Jede Variante ist bewusst so gebaut, dass sie eine ZUSTANDSFRAGE stellt statt
    einer Zustandsbehauptung. Verboten und in keinem dieser Texte enthalten:
    "fällig", "überfällig", "versäumt", "nicht durchgeführt", "fehlt".
    """
    punkt = _punkt_text(von_km, bis_km)
    zusatz = _HERKUNFT_ZUSATZ.get(herkunft, "")
    if status == STATUS_NAEHERT_SICH:
        rest = _km(von_km - kilometerstand)
        kern = (f"Für „{bauteil}“ ist ein Wartungspunkt bei {punkt} hinterlegt — rund "
                f"{rest} voraus. Vor dem Kauf klären, wann diese Arbeit zuletzt "
                f"gemacht wurde, und den Beleg dazu ansehen.")
    elif status == STATUS_IM_BEREICH:
        kern = (f"Bei dieser Laufleistung liegt „{bauteil}“ im relevanten Bereich des "
                f"hinterlegten Wartungspunkts ({punkt}). Nachweis über die Durchführung "
                f"zeigen lassen — ob die Arbeit bereits erledigt ist, geht aus den "
                f"vorliegenden Daten nicht hervor.")
    elif status == STATUS_DARUEBER:
        rueck = _km(kilometerstand - (bis_km if bis_km is not None else von_km))
        kern = (f"Der hinterlegte Wartungspunkt für „{bauteil}“ ({punkt}) liegt rund "
                f"{rueck} zurück. Bei dieser Laufleistung sollte geprüft werden, ob die "
                f"Wartung bereits durchgeführt wurde — die vorliegenden Daten sagen "
                f"darüber nichts aus.")
    else:
        return ""
    return kern + zusatz


# ── Evidence -> Wartungshinweise ──────────────────────────────────────────────

# Aus welcher Insight-Kategorie ein Hinweis entstehen darf, und wie die Herkunft
# im Ergebnis heißt. `wartung_oel_km` steht hier bewusst NICHT (Stufe C).
_QUELLEN_KATEGORIEN = {"wartung": "db_wartung", "web_wartung": "web_wartung"}

# Aus dem DB-Wartungs-Insight den reinen Intervallteil zurückgewinnen.
# `build_insights` setzt ihn wörtlich so zusammen (app/evidence.py).
_INTERVALL_SATZ = re.compile(r"Vorgesehenes Intervall:\s*(?P<wert>.+?)\.\s*$")

_WARTUNG_TITEL = re.compile(r"^(?P<bauteil>.+?)\s+—\s+")


def _bauteil(i: Insight) -> str:
    """Bauteil eines Wartungs-Insights.

    `quellen[0].ref` trägt es bei DB-Insights direkt (von `build_insights`
    gesetzt); bei Web-Insights steht es vor dem Gedankenstrich im Titel.
    """
    for q in i.quellen:
        if q.typ == "motorvarianten" and q.ref:
            return q.ref.strip()
    m = _WARTUNG_TITEL.match(i.titel or "")
    return _anzeigeform((m.group("bauteil").strip() if m else (i.titel or "").strip())
                        or "Wartungspunkt")


def _anzeigeform(bauteil: str) -> str:
    """Erster Buchstabe groß — aber NUR bei durchgehend kleingeschriebenen Namen.

    Web-Fakten tragen den Bauteilnamen aus dem normalisierten Vokabular des
    technischen Fallbacks ("zahnriemen", "agr ventil"). Wörtlich übernommen
    stünde im Prüfplan „zahnriemen“, was auf Papier wie ein Fehler aussieht.
    Bereits gemischt geschriebene DB-Namen ("AGR-Kühler", "SMG-Getriebeöl")
    bleiben unangetastet — sie würden durch eine pauschale Umwandlung kaputt
    gehen.
    """
    return bauteil[:1].upper() + bauteil[1:] if bauteil.islower() else bauteil


def _intervall_text(i: Insight, herkunft: str) -> str | None:
    """Der Textausschnitt, aus dem ein Kilometerwert gelesen werden darf.

    Bei DB-Insights bewusst NUR der Teil hinter "Vorgesehenes Intervall:". Der
    freie `hinweis`-Teil enthält im Bestand ebenfalls Zahlen ("Bei
    Kurzstreckenbetrieb kann der DPF verstopfen"), die kein Wartungspunkt sind —
    sie dürfen nicht versehentlich zu einem werden. Fehlt der Intervallteil,
    entsteht korrekt kein Hinweis.

    Bei Web-Insights ist die gesamte `aussage` der Kandidat: der Provider hat sie
    bereits gegen ein Intervallmuster geprüft (app/technical_research.py) und
    ohne Quelle gar nicht erst aufgenommen.
    """
    text = (i.beschreibung or "").strip()
    if not text:
        return None
    if herkunft == "db_wartung":
        m = _INTERVALL_SATZ.search(text)
        return m.group("wert").strip() if m else None
    return text


def _wartungshinweise(insights: list[Insight], kilometerstand: int) -> list[Wartungshinweis]:
    """Wartungshinweise aus vorhandener Evidence — nie aus dem Nichts.

    Deduplizierung über das Bauteil: nennt sowohl die Fahrzeugdatenbank als auch
    die Webrecherche denselben Wartungspunkt, gewinnt die geprüfte DB-Angabe
    (sie wird zuerst eingetragen). Dieselbe Regel wie in den Kaufaktionen.

    Sortierung: der bereits passierte Punkt zuerst, dann der erreichte, dann der
    nahende — innerhalb einer Stufe der mit dem größten Abstand. Bei Gleichstand
    nach Bauteilname, damit die Ausgabe deterministisch bleibt.
    """
    gesehen: set[str] = set()
    out: list[Wartungshinweis] = []
    for i in insights:
        herkunft = _QUELLEN_KATEGORIEN.get(i.kategorie)
        if herkunft is None:
            continue
        roh = _intervall_text(i, herkunft)
        punkt = parse_wartungspunkt(roh)
        if punkt is None:
            continue           # zeit-/zustandsbezogen: keine Kilometerrechnung
        von_km, bis_km = punkt
        status = status_zu_punkt(kilometerstand, von_km, bis_km)
        if status == STATUS_ENTFERNT:
            continue           # §G: kein Anlass, also keine Warnung
        bauteil = _bauteil(i)
        schluessel = norm_bauteil(bauteil)
        if schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        out.append(Wartungshinweis(
            bauteil=bauteil,
            status=status,
            punkt_km=von_km,
            punkt_bis_km=bis_km,
            differenz_km=kilometerstand - von_km,
            intervall_text=(roh or "").strip(),
            hinweis=_formuliere(bauteil, status, kilometerstand, von_km, bis_km, herkunft),
            herkunft=herkunft,
            evidence_id=i.id,
            quellen=[EvidenceQuelle(**q.model_dump()) for q in i.quellen],
        ))

    rang = {STATUS_DARUEBER: 3, STATUS_IM_BEREICH: 2, STATUS_NAEHERT_SICH: 1}
    out.sort(key=lambda w: (-rang[w.status], -abs(w.differenz_km), w.bauteil))
    return out[:MAX_HINWEISE]


# Bauteilschlüssel für die Dedup. Muss dieselbe Normalisierung nutzen wie die
# Kaufaktionen, damit "Zahnriemen" aus DB und Web denselben Schlüssel ergeben.
_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                          "Ä": "ae", "Ö": "oe", "Ü": "ue"})


def norm_bauteil(text: str | None) -> str:
    t = (text or "").strip().lower().translate(_UMLAUTE)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# ── Öffentliche API ───────────────────────────────────────────────────────────

def build_laufleistungskontext(req, insights: list[Insight] | None,
                               *, heute_jahr: int | None = None) -> Laufleistungskontext | None:
    """Baut den Laufleistungskontext — oder None, wenn nichts Belastbares übrig bleibt.

    Bekommt bewusst NUR den Request und die bereits gebauten Insights: keine
    Marktanalyse, keinen Preis, keinen LLM-Bericht, keine neue DB-Abfrage. Damit
    ist §13 strukturell und nicht nur textlich erfüllt, und §14 (identisches
    Ergebnis bei `completed_no_market`) gilt automatisch.

    `heute_jahr` existiert für reproduzierbare Tests; produktiv wird das aktuelle
    Jahr genommen.
    """
    km = getattr(req, "kilometerstand", None)
    try:
        km = int(km) if km is not None else None
    except (TypeError, ValueError):
        km = None
    if km is not None and not (0 <= km <= MAX_PLAUSIBEL_KM):
        log.info("Laufleistung: Kilometerstand unplausibel (%s) — verworfen", km)
        km = None

    alter = fahrzeugalter(getattr(req, "baujahr", None), heute_jahr=heute_jahr)
    pro_jahr = km_pro_jahr(km, alter)
    hinweise = _wartungshinweise(insights or [], km) if km else []

    ctx = Laufleistungskontext(
        kilometerstand=km,
        fahrzeugalter_jahre=alter,
        km_pro_jahr=pro_jahr,
        laufleistungs_einordnung=einordnung(pro_jahr, alter),
        wartungshinweise=hinweise,
        # Unveränderlich False, solange es keine Datenquelle für den letzten
        # Service gibt — siehe Modulkopf. Kein Platzhalter, sondern ein Befund.
        letzter_service_bekannt=False,
    )
    return ctx if ctx.hat_inhalt() else None


_EINORDNUNG_TEXT = {
    EINORDNUNG_NIEDRIG: "unterdurchschnittlich",
    EINORDNUNG_DURCHSCHNITTLICH: "im üblichen Rahmen",
    EINORDNUNG_ERHOEHT: "überdurchschnittlich",
}


def prompt_block(ctx: Laufleistungskontext | None) -> str:
    """Kompakter Prompt-Abschnitt — leerer String, wenn nichts vorliegt.

    Der Block trägt die Verbote MIT, statt sich auf den Systemprompt zu verlassen.
    Ein Modell, das "Zahnriemen: Wartungspunkt bei 120.000 km, Fahrzeug bei
    160.000 km" liest, formuliert ohne ausdrückliches Verbot mit hoher
    Wahrscheinlichkeit "Zahnriemenwechsel überfällig" — und genau diese Aussage
    ist durch nichts gedeckt.
    """
    if ctx is None:
        return ""
    zeilen: list[str] = []
    if ctx.kilometerstand is not None:
        zeilen.append(f"Kilometerstand: {_km(ctx.kilometerstand)}")
    if ctx.fahrzeugalter_jahre is not None:
        zeilen.append(f"Fahrzeugalter: ungefähr {ctx.fahrzeugalter_jahre} Jahre "
                      f"(aus dem Baujahr; der Monat der Erstzulassung ist nicht bekannt)")
    if ctx.km_pro_jahr is not None:
        satz = (f"Durchschnittliche Fahrleistung seit dem Baujahr: rund "
                f"{_km(ctx.km_pro_jahr)} pro Jahr")
        if ctx.laufleistungs_einordnung:
            satz += f" — {_EINORDNUNG_TEXT[ctx.laufleistungs_einordnung]}"
        zeilen.append(satz + ".")
    for w in ctx.wartungshinweise:
        zeilen.append(f"Wartungspunkt „{w.bauteil}“ (Beleg {w.evidence_id}): {w.hinweis}")
    if not zeilen:
        return ""
    kopf = [
        "## Laufleistung und Wartung (deterministisch berechnet)",
        "Der Zeitpunkt des letzten Service ist NICHT bekannt — es existiert dazu "
        "keine Angabe, weder im Inserat noch in der Fahrzeugdatenbank. Schreibe "
        "deshalb NIEMALS, ein Service sei fällig, überfällig, versäumt oder nicht "
        "durchgeführt worden, und behaupte nie, die Servicehistorie fehle. Ein "
        "Wartungspunkt heißt ausschließlich: an dieser Stelle den NACHWEIS "
        "verlangen.",
        "Die durchschnittliche Fahrleistung ist ein Mittelwert über die gesamte "
        "Fahrzeuglebensdauer, keine gemessene Jahresleistung eines Vorbesitzers — "
        "und sie ist eine Einordnung, kein Qualitätsurteil.",
        "Leite aus der Laufleistung KEINE Preisaussage ab (nicht „deswegen "
        "günstig/teuer“, kein Abschlag, kein Marktwert). Der Preis wird "
        "ausschließlich im Marktvergleich behandelt.",
    ]
    return "\n".join([*kopf, *zeilen])
