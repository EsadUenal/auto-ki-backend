"""
Vergleichstabelle, Zeile "Motor/Leistung": bestaetigte TEILE der zusammengesetzten Motorangabe (field_evidence.engine_name,
verification_state user_only, bestaetigte_teile) werden transportiert; Leistung und Motorangabe getrennt bewertet.

Synthetische Feldzustaende ([SYNTH]) in der Form, die das Backend liefert (Befund: Lauf 18.11.2026, engine_name user_only mit
bestaetigte_teile ["5.0"], horsepower durch Web bestaetigt). Kein Live-Nachweis.

Netzsperre (extern; Loopback bleibt offen) + Temp-DB VOR den App-Importen. Lauf:
    python scripts/test_isolated.py test_vergleichstabelle_motor_teilbestaetigung.py
"""
import os
import socket
import sys
import tempfile
from types import SimpleNamespace

_TMP = tempfile.mkdtemp(prefix="vt_motor_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_TMP, "chroma")
_orig_connect = socket.socket.connect


def _connect(self, addr, *a, **k):
    if isinstance(addr, tuple) and str(addr[0]) in ("127.0.0.1", "::1", "localhost"):
        return _orig_connect(self, addr, *a, **k)
    raise RuntimeError(f"Netzzugriff im Test gesperrt: {addr!r}")


def _cc(addr, *a, **k):
    raise RuntimeError(f"Netzzugriff im Test gesperrt: {addr!r}")


socket.socket.connect = _connect
socket.create_connection = _cc
print("Netzsperre (extern):", socket.socket.connect is _connect, "| Temp-DB:", os.environ["AUTO_KI_DB_PATH"])
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)

from app import database as _db  # noqa: E402
_db.ensure_tables()
from app.models import KaufCheckRequest  # noqa: E402
from app import vergleichstabelle as vt  # noqa: E402

fehler: list[str] = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        fehler.append(name)


REQ = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000, kraftstoff="Benzin",
                       motor="5.0 V8, Heckantrieb", leistung_ps=477, ausstattung=[])


def identitaet(ps_bestaetigt, engine_fe):
    hp_fe = ({"verification_state": "user_confirmed", "confirmed_by": ["web"]} if ps_bestaetigt else
             {"verification_state": "user_only", "confirmed_by": []})
    return SimpleNamespace(horsepower=477, engine_name="5.0 V8, Heckantrieb", generation=None, fuel="benzin",
                           field_evidence={"horsepower": hp_fe, "engine_name": engine_fe})


def zeile(identity):
    z = [x for x in vt.baue_zeilen(REQ, None, None, identity=identity) if x.kriterium == "Motor/Leistung"]
    return z[0] if z else None


TEIL = {"verification_state": "user_only", "bestaetigte_teile": ["5.0"], "raw_user_value": "5.0 V8, Heckantrieb"}

print("== A: Teilbestaetigter Motor UND bestaetigte Leistung ==")
z = zeile(identitaet(True, TEIL))
check("A1 Referenz nennt Leistung und den bestaetigten Motorteil", z is not None and z.referenz == "477 PS; Motor: „5.0“", str(z))
check("A2 Einordnung: Leistung bestaetigt, von der Motorangabe NUR „5.0“ bestaetigt, uebrige Bestandteile nicht unabhaengig geprueft",
      z.einordnung == "✓ Leistung bestätigt; von der Motorangabe nur „5.0“ bestätigt, übrige Bestandteile nicht unabhängig geprüft",
      z.einordnung)
check("A3 keine Vollbestaetigung und keine Ableitung weiterer Teile (V8/Heckantrieb nicht als bestaetigt genannt)",
      z.einordnung != vt.PASST and "V8" not in z.einordnung and "Heck" not in z.einordnung and "V8" not in z.referenz
      and "Heck" not in z.referenz and z.angabe.startswith("5.0 V8, Heckantrieb"), f"{z.angabe} | {z.referenz}")
check("A4 die tatsaechliche Markdown-Zelle traegt Referenz und Einordnung",
      "477 PS; Motor: „5.0“" in vt.als_markdown([z]) and "nur „5.0“ bestätigt" in vt.als_markdown([z]))

print("\n== B: Teilbestaetigter Motor OHNE bestaetigte Leistung ==")
z = zeile(identitaet(False, TEIL))
check("B1 nur der belegte Motorteil steht als Referenz, Leistung ausdruecklich nicht geprueft; kein '✓'",
      z is not None and z.referenz == "Motor: „5.0“" and "Leistung nicht unabhängig geprüft" in z.einordnung
      and not z.einordnung.startswith("✓"), str(z))

print("\n== C: Bestaetigte Leistung OHNE Motor-Teilbestaetigung (bisheriges Verhalten) ==")
for name, fe in (("kein Feld", {"verification_state": "user_only"}), ("leere Liste", {"verification_state": "user_only", "bestaetigte_teile": []}),
                 ("nur Leerstrings", {"verification_state": "user_only", "bestaetigte_teile": ["", "   "]}),
                 ("None", {"verification_state": "user_only", "bestaetigte_teile": None}), ("kein engine_name-Eintrag", {})):
    z = zeile(identitaet(True, fe))
    check(f"C {name}: Referenz '477 PS', bisherige Einordnung 'Leistung bestätigt; übrige Angaben nicht unabhängig geprüft'",
          z.referenz == "477 PS" and z.einordnung == "✓ Leistung bestätigt; übrige Angaben nicht unabhängig geprüft", str(z))
z = zeile(identitaet(False, {"verification_state": "user_only", "bestaetigte_teile": []}))
check("C6 weder Leistung noch Motor bestaetigt: 'nicht bewertbar', keine Referenz",
      z.einordnung == vt.NICHT_BEWERTBAR and z.referenz is None, str(z))

print("\n== D: Andere Herkunftsstatus bleiben erhalten ==")
for state in ("user_confirmed", "web_only", "conflict", "unknown"):
    z = zeile(identitaet(True, {"verification_state": state, "bestaetigte_teile": ["5.0"]}))
    check(f"D {state}: bestaetigte_teile wirkt nur bei user_only (bisherige Zeile)",
          z.referenz == "477 PS" and z.einordnung == "✓ Leistung bestätigt; übrige Angaben nicht unabhängig geprüft", str(z))
z = zeile(identitaet(False, {"verification_state": "user_confirmed", "bestaetigte_teile": ["5.0"]}))
check("D5 vollstaendig bestaetigter Motor ohne Leistung: keine Teilbestaetigungszeile", z.einordnung == vt.NICHT_BEWERTBAR, str(z))
z = zeile(identitaet(True, {"verification_state": "user_only", "bestaetigte_teile": ["5.0", "V8"]}))
check("D6 mehrere belegte Teile werden alle, aber nur diese genannt",
      z.referenz == "477 PS; Motor: „5.0“, „V8“" and "nur „5.0“, „V8“ bestätigt" in z.einordnung and "Heck" not in z.einordnung, str(z))

print("\n" + ("ALLE GRÜN" if not fehler else f"{len(fehler)} FEHLER: {fehler}"))
sys.exit(1 if fehler else 0)
