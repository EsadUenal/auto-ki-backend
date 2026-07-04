"""
Ersatzteil-Such-Gate.

FastAPI-Dependency: stellt sicher dass der eingeloggte Nutzer noch
Ersatzteil-Suchen übrig hat, bevor eine Preisvergleichs-Suche ausgeführt wird.

MAX-Abo: unbegrenzt (kein Dekrement)
Ohne Abo: 1 Gratis-Suche (einmalig, kein monatliches Reset)
LIGHT/PRO: monatliches Kontingent (Reset via Stripe invoice.paid — siehe payments.py)
"""
from __future__ import annotations

from fastapi import Depends, HTTPException

from app.database import get_conn
from app.routers.user_auth import get_current_user_id


def require_ersatzteil_access(user_id: int = Depends(get_current_user_id)) -> int:
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
            "UPDATE users SET ersatzteil_suchen_verbleibend = ersatzteil_suchen_verbleibend - 1 "
            "WHERE id = ? AND ersatzteil_suchen_verbleibend > 0",
            (user_id,),
        )
        conn.commit()

    if result.rowcount == 0:
        raise HTTPException(
            status_code=402,
            detail={
                "fehler": {
                    "code": "payment_required",
                    "nachricht": "Kein Ersatzteilsuchen-Kontingent mehr. Abo abschließen oder upgraden.",
                }
            },
        )

    return user_id
