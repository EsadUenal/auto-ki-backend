"""Deterministische Provider-Reliability-/Kostenkontrolltests.

Keine echten Gemini-/Tavily-Aufrufe; alle Provider-Antworten sind lokale Fakes.
"""
from __future__ import annotations

import asyncio
import io
import sys

import httpx
from google.genai.errors import ClientError, ServerError

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

import app.gemini_retry as gr
import app.provider_control as pc
import app.web_search as ws
from app.config import PROVIDER_FEATURE_LIMITS
from app.config import (
    GEMINI_CHAT_MAX_OUTPUT_TOKENS, GEMINI_ANALYSE_MAX_OUTPUT_TOKENS,
    GEMINI_JSON_MAX_OUTPUT_TOKENS, GEMINI_MAX_INPUT_CHARS,
    PROVIDER_ADMIN_IMAGE_BATCH_MAX,
)

FEHLER: list[str] = []
PASS = 0


def check(name: str, ok: bool) -> None:
    global PASS
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}")
    if ok:
        PASS += 1
    else:
        FEHLER.append(name)


def server_error(code: int) -> ServerError:
    body = {"error": {"code": code, "message": "simuliert", "status": "UNAVAILABLE"}}
    return ServerError(code, body, httpx.Response(code, json=body, request=httpx.Request("POST", "https://x")))


def client_error(code: int, message: str = "simuliert", details: list | None = None) -> ClientError:
    body = {"error": {"code": code, "message": message,
                      "status": "RESOURCE_EXHAUSTED", "details": details or []}}
    return ClientError(code, body, httpx.Response(code, json=body, request=httpx.Request("POST", "https://x")))


class AsyncSequence:
    def __init__(self, values):
        self.values = list(values)
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


async def gemini_tests() -> None:
    real_sleep, real_timeout = gr.asyncio.sleep, gr.GEMINI_TIMEOUT_SECONDS

    async def no_sleep(_):
        return None

    gr.asyncio.sleep = no_sleep
    try:
        seq = AsyncSequence([client_error(429), "ok"])
        check("Gemini 429 temporaer: ein Retry, dann Erfolg",
              await gr.with_retry(seq) == "ok" and seq.calls == 2)

        quota = client_error(
            429, "Quota exceeded for quota metric requests per day",
            [{"retryDelay": "86400s"}],
        )
        seq = AsyncSequence([quota, "darf-nicht-passieren"])
        try:
            await gr.with_retry(seq)
            caught = None
        except Exception as exc:
            caught = exc
        check("Gemini Tagesquota: kein Retry",
              isinstance(caught, gr.GeminiQuotaErschoepft) and seq.calls == 1)

        seq = AsyncSequence([server_error(500), "ok"])
        check("Gemini 500: begrenzter Retry",
              await gr.with_retry(seq) == "ok" and seq.calls == 2)

        seq = AsyncSequence([client_error(400), "darf-nicht-passieren"])
        try:
            await gr.with_retry(seq)
            caught = None
        except Exception as exc:
            caught = exc
        check("Gemini 400: kein Retry",
              isinstance(caught, gr.GeminiPermanentFehler) and seq.calls == 1)

        seq = AsyncSequence([client_error(401), "darf-nicht-passieren"])
        try:
            await gr.with_retry(seq)
            caught = None
        except Exception as exc:
            caught = exc
        check("Gemini Auth-Fehler: kein Retry",
              isinstance(caught, gr.GeminiPermanentFehler) and seq.calls == 1)

        gr.GEMINI_TIMEOUT_SECONDS = 0.01

        async def haengt():
            await real_sleep(1)

        seq = AsyncSequence([])

        async def timeout_call():
            seq.calls += 1
            await haengt()

        try:
            await gr.with_retry(timeout_call)
            caught = None
        except Exception as exc:
            caught = exc
        check("Gemini Timeout: kontrolliert und maximal drei Calls",
              isinstance(caught, gr.GeminiVoruebergehendNichtErreichbar)
              and seq.calls == gr.GEMINI_MAX_ATTEMPTS)

        gr.GEMINI_TIMEOUT_SECONDS = real_timeout
        seq = AsyncSequence([client_error(429), server_error(503), httpx.ReadTimeout("x")])
        try:
            await gr.with_retry(seq)
        except gr.GeminiFehlgeschlagen:
            pass
        check("Gemischte Fehler teilen EIN Retry-Budget", seq.calls == gr.GEMINI_MAX_ATTEMPTS)
    finally:
        gr.asyncio.sleep, gr.GEMINI_TIMEOUT_SECONDS = real_sleep, real_timeout


class FakeClient:
    sequence: list = []
    calls = 0
    bodies: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json):
        type(self).calls += 1
        type(self).bodies.append(json)
        value = type(self).sequence.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def response(code: int, body) -> httpx.Response:
    return httpx.Response(code, json=body, request=httpx.Request("POST", "https://api.tavily.com/search"))


async def tavily_case(values, *, count: int = 5) -> tuple[list[dict], bool, int]:
    FakeClient.sequence = list(values)
    FakeClient.calls = 0
    FakeClient.bodies = []
    ws._cache.clear()
    return (*await ws._tavily_search_intern("private query", count=count, bypass_cache=True), FakeClient.calls)


async def tavily_tests() -> None:
    real_client, real_sleep, real_key = ws.httpx.AsyncClient, ws.asyncio.sleep, ws.TAVILY_API_KEY

    async def no_sleep(_):
        return None

    ws.httpx.AsyncClient = FakeClient
    ws.asyncio.sleep = no_sleep
    ws.TAVILY_API_KEY = "fake"
    try:
        results, failed, calls = await tavily_case([
            httpx.ReadTimeout("simuliert"), response(200, {"results": [{"url": "https://x"}]})
        ])
        check("Tavily Timeout: Retry und Erfolg", calls == 2 and not failed and len(results) == 1)

        results, failed, calls = await tavily_case([
            response(429, {"error": "busy"}), response(200, {"results": []})
        ])
        check("Tavily 429 temporaer: Retry", calls == 2 and not failed)

        results, failed, calls = await tavily_case([
            response(429, {"error": "monthly quota exhausted"}), response(200, {"results": []})
        ])
        check("Tavily Quota: kein sinnloser Retry", calls == 1 and failed)

        results, failed, calls = await tavily_case([
            response(500, {"error": "down"}), response(500, {"error": "down"}),
            response(200, {"results": []}),
        ])
        check("Tavily 500: Retry-Maximum ist endlich", calls == ws._MAX_RETRIES and not failed)

        results, failed, calls = await tavily_case([response(200, [])])
        check("Tavily ungueltige Antwort: kein Retry", calls == 1 and failed)

        results, failed, calls = await tavily_case([response(400, {"error": "bad request"})])
        check("Tavily 400: kein Retry", calls == 1 and failed)

        await tavily_case([response(200, {"results": []})], count=999)
        check("Tavily max_results wird zentral gekappt",
              FakeClient.bodies[0]["max_results"] <= ws.TAVILY_MAX_RESULTS)
    finally:
        ws.httpx.AsyncClient, ws.asyncio.sleep, ws.TAVILY_API_KEY = real_client, real_sleep, real_key


async def budget_tests() -> None:
    async with pc.provider_scope("chat", key="budget-a") as scope:
        limit = scope.limits["gemini"]
        for i in range(limit):
            pc.claim_call("gemini", retry=i > 0)
        try:
            pc.claim_call("gemini")
            caught = False
        except pc.ProviderCallLimitExceeded:
            caught = True
    check("Harte Feature-Call-Obergrenze", caught and limit == PROVIDER_FEATURE_LIMITS["chat"]["gemini"])

    old = pc.PROVIDER_USER_MAX_CONCURRENT
    pc.PROVIDER_USER_MAX_CONCURRENT = 2
    release = asyncio.Event()

    async def parallel_worker():
        try:
            async with pc.provider_scope("chat", key="same-user"):
                await release.wait()
                return "ok"
        except pc.ProviderCapacityExceeded:
            return "blocked"

    tasks = [asyncio.create_task(parallel_worker()) for _ in range(20)]
    await asyncio.sleep(0)
    release.set()
    try:
        parallel_results = await asyncio.gather(*tasks)
    finally:
        pc.PROVIDER_USER_MAX_CONCURRENT = old
    check("20 parallele gleiche Nutzeraktionen werden auf zwei gedeckelt",
          parallel_results.count("ok") == 2 and parallel_results.count("blocked") == 18)

    old_global = pc.PROVIDER_GLOBAL_MAX_CONCURRENT
    pc.PROVIDER_GLOBAL_MAX_CONCURRENT = 1
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold_global():
        async with pc.provider_scope("chat", key="global-a"):
            entered.set()
            await release.wait()

    task = asyncio.create_task(hold_global())
    await entered.wait()
    try:
        try:
            async with pc.provider_scope("chat", key="global-b"):
                pass
            global_blocked = False
        except pc.ProviderCapacityExceeded:
            global_blocked = True
    finally:
        release.set()
        await task
        pc.PROVIDER_GLOBAL_MAX_CONCURRENT = old_global
    check("Globale Parallelitaetsgrenze greift", global_blocked)

    check("Admin-/Batch-Budget ist endlich",
          0 < PROVIDER_FEATURE_LIMITS["admin"]["gemini"] <= 8)
    check("Offline-Bildbatch ist hart begrenzt", 0 < PROVIDER_ADMIN_IMAGE_BATCH_MAX <= 20)
    check("Offline-Bildbatch hat dasselbe harte Provider-Budget",
          PROVIDER_FEATURE_LIMITS["admin_image_batch"]["gemini"]
          == PROVIDER_ADMIN_IMAGE_BATCH_MAX)
    check("Alle zentralen Feature-Budgets sind endlich",
          all(isinstance(v, int) and v >= 0
              for limits in PROVIDER_FEATURE_LIMITS.values() for v in limits.values()))
    check("Gemini Input und Output sind hart begrenzt",
          0 < GEMINI_CHAT_MAX_OUTPUT_TOKENS <= 4096
          and 0 < GEMINI_ANALYSE_MAX_OUTPUT_TOKENS <= 4096
          and 0 < GEMINI_JSON_MAX_OUTPUT_TOKENS <= 16384
          and 0 < GEMINI_MAX_INPUT_CHARS <= 120000)

    from app.gemini_retry import KI_UEBERLASTET_NACHRICHT
    from app.routers.analyse_frage import _sse_generator as analyse_sse
    from app.routers.chat import _sse_generator as chat_sse

    old_global = pc.PROVIDER_GLOBAL_MAX_CONCURRENT
    pc.PROVIDER_GLOBAL_MAX_CONCURRENT = 0
    try:
        chat_events = [event async for event in chat_sse("Hallo", [], cost_key="sse-chat")]
        analyse_events = [
            event async for event in analyse_sse("Kontext", "Frage", [], "kauf", "sse-analyse")
        ]
    finally:
        pc.PROVIDER_GLOBAL_MAX_CONCURRENT = old_global
    check("Chat-Stream meldet Capacity-Limit freundlich",
          any(KI_UEBERLASTET_NACHRICHT in event for event in chat_events)
          and chat_events[-1] == "data: [DONE]\n\n")
    check("Analyse-Stream meldet Capacity-Limit freundlich",
          any(KI_UEBERLASTET_NACHRICHT in event for event in analyse_events)
          and analyse_events[-1] == "data: [DONE]\n\n")

    import app.routers.analyse_frage as r_analyse
    import app.routers.chat as r_chat

    async def kaputt(*_a, **_k):
        raise RuntimeError("interner-stacktrace-text")
        yield  # pragma: no cover

    real_chat, real_analyse = r_chat.chat_stream, r_analyse.analyse_frage_stream
    r_chat.chat_stream, r_analyse.analyse_frage_stream = kaputt, kaputt
    try:
        chat_events = [e async for e in chat_sse("Hallo", [], cost_key="sse-err")]
        analyse_events = [e async for e in analyse_sse("K", "F", [], "kauf", "sse-err")]
    finally:
        r_chat.chat_stream, r_analyse.analyse_frage_stream = real_chat, real_analyse
    for label, events in (("Chat", chat_events), ("Analyse", analyse_events)):
        check(f"{label}-Stream: interner Fehler endet sauber mit [DONE], ohne Details",
              events[-1] == "data: [DONE]\n\n"
              and any(KI_UEBERLASTET_NACHRICHT in e for e in events)
              and not any("interner-stacktrace-text" in e or "RuntimeError" in e for e in events))
    check("Stream-Fehler geben Parallelitaets-Slots frei",
          pc._global_active == 0 and "sse-err" not in pc._active_by_key)


class FakeReq:
    def __init__(self, token=None, host="10.0.0.1"):
        self.cookies = {"auth_token": token} if token else {}
        self.client = type("C", (), {"host": host})()
        self.headers = {}


async def identity_and_release_tests() -> None:
    import app.usage_limit as ul
    real_uid = ul._user_id_aus_cookie
    ul._user_id_aus_cookie = lambda req: 7 if req.cookies.get("auth_token") else None
    try:
        k1 = pc.request_cost_key(FakeReq("token-a"))
        k2 = pc.request_cost_key(FakeReq("token-b"))
        k_uid = pc.request_cost_key(FakeReq(), 7)
        k_anon = pc.request_cost_key(FakeReq(host="10.0.0.1"))
    finally:
        ul._user_id_aus_cookie = real_uid
    check("Mehrere Tokens desselben Kontos teilen EINEN Kosten-Key",
          k1 == k2 == k_uid)
    check("Anonymer Kosten-Key folgt der Kontingent-Identitaet (client_ip)",
          k_anon == pc.hashlib.sha256(ul._schluessel(FakeReq(host="10.0.0.1"), None)
                                      .encode()).hexdigest()[:16])
    check("Kosten-Key enthaelt kein Token", "token-a" not in k1)

    async def gen():
        async with pc.provider_scope("chat", key="fremdkontext"):
            yield 1
            yield 2

    g = gen()
    await asyncio.create_task(g.__anext__())       # Scope in Kontext A betreten
    await asyncio.create_task(g.aclose())          # in Kontext B finalisiert
    check("Finalisierung in fremdem Kontext leckt keinen Slot",
          pc._global_active == 0 and "fremdkontext" not in pc._active_by_key)


async def main() -> None:
    await gemini_tests()
    await tavily_tests()
    await budget_tests()
    await identity_and_release_tests()


asyncio.run(main())
print(f"\n{PASS} PASS / {len(FEHLER)} FAIL")
if FEHLER:
    for f in FEHLER:
        print(" -", f)
    raise SystemExit(1)
print("ALLE PROVIDER-RELIABILITY-TESTS GRUEN")
