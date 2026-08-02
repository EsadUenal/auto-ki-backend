"""
Checks-Router — Phase 2c: Kauf-Check & Verkaufs-Check pro Nutzer speichern.

Sicherheit: Jeder Endpunkt prüft user_id aus dem JWT-Cookie.
Fremde IDs → 403.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.database import get_conn
from app.gemini_retry import GeminiFehlgeschlagen, KI_UEBERLASTET_NACHRICHT
from app.inserat import run_inserat_optimierung
from app.models import InseratOptimierung, VerkaufsCheckRequest
from app.routers.user_auth import get_current_user_id

log = logging.getLogger(__name__)

router = APIRouter(prefix="/checks", tags=["checks"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_own_check(conn, check_id: int, user_id: int) -> dict:
    row = conn.execute(
        "SELECT id, user_id, typ, titel, eingabe, ergebnis, created_at FROM checks WHERE id = ?",
        (check_id,),
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
    return dict(row)


def _serialize(row: dict) -> dict:
    """JSON-Blobs aus DB in Python-Dicts umwandeln."""
    return {
        "id":         row["id"],
        "typ":        row["typ"],
        "titel":      row["titel"],
        "created_at": row["created_at"],
        "eingabe":    json.loads(row["eingabe"]),
        "ergebnis":   json.loads(row["ergebnis"]),
    }


# ── Schemas ───────────────────────────────────────────────────────────────────

class SaveCheckBody(BaseModel):
    typ:     str    # 'kauf' | 'verkauf'
    titel:   str
    eingabe: dict   # Formulardaten
    ergebnis: dict  # Ergebnisdaten


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("")
def list_checks(user_id: int = Depends(get_current_user_id)):
    """Alle Checks des eingeloggten Nutzers, neueste zuerst."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, typ, titel, created_at FROM checks WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("", status_code=201)
def save_check(body: SaveCheckBody, user_id: int = Depends(get_current_user_id)):
    """Kauf- oder Verkaufs-Check speichern."""
    if body.typ not in ("kauf", "verkauf"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"fehler": {"code": "invalid_typ", "nachricht": "typ muss 'kauf' oder 'verkauf' sein."}},
        )
    with get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO checks (user_id, typ, titel, eingabe, ergebnis) VALUES (?, ?, ?, ?, ?)",
            (user_id, body.typ, body.titel.strip(), json.dumps(body.eingabe), json.dumps(body.ergebnis)),
        )
        conn.commit()
        check_id: int = cursor.lastrowid  # type: ignore[assignment]
        row = conn.execute(
            "SELECT id, typ, titel, eingabe, ergebnis, created_at FROM checks WHERE id = ?",
            (check_id,),
        ).fetchone()
    return _serialize(dict(row))


@router.get("/{check_id}")
def get_check(check_id: int, user_id: int = Depends(get_current_user_id)):
    """Vollständigen Check mit Eingabe + Ergebnis laden."""
    with get_conn() as conn:
        row = _get_own_check(conn, check_id, user_id)
    return _serialize(row)


@router.delete("/{check_id}", status_code=204)
def delete_check(check_id: int, user_id: int = Depends(get_current_user_id)):
    """Check löschen."""
    with get_conn() as conn:
        _get_own_check(conn, check_id, user_id)
        conn.execute("DELETE FROM checks WHERE id = ?", (check_id,))
        conn.commit()


# ── Analyse-Rückfragen (Q&A) pro Check ────────────────────────────────────────

class SaveFrageBody(BaseModel):
    frage:   str = Field(max_length=2_000)
    antwort: str = Field(max_length=20_000)


@router.get("/{check_id}/fragen")
def list_check_fragen(check_id: int, user_id: int = Depends(get_current_user_id)):
    """Gespeicherte Analyse-Rückfragen eines Checks (älteste zuerst)."""
    with get_conn() as conn:
        _get_own_check(conn, check_id, user_id)   # Existenz + Ownership (403/404)
        rows = conn.execute(
            "SELECT frage, antwort, created_at FROM check_frage WHERE check_id = ? ORDER BY id",
            (check_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/{check_id}/fragen", status_code=201)
def add_check_frage(check_id: int, body: SaveFrageBody, user_id: int = Depends(get_current_user_id)):
    """Eine abgeschlossene Frage/Antwort an den Check anhängen."""
    with get_conn() as conn:
        _get_own_check(conn, check_id, user_id)   # Existenz + Ownership (403/404)
        conn.execute(
            "INSERT INTO check_frage (check_id, frage, antwort) VALUES (?, ?, ?)",
            (check_id, body.frage, body.antwort),
        )
        conn.commit()
    return {"ok": True}


# ── Phase 4: Inserats-Optimierung pro Verkaufscheck ───────────────────────────
#
# On-demand (NICHT bei jedem Check automatisch — Kosten/Geschwindigkeit). Der
# erzeugte Text wird in ergebnis.inserat_optimierung PERSISTIERT: beim erneuten
# Öffnen desselben gespeicherten Checks liegt er vor -> KEIN neuer LLM-Call.
# Verbraucht KEIN Check-Kontingent (Folge-Werkzeug eines bereits bezahlten Checks);
# Auth + Ownership schützen vor Fremdzugriff, das globale Rate-Limit vor Missbrauch.

@router.post("/{check_id}/inserat-optimierung", response_model=InseratOptimierung)
async def optimiere_inserat(
    check_id: int,
    body: VerkaufsCheckRequest,
    user_id: int = Depends(get_current_user_id),
):
    """Optimierte Inseratsversion (Titel + Beschreibung) für einen Verkaufscheck.

    Idempotent: ist bereits eine Version am Check gespeichert, wird sie ohne neuen
    LLM-Aufruf zurückgegeben. Der LLM erzeugt reinen Text; `run_inserat_optimierung`
    prüft ihn danach gegen die Eingangsdaten (Fakten-Schutz), bevor er persistiert wird.
    """
    with get_conn() as conn:
        row = _get_own_check(conn, check_id, user_id)   # Existenz + Ownership (403/404)
        if row["typ"] != "verkauf":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"fehler": {"code": "kein_verkaufscheck",
                                   "nachricht": "Inserats-Optimierung gibt es nur für Verkaufschecks."}},
            )
        try:
            ergebnis = json.loads(row["ergebnis"])
        except (json.JSONDecodeError, TypeError):
            ergebnis = {}
        bereits = ergebnis.get("inserat_optimierung")
        if bereits:
            # Schon vorhanden -> keine erneute Generierung (Kosten sparen).
            return InseratOptimierung(**bereits)

    try:
        optimierung = await run_inserat_optimierung(body)
    except GeminiFehlgeschlagen as exc:
        log.warning("Inserat-Optimierung: Gemini-Ausfall (check_id=%s): %s", check_id, exc)
        raise HTTPException(
            status_code=503,
            detail={"fehler": {"code": "ki_ueberlastet", "nachricht": KI_UEBERLASTET_NACHRICHT}},
        ) from exc

    # In ergebnis.inserat_optimierung persistieren (erneut laden + prüfen, falls sich
    # der Check zwischenzeitlich geändert hat — Ownership erneut bestätigen).
    with get_conn() as conn:
        row = _get_own_check(conn, check_id, user_id)
        try:
            ergebnis = json.loads(row["ergebnis"])
        except (json.JSONDecodeError, TypeError):
            ergebnis = {}
        # Falls ein paralleler Request bereits gespeichert hat, dessen Version behalten.
        if ergebnis.get("inserat_optimierung"):
            return InseratOptimierung(**ergebnis["inserat_optimierung"])
        ergebnis["inserat_optimierung"] = optimierung.model_dump()
        conn.execute(
            "UPDATE checks SET ergebnis = ? WHERE id = ? AND user_id = ?",
            (json.dumps(ergebnis), check_id, user_id),
        )
        conn.commit()
    return optimierung
