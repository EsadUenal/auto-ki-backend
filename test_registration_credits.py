"""
Test: Neu registrierte Konten passen zum bezahlten Check-Pricing.

Hintergrund: bis Consumer Pricing V1 bekam jede Registrierung einen
generischen Gratis-Check (`checks_verbleibend = 1`). Mit zwei getrennt
bepreisten Einmalprodukten (KaufCheck 9,99 EUR, VerkaufsCheck 7,99 EUR) passt
das nicht mehr — und die Preisseite bewirbt auch keinen Gratis-Check.

Ebenso wichtig ist die Gegenrichtung: Bestandsnutzer duerfen dabei NICHTS
verlieren. Ein bereits vorhandenes generisches Guthaben bleibt gueltig und
weiterhin fuer BEIDE Check-Arten einloesbar.

  A) neuer Nutzer startet mit generic=0, kauf=0, verkauf=0
  B) neuer Nutzer ohne KaufCheck-Berechtigung   -> KaufCheck blockiert
  C) neuer Nutzer ohne VerkaufsCheck-Berechtigung -> VerkaufsCheck blockiert
  D) Bestandsnutzer mit generic=1 darf weiterhin genau einen Check nutzen
  E) das Legacy-Guthaben wird dabei korrekt 1 -> 0 verbraucht
  F) typgebundenes KaufCheck-Guthaben bleibt getrennt
  G) typgebundenes VerkaufsCheck-Guthaben bleibt getrennt
  H) eine Registrierung veraendert KEINEN Bestandsnutzer

Ausfuehren:  python test_registration_credits.py
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="vira_reg_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "test.db")

import app.database as db          # noqa: E402
db.ensure_tables()

import app.check_gate as gate      # noqa: E402
import app.routers.user_auth as auth  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from starlette.requests import Request  # noqa: E402

FEHLER = []


def check(name, cond, info=""):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}{(' — ' + info) if info else ''}")
    if not cond:
        FEHLER.append(name)


class _FakeResponse:
    """Nimmt das Auth-Cookie entgegen; Inhalt ist hier nicht Gegenstand."""
    def set_cookie(self, **kwargs):
        pass


def _request() -> Request:
    """Echtes Starlette-Request-Objekt.

    Der slowapi-Dekorator auf `register` prueft den Typ und lehnt eine
    Attrappe ab — deshalb hier ein minimaler, aber echter ASGI-Scope statt
    eines nachgebauten Objekts.
    """
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/auth/register",
        "headers": [],
        "query_string": b"",
        "client": ("127.0.0.1", 1234),
    })


def registriere(email: str) -> dict:
    body = auth.RegisterBody(email=email, password="ein-langes-testpasswort", agb_akzeptiert=True)
    return auth.register(body, _FakeResponse(), _request())


def stand(uid: int):
    with db.get_conn() as conn:
        r = conn.execute(
            "SELECT checks_verbleibend, kaufchecks_verbleibend, verkaufschecks_verbleibend "
            "FROM users WHERE id=?", (uid,)
        ).fetchone()
    return r["checks_verbleibend"], r["kaufchecks_verbleibend"], r["verkaufschecks_verbleibend"]


# ── A) Neuer Nutzer startet ohne jede Check-Berechtigung ────────────────────
antwort = registriere("neu-a@test.de")
uid_a = antwort["id"]
check("A: neuer Nutzer startet mit generic=0, kauf=0, verkauf=0",
      stand(uid_a) == (0, 0, 0), str(stand(uid_a)))
check("A: die Registrierungs-Antwort meldet dasselbe (kein Phantom-Guthaben)",
      antwort["checks_verbleibend"] == 0
      and antwort["kaufchecks_verbleibend"] == 0
      and antwort["verkaufschecks_verbleibend"] == 0,
      str({k: antwort[k] for k in ("checks_verbleibend", "kaufchecks_verbleibend",
                                   "verkaufschecks_verbleibend")}))

# Der Schema-Default darf ebenfalls nichts verschenken.
with db.get_conn() as conn:
    spalten = {r[1]: r[4] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
check("A: auch der Schema-Default verschenkt keinen Check",
      spalten["checks_verbleibend"] == "0"
      and spalten["kaufchecks_verbleibend"] == "0"
      and spalten["verkaufschecks_verbleibend"] == "0",
      str({k: spalten[k] for k in ("checks_verbleibend", "kaufchecks_verbleibend",
                                   "verkaufschecks_verbleibend")}))

# ── B/C) Ohne Berechtigung sind beide Checks blockiert ──────────────────────
for typ, name in (("kauf", "B: KaufCheck"), ("verkauf", "C: VerkaufsCheck")):
    try:
        gate._entnehme(uid_a, typ)
        check(f"{name} ohne Kauf blockiert", False)
    except HTTPException as e:
        check(f"{name} ohne Kauf blockiert (402)", e.status_code == 402)
check("B/C: der Fehlversuch hat nichts ins Minus gezogen", stand(uid_a) == (0, 0, 0), str(stand(uid_a)))

# ── D/E) Bestandsnutzer behaelt sein generisches Guthaben ───────────────────
# Legacy-Zustand nachstellen: ein Konto, das VOR der Umstellung registriert
# wurde und deshalb noch einen generischen Gratis-Check besitzt.
with db.get_conn() as conn:
    cur = conn.execute(
        "INSERT INTO users (email, password_hash, abo_typ, checks_verbleibend) VALUES (?,?,'none',1)",
        ("bestand-d@test.de", "x"),
    )
    conn.commit()
    uid_d = cur.lastrowid

check("D: Bestandsnutzer hat weiterhin sein generisches Guthaben",
      stand(uid_d) == (1, 0, 0), str(stand(uid_d)))
zugriff = gate._entnehme(uid_d, "kauf")
check("D: er darf damit weiterhin genau einen Check nutzen",
      zugriff.quelle == "checks_verbleibend", zugriff.quelle)
check("E: das Legacy-Guthaben wird korrekt 1 -> 0 verbraucht",
      stand(uid_d) == (0, 0, 0), str(stand(uid_d)))
try:
    gate._entnehme(uid_d, "verkauf")
    check("E: danach ist auch er ohne Berechtigung blockiert", False)
except HTTPException as e:
    check("E: danach ist auch er ohne Berechtigung blockiert (402)", e.status_code == 402)

# Das Legacy-Guthaben gilt fuer BEIDE Check-Arten (kein Rechteverlust).
with db.get_conn() as conn:
    cur = conn.execute(
        "INSERT INTO users (email, password_hash, abo_typ, checks_verbleibend) VALUES (?,?,'none',1)",
        ("bestand-d2@test.de", "x"),
    )
    conn.commit()
    uid_d2 = cur.lastrowid
gate._entnehme(uid_d2, "verkauf")
check("D: dasselbe Legacy-Guthaben ist auch fuer den VerkaufsCheck einloesbar",
      stand(uid_d2) == (0, 0, 0), str(stand(uid_d2)))

# ── F/G) Typgebundene Guthaben bleiben getrennt ─────────────────────────────
with db.get_conn() as conn:
    cur = conn.execute(
        "INSERT INTO users (email, password_hash, abo_typ, kaufchecks_verbleibend) "
        "VALUES (?,?,'none',1)", ("typ-f@test.de", "x"))
    conn.commit()
    uid_f = cur.lastrowid
try:
    gate._entnehme(uid_f, "verkauf")
    check("F: gekaufter KaufCheck schaltet keinen VerkaufsCheck frei", False)
except HTTPException as e:
    check("F: gekaufter KaufCheck schaltet keinen VerkaufsCheck frei (402)", e.status_code == 402)
check("F: die KaufCheck-Berechtigung blieb dabei erhalten", stand(uid_f) == (0, 1, 0), str(stand(uid_f)))

with db.get_conn() as conn:
    cur = conn.execute(
        "INSERT INTO users (email, password_hash, abo_typ, verkaufschecks_verbleibend) "
        "VALUES (?,?,'none',1)", ("typ-g@test.de", "x"))
    conn.commit()
    uid_g = cur.lastrowid
try:
    gate._entnehme(uid_g, "kauf")
    check("G: gekaufter VerkaufsCheck schaltet keinen KaufCheck frei", False)
except HTTPException as e:
    check("G: gekaufter VerkaufsCheck schaltet keinen KaufCheck frei (402)", e.status_code == 402)
check("G: die VerkaufsCheck-Berechtigung blieb dabei erhalten", stand(uid_g) == (0, 0, 1), str(stand(uid_g)))

# ── H) Eine Registrierung fasst Bestandskonten nicht an ─────────────────────
with db.get_conn() as conn:
    cur = conn.execute(
        "INSERT INTO users (email, password_hash, abo_typ, checks_verbleibend, "
        "kaufchecks_verbleibend, verkaufschecks_verbleibend) VALUES (?,?,'pro',3,2,1)",
        ("bestand-h@test.de", "x"))
    conn.commit()
    uid_h = cur.lastrowid
vorher = stand(uid_h)
registriere("neu-h@test.de")
check("H: eine Registrierung veraendert Bestandsnutzer nicht",
      stand(uid_h) == vorher == (3, 2, 1), f"{vorher} -> {stand(uid_h)}")

# Und es existiert keine Migration, die vorhandene Guthaben zurueksetzt.
import pathlib  # noqa: E402
quellen = "\n".join(
    p.read_text(encoding="utf-8")
    for p in pathlib.Path("app").rglob("*.py")
)
import re  # noqa: E402
enteignung = re.search(
    r"UPDATE\s+users\s+SET\s+checks_verbleibend\s*=\s*0(?!\s*,?\s*\w*\s*WHERE\s+id)", quellen)
check("H: keine Migration setzt vorhandene generische Guthaben pauschal auf 0",
      enteignung is None, enteignung.group(0) if enteignung else "")

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER:")
    for f in FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("Alle Registrierungs-/Bestandsschutz-Tests bestanden.")
