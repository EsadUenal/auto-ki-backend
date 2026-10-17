"""
Lexus-RC-F-Forensik, Package A (Basic Maintenance Coverage) — Root Cause 5
+ §10/§12 Auftrag (Intervallformen, Observability).

BEFUND: `_bauteil_vokabular()` (mit "schwachstelle" GETEILT) kannte kein
generisches "Motoröl/Ölwechsel"-Muster -- nur Symptom-Komposita wie
"oelverlust"/"oelverbrauch". Eine der häufigsten Wartungsaussagen überhaupt
("Motoröl alle X km wechseln") erreichte NIE `treffer_voc`, unabhängig von
Quellenqualität -- im Hauptpfad `grund="no_component"`, im Candidate-Pfad
VOLLSTÄNDIG STILL (kein Reject-Eintrag).

FIX: `_wartung_vokabular()` -- eine SEPARATE, nur für kategorie=="wartung"
gemergte Erweiterung (NICHT in `_KOMPONENTEN` selbst, siehe Auftrag: "nicht
blind erweitern, wenn Schwachstellen-Erkennung sich dadurch ändert"). Zusätzlich:
`_wartung_merkmal_normalisiert()` verlor bisher ein zweites Intervall im selben
Claim ("... alle 15.000 km ODER 12 Monate" -> nur km wurde erfasst) -- jetzt
werden alle Intervall-Angaben gesammelt. Und: der Candidate-Pfad loggt den
"kein Bauteil trotz Intervall"-Fall jetzt explizit (`no_component`) statt
stillschweigend zu verwerfen.

    python test_wartung_motoroel_vokabular.py
"""
from app.technical_research import (
    _wartung_candidate_fakten, _wartung_merkmal_normalisiert, _ziel, phase_fakten,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


class _Ident:
    def __init__(self, generation=None):
        self.generation = generation


def _lauf(treffer_liste, *, marke="Lexus", modell="RC F", motor=None, generation=None):
    extra = {"generation": generation} if generation else None
    z = _ziel(marke, modell, 2017, motor, extra)
    abgelehnt: list[dict] = []
    fakten = phase_fakten({"wartung": treffer_liste}, z, _Ident(generation), ("wartung",), abgelehnt)
    return fakten, abgelehnt


def _gruende(abgelehnt):
    return {a.get("grund") for a in abgelehnt}


print("=" * 78)
print("A) PRIMARY: 'Motoroel alle 15.000 km wechseln' -> accepted")
print("=" * 78)
# Hersteller-Domain einer BEREITS gelisteten Marke (volkswagen.at) -- bewusst
# NICHT Lexus hier: score_domain()/Fact-Scoring fuer Wartung ist in diesem
# Package UNVERAENDERT (siehe Root-Cause-1-Fix, der sich ausdruecklich NUR auf
# die Identitaetsstufe beschraenkt) -- dieser Testfall prueft isoliert die
# Vokabular-Erweiterung, nicht die Markenerkennung.
primary = treffer("https://www.volkswagen.at/golf-service", "VW Golf Service",
                  "Das Motoroel wird beim Golf alle 15.000 km gewechselt.")
fakten, abgelehnt = _lauf([primary], marke="VW", modell="Golf")
check("A) Hersteller-Quelle mit 'Motoroel' -> genau 1 bestaetigter Wartungsfakt",
      len(fakten) == 1 and fakten[0].bauteil and "oel" in fakten[0].bauteil.lower(),
      f"fakten={fakten}")

print("\n" + "=" * 78)
print("B) STRONG SECONDARY (Technik-Hersteller-Domain): 'Oelwechsel alle 15.000 km'")
print("=" * 78)
strong_sec = treffer("https://www.bosch-presse.de/lexus-rc-f-oelwechsel", "Lexus RC F Oelwechsel",
                     "Der Oelwechsel erfolgt beim RC F alle 15.000 km.")
fakten, abgelehnt = _lauf([strong_sec])
check("B) Technik-Hersteller-Domain (score>=MIN_SCORE_FAKT) -> Hauptpfad akzeptiert "
      "(bestehende Policy, jetzt ERREICHBAR dank Vokabular-Fix)",
      len(fakten) == 1, f"fakten={fakten} abgelehnt={abgelehnt}")

print("\n" + "=" * 78)
print("C) LOW-SCORE SECONDARY: 'Motoroel alle 15.000 km wechseln' -> Candidate, "
      "NICHT mehr still verloren")
print("=" * 78)
low_score = treffer("https://www.repareo.de/lexus-rc-f-motoroel", "Lexus RC F Motoroel",
                    "Das Motoroel wird beim RC F alle 15.000 km gewechselt.")
fakten_c, abgelehnt_c = _wartung_candidate_fakten(
    [low_score], marke="Lexus", modell="RC F", generation=None, motor=None)
check("C) einzelne Low-Score-Quelle erzeugt KEINEN harten Fakt (bestehende Policy)",
      len(fakten_c) == 0)
check("C) ABER: sie erzeugt jetzt einen SICHTBAREN Candidate-Reject "
      "('wartung_candidate_unverified'), NICHT mehr stilles Verschwinden",
      "wartung_candidate_unverified" in _gruende(abgelehnt_c), str(abgelehnt_c))

print("\n" + "=" * 78)
print("D) 2 unabhaengige Secondary-Families, gleicher Wert -> CORROBORATED, nicht VERIFIED")
print("=" * 78)
sec_a = treffer("https://www.repareo.de/a", "Lexus RC F Wartung A",
                "Das Motoroel wird beim RC F alle 15.000 km gewechselt.")
sec_b = treffer("https://www.autoteile-xyz.de/b", "Lexus RC F Wartung B",
                "Der Oelwechsel erfolgt beim RC F alle 15.000 km.")
fakten_d, abgelehnt_d = _wartung_candidate_fakten(
    [sec_a, sec_b], marke="Lexus", modell="RC F", generation=None, motor=None)
check("D) zwei Secondary-Families, gleicher Wert -> KEIN harter Fakt",
      len(fakten_d) == 0)
check("D) Agreement sichtbar als 'wartung_corroborated_not_verified'",
      "wartung_corroborated_not_verified" in _gruende(abgelehnt_d), str(abgelehnt_d))

print("\n" + "=" * 78)
print("E) 2 Secondary-Families, UNTERSCHIEDLICHE Werte -> CONFLICT")
print("=" * 78)
sec_konflikt = treffer("https://www.autoteile-xyz.de/c", "Lexus RC F Wartung C",
                       "Das Motoroel wird beim RC F alle 10.000 km gewechselt.")
fakten_e, abgelehnt_e = _wartung_candidate_fakten(
    [sec_a, sec_konflikt], marke="Lexus", modell="RC F", generation=None, motor=None)
check("E) widersprechende Werte (15.000 vs. 10.000 km) -> KEIN harter Fakt",
      len(fakten_e) == 0)
check("E) Konflikt sichtbar als 'wartung_candidate_conflict'",
      "wartung_candidate_conflict" in _gruende(abgelehnt_e), str(abgelehnt_e))

print("\n" + "=" * 78)
print("F) 'Motoroel pruefen' vs. 'Motoroel wechseln' -> unterschiedliche Action, "
     "keine falsche Übereinstimmung")
print("=" * 78)
pruefen = treffer("https://www.repareo.de/d", "Lexus RC F Wartung D",
                  "Das Motoroel wird beim RC F alle 15.000 km geprueft.")
wechseln = treffer("https://www.autoteile-xyz.de/e", "Lexus RC F Wartung E",
                   "Das Motoroel wird beim RC F alle 15.000 km gewechselt.")
fakten_f, abgelehnt_f = _wartung_candidate_fakten(
    [pruefen, wechseln], marke="Lexus", modell="RC F", generation=None, motor=None)
check("F) 'pruefen' und 'wechseln' bilden GETRENNTE Signatur-Gruppen -- keine "
      "fälschliche CORROBORATED-Übereinstimmung zwischen verschiedenen Aktionen",
      "wartung_corroborated_not_verified" not in _gruende(abgelehnt_f), str(abgelehnt_f))
check("F) stattdessen je 1 einzelne, unverifizierte Gruppe",
      _gruende(abgelehnt_f) == {"wartung_candidate_unverified"}, str(abgelehnt_f))

print("\n" + "=" * 78)
print("G) km + Zeitintervall im selben Claim -- beide Werte bleiben erhalten")
print("=" * 78)
check("G) 'alle 15.000 km oder 12 Monate' -> BEIDE Werte in der Signatur enthalten",
      _wartung_merkmal_normalisiert("Das Motoroel wird alle 15.000 km oder 12 Monate gewechselt.")
      == "12monate+15000km")
check("G) Reihenfolge im Satz aendert die Signatur NICHT (deterministisch sortiert)",
      _wartung_merkmal_normalisiert("Alle 12 Monate oder 15.000 km wird das Motoroel gewechselt.")
      == "12monate+15000km")
check("G) einzelnes Intervall bleibt UNVERAENDERT identisch zu vorher (Rueckwaertskompatibilitaet)",
      _wartung_merkmal_normalisiert("Das Motoroel wird alle 15.000 km gewechselt.") == "15000km")

print("\n" + "=" * 78)
print("H) Intervall vorhanden, aber KEIN Bauteil erkennbar -> expliziter Reject 'no_component'")
print("=" * 78)
ohne_bauteil = treffer("https://www.repareo.de/f", "Lexus RC F Service",
                       "Beim RC F wird etwas alle 15.000 km gemacht.")
fakten_h, abgelehnt_h = _wartung_candidate_fakten(
    [ohne_bauteil], marke="Lexus", modell="RC F", generation=None, motor=None)
check("H) kein Fakt ohne erkennbares Bauteil", len(fakten_h) == 0)
check("H) explizit als 'no_component' geloggt (vorher: STILLES Verschwinden, kein Eintrag)",
      "no_component" in _gruende(abgelehnt_h), str(abgelehnt_h))

print("\n" + "=" * 78)
print("Scope-Isolierung: Schwachstelle unveraendert (kein neues Motoroel-Vokabular dort)")
print("=" * 78)
from app.technical_research import _extrahiere_fakten
schwach_treffer = treffer("https://www.adac.de/lexus-rc-f-oel", "Lexus RC F Mängel",
                          "Das Motoroel wird beim RC F alle 15.000 km gewechselt.")
abgelehnt_sw: list[dict] = []
fakten_sw = _extrahiere_fakten([schwach_treffer], "schwachstelle", marke="Lexus", modell="RC F",
                               abgelehnt=abgelehnt_sw)
check("Scope: dieselbe Motoroel-Aussage wird fuer 'schwachstelle' NICHT als Fakt akzeptiert "
      "(Vokabular-Erweiterung betrifft nur 'wartung'; der exakte Reject-Grund ist hier "
      "zweitrangig -- entscheidend ist, dass KEIN Fakt mit Bauteil 'Motoroel'/'Oelwechsel' "
      "ueber den neuen Wartungs-Vokabular-Eintrag entsteht)",
      len(fakten_sw) == 0, f"fakten={fakten_sw} abgelehnt={abgelehnt_sw}")


print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE WARTUNGS-MOTOROEL-VOKABULAR-TESTS GRUEN")
