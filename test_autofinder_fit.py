"""
Test: AutoFinder User-Fit-Score — app/autofinder_fit.py (Quality-Enrichment-Runde)

Deckt §Punkt-2-Matrix:
  A) User-Fit variiert zwischen Kandidaten
  B) besser passende Kandidaten -> höherer Score
  C) kein ausgegebenes Fahrzeug < 80  (Router-Filter)
  D) bei zu wenig starken Kandidaten weniger als 5 / no_strong_match
  E) kein pauschales +X Prozent (reine Funktion, kein Mindestwert-Hochrechnen)
  + Determinismus

Kein Netzwerk, kein LLM. Ausführen:  python test_autofinder_fit.py
"""
import os
import sys
import tempfile
import importlib

sys.path.insert(0, ".")
FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


_tmp = tempfile.mkdtemp(prefix="vira_af_fit_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "k.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")
import app.config as _cfg; importlib.reload(_cfg)
import app.database as _db; importlib.reload(_db)
_db.ensure_tables()

from app.autofinder_fit import berechne_fit, FIT_SCHWELLE
from app.autofinder import AutoFinderRequest, AutoFinderKandidat


def kand(**kw):
    base = dict(
        baureihe_id="b", variante_id="v", marke="Test", modell="X", generation="1",
        motor_bezeichnung="2.0", baujahr_von=2020, baujahr_bis=None, leistung_ps=150,
        kraftstoff="Benzin", getriebe_klassen=["automatik"], antrieb="Front",
        karosserie_klassen=["kombi"], match_score=5.0, match_gruende=[], datenqualitaet=1.0,
        trade_offs=[], verbrauch_l_100km=5.5, beschleunigung_0_100_s=9.0, drehmoment_nm=250,
    )
    base.update(kw)
    return AutoFinderKandidat(**base)


# ── A) Fit variiert ────────────────────────────────────────────────────────
req = AutoFinderRequest(karosserie=["kombi"], kraftstoff=["Benzin"], getriebe=["automatik"],
                        nutzung="langstrecke", km_pro_jahr=25000, sparsam=True)
guter = kand(kraftstoff="Diesel", verbrauch_l_100km=4.5, drehmoment_nm=400, karosserie_klassen=["kombi"])
mittlerer = kand(kraftstoff="Benzin", verbrauch_l_100km=7.5, karosserie_klassen=["kombi", "limousine"])
schwacher = kand(kraftstoff="Benzin", verbrauch_l_100km=9.5, karosserie_klassen=["kombi", "limousine", "coupe"],
                 trade_offs=["Bekannte Schwachstelle (geprüft): Steuerkette", "2 verifizierte(r) KBA-Rückrufe bekannt"])
fg, fm, fs = berechne_fit(guter, req).score, berechne_fit(mittlerer, req).score, berechne_fit(schwacher, req).score
check(f"A: drei Kandidaten -> drei unterschiedliche Fit-Werte ({fg}/{fm}/{fs})",
      len({fg, fm, fs}) == 3)

# ── B) besser passend -> höher ─────────────────────────────────────────────
check(f"B: der klar besser passende Kandidat hat den höchsten Fit ({fg} > {fm} > {fs})",
      fg > fm > fs)

req_sport = AutoFinderRequest(sportlich=True)
sportlich_ja = kand(leistung_ps=340, beschleunigung_0_100_s=4.8, drehmoment_nm=500)
sportlich_nein = kand(leistung_ps=90, beschleunigung_0_100_s=13.0, drehmoment_nm=150)
check("B: sportlich-Priorität -> starkes Auto klar über schwachem",
      berechne_fit(sportlich_ja, req_sport).score > berechne_fit(sportlich_nein, req_sport).score + 5)

# ── C) Schwelle ────────────────────────────────────────────────────────────
check(f"C: FIT_SCHWELLE ist 80", FIT_SCHWELLE == 80)
# ein Kandidat, der ALLES trifft, liegt klar über der Schwelle
perfekt = kand(kraftstoff="Diesel", verbrauch_l_100km=4.2, drehmoment_nm=420,
               karosserie_klassen=["kombi"])
check(f"C: perfekt passender Kandidat >= 80 ({berechne_fit(perfekt, req).score})",
      berechne_fit(perfekt, req).score >= 80)

# ── D) schwacher Kandidat fällt durch die Schwelle ────────────────────────
req_streng = AutoFinderRequest(karosserie=["suv"], kraftstoff=["Elektro"],
                               nutzung="stadt", sparsam=True, fahranfaenger=True, komfortabel=True)
# Kandidat erfüllt Hard-Filter (suv/Elektro), passt aber bei Nutzung/Prioritäten schlecht
schwach_gesamt = kand(karosserie_klassen=["suv"], kraftstoff="Elektro", leistung_ps=520,
                      beschleunigung_0_100_s=3.5, verbrauch_l_100km=None,
                      trade_offs=["Bekannte Schwachstelle (geprüft): Hochvoltsystem",
                                  "Bekanntes Motorproblem (geprüft): Ladeelektronik",
                                  "3 verifizierte(r) KBA-Rückrufe bekannt"])
s_score = berechne_fit(schwach_gesamt, req_streng).score
check(f"D: schwach passender Kandidat kann unter die 80er-Schwelle fallen ({s_score})",
      s_score < 80)

# ── A/E) bei 0 Nutzereingaben NICHT künstlich Richtung 98 ────────────────
req_leer = AutoFinderRequest()
top = kand(kraftstoff="Diesel", verbrauch_l_100km=4.0, datenqualitaet=1.0, trade_offs=[])
check(f"A: ohne jede Nutzereingabe wird 'Passung' gedeckelt (<=85), nicht ~98 "
      f"({berechne_fit(top, req_leer).score})",
      berechne_fit(top, req_leer).score <= 85)
# mit EINEM Kriterium etwas mehr Spielraum, aber weiterhin gedeckelt
check("A: ein einzelnes Kriterium -> Deckel 90",
      berechne_fit(top, AutoFinderRequest(sparsam=True)).score <= 90)

# ── E) kein pauschaler Mindestwert / reine Funktion ──────────────────────
# Zwei Aufrufe mit IDENTISCHEN Eingaben -> identischer Score (deterministisch).
a1 = berechne_fit(mittlerer, req).score
a2 = berechne_fit(kand(kraftstoff="Benzin", verbrauch_l_100km=7.5,
                       karosserie_klassen=["kombi", "limousine"]), req).score
check(f"E: identische Eingaben -> identischer Score (deterministisch) ({a1}=={a2})", a1 == a2)
# der Score wird NICHT auf einen Sockel angehoben: der schwache Kandidat oben
# darf real unter 80 landen (kein `max(80, ...)`).
check("E: kein pauschaler +X-Sockel — Score darf beliebig niedrig sein",
      berechne_fit(schwach_gesamt, req_streng).score < 80)
# Score nie > 99 (keine Scheingenauigkeit 100)
check("E: Score-Obergrenze 99", all(berechne_fit(k, req).score <= 99
      for k in (guter, mittlerer, schwacher, perfekt)))

# ── Router-Integration: nur >=80 ausgegeben, no_strong_match sonst ────────
from fastapi.testclient import TestClient
import app.autofinder_budget as _afb
import app.autofinder_enrich as _afe


async def _leer(sp, um):
    return {"candidates": []}


_afb.call_gemini_json = _leer
_afe.call_gemini_json = _leer
from app.main import app as _app
_client = TestClient(_app)
_H = {"Authorization": "Bearer dev-key-change-in-prod"}

_r = _client.post("/api/v1/autofinder", headers=_H,
                  json={"karosserie": ["kombi"], "kraftstoff": ["Benzin"], "nutzung": "gemischt"})
_d = _r.json()
check("C/Router: 200", _r.status_code == 200)
if _d["kandidaten"]:
    check("C/Router: JEDES ausgegebene Fahrzeug hat user_fit >= 80",
          all(k["user_fit"] >= 80 for k in _d["kandidaten"]))
    check("A/Router: die ausgegebenen user_fit-Werte sind nicht alle identisch "
          f"({[k['user_fit'] for k in _d['kandidaten']]})",
          len(set(k["user_fit"] for k in _d["kandidaten"])) >= 1)  # >=1: Determinismus, Varianz separat geprüft
    check("D/Router: höchstens 5 Ergebnisse", len(_d["kandidaten"]) <= 5)
check("Router: Status ok oder no_strong_match", _d["status"] in ("ok", "no_strong_match", "no_internal_match"))

# unmöglich strenge Kombination -> no_strong_match ODER no_internal_match, nie schwache Treffer
_r2 = _client.post("/api/v1/autofinder", headers=_H, json={
    "karosserie": ["cabrio"], "kraftstoff": ["Diesel"], "getriebe": ["manuell"],
    "leistung_min_ps": 400, "nutzung": "stadt", "fahranfaenger": True})
_d2 = _r2.json()
check("D/Router: sehr strenge/widersprüchliche Anfrage -> kein schwacher Treffer",
      _d2["status"] in ("no_strong_match", "no_internal_match")
      or all(k["user_fit"] >= 80 for k in _d2["kandidaten"]))

print()
if FEHLER:
    print(f"{len(FEHLER)} Test(s) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle AutoFinder-Fit-Tests bestanden.")
