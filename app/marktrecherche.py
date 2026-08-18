from __future__ import annotations

"""
Adaptive, QUALITÄTS-GESTEUERTE Marktrecherche (Reliability-Sprint 3, §8/§17-§21).

Frühere Logik stoppte, sobald "mittel" erreicht war — populäre Fahrzeuge landeten
dann oft dünn ("niedrig"/"mittel"), obwohl mit weiterer Recherche eine belastbare
Basis erreichbar gewesen wäre. Das widerspricht der Gold-Anforderung (§0): eine
niedrige Datenqualität darf KEIN abgeschlossenes bezahltes Endergebnis sein.

Diese Funktion sucht in mehreren Stufen (Query Planner, §8), AKKUMULIERT die Treffer
und bewertet nach JEDER Stufe die Zahl der AKZEPTIERTEN, modelltreuen Vergleiche via
analysiere_markt. Sie vertieft weiter, solange die Qualität noch nicht "hoch" ist,
und endet erst, wenn "hoch" erreicht ist ODER alle Stufen (inkl. der generischen
Rare-Vehicle-Erweiterung, §19) ausgeschöpft sind — nie anhand roher Trefferzahl.
Kein Fahrzeug-Hardcoding.

Reicht die Qualität nach voller Vertiefung nicht (kein belastbarer Median), gilt der
Check als `research_failed` (§21): der aufrufende Flow erzeugt KEINEN fertigen
Bericht und erstattet das Kontingent zurück. `research_failed` unterscheidet dabei
zwischen einem ECHTEN technischen Ausfall (Suchprovider nicht erreichbar, kein
API-Key) und Datenknappheit trotz vollständig ausgeschöpfter Recherche — für die
Nutzermeldung (§33) und die Diagnose (§34).
"""

import logging
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.marktvergleich import analysiere_markt
from app.vehicle_identity import VehicleIdentity
from app.marktvergleich import ist_teile_suchseite
from app.web_search import (
    CLASSIC_AUKTION_DOMAINS,
    MARKTPLATZ_DOMAINS,
    hat_brauchbaren_raw_content,
    hole_raw_content,
    ist_info_domain,
    ist_marktplatz_domain,
    tavily_search_mit_status,
)

log = logging.getLogger(__name__)


class RechercheUnzureichend(Exception):
    """Nach voller Recherche keine belastbare Preisbasis (§21). Trägt die (schwache)
    Marktanalyse, den Grund (technical_failure|data_exhausted) und eine passende
    Nutzermeldung mit — der Router erstattet das Kontingent zurück und liefert KEINEN
    fertigen Bericht."""

    def __init__(self, marktanalyse, nachricht: str, grund: str = "data_exhausted"):
        self.marktanalyse = marktanalyse
        self.nachricht = nachricht
        self.grund = grund               # "technical_failure" | "data_exhausted"
        super().__init__(nachricht)


NACHRICHT_TECHNISCH = (
    "VIRA konnte die externe Marktrecherche gerade technisch nicht zuverlässig "
    "abschließen (Suchdienst nicht erreichbar). Dieser Check wurde deshalb NICHT "
    "abgeschlossen und NICHT von deinem Kontingent abgezogen. Bitte versuche es "
    "gleich erneut."
)


def nachricht_unzureichend(identity: "VehicleIdentity | None", grund: str) -> str:
    """Nutzerfreundliche Meldung (§33): berücksichtigt tatsächlich eingegebene Daten
    — 'Fahrzeug genauer angeben' wird NICHT vorgeschlagen, wenn Motor/Baujahr/km
    bereits vorliegen, und technische Ausfälle werden nicht als Datenproblem des
    Nutzers dargestellt."""
    if grund == "technical_failure":
        return NACHRICHT_TECHNISCH

    fehlende_hinweise: list[str] = []
    if identity is not None:
        if not (identity.engine_name or identity.displacement):
            fehlende_hinweise.append("Motor")
        if not identity.year:
            fehlende_hinweise.append("Baujahr")
        if not identity.mileage:
            fehlende_hinweise.append("Kilometerstand")

    basis = (
        "VIRA konnte für dieses Fahrzeug aktuell keine belastbare Marktdatenbasis finden — "
        "es liegen zu wenige wirklich vergleichbare Angebote vor, um einen zuverlässigen "
        "Marktwert und eine belastbare Preisbewertung zu erstellen. Dieser Check wurde "
        "deshalb NICHT abgeschlossen und NICHT von deinem Kontingent abgezogen. Du kannst "
        "es gleich erneut versuchen"
    )
    if fehlende_hinweise:
        return basis + f" oder das Fahrzeug genauer angeben ({', '.join(fehlende_hinweise)})."
    # Alle wesentlichen Angaben liegen bereits vor (§33) — nicht so tun, als läge es
    # am Nutzer.
    return basis + "."


# Rückwärtskompatibler Alias (alte Importe / Tests) — feste, konservative Meldung.
NACHRICHT_UNZUREICHEND = nachricht_unzureichend(None, "data_exhausted")


def _norm_url(url: str) -> str:
    try:
        p = urlparse(url or "")
        return f"{p.netloc.lower()}{p.path.rstrip('/')}".removeprefix("www.")
    except Exception:
        return url or ""


def _merge(dst: list[dict], seen: set[str], results: list[dict]) -> int:
    """Fügt nur echt neue URLs hinzu (dedupliziert über Stufen). Gibt #neu zurück."""
    neu = 0
    for r in results or []:
        k = _norm_url(r.get("url", ""))
        if k and k not in seen:
            seen.add(k)
            dst.append(r)
            neu += 1
    return neu


# Sicherheitsdeckel für die Extract-Nachladung je Marktanalyse. BEWUSST großzügig:
# er soll einen Ausreißer begrenzen, nicht die Qualität steuern. Der reale Bedarf
# liegt nach Messung deutlich darunter (siehe Diagnose-Statistik `extract`).
MAX_EXTRACT_URLS = 24


def ist_extract_kandidat(r: dict) -> bool:
    """Lohnt es, für diesen Suchtreffer den Seiteninhalt nachzuladen (§2)?

    Nur echte Fahrzeugmarktplätze ohne brauchbaren Inhalt. Ratgeber-/Info-Seiten und
    Teile-/Zubehör-Suchen liefern ohnehin keine Marktbeobachtung — für sie wäre der
    Extract verschwendetes Budget. Treffer, die bereits genug `raw_content`
    mitbringen, werden nie nachgeladen.
    """
    url = r.get("url") or ""
    if not url or hat_brauchbaren_raw_content(r):
        return False
    if ist_info_domain(url) or ist_teile_suchseite(url, r.get("title")):
        return False
    return ist_marktplatz_domain(url)


async def ergaenze_raw_content(results: list[dict], budget: int) -> tuple[int, dict, int]:
    """Lädt fehlenden Seiteninhalt für relevante Marktseiten nach.

    Der nachgeladene Inhalt wird IN DIE VORHANDENEN Treffer geschrieben — es
    entsteht keine zweite Auswertungsschiene: die Karten-Segmentierung und die
    Fahrzeugvalidierung laufen anschließend exakt wie bei Treffern, die ihren
    `raw_content` direkt aus der Suche mitgebracht haben.

    Gibt (Anzahl ergänzter Treffer, Statistik, verbrauchtes Budget) zurück.
    """
    if budget <= 0:
        return 0, {}, 0
    kandidaten = [r for r in results if ist_extract_kandidat(r)][:budget]
    if not kandidaten:
        return 0, {}, 0
    inhalte, statistik = await hole_raw_content([r["url"] for r in kandidaten])
    ergaenzt = 0
    for r in kandidaten:
        inhalt = inhalte.get(r["url"])
        if inhalt:
            r["raw_content"] = inhalt
            ergaenzt += 1
    return ergaenzt, statistik, len(kandidaten)


def genug(ma) -> bool:
    """Abbruchkriterium der Recherche: das bestmögliche Ergebnis ist erreicht.

    Bewusst NUR `completed_high` (§11). Ein bereits erreichtes `completed_medium`
    beendet die Suche NICHT — es kann durch zusätzliche Plattformen noch zu HIGH
    werden. Das war früher gefährlich, weil `analysiere_markt` immer auf dem
    GESAMTEN akkumulierten Pool rechnet und eine spätere, breitere Stufe ein gutes
    Zwischenergebnis wieder verwässern konnte. Genau davor schützt jetzt die
    best_so_far-Logik in `vertiefe_marktrecherche`: weitergesucht wird, aber ein
    einmal erreichter Stand kann nicht mehr verloren gehen.
    """
    return research_status(ma) == "completed_high"


def research_status(ma) -> str:
    """Quality-Gate-Ergebnis (§0/§17): completed_high | completed_medium | research_failed.

    Zwei UNABHÄNGIGE Achsen (§2/§9/§10):
      - Datenqualität   = wie zuverlässig/ähnlich sind die einzelnen Fahrzeuge
      - Marktabdeckung  = wie viele unabhängige Plattformen haben beigetragen

    Regeln:
      - "hoch"   + Median + Marktabdeckung "gut"/"breit"  -> completed_high
      - "hoch"   + Median + nur EINE Plattform            -> completed_medium
      - "mittel" + Median                                 -> completed_medium
      - sonst (kein Median / "niedrig")                   -> research_failed

    Zusätzliche Deckelung (§B): musste die Preisstatistik mangels ausreichend guter
    Vergleiche auf nur "bedingt" passende Beobachtungen zurückgreifen
    (`fallback_bedingt`), ist das Ergebnis höchstens completed_medium — auch wenn
    Streuung und Trefferzahl formal für "hoch" reichen würden.

    Der Single-Source-Deckel (§9) ist die ehrliche Aussage, nicht ein Abbruch: eine
    saubere Basis aus acht validierten Angeboten EINER Plattform liefert ein
    Ergebnis — mit hoher Datenqualität, eingeschränkter Marktabdeckung und
    höchstens mittlerem Gesamtvertrauen. Eine einzige Domain führt ausdrücklich
    NICHT mehr automatisch zu research_failed.
    """
    if not (ma and ma.median_eur):
        return "research_failed"
    if ma.datenqualitaet == "hoch":
        if getattr(ma, "fallback_bedingt", False):
            return "completed_medium"
        return "completed_high" if ma.marktabdeckung in ("gut", "breit") else "completed_medium"
    if ma.datenqualitaet == "mittel":
        return "completed_medium"
    return "research_failed"


# Rangfolge für den best_so_far-Vergleich (§11) — je höher, desto besser.
_STATUS_RANG = {"research_failed": 0, "completed_medium": 1, "completed_high": 2}
_QUALI_RANG = {"niedrig": 0, "mittel": 1, "hoch": 2}
_ABDECKUNG_RANG = {"eingeschraenkt": 0, "gut": 1, "breit": 2}


def bewertungsrang(ma) -> tuple[int, int, int, int]:
    """Vergleichbare Güte einer Marktanalyse (§11).

    Reihenfolge der Kriterien: Gesamtstatus zuerst (ein auslieferbares Ergebnis
    schlägt jedes nicht auslieferbare), dann Datenqualität, dann Marktabdeckung,
    zuletzt die Zahl der verwendeten Vergleiche als Feinunterscheidung.
    """
    if not ma:
        return (0, 0, 0, 0)
    return (
        _STATUS_RANG.get(research_status(ma), 0),
        _QUALI_RANG.get(ma.datenqualitaet, 0),
        _ABDECKUNG_RANG.get(getattr(ma, "marktabdeckung", "eingeschraenkt"), 0),
        ma.verwendet or 0,
    )


def research_failure_grund(ma, hatte_technischen_fehler: bool) -> str:
    """Feinere Unterscheidung für research_failed (§21): 'technical_failure' (echter
    Such-/API-Ausfall, KEINE brauchbaren Rohtreffer über alle Stufen) vs.
    'data_exhausted' (Suche lief technisch durch, aber selbst nach voller Vertiefung
    keine belastbare Vergleichsbasis). Nur aussagekräftig, wenn research_status()
    bereits 'research_failed' ergeben hat."""
    if hatte_technischen_fehler and (not ma or not ma.gefunden):
        return "technical_failure"
    return "data_exhausted"


@dataclass
class QueryStufe:
    """Eine Stufe des Markt-Query-Planners (§8/§9) — dokumentiert zu Diagnosezwecken
    (§34), welche VehicleIdentity-Felder tatsächlich in die Query eingeflossen sind."""

    query: str
    include_domains: list[str] | None
    label: str
    felder: list[str] = field(default_factory=list)
    raw_content: bool = False
    # §Phase 4 (Diagnose): Komplement zu `felder` — am Fahrzeug vorhandene, aber auf
    # dieser Stufe bewusst NICHT verwendete VehicleIdentity-Felder. Wird nach dem
    # Bau aller Stufen einmalig befüllt (siehe Ende von baue_deep_queries/
    # baue_rare_queries), rein informativ für scripts/diagnose_recherche.py.
    weggelassene_felder: list[str] = field(default_factory=list)


# §Phase 4 (Diagnose): vollständige Menge der VehicleIdentity-Felder, die der
# Query-Planner grundsätzlich verwenden KÖNNTE — Grundlage für "bewusst
# weggelassene Felder" je Stufe (Komplement zu QueryStufe.felder).
_ALLE_IDENTITY_FELDER = (
    "make", "model", "model_variant", "generation", "engine_name", "displacement",
    "fuel", "horsepower", "transmission", "year", "mileage",
)


def _weggelassen(identity: "VehicleIdentity", verwendet: list[str]) -> list[str]:
    """Von der Stufe bewusst NICHT verwendete, aber am Fahrzeug tatsächlich
    vorhandene Felder (§Phase 4) — reine Diagnose-Transparenz, ändert keine Logik."""
    return [f for f in _ALLE_IDENTITY_FELDER
            if f not in verwendet and getattr(identity, f, None)]


def _q(*teile) -> str:
    return " ".join(str(t) for t in teile if t)


def _ohne_dup(basis: str, *zusatz) -> str:
    """Hängt `zusatz`-Teile nur an, wenn sie nicht schon (case-insensitiv) Teil von
    `basis` sind — verhindert Wiederholungen wie 'BMW 320d G20 320d ...', wenn die
    essenzielle Bezeichnung die Motorbezeichnung bereits enthält."""
    b = basis.lower()
    neu = [t for t in zusatz if t and str(t).lower() not in b]
    return _q(basis, *neu)


def baue_deep_queries(identity: VehicleIdentity) -> list[QueryStufe]:
    """Markt-Query-Planner (§8/§9): baut gestufte Suchanfragen aus der zentralen
    VehicleIdentity — von eng/portal-spezifisch bis breit/Fallback. Jede Stufe
    dokumentiert, welche Felder tatsächlich verwendet wurden (§9), damit sich in der
    Diagnose (§34) nachvollziehen lässt, ob die Nutzerdaten wirklich einflossen.

    Kein `site:`-Text im Query-String (Doppelrestriktion mit `include_domains`,
    siehe Kommentar in app/routers/ersatzteile.py) — Portal-Fokussierung läuft
    ausschließlich über Tavilys strukturiertes `include_domains`.
    """
    make, model = identity.make, identity.model
    variant = identity.model_variant
    code = identity.generation_code()
    motor = identity.motor_kurz()
    ps = identity.leistung_kurz()
    jahr = identity.year
    getriebe = identity.transmission
    essenziell = identity.essenziell() or _q(make, model)

    stufen: list[QueryStufe] = []

    # STUFE A — eng, allgemein (kein Domain-Filter): essenziell + Motor/Leistung/
    # Baujahr/Getriebe, so nah wie möglich an einer echten Verkaufsanzeige formuliert.
    stufen.append(QueryStufe(
        query=_ohne_dup(essenziell, motor, ps, getriebe, jahr, "gebraucht"),
        include_domains=None, label="eng",
        felder=[f for f in ("make", "model", "model_variant", "generation",
                            "engine_name", "horsepower", "transmission", "year") if getattr(identity, f, None)],
    ))
    stufen.append(QueryStufe(
        query=_ohne_dup(essenziell, identity.displacement, identity.fuel, ps, getriebe, jahr),
        include_domains=None, label="eng-variante",
        felder=[f for f in ("displacement", "fuel", "horsepower", "transmission", "year") if getattr(identity, f, None)],
    ))

    # STUFE B — dieselbe enge Formulierung, portal-spezifisch (§8: mobile.de,
    # autoscout24.de, kleinanzeigen.de, autouncle — kein site:-Text, nur include_domains).
    # raw_content=True (Reliability-Sprint-4-Nachbesserung, empirisch belegt per
    # scripts/diagnose_bmw320d_root_cause.py + direktem Tavily-Extract-Vergleich):
    # die kurzen Search-Snippets dieser Portale reichten oft nicht für extrahierbare
    # Preis-Datenpunkte, während derselbe volle Seiteninhalt (raw_content/Extract)
    # deutlich mehr lieferte (kleinanzeigen.de: 37 statt vereinzelter Punkte).
    # raw_content kostet KEINEN zusätzlichen Tavily-Credit (search_depth bleibt
    # "basic") — nur mehr Text pro ohnehin bezahltem Treffer.
    stufen.append(QueryStufe(
        query=_ohne_dup(essenziell, motor, jahr, getriebe, "gebraucht kaufen"),
        include_domains=MARKTPLATZ_DOMAINS, label="portal-eng", raw_content=True,
        felder=[f for f in ("make", "model", "generation", "engine_name", "year") if getattr(identity, f, None)],
    ))

    # STUFE C — kontrolliertes Fenster: Baujahr ±1, Kilometer-Fenster (±20-30 %,
    # als grobe Textangabe), gleicher Motor/Fuel/Generation.
    if jahr:
        km_hinweis = None
        if identity.mileage:
            lo = int(identity.mileage * 0.75 // 5000 * 5000)
            hi = int(identity.mileage * 1.25 // 5000 * 5000)
            km_hinweis = f"{lo}-{hi} km"
        stufen.append(QueryStufe(
            query=_ohne_dup(essenziell, motor, f"{jahr - 1} {jahr} {jahr + 1}", km_hinweis, "gebraucht"),
            include_domains=MARKTPLATZ_DOMAINS, label="fenster-jahr-km", raw_content=True,
            felder=[f for f in ("make", "model", "engine_name", "year", "mileage") if getattr(identity, f, None)],
        ))

    # STUFE D — gleiche Generation + gleicher Motor, größeres Fenster, kein Domain-Filter.
    stufen.append(QueryStufe(
        query=_q(make, model, code, motor, "gebraucht Deutschland"),
        include_domains=None, label="generation-motor",
        felder=[f for f in ("make", "model", "generation", "engine_name") if getattr(identity, f, None)],
    ))
    if jahr:
        stufen.append(QueryStufe(
            query=_q(make, model, f"{jahr - 2} bis {jahr + 2}", "gebrauchtwagen Deutschland"),
            include_domains=None, label="fenster-erweitert",
            felder=[f for f in ("make", "model", "year") if getattr(identity, f, None)],
        ))

    # STUFE E — breiter Fallback (schwächer gewichtet): alternative Bezeichnung,
    # allgemeine Preisübersicht, weiteres Portal für Quellenvielfalt. Ab hier
    # Raw-Content aktivieren — mehr Text = mehr extrahierbare Preis-Datenpunkte,
    # wenn die Snippets bislang dünn waren.
    stufen.append(QueryStufe(
        query=_q(make, model, jahr, "Gebrauchtpreis Deutschland"),
        include_domains=None, label="breit-alternativ", raw_content=True,
        felder=[f for f in ("make", "model", "year") if getattr(identity, f, None)],
    ))
    stufen.append(QueryStufe(
        query=_q(make, model, "gebrauchtwagen kaufen Deutschland Preis"),
        include_domains=None, label="breit", raw_content=True,
        felder=[f for f in ("make", "model") if getattr(identity, f, None)],
    ))
    stufen.append(QueryStufe(
        query=_q(make, model, code, "gebrauchtwagen Angebote autouncle"),
        include_domains=None, label="breit-portal", raw_content=True,
        felder=[f for f in ("make", "model", "generation") if getattr(identity, f, None)],
    ))
    if variant and variant.lower() not in essenziell.lower():
        stufen.append(QueryStufe(
            query=_q(make, model, variant, "gebraucht Deutschland"),
            include_domains=None, label="variante-fallback", raw_content=True,
            felder=[f for f in ("make", "model", "model_variant") if getattr(identity, f, None)],
        ))

    # Deduplizieren (gleicher Query-Text), Reihenfolge erhalten.
    gesehen: set[str] = set()
    out: list[QueryStufe] = []
    for s in stufen:
        s.query = s.query.strip()
        if s.query and s.query not in gesehen:
            gesehen.add(s.query)
            s.weggelassene_felder = _weggelassen(identity, s.felder)
            out.append(s)
    return out


def baue_rare_queries(identity: VehicleIdentity) -> list[QueryStufe]:
    """Generische Rare-/Collector-Rechercheleiter (§19) — wird NICHT anhand einer
    "ist das ein seltenes Auto"-Erkennung aktiviert, sondern rein anhand von
    Datennot NACH der Standard-Ladder (siehe vertiefe_marktrecherche). Für ein
    verbreitetes Fahrzeug wird diese Ladder praktisch nie erreicht. Kein
    Fahrzeug-Hardcoding — allgemeine Sammler-/Auktions-/Spezialhändler-Begriffe und
    generische Classic-/Auktionsportale (app.web_search.CLASSIC_AUKTION_DOMAINS).
    """
    make, model = identity.make, identity.model
    variant = identity.model_variant
    jahr = identity.year
    essenziell = identity.essenziell() or _q(make, model)

    stufen: list[QueryStufe] = [
        # Stufe 2: historische Inserate / Verkaufsarchive.
        QueryStufe(_q(essenziell, jahr, "historisches Inserat Verkaufsarchiv"),
                  None, "rare-historisch", raw_content=True,
                  felder=["make", "model", "year"]),
        # Stufe 3: dokumentierte Auktionsergebnisse.
        QueryStufe(_q(essenziell, "Auktion Ergebnis verkauft Zuschlag"),
                  None, "rare-auktion", raw_content=True,
                  felder=["make", "model", "model_variant"]),
        # Stufe 4: Classic-/Collector-Marktplätze (generische Domain-Gruppe).
        QueryStufe(_q(essenziell, jahr, "for sale"),
                  CLASSIC_AUKTION_DOMAINS, "rare-classic-portal", raw_content=True,
                  felder=["make", "model", "year"]),
        # Stufe 5: Spezialhändler.
        QueryStufe(_q(essenziell, "Spezialhändler Oldtimerhandel Sammlerfahrzeug"),
                  None, "rare-spezialhaendler", raw_content=True,
                  felder=["make", "model"]),
        # Stufe 6: angrenzende Baujahre / gleiche Variante.
        QueryStufe(_q(make, model, variant, f"{jahr - 3}-{jahr + 3}" if jahr else None, "verkauft"),
                  None, "rare-baujahr-fenster", raw_content=True,
                  felder=[f for f in ("make", "model", "model_variant", "year") if getattr(identity, f, None)]),
        # Stufe 7: internationaler Markt (deutscher Markt zu klein).
        QueryStufe(_q(make, model, variant, "for sale price"),
                  None, "rare-international", raw_content=True,
                  felder=[f for f in ("make", "model", "model_variant") if getattr(identity, f, None)]),
    ]
    gesehen: set[str] = set()
    out: list[QueryStufe] = []
    for s in stufen:
        s.query = s.query.strip()
        if s.query and s.query not in gesehen:
            gesehen.add(s.query)
            s.weggelassene_felder = _weggelassen(identity, s.felder)
            out.append(s)
    return out


async def vertiefe_marktrecherche(
    initial_results: list[dict],
    deep_queries: list[QueryStufe],
    ziel: dict,
    angebot_eur: int | None,
    exclude_domains: list[str] | None,
    *,
    count: int = 10,
    zweck: str = "marktvergleich",
    max_stufen: int = 9,
    rare_queries: list[QueryStufe] | None = None,
    bypass_cache: bool = False,
):
    """Vertieft die Marktrecherche adaptiv bis zur Qualitätsschwelle. Gibt
    (accumulated_results, marktanalyse, diagnose) zurück.

    Läuft die Standard-Ladder (`deep_queries`) vollständig durch, ohne "hoch" zu
    erreichen, UND bleibt die Datenbasis dünn (verwendet < 3), wird die generische
    Rare-Vehicle-Erweiterung (`rare_queries`, §19) automatisch angehängt — nicht
    weil das Fahrzeug als "selten" erkannt wurde, sondern weil die Standard-Suche
    objektiv zu wenig gefunden hat. `bypass_cache` (§22, Retry) erzwingt frische
    Tavily-Calls statt derselben ggf. dünnen gecachten Antwort.
    """
    t0 = time.perf_counter()
    accumulated: list[dict] = list(initial_results or [])
    seen: set[str] = {_norm_url(r.get("url", "")) for r in accumulated}
    hatte_technischen_fehler = False
    # Extract-Nachladung: Budget und Verbrauch über ALLE Query-Stufen hinweg.
    extract_budget_verbraucht = 0
    extract_gesamt: dict[str, int] = {}
    extract_ergaenzt = 0

    ma = analysiere_markt(accumulated, ziel, angebot_eur)
    stufen_log: list[dict] = [{
        "stufe": "initial", "label": "initial", "roh": len(accumulated), "neu": len(accumulated),
        "akzeptiert": ma.verwendet, "quali": ma.datenqualitaet,
        "abdeckung": ma.marktabdeckung, "felder": [],
        "hintergrund_domains": list(ma.hintergrund_domains),
    }]

    # ── best_so_far (§11, verpflichtend) ─────────────────────────────────────
    # Ein bereits brauchbares Ergebnis darf durch spätere Recherche NIEMALS
    # verschlechtert werden. `analysiere_markt` rechnet immer auf dem GESAMTEN
    # akkumulierten Pool — eine spätere, breitere Stufe (bis hin zur Rare-Ladder)
    # kann einen guten Zwischenstand also nachträglich verwässern. Deshalb wird
    # nach JEDER Stufe der beste bisher erreichte Stand samt der zu ihm gehörenden
    # Trefferliste festgehalten und am Ende zurückgegeben. Weitergesucht wird
    # trotzdem — nur verlieren kann man nichts mehr.
    best_ma = ma
    best_results: list[dict] = list(accumulated)
    best_rang = bewertungsrang(ma)
    best_stufe = "initial"
    best_verlauf: list[dict] = [{
        "stufe": "initial", "rang": list(best_rang), "uebernommen": True,
        "status": research_status(ma), "quali": ma.datenqualitaet,
        "abdeckung": ma.marktabdeckung, "verwendet": ma.verwendet,
    }]

    def _pruefe_best(stufe_name: str) -> bool:
        """Übernimmt den aktuellen Stand, wenn er besser ist. Gibt zurück, ob übernommen."""
        nonlocal best_ma, best_results, best_rang, best_stufe
        rang = bewertungsrang(ma)
        besser = rang > best_rang
        if besser:
            best_ma, best_results, best_rang, best_stufe = ma, list(accumulated), rang, stufe_name
        best_verlauf.append({
            "stufe": stufe_name, "rang": list(rang), "uebernommen": besser,
            "status": research_status(ma), "quali": ma.datenqualitaet,
            "abdeckung": ma.marktabdeckung, "verwendet": ma.verwendet,
            "bester_stand": best_stufe,
        })
        return besser

    async def _laufe_stufen(stufen: list[QueryStufe], praefix: str) -> None:
        nonlocal ma, hatte_technischen_fehler, extract_budget_verbraucht, extract_ergaenzt
        for i, stufe in enumerate(stufen[:max_stufen], 1):
            if not stufe.query or not stufe.query.strip():
                continue
            results, fehler = await tavily_search_mit_status(
                stufe.query, count=count, include_domains=stufe.include_domains,
                exclude_domains=exclude_domains, include_raw_content=stufe.raw_content,
                bypass_cache=bypass_cache,
            )
            hatte_technischen_fehler = hatte_technischen_fehler or fehler
            # Fehlenden Seiteninhalt VOR dem Merge nachladen — sonst landet ein
            # Treffer ohne raw_content im Pool und wird von `_merge` bei einer
            # späteren Stufe als Dublette abgewiesen, obwohl er dort mit Inhalt
            # gekommen wäre.
            ergaenzt, extract_stat, verbraucht = await ergaenze_raw_content(
                results, MAX_EXTRACT_URLS - extract_budget_verbraucht)
            extract_budget_verbraucht += verbraucht
            extract_ergaenzt += ergaenzt
            for k, v in (extract_stat or {}).items():
                extract_gesamt[k] = extract_gesamt.get(k, 0) + v
            neu = _merge(accumulated, seen, results)
            ma = analysiere_markt(accumulated, ziel, angebot_eur)
            stufen_name = f"{praefix}{i}"
            stufen_log.append({
                "stufe": stufen_name, "label": stufe.label, "query": stufe.query[:100],
                "domains": stufe.include_domains, "felder": stufe.felder,
                # §Phase 4: bewusst weggelassene Felder je Stufe (Diagnose-
                # Transparenz — welche Nutzerdaten in dieser Stufe NICHT einflossen).
                "weggelassene_felder": stufe.weggelassene_felder,
                "raw_content": stufe.raw_content,
                "roh": len(results), "neu": neu, "akzeptiert": ma.verwendet,
                "extract_ergaenzt": ergaenzt,
                "quali": ma.datenqualitaet, "abdeckung": ma.marktabdeckung,
                "hintergrund_domains": list(ma.hintergrund_domains),
            })
            _pruefe_best(stufen_name)
            if genug(ma):
                return

    if not genug(ma):
        await _laufe_stufen(deep_queries, "")

        # §19: generische Rare-Vehicle-Erweiterung NUR bei objektiver Datennot nach
        # voller Standard-Ladder — nicht an eine Fahrzeugerkennung gekoppelt. "Datennot"
        # heißt: der BESTE bisher erreichte Stand (nicht der zufällig letzte) wäre
        # research_failed — kein belastbarer Median ODER Qualität weiterhin "niedrig".
        # NICHT nur eine rohe verwendet-Zählung, denn auch mit mehreren Datenpunkten
        # kann der Streuungs-Guard in analysiere_markt() den Median verwerfen (§0:
        # kein Schein-Median trotz vorhandener Rohdaten).
        if research_status(best_ma) == "research_failed" and rare_queries:
            await _laufe_stufen(rare_queries, "rare-")

    # §11: IMMER die beste während der gesamten Recherche erreichte Analyse
    # zurückgeben — nie den zufällig letzten (womöglich verwässerten) Stand.
    ma = best_ma
    accumulated = best_results
    status = research_status(ma)
    grund = research_failure_grund(ma, hatte_technischen_fehler) if status == "research_failed" else None
    diagnose = {
        "zweck": zweck,
        "stufen": stufen_log,
        "roh_seiten": len(accumulated),
        "gefunden_datenpunkte": ma.gefunden if ma else 0,
        "akzeptiert": ma.verwendet if ma else 0,
        "verworfen": (ma.gefunden - ma.verwendet) if ma else 0,
        "datenqualitaet": ma.datenqualitaet if ma else "niedrig",
        "marktabdeckung": ma.marktabdeckung if ma else "eingeschraenkt",
        "anzahl_domains": ma.anzahl_domains if ma else 0,
        # §11: nachvollziehbarer best_so_far-Verlauf für die Diagnose.
        "best_so_far": best_verlauf,
        "bester_stand_stufe": best_stufe,
        "research_status": status,
        "research_failure_grund": grund,
        "hatte_technischen_fehler": hatte_technischen_fehler,
        # §7: Verbrauch der Extract-Nachladung (Calls, URLs, Cache-Treffer, Fehler).
        "extract": dict(extract_gesamt, ergaenzte_seiten=extract_ergaenzt,
                        budget=MAX_EXTRACT_URLS, budget_verbraucht=extract_budget_verbraucht),
        "domains": list(ma.quellen_domains) if ma else [],
        "hintergrund_domains": list(ma.hintergrund_domains) if ma else [],
        "dauer_ms": round((time.perf_counter() - t0) * 1000),
    }
    log.info("[RECHERCHE %s] status=%s grund=%s akzeptiert=%d/%d quali=%s abdeckung=%s "
             "bester_stand=%s stufen=%d dauer=%dms domains=%s",
             zweck, status, grund, diagnose["akzeptiert"], diagnose["gefunden_datenpunkte"],
             diagnose["datenqualitaet"], diagnose["marktabdeckung"], best_stufe,
             len(stufen_log), diagnose["dauer_ms"], diagnose["domains"])
    return accumulated, ma, diagnose
