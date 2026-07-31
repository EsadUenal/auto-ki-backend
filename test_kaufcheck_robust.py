"""
Test: Robustheit des Kaufcheck-JSON-Parsings (app/car_lookup).

Sichert die behobene Regression ab (KEIN Gemini-Aufruf, deterministisch):
  A) Vollständiges valides JSON -> sauber geparst, "bericht" ist Markdown.
  B) Abgeschnittenes JSON (mitten in Tabelle) -> "bericht" wird NIE als roher
     {"bericht":...}-Text ausgegeben, sondern als geborgener Markdown.
  C) Kaputtes JSON ohne bergbaren Bericht -> saubere Meldung statt Roh-JSON.
  D) finish_reason MAX_TOKENS wird als "abgeschnitten" erkannt.
  E) Reiner Markdown-Input bleibt unverändert (Aufruf aus run_kaufcheck-Nachtrag).

Ausfuehren:  python test_kaufcheck_robust.py
"""
import os
import sys
import re
import json
import types
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_test_"), "test.db")
sys.path.insert(0, ".")

import app.car_lookup as cl   # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


def parse_chain(raw: str) -> dict:
    """Exakt die Parse-/Fallback-Kette aus call_gemini_json (ohne API/Truncation-Check)."""
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw.strip())
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        try:
            return json.loads(cl._escape_json_strings(raw))
        except json.JSONDecodeError:
            try:
                return json.loads(cl._escape_json_strings(cl._repariere_fehlendes_komma(raw)))
            except json.JSONDecodeError:
                return cl._notfall_extraktion(raw)


# ── A) Vollständiges valides JSON ──────────────────────────────────────────
voll = json.dumps({
    "bericht": "## Fahrzeug erkannt\nBMW 320d.\n\n## Kaufempfehlung\n**KAUFEN**",
    "empfehlung": "kaufen", "preis_bewertung": "guenstig",
    "marktpreis_min": 23000, "marktpreis_max": 29000,
}, ensure_ascii=False)
rA = parse_chain(voll)
check("A: valides JSON -> bericht ist Markdown (kein JSON)", rA["bericht"].startswith("## Fahrzeug"))
check("A: valides JSON -> empfehlung korrekt", rA.get("empfehlung") == "kaufen")

# ── B) Abgeschnittenes JSON (mitten in Vergleichstabelle) ──────────────────
trunc = ('{\n  "bericht": "## Fahrzeug erkannt\\nBMW 320d (G20).\\n\\n'
         '## Inserat im Vergleich\\n| Kriterium | Inserat-Angabe | DB')
rB = parse_chain(trunc)
check("B: abgeschnitten -> bericht beginnt NICHT mit '{'", not rB["bericht"].lstrip().startswith("{"))
check("B: abgeschnitten -> kein '\"bericht\":' im sichtbaren Bericht", '"bericht"' not in rB["bericht"])
check("B: abgeschnitten -> geborgener Markdown enthält Überschrift", "## Fahrzeug erkannt" in rB["bericht"])

# ── C) Kaputtes JSON ohne bergbaren bericht ────────────────────────────────
kaputt = '{"empfehlung": "kaufen", "preis_bewertung":'
rC = parse_chain(kaputt)
check("C: kein bericht bergbar -> beginnt NICHT mit '{'", not rC["bericht"].lstrip().startswith("{"))
check("C: kein bericht bergbar -> saubere Meldung statt Roh-JSON", "erneut" in rC["bericht"].lower())

# ── D) _ist_abgeschnitten ──────────────────────────────────────────────────
def fake_resp(reason_name):
    fr = types.SimpleNamespace(name=reason_name)
    return types.SimpleNamespace(candidates=[types.SimpleNamespace(finish_reason=fr)])


check("D: MAX_TOKENS -> abgeschnitten", cl._ist_abgeschnitten(fake_resp("MAX_TOKENS")) is True)
check("D: STOP -> nicht abgeschnitten", cl._ist_abgeschnitten(fake_resp("STOP")) is False)
check("D: keine candidates -> nicht abgeschnitten",
      cl._ist_abgeschnitten(types.SimpleNamespace(candidates=[])) is False)

# ── E) Reiner Markdown bleibt unverändert (run_kaufcheck-Nachtrag) ──────────
md = "## Fahrzeug erkannt\nBMW.\n\n## Kaufempfehlung\n**KAUFEN**"
check("E: reiner Markdown unverändert durch _extrahiere_bericht_string",
      cl._extrahiere_bericht_string(md) == md)

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER: " + ", ".join(FEHLER))
    raise SystemExit(1)
print("Alle Kaufcheck-Robustheits-Tests bestanden.")
