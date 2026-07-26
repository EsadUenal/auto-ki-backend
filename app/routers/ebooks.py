"""
E-Book-Router — digitale Produkte, kein physischer Versand

Endpoints:
  GET  /ebooks                      → Katalog mit abo-rabattiertem Preis
  POST /ebooks/checkout             → Stripe-Checkout-Session (kein Adressfeld nötig)
  GET  /ebooks/bestellungen         → Kaufhistorie des Nutzers
  GET  /ebooks/{ebook_id}/download  → PDF-Download nach Kaufnachweis
"""
from __future__ import annotations

from pathlib import Path

import stripe
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

_STATIC_DIR = Path(__file__).parent.parent.parent / "static"

from app.config import FRONTEND_URL, STRIPE_SECRET_KEY
from app.database import get_conn
from app.einwilligung import (
    require_agb, require_widerruf_verzicht,
    record as record_einwilligung, ART_AGB, ART_WIDERRUF,
)
from app.routers.user_auth import get_current_user_id
from app.utf8 import UTF8JSONResponse

stripe.api_key = STRIPE_SECRET_KEY

router = APIRouter(
    prefix="/ebooks",
    tags=["ebooks"],
    default_response_class=UTF8JSONResponse,
)

_ABO_DISCOUNT_TYPEN = {"pro", "max"}


class CheckoutBody(BaseModel):
    ebook_id: str
    agb_akzeptiert: bool = False    # AGB + Datenschutz — Pflicht bei jedem Kauf
    widerruf_verzicht: bool = False # Zustimmung zur sofortigen Ausführung (digitaler Download)


def _user_abo(user_id: int) -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT abo_typ FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=401)
    return row["abo_typ"]


@router.get("")
def list_ebooks(user_id: int = Depends(get_current_user_id)):
    """Gibt alle aktiven E-Books mit dem für den Nutzer gültigen Preis zurück."""
    abo_typ = _user_abo(user_id)
    hat_rabatt = abo_typ in _ABO_DISCOUNT_TYPEN
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, titel, untertitel, beschreibung, zielgruppe, preis_normal, preis_abo "
            "FROM ebook WHERE aktiv=1 ORDER BY rowid"
        ).fetchall()
    return [
        {
            "id":           r["id"],
            "titel":        r["titel"],
            "untertitel":   r["untertitel"],
            "beschreibung": r["beschreibung"],
            "zielgruppe":   r["zielgruppe"],
            "preis":        r["preis_abo"] if hat_rabatt else r["preis_normal"],
            "preis_normal": r["preis_normal"],
            "hat_rabatt":   hat_rabatt,
        }
        for r in rows
    ]


@router.post("/checkout")
def create_ebook_checkout(
    body: CheckoutBody,
    user_id: int = Depends(get_current_user_id),
):
    """
    Erstellt eine Stripe-Checkout-Session für ein E-Book (digitales Produkt).
    Preis serverseitig berechnet, kein Adressfeld nötig.
    """
    # Pflicht-Zustimmungen serverseitig erzwingen (unabhängig vom Frontend).
    require_agb(body.agb_akzeptiert)
    require_widerruf_verzicht(body.widerruf_verzicht)
    abo_typ = _user_abo(user_id)
    hat_rabatt = abo_typ in _ABO_DISCOUNT_TYPEN

    with get_conn() as conn:
        ebook = conn.execute(
            "SELECT id, titel, preis_normal, preis_abo FROM ebook WHERE id=? AND aktiv=1",
            (body.ebook_id,),
        ).fetchone()

    if not ebook:
        raise HTTPException(
            status_code=404,
            detail={"fehler": {"code": "nicht_gefunden", "nachricht": "E-Book nicht gefunden."}},
        )

    preis = ebook["preis_abo"] if hat_rabatt else ebook["preis_normal"]
    betrag_cents = round(preis * 100)

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        mode="payment",
        line_items=[{
            "price_data": {
                "currency": "eur",
                "unit_amount": betrag_cents,
                "product_data": {
                    "name": ebook["titel"],
                    "description": "Digitales E-Book — Lieferung per E-Mail",
                },
            },
            "quantity": 1,
        }],
        success_url=f"{FRONTEND_URL}/ebooks?payment=success&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{FRONTEND_URL}/ebooks?payment=cancelled",
        metadata={
            "typ":           "ebook",
            "user_id":       str(user_id),
            "ebook_id":      body.ebook_id,
            "preis_bezahlt": str(preis),
        },
    )

    # Nachweis der Zustimmungen für diesen Kauf festhalten.
    record_einwilligung(user_id, ART_AGB, f"ebook:{body.ebook_id}")
    record_einwilligung(user_id, ART_WIDERRUF, f"ebook:{body.ebook_id}")

    return {"url": session.url}



@router.get("/bestellungen")
def list_ebook_bestellungen(user_id: int = Depends(get_current_user_id)):
    """Gibt alle E-Book-Käufe des eingeloggten Nutzers zurück."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT b.id, b.ebook_id, e.titel, e.untertitel, e.zielgruppe,
                      b.preis_bezahlt, b.status, b.paid_at, b.created_at
               FROM ebook_bestellung b
               JOIN ebook e ON e.id = b.ebook_id
               WHERE b.user_id = ?
               ORDER BY b.created_at DESC""",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/{ebook_id}/download")
def download_ebook(ebook_id: str, user_id: int = Depends(get_current_user_id)):
    """Prüft Kaufnachweis und sendet die PDF als Datei-Download."""
    with get_conn() as conn:
        kauf = conn.execute(
            "SELECT 1 FROM ebook_bestellung WHERE user_id=? AND ebook_id=? AND status='bezahlt'",
            (user_id, ebook_id),
        ).fetchone()
    if not kauf:
        raise HTTPException(
            status_code=403,
            detail={"fehler": {"code": "kein_kauf", "nachricht": "Kein gültiger Kauf für dieses E-Book."}},
        )

    with get_conn() as conn:
        ebook = conn.execute("SELECT titel FROM ebook WHERE id=?", (ebook_id,)).fetchone()
    if not ebook:
        raise HTTPException(status_code=404)

    ebook_dir = _STATIC_DIR / "ebooks"
    pdf_path = ebook_dir / f"{ebook_id}.pdf"
    if not pdf_path.exists():
        candidates = sorted(ebook_dir.glob(f"*{ebook_id}*.pdf"))
        if candidates:
            pdf_path = candidates[0]
    if not pdf_path.exists():
        raise HTTPException(
            status_code=503,
            detail={"fehler": {"code": "datei_fehlt", "nachricht": "Datei wird gerade vorbereitet. Bitte versuche es in wenigen Minuten erneut."}},
        )

    sicherer_titel = "".join(c if c.isalnum() or c in "-_ " else "_" for c in ebook["titel"])
    filename = f"Vira_{sicherer_titel}.pdf"
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
