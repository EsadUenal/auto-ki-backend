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

from fastapi import APIRouter, HTTPException, Request
from slowapi import Limiter
from app.client_ip import limit_schluessel

from app.auth import verify_api_key
from app.usage_limit import require_autofinder_kontingent
from app.provider_control import provider_action
from app.autofinder import AutoFinderRequest as _EngineRequest
from app.autofinder import finde_fahrzeuge
from app.autofinder_budget import (
    BUDGET_UNKNOWN,
    CONF_UNKNOWN,
    bewerte_budget,
    budget_adjustment_fuer,
    budget_angegeben,
    konsolidiere_budget_status,
)
from app.autofinder_identity import consumer_modellname, dedupe_semantisch
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
from app.autofinder_visual import (
    CONF_EXACT,
    generischer_fallback,
    resolve_image,
    visual_key_v2,
    waehle_karosserie,
)
from app.autofinder_variante import (
    LEERE_SUCHE_MELDUNG,
    Q_MEHRDEUTIG,
    hat_verwertbares_kriterium,
    loese_baujahre,
    loese_getriebe,
    loese_karosserie,
    pruefe_hardfilter_einhaltung,
)
from app.database import get_alle_baureihen_kurz
from app.models import (
    # Die AutoFinderImage*-Modelle bleiben in app/models.py bestehen (API-
    # Contract/Offline-Werkzeuge), werden vom Router aber nicht mehr gebraucht:
    # der Ensure-Endpunkt ist entfallen (siehe unten).
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
limiter = Limiter(key_func=limit_schluessel)
_AUTOFINDER_RATE_LIMIT = "20/minute"   # bewusst == app.rate_limit.RATE_LIMIT, siehe oben

# §7: Pflicht-Transparenzhinweis — der aktuelle Bestand ist eine ENFAL-Vor-
# auswahl, kein vollständiger Marktüberblick. Zahl synchron mit dem
# kanonischen Bestand halten (siehe test_autofinder_norm.py Abschnitt 18).
_DATA_SCOPE_HINT = (
    "Die interne Vorauswahl basiert aktuell auf 416 von ENFAL gepflegten "
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
    """Alle Marken, die ENFAL intern überhaupt führt (kleingeschrieben).

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


# KILOMETERFILTER — BEWUSST ENTFERNT STATT ERKLÄRT
# -------------------------------------------------
# Früher nahm das Suchformular einen "Gesamtkilometer max."-Wert entgegen, den
# nichts auswertete; die Antwort trug dafür einen Hinweistext. Ein Filter, der
# im Formular steht, aber die Auswahl nicht einschränkt, ist irreführend —
# auch mit Erklärung darunter. ENFAL hat für einen fahrzeugbezogenen
# Kilometer-Filter keine Datenquelle: die Datenbank führt keine Kilometer je
# Baureihe, und es werden bewusst keine Gebrauchtwagen-Angebote abgerufen.
#
# Deshalb: das Feld ist aus dem Formular raus, wird nicht mehr an die Engine
# durchgereicht, taucht nicht in `filters_applied` auf, geht nicht in den
# Score ein und wird auch dem Budget-Prompt nicht mehr mitgegeben. Die
# HTTP-Schicht nimmt einen mitgeschickten Wert weiter entgegen (alte Clients),
# ignoriert ihn aber vollständig.


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
            "Das Budget wird als grobe Orientierung für die Reihenfolge genutzt. "
            "Es ist keine konkrete Marktpreisangabe und keine Garantie."
        )
    return None


def _zu_engine_request(body: AutoFinderRequest) -> _EngineRequest:
    """Reine Feld-für-Feld-Übersetzung — keine Filterentscheidung hier."""
    return _EngineRequest(
        budget_min=body.budget_min,
        budget_max=body.budget_max,
        baujahr_von=body.baujahr_von,
        baujahr_bis=body.baujahr_bis,
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


@dataclass
class _Variante:
    """Die KONKRETE empfohlene Ausprägung eines Kandidaten.

    Interne DB-Kandidaten bringen die Auflösung schon aus der Engine mit
    (app/autofinder.py -> app/autofinder_variante.py). Web-Kandidaten haben
    keine Baureihenzeile; für sie wird hier aus denselben Regeln aufgelöst,
    damit BEIDE Herkünfte exakt dieselbe Ausgabeform haben.
    """
    karosserie: list[str]              # genau EIN Eintrag, wenn auflösbar
    karosserie_konkret: bool
    karosserie_quelle: str             # siehe AutoFinderKandidatOut.karosserie_quelle
    karosserie_baureihe: list[str]     # was die Baureihe insgesamt anbietet
    getriebe: list[str]                # genau EIN Eintrag, wenn auflösbar
    getriebe_konkret: bool
    getriebe_verfuegbar: list[str]
    generation: str | None
    baujahr_von: int | None
    baujahr_bis: int | None
    generation_baujahr_von: int | None
    generation_baujahr_bis: int | None


def _konkrete_variante(k, body: AutoFinderRequest) -> _Variante:
    belegbar = sorted(k.karosserie_klassen or [])
    konkret = getattr(k, "karosserie_konkret", None)
    quelle = getattr(k, "karosserie_quelle", Q_MEHRDEUTIG)
    baureihe_karo = sorted(getattr(k, "karosserie_baureihe", None) or belegbar)
    if konkret is None and quelle == Q_MEHRDEUTIG and belegbar:
        # Web-Kandidat (oder sonst nicht vorab aufgelöst): dieselben Regeln,
        # nur ohne Baureihen-Kontext — es gibt keine Schwestervarianten.
        auf = loese_karosserie(frozenset(belegbar), getattr(k, "motor_bezeichnung", None),
                               frozenset(), gewuenscht=list(body.karosserie))
        konkret, quelle = auf.konkret, auf.quelle

    getr_verfuegbar = sorted(k.getriebe_klassen or [])
    getr_konkret = getattr(k, "getriebe_konkret", None)
    if getr_konkret is None:
        getr_konkret = loese_getriebe(getr_verfuegbar, gewuenscht=list(body.getriebe)).konkret

    gen_von = getattr(k, "generation_baujahr_von", None)
    gen_bis = getattr(k, "generation_baujahr_bis", None)
    bj_von, bj_bis = k.baujahr_von, k.baujahr_bis
    if gen_von is None and gen_bis is None:
        # Web-Kandidat: Bauzeit ist dort bereits die belegte Angabe.
        bj = loese_baujahre(k.baujahr_von, k.baujahr_bis, body.baujahr_von, body.baujahr_bis)
        bj_von, bj_bis = bj.von, bj.bis
        gen_von, gen_bis = bj.generation_von, bj.generation_bis

    return _Variante(
        karosserie=[konkret] if konkret else belegbar,
        karosserie_konkret=konkret is not None,
        karosserie_quelle=quelle,
        karosserie_baureihe=baureihe_karo,
        getriebe=[getr_konkret] if getr_konkret else getr_verfuegbar,
        getriebe_konkret=getr_konkret is not None,
        getriebe_verfuegbar=getr_verfuegbar,
        generation=getattr(k, "generation_label", None) or k.generation,
        baujahr_von=bj_von, baujahr_bis=bj_bis,
        generation_baujahr_von=gen_von, generation_baujahr_bis=gen_bis,
    )


def _zu_kandidat_out(k, var: _Variante, *, budget_status: str = BUDGET_UNKNOWN,
                      budget_confidence: str = CONF_UNKNOWN,
                      budget_adjustment: float = 0.0,
                      bevorzugte_karosserie: str | None = None,
                      user_fit: int = 0, user_fit_gruende: list[str] | None = None,
                      enrichment: Enrichment | None = None,
                      enrichment_status: str = "unavailable",
                      known_points: list[str] | None = None) -> AutoFinderKandidatOut:
    """Übersetzung des Engine-Kandidaten inkl. Fit-Score (deterministisch) und
    Gemini-Enrichment (why_fits / trade_offs / Preisorientierung).
    `k.match_score` bleibt der INTERNE Ranking-Score (`base_match_score`);
    `user_fit` ist die nutzer-verständliche Passung.

    `karosserie`/`getriebe`/`baujahr_*` tragen die KONKRETE empfohlene
    Ausprägung (siehe `_konkrete_variante`), nicht mehr die Sammelangaben der
    Baureihe. Was die Baureihe darüber hinaus anbietet, steht getrennt in
    `karosserie_verfuegbar`/`getriebe_verfuegbar` — sichtbar als Kontext,
    nie als Bestandteil der Empfehlung."""
    enr = enrichment or Enrichment()
    karosserie_out = list(var.karosserie)
    # §Multi-Body: ein kombinierter Modellname ("S60/V60") beschreibt zwei
    # Karosserievarianten derselben Baureihe — als Kombi-Empfehlung ausgegeben
    # behauptet er ein Modell, das es nicht gibt. Ist die angezeigte Karosserie
    # bekannt und löst genau EINEN Namensteil auf, wird auf diesen projiziert
    # ("V60"); sonst bleibt der Name unverändert (kein Raten).
    modell_out = consumer_modellname(
        k.modell, karosserie_out[0] if karosserie_out else None)
    return AutoFinderKandidatOut(
        # Runde 4: bei web_discovered-Kandidaten sind baureihe_id/variante_id
        # bewusst None — die kanonische Kennung ist candidate_id.
        candidate_id=kandidat_id(k),
        baureihe_id=k.baureihe_id,
        variante_id=k.variante_id,
        marke=k.marke,
        modell=modell_out,
        generation=var.generation,
        motor=k.motor_bezeichnung,
        motor_hergeleitet=bool(getattr(k, "motor_hergeleitet", False)),
        baujahr_von=var.baujahr_von,
        baujahr_bis=var.baujahr_bis,
        generation_baujahr_von=var.generation_baujahr_von,
        generation_baujahr_bis=var.generation_baujahr_bis,
        leistung_ps=k.leistung_ps,
        kraftstoff=k.kraftstoff,
        getriebe=list(var.getriebe),
        getriebe_verfuegbar=list(var.getriebe_verfuegbar),
        getriebe_konkret=var.getriebe_konkret,
        antrieb=k.antrieb,
        karosserie=karosserie_out,
        karosserie_verfuegbar=list(var.karosserie_baureihe),
        karosserie_konkret=var.karosserie_konkret,
        karosserie_quelle=var.karosserie_quelle,
        match_score=k.match_score + budget_adjustment,
        datenqualitaet=k.datenqualitaet,
        match_gruende=[strip_pruef_label(g) for g in k.match_gruende],
        trade_offs=list(enr.trade_offs),
        user_fit=user_fit,
        user_fit_gruende=list(user_fit_gruende or []),
        why_fits=list(enr.why_fits),
        known_points=list(known_points or []),
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
        **_bild_felder(k, bevorzugte_karosserie=(
            karosserie_out[0] if var.karosserie_konkret else bevorzugte_karosserie)),
    )


def _neutrale_bildfelder(k, bevorzugte_karosserie: str | None) -> dict:
    """Neutrale Silhouette statt eines falschen Fahrzeugbildes.

    Wichtig: `image_url` bleibt NICHT leer — der bestehende Vertrag garantiert
    jedem Kandidaten ein gueltiges Bildfeld (test_autofinder_visual.py K). Nur
    `image_type=generic_fallback` signalisiert dem Frontend, dass hier noch ein
    exaktes Bild nachgezogen werden muss.
    """
    try:
        karo = waehle_karosserie(getattr(k, "karosserie_klassen", None) or [],
                                 bevorzugte_karosserie=bevorzugte_karosserie)
        bild = generischer_fallback(karo)
        return dict(image_url=bild.image_url, image_type=bild.image_type,
                    image_confidence=bild.image_confidence, ai_generated=False)
    except Exception:
        log.exception("AutoFinder: neutraler Bild-Fallback fehlgeschlagen")
        return dict(image_url="", image_type="generic_fallback",
                    image_confidence="representative", ai_generated=False)


def _ist_exaktes_asset(k, bild, bevorzugte_karosserie: str | None) -> bool:
    """§Exact-Image-Regel: für FINALE Consumer-Result-Cards zählt ein Asset nur
    dann als Fahrzeugbild, wenn es GENAU dieses Fahrzeug zeigt.

    BEFUND, der die Regel nötig gemacht hat: ein Ford Focus **Mk4** wurde mit
    `ford--focus--mk3--kombi.webp` illustriert. Der Resolver hatte korrekt
    `image_confidence='model_match'` und `fallback_used=True` gemeldet — nur
    hat der Consumer-Pfad diese Abstufung nie ausgewertet und das Asset zudem
    mit `image_type='generated_cached'` weitergereicht, weshalb auch das
    Frontend es für ein echtes Bild hielt.

    Der Resolver selbst behält seine Stufen (exact / generation_match /
    model_match / representative) — andere interne Nutzer dürfen sie weiter
    verwenden. Nur der Consumer-Ausgabepfad ist strenger:

      * `image_confidence == exact`, UND
      * der aufgelöste Key ist der Key GENAU DIESES Kandidaten.

    Akzeptiert werden dabei die beiden kanonischen Key-Formen desselben
    Fahrzeugs: der karosseriespezifische Resolver-Key
    (`ford--focus--mk4--kombi`, kuratierte Assets) und der Engine-/On-Demand-Key
    (`ford--focus--mk4`, unter dem der Ensure-Flow erzeugte Bilder ablegt).
    Beide tragen Marke+Modell+Generation; ein Asset einer ANDEREN Generation
    oder eines anderen Modells kann damit nie durchrutschen.
    """
    if bild.image_confidence != CONF_EXACT or bild.fallback_used:
        return False
    exakt_karo = visual_key_v2(
        getattr(k, "marke", "") or "", getattr(k, "modell", "") or "",
        getattr(k, "generation", None), getattr(k, "karosserie_klassen", None) or [],
        bevorzugte_karosserie=bevorzugte_karosserie)
    erlaubt = {exakt_karo, getattr(k, "visual_key", "") or ""}
    return bild.resolved_visual_key in erlaubt


def _bild_felder(k, *, bevorzugte_karosserie: str | None) -> dict:
    """§7 Runde 5: Bildauflösung darf die Antwort NIE gefährden — jeder
    Fehler landet im generischen Fallback (siehe `resolve_image`), nie als
    Exception hier.

    §Exact-Image-Regel: ein nicht-exaktes Asset wird NICHT als Fahrzeugbild
    ausgegeben, sondern als `generic_fallback` gemeldet. Damit greift die
    bereits vorhandene Image-Guarantee unverändert weiter: das Frontend zieht
    für genau diesen visual_key ein exaktes Bild über den On-Demand-Ensure-Flow
    nach und lässt den Kandidaten, wenn das zweimal scheitert, zugunsten des
    nächsten qualifizierten Kandidaten fallen. Ein Bild der falschen Generation
    wird nie ersatzweise angezeigt.
    """
    try:
        bild = resolve_image(k, bevorzugte_karosserie=bevorzugte_karosserie)
        if not _ist_exaktes_asset(k, bild, bevorzugte_karosserie):
            log.info("AutoFinder: Asset %r fuer %s verworfen (confidence=%s) — kein exaktes "
                     "Bild dieses Fahrzeugs, On-Demand-Ensure uebernimmt",
                     bild.resolved_visual_key, getattr(k, "visual_key", "?"),
                     bild.image_confidence)
            return _neutrale_bildfelder(k, bevorzugte_karosserie)
        return dict(image_url=bild.image_url, image_type=bild.image_type,
                    image_confidence=bild.image_confidence, ai_generated=bild.ai_generated)
    except Exception:
        log.exception("AutoFinder: Bildauflösung fehlgeschlagen — neutrale Defaults")
        return _neutrale_bildfelder(k, bevorzugte_karosserie)


# §Punkt 2: nur Kandidaten mit diesem Fit oder besser gehen in die Ausgabe.
# Sichtbare Empfehlungen im Consumer-UI (Frontend-Anzeigedeckel).
_MAX_AUSGABE = 5
# Fruher lieferte der Endpunkt einen groesseren Pool (8), damit das Frontend
# Kandidaten ohne erzeugbares Bild aus dem finalen Set werfen und ersetzen
# konnte. Mit dem Wegfall der Fahrzeugbilder gibt es kein Image-Ready-Gate
# mehr — die finale Auswahl ist wieder rein fachlich (Candidate Integrity +
# Fit >= FIT_SCHWELLE + Budget + Enrichment) und damit exakt der Ausgabedeckel.
# Weniger als 5 qualifizierte Kandidaten heisst schlicht: weniger Karten.
_KANDIDATEN_POOL = _MAX_AUSGABE


# §Bekannte Punkte: Gemini liefert sie NICHT mehr. Ein Sprachmodell kann eine
# modelltypische Schwäche nicht an Generation/Motor/Baujahr binden — real
# beobachtet: ein "Nockenwellenversteller/Kettentrieb"-Hinweis stand unter
# einer konkreten 220-PS-Variante, ohne dass irgendetwas ihn dieser Variante
# zuordnete. Deshalb sind "bekannte Punkte" jetzt ausschließlich die bereits
# geprüften DB-Fakten des Kandidaten (Schwachstellen/Rückrufe aus
# `get_baureihe()`, siehe app/autofinder._trade_offs_fuer). Ein Fakt, der nur
# an der BAUREIHE hängt, wird auch als solcher gekennzeichnet — er ist nicht
# automatisch eine Eigenschaft genau dieser Motorisierung.
_BAUREIHE_PRAEFIX = "Bekannte Schwachstelle"


def _bekannte_punkte(k) -> list[str]:
    punkte: list[str] = []
    for roh in (getattr(k, "trade_offs", None) or []):
        text = strip_pruef_label(roh)
        if not text:
            continue
        if roh.startswith(_BAUREIHE_PRAEFIX):
            text += " — für die Baureihe dokumentiert, nicht zwingend für genau diese Motorisierung"
        punkte.append(text)
    return punkte


# §Fahranfänger: bei spürbarer Leistung darf keine absolute Eignungsaussage
# stehen. Der Prompt fordert die differenzierte Formulierung bereits an —
# dieser Filter ist die deterministische Absicherung dahinter, denn eine
# Prompt-Regel ist keine Garantie.
_EINSTEIGER_PS_SCHWELLE = 150
_ABSOLUTE_EINSTEIGER_MUSTER = (
    "ideal für fahranfänger", "ideal für einsteiger", "perfekt für fahranfänger",
    "perfekt für einsteiger", "ohne überforderung", "fahrsicher für einsteiger",
    "problemlos für fahranfänger", "bestens für fahranfänger",
    "gut für fahranfänger geeignet", "ideal für den fahranfänger",
)


def _ist_absolute_einsteigeraussage(text: str) -> bool:
    s = text.lower()
    return any(m in s for m in _ABSOLUTE_EINSTEIGER_MUSTER)


def _bereinige_einsteigeraussagen(texte: list[str], leistung_ps: int | None) -> list[str]:
    if leistung_ps is None or leistung_ps < _EINSTEIGER_PS_SCHWELLE:
        return texte
    return [t for t in texte if not _ist_absolute_einsteigeraussage(t)]


def _einsteiger_hinweis(k, body: AutoFinderRequest) -> str | None:
    """Deterministischer Trade-off, wenn jemand als Fahranfänger sucht und die
    Empfehlung spürbar Leistung hat. Kein Werturteil über den Fahrer — die
    Zahl steht so in der Datenbank, die Folgen (Versicherung, Fahrverhalten)
    sind sachlich benannt."""
    if not body.fahranfaenger:
        return None
    ps = getattr(k, "leistung_ps", None)
    if ps is None or ps < _EINSTEIGER_PS_SCHWELLE:
        return None
    return (
        f"{ps} PS sind für den Einstieg viel Leistung: Versicherungseinstufung, "
        "Unterhalt und das Fahrverhalten bei Nässe gehören hier ausdrücklich mit "
        "in die Entscheidung."
    )


# §Budget: eine Überschreitung muss in der angezeigten Passung sichtbar sein
# und nicht nur in einem Nebenlabel. Abzug in Prozentpunkten auf `user_fit` —
# deterministisch aus dem bereits konsolidierten Budget-Status abgeleitet,
# nicht aus einer zweiten Schätzung.
_BUDGET_FIT_ABZUG = {"OUT_OF_BUDGET": 12, "NEAR_BUDGET": 4}


def _fit_nach_budget(fit: int, budget_status: str) -> int:
    return max(0, fit - _BUDGET_FIT_ABZUG.get(budget_status, 0))


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
      3. Cap auf den Kandidaten-Pool (<= _KANDIDATEN_POOL), stabile Fit-Sortierung.
         Das Frontend trimmt daraus die bis zu _MAX_AUSGABE sichtbaren, bild-
         fertigen Empfehlungen (Image-Guarantee).
      4. Budget-Call (nur wenn Budget angegeben) auf GENAU DIESEN Pool -> begrenzte
         Anpassung, stabile Neusortierung innerhalb des Pools.
      5. EIN Enrichment-Call auf den (ggf. neu sortierten) Pool.
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

    # 3) stabile Fit-Sortierung + semantischer Dedupe + Cap
    mit_fit.sort(key=lambda t: (
        -t[0], -t[2].match_score, -t[2].datenqualitaet,
        -(t[2].baujahr_von or 0), kandidat_id(t[2]),
    ))

    # §Candidate Integrity: LETZTES Gate vor der Ausgabe. Interne DB und
    # Web-Recherche können dasselbe reale Fahrzeug liefern (z.B. Ford Focus
    # "Mk4" intern vs. "Vierte Generation" aus dem Web) — der Merge davor
    # gruppiert nur über baureihe_id/candidate_id und kann das nicht sehen.
    # Der Dedupe läuft VOR dem Pool-Cap, damit ein gestrichenes Duplikat
    # einen echten weiteren Kandidaten nachrücken lässt statt einen Platz zu
    # verschwenden — und damit die Image-Ready-Auswahl im Frontend garantiert
    # keine semantischen Duplikate mehr sieht.
    entdupliziert, entfernte_ids = dedupe_semantisch(
        [(t[0], t[2]) for t in mit_fit])
    if entfernte_ids:
        log.info("AutoFinder: %d semantische(s) Duplikat(e) entfernt: %s",
                 len(entfernte_ids), entfernte_ids)
        behalten = {id(k) for _f, k in entdupliziert}
        mit_fit = [t for t in mit_fit if id(t[2]) in behalten]

    final = mit_fit[:_KANDIDATEN_POOL]
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

        ps = getattr(kand, "leistung_ps", None)
        enr.why_fits = _bereinige_einsteigeraussagen(list(enr.why_fits), ps)
        enr.trade_offs = _bereinige_einsteigeraussagen(list(enr.trade_offs), ps)
        einsteiger = _einsteiger_hinweis(kand, body)
        if einsteiger and einsteiger not in enr.trade_offs:
            enr.trade_offs = [einsteiger] + list(enr.trade_offs)

        # §Budget/Preis-Konsistenz: Budget-Kategorie und Preisorientierung
        # kommen aus zwei getrennten Gemini-Calls und konnten sich bisher
        # widersprechen ("Im Budget" neben "ca. 27.000–39.000 €" bei 25.000 €
        # Budget). Liegt eine Spanne vor, ist sie die belastbarere Aussage —
        # der ANGEZEIGTE Status wird deterministisch daraus abgeleitet.
        # `budget_adjustment` (Ranking) bleibt bewusst unverändert: die
        # Reihenfolge steht zu diesem Zeitpunkt bereits fest, und das
        # Enrichment darf das Ranking nicht nachträglich umwerfen.
        b_status, b_conf = konsolidiere_budget_status(
            b_status, b_conf,
            preis_min=enr.estimated_price_min,
            preis_max=enr.estimated_price_max,
            preis_confidence=enr.price_confidence,
            budget_min=body.budget_min, budget_max=body.budget_max,
        )

        outs.append(_zu_kandidat_out(
            kand, _konkrete_variante(kand, body),
            budget_status=b_status, budget_confidence=b_conf,
            budget_adjustment=b_anp, bevorzugte_karosserie=bevorzugte_karosserie,
            user_fit=_fit_nach_budget(score, b_status), user_fit_gruende=gruende,
            enrichment=enr, enrichment_status=e_status,
            known_points=_bekannte_punkte(kand),
        ))

    # §Finale Validierung: die fertige Antwort noch einmal gegen die harten
    # Filter halten. Zwischen Hard-Filter und hier liegen Merge, Dedupe,
    # Budget-Umsortierung, Enrichment und die Variantenauflösung — ein
    # Kandidat, der danach den Wunsch verletzt, wird ausgeliefert statt
    # bemerkt. Das darf nicht passieren, auch nicht bei einem Fehler in einer
    # dieser Stufen.
    geprueft: list[AutoFinderKandidatOut] = []
    for out in outs:
        verstoesse = pruefe_hardfilter_einhaltung(out, engine_request)
        if verstoesse:
            log.error("AutoFinder: Kandidat %s verletzt Hard-Filter und wird verworfen: %s",
                      out.candidate_id,
                      "; ".join(f"{v.feld}: erwartet {v.erwartet}, ist {v.tatsaechlich}"
                                for v in verstoesse))
            continue
        geprueft.append(out)
    outs = geprueft
    if not outs:
        return _FinalErgebnis([], "no_strong_match", warnungen, None,
                              budget_ausgefallen, budget_aufgerufen)

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
    summary="AutoFinder: kostenlose Fahrzeugempfehlung aus der ENFAL-Datenbank",
)
@limiter.limit(_AUTOFINDER_RATE_LIMIT)
@provider_action("autofinder")
async def autofinder_endpunkt(body: AutoFinderRequest, request: Request):
    verify_api_key(request)

    # LEERE SUCHE — vor allem anderen, was Geld oder Kontingent kostet.
    # Eine Anfrage ohne ein einziges auswertbares Kriterium ist keine Suche:
    # sie darf weder das Monatskontingent belasten noch Gemini oder Tavily
    # anfassen. Die Pruefung steht deshalb VOR `require_autofinder_kontingent`
    # und vor jedem Datenbankzugriff. Bewusst nicht strenger als noetig — EIN
    # Kriterium genuegt, niemand muss das Formular ausfuellen.
    if not hat_verwertbares_kriterium(body):
        raise HTTPException(status_code=422, detail=LEERE_SUCHE_MELDUNG)

    # Monatliches Kontingent (Free 5 / Plus 50) VOR jeder Datenbank- und
    # Provider-Arbeit: eine ueberschrittene Grenze soll keine Kosten mehr
    # verursachen. Ergaenzt das bestehende Rate-Limit (20/min), ersetzt es nicht.
    require_autofinder_kontingent(request)

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
                "Web-Recherche zu Fahrzeugmodellen, die ENFAL intern noch nicht "
                "pflegt — technische Angaben dort sind belegt, aber nicht "
                "ENFAL-geprüft."
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
# BILD-ON-DEMAND — PRODUKTENTSCHEIDUNG: CONSUMER-SEITIG ABGESCHALTET
# ══════════════════════════════════════════════════════════════════════════
# AutoFinder zeigt keine modellgenauen Fahrzeugbilder mehr. Die Karten tragen
# stattdessen ein gestaltetes ENFAL Vehicle Identity Panel (Frontend).
#
# WARUM DER ENSURE-ENDPUNKT KOMPLETT WEG IST — und nicht nur ungenutzt bleibt:
# er war der EINZIGE Pfad, über den ein oeffentlicher Consumer-Request eine
# kostenpflichtige Gemini-Bildgenerierung ausloesen konnte. Ein bloss vom
# Frontend nicht mehr aufgerufener, aber weiter erreichbarer Endpunkt haette
# genau dieses Kostenrisiko offen gelassen (jeder Aufruf mit gueltigem
# API-Key haette weiter generiert). Deshalb: aus dem Router entfernt.
# Ergebnis: AUTOFINDER RUNTIME IMAGE COST = 0.
#
# Der OFFLINE-Code (app/autofinder_generation.py, app/autofinder_images.py,
# scripts/autofinder_*) bleibt bewusst unangetastet im Repo: er ist ein
# Admin-/Batch-Werkzeug ohne oeffentliche Route, seine Entfernung waere
# reines Risiko ohne Nutzen. Ohne Router-Eintrag ist er von aussen nicht
# mehr erreichbar.
#
# Die Ausliefer-Route unten bleibt: sie liest ausschliesslich bereits
# vorhandene Dateien vom Datentraeger, ruft NIE einen Provider und kann damit
# keine Kosten erzeugen. Sie versorgt weiterhin die Offline-Review-Werkzeuge.


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
