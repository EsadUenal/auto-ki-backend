"""
Provider-Matrix-Messung (Reliability-Sprint 4, Phase 0/§5).

Misst für BMW 320d G20 und Opel Insignia B Grand Sport, wie viele ROHE Treffer,
konkrete Einzelinserate (source_type="listing") vs. Kategorie-/Suchseiten
(source_type="category") und tatsächlich extrahierbare Preis-Datenpunkte die
verschiedenen Tavily-Konfigurationen liefern:

  - search_depth: basic vs. advanced
  - max_results: 10 vs. 20
  - include_raw_content: an/aus
  - Tavily Extract (voller Seiteninhalt) auf den vielversprechendsten URLs der
    besten Search-Konfiguration

KEIN Fahrzeug-Hardcoding im Produktcode — die Testfahrzeuge stehen nur HIER.
Nutzt ECHTE Tavily-Calls (TAVILY_API_KEY muss gesetzt sein) — kostet Credits.

    python scripts/diagnose_provider_matrix.py            # beide Fahrzeuge
    python scripts/diagnose_provider_matrix.py bmw         # nur BMW 320d
    python scripts/diagnose_provider_matrix.py insignia    # nur Insignia
"""
from __future__ import annotations

import asyncio
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, ".")

from app.config import TAVILY_API_KEY
from app.marktrecherche import baue_deep_queries
from app.marktvergleich import _extrahiere_aus_text
from app.vehicle_identity import VehicleIdentity
from app.web_search import (
    US_QUELLEN_AUSSCHLUSS, ist_einzelinserat, ist_kategorieseite, ist_info_domain,
    tavily_search_mit_status, tavily_extract,
)

FAELLE = {
    "bmw": dict(
        marke="BMW", modell="320d", baujahr=2019, kilometerstand=120000,
        motor="320d", kraftstoff="Diesel", getriebe="Automatik",
        beschreibung="190 PS",
    ),
    "insignia": dict(
        marke="Opel", modell="Insignia", baujahr=2020, kilometerstand=115000,
        motor="2.0 Diesel Grand Sport", kraftstoff="Diesel", getriebe="Automatik",
        beschreibung="174 PS Grand Sport",
    ),
}

_MATRIX = [
    dict(search_depth="basic", max_results=10, include_raw_content=False),
    dict(search_depth="basic", max_results=10, include_raw_content=True),
    dict(search_depth="basic", max_results=20, include_raw_content=False),
    dict(search_depth="basic", max_results=20, include_raw_content=True),
    dict(search_depth="advanced", max_results=10, include_raw_content=False),
    dict(search_depth="advanced", max_results=10, include_raw_content=True),
    dict(search_depth="advanced", max_results=20, include_raw_content=False),
    dict(search_depth="advanced", max_results=20, include_raw_content=True),
]


def _klassifiziere(url: str, titel: str) -> str:
    if ist_info_domain(url):
        return "info"
    if ist_einzelinserat(url, titel):
        return "listing"
    if ist_kategorieseite(url, titel):
        return "category"
    return "unknown"


async def messe_konfiguration(query: str, cfg: dict) -> dict:
    t0 = time.perf_counter()
    results, fehler = await tavily_search_mit_status(
        query, count=cfg["max_results"], exclude_domains=US_QUELLEN_AUSSCHLUSS,
        include_raw_content=cfg["include_raw_content"], search_depth=cfg["search_depth"],
        bypass_cache=True,
    )
    dauer_ms = round((time.perf_counter() - t0) * 1000)

    typen = {"listing": 0, "category": 0, "info": 0, "unknown": 0}
    datenpunkte = 0
    volle_attribute = 0
    urls_listing: list[str] = []
    for r in results:
        url, titel = r.get("url", ""), r.get("title", "")
        typ = _klassifiziere(url, titel)
        typen[typ] += 1
        if typ == "listing":
            urls_listing.append(url)
        raw = (r.get("raw_content") or "")[:20_000]
        text = f"{titel}\n{r.get('content','')}\n{raw}"
        punkte = _extrahiere_aus_text(text, url, typ if typ in ("listing", "category") else "unknown")
        datenpunkte += len(punkte)
        volle_attribute += sum(1 for p in punkte if p.baujahr is not None and p.kilometerstand is not None)

    return {
        "cfg": cfg, "fehler": fehler, "dauer_ms": dauer_ms,
        "roh": len(results), "typen": typen, "datenpunkte": datenpunkte,
        "volle_attribute": volle_attribute, "urls_listing": urls_listing[:5],
    }


async def messe_fahrzeug(name: str, req_kwargs: dict) -> None:
    print("\n" + "=" * 78)
    print(f"PROVIDER-MATRIX: {name}")
    print("=" * 78)
    req = SimpleNamespace(**req_kwargs)
    identity = VehicleIdentity.from_market_context(None, None, req)
    deep_queries = baue_deep_queries(identity)
    query = deep_queries[0].query if deep_queries else f"{req.marke} {req.modell} gebraucht"
    print(f"Query (Stufe A, eng): {query!r}\n")

    beste: dict | None = None
    for cfg in _MATRIX:
        r = await messe_konfiguration(query, cfg)
        beste = r if beste is None or r["datenpunkte"] > beste["datenpunkte"] else beste
        print(f"  search_depth={cfg['search_depth']:<9} max_results={cfg['max_results']:<3} "
              f"raw_content={str(cfg['include_raw_content']):<5} | "
              f"roh={r['roh']:<3} listing={r['typen']['listing']:<2} "
              f"category={r['typen']['category']:<2} info={r['typen']['info']:<2} "
              f"unknown={r['typen']['unknown']:<2} | datenpunkte={r['datenpunkte']:<3} "
              f"volle_attr={r['volle_attribute']:<3} | {r['dauer_ms']} ms"
              f"{'  [FEHLER]' if r['fehler'] else ''}")

    if beste and beste["urls_listing"]:
        print(f"\n  -- Tavily Extract auf {len(beste['urls_listing'])} Listing-URL(s) "
              f"der besten Konfiguration --")
        t0 = time.perf_counter()
        extrahiert = await tavily_extract(beste["urls_listing"])
        dauer_ms = round((time.perf_counter() - t0) * 1000)
        for e in extrahiert:
            laenge = len(e["raw_content"] or "")
            print(f"    {'OK ' if e['erfolg'] else 'FEHL'} len={laenge:<6} {e['url']}")
        print(f"    Extract-Laufzeit: {dauer_ms} ms")
    else:
        print("\n  -- Keine Listing-URL für Extract-Test gefunden (auch nicht in der "
              "besten Konfiguration) --")


async def main() -> None:
    if not TAVILY_API_KEY:
        print("TAVILY_API_KEY nicht gesetzt — Messung ohne echte Websuche nicht aussagekräftig.")
        return
    faelle = [f.lower() for f in sys.argv[1:]] or list(FAELLE.keys())
    for name in faelle:
        if name not in FAELLE:
            print(f"Unbekannter Fall: {name!r} (verfügbar: {list(FAELLE.keys())})")
            continue
        await messe_fahrzeug(name, FAELLE[name])


if __name__ == "__main__":
    asyncio.run(main())
