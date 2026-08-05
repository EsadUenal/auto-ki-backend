"""
Test: Report-Validator (Reliability-Sprint 4, §Phase 8).

Deterministisch, kein Netzwerk/LLM.

Ausfuehren:  python test_report_validator.py
"""
import sys
sys.path.insert(0, ".")

from app.report_validator import pruefe_bericht   # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


AUSGESCHLOSSEN = [{
    "id": "rk-1", "mangel": "Brandgefahr der Hochvoltbatterie",
    "abhilfe": "Austausch der Hochvoltbatterie-Module",
}]

# 1: Prosa-Absatz mit EINEM verbotenen Satz -> nur der Satz raus, Rest bleibt.
bericht1 = ("## Kritische Risiken\n"
            "Der Zahnriemenwechsel ist überfällig. Die Hochvoltbatterie kann laut Rückruf "
            "Brandgefahr aufweisen. Prüfe außerdem die Bremsen vor dem Kauf.")
bereinigt1, warn1 = pruefe_bericht(bericht1, AUSGESCHLOSSEN)
check("1: 'Hochvoltbatterie'-Satz entfernt", "Hochvoltbatterie" not in bereinigt1)
check("1b: Zahnriemen-Satz bleibt erhalten", "Zahnriemenwechsel" in bereinigt1)
check("1c: Bremsen-Satz bleibt erhalten", "Bremsen" in bereinigt1)
check("1d: eine Warnung geloggt", len(warn1) == 1)

# 2: Checklisten-Bullet mit verbotenem Begriff -> ganze Zeile raus, andere Bullets bleiben.
bericht2 = ("## Besichtigungs-Checkliste\n"
            "- [ ] Ölstand prüfen\n"
            "- [ ] Hochvoltbatterie-Zustand dokumentieren lassen\n"
            "- [ ] Serviceheft einsehen")
bereinigt2, _ = pruefe_bericht(bericht2, AUSGESCHLOSSEN)
check("2: Hochvolt-Bullet komplett entfernt", "Hochvoltbatterie" not in bereinigt2)
check("2b: Ölstand-Bullet bleibt", "Ölstand prüfen" in bereinigt2)
check("2c: Serviceheft-Bullet bleibt", "Serviceheft einsehen" in bereinigt2)
check("2d: keine leere '- [ ] '-Restzeile", "- [ ] \n" not in bereinigt2 + "\n" and not any(
    l.strip() in ("-", "- [ ]", "*") for l in bereinigt2.split("\n")))

# 3: kein Treffer -> Bericht unverändert (Identität).
bericht3 = "Alles unauffällig. Kein Rückruf betrifft dieses Fahrzeug."
bereinigt3, warn3 = pruefe_bericht(bericht3, AUSGESCHLOSSEN)
check("3: unveränderter Bericht ohne Treffer", bereinigt3 == bericht3 and warn3 == [])

# 4: keine ausgeschlossenen Rückrufe -> No-Op.
bereinigt4, warn4 = pruefe_bericht(bericht1, [])
check("4: leere ausgeschlossene Liste -> No-Op", bereinigt4 == bericht1 and warn4 == [])

# 5: leerer Bericht -> No-Op, kein Crash.
bereinigt5, warn5 = pruefe_bericht("", AUSGESCHLOSSEN)
check("5: leerer Bericht -> No-Op", bereinigt5 == "" and warn5 == [])

# 6: Live-Regression (Reliability-Sprint 4) — BMW 320d G20: drei KBA-Rückrufe,
# einer davon Hochvolt/PHEV (ausgeschlossen), ZWEI weitere allgemeine (erlaubt).
# Der generische 'Prüfung und ggf. Austausch des/der X'-Abhilfe-Baustein ist
# IDENTISCH in allen drei `abhilfe`-Texten -> darf NICHT dazu führen, dass die
# legitimen Prüf-/FIN-Empfehlungssätze der ERLAUBTEN Rückrufe entfernt werden.
AUSGESCHLOSSEN_HV = [{
    "mangel": "Brandgefahr der Hochvoltbatterie",
    "abhilfe": "Prüfung und ggf. Austausch der Hochvoltbatterie-Module",
}]
ERLAUBT_ALLGEMEIN = [
    {"mangel": "Möglicher Ausfall des Bremskraftverstärkers",
     "abhilfe": "Prüfung und ggf. Austausch des Bremskraftverstärkers"},
    {"mangel": "Mangelhafte Schweißnähte an der Lenkung",
     "abhilfe": "Prüfung und ggf. Austausch der Lenkung"},
]
bericht6 = (
    "## Kritische Risiken\n"
    "Das Fahrzeug ist grundsätzlich interessant, jedoch sind zwei KBA-Rückrufe relevant, "
    "die eine Überprüfung erfordern.\n"
    "**NUR MIT WERKSTATTPRÜFUNG** kaufen.\n"
    "Eine detaillierte Werkstattprüfung ist daher vor dem Kauf unerlässlich.\n"
    "Eine Prüfung anhand der Fahrgestellnummer (FIN) ist zwingend erforderlich, um "
    "festzustellen, ob das Fahrzeug betroffen ist."
)
bereinigt6, warn6 = pruefe_bericht(bericht6, AUSGESCHLOSSEN_HV, ERLAUBT_ALLGEMEIN)
check("6: legitime Prüf-/FIN-Sätze bleiben erhalten (kein 'Prüfung'-Fehltreffer)",
      bereinigt6 == bericht6 and warn6 == [])

# 6b: Gegenprobe — enthält der Bericht wirklich 'Hochvoltbatterie', wird das
# weiterhin korrekt entfernt (die Differenzmenge entschärft nur ECHT geteilte
# generische Begriffe, nicht die tatsächlich spezifischen).
bericht6b = bericht6 + " Die Hochvoltbatterie sollte separat begutachtet werden."
bereinigt6b, warn6b = pruefe_bericht(bericht6b, AUSGESCHLOSSEN_HV, ERLAUBT_ALLGEMEIN)
check("6b: 'Hochvoltbatterie'-Satz weiterhin entfernt trotz Differenzmenge",
      "Hochvoltbatterie" not in bereinigt6b)

print()
if FEHLER:
    print(f"{len(FEHLER)} Test(s) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle Report-Validator-Tests bestanden.")
