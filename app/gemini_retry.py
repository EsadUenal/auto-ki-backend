"""
Retry-Logik für Gemini-Fehler — EINE vereinheitlichte Strategie für alle drei
transienten Fehlerklassen, die bei einem Gemini-Aufruf auftreten können:

  429 RESOURCE_EXHAUSTED  → warte retryDelay aus dem Fehler (oder exponentiell
                            wachsend, falls Google keinen Wert mitliefert)
  5xx Providerfehler      → exponentielles Backoff (kurze Überlast-Spitzen)
  504 DEADLINE_EXCEEDED   → dasselbe Backoff (Generierung lief noch,
                            riss aber die Server-Deadline) — siehe
                            _ist_transienter_serverfehler
  Netzwerkfehler/Timeouts → exponentielles Backoff (httpx.TransportError:
                            Verbindungsabbruch, Timeout, DNS-Fehler, ...)

Alle retrybaren Klassen teilen EIN gemeinsames, kleines Versuchsbudget und enden
nach dessen Ausschoepfung in EINER gemeinsamen Exception-
Basisklasse (GeminiFehlgeschlagen), damit Aufrufer (Chat, Kauf-/Verkaufscheck)
nicht drei verschiedene Fehlerarten einzeln behandeln müssen — ein einziges
`except GeminiFehlgeschlagen` genügt, um dem Nutzer zuverlässig eine
verständliche Meldung zu zeigen statt eines rohen Fehlers oder Stacktraces.

Sonstige 4xx/5xx (401, 400, 403 ...) werden sofort weitergeworfen — ein Retry
würde dort nie erfolgreich sein.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from typing import Callable, Awaitable, TypeVar

import httpx
from google.genai.errors import ClientError, ServerError

from app.config import (
    GEMINI_MAX_ATTEMPTS,
    GEMINI_RETRY_BASE_SECONDS,
    GEMINI_RETRY_CAP_SECONDS,
    GEMINI_TIMEOUT_SECONDS,
    GEMINI_TOTAL_TIMEOUT_SECONDS,
    LLM_MODEL,
)
from app.provider_control import (
    ProviderCallLimitExceeded,
    claim_call,
    log_provider_event,
)

log = logging.getLogger(__name__)

# Eine einzige, konsistente Nutzermeldung für JEDEN Gemini-Totalausfall — Chat,
# Kauf-Check und Verkaufs-Check zeigen dieselbe professionelle Formulierung
# statt technischer Fehlertexte oder rohem Exception-Text.
KI_UEBERLASTET_NACHRICHT = (
    "Der KI-Dienst ist momentan stark ausgelastet. Bitte versuche es in wenigen "
    "Sekunden erneut."
)

MAX_RETRIES_429         = GEMINI_MAX_ATTEMPTS  # Kompatibilitaetsalias: Gesamtversuche
MAX_RETRIES_503         = GEMINI_MAX_ATTEMPTS
MAX_RETRIES_NETWORK     = GEMINI_MAX_ATTEMPTS
DAILY_LIMIT_THRESHOLD_S = 3600  # retryDelay > 1 h → Tageslimit
DEFAULT_RETRY_S_429     = GEMINI_RETRY_BASE_SECONDS
RETRY_DELAY_503_S       = GEMINI_RETRY_BASE_SECONDS
RETRY_DELAY_503_CAP_S   = GEMINI_RETRY_CAP_SECONDS
RETRY_DELAY_NETWORK_S   = GEMINI_RETRY_BASE_SECONDS
RETRY_DELAY_NETWORK_CAP_S = GEMINI_RETRY_CAP_SECONDS


def _exponential_delay(basis_s: float, versuch: int, cap_s: float) -> float:
    """Exponentielles Backoff (Versuch 0 -> basis_s, Versuch 1 -> 2×basis_s, ...), gedeckelt."""
    return min(basis_s * (2 ** versuch), cap_s)


class GeminiFehlgeschlagen(Exception):
    """
    Gemeinsame Basisklasse: Gemini konnte trotz vollständig ausgeschöpftem Retry
    KEINE verwertbare Antwort liefern. Aufrufer sollten darauf mit einer
    freundlichen Nutzermeldung reagieren (z.B. "Der KI-Dienst ist momentan stark
    ausgelastet...") und — falls ein Check-Kontingent für diese Anfrage bereits
    verbraucht wurde — dieses zurückerstatten, da der Nutzer keine Gegenleistung
    erhalten hat.
    """
    pass


class RateLimitExhausted(GeminiFehlgeschlagen):
    """429 Rate-Limit: entweder Tageslimit (Google meldet retryDelay > 1h) oder
    alle 429-Retries ausgeschöpft."""
    pass


class GeminiQuotaErschoepft(RateLimitExhausted):
    """Laengerfristige/taegliche Quote: absichtlich ohne Retry."""


class GeminiPermanentFehler(GeminiFehlgeschlagen):
    """Auth, ungueltige Anfrage oder andere nicht retrybare Provider-Antwort."""


class GeminiAntwortUngueltig(GeminiPermanentFehler):
    """Provider-Antwort ist leer, unparsebar oder strukturell unbrauchbar."""


class GeminiVoruebergehendNichtErreichbar(GeminiFehlgeschlagen):
    """503 Überlastung, 504 Deadline oder Netzwerkfehler — nach Ausschöpfen
    aller Retries."""
    pass


def _is_429(exc: Exception) -> bool:
    return isinstance(exc, ClientError) and exc.code == 429


# Transiente Server-Antworten von Gemini. BEIDE bedeuten fachlich dasselbe:
# "gerade nicht lieferbar, gleich vielleicht schon" — und beide werden deshalb
# mit derselben bestehenden Backoff-Mechanik wiederholt (KEINE zweite parallele
# Retry-Architektur).
#
#   503 UNAVAILABLE       Google lehnt direkt ab ("This model is currently
#                         experiencing high demand").
#   504 DEADLINE_EXCEEDED Die Generierung lief noch, hat aber die Deadline
#                         gerissen, die der google-genai-SDK aus
#                         HttpOptions.timeout als Header `X-Server-Timeout`
#                         mitschickt. Das ist eine ECHTE Serverantwort (nicht
#                         der lokale httpx-Timeout — der käme als
#                         httpx.TimeoutException und wird unten separat
#                         behandelt).
#
# Warum das vorher fehlte und was es angerichtet hat (Root-Cause-Audit):
# `ServerError(504)` ist weder `GeminiFehlgeschlagen` noch `RechercheUnzureichend`.
# Der Fehler lief deshalb an JEDEM Router-`except` vorbei bis in
# `main.generic_exception_handler` -> HTTP 500 "Ein interner Fehler ist
# aufgetreten", OHNE `refund_check_credit`. Der Nutzer verlor bei jeder
# Provider-Störung ein bezahltes Check-Kontingent und bekam eine unbrauchbare
# Meldung. Zusätzlich wiederholte NIEMAND den Call: das SDK-eigene Retry ist
# mangels `retry_options` deaktiviert (retry_args(None) -> stop_after_attempt(1)),
# obwohl der SDK-Default 504 sehr wohl als retrybar führt.
#
# Nur die ueblichen Gateway-/Provider-5xx sind kurz retrybar; das gemeinsame
# Drei-Versuchs-Budget verhindert minutenlange oder multiplizierte Schleifen.
_TRANSIENTE_SERVER_CODES = (500, 502, 503, 504)


def _ist_transienter_serverfehler(exc: Exception) -> bool:
    return isinstance(exc, ServerError) and exc.code in _TRANSIENTE_SERVER_CODES


def _extract_retry_delay(exc: ClientError) -> float | None:
    """Gibt die von Google im Fehler mitgeteilte retryDelay zurück, oder None wenn
    keine vorhanden ist (Aufrufer wendet dann exponentielles Backoff an, siehe
    _fallback_429_delay)."""
    try:
        details = exc.details
        error_details = (
            details.get("error", {}).get("details", [])
            if isinstance(details, dict) else []
        )
        for item in error_details:
            delay_str = item.get("retryDelay", "")
            if delay_str:
                total = 0.0
                for minutes in re.findall(r"(\d+)m", delay_str):
                    total += int(minutes) * 60
                for seconds in re.findall(r"([\d.]+)s", delay_str):
                    total += float(seconds)
                if total > 0:
                    return total
    except Exception:
        pass
    return None


def _ist_quota_erschoepft(exc: ClientError) -> bool:
    """Erkennt taegliche/langfristige Quota anhand strukturierter Details/Text."""
    delay = _extract_retry_delay(exc)
    if delay is not None and delay > DAILY_LIMIT_THRESHOLD_S:
        return True
    try:
        text = f"{exc} {exc.details}".lower()
    except Exception:
        text = str(exc).lower()
    marker = (
        "per day", "per_day", "daily", "tageslimit", "day quota",
        "requestsperday", "request per day", "quota exhausted",
        "check your plan and billing", "billing details",
    )
    return any(m in text for m in marker)


def _fallback_429_delay(versuch: int) -> float:
    """Exponentielles Backoff für den seltenen Fall, dass Google KEINE retryDelay im
    Fehler mitliefert — vorher fixe DEFAULT_RETRY_S_429 (60s) bei JEDEM Versuch,
    jetzt wachsend (60s, 120s, ...), damit spätere Versuche dem Server mehr Zeit
    zur Erholung geben. Ist im Fehler ein Wert angegeben, ist DIESER weiterhin
    maßgeblich (Google kennt seine eigene Rate-Limit-Situation am besten) —
    dieser Fallback greift nur, wenn kein Wert vorhanden ist."""
    return _exponential_delay(DEFAULT_RETRY_S_429, versuch, GEMINI_RETRY_CAP_SECONDS)


def _retry_delay(exc: Exception, attempt: int) -> float:
    if isinstance(exc, ClientError):
        supplied = _extract_retry_delay(exc)
        if supplied is not None:
            return min(supplied, GEMINI_RETRY_CAP_SECONDS)
    basis = _exponential_delay(GEMINI_RETRY_BASE_SECONDS, attempt - 1, GEMINI_RETRY_CAP_SECONDS)
    return basis + random.uniform(0.0, min(0.25, basis * 0.1))


def classify_gemini_error(exc: Exception) -> tuple[str, bool]:
    """(Klasse, retrybar). Keine Stringvergleiche in den Aufrufern."""
    if isinstance(exc, ProviderCallLimitExceeded):
        return "call_limit", False
    if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)):
        return "timeout", True
    if isinstance(exc, httpx.TransportError):
        return "network", True
    if isinstance(exc, ClientError):
        if exc.code == 429:
            return ("quota_exhausted", False) if _ist_quota_erschoepft(exc) else ("rate_limit", True)
        if exc.code in (401, 403):
            return "auth", False
        return "invalid_request", False
    if isinstance(exc, ServerError):
        return ("provider_5xx", True) if exc.code in _TRANSIENTE_SERVER_CODES else ("provider_error", False)
    return "internal", False


def _final_exception(kind: str, attempts: int, exc: Exception) -> GeminiFehlgeschlagen:
    if kind == "quota_exhausted":
        return GeminiQuotaErschoepft("Provider-Kontingent ist laengerfristig erschoepft.")
    if kind == "rate_limit":
        return RateLimitExhausted(f"Rate-Limit nach {attempts} Versuchen weiterhin aktiv.")
    if kind in ("timeout", "network", "provider_5xx"):
        return GeminiVoruebergehendNichtErreichbar(
            f"Provider voruebergehend nicht erreichbar ({kind}, {attempts} Versuch(e))."
        )
    if kind == "call_limit":
        return GeminiVoruebergehendNichtErreichbar("Provider-Aufrufbudget der Aktion erschoepft.")
    return GeminiPermanentFehler(f"Nicht retrybarer Provider-Fehler ({kind}).")


T = TypeVar("T")


# ------------------------------------------------------------------ #
#  Sync (für generate_content und generate_content_stream)           #
# ------------------------------------------------------------------ #

def with_retry_sync(fn: Callable[[], T], *, model: str | None = None) -> T:
    """
    Synchroner Retry für 429 (Rate-Limit), 503 (Transient Overload) und
    Netzwerkfehler (Timeout/Verbindungsabbruch). Andere Fehler werden sofort
    weitergegeben.
    """
    started_total = time.monotonic()
    model_name = model or LLM_MODEL
    for attempt in range(1, GEMINI_MAX_ATTEMPTS + 1):
        try:
            claim_call("gemini", retry=attempt > 1)
            call_started = time.monotonic()
            result = fn()
            log_provider_event("gemini", status="success", attempt=attempt, started=call_started, model=model_name)
            return result
        except Exception as exc:
            kind, retryable = classify_gemini_error(exc)
            log_provider_event("gemini", status="error", error_class=kind,
                               attempt=attempt, started=locals().get("call_started", started_total), model=model_name)
            elapsed = time.monotonic() - started_total
            if not retryable or attempt >= GEMINI_MAX_ATTEMPTS or elapsed >= GEMINI_TOTAL_TIMEOUT_SECONDS:
                raise _final_exception(kind, attempt, exc) from exc
            delay = min(_retry_delay(exc, attempt), max(0.0, GEMINI_TOTAL_TIMEOUT_SECONDS - elapsed))
            log.warning("provider_retry provider=gemini class=%s attempt=%d/%d delay_s=%.2f",
                        kind, attempt, GEMINI_MAX_ATTEMPTS, delay)
            time.sleep(delay)
    raise GeminiVoruebergehendNichtErreichbar("Gemini-Aufrufbudget erschoepft.")


# ------------------------------------------------------------------ #
#  Async (für async generate_content)                                #
# ------------------------------------------------------------------ #

async def with_retry(fn: Callable[[], Awaitable[T]], *, model: str | None = None) -> T:
    """Asynchroner Retry für 429, 503 und Netzwerkfehler — siehe with_retry_sync."""
    started_total = time.monotonic()
    model_name = model or LLM_MODEL
    for attempt in range(1, GEMINI_MAX_ATTEMPTS + 1):
        try:
            claim_call("gemini", retry=attempt > 1)
            call_started = time.monotonic()
            remaining = max(0.1, GEMINI_TOTAL_TIMEOUT_SECONDS - (call_started - started_total))
            result = await asyncio.wait_for(fn(), timeout=min(GEMINI_TIMEOUT_SECONDS, remaining))
            log_provider_event("gemini", status="success", attempt=attempt, started=call_started, model=model_name)
            return result
        except Exception as exc:
            kind, retryable = classify_gemini_error(exc)
            log_provider_event("gemini", status="error", error_class=kind,
                               attempt=attempt, started=locals().get("call_started", started_total), model=model_name)
            elapsed = time.monotonic() - started_total
            if not retryable or attempt >= GEMINI_MAX_ATTEMPTS or elapsed >= GEMINI_TOTAL_TIMEOUT_SECONDS:
                raise _final_exception(kind, attempt, exc) from exc
            delay = min(_retry_delay(exc, attempt), max(0.0, GEMINI_TOTAL_TIMEOUT_SECONDS - elapsed))
            log.warning("provider_retry provider=gemini class=%s attempt=%d/%d delay_s=%.2f",
                        kind, attempt, GEMINI_MAX_ATTEMPTS, delay)
            await asyncio.sleep(delay)
    raise GeminiVoruebergehendNichtErreichbar("Gemini-Aufrufbudget erschoepft.")
