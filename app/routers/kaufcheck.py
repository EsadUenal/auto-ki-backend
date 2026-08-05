from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.models import KaufCheckRequest, KaufCheckResponse, FehlerResponse
from app.auth import verify_api_key
from app.check_gate import require_check_access, refund_check_credit
from app.gemini_retry import GeminiFehlgeschlagen, KI_UEBERLASTET_NACHRICHT
from app.kaufcheck import run_kaufcheck
from app.marktrecherche import RechercheUnzureichend
from app.utf8 import UTF8JSONResponse

log = logging.getLogger(__name__)

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
        503: {"model": FehlerResponse},
    },
)
@limiter.limit("10/minute")
async def kaufcheck_endpunkt(
    body: KaufCheckRequest,
    request: Request,
    retry: bool = False,
    user_id: int = Depends(require_check_access),
):
    verify_api_key(request)
    try:
        # §22: "Erneut versuchen" nach research_failed erzwingt frische Tavily-Calls
        # statt derselben ggf. dünnen gecachten Antwort (?retry=true).
        result = await run_kaufcheck(body, retry=retry)
    except RechercheUnzureichend as exc:
        # §0/§4: keine belastbare Marktdatenbasis -> KEIN fertiger Bericht, Kontingent
        # zurückerstatten (idempotent). Der Nutzer erhält eine freundliche Meldung mit
        # Retry-Option; research_status="research_failed" signalisiert dem Frontend,
        # den Check NICHT als abgeschlossen zu behandeln (nicht speichern).
        log.info("Kaufcheck: research_failed, erstatte Kontingent zurück (user_id=%s)", user_id)
        refund_check_credit(user_id)
        return KaufCheckResponse(
            bericht=exc.nachricht,
            empfehlung="unbekannt",
            preis_bewertung="unbekannt",
            quelle="web",
            vertrauen="niedrig",
            research_status="research_failed",
        )
    except GeminiFehlgeschlagen as exc:
        # Der Nutzer hat keine verwertbare Analyse erhalten — das bereits von
        # require_check_access() abgezogene Check-Kontingent zurückerstatten,
        # statt ihm einen Check zu berechnen, für den er nichts bekommen hat.
        log.warning("Kaufcheck: Gemini-Totalausfall, erstatte Kontingent zurück (user_id=%s): %s", user_id, exc)
        refund_check_credit(user_id)
        raise HTTPException(
            status_code=503,
            detail={"fehler": {"code": "ki_ueberlastet", "nachricht": KI_UEBERLASTET_NACHRICHT}},
        ) from exc
    return KaufCheckResponse(**result)
