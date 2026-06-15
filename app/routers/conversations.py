"""
Conversations-Router — Phase 2c: persistente Chats pro Nutzer.

Alle Endpunkte prüfen serverseitig, dass die Konversation dem eingeloggten
Nutzer gehört (user_id-Abgleich aus dem JWT). Fremde IDs → 403.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.database import get_conn
from app.routers.user_auth import get_current_user_id

router = APIRouter(prefix="/conversations", tags=["conversations"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_own_conversation(conn, conv_id: int, user_id: int) -> dict:
    """Lädt Konversation und prüft Eigentümerschaft. 404/403 bei Fehler."""
    row = conn.execute(
        "SELECT id, user_id, title, created_at, updated_at FROM conversations WHERE id = ?",
        (conv_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"fehler": {"code": "not_found", "nachricht": "Konversation nicht gefunden."}},
        )
    if row["user_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"fehler": {"code": "forbidden", "nachricht": "Zugriff verweigert."}},
        )
    return dict(row)


# ── Schemas ───────────────────────────────────────────────────────────────────

class CreateConversationBody(BaseModel):
    title: str = "Neuer Chat"


class PatchConversationBody(BaseModel):
    title: str


class AddMessageBody(BaseModel):
    role: str   # 'user' | 'assistant'
    content: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("")
def list_conversations(user_id: int = Depends(get_current_user_id)):
    """Alle Konversationen des eingeloggten Nutzers, neueste zuerst."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, title, created_at, updated_at
            FROM conversations
            WHERE user_id = ?
            ORDER BY updated_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("", status_code=201)
def create_conversation(
    body: CreateConversationBody,
    user_id: int = Depends(get_current_user_id),
):
    """Neue leere Konversation anlegen."""
    with get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO conversations (user_id, title) VALUES (?, ?)",
            (user_id, body.title.strip() or "Neuer Chat"),
        )
        conn.commit()
        conv_id: int = cursor.lastrowid  # type: ignore[assignment]
        row = conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations WHERE id = ?",
            (conv_id,),
        ).fetchone()
    return dict(row)


@router.get("/{conv_id}")
def get_conversation(conv_id: int, user_id: int = Depends(get_current_user_id)):
    """Konversation mit allen Nachrichten laden."""
    with get_conn() as conn:
        conv = _get_own_conversation(conn, conv_id, user_id)
        messages = conn.execute(
            "SELECT id, role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY id ASC",
            (conv_id,),
        ).fetchall()
    conv["messages"] = [dict(m) for m in messages]
    return conv


@router.patch("/{conv_id}")
def patch_conversation(
    conv_id: int,
    body: PatchConversationBody,
    user_id: int = Depends(get_current_user_id),
):
    """Titel einer Konversation aktualisieren."""
    with get_conn() as conn:
        _get_own_conversation(conn, conv_id, user_id)
        conn.execute(
            "UPDATE conversations SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (body.title.strip() or "Neuer Chat", conv_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations WHERE id = ?",
            (conv_id,),
        ).fetchone()
    return dict(row)


@router.delete("/{conv_id}", status_code=204)
def delete_conversation(conv_id: int, user_id: int = Depends(get_current_user_id)):
    """Konversation (+ alle Nachrichten via CASCADE) löschen."""
    with get_conn() as conn:
        _get_own_conversation(conn, conv_id, user_id)
        conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        conn.commit()


@router.post("/{conv_id}/messages", status_code=201)
def add_message(
    conv_id: int,
    body: AddMessageBody,
    user_id: int = Depends(get_current_user_id),
):
    """Nachricht an eine Konversation anhängen + updated_at der Konversation aktualisieren."""
    if body.role not in ("user", "assistant"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"fehler": {"code": "invalid_role", "nachricht": "role muss 'user' oder 'assistant' sein."}},
        )
    with get_conn() as conn:
        _get_own_conversation(conn, conv_id, user_id)
        cursor = conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (conv_id, body.role, body.content),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (conv_id,),
        )
        conn.commit()
        msg_id: int = cursor.lastrowid  # type: ignore[assignment]
        row = conn.execute(
            "SELECT id, role, content, created_at FROM messages WHERE id = ?",
            (msg_id,),
        ).fetchone()
    return dict(row)
