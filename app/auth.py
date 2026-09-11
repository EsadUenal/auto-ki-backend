import hmac

from fastapi import Request, HTTPException, status
from app.config import API_KEY, ADMIN_API_KEY


def _bearer(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"fehler": {"code": "unauthorized", "nachricht": "Authorization-Header fehlt oder ungültig."}},
        )
    return auth.removeprefix("Bearer ").strip()


def verify_api_key(request: Request) -> None:
    """Consumer-Key. Steht im oeffentlichen Frontend-Bundle — kein Admin-Zugang."""
    token = _bearer(request)
    # Konstante Vergleichszeit statt "!=" — verhindert Timing-Angriffe, die aus
    # der Antwortzeit auf übereinstimmende Präfixe schließen könnten.
    if not hmac.compare_digest(token, API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"fehler": {"code": "forbidden", "nachricht": "Ungültiger API-Schlüssel."}},
        )


def verify_admin_key(request: Request) -> None:
    """Admin-Key — ausschliesslich fuer app/routers/admin.py.

    Fail-closed: ohne gesetzten AUTO_KI_ADMIN_API_KEY, oder wenn er versehentlich
    dem oeffentlichen Consumer-Key gleicht, ist JEDER Admin-Aufruf verboten. Es
    gibt bewusst keinen Fallback auf API_KEY.
    """
    token = _bearer(request)
    offen = bool(ADMIN_API_KEY) and not hmac.compare_digest(ADMIN_API_KEY, API_KEY)
    if not offen or not hmac.compare_digest(token, ADMIN_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"fehler": {"code": "forbidden", "nachricht": "Kein Admin-Zugriff."}},
        )
