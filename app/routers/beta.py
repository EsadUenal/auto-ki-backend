"""
Closed-Beta: EIN Endpunkt zum Einloesen einer persoenlichen Einladung.

Bewusst gibt es hier KEINEN Endpunkt zum ERSTELLEN von Einladungen. Fuer eine
Handvoll namentlich bekannter Tester waere eine oeffentlich erreichbare
Erzeugungsroute (selbst hinter einem Admin-Key) zusaetzliche Angriffsflaeche
ohne Nutzen — Einladungen entstehen ausschliesslich ueber das interne Werkzeug
`scripts/beta_invite_tool.py`.

Der Endpunkt verlangt ein Login: das Paket wird einem KONTO gutgeschrieben, und
nur mit einem Konto laesst sich pruefen, ob die eingeladene Adresse zur
angemeldeten passt (app/beta_invite.py). Der Token allein berechtigt zu nichts.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app import beta_invite
from app.rate_limit import limiter
from app.routers.user_auth import get_current_user_id
from app.utf8 import UTF8JSONResponse

router = APIRouter(prefix="/beta", tags=["beta"], default_response_class=UTF8JSONResponse)


class RedeemBody(BaseModel):
    token: str


@router.post("/redeem", summary="Persoenliche Closed-Beta-Einladung einloesen")
@limiter.limit("10/minute")
def redeem(body: RedeemBody, request: Request,
           user_id: int = Depends(get_current_user_id)):
    """Loest die Einladung fuer das eingeloggte Konto ein.

    Antwortet IMMER mit HTTP 200 und einem `status`-Feld statt mit einem
    Fehlercode. Grund: ein 404 fuer unbekannte und ein 200 fuer existierende
    Token waere ein Orakel, mit dem sich Token (und die Frage, ob eine Adresse
    eingeladen wurde) erraten liessen. Die Oberflaeche unterscheidet die Faelle
    ueber `status`, der Server gibt dabei nie preis, WER eingeladen wurde.

    Rate Limit zusaetzlich zur Token-Entropie: 256 Bit sind nicht zu erraten,
    aber ein offener Endpunkt soll trotzdem nicht beliebig oft befragt werden
    koennen.
    """
    ergebnis = beta_invite.loese_ein(body.token, user_id)
    return {
        "status": ergebnis.status,
        "kaufchecks": ergebnis.kaufchecks,
        "verkaufschecks": ergebnis.verkaufschecks,
    }
