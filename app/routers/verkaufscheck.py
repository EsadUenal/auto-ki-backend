from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.models import VerkaufsCheckRequest, VerkaufsCheckResponse, FehlerResponse
from app.auth import verify_api_key
from app.check_gate import require_check_access
from app.verkaufscheck import run_verkaufscheck
from app.utf8 import UTF8JSONResponse

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
    },
)
@limiter.limit("10/minute")
async def verkaufscheck_endpunkt(
    body: VerkaufsCheckRequest,
    request: Request,
    _user_id: int = Depends(require_check_access),
):
    verify_api_key(request)
    result = await run_verkaufscheck(body)
    return VerkaufsCheckResponse(**result)
