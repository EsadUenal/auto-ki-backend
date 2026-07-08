"""
Einwilligungen — serverseitige Erzwingung + Nachweis.

Zwei Zustimmungen:
  - agb_datenschutz : Zustimmung zu AGB & Datenschutzerklaerung
  - widerruf_verzicht: ausdrueckliche Zustimmung zur sofortigen Ausfuehrung
                       (Verlust des Widerrufsrechts bei digitalen Kaeufen)

require_*() erzwingen die Zustimmung (HTTP 400 bei Fehlen) — unabhaengig vom
Frontend. record() schreibt einen Nachweis-Eintrag mit Zeitstempel.
"""
from __future__ import annotations

from fastapi import HTTPException

from app.database import get_conn

ART_AGB = "agb_datenschutz"
ART_WIDERRUF = "widerruf_verzicht"


def require_agb(agb_akzeptiert: bool) -> None:
    if not agb_akzeptiert:
        raise HTTPException(
            status_code=400,
            detail={"fehler": {"code": "agb_erforderlich",
                               "nachricht": "Bitte akzeptiere die AGB und die Datenschutzerklärung, um fortzufahren."}},
        )


def require_widerruf_verzicht(widerruf_verzicht: bool) -> None:
    if not widerruf_verzicht:
        raise HTTPException(
            status_code=400,
            detail={"fehler": {"code": "widerruf_erforderlich",
                               "nachricht": "Bitte bestätige den Hinweis zum Widerrufsrecht, um fortzufahren."}},
        )


def record(user_id: int, art: str, kontext: str) -> None:
    """Schreibt einen Nachweis-Eintrag. Fehler hier dürfen den Kauf-/Registrierungs-
    fluss nicht abbrechen (der Nachweis ist sekundär zur eigentlichen Aktion)."""
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO einwilligung (user_id, art, kontext) VALUES (?,?,?)",
                (user_id, art, kontext),
            )
            conn.commit()
    except Exception:
        pass
