"""
Phase 5 — VIRA Dealer Router (/dealer/*).

Alle Endpunkte: Auth (Cookie) + Dealer-Berechtigung (require_dealer) + Ownership
(jede Query ist auf user_id eingeschränkt). Ein Nutzer sieht/ändert ausschließlich
seine eigenen Fahrzeuge; fremde IDs -> 404 (keine Existenz-Preisgabe).
"""
from __future__ import annotations

import json
import logging
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from app.database import get_conn
from app.dealer import build_dealer_vehicle, require_dealer
from app.models import (
    DealerSummary, DealerVehicle, DealerVehicleCreate, DealerVehicleUpdate,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/dealer", tags=["dealer"])

# Fahrzeug-Kernspalten (für SELECT/Update-Whitelist).
_VEHICLE_COLS = (
    "id, user_id, kaufcheck_id, verkaufscheck_id, marke, modell, baureihe, motor, "
    "baujahr, kilometerstand, status, einkaufspreis, nebenkosten, geplanter_verkaufspreis, "
    "tatsaechlicher_verkaufspreis, interne_notiz, created_at, updated_at, sold_at"
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_own(conn, vehicle_id: int, user_id: int) -> dict:
    row = conn.execute(
        f"SELECT {_VEHICLE_COLS} FROM dealer_vehicle WHERE id=?", (vehicle_id,)
    ).fetchone()
    # 404 auch bei fremdem Fahrzeug -> keine Existenz-Preisgabe an Dritte.
    if row is None or row["user_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"fehler": {"code": "not_found", "nachricht": "Fahrzeug nicht gefunden."}},
        )
    return dict(row)


def _load_check_ergebnis(conn, check_id: int | None, user_id: int, typ: str) -> dict | None:
    """Lädt das gespeicherte Ergebnis eines verknüpften Checks (nur eigener, passender
    Typ). Nichts neu berechnen — es werden die persistierten Analysedaten gelesen."""
    if not check_id:
        return None
    row = conn.execute(
        "SELECT ergebnis FROM checks WHERE id=? AND user_id=? AND typ=?",
        (check_id, user_id, typ),
    ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["ergebnis"])
    except (json.JSONDecodeError, TypeError):
        return None


def _to_response(conn, v: dict, user_id: int) -> DealerVehicle:
    kauf = _load_check_ergebnis(conn, v.get("kaufcheck_id"), user_id, "kauf")
    verkauf = _load_check_ergebnis(conn, v.get("verkaufscheck_id"), user_id, "verkauf")
    return build_dealer_vehicle(v, kauf, verkauf)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/summary", response_model=DealerSummary)
def dealer_summary(user_id: int = Depends(require_dealer)):
    """Dashboard-Kennzahlen. Kapital/Margen nur wenn Daten vorhanden (sonst None)."""
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT {_VEHICLE_COLS} FROM dealer_vehicle WHERE user_id=?", (user_id,)
        ).fetchall()]
        fahrzeuge = [_to_response(conn, v, user_id) for v in rows]

    by_status = {s: 0 for s in ("beobachtung", "einkauf_geplant", "im_bestand", "verkauft")}
    for v in rows:
        by_status[v["status"]] = by_status.get(v["status"], 0) + 1

    # Gebundenes Kapital = Gesamteinsatz der Fahrzeuge im Bestand (nur berechenbare).
    kapital_werte = [f.finanzen.gesamteinsatz for f, v in zip(fahrzeuge, rows)
                     if v["status"] == "im_bestand" and f.finanzen.gesamteinsatz is not None]
    # Geplante Bruttomarge = mögliche Marge offener Fahrzeuge (nicht verkauft).
    marge_werte = [f.finanzen.moegliche_bruttomarge for f, v in zip(fahrzeuge, rows)
                   if v["status"] != "verkauft" and f.finanzen.moegliche_bruttomarge is not None]
    real_werte = [f.finanzen.realisierte_bruttomarge for f, v in zip(fahrzeuge, rows)
                  if v["status"] == "verkauft" and f.finanzen.realisierte_bruttomarge is not None]

    return DealerSummary(
        fahrzeuge_gesamt=len(rows),
        beobachtung=by_status["beobachtung"],
        einkauf_geplant=by_status["einkauf_geplant"],
        im_bestand=by_status["im_bestand"],
        verkauft=by_status["verkauft"],
        gebundenes_kapital=sum(kapital_werte) if kapital_werte else None,
        geplante_bruttomarge=sum(marge_werte) if marge_werte else None,
        realisierte_bruttomarge=sum(real_werte) if real_werte else None,
        braucht_aufmerksamkeit=sum(1 for f in fahrzeuge if f.braucht_aufmerksamkeit),
    )


@router.get("/vehicles", response_model=list[DealerVehicle])
def list_vehicles(user_id: int = Depends(require_dealer)):
    """Alle Fahrzeuge des Händlers, neueste zuerst."""
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT {_VEHICLE_COLS} FROM dealer_vehicle WHERE user_id=? ORDER BY created_at DESC, id DESC",
            (user_id,),
        ).fetchall()]
        return [_to_response(conn, v, user_id) for v in rows]


@router.post("/vehicles", response_model=DealerVehicle, status_code=201)
def create_vehicle(body: DealerVehicleCreate, user_id: int = Depends(require_dealer)):
    """Manuelles Fahrzeug anlegen (ohne vorherigen Check)."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO dealer_vehicle "
            "(user_id, marke, modell, baureihe, motor, baujahr, kilometerstand, status, "
            " einkaufspreis, nebenkosten, geplanter_verkaufspreis, interne_notiz) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, body.marke, body.modell, body.baureihe, body.motor, body.baujahr,
             body.kilometerstand, body.status, body.einkaufspreis, body.nebenkosten,
             body.geplanter_verkaufspreis, body.interne_notiz),
        )
        conn.commit()
        v = _row_own(conn, cur.lastrowid, user_id)
        return _to_response(conn, v, user_id)


@router.post("/vehicles/from-check/{check_id}", response_model=DealerVehicle)
def create_from_check(check_id: int, user_id: int = Depends(require_dealer)):
    """Fahrzeug aus einem gespeicherten KAUFCHECK übernehmen (Status 'beobachtung').

    Idempotent: existiert bereits ein Fahrzeug für diesen Check, wird es zurückgegeben
    (200, kein Duplikat). Sonst wird es angelegt (201) — Fahrzeugdaten aus dem Check
    übernommen (keine Doppeleingabe).
    """
    with get_conn() as conn:
        check = conn.execute(
            "SELECT id, user_id, typ, eingabe, ergebnis FROM checks WHERE id=?", (check_id,)
        ).fetchone()
        if check is None or check["user_id"] != user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"fehler": {"code": "not_found", "nachricht": "Check nicht gefunden."}},
            )
        if check["typ"] != "kauf":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"fehler": {"code": "kein_kaufcheck",
                                   "nachricht": "Nur Kaufchecks können übernommen werden."}},
            )

        # Bereits verknüpft? -> vorhandenes Fahrzeug zurückgeben (kein Duplikat).
        existing = conn.execute(
            f"SELECT {_VEHICLE_COLS} FROM dealer_vehicle WHERE user_id=? AND kaufcheck_id=?",
            (user_id, check_id),
        ).fetchone()
        if existing:
            return _to_response(conn, dict(existing), user_id)

        eingabe = _safe_json(check["eingabe"])
        ergebnis = _safe_json(check["ergebnis"])
        try:
            cur = conn.execute(
                "INSERT INTO dealer_vehicle "
                "(user_id, kaufcheck_id, marke, modell, baureihe, motor, baujahr, kilometerstand, status) "
                "VALUES (?,?,?,?,?,?,?,?, 'beobachtung')",
                (user_id, check_id,
                 eingabe.get("marke"), eingabe.get("modell"),
                 ergebnis.get("baureihe_erkannt"),
                 eingabe.get("motor") or ergebnis.get("motor_erkannt"),
                 eingabe.get("baujahr"), eingabe.get("kilometerstand")),
            )
            conn.commit()
            v = _row_own(conn, cur.lastrowid, user_id)
        except sqlite3.IntegrityError:
            # Race: paralleler from-check hat die Verknüpfung soeben angelegt.
            existing = conn.execute(
                f"SELECT {_VEHICLE_COLS} FROM dealer_vehicle WHERE user_id=? AND kaufcheck_id=?",
                (user_id, check_id),
            ).fetchone()
            if existing:
                return _to_response(conn, dict(existing), user_id)
            raise
        return _to_response(conn, v, user_id)


@router.get("/vehicles/{vehicle_id}", response_model=DealerVehicle)
def get_vehicle(vehicle_id: int, user_id: int = Depends(require_dealer)):
    with get_conn() as conn:
        v = _row_own(conn, vehicle_id, user_id)
        return _to_response(conn, v, user_id)


@router.patch("/vehicles/{vehicle_id}", response_model=DealerVehicle)
def update_vehicle(vehicle_id: int, body: DealerVehicleUpdate, user_id: int = Depends(require_dealer)):
    """Teilweises Update. status='verkauft' setzt sold_at (falls noch nicht gesetzt);
    ein Wechsel zurück löscht sold_at wieder."""
    felder = body.model_dump(exclude_unset=True)

    with get_conn() as conn:
        v = _row_own(conn, vehicle_id, user_id)

        # verkaufscheck_id nur akzeptieren, wenn es ein eigener Verkaufscheck ist.
        if "verkaufscheck_id" in felder and felder["verkaufscheck_id"] is not None:
            ok = conn.execute(
                "SELECT 1 FROM checks WHERE id=? AND user_id=? AND typ='verkauf'",
                (felder["verkaufscheck_id"], user_id),
            ).fetchone()
            if not ok:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"fehler": {"code": "kein_verkaufscheck",
                                       "nachricht": "Verkaufscheck nicht gefunden."}},
                )

        sets: list[str] = []
        params: list = []
        for key, val in felder.items():
            sets.append(f"{key} = ?")
            params.append(val)

        # sold_at automatisch pflegen.
        neuer_status = felder.get("status", v["status"])
        if "status" in felder:
            if neuer_status == "verkauft" and not v.get("sold_at"):
                sets.append("sold_at = CURRENT_TIMESTAMP")
            elif neuer_status != "verkauft":
                sets.append("sold_at = NULL")

        sets.append("updated_at = CURRENT_TIMESTAMP")

        if sets:
            params.extend([vehicle_id, user_id])
            conn.execute(
                f"UPDATE dealer_vehicle SET {', '.join(sets)} WHERE id=? AND user_id=?",
                params,
            )
            conn.commit()

        v = _row_own(conn, vehicle_id, user_id)
        return _to_response(conn, v, user_id)


@router.delete("/vehicles/{vehicle_id}", status_code=204)
def delete_vehicle(vehicle_id: int, user_id: int = Depends(require_dealer)):
    with get_conn() as conn:
        _row_own(conn, vehicle_id, user_id)   # Existenz + Ownership (404)
        conn.execute("DELETE FROM dealer_vehicle WHERE id=? AND user_id=?", (vehicle_id, user_id))
        conn.commit()


def _safe_json(raw: str | None) -> dict:
    try:
        d = json.loads(raw) if raw else {}
        return d if isinstance(d, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}
