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
gewechselt bekommen haben — ENFAL kann das nicht unterscheiden und behauptet es
deshalb nicht. Was ENFAL leisten kann und hier auch leistet: dem Käufer sagen,
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
from app.wartungsangabe import aus_request as wartungsangabe_aus_request
from app.servicehistorie import (
    NICHT_VORHANDEN as SH_NICHT_VORHANDEN, TEILWEISE as SH_TEILWEISE,
    UMFANG_UNKLAR as SH_UMFANG_UNKLAR, status as servicehistorie_status,
)

log = logging.getLogger(__name__)


# Audit-Nachtrag (P2-5-Folgeaudit): Dieses Modul enthielt hier zuvor
# `SCHWELLE_NIEDRIG`/`REFERENZ_DURCHSCHNITT` — zwei unbelegte Phase-2-Literale
# (10.000 / 15.000 km/Jahr, kein ADAC-/DAT-/KBA-Verweis), die ausschließlich
# app/key_findings.py::_positive_findings_kauf für ein "Unterdurchschnittliche
# Laufleistung"-Finding nutzte. Dieses Finding wurde entfernt (eine unbelegte
# Schwelle darf keine objektiv klingende Fahrzeugbewertung erzeugen) — womit
# beide Konstanten nirgends mehr gebraucht wurden und mit entfernt sind.
# km/Jahr bleibt reiner Zahlenkontext, siehe `Laufleistungskontext.km_pro_jahr`
# und `prompt_block` unten; es gibt keine Ersatzschwelle.

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


# N14-Fix (ENFAL-Reparaturpaket, RC-F-Lauf 07.11.2026): eine im selben
# Quelltext direkt an den km-Wert angehängte ZEITLICHE Alternative ("alle
# 90.000 km ODER 6 Jahre", real im Lexus-Serviceheft auch mit "/" statt
# "oder" formuliert: "Alle 90.000 km / 6 Jahre") ging bisher vollständig
# verloren -- weder in der Statusberechnung noch im sichtbaren Wortlaut
# (`_formuliere()` kannte nur `von_km`/`bis_km`). Das ist NICHT dasselbe wie
# die oben beschriebene reine Zeitangabe ("Alle 2 Jahre", ohne km-Anteil) --
# jene bleibt bewusst unverändert außerhalb jeder km-Rechnung. Hier geht es
# um den Fall, dass BEIDE Kriterien im selben Satz als Alternative stehen.
# Bewusst NUR Texterkennung, direkt an den (bereits von `parse_wartungspunkt`
# gewählten) ERSTEN km-Wert gebunden -- kein Treffer auf ein nachfolgendes
# "Danach alle X km / jährlich"-Folgeintervall (das trägt keine Zahl vor
# "jährlich" und matcht deshalb nicht). KEINE neue Fälligkeitsberechnung:
# der Erstzulassungsmonat ist nirgends erfasst (s. `fahrzeugalter()`), eine
# zeitliche Fälligkeit bleibt deshalb grundsätzlich unbestimmbar -- diese
# Funktion macht die Alternative nur SICHTBAR, statt sie stillschweigend zu
# verschlucken.
_ZEITALTERNATIVE = re.compile(
    rf"(?:{_ZAHL})\s*(?:tkm|km)\s*(?:oder|/)\s*(\d{{1,2}})\s*(jahre?|monate?)\b",
    re.IGNORECASE)


def zeit_anzeige(zahl: int, einheit: str) -> str:
    """Grammatisch korrekte Anzeige ("1 Jahr", "6 Jahre", "1 Monat", "18 Monate").
    Nur Darstellung: `parse_zeitalternative` und die Schwellenlogik bleiben unverändert."""
    if zahl == 1:
        einheit = {"Jahre": "Jahr", "Monate": "Monat"}.get(einheit, einheit)
    return f"{zahl} {einheit}"


def parse_zeitalternative(text: str | None) -> tuple[int, str] | None:
    """Zeitliche Alternative direkt neben dem ERSTEN km-Wert im Text -- oder
    None. Rückgabe `(zahl, "Jahre"|"Monate")`."""
    if not text:
        return None
    m = _ZEITALTERNATIVE.search(text)
    if not m:
        return None
    einheit = "Jahre" if m.group(2).lower().startswith("jahr") else "Monate"
    return int(m.group(1)), einheit


# N14-Fix, Fortsetzung (ENFAL-Reparaturpaket, 07.11.2026, Restlücke): die
# obige Teilfassung machte eine Zeitalternative nur SICHTBAR, wenn der
# km-Status bereits `naehert_sich`/`im_bereich`/`darueber` war. Bei
# `entfernt` (Kilometerstand weit unter dem Punkt) verschluckte
# `_wartungshinweise()` den gesamten Hinweis -- auch dann, wenn das
# Fahrzeug nach dem Baujahr längst älter ist als die genannte zeitliche
# Alternative. Ein 2017er Fahrzeug mit 20.000 km und einem Punkt
# "90.000 km / 6 Jahre" ist 2026 rechnerisch 9 Jahre alt: die 6-Jahres-
# Alternative ist dann unabhängig vom (niedrigen) Kilometerstand bereits
# erreichbar -- der km-Status allein darf das nicht verschlucken.
#
# Diese Funktion entscheidet NUR, ob die Zeitalternative trotz entferntem
# km-Status erwähnt werden MUSS (relevant oder ungeklärt), nicht ob sie
# ERREICHT ist -- dieselbe Fälligkeitsenthaltung wie im gesamten Modul.
# `alter_jahre` stammt aus dem Baujahr (`fahrzeugalter()`) und ist deshalb
# selbst nur eine Näherung ohne den Erstzulassungsmonat; ein zusätzliches
# Jahr Toleranz verhindert, dass ein Fahrzeug, das erst spät im
# Geburtsjahr zugelassen wurde, fälschlich als "noch nicht so alt"
# durchgeht. Ist das Baujahr gar nicht bekannt, lässt sich die
# Alternative ebenfalls nicht ausschließen -- auch dann gilt sie als
# ungeklärt, nicht als unauffällig.
def _zeitalternative_trotz_entfernt_moeglich(alter_jahre: int | None,
                                              zeitalternative: tuple[int, str]) -> bool:
    zahl, einheit = zeitalternative
    schwelle_jahre = zahl if einheit == "Jahre" else zahl / 12.0
    if alter_jahre is None:
        return True            # Baujahr unbekannt: nicht ausschliessbar
    return (alter_jahre + 1) >= schwelle_jahre


# ── Priorisierung der Servicehistorie-Prüfung (Wartungsrangfolge am Fünferlimit) ──────────────
#
# Ordnet NUR die Auswahl bei sonst gleichem Rang -- es entsteht keine Aussage über das Fahrzeug.
# Dieselben Parser wie oben (`parse_wartungspunkt`, `parse_zeitalternative`, `fahrzeugalter`), keine
# zweite Zahleninterpretation. `_zeitalternative_trotz_entfernt_moeglich` fragt "könnte die Zeitschwelle
# erreicht sein?" (alter + 1 >= Schwelle, vorsichtig AUFNEHMEN); hier wird die Gegenfrage "ist sie
# BELEGBAR erreicht?" gestellt (alter - 1 >= Schwelle): aus dem Baujahr allein folgt nur ein Altersintervall
# [alter-1, alter+1], belegbar ist nur, was in seinem ganzen Bereich gilt.
_FOLGEINTERVALL = re.compile(r"\bdanach\b", re.IGNORECASE)
# Bedingte Angabe: das Intervall gilt nur unter einer Voraussetzung (Anzeige/Hinweismeldung, Landesbezug,
# "bei Bedarf"). Daraus entsteht kein Vorrang -- ob die Bedingung am Fahrzeug eintrat, ist unbekannt.
_BEDINGT = re.compile(
    r"\b(?:wenn|falls|sofern|je\s+nach|abh(?:ä|ae)ngig|nach\s+bedarf|bei\s+bedarf)\b"
    r"|hinweismeldung|wartungsanzeige|serviceanzeige|service-?anzeige", re.IGNORECASE)


# Sätze, die `app/evidence.py::_web_beschreibung` HINTER die Quellenaussage hängt (Marktvorbehalt, unbestätigte
# Anwendbarkeit, ...): sie gehören nicht zum Intervall. Ohne diesen Schnitt würde das "falls für Ihren Markt
# zutreffend" des angehängten Marktvorbehalts jede Intervallangabe als "bedingt" erscheinen lassen.
_ANGEHAENGT = ("Die Quelle schränkt sich selbst ein", "Ob das für Ihr Fahrzeug in Deutschland",
               "Ob diese Bedingung auf dieses Fahrzeug", "Laut Quelle betrifft das")
# Übereinstimmend belegte Fakten (`beleglage == "uebereinstimmend_unbestaetigt"`) stehen als HÜLLE um die
# Quellenaussage: Präfix VOR dem wörtlichen Zitat, Bestätigungsstatus dahinter. Die Hülle ist kein Anhang (ein
# Schnitt an ihrem Präfix würde die ganze Aussage verwerfen), sie wird um das Zitat herum entfernt.
_UEBEREINSTIMMEND_PRAEFIX = "Die ausgewerteten Quellen nennen übereinstimmend: „"
_UEBEREINSTIMMEND_SUFFIX = "“ Für dieses Fahrzeug ist diese Angabe nicht ausreichend bestätigt."


def quellenaussage(text: str | None) -> str:
    """Die Intervallaussage der Quelle OHNE die von `app/evidence.py::_web_beschreibung` ergänzten Teile
    (Übereinstimmungs-Hülle, Marktvorbehalt, Teil-Geltung, unbestätigte technische Bedingung). Was die ENFAL-
    Auswertung zusätzlich anfügt, ist weder Erst- noch Folgeintervall und nie eine Bedingung des Intervalls."""
    t = text or ""
    if t.startswith(_UEBEREINSTIMMEND_PRAEFIX):
        t = t[len(_UEBEREINSTIMMEND_PRAEFIX):]
        i = t.find(_UEBEREINSTIMMEND_SUFFIX)
        if i >= 0:
            t = t[:i] + " " + t[i + len(_UEBEREINSTIMMEND_SUFFIX):]
    schnitt = [t.find(m) for m in _ANGEHAENGT if t.find(m) >= 0]
    return (t[:min(schnitt)] if schnitt else t).strip()


def hat_quellenvorbehalt(text: str | None) -> bool:
    """Trägt die Beschreibung einen Marktvorbehalt der Quelle (nicht bestätigte Geltung für Deutschland)?"""
    t = text or ""
    return "Die Quelle schränkt sich selbst ein" in t or "Ob das für Ihr Fahrzeug in Deutschland" in t


def erstintervall_text(text: str | None) -> str:
    """Nur der Teil VOR einem Folgeintervall ("Danach alle 15.000 km / jährlich") und ohne angefügte
    Marktvorbehalt-/Anwendbarkeitssätze: Erst- und Folgeintervall bleiben getrennt, das Folgeintervall wird
    hier nie gelesen, ein Marktvorbehalt macht das Intervall selbst nicht "bedingt"."""
    return _FOLGEINTERVALL.split(quellenaussage(text), maxsplit=1)[0]


def zeitalternative_belegbar_erreicht(alter_jahre: int | None, zeitalternative: tuple[int, str]) -> bool:
    zahl, einheit = zeitalternative
    schwelle_jahre = zahl if einheit == "Jahre" else zahl / 12.0
    if alter_jahre is None:
        return False           # Baujahr unbekannt: kein Vorrang
    return (alter_jahre - 1) >= schwelle_jahre


# Zeitschwelle für die RANGFOLGE: kennt zusätzlich die reine Zeitangabe ("alle 6 Jahre") und die umgekehrte
# Schreibweise ("6 Jahre oder 105.000 km"). `parse_zeitalternative` (Anzeige/Hinweistexte) bleibt bewusst
# UNVERÄNDERT -- eine Erweiterung dort würde sichtbare Wartungstexte und die "entfernt"-Prüfung des
# Laufleistungskontexts ändern. Kilometerwerte kommen weiterhin ausschließlich aus `parse_wartungspunkt`.
_ZEIT_ZAHL = re.compile(r"(?<![0-9.,])(\d{1,3})\s*(jahre?n?|monate?n?)\b", re.IGNORECASE)
_VERBINDER_FUELL = re.compile(r"\b(?:alle|alle[nr]?|spätestens|nach|jeweils|zumindest|mindestens|ca\.?|etwa)\b",
                              re.IGNORECASE)
_VERBINDER_ODER = re.compile(r"^(?:oder|/|bzw\.?|beziehungsweise)$", re.IGNORECASE)
_VERBINDER_UND = re.compile(r"^(?:und|sowie|\+|&|,\s*und)$", re.IGNORECASE)


def _km_spanne(text: str) -> tuple[int, int] | None:
    """Position des ersten Kilometerwerts -- dieselben Muster wie `parse_wartungspunkt`."""
    m = _SPANNE.search(text) or _EINZELN.search(text)
    return (m.start(), m.end()) if m else None


def parse_zeitschwelle(text: str | None) -> tuple[int, str] | None:
    """Erste ausdrücklich genannte Zeitangabe `(zahl, "Jahre"|"Monate")` -- oder None. Nur für die Rangfolge."""
    m = _ZEIT_ZAHL.search(text or "")
    if not m:
        return None
    return int(m.group(1)), ("Jahre" if m.group(2).lower().startswith("jahr") else "Monate")


def schwelle_belegbar_erreicht(text: str | None, kilometerstand: int | None, baujahr: int | None,
                               *, heute_jahr: int | None = None) -> bool:
    """True nur, wenn die Schwelle des ERSTintervalls belegbar erreicht ist.

    Schwellen: Kilometer (erster Wartungspunkt, `parse_wartungspunkt`) und/oder Zeit (`parse_zeitschwelle`,
    sicher erreicht = ganzes mögliches Altersintervall, alter-1 >= Schwelle).
      * nur Kilometer / nur Zeit ("alle 6 Jahre"): diese eine Schwelle entscheidet;
      * beide mit ODER ("km oder Jahre", "Jahre oder km", "km / Jahre"): eine sicher erreichte genügt, die
        unbekannte andere Seite hebt sie nicht auf;
      * beide mit UND ("km und Jahre"): beide müssen sicher erreicht sein -- eine unbekannte Seite = kein
        Vorrang (UND wird nie als ODER gelesen);
      * beide ohne erkennbaren Verbinder: mehrdeutig -> kein Vorrang.
    Bedingte Angaben ("wenn", "falls", Hinweismeldung, ...) und alles ab einem Folgeintervall ("Danach")
    zählen nicht. Behauptet keine Fälligkeit und keine unterlassene Wartung: gemeint ist nur "hier lohnt der
    Nachweis der Servicehistorie"."""
    return schwelle_belegbar_erreicht_alter(text, kilometerstand, fahrzeugalter(baujahr, heute_jahr=heute_jahr))


def schwelle_belegbar_erreicht_alter(text: str | None, kilometerstand: int | None,
                                     alter_jahre: int | None) -> bool:
    """Wie `schwelle_belegbar_erreicht`, aber mit bereits berechnetem Fahrzeugalter (`fahrzeugalter()`): die
    GEMEINSAME Schwellenlogik für Auswahl-Rang und Aktionsrang."""
    erst = erstintervall_text(text)
    if not erst or _BEDINGT.search(erst):
        return False
    punkt = parse_wartungspunkt(erst)
    zeit = parse_zeitschwelle(erst)
    km_ok = bool(punkt and kilometerstand and kilometerstand > 0 and kilometerstand >= punkt[0])
    zeit_ok = bool(zeit and zeitalternative_belegbar_erreicht(alter_jahre, zeit))
    if punkt and not zeit:
        return km_ok
    if zeit and not punkt:
        return zeit_ok
    if not punkt and not zeit:
        return False
    # beide Schwellen genannt: Verbinder zwischen den beiden Angaben bestimmt ODER/UND
    km_pos = _km_spanne(erst)
    zm = _ZEIT_ZAHL.search(erst)
    if not km_pos or not zm:
        return False
    if km_pos[0] < zm.start():
        zwischen = erst[km_pos[1]:zm.start()]
    else:
        zwischen = erst[zm.end():km_pos[0]]
    verbinder = _VERBINDER_FUELL.sub(" ", zwischen).strip(" ,;:()")
    if _VERBINDER_ODER.match(verbinder):
        return km_ok or zeit_ok
    if _VERBINDER_UND.match(verbinder):
        return km_ok and zeit_ok
    return False


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
                von_km: int, bis_km: int | None, herkunft: str,
                zeitalternative: tuple[int, str] | None = None,
                zeit_offen_trotz_entfernt: bool = False,
                zeit_erreicht_ab_jahre: int | None = None) -> str:
    """Der ausformulierte Hinweis.

    Jede Variante ist bewusst so gebaut, dass sie eine ZUSTANDSFRAGE stellt statt
    einer Zustandsbehauptung. Verboten und in keinem dieser Texte enthalten:
    "fällig", "überfällig", "versäumt", "nicht durchgeführt", "fehlt".

    N14-Fix (ENFAL-Reparaturpaket, RC-F-Lauf 07.11.2026): `zeitalternative`
    (s. `parse_zeitalternative()`) macht eine im Quelltext mitgenannte
    zeitliche Alternative ("oder 6 Jahre") sichtbar, OHNE eine zeitliche
    Fälligkeit zu berechnen (Erstzulassungsmonat nirgends erfasst, s.
    `fahrzeugalter()`) -- der km-Status bleibt ausdrücklich NUR ein
    Kilometerstatus, die zeitliche Frage bleibt ausdrücklich offen genannt.
    Kein Rückschluss aus Baujahr/Kilometerstand/Marktvorbehalt.

    N14-Fix, Fortsetzung: `zeit_offen_trotz_entfernt` (s.
    `_zeitalternative_trotz_entfernt_moeglich()`) behandelt den Fall, dass der
    km-Status selbst `entfernt` ist, die zeitliche Alternative aber trotzdem
    relevant oder ungeklärt bleibt -- hier bekommt der Hinweis einen eigenen
    Wortlaut, der ausdrücklich sagt, dass der Kilometeranteil NICHT einschlägig
    ist, statt (wie in den anderen Zweigen) einen Kilometerstatus zu behaupten,
    der hier gar nicht vorliegt.
    """
    punkt = _punkt_text(von_km, bis_km)
    zusatz = _HERKUNFT_ZUSATZ.get(herkunft, "")
    # F3: eine SEPARAT belegte Zeitschwelle (`zeitalternative_belegbar_erreicht`: im ganzen möglichen
    # Altersintervall überschritten, nicht bedingt) ändert den Zeittext. Ein erreichter Kilometerpunkt beweist
    # nichts über die Zeit und kommt hier nie hinein. Die Arbeit selbst bleibt unbelegt: keine Fälligkeit,
    # kein Versäumnis.
    zeit_erreicht_text = ""
    if zeitalternative and zeit_erreicht_ab_jahre is not None:
        zahl, einheit = zeitalternative
        zeit_erreicht_text = (
            f"die zeitliche Alternative (oder {zeit_anzeige(zahl, einheit)}) ist nach dem Baujahr bereits "
            f"erreicht (Fahrzeugalter mindestens {zeit_erreicht_ab_jahre} Jahre; der genaue Erstzulassungsmonat "
            f"ist unbekannt). Ob die Arbeit seitdem gemacht wurde, geht aus den vorliegenden Daten nicht hervor.")
    if zeit_offen_trotz_entfernt and zeitalternative and zeit_erreicht_text:
        zahl, einheit = zeitalternative
        return (f"Für „{bauteil}“ ist neben dem Kilometerpunkt ({punkt}) zusätzlich eine zeitliche Alternative "
                f"hinterlegt. Der aktuelle Kilometerstand liegt weit von {punkt} entfernt -- der "
                f"Kilometeranteil dieses Wartungspunkts ist damit nicht einschlägig; "
                f"{zeit_erreicht_text} Vor dem Kauf klären, wann diese Arbeit zuletzt gemacht wurde, und den "
                f"Beleg dazu ansehen.") + zusatz
    if zeit_offen_trotz_entfernt and zeitalternative:
        zahl, einheit = zeitalternative
        kern = (
            f"Für „{bauteil}“ ist neben dem Kilometerpunkt ({punkt}) zusätzlich eine "
            f"zeitliche Alternative hinterlegt (oder {zeit_anzeige(zahl, einheit)}). Der aktuelle "
            f"Kilometerstand liegt weit von {punkt} entfernt -- der Kilometeranteil "
            f"dieses Wartungspunkts ist damit nicht einschlägig. Ohne den genauen "
            f"Erstzulassungsmonat und ohne bekannte Servicehistorie lässt sich aber "
            f"nicht ausschließen, dass die zeitliche Alternative bereits erreicht ist. "
            f"Vor dem Kauf klären, wann diese Arbeit zuletzt gemacht wurde, und den "
            f"Beleg dazu ansehen.")
        return kern + zusatz
    zeit_zusatz = ""
    if zeitalternative and zeit_erreicht_text:
        zeit_zusatz = (f" Der oben genannte Stand bezieht sich ausschließlich auf den Kilometerstand; "
                       f"{zeit_erreicht_text}")
    elif zeitalternative:
        zahl, einheit = zeitalternative
        zeit_zusatz = (
            f" Die Quelle nennt für diesen Wartungspunkt zusätzlich eine zeitliche "
            f"Alternative (oder {zeit_anzeige(zahl, einheit)}): der oben genannte Stand bezieht "
            f"sich ausschließlich auf den Kilometerstand -- ob die zeitliche "
            f"Alternative bereits erreicht ist, lässt sich ohne den genauen "
            f"Erstzulassungsmonat nicht bestimmen und bleibt deshalb offen.")
    if status == STATUS_NAEHERT_SICH:
        rest = _km(von_km - kilometerstand)
        kern = (f"Für „{bauteil}“ ist ein Wartungspunkt bei {punkt} hinterlegt: rund "
                f"{rest} voraus. Vor dem Kauf klären, wann diese Arbeit zuletzt "
                f"gemacht wurde, und den Beleg dazu ansehen.")
    elif status == STATUS_IM_BEREICH:
        kern = (f"Bei dieser Laufleistung liegt „{bauteil}“ im relevanten Bereich des "
                f"hinterlegten Wartungspunkts ({punkt}). Nachweis über die Durchführung "
                f"zeigen lassen: ob die Arbeit bereits erledigt ist, geht aus den "
                f"vorliegenden Daten nicht hervor.")
    elif status == STATUS_DARUEBER:
        # ENFAL-Produktionsbefund (01.11.2026, L2): die vorige Fassung rechnete
        # `kilometerstand - von_km` aus und nannte das Ergebnis "liegt rund X km
        # zurück" -- bei einem WIEDERKEHRENDEN Intervall (kein `bis_km`, z.B.
        # "alle 15.000 km") ist das keine zulässige Rückstandsrechnung: ohne
        # bekannten letzten Servicezeitpunkt weiß niemand, wie oft das Intervall
        # seither bereits durchlaufen wurde. Bei hoher Laufleistung wird die Zahl
        # beliebig groß und suggeriert einen konkreten, großen Rückstand, den die
        # Daten nicht stützen -- exakt die Fälligkeitsbehauptung, die dieser
        # Funktion laut Docstring verboten ist. KEINE Ersatzrechnung (auch eine
        # Modulo-Rechnung kennt den letzten Service nicht) -- das Intervall bleibt
        # genannt, der Rückstand bleibt unberechnet.
        #
        # N5-Fix (ENFAL-Reparaturpaket, 2026-11): die Zahl war entfernt, aber
        # "liegt ... bereits hinter dem Fahrzeug" benennt grammatisch weiterhin
        # EINEN einzelnen, bereits vergangenen Termin -- bei einem wiederkehrenden
        # Intervall (z.B. alle 15.000 km, hier 82.000 km Laufleistung) wäre der
        # Punkt rechnerisch längst mehrfach erreicht worden, nicht einmalig
        # "vorbei". "mindestens einmal erreicht" benennt nur die Tatsache (das
        # Intervall liegt im/unter dem aktuellen Kilometerstand), ohne eine
        # Anzahl, ein Datum oder eine Fälligkeit zu suggerieren -- weiterhin
        # KEINE Ersatzrechnung, kein "fällig"/"überfällig"/"versäumt".
        kern = (f"Der hinterlegte Wartungspunkt für „{bauteil}“ ({punkt}) wurde bei "
                f"dieser Laufleistung rein rechnerisch mindestens einmal erreicht. Ohne "
                f"bekannten Bezug zur tatsächlichen Servicehistorie lässt sich daraus "
                f"weder eine Anzahl noch ein Rückstand in Kilometern ableiten. Nachweis "
                f"über die Durchführung zeigen lassen: ob die Arbeit bereits erledigt "
                f"ist, geht aus den vorliegenden Daten nicht hervor.")
    else:
        return ""
    return kern + zeit_zusatz + zusatz


# ── Evidence -> Wartungshinweise ──────────────────────────────────────────────

# Aus welcher Insight-Kategorie ein Hinweis entstehen darf, und wie die Herkunft
# im Ergebnis heißt. `wartung_oel_km` steht hier bewusst NICHT (Stufe C).
_QUELLEN_KATEGORIEN = {"wartung": "db_wartung", "web_wartung": "web_wartung"}

# Aus dem DB-Wartungs-Insight den reinen Intervallteil zurückgewinnen.
# `build_insights` setzt ihn wörtlich so zusammen (app/evidence.py).
_INTERVALL_SATZ = re.compile(r"Vorgesehenes Intervall:\s*(?P<wert>.+?)\.\s*$")

_WARTUNG_TITEL = re.compile(r"^(?P<bauteil>.+?)(?:\s+—\s+|:\s+)")


def _bauteil(i: Insight) -> str:
    """Bauteil eines Wartungs-Insights.

    `quellen[0].ref` trägt es bei DB-Insights direkt (von `build_insights`
    gesetzt); bei Web-Insights steht es vor dem Gedankenstrich im Titel.
    """
    for q in i.quellen:
        if q.typ == "motorvarianten" and q.ref:
            return q.ref.strip()
    # F4: bei Web-Insights ist das STRUKTURIERTE Bauteil der Schlüssel; der Titel kann eine Präsenz-Bedingung
    # davor tragen ("Falls Automatikgetriebeöl verbaut: ...", app/evidence.py::wende_praesenz_an).
    if i.kategorie == "web_wartung" and (getattr(i, "bauteil", None) or "").strip():
        return _anzeigeform(i.bauteil.strip())
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


def _wartungshinweise(insights: list[Insight], kilometerstand: int,
                      alter_jahre: int | None = None) -> list[Wartungshinweis]:
    """Wartungshinweise aus vorhandener Evidence — nie aus dem Nichts.

    Deduplizierung über das Bauteil: nennt sowohl die Fahrzeugdatenbank als auch
    die Webrecherche denselben Wartungspunkt, gewinnt die geprüfte DB-Angabe
    (sie wird zuerst eingetragen). Dieselbe Regel wie in den Kaufaktionen.

    Sortierung: der bereits passierte Punkt zuerst, dann der erreichte, dann der
    nahende, zuletzt (falls vorhanden) der km-seitig entfernte Punkt mit
    trotzdem relevanter/ungeklärter Zeitalternative — innerhalb einer Stufe der
    mit dem größten Abstand. Bei Gleichstand nach Bauteilname, damit die
    Ausgabe deterministisch bleibt.

    `alter_jahre` (N14-Fix, Fortsetzung): einzig dafür gebraucht, einen km-seitig
    `entfernten` Punkt NICHT zu verschlucken, wenn dieselbe Quelle eine
    zeitliche Alternative nennt, die das Fahrzeug nach dem Baujahr bereits
    erreicht haben könnte — s. `_zeitalternative_trotz_entfernt_moeglich()`.
    """
    gesehen: set[str] = set()
    out: list[Wartungshinweis] = []
    # Root-Cause-Closing: ein Wartungseintrag kann in eine Schwachstellen-Aussage
    # zum selben Bauteil zusammengeführt sein (app/risikothemen.py). Sein
    # Intervall zählt trotzdem; als Beleg steht die ID der sichtbaren,
    # zusammengeführten Aussage, denn nur sie existiert in `insights`.
    kandidaten = [(i, i.id) for i in insights]
    kandidaten += [(n, i.id) for i in insights for n in getattr(i, "nebenbelege", None) or []]
    for i, beleg_id in kandidaten:
        herkunft = _QUELLEN_KATEGORIEN.get(i.kategorie)
        if herkunft is None:
            continue
        roh = _intervall_text(i, herkunft)
        punkt = parse_wartungspunkt(roh)
        if punkt is None:
            continue           # zeit-/zustandsbezogen: keine Kilometerrechnung
        von_km, bis_km = punkt
        status = status_zu_punkt(kilometerstand, von_km, bis_km)
        # N14-Fix: s. `parse_zeitalternative()`-Docstring -- eine im selben
        # Text genannte Zeitalternative wird sichtbar gemacht, nicht berechnet.
        zeitalternative = parse_zeitalternative(roh)
        zeit_offen_trotz_entfernt = (
            status == STATUS_ENTFERNT and zeitalternative is not None
            and _zeitalternative_trotz_entfernt_moeglich(alter_jahre, zeitalternative)
        )
        # F3: Zeitschwelle SEPARAT belegt (nicht bedingt, im ganzen Altersintervall überschritten).
        zeit_erreicht_ab = None
        if (zeitalternative is not None and alter_jahre is not None
                and not _BEDINGT.search(erstintervall_text(roh))
                and zeitalternative_belegbar_erreicht(alter_jahre, zeitalternative)):
            zeit_erreicht_ab = alter_jahre - 1
        if status == STATUS_ENTFERNT and not zeit_offen_trotz_entfernt:
            continue           # §G: kein Anlass, also keine Warnung
        bauteil = _bauteil(i)
        schluessel = norm_bauteil(bauteil)
        if schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        # F4: die Präsenz-Bedingung des Titels ("Falls ... verbaut") bleibt in der Ausgabe erhalten, ist aber
        # nicht Teil des Schlüssels.
        bedingung_praefix = ""
        if (i.titel or "").startswith("Falls ") and ": " in i.titel:
            bedingung_praefix = i.titel.split(": ", 1)[0] + ": "
        out.append(Wartungshinweis(
            bauteil=bauteil,
            status=status,
            punkt_km=von_km,
            punkt_bis_km=bis_km,
            differenz_km=kilometerstand - von_km,
            intervall_text=(roh or "").strip(),
            hinweis=bedingung_praefix + _formuliere(bauteil, status, kilometerstand, von_km, bis_km, herkunft,
                                                    zeitalternative,
                                                    zeit_offen_trotz_entfernt=zeit_offen_trotz_entfernt,
                                                    zeit_erreicht_ab_jahre=zeit_erreicht_ab)
            # F3: der Marktvorbehalt der Quelle macht das Intervall nicht "bedingt" (s. `erstintervall_text`),
            # bleibt aber SICHTBAR: erreichte Schwelle und bestätigte Geltung für Deutschland sind getrennte Fragen.
            + (" Die Quelle schränkt die Angabe selbst ein; ob sie für dieses Fahrzeug in Deutschland gilt, ist "
               "nicht bestätigt." if hat_quellenvorbehalt(roh) else ""),
            herkunft=herkunft,
            evidence_id=beleg_id,
            quellen=[EvidenceQuelle(**q.model_dump()) for q in i.quellen],
            # N12/N13-Fix (ENFAL-Testabsicherung, 2026-11): unverändert von der
            # Insight übernommen -- s. Insight-Felder gleichen Namens.
            taetigkeit=getattr(i, "taetigkeit", None),
            technische_bedingung=getattr(i, "technische_bedingung", None),
            technische_bedingung_status=getattr(i, "technische_bedingung_status", None),
        ))

    # STATUS_ENTFERNT taucht hier nur noch in genau einem Fall auf: km-seitig
    # entfernt, aber mit relevanter/ungeklärter Zeitalternative (s. oben) --
    # rangiert bewusst zuletzt, denn der km-Anteil ist nachweislich nicht
    # einschlägig und nur die Zeitfrage bleibt offen.
    rang = {STATUS_DARUEBER: 3, STATUS_IM_BEREICH: 2, STATUS_NAEHERT_SICH: 1, STATUS_ENTFERNT: 0}
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
    hinweise = _wartungshinweise(insights or [], km, alter) if km else []

    ctx = Laufleistungskontext(
        kilometerstand=km,
        fahrzeugalter_jahre=alter,
        km_pro_jahr=pro_jahr,
        wartungshinweise=hinweise,
        # Unveränderlich False, solange es keine Datenquelle für den letzten
        # Service gibt — siehe Modulkopf. Kein Platzhalter, sondern ein Befund.
        letzter_service_bekannt=False,
        # Die Angabe des Inserats zur Servicehistorie — sie ersetzt kein
        # Servicedatum, verschiebt aber die Grenze des Sagbaren (siehe
        # `prompt_block`).
        servicehistorie=servicehistorie_status(req),
        # Nennt das Inserat eine konkrete letzte Wartung, ist das die einzige
        # Zahl zum Wartungsstand, die es überhaupt gibt. Sie bleibt eine Angabe.
        letzte_wartung_angabe=(lambda w: w.anzeige() if w else None)(
            wartungsangabe_aus_request(req)),
    )
    return ctx if ctx.hat_inhalt() else None


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
        # Bewusst OHNE Einordnung ("niedrig"/"durchschnittlich"/"erhöht"): dafür
        # existiert im Projekt keine belastbare fachliche Schwellenbasis — siehe
        # den Kommentar bei SCHWELLE_NIEDRIG/REFERENZ_DURCHSCHNITT oben. Die
        # Zeile bleibt eine reine Zahl mit Einordnung als Durchschnittswert,
        # kein Urteil.
        zeilen.append(f"Durchschnittliche Fahrleistung seit dem Baujahr: rund "
                      f"{_km(ctx.km_pro_jahr)} pro Jahr (grober Durchschnitt seit "
                      f"dem Baujahr).")
    for w in ctx.wartungshinweise:
        zeilen.append(f"Wartungspunkt „{w.bauteil}“ (Beleg {w.evidence_id}): {w.hinweis}")
    if not zeilen:
        return ""
    # Die Servicehistorie-Angabe verschiebt genau EINEN Satz dieses Blocks. Ohne sie
    # gilt weiterhin das pauschale Verbot, ein Fehlen der Servicehistorie zu
    # behaupten. Gibt das Inserat AUSDRÜCKLICH an, dass keine Historie vorliegt,
    # wäre dieses Verbot falsch: es würde eine belegte Angabe des Inserats
    # unterdrücken. Erlaubt wird deshalb genau die Wiedergabe DIESER Angabe — die
    # Fälligkeits-Verbote bleiben in jedem Fall unverändert bestehen.
    #
    # LIVE-RUN-BEFUND: nennt das Inserat eine konkrete letzte Wartung, war der
    # pauschale Satz "Der Zeitpunkt des letzten Service ist NICHT bekannt"
    # schlicht falsch — der Nutzer hatte ihn gerade eingegeben. Diese Angabe
    # steht deshalb ganz vorn und hat Vorrang vor den Servicehistorie-Varianten.
    # Ein GEPRÜFTES Servicedatum ist sie trotzdem nicht: die Fälligkeits-Verbote
    # bleiben Wort für Wort bestehen.
    if ctx.letzte_wartung_angabe:
        service_satz = (
            f"Das Inserat gibt eine letzte Wartung {ctx.letzte_wartung_angabe} an. Das "
            f"ist eine ANGABE des Inserats, kein geprüfter Nachweis: gib sie als Angabe "
            f"wieder und frage nach dem Beleg, statt zu fragen, wann die letzte Wartung "
            f"war. Ein Servicedatum, das über diese Angabe hinausgeht, existiert nicht. "
            f"Schreibe deshalb weiterhin NIEMALS, ein Service sei fällig, überfällig, "
            f"versäumt oder nicht durchgeführt worden. Ein Wartungspunkt heißt "
            f"ausschließlich: an dieser Stelle den NACHWEIS verlangen.")
    elif ctx.servicehistorie == SH_NICHT_VORHANDEN:
        service_satz = (
            "Der Zeitpunkt des letzten Service ist NICHT bekannt. Das Inserat gibt "
            "allerdings an, dass keine Servicehistorie vorliegt: diese ANGABE darfst "
            "du wiedergeben und als Unsicherheit in der Wartungsbewertung benennen, "
            "nicht als geprüften Befund und nicht als festgestellten Mangel. Schreibe "
            "trotzdem NIEMALS, ein Service sei fällig, überfällig oder versäumt: ohne "
            "Servicedatum ist das durch nichts gedeckt. Ein Wartungspunkt heißt "
            "ausschließlich: an dieser Stelle den NACHWEIS verlangen.")
    elif ctx.servicehistorie in (SH_TEILWEISE, SH_UMFANG_UNKLAR):
        service_satz = (
            "Der Zeitpunkt des letzten Service ist NICHT bekannt: das Inserat nennt "
            "zur Servicehistorie nur einen Umfang, kein Datum und keinen "
            "Kilometerstand. Schreibe deshalb NIEMALS, ein Service sei fällig, "
            "überfällig, versäumt oder nicht durchgeführt worden. Ein Wartungspunkt "
            "heißt ausschließlich: an dieser Stelle den NACHWEIS verlangen.")
    else:
        service_satz = (
            "Der Zeitpunkt des letzten Service ist NICHT bekannt: es existiert dazu "
            "keine Angabe, weder im Inserat noch in der Fahrzeugdatenbank. Schreibe "
            "deshalb NIEMALS, ein Service sei fällig, überfällig, versäumt oder nicht "
            "durchgeführt worden, und behaupte nie, die Servicehistorie fehle. Ein "
            "Wartungspunkt heißt ausschließlich: an dieser Stelle den NACHWEIS "
            "verlangen.")
    kopf = [
        "## Laufleistung und Wartung (deterministisch berechnet)",
        service_satz,
        "Die durchschnittliche Fahrleistung ist ein Mittelwert über die gesamte "
        "Fahrzeuglebensdauer, keine gemessene Jahresleistung eines Vorbesitzers. "
        "Bewerte sie NICHT als gut oder schlecht: nenne nur die Zahl, es gibt "
        "dafür keine belastbare Vergleichsschwelle.",
        "Leite aus der Laufleistung KEINE Preisaussage ab (nicht „deswegen "
        "günstig/teuer“, kein Abschlag, kein Marktwert). Der Preis wird "
        "ausschließlich im Marktvergleich behandelt.",
    ]
    return "\n".join([*kopf, *zeilen])
