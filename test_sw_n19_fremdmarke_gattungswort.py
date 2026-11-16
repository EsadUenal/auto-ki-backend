"""
SW-N19/E1 (B) -- Markenerkennung: Gattungswort ("seat") ist keine Fremdmarke, echte Fremdfahrzeuge bleiben es.

Befund (reale Sichtung eines PistonHeads-Kaufratgebers zum Lexus RC F, 06.08.2023, Autor Tony Middlehurst,
https://www.pistonheads.com/news/ph-buying-guides/lexus-rc-f-ph-used-buying-guide/47506, gesichtet 15.11.2026): der Satz
"Taller folk found the seat bases to be too high ..." galt als Satz über die Marke SEAT (`_fremde_marke_satz` verglich
kleingeschriebene Tokens mit `MARKEN`). Folge: `fremdfahrzeug_segment`, Modellkontext beendet, alle folgenden Sätze des
Abschnitts gingen ohne inhaltliche Prüfung verloren.

Regel: nur Marken, die zugleich gewöhnliche Wörter sind (seat, mini, smart, land/rover), brauchen einen Eigennamen-Kontext
(GROSS geschrieben und gefolgt von einer Modellbezeichnung, oder durchgehend GROSS, oder "Land Rover"). Alle anderen Marken
("ford mustang", "bmw m4") zählen unabhängig von der Schreibweise. Satzgrenzen, Quellenqualifikation und alle Schwellen
bleiben unverändert.

    python scripts/test_isolated.py test_sw_n19_fremdmarke_gattungswort.py
"""
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="swn19b_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")
import socket  # noqa: E402


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


socket.socket.connect = lambda *a, **k: _gesperrt()
socket.create_connection = _gesperrt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)

from app import database as _db  # noqa: E402
_db.ensure_tables()
import app.technical_research as tr  # noqa: E402

FAILS: list[str] = []


def check(name, ok, detail=""):
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if (detail and not ok) else ""))
    if not ok:
        FAILS.append(name)


def fm(satz, marke="Lexus", modell="RC F"):
    return tr._fremde_marke_satz(satz, marke, modell)


print("== A: Gattungswort ja/nein, Eigenname und Fremdfahrzeug ==")
check("A1 englisches 'seat' (Sitz) ist keine Markennennung", fm("Taller folk found the seat bases to be too high.") is None)
check("A2 'heated/ventilated seat' ist keine Markennennung",
      fm("Others have experienced a section of the heated/ventilated seat becoming very hot.") is None)
check("A3 'smart key' / 'mini spare' (Gattungswörter, klein) keine Markennennung",
      fm("The smart key battery died and the mini spare tyre was flat.") is None)
check("A4 'SEAT' (durchgehend groß) bleibt Fremdmarke", fm("Der SEAT Leon hat dasselbe Problem.") == "seat")
check("A5 'Seat Leon' (Eigenname + Modell) bleibt Fremdmarke", fm("The Seat Leon had the same problem.") == "seat")
check("A6 'Seat' am Satzanfang vor Kleinwort ist Sitz ('Seat belts ...'), 'Audi' bleibt Fremdmarke",
      fm("Seat belts and the Audi A5 were tested.") == "audi")
check("A7 'Seat' am Satzanfang mit Modell bleibt Marke", fm("Seat Ibiza owners report a fault.") == "seat")
check("A8 KLEINGESCHRIEBENES 'ford mustang' bleibt Fremdfahrzeug", fm("the ford mustang had trouble with its water pump") == "ford")
check("A9 'Ford Mustang' bleibt Fremdfahrzeug", fm("The Ford Mustang had trouble with its water pump.") == "ford")
check("A10 'bmw m4' kleingeschrieben bleibt Fremdfahrzeug", fm("the bmw m4 has the same sensor") == "bmw")
check("A11 'Land Rover' bleibt Fremdmarke, 'Land' allein (Länder) nicht", fm("a Land Rover Defender leaks") == "land,rover"
      and fm("Im Land gibt es das nicht.") is None)
check("A12 eigene Marke im Satz hebt die Fremdmarke weiter auf (Vergleich)", fm("The Lexus RC F rivals the BMW M4.") is None)
check("A13 eigenes Modell im Satz hebt die Fremdmarke weiter auf",
      fm("The RC F, unlike the Mercedes C63, has a naturally aspirated V8.") is None)

print("\n== B: ein Gattungswort zerstört den Modellkontext nicht (reale Sätze, RC-F-Kaufratgeber) ==")
SAETZE = [
    "The RC F driving position didn’t suit everybody.",
    "Taller folk found the seat bases to be too high, especially with the sunroof fitted, promoting a hunched, bent-neck posture.",
    "The dashtop also seemed quite high.",
    "Some owners have had an HVAC servo motor malfunction, usually at around 25,000 miles, which prevented the passenger "
    "side vent from blowing cold air.",
    "Others have experienced a section of the heated/ventilated seat becoming very hot when the cooling function was engaged.",
]
ab: list[dict] = []
erlaubt = tr._segment_kontext_saetze(SAETZE, marke="Lexus", modell="RC F", titel_stark=True, abgelehnt=ab,
                                      url="x", kategorie="schwachstelle", fremdabschnitt_sperre=True)
check("B1 alle fünf Sätze behalten den Modellkontext", erlaubt == SAETZE and not ab, str([a["satz"][:40] for a in ab]))
FREMD = ["The RC F driving position didn’t suit everybody.",
         "The Ford Mustang has a heated seat fault.",
         "The dashtop also seemed quite high."]
ab2: list[dict] = []
erlaubt2 = tr._segment_kontext_saetze(FREMD, marke="Lexus", modell="RC F", titel_stark=True, abgelehnt=ab2,
                                       url="x", kategorie="schwachstelle", fremdabschnitt_sperre=True)
check("B2 ein echter Fremdfahrzeug-Satz beendet den Kontext weiterhin (Mustang-Satz und Folgesatz verworfen)",
      erlaubt2 == [FREMD[0]] and len(ab2) == 2, str(erlaubt2))

print("\n" + ("ALLE GRÜN" if not FAILS else f"{len(FAILS)} FEHLER: {FAILS}"))
sys.exit(1 if FAILS else 0)
