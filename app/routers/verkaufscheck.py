from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.models import VerkaufsCheckRequest, VerkaufsCheckResponse, FehlerResponse
from app.auth import verify_api_key
from app.check_gate import require_check_access, refund_check_credit
from app.gemini_retry import GeminiFehlgeschlagen, KI_UEBERLASTET_NACHRICHT
from app.verkaufscheck import run_verkaufscheck
from app.utf8 import UTF8JSONResponse

log = logging.getLogger(__name__)

router = APIRouter(default_response_class=UTF8JSONResponse)
limiter = Limiter(key_func=get_remote_address)


@router.post(
    "/verkaufscheck",
    response_model=VerkaufsCheckResponse,
    summary="Verkaufs-Check: Preisspanne, Optimierungstipps und Verkaufsstrategie",
    responses={
        401: {"model": FehlerResponse},
        402: {"model": FehlerResponse},
        403: {"model": FehlerResponse},
        429: {"model": FehlerResponse},
        500: {"model": FehlerResponse},
        503: {"model": FehlerResponse},
    },
)
@limiter.limit("10/minute")
async def verkaufscheck_endpunkt(
    body: VerkaufsCheckRequest,
    request: Request,
    user_id: int = Depends(require_check_access),
):
    verify_api_key(request)
    try:
        result = await run_verkaufscheck(body)
    except GeminiFehlgeschlagen as exc:
        # Der Nutzer hat keine verwertbare Analyse erhalten — das bereits von
        # require_check_access() abgezogene Check-Kontingent zurückerstatten.
        log.warning("Verkaufscheck: Gemini-Totalausfall, erstatte Kontingent zurück (user_id=%s): %s", user_id, exc)
        refund_check_credit(user_id)
        raise HTTPException(
            status_code=503,
            detail={"fehler": {"code": "ki_ueberlastet", "nachricht": KI_UEBERLASTET_NACHRICHT}},
        ) from exc
    return VerkaufsCheckResponse(**result)
