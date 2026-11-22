"""
AF-01 -- Mindestpassung gilt fuer den tatsaechlich ausgegebenen, budgetkorrigierten user_fit (offline).

    python scripts/test_isolated.py test_autofinder_mindestpassung.py

Produktiver Endpunkt, echte Engine, echte Fit-Berechnung (`berechne_fit` wird nur DURCHGEREICHT und der Rohwert
aufgezeichnet, kein Fit-Stub). Nur die Providerantworten sind gestubbt, und zwar konsistent: Budgetstatus und
Preisspanne passen zusammen (die produktive Konsolidierung bestaetigt sie, ueberschreibt sie nie). Tavily ist verboten.

NACHWEISGRENZE: Das Kontingent (`require_autofinder_kontingent`) ist auf einen No-op gesetzt, damit mehrere Anfragen in
einem Prozess laufen. Es gehoert nicht zur geprueften Kette. Die Gemini-Antworten sind Annahmen, keine echten Modellwerte.
"""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi.testclient import TestClient

import app.autofinder_budget as afb
import app.autofinder_enrich as afe
import app.autofinder_web as afw
import app.routers.autofinder as R
from app.autofinder_fit import FIT_SCHWELLE
from app.main import app as _app

FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f"  -> {detail}" if not bedingung and detail else ""))
    if not bedingung:
        FEHLER.append(name)


RAW: dict[str, int] = {}
_orig_fit = R.berechne_fit


def _rec(kand, req):
    f = _orig_fit(kand, req)
    RAW[R.kandidat_id(kand)] = f.score
    return f


R.berechne_fit = _rec
R.require_autofinder_kontingent = lambda request: None   # Nachweisgrenze, siehe Modulkopf

CALLS = {"budget": 0, "enrich": 0, "tavily": 0}
BUDGET_IDS: list[str] = []
MODE: dict = {}


async def _tavily_verboten(*a, **k):
    CALLS["tavily"] += 1
    raise AssertionError("Tavily verboten")


afw.tavily_search_mit_status = _tavily_verboten


async def _budget(sp, um):
    CALLS["budget"] += 1
    ids = re.findall(r"candidate_id=(\S+)", um)
    BUDGET_IDS[:] = ids
    return {"candidates": [{"candidate_id": i, "budget_status": MODE["status"], "confidence": "MEDIUM"} for i in ids]}


async def _enrich(sp, um):
    CALLS["enrich"] += 1
    ids = re.findall(r"candidate_id=(\S+)", um)
    return {"candidates": [{"candidate_id": i, "why_fits": ["Grund eins", "Grund zwei", "Grund drei"],
                            "trade_offs": ["Nachteil eins", "Nachteil zwei"],
                            "estimated_price_min": 18000, "estimated_price_max": 25000,
                            "price_confidence": "MEDIUM"} for i in ids]}


afb.call_gemini_json = _budget
afe.call_gemini_json = _enrich
client = TestClient(_app)
H = {"Authorization": "Bearer dev-key-change-in-prod"}

# Konsistente Stub-Paare (Status <-> Spanne 18.000-25.000 EUR, MEDIUM)
OUT = ("OUT_OF_BUDGET", dict(budget_max=8000))
NEAR = ("NEAR_BUDGET", dict(budget_max=20000))
IN = ("IN_BUDGET", dict(budget_max=30000))
ABZUG = {"OUT_OF_BUDGET": 12, "NEAR_BUDGET": 4, "IN_BUDGET": 0}

GEMISCHT = {"sportlich": True, "nutzung": "stadt", "sparsam": True}          # Roh-Fit 87..95
ALLE_UNTER = {"fahranfaenger": True, "sportlich": True}                      # Roh-Fit 84
FILTER = {"sportlich": True, "nutzung": "stadt", "sparsam": True, "kraftstoff": ["Benzin"], "karosserie": ["kompakt"]}


def lauf(anfrage: dict, fall: tuple | None):
    RAW.clear()
    CALLS.update(budget=0, enrich=0, tavily=0)
    BUDGET_IDS.clear()
    body = dict(anfrage)
    if fall:
        MODE["status"] = fall[0]
        body.update(fall[1])
    r = client.post("/api/v1/autofinder", headers=H, json=body)
    assert r.status_code == 200, r.text
    return r.json(), dict(CALLS)


def zeile(d):
    return [(k["marke"] + " " + k["modell"], RAW.get(k["candidate_id"]), k["user_fit"]) for k in d["kandidaten"]]


print("\n=== A) OUT, gemischter Fall: passende Karten bleiben, schwaechere entfallen ===")
d, calls = lauf(GEMISCHT, OUT)
pool = list(BUDGET_IDS)
erwartet = [i for i in pool if RAW[i] - ABZUG["OUT_OF_BUDGET"] >= FIT_SCHWELLE]
ausgegeben = [k["candidate_id"] for k in d["kandidaten"]]
print("   Pool (bewertet):", [(i.split("--")[1] if "--" in i else i, RAW[i], RAW[i] - 12) for i in pool])
print("   ausgegeben:", zeile(d))
check("A1 Testvoraussetzung: Pool enthaelt Karten ueber UND unter der Grenze (echter Roh-Fit, kein Fit-Stub)",
      any(RAW[i] - 12 >= FIT_SCHWELLE for i in pool) and any(RAW[i] - 12 < FIT_SCHWELLE for i in pool)
      and all(RAW[i] >= FIT_SCHWELLE for i in pool))
check("A2 keine ausgegebene Karte unter FIT_SCHWELLE", all(k["user_fit"] >= FIT_SCHWELLE for k in d["kandidaten"]))
check("A3 ausgegeben werden genau die Pool-Karten mit Endwert >= Schwelle, in unveraenderter Reihenfolge",
      ausgegeben == erwartet, f"{ausgegeben} != {erwartet}")
check("A4 user_fit ist Roh-Fit minus Budgetabzug (nichts angehoben)",
      all(k["user_fit"] == RAW[k["candidate_id"]] - 12 for k in d["kandidaten"]))
check("A5 Budget-Label/Status der verbleibenden Karten: OUT_OF_BUDGET, Spanne vorhanden",
      all(k["budget_status"] == "OUT_OF_BUDGET" and k["estimated_price_min"] == 18000 for k in d["kandidaten"]))
check("A6 status ok, kein Auffueller: weniger als 5 Karten", d["status"] == "ok" and 0 < len(d["kandidaten"]) < 5)
check("A7 genau je EIN Budget- und Enrichment-Call, kein Tavily, keine Nachsuche",
      calls == {"budget": 1, "enrich": 1, "tavily": 0}, str(calls))

print("\n=== B) OUT, alle unter der Grenze: no_strong_match mit zutreffendem Hinweis ===")
d, calls = lauf(ALLE_UNTER, OUT)
pool = list(BUDGET_IDS)
print("   Pool:", [(RAW[i], RAW[i] - 12) for i in pool])
txt = " ".join(d["warnings"]).lower()
check("B1 Testvoraussetzung: alle Pool-Karten bestehen das Roh-Gate und fallen nach Abzug darunter",
      pool and all(RAW[i] >= FIT_SCHWELLE and RAW[i] - 12 < FIT_SCHWELLE for i in pool))
check("B2 keine Karte, status no_strong_match (nicht no_internal_match)",
      d["kandidaten"] == [] and d["status"] == "no_strong_match", d["status"])
check("B3 Hinweis nennt fehlenden starken Treffer, behauptet NICHT 'keine passenden Fahrzeuge'",
      "keinen wirklich starken treffer" in txt and "keinen passenden treffer" not in txt, txt)
check("B4 kein zusaetzlicher Provideraufruf", calls == {"budget": 1, "enrich": 1, "tavily": 0}, str(calls))

print("\n=== C) NEAR mit Endwert genau FIT_SCHWELLE bleibt ===")
d, calls = lauf(ALLE_UNTER, NEAR)
print("   ausgegeben:", zeile(d))
check("C1 Roh-Fit 84 - 4 = 80: alle Karten bleiben", len(d["kandidaten"]) >= 1
      and all(k["user_fit"] == FIT_SCHWELLE for k in d["kandidaten"]))
check("C2 status ok, NEAR_BUDGET",
      d["status"] == "ok" and all(k["budget_status"] == "NEAR_BUDGET" for k in d["kandidaten"]))

print("\n=== D) IN-Gegenfall und Fall ohne Budget: unveraendert ===")
d_in, calls_in = lauf(GEMISCHT, IN)
print("   IN:", zeile(d_in))
check("D1 IN: fuenf Karten, kein Abzug (user_fit == Roh-Fit), alle >= Schwelle",
      len(d_in["kandidaten"]) == 5 and all(k["user_fit"] == RAW[k["candidate_id"]] for k in d_in["kandidaten"]))
d_ohne, calls_ohne = lauf(GEMISCHT, None)
print("   ohne Budget:", zeile(d_ohne))
check("D2 ohne Budget: fuenf Karten, user_fit == Roh-Fit, Budget nicht aufgerufen",
      len(d_ohne["kandidaten"]) == 5 and all(k["user_fit"] == RAW[k["candidate_id"]] for k in d_ohne["kandidaten"])
      and calls_ohne["budget"] == 0)
check("D3 IN und ohne Budget geben dieselben Karten in derselben Reihenfolge aus",
      [k["candidate_id"] for k in d_in["kandidaten"]] == [k["candidate_id"] for k in d_ohne["kandidaten"]])

print("\n=== E) Hard-Filter, Ausgabelimit, Provideranzahl ===")
d_f, calls_f = lauf(FILTER, OUT)
d_f_in, calls_f_in = lauf(FILTER, IN)
print("   Filter OUT:", zeile(d_f), "| IN:", zeile(d_f_in))
check("E1 Hard-Filter bleiben erhalten (Benzin, kompakt) in beiden Faellen",
      all(k["kraftstoff"] == "Benzin" and "kompakt" in k["karosserie"] + k["karosserie_verfuegbar"]
          for k in d_f["kandidaten"] + d_f_in["kandidaten"]))
check("E2 Ausgabelimit 5 bleibt in allen Faellen",
      all(len(x["kandidaten"]) <= 5 for x in (d, d_in, d_ohne, d_f, d_f_in)))
check("E3 Providerzahl mit Fix: Budget 1 / Enrichment 1 / Tavily 0, auch bei Ausschluss",
      calls_f == calls_f_in == {"budget": 1, "enrich": 1, "tavily": 0}, f"{calls_f} {calls_f_in}")
check("E4 verbleibende Karten behalten die Reihenfolge des IN-Laufs (Fit primaer, Budget-Tiebreak)",
      [k["candidate_id"] for k in d_f["kandidaten"]]
      == [i for i in [k["candidate_id"] for k in d_f_in["kandidaten"]] if i in {k["candidate_id"] for k in d_f["kandidaten"]}])

print()
if FEHLER:
    print(f"{len(FEHLER)} Pruefung(en) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle AF-01-Pruefungen bestanden.")
