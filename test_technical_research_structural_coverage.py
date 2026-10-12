"""
Package C / RC-W3 (struktureller Coverage-Fix): echte technische Defekt-/
Verschleiss-/Ursache-Folge-Claims ohne klassisches `_PROBLEM_WORTE`-Wort
duerfen nicht mehr stillschweigend an `no_problem_signal` verloren gehen -
aber nur ueber ein kleines, geschlossenes Struktursignal-Set (Material/
Zustand, Verschleiss-Veraenderung, "Spiel" nur mit Erwerbs-Verb), NIE ueber
eine pauschale Erweiterung von `_PROBLEM_WORTE` oder generische Akzeptanz
jedes Komponentenworts/jeder Vergleichsform.

BEFUND (RC-W3 Silent-Drop-Audit): "Bei hoeheren Laufleistungen kann die
Steuerkette deutlich laenger werden." und "Feuchtigkeit im Scheinwerfer
kann Korrosion am Steuergeraet verursachen." wurden verworfen, obwohl beide
echte, kaufrelevante technische Claims sind. Eine naive Erweiterung um
Woerter wie "Rost"/"Korrosion" waere NICHT sicher gewesen, weil die
bestehende Negationslogik (`_ist_benign_relativiert`) "kein(e) <Wort>" nur
fuer eine feste Phrasenliste erkannte, nicht generisch fuer jedes
Defektwort - "Das Fahrzeug zeigt keinen Rost" waere sonst zum Schwachpunkt
geworden.

GENERISCHE REGEL (app/technical_research.py):
  `_hat_strukturelles_defektsignal()` - kleines, geschlossenes Signal-Set.
  `_RE_NEGATION_NOMEN` / `_RE_NEGATION_ADJ` - generische "kein(e) <Defekt-
  wort>"/"nicht <Verschleiss-Adjektiv>"-Verneinung ueber die GESAMTE
  Wortliste (alt + neu), statt Phrase-fuer-Phrase.

    python test_technical_research_structural_coverage.py
"""
from app.technical_research import _extrahiere_fakten

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def _treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


def _pruefe(satz: str, kategorie: str = "schwachstelle") -> tuple[bool, list[str]]:
    abgelehnt: list[dict] = []
    t = [_treffer("https://www.autobild.de/test", "Testmodell Ratgeber", satz)]
    fakten = _extrahiere_fakten(t, kategorie, baujahr=2019, abgelehnt=abgelehnt)
    return bool(fakten), [a.get("grund") for a in abgelehnt]


print("\n=== MUSS ACCEPT (echte technische Claims) ===")
_ACCEPT = [
    ("A positiver Defekt (klassisches Problemwort)",
     "Die Wasserpumpe faellt haeufig aus."),
    ("B Verschleiss-Veraenderung ohne Problemwort",
     "Bei hoeheren Laufleistungen kann die Steuerkette deutlich laenger werden."),
    ("C Ursache/Folge mit Korrosion",
     "Feuchtigkeit im Scheinwerfer kann Korrosion am Steuergeraet verursachen."),
    ("D Spiel mit Erwerbs-Verb",
     "Die Querlenkerbuchsen bekommen frueh Spiel."),
    ("E Verschleiss-Veraenderung (porös/undicht)",
     "Die Dichtung wird mit der Zeit poroes und kann undicht werden."),
    ("F Funktionsverlust/Ursache-Folge (klassisches Problemwort 'Ausfall')",
     "Ein Ausfall der Kraftstoffpumpe kann zum Motorstillstand fuehren."),
]
for name, satz in _ACCEPT:
    akzeptiert, gruende = _pruefe(satz)
    check(f"{name}: AKZEPTIERT (gruende={gruende})", akzeptiert)

print("\n=== MUSS REJECT (benign/negiert/zu vage/kein Signal) ===")
_REJECT = [
    ("G negierter Rost", "Das Fahrzeug zeigt keinen Rost an der Karosserie."),
    ("H negierte Korrosion", "Es ist keine Korrosion am Steuergeraet feststellbar."),
    ("I negiertes Spiel", "Die Lager haben kein Spiel."),
    ("J benign/konstruktionsbedingt", "Das Geraeusch ist konstruktionsbedingt unkritisch."),
    ("K relativierter Unfallverdacht",
     "Das Lenkrad steht leicht schief, muss aber nicht auf einen Unfall hindeuten."),
    ("L vage Anekdote, kein Bauteil", "Manche Fahrer berichten von Problemen."),
    ("M normale Leistungsvarianz (kein Struktursignal)",
     "Die Leistung kann im Winter etwas geringer sein."),
    ("N normale Verbrauchsvarianz (kein Struktursignal)",
     "Der Verbrauch kann im Stadtverkehr hoeher sein."),
]
for name, satz in _REJECT:
    akzeptiert, gruende = _pruefe(satz)
    check(f"{name}: VERWORFEN (gruende={gruende})", not akzeptiert)

print("\n=== Negations-Stresstest: Abwesenheitsformen ueber 'kein(e) X' hinaus ===")
_NEGATION_STRESS_REJECT = [
    ("nicht feststellbar", "Korrosion ist am Steuergeraet nicht feststellbar."),
    ("nicht vorhanden", "Rost ist an der Karosserie nicht vorhanden."),
    ("ohne X", "Der Unterboden ist ohne Korrosion."),
    ("frei von X", "Das Bauteil ist frei von Rost."),
    ("keine Anzeichen fuer X", "Es konnten keine Anzeichen fuer Korrosion festgestellt werden."),
    ("keinerlei X-Spuren", "Am Fahrzeug finden sich keinerlei Rostspuren."),
    ("kein Hinweis auf X", "Es besteht kein Hinweis auf Durchrostung."),
    ("weder X noch Y", "Die Karosserie zeigt weder Rost noch Korrosion."),
]
for name, satz in _NEGATION_STRESS_REJECT:
    akzeptiert, gruende = _pruefe(satz)
    check(f"Negationsform '{name}': VERWORFEN (gruende={gruende})", not akzeptiert)

print("\n=== Negations-Stresstest: echte Claims bleiben trotz Haertung erhalten ===")
_NEGATION_STRESS_ACCEPT = [
    ("Negation im Nebensatz, echter Folgeclaim",
     "Nicht behobene Korrosion kann sich weiter ausbreiten."),
    ("'ohne' mit Abstand zum Defektwort (kein falscher Treffer)",
     "Ohne rechtzeitige Reparatur kann Rost die Struktur schwaechen."),
    ("'nicht nur' ohne Abwesenheits-Adjektiv",
     "Korrosion ist nicht nur oberflaechlich."),
]
for name, satz in _NEGATION_STRESS_ACCEPT:
    akzeptiert, gruende = _pruefe(satz)
    check(f"{name}: bleibt AKZEPTIERT (gruende={gruende})", akzeptiert)

print("\n=== Symptome bleiben bewusst konservativ (keine neue Akzeptanz) ===")
akzeptiert, gruende = _pruefe("Beim Kaltstart kann ein metallisches Rasseln auftreten.")
check("bloßes Symptom ohne Defektbezug bleibt VERWORFEN (no_problem_signal)",
      not akzeptiert and gruende == ["no_problem_signal"])

print("\n=== Mixed Context: echter Struktur-Claim + benigner Satz im selben Treffer ===")
treffer_mixed = [_treffer(
    "https://www.autobild.de/mazda-mx-5-ratgeber", "Mazda MX-5 ND Gebrauchtwagen-Ratgeber",
    "Ein schief stehendes Lenkrad muss nicht unbedingt auf einen Unfallschaden hindeuten. "
    "Das ist haeufig unproblematisch. "
    "Bei hoeheren Laufleistungen kann die Steuerkette deutlich laenger werden, eine "
    "Probefahrt ist dringend empfehlenswert.")]
abgelehnt_mixed: list[dict] = []
fakten_mixed = _extrahiere_fakten(treffer_mixed, "schwachstelle", marke="Mazda", modell="MX-5",
                                  baujahr=2019, abgelehnt=abgelehnt_mixed)
check("genau 1 Fakt (Steuerkette), Lenkrad bleibt draussen",
      len(fakten_mixed) == 1 and fakten_mixed[0].bauteil == "Steuerkette")
check("Lenkrad-Satz weiterhin als benign_relativiert dokumentiert",
      any(a.get("grund") == "benign_relativiert" for a in abgelehnt_mixed))

print("\n=== Wartung unveraendert (Kategorie 'wartung', nicht vom Struktursignal betroffen) ===")
akzeptiert, gruende = _pruefe("Das Getriebeoel sollte spaetestens alle 60.000 km gewechselt "
                              "werden.", kategorie="wartung")
check("Wartung mit Intervall bleibt AKZEPTIERT", akzeptiert)
akzeptiert, gruende = _pruefe("Dieses Modell braucht gelegentlich eine Inspektion.",
                              kategorie="wartung")
check("Wartung ohne Intervall bleibt VERWORFEN (no_maintenance_interval)",
      not akzeptiert and gruende == ["no_maintenance_interval"])

print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE STRUCTURAL-COVERAGE-TESTS GRUEN")
