"""
Etappe-1 Failure-Mode-Abnahme — deterministisch, KEIN Netzwerk, KEIN Tavily-Budget.

Beweist, dass die Marktanalyse-Pipeline (app/marktrecherche.py,
app/marktvergleich.py, app/web_search.py) bei Ausfall, leerer Quelle, kaputtem
raw_content, Teilfehlern, Source-Policy-Verstößen und schlechteren spaeteren
Suchstufen KONTROLLIERT und EHRLICH reagiert:

  - nie ein erfundener Marktpreis
  - nie ein Absturz bis zum Nutzer
  - ein einmal erreichter guter Stand (best_so_far) geht nie verloren
  - eine nicht freigegebene Quelle (mobile.de/autoscout24.de) beeinflusst nie
    Median, Spanne oder Marktabdeckung

Techniken (§1 der Aufgabenstellung): Stub/Monkeypatch/Fixture statt echter
Tavily-Aufrufe. Section A ist die einzige Ausnahme, die bewusst tiefer ansetzt:
sie faked NUR den httpx-Client und laesst den echten Such-Code
(app.web_search._tavily_search_intern) laufen, um die tatsaechliche
Exception-/Retry-Behandlung zu pruefen statt deren Vertrag anzunehmen.

    python test_failure_modes.py
"""
import os
import sys
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_fail_"), "test.db")
sys.path.insert(0, ".")

# §Source-Policy: Der Production-Default gibt KEINE Marktquelle zum Preisbilden
# frei (app/config.ALLOWED_MARKET_SOURCES ist leer). Dieser Test prueft die
# ANALYSE-ENGINE und braucht dafuer die historischen/synthetischen Testdomains —
# die Freigabe gilt ausschliesslich in diesem Testprozess und ist KEINE
# produktive Qualifikation der Quelle. Siehe _source_policy_testharness.py.
import _source_policy_testharness  # noqa: E402,F401

import asyncio                                                           # noqa: E402
import json                                                              # noqa: E402
from types import SimpleNamespace                                        # noqa: E402

import httpx                                                             # noqa: E402

import app.marktrecherche as mr                                          # noqa: E402
import app.web_search as ws                                              # noqa: E402
from app.marktrecherche import QueryStufe, vertiefe_marktrecherche       # noqa: E402
from app.marktvergleich import (                                         # noqa: E402
    _bewerte, _eindeutige_karosserie, _extrahiere_aus_text, analysiere_markt,
    baue_ziel,
)
from app.web_search import darf_preisbildend_sein                        # noqa: E402

_fails: list[str] = []


def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        _fails.append(name)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ══════════════════════════════════════════════════════════════════════════
# Gemeinsame Fixtures: BMW-320d-G20-Ziel + Kartenerzeuger
#
# Synthetische Baureihe/Motor (wie test_motor_evidence_trust.py) statt echter DB —
# der Test soll von der DB-Isolation (Temp-DB via AUTO_KI_DB_PATH) unabhaengig
# bleiben und ausschliesslich die Pipeline pruefen, nicht den DB-Inhalt.
# ══════════════════════════════════════════════════════════════════════════
_BR = {"id": "bmw-3er-g20-g21", "marke": "BMW", "modell": "3er", "generation": "G20/G21"}
_MM = {"bezeichnung": "320d", "motorcode": "B47D20", "kraftstoff": "Diesel",
      "leistung_ps": 190}
_REQ = SimpleNamespace(marke="BMW", modell="320d G20", baujahr=2019,
                       kilometerstand=120_000, motor="320d 190 PS",
                       kraftstoff="Diesel", getriebe="Automatik", preis_eur=24_900)
ZIEL = baue_ziel(_BR, _MM, _REQ, [_BR], [
    {"baureihe_id": "bmw-3er-g20-g21", "bezeichnung": "320d", "motorcode": "B47D20"},
    {"baureihe_id": "bmw-3er-g20-g21", "bezeichnung": "330d", "motorcode": "B57D30"}])


def karte(titel, lid, preis, km, ez, beschr="", domain_slug="x"):
    bild = f"https://img.kleinanzeigen.de/api/v1/prod-ads/images/aa/{lid}-uuid"
    return (f"* [![{titel} Vorschau]({bild})\n\n"
            f"  20](/s-anzeige/{domain_slug}/{lid}-216-1111)\n\n  12307 Berlin\n\n  Heute\n\n"
            f"  ## [{titel}](/s-anzeige/{domain_slug}/{lid}-216-1111)\n\n  {beschr}\n\n"
            f"  {preis} €\n\n  {km} km   EZ {ez}\n")


def bmw_karten(n, id_start=100000, preis_start=24_500, km_start=118_000):
    """n saubere, strukturell trennbare BMW-320d-G20-Karten (Kleinanzeigen-Form)."""
    out = ""
    for i in range(n):
        out += karte(f"BMW 320d G20 Advantage #{i}", str(id_start + i),
                     str(preis_start + i * 200), str(km_start + i * 500),
                     "05/2019", "BMW 320d G20 Limousine, Diesel, Scheckheft")
    return out


def seite(url, titel, karten_text, raw_content_override=None):
    return {"url": url, "title": titel, "content": "",
            "raw_content": (raw_content_override if raw_content_override is not None
                            else "## Ergebnisse\n\n" + karten_text)}


def stufen(n, praefix="stufe"):
    return [QueryStufe(query=f"BMW 320d G20 dummy {i}", include_domains=None,
                       label=f"{praefix}{i}", felder=[]) for i in range(1, n + 1)]


def stub_sequence(*antworten):
    """Liefert einen Ersatz fuer tavily_search_mit_status: bei jedem Aufruf die
    naechste vorgegebene (results, fehler)-Antwort, danach wiederholt die letzte."""
    calls = {"n": 0}

    async def _stub(query, count=10, include_domains=None, exclude_domains=None,
                    include_raw_content=False, bypass_cache=False, search_depth="basic"):
        i = calls["n"]
        calls["n"] += 1
        return antworten[i] if i < len(antworten) else antworten[-1]
    return _stub, calls


# ══════════════════════════════════════════════════════════════════════════
# A) Tavily-Ausfall AUF HTTP-EBENE — echter Produktionscode, nur httpx gefaked
# ══════════════════════════════════════════════════════════════════════════
class _FakeAsyncClient:
    """Ersetzt httpx.AsyncClient. `verhalten(call_index)` liefert entweder eine
    Exception (wird geworfen) oder eine (status_code, json_data_or_None)-Tupel."""

    _verhalten = None
    _calls = {"n": 0}

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        i = _FakeAsyncClient._calls["n"]
        _FakeAsyncClient._calls["n"] += 1
        spec = _FakeAsyncClient._verhalten(i)
        if isinstance(spec, BaseException):
            raise spec
        status, data = spec
        return _FakeResponse(status, data)


class _FakeResponse:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("POST", "https://api.tavily.com/search")
            resp = httpx.Response(self.status_code, request=req, content=b"error")
            raise httpx.HTTPStatusError(f"status {self.status_code}", request=req, response=resp)

    def json(self):
        if self._data is None:
            raise ValueError("kaputtes JSON")
        return self._data

    @property
    def text(self):
        return "error body"


_orig_asyncclient = ws.httpx.AsyncClient
_orig_key = ws.TAVILY_API_KEY
_orig_sleep = ws.asyncio.sleep


def _mit_fake_http(verhalten, retries_beschleunigen=True):
    """Kontextmanager-Ersatz: patcht httpx.AsyncClient + TAVILY_API_KEY fuer einen
    Aufruf und stellt danach den Originalzustand wieder her."""
    ws.httpx.AsyncClient = _FakeAsyncClient
    ws.TAVILY_API_KEY = "dummy-test-key"
    _FakeAsyncClient._verhalten = verhalten
    _FakeAsyncClient._calls = {"n": 0}
    if retries_beschleunigen:
        async def _schneller_sleep(_):
            return None
        ws.asyncio.sleep = _schneller_sleep


def _fake_http_zuruecksetzen():
    ws.httpx.AsyncClient = _orig_asyncclient
    ws.TAVILY_API_KEY = _orig_key
    ws.asyncio.sleep = _orig_sleep


# A1) generische Exception (z.B. Verbindungsfehler)
_mit_fake_http(lambda i: ConnectionError("Verbindung verweigert"))
_res_a1, _fehler_a1 = run(ws.tavily_search_mit_status("BMW 320d", count=5))
_fake_http_zuruecksetzen()
check("A1: generische Exception -> results=[] , fehler=True (kein Crash)",
      _res_a1 == [] and _fehler_a1 is True)

# A2) Timeout
_mit_fake_http(lambda i: httpx.TimeoutException("Timeout"))
_res_a2, _fehler_a2 = run(ws.tavily_search_mit_status("BMW 320d", count=5))
_fake_http_zuruecksetzen()
check("A2: Timeout -> results=[] , fehler=True (kein Crash)",
      _res_a2 == [] and _fehler_a2 is True)

# A3) Rate-Limit/Quota (429) — muss die Retries ausschoepfen, dann sauber scheitern.
_mit_fake_http(lambda i: (429, None))
_res_a3, _fehler_a3 = run(ws.tavily_search_mit_status("BMW 320d", count=5))
_versuche_a3 = _FakeAsyncClient._calls["n"]
_fake_http_zuruecksetzen()
check("A3: 429 -> results=[] , fehler=True nach Ausschoepfen der Retries",
      _res_a3 == [] and _fehler_a3 is True)
check("A3b: 429 wurde tatsaechlich mehrfach versucht (Retry-Pfad genutzt)",
      _versuche_a3 >= 2)

# A4) Ungueltige/kaputte Response (kein valides JSON)
_mit_fake_http(lambda i: (200, None))  # data=None -> resp.json() wirft
_res_a4, _fehler_a4 = run(ws.tavily_search_mit_status("BMW 320d", count=5))
_fake_http_zuruecksetzen()
check("A4: kaputtes JSON -> results=[] , fehler=True (kein Crash)",
      _res_a4 == [] and _fehler_a4 is True)

# A5: Gegenprobe — ein erfolgreicher Call bleibt unveraendert funktionsfaehig.
_mit_fake_http(lambda i: (200, {"results": [{"url": "https://kleinanzeigen.de/s-anzeige/x/1-1", "title": "t"}]}))
_res_a5, _fehler_a5 = run(ws.tavily_search_mit_status("BMW 320d", count=5))
_fake_http_zuruecksetzen()
check("A5: Gegenprobe — Erfolg bleibt Erfolg (fehler=False, 1 Treffer)",
      _fehler_a5 is False and len(_res_a5) == 1)


# ══════════════════════════════════════════════════════════════════════════
# B) vertiefe_marktrecherche unter kontrollierten Stufenfolgen (gestubbt)
# ══════════════════════════════════════════════════════════════════════════
_orig_such = mr.tavily_search_mit_status


def _mit_stub(stub):
    mr.tavily_search_mit_status = stub


def _stub_zuruecksetzen():
    mr.tavily_search_mit_status = _orig_such


# B1) Kompletter technischer Ausfall ueber ALLE Stufen.
_stub, _ = stub_sequence(([], True))
_mit_stub(_stub)
_, ma_b1, diag_b1 = run(vertiefe_marktrecherche(
    [], stufen(5), ZIEL, 24_900, [], count=5, zweck="b1", max_stufen=5))
_stub_zuruecksetzen()
check("B1: technischer Totalausfall -> kein Crash", True)
check("B1: research_status = research_failed",
      diag_b1["research_status"] == "research_failed")
check("B1: Grund = technical_failure",
      diag_b1["research_failure_grund"] == "technical_failure")
check("B1: kein Median, keine Spanne",
      ma_b1.median_eur is None and ma_b1.spanne_min_eur is None)
check("B1: verwendet = 0", ma_b1.verwendet == 0)

# B2) Leere, aber technisch fehlerfreie Suche (kein API-Fehler, einfach nichts).
_stub, _ = stub_sequence(([], False))
_mit_stub(_stub)
_, ma_b2, diag_b2 = run(vertiefe_marktrecherche(
    [], stufen(5), ZIEL, 24_900, [], count=5, zweck="b2", max_stufen=5))
_stub_zuruecksetzen()
check("B2: leere Ergebnisse -> research_failed",
      diag_b2["research_status"] == "research_failed")
check("B2: Grund = data_exhausted (kein technischer Fehler)",
      diag_b2["research_failure_grund"] == "data_exhausted")
check("B2: verwendet=0, median=None, spanne=None",
      ma_b2.verwendet == 0 and ma_b2.median_eur is None and ma_b2.spanne_min_eur is None)

# B3) raw_content leer/None ueber mehrere Treffer — Snippet-Pfad darf normal
# arbeiten, aber keine strukturelle Evidence erfinden.
_leer_content_treffer = [
    seite("https://www.kleinanzeigen.de/s-anzeige/bmw-320d-x/9999001-216-1",
          "BMW 320d G20 gebraucht kaufen", "", raw_content_override=""),
    seite("https://www.kleinanzeigen.de/s-anzeige/bmw-320d-y/9999002-216-1",
          "BMW 320d G20 gebraucht kaufen", "", raw_content_override=None),
]
_stub, _ = stub_sequence((_leer_content_treffer, False))
_mit_stub(_stub)
_, ma_b3, diag_b3 = run(vertiefe_marktrecherche(
    [], stufen(3), ZIEL, 24_900, [], count=5, zweck="b3", max_stufen=3))
_stub_zuruecksetzen()
check("B3: leerer/None raw_content -> kein Crash", True)
check("B3: keine erfundene Evidence -> verwendet=0",
      ma_b3.verwendet == 0)
check("B3: research_failed statt Scheinergebnis",
      diag_b3["research_status"] == "research_failed")

# B4) best_so_far MUSS erhalten bleiben, wenn spaetere Stufen schlechter/leer/
# kaputt sind.
_stufe1 = [seite("https://www.kleinanzeigen.de/s-autos/dünn", "BMW 320d dünn",
                  bmw_karten(2, id_start=200001))]
_stufe2 = [seite("https://www.kleinanzeigen.de/s-autos/gut", "BMW 320d gut",
                  bmw_karten(6, id_start=200100))]
_stufe3 = [seite("https://www.kleinanzeigen.de/s-autos/schlecht", "BMW 320d schlecht",
                  bmw_karten(2, id_start=200200, preis_start=9_999_999))]  # unplausibel
_stufe4 = []
_stufe5_fehler = ([], True)
_stub, calls_b4 = stub_sequence(
    (_stufe1, False), (_stufe2, False), (_stufe3, False), (_stufe4, False), _stufe5_fehler)
_mit_stub(_stub)
_, ma_b4, diag_b4 = run(vertiefe_marktrecherche(
    [], stufen(5), ZIEL, 24_900, [], count=10, zweck="b4", max_stufen=5))
_stub_zuruecksetzen()
_verlauf_b4 = diag_b4["best_so_far"]
_uebernommen_b4 = [v["stufe"] for v in _verlauf_b4 if v["uebernommen"]]
check("B4: Stufe 2 (6 gute) wurde als bester Stand uebernommen",
      "2" in _uebernommen_b4 or "stufe2" in _uebernommen_b4)
check("B4: bester_stand_stufe zeigt auf Stufe 2",
      "2" in str(diag_b4["bester_stand_stufe"]))
check("B4: finale Datenqualitaet stammt NICHT von der kaputten Stufe 3/4/5",
      ma_b4.verwendet >= 5)
check("B4: kein Absturz durch Stufe 5 (technischer Fehler mittendrin)", True)
_vorher_b4_stufe2 = [v for v in _verlauf_b4 if v["stufe"] in ("2", "stufe2")]
if _vorher_b4_stufe2:
    check("B4: Stufe 2 Rang == finaler Rang (nichts ging nach Stufe 2 verloren)",
          list(_vorher_b4_stufe2[0]["rang"]) == list(mr.bewertungsrang(ma_b4)))

# B5) best_so_far MUSS sich verbessern, wenn spaetere Stufen objektiv besser sind.
_s1 = [seite("https://www.kleinanzeigen.de/s-autos/duenn2", "BMW 320d",
             bmw_karten(3, id_start=300001))]
_s2 = [seite("https://www.kleinanzeigen.de/s-autos/mittel2", "BMW 320d",
             bmw_karten(5, id_start=300100))]
_s3 = [seite("https://www.kleinanzeigen.de/s-autos/gut2", "BMW 320d",
             bmw_karten(8, id_start=300200))]
_stub, _ = stub_sequence((_s1, False), (_s2, False), (_s3, False))
_mit_stub(_stub)
_, ma_b5, diag_b5 = run(vertiefe_marktrecherche(
    [], stufen(3), ZIEL, 24_900, [], count=10, zweck="b5", max_stufen=3))
_stub_zuruecksetzen()
_raenge_b5 = [tuple(v["rang"]) for v in diag_b5["best_so_far"]]
check("B5: der Rang steigt (oder bleibt gleich) mit jeder Stufe — kein Rueckschritt",
      all(_raenge_b5[i] <= _raenge_b5[i + 1] for i in range(len(_raenge_b5) - 1))
      or max(_raenge_b5) == _raenge_b5[-1])
check("B5: finaler verwendet-Wert stammt aus der besten (letzten) Stufe",
      ma_b5.verwendet >= 5)

# B6) Niemals ein guter Stand -> ehrliches research_failed, KEIN Fallback-Preis.
_stub, _ = stub_sequence(([], False), ([], True), ([], False))
_mit_stub(_stub)
_, ma_b6, diag_b6 = run(vertiefe_marktrecherche(
    [], stufen(3), ZIEL, 24_900, [], count=10, zweck="b6", max_stufen=3))
_stub_zuruecksetzen()
check("B6: nie ein guter Stand -> research_failed",
      diag_b6["research_status"] == "research_failed")
check("B6: kein Fallback auf das Angebot (24.900) als Median",
      ma_b6.median_eur is None)
check("B6: keine Kontextbeobachtung wird als Median missbraucht",
      ma_b6.median_eur is None and ma_b6.verwendet == 0)


# ══════════════════════════════════════════════════════════════════════════
# C) Extract-Ausfall (Tavily-Extract-API) — Such-Treffer ohne raw_content
# ══════════════════════════════════════════════════════════════════════════
_orig_extract = ws.tavily_extract


async def _extract_alle_fehler(urls, *, advanced=False):
    raise RuntimeError("Extract-Dienst nicht erreichbar")


_C2_ZAEHLER = {"n": 0}


async def _extract_teilausfall(urls, *, advanced=False):
    # §_MIN_RAW_CONTENT_LEN (app/web_search.py, 400 Zeichen): der Erfolgsinhalt
    # muss diese Schwelle ueberschreiten, sonst zaehlt ihn hole_raw_content
    # selbst korrekt als Fehler — das war der erste Anlauf dieses Stubs, ein
    # Fixture-Fehler, kein Codefehler. Der Zaehler laeuft global ueber ALLE
    # Batches (die Extract-API wird in 5er-Batches aufgerufen), nicht pro Batch.
    out = []
    for u in urls:
        i = _C2_ZAEHLER["n"]
        _C2_ZAEHLER["n"] += 1
        if i % 2 == 0:
            out.append({"url": u, "raw_content": None, "erfolg": False})
        else:
            inhalt = ("## Ergebnisse\n\n" + bmw_karten(1, id_start=400000 + i)) * 3
            out.append({"url": u, "raw_content": inhalt, "erfolg": True})
    return out


_urls_c = [f"https://www.kleinanzeigen.de/s-autos/c{i}" for i in range(6)]
_treffer_c = [{"url": u, "title": "BMW 320d G20 gebraucht kaufen", "content": "",
              "raw_content": ""} for u in _urls_c]

ws.tavily_extract = _extract_alle_fehler
_erg_c1, _stat_c1, _verbraucht_c1 = run(mr.ergaenze_raw_content(list(_treffer_c), 24))
ws.tavily_extract = _orig_extract
check("C1: Extract komplett kaputt -> kein Crash", True)
check("C1: 0 ergaenzte Seiten, Fehler korrekt gezaehlt",
      _erg_c1 == 0 and _stat_c1.get("fehler", 0) >= 1)
check("C1: die urspruenglichen (leeren) Treffer bleiben unveraendert nutzbar "
      "(kein erfundener Inhalt)",
      all(not t.get("raw_content") for t in _treffer_c))

ws.tavily_extract = _extract_teilausfall
_treffer_c2 = [{"url": u, "title": "BMW 320d G20 gebraucht kaufen", "content": "",
               "raw_content": ""} for u in _urls_c]
_erg_c2, _stat_c2, _verbraucht_c2 = run(mr.ergaenze_raw_content(_treffer_c2, 24))
ws.tavily_extract = _orig_extract
check("C2: Extract-Teilausfall -> die erfolgreichen Haelften wurden ergaenzt",
      _erg_c2 == 3)
check("C2: die fehlgeschlagenen bleiben ohne Inhalt (kein Fake)",
      sum(1 for t in _treffer_c2 if not t.get("raw_content")) == 3)


# ══════════════════════════════════════════════════════════════════════════
# D) Kaputtes raw_content — kein Hochstufen auf high-confidence Evidence
# ══════════════════════════════════════════════════════════════════════════
def _extrahiere(url, titel, raw):
    text = titel + "\n\n" + raw
    return _extrahiere_aus_text(text, url, "market_category",
                                grenzen=(len(titel) + 1, len(titel) + 2),
                                seiten_body=_eindeutige_karosserie(url))


URL_D = "https://www.kleinanzeigen.de/s-autos/bmw-320d"

# D1) abgeschnittenes Markdown (Karte bricht mitten im Preis ab)
_roh_d1 = _extrahiere(URL_D, "BMW 320d gebraucht kaufen",
                      "## Ergebnisse\n\n* [![BMW 320d G20 Vorschau](https://img.x/1.jpg)\n\n"
                      "  ## [BMW 320d G20]")  # kein Preis, kein Abschluss
check("D1: abgeschnittenes Markdown erzeugt keine Fake-Beobachtung",
      len([b for b in _roh_d1 if b.preis_eur]) == 0)

# D2) nur Navigation/Filtertext, kein Fahrzeuginhalt
_roh_d2 = _extrahiere(URL_D, "BMW 320d gebraucht kaufen",
                      "Filter\n\nPreis von\n\nPreis bis\n\nUmkreis\n\nPLZ eingeben\n\nSortierung")
check("D2: reiner Navigationstext liefert keine Beobachtung",
      len(_roh_d2) == 0)

# D3) nur Seitentitel, kein Body
_roh_d3 = _extrahiere(URL_D, "BMW 320d G20 gebraucht kaufen | kleinanzeigen.de", "")
check("D3: Seitentitel allein (kein Fahrzeugpreis im Body) liefert keine Beobachtung",
      len(_roh_d3) == 0)

# D4) mehrere Fahrzeuge ohne sichere Kartengrenzen (Fliesstext-Mix).
#
# Erster Anlauf dieses Tests war selbst fehlerhaft: ein Satz mit ZWEI
# verschiedenen Markennamen ("BMW ... Audi ...") wird vom Segmenter ueber
# `title_anchor` (wiederkehrende Fahrzeugtitel als Anker) korrekt strukturell
# in zwei Bloecke getrennt — das ist keine fehlende Kartengrenze, sondern eine
# ECHTE. Fuer den beabsichtigten Fall (keinerlei strukturelles Signal) darf der
# Text weder eine Wiederholung des Markennamens noch einen Satzpunkt-Trenner
# enthalten — sonst greifen `title_anchor` bzw. `block_structure` zu Recht.
_mix = ("Im aktuellen Angebot findet sich ein BMW 320d G20 fuer 24.900 EUR mit "
        "118.000 km EZ 05/2019 und weiterhin ein weiteres Fahrzeug fuer "
        "23.900 EUR mit 95.000 km EZ 08/2018 ohne naehere Angabe")
_roh_d4 = _extrahiere(URL_D, "Gebrauchtwagen gemischt", _mix)
_bew_d4 = [_bewerte(b, ZIEL) for b in _roh_d4]
check("D4: ohne sichere Kartengrenzen wird KEIN Datenpunkt 'sehr_aehnlich' "
      "(hoechstens bedingt/Zeichenfenster)",
      all(b.vergleichbarkeit != "sehr_aehnlich" for b in _bew_d4))
check("D4b: beide Datenpunkte sind als window_fallback markiert "
      "(keine erfundene strukturelle Herkunft)",
      all(b.window_fallback_used for b in _bew_d4))

# D5) Preis ohne jede Fahrzeugidentitaet
_roh_d5 = _extrahiere(URL_D, "Irrelevante Seite", "24.900 €")
check("D5: Preis ohne Fahrzeugkontext liefert keine sinnvolle Beobachtung "
      "(kein Fahrzeugtitel/Modellhinweis)",
      len(_roh_d5) == 0 or all(b.make is None for b in _roh_d5))

# D6) Fahrzeugtext ohne Preis
_roh_d6 = _extrahiere(URL_D, "BMW 320d gebraucht kaufen",
                      "BMW 320d G20 Limousine, Diesel, Scheckheft, top Zustand")
check("D6: Fahrzeugtext ohne Preis erzeugt keine Preisbeobachtung",
      len(_roh_d6) == 0)


# ══════════════════════════════════════════════════════════════════════════
# E) Source-Policy unter Fehlerbedingungen
# ══════════════════════════════════════════════════════════════════════════
def _perfekte_karten_domain(n, domain_url, id_start):
    return seite(domain_url, "BMW 320d G20 gebraucht kaufen bei",
                bmw_karten(n, id_start=id_start))


# E1) NUR gesperrte Quellen, fachlich perfekt.
_nur_gesperrt = [
    _perfekte_karten_domain(6, "https://suchen.mobile.de/fahrzeuge/details.html", 500001),
    _perfekte_karten_domain(6, "https://www.autoscout24.de/lst/bmw/3er", 500100),
]
_ma_e1 = analysiere_markt(_nur_gesperrt, ZIEL, 24_900)
check("E1: ausschliesslich gesperrte Quellen -> 0 preisbildend",
      _ma_e1.verwendet == 0)
check("E1: kein Median, keine Spanne",
      _ma_e1.median_eur is None and _ma_e1.spanne_min_eur is None)
check("E1: keine Marktabdeckung durch gesperrte Domains",
      _ma_e1.anzahl_domains == 0 and _ma_e1.marktabdeckung == "eingeschraenkt")
check("E1: 'viele Treffer' umgeht die Source-Policy NICHT (12 Karten, trotzdem 0)",
      True)

# E2) Gemischt: 5 mobile.de + 5 autoscout24 + 4 kleinanzeigen (alle fachlich sauber).
_gemischt = [
    _perfekte_karten_domain(5, "https://suchen.mobile.de/fahrzeuge/details.html", 500200),
    _perfekte_karten_domain(5, "https://www.autoscout24.de/lst/bmw/3er", 500300),
    _perfekte_karten_domain(4, "https://www.kleinanzeigen.de/s-autos/bmw-320d", 500400),
]
_ma_e2 = analysiere_markt(_gemischt, ZIEL, 24_900)
check("E2: nur die 4 Kleinanzeigen-Karten sind preisbildend",
      _ma_e2.verwendet == 4)
check("E2: alle verwendeten Quell-Domains sind kleinanzeigen.de",
      all("kleinanzeigen.de" in d for d in _ma_e2.quellen_domains))
check("E2: Median existiert nur aus den 4 freigegebenen Karten",
      _ma_e2.median_eur is not None)
check("E2: mobile.de/autoscout24 stehen in keiner verwendeten Beobachtung",
      all("mobile.de" not in ((b.detail_url or b.quelle_url) or "")
          and "autoscout24" not in ((b.detail_url or b.quelle_url) or "")
          for b in _ma_e2.beobachtungen))


# ══════════════════════════════════════════════════════════════════════════
# F) Dedupe unter Fehlerbedingungen — dieselbe Anzeige mehrfach ueber Stufen
# ══════════════════════════════════════════════════════════════════════════
_gleiche_id = "600001"
_stufe_schwach = [seite(
    "https://www.kleinanzeigen.de/s-autos/schwach", "BMW 320d G20 gebraucht",
    "Irgendein Text ohne klare Struktur BMW 320d G20 24.900 € 118.000 km EZ 05/2019 "
    f"/s-anzeige/x/{_gleiche_id}-216-1 mittendrin erwaehnt")]
_stufe_stark = [seite(
    "https://www.kleinanzeigen.de/s-autos/stark", "BMW 320d G20 gebraucht",
    karte("BMW 320d G20 Advantage", _gleiche_id, "24.900", "118.000", "05/2019",
          "BMW 320d G20 Limousine, Diesel, Scheckheft")
    + bmw_karten(3, id_start=600100))]
_stub, _ = stub_sequence((_stufe_schwach, False), (_stufe_stark, False))
_mit_stub(_stub)
_, ma_f, diag_f = run(vertiefe_marktrecherche(
    [], stufen(2), ZIEL, 24_900, [], count=10, zweck="f", max_stufen=2))
_stub_zuruecksetzen()
_ids_f = [b.listing_id for b in ma_f.beobachtungen if b.listing_id == _gleiche_id]
check("F: dieselbe Listing-ID ueber zwei Stufen zaehlt nur EINMAL preisbildend",
      len(_ids_f) <= 1)
check("F: Gesamtzahl verwendeter Beobachtungen bleibt unter der Rohsumme "
      "(kein Doppelzaehlen)",
      ma_f.verwendet < 6)


# ══════════════════════════════════════════════════════════════════════════
# G) Single-Source-Semantik
# ══════════════════════════════════════════════════════════════════════════
_nur_ka = [seite("https://www.kleinanzeigen.de/s-autos/bmw-320d",
                 "BMW 320d G20 gebraucht kaufen", bmw_karten(8, id_start=700001))]
_ma_g = analysiere_markt(_nur_ka, ZIEL, 24_900)
check("G: 8 saubere Kleinanzeigen-Listings liefern ein Ergebnis",
      _ma_g.verwendet >= 6 and _ma_g.median_eur is not None)
check("G: Marktabdeckung bleibt bei einer Plattform 'eingeschraenkt'",
      _ma_g.marktabdeckung == "eingeschraenkt" and _ma_g.anzahl_domains == 1)
check("G: research_status wird durch Single-Source NICHT automatisch "
      "research_failed (Datenqualitaet darf hoch/mittel sein)",
      mr.research_status(_ma_g) in ("completed_high", "completed_medium"))


# ══════════════════════════════════════════════════════════════════════════
# H) Duenne Datenbasis — 0/1/2/3/5/6 Listings (bestehende Schwellen beobachten)
# ══════════════════════════════════════════════════════════════════════════
for n in (0, 1, 2, 3, 5, 6):
    seiten_h = [seite("https://www.kleinanzeigen.de/s-autos/bmw-320d",
                      "BMW 320d G20 gebraucht kaufen",
                      bmw_karten(n, id_start=800000 + n * 100))] if n else []
    ma_h = analysiere_markt(seiten_h, ZIEL, 24_900)
    status_h = mr.research_status(ma_h)
    print(f"    n={n}: verwendet={ma_h.verwendet} median={ma_h.median_eur} "
          f"quali={ma_h.datenqualitaet} status={status_h}")
    if n < 3:
        check(f"H: n={n} -> kein Median (unter der 3er-Mindestgrenze)",
              ma_h.median_eur is None)
    else:
        check(f"H: n={n} -> Median vorhanden (3 oder mehr saubere Vergleiche)",
              ma_h.median_eur is not None)


# ══════════════════════════════════════════════════════════════════════════
# I) Insignia-Kontrollfall — gespeicherter Lauf, KEIN Live-Search
# ══════════════════════════════════════════════════════════════════════════
import glob                                                               # noqa: E402


def _juengster_insignia_lauf():
    kandidaten = sorted(glob.glob("diagnose_runs/*_insignia_lauf1.json"))
    return kandidaten[-1] if kandidaten else None


_pfad_i = _juengster_insignia_lauf()
if _pfad_i:
    _d_i = json.load(open(_pfad_i, encoding="utf-8"))
    _z_i = _d_i.get("zusammenfassung", {})
    check("I: Insignia-Kontrolllauf gefunden und lesbar", True)
    check("I: research_status ist research_failed (ehrliches Ergebnis trotz "
          "vorhandenem Marktmaterial)",
          _z_i.get("research_status") == "research_failed")
    check("I: kein erfundener Median trotz Kartenfunden",
          _z_i.get("median_eur") is None)
else:
    check("I: kein gespeicherter Insignia-Lauf gefunden (uebersprungen)", True)


print()
if _fails:
    print(f"{len(_fails)} FEHLER: " + "; ".join(_fails))
    sys.exit(1)
print("Alle Pruefungen bestanden.")
