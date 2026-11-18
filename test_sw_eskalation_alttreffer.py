"""
Schwachstellen-Eskalation: bereits erfolglos (mit Volltext) ausgewertete Alttreffer belegen keine erneuten Kuratierungsplätze.

Befund (Lauf 977695250d, 17.11.2026): Runde 2 kuratierte `roh + neue_treffer` neu; bei Domain-Score-Gleichstand gewannen die alten Treffer
(Pool-Reihenfolge) erneut die 8 Plätze, neue Treffer fielen `rang_ueber_limit` heraus. Regel jetzt: nur im selben Lauf, nur Schwachstellen,
nur Treffer, die kuratiert waren, Volltext hatten und weder Fakt noch Kandidat ergaben; nur für denselben Text.

Kontrollierte Suchantworten auf HTTP-Ebene (der httpx-Client von app/web_search.py wird ersetzt): Cache-Schlüssel,
Sperrfilter (`ohne_gesperrte_quellen`), `claim_call`-Budget, Kuratierung und Extraktion laufen unverändert echt.
Alle Inhalte sind SYNTHETISCH (Testmarke Modellz) und beweisen nur die Aufzeichnung, keine Fahrzeugfakten und keine
historische Live-Ursache.

    python scripts/test_isolated.py test_sw_eskalation_alttreffer.py
"""
import asyncio
import logging
import os
import re
import socket
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="swdiag_")
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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import database as _db  # noqa: E402
_db.ensure_tables()
import app.technical_research as tr  # noqa: E402
import app.web_search as ws  # noqa: E402
from app.evidence import build_insights  # noqa: E402
from app.kaufaktionen import build_kaufaktionen  # noqa: E402
from app.models import KaufCheckRequest  # noqa: E402
from app.provider_control import provider_scope  # noqa: E402

FEHLER: list[str] = []


def check(name, ok, detail=""):
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if (detail and not ok) else ""))
    if not ok:
        FEHLER.append(name)


def t(url, titel, inhalt, raw=None):
    r = {"url": url, "title": titel, "content": inhalt}
    if raw is not None:
        r["raw_content"] = raw
    return r


IDENT = [t("https://www.adac.de/testmarke-modellz", "Testmarke Modellz (seit 2015)",
           "Die Testmarke Modellz (seit 2015): 2.0 Turbo mit 200 PS, Benzin, Frontantrieb."),
         t("https://www.auto-motor-und-sport.de/testmarke-modellz", "Testmarke Modellz im Test",
           "Testmarke Modellz (seit 2015) mit 2.0 Turbo und 200 PS, Frontantrieb.")]
SATZ_SW = "Bei der Testmarke Modellz ist die Wasserpumpe ein bekanntes Problem."
SW_ADAC = t("https://www.adac.de/testmarke-modellz-probleme", "Testmarke Modellz Schwachstellen", SATZ_SW,
            raw=("Testmarke Modellz im Überblick. " + SATZ_SW + " ") * 12)
SW_PROSPEKT = t("https://www.prospekt-archiv-x.de/testmarke-modellz", "Testmarke Modellz Prospekt",
                "Die Testmarke Modellz gibt es in vielen Farben.")
SW_MARKTPLATZ = t("https://suchen.mobile.de/fahrzeuge/testmarke-modellz", "Testmarke Modellz kaufen", "Inserat")


def esk(n, praefix):
    return [t(f"https://www.{praefix}-{i}.de/testmarke-modellz", f"Testmarke Modellz Bericht {praefix} {i}",
              f"Testmarke Modellz Bericht Nummer {i}.") for i in range(n)]


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class FakeClient:
    """Ersetzt nur den HTTP-Transport; jeder POST ist ein echter, budgetierter Tavily-Versuch im Code."""
    routing: list[tuple[str, list]] = []
    posts: list[dict] = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        q = json["query"]
        FakeClient.posts.append({"query": q, "raw": json.get("include_raw_content")})
        if "technische Daten" in q or "Generation Baureihe Bauzeitraum" in q:
            return _Resp({"results": [dict(r) for r in IDENT]})
        for teil, liste in FakeClient.routing:
            if teil in q:
                return _Resp({"results": [dict(r) for r in liste]})
        return _Resp({"results": []})


ws.httpx.AsyncClient = FakeClient
ws.TAVILY_API_KEY = "offline-test-key-not-real"


class Sammler(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.zeilen: list[str] = []

    def emit(self, record):
        self.zeilen.append(record.getMessage())


async def lauf(routing):
    ws._cache.clear()
    FakeClient.routing, FakeClient.posts = routing, []
    h = Sammler()
    lg = logging.getLogger("app.technical_research")
    alt = lg.level
    lg.setLevel(logging.INFO)
    lg.addHandler(h)
    try:
        async with provider_scope("kaufcheck", key="sw-diagnose-test") as scope:
            rec = await tr.TavilyTechnicalResearchProvider().recherchiere(
                marke="Testmarke", modell="Modellz", baujahr=2018, motor="2.0 Turbo 200 PS", ausgeloest_durch="test")
            budget = dict(scope.calls), dict(scope.skipped)
    finally:
        lg.removeHandler(h)
        lg.setLevel(alt)
    return rec, [z for z in h.zeilen if z.startswith("sw_diagnose")], list(FakeClient.posts), budget


def feld(zeile, name):
    m = re.search(rf"\b{name}=('[^']*'|\S+)", zeile)
    return m.group(1).strip("'") if m else None




IDENT = [t("https://www.adac.de/testmarke-modellz", "Testmarke Modellz (seit 2015)",
           "Die Testmarke Modellz (seit 2015): 2.0 Turbo mit 200 PS, Benzin, Frontantrieb."),
         t("https://www.auto-motor-und-sport.de/testmarke-modellz", "Testmarke Modellz im Test",
           "Testmarke Modellz (seit 2015) mit 2.0 Turbo und 200 PS, Frontantrieb.")]
SATZ_SW = "Bei der Testmarke Modellz ist die Wasserpumpe ein bekanntes Problem."


def _nebensache(i, pfad="alt"):
    """[SYNTH] Volltext zur Testmarke ohne Problemaussage -> wird ausgewertet, liefert weder Fakt noch Kandidat.
    Gleiche (qualifizierte) Domain wie der spätere geeignete Treffer -> GLEICHER Domain-Score, die Pool-Reihenfolge entscheidet."""
    txt = f"Testmarke Modellz Bericht Nummer {i}: Die Testmarke Modellz gibt es in vielen Farben und Ausstattungen. "
    return t(f"https://www.adac.de/testmarke-modellz-{pfad}-{i}", f"Testmarke Modellz Bericht {pfad} {i}", txt, raw=txt * 12)


ALT = [_nebensache(i) for i in range(8)]                                         # Runde 1: 8 Treffer, gleicher Score
GEEIGNET = t("https://www.adac.de/testmarke-modellz-probleme", "Testmarke Modellz Schwachstellen", SATZ_SW,
             raw=("Testmarke Modellz im Überblick. " + SATZ_SW + " ") * 12)      # [SYNTH] gleicher Score, in Runde 2 am Pool-Ende
NEU = [_nebensache(i, "neu") for i in range(7)] + [GEEIGNET]
ROUTING = [("bekannte Probleme Schwachstellen", ALT),
           ("Werkstattinformation technischer Hinweis", NEU),
           ("technical service bulletin", [])]


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class FakeClient:
    routing: list[tuple[str, list]] = []
    posts: list[dict] = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        q = json["query"]
        FakeClient.posts.append({"query": q, "raw": json.get("include_raw_content")})
        if "technische Daten" in q or "Generation Baureihe Bauzeitraum" in q:
            return _Resp({"results": [dict(r) for r in IDENT]})
        for teil, liste in FakeClient.routing:
            if teil in q:
                return _Resp({"results": [dict(r) for r in liste]})
        return _Resp({"results": []})


ws.httpx.AsyncClient = FakeClient
ws.TAVILY_API_KEY = "offline-test-key-not-real"


class Sammler(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.zeilen: list[str] = []

    def emit(self, record):
        self.zeilen.append(record.getMessage())


async def lauf(routing):
    ws._cache.clear()
    FakeClient.routing, FakeClient.posts = routing, []
    h = Sammler()
    lg = logging.getLogger("app.technical_research")
    alt = lg.level
    lg.setLevel(logging.INFO)
    lg.addHandler(h)
    try:
        async with provider_scope("kaufcheck", key="sw-alttreffer-test") as scope:
            rec = await tr.TavilyTechnicalResearchProvider().recherchiere(
                marke="Testmarke", modell="Modellz", baujahr=2018, motor="2.0 Turbo 200 PS", ausgeloest_durch="test")
            budget = dict(scope.calls), dict(scope.skipped)
    finally:
        lg.removeHandler(h)
        lg.setLevel(alt)
    return rec, [z for z in h.zeilen if z.startswith("sw_diagnose")], list(FakeClient.posts), budget


def feld(zeile, name):
    m = re.search(rf"\b{name}=('[^']*'|\S+)", zeile)
    return m.group(1).strip("'") if m else None


def sw_fakten(rec):
    return [f for f in rec.fakten if f.kategorie == "schwachstelle"]


print("== A: [SYNTH] Eskalationspfad nach dem Fix: zuletzt gelieferter, geeigneter Treffer rückt nach ==")
rec, z, posts, budget = asyncio.run(lauf(ROUTING))
k1 = next((x for x in z if "stufe=kuratierung" in x and "runde=1" in x), "")
k2 = next((x for x in z if "stufe=kuratierung" in x and "runde=2" in x), "")
check("A1 Runde 1 unverändert: Pool 8, 8 kuratiert, kein Fakt, kein Kandidat",
      feld(k1, "treffer") == "8" and feld(k1, "kuratiert") == "8" and feld(k1, "fakten") == "0" and feld(k1, "kandidaten") == "0", k1)
check("A2 Runde 2: die 8 erfolglos ausgewerteten Alttreffer werden nicht erneut ausgewertet (Grund sichtbar); Pool 16, "
      "ausgewertet 8 (= tatsächliche Trefferzahl, keine feste Obergrenze)",
      "bereits_ausgewertet_ohne_fakt=8" in k2 and feld(k2, "limit") == "unbegrenzt" and feld(k2, "kuratiert") == "8"
      and feld(k2, "treffer") == "16", k2)
t2 = [x for x in z if "stufe=treffer" in x and "runde=2" in x]
check("A3 alle 8 NEUEN Treffer (Herkunft r2) sind kuratiert, auch der zuletzt gelieferte",
      sum("herkunft=r2:" in x and "kuratiert=ja" in x for x in t2) == 8, str([x[-120:] for x in t2 if "herkunft=r2" in x][:2]))
geeignet_z = next((x for x in t2 if "testmarke-modellz-probleme" in x), "")
check("A4 der geeignete Treffer wird extrahiert und liefert einen regulären Fakt (Wasserpumpe)",
      "kuratiert=ja" in geeignet_z and "fakten=regulaer" in geeignet_z
      and [f.bauteil for f in sw_fakten(rec)] == ["Wasserpumpe"], geeignet_z)
check("A5 Budget unverändert gezählt: HTTP-Versuche == Tavily-Calls, nichts übersprungen",
      budget[0]["tavily"] == len(posts) and budget[1]["tavily"] == 0, f"{budget} posts={len(posts)}")

REQ = KaufCheckRequest(marke="Testmarke", modell="Modellz", baujahr=2018, kilometerstand=90000, kraftstoff="Benzin",
                       motor="2.0 Turbo 200 PS", ausstattung=[])
ins = build_insights(None, None, [], REQ, check_typ="kauf", web_recherche=rec, canonical_rueckrufe=[])
ka = build_kaufaktionen(REQ, None, None, ins)
h = Sammler()
lg = logging.getLogger("app.technical_research")
lg.setLevel(logging.INFO)
lg.addHandler(h)
tr.sw_diagnose_ausgabe(rec, ins, ka)
lg.removeHandler(h)
aus = next((x for x in h.zeilen if "stufe=ausgabe" in x), "")
check("A6 Ausgabe: Web-Schwachstellen-Hinweis mit Quelle des nachgerückten Treffers",
      (feld(aus, "insights_web_schwachstelle") or "0") != "0" and "adac.de" in (feld(aus, "quellen") or ""), aus)
akt = feld(aus, "aktionen") or ""
check("A7 Ausgabe: Besichtigungs-, Probefahrt- und Verkäuferfragen-Aktion aus diesem Hinweis (eine Dokumenten-Aktion entsteht "
      "für einen Schwachstellen-Hinweis konstruktionsbedingt nicht: `kaufaktionen` ordnet Dokumente Wartung/Rückruf zu)",
      all(k in akt for k in ("besichtigung=", "probefahrt=", "verkaeuferfragen=")), akt)

print("\n== B: Rolle des Erfolglos-Ausschlusses ohne Obergrenze: ohne ihn würden die 8 Alttreffer erneut ausgewertet ==")
_orig = tr._ohne_erfolglos_ausgewertete
tr._ohne_erfolglos_ausgewertete = lambda pool, erledigt: (pool, [])
try:
    rec_v, z_v, _p, _b = asyncio.run(lauf(ROUTING))
finally:
    tr._ohne_erfolglos_ausgewertete = _orig
k2v = next((x for x in z_v if "stufe=kuratierung" in x and "runde=2" in x), "")
check("B1 [SYNTH] ohne Erfolglos-Ausschluss: Runde 2 wertet alle 16 aus (die 8 Alttreffer erneut); geeigneter Treffer ist dabei",
      feld(k2v, "kuratiert") == "16" and [f.bauteil for f in sw_fakten(rec_v)] == ["Wasserpumpe"], k2v)

print("\n== C: Zustandsregel im Detail (Hilfsfunktionen, [SYNTH]) ==")


def treffer(url, ot="volltext", text="x" * 50):
    return {"url": url, "title": url, "content": text, "_ot": ot}


class _Q:
    def __init__(self, url):
        self.url = url


class _F:
    def __init__(self, url, kat="schwachstelle"):
        self.kategorie, self.quellen = kat, [_Q(url)]


a, b, c, d, e = (treffer(f"https://www.{n}.de/x") for n in "abcde")
c["_ot"] = "snippet"                                                  # kein Volltext (Abruf ohne verwertbaren Text)
stats = {"kuratiert_ids": [id(a), id(b), id(c), id(d)]}               # e war NICHT kuratiert (nie ausgewertet)
erledigt = tr._sw_erfolglos_ausgewertet([a, b, c, d, e], stats, [_F("https://www.b.de/x")],
                                        [{"kategorie": "schwachstelle", "url": "https://www.d.de/x",
                                          "grund": "x_multi_source_pending_upgrade"}])
check("C1 nur kuratiert + Volltext + ohne Fakt + ohne Kandidat gilt als erfolglos ausgewertet (hier: nur a)",
      set(erledigt) == {"a.de/x"}, str(set(erledigt)))
check("C2 Treffer mit Fakt (b) und mit Kandidat (d) bleiben zulässig", "b.de/x" not in erledigt and "d.de/x" not in erledigt)
check("C3 Snippet-/Abruffehler-Fall (c) gilt nicht als fachlich negatives Ergebnis", "c.de/x" not in erledigt)
check("C4 nicht ausgewählter Treffer (e) wird nicht irrtümlich als ausgewertet behandelt", "e.de/x" not in erledigt)
beh, vorab = tr._ohne_erfolglos_ausgewertete([a, b, c, d, e], erledigt)
check("C5 Vorab-Ausschluss trifft genau a", [r["url"] for r in vorab] == ["https://www.a.de/x"] and len(beh) == 4)
a_neu = dict(a, content="x" * 50 + " später ergänzter Volltext")
beh2, vorab2 = tr._ohne_erfolglos_ausgewertete([a_neu], erledigt)
check("C6 geänderter Text derselben URL (später ergänzter Volltext) ist wieder zulässig", not vorab2 and len(beh2) == 1)
check("C7 leerer Zustand ändert nichts", tr._ohne_erfolglos_ausgewertete([a, b], {}) == ([a, b], []))

print("\n== D: Isolation -- Status wirkt nicht auf andere Läufe; Wartung unverändert ==")
rec2, z2, _p2, _b2 = asyncio.run(lauf(ROUTING))
check("D1 zweiter, getrennter Lauf: identisches Ergebnis, kein Status aus dem ersten Lauf",
      [f.bauteil for f in sw_fakten(rec2)] == [f.bauteil for f in sw_fakten(rec)]
      and sum("bereits_ausgewertet_ohne_fakt=8" in x for x in z2 if "stufe=kuratierung" in x and "runde=2" in x) == 1)
QUELLE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "technical_research.py"), encoding="utf-8").read()
check("D2 Ausschluss ist an die Kategorie gebunden: nur kat == 'schwachstelle' ruft die Regel auf",
      'if kat == "schwachstelle" else (roh[kat], [])' in QUELLE)

print("\n== E: [SYNTH] mehr als acht zulässige Treffer -- Runde 1 und Eskalationsrunde ==")
GEEIGNET_E = t("https://www.adac.de/testmarke-modellz-probleme-e", "Testmarke Modellz Schwachstellen E", SATZ_SW,
               raw=("Testmarke Modellz im Überblick. " + SATZ_SW + " ") * 12)
rec_e1, z_e1, posts_e1, budget_e1 = asyncio.run(lauf([
    ("bekannte Probleme Schwachstellen", [_nebensache(i, "r1") for i in range(11)] + [GEEIGNET_E]),
    ("Gebrauchtwagen Test", [])]))
k_e1 = next((x for x in z_e1 if "stufe=kuratierung" in x and "runde=1" in x), "")
check("E1 Runde 1: 12 zulässige Treffer, alle 12 ausgewertet (limit=unbegrenzt), nichts 'rang_ueber_limit'",
      feld(k_e1, "treffer") == "12" and feld(k_e1, "kuratiert") == "12" and feld(k_e1, "limit") == "unbegrenzt"
      and "rang_ueber_limit" not in k_e1, k_e1)
check("E2 der geeignete Treffer an 12. Stelle erreicht den Fakt; keine Eskalationsrunde nötig",
      [f.bauteil for f in sw_fakten(rec_e1)] == ["Wasserpumpe"] and not any("runde=2" in x for x in z_e1))
ins_e = build_insights(None, None, [], REQ, check_typ="kauf", web_recherche=rec_e1, canonical_rueckrufe=[])
ka_e = build_kaufaktionen(REQ, None, None, ins_e)
h = Sammler()
lg.addHandler(h)
tr.sw_diagnose_ausgabe(rec_e1, ins_e, ka_e)
lg.removeHandler(h)
aus_e = next((x for x in h.zeilen if "stufe=ausgabe" in x), "")
check("E3 Ausgabe: Insight, Besichtigungs-/Probefahrt-Aktion und Verkäuferfrage aus dem Treffer hinter Position 8",
      feld(aus_e, "insights_web_schwachstelle") == "1"
      and all(k in (feld(aus_e, "aktionen") or "") for k in ("besichtigung=", "probefahrt=", "verkaeuferfragen=")), aus_e)

NEU_E = [_nebensache(i, "e2") for i in range(12)] + [GEEIGNET_E]
rec_e2, z_e2, posts_e2, budget_e2 = asyncio.run(lauf([
    ("bekannte Probleme Schwachstellen", ALT),
    ("Werkstattinformation technischer Hinweis", NEU_E[:7]),
    ("technical service bulletin", NEU_E[7:])]))
k_e2 = next((x for x in z_e2 if "stufe=kuratierung" in x and "runde=2" in x), "")
check("E4 Eskalationsrunde: 13 neue Treffer, alle 13 ausgewertet (mehr als 8), 8 Alttreffer vorab ausgeschlossen",
      feld(k_e2, "treffer") == "21" and feld(k_e2, "kuratiert") == "13" and "bereits_ausgewertet_ohne_fakt=8" in k_e2, k_e2)
check("E5 geeigneter Treffer an 13. Stelle der Eskalationsrunde erreicht den Fakt",
      [f.bauteil for f in sw_fakten(rec_e2)] == ["Wasserpumpe"])

print("\n== F: Rückruf und Wartung behalten ihre Grenze von 8; Schwachstellen ohne Grenze ==")
from app.models import WebVehicleIdentity  # noqa: E402
roh_f = {k: [_nebensache(i, k) for i in range(12)] for k in ("rueckruf", "schwachstelle", "wartung")}
stats_f: dict = {}
tr.phase_fakten(roh_f, tr._ziel("Testmarke", "Modellz", 2018, "2.0 Turbo 200 PS", None), WebVehicleIdentity(belegt=True),
                ("rueckruf", "schwachstelle", "wartung"), [], stats=stats_f)
check("F1 gleiche 12 Treffer je Kategorie: Rückruf 8, Wartung 8, Schwachstellen 12 ausgewertet",
      (stats_f["rueckruf"]["curated"], stats_f["wartung"]["curated"], stats_f["schwachstelle"]["curated"]) == (8, 8, 12)
      and stats_f["rueckruf"]["kuratierungs_limit"] == 8 and stats_f["wartung"]["kuratierungs_limit"] == 8
      and stats_f["schwachstelle"]["kuratierungs_limit"] is None, str({k: (v["curated"], v["kuratierungs_limit"]) for k, v in stats_f.items()}))

print("\n== G: Ausgabegrenze von höchstens fünf Schwachstellen-Fakten bleibt wirksam ==")
BAUTEILE = ["Wasserpumpe", "Turbolader", "Kupplung", "Steuerkette", "Zahnriemen", "Zylinderkopfdichtung", "Getriebe"]
VIELE = [t(f"https://www.adac.de/testmarke-modellz-{b.lower()}", f"Testmarke Modellz {b}",
           f"Bei der Testmarke Modellz ist die {b} ein bekanntes Problem.",
           raw=(f"Testmarke Modellz im Überblick. Bei der Testmarke Modellz ist die {b} ein bekanntes Problem. ") * 12)
         for b in BAUTEILE]
rec_g, z_g, _pg, _bg = asyncio.run(lauf([("bekannte Probleme Schwachstellen", VIELE), ("Gebrauchtwagen Test", [])]))
check("G1 7 zulässige Treffer mit je eigenem Bauteil -> alle ausgewertet, aber höchstens MAX_FAKTEN_JE_KATEGORIE Fakten",
      feld(next((x for x in z_g if "stufe=kuratierung" in x and "runde=1" in x), ""), "kuratiert") == "7"
      and 0 < len(sw_fakten(rec_g)) <= tr.MAX_FAKTEN_JE_KATEGORIE, str([f.bauteil for f in sw_fakten(rec_g)]))

print("\n== H: Budget und Runden ==")
check("H1 gleiche fachliche Eskalationsentscheidung (A mit/ohne Erfolglos-Ausschluss): identische Suchcalls",
      len(posts) == len(_p) and budget[0] == _b[0] and budget[1] == _b[1], f"{len(posts)} vs {len(_p)}; {budget} vs {_b}")
runden_e2 = {int(feld(x, "runde")) for x in z_e2 if feld(x, "runde")}
check("H2 Budget- und Rundengrenzen bei neuen Kandidaten/Treffern eingehalten: Calls == HTTP-Versuche, nichts übersprungen, "
      "Runden <= _MAX_ADAPTIVE_RUNDEN",
      budget_e2[0]["tavily"] == len(posts_e2) and budget_e2[1]["tavily"] == 0 and max(runden_e2) <= tr._MAX_ADAPTIVE_RUNDEN,
      f"{budget_e2} posts={len(posts_e2)} runden={runden_e2}")

print("\n" + ("ALLE GRÜN" if not FEHLER else f"{len(FEHLER)} FEHLER: {FEHLER}"))
sys.exit(1 if FEHLER else 0)
