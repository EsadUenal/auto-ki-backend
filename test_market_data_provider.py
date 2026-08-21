"""
Deterministische Tests fuer die MarketDataProvider-Schnittstelle und den
mobile.de-Sandbox-Provider (Etappe 3).

OHNE NETZ, OHNE SECRETS. Alle Antworten sind handgeschriebene Fixtures, die dem
real gemessenen Sandbox-Schema nachgebildet sind. Es stehen KEINE echten
Zugangsdaten in dieser Datei und es wird keine echte URL kontaktiert.

Geprueft wird der Provider-VERTRAG, nicht die Marktqualitaet: Feldmapping,
Einheiten, Enums, Fehlerverhalten und die Grenze zur Etappe-1-Bewertung.
"""
import asyncio

import httpx

from app.market_data_provider import (
    EXTRACTION_SOURCE_API,
    SEGMENTATION_METHOD_API,
    FixtureProvider,
    evidenztext,
)
from app.mobile_de_provider import (
    MobileDeProvider,
    MobileDeSandboxNichtKonfiguriert,
    ad_zu_beobachtung,
    baujahr_aus_ad,
    ist_sandbox_url,
    kw_zu_ps,
    preis_aus_ad,
)
from app.models import Preisbeobachtung
from app.vehicle_identity import VehicleIdentity

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    status = "OK  " if bedingung else "FAIL"
    print(f"[{status}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def lauf(coro):
    return asyncio.run(coro)


# ── Fixture: ein Ad im real gemessenen Sandbox-Schema ────────────────────────

def ad(**overrides):
    basis = {
        "mobileAdId": "1000011",
        "detailPageUrl": "https://suchen.mobile.de/auto-inserat/vw-golf-vii/1000011.html?source=api",
        "make": "VW",
        "model": "Golf",
        "modelDescription": "Golf VII 1.4 TSI Highline XENON NAVI AAC SHZ",
        "firstRegistration": "201301",
        "constructionYear": None,
        "mileage": 59987,
        "fuel": "PETROL",
        "power": 103,
        "gearbox": "MANUAL_GEAR",
        "category": "Limousine",
        "vehicleClass": "Car",
        "price": {"consumerPriceGross": "14900.00", "currency": "EUR", "type": "FIXED"},
    }
    basis.update(overrides)
    return basis


print("=== A. Search-Response-Mapping ===")

b = ad_zu_beobachtung(ad())
check("A1: Ad wird zu einer Preisbeobachtung", isinstance(b, Preisbeobachtung))
check("A2: Preis aus Dezimalstring -> 14900", b.preis_eur == 14900)
check("A3: km uebernommen", b.kilometerstand == 59987)
check("A4: Baujahr aus 'YYYYMM' -> 2013", b.baujahr == 2013)
check("A5: kW 103 -> 140 PS", b.horsepower == 140)
check("A6: PETROL -> benzin", b.fuel == "benzin")
check("A7: MANUAL_GEAR -> schaltgetriebe", b.transmission == "schaltgetriebe")
check("A8: Limousine -> limousine", b.body == "limousine")
check("A9: body_evidence='detail' bei belegter Karosserie", b.body_evidence == "detail")
check("A10: stabile Listing-ID", b.listing_id == "1000011")
check("A11: listing_key traegt Domain+ID", b.listing_key == "id:mobile.de:1000011")
check("A12: detail_url gesetzt", b.detail_url and b.detail_url.endswith("1000011.html?source=api"))
check("A13: quelle_url == detail_url", b.quelle_url == b.detail_url)
check("A14: quelle_domain mobile.de", b.quelle_domain == "mobile.de")
check("A15: source_type='listing'", b.source_type == "listing")

print()
print("=== B. Herkunft/Evidence (API-Vokabular) ===")

check("B1: extraction_source='api'", b.extraction_source == EXTRACTION_SOURCE_API == "api")
check("B2: segmentation_method='api_structured'",
      b.segmentation_method == SEGMENTATION_METHOD_API == "api_structured")
check("B3: structural_confidence='high'", b.structural_confidence == "high")
check("B4: window_fallback_used=False", b.window_fallback_used is False)
check("B5: unbewertet uebergeben (vergleichbarkeit leer)", b.vergleichbarkeit == "")

# Der Evidenztext ist das, was _bewerte liest.
text = b.gruende[0][1:] if b.gruende and b.gruende[0].startswith("\x00") else ""
check("B6: Evidenztext liegt in gruende[0] mit \\x00-Praefix", bool(text))
check("B7: Evidenztext enthaelt modelDescription verbatim",
      "Golf VII 1.4 TSI Highline" in text)
check("B8: Evidenztext enthaelt normalisierten Kraftstoff", "Benzin" in text)
check("B9: Evidenztext enthaelt PS-Angabe (nicht kW)", "140 PS" in text and "103 PS" not in text)
check("B10: Evidenztext enthaelt EZ mit Monat", "EZ 01/2013" in text)

print()
print("=== C. modelDescription wird NICHT als Motorwahrheit gesetzt (§8) ===")

check("C1: engine_variant bleibt None (setzt _bewerte)", b.engine_variant is None)
check("C2: make bleibt None (setzt _bewerte)", b.make is None)
check("C3: model bleibt None (setzt _bewerte)", b.model is None)

print()
print("=== D. Generation bleibt unknown (§12) ===")

check("D1: generation None", b.generation is None)
check("D2: generation_evidence 'unknown'", b.generation_evidence == "unknown")

print()
print("=== E. Enum-Randfaelle ===")

check("E1: DIESEL -> diesel", ad_zu_beobachtung(ad(fuel="DIESEL")).fuel == "diesel")
check("E2: ELECTRICITY -> elektro", ad_zu_beobachtung(ad(fuel="ELECTRICITY")).fuel == "elektro")
check("E3: HYBRID -> hybrid", ad_zu_beobachtung(ad(fuel="HYBRID")).fuel == "hybrid")
check("E4: unbekannter Fuel-Enum -> None, kein Crash",
      ad_zu_beobachtung(ad(fuel="WASSERSTOFF_XYZ")).fuel is None)
check("E5: fuel None -> None", ad_zu_beobachtung(ad(fuel=None)).fuel is None)
check("E6: AUTOMATIC_GEAR -> automatik",
      ad_zu_beobachtung(ad(gearbox="AUTOMATIC_GEAR")).transmission == "automatik")
check("E7: SEMIAUTOMATIC_GEAR bleibt unknown",
      ad_zu_beobachtung(ad(gearbox="SEMIAUTOMATIC_GEAR")).transmission is None)
check("E8: EstateCar -> kombi", ad_zu_beobachtung(ad(category="EstateCar")).body == "kombi")
check("E9: OffRoad -> suv", ad_zu_beobachtung(ad(category="OffRoad")).body == "suv")
check("E10: Van -> van", ad_zu_beobachtung(ad(category="Van")).body == "van")

b_sports = ad_zu_beobachtung(ad(category="SportsCar"))
check("E11: SportsCar bleibt unknown (Coupe ODER Cabrio)", b_sports.body is None)
check("E12: body_evidence dann 'unknown'", b_sports.body_evidence == "unknown")
check("E13: SmallCar bleibt unknown", ad_zu_beobachtung(ad(category="SmallCar")).body is None)
check("E14: vehicleClass='Car' liefert KEINE Karosserie",
      ad_zu_beobachtung(ad(category=None, vehicleClass="Car")).body is None)

print()
print("=== F. Preis-Randfaelle ===")

check("F1: fremde Waehrung -> kein Preis, keine Umrechnung",
      preis_aus_ad({"consumerPriceGross": "14900.00", "currency": "CHF"}) is None)
check("F2: Ad mit fremder Waehrung wird verworfen",
      ad_zu_beobachtung(ad(price={"consumerPriceGross": "1.00", "currency": "USD"})) is None)
check("F3: kaputter Preisstring -> None",
      preis_aus_ad({"consumerPriceGross": "vb", "currency": "EUR"}) is None)
check("F4: Ad mit kaputtem Preis liefert None (kein Crash)",
      ad_zu_beobachtung(ad(price={"consumerPriceGross": "auf Anfrage", "currency": "EUR"})) is None)
check("F5: fehlendes price-Feld -> None", ad_zu_beobachtung(ad(price=None)) is None)
check("F6: price kein dict -> None", preis_aus_ad("14900") is None)
check("F7: Preis 0 -> None", preis_aus_ad({"consumerPriceGross": "0.00", "currency": "EUR"}) is None)
check("F8: Rundung kaufmaennisch",
      preis_aus_ad({"consumerPriceGross": "14900.60", "currency": "EUR"}) == 14901)

print()
print("=== G. Einheiten / Datumsformate ===")

check("G1: kW->PS 160 -> 218 (BMW 325i, reale Werksangabe)", kw_zu_ps(160) == 218)
check("G2: kW->PS 63 -> 86", kw_zu_ps(63) == 86)
check("G3: power None -> None", kw_zu_ps(None) is None)
check("G4: power 0 -> None", kw_zu_ps(0) is None)
check("G5: power Text -> None, kein Crash", kw_zu_ps("stark") is None)
check("G6: power bool wird nicht als Zahl gelesen", kw_zu_ps(True) is None)
check("G7: 'YYYYMM' -> Jahr", baujahr_aus_ad("200901", None) == 2009)
check("G8: constructionYear-Fallback greift", baujahr_aus_ad(None, 2015) == 2015)
check("G9: firstRegistration schlaegt constructionYear",
      baujahr_aus_ad("201901", 2005) == 2019)
check("G10: beide fehlen -> None", baujahr_aus_ad(None, None) is None)
check("G11: unplausibles Jahr -> None", baujahr_aus_ad("180001", None) is None)
check("G12: Ad ohne EZ nutzt constructionYear",
      ad_zu_beobachtung(ad(firstRegistration=None, constructionYear=2016)).baujahr == 2016)
check("G13: Ad ohne km -> kilometerstand None",
      ad_zu_beobachtung(ad(mileage=None)).kilometerstand is None)

print()
print("=== H. Sandbox-Gate (§14: kein Production-Enable) ===")

check("H1: offizieller Sandbox-Host wird akzeptiert",
      ist_sandbox_url("https://services.sandbox.mobile.de") is True)
check("H1b: offizieller Host mit Pfad bleibt akzeptiert",
      ist_sandbox_url("https://services.sandbox.mobile.de/search-api") is True)
check("H2: Produktivhost abgelehnt",
      ist_sandbox_url("https://services.mobile.de") is False)
check("H3: leere URL abgelehnt", ist_sandbox_url("") is False)
check("H4: None abgelehnt", ist_sandbox_url(None) is False)
check("H5: 'sandbox' nur im Pfad zaehlt NICHT",
      ist_sandbox_url("https://services.mobile.de/sandbox") is False)

# ── Hardening-Check (nach Etappe 3): exakter Hostvergleich statt Substring ───
check("H10: fremder Host mit 'sandbox' im Namen wird abgelehnt",
      ist_sandbox_url("https://sandbox.evil.example") is False)
check("H11: echter Sandbox-Host als PRAEFIX eines fremden Hosts wird abgelehnt "
      "(Substring-Falle, per exaktem Hostvergleich behoben)",
      ist_sandbox_url("https://services.sandbox.mobile.de.evil.example") is False)
check("H12: 'sandbox' als Namensbestandteil einer fremden Domain wird abgelehnt",
      ist_sandbox_url("https://evil-sandbox.example") is False)
check("H13: HTTP statt HTTPS wird abgelehnt (Basic Auth braucht TLS)",
      ist_sandbox_url("http://services.sandbox.mobile.de") is False)
check("H14: eingebettete Credentials im URL-String werden abgelehnt",
      ist_sandbox_url("https://user:pass@services.sandbox.mobile.de") is False)
check("H15: Groß-/Kleinschreibung des Hosts ist irrelevant",
      ist_sandbox_url("https://SERVICES.SANDBOX.MOBILE.DE") is True)
check("H16: Subdomain des offiziellen Hosts zaehlt NICHT automatisch "
      "(exakter Match, keine Teilstring-Grosszuegigkeit in die andere Richtung)",
      ist_sandbox_url("https://x.services.sandbox.mobile.de") is False)
check("H17: kaputte URL fuehrt zu False, kein Crash",
      ist_sandbox_url("https://[::1") is False)

p_prod = MobileDeProvider(base_url="https://services.mobile.de", username="u", password="p")
try:
    p_prod.pruefe_sandbox()
    check("H6: Produktiv-URL wird hart abgelehnt", False)
except MobileDeSandboxNichtKonfiguriert as exc:
    check("H6: Produktiv-URL wird hart abgelehnt", True)
    check("H7: Fehlermeldung enthaelt kein Passwort", "p" != str(exc) and "password" not in str(exc).lower())

p_leer = MobileDeProvider(base_url="", username="", password="")
check("H8: unkonfiguriert -> nicht konfiguriert", p_leer.konfiguriert is False)
obs, fehler = lauf(p_leer.find_comparables(VehicleIdentity(make="VW", model="Golf")))
check("H9: unkonfiguriert -> ([], True) technischer Fehler", obs == [] and fehler is True)

print()
print("=== I. HTTP-Fehlerverhalten (Transport gemockt, kein Netz) ===")


def provider_mit_antworten(antworten: dict, *, fehler: Exception | None = None):
    """Baut einen Provider mit gemocktem Transport. `antworten` bildet einen
    Pfad-Teilstring auf (status, json) ab."""
    def handler(request: httpx.Request) -> httpx.Response:
        if fehler is not None:
            raise fehler
        for teil, (status, payload) in antworten.items():
            if teil in request.url.path:
                return httpx.Response(status, json=payload)
        return httpx.Response(404, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return MobileDeProvider(
        base_url="https://services.sandbox.mobile.de",
        username="testuser", password="testpass", client=client)


REFDATA_MAKES = {"values": [{"name": "VW", "description": "Volkswagen"},
                            {"name": "BMW", "description": "BMW"}]}
REFDATA_VW_MODELLE = {"values": [{"name": "Golf", "description": "Golf"},
                                 {"name": "Passat", "description": "Passat"}]}
REFDATA_BMW_MODELLE = {"values": [{"name": "320", "description": "320"},
                                  {"name": "318", "description": "318"}]}

identity_golf = VehicleIdentity(make="Volkswagen", model="Golf", year=2013, mileage=60000)

p_ok = provider_mit_antworten({
    "/refdata/classes/Car/makes/VW/models": (200, REFDATA_VW_MODELLE),
    "/refdata/classes/Car/makes": (200, REFDATA_MAKES),
    "/search-api/search": (200, {"total": 2, "ads": [ad(), ad(mobileAdId="1000012",
                                                         price={"consumerPriceGross": "13500.00",
                                                                "currency": "EUR"})]}),
})
obs, fehler = lauf(p_ok.find_comparables(identity_golf, limit=10))
check("I1: erfolgreiche Suche -> 2 Beobachtungen", len(obs) == 2)
check("I2: kein technischer Fehler", fehler is False)
check("I3: IDs unterschiedlich", {o.listing_id for o in obs} == {"1000011", "1000012"})

p_500 = provider_mit_antworten({
    "/refdata/classes/Car/makes/VW/models": (200, REFDATA_VW_MODELLE),
    "/refdata/classes/Car/makes": (200, REFDATA_MAKES),
    "/search-api/search": (500, {}),
})
obs, fehler = lauf(p_500.find_comparables(identity_golf))
check("I4: HTTP 500 -> ([], True)", obs == [] and fehler is True)

p_400 = provider_mit_antworten({
    "/refdata/classes/Car/makes/VW/models": (200, REFDATA_VW_MODELLE),
    "/refdata/classes/Car/makes": (200, REFDATA_MAKES),
    "/search-api/search": (400, {}),
})
obs, fehler = lauf(p_400.find_comparables(identity_golf))
check("I5: HTTP 400 -> ([], True)", obs == [] and fehler is True)

p_timeout = provider_mit_antworten({}, fehler=httpx.ConnectTimeout("timeout"))
obs, fehler = lauf(p_timeout.find_comparables(identity_golf))
check("I6: Timeout -> ([], True)", obs == [] and fehler is True)

p_netz = provider_mit_antworten({}, fehler=httpx.ConnectError("kein netz"))
obs, fehler = lauf(p_netz.find_comparables(identity_golf))
check("I7: Netzwerkfehler -> ([], True)", obs == [] and fehler is True)

p_leer_res = provider_mit_antworten({
    "/refdata/classes/Car/makes/VW/models": (200, REFDATA_VW_MODELLE),
    "/refdata/classes/Car/makes": (200, REFDATA_MAKES),
    "/search-api/search": (200, {"total": 0, "ads": []}),
})
obs, fehler = lauf(p_leer_res.find_comparables(identity_golf))
check("I8: leere Trefferliste -> ([], False), KEIN technischer Fehler",
      obs == [] and fehler is False)

p_struktur = provider_mit_antworten({
    "/refdata/classes/Car/makes/VW/models": (200, REFDATA_VW_MODELLE),
    "/refdata/classes/Car/makes": (200, REFDATA_MAKES),
    "/search-api/search": (200, {"unerwartet": True}),
})
obs, fehler = lauf(p_struktur.find_comparables(identity_golf))
check("I9: 200 mit unerwarteter Struktur -> ([], False)", obs == [] and fehler is False)

p_teil = provider_mit_antworten({
    "/refdata/classes/Car/makes/VW/models": (200, REFDATA_VW_MODELLE),
    "/refdata/classes/Car/makes": (200, REFDATA_MAKES),
    "/search-api/search": (200, {"ads": [ad(), ad(price=None), {"kaputt": 1}, "kein dict"]}),
})
obs, fehler = lauf(p_teil.find_comparables(identity_golf))
check("I10: unbrauchbare Ads werden uebersprungen, gute bleiben",
      len(obs) == 1 and obs[0].listing_id == "1000011" and fehler is False)

print()
print("=== J. classification-Aufloesung (Refdata statt Raten) ===")

cls = lauf(p_ok.classification(VehicleIdentity(make="Volkswagen", model="Golf")))
check("J1: 'Volkswagen' -> Refdata-Schluessel 'VW'",
      cls == "refdata/classes/Car/makes/VW/models/Golf")

cls = lauf(p_ok.classification(VehicleIdentity(make="VW", model="Golf")))
check("J2: 'VW' funktioniert ebenso", cls == "refdata/classes/Car/makes/VW/models/Golf")

cls = lauf(p_ok.classification(VehicleIdentity(make="Volkswagen", model="Unbekanntes")))
check("J3: unaufloesbares Modell -> Marke-only statt HTTP 400",
      cls == "refdata/classes/Car/makes/VW")

cls = lauf(p_ok.classification(VehicleIdentity(make="Nichtmarke", model="Golf")))
check("J4: unbekannte Marke -> None (keine markenlose Gesamtsuche)", cls is None)

p_bmw = provider_mit_antworten({
    "/refdata/classes/Car/makes/BMW/models": (200, REFDATA_BMW_MODELLE),
    "/refdata/classes/Car/makes": (200, REFDATA_MAKES),
    "/search-api/search": (200, {"ads": []}),
})
cls = lauf(p_bmw.classification(VehicleIdentity(make="BMW", model="3er", model_variant="320d")))
check("J5: VIRA-Baureihe '3er' + Variante '320d' -> mobile.de-Modell '320'",
      cls == "refdata/classes/Car/makes/BMW/models/320")

cls = lauf(p_bmw.classification(VehicleIdentity(make="BMW", model="3er")))
check("J6: nur '3er' ohne Variante -> ehrlich Marke-only",
      cls == "refdata/classes/Car/makes/BMW")

print()
print("=== K. Suchparameter (nur nachgemessene Namen) ===")

params = p_ok._such_params(identity_golf, "refdata/classes/Car/makes/VW/models/Golf", 20)
check("K1: classification gesetzt", params.get("classification", "").endswith("/Golf"))
check("K2: page.size gesetzt", params.get("page.size") == 20)
check("K3: page.size auf 100 gedeckelt",
      p_ok._such_params(identity_golf, None, 5000).get("page.size") == 100)
check("K4: firstRegistrationDate.min im Format 'YYYY-MM' (mit Bindestrich)",
      params.get("firstRegistrationDate.min") == "2011-01")
check("K5: firstRegistrationDate.max im Format 'YYYY-MM'",
      params.get("firstRegistrationDate.max") == "2015-12")
check("K6: mileage.max gesetzt", params.get("mileage.max") == 120000)
check("K7: KEIN 'make'/'model'-Parameter (wird von mobile.de still ignoriert)",
      "make" not in params and "model" not in params)
check("K8: KEIN Kraftstoff-Filter (ungepruefte DB-Angabe darf nicht verengen)",
      "fuel" not in params)
check("K9: KEIN Getriebe-Filter", "gearbox" not in params)
check("K10: ohne Baujahr kein Datumsfilter",
      "firstRegistrationDate.min" not in p_ok._such_params(
          VehicleIdentity(make="VW", model="Golf"), None, 10))

print()
print("=== L. FixtureProvider ===")

fix_b = ad_zu_beobachtung(ad())
fp = FixtureProvider([fix_b, ad_zu_beobachtung(ad(mobileAdId="2"))])
obs, fehler = lauf(fp.find_comparables(identity_golf, limit=10))
check("L1: liefert die hinterlegten Beobachtungen", len(obs) == 2)
check("L2: kein Fehler", fehler is False)
check("L3: limit wird respektiert",
      len(lauf(fp.find_comparables(identity_golf, limit=1))[0]) == 1)
check("L4: Anfrage wird fuer Diagnose mitgeschrieben",
      fp.letzte_anfrage is not None and fp.letzte_anfrage[1] == 1)

obs1, _ = lauf(fp.find_comparables(identity_golf))
obs1[0].vergleichbarkeit = "ungeeignet"
obs2, _ = lauf(fp.find_comparables(identity_golf))
check("L5: liefert Kopien — Mutation faerbt nicht auf den naechsten Lauf ab",
      obs2[0].vergleichbarkeit == "")

fp_fehler = FixtureProvider([], hatte_technischen_fehler=True)
check("L6: technischer Fehler durchreichbar",
      lauf(fp_fehler.find_comparables(identity_golf))[1] is True)

print()
print("=== M. evidenztext ===")

check("M1: None-Teile fallen raus", evidenztext("BMW", None, "320d") == "BMW 320d")
check("M2: leere Strings fallen raus", evidenztext("BMW", "  ", "320d") == "BMW 320d")
check("M3: leer bleibt leer", evidenztext(None, None) == "")

print()
print("=== N. Redirect-/Credential-Risiko (Hardening-Check nach Etappe 3) ===")

# Ein (fiktiver, fehlkonfigurierter) Sandbox-Host antwortet mit einem Redirect
# auf einen FREMDEN Host. Ohne `follow_redirects=False` wuerde httpx dem Ziel
# automatisch folgen und denselben Authorization-Header (Basic-Auth-Credentials)
# an den fremden Host mitschicken.
aufrufe: list[str] = []


def redirect_handler(request: httpx.Request) -> httpx.Response:
    aufrufe.append(str(request.url))
    if "evil.example" in request.url.host:
        # Wuerde NIE erreicht werden duerfen — ein Aufruf hier waere der Beweis,
        # dass Credentials dem Redirect gefolgt sind.
        return httpx.Response(200, json={"ads": []})
    return httpx.Response(
        302, headers={"Location": "https://credential-sink.evil.example/steal"})


p_redirect = MobileDeProvider(
    base_url="https://services.sandbox.mobile.de", username="testuser", password="testpass",
    client=httpx.AsyncClient(transport=httpx.MockTransport(redirect_handler)))

try:
    obs, fehler = lauf(p_redirect._get("/search-api/search", {"page.size": 1}))
    ergebnis_ok = False  # ein 302 sollte NIE als brauchbares JSON durchgehen
except Exception:
    ergebnis_ok = True  # 302 ohne JSON-Body -> Fehler ist das korrekte Verhalten

check("N1: 302-Redirect wird NICHT automatisch verfolgt "
      "(genau 1 Request, nie der fremde Host)",
      len(aufrufe) == 1 and "evil.example" not in aufrufe[0])
check("N2: der fremde Redirect-Host wurde zu keinem Zeitpunkt kontaktiert",
      not any("evil.example" in a for a in aufrufe))
check("N3: 302-Antwort fuehrt zu einem Fehler statt zu einem stillen Fake-Erfolg",
      ergebnis_ok)

# Dieselbe Garantie auf der oeffentlichen Schnittstelle: find_comparables()
# darf bei einem Redirect nie crashen, sondern muss ihn als technischen
# Fehler behandeln (ueber den bestehenden allgemeinen except-Zweig).
aufrufe.clear()
p_redirect2 = MobileDeProvider(
    base_url="https://services.sandbox.mobile.de", username="testuser", password="testpass",
    client=httpx.AsyncClient(transport=httpx.MockTransport(redirect_handler)))
obs2, fehler2 = lauf(p_redirect2.find_comparables(VehicleIdentity(make="Nichtmarke")))
check("N4: find_comparables() faengt den Redirect ab -> ([], True), kein Crash",
      obs2 == [] and fehler2 is True)

print()
if _FEHLER:
    print(f"FEHLGESCHLAGEN: {len(_FEHLER)}")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE PROVIDER-TESTS GRUEN")
