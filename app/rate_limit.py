"""Zentrale Rate-Limiter-Instanz.

Liegt bewusst in einem eigenen Modul (nicht in main.py), damit sowohl main.py
(SlowAPIMiddleware) als auch einzelne Router die *dieselbe* Limiter-Instanz
importieren koennen — z.B. um den Stripe-Webhook per ``@limiter.exempt`` vom
globalen Limit auszunehmen — ohne Zirkelimport ueber main.py.
"""
from slowapi import Limiter
from app.client_ip import limit_schluessel

from app.config import RATE_LIMIT

limiter = Limiter(key_func=limit_schluessel, default_limits=[RATE_LIMIT])
