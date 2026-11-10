"""
N19 -- Budget-Skips in der ERSTEN Suchrunde und in der Rückrufphase (zusätzlich zur Nachsuche).

Je Phase (Identität Runde 1, Rückruf, Technik Runde 1) werden getrennt gemeldet: geplant,
ausgeführt, budgetbedingt übersprungen (Erstversuch am Budget gescheitert) und am Budget
gescheiterter RETRY (Erstversuch lief -> gilt als ausgeführt). Echter Pfad
tavily_search -> claim_call mit gestubbtem HTTP; Netzsperre und Temp-DB vor dem App-Import.

    python test_n19_budget_weitere_phasen.py
"""
import asyncio
import logging
import os
import socket
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="n19_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


# Windows baut den asyncio-Wakeup per Loopback-socketpair: nur DIESE Standardbibliotheks-
# Operation ist erlaubt (wie scripts/test_isolated.py), jede andere Verbindung ist gesperrt.
import threading  # noqa: E402

_paar = threading.local()
_orig_paar, _orig_connect = socket.socketpair, socket.socket.connect


def _socketpair(*a, **k):
    _paar.aktiv = True
    try:
        return _orig_paar(*a, **k)
    finally:
        _paar.aktiv = False


def _connect(sock, adresse):
    if getattr(_paar, "aktiv", False):
        return _orig_connect(sock, adresse)
    return _gesperrt()


socket.socketpair, socket.socket.connect = _socketpair, _connect
socket.create_connection = _gesperrt
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402

from app import database as _db  # noqa: E402

_db.ensure_tables()   # Temp-DB (AUTO_KI_DB_PATH oben), nie die Live-DB

import app.kaufcheck as kc  # noqa: E402
import app.provider_control as pc  # noqa: E402
import app.technical_research as tr  # noqa: E402
import app.web_search as ws  # noqa: E402
from app.config import PROVIDER_FEATURE_LIMITS  # noqa: E402
from app.models import KaufCheckRequest, KaufCheckResponse  # noqa: E402

_FEHLER: list[str] = []


def check(name, bedingung, detail=""):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


# ── 1) Echter Pfad: tavily_search -> claim_call (Tavily-HTTP durch Fake ersetzt) ──
class FakeClient:
    calls = 0

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json):
        type(self).calls += 1
        return httpx.Response(200, json={"results": [{"url": "https://example.test/a", "title": "t",
                                                      "content": "c"}]},
                              request=httpx.Request("POST", url))


async def echter_pfad():
    real = (ws.httpx.AsyncClient, ws.TAVILY_API_KEY)
    ws.httpx.AsyncClient, ws.TAVILY_API_KEY = FakeClient, "fake"
    PROVIDER_FEATURE_LIMITS["n19_eins"] = {"gemini": 5, "tavily": 1}
    try:
        ws._cache.clear()
        async with pc.provider_scope("n19_eins", key="n19-a") as scope:
            erste = await ws.tavily_search("erste anfrage", bypass_cache=True)
            zweite = await ws.tavily_search("zweite anfrage", bypass_cache=True)
            check("Echter Pfad: bei Budget 1 findet genau EIN Aufruf statt", FakeClient.calls == 1,
                  str(FakeClient.calls))
            check("Echter Pfad: der erste Aufruf liefert sein Ergebnis (vorhandene Ergebnisse erhalten)",
                  len(erste) == 1)
            check("Echter Pfad: der zweite liefert []", zweite == [])
            check("Echter Pfad: der Zähler meldet den budgetbedingten Skip",
                  pc.budget_uebersprungen("tavily") == 1 and scope.skipped["tavily"] == 1
                  and scope.calls["tavily"] == 1,
                  f"skipped={scope.skipped} calls={scope.calls}")
        check("Ohne Scope: Zähler ist 0", pc.budget_uebersprungen("tavily") == 0)
        check("Limits unverändert (kaufcheck/tavily)", PROVIDER_FEATURE_LIMITS["kaufcheck"]["tavily"] == 22,
              str(PROVIDER_FEATURE_LIMITS["kaufcheck"]))
    finally:
        ws.httpx.AsyncClient, ws.TAVILY_API_KEY = real


# ── 2) Rundenzählung im echten run_kaufcheck-Pfad (RC F, DB-Miss) ──────────────
def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


RK_URL = ("https://www.lexus.de/content/dam/lexus/nmsc/germany/lexus-besitzer/"
          "fahrzeuginformationen/RCF_tcm-3150-1284184.pdf")
RK_DUENN = "12V Spannungsversorgung trennen\nHinweise zum Transport\nZugang 12V Batterie"
RK_VOLL = ("Lexus RC F\n(UXC1 (EU, M), ab 09/2014)\nStand: 10/2014\n"
           "Lexus RC F (UXC1 (EU, ab 09/2014)\n12V Spannungsversorgung trennen\n"
           "Hinweise zum Transport\nZugang 12V Batterie\nLexus RC F\n(UXC1 (EU, M), ab 09/2014)")
ADAC = treffer(
    "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/",
    "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25): Technische Daten | ADAC",
    "Marke Lexus Modell RC F Typ Ultimate Edition Automatik Baureihe RC (UXC1) "
    "(12/14 - 06/25) Herstellerinterne Baureihenbezeichnung UXC1 Motorcode 2UR-GSE "
    "Leistung maximal in PS 464 Antriebsart Hinterrad Getriebeart Automatikgetriebe")
AMS = treffer("https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
              "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
              "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Leistung 181 PS bis "
              "477 PS. Hubraum 1998 cm3 bis 4969 cm3. LEXUS RC F TRACK EDITION IM SUPERTEST: "
              "Was kann der V8-Sauger auf der Rennstrecke? LEXUS LC 500 UND RC F: "
              "V8-Sportler auf Abschiedstour.")
REQ = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000, motor="5.0 V8",
                       kraftstoff="Benzin", leistung_ps=477, getriebe="Automatik", antrieb="Heck",
                       ausstattung=[])
# Echter Live-Treffer (08.11.2026, tests/fixtures): ein Forenbeitrag, der in Runde 1 kuratiert, aber
# fachlich verworfen wird -> Nachsuche (null_kandidaten) wie im Live-Lauf.
import json  # noqa: E402
from pathlib import Path  # noqa: E402

_WART = json.loads((Path(__file__).parent / "tests" / "fixtures"
                    / "live_wartung_treffer_2026-11-08.json").read_text(encoding="utf-8"))
FORUM = next(treffer(h["url"], h["title"], h["content"] or "")
             for h in _WART["ohne UXC1"] if "motor-talk" in h["url"])
SW_R2 = ("Werkstattinformation technischer Hinweis", "technical service bulletin OR manufacturer")


class _LogFang(logging.Handler):
    def __init__(self):
        super().__init__()
        self.zeilen: list[str] = []

    def emit(self, record):
        self.zeilen.append(record.getMessage())


def db_miss(marke, modell, baujahr):
    return None, {"match_art": "no_match", "konfidenz": "niedrig", "belastbar": False,
                  "fehlende_angabe": "simulierter DB-Miss, Testdouble"}


_ECHTER_SCHLAF = asyncio.sleep   # vor dem Patch von ws.asyncio.sleep gesichert (selbes Modul)


class HttpFake:
    """Gestubbtes Tavily-HTTP: der ECHTE Pfad tavily_search -> claim_call -> post läuft."""
    posts: list[str] = []
    erste_antwort_429_bei: str | None = None
    fehler_400_bei: str | None = None
    _schon_429: set = set()
    routing: dict = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json):
        q = json["query"]
        cls = type(self)
        await _ECHTER_SCHLAF(0)    # Kontrollabgabe wie bei echtem Netzwerk: parallele Anfragen verschränken sich
        cls.posts.append(q)
        req = httpx.Request("POST", url)
        if cls.fehler_400_bei and cls.fehler_400_bei in q:
            return httpx.Response(400, json={"error": "bad request"}, request=req)
        if cls.erste_antwort_429_bei and cls.erste_antwort_429_bei in q and q not in cls._schon_429:
            cls._schon_429.add(q)
            return httpx.Response(429, json={"error": "busy"}, request=req)
        liste = []
        for teil, hits in cls.routing.items():
            if teil in q:
                liste = hits
                break
        return httpx.Response(200, json={"results": [
            {"url": h["url"], "title": h["title"], "content": h["content"]} for h in liste]}, request=req)


async def lauf_http(*, tavily_budget=None, retry_429_bei=None, fehler_400_bei=None):
    """Wie lauf(), aber über den echten Suchpfad (web_search) mit gestubbtem HTTP."""
    HttpFake.posts, HttpFake._schon_429 = [], set()
    HttpFake.erste_antwort_429_bei = retry_429_bei
    HttpFake.fehler_400_bei = fehler_400_bei
    HttpFake.routing = {"Generation Modellcode": [treffer(RK_URL, "Lexus RC F Rettungskarte", RK_DUENN)],
                        "technische Daten": [ADAC], "Generation Baureihe Bauzeitraum": [AMS],
                        "bekannte Probleme Schwachstellen": [FORUM]}
    ws._cache.clear()

    async def kein_schlaf(_):
        await _ECHTER_SCHLAF(0)

    echt = (ws.httpx.AsyncClient, ws.TAVILY_API_KEY, ws.asyncio.sleep)
    ws.httpx.AsyncClient, ws.TAVILY_API_KEY, ws.asyncio.sleep = HttpFake, "fake", kein_schlaf
    try:
        r, q, a, z = await lauf(tavily_budget=tavily_budget, http=True)
    finally:
        ws.httpx.AsyncClient, ws.TAVILY_API_KEY, ws.asyncio.sleep = echt
    return r, list(HttpFake.posts), z


async def lauf(*, tavily_budget=None, fehler_bei=None, nachsuche_treffer=None, http=False):
    """tavily_budget: Anzahl erlaubter Calls (None = unbegrenzt, kein Scope).
    fehler_bei: Teilstring einer Query, bei der der Provider eine Exception wirft.
    nachsuche_treffer: Treffer für die Schwachstellen-Nachsuche (echter Nulltreffer = [])."""
    routing = {"Generation Modellcode": [treffer(RK_URL, "Lexus RC F Rettungskarte", RK_DUENN)],
               "technische Daten": [ADAC], "Generation Baureihe Bauzeitraum": [AMS],
               "bekannte Probleme Schwachstellen": [FORUM]}
    queries: list[str] = []
    ausgefuehrt: list[str] = []

    async def search(query, *, count=8, exclude_domains=None, include_domains=None):
        queries.append(query)
        # Spiegel von web_search: der Budget-Claim passiert VOR dem Call; ist es erschöpft,
        # kommt [] zurück (ProviderCallLimitExceeded wird dort abgefangen).
        try:
            pc.claim_call("tavily")
        except pc.ProviderCallLimitExceeded:
            return []
        ausgefuehrt.append(query)
        if fehler_bei and fehler_bei in query:
            raise RuntimeError("simulierter Providerfehler")
        if any(s in query for s in SW_R2):
            return list(nachsuche_treffer or [])
        for teil, liste in routing.items():
            if teil in query:
                return [dict(h) for h in liste]
        return []

    async def extract(urls):
        gefunden = {u: RK_VOLL for u in urls if u == RK_URL}
        return gefunden, {"angefragt": len(urls), "aus_cache": 0, "extract_calls": 1,
                          "extract_urls": len(urls), "erfolge": len(gefunden),
                          "fehler": len(urls) - len(gefunden)}

    orig = (kc.call_gemini_json, kc.tavily_search_with_fallback, kc.marktpreis_recherche_moeglich,
            kc.vertiefe_marktrecherche, kc.find_baureihe_mit_vertrauen, tr.tavily_search, tr.hole_raw_content)

    async def gemini(system, user):
        return {"bericht": "x", "empfehlung": "kaufen_nach_besichtigung",
                "risiko_evidence_ids": [], "empfehlung_evidence_ids": []}

    async def verboten(*a, **k):
        raise AssertionError("Marktprovider darf nicht laufen")

    kc.call_gemini_json = gemini
    kc.tavily_search_with_fallback = kc.vertiefe_marktrecherche = verboten
    kc.marktpreis_recherche_moeglich = lambda *a: False
    kc.find_baureihe_mit_vertrauen = db_miss
    if http:
        tr.hole_raw_content = extract      # tr.tavily_search bleibt der ECHTE Suchpfad
    else:
        tr.tavily_search, tr.hole_raw_content = search, extract
    fang, logger = _LogFang(), logging.getLogger("app.technical_research")
    alt = logger.level
    logger.addHandler(fang)
    logger.setLevel(logging.INFO)
    try:
        if tavily_budget is None:
            r = KaufCheckResponse(**await kc.run_kaufcheck(REQ))
        else:
            PROVIDER_FEATURE_LIMITS["n19_run"] = {"gemini": 50, "tavily": tavily_budget}
            async with pc.provider_scope("n19_run", key="n19-run"):
                r = KaufCheckResponse(**await kc.run_kaufcheck(REQ))
    finally:
        logger.removeHandler(fang)
        logger.setLevel(alt)
        (kc.call_gemini_json, kc.tavily_search_with_fallback, kc.marktpreis_recherche_moeglich,
         kc.vertiefe_marktrecherche, kc.find_baureihe_mit_vertrauen, tr.tavily_search,
         tr.hole_raw_content) = orig
    runde = [z for z in fang.zeilen if z.startswith("research_phase=schwachstelle round=2")]
    global _LETZTE_ZEILEN
    _LETZTE_ZEILEN = list(fang.zeilen)
    return r, queries, ausgefuehrt, runde


_LETZTE_ZEILEN: list[str] = []


def technik_status():
    z = next((x for x in _LETZTE_ZEILEN if x.startswith("Technische Recherche:")), "")
    return "failed" if "'technik': 'failed'" in z else ("partial" if "'technik': 'partial'" in z else z)


def sw_r2(qs):
    return [q for q in qs if any(s in q for s in SW_R2)]


PHASEN = {
    # phase: (Marker der ERSTEN Anfrage, Anzahl der Anfragen der Runde, 429-Marker der ersten Anfrage)
    "identitaet": ("477 PS technische Daten", 2, "477 PS technische Daten"),
    "rueckruf": ("UXC1 Rückruf", 2, "Lexus RC F UXC1 Rückruf"),
    "technik": ("bekannte Probleme Schwachstellen", 4, "bekannte Probleme Schwachstellen"),
}


def runden_zeile(phase, runde=1):
    return next((z for z in _LETZTE_ZEILEN if z.startswith(f"research_phase={phase} round={runde} geplant=")), "")


def eintraege(r, phase):
    return [e for e in r.nachsuche_uebersprungen if e["kategorie"] == phase]


async def tests():
    ref, posts0, _ = await lauf_http()
    for ph, (marker, n, _m) in PHASEN.items():
        zz = runden_zeile(ph)
        check(f"Referenz {ph}: Zeile geplant={n} ausgefuehrt={n} budget_uebersprungen=0 retry_budget_abgelehnt=0",
              f"geplant={n} ausgefuehrt={n} budget_uebersprungen=0 retry_budget_abgelehnt=0" in zz, zz)
    # Echter Nulltreffer: im Referenzlauf liefert die gestubbte Suche für die Rückruf-Anfragen (und die
    # Gegenfälle dazu) eine LEERE Trefferliste; sie zählt als ausgeführt, nicht als übersprungen.
    check("Referenz (echte Nulltreffer in der Rückrufphase): ausgeführt, kein Skip, kein Hinweis",
          "ausgefuehrt=2 budget_uebersprungen=0" in runden_zeile("rueckruf") and not ref.nachsuche_uebersprungen)
    check("Referenz: nichts übersprungen, kein Budgethinweis im Bericht",
          ref.nachsuche_uebersprungen == [] and "Anfragebudget" not in ref.bericht)
    check("Referenz: Nachsuche (Schwachstellen Runde 2) wie bisher gezählt",
          "geplant=2 ausgefuehrt=2 budget_uebersprungen=0 retry_budget_abgelehnt=0" in
          next((z for z in _LETZTE_ZEILEN if z.startswith("research_phase=schwachstelle round=2")), ""))

    pos = {ph: next(i for i, q in enumerate(posts0) if marker in q) for ph, (marker, _n, _m) in PHASEN.items()}

    for ph, (marker, n, m429) in PHASEN.items():
        # (a) Erstversuch am Budget blockiert: genau EIN Aufruf der Runde findet statt
        r, posts, _ = await lauf_http(tavily_budget=pos[ph] + 1)
        zz = runden_zeile(ph)
        check(f"{ph}: blockierter Erstversuch -> geplant={n} ausgefuehrt=1 budget_uebersprungen={n - 1} retry=0",
              f"geplant={n} ausgefuehrt=1 budget_uebersprungen={n - 1} retry_budget_abgelehnt=0" in zz, zz)
        e = eintraege(r, ph)
        check(f"{ph}: API-Feld meldet die Runde als unvollständig",
              len(e) == 1 and e[0]["runde"] == 1 and e[0]["geplant"] == n and e[0]["ausgefuehrt"] == 1
              and e[0]["budget_uebersprungen"] == n - 1, str(e))
        check(f"{ph}: Bericht nennt die Runde (kein leeres Ergebnis, kein Fehler)",
              f"Die Suche zu " in r.bericht and "wurde nur teilweise ausgeführt" in r.bericht
              and "Abdeckung ist unvollständig" in r.bericht)
        check(f"{ph}: der ausgeführte Aufruf der Runde fand statt (vorhandene Treffer erhalten)",
              sum(1 for q in posts if marker in q) >= 1)

        # (b) Retry am Budget blockiert: Erstversuche aller Anfragen liefen -> ausgeführt
        r2, posts2, _ = await lauf_http(tavily_budget=pos[ph] + n, retry_429_bei=m429)
        z2 = runden_zeile(ph)
        check(f"{ph}: blockierter Retry -> ausgefuehrt={n} budget_uebersprungen=0 retry_budget_abgelehnt=1",
              f"geplant={n} ausgefuehrt={n} budget_uebersprungen=0 retry_budget_abgelehnt=1" in z2, z2)
        check(f"{ph}: blockierter Retry erzeugt KEINEN Eintrag im API-Feld", eintraege(r2, ph) == [])

        # (c) Providerfehler (HTTP 400): Aufruf lief, kein Budget-Skip
        r3, _p3, _ = await lauf_http(fehler_400_bei=m429)
        z3 = runden_zeile(ph)
        check(f"{ph}: Providerfehler -> ausgefuehrt={n}, KEIN Budget-Skip", f"budget_uebersprungen=0" in z3 and
              f"ausgefuehrt={n}" in z3 and eintraege(r3, ph) == [], z3)

    # Bestehende Nachsuche-Zählung bleibt (Schwachstellen Runde 2, Budget für genau einen Aufruf)
    erste = next(i for i, q in enumerate(posts0) if any(t in q for t in SW_R2))
    r4, posts4, _ = await lauf_http(tavily_budget=erste + 1)
    z4 = next((z for z in _LETZTE_ZEILEN if z.startswith("research_phase=schwachstelle round=2")), "")
    check("Bestehende Nachsuche-Zählung (Schwachstellen Runde 2) unverändert",
          "geplant=2 ausgefuehrt=1 budget_uebersprungen=1 retry_budget_abgelehnt=0" in z4, z4)
    sk = [e for e in r4.nachsuche_uebersprungen if e["kategorie"] == "schwachstelle"]
    check("Bestehendes API-Feld der Nachsuche unverändert (Runde 2)",
          len(sk) == 1 and sk[0]["runde"] == 2 and sk[0]["budget_uebersprungen"] == 1, str(sk))
    check("Empfehlung/Status in den Budget-Fällen unverändert zur Referenz",
          (r4.empfehlung, r4.recommendation_state) == (ref.empfehlung, ref.recommendation_state))


async def main():
    await tests()


asyncio.run(main())

print()
if _FEHLER:
    print("FEHLER:", _FEHLER)
    raise SystemExit(1)
print("ALLE OK")
