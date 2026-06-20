"""
Check-Gate — Phase 2d

FastAPI-Dependency: stellt sicher dass der eingeloggte Nutzer
noch Check-Kontingent hat, bevor ein Kauf- oder Verkaufs-Check
ausgeführt wird.

MAX-Abo: unbegrenzt (kein Dekrement)
Alle anderen: atomares Dekrement; 402 wenn checks_verbleibend == 0
"""
from __future__ import annotations

from fastapi import Depends, HTTPException

from app.database import get_conn
from app.routers.user_auth import get_current_user_id


def require_check_access(user_id: int = Depends(get_current_user_id)) -> int:
    with get_conn() as conn:
        user = conn.execute(
            "SELECT abo_typ FROM users WHERE id=?", (user_id,)
        ).fetchone()

    if not user:
        raise HTTPException(
            status_code=401,
            detail={"fehler": {"code": "unauthorized", "nachricht": "Nutzer nicht gefunden."}},
        )

    if user["abo_typ"] == "max":
        return user_id  # unbegrenzt

    # Atomares Dekrement — race-condition-sicher
    with get_conn() as conn:
        result = conn.execute(
            "UPDATE users SET checks_verbleibend = checks_verbleibend - 1 "
            "WHERE id = ? AND checks_verbleibend > 0",
            (user_id,),
        )
        conn.commit()

    if result.rowcount == 0:
        raise HTTPException(
            status_code=402,
            detail={
                "fehler": {
                    "code": "payment_required",
                    "nachricht": "Kein Check-Kontingent mehr. Abo abschließen oder Einzelkauf buchen.",
                }
            },
        )

    return user_id
