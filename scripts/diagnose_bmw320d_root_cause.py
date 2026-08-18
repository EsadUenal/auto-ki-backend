"""
BMW 320d G20 — Root-Cause-Diagnose (Reliability-Sprint 4, Nachbesserung).

Beantwortet konkret:
  1. Liefert der BMW-320d-G20-Fall (2019, 190 PS, Automatik, 120.000 km) über
     mehrere unabhängige Cache-Bypass-Läufe zuverlässig einen belastbaren Check?
  2. Welche Provider-Konfiguration (basic/advanced x raw_content, + Extract)
     liefert die meisten VALIDIERTEN Einzelfahrzeuge?
  3. Hilft Tavily Extract auf den besten Kandidaten-URLs tatsächlich?

Nutzt ECHTE Tavily-Calls (TAVILY_API_KEY muss gesetzt sein) — kostet Credits.

    python scripts/diagnose_bmw320d_root_cause.py runs      # 3x Standard-Pipeline
    python scripts/diagnose_bmw320d_root_cause.py matrix    # Provider-Matrix
    python scripts/diagnose_bmw320d_root_cause.py extract   # Extract-Augmentierung
    python scripts/diagnose_bmw320d_root_cause.py alle      # alles (Default)
"""
from __future__ import annotations

import asyncio
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, ".")

from app.car_lookup import find_baureihe, find_motor
from app.config import TAVILY_API_KEY
from app.marktrecherche import baue_deep_queries, baue_rare_queries, vertiefe_marktrecherche
from app.marktvergleich import baue_ziel, analysiere_markt, _extrahiere_aus_text
from app.vehicle_identity import VehicleIdentity
from app.web_search import (
    US_QUELLEN_AUSSCHLUSS, ist_einzelinserat, ist_kategorieseite, ist_info_domain,
    tavily_search_mit_status, tavily_extract,
)

REQ = dict(marke="BMW", modell="320d", baujahr=2019, kilometerstand=120000,
          motor="320d", kraftstoff="Diesel", getriebe="Automatik", preis_eur=24900)


def _abschnitt(titel: str) -> None:
    print("\n" + "=" * 78)
    print(titel)
    print("=" * 78)


def _klassifiziere(url: str, titel: str) -> str:
    if ist_info_domain(url):
        return "info"
    if ist_einzelinserat(url, titel):
        return "listing_detail"
    if ist_kategorieseite(url, titel):
        return "category_search"
    return "unknown"


async def einzelner_lauf(lauf_nr: int) -> dict:
    """Ein vollständiger Kaufcheck-Marktrecherche-Lauf, bypass_cache=True, mit
    voller Stufen-Diagnose (Query, Modus, Treffer, Klassifikation, Ablehnungen)."""
    _abschnitt(f"LAUF {lauf_nr} — BMW 320d G20 (Cache-Bypass)")
    req = SimpleNamespace(**REQ)
    t0 = time.perf_counter()

    baureihe = find_baureihe(req.marke, req.modell, req.baujahr)
    motor_match = find_motor(baureihe, req.motor) if baureihe else None
    ziel = baue_ziel(baureihe, motor_match, req, [], [])
    identity = VehicleIdentity.from_market_context(baureihe, motor_match, req)
    deep_queries = baue_deep_queries(identity)
    rare_queries = baue_rare_queries(identity)

    ergebnisse, ma, diag = await vertiefe_marktrecherche(
        [], deep_queries, ziel, req.preis_eur, US_QUELLEN_AUSSCHLUSS,
        count=20, zweck=f"root-cause-lauf-{lauf_nr}", rare_queries=rare_queries,
        bypass_cache=True)

    # Klassifikation ALLER akkumulierten Roh-Treffer dieses Laufs (nicht nur der
    # letzten Stufe) — für die geforderte volle Transparenz.
    typen = {"listing_detail": 0, "category_search": 0, "info": 0, "unknown": 0}
    for r in ergebnisse:
        typen[_klassifiziere(r.get("url", ""), r.get("title", ""))] += 1

    print(f"Query-Stufen durchlaufen: {len(diag['stufen'])}")
    for s in diag["stufen"]:
        print(f"  [{s.get('stufe')}] label={s.get('label')!r} "
              f"query={s.get('query')!r}")
        print(f"      felder={s.get('felder')} weggelassen={s.get('weggelassene_felder')} "
              f"domains={s.get('domains')} raw_content={s.get('raw_content')}")
        print(f"      roh={s.get('roh')} neu={s.get('neu')} akzeptiert={s.get('akzeptiert')} "
              f"quali={s.get('quali')} hintergrund_domains={s.get('hintergrund_domains')}")

    print(f"\nRohe Treffer gesamt: {len(ergebnisse)}")
    print(f"  davon listing_detail:  {typen['listing_detail']}")
    print(f"  davon category_search: {typen['category_search']}")
    print(f"  davon info:            {typen['info']}")
    print(f"  davon unknown:         {typen['unknown']}")
    print(f"\nExtrahierte Datenpunkte gesamt (gefunden): {ma.gefunden}")
    print(f"Validiert/verwendet (Median-tragend):        {ma.verwendet}")
    print(f"Hintergrund-Domains (ausgeschlossen):        {ma.hintergrund_domains}")
    print(f"Median-tragende Domains:                     {ma.quellen_domains}")
    print(f"Median: {ma.median_eur} € | Spanne: {ma.spanne_min_eur}-{ma.spanne_max_eur} €")
    print(f"Datenqualität: {ma.datenqualitaet}")
    print(f"research_status: {diag['research_status']} | grund: {diag.get('research_failure_grund')}")
    print(f"Dauer: {round((time.perf_counter() - t0) * 1000)} ms")

    # Konkrete verwendete Fahrzeuge einzeln auflisten (Nachvollziehbarkeit §16).
    if ma.beobachtungen:
        print("\nVerwendete MarketObservations:")
        for b in ma.beobachtungen:
            print(f"  {b.preis_eur} € | {b.baujahr} | {b.kilometerstand} km | "
                  f"{b.vergleichbarkeit} | {b.source_type} | {b.quelle_domain}")

    return {
        "lauf": lauf_nr, "status": diag["research_status"], "quali": ma.datenqualitaet,
        "verwendet": ma.verwendet, "median": ma.median_eur, "domains": ma.quellen_domains,
        "dauer_ms": round((time.perf_counter() - t0) * 1000),
    }


async def drei_laeufe() -> None:
    ergebnisse = []
    for i in range(1, 4):
        ergebnisse.append(await einzelner_lauf(i))
    _abschnitt("ZUSAMMENFASSUNG: 3 unabhängige Cache-Bypass-Läufe")
    for e in ergebnisse:
        print(f"  Lauf {e['lauf']}: status={e['status']} quali={e['quali']} "
              f"verwendet={e['verwendet']} median={e['median']} domains={e['domains']} "
              f"({e['dauer_ms']} ms)")
    erfolgreich = sum(1 for e in ergebnisse if e["status"] in ("completed_high", "completed_medium"))
    print(f"\n  {erfolgreich}/3 Läufe erfolgreich (completed_high/medium).")


_MATRIX = [
    dict(search_depth="basic", max_results=20, include_raw_content=False),
    dict(search_depth="advanced", max_results=20, include_raw_content=False),
    dict(search_depth="basic", max_results=20, include_raw_content=True),
    dict(search_depth="advanced", max_results=20, include_raw_content=True),
]


async def provider_matrix() -> list[tuple[str, list]]:
    _abschnitt("PROVIDER-MATRIX — BMW 320d G20 (Stufe A 'eng')")
    req = SimpleNamespace(**REQ)
    baureihe = find_baureihe(req.marke, req.modell, req.baujahr)
    motor_match = find_motor(baureihe, req.motor) if baureihe else None
    identity = VehicleIdentity.from_market_context(baureihe, motor_match, req)
    deep_queries = baue_deep_queries(identity)
    query = deep_queries[0].query
    print(f"Query: {query!r}\n")

    beste_urls: list[str] = []
    for cfg in _MATRIX:
        t0 = time.perf_counter()
        try:
            results, fehler = await tavily_search_mit_status(
                query, count=cfg["max_results"], exclude_domains=US_QUELLEN_AUSSCHLUSS,
                include_raw_content=cfg["include_raw_content"], search_depth=cfg["search_depth"],
                bypass_cache=True)
        except Exception as exc:
            print(f"  {cfg} -> AUSNAHME: {type(exc).__name__}: {exc}")
            continue
        dauer_ms = round((time.perf_counter() - t0) * 1000)

        typen = {"listing_detail": 0, "category_search": 0, "info": 0, "unknown": 0}
        datenpunkte = 0
        volle_attribute = 0
        kandidat_urls = []
        for r in results:
            url, titel = r.get("url", ""), r.get("title", "")
            typ = _klassifiziere(url, titel)
            typen[typ] += 1
            if typ in ("listing_detail", "unknown"):
                kandidat_urls.append((url, r.get("title", "")))
            raw = (r.get("raw_content") or "")[:20_000]
            text = f"{titel}\n{r.get('content','')}\n{raw}"
            punkte = _extrahiere_aus_text(text, url, typ if typ in ("listing_detail", "category_search") else "unknown")
            datenpunkte += len(punkte)
            volle_attribute += sum(1 for p in punkte if p.baujahr is not None and p.kilometerstand is not None)

        print(f"  search_depth={cfg['search_depth']:<9} raw_content={str(cfg['include_raw_content']):<5} | "
              f"roh={len(results):<3} listing={typen['listing_detail']:<2} "
              f"category={typen['category_search']:<2} info={typen['info']:<2} "
              f"unknown={typen['unknown']:<2} | datenpunkte={datenpunkte:<4} "
              f"volle_attr={volle_attribute:<3} | {dauer_ms} ms{' [FEHLER]' if fehler else ''}")

        if not beste_urls:
            beste_urls = [u for u, _ in kandidat_urls[:5]]

    return beste_urls


async def extract_augmentierung(kandidat_urls: list[str] | None = None) -> None:
    _abschnitt("EXTRACT-AUGMENTIERUNG — beste Kandidaten-URLs voll abrufen")
    if not kandidat_urls:
        kandidat_urls = await provider_matrix()
    if not kandidat_urls:
        print("Keine Kandidaten-URLs gefunden.")
        return
    print(f"Extract auf {len(kandidat_urls)} URL(s):")
    t0 = time.perf_counter()
    extrahiert = await tavily_extract(kandidat_urls, advanced=True)
    dauer_ms = round((time.perf_counter() - t0) * 1000)

    gesamt_neue_punkte = 0
    for e in extrahiert:
        laenge = len(e["raw_content"] or "")
        typ = _klassifiziere(e["url"], "")
        punkte = _extrahiere_aus_text(e["raw_content"] or "", e["url"], typ)
        gesamt_neue_punkte += len(punkte)
        print(f"  {'OK ' if e['erfolg'] else 'FEHL'} len={laenge:<7} typ={typ:<15} "
              f"extrahierte_punkte={len(punkte):<3} {e['url']}")
        for p in punkte[:3]:
            print(f"      -> {p.preis_eur} € | {p.baujahr} | {p.kilometerstand} km")

    print(f"\nExtract-Laufzeit: {dauer_ms} ms | zusätzliche Datenpunkte gesamt: {gesamt_neue_punkte}")


async def main() -> None:
    if not TAVILY_API_KEY:
        print("TAVILY_API_KEY nicht gesetzt.")
        return
    modus = sys.argv[1] if len(sys.argv) > 1 else "alle"
    if modus in ("runs", "alle"):
        await drei_laeufe()
    if modus in ("matrix", "alle"):
        kandidaten = await provider_matrix()
        if modus == "alle":
            await extract_augmentierung(kandidaten)
    if modus == "extract":
        await extract_augmentierung()


if __name__ == "__main__":
    asyncio.run(main())
