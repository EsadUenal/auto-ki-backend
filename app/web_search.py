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

import asyncio
import logging
import time
from datetime import date
from typing import Any

import httpx

from app.config import TAVILY_API_KEY

log = logging.getLogger(__name__)

_ENDPOINT = "https://api.tavily.com/search"

# Retry mit Exponential Backoff — nur für transiente Fehler (429 Rate-Limit, 5xx
# Server-Fehler). Andere Fehler (400 ungültige Anfrage, 401 falscher Key etc.)
# werden sofort aufgegeben, ein Retry würde dort ohnehin nie erfolgreich sein.
_MAX_RETRIES = 3
_BACKOFF_BASIS_S = 1.0  # 1s, 2s, 4s

# Kurzlebiger In-Memory-Cache für IDENTISCHE Suchanfragen (gleicher Query-String +
# Domain-Filter). Fängt den häufigen Fall ab, dass dieselbe Baureihe innerhalb
# kurzer Zeit mehrfach gesucht wird (z.B. mehrere Nutzer fragen zeitnah nach
# demselben Auto, oder eine Folgefrage im selben Gespräch löst dieselbe Query
# erneut aus). TTL bewusst kurz (5 Minuten) — Marktpreise/Web-Inhalte ändern sich
# nicht innerhalb weniger Minuten, Ergebnis bleibt für den Nutzer identisch.
_CACHE_TTL_S = 300.0
_cache: dict[tuple, tuple[float, list[dict]]] = {}


def _cache_key(query: str, count: int, include_domains, exclude_domains) -> tuple:
    return (
        query,
        count,
        tuple(include_domains) if include_domains else None,
        tuple(exclude_domains) if exclude_domains else None,
    )


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

    key = _cache_key(query, count, include_domains, exclude_domains)
    cached = _cache.get(key)
    if cached is not None and (time.monotonic() - cached[0]) < _CACHE_TTL_S:
        log.debug("Tavily Cache-Treffer für %r", query[:80])
        return cached[1]

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

    results: list[dict[str, Any]] = []
    for versuch in range(_MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(_ENDPOINT, json=body)
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])
                log.info("Tavily Search: %d Ergebnisse für %r", len(results), query[:80])
                break

        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            transient = status_code == 429 or status_code >= 500
            if transient and versuch < _MAX_RETRIES - 1:
                delay = _BACKOFF_BASIS_S * (2 ** versuch)
                log.warning("Tavily %s (Versuch %d/%d) für %r — warte %.0fs",
                            status_code, versuch + 1, _MAX_RETRIES, query[:60], delay)
                await asyncio.sleep(delay)
                continue
            log.warning("Tavily HTTP-Fehler %s für %r: %s",
                        status_code, query[:60], exc.response.text[:200])
            break
        except Exception as exc:
            log.warning("Tavily Fehler (%s): %s", type(exc).__name__, exc)
            break

    if results:
        # Größe begrenzen (Long-Running-Prozess) — bei Überschreitung einfach den
        # ältesten Eintrag verdrängen statt eine komplexe LRU-Struktur zu pflegen;
        # bei realistischer Anfragevielfalt wird dieses Limit kaum je erreicht.
        if len(_cache) >= 500:
            aeltester = min(_cache, key=lambda k: _cache[k][0])
            del _cache[aeltester]
        _cache[key] = (time.monotonic(), results)
    return results


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
