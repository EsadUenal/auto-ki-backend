from fastapi import Request, HTTPException, status
from app.config import API_KEY


def verify_api_key(request: Request) -> None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"fehler": {"code": "unauthorized", "nachricht": "Authorization-Header fehlt oder ungültig."}},
        )
    token = auth.removeprefix("Bearer ").strip()
    if token != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"fehler": {"code": "forbidden", "nachricht": "Ungültiger API-Schlüssel."}},
        )
