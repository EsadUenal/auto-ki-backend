from __future__ import annotations

"""
AutoFinder-Router — Runde 2: kostenloser Consumer-Endpunkt.

POST /api/v1/autofinder ist bewusst OHNE Check-Gate: kein `require_check_access`,
kein Credit-Verbrauch, kein Login-Zwang (nur der bestehende API-Key wie bei
`/fahrzeug`). AutoFinder ist ein Traffic-/Akquise-Feature, kein bezahltes
Produkt (Produktspezifikation §6).

SCORE-SAFETY (§10)
-------------------
Dieser Router führt KEINE zweite Ranking-Logik. Er übersetzt die HTTP-Eingabe
in `app.autofinder.AutoFinderRequest`, ruft `app.autofinder.finde_fahrzeuge()`
EINMAL auf und übersetzt das Ergebnis 1:1 in den HTTP-Vertrag. Kein
Nachsortieren, kein Score-Override, kein Gemini, keine Web-Kandidaten — die
Reihenfolge, die die Engine liefert, ist die Reihenfolge, die der Client sieht.

KEINE EXTERNEN CALLS (§13)
---------------------------
Dieses Modul importiert weder Tavily- noch Gemini-/LLM-Module. Tavily = 0,
Gemini = 0 gilt strukturell (siehe test_autofinder_api.py, das den Quellcode
darauf prüft).
"""

import logging

from fastapi import APIRouter, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.auth import verify_api_key
from app.autofinder import AutoFinderRequest as _EngineRequest
from app.autofinder import finde_fahrzeuge
from app.models import AutoFinderKandidatOut, AutoFinderRequest, AutoFinderResponse
from app.utf8 import UTF8JSONResponse

log = logging.getLogger(__name__)

router = APIRouter(default_response_class=UTF8JSONResponse)
# 60/min: grosszügig genug für normale Filter-Klick-Nutzung (mehrere Suchen
# pro Minute beim Anpassen von Filtern), endlich genug gegen Scraping/
# Missbrauch eines Endpunkts ohne Check-Gate. Höher als KaufCheck/
# VerkaufsCheck (10/min), weil dort das Check-Kontingent selbst bereits eine
# harte Bremse ist — hier gibt es keine solche zweite Bremse.
limiter = Limiter(key_func=get_remote_address)
_AUTOFINDER_RATE_LIMIT = "60/minute"

# §7: Pflicht-Transparenzhinweis — der aktuelle Bestand ist eine VIRA-Vor-
# auswahl, kein vollständiger Marktüberblick. Zahl synchron mit dem
# kanonischen Bestand halten (siehe test_autofinder_norm.py Abschnitt 18).
_DATA_SCOPE_HINT = (
    "Die interne Vorauswahl basiert aktuell auf 416 von VIRA gepflegten "
    "Baureihen. Weitere Modelle können in einer späteren Web-Ergänzung "
    "berücksichtigt werden."
)

# §8: Ab wann ein Treffer als "sehr wenige Kandidaten" gilt — normal
# ausgeben, aber mit Coverage-Hinweis. Bewusst auf die Menge VOR der
# Diversitäts-Kappung bezogen (treffer_vor_diversitaet), nicht auf die
# Top-5-Anzahl, die bei jedem Treffer >=1 ohnehin bei "bis zu 5" läge.
_NIEDRIGE_COVERAGE_SCHWELLE = 3

# §9: Was als "sehr geringe Jahresfahrleistung" gilt — bewusst der bereits im
# Audit verwendete Schwellenwert (auch in db-seitigen Auswertungen als unterste
# km/Jahr-Kategorie benutzt), keine neu erfundene Zahl.
_STADT_KURZSTRECKE_KM_SCHWELLE = 10_000


def _diesel_stadt_kurzstrecke_warnung(body: AutoFinderRequest) -> str | None:
    """§9: Diesel bleibt als ausdrücklicher Nutzerwunsch bestehen (KEIN
    heimliches Überschreiben) — aber bei Stadt+geringer Jahresfahrleistung
    bekommt die Antwort einen neutralen Hinweis. Keine erfundene DPF-
    Schadenswahrscheinlichkeit, keine Panikmache — nur der Hinweis, dass das
    Muster gesondert bedacht werden sollte."""
    if body.nutzung != "stadt":
        return None
    if body.km_pro_jahr is None or body.km_pro_jahr > _STADT_KURZSTRECKE_KM_SCHWELLE:
        return None
    if "Diesel" not in (body.kraftstoff or []):
        return None
    return (
        "Du hast Diesel ausdrücklich gewünscht, gibst aber überwiegend "
        "Stadtverkehr und eine geringe Jahresfahrleistung an. Dieses "
        "Nutzungsmuster sollte bei der Fahrzeugwahl gesondert bedacht werden."
    )


def _budget_kilometer_hinweis(body: AutoFinderRequest) -> str | None:
    """§4/§11 Test F: Budget/Kilometer werden entgegengenommen, aber NICHT
    als harter Filter ausgewertet — das muss für den Nutzer sichtbar sein,
    statt die Angabe stillschweigend zu ignorieren."""
    if body.budget_min is None and body.budget_max is None and body.kilometer_max is None:
        return None
    return (
        "Budget- und Kilometerangaben fließen aktuell noch nicht in die Auswahl "
        "ein — VIRA hat dafür noch keinen belastbaren Marktpreis-Datenbestand. "
        "Sie dienen nur zur Orientierung."
    )


def _zu_engine_request(body: AutoFinderRequest) -> _EngineRequest:
    """Reine Feld-für-Feld-Übersetzung — keine Filterentscheidung hier."""
    return _EngineRequest(
        budget_min=body.budget_min,
        budget_max=body.budget_max,
        baujahr_von=body.baujahr_von,
        baujahr_bis=body.baujahr_bis,
        kilometer_max=body.kilometer_max,
        marken_bevorzugt=list(body.marken_bevorzugt),
        marken_ausschliessen=list(body.marken_ausschliessen),
        karosserie=list(body.karosserie),
        kraftstoff=list(body.kraftstoff),
        getriebe=list(body.getriebe),
        leistung_min_ps=body.leistung_min_ps,
        leistung_max_ps=body.leistung_max_ps,
        antrieb=list(body.antrieb),
        nutzung=body.nutzung,
        km_pro_jahr=body.km_pro_jahr,
        sportlich=body.sportlich,
        sparsam=body.sparsam,
        fahranfaenger=body.fahranfaenger,
        praktisch=body.praktisch,
        komfortabel=body.komfortabel,
        familie=body.familie,
    )


def _zu_kandidat_out(k) -> AutoFinderKandidatOut:
    """1:1-Übersetzung des Engine-Kandidaten — keine Neuberechnung, kein
    Nachjustieren von Score/Reihenfolge (§10)."""
    return AutoFinderKandidatOut(
        baureihe_id=k.baureihe_id,
        variante_id=k.variante_id,
        marke=k.marke,
        modell=k.modell,
        generation=k.generation,
        motor=k.motor_bezeichnung,
        baujahr_von=k.baujahr_von,
        baujahr_bis=k.baujahr_bis,
        leistung_ps=k.leistung_ps,
        kraftstoff=k.kraftstoff,
        getriebe=list(k.getriebe_klassen),
        antrieb=k.antrieb,
        karosserie=list(k.karosserie_klassen),
        match_score=k.match_score,
        datenqualitaet=k.datenqualitaet,
        match_gruende=list(k.match_gruende),
        trade_offs=list(k.trade_offs),
        source_type=k.source_type,
        visual_key=k.visual_key,
        market_price_min=k.market_price_min,
        market_price_max=k.market_price_max,
        market_price_median=k.market_price_median,
        market_data_quality=k.market_data_quality,
        market_sample_size=k.market_sample_size,
        such_filter_hinweis=None,   # §5/§14: Struktur vorbereitet, in Runde 2 nicht befüllt
    )


def _filters_applied(body: AutoFinderRequest) -> dict:
    """Nur tatsächlich WIRKENDE Filter/Prioritäten — reine Transparenz für den
    Client, keine Rückwirkung auf die Suche selbst."""
    angewandt: dict = {}
    for feld in ("marken_bevorzugt", "marken_ausschliessen", "karosserie",
                 "kraftstoff", "getriebe", "antrieb"):
        werte = getattr(body, feld)
        if werte:
            angewandt[feld] = werte
    for feld in ("leistung_min_ps", "leistung_max_ps", "baujahr_von", "baujahr_bis", "nutzung"):
        wert = getattr(body, feld)
        if wert is not None:
            angewandt[feld] = wert
    for prio in ("sportlich", "sparsam", "fahranfaenger"):
        if getattr(body, prio):
            angewandt[prio] = True
    return angewandt


@router.post(
    "/autofinder",
    response_model=AutoFinderResponse,
    summary="AutoFinder: kostenlose Fahrzeugempfehlung aus der VIRA-Datenbank",
)
@limiter.limit(_AUTOFINDER_RATE_LIMIT)
async def autofinder_endpunkt(body: AutoFinderRequest, request: Request):
    verify_api_key(request)

    engine_request = _zu_engine_request(body)
    ergebnis = finde_fahrzeuge(engine_request, k=5)

    warnungen: list[str] = []

    diesel_warnung = _diesel_stadt_kurzstrecke_warnung(body)
    if diesel_warnung:
        warnungen.append(diesel_warnung)

    budget_hinweis = _budget_kilometer_hinweis(body)
    if budget_hinweis:
        warnungen.append(budget_hinweis)

    if not ergebnis.kandidaten:
        status_wert = "no_internal_match"
        warnungen.append(
            "Der interne Datenbestand enthält aktuell keinen passenden Treffer "
            "für diese Kombination."
        )
    else:
        status_wert = "ok"
        if ergebnis.treffer_vor_diversitaet < _NIEDRIGE_COVERAGE_SCHWELLE:
            warnungen.append(
                "Nur wenige passende Fahrzeuge im internen Bestand gefunden — "
                "die Auswahl ist entsprechend klein."
            )

    return AutoFinderResponse(
        status=status_wert,
        kandidaten=[_zu_kandidat_out(k) for k in ergebnis.kandidaten],
        total_candidates_considered=ergebnis.treffer_vor_diversitaet,
        filters_applied=_filters_applied(body),
        warnings=warnungen,
        data_scope_hint=_DATA_SCOPE_HINT,
    )
