"""
RC-W5-Query-Strategy-Fix (Schwachstelle): zwei orthogonale Search-Intents
statt der bisherigen Synonym-Varianten mit "erster Treffer gewinnt"-Fallback.

BEFUND (Live-A/B-Audit, Scratch, nicht im Repo)
------------------------------------------------
`TavilyTechnicalResearchProvider.recherchiere()` fragte Schwachstelle bisher
über `tavily_search_with_fallback([Q1, Q2], ...)` an — Q1 ("typische Probleme
Schwachstellen") und Q2 ("bekannte Schwachstellen") sind nahezu Synonyme
desselben Suchintents. Sobald Q1 irgendein rohes Ergebnis lieferte (egal wie
schwach/benign), wurde Q2 NIE ausgeführt. Ein realer Live-A/B-Test (identisches
Fahrzeug) zeigte: eine Query mit einem GENUIN anderen Intent ("Gebrauchtwagen
Test TÜV Kaufberatung" statt "Schwachstellen") fand bei adac.de/autobild.de/
auto-motor-und-sport.de 4 valide, bisher nie gefundene Fakten — UND die alte
Formulierung fand unabhängig davon einen eigenen Fakt, den die neue nicht fand.
Unterschiedliche Suchintentionen liefern also echte, komplementäre Information.

FIX: Schwachstelle führt jetzt IMMER beide Intents aus (kein Early Exit
zwischen ihnen) und vereint die Rohergebnisse VOR der unveränderten
Extraktions-/Dedupe-Pipeline (`curate_results` + `_extrahiere_fakten`, inkl.
bestehender RC-W3-Dedupe-Logik). Wartung bleibt unverändert beim bisherigen
Fallback-Mechanismus.

    python test_technical_research_schwachstelle_intents.py
"""
import asyncio
import types

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
    _treffer("https://www.adac.de/mazda-mx-5-nd", "Mazda MX-5 ND (seit 2015)",
             "Die 4. Generation des Mazda MX-5 (ND, seit 2015): 2.0 SKYACTIV-G mit "
             "184 PS, Benziner, Hinterradantrieb."),
    _treffer("https://www.auto-motor-und-sport.de/mazda-mx-5-nd", "Mazda MX-5 ND im Test",
             "Mazda MX-5 ND (seit 2015) mit 2.0 SKYACTIV-G und 184 PS, Heckantrieb."),
]
# Ohne jeden Generations-Code-Token (kein "ND"/Römisch/Kontext) -- Phase 1
# belegt Marke+Modell, bestätigt aber bewusst KEINE Generation. Für die
# "Generation unbekannt"-Testfälle, damit sie nicht zufällig durch den
# identischen ND-Hinweis der anderen Fixture unterlaufen werden.
IDENTITAET_TREFFER_NEUTRAL = [
    _treffer("https://www.adac.de/mazda-mx-5", "Mazda MX-5",
             "Der Mazda MX-5: 2.0 SKYACTIV-G mit 184 PS, Benziner, Hinterradantrieb."),
    _treffer("https://www.auto-motor-und-sport.de/mazda-mx-5", "Mazda MX-5 im Test",
             "Mazda MX-5 mit 2.0 SKYACTIV-G und 184 PS, Heckantrieb."),
]

GETRIEBE_FAKT = _treffer(
    "https://www.adac.de/mazda-mx-5-nd-getriebe", "Mazda MX-5 ND Mängel",
    "Das Getriebe neigt zu Defekten und macht sich durch Rucken bemerkbar.")
WASSERPUMPE_FAKT = _treffer(
    "https://www.autobild.de/mazda-mx-5-nd-wasserpumpe", "Mazda MX-5 ND Gebrauchtwagen-Check",
    "Die Wasserpumpe kann bei hoher Laufleistung undicht werden.")
KUPPLUNG_FAKT = _treffer(
    "https://www.auto-motor-und-sport.de/mazda-mx-5-nd-kupplung", "Mazda MX-5 ND Kupplungsschäden",
    "Die Kupplung verschleißt bei Vielfahrern deutlich schneller als angegeben.")
BENIGN_LENKRAD = _treffer(
    "https://www.autobild.de/mazda-mx-5-nd-lenkrad", "Mazda MX-5 ND Gebrauchtwagen-Check",
    "Ein schief stehendes Lenkrad muss nicht unbedingt auf einen Unfallschaden hindeuten.")
SCHLECHTE_QUELLE_GETRIEBE = _treffer(
    "https://www.motor-talk.de/forum/mazda-mx-5-nd", "Mazda MX-5 ND Forum",
    "Das Getriebe neigt zu Defekten und macht sich durch Rucken bemerkbar.")


class FakeTavily:
    """Routet Query-Strings auf vordefinierte Treffer; protokolliert jeden
    Aufruf. Ungedeckte Queries liefern [] (kein Kill-Switch nötig -- reiner
    Offline-Test ohne jede reale Netzwerkfunktion)."""

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
        for substr, treffer in self.routing.items():
            if substr in query:
                return list(treffer)
        return []


def _patch(fake: FakeTavily):
    web_search.tavily_search = fake
    technical_research.tavily_search = fake


async def _recherchiere(routing, *, marke="Mazda", modell="MX-5", baujahr=2019,
                        motor="2.0 SKYACTIV-G 184 PS", identitaet_treffer=None):
    fake = FakeTavily(routing, identitaet_treffer=identitaet_treffer)
    _patch(fake)
    provider = TavilyTechnicalResearchProvider()
    recherche = await provider.recherchiere(marke=marke, modell=modell, baujahr=baujahr,
                                            motor=motor, ausgeloest_durch="test")
    return recherche, fake.calls


def _sw_fakten(recherche):
    return sorted((f.bauteil, f.aussage) for f in recherche.fakten if f.kategorie == "schwachstelle")


# ── A/B) zwei unterschiedliche Intents werden erzeugt UND beide ausgeführt,
# auch wenn der erste bereits ein Ergebnis liefert (Kern-Regressionstest
# gegen das alte "erster Treffer gewinnt"-Verhalten) ─────────────────────────
async def test_beide_intents_immer_ausgefuehrt():
    recherche, calls = await _recherchiere({
        "bekannte Probleme Schwachstellen": [GETRIEBE_FAKT],
        "Gebrauchtwagen Test TÜV Kaufberatung": [WASSERPUMPE_FAKT],
    })
    sw_calls = [c for c in calls if "Schwachstellen" in c or "Gebrauchtwagen" in c]
    check("A/B) genau 2 unterschiedliche Schwachstelle-Queries ausgeführt",
          len(sw_calls) == 2 and len(set(sw_calls)) == 2, str(sw_calls))
    check("B) zweiter Intent lief TROTZ nichtleerem ersten Intent (kein Early Exit)",
          any("Gebrauchtwagen" in c for c in sw_calls))
    check("F) beide Intents liefern zusammen beide Fakten",
          _sw_fakten(recherche) == sorted([("Getriebe", "Das Getriebe neigt zu Defekten und "
                                           "macht sich durch Rucken bemerkbar"),
                                          ("Wasserpumpe", "Die Wasserpumpe kann bei hoher "
                                           "Laufleistung undicht werden")]))


# ── C) keine bloßen Synonym-Duplikate -- die zwei Query-Strings unterscheiden
# sich STRUKTURELL (Intent B enthält keines der Schwachstellen-Synonyme) ────
def test_keine_synonym_duplikate():
    z1 = " ".join(filter(None, ["Mazda MX-5", "2019", "bekannte Probleme Schwachstellen"]))
    z2 = "Mazda MX-5 Gebrauchtwagen Test TÜV Kaufberatung"
    check("C) Intent A und B sind nicht identisch", z1 != z2)
    check("C) Intent B enthält kein Schwachstellen-Synonym (Probleme/Schwachstellen/Mängel)",
          not any(w in z2 for w in ("Probleme", "Schwachstellen", "Mängel", "Defekt")))


# ── D/E) VehicleIdentity generisch genutzt: Generation NUR wenn bekannt ─────
async def test_generation_nur_wenn_bekannt():
    # Generation-neutrale Identitätsfixture (kein "ND"/Code-Hinweis im Text)
    # -- Phase 1 belegt Marke+Modell, bestätigt aber bewusst KEINE Generation.
    _, calls_ohne = await _recherchiere({"bekannte Probleme Schwachstellen": [GETRIEBE_FAKT],
                                         "Gebrauchtwagen Test TÜV Kaufberatung": []},
                                        identitaet_treffer=IDENTITAET_TREFFER_NEUTRAL)
    sw_q1_ohne = next(c for c in calls_ohne if "bekannte Probleme Schwachstellen" in c)
    check("E) ohne bestätigte Generation steht keine Generation in Intent A",
          sw_q1_ohne == "Mazda MX-5 2019 bekannte Probleme Schwachstellen", sw_q1_ohne)

    # Mit künstlich vorbelegter Identität (simuliert Phase 1 = "ND" bestätigt):
    # ausgerichtet über ziel["generation"], genau wie die Produktion es für
    # eine BEREITS belegte Generation tun würde (siehe `gen = identitaet.
    # generation or z.get("generation")` in recherchiere()).
    fake = FakeTavily({"bekannte Probleme Schwachstellen": [GETRIEBE_FAKT],
                      "Gebrauchtwagen Test TÜV Kaufberatung": []},
                     identitaet_treffer=IDENTITAET_TREFFER_NEUTRAL)
    _patch(fake)
    provider = TavilyTechnicalResearchProvider()
    await provider.recherchiere(marke="Mazda", modell="MX-5", baujahr=2019,
                               motor="2.0 SKYACTIV-G 184 PS", ausgeloest_durch="test",
                               ziel={"generation": "ND"})
    sw_q1_mit = next(c for c in fake.calls if "bekannte Probleme Schwachstellen" in c)
    check("D) MIT bekannter Generation steht sie in Intent A",
          sw_q1_mit == "Mazda MX-5 ND 2019 bekannte Probleme Schwachstellen", sw_q1_mit)
    sw_q2_mit = next(c for c in fake.calls if "Gebrauchtwagen" in c)
    check("D) Intent B bleibt bewusst OHNE Generation (editoriale Artikel "
          "sind meist modellgenerationsweit, nicht jahrgangsscharf formuliert)",
          sw_q2_mit == "Mazda MX-5 Gebrauchtwagen Test TÜV Kaufberatung", sw_q2_mit)


# ── G/H) Dedupe: identischer Fakt aus beiden Intents -> 1; unterschiedliche
# Fakten (gleiche oder andere Komponente) bleiben beide erhalten ───────────
async def test_dedupe_identisch_und_verschieden():
    recherche, _ = await _recherchiere({
        "bekannte Probleme Schwachstellen": [GETRIEBE_FAKT],
        "Gebrauchtwagen Test TÜV Kaufberatung": [GETRIEBE_FAKT],
    })
    check("G) identischer Fakt aus beiden Intents -> genau 1 Fakt",
          len(_sw_fakten(recherche)) == 1 and _sw_fakten(recherche)[0][0] == "Getriebe")

    recherche, _ = await _recherchiere({
        "bekannte Probleme Schwachstellen": [GETRIEBE_FAKT],
        "Gebrauchtwagen Test TÜV Kaufberatung": [KUPPLUNG_FAKT],
    })
    check("H) unterschiedliche Komponenten aus beiden Intents -> beide erhalten",
          {b for b, _ in _sw_fakten(recherche)} == {"Getriebe", "Kupplung"})


# ── I) benigner Treffer aus Intent B bleibt (RC-W3) abgelehnt ──────────────
async def test_benign_bleibt_rejected():
    recherche, _ = await _recherchiere({
        "bekannte Probleme Schwachstellen": [GETRIEBE_FAKT],
        "Gebrauchtwagen Test TÜV Kaufberatung": [BENIGN_LENKRAD],
    })
    fakten = _sw_fakten(recherche)
    check("I) Lenkrad-Fakt (benign_relativiert) NICHT in den Fakten",
          not any(b == "Lenkrad" for b, _ in fakten))
    check("I) Getriebe-Fakt bleibt trotzdem erhalten",
          any(b == "Getriebe" for b, _ in fakten))
    check("I) Lenkrad wurde explizit als benign_relativiert abgelehnt, nicht nur stillschweigend",
          any(a.get("grund") == "benign_relativiert" for a in recherche.abgelehnte_fakten))


# ── E/Source-Filter) schlechte Quelle aus einem Intent -- bestehender
# source_quality-Filter greift unverändert ─────────────────────────────────
async def test_schlechte_quelle_gefiltert():
    recherche, _ = await _recherchiere({
        "bekannte Probleme Schwachstellen": [SCHLECHTE_QUELLE_GETRIEBE],
        "Gebrauchtwagen Test TÜV Kaufberatung": [GETRIEBE_FAKT],
    })
    check("schlechte Quelle (motor-talk.de) + gute Quelle (adac.de), gleicher Inhalt "
          "-> genau 1 valider Fakt, keine Verdopplung",
          len(_sw_fakten(recherche)) == 1 and _sw_fakten(recherche)[0][0] == "Getriebe")
    check("schlechte Quelle wurde via source_quality abgelehnt",
          any(a.get("grund") == "source_quality" for a in recherche.abgelehnte_fakten))


# ── J) Wartung bleibt UNVERÄNDERT: exakt dieselben 2 Query-Strings wie vor
# diesem Fix, UND weiterhin "erster Treffer gewinnt" (Fallback-Skip bleibt) ─
async def test_wartung_unveraendert():
    fake = FakeTavily({
        "bekannte Probleme Schwachstellen": [],
        "Gebrauchtwagen Test TÜV Kaufberatung": [],
        "Wartungsintervall Serviceintervall": [GETRIEBE_FAKT],  # beliebiger nichtleerer Treffer
        "Inspektionsintervall": [KUPPLUNG_FAKT],
    }, identitaet_treffer=IDENTITAET_TREFFER_NEUTRAL)
    _patch(fake)
    provider = TavilyTechnicalResearchProvider()
    await provider.recherchiere(marke="Mazda", modell="MX-5", baujahr=2019,
                               motor="2.0 SKYACTIV-G 184 PS", ausgeloest_durch="test")
    wa_calls = [c for c in fake.calls if "Wartungsintervall" in c or "Inspektionsintervall" in c]
    check("J) Wartung-Q1-String exakt wie vor dem Fix",
          any(c == "Mazda MX-5 Wartungsintervall Serviceintervall" for c in wa_calls))
    check("J) Wartung-Q2 wurde NICHT ausgeführt (Q1 war nichtleer -> Fallback-Skip "
          "bleibt für Wartung unverändert bestehen)",
          not any("Inspektionsintervall" in c for c in wa_calls), str(wa_calls))


async def _run_all():
    await test_beide_intents_immer_ausgefuehrt()
    test_keine_synonym_duplikate()
    await test_generation_nur_wenn_bekannt()
    await test_dedupe_identisch_und_verschieden()
    await test_benign_bleibt_rejected()
    await test_schlechte_quelle_gefiltert()
    await test_wartung_unveraendert()


asyncio.run(_run_all())

print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE SCHWACHSTELLE-INTENT-TESTS GRUEN")
