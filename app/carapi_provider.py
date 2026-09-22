from __future__ import annotations

"""
CarAPI.dev: externe Marktorientierung fuer den VerkaufsCheck (RC1).

WAS DIESER ADAPTER LIEFERT
---------------------------
Zwei normalisierte Antworten, beide ohne Rohdaten des Providers:

  `hole_bewertung`     GET /v1/vehicle-valuation  -> `Marktbewertung`
  `hole_marktdauer`    GET /v1/time-to-sell       -> `Marktdauer`

Der VerkaufsCheck spricht NUR mit diesen beiden Funktionen. Welche Parameter
CarAPI erwartet, wie Marken/Modelle heissen und welche Fehlerbilder es gibt,
bleibt hier gekapselt.

WAS DIE ZAHLEN BEDEUTEN (und was nicht)
-----------------------------------------
* `valuationPrice` ist EIN Orientierungswert fuer das BASISMODELL ("golf",
  "3-series"). CarAPI dokumentiert ausdruecklich, dass Bewertungen je
  Basismodell gefuehrt werden; Ausstattungslinien wie GTI oder 320d sind keine
  eigene Stufe. Leistung, Kraftstoff und Laufleistung verfeinern den Wert,
  machen ihn aber nicht zur Bewertung genau dieser Variante. Es gibt keine
  Stichprobengroesse, keinen Median, keine Quartile. Deshalb erfindet der
  Adapter auch keine Spanne.
* Time-to-Sell beschreibt, wie lange vergleichbare Inserate online bleiben, bis
  sie verschwinden. Das umfasst Verkaeufe, aber auch zurueckgezogene und
  abgelaufene Anzeigen. Es ist keine Verkaufsdauer.

BETRIEB
--------
* Nur aktiv, wenn AUTO_KI_CARAPI_ERLAUBT=1 UND ein Key gesetzt ist. Default AUS,
  bis die schriftliche Nutzungsfreigabe vorliegt.
* Genau EIN Versuch pro Call, keine Retries (auch 404 kostet laut Doku einen
  Credit). Budget pro VerkaufsCheck ueber provider_control ("carapi": 2).
* Der Token wird als Query-Parameter uebertragen (so dokumentiert). Deshalb
  wird hier NIE eine URL oder ein Exception-Text geloggt, und der httpx-Logger
  (der Request-URLs auf INFO schreibt) wird auf WARNING gehalten.
* Kurzzeit-Cache im Speicher (6 h, Terms erlauben hoechstens 24 h), ohne Token
  im Schluessel. Keine Persistenz in der Datenbank.
"""

import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from app import config
from app.provider_control import ProviderCallLimitExceeded, claim_call, log_provider_event

log = logging.getLogger(__name__)
# httpx schreibt auf INFO "HTTP Request: GET <url>", die URL enthaelt den Token.
logging.getLogger("httpx").setLevel(max(logging.WARNING, logging.getLogger("httpx").level))

PROVIDER = "carapi"
_BASIS_URL = "https://api.carapi.dev/v1"
_LAND = "DE"
_CACHE_TTL_S = 6 * 3600
_cache: dict[tuple, tuple[float, Any]] = {}

STATUS_OK = "ok"
STATUS_DEAKTIVIERT = "deaktiviert"          # Schalter aus oder kein Key
STATUS_NICHT_VERFUEGBAR = "nicht_verfuegbar"  # 404 / zu wenig Daten / nicht abbildbar
STATUS_FEHLER = "fehler"                    # Timeout, Netz, 403, 429, 5xx, kaputte Antwort


@dataclass
class Anfrage:
    """Die an CarAPI gesendeten Fahrzeugparameter (ohne Token)."""
    make: str
    model: str
    year: int | None = None
    fuel: str | None = None
    kw: int | None = None
    mileage: int | None = None

    def params(self) -> dict[str, Any]:
        p = {"make": self.make, "model": self.model, "country": _LAND}
        for k in ("year", "fuel", "kw", "mileage"):
            v = getattr(self, k)
            if v is not None:
                p[k] = v
        return p


@dataclass
class Marktbewertung:
    provider: str = PROVIDER
    status: str = STATUS_DEAKTIVIERT
    grund: str | None = None
    wert_eur: int | None = None          # valuationPrice, ungerundet (nur intern)
    waehrung: str | None = None
    land: str = _LAND
    angefragt: dict[str, Any] = field(default_factory=dict)
    aufgeloest: dict[str, Any] = field(default_factory=dict)   # make/model laut Provider
    filter: list[str] = field(default_factory=list)            # zurueckgemeldete Filter
    spezifitaet: str = "basismodell"
    abgerufen_am: str | None = None

    def als_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Marktdauer:
    provider: str = PROVIDER
    status: str = STATUS_DEAKTIVIERT
    grund: str | None = None
    median_tage: int | None = None
    p25_tage: int | None = None
    p75_tage: int | None = None
    land: str = _LAND
    angefragt: dict[str, Any] = field(default_factory=dict)
    aufgeloest: dict[str, Any] = field(default_factory=dict)
    abgerufen_am: str | None = None

    def als_dict(self) -> dict[str, Any]:
        return asdict(self)


def ist_aktiv() -> bool:
    """CarAPI darf nur mit Schalter UND Key laufen."""
    return bool(config.CARAPI_ERLAUBT and config.CARAPI_API_KEY)


# ── Mapping ENFAL -> CarAPI ────────────────────────────────────────────────────

_MARKEN = {
    "vw": "volkswagen", "volkswagen": "volkswagen", "mercedes": "mercedes-benz",
    "mercedes-benz": "mercedes-benz", "mercedes benz": "mercedes-benz",
    "skoda": "skoda", "škoda": "skoda", "citroën": "citroen", "citroen": "citroen",
    "alfa romeo": "alfa-romeo", "land rover": "land-rover",
}

_KRAFTSTOFF = {
    "benzin": "petrol", "super": "petrol", "petrol": "petrol",
    "diesel": "diesel",
    "elektro": "electric", "electric": "electric",
    "hybrid": "hybrid", "plug-in-hybrid": "hybrid",
    "lpg": "lpg", "autogas": "lpg", "cng": "cng", "erdgas": "cng",
    "wasserstoff": "hydrogen",
}


def carapi_marke(marke: str | None) -> str | None:
    m = (marke or "").strip().lower()
    if not m:
        return None
    return _MARKEN.get(m, re.sub(r"\s+", "-", m))


def carapi_modell(modell: str | None) -> str | None:
    """Basismodell im CarAPI-Schema. Erwartet den DB-Modellnamen der Baureihe
    ("Golf", "3er", "C-Klasse"), NICHT die Nutzereingabe mit Ausstattungslinie."""
    m = (modell or "").strip().lower()
    if not m:
        return None
    treffer = re.fullmatch(r"(\d)\s*er", m)
    if treffer:                                      # BMW 3er -> 3-series
        return f"{treffer.group(1)}-series"
    treffer = re.fullmatch(r"([a-z]{1,3})\s*-?\s*klasse", m)
    if treffer:                                      # C-Klasse -> c-class
        return f"{treffer.group(1)}-class"
    return re.sub(r"\s+", "-", m)


def carapi_kraftstoff(kraftstoff: str | None) -> str | None:
    """Nur eindeutige Angaben; "LPG/CNG" ist mehrdeutig und bleibt ungesetzt."""
    k = (kraftstoff or "").strip().lower()
    return _KRAFTSTOFF.get(k)


def kw_aus_ps(ps: int | None) -> int | None:
    return round(ps * 0.73549875) if ps else None


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def _get(pfad: str, anfrage: Anfrage) -> tuple[str, dict | None, str | None]:
    """Ein einziger GET. Rueckgabe (status, json|None, grund|None).

    Loggt NIE die URL, den Token oder einen Exception-Text (der bei httpx die
    URL enthaelt), nur Klasse und Statuscode.
    """
    schluessel = (pfad, tuple(sorted(anfrage.params().items())))
    treffer = _cache.get(schluessel)
    if treffer and time.monotonic() - treffer[0] < _CACHE_TTL_S:
        return treffer[1]

    gestartet = time.monotonic()
    try:
        claim_call(PROVIDER)
    except ProviderCallLimitExceeded:
        return STATUS_FEHLER, None, "budget"

    params = {**anfrage.params(), "token": config.CARAPI_API_KEY}
    timeout = httpx.Timeout(config.CARAPI_TIMEOUT_SECONDS)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{_BASIS_URL}/{pfad}", params=params)
    except httpx.TimeoutException:
        log_provider_event(PROVIDER, status="error", error_class="timeout", attempt=1, started=gestartet)
        return STATUS_FEHLER, None, "timeout"
    except httpx.HTTPError as exc:
        log_provider_event(PROVIDER, status="error", error_class="network", attempt=1, started=gestartet)
        log.warning("CarAPI %s Netzwerkfehler (%s)", pfad, type(exc).__name__)
        return STATUS_FEHLER, None, "netzwerk"

    code = resp.status_code
    if code == 404:
        ergebnis = (STATUS_NICHT_VERFUEGBAR, None, "keine_daten")
        log_provider_event(PROVIDER, status="success", attempt=1, started=gestartet)
        _cache[schluessel] = (time.monotonic(), ergebnis)   # 404 kostet auch einen Credit
        return ergebnis
    if code != 200:
        klasse = {400: "ungueltige_anfrage", 403: "zugang", 429: "rate_limit"}.get(
            code, "provider_5xx" if code >= 500 else "http")
        log_provider_event(PROVIDER, status="error", error_class=klasse, attempt=1, started=gestartet)
        log.warning("CarAPI %s HTTP %s", pfad, code)
        return STATUS_FEHLER, None, klasse
    try:
        daten = resp.json()
        if not isinstance(daten, dict):
            raise ValueError("kein Objekt")
    except ValueError:
        log_provider_event(PROVIDER, status="error", error_class="invalid_response", attempt=1, started=gestartet)
        return STATUS_FEHLER, None, "ungueltige_antwort"
    log_provider_event(PROVIDER, status="success", attempt=1, started=gestartet)
    ergebnis = (STATUS_OK, daten, None)
    if len(_cache) >= 500:
        _cache.pop(min(_cache, key=lambda k: _cache[k][0]), None)
    _cache[schluessel] = (time.monotonic(), ergebnis)
    return ergebnis


def _ganzzahl(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and v >= 0:
        return int(round(v))
    return None


# ── Oeffentliche Funktionen ───────────────────────────────────────────────────

async def hole_bewertung(anfrage: Anfrage | None) -> Marktbewertung:
    """Valuation normalisieren. Wirft nie; jeder Ausfall ist ein Status."""
    if not ist_aktiv():
        return Marktbewertung(status=STATUS_DEAKTIVIERT, grund="nicht_freigegeben")
    if anfrage is None or not anfrage.year:
        return Marktbewertung(status=STATUS_NICHT_VERFUEGBAR, grund="fahrzeug_nicht_abbildbar")
    status, daten, grund = await _get("vehicle-valuation", anfrage)
    b = Marktbewertung(status=status, grund=grund, angefragt=anfrage.params(), abgerufen_am=_jetzt())
    if status != STATUS_OK:
        return b
    wert = _ganzzahl(daten.get("valuationPrice"))
    waehrung = str(daten.get("currency") or "").upper() or None
    if not wert or wert < 300 or waehrung != "EUR":
        # Kein Wert, Unsinnswert oder fremde Waehrung: lieber keine Orientierung.
        b.status, b.grund = STATUS_NICHT_VERFUEGBAR, "kein_verwertbarer_wert"
        return b
    b.wert_eur, b.waehrung = wert, waehrung
    b.aufgeloest = {"make": daten.get("make"), "model": daten.get("model"), "year": daten.get("year")}
    # Nur Filter, die CarAPI zurueckmeldet, gelten als angewendet.
    b.filter = [k for k in ("fuel", "kw", "mileage") if daten.get(k) is not None]
    return b


async def hole_marktdauer(anfrage: Anfrage | None) -> Marktdauer:
    """Time-to-Sell normalisieren. Wirft nie; jeder Ausfall ist ein Status."""
    if not ist_aktiv():
        return Marktdauer(status=STATUS_DEAKTIVIERT, grund="nicht_freigegeben")
    if anfrage is None:
        return Marktdauer(status=STATUS_NICHT_VERFUEGBAR, grund="fahrzeug_nicht_abbildbar")
    status, daten, grund = await _get("time-to-sell", anfrage)
    d = Marktdauer(status=status, grund=grund, angefragt=anfrage.params(), abgerufen_am=_jetzt())
    if status != STATUS_OK:
        return d
    med, p25, p75 = (_ganzzahl(daten.get(k)) for k in ("medianDaysToSell", "p25Days", "p75Days"))
    if med is None or p25 is None or p75 is None or not (p25 <= med <= p75):
        d.status, d.grund = STATUS_NICHT_VERFUEGBAR, "kein_verwertbarer_wert"
        return d
    d.median_tage, d.p25_tage, d.p75_tage = med, p25, p75
    d.aufgeloest = {"make": daten.get("make"), "model": daten.get("model")}
    return d


def baue_anfrage(marke: str | None, db_modell: str | None, baujahr: int | None,
                 kraftstoff: str | None, kw: int | None, kilometerstand: int | None) -> Anfrage | None:
    """Anfrage aus BELASTBAR erkannten Daten. Ohne Marke/Basismodell: None."""
    make, model = carapi_marke(marke), carapi_modell(db_modell)
    if not make or not model:
        return None
    return Anfrage(make=make, model=model, year=baujahr or None,
                   fuel=carapi_kraftstoff(kraftstoff),
                   kw=kw if kw and 0 < kw <= 2000 else None,
                   mileage=kilometerstand if kilometerstand and 0 < kilometerstand <= 1_000_000 else None)
