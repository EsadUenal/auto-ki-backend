"""
SW-Diagnose -- Schwachstellen-Recherchekette je Lauf und Runde im normalen Produktions-Log (INFO, `sw_diagnose`).

Kontrollierte Suchantworten auf HTTP-Ebene (der httpx-Client von app/web_search.py wird ersetzt): Cache-Schlüssel,
Sperrfilter (`ohne_gesperrte_quellen`), `claim_call`-Budget, Kuratierung und Extraktion laufen unverändert echt.
Alle Inhalte sind SYNTHETISCH (Testmarke Modellz) und beweisen nur die Aufzeichnung, keine Fahrzeugfakten und keine
historische Live-Ursache.

    python scripts/test_isolated.py test_sw_diagnose_kette.py
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


# ══ A: Runde 1 -- Treffer gefunden, Sperrfilter, Duplikat, verworfener Treffer ═══════════════════════════════════
print("== A: Runde 1 (geeigneter Treffer, Sperrfilter, Duplikat, Ablehnung) ==")
rec_a, z_a, posts_a, budget_a = asyncio.run(lauf([
    ("bekannte Probleme Schwachstellen", [SW_ADAC, SW_PROSPEKT, SW_MARKTPLATZ]),
    ("Gebrauchtwagen Test", [SW_ADAC]),
]))
laeufe = {feld(z, "lauf") for z in z_a}
check("A1 alle sw_diagnose-Zeilen tragen DIESELBE Laufkennung", len(laeufe) == 1 and None not in laeufe, str(laeufe))
suche = [z for z in z_a if "stufe=suche" in z]
tw = next((z for z in suche if "intent=technical_weakness" in z), "")
ur = next((z for z in suche if "intent=used_car_review" in z), "")
check("A2 Suchwortlaut je Intent steht im Log (tatsächlich gesendete Anfrage)",
      feld(tw, "query") in [p["query"] for p in posts_a] and "bekannte Probleme Schwachstellen" in tw
      and "Gebrauchtwagen Test TÜV Kaufberatung" in ur, f"{tw} | {ur}")
check("A3 Trefferzahl vor und nach dem bestehenden Sperrfilter: 3 vom Provider, 2 nach Filter (Marktplatz entfernt)",
      feld(tw, "treffer_provider") == "3" and feld(tw, "treffer_nach_sperrfilter") == "2"
      and feld(tw, "treffer_erhalten") == "2" and feld(tw, "runde") == "1", tw)
kur = next((z for z in z_a if "stufe=kuratierung" in z and "runde=1" in z), "")
check("A4 Kuratierung: 3 Treffer im Pool, 2 ausgewählt, keine feste Obergrenze (limit=unbegrenzt), Duplikat als Grund",
      feld(kur, "treffer") == "3" and feld(kur, "kuratiert") == "2" and feld(kur, "limit") == "unbegrenzt"
      and "url_duplikat=1" in kur and feld(kur, "fakten") == "1", kur)
tz = [z for z in z_a if "stufe=treffer" in z and "runde=1" in z]
adac = [z for z in tz if "adac.de/testmarke-modellz-probleme" in z]
check("A5 geeigneter Treffer: ausgewählt, Treffer-Ebene bestanden, Volltext, ein regulärer Fakt -- und die Dublette "
      "derselben URL getrennt als 'nicht ausgewählt: url_duplikat'",
      len(adac) == 2 and "kuratiert=ja" in adac[0] and "treffer_ebene=bestanden" in adac[0] and "text=volltext" in adac[0]
      and "fakten=regulaer" in adac[0] and "kuratiert=nein:url_duplikat" in adac[1] and "fakten=0" in adac[1], str(adac))
pros = next((z for z in tz if "prospekt-archiv-x.de" in z), "")
check("A6 verworfener Treffer: ausgewählt, aber mit dem TATSÄCHLICHEN Ablehnungsgrund der Extraktion, kein Fakt; "
      "Snippet-Status mit Grund",
      "kuratiert=ja" in pros and feld(pros, "treffer_ebene") == "source_quality" and "fakten=0" in pros
      and "text=snippet(fehlt)" in pros, pros)
check("A7 kein Seitentext und kein Schlüssel in den Diagnosezeilen (nur URL ohne Query, Zahlen, Gründe)",
      not any(SATZ_SW[:30] in z or "offline-test-key" in z or "Überblick" in z for z in z_a))
check("A8 keine Eskalationsrunde, wenn Runde 1 einen Fakt brachte (Diagnose ändert die Steuerung nicht)",
      not any("runde=2" in z for z in z_a) and [f.bauteil for f in rec_a.fakten if f.kategorie == "schwachstelle"]
      == ["Wasserpumpe"])
check("A9 Budget: jeder HTTP-Versuch ist genau ein gezählter Tavily-Call, nichts budgetbedingt übersprungen",
      budget_a[0]["tavily"] == len(posts_a) and budget_a[1]["tavily"] == 0 and rec_a.anfragen == len(posts_a),
      f"{budget_a} posts={len(posts_a)} anfragen={rec_a.anfragen}")
check("A10 Laufkennung ist nur Log-Korrelation: nicht in der serialisierten Recherche",
      "diagnose_lauf" not in rec_a.model_dump() and rec_a.diagnose_lauf == next(iter(laeufe)))

# Ausgabe-Stufe (KaufCheck-Seite): Insights und Aktionen derselben Kette, gleiche Laufkennung
REQ = KaufCheckRequest(marke="Testmarke", modell="Modellz", baujahr=2018, kilometerstand=90000, kraftstoff="Benzin",
                       motor="2.0 Turbo 200 PS", ausstattung=[])
ins_a = build_insights(None, None, [], REQ, check_typ="kauf", web_recherche=rec_a, canonical_rueckrufe=[])
ka_a = build_kaufaktionen(REQ, None, None, ins_a)
h = Sammler()
lg = logging.getLogger("app.technical_research")
lg.setLevel(logging.INFO)
lg.addHandler(h)
tr.sw_diagnose_ausgabe(rec_a, ins_a, ka_a)
lg.removeHandler(h)
aus = next((z for z in h.zeilen if "stufe=ausgabe" in z), "")
check("A11 Ausgabe-Stufe: Web-Schwachstellen-Insights, Quelle und Aktionen mit derselben Laufkennung",
      feld(aus, "lauf") == rec_a.diagnose_lauf and feld(aus, "insights_web_schwachstelle") == "1"
      and "adac.de" in (feld(aus, "quellen") or "") and feld(aus, "aktionen") not in (None, "-"), aus)

# ══ B: Eskalation -- Runde 1 ohne Kandidat, Runde 2 mit 16 Treffern und Kuratierungsgrenze ═════════════════════════
print("\n== B: Runde 1 ohne Kandidat -> Eskalation Runde 2 (Kuratierungsgrenze, Herkunft je Runde) ==")
rec_b, z_b, posts_b, budget_b = asyncio.run(lauf([
    ("bekannte Probleme Schwachstellen", [SW_PROSPEKT]),
    ("Werkstattinformation technischer Hinweis", esk(8, "werkstatt")),
    ("technical service bulletin", esk(8, "bulletin")),
]))
check("B1 eine Laufkennung über beide Runden", len({feld(z, "lauf") for z in z_b}) == 1)
s2 = [z for z in z_b if "stufe=suche" in z and "runde=2" in z]
check("B2 Runde 2: beide Eskalationsanfragen mit Intent und tatsächlichem Wortlaut",
      {feld(z, "intent") for z in s2} == {"source_upgrade_official", "source_upgrade_en"}
      and all(feld(z, "query") in [p["query"] for p in posts_b] for z in s2)
      and all(feld(z, "treffer_provider") == "8" for z in s2), str(s2))
k2 = next((z for z in z_b if "stufe=kuratierung" in z and "runde=2" in z), "")
check("B3 Runde 2: Pool 17 (1 aus Runde 1 + 16 neue), ALLE 17 ausgewertet -- keine feste Obergrenze mehr (limit=unbegrenzt)",
      feld(k2, "treffer") == "17" and feld(k2, "kuratiert") == "17" and "rang_ueber_limit" not in k2
      and feld(k2, "limit") == "unbegrenzt", k2)
t2 = [z for z in z_b if "stufe=treffer" in z and "runde=2" in z]
check("B4 jeder Treffer trägt seine Herkunft (Runde und Intent) -- Runde-1-Treffer im Pool der Runde 2 bleibt r1",
      any("herkunft=r1:technical_weakness" in z for z in t2)
      and sum("herkunft=r2:source_upgrade_official" in z for z in t2) == 8
      and sum("herkunft=r2:source_upgrade_en" in z for z in t2) == 8, str(t2[:3]))
check("B5 kein Treffer fällt mehr 'rang_ueber_limit' heraus; jeder ausgewertete Treffer trägt eine Treffer-Bewertung",
      not any("rang_ueber_limit" in z for z in t2) and sum("kuratiert=ja" in z for z in t2) == 17)
check("B6 Budget unverändert gezählt: HTTP-Versuche == Tavily-Calls, nichts übersprungen",
      budget_b[0]["tavily"] == len(posts_b) and budget_b[1]["tavily"] == 0, f"{budget_b} posts={len(posts_b)}")

print("\n" + ("ALLE GRÜN" if not FEHLER else f"{len(FEHLER)} FEHLER: {FEHLER}"))
sys.exit(1 if FEHLER else 0)
