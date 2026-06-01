from __future__ import annotations

import logging
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.config import RATE_LIMIT
from app.routers import fahrzeug, chat, admin
from app.utf8 import UTF8JSONResponse


def _utf8_json(status_code: int, content: dict) -> UTF8JSONResponse:
    return UTF8JSONResponse(status_code=status_code, content=content)


limiter = Limiter(key_func=get_remote_address, default_limits=[RATE_LIMIT])

app = FastAPI(
    title="Auto-KI Backend",
    description="Auf Autos spezialisierte Wissens-KI. Phase 1.",
    version="0.1.0",
    default_response_class=UTF8JSONResponse,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # Testphase: alle Origins (file://, localhost, etc.)
    allow_methods=["*"],          # OPTIONS-Preflight + POST/GET
    allow_headers=["*"],          # Authorization, Content-Type usw.
    allow_credentials=False,      # muss False bleiben wenn allow_origins="*"
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return _utf8_json(429, {"fehler": {"code": "rate_limit", "nachricht": "Zu viele Anfragen, bitte kurz warten."}})


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logging.exception("Unhandled error: %s", exc)
    return _utf8_json(500, {"fehler": {"code": "interner_fehler", "nachricht": "Ein interner Fehler ist aufgetreten."}})


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "fehler" in detail:
        content = detail
    else:
        content = {"fehler": {"code": str(exc.status_code), "nachricht": str(detail)}}
    return _utf8_json(exc.status_code, content)


app.include_router(fahrzeug.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")


@app.get("/health")
def health():
    return {"status": "ok"}
