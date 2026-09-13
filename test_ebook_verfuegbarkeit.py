"""
Test: E-Book-Verfügbarkeit und Auslieferung (app/routers/ebooks.py)

Ohne Netzwerk, ohne echtes Stripe (gemockt), gegen eine TEMPORÄRE DB:

  1) Ein E-Book ohne PDF (angekündigter Titel) ist NICHT kaufbar (409),
     es wird keine Stripe-Session angelegt.
  2) Ein E-Book mit PDF ist kaufbar; die Stripe-Produktbeschreibung verspricht
     keine E-Mail-Lieferung (es gibt keinen E-Mail-Versand).
  3) Download: Käufer bekommt genau die richtige PDF, Nicht-Käufer 403.

Ausführen:  python test_ebook_verfuegbarkeit.py
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="enfal_ebook_test_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "test.db")

import app.database as db            # noqa: E402
db.ensure_tables()

import app.routers.ebooks as eb      # noqa: E402
from fastapi import HTTPException    # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


def neuer_user(email: str) -> int:
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, abo_typ, checks_verbleibend) VALUES (?,?,'none',0)",
            (email, "x"),
        )
        conn.commit()
        return cur.lastrowid


class _FakeSession:
    url = "https://checkout.stripe.test/session"


AUFRUFE: list[dict] = []


def _fake_create(**kwargs):
    AUFRUFE.append(kwargs)
    return _FakeSession()


eb.stripe.checkout.Session.create = _fake_create

kaeufer = neuer_user("kaeufer@example.org")
fremder = neuer_user("fremd@example.org")

# ── 1) Angekündigter Titel ohne PDF ─────────────────────────────────────────
with db.get_conn() as conn:
    aktive = [r["id"] for r in conn.execute("SELECT id FROM ebook WHERE aktiv=1")]
ohne_pdf = [e for e in aktive if eb._pdf_pfad(e) is None]
mit_pdf = [e for e in aktive if eb._pdf_pfad(e) is not None]
check("Seed enthält mindestens ein E-Book ohne PDF (angekündigt)", len(ohne_pdf) >= 1)
check("Seed enthält auslieferbare E-Books", len(mit_pdf) >= 1)

for eid in ohne_pdf:
    AUFRUFE.clear()
    try:
        eb.create_ebook_checkout(eb.CheckoutBody(ebook_id=eid, agb_akzeptiert=True, widerruf_verzicht=True),
                                 user_id=kaeufer)
        status = 200
    except HTTPException as exc:
        status = exc.status_code
    check(f"{eid}: Checkout ohne PDF -> 409", status == 409)
    check(f"{eid}: keine Stripe-Session angelegt", AUFRUFE == [])

# ── 2) Kaufbarer Titel ──────────────────────────────────────────────────────
AUFRUFE.clear()
res = eb.create_ebook_checkout(eb.CheckoutBody(ebook_id=mit_pdf[0], agb_akzeptiert=True, widerruf_verzicht=True),
                               user_id=kaeufer)
check("Checkout mit PDF liefert Stripe-URL", res.get("url") == _FakeSession.url)
beschreibung = AUFRUFE[0]["line_items"][0]["price_data"]["product_data"]["description"]
check("Stripe-Beschreibung verspricht keine E-Mail-Lieferung", "mail" not in beschreibung.lower())

# ── 3) Download ─────────────────────────────────────────────────────────────
with db.get_conn() as conn:
    conn.execute(
        "INSERT INTO ebook_bestellung (user_id, ebook_id, preis_bezahlt, stripe_session_id, status, paid_at) "
        "VALUES (?,?,?,?, 'bezahlt', CURRENT_TIMESTAMP)",
        (kaeufer, mit_pdf[0], 1.0, "cs_test_ebook_1"),
    )
    conn.commit()

antwort = eb.download_ebook(mit_pdf[0], user_id=kaeufer)
check("Käufer: Download zeigt auf die richtige PDF", os.path.samefile(antwort.path, eb._pdf_pfad(mit_pdf[0])))
check("Käufer: Content-Type application/pdf", antwort.media_type == "application/pdf")
with open(antwort.path, "rb") as f:
    check("Käufer: Datei ist eine PDF", f.read(5) == b"%PDF-")

try:
    eb.download_ebook(mit_pdf[0], user_id=fremder)
    status = 200
except HTTPException as exc:
    status = exc.status_code
check("Nicht-Käufer: Download -> 403", status == 403)

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER:")
    for f in FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("Alle E-Book-Verfügbarkeitstests bestanden.")
