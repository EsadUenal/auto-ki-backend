"""
P1 — Router-Verhalten bei dauerhaftem Gemini-504 (KaufCheck UND VerkaufsCheck).

Vor dem Fix: `ServerError(504)` war weder `GeminiFehlgeschlagen` noch
`RechercheUnzureichend` -> kein Router-`except` griff -> HTTP 500 aus
`main.generic_exception_handler`, `refund_check_credit` wurde NIE aufgerufen.
Der Nutzer verlor ein bezahltes Check-Kontingent.

Nach dem Fix endet ein dauerhafter 504 in `GeminiVoruebergehendNichtErreichbar`
(Subklasse von `GeminiFehlgeschlagen`) — die Router fangen das bereits und
erstatten zurueck.

Deterministisch: echte Router-Funktionen, echte SQLite-Testdatenbank, KEIN
Netzwerk, KEIN echter Gemini-Call.

    python test_router_504_refund.py
"""
import asyncio
import os
import sys
import io
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_504_"), "test.db")
os.environ.setdefault("AUTO_KI_CHROMA_PATH", tempfile.mkdtemp(prefix="vira_504_chroma_"))
sys.path.insert(0, ".")

import httpx
from fastapi import HTTPException
from google.genai.errors import ServerError

import app.gemini_retry as gr
import app.kaufcheck as kc
import app.verkaufscheck as vc
import app.routers.kaufcheck as r_kauf
import app.routers.verkaufscheck as r_verk
from app.database import get_conn, ensure_tables
from app.check_gate import refund_check_credit
from app.models import KaufCheckRequest, VerkaufsCheckRequest

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def server_error(code: int, status: str) -> ServerError:
    body = {"error": {"code": code, "message": "simuliert", "status": status}}
    resp = httpx.Response(code, json=body, request=httpx.Request("POST", "https://x"))
    return ServerError(code, body, resp)


# ── Testnutzer anlegen ───────────────────────────────────────────────────────
ensure_tables()
with get_conn() as conn:
    conn.execute(
        "INSERT INTO users (email, password_hash, abo_typ, checks_verbleibend) "
        "VALUES (?, ?, ?, ?)", ("t504@test.local", "x", "pro", 10))
    USER_ID = conn.execute("SELECT id FROM users WHERE email=?",
                           ("t504@test.local",)).fetchone()["id"]


def credits() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT checks_verbleibend FROM users WHERE id=?",
                            (USER_ID,)).fetchone()["checks_verbleibend"]


def dekrementiere() -> None:
    """Simuliert, was require_check_access() vor dem Handler bereits getan hat."""
    with get_conn() as conn:
        conn.execute("UPDATE users SET checks_verbleibend = checks_verbleibend - 1 "
                     "WHERE id=?", (USER_ID,))


# Backoff neutralisieren — sonst laeuft der Test minutenlang.
_ECHT_ASYNC_SLEEP = gr.asyncio.sleep


async def _kein_schlaf(_s):
    return None


gr.asyncio.sleep = _kein_schlaf

# API-Key-Pruefung im Router deaktivieren (nicht Gegenstand dieses Tests).
r_kauf.verify_api_key = lambda request: None
r_verk.verify_api_key = lambda request: None

REQ_K = KaufCheckRequest(marke="Opel", modell="Insignia", baujahr=2018,
                         kilometerstand=112000, motor="2.0 Diesel", preis_eur=13500)
REQ_V = VerkaufsCheckRequest(marke="Opel", modell="Insignia", baujahr=2018,
                             kilometerstand=112000, motor="2.0 Diesel",
                             preis_vorstellung=13500)


class _Aufrufzaehler:
    def __init__(self):
        self.n = 0

    def __call__(self, user_id):
        self.n += 1
        refund_check_credit(user_id)


def _dauerhaft_504(*args, **kwargs):
    raise server_error(504, "DEADLINE_EXCEEDED")


async def _gemini_504(system_prompt, user_msg):
    # laeuft durch with_retry -> nach MAX_RETRIES_503 Versuchen
    # GeminiVoruebergehendNichtErreichbar
    return await gr.with_retry(_dauerhaft_504)


async def _stub_recherche(initial, deep, ziel, angebot, exclude, **kw):
    from app.models import Marktanalyse
    return [], Marktanalyse(gefunden=5, verwendet=3, anzahl_sehr_aehnlich=3,
                            median_eur=13000, spanne_min_eur=12200,
                            spanne_max_eur=14000, datenqualitaet="hoch",
                            marktabdeckung="gut", anzahl_domains=2,
                            quellen_domains=["a.de", "b.de"]), {}


async def _stub_tavily(*a, **kw):
    return []


def _umgebung(modul):
    orig = (modul.call_gemini_json, modul.vertiefe_marktrecherche,
            modul.tavily_search_with_fallback, modul.TAVILY_API_KEY)
    modul.call_gemini_json = _gemini_504
    modul.vertiefe_marktrecherche = _stub_recherche
    modul.tavily_search_with_fallback = _stub_tavily
    modul.TAVILY_API_KEY = "test"
    return orig


def _zurueck(modul, orig):
    (modul.call_gemini_json, modul.vertiefe_marktrecherche,
     modul.tavily_search_with_fallback, modul.TAVILY_API_KEY) = orig


def FakeRequest():
    """Echtes starlette.Request — der `@limiter.limit`-Dekorator (slowapi) lehnt
    Attrappen ausdruecklich ab ("parameter `request` must be an instance of
    starlette.requests.Request")."""
    from starlette.requests import Request
    return Request({
        "type": "http", "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": "/api/v1/check", "raw_path": b"/api/v1/check", "query_string": b"",
        "root_path": "", "headers": [], "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80), "app": None,
    })


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== Vorbedingung ===")
check("V1 Testnutzer hat 10 Credits", credits() == 10)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== E) KaufCheck-Router: dauerhafter 504 ===")

zaehler_k = _Aufrufzaehler()
r_kauf.refund_check_credit = zaehler_k
orig = _umgebung(kc)
vorher = credits()
dekrementiere()
exc_k = None
try:
    asyncio.run(r_kauf.kaufcheck_endpunkt(REQ_K, FakeRequest(), retry=False, user_id=USER_ID))
except HTTPException as e:
    exc_k = e
except Exception as e:  # pragma: no cover
    exc_k = e
finally:
    _zurueck(kc, orig)

check("E1 Router wirft HTTPException (kein roher ServerError)",
      isinstance(exc_k, HTTPException))
check("E2 HTTP 503 statt generischem 500",
      isinstance(exc_k, HTTPException) and exc_k.status_code == 503)
check("E3 bestehende Provider-Fehlermeldung",
      isinstance(exc_k, HTTPException)
      and exc_k.detail.get("fehler", {}).get("code") == "ki_ueberlastet")
check("E4 refund_check_credit genau EINMAL aufgerufen", zaehler_k.n == 1)
check(f"E5 Credit exakt zurueckerstattet ({vorher})", credits() == vorher)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== F) VerkaufsCheck-Router: dauerhafter 504 ===")

zaehler_v = _Aufrufzaehler()
r_verk.refund_check_credit = zaehler_v
orig = _umgebung(vc)
vorher = credits()
dekrementiere()
exc_v = None
try:
    asyncio.run(r_verk.verkaufscheck_endpunkt(REQ_V, FakeRequest(), retry=False, user_id=USER_ID))
except HTTPException as e:
    exc_v = e
except Exception as e:  # pragma: no cover
    exc_v = e
finally:
    _zurueck(vc, orig)

check("F1 Router wirft HTTPException (kein roher ServerError)",
      isinstance(exc_v, HTTPException))
check("F2 HTTP 503 statt generischem 500",
      isinstance(exc_v, HTTPException) and exc_v.status_code == 503)
check("F3 bestehende Provider-Fehlermeldung",
      isinstance(exc_v, HTTPException)
      and exc_v.detail.get("fehler", {}).get("code") == "ki_ueberlastet")
check("F4 refund_check_credit genau EINMAL aufgerufen", zaehler_v.n == 1)
check(f"F5 Credit exakt zurueckerstattet ({vorher})", credits() == vorher)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== G) Keine doppelte Rueckerstattung ===")

check("G1 KaufCheck: genau ein Refund-Aufruf pro Fehlschlag", zaehler_k.n == 1)
check("G2 VerkaufsCheck: genau ein Refund-Aufruf pro Fehlschlag", zaehler_v.n == 1)
check("G3 Gesamtstand unveraendert (10) — jeder Abzug wurde genau einmal erstattet",
      credits() == 10)

# Erfolgreicher Lauf darf NICHT erstatten.
zaehler_ok = _Aufrufzaehler()
r_verk.refund_check_credit = zaehler_ok


async def _gemini_ok(system_prompt, user_msg):
    return {"bericht": "## Fahrzeug erkannt\nOpel Insignia B.\n\n## (a) Marktvergleich\nOK",
            "preis_evidence_ids": [], "strategie_evidence_ids": [], "argument_evidence_ids": []}


orig = _umgebung(vc)
vc.call_gemini_json = _gemini_ok
dekrementiere()
nach_abzug = credits()
ok_erg = None
try:
    ok_erg = asyncio.run(r_verk.verkaufscheck_endpunkt(REQ_V, FakeRequest(), retry=False,
                                                       user_id=USER_ID))
finally:
    _zurueck(vc, orig)
check("G4 erfolgreicher Check loest KEINEN Refund aus", zaehler_ok.n == 0)
check("G5 Credit bleibt abgezogen (Leistung wurde erbracht)", credits() == nach_abzug)
check("G6 Erfolgreicher Check liefert eine Antwort", ok_erg is not None)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== H) Nicht-transiente Fehler bleiben unveraendert ===")


async def _gemini_500(system_prompt, user_msg):
    return await gr.with_retry(lambda: (_ for _ in ()).throw(server_error(500, "INTERNAL")))


zaehler_500 = _Aufrufzaehler()
r_verk.refund_check_credit = zaehler_500
orig = _umgebung(vc)
vc.call_gemini_json = _gemini_500
exc_500 = None
try:
    asyncio.run(r_verk.verkaufscheck_endpunkt(REQ_V, FakeRequest(), retry=False, user_id=USER_ID))
except Exception as e:
    exc_500 = e
finally:
    _zurueck(vc, orig)
check("H1 500 wird NICHT als transienter Providerfehler maskiert",
      isinstance(exc_500, ServerError))
check("H2 500 loest keinen Refund aus (unveraendertes Bestandsverhalten)",
      zaehler_500.n == 0)


gr.asyncio.sleep = _ECHT_ASYNC_SLEEP

print()
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE ROUTER-504-REFUND-TESTS GRUEN")
