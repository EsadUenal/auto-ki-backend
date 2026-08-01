"""
Test: Robustheit + Preislogik des Verkaufschecks.

Deterministisch, kein echter Gemini-Aufruf (der Client wird für den Retry-Test
gemockt):
  A) Abgeschnittenes JSON (bericht ist das LETZTE Feld) -> nie roher JSON im Bericht.
  B) MAX_TOKENS -> EIN kontrollierter Retry; bleibt es abgeschnitten -> sauberer
     Fehler (GeminiFehlgeschlagen). Gelingt der Retry -> normales Ergebnis.
  D) Preis-Konsistenz-Guard: empfohlener/maximaler Preis über der Markt-Obergrenze
     wird mit Euro UND Prozent transparent gemacht; innerhalb der Spanne kein Hinweis.

Ausfuehren:  python test_verkaufscheck_robust.py
"""
import os
import sys
import re
import json
import types
import asyncio
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_test_"), "test.db")
sys.path.insert(0, ".")

import app.car_lookup as cl                     # noqa: E402
import app.verkaufscheck as vc                  # noqa: E402
from app.gemini_retry import GeminiFehlgeschlagen  # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


def parse_chain(raw: str) -> dict:
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


# ── A) Abgeschnittenes Verkaufs-JSON: bericht ist das LETZTE Feld ───────────
trunc = (
    '{\n  "schnellverkaufs_preis": 20500,\n  "maximal_preis": 24500,\n'
    '  "empfohlener_preis": 22500,\n  "marktpreis_min": 18000,\n  "marktpreis_max": 21500,\n'
    '  "bericht": "## Fahrzeug erkannt\\nMercedes C200 (W205).\\n\\n'
    '## (c) Preis-Optimierungstipps\\n**Historie:** Betonen Sie unfallfrei'
)
rA = parse_chain(trunc)
check("A: abgeschnitten -> bericht beginnt NICHT mit '{'", not rA["bericht"].lstrip().startswith("{"))
check("A: abgeschnitten -> kein '\"bericht\":' im Bericht", '"bericht"' not in rA["bericht"])
check("A: abgeschnitten -> keine Zahlfeld-Keys im Bericht", '"schnellverkaufs_preis"' not in rA["bericht"])
check("A: abgeschnitten -> geborgener Markdown enthält Überschrift", "## Fahrzeug erkannt" in rA["bericht"])


# ── B) MAX_TOKENS: Retry, dann Fehler bzw. Erfolg ──────────────────────────
def fake_resp(reason_name, text):
    fr = types.SimpleNamespace(name=reason_name)
    cand = types.SimpleNamespace(finish_reason=fr)
    return types.SimpleNamespace(candidates=[cand], text=text)


class FakeModels:
    def __init__(self, responses):
        self.responses = responses
        self.calls = 0

    async def generate_content(self, **kw):
        r = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return r


def _install_fake_client(responses):
    models = FakeModels(responses)
    client = types.SimpleNamespace(aio=types.SimpleNamespace(models=models))
    cl.get_gemini_client = lambda: client

    async def _wr(func):
        return await func()

    cl.with_retry = _wr
    return models


_ECHT_CLIENT, _ECHT_WR = cl.get_gemini_client, cl.with_retry
try:
    # B1: immer MAX_TOKENS -> nach 1 Retry (=2 Aufrufe) sauberer Fehler
    m = _install_fake_client([fake_resp("MAX_TOKENS", '{"bericht": "## abgeschnitten'),
                              fake_resp("MAX_TOKENS", '{"bericht": "## abgeschnitten')])
    raised = False
    try:
        asyncio.run(cl.call_gemini_json("sys", "msg"))
    except GeminiFehlgeschlagen:
        raised = True
    check("B1: dauerhaft MAX_TOKENS -> GeminiFehlgeschlagen", raised)
    check("B1: genau 2 Aufrufe (1 Retry)", m.calls == 2)

    # B2: erst MAX_TOKENS, dann Erfolg -> Retry liefert Ergebnis
    good = json.dumps({"bericht": "## Fahrzeug erkannt\nOK", "empfohlener_preis": 20000}, ensure_ascii=False)
    m2 = _install_fake_client([fake_resp("MAX_TOKENS", "{"), fake_resp("STOP", good)])
    res = asyncio.run(cl.call_gemini_json("sys", "msg"))
    check("B2: Retry erfolgreich -> geparstes Ergebnis", res.get("bericht", "").startswith("## Fahrzeug"))
    check("B2: genau 2 Aufrufe", m2.calls == 2)
finally:
    cl.get_gemini_client, cl.with_retry = _ECHT_CLIENT, _ECHT_WR


# ── D) Preis-Konsistenz-Guard ──────────────────────────────────────────────
bericht_ohne = "## (b) Empfohlene Preisspanne\nBegründung ohne Abweichungsangabe."

# empfohlen 22.000 (2,3% über 21.500) und max 23.500 (9,3%) -> Hinweis mit € und %
r1 = vc._preis_konsistenz_hinweis(bericht_ohne, 22000, 23500, 21500)
check("D1: Hinweis angehängt", "Hinweis zur Preiseinordnung" in r1)
check("D1: nennt empfohlenen Preis über Markt", "22.000" in r1 and "21.500" in r1)
check("D1: nennt Prozent", "%" in r1.split("Hinweis zur Preiseinordnung")[1])
check("D1: nennt Maximalpreis-Abweichung", "23.500" in r1 and "2.000" in r1)

# innerhalb der Spanne -> kein Hinweis
r2 = vc._preis_konsistenz_hinweis(bericht_ohne, 21000, 21500, 21500)
check("D2: innerhalb Spanne -> kein Hinweis", "Hinweis zur Preiseinordnung" not in r2)

# Bericht begründet Abweichung bereits (% + Markt-Obergrenze) -> kein zusätzlicher Hinweis
bericht_mit = "Der Maximalpreis liegt ~9 % über der Markt-Obergrenze, begründet durch seltene Ausstattung."
r3 = vc._preis_konsistenz_hinweis(bericht_mit, 22000, 23500, 21500)
check("D3: bereits begründet -> kein doppelter Hinweis", "Hinweis zur Preiseinordnung" not in r3)

# keine Markt-Obergrenze (kein Web) -> kein Hinweis
r4 = vc._preis_konsistenz_hinweis(bericht_ohne, 22000, 23500, None)
check("D4: kein marktpreis_max -> kein Hinweis", "Hinweis zur Preiseinordnung" not in r4)

# knapp über Markt (unter Toleranz) -> kein Hinweis
r5 = vc._preis_konsistenz_hinweis(bericht_ohne, 21600, 21600, 21500)
check("D5: unter Toleranz -> kein Hinweis", "Hinweis zur Preiseinordnung" not in r5)


print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER: " + ", ".join(FEHLER))
    raise SystemExit(1)
print("Alle Verkaufscheck-Robustheits-/Preislogik-Tests bestanden.")
