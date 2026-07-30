"""
Analyse-Rückfragen — kontextgebundener Chat nach einem Check.

Endpoint:
  POST /analyse-frage  → beantwortet eine Frage AUSSCHLIESSLICH zur mitgeschickten
                         Analyse (Text-Stream via SSE). Verbraucht KEIN Check-
                         Kontingent (Folgefrage zu einem bereits erstellten Check).

Sicherheit/Kosten:
  - API-Key-geschützt wie /chat (kein zusätzlicher Check-Abzug).
  - Rate-Limit 20/min. Themen-Gating erledigt die System-Instruction (llm.py).
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.models import AnalyseFrageRequest, FehlerResponse
from app.auth import verify_api_key
from app.llm import analyse_frage_stream
from app.utf8 import UTF8JSONResponse

router = APIRouter(default_response_class=UTF8JSONResponse)
limiter = Limiter(key_func=get_remote_address)


async def _sse_generator(analyse_kontext: str, frage: str, verlauf: list[dict], check_typ: str):
    """SSE-Stream: reine Textfragmente + abschließendes [DONE]."""
    async for event in analyse_frage_stream(analyse_kontext, frage, verlauf, check_typ):
        if event["type"] == "text":
            data = json.dumps({"delta": event["delta"]}, ensure_ascii=False)
            yield f"data: {data}\n\n"
    yield "data: [DONE]\n\n"


@router.post(
    "/analyse-frage",
    summary="Kontextgebundene Rückfrage zu einer Check-Analyse (Streaming)",
    responses={
        401: {"model": FehlerResponse},
        403: {"model": FehlerResponse},
        429: {"model": FehlerResponse},
        500: {"model": FehlerResponse},
    },
)
@limiter.limit("20/minute")
async def analyse_frage_endpunkt(body: AnalyseFrageRequest, request: Request):
    verify_api_key(request)
    verlauf = [m.model_dump() for m in body.verlauf]
    return StreamingResponse(
        _sse_generator(body.analyse_kontext, body.frage, verlauf, body.check_typ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
