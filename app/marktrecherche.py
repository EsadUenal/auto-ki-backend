from __future__ import annotations

"""
Adaptive, mehrstufige Marktrecherche (Core Reliability Sprint).

Root-Cause #4/#7: Die bisherige Suche gab die ERSTE nicht-leere Query zurück und
stoppte — Stopp-Kriterium war die rohe Trefferzahl. Bei verbreiteten Fahrzeugen
endete das dünn ("niedrig"), obwohl mit weiterer Recherche belastbare Daten
erreichbar gewesen wären.

Diese Funktion AKKUMULIERT Web-Treffer über zusätzliche Suchstufen und bewertet nach
JEDER Stufe die Zahl der AKZEPTIERTEN (modelltreuen) Vergleiche via analysiere_markt.
Sie endet, wenn genug wirklich passende Daten vorliegen ODER alle Stufen ausgeschöpft
sind — nicht anhand roher Trefferzahl. Kein Fahrzeug-Hardcoding.
"""

import logging
import time
from urllib.parse import urlparse

from app.marktvergleich import analysiere_markt
from app.web_search import tavily_search

log = logging.getLogger(__name__)


def _norm_url(url: str) -> str:
    try:
        p = urlparse(url or "")
        return f"{p.netloc.lower()}{p.path.rstrip('/')}".removeprefix("www.")
    except Exception:
        return url or ""


def _merge(dst: list[dict], seen: set[str], results: list[dict]) -> int:
    """Fügt nur echt neue URLs hinzu (dedupliziert über Stufen). Gibt #neu zurück."""
    neu = 0
    for r in results or []:
        k = _norm_url(r.get("url", ""))
        if k and k not in seen:
            seen.add(k)
            dst.append(r)
            neu += 1
    return neu


def genug(ma) -> bool:
    """Die Recherche DARF enden: belastbarer Median UND mindestens mittlere
    Datenqualität mit genügend akzeptierten, modelltreuen Vergleichen.

    Entscheidend ist die Zahl AKZEPTIERTER Ergebnisse (ma.verwendet), NICHT die rohe
    Trefferzahl. Bei einem seltenen Fahrzeug bleibt genug()==False -> es werden erst
    alle Suchstufen durchlaufen, bevor 'begrenzte Datenbasis' das Endergebnis ist.
    """
    return bool(ma and ma.median_eur and ma.datenqualitaet in ("mittel", "hoch") and ma.verwendet >= 4)


async def vertiefe_marktrecherche(
    initial_results: list[dict],
    deep_queries: list[str],
    ziel: dict,
    angebot_eur: int | None,
    exclude_domains: list[str] | None,
    *,
    count: int = 8,
    zweck: str = "marktvergleich",
    max_stufen: int = 4,
):
    """Vertieft die Marktrecherche adaptiv. Gibt (accumulated_results, marktanalyse,
    diagnose) zurück. `diagnose` ist ein internes, nicht-sensibles Log-Objekt."""
    t0 = time.perf_counter()
    accumulated: list[dict] = list(initial_results or [])
    seen: set[str] = {_norm_url(r.get("url", "")) for r in accumulated}

    ma = analysiere_markt(accumulated, ziel, angebot_eur)
    stufen: list[dict] = [{
        "stufe": "initial", "roh": len(accumulated), "neu": len(accumulated),
        "akzeptiert": ma.verwendet, "quali": ma.datenqualitaet,
    }]

    if not genug(ma):
        for i, q in enumerate(deep_queries[:max_stufen], 1):
            if not q or not q.strip():
                continue
            results = await tavily_search(q, count=count, exclude_domains=exclude_domains)
            neu = _merge(accumulated, seen, results)
            ma = analysiere_markt(accumulated, ziel, angebot_eur)
            stufen.append({
                "stufe": i, "query": q[:80], "roh": len(results), "neu": neu,
                "akzeptiert": ma.verwendet, "quali": ma.datenqualitaet,
            })
            if genug(ma):
                break

    diagnose = {
        "zweck": zweck,
        "stufen": stufen,
        "roh_seiten": len(accumulated),
        "gefunden_datenpunkte": ma.gefunden if ma else 0,
        "akzeptiert": ma.verwendet if ma else 0,
        "verworfen": (ma.gefunden - ma.verwendet) if ma else 0,
        "datenqualitaet": ma.datenqualitaet if ma else "niedrig",
        "domains": list(ma.quellen_domains) if ma else [],
        "dauer_ms": round((time.perf_counter() - t0) * 1000),
    }
    # Nachvollziehbare interne Suchdiagnose (keine sensiblen Daten): pro Recherche
    # Zweck, Stufen, roh/akzeptiert/verworfen, Domains, Datenqualität, Dauer.
    log.info("[RECHERCHE %s] akzeptiert=%d/%d quali=%s stufen=%d dauer=%dms domains=%s",
             zweck, diagnose["akzeptiert"], diagnose["gefunden_datenpunkte"],
             diagnose["datenqualitaet"], len(stufen), diagnose["dauer_ms"], diagnose["domains"])
    return accumulated, ma, diagnose
