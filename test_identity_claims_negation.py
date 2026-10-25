"""
Negationssichere Identity-Claims (Backend-Folgeauftrag).

BEFUND (reproduziert, RC-F-Backend-Forensik): `_identitaets_claims()` prüfte
Kraftstoff-/Antriebs-/Getriebe-/Motorcode-Claims nur per `rx.search(text)`
bzw. `finditer(text)`, ganz ohne Verneinungsprüfung:
  - "Der RC F wird nicht mit einer Achtstufen-Automatik ausgeliefert,
    sondern mit einem Sechsganggetriebe." erzeugte trotzdem einen
    Automatik-Claim.
  - "Der RC F fährt nicht mit Diesel, sondern mit Benzin." erzeugte BEIDE
    Kraftstoff-Claims (Diesel UND Benzin).

FIX: `_verneint_im_teilsatz()` (dieselbe Idee wie die bereits bestehende,
battle-getestete `app.kraftstoff_powertrain._positiv_erwaehnt()` für die
Powertrain-Achse) — Verneinung zählt nur im EIGENEN Teilsatz. Teilsatzgrenzen
sind Satzzeichen ODER das Wort "sondern" — NICHT jedes Komma (Nachbesserung:
reine Komma-Grenzen scheiterten sowohl bei "nicht X sondern Y" OHNE Komma als
auch bei einem eingeschobenen Nebensatz "nicht mit einer, auch optional
angebotenen, Automatik", wo ein Komma die Verneinung fälschlich vorzeitig
beendet hätte). "nicht nur X" und "nicht ohne X" zählen NICHT als Verneinung
von X. Kein pauschales Verwerfen des ganzen Textes bei irgendeiner Verneinung
irgendwo — nur der konkret betroffene Treffer wird übersprungen.

Deckt Kraftstoff, Radantrieb, Getriebe (manuell + automatik) und Motorcode
ab — generisch, keine Marken-/Modell-Sonderlogik. Powertrain ist bereits
über `test_powertrain_negation.py` abgedeckt (eigene Funktion, hier nur
indirekt mitgetestet, da `_identitaets_claims` sie aufruft).

    python test_identity_claims_negation.py
"""
from app.technical_research import _identitaets_claims, _ziel

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, info: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f"   {info}" if info else ""))
    if not bedingung:
        _FEHLER.append(name)


ZIEL = _ziel("Lexus", "RC F", 2017, "5.0 V8, Heckantrieb", None)
ZIEL["leistung_ps"] = 477


def treffer(text: str, titel: str = "Lexus RC F") -> dict:
    return {"url": "https://www.autoportal-beispiel.de/lexus-rc-f", "title": titel, "content": text}


def werte(text: str, feld: str) -> list:
    return [c["wert"] for c in _identitaets_claims(treffer(text), ZIEL) if c["feld"] == feld]


# ══════════════════════════════════════════════════════════════════════════
print("=== A) Reproduzierte Produktionsfehler: korrekt behoben ===")
check("A1 'nicht ... Achtstufen-Automatik ... sondern ... Sechsganggetriebe' "
      "claimt KEINE Automatik mehr",
      "automatik" not in werte(
          "Der RC F wird nicht mit einer Achtstufen-Automatik ausgeliefert, "
          "sondern mit einem Sechsganggetriebe.", "transmission"))
check("A2 'nicht mit Diesel, sondern mit Benzin' claimt NUR noch Benzin",
      werte("Der RC F fährt nicht mit Diesel, sondern mit Benzin.", "fuel") == ["benzin"])


# ══════════════════════════════════════════════════════════════════════════
print("\n=== B) 'sondern'-Alternative bleibt erhalten ===")
check("B1 Radantrieb nach 'sondern' wird bejaht",
      werte("Der RC F hat nicht Frontantrieb, sondern Heckantrieb.", "drivetrain") == ["Heck"])
check("B2 Getriebe nach 'sondern' wird bejaht",
      "automatik" in werte(
          "Der RC F hat nicht ein Sechsganggetriebe, sondern eine "
          "Achtstufen-Automatik.", "transmission"))
check("B3 'sondern' OHNE Komma davor wird trotzdem als Teilsatzgrenze "
      "erkannt (keine rein interpunktionsbasierte Sicherheit)",
      werte("Der RC F fährt nicht Diesel sondern Benzin.", "fuel") == ["benzin"])


# ══════════════════════════════════════════════════════════════════════════
print("\n=== C) 'nicht nur X' ist KEINE Verneinung von X (Ergänzung, kein "
      "Ausschluss) ===")
check("C1 'nicht nur mit Heckantrieb, sondern optional auch mit Allrad' "
      "bejaht weiterhin Heckantrieb",
      "Heck" in werte(
          "Der RC F ist nicht nur mit Heckantrieb, sondern optional auch "
          "mit Allradantrieb lieferbar.", "drivetrain"))


# ══════════════════════════════════════════════════════════════════════════
print("\n=== D) 'nicht ohne X' (doppelte Verneinung) bejaht X ===")
check("D1 'kommt nicht ohne Achtstufen-Automatik aus' bejaht Automatik",
      "automatik" in werte("Der RC F kommt nicht ohne Achtstufen-Automatik aus.",
                           "transmission"))


# ══════════════════════════════════════════════════════════════════════════
print("\n=== E) Reine, unqualifizierte Verneinung bleibt unterdrückt ===")
check("E1 'wird nicht mit Frontantrieb ausgeliefert' claimt KEINEN Radantrieb",
      werte("Der RC F wird nicht mit Frontantrieb ausgeliefert.", "drivetrain") == [])
check("E2 'hat keinen Diesel' claimt keinen Kraftstoff",
      werte("Der RC F hat keinen Diesel.", "fuel") == [])


# ══════════════════════════════════════════════════════════════════════════
print("\n=== F) Verneinung in einem früheren Satz blockiert eine spätere, "
      "unabhängige positive Nennung nicht (Satzgrenze) ===")
check("F1 Kraftstoff im ZWEITEN Satz bleibt bestätigt, obwohl der erste "
      "Satz eine andere Angabe verneint",
      werte("Der RC F fährt nicht mit Diesel. In einem anderen Markt gibt "
            "es ihn mit Benzin.", "fuel") == ["benzin"])


# ══════════════════════════════════════════════════════════════════════════
print("\n=== G) Positivkontrolle: unveränderte, unverneinte Fälle "
      "funktionieren weiterhin ===")
check("G1 einfacher Kraftstoff-Claim weiterhin erkannt",
      werte("Der RC F fährt mit Benzin.", "fuel") == ["benzin"])
check("G2 einfacher Radantrieb-Claim weiterhin erkannt",
      werte("Der RC F hat Heckantrieb.", "drivetrain") == ["Heck"])
check("G3 einfacher Automatik-Claim (Zahlwort, voriger Fix) weiterhin erkannt",
      "automatik" in werte("Der RC F hat eine Achtstufen-Automatik.", "transmission"))


# ══════════════════════════════════════════════════════════════════════════
print("\n=== H) Eingeschobener Nebensatz OHNE 'sondern' darf die Verneinung "
      "nicht vorzeitig per Komma beenden (keine rein interpunktionsbasierte "
      "Sicherheit) ===")
check("H1 'nicht mit einer, auch optional angebotenen, Automatik' bleibt "
      "verneint -- das Komma vor 'auch' ist KEINE Teilsatzgrenze",
      werte("Der RC F wird nicht mit einer, auch optional angebotenen, "
            "Automatik ausgeliefert.", "transmission") == [])


# ══════════════════════════════════════════════════════════════════════════
print("\n=== I) Varianten-Alternativen dürfen keine eindeutige "
      "Radantriebsbestätigung erzeugen (ganzer Pfad bis VehicleIdentity) ===")
from app.technical_research import werte_identitaet_aus
from app.models import TechnischeRecherche
from app.vehicle_identity import VehicleIdentity

check("I1 einzelne positive Erwähnungen (Heck UND Allrad) werden auf "
      "Claim-Ebene weiterhin erkannt",
      set(werte("Der RC F ist nicht nur mit Heckantrieb, sondern optional "
                "auch mit Allradantrieb lieferbar.", "drivetrain")) == {"Heck", "Allrad"})

# Einzelne Tier-1-Quelle (Herstellerdomain) -- der kritischste Fall: Gewicht
# allein würde NICHT ausreichen, um zwischen "eindeutig" und "Alternativen"
# zu unterscheiden; das muss über den bestehenden Konflikt-Mechanismus in
# `konsens()` laufen (zwei vergleichbar starke Werte -> UNKNOWN).
treffer_variante_1quelle = [{
    "url": "https://www.lexus.de/rc-f/technische-daten",
    "title": "Lexus RC F technische Daten",
    "content": "Der RC F ist nicht nur mit Heckantrieb, sondern optional auch mit Allradantrieb lieferbar.",
}]
ident_1quelle, _ = werte_identitaet_aus(treffer_variante_1quelle, ZIEL)
check("I2 EINE Tier-1-Quelle mit Alternativtext bestätigt KEINEN Radantrieb "
      "(WebVehicleIdentity.antrieb bleibt None)",
      ident_1quelle.antrieb is None)

# Zwei UNABHÄNGIGE Quellen, beide beschreiben dieselben Alternativen (Heck
# serienmäßig + Allrad optional) mit eigenem, unabhängigem Wortlaut.
treffer_variante_2quellen = [
    {"url": "https://www.autozeitung.de/lexus-rc-f-allrad-option",
     "title": "Lexus RC F Antrieb",
     "content": "Der RC F ist nicht nur mit Heckantrieb, sondern optional auch mit Allradantrieb lieferbar."},
    {"url": "https://www.auto-data.net/lexus-rc-f-antrieb",
     "title": "Lexus RC F Antrieb",
     "content": "Serienmäßig kommt der RC F nicht nur mit Heckantrieb, es ist auch eine Allrad-Variante erhältlich."},
]
ident_2quellen, _ = werte_identitaet_aus(treffer_variante_2quellen, ZIEL)
check("I3 ZWEI unabhängige Quellen mit demselben Alternativkontext "
      "bestätigen ebenfalls KEINEN Radantrieb",
      ident_2quellen.antrieb is None)

identity_voll = VehicleIdentity(make=ZIEL["marke"], model=ZIEL["modell"])
identity_voll.apply_web_evidence(TechnischeRecherche(identitaet=ident_2quellen))
check("I4 Dasselbe gilt am Ende des vollen Pfads bis VehicleIdentity.drivetrain",
      identity_voll.drivetrain is None)

# Regressionskontrolle: zwei unabhängige Quellen, die EINDEUTIG nur Heck
# nennen (kein Alternativkontext), müssen weiterhin normal bestätigen.
treffer_eindeutig_2quellen = [
    {"url": "https://www.autozeitung.de/lexus-rc-f-antrieb", "title": "Lexus RC F Antrieb",
     "content": "Der RC F hat Heckantrieb."},
    {"url": "https://www.auto-data.net/lexus-rc-f-antrieb", "title": "Lexus RC F Antrieb",
     "content": "Der RC F wird mit Heckantrieb ausgeliefert."},
]
ident_eindeutig, _ = werte_identitaet_aus(treffer_eindeutig_2quellen, ZIEL)
check("I5 Regressionskontrolle: eindeutiger Radantrieb ohne Alternativkontext "
      "wird weiterhin korrekt bestätigt", ident_eindeutig.antrieb == "Heck")


# ══════════════════════════════════════════════════════════════════════════
print("\n=== J) Fremde Varianten, mehrsätziger Kontext, widersprüchliche "
      "Quellen (Negationsreichweite realistisch, nicht nur isolierte Sätze) ===")
check("J1 Ein als Kontrast erkannter Fremdfahrzeug-Satz ('Anders als ...') "
      "mit Alternativ-/Ergänzungskonstruktion bindet nicht an das "
      "Zielfahrzeug; dessen eigener, separater Claim bleibt erhalten",
      werte("Anders als der Lexus IS F, der nicht nur Heckantrieb, sondern "
            "auch Allrad bietet. Der RC F hat serienmäßig Heckantrieb.",
            "drivetrain") == ["Heck"])
check("J2 mehrsätziger Kontext: Verneinung im ersten Satz bindet nicht an "
      "eine unabhängige, spätere Tatsachenaussage",
      werte("Der RC F hat keinen Allradantrieb. Serienmäßig ist er mit "
            "Heckantrieb ausgestattet.", "drivetrain") == ["Heck"])


# ══════════════════════════════════════════════════════════════════════════
print("\n=== K) Eine Gangzahl allein beweist weder manuell noch automatisch "
      "(keine automatische Handschaltungszuordnung ergänzt) ===")
check("K1 'Sechsganggetriebe' (ohne 'Schalt-'/'Automatik'-Zusatz) claimt "
      "KEIN manuelles Getriebe",
      werte("Der RC F hat ein Sechsganggetriebe.", "transmission") == [])
check("K2 'Achtganggetriebe' (ohne Zusatz) claimt ebenfalls nichts",
      werte("Der RC F hat ein Achtganggetriebe.", "transmission") == [])
check("K3 Gegenprobe: 'Sechsgang-Schaltgetriebe' bleibt eindeutig manuell",
      werte("Der RC F hat ein Sechsgang-Schaltgetriebe.", "transmission") == ["manuell"])


print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE IDENTITY-CLAIMS-NEGATION-TESTS GRUEN")
