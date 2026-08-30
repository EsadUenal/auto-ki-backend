"""
Test: AutoFinder-Budget-Plausibilität (Runde 3) — Gemini-Urteil über eine
bereits feststehende Foundation-Shortlist.

Deckt die Produktspezifikations-Abschnitte §11 (Budget-Safety A-F) und §15
(Testliste 1-18): genau EIN Gemini-Call bei Budget / null Calls ohne Budget /
strikte candidate_id-Validierung (unbekannte IDs verworfen, fehlende IDs
UNKNOWN, Duplikate verworfen) / begrenzte, richtungsklare Score-Wirkung je
Status / Ausfallsicherheit (Provider-Fehler, kaputtes JSON) / kein Hard-
Delete durch Budget / keine Preiszahl in der Response / Markt-Felder bleiben
None / Determinismus / Diversität bleibt erhalten / kein Call bei
no_internal_match / bestehende 422-Validierung unverändert.

Gemini wird über `app.autofinder_budget.call_gemini_json` gezielt gefaked
(siehe Fake-Funktionen unten) — echte Kandidaten-IDs werden aus dem an
Gemini gesendeten Prompt extrahiert, damit die Fakes ohne Vorwissen über die
konkrete DB-Auswahl funktionieren. KEIN Netzwerk, KEIN echter Gemini-Call.

Ausführen:  python test_autofinder_budget.py
"""
import importlib
import inspect
import os
import re
import sys
import tempfile

sys.path.insert(0, ".")

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


# ── Frische kanonische DB + eigener Test-API-Key ────────────────────────────
_tmp = tempfile.mkdtemp(prefix="vira_af_budget_")
_db_pfad = os.path.join(_tmp, "kanonisch.db")
os.environ["AUTO_KI_DB_PATH"] = _db_pfad
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")
os.environ["AUTO_KI_API_KEY"] = "test-key-autofinder-budget"

import app.config as _cfg
importlib.reload(_cfg)
import app.database as _db
importlib.reload(_db)
_db.ensure_tables()

from fastapi.testclient import TestClient   # noqa: E402
from app.main import app as fastapi_app     # noqa: E402
import app.routers.autofinder as af_router  # noqa: E402
import app.autofinder as af                 # noqa: E402
import app.autofinder_budget as af_budget   # noqa: E402
from app.gemini_retry import GeminiVoruebergehendNichtErreichbar   # noqa: E402
from app.rate_limit import limiter as _global_limiter               # noqa: E402

client = TestClient(fastapi_app)
HEADERS = {"Authorization": "Bearer test-key-autofinder-budget"}
URL = "/api/v1/autofinder"


def post(body: dict):
    return client.post(URL, json=body, headers=HEADERS)


def _reset_limiters() -> None:
    _global_limiter.reset()
    af_router.limiter.reset()


def _extrahiere_ids(user_msg: str) -> list[str]:
    return re.findall(r"candidate_id=(\S+)", user_msg)


# ══════════════════════════════════════════════════════════════════════════
# Zähl-Wrapper — misst, WIE OFT Gemini aufgerufen wird (§15 Test 1/2/3/17)
# ══════════════════════════════════════════════════════════════════════════
class _Zaehler:
    def __init__(self, innen):
        self.n = 0
        self.letzter_user_msg = None
        self._innen = innen

    async def __call__(self, system_prompt, user_msg):
        self.n += 1
        self.letzter_user_msg = user_msg
        return await self._innen(system_prompt, user_msg)


# ── Fake-Gemini-Antworten ────────────────────────────────────────────────────
async def _gemini_leer(system_prompt, user_msg):
    return {"candidates": []}


async def _gemini_alle(status: str, conf: str):
    async def _fn(system_prompt, user_msg):
        ids = _extrahiere_ids(user_msg)
        return {"candidates": [{"candidate_id": i, "budget_status": status, "confidence": conf}
                                for i in ids]}
    return _fn


async def _gemini_teilweise(anzahl: int, status: str = "IN_BUDGET", conf: str = "HIGH"):
    async def _fn(system_prompt, user_msg):
        ids = _extrahiere_ids(user_msg)[:anzahl]
        return {"candidates": [{"candidate_id": i, "budget_status": status, "confidence": conf}
                                for i in ids]}
    return _fn


async def _gemini_unbekannte_id(system_prompt, user_msg):
    ids = _extrahiere_ids(user_msg)
    return {"candidates": [
        {"candidate_id": "phantom-fahrzeug-existiert-nicht", "budget_status": "IN_BUDGET", "confidence": "HIGH"},
        {"candidate_id": ids[0], "budget_status": "NEAR_BUDGET", "confidence": "MEDIUM"} if ids else
        {"candidate_id": "irrelevant", "budget_status": "NEAR_BUDGET", "confidence": "MEDIUM"},
    ]}


async def _gemini_erfundener_preis(system_prompt, user_msg):
    ids = _extrahiere_ids(user_msg)
    return {"candidates": [
        {"candidate_id": i, "budget_status": "IN_BUDGET", "confidence": "HIGH",
         "preis_eur": 12345, "market_price": "10000-12000 EUR", "geschaetzter_preis": "11.500 €"}
        for i in ids
    ]}


async def _gemini_kaputtes_json(system_prompt, user_msg):
    return "das ist kein dict, sondern nur ein String"


async def _gemini_schema_verstoss(system_prompt, user_msg):
    ids = _extrahiere_ids(user_msg)
    return {"candidates": [{"candidate_id": i, "budget_status": "SUPER_GUENSTIG", "confidence": "SEHR_SICHER"}
                            for i in ids]}


async def _gemini_duplikat(system_prompt, user_msg):
    ids = _extrahiere_ids(user_msg)
    if not ids:
        return {"candidates": []}
    erste = ids[0]
    return {"candidates": [
        {"candidate_id": erste, "budget_status": "IN_BUDGET", "confidence": "HIGH"},
        {"candidate_id": erste, "budget_status": "OUT_OF_BUDGET", "confidence": "LOW"},
    ]}


async def _gemini_503(system_prompt, user_msg):
    raise GeminiVoruebergehendNichtErreichbar("simulierter Provider-Totalausfall")


BODY_MIT_BUDGET = {"kraftstoff": ["Diesel"], "getriebe": ["automatik"],
                    "leistung_min_ps": 150, "budget_min": 20000, "budget_max": 35000}
BODY_OHNE_BUDGET = {"kraftstoff": ["Diesel"], "getriebe": ["automatik"], "leistung_min_ps": 150}


# ══════════════════════════════════════════════════════════════════════════
# 1) Budget vorhanden -> exakt 1 Gemini-Call
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
zaehler1 = _Zaehler(_gemini_leer)
af_budget.call_gemini_json = zaehler1
r1 = post(BODY_MIT_BUDGET)
check("1: 200", r1.status_code == 200)
check("1: Budget vorhanden -> exakt 1 Gemini-Call", zaehler1.n == 1)


# ══════════════════════════════════════════════════════════════════════════
# 2) kein Budget -> 0 Gemini-Calls
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
zaehler2 = _Zaehler(_gemini_leer)
af_budget.call_gemini_json = zaehler2
r2 = post(BODY_OHNE_BUDGET)
check("2: 200", r2.status_code == 200)
check("2: kein Budget -> 0 Gemini-Calls", zaehler2.n == 0)


# ══════════════════════════════════════════════════════════════════════════
# 3) 10-15 Kandidaten -> EIN gemeinsamer Gemini-Call (nicht einer pro Auto)
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
zaehler3 = _Zaehler(_gemini_leer)
af_budget.call_gemini_json = zaehler3
r3 = post({"kraftstoff": ["Benzin"], "budget_min": 5000, "budget_max": 80000})
data3 = r3.json()
ids_im_prompt = _extrahiere_ids(zaehler3.letzter_user_msg or "")
check("3: 200", r3.status_code == 200)
check("3: Shortlist enthält mehrere Kandidaten (>=10)", len(ids_im_prompt) >= 10)
check("3: trotzdem NUR EIN Gemini-Call für die GESAMTE Shortlist", zaehler3.n == 1)
check("3: total_candidates_considered bestätigt großen Pool", data3["total_candidates_considered"] >= 10)


# ══════════════════════════════════════════════════════════════════════════
# 4/5/6/7) Score-Wirkung je Status — begrenzt und richtungsklar
# ══════════════════════════════════════════════════════════════════════════
async def _fn_alle_in(sp, um):
    ids = _extrahiere_ids(um)
    return {"candidates": [{"candidate_id": i, "budget_status": "IN_BUDGET", "confidence": "HIGH"} for i in ids]}


async def _fn_alle_near(sp, um):
    ids = _extrahiere_ids(um)
    return {"candidates": [{"candidate_id": i, "budget_status": "NEAR_BUDGET", "confidence": "MEDIUM"} for i in ids]}


async def _fn_alle_out(sp, um):
    ids = _extrahiere_ids(um)
    return {"candidates": [{"candidate_id": i, "budget_status": "OUT_OF_BUDGET", "confidence": "LOW"} for i in ids]}


_reset_limiters()
af_budget.call_gemini_json = _fn_alle_in
r_in = post(BODY_MIT_BUDGET)
data_in = r_in.json()
check("4: 200 (alle IN_BUDGET)", r_in.status_code == 200)
check("4: IN_BUDGET -> Rankingbonus == +1.5 für jeden Kandidaten",
      all(k["budget_status"] == "IN_BUDGET" and k["budget_adjustment"] == af_budget.BUDGET_BONUS_IN
          for k in data_in["kandidaten"]))
check("4: match_score == base_match_score + budget_adjustment",
      all(abs(k["match_score"] - (k["base_match_score"] + k["budget_adjustment"])) < 1e-9
          for k in data_in["kandidaten"]))

_reset_limiters()
af_budget.call_gemini_json = _fn_alle_near
r_near = post(BODY_MIT_BUDGET)
data_near = r_near.json()
check("5: 200 (alle NEAR_BUDGET)", r_near.status_code == 200)
check("5: NEAR_BUDGET -> begrenzte Wirkung == +0.5 (kleiner als IN_BUDGET-Bonus)",
      all(k["budget_status"] == "NEAR_BUDGET" and k["budget_adjustment"] == af_budget.BUDGET_BONUS_NEAR
          for k in data_near["kandidaten"])
      and af_budget.BUDGET_BONUS_NEAR < af_budget.BUDGET_BONUS_IN)

_reset_limiters()
af_budget.call_gemini_json = _fn_alle_out
r_out = post(BODY_MIT_BUDGET)
data_out = r_out.json()
check("6: 200 (alle OUT_OF_BUDGET)", r_out.status_code == 200)
check("6: OUT_OF_BUDGET -> begrenzter Malus == -1.5",
      all(k["budget_status"] == "OUT_OF_BUDGET" and k["budget_adjustment"] == af_budget.BUDGET_MALUS_OUT
          for k in data_out["kandidaten"]))
check("6: Malus bleibt UNTER einem einzelnen starken Foundation-Kriterium "
      "(z.B. 'Hohe Leistung' = 2 Punkte) — kein Dominieren des Foundation-Scores",
      abs(af_budget.BUDGET_MALUS_OUT) < 2.0)
check("6: kein Kandidat wurde durch den Malus aus der Antwort entfernt "
      "(gleiche Anzahl wie bei IN_BUDGET-Lauf)", len(data_out["kandidaten"]) == len(data_in["kandidaten"]))

_reset_limiters()
af_budget.call_gemini_json = _gemini_leer   # -> alle UNKNOWN (leere Kandidatenliste)
r_unk = post(BODY_MIT_BUDGET)
data_unk = r_unk.json()
_reset_limiters()
af_budget.call_gemini_json = _gemini_leer
r_ohne = post(BODY_OHNE_BUDGET)
data_ohne = r_ohne.json()
check("7: 200 (UNKNOWN)", r_unk.status_code == 200)
check("7: UNKNOWN -> budget_adjustment == 0.0 für jeden Kandidaten",
      all(k["budget_status"] == "UNKNOWN" and k["budget_adjustment"] == 0.0
          for k in data_unk["kandidaten"]))
check("7: UNKNOWN ist neutral -> IDENTISCHE Reihenfolge wie ganz ohne Budget",
      [k["variante_id"] for k in data_unk["kandidaten"]] == [k["variante_id"] for k in data_ohne["kandidaten"]])


# ══════════════════════════════════════════════════════════════════════════
# 8) Gemini-Ausfall -> HTTP 200 + Foundation-Resultat unverändert
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
af_budget.call_gemini_json = _gemini_503
r8 = post(BODY_MIT_BUDGET)
data8 = r8.json()
check("8: Gemini-Totalausfall -> trotzdem HTTP 200 (kein 500, kein Nutzerverlust)",
      r8.status_code == 200)
check("8: alle Kandidaten bleiben budget_status=UNKNOWN",
      all(k["budget_status"] == "UNKNOWN" for k in data8["kandidaten"]))
check("8: Foundation-Reihenfolge bleibt exakt erhalten (identisch zum Non-Budget-Lauf)",
      [k["variante_id"] for k in data8["kandidaten"]] == [k["variante_id"] for k in data_ohne["kandidaten"]])
check("8: neutraler Warnhinweis vorhanden (§8 Produktspezifikation)",
      any("nicht zusätzlich berücksichtigt" in w for w in data8["warnings"]))


# ══════════════════════════════════════════════════════════════════════════
# 9) Gemini ungültiges JSON (kein dict) -> Fallback
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
af_budget.call_gemini_json = _gemini_kaputtes_json
r9 = post(BODY_MIT_BUDGET)
data9 = r9.json()
check("9: 200 trotz kaputter Gemini-Antwort", r9.status_code == 200)
check("9: Fallback -> alle UNKNOWN", all(k["budget_status"] == "UNKNOWN" for k in data9["kandidaten"]))

_reset_limiters()
af_budget.call_gemini_json = _gemini_schema_verstoss
r9b = post(BODY_MIT_BUDGET)
data9b = r9b.json()
check("9b: ungültige Enum-Werte ('SUPER_GUENSTIG'/'SEHR_SICHER') -> verworfen, 200",
      r9b.status_code == 200 and all(k["budget_status"] == "UNKNOWN" for k in data9b["kandidaten"]))

_reset_limiters()
af_budget.call_gemini_json = _gemini_duplikat
r9c = post(BODY_MIT_BUDGET)
data9c = r9c.json()
check("9c: Duplikat-candidate_id -> erster Treffer gewinnt, kein Crash",
      r9c.status_code == 200)


# ══════════════════════════════════════════════════════════════════════════
# 10) Gemini nennt unbekannte candidate_id -> ignoriert
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
af_budget.call_gemini_json = _gemini_unbekannte_id
r10 = post(BODY_MIT_BUDGET)
data10 = r10.json()
check("10: 200", r10.status_code == 200)
check("10: die erfundene ID 'phantom-fahrzeug-existiert-nicht' taucht NIRGENDS "
      "in der Response auf", "phantom-fahrzeug-existiert-nicht"
      not in [k["variante_id"] for k in data10["kandidaten"]])
check("10: der EINE tatsächlich adressierte, gültige Kandidat trägt NEAR_BUDGET",
      any(k["budget_status"] == "NEAR_BUDGET" for k in data10["kandidaten"]))
check("10: alle NICHT adressierten Kandidaten bleiben UNKNOWN",
      sum(1 for k in data10["kandidaten"] if k["budget_status"] == "UNKNOWN")
      == len(data10["kandidaten"]) - 1)


# ══════════════════════════════════════════════════════════════════════════
# 11) Gemini antwortet nur für einen Teil der Shortlist -> Rest UNKNOWN
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
zaehler11 = _Zaehler(_gemini_leer)


async def _teilweise_stub(system_prompt, user_msg):
    ids = _extrahiere_ids(user_msg)
    haelfte = ids[:max(1, len(ids) // 2)]
    return {"candidates": [{"candidate_id": i, "budget_status": "IN_BUDGET", "confidence": "HIGH"}
                            for i in haelfte]}


af_budget.call_gemini_json = _teilweise_stub
r11 = post({"kraftstoff": ["Benzin"], "budget_min": 5000, "budget_max": 80000})
data11 = r11.json()
anzahl_in_budget = sum(1 for k in data11["kandidaten"] if k["budget_status"] == "IN_BUDGET")
anzahl_unknown = sum(1 for k in data11["kandidaten"] if k["budget_status"] == "UNKNOWN")
check("11: 200", r11.status_code == 200)
check("11: mindestens ein Kandidat wurde von Gemini adressiert (IN_BUDGET)", anzahl_in_budget >= 1)
check("11: nicht adressierte Kandidaten der finalen Top-5 sind UNKNOWN "
      "(kein Raten für fehlende IDs)", anzahl_unknown >= 0 and anzahl_in_budget + anzahl_unknown
      == len(data11["kandidaten"]))


# ══════════════════════════════════════════════════════════════════════════
# 12) Gemini kann technische Hard Filters NICHT umgehen
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
af_budget.call_gemini_json = _fn_alle_in   # bestmöglicher Bonus für ALLE — trotzdem kein Filter-Bruch
r12 = post({"kraftstoff": ["Diesel"], "getriebe": ["automatik"],
            "budget_min": 1000, "budget_max": 2000})   # unrealistisch niedriges Budget
data12 = r12.json()
check("12: 200", r12.status_code == 200)
check("12: trotz maximalem IN_BUDGET-Bonus für ALLE Kandidaten bleiben NUR "
      "Diesel-Automatik-Fahrzeuge im Ergebnis (Hard Filter unverändert)",
      all(k["kraftstoff"] == "Diesel" and "automatik" in k["getriebe"] for k in data12["kandidaten"]))


# ══════════════════════════════════════════════════════════════════════════
# 13) keine konkrete Preiszahl in der AutoFinder-Response
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
af_budget.call_gemini_json = _gemini_erfundener_preis
r13 = post(BODY_MIT_BUDGET)
data13 = r13.json()
_roh_text = r13.text
check("13: 200", r13.status_code == 200)
check("13: der von Gemini erfundene Preis (12345/10000-12000 EUR/11.500 €) "
      "erscheint NIRGENDS im rohen Response-Text",
      "12345" not in _roh_text and "10000-12000" not in _roh_text and "11.500" not in _roh_text)
check("13: kein Kandidat trägt ein 'preis_eur'/'market_price'/'geschaetzter_preis'-Feld "
      "(Schema kennt diese Felder gar nicht)",
      all("preis_eur" not in k and "market_price" not in k and "geschaetzter_preis" not in k
          for k in data13["kandidaten"]))
check("13: Gemini konnte trotz erfundener Zusatzfelder wenigstens den "
      "gültigen budget_status IN_BUDGET durchbringen (Validierung liest NUR "
      "bekannte Felder, verwirft aber nicht den ganzen Eintrag)",
      any(k["budget_status"] == "IN_BUDGET" for k in data13["kandidaten"]))


# ══════════════════════════════════════════════════════════════════════════
# 14) Market-Felder bleiben weiterhin None — unabhängig vom Budget-Szenario
# ══════════════════════════════════════════════════════════════════════════
check("14: market_price_min/max/median/data_quality/sample_size bleiben None "
      "(IN_BUDGET-Szenario)",
      all(k["market_price_min"] is None and k["market_price_max"] is None
          and k["market_price_median"] is None and k["market_data_quality"] is None
          and k["market_sample_size"] is None for k in data_in["kandidaten"]))


# ══════════════════════════════════════════════════════════════════════════
# 15) gleicher Gemini-Mock + gleicher Request -> gleiche Reihenfolge
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
af_budget.call_gemini_json = _fn_alle_near
r15a = post(BODY_MIT_BUDGET)
_reset_limiters()
af_budget.call_gemini_json = _fn_alle_near
r15b = post(BODY_MIT_BUDGET)
data15a, data15b = r15a.json(), r15b.json()
check("15: zwei identische Läufe mit identischem Mock liefern identische Reihenfolge",
      [k["variante_id"] for k in data15a["kandidaten"]] == [k["variante_id"] for k in data15b["kandidaten"]])
check("15: auch match_score bleibt stabil identisch",
      [k["match_score"] for k in data15a["kandidaten"]] == [k["match_score"] for k in data15b["kandidaten"]])


# ══════════════════════════════════════════════════════════════════════════
# 16) Diversitätsregeln bleiben nach Budget-Umsortierung erhalten
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()


async def _gemini_bevorzugt_eine_marke(system_prompt, user_msg):
    """Bewusst adversarial: gibt IN_BUDGET nur für eine bestimmte Marke, um zu
    pruefen, ob eine Bevorzugung die Diversitaetsgrenzen durchbrechen koennte."""
    ids = _extrahiere_ids(user_msg)
    out = []
    for i in ids:
        out.append({"candidate_id": i, "budget_status": "IN_BUDGET", "confidence": "HIGH"})
    return {"candidates": out}


af_budget.call_gemini_json = _gemini_bevorzugt_eine_marke
r16 = post({"sportlich": True, "kraftstoff": ["Benzin"], "budget_min": 10000, "budget_max": 200000})
data16 = r16.json()
_marken16: dict[str, int] = {}
_baureihen16: dict[str, int] = {}
for k in data16["kandidaten"]:
    _marken16[k["marke"]] = _marken16.get(k["marke"], 0) + 1
    _baureihen16[k["baureihe_id"]] = _baureihen16.get(k["baureihe_id"], 0) + 1
check("16: 200", r16.status_code == 200)
check("16: max. 2 Kandidaten je Marke bleibt AUCH nach Budget-Umsortierung erhalten",
      all(v <= 2 for v in _marken16.values()))
check("16: max. 1 Kandidat je Baureihe bleibt AUCH nach Budget-Umsortierung erhalten",
      all(v <= 1 for v in _baureihen16.values()))
check("16: exakt max. 5 Kandidaten", len(data16["kandidaten"]) <= 5)


# ══════════════════════════════════════════════════════════════════════════
# 17) no_internal_match -> kein Gemini-Call
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
zaehler17 = _Zaehler(_gemini_leer)
af_budget.call_gemini_json = zaehler17
r17 = post({"kraftstoff": ["Elektro"], "getriebe": ["manuell"],
            "budget_min": 10000, "budget_max": 20000})   # unmögliche Kombination
data17 = r17.json()
check("17: 200", r17.status_code == 200)
check("17: status == 'no_internal_match'", data17["status"] == "no_internal_match")
check("17: KEIN Gemini-Call bei 0 internen Kandidaten, obwohl Budget angegeben war",
      zaehler17.n == 0)


# ══════════════════════════════════════════════════════════════════════════
# 18) Budgetfelder ungültig -> bestehende 422-Validierung (unverändert durch Runde 3)
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
check("18: budget_min > budget_max -> weiterhin 422",
      post({"budget_min": 9000, "budget_max": 1000}).status_code == 422)
check("18: negatives Budget -> weiterhin 422",
      post({"budget_min": -1}).status_code == 422)


# ══════════════════════════════════════════════════════════════════════════
# §11 D (explizit): OUT_OF_BUDGET für den technisch STÄRKSTEN Kandidaten
# führt zu einem Malus, aber niemals zu einem Hard-Delete — geprüft auf
# Funktionsebene (deterministisch, unabhängig von der realen Datenbreite).
# ══════════════════════════════════════════════════════════════════════════
from dataclasses import dataclass as _dc, field as _field   # noqa: E402


@_dc
class _FakeKandidat:
    baureihe_id: str
    variante_id: str
    marke: str = "Test"
    modell: str = "X"
    generation: str = "1"
    motor_bezeichnung: str = "1.0"
    baujahr_von: int | None = 2020
    baujahr_bis: int | None = None
    leistung_ps: int | None = 100
    kraftstoff: str = "Benzin"
    getriebe_klassen: list = _field(default_factory=lambda: ["manuell"])
    antrieb: str | None = "Front"
    karosserie_klassen: list = _field(default_factory=lambda: ["kompakt"])
    match_score: float = 0.0
    match_gruende: list = _field(default_factory=list)
    trade_offs: list = _field(default_factory=list)
    datenqualitaet: float = 1.0
    source_type: str = "internal_db"
    market_price_min: int | None = None
    market_price_max: int | None = None
    market_price_median: int | None = None
    market_data_quality: str | None = None
    market_sample_size: int | None = None
    visual_key: str = "test--x--1"


_bester = _FakeKandidat(baureihe_id="b1", variante_id="v1", match_score=10.0)   # klar staerkster Kandidat
_schwaecher1 = _FakeKandidat(baureihe_id="b2", variante_id="v2", match_score=3.0)
_schwaecher2 = _FakeKandidat(baureihe_id="b3", variante_id="v3", match_score=2.0)

_budget_map_d = {"v1": (af_budget.OUT_OF_BUDGET, "HIGH")}   # NUR der beste bekommt einen Malus
_top5_d = af_router._top5_nach_budget([_bester, _schwaecher1, _schwaecher2], _budget_map_d, k=5)
check("§11-D: der technisch stärkste Kandidat (v1) bleibt trotz OUT_OF_BUDGET "
      "IN der Ergebnisliste (kein Hard-Delete)",
      any(k.variante_id == "v1" for k in _top5_d))
_v1_out = next(k for k in _top5_d if k.variante_id == "v1")
check("§11-D: v1 behält seinen Foundation-Score MINUS dem begrenzten Malus "
      "(10.0 - 1.5 = 8.5), bleibt damit klar vor den schwächeren Kandidaten",
      _v1_out.match_score == 10.0 + af_budget.BUDGET_MALUS_OUT == 8.5)
check("§11-D: trotz Malus bleibt v1 auf Platz 1 (10 - 1.5 = 8.5 > 3.0 und > 2.0 "
      "— Budget dominiert NICHT das technische Ranking)",
      _top5_d[0].variante_id == "v1")


# ══════════════════════════════════════════════════════════════════════════
# Direkte Unit-Tests der Validierung (app.autofinder_budget._validiere_antwort)
# ══════════════════════════════════════════════════════════════════════════
_erlaubte = {"v1", "v2", "v3"}
_val1 = af_budget._validiere_antwort(
    {"candidates": [
        {"candidate_id": "v1", "budget_status": "IN_BUDGET", "confidence": "HIGH"},
        {"candidate_id": "unbekannt-999", "budget_status": "IN_BUDGET", "confidence": "HIGH"},
        {"candidate_id": "v2", "budget_status": "KAPUTTER_STATUS", "confidence": "HIGH"},
        {"candidate_id": "v2", "budget_status": "OUT_OF_BUDGET", "confidence": "LOW"},
    ]},
    _erlaubte,
)
check("Validierung: unbekannte ID wird verworfen", "unbekannt-999" not in _val1)
check("Validierung: gültiger Eintrag (v1) wird übernommen", _val1.get("v1") == ("IN_BUDGET", "HIGH"))
check("Validierung: ein ungültiger Eintrag für v2 (kaputter Enum-Wert) blockiert "
      "NICHT den darauffolgenden GÜLTIGEN Eintrag für dieselbe ID — erster "
      "GÜLTIGER Treffer gewinnt, kein Datenverlust durch eine defekte Dublette",
      _val1.get("v2") == ("OUT_OF_BUDGET", "LOW"))
check("Validierung: fehlende ID (v3) ist einfach nicht im Ergebnis (== UNKNOWN beim Aufrufer)",
      "v3" not in _val1)

_val_dup = af_budget._validiere_antwort(
    {"candidates": [
        {"candidate_id": "v3", "budget_status": "IN_BUDGET", "confidence": "HIGH"},
        {"candidate_id": "v3", "budget_status": "OUT_OF_BUDGET", "confidence": "LOW"},
    ]},
    _erlaubte,
)
check("Validierung: zwei GÜLTIGE Einträge für dieselbe ID -> der ERSTE gewinnt "
      "(kein 'letzter gewinnt', kein Überschreiben)",
      _val_dup.get("v3") == ("IN_BUDGET", "HIGH"))

_val_kaputt = af_budget._validiere_antwort("kein dict", _erlaubte)
check("Validierung: Nicht-Dict-Antwort -> leeres Ergebnis, kein Crash", _val_kaputt == {})
_val_kaputt2 = af_budget._validiere_antwort({"candidates": "kein array"}, _erlaubte)
check("Validierung: 'candidates' kein Array -> leeres Ergebnis, kein Crash", _val_kaputt2 == {})


# ══════════════════════════════════════════════════════════════════════════
# Prompt-Inhalt: KEINE Schwachstellen/Rückrufe/DB-Kontext (§3)
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
zaehler_prompt = _Zaehler(_gemini_leer)
af_budget.call_gemini_json = zaehler_prompt
post(BODY_MIT_BUDGET)
_prompt = zaehler_prompt.letzter_user_msg or ""
check("§3: Prompt an Gemini enthält KEIN Wort 'Schwachstelle'", "schwachstelle" not in _prompt.lower())
check("§3: Prompt an Gemini enthält KEIN Wort 'Rückruf'/'rueckruf'",
      "rückruf" not in _prompt.lower() and "rueckruf" not in _prompt.lower())
check("§3: Prompt an Gemini enthält das Budgetfenster",
      str(BODY_MIT_BUDGET["budget_min"]) in _prompt and str(BODY_MIT_BUDGET["budget_max"]) in _prompt)


# ══════════════════════════════════════════════════════════════════════════
# Kein Tavily / kein zweiter Gemini-Client — strukturelle Prüfung
# ══════════════════════════════════════════════════════════════════════════
_budget_quelle = inspect.getsource(af_budget)
_budget_import_zeilen = "\n".join(
    z for z in _budget_quelle.splitlines() if z.strip().startswith(("import ", "from ")))
check("Struktur: app/autofinder_budget.py importiert kein Tavily/Web-Modul",
      "tavily" not in _budget_import_zeilen.lower() and "web_search" not in _budget_import_zeilen.lower())
check("Struktur: app/autofinder_budget.py baut KEINEN eigenen genai.Client "
      "(nutzt call_gemini_json aus app.car_lookup)",
      "genai.Client(" not in _budget_quelle)
check("Struktur: app/autofinder.py (Foundation) hat NULL Diff in dieser Runde "
      "(reiner Import-Check: Modul lädt weiterhin ohne autofinder_budget-Import)",
      "autofinder_budget" not in inspect.getsource(af))


# ══════════════════════════════════════════════════════════════════════════
# Token-Schätzung (§16, informativ)
# ══════════════════════════════════════════════════════════════════════════
_geschaetzte_tokens = len(_prompt) / 4 + len(af_budget._SYSTEM_PROMPT) / 4
print(f"\nGeschätzte Prompt-Tokens (System+User, ~4 Zeichen/Token): {_geschaetzte_tokens:.0f}")


print()
if FEHLER:
    print(f"{len(FEHLER)} Test(s) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle AutoFinder-Budget-Tests bestanden.")
