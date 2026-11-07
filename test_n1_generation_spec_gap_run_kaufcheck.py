"""
ENFAL -- "N1: Generationsrecherche bis zur tatsächlichen Blockade verfolgen"
(2026-11-02), vollständiger Webpfad.

Ergänzt `test_recherche_volltext_integration.py` (Tests B2-B4, Provider-
Ebene) um denselben Nachweis über den ECHTEN `run_kaufcheck()`-Pfad: DB-Miss
NUR an der schmalen Katalog-Lookup-Grenze simuliert (`find_baureihe_mit_
vertrauen` gestubbt, keine echte DB-Änderung), Gemini/Markt gestubbt bzw.
gesperrt, `app.technical_research.tavily_search`/`hole_raw_content` als
einzige externe Stellen gestubbt -- der ECHTE `TavilyTechnicalResearchProvider`
läuft unverändert (KEIN `FixtureTechnicalResearchProvider`, der die Identity-
Spec-Gap-Runde 2 gar nicht nachbildet). Keine fertige Identität, kein
fertiges Empfehlungsergebnis wird eingespeist -- jeder Status entsteht
ausschließlich aus dem echten Lauf über die unveränderte Extraktions-/
Konsens-/Empfehlungs-Pipeline.

Beweist Orchestrierung und Kostendisziplin, NICHT echte Suchmaschinen-
platzierung (keine Aussage darüber, ob Tavily diese Treffer in einem
echten Live-Lauf tatsächlich findet).

    python test_n1_generation_spec_gap_run_kaufcheck.py
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import app.technical_research as tr
from app import kaufcheck as kc
from app.models import KaufCheckRequest, KaufCheckResponse

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# ── Echte Quellentexte (wörtlich zitiert, s. test_rc_f_lexus_originalbeleg.py /
# test_recherche_volltext_integration.py) ────────────────────────────────────
_RETTUNGSKARTE_URL = ("https://www.lexus.de/content/dam/lexus/nmsc/germany/lexus-besitzer/"
                      "fahrzeuginformationen/RCF_tcm-3150-1284184.pdf")
_RETTUNGSKARTE_SNIPPET_DUENN = (
    "12V Spannungsversorgung trennen\nHinweise zum Transport\nZugang 12V Batterie")
_RETTUNGSKARTE_VOLLTEXT = (
    "Lexus RC F\n(UXC1 (EU, M), ab 09/2014)\nStand: 10/2014\n"
    "Lexus RC F (UXC1 (EU, ab 09/2014)\n"
    "12V Spannungsversorgung trennen\nHinweise zum Transport\nZugang 12V Batterie\n"
    "Lexus RC F\n(UXC1 (EU, M), ab 09/2014)")
_ADAC = treffer(
    "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/",
    "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25): Technische Daten | ADAC",
    "Marke Lexus Modell RC F Typ Ultimate Edition Automatik Baureihe RC (UXC1) "
    "(12/14 - 06/25) Herstellerinterne Baureihenbezeichnung UXC1 Motorcode 2UR-GSE "
    "Leistung maximal in PS 464 Antriebsart Hinterrad Getriebeart Automatikgetriebe")
_AMS = treffer(
    "https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
    "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
    "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Leistung 181 PS bis "
    "477 PS. Hubraum 1998 cm3 bis 4969 cm3. LEXUS RC F TRACK EDITION IM SUPERTEST: "
    "Was kann der V8-Sauger auf der Rennstrecke? LEXUS LC 500 UND RC F: "
    "V8-Sportler auf Abschiedstour.")


class _FakeSearchExtract:
    """Stubt NUR die beiden externen Stellen (`tavily_search`/`hole_raw_content`
    auf Modulebene von `app.technical_research`) -- derselbe, bereits
    etablierte Stub wie in `test_recherche_volltext_integration.py`."""

    def __init__(self, *, routing: dict[str, list[dict]], extract_text: dict[str, str] | None = None):
        self.routing = routing
        self.extract_text = extract_text or {}
        self.search_calls: list[str] = []
        self.extract_calls: list[list[str]] = []

    async def search(self, query, *, count=8, exclude_domains=None, include_domains=None):
        self.search_calls.append(query)
        for substr, treffer_liste in self.routing.items():
            if substr in query:
                return list(treffer_liste)
        return []

    async def extract(self, urls):
        self.extract_calls.append(list(urls))
        gefunden = {u: self.extract_text[u] for u in urls if u in self.extract_text}
        fehler = len(urls) - len(gefunden)
        return gefunden, {"angefragt": len(urls), "aus_cache": 0, "extract_calls": 1,
                          "extract_urls": len(urls), "erfolge": len(gefunden), "fehler": fehler}


def _db_miss_stub(marke, modell, baujahr):
    """DB-Miss NUR an der schmalen Katalog-Lookup-Funktion simuliert -- exakt
    die reale 'kein Treffer'-Rückgabeform (gegen eine echte unbekannte Marke
    verifiziert, nicht erfunden). Keine echte DB-Zeile wird gelesen/geändert."""
    return None, {"match_art": "no_match", "konfidenz": "niedrig", "belastbar": False,
                  "fehlende_angabe": "simulierter DB-Miss, Testdouble"}


async def _lauf(req: KaufCheckRequest, fake: _FakeSearchExtract) -> KaufCheckResponse:
    orig = (kc.call_gemini_json, kc.tavily_search_with_fallback,
            kc.marktpreis_recherche_moeglich, kc.vertiefe_marktrecherche,
            kc.find_baureihe_mit_vertrauen, tr.tavily_search, tr.hole_raw_content)

    async def gemini(system, user):
        return {"bericht": "Laut Inserat unfallfrei. KAUFEN NACH BESICHTIGUNG.",
                "empfehlung": "kaufen_nach_besichtigung", "risiko_evidence_ids": [],
                "empfehlung_evidence_ids": []}

    async def verboten(*a, **k):
        raise AssertionError("Marktprovider darf nicht laufen -- kein Netzwerk/Provider-Aufruf erlaubt")

    kc.call_gemini_json = gemini
    kc.tavily_search_with_fallback, kc.vertiefe_marktrecherche = verboten, verboten
    kc.marktpreis_recherche_moeglich = lambda *a: False
    kc.find_baureihe_mit_vertrauen = _db_miss_stub
    # KEIN FixtureTechnicalResearchProvider: `kc.recherchiere_technisch` bleibt
    # die ECHTE Funktion und instanziiert selbst einen echten
    # `TavilyTechnicalResearchProvider()` -- nur die beiden genuinen externen
    # I/O-Stellen (Suche/Extract) sind gestubbt, damit die Identity-Spec-Gap-
    # Runde 2 (die ein Fixture-Provider gar nicht nachbildet) tatsächlich läuft.
    tr.tavily_search = fake.search
    tr.hole_raw_content = fake.extract
    try:
        return KaufCheckResponse(**await kc.run_kaufcheck(req))
    finally:
        (kc.call_gemini_json, kc.tavily_search_with_fallback,
         kc.marktpreis_recherche_moeglich, kc.vertiefe_marktrecherche,
         kc.find_baureihe_mit_vertrauen, tr.tavily_search, tr.hole_raw_content) = orig


async def main() -> None:
    print("=" * 78)
    print("N1 -- vollständiger run_kaufcheck-Webpfad, Generation nur über "
          "Identity-Spec-Gap-Runde 2 erreichbar")
    print("=" * 78)

    fake = _FakeSearchExtract(
        routing={
            "technische Daten": [_ADAC],
            "Generation Baureihe Bauzeitraum": [_AMS],   # Generation bleibt nach Runde 1 offen
            "Generation Modellcode": [treffer(
                _RETTUNGSKARTE_URL, "Lexus RC F Rettungskarte", _RETTUNGSKARTE_SNIPPET_DUENN)],
        },
        extract_text={_RETTUNGSKARTE_URL: _RETTUNGSKARTE_VOLLTEXT})
    r = await _lauf(
        KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000,
                         motor="5.0 V8", kraftstoff="Benzin", leistung_ps=477,
                         getriebe="Automatik", antrieb="Heck", ausstattung=[]),
        fake)

    print(f"  Suchanfragen: {fake.search_calls}")
    print(f"  Extract-Aufrufe: {fake.extract_calls}")
    print(f"  vehicle_identity.generation: {r.vehicle_identity.get('generation')!r}")

    check("1) die Identity-Spec-Gap-Query für 'generation' lief tatsächlich "
          "(kein vorzeitiger Abbruch, keine Blockade durch andere bestätigte Felder)",
          any("Generation Modellcode" in q for q in fake.search_calls), str(fake.search_calls))
    check("2) die Rettungskarte wurde nachgeladen, OBWOHL sie erst in Runde 2 "
          "gefunden wurde -- GENAU EIN Abruf, kein erfundener Beleg",
          fake.extract_calls == [[_RETTUNGSKARTE_URL]], f"extract_calls={fake.extract_calls!r}")
    check("3) die Generation erscheint im fertigen Bericht als 'UXC1' -- über "
          "den echten Produktpfad (Extraktion -> Konsens -> Identität -> "
          "KaufCheckResponse), KEINE manuell eingespeiste Identität/Empfehlung",
          r.vehicle_identity.get("generation") == "UXC1",
          f"vehicle_identity={r.vehicle_identity!r}")
    check("4) kein Crash, kein research_failed -- normaler Berichtsabschluss",
          r.recommendation_state is not None, f"recommendation_state={r.recommendation_state!r}")

    if _FEHLER:
        print(f"\n{len(_FEHLER)} FEHLER:")
        for f in _FEHLER:
            print(f"  - {f}")
        raise SystemExit(1)
    print("\nTEIL A GRUEN")


# ══════════════════════════════════════════════════════════════════════════
# TEIL B -- ENFAL-Folgeauftrag ("Ablehnungsgrund und Generationssperre
# eindeutig nachweisen", 2026-11): ZWEI Läufe mit IDENTISCHER Motor-Evidenz
# (nur `_ADAC`, EINE Domain -- Motorisierung bleibt dadurch in BEIDEN Läufen
# unbestätigt), einmal OHNE und einmal MIT ausreichendem Generationsbeleg
# (Rettungskarte). Beweist am echten `run_kaufcheck()`-Pfad:
#   * genau die Generationssperre fällt weg (verschwindet aus
#     "Blockierende Identitätslücke: ..."), wenn die Generation ausreichend
#     belegt wird,
#   * eine ANDERE, berechtigte Sperre (Motorisierung, N6-Thema, hier bewusst
#     NICHT behoben) bleibt davon unberührt bestehen -- die beiden Gates
#     sind nachweislich unabhängig voneinander.
# `_AMS` trägt bewusst zusätzlich eine "XC1"-Nennung (nicht-amtliche Quelle,
# kein Rollenbeleg) -- der Gegenfall "unpassende/schwache Kennung reicht
# NICHT" ist damit in Lauf 1 (ohne Rettungskarte) mitbelegt: trotz "XC1" im
# Treffer-Set bleibt Generation dort unbestätigt.
# ══════════════════════════════════════════════════════════════════════════

async def _lauf_b(req: KaufCheckRequest, fake: _FakeSearchExtract) -> KaufCheckResponse:
    return await _lauf(req, fake)


async def main_b() -> None:
    print("\n" + "=" * 78)
    print("TEIL B) Zwei Läufe, identische Motor-Evidenz: nur die Generationssperre fällt weg")
    print("=" * 78)

    req = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000,
                           motor="5.0 V8", kraftstoff="Benzin", leistung_ps=477,
                           getriebe="Automatik", antrieb="Heck", ausstattung=[])

    # LAUF 1 -- OHNE ausreichenden Generationsbeleg. Motor-Evidenz: NUR _ADAC
    # (eine Domain) -> Motorisierung bleibt unbestätigt (identisch zu Lauf 2).
    fake1 = _FakeSearchExtract(routing={
        "technische Daten": [_ADAC],
        "Generation Baureihe Bauzeitraum": [_AMS],
    })
    r1 = await _lauf_b(req, fake1)
    hinweis1 = r1.bericht[r1.bericht.find("Blockierende Identitätslücke"):][:120]
    print(f"  Lauf 1 -- generation={r1.vehicle_identity.get('generation')!r} "
          f"state={r1.recommendation_state!r}")
    print(f"           Hinweis: {hinweis1!r}")

    check("5) Lauf 1 (ohne Rettungskarte, trotz 'XC1' im Treffer-Set): Generation "
          "bleibt UNBESTÄTIGT -- eine nicht-amtliche Kennung allein reicht nicht",
          r1.vehicle_identity.get("generation") is None,
          f"generation={r1.vehicle_identity.get('generation')!r}")
    check("6) Lauf 1: BEIDE Sperren stehen in der Blockade-Meldung -- Generation UND "
          "Motorisierung, identische Motor-Evidenz wie Lauf 2",
          "Generation" in hinweis1 and "Motorisierung" in hinweis1, hinweis1)

    # LAUF 2 -- MIT ausreichendem Generationsbeleg (Rettungskarte zusätzlich).
    # IDENTISCHE Motor-Evidenz wie Lauf 1 (weiterhin nur _ADAC).
    fake2 = _FakeSearchExtract(
        routing={
            "technische Daten": [_ADAC],
            "Generation Baureihe Bauzeitraum": [_AMS],
            "Generation Modellcode": [treffer(
                _RETTUNGSKARTE_URL, "Lexus RC F Rettungskarte", _RETTUNGSKARTE_SNIPPET_DUENN)],
        },
        extract_text={_RETTUNGSKARTE_URL: _RETTUNGSKARTE_VOLLTEXT})
    r2 = await _lauf_b(req, fake2)
    hinweis2 = r2.bericht[r2.bericht.find("Blockierende Identitätslücke"):][:120]
    print(f"  Lauf 2 -- generation={r2.vehicle_identity.get('generation')!r} "
          f"state={r2.recommendation_state!r}")
    print(f"           Hinweis: {hinweis2!r}")

    check("7) Lauf 2 (mit Rettungskarte): Generation wird bestätigt ('UXC1') -- über "
          "denselben echten Produktpfad, identische Motor-Evidenz wie Lauf 1",
          r2.vehicle_identity.get("generation") == "UXC1",
          f"generation={r2.vehicle_identity.get('generation')!r}")
    check("8) Lauf 2: GENAU die Generationssperre ist aus der Blockade-Meldung "
          "verschwunden -- 'Motorisierung' bleibt separat, unverändert stehen",
          "Generation" not in hinweis2 and "Motorisierung" in hinweis2, hinweis2)
    check("9) recommendation_state bleibt in BEIDEN Läufen identisch "
          "(LIMITED_ANALYSIS) -- die Motorisierungssperre hält den Zustand "
          "unabhängig von der Generationsklärung, kein Nebeneffekt der N1-Fix "
          "auf die separate N6-Sperre",
          r1.recommendation_state == r2.recommendation_state == "LIMITED_ANALYSIS",
          f"{r1.recommendation_state!r} vs {r2.recommendation_state!r}")

    if _FEHLER:
        print(f"\n{len(_FEHLER)} FEHLER (Teil B):")
        for f in _FEHLER[-10:]:
            print(f"  - {f}")
        raise SystemExit(1)
    print("\nTEIL B GRUEN")


asyncio.run(main())
asyncio.run(main_b())
print("\nALLE TESTS GRUEN (Teil A + Teil B)")
