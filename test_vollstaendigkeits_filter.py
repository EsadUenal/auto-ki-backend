"""
PASS-GATE-Folgeauftrag §E: deterministischer Post-Generation-Filter gegen
Vollständigkeits-/Evidenzbehauptungen, die der tatsächliche Research-State
nicht trägt.

Production-Befund (Lexus RC F): die UI zeigte weiterhin "Der technische
KaufCheck und die Prüflisten sind davon unabhängig vollständig.", obwohl
Weakness/Wartung 0 Fakten lieferten. Code-Audit (diese Runde) ergab: dieser
exakte Satz existiert in KEINER bestehenden Backend-Textquelle
(app/kaufcheck_bericht.py verwendet laut eigenem Moduldocstring bewusst KEINE
freie LLM-Prosa als Ausgabekanal; `no_market_prompt_block()` ist nur eine
PROMPT-Anweisung an Gemini, deren generierter "bericht"-Text in
app/kaufcheck.py vor der Antwort durch den hier getesteten, deterministischen
`bericht()` vollständig ÜBERSCHRIEBEN wird -- Gemini-Freitext erreicht die
Antwort nie). Der Filter hier ist deshalb eine GENERISCHE, deterministische
Absicherung (kein einzelnes String-Replacement, kein Hardcode auf DIESEN
einen Satz) für JEDEN künftigen Text in diesem Modul, nicht eine Reparatur
des konkreten Produktionsfunds selbst (der sitzt vermutlich im Frontend,
siehe Abschlussbericht).

GENERISCH getestet, kein Fahrzeug-/Marken-Hardcode. KEIN Netzwerk.

    python test_vollstaendigkeits_filter.py
"""
from app.kaufcheck_bericht import (
    _forschung_tatsaechlich_unvollstaendig,
    _ohne_unbelegte_vollstaendigkeitsbehauptung,
)
from app.technical_research import PHASE_FAILED, PHASE_PARTIAL, PHASE_SUCCESS
from app.models import TechnischeRecherche, WebFakt

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


print("=== 1) Der konkrete Produktionsfund wird satzweise entfernt, wenn der "
      "Research-State widerspricht ===")
PRODUKTIONSFUND = ("Der technische KaufCheck und die Prüflisten sind davon unabhängig "
                   "vollständig. Das Fahrzeug wirkt gepflegt.")
check("1a bei unvollstaendig=True wird NUR der Vollstaendigkeitssatz entfernt",
      _ohne_unbelegte_vollstaendigkeitsbehauptung(PRODUKTIONSFUND, unvollstaendig=True)
      == "Das Fahrzeug wirkt gepflegt.")
check("1b bei unvollstaendig=False bleibt der Text unveraendert (kein Grund zum Eingriff)",
      _ohne_unbelegte_vollstaendigkeitsbehauptung(PRODUKTIONSFUND, unvollstaendig=False)
      == PRODUKTIONSFUND)

print("\n=== 2) Generisches Satzmuster, kein fester String ===")
check("2a 'umfassend durchgefuehrt' wird ebenfalls erkannt (Verb-Variante)",
      _ohne_unbelegte_vollstaendigkeitsbehauptung(
          "Die Pruefung wurde umfassend durchgefuehrt.", unvollstaendig=True) is None)
check("2b 'komplett analysiert' wird erkannt (anderes Vollstaendigkeitswort)",
      _ohne_unbelegte_vollstaendigkeitsbehauptung(
          "Das Fahrzeug wurde komplett analysiert.", unvollstaendig=True) is None)
check("2c 'Recherche war lueckenlos' wird erkannt (Nomen-Variante)",
      _ohne_unbelegte_vollstaendigkeitsbehauptung(
          "Die Recherche war lueckenlos.", unvollstaendig=True) is None)

print("\n=== 3) Praezision: Vollstaendigkeit UEBER DAS FAHRZEUG (nicht ueber den "
      "Check) bleibt unangetastet ===")
check("3a 'Fahrzeug wurde vollstaendig gewartet' betrifft die Wartung des Autos, "
      "NICHT den Pruefprozess -- bleibt stehen",
      _ohne_unbelegte_vollstaendigkeitsbehauptung(
          "Das Fahrzeug wurde laut Inserat vollstaendig gewartet.", unvollstaendig=True)
      == "Das Fahrzeug wurde laut Inserat vollstaendig gewartet.")
check("3b Satz ganz ohne Vollstaendigkeitswort bleibt unangetastet",
      _ohne_unbelegte_vollstaendigkeitsbehauptung(
          "Das Fahrzeug hat einen guten Allgemeinzustand.", unvollstaendig=True)
      == "Das Fahrzeug hat einen guten Allgemeinzustand.")

print("\n=== 4) Mehrsatz-Text: nur der betroffene Satz fliegt raus ===")
mehrsatz = "Nicht sicher belegt: Generation. Der KaufCheck wurde dennoch vollstaendig abgeschlossen."
check("4a nur der zweite Satz wird entfernt",
      _ohne_unbelegte_vollstaendigkeitsbehauptung(mehrsatz, unvollstaendig=True)
      == "Nicht sicher belegt: Generation.")

print("\n=== 5) leer/None robust ===")
check("5a None bleibt None", _ohne_unbelegte_vollstaendigkeitsbehauptung(None, unvollstaendig=True) is None)
check("5b Leerstring bleibt Leerstring",
      _ohne_unbelegte_vollstaendigkeitsbehauptung("", unvollstaendig=True) == "")
check("5c Text, der NACH Filterung komplett leer waere, wird zu None (kein Leerstring-Bullet)",
      _ohne_unbelegte_vollstaendigkeitsbehauptung(
          "Die Analyse war vollstaendig und abgeschlossen.", unvollstaendig=True) is None)

print("\n=== 6) _forschung_tatsaechlich_unvollstaendig(): generischer IST-Zustand ===")
check("6a None -> False (kein Web-Fallback, keine Aussage moeglich)",
      _forschung_tatsaechlich_unvollstaendig(None) is False)
check("6b Technik-Phase FAILED -> True",
      _forschung_tatsaechlich_unvollstaendig(
          TechnischeRecherche(phasen_status={"technik": PHASE_FAILED})) is True)
check("6c Technik-Phase SUCCESS, aber 0 Fakten fuer schwachstelle/wartung -> True "
      "(exakt der Produktionsfall)",
      _forschung_tatsaechlich_unvollstaendig(
          TechnischeRecherche(phasen_status={"technik": PHASE_SUCCESS}, fakten=[])) is True)
check("6d Technik-Phase PARTIAL, aber mind. 1 Fakt (schwachstelle) vorhanden -> False",
      _forschung_tatsaechlich_unvollstaendig(
          TechnischeRecherche(phasen_status={"technik": PHASE_PARTIAL},
                              fakten=[WebFakt(kategorie="schwachstelle", aussage="x",
                                              bauteil="y")])) is False)
check("6e Technik-Phase NOT_RUN (kein Web-Fallback geschah) -> False",
      _forschung_tatsaechlich_unvollstaendig(TechnischeRecherche(phasen_status={})) is False)

print()
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE VOLLSTAENDIGKEITS-FILTER-TESTS GRUEN")
