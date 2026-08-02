from __future__ import annotations

import asyncio
import logging
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.config import RATE_LIMIT, CORS_ORIGINS, CORS_IS_DEFAULT, DB_PATH, API_KEY, JWT_SECRET, LOG_LEVEL, DB_BACKUP_INTERVAL_SECONDS
from app.database import ensure_tables
from app.db_writer import backup_sqlite_now
from app.routers import fahrzeug, chat, admin, kaufcheck, verkaufscheck, user_auth, conversations, checks, payments, posters, ebooks, ersatzteile, analyse_frage, dealer
from app.llm import warmup_chroma
from app.utf8 import UTF8JSONResponse

# App-Logging konfigurieren, bevor irgendein Logger benutzt wird. Ohne dies bleibt
# der App-eigene log.info(...)-Output (DB-Pfad, Backups, Gemini-Retries) unsichtbar,
# weil die Python-Root-Vorgabe erst ab WARNING ausgibt. Level per AUTO_KI_LOG_LEVEL
# steuerbar. force=True überschreibt einen evtl. von uvicorn gesetzten Root-Handler,
# damit ein einheitliches Format mit Zeitstempel entsteht (wichtig für Prod-Logs).
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    force=True,
)

log = logging.getLogger(__name__)

# Maximale Request-Body-Größe (Bytes) — Sicherheitsnetz gegen übergroße Payloads
# (z.B. riesige bild_base64-Felder oder absichtlich aufgeblähte JSON-Bodies), die
# sonst vollständig in den Speicher eingelesen werden, bevor Pydantic überhaupt
# validiert. 8 MB ist großzügig genug für ein Inserat-Screenshot als Base64.
_MAX_BODY_BYTES = 8 * 1024 * 1024


class _MaxBodySizeMiddleware:
    """ASGI-Middleware: lehnt Requests mit zu großem Content-Length sofort ab
    (413), bevor der Body überhaupt gelesen/geparst wird."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            content_length = headers.get(b"content-length")
            if content_length is not None:
                try:
                    if int(content_length) > _MAX_BODY_BYTES:
                        response = JSONResponse(
                            status_code=413,
                            content={"fehler": {"code": "payload_too_large",
                                                 "nachricht": "Anfrage zu groß."}},
                        )
                        await response(scope, receive, send)
                        return
                except ValueError:
                    pass
        await self.app(scope, receive, send)


class _SecurityHeadersMiddleware:
    """ASGI-Middleware: setzt Standard-Security-Header auf jede Antwort.

    Railway/ein vorgeschalteter Proxy terminiert TLS, aber die API selbst
    sendet ohne dies GAR KEINE Security-Header — jeder direkte Client
    (nicht nur der Browser über das Frontend-nginx) bekäme Antworten ohne
    X-Content-Type-Options/X-Frame-Options/etc. HSTS ist hier ebenfalls
    sinnvoll: der Header wird unabhängig vom vorgeschalteten Proxy gesetzt.

    BEWUSST NICHT gesetzt (Security-Header-Audit Runde 2):
    - Cross-Origin-Resource-Policy: Das Frontend lädt E-Book-PDFs und JSON
      per fetch() mit credentials:'include' CROSS-ORIGIN (eigene Domain).
      same-origin/same-site würde diese Downloads blockieren.
    - Cross-Origin-Embedder-Policy: bräuchte auf JEDER Cross-Origin-Antwort
      einen passenden CORP-Header, sonst schlägt genau der Download-Fetch
      oben fehl. Kein technischer Nutzen (kein SharedArrayBuffer/WASM) —
      Risiko ohne Gegenwert.
    - Content-Security-Policy: absichtlich nicht aktiviert (siehe
      DEPLOYMENT_REPORT.md für einen dokumentierten Vorschlag).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend([
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"strict-transport-security", b"max-age=63072000; includeSubDomains"),
                    (b"permissions-policy", b"geolocation=(), microphone=(), camera=()"),
                    (b"cross-origin-opener-policy", b"same-origin"),
                ])
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _utf8_json(status_code: int, content: dict) -> UTF8JSONResponse:
    return UTF8JSONResponse(status_code=status_code, content=content)


# Limiter lebt in app.rate_limit, damit Router (z.B. der Stripe-Webhook) ihn per
# @limiter.exempt ohne Zirkelimport ueber main.py nutzen koennen.
from app.rate_limit import limiter

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
app.add_middleware(_MaxBodySizeMiddleware)
app.add_middleware(_SecurityHeadersMiddleware)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
# OHNE diese Middleware greift der oben konfigurierte default_limits=[RATE_LIMIT]
# NIRGENDS automatisch — SlowAPI prüft Limits nur für Routen mit explizitem
# @limiter.limit(...)-Decorator ODER wenn diese Middleware jede Anfrage gegen
# app.state.limiter prüft. Vorher hatten dadurch NUR /chat, /kaufcheck und
# /verkaufscheck (eigene @limiter.limit-Decorator mit eigener Limiter-Instanz)
# überhaupt ein Rate-Limit — u.a. /auth/login und /auth/register liefen völlig
# ungedrosselt (Brute-Force-/Registrierungs-Spam-Risiko).
app.add_middleware(SlowAPIMiddleware)


async def _periodic_backup_loop() -> None:
    """Sichert die komplette SQLite-DB in festem Takt (siehe DB_BACKUP_INTERVAL_SECONDS).

    Ergänzt die bestehende, ereignisgesteuerte Sicherung in db_writer.py (die nur
    bei Fahrzeug-Admin-Schreibvorgängen lief) um eine zeitgesteuerte Sicherung,
    die auch Nutzerkonten, Chats und Käufe abdeckt.
    """
    if DB_BACKUP_INTERVAL_SECONDS <= 0:
        log.info("Periodisches Backup deaktiviert (AUTO_KI_DB_BACKUP_INTERVAL_SECONDS<=0).")
        return
    while True:
        await asyncio.sleep(DB_BACKUP_INTERVAL_SECONDS)
        try:
            backup_sqlite_now()
        except Exception:
            log.exception("Periodisches Backup fehlgeschlagen.")


@app.on_event("startup")
def on_startup() -> None:
    """Tabellen anlegen (idempotent) + ChromaDB vorladen."""
    ensure_tables()
    warmup_chroma()
    _warn_if_insecure_defaults()
    app.state.backup_task = asyncio.create_task(_periodic_backup_loop())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    """Hintergrund-Task sauber beenden (kein Zombie-Task bei Redeploy/Restart)."""
    task = getattr(app.state, "backup_task", None)
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def _warn_if_insecure_defaults() -> None:
    """Lautes Log-Signal beim Start, falls sicherheitskritische Secrets noch auf
    dem Entwicklungs-Default stehen — leicht zu übersehen vor einem Public-Launch,
    da die App damit anstandslos weiterläuft (fail-open)."""
    if API_KEY == "dev-key-change-in-prod":
        log.warning(
            "!!! AUTO_KI_API_KEY ist nicht gesetzt — Admin-/Fahrzeug-Endpunkte "
            "verwenden den öffentlich bekannten Dev-Default. Vor Launch per "
            "Umgebungsvariable AUTO_KI_API_KEY setzen. !!!"
        )
    if JWT_SECRET == "dev-jwt-secret-change-in-prod":
        log.warning(
            "!!! AUTO_KI_JWT_SECRET ist nicht gesetzt — Login-Tokens verwenden "
            "den öffentlich bekannten Dev-Default und können von JEDEM gefälscht "
            "werden. Vor Launch per Umgebungsvariable AUTO_KI_JWT_SECRET setzen "
            "(z.B. `openssl rand -hex 32`). !!!"
        )
    if CORS_IS_DEFAULT:
        log.warning(
            "!!! AUTO_KI_CORS_ORIGINS ist nicht gesetzt — nur localhost-Origins "
            "sind erlaubt. Die echte Frontend-Domain wird sonst vom Browser "
            "blockiert (CORS). Vor Launch per Umgebungsvariable "
            "AUTO_KI_CORS_ORIGINS=https://deine-domain.de setzen. !!!"
        )


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


# Kurze, kundentaugliche Texte je Pydantic-Fehlertyp — ohne Eintrag fällt
# validation_error_handler() auf einen generischen Satz zurück.
_VALIDIERUNGS_TEXTE = {
    "string_too_long":  "darf höchstens {max_length} Zeichen lang sein.",
    "string_too_short": "muss mindestens {min_length} Zeichen lang sein.",
    "missing":          "ist erforderlich.",
    "int_parsing":      "muss eine Zahl sein.",
    "float_parsing":    "muss eine Zahl sein.",
    "greater_than_equal": "muss mindestens {ge} sein.",
    "less_than_equal":  "darf höchstens {le} sein.",
}


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Ersetzt FastAPIs Standard-422-Antwort (roher JSON-Dump mit loc/type/msg/url —
    z.B. bei einer zu langen Eingabe im Kaufcheck-Feld 'Motor / Antrieb') durch eine
    kurze, für Kunden verständliche Meldung im app-eigenen {"fehler": {...}}-Format,
    konsistent mit den anderen Exception-Handlern in dieser Datei."""
    fehler = exc.errors()
    if fehler:
        erster = fehler[0]
        feld = ".".join(str(p) for p in erster.get("loc", []) if p != "body") or "Eingabe"
        vorlage = _VALIDIERUNGS_TEXTE.get(erster.get("type", ""), "ist ungültig.")
        try:
            text = vorlage.format(**erster.get("ctx", {}))
        except (KeyError, IndexError):
            text = "ist ungültig."
        nachricht = f"{feld}: {text}"
    else:
        nachricht = "Eingabe ist ungültig."
    return _utf8_json(422, {"fehler": {"code": "validierung", "nachricht": nachricht}})


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
app.include_router(analyse_frage.router, prefix="/api/v1")
app.include_router(dealer.router,        prefix="/api/v1")


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
