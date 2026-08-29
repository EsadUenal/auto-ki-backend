"""
P1 — Gemini 504 DEADLINE_EXCEEDED wird wie 503 als transient behandelt.

Hintergrund (Root-Cause-Audit): Ein `ServerError(504)` ist weder
`GeminiFehlgeschlagen` noch `RechercheUnzureichend`. Er lief deshalb an JEDEM
Router-`except` vorbei bis in `main.generic_exception_handler` -> HTTP 500,
OHNE `refund_check_credit`. Zusaetzlich wiederholte ihn niemand: das SDK-eigene
Retry ist mangels `retry_options` deaktiviert, und VIRAs `with_retry` kannte nur
503.

Deterministisch: KEIN Netzwerk, KEIN echter Gemini-Call, keine 90-Sekunden-Calls.

    python test_gemini_deadline_retry.py
"""
import asyncio
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

import httpx
from google.genai.errors import ServerError, ClientError

import app.gemini_retry as gr
from app.gemini_retry import (
    with_retry, with_retry_sync, GeminiFehlgeschlagen,
    GeminiVoruebergehendNichtErreichbar, RateLimitExhausted,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def server_error(code: int, status: str) -> ServerError:
    body = {"error": {"code": code, "message": "simuliert", "status": status}}
    resp = httpx.Response(code, json=body, request=httpx.Request("POST", "https://x"))
    return ServerError(code, body, resp)


def client_error(code: int, status: str) -> ClientError:
    body = {"error": {"code": code, "message": "simuliert", "status": status}}
    resp = httpx.Response(code, json=body, request=httpx.Request("POST", "https://x"))
    return ClientError(code, body, resp)


# Backoff neutralisieren — die Wartezeiten selbst sind nicht Gegenstand des Tests
# und wuerden den Lauf unnoetig um Minuten verlaengern.
_ECHT_SLEEP_ASYNC, _ECHT_SLEEP_SYNC = asyncio.sleep, gr.time.sleep


async def _kein_schlaf(_s):
    return None


gr.asyncio.sleep = _kein_schlaf
gr.time.sleep = lambda _s: None


class Zaehler:
    """Callable, das eine feste Fehlerfolge abspielt und die Aufrufe zaehlt."""

    def __init__(self, fehler_folge, danach="OK"):
        self.folge = list(fehler_folge)
        self.danach = danach
        self.aufrufe = 0

    async def acall(self):
        self.aufrufe += 1
        if self.folge:
            raise self.folge.pop(0)
        return self.danach

    def scall(self):
        self.aufrufe += 1
        if self.folge:
            raise self.folge.pop(0)
        return self.danach


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 0) Klassifikation ===")

check("0.1 503 UNAVAILABLE gilt als transient",
      gr._ist_transienter_serverfehler(server_error(503, "UNAVAILABLE")))
check("0.2 504 DEADLINE_EXCEEDED gilt als transient",
      gr._ist_transienter_serverfehler(server_error(504, "DEADLINE_EXCEEDED")))
check("0.3 500 INTERNAL gilt NICHT als transient",
      not gr._ist_transienter_serverfehler(server_error(500, "INTERNAL")))
check("0.4 502 BAD_GATEWAY gilt NICHT als transient",
      not gr._ist_transienter_serverfehler(server_error(502, "BAD_GATEWAY")))
check("0.5 Retry-Budget unveraendert (keine zweite Architektur)",
      gr.MAX_RETRIES_503 == 5)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== A) 503 wird weiterhin retryt (keine Regression) ===")

z = Zaehler([server_error(503, "UNAVAILABLE")] * 2)
erg = asyncio.run(with_retry(z.acall))
check("A1 503 -> Retry, dann Erfolg", erg == "OK")
check("A2 genau 3 Aufrufe (2 Fehler + 1 Erfolg)", z.aufrufe == 3)

z = Zaehler([server_error(503, "UNAVAILABLE")] * 99)
try:
    asyncio.run(with_retry(z.acall))
    dauerhaft_503 = None
except GeminiVoruebergehendNichtErreichbar as e:
    dauerhaft_503 = e
check("A3 dauerhaftes 503 -> GeminiVoruebergehendNichtErreichbar",
      dauerhaft_503 is not None)
check("A4 Versuche gedeckelt auf MAX_RETRIES_503", z.aufrufe == gr.MAX_RETRIES_503)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== B) 504 DEADLINE_EXCEEDED wird jetzt retryt ===")

z = Zaehler([server_error(504, "DEADLINE_EXCEEDED")] * 2)
erg = asyncio.run(with_retry(z.acall))
check("B1 504 -> Retry statt sofortigem Durchreichen", z.aufrufe == 3)
check("B2 Ergebnis wird geliefert", erg == "OK")

zs = Zaehler([server_error(504, "DEADLINE_EXCEEDED")])
check("B3 sync-Pfad retryt 504 ebenfalls",
      with_retry_sync(zs.scall) == "OK" and zs.aufrufe == 2)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== C) 504 beim ersten Versuch, danach Erfolg ===")

z = Zaehler([server_error(504, "DEADLINE_EXCEEDED")])
erg = asyncio.run(with_retry(z.acall))
check("C1 Request endet erfolgreich", erg == "OK")
check("C2 genau 2 Aufrufe", z.aufrufe == 2)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== D) Dauerhafter 504 -> kontrollierter Provider-Fehler ===")

z = Zaehler([server_error(504, "DEADLINE_EXCEEDED")] * 99)
fehler = None
try:
    asyncio.run(with_retry(z.acall))
except Exception as e:
    fehler = e
check("D1 endet in GeminiVoruebergehendNichtErreichbar",
      isinstance(fehler, GeminiVoruebergehendNichtErreichbar))
check("D2 ist ein GeminiFehlgeschlagen (Router faengt es)",
      isinstance(fehler, GeminiFehlgeschlagen))
check("D3 KEIN roher ServerError mehr", not isinstance(fehler, ServerError))
check("D4 Versuche gedeckelt", z.aufrufe == gr.MAX_RETRIES_503)
check("D5 Fehlertext nennt den echten Code", "504" in str(fehler))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== H) Andere 5xx werden NICHT blind als transient behandelt ===")

for code, status in ((500, "INTERNAL"), (502, "BAD_GATEWAY")):
    z = Zaehler([server_error(code, status)] * 99)
    roh = None
    try:
        asyncio.run(with_retry(z.acall))
    except Exception as e:
        roh = e
    check(f"H1 {code} wird sofort durchgereicht (kein Retry)", z.aufrufe == 1)
    check(f"H2 {code} bleibt ServerError, wird NICHT als transient maskiert",
          isinstance(roh, ServerError) and not isinstance(roh, GeminiFehlgeschlagen))

z = Zaehler([client_error(429, "RESOURCE_EXHAUSTED")] * 99)
r429 = None
try:
    asyncio.run(with_retry(z.acall))
except Exception as e:
    r429 = e
check("H3 429 weiterhin eigener Pfad (RateLimitExhausted)",
      isinstance(r429, RateLimitExhausted))

z = Zaehler([client_error(400, "INVALID_ARGUMENT")] * 99)
r400 = None
try:
    asyncio.run(with_retry(z.acall))
except Exception as e:
    r400 = e
check("H4 400 sofort durchgereicht", z.aufrufe == 1 and isinstance(r400, ClientError))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== I) Gemischte transiente Folge (503 -> 504 -> Erfolg) ===")

z = Zaehler([server_error(503, "UNAVAILABLE"), server_error(504, "DEADLINE_EXCEEDED")])
erg = asyncio.run(with_retry(z.acall))
check("I1 503 und 504 teilen sich dasselbe Budget", erg == "OK" and z.aufrufe == 3)


gr.asyncio.sleep, gr.time.sleep = _ECHT_SLEEP_ASYNC, _ECHT_SLEEP_SYNC

print()
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE GEMINI-DEADLINE-RETRY-TESTS GRUEN")
