"""
Test: AutoFinder Top-5-Enrichment — app/autofinder_enrich.py

Deckt:
  PRICE  F) Preisrange parsebar   G) min < max   H) kein Begriff "Marktpreis"
         I) kein Portal-Call      J) Low Confidence sauber
         K) Gemini Failure bricht Suche nicht
  CONTENT T) >=3 why_fits bei erfolgreichem Enrichment
          U) Trade-offs vorhanden wenn sinnvoll
          V) rejected/untrusted weakness nicht als konkreter Mangel
          W) String "(ungeprüft)" NICHT im Consumer Result

Netzwerk/LLM werden gefaked. Ausführen:  python test_autofinder_enrich.py
"""
import os
import sys
import tempfile
import importlib
import inspect

sys.path.insert(0, ".")
FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


_tmp = tempfile.mkdtemp(prefix="vira_af_enrich_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "k.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")
import app.config as _cfg; importlib.reload(_cfg)
import app.database as _db; importlib.reload(_db)
_db.ensure_tables()

import app.autofinder_enrich as afe
from app.autofinder_enrich import (
    Enrichment, deterministischer_fallback, strip_pruef_label, _validiere, _parse_preis,
)


class _K:
    def __init__(self, vid="v1", **kw):
        self.variante_id = vid
        self.candidate_id = None
        self.marke = "BMW"; self.modell = "3er"; self.generation = "G20"
        self.motor_bezeichnung = "320d"; self.baujahr_von = 2019; self.baujahr_bis = None
        self.leistung_ps = 190; self.kraftstoff = "Diesel"
        self.getriebe_klassen = ["automatik"]; self.antrieb = "Heck"
        self.karosserie_klassen = ["limousine"]
        self.match_gruende = ["Diesel eignet sich für Langstrecke"]
        self.trade_offs = []
        self.source_type = "internal_db"
        self.verbrauch_l_100km = 4.6; self.drehmoment_nm = 400; self.beschleunigung_0_100_s = 7.1
        for k, v in kw.items():
            setattr(self, k, v)


# ── H) kein "Marktpreis" im System-Prompt, Portal-Verbot ─────────────────
sp = afe._SYSTEM_PROMPT.lower()
check("H: System-Prompt verbietet 'Marktpreis'/'Marktwert' ausdrücklich",
      "marktpreis" in sp and "marktwert" in sp and "niemals" in sp)
check("H: System-Prompt fordert BREITE Spanne, keine Einzelzahl",
      "einzelzahl" in sp and ("spanne" in sp or "breite" in sp))

# ── I) kein Portal-Call — Modul importiert keinen Portal-/Scraper-Code ────
src = inspect.getsource(afe)
check("I: kein Portal/Scraping-Import (mobile.de/autoscout/tavily/requests.get)",
      not any(t in src.lower() for t in ("mobile.de", "autoscout", "import requests",
                                          "tavily", "httpx.get", "web_search")))
check("I: einziger externer Call ist call_gemini_json (bestehende Infrastruktur)",
      "from app.car_lookup import call_gemini_json" in src and src.count("call_gemini_json") <= 4)

# ── F/G) Preis parsebar, min < max, Grenzen ─────────────────────────────
check("F/G: gültige Spanne wird übernommen", _parse_preis(12000, 16000, "MEDIUM") == (12000, 16000, "MEDIUM"))
check("G: min >= max wird verworfen -> UNKNOWN", _parse_preis(16000, 12000, "HIGH") == (None, None, "UNKNOWN"))
check("G: absurd niedrig/hoch wird verworfen", _parse_preis(100, 200, "HIGH") == (None, None, "UNKNOWN")
      and _parse_preis(50000, 900000, "HIGH") == (None, None, "UNKNOWN"))
check("F: nicht-numerisch -> UNKNOWN", _parse_preis("zwölftausend", None, "HIGH") == (None, None, "UNKNOWN"))

# ── J) Low Confidence sauber durchgereicht ──────────────────────────────
_erlaubt = {"v1"}
_low = _validiere({"candidates": [{"candidate_id": "v1",
    "why_fits": ["a", "b", "c"], "trade_offs": ["x"], "known_points": [],
    "estimated_price_min": 8000, "estimated_price_max": 22000, "price_confidence": "LOW"}]}, _erlaubt)
check("J: LOW confidence + breite Spanne wird sauber übernommen",
      _low["v1"].price_confidence == "LOW" and _low["v1"].estimated_price_max - _low["v1"].estimated_price_min >= 10000)
_badconf = _validiere({"candidates": [{"candidate_id": "v1", "why_fits": ["a", "b", "c"],
    "estimated_price_min": 8000, "estimated_price_max": 12000, "price_confidence": "SEHR_SICHER"}]}, _erlaubt)
check("J: unbekannter confidence-Wert -> UNKNOWN, Spanne trotzdem da",
      _badconf["v1"].price_confidence == "UNKNOWN")

# ── T) >=3 why_fits bei erfolgreichem Enrichment ───────────────────────
_zu_duenn = _validiere({"candidates": [{"candidate_id": "v1", "why_fits": ["nur einer"],
    "trade_offs": ["x", "y"]}]}, _erlaubt)
check("T: Antwort mit < 3 why_fits wird verworfen (Kandidat -> Fallback)",
      "v1" not in _zu_duenn)
_ok = _validiere({"candidates": [{"candidate_id": "v1",
    "why_fits": ["Grund 1", "Grund 2", "Grund 3", "Grund 4"],
    "trade_offs": ["Nachteil 1", "Nachteil 2"], "known_points": ["Punkt 1"]}]}, _erlaubt)
check("T: >=3 why_fits wird übernommen", len(_ok["v1"].why_fits) == 4)
check("U: trade_offs werden übernommen (2)", len(_ok["v1"].trade_offs) == 2)

# ── W) "(ungeprüft)" / "(geprüft)" wird aus jedem Text entfernt ────────
check("W: strip_pruef_label entfernt '(ungeprüft)'",
      strip_pruef_label("Bekannte Schwachstelle (ungeprüft): Steuerkette") == "Bekannte Schwachstelle Steuerkette"
      or "ungeprüft" not in strip_pruef_label("Bekannte Schwachstelle (ungeprüft): Steuerkette"))
_mit_label = _validiere({"candidates": [{"candidate_id": "v1",
    "why_fits": ["a", "b", "c"],
    "trade_offs": ["Motorproblem (ungeprüft): Turbo", "Verbrauch (geprüft) hoch"]}]}, _erlaubt)
check("W: kein '(ungeprüft)'/'(geprüft)' in validierten trade_offs",
      all("geprüft" not in t for t in _mit_label["v1"].trade_offs))

# ── V) deterministischer Fallback: nur VERIFIZIERTE Schwächen, kein Label ─
_k_unverified = _K(trade_offs=[
    "Bekannte Schwachstelle (ungeprüft): DPF",
    "Bekannte Schwachstelle (geprüft): Steuerkette",
    "2 verifizierte(r) KBA-Rückrufe bekannt",
])
_fb = deterministischer_fallback(_k_unverified)
check("V: Fallback-trade_offs enthalten die UNVERIFIED-Schwäche NICHT",
      not any("DPF" in t for t in _fb.trade_offs))
check("V: Fallback-trade_offs enthalten die verifizierte Schwäche + Rückruf (ohne Label)",
      any("Steuerkette" in t for t in _fb.trade_offs)
      and any("Rückruf" in t for t in _fb.trade_offs)
      and all("geprüft" not in t for t in _fb.trade_offs))
check("V: Fallback nennt keinen Preis", _fb.estimated_price_min is None and _fb.price_confidence == "UNKNOWN")
check("T/Fallback: why_fits kommen aus match_gruende", _fb.why_fits == ["Diesel eignet sich für Langstrecke"])

# ── K) Gemini-Failure bricht Suche nicht (Router-Ebene) ────────────────
import app.autofinder_budget as _afb
_afb.call_gemini_json = lambda sp, um: (_ for _ in ()).throw(RuntimeError("nie erreicht"))  # noqa: E731


async def _enrich_503(sp, um):
    from app.gemini_retry import GeminiFehlgeschlagen
    raise GeminiFehlgeschlagen("503 UNAVAILABLE")


async def _budget_leer(sp, um):
    return {"candidates": []}


afe.call_gemini_json = _enrich_503
_afb.call_gemini_json = _budget_leer
from fastapi.testclient import TestClient
from app.main import app as _app
_c = TestClient(_app)
_H = {"Authorization": "Bearer dev-key-change-in-prod"}
_r = _c.post("/api/v1/autofinder", headers=_H,
             json={"karosserie": ["kombi"], "kraftstoff": ["Diesel"], "nutzung": "langstrecke"})
_d = _r.json()
check("K: Enrichment-503 -> Suche liefert weiterhin 200", _r.status_code == 200)
check("K: Kandidaten trotzdem vorhanden (deterministisch)", len(_d["kandidaten"]) >= 1 or _d["status"] != "ok")
if _d["kandidaten"]:
    check("K: enrichment_status='fallback', enrichment_notice gesetzt",
          all(k["enrichment_status"] == "fallback" for k in _d["kandidaten"])
          and _d.get("enrichment_notice"))
    check("K/H: kein estimated_price bei Enrichment-Ausfall",
          all(k["estimated_price_min"] is None for k in _d["kandidaten"]))
    check("W/Router: kein '(ungeprüft)' irgendwo in der Consumer-Response",
          "ungeprüft" not in str(_d).lower())

# ── erfolgreiches Enrichment über den Router ──────────────────────────
async def _enrich_gut(sp, um):
    import re
    ids = re.findall(r"candidate_id=(\S+)", um)
    return {"candidates": [{"candidate_id": i,
        "why_fits": ["Sparsamer Diesel passt zur Langstrecke", "Automatik wie gewünscht",
                     "Kombi bietet Alltagsnutzen", "Leistung im komfortablen Bereich"],
        "trade_offs": ["Höhere Anschaffung als Benziner", "AdBlue nachfüllen nötig"],
        "known_points": ["Bei frühen Baujahren Steuerkettenthema beachten"],
        "estimated_price_min": 15000, "estimated_price_max": 22000,
        "price_confidence": "MEDIUM"} for i in ids]}


afe.call_gemini_json = _enrich_gut
_r2 = _c.post("/api/v1/autofinder", headers=_H,
              json={"karosserie": ["kombi"], "kraftstoff": ["Diesel"], "nutzung": "langstrecke"})
_d2 = _r2.json()
if _d2["kandidaten"]:
    k0 = _d2["kandidaten"][0]
    check("T/Router: erfolgreiches Enrichment -> >=3 why_fits", len(k0["why_fits"]) >= 3)
    check("U/Router: trade_offs vorhanden", len(k0["trade_offs"]) >= 1)
    check("F/G/Router: estimated_price_min < estimated_price_max, confidence gesetzt",
          k0["estimated_price_min"] < k0["estimated_price_max"] and k0["price_confidence"] in ("HIGH", "MEDIUM", "LOW"))
    check("Router: enrichment_status='ok', kein enrichment_notice",
          all(k["enrichment_status"] == "ok" for k in _d2["kandidaten"]) and not _d2.get("enrichment_notice"))
    check("H/Router: das Wort 'Marktpreis' taucht NICHT in der Response auf",
          "marktpreis" not in str(_d2).lower())

print()
if FEHLER:
    print(f"{len(FEHLER)} Test(s) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle AutoFinder-Enrichment-Tests bestanden.")
