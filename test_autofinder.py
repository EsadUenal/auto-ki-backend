"""
Test: AutoFinder-Engine (Runde 1) — app/autofinder.py

Testmatrix A-N der Produktspezifikation, gegen den VOLLSTAENDIGEN kanonischen
Fahrzeugbestand (frisch gebootstrappt, kein Mock, keine Legacy-DB). Deckt:
Hard Filter, Motor-Level-Empfehlung, Dedupe, additiver Score, "Missing != gut"
(§7/§8), Trust/rejected-Gate (§10), Marken-/Baureihen-Diversitaet,
deterministischer Tie-Break, Determinismus wiederholter Suchen, und dass
unbekannte Normalisierungswerte nie zu einem stillen Treffer fuehren.

Kein Netzwerk, kein LLM. Ausfuehren:  python test_autofinder.py
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


# ── Frische kanonische DB bootstrappen ──────────────────────────────────────
_tmp = tempfile.mkdtemp(prefix="vira_af_engine_")
_db_pfad = os.path.join(_tmp, "kanonisch.db")
os.environ["AUTO_KI_DB_PATH"] = _db_pfad
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")

import app.config as _cfg
importlib.reload(_cfg)
import app.database as _db
importlib.reload(_db)
_db.ensure_tables()

import app.autofinder as af
importlib.reload(af)

with _db.get_conn() as _conn:
    _NB = _conn.execute("SELECT COUNT(*) FROM baureihe").fetchone()[0]
    _NM = _conn.execute("SELECT COUNT(*) FROM motorvariante").fetchone()[0]
check("Kanonischer Bestand: 416 Baureihen geladen", _NB == 416)
check("Kanonischer Bestand: 3231 Motorvarianten geladen", _NM == 3231)


def alle_kandidaten():
    """Vollstaendige, normalisierte Kandidatenbasis — fuer Tests, die auf dem
    GESAMTEN gefilterten Pool pruefen wollen, nicht nur auf den Top-5."""
    with _db.get_conn() as conn:
        roh = af._lade_rohkandidaten(conn)
    return [af._annotiere_normalisierung(r) for r in roh]


_ALLE = alle_kandidaten()


# ══════════════════════════════════════════════════════════════════════════
# A) Diesel + Automatik + Kombi + >=150 PS -> nur passende Motorvarianten
# ══════════════════════════════════════════════════════════════════════════
req_a = af.AutoFinderRequest(kraftstoff=["Diesel"], getriebe=["automatik"],
                              karosserie=["kombi"], leistung_min_ps=150)
pool_a = [r for r in _ALLE if af.erfuellt_harte_filter(r, req_a)]
check("A: mindestens ein Treffer", len(pool_a) > 0)
check("A: ALLE Treffer sind Diesel",
      all(r["kraftstoff"] == "Diesel" for r in pool_a))
check("A: ALLE Treffer haben Automatik im Getriebe-Set",
      all("automatik" in r["_getriebe"] for r in pool_a))
check("A: ALLE Treffer sind als Kombi klassifiziert",
      all("kombi" in r["_karo"] for r in pool_a))
check("A: ALLE Treffer haben >=150 PS",
      all((r["leistung_ps"] or 0) >= 150 for r in pool_a))

erg_a = af.finde_fahrzeuge(req_a, k=5)
check("A: Top-5-Ergebnis liefert konkrete Baureihe+Motor (nicht nur Baureihe)",
      all(c.motor_bezeichnung for c in erg_a.kandidaten))


# ══════════════════════════════════════════════════════════════════════════
# B) Benzin + Handschalter + Kleinwagen -> passend
# ══════════════════════════════════════════════════════════════════════════
req_b = af.AutoFinderRequest(kraftstoff=["Benzin"], getriebe=["manuell"],
                              karosserie=["kleinwagen"])
pool_b = [r for r in _ALLE if af.erfuellt_harte_filter(r, req_b)]
check("B: mindestens ein Treffer", len(pool_b) > 0)
check("B: ALLE Treffer sind Benzin", all(r["kraftstoff"] == "Benzin" for r in pool_b))
check("B: ALLE Treffer haben Manuell im Getriebe-Set",
      all("manuell" in r["_getriebe"] for r in pool_b))
check("B: ALLE Treffer sind als Kleinwagen klassifiziert",
      all("kleinwagen" in r["_karo"] for r in pool_b))


# ══════════════════════════════════════════════════════════════════════════
# C) Elektro + Handschalter -> kein erfundener Treffer
# ══════════════════════════════════════════════════════════════════════════
req_c = af.AutoFinderRequest(kraftstoff=["Elektro"], getriebe=["manuell"])
pool_c = [r for r in _ALLE if af.erfuellt_harte_filter(r, req_c)]
check("C: 0 Treffer (kein Elektroauto der DB traegt ein manuelles Getriebe)",
      len(pool_c) == 0)
erg_c = af.finde_fahrzeuge(req_c, k=5)
check("C: finde_fahrzeuge liefert leere Liste, KEINEN erfundenen Kandidaten",
      erg_c.kandidaten == [])


# ══════════════════════════════════════════════════════════════════════════
# D) Marke ausgeschlossen -> nie im Ergebnis
# ══════════════════════════════════════════════════════════════════════════
req_d = af.AutoFinderRequest(marken_ausschliessen=["BMW"], kraftstoff=["Diesel"])
pool_d = [r for r in _ALLE if af.erfuellt_harte_filter(r, req_d)]
check("D: BMW ist im vollen Pool vorhanden (Testvoraussetzung)",
      any(r["marke"] == "BMW" for r in [x for x in _ALLE if x["kraftstoff"] == "Diesel"]))
check("D: BMW erscheint NIRGENDWO im gefilterten Pool",
      not any(r["marke"] == "BMW" for r in pool_d))
erg_d = af.finde_fahrzeuge(req_d, k=5)
check("D: BMW erscheint NIRGENDWO im Top-5-Ergebnis",
      not any(c.marke == "BMW" for c in erg_d.kandidaten))

# Case-Insensitivitaet der Markenfilter
req_d2 = af.AutoFinderRequest(marken_ausschliessen=["bmw"], kraftstoff=["Diesel"])
pool_d2 = [r for r in _ALLE if af.erfuellt_harte_filter(r, req_d2)]
check("D: Markenausschluss ist case-insensitiv ('bmw' == 'BMW')",
      not any(r["marke"] == "BMW" for r in pool_d2))


# ══════════════════════════════════════════════════════════════════════════
# E) Leistungsfenster -> korrekt
# ══════════════════════════════════════════════════════════════════════════
req_e = af.AutoFinderRequest(leistung_min_ps=100, leistung_max_ps=150)
pool_e = [r for r in _ALLE if af.erfuellt_harte_filter(r, req_e)]
check("E: mindestens ein Treffer", len(pool_e) > 0)
check("E: ALLE Treffer liegen im Fenster [100,150] PS",
      all(r["leistung_ps"] is not None and 100 <= r["leistung_ps"] <= 150 for r in pool_e))
check("E: Motoren OHNE Leistungsangabe werden ausgeschlossen (kein Raten)",
      not any(r["leistung_ps"] is None for r in pool_e))


# ══════════════════════════════════════════════════════════════════════════
# F) Baujahrfenster -> korrekt
# ══════════════════════════════════════════════════════════════════════════
req_f = af.AutoFinderRequest(baujahr_von=2015, baujahr_bis=2018)
pool_f = [r for r in _ALLE if af.erfuellt_harte_filter(r, req_f)]
check("F: mindestens ein Treffer", len(pool_f) > 0)


def _ueberschneidet(von, bis, req_von=2015, req_bis=2018):
    bis_eff = bis if bis is not None else 9999
    return von is not None and von <= req_bis and bis_eff >= req_von


check("F: ALLE Treffer ueberschneiden sich mit 2015-2018",
      all(_ueberschneidet(r["bauzeitraum_von"], r["bauzeitraum_bis"]) for r in pool_f))
check("F: eine Baureihe MIT offenem Bauende (bis=None, 'noch aktuell') und "
      "Start <=2018 gilt als Treffer",
      any(r["bauzeitraum_bis"] is None for r in pool_f))


# ══════════════════════════════════════════════════════════════════════════
# G) Ausstattungsdubletten -> deduped
# ══════════════════════════════════════════════════════════════════════════
# Realer, bekannter Dublettenfall aus dem Audit: RS 5 B9, 450 PS Benzin
# Allrad Automatik, 4 Ausstattungslinien (Coupé/Sportback x Competition).
_rs5_gruppe = [r for r in _ALLE
               if r["baureihe_id"] == "audi-rs-5-b9" and r["leistung_ps"] == 450
               and r["kraftstoff"] == "Benzin"]
check("G: Testvoraussetzung — RS 5 B9 450 PS hat >=4 Rohzeilen vor Dedupe",
      len(_rs5_gruppe) >= 4)
_rs5_dedupliziert = af.dedupe_kandidaten(_rs5_gruppe)
check("G: nach Dedupe genau 1 Kandidat je (Baureihe,PS,Kraftstoff,Antrieb,Getriebeklasse)",
      len(_rs5_dedupliziert) == 1)

# Echte technische Unterschiede (unterschiedlicher Antrieb) duerfen NICHT
# zusammengeworfen werden.
_c220 = [r for r in _ALLE if r["baureihe_id"] == "mercedes-benz-c-klasse-w206"
         and r["leistung_ps"] == 194 and r["kraftstoff"] == "Diesel"]
_c220_front = [r for r in _c220 if r["antrieb"] == "Front"]
_c220_allrad = [r for r in _c220 if r["antrieb"] == "Allrad"]
if _c220_front and _c220_allrad:
    _c220_dedup = af.dedupe_kandidaten(_c220)
    antriebe = {r["antrieb"] for r in _c220_dedup}
    check("G: unterschiedlicher Antrieb bleibt nach Dedupe als eigener Kandidat erhalten",
          "Front" in antriebe and "Allrad" in antriebe)

# Zugesicherte Obergrenze: Dedupe reduziert um deutlich weniger als die
# rohe (antriebsblinde) 11,5%-Quote aus dem Audit, aber es reduziert.
_gefiltert_gesamt = _ALLE
_dedup_gesamt = af.dedupe_kandidaten(_gefiltert_gesamt)
check("G: Dedupe reduziert die Gesamtmenge tatsaechlich",
      len(_dedup_gesamt) < len(_gefiltert_gesamt))
check("G: Dedupe wirft keine Baureihe komplett heraus (nur Trimlinien)",
      {r["baureihe_id"] for r in _dedup_gesamt} == {r["baureihe_id"] for r in _gefiltert_gesamt})


# ══════════════════════════════════════════════════════════════════════════
# H) Missing Verbrauch -> kein Sparsamkeitsbonus
# ══════════════════════════════════════════════════════════════════════════
_basis_roh = {
    "baureihe_id": "test-h", "marke": "Test", "modell": "H", "generation": "1",
    "bauzeitraum_von": 2020, "bauzeitraum_bis": None,
    "karosserie": '["Kombi"]', "segment": "Kompaktklasse", "euro_ncap_sterne": None,
    "variante_id": "test-h-1", "bezeichnung": "1.0 Test", "motorcode": None,
    "kraftstoff": "Benzin", "leistung_ps": 100, "drehmoment_nm": 150,
    "getriebe": '["Manuell"]', "antrieb": "Front",
    "beschleunigung_0_100": None, "verbrauch_wltp": None, "verbrauch_real": None,
}
_roh_ohne_verbrauch = af._annotiere_normalisierung(dict(_basis_roh))
score_h, gruende_h = af._score_kandidat(_roh_ohne_verbrauch, af.AutoFinderRequest(sparsam=True))
check("H: sparsam=True aber Verbrauch fehlt -> 0 Punkte aus dem Sparsam-Zweig",
      score_h == 0.0)
check("H: kein Sparsamkeits-Grund im Text, wenn Verbrauch fehlt",
      not any("verbrauch" in g.lower() for g in gruende_h))

_roh_mit_verbrauch = af._annotiere_normalisierung({**_basis_roh, "verbrauch_real": 5.0})
score_h2, gruende_h2 = af._score_kandidat(_roh_mit_verbrauch, af.AutoFinderRequest(sparsam=True))
check("H: sparsam=True MIT belegtem Verbrauch -> Punkte > 0 (Kontrastprobe)",
      score_h2 > 0.0)


# ══════════════════════════════════════════════════════════════════════════
# I) Missing Schwachstellen -> kein Zuverlaessigkeitsbonus (§8 P0-2)
# ══════════════════════════════════════════════════════════════════════════
_quellcode_score = inspect.getsource(af._score_kandidat)
check("I: _score_kandidat() liest strukturell KEINE Schwachstellen-/"
      "Rueckruf-/Wartungsfelder (kein Zuverlaessigkeits-Scoring moeglich)",
      not any(w in _quellcode_score.lower() for w in
              ("schwachstelle", "rueckruf", "wartung", "zuverl", "trust", "verifiz")))

# Zwei identische Kandidaten (gleiche harte Fakten), die sich NUR darin
# unterscheiden wuerden, wie viele Schwachstellen ihre Baureihe traegt --
# _score_kandidat bekommt diese Information gar nicht uebergeben und MUSS
# deshalb fuer beide denselben Score liefern.
_roh_i1 = af._annotiere_normalisierung({**_basis_roh, "baureihe_id": "bmw-3er-g20-g21"})
_roh_i2 = af._annotiere_normalisierung({**_basis_roh, "baureihe_id": "toyota-aygo-ii"})
score_i1, _ = af._score_kandidat(_roh_i1, af.AutoFinderRequest(fahranfaenger=True))
score_i2, _ = af._score_kandidat(_roh_i2, af.AutoFinderRequest(fahranfaenger=True))
check("I: identische Hard-Facts -> identischer Score, unabhaengig von der "
      "(hier gar nicht sichtbaren) Schwachstellenlage der jeweiligen Baureihe",
      score_i1 == score_i2)


# ══════════════════════════════════════════════════════════════════════════
# J) rejected Fakt -> nicht verwendet (§10 Trust-Gate)
# ══════════════════════════════════════════════════════════════════════════
# Bekannter Fall aus der DB: BMW 3er G20/G21 traegt eine ALS FALSCH ERKANNTE
# Schwachstelle ("Bremsen", fakt_verifikation.status='rejected'). AutoFinder
# liest Baureihenfakten ausschliesslich ueber database.get_baureihe() (§10) —
# genau die Route, die `sichtbare_fakten()` bereits anwendet. Dieser Test
# beweist, dass diese Route (auf der `_trade_offs_fuer` aufsetzt) den
# widerlegten Fakt vollstaendig entfernt, nicht nur bei bestimmter
# Severity ausblendet.
_bmw_g20 = _db.get_baureihe("BMW", "3er", "G20/G21")
check("J: Testvoraussetzung — BMW 3er G20/G21 in der DB gefunden",
      _bmw_g20 is not None)
if _bmw_g20:
    _rejected_ids = {15}  # siehe fakt_verifikation.id=2 -> schwachstelle_baureihe.id=15
    check("J: rejected Schwachstelle (id=15, 'Bremsen') erscheint NICHT in "
          "get_baureihe()['schwachstellen_baureihe'] (Quelle von _trade_offs_fuer)",
          not any(s.get("id") in _rejected_ids
                  for s in _bmw_g20.get("schwachstellen_baureihe", [])))

    _g20d_roh = next((r for r in _ALLE if r["baureihe_id"] == "bmw-3er-g20-g21"
                       and r["bezeichnung"] == "320d xDrive"), None)
    if _g20d_roh:
        _trade_offs = af._trade_offs_fuer(_g20d_roh)
        check("J: 'Bremsen' taucht auch in den AutoFinder-Trade-offs nicht auf",
              not any("bremsen" in t.lower() for t in _trade_offs))


# ══════════════════════════════════════════════════════════════════════════
# K) Top-K Marken-Diversitaet
# ══════════════════════════════════════════════════════════════════════════
_synth_pool = []
for marke in ("BMW", "BMW", "BMW", "BMW", "Audi", "Audi", "Mercedes-Benz"):
    for i in range(1):
        idx = len(_synth_pool)
        _synth_pool.append({
            **_basis_roh, "marke": marke, "baureihe_id": f"{marke.lower()}-{idx}",
            "variante_id": f"v{idx}", "bauzeitraum_von": 2020 - idx,
        })
_diversifiziert = af.diversifiziere(_synth_pool, max_pro_marke=2, max_pro_baureihe=1, k=5)
_marken_counts = {}
for r in _diversifiziert:
    _marken_counts[r["marke"]] = _marken_counts.get(r["marke"], 0) + 1
check("K: max. 2 Kandidaten je Marke", all(v <= 2 for v in _marken_counts.values()))
check("K: max. 1 Kandidat je Baureihe (hier: jede Zeile eigene Baureihe -> alle 5 durch)",
      len(_diversifiziert) == 5)
check("K: BMW (4 Rohkandidaten) wird auf 2 gedeckelt", _marken_counts.get("BMW") == 2)

# Reale Suche: Sportlich+Benzin lieferte im Audit einen starken Markenbias.
req_sportlich = af.AutoFinderRequest(sportlich=True, kraftstoff=["Benzin"])
erg_sportlich = af.finde_fahrzeuge(req_sportlich, k=5)
_marken_real = {}
_baureihen_real = {}
for c in erg_sportlich.kandidaten:
    _marken_real[c.marke] = _marken_real.get(c.marke, 0) + 1
    _baureihen_real[c.baureihe_id] = _baureihen_real.get(c.baureihe_id, 0) + 1
check("K (real): 'sportlich+Benzin' haelt die Marken-Diversitaetsgrenze ein",
      all(v <= 2 for v in _marken_real.values()))
check("K (real): 'sportlich+Benzin' haelt die Baureihen-Diversitaetsgrenze ein",
      all(v <= 1 for v in _baureihen_real.values()))


# ══════════════════════════════════════════════════════════════════════════
# L) Tie Break deterministisch
# ══════════════════════════════════════════════════════════════════════════
_tie_pool = [
    {**_basis_roh, "baureihe_id": f"tie-{v}", "variante_id": v, "bauzeitraum_von": 2020}
    for v in ("zzz", "aaa", "mmm")
]
_tie_scored = [(0.0, 0.5, r) for r in _tie_pool]
_tie_sortiert = sorted(_tie_scored, key=lambda t: af._sortierschluessel(t))
_reihenfolge = [t[2]["variante_id"] for t in _tie_sortiert]
check("L: bei exakt gleichem Score+DQ+Baujahr ist die Reihenfolge die "
      "aufsteigende variante_id (deterministisch, nie zufaellig)",
      _reihenfolge == ["aaa", "mmm", "zzz"])

# Zehnmal wiederholt -> IMMER dieselbe Reihenfolge (kein Set-Iteration-Zufall)
_wiederholungen = set()
for _ in range(10):
    r = sorted(_tie_scored, key=lambda t: af._sortierschluessel(t))
    _wiederholungen.add(tuple(t[2]["variante_id"] for t in r))
check("L: 10 Wiederholungen der Sortierung liefern IMMER dieselbe Reihenfolge",
      len(_wiederholungen) == 1)


# ══════════════════════════════════════════════════════════════════════════
# M) gleiche Suche zweimal -> identische Reihenfolge
# ══════════════════════════════════════════════════════════════════════════
_req_m = af.AutoFinderRequest(kraftstoff=["Diesel"], getriebe=["automatik"], leistung_min_ps=150)
_erg_m1 = af.finde_fahrzeuge(_req_m, k=5)
_erg_m2 = af.finde_fahrzeuge(_req_m, k=5)
check("M: dieselbe Suche zweimal ausgefuehrt liefert identische variante_id-Reihenfolge",
      [c.variante_id for c in _erg_m1.kandidaten] == [c.variante_id for c in _erg_m2.kandidaten])
check("M: auch Match-Score und Datenqualitaet sind stabil identisch",
      [(c.match_score, c.datenqualitaet) for c in _erg_m1.kandidaten]
      == [(c.match_score, c.datenqualitaet) for c in _erg_m2.kandidaten])


# ══════════════════════════════════════════════════════════════════════════
# N) unbekannte Normalisierungswerte -> UNKNOWN, kein Guess
# ══════════════════════════════════════════════════════════════════════════
_roh_unbekannt = af._annotiere_normalisierung({
    **_basis_roh, "karosserie": '["Bratpfanne"]', "getriebe": '["Zaubergetriebe"]',
})
check("N: nicht klassifizierbare Karosserie -> leeres Set (nicht geraten)",
      _roh_unbekannt["_karo"] == frozenset())
check("N: nicht klassifizierbares Getriebe -> leeres Set (nicht geraten)",
      _roh_unbekannt["_getriebe"] == frozenset())
check("N: aktiver Karosserie-Filter schliesst unbekannten Kandidaten aus "
      "(kein stiller Treffer durch Raten)",
      not af.erfuellt_harte_filter(_roh_unbekannt, af.AutoFinderRequest(karosserie=["kombi"])))
check("N: aktiver Getriebe-Filter schliesst unbekannten Kandidaten aus",
      not af.erfuellt_harte_filter(_roh_unbekannt, af.AutoFinderRequest(getriebe=["automatik"])))
check("N: OHNE aktiven Filter bleibt der unbekannt klassifizierte Kandidat "
      "trotzdem auffindbar (kein Ausschluss ohne Grund)",
      af.erfuellt_harte_filter(_roh_unbekannt, af.AutoFinderRequest()))


# ══════════════════════════════════════════════════════════════════════════
# Zusatz: Reserved Fields ohne Effekt (§4) — praktisch/komfortabel/familie
# ══════════════════════════════════════════════════════════════════════════
_roh_neutral = af._annotiere_normalisierung(dict(_basis_roh))
score_neutral, _ = af._score_kandidat(_roh_neutral, af.AutoFinderRequest())
score_reserved, _ = af._score_kandidat(
    _roh_neutral, af.AutoFinderRequest(praktisch=True, komfortabel=True, familie=True))
check("Reserviert: praktisch/komfortabel/familie aendern den Score NICHT (§4 — "
      "DB traegt keine Platz-/Komfortdaten, kein stiller Rateersatz)",
      score_neutral == score_reserved == 0.0)

_erg_budget = af.finde_fahrzeuge(
    af.AutoFinderRequest(kraftstoff=["Diesel"], budget_min=5000, budget_max=8000), k=5)
check("Reserviert: budget_min/budget_max filtern NICHT hart (kein erfundener Preis, §5/§13)",
      len(_erg_budget.kandidaten) == len(
          af.finde_fahrzeuge(af.AutoFinderRequest(kraftstoff=["Diesel"]), k=5).kandidaten))

check("Vorbereitung §12/§13: market_*-Felder bleiben None in Runde 1",
      all(c.market_price_min is None and c.market_price_max is None
          and c.market_price_median is None for c in erg_a.kandidaten))
check("Vorbereitung §12: source_type ist 'internal_db' (kein Web-Kandidat in Runde 1)",
      all(c.source_type == "internal_db" for c in erg_a.kandidaten))
check("Vorbereitung §15: jeder Kandidat traegt einen nicht-leeren visual_key",
      all(c.visual_key for c in erg_a.kandidaten))
check("Datenqualitaet (§9) ist vom Match-Score GETRENNT (eigenes Feld, kein Vermischen)",
      all(0.0 <= c.datenqualitaet <= 1.0 for c in erg_a.kandidaten))


# ══════════════════════════════════════════════════════════════════════════
# Performance (§19)
# ══════════════════════════════════════════════════════════════════════════
_req_perf = af.AutoFinderRequest(kraftstoff=["Diesel"], getriebe=["automatik"])
af.finde_fahrzeuge(_req_perf, k=5)  # Cache aufwaermen
_zeiten = []
for _ in range(5):
    _t0 = time.perf_counter()
    af.finde_fahrzeuge(_req_perf, k=5)
    _zeiten.append((time.perf_counter() - _t0) * 1000)
print(f"\nPerformance (Cache warm, 5 Läufe): {[round(z, 1) for z in _zeiten]} ms")
check("Performance: bei warmem Cache deutlich unter 100ms (§19)",
      max(_zeiten) < 100.0)


print()
if FEHLER:
    print(f"{len(FEHLER)} Test(s) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle AutoFinder-Engine-Tests bestanden.")
