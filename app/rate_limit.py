"""Zentrale Rate-Limiter-Instanz.

Liegt bewusst in einem eigenen Modul (nicht in main.py), damit sowohl main.py
(SlowAPIMiddleware) als auch einzelne Router die *dieselbe* Limiter-Instanz
importieren koennen — z.B. um den Stripe-Webhook per ``@limiter.exempt`` vom
globalen Limit auszunehmen — ohne Zirkelimport ueber main.py.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import RATE_LIMIT

limiter = Limiter(key_func=get_remote_address, default_limits=[RATE_LIMIT])
