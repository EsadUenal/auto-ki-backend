from __future__ import annotations

import json
import logging
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from slowapi import Limiter
from app.client_ip import limit_schluessel

from app.models import ChatRequest, ChatResponse, FehlerResponse
from app.auth import verify_api_key
from app.gemini_retry import KI_UEBERLASTET_NACHRICHT
from app.llm import chat_stream
from app.postprocess import postprocess_answer
from app.usage_limit import require_chat_kontingent
from app.provider_control import ProviderCapacityExceeded, provider_scope, request_cost_key
from app.utf8 import UTF8JSONResponse

router = APIRouter(default_response_class=UTF8JSONResponse)
limiter = Limiter(key_func=limit_schluessel)
log = logging.getLogger(__name__)


async def _sse_generator(message: str, verlauf: list[dict], fahrzeug_kontext: str | None = None,
                         cost_key: str = "anonymous"):
    """SSE-Stream: Textfragmente + abschließendes Meta-Event."""
    full_text = []
    meta = {}

    try:
        async with provider_scope("chat", key=cost_key):
            async for event in chat_stream(message, verlauf, fahrzeug_kontext=fahrzeug_kontext):
                if event["type"] == "status":
                    data = json.dumps({"status": event["text"]}, ensure_ascii=False)
                    yield f"data: {data}\n\n"
                elif event["type"] == "text":
                    full_text.append(event["delta"])
                    data = json.dumps({"delta": event["delta"]}, ensure_ascii=False)
                    yield f"data: {data}\n\n"
                elif event["type"] == "meta":
                    meta = event
                    payload = {
                        "answer": postprocess_answer("".join(full_text)),
                        "quelle": meta.get("quelle", "gemischt"),
                        "fahrzeug_referenz": meta.get("fahrzeug_referenz", []),
                        "vertrauen": meta.get("vertrauen", "mittel"),
                        "belege": meta.get("belege", []),
                        "abgeschnitten": bool(meta.get("abgeschnitten", False)),
                    }
                    data = json.dumps({"meta": payload}, ensure_ascii=False)
                    yield f"data: {data}\n\n"
    except ProviderCapacityExceeded:
        data = json.dumps({"delta": KI_UEBERLASTET_NACHRICHT}, ensure_ascii=False)
        yield f"data: {data}\n\n"
    except Exception as exc:
        # Kein abgerissener Stream und keine internen Details im SSE-Text.
        log.error("Chat-Stream error_class=%s", type(exc).__name__)
        data = json.dumps({"delta": KI_UEBERLASTET_NACHRICHT}, ensure_ascii=False)
        yield f"data: {data}\n\n"

    yield "data: [DONE]\n\n"


@router.post(
    "/chat",
    summary="KI-Konversation (Gemini 2.5 Flash, DB-first)",
    responses={
        401: {"model": FehlerResponse},
        403: {"model": FehlerResponse},
        429: {"model": FehlerResponse},
        500: {"model": FehlerResponse},
    },
)
@limiter.limit("20/minute")
async def chat_endpunkt(body: ChatRequest, request: Request):
    verify_api_key(request)
    # Monatliches Chat-Kontingent (Free 20 / Plus 100) — zaehlt VOR dem
    # LLM-Aufruf, damit eine ueberschrittene Grenze keine Modellkosten
    # mehr verursacht.
    require_chat_kontingent(request)

    verlauf = [m.model_dump() for m in body.verlauf]
    cost_key = request_cost_key(request)

    if body.stream:
        return StreamingResponse(
            _sse_generator(body.message, verlauf, body.fahrzeug_kontext, cost_key),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    full_text = []
    meta = {}
    async with provider_scope("chat", key=cost_key):
        async for event in chat_stream(body.message, verlauf, fahrzeug_kontext=body.fahrzeug_kontext):
            if event["type"] == "text":
                full_text.append(event["delta"])
            elif event["type"] == "meta":
                meta = event

    return ChatResponse(
        answer=postprocess_answer("".join(full_text)),
        quelle=meta.get("quelle", "gemischt"),
        fahrzeug_referenz=meta.get("fahrzeug_referenz", []),
        vertrauen=meta.get("vertrauen", "mittel"),
        belege=meta.get("belege", []),
        abgeschnitten=bool(meta.get("abgeschnitten", False)),
    )
