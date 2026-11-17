"""
Eine Bewertungsnote ist kein Hubraum.

Befund (17.11.2026, gesichertes ADAC-HTML Eintrag 244185): "ADAC Urteil Autotest: 2.5 Lexus RC 200t F Sport Automatik"
(Teaser zu ähnlichen Autotests) erzeugte einen starken displacement-Claim 2.5, weil `_RE_HUBRAUM` eine Dezimalzahl mit
folgendem Wort auch OHNE Einheit akzeptiert ("5.0 V8"). Fix: einheitslose Zahl mit Bewertungsbegriff direkt davor
(Urteil/Note/Bewertung/Rating/Score/...) oder Bewertungseinheit direkt dahinter (Sterne/Punkte) ist keine Hubraumangabe.

[ECHT]  = vollständiger extrahierter Seitentext des ADAC-Originals (tests/fixtures/adac_rcf_244185_seitentext_2026-11-17.txt;
          aus dem am 17.11.2026 abgerufenen HTML, Tags/Skripte entfernt).
[SYNTH] = Gegenproben mit anderem Bewertungswortlaut bzw. gültigen Hubraumangaben.

    python scripts/test_isolated.py test_hubraum_bewertungsnote.py
"""
import os
import socket
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="hubraum_note_")
os.environ.setdefault("AUTO_KI_DB_PATH", os.path.join(_tmp, "t.db"))
os.environ.setdefault("AUTO_KI_CHROMA_PATH", os.path.join(_tmp, "chroma"))
_orig_connect = socket.socket.connect


def _connect(self, addr, *a, **k):
    if isinstance(addr, tuple) and str(addr[0]) in ("127.0.0.1", "::1", "localhost"):
        return _orig_connect(self, addr, *a, **k)
    raise RuntimeError(f"Netzzugriff im Test gesperrt: {addr!r}")


def _cc(addr, *a, **k):
    raise RuntimeError(f"Netzzugriff im Test gesperrt: {addr!r}")


socket.socket.connect = _connect
socket.create_connection = _cc
HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)

from app import database as _db  # noqa: E402
_db.ensure_tables()
from app.technical_research import _hubraum_liter_werte, _identitaets_claims, _ziel, werte_identitaet_aus  # noqa: E402

FAILS: list[str] = []


def check(name, ok, detail=""):
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if (detail and not ok) else ""))
    if not ok:
        FAILS.append(name)


def liter(s):
    return [w for w, _ in _hubraum_liter_werte(s)]


ADAC_URL = "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/244185/"
ADAC_TITEL = "Lexus RC F Automatik (12/14 - 12/17): Technische Daten, Bilder, Preise | ADAC"
with open(os.path.join(HIER, "tests", "fixtures", "adac_rcf_244185_seitentext_2026-11-17.txt"), encoding="utf-8") as f:
    ADAC_TEXT = f.read()
ADAC = {"url": ADAC_URL, "title": ADAC_TITEL, "content": ADAC_TEXT}
ZIEL = _ziel("Lexus", "RC F", 2017, "5.0 V8, Heckantrieb", None)
ZIEL.update(leistung_ps=477)

print("== A: [ECHT] ADAC-Original 244185 ==")
check("A0 Auslöser im Original vorhanden", "ADAC Urteil Autotest: 2.5 Lexus RC 200t F Sport" in ADAC_TEXT)
claims = _identitaets_claims(ADAC, ZIEL)
hub = [c["wert"] for c in claims if c["feld"] == "displacement"]
check("A1 kein Hubraum-Claim 2.5 mehr (falscher Einzelclaim verschwunden)", "2.5" not in hub, str(hub))
check("A2 echter Hubraum 4969 ccm -> 5.0 bleibt erhalten", hub and set(hub) == {"5.0"}, str(hub))
check("A3 Kraftstoff-Claim 'benzin' (ADAC-Quelle) bleibt erhalten",
      any(c["feld"] == "fuel" and c["wert"] == "benzin" and c["domain"] == "adac.de" for c in claims))
check("A4 Leistung 477 und Antrieb Heck bleiben erhalten",
      any(c["feld"] == "horsepower" and c["wert"] == 477 for c in claims)
      and any(c["feld"] == "drivetrain" and c["wert"] == "Heck" for c in claims))
TM = {"url": "https://www.toyota-media.de/blog/lexus-modelle/artikel/x/text", "title": "Lexus RC F",
      "content": "Der Lexus RC F wird von einem 5,0 Liter V8-Saugmotor angetrieben, der 351 kW / 477 PS leistet."}
w, _ = werte_identitaet_aus([ADAC, TM], ZIEL)
d = (w.diagnose or {}).get("felder", {}).get("displacement", {})
check("A5 Konsens: nur '5.0' als Wert, kein konkurrierender 2.5-Wert",
      set(((d.get("konsens") or {}).get("werte") or {}).keys()) == {"5.0"}, str((d.get("konsens") or {}).get("werte")))

print("\n== B: [SYNTH] Gegenproben ==")
check("B1 anderer Bewertungswortlaut: 'Gesamtnote: 2,5 Audi A4' -> kein Hubraum", liter("Gesamtnote: 2,5 Audi A4 Avant") == [])
check("B2 'Bewertung 1.8 Mercedes C-Klasse' -> kein Hubraum", liter("Bewertung 1.8 Mercedes C-Klasse im Test") == [])
check("B3 'Rating: 3.5 Toyota Yaris' -> kein Hubraum", liter("Rating: 3.5 Toyota Yaris") == [])
check("B4 Bewertungseinheit dahinter: '4.5 Sterne Skoda' -> kein Hubraum", liter("Kundenurteil 4.5 Sterne Skoda") == [])
check("B5 gültig: '5.0 V8' bleibt Hubraum", liter("Lexus RC F mit 5.0 V8 Saugmotor") == ["5.0"])
check("B6 gültig: '2.0 TSI' bleibt Hubraum", liter("VW Golf 2.0 TSI 190 PS") == ["2.0"])
check("B7 gültig: '5,0 Liter' bleibt Hubraum", liter("ein 5,0 Liter V8") == ["5.0"])
check("B8 gültig: '1.6 l' bleibt Hubraum", liter("Motor 1.6 l Benziner") == ["1.6"])
check("B9 reale ABWEICHENDE Hubraumangabe wird nicht als Note unterdrückt (Wertungswort + ausdrückliche Einheit)",
      liter("Urteil: Der Wagen hat 2,5 Liter Hubraum") == ["2.5"], str(liter("Urteil: Der Wagen hat 2,5 Liter Hubraum")))
check("B10 gültig: Hubraum nach Absatzgrenze trotz früherem Wertungswort",
      liter("Gesamtnote gut. Der Motor: 2.0 TSI mit 190 PS") == ["2.0"], str(liter("Gesamtnote gut. Der Motor: 2.0 TSI mit 190 PS")))
check("B11 Tausenderformat bleibt: '4.969 cm³' -> 5.0 (Claim-Pfad)",
      [c["wert"] for c in _identitaets_claims({"url": "https://www.toyota-media.de/x", "title": "Lexus RC F",
                                               "content": "Der Lexus RC F hat 4.969 cm³ Hubraum."}, ZIEL)
       if c["feld"] == "displacement"] == ["5.0"])

print("\n" + ("ALLE GRÜN" if not FAILS else f"{len(FAILS)} FEHLER: {FAILS}"))
sys.exit(1 if FAILS else 0)
