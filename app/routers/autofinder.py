from __future__ import annotations

"""
AutoFinder-Router — Runde 2 (HTTP) + Runde 3 (Budget-Plausibilität).

POST /api/v1/autofinder ist bewusst OHNE Check-Gate: kein `require_check_access`,
kein Credit-Verbrauch, kein Login-Zwang (nur der bestehende API-Key wie bei
`/fahrzeug`). AutoFinder ist ein Traffic-/Akquise-Feature, kein bezahltes
Produkt (Produktspezifikation §6).

SCORE-SAFETY (§10 Runde 2 / §6 Runde 3)
-----------------------------------------
Die FOUNDATION (`app.autofinder.finde_fahrzeuge`, Runde 1) bleibt vollständig
unangetastet und bestimmt weiterhin ALLEIN, welche Kandidaten überhaupt in
Frage kommen (harte Filter, Dedupe, Diversität, Basis-Score) — dieser Router
importiert `app/autofinder.py` unverändert, kein einziger Byte-Unterschied in
Runde 3.

Budget ist eine NACHGELAGERTE, EIGENSTÄNDIG SICHTBARE Anpassung: Gemini
(`app.autofinder_budget.bewerte_budget`) bewertet die bereits fertige
Foundation-Shortlist (bis zu 15 Kandidaten, IMMER schon diversitätsgeprüft),
darf aber NIE einen Kandidaten hinzufügen/entfernen/technisch verändern —
nur je Kandidat einen streng begrenzten Bonus/Malus auf den bereits
feststehenden Score legen (`app.autofinder_budget.BUDGET_ADJUSTMENT`). Die
Top-5-Auswahl danach ist ein reines STABILES Neu-Sortieren dieser Teilmenge
— da eine bereits Diversitäts-geprüfte Liste (≤2/Marke, ≤1/Baureihe) nach
Konstruktion JEDE Teilmenge/Umsortierung dieser Grenzen einhält, ist keine
zweite Diversitäts-Berechnung nötig (siehe `_top5_nach_budget`).

KEINE EXTERNEN CALLS AUSSER EINEM OPTIONALEN GEMINI-CALL (§13/§17 Runde 3)
----------------------------------------------------------------------------
Weiterhin KEIN Tavily, KEIN Search Grounding, KEIN neuer Gemini-Client. Wenn
der Nutzer kein Budget angibt ODER die Foundation 0 Kandidaten liefert, wird
`app.autofinder_budget` gar nicht erst aufgerufen — 0 externe Calls, exakt wie
Runde 2. Ist ein Budget angegeben UND gibt es Kandidaten, läuft GENAU EIN
Gemini-Call für die gesamte Shortlist (nie einer pro Kandidat).
"""

import logging

from fastapi import APIRouter, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.auth import verify_api_key
from app.autofinder import AutoFinderRequest as _EngineRequest
from app.autofinder import finde_fahrzeuge
from app.autofinder_budget import (
    BUDGET_UNKNOWN,
    CONF_UNKNOWN,
    bewerte_budget,
    budget_adjustment_fuer,
    budget_angegeben,
)
from app.models import AutoFinderKandidatOut, AutoFinderRequest, AutoFinderResponse
from app.utf8 import UTF8JSONResponse

log = logging.getLogger(__name__)

router = APIRouter(default_response_class=UTF8JSONResponse)
#
# RATE-LIMIT-KLARSTELLUNG (Runde 2 P2-Cleanup):
# `app/main.py` haengt bereits per SlowAPIMiddleware ein globales Default-
# Limit (`app.rate_limit.RATE_LIMIT`, aktuell 20/min) an JEDEN Request —
# unabhaengig davon, was hier steht. Ein hoeherer lokaler Wert (Runde 2 hatte
# faelschlich 60/min dokumentiert) waere deshalb NIE wirksam: die Middleware
# hat bereits vorher zugeschlagen. Geprueft wurde die einzige bestehende
# Möglichkeit, davon abzuweichen — `Limiter.exempt` (siehe
# app/routers/payments.py, Stripe-Webhook) — aber `exempt` schaltet für die
# betroffene Route JEDE Pruefung ab (auch einen eigenen `.limit()` auf
# DERSELBEN Limiter-Instanz), macht den Endpunkt also unlimitiert statt
# separat limitiert. Das waere eine echte Schwächung, keine saubere Lösung.
#
# Deshalb bewusst Option A: der lokale Wert spiegelt exakt das globale
# Default-Limit — kein irreführender höherer Wert, keine neue Rate-Limit-
# Architektur, keine Änderung an app/rate_limit.py oder anderen Routen. Der
# eigene Decorator bleibt trotzdem stehen (Konsistenz mit dem bestehenden
# Muster bei KaufCheck/VerkaufsCheck: der Endpunkt dokumentiert sein Limit
# selbst, statt sich implizit auf die App-weite Middleware zu verlassen).
limiter = Limiter(key_func=get_remote_address)
_AUTOFINDER_RATE_LIMIT = "20/minute"   # bewusst == app.rate_limit.RATE_LIMIT, siehe oben

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

# §5 Runde 3: "Top 10-15 interne Kandidaten VOR Budgetbewertung" — die
# Foundation liefert diese Menge bereits vollständig diversitätsgeprüft
# (siehe Moduldoc oben), der Router muss dafür nichts Eigenes berechnen.
_BUDGET_SHORTLIST_K = 15


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


def _kilometer_hinweis(body: AutoFinderRequest) -> str | None:
    """§4 Runde 3: `kilometer_max` bleibt weiterhin KEIN harter Filter (die DB
    hat keine Kilometerangabe je Baureihe) — das muss sichtbar bleiben, auch
    wenn Budget jetzt (Runde 3) das Ranking beeinflussen darf."""
    if body.kilometer_max is None:
        return None
    return (
        "Kilometerangaben fließen aktuell nicht in die Auswahl ein — VIRA hat "
        "dafür noch keinen belastbaren Marktpreis-/Gebrauchtwagen-Datenbestand. "
        "Sie dienen nur zur Orientierung."
    )


def _budget_ergebnis_hinweis(body: AutoFinderRequest, *, gemini_aufgerufen: bool,
                              gemini_ausgefallen: bool) -> str | None:
    """§2/§8 Runde 3: Budget ist NIE eine Marktpreis-/Preisspannen-Aussage —
    dieser Hinweis nennt bewusst KEINE Zahl, KEIN "günstig"/"teuer". Bei
    Gemini-Ausfall die vom Produkt vorgegebene neutrale Meldung (§8)."""
    if not budget_angegeben(body.budget_min, body.budget_max):
        return None
    if gemini_ausgefallen:
        return "Budget konnte diesmal nicht zusätzlich berücksichtigt werden."
    if gemini_aufgerufen:
        return (
            "Das Budget wird als grobe Orientierung für die Reihenfolge genutzt "
            "— keine konkrete Marktpreisangabe und keine Garantie."
        )
    return None


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


def _zu_kandidat_out(k, *, budget_status: str = BUDGET_UNKNOWN,
                      budget_confidence: str = CONF_UNKNOWN,
                      budget_adjustment: float = 0.0) -> AutoFinderKandidatOut:
    """Übersetzung des Engine-Kandidaten. `k.match_score` (Foundation, Runde 1)
    bleibt unverändert `base_match_score`; die Budget-Anpassung (Runde 3, IMMER
    0.0 ohne Budget/bei UNKNOWN) wird additiv und NACHVOLLZIEHBAR obendrauf
    gelegt — kein Nachjustieren des Foundation-Werts selbst (§10/§12)."""
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
        match_score=k.match_score + budget_adjustment,
        datenqualitaet=k.datenqualitaet,
        match_gruende=list(k.match_gruende),
        trade_offs=list(k.trade_offs),
        budget_status=budget_status,
        budget_confidence=budget_confidence,
        base_match_score=k.match_score,
        budget_adjustment=budget_adjustment,
        source_type=k.source_type,
        visual_key=k.visual_key,
        market_price_min=k.market_price_min,
        market_price_max=k.market_price_max,
        market_price_median=k.market_price_median,
        market_data_quality=k.market_data_quality,
        market_sample_size=k.market_sample_size,
        such_filter_hinweis=None,   # §5/§14: Struktur vorbereitet, weiterhin nicht befüllt
    )


def _top5_nach_budget(kandidaten: list, budget_map: dict[str, tuple[str, str]],
                       *, k: int = 5) -> list[AutoFinderKandidatOut]:
    """Wendet die begrenzte Budget-Anpassung an, sortiert die (bereits
    diversitätsgeprüfte, siehe Moduldoc) Shortlist stabil neu und kappt auf
    `k`. OHNE Budgetangabe/bei komplett leerem `budget_map` ist jede
    Anpassung 0.0 — die Reihenfolge bleibt dann bit-identisch zur reinen
    Foundation-Sortierung (Runde 2), weil dieselben Tie-Break-Felder in
    derselben Reihenfolge verwendet werden wie in `app.autofinder._sortierschluessel`."""
    angereichert = []
    for kand in kandidaten:
        status, conf = budget_map.get(kand.variante_id, (BUDGET_UNKNOWN, CONF_UNKNOWN))
        anpassung = budget_adjustment_fuer(status)
        finaler_score = kand.match_score + anpassung
        angereichert.append((finaler_score, kand, status, conf, anpassung))

    angereichert.sort(key=lambda t: (
        -t[0],                              # finaler Score
        -t[1].datenqualitaet,
        -(t[1].baujahr_von or 0),
        t[1].variante_id,
    ))

    return [
        _zu_kandidat_out(kand, budget_status=status, budget_confidence=conf,
                         budget_adjustment=anpassung)
        for _, kand, status, conf, anpassung in angereichert[:k]
    ]


def _filters_applied(body: AutoFinderRequest) -> dict:
    """Nur tatsächlich WIRKENDE Filter/Prioritäten — reine Transparenz für den
    Client, keine Rückwirkung auf die Suche selbst."""
    angewandt: dict = {}
    for feld in ("marken_bevorzugt", "marken_ausschliessen", "karosserie",
                 "kraftstoff", "getriebe", "antrieb"):
        werte = getattr(body, feld)
        if werte:
            angewandt[feld] = werte
    for feld in ("leistung_min_ps", "leistung_max_ps", "baujahr_von", "baujahr_bis",
                 "nutzung", "budget_min", "budget_max"):
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

    hat_budget = budget_angegeben(body.budget_min, body.budget_max)
    # §7: Ohne Budget bleibt es bei k=5 (Runde 2 unverändert, kein Gemini
    # möglich). MIT Budget wird die groessere, aber weiterhin vollständig
    # diversitätsgeprüfte Shortlist geholt, damit Gemini genau EINMAL über
    # die ganze Auswahl urteilen kann, bevor auf 5 gekappt wird (§5).
    engine_request = _zu_engine_request(body)
    ergebnis = finde_fahrzeuge(engine_request, k=_BUDGET_SHORTLIST_K if hat_budget else 5)

    warnungen: list[str] = []

    diesel_warnung = _diesel_stadt_kurzstrecke_warnung(body)
    if diesel_warnung:
        warnungen.append(diesel_warnung)

    kilometer_hinweis = _kilometer_hinweis(body)
    if kilometer_hinweis:
        warnungen.append(kilometer_hinweis)

    budget_map: dict[str, tuple[str, str]] = {}
    gemini_aufgerufen = False
    gemini_ausgefallen = False

    # §17: no_internal_match -> kein Gemini-Call. §7: kein Budget -> kein Call.
    if hat_budget and ergebnis.kandidaten:
        gemini_aufgerufen = True
        budget_map, gemini_ausgefallen = await bewerte_budget(
            ergebnis.kandidaten,
            budget_min=body.budget_min, budget_max=body.budget_max,
            baujahr_von=body.baujahr_von, baujahr_bis=body.baujahr_bis,
            kilometer_max=body.kilometer_max,
        )

    budget_hinweis = _budget_ergebnis_hinweis(
        body, gemini_aufgerufen=gemini_aufgerufen, gemini_ausgefallen=gemini_ausgefallen)
    if budget_hinweis:
        warnungen.append(budget_hinweis)

    if not ergebnis.kandidaten:
        status_wert = "no_internal_match"
        warnungen.append(
            "Der interne Datenbestand enthält aktuell keinen passenden Treffer "
            "für diese Kombination."
        )
        finale_kandidaten: list[AutoFinderKandidatOut] = []
    else:
        status_wert = "ok"
        if ergebnis.treffer_vor_diversitaet < _NIEDRIGE_COVERAGE_SCHWELLE:
            warnungen.append(
                "Nur wenige passende Fahrzeuge im internen Bestand gefunden — "
                "die Auswahl ist entsprechend klein."
            )
        # §6/§12: begrenzte Budget-Anpassung anwenden, stabil neu sortieren,
        # auf exakt 5 kappen — bei leerem budget_map (kein Budget ODER
        # Gemini ausgefallen) ist das ein Nullsummen-Re-Sort (siehe Docstring
        # von `_top5_nach_budget`), die Foundation-Reihenfolge bleibt erhalten.
        finale_kandidaten = _top5_nach_budget(ergebnis.kandidaten, budget_map, k=5)

    return AutoFinderResponse(
        status=status_wert,
        kandidaten=finale_kandidaten,
        total_candidates_considered=ergebnis.treffer_vor_diversitaet,
        filters_applied=_filters_applied(body),
        warnings=warnungen,
        data_scope_hint=_DATA_SCOPE_HINT,
    )
