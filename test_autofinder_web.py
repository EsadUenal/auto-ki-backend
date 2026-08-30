"""
Test: AutoFinder Controlled Web Fallback (Runde 4) — app/autofinder_web.py

Deckt die Testmatrix A-V der Produktspezifikation ab: Coverage-Gate (wann Web
ueberhaupt laufen darf), Kostendeckel (Tavily <=2, Discovery-Gemini <=1,
Budget-Gemini <=1), das Validierungs-Gate gegen Phantomfahrzeuge und erfundene
technische Werte, harte Filter auch fuer Web-Kandidaten, Ausfallsicherheit,
Determinismus, sowie die Rechte-/Source-Zusicherungen (keine Marktplaetze,
keine Preise).

Tavily und Gemini werden vollstaendig gefaked — KEIN Netzwerk. Der Tavily-Key
wird zusaetzlich hart geleert, damit auch ein vergessener Stub nicht
versehentlich einen echten Request ausloest.

Ausfuehren:  python test_autofinder_web.py
"""
import asyncio
import importlib
import inspect
import os
import sys
import tempfile

sys.path.insert(0, ".")

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


# ── Frische kanonische DB, eigener API-Key, Tavily hart aus ────────────────
_tmp = tempfile.mkdtemp(prefix="vira_af_web_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "kanonisch.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")
os.environ["AUTO_KI_API_KEY"] = "test-key-autofinder-web"
# Sicherheitsnetz gegen echte Netzwerkzugriffe: leerer Key laesst
# web_search._tavily_search_intern sofort ([], True) zurueckgeben.
os.environ["TAVILY_API_KEY"] = ""

import app.config as _cfg
importlib.reload(_cfg)
import app.database as _db
importlib.reload(_db)
_db.ensure_tables()

from fastapi.testclient import TestClient          # noqa: E402
from app.main import app as fastapi_app            # noqa: E402
import app.routers.autofinder as af_router         # noqa: E402
import app.autofinder as af                        # noqa: E402
import app.autofinder_web as afw                   # noqa: E402
import app.autofinder_budget as af_budget          # noqa: E402
from app.rate_limit import limiter as _global_limiter   # noqa: E402

client = TestClient(fastapi_app)
HEADERS = {"Authorization": "Bearer test-key-autofinder-web"}
URL = "/api/v1/autofinder"


def post(body: dict):
    return client.post(URL, json=body, headers=HEADERS)


def _reset_limiters():
    _global_limiter.reset()
    af_router.limiter.reset()


# ══════════════════════════════════════════════════════════════════════════
# Fakes: Tavily + Discovery-Gemini, jeweils mit Aufrufzaehler
# ══════════════════════════════════════════════════════════════════════════
class _TavilyFake:
    """Ersetzt `tavily_search_mit_status`. Zaehlt Aufrufe und merkt sich die
    uebergebenen exclude_domains (fuer den Marktplatz-Nachweis)."""

    def __init__(self, treffer=None, fehler=False, exc=None):
        self.n = 0
        self.queries: list[str] = []
        self.exclude_domains: list = []
        self._treffer = treffer if treffer is not None else []
        self._fehler = fehler
        self._exc = exc

    async def __call__(self, query, count=5, include_domains=None,
                       exclude_domains=None, include_raw_content=False,
                       bypass_cache=False, search_depth="basic"):
        self.n += 1
        self.queries.append(query)
        self.exclude_domains = list(exclude_domains or [])
        if self._exc:
            raise self._exc
        return list(self._treffer), self._fehler


class _GeminiFake:
    def __init__(self, antwort=None, exc=None):
        self.n = 0
        self.letzter_prompt = None
        self._antwort = antwort if antwort is not None else {"candidates": []}
        self._exc = exc

    async def __call__(self, system_prompt, user_msg):
        self.n += 1
        self.letzter_prompt = user_msg
        if self._exc:
            raise self._exc
        return self._antwort


async def _budget_gemini_leer(system_prompt, user_msg):
    return {"candidates": []}


# Realistische, NICHT-Marktplatz-Evidenzen (Hersteller + Fachmedium).
EV_HERSTELLER = {
    "url": "https://www.renault.de/modelle/megane-grandtour.html",
    "title": "Renault Megane Grandtour – Technische Daten",
    "content": ("Der Renault Megane Grandtour ist als Kombi erhaeltlich. "
                "Die Blue dCi 150 Dieselversion leistet 150 PS und ist mit "
                "EDC Automatik kombinierbar. Produktion ab 2018."),
}
EV_FACHMEDIUM = {
    "url": "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/renault/megane/",
    "title": "Renault Megane Grandtour im ADAC Autotest",
    "content": ("Renault Megane Grandtour Kombi, Diesel Blue dCi 150 mit 150 PS, "
                "Automatik, Baujahr ab 2018."),
}
EVIDENZEN = [EV_HERSTELLER, EV_FACHMEDIUM]

GUELTIGER_KANDIDAT = {
    "marke": "Renault", "modell": "Megane", "generation": "Grandtour",
    "motor": "Blue dCi 150", "baujahr_von": 2018, "baujahr_bis": None,
    "kraftstoff": "Diesel", "leistung_ps": 150,
    "getriebe": "automatik", "karosserie": "kombi", "antrieb": "Front",
    "evidence": [1, 2],
}

# Filter, die intern (13 Marken) sehr wenige/keine Treffer liefern und deshalb
# das Coverage-Gate ausloesen.
BODY_SCHWACH = {"kraftstoff": ["Plug-in-Hybrid"], "getriebe": ["automatik"],
                 "leistung_min_ps": 600}
BODY_LEER = {"kraftstoff": ["Elektro"], "getriebe": ["manuell"]}
BODY_STARK = {"kraftstoff": ["Diesel"], "getriebe": ["automatik"]}


def _setze_fakes(tav: _TavilyFake, gem: _GeminiFake):
    afw.tavily_search_mit_status = tav
    afw.call_gemini_json = gem
    af_budget.call_gemini_json = _budget_gemini_leer


# ══════════════════════════════════════════════════════════════════════════
# A) >=3 gute interne Kandidaten -> 0 Tavily-Calls
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
tav_a, gem_a = _TavilyFake(EVIDENZEN), _GeminiFake({"candidates": [GUELTIGER_KANDIDAT]})
_setze_fakes(tav_a, gem_a)
r_a = post(BODY_STARK)
data_a = r_a.json()
check("A: 200", r_a.status_code == 200)
check("A: Testvoraussetzung — >=3 interne Kandidaten vorhanden",
      data_a["total_candidates_considered"] >= 3)
check("A: gute interne Coverage -> 0 Tavily-Calls", tav_a.n == 0)
check("A: gute interne Coverage -> 0 Discovery-Gemini-Calls", gem_a.n == 0)
check("A: alle Kandidaten sind internal_db",
      all(k["source_type"] == "internal_db" for k in data_a["kandidaten"]))


# ══════════════════════════════════════════════════════════════════════════
# B) 0 interne Kandidaten -> Web-Fallback aktiviert
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
tav_b, gem_b = _TavilyFake(EVIDENZEN), _GeminiFake({"candidates": []})
_setze_fakes(tav_b, gem_b)
r_b = post(BODY_LEER)
check("B: 200", r_b.status_code == 200)
check("B: 0 interne Kandidaten -> Web-Fallback lief (Tavily aufgerufen)", tav_b.n >= 1)


# ══════════════════════════════════════════════════════════════════════════
# C) wenige interne Kandidaten (<3) -> Web-Fallback aktiviert
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
tav_c, gem_c = _TavilyFake(EVIDENZEN), _GeminiFake({"candidates": []})
_setze_fakes(tav_c, gem_c)
r_c = post(BODY_SCHWACH)
data_c = r_c.json()
check("C: 200", r_c.status_code == 200)
check("C: Testvoraussetzung — weniger als 3 interne Kandidaten",
      data_c["total_candidates_considered"] < afw.COVERAGE_MIN_INTERNE_KANDIDATEN)
check("C: geringe Coverage -> Web-Fallback lief", tav_c.n >= 1)


# ══════════════════════════════════════════════════════════════════════════
# D) intern gar nicht gefuehrte Marke -> Web-Fallback moeglich
# ══════════════════════════════════════════════════════════════════════════
_marken = af_router._bekannte_marken()
check("D: Testvoraussetzung — 'Renault' ist NICHT im internen Bestand",
      "renault" not in _marken)
check("D: Testvoraussetzung — 'BMW' IST im internen Bestand", "bmw" in _marken)

# Viele interne BMW-Treffer, aber Renault fehlt komplett -> Regel 3 greift.
_viele_bmw = [object()] * 10


class _FakeReq:
    def __init__(self, marken):
        self.marken_bevorzugt = marken


_ja, _grund = afw.braucht_web_fallback(_viele_bmw, _FakeReq(["BMW", "Renault"]), _marken)
check("D: gewuenschte, intern fehlende Marke loest Web-Fallback aus — "
      "auch bei vielen internen Treffern anderer Marken",
      _ja and _grund == afw.GRUND_MARKE_NICHT_IM_BESTAND)
_nein, _ = afw.braucht_web_fallback(_viele_bmw, _FakeReq(["BMW"]), _marken)
check("D: nur intern vorhandene Marken -> KEIN Web-Fallback", not _nein)


# ══════════════════════════════════════════════════════════════════════════
# E) Web-Kandidat mit vollstaendiger Evidenz -> akzeptiert
# ══════════════════════════════════════════════════════════════════════════
_kand_e, _grund_e = afw._pruefe_kandidat(GUELTIGER_KANDIDAT, EVIDENZEN)
check("E: vollstaendig belegter Kandidat wird akzeptiert",
      _kand_e is not None and _grund_e == "")
if _kand_e:
    check("E: Marke/Modell/Kraftstoff/Leistung sind als belegt markiert",
          {"marke", "modell", "kraftstoff", "leistung_ps"} <= set(_kand_e.web_verified_fields))
    check("E: zwei unabhaengige Domains, davon Hersteller -> discovery_confidence HIGH",
          _kand_e.discovery_confidence == afw.CONF_HIGH)
    check("E: evidence_count entspricht den zitierten Belegen", _kand_e.evidence_count == 2)


# ══════════════════════════════════════════════════════════════════════════
# F) fehlende Leistung/Motorisierung -> abgelehnt
# ══════════════════════════════════════════════════════════════════════════
_ohne_motor = {**GUELTIGER_KANDIDAT, "leistung_ps": None, "motor": None}
_k_f, _g_f = afw._pruefe_kandidat(_ohne_motor, EVIDENZEN)
check("F: weder Leistung noch Motorisierung -> abgelehnt",
      _k_f is None and "Leistung" in _g_f)

# Und: bei hartem Leistungsfilter faellt ein Kandidat ohne Leistung durch die
# Foundation-Hardfilter (dieselbe Logik wie fuer DB-Kandidaten).
_kand_ohne_ps = afw.WebKandidat(
    candidate_id="web:test--ohneps", marke="Renault", modell="Megane",
    generation="G", motor_bezeichnung="dCi", baujahr_von=2018, baujahr_bis=None,
    leistung_ps=None, kraftstoff="Diesel", getriebe_klassen=["automatik"],
    antrieb="Front", karosserie_klassen=["kombi"])
_req_ps = af.AutoFinderRequest(leistung_min_ps=150)
check("F: Kandidat ohne Leistung faellt bei hartem Leistungsfilter durch",
      afw._filtere_und_bewerte([_kand_ohne_ps], _req_ps) == [])


# ══════════════════════════════════════════════════════════════════════════
# G) falscher Kraftstoff -> abgelehnt
# ══════════════════════════════════════════════════════════════════════════
_kand_benzin = afw.WebKandidat(
    candidate_id="web:test--benzin", marke="Renault", modell="Megane",
    generation="G", motor_bezeichnung="TCe", baujahr_von=2018, baujahr_bis=None,
    leistung_ps=160, kraftstoff="Benzin", getriebe_klassen=["automatik"],
    antrieb="Front", karosserie_klassen=["kombi"])
_req_diesel = af.AutoFinderRequest(kraftstoff=["Diesel"])
check("G: Benzin-Kandidat faellt bei hartem Diesel-Filter durch",
      afw._filtere_und_bewerte([_kand_benzin], _req_diesel) == [])

# Zusaetzlich: ein Kraftstoff, der im Belegtext gar nicht vorkommt, wird
# bereits im Validierungs-Gate abgelehnt.
_falscher_kraftstoff = {**GUELTIGER_KANDIDAT, "kraftstoff": "Elektro"}
_k_g, _g_g = afw._pruefe_kandidat(_falscher_kraftstoff, EVIDENZEN)
check("G: Kraftstoff ohne Beleg im zitierten Text -> abgelehnt",
      _k_g is None and "Kraftstoff" in _g_g)


# ══════════════════════════════════════════════════════════════════════════
# H) falsche Karosserie -> abgelehnt
# ══════════════════════════════════════════════════════════════════════════
_kand_suv = afw.WebKandidat(
    candidate_id="web:test--suv", marke="Renault", modell="Kadjar",
    generation="G", motor_bezeichnung="dCi 150", baujahr_von=2018, baujahr_bis=None,
    leistung_ps=150, kraftstoff="Diesel", getriebe_klassen=["automatik"],
    antrieb="Front", karosserie_klassen=["suv"])
_req_kombi = af.AutoFinderRequest(karosserie=["kombi"])
check("H: SUV-Kandidat faellt bei hartem Kombi-Filter durch",
      afw._filtere_und_bewerte([_kand_suv], _req_kombi) == [])

_kand_ohne_karo = afw.WebKandidat(
    candidate_id="web:test--ohnekaro", marke="Renault", modell="Megane",
    generation="G", motor_bezeichnung="dCi 150", baujahr_von=2018, baujahr_bis=None,
    leistung_ps=150, kraftstoff="Diesel", getriebe_klassen=["automatik"],
    antrieb="Front", karosserie_klassen=[])
check("H: Kandidat mit UNBEKANNTER Karosserie faellt bei hartem Karosserie-"
      "Filter durch (UNKNOWN wird nicht wohlwollend ausgelegt)",
      afw._filtere_und_bewerte([_kand_ohne_karo], _req_kombi) == [])


# ══════════════════════════════════════════════════════════════════════════
# I) widerspruechliche Quellen -> abgelehnt
# ══════════════════════════════════════════════════════════════════════════
_a = afw.WebKandidat(
    candidate_id="web:renault--megane--g--dci", marke="Renault", modell="Megane",
    generation="G", motor_bezeichnung="dCi", baujahr_von=2018, baujahr_bis=None,
    leistung_ps=150, kraftstoff="Diesel", getriebe_klassen=["automatik"],
    antrieb="Front", karosserie_klassen=["kombi"])
_b = afw.WebKandidat(
    candidate_id="web:renault--megane--g--dci", marke="Renault", modell="Megane",
    generation="G", motor_bezeichnung="dCi", baujahr_von=2018, baujahr_bis=None,
    leistung_ps=190, kraftstoff="Diesel", getriebe_klassen=["automatik"],
    antrieb="Front", karosserie_klassen=["kombi"])
check("I: zwei Eintraege zur selben Identitaet mit widerspruechlicher Leistung "
      "-> BEIDE verworfen (nicht der bequemere gewaehlt)",
      afw._entferne_widersprueche([_a, _b]) == [])
check("I: ohne Widerspruch bleibt der Kandidat erhalten",
      len(afw._entferne_widersprueche([_a])) == 1)


# ══════════════════════════════════════════════════════════════════════════
# J) Gemini erfindet Kandidat ohne Evidenz -> abgelehnt
# ══════════════════════════════════════════════════════════════════════════
_phantom = {**GUELTIGER_KANDIDAT, "marke": "Wolkenwagen", "modell": "Phantom",
            "evidence": [1, 2]}
_k_j, _g_j = afw._pruefe_kandidat(_phantom, EVIDENZEN)
check("J: Fahrzeug, dessen Marke in keinem zitierten Beleg vorkommt -> abgelehnt",
      _k_j is None and "Marke" in _g_j)

_ohne_evidenz = {**GUELTIGER_KANDIDAT, "evidence": []}
_k_j2, _g_j2 = afw._pruefe_kandidat(_ohne_evidenz, EVIDENZEN)
check("J: Kandidat ohne Belegnummern -> abgelehnt",
      _k_j2 is None and "Belegnummern" in _g_j2)

_erfundener_beleg = {**GUELTIGER_KANDIDAT, "evidence": [1, 99]}
_k_j3, _g_j3 = afw._pruefe_kandidat(_erfundener_beleg, EVIDENZEN)
check("J: erfundene Belegnummer (99 existiert nicht) -> abgelehnt",
      _k_j3 is None and "existiert nicht" in _g_j3)


# ══════════════════════════════════════════════════════════════════════════
# K) Gemini erfindet technische Werte -> nicht uebernommen
# ══════════════════════════════════════════════════════════════════════════
_erfundene_ps = {**GUELTIGER_KANDIDAT, "leistung_ps": 999}
_k_k, _g_k = afw._pruefe_kandidat(_erfundene_ps, EVIDENZEN)
check("K: Leistungswert, der in keinem zitierten Beleg steht -> Kandidat "
      "abgelehnt (kein stilles Uebernehmen des Modellwissens)",
      _k_k is None and "Leistungsangabe" in _g_k)

_erfundenes_modell = {**GUELTIGER_KANDIDAT, "modell": "Zafira"}
_k_k2, _g_k2 = afw._pruefe_kandidat(_erfundenes_modell, EVIDENZEN)
check("K: Modellname ohne Beleg -> abgelehnt", _k_k2 is None and "Modell" in _g_k2)


# ══════════════════════════════════════════════════════════════════════════
# L) Tavily-Ausfall -> interne Ergebnisse weiterhin HTTP 200
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
tav_l = _TavilyFake(exc=RuntimeError("Tavily komplett down"))
gem_l = _GeminiFake({"candidates": [GUELTIGER_KANDIDAT]})
_setze_fakes(tav_l, gem_l)
r_l = post(BODY_SCHWACH)
data_l = r_l.json()
check("L: Tavily-Totalausfall -> trotzdem HTTP 200", r_l.status_code == 200)
check("L: interne Kandidaten bleiben erhalten", len(data_l["kandidaten"]) >= 1)
check("L: ohne Evidenzen wird der Discovery-Gemini gar nicht erst aufgerufen",
      gem_l.n == 0)


# ══════════════════════════════════════════════════════════════════════════
# M) Discovery-Gemini-Ausfall -> interne Ergebnisse weiterhin HTTP 200
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
tav_m = _TavilyFake(EVIDENZEN)
gem_m = _GeminiFake(exc=RuntimeError("Gemini down"))
_setze_fakes(tav_m, gem_m)
r_m = post(BODY_SCHWACH)
data_m = r_m.json()
check("M: Discovery-Gemini-Ausfall -> trotzdem HTTP 200", r_m.status_code == 200)
check("M: interne Kandidaten bleiben erhalten", len(data_m["kandidaten"]) >= 1)
check("M: kein web_discovered-Kandidat bei Gemini-Ausfall",
      all(k["source_type"] == "internal_db" for k in data_m["kandidaten"]))

_reset_limiters()
gem_m2 = _GeminiFake({"kaputt": "kein candidates-Array"})
_setze_fakes(_TavilyFake(EVIDENZEN), gem_m2)
r_m2 = post(BODY_SCHWACH)
check("M: kaputte Gemini-Antwort (kein candidates-Array) -> 200, kein Crash",
      r_m2.status_code == 200)


# ══════════════════════════════════════════════════════════════════════════
# N/O) Web-Kandidat: keine Fake-DB-ID, korrekter source_type
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
tav_n = _TavilyFake(EVIDENZEN)
gem_n = _GeminiFake({"candidates": [GUELTIGER_KANDIDAT]})
_setze_fakes(tav_n, gem_n)
r_n = post({"kraftstoff": ["Diesel"], "karosserie": ["kombi"],
            "getriebe": ["automatik"], "leistung_min_ps": 150,
            "marken_bevorzugt": ["Renault"]})
data_n = r_n.json()
_web = [k for k in data_n["kandidaten"] if k["source_type"] == "web_discovered"]
check("N/O: 200", r_n.status_code == 200)
check("N/O: der validierte Web-Kandidat erscheint im Ergebnis", len(_web) >= 1)
if _web:
    w = _web[0]
    check("N: web_discovered-Kandidat hat KEINE baureihe_id", w["baureihe_id"] is None)
    check("N: web_discovered-Kandidat hat KEINE variante_id", w["variante_id"] is None)
    check("N: candidate_id traegt das unverwechselbare 'web:'-Praefix",
          w["candidate_id"].startswith("web:"))
    check("O: source_type == 'web_discovered'", w["source_type"] == "web_discovered")
    check("O: source_urls sind befuellt und enthalten die Belegquellen",
          len(w["source_urls"]) >= 1)
    check("O: evidence_count > 0", w["evidence_count"] > 0)
    check("O: discovery_confidence ist gesetzt",
          w["discovery_confidence"] in ("HIGH", "MEDIUM", "LOW"))
    check("O: web_verified_fields nennt die tatsaechlich belegten Felder",
          "marke" in w["web_verified_fields"])
_intern = [k for k in data_n["kandidaten"] if k["source_type"] == "internal_db"]
check("N/O: interne Kandidaten behalten ihre echten DB-IDs",
      all(k["baureihe_id"] and k["variante_id"] for k in _intern))


# ══════════════════════════════════════════════════════════════════════════
# P/Q/R/S) Kostendeckel
# ══════════════════════════════════════════════════════════════════════════
_reset_limiters()
# Tavily liefert absichtlich NICHTS -> der zweite Call wird als Reserve genutzt,
# darf aber die Obergrenze nicht ueberschreiten.
tav_p = _TavilyFake([])
gem_p = _GeminiFake({"candidates": []})
_setze_fakes(tav_p, gem_p)
post(BODY_LEER)
check(f"P: hoechstens {afw.MAX_TAVILY_CALLS} Tavily-Calls pro Suche "
      f"(gemessen: {tav_p.n})", tav_p.n <= afw.MAX_TAVILY_CALLS)

_reset_limiters()
tav_q = _TavilyFake(EVIDENZEN)
gem_q = _GeminiFake({"candidates": [GUELTIGER_KANDIDAT]})
_setze_fakes(tav_q, gem_q)
post(BODY_SCHWACH)
check(f"Q: hoechstens {afw.MAX_DISCOVERY_GEMINI_CALLS} Discovery-Gemini-Call "
      f"(gemessen: {gem_q.n})", gem_q.n <= afw.MAX_DISCOVERY_GEMINI_CALLS)


class _BudgetZaehler:
    def __init__(self):
        self.n = 0

    async def __call__(self, system_prompt, user_msg):
        self.n += 1
        return {"candidates": []}


_reset_limiters()
_bz = _BudgetZaehler()
afw.tavily_search_mit_status = _TavilyFake(EVIDENZEN)
afw.call_gemini_json = _GeminiFake({"candidates": [GUELTIGER_KANDIDAT]})
af_budget.call_gemini_json = _bz
post({**BODY_SCHWACH, "budget_min": 10000, "budget_max": 25000})
check(f"R: hoechstens 1 Budget-Gemini-Call, auch mit aktivem Web-Fallback "
      f"(gemessen: {_bz.n})", _bz.n <= 1)

_reset_limiters()
_bz2 = _BudgetZaehler()
afw.tavily_search_mit_status = _TavilyFake(EVIDENZEN)
afw.call_gemini_json = _GeminiFake({"candidates": [GUELTIGER_KANDIDAT]})
af_budget.call_gemini_json = _bz2
post(BODY_SCHWACH)   # ohne Budget
check("S: kein Budget -> 0 Budget-Gemini-Calls (auch bei aktivem Web-Fallback)",
      _bz2.n == 0)

# Normalfall: gute Coverage, kein Budget -> gar keine externen Calls.
_reset_limiters()
tav_null, gem_null, bz_null = _TavilyFake(EVIDENZEN), _GeminiFake(), _BudgetZaehler()
afw.tavily_search_mit_status = tav_null
afw.call_gemini_json = gem_null
af_budget.call_gemini_json = bz_null
post(BODY_STARK)
check("Kostendeckel: gute Coverage + kein Budget -> Tavily 0, Discovery-Gemini 0, "
      "Budget-Gemini 0", tav_null.n == 0 and gem_null.n == 0 and bz_null.n == 0)


# ══════════════════════════════════════════════════════════════════════════
# T) gleiche Mocks + gleicher Request -> deterministische Ergebnisse
# ══════════════════════════════════════════════════════════════════════════
_body_t = {"kraftstoff": ["Diesel"], "karosserie": ["kombi"],
           "getriebe": ["automatik"], "leistung_min_ps": 150,
           "marken_bevorzugt": ["Renault"]}
_reset_limiters()
_setze_fakes(_TavilyFake(EVIDENZEN), _GeminiFake({"candidates": [GUELTIGER_KANDIDAT]}))
_t1 = post(_body_t).json()
_reset_limiters()
_setze_fakes(_TavilyFake(EVIDENZEN), _GeminiFake({"candidates": [GUELTIGER_KANDIDAT]}))
_t2 = post(_body_t).json()
check("T: identische Mocks + identischer Request -> identische Reihenfolge",
      [k["candidate_id"] for k in _t1["kandidaten"]]
      == [k["candidate_id"] for k in _t2["kandidaten"]])
check("T: auch match_score bleibt stabil identisch",
      [k["match_score"] for k in _t1["kandidaten"]]
      == [k["match_score"] for k in _t2["kandidaten"]])


# ══════════════════════════════════════════════════════════════════════════
# U) keine Marktpreise in der Response
# ══════════════════════════════════════════════════════════════════════════
check("U: market_price_min/max/median/data_quality/sample_size bleiben None — "
      "auch fuer web_discovered-Kandidaten",
      all(k["market_price_min"] is None and k["market_price_max"] is None
          and k["market_price_median"] is None and k["market_data_quality"] is None
          and k["market_sample_size"] is None for k in _t1["kandidaten"]))
check("U: kein Preis-Feld im Kandidatenschema",
      all("preis" not in schluessel.lower() and "price" not in schluessel.lower()
          or schluessel.startswith("market_")
          for k in _t1["kandidaten"] for schluessel in k))


# ══════════════════════════════════════════════════════════════════════════
# V) keine Marktportal-URLs — doppelt gesichert
# ══════════════════════════════════════════════════════════════════════════
_PORTALE = ("mobile.de", "autoscout24", "kleinanzeigen", "autouncle")
_alle_urls = [u for k in _t1["kandidaten"] for u in k["source_urls"]]
check("V: keine Marktportal-URL in den ausgelieferten Belegen",
      not any(p in u.lower() for u in _alle_urls for p in _PORTALE))

# Sperre 1: Marktplaetze werden Tavily gar nicht erst zugemutet.
_reset_limiters()
tav_v = _TavilyFake(EVIDENZEN)
_setze_fakes(tav_v, _GeminiFake({"candidates": []}))
post(BODY_LEER)
_excl = " ".join(tav_v.exclude_domains).lower()
check("V: Tavily-Anfrage schliesst Marktplatz-Domains ausdruecklich aus",
      all(p in _excl for p in ("mobile.de", "autoscout24", "kleinanzeigen")))

# Sperre 2: selbst wenn ein Marktplatz-Treffer durchkaeme, lehnt das Gate ab.
_EV_MARKTPLATZ = [{
    "url": "https://www.mobile.de/fahrzeuge/details.html?id=123456789",
    "title": "Renault Megane Grandtour Blue dCi 150 Automatik",
    "content": "Renault Megane Grandtour, Diesel, 150 PS, Automatik, 2018.",
}]
_k_v, _g_v = afw._pruefe_kandidat({**GUELTIGER_KANDIDAT, "evidence": [1]}, _EV_MARKTPLATZ)
check("V: Kandidat, der sich auf eine Marktplatz-Quelle stuetzt -> abgelehnt",
      _k_v is None and "Marktplatz" in _g_v)


# ══════════════════════════════════════════════════════════════════════════
# W) Kurze Modellnamen — Belegabgleich auf Wortgrenzen statt Substring
# ══════════════════════════════════════════════════════════════════════════
# Ohne Wortgrenzen wuerde "i3" auch in "i30" und "C4" auch in "C4H" als belegt
# gelten. Weil dieser Abgleich Teil des Safety-Gates ist, wird er hier direkt
# und vollstaendig geprueft.
def _match(begriff, text):
    return afw._kommt_vor(begriff, afw._normalisiere_beleg(text))


check("W-A: echtes 'BMW i3' -> Match",
      _match("i3", "BMW i3 Elektro 170 PS Baujahr 2018"))
check("W-B: 'Hyundai i30' bestaetigt NICHT 'i3'",
      not _match("i3", "Hyundai i30 Kombi Diesel 136 PS"))
check("W-C: echtes 'Citroën C4' -> Match",
      _match("C4", "Citroën C4 BlueHDi 130 Diesel"))
check("W-C2: Unicode-/Bindestrich-Schreibweisen werden gefaltet "
      "('Citroën' findet 'CITROEN-C4')",
      _match("Citroën", "CITROEN-C4 PureTech 130"))
check("W-D: eingebettetes 'C4H' bestaetigt NICHT 'C4'",
      not _match("C4", "Renault C4H Limousine Studie"))
check("W-E: echtes 'Audi A3' -> Match",
      _match("A3", "Audi A3 Sportback 35 TDI 150 PS"))
check("W-F: 'A31' bestaetigt NICHT 'A3'", not _match("A3", "Audi A31 Studie"))
check("W-F2: 'A3X' bestaetigt NICHT 'A3'", not _match("A3", "Audi A3X Concept"))
check("W-G: normale, lange Modellnamen weiterhin unveraendert erkannt",
      _match("Megane", "Renault Megane Grandtour Blue dCi 150")
      and _match("Grandtour", "Renault Megane Grandtour Blue dCi 150"))
check("W: auch Zahlen werden auf Wortgrenzen geprueft — '150' gilt NICHT "
      "als belegt durch '1500'",
      not _match("150", "Drehmoment 1500 Nm") and _match("150", "Leistung 150 PS"))
check("W: leerer/nur-Sonderzeichen-Begriff gilt nie als belegt",
      not _match("", "irgendein text") and not _match("---", "irgendein text"))

# Gate-Ebene: ein Kandidat "BMW i3", dessen einziger Beleg von einem i30
# handelt, muss abgelehnt werden.
_EV_I30 = [{
    "url": "https://www.hyundai.de/modelle/i30/",
    "title": "Hyundai i30 Kombi — Technische Daten",
    "content": "Der Hyundai i30 Kombi ist als Diesel mit 136 PS erhaeltlich, Baujahr ab 2018.",
}]
_k_w, _g_w = afw._pruefe_kandidat({
    "marke": "BMW", "modell": "i3", "generation": "I01", "motor": "i3",
    "baujahr_von": 2018, "kraftstoff": "Diesel", "leistung_ps": 136,
    "evidence": [1],
}, _EV_I30)
check("W: Kandidat 'BMW i3', belegt nur durch einen Hyundai-i30-Treffer -> "
      "abgelehnt (kein Substring-Fehltreffer)", _k_w is None)


# ══════════════════════════════════════════════════════════════════════════
# Quellenlage: eine einzelne Nicht-Primaerquelle reicht NICHT
# ══════════════════════════════════════════════════════════════════════════
_EV_EINZEL_SCHWACH = [{
    "url": "https://irgendein-blog.example.com/kombis",
    "title": "Renault Megane Grandtour",
    "content": "Renault Megane Grandtour Diesel 150 PS Automatik 2018 Kombi.",
}]
_k_s1, _g_s1 = afw._pruefe_kandidat({**GUELTIGER_KANDIDAT, "evidence": [1]},
                                     _EV_EINZEL_SCHWACH)
check("Quellenlage: eine einzige unbekannte Quelle traegt keinen Kandidaten",
      _k_s1 is None and "Primaerquelle" in _g_s1)

_k_s2, _g_s2 = afw._pruefe_kandidat({**GUELTIGER_KANDIDAT, "evidence": [1]},
                                     [EV_HERSTELLER])
check("Quellenlage: eine einzelne HERSTELLER-Quelle traegt den Kandidaten "
      "(Confidence dann nur LOW)",
      _k_s2 is not None and _k_s2.discovery_confidence == afw.CONF_LOW)


# ══════════════════════════════════════════════════════════════════════════
# Struktur: kein Marktpreis-/Portalpfad im Modul, Foundation unveraendert
# ══════════════════════════════════════════════════════════════════════════
_web_quelle = inspect.getsource(afw)
check("Struktur: autofinder_web nutzt KEINE Marktpreis-Module "
      "(marktvergleich/marktrecherche/preisurteil)",
      not any(m in _web_quelle for m in
              ("marktvergleich", "marktrecherche", "preisurteil", "market_data_provider")))
check("Struktur: autofinder_web baut keinen eigenen genai.Client",
      "genai.Client(" not in _web_quelle)
check("Struktur: Web-Kandidaten nutzen dieselben Foundation-Hardfilter "
      "(kein zweiter, milderer Filterpfad)",
      "erfuellt_harte_filter" in _web_quelle and "_engine_score" in _web_quelle)


# ══════════════════════════════════════════════════════════════════════════
# Coverage-Gate direkt (Einheiten-Ebene)
# ══════════════════════════════════════════════════════════════════════════
_leer_req = _FakeReq([])
check("Coverage: 0 interne Kandidaten -> Web, Grund no_internal_match",
      afw.braucht_web_fallback([], _leer_req, _marken)
      == (True, afw.GRUND_KEIN_INTERNER_TREFFER))
check("Coverage: 2 interne Kandidaten -> Web, Grund geringe_interne_coverage",
      afw.braucht_web_fallback([object(), object()], _leer_req, _marken)
      == (True, afw.GRUND_GERINGE_COVERAGE))
check("Coverage: 3 interne Kandidaten -> KEIN Web",
      afw.braucht_web_fallback([object()] * 3, _leer_req, _marken) == (False, None))


print()
if FEHLER:
    print(f"{len(FEHLER)} Test(s) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle AutoFinder-Web-Fallback-Tests bestanden.")
