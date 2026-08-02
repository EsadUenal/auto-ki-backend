"""
Test: Phase-5-Berechtigung an das Abo-System gekoppelt (entitlements.has_dealer_access
+ require_dealer). MAX-Tarif = Händlertarif; ist_haendler bleibt manueller Override.

Prüft §9: abo-Matrix, manueller Override, Downgrade/Kündigung entzieht Zugriff,
DealerVehicle-Daten bleiben erhalten, erneutes MAX macht Bestand wieder sichtbar,
Ownership intakt.

Ausfuehren:  python test_dealer_access.py
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="vira_dacc_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "test.db")

import app.database as db                 # noqa: E402
db.ensure_tables()

import app.routers.dealer as dealer_r     # noqa: E402
from app.dealer import require_dealer      # noqa: E402
from app.entitlements import has_dealer_access  # noqa: E402
from app.models import DealerVehicleCreate  # noqa: E402
from fastapi import HTTPException           # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


def erlaubt(user_id) -> bool:
    try:
        return require_dealer(user_id) == user_id
    except HTTPException as e:
        if e.status_code == 403:
            return False
        raise


def neuer_user(email, abo_typ="none", ist_haendler=0) -> int:
    with db.get_conn() as conn:
        return conn.execute(
            "INSERT INTO users (email, password_hash, abo_typ, ist_haendler) VALUES (?, 'x', ?, ?)",
            (email, abo_typ, ist_haendler),
        ).lastrowid


def set_abo(user_id, abo_typ):
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET abo_typ=? WHERE id=?", (abo_typ, user_id))
        conn.commit()


# ── reine Funktions-Wahrheitstabelle ─────────────────────────────────────────
check("F: max -> True", has_dealer_access("max", False) is True)
check("F: pro -> False", has_dealer_access("pro", False) is False)
check("F: light -> False", has_dealer_access("light", False) is False)
check("F: none -> False", has_dealer_access("none", False) is False)
check("F: pro + Override -> True", has_dealer_access("pro", True) is True)
check("F: none + Override(1) -> True", has_dealer_access("none", 1) is True)

# ── require_dealer über echte DB ─────────────────────────────────────────────
u_max   = neuer_user("max@t.local",   abo_typ="max",   ist_haendler=0)
u_pro   = neuer_user("pro@t.local",   abo_typ="pro",   ist_haendler=0)
u_light = neuer_user("light@t.local", abo_typ="light", ist_haendler=0)
u_none  = neuer_user("none@t.local",  abo_typ="none",  ist_haendler=0)
u_ovr   = neuer_user("ovr@t.local",   abo_typ="pro",   ist_haendler=1)

check("1: MAX ohne Override -> erlaubt", erlaubt(u_max))
check("2: Pro -> 403", not erlaubt(u_pro))
check("3: Light -> 403", not erlaubt(u_light))
check("4: None -> 403", not erlaubt(u_none))
check("5: Pro + manueller Override -> erlaubt", erlaubt(u_ovr))

# ── 6/7) MAX -> Downgrade/Kündigung entzieht Zugriff ─────────────────────────
# MAX-Nutzer legt einen Bestand an
v = dealer_r.create_vehicle(DealerVehicleCreate(marke="BMW", modell="320d", einkaufspreis=20000), u_max)
check("Bestand als MAX angelegt", v.id is not None)

set_abo(u_max, "pro")     # Downgrade auf Pro (Stripe-Webhook-Verhalten simuliert)
check("6: MAX -> Pro -> Zugriff weg (403)", not erlaubt(u_max))
set_abo(u_max, "none")    # Kündigung/Ablauf
check("7: MAX -> none -> Zugriff weg (403)", not erlaubt(u_max))

# ── 8) DealerVehicle-Daten bleiben nach Downgrade in der DB ───────────────────
with db.get_conn() as conn:
    noch_da = conn.execute("SELECT COUNT(*) FROM dealer_vehicle WHERE user_id=?", (u_max,)).fetchone()[0]
check("8: DealerVehicle nach Downgrade weiterhin in DB", noch_da == 1)

# ── 9) erneutes MAX -> alter Bestand wieder sichtbar ─────────────────────────
set_abo(u_max, "max")
check("9a: erneutes MAX -> Zugriff wieder erlaubt", erlaubt(u_max))
liste = dealer_r.list_vehicles(u_max)
check("9b: alter Bestand unverändert wieder sichtbar", len(liste) == 1 and liste[0].id == v.id)
check("9c: Finanzdaten unverändert (Einkauf 20.000)", liste[0].finanzen.einkaufspreis == 20000)

# ── 10) Ownership weiterhin intakt (fremder MAX-Nutzer sieht es nicht) ───────
u_max2 = neuer_user("max2@t.local", abo_typ="max")
try:
    dealer_r.get_vehicle(v.id, u_max2)
    check("10: Fremd-MAX -> 404 (Ownership)", False)
except HTTPException as e:
    check("10: Fremd-MAX -> 404 (Ownership)", e.status_code == 404)

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER: " + ", ".join(FEHLER))
    raise SystemExit(1)
print("Alle Dealer-Access-Tests bestanden.")
