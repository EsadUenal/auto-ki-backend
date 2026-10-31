"""
ENFAL-Folgeauftrag ("Generationsbelege nach belegter Modellfamilien-
Zugehörigkeit bewerten", 2026-11): dauerhafte, gezielte Regression für den
Tier-Erhaltungs-Fix in `_identitaets_claims()` (app/technical_research.py).

BEFUND (vorheriger Analyse-Auftrag, real am Lexus-RC-F-Quellensatz
nachgewiesen): auto-motor-und-sport.de ist eine echte Tier-2-Fachmedien-
Domain (`_TIER_FACHMEDIEN = 40`, app/web_search.py). Ihr Titel "Lexus RC
(1. Generation) (XC1) im Ueberblick (ab 2015)" nennt aber nur die
Modellfamilie "RC", nicht das Zielmodell "RC F" -- das macht die Quelle
pauschal "schwach ausgerichtet" (`ausgerichtet()`), was bisher JEDEN daraus
gewonnenen Claim (PS, Hubraum, Generation, ...) undifferenziert auf Tier 3
abwertete. Für variantenspezifische Werte (PS/Hubraum/...) ist das
sachlich richtig -- für den Generationscode "XC1" nicht: der Code wird
durch `_modellfamilie_zugehoerigkeit_belegt()` bereits EIGENSTAENDIG und
belastbar (Absatzgrenze/Fremdmarke/Verneinung) dem Zielmodell zugeordnet,
bevor er ueberhaupt als Claim entsteht (Praefix-Pfad,
`_modellfamilie_praefixe()`). Die pauschale Tier-3-Abwertung bestrafte
diesen bereits bestaetigten Beleg zusaetzlich, ohne eigenen Mehrwert.

FIX: `add()` in `_identitaets_claims()` akzeptiert jetzt einen optionalen
`tier_override` -- NUR die generation/generation_nummer-Claims aus dem
bereits bestehenden, bereits getesteten Praefix-Zugehoerigkeitspfad nutzen
ihn (den ECHTEN Domain-Tier VOR der "schwach"-Abwertung, nach der
Regionsfremd-Anpassung). Alle anderen Claim-Arten und alle Quellen, deren
Zugehoerigkeit NICHT eigenstaendig belegt ist, bleiben unveraendert.

Kein Lexus-/Marken-Sonderfall (s. Fall E unten, anderes Fahrzeug). UXC1 und
XC1 werden NICHT gleichgesetzt -- die Generation bleibt beim vollen realen
Quellensatz weiterhin UNKNOWN (test_rc_f_generation_contract_diagnose.py),
nur jetzt ueber einen ehrlicheren Konflikt-Pfad statt zwei zu schwacher
Fragmente.

    python scripts/test_isolated.py test_generation_tier_zugehoerigkeit_erhalten.py
"""
from app.technical_research import _identitaets_claims, _ziel

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


def generation_claims(marke, modell, baujahr, motor, r):
    z = _ziel(marke, modell, baujahr, motor, None)
    return [c for c in _identitaets_claims(r, z) if c["feld"] in ("generation", "generation_nummer")]


def andere_claims(marke, modell, baujahr, motor, r):
    z = _ziel(marke, modell, baujahr, motor, None)
    return [c for c in _identitaets_claims(r, z) if c["feld"] not in ("generation", "generation_nummer")]


# ── A) Reale AMS-Fixture: Generationsclaim behaelt sein ECHTES Tier (2) ────
AMS_REAL = treffer(
    "https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
    "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
    "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Leistung 181 PS bis "
    "477 PS. LEXUS RC F TRACK EDITION IM SUPERTEST: Was kann der V8-Sauger auf der "
    "Rennstrecke? LEXUS LC 500 UND RC F: V8-Sportler auf Abschiedstour.")
gen_a = generation_claims("Lexus", "RC F", 2017, "5.0 V8", AMS_REAL)
check("A1) genau ein Generationsclaim (XC1) plus Ordinalzahl erkannt",
      {c["wert"] for c in gen_a} == {"XC1", "1. Generation"}, str(gen_a))
check("A2) beide behalten das ECHTE Fachmedien-Tier 2 (nicht die pauschale 3)",
      all(c["tier"] == 2 for c in gen_a), str(gen_a))

# ── B) Variantenspezifischer Claim bekommt dadurch KEINE Freigabe ──────────
PS_SCHWACH = treffer(
    "https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
    "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
    "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Der Lexus RC F "
    "leistet 477 PS.")
andere_b = andere_claims("Lexus", "RC F", 2017, "5.0 V8", PS_SCHWACH)
check("B1) PS-Claim bleibt unveraendert auf Tier 3 (variantenspezifisch, "
      "keine Zugehoerigkeitspruefung dafuer)",
      bool(andere_b) and all(c["tier"] == 3 for c in andere_b if c["feld"] == "horsepower"),
      str(andere_b))

# ── C) Fehlende/mehrdeutige Familienzugehoerigkeit: keine Ausnahme ─────────
# Zwei Modellnennungen im eigenen Satz des Codes (spaltenweise serialisierte
# Vergleichstabelle) -- `_modellfamilie_zugehoerigkeit_belegt()` verwirft das
# bereits strukturell (s. test_identity_familie_zugehoerigkeit.py Fall P).
MEHRDEUTIG = treffer(
    "https://www.auto-motor-und-sport.de/vergleich/",
    "Lexus RC Vergleich",
    "Modell RC 350 Modell RC F Baureihe RC (UXB2) Baureihe RC (UXC1).")
gen_c = generation_claims("Lexus", "RC F", 2017, "5.0 V8", MEHRDEUTIG)
check("C1) strukturell unentscheidbare Zuordnung erzeugt KEINEN Generationsclaim "
      "(keine Tier-Ausnahme ohne belegte Zugehoerigkeit)",
      gen_c == [], str(gen_c))

# ── D) Fremde Generation / unpassendes Baujahr bleibt geschuetzt ───────────
BAUJAHR_AUSSERHALB = treffer(
    "https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
    "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
    "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Der Lexus RC F "
    "leistet 477 PS.")
gen_d = generation_claims("Lexus", "RC F", 2010, "5.0 V8", BAUJAHR_AUSSERHALB)
check("D1) Baujahr 2010 ausserhalb des Fensters (ab 2015) -> Claim bleibt "
      "verworfen, UNABHAENGIG vom (jetzt hoeheren) Tier",
      bool(gen_d) and all(c.get("verworfen") == "baujahr_ausserhalb" for c in gen_d
                          if c["feld"] == "generation"),
      str(gen_d))

# ── E) Generische Kontrolle mit anderem synthetischem Fahrzeug (kein Lexus) ─
VW_GOLF = treffer(
    "https://www.auto-motor-und-sport.de/marken-modelle/vw/golf/",
    "VW Golf (8. Generation) (CD1) im Ueberblick (ab 2019)",
    "VW Golf (8. Generation) (CD1) im Ueberblick (ab 2019). Der VW Golf GTI "
    "leistet 245 PS.")
gen_e = generation_claims("VW", "Golf GTI", 2020, "2.0 TSI", VW_GOLF)
andere_e = andere_claims("VW", "Golf GTI", 2020, "2.0 TSI", VW_GOLF)
check("E1) generisch: VW Golf GTI (anderes Fahrzeug) -> Generationsclaim "
      "(CD1) behaelt ebenfalls Tier 2, keine Marken-/Modell-Sonderregel",
      bool(gen_e) and all(c["tier"] == 2 for c in gen_e
                          if c["wert"] == "CD1" and not c.get("verworfen")),
      str(gen_e))
check("E2) VW-PS-Claim bleibt ebenso auf Tier 3 wie im Lexus-Fall",
      bool(andere_e) and all(c["tier"] == 3 for c in andere_e if c["feld"] == "horsepower"),
      str(andere_e))

# ── F) Derselbe Fall MIT explizitem Fremdmarkt (US_QUELLEN_AUSSCHLUSS) ─────
# ENFAL-Abschlussauftrag ("Generations-Tier-Fix abschliessend absichern"):
# `tier_vor_schwach_abwertung` wird NACH der Regionsfremd-Anpassung
# (`_ist_regionsfremd()`, Zeile ~1858) erfasst -- der Praefix-Pfad darf eine
# US-Quelle also NICHT ueber die Regionsfremd-Deckelung hinaus aufwerten,
# selbst wenn `_modellfamilie_zugehoerigkeit_belegt()` die Zuordnung
# bestaetigt. motortrend.com steht in `US_QUELLEN_AUSSCHLUSS`
# (app/web_search.py) und hat KEINEN eigenen Fachmedien-Bonus -- ihr
# natuerliches Tier waere ohne Fremdmarkt-Anpassung bereits 3; dieser Fall
# beweist trotzdem die REIHENFOLGE im Code (Fremdmarkt-Deckelung wirkt auf
# `tier`, BEVOR `tier_vor_schwach_abwertung` diesen Wert festhaelt), nicht
# nur ein zufaelliges Zahlenergebnis.
US_QUELLE = treffer(
    "https://www.motortrend.com/cars/lexus/rc/",
    "Lexus RC (1. Generation) (XC1) Overview (from 2015)",
    "Lexus RC (1. Generation) (XC1) Overview (from 2015). The Lexus RC F "
    "makes 477 hp.")
gen_f = generation_claims("Lexus", "RC F", 2017, "5.0 V8", US_QUELLE)
check("F1) US-Quelle (US_QUELLEN_AUSSCHLUSS) bleibt trotz bestaetigter "
      "Zugehoerigkeit auf Tier 3 begrenzt -- die Regionsfremd-Deckelung wird "
      "vom tier_override NICHT umgangen",
      bool(gen_f) and all(c["tier"] == 3 for c in gen_f if not c.get("verworfen")),
      str(gen_f))

print()
if _FEHLER:
    print(f"FEHLER: {len(_FEHLER)} Check(s) fehlgeschlagen: {_FEHLER}")
    raise SystemExit(1)
print("ALLE GENERATION-TIER-ZUGEHOERIGKEIT-TESTS GRUEN")
