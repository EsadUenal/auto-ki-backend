"""
Payments-Router — Phase 2d (Stripe Testmodus)

Endpoints:
  POST /payments/checkout-session  → erzeugt Stripe-Checkout-URL
  POST /payments/webhook           → verarbeitet Stripe-Webhooks (signaturverified)
  GET  /payments/status            → gibt aktuellen Abo-Status zurück

Sicherheit:
  - Freischaltung NUR über verifizierten Webhook, nie über redirect-URL
  - Webhook-Signatur wird mit stripe.Webhook.construct_event geprüft
  - Idempotenz via stripe_events-Tabelle
"""
from __future__ import annotations

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.config import (
    FRONTEND_URL,
    STRIPE_PRICE_EINZELKAUF,
    STRIPE_PRICE_LIGHT,
    STRIPE_PRICE_MAX,
    STRIPE_PRICE_PRO,
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
)
from app.database import get_conn
from app.routers.user_auth import get_current_user_id
from app.utf8 import UTF8JSONResponse

stripe.api_key = STRIPE_SECRET_KEY

router = APIRouter(
    prefix="/payments",
    tags=["payments"],
    default_response_class=UTF8JSONResponse,
)

_ABO_PRICE = {
    "light": lambda: STRIPE_PRICE_LIGHT,
    "pro":   lambda: STRIPE_PRICE_PRO,
    "max":   lambda: STRIPE_PRICE_MAX,
}

_ABO_CHECKS = {
    "light": 3,
    "pro":   10,
    "max":   0,   # unlimited — checks_verbleibend spielt keine Rolle
}


# ── Schemas ────────────────────────────────────────────────────────────────────

class CheckoutBody(BaseModel):
    typ: str                    # "abo" | "einzelkauf"
    abo_typ: str | None = None  # "light" | "pro" | "max" (nur bei typ=="abo")


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _get_or_create_customer(user_id: int) -> str:
    """Liest vorhandene stripe_customer_id oder erstellt neuen Stripe-Kunden."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT email, stripe_customer_id FROM users WHERE id=?", (user_id,)
        ).fetchone()

    if not row:
        raise HTTPException(
            status_code=401,
            detail={"fehler": {"code": "unauthorized", "nachricht": "Nutzer nicht gefunden."}},
        )

    if row["stripe_customer_id"]:
        return row["stripe_customer_id"]

    customer = stripe.Customer.create(
        email=row["email"],
        metadata={"user_id": str(user_id)},
    )
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET stripe_customer_id=? WHERE id=?",
            (customer.id, user_id),
        )
        conn.commit()
    return customer.id


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/checkout-session")
def create_checkout_session(
    body: CheckoutBody,
    user_id: int = Depends(get_current_user_id),
):
    """Erzeugt eine Stripe-Checkout-Session und gibt die URL zurück."""
    customer_id = _get_or_create_customer(user_id)
    success_url = f"{FRONTEND_URL}/pricing?payment=success"
    cancel_url  = f"{FRONTEND_URL}/pricing?payment=cancelled"

    if body.typ == "abo":
        if not body.abo_typ or body.abo_typ not in _ABO_PRICE:
            raise HTTPException(
                status_code=400,
                detail={"fehler": {"code": "bad_request", "nachricht": "Unbekanntes Abo-Typ."}},
            )
        price_id = _ABO_PRICE[body.abo_typ]()
        if not price_id:
            raise HTTPException(
                status_code=500,
                detail={"fehler": {"code": "konfiguration", "nachricht": "Stripe-Preis nicht konfiguriert."}},
            )

        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"user_id": str(user_id), "typ": "abo", "abo_typ": body.abo_typ},
        )

    elif body.typ == "einzelkauf":
        if not STRIPE_PRICE_EINZELKAUF:
            raise HTTPException(
                status_code=500,
                detail={"fehler": {"code": "konfiguration", "nachricht": "Stripe-Preis nicht konfiguriert."}},
            )

        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            mode="payment",
            line_items=[{"price": STRIPE_PRICE_EINZELKAUF, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"user_id": str(user_id), "typ": "einzelkauf"},
        )

    else:
        raise HTTPException(
            status_code=400,
            detail={"fehler": {"code": "bad_request", "nachricht": "Unbekannter Checkout-Typ."}},
        )

    return {"url": session.url}


@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(request: Request):
    """
    Stripe-Webhook — einziger Weg zur Freischaltung.
    Verarbeitet: checkout.session.completed, invoice.paid, customer.subscription.deleted
    """
    payload    = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception:
        raise HTTPException(status_code=400, detail="Ungültige Webhook-Signatur")

    event_id   = event.id
    event_type = event.type
    obj        = event.data.object

    # Idempotenz: jedes Event nur einmal verarbeiten
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM stripe_events WHERE event_id=?", (event_id,)).fetchone():
            return {"ok": True, "skipped": True}
        conn.execute("INSERT INTO stripe_events (event_id) VALUES (?)", (event_id,))
        conn.commit()

    # ── checkout.session.completed ─────────────────────────────────────────────
    if event_type == "checkout.session.completed":
        meta    = obj.metadata or {}
        user_id = int(meta.get("user_id", 0))
        typ     = meta.get("typ", "")

        if typ == "abo":
            abo_typ = meta.get("abo_typ", "")
            sub_id  = getattr(obj, "subscription", None)
            checks  = _ABO_CHECKS.get(abo_typ, 0)
            with get_conn() as conn:
                conn.execute(
                    "UPDATE users SET abo_typ=?, checks_verbleibend=?, stripe_subscription_id=? WHERE id=?",
                    (abo_typ, checks, sub_id, user_id),
                )
                conn.commit()

        elif typ == "einzelkauf":
            with get_conn() as conn:
                conn.execute(
                    "UPDATE users SET checks_verbleibend = checks_verbleibend + 1 WHERE id=?",
                    (user_id,),
                )
                conn.commit()

    # ── invoice.paid — monatlicher Reset ──────────────────────────────────────
    elif event_type == "invoice.paid":
        billing_reason = getattr(obj, "billing_reason", "")
        if billing_reason != "subscription_cycle":
            return {"ok": True}   # Erstrechnung wird über checkout.session.completed verarbeitet

        sub_id = getattr(obj, "subscription", None)
        if not sub_id:
            return {"ok": True}

        with get_conn() as conn:
            user = conn.execute(
                "SELECT id, abo_typ FROM users WHERE stripe_subscription_id=?", (sub_id,)
            ).fetchone()
            if user and user["abo_typ"] != "max":
                checks = _ABO_CHECKS.get(user["abo_typ"], 0)
                conn.execute(
                    "UPDATE users SET checks_verbleibend=? WHERE id=?",
                    (checks, user["id"]),
                )
                conn.commit()

    # ── customer.subscription.deleted — Abo gekündigt ─────────────────────────
    elif event_type == "customer.subscription.deleted":
        sub_id = getattr(obj, "id", None)
        if sub_id:
            with get_conn() as conn:
                conn.execute(
                    "UPDATE users SET abo_typ='none', checks_verbleibend=0, stripe_subscription_id=NULL "
                    "WHERE stripe_subscription_id=?",
                    (sub_id,),
                )
                conn.commit()

    return {"ok": True}


@router.get("/status")
def payment_status(user_id: int = Depends(get_current_user_id)):
    """Gibt aktuellen Abo-Status des eingeloggten Nutzers zurück."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT abo_typ, checks_verbleibend FROM users WHERE id=?", (user_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=401)
    return {
        "abo_typ": row["abo_typ"],
        "checks_verbleibend": row["checks_verbleibend"],
        "hat_abo": row["abo_typ"] != "none",
    }
