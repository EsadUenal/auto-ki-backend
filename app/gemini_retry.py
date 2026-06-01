"""
Retry-Logik für Gemini-429-Fehler (RESOURCE_EXHAUSTED).

Gemini gibt bei 429 eine retryDelay-Angabe zurück, z.B.:
  {"error": {"details": [{"@type": "...RetryInfo", "retryDelay": "30s"}]}}

Verhalten:
  - Extrahiere retryDelay aus dem Fehler; falls nicht vorhanden: exponentieller Fallback.
  - Warte genau die angegebene Zeit, dann erneuter Versuch.
  - Nach MAX_RETRIES Fehlern: RateLimitExhausted mit "Tageslimit erreicht"-Meldung.
  - retryDelay > DAILY_LIMIT_THRESHOLD_S → sofort als Tageslimit werten, nicht warten.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Callable, Awaitable, TypeVar

from google.genai.errors import ClientError

log = logging.getLogger(__name__)

MAX_RETRIES             = 3     # Versuche insgesamt (1 original + 2 Wiederholungen)
DAILY_LIMIT_THRESHOLD_S = 3600  # retryDelay > 1 h → Tageslimit
DEFAULT_RETRY_S         = 60    # Fallback wenn keine retryDelay im Fehler


class RateLimitExhausted(Exception):
    """Wird geworfen wenn das Tageslimit erreicht ist."""
    pass


def _is_429(exc: Exception) -> bool:
    return isinstance(exc, ClientError) and exc.code == 429


def _extract_retry_delay(exc: ClientError) -> float:
    """
    Liest retryDelay aus dem Gemini-Fehler-JSON.
    Format: "30s" oder "1.5s" → float Sekunden.
    Gibt DEFAULT_RETRY_S zurück wenn nichts gefunden.
    """
    try:
        details = exc.details  # voller response-JSON-Dict
        # Pfad: details["error"]["details"] → Liste mit RetryInfo
        error_details = (
            details.get("error", {}).get("details", [])
            if isinstance(details, dict)
            else []
        )
        for item in error_details:
            delay_str = item.get("retryDelay", "")
            if delay_str:
                # "30s" → 30.0  |  "1.500s" → 1.5  |  "2m30s" → 150.0
                total = 0.0
                for minutes in re.findall(r"(\d+)m", delay_str):
                    total += int(minutes) * 60
                for seconds in re.findall(r"([\d.]+)s", delay_str):
                    total += float(seconds)
                if total > 0:
                    return total
    except Exception:
        pass
    return float(DEFAULT_RETRY_S)


T = TypeVar("T")


async def with_retry(fn: Callable[[], Awaitable[T]]) -> T:
    """
    Ruft fn() auf und wiederholt bei 429 automatisch nach retryDelay.
    fn muss ein awaitable zurückgeben.

    Beispiel:
        result = await with_retry(lambda: client.models.generate_content_async(...))
    """
    last_exc: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return await fn()
        except ClientError as exc:
            if not _is_429(exc):
                raise  # Andere Fehler (401, 400 …) sofort weiterwerfen

            delay = _extract_retry_delay(exc)
            last_exc = exc

            if delay > DAILY_LIMIT_THRESHOLD_S:
                raise RateLimitExhausted(
                    "Tageslimit erreicht, morgen weiter."
                ) from exc

            if attempt == MAX_RETRIES:
                break  # Schleife verlassen → Meldung unten

            log.warning(
                "Gemini 429 (Versuch %d/%d). Warte %.0f s ...",
                attempt, MAX_RETRIES, delay,
            )
            await asyncio.sleep(delay)

    raise RateLimitExhausted(
        f"Tageslimit erreicht, morgen weiter. "
        f"(Gemini 429 nach {MAX_RETRIES} Versuchen)"
    ) from last_exc


def with_retry_sync(fn: Callable[[], T]) -> T:
    """
    Synchrone Variante für Streaming-Calls
    (generate_content_stream gibt einen sync Iterator zurück).
    """
    import time

    last_exc: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn()
        except ClientError as exc:
            if not _is_429(exc):
                raise

            delay = _extract_retry_delay(exc)
            last_exc = exc

            if delay > DAILY_LIMIT_THRESHOLD_S:
                raise RateLimitExhausted(
                    "Tageslimit erreicht, morgen weiter."
                ) from exc

            if attempt == MAX_RETRIES:
                break

            log.warning(
                "Gemini 429 Streaming (Versuch %d/%d). Warte %.0f s ...",
                attempt, MAX_RETRIES, delay,
            )
            time.sleep(delay)

    raise RateLimitExhausted(
        f"Tageslimit erreicht, morgen weiter. "
        f"(Gemini 429 nach {MAX_RETRIES} Versuchen)"
    ) from last_exc
