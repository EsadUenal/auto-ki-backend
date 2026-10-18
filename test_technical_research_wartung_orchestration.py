"""
RC-W5-Fix (Wartung): kein "erster Treffer gewinnt"-Early-Exit mehr zwischen
den beiden Wartungs-Queries.

BEFUND (RC-W5-Design-Audit, reines Code-Audit ohne Live-Calls)
----------------------------------------------------------------
`TavilyTechnicalResearchProvider.recherchiere()` fragte Wartung bisher über
`tavily_search_with_fallback([Q1, Q2], ...)` an. Dieser Pfad prüfte NUR, ob Q1
ROH nichtleer war -- VOR jeder Curation/Extraktion. Lieferte Q1 ausschliesslich
Treffer, die `_extrahiere_fakten()` oder der Wartungs-Evidence-Layer (Commit
55873be) komplett verwarfen (z.B. "source_quality", "no_maintenance_interval"),
wurde Q2 TROTZDEM NIE ausgeführt. Damit konnte ausgerechnet der gerade
committete Evidence-Layer wirkungslos werden: eine zweite, unabhängige
Source-Family -- notwendig für CORROBORATED, für die Cross-Tier-Anreicherung
zu VERIFIED, oder für die Erkennung eines CONFLICT -- bekam nie die Chance zu
laufen.

ENTSCHEIDUNG (kein Coverage-Score, kein Early-Exit; Qualität vor einem
gesparten Tavily-Call): Wartung führt jetzt IMMER beide Queries aus, parallel
via `asyncio.gather`, analog zu den beiden Schwachstelle-Intents (siehe
test_technical_research_schwachstelle_intents.py). Die Rohtreffer werden VOR
der unveränderten Extraktions-/Evidence-Pipeline vereint -- `curate_results`
dedupliziert exakte URL-/Domain+Titel-Duplikate, `_extrahiere_fakten()` und
`_wartung_candidate_fakten()` laufen danach EINMAL, unverändert, auf der
vereinten Liste.

    python test_technical_research_wartung_orchestration.py
"""
import asyncio

import app.technical_research as technical_research
import app.web_search as web_search
from app.technical_research import TavilyTechnicalResearchProvider

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def _treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


IDENTITAET_TREFFER = [
    _treffer("https://www.adac.de/vw-golf-7", "VW Golf 7 (2013–2020)",
             "Der VW Golf 7 (seit 2013): 2.0 TDI mit 150 PS, Diesel, Frontantrieb."),
    _treffer("https://www.auto-motor-und-sport.de/vw-golf-7", "VW Golf 7 im Test",
             "VW Golf 7 (seit 2013) mit 2.0 TDI und 150 PS, Frontantrieb."),
]

# PRIMARY (Hersteller, score >= MIN_SCORE_FAKT) -- läuft über den bestehenden
# Hauptpfad, unverändert.
VW_ZAHNRIEMEN_STARK = _treffer(
    "https://www.volkswagen.at/inspektion-golf", "VW Golf Inspektion",
    "Der Zahnriemen wird beim 2.0 TDI alle 210.000 km gewechselt.")
# Fachmedien (score >= MIN_SCORE_FAKT) -- ebenfalls Hauptpfad, anderes Bauteil.
ADAC_KUEHLUNG_STARK = _treffer(
    "https://www.adac.de/vw-golf-7-wasserpumpe", "VW Golf 7 Wartung",
    "Die Wasserpumpe wird beim 2.0 TDI alle 90.000 km ersetzt.")
# SECONDARY (unterhalb MIN_SCORE_FAKT) -- nur über den Candidate-Evidence-Pfad
# erreichbar, braucht eine zweite, unabhängige Family zur Bestätigung.
REPAREO_ZAHNRIEMEN_SCHWACH = _treffer(
    "https://www.repareo.de/inspektion-vw-golf-7", "VW Golf 7 Inspektion",
    "Der Zahnriemen wird beim 2.0 TDI alle 210.000 km gewechselt.")
AUTOTEILE_ZAHNRIEMEN_SCHWACH_GLEICH = _treffer(
    "https://www.autoteile-xyz.de/golf-zahnriemen", "VW Golf 7 Wartung",
    "Der Zahnriemen wird beim 2.0 TDI alle 210.000 km gewechselt.")
AUTOTEILE_ZAHNRIEMEN_SCHWACH_KONFLIKT = _treffer(
    "https://www.autoteile-xyz.de/golf-zahnriemen-2", "VW Golf 7 Wartung",
    "Der Zahnriemen wird beim 2.0 TDI alle 150.000 km gewechselt.")
REPAREO_BREMSE_FEST = _treffer(
    "https://www.repareo.de/golf-bremse", "VW Golf 7 Wartung",
    "Die Bremsfluessigkeit wird beim 2.0 TDI alle 2 Jahre gewechselt.")
AUTOTEILE_BREMSE_LONGLIFE = _treffer(
    "https://www.autoteile-xyz.de/golf-bremse", "VW Golf 7 Wartung",
    "Die Bremsfluessigkeit wird beim 2.0 TDI im LongLife-Intervall alle 4 Jahre gewechselt.")
# Raw nichtleer, aber ohne jedes Wartungsintervall -> 0 akzeptierte Facts UND
# 0 Candidate-Kandidaten (reiner Fülltext). Historischer RC-W5-Bug: der alte
# Fallback hätte Q2 wegen dieses NICHTLEEREN Rohtreffers nie ausgeführt.
VOLKSWAGEN_OHNE_INTERVALL = _treffer(
    "https://www.volkswagen.at/golf-uebersicht", "VW Golf Übersicht",
    "Der VW Golf 7 ist ein beliebter Kompaktwagen mit viel Platz im Innenraum.")


class FakeTavily:
    """Routet Query-Strings auf vordefinierte Treffer; protokolliert jeden
    Aufruf. Ungedeckte Queries liefern [] (reiner Offline-Test)."""

    def __init__(self, routing: dict[str, list[dict]], *, identitaet_treffer=None):
        self.routing = routing
        self.identitaet_treffer = identitaet_treffer if identitaet_treffer is not None else IDENTITAET_TREFFER
        self.calls: list[str] = []

    async def __call__(self, query, count=5, include_domains=None, exclude_domains=None,
                       include_raw_content=False, bypass_cache=False, search_depth="basic"):
        self.calls.append(query)
        if "technische Daten" in query or "Generation Baureihe Bauzeitraum" in query:
            return list(self.identitaet_treffer)
        if "Rückruf" in query:
            return []
        if "Schwachstellen" in query or "Gebrauchtwagen" in query:
            return []
        for substr, treffer in self.routing.items():
            if substr in query:
                return list(treffer)
        return []


def _patch(fake: FakeTavily):
    web_search.tavily_search = fake
    technical_research.tavily_search = fake


async def _recherchiere(routing, *, marke="VW", modell="Golf", baujahr=2018,
                        motor="2.0 TDI 150 PS"):
    fake = FakeTavily(routing)
    _patch(fake)
    provider = TavilyTechnicalResearchProvider()
    recherche = await provider.recherchiere(marke=marke, modell=modell, baujahr=baujahr,
                                            motor=motor, ausgeloest_durch="test")
    return recherche, fake.calls


def _wa_calls(calls):
    return [c for c in calls if "Wartungsintervall" in c or "Inspektionsintervall" in c]


def _wa_fakten(recherche):
    return sorted((f.bauteil, f.aussage) for f in recherche.fakten if f.kategorie == "wartung")


def _gruende(recherche):
    return {a.get("grund") for a in recherche.abgelehnte_fakten}


# ── Pflicht-Regressionstest: historischer RC-W5-Bug (§10/§11 Auftrag) ──────
# Q1 liefert einen ROH-nichtleeren Treffer, der nach Curation/Extraktion KEINEN
# verwendbaren Wartungsfakt ergibt (kein Intervall im Text). Q2 enthält einen
# validen Wartungsfakt. ALT: Q2 wurde wegen des nichtleeren Q1-Rohtreffers nie
# ausgeführt. NEU: Q2 läuft immer, der valide Fakt erreicht die Pipeline.
async def test_historischer_bug_q1_raw_nichtleer_ohne_fakt():
    recherche, calls = await _recherchiere({
        "Wartungsintervall Serviceintervall": [VOLKSWAGEN_OHNE_INTERVALL],
        "Inspektionsintervall": [VW_ZAHNRIEMEN_STARK],
    })
    wa_calls = _wa_calls(calls)
    check("historischer Bug: Q1 (ohne Intervall) wurde ausgeführt",
          any("Wartungsintervall" in c for c in wa_calls))
    check("historischer Bug: Q2 WURDE ausgeführt, OBWOHL Q1 roh nichtleer war",
          any("Inspektionsintervall" in c for c in wa_calls), str(wa_calls))
    check("historischer Bug: Q1 selbst ergab 0 Wartungsfakten (Kontrolle, kein Scheinbeweis)",
          "no_maintenance_interval" in _gruende(recherche) or "no_component" in _gruende(recherche))
    check("historischer Bug: Q2s validen Fakt (Zahnriemen) erreicht trotzdem die Fakten",
          any(b == "Zahnriemen" for b, _ in _wa_fakten(recherche)), str(_wa_fakten(recherche)))


# ── A) identisch zum historischen Bug-Test, als expliziter Matrix-Fall ────
async def test_a_q1_raw_nichtleer_0_akzeptiert():
    recherche, calls = await _recherchiere({
        "Wartungsintervall Serviceintervall": [VOLKSWAGEN_OHNE_INTERVALL],
        "Inspektionsintervall": [VW_ZAHNRIEMEN_STARK],
    })
    check("A) Q2 lief trotz Q1-Rohtreffer, Fakt kommt durch",
          any(b == "Zahnriemen" for b, _ in _wa_fakten(recherche)))


# ── B/E) beide Queries valide, unterschiedliches Bauteil -> beide Facts ───
async def test_b_unterschiedliche_bauteile():
    recherche, calls = await _recherchiere({
        "Wartungsintervall Serviceintervall": [VW_ZAHNRIEMEN_STARK],
        "Inspektionsintervall": [ADAC_KUEHLUNG_STARK],
    })
    check("B) genau 2 Wartungs-Queries ausgeführt", len(_wa_calls(calls)) == 2)
    bauteile = {b for b, _ in _wa_fakten(recherche)}
    check("B) beide Bauteile (Zahnriemen + Wasserpumpe) erhalten",
          bauteile == {"Zahnriemen", "Wasserpumpe"}, str(bauteile))


# ── C) Q1 und Q2 liefern denselben Treffer -> keine doppelte Aussage ──────
async def test_c_dedupe_identischer_treffer():
    recherche, _ = await _recherchiere({
        "Wartungsintervall Serviceintervall": [VW_ZAHNRIEMEN_STARK],
        "Inspektionsintervall": [VW_ZAHNRIEMEN_STARK],
    })
    fakten = _wa_fakten(recherche)
    check("C) identischer Treffer aus beiden Queries -> genau 1 Fakt, keine Verdopplung",
          len(fakten) == 1 and fakten[0][0] == "Zahnriemen", str(fakten))


# ── D) Q1 nur schwache Candidate-Evidence, Q2 liefert ergänzende Family ───
# (beweist: beide Ergebnisse erreichen tatsächlich dieselbe Evidence-Pipeline)
async def test_d_candidate_evidence_aus_beiden_queries():
    recherche, _ = await _recherchiere({
        "Wartungsintervall Serviceintervall": [REPAREO_ZAHNRIEMEN_SCHWACH],
        "Inspektionsintervall": [AUTOTEILE_ZAHNRIEMEN_SCHWACH_GLEICH],
    })
    check("D) zwei unabhängige Secondary-Families aus Q1+Q2 -> AGREEMENT (CORROBORATED, "
          "weiterhin NICHT user-visible -- Evidence-Layer unverändert)",
          "wartung_corroborated_not_verified" in _gruende(recherche))
    check("D) kein künstlich erzeugter harter Wartungsfakt aus bloßem Secondary-Agreement",
          not any(b == "Zahnriemen" for b, _ in _wa_fakten(recherche)))


# ── F) unterschiedliche Service-Regime (fest vs. LongLife) -> kein Conflict ─
async def test_f_regime_unterschiedlich_kein_conflict():
    recherche, _ = await _recherchiere({
        "Wartungsintervall Serviceintervall": [REPAREO_BREMSE_FEST],
        "Inspektionsintervall": [AUTOTEILE_BREMSE_LONGLIFE],
    })
    check("F) fest vs. LongLife aus Q1/Q2 wird NICHT als Conflict gewertet",
          "wartung_candidate_conflict" not in _gruende(recherche))


# ── G) widersprüchliche Intervalle aus Q1/Q2 -> Conflict wird erkannt ─────
# (nur möglich, WEIL Q2 tatsächlich lief -- Kernbeweis gegen den alten Bug)
async def test_g_widerspruch_zwischen_q1_q2_wird_als_conflict_erkannt():
    recherche, _ = await _recherchiere({
        "Wartungsintervall Serviceintervall": [REPAREO_ZAHNRIEMEN_SCHWACH],
        "Inspektionsintervall": [AUTOTEILE_ZAHNRIEMEN_SCHWACH_KONFLIKT],
    })
    check("G) widersprechende Intervalle aus Q1 (210.000 km) und Q2 (150.000 km) -> CONFLICT",
          "wartung_candidate_conflict" in _gruende(recherche))
    check("G) kein bestätigter Wartungsfakt bei Conflict",
          not any(b == "Zahnriemen" for b, _ in _wa_fakten(recherche)))


# ── H/I/J) leere Kombinationen -> funktioniert, kein Crash ────────────────
async def test_h_q1_leer_q2_valid():
    recherche, calls = await _recherchiere({
        "Wartungsintervall Serviceintervall": [],
        "Inspektionsintervall": [VW_ZAHNRIEMEN_STARK],
    })
    check("H) Q1 leer, Q2 valide -> Fakt kommt trotzdem durch",
          any(b == "Zahnriemen" for b, _ in _wa_fakten(recherche)))
    check("H) beide Queries wurden ausgeführt", len(_wa_calls(calls)) == 2)


async def test_i_q1_valid_q2_leer():
    recherche, calls = await _recherchiere({
        "Wartungsintervall Serviceintervall": [VW_ZAHNRIEMEN_STARK],
        "Inspektionsintervall": [],
    })
    check("I) Q1 valide, Q2 leer -> Fakt kommt durch",
          any(b == "Zahnriemen" for b, _ in _wa_fakten(recherche)))
    check("I) beide Queries wurden ausgeführt", len(_wa_calls(calls)) == 2)


async def test_j_beide_leer():
    recherche, calls = await _recherchiere({
        "Wartungsintervall Serviceintervall": [],
        "Inspektionsintervall": [],
    })
    check("J) beide Wartungs-Queries leer -> sauberes leeres Ergebnis, kein Crash",
          _wa_fakten(recherche) == [])
    check("J) beide Queries wurden trotzdem ausgeführt", len(_wa_calls(calls)) == 2)


# ── K/L/M) Identity/Recall/Schwachstelle bleiben unverändert ──────────────
async def test_klm_andere_phasen_unveraendert():
    recherche, calls = await _recherchiere({
        "Wartungsintervall Serviceintervall": [VW_ZAHNRIEMEN_STARK],
        "Inspektionsintervall": [],
    })
    ident_calls = [c for c in calls if "technische Daten" in c or "Generation Baureihe Bauzeitraum" in c]
    rueckruf_calls = [c for c in calls if "Rückruf" in c]
    sw_calls = [c for c in calls if "Schwachstellen" in c or "Gebrauchtwagen" in c]
    check("L) Identität Runde 1 weiterhin exakt 2 Queries", len(ident_calls) == 2, str(ident_calls))
    check("M) Rückruf weiterhin exakt 2 Queries", len(rueckruf_calls) == 2, str(rueckruf_calls))
    check("K) Schwachstelle weiterhin exakt 2 Queries", len(sw_calls) == 2, str(sw_calls))
    # Adaptive-Evidence-Research (Folgeauftrag §C): die IDENTITAET_TREFFER-
    # Fixture dieser Datei belegt Kraftstoff/Antrieb ("Diesel"/"Frontantrieb"),
    # aber WEDER Generation NOCH Getriebe/Powertrain -- das löst GENAU EINE
    # zusätzliche, gebündelte Identity-Spec-Gap-Runde aus (2 weitere Queries,
    # unabhängig von der Anzahl offener Felder). §7 Call-Modell gilt
    # inhaltlich unverändert (2+2+2+2=8 für Identität-Runde-1/Rückruf/
    # Schwachstelle/Wartung), PLUS diese neue, bewusste Zusatzrunde = 10.
    check("§7+§C Call-Modell: 8 (Identität-Runde-1 + Rückruf + Schwachstelle + "
          "Wartung) + 2 (Identity-Spec-Gap-Runde: Generation/Getriebe/Powertrain "
          "offen) = 10", len(calls) == 10, str(calls))
    check("phasen_status.identitaet bleibt SUCCESS", recherche.phasen_status.get("identitaet") == "success")


async def _run_all():
    await test_historischer_bug_q1_raw_nichtleer_ohne_fakt()
    await test_a_q1_raw_nichtleer_0_akzeptiert()
    await test_b_unterschiedliche_bauteile()
    await test_c_dedupe_identischer_treffer()
    await test_d_candidate_evidence_aus_beiden_queries()
    await test_f_regime_unterschiedlich_kein_conflict()
    await test_g_widerspruch_zwischen_q1_q2_wird_als_conflict_erkannt()
    await test_h_q1_leer_q2_valid()
    await test_i_q1_valid_q2_leer()
    await test_j_beide_leer()
    await test_klm_andere_phasen_unveraendert()


asyncio.run(_run_all())

print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE WARTUNGS-ORCHESTRATION-TESTS GRUEN")
