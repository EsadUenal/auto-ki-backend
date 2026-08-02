from __future__ import annotations

"""
Zentrale Berechtigungs-Ableitungen (eine Quelle der Wahrheit).

Bewusst OHNE Imports aus Routern/DB — damit sowohl der Auth-Router als auch der
Dealer-Gate diese Regeln nutzen können, ohne Zirkelimport.
"""


def has_dealer_access(abo_typ: str | None, ist_haendler: object) -> bool:
    """Effektive VIRA-Dealer-Berechtigung.

    Der MAX-Tarif IST der Händlertarif -> abo_typ == "max" schaltet Dealer frei.
    `ist_haendler` bleibt als MANUELLER DB-Override (Testaccounts/Support/Sonder-
    freischaltungen) erhalten. Wird bewusst bei JEDER Anfrage aus dem aktuellen
    Account abgeleitet — nach einer MAX-Kündigung (abo_typ != "max") entfällt der
    Zugriff automatisch, sofern kein manueller Override gesetzt ist.
    """
    return abo_typ == "max" or bool(ist_haendler)
