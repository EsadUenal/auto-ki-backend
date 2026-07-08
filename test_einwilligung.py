"""
Regressionstest fuer die serverseitige Einwilligungs-Erzwingung
(app/einwilligung.py + Verankerung in Registrierung/Checkouts).

Kein Netzwerk-/DB-Schreibzugriff: getestet werden die reine Erzwingungslogik
(HTTP 400 bei fehlender Zustimmung) sowie strukturell, dass Registrierung und
alle Checkout-Endpunkte die Zustimmung verlangen und die Request-Modelle die
Felder besitzen.

Ausfuehren: python test_einwilligung.py
"""

import inspect
from fastapi import HTTPException

import app.einwilligung as einw
import app.database as db
import app.routers.user_auth as ua
import app.routers.payments as pay
import app.routers.ebooks as eb

FEHLER = []


def check(name, cond):
    if cond:
        print(f"[OK] {name}")
    else:
        FEHLER.append(f"[FEHLER] {name}")


def raises_400(fn, arg):
    try:
        fn(arg)
        return False
    except HTTPException as e:
        return e.status_code == 400
    except Exception:
        return False


def allows(fn, arg):
    try:
        fn(arg)
        return True
    except Exception:
        return False


# ── Erzwingungslogik ────────────────────────────────────────────────────────
check("require_agb(False) -> 400", raises_400(einw.require_agb, False))
check("require_agb(True) erlaubt", allows(einw.require_agb, True))
check("require_widerruf_verzicht(False) -> 400", raises_400(einw.require_widerruf_verzicht, False))
check("require_widerruf_verzicht(True) erlaubt", allows(einw.require_widerruf_verzicht, True))

# ── Nachweis-Tabelle im Schema ──────────────────────────────────────────────
check("einwilligung-Tabelle im DB-Schema", "CREATE TABLE IF NOT EXISTS einwilligung" in db._SCHEMA_SQL)

# ── Request-Modelle haben die Zustimmungsfelder ─────────────────────────────
check("RegisterBody.agb_akzeptiert", "agb_akzeptiert" in ua.RegisterBody.model_fields)
check("payments.CheckoutBody.agb_akzeptiert", "agb_akzeptiert" in pay.CheckoutBody.model_fields)
check("payments.CheckoutBody.widerruf_verzicht", "widerruf_verzicht" in pay.CheckoutBody.model_fields)
check("ebooks.CheckoutBody.agb_akzeptiert", "agb_akzeptiert" in eb.CheckoutBody.model_fields)
check("ebooks.CheckoutBody.widerruf_verzicht", "widerruf_verzicht" in eb.CheckoutBody.model_fields)

# ── Endpunkte erzwingen die Zustimmung ──────────────────────────────────────
reg_src = inspect.getsource(ua.register)
check("register() erzwingt AGB", "require_agb(" in reg_src)
check("register() protokolliert AGB", "record_einwilligung(" in reg_src)

pay_src = inspect.getsource(pay.create_checkout_session)
check("payments-Checkout erzwingt AGB", "require_agb(" in pay_src)
check("payments-Checkout erzwingt Widerruf-Verzicht", "require_widerruf_verzicht(" in pay_src)
check("payments-Checkout protokolliert", "record_einwilligung(" in pay_src)

eb_src = inspect.getsource(eb.create_ebook_checkout)
check("ebook-Checkout erzwingt AGB", "require_agb(" in eb_src)
check("ebook-Checkout erzwingt Widerruf-Verzicht", "require_widerruf_verzicht(" in eb_src)
check("ebook-Checkout protokolliert", "record_einwilligung(" in eb_src)

# ── Defaults sind restriktiv (False) -> ohne Zustimmung wird abgelehnt ──────
check("RegisterBody.agb_akzeptiert Default False", ua.RegisterBody.model_fields["agb_akzeptiert"].default is False)
check("payments widerruf_verzicht Default False", pay.CheckoutBody.model_fields["widerruf_verzicht"].default is False)
check("ebooks widerruf_verzicht Default False", eb.CheckoutBody.model_fields["widerruf_verzicht"].default is False)

print()
if FEHLER:
    print("\n".join(FEHLER))
    print(f"\n{len(FEHLER)} FEHLER")
    raise SystemExit(1)
print("Alle Einwilligungs-Tests bestanden.")
