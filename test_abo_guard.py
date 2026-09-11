"""
Regressionstest für die Launch-Sicherung gegen parallele Abos
(app/routers/payments.py :: _hat_laufendes_abo).

Kein Netzwerk-/Stripe-Aufruf: stripe.Subscription.list wird durch eine Attrappe
ersetzt, die eine kontrollierte Statusliste zurückgibt. Getestet wird die
Blockier-Entscheidung für alle relevanten Subscription-Zustände.

Ausführen: python test_abo_guard.py
"""

import app.routers.payments as payments

FEHLER = []


class _FakeSub:
    def __init__(self, status):
        self.status = status


class _FakeList:
    def __init__(self, subs):
        self.data = subs


def _patch(subs):
    """Ersetzt stripe.Subscription.list durch eine Attrappe mit fixer Statusliste."""
    payments.stripe.Subscription.list = staticmethod(lambda **kwargs: _FakeList(subs))


def check(name, statuses, erwartet_blockiert):
    _patch([_FakeSub(s) for s in statuses])
    ergebnis = payments._hat_laufendes_abo("cus_test")
    if ergebnis != erwartet_blockiert:
        FEHLER.append(
            f"[FEHLER] {name}\n  Zustände: {statuses}\n"
            f"  Erwartet blockiert={erwartet_blockiert}, erhalten={ergebnis}"
        )
    else:
        zustand = "blockiert" if ergebnis else "erlaubt"
        print(f"[OK] {name} -> {zustand}")


# ── Blockiert (laufendes Abo) ──────────────────────────────────────────────────
check("aktives Abo", ["active"], True)
check("trialing Abo", ["trialing"], True)
# cancel_at_period_end (noch nicht abgelaufen) = bei Stripe weiterhin status active
check("gekündigt aber noch laufend (cancel_at_period_end)", ["active"], True)
# past_due = Abo besteht weiter, Stripe versucht Zahlung erneut → blockieren
check("past_due (Dunning, kann sich erholen)", ["past_due"], True)
check("mehrere Subs, eine aktiv", ["canceled", "active"], True)
check("mehrere Subs, eine trialing", ["canceled", "trialing"], True)
check("mehrere Subs, eine past_due", ["canceled", "past_due"], True)

# ── Erlaubt (kein laufendes Abo) ───────────────────────────────────────────────
check("kein Abo (leer)", [], False)
check("gekündigt und beendet", ["canceled"], False)
check("incomplete", ["incomplete"], False)
check("incomplete_expired", ["incomplete_expired"], False)
check("unpaid", ["unpaid"], False)
check("nur beendete Historie", ["canceled", "canceled", "incomplete_expired"], False)


# ── Struktur-Checks: Sperre nur im Abo-Zweig, Einmalkäufe unberührt ────────────
import inspect

_src = inspect.getsource(payments.create_checkout_session)
if '_hat_laufendes_abo(customer_id)' not in _src:
    FEHLER.append("[FEHLER] Guard-Aufruf _hat_laufendes_abo fehlt in create_checkout_session")
else:
    print("[OK] Guard wird in create_checkout_session aufgerufen")

# Security Fix Block 1: Legacy-Abos ("abo") und "einzelkauf" sind nicht mehr
# kaufbar. Das einzige Abo ist Plus — der Guard muss dort VOR der Session-
# Erzeugung stehen und darf den Check-Zweig (Einmalkauf, mode='payment') nicht
# betreffen.
_check_teil, _plus_teil = _src.split('elif body.typ == "plus"')
_plus_teil = _plus_teil.split("else:")[0]
if ('_hat_laufendes_abo' in _plus_teil and 'mode="subscription"' in _plus_teil
        and _plus_teil.index('_hat_laufendes_abo') < _plus_teil.index('Session.create')):
    print("[OK] Guard steht im Plus-Zweig vor der Session-Erzeugung (mode=subscription)")
else:
    FEHLER.append("[FEHLER] Guard nicht korrekt im Plus-Zweig platziert")

if '_hat_laufendes_abo' in _check_teil.split('if body.typ == "check"')[-1]:
    FEHLER.append("[FEHLER] Guard betrifft faelschlich den Check-Zweig (Einmalkauf)")
else:
    print("[OK] Check-Zweig (Einmalkauf) unberührt")


# ── Ergebnis ───────────────────────────────────────────────────────────────────
print()
if FEHLER:
    print("\n".join(FEHLER))
    print(f"\n{len(FEHLER)} FEHLER")
    raise SystemExit(1)
print("Alle Abo-Guard-Tests bestanden.")
