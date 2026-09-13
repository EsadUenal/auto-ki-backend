"""Zentrale harte Budgets und Concurrency-Gates für externe Provider.

Ein Scope entspricht genau einer Nutzer- oder Admin-Aktion. Jeder reale
HTTP-Versuch (auch ein Retry und Tavily Extract) muss über ``claim_call``
gezählt werden. Ohne Scope (Skripte/Diagnose) begrenzen nur die Retry-Maxima
je Einzeloperation; alle Consumer- und Admin-Routen laufen in einem Scope.
"""
from __future__ import annotations

import contextvars
import functools
import hashlib
import logging
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Callable

from app.config import (
    PROVIDER_FEATURE_LIMITS,
    PROVIDER_GLOBAL_MAX_CONCURRENT,
    PROVIDER_USER_MAX_CONCURRENT,
)

log = logging.getLogger(__name__)


class ProviderCallLimitExceeded(RuntimeError):
    """Das harte Aufrufbudget der laufenden Aktion ist verbraucht."""


class ProviderCapacityExceeded(RuntimeError):
    """Globale oder nutzerbezogene Parallelitätsgrenze ist erreicht."""


@dataclass
class ProviderScope:
    feature: str
    limits: dict[str, int]
    key: str
    started: float = field(default_factory=time.monotonic)
    calls: dict[str, int] = field(default_factory=lambda: {"gemini": 0, "tavily": 0})
    retries: dict[str, int] = field(default_factory=lambda: {"gemini": 0, "tavily": 0})


_scope_var: contextvars.ContextVar[ProviderScope | None] = contextvars.ContextVar(
    "provider_scope", default=None
)
_counter_lock = threading.Lock()
_global_active = 0
_active_by_key: dict[str, int] = {}


def request_cost_key(request: Any | None, user_id: int | None = None) -> str:
    """Stabiler, nicht rückrechenbarer Kosten-Key; loggt nie Cookie oder Token.

    Dieselbe Identität wie die Kontingente (app/usage_limit.py): geprüftes Konto
    aus dem Cookie, sonst die Client-IP aus app/client_ip.py. Ein Key je Token
    liesse sich durch mehrere Logins vervielfachen; die rohe Peer-Adresse wäre
    hinter einem Proxy für alle anonymen Nutzer dieselbe.
    """
    from app.usage_limit import _schluessel, _user_id_aus_cookie

    if user_id is None and request is not None:
        try:
            user_id = _user_id_aus_cookie(request)
        except Exception:
            user_id = None
    if user_id is not None:
        raw = f"user:{user_id}"
    else:
        try:
            raw = _schluessel(request, None)
        except Exception:
            raw = "ip:unknown"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def current_scope() -> ProviderScope | None:
    return _scope_var.get()


def claim_call(provider: str, *, retry: bool = False) -> tuple[str, int, int]:
    """Registriert einen realen Provider-Versuch oder bricht vor dem HTTP-Call ab."""
    scope = current_scope()
    if scope is None:
        # Bibliotheks-/Diagnoseaufrufe ausserhalb eines Request-Scopes werden
        # weiterhin durch die lokalen Retry-Maxima begrenzt. Ein dauerhaft im
        # ContextVar liegender Zaehler wuerde dagegen spaetere, unabhaengige
        # Aufrufe desselben Worker-Tasks faelschlich mitzaehlen.
        limit = max(0, int(PROVIDER_FEATURE_LIMITS["unscoped"].get(provider, 0)))
        return "unscoped", 1, limit
    limit = max(0, int(scope.limits.get(provider, 0)))
    used = scope.calls.get(provider, 0)
    if used >= limit:
        log.warning(
            "provider_event provider=%s feature=%s status=call_limit attempts=%d limit=%d",
            provider, scope.feature, used, limit,
        )
        raise ProviderCallLimitExceeded(
            f"Provider-Aufrufbudget für {scope.feature}/{provider} ausgeschöpft."
        )
    used += 1
    scope.calls[provider] = used
    if retry:
        scope.retries[provider] = scope.retries.get(provider, 0) + 1
    return scope.feature, used, limit


def log_provider_event(
    provider: str,
    *,
    status: str,
    attempt: int,
    started: float,
    error_class: str | None = None,
    model: str | None = None,
) -> None:
    """Strukturiertes, datensparsames Provider-Log ohne Prompt/Query/Inhalte."""
    scope = current_scope()
    feature = scope.feature if scope else "unscoped"
    log.info(
        "provider_event provider=%s feature=%s model=%s status=%s "
        "error_class=%s attempt=%d duration_ms=%d",
        provider, feature, model or "-", status, error_class or "-", attempt,
        round((time.monotonic() - started) * 1000),
    )


@asynccontextmanager
async def provider_scope(feature: str, *, key: str = "anonymous"):
    """Begrenzt Calls und parallele Aktionen; verschachtelte Scopes teilen Budget."""
    existing = current_scope()
    if existing is not None:
        yield existing
        return

    limits = dict(PROVIDER_FEATURE_LIMITS.get(feature, PROVIDER_FEATURE_LIMITS["unscoped"]))
    global _global_active
    with _counter_lock:
        key_active = _active_by_key.get(key, 0)
        if _global_active >= PROVIDER_GLOBAL_MAX_CONCURRENT:
            raise ProviderCapacityExceeded("Der Dienst ist momentan ausgelastet.")
        if key_active >= PROVIDER_USER_MAX_CONCURRENT:
            raise ProviderCapacityExceeded("Für dieses Konto laufen bereits Analysen.")
        _global_active += 1
        _active_by_key[key] = key_active + 1

    scope = ProviderScope(feature, limits, key)
    token = _scope_var.set(scope)
    try:
        yield scope
    finally:
        duration_ms = round((time.monotonic() - scope.started) * 1000)
        log.info(
            "provider_action feature=%s gemini_calls=%d tavily_calls=%d "
            "gemini_retries=%d tavily_retries=%d duration_ms=%d",
            scope.feature, scope.calls["gemini"], scope.calls["tavily"],
            scope.retries["gemini"], scope.retries["tavily"], duration_ms,
        )
        # Slots zuerst freigeben: ein abgebrochener SSE-Generator kann in einem
        # fremden Kontext finalisiert werden, dann wirft reset() ValueError —
        # der Slot darf dabei nicht dauerhaft belegt bleiben.
        with _counter_lock:
            _global_active = max(0, _global_active - 1)
            left = _active_by_key.get(key, 1) - 1
            if left <= 0:
                _active_by_key.pop(key, None)
            else:
                _active_by_key[key] = left
        try:
            _scope_var.reset(token)
        except ValueError:
            pass


def provider_action(feature: str) -> Callable:
    """Decorator für normale async Endpunkte/Funktionen (keine Async-Generatoren)."""
    def decorate(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapped(*args, **kwargs):
            request = kwargs.get("request")
            if request is None:
                request = next((a for a in args if hasattr(a, "cookies") and hasattr(a, "client")), None)
            key = request_cost_key(request, kwargs.get("user_id"))
            async with provider_scope(feature, key=key):
                return await fn(*args, **kwargs)
        return wrapped
    return decorate
