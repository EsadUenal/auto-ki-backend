"""
Wartungs-Sammelpunkt (`wartungsrest`): Darstellung je Arbeit als eigene Zeile, identischer Marktvorbehalt einmal mit den NAMEN der
betroffenen Arbeiten. Die vollständigen Texte aus den realen Fixtures stehen in test_n21_wartung_kurzform.py (C1-C6) und
test_wartung_abnahme_buendelung.py (Zuordnung je Arbeit); hier die Grenzfälle der Darstellungsregel.

    python scripts/test_isolated.py test_wartungsrest_darstellung.py
"""
import os
import socket
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="wartungsrest_")
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
from app.kaufaktionen import VERKAEUFERFRAGEN, DOKUMENTE, _Sammler, _wartungsrest_text  # noqa: E402

FEHLER: list[str] = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        FEHLER.append(name)


MV = "Marktvorbehalt der Quelle, Geltung in Deutschland nicht bestätigt"
A = f"„Motoröl“ (Wechsel; laut Quelle: alle 15.000 km oder 1 Jahr; {MV})"
B = f"„Ventilspiel“ (Prüfung; laut Quelle: alle 90.000 km oder 6 Jahre; {MV})"
C = "„Kühlmittel“ (Prüfung; laut Quelle: alle 30.000 km oder 2 Jahre; Anwendbarkeit ungeklärt, zuerst klären: nur Zusatzheizung)"
R = "„Riemen“ (Prüfung; laut Quelle: Erstintervall 105.000 km oder 6 Jahre, danach 15.000 km / 1 Jahr)"

print("== Darstellungsregel ==")
t = _wartungsrest_text(VERKAEUFERFRAGEN, [A, B, C, R])
zeilen = t.split("\n")
check("1 Einleitung unverändert, danach je Arbeit genau eine Zeile (4 Arbeiten -> 4 Aufzählungszeilen)",
      zeilen[0] == _Sammler._rest_einleitung(VERKAEUFERFRAGEN).rstrip()
      and [z[:2] for z in zeilen[1:5]] == ["• "] * 4 and [z[2:] for z in zeilen[3:5]] == [C, R], t)
check("2 identischer Marktvorbehalt (zwei Arbeiten): EINMAL, mit den Namen genau dieser beiden; nicht in den Arbeitszeilen",
      t.count(MV) == 1 and zeilen[-1] == f"{MV}: für „Motoröl“, „Ventilspiel“."
      and MV not in zeilen[1] and MV not in zeilen[2], t)
check("3 Bedingung/Anwendbarkeit und Erst-/Folgeintervall bleiben in der Zeile ihrer Arbeit",
      "Anwendbarkeit ungeklärt, zuerst klären: nur Zusatzheizung" in zeilen[3] and "Erstintervall 105.000 km oder 6 Jahre, danach 15.000 km / 1 Jahr" in zeilen[4])
t1 = _wartungsrest_text(DOKUMENTE, [A, C, R])
check("4 nur EINE Arbeit mit Marktvorbehalt: bleibt in ihrer Zeile, keine Schlusszeile",
      MV in t1.split("\n")[1] and t1.count(MV) == 1 and not t1.split("\n")[-1].startswith("Marktvorbehalt"), t1)
t0 = _wartungsrest_text(DOKUMENTE, [C, R])
check("5 ohne Marktvorbehalt: keine Schlusszeile, Dokument-Einleitung unverändert",
      t0.split("\n")[0] == _Sammler._rest_einleitung(DOKUMENTE).rstrip() and len(t0.split("\n")) == 3, t0)
ohne_namen = "Bauteil ohne Anführungszeichen (Prüfung; " + MV + ")"
t2 = _wartungsrest_text(VERKAEUFERFRAGEN, [A, ohne_namen])
check("6 nicht eindeutig benennbare Arbeit: nichts bündeln (Vorbehalt bleibt in beiden Zeilen)", t2.count(MV) == 2, t2)
check("7 keine Kürzung: alle Zeileninhalte sind vollständig im Text enthalten (jeweils ohne Vorbehalt, falls gebündelt)",
      all(z.replace("; " + MV, "") in t for z in (A, B, C, R)))
check("8 keine Fälligkeits-/Versäumnisaussage hinzugefügt",
      not any(w in t.lower().replace("falls durchgeführt", "") for w in ("überfällig", "versäumt", "nicht durchgeführt", " fällig")))

print("\n" + ("ALLE GRÜN" if not FEHLER else f"{len(FEHLER)} FEHLER: {FEHLER}"))
sys.exit(1 if FEHLER else 0)
