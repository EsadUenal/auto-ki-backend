from __future__ import annotations

from fastapi import APIRouter, Request, HTTPException, status
from pydantic import BaseModel

from app.auth import verify_api_key
from app.admin_llm import entwurf_erstellen, generationen_auflisten
from app.db_writer import save_fahrzeug
from app.utf8 import UTF8JSONResponse

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

@router.post("/entwurf", summary="LLM erstellt Schema-Entwurf (noch nicht gespeichert)")
async def entwurf(body: EntwurfRequest, request: Request):
    verify_api_key(request)
    try:
        data = await entwurf_erstellen(body.marke, body.modell, body.generation)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"fehler": {"code": "llm_fehler", "nachricht": str(e)}},
        )
    return {"entwurf": data}


@router.post("/batch", summary="LLM listet Generationen auf (Batch-Vorbereitung)")
async def batch(body: BatchRequest, request: Request):
    verify_api_key(request)
    try:
        generationen = await generationen_auflisten(body.anfrage)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"fehler": {"code": "llm_fehler", "nachricht": str(e)}},
        )
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
