from __future__ import annotations

"""
SearchProvider-Schnittstelle (Reliability-Sprint 4, Nachbesserung §4).

Vorbereitung, KEINE Migration: `app/web_search.py` bleibt der einzige tatsächlich
verdrahtete Such-Client (Tavily). Dieses Modul definiert nur die Ziel-Schnittstelle,
damit ein zweiter Provider später sauber danebengestellt werden kann, OHNE dass
`app/marktrecherche.py` (Query-Planner) oder `app/marktvergleich.py`
(Extraktion/Bewertung) etwas von der konkreten Quelle wissen müssen.

WARUM das nötig werden könnte (empirisch belegt, scripts/diagnose_bmw320d_root_
cause.py, 3 unabhängige Cache-Bypass-Läufe für "BMW 320d G20, 2019, 190 PS,
Automatik, 120.000 km" — ein bewusst POPULÄRER, nicht seltener Fall):

  - kleinanzeigen.de liefert zuverlässig reichhaltige, einzeln attribuierte
    Preisdaten (9 valide Datenpunkte in allen 3 Läufen identisch) — aber IMMER
    nur diese EINE Domain.
  - mobile.de blockiert Tavily Extract aktiv ("Access denied", Bot-Schutz) —
    KEIN Umgehungsversuch (§ Sicherheitsrichtlinie: keine Umgehung von Zugriffs-
    schutz). Auch über normale Tavily-Search-Snippets liefert mobile.de für
    diese Query praktisch keine extrahierbaren Preis-Datenpunkte.
  - autoscout24.de (nach Behebung eines TLD-Validierungsfehlers in
    `MARKTPLATZ_DOMAINS`, siehe web_search.py) liefert Suchtreffer, aber die
    Listing-Seiten sind clientseitig (JS) gerendert — Tavilys statischer/Such-
    Snippet-Inhalt bzw. selbst Extract liefert nur vereinzelte, nicht
    zuverlässig vollständige Preis-Datenpunkte.
  - autouncle.de liefert gelegentlich echte Einzelinserat-Detailseiten (siehe
    ist_einzelinserat-Regex-Fix), aber nicht in jedem Lauf für jedes Fahrzeug.
  - Andere beobachtete Domains (bmw-hubauer.de, autohero.com, autobild.de)
    lassen sich zwar per Extract abrufen, liefern aber bei generischer Regex-
    Extraktion NACHWEISLICH FALSCHE Werte (getestet: Baujahr/km-Kombinationen,
    die offensichtlich nicht zu echten Fahrzeugen gehören — vermutlich
    Finanzierungs-/Navigations-/Footer-Zahlen). Eine domain-spezifische
    Parser-Anpassung für jede dieser Seiten wäre ein großer Umbau (explizit
    NICHT gewollt) und wurde deshalb NICHT vorgenommen.

  Ergebnis: Tavily (basic/advanced/raw_content/Extract) liefert für diesen
  POPULÄREN Fall strukturell nur EINE verlässliche Domain. Das "mittel"-
  Qualitätsgate verlangt bewusst >=2 unabhängige Domains (§14 Reliability-
  Sprint 3, nicht gelockert) — das ist in dieser Konfiguration nicht ehrlich
  erreichbar, ohne entweder die Domain-Vielfalt-Anforderung aufzuweichen
  (ausdrücklich NICHT gewollt) oder eine zweite, unabhängige Datenquelle
  hinzuzunehmen.

KONKRETER VORSCHLAG (keine Fantasieintegration — reale, heute existierende
Optionen, keine davon bereits angebunden oder mit Zugangsdaten hinterlegt):

  1. mobile.de Search API / AutoScout24 Connect API — offizielle B2B-Partner-
     Schnittstellen der beiden größten deutschen Fahrzeugmarktplätze. Liefern
     strukturierte Einzelinserate (Preis/km/Baujahr/Motor exakt, keine Regex-
     Extraktion nötig) LEGAL und ohne Zugriffsschutz-Umgehung — erfordern aber
     eine Geschäftsvereinbarung/Partnerschaft mit dem jeweiligen Portal (kein
     einfacher API-Key-Self-Service). Höchste Datenqualität, höchster
     Beschaffungsaufwand.
  2. MarketCheck.com API (oder vergleichbarer Fahrzeugdaten-Aggregator mit
     EU-/DE-Abdeckung) — kommerzielle API, die bereits lizenziert Gebraucht-
     wagen-Inserate mehrerer Portale strukturiert bereitstellt. Prüfen, ob
     Baustein für DE-Markt-Abdeckung vorhanden ist (zum jetzigen Zeitpunkt
     NICHT verifiziert — vor Kauf/Integration Testzugang klären).
  3. Ein zweiter allgemeiner Such-Provider (z.B. Serper.dev, Brave Search API,
     Bing Web Search API) als ZUSÄTZLICHE Discovery-Quelle neben Tavily — löst
     das JS-Rendering-Problem von autoscout24.de NICHT zwangsläufig, könnte
     aber andere/bessere Snippets für kleinanzeigen.de-ähnliche Domains liefern
     und so die Domain-Vielfalt organisch erhöhen. Geringster Beschaffungs-
     aufwand, unsicherster Erfolg (nicht gemessen, da kein Zugang vorhanden).

  Alle drei Optionen brauchen einen vom Nutzer zu beschaffenden API-Key/Vertrag
  — keine davon wurde hier implementiert oder vorgetäuscht.
"""

from typing import Any, Protocol


class SearchProvider(Protocol):
    """Ziel-Schnittstelle für einen austauschbaren Such-Provider. Aktuell nur von
    `app/web_search.py`s Tavily-Funktionen faktisch erfüllt (strukturell kompatibel,
    aber NICHT formal als Implementierung dieser Klasse registriert — das wäre der
    nächste, hier bewusst NICHT vorgenommene Schritt)."""

    async def search(
        self,
        query: str,
        *,
        count: int = 10,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        include_raw_content: bool = False,
        search_depth: str = "basic",
        bypass_cache: bool = False,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Gibt (ergebnisse, hatte_technischen_fehler) zurück — dieselbe Semantik
        wie `app.web_search.tavily_search_mit_status`, damit ein Aufrufer (z.B.
        `app/marktrecherche.py`) zwischen Providern wechseln kann, ohne seine
        eigene Logik zu ändern."""
        ...
