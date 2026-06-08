"""
Retry-Logik für Gemini-Fehler.

Behandelt zwei Fehlerklassen:
  429 RESOURCE_EXHAUSTED  → warte retryDelay aus dem Fehler, dann retry
  503 UNAVAILABLE         → kurze feste Wartezeit, dann retry (transient overload)

Sonstige 4xx/5xx werden sofort weitergeworfen.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Callable, Awaitable, TypeVar

from google.genai.errors import ClientError, ServerError

log = logging.getLogger(__name__)

MAX_RETRIES_429         = 3     # 429: 1 original + 2 Wiederholungen
MAX_RETRIES_503         = 5     # 503: bis zu 5 Versuche (robuster bei Überlast)
DAILY_LIMIT_THRESHOLD_S = 3600  # retryDelay > 1 h → Tageslimit
DEFAULT_RETRY_S_429     = 60    # Fallback wenn keine retryDelay im Fehler
RETRY_DELAY_503_S       = 15    # 15s Wartezeit bei 503 (vorher 8s)


class RateLimitExhausted(Exception):
    """Wird geworfen wenn das Tageslimit erreicht ist."""
    pass


def _is_429(exc: Exception) -> bool:
    return isinstance(exc, ClientError) and exc.code == 429


def _is_503(exc: Exception) -> bool:
    return isinstance(exc, ServerError) and exc.code == 503


def _extract_retry_delay(exc: ClientError) -> float:
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
    return float(DEFAULT_RETRY_S_429)


T = TypeVar("T")


# ------------------------------------------------------------------ #
#  Sync (für generate_content und generate_content_stream)           #
# ------------------------------------------------------------------ #

def with_retry_sync(fn: Callable[[], T]) -> T:
    """
    Synchroner Retry für 429 (Rate-Limit) und 503 (Transient Overload).
    Andere Fehler werden sofort weitergegeben.
    """
    attempts_429 = 0
    attempts_503 = 0

    while True:
        try:
            return fn()

        except ClientError as exc:
            if not _is_429(exc):
                raise  # 401, 400, 403 … sofort weiterwerfen

            attempts_429 += 1
            delay = _extract_retry_delay(exc)

            if delay > DAILY_LIMIT_THRESHOLD_S:
                raise RateLimitExhausted("Tageslimit erreicht, morgen weiter.") from exc

            if attempts_429 >= MAX_RETRIES_429:
                raise RateLimitExhausted(
                    f"Tageslimit erreicht, morgen weiter. "
                    f"(429 nach {MAX_RETRIES_429} Versuchen)"
                ) from exc

            log.warning("Gemini 429 (Versuch %d/%d). Warte %.0f s …",
                        attempts_429, MAX_RETRIES_429, delay)
            time.sleep(delay)

        except ServerError as exc:
            if not _is_503(exc):
                raise  # andere 5xx sofort

            attempts_503 += 1
            if attempts_503 >= MAX_RETRIES_503:
                raise  # nach 3x 503 aufgeben

            log.warning("Gemini 503 transient (Versuch %d/%d). Warte %d s …",
                        attempts_503, MAX_RETRIES_503, RETRY_DELAY_503_S)
            time.sleep(RETRY_DELAY_503_S)


# ------------------------------------------------------------------ #
#  Async (für async generate_content)                                #
# ------------------------------------------------------------------ #

async def with_retry(fn: Callable[[], Awaitable[T]]) -> T:
    """Asynchroner Retry für 429 und 503."""
    attempts_429 = 0
    attempts_503 = 0

    while True:
        try:
            return await fn()

        except ClientError as exc:
            if not _is_429(exc):
                raise

            attempts_429 += 1
            delay = _extract_retry_delay(exc)

            if delay > DAILY_LIMIT_THRESHOLD_S:
                raise RateLimitExhausted("Tageslimit erreicht, morgen weiter.") from exc

            if attempts_429 >= MAX_RETRIES_429:
                raise RateLimitExhausted(
                    f"Tageslimit erreicht, morgen weiter. "
                    f"(429 nach {MAX_RETRIES_429} Versuchen)"
                ) from exc

            log.warning("Gemini 429 async (Versuch %d/%d). Warte %.0f s …",
                        attempts_429, MAX_RETRIES_429, delay)
            await asyncio.sleep(delay)

        except ServerError as exc:
            if not _is_503(exc):
                raise

            attempts_503 += 1
            if attempts_503 >= MAX_RETRIES_503:
                raise

            log.warning("Gemini 503 async transient (Versuch %d/%d). Warte %d s …",
                        attempts_503, MAX_RETRIES_503, RETRY_DELAY_503_S)
            await asyncio.sleep(RETRY_DELAY_503_S)
