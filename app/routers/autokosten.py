from __future__ import annotations

"""
Autokosten-Router — nur die Kraftstoff-Referenz.

Der Rechner selbst läuft VOLLSTÄNDIG im Frontend (deterministisch, ohne Netz,
siehe `src/components/autokosten/logic.ts`). Das Backend liefert hier genau eine
Sache: den amtlichen deutschen Wochen-Referenzpreis für Benzin und Diesel, damit
im Formular keine jahrealten Platzhalter stehen (app/fuel_referenz.py).

  GET /api/v1/autokosten/kraftstoff-referenz

Ohne Login und ohne Kontingent — wie `/autofinder` nur mit dem öffentlichen
Consumer-API-Key. Der Rechner ist ein kostenloses Akquise-Werkzeug.

Der Endpunkt kann NIE den Rechner blockieren: bei jedem Ausfall der amtlichen
Quelle kommt eine gültige Antwort mit `status != "ok"` und `preis: null`, und das
Frontend lässt das Feld dann leer und editierbar.

KEIN Gemini, KEIN Tavily, KEIN CarAPI.
"""

from fastapi import APIRouter, Request

from app.auth import verify_api_key
from app.fuel_referenz import als_dict, hole_referenzen
from app.utf8 import UTF8JSONResponse

router = APIRouter(default_response_class=UTF8JSONResponse)


@router.get(
    "/autokosten/kraftstoff-referenz",
    summary="Amtlicher Wochen-Referenzpreis Deutschland (Benzin/Diesel)",
)
async def kraftstoff_referenz(request: Request):
    verify_api_key(request)
    referenzen = await hole_referenzen()
    return {"land": "DE", "kraftstoffe": [als_dict(r) for r in referenzen]}
