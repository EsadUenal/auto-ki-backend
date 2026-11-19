"""
SW-N19/E1 -- Schwachstellen: Suchrichtung bei null Kandidaten und Fahrzeugkontext in Fachtexten.

Belegte Ursachen (echte Läufe 977695250d / 3a6b2e730d, Lexus RC F 2017, beide ohne einen einzigen Kandidaten, gap
"null_kandidaten"; Originaltexte heute abgerufen, s. Fixture):

  U1 Suchrichtung: die englische Nachsuche fragte nach TSB/Herstellererklärung -- im echten Lauf kamen Rückruf-Händler-
     seiten, eine Broschüre und eine allgemeine Konzernerklärung. Die Quellengattung, die der Pfad für einen begrenzten
     Fachbericht qualifiziert (englischsprachige redaktionelle Fachpresse), wurde nie angefragt. Die PistonHeads-
     Kaufberatung zum RC F nennt modellbezogene Fehlerbilder und erreicht mit dem bestehenden Pfad drei begrenzte Berichte.
  U2 Fahrzeugkontext: "beim Anbremsen", "beim Reisen", "beim Gebrauchtwagenkauf" galten als anderes Fahrzeug; "beim
     Cruisen\\nSolide ..." wurde über einen Zeilenumbruch zu einem Fahrzeugnamen; "Der V8-Saugmotor ..." galt als unsichere
     Nominalphrase. Jeder dieser Fehlalarme brach den Kontext, die folgenden modellbezogenen Problemsätze (Kühlmittel-
     undichtigkeit "Valley Plate", Reifenverschleiß) fielen als `fremdfahrzeug_segment`.

Was NICHT geändert ist: Quellenqualifikation (carvago bleibt ohne Fachpresse-Status, keine Ausgabe daraus), Schwellen,
Budgets (gleiche Anzahl Anfragen), Reichweiten-/Häufigkeits-/Marktregeln, Rückruftrennung.

Fixture: tests/fixtures/sw_quellen_rcf_abruf_2026-11-19.json -- heutige Recherche, KEIN Replay des historischen Requests.
Ein Fixture belegt die Verarbeitung, nicht die Auffindbarkeit in einem künftigen Suchlauf.

    python scripts/test_isolated.py test_sw_null_kandidaten_kaufberatung.py
"""
import asyncio
import json
import logging
import os
import re
import socket
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="sw_nullkand_")
os.environ.setdefault("AUTO_KI_DB_PATH", os.path.join(_tmp, "t.db"))
os.environ.setdefault("AUTO_KI_CHROMA_PATH", os.path.join(_tmp, "chroma"))


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


_orig_connect = socket.socket.connect


def _connect(self, addr, *a, **k):
    if isinstance(addr, tuple) and addr[0] not in ("127.0.0.1", "::1", "localhost"):
        _gesperrt()
    return _orig_connect(self, addr, *a, **k)


socket.socket.connect = _connect
socket.create_connection = _gesperrt
HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
print("Netzsperre (extern):", socket.socket.connect is _connect, "| Temp-DB:", os.environ["AUTO_KI_DB_PATH"])

from app import database as _db  # noqa: E402
_db.ensure_tables()
import app.technical_research as tr  # noqa: E402
import app.web_search as ws  # noqa: E402
from app.evidence import build_insights  # noqa: E402
from app.kaufaktionen import build_kaufaktionen  # noqa: E402
from app.kaufcheck_bericht import bericht  # noqa: E402
from app.models import KaufCheckRequest, WebVehicleIdentity  # noqa: E402
from app.provider_control import provider_scope  # noqa: E402
from app.vehicle_identity import VehicleIdentity  # noqa: E402

FEHLER: list[str] = []


def check(name, ok, detail=""):
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if (detail and not ok) else ""))
    if not ok:
        FEHLER.append(name)


_FIX = json.load(open(os.path.join(HIER, "tests", "fixtures", "sw_quellen_rcf_abruf_2026-11-19.json"), encoding="utf-8"))
Q = {q["key"]: q for q in _FIX["quellen"]}


def treffer(key):
    q = Q[key]
    return {"url": q["url"], "title": q["title"], "content": q["text"][:400], "raw_content": q["text"]}


ADAC_TEXT = open(os.path.join(HIER, "tests", "fixtures", "adac_rcf_244185_seitentext_2026-11-17.txt"), encoding="utf-8").read()
IDENT = [
    {"url": "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/244185/",
     "title": "Lexus RC F Automatik (12/14 - 12/17): Technische Daten, Bilder, Preise | ADAC", "content": ADAC_TEXT},
    # [ECHT] Toyota Deutschland Media, wortgleich aus test_identitaet_diagnose.py
    {"url": "https://www.toyota-media.de/blog/lexus-modelle/artikel/neues-adaptiv-variables-fahrwerk-fuer-lexus-gs-f-und-rc-f/text",
     "title": "NEUES ADAPTIV VARIABLES FAHRWERK FÜR LEXUS F-MODELLE",
     "content": ("Köln, 1. Dezember 2016. Lexus hat die Fahrstabilität und den Fahrkomfort der beiden F-Modelle GS F "
                 "(Kraftstoffverbrauch kombiniert 11,2/100km; CO2-Emissionen 260g/km) und RC F (Kraftstoffverbrauch kombiniert "
                 "10,8/100km; CO2-Emissionen 251g/km) mit der neuesten Generation des adaptiv variablen Fahrwerks AVS aufgewertet. "
                 "Der Lexus GS F und der RC F werden beide von einem 5,0 Liter V8-Saugmotor angetrieben, der 351 kW / 477 PS "
                 "leistet und an eine Achtstufen-Sport-Automatik gekoppelt ist.")},
]
ZIEL_KW = dict(marke="Lexus", modell="RC F", baujahr=2017, motor="5.0 V8, Heckantrieb")

# ══ A: Suchrichtung (U1) ═════════════════════════════════════════════════════════════════════════════════════════
print("== A: Suchrichtung der Schwachstellen-Nachsuche ==")
kw = dict(breit="Lexus RC F", jahr="2017", basis="Lexus RC F UXC1")
q_null = tr._eskalations_queries("schwachstelle", **kw, gap_grund="null_kandidaten")
q_pend = tr._eskalations_queries("schwachstelle", **kw, gap_grund="pending_upgrade")
q_alt = tr._eskalations_queries("schwachstelle", **kw)
check("A1 null Kandidaten: weiterhin genau 2 Anfragen (Budget unverändert); englisch nach Gebrauchtwagen-Kaufberatungen, "
      "deutsch unverändert",
      len(q_null) == 2 and q_null[0] == q_pend[0]
      and q_null[1] == ("problem_report_en", "Lexus RC F used buying guide common problems")
      and not any("technical service bulletin" in q for _, q in q_null), str(q_null))
check("A2 Source-Upgrade (Kandidaten vorhanden) und Aufruf ohne Gap-Angabe: unverändert TSB-/Herstelleranfrage",
      q_pend == q_alt and q_pend[1][0] == "source_upgrade_en" and "technical service bulletin" in q_pend[1][1])
check("A3 Wartung und Runde 3 unverändert",
      tr._eskalations_queries("wartung", **kw, gap_grund="null_kandidaten") == tr._eskalations_queries("wartung", **kw)
      and tr._eskalations_queries("schwachstelle", **kw, runde=3, gap_grund="null_kandidaten")
      == tr._eskalations_queries("schwachstelle", **kw, runde=3))

# ══ B: Fahrzeugkontext (U2) ══════════════════════════════════════════════════════════════════════════════════════
print("\n== B: Fremdfahrzeug-Erkennung -- Fehlalarme weg, echte Fremdfahrzeuge bleiben ==")
af = tr._anderes_fahrzeug_lokal
check("B1 [ECHT-Wortlaut] Tätigkeiten nach 'beim' sind kein Fahrzeug",
      af("Eine bekannte Eigenheit des Lexus RC F ist die Elastizität des Fahrwerks beim Anbremsen.", "RC F") is None
      and af("Dennoch gibt es einige modellspezifische Eigenheiten, die man beim Gebrauchtwagenkauf genau prüfen sollte.",
             "RC F") is None
      and af("Empfohlener Motor: Der V8-Saugmotor, für ein direkteres Ansprechverhalten beim Reisen auf der Autobahn.",
             "RC F") is None)
check("B2 [ECHT-Wortlaut] Listenzeilen: 'beim Cruisen' + Zeilenumbruch + nächster Listenpunkt ist kein Fahrzeugname",
      af("Hohe Fahrstabilität und hervorragender Geräuschkomfort beim Cruisen \nSolide verarbeitetes Interieur", "RC F") is None)
check("B3 [SYNTH] echte Fremdfahrzeuge bleiben erkannt (Eigenname, Kürzel mit Ziffer, mehrteilig in einer Zeile, Kontrast)",
      af("Beim Passat tritt das häufiger auf.", "RC F") == "passat"
      and af("Für den Tiguan gilt ein anderes Intervall.", "RC F") == "tiguan"
      and af("Beim Golf ist das anders.", "RC F") == "golf"
      and af("Beim RC 300h tritt das nicht auf.", "RC F") is not None
      and af("Im Gegensatz zum Mustang hat der Wagen einen Saugmotor.", "RC F") == "mustang")
check("B4 [SYNTH] Grenze ehrlich: ein Kompositum ohne bekannten Tätigkeitskopf bleibt verdächtig ('für den Zugbetrieb')",
      af("Das Fahrzeug ist nicht für den Zugbetrieb homologiert.", "RC F") is not None)
un = tr._unsichere_satzanfangs_nominalphrase
check("B5 [ECHT-Wortlaut] Bauteil-Kompositum am Satzanfang ist kein Fahrzeug; ein Eigenname bleibt unsicher",
      un("Der V8-Saugmotor und das Wandler-Automatikgetriebe gelten als äußerst langlebig.", "RC F") is None
      and un("Der Passat hat ein Problem mit der Wasserpumpe.", "RC F") == "passat")

# Realer carvago-Text durch den bestehenden Extraktionspfad (vorher/nachher)
ZIEL = tr._ziel("Lexus", "RC F", 2017, "5.0 V8, Heckantrieb", None)
ZIEL.update(leistung_ps=477, kilometerstand=82000)
IDENT_W = WebVehicleIdentity(belegt=True, marke="Lexus", modell="RC F", generation="UXC1")
VALLEY = "Abseits der Rückrufe berichten Besitzer über ein wiederkehrendes Kühlmittelproblem"
VALLEY_FAQ = "Bei Exemplaren mit hoher Laufleistung können allerdings Kühlmittelleckagen an der Valley Plate"
REIFEN = "In Berichten und Tests wird immer wieder ein erhöhter Verschleiß an den inneren Reifenschultern"


def extrahiere(key):
    roh, _stat = tr._mit_originaltext([treffer(key)])
    abgelehnt: list[dict] = []
    fakten = tr.phase_fakten({"schwachstelle": roh}, ZIEL, IDENT_W, ("schwachstelle",), abgelehnt,
                             erlaube_multi_source=frozenset({"schwachstelle"}))
    return fakten, abgelehnt


def grund_von(abgelehnt, anfang):
    return [a["grund"] for a in abgelehnt if (a.get("satz") or "").startswith(anfang[:60])]


ALT_RE = re.compile(tr._RE_ANDERES_SUBJEKT.pattern.replace(r"(?:[ \t]+[A-ZÄÖÜ0-9]", r"(?:\s+[A-ZÄÖÜ0-9]"))
_neu = (tr._RE_ANDERES_SUBJEKT, tr._ist_taetigkeits_nominalisierung, tr._ist_bauteil_kompositum)
tr._RE_ANDERES_SUBJEKT, tr._ist_taetigkeits_nominalisierung, tr._ist_bauteil_kompositum = (
    ALT_RE, lambda *a: False, lambda *a: False)
try:
    f_vor, ab_vor = extrahiere("carvago_rcf")
finally:
    tr._RE_ANDERES_SUBJEKT, tr._ist_taetigkeits_nominalisierung, tr._ist_bauteil_kompositum = _neu
f_nach, ab_nach = extrahiere("carvago_rcf")
check("B6 VORHER [ECHT carvago]: die modellbezogenen Problemsätze fallen als 'fremdfahrzeug_segment' (Kontextbruch)",
      grund_von(ab_vor, VALLEY) == ["fremdfahrzeug_segment"] and grund_von(ab_vor, VALLEY_FAQ) == ["fremdfahrzeug_segment"]
      and grund_von(ab_vor, REIFEN) == ["fremdfahrzeug_segment"],
      f"{grund_von(ab_vor, VALLEY)} {grund_von(ab_vor, VALLEY_FAQ)} {grund_von(ab_vor, REIFEN)}")
check("B7 NACHHER: dieselben Sätze erreichen die fachliche Claim-Prüfung (kein Segment-Ausschluss mehr)",
      "fremdfahrzeug_segment" not in grund_von(ab_nach, VALLEY) + grund_von(ab_nach, VALLEY_FAQ) + grund_von(ab_nach, REIFEN)
      and grund_von(ab_nach, REIFEN) == ["schwachstelle_candidate_ohne_hauptpfad_anker"],
      f"{grund_von(ab_nach, VALLEY)} {grund_von(ab_nach, VALLEY_FAQ)} {grund_von(ab_nach, REIFEN)}")
check("B8 ... aber KEINE Ausgabe aus carvago: Quelle ohne Fachpresse-Status, Qualifikation unverändert",
      f_vor == [] and f_nach == [] and any(a["grund"] == "source_quality" for a in ab_nach))
fremd_saetze = [a for a in ab_nach if a["grund"] == "fremdfahrzeug_segment"]
check("B9 echte Fremdfahrzeuge im selben Text bleiben ausgeschlossen (Porsche/Lamborghini/Cupra-Teaser)",
      any("Porsche" in (a.get("satz") or "") for a in fremd_saetze)
      and any("Cupra" in (a.get("satz") or "") or "CUPRA" in (a.get("satz") or "") for a in fremd_saetze))
check("B10 Rückrufe bleiben Rückrufe: der Abschnitt zu den Kraftstoff-Rückrufen erzeugt keinen Schwachstellenkandidaten",
      not any(a["grund"].startswith("schwachstelle_candidate") and "Kraftstoff" in (a.get("satz") or "") for a in ab_nach))

f_ae, ab_ae = extrahiere("autoexpress_rc_used")
check("B11 [ECHT Auto Express, Baureihen-Ratgeber RC] Aussagen zur ganzen RC-Reihe werden NICHT auf die Variante RC F "
      "übertragen",
      f_ae == [] and "schwach_ausgerichtet_ohne_modellnennung" in grund_von(ab_ae, "Globally, air-con glitches"),
      str(grund_von(ab_ae, "Globally, air-con glitches")))

# ══ C: Normaler Recherchepfad bis zur sichtbaren Ausgabe ══════════════════════════════════════════════════════════
print("\n== C: Recherchepfad (kontrollierte Suchantworten = heutige Originaltexte) bis Insight, Prüfpunkte, Bericht ==")


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class FakeClient:
    routing: list[tuple[str, list]] = []
    posts: list[str] = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        q = json["query"]
        FakeClient.posts.append(q)
        if "technische Daten" in q or "Generation Baureihe Bauzeitraum" in q:
            return _Resp({"results": [dict(r) for r in IDENT]})
        for teil, liste in FakeClient.routing:
            if teil in q:
                return _Resp({"results": [dict(r) for r in liste]})
        return _Resp({"results": []})


ws.httpx.AsyncClient = FakeClient
ws.TAVILY_API_KEY = "offline-test-key-not-real"
ROUTING = [
    ("bekannte Probleme Schwachstellen", [treffer("carvago_rcf"), treffer("autobild_rc"), treffer("motortalk_rcf")]),
    ("Gebrauchtwagen Test", [treffer("autozeitung_rcf")]),
    ("Werkstattinformation technischer Hinweis", [treffer("pressroom_tsb")]),
    ("technical service bulletin", [treffer("carcomplaints_tsb")]),                          # bisherige Anfrage
    ("used buying guide common problems", [treffer("ph_rcf_guide"), treffer("autoexpress_rc_used")]),   # neue Anfrage
]


class Sammler(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.zeilen: list[str] = []

    def emit(self, record):
        self.zeilen.append(record.getMessage())


async def lauf():
    ws._cache.clear()
    FakeClient.routing, FakeClient.posts = ROUTING, []
    h = Sammler()
    lg = logging.getLogger("app.technical_research")
    lg.setLevel(logging.INFO)
    lg.addHandler(h)
    try:
        async with provider_scope("kaufcheck", key="sw-nullkand-test") as scope:
            rec = await tr.TavilyTechnicalResearchProvider().recherchiere(ausgeloest_durch="test", **ZIEL_KW)
            budget = dict(scope.calls), dict(scope.skipped)
    finally:
        lg.removeHandler(h)
    return rec, h.zeilen, list(FakeClient.posts), budget


_orig_esk = tr._eskalations_queries
tr._eskalations_queries = lambda *a, gap_grund=None, **k: _orig_esk(*a, **k)       # VORHER: Anfrage ohne Gap-Unterscheidung
try:
    rec_v, log_v, posts_v, budget_v = asyncio.run(lauf())
finally:
    tr._eskalations_queries = _orig_esk
rec_n, log_n, posts_n, budget_n = asyncio.run(lauf())

sw = lambda rec: [f for f in rec.fakten if f.kategorie == "schwachstelle"]  # noqa: E731
r2 = lambda log: next((z for z in log if z.startswith("research_phase=schwachstelle round=2")), "")  # noqa: E731
check("C1 Runde 1 ohne Kandidaten -> Nachsuche 'null_kandidaten' in beiden Läufen",
      "gap=null_kandidaten" in r2(log_v) and "gap=null_kandidaten" in r2(log_n), r2(log_n))
check("C2 VORHER: englische Anfrage nach TSB (US-Aggregator) -> kein Schwachstellenfakt",
      any("technical service bulletin" in p for p in posts_v) and sw(rec_v) == [], str([f.bauteil for f in sw(rec_v)]))
check("C3 NACHHER: englische Anfrage nach Kaufberatungen -> drei begrenzte Fachberichte aus der PistonHeads-Kaufberatung",
      any("used buying guide common problems" in p for p in posts_n)
      and sorted(f.bauteil for f in sw(rec_n)) == ["Klimaanlage", "Rost", "Sitzheizung/-belüftung"]
      and all(f.beleglage == "begrenzter_bericht" and f.confidence == "niedrig" for f in sw(rec_n))
      and all("pistonheads.com" in q.url for f in sw(rec_n) for q in f.quellen), str([(f.bauteil, f.beleglage) for f in sw(rec_n)]))
check("C4 Budget: gleiche Zahl Suchaufrufe vorher/nachher, Calls == HTTP-Versuche, nichts übersprungen",
      len(posts_v) == len(posts_n) and budget_n[0]["tavily"] == len(posts_n) and budget_n[1]["tavily"] == 0
      and budget_v[0] == budget_n[0], f"{len(posts_v)} vs {len(posts_n)}; {budget_v} vs {budget_n}")
check("C5 fachfremde Ergebnisse unverändert (Rückruf-/Wartungsfakten, Identität)",
      [(f.kategorie, f.bauteil) for f in rec_v.fakten if f.kategorie != "schwachstelle"]
      == [(f.kategorie, f.bauteil) for f in rec_n.fakten if f.kategorie != "schwachstelle"]
      and rec_v.identitaet.model_dump() == rec_n.identitaet.model_dump())
check("C6 kein Rückruf als neue Schwachstelle (Kraftstoffleitung/Hochdruckpumpe nicht als Schwachstellenfakt)",
      not any(re.search(r"kraftstoff|fuel|pump", (f.bauteil or "") + f.aussage, re.I) for f in sw(rec_n)))
check("C7 Marktvorbehalt und Baujahrgeltung bleiben ehrlich offen (englische UK-Quelle)",
      all(f.regionale_geltung == "unresolved" and f.geltung_fuer_fahrzeug == "unresolved" for f in sw(rec_n)))

REQ = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000, kraftstoff="Benzin",
                       motor="5.0 V8, Heckantrieb", leistung_ps=477, getriebe="Automatik", ausstattung=[], vorbesitzer=2,
                       scheckheftgepflegt=True)
ins = build_insights(None, None, [], REQ, check_typ="kauf", canonical_rueckrufe=[], web_recherche=rec_n)
iw = {i.bauteil: i for i in ins if i.kategorie == "web_schwachstelle"}
check("C8 Insights: drei Web-Schwachstellen, Titel nennt den begrenzten Bericht, Datenqualität niedrig",
      set(iw) == {"Klimaanlage", "Rost", "Sitzheizung/-belüftung"}
      and all("(begrenzter Bericht einer Fachquelle)" in i.titel and i.confidence == "niedrig" for i in iw.values()), str(set(iw)))
b = iw.get("Rost").beschreibung if "Rost" in iw else ""
check("C9 Beschreibung: Originalzitat, keine Häufigkeit, Baujahr- und Deutschlandgeltung nicht belegt",
      "poorly-applied rear wheelarch sealer" in b and "eine Häufigkeit oder eine modellweite Schwachstelle ist daraus nicht belegt" in b
      and "nicht belegt" in b and "Ob das für Ihr Fahrzeug in Deutschland zutrifft, ist nicht bestätigt" in b, b)
ins_v = build_insights(None, None, [], REQ, check_typ="kauf", canonical_rueckrufe=[], web_recherche=rec_v)
check("C10 VORHER keine Web-Schwachstellen-Insights", not [i for i in ins_v if i.kategorie == "web_schwachstelle"])
ka = build_kaufaktionen(REQ, None, None, ins)
besi = {a.titel: a.aktion for a in ka.besichtigung.fahrzeugspezifisch}
fra = {a.titel: a.aktion for a in ka.verkaeuferfragen.fahrzeugspezifisch}
check("C11 Besichtigungspunkte mit Herkunfts-/Geltungsvorbehalt für Klimaanlage und Rost",
      "Klimaanlage" in besi and "Rost" in besi and "Geltung für dieses Baujahr nicht belegt" in besi["Klimaanlage"], str(list(besi)))
fk = fra.get("Wurde am Bauteil „Klimaanlage“ bereits gearbeitet oder etwas ersetzt?", "")
check("C12 Verkäuferfrage: einzelne Fälle, keine belegte Häufigkeit, kein 'bekannter Schwachpunkt'",
      "Eine Fachquelle berichtet von einzelnen Fällen" in fk and "keine belegte Häufigkeit" in fk and "Schwachpunkt" not in fk, fk)
check("C13 keine künstliche Dokumentenanforderung aus den begrenzten Berichten",
      not [a for a in ka.dokumente.fahrzeugspezifisch if a.kategorie == "web_schwachstelle"])
text = bericht(REQ, VehicleIdentity.from_check_context(None, None, REQ), None, None, ins, ka, [], "kaufen_nach_besichtigung",
               type("PA", (), {"label": None})(), False, None, None, ["Inserat"])
abschnitt = text.split("Relevante Risiken und Hinweise", 1)[1].split("\n## ", 1)[0] if "Relevante Risiken und Hinweise" in text else ""
check("C14 Bericht: Abschnitt nennt Aussage, begrenzten Status und Quelle (pistonheads.com, Fachmedien)",
      "### Rost" in abschnitt and "Some owners have had an HVAC servo motor" in abschnitt
      and "pistonheads.com (Fachmedien)" in abschnitt and "keine belegte Häufigkeit" in abschnitt, abschnitt[:400])
aus = [z for z in log_n if z.startswith("sw_diagnose") and "stufe=treffer" in z and "pistonheads.com" in z]
check("C15 SW-Diagnose weist den neuen Intent und die Fakten des Treffers aus",
      aus and "herkunft=r2:problem_report_en" in aus[0] and "fakten=begrenzter_bericht" in aus[0], str(aus[:1]))

print("\n" + ("ALLE GRÜN" if not FEHLER else f"{len(FEHLER)} FEHLER: {FEHLER}"))
sys.exit(1 if FEHLER else 0)
