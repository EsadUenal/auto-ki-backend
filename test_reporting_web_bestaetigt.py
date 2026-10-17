"""
Reporting-Hardening (Lexus-RC-F-Forensik, Folgeauftrag): generische Fehlerklasse
"ein Satz behauptet Web-Bestätigung, zeigt aber unbestätigte Nutzerangaben mit".

BEFUND (realer Production-Satz): "Fahrzeugidentität über Webquellen
plausibilisiert: Lexus RC F 5.0 V8, Heckantrieb 477 PS" — Lexus/RC F/477 PS
waren web-bestätigt, "5.0 V8, Heckantrieb" stammte ausschließlich aus der
Nutzereingabe (`verification_state == "user_only"`). `fahrzeug_titel()`
(app/anzeige.py) nahm jedes gesetzte Feld blind auf, unabhängig vom
Evidence-State.

FIX: `fahrzeug_titel(..., nur_web_bestaetigt=True)` + `_web_bestaetigt()` —
EIN generischer, feldunabhängiger Mechanismus (make/model/model_variant/
generation/year/engine_name/engine_code/horsepower gleich behandelt). Die
NORMALE Fahrzeugüberschrift (Default `nur_web_bestaetigt=False`) bleibt
unverändert und zeigt weiterhin klar herkunftsgekennzeichnete Nutzerangaben.

    python test_reporting_web_bestaetigt.py
"""
from app.anzeige import _web_bestaetigt, fahrzeug_titel
from app.vehicle_identity import VehicleIdentity

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def identity_lexus_rc_f() -> VehicleIdentity:
    """Reproduziert exakt den realen Production-Fall: Marke/Modell/PS
    web-bestätigt (zwei_domain-Pfad), Motor/Antrieb nur Nutzerangabe."""
    ident = VehicleIdentity(make="Lexus", model="RC F", horsepower=477,
                            engine_name="5.0 V8, Heckantrieb")
    ident.field_evidence = {
        "make": {"verification_state": "user_only", "confirmed_by": ["web"]},
        "model": {"verification_state": "user_only", "confirmed_by": ["web"]},
        "horsepower": {"verification_state": "web_only", "confirmed_by": []},
        "engine_name": {"verification_state": "user_only", "confirmed_by": []},
    }
    return ident


print("=" * 78)
print("A) make/model/PS web-bestätigt, engine_name user_only")
print("=" * 78)

ident_a = identity_lexus_rc_f()
titel_a_web = fahrzeug_titel(ident_a, mit_jahr=False, nur_web_bestaetigt=True)
check("A) Web-Plausibilisierungs-Titel enthält Lexus/RC F/477 PS",
      "Lexus" in titel_a_web and "RC" in titel_a_web and "F" in titel_a_web and "477" in titel_a_web,
      titel_a_web)
check("A) Web-Plausibilisierungs-Titel enthält 'engine_name' (5.0/V8/Heckantrieb) NICHT",
      "5.0" not in titel_a_web and "V8" not in titel_a_web and "Heckantrieb" not in titel_a_web,
      titel_a_web)

print("\n" + "=" * 78)
print("B) engine_name selbst web-bestätigt -> darf erscheinen")
print("=" * 78)

ident_b = identity_lexus_rc_f()
ident_b.field_evidence["engine_name"] = {"verification_state": "web_only", "confirmed_by": []}
titel_b_web = fahrzeug_titel(ident_b, mit_jahr=False, nur_web_bestaetigt=True)
check("B) Web-Plausibilisierungs-Titel enthält jetzt den (jetzt web-bestätigten) Motor",
      "5.0" in titel_b_web and "V8" in titel_b_web, titel_b_web)

print("\n" + "=" * 78)
print("C) generation user_only/unknown -> nicht als webbestätigt ausgeben")
print("=" * 78)

ident_c1 = identity_lexus_rc_f()
ident_c1.generation = "ND"
ident_c1.field_evidence["generation"] = {"verification_state": "user_only", "confirmed_by": []}
titel_c1 = fahrzeug_titel(ident_c1, mit_jahr=False, nur_web_bestaetigt=True)
check("C1) generation user_only erscheint NICHT im Web-Plausibilisierungs-Titel",
      "ND" not in titel_c1.split(), titel_c1)

ident_c2 = identity_lexus_rc_f()
ident_c2.generation = None  # unknown -- gar nicht gesetzt
titel_c2 = fahrzeug_titel(ident_c2, mit_jahr=False, nur_web_bestaetigt=True)
check("C2) generation unknown (None) beeinflusst den Titel nicht (kein Crash, kein Platzhalter)",
      "None" not in titel_c2, titel_c2)

print("\n" + "=" * 78)
print("D) normale UI-Fahrzeugdarstellung zeigt weiterhin klar deklarierte Nutzereingaben")
print("=" * 78)

ident_d = identity_lexus_rc_f()
titel_d_normal = fahrzeug_titel(ident_d, mit_jahr=False)  # Default: nur_web_bestaetigt=False
check("D) normale Überschrift (Default) zeigt weiterhin 5.0/V8/Heckantrieb",
      "5.0" in titel_d_normal and "V8" in titel_d_normal, titel_d_normal)
check("D) normale Überschrift zeigt weiterhin Lexus/RC F/477 PS",
      "Lexus" in titel_d_normal and "477" in titel_d_normal, titel_d_normal)

print("\n" + "=" * 78)
print("E) _web_bestaetigt() -- generischer Mechanismus, alle Felder gleich behandelt")
print("=" * 78)

for feld in ("make", "model", "model_variant", "generation", "year",
            "engine_name", "engine_code", "horsepower"):
    ident_e = VehicleIdentity()
    ident_e.field_evidence = {feld: {"verification_state": "web_only", "confirmed_by": []}}
    check(f"E) {feld}: state='web_only' -> _web_bestaetigt() True",
          _web_bestaetigt(ident_e, feld) is True)
    ident_e2 = VehicleIdentity()
    ident_e2.field_evidence = {feld: {"verification_state": "user_confirmed",
                                      "confirmed_by": ["web"]}}
    check(f"E) {feld}: 'web' in confirmed_by -> _web_bestaetigt() True",
          _web_bestaetigt(ident_e2, feld) is True)
    ident_e3 = VehicleIdentity()
    ident_e3.field_evidence = {feld: {"verification_state": "user_only", "confirmed_by": []}}
    check(f"E) {feld}: user_only, kein Web-Anteil -> _web_bestaetigt() False",
          _web_bestaetigt(ident_e3, feld) is False)
    ident_e4 = VehicleIdentity()  # Feld gar nicht in field_evidence
    check(f"E) {feld}: field_evidence fehlt komplett -> _web_bestaetigt() False (fail-closed)",
          _web_bestaetigt(ident_e4, feld) is False)

print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE REPORTING-WEB-BESTAETIGT-TESTS GRUEN")
