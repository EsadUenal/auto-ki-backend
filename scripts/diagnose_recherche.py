"""
Internes Diagnose-Werkzeug (Reliability-Sprint 3, §34/§35).

KEIN öffentlicher API-Endpoint, KEINE Debug-Daten im Kunden-Response — nur ein
lokal ausführbares Skript, das die Recherche-Pipeline direkt aufruft und pro
Such-Stufe protokolliert: Query, verwendete VehicleIdentity-Felder, Domains, rohe
Treffer, Listing-/Kategorie-/Info-Treffer, akzeptierte MarketObservations,
verworfene Treffer + Gründe, sehr ähnlich/ähnlich/bedingt, Domains, Quality Score,
Status, Dauer. Für die Ersatzteil-Fälle zusätzlich: Rohergebnis -> Kompatibilitäts-
klassifikation -> gefilterte Liste -> empfohlener Index -> Empfehlungstext.

Nutzt ECHTE Tavily-/Gemini-Calls (TAVILY_API_KEY muss gesetzt sein) — kein Mock.
KEIN Fahrzeug-Hardcoding im Produktcode: die Testfahrzeuge stehen nur HIER, als
Aufrufparameter des Diagnose-Skripts, nicht in der Pipeline selbst.

    python scripts/diagnose_recherche.py            # alle vier Fälle A-D
    python scripts/diagnose_recherche.py b           # nur Fall B (Kaufcheck)
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, ".")

from app.car_lookup import find_baureihe, find_motor
from app.config import TAVILY_API_KEY
from app.database import get_alle_baureihen_kurz, get_alle_motorvarianten_kurz
from app.ersatzteil_kompat import parse_fahrzeug, parse_bauteil, klassifiziere
from app.marktrecherche import baue_deep_queries, baue_rare_queries, vertiefe_marktrecherche
from app.marktvergleich import baue_ziel
from app.routers.ersatzteile import _mehrstufige_suche, _bewerte_kompatibilitaet
from app.vehicle_identity import VehicleIdentity
from app.web_search import US_QUELLEN_AUSSCHLUSS, tavily_search_with_fallback


def _abschnitt(titel: str) -> None:
    print("\n" + "=" * 78)
    print(titel)
    print("=" * 78)


async def diagnose_markt(name: str, req_kwargs: dict, *, check_typ: str = "kauf") -> None:
    """Führt die Markt-Query-Pipeline für ein Testfahrzeug aus (ohne Gemini-Call)
    und protokolliert jede Stufe (§34)."""
    _abschnitt(f"FALL {name}")
    req = SimpleNamespace(**req_kwargs)
    t0 = time.perf_counter()

    baureihe = find_baureihe(req.marke, req.modell, getattr(req, "baujahr", None))
    motor_match = find_motor(baureihe, getattr(req, "motor", None)) if baureihe else None
    print(f"Baureihe erkannt: {baureihe.get('id') if baureihe else None}")
    print(f"Motor erkannt:    {motor_match.get('bezeichnung') if motor_match else None}")

    identity = VehicleIdentity.from_market_context(baureihe, motor_match, req)
    print(f"VehicleIdentity:  {json.dumps(identity.as_diagnose(), ensure_ascii=False)}")
    print(f"Essenziell:       {identity.essenziell()!r}")

    ziel = baue_ziel(baureihe, motor_match, req,
                     get_alle_baureihen_kurz() if baureihe else [],
                     get_alle_motorvarianten_kurz() if baureihe else [])

    # Initiale kaskadierende Suche (wie kaufcheck.py/verkaufscheck.py).
    q_spezifisch = " ".join(filter(None, [req.marke, req.modell, getattr(req, "motor", None),
                                          str(getattr(req, "baujahr", "") or "")]))
    initial = await tavily_search_with_fallback([q_spezifisch], count=8,
                                                exclude_domains=US_QUELLEN_AUSSCHLUSS)

    deep_queries = baue_deep_queries(identity)
    rare_queries = baue_rare_queries(identity)
    print(f"\nQuery-Planner: {len(deep_queries)} Standard-Stufen, {len(rare_queries)} Rare-Stufen")

    _, ma, diag = await vertiefe_marktrecherche(
        initial, deep_queries, ziel, req_kwargs.get("preis_eur") or req_kwargs.get("preis_vorstellung"),
        US_QUELLEN_AUSSCHLUSS, count=10, zweck=f"diagnose-{name}",
        rare_queries=rare_queries)

    print("\n-- Stufen --")
    for s in diag["stufen"]:
        print(f"  [{s.get('stufe')}] label={s.get('label')} felder={s.get('felder')} "
              f"domains={s.get('domains')} query={s.get('query')!r}")
        print(f"      roh={s.get('roh')} neu={s.get('neu')} akzeptiert={s.get('akzeptiert')} "
              f"quali={s.get('quali')}")

    print("\n-- Ergebnis --")
    print(f"  gefunden (Datenpunkte gesamt):     {diag['gefunden_datenpunkte']}")
    print(f"  akzeptiert (verwendet):            {diag['akzeptiert']}")
    print(f"  verworfen:                         {diag['verworfen']}")
    if ma:
        print(f"  sehr_aehnlich / aehnlich / bedingt: "
              f"{ma.anzahl_sehr_aehnlich} / {ma.anzahl_aehnlich} / {ma.anzahl_bedingt}")
        listing_n = sum(1 for b in ma.beobachtungen if b.source_type == "listing")
        category_n = sum(1 for b in ma.beobachtungen if b.source_type == "category")
        unknown_n = sum(1 for b in ma.beobachtungen if b.source_type == "unknown")
        print(f"  source_type (verwendet): listing={listing_n} category={category_n} unknown={unknown_n}")
        print(f"  Median: {ma.median_eur} € | Spanne: {ma.spanne_min_eur}-{ma.spanne_max_eur} €")
    print(f"  Domains:                           {diag['domains']}")
    print(f"  Datenqualität:                     {diag['datenqualitaet']}")
    print(f"  research_status:                   {diag['research_status']}")
    print(f"  research_failure_grund:            {diag.get('research_failure_grund')}")
    print(f"  hatte_technischen_fehler:          {diag.get('hatte_technischen_fehler')}")
    print(f"  Dauer:                             {diag['dauer_ms']} ms "
          f"(gesamt inkl. initial: {round((time.perf_counter() - t0) * 1000)} ms)")


async def diagnose_ersatzteil(fahrzeug: str, bauteil: str, label: str) -> None:
    _abschnitt(f"FALL A ({label}): {fahrzeug!r} / {bauteil!r}")
    identity = VehicleIdentity.from_text(fahrzeug)
    print(f"VehicleIdentity essenziell: {identity.essenziell()!r} "
          f"(performance={sorted(identity.performance_markers)}, "
          f"editions={sorted(identity.edition_markers)})")

    roh = await _mehrstufige_suche(fahrzeug, bauteil)
    print(f"Rohe Web-Treffer: {len(roh)}")
    for r in roh:
        print(f"  - {r.get('url')}")

    fz = parse_fahrzeug(fahrzeug)
    teil = parse_bauteil(bauteil)
    print(f"\nparse_fahrzeug: marken={fz['marken']} performance={fz['performance']} "
          f"editions={fz['editions']} chassis={fz['chassis']}")

    # Direkter Klassifikationstest gegen die Roh-Treffer-Titel (ohne Gemini-Strukturierung).
    for r in roh:
        text = f"{r.get('title','')} {r.get('content','')}"
        kompat, grund = klassifiziere(fz, teil, text)
        print(f"  [{kompat:>10}] {grund:<55} | {r.get('title','')[:70]}")


async def main() -> None:
    if not TAVILY_API_KEY:
        print("TAVILY_API_KEY nicht gesetzt — Diagnose ohne echte Websuche nicht aussagekräftig.")
        return

    faelle = sys.argv[1:] or ["a", "b", "c", "d"]
    faelle = [f.lower() for f in faelle]

    if "a" in faelle:
        await diagnose_ersatzteil("BMW M3 E92", "Bremsscheiben vorne", "Kurzform")
        await diagnose_ersatzteil(
            "BMW M3 E92 2011 06/2011 Coupé Benzin 4.0 V8 3999 cm³ 420 PS DKG Hinterradantrieb",
            "Bremsscheiben vorne", "Detailform")

    if "b" in faelle:
        await diagnose_markt("B (Kaufcheck BMW 320d G20)", dict(
            marke="BMW", modell="320d", baujahr=2019, kilometerstand=120000,
            motor="320d", kraftstoff="Diesel", preis_eur=24900,
        ), check_typ="kauf")

    if "c" in faelle:
        await diagnose_markt("C (Verkaufscheck Opel Insignia B Grand Sport)", dict(
            marke="Opel", modell="Insignia", baujahr=2020, kilometerstand=115000,
            motor="2.0 Diesel Grand Sport", kraftstoff="Diesel", getriebe="Automatik",
            preis_vorstellung=18900,
        ), check_typ="verkauf")

    if "d" in faelle:
        await diagnose_markt("D (Rare Vehicle Isdera Imperator 108i)", dict(
            marke="Isdera", modell="Imperator 108i", baujahr=1991, kilometerstand=43000,
            motor=None, kraftstoff="Benzin", preis_eur=650000,
        ), check_typ="kauf")


if __name__ == "__main__":
    asyncio.run(main())
