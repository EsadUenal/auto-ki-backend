from __future__ import annotations

import logging
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.config import RATE_LIMIT, CORS_ORIGINS, DB_PATH
from app.database import ensure_tables
from app.routers import fahrzeug, chat, admin, kaufcheck, verkaufscheck, user_auth, conversations, checks, payments, posters, ebooks, ersatzteile
from app.llm import warmup_chroma
from app.utf8 import UTF8JSONResponse


def _utf8_json(status_code: int, content: dict) -> UTF8JSONResponse:
    return UTF8JSONResponse(status_code=status_code, content=content)


limiter = Limiter(key_func=get_remote_address, default_limits=[RATE_LIMIT])

app = FastAPI(
    title="Vira Backend",
    description="Vira — KI-Autoberatung. Kauf, Verkauf, technisches Wissen.",
    version="0.1.0",
    default_response_class=UTF8JSONResponse,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,   # konkrete Origins nötig damit Cookies funktionieren
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,       # httpOnly-Cookie wird mitgeschickt
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.on_event("startup")
def on_startup() -> None:
    """Tabellen anlegen (idempotent) + ChromaDB vorladen."""
    ensure_tables()
    warmup_chroma()


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


app.include_router(fahrzeug.router,      prefix="/api/v1")
app.include_router(chat.router,          prefix="/api/v1")
app.include_router(admin.router,         prefix="/api/v1")
app.include_router(kaufcheck.router,     prefix="/api/v1")
app.include_router(verkaufscheck.router, prefix="/api/v1")
app.include_router(user_auth.router,     prefix="/api/v1")
app.include_router(conversations.router, prefix="/api/v1")
app.include_router(checks.router,        prefix="/api/v1")
app.include_router(payments.router,      prefix="/api/v1")
app.include_router(posters.router,       prefix="/api/v1")
app.include_router(ebooks.router,        prefix="/api/v1")
app.include_router(ersatzteile.router,   prefix="/api/v1")


@app.get("/health")
def health():
    import sqlite3
    db_path = str(DB_PATH)
    try:
        conn = sqlite3.connect(db_path)
        tables = sorted(
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        )
        conn.close()
    except Exception as e:
        tables = [f"FEHLER: {e}"]
    return {"status": "ok", "db_path": db_path, "tables": tables}
