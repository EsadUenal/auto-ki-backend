"""
Test: VIRA Plus — Monatskontingente, Verbrauchsreihenfolge, Bestandsschutz.

Prueft OHNE Netzwerk, OHNE Login und OHNE echtes Stripe gegen eine TEMPORAERE DB.

  U)  Plus-Nutzer erhaelt 5 KaufChecks / 1 VerkaufsCheck
  V)  Renewal SETZT auf 5/1 (kein Addieren)
  W)  nicht verbrauchte Plus-Credits rollen NICHT ins naechste Monat
  X)  gekaufte Credits ueberleben ein Renewal
  Y)  gekaufte Credits ueberleben die Kuendigung
  Z)  Plus-Credit wird ZUERST konsumiert
  AA) danach gekauftes typgebundenes Guthaben
  AB) danach legitimes generisches Legacy-Guthaben
  AC) Refund landet exakt im konsumierten Topf
  Q)  Monatsgrant ist idempotent (doppeltes Event -> weiterhin 5/1)
  S)  fehlgeschlagene Zahlung -> kein Grant
  T)  nach Periodenende -> kein Plus mehr, gekauftes Guthaben bleibt

Ausfuehren:  python test_plus_entitlements.py
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

_TMP = tempfile.mkdtemp(prefix="vira_plus_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "test.db")

import app.database as db          # noqa: E402
db.ensure_tables()

import app.check_gate as gate      # noqa: E402
from app import plus               # noqa: E402
from app.config import PLUS_KAUFCHECKS_PRO_MONAT, PLUS_VERKAUFSCHECKS_PRO_MONAT  # noqa: E402
from fastapi import HTTPException  # noqa: E402

FEHLER = []


def check(name, cond, info=""):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}{(' — ' + info) if info else ''}")
    if not cond:
        FEHLER.append(name)


def ts(tage: int) -> int:
    return int((datetime.now(timezone.utc) + timedelta(days=tage)).timestamp())


def neuer_user(email, kauf=0, verkauf=0, generic=0, abo="none"):
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, abo_typ, checks_verbleibend, "
            "kaufchecks_verbleibend, verkaufschecks_verbleibend) VALUES (?,?,?,?,?,?)",
            (email, "x", abo, generic, kauf, verkauf))
        conn.commit()
        return cur.lastrowid


def stand(uid):
    """(generic, gekauft_kauf, gekauft_verkauf, plus_kauf, plus_verkauf)"""
    with db.get_conn() as conn:
        r = conn.execute(
            "SELECT checks_verbleibend, kaufchecks_verbleibend, verkaufschecks_verbleibend, "
            "plus_kaufchecks_verbleibend, plus_verkaufschecks_verbleibend FROM users WHERE id=?",
            (uid,)).fetchone()
    return (r["checks_verbleibend"], r["kaufchecks_verbleibend"], r["verkaufschecks_verbleibend"],
            r["plus_kaufchecks_verbleibend"], r["plus_verkaufschecks_verbleibend"])


# ── U) Erste Plus-Periode ────────────────────────────────────────────────────
uid = neuer_user("u@test.de")
plus.grant_periode(uid, "sub_U", ts(-1), ts(29))
check("U: Plus-Nutzer erhaelt 5 KaufChecks / 1 VerkaufsCheck",
      stand(uid)[3:] == (PLUS_KAUFCHECKS_PRO_MONAT, PLUS_VERKAUFSCHECKS_PRO_MONAT), str(stand(uid)))
with db.get_conn() as conn:
    row = conn.execute("SELECT plus_period_end FROM users WHERE id=?", (uid,)).fetchone()
check("U: Plus ist aktiv (bezahlter Zeitraum laeuft)", plus.ist_aktiv(row))

# ── Q) Idempotenz: dasselbe Event zweimal ────────────────────────────────────
plus.grant_periode(uid, "sub_U", ts(-1), ts(29))
plus.grant_periode(uid, "sub_U", ts(-1), ts(29))
check("Q: doppelt zugestelltes Event -> weiterhin 5/1 (kein 10/2)",
      stand(uid)[3:] == (5, 1), str(stand(uid)))

# ── V/W) Renewal setzt zurueck statt zu addieren ─────────────────────────────
uid = neuer_user("v@test.de")
# Monat 1 laeuft noch — sonst wuerde das Gate den Plus-Topf zu Recht ignorieren.
plus.grant_periode(uid, "sub_V", ts(-1), ts(29))
gate._entnehme(uid, "kauf")          # 5 -> 4
gate._entnehme(uid, "kauf")          # 4 -> 3
check("V: nach 2 Checks stehen 3 Plus-KaufChecks", stand(uid)[3] == 3, str(stand(uid)))
plus.grant_periode(uid, "sub_V", ts(29), ts(59))  # neuer bezahlter Monat
check("V: Renewal setzt auf 5/1 zurueck", stand(uid)[3:] == (5, 1), str(stand(uid)))
check("W: nicht verbrauchte Plus-Checks rollen NICHT (kein 3+5=8)", stand(uid)[3] == 5)

# ── X) Gekaufte Credits ueberleben das Renewal ───────────────────────────────
uid = neuer_user("x@test.de", kauf=2, verkauf=1)
plus.grant_periode(uid, "sub_X", ts(-1), ts(29))
plus.grant_periode(uid, "sub_X", ts(29), ts(59))
check("X: gekaufte Credits ueberleben das Renewal unveraendert",
      stand(uid)[1:3] == (2, 1), str(stand(uid)))

# ── Y) Gekaufte Credits ueberleben die Kuendigung ────────────────────────────
uid = neuer_user("y@test.de", kauf=3, verkauf=2)
plus.grant_periode(uid, "sub_Y", ts(-1), ts(29))
plus.beende("sub_Y")
check("Y: gekaufte Credits ueberleben die Kuendigung", stand(uid)[1:3] == (3, 2), str(stand(uid)))
check("Y: Plus-Kontingente sind nach der Kuendigung weg", stand(uid)[3:] == (0, 0), str(stand(uid)))
with db.get_conn() as conn:
    row = conn.execute("SELECT plus_period_end FROM users WHERE id=?", (uid,)).fetchone()
check("Y: Plus ist danach nicht mehr aktiv", not plus.ist_aktiv(row))
z = gate._entnehme(uid, "kauf")
check("Y: der gekaufte KaufCheck ist weiterhin nutzbar", z.quelle == "kaufchecks_verbleibend", z.quelle)

# ── Z/AA/AB) Verbrauchsreihenfolge ───────────────────────────────────────────
uid = neuer_user("z@test.de", kauf=1, generic=1)
plus.grant_periode(uid, "sub_Z", ts(-1), ts(29))
z1 = gate._entnehme(uid, "kauf")
check("Z: ZUERST wird das Plus-Kontingent verbraucht",
      z1.quelle == "plus_kaufchecks_verbleibend", z1.quelle)
for _ in range(4):
    gate._entnehme(uid, "kauf")      # Plus aufbrauchen
check("Z: Plus-Topf ist danach leer, Gekauftes unberuehrt",
      stand(uid)[3] == 0 and stand(uid)[1] == 1, str(stand(uid)))
z2 = gate._entnehme(uid, "kauf")
check("AA: danach wird das GEKAUFTE Guthaben verbraucht",
      z2.quelle == "kaufchecks_verbleibend", z2.quelle)
z3 = gate._entnehme(uid, "kauf")
check("AB: zuletzt der generische Legacy-Topf",
      z3.quelle == "checks_verbleibend", z3.quelle)
try:
    gate._entnehme(uid, "kauf")
    check("AB: danach blockiert (402)", False)
except HTTPException as e:
    check("AB: danach blockiert (402)", e.status_code == 402)

# ── AC) Refund exakt in den konsumierten Topf ────────────────────────────────
for topf, setup in [
    ("plus_kaufchecks_verbleibend", dict(kauf=0, generic=0)),
    ("kaufchecks_verbleibend",      dict(kauf=1, generic=0)),
    ("checks_verbleibend",          dict(kauf=0, generic=1)),
]:
    uid = neuer_user(f"ac-{topf}@test.de", **setup)
    if topf == "plus_kaufchecks_verbleibend":
        plus.grant_periode(uid, f"sub_{topf}", ts(-1), ts(29))
    vorher = stand(uid)
    zz = gate._entnehme(uid, "kauf")
    check(f"AC: Entnahme aus {topf}", zz.quelle == topf, zz.quelle)
    gate.refund_check_credit(zz)
    check(f"AC: Refund landet exakt in {topf}", stand(uid) == vorher,
          f"{vorher} -> {stand(uid)}")

# Ein fehlgeschlagener VerkaufsCheck darf keinen KaufCheck-Anspruch erzeugen.
uid = neuer_user("ac-typ@test.de")
plus.grant_periode(uid, "sub_ACT", ts(-1), ts(29))
zv = gate._entnehme(uid, "verkauf")
gate.refund_check_credit(zv)
check("AC: Refund eines VerkaufsChecks erzeugt keinen KaufCheck",
      stand(uid)[3:] == (5, 1), str(stand(uid)))

# ── S) Fehlgeschlagene Zahlung -> kein Grant ─────────────────────────────────
uid = neuer_user("s@test.de")
check("S: ohne bezahlten Zeitraum gibt es kein Plus-Kontingent",
      stand(uid)[3:] == (0, 0), str(stand(uid)))
try:
    gate._entnehme(uid, "kauf")
    check("S: und der Check ist blockiert", False)
except HTTPException as e:
    check("S: und der Check ist blockiert (402)", e.status_code == 402)

# ── T) Abgelaufener Zeitraum ─────────────────────────────────────────────────
uid = neuer_user("t@test.de", kauf=1)
plus.grant_periode(uid, "sub_T", ts(-40), ts(-5))   # bereits abgelaufen
with db.get_conn() as conn:
    row = conn.execute("SELECT plus_period_end FROM users WHERE id=?", (uid,)).fetchone()
check("T: abgelaufener Zeitraum -> Plus nicht aktiv", not plus.ist_aktiv(row))
zt = gate._entnehme(uid, "kauf")
check("T: das Plus-Kontingent wird nicht mehr angefasst",
      zt.quelle == "kaufchecks_verbleibend", zt.quelle)
check("T: gekauftes Guthaben blieb erhalten und wurde jetzt genutzt",
      stand(uid)[1] == 0)

# ── Statusanzeige ────────────────────────────────────────────────────────────
uid = neuer_user("st@test.de", kauf=2)
plus.grant_periode(uid, "sub_ST", ts(-1), ts(29))
k = gate.kontingente(uid)
check("Anzeige trennt gekauft und monatlich",
      k["kaufchecks_verbleibend"] == 2 and k["plus_kaufchecks_verbleibend"] == 5 and k["plus_aktiv"],
      str({x: k[x] for x in ("kaufchecks_verbleibend", "plus_kaufchecks_verbleibend", "plus_aktiv")}))

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER:")
    for f in FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("Alle Plus-Entitlement-Tests bestanden.")
