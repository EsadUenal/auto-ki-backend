"""
Technische Spec-Extraction: generische Schreibvarianten (Package C,
Forensik-Folgeauftrag).

Production-Befund (Lexus RC F): "477 PS"/"Heckantrieb" wurden web-bestaetigt,
"5.0 V8"/"Benzin"/"Automatik" blieben user_only -- als Vokabular-/
Extraktionsluecke diagnostiziert (Abnahmepunkt 7), NICHT als Claim-Binding-
oder Konsens-Bug. Dieser Test deckt NUR die generischen, in der Forensik
explizit angeforderten Schreibvarianten ab -- keine Fahrzeug-/Marken-Hardcodes.

Backend-Folgeauftrag (reproduziert + behoben): die "Automatik"-Vokabularluecke
betraf konkret ausgeschriebene deutsche Zahlwoerter ("Achtstufen-Automatik")
und ein zwischen Stufenangabe und "Automatik" eingeschobenes Markenwort
("Achtstufen-SPORT-Automatik") -- belegt am echten Herstellertext
toyota-media.de (01.12.2016) zu Lexus RC F/GS F. Kraftstoff/Antrieb waren bei
genauerer Pruefung KEINE Vokabularluecke (siehe app/technical_research.py,
_KRAFTSTOFF_CLAIM/_ANTRIEB_CLAIM) -- ein einzelner Treffer bestaetigt ein Feld
nur bei ausreichendem Konsens-Gewicht, das ist Trust-System, keine Luecke.

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

print("\n=== Getriebe (Automatik): ausgeschriebene Zahlwoerter + eingeschobenes "
      "Markenwort (Backend-Folgeauftrag, real belegt: toyota-media.de 01.12.2016, "
      "'Achtstufen-Sport-Automatik' fuer Lexus RC F/GS F) ===")
for text in ("Achtstufen-Automatik", "Achtstufen-Sport-Automatik",
            "8-Stufen-Sport-Automatik", "Siebenstufen-Automatik",
            "Siebengang-Automatik", "Neunstufen-Automatik", "Sechsgang-Automatik",
            "Zehnstufen-Automatik", "Vierstufen-Automatik",
            "an eine Achtstufen-Sport-Automatik gekoppelt"):
    check(f"Automatik erkannt in {text!r}", bool(_RE_GETRIEBE_AUTO.search(text)))

print("\n=== Getriebe: kein Fehlalarm durch die neuen Zahlwort-/Zusatzwort-Muster ===")
for text in ("Achtstufen-Schaltgetriebe", "Achtzylinder", "Siebensitzer"):
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
