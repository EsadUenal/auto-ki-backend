from __future__ import annotations

import json
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.models import ChatRequest, ChatResponse, FehlerResponse
from app.auth import verify_api_key
from app.llm import chat_stream
from app.utf8 import UTF8JSONResponse

router = APIRouter(default_response_class=UTF8JSONResponse)
limiter = Limiter(key_func=get_remote_address)


async def _sse_generator(message: str, verlauf: list[dict]):
    """SSE-Stream: Textfragmente + abschließendes Meta-Event."""
    full_text = []
    meta = {}

    async for event in chat_stream(message, verlauf):
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
                "answer": "".join(full_text),
                "quelle": meta.get("quelle", "gemischt"),
                "fahrzeug_referenz": meta.get("fahrzeug_referenz", []),
                "vertrauen": meta.get("vertrauen", "mittel"),
                "belege": meta.get("belege", []),
            }
            data = json.dumps({"meta": payload}, ensure_ascii=False)
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

    verlauf = [m.model_dump() for m in body.verlauf]

    if body.stream:
        return StreamingResponse(
            _sse_generator(body.message, verlauf),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    full_text = []
    meta = {}
    async for event in chat_stream(body.message, verlauf):
        if event["type"] == "text":
            full_text.append(event["delta"])
        elif event["type"] == "meta":
            meta = event

    return ChatResponse(
        answer="".join(full_text),
        quelle=meta.get("quelle", "gemischt"),
        fahrzeug_referenz=meta.get("fahrzeug_referenz", []),
        vertrauen=meta.get("vertrauen", "mittel"),
        belege=meta.get("belege", []),
    )
