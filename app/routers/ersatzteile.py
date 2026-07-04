"""
Ersatzteile-Router — intelligenter Preisvergleich für KFZ-Ersatzteile.

Ablauf:
  1. Gating prüfen (require_ersatzteil_access) — dekrementiert Kontingent
  2. Kurze, gezielte Tavily-Suche über Autodoc, kfzteile24, eBay, Amazon, TecDoc
  3. Gemini (JSON-Modus) strukturiert die Rohtreffer zu Vergleichskarten
     + liefert eine kurze KI-Einschätzung, welches Teil empfehlenswert ist
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator

from app.car_lookup import call_gemini_json
from app.config import TAVILY_API_KEY
from app.ersatzteil_gate import require_ersatzteil_access
from app.gemini_retry import RateLimitExhausted
from app.utf8 import UTF8JSONResponse
from app.web_search import results_to_belege, tavily_search

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/ersatzteile",
    tags=["ersatzteile"],
    default_response_class=UTF8JSONResponse,
)

_SHOP_DOMAINS = [
    "autodoc.de",
    "kfzteile24.de",
    "ebay.de",
]
_SITE_FILTER = "(site:autodoc.de OR site:kfzteile24.de OR site:ebay.de)"

_SYSTEM = """\
Du bist ein KFZ-Ersatzteil-Experte. Du erhältst rohe Web-Suchergebnisse zu einer \
Ersatzteilsuche (Fahrzeug + gesuchtes Bauteil) und wandelst sie in einen strukturierten \
Preisvergleich um — wie ein Vergleichsportal für Autoteile.

AUSGABE: Ausschließlich gültiges JSON, kein Text davor oder danach.

{
  "ergebnisse": [
    {
      "teilename": "<konkreter Produktname>",
      "anbieter": "<Shop-Name, z.B. Autodoc, kfzteile24, eBay>",
      "preis_eur": <Zahl in EUR ohne Währungszeichen, oder null wenn unbekannt>,
      "marke_typ": "oem" | "original" | "nachbau" | "unbekannt",
      "qualitaetsstufe": "<kurzer Begriff, z.B. 'Erstausrüster-Qualität', 'Premium-Nachbau', 'Budget-Nachbau'>",
      "url": "<direkte Produkt-URL aus den Quellen>",
      "hinweis": "<1 kurzer Satz: Besonderheit, Lieferzeit o.ä.>"
    }
  ],
  "empfehlung": "<2-4 Sätze: welches Teil ist empfehlenswert und warum — konkret, ehrlich, ohne Marketing-Sprache>",
  "empfohlener_index": <0-basierter Index des empfohlenen Eintrags in "ergebnisse", oder null>
}

REGELN:
1. Nutze NUR Informationen aus den bereitgestellten Web-Ergebnissen. Erfinde keine Preise oder Produkte.
2. Wenn ein Preis im Text nicht eindeutig erkennbar ist, setze preis_eur auf null — niemals schätzen.
3. "oem" = vom Fahrzeughersteller selbst (z.B. "BMW Original"), "original" = Erstausrüster-Marke (z.B. Brembo, Bosch, ATE), "nachbau" = günstige Nachbau-Marke ohne Erstausrüster-Bezug.
4. Maximal 8 Einträge, sortiert nach Preis aufsteigend.
5. Wenn KEINE brauchbaren Treffer in den Web-Ergebnissen stehen, gib "ergebnisse": [] zurück und erkläre in "empfehlung" ehrlich, dass keine Angebote gefunden wurden.
6. Die Empfehlung soll dem Nutzer Sicherheit geben: worauf er beim gewählten Teil achten sollte (Qualität vs. Preis), nicht nur "das billigste".
7. Schreibe ausschließlich auf Deutsch, sachlich, ohne Übertreibung.\
"""


class SucheBody(BaseModel):
    fahrzeug: str
    bauteil: str

    @field_validator("fahrzeug", "bauteil")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("darf nicht leer sein")
        return v[:120]


def _build_query(fahrzeug: str, bauteil: str) -> str:
    """
    Kurze, gezielte Suchanfrage — Tavily akzeptiert max. ~400 Zeichen.
    Site-Filter im Query-Text (zusätzlich zu include_domains) schränkt die
    Treffer gezielt auf die drei geprüften Shops ein → deutlich relevantere Ergebnisse.
    """
    query = f"{fahrzeug} {bauteil} kaufen Preis {_SITE_FILTER}"
    return query[:350]


@router.post("/suche")
async def ersatzteil_suche(
    body: SucheBody,
    _user_id: int = Depends(require_ersatzteil_access),
):
    query = _build_query(body.fahrzeug, body.bauteil)

    web_results: list[dict] = []
    if TAVILY_API_KEY:
        web_results = await tavily_search(query, count=8, include_domains=_SHOP_DOMAINS)

    if not web_results:
        return {
            "suchanfrage": {"fahrzeug": body.fahrzeug, "bauteil": body.bauteil},
            "ergebnisse": [],
            "empfehlung": "Für diese Suche wurden aktuell keine Angebote gefunden. "
                           "Versuche es mit einer präziseren Bauteil-Bezeichnung "
                           "(z. B. mit Fahrzeug-Generation wie 'E92' statt nur 'M3').",
            "empfohlener_index": None,
            "quelle": "web",
            "belege": [],
        }

    belege = results_to_belege(web_results)
    web_ctx_lines = ["=== WEB-TREFFER ===", ""]
    for i, r in enumerate(web_results, 1):
        web_ctx_lines.append(f"[{i}] {r.get('title', '')}")
        web_ctx_lines.append(f"    URL: {r.get('url', '')}")
        content = (r.get("content") or "").strip()
        if content:
            web_ctx_lines.append(f"    Inhalt: {content}")
        web_ctx_lines.append("")
    web_ctx = "\n".join(web_ctx_lines)

    user_msg = (
        f"GESUCHTES FAHRZEUG: {body.fahrzeug}\n"
        f"GESUCHTES BAUTEIL: {body.bauteil}\n\n"
        f"{web_ctx}"
    )

    try:
        result = await call_gemini_json(_SYSTEM, user_msg)
    except RateLimitExhausted as exc:
        return {
            "suchanfrage": {"fahrzeug": body.fahrzeug, "bauteil": body.bauteil},
            "ergebnisse": [],
            "empfehlung": f"KI-Auswertung momentan nicht verfügbar: {exc}",
            "empfohlener_index": None,
            "quelle": "web",
            "belege": belege,
        }

    ergebnisse = result.get("ergebnisse", [])
    if not isinstance(ergebnisse, list):
        ergebnisse = []

    return {
        "suchanfrage": {"fahrzeug": body.fahrzeug, "bauteil": body.bauteil},
        "ergebnisse": ergebnisse,
        "empfehlung": result.get("empfehlung", ""),
        "empfohlener_index": result.get("empfohlener_index"),
        "quelle": "web",
        "belege": belege,
    }
