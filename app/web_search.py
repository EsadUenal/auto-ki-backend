"""
Brave Search API — asynchroner Client für DB-first-Web-Fallback.

Wird ausgelöst wenn:
  - Preisfragen (Gebraucht-/Neupreis, Marktpreis, "was kostet")
  - Rückruf-Fragen ("aktueller Rückruf", "recall")
  - DB hat für die Frage keine ausreichenden Daten

Ergebnisse sind klar als Quelle "web" gekennzeichnet und erhalten vertrauen="niedrig".
Der API-Key wird via Umgebungsvariable BRAVE_SEARCH_API_KEY gesetzt.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx

from app.config import BRAVE_SEARCH_API_KEY

log = logging.getLogger(__name__)

_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


async def brave_search(query: str, count: int = 5) -> list[dict[str, Any]]:
    """
    Ruft die Brave Search API auf.

    Gibt [] zurück wenn:
      - BRAVE_SEARCH_API_KEY nicht gesetzt (Websuche deaktiviert)
      - Netzwerkfehler oder API-Fehler

    Fehler werden nie weitergeworfen — Websuche ist ein optionaler Fallback,
    der den Haupt-Flow nicht blockieren darf.
    """
    if not BRAVE_SEARCH_API_KEY:
        log.debug("Websuche übersprungen: BRAVE_SEARCH_API_KEY nicht gesetzt.")
        return []

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                _ENDPOINT,
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
                },
                params={
                    "q": query,
                    "count": count,
                    "country": "de",
                    "search_lang": "de",
                    "ui_lang": "de-DE",
                    "safesearch": "moderate",
                    "text_decorations": "false",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("web", {}).get("results", [])
            log.info("Brave Search: %d Ergebnisse für %r", len(results), query[:80])
            return results

    except httpx.HTTPStatusError as exc:
        log.warning("Brave Search HTTP-Fehler %s für %r: %s", exc.response.status_code, query[:60], exc)
    except Exception as exc:
        log.warning("Brave Search Fehler (%s): %s", type(exc).__name__, exc)

    return []


def results_to_context(results: list[dict]) -> str:
    """
    Wandelt Brave-Suchergebnisse in einen LLM-Kontext-Block um.

    Der Block ist bewusst klar als "ungeprüft" markiert, damit das LLM
    Web-Daten nicht mit geprüften DB-Daten verwechselt.
    """
    if not results:
        return ""

    lines = [
        "=== AKTUELLE WEB-ERGEBNISSE (Brave Search — ungeprüft, kritisch verwenden) ===",
        "Diese Daten stammen aus dem Web und sind NICHT redaktionell geprüft.",
        "Preise sind Marktorientierungen, keine garantierten Angebote.",
        "",
    ]
    for i, r in enumerate(results, 1):
        title = r.get("title", "Ohne Titel")
        url = r.get("url", "")
        desc = (r.get("description") or "").strip()
        age = r.get("page_age", "")
        lines.append(f"[{i}] {title}")
        if url:
            lines.append(f"    Quelle: {url}")
        if desc:
            lines.append(f"    {desc}")
        if age:
            lines.append(f"    Datum: {age}")
        lines.append("")

    return "\n".join(lines)


def results_to_belege(results: list[dict]) -> list[dict]:
    """
    Erstellt die `belege`-Liste für die API-Antwort.

    Jeder Beleg enthält typ="web", titel, url, snippet und abgerufen-Datum.
    Das Frontend kann diese URLs direkt als Quell-Links anzeigen.
    """
    heute = date.today().isoformat()
    return [
        {
            "typ": "web",
            "titel": (r.get("title") or "")[:120],
            "url": r.get("url", ""),
            "snippet": (r.get("description") or "")[:200],
            "abgerufen": heute,
        }
        for r in results
        if r.get("url")
    ]


def build_price_query(marke: str, modell: str, generation: str, nutzer_frage: str) -> str:
    """
    Baut eine zielgerichtete Suchanfrage für Marktpreis-Abfragen.

    Nutzt die Originalfrage des Nutzers als Basis, ergänzt um den
    vollständigen Fahrzeugnamen und 'Deutschland' für lokale Ergebnisse.
    """
    # Fahrzeugname aus den DB-Feldern zusammensetzen
    fahrzeug = f"{marke} {modell} {generation}".strip()

    # Originalfrage bereits präzise genug? Dann nur Deutschland ergänzen
    frage_lower = nutzer_frage.lower()
    if fahrzeug.lower() in frage_lower or modell.lower() in frage_lower:
        query = nutzer_frage
    else:
        query = f"{fahrzeug} {nutzer_frage}"

    if "deutschland" not in frage_lower and ".de" not in frage_lower:
        query = f"{query} Deutschland"

    return query
