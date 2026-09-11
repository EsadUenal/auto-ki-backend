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

from app import plus as plus_modul
from app.check_gate import gutschrift, kontingente
from app.config import (
    FRONTEND_URL,
    STRIPE_PRICE_KAUFCHECK,
    STRIPE_PRICE_PLUS,
    STRIPE_PRICE_VERKAUFSCHECK,
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
)
from app.database import get_conn
from app.einwilligung import (
    require_agb, require_widerruf_verzicht,
    record as record_einwilligung, ART_AGB, ART_WIDERRUF,
)
from app.rate_limit import limiter
from app.usage_limit import nutzung
from app.routers.user_auth import get_current_user_id
from app.utf8 import UTF8JSONResponse

stripe.api_key = STRIPE_SECRET_KEY

router = APIRouter(
    prefix="/payments",
    tags=["payments"],
    default_response_class=UTF8JSONResponse,
)

# Consumer Pricing V1 — kaufbare Einmalprodukte.
# Der Client sendet AUSSCHLIESSLICH diesen Schluessel; Betrag und Price-ID
# stammen immer aus der Server-Konfiguration. Ein Client kann damit weder einen
# Preis noch ein fremdes Produkt bestimmen.
_CHECK_PRICE = {
    "kaufcheck":     lambda: STRIPE_PRICE_KAUFCHECK,
    "verkaufscheck": lambda: STRIPE_PRICE_VERKAUFSCHECK,
}

# Einzige Checkout-Typen, fuer die NEUE Sessions angelegt werden: das aktuelle
# Consumer-Angebot (KaufCheck, VerkaufsCheck, Plus). Die Legacy-Produkte
# light/pro/max ("abo") und der generische "einzelkauf" werden nicht mehr
# verkauft — auch nicht per direktem API-Aufruf. Bestehende Legacy-Kunden
# behalten ihre Rechte: Renewal, Kuendigung und Webhook-Verarbeitung ihrer
# laufenden Abos bleiben unveraendert (siehe _verarbeite_event).
_NEU_KAUFBAR = ("check", "plus")

# Legacy light/pro: monatliche Kontingente laufender Bestandsabos (invoice.paid).
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
    typ: str                        # "check" | "plus" (siehe _NEU_KAUFBAR)
    produkt: str | None = None      # "kaufcheck" | "verkaufscheck" (nur bei typ=="check")
    abo_typ: str | None = None      # Legacy-Feld, wird ignoriert (Abos light/pro/max nicht mehr kaufbar)
    agb_akzeptiert: bool = False    # AGB + Datenschutz — Pflicht bei jedem Kauf
    widerruf_verzicht: bool = False # Zustimmung zur sofortigen Ausführung (digitaler Kauf)


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


def _period_start_ts(sub) -> int | None:
    """Periodenbeginn robust aus dem Subscription-Objekt lesen (wie _period_end_ts)."""
    ts = getattr(sub, "current_period_start", None)
    if ts:
        return int(ts)
    try:
        items = getattr(sub, "items", None)
        data = getattr(items, "data", None) if items else None
        if not data and isinstance(items, list):
            data = items
        if data:
            ts = getattr(data[0], "current_period_start", None)
            if ts:
                return int(ts)
    except Exception:
        pass
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
    # Pflicht-Zustimmungen serverseitig erzwingen (unabhängig vom Frontend):
    # AGB/Datenschutz bei jedem Kauf, Widerrufs-Verzicht bei jedem digitalen Kauf.
    require_agb(body.agb_akzeptiert)
    require_widerruf_verzicht(body.widerruf_verzicht)
    # Nur das aktuelle Angebot ist neu kaufbar. Die Pruefung steht VOR jedem
    # Stripe-Aufruf: fuer ein nicht verkauftes Produkt wird weder ein Kunde noch
    # eine Session angelegt noch eine Einwilligung protokolliert.
    if body.typ not in _NEU_KAUFBAR:
        raise HTTPException(
            status_code=400,
            detail={"fehler": {"code": "produkt_nicht_verfuegbar",
                               "nachricht": "Dieses Produkt ist nicht erhältlich."}},
        )
    customer_id = _get_or_create_customer(user_id)
    # Nach dem Kauf kehrt der Kunde dorthin zurueck, wo er den Kauf begonnen hat:
    # bei einem Check auf die jeweilige Check-Seite (der Kontext dort ist noch
    # vorhanden), sonst wie bisher auf die Preisseite.
    ziel = {
        "kaufcheck":     "/kaufcheck",
        "verkaufscheck": "/verkaufscheck",
    }.get(body.produkt or "", "/pricing") if body.typ == "check" else "/pricing"
    success_url = f"{FRONTEND_URL}{ziel}?payment=success&session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url  = f"{FRONTEND_URL}{ziel}?payment=cancelled"

    if body.typ == "check":
        # Consumer V1: getrennte Einmalprodukte. Nur bekannte Schluessel sind
        # kaufbar; alles andere wird abgelehnt, statt auf irgendein Produkt
        # zurueckzufallen.
        if body.produkt not in _CHECK_PRICE:
            raise HTTPException(
                status_code=400,
                detail={"fehler": {"code": "bad_request", "nachricht": "Unbekanntes Produkt."}},
            )
        price_id = _CHECK_PRICE[body.produkt]()
        if not price_id:
            raise HTTPException(
                status_code=500,
                detail={"fehler": {"code": "konfiguration", "nachricht": "Stripe-Preis nicht konfiguriert."}},
            )

        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            mode="payment",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"user_id": str(user_id), "typ": "check", "produkt": body.produkt},
        )

    elif body.typ == "plus":
        # VIRA Plus — das einzige neu angebotene Abo. mode="subscription",
        # damit Stripe die monatliche Verlaengerung und die Kuendigung fuehrt.
        if not STRIPE_PRICE_PLUS:
            raise HTTPException(
                status_code=500,
                detail={"fehler": {"code": "konfiguration", "nachricht": "Stripe-Preis nicht konfiguriert."}},
            )
        # Schutz vor zwei parallel abbuchenden Abos — gilt auch gegenueber
        # bestehenden Legacy-Abos (light/pro/max).
        if _hat_laufendes_abo(customer_id):
            raise HTTPException(
                status_code=409,
                detail={"fehler": {"code": "abo_bereits_aktiv",
                                   "nachricht": "Du besitzt bereits ein aktives Abonnement. "
                                                "Bitte verwalte oder kuendige dieses zuerst."}},
            )
        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": STRIPE_PRICE_PLUS, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"user_id": str(user_id), "typ": "plus"},
            # Auch auf der Subscription hinterlegen: invoice.paid der FOLGE-
            # monate traegt die Session-Metadata nicht mehr, wohl aber die der
            # Subscription. Ohne das waere der Nutzer beim Renewal nicht mehr
            # zuzuordnen.
            subscription_data={"metadata": {"user_id": str(user_id), "typ": "plus"}},
        )

    else:
        raise HTTPException(
            status_code=400,
            detail={"fehler": {"code": "bad_request", "nachricht": "Unbekannter Checkout-Typ."}},
        )

    # Nachweis der Zustimmungen für diesen Kauf festhalten.
    kontext = f"check:{body.produkt}" if body.typ == "check" else "plus"
    record_einwilligung(user_id, ART_AGB, kontext)
    record_einwilligung(user_id, ART_WIDERRUF, kontext)

    return {"url": session.url}


@router.post("/webhook", include_in_schema=False)
@limiter.exempt   # Stripe-Webhook nie drosseln: unter geteilter IP koennte das
                  # globale Rate-Limit sonst 429 liefern -> verzoegerte Freischaltung.
async def stripe_webhook(request: Request):
    """
    Stripe-Webhook — einziger Weg zur Freischaltung.
    Verarbeitet: checkout.session.completed, invoice.paid, customer.subscription.deleted
    """
    # Fail-closed: Stripe prueft die Signatur mit HMAC-SHA256 ueber das Secret.
    # Bei leerem Secret kann JEDER eine "gueltige" Signatur selbst berechnen und
    # damit Zahlungen vortaeuschen. Ohne Secret wird deshalb gar nichts
    # verarbeitet. 503 statt 400: Stripe stellt das Event spaeter erneut zu,
    # sobald das Secret konfiguriert ist — echte Zahlungen gehen nicht verloren.
    webhook_secret = (STRIPE_WEBHOOK_SECRET or "").strip()
    if not webhook_secret:
        log.error("Stripe-Webhook abgelehnt: STRIPE_WEBHOOK_SECRET ist nicht gesetzt.")
        raise HTTPException(
            status_code=503,
            detail={"fehler": {"code": "webhook_nicht_konfiguriert",
                               "nachricht": "Webhook nicht konfiguriert."}},
        )

    payload    = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
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
        # Checkout-Sessions laufen ueber einen zusaetzlichen session-scoped Claim,
        # damit Webhook und Verify-Fallback (/verify-session) dieselbe Session nie
        # doppelt freischalten. Andere Event-Typen wie gehabt.
        if event_type == "checkout.session.completed":
            _verarbeite_checkout_session(obj)
        else:
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

        elif typ == "plus":
            # Erste Periode. Zeitraum kommt aus der Subscription bei Stripe —
            # nicht aus der Redirect-URL und nicht aus Client-State.
            sub_id = getattr(obj, "subscription", None)
            if not user_id or not sub_id:
                raise ValueError("plus-Checkout ohne user_id oder subscription")
            sub = stripe.Subscription.retrieve(sub_id)
            plus_modul.grant_periode(user_id, sub_id,
                                     _period_start_ts(sub), _period_end_ts(sub))

        elif typ == "check":
            # Produkt kommt aus der Session-Metadata, die der Server beim
            # Anlegen gesetzt hat — nicht aus einer Client-Angabe. Ein
            # unbekannter Wert schaltet NICHTS frei (ValueError -> der Aufrufer
            # rollt den Idempotenz-Claim zurueck und Stripe retryt; ein falsches
            # Produkt still gutzuschreiben waere schlimmer als ein Retry).
            produkt = _m.get("produkt", "")
            if not user_id:
                raise ValueError("checkout.session.completed ohne user_id")
            gutschrift(user_id, produkt)

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

        # ── VIRA Plus: neuer bezahlter Abrechnungszeitraum ────────────────────
        # Ausloeser ist ausschliesslich eine von Stripe als BEZAHLT gemeldete
        # Rechnung. Eine fehlgeschlagene Verlaengerung feuert dieses Event nicht
        # und erzeugt damit auch kein neues Kontingent.
        with get_conn() as conn:
            plus_user = conn.execute(
                "SELECT id FROM users WHERE plus_subscription_id=?", (sub_id,)
            ).fetchone()
        if plus_user:
            sub = stripe.Subscription.retrieve(sub_id)
            # grant_periode SETZT auf 5/1 statt zu addieren -> kein Uebertrag
            # nicht verbrauchter Plus-Checks und zugleich idempotent gegen
            # doppelt zugestellte Events.
            plus_modul.grant_periode(plus_user["id"], sub_id,
                                     _period_start_ts(sub), _period_end_ts(sub))
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
            # Plus endet — GEKAUFTES Guthaben bleibt ausdruecklich erhalten.
            plus_modul.beende(sub_id)
            with get_conn() as conn:
                conn.execute(
                    "UPDATE users SET abo_typ='none', checks_verbleibend=0, ersatzteil_suchen_verbleibend=0, "
                    "stripe_subscription_id=NULL, abo_kuendigt_zum=NULL "
                    "WHERE stripe_subscription_id=?",
                    (sub_id,),
                )
                conn.commit()


def _verarbeite_checkout_session(obj) -> None:
    """Verarbeitet eine abgeschlossene Checkout-Session *idempotent*.

    Beansprucht einen session-scoped Eintrag in ``stripe_events`` (Key
    ``cs:<session_id>``), bevor die eigentliche Freischaltung laeuft. Dadurch
    koennen Webhook UND Verify-Fallback dieselbe Session gefahrlos verarbeiten,
    ohne doppelt freizuschalten (z.B. doppelte Check-Gutschrift beim Einzelkauf).
    Schlaegt die Freischaltung fehl, wird der Claim wieder entfernt, damit ein
    Retry (Stripe-Webhook) bzw. ein erneuter Verify-Aufruf sie wirklich nachholt.
    """
    session_id = getattr(obj, "id", "")
    if not session_id:
        return
    claim = f"cs:{session_id}"
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO stripe_events (event_id) VALUES (?)", (claim,)
        )
        conn.commit()
        if cur.rowcount == 0:
            return   # bereits (durch Webhook ODER Verify) verarbeitet
    try:
        _verarbeite_event("checkout.session.completed", obj)
    except Exception:
        with get_conn() as conn:
            conn.execute("DELETE FROM stripe_events WHERE event_id=?", (claim,))
            conn.commit()
        raise


class VerifySessionBody(BaseModel):
    session_id: str


@router.post("/verify-session")
def verify_session(body: VerifySessionBody, user_id: int = Depends(get_current_user_id)):
    """Fallback zum Webhook.

    Nach der Rueckkehr des Kunden von Stripe prueft das Frontend hierueber die
    Checkout-Session direkt bei Stripe. Ist sie bezahlt und gehoert sie dem
    eingeloggten Konto, wird *idempotent* freigeschaltet — falls der Webhook
    (noch) nicht angekommen ist. So bekommt ein zahlender Kunde selbst bei
    verzoegertem oder verpasstem Webhook sofort Zugang. Eine Doppel-Freischaltung
    mit dem Webhook ist durch den session-scoped Idempotenz-Claim ausgeschlossen.
    """
    if not body.session_id:
        raise HTTPException(
            status_code=400,
            detail={"fehler": {"code": "bad_request", "nachricht": "session_id fehlt."}},
        )
    try:
        session = stripe.checkout.Session.retrieve(body.session_id)
    except Exception:
        raise HTTPException(
            status_code=404,
            detail={"fehler": {"code": "nicht_gefunden", "nachricht": "Checkout-Session nicht gefunden."}},
        )

    # Nur tatsaechlich bezahlte/abgeschlossene Sessions freischalten.
    bezahlt = (
        getattr(session, "payment_status", None) == "paid"
        or getattr(session, "status", None) == "complete"
    )
    if not bezahlt:
        return {"ok": True, "freigeschaltet": False, "status": getattr(session, "status", None)}

    # Session muss dem anfragenden Konto gehoeren (kein Fremd-Claim ueber geratene IDs).
    meta = session.metadata or {}
    _m   = meta._data if hasattr(meta, "_data") else (meta if isinstance(meta, dict) else {})
    if int(_m.get("user_id", 0) or 0) != user_id:
        raise HTTPException(
            status_code=403,
            detail={"fehler": {"code": "forbidden", "nachricht": "Session gehoert nicht zu diesem Konto."}},
        )

    _verarbeite_checkout_session(session)
    return {"ok": True, "freigeschaltet": True}


@router.post("/cancel-subscription")
def cancel_subscription(user_id: int = Depends(get_current_user_id)):
    """
    Abo kündigen — cancel_at_period_end=True bei Stripe.
    Das Abo läuft bis Periodenende, danach setzt der webhook abo_typ='none'.
    Speichert abo_kuendigt_zum für die UI-Anzeige.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT stripe_subscription_id, abo_typ, plus_subscription_id, plus_period_end "
            "FROM users WHERE id=? AND deleted_at IS NULL",
            (user_id,),
        ).fetchone()

    # VIRA Plus zuerst: das ist das einzige Abo, das neue Nutzer abschliessen
    # koennen. Legacy light/pro/max bleibt fuer Bestandskunden unveraendert
    # kuendbar (Zweig darunter).
    ist_plus = bool(row) and plus_modul.ist_aktiv(row) and bool(row["plus_subscription_id"])
    sub_id = row["plus_subscription_id"] if ist_plus else (row["stripe_subscription_id"] if row else None)

    if not row or (not ist_plus and (row["abo_typ"] == "none" or not row["stripe_subscription_id"])):
        raise HTTPException(
            status_code=400,
            detail={"fehler": {"code": "kein_abo", "nachricht": "Kein aktives Abo gefunden."}},
        )

    # cancel_at_period_end: die bereits bezahlte Periode laeuft vollstaendig zu
    # Ende. Es wird bewusst NICHT sofort abgeschaltet — der Nutzer hat den Monat
    # bezahlt. Erst danach bleibt der naechste Grant aus.
    sub = stripe.Subscription.modify(sub_id, cancel_at_period_end=True)
    ts = _period_end_ts(sub)
    kuendigt_zum = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts else None

    if ist_plus:
        plus_modul.merke_kuendigung(user_id, kuendigt_zum)
    else:
        with get_conn() as conn:
            conn.execute("UPDATE users SET abo_kuendigt_zum=? WHERE id=?", (kuendigt_zum, user_id))
            conn.commit()

    return {"ok": True, "abo_kuendigt_zum": kuendigt_zum, "plus": ist_plus}


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
        **kontingente(user_id),
        **plus_modul.status(user_id),
        **nutzung(user_id),
    }
