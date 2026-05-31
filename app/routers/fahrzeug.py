from __future__ import annotations

from fastapi import APIRouter, Request, HTTPException, status
from app.models import FahrzeugRequest, FehlerResponse
from app.database import get_baureihe
from app.auth import verify_api_key
from app.utf8 import UTF8JSONResponse

router = APIRouter(default_response_class=UTF8JSONResponse)


@router.post(
    "/fahrzeug",
    summary="Rohe Fahrzeugdaten (kein LLM)",
    responses={
        404: {"model": FehlerResponse},
        401: {"model": FehlerResponse},
        403: {"model": FehlerResponse},
    },
)
def fahrzeug_endpunkt(body: FahrzeugRequest, request: Request):
    verify_api_key(request)

    data = get_baureihe(body.marke, body.modell, body.generation)

    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "fehler": {
                    "code": "nicht_gefunden",
                    "nachricht": (
                        f"Kein Profil für {body.marke} {body.modell} {body.generation} "
                        "in der Datenbank. Dazu habe ich kein geprüftes Profil."
                    ),
                }
            },
        )

    return data
