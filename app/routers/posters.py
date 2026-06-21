"""
Poster-Router — Etappe 2 (Kauf + Stripe)

Endpoints:
  GET  /posters                    → Katalog mit abo-rabattiertem Preis
  POST /posters/checkout           → Stripe-Checkout-Session erstellen
  GET  /posters/bestellungen       → Bestellhistorie des Nutzers
  GET  /posters/bestellungen/{id}  → Einzelne Bestellung
  GET  /posters/adresse            → Gespeicherte Lieferadresse laden
  PUT  /posters/adresse            → Lieferadresse speichern / aktualisieren

Sicherheit:
  - Preisberechnung ausschließlich serverseitig anhand abo_typ in DB
  - Bestellung wird erst nach verifiziertem Stripe-Webhook angelegt (in payments.py)
  - Adresse wird als Snapshot in poster_bestellung eingebettet (unveränderlich)
"""
from __future__ import annotations

import stripe
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from app.config import FRONTEND_URL, STRIPE_SECRET_KEY
from app.database import get_conn
from app.routers.user_auth import get_current_user_id
from app.utf8 import UTF8JSONResponse

stripe.api_key = STRIPE_SECRET_KEY

router = APIRouter(
    prefix="/posters",
    tags=["posters"],
    default_response_class=UTF8JSONResponse,
)

_ABO_DISCOUNT_TYPEN = {"pro", "max"}


# ── Schemas ────────────────────────────────────────────────────────────────────

class AdresseBody(BaseModel):
    name:    str
    strasse: str
    plz:     str
    ort:     str
    land:    str = "DE"

    @field_validator("name", "strasse", "plz", "ort")
    @classmethod
    def nicht_leer(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Darf nicht leer sein.")
        return v.strip()

    @field_validator("land")
    @classmethod
    def land_strip(cls, v: str) -> str:
        return v.strip() or "DE"


class CheckoutBody(BaseModel):
    poster_id:        str
    adresse:          AdresseBody
    adresse_speichern: bool = False


# ── Hilfsfunktion ─────────────────────────────────────────────────────────────

def _user_abo(user_id: int) -> str:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT abo_typ FROM users WHERE id=?", (user_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=401)
    return row["abo_typ"]


def _poster_preis(poster: dict, abo_typ: str) -> float:
    if abo_typ in _ABO_DISCOUNT_TYPEN:
        return poster["preis_abo"]
    return poster["preis_normal"]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("")
def list_posters(user_id: int = Depends(get_current_user_id)):
    """Gibt alle aktiven Poster mit dem für den Nutzer gültigen Preis zurück."""
    abo_typ = _user_abo(user_id)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, titel, beschreibung, preis_normal, preis_abo, bildpfad "
            "FROM poster WHERE aktiv=1 ORDER BY rowid"
        ).fetchall()
    result = []
    for r in rows:
        hat_rabatt = abo_typ in _ABO_DISCOUNT_TYPEN
        result.append({
            "id":           r["id"],
            "titel":        r["titel"],
            "beschreibung": r["beschreibung"],
            "bildpfad":     r["bildpfad"],
            "preis":        r["preis_abo"] if hat_rabatt else r["preis_normal"],
            "preis_normal": r["preis_normal"],
            "hat_rabatt":   hat_rabatt,
        })
    return result


@router.post("/checkout")
def create_poster_checkout(
    body: CheckoutBody,
    user_id: int = Depends(get_current_user_id),
):
    """
    Erstellt eine Stripe-Checkout-Session für ein Poster.
    Der Preis wird serverseitig anhand des abo_typ des Nutzers bestimmt.
    Die Bestellung wird erst nach Webhook-Bestätigung angelegt.
    """
    abo_typ = _user_abo(user_id)

    with get_conn() as conn:
        poster = conn.execute(
            "SELECT id, titel, preis_normal, preis_abo FROM poster WHERE id=? AND aktiv=1",
            (body.poster_id,),
        ).fetchone()

    if not poster:
        raise HTTPException(
            status_code=404,
            detail={"fehler": {"code": "nicht_gefunden", "nachricht": "Poster nicht gefunden."}},
        )

    preis = _poster_preis(dict(poster), abo_typ)
    betrag_cents = round(preis * 100)

    if body.adresse_speichern:
        a = body.adresse
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO gespeicherte_adresse (user_id, name, strasse, plz, ort, land, updated_at)
                   VALUES (?,?,?,?,?,?, CURRENT_TIMESTAMP)
                   ON CONFLICT(user_id) DO UPDATE SET
                     name=excluded.name, strasse=excluded.strasse, plz=excluded.plz,
                     ort=excluded.ort, land=excluded.land, updated_at=CURRENT_TIMESTAMP""",
                (user_id, a.name, a.strasse, a.plz, a.ort, a.land),
            )
            conn.commit()

    a = body.adresse
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        mode="payment",
        line_items=[{
            "price_data": {
                "currency": "eur",
                "unit_amount": betrag_cents,
                "product_data": {"name": poster["titel"]},
            },
            "quantity": 1,
        }],
        success_url=f"{FRONTEND_URL}/poster?payment=success",
        cancel_url=f"{FRONTEND_URL}/poster?payment=cancelled",
        metadata={
            "typ":             "poster",
            "user_id":         str(user_id),
            "poster_id":       body.poster_id,
            "preis_bezahlt":   str(preis),
            "adresse_name":    a.name,
            "adresse_strasse": a.strasse,
            "adresse_plz":     a.plz,
            "adresse_ort":     a.ort,
            "adresse_land":    a.land,
        },
    )

    return {"url": session.url}


@router.get("/bestellungen")
def list_bestellungen(user_id: int = Depends(get_current_user_id)):
    """Gibt alle Bestellungen des eingeloggten Nutzers zurück."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT b.id, b.poster_id, p.titel AS poster_titel, p.bildpfad,
                      b.preis_bezahlt, b.status, b.paid_at, b.created_at,
                      b.adresse_name, b.adresse_strasse, b.adresse_plz,
                      b.adresse_ort, b.adresse_land
               FROM poster_bestellung b
               JOIN poster p ON p.id = b.poster_id
               WHERE b.user_id = ?
               ORDER BY b.created_at DESC""",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/bestellungen/{bestellung_id}")
def get_bestellung(
    bestellung_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Gibt eine einzelne Bestellung zurück (nur eigene)."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT b.id, b.poster_id, p.titel AS poster_titel, p.bildpfad,
                      b.preis_bezahlt, b.status, b.stripe_session_id,
                      b.stripe_payment_intent_id, b.paid_at, b.created_at,
                      b.adresse_name, b.adresse_strasse, b.adresse_plz,
                      b.adresse_ort, b.adresse_land
               FROM poster_bestellung b
               JOIN poster p ON p.id = b.poster_id
               WHERE b.id=? AND b.user_id=?""",
            (bestellung_id, user_id),
        ).fetchone()
    if not row:
        raise HTTPException(
            status_code=404,
            detail={"fehler": {"code": "nicht_gefunden", "nachricht": "Bestellung nicht gefunden."}},
        )
    return dict(row)


@router.get("/adresse")
def get_adresse(user_id: int = Depends(get_current_user_id)):
    """Gibt die gespeicherte Lieferadresse des Nutzers zurück (oder null)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT name, strasse, plz, ort, land FROM gespeicherte_adresse WHERE user_id=?",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return dict(row)


@router.put("/adresse")
def save_adresse(
    body: AdresseBody,
    user_id: int = Depends(get_current_user_id),
):
    """Speichert oder aktualisiert die Lieferadresse des Nutzers."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO gespeicherte_adresse (user_id, name, strasse, plz, ort, land, updated_at)
               VALUES (?,?,?,?,?,?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id) DO UPDATE SET
                 name=excluded.name, strasse=excluded.strasse, plz=excluded.plz,
                 ort=excluded.ort, land=excluded.land, updated_at=CURRENT_TIMESTAMP""",
            (user_id, body.name, body.strasse, body.plz, body.ort, body.land),
        )
        conn.commit()
    return {"ok": True}
