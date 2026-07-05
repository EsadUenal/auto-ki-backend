"""
Regressionstests für die Production-Readiness-Sicherheitsprüfung.

Deckt die in dieser Session gefundenen und behobenen Probleme ab:
  1. CORS: "null"-Origin nicht mehr im Default zugelassen
  2. Globales Rate-Limit (SlowAPIMiddleware) tatsächlich aktiv
  3. Konstante Vergleichszeit beim API-Key-Check
  4. Input-Limits gegen DOS/Kostenmissbrauch (Chat/Kauf-/Verkaufscheck)
  5. Stripe-Webhook: Idempotenz-Claim wird bei Verarbeitungsfehlern zurückgerollt

Kein Netzwerk-/Gemini-/Stripe-Aufruf nötig für die meisten Tests — reine
Struktur-/Verhaltensprüfung. Ausführen: python test_security_audit.py
"""

import inspect
from unittest.mock import Mock

from fastapi import HTTPException
from pydantic import ValidationError

FEHLER = []


def check(name: str, bedingung: bool, detail: str = ""):
    if bedingung:
        print(f"[OK] {name}")
    else:
        FEHLER.append(f"[FEHLER] {name} — {detail}")


# ---------- 1) CORS: "null"-Origin nicht mehr im Default ----------
import app.config as config

check(
    "CORS-Default enthält kein 'null'",
    "null" not in config.CORS_ORIGINS,
    f"CORS_ORIGINS={config.CORS_ORIGINS}",
)
check(
    "CORS-Default enthält weiterhin die lokalen Dev-Origins",
    "http://localhost:3000" in config.CORS_ORIGINS,
)

# ---------- 2) Globales Rate-Limit tatsächlich verdrahtet ----------
import app.main as main

middleware_klassen = [m.cls.__name__ for m in main.app.user_middleware]
check(
    "SlowAPIMiddleware ist registriert (aktiviert default_limits global)",
    "SlowAPIMiddleware" in middleware_klassen,
    f"Registrierte Middlewares: {middleware_klassen}",
)
check(
    "_MaxBodySizeMiddleware ist registriert",
    "_MaxBodySizeMiddleware" in middleware_klassen,
    f"Registrierte Middlewares: {middleware_klassen}",
)
check(
    "app.state.limiter existiert und hat default_limits",
    bool(getattr(main.app.state.limiter, "_default_limits", None)),
)

# Dedizierte Login/Register-Limits (Brute-Force-Härtung)
import app.routers.user_auth as user_auth

check(
    "user_auth.py hat eine eigene Limiter-Instanz",
    hasattr(user_auth, "limiter"),
)
login_src = inspect.getsource(user_auth.login)
register_src = inspect.getsource(user_auth.register)
check("login() akzeptiert 'request' (Voraussetzung für @limiter.limit)", "request" in inspect.signature(user_auth.login).parameters)
check("register() akzeptiert 'request' (Voraussetzung für @limiter.limit)", "request" in inspect.signature(user_auth.register).parameters)

# ---------- 3) Konstante Vergleichszeit beim API-Key ----------
import app.auth as auth_module

auth_src = inspect.getsource(auth_module.verify_api_key)
check(
    "verify_api_key() nutzt hmac.compare_digest statt '!='",
    "compare_digest" in auth_src,
)

_orig_api_key = auth_module.API_KEY
auth_module.API_KEY = "testkey123"
try:
    req_ok = Mock()
    req_ok.headers = {"Authorization": "Bearer testkey123"}
    auth_module.verify_api_key(req_ok)  # darf nicht werfen
    check("verify_api_key() akzeptiert korrekten Key weiterhin", True)
except HTTPException:
    check("verify_api_key() akzeptiert korrekten Key weiterhin", False, "korrekter Key wurde abgelehnt")

req_bad = Mock()
req_bad.headers = {"Authorization": "Bearer falsch"}
try:
    auth_module.verify_api_key(req_bad)
    check("verify_api_key() lehnt falschen Key ab", False, "falscher Key wurde akzeptiert")
except HTTPException as e:
    check("verify_api_key() lehnt falschen Key ab", e.status_code == 403)

req_missing = Mock()
req_missing.headers = {}
try:
    auth_module.verify_api_key(req_missing)
    check("verify_api_key() lehnt fehlenden Header ab", False, "fehlender Header wurde akzeptiert")
except HTTPException as e:
    check("verify_api_key() lehnt fehlenden Header ab", e.status_code == 401)
auth_module.API_KEY = _orig_api_key

# ---------- 4) Input-Limits ----------
from app.models import ChatRequest, KaufCheckRequest, VerkaufsCheckRequest

check("Normale Chat-Nachricht bleibt gültig", ChatRequest(message="Hallo").message == "Hallo")

try:
    ChatRequest(message="A" * 50_000)
    check("Riesige Chat-Nachricht (50k Zeichen) wird abgelehnt", False)
except ValidationError:
    check("Riesige Chat-Nachricht (50k Zeichen) wird abgelehnt", True)

try:
    ChatRequest(message="x", verlauf=[{"rolle": "user", "text": "hi"}] * 200)
    check("Verlauf mit 200 Nachrichten wird abgelehnt (Limit 100)", False)
except ValidationError:
    check("Verlauf mit 200 Nachrichten wird abgelehnt (Limit 100)", True)

check(
    "Normaler Verlauf (20 Nachrichten) bleibt gültig",
    len(ChatRequest(message="x", verlauf=[{"rolle": "user", "text": "hi"}] * 20).verlauf) == 20,
)

try:
    KaufCheckRequest(marke="BMW", bild_base64="A" * 7_000_000)
    check("Kaufcheck: riesiges bild_base64 (7 MB) wird abgelehnt", False)
except ValidationError:
    check("Kaufcheck: riesiges bild_base64 (7 MB) wird abgelehnt", True)

check(
    "Kaufcheck: normale Anfrage bleibt gültig",
    KaufCheckRequest(marke="BMW", modell="320d", freitext="Scheckheftgepflegt.").marke == "BMW",
)

try:
    VerkaufsCheckRequest(marke="VW", maengel=["x"] * 500)
    check("Verkaufscheck: riesige maengel-Liste wird abgelehnt (Limit 100)", False)
except ValidationError:
    check("Verkaufscheck: riesige maengel-Liste wird abgelehnt (Limit 100)", True)

# ---------- 5) Stripe-Webhook: Idempotenz-Rollback bei Fehlern ----------
import app.routers.payments as payments

check(
    "payments.py hat eine separate _verarbeite_event()-Funktion",
    hasattr(payments, "_verarbeite_event"),
)
webhook_src = inspect.getsource(payments.stripe_webhook)
check(
    "Webhook rollt den Idempotenz-Claim bei Fehlern zurück (DELETE FROM stripe_events)",
    "DELETE FROM stripe_events" in webhook_src,
)
check(
    "Webhook beansprucht das Event atomar (INSERT OR IGNORE) VOR der Verarbeitung",
    "INSERT OR IGNORE INTO stripe_events" in webhook_src,
)
# Der Claim muss VOR dem try/_verarbeite_event-Aufruf stehen, aber die Fehlerbehandlung
# (DELETE) muss im except-Zweig um _verarbeite_event() liegen — Reihenfolge grob prüfen:
idx_insert = webhook_src.index("INSERT OR IGNORE INTO stripe_events")
idx_call   = webhook_src.index("_verarbeite_event(")
idx_delete = webhook_src.index("DELETE FROM stripe_events")
check(
    "Reihenfolge korrekt: Claim vor Verarbeitung vor Rollback-Pfad",
    idx_insert < idx_call < idx_delete,
)

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER:\n")
    for f in FEHLER:
        print(f)
    raise SystemExit(1)
else:
    print("Alle Security-Regressionstests bestanden.")
