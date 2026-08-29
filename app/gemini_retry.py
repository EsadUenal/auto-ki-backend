"""
Retry-Logik für Gemini-Fehler — EINE vereinheitlichte Strategie für alle drei
transienten Fehlerklassen, die bei einem Gemini-Aufruf auftreten können:

  429 RESOURCE_EXHAUSTED  → warte retryDelay aus dem Fehler (oder exponentiell
                            wachsend, falls Google keinen Wert mitliefert)
  503 UNAVAILABLE         → exponentielles Backoff (kurze Überlast-Spitzen)
  504 DEADLINE_EXCEEDED   → dasselbe Backoff wie 503 (Generierung lief noch,
                            riss aber die Server-Deadline) — siehe
                            _ist_transienter_serverfehler
  Netzwerkfehler/Timeouts → exponentielles Backoff (httpx.TransportError:
                            Verbindungsabbruch, Timeout, DNS-Fehler, ...)

Alle drei enden nach Ausschöpfen ihrer Versuche in EINER gemeinsamen Exception-
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
import re
import time
from typing import Callable, Awaitable, TypeVar

import httpx
from google.genai.errors import ClientError, ServerError

log = logging.getLogger(__name__)

# Eine einzige, konsistente Nutzermeldung für JEDEN Gemini-Totalausfall — Chat,
# Kauf-Check und Verkaufs-Check zeigen dieselbe professionelle Formulierung
# statt technischer Fehlertexte oder rohem Exception-Text.
KI_UEBERLASTET_NACHRICHT = (
    "Der KI-Dienst ist momentan stark ausgelastet. Bitte versuche es in wenigen "
    "Sekunden erneut."
)

MAX_RETRIES_429         = 3     # 429: 1 original + 2 Wiederholungen
MAX_RETRIES_503         = 5     # 503: bis zu 5 Versuche (robuster bei Überlast)
MAX_RETRIES_NETWORK     = 3     # Verbindungsabbruch/Timeout: meist sehr kurzlebig
DAILY_LIMIT_THRESHOLD_S = 3600  # retryDelay > 1 h → Tageslimit
DEFAULT_RETRY_S_429     = 60    # Fallback-Basis wenn keine retryDelay im Fehler (siehe unten)
RETRY_DELAY_503_S       = 2     # 503: Exponential-Backoff-Basis (2s, 4s, 8s, 16s, 20s-Cap)
RETRY_DELAY_503_CAP_S   = 20    # Obergrenze pro Versuch, damit MAX_RETRIES_503 nicht zu lang wird
RETRY_DELAY_NETWORK_S   = 1     # Netzwerkfehler: Basis 1s (1s, 2s, 4s)
RETRY_DELAY_NETWORK_CAP_S = 5


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
# Bewusst NICHT als transient gewertet: 500, 502 und sonstige 5xx. Ein
# pauschales "alle 5xx sind transient" würde echte, dauerhafte Fehler hinter
# minutenlangen Retries verstecken.
_TRANSIENTE_SERVER_CODES = (503, 504)


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


def _fallback_429_delay(versuch: int) -> float:
    """Exponentielles Backoff für den seltenen Fall, dass Google KEINE retryDelay im
    Fehler mitliefert — vorher fixe DEFAULT_RETRY_S_429 (60s) bei JEDEM Versuch,
    jetzt wachsend (60s, 120s, ...), damit spätere Versuche dem Server mehr Zeit
    zur Erholung geben. Ist im Fehler ein Wert angegeben, ist DIESER weiterhin
    maßgeblich (Google kennt seine eigene Rate-Limit-Situation am besten) —
    dieser Fallback greift nur, wenn kein Wert vorhanden ist."""
    return DEFAULT_RETRY_S_429 * (2 ** versuch)


T = TypeVar("T")


# ------------------------------------------------------------------ #
#  Sync (für generate_content und generate_content_stream)           #
# ------------------------------------------------------------------ #

def with_retry_sync(fn: Callable[[], T]) -> T:
    """
    Synchroner Retry für 429 (Rate-Limit), 503 (Transient Overload) und
    Netzwerkfehler (Timeout/Verbindungsabbruch). Andere Fehler werden sofort
    weitergegeben.
    """
    attempts_429 = 0
    attempts_503 = 0
    attempts_network = 0

    while True:
        try:
            return fn()

        except ClientError as exc:
            if not _is_429(exc):
                raise  # 401, 400, 403 … sofort weiterwerfen

            attempts_429 += 1
            delay = _extract_retry_delay(exc)

            if delay is not None and delay > DAILY_LIMIT_THRESHOLD_S:
                raise RateLimitExhausted("Tageslimit erreicht, morgen weiter.") from exc

            if attempts_429 >= MAX_RETRIES_429:
                raise RateLimitExhausted(
                    f"Tageslimit erreicht, morgen weiter. "
                    f"(429 nach {MAX_RETRIES_429} Versuchen)"
                ) from exc

            if delay is None:
                delay = _fallback_429_delay(attempts_429 - 1)
            log.warning("Gemini 429 (Versuch %d/%d). Warte %.0f s …",
                        attempts_429, MAX_RETRIES_429, delay)
            time.sleep(delay)

        except ServerError as exc:
            if not _ist_transienter_serverfehler(exc):
                raise  # andere 5xx (500, 502, …) sofort

            attempts_503 += 1
            if attempts_503 >= MAX_RETRIES_503:
                raise GeminiVoruebergehendNichtErreichbar(
                    f"Gemini {exc.code} nach {MAX_RETRIES_503} Versuchen weiterhin nicht lieferbar."
                ) from exc

            delay = _exponential_delay(RETRY_DELAY_503_S, attempts_503 - 1, RETRY_DELAY_503_CAP_S)
            log.warning("Gemini %s transient (Versuch %d/%d). Warte %.0f s …",
                        exc.code, attempts_503, MAX_RETRIES_503, delay)
            time.sleep(delay)

        except httpx.TransportError as exc:
            # Verbindungsabbruch, Timeout, DNS-Fehler o.ä. — bisher komplett
            # ungefangen und sofort nach oben durchgereicht. Meist sehr
            # kurzlebig, daher kurzer Retry mit wenigen Versuchen.
            attempts_network += 1
            if attempts_network >= MAX_RETRIES_NETWORK:
                raise GeminiVoruebergehendNichtErreichbar(
                    f"Netzwerkfehler nach {MAX_RETRIES_NETWORK} Versuchen: {exc}"
                ) from exc

            delay = _exponential_delay(RETRY_DELAY_NETWORK_S, attempts_network - 1, RETRY_DELAY_NETWORK_CAP_S)
            log.warning("Gemini Netzwerkfehler (Versuch %d/%d): %s. Warte %.0f s …",
                        attempts_network, MAX_RETRIES_NETWORK, exc, delay)
            time.sleep(delay)


# ------------------------------------------------------------------ #
#  Async (für async generate_content)                                #
# ------------------------------------------------------------------ #

async def with_retry(fn: Callable[[], Awaitable[T]]) -> T:
    """Asynchroner Retry für 429, 503 und Netzwerkfehler — siehe with_retry_sync."""
    attempts_429 = 0
    attempts_503 = 0
    attempts_network = 0

    while True:
        try:
            return await fn()

        except ClientError as exc:
            if not _is_429(exc):
                raise

            attempts_429 += 1
            delay = _extract_retry_delay(exc)

            if delay is not None and delay > DAILY_LIMIT_THRESHOLD_S:
                raise RateLimitExhausted("Tageslimit erreicht, morgen weiter.") from exc

            if attempts_429 >= MAX_RETRIES_429:
                raise RateLimitExhausted(
                    f"Tageslimit erreicht, morgen weiter. "
                    f"(429 nach {MAX_RETRIES_429} Versuchen)"
                ) from exc

            if delay is None:
                delay = _fallback_429_delay(attempts_429 - 1)
            log.warning("Gemini 429 async (Versuch %d/%d). Warte %.0f s …",
                        attempts_429, MAX_RETRIES_429, delay)
            await asyncio.sleep(delay)

        except ServerError as exc:
            if not _ist_transienter_serverfehler(exc):
                raise  # andere 5xx (500, 502, …) sofort

            attempts_503 += 1
            if attempts_503 >= MAX_RETRIES_503:
                raise GeminiVoruebergehendNichtErreichbar(
                    f"Gemini {exc.code} nach {MAX_RETRIES_503} Versuchen weiterhin nicht lieferbar."
                ) from exc

            delay = _exponential_delay(RETRY_DELAY_503_S, attempts_503 - 1, RETRY_DELAY_503_CAP_S)
            log.warning("Gemini %s async transient (Versuch %d/%d). Warte %.0f s …",
                        exc.code, attempts_503, MAX_RETRIES_503, delay)
            await asyncio.sleep(delay)

        except httpx.TransportError as exc:
            attempts_network += 1
            if attempts_network >= MAX_RETRIES_NETWORK:
                raise GeminiVoruebergehendNichtErreichbar(
                    f"Netzwerkfehler nach {MAX_RETRIES_NETWORK} Versuchen: {exc}"
                ) from exc

            delay = _exponential_delay(RETRY_DELAY_NETWORK_S, attempts_network - 1, RETRY_DELAY_NETWORK_CAP_S)
            log.warning("Gemini Netzwerkfehler async (Versuch %d/%d): %s. Warte %.0f s …",
                        attempts_network, MAX_RETRIES_NETWORK, exc, delay)
            await asyncio.sleep(delay)
