from __future__ import annotations

"""
MobileDeProvider — mobile.de Search API, AUSSCHLIESSLICH SANDBOX (Etappe 3).

╔════════════════════════════════════════════════════════════════════════════╗
║ SANDBOX ONLY. Dieser Provider ist NIRGENDS in die Produktionspipeline      ║
║ verdrahtet. Er wird von `kaufcheck`/`verkaufscheck`/`marktrecherche` nicht ║
║ importiert und nicht aufgerufen. Zusaetzlich verweigert er den Dienst,     ║
║ wenn die Basis-URL nicht erkennbar eine Sandbox-URL ist (siehe             ║
║ `ist_sandbox_url`) — ein versehentlicher Produktivlauf soll technisch      ║
║ scheitern, nicht nur per Konvention unterbleiben.                          ║
║                                                                            ║
║ Unabhaengig davon gilt weiterhin die Source-Policy: mobile.de steht NICHT  ║
║ in `ALLOWED_MARKET_SOURCES` (Production-Default leer). `_bewerte` verwirft ║
║ Beobachtungen dieses Providers daher mit SOURCE_POLICY_GRUND, solange die  ║
║ Quelle nicht ausdruecklich freigegeben ist. Das ist gewollt: Etappe 2 ist  ║
║ PENDING (Nutzungsrechte/Vertrag beim Anbieter offen).                      ║
╚════════════════════════════════════════════════════════════════════════════╝

Zugangsdaten kommen ausschliesslich aus der Umgebung:
    MOBILE_DE_BASE_URL, MOBILE_DE_USERNAME, MOBILE_DE_PASSWORD
Sie werden nie geloggt, nie in Fehlermeldungen aufgenommen und nie im
Rueckgabewert weitergereicht.

── Empirisch gegen die Sandbox verifizierte API-Eigenheiten ─────────────────

Alles Folgende wurde gemessen, nicht aus der Dokumentation abgeleitet. Mehrere
Punkte weichen von der oeffentlichen Doku bzw. von naheliegenden Annahmen ab:

  1. UNBEKANNTE FILTERPARAMETER WERDEN STILL IGNORIERT. `?make=VW&model=Golf`
     liefert HTTP 200 und die unveraenderte Gesamttrefferzahl (19.298 statt
     742). Ein Tippfehler im Parameternamen fuehrt also NICHT zu einem Fehler,
     sondern zu einer klammheimlich ungefilterten Suche. Deshalb wird hier
     ausschliesslich mit nachgemessenen Parameternamen gearbeitet.
  2. Marke/Modell laufen ueber `classification`, nicht ueber eigene Parameter:
     `refdata/classes/Car/makes/<MAKE>/models/<MODEL>`.
  3. Die Schluessel darin sind mobile.de-Refdata-Schluessel, nicht Klartext:
     `VW` ist gueltig, `VOLKSWAGEN` gibt HTTP 400. `325` ist gueltig, `3ER`
     gibt HTTP 400. Ein unbekannter Schluessel ist also ein harter Fehler, kein
     leeres Ergebnis — deshalb wird ueber `/refdata` aufgeloest statt geraten.
  4. mobile.de fuehrt Modelle motorbezogen ("320", "318"), VIRA denkt in
     Baureihen ("3er"). Die Aufloesung muss das ueberbruecken oder ehrlich auf
     Marke-only zurueckfallen.
  5. `firstRegistrationDate.min` erwartet `"YYYY-MM"` MIT Bindestrich — obwohl
     das Antwortfeld `firstRegistration` `"YYYYMM"` OHNE Bindestrich liefert.
  6. `power` ist ein nackter int in kW ohne Einheitsfeld.
  7. `price.consumerPriceGross` ist ein Dezimalstring, daneben steht `currency`.
"""

import asyncio
import logging
import os
from typing import Any

import httpx

from app.market_data_provider import (
    EXTRACTION_SOURCE_API,
    SEGMENTATION_METHOD_API,
    evidenztext,
)
from app.models import Preisbeobachtung
from app.vehicle_identity import VehicleIdentity

log = logging.getLogger(__name__)

_TIMEOUT_S = 20.0
_ACCEPT = "application/vnd.de.mobile.api+json"
# mobile.de deckelt die Seitengroesse bei 100 (dokumentiert).
_MAX_PAGE_SIZE = 100

# kW -> PS. `Preisbeobachtung.horsepower` ist PS (vgl. `_RE_PS` in
# app/marktvergleich.py, das auf "<zahl> ps" matcht).
_KW_ZU_PS = 1.35962


# ── Enum-Mappings ────────────────────────────────────────────────────────────
# Grundsatz: nur eindeutige Zuordnungen. Ein unbekannter oder mehrdeutiger Wert
# wird zu None (= unbekannt) und fuehrt NIE zu einem Fehler. "Unknown bleibt
# unknown" ist dieselbe Regel, die Etappe 1 fuer die Generation durchgesetzt hat.

_FUEL_MAP: dict[str, str] = {
    # In der Sandbox beobachtet:
    "DIESEL": "diesel",
    "PETROL": "benzin",
    # Nicht in der Sandbox beobachtet (kein E-/Hybrid-Testinserat vorhanden),
    # aber in VIRAs Kraftstoffvokabular eindeutig abbildbar. Durch Tests
    # abgesichert, damit ein spaeterer Realdatensatz nicht stillschweigend
    # als "unbekannt" durchfaellt.
    "ELECTRICITY": "elektro",
    "HYBRID": "hybrid",
    "HYBRID_DIESEL": "hybrid",
}

_GEARBOX_MAP: dict[str, str] = {
    "AUTOMATIC_GEAR": "automatik",
    "MANUAL_GEAR": "schaltgetriebe",
    # SEMIAUTOMATIC_GEAR bewusst NICHT gemappt: VIRA kennt nur "automatik" und
    # "schaltgetriebe"; ein Halbautomat ist keins von beidem eindeutig.
}

# `category` traegt die Karosserieform. `vehicleClass` ist in der gesamten
# Stichprobe "Car" (Fahrzeug-Obertyp) und wird deshalb NICHT ausgewertet.
_BODY_MAP: dict[str, str] = {
    "EstateCar": "kombi",
    "OffRoad": "suv",
    "Van": "van",
    "Limousine": "limousine",
    # NICHT gemappt und damit bewusst unknown:
    #   "SportsCar" — kann Coupe ODER Cabrio/Roadster sein, VIRA trennt beides.
    #   "SmallCar"  — hat in VIRAs Karosserievokabular keine eindeutige
    #                 Entsprechung (meist Schraegheck, aber nicht zwingend).
}

# Rueckabbildung fuer den Evidenztext: `_bewerte` liest Kraftstoff/Getriebe/
# Karosserie auch aus dem Text (Wortgrenzen-Regex). Damit die strukturierten
# Felder und der Text nicht widerspruechlich sind, wird derselbe normalisierte
# Begriff eingesetzt, den VIRA selbst verwendet.
_BODY_WORT = {"kombi": "Kombi", "suv": "SUV", "van": "Van", "limousine": "Limousine"}
_GETRIEBE_WORT = {"automatik": "Automatik", "schaltgetriebe": "Schaltgetriebe"}
_FUEL_WORT = {"diesel": "Diesel", "benzin": "Benzin",
              "elektro": "Elektro", "hybrid": "Hybrid"}


def ist_sandbox_url(base_url: str | None) -> bool:
    """Sicherheitsgate: nur eine erkennbare Sandbox-Basis-URL ist zulaessig.

    Bewusst eine POSITIVE Erkennung ("sandbox" muss im Host vorkommen) statt
    einer Sperrliste der Produktionshosts. Eine Sperrliste waere unvollstaendig,
    sobald mobile.de einen weiteren Produktivhost einfuehrt; die Positivpruefung
    faellt im Zweifel auf "nicht erlaubt".
    """
    if not base_url:
        return False
    return "sandbox" in str(base_url).split("//", 1)[-1].split("/", 1)[0].lower()


def kw_zu_ps(kw: Any) -> int | None:
    """kW -> PS. `None` bei fehlendem oder unbrauchbarem Wert (nie ein Crash)."""
    if kw is None or isinstance(kw, bool):
        return None
    try:
        wert = float(kw)
    except (TypeError, ValueError):
        return None
    if wert <= 0:
        return None
    return int(round(wert * _KW_ZU_PS))


def preis_aus_ad(preis: Any) -> int | None:
    """`price.consumerPriceGross` -> EUR-Ganzzahl.

    Der Wert ist ein DEZIMALSTRING ("15200.00"), kein Zahltyp. Fremde Waehrungen
    werden verworfen statt umgerechnet — ein Wechselkurs waere eine erfundene
    Zahl in genau dem Feld, das den Median traegt.
    """
    if not isinstance(preis, dict):
        return None
    if preis.get("currency") != "EUR":
        return None
    roh = preis.get("consumerPriceGross")
    if roh is None or isinstance(roh, bool):
        return None
    try:
        wert = float(roh)
    except (TypeError, ValueError):
        return None
    if wert <= 0:
        return None
    return int(round(wert))


def baujahr_aus_ad(first_registration: Any, construction_year: Any) -> int | None:
    """Baujahr aus `firstRegistration` ("YYYYMM", ohne Trennzeichen), sonst aus
    `constructionYear`. In der Stichprobe ist oft genau eines von beiden gesetzt."""
    roh = str(first_registration or "").strip()
    if len(roh) == 6 and roh.isdigit():
        jahr = int(roh[:4])
        if 1900 <= jahr <= 2100:
            return jahr
    if isinstance(construction_year, int) and not isinstance(construction_year, bool):
        if 1900 <= construction_year <= 2100:
            return construction_year
    try:
        jahr = int(str(construction_year).strip())
    except (TypeError, ValueError):
        return None
    return jahr if 1900 <= jahr <= 2100 else None


def _km_aus_ad(mileage: Any) -> int | None:
    if mileage is None or isinstance(mileage, bool):
        return None
    try:
        wert = int(float(mileage))
    except (TypeError, ValueError):
        return None
    return wert if wert >= 0 else None


def ad_zu_beobachtung(ad: dict[str, Any]) -> Preisbeobachtung | None:
    """Ein mobile.de-Search-Ad -> eine UNBEWERTETE `Preisbeobachtung`.

    Gibt `None` zurueck, wenn kein verwertbarer Preis vorliegt — `preis_eur` ist
    das einzige Pflichtfeld des Modells, und eine Beobachtung ohne Preis kann per
    Definition nichts zum Marktvergleich beitragen.

    NICHT gesetzt werden `make`, `model`, `generation` und `engine_variant`:
    diese Felder schreibt `marktvergleich._bewerte` selbst, und zwar nur nach
    bestandener Pruefung gegen das Zielfahrzeug. Wuerde der Provider sie
    vorbelegen, saehe eine ungeprueft uebernommene Anzeige wie eine bestaetigte
    aus — genau der Fehler, den die Etappe-1-Motor-Evidence beseitigt hat.
    """
    if not isinstance(ad, dict):
        return None

    preis_eur = preis_aus_ad(ad.get("price"))
    if preis_eur is None:
        return None

    listing_id = str(ad.get("mobileAdId")).strip() if ad.get("mobileAdId") is not None else None
    listing_id = listing_id or None
    detail_url = str(ad.get("detailPageUrl") or "").strip() or None

    fuel = _FUEL_MAP.get(str(ad.get("fuel") or "").upper())
    transmission = _GEARBOX_MAP.get(str(ad.get("gearbox") or "").upper())
    body = _BODY_MAP.get(str(ad.get("category") or "").strip())
    horsepower = kw_zu_ps(ad.get("power"))
    baujahr = baujahr_aus_ad(ad.get("firstRegistration"), ad.get("constructionYear"))
    km = _km_aus_ad(ad.get("mileage"))

    if listing_id:
        listing_key = f"id:mobile.de:{listing_id}"
    elif detail_url:
        listing_key = f"url:{detail_url}"
    else:
        listing_key = f"v:{preis_eur}:{km}:{baujahr}"

    # ── Evidenztext (§modelDescription) ──────────────────────────────────────
    # `_bewerte` liest seine Belege aus diesem Text. Er ist eine reine
    # Serialisierung der Anzeigenfelder — nichts wird ergaenzt, nichts vom
    # Zielfahrzeug uebernommen.
    #
    # `modelDescription` geht VERBATIM hinein und wird ausdruecklich NICHT auf
    # `engine_variant` gelegt. Der Freitext mischt Modell, Motor, Ausstattung
    # und Karosserie ("Focus Turnier 1.6 EB Titanium XENON SHZ FSHZ TEM") und
    # ist damit Listing-Evidenz, aber keine exakte Motorbezeichnung. Ob eine
    # Motorvariante wirklich belegt ist, entscheidet weiterhin allein
    # `_bewerte` (Abgleich gegen `ziel_motor_tokens`) — und dieselbe Prueflogik
    # faengt dort auch den Widerspruchsfall ab, den die Sandbox liefert
    # (make/model "CITROEN/C3" bei modelDescription "C5 Aircross Shine").
    text = evidenztext(
        ad.get("make"),
        ad.get("model"),
        ad.get("modelDescription"),
        _FUEL_WORT.get(fuel or ""),
        f"{horsepower} PS" if horsepower else None,
        _GETRIEBE_WORT.get(transmission or ""),
        _BODY_WORT.get(body or ""),
        f"EZ {str(ad.get('firstRegistration'))[4:6]}/{baujahr}"
        if baujahr and len(str(ad.get("firstRegistration") or "")) == 6 else
        (f"Baujahr {baujahr}" if baujahr else None),
        f"{km} km" if km is not None else None,
        f"{preis_eur} EUR",
    )

    return Preisbeobachtung(
        preis_eur=preis_eur,
        kilometerstand=km,
        baujahr=baujahr,
        quelle_domain="mobile.de",
        quelle_url=detail_url,
        # Ein Ad-Objekt ist per Definition EIN Inserat, keine Trefferliste.
        source_type="listing",
        listing_key=listing_key,
        listing_id=listing_id,
        detail_url=detail_url,
        fuel=fuel,
        horsepower=horsepower,
        transmission=transmission,
        body=body,
        # "detail": die Angabe stammt aus dem Datensatz des Inserats selbst —
        # nicht aus einem Seitenfilter-Kontext und nicht aus einem Zeichenfenster.
        # Ohne belegte Karosserie bleibt die Herkunft ehrlich "unknown".
        body_evidence="detail" if body else "unknown",
        # mobile.de liefert im Suchergebnis keinen Chassiscode und keine
        # Generation. Es wird KEINE aus der Fahrzeug-DB erfunden — falls das
        # Inserat sie im Freitext nennt, findet `_bewerte` sie im Evidenztext.
        generation=None,
        generation_evidence="unknown",
        extraction_source=EXTRACTION_SOURCE_API,
        segmentation_method=SEGMENTATION_METHOD_API,
        structural_confidence="high",
        window_fallback_used=False,
        vergleichbarkeit="",
        gruende=[f"\x00{text}"],
    )


class MobileDeSandboxNichtKonfiguriert(RuntimeError):
    """Zugangsdaten fehlen oder die Basis-URL ist keine Sandbox-URL."""


class MobileDeProvider:
    """mobile.de Search API als `MarketDataProvider` — SANDBOX ONLY."""

    name = "mobile_de_sandbox"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # `None` heisst "nicht angegeben, aus der Umgebung lesen". Ein
        # ausdruecklich uebergebener Wert gewinnt IMMER — auch ein leerer.
        # Das ist kein Detail: `app/config.py` laedt die .env via `load_dotenv`
        # in `os.environ`, ein `or os.environ.get(...)` wuerde also ein
        # bewusstes `username=""` still durch die echten Zugangsdaten ersetzen.
        # Ein Test (oder ein Aufrufer), der den Provider absichtlich
        # unkonfiguriert bauen will, bekaeme dann ungewollt einen scharfen.
        def _aus_env(wert: str | None, name: str) -> str:
            return os.environ.get(name, "") if wert is None else wert

        self._base_url = _aus_env(base_url, "MOBILE_DE_BASE_URL").rstrip("/")
        self._username = _aus_env(username, "MOBILE_DE_USERNAME")
        self._password = _aus_env(password, "MOBILE_DE_PASSWORD")
        self._client = client
        # Refdata ist ueber die Laufzeit stabil — einmal laden reicht.
        self._makes_cache: list[dict[str, Any]] | None = None
        self._modelle_cache: dict[str, list[dict[str, Any]]] = {}

    # ── Konfiguration ────────────────────────────────────────────────────────

    @property
    def konfiguriert(self) -> bool:
        return bool(self._base_url and self._username and self._password)

    def pruefe_sandbox(self) -> None:
        """Wirft, wenn nicht sauber als Sandbox konfiguriert. Die Meldung nennt
        NIE einen Zugangsdatenwert — nur, welcher Variablenname fehlt."""
        fehlend = [n for n, v in (
            ("MOBILE_DE_BASE_URL", self._base_url),
            ("MOBILE_DE_USERNAME", self._username),
            ("MOBILE_DE_PASSWORD", self._password),
        ) if not v]
        if fehlend:
            raise MobileDeSandboxNichtKonfiguriert(
                "mobile.de-Sandbox nicht konfiguriert, fehlend: " + ", ".join(fehlend))
        if not ist_sandbox_url(self._base_url):
            raise MobileDeSandboxNichtKonfiguriert(
                "MOBILE_DE_BASE_URL zeigt nicht auf eine Sandbox — dieser Provider "
                "ist ausdruecklich SANDBOX ONLY und verweigert jeden anderen Host.")

    # ── HTTP ─────────────────────────────────────────────────────────────────

    async def _get(self, pfad: str, params: dict[str, Any] | None = None) -> Any:
        """GET gegen die Sandbox. Gibt das JSON zurueck oder wirft httpx-Fehler.

        Der `Authorization`-Header wird von httpx aus der BasicAuth erzeugt und
        hier weder gebaut noch geloggt.
        """
        url = f"{self._base_url}{pfad}"
        auth = httpx.BasicAuth(self._username, self._password)
        headers = {"Accept": _ACCEPT}
        if self._client is not None:
            resp = await self._client.get(url, params=params, auth=auth, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                resp = await client.get(url, params=params, auth=auth, headers=headers)
        resp.raise_for_status()
        return resp.json()

    # ── Refdata: Marke/Modell -> classification ──────────────────────────────

    async def _makes(self) -> list[dict[str, Any]]:
        if self._makes_cache is None:
            data = await self._get("/refdata/classes/Car/makes")
            self._makes_cache = list((data or {}).get("values") or [])
        return self._makes_cache

    async def _modelle(self, make_key: str) -> list[dict[str, Any]]:
        if make_key not in self._modelle_cache:
            data = await self._get(f"/refdata/classes/Car/makes/{make_key}/models")
            self._modelle_cache[make_key] = list((data or {}).get("values") or [])
        return self._modelle_cache[make_key]

    @staticmethod
    def _finde(eintraege: list[dict[str, Any]], kandidat: str) -> str | None:
        """Exakter, case-unabhaengiger Treffer auf `name` ODER `description`.

        Kein Teilstring- und kein Fuzzy-Match: mobile.de-Refdata enthaelt
        Eintraege wie "320" und "320 Gran Turismo"; ein Teilstringtreffer wuerde
        den Zielraum stillschweigend verschieben.
        """
        gesucht = (kandidat or "").strip().lower()
        if not gesucht:
            return None
        for e in eintraege:
            for feld in ("name", "description"):
                if str(e.get(feld) or "").strip().lower() == gesucht:
                    return str(e.get("name"))
        return None

    @staticmethod
    def _modell_kandidaten(identity: VehicleIdentity) -> list[str]:
        """Mögliche mobile.de-Modellschluessel aus der VIRA-Identitaet.

        Hintergrund (§4 im Modulkopf): mobile.de fuehrt Modelle motorbezogen
        ("320"), VIRA in Baureihen ("3er"). Die Kandidaten decken beide Welten
        ab, in absteigender Belastbarkeit. Der Ziffernkandidat entsteht nur aus
        einer bereits vorhandenen Bezeichnung ("320d" -> "320"), es wird nichts
        dazuerfunden.
        """
        kandidaten: list[str] = []
        for roh in (identity.model, identity.model_variant, identity.engine_name):
            wert = (roh or "").strip()
            if not wert or wert.lower() in (k.lower() for k in kandidaten):
                continue
            kandidaten.append(wert)
            # "320d"/"325i" -> "320"/"325": fuehrende Ziffernfolge ab 3 Stellen.
            ziffern = ""
            for zeichen in wert:
                if zeichen.isdigit():
                    ziffern += zeichen
                else:
                    break
            if len(ziffern) >= 3 and ziffern.lower() not in (k.lower() for k in kandidaten):
                kandidaten.append(ziffern)
        return kandidaten

    async def classification(self, identity: VehicleIdentity) -> str | None:
        """`refdata/...`-Pfad fuer Marke (+Modell), oder `None`.

        Faellt bewusst gestuft zurueck: Marke+Modell -> nur Marke -> `None`. Ein
        unaufloesbares Modell darf die Suche nicht in einen HTTP 400 laufen
        lassen (mobile.de quittiert unbekannte Refdata-Schluessel hart), aber
        auch nicht zu einer stillschweigend markenlosen Gesamtsuche fuehren.
        """
        make_key = self._finde(await self._makes(), identity.make or "")
        if not make_key:
            return None
        basis = f"refdata/classes/Car/makes/{make_key}"
        modelle = await self._modelle(make_key)
        for kandidat in self._modell_kandidaten(identity):
            treffer = self._finde(modelle, kandidat)
            if treffer:
                return f"{basis}/models/{treffer}"
        return basis

    # ── Suchparameter ────────────────────────────────────────────────────────

    def _such_params(self, identity: VehicleIdentity, classification: str | None,
                     limit: int) -> dict[str, Any]:
        """Minimale, NACHGEMESSENE Filtermenge.

        Bewusst klein gehalten (kein Nachbau der Tavily-Query-Ladder): jeder
        zusaetzliche Filter verengt die Treffermenge, und die eigentliche
        Strenge liegt ohnehin in `_bewerte`. Der Etappe-1-Grundsatz "BREIT
        suchen, STRENG validieren" gilt hier unveraendert.

        Kraftstoff und Getriebe werden NICHT als Filter gesetzt: eine falsch
        aufgeloeste DB-Angabe wuerde sonst schon die Suche verengen, statt nur
        die Bewertung zu beeinflussen — genau der P0, der in Etappe 1 beim
        VW Golf gefunden wurde ("Diesel" wurde still zu "Benzin").
        """
        params: dict[str, Any] = {"page.size": max(1, min(int(limit), _MAX_PAGE_SIZE))}
        if classification:
            params["classification"] = classification
        if identity.year:
            # +/- 2 Jahre um das Zielbaujahr. `firstRegistrationDate` erwartet
            # "YYYY-MM" MIT Bindestrich (nachgemessen) — im Unterschied zum
            # Antwortfeld `firstRegistration` ("YYYYMM").
            params["firstRegistrationDate.min"] = f"{int(identity.year) - 2:04d}-01"
            params["firstRegistrationDate.max"] = f"{int(identity.year) + 2:04d}-12"
        if identity.mileage:
            # Nur eine Obergrenze: eine Untergrenze wuerde gepflegte
            # Niedrig-km-Fahrzeuge aus dem Vergleich draengen.
            params["mileage.max"] = int(identity.mileage) * 2
        return params

    # ── Schnittstelle ────────────────────────────────────────────────────────

    async def find_comparables(
        self,
        identity: VehicleIdentity,
        *,
        limit: int = 20,
    ) -> tuple[list[Preisbeobachtung], bool]:
        """Vergleichsbeobachtungen aus der mobile.de-SANDBOX — unbewertet.

        `(beobachtungen, hatte_technischen_fehler)`. Ein leeres Ergebnis ohne
        Fehler heisst "nichts gefunden" (data_exhausted), nicht "kaputt".
        """
        try:
            self.pruefe_sandbox()
        except MobileDeSandboxNichtKonfiguriert as exc:
            log.warning("mobile.de-Provider nicht nutzbar: %s", exc)
            return [], True

        try:
            classification = await self.classification(identity)
            params = self._such_params(identity, classification, limit)
            data = await self._get("/search-api/search", params)
        except httpx.HTTPStatusError as exc:
            log.warning("mobile.de Search HTTP %s", exc.response.status_code)
            return [], True
        except (httpx.RequestError, asyncio.TimeoutError) as exc:
            log.warning("mobile.de Search Netzwerkfehler (%s)", type(exc).__name__)
            return [], True
        except Exception as exc:  # defekte Antwort (kein JSON o.ae.)
            log.warning("mobile.de Search unerwarteter Fehler (%s)", type(exc).__name__)
            return [], True

        ads = (data or {}).get("ads") if isinstance(data, dict) else None
        if not isinstance(ads, list):
            # 200 mit unerwarteter Struktur ist kein technischer Ausfall,
            # sondern schlicht kein verwertbares Ergebnis.
            return [], False

        beobachtungen: list[Preisbeobachtung] = []
        for ad in ads[:limit]:
            b = ad_zu_beobachtung(ad)
            if b is not None:
                beobachtungen.append(b)
        log.info("mobile.de-Sandbox: %d Ads -> %d Beobachtungen (classification=%s)",
                 len(ads), len(beobachtungen), classification)
        return beobachtungen, False
