from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.models import KaufCheckRequest, KaufCheckResponse, FehlerResponse
from app.auth import verify_api_key
from app.check_gate import require_check_access
from app.kaufcheck import run_kaufcheck
from app.utf8 import UTF8JSONResponse

router = APIRouter(default_response_class=UTF8JSONResponse)
limiter = Limiter(key_func=get_remote_address)


@router.post(
    "/kaufcheck",
    response_model=KaufCheckResponse,
    summary="Kauf-Check: Inserat analysieren, Marktpreis bewerten, Empfehlung geben",
    responses={
        401: {"model": FehlerResponse},
        402: {"model": FehlerResponse},
        403: {"model": FehlerResponse},
        429: {"model": FehlerResponse},
        500: {"model": FehlerResponse},
    },
)
@limiter.limit("10/minute")
async def kaufcheck_endpunkt(
    body: KaufCheckRequest,
    request: Request,
    _user_id: int = Depends(require_check_access),
):
    verify_api_key(request)
    result = await run_kaufcheck(body)
    return KaufCheckResponse(**result)
