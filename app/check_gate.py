"""
Check-Gate — Consumer Pricing V1 (getrennte Berechtigungen je Check-Art)

FastAPI-Dependency: stellt sicher, dass der eingeloggte Nutzer eine passende
Berechtigung besitzt, bevor ein Kauf- oder Verkaufs-Check ausgeführt wird.

WARUM TYPGEBUNDEN
-----------------
Bis Consumer Pricing V1 gab es EIN generisches Kontingent (`checks_verbleibend`)
für beide Check-Arten. Mit zwei getrennt bepreisten Produkten (KaufCheck 9,99 €,
VerkaufsCheck 7,99 €) wäre das falsch: ein gekaufter KaufCheck hätte einen
VerkaufsCheck mit freigeschaltet — der Kunde hätte das günstigere Produkt zum
teureren Preis oder umgekehrt bekommen. Neu gekaufte Checks landen deshalb in
eigenen Spalten (`kaufchecks_verbleibend`, `verkaufschecks_verbleibend`), die
sich gegenseitig NICHT freischalten.

VERBRAUCHSREIHENFOLGE (bewusst in dieser Reihenfolge)
-----------------------------------------------------
  1. MAX-Abo            → unbegrenzt, kein Dekrement
  2. typgebundenes Kontingent der angefragten Check-Art
  3. generisches Legacy-Kontingent `checks_verbleibend`

Schritt 3 ist der Bestandsschutz: Registrierungs-Gratischeck, Abo-Kontingente
(light/pro) und frühere Einzelkäufe liegen alle im generischen Topf. Diese
Ansprüche bleiben für BEIDE Check-Arten gültig — genau wie vor V1. Sie werden
weder gelöscht noch stillschweigend in eines der neuen Produkte umgewandelt.
Neu ist ausschließlich, dass ab jetzt gekaufte Checks typgebunden sind.

Jede Entnahme ist ein atomares `UPDATE ... WHERE <spalte> > 0` — zwei parallele
Requests auf dasselbe letzte Kontingent können nicht beide durchkommen, weil
genau eines der beiden UPDATEs rowcount 0 liefert.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import Depends, HTTPException

from app.database import get_conn
from app.routers.user_auth import get_current_user_id

log = logging.getLogger(__name__)

# Check-Art -> Spalte mit dem typgebundenen Kontingent.
_SPALTE = {
    "kauf":    "kaufchecks_verbleibend",
    "verkauf": "verkaufschecks_verbleibend",
}

# Generisches Legacy-Kontingent (Registrierung, Abo, Alt-Einzelkäufe).
_LEGACY_SPALTE = "checks_verbleibend"

# Quelle "unbegrenzt": MAX-Abo, es wurde nichts abgezogen.
QUELLE_UNBEGRENZT = "unbegrenzt"


@dataclass(frozen=True)
class CheckZugriff:
    """Ergebnis eines erfolgreichen Gate-Durchlaufs.

    `quelle` hält fest, WORAUS das Kontingent entnommen wurde. Nur damit kann
    eine spätere Rückerstattung exakt denselben Topf wieder auffüllen — ein
    fehlgeschlagener VerkaufsCheck darf keinen KaufCheck-Anspruch erzeugen.
    """
    user_id: int
    typ: str      # "kauf" | "verkauf"
    quelle: str   # Spaltenname oder QUELLE_UNBEGRENZT


def _nachricht(typ: str) -> str:
    return (
        "Für diesen KaufCheck ist keine Berechtigung vorhanden."
        if typ == "kauf"
        else "Für diesen VerkaufsCheck ist keine Berechtigung vorhanden."
    )


def _entnehme(user_id: int, typ: str) -> CheckZugriff:
    """Prüft und entnimmt EIN Kontingent. Wirft 401/402, wenn nichts verfügbar."""
    spalte = _SPALTE[typ]

    with get_conn() as conn:
        user = conn.execute("SELECT abo_typ FROM users WHERE id=?", (user_id,)).fetchone()

    if not user:
        raise HTTPException(
            status_code=401,
            detail={"fehler": {"code": "unauthorized", "nachricht": "Nutzer nicht gefunden."}},
        )

    if user["abo_typ"] == "max":
        return CheckZugriff(user_id=user_id, typ=typ, quelle=QUELLE_UNBEGRENZT)

    # 1. Typgebundenes Kontingent, 2. generischer Legacy-Topf — beide atomar.
    for kandidat in (spalte, _LEGACY_SPALTE):
        with get_conn() as conn:
            result = conn.execute(
                f"UPDATE users SET {kandidat} = {kandidat} - 1 "
                f"WHERE id = ? AND {kandidat} > 0",
                (user_id,),
            )
            conn.commit()
        if result.rowcount == 1:
            return CheckZugriff(user_id=user_id, typ=typ, quelle=kandidat)

    raise HTTPException(
        status_code=402,
        detail={
            "fehler": {
                "code": "payment_required",
                "produkt": "kaufcheck" if typ == "kauf" else "verkaufscheck",
                "nachricht": _nachricht(typ),
            }
        },
    )


def require_kaufcheck_access(user_id: int = Depends(get_current_user_id)) -> CheckZugriff:
    """Dependency für POST /kaufcheck — entnimmt genau eine KaufCheck-Berechtigung."""
    return _entnehme(user_id, "kauf")


def require_verkaufscheck_access(user_id: int = Depends(get_current_user_id)) -> CheckZugriff:
    """Dependency für POST /verkaufscheck — entnimmt genau eine VerkaufsCheck-Berechtigung."""
    return _entnehme(user_id, "verkauf")


def refund_check_credit(zugriff: CheckZugriff) -> None:
    """
    Gibt EIN entnommenes Kontingent in exakt den Topf zurück, aus dem es kam.

    Aufrufer sind die Check-Router, wenn der Nutzer trotz gültiger Berechtigung
    keine verwertbare Analyse erhalten hat (Gemini-Totalausfall, unzureichende
    Recherche). Der Anspruch darf dabei nicht verfallen — bezahlt ist bezahlt.

    MAX-Abo (`QUELLE_UNBEGRENZT`) hatte nie ein Dekrement; hier bewusst NICHT
    erhöhen, sonst würde ein unbegrenztes Kontingent in ein gezähltes mutieren.

    Best-effort: ein Fehler hier darf die eigentliche Fehlerantwort an den
    Nutzer nicht verhindern, nur geloggt werden.
    """
    if zugriff.quelle == QUELLE_UNBEGRENZT:
        return
    if zugriff.quelle not in (*_SPALTE.values(), _LEGACY_SPALTE):
        log.error("Unbekannte Kontingent-Quelle %r — keine Rückerstattung", zugriff.quelle)
        return
    try:
        with get_conn() as conn:
            conn.execute(
                f"UPDATE users SET {zugriff.quelle} = {zugriff.quelle} + 1 WHERE id=?",
                (zugriff.user_id,),
            )
            conn.commit()
    except Exception:
        log.exception(
            "Rückerstattung (%s) für user_id=%s fehlgeschlagen",
            zugriff.quelle, zugriff.user_id,
        )


def gutschrift(user_id: int, produkt: str, anzahl: int = 1) -> None:
    """Schreibt eine gekaufte Check-Berechtigung gut (Aufrufer: Stripe-Webhook).

    `produkt` ist der serverseitig bestimmte Produktschlüssel — niemals ein vom
    Client gesendeter Wert. Unbekannte Produkte werden NICHT gutgeschrieben.
    """
    typ = {"kaufcheck": "kauf", "verkaufscheck": "verkauf"}.get(produkt)
    if typ is None:
        raise ValueError(f"Unbekanntes Check-Produkt: {produkt!r}")
    spalte = _SPALTE[typ]
    with get_conn() as conn:
        conn.execute(
            f"UPDATE users SET {spalte} = {spalte} + ? WHERE id=?",
            (anzahl, user_id),
        )
        conn.commit()


def kontingente(user_id: int) -> dict:
    """Liest die Kontingente eines Nutzers (für /payments/status und /auth/me)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT abo_typ, checks_verbleibend, kaufchecks_verbleibend, "
            "verkaufschecks_verbleibend FROM users WHERE id=?",
            (user_id,),
        ).fetchone()
    if not row:
        return {}
    unbegrenzt = row["abo_typ"] == "max"
    return {
        "unbegrenzt": unbegrenzt,
        "kaufchecks_verbleibend": row["kaufchecks_verbleibend"],
        "verkaufschecks_verbleibend": row["verkaufschecks_verbleibend"],
        "checks_verbleibend": row["checks_verbleibend"],
    }
