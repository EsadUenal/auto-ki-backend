"""
Analyse-Rückfragen — kontextgebundener Chat nach einem Check.

Endpoint:
  POST /analyse-frage  → beantwortet eine Frage AUSSCHLIESSLICH zu einem
                         gespeicherten, selbst bezahlten Check (Text-Stream via SSE).
                         Verbraucht KEIN Check-Kontingent (Folgefrage zu einem
                         bereits erstellten Check).

Sicherheit/Kosten (Security Block 3, P2-6):
  Frueher schickte der Client den Analysetext SELBST mit. Damit war der Endpunkt
  ein zweiter, praktisch freier Gemini-Kanal: beliebiger Text rein, Antwort
  raus — mit eigenem Monatstopf neben dem Chat-Limit und ohne jeden Bezug zu
  einer bezahlten Leistung.

  Jetzt gilt:
    - Login erforderlich,
    - der Client nennt nur eine `check_id`,
    - der Check muss IHM gehoeren (sonst 403/404),
    - der Check muss aus einem echten, bezahlten Lauf stammen
      (`checks.lauf_id`, siehe app/check_lauf.py) — ein per POST /checks selbst
      angelegter Datensatz reicht nicht,
    - der Analysetext wird SERVERSEITIG aus dem gespeicherten Ergebnis gebaut.

  Das Monatskontingent bleibt zusaetzlich bestehen (Abuse-Deckel), ist aber
  nicht mehr die einzige Huerde.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from slowapi import Limiter
from app.client_ip import limit_schluessel

from app.models import AnalyseFrageRequest, FehlerResponse
from app.auth import verify_api_key
from app.gemini_retry import KI_UEBERLASTET_NACHRICHT
from app.database import get_conn
from app.routers.user_auth import get_current_user_id
from app.usage_limit import require_analyse_frage_kontingent
from app.provider_control import ProviderCapacityExceeded, provider_scope, request_cost_key
from app.llm import analyse_frage_stream, ANALYSE_KONTEXT_MAX
from app.analyse_kontext import baue_analyse_kontext
from app.utf8 import UTF8JSONResponse

router = APIRouter(default_response_class=UTF8JSONResponse)
limiter = Limiter(key_func=limit_schluessel)
log = logging.getLogger(__name__)

def _eigener_bezahlter_check(check_id: int, user_id: int) -> tuple[str, str, str | None]:
    """Laedt Typ und Ergebnis eines eigenen, bezahlten Checks. Sonst 403/404."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id, typ, ergebnis, eingabe, lauf_id FROM checks WHERE id=?", (check_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"fehler": {"code": "not_found", "nachricht": "Check nicht gefunden."}},
        )
    if row["user_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"fehler": {"code": "forbidden", "nachricht": "Zugriff verweigert."}},
        )
    if not row["lauf_id"]:
        # Kein Nachweis eines echten Laufs: selbst angelegte Datensaetze und
        # Checks aus der Zeit vor dem Nachweis. Bewusst KEINE nachtraegliche
        # Freischaltung — sonst waere der Kanal wieder offen.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"fehler": {"code": "kein_echter_check",
                               "nachricht": "Rückfragen gibt es nur zu einem durchgeführten Check."}},
        )
    return row["typ"], row["ergebnis"], row["eingabe"]


def _kontext_aus_check(typ: str, ergebnis_json: str, eingabe_json: str | None = None) -> str:
    """Baut den Analysetext aus dem GESPEICHERTEN Ergebnis und den gespeicherten Angaben —
    nie aus Client-Text. Die Auswahl (Vorbehalte vor Bericht, Auslassungen benannt) macht
    `app.analyse_kontext`; die Grenze ist dieselbe wie im LLM-Aufruf."""
    kontext = baue_analyse_kontext(typ, ergebnis_json, eingabe_json, ANALYSE_KONTEXT_MAX)
    if not kontext.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"fehler": {"code": "kein_analysetext",
                               "nachricht": "Zu diesem Check liegt kein Analysetext vor."}},
        )
    return kontext


def _fehler_event(teilantwort: bool) -> str:
    """Eigenes Fehlerereignis (CHAT-1): der Fehlertext ist KEIN Antworttext. `teilantwort` sagt dem
    Client, dass bereits Text gesendet wurde, der unvollstaendig ist."""
    data = json.dumps({"fehler": {"code": "ki_ueberlastet", "nachricht": KI_UEBERLASTET_NACHRICHT},
                       "teilantwort": bool(teilantwort)}, ensure_ascii=False)
    return f"data: {data}\n\n"


async def _sse_generator(analyse_kontext: str, frage: str, verlauf: list[dict], check_typ: str,
                         cost_key: str = "anonymous"):
    """SSE-Stream: Textfragmente, optional Meta (abgeschnitten) bzw. Fehlerereignis, dann [DONE]."""
    hat_text = False
    try:
        async with provider_scope("analyse_frage", key=cost_key):
            async for event in analyse_frage_stream(analyse_kontext, frage, verlauf, check_typ):
                if event["type"] == "text":
                    hat_text = True
                    data = json.dumps({"delta": event["delta"]}, ensure_ascii=False)
                    yield f"data: {data}\n\n"
                elif event["type"] == "meta":
                    data = json.dumps({"meta": {"abgeschnitten": bool(event.get("abgeschnitten"))}})
                    yield f"data: {data}\n\n"
                elif event["type"] == "error":
                    yield _fehler_event(event.get("teilantwort", hat_text))
    except ProviderCapacityExceeded:
        yield _fehler_event(hat_text)
    except Exception as exc:
        # Kein abgerissener Stream und keine internen Details im SSE-Text.
        log.error("Analyse-Frage-Stream error_class=%s", type(exc).__name__)
        yield _fehler_event(hat_text)
    yield "data: [DONE]\n\n"


@router.post(
    "/analyse-frage",
    summary="Kontextgebundene Rückfrage zu einer Check-Analyse (Streaming)",
    responses={
        401: {"model": FehlerResponse},
        403: {"model": FehlerResponse},
        404: {"model": FehlerResponse},
        429: {"model": FehlerResponse},
        500: {"model": FehlerResponse},
    },
)
@limiter.limit("20/minute")
async def analyse_frage_endpunkt(
    body: AnalyseFrageRequest,
    request: Request,
    user_id: int = Depends(get_current_user_id),
):
    verify_api_key(request)
    typ, ergebnis_json, eingabe_json = _eigener_bezahlter_check(body.check_id, user_id)
    kontext = _kontext_aus_check(typ, ergebnis_json, eingabe_json)
    require_analyse_frage_kontingent(request)
    verlauf = [m.model_dump() for m in body.verlauf]
    return StreamingResponse(
        _sse_generator(kontext, body.frage, verlauf, typ, request_cost_key(request, user_id)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
