from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from app.client_ip import limit_schluessel

from app.models import VerkaufsCheckRequest, VerkaufsCheckResponse, FehlerResponse
from app.auth import verify_api_key
from app.routers.user_auth import get_current_user_id
from app.check_gate import entnehme_verkaufscheck, refund_check_credit
from app.check_lauf import erzeuge as erzeuge_lauf_nachweis
from app.usage_limit import verbrauche_check_versuch
from app.gemini_retry import GeminiFehlgeschlagen, KI_UEBERLASTET_NACHRICHT
from app.provider_control import provider_action
from app.verkaufscheck import run_verkaufscheck
from app.marktrecherche import RechercheUnzureichend
from app.utf8 import UTF8JSONResponse

log = logging.getLogger(__name__)

router = APIRouter(default_response_class=UTF8JSONResponse)
limiter = Limiter(key_func=limit_schluessel)


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
@provider_action("verkaufscheck")
async def verkaufscheck_endpunkt(
    body: VerkaufsCheckRequest,
    request: Request,
    retry: bool = False,
    user_id: int = Depends(get_current_user_id),
):
    verify_api_key(request)
    # P1-6: siehe kaufcheck.py — Entnahme erst nach Validierung, Auth und API-Key.
    # P2-7: Technischer Versuchszaehler VOR der Entnahme. Er wird bei einem
    # Fehlschlag NICHT zurueckgesetzt — sonst liesse sich mit einem einzigen
    # Guthaben beliebig oft Recherche ausloesen (Rueckerstattungs-Schleife).
    verbrauche_check_versuch(request)
    zugriff = entnehme_verkaufscheck(user_id)
    try:
        # §22: "Erneut versuchen" nach research_failed erzwingt frische Tavily-Calls
        # statt derselben ggf. dünnen gecachten Antwort (?retry=true).
        result = await run_verkaufscheck(body, retry=retry)
    except RechercheUnzureichend as exc:
        # §0/§4/§10: keine belastbare Marktdatenbasis -> KEINE Preisstrategie, kein
        # fertiger Bericht, Kontingent zurückerstatten (idempotent). research_failed
        # signalisiert dem Frontend, den Check nicht als abgeschlossen zu behandeln.
        log.info("Verkaufscheck: research_failed, erstatte Kontingent zurück (user_id=%s)", zugriff.user_id)
        refund_check_credit(zugriff)
        return VerkaufsCheckResponse(
            bericht=exc.nachricht,
            quelle="web",
            vertrauen="niedrig",
            research_status="research_failed",
        )
    except GeminiFehlgeschlagen as exc:
        # Der Nutzer hat keine verwertbare Analyse erhalten — das oben entnommene
        # require_check_access() abgezogene Check-Kontingent zurückerstatten.
        log.warning("Verkaufscheck: Gemini-Totalausfall, erstatte Kontingent zurück (user_id=%s): %s", zugriff.user_id, exc)
        refund_check_credit(zugriff)
        raise HTTPException(
            status_code=503,
            detail={"fehler": {"code": "ki_ueberlastet", "nachricht": KI_UEBERLASTET_NACHRICHT}},
        ) from exc
    except Exception:
        # P1-6: Jeder andere Fehler (unerwarteter 500, Timeout, Provider-Panne,
        # Fehler im weiteren Check-Ablauf) darf kein bezahltes Kontingent
        # vernichten. Genau einmal erstatten (CheckZugriff.erstattet) und den
        # Fehler unveraendert weiterreichen — die Antwort an den Nutzer bleibt,
        # wie sie war.
        log.exception("Verkaufscheck: unerwarteter Fehler, erstatte Kontingent zurueck (user_id=%s)",
                      zugriff.user_id)
        refund_check_credit(zugriff)
        raise
    # P1-4: Nachweis ueber den tatsaechlichen Lauf (siehe kaufcheck.py).
    result["lauf_id"] = erzeuge_lauf_nachweis(zugriff.user_id, "verkauf")
    return VerkaufsCheckResponse(**result)
