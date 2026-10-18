"""
Technische Spec-Extraction: generische Schreibvarianten (Package C,
Forensik-Folgeauftrag).

Production-Befund (Lexus RC F): "477 PS"/"Heckantrieb" wurden web-bestaetigt,
"5.0 V8"/"Benzin"/"Automatik" blieben user_only -- als Vokabular-/
Extraktionsluecke diagnostiziert (Abnahmepunkt 7), NICHT als Claim-Binding-
oder Konsens-Bug. Dieser Test deckt NUR die generischen, in der Forensik
explizit angeforderten Schreibvarianten ab -- keine Fahrzeug-/Marken-Hardcodes.

    python test_spec_extraction_vokabular.py
"""
from app.technical_research import (
    _KRAFTSTOFF_CLAIM, _RE_GETRIEBE_AUTO, _RE_GETRIEBE_MANUELL, _RE_HUBRAUM,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


print("=== Hubraum ===")
for text, erwartet in (
    ("5.0 L", "5.0"), ("5,0 L", "5,0"), ("5.0-liter", "5.0"),
    ("5.0 litre", "5.0"), ("5,0-Liter", "5,0"), ("5.0-liter V8", "5.0"),
):
    m = _RE_HUBRAUM.search(text)
    check(f"Hubraum erkannt in {text!r}", bool(m) and m.group(1) == erwartet)

print("\n=== Kraftstoff ===")
for text, erwartet in (("petrol engine", "benzin"), ("gasoline engine", "benzin"),
                       ("Dieselmotor", "diesel")):
    treffer = [wert for wert, rx in _KRAFTSTOFF_CLAIM if rx.search(text)]
    check(f"Kraftstoff {erwartet!r} erkannt in {text!r}", treffer == [erwartet])

print("\n=== Getriebe (Automatik) ===")
for text in ("8-Stufen-Automatik", "Automatikgetriebe", "automatische 8-Gang",
            "8-speed automatic", "8-speed automatic transmission",
            "automatic transmission"):
    check(f"Automatik erkannt in {text!r}", bool(_RE_GETRIEBE_AUTO.search(text)))

print("\n=== Getriebe: bare 'X-speed' OHNE 'automatic' wird NICHT geclaimt "
      "(Package-C-Vorgabe) ===")
for text in ("8-speed", "6-speed manual gearbox"):
    check(f"'{text}' loest KEINEN Automatik-Claim aus", not _RE_GETRIEBE_AUTO.search(text))

print("\n=== Getriebe (Manuell) bleibt unveraendert funktionsfaehig ===")
check("6-Gang-Schaltgetriebe weiterhin erkannt",
      bool(_RE_GETRIEBE_MANUELL.search("6-Gang-Schaltgetriebe")))

print()
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE SPEC-EXTRACTION-VOKABULAR-TESTS GRUEN")
