"""
GAS-1 — Gasantrieb (CNG/LPG/unbekannt) im KaufCheck und AutoFinder-Folgefehler F1–F4 (offline).

    python scripts/test_isolated.py test_kaufcheck_gas.py

Echte Seed-Daten (Temp-DB des Isolationsrunners), echte Produzentenpfade (`run_kaufcheck`, Engine,
Router, Chat-Kontext, Migration). Provider sind gestubbt: jeder Gemini-/Tavily-Aufruf wird gezählt und
scheitert, außer der Gemini-Attrappe des KaufCheck, die nur Evidence-IDs liefert (der Bericht ist
kanonisch/deterministisch). NACHWEISGRENZE: keine echten Provideraufrufe, kein Live-Markt.

  A  Erkennung und Daten: Gasbegriffe, Variantenbezug, Vollständigkeit, Gasart je Variante
  B  AutoFinder F1–F4: G-TEC/316g, Web-Gaskennzeichnung, Budget-Prompt, Enrichment-Prompt, Literbezug
  C  Eingabe/API: Normalisierung, alte Requests
  D  KaufCheck Ende-zu-Ende: CNG, LPG, unbekannte Gasart, Nachrüstung, Widersprüche, Nicht-Gas
  E  Rückruf 2170 (Diesel/CNG/ungeklärter Benziner), 5108 Gasdruckfeder unverändert
  F  Chat-Kontext: Einheit, Zyklus, Bezug
  G  Markt: kein Benzin-Vergleich, Ohne-Marktdaten-Zustand, kein Provideraufruf
  H  Migration: frisch, bestehend, idempotent, Abbruch ohne Teilschreibung, Rückrufbestand unverändert
"""
import asyncio
import datetime as dt
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
import app.car_lookup as cl
import app.data_migrations as dm
import app.kaufcheck as kc
import app.llm as llm
import app.routers.autofinder as R
import app.web_search as ws
from app import gasantrieb as G
from app import motor_geltung_daten as gd
from app.autofinder_variante import pruefe_hardfilter_einhaltung
from app.config import DB_PATH
from app.database import get_conn
from app.main import app as _app
from app.models import KaufCheckRequest
from app.motor_applicability import varianten_applicability
from app.recall_filter import rueckruf_applicability
from app.vehicle_identity import VehicleIdentity

FEHLER: list[str] = []


def check(name, bedingung, detail=""):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f"  -> {str(detail)[:600]}" if not bedingung and detail else ""))
    if not bedingung:
        FEHLER.append(name)


PROVIDER = {"gemini": 0, "tavily": 0, "markt": 0}


async def _kein_gemini(*a, **k):
    PROVIDER["gemini"] += 1
    raise RuntimeError("Gemini verboten")


async def _kein_tavily(*a, **k):
    PROVIDER["tavily"] += 1
    raise RuntimeError("Tavily verboten")


async def _nur_ids(system, user):
    return {"risiko_evidence_ids": []}


cl.call_gemini_json = _kein_gemini
ws._tavily_search_intern = _kein_tavily
afw.tavily_search_mit_status = _kein_tavily
kc.call_gemini_json = _nur_ids
kc._heute = lambda: dt.date(2026, 11, 25)


def kaufcheck(**kw):
    req = KaufCheckRequest(**kw)
    return req, asyncio.run(kc.run_kaufcheck(req))


def aktionen(erg, bereich):
    plan = getattr(erg["kaufaktionen"], bereich)
    return plan.fahrzeugspezifisch + plan.basis


def gas_aktionen(erg):
    return {a.id: a for b in ("verkaeuferfragen", "dokumente", "besichtigung", "probefahrt")
            for a in aktionen(erg, b) if "gasanlage" in a.id}


_FRIST = re.compile(r"\b(alle\s+\d+\s+(jahre|monate)|jährlich|\d+\s*(monate|jahre)\b|fällig|überfällig|pflicht)", re.I)

# ══════════════════════════════════════════════════════════════════════════
print("=== A) Erkennung und Daten ===")
for txt, soll in (("Verschraubung der Gasdruckfeder der Heckklappe kann sich lösen.", None),
                  ("Gasgenerator des Fahrerairbags", None), ("Abgasanlage undicht", None), ("Gaspedal klemmt", None),
                  ("Xenon-Gasentladungslampe", None), ("Defekter O-Ring am Ventil einer CNG-Druckflasche", G.CNG),
                  ("Möglicher Erdgasaustritt aus dem CNG-Tank", G.CNG), ("LPG-Anlage", G.LPG),
                  ("Gasanlage", G.GAS_UNBEKANNT), ("CNG- und LPG-Fahrzeuge", G.GAS_UNBEKANNT)):
    check(f"A1 Gasbezug {txt!r} -> {soll}", G.gas_bauteil_scope(txt) == soll, G.gas_bauteil_scope(txt))

with get_conn() as c:
    c.row_factory = sqlite3.Row
    MOTOREN = {r["variante_id"]: dict(r) for r in c.execute(
        "select m.*, g.antriebsgas from motorvariante m left join motorvariante_geltung g using(variante_id)")}
    FAKTEN = {}
    for t in ("schwachstelle_motor", "kritische_wartung"):
        for r in c.execute(f"select * from {t}"):
            d = dict(r)
            FAKTEN.setdefault(d["variante_id"], []).append(" ".join(str(d.get(k) or "") for k in ("bauteil", "beschreibung", "hinweis")))
    BR_TEXT = {r[0]: " ".join(str(x or "") for x in r[1:]) for r in c.execute(
        "select id, erkennung_generation, facelift_merkmale, kaufberatung from baureihe")}

# Vollständigkeit: jede Variante, die IN IHREN EIGENEN Angaben (Bezeichnung, Emblem, Unterscheidung,
# variantengebundener Fakt) einen Gasantrieb nennt, ist gekennzeichnet — und keine andere.
erwartet = set()
for vid, m in MOTOREN.items():
    eigen = " ".join(str(m.get(k) or "") for k in ("bezeichnung", "heck_emblem", "optische_unterscheidung"))
    if G.gas_laut_bezeichnung(eigen) or any(G.gas_bauteil_scope(f) for f in FAKTEN.get(vid, [])):
        erwartet.add(vid)
gekennzeichnet = {vid for vid, m in MOTOREN.items() if m["antriebsgas"]}
check("A2 Vollständigkeit: genau die Varianten mit eigenem Gasbezug sind gekennzeichnet (21)",
      erwartet == gekennzeichnet and len(gekennzeichnet) == 21, sorted(erwartet ^ gekennzeichnet))
check("A3 die fünf GAS-1-Varianten sind als 'Gas' ohne Einheit/Zyklus gekennzeichnet",
      all(MOTOREN[v]["antriebsgas"] == "Gas" for v in gd.GELTUNG_GAS1))
# Variantenbezug: Gasbegriffe in Baureihentexten machen keine Nachbarvariante zum Gasfahrzeug.
zafira_c = [v for v in MOTOREN if v.startswith("opel-zafira-c-")]
check("A4 Variantenbezug: in der Zafira C ist nur die CNG-Variante gekennzeichnet, alle anderen nicht",
      [v for v in zafira_c if MOTOREN[v]["antriebsgas"]] == ["opel-zafira-c-1.6-cng-turbo-150ps"] and len(zafira_c) > 3)
br_mit_gas = [b for b, t in BR_TEXT.items() if G.gas_bauteil_scope(t) or G.gas_laut_bezeichnung(t)]
check("A5 Baureihentexte mit Gasbegriff kennzeichnen keine Variante ohne eigenen Bezug",
      all(not (MOTOREN[v]["antriebsgas"] and v not in erwartet) for v in MOTOREN), br_mit_gas[:5])

with get_conn() as c:
    c.row_factory = sqlite3.Row
    from app.database import get_baureihe
    def motor(vid):
        bid = MOTOREN[vid]["baureihe_id"]
        r = c.execute("select marke, modell, generation from baureihe where id=?", (bid,)).fetchone()
        return next(m for m in get_baureihe(r[0], r[1], r[2])["motoren"] if m["variante_id"] == vid)
    M_TGI, M_ZC_CNG, M_ASTRA_LPG = motor("volkswagen-golf-viii-1.5-tgi"), motor("opel-zafira-c-1.6-cng-turbo-150ps"), motor("opel-astra-k-1.4-turbo-lpg-140-ps")
    M_POLO, M_KAMIQ, M_316G = motor("volkswagen-polo-vi-1.0-tgi-90-ps"), motor("skoda-kamiq-erste-generation-1.0-g-tec-90-ps"), motor("bmw-3er-e36-316g")
    M_ZC_14 = motor("opel-zafira-c-1.4-turbo-140ps")
check("A6 Gasart mit Herkunft: TGI belegt CNG; Zafira 'CNG' und Astra 'LPG' laut Bezeichnung; Polo TGI/Kamiq/316g unbekannt; 1.4 Turbo keine",
      (G.gasart_der_variante(M_TGI)[0], G.gasart_der_variante(M_ZC_CNG), G.gasart_der_variante(M_ASTRA_LPG)[0],
       G.gasart_der_variante(M_POLO)[0], G.gasart_der_variante(M_KAMIQ)[0], G.gasart_der_variante(M_316G)[0],
       G.gasart_der_variante(M_ZC_14)) ==
      ("CNG", ("CNG", "laut Variantenbezeichnung im ENFAL-Datensatz, Gasart nicht unabhängig geprüft"), "LPG",
       G.GAS_UNBEKANNT, G.GAS_UNBEKANNT, G.GAS_UNBEKANNT, (None, None)))

# ══════════════════════════════════════════════════════════════════════════
print("\n=== B) AutoFinder F1–F4 ===")
with get_conn() as c:
    BASIS = af._annotiere_basis(af._lade_rohkandidaten(c))
ROH = {r["variante_id"]: r for r in BASIS}
req_b = af.AutoFinderRequest(kraftstoff=["Benzin"], sparsam=True)
for vid in gd.GELTUNG_GAS1:
    r = ROH[vid]
    k = af._zu_kandidat(r, *af._score_kandidat(r, req_b), af._datenqualitaet(r), req_b)
    check(f"B1 {vid}: Gasfahrzeug, kein Benzin-Treffer (Filter + Endvalidierung), keine Liter-Bewertung",
          r["antriebsgas"] == "Gas" and r["_verbrauch_einheit"] == "unbekannt" and r["_verbrauch"] is None
          and not af.erfuellt_harte_filter(r, req_b) and pruefe_hardfilter_einhaltung(k, req_b)
          and not any("erbrauch" in g for g in af._score_kandidat(r, req_b)[1]))
check("B2 Kamiq G-TEC: der Wert 3,5 bleibt als Wert erhalten, gilt aber nicht als Liter (keine 'Niedriger Verbrauch'-Bewertung)",
      ROH["skoda-kamiq-erste-generation-1.0-g-tec-90-ps"]["_verbrauch_anzeige"] == 3.5)
k_kamiq = af._zu_kandidat(ROH["skoda-kamiq-erste-generation-1.0-g-tec-90-ps"], 0.0, [], 1.0, af.AutoFinderRequest())
karte = R._zu_kandidat_out(k_kamiq, R._konkrete_variante(k_kamiq, R.AutoFinderRequest()),
                           enrichment=afe.deterministischer_fallback(k_kamiq), enrichment_status="fallback")
check("B3 Karte Kamiq: antriebsgas 'Gas', Einheit unbekannt (Frontend sperrt damit keine Übergabe mehr ab, sondern übergibt Gas)",
      (karte.antriebsgas, karte.verbrauch_einheit, karte.kraftstoff) == ("Gas", "unbekannt", "Benzin"))

web_roh = {"marke": "Seat", "modell": "Ibiza", "generation": None, "motor": "1.0 TGI", "baujahr_von": 2018,
           "baujahr_bis": None, "kraftstoff": "Benzin", "leistung_ps": 90, "getriebe": "manuell",
           "karosserie": "kleinwagen", "antrieb": "Front", "evidence": [1, 2]}
evid = [{"title": "Seat Ibiza 1.0 TGI 90 PS", "content": "Seat Ibiza 1.0 TGI mit 90 PS, Erdgas (CNG) und Benzin.",
         "url": "https://www.example.org/a", "score": 0.9},
        {"title": "Seat Ibiza TGI", "content": "Der Seat Ibiza 1.0 TGI (90 PS) fährt mit Erdgas, Benzin als Reserve.",
         "url": "https://www.example.com/b", "score": 0.9}]
wk, grund = afw._pruefe_kandidat(web_roh, evid)          # derselbe Validierungspfad wie im Produktivlauf
check("B4 Web-Kandidat '1.0 TGI' (Quelle belegt 'Benzin'): als Gasfahrzeug gekennzeichnet und kein Treffer einer "
      "Benzinauswahl (Filter + Endvalidierung)",
      wk is not None and wk.antriebsgas == "Gas" and wk.verbrauch_einheit == "unbekannt"
      and not af.erfuellt_harte_filter(afw._als_engine_roh(wk), af.AutoFinderRequest(kraftstoff=["Benzin"]))
      and bool(pruefe_hardfilter_einhaltung(wk, af.AutoFinderRequest(kraftstoff=["Benzin"]))), grund)
wk2 = afw.WebKandidat(candidate_id="web:x", marke="Seat", modell="Ibiza", generation=None, motor_bezeichnung="1.0 TSI",
                      baujahr_von=2018, baujahr_bis=None, leistung_ps=95, kraftstoff="Benzin", getriebe_klassen=[],
                      antrieb=None, karosserie_klassen=[])
check("B5 Web-Kandidat '1.0 TSI' bleibt ein gewöhnlicher Benziner",
      wk2.antriebsgas is None and af.erfuellt_harte_filter(afw._als_engine_roh(wk2), af.AutoFinderRequest(kraftstoff=["Benzin"])))

k_tgi = af._zu_kandidat(ROH["volkswagen-golf-viii-1.5-tgi"], 0.0, [], 1.0, af.AutoFinderRequest())
zeile = afb._formatiere_kandidat_zeile(k_tgi)
check("B6 F3 Budget-Prompt: Golf TGI mit Kraftstoff-Hinweis statt 'Benzin'",
      "primär Erdgas (CNG), Benzin nur als Reserve" in zeile and "| Benzin |" not in zeile, zeile)
k_tsi = af._zu_kandidat(ROH["volkswagen-golf-viii-1.5-tsi-110-kw"], 0.0, [], 1.0, af.AutoFinderRequest())
check("B7 F3 Budget-Prompt: gewöhnlicher Benziner unverändert 'Benzin'", "| Benzin |" in afb._formatiere_kandidat_zeile(k_tsi))
check("B8 F4 Enrichment-Prompt: kein pauschales 'Motorzeitraum NICHT hinterlegt' mehr; belegt nur, wo der Kandidat ihn nennt",
      "Der Motorzeitraum ist NICHT hinterlegt" not in afe._SYSTEM_PROMPT
      and "Ein Motorzeitraum ist NUR dort belegt, wo die Angaben des Kandidaten ihn ausdrücklich nennen" in afe._SYSTEM_PROMPT)

# Literwert eines Gasfahrzeugs: nur mit belegtem Bezug, und nur so benannt.
def gaskand(**kw):
    basis = dict(antriebsgas="Gas", verbrauch_einheit="l", verbrauch_wert=6.0, verbrauch_l_100km=None,
                 verbrauch_basis="wltp", verbrauch_zyklus=None, verbrauch_bezug=None, motor_bezeichnung="1.4 TGI")
    basis.update(kw)
    return SimpleNamespace(**basis)
S = afe.bereinige_unbelegte_aussagen
k_benzin = gaskand(verbrauch_bezug="Benzin")
check("B9 belegter Literwert im ausdrücklich benannten Benzinbetrieb bleibt",
      S(["Im Benzinbetrieb liegt der Verbrauch bei 6,0 l/100 km."], k_benzin) == ["Im Benzinbetrieb liegt der Verbrauch bei 6,0 l/100 km."])
check("B10 derselbe Literwert ohne Benzinbezug im Satz (als Gasverbrauch lesbar) entfällt",
      S(["Der Verbrauch liegt bei 6,0 l/100 km."], k_benzin) == [])
check("B11 Literwert ohne belegten Bezug entfällt, auch mit 'Benzin' im Satz",
      S(["Im Benzinbetrieb liegt der Verbrauch bei 6,0 l/100 km."], gaskand()) == [])
k_lpg = gaskand(verbrauch_bezug="LPG", verbrauch_wert=9.0)
check("B12 LPG-Literwert nur als Autogas, nie als Benzin; CNG nicht mit LPG verwechselt",
      S(["Mit Autogas liegt der Verbrauch bei 9,0 l/100 km."], k_lpg) and not S(["Im Benzinbetrieb 9,0 l/100 km."], k_lpg)
      and not G.literwert_belegt("Erdgas 9,0 l/100 km", gas=True, einheit="l", bezug="CNG"))
check("B13 Kontextzeile: Literwert ohne Bezug wird nicht als Verbrauch angeboten; mit Benzinbezug ausdrücklich so benannt",
      "keine Verbrauchszahl nennen" in afe._verbrauch_zeile(gaskand()) and "NUR im Benzinbetrieb" in afe._verbrauch_zeile(k_benzin))

# ══════════════════════════════════════════════════════════════════════════
print("\n=== C) Eingabe/API ===")
for kw, soll in (({"kraftstoff": "CNG"}, ("CNG", None)), ({"kraftstoff": "Erdgas"}, ("CNG", None)),
                 ({"kraftstoff": "Autogas"}, ("LPG", None)), ({"kraftstoff": "lpg"}, ("LPG", None)),
                 ({"kraftstoff": "gas"}, ("GAS_UNBEKANNT", None)), ({"antriebsgas": "GAS_UNBEKANNT"}, ("GAS_UNBEKANNT", None)),
                 ({"antriebsgas": "cng", "kraftstoff": "LPG"}, ("GAS_UNBEKANNT", None)),
                 ({"antriebsgas": "LPG", "kraftstoff": "benzin"}, ("LPG", "benzin")),
                 ({"antriebsgas": "Wasserstoff"}, (None, None)), ({"kraftstoff": "benzin"}, (None, "benzin")),
                 ({"kraftstoff": "diesel"}, (None, "diesel"))):
    r = KaufCheckRequest(marke="VW", modell="Golf", **kw)
    check(f"C1 Normalisierung {kw} -> {soll}", (r.antriebsgas, r.kraftstoff) == soll, (r.antriebsgas, r.kraftstoff))
alt = {"marke": "BMW", "modell": "320d", "baujahr": 2019, "kraftstoff": "diesel", "scheckheftgepflegt": True}
check("C2 alter Request ohne Gasfeld bleibt gültig und unverändert", KaufCheckRequest(**alt).antriebsgas is None
      and KaufCheckRequest(**alt).model_dump(exclude={"antriebsgas"}) == KaufCheckRequest.model_validate(alt).model_dump(exclude={"antriebsgas"}))

# ══════════════════════════════════════════════════════════════════════════
print("\n=== D) KaufCheck Ende-zu-Ende ===")
P0 = dict(PROVIDER)
FALL = {}
FALL["cng"] = kaufcheck(marke="Volkswagen", modell="Golf", baujahr=2021, motor="1.5 TGI")
FALL["lpg"] = kaufcheck(marke="Opel", modell="Astra", baujahr=2018, motor="1.4 Turbo LPG", kraftstoff="LPG", leistung_ps=140)
FALL["unbekannt"] = kaufcheck(marke="Volkswagen", modell="Polo", baujahr=2019, motor="1.0 TGI")
FALL["nachruestung"] = kaufcheck(marke="Opel", modell="Zafira", baujahr=2014, motor="1.4 Turbo", leistung_ps=140, kraftstoff="Autogas")
FALL["widerspruch_gasart"] = kaufcheck(marke="Opel", modell="Zafira", baujahr=2014, motor="1.6 CNG Turbo", antriebsgas="LPG")
FALL["diesel_gas"] = kaufcheck(marke="Opel", modell="Zafira", baujahr=2014, motor="2.0 CDTI", leistung_ps=170, kraftstoff="CNG")
FALL["benzin_bei_gas"] = kaufcheck(marke="Volkswagen", modell="Golf", baujahr=2021, motor="1.5 TGI", kraftstoff="benzin")
FALL["zafira_cng"] = kaufcheck(marke="Opel", modell="Zafira", baujahr=2014, motor="1.6 CNG Turbo")
FALL["zafira_diesel"] = kaufcheck(marke="Opel", modell="Zafira", baujahr=2014, motor="2.0 CDTI", leistung_ps=170)
FALL["zafira_benzin"] = kaufcheck(marke="Opel", modell="Zafira", baujahr=2014, motor="1.4 Turbo", leistung_ps=140)


def ident(f):
    return FALL[f][1]["vehicle_identity"]


def bericht(f):
    return FALL[f][1]["bericht"]


def gas_status(f):
    return (ident(f).get("field_evidence", {}).get("gas") or {}).get("gas_status")


erw = {"cng": ("CNG", "datensatz"), "lpg": ("LPG", "bestaetigt"), "unbekannt": ("GAS_UNBEKANNT", "datensatz"),
       "nachruestung": ("LPG", "nachruestung_moeglich"), "widerspruch_gasart": ("GAS_UNBEKANNT", "widerspruch_gasart"),
       "diesel_gas": ("CNG", "sonderkonfiguration"), "benzin_bei_gas": ("CNG", "angabe_benzin"),
       "zafira_diesel": (None, None), "zafira_benzin": (None, None)}
for f, soll in erw.items():
    check(f"D1 {f}: Gasart/Status in der Identität {soll}", (ident(f).get("gas"), gas_status(f)) == soll,
          (FALL[f][1]["motor_erkannt"], ident(f).get("gas"), gas_status(f)))
check("D2 Gasart wird nie geraten: unbekannte Gasart und Widerspruch bleiben GAS_UNBEKANNT",
      ident("unbekannt")["gas"] == ident("widerspruch_gasart")["gas"] == "GAS_UNBEKANNT")
check("D3 Angabe bleibt Angabe: LPG laut Angabe bei Nicht-Gas-Variante ist 'user_only', keine Bestätigung durch ENFAL",
      ident("nachruestung")["field_evidence"]["gas"]["verification_state"] == "user_only"
      and ident("nachruestung")["field_evidence"]["gas"]["confirmed_by"] == [])
check("D4 Bericht CNG: Gaszeile mit Herkunft und Hinweis zur Motorklasse",
      "- Gasantrieb: Erdgas (CNG). Herkunft: ENFAL-Fahrzeugdaten mit hinterlegter Quelle." in bericht("cng")
      and "kein gewöhnlicher Benziner" in bericht("cng"), bericht("cng")[:1500])
check("D5 Bericht Nachrüstung: Werksausführung ausdrücklich nicht belegt, Nachrüstung möglich, kein Widerspruch",
      "eine Werksausführung ist nicht belegt, eine nachgerüstete Gasanlage ist möglich" in bericht("nachruestung")
      and not any("Gasantrieb" in k.titel for k in FALL["nachruestung"][1]["key_findings"]))
for f in ("widerspruch_gasart", "diesel_gas", "benzin_bei_gas"):
    kfs = [k for k in FALL[f][1]["key_findings"] if k.titel == "Angaben zum Gasantrieb passen nicht zusammen"]
    check(f"D6 {f}: Widerspruch als Key Finding, Vergleichstabelle markiert, keine Technikdiagnose",
          len(kfs) == 1 and "| Gasantrieb |" in bericht(f) and "Werkstatt" not in kfs[0].beschreibung,
          [k.titel for k in FALL[f][1]["key_findings"]])
check("D7 Diesel + CNG: als ungeklärte Sonderkonfiguration oder Eingabefehler benannt",
      "Ungeklärte Sonderkonfiguration oder Eingabefehler" in bericht("diesel_gas"))
for f in ("cng", "lpg", "unbekannt", "nachruestung", "widerspruch_gasart", "diesel_gas", "benzin_bei_gas", "zafira_cng"):
    ga = gas_aktionen(FALL[f][1])
    texte = " ".join(a.titel + " " + a.aktion for a in ga.values())
    check(f"D8 {f}: Gasfrage (Verkäufer) und Gasunterlagen (Dokumente) im Bericht, ohne Frist/Intervall/Prüfpflicht-Behauptung",
          {"frage-basis-gasanlage", "dokument-basis-gasanlage_nachweise"} <= set(ga)
          and "Gasanlage" in bericht(f) and "Unterlagen zur Gasanlage zeigen lassen" in bericht(f)
          and not _FRIST.search(texte.replace("keine Fristen oder Prüfpflichten", "")), texte)
check("D9 Fragen tragen die Gasart: CNG nennt Erdgas, LPG nennt Autogas, unbekannt fragt nach der Gasart, ungeklärt fragt ob verbaut",
      "Erdgas (CNG)-Anlage" in gas_aktionen(FALL["cng"][1])["frage-basis-gasanlage"].titel
      and "Autogas (LPG)-Anlage" in gas_aktionen(FALL["lpg"][1])["frage-basis-gasanlage"].titel
      and gas_aktionen(FALL["unbekannt"][1])["frage-basis-gasanlage"].titel.startswith("Welche Gasart")
      and gas_aktionen(FALL["diesel_gas"][1])["frage-basis-gasanlage"].titel.startswith("Ist eine Gasanlage verbaut"))
for f in ("zafira_diesel", "zafira_benzin"):
    check(f"D10 {f} (kein Gas): keine Gaszeile, keine Gasfrage/-unterlage, kein Gas-Widerspruch",
          "Gasantrieb" not in bericht(f) and not gas_aktionen(FALL[f][1])
          and not any("Gasantrieb" in k.titel for k in FALL[f][1]["key_findings"]))

# E2: ungeprüfte DB-Intervalle bleiben über den bestehenden Wartungsschutz erhalten (Wortlaut + Prüfstatus).
lpg_texte = bericht("lpg") + " ".join(a.titel + " " + a.aktion for b in ("verkaeuferfragen", "dokumente")
                                      for a in aktionen(FALL["lpg"][1], b))
cng_texte = bericht("zafira_cng") + " ".join(a.titel + " " + a.aktion for b in ("verkaeuferfragen", "dokumente")
                                            for a in aktionen(FALL["zafira_cng"][1], b))
check("D11 E2 LPG-Anlage 'Jährlich' bleibt mit Originalwortlaut als hinterlegter, ungeprüfter Hinweis erhalten",
      "Hinterlegter Wartungshinweis: Jährlich" in lpg_texte and "ungeprüfter Hinweis" in lpg_texte)
check("D12 E2 CNG-Tanks 'Alle 4 Jahre' bleibt hinterlegt/ungeprüft, ohne abgeleitete Fälligkeit oder Herstellervorgabe",
      "hinterlegt, ungeprüft: Alle 4 Jahre" in cng_texte and not re.search(r"überfällig|ist fällig|wird fällig|Herstellervorgabe(?! )", cng_texte))
check("D13 Identität/Kontext an das Modell trägt die Gasart (kanonischer Kontext)",
      ident("cng").get("gas") == "CNG" and "gas_bewertet" not in ident("cng"))

# ══════════════════════════════════════════════════════════════════════════
print("\n=== E) Rückrufe ===")
r_cng = [i for i in FALL["zafira_cng"][1]["insights"] if i.kategorie == "rueckruf" and "CNG-Druckflasche" in i.beschreibung]
r_dsl = [i for i in FALL["zafira_diesel"][1]["insights"] if i.kategorie == "rueckruf" and "CNG-Druckflasche" in i.beschreibung]
r_bzn = [i for i in FALL["zafira_benzin"][1]["insights"] if i.kategorie == "rueckruf" and "CNG-Druckflasche" in i.beschreibung]
r_wid = [i for i in FALL["widerspruch_gasart"][1]["insights"] if i.kategorie == "rueckruf" and "CNG-Druckflasche" in i.beschreibung]
r_nach = [i for i in FALL["nachruestung"][1]["insights"] if i.kategorie == "rueckruf" and "CNG-Druckflasche" in i.beschreibung]
sc = lambda lst: (lst[0].recall_status or {}).get("scope") if lst else None
check("E1 2170 CNG-Variante (Gasart aus Datensatz, Baujahr gedeckt, KBA 6340 belegt): variant_match, Betroffenheit/Durchführung offen",
      sc(r_cng) == "variant_match" and r_cng[0].recall_status.get("vehicle_affected") == "unknown"
      and r_cng[0].recall_status.get("completion") == "unknown", r_cng and r_cng[0].recall_status)
check("E2 2170 Diesel ohne Gasangabe: entfällt (incompatible)", r_dsl == [])
check("E3 2170 ungeklärter Benziner (1.4 Turbo, Nachrüstung nicht ausgeschlossen): unclear", sc(r_bzn) == "unclear")
check("E4 2170 bei widersprüchlicher Gasart: unclear (keine engere Zuordnung)", sc(r_wid) == "unclear")
check("E5 2170 bei LPG laut Angabe (andere Gasart, nur Angabe): entfällt", r_nach == [])
ident_dsl_cng = VehicleIdentity(make="Opel", fuel="diesel")
G.setze_in_identitaet(ident_dsl_cng, G.gas_identitaet(SimpleNamespace(antriebsgas="CNG", kraftstoff=None),
                                                      {"kraftstoff": "Diesel", "bezeichnung": "2.0 CDTI"}))
r2170 = {"mangel": "Defekter O-Ring am Ventil einer CNG-Druckflasche kann zum Austritt von Kraftstoff (Gas) führen.",
         "abhilfe": "Austausch des Füllventils", "betroffene_baujahre": "2011-2016", "baureihe_id": "opel-zafira-c"}
check("E6 passende Gasart allein (nur Angabe) begründet kein variant_match",
      rueckruf_applicability(r2170, True, "6340", None, marke="Opel", identity=ident_dsl_cng)[0] != "variant_match")
r5108 = {"mangel": "Verschraubung der Gasdruckfeder der Heckklappe kann sich lösen.", "abhilfe": "Verschraubung prüfen",
         "betroffene_baujahre": "2015", "baureihe_id": "volkswagen-caddy-iii"}
for gas_req, mm in ((SimpleNamespace(antriebsgas=None, kraftstoff=None), {"kraftstoff": "Benzin", "bezeichnung": "1.4 TGI (110 PS)", "antriebsgas": "Gas"}),
                    (SimpleNamespace(antriebsgas=None, kraftstoff=None), {"kraftstoff": "Diesel", "bezeichnung": "2.0 TDI"})):
    i_ohne = VehicleIdentity(make="Volkswagen", fuel=G.normalisiere_angabe(None) or ("diesel" if mm["kraftstoff"] == "Diesel" else "benzin"))
    i_mit = VehicleIdentity(make="Volkswagen", fuel=i_ohne.fuel)
    G.setze_in_identitaet(i_mit, G.gas_identitaet(gas_req, mm))
    check(f"E7 5108 Gasdruckfeder ({mm['bezeichnung']}): Bewertung mit und ohne Gasachse identisch",
          rueckruf_applicability(r5108, True, "x", mm, identity=i_mit) == rueckruf_applicability(r5108, True, "x", mm, identity=i_ohne))
fakt = {"bauteil": "CNG-Tanks", "beschreibung": "Sichtprüfung der Gastanks"}
i_vk = VehicleIdentity(make="Opel", fuel="diesel")      # VerkaufCheck/Chat: Gasachse nicht bewertet
check("E8 Gasachse nur im KaufCheck: ohne Bewertung (VerkaufCheck/Chat) bleibt die Faktprüfung unverändert",
      varianten_applicability(fakt, i_vk)[0] != "incompatible"
      and varianten_applicability(fakt, (lambda i: (G.setze_in_identitaet(i, None), i)[1])(VehicleIdentity(make="Opel", fuel="diesel")))[0] == "incompatible")

# ══════════════════════════════════════════════════════════════════════════
print("\n=== F) Chat-Kontext ===")
ctx = llm._sql_context(["volkswagen-golf-viii", "skoda-kamiq-erste-generation", "opel-zafira-c"])
tgi_block = ctx[ctx.index("(volkswagen-golf-viii-1.5-tgi)"):][:900]
kamiq_block = ctx[ctx.index("(skoda-kamiq-erste-generation-1.0-g-tec-90-ps)"):][:900]
tsi_block = ctx[ctx.index("(volkswagen-golf-viii-1.5-tsi-110-kw)"):][:900]
check("F1 Golf TGI: Gasfahrzeug mit Herkunft, 4,1 kg/100km im Gasbetrieb, WLTP belegt, kein Literwert",
      "Gasfahrzeug: Erdgas (CNG)" in tgi_block and "4,1 kg/100km (kg, NICHT Liter)" in tgi_block
      and "(WLTP)" in tgi_block and "l/100km" not in tgi_block.split("CO2")[0], tgi_block)
check("F2 Kamiq G-TEC: Werte ohne belegte Einheit, nicht als Liter",
      "OHNE belegte Einheit" in kamiq_block and "l/100km" not in kamiq_block.split("CO2")[0], kamiq_block)
check("F3 gewöhnlicher Benziner: Liter bleibt, 'WLTP' nur bei belegtem Zyklus (hier: Zyklus nicht geprüft)",
      "l/100km" in tsi_block and "Zyklus nicht geprüft" in tsi_block and "Verbrauch WLTP" not in tsi_block, tsi_block)
check("F4 Zafira C: CNG-Variante als Gasfahrzeug, Dieselvarianten ohne Gaskennzeichnung",
      "Gasfahrzeug: Erdgas (CNG), laut Variantenbezeichnung" in ctx[ctx.index("(opel-zafira-c-1.6-cng-turbo-150ps)"):][:600]
      and "Gasfahrzeug" not in ctx[ctx.index("(opel-zafira-c-2.0-cdti-170ps)"):][:400])
gas_m = {"verbrauch_wltp": 6.0, "verbrauch_real": None, "antriebsgas": "Gas", "bezeichnung": "1.4 TGI",
         "verbrauch_einheit": "l", "verbrauch_bezug": "Benzin", "verbrauch_zyklus": None}
check("F5 Kontext: belegter Literwert im Benzinbetrieb bleibt ausdrücklich so benannt; ohne Bezug nicht als Verbrauch",
      "Verbrauch im Benzinbetrieb laut Verbrauchsspalte (Zyklus nicht geprüft): 6,0 l/100km" in G.verbrauch_kontextzeilen(gas_m)[0]
      and "OHNE belegten Bezug" in G.verbrauch_kontextzeilen({**gas_m, "verbrauch_bezug": None})[0])

# ══════════════════════════════════════════════════════════════════════════
print("\n=== G) Markt ===")
check("G1 Gasfahrzeuge: Ohne-Marktdaten-Zustand (completed_no_market), keine Preisspanne, verständliche Begründung",
      all(FALL[f][1]["research_status"] == "completed_no_market" and FALL[f][1]["marktpreis_min"] is None
          and G.MARKT_HINWEIS in bericht(f) for f in ("cng", "lpg", "unbekannt", "nachruestung")))
check("G2 Nicht-Gas: bisheriger Ohne-Markt-Satz ohne Gasbegründung",
      "Keine belastbare Marktpreisbewertung verfügbar." in bericht("zafira_diesel") and G.MARKT_HINWEIS not in bericht("zafira_diesel"))
check("G3 kein Gemini-/Tavily-Aufruf in allen KaufCheck-Fällen", PROVIDER == P0, PROVIDER)
# Freigegebene Marktquelle simuliert: eine Gasangabe startet keine Marktrecherche, eine Gasvariante verwirft sie.
MARKT = {"n": 0}


async def _markt(*a, **k):
    MARKT["n"] += 1
    return []
_alt_moeglich, _alt_tav = kc.marktpreis_recherche_moeglich, kc.tavily_search_with_fallback
kc.marktpreis_recherche_moeglich = lambda *a, **k: True
kc.tavily_search_with_fallback = _markt
_alt_vertiefe = kc.vertiefe_marktrecherche


async def _vertiefe(*a, **k):
    MARKT["n"] += 100
    raise AssertionError("Vertiefung bei Gas verboten")
kc.vertiefe_marktrecherche = _vertiefe
try:
    _, e_gas = kaufcheck(marke="Opel", modell="Astra", baujahr=2018, motor="1.4 Turbo LPG", kraftstoff="LPG", leistung_ps=140)
    n_angabe = MARKT["n"]
    _, e_ds = kaufcheck(marke="Volkswagen", modell="Golf", baujahr=2021, motor="1.5 TGI")
finally:
    kc.marktpreis_recherche_moeglich, kc.tavily_search_with_fallback = _alt_moeglich, _alt_tav
    kc.vertiefe_marktrecherche = _alt_vertiefe
check("G4 Gasangabe: keine Marktrecherche trotz freigegebener Quelle; Gasvariante ohne Angabe: keine Vertiefung, kein Benzin-Vergleich",
      n_angabe == 0 and MARKT["n"] < 100 and e_gas["research_status"] == e_ds["research_status"] == "completed_no_market"
      and e_ds["marktpreis_min"] is None, MARKT)

# ══════════════════════════════════════════════════════════════════════════
print("\n=== H) Migration ===")
c = sqlite3.connect(str(DB_PATH), isolation_level=None)
c.execute("pragma foreign_keys=ON")


def hash_t(t):
    rows = c.execute(f'select * from "{t}" order by 1').fetchall()
    return len(rows), hashlib.sha256(repr(rows).encode()).hexdigest()


MG = dm.MARKER_MOTOR_GELTUNG_GAS1
check("H1 frische DB (Seed + Bootstrap): Marker gesetzt, 27 Geltungszeilen, alle fünf GAS-1-Zeilen da",
      c.execute("select count(*) from schema_migrations where name=?", (MG,)).fetchone()[0] == 1
      and c.execute("select count(*) from motorvariante_geltung").fetchone()[0] == 27
      and c.execute("select count(*) from motorvariante_geltung where variante_id in (%s)" % ",".join("?" * 5),
                    list(gd.GELTUNG_GAS1)).fetchone()[0] == 5)
for i in range(3):
    c.execute("insert into rueckruf (baureihe_id, mangel, kba_referenz, canonical_make, canonical_nameplate) "
              "values (NULL, 'Testmangel', ?, 'MAZDA', 'CX-5')", (f"GAS1-{i}",))
rr0, gel0 = hash_t("rueckruf"), hash_t("motorvariante_geltung")
# Bestehende DB ohne GAS-1 (Stand nach AF-10): Marker und Zeilen entfernen, Migration erneut.
c.execute("delete from schema_migrations where name=?", (MG,))
c.execute("delete from motorvariante_geltung where variante_id in (%s)" % ",".join("?" * 5), list(gd.GELTUNG_GAS1))
check("H2 bestehende DB: Migration trägt die fünf Zeilen nach (Marker gesetzt, Inhalt wie frisch)",
      dm.fuehre_migration_aus(c, MG, dm.SCHRITTE_MOTOR_GELTUNG_GAS1) is True and hash_t("motorvariante_geltung") == gel0)
check("H3 Rückrufbestand (inkl. canonical-only) unverändert", hash_t("rueckruf") == rr0)
check("H4 zweiter Lauf ist ein No-op", dm.fuehre_migration_aus(c, MG, dm.SCHRITTE_MOTOR_GELTUNG_GAS1) is False
      and hash_t("motorvariante_geltung") == gel0)
c.execute("delete from schema_migrations where name=?", (MG,))
c.execute("delete from motorvariante_geltung where variante_id in (%s)" % ",".join("?" * 4), list(gd.GELTUNG_GAS1)[:4])
c.execute("update motorvariante_geltung set notiz='abweichend' where variante_id=?", (list(gd.GELTUNG_GAS1)[4],))
vorher = hash_t("motorvariante_geltung")
ok = dm.fuehre_migration_aus(c, MG, dm.SCHRITTE_MOTOR_GELTUNG_GAS1)
check("H5 abweichende vorhandene Zeile: Abbruch, kein Marker, KEINE Teilschreibung der übrigen vier",
      ok is False and hash_t("motorvariante_geltung") == vorher
      and c.execute("select count(*) from schema_migrations where name=?", (MG,)).fetchone()[0] == 0)
c.execute("update motorvariante_geltung set notiz=? where variante_id=?",
          (gd.GELTUNG_GAS1[list(gd.GELTUNG_GAS1)[4]]["notiz"], list(gd.GELTUNG_GAS1)[4]))
check("H6 nach Bereinigung läuft sie vollständig; Integritätsprüfung sauber",
      dm.fuehre_migration_aus(c, MG, dm.SCHRITTE_MOTOR_GELTUNG_GAS1) is True and hash_t("motorvariante_geltung") == gel0
      and dm.pruefe_integritaet(c) == [])
c2 = sqlite3.connect(":memory:")
c2.execute("create table motorvariante_geltung (variante_id text primary key, bauzeit_von integer, bauzeit_bis integer, "
           "getriebe_unbestaetigt text, quelle text not null, notiz text, antriebsgas text, verbrauch_einheit text, "
           "verbrauch_zyklus text, kraftstoff_hinweis text)")
c2.execute("insert into motorvariante_geltung (variante_id, quelle, antriebsgas) values ('x', 'q', 'CNG')")
import app.database as database
database._migriere_geltung_spalten(c2); database._migriere_geltung_spalten(c2)
check("H7 additive Spalte verbrauch_bezug auf Produktionsstand nach AF-10: angelegt, Zeile unverändert, zweiter Lauf harmlos",
      "verbrauch_bezug" in {r[1] for r in c2.execute("pragma table_info(motorvariante_geltung)")}
      and c2.execute("select variante_id, quelle, antriebsgas, verbrauch_bezug from motorvariante_geltung").fetchall() == [("x", "q", "CNG", None)])
c.execute("delete from rueckruf where kba_referenz like 'GAS1-%'")

print()
if FEHLER:
    print(f"{len(FEHLER)} Pruefung(en) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle GAS-1-Pruefungen bestanden.")
