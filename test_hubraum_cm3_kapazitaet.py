"""
Hubraum-Extraktion am aufgezeichneten auto-data.net-Treffer (tests/fixtures/
live_rollenbeleg_treffer_2026-11-08.json).

Befund: "Hubraum" und "4969 cm3 303.23 cu. in." stehen in getrennten Absätzen und gingen wie der
Motorcode-Wert an der Satzbildung verloren; dafür entstanden falsche Hubraum-Claims "8.8"
(Motoröl-Füllmenge 8.8 l), "9.3" (9.3 US qt) und "4.5" (Beschleunigung 4.5 s).

Korrektur (generisch, ohne Schwellenänderung): Label/Wert-Paar "Hubraum" + Wert mit ausdrücklicher
Einheit, Liter-Angaben mit anschließender qt/gal-Umrechnung sind Kapazitäten, ein kleines,
alleinstehendes "s" ist die Sekunde.

Dieser Test belegt das Verhalten am AUFGEZEICHNETEN Treffer, nicht den damaligen Live-Treffersatz.

    python test_hubraum_cm3_kapazitaet.py
"""
import json
import os
import socket
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="hub_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


socket.socket.connect = _gesperrt
socket.create_connection = _gesperrt
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.technical_research import (  # noqa: E402
    _HUBRAUM_FREMDEINHEIT, _identitaets_claims, _ziel, werte_identitaet_aus,
)

_FEHLER: list[str] = []


def check(name, bedingung, detail=""):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


FIX = json.loads((Path(__file__).parent / "tests" / "fixtures"
                  / "live_rollenbeleg_treffer_2026-11-08.json").read_text(encoding="utf-8"))
HITS = [h for g in FIX["treffer"] for h in g["hits"]]
AUTODATA = next(h for h in HITS if "auto-data.net" in h["url"])
ZP = _ziel("Lexus", "RC F", 2017, "5.0 V8, Heckantrieb",
           {"leistung_ps": 477, "kraftstoff": "Benzin", "getriebe": "automatik", "antrieb": "Heck"})


def hubraum(hit):
    return [c["wert"] for c in _identitaets_claims(hit, ZP) if c["feld"] == "displacement" and not c.get("verworfen")]


# 1) Fundstellen im Originaltext
txt = AUTODATA["content"]
check("Fundstelle: 'Hubraum' mit '4969 cm3 303.23 cu. in.' in getrennten Absätzen",
      "Hubraum\n\n4969 cm3 303.23 cu. in." in txt)
check("Herkunft der falschen Werte: 8.8 (Motorölfüllmenge), 9.3 (US qt), 4.5 (Beschleunigung)",
      "Motorölfüllmenge\n\n8.8 l 9.3 US qt" in txt and "[...] 4.5 s\n\nBeschleunigung" in txt)

# 2) Originaltreffer
h = hubraum(AUTODATA)
check("Originaltreffer: korrekter Hubraum 5.0 (aus 4969 cm3) entsteht", "5.0" in h, str(h))
check("Originaltreffer: 8.8, 9.3 und 4.5 entstehen NICHT mehr als Hubraum",
      not ({"8.8", "9.3", "4.5"} & set(h)), str(h))
check("Originaltreffer: ausschließlich der korrekte Hubraum", set(h) == {"5.0"}, str(h))

# 3) Legitime Hubraumangaben bleiben erhalten
def mit(content):
    return dict(AUTODATA, content=content)


check("Legitime Literangabe '5,0 Liter' bleibt Hubraum", "5.0" in hubraum(mit("Der RC F hat einen 5,0 Liter V8 Saugmotor mit 477 PS.")))
check("Legitime Angabe '5.0 V8' (unitloser Zweig) bleibt Hubraum", "5.0" in hubraum(mit("Lexus RC F 5.0 V8 Automatik mit Heckantrieb.")))
check("Legitime cm³-Angabe im Fließtext bleibt Hubraum", "5.0" in hubraum(mit("Der Motor hat 4969 cm³ Hubraum und leistet 477 PS.")))
check("Label/Wert-Paar mit Liter-Einheit ('Hubraum' / '5.0 l') wird erfasst", "5.0" in hubraum(mit("Technische Daten zum Fahrzeug\n\nHubraum\n\n5.0 l\n\nZylinder\n\n8")))
check("Modellbezeichnung mit großem 'S' ('3.0 S Coupé') bleibt Hubraum 3.0", "3.0" in hubraum(mit("Boxster 3.0 S Coupé mit Sportfahrwerk.")))

# 4) Gegenfälle: keine Kapazität/Zeit als Hubraum
check("Ölfüllmenge in Litern mit qt-Umrechnung liefert keinen Hubraum", hubraum(mit("Füllmenge Motoröl 8.8 l 9.3 US qt | 7.74 UK qt beim Wechsel.")) == [])
check("Tankinhalt mit gal-Umrechnung liefert keinen Hubraum", hubraum(mit("Tankinhalt 66 l 17.44 US gal | 14.52 UK gal im Datenblatt.")) == [])
check("Beschleunigung '4.5 s' liefert keinen Hubraum", hubraum(mit("Beschleunigung 0 - 100 km/h 4.5 s laut Hersteller im Test.")) == [])
check("'Hubraum' mit Zahl OHNE Einheit bildet kein Label/Wert-Paar", hubraum(mit("Hubraum\n\n12\n\nZylinder\n\n8")) == [])
check("Fremdeinheit-Filter: kleines 's' = Sekunde, großes 'S' nicht",
      bool(_HUBRAUM_FREMDEINHEIT.match(" s Beschleunigung")) and not _HUBRAUM_FREMDEINHEIT.match(" S Coupé"))

# 5) Claim getrennt von Bestätigung (Schwellen unverändert)
allein, _ = werte_identitaet_aus([AUTODATA], ZP)
alle, _ = werte_identitaet_aus(HITS, ZP)
fw = (alle.feldwerte or {})
check("auto-data.net (Tier 3) ALLEIN: Hubraum-Claim da, aber keine Bestätigung im Ergebnis",
      "5.0" in hubraum(AUTODATA) and "displacement" not in (allein.feldwerte or {}))
dv = fw.get("displacement") or {}
print("   [Info] Aufgezeichneter Treffersatz -> Hubraum", dv.get("value"), "Domains", sorted(dv.get("domain_namen") or []))
check("Aufgezeichneter Treffersatz: bestätigt wird NUR der korrekte Wert 5.0 (8.8/9.3/4.5 gelangen nirgends hin)",
      dv.get("value") == "5.0")
TOYOTA = {"url": "https://global.toyota/en/detail/4101939",
          "title": "Lexus Launches All-new 'RC F' High-performance Coupe in Japan | Toyota Motor Corporation",
          "content": ("the RC F powertrain has a new V8 5.0-liter engine using D-4S fuel injection technology and "
                      "Dual VVT-i technology. RC F Engine Key Specifications: Total displacement (cc) Maximum output "
                      "(kW[PS]/rpm) Maximum torque (N-m[kgf-m]/rpm) 2UR-GSE 4,968 351 [477] / 7,100 530 [54.0] / "
                      "4,800-5,600. Engine Transmission Driveline Price RC F Carbon Exterior package 2UR-GSE "
                      "(V8 5.0-liter) 8-Speed SPDS Rear-wheel drive 10,300,000 yen.")}
t1, _ = werte_identitaet_aus([TOYOTA], ZP)
t2, _ = werte_identitaet_aus([TOYOTA, AUTODATA], ZP)
d1 = (t1.feldwerte or {}).get("displacement") or {}
d2 = (t2.feldwerte or {}).get("displacement") or {}
print("   [Info] Toyota allein -> Hubraum", d1.get("value"), "Domains", d1.get("domains"),
      "| Toyota + auto-data ->", d2.get("value"), "Domains", d2.get("domains"))
check("Mit starker Quelle: Hubraum 5.0 wird bestätigt, auto-data.net zählt als weitere Domain, kein falscher Wert",
      d1.get("value") == d2.get("value") == "5.0" and (d2.get("domains") or 0) == (d1.get("domains") or 0) + 1)

# 6) Motorcode-Fix, 477 PS, UXC1 bleiben erhalten
check("Motorcode-Claim 2UR-GSE aus demselben Treffer bleibt",
      "2UR-GSE" in [c["wert"] for c in _identitaets_claims(AUTODATA, ZP) if c["feld"] == "engine_code"])
check("477 PS bleiben bestätigt", alle.leistung_ps == 477, str(alle.leistung_ps))

print()
if _FEHLER:
    print("FEHLER:", _FEHLER)
    raise SystemExit(1)
print("ALLE OK")
