"""
Test: AutoFinder-HTTP-API (Runde 2) — POST /api/v1/autofinder

Testmatrix A-R der Produktspezifikation gegen den echten FastAPI-Router (via
TestClient), auf einer frisch gebootstrappten kanonischen DB. Deckt: HTTP-
Vertrag, kostenlos/kein Credit-Verbrauch, Score-Safety (Router sortiert nicht
nach), no_internal_match, Coverage-/Diesel-Stadt-Warnungen, Input-Validierung
(422), Trust/rejected-Regression, Determinismus, Data-Scope-Hinweis, und dass
Runde 2 strukturell weiterhin 0 externe Calls macht.

Runde 3 (Budget-Plausibilität) fügt einen ECHTEN Gemini-Call in den Endpunkt
ein, sobald ein Budget angegeben wird — deshalb wird `call_gemini_json` hier
global auf einen neutralen Stub gepatcht (siehe unten), der IMMER eine leere
Kandidatenliste liefert (-> alle Kandidaten bleiben budget_status=UNKNOWN,
budget_adjustment=0.0). Das hält diese Datei bei ihrem ursprünglichen Zweck
(HTTP-Vertrag der Runde 2, unbeeinflusst von Budget-Feinheiten) und weiterhin
vollständig netzwerkfrei. Die Budget-Feinheiten selbst (IN_BUDGET-Bonus,
Gemini-Ausfall-Fallback, Validierung kaputter Antworten, ...) deckt die
separate test_autofinder_budget.py mit gezielten Fake-Antworten ab.

Kein Netzwerk, kein LLM. Ausfuehren:  python test_autofinder_api.py
"""
import importlib
import inspect
import os
import sys
import tempfile
import time

sys.path.insert(0, ".")

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


# ── Frische kanonische DB + eigener Test-API-Key (NICHT der echte aus .env) ─
_tmp = tempfile.mkdtemp(prefix="vira_af_api_")
_db_pfad = os.path.join(_tmp, "kanonisch.db")
os.environ["AUTO_KI_DB_PATH"] = _db_pfad
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")
os.environ["AUTO_KI_API_KEY"] = "test-key-autofinder-api"   # override=False in config.py respektiert das

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
from app.rate_limit import limiter as _global_limiter   # noqa: E402


async def _stub_gemini_neutral(system_prompt: str, user_msg: str) -> dict:
    """Runde-3-Stub für diese Runde-2-Testdatei: liefert IMMER eine leere
    Kandidatenliste (kein echter Netzwerk-/Gemini-Call). Wirkung im Router:
    jeder Kandidat bleibt budget_status=UNKNOWN, budget_adjustment=0.0 — die
    Reihenfolge bleibt identisch zur reinen Foundation-Sortierung."""
    return {"candidates": []}


af_budget.call_gemini_json = _stub_gemini_neutral

client = TestClient(fastapi_app)   # OHNE `with` — kein Lifespan/Backup-Task nötig
HEADERS = {"Authorization": "Bearer test-key-autofinder-api"}
URL = "/api/v1/autofinder"


def post(body: dict, headers=HEADERS):
    return client.post(URL, json=body, headers=headers)


def _reset_limiters() -> None:
    """Testisolation, KEINE Produktionsänderung: dieses Skript feuert in wenigen
    Sekunden weit mehr Requests ab, als ein echter Nutzer in einer Minute
    absetzt, und würde sonst am bestehenden globalen Default-Limit
    (app.rate_limit.RATE_LIMIT, aktuell 20/min, greift für JEDEN Endpunkt via
    SlowAPIMiddleware) und/oder am routereigenen Limit vorbeischrammen — nicht
    weil die Korrektheits-Prüfung fehlschlägt, sondern weil das Testskript
    selbst zu viele Anfragen in zu kurzer Zeit stellt. Reset vor jedem
    Testabschnitt entkoppelt das; der dedizierte Rate-Limit-Test (Abschnitt L)
    resettet zusätzlich unmittelbar vor seinem gezielten Burst."""
    _global_limiter.reset()
    af_router.limiter.reset()


# ══════════════════════════════════════════════════════════════════════════
# A) gültige Standardsuche -> 200 + max 5 Kandidaten
# ══════════════════════════════════════════════════════════════════════════
r_a = post({})
check("A: leerer Body -> 200", r_a.status_code == 200)
data_a = r_a.json()
check("A: max. 5 Kandidaten", len(data_a["kandidaten"]) <= 5)
check("A: status ist 'ok' (416 Baureihen -> garantiert Treffer ohne Filter)",
      data_a["status"] == "ok")


# ══════════════════════════════════════════════════════════════════════════
# B) Diesel + Automatik + Kombi + >=150 PS -> passende konkrete Motoren
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
r_b = post({"kraftstoff": ["Diesel"], "getriebe": ["automatik"],
            "karosserie": ["kombi"], "leistung_min_ps": 150})
check("B: 200", r_b.status_code == 200)
data_b = r_b.json()
check("B: mindestens ein Kandidat", len(data_b["kandidaten"]) > 0)
check("B: ALLE Kandidaten sind Diesel", all(k["kraftstoff"] == "Diesel" for k in data_b["kandidaten"]))
check("B: ALLE Kandidaten haben Automatik", all("automatik" in k["getriebe"] for k in data_b["kandidaten"]))
check("B: ALLE Kandidaten haben eine konkrete Motorbezeichnung (nicht nur Baureihe)",
      all(k["motor"] for k in data_b["kandidaten"]))
check("B: ALLE Kandidaten haben >=150 PS",
      all((k["leistung_ps"] or 0) >= 150 for k in data_b["kandidaten"]))


# ══════════════════════════════════════════════════════════════════════════
# C) unmögliche Kombination -> 200 / no_internal_match / []
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
r_c = post({"kraftstoff": ["Elektro"], "getriebe": ["manuell"]})
check("C: 200 (kein Server-Fehler bei 0 Treffern)", r_c.status_code == 200)
data_c = r_c.json()
check("C: status == 'no_internal_match'", data_c["status"] == "no_internal_match")
check("C: kandidaten == []", data_c["kandidaten"] == [])
check("C: Warnung erklärt den fehlenden internen Treffer",
      any("keinen passenden Treffer" in w for w in data_c["warnings"]))


# ══════════════════════════════════════════════════════════════════════════
# D) keine Kandidaten erfunden
# ══════════════════════════════════════════════════════════════════════════
check("D: no_internal_match erfindet KEINEN Platzhalter-Kandidaten",
      len(data_c["kandidaten"]) == 0)
# total_candidates_considered muss ehrlich 0 sein, kein geschönter Wert
check("D: total_candidates_considered ist 0 bei 0 Treffern",
      data_c["total_candidates_considered"] == 0)


# ══════════════════════════════════════════════════════════════════════════
# E) Marke ausgeschlossen -> nie enthalten
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
r_e = post({"marken_ausschliessen": ["BMW"], "kraftstoff": ["Diesel"]})
check("E: 200", r_e.status_code == 200)
data_e = r_e.json()
check("E: BMW erscheint NIRGENDWO im Ergebnis",
      not any(k["marke"] == "BMW" for k in data_e["kandidaten"]))


# ══════════════════════════════════════════════════════════════════════════
# F) budget gesetzt -> noch KEIN harter Preisfilter
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
r_f_ohne = post({"kraftstoff": ["Diesel"]})
r_f_mit = post({"kraftstoff": ["Diesel"], "budget_min": 3000, "budget_max": 6000})
check("F: 200 mit Budget", r_f_mit.status_code == 200)
data_f_ohne, data_f_mit = r_f_ohne.json(), r_f_mit.json()
check("F: Budget veraendert die Trefferzahl NICHT (kein harter Preisfilter)",
      data_f_ohne["total_candidates_considered"] == data_f_mit["total_candidates_considered"])
check("F: Budget-Hinweis in den Warnungen (Transparenz statt stilles Ignorieren)",
      any("Budget" in w for w in data_f_mit["warnings"]))
check("F: OHNE Budget-Angabe erscheint der Budget-Hinweis NICHT",
      not any("Budget" in w for w in data_f_ohne["warnings"]))


# ══════════════════════════════════════════════════════════════════════════
# G) market fields -> alle None
# ══════════════════════════════════════════════════════════════════════════
check("G: market_price_min/max/median/data_quality/sample_size sind None",
      all(k["market_price_min"] is None and k["market_price_max"] is None
          and k["market_price_median"] is None and k["market_data_quality"] is None
          and k["market_sample_size"] is None for k in data_a["kandidaten"]))
check("G: such_filter_hinweis ist None (§14 nicht in Runde 2 befuellt)",
      all(k["such_filter_hinweis"] is None for k in data_a["kandidaten"]))


# ══════════════════════════════════════════════════════════════════════════
# H) source_type -> internal_db
# ══════════════════════════════════════════════════════════════════════════
check("H: ALLE Kandidaten haben source_type == 'internal_db'",
      all(k["source_type"] == "internal_db" for k in data_a["kandidaten"]))


# ══════════════════════════════════════════════════════════════════════════
# I) visual_key -> vorhanden
# ══════════════════════════════════════════════════════════════════════════
check("I: ALLE Kandidaten haben einen nicht-leeren visual_key",
      all(k["visual_key"] for k in data_a["kandidaten"]))


# ══════════════════════════════════════════════════════════════════════════
# J) kein Login-/Check-Credit nötig
# ══════════════════════════════════════════════════════════════════════════
_router_quelle = inspect.getsource(af_router)
# Praezise auf tatsaechliche Nutzung pruefen (Depends()/Funktionsaufruf), NICHT
# auf blossen Substring-Treffer — der Router-Docstring erklaert bewusst in Prosa,
# dass KEIN require_check_access verwendet wird ("... ist bewusst OHNE Check-
# Gate: kein `require_check_access`, ..."), ein naiver Substring-Test wuerde
# genau diese Erklaerung als falschen Treffer werten.
_endpoint_quelle = inspect.getsource(af_router.autofinder_endpunkt)
check("J: Endpoint-Funktion nutzt KEIN Depends(require_check_access) / "
      "Depends(get_current_user_id) als Parameter",
      "Depends(require_check_access)" not in _endpoint_quelle
      and "Depends(get_current_user_id)" not in _endpoint_quelle
      and "require_check_access" not in inspect.signature(af_router.autofinder_endpunkt).parameters)
check("J: Router importiert app.check_gate NICHT (kein Depends-Auth-Gate ueberhaupt verfuegbar)",
      "from app.check_gate import" not in _router_quelle
      and "import app.check_gate" not in _router_quelle)
check("J: Anfrage OHNE Cookie/Login-Session liefert trotzdem 200 (nur API-Key)",
      post({}).status_code == 200)


# ══════════════════════════════════════════════════════════════════════════
# K) kein Credit-Verbrauch
# ══════════════════════════════════════════════════════════════════════════
check("K: Router importiert refund_check_credit NICHT und schreibt nicht auf "
      "checks_verbleibend",
      "refund_check_credit" not in _router_quelle and "checks_verbleibend" not in _router_quelle)


# ══════════════════════════════════════════════════════════════════════════
# M) ungültige Min/Max-Werte -> 422
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
check("M: leistung_min_ps > leistung_max_ps -> 422",
      post({"leistung_min_ps": 300, "leistung_max_ps": 100}).status_code == 422)
check("M: budget_min > budget_max -> 422",
      post({"budget_min": 9000, "budget_max": 1000}).status_code == 422)
check("M: baujahr_von > baujahr_bis -> 422",
      post({"baujahr_von": 2020, "baujahr_bis": 2010}).status_code == 422)
check("M: negative PS -> 422", post({"leistung_min_ps": -50}).status_code == 422)
check("M: negatives Budget -> 422", post({"budget_min": -1}).status_code == 422)
check("M: negative km/Jahr -> 422", post({"km_pro_jahr": -1}).status_code == 422)
check("M: ungueltiger Enum-Wert bei nutzung -> 422",
      post({"nutzung": "mondflug"}).status_code == 422)
check("M: ungueltiger Enum-Wert bei kraftstoff -> 422",
      post({"kraftstoff": ["Kohle"]}).status_code == 422)
check("M: ungueltiger Enum-Wert bei getriebe -> 422",
      post({"getriebe": ["Zaubergetriebe"]}).status_code == 422)
check("M: ungueltiger Enum-Wert bei karosserie -> 422",
      post({"karosserie": ["Bratpfanne"]}).status_code == 422)
check("M: ungueltiger Enum-Wert bei antrieb -> 422",
      post({"antrieb": ["Antigravitation"]}).status_code == 422)
check("M: leere optionale Listen sind gueltig (200)",
      post({"karosserie": [], "kraftstoff": []}).status_code == 200)
check("M: unbekannte Marke crasht nicht (200, einfach 0 Zusatzwirkung/kein Treffer)",
      post({"marken_bevorzugt": ["Trabant-Deluxe-Werke"]}).status_code == 200)
check("M: Umlaute/Unicode im Markenfeld werden sauber verarbeitet (200)",
      post({"marken_ausschliessen": ["Škoda", "Citroën"]}).status_code == 200)


# ══════════════════════════════════════════════════════════════════════════
# N) Stadt + geringe km + Diesel -> Warning vorhanden
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
r_n = post({"nutzung": "stadt", "km_pro_jahr": 5000, "kraftstoff": ["Diesel"]})
check("N: 200", r_n.status_code == 200)
data_n = r_n.json()
check("N: Diesel-Stadt-Kurzstrecke-Warnung vorhanden",
      any("Diesel" in w and "Stadtverkehr" in w for w in data_n["warnings"]))
check("N: Diesel bleibt trotzdem als harter Filter aktiv (keine heimliche Überschreibung)",
      all(k["kraftstoff"] == "Diesel" for k in data_n["kandidaten"]))


# ══════════════════════════════════════════════════════════════════════════
# O) Stadt + geringe km + Kraftstoff egal -> Diesel nicht künstlich bevorzugt
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
r_o = post({"nutzung": "stadt", "km_pro_jahr": 5000})
check("O: 200", r_o.status_code == 200)
data_o = r_o.json()
check("O: OHNE explizit gewählten Diesel erscheint KEINE Diesel-Stadt-Warnung "
      "(Warnung nur bei ausdrücklichem Diesel-Wunsch)",
      not any("Diesel" in w for w in data_o["warnings"]))
# Score-strukturelle Kontrolle: der 'stadt'-Zweig in der Engine vergibt Punkte
# NUR für Benzin/Elektro/Mild-Hybrid — Diesel bekommt strukturell 0 Bonus.
_score_quelle = inspect.getsource(af._score_kandidat)
_stadt_block = _score_quelle.split('elif req.nutzung == "stadt":')[1].split("\n\n")[0]
check("O: der 'stadt'-Score-Zweig der Engine nennt 'Diesel' an KEINER Stelle "
      "(strukturell kein Diesel-Bonus möglich)",
      "Diesel" not in _stadt_block)


# ══════════════════════════════════════════════════════════════════════════
# P) Trust/rejected Regression
# ══════════════════════════════════════════════════════════════════════════
import app.database as _database
_bmw_g20 = _database.get_baureihe("BMW", "3er", "G20/G21")
check("P: Testvoraussetzung — BMW 3er G20/G21 gefunden", _bmw_g20 is not None)
if _bmw_g20:
    check("P: bekannter rejected Fakt (id=15, 'Bremsen') ist über die API-Datenquelle "
          "NICHT sichtbar", not any(s.get("id") == 15 for s in _bmw_g20.get("schwachstellen_baureihe", [])))
_reset_limiters()
r_p = post({"kraftstoff": ["Diesel"], "getriebe": ["automatik"], "leistung_min_ps": 150})
_alle_trade_offs = [t for k in r_p.json()["kandidaten"] for t in k["trade_offs"]]
check("P: 'Bremsen' (rejected) erscheint in KEINEM Trade-off der Antwort",
      not any("bremsen" in t.lower() for t in _alle_trade_offs))


# ══════════════════════════════════════════════════════════════════════════
# Q) gleicher Request zweimal -> identische Kandidatenreihenfolge
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
_body_q = {"kraftstoff": ["Diesel"], "getriebe": ["automatik"], "leistung_min_ps": 150}
r_q1 = post(_body_q).json()
r_q2 = post(_body_q).json()
check("Q: zwei identische Requests liefern identische variante_id-Reihenfolge",
      [k["variante_id"] for k in r_q1["kandidaten"]] == [k["variante_id"] for k in r_q2["kandidaten"]])
check("Q: auch match_score bleibt stabil identisch",
      [k["match_score"] for k in r_q1["kandidaten"]] == [k["match_score"] for k in r_q2["kandidaten"]])


# ══════════════════════════════════════════════════════════════════════════
# R) Data-Scope-Hinweis vorhanden
# ══════════════════════════════════════════════════════════════════════════
check("R: data_scope_hint ist gesetzt und nennt die 416 Baureihen",
      "416" in data_a["data_scope_hint"] and "VIRA" in data_a["data_scope_hint"])
check("R: data_scope_hint behauptet NICHT, die beste Marktauswahl zu sein",
      "beste" not in data_a["data_scope_hint"].lower()
      and "gesamten markt" not in data_a["data_scope_hint"].lower())
check("R: data_scope_hint auch bei no_internal_match vorhanden",
      "416" in data_c["data_scope_hint"])


# ══════════════════════════════════════════════════════════════════════════
# §13: strukturell 0 externe Calls in Runde 2
# ══════════════════════════════════════════════════════════════════════════
# Praezise auf die tatsaechlichen Import-Zeilen pruefen (nicht auf den
# gesamten Quelltext inkl. Docstrings/Kommentaren, die die AN-/ABWESENHEIT von
# Tavily/Gemini bewusst in Prosa erklaeren und sonst selbst als Treffer zaehlen
# wuerden).
_import_zeilen = [z for z in _router_quelle.splitlines()
                   if z.strip().startswith(("import ", "from "))]
_imports_text = "\n".join(_import_zeilen).lower()
check("§13: Router importiert kein Tavily/Gemini/Web-Modul",
      not any(w in _imports_text for w in
              ("tavily", "gemini", "genai", "web_search", "requests", "httpx")))


# ══════════════════════════════════════════════════════════════════════════
# S) Coverage-Warning — gezielter, realer Fall mit genau 2 internen
# Kandidaten (Runde-2-P2-Cleanup, bisher nur implementiert, nicht getestet).
# Realer Filter: Plug-in-Hybrid + Automatik + >=600 PS trifft in der
# kanonischen DB exakt auf zwei Fahrzeuge (AMG S63 E Performance 802 PS,
# AMG C63 E Performance 680 PS) — deterministisch reproduzierbar, kein
# konstruiertes Fixture.
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
_body_low = {"kraftstoff": ["Plug-in-Hybrid"], "getriebe": ["automatik"], "leistung_min_ps": 600}
r_low = post(_body_low)
check("S: 200 bei niedriger Coverage", r_low.status_code == 200)
data_low = r_low.json()
check("S: status == 'ok' (NICHT no_internal_match — Kandidaten SIND vorhanden)",
      data_low["status"] == "ok")
check("S: genau 2 Kandidaten (kein erfundener Auffüller)",
      len(data_low["kandidaten"]) == 2)
check("S: total_candidates_considered == 2 (ehrlich, nicht beschönigt)",
      data_low["total_candidates_considered"] == 2)
check("S: Coverage-Warnung vorhanden",
      any("wenige" in w.lower() for w in data_low["warnings"]))
check("S: beide Kandidaten sind echte DB-Fahrzeuge (Plug-in-Hybrid, Automatik, >=600 PS)",
      all(k["kraftstoff"] == "Plug-in-Hybrid" and "automatik" in k["getriebe"]
          and (k["leistung_ps"] or 0) >= 600 for k in data_low["kandidaten"]))

# Kontrastprobe 1: 0 Treffer -> no_internal_match, NICHT die Low-Coverage-Warnung
_reset_limiters()
r_null = post({"kraftstoff": ["Plug-in-Hybrid"], "getriebe": ["automatik"], "leistung_min_ps": 900})
data_null = r_null.json()
check("S: 0 Treffer -> status == 'no_internal_match' (nicht 'ok' mit Warnung)",
      data_null["status"] == "no_internal_match")
check("S: 0 Treffer -> KEINE Low-Coverage-Warnung, sondern die no_internal_match-Meldung",
      not any("wenige" in w.lower() for w in data_null["warnings"]))

# Kontrastprobe 2: großzügige Trefferzahl -> KEINE falsche Low-Coverage-Warnung
_reset_limiters()
r_viele = post({"kraftstoff": ["Plug-in-Hybrid"], "getriebe": ["automatik"], "leistung_min_ps": 300})
data_viele = r_viele.json()
check("S: Testvoraussetzung — >=3 interne Treffer vor Diversität",
      data_viele["total_candidates_considered"] >= 3)
check("S: bei ausreichender Coverage erscheint KEINE Low-Coverage-Warnung",
      not any("wenige" in w.lower() for w in data_viele["warnings"]))
check("S: bei ausreichender Coverage bleibt status == 'ok'", data_viele["status"] == "ok")


# ══════════════════════════════════════════════════════════════════════════
# Score-Safety (§10): filters_applied spiegelt exakt die Eingabe, keine
# versteckte Router-Logik verändert die Filter selbst.
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
r_safety = post({"kraftstoff": ["Diesel"], "leistung_min_ps": 150})
data_safety = r_safety.json()
check("Score-Safety: filters_applied nennt genau die uebergebenen Werte",
      data_safety["filters_applied"].get("kraftstoff") == ["Diesel"]
      and data_safety["filters_applied"].get("leistung_min_ps") == 150)


# ══════════════════════════════════════════════════════════════════════════
# Performance (§14) — Ziel: warm deutlich unter 150ms
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
post({"kraftstoff": ["Diesel"]})  # Cache aufwaermen
_zeiten = []
for _ in range(5):
    _t0 = time.perf_counter()
    post({"kraftstoff": ["Diesel"], "getriebe": ["automatik"]})
    _zeiten.append((time.perf_counter() - _t0) * 1000)
print(f"\nHTTP-Performance (Cache warm, 5 Läufe): {[round(z, 1) for z in _zeiten]} ms")
check("Performance: warm unter 150ms (§14)", max(_zeiten) < 150.0)


# ══════════════════════════════════════════════════════════════════════════
# L) Rate Limit aktiv — BEWUSST ALS LETZTER, ISOLIERTER TEST: reset() räumt
# zuerst das durch die vorherigen ~30 Korrektheits-Requests bereits verbrauchte
# Kontingent weg, damit dieser Test ausschließlich den Rate-Limit-MECHANISMUS
# prüft, statt zufällig von der Anfragenzahl der Tests davor abzuhängen.
# ══════════════════════════════════════════════════════════════════════════
check("L: Endpoint traegt einen @limiter.limit(...)-Decorator",
      "@limiter.limit(" in _router_quelle)
# P2-Cleanup: der routereigene Wert spiegelt bewusst exakt das globale
# Default-Limit (20/min) — siehe Kommentar in app/routers/autofinder.py, warum
# ein hoeherer lokaler Wert durch die globale SlowAPIMiddleware ohnehin nie
# wirksam waere. Der Test prueft deshalb explizit gegen 20/min, nicht gegen
# einen fiktiven groesseren Wert.
check("L: routereigener Rate-Limit-Wert ist konsistent mit dem tatsaechlich "
      "wirksamen globalen Default (20/minute) — keine irreführende Konfiguration",
      "_AUTOFINDER_RATE_LIMIT = \"20/minute\"" in _router_quelle)
_reset_limiters()
_codes = [post({}).status_code for _ in range(25)]
check("L: bei 25 Anfragen in Folge (Limit 20/min) greift das Rate-Limit "
      "(mind. ein 429)", 429 in _codes)
check("L: die ERSTEN 20 Anfragen sind NICHT limitiert (kein zu aggressives "
      "Limit)", all(c == 200 for c in _codes[:20]))


print()
if FEHLER:
    print(f"{len(FEHLER)} Test(s) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle AutoFinder-API-Tests bestanden.")
