"""
Test: Idempotente Freischaltung + Verify-Fallback (app/routers/payments.py)

Prueft OHNE Netzwerk, OHNE Login und OHNE echtes Stripe (Stripe wird gemockt)
gegen eine TEMPORAERE DB die neue Absicherung gegen "bezahlt, aber kein Zugang":

  1) Bezahlte E-Book-Checkout-Session wird genau EINMAL freigeschaltet; ein
     zweiter Aufruf (Weg Webhook <-> Verify) erzeugt KEINE Doppel-Bestellung.
  2) Einzelkauf: Checks werden nur +1 gutgeschrieben, auch bei zwei Aufrufen
     (kein Doppel-Credit trotz nicht-idempotentem UPDATE).
  3) verify_session: bezahlte, eigene Session -> freigeschaltet + idempotent.
  4) Unbezahlte Session -> KEINE Freischaltung.
  5) Session mit fremder user_id -> HTTP 403 (kein Fremd-Claim).

Ausfuehren:  python test_payments_verify.py
"""
import os
import tempfile
import time

# WICHTIG: temporaere DB VOR dem Import der app-Module setzen, damit config.DB_PATH
# sie uebernimmt und keine echte DB angefasst wird.
_TMP = tempfile.mkdtemp(prefix="vira_test_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "test.db")

import app.database as db          # noqa: E402
db.ensure_tables()                  # Schema + Ebook-Seed (inkl. 'dein-erstes-auto')

import app.routers.payments as pay  # noqa: E402
from fastapi import HTTPException   # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


# ── Helfer ──────────────────────────────────────────────────────────────────
def neuer_user(email: str) -> int:
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, abo_typ, checks_verbleibend) "
            "VALUES (?,?, 'none', 0)",
            (email, "x"),
        )
        return cur.lastrowid


def anzahl_bestellungen(uid: int, ebook_id: str = "dein-erstes-auto") -> int:
    with db.get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM ebook_bestellung WHERE user_id=? AND ebook_id=?",
            (uid, ebook_id),
        ).fetchone()[0]


def checks(uid: int) -> int:
    with db.get_conn() as conn:
        return conn.execute(
            "SELECT checks_verbleibend FROM users WHERE id=?", (uid,)
        ).fetchone()[0]


class _Meta:
    """Ahmt Stripes StripeObject-Metadata mit ._data nach."""
    def __init__(self, d):
        self._data = d


class _Session:
    """Minimaler Stand-in fuer ein Stripe-Checkout-Session-Objekt."""
    def __init__(self, sid, meta, payment_status="paid", status="complete",
                 payment_intent="pi_test", created=None, subscription=None):
        self.id = sid
        self.metadata = _Meta(meta)
        self.payment_status = payment_status
        self.status = status
        self.payment_intent = payment_intent
        self.created = created if created is not None else int(time.time())
        self.subscription = subscription


# ── 1) E-Book: bezahlte Session -> genau EINE Bestellung, auch bei 2 Aufrufen ─
uid = neuer_user("ebook@test.local")
sess = _Session("cs_test_ebook_1", {
    "typ": "ebook", "user_id": str(uid),
    "ebook_id": "dein-erstes-auto", "preis_bezahlt": "14.99",
})
pay._verarbeite_checkout_session(sess)
check("E-Book: 1 Bestellung nach erstem Aufruf", anzahl_bestellungen(uid) == 1)
pay._verarbeite_checkout_session(sess)   # zweiter Weg (Webhook + Verify)
check("E-Book: KEINE Doppel-Bestellung nach zweitem Aufruf", anzahl_bestellungen(uid) == 1)

# ── 2) Einzelkauf: Checks nur +1, auch bei 2 Aufrufen ───────────────────────
uid2 = neuer_user("einzel@test.local")
sess2 = _Session("cs_test_einzel_1", {"typ": "einzelkauf", "user_id": str(uid2)})
vor = checks(uid2)
pay._verarbeite_checkout_session(sess2)
pay._verarbeite_checkout_session(sess2)
check("Einzelkauf: Checks genau +1 (kein Doppel-Credit)", checks(uid2) == vor + 1)

# ── Stripe.retrieve mocken fuer die verify_session-Tests ────────────────────
_orig_retrieve = pay.stripe.checkout.Session.retrieve
try:
    # 3) bezahlte, eigene Session -> freigeschaltet + idempotent
    uid3 = neuer_user("verify@test.local")
    paid = _Session("cs_test_verify_paid", {
        "typ": "ebook", "user_id": str(uid3),
        "ebook_id": "dein-erstes-auto", "preis_bezahlt": "14.99",
    })
    pay.stripe.checkout.Session.retrieve = lambda sid: paid
    res = pay.verify_session(pay.VerifySessionBody(session_id="cs_test_verify_paid"), user_id=uid3)
    check("verify: bezahlt+eigen -> freigeschaltet True", res.get("freigeschaltet") is True)
    check("verify: Bestellung angelegt", anzahl_bestellungen(uid3) == 1)
    pay.verify_session(pay.VerifySessionBody(session_id="cs_test_verify_paid"), user_id=uid3)
    check("verify: idempotent (keine Doppel-Bestellung)", anzahl_bestellungen(uid3) == 1)

    # 4) unbezahlte Session -> keine Freischaltung
    uid4 = neuer_user("unpaid@test.local")
    unpaid = _Session("cs_test_unpaid", {
        "typ": "ebook", "user_id": str(uid4),
        "ebook_id": "dein-erstes-auto", "preis_bezahlt": "14.99",
    }, payment_status="unpaid", status="open")
    pay.stripe.checkout.Session.retrieve = lambda sid: unpaid
    res_u = pay.verify_session(pay.VerifySessionBody(session_id="cs_test_unpaid"), user_id=uid4)
    check("verify: unbezahlt -> freigeschaltet False", res_u.get("freigeschaltet") is False)
    check("verify: unbezahlt -> keine Bestellung", anzahl_bestellungen(uid4) == 0)

    # 5) fremde user_id in der Session -> 403, keine Freischaltung
    uid5 = neuer_user("owner@test.local")
    fremd = _Session("cs_test_fremd", {
        "typ": "ebook", "user_id": str(uid5),
        "ebook_id": "dein-erstes-auto", "preis_bezahlt": "14.99",
    })
    pay.stripe.checkout.Session.retrieve = lambda sid: fremd
    forbidden = False
    try:
        # anderer eingeloggter Nutzer (uid3) versucht, fremde Session zu claimen
        pay.verify_session(pay.VerifySessionBody(session_id="cs_test_fremd"), user_id=uid3)
    except HTTPException as e:
        forbidden = (e.status_code == 403)
    check("verify: fremde user_id -> 403", forbidden)
    check("verify: fremd -> keine Bestellung fuer Session-Eigner", anzahl_bestellungen(uid5) == 0)
finally:
    pay.stripe.checkout.Session.retrieve = _orig_retrieve

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER: " + ", ".join(FEHLER))
    raise SystemExit(1)
print("Alle Verify-/Idempotenz-Tests bestanden.")
