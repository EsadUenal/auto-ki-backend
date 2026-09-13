from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from app.client_ip import limit_schluessel

from app.models import KaufCheckRequest, KaufCheckResponse, FehlerResponse
from app.auth import verify_api_key
from app.routers.user_auth import get_current_user_id
from app.check_gate import entnehme_kaufcheck, refund_check_credit
from app.check_lauf import erzeuge as erzeuge_lauf_nachweis
from app.gemini_retry import GeminiFehlgeschlagen, KI_UEBERLASTET_NACHRICHT
from app.kaufcheck import run_kaufcheck
from app.marktrecherche import RechercheUnzureichend
from app.utf8 import UTF8JSONResponse

log = logging.getLogger(__name__)

router = APIRouter(default_response_class=UTF8JSONResponse)
limiter = Limiter(key_func=limit_schluessel)


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
    user_id: int = Depends(get_current_user_id),
):
    verify_api_key(request)
    # P1-6: Kontingent erst JETZT entnehmen — Body-Validierung (422), Login,
    # API-Key und Rate-Limit sind hier durch. Vorher lief das in einer
    # Dependency und kostete schon bei einer ungueltigen Eingabe einen Check.
    zugriff = entnehme_kaufcheck(user_id)
    try:
        # §22: "Erneut versuchen" nach research_failed erzwingt frische Tavily-Calls
        # statt derselben ggf. dünnen gecachten Antwort (?retry=true).
        result = await run_kaufcheck(body, retry=retry)
    except RechercheUnzureichend as exc:
        # P0-1: Fehlende Marktdaten brechen den Kaufcheck NICHT mehr ab —
        # `run_kaufcheck` liefert dafür jetzt research_status="completed_no_market"
        # und einen vollständigen technischen Bericht (siehe app/kaufcheck.py).
        # Dieser Zweig ist damit für den Kaufcheck praktisch unerreichbar geworden.
        #
        # Er bleibt bewusst als Sicherheitsnetz stehen: `RechercheUnzureichend` ist
        # weiterhin eine gültige Exception der geteilten Marktrecherche (der
        # Verkaufscheck löst sie regulär aus — dort IST der Marktpreis das Produkt).
        # Käme sie hier je wieder an, ist ein 500er die falsche Antwort; die
        # bestehende Rückerstattung ist das richtige Verhalten.
        log.info("Kaufcheck: RechercheUnzureichend (unerwartet nach P0-1), "
                 "erstatte Kontingent zurück (user_id=%s)", zugriff.user_id)
        refund_check_credit(zugriff)
        return KaufCheckResponse(
            bericht=exc.nachricht,
            empfehlung="unbekannt",
            preis_bewertung="unbekannt",
            quelle="web",
            vertrauen="niedrig",
            research_status="research_failed",
        )
    except GeminiFehlgeschlagen as exc:
        # Der Nutzer hat keine verwertbare Analyse erhalten — das oben entnommene
        # Check-Kontingent zurückerstatten,
        # statt ihm einen Check zu berechnen, für den er nichts bekommen hat.
        log.warning("Kaufcheck: Gemini-Totalausfall, erstatte Kontingent zurück (user_id=%s): %s", zugriff.user_id, exc)
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
        log.exception("Kaufcheck: unerwarteter Fehler, erstatte Kontingent zurueck (user_id=%s)",
                      zugriff.user_id)
        refund_check_credit(zugriff)
        raise
    # P1-4: Nachweis ueber den tatsaechlichen Lauf. Das Frontend reicht ihn beim
    # Speichern zurueck; nur damit gilt ein gespeicherter Check als echt.
    result["lauf_id"] = erzeuge_lauf_nachweis(zugriff.user_id, "kauf")
    return KaufCheckResponse(**result)
