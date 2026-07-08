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
from datetime import datetime, timezone

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

_ABO_ERSATZTEIL_SUCHEN = {
    "light": 5,
    "pro":   20,
    "max":   0,   # unlimited — ersatzteil_suchen_verbleibend spielt keine Rolle
}


# ── Schemas ────────────────────────────────────────────────────────────────────

class CheckoutBody(BaseModel):
    typ: str                    # "abo" | "einzelkauf"
    abo_typ: str | None = None  # "light" | "pro" | "max" (nur bei typ=="abo")


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

import logging
log = logging.getLogger(__name__)


def _period_end_ts(sub) -> int | None:
    """
    Liest den Periodenend-Timestamp robust aus einem Stripe-Subscription-Objekt.

    Reihenfolge der Fallbacks:
      1. sub.cancel_at          — explizit gesetzt wenn cancel_at_period_end=True (zuverlässigst)
      2. sub.current_period_end — Top-Level-Feld (API < 2024-09-30 / SDK <= 14.x)
      3. sub.items.data[0].current_period_end — neues API-Format (2024-09-30+)
    """
    # 1. cancel_at (gesetzt bei cancel_at_period_end=True)
    ts = getattr(sub, "cancel_at", None)
    if ts:
        return int(ts)

    # 2. Top-Level current_period_end (ältere API-Versionen)
    ts = getattr(sub, "current_period_end", None)
    if ts:
        return int(ts)

    # 3. Items-Ebene (Stripe API 2024-09-30+)
    try:
        items = getattr(sub, "items", None)
        data = getattr(items, "data", None) if items else None
        if not data and isinstance(items, list):
            data = items
        if data:
            ts = getattr(data[0], "current_period_end", None)
            if ts:
                return int(ts)
    except Exception:
        pass

    log.warning("Stripe: current_period_end nicht gefunden auf sub %s", getattr(sub, "id", "?"))
    return None


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


_LAUFENDE_STATUS = ("active", "trialing", "past_due")


def _hat_laufendes_abo(customer_id: str) -> bool:
    """True, wenn der Kunde bereits ein laufendes Abo besitzt.

    Laufend = Stripe-Status ``active``, ``trialing`` oder ``past_due``:
      - ``active``    — normal laufendes Abo (inkl. ``cancel_at_period_end`` bis
                        zum Periodenende: Stripe behält hier status ``active``).
      - ``trialing``  — Testphase, Abo besteht.
      - ``past_due``  — Verlängerung fehlgeschlagen, aber Abo besteht weiter und
                        Stripe versucht die Zahlung erneut (Dunning). Erholt sich
                        der Retry, wird das Abo wieder ``active`` → ein hier neu
                        abgeschlossenes zweites Abo führte zu zwei abbuchenden
                        Abos. Deshalb ebenfalls blockieren.

    NICHT laufend (neues Abo erlaubt): ``canceled`` (beendet), ``unpaid``,
    ``incomplete``, ``incomplete_expired`` — dort findet keine automatische
    Abbuchung mehr statt.

    Fragt Stripe direkt (nicht die lokale DB) ab, damit die Sperre unabhängig von
    einem evtl. noch nicht eingetroffenen Webhook zuverlässig greift.
    """
    subs = stripe.Subscription.list(customer=customer_id, status="all", limit=100)
    return any(getattr(s, "status", None) in _LAUFENDE_STATUS for s in subs.data)


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

        # ── Launch-Sicherung gegen parallele Abos ───────────────────────────────
        # Besitzt der Kunde bereits ein laufendes Abo (active/trialing, inkl. per
        # cancel_at_period_end gekündigt aber noch aktiv), wird KEINE weitere
        # Checkout-Session erstellt und KEIN zweites Stripe-Abo erzeugt.
        # Bewusst KEINE Upgrade-/Downgrade-/Proration-Logik — nur die sichere
        # Verhinderung mehrerer gleichzeitiger Abonnements.
        if _hat_laufendes_abo(customer_id):
            raise HTTPException(
                status_code=409,
                detail={"fehler": {"code": "abo_bereits_aktiv",
                                   "nachricht": "Du besitzt bereits ein aktives Abonnement. "
                                                "Bitte verwalte oder kündige dieses zuerst."}},
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

    # Idempotenz, race-sicher UND fehlertolerant:
    #  1. Event atomar "beanspruchen" (INSERT — event_id ist PRIMARY KEY). Schlägt
    #     das fehl (Zeile existiert schon), wurde das Event bereits verarbeitet
    #     ODER wird gerade parallel verarbeitet → überspringen.
    #  2. Erst NACH erfolgreicher Verarbeitung bleibt der Claim bestehen.
    #  3. Schlägt die Verarbeitung fehl, wird der Claim wieder entfernt, damit
    #     Stripes automatischer Retry das Event TATSÄCHLICH erneut verarbeitet.
    #
    # VORHER wurde der event_id-Eintrag committet, BEVOR irgendetwas verarbeitet
    # wurde. Trat danach ein Fehler auf (z.B. unerwartetes Metadata-Format,
    # DB-Fehler), gab die Funktion 500 zurück, Stripe retryte automatisch — der
    # Retry traf aber sofort auf "schon erledigt" und wurde stillschweigend
    # übersprungen, OHNE dass die Freischaltung je stattfand. Der Nutzer hätte
    # bezahlt, aber nie sein Guthaben bekommen — und kein Retry hätte das je
    # repariert.
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO stripe_events (event_id) VALUES (?)", (event_id,)
        )
        conn.commit()
        if cur.rowcount == 0:
            return {"ok": True, "skipped": True}

    try:
        _verarbeite_event(event_type, obj)
    except Exception:
        with get_conn() as conn:
            conn.execute("DELETE FROM stripe_events WHERE event_id=?", (event_id,))
            conn.commit()
        raise

    return {"ok": True}


def _verarbeite_event(event_type: str, obj) -> None:
    """Wendet ein einzelnes, bereits signatur-geprüftes Stripe-Event an.
    Wirft bei Fehlern normal weiter — der Aufrufer (stripe_webhook) entscheidet
    anhand dessen, ob der Idempotenz-Claim bestehen bleibt oder zurückgerollt wird."""
    # ── checkout.session.completed ─────────────────────────────────────────────
    if event_type == "checkout.session.completed":
        # obj.metadata ist StripeObject (Stripe 15.x) — kein .get(), kein dict().
        # Sicher über getattr() zugreifen; _data-Dict als Fallback.
        meta    = obj.metadata or {}
        _m      = meta._data if hasattr(meta, "_data") else (meta if isinstance(meta, dict) else {})
        user_id = int(_m.get("user_id", 0) or 0)
        typ     = _m.get("typ", "")

        if typ == "abo":
            abo_typ   = _m.get("abo_typ", "")
            sub_id    = getattr(obj, "subscription", None)
            checks    = _ABO_CHECKS.get(abo_typ, 0)
            ersatzteil_suchen = _ABO_ERSATZTEIL_SUCHEN.get(abo_typ, 0)
            with get_conn() as conn:
                # abo_typ + checks + ersatzteilsuchen immer schreiben (Kern-Freischaltung)
                conn.execute(
                    "UPDATE users SET abo_typ=?, checks_verbleibend=?, ersatzteil_suchen_verbleibend=? WHERE id=?",
                    (abo_typ, checks, ersatzteil_suchen, user_id),
                )
                # stripe_subscription_id separat — Spalte könnte bei alten DBs fehlen
                try:
                    conn.execute(
                        "UPDATE users SET stripe_subscription_id=? WHERE id=?",
                        (sub_id, user_id),
                    )
                except Exception:
                    pass
                conn.commit()

        elif typ == "einzelkauf":
            with get_conn() as conn:
                conn.execute(
                    "UPDATE users SET checks_verbleibend = checks_verbleibend + 1 WHERE id=?",
                    (user_id,),
                )
                conn.commit()

        elif typ == "ebook":
            ebook_id       = _m.get("ebook_id", "")
            preis_bezahlt  = float(_m.get("preis_bezahlt", "0") or "0")
            session_id     = getattr(obj, "id", "")
            payment_intent = getattr(obj, "payment_intent", None)
            paid_at        = None
            created_ts     = getattr(obj, "created", None)
            if created_ts:
                paid_at = datetime.fromtimestamp(created_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            if user_id and ebook_id and session_id:
                with get_conn() as conn:
                    conn.execute(
                        """INSERT OR IGNORE INTO ebook_bestellung
                           (user_id, ebook_id, preis_bezahlt, stripe_session_id,
                            stripe_payment_intent_id, status, paid_at)
                           VALUES (?,?,?,?,?,'bezahlt',?)""",
                        (user_id, ebook_id, preis_bezahlt, session_id, payment_intent, paid_at),
                    )
                    conn.commit()

        elif typ == "poster":
            poster_id        = _m.get("poster_id", "")
            preis_bezahlt    = float(_m.get("preis_bezahlt", "0") or "0")
            adresse_name     = _m.get("adresse_name", "")
            adresse_strasse  = _m.get("adresse_strasse", "")
            adresse_plz      = _m.get("adresse_plz", "")
            adresse_ort      = _m.get("adresse_ort", "")
            adresse_land     = _m.get("adresse_land", "DE")
            session_id       = getattr(obj, "id", "")
            payment_intent   = getattr(obj, "payment_intent", None)
            paid_at          = None
            created_ts       = getattr(obj, "created", None)
            if created_ts:
                from datetime import datetime, timezone
                paid_at = datetime.fromtimestamp(created_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            if user_id and poster_id and session_id:
                with get_conn() as conn:
                    conn.execute(
                        """INSERT OR IGNORE INTO poster_bestellung
                           (user_id, poster_id, preis_bezahlt, stripe_session_id,
                            stripe_payment_intent_id, status, paid_at,
                            adresse_name, adresse_strasse, adresse_plz, adresse_ort, adresse_land)
                           VALUES (?,?,?,?,?,'bezahlt',?,?,?,?,?,?)""",
                        (user_id, poster_id, preis_bezahlt, session_id, payment_intent,
                         paid_at, adresse_name, adresse_strasse, adresse_plz, adresse_ort, adresse_land),
                    )
                    conn.commit()

    # ── invoice.paid — monatlicher Reset ──────────────────────────────────────
    elif event_type == "invoice.paid":
        billing_reason = getattr(obj, "billing_reason", "")
        if billing_reason != "subscription_cycle":
            return   # Erstrechnung wird über checkout.session.completed verarbeitet

        sub_id = getattr(obj, "subscription", None)
        if not sub_id:
            return

        with get_conn() as conn:
            user = conn.execute(
                "SELECT id, abo_typ FROM users WHERE stripe_subscription_id=?", (sub_id,)
            ).fetchone()
            if user and user["abo_typ"] != "max":
                checks = _ABO_CHECKS.get(user["abo_typ"], 0)
                ersatzteil_suchen = _ABO_ERSATZTEIL_SUCHEN.get(user["abo_typ"], 0)
                conn.execute(
                    "UPDATE users SET checks_verbleibend=?, ersatzteil_suchen_verbleibend=? WHERE id=?",
                    (checks, ersatzteil_suchen, user["id"]),
                )
                conn.commit()

    # ── customer.subscription.deleted — Abo gekündigt ─────────────────────────
    elif event_type == "customer.subscription.deleted":
        sub_id = getattr(obj, "id", None)
        if sub_id:
            with get_conn() as conn:
                conn.execute(
                    "UPDATE users SET abo_typ='none', checks_verbleibend=0, ersatzteil_suchen_verbleibend=0, "
                    "stripe_subscription_id=NULL, abo_kuendigt_zum=NULL "
                    "WHERE stripe_subscription_id=?",
                    (sub_id,),
                )
                conn.commit()


@router.post("/cancel-subscription")
def cancel_subscription(user_id: int = Depends(get_current_user_id)):
    """
    Abo kündigen — cancel_at_period_end=True bei Stripe.
    Das Abo läuft bis Periodenende, danach setzt der webhook abo_typ='none'.
    Speichert abo_kuendigt_zum für die UI-Anzeige.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT stripe_subscription_id, abo_typ FROM users WHERE id=? AND deleted_at IS NULL",
            (user_id,),
        ).fetchone()

    if not row or row["abo_typ"] == "none" or not row["stripe_subscription_id"]:
        raise HTTPException(
            status_code=400,
            detail={"fehler": {"code": "kein_abo", "nachricht": "Kein aktives Abo gefunden."}},
        )

    sub = stripe.Subscription.modify(
        row["stripe_subscription_id"],
        cancel_at_period_end=True,
    )
    ts = _period_end_ts(sub)
    kuendigt_zum = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts else None

    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET abo_kuendigt_zum=? WHERE id=?",
            (kuendigt_zum, user_id),
        )
        conn.commit()

    return {"ok": True, "abo_kuendigt_zum": kuendigt_zum}


@router.get("/status")
def payment_status(user_id: int = Depends(get_current_user_id)):
    """Gibt aktuellen Abo-Status des eingeloggten Nutzers zurück."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT abo_typ, checks_verbleibend, abo_kuendigt_zum FROM users WHERE id=?",
            (user_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=401)
    return {
        "abo_typ": row["abo_typ"],
        "checks_verbleibend": row["checks_verbleibend"],
        "hat_abo": row["abo_typ"] != "none",
        "abo_kuendigt_zum": row["abo_kuendigt_zum"],
    }
