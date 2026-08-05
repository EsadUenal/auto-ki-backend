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
import re
import time
from datetime import date
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import TAVILY_API_KEY

log = logging.getLogger(__name__)

_ENDPOINT = "https://api.tavily.com/search"

# ============================================================================
# Quellenqualität: Kategorisierung, Ranking, Filterung (Final Polish)
# ============================================================================
#
# Ziel: VIRA bevorzugt hochwertige, vertrauenswürdige, möglichst offizielle
# Quellen — statt alle Tavily-Treffer gleich zu behandeln und unbegrenzt
# anzuzeigen. Reine Nachbearbeitung der Suchergebnisse (kein Einfluss auf
# Fakteninhalte) — siehe curate_results() als zentraler Einstiegspunkt.

# Social Media liefert für Kfz-Fachfragen so gut wie nie echten Mehrwert
# (keine technischen Fakten, keine geprüften Preise) — wird IMMER aus der
# Tavily-Suche ausgeschlossen, unabhängig vom aufrufenden Flow.
SOCIAL_MEDIA_AUSSCHLUSS = [
    "instagram.com", "tiktok.com", "facebook.com", "pinterest.com",
    "threads.net", "x.com", "twitter.com",
]

# US-zentrierte Auto-Portale liefern oft abweichende US-Modelljahre/
# -Ausstattungen/-Einheiten (mph, US-Gallonen) statt der hierzulande
# relevanten EU-Spezifikationen. War bisher nur in llm.py (Chat) definiert —
# jetzt zentral, damit Kaufcheck/Verkaufscheck sie ebenfalls nutzen.
US_QUELLEN_AUSSCHLUSS = [
    "caranddriver.com", "motortrend.com", "edmunds.com", "cars.com",
    "kbb.com", "autotrader.com", "consumerreports.org", "roadandtrack.com",
    "carsguide.com.au", "carexpert.com.au",
]

# ---------- Domain-Qualitätsstufen ----------
# Werte = Score-Bonus. Höher = vertrauenswürdiger/offizieller.
_TIER_AMTLICH = 50       # KBA, TÜV, DEKRA — hoheitlich/technisch geprüft
_TIER_HERSTELLER = 48    # Marken-Website des jeweiligen Herstellers
_TIER_FACHMEDIEN = 40    # ADAC, Auto Motor und Sport, AutoBild
_TIER_TECHNIK = 38       # Bosch, Hella, bekannte technische Datenbanken
_TIER_MARKTPLATZ = 32    # mobile.de, AutoScout24, AutoUncle
_TIER_NACHSCHLAGEWERK = 18   # Wikipedia u.ä. — brauchbar, aber nicht autoritativ
_TIER_COMMUNITY = 12     # Motor-Talk, Reddit — nur mit Mehrwert relevant
_TIER_NACHRICHTEN = 22   # etablierte Nachrichtenseiten (allgemein, nicht Kfz-Fachmedien)
_TIER_UNBEKANNT = 0
_TIER_GESPERRT = -1000   # Social Media — wird zusätzlich hart herausgefiltert

_AMTLICH_DOMAINS = frozenset({
    "kba.de", "tuev-sued.de", "tuvsud.com", "tuev-nord.de", "tuv.com",
    "tuev-rheinland.de", "dekra.de", "dekra-akademie.de",
})
_FACHMEDIEN_DOMAINS = frozenset({
    "adac.de", "auto-motor-und-sport.de", "ams-testcenter.de", "autobild.de",
})
_TECHNIK_DOMAINS = frozenset({
    "bosch.de", "bosch-mobility.com", "bosch-presse.de", "hella.com",
    "de.hella.com", "boschcarservice.com",
})
_MARKTPLATZ_DOMAINS = frozenset({
    "mobile.de", "autoscout24", "autouncle", "kleinanzeigen.de",
})

# Generische Sammler-/Classic-/Auktionsportale (§19) — nur als zusätzliche Discovery-
# Domain-Gruppe für die Rare-Vehicle-Ladder, kein Fahrzeug-Hardcoding. Werden NICHT
# in _MARKTPLATZ_DOMAINS geführt, da ihre Preise (Auktionsergebnisse, Sammlerpreise)
# eine andere Aussagekraft haben als ein normaler Gebrauchtwagen-Median.
_CLASSIC_AUKTION_DOMAINS = frozenset({
    "classic-trader.com", "carandclassic.com", "collectingcars.com",
    "classicdriver.com", "bringatrailer.com", "hemmings.com", "classic.com",
})

# Öffentliche Aliase für den Markt-Query-Planner (app/marktrecherche.py, §8/§19) —
# als Liste, weil Tavilys `include_domains` eine geordnete Liste erwartet.
MARKTPLATZ_DOMAINS = sorted(_MARKTPLATZ_DOMAINS)
CLASSIC_AUKTION_DOMAINS = sorted(_CLASSIC_AUKTION_DOMAINS)
_NACHSCHLAGEWERK_DOMAINS = frozenset({"wikipedia.org"})
_COMMUNITY_DOMAINS = frozenset({
    "motor-talk.de", "reddit.com",
})
_NACHRICHTEN_DOMAINS = frozenset({
    "spiegel.de", "faz.net", "sueddeutsche.de", "zeit.de", "tagesschau.de",
    "ndr.de", "focus.de", "n-tv.de", "welt.de",
})

# Herstellerseiten lassen sich nicht per fixer Domain-Liste abbilden (jede
# Marke hat ihre eigene) — stattdessen wird geprüft, ob der Markenname als
# Wortbestandteil im Domainnamen vorkommt (z.B. "bmw.de", "mercedes-benz.de",
# "volkswagen.de", "audi.de"). Deckt automatisch auch Ländervarianten ab
# (z.B. "bmw.at", "vw.co.uk").
_HERSTELLER_MARKEN = frozenset({
    "bmw", "mercedes-benz", "mercedes", "audi", "volkswagen", "vw", "opel",
    "toyota", "honda", "hyundai", "kia", "seat", "skoda", "škoda", "peugeot",
    "renault", "fiat", "volvo", "tesla", "porsche", "mazda", "subaru", "ford",
    "citroen", "citroën", "mini", "jaguar", "landrover", "land-rover", "jeep",
    "dacia", "smart", "cupra", "alfaromeo", "alfa-romeo", "suzuki", "mitsubishi",
})


# §9: Informations-/Ratgeber-/Nachschlage-Seiten sind KEINE konkreten Vergleichs-
# inserate. Ihre Preisangaben (Baujahr-Preistabellen, Steuer-/Ratgeber-Seiten,
# Konfiguratoren) dürfen NICHT in den Marktmedian einfließen — höchstens als
# Hintergrundquelle. fahrzeugschein.de ist der im Sprint konkret genannte Fall.
_INFO_NONMARKET_DOMAINS = frozenset({
    "fahrzeugschein.de", "kfz-steuer.de", "steuerklassen.com",
    "carwow.de", "carwow.co.uk", "adac.de",
})


def _domain_von(url: str) -> str:
    """Extrahiert den Domainnamen (ohne 'www.') aus einer URL."""
    try:
        netloc = urlparse(url).netloc.lower()
    except Exception:
        return ""
    return netloc[4:] if netloc.startswith("www.") else netloc


def ist_info_domain(url: str) -> bool:
    """True für Informations-/Ratgeber-/Nachschlage-Seiten, deren Preisangaben NICHT
    als konkrete Marktbeobachtung (Median-Datenpunkt) zählen dürfen (§9). Marktplätze
    (mobile.de, autoscout24, autouncle) sind bewusst NICHT enthalten."""
    domain = _domain_von(url)
    if not domain:
        return False
    return (_enthaelt_domain(domain, _INFO_NONMARKET_DOMAINS)
            or _enthaelt_domain(domain, _NACHSCHLAGEWERK_DOMAINS))


# ============================================================================
# Einzelinserat- vs. Kategorie-/Suchseiten-Erkennung (Reliability-Sprint 3, §10-§13)
# ============================================================================
#
# Tavily liefert Discovery-Treffer — häufig darunter Kategorie-/Suchergebnis-Seiten
# ("Opel Insignia gebraucht kaufen — AutoScout24"), die viele Fahrzeuge/Preise in
# EINEM Snippet mischen. Ein solches Snippet darf zwar als Recherche-/Hintergrund-
# quelle dienen, aber NICHT als eigenständiges "Vergleichsfahrzeug" gezählt werden —
# das würde Rauschen als Marktbeobachtung tarnen und künstlich Quellenvielfalt/HIGH
# erzeugen. Portalspezifische URL-Muster für konkrete Einzelinserate (empirisch aus
# echten Tavily-Treffern kalibriert, siehe scripts/diagnose_recherche.py).
_EINZELINSERAT_URL_MUSTER = (
    re.compile(r"mobile\.de/(?:fahrzeuge/details|auto)[/.].*[?&/]id=?\d+", re.I),
    re.compile(r"mobile\.de/.+-\d{9,}\.html", re.I),
    re.compile(r"autoscout24\.[a-z.]+/angebote/[^/?]+-\d+", re.I),
    re.compile(r"(?:kleinanzeigen|ebay-kleinanzeigen)\.de/s-anzeige/[^/]+/\d+", re.I),
    # AutoUncle-Detailseiten liegen real unter /de/d/<id>-<slug> (Reliability-
    # Sprint 4, empirisch per scripts/diagnose_provider_matrix.py verifiziert) —
    # die alte /car-<id>|/bil-<id>|/fahrzeug-<id>-Annahme traf das reale Format nicht.
    re.compile(r"autouncle\.[a-z.]+/.+/(?:bil-|car-|fahrzeug-|d/)\d+", re.I),
    re.compile(r"/(?:fahrzeug|angebot|inserat|listing|vehicle)[-_/][\w-]*\d{5,}", re.I),
)

# Kategorie-/Such-/Filterseiten: Domain-Root, Listen-Pfade oder Filter-Query-Parameter
# — liefern viele Fahrzeuge gemischt in einem Treffer, sind aber KEIN Einzelinserat.
_KATEGORIESEITE_URL_MUSTER = (
    re.compile(r"^/?(?:de/)?(?:gebrauchtwagen|marktplatz|fahrzeugsuche|search|angebote)/?$", re.I),
    re.compile(r"[?&](?:makeModelVariant|fromsearch|damageUnrepaired|sort)=", re.I),
)

# Generischer Kategorieseiten-Titel: "<Marke> <Modell> gebraucht kaufen — <Domain>"
# ohne konkreten Preis-/km-/EZ-Token im Titel selbst.
_KATEGORIE_TITEL_MUSTER = re.compile(
    r"gebraucht(?:wagen)?\s+kaufen\b|gebrauchtwagen\s+angebote\b|—\s*(?:auto\s?scout24|mobile\.de)",
    re.I,
)

# ── Domain-unabhängige Kategorie-/Suchseiten-Erkennung (Reliability-Sprint 4,
# §Phase 1) ───────────────────────────────────────────────────────────────────
# Der Nutzerbefund (12gebrauchtwagen.de u.ä.) zeigt: Kategorie-/Filter-/Ratgeber-
# seiten sind NICHT auf die vier festen `_MARKTPLATZ_DOMAINS` beschränkt. Statt
# eine feste Domain-Sperrliste zu pflegen ("gesamte Domain pauschal sperren" —
# ausdrücklich NICHT gewollt), wird der Seitentyp anhand generischer Pfad-/Query-
# Signale erkannt, die für JEDE Domain gelten. Eine URL mit einer langen
# ID-artigen Ziffernfolge (>=6 Ziffern) gilt als potenzielle Detailseite und wird
# NICHT pauschal als Kategorie eingestuft (konservativ: im Zweifel keine
# Kategorie-Aussage treffen, siehe ist_generische_suchseite-Docstring).
_GENERISCHE_KATEGORIE_PFAD = re.compile(
    r"/(?:gebrauchtwagen(?:-kaufen|-angebote)?|kraftstoff|marken?|marken-modelle|"
    r"modelle?|hersteller|liste|angebote|ratgeber|vergleich|preise|preisuebersicht|"
    r"test(?:bericht)?e?|krankheiten|probleme)(?:/|$|\?)",
    re.I,
)
_GENERISCHE_KATEGORIE_QUERY = re.compile(r"[?&](?:marke|modell|make|model|sort|page)=", re.I)
# Eine >=6-stellige Ziffernfolge im Pfad ist ein starkes Signal für eine konkrete
# Einzelseite (Listing-/Anzeigen-ID, Artikel-ID) — dann NICHT als Kategorie werten,
# selbst wenn ein generisches Pfadwort ebenfalls vorkommt.
_HAT_LANGE_ID = re.compile(r"[-/]\d{6,}(?:[-/]|$)")

# Empirisch belegte reine Aggregator-Domains (Reliability-Sprint 4,
# scripts/diagnose_provider_matrix.py): ALLE bisher beobachteten URLs dieser
# Domain(s) sind Modell-/Motor-/Filter-Suchseiten der Form "/auto/<marke>/
# <modell>[/<filter>]" OHNE jede Einzelinserat-ID — z.B.
# 12gebrauchtwagen.de/auto/bmw/3er, .../auto/bmw/3er/kombi, .../auto/bmw/3er/y-2020.
# KEIN pauschales Domain-Verbot (die Domain bleibt volle Discovery-/Hintergrund-
# quelle) — nur die Klassifikation "das ist keine Einzelbeobachtung" ist hier
# domain-gestützt, weil die generischen Pfad-Muster (s.o.) das reale Format
# dieser konkreten Seite nicht zuverlässig treffen (kein festes Schlüsselwort wie
# 'kraftstoff'/'gebrauchtwagen' im Pfad, sondern freie Marke/Modell-Segmente).
_BEKANNTE_AGGREGATOR_DOMAINS = frozenset({"12gebrauchtwagen.de"})


def ist_generische_suchseite(url: str, title: str | None = None) -> bool:
    """Domain-UNABHÄNGIGE Erkennung einer Kategorie-/Such-/Filter-/Ratgeberseite
    (§Phase 1). Ergänzt `ist_kategorieseite` (die nur die vier bekannten
    Marktplätze abdeckt) um generische Pfad-/Query-/Titel-Signale, die auf JEDER
    Domain gelten — z.B. 12gebrauchtwagen.de/kraftstoff/diesel/opel/insignia oder
    zweispurig.at/opel-insignia/gebrauchtwagen/liste?marke=48&modell=800.

    Konservativ: eine URL mit einer langen (>=6-stelligen) ID-Ziffernfolge im Pfad
    gilt als potenzielle Detailseite und wird NICHT als Kategorie eingestuft, auch
    wenn ein generisches Pfadwort vorkommt — im Zweifel keine Kategorie-Aussage
    treffen (bleibt dann `source_type="unknown"`, siehe marktvergleich.py)."""
    if not url:
        return False
    domain = _domain_von(url)
    try:
        pfad_und_query = url.split(domain, 1)[1] if domain else url
    except IndexError:
        pfad_und_query = url
    if _HAT_LANGE_ID.search(pfad_und_query):
        return False
    if domain and _enthaelt_domain(domain, _BEKANNTE_AGGREGATOR_DOMAINS):
        return True
    if _GENERISCHE_KATEGORIE_PFAD.search(pfad_und_query) or _GENERISCHE_KATEGORIE_QUERY.search(pfad_und_query):
        return True
    if title and _KATEGORIE_TITEL_MUSTER.search(title) and not re.search(r"\d[.,]?\d{3}\s*(?:€|eur|km)", title, re.I):
        return True
    return False


def ist_einzelinserat(url: str, title: str | None = None) -> bool:
    """True für eine URL, die strukturell auf EIN konkretes Fahrzeuginserat zeigt
    (§11). False für Kategorie-/Such-/Info-Seiten oder wenn unklar (konservativ:
    im Zweifel KEIN Einzelinserat — zählt dann nicht Richtung HIGH, bleibt aber als
    Hintergrundquelle erhalten, siehe marktvergleich.py)."""
    if not url:
        return False
    domain = _domain_von(url)
    if not domain or not _enthaelt_domain(domain, _MARKTPLATZ_DOMAINS):
        # Nur für bekannte Fahrzeugmarktplätze bewertbar — alles andere (Foren,
        # Presseartikel, ...) ist per Definition kein Marktplatz-Einzelinserat.
        return False
    try:
        pfad_und_query = url.split(domain, 1)[1]
    except IndexError:
        pfad_und_query = url
    if any(m.search(pfad_und_query) for m in _KATEGORIESEITE_URL_MUSTER):
        return False
    if title and _KATEGORIE_TITEL_MUSTER.search(title) and not re.search(r"\d[.,]?\d{3}\s*(?:€|eur|km)", title, re.I):
        return False
    return any(m.search(url) for m in _EINZELINSERAT_URL_MUSTER)


def ist_kategorieseite(url: str, title: str | None = None) -> bool:
    """True für eine erkennbare Kategorie-/Such-/Übersichts-/Ratgeberseite (§Phase 1)
    — Gegenstück zu `ist_einzelinserat`. Deckt sowohl die vier bekannten Marktplätze
    (marktplatzspezifische URL-/Titel-Muster) als auch JEDE ANDERE Domain ab
    (generische, domain-unabhängige Signale via `ist_generische_suchseite` — §Phase
    1: "keine Domain pauschal sperren, aber Seitentyp klassifizieren")."""
    if not url:
        return False
    if ist_generische_suchseite(url, title):
        return True
    domain = _domain_von(url)
    if not domain or not _enthaelt_domain(domain, _MARKTPLATZ_DOMAINS):
        return False
    if ist_einzelinserat(url, title):
        return False
    try:
        pfad_und_query = url.split(domain, 1)[1]
    except IndexError:
        pfad_und_query = url
    if any(m.search(pfad_und_query) for m in _KATEGORIESEITE_URL_MUSTER):
        return True
    if title and _KATEGORIE_TITEL_MUSTER.search(title):
        return True
    return False


def _ist_herstellerseite(domain: str) -> bool:
    """Prüft ob eine Domain zu einer bekannten Automarke gehört (z.B. bmw.de)."""
    kern = domain.split(".")[0] if domain else ""
    kern = kern.replace("-", "")
    return any(kern == marke.replace("-", "") for marke in _HERSTELLER_MARKEN)


def _enthaelt_domain(domain: str, gruppe: frozenset[str]) -> bool:
    """
    Teilstring-Match statt exaktem Vergleich — deckt Subdomains (z.B.
    'suchen.mobile.de' für 'mobile.de') und länderspezifische TLD-Varianten
    (z.B. 'autoscout24.at' für den Kern-Token 'autoscout24') gleichermaßen ab.
    """
    return any(eintrag in domain for eintrag in gruppe)


# ---------- Themen-Kategorien ----------
# Bestimmt, welche Domain-Gruppen für eine Fragestellung zusätzlich geboostet
# werden (z.B. Marktplätze bei Preisfragen, KBA/Hersteller bei Rückrufen).
KATEGORIE_TECHNISCHE_DATEN = "technische_daten"
KATEGORIE_MARKTPREISE      = "marktpreise"
KATEGORIE_RUECKRUFE        = "rueckrufe"
KATEGORIE_SCHWACHSTELLEN   = "schwachstellen"
KATEGORIE_WARTUNG          = "wartung"
KATEGORIE_DIAGNOSE         = "diagnose"

# Kategorie -> zusätzlich geboostete Domain-Gruppen (siehe Anforderung: Quellen
# sollen thematisch passend ausgewählt werden, nicht pauschal gleich behandelt).
_KATEGORIE_BOOST: dict[str, tuple[frozenset[str], ...]] = {
    KATEGORIE_TECHNISCHE_DATEN: (_TECHNIK_DOMAINS,),
    KATEGORIE_MARKTPREISE:      (_MARKTPLATZ_DOMAINS,),
    KATEGORIE_RUECKRUFE:        (_AMTLICH_DOMAINS,),
    KATEGORIE_SCHWACHSTELLEN:   (_FACHMEDIEN_DOMAINS, _COMMUNITY_DOMAINS),
    KATEGORIE_WARTUNG:          (_TECHNIK_DOMAINS,),
    KATEGORIE_DIAGNOSE:         (_TECHNIK_DOMAINS, _FACHMEDIEN_DOMAINS),
}
_KATEGORIE_BOOST_WERT = 15


def score_domain(url: str, kategorie: str | None = None) -> int:
    """Bewertet die Vertrauenswürdigkeit/Offizialität einer Quelle. Höher = besser."""
    domain = _domain_von(url)
    if not domain:
        return _TIER_UNBEKANNT

    if any(sperr in domain for sperr in SOCIAL_MEDIA_AUSSCHLUSS):
        return _TIER_GESPERRT

    if _enthaelt_domain(domain, _AMTLICH_DOMAINS):
        score = _TIER_AMTLICH
    elif _ist_herstellerseite(domain):
        score = _TIER_HERSTELLER
    elif _enthaelt_domain(domain, _FACHMEDIEN_DOMAINS):
        score = _TIER_FACHMEDIEN
    elif _enthaelt_domain(domain, _TECHNIK_DOMAINS):
        score = _TIER_TECHNIK
    elif _enthaelt_domain(domain, _MARKTPLATZ_DOMAINS):
        score = _TIER_MARKTPLATZ
    elif _enthaelt_domain(domain, _NACHSCHLAGEWERK_DOMAINS):
        score = _TIER_NACHSCHLAGEWERK
    elif _enthaelt_domain(domain, _COMMUNITY_DOMAINS):
        score = _TIER_COMMUNITY
    elif _enthaelt_domain(domain, _NACHRICHTEN_DOMAINS):
        score = _TIER_NACHRICHTEN
    else:
        score = _TIER_UNBEKANNT

    if kategorie:
        for gruppe in _KATEGORIE_BOOST.get(kategorie, ()):
            if _enthaelt_domain(domain, gruppe):
                score += _KATEGORIE_BOOST_WERT
                break

    return score


def _normalisiere_url(url: str) -> str:
    """Für Duplikat-Erkennung: ohne Query-String/Fragment/trailing Slash."""
    try:
        p = urlparse(url)
        pfad = p.path.rstrip("/")
        return f"{p.netloc.lower()}{pfad}".removeprefix("www.")
    except Exception:
        return url


def curate_results(
    results: list[dict[str, Any]],
    kategorie: str | None = None,
    max_results: int = 5,
) -> list[dict[str, Any]]:
    """
    Zentrale Nachbearbeitung roher Tavily-Treffer für alle vier Flows
    (Chat, Diagnose, Kaufcheck, Verkaufscheck):

      1. Social Media hart herausfiltern (unabhängig von exclude_domains,
         als zweites Sicherheitsnetz — siehe SOCIAL_MEDIA_AUSSCHLUSS)
      2. Exakte URL-Duplikate entfernen
      3. Near-Duplikate entfernen (gleiche Domain + gleicher Titel)
      4. Nach Quellenqualität sortieren (score_domain, thematisch geboostet)
      5. Auf max_results kürzen (Standard 5 — "nicht mehr Quellen als nötig")

    Ändert NICHT den Inhalt (title/content/url) einzelner Treffer — nur
    Auswahl und Reihenfolge.
    """
    if not results:
        return []

    gesehen_urls: set[str] = set()
    gesehen_domain_titel: set[tuple[str, str]] = set()
    bereinigt: list[dict[str, Any]] = []

    for r in results:
        url = r.get("url", "")
        domain = _domain_von(url)
        if not domain or any(sperr in domain for sperr in SOCIAL_MEDIA_AUSSCHLUSS):
            continue

        url_key = _normalisiere_url(url)
        if url_key in gesehen_urls:
            continue

        titel_key = (domain, (r.get("title") or "").strip().lower())
        if titel_key in gesehen_domain_titel:
            continue

        gesehen_urls.add(url_key)
        gesehen_domain_titel.add(titel_key)
        bereinigt.append(r)

    # Stabile Sortierung nach Score (bei Gleichstand bleibt Tavily-Relevanz-
    # Reihenfolge erhalten, da Python sort() stabil ist).
    bereinigt.sort(key=lambda r: score_domain(r.get("url", ""), kategorie), reverse=True)

    return bereinigt[:max_results]

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


# Query-Version (§Phase 5): bei Änderungen an der Extraktions-/Klassifikations-
# Logik (z.B. neue source_type-Kategorien), die dieselbe Tavily-Antwort anders
# bewerten würden, hochzählen — damit alte Cache-Einträge aus VOR der Änderung
# nicht unbemerkt weiterverwendet werden, obwohl sie heute anders klassifiziert
# würden. Fließt in den Cache-Key ein (siehe _cache_key).
QUERY_VERSION = 2


def _cache_key(query: str, count: int, include_domains, exclude_domains,
               include_raw_content=False, search_depth: str = "basic") -> tuple:
    return (
        QUERY_VERSION,
        query,
        count,
        tuple(include_domains) if include_domains else None,
        tuple(exclude_domains) if exclude_domains else None,
        bool(include_raw_content),
        search_depth,
    )


async def _tavily_search_intern(
    query: str,
    count: int = 5,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    include_raw_content: bool = False,
    bypass_cache: bool = False,
    search_depth: str = "basic",
) -> tuple[list[dict[str, Any]], bool]:
    """Interner Kern von `tavily_search`/`tavily_search_mit_status` (Reliability-
    Sprint 3, §21/§22). Gibt (ergebnisse, hatte_technischen_fehler) zurück —
    `hatte_technischen_fehler=True` NUR bei echtem Netzwerk-/API-Ausfall (nach
    Ausschöpfen der Retries), NICHT bei einer legitim leeren Trefferliste (200 OK,
    einfach nichts gefunden). Diese Unterscheidung ist die Grundlage für
    `research_status`s technical_failure- vs. data_exhausted-Semantik (§21).

    `bypass_cache=True` (Retry, §22): überspringt NUR das Cache-LESEN — ein frischer
    Request wird ausgeführt und das Ergebnis danach normal gecacht (kommt allen
    späteren, nicht-bypassenden Aufrufern zugute). Verhindert, dass ein Retry exakt
    dieselbe (ggf. dünne) gecachte Antwort wiederverwendet.
    """
    if not TAVILY_API_KEY:
        log.debug("Websuche übersprungen: TAVILY_API_KEY nicht gesetzt.")
        return [], True   # kein API-Key = technischer Ausfall, keine Datenknappheit

    # Social Media grundsätzlich ausschließen (spart Tavily-Ergebnis-Slots für
    # tatsächlich brauchbare Quellen) — zusätzlich zu vom Aufrufer übergebenen
    # Domains, unabhängig davon ob der Aufrufer daran denkt. curate_results()
    # filtert sie zur Sicherheit trotzdem nochmal heraus.
    exclude_domains = list({*(exclude_domains or []), *SOCIAL_MEDIA_AUSSCHLUSS})

    key = _cache_key(query, count, include_domains, exclude_domains, include_raw_content, search_depth)
    if not bypass_cache:
        cached = _cache.get(key)
        if cached is not None and (time.monotonic() - cached[0]) < _CACHE_TTL_S:
            log.debug("Tavily Cache-Treffer für %r", query[:80])
            return cached[1], False

    body: dict[str, Any] = {
        "api_key":      TAVILY_API_KEY,
        "query":        query,
        # search_depth: "basic" (1 Credit) ist der Produktions-Default (Sprint-3-
        # Messung: advanced brachte für populäre Fahrzeuge keinen Mehrwert). Ein
        # Aufrufer kann gezielt "advanced" anfordern (Diagnose-Skript, ggf. eine
        # spätere validierende Such-Stufe je Phase-0/13-Entscheidung).
        "search_depth": search_depth,
        "max_results":  count,
        "include_answer":     False,
        "include_images":     False,
        # Raw-Content nur wenn explizit angefordert (§2 Punkt 8): liefert mehr Text
        # pro Treffer -> mehr extrahierbare Preis-Datenpunkte, wenn die Snippets für
        # eine belastbare Marktbasis nicht reichen.
        "include_raw_content": bool(include_raw_content),
    }
    if include_domains:
        body["include_domains"] = include_domains
    if exclude_domains:
        body["exclude_domains"] = exclude_domains

    results: list[dict[str, Any]] = []
    hatte_fehler = False
    for versuch in range(_MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(_ENDPOINT, json=body)
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])
                log.info("Tavily Search: %d Ergebnisse für %r", len(results), query[:80])
                hatte_fehler = False
                break

        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            transient = status_code == 429 or status_code >= 500
            hatte_fehler = True
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
            hatte_fehler = True
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
    return results, hatte_fehler


async def tavily_search(
    query: str,
    count: int = 5,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    include_raw_content: bool = False,
    bypass_cache: bool = False,
    search_depth: str = "basic",
) -> list[dict[str, Any]]:
    """
    Ruft die Tavily Search API auf (POST, JSON-Body).

    Gibt [] zurück wenn:
      - TAVILY_API_KEY nicht gesetzt (Websuche deaktiviert)
      - Netzwerkfehler oder API-Fehler (nie weitergeworfen)

    search_depth="basic" (Default, 1 Credit): Produktions-Standard. "advanced"
    (2 Credits) kann gezielt angefordert werden — siehe Provider-Messung
    (scripts/diagnose_recherche.py) für die Entscheidungsgrundlage.
    include_domains: optional — beschränkt die Suche auf bestimmte Shops/Quellen.
    exclude_domains: optional — blendet z.B. US-zentrierte Auto-Portale aus,
      deren Modelljahre/Ausstattungen vom europäischen Markt abweichen.
    bypass_cache: optional (§22) — überspringt das Cache-Lesen für einen echten
      Retry statt derselben (ggf. dünnen) gecachten Antwort.

    Für die feinere technical_failure-/data_exhausted-Unterscheidung (§21) siehe
    `tavily_search_mit_status`.
    """
    ergebnisse, _hatte_fehler = await _tavily_search_intern(
        query, count, include_domains, exclude_domains, include_raw_content, bypass_cache, search_depth)
    return ergebnisse


async def tavily_search_mit_status(
    query: str,
    count: int = 5,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    include_raw_content: bool = False,
    bypass_cache: bool = False,
    search_depth: str = "basic",
) -> tuple[list[dict[str, Any]], bool]:
    """Wie `tavily_search`, gibt zusätzlich zurück, ob ein ECHTER technischer Fehler
    auftrat (API-Key fehlt, Netzwerk-/HTTP-Fehler nach Retries) — im Unterschied zu
    einer legitim leeren Trefferliste. Nutzt die adaptive Marktrecherche (§21), um
    `research_status` sauber zwischen "technical_failure" und "data_exhausted" zu
    trennen."""
    return await _tavily_search_intern(
        query, count, include_domains, exclude_domains, include_raw_content, bypass_cache, search_depth)


_ENDPOINT_EXTRACT = "https://api.tavily.com/extract"


async def tavily_extract(urls: list[str], *, advanced: bool = False) -> list[dict[str, Any]]:
    """Ruft die Tavily Extract API für konkrete URLs auf (Provider-Messung §Phase 0
    /13 — voller Seiteninhalt statt nur Snippet). NUR für das Diagnose-Skript und
    eine spätere, explizit entschiedene Produktions-Integration (§13) — nicht
    standardmäßig in der Kaufcheck-/Verkaufscheck-Pipeline verdrahtet.

    Gibt eine Liste von {"url", "raw_content", "erfolg"} zurück; fehlgeschlagene
    URLs liefern "raw_content": None statt eine Exception zu werfen (Aufrufer
    entscheidet selbst, wie streng er mit Teilausfällen umgeht).
    """
    if not TAVILY_API_KEY or not urls:
        return []
    body: dict[str, Any] = {
        "api_key":       TAVILY_API_KEY,
        "urls":          urls,
        "extract_depth": "advanced" if advanced else "basic",
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(_ENDPOINT_EXTRACT, json=body)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.warning("Tavily Extract Fehler (%s): %s", type(exc).__name__, exc)
        return [{"url": u, "raw_content": None, "erfolg": False} for u in urls]

    ok = {r.get("url"): r.get("raw_content") for r in data.get("results", [])}
    failed = {r.get("url") for r in data.get("failed_results", [])}
    out = []
    for u in urls:
        if u in ok:
            out.append({"url": u, "raw_content": ok[u], "erfolg": True})
        else:
            out.append({"url": u, "raw_content": None, "erfolg": u not in failed})
    return out


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


def _qualitaets_label(url: str) -> str:
    """Kurzes, nutzerverständliches Label für die Quellenanzeige (kein
    Entwicklerbegriff wie 'Tier 1' — siehe Chat-Stilregeln)."""
    domain = _domain_von(url)
    if _enthaelt_domain(domain, _AMTLICH_DOMAINS):
        return "Amtlich/Prüforganisation"
    if _ist_herstellerseite(domain):
        return "Hersteller"
    if _enthaelt_domain(domain, _FACHMEDIEN_DOMAINS):
        return "Fachmedien"
    if _enthaelt_domain(domain, _TECHNIK_DOMAINS):
        return "Technik-Hersteller"
    if _enthaelt_domain(domain, _MARKTPLATZ_DOMAINS):
        return "Marktplatz"
    if _enthaelt_domain(domain, _NACHSCHLAGEWERK_DOMAINS):
        return "Nachschlagewerk"
    if _enthaelt_domain(domain, _COMMUNITY_DOMAINS):
        return "Community/Erfahrungsbericht"
    if _enthaelt_domain(domain, _NACHRICHTEN_DOMAINS):
        return "Nachrichten"
    return "Sonstige Quelle"


def results_to_belege(results: list[dict]) -> list[dict]:
    """
    Erstellt die `belege`-Liste für die API-Antwort.

    Jeder Beleg enthält typ="web", titel, url, snippet, abgerufen, qualitaet.
    Das Frontend kann diese URLs direkt als klickbare Quell-Links anzeigen;
    `qualitaet` erlaubt eine professionellere Darstellung (z.B. Badge/Icon
    statt einer undifferenzierten URL-Liste).
    """
    heute = date.today().isoformat()
    return [
        {
            "typ":       "web",
            "titel":     (r.get("title") or "")[:120],
            "url":       r.get("url", ""),
            "snippet":   (r.get("content") or "")[:200],
            "abgerufen": heute,
            "qualitaet": _qualitaets_label(r.get("url", "")),
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
