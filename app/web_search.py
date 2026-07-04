"""
Tavily Search API — asynchroner Client für DB-first-Web-Fallback.

Wird ausgelöst wenn:
  - Preisfragen (Gebraucht-/Neupreis, Marktpreis, "was kostet")
  - Rückruf-Fragen ("aktueller Rückruf", "recall")
  - DB hat für die Frage keine ausreichenden Daten

Ergebnisse sind klar als Quelle "web" gekennzeichnet und erhalten vertrauen="niedrig".
Der API-Key wird via Umgebungsvariable TAVILY_API_KEY gesetzt.
Free-Plan: 1.000 Abfragen/Monat (basic = 1 Credit, kein Kreditkarte nötig).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx

from app.config import TAVILY_API_KEY

log = logging.getLogger(__name__)

_ENDPOINT = "https://api.tavily.com/search"


async def tavily_search(
    query: str,
    count: int = 5,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Ruft die Tavily Search API auf (POST, JSON-Body).

    Gibt [] zurück wenn:
      - TAVILY_API_KEY nicht gesetzt (Websuche deaktiviert)
      - Netzwerkfehler oder API-Fehler (nie weitergeworfen)

    search_depth="basic": 1 Credit pro Abfrage — sparsamster Modus.
    include_domains: optional — beschränkt die Suche auf bestimmte Shops/Quellen.
    exclude_domains: optional — blendet z.B. US-zentrierte Auto-Portale aus,
      deren Modelljahre/Ausstattungen vom europäischen Markt abweichen.
    """
    if not TAVILY_API_KEY:
        log.debug("Websuche übersprungen: TAVILY_API_KEY nicht gesetzt.")
        return []

    body: dict[str, Any] = {
        "api_key":      TAVILY_API_KEY,
        "query":        query,
        "search_depth": "basic",
        "max_results":  count,
        "include_answer":     False,
        "include_images":     False,
        "include_raw_content": False,
    }
    if include_domains:
        body["include_domains"] = include_domains
    if exclude_domains:
        body["exclude_domains"] = exclude_domains

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(_ENDPOINT, json=body)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            log.info("Tavily Search: %d Ergebnisse für %r", len(results), query[:80])
            return results

    except httpx.HTTPStatusError as exc:
        log.warning("Tavily HTTP-Fehler %s für %r: %s",
                    exc.response.status_code, query[:60], exc.response.text[:200])
    except Exception as exc:
        log.warning("Tavily Fehler (%s): %s", type(exc).__name__, exc)

    return []


async def tavily_search_with_fallback(
    queries: list[str],
    count: int = 5,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Probiert mehrere Suchanfragen der Reihe nach, bis eine Ergebnisse liefert.

    Macht die Marktpreis-/Informationssuche robuster: liefert die spezifischste
    Query (z.B. mit Motor + Baujahr + Kilometerstand) nichts, wird automatisch
    mit einer breiteren Query (z.B. nur Marke + Modell) nachgesucht — damit auch
    für seltene/exotische Fahrzeuge möglichst immer eine Marktpreisspanne entsteht.
    """
    for q in queries:
        if not q or not q.strip():
            continue
        results = await tavily_search(q, count=count, include_domains=include_domains, exclude_domains=exclude_domains)
        if results:
            return results
    return []


def results_to_context(results: list[dict]) -> str:
    """
    Wandelt Tavily-Suchergebnisse in einen LLM-Kontext-Block um.

    Bewusst als "ungeprüft" markiert — das LLM soll Web-Daten nicht
    mit geprüften DB-Daten gleichsetzen.
    """
    if not results:
        return ""

    lines = [
        "=== AKTUELLE WEB-ERGEBNISSE (Tavily Search — ungeprüft, kritisch verwenden) ===",
        "Diese Daten stammen aus dem Web und sind NICHT redaktionell geprüft.",
        "Preise sind Marktorientierungen, keine garantierten Angebote.",
        "",
    ]
    for i, r in enumerate(results, 1):
        title   = r.get("title", "Ohne Titel")
        url     = r.get("url", "")
        content = (r.get("content") or "").strip()
        pub     = r.get("published_date", "")
        lines.append(f"[{i}] {title}")
        if url:
            lines.append(f"    Quelle: {url}")
        if content:
            lines.append(f"    {content}")
        if pub:
            lines.append(f"    Datum: {pub}")
        lines.append("")

    return "\n".join(lines)


def results_to_belege(results: list[dict]) -> list[dict]:
    """
    Erstellt die `belege`-Liste für die API-Antwort.

    Jeder Beleg enthält typ="web", titel, url, snippet, abgerufen.
    Das Frontend kann diese URLs direkt als klickbare Quell-Links anzeigen.
    """
    heute = date.today().isoformat()
    return [
        {
            "typ":       "web",
            "titel":     (r.get("title") or "")[:120],
            "url":       r.get("url", ""),
            "snippet":   (r.get("content") or "")[:200],
            "abgerufen": heute,
        }
        for r in results
        if r.get("url")
    ]


def build_price_query(marke: str, modell: str, generation: str, nutzer_frage: str) -> str:
    """
    Baut eine zielgerichtete Suchanfrage für Marktpreis-/Rückruf-Abfragen.

    Nutzt die Originalfrage als Basis, ergänzt Fahrzeugname und "Deutschland"
    wenn nicht bereits enthalten.
    """
    fahrzeug    = f"{marke} {modell} {generation}".strip()
    frage_lower = nutzer_frage.lower()

    if fahrzeug.lower() in frage_lower or modell.lower() in frage_lower:
        query = nutzer_frage
    else:
        query = f"{fahrzeug} {nutzer_frage}"

    if "deutschland" not in frage_lower and ".de" not in frage_lower:
        query = f"{query} Deutschland"

    return query
