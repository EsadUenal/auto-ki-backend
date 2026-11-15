"""
N6/N17 -- Motorkern-Prüfung: ausdrücklich genannte Hubräume in cm³ zählen wie die entsprechende Literangabe.

Befund (Replay des aufgezeichneten Autozeitung-Treffers, tests/fixtures/live_rollenbeleg_treffer_2026-11-08.json):
der Satz "... V8-Zylinder, 4-Ventiler; 4969 cm³; 351 kW/477 PS ..." (Tier 2, kein Verwurf) nennt Bauart UND Hubraum, zählte aber
nicht als Motorkern-Beleg, weil die Prüfung nur Literangaben las, die Hubraum-CLAIMS dagegen cm³ umrechnen.

Korrektur: `_hubraum_werte_litergleich` (dieselbe cm³-Erkennung/-Umrechnung wie die Claims). Unverändert: Domainschwelle, Quellen-
qualifikation (Markt/Zeit), Satzgrenzen, Mehrdeutigkeit, Kraftstoff/Elektrifizierung. Dieser Test belegt das Verhalten an
AUFGEZEICHNETEN Texten; er sagt nichts über den Lauf vom 14.11. (Originaldaten liegen lokal nicht vor).

    python scripts/test_isolated.py test_n6_n17_motorkern_ccm.py
"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.technical_research import (_ziel, _hubraum_liter_werte, _hubraum_werte_litergleich,  # noqa: E402
                                    _qualifizierte_quelle, werte_identitaet_aus)

FAILS: list[str] = []


def check(name, ok, detail=""):
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if (detail and not ok) else ""))
    if not ok:
        FAILS.append(name)


def t(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


RC = "5.0 V8, Heckantrieb"
FX = json.load(open(Path(__file__).parent / "tests/fixtures/live_rollenbeleg_treffer_2026-11-08.json", encoding="utf-8"))
_az = FX["treffer"][0]["hits"][1]
AUTOZEITUNG = t(_az["url"], _az["title"], _az["content"])
_ad = FX["treffer"][0]["hits"][2]
ADAC_252101 = t(_ad["url"], _ad["title"], _ad["content"])
_ad2 = FX["treffer"][1]["hits"][1]
ADAC_ULTIMATE = t(_ad2["url"], _ad2["title"], _ad2["content"])      # Datenblatt 08/24 - 06/25


def synth(satz, host="www.auto-motor-und-sport.de", titel="Lexus RC F Test"):
    return t(f"https://{host}/synthetisch/rcf", titel, satz)


def kern(motor, quellen, baujahr=2017, modell="RC F"):
    w, _ = werte_identitaet_aus(quellen, _ziel("Lexus", modell, baujahr, motor, None))
    return w


def beleg(w):
    return w.motor is not None, bool((w.feldwerte.get("engine_name") or {}).get("kern_vollstaendig"))


print("== A: Hilfsfunktion (gleiche Erkennung wie die Claims) ==")
S_CCM = "Der RC F hat einen V8 mit 4969 cm³ Hubraum und 477 PS."
check("A1 Literangabe unverändert erkannt", _hubraum_werte_litergleich("5.0-Liter-V8 mit 477 PS") == [("5.0", 0)])
check("A2 cm³ -> Literwert, wie die Hubraum-Claims", [w for w, _ in _hubraum_werte_litergleich(S_CCM)] == ["5.0"])
check("A3 die Literfunktion selbst ist unverändert (kein cm³)", _hubraum_liter_werte(S_CCM) == [])
check("A4 'ccm' und 'cm3' werden erkannt", [w for w, _ in _hubraum_werte_litergleich("V8, 4969 ccm")] == ["5.0"]
      and [w for w, _ in _hubraum_werte_litergleich("V8, 4969 cm3")] == ["5.0"])
check("A5 Nicht-Hubraum bleibt ausgeschlossen (Verbrauch l/100 km, Füllmenge)",
      _hubraum_werte_litergleich("Verbrauch 11,8 l/100 km") == [])

print("\n== B: aufgezeichneter Autozeitung-Text (Regression) ==")
az_q = _qualifizierte_quelle(AUTOZEITUNG, _ziel("Lexus", "RC F", 2017, RC, None))
check("B1 Autozeitung ist qualifiziert (Tier 2, kein Datenblatt-Verwurf)", az_q["tier"] == 2 and not az_q["verwurf_grund_datenblatt"])
sat = [s for s in az_q["saetze"] if "V8-Zylinder" in s]
check("B2 der cm³-Satz mit V8 liefert jetzt den Hubraum 5.0", len(sat) == 1
      and [w for w, _ in _hubraum_werte_litergleich(sat[0])] == ["5.0"] and _hubraum_liter_werte(sat[0]) == [])
w = kern(RC, [AUTOZEITUNG])
check("B3 EINE qualifizierte Domain bestätigt den Motorkern weiterhin NICHT (Schwelle unverändert)", w.motor is None, repr(w.motor))

print("\n== C: Beitrag zur Bestätigung (zweite qualifizierte Domain, synthetisch) ==")
ZWEI = [AUTOZEITUNG, synth("Der Lexus RC F wird von einem V8 mit 4969 cm³ Hubraum angetrieben.")]
w = kern(RC, ZWEI)
check("C1 zwei Domains, beide mit V8 + cm³ im selben Satz: Motorkern bestätigt", beleg(w) == (True, True), str(beleg(w)))
LIT = [AUTOZEITUNG, synth("Der Lexus RC F wird von einem 5,0 Liter V8 angetrieben.")]
w = kern(RC, LIT)
check("C2 Autozeitung (cm³) + Quelle mit Literangabe: Motorkern bestätigt (gleichwertig)", beleg(w) == (True, True), str(beleg(w)))
LIT2 = [synth("Der Lexus RC F hat einen 5,0 Liter V8 mit 477 PS."),
        synth("Der Lexus RC F fährt mit einem 5,0-Liter-V8.", host="www.autobild.de")]
check("C3 reine Literangaben verhalten sich unverändert (bestätigt)", beleg(kern(RC, LIT2)) == (True, True))
CCM2 = [synth("Der Lexus RC F hat einen V8 mit 4969 cm³ und 477 PS."),
        synth("Der Lexus RC F fährt mit einem V8 (4969 ccm).", host="www.autobild.de")]
check("C3b reine cm³-Angaben aus zwei Domains: bestätigt", beleg(kern(RC, CCM2)) == (True, True))
G = [synth("Der RC F ist ein V8-Sportcoupe. Seine Maschine hat 4969 cm³ Hubraum."),
     synth("Der RC F ist ein V8-Sportcoupe. Seine Maschine hat 4969 cm³ Hubraum.", host="www.autobild.de")]
check("C4 Bauart und cm³-Hubraum in getrennten Sätzen zählen NICHT", kern(RC, G).motor is None)
FALSCH = [synth("Der Lexus RC F hat einen V8 mit 3456 cm³ und 400 PS."),
          synth("Der Lexus RC F fährt mit einem V8 (3456 ccm).", host="www.autobild.de")]
check("C5 falscher cm³-Hubraum zählt nicht (Ziel 5.0)", kern(RC, FALSCH).motor is None)
check("C5b ... auch bei Ziel 3.5 zählt der 5.0er-Beleg nicht", kern("3.5 V8, Heckantrieb", ZWEI).motor is None)
MEHR = [synth("Der RC F hat wahlweise einen V8 mit 4969 cm³ oder einen V8 mit 3456 cm³."),
        synth("Der RC F hat wahlweise einen V8 mit 4969 ccm oder einen V8 mit 3456 ccm.", host="www.autobild.de")]
check("C6 zusätzlicher abweichender Hubraum im selben Satz bleibt mehrdeutig -> nicht bestätigt", kern(RC, MEHR).motor is None)
MEHR2 = [synth("Der RC F hat einen 5,0 Liter V8 mit 3456 cm³ Hubraum."),
         synth("Der RC F hat einen 5,0 Liter V8 mit 3456 ccm Hubraum.", host="www.autobild.de")]
check("C6b Liter 5.0 + abweichende cm³ (3.5) im selben Satz: mehrdeutig -> nicht bestätigt", kern(RC, MEHR2).motor is None)
GLEICH = [synth("Der RC F hat einen 5,0 Liter V8 (4969 cm³)."),
          synth("Der RC F hat einen 5,0 Liter V8 (4969 ccm).", host="www.autobild.de")]
check("C7 gleichwertige Liter-/cm³-Angaben im selben Satz widersprechen sich nicht -> bestätigt",
      beleg(kern(RC, GLEICH)) == (True, True), str(beleg(kern(RC, GLEICH))))

print("\n== D: Markt/Zeit-Ausschluss bleibt ==")
ausser = synth("Lexus RC F Datenblatt Modellstart 01/18 Modellende 03/19. Der RC F hat einen V8 mit 4969 cm³ Hubraum.")
ausser2 = synth("Lexus RC F Datenblatt Modellstart 01/18 Modellende 03/19. Der RC F hat einen V8 mit 4969 ccm Hubraum.",
                host="www.autobild.de")
check("D1 Datenblatt-Fenster nach Baujahr 2017: cm³-Beleg wird nicht verwendet", kern(RC, [ausser, ausser2]).motor is None)
inner = synth("Lexus RC F Datenblatt Modellstart 01/16 Modellende 12/17. Der RC F hat einen V8 mit 4969 cm³ Hubraum.")
inner2 = synth("Lexus RC F Datenblatt Modellstart 01/16 Modellende 12/17. Der RC F hat einen V8 mit 4969 ccm Hubraum.",
               host="www.autobild.de")
check("D1b Positivkontrolle: Fenster umfasst 2017 -> bestätigt", beleg(kern(RC, [inner, inner2])) == (True, True))
_ad3 = FX["treffer"][1]["hits"][2]
ADAC_LC = t(_ad3["url"], _ad3["title"], _ad3["content"])             # LC Coupé 500, 05/23 - 03/25
check("D2 aufgezeichnete ADAC-Datenblätter außerhalb 2017 (RC F Ultimate Edition 08/24-06/25, LC 05/23-03/25) bleiben ausgeschlossen",
      all(_qualifizierte_quelle(q, _ziel("Lexus", "RC F", 2017, RC, None))["verwurf_grund_datenblatt"]
          for q in (ADAC_ULTIMATE, ADAC_LC)))
check("D3 ... und auch zusammen mit dem RC-200t-Datenblatt (anderes Modell, 01/16-05/18) tragen sie nichts zur Bestätigung bei", kern(RC, [AUTOZEITUNG, ADAC_252101, ADAC_ULTIMATE, ADAC_LC]).motor is None)
check("D4 fremdes Modell (IS F): nichts bestätigt", kern(RC, ZWEI, modell="IS F").motor is None)

print("\n== E: andere Felder unberührt ==")
w = kern(RC, ZWEI)
check("E1 Kraftstoff und Elektrifizierung nicht aus Motorkern/Hubraum abgeleitet",
      "fuel" not in w.feldwerte and "powertrain" not in w.feldwerte and getattr(w, "kraftstoff", None) is None
      and getattr(w, "powertrain", None) is None, str(list(w.feldwerte)))
check("E2 'V-Motor' + '8 Zylinder' (getrennte Zellen) wird NICHT zu V8 zusammengesetzt",
      kern(RC, [t("https://www.auto-data.net/de/x", "Lexus RC F", "Motorkonfiguration V-Motor Anzahl der Zylinder 8 Hubraum 4969 cm3."),
                synth("Motorkonfiguration V-Motor Anzahl der Zylinder 8 Hubraum 4969 cm3.", host="www.autobild.de")]).motor is None)

print("\n" + ("ALLE GRÜN" if not FAILS else f"{len(FAILS)} FEHLER: {FAILS}"))
sys.exit(1 if FAILS else 0)
