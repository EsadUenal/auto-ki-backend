"""
AF-10 / AF-11 / AF-12 und konkreter AF-03/07-Kontext -- Gegenproben (offline).

    python scripts/test_isolated.py test_autofinder_af10_12.py

AF-10  Gasantrieb und Verbrauchseinheit (kg/l/unbekannt) datengetragen, Filter, Fit, Kontext.
AF-11  Ausgabe nach der ANGEZEIGTEN Passung sortiert (stabil).
AF-12  Verbrauchszyklus nur bei belegtem Zyklus; der 120i-Rohdatensatz bleibt unveraendert.
AF-07  Golf VII 1.2 TSI: belegte Bauzeit; Preiskontext (Motorzeit statt Wunschfenster, Jahresfahrleistung
       ist kein Kilometerstand, Verlaesslichkeit im Backend begrenzt, Preisspanne bleibt).

Echte Seed-Daten (Temp-DB des Isolationsrunners), echte Engine/Fit/Produzentenpfade. Nur die Provider-
antworten sind gestubbt, Tavily ist verboten. NACHWEISGRENZE: Kontingent ist ein No-op, Gemini-Antworten
sind konstruierte Annahmen.
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
import app.autofinder_web as afw
import app.data_migrations as dm
import app.database as database
import app.routers.autofinder as R
from app.autofinder_fit import berechne_fit
from app.autofinder_variante import pruefe_hardfilter_einhaltung
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
MODE = {"stat": None, "preise": None, "conf": "MEDIUM"}


async def _tavily(*a, **k):
    CALLS["tavily"] += 1
    raise AssertionError("Tavily verboten")


afw.tavily_search_mit_status = _tavily


async def _budget(sp, um):
    CALLS["budget"] += 1
    ids = re.findall(r"candidate_id=(\S+)", um)
    stat = MODE["stat"] or ["IN_BUDGET"]
    return {"candidates": [{"candidate_id": i, "budget_status": stat[n % len(stat)], "confidence": "MEDIUM"}
                           for n, i in enumerate(ids)]}


async def _enrich(sp, um):
    CALLS["enrich"] += 1
    ids = re.findall(r"candidate_id=(\S+)", um)
    pr = MODE["preise"] or [(18000, 25000)]
    return {"candidates": [{"candidate_id": i, "why_fits": ["Grund eins", "Grund zwei", "Grund drei"],
                            "trade_offs": ["Nachteil eins", "Nachteil zwei"],
                            "estimated_price_min": pr[n % len(pr)][0], "estimated_price_max": pr[n % len(pr)][1],
                            "price_confidence": MODE["conf"]} for n, i in enumerate(ids)]}


afb.call_gemini_json = _budget
afe.call_gemini_json = _enrich
client = TestClient(_app)
H = {"Authorization": "Bearer dev-key-change-in-prod"}


def lauf(body):
    CALLS.update(budget=0, enrich=0, tavily=0)
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


TGI = "volkswagen-golf-viii-1.5-tgi"
TSI15 = "volkswagen-golf-viii-1.5-tsi-110-kw"
UP_TGI = "volkswagen-up-up-(facelift)-1.0-tgi-(68-ps)"

print("=== AF-10) Gasantrieb und Verbrauchseinheit ===")
t = roh(TGI)
check("A1 der Golf VIII 1.5 TGI ist als primär CNG mit Einheit kg und belegtem WLTP-Zyklus geführt (Wert 4,1 unverändert)",
      (t["antriebsgas"], t["_verbrauch_einheit"], t["verbrauch_zyklus"], t["_verbrauch_anzeige"]) == ("CNG", "kg", "WLTP", 4.1))
check("A2 kg wird nicht als Liter bewertbar: _verbrauch ist leer, die Datensatzklasse bleibt 'Benzin'",
      t["_verbrauch"] is None and t["kraftstoff"] == "Benzin")
check("A3 ein regulärer Benziner bleibt Liter und bewertbar (1.5 TSI)",
      roh(TSI15)["_verbrauch_einheit"] == "l" and roh(TSI15)["_verbrauch"] is not None and not roh(TSI15)["antriebsgas"])
u = roh(UP_TGI)
check("A4 Gasfahrzeug ohne belegte Einheit: Einheit 'unbekannt', nicht als Liter bewertet, nicht als kg erfunden",
      u["antriebsgas"] == "Gas" and u["_verbrauch_einheit"] == "unbekannt" and u["_verbrauch"] is None
      and u["_verbrauch_anzeige"] is not None)
gas_ids = [r["variante_id"] for r in BASIS if r["antriebsgas"]]
check("A5 es sind 16 Gasfahrzeuge gekennzeichnet; nur der Golf VIII TGI trägt eine belegte Einheit",
      len(gas_ids) == 16 and [r["variante_id"] for r in BASIS if r["antriebsgas"] and r["verbrauch_einheit"]] == [TGI])

check("B1 reine Benzinauswahl: das primär mit CNG betriebene Fahrzeug ist kein Treffer, der reguläre Benziner schon",
      TGI not in treffer(marken_bevorzugt=["Volkswagen"], kraftstoff=["Benzin"])
      and TSI15 in treffer(marken_bevorzugt=["Volkswagen"], kraftstoff=["Benzin"]))
check("B2 ohne Kraftstoffbeschränkung dürfen gekennzeichnete Gasfahrzeuge vorgeschlagen werden",
      TGI in treffer(marken_bevorzugt=["Volkswagen"], baujahr_von=2020))
check("B3 ausdrückliche Gaswahl lässt das Gasfahrzeug zu und schließt reguläre Benziner aus",
      TGI in treffer(marken_bevorzugt=["Volkswagen"], kraftstoff=["CNG"])
      and TSI15 not in treffer(marken_bevorzugt=["Volkswagen"], kraftstoff=["CNG"]))
check("B4 Benzin UND Gas: beide",
      {TGI, TSI15} <= set(treffer(marken_bevorzugt=["Volkswagen"], kraftstoff=["Benzin", "Erdgas"])))
k_tgi = af._zu_kandidat(t, 0.0, [], 1.0, af.AutoFinderRequest())
v_gas = pruefe_hardfilter_einhaltung(SimpleNamespace(candidate_id="x", kraftstoff="Benzin", antriebsgas="CNG", karosserie=[],
                                                     getriebe=[], antrieb=None, leistung_ps=None),
                                     af.AutoFinderRequest(kraftstoff=["Benzin"]))
v_ok = pruefe_hardfilter_einhaltung(SimpleNamespace(candidate_id="x", kraftstoff="Benzin", antriebsgas="CNG", karosserie=[],
                                                    getriebe=[], antrieb=None, leistung_ps=None),
                                    af.AutoFinderRequest(kraftstoff=["CNG"]))
check("B5 die Endvalidierung weist ein Gasfahrzeug in einer reinen Benzinauswahl ab, bei Gaswahl nicht", bool(v_gas) and not v_ok)

req_s = af.AutoFinderRequest(sparsam=True, nutzung="stadt")
f_tgi = berechne_fit(k_tgi, req_s)
k_tsi = af._zu_kandidat(roh(TSI15), 0.0, [], 1.0, req_s)
f_tsi = berechne_fit(k_tsi, req_s)
check("C1 Sparsamkeit: für kg-Verbrauch keine Bewertung (kein Bonus aus 4,1 gegen Liter-Schwellen), für Liter schon",
      "Sparsam" not in [c.label for c in f_tgi.komponenten] and "Sparsam" in [c.label for c in f_tsi.komponenten])
k_up = af._zu_kandidat(u, 0.0, [], 1.0, req_s)
check("C2 unbekannte Einheit: ebenfalls keine Sparsamkeitsbewertung",
      "Sparsam" not in [c.label for c in berechne_fit(k_up, req_s).komponenten])
gr = af._score_kandidat(t, af.AutoFinderRequest(sparsam=True))[1]
check("C3 der Ranking-Score nennt keinen Verbrauchsgrund für kg-Werte", not any("Verbrauch" in g for g in gr), str(gr))
check("C4 Datenqualität zählt den hinterlegten Wert weiter mit (kein künstlicher Abzug)",
      af._datenqualitaet(t) == af._datenqualitaet({**t, "_verbrauch_anzeige": 4.1, "_verbrauch": 4.1}))

blk = afe._kandidat_block(k_tgi, af.AutoFinderRequest())
check("D1 Modellkontext: Kraftstoff 'primär Erdgas (CNG), Benzin nur als Reserve', Verbrauch in kg mit Zyklus, ausdrücklich nicht Liter",
      "primär Erdgas (CNG), Benzin nur als Reserve" in blk and "4.1 kg/100km Gas, NICHT Liter" in blk
      and "WLTP laut ENFAL-Datensatz" in blk and "l/100km" not in blk, blk)
blk_up = afe._kandidat_block(k_up, af.AutoFinderRequest())
check("D2 unbekannte Einheit: der Kontext sagt 'UNBEKANNTER Einheit', nennt keine Literangabe",
      "UNBEKANNTER Einheit" in blk_up and "l/100km" not in blk_up, blk_up)
check("D3 Prompt-Regeln: Einheit, Gas-Reserve, WLTP nur bei genanntem Zyklus",
      "bei Gas kg/100 km, nie Liter" in afe._SYSTEM_PROMPT and "kein gewöhnlicher Benziner" in afe._SYSTEM_PROMPT
      and "nur, wenn die Angabe den Zyklus WLTP nennt" in afe._SYSTEM_PROMPT)
S = afe.bereinige_unbelegte_aussagen
check("D4 Modelltext: '4,1 l/100 km' beim Gasfahrzeug entfällt, '4,1 kg/100 km' bleibt, WLTP bleibt (belegt)",
      not S(["Der Verbrauch liegt bei 4,1 l/100 km."], k_tgi) and S(["Der Verbrauch liegt bei 4,1 kg/100 km."], k_tgi)
      and S(["Laut WLTP sind es 4,1 kg/100 km."], k_tgi))
check("D5 unbekannte Einheit: jede Verbrauchszahl entfällt", not S(["Der Verbrauch liegt bei 3,6 kg/100 km."], k_up)
      and not S(["Der Verbrauch liegt bei 3,6 l/100 km."], k_up))
check("D6 regulärer Benziner: Literangabe mit passender Zahl bleibt (kein WLTP ohne Beleg)",
      S(["Der Verbrauch liegt bei " + f"{k_tsi.verbrauch_wert:.1f}".replace(".", ",") + " l/100 km."], k_tsi)
      and not S(["WLTP: " + f"{k_tsi.verbrauch_wert:.1f}".replace(".", ",") + " l/100 km."], k_tsi))

d_b, c_b = lauf({"kraftstoff": ["Benzin"], "nutzung": "stadt", "sparsam": True})
check("E1 Endpunkt: eine Benzinauswahl liefert keinen Gas-Treffer (ohne Websuche)",
      c_b["tavily"] == 0 and d_b["kandidaten"] and not any(k["antriebsgas"] for k in d_b["kandidaten"]))
# Die API-Kraftstoffwahl kennt kein Gas (Vokabular unveraendert); Gas wird ohne Kraftstoffwahl vorgeschlagen.
# Der Vertrag der Karte wird ueber den produktiven Karten-Baupfad geprueft.
body = R.AutoFinderRequest(nutzung="stadt")
karte = R._zu_kandidat_out(k_tgi, R._konkrete_variante(k_tgi, body), enrichment=afe.deterministischer_fallback(k_tgi),
                           enrichment_status="fallback")
check("E2 Karte des Gasfahrzeugs: Datensatzklasse Benzin, aber Hinweis, Einheit kg und belegter Zyklus im Vertrag",
      (karte.kraftstoff, karte.kraftstoff_hinweis, karte.verbrauch_einheit, karte.verbrauch_zyklus, karte.antriebsgas)
      == ("Benzin", "primär Erdgas (CNG), Benzin nur als Reserve", "kg", "WLTP", "CNG"))
karte_u = R._zu_kandidat_out(k_up, R._konkrete_variante(k_up, body), enrichment=afe.deterministischer_fallback(k_up),
                             enrichment_status="fallback")
check("E3 Gasfahrzeug mit unbekannter Einheit: Vertrag sagt 'unbekannt', keinen Zyklus",
      karte_u.verbrauch_einheit == "unbekannt" and karte_u.verbrauch_zyklus is None and karte_u.antriebsgas == "Gas")
print("\n=== AF-11) Sortierung nach angezeigter Passung ===")
MODE["stat"] = ["IN_BUDGET", "NEAR_BUDGET", "IN_BUDGET", "OUT_OF_BUDGET", "NEAR_BUDGET"]
MODE["preise"] = [(8000, 12000), (14000, 18000), (9000, 13000), (22000, 30000), (15000, 19000)]
d_m, calls_m = lauf({"nutzung": "stadt", "sparsam": True, "budget_max": 15000, "kraftstoff": ["Benzin"]})
fit = [k["user_fit"] for k in d_m["kandidaten"]]
print("   angezeigte Passung:", fit, [k["budget_status"][:3] for k in d_m["kandidaten"]])
check("F1 Testvoraussetzung: Karten mit unterschiedlicher Budgetkorrektur", len(set(fit)) > 1 and len(fit) >= 3)
check("F2 die angezeigte Passung ist über die Liste absteigend", fit == sorted(fit, reverse=True))
check("F3 Pool/Anzahl und Provider unverändert (je ein Budget-/Enrichment-Aufruf, kein Tavily), Schwelle 80 gilt",
      calls_m == {"budget": 1, "enrich": 1, "tavily": 0} and all(f >= 80 for f in fit))
MODE["stat"] = None; MODE["preise"] = None
d_ohne, _ = lauf({"nutzung": "stadt", "sparsam": True, "kraftstoff": ["Benzin"]})
d_alle, _ = lauf({"nutzung": "stadt", "sparsam": True, "kraftstoff": ["Benzin"], "budget_max": 30000})
check("F4 Gleichstand: ohne Korrektur bleibt die bisherige Reihenfolge exakt erhalten (stabil)",
      [k["candidate_id"] for k in d_ohne["kandidaten"]] == [k["candidate_id"] for k in d_alle["kandidaten"]]
      and len({k["user_fit"] for k in d_alle["kandidaten"]}) < len(d_alle["kandidaten"]) + 1)

print("\n=== AF-12) Verbrauchszyklus und der 120i-Rohdatensatz ===")
V120 = roh("bmw-1er-f2x-120i-n20")
check("G1 die Spalte verbrauch_wltp belegt keinen Zyklus: der 120i-Verbrauchstext sagt kein 'WLTP'",
      "WLTP" not in af._verbrauch_text(V120) and "Zyklus nicht geprüft" in af._verbrauch_text(V120))
k120 = af._zu_kandidat(V120, 0.0, [], 1.0, af.AutoFinderRequest(sparsam=True))
b120 = afe._kandidat_block(k120, af.AutoFinderRequest())
check("G2 der Modellkontext nennt für den 120i 'Angabe laut ENFAL-Datensatz, Zyklus nicht geprüft' und keine Liter-Umdeutung",
      "Zyklus nicht geprüft" in b120 and "WLTP laut" not in b120 and "l/100km" in b120, b120)
check("G3 der 120i-Rohdatensatz ist unverändert (184 PS, 270 Nm, N20B20, Manuell/Automatik)",
      (V120["leistung_ps"], V120["drehmoment_nm"], V120["motorcode"], sorted(V120["_getriebe"])) ==
      (184, 270, "N20B20", ["automatik", "manuell"]))
check("G4 belegt ist der Zyklus nur beim Golf VIII TGI (WLTP); sonst keine Zyklusangabe in den Daten",
      [r["variante_id"] for r in BASIS if r["verbrauch_zyklus"]] == [TGI])

print("\n=== AF-03/07) Golf VII 1.2 TSI: belegte Bauzeit und Preiskontext ===")
G105, G85, G14 = "volkswagen-golf-vii-1.2-tsi-77kw", "volkswagen-golf-vii-1.2-tsi-63kw", None
check("H1 belegte Grenzen 2013–2015 für 105 PS und 85 PS",
      all((roh(v)["motor_bauzeit_von"], roh(v)["motor_bauzeit_bis"]) == (2013, 2015) for v in (G105, G85)))
w_alt = treffer(marken_bevorzugt=["Volkswagen"], baujahr_von=2013, baujahr_bis=2015)
w_neu = treffer(marken_bevorzugt=["Volkswagen"], baujahr_von=2016, baujahr_bis=2019)
check("H2 2013–2015 bleibt passend", G105 in w_alt and G85 in w_alt)
check("H3 2016–2019 gilt NICHT als belegte Produktionszeit der 105-PS-Ausführung", G105 not in w_neu and G85 not in w_neu)
check("H4 andere Golf-VII-Motoren ohne Grenzen sind unverändert (2016–2019 weiter passend)",
      any(v.startswith("volkswagen-golf-vii-") and v not in (G105, G85) for v in w_neu))
k105 = af._zu_kandidat(roh(G105), 0.0, [], 1.0, af.AutoFinderRequest(baujahr_von=2013, baujahr_bis=2019))
check("H5 Karte: Fenster auf die belegte Bauzeit begrenzt (2013–2015), Generation bleibt 2012–2020",
      (k105.baujahr_von, k105.baujahr_bis, k105.generation_baujahr_von) == (2013, 2015, 2012))

k105w = af._zu_kandidat(roh(G105), 0.0, [], 1.0, af.AutoFinderRequest(baujahr_von=2014, baujahr_bis=2019))
um = afe._baue_user_message([k105w], af.AutoFinderRequest(km_pro_jahr=15000, baujahr_von=2014, baujahr_bis=2019))
check("I1 Prompt: Wunschfenster = Suchwunsch, belegte Motorzeit statt Wunschfenster als Fahrzeugzeit",
      "Motorzeitraum laut Datensatz ab 2013 bis 2015" in um and "Wunschfenster des Nutzers 2014–2015" in um
      and "KEIN Baujahr des Fahrzeugs" in afe._SYSTEM_PROMPT)
check("I2 Jahresfahrleistung steht ausdrücklich als Nutzung, nicht als Kilometerstand (Nutzer-Kontext und Preisregel)",
      "KEIN Kilometerstand eines Fahrzeugs" in um and "NICHT der Kilometerstand eines Fahrzeugs" in afe._SYSTEM_PROMPT
      and "Kilometerstand und Zustand sind unbekannt" in afe._SYSTEM_PROMPT)

mk = lambda **kw: SimpleNamespace(price_confidence=kw.get("c", "MEDIUM"), estimated_price_min=18000, estimated_price_max=25000)
K_INT = SimpleNamespace(generation_baujahr_von=2012, motor_bauzeit_von=None, motor_bauzeit_bis=None)
K_BEL = SimpleNamespace(generation_baujahr_von=2012, motor_bauzeit_von=2013, motor_bauzeit_bis=2015)
K_WEB = SimpleNamespace(generation_baujahr_von=None, motor_bauzeit_von=None, motor_bauzeit_bis=None)
REQ_F = SimpleNamespace(baujahr_von=2016, baujahr_bis=2019)
REQ_0 = SimpleNamespace(baujahr_von=None, baujahr_bis=None)


def conf(c, k, r):
    e = mk(c=c); afe.preis_verlaesslichkeit(e, k, r); return e.price_confidence, (e.estimated_price_min, e.estimated_price_max)


check("J1 HIGH wird ohne Kilometer-/Zustandsbasis höchstens MEDIUM (Spanne unverändert)",
      conf("HIGH", K_BEL, REQ_0) == ("MEDIUM", (18000, 25000)))
check("J2 Wunschfenster ohne belegte Motorzeit: nur LOW (Spanne unverändert, kein Abschlag)",
      conf("MEDIUM", K_INT, REQ_F) == ("LOW", (18000, 25000)) and conf("HIGH", K_INT, REQ_F)[0] == "LOW")
check("J3 belegte Motorzeit: MEDIUM bleibt MEDIUM", conf("MEDIUM", K_BEL, REQ_F)[0] == "MEDIUM")
check("J4 ohne Wunschfenster bleibt MEDIUM; Web-Kandidat mit belegter Bauzeit unberührt",
      conf("MEDIUM", K_INT, REQ_0)[0] == "MEDIUM" and conf("MEDIUM", K_WEB, REQ_F)[0] == "MEDIUM")
check("J5 UNKNOWN und fehlende Spanne bleiben unberührt",
      conf("UNKNOWN", K_INT, REQ_F)[0] == "UNKNOWN"
      and (lambda e: (afe.preis_verlaesslichkeit(e, K_INT, REQ_F), e.price_confidence)[1])(
          SimpleNamespace(price_confidence="MEDIUM", estimated_price_min=None, estimated_price_max=None)) == "MEDIUM")

MODE["conf"] = "MEDIUM"
d_f, _ = lauf({"nutzung": "stadt", "sparsam": True, "kraftstoff": ["Benzin"], "baujahr_von": 2016, "baujahr_bis": 2019})
d_n, _ = lauf({"nutzung": "stadt", "sparsam": True, "kraftstoff": ["Benzin"]})
check("J6 Endpunkt mit Wunschfenster ohne belegte Motorzeit: Verlässlichkeit LOW, Spanne 18.000–25.000 € bleibt auf den Karten",
      d_f["kandidaten"] and all(k["price_confidence"] == "LOW" and (k["estimated_price_min"], k["estimated_price_max"]) == (18000, 25000)
                                for k in d_f["kandidaten"]))
check("J7 Endpunkt ohne Wunschfenster: MEDIUM bleibt, Preisorientierung erhalten",
      d_n["kandidaten"] and all(k["price_confidence"] == "MEDIUM" and k["estimated_price_min"] == 18000 for k in d_n["kandidaten"]))

print("\n=== Bootstrap, Idempotenz, canonical-only-Rückrufmodell ===")
c = sqlite3.connect(str(DB_PATH), isolation_level=None)
c.execute("pragma foreign_keys=ON")


def hash_t(t):
    rows = c.execute(f'select * from "{t}" order by 1').fetchall()
    return len(rows), hashlib.sha256(repr(rows).encode()).hexdigest()


M10 = dm.MARKER_MOTOR_GELTUNG_AF10
check("K1 Bootstrap: Marker und alle 22 Geltungszeilen sind da (Seed + Migrationen)",
      c.execute("select count(*) from schema_migrations where name=?", (M10,)).fetchone()[0] == 1
      and c.execute("select count(*) from motorvariante_geltung").fetchone()[0] == 22)
for i in range(5):
    c.execute("insert into rueckruf (baureihe_id, mangel, kba_referenz, canonical_make, canonical_nameplate) "
              "values (NULL, 'Testmangel', ?, 'MAZDA', 'CX-5')", (f"AF10-{i}",))
rr0, gel0 = hash_t("rueckruf"), hash_t("motorvariante_geltung")
c.execute("delete from schema_migrations where name=?", (M10,))
from app import motor_geltung_daten as gd
c.execute("delete from motorvariante_geltung where variante_id in (%s)" % ",".join("?" * len(gd.GELTUNG_AF10)),
          list(gd.GELTUNG_AF10))
dm.protokoll.clear()
check("K2 die Migration läuft mit canonical-only-Rückrufen (Marker neu gesetzt, 22 Zeilen wieder da)",
      dm.fuehre_migration_aus(c, M10, dm.SCHRITTE_MOTOR_GELTUNG_AF10) is True and hash_t("motorvariante_geltung") == gel0)
check("K3 Rückrufbestand durch die Migration inhaltlich unverändert (Anzahl und Hash)", hash_t("rueckruf") == rr0)
check("K4 zweiter Lauf ist ein No-op", dm.fuehre_migration_aus(c, M10, dm.SCHRITTE_MOTOR_GELTUNG_AF10) is False
      and hash_t("motorvariante_geltung") == gel0)
# Abweichende vorhandene Geltung wird nicht ueberschrieben
c.execute("delete from schema_migrations where name=?", (M10,))
c.execute("update motorvariante_geltung set bauzeit_von=1999 where variante_id=?", (G105,))
vorher = hash_t("motorvariante_geltung")
ok = dm.fuehre_migration_aus(c, M10, dm.SCHRITTE_MOTOR_GELTUNG_AF10)
check("K5 eine abweichende vorhandene Geltung wird nicht überschrieben: Abbruch, kein Marker, keine Teilschreibung",
      ok is False and hash_t("motorvariante_geltung") == vorher
      and c.execute("select count(*) from schema_migrations where name=?", (M10,)).fetchone()[0] == 0)
c.execute("update motorvariante_geltung set bauzeit_von=2013 where variante_id=?", (G105,))
dm.fuehre_migration_aus(c, M10, dm.SCHRITTE_MOTOR_GELTUNG_AF10)

# Spaltenmigration auf einer bereits angelegten Tabelle (Produktionsstand nach AF-03)
c2 = sqlite3.connect(":memory:")
c2.execute("create table motorvariante_geltung (variante_id text primary key, bauzeit_von integer, bauzeit_bis integer, "
           "getriebe_unbestaetigt text, quelle text not null, notiz text)")
c2.execute("insert into motorvariante_geltung values ('x', 2015, NULL, NULL, 'q', 'n')")
database._migriere_geltung_spalten(c2); database._migriere_geltung_spalten(c2)
cols = {r[1] for r in c2.execute("pragma table_info(motorvariante_geltung)")}
check("K6 additive Spaltenmigration: neue Spalten da, bestehende Zeile unverändert, zweiter Lauf harmlos",
      {"antriebsgas", "verbrauch_einheit", "verbrauch_zyklus", "kraftstoff_hinweis"} <= cols
      and c2.execute("select variante_id, bauzeit_von, quelle, antriebsgas from motorvariante_geltung").fetchall() == [("x", 2015, "q", None)])
check("K7 die Integritätsprüfung (inkl. canonical-only-Regel) bleibt sauber", dm.pruefe_integritaet(c) == [])
c.execute("delete from rueckruf where kba_referenz like 'AF10-%'")

import glob as _glob
code = "".join(open(f, encoding="utf-8").read() for f in _glob.glob("app/**/*.py", recursive=True)
               if not f.replace("\\", "/").endswith(("motor_geltung_daten.py", "data_migrations.py")))
check("L1 keine Fahrzeug-ID- oder Namenssonderregel im Anwendungscode",
      "golf-viii-1.5-tgi" not in code and "golf-vii-1.2" not in code and "tgi" not in code.lower().replace("antriebsgas", ""))

print()
if FEHLER:
    print(f"{len(FEHLER)} Pruefung(en) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle AF-10..12-Pruefungen bestanden.")
