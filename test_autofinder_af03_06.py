"""
AF-03 bis AF-06 -- Gegenproben (offline).

    python scripts/test_isolated.py test_autofinder_af03_06.py

AF-05  Bekannte Punkte: Motor-Gate und Zeitgeltung aus dem KaufCheck, Prüfstatus bis zur Karte.
AF-04  Größenklasse (Segment) für Kleinwagen vs. Kompakt, andere Karosserien unberührt.
AF-03  Generations- statt Motorjahre im Modellkontext; Suchfenster ist kein Fahrzeugmerkmal.
AF-06  Modelltext: unbelegte Serienausstattung/Getriebebauart/Wetter-Absolutaussagen und falsch
       bezeichneter Verbrauch entfallen, zulässige Texte und die KI-Preisspanne bleiben.

Echte Seed-Daten (temporäre DB des Isolationsrunners), echte Engine, echter Fit. Nur die Provider-
antworten sind gestubbt, Tavily ist verboten. NACHWEISGRENZE: Kontingent ist ein No-op, die Gemini-
Antworten sind konstruierte Annahmen, keine echten Modellwerte.
"""
import asyncio
import re
import sys
from types import SimpleNamespace

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi.testclient import TestClient

import app.autofinder as af
import app.autofinder_budget as afb
import app.autofinder_enrich as afe
import app.autofinder_web as afw
import app.routers.autofinder as R
from app.autofinder_norm import groessenklasse_anwenden, SEG_KLEINWAGEN, SEG_KOMPAKTKLASSE
from app.autofinder_variante import baujahr_kontext
from app.database import get_conn
from app.gemini_retry import GeminiFehlgeschlagen
from app.main import app as _app

FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f"  -> {detail}" if not bedingung and detail else ""))
    if not bedingung:
        FEHLER.append(name)


R.require_autofinder_kontingent = lambda request: None   # Nachweisgrenze, siehe Modulkopf
CALLS = {"budget": 0, "enrich": 0, "tavily": 0}
MODE = {"enrich": "ok"}


async def _tavily_verboten(*a, **k):
    CALLS["tavily"] += 1
    raise AssertionError("Tavily verboten")


afw.tavily_search_mit_status = _tavily_verboten


async def _budget(sp, um):
    CALLS["budget"] += 1
    ids = re.findall(r"candidate_id=(\S+)", um)
    return {"candidates": [{"candidate_id": i, "budget_status": "IN_BUDGET", "confidence": "MEDIUM"} for i in ids]}


async def _enrich(sp, um):
    CALLS["enrich"] += 1
    if MODE["enrich"] == "ausfall":
        raise GeminiFehlgeschlagen("Test: Enrichment ausgefallen")
    ids = re.findall(r"candidate_id=(\S+)", um)
    return {"candidates": [{"candidate_id": i, "why_fits": ["Grund eins", "Grund zwei", "Grund drei"],
                            "trade_offs": ["Nachteil eins", "Nachteil zwei"],
                            "estimated_price_min": 18000, "estimated_price_max": 25000,
                            "price_confidence": "MEDIUM"} for i in ids]}


afb.call_gemini_json = _budget
afe.call_gemini_json = _enrich
client = TestClient(_app)
H = {"Authorization": "Bearer dev-key-change-in-prod"}

with get_conn() as _c:
    BASIS = af._lade_kandidatenbasis_gecacht(_c)


def zeilen(baureihe_id):
    return [r for r in BASIS if r["baureihe_id"] == baureihe_id]


def roh_variante(variante_id):
    return next(r for r in BASIS if r["variante_id"] == variante_id)


def texte(punkte, rb=None):
    k = SimpleNamespace(bekannte_punkte=punkte, trade_offs=[])
    return R._bekannte_punkte(k)


# ════════════════════════════════════════════════════════════════════════
print("=== AF-05) Bekannte Punkte: Motor-Gate, Zeitgeltung, Prüfstatus ===")
V116 = roh_variante("bmw-1er-f20-f21-116i")        # Benzin, N13
V120D = roh_variante("bmw-1er-f20-f21-120d")       # Diesel, N47

p_spaet = af._bekannte_punkte_fuer(V116, (2016, 2019))
t_spaet = " | ".join(texte(p_spaet))
print("   116i 2016-2019:", t_spaet)
check("A1 Diesel-Hinweis (N47) entfällt beim Benziner",
      "N47" not in t_spaet and not any("Dieselmotoren" in t for t in texte(p_spaet)))
check("A2 zeitlich ausgeschlossene Fakten (2011-2013) entfallen: weder N20-Baureihenhinweis noch Motor-Steuerkette",
      "Steuerkette" not in t_spaet)
check("A3 zeitlose Motorfakten ohne Zeitangabe bleiben, mit Prüfstatus in Worten",
      "Hochdruckpumpe" in t_spaet and "noch nicht von ENFAL geprüft" in t_spaet)

p_frueh = af._bekannte_punkte_fuer(V116, (2011, 2013))
t_frueh = texte(p_frueh)
print("   116i 2011-2013:", " | ".join(t_frueh))
check("A4 im passenden Zeitraum bleibt der Motorfakt (Steuerkette); Diesel- UND N20-Hinweis (anderer Motorcode) entfallen",
      any("Bekanntes Motorproblem: Steuerkette" in t for t in t_frueh)
      and not any("N47" in t or "N20" in t for t in t_frueh))
t_120d = texte(af._bekannte_punkte_fuer(V120D, (2011, 2013)))
check("A5 Baureihenhinweis und Motorfakt bleiben unterscheidbar (120d)",
      any("Bekannte Schwachstelle:" in t and "für die Baureihe dokumentiert, nicht zwingend" in t for t in t_120d)
      and any(t.startswith("Bekanntes Motorproblem") for t in t_120d)
      and all("nicht zwingend" not in t for t in t_120d if t.startswith("Bekanntes Motorproblem")))

p_offen = af._bekannte_punkte_fuer(V116, (None, None))
t_offen = texte(p_offen)
print("   116i ohne Fenster:", " | ".join(t_offen))
check("A6 ohne Baujahrfenster bleibt die Zeitgeltung ausdrücklich UNKLAR (nicht still passend)",
      any("passt nicht eindeutig zum gesuchten Baujahr" in t for t in t_offen))

p_d = af._bekannte_punkte_fuer(V120D, (2011, 2013))
t_d = " | ".join(texte(p_d))
check("A7 Gegenprobe Diesel: der N47-Hinweis bleibt beim Diesel, der N20-Benzinerhinweis entfällt",
      "N47" in t_d and "N20" not in t_d, t_d)

# Prüfstatus: über ALLE Kandidaten der Seed-Basis konsistent zum Fakt-Trust
verletzt, gesehen = [], 0
for r in BASIS:
    if r["marke"] not in ("BMW", "Audi", "Volkswagen", "Opel"):
        continue
    for p in af._bekannte_punkte_fuer(r, (None, None)):
        if p["art"] == "rueckruf":
            continue
        gesehen += 1
        t = texte([p])[0]
        geprueft = "von ENFAL geprüft" in t.replace("noch nicht von ENFAL geprüft", "").replace(
            "nur teilweise von ENFAL geprüft", "")
        if geprueft != (p["trust"] == "verified"):
            verletzt.append((r["variante_id"], p["trust"], t))
print(f"   Prüfstatus-Konsistenz über {gesehen} Fakten")
check("A8 kein ungeprüfter oder teilgeprüfter Fakt trägt je die Aussage 'von ENFAL geprüft' (und umgekehrt)",
      gesehen > 50 and not verletzt, str(verletzt[:2]))


def lauf(body):
    CALLS.update(budget=0, enrich=0, tavily=0)
    r = client.post("/api/v1/autofinder", headers=H, json=body)
    assert r.status_code == 200, r.text
    return r.json(), dict(CALLS)


WUNSCH_BEKANNT = {"kraftstoff": ["Benzin"], "nutzung": "stadt", "sparsam": True, "budget_max": 40000,
                  "baujahr_von": 2011, "baujahr_bis": 2013}
MODE["enrich"] = "ok"
d_ok, calls_ok = lauf(WUNSCH_BEKANNT)
MODE["enrich"] = "ausfall"
d_fb, calls_fb = lauf(WUNSCH_BEKANNT)
MODE["enrich"] = "ok"
alle_punkte = [(k["enrichment_status"], p) for dd in (d_ok, d_fb) for k in dd["kandidaten"]
               for p in k["known_points"] if p.startswith(("Bekannte Schwachstelle", "Bekanntes Motorproblem"))]
print(f"   Endpunkt: {len(d_ok['kandidaten'])} Karten (Modell), {len(d_fb['kandidaten'])} Karten (Fallback), "
      f"{len(alle_punkte)} Schwachstellenpunkte")
check("A9 Testvoraussetzung: Haupt- und Fallback-Pfad liefern Karten mit Status ok bzw. fallback, ohne Websuche",
      calls_ok["tavily"] == 0 and calls_fb["tavily"] == 0 and d_ok["kandidaten"] and d_fb["kandidaten"]
      and all(k["enrichment_status"] == "ok" for k in d_ok["kandidaten"])
      and all(k["enrichment_status"] == "fallback" for k in d_fb["kandidaten"]))
check("A10 in BEIDEN Pfaden trägt jeder Schwachstellenpunkt einen Prüfstatus in Worten",
      {s for s, _ in alle_punkte} >= {"ok", "fallback"}
      and all(re.search(r"(noch nicht|nur teilweise|von) ENFAL geprüft", p) for _, p in alle_punkte),
      str([p for _, p in alle_punkte if not re.search(r"ENFAL geprüft", p)][:2]))
check("A11 im Fallback-Pfad erscheinen ungeprüfte Fakten nicht als Trade-off-Tatsache",
      all("noch nicht" not in t for k in d_fb["kandidaten"] for t in k["trade_offs"]))

# ════════════════════════════════════════════════════════════════════════
print("\n=== AF-04) Größenklasse: Segment für Kleinwagen vs. Kompakt ===")
polo = [r for r in zeilen("volkswagen-polo-vi")]
a1 = [r for r in zeilen("audi-a1-gb")]
golf = [r for r in zeilen("volkswagen-golf-viii")]
check("B1 Polo VI und A1 (Segment Kleinwagen) sind Kleinwagen, nicht Kompakt",
      all(r["_karo"] == {"kleinwagen"} for r in polo + a1), str({tuple(r["_karo"]) for r in polo + a1}))
check("B2 Golf VIII (Segment Kompaktklasse) bleibt Kompakt, seine Karosserieform Kombi unberührt",
      all("kompakt" in r["_karo"] and "kleinwagen" not in r["_karo"] for r in golf))


def passt(r, **kw):
    return af.erfuellt_harte_filter(r, af.AutoFinderRequest(**kw))


p0, g0, a0 = polo[0], golf[0], a1[0]
check("B3 Auswahl Kleinwagen: Polo/A1 ja, Golf nein",
      passt(p0, karosserie=["kleinwagen"]) and passt(a0, karosserie=["kleinwagen"])
      and not passt(g0, karosserie=["kleinwagen"]))
check("B4 Auswahl Kompakt: Golf ja, Polo/A1 nein",
      passt(g0, karosserie=["kompakt"]) and not passt(p0, karosserie=["kompakt"])
      and not passt(a0, karosserie=["kompakt"]))
check("B5 Auswahl beide: alle drei",
      all(passt(r, karosserie=["kleinwagen", "kompakt"]) for r in (p0, g0, a0)))
check("B6 unbekanntes Segment: bisherige Klassifikation bleibt",
      groessenklasse_anwenden(frozenset({"kompakt"}), "unbekannt") == frozenset({"kompakt"})
      and groessenklasse_anwenden(frozenset({"kompakt"}), SEG_KOMPAKTKLASSE) == frozenset({"kompakt"}))
check("B7 andere Karosseriearten werden nie durch das Segment überschrieben",
      groessenklasse_anwenden(frozenset({"suv"}), SEG_KLEINWAGEN) == frozenset({"suv"})
      and groessenklasse_anwenden(frozenset({"kombi", "kompakt"}), SEG_KLEINWAGEN) == frozenset({"kombi", "kleinwagen"})
      and groessenklasse_anwenden(frozenset({"van"}), SEG_KLEINWAGEN) == frozenset({"van"}))
taigo = zeilen("volkswagen-taigo-2020")
check("B8 Kleinwagen-SUV (Taigo) bleibt SUV/Coupé, kein Kleinwagen",
      taigo and all("suv" in r["_karo"] and "kleinwagen" not in r["_karo"] for r in taigo))

d_kw, _ = lauf({"karosserie": ["kleinwagen"], "nutzung": "stadt", "sparsam": True})
d_ko, _ = lauf({"karosserie": ["kompakt"], "nutzung": "stadt", "sparsam": True})
print("   Karten Kleinwagen:", [(k["marke"] + " " + k["modell"], k["karosserie"]) for k in d_kw["kandidaten"]])
print("   Karten Kompakt:   ", [(k["marke"] + " " + k["modell"], k["karosserie"]) for k in d_ko["kandidaten"]])
check("B9 Karte, Auswahl und Endvalidierung meinen dasselbe: Kleinwagen-Suche zeigt nur Kleinwagen",
      d_kw["kandidaten"] and all(k["karosserie"] == ["kleinwagen"] for k in d_kw["kandidaten"])
      and d_kw["status"] == "ok")
check("B10 Kompakt-Suche zeigt keinen Kleinwagen",
      d_ko["kandidaten"] and all("kleinwagen" not in k["karosserie"] and "kompakt" in k["karosserie"]
                                 for k in d_ko["kandidaten"]))

# ════════════════════════════════════════════════════════════════════════
print("\n=== AF-03) Generationsjahre statt Motorjahre im Modellkontext ===")
K_INTERN = SimpleNamespace(baujahr_von=2016, baujahr_bis=2019, generation_baujahr_von=2011, generation_baujahr_bis=2019)
K_INTERN_OHNE = SimpleNamespace(baujahr_von=2011, baujahr_bis=2019, generation_baujahr_von=2011, generation_baujahr_bis=2019)
K_WEB = SimpleNamespace(baujahr_von=2018, baujahr_bis=2022, generation_baujahr_von=None, generation_baujahr_bis=None)
c1, c2, c3 = baujahr_kontext(K_INTERN), baujahr_kontext(K_INTERN_OHNE), baujahr_kontext(K_WEB)
print("  ", c1, "|", c2, "|", c3)
check("C1 Datensatz-Kandidat: Generationsjahre + 'Motorzeitraum nicht hinterlegt', Wunschfenster als solches",
      "Baureihe gebaut 2011–2019" in c1 and "Motorzeitraum nicht hinterlegt" in c1
      and "Wunschfenster des Nutzers 2016–2019" in c1 and "keine Fahrzeugeigenschaft" in c1)
check("C2 ohne Wunschfenster keine Fenster-Aussage", "Wunschfenster" not in c2 and "nicht hinterlegt" in c2)
check("C3 Web-Kandidat mit belegter Bauzeit bleibt unverändert", c3 == "Baujahre 2018–2022")

_req116 = af.AutoFinderRequest(marken_bevorzugt=["BMW"], baujahr_von=2016, baujahr_bis=2019, kraftstoff=["Benzin"])
V120I = roh_variante("bmw-1er-f2x-120i-n20")        # keine Geltungszeile: Motorzeitraum nicht hinterlegt
k116 = af._zu_kandidat(V120I, 0.0, [], 1.0, _req116)
msg = afe._baue_user_message([k116], af.AutoFinderRequest(baujahr_von=2016, baujahr_bis=2019))
print("   Enrichment-Block:", [z for z in msg.splitlines() if "Baureihe gebaut" in z][:1])
check("C4 der Enrichment-Prompt nennt Generationsjahre, fehlenden Motorzeitraum und Fenster als Wunsch",
      "Baureihe gebaut 2011–2019, Motorzeitraum nicht hinterlegt" in msg
      and "Wunschfenster des Nutzers 2016–2019" in msg and "Relevante Baujahre" not in msg)
check("C5 der Budget-Prompt nutzt dieselbe ehrliche Angabe",
      "Motorzeitraum nicht hinterlegt" in afb._formatiere_kandidat_zeile(k116))
check("C6 der Prompt verbietet Aussagen über Motorzeiträume und Wunschfenster als Fahrzeugmerkmal",
      "Der Motorzeitraum ist NICHT hinterlegt" in afe._SYSTEM_PROMPT)

# Das Jahresfenster wirkt weiter (Generationsüberlappung): AF-03 ändert keinen Filter
check("C7 der Jahresfilter wirkt unverändert auf die Generationsbauzeit (120i 2011-2019 passt zu 2016-2019, nicht zu 2021+)",
      passt(V120I, baujahr_von=2016, baujahr_bis=2019) and not passt(V120I, baujahr_von=2021))

# Beanstandete Kombination Getriebe: der Datensatz führt mehrere, Karte sagt 'wählbar'
golf_tsi = roh_variante("volkswagen-golf-viii-1.0-tsi-81-kw")
kt = af._zu_kandidat(golf_tsi, 0.0, [], 1.0, af.AutoFinderRequest(getriebe=["automatik"], kraftstoff=["Benzin"]))
check("C8 Golf 1.0 TSI, Wunsch Automatik: kein bestätigtes Automatikgetriebe, bestätigt ist nur das Schaltgetriebe (DSG ist als nicht bestätigt geführt)",
      kt is not None and kt.getriebe_konkret == "manuell" and "automatik" in kt.getriebe_unbestaetigt)

# ════════════════════════════════════════════════════════════════════════
print("\n=== AF-06) Modelltext und Verbrauch ===")
K_F20 = SimpleNamespace(
    marke="BMW", modell="1er", generation="F20/F21", generation_label=None, motor_bezeichnung="116i",
    baujahr_von=2016, baujahr_bis=2019, generation_baujahr_von=2011, generation_baujahr_bis=2019,
    kraftstoff="Benzin", getriebe_konkret="automatik", getriebe_klassen=["automatik", "manuell"],
    leistung_ps=136, antrieb="Heck", karosserie_konkret="kompakt", karosserie_klassen=["kompakt"],
    verbrauch_l_100km=7.0, verbrauch_basis="real", drehmoment_nm=220, beschleunigung_0_100_s=8.5,
    match_gruende=[], budget_status="UNKNOWN", source_type="internal_db", baureihe_id="b", variante_id="v116i")
K_F20.candidate_id = afe._kandidat_id(K_F20)


async def _modell_antwort(sp, um):
    return {"candidates": [{
        "candidate_id": K_F20.candidate_id,
        "why_fits": ["Die serienmäßige 8-Stufen-Wandlerautomatik schaltet komfortabel.",
                     "Das kompakte Format passt gut zum Stadtverkehr.",
                     "Verbrauch von 5,1 l/100 km laut WLTP, belegt.",
                     "Heckantrieb sorgt für ein sportliches Fahrgefühl. Es passt zum Budget."],
        "trade_offs": ["Gutmütiges Eigenlenkverhalten in allen Wettersituationen.",
                       "Der Verbrauch liegt bei etwa 7,0 l/100 km (geschätzter Realwert, kein WLTP).",
                       "Unterhalt und Versicherung liegen über dem Kleinwagen-Schnitt."],
        "estimated_price_min": 18000, "estimated_price_max": 25000, "price_confidence": "MEDIUM"}]}


afe.call_gemini_json = _modell_antwort
enr_map, ausgefallen = asyncio.run(afe.enrich_kandidaten([K_F20], af.AutoFinderRequest(nutzung="stadt")))
afe.call_gemini_json = _enrich
e = enr_map[K_F20.candidate_id]
w, t = " ".join(e.why_fits), " ".join(e.trade_offs)
print("   why_fits:", e.why_fits, "\n   trade_offs:", e.trade_offs)
check("D1 'serienmäßige 8-Stufen-Wandlerautomatik' (Automatik belegt keine Bauart/Serie) entfällt",
      "serienmäßig" not in w and "Wandler" not in w and "8-Stufen" not in w)
check("D2 die Wetter-Absolutaussage entfällt", "Wettersituation" not in t and "Eigenlenkverhalten" not in t)
check("D3 eine nicht gelieferte Verbrauchszahl ('5,1 l/100 km laut WLTP, belegt') entfällt", "5,1" not in w)
check("D4 die gelieferte Verbrauchszahl mit korrekter Basis ('7,0 ... geschätzter Realwert, kein WLTP') bleibt",
      "7,0 l/100 km" in t and "kein WLTP" in t)
check("D5 zulässige, hilfreiche Aussagen bleiben (Format, Heckantrieb, Budget, Unterhalt)",
      "kompakte Format" in w and "Heckantrieb" in w and "Es passt zum Budget" in w and "Unterhalt" in t)
check("D6 die KI-Preisspanne bleibt vollständig erhalten",
      (e.estimated_price_min, e.estimated_price_max, e.price_confidence) == (18000, 25000, "MEDIUM"))
check("D7 Satzweise: ein Satz mit Mangel fällt, die übrigen Sätze desselben Eintrags bleiben",
      afe.bereinige_unbelegte_aussagen(["Schaltet serienmäßig sanft. Der Motor ist leise."], K_F20)
      == ["Der Motor ist leise."])
K_WLTP = SimpleNamespace(**{**K_F20.__dict__, "verbrauch_l_100km": 5.4, "verbrauch_basis": "wltp"})
check("D8 WLTP-Basis: gelieferte Zahl mit Basis bleibt; 'tatsächlich/belegt' fällt; falsche Basis (WLTP bei Schätzwert) fällt",
      afe.bereinige_unbelegte_aussagen(["Der WLTP-Wert laut Datensatz liegt bei 5,4 l/100 km."], K_WLTP)
      and not afe.bereinige_unbelegte_aussagen(["Der tatsächliche Verbrauch liegt bei 5,4 l/100 km."], K_WLTP)
      and not afe.bereinige_unbelegte_aussagen(["Der WLTP-Verbrauch beträgt 7,0 l/100 km."], K_F20))
check("D9 Preisaussagen im Text und andere Zahlen werden nicht angefasst",
      afe.bereinige_unbelegte_aussagen(["Ein Gebrauchter kostet etwa 18.000 bis 25.000 €. Er hat 136 PS."], K_F20)
      == ["Ein Gebrauchter kostet etwa 18.000 bis 25.000 €. Er hat 136 PS."])
block = afe._kandidat_block(K_F20, af.AutoFinderRequest())
print("   Prompt-Block:", [z.strip() for z in block.splitlines() if "Verbrauch" in z or "Getriebe" in z])
check("D10 der Prompt nennt die Verbrauchsbasis, kennzeichnet die Getriebeangabe als bloße Klasse",
      "geschätzter Realwert (kein WLTP)" in block and "nicht extern geprüft" in block
      and "Bauart, Gangzahl und Serienausstattung sind nicht angegeben" in block)

bewertet = af._score_kandidat(V116, af.AutoFinderRequest(nutzung="langstrecke", sparsam=True))
gr_text = " | ".join(bewertet[1])
vg = roh_variante("bmw-1er-f20-f21-116i")
check("D11 'Verbrauch belegt' existiert nicht mehr; der Grund nennt Datensatz und Basis",
      "belegt" not in gr_text and "laut Datensatz" in gr_text and "geschätzter Realwert, kein WLTP" in gr_text, gr_text)
V_WLTP = next(r for r in BASIS if r["_verbrauch_basis"] == "wltp")
check("D12 bei hinterlegtem WLTP-Wert steht 'WLTP-Wert', ohne 'belegt'",
      "WLTP-Wert" in af._verbrauch_text(V_WLTP) and "belegt" not in af._verbrauch_text(V_WLTP))

# ════════════════════════════════════════════════════════════════════════
print("\n=== Unverändertes Such-/Bewertungsverhalten und Providerzahl ===")
d_pz, calls_pz = lauf({"nutzung": "stadt", "sparsam": True, "budget_max": 30000})
check("E1 Providerzahl unverändert: je ein Budget- und Enrichment-Aufruf, kein Tavily",
      calls_pz == {"budget": 1, "enrich": 1, "tavily": 0}, str(calls_pz))
check("E2 die KI-Preisspanne erscheint weiter auf den Karten",
      d_pz["kandidaten"] and all(k["estimated_price_min"] == 18000 and k["estimated_price_max"] == 25000
                                 for k in d_pz["kandidaten"]))
check("E3 Fit-Schwelle und Ausgabelimit unverändert",
      all(k["user_fit"] >= 80 for k in d_pz["kandidaten"]) and len(d_pz["kandidaten"]) <= 5)

# ════════════════════════════════════════════════════════════════════════
print("\n=== AF-05b) Motorwiderspruch: gleicher Kraftstoff hebt einen ausdrücklich anderen Motorcode nicht auf ===")
from app.motor_applicability import schwachstelle_applicability as _sa

_N20 = {"bauteil": "Steuerkette (N20 Benzinmotoren)", "beschreibung": "x", "betroffene_baujahre": None}
_M13 = {"variante_id": "a", "bezeichnung": "116i", "motorcode": "N13B16", "kraftstoff": "Benzin", "zylinder": 4}
_M20 = {"variante_id": "b", "bezeichnung": "120i", "motorcode": "N20B20", "kraftstoff": "Benzin", "zylinder": 4}
_MOHNE = {"variante_id": "c", "bezeichnung": "116i", "motorcode": None, "kraftstoff": "Benzin", "zylinder": 3}
_BR = {"id": "x", "motoren": [_M13, _M20, _MOHNE]}
check("G1 gleicher Kraftstoff, ausdrücklich anderer Motorcode (N20 vs. N13B16): ausgeschlossen",
      _sa(_N20, _M13, _BR, motorcode_widerspruch=True)[0] == "incompatible")
check("G2 passender Motorbezug (N20 vs. N20B20): erhalten",
      _sa(_N20, _M20, _BR, motorcode_widerspruch=True)[0] == "kompatibel")
check("G3 fehlender Motorcode am Fahrzeug: kein Ausschluss aus Datenmangel (bleibt kompatibel/eingeschränkt)",
      _sa(_N20, _MOHNE, _BR, motorcode_widerspruch=True)[0] != "incompatible")
check("G4 fehlende Motorvariante: weiterhin 'unklar'",
      _sa(_N20, None, _BR, motorcode_widerspruch=True)[0] == "unklar")
check("G5 KaufCheck/VerkaufCheck-Verhalten (Vorgabe ohne Schalter) bleibt unverändert: derselbe Fall bleibt kompatibel",
      _sa(_N20, _M13, _BR)[0] == "kompatibel" and _sa(_N20, _M13, _BR)[1] == "kraftstoff_treffer")
check("G6 gemischter Scope (Code + Kürzel) löst den Motorcode-Widerspruch nicht aus",
      _sa({"bauteil": "Turbolader (N20 TFSI)", "beschreibung": "x"}, _M13, _BR,
          motorcode_widerspruch=True)[0] != "incompatible")
check("G7 zeitlich eindeutig außerhalb: ausgeschlossen; unscharf: unklar; passend: ok",
      af._zeit_status("2011-2013", 2016, 2019) == af.Z_AUSSERHALB
      and af._zeit_status("2011-2013", 2012, 2012) == af.Z_OK
      and af._zeit_status("2011-2013", None, None) == af.Z_UNKLAR)

# Echte Daten: der N20-Benzinerhinweis erscheint nicht mehr beim 116i (N13B16), auch nicht ohne Fenster
p_n13 = texte(af._bekannte_punkte_fuer(roh_variante("bmw-1er-f20-f21-116i"), (None, None)))
check("G8 echter Datensatz: 116i (N13B16) zeigt den N20-Hinweis auch ohne Baujahrfenster nicht",
      not any("N20" in t for t in p_n13), " | ".join(p_n13))
p_n20 = texte(af._bekannte_punkte_fuer(roh_variante("bmw-1er-f2x-120i-n20"), (None, None)))
check("G9 echter Datensatz: der 120i (N20B20) behält den N20-Hinweis", any("N20" in t for t in p_n20))

# ════════════════════════════════════════════════════════════════════════
print("\n=== AF-03b) Belegte Motorvarianten: 116i vor/nach der Modellpflege, Golf TSI gegenüber eTSI ===")
with get_conn() as _c2:
    _g = {r[0]: r for r in _c2.execute("select variante_id, bauzeit_von, bauzeit_bis, getriebe_unbestaetigt, quelle, notiz "
                                       "from motorvariante_geltung")}
    _lci = _c2.execute("select leistung_ps, leistung_kw, hubraum_ccm, zylinder, drehmoment_nm, getriebe, motorcode "
                       "from motorvariante where variante_id='bmw-1er-f20-f21-116i-lci'").fetchone()
check("F1 die belegte 116i der Modellpflege ist da: 109 PS/80 kW, 1499 cm³, 3 Zylinder, 180 Nm, nur 6-Gang manuell, Motorcode nicht geraten",
      tuple(_lci) == (109, 80, 1499, 3, 180, '["6-Gang manuell"]', None), str(tuple(_lci) if _lci else None))
check("F2 jede Geltungszeile trägt Beleg und Notiz",
      all(r[4] and "2026-11-23" in r[4] and r[5] for r in _g.values()) and len(_g) == 4)
check("F3 der ältere 116i (136 PS, Automatik) bleibt als eigene Zeile erhalten, nur nach oben begrenzt",
      roh_variante("bmw-1er-f20-f21-116i")["leistung_ps"] == 136 and _g["bmw-1er-f20-f21-116i"][1:3] == (None, 2015))
check("F4 der 116i der Modellpflege beginnt 2015, ohne belegtes Ende", _g["bmw-1er-f20-f21-116i-lci"][1:3] == (2015, None))

with get_conn() as _c3:
    BASIS = af._annotiere_basis(af._lade_rohkandidaten(_c3))   # frisch, ohne 60-s-Cache


def treffer(**kw):
    req = af.AutoFinderRequest(**kw)
    return [r["variante_id"] for r in BASIS if af.erfuellt_harte_filter(r, req)]


spaet = treffer(marken_bevorzugt=["BMW"], baujahr_von=2016, baujahr_bis=2019)
frueh = treffer(marken_bevorzugt=["BMW"], baujahr_von=2011, baujahr_bis=2014)
check("F5 Fenster 2016-2019: der 116i der Modellpflege passt, der ältere 116i (136 PS) NICHT mehr als passende Ausführung",
      "bmw-1er-f20-f21-116i-lci" in spaet and "bmw-1er-f20-f21-116i" not in spaet)
check("F6 Fenster 2011-2014: der ältere 116i passt weiterhin, der der Modellpflege (ab 2015) nicht",
      "bmw-1er-f20-f21-116i" in frueh and "bmw-1er-f20-f21-116i-lci" not in frueh)
check("F7 der alte 118i mit 170 PS wird für 2016-2019 nicht mehr als passend ausgegeben",
      "bmw-1er-f20-f21-118i" not in spaet and "bmw-1er-f20-f21-118i" in frueh)
auto_spaet = treffer(marken_bevorzugt=["BMW"], baujahr_von=2016, baujahr_bis=2019, getriebe=["automatik"])
check("F8 Automatik 2016-2019: kein 116i mehr (die Modellpflege-Ausführung hat nur Schaltgetriebe)",
      not any("1er-f20-f21-116i" in v for v in auto_spaet))
auto_frueh = treffer(marken_bevorzugt=["BMW"], baujahr_von=2011, baujahr_bis=2014, getriebe=["automatik"])
check("F9 Automatik 2011-2014: der ältere 116i bleibt (seine Getriebeangabe wird nicht angefasst)",
      "bmw-1er-f20-f21-116i" in auto_frueh)

k_alt = af._zu_kandidat(roh_variante("bmw-1er-f20-f21-116i"), 0.0, [], 1.0, af.AutoFinderRequest())
k_neu = af._zu_kandidat(roh_variante("bmw-1er-f20-f21-116i-lci"), 0.0, [], 1.0, af.AutoFinderRequest())
check("F10 Kartenjahre mit belegter Motorgrenze: alt 2011-2015, neu 2015-2019; Generation bleibt 2011-2019",
      (k_alt.baujahr_von, k_alt.baujahr_bis, k_alt.motor_bauzeit_bis) == (2011, 2015, 2015)
      and (k_neu.baujahr_von, k_neu.baujahr_bis, k_neu.motor_bauzeit_von) == (2015, 2019, 2015)
      and k_alt.generation_baujahr_von == 2011 and k_neu.generation_baujahr_bis == 2019)
ctx = baujahr_kontext(k_alt)
check("F11 der Modellkontext nennt die belegte Grenze und behauptet keinen vollständigen Motorzeitraum",
      "Motorzeitraum laut Datensatz bis 2015" in ctx and "nur diese Grenze ist belegt" in ctx
      and "nicht hinterlegt" not in ctx, ctx)

golf_auto = treffer(marken_bevorzugt=["Volkswagen"], getriebe=["automatik"], kraftstoff=["Benzin", "Mild-Hybrid"])
golf_schalt = treffer(marken_bevorzugt=["Volkswagen"], getriebe=["manuell"], kraftstoff=["Benzin", "Mild-Hybrid"])
check("F12 Golf VIII, Wunsch Automatik: der 1.0 TSI (81 kW) zählt NICHT als bestätigte Auswahl, der 1.0 eTSI schon",
      "volkswagen-golf-viii-1.0-tsi-81-kw" not in golf_auto and "volkswagen-golf-viii-1.0-etsi-81-kw" in golf_auto)
check("F13 Golf VIII, Wunsch Schaltgetriebe: der 1.0 TSI (81 kW) bleibt, der eTSI (nur DSG) nicht",
      "volkswagen-golf-viii-1.0-tsi-81-kw" in golf_schalt and "volkswagen-golf-viii-1.0-etsi-81-kw" not in golf_schalt)
kt = af._zu_kandidat(roh_variante("volkswagen-golf-viii-1.0-tsi-81-kw"), 0.0, [], 1.0, af.AutoFinderRequest(getriebe=["manuell"]))
check("F14 die Karte führt das Getriebe als bestätigt (manuell) und die DSG als nicht bestätigt",
      kt.getriebe_konkret == "manuell" and kt.getriebe_klassen == ["manuell"] and kt.getriebe_unbestaetigt == ["automatik"])
check("F15 Varianten ohne Geltungszeile verhalten sich wie bisher (Polo: keine Motorgrenze, Getriebe unverändert)",
      all(r["motor_bauzeit_von"] is None and r["motor_bauzeit_bis"] is None and not r["_getriebe_unbestaetigt"]
          for r in zeilen("volkswagen-polo-vi")))

import glob as _glob
_anwendungscode = "".join(open(f, encoding="utf-8").read() for f in _glob.glob("app/**/*.py", recursive=True)
                          if not f.replace("\\", "/").endswith(("motor_geltung_daten.py", "data_migrations.py")))
check("F16 keine Fahrzeug-ID-Sperrliste im Anwendungscode (die Daten stehen nur in Tabelle und Datenmodul)",
      "116i-lci" not in _anwendungscode and "golf-viii-1.0-tsi" not in _anwendungscode)

d_f20, c_f20 = lauf({"kraftstoff": ["Benzin"], "nutzung": "stadt", "sparsam": True, "baujahr_von": 2016, "baujahr_bis": 2019})
eng = af.finde_fahrzeuge(af.AutoFinderRequest(marken_bevorzugt=["BMW"], baujahr_von=2016, baujahr_bis=2019,
                                              kraftstoff=["Benzin"]), k=50).kandidaten
print("   Engine BMW Benzin 2016-2019:", [(k.modell, k.motor_bezeichnung, k.leistung_ps) for k in eng])
check("F17 Engine-Ausgabe: die strittige Kombination (116i mit 136 PS, 118i mit 170 PS) erscheint für 2016-2019 nicht",
      not any(k.variante_id in ("bmw-1er-f20-f21-116i", "bmw-1er-f20-f21-118i") for k in eng))
check("F18 Endpunkt: neue Felder sind im Vertrag (motor_bauzeit_*, getriebe_unbestaetigt), ohne Websuche",
      c_f20["tavily"] == 0 and d_f20["kandidaten"]
      and all({"motor_bauzeit_von", "motor_bauzeit_bis", "getriebe_unbestaetigt"} <= set(k) for k in d_f20["kandidaten"]))

print("\n=== AF-05c) Rückrufzahl gehört zur Baureihe ===")
rt = af.rueckruf_text(4)
check("H1 die Rückrufzeile nennt die Baureihe und leitet nichts zu Fahrzeug, Motor, Zeitraum, FIN oder Durchführung ab",
      rt.startswith("Zur Baureihe sind 4 verifizierte KBA-Rückrufe dokumentiert")
      and "nicht gesagt" in rt and "Motor, Zeitraum und FIN nicht geprüft" in rt, rt)
check("H2 Einzahl", af.rueckruf_text(1).startswith("Zur Baureihe sind 1 verifizierte KBA-Rückruf dokumentiert"))
check("H3 die Kennzeichnung 'KBA-Rückruf' bleibt für Fit und Fallback lesbar",
      "KBA-Rückruf" in rt and R._bekannte_punkte(SimpleNamespace(
          bekannte_punkte=[{"art": "rueckruf", "anzahl": 2, "trust": "verified"}], trade_offs=[]))[0].startswith("Zur Baureihe"))

print(chr(10) + "=== KaufCheck-Motorwahl mit req=req (produktiver Zuordnungspfad) und belegten Grenzen ===")
from app.car_lookup import find_motor as _find_motor
from app.models import KaufCheckRequest as _KCR
_b_f20 = __import__("app.database", fromlist=["get_baureihe"]).get_baureihe("BMW", "1er", "F20/F21")


def _wahl(**kw):
    req = _KCR(marke="BMW", modell="1er", **kw)
    return (_find_motor(_b_f20, req.motor, req.modell, req=req) or {}).get("variante_id")


check("M1 2017, Benzin, '116i': die Modellpflege-Variante (die ältere ist durch ihre belegte Grenze ausgeschlossen)",
      _wahl(baujahr=2017, kraftstoff="Benzin", motor="116i") == "bmw-1er-f20-f21-116i-lci")
check("M2 2017, '116i' mit ausdrücklichen 109 PS: dieselbe Variante",
      _wahl(baujahr=2017, kraftstoff="Benzin", motor="116i", leistung_ps=109) == "bmw-1er-f20-f21-116i-lci")
check("M3 2017, '116i' mit 136 PS: keine Variante (ausgeschlossen, nicht still die ältere Zeile)",
      _wahl(baujahr=2017, kraftstoff="Benzin", motor="116i", leistung_ps=136) is None)
check("M4 2012, '116i': die ältere Variante (die der Modellpflege beginnt erst 2015)",
      _wahl(baujahr=2012, kraftstoff="Benzin", motor="116i") == "bmw-1er-f20-f21-116i")
check("M5 2012, '116i' mit 136 PS: die ältere Variante",
      _wahl(baujahr=2012, kraftstoff="Benzin", motor="116i", leistung_ps=136) == "bmw-1er-f20-f21-116i")
check("M6 2012, '116i' mit 109 PS: keine Variante (kein stilles Wählen der erst ab 2015 belegten Zeile)",
      _wahl(baujahr=2012, kraftstoff="Benzin", motor="116i", leistung_ps=109) is None)
check("M7 2015 (beide Grenzen berühren sich): ehrlich unklar, keine Wahl nach Zeilenreihenfolge",
      _wahl(baujahr=2015, kraftstoff="Benzin", motor="116i") is None)
check("M8 Variante ohne Grenzen bleibt unverändert (120i 2017)",
      _wahl(baujahr=2017, kraftstoff="Benzin", motor="120i") == "bmw-1er-f2x-120i-n20")
check("M9 ohne Baujahr keine neue Annahme: 116i bleibt mehrdeutig",
      _wahl(kraftstoff="Benzin", motor="116i") is None)
check("M10 der Pfad ohne req (req=None) ist unverändert",
      (_find_motor(_b_f20, "116i", "1er") or {}).get("variante_id") == "bmw-1er-f20-f21-116i")

print()
if FEHLER:
    print(f"{len(FEHLER)} Pruefung(en) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle AF-03..06-Pruefungen bestanden.")
