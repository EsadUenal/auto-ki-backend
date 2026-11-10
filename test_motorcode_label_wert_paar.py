"""
Motorcode-Parserbefund (aufgezeichneter auto-data.net-Treffer, tests/fixtures/
live_rollenbeleg_treffer_2026-11-08.json): der Abschnitt "## Motor" nennt

    Motormodell/Motorcode

    2UR-GSE [...]

als Label-Wert-Paar in getrennten Absätzen. `_saetze()` verwarf beide Zeilen einzeln (Mindestlänge),
daher entstand nie ein engine_code-Claim. Korrigiert wird nur die Satzbildung der Identitäts-Claims;
Modell-, Zeitraum-, Quellenprüfung und Bestätigungsschwellen bleiben unverändert.

Dieser Test belegt das Verhalten am AUFGEZEICHNETEN Treffer. Er sagt nichts über den damaligen
Live-Treffersatz.

    python test_motorcode_label_wert_paar.py
"""
import json
import os
import socket
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="mc_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


socket.socket.connect = _gesperrt
socket.create_connection = _gesperrt
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.technical_research import (  # noqa: E402
    _identitaets_claims, _ziel, ausgerichtet, werte_identitaet_aus,
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
Z = _ziel("Lexus", "RC F", 2017, "5.0 V8, Heckantrieb", None)


def codes(hit, ziel=Z):
    return [(c["wert"], c.get("verworfen")) for c in _identitaets_claims(hit, ziel) if c["feld"] == "engine_code"]


# 1) Fundstelle: Label und Wert stehen im Originaltext zusammen im Abschnitt "## Motor"
txt = AUTODATA["content"]
i = txt.find("Motormodell/Motorcode")
check("Fundstelle: Label 'Motormodell/Motorcode' mit Wert 2UR-GSE im Abschnitt '## Motor' neben '477 PS'",
      i > 0 and txt[i:].split()[1] == "2UR-GSE" and "## Motor" in txt[:i] and "477 PS" in txt[:i],
      repr(txt[i - 80:i + 40]))
check("Titel nennt das Zielmodell ('Lexus RC F 5.0 (477 PS)')", AUTODATA["title"].startswith("Lexus RC F 5.0"))

# 2) Der Originaltreffer erzeugt jetzt den Claim
c = codes(AUTODATA)
check("Originaltreffer: engine_code-Claim 2UR-GSE entsteht (nicht verworfen)", ("2UR-GSE", None) in c, str(c))

# 3) Gegenfälle
ZP = _ziel("Lexus", "RC F", 2017, "5.0 V8, Heckantrieb",
           {"leistung_ps": 477, "kraftstoff": "Benzin", "getriebe": "automatik", "antrieb": "Heck"})
fremd = {"url": "https://www.auto-data.net/de/lexus-rc-300h",
         "title": "Lexus RC 300h 2.5 (220 PS) Hybrid | Technische Daten, Verbrauch, Maße",
         "content": "## Motor\n\nMax. Motorleistung\n\n220 PS @ 5700 U/min\n\nMotormodell/Motorcode\n\n2AR-FSE [...]"}
ident_fremd, _ = werte_identitaet_aus([fremd], ZP)
check("Fremdes Modell im Titel (RC 300h): der Treffer ist für das Ziel RC F nicht ausgerichtet",
      ausgerichtet(fremd, "Lexus", "RC F")[0] is False)
check("… und liefert keinen Motorcode für das Ziel (Aggregation)",
      ident_fremd.motorcode is None and not (ident_fremd.feldwerte or {}).get("engine_code"))

beilaeufig = dict(AUTODATA, content=(
    "Der Motorcode steht im Fahrzeugschein und nicht im Inserat, daher bitte beim Verkäufer erfragen.\n\n"
    "Motorcode\n\nHubraum\n\n4969 cm3"))
check("Beiläufige Erwähnung / Feldwort als Wert ('Motorcode' -> 'Hubraum'): kein Claim", codes(beilaeufig) == [],
      str(codes(beilaeufig)))

ohne_label = dict(AUTODATA, content="Anzahl der Zylinder\n\n8\n\n2UR-GSE\n\nHubraum\n\n4969 cm3")
check("Unbeschrifteter Codewert ohne Motorcode-Label (Snippetanfang): kein Claim", codes(ohne_label) == [],
      str(codes(ohne_label)))

# 4) Bestätigung im Bericht: reichen die Belege? (getrennt vom Claim; Schwellen unverändert)
allein, _ = werte_identitaet_aus([AUTODATA], ZP)
check("auto-data.net ALLEIN (Tier 3): Claim entsteht, reicht aber NICHT für die Bestätigung (Schwelle unverändert)",
      ("2UR-GSE", None) in codes(AUTODATA) and allein.motorcode is None)
alle, _ = werte_identitaet_aus(HITS, ZP)
check("Aufgezeichneter Treffersatz: Motorcode bleibt ohne starke Quelle unbestätigt (ADAC-Datenblatt 2024/25 "
      "wird für 2017 korrekt verworfen)", alle.motorcode is None)
check("477 PS bleiben bestätigt (aufgezeichnete Treffer)", alle.leistung_ps == 477, str(alle.leistung_ps))

# Mit einer starken Zweitquelle (echter Toyota-Pressetext, wörtlich aus test_motorcode_kw_ps_hubraum_fremdeinheit.py)
TOYOTA = {"url": "https://global.toyota/en/detail/4101939",
          "title": "Lexus Launches All-new 'RC F' High-performance Coupe in Japan | Toyota Motor Corporation",
          "content": ("the RC F powertrain has a new V8 5.0-liter engine using D-4S fuel injection technology and "
                      "Dual VVT-i technology with VVT-iE, which delivers the response and exhilarating sense of "
                      "engine speed of a naturally aspirated engine. RC F Engine Key Specifications: Total "
                      "displacement (cc) Maximum output (kW[PS]/rpm) Maximum torque (N-m[kgf-m]/rpm) 2UR-GSE "
                      "4,968 351 [477] / 7,100 530 [54.0] / 4,800-5,600. Engine Transmission Driveline Price RC F "
                      "Carbon Exterior package 2UR-GSE (V8 5.0-liter) 8-Speed SPDS Rear-wheel drive 10,300,000 yen.")}
t_allein, _ = werte_identitaet_aus([TOYOTA], ZP)
t_plus, _ = werte_identitaet_aus([TOYOTA, AUTODATA], ZP)
n_allein = (t_allein.feldwerte or {}).get("engine_code", {}).get("domains")
n_plus = (t_plus.feldwerte or {}).get("engine_code", {}).get("domains")
print("   [Info] Toyota allein ->", t_allein.motorcode, "Domains:", n_allein, "| Toyota + auto-data ->",
      t_plus.motorcode, "Domains:", n_plus)
check("Mit starker Zweitquelle: derselbe Code, jetzt von zwei Domains getragen (auto-data.net zählt mit)",
      t_allein.motorcode == t_plus.motorcode == "2UR-GSE" and (n_plus or 0) == (n_allein or 0) + 1,
      f"{n_allein} -> {n_plus}")

print()
if _FEHLER:
    print("FEHLER:", _FEHLER)
    raise SystemExit(1)
print("ALLE OK")
