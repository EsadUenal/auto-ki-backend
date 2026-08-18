"""
Extract-Fallback für fehlenden Seiteninhalt — deterministisch, KEIN Netzwerk.

Tavilys Suche liefert `raw_content` nur unzuverlässig mit (gemessen: 0, 7, 11 und 11
von 15-17 Marktplatz-Suchseiten). Fehlt er, fehlen die Fahrzeugkarten. Die
Extract-API holt ihn nach — als reiner Fallback, ohne zweite Auswertungsschiene.

Fälle A-G aus der Aufgabenstellung.

    python test_extract_fallback.py
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, ".")
os.environ.setdefault("AUTO_KI_DB_PATH",
                      os.path.join(tempfile.mkdtemp(prefix="vira_ex_"), "test.db"))

from types import SimpleNamespace                                        # noqa: E402

import app.marktrecherche as mr                                          # noqa: E402
import app.web_search as ws                                              # noqa: E402
from app.marktrecherche import (                                         # noqa: E402
    QueryStufe, ergaenze_raw_content, ist_extract_kandidat, vertiefe_marktrecherche,
)
from app.marktvergleich import analysiere_markt, baue_ziel                # noqa: E402
from app.chassis_codes import VERIFIZIERTE_CHASSIS_CODES                  # noqa: E402

_fails: list[str] = []


def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        _fails.append(name)


def _leere_caches():
    ws._extract_cache.clear()
    ws._cache.clear()


# ── Zielprofil ──────────────────────────────────────────────────────────────
BAUREIHE = {"marke": "BMW", "modell": "3er", "generation": "G20/G21",
            "id": "bmw-3er-g20-g21", "karosserie": ["Limousine", "Touring"],
            "chassis_codes": VERIFIZIERTE_CHASSIS_CODES["bmw-3er-g20-g21"]}
ALLE = [BAUREIHE, {"id": "bmw-3er-f30", "marke": "BMW", "modell": "3er",
                   "generation": "F30"}]
MOTOREN = [{"baureihe_id": "bmw-3er-g20-g21", "bezeichnung": "320d", "motorcode": "B47D20"},
           {"baureihe_id": "bmw-3er-g20-g21", "bezeichnung": "330i"}]
REQ = SimpleNamespace(marke="BMW", modell="3er G20", motor="320d", kraftstoff="Diesel",
                      baujahr=2019, kilometerstand=120_000, preis_eur=24_900)
ZIEL = baue_ziel(BAUREIHE, {"bezeichnung": "320d", "kraftstoff": "Diesel",
                            "leistung_ps": 190, "motorcode": "B47D20"},
                 REQ, ALLE, MOTOREN)

SUCH_URL = "https://www.kleinanzeigen.de/s-autos/bmw-320d-2019/k0c216+autos.typ_s:limousine"
NAV = "## Filter\n\n### Preis\n\n## Ergebnisse\n\n"


def _karte(titel, lid, preis, km, ez):
    return (f"![{titel} Vorschau](https://img.kleinanzeigen.de/x.jpg)\n\n"
            f"## [{titel}](/s-anzeige/bmw/{lid}-216-4711)\n\n"
            f"Gepflegtes Fahrzeug, scheckheftgepflegt, unfallfrei...\n\n"
            f"{preis} €\n\n{km} km\n\nEZ {ez}\n\n")


SEITENINHALT = (NAV
                + _karte("BMW 320d Limousine Diesel", "3480000001", "24.900", "118.000", "05/2019")
                + _karte("BMW 320d Limousine Diesel", "3480000002", "25.400", "121.000", "06/2019")
                + _karte("BMW 320d Limousine Diesel", "3480000003", "25.900", "117.000", "07/2019"))


def _treffer(url, raw=""):
    return {"url": url, "title": "Bmw 320d 2019, Limousine gebraucht kaufen",
            "content": "BMW 320d Angebote", "raw_content": raw}


class _ExtractStub:
    """Ersetzt die Extract-API. Zählt Aufrufe und kann gezielt URLs scheitern lassen."""

    def __init__(self, inhalte: dict, fehler: set | None = None):
        self.inhalte = inhalte
        self.fehler = fehler or set()
        self.calls: list[list[str]] = []

    async def __call__(self, urls, *, advanced=False):
        self.calls.append(list(urls))
        out = []
        for u in urls:
            if u in self.fehler:
                out.append({"url": u, "raw_content": None, "erfolg": False})
            else:
                out.append({"url": u, "raw_content": self.inhalte.get(u, ""), "erfolg": True})
        return out


def _mit_stub(stub, fn):
    orig = ws.tavily_extract
    ws.tavily_extract = stub
    try:
        return fn()
    finally:
        ws.tavily_extract = orig


# ══ A — Treffer MIT raw_content -> kein Extract ═════════════════════════════
_leere_caches()
stub = _ExtractStub({SUCH_URL: SEITENINHALT})
res_a = [_treffer(SUCH_URL, SEITENINHALT)]
erg, stat, verbraucht = _mit_stub(stub, lambda: asyncio.run(ergaenze_raw_content(res_a, 24)))
check("A: vorhandener raw_content löst KEINEN Extract aus", stub.calls == [])
check("A: nichts ergänzt, kein Budget verbraucht", erg == 0 and verbraucht == 0)
check("A: der Treffer ist auch kein Kandidat", ist_extract_kandidat(res_a[0]) is False)

# ══ B — relevanter Treffer OHNE raw_content -> Extract + normale Verarbeitung ══
_leere_caches()
stub = _ExtractStub({SUCH_URL: SEITENINHALT})
res_b = [_treffer(SUCH_URL, "")]
check("B: der Treffer ist ein Extract-Kandidat", ist_extract_kandidat(res_b[0]) is True)
erg, stat, verbraucht = _mit_stub(stub, lambda: asyncio.run(ergaenze_raw_content(res_b, 24)))
check("B: genau ein Extract-Aufruf", len(stub.calls) == 1 and stub.calls[0] == [SUCH_URL])
check("B: der Inhalt wurde in den Treffer geschrieben",
      erg == 1 and res_b[0]["raw_content"] == SEITENINHALT.strip())
ma_b = analysiere_markt(res_b, ZIEL, None)
check("B: der nachgeladene Inhalt liefert Fahrzeugkarten",
      sorted(x.preis_eur for x in ma_b.beobachtungen) == [24900, 25400, 25900])
check("B: die Karten sind strukturell segmentiert (dieselbe Pipeline)",
      all(x.segmentation_method == "detail_link" for x in ma_b.beobachtungen))

# ══ C — dieselbe URL in drei Query-Stufen -> nur ein Extract ════════════════
_leere_caches()
stub = _ExtractStub({SUCH_URL: SEITENINHALT})


class _SuchStub:
    """Liefert in JEDER Stufe denselben Treffer ohne raw_content."""

    def __init__(self):
        self.calls = 0

    async def __call__(self, query, *a, **kw):
        self.calls += 1
        return [_treffer(SUCH_URL, "")], False


such = _SuchStub()
orig_suche = mr.tavily_search_mit_status
mr.tavily_search_mit_status = such
try:
    stufen = [QueryStufe(query=f"q{i}", include_domains=None, label=f"s{i}")
              for i in range(1, 4)]
    _res, ma_c, diag_c = _mit_stub(stub, lambda: asyncio.run(vertiefe_marktrecherche(
        [], stufen, ZIEL, REQ.preis_eur, None, count=10, zweck="test-extract")))
finally:
    mr.tavily_search_mit_status = orig_suche

check("C: die Suche lief über mehrere Stufen", such.calls >= 3)
check("C: trotzdem nur EIN Extract-Aufruf", len(stub.calls) == 1)
check("C: nur eine URL extrahiert", sum(len(c) for c in stub.calls) == 1)
check("C: die Marktanalyse hat die Karten trotzdem",
      sorted(x.preis_eur for x in ma_c.beobachtungen) == [24900, 25400, 25900])
# Die Seite taucht in allen drei Stufen auf und wird jedes Mal befüllt — aber nur
# der ERSTE Durchlauf kostet einen API-Aufruf, die beiden anderen bedient der Cache.
check("C: die Diagnose weist genau einen bezahlten Aufruf aus",
      diag_c["extract"]["extract_calls"] == 1 and diag_c["extract"]["extract_urls"] == 1)
check("C: spätere Stufen bedienen sich am Cache", diag_c["extract"]["aus_cache"] == 2)
check("C: nur ein Extract-Erfolg trotz drei befüllter Stufen",
      diag_c["extract"]["erfolge"] == 1 and diag_c["extract"]["ergaenzte_seiten"] == 3)
check("C: das Budget ist ausgewiesen",
      diag_c["extract"]["budget"] == mr.MAX_EXTRACT_URLS)

# ══ D — Extract schlägt fehl -> Recherche läuft weiter ═════════════════════
_leere_caches()
stub = _ExtractStub({}, fehler={SUCH_URL})
res_d = [_treffer(SUCH_URL, "")]
erg, stat, _v = _mit_stub(stub, lambda: asyncio.run(ergaenze_raw_content(res_d, 24)))
check("D: nichts ergänzt", erg == 0)
check("D: der ursprüngliche Treffer bleibt erhalten",
      res_d[0]["url"] == SUCH_URL and res_d[0]["content"] == "BMW 320d Angebote")
check("D: als Fehler gezählt, keine Exception", stat["fehler"] == 1)


class _KaputterStub:
    async def __call__(self, urls, *, advanced=False):
        raise RuntimeError("Extract-Endpunkt nicht erreichbar")


_leere_caches()
res_d2 = [_treffer(SUCH_URL, "")]
erg2, stat2, _ = _mit_stub(_KaputterStub(),
                           lambda: asyncio.run(ergaenze_raw_content(res_d2, 24)))
check("D: auch eine Exception im Extract bricht nichts ab",
      erg2 == 0 and stat2["fehler"] == 1 and res_d2[0]["raw_content"] == "")

# ══ E — gemischter Batch: 2 erfolgreich, 1 fehlgeschlagen ══════════════════
_leere_caches()
U1 = "https://www.kleinanzeigen.de/s-autos/bmw-320d-a/k0c216"
U2 = "https://www.kleinanzeigen.de/s-autos/bmw-320d-b/k0c216"
U3 = "https://www.kleinanzeigen.de/s-autos/bmw-320d-c/k0c216"
stub = _ExtractStub({U1: SEITENINHALT, U3: SEITENINHALT}, fehler={U2})
res_e = [_treffer(U1, ""), _treffer(U2, ""), _treffer(U3, "")]
erg, stat, _ = _mit_stub(stub, lambda: asyncio.run(ergaenze_raw_content(res_e, 24)))
check("E: zwei Inhalte ergänzt, einer fehlgeschlagen",
      erg == 2 and stat["erfolge"] == 2 and stat["fehler"] == 1)
check("E: alle drei in EINEM Batch angefragt", len(stub.calls) == 1 and len(stub.calls[0]) == 3)
check("E: die erfolgreichen Seiten liefern Karten",
      res_e[0]["raw_content"] and res_e[2]["raw_content"] and res_e[1]["raw_content"] == "")

# ══ F — Ratgeber-/Teile-/Fremdseiten werden nicht extrahiert ══════════════
check("F: Info-/Ratgeberdomain ist kein Kandidat",
      ist_extract_kandidat(_treffer("https://www.adac.de/rund-ums-fahrzeug/bmw-3er", "")) is False)
check("F: Teile-/Zubehörsuche ist kein Kandidat",
      ist_extract_kandidat(
          {"url": "https://www.kleinanzeigen.de/s-autos/g20-scheinwerfer/k0c216",
           "title": "Scheinwerfer", "content": "", "raw_content": ""}) is False)
check("F: eine fremde Domain ohne Marktplatzbezug ist kein Kandidat",
      ist_extract_kandidat(_treffer("https://www.youtube.com/watch?v=x", "")) is False)
check("F: ein echter Marktplatz ohne Inhalt IST ein Kandidat",
      ist_extract_kandidat(_treffer("https://suchen.mobile.de/auto/bmw-3er.html", "")) is True)
_leere_caches()
stub = _ExtractStub({})
res_f = [_treffer("https://www.adac.de/x", ""), _treffer("https://www.youtube.com/x", "")]
_mit_stub(stub, lambda: asyncio.run(ergaenze_raw_content(res_f, 24)))
check("F: für solche Treffer wird gar kein Extract gesendet", stub.calls == [])

# ══ G — Search-raw_content und Extract-Content ergeben identische Cards ════
_leere_caches()
ma_search = analysiere_markt([_treffer(SUCH_URL, SEITENINHALT)], ZIEL, None)
_leere_caches()
stub = _ExtractStub({SUCH_URL: SEITENINHALT})
res_g = [_treffer(SUCH_URL, "")]
_mit_stub(stub, lambda: asyncio.run(ergaenze_raw_content(res_g, 24)))
ma_extract = analysiere_markt(res_g, ZIEL, None)


def _fingerabdruck(ma):
    return [(b.listing_key, b.preis_eur, b.kilometerstand, b.baujahr, b.generation,
             b.generation_evidence, b.engine_variant, b.body, b.vergleichbarkeit,
             b.segmentation_method, b.structural_confidence, b.window_fallback_used)
            for b in ma.beobachtungen]


check("G: identischer Seitentext -> identische MarketObservations",
      _fingerabdruck(ma_search) == _fingerabdruck(ma_extract))
check("G: identischer Median und identische Spanne",
      (ma_search.median_eur, ma_search.spanne_min_eur, ma_search.spanne_max_eur)
      == (ma_extract.median_eur, ma_extract.spanne_min_eur, ma_extract.spanne_max_eur))
check("G: identische Datenqualität und Marktabdeckung",
      (ma_search.datenqualitaet, ma_search.marktabdeckung)
      == (ma_extract.datenqualitaet, ma_extract.marktabdeckung))

# ══ Budget und Kanonisierung ═══════════════════════════════════════════════
_leere_caches()
stub = _ExtractStub({f"https://www.kleinanzeigen.de/s-autos/x{i}/k0c216": SEITENINHALT
                     for i in range(10)})
res_b2 = [_treffer(f"https://www.kleinanzeigen.de/s-autos/x{i}/k0c216", "") for i in range(10)]
erg, stat, verbraucht = _mit_stub(stub, lambda: asyncio.run(ergaenze_raw_content(res_b2, 3)))
check("Budget: mehr Kandidaten als Budget -> nur Budget viele angefragt",
      verbraucht == 3 and stat["angefragt"] == 3)
check("Budget: aufgebrauchtes Budget löst keinen Aufruf mehr aus",
      _mit_stub(stub, lambda: asyncio.run(ergaenze_raw_content(res_b2, 0)))[0] == 0)

_leere_caches()
stub = _ExtractStub({SUCH_URL: SEITENINHALT})
res_k = [_treffer(SUCH_URL, ""), _treffer(SUCH_URL + "/", "")]
_mit_stub(stub, lambda: asyncio.run(ergaenze_raw_content(res_k, 24)))
check("Kanonisierung: URL mit und ohne Schrägstrich wird einmal geladen",
      sum(len(c) for c in stub.calls) == 1)

check("Batchgröße ist begrenzt und begründet", ws._EXTRACT_BATCH_SIZE == 5)
check("Extract-Cache-Key trägt die Version",
      ws._extract_cache_key("https://x.de/a")[1] == ws._EXTRACT_CACHE_VERSION)
check("Cache liegt nur im Prozess, nicht in der Datenbank",
      isinstance(ws._extract_cache, dict))

print()
if _fails:
    print(f"{len(_fails)} Test(s) fehlgeschlagen.")
    sys.exit(1)
print("Alle Extract-Fallback-Tests bestanden.")
