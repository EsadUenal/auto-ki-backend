from __future__ import annotations

import logging
import traceback

from fastapi import APIRouter, Request, HTTPException, status
from pydantic import BaseModel

from fastapi.responses import StreamingResponse
from google.genai.errors import ServerError

from app.auth import verify_api_key
from app.admin_llm import entwurf_erstellen, entwurf_stream, generationen_auflisten
from app.db_writer import save_fahrzeug
from app.utf8 import UTF8JSONResponse
from app.gemini_retry import RateLimitExhausted

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", default_response_class=UTF8JSONResponse)


# ---------- Request-Modelle ----------

class EntwurfRequest(BaseModel):
    marke: str
    modell: str
    generation: str


class BatchRequest(BaseModel):
    anfrage: str   # z.B. "alle BMW 4er Generationen"


class SpeichernRequest(BaseModel):
    daten: dict    # das geprüfte Fahrzeug-JSON


# ---------- Endpunkte ----------

def _llm_error(exc: Exception) -> HTTPException:
    """Wandelt LLM-Fehler in passende HTTP-Fehler um. Loggt immer den vollen Traceback."""
    log.error(
        "Admin-LLM-Fehler [%s]: %s\n%s",
        type(exc).__name__, exc, traceback.format_exc(),
    )

    if isinstance(exc, RateLimitExhausted):
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"fehler": {"code": "rate_limit", "nachricht": str(exc)}},
        )
    if isinstance(exc, ValueError) and "unvollständig" in str(exc):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"fehler": {"code": "antwort_unvollstaendig", "nachricht": str(exc)}},
        )
    if isinstance(exc, ServerError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"fehler": {"code": "llm_nicht_erreichbar",
                               "nachricht": f"Gemini nicht erreichbar: {exc}"}},
        )
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={"fehler": {"code": "llm_fehler", "nachricht": str(exc)}},
    )


@router.post("/entwurf", summary="LLM erstellt Schema-Entwurf — vollständig (non-streaming)")
async def entwurf(body: EntwurfRequest, request: Request):
    verify_api_key(request)
    try:
        data = await entwurf_erstellen(body.marke, body.modell, body.generation)
    except Exception as e:
        raise _llm_error(e)
    return {"entwurf": data}


async def _sse_entwurf(marke: str, modell: str, generation: str):
    async for fragment in entwurf_stream(marke, modell, generation):
        yield f"data: {fragment}\n\n"
    yield "data: [DONE]\n\n"


@router.post("/entwurf-stream", summary="LLM erstellt Schema-Entwurf — SSE-Streaming")
async def entwurf_stream_endpoint(body: EntwurfRequest, request: Request):
    verify_api_key(request)
    return StreamingResponse(
        _sse_entwurf(body.marke, body.modell, body.generation),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/batch", summary="LLM listet Generationen auf (Batch-Vorbereitung)")
async def batch(body: BatchRequest, request: Request):
    verify_api_key(request)
    try:
        generationen = await generationen_auflisten(body.anfrage)
    except Exception as e:
        raise _llm_error(e)
    return {"generationen": generationen}


@router.post("/speichern", summary="Geprüftes JSON in SQLite + ChromaDB schreiben")
async def speichern(body: SpeichernRequest, request: Request):
    verify_api_key(request)

    daten = body.daten
    if not all(k in daten for k in ("marke", "modell", "generation")):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"fehler": {"code": "validierung", "nachricht": "marke, modell und generation sind Pflicht."}},
        )

    try:
        bid = save_fahrzeug(daten)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"fehler": {"code": "db_fehler", "nachricht": str(e)}},
        )

    return {
        "gespeichert": True,
        "baureihe_id": bid,
        "nachricht": f"{daten['marke']} {daten['modell']} {daten['generation']} erfolgreich gespeichert.",
    }
