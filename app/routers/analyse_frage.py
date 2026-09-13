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

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from slowapi import Limiter
from app.client_ip import limit_schluessel

from app.models import AnalyseFrageRequest, FehlerResponse
from app.auth import verify_api_key
from app.database import get_conn
from app.routers.user_auth import get_current_user_id
from app.usage_limit import require_analyse_frage_kontingent
from app.llm import analyse_frage_stream
from app.utf8 import UTF8JSONResponse

router = APIRouter(default_response_class=UTF8JSONResponse)
limiter = Limiter(key_func=limit_schluessel)

# Felder aus dem gespeicherten Ergebnis, die als Kurzfassung vor den Bericht
# gestellt werden. Bewusst eine feste Liste: es geht nur in den Kontext, was der
# Server selbst erzeugt hat — nie beliebige Client-Schluessel.
_KURZFASSUNG = (
    ("empfehlung", "Empfehlung"),
    ("preis_bewertung", "Preisbewertung"),
    ("marktpreis_min", "Marktpreis von (EUR)"),
    ("marktpreis_max", "Marktpreis bis (EUR)"),
    ("empfohlener_preis", "Empfohlener Preis (EUR)"),
    ("schnellverkaufs_preis", "Schnellverkaufspreis (EUR)"),
    ("maximal_preis", "Maximalpreis (EUR)"),
    ("research_status", "Recherchestatus"),
    ("vertrauen", "Vertrauen"),
)

_MAX_KONTEXT = 60_000   # Sicherheitsnetz gegen einen aussergewoehnlich langen Bericht


def _eigener_bezahlter_check(check_id: int, user_id: int) -> tuple[str, str]:
    """Laedt Typ und Ergebnis eines eigenen, bezahlten Checks. Sonst 403/404."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id, typ, ergebnis, lauf_id FROM checks WHERE id=?", (check_id,)
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
    return row["typ"], row["ergebnis"]


def _kontext_aus_check(typ: str, ergebnis_json: str) -> str:
    """Baut den Analysetext aus dem GESPEICHERTEN Ergebnis — nie aus Client-Text."""
    try:
        ergebnis = json.loads(ergebnis_json) or {}
    except (json.JSONDecodeError, TypeError):
        ergebnis = {}
    zeilen = [f"Art der Analyse: {'Kaufcheck' if typ == 'kauf' else 'Verkaufscheck'}"]
    for schluessel, titel in _KURZFASSUNG:
        wert = ergebnis.get(schluessel)
        if wert not in (None, "", []):
            zeilen.append(f"{titel}: {wert}")
    bericht = ergebnis.get("bericht")
    if isinstance(bericht, str) and bericht.strip():
        zeilen.append("")
        zeilen.append(bericht)
    kontext = "\n".join(zeilen)
    if not kontext.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"fehler": {"code": "kein_analysetext",
                               "nachricht": "Zu diesem Check liegt kein Analysetext vor."}},
        )
    return kontext[:_MAX_KONTEXT]


async def _sse_generator(analyse_kontext: str, frage: str, verlauf: list[dict], check_typ: str):
    """SSE-Stream: reine Textfragmente + abschließendes [DONE]."""
    async for event in analyse_frage_stream(analyse_kontext, frage, verlauf, check_typ):
        if event["type"] == "text":
            data = json.dumps({"delta": event["delta"]}, ensure_ascii=False)
            yield f"data: {data}\n\n"
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
    typ, ergebnis_json = _eigener_bezahlter_check(body.check_id, user_id)
    kontext = _kontext_aus_check(typ, ergebnis_json)
    require_analyse_frage_kontingent(request)
    verlauf = [m.model_dump() for m in body.verlauf]
    return StreamingResponse(
        _sse_generator(kontext, body.frage, verlauf, typ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
