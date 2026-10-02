"""
Release-Hardening (Continuation): per-phase research completeness + strong-
evidence powertrain resolution. Both were explicit stop conditions left open
by the prior report. KEIN Netzwerk, KEIN LLM-Call, KEIN Tavily-Call.

GENERISCH (kein Fahrzeug-/Marken-Branching in der Logik): alle Fixtures unten
verwenden eine erfundene Marke/Modell ("Testmarke Modellz"), um zu beweisen,
dass nichts davon an eine konkrete Marke/Generation/Motor gebunden ist. Der
reale Mazda-MX-5-Produktions-Smoke-Test läuft separat in Abschnitt D.

  A) Per-Phase Research Completeness (app/technical_research.py::_phase_status,
     TechnischeRecherche.phasen_status)
     A1  Rückrufsuche erfolgreich, 0 Rückrufe      -> RECALL SUCCESS
     A2  Rückrufsuche: Provider schlägt fehl        -> RECALL FAILED
     A3  Technik-Suche nur Tier-3/duenn             -> TECHNICAL PARTIAL
     A4  Identität erfolgreich + Rückruf FAILED     -> NICHT gleichwertig zu
                                                        einer vollständig
                                                        erfolgreichen Analyse
                                                        (Bericht unterscheidet)
     A5  alle Phasen erfolgreich                    -> normale Beleglage bleibt
                                                        möglich (kein
                                                        erzwungener Hinweis)

  B) Starke, explizite Powertrain-Evidenz (app/technical_research.py::
     _identitaets_claims/werte_identitaet_aus, wiederverwendet aus
     app/kraftstoff_powertrain.py::powertrain_aus_freitext)
     B1  nur "Benzin"                                -> powertrain UNKNOWN
     B2  starke Quelle: "reiner Verbrenner"           -> ICE + Provenienz
     B3  starke Quelle: "Mild-Hybrid"                 -> MHEV
     B4  starke Quelle: "Plug-in-Hybrid"              -> PHEV
     B5  starke Quelle: "reines Elektroauto"           -> BEV
     B6  zwei starke, widersprechende Quellen         -> Konflikt (None),
                                                          keine willkürliche Wahl

  C) technical_coverage()/Report-Hinweis bleibt additiv (ändert nie Empfehlung)

  D) Mazda-Production-Smoke-Fixture erneut, diesmal MIT und OHNE starke
     ICE-Evidenz

    python test_phase_completeness_powertrain.py
"""
import asyncio

from app.kaufcheck_bericht import forschungsluecken_hinweis
from app.models import TechnischeRecherche
from app.technical_research import (
    PHASE_FAILED, PHASE_NOT_RUN, PHASE_PARTIAL, PHASE_SUCCESS,
    FixtureTechnicalResearchProvider, _identitaets_claims, phase_identitaet,
    werte_identitaet_aus,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def treffer(url: str, titel: str, inhalt: str) -> dict:
    return {"url": url, "title": titel, "content": inhalt}


# Zwei unabhängige, als Tier<=2 bekannte Domains (dieselben, die der Rest der
# Testsuite durchgängig für "starke Quelle" verwendet) — nichts davon ist
# Mazda-/BMW-/markenspezifischer Code, nur Testdaten.
def _identitaet_fixture(extra_satz: str = "") -> list[dict]:
    return [
        treffer("https://www.adac.de/testmarke-modellz", "Testmarke Modellz im Test",
                f"Die Testmarke Modellz ist ein Fahrzeug mit 2.0-Liter-Benzinmotor, 150 PS. {extra_satz}"),
        treffer("https://www.auto-motor-und-sport.de/testmarke-modellz", "Testmarke Modellz Daten",
                f"Testmarke Modellz: 2.0-Liter-Benzinmotor, 150 PS, Frontantrieb. {extra_satz}"),
    ]


ZIEL = dict(marke="Testmarke", modell="Modellz", baujahr=2021, motor=None)


# ══════════════════════════════════════════════════════════════════════════
print("\n=== A) Per-Phase Research Completeness ===")

print("--- A1: Rückrufsuche erfolgreich, 0 Rückrufe -> RECALL SUCCESS ---")
provider_a1 = FixtureTechnicalResearchProvider({
    "identitaet": _identitaet_fixture(), "rueckruf": [], "schwachstelle": [], "wartung": [],
})
res_a1 = asyncio.run(provider_a1.recherchiere(**ZIEL, ausgeloest_durch="db_miss"))
check("A1a Rückruf-Phase lief (phasen enthält 'rueckruf')", "rueckruf" in res_a1.phasen)
check("A1b 0 Rückruf-Fakten", not any(f.kategorie == "rueckruf" for f in res_a1.fakten))
check("A1c phasen_status['rueckruf'] == SUCCESS (0 Treffer ist ein gültiges Ergebnis)",
      res_a1.phasen_status.get("rueckruf") == PHASE_SUCCESS)
check("A1d provider_fehler bleibt False", res_a1.provider_fehler is False)

print("--- A2: Rückrufsuche: Provider schlägt vollständig fehl -> RECALL FAILED ---")
provider_a2 = FixtureTechnicalResearchProvider(
    {"identitaet": _identitaet_fixture(), "rueckruf": [
        treffer("https://www.kba.de/irrelevant", "Rückruf", "Ein Rückruf, den die fehlgeschlagene "
                "Suche NIE sehen darf.")],
     "schwachstelle": [], "wartung": []},
    fehler_phasen=frozenset({"rueckruf"}))
res_a2 = asyncio.run(provider_a2.recherchiere(**ZIEL, ausgeloest_durch="db_miss"))
check("A2a phasen_status['rueckruf'] == FAILED", res_a2.phasen_status.get("rueckruf") == PHASE_FAILED)
check("A2b der simulierte Rückruf-Treffer wurde NICHT verwendet (Phase lief nicht)",
      not any(f.kategorie == "rueckruf" for f in res_a2.fakten))
check("A2c provider_fehler == True", res_a2.provider_fehler is True)
check("A2d A1 (SUCCESS, 0 Treffer) und A2 (FAILED) sind UNTERSCHIEDLICHE Zustände",
      res_a1.phasen_status.get("rueckruf") != res_a2.phasen_status.get("rueckruf"))

print("--- A3: Technik-Suche liefert nur Tier-3/duenne Quellen -> TECHNICAL PARTIAL ---")
provider_a3 = FixtureTechnicalResearchProvider({
    "identitaet": _identitaet_fixture(), "rueckruf": [],
    "schwachstelle": [
        treffer("https://www.irgendein-forum-xyz.de/thread/123", "Forenbeitrag",
                "Ein Nutzer berichtet: die Testmarke Modellz hat gelegentlich ein Problem mit "
                "dem Kühler."),
    ],
    "wartung": [],
})
res_a3 = asyncio.run(provider_a3.recherchiere(**ZIEL, ausgeloest_durch="db_miss"))
check("A3a Technik-Phase lief", "technik" in res_a3.phasen)
check("A3b phasen_status['technik'] == PARTIAL (nur Tier-3-Beleg)",
      res_a3.phasen_status.get("technik") == PHASE_PARTIAL)

print("--- A4: Identität erfolgreich + Rückruf FAILED -> NICHT gleichwertig zu voller Success ---")
hinweis_a4 = forschungsluecken_hinweis(res_a2)
res_a5_fuer_vergleich = asyncio.run(FixtureTechnicalResearchProvider({
    "identitaet": _identitaet_fixture(), "rueckruf": [], "schwachstelle": [
        treffer("https://www.adac.de/testmarke-modellz-schwachstellen", "Schwachstellen",
                "Die Testmarke Modellz gilt als bekannte Schwachstelle beim Kühler."),
    ], "wartung": [],
}).recherchiere(**ZIEL, ausgeloest_durch="db_miss"))
hinweis_a5 = forschungsluecken_hinweis(res_a5_fuer_vergleich)
check("A4a FAILED-Fall erzeugt einen Berichtshinweis", len(hinweis_a4) > 0)
check("A4b voller Erfolg (A5-Szenario) erzeugt KEINEN Hinweis", len(hinweis_a5) == 0)
check("A4c der Hinweis behauptet NICHT, es gäbe keinen Rückruf — nur, dass die Prüfung "
      "nicht abgeschlossen wurde", "nicht" in hinweis_a4[0].lower() and "Rückrufrecherche" in hinweis_a4[0])

print("--- A5: alle Phasen erfolgreich -> normale Beleglage bleibt möglich ---")
check("A5a identitaet SUCCESS", res_a5_fuer_vergleich.phasen_status.get("identitaet") == PHASE_SUCCESS)
check("A5b rueckruf SUCCESS", res_a5_fuer_vergleich.phasen_status.get("rueckruf") == PHASE_SUCCESS)
check("A5c technik SUCCESS (Tier<=2-Beleg vorhanden)",
      res_a5_fuer_vergleich.phasen_status.get("technik") == PHASE_SUCCESS)
check("A5d kein Forschungslücken-Hinweis nötig", forschungsluecken_hinweis(res_a5_fuer_vergleich) == [])

print("--- Zusatz: Identität nie versucht -> NOT_RUN, nicht FAILED ---")
res_leer = TechnischeRecherche(ausgeloest_durch="db_miss")
check("Leeres TechnischeRecherche-Objekt: phasen_status default leeres dict (additiv, kein Crash)",
      res_leer.phasen_status == {})
check("forschungsluecken_hinweis toleriert ein leeres phasen_status (kein FAILED => kein Hinweis)",
      forschungsluecken_hinweis(res_leer) == [])

provider_notrun = FixtureTechnicalResearchProvider({"identitaet": [], "rueckruf": [], "schwachstelle": [], "wartung": []})
res_notrun = asyncio.run(provider_notrun.recherchiere(**ZIEL, ausgeloest_durch="db_miss"))
check("Identität nicht belegt -> rueckruf/technik NOT_RUN (nicht FAILED)",
      res_notrun.phasen_status.get("rueckruf") == PHASE_NOT_RUN
      and res_notrun.phasen_status.get("technik") == PHASE_NOT_RUN)
check("NOT_RUN erzeugt KEINEN 'Forschung fehlgeschlagen'-Hinweis (das ist ein anderer, "
      "bereits existierender Berichtsfall: 'Identität nicht bestätigt')",
      forschungsluecken_hinweis(res_notrun) == [])


# ══════════════════════════════════════════════════════════════════════════
print("\n=== B) Starke, explizite Powertrain-Evidenz ===")

print("--- B1: nur 'Benzin' -> powertrain UNKNOWN ---")
ident_b1, _ = werte_identitaet_aus(_identitaet_fixture(), ZIEL)
check("B1a fuel wurde erkannt (Benzin)", (ident_b1.kraftstoff or "").lower() == "benzin")
check("B1b powertrain bleibt UNKNOWN (None) — 'Benzin' allein impliziert NIE ICE",
      ident_b1.powertrain is None)

print("--- B2: starke Quelle 'reiner Verbrenner' -> ICE + Provenienz ---")
ident_b2, _ = werte_identitaet_aus(_identitaet_fixture("Es handelt sich um einen reinen "
                                                       "Verbrenner ohne jede Elektrifizierung."), ZIEL)
check("B2a powertrain == ICE", ident_b2.powertrain == "ICE")
check("B2b Provenienz vorhanden (feldwerte trägt Quellenanzahl + Confidence)",
      "powertrain" in ident_b2.feldwerte
      and ident_b2.feldwerte["powertrain"]["domains"] >= 1
      and ident_b2.feldwerte["powertrain"]["confidence"] in ("hoch", "mittel", "niedrig"))

print("--- B3: starke Quelle 'Mild-Hybrid' -> MHEV ---")
ident_b3, _ = werte_identitaet_aus(_identitaet_fixture("Der Antrieb ist als Mild-Hybrid ausgelegt."), ZIEL)
check("B3 powertrain == MHEV", ident_b3.powertrain == "MHEV")

print("--- B4: starke Quelle 'Plug-in-Hybrid' -> PHEV ---")
ident_b4, _ = werte_identitaet_aus(_identitaet_fixture("Es ist als Plug-in-Hybrid erhältlich."), ZIEL)
check("B4 powertrain == PHEV", ident_b4.powertrain == "PHEV")

print("--- B5: starke Quelle 'reines Elektroauto' -> BEV ---")
ident_b5, _ = werte_identitaet_aus(_identitaet_fixture("Die Testmarke Modellz ist ein reines Elektroauto."), ZIEL)
check("B5 powertrain == BEV", ident_b5.powertrain == "BEV")

print("--- B6: zwei starke, widersprechende Powertrain-Quellen -> Konflikt, keine Willkür ---")
treffer_konflikt = [
    treffer("https://www.adac.de/testmarke-modellz", "Testmarke Modellz im Test",
            "Die Testmarke Modellz ist ein reiner Verbrenner ohne jede Elektrifizierung."),
    treffer("https://www.auto-motor-und-sport.de/testmarke-modellz", "Testmarke Modellz Daten",
            "Die Testmarke Modellz ist als Mild-Hybrid ausgelegt."),
]
ident_b6, _ = werte_identitaet_aus(treffer_konflikt, ZIEL)
check("B6a powertrain bleibt None (Konflikt, kein 'erstbestes Signal gewinnt')",
      ident_b6.powertrain is None)
check("B6b 'powertrain' erscheint NICHT in feldwerte (kein willkürlich akzeptierter Wert)",
      "powertrain" not in ident_b6.feldwerte)


# ══════════════════════════════════════════════════════════════════════════
print("\n=== D) Mazda-Production-Smoke-Fixture: MIT und OHNE starke ICE-Evidenz ===")
# Dieselbe reale Eingabe wie im vorherigen Produktions-Smoke-Test — diesmal
# zusätzlich geprüft: OHNE eine ausdrückliche Elektrifizierungsaussage bleibt
# Antriebsart(=powertrain) UNKNOWN (korrekt, wie zuvor berichtet); MIT einer
# starken, ausdrücklichen Aussage löst sie sich auf.
ZIEL_MAZDA = dict(marke="Mazda", modell="MX-5", baujahr=2019, motor="2.0 SKYACTIV-G")

treffer_mazda_ohne_ice = [
    treffer("https://www.adac.de/mazda-mx-5-nd", "Mazda MX-5 ND (seit 2015)",
            "Die 4. Generation des Mazda MX-5 (ND, seit 2015): 2.0 SKYACTIV-G mit 184 PS, "
            "Benziner, Hinterradantrieb."),
    treffer("https://www.auto-motor-und-sport.de/mazda-mx-5-nd", "Mazda MX-5 ND im Test",
            "Mazda MX-5 ND (seit 2015) mit 2.0 SKYACTIV-G und 184 PS, Heckantrieb."),
]
ident_mazda_ohne, _ = werte_identitaet_aus(treffer_mazda_ohne_ice, ZIEL_MAZDA)
check("D1 OHNE explizite Elektrifizierungsaussage bleibt powertrain korrekt UNKNOWN "
      "(kein Auto-ICE aus 'Benzin')", ident_mazda_ohne.powertrain is None)

treffer_mazda_mit_ice = [
    treffer("https://www.adac.de/mazda-mx-5-nd", "Mazda MX-5 ND (seit 2015)",
            "Die 4. Generation des Mazda MX-5 (ND, seit 2015): 2.0 SKYACTIV-G mit 184 PS, "
            "Benziner, Hinterradantrieb. Der MX-5 wird ausschließlich als reiner "
            "Verbrenner ohne jede Elektrifizierung angeboten."),
    treffer("https://www.auto-motor-und-sport.de/mazda-mx-5-nd", "Mazda MX-5 ND im Test",
            "Mazda MX-5 ND (seit 2015) mit 2.0 SKYACTIV-G und 184 PS, Heckantrieb. "
            "Anders als viele Wettbewerber bleibt der MX-5 ein reiner Verbrenner."),
]
ident_mazda_mit, _ = werte_identitaet_aus(treffer_mazda_mit_ice, ZIEL_MAZDA)
check("D2 MIT starker, ausdrücklicher ICE-Aussage löst powertrain auf: ICE",
      ident_mazda_mit.powertrain == "ICE")
check("D3 Fuel/Antrieb bleiben in beiden Fällen unverändert korrekt (Fix ist additiv)",
      ident_mazda_ohne.kraftstoff == ident_mazda_mit.kraftstoff == "benzin"
      and ident_mazda_ohne.antrieb == ident_mazda_mit.antrieb == "Heck")


print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE PHASE-COMPLETENESS/POWERTRAIN-TESTS GRUEN")
