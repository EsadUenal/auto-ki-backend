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

KOSTENDECKEL (§14 Runde 4)
---------------------------
Der Normalfall — gute interne Abdeckung, kein Budget — kostet weiterhin NULL
externe Calls. Erst konkrete Mängel schalten Schichten zu:

    gute DB-Coverage, kein Budget   Tavily 0   Discovery-Gemini 0   Budget-Gemini 0
    gute DB-Coverage, mit Budget    Tavily 0   Discovery-Gemini 0   Budget-Gemini 1
    schwache Coverage, kein Budget  Tavily ≤2  Discovery-Gemini ≤1  Budget-Gemini 0
    schwache Coverage, mit Budget   Tavily ≤2  Discovery-Gemini ≤1  Budget-Gemini 1

Das Coverage-Gate (`app.autofinder_web.braucht_web_fallback`) entscheidet das
deterministisch — nicht "die DB ist nicht perfekt", sondern ein benennbarer
Mangel (kein Treffer / <3 Treffer / gewünschte Marke gar nicht im Bestand).

WEB ERGÄNZT FAHRZEUGE, NIE PREISE (§16 Runde 4)
------------------------------------------------
Der Web-Fallback sucht ausschließlich nach real existierenden MODELLEN.
Marktplätze sind doppelt gesperrt (Ausschluss schon bei der Tavily-Anfrage,
nochmals harte Ablehnung jeder Beleg-URL im Validierungs-Gate). Es gibt
weiterhin keine Marktpreise, keine Preisspannen, keine Inserate, keine Bilder.
"""

import logging
from dataclasses import dataclass

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
from app.autofinder_enrich import (
    Enrichment,
    deterministischer_fallback,
    enrich_kandidaten,
    strip_pruef_label,
)
from app.autofinder_fit import FIT_SCHWELLE, berechne_fit
from app.autofinder_web import (
    braucht_web_fallback,
    entdecke_web_kandidaten,
    kandidat_id,
    merge_und_diversifiziere,
)
from app.autofinder_visual import resolve_image
from app.database import get_alle_baureihen_kurz
from app.models import (
    AutoFinderImageEnsureRequest,
    AutoFinderImageEnsureResponse,
    AutoFinderImageResult,
    AutoFinderKandidatOut,
    AutoFinderRequest,
    AutoFinderResponse,
)
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


def _bekannte_marken() -> set[str]:
    """Alle Marken, die VIRA intern überhaupt führt (kleingeschrieben).

    Nutzt die bereits gecachte Baureihen-Kurzliste (`database._cached_alle`,
    60s TTL) — kein zusätzlicher Full-Table-Scan pro Request. Grundlage für
    Coverage-Regel 3: verlangt der Nutzer eine Marke, die es intern gar nicht
    gibt, hilft auch eine gefüllte Trefferliste anderer Marken nicht.
    """
    try:
        return {(b.get("marke") or "").strip().lower()
                for b in get_alle_baureihen_kurz() if b.get("marke")}
    except Exception:
        log.exception("AutoFinder: Markenliste nicht ermittelbar — "
                      "Coverage-Regel 'Marke nicht im Bestand' greift diesmal nicht")
        return set()


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


def _karosserie_ausgabe(k, bevorzugte_karosserie: str | None) -> list[str]:
    """Consumer-Karosserie-Liste. Bei EINDEUTIGER Karosserie-Anfrage steht die
    gewünschte (Hard-Filter-garantiert vorhandene) Klasse ZUERST — ein
    Multi-Body-Kandidat wird dann nicht mit einer irreführenden Fremdklasse
    vorne angezeigt (§Multi-Body). Sonst wie gehabt (sortiert)."""
    klassen = list(k.karosserie_klassen or [])
    if bevorzugte_karosserie and bevorzugte_karosserie in klassen:
        return [bevorzugte_karosserie] + [c for c in klassen if c != bevorzugte_karosserie]
    return klassen


def _zu_kandidat_out(k, *, budget_status: str = BUDGET_UNKNOWN,
                      budget_confidence: str = CONF_UNKNOWN,
                      budget_adjustment: float = 0.0,
                      bevorzugte_karosserie: str | None = None,
                      user_fit: int = 0, user_fit_gruende: list[str] | None = None,
                      enrichment: Enrichment | None = None,
                      enrichment_status: str = "unavailable") -> AutoFinderKandidatOut:
    """Übersetzung des Engine-Kandidaten inkl. Fit-Score (deterministisch) und
    Gemini-Enrichment (why_fits / trade_offs / known_points / Preisorientierung).
    `k.match_score` bleibt der INTERNE Ranking-Score (`base_match_score`);
    `user_fit` ist die nutzer-verständliche Passung."""
    enr = enrichment or Enrichment()
    return AutoFinderKandidatOut(
        # Runde 4: bei web_discovered-Kandidaten sind baureihe_id/variante_id
        # bewusst None — die kanonische Kennung ist candidate_id.
        candidate_id=kandidat_id(k),
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
        karosserie=_karosserie_ausgabe(k, bevorzugte_karosserie),
        match_score=k.match_score + budget_adjustment,
        datenqualitaet=k.datenqualitaet,
        match_gruende=[strip_pruef_label(g) for g in k.match_gruende],
        trade_offs=list(enr.trade_offs),
        user_fit=user_fit,
        user_fit_gruende=list(user_fit_gruende or []),
        why_fits=list(enr.why_fits),
        known_points=list(enr.known_points),
        enrichment_status=enrichment_status,
        estimated_price_min=enr.estimated_price_min,
        estimated_price_max=enr.estimated_price_max,
        price_confidence=enr.price_confidence,
        budget_status=budget_status,
        budget_confidence=budget_confidence,
        base_match_score=k.match_score,
        budget_adjustment=budget_adjustment,
        source_type=k.source_type,
        visual_key=k.visual_key,
        # Runde 4: bei internen DB-Kandidaten existieren diese Attribute nicht —
        # dann bleiben die Felder auf ihren neutralen Defaults (leer/0/UNKNOWN).
        source_urls=list(getattr(k, "source_urls", []) or []),
        evidence_count=getattr(k, "evidence_count", 0) or 0,
        discovery_confidence=getattr(k, "discovery_confidence", "UNKNOWN") or "UNKNOWN",
        web_verified_fields=list(getattr(k, "web_verified_fields", []) or []),
        market_price_min=k.market_price_min,
        market_price_max=k.market_price_max,
        market_price_median=k.market_price_median,
        market_data_quality=k.market_data_quality,
        market_sample_size=k.market_sample_size,
        such_filter_hinweis=None,   # §5/§14: Struktur vorbereitet, weiterhin nicht befüllt
        **_bild_felder(k, bevorzugte_karosserie=bevorzugte_karosserie),
    )


def _bild_felder(k, *, bevorzugte_karosserie: str | None) -> dict:
    """§7 Runde 5: Bildauflösung darf die Antwort NIE gefährden — jeder
    Fehler landet im generischen Fallback (siehe `resolve_image`), nie als
    Exception hier."""
    try:
        bild = resolve_image(k, bevorzugte_karosserie=bevorzugte_karosserie)
        return dict(image_url=bild.image_url, image_type=bild.image_type,
                    image_confidence=bild.image_confidence, ai_generated=bild.ai_generated)
    except Exception:
        log.exception("AutoFinder: Bildauflösung fehlgeschlagen — neutrale Defaults")
        return dict(image_url="", image_type="generic_fallback",
                    image_confidence="representative", ai_generated=False)


# §Punkt 2: nur Kandidaten mit diesem Fit oder besser gehen in die Ausgabe.
_MAX_AUSGABE = 5


@dataclass
class _FinalErgebnis:
    outs: list[AutoFinderKandidatOut]
    status_wert: str                 # "ok" | "no_strong_match"
    warnungen: list[str]
    enrichment_notice: str | None
    budget_ausgefallen: bool
    budget_aufgerufen: bool


async def _finalisiere(
    kandidaten: list, engine_request, body: AutoFinderRequest, *,
    bevorzugte_karosserie: str | None,
) -> _FinalErgebnis:
    """Der Quality-Enrichment-Kern:

      1. Fit-Score (deterministisch) für ALLE gemergten Kandidaten.
      2. Schwellen-Filter: nur >= FIT_SCHWELLE. Keiner -> no_strong_match.
      3. Cap auf 5, stabile Fit-Sortierung.
      4. Budget-Call (nur wenn Budget angegeben) auf GENAU DIESE <=5 -> begrenzte
         Anpassung, stabile Neusortierung innerhalb der 5.
      5. EIN Enrichment-Call auf die (ggf. neu sortierten) <=5.
      6. Kandidaten-Objekte bauen. Bei Enrichment-Ausfall deterministischer
         Fallback je Kandidat + neutraler Hinweis.
    """
    warnungen: list[str] = []

    # 1) + 2) Fit + Schwelle
    mit_fit: list[tuple] = []
    for kand in kandidaten:
        try:
            fit = berechne_fit(kand, engine_request)
        except Exception:
            log.exception("AutoFinder: Fit-Berechnung fehlgeschlagen für %s", kandidat_id(kand))
            continue
        if fit.score >= FIT_SCHWELLE:
            mit_fit.append((fit.score, fit.gruende, kand))

    if not mit_fit:
        return _FinalErgebnis([], "no_strong_match", warnungen, None, False, False)

    # 3) stabile Fit-Sortierung + Cap
    mit_fit.sort(key=lambda t: (
        -t[0], -t[2].match_score, -t[2].datenqualitaet,
        -(t[2].baujahr_von or 0), kandidat_id(t[2]),
    ))
    final = mit_fit[:_MAX_AUSGABE]
    final_kands = [t[2] for t in final]

    # 4) Budget-Call auf die finale Liste (§Punkt 3: grobe Orientierung, kein Preis)
    budget_map: dict[str, tuple[str, str]] = {}
    budget_aufgerufen = False
    budget_ausgefallen = False
    hat_budget = budget_angegeben(body.budget_min, body.budget_max)
    if hat_budget:
        budget_aufgerufen = True
        budget_map, budget_ausgefallen = await bewerte_budget(
            final_kands,
            budget_min=body.budget_min, budget_max=body.budget_max,
            baujahr_von=body.baujahr_von, baujahr_bis=body.baujahr_bis,
            kilometer_max=body.kilometer_max,
        )

    def _b(kand) -> tuple[str, str, float]:
        s, c = budget_map.get(kandidat_id(kand), (BUDGET_UNKNOWN, CONF_UNKNOWN))
        return s, c, budget_adjustment_fuer(s)

    # stabile Neusortierung: Fit ist PRIMÄR (Qualität vor Preis). Erst danach
    # zählt der interne Ranking-Score PLUS die streng begrenzte Budget-
    # Anpassung (max ±1.5) — genau wie in der bisherigen Budget-Logik: Budget
    # kann zwischen technisch gleichwertigen Kandidaten den Ausschlag geben,
    # aber nie einen klar besseren an einem klar schlechteren vorbeiziehen.
    final.sort(key=lambda t: (
        -t[0], -(t[2].match_score + _b(t[2])[2]), -t[2].datenqualitaet,
        -(t[2].baujahr_von or 0), kandidat_id(t[2]),
    ))
    final_kands = [t[2] for t in final]
    for kand in final_kands:
        try:
            kand.budget_status = _b(kand)[0]   # nur Kontext fürs Enrichment-Prompt
        except Exception:
            pass

    budget_hinweis = _budget_ergebnis_hinweis(
        body, gemini_aufgerufen=budget_aufgerufen, gemini_ausgefallen=budget_ausgefallen)
    if budget_hinweis:
        warnungen.append(budget_hinweis)

    # 5) EIN Enrichment-Call
    enr_map: dict[str, Enrichment] = {}
    enr_ausgefallen = False
    try:
        enr_map, enr_ausgefallen = await enrich_kandidaten(final_kands, engine_request)
    except Exception:
        log.exception("AutoFinder: Enrichment-Aufruf unerwartet fehlgeschlagen")
        enr_ausgefallen = True

    # 6) Objekte bauen
    outs: list[AutoFinderKandidatOut] = []
    fallback_genutzt = False
    for score, gruende, kand in final:
        b_status, b_conf, b_anp = _b(kand)
        enr = enr_map.get(kandidat_id(kand))
        if enr is not None:
            e_status = "ok"
        else:
            enr = deterministischer_fallback(kand)
            e_status = "fallback"
            fallback_genutzt = True
        outs.append(_zu_kandidat_out(
            kand, budget_status=b_status, budget_confidence=b_conf,
            budget_adjustment=b_anp, bevorzugte_karosserie=bevorzugte_karosserie,
            user_fit=score, user_fit_gruende=gruende,
            enrichment=enr, enrichment_status=e_status,
        ))

    enrichment_notice = None
    if enr_ausgefallen or fallback_genutzt:
        enrichment_notice = (
            "Die Zusatzanalyse (ausführliche Gründe, Preisorientierung) konnte "
            "diesmal nicht vollständig geladen werden — die Fahrzeugdaten und "
            "die Passung sind davon unberührt."
        )
        warnungen.append(enrichment_notice)

    return _FinalErgebnis(outs, "ok", warnungen, enrichment_notice,
                          budget_ausgefallen, budget_aufgerufen)


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

    # Quality-Enrichment-Runde: IMMER die größere, diversitätsgeprüfte
    # Shortlist holen — der Fit-Filter (§Punkt 2) braucht Spielraum, um
    # unter der 80er-Schwelle liegende Kandidaten wegzulassen und trotzdem
    # bis zu 5 starke auszugeben. Budget + Enrichment laufen danach auf der
    # finalen <=5-Liste (siehe _finalisiere).
    engine_request = _zu_engine_request(body)
    ergebnis = finde_fahrzeuge(engine_request, k=_BUDGET_SHORTLIST_K)

    warnungen: list[str] = []

    diesel_warnung = _diesel_stadt_kurzstrecke_warnung(body)
    if diesel_warnung:
        warnungen.append(diesel_warnung)

    kilometer_hinweis = _kilometer_hinweis(body)
    if kilometer_hinweis:
        warnungen.append(kilometer_hinweis)

    # ── Runde 4: kontrollierter Web-Fallback ────────────────────────────────
    # Läuft NUR bei nachweislichem Coverage-Mangel. Schlägt er fehl (Tavily
    # down, Gemini down, nichts Belastbares gefunden), bleibt es schlicht bei
    # den internen Treffern — `entdecke_web_kandidaten` wirft nie (§13).
    web_kandidaten: list = []
    web_grund: str | None = None
    braucht_web, web_grund = braucht_web_fallback(
        ergebnis.kandidaten, body, _bekannte_marken())
    if braucht_web:
        web_ergebnis = await entdecke_web_kandidaten(body)
        web_kandidaten = web_ergebnis.kandidaten
        if web_kandidaten:
            warnungen.append(
                f"{len(web_kandidaten)} Vorschlag/Vorschläge stammen aus einer "
                "Web-Recherche zu Fahrzeugmodellen, die VIRA intern noch nicht "
                "pflegt — technische Angaben dort sind belegt, aber nicht "
                "VIRA-geprüft."
            )

    # Interne und Web-Kandidaten in EINE Rangliste (§11) — ohne pauschalen
    # DB-Bonus, mit erneut angewandten Diversitätsgrenzen.
    zusammengefuehrt = merge_und_diversifiziere(
        ergebnis.kandidaten, web_kandidaten, k=_BUDGET_SHORTLIST_K)

    enrichment_notice: str | None = None

    if not zusammengefuehrt:
        # Weder intern noch (falls überhaupt gesucht) im Web etwas Belastbares.
        status_wert = "no_internal_match"
        warnungen.append(
            "Der interne Datenbestand enthält aktuell keinen passenden Treffer "
            "für diese Kombination."
        )
        finale_kandidaten: list[AutoFinderKandidatOut] = []
    else:
        if ergebnis.treffer_vor_diversitaet < _NIEDRIGE_COVERAGE_SCHWELLE:
            warnungen.append(
                "Nur wenige passende Fahrzeuge im internen Bestand gefunden — "
                "die Auswahl ist entsprechend klein."
            )
        # §Punkt 2/Runde 5: bei EINDEUTIGER Karosserie-Anfrage (genau eine
        # gewählt) ist diese Klasse Hard-Filter-garantiert vorhanden UND
        # nachweislich das Gesuchte — Resolver + Consumer-Ausgabe stellen sie
        # dann voran.
        bevorzugte_karosserie = body.karosserie[0] if len(body.karosserie) == 1 else None
        fin = await _finalisiere(
            zusammengefuehrt, engine_request, body,
            bevorzugte_karosserie=bevorzugte_karosserie)
        finale_kandidaten = fin.outs
        status_wert = fin.status_wert
        enrichment_notice = fin.enrichment_notice
        warnungen.extend(fin.warnungen)
        if status_wert == "no_strong_match":
            warnungen.append(
                "Zu deinen Angaben gibt es aktuell keinen wirklich starken "
                "Treffer im Bestand. Versuche es mit weniger oder etwas "
                "weiteren Filtern (Budget, Baujahr, Karosserie)."
            )

    return AutoFinderResponse(
        status=status_wert,
        kandidaten=finale_kandidaten,
        total_candidates_considered=ergebnis.treffer_vor_diversitaet,
        filters_applied=_filters_applied(body),
        warnings=warnungen,
        data_scope_hint=_DATA_SCOPE_HINT,
        enrichment_notice=enrichment_notice,
    )


# ══════════════════════════════════════════════════════════════════════════
# BILD-ON-DEMAND (§Punkt 1) — dedizierter Endpunkt, GETRENNT vom Such-Pfad
# ══════════════════════════════════════════════════════════════════════════
# Der Such-Endpunkt oben löst NIE eine Bildgenerierung aus. `app.autofinder_
# images` (das die Offline-Pipeline nutzt) wird hier bewusst LAZY importiert,
# damit `import app.routers.autofinder` bildgenerierungsfrei bleibt.

_IMAGE_ENSURE_RATE_LIMIT = "10/minute"


@router.post(
    "/autofinder/images/ensure",
    response_model=AutoFinderImageEnsureResponse,
    summary="AutoFinder: fehlende finale Fahrzeugbilder nacherzeugen (gecacht)",
)
@limiter.limit(_IMAGE_ENSURE_RATE_LIMIT)
async def autofinder_images_ensure(body: AutoFinderImageEnsureRequest, request: Request):
    verify_api_key(request)
    if not body.items:
        return AutoFinderImageEnsureResponse(results=[])
    from app.autofinder_images import ensure_images  # lazy: siehe oben

    roh = [i.model_dump() for i in body.items]
    try:
        ergebnisse = await ensure_images(roh)
    except Exception:
        log.exception("AutoFinder-Images: ensure_images fehlgeschlagen — leeres Ergebnis")
        ergebnisse = [{"visual_key": i["visual_key"], "status": "failed"} for i in roh]
    return AutoFinderImageEnsureResponse(
        results=[AutoFinderImageResult(**r) for r in ergebnisse]
    )


@router.get(
    "/autofinder/img/{visual_key}",
    summary="AutoFinder: on-demand erzeugtes Fahrzeugbild ausliefern",
)
async def autofinder_img(visual_key: str):
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    from app.autofinder_images import bild_pfad

    pfad = bild_pfad(visual_key)
    if pfad is None:
        raise HTTPException(status_code=404, detail="Bild nicht vorhanden")
    return FileResponse(str(pfad), media_type="image/webp",
                        headers={"Cache-Control": "public, max-age=86400"})
