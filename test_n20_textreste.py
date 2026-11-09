"""
N20 -- Textreste und Wiederholungen im KaufCheck-Bericht (RC-F-Replay 08.11.2026):
  * "(oder 1 Jahre)" -> "(oder 1 Jahr)"
  * Öl-Fundstellen: Satzzeichen vor "Weitere Stelle im selben Dokument", die
    Fußnotenbedingung bleibt an ihrer eigenen Stelle
  * EINE Wartungsfrage, ausgerichtet an der belegten Tätigkeit (gewechselt / geprüft /
    neutral), für km-relevante UND weit entfernte Punkte
  * Kaltstart-Dopplung gestrafft, pauschale Checklistenformulierungen konkret/neutral

Reine Textänderungen: Evidenz, Rangfolge, Limits, Empfehlung sind unberührt.

    python test_n20_textreste.py
"""
import os
import socket
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="n20_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


socket.socket.connect = _gesperrt
socket.create_connection = _gesperrt
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.kaufaktionen import _wartungsfrage  # noqa: E402
from app.laufleistung import parse_zeitalternative, zeit_anzeige  # noqa: E402
from app.pruefplan_basis import (  # noqa: E402
    BASIS_BESICHTIGUNG, BASIS_PROBEFAHRT, BASIS_VERKAEUFERFRAGEN,
)

_FEHLER: list[str] = []


def check(name, bedingung, detail=""):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


# 1) Zeitangaben
check("1 Jahr / 6 Jahre / 1 Monat / 18 Monate",
      [zeit_anzeige(1, "Jahre"), zeit_anzeige(6, "Jahre"), zeit_anzeige(1, "Monate"),
       zeit_anzeige(18, "Monate")] == ["1 Jahr", "6 Jahre", "1 Monat", "18 Monate"])
check("Parser unverändert (Einheit im Plural, Schwellenlogik darauf angewiesen)",
      parse_zeitalternative("alle 15.000 km / 1 Jahr") == (1, "Jahre")
      and parse_zeitalternative("90.000 km oder 6 Jahre") == (6, "Jahre"))

# 2) Öl-Fundstellen: Satzzeichen/Fußnote werden über den echten Pfad (echte Serviceheft-Blöcke) in
#    test_n7_wartungsanfrage_ohne_generation.py geprüft (Check D6).

# 3) Wartungsfragen
check("Wechsel -> 'bereits gewechselt'",
      _wartungsfrage("Motorkühlmittel", "ersetzen")
      == "Wurde „Motorkühlmittel“ bereits gewechselt, und wenn ja, wann und bei welchem Kilometerstand?")
check("Kontrolle -> 'bereits geprüft'", "bereits geprüft" in _wartungsfrage("Ventilspiel", "pruefen"))
neutral = _wartungsfrage("Automatikgetriebeöl", None)
check("offene Tätigkeit -> neutral, ohne Rückschluss aus dem Bauteilnamen",
      "bereits durchgeführt" in neutral and "gewechselt" not in neutral and "geprüft" not in neutral
      and "gemacht" not in neutral, neutral)
check("km-relevante und weit entfernte Punkte teilen EINE Fragenform (dieselbe Funktion)",
      _wartungsfrage("X", "ersetzen").startswith("Wurde „X“ bereits")
      and "Wann wurde" not in _wartungsfrage("X", "ersetzen"))

# 4) Checklisten
besichtigung = {e[0]: e for e in BASIS_BESICHTIGUNG}
probe = {e[0]: e for e in BASIS_PROBEFAHRT}
fragen = {e[0]: e for e in BASIS_VERKAEUFERFRAGEN}
check("Kaltstart: Besichtigung nennt Startdauer und Rauch, Geräusche gehören zur Probefahrt",
      "Startdauer" in besichtigung["kaltstart"][3] and "Rauch" in besichtigung["kaltstart"][3]
      and "Geräusche in den ersten Sekunden" not in besichtigung["kaltstart"][3])
check("Kaltstart: Geräuschpunkt der Probefahrt steht nicht mehr doppelt (in 'Leerlauf' aufgegangen)",
      "startgeraeusche" not in probe and "Rasseln" in probe["leerlauf"][3]
      and "hinhören" in probe["leerlauf"][2])
check("'Alle Gänge einmal durchfahren' ersetzt (auch für Automatik zutreffend)",
      "Alle Gänge einmal" not in probe["schalten"][2]
      and "Fahrstufe" not in probe["schalten"][2] + probe["schalten"][3])
check("'seriöser Verkäufer stimmt in der Regel zu' ersetzt durch konkrete, neutrale Aussage",
      "in der Regel" not in fragen["werkstattpruefung"][3]
      and "ohne nachvollziehbaren Grund" in fragen["werkstattpruefung"][3])
check("Prüfplan-Umfang bleibt im Zielkorridor (Probefahrt 15-25 Punkte)", 15 <= len(BASIS_PROBEFAHRT) <= 25,
      str(len(BASIS_PROBEFAHRT)))

print()
if _FEHLER:
    print("FEHLER:", _FEHLER)
    raise SystemExit(1)
print("ALLE OK")
