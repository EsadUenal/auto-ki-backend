"""
Test: Bericht/Feld-Synchronisation (app/kaufempfehlung_sync) und
Wartungs-Faelligkeits-Guard (app/postprocess.neutralisiere_wartungs_faelligkeit)
-- kein LLM, keine Netzwerkaufrufe, kein Tavily.

Deckt die geforderten Faelle A-H ab:
  A  Floor hebt an -> Bericht zeigt ebenfalls die neue Stufe
  B  Floor greift nicht -> Bericht unveraendert
  C  "Zahnriemen ... ist ueberfaellig" ohne harte Evidence -> neutralisiert
  D  "faelliger Zahnriemenwechsel" -> neutralisiert
  E  legitime Nicht-Wartungs-Verwendung von "faellig" -> unveraendert
  F  No-Market unveraendert (Guards greifen unabhaengig vom Marktpfad)
  G  Recommendation-Enum bleibt korrekt (Sync erzeugt keinen ungueltigen Wert)
  H  P2-5 (app/laufleistung.py) weiterhin unberuehrt/gruen

Ausfuehren:  python test_kaufempfehlung_sync.py
"""
import os
import sys
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_sync_"), "test.db")
sys.path.insert(0, ".")

from app.kaufempfehlung_sync import synchronisiere_kaufempfehlung, _ANZEIGE_TEXT  # noqa: E402
from app.postprocess import neutralisiere_wartungs_faelligkeit  # noqa: E402
from app.empfehlungs_floor import (  # noqa: E402
    wende_floor_an, KAUFEN_NACH_BESICHTIGUNG, NUR_MIT_WERKSTATTPRUEFUNG,
)
from app.models import Insight  # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


def schwachstelle(iid, schweregrad):
    return Insight(id=iid, kategorie="schwachstelle", titel="Bauteil — bekannte Schwachstelle",
                   beschreibung="", confidence="hoch", schweregrad=schweregrad)


BERICHT_VORLAGE = (
    "## Fahrzeug erkannt\n"
    "BMW 3er (G20/G21), 320d, Baujahr 2020.\n\n"
    "## Kaufempfehlung\n"
    "**KAUFEN NACH BESICHTIGUNG**\n"
    "Das Inserat zeigt ein plausibles Profil. Vor dem Kauf sollten die bekannten "
    "Rueckrufe geprueft werden.\n\n"
    "## Kritische Risiken\n"
    "- KBA-Rueckrufe pruefen.\n\n"
    "## Preis-Einschaetzung\n"
    "Keine belastbare Marktbasis ermittelt.\n"
)

# -- A) Floor hebt an -> Bericht synchronisiert --
print("-- A) Floor hebt an -> Bericht wird synchronisiert --")
emp, bef = wende_floor_an(KAUFEN_NACH_BESICHTIGUNG, [schwachstelle("schwachstelle-1", "hoch")])
check("A0: Floor hat tatsaechlich angehoben (Testvoraussetzung)",
      emp == NUR_MIT_WERKSTATTPRUEFUNG and bef is not None)
neu = synchronisiere_kaufempfehlung(BERICHT_VORLAGE, emp)
check("A: neue Ueberschrift im Bericht", "**NUR MIT WERKSTATTPRÜFUNG**" in neu)
check("A2: alte Ueberschrift verschwunden", "**KAUFEN NACH BESICHTIGUNG**" not in neu)
check("A3: Rest des Absatzes (Begruendung) unveraendert",
      "Das Inserat zeigt ein plausibles Profil. Vor dem Kauf sollten die bekannten "
      "Rueckrufe geprueft werden." in neu)
check("A4: uebrige Abschnitte byte-identisch",
      "## Fahrzeug erkannt\nBMW 3er (G20/G21), 320d, Baujahr 2020." in neu
      and "## Kritische Risiken\n- KBA-Rueckrufe pruefen." in neu
      and "## Preis-Einschaetzung\nKeine belastbare Marktbasis ermittelt." in neu)

# Alle sechs Anzeige-Texte einzeln durchprobieren (Enum-Vollstaendigkeit).
for enum_wert, anzeige in _ANZEIGE_TEXT.items():
    vorlage = BERICHT_VORLAGE.replace("KAUFEN NACH BESICHTIGUNG", "EGAL WAS HIER STAND")
    ergebnis = synchronisiere_kaufempfehlung(vorlage, enum_wert)
    check(f"A5: Enum {enum_wert} -> Anzeige {anzeige!r} korrekt gesetzt",
          f"**{anzeige}**" in ergebnis)

# -- B) Floor greift nicht -> Bericht unveraendert --
print("\n-- B) Floor greift nicht -> Bericht unveraendert --")
emp2, bef2 = wende_floor_an(KAUFEN_NACH_BESICHTIGUNG, [schwachstelle("schwachstelle-1", "gering")])
check("B0: Floor greift NICHT (Testvoraussetzung)", emp2 == KAUFEN_NACH_BESICHTIGUNG and bef2 is None)
# Simuliert exakt die Verdrahtung in app/kaufcheck.py: der Sync-Call wird nur
# ausgefuehrt, wenn floor_befund is not None.
bericht_unveraendert = BERICHT_VORLAGE
if bef2 is not None:
    bericht_unveraendert = synchronisiere_kaufempfehlung(bericht_unveraendert, emp2)
check("B: Bericht byte-identisch, wenn der Floor nicht griff",
      bericht_unveraendert == BERICHT_VORLAGE)

# Randfaelle der Sync-Funktion selbst
check("B2: kein Bericht -> None bleibt None", synchronisiere_kaufempfehlung(None, "nur_mit_werkstattpruefung") is None)
check("B3: 'unbekannt' hat keinen Anzeige-Text -> Bericht unveraendert",
      synchronisiere_kaufempfehlung(BERICHT_VORLAGE, "unbekannt") == BERICHT_VORLAGE)
check("B4: fehlender Abschnitt -> Bericht unveraendert statt Exception",
      synchronisiere_kaufempfehlung("Kein Kaufempfehlungsabschnitt hier.", "nur_mit_werkstattpruefung")
      == "Kein Kaufempfehlungsabschnitt hier.")

# -- C) "... ist ueberfaellig" im Wartungskontext -> neutralisiert --
print("\n-- C) Praedikative Faelligkeitsbehauptung wird neutralisiert --")
satz_c = "Der Zahnriemenwechsel ist bei dieser Laufleistung überfällig."
neu_c = neutralisiere_wartungs_faelligkeit(satz_c)
check("C: 'überfällig' verschwindet", "überfällig" not in neu_c.lower())
check("C2: Bauteilbezug bleibt erhalten", "Zahnriemenwechsel" in neu_c)
check("C3: keine Gewissheitsbehauptung mehr ('zu prüfen' statt Fakt)", "zu prüfen" in neu_c)

# -- D) "fälliger Zahnriemenwechsel" (attributiv) -> neutralisiert --
print("\n-- D) Attributive Faelligkeitsbehauptung wird neutralisiert (Endung erhalten) --")
satz_d = "Der fällige Zahnriemenwechsel sollte vor dem Kauf nachgewiesen werden."
neu_d = neutralisiere_wartungs_faelligkeit(satz_d)
check("D: 'fällige' ersetzt", "fällige" not in neu_d.lower())
check("D2: grammatisch korrekte Endung (zu prüfende)", "zu prüfende Zahnriemenwechsel" in neu_d)

satz_d2 = "Ein fälliger Ölwechsel wurde laut Inserat bereits durchgeführt."
neu_d2 = neutralisiere_wartungs_faelligkeit(satz_d2)
check("D3: maskuline Endung 'fälliger' -> 'zu prüfender' (exakt das Auftragsbeispiel)",
      "zu prüfender Ölwechsel" in neu_d2)

# -- E) Legitime Nicht-Wartungs-Verwendung -> unveraendert --
print("\n-- E) Nicht-Wartungskontext bleibt unangetastet --")
satz_e1 = "Die Rate für den Anschlusskredit ist zum Monatsende fällig."
check("E: 'fällig' bei einer Zahlung bleibt stehen (kein Wartungskontext)",
      neutralisiere_wartungs_faelligkeit(satz_e1) == satz_e1)
satz_e2 = "Der Verkäufer meldet sich, sobald der TÜV-Termin fällig wird."
check("E2: TÜV-Fälligkeit ausserhalb Wartungswortliste bleibt unangetastet",
      neutralisiere_wartungs_faelligkeit(satz_e2) == satz_e2)

# -- F) No-Market: Guards greifen unabhaengig vom Marktpfad --
print("\n-- F) No-Market beeinflusst die Guards nicht --")
bericht_no_market = BERICHT_VORLAGE + "\nKein Marktpreis verfügbar, da keine Web-Daten vorliegen.\n"
neu_f = neutralisiere_wartungs_faelligkeit(bericht_no_market)
check("F: No-Market-Hinweis bleibt unveraendert (kein Wartungskontext dort)",
      "Kein Marktpreis verfügbar, da keine Web-Daten vorliegen." in neu_f)
emp_f, bef_f = wende_floor_an(KAUFEN_NACH_BESICHTIGUNG, [schwachstelle("schwachstelle-9", "hoch")])
sync_f = synchronisiere_kaufempfehlung(bericht_no_market, emp_f)
check("F2: Sync funktioniert identisch im No-Market-Bericht",
      "**NUR MIT WERKSTATTPRÜFUNG**" in sync_f
      and "Kein Marktpreis verfügbar, da keine Web-Daten vorliegen." in sync_f)

# -- G) Recommendation-Enum bleibt korrekt --
print("\n-- G) Sync erzeugt niemals einen ungueltigen Enum-Anzeigetext --")
GUELTIGE_ENUMS = {"kaufen", "kaufen_nach_besichtigung", "nur_mit_werkstattpruefung",
                  "preis_nachverhandeln", "hohes_risiko", "finger_weg"}
check("G: _ANZEIGE_TEXT deckt genau die gueltigen Enum-Werte ab (kein 'unbekannt')",
      set(_ANZEIGE_TEXT.keys()) == GUELTIGE_ENUMS)
check("G2: unbekannter/erfundener Enum-Wert erzeugt keinen Text (fail-safe)",
      synchronisiere_kaufempfehlung(BERICHT_VORLAGE, "erfundener_wert") == BERICHT_VORLAGE)

# -- H) P2-5 (Laufleistungskontext) unberuehrt --
print("\n-- H) P2-5 (app/laufleistung) bleibt unberuehrt --")
import subprocess  # noqa: E402
p = subprocess.run([sys.executable, "test_laufleistung.py"], capture_output=True, text=True)
check("H: test_laufleistung.py weiterhin gruen", p.returncode == 0)

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER: " + ", ".join(FEHLER))
    raise SystemExit(1)
print("Alle Bericht-Sync-/Wartungs-Guard-Tests bestanden.")
