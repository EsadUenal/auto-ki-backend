"""
Test: Consumer Pricing V1 — getrennte Check-Berechtigungen (Payments + Gate)

Prueft OHNE Netzwerk, OHNE Login und OHNE echtes Stripe (Stripe wird gemockt)
gegen eine TEMPORAERE DB, dass KaufCheck und VerkaufsCheck serverseitig
getrennte Produkte sind und Zahlungen nicht manipulierbar oder doppelt
verrechenbar sind.

Abgedeckte Punkte der Freigabematrix:
  B) KaufCheck-Checkout waehlt serverseitig das richtige Produkt
  C) VerkaufsCheck-Checkout waehlt serverseitig das richtige Produkt
  D) Client kann Preis/Betrag nicht bestimmen
  E) gueltiger Webhook  -> genau 1 Berechtigung
  F) doppelter Webhook  -> weiterhin genau 1
  G) ungueltige Signatur -> 0
  H) unbekanntes Produkt -> 0
  I) KaufCheck-Berechtigung startet keinen VerkaufsCheck
  J) VerkaufsCheck-Berechtigung startet keinen KaufCheck
  K) keine Berechtigung  -> Check blockiert (402)
  L) 1 Berechtigung + zwei parallele Starts -> genau einer kommt durch
  M) technischer Fehler  -> Berechtigung kommt in denselben Topf zurueck
  N) erfolgreicher Check -> verbraucht
  O) Legacy-Kontingent bleibt fuer beide Check-Arten gueltig (Bestandsschutz)

Ausfuehren:  python test_payments_entitlements.py
"""
import os
import tempfile
import threading

# WICHTIG: temporaere DB VOR dem Import der app-Module setzen.
_TMP = tempfile.mkdtemp(prefix="vira_ent_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["STRIPE_PRICE_KAUFCHECK"] = "price_TEST_kauf"
os.environ["STRIPE_PRICE_VERKAUFSCHECK"] = "price_TEST_verkauf"

import app.database as db            # noqa: E402
db.ensure_tables()

import app.check_gate as gate        # noqa: E402
import app.routers.payments as pay   # noqa: E402
from fastapi import HTTPException    # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


# ── Helfer ───────────────────────────────────────────────────────────────────

def neuer_user(email: str, checks: int = 0, kauf: int = 0, verkauf: int = 0,
               abo: str = "none") -> int:
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, abo_typ, checks_verbleibend, "
            "kaufchecks_verbleibend, verkaufschecks_verbleibend) VALUES (?,?,?,?,?,?)",
            (email, "x", abo, checks, kauf, verkauf),
        )
        conn.commit()
        return cur.lastrowid


def stand(uid: int) -> tuple[int, int, int]:
    with db.get_conn() as conn:
        r = conn.execute(
            "SELECT checks_verbleibend, kaufchecks_verbleibend, verkaufschecks_verbleibend "
            "FROM users WHERE id=?", (uid,)
        ).fetchone()
    return r["checks_verbleibend"], r["kaufchecks_verbleibend"], r["verkaufschecks_verbleibend"]


class FakeSession:
    """Minimaler Ersatz fuer ein Stripe-Checkout-Session-Objekt."""
    def __init__(self, sid, metadata):
        self.id = sid
        self.metadata = metadata
        self.url = "https://checkout.stripe.test/" + sid
        self.payment_status = "paid"
        self.status = "complete"


_ERZEUGT = []


def fake_session_create(**kwargs):
    _ERZEUGT.append(kwargs)
    return FakeSession(f"cs_test_{len(_ERZEUGT)}", kwargs.get("metadata", {}))


pay.stripe.checkout.Session.create = staticmethod(fake_session_create)
pay.stripe.Customer.create = staticmethod(
    lambda **kw: type("C", (), {"id": "cus_test_1"})()
)
# Die neuen Preise stammen aus der Server-Konfiguration, nicht aus dem Request.
pay._CHECK_PRICE = {
    "kaufcheck":     lambda: "price_TEST_kauf",
    "verkaufscheck": lambda: "price_TEST_verkauf",
}


def checkout(user_id: int, **body_felder):
    _ERZEUGT.clear()
    body = pay.CheckoutBody(agb_akzeptiert=True, widerruf_verzicht=True, **body_felder)
    return pay.create_checkout_session(body, user_id=user_id)


def webhook_session(user_id: int, produkt: str, sid: str):
    """Simuliert ein signatur-geprueftes checkout.session.completed."""
    obj = FakeSession(sid, {"user_id": str(user_id), "typ": "check", "produkt": produkt})
    pay._verarbeite_checkout_session(obj)


# ── B) KaufCheck-Checkout waehlt serverseitig das richtige Produkt ───────────
uid = neuer_user("b@test.de")
checkout(uid, typ="check", produkt="kaufcheck")
check("B: KaufCheck-Checkout nutzt die serverseitige KaufCheck-Price-ID",
      _ERZEUGT[0]["line_items"] == [{"price": "price_TEST_kauf", "quantity": 1}])
check("B: KaufCheck-Checkout ist eine Einmalzahlung (mode=payment)",
      _ERZEUGT[0]["mode"] == "payment")
check("B: Produkt steht serverseitig in der Metadata",
      _ERZEUGT[0]["metadata"]["produkt"] == "kaufcheck")

# ── C) VerkaufsCheck-Checkout ────────────────────────────────────────────────
checkout(uid, typ="check", produkt="verkaufscheck")
check("C: VerkaufsCheck-Checkout nutzt die serverseitige VerkaufsCheck-Price-ID",
      _ERZEUGT[0]["line_items"] == [{"price": "price_TEST_verkauf", "quantity": 1}])

# ── D) Client kann Preis/Betrag nicht bestimmen ──────────────────────────────
# Das Request-Schema kennt ueberhaupt kein Betragsfeld — ein mitgeschicktes
# `preis`/`amount` wird von Pydantic verworfen und kann nichts beeinflussen.
body_mit_preis = pay.CheckoutBody(
    typ="check", produkt="kaufcheck", agb_akzeptiert=True, widerruf_verzicht=True,
    **{},
)
check("D: CheckoutBody besitzt kein Betragsfeld",
      not any(f in pay.CheckoutBody.model_fields for f in ("preis", "amount", "unit_amount", "price")))
_ERZEUGT.clear()
pay.create_checkout_session(body_mit_preis, user_id=uid)
check("D: Betrag stammt weiterhin aus der Server-Price-ID",
      _ERZEUGT[0]["line_items"] == [{"price": "price_TEST_kauf", "quantity": 1}])

try:
    checkout(uid, typ="check", produkt="gratischeck")
    check("D: unbekanntes Produkt wird abgelehnt", False)
except HTTPException as e:
    check("D: unbekanntes Produkt wird abgelehnt (400)", e.status_code == 400)

# ── E) Gueltiger Webhook -> genau 1 Berechtigung ─────────────────────────────
uid = neuer_user("e@test.de")
webhook_session(uid, "kaufcheck", "cs_e_1")
check("E: bezahlter KaufCheck schreibt genau 1 KaufCheck-Berechtigung gut",
      stand(uid) == (0, 1, 0))

# ── F) Doppelter Webhook -> weiterhin genau 1 ────────────────────────────────
webhook_session(uid, "kaufcheck", "cs_e_1")
webhook_session(uid, "kaufcheck", "cs_e_1")
check("F: dasselbe Event mehrfach -> weiterhin genau 1 Berechtigung",
      stand(uid) == (0, 1, 0))

# ── G) Ungueltige Signatur -> 0 ──────────────────────────────────────────────
uid_g = neuer_user("g@test.de")


class _SigFehler(Exception):
    pass


_orig_construct = pay.stripe.Webhook.construct_event
pay.stripe.Webhook.construct_event = staticmethod(
    lambda payload, sig, secret: (_ for _ in ()).throw(_SigFehler("bad signature"))
)
import asyncio  # noqa: E402


class _FakeRequest:
    def __init__(self, body: bytes, sig: str):
        self._body = body
        self.headers = {"stripe-signature": sig}

    async def body(self):
        return self._body


try:
    asyncio.run(pay.stripe_webhook(_FakeRequest(b"{}", "falsch")))
    check("G: ungueltige Webhook-Signatur wird abgelehnt", False)
except HTTPException as e:
    check("G: ungueltige Webhook-Signatur wird abgelehnt (400)", e.status_code == 400)
pay.stripe.Webhook.construct_event = _orig_construct
check("G: nach ungueltiger Signatur wurde nichts gutgeschrieben", stand(uid_g) == (0, 0, 0))

# ── H) Unbekanntes Produkt im Webhook -> 0 ───────────────────────────────────
uid_h = neuer_user("h@test.de")
try:
    webhook_session(uid_h, "premium_bundle", "cs_h_1")
except ValueError:
    pass   # erwartet: Claim wird zurueckgerollt, Stripe retryt
check("H: unbekanntes Produkt schaltet nichts frei", stand(uid_h) == (0, 0, 0))

# ── I) KaufCheck-Berechtigung startet keinen VerkaufsCheck ───────────────────
uid = neuer_user("i@test.de", kauf=1)
try:
    gate._entnehme(uid, "verkauf")
    check("I: KaufCheck-Berechtigung startet keinen VerkaufsCheck", False)
except HTTPException as e:
    check("I: KaufCheck-Berechtigung startet keinen VerkaufsCheck (402)", e.status_code == 402)
check("I: die KaufCheck-Berechtigung blieb dabei unangetastet", stand(uid) == (0, 1, 0))

# ── J) VerkaufsCheck-Berechtigung startet keinen KaufCheck ───────────────────
uid = neuer_user("j@test.de", verkauf=1)
try:
    gate._entnehme(uid, "kauf")
    check("J: VerkaufsCheck-Berechtigung startet keinen KaufCheck", False)
except HTTPException as e:
    check("J: VerkaufsCheck-Berechtigung startet keinen KaufCheck (402)", e.status_code == 402)
check("J: die VerkaufsCheck-Berechtigung blieb dabei unangetastet", stand(uid) == (0, 0, 1))

# ── K) Keine Berechtigung -> Check blockiert ─────────────────────────────────
uid = neuer_user("k@test.de")
for typ in ("kauf", "verkauf"):
    try:
        gate._entnehme(uid, typ)
        check(f"K: ohne Berechtigung ist {typ} blockiert", False)
    except HTTPException as e:
        check(f"K: ohne Berechtigung ist {typ} blockiert (402)", e.status_code == 402)

# ── L) 1 Berechtigung + zwei parallele Starts -> genau einer ─────────────────
uid = neuer_user("l@test.de", kauf=1)
ergebnisse = []
barriere = threading.Barrier(2)


def starte():
    barriere.wait()
    try:
        ergebnisse.append(("ok", gate._entnehme(uid, "kauf")))
    except HTTPException:
        ergebnisse.append(("402", None))


t1, t2 = threading.Thread(target=starte), threading.Thread(target=starte)
t1.start(); t2.start(); t1.join(); t2.join()
erfolge = [e for e in ergebnisse if e[0] == "ok"]
check("L: zwei parallele Starts auf 1 Berechtigung -> genau einer kommt durch",
      len(erfolge) == 1)
check("L: das Kontingent steht danach auf 0 (kein Doppelverbrauch)",
      stand(uid) == (0, 0, 0))

# ── M) Technischer Fehler -> Berechtigung kommt in denselben Topf zurueck ────
uid = neuer_user("m@test.de", kauf=1)
zugriff = gate._entnehme(uid, "kauf")
check("M: nach Start ist die KaufCheck-Berechtigung abgezogen", stand(uid) == (0, 0, 0))
gate.refund_check_credit(zugriff)
check("M: nach technischem Fehler ist sie exakt im KaufCheck-Topf zurueck",
      stand(uid) == (0, 1, 0))

# Ein aus dem Legacy-Topf entnommener Check kommt auch dorthin zurueck.
uid = neuer_user("m2@test.de", checks=1)
z2 = gate._entnehme(uid, "verkauf")
check("M: Legacy-Kontingent wird als Fallback entnommen", stand(uid) == (0, 0, 0))
gate.refund_check_credit(z2)
check("M: Rueckerstattung landet im Legacy-Topf, nicht im VerkaufsCheck-Topf",
      stand(uid) == (1, 0, 0))

# MAX-Abo: nie dekrementiert, also auch nie erhoeht.
uid = neuer_user("m3@test.de", abo="max")
z3 = gate._entnehme(uid, "kauf")
gate.refund_check_credit(z3)
check("M: MAX-Abo bleibt unbegrenzt und wird durch Refund nicht zu Zaehlwerten",
      stand(uid) == (0, 0, 0) and z3.quelle == gate.QUELLE_UNBEGRENZT)

# ── N) Erfolgreicher Check -> verbraucht ─────────────────────────────────────
uid = neuer_user("n@test.de", kauf=1)
gate._entnehme(uid, "kauf")          # erfolgreicher Lauf: KEIN Refund
check("N: erfolgreicher Check verbraucht die Berechtigung endgueltig",
      stand(uid) == (0, 0, 0))
try:
    gate._entnehme(uid, "kauf")
    check("N: ein zweiter Check ohne neue Berechtigung ist blockiert", False)
except HTTPException as e:
    check("N: ein zweiter Check ohne neue Berechtigung ist blockiert (402)",
          e.status_code == 402)

# ── O) Bestandsschutz: Legacy-Kontingent gilt weiter fuer beide Arten ────────
uid = neuer_user("o@test.de", checks=2)
gate._entnehme(uid, "kauf")
gate._entnehme(uid, "verkauf")
check("O: Legacy-Kontingent bleibt fuer BEIDE Check-Arten gueltig",
      stand(uid) == (0, 0, 0))

# Typgebundenes Kontingent wird VOR dem Legacy-Topf verbraucht.
uid = neuer_user("o2@test.de", checks=1, kauf=1)
gate._entnehme(uid, "kauf")
check("O: typgebundenes Kontingent wird vor dem Legacy-Topf verbraucht",
      stand(uid) == (1, 0, 0))

# Gutschrift schreibt nur in den typgebundenen Topf.
uid = neuer_user("o3@test.de")
gate.gutschrift(uid, "verkaufscheck")
check("O: Gutschrift landet ausschliesslich im richtigen typgebundenen Topf",
      stand(uid) == (0, 0, 1))
try:
    gate.gutschrift(uid, "irgendwas")
    check("O: unbekanntes Produkt wird nicht gutgeschrieben", False)
except ValueError:
    check("O: unbekanntes Produkt wird nicht gutgeschrieben (ValueError)", True)

# ── Ergebnis ─────────────────────────────────────────────────────────────────
print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER:")
    for f in FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("Alle Entitlement-/Payment-Tests bestanden.")
