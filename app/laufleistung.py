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
from app.risikothemen import DATENSATZ_MARKER, SCHLUSSSATZ_KEINE_HERSTELLERVORGABE, SCHLUSSSATZ_UNEINHEITLICH
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
    r"|hinweismeldung|wartungsanzeige|serviceanzeige|service-?anzeige"
    # Erschwerte Einsatzbedingungen sind eine EIGENE, bedingte Regel (die normalen sind ein Geltungsbereich).
    r"|erschwert\w*\s+(?:einsatz|betriebs)bedingungen|unter\s+erschwerten", re.IGNORECASE)


# Sätze, die `app/evidence.py::_web_beschreibung` HINTER die Quellenaussage hängt (Marktvorbehalt, unbestätigte
# Anwendbarkeit, ...): sie gehören nicht zum Intervall. Ohne diesen Schnitt würde das "falls für Ihren Markt
# zutreffend" des angehängten Marktvorbehalts jede Intervallangabe als "bedingt" erscheinen lassen.
_ANGEHAENGT = ("Die Quelle schränkt sich selbst ein", "Ob das für Ihr Fahrzeug in Deutschland",
               "Ob diese Bedingung auf dieses Fahrzeug", "Laut Quelle betrifft das")
# Übereinstimmend belegte Fakten (`beleglage == "uebereinstimmend_unbestaetigt"`) stehen als HÜLLE um die
# Quellenaussage: Präfix VOR dem wörtlichen Zitat, Bestätigungsstatus dahinter. Die Hülle ist kein Anhang (ein
# Schnitt an ihrem Präfix würde die ganze Aussage verwerfen), sie wird um das Zitat herum entfernt.
# Trennmarker, mit dem der Erzeuger (app/technical_research.py::_wartung_gleiche_arbeit_zusammenfuehren) zwei
# Fundstellen DESSELBEN Dokuments in einen Aussagetext legt. Hier definiert (nicht dort), weil dieses Modul leicht
# ist und der Erzeuger es ohnehin importiert: so zieht eine gemeinsame Textkonstante keinen schweren Import nach.
WEITERE_STELLE_MARKER = "Weitere Stelle im selben Dokument:"
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


# Vom Erzeuger angehängte Zeile je Fundstelle ("Geltungsbereich laut Quelle: Für normale Einsatzbedingungen."): eine
# Annotation zum Einsatzbereich, nicht Teil der Intervallaussage (wie der Marktvorbehalt).
_GELTUNGSZEILE = re.compile(r"\.?\s*Geltungsbereich laut Quelle:[^.]*\.")   # samt dem vom Erzeuger davor gesetzten Punkt


def erstintervall_text(text: str | None) -> str:
    """Nur der Teil VOR einem Folgeintervall ("Danach alle 15.000 km / jährlich") und ohne angefügte
    Marktvorbehalt-/Anwendbarkeitssätze: Erst- und Folgeintervall bleiben getrennt, das Folgeintervall wird
    hier nie gelesen, ein Marktvorbehalt macht das Intervall selbst nicht "bedingt"."""
    return _GELTUNGSZEILE.sub("", _FOLGEINTERVALL.split(quellenaussage(text), maxsplit=1)[0])


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


_ZEIT_JAEHRLICH = re.compile(
    r"(?<![\w-])(?:(?:einmal|1\s*x|1\s*mal)\s+(?:pro\s+jahr|im\s+jahr|j[äa]hrlich)|j[äa]hrlich|jedes\s+jahr)(?![\w-])",
    re.IGNORECASE)
# Einsatzbedingungen: "normale" Einsatzbedingungen sind ein GELTUNGSBEREICH der Angabe, "erschwerte" eine eigene,
# bedingte Regel (`_BEDINGT`). Beides ist weder ein Marktvorbehalt noch eine technische Ausstattungsbedingung.
_EINSATZ_NORMAL = re.compile(r"normale[nr]?\s+(?:einsatz|betriebs)bedingungen", re.IGNORECASE)
_EINSATZ_ERSCHWERT = re.compile(r"erschwert\w*\s+(?:einsatz|betriebs)bedingungen|unter\s+erschwerten", re.IGNORECASE)


def _zeit_treffer(text: str | None) -> tuple[int, str, int, int] | None:
    """Erste ausdrücklich genannte Zeitangabe `(zahl, "Jahre"|"Monate", start, ende)`: Ziffernform ("6 Jahre",
    "12 Monate") ODER "jährlich"/"einmal jährlich"/"jedes Jahr" (= 1 Jahr). Nur der frühere Treffer zählt."""
    t = text or ""
    kandidaten: list[tuple[int, int, int, str]] = []
    m = _ZEIT_ZAHL.search(t)
    if m:
        kandidaten.append((m.start(), m.end(), int(m.group(1)),
                           "Jahre" if m.group(2).lower().startswith("jahr") else "Monate"))
    j = _ZEIT_JAEHRLICH.search(t)
    if j:
        kandidaten.append((j.start(), j.end(), 1, "Jahre"))
    if not kandidaten:
        return None
    start, ende, zahl, einheit = min(kandidaten)
    return zahl, einheit, start, ende


def parse_zeitschwelle(text: str | None) -> tuple[int, str] | None:
    """Erste ausdrücklich genannte Zeitangabe `(zahl, "Jahre"|"Monate")` -- oder None. Nur für die Rangfolge
    und die Regelauswertung; gelesen wird immer nur das ERSTintervall (`erstintervall_text`)."""
    z = _zeit_treffer(text)
    return (z[0], z[1]) if z else None


def _zeit_klasse(zeit: tuple[int, str], alter_jahre: int | None) -> str:
    """Lage der Zeitschwelle zum Fahrzeugalter. Aus dem Baujahr allein folgt nur ein Altersintervall
    [alter-1, alter+1]: `erreicht` gilt im ganzen Intervall (alter-1 >= Schwelle), `band` liegt mitten darin
    (erst hier ist der Erstzulassungsmonat entscheidend), `nicht_erreicht` gilt auch im ältesten Fall nicht,
    `baujahr_unbekannt` ohne Baujahr."""
    zahl, einheit = zeit
    schwelle = zahl if einheit == "Jahre" else zahl / 12.0
    if alter_jahre is None:
        return "baujahr_unbekannt"
    if alter_jahre - 1 >= schwelle:
        return "erreicht"
    if alter_jahre + 1 >= schwelle:
        return "band"
    return "nicht_erreicht"


class WartungsRegel:
    """EINE Fundstelle einer Wartungsaussage mit ihrer eigenen Bedingung und ihrem ERSTintervall.

    Kilometer, Zeit und Bedingung gehören zur selben Fundstelle und werden nie über Fundstellen hinweg
    gemischt. `bedingt`: eine Voraussetzung (Anzeige/Hinweismeldung, Landesbezug, "falls", erschwerte
    Einsatzbedingungen) ist an DIESE Fundstelle geknüpft -- das ist weder der Marktvorbehalt des Dokuments
    (`hat_quellenvorbehalt`) noch die technische Ausstattungsbedingung des Fakts (`technische_bedingung`)."""
    __slots__ = ("text", "erst", "bedingt", "einsatz", "km", "zeit", "verbinder")

    def __init__(self, text: str):
        self.text = text
        self.erst = _GELTUNGSZEILE.sub("", _FOLGEINTERVALL.split(text, maxsplit=1)[0]).strip()
        # Einsatzbereich: aus dem GANZEN Fundstellentext (die vom Erzeuger angehängte Zeile "Geltungsbereich laut
        # Quelle: Für normale Einsatzbedingungen." steht hinter einem eventuellen "Danach ..."). Nennt eine Fundstelle
        # BEIDE Bereiche, ist sie nicht eindeutig zuzuordnen und gilt als bedingt ("gemischt").
        normal, erschwert = bool(_EINSATZ_NORMAL.search(text)), bool(_EINSATZ_ERSCHWERT.search(text))
        self.einsatz = ("gemischt" if normal and erschwert else "erschwert" if erschwert
                        else "normal" if normal else None)
        self.bedingt = bool(_BEDINGT.search(self.erst)) or self.einsatz in ("erschwert", "gemischt")
        self.km = parse_wartungspunkt(self.erst)
        z = _zeit_treffer(self.erst)
        self.zeit = (z[0], z[1]) if z else None
        self.verbinder = None
        if self.km and z:
            pos = _km_spanne(self.erst)
            if pos:
                zwischen = self.erst[pos[1]:z[2]] if pos[0] < z[2] else self.erst[z[3]:pos[0]]
                v = _VERBINDER_FUELL.sub(" ", zwischen).strip(" ,;:()")
                self.verbinder = ("oder" if _VERBINDER_ODER.match(v) else
                                  "und" if _VERBINDER_UND.match(v) else "unklar")
            else:
                self.verbinder = "unklar"

    def signatur(self) -> tuple:
        return (self.bedingt, self.km, self.zeit, self.verbinder)

    def zeit_alternative(self) -> tuple[int, str] | None:
        """Zeitliche ALTERNATIVE zum Kilometerpunkt (ODER). Eine UND-Zeit ist keine Alternative."""
        return self.zeit if (self.km and self.zeit and self.verbinder == "oder") else None


def zerlege_regeln(text: str | None) -> list[WartungsRegel]:
    """Die einzelnen Fundstellen einer Wartungsaussage -- getrennt an BEIDEN Zusammenführungs-Markern:
    `WEITERE_STELLE_MARKER` (der Erzeuger legt zwei Fundstellen DESSELBEN Dokuments zusammen) und
    `DATENSATZ_MARKER` ("Weiterer Datensatz (Webrecherche): ...", app/risikothemen.py führt Aussagen gleichen Bauteils
    zu einem Insight zusammen). Der Marktvorbehalt-/Hüllentext der ENFAL-Auswertung (`quellenaussage`) wird JE
    Datensatz abgeschnitten (sonst verschwänden alle Datensätze hinter dem ersten Vorbehalt); die angehängten
    Schlusssätze der Zusammenführung gehören zu keiner Regel. Jede Fundstelle behält ihren Wortlaut."""
    out: list[WartungsRegel] = []
    for datensatz in DATENSATZ_MARKER.split(str(text or "")):
        for satz in (SCHLUSSSATZ_UNEINHEITLICH, SCHLUSSSATZ_KEINE_HERSTELLERVORGABE):
            datensatz = datensatz.replace(satz, " ")
        for teil in quellenaussage(datensatz).split(WEITERE_STELLE_MARKER):
            seg = " ".join(teil.split())
            if seg:
                out.append(WartungsRegel(seg))
    return out


def eindeutige_regeln(regeln: list[WartungsRegel]) -> list[WartungsRegel]:
    """Gleiche Aussagen (gleiche Bedingung, Kilometer, Zeit, Verknüpfung) sind EINE Regel: zwei Fundstellen
    desselben Dokuments sind keine unabhängigen Belege."""
    gesehen: set[tuple] = set()
    out: list[WartungsRegel] = []
    for r in regeln:
        if r.signatur() not in gesehen:
            gesehen.add(r.signatur())
            out.append(r)
    return out


def _regel_erreicht(r: WartungsRegel, kilometerstand: int | None, alter_jahre: int | None) -> bool:
    """Ist die ERSTE Schwelle DIESER Regel belegbar erreicht? Eine Schwelle (nur Kilometer / nur Zeit): sie
    entscheidet. Beide mit ODER: eine sicher erreichte genügt (die unbekannte andere Seite hebt sie nicht
    auf). Beide mit UND: beide müssen sicher erreicht sein. Ohne erkennbare Verknüpfung: kein Vorrang."""
    km_ok = bool(r.km and kilometerstand and kilometerstand > 0 and kilometerstand >= r.km[0])
    zeit_ok = bool(r.zeit and _zeit_klasse(r.zeit, alter_jahre) == "erreicht")
    if r.km and not r.zeit:
        return km_ok
    if r.zeit and not r.km:
        return zeit_ok
    if not r.km and not r.zeit:
        return False
    if r.verbinder == "oder":
        return km_ok or zeit_ok
    if r.verbinder == "und":
        return km_ok and zeit_ok
    return False


def ist_nur_bedingt(text: str | None) -> bool:
    """Hat die Aussage Intervalle, aber ausschließlich BEDINGTE (keine Grundregel ohne Voraussetzung)?"""
    mit_schwelle = [r for r in zerlege_regeln(text) if r.km or r.zeit]
    return bool(mit_schwelle) and all(r.bedingt for r in mit_schwelle)


def schwelle_belegbar_erreicht(text: str | None, kilometerstand: int | None, baujahr: int | None,
                               *, heute_jahr: int | None = None) -> bool:
    """True nur, wenn die Schwelle des ERSTintervalls belegbar erreicht ist -- siehe
    `schwelle_belegbar_erreicht_alter`."""
    return schwelle_belegbar_erreicht_alter(text, kilometerstand, fahrzeugalter(baujahr, heute_jahr=heute_jahr))


def schwelle_belegbar_erreicht_alter(text: str | None, kilometerstand: int | None,
                                     alter_jahre: int | None) -> bool:
    """Gemeinsame Schwellenlogik für Auswahl-Rang, Aktionsrang und Hinweis (`_regel_erreicht` je Fundstelle).

    Nur UNBEDINGTE Grundregeln zählen; eine bedingte Zusatzregel (Hinweismeldung, "falls", erschwerte
    Einsatzbedingungen) hebt eine Grundregel weder auf noch ersetzt sie. Gibt es mehrere VERSCHIEDENE
    Grundregeln, muss jede erreicht sein (keine willkürliche Erstauswahl). Folgeintervalle ("Danach ...")
    zählen nicht. Behauptet keine Fälligkeit und keine unterlassene Wartung: gemeint ist nur "hier lohnt der
    Nachweis der Servicehistorie"."""
    grund = eindeutige_regeln([r for r in zerlege_regeln(text) if not r.bedingt and (r.km or r.zeit)])
    return bool(grund) and all(_regel_erreicht(r, kilometerstand, alter_jahre) for r in grund)


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


def _zeit_erreicht_satz(zeitalternative: tuple[int, str], ab_jahre: int) -> str:
    zahl, einheit = zeitalternative
    return (f"die zeitliche Alternative (oder {zeit_anzeige(zahl, einheit)}) ist nach dem Baujahr bereits "
            f"erreicht (Fahrzeugalter mindestens {ab_jahre} Jahre; der genaue Erstzulassungsmonat "
            f"ist unbekannt). Ob die Arbeit seitdem gemacht wurde, geht aus den vorliegenden Daten nicht hervor.")


def _zeit_technik_satz(zeitalternative: tuple[int, str], technik: str, ab_jahre: int | None) -> str:
    """Zeit-Aussage bei UNGEKLÄRTER technischer Voraussetzung: der Grund der Offenheit ist die Voraussetzung."""
    zahl, einheit = zeitalternative
    satz = (f"Die Quelle nennt für diesen Wartungspunkt zusätzlich eine zeitliche Alternative "
            f"(oder {zeit_anzeige(zahl, einheit)}); ob sie für dieses Fahrzeug überhaupt gilt, hängt von der "
            f"ungeklärten technischen Voraussetzung ab ({technik}).")
    if ab_jahre is not None:
        satz += (f" Sofern die Voraussetzung zutrifft, ist die Zeitgrenze nach dem Baujahr bereits erreicht "
                 f"(Fahrzeugalter mindestens {ab_jahre} Jahre); ob die Arbeit seitdem gemacht wurde, geht aus "
                 f"den vorliegenden Daten nicht hervor.")
    return satz


def _zeit_offen_satz(klasse: str) -> str:
    """Der tatsächliche Grund, warum die Zeitalternative offen bleibt (nicht pauschal der Erstzulassungsmonat)."""
    if klasse == "baujahr_unbekannt":
        return ("ob die zeitliche Alternative bereits erreicht ist, lässt sich ohne bekanntes Baujahr nicht "
                "bestimmen und bleibt deshalb offen.")
    if klasse == "nicht_erreicht":
        return "nach dem Baujahr ist die zeitliche Alternative rechnerisch noch nicht erreicht."
    return ("ob die zeitliche Alternative bereits erreicht ist, lässt sich ohne den genauen "
            "Erstzulassungsmonat nicht bestimmen und bleibt deshalb offen.")


def _formuliere(bauteil: str, status: str, kilometerstand: int,
                von_km: int, bis_km: int | None, herkunft: str,
                zeitalternative: tuple[int, str] | None = None,
                zeit_offen_trotz_entfernt: bool = False,
                zeit_erreicht_ab_jahre: int | None = None,
                *, zeit_klasse: str = "band", technik: str | None = None) -> str:
    """Der ausformulierte Hinweis für EINE unbedingte Regel.

    Jede Variante ist bewusst so gebaut, dass sie eine ZUSTANDSFRAGE stellt statt
    einer Zustandsbehauptung. Verboten und in keinem dieser Texte enthalten:
    "fällig", "überfällig", "versäumt", "nicht durchgeführt", "fehlt".

    N14-Fix (ENFAL-Reparaturpaket, RC-F-Lauf 07.11.2026): `zeitalternative`
    macht eine im Quelltext mitgenannte zeitliche Alternative ("oder 6 Jahre")
    sichtbar; der km-Status bleibt ausdrücklich NUR ein Kilometerstatus.
    `zeit_offen_trotz_entfernt` behandelt den Fall, dass der km-Status selbst
    `entfernt` ist, die zeitliche Alternative aber trotzdem relevant oder
    ungeklärt bleibt.

    Regime-Paket (N5/N12): `zeit_klasse` ("erreicht" | "band" | "baujahr_unbekannt" | "nicht_erreicht",
    s. `_zeit_klasse`) benennt den TATSÄCHLICHEN Grund, warum die Zeit offen ist. Der fehlende Erstzulassungs-
    monat wird nur im Unsicherheitsbereich ("band") genannt, wo er entscheidend ist. `technik` (ungeklärte
    technische Voraussetzung des Fakts) macht diese zum Grund der offenen Zeitaussage.
    """
    punkt = _punkt_text(von_km, bis_km)
    zusatz = _HERKUNFT_ZUSATZ.get(herkunft, "")
    # F3: eine SEPARAT belegte Zeitschwelle (im ganzen möglichen Altersintervall überschritten, nicht bedingt)
    # ändert den Zeittext. Ein erreichter Kilometerpunkt beweist nichts über die Zeit und kommt hier nie hinein.
    zeit_erreicht_text = ""
    if zeitalternative and zeit_erreicht_ab_jahre is not None and not technik:
        zeit_erreicht_text = _zeit_erreicht_satz(zeitalternative, zeit_erreicht_ab_jahre)
    technik_satz = (_zeit_technik_satz(zeitalternative, technik, zeit_erreicht_ab_jahre)
                    if zeitalternative and technik else "")
    if zeit_offen_trotz_entfernt and zeitalternative:
        kopf = (f"Für „{bauteil}“ ist neben dem Kilometerpunkt ({punkt}) zusätzlich eine zeitliche Alternative "
                f"hinterlegt. Der aktuelle Kilometerstand liegt weit von {punkt} entfernt -- der "
                f"Kilometeranteil dieses Wartungspunkts ist damit nicht einschlägig; ")
        schluss = (" Vor dem Kauf klären, wann diese Arbeit zuletzt gemacht wurde, und den Beleg dazu ansehen.")
        if technik_satz:
            return kopf.rstrip("; ") + ". " + technik_satz + schluss + zusatz
        if zeit_erreicht_text:
            return kopf + zeit_erreicht_text + schluss + zusatz
        zahl, einheit = zeitalternative
        if zeit_klasse == "baujahr_unbekannt":
            grund = ("Ohne bekanntes Baujahr und ohne bekannte Servicehistorie lässt sich nicht ausschließen, "
                     "dass die zeitliche Alternative bereits erreicht ist.")
        else:
            grund = ("Ohne den genauen Erstzulassungsmonat und ohne bekannte Servicehistorie lässt sich aber "
                     "nicht ausschließen, dass die zeitliche Alternative bereits erreicht ist.")
        return (f"Für „{bauteil}“ ist neben dem Kilometerpunkt ({punkt}) zusätzlich eine "
                f"zeitliche Alternative hinterlegt (oder {zeit_anzeige(zahl, einheit)}). Der aktuelle "
                f"Kilometerstand liegt weit von {punkt} entfernt -- der Kilometeranteil "
                f"dieses Wartungspunkts ist damit nicht einschlägig. {grund}"
                f"{schluss}") + zusatz
    zeit_zusatz = ""
    if technik_satz:
        zeit_zusatz = f" Der oben genannte Stand bezieht sich ausschließlich auf den Kilometerstand. {technik_satz}"
    elif zeitalternative and zeit_erreicht_text:
        zeit_zusatz = (f" Der oben genannte Stand bezieht sich ausschließlich auf den Kilometerstand; "
                       f"{zeit_erreicht_text}")
    elif zeitalternative:
        zahl, einheit = zeitalternative
        zeit_zusatz = (
            f" Die Quelle nennt für diesen Wartungspunkt zusätzlich eine zeitliche "
            f"Alternative (oder {zeit_anzeige(zahl, einheit)}): der oben genannte Stand bezieht "
            f"sich ausschließlich auf den Kilometerstand -- {_zeit_offen_satz(zeit_klasse)}")
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
        # ENFAL-Produktionsbefund (01.11.2026, L2) / N5-Fix: bei einem WIEDERKEHRENDEN Intervall (kein `bis_km`)
        # ist `kilometerstand - von_km` keine zulässige Rückstandsrechnung -- ohne bekannten letzten
        # Servicezeitpunkt weiß niemand, wie oft das Intervall seither durchlaufen wurde. "mindestens einmal
        # erreicht" benennt nur die Tatsache, ohne Anzahl, Datum oder Fälligkeit; KEINE Ersatzrechnung.
        kern = (f"Der hinterlegte Wartungspunkt für „{bauteil}“ ({punkt}) wurde bei "
                f"dieser Laufleistung rein rechnerisch mindestens einmal erreicht. Ohne "
                f"bekannten Bezug zur tatsächlichen Servicehistorie lässt sich daraus "
                f"weder eine Anzahl noch ein Rückstand in Kilometern ableiten. Nachweis "
                f"über die Durchführung zeigen lassen: ob die Arbeit bereits erledigt "
                f"ist, geht aus den vorliegenden Daten nicht hervor.")
    else:
        return ""
    return kern + zeit_zusatz + zusatz


def _km_bedingt_satz(status: str, kilometerstand: int, von_km: int, bis_km: int | None) -> str:
    """Rechenstand des Kilometerpunkts UNTER VORBEHALT einer ungeklärten Voraussetzung -- nie als erreicht."""
    punkt = _punkt_text(von_km, bis_km)
    if status == STATUS_DARUEBER:
        return (f"Träfe die Voraussetzung zu, wäre der Kilometerpunkt ({punkt}) bei dieser Laufleistung rein "
                f"rechnerisch mindestens einmal erreicht; weder eine Anzahl noch ein Rückstand lässt sich daraus "
                f"ableiten.")
    if status == STATUS_IM_BEREICH:
        return f"Träfe die Voraussetzung zu, läge diese Laufleistung im relevanten Bereich des Punkts ({punkt})."
    if status == STATUS_NAEHERT_SICH:
        return (f"Träfe die Voraussetzung zu, läge der Kilometerpunkt ({punkt}) rund "
                f"{_km(von_km - kilometerstand)} voraus.")
    return (f"Der aktuelle Kilometerstand liegt weit vom Kilometerpunkt ({punkt}) entfernt -- der Kilometeranteil "
            f"ist damit nicht einschlägig.")


def _zeit_bedingt_satz(zeit_alt: tuple[int, str] | None, klasse: str | None) -> str:
    """Zeit-Aussage UNTER VORBEHALT einer ungeklärten Voraussetzung: nie "erreicht" als Behauptung."""
    if not zeit_alt:
        return ""
    zahl, einheit = zeit_alt
    alt = f"oder {zeit_anzeige(zahl, einheit)}"
    if klasse == "nicht_erreicht":
        return f" Nach dem Baujahr ist die zeitliche Alternative ({alt}) rechnerisch noch nicht erreicht."
    return (f" Die Quelle nennt zusätzlich eine zeitliche Alternative ({alt}). Ohne Klärung der Voraussetzung lässt "
            f"sich nicht ausschließen, dass die zeitliche Alternative bereits erreicht ist.")


_VERNEHMBAR_KURZ = 220


def _zusatz_satz(texte: list[str], zeitregel: bool = False) -> str:
    """Bedingte Zusatzregel(n): wörtlich, mit ungeklärter Voraussetzung, NICHT ausgewertet."""
    if not texte:
        return ""
    zitate = "; ".join(f"„{_kurz(t, 420)}“" for t in texte[:3])
    ende = ("sie wird hier nicht ausgewertet, und ihre Angaben werden nicht auf die Zeitregel übertragen."
            if zeitregel else "sie wird hier nicht ausgewertet und ändert die Grundregel nicht.")
    return (f" Zusätzlich nennt die Quelle eine bedingte Regel ({zitate}). Ob ihre Voraussetzung auf dieses "
            f"Fahrzeug zutrifft, ist ungeklärt; {ende}")


def _kurz(text: str, n: int = _VERNEHMBAR_KURZ) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[:n - 3].rstrip() + "..."


def _formuliere_regeln(bauteil: str, ausw: "_Auswertung", kilometerstand: int, herkunft: str,
                       alter_jahre: int | None, technik: str | None) -> str:
    """Hinweistext aus der regimegetrennten Auswertung (`_auswerte_regeln`).

    * EINE Grundregel (ohne Voraussetzung): eigenständig ausgewertet (`_formuliere`); jede bedingte Zusatzregel
      steht danach WÖRTLICH mit ihrer ungeklärten Voraussetzung da, ändert aber nichts an der Grundregel.
    * MEHRERE verschiedene Grundregeln: keine wird ausgewählt, Kilometer- und Zeitwerte verschiedener Regeln
      werden nicht kombiniert.
    * NUR bedingte Regeln: weder Kilometer noch Zeit werden als erreicht ausgegeben."""
    zusatz = _HERKUNFT_ZUSATZ.get(herkunft, "")
    regeln = ausw.regeln
    einsatz = (" Die Angabe gilt laut Quelle für normale Einsatzbedingungen; welche Einsatzbedingungen beim "
               "Fahrzeug vorlagen, ist unbekannt.") if any(r.einsatz == "normal" for r in regeln) else ""
    bedingte = _zusatz_satz(ausw.zusatz) if ausw.modus == "grund" else ""
    if ausw.modus == "grund" and not ausw.mehrdeutig:
        r = regeln[0]
        alt = r.zeit_alternative()
        klasse = _zeit_klasse(alt, alter_jahre) if alt else "band"
        ab = (alter_jahre - 1) if (alt and klasse == "erreicht" and alter_jahre is not None) else None
        text = _formuliere(bauteil, ausw.status, kilometerstand, ausw.von_km, ausw.bis_km, herkunft,
                           alt, zeit_offen_trotz_entfernt=ausw.zeit_offen_trotz_entfernt,
                           zeit_erreicht_ab_jahre=ab, zeit_klasse=klasse, technik=technik)
        if zusatz and text.endswith(zusatz):
            text = text[:-len(zusatz)]
        return text + einsatz + bedingte + zusatz
    if ausw.mehrdeutig:
        aufzaehlung = "; ".join(f"„{_kurz(r.erst, 140)}“" for r in regeln[:4])
        bed = ("Alle diese Angaben sind an eine Voraussetzung geknüpft, deren Zutreffen ungeklärt ist. "
               if ausw.modus == "nur_bedingt" else "")
        frueh = _punkt_text(ausw.von_km, ausw.bis_km)
        if ausw.modus == "nur_bedingt":
            km_satz = _km_bedingt_satz(ausw.status, kilometerstand, ausw.von_km, ausw.bis_km)
            km_satz = km_satz.replace("Träfe die Voraussetzung zu", "Träfe die früheste Angabe samt Voraussetzung zu")
        elif ausw.status == STATUS_DARUEBER:
            km_satz = (f"Der früheste genannte Kilometerpunkt ({frueh}) wurde bei dieser Laufleistung rein "
                       f"rechnerisch mindestens einmal erreicht.")
        elif ausw.status == STATUS_IM_BEREICH:
            km_satz = f"Der früheste genannte Kilometerpunkt ({frueh}) liegt bei dieser Laufleistung im relevanten Bereich."
        elif ausw.status == STATUS_NAEHERT_SICH:
            km_satz = f"Der früheste genannte Kilometerpunkt ({frueh}) liegt rund {_km(ausw.von_km - kilometerstand)} voraus."
        else:
            km_satz = (f"Der aktuelle Kilometerstand liegt weit vom frühesten genannten Kilometerpunkt ({frueh}) "
                       f"entfernt; zeitliche Alternativen lassen sich ohne Zuordnung der Angaben nicht bewerten.")
        return (f"Die Quelle nennt für „{bauteil}“ mehrere unterschiedliche Intervalle: {aufzaehlung}. {bed}"
                f"Welche Angabe für dieses Fahrzeug gilt, geht aus den vorliegenden Daten nicht hervor; keine wird "
                f"ausgewählt, und Kilometer- und Zeitwerte verschiedener Angaben werden nicht kombiniert. "
                f"{km_satz} Nachweis über die Durchführung zeigen lassen: ob die Arbeit bereits erledigt ist, "
                f"geht aus den vorliegenden Daten nicht hervor.") + einsatz + bedingte + zusatz
    # nur bedingte Regel(n), genau eine
    r = regeln[0]
    alt = r.zeit_alternative()
    klasse = _zeit_klasse(alt, alter_jahre) if alt else None
    punkt = _punkt_text(ausw.von_km, ausw.bis_km)
    schluss = ("Vor dem Kauf klären, wann diese Arbeit zuletzt gemacht wurde, und den Beleg dazu ansehen."
               if ausw.zeit_offen_trotz_entfernt else
               "Nachweis über die Durchführung zeigen lassen: ob die Arbeit bereits erledigt ist, geht aus den "
               "vorliegenden Daten nicht hervor.")
    return (f"Für „{bauteil}“ nennt die Quelle ein Intervall ({punkt}) nur unter einer Voraussetzung: "
            f"„{_kurz(r.text, 420)}“. Ob diese Voraussetzung auf dieses Fahrzeug zutrifft, ist ungeklärt; deshalb "
            f"wird der Punkt weder als erreicht noch als nicht erreicht ausgegeben. "
            f"{_km_bedingt_satz(ausw.status, kilometerstand, ausw.von_km, ausw.bis_km)}"
            f"{_zeit_bedingt_satz(alt, klasse)} {schluss}") + einsatz + zusatz


class _Auswertung:
    """Ergebnis der regimegetrennten Auswertung einer Wartungsaussage (`_auswerte_regeln`)."""
    __slots__ = ("modus", "regeln", "zusatz", "mehrdeutig", "status", "von_km", "bis_km",
                 "zeit_offen_trotz_entfernt")


def _auswerte_regeln(roh: str | None, kilometerstand: int, alter_jahre: int | None,
                     zeit_erlaubt: bool = False) -> "_Auswertung | None":
    """Wertet die Fundstellen einer Aussage GETRENNT aus -- oder None (kein Hinweis).

    `modus` "grund": mindestens eine UNBEDINGTE Regel mit Kilometerpunkt; nur unbedingte Regeln werden ausgewertet
    (eine reine Zeit-Grundregel daneben zählt für die Mehrdeutigkeit mit). "nur_bedingt": alle Regeln mit
    Kilometerpunkt sind bedingt. Mehrere VERSCHIEDENE Regeln gleicher Art sind `mehrdeutig`: Status und Punkt stammen
    dann vom FRÜHESTEN genannten Kilometerpunkt, es wird keine Regel ausgewählt und nichts kombiniert.

    `modus` "zeit": es gibt UNBEDINGTE Regeln, aber KEINE davon mit Kilometerpunkt (reine Zeit-Grundregel) -- nur für
    Webrecherche-Fakten (`zeit_erlaubt`). Der Kilometerpunkt einer bedingten Zusatzregel ersetzt die Zeitregel nie
    (`von_km`/`bis_km` bleiben None); die Zusatzregel steht nur als wörtliche, ungeklärte Zusatzaussage dabei. Status:
    "darueber" = jede Zeitgrenze belegbar erreicht, sonst "entfernt" (nur die Zeitfrage ist offen). Ist die Grenze auch
    im ältesten Fall nicht erreicht, entsteht kein Hinweis (kein Anlass)."""
    rs = zerlege_regeln(roh)
    schwelle = [r for r in rs if r.km or r.zeit]
    grund_alle = eindeutige_regeln([r for r in schwelle if not r.bedingt])
    if grund_alle and not any(r.km for r in grund_alle):
        if not zeit_erlaubt:
            return None
        klassen = [_zeit_klasse(r.zeit, alter_jahre) for r in grund_alle]
        if all(k == "nicht_erreicht" for k in klassen):
            return None
        a = _Auswertung()
        a.modus = "zeit"
        a.regeln = grund_alle
        a.zusatz = list(dict.fromkeys(r.text for r in rs if r.bedingt))
        a.mehrdeutig = len(grund_alle) > 1
        a.von_km = a.bis_km = None
        a.status = STATUS_DARUEBER if all(k == "erreicht" for k in klassen) else STATUS_ENTFERNT
        a.zeit_offen_trotz_entfernt = a.status == STATUS_ENTFERNT     # nur die Zeitfrage ist offen: kein Überspringen
        return a
    grund = grund_alle
    basis = grund if grund else eindeutige_regeln([r for r in schwelle if r.km])
    if not basis:
        return None
    a = _Auswertung()
    a.modus = "grund" if grund else "nur_bedingt"
    a.regeln = basis
    a.zusatz = list(dict.fromkeys(r.text for r in rs if r.bedingt)) if grund else []
    a.mehrdeutig = len(basis) > 1
    frueh = min((r for r in basis if r.km), key=lambda r: (r.km[0], r.km[1] if r.km[1] is not None else 0))
    a.von_km, a.bis_km = frueh.km
    a.status = status_zu_punkt(kilometerstand, a.von_km, a.bis_km)
    a.zeit_offen_trotz_entfernt = (
        a.status == STATUS_ENTFERNT
        and any(r.zeit_alternative() and _zeit_klasse(r.zeit_alternative(), alter_jahre) != "nicht_erreicht"
                for r in basis))
    return a


def _formuliere_zeit(bauteil: str, regeln: list[WartungsRegel], alter_jahre: int | None, technik: str | None,
                     herkunft: str, zusatz_texte: list[str]) -> str:
    """Hinweistext einer reinen Zeit-Grundregel. Keine Fälligkeit, keine unterlassene Wartung; der Grund der
    offenen Aussage ist der tatsächliche (Baujahr unbekannt / Unsicherheitsbereich / technische Voraussetzung)."""
    einsatz = (" Die Angabe gilt laut Quelle für normale Einsatzbedingungen; welche Einsatzbedingungen beim "
               "Fahrzeug vorlagen, ist unbekannt.") if any(r.einsatz == "normal" for r in regeln) else ""
    zusatz = _HERKUNFT_ZUSATZ.get(herkunft, "")
    if len(regeln) > 1:
        aufz = "; ".join(f"„{_kurz(r.erst, 140)}“" for r in regeln[:4])
        return (f"Die Quelle nennt für „{bauteil}“ mehrere unterschiedliche Zeitintervalle ohne erkennbare "
                f"Zuordnung: {aufz}. Welches für dieses Fahrzeug gilt, geht aus den vorliegenden Daten nicht hervor; "
                f"keines wird ausgewählt oder mit einer anderen Angabe kombiniert."
                f"{einsatz}{_zusatz_satz(zusatz_texte, True)}{zusatz}")
    zahl, einheit = regeln[0].zeit
    klasse = _zeit_klasse(regeln[0].zeit, alter_jahre)
    kopf = f"Für „{bauteil}“ nennt die Quelle ein Zeitintervall ({zeit_anzeige(zahl, einheit)}) ohne Kilometerangabe."
    erreicht = (f"nach dem Baujahr bereits erreicht (Fahrzeugalter mindestens {alter_jahre - 1} Jahre; der genaue "
                f"Erstzulassungsmonat ist unbekannt)") if klasse == "erreicht" else ""
    if technik:
        satz = (f" Ob die Zeitgrenze für dieses Fahrzeug überhaupt gilt, hängt von der ungeklärten technischen "
                f"Voraussetzung ab ({technik}).")
        if erreicht:
            satz += f" Sofern sie zutrifft, ist die Zeitgrenze {erreicht}."
    elif klasse == "erreicht":
        satz = (f" Diese Zeitgrenze ist {erreicht}. Ob die Arbeit seitdem gemacht wurde, geht aus den vorliegenden "
                f"Daten nicht hervor.")
    elif klasse == "baujahr_unbekannt":
        satz = (" Ob diese Zeitgrenze bereits erreicht ist, lässt sich ohne bekanntes Baujahr nicht bestimmen und "
                "bleibt deshalb offen. Vor dem Kauf klären, wann diese Arbeit zuletzt gemacht wurde, und den Beleg "
                "dazu ansehen.")
    else:
        satz = (" Ob diese Zeitgrenze bereits erreicht ist, lässt sich ohne den genauen Erstzulassungsmonat nicht "
                "bestimmen und bleibt deshalb offen. Vor dem Kauf klären, wann diese Arbeit zuletzt gemacht wurde, "
                "und den Beleg dazu ansehen.")
    return f"{kopf}{satz}{einsatz}{_zusatz_satz(zusatz_texte, True)}{zusatz}"


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


def _regel_signaturen(i: Insight) -> set[tuple]:
    herkunft = _QUELLEN_KATEGORIEN.get(i.kategorie)
    if herkunft is None:
        return set()
    return {r.signatur() for r in zerlege_regeln(_intervall_text(i, herkunft)) if r.km or r.zeit}


def _kandidaten(insights: list[Insight]) -> list[tuple[Insight, str]]:
    """Insights und ihre Nebenbelege (zusammengeführte Aussagen). Ein Nebenbeleg, dessen Regeln der führende Insight
    in seinem Text (`Weiterer Datensatz ...`) bereits trägt, wird nicht ein zweites Mal bewertet: gleiche Fundstellen
    sind keine unabhängigen Belege und erzeugen keine zweite Aktion."""
    out = [(i, i.id) for i in insights]
    for i in insights:
        eigene = _regel_signaturen(i)
        for n in getattr(i, "nebenbelege", None) or []:
            sig = _regel_signaturen(n)
            if eigene and sig and sig <= eigene:
                continue
            out.append((n, i.id))
    return out


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
    gesehen_grund: set[str] = set()
    out: list[tuple[bool, Wartungshinweis]] = []
    # Root-Cause-Closing: ein Wartungseintrag kann in eine Schwachstellen-Aussage
    # zum selben Bauteil zusammengeführt sein (app/risikothemen.py). Sein
    # Intervall zählt trotzdem; als Beleg steht die ID der sichtbaren,
    # zusammengeführten Aussage, denn nur sie existiert in `insights`.
    kandidaten = _kandidaten(insights)
    for i, beleg_id in kandidaten:
        herkunft = _QUELLEN_KATEGORIEN.get(i.kategorie)
        if herkunft is None:
            continue
        roh = _intervall_text(i, herkunft)
        # Regime-Paket (N5/N12): die Fundstellen der Aussage werden GETRENNT ausgewertet (Bedingung, Kilometer, Zeit
        # und Tätigkeit gehören zur selben Fundstelle) -- s. `_auswerte_regeln`. Gelesen wird nur das ERSTintervall:
        # "Danach ..." wird nie zum Erstintervall. Ohne Kilometerpunkt im Erstintervall entsteht kein Hinweis.
        ausw = _auswerte_regeln(roh, kilometerstand, alter_jahre, zeit_erlaubt=(herkunft == "web_wartung"))
        if ausw is None:
            continue           # zustandsbezogen, DB-Zeitzeile oder kein Anlass: kein Hinweis
        status = ausw.status
        if status == STATUS_ENTFERNT and not ausw.zeit_offen_trotz_entfernt:
            continue           # §G: kein Anlass, also keine Warnung
        bauteil = _bauteil(i)
        schluessel = norm_bauteil(bauteil)
        # Eine NUR bedingte Regel (Voraussetzung ungeklärt, z. B. erschwerte Einsatzbedingungen) verbraucht den
        # Bauteilplatz nicht gegen eine spätere GRUNDREGEL desselben Bauteils: sonst ginge die Grundregel verloren.
        nur_bedingt = ausw.modus == "nur_bedingt"
        if schluessel in gesehen_grund or (nur_bedingt and schluessel in gesehen):
            continue
        gesehen.add(schluessel)
        if not nur_bedingt:
            gesehen_grund.add(schluessel)
        # F4: die Präsenz-Bedingung des Titels ("Falls ... verbaut") bleibt in der Ausgabe erhalten, ist aber
        # nicht Teil des Schlüssels.
        bedingung_praefix = ""
        if (i.titel or "").startswith("Falls ") and ": " in i.titel:
            bedingung_praefix = i.titel.split(": ", 1)[0] + ": "
        technik = (getattr(i, "technische_bedingung", None)
                   if getattr(i, "technische_bedingung_status", None) == "unresolved" else None)
        out.append((ausw.modus == "nur_bedingt", Wartungshinweis(
            bauteil=bauteil,
            status=status,
            punkt_km=ausw.von_km,
            punkt_bis_km=ausw.bis_km,
            # Reine Zeitregel: kein Kilometerpunkt -> null (nie 0, nie ein Wert der bedingten Zusatzregel).
            differenz_km=(kilometerstand - ausw.von_km) if ausw.von_km is not None else None,
            intervall_text=(roh or "").strip(),
            hinweis=bedingung_praefix + (
                _formuliere_zeit(bauteil, ausw.regeln, alter_jahre, technik, herkunft, ausw.zusatz)
                if ausw.modus == "zeit" else
                _formuliere_regeln(bauteil, ausw, kilometerstand, herkunft, alter_jahre, technik))
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
        )))

    # STATUS_ENTFERNT taucht hier nur noch in genau einem Fall auf: km-seitig
    # entfernt, aber mit relevanter/ungeklärter Zeitalternative (s. oben) --
    # rangiert bewusst zuletzt, denn der km-Anteil ist nachweislich nicht
    # einschlägig und nur die Zeitfrage bleibt offen.
    rang = {STATUS_DARUEBER: 3, STATUS_IM_BEREICH: 2, STATUS_NAEHERT_SICH: 1, STATUS_ENTFERNT: 0}
    # Eine NUR bedingte Regel (Voraussetzung ungeklärt) rangiert höchstens wie ein nahender Punkt: sie ist keine
    # belegte Grundregel und darf eine solche nicht verdrängen.
    out.sort(key=lambda p: (-(min(rang[p[1].status], 1) if p[0] else rang[p[1].status]),
                            -(abs(p[1].differenz_km) if p[1].differenz_km is not None else 0), p[1].bauteil))
    return [w for _, w in out][:MAX_HINWEISE]


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
