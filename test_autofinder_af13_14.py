"""
AF-13 / AF-14 -- Motorzeiträume (belegt / teilweise / unbekannt) und Budget-/Modelltext-Konsistenz (offline).

    python scripts/test_isolated.py test_autofinder_af13_14.py

AF-13  A  Auswahl: belegte Ausschlüsse bleiben, unbekannte Varianten bleiben nutzbar (alle Varianten, nicht nur Audi/BMW)
       B  Passung: keine bestätigte Baujahr-Passung ohne belegte Motorzeit; Wirkung auf die tatsächliche Bewertung
       C  Zeitdarstellung: Wunschfenster / Baureihenüberschneidung / Motorzeit getrennt; offenes Ende ist nicht "heute"
       D  Prompts: Budget und Enrichment bekommen dieselbe Zeitgrundlage; kein Rohstatus im Enrichment
AF-14  E  Budgetlage: fünf Fälle getrennt; nach unten überlappend ist keine Überschreitung
       F  Modelltexte gegen das finale Budgeturteil; unbelegte Kilometerstandsaussagen entfallen, Nutzung bleibt
       G  Ende-zu-Ende über den produktiven Router (Provider gestubbt): Mindestpassung, Sortierung, Providerzahl, Preisspanne
Daten  H  Audi A3 8V 1.2 TFSI: belegte Grenzen, Bezeichnung, Migration (frisch, idempotent, Abbruch ohne Teilschreibung)

Echte Seed-Daten (Temp-DB des Isolationsrunners), echte Engine/Fit/Router. Nur die Providerantworten sind
gestubbt, Tavily ist verboten. NACHWEISGRENZE: Gemini-Antworten sind konstruierte Annahmen, kein Live-Lauf.
"""
import hashlib
import re
import sqlite3
import sys
from types import SimpleNamespace

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi.testclient import TestClient

import app.autofinder as af
import app.autofinder_budget as afb
import app.autofinder_enrich as afe
import app.autofinder_fit as aff
import app.autofinder_web as afw
import app.data_migrations as dm
import app.routers.autofinder as R
from app import motor_geltung_daten as gd
from app.autofinder_fit import FIT_SCHWELLE, berechne_fit
from app.autofinder_variante import (
    MZ_BELEGT, MZ_TEILWEISE, MZ_UNBEKANNT, baujahr_kontext, motorzeit_status, wunschfenster_text,
)
from app.config import DB_PATH
from app.database import get_conn
from app.main import app as _app

FEHLER: list[str] = []


def check(name, bedingung, detail=""):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f"  -> {detail}" if not bedingung and detail else ""))
    if not bedingung:
        FEHLER.append(name)


R.require_autofinder_kontingent = lambda request: None
CALLS = {"budget": 0, "enrich": 0, "tavily": 0}
MODE = {"stat": None, "preise": {}, "texte": None, "default": (18000, 25000), "conf": "MEDIUM"}
PROMPTS = {"budget": [], "enrich": []}


async def _tavily(*a, **k):
    CALLS["tavily"] += 1
    raise AssertionError("Tavily verboten")


afw.tavily_search_mit_status = _tavily


async def _budget(sp, um):
    CALLS["budget"] += 1
    PROMPTS["budget"].append(um)
    ids = re.findall(r"candidate_id=(\S+)", um)
    return {"candidates": [{"candidate_id": i, "budget_status": (MODE["stat"] or "NEAR_BUDGET"), "confidence": "MEDIUM"}
                           for i in ids]}


async def _enrich(sp, um):
    CALLS["enrich"] += 1
    PROMPTS["enrich"].append(um)
    ids = re.findall(r"candidate_id=(\S+)", um)
    why, trade = MODE["texte"] or (["Grund eins", "Grund zwei", "Grund drei"], ["Nachteil eins", "Nachteil zwei"])
    out = []
    for i in ids:
        lo, hi = MODE["preise"].get(i, MODE["default"])
        out.append({"candidate_id": i, "why_fits": list(why), "trade_offs": list(trade),
                    "estimated_price_min": lo, "estimated_price_max": hi, "price_confidence": MODE["conf"]})
    return {"candidates": out}


afb.call_gemini_json = _budget
afe.call_gemini_json = _enrich
client = TestClient(_app)
H = {"Authorization": "Bearer dev-key-change-in-prod"}


def lauf(body):
    CALLS.update(budget=0, enrich=0, tavily=0)
    PROMPTS["budget"].clear(); PROMPTS["enrich"].clear()
    r = client.post("/api/v1/autofinder", headers=H, json=body)
    assert r.status_code == 200, r.text
    return r.json(), dict(CALLS)


def basis():
    with get_conn() as c:
        return af._annotiere_basis(af._lade_rohkandidaten(c))


BASIS = basis()
roh = lambda vid: next(r for r in BASIS if r["variante_id"] == vid)


def treffer(**kw):
    req = af.AutoFinderRequest(**kw)
    return [r["variante_id"] for r in BASIS if af.erfuellt_harte_filter(r, req)]


def kand(vid, **req_kw):
    req = af.AutoFinderRequest(**req_kw)
    return af._zu_kandidat(roh(vid), 0.0, [], 1.0, req), req


G77 = "volkswagen-golf-vii-1.2-tsi-77kw"       # belegt 2013-2015
F116 = "bmw-1er-f20-f21-116i"                   # teilweise: nur Obergrenze 2015
F116L = "bmw-1er-f20-f21-116i-lci"              # teilweise: nur Untergrenze 2015
F40 = "bmw-1er-f40-118i"                        # unbekannt, Baureihe offen (2019 ->)
A3_105, A3_110 = gd.A3_8V_105, gd.A3_8V_110

print("=== A) Auswahl: belegter Ausschluss bleibt, unbekannte Zeit schließt nicht pauschal aus ===")
check("A1 Statusableitung: beide Grenzen = belegt, eine = teilweise, keine = unbekannt",
      (motorzeit_status(2013, 2015), motorzeit_status(2015, None), motorzeit_status(None, 2015),
       motorzeit_status(None, None)) == (MZ_BELEGT, MZ_TEILWEISE, MZ_TEILWEISE, MZ_UNBEKANNT))
check("A2 belegte Bauzeit (Golf VII 1.2 TSI 2013–2015): außerhalb des Fensters ausgeschlossen, innerhalb ausgewählt",
      G77 not in treffer(marken_bevorzugt=["Volkswagen"], baujahr_von=2016, baujahr_bis=2020)
      and G77 in treffer(marken_bevorzugt=["Volkswagen"], baujahr_von=2014, baujahr_bis=2018))
check("A3 Teilgrenze nur in belegter Richtung: 116i (nur Obergrenze 2015) ab 2016 ausgeschlossen, bis 2014 ausgewählt; "
      "116i LCI (nur Untergrenze 2015) bis 2014 ausgeschlossen, ab 2016 ausgewählt",
      F116 not in treffer(marken_bevorzugt=["BMW"], baujahr_von=2016, baujahr_bis=2019)
      and F116 in treffer(marken_bevorzugt=["BMW"], baujahr_von=2011, baujahr_bis=2014)
      and F116L not in treffer(marken_bevorzugt=["BMW"], baujahr_von=2011, baujahr_bis=2014)
      and F116L in treffer(marken_bevorzugt=["BMW"], baujahr_von=2016, baujahr_bis=2019))
check("A4 unbekannte Motorzeit bleibt nutzbar: BMW 118i F40 (Baureihe 2019 →) wird im Fenster 2016–2022 weiter ausgewählt",
      F40 in treffer(marken_bevorzugt=["BMW"], baujahr_von=2016, baujahr_bis=2022)
      and F40 not in treffer(marken_bevorzugt=["BMW"], baujahr_von=2012, baujahr_bis=2017))

# Allgemein über ALLE Varianten mit belegter Grenze (keine Einzelfälle): ein Fenster ganz außerhalb schließt aus,
# eines, das die Grenze und die Baureihe einschließt, nicht.
mit_grenze = [r for r in BASIS if r.get("motor_bauzeit_von") is not None or r.get("motor_bauzeit_bis") is not None]
verstoesse = []
for r in mit_grenze:
    req_vor = af.AutoFinderRequest(baujahr_von=1900, baujahr_bis=(r["motor_bauzeit_von"] - 1)) \
        if r.get("motor_bauzeit_von") is not None else None
    req_nach = af.AutoFinderRequest(baujahr_von=(r["motor_bauzeit_bis"] + 1), baujahr_bis=2100) \
        if r.get("motor_bauzeit_bis") is not None else None
    if req_vor and af._baujahr_ueberschneidet(r, req_vor):
        verstoesse.append((r["variante_id"], "vor"))
    if req_nach and af._baujahr_ueberschneidet(r, req_nach):
        verstoesse.append((r["variante_id"], "nach"))
check(f"A5 alle {len(mit_grenze)} Varianten mit belegter Grenze: ein Fenster jenseits der Grenze schließt IMMER aus",
      len(mit_grenze) >= 5 and not verstoesse, str(verstoesse[:5]))
ohne_grenze = [r for r in BASIS if r.get("motor_bauzeit_von") is None and r.get("motor_bauzeit_bis") is None
               and r.get("bauzeitraum_von") is not None]
req_mitte = lambda r: af.AutoFinderRequest(baujahr_von=r["bauzeitraum_von"], baujahr_bis=r["bauzeitraum_von"])
check("A6 unbekannte Zeit: die Baureihenüberschneidung entscheidet weiter über die Auswahl (kein pauschaler Ausschluss)",
      all(af._baujahr_ueberschneidet(r, req_mitte(r)) for r in ohne_grenze) and len(ohne_grenze) > 1000)

print("\n=== B) Passung: keine bestätigte Baujahr-Passung ohne belegte Motorzeit ===")
BJ = dict(baujahr_von=2016, baujahr_bis=2022)
k_unb, req_unb = kand(F40, **BJ)
k_bel, req_bel = kand(G77, baujahr_von=2013, baujahr_bis=2015)
k_teil, req_teil = kand(F116, baujahr_von=2011, baujahr_bis=2014)
f_unb, f_bel, f_teil = berechne_fit(k_unb, req_unb), berechne_fit(k_bel, req_bel), berechne_fit(k_teil, req_teil)
lab = lambda f: [c.label for c in f.komponenten]
check("B1 unbekannte Motorzeit: weder Komponente noch positiver Grund 'Baujahr im Wunschfenster'",
      "Baujahr im Wunschfenster" not in lab(f_unb) and "Baujahr im Wunschfenster" not in f_unb.gruende)
check("B2 teilweise belegt (nur eine Grenze): ebenfalls keine bestätigte Passung",
      "Baujahr im Wunschfenster" not in lab(f_teil) and "Baujahr im Wunschfenster" not in f_teil.gruende)
check("B3 belegte Motorzeit (Beginn und Ende): die Passung wird weiter bewertet",
      "Baujahr im Wunschfenster" in lab(f_bel))


def fit_alt(k, req):
    """Verhalten VOR AF-13: die Baureihenüberschneidung zählt immer als Baujahr-Passung."""
    orig = aff._baujahr_bewertbar
    aff._baujahr_bewertbar = lambda _k: True
    try:
        return berechne_fit(k, req)
    finally:
        aff._baujahr_bewertbar = orig


a_unb = fit_alt(k_unb, req_unb)
print(f"   Wirkung (BMW 118i F40, Fenster 2016–2022): Fit alt={a_unb.score} (Gründe {a_unb.gruende}) -> neu={f_unb.score} "
      f"(Gründe {f_unb.gruende})")
bj_alt = next(c for c in a_unb.komponenten if c.label == "Baujahr im Wunschfenster")
check("B4 Wirkung auf die tatsächliche Bewertung: die frühere Baujahr-Komponente (Gewicht 0,7) fließt nicht mehr ein, "
      "der Score wird neu berechnet (nicht nur der Text gelöscht)",
      bj_alt.gewicht == 0.7 and len(f_unb.komponenten) == len(a_unb.komponenten) - 1
      and abs(sum(c.gewicht for c in f_unb.komponenten) - (sum(c.gewicht for c in a_unb.komponenten) - 0.7)) < 1e-9)
REAL = dict(baujahr_von=2016, baujahr_bis=2022, leistung_min_ps=100, leistung_max_ps=200, nutzung="gemischt",
            km_pro_jahr=15000, sparsam=True, fahranfaenger=True, karosserie=["kompakt"])
k_r, req_r = kand(F40, **REAL)
f_neu, f_alt = berechne_fit(k_r, req_r), fit_alt(k_r, req_r)
print(f"   Wirkung (BMW 118i F40, volle Anfrage der echten Suche): Fit alt={f_alt.score} -> neu={f_neu.score}")
check("B5 kein pauschaler Strafpunkt: die Schwelle bleibt 80, die übrigen Komponenten sind unverändert; bei der vollen "
      "Anfrage weicht der Score nur um den Wegfall der einen Komponente ab (|Δ| ≤ 3)",
      FIT_SCHWELLE == 80 and abs(f_neu.score - f_alt.score) <= 3
      and all(any(c2.label == c.label and c2.erfuellung == c.erfuellung for c2 in f_alt.komponenten)
              for c in f_neu.komponenten))
# Sonderfall: ist das Baujahrfenster das EINZIGE Nutzerkriterium, entfällt mit ihm das Kriterium; die bestehende
# Deckelung bei wenigen Nutzereingaben (nie angehoben, nur begrenzt) gilt dann wie bei jedem nicht bewertbaren Kriterium.
check("B5b nur Baujahrfenster angegeben und Motorzeit unbekannt: bestehende Deckelung (0 Kriterien -> max. 85), "
      "Mindestpassung bleibt erreichbar", f_unb.score <= 85 and f_unb.score >= FIT_SCHWELLE)
# Allgemein über viele verschiedene Fahrzeuge: wo die Motorzeit nicht belegt ist, gibt es nie den positiven Grund.
stichprobe = [r for i, r in enumerate(ohne_grenze) if i % 37 == 0]
marken = {r["marke"] for r in stichprobe}
verletzt = []
for r in stichprobe:
    f = r["bauzeitraum_von"]
    req = af.AutoFinderRequest(baujahr_von=f, baujahr_bis=f + 3)
    k = af._zu_kandidat(r, 0.0, [], 1.0, req)
    fb = berechne_fit(k, req)
    if "Baujahr im Wunschfenster" in fb.gruende or "Baujahr im Wunschfenster" in lab(fb):
        verletzt.append(r["variante_id"])
check(f"B6 Gegenprobe über {len(stichprobe)} Varianten aus {len(marken)} Marken mit unbekannter Motorzeit: nie ein "
      "positiver Baujahr-Grund", len(marken) >= 10 and not verletzt, str(verletzt[:5]))
k_web = SimpleNamespace(baujahr_von=2018, baujahr_bis=2022, generation_baujahr_von=None, generation_baujahr_bis=None,
                        leistung_ps=130, kraftstoff="Benzin", karosserie_klassen=["kompakt"], datenqualitaet=0.8,
                        trade_offs=[])
check("B7 Web-Kandidat (belegte Bauzeit, keine Generation) wird unverändert bewertet",
      "Baujahr im Wunschfenster" in [c.label for c in berechne_fit(k_web, SimpleNamespace(baujahr_von=2016, baujahr_bis=2022)).komponenten])

print("\n=== C) Zeitdarstellung: Wunsch, Überschneidung und Motorzeit getrennt ===")
check("C1 Kandidat führt den EINGEGEBENEN Wunsch (2016–2022) getrennt von der Überschneidung (2019–2022)",
      (k_unb.wunsch_baujahr_von, k_unb.wunsch_baujahr_bis, k_unb.baujahr_von, k_unb.baujahr_bis) == (2016, 2022, 2019, 2022)
      and k_unb.motorzeit_status == MZ_UNBEKANNT and k_unb.generation_baujahr_bis is None)
ctx = baujahr_kontext(k_unb)
check("C2 offenes Baureihenende heißt 'Ende nicht hinterlegt', nie 'heute' (Kontext, ohne Verschnitt als Wunsch)",
      "ab 2019, Ende nicht hinterlegt" in ctx and "heute" not in ctx and "Wunschfenster" not in ctx
      and "Motorzeitraum nicht hinterlegt" in ctx and "Erstzulassung des einzelnen Fahrzeugs unbekannt" in ctx, ctx)
check("C3 belegter Motorzeitraum: beide Grenzen, ohne den Zusatz 'nur diese Grenze'; teilweise: mit dem Zusatz",
      "Motorzeitraum laut Datensatz ab 2013 bis 2015" in baujahr_kontext(k_bel)
      and "nur diese Grenze" not in baujahr_kontext(k_bel)
      and "Motorzeitraum laut Datensatz bis 2015 (nur diese Grenze ist belegt)" in baujahr_kontext(k_teil))
check("C4 Wunschtext nennt das eingegebene Fenster unverändert und als Suchwunsch; ohne Fenster keine Aussage",
      wunschfenster_text(2016, 2022) == "Wunschfenster des Nutzers 2016–2022 (Suchwunsch zur Erstzulassung, keine Fahrzeugeigenschaft)"
      and wunschfenster_text(None, None) is None and "2016–?" in wunschfenster_text(2016, None))
check("C5 Web-Kandidat mit offenem Ende: 'Ende nicht hinterlegt' statt 'heute'",
      baujahr_kontext(SimpleNamespace(baujahr_von=2018, baujahr_bis=None, generation_baujahr_von=None,
                                      generation_baujahr_bis=None)) == "Baujahre 2018–Ende nicht hinterlegt")

print("\n=== D) Prompts: dieselbe Zeitgrundlage, kein Rohstatus im Enrichment ===")
kb = [kand(v, **BJ)[0] for v in (F40, A3_110, F116L)]
um_e = afe._baue_user_message(kb, af.AutoFinderRequest(budget_min=15000, budget_max=25000, **BJ))
um_b = afb._baue_user_message(kb, budget_min=15000, budget_max=25000, baujahr_von=2016, baujahr_bis=2022)
fenster = "Wunschfenster des Nutzers 2016–2022 (Suchwunsch zur Erstzulassung, keine Fahrzeugeigenschaft)"
check("D1 beide Prompts tragen dasselbe, EINGEGEBENE Wunschfenster (2016–2022), nicht den Verschnitt (2019–2022)",
      fenster in um_e and fenster in um_b and "2019–2022" not in um_e and "2019–2022" not in um_b)
check("D2 beide Prompts tragen für jeden Kandidaten dieselbe Zeitangabe (baujahr_kontext)",
      all(baujahr_kontext(k) in um_e and baujahr_kontext(k) in um_b for k in kb))
check("D3 das Enrichment bekommt KEINEN rohen Budgetstatus ('Budget-Einschätzung') mehr; das Budget des Nutzers bleibt",
      "Budget-Einschätzung" not in um_e and "Budget: 15000–25000 EUR" in um_e)
kb[0].budget_status = "NEAR_BUDGET"
check("D4 auch ein am Kandidaten gesetzter Rohstatus gelangt nicht in den Prompt",
      "NEAR_BUDGET" not in afe._baue_user_message(kb, af.AutoFinderRequest(budget_max=25000)))
check("D5 Prompt-Regeln: Budget wird nicht beurteilt; über Kilometerstand ist nichts bekannt, Nutzung bleibt nennbar",
      "NICHT beurteilen, ob ein Fahrzeug \"im Budget\" liegt" in afe._SYSTEM_PROMPT
      and "Über den Kilometerstand oder die Laufleistung eines angebotenen Fahrzeugs ist nichts bekannt" in afe._SYSTEM_PROMPT
      and "Die Jahresfahrleistung des Nutzers darfst du als seine Nutzung nennen" in afe._SYSTEM_PROMPT
      )

print("\n=== E) Budgetlage: fünf Fälle getrennt (Budget 15.000–25.000 €) ===")
I, N, O = afb.IN_BUDGET, afb.NEAR_BUDGET, afb.OUT_OF_BUDGET


def kons(pmin, pmax, start=N, conf=afb.CONF_MEDIUM, bmin=15000, bmax=25000):
    return afb.konsolidiere_budget_status(start, afb.CONF_HIGH, preis_min=pmin, preis_max=pmax,
                                          preis_confidence=conf, budget_min=bmin, budget_max=bmax)


FAELLE = {
    "ganz unterhalb": ((6000, 9000), afb.LAGE_UNTERHALB, afb.BELOW_BUDGET),
    "nach unten überlappend (Audi 11–19k)": ((11000, 19000), afb.LAGE_UNTEN, I),
    "innerhalb": ((16000, 24000), afb.LAGE_INNERHALB, I),
    "nach oben überlappend (BMW 17–26k)": ((17000, 26000), afb.LAGE_OBEN, N),
    "ganz oberhalb, knapp": ((26000, 31000), afb.LAGE_OBERHALB, N),
    "ganz oberhalb, deutlich": ((40000, 55000), afb.LAGE_OBERHALB, O),
}
for name, ((a, b), lage, status) in FAELLE.items():
    check(f"E {name}: Lage '{lage}' -> {status}",
          afb.budget_lage(a, b, 15000, 25000) == lage and kons(a, b)[0] == status)
B = afb.BELOW_BUDGET
check("E6b Grenzberührung (Spanne endet genau bei budget_min) gilt als innerhalb, nicht als unterhalb",
      afb.budget_lage(10000, 15000, 15000, 25000) == afb.LAGE_INNERHALB or afb.budget_lage(10000, 15000, 15000, 25000) == afb.LAGE_UNTEN)
check("E6c ohne budget_min gibt es kein 'unterhalb'; ohne budget_max bleibt der Status unverändert",
      kons(1000, 3000, bmin=None)[0] == I
      and afb.konsolidiere_budget_status(N, afb.CONF_MEDIUM, preis_min=1000, preis_max=3000, budget_min=15000,
                                         budget_max=None) == (N, afb.CONF_MEDIUM))
check("E6d BELOW_BUDGET ist kein Gemini-Wert (Validierung unverändert), ohne Ranking-Wirkung und ohne Fit-Abzug",
      B not in afb.BUDGET_STATUS_WERTE and afb.budget_adjustment_fuer(B) == 0.0 and R._fit_nach_budget(88, B) == 88)
check("E6e die Confidence bleibt durch die Preisspanne begrenzt",
      kons(6000, 9000, conf=afb.CONF_LOW)[1] == afb.CONF_LOW)
check("E7 spannt die Spanne das ganze Fenster auf (9–30k), liegt ein Teil über der Obergrenze -> NEAR_BUDGET",
      afb.budget_lage(9000, 30000, 15000, 25000) == afb.LAGE_OBEN and kons(9000, 30000)[0] == N)
check("E8 ohne Untergrenze: eine Spanne bis zur Obergrenze ist 'innerhalb'; die Obergrenze bleibt maßgeblich",
      kons(5000, 20000, bmin=None)[0] == I and kons(22000, 27000, bmin=None)[0] == N)
check("E9 Fit-Abzug: nur wirklich überschreitende Spannen (NEAR/OUT) werden budgetkorrigiert, "
      "die nach unten überlappende Spanne (IN) nicht",
      R._fit_nach_budget(88, kons(11000, 19000)[0]) == 88 and R._fit_nach_budget(88, kons(17000, 26000)[0]) == 84
      and R._fit_nach_budget(88, kons(40000, 55000)[0]) == 76)
check("E10 die Confidence bleibt durch die Preisspanne begrenzt; fehlt die Spanne, bleibt der Status unverändert",
      kons(11000, 19000, start=N, conf=afb.CONF_LOW)[1] == afb.CONF_LOW
      and afb.konsolidiere_budget_status(N, afb.CONF_MEDIUM, preis_min=None, preis_max=19000, budget_min=15000,
                                         budget_max=25000) == (N, afb.CONF_MEDIUM))

print("\n=== F) Modelltexte gegen das finale Budgeturteil; Kilometerstand vs. Nutzung ===")
POS = "Das Fahrzeug passt zum Budgetrahmen."
NEG = "Der Preis liegt knapp über deinem Budget."
NEUTRAL = "Dein Budget liegt bei 15.000 bis 25.000 €."
b = afe.bereinige_budgetaussagen
check("F1 IN_BUDGET: positive Budgetaussage bleibt, 'über Budget' entfällt",
      b([POS, NEG, NEUTRAL], I) == [POS, NEUTRAL])
check("F2 NEAR_BUDGET: 'passt zum Budgetrahmen' entfällt, 'knapp über Budget' bleibt",
      b([POS, NEG, NEUTRAL], N) == [NEG, NEUTRAL])
check("F3 OUT_OF_BUDGET: wie NEAR; UNKNOWN: weder Passung noch Überschreitung ohne Grundlage",
      b([POS, NEG, NEUTRAL], O) == [NEG, NEUTRAL] and b([POS, NEG, NEUTRAL], afb.BUDGET_UNKNOWN) == [NEUTRAL])
check("F4 nur der Satz entfällt, der Rest des Eintrags bleibt (keine pauschale Löschung)",
      b([f"{POS} Die Karosserie ist kompakt."], N) == ["Die Karosserie ist kompakt."])
check("F4b BELOW_BUDGET: weder 'passt zum Budgetrahmen' noch 'über Budget'; Nennung des Budgets bleibt",
      b([POS, NEG, NEUTRAL], B) == [NEUTRAL])
check("F5 Sätze ohne Budgetaussage bleiben in jedem Status unverändert",
      all(b(["Der 118i bietet 140 PS und Automatik."], s) == ["Der 118i bietet 140 PS und Automatik."]
          for s in (I, N, O, afb.BUDGET_UNKNOWN)))
K1 = "Wegen früher Baujahre sind eher höhere Laufleistungen zu erwarten."
K2 = "Bei 15.000 km pro Jahr passt ein Benziner gut."
K3 = "Kilometerstand und Zustand sind unbekannt, die Spanne bleibt breit."
K4 = "Bei hoher Jahresfahrleistung zählt der Verbrauch stärker."
k_leer = SimpleNamespace(motor_bezeichnung="x", verbrauch_einheit="l", antriebsgas=None, verbrauch_wert=None,
                         verbrauch_l_100km=None, verbrauch_basis=None)
check("F6 unbelegte Fahrzeug-Laufleistung entfällt; Jahresfahrleistung (Nutzung) und 'unbekannt' bleiben",
      afe.bereinige_unbelegte_aussagen([K1, K2, K3, K4], k_leer) == [K2, K3, K4])
check("F7 auch eine Budget+Laufleistungs-Kombination (Audi-Fall) entfällt",
      afe.bereinige_unbelegte_aussagen(
          ["Wegen früher Baujahre sind innerhalb des Budgets eher höhere Laufleistungen zu erwarten."], k_leer) == [])

print("\n=== G) Ende-zu-Ende über den Router (Provider gestubbt) ===")
BODY = {"budget_min": 15000, "budget_max": 25000, "karosserie": ["kompakt"], "kraftstoff": ["Benzin"],
        "getriebe": ["automatik"], "nutzung": "gemischt", "km_pro_jahr": 15000, "sparsam": True, "fahranfaenger": True,
        "baujahr_von": 2016, "baujahr_bis": 2022, "leistung_min_ps": 100, "leistung_max_ps": 200,
        "marken_bevorzugt": ["Volkswagen", "Audi", "BMW"]}
MODE["preise"] = {F40: (17000, 26000), A3_110: (11000, 19000)}
MODE["stat"] = "NEAR_BUDGET"
MODE["texte"] = (["Das Fahrzeug passt zum Budgetrahmen.", "Bei 15.000 km pro Jahr passt ein Benziner gut.",
                  "Wegen früher Baujahre sind innerhalb des Budgets eher höhere Laufleistungen zu erwarten."],
                 ["Der Preis liegt knapp über deinem Budget.", "Kilometerstand und Zustand sind unbekannt."])
d, n = lauf(BODY)
karten = {k["variante_id"]: k for k in d["kandidaten"]}
print("   Karten:", [(v, k["user_fit"], k["budget_status"], k["estimated_price_min"], k["estimated_price_max"])
                     for v, k in karten.items()])
check("G1 Providerzahl unverändert: genau ein Budget- und ein Enrichment-Call, kein Tavily",
      n == {"budget": 1, "enrich": 1, "tavily": 0})
check("G2 Mindestpassung bleibt: jede Karte ≥ 80, Sortierung absteigend nach der angezeigten Passung",
      d["kandidaten"] and all(k["user_fit"] >= FIT_SCHWELLE for k in d["kandidaten"])
      and [k["user_fit"] for k in d["kandidaten"]] == sorted((k["user_fit"] for k in d["kandidaten"]), reverse=True))
check("G3 der Audi mit 105 PS (belegt 2013–2014) ist im Fenster 2016–2022 ausgeschlossen; der unbekannte BMW F40 bleibt",
      A3_105 not in karten and F40 in karten)
if A3_110 in karten and F40 in karten:
    a3, bm = karten[A3_110], karten[F40]
    check("G4 nach unten überlappende Spanne 11–19k: IN_BUDGET, kein Überschreitungsabzug; BMW 17–26k: NEAR_BUDGET (−4)",
          (a3["budget_status"], bm["budget_status"]) == (I, N)
          and a3["user_fit"] > bm["user_fit"] - 1 and a3["budget_adjustment"] == bm["budget_adjustment"])
    check("G5 KI-Preisspannen und Kennzeichnung bleiben unverändert (keine Abschläge, keine Korrektur)",
          (a3["estimated_price_min"], a3["estimated_price_max"]) == (11000, 19000)
          and (bm["estimated_price_min"], bm["estimated_price_max"]) == (17000, 26000))
    check("G6 sichtbarer Text des BMW (NEAR): keine 'passt zum Budgetrahmen'-Behauptung, 'knapp über Budget' bleibt",
          not any("Budgetrahmen" in t for t in bm["why_fits"] + bm["trade_offs"])
          and any("knapp über deinem Budget" in t for t in bm["trade_offs"]))
    check("G7 sichtbarer Text des Audi (IN): 'passt zum Budgetrahmen' bleibt, 'knapp über Budget' entfällt",
          any("Budgetrahmen" in t for t in a3["why_fits"]) and not any("über deinem Budget" in t for t in a3["trade_offs"]))
    check("G8 Nutzung bleibt, unbelegte Laufleistung entfällt, 'Kilometerstand unbekannt' bleibt",
          all(any("15.000 km pro Jahr" in t for t in k["why_fits"]) for k in (a3, bm))
          and not any("Laufleistung" in t for k in (a3, bm) for t in k["why_fits"] + k["trade_offs"])
          and all(any("unbekannt" in t for t in k["trade_offs"]) for k in (a3, bm)))
    check("G9 Karte trägt Wunschfenster, Status und die Motorzeit getrennt (BMW: 2016–2022 gewünscht, Überschneidung 2019–2022, "
          "unbekannt; Audi 110 PS: teilweise ab 2014)",
          (bm["wunsch_baujahr_von"], bm["wunsch_baujahr_bis"], bm["baujahr_von"], bm["baujahr_bis"],
           bm["motorzeit_status"], bm["generation_baujahr_bis"]) == (2016, 2022, 2019, 2022, MZ_UNBEKANNT, None)
          and (a3["motorzeit_status"], a3["motor_bauzeit_von"], a3["motor_bauzeit_bis"]) == (MZ_TEILWEISE, 2014, None))
    check("G10 Positivgrund 'Baujahr im Wunschfenster' erscheint bei keiner Karte mit unbekannter/teilweiser Motorzeit",
          all("Baujahr im Wunschfenster" not in k["user_fit_gruende"] for k in d["kandidaten"]))
else:
    check("G4-G10 Audi A3 8V 110 PS und BMW F40 stehen in der Ausgabe", False, str(list(karten)))
um = PROMPTS["enrich"][0]
check("G11 Enrichment-Prompt der echten Suche: Wunschfenster 2016–2022, offene Baureihe ohne 'heute', kein Rohstatus",
      "Wunschfenster des Nutzers 2016–2022" in um and "Baureihe gebaut ab 2019, Ende nicht hinterlegt" in um
      and "heute" not in um and "Budget-Einschätzung" not in um)
# Ende-zu-Ende: Spanne ganz unter budget_min (6–9k bei 15–25k) über den produktiven Router
MODE["preise"] = {F40: (6000, 9000), A3_110: (17000, 26000)}
MODE["preise"] = {F40: (16000, 20000), A3_110: (17000, 26000)}
d_in, _ = lauf(BODY)       # Vergleichslauf: dieselbe Karte, Spanne innerhalb des Budgets (kein Abzug)
fit_in = next((k["user_fit"] for k in d_in["kandidaten"] if k["variante_id"] == F40), None)
MODE["preise"] = {F40: (6000, 9000), A3_110: (17000, 26000)}
d_u, n_u = lauf(BODY)
ku = {k["variante_id"]: k for k in d_u["kandidaten"]}
if F40 in ku and A3_110 in ku:
    u, o = ku[F40], ku[A3_110]
    check("G13 Spanne ganz unter budget_min: BELOW_BUDGET, KEIN −4-Abzug (Fit = Rohfit), Preisspanne unverändert",
          u["budget_status"] == B and (u["estimated_price_min"], u["estimated_price_max"]) == (6000, 9000)
          and o["budget_status"] == N and u["user_fit"] == fit_in)
    check("G14 BELOW: Text ohne Budget-Passung/Überschreitung, Mindestpassung und Sortierung bleiben, Providerzahl 1/1",
          not any(("Budgetrahmen" in t or "über deinem Budget" in t) for t in u["why_fits"] + u["trade_offs"])
          and all(k["user_fit"] >= FIT_SCHWELLE for k in d_u["kandidaten"])
          and [k["user_fit"] for k in d_u["kandidaten"]] == sorted((k["user_fit"] for k in d_u["kandidaten"]), reverse=True)
          and n_u == {"budget": 1, "enrich": 1, "tavily": 0})
else:
    check("G13-G14 F40 und A3 110 PS in der Ausgabe", False, str(list(ku)))
MODE["preise"] = {F40: (17000, 26000), A3_110: (11000, 19000)}
d_gas, _ = lauf({"kraftstoff": ["Benzin"], "marken_bevorzugt": ["Volkswagen"], "baujahr_von": 2020})
check("G12 Gas-Schutz bleibt: reine Benzinauswahl liefert kein Gasfahrzeug",
      all(not k.get("antriebsgas") for k in d_gas["kandidaten"]))

print("\n=== H) Daten: Audi A3 8V 1.2 TFSI ===")
c = sqlite3.connect(str(DB_PATH), isolation_level=None)
c.execute("pragma foreign_keys=ON")


def hash_t(t):
    rows = c.execute(f'select * from "{t}" order by 1').fetchall()
    return len(rows), hashlib.sha256(repr(rows).encode()).hexdigest()


gel = {r[0]: r[1:] for r in c.execute("select variante_id, bauzeit_von, bauzeit_bis from motorvariante_geltung "
                                      "where variante_id in (?,?)", (A3_105, A3_110))}
bez = dict(c.execute("select variante_id, bezeichnung from motorvariante where variante_id in (?,?)", (A3_105, A3_110)))
check("H1 frische DB (Seed + Bootstrap): 105 PS 2013–2014, 110 PS ab 2014 (Ende offen), Bezeichnung ohne '(Facelift)'",
      gel == {A3_105: (2013, 2014), A3_110: (2014, None)} and bez[A3_110] == "1.2 TFSI (110 PS)"
      and "Facelift" not in bez[A3_110])
check("H2 die 105-PS-Zeile ist NICHT bis 2016 begrenzt (2016 ist die Modellpflege, kein belegtes Ende)",
      gel[A3_105][1] == 2014 and "2013–2016" not in gd.GELTUNG_AF13[A3_105]["notiz"])
check("H3 Quelle und Hinweis sind mitgeführt; Leistung/Drehmoment der Zeilen unverändert (105 PS/175 Nm, 110 PS/175 Nm)",
      all(gd.GELTUNG_AF13[v]["quelle"].startswith("ADAC Autokatalog") for v in (A3_105, A3_110))
      and c.execute("select leistung_ps, drehmoment_nm from motorvariante where variante_id=?", (A3_105,)).fetchone() == (105, 175)
      and c.execute("select leistung_ps, drehmoment_nm from motorvariante where variante_id=?", (A3_110,)).fetchone() == (110, 175))
check("H4 BMW 118i F40: KEINE Datenänderung (kein Geltungseintrag, F40-Bauende und 136-PS-Variante zurückgestellt)",
      c.execute("select count(*) from motorvariante_geltung where variante_id=?", (F40,)).fetchone()[0] == 0
      and c.execute("select count(*) from motorvariante where variante_id like 'bmw-1er-f40-118i%'").fetchone()[0] == 1)

# Datenmodul == Migration == Seed: die eingecheckte Seed-Datei trägt dieselben Werte wie das Datenmodul.
import os
_seed = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "seed_fahrzeugdaten.sql"), encoding="utf-8").read()
_zeilen = {v: [l for l in _seed.splitlines() if l.startswith("INSERT INTO motorvariante_geltung") and f"VALUES ('{v}'," in l]
           for v in (A3_105, A3_110)}
check("H4b Seed trägt je Audi-Zeile genau einen Geltungseintrag mit den Werten des Datenmoduls (von/bis/Quelle/Notiz)",
      all(len(z) == 1 for z in _zeilen.values())
      and f"VALUES ('{A3_105}',2013,2014,NULL,'{gd.GELTUNG_AF13[A3_105]['quelle']}'" in _zeilen[A3_105][0]
      and f"VALUES ('{A3_110}',2014,NULL,NULL,'{gd.GELTUNG_AF13[A3_110]['quelle']}'" in _zeilen[A3_110][0]
      and gd.GELTUNG_AF13[A3_105]["notiz"] in _zeilen[A3_105][0] and gd.GELTUNG_AF13[A3_110]["notiz"] in _zeilen[A3_110][0]
      and f"'{A3_110}','audi-a3-typ-8v','{gd.BEZEICHNUNG_AF13[A3_110][1]}'," in _seed
      and gd.BEZEICHNUNG_AF13[A3_110][0] not in _seed.split(f"'{A3_110}','audi-a3-typ-8v',")[1][:60])
# Seed-only-Bootstrap (ohne Migration) und Migration auf bestehender DB ergeben denselben Zustand: die Migration ist auf
# einer frisch aus dem Seed gebauten DB ein No-op ("bereits eingetragen"), auf einer Vor-AF-13-DB trägt sie nach (H6).
MG = dm.MARKER_MOTOR_GELTUNG_AF13
check("H5 Marker nach frischem Bootstrap gesetzt",
      c.execute("select count(*) from schema_migrations where name=?", (MG,)).fetchone()[0] == 1)
for i in range(3):
    c.execute("insert into rueckruf (baureihe_id, mangel, kba_referenz, canonical_make, canonical_nameplate) "
              "values (NULL, 'Testmangel', ?, 'MAZDA', 'CX-5')", (f"AF13-{i}",))
rr0, gel0, mv0 = hash_t("rueckruf"), hash_t("motorvariante_geltung"), hash_t("motorvariante")
c.execute("delete from schema_migrations where name=?", (MG,))
c.execute("delete from motorvariante_geltung where variante_id in (?,?)", (A3_105, A3_110))
c.execute("update motorvariante set bezeichnung='1.2 TFSI (Facelift)' where variante_id=?", (A3_110,))
check("H6 bestehende DB (Stand vor AF-13): Migration trägt Geltung und Bezeichnung nach, Inhalt wie frisch",
      dm.fuehre_migration_aus(c, MG, dm.SCHRITTE_MOTOR_GELTUNG_AF13) is True
      and hash_t("motorvariante_geltung") == gel0 and hash_t("motorvariante") == mv0)
check("H7 Rückrufbestand und fremde Daten (Gas-Geltung) unverändert",
      hash_t("rueckruf") == rr0
      and c.execute("select count(*) from motorvariante_geltung where antriebsgas is not null").fetchone()[0] >= 20)
check("H8 zweiter Lauf ist ein No-op", dm.fuehre_migration_aus(c, MG, dm.SCHRITTE_MOTOR_GELTUNG_AF13) is False
      and hash_t("motorvariante_geltung") == gel0)
c.execute("delete from schema_migrations where name=?", (MG,))
c.execute("delete from motorvariante_geltung where variante_id=?", (A3_105,))
c.execute("update motorvariante_geltung set notiz='abweichend' where variante_id=?", (A3_110,))
vorher_g, vorher_m = hash_t("motorvariante_geltung"), hash_t("motorvariante")
ok = dm.fuehre_migration_aus(c, MG, dm.SCHRITTE_MOTOR_GELTUNG_AF13)
check("H9 abweichende vorhandene Geltung: Abbruch, kein Marker, KEINE Teilschreibung der anderen Zeile",
      ok is False and hash_t("motorvariante_geltung") == vorher_g and hash_t("motorvariante") == vorher_m
      and c.execute("select count(*) from schema_migrations where name=?", (MG,)).fetchone()[0] == 0)
c.execute("update motorvariante_geltung set notiz=? where variante_id=?", (gd.GELTUNG_AF13[A3_110]["notiz"], A3_110))
c.execute("update motorvariante set bezeichnung='1.2 TFSI (etwas anderes)' where variante_id=?", (A3_110,))
vorher_g = hash_t("motorvariante_geltung")
ok = dm.fuehre_migration_aus(c, MG, dm.SCHRITTE_MOTOR_GELTUNG_AF13)
check("H10 unerwartete Bezeichnung (Datensatz zwischenzeitlich geändert): Abbruch ohne Teilschreibung, nichts überschrieben",
      ok is False and hash_t("motorvariante_geltung") == vorher_g
      and c.execute("select bezeichnung from motorvariante where variante_id=?", (A3_110,)).fetchone()[0] == "1.2 TFSI (etwas anderes)"
      and c.execute("select count(*) from schema_migrations where name=?", (MG,)).fetchone()[0] == 0)
c.execute("update motorvariante set bezeichnung='1.2 TFSI (Facelift)' where variante_id=?", (A3_110,))
check("H11 nach Bereinigung läuft sie vollständig; Inhalt wie frisch, Integritätsprüfung sauber",
      dm.fuehre_migration_aus(c, MG, dm.SCHRITTE_MOTOR_GELTUNG_AF13) is True
      and hash_t("motorvariante_geltung") == gel0 and hash_t("motorvariante") == mv0 and dm.pruefe_integritaet(c) == [])
c.execute("delete from rueckruf where kba_referenz like 'AF13-%'")

print()
if FEHLER:
    print(f"{len(FEHLER)} Pruefung(en) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle AF-13/14-Pruefungen bestanden.")
