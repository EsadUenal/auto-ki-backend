"""
Claim-Level Vehicle Binding (Forensik-Folgeauftrag, Package A).

Root Cause (Lexus-RC-F-Forensik, CONFIRMED): `_identitaets_claims()` scannte bei
stark (Titel-)ausgerichteten Treffern den GESAMTEN Dokumenttext nach Detail-Claims
(powertrain/fuel/drivetrain/horsepower/transmission/displacement/engine_code) --
unabhaengig davon, ob der konkrete Satz tatsaechlich vom Zielmodell handelt. Eine
Seite ueber ein Modell kann im selben Text eine NAMENSNAHE Sibling-Variante
(Hybrid-/Elektro-Geschwister derselben Baureihe) beschreiben; deren Claims wurden
bisher unveraendert dem Zielmodell zugerechnet (reales Beispiel: Lexus "RC F" vs.
"RC 300h" auf derselben Seite).

Fix: Wiederverwendung der bereits bestehenden, produktiv fuer rueckruf/
schwachstelle/wartung genutzten Segment-/Widerspruchs-Bindung
(`_segment_kontext_saetze`) -- keine zweite, parallele Architektur.

GENERISCH getestet mit erfundenen Marken/Modellen. Die Sibling-Modellnamen sind
bewusst so gewaehlt, dass sie (wie im realen Fall "RC F" vs. "RC 300h") NICHT
alle Signatur-Tokens des Zielmodells enthalten -- sonst waere der Testfall
trivial und wuerde den echten Fehlerfall nicht abbilden.

    python test_claim_level_vehicle_binding.py
"""
from app.technical_research import _identitaets_claims, werte_identitaet_aus

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def treffer(url: str, titel: str, inhalt: str) -> dict:
    return {"url": url, "title": titel, "content": inhalt}


def claims_fuer(feld, claims):
    return [c["wert"] for c in claims if c["feld"] == feld]


ZIEL = dict(marke="Testmarke", modell="Modellz F", baujahr=2021, motor=None)

print("=== A) Zielmodell (ICE) + namensnahe Sibling-Hybrid-Variante im selben Dokument ===")
r_a = treffer(
    "https://www.fachmedium.de/testmarke-modellz-f",
    "Testmarke Modellz F im Test",
    "Die Testmarke Modellz F ist ein reiner Verbrenner mit 150 PS. "
    "Die Baureihe gibt es daneben auch als Testmarke Modellz Hybrid mit "
    "Hybridantrieb und 190 PS.")
claims_a = _identitaets_claims(r_a, ZIEL)
check("A1 kein Hybrid/HEV-Claim aus dem Sibling-Satz",
      "HEV" not in claims_fuer("powertrain", claims_a))
check("A2 die eigene PS-Angabe (150) des Zielmodells bleibt erhalten",
      150 in claims_fuer("horsepower", claims_a))
check("A3 die PS-Angabe (190) der Sibling-Variante wird NICHT uebernommen",
      190 not in claims_fuer("horsepower", claims_a))

print("\n=== B) Zielmodell (Hybrid) + namensnahe Sibling-ICE-Variante (symmetrisch) ===")
ziel_hybrid = dict(marke="Testmarke", modell="Modellz Hybrid", baujahr=2021, motor=None)
r_b = treffer(
    "https://www.fachmedium.de/testmarke-modellz-hybrid",
    "Testmarke Modellz Hybrid im Test",
    "Die Testmarke Modellz Hybrid kombiniert Elektromotor und Verbrenner mit "
    "190 PS Systemleistung. Die Baureihe gibt es daneben auch als Testmarke "
    "Modellz F mit einem reinen Verbrennermotor ohne jede Elektrifizierung.")
claims_b = _identitaets_claims(r_b, ziel_hybrid)
check("B1 kein ICE-Claim aus dem Sibling-Satz ueber die Nicht-Hybrid-Variante",
      "ICE" not in claims_fuer("powertrain", claims_b))

print("\n=== C) Zielmodell und korrekter Claim stehen im selben lokalen Satz ===")
r_c = treffer("https://www.fachmedium.de/testmarke-modellz-f", "Testmarke Modellz F im Test",
              "Die Testmarke Modellz F leistet 150 PS und hat Heckantrieb.")
claims_c = _identitaets_claims(r_c, ZIEL)
check("C1 PS-Claim im eigenen Satz wird weiterhin erkannt",
      150 in claims_fuer("horsepower", claims_c))
check("C2 Antriebs-Claim im eigenen Satz wird weiterhin erkannt",
      "Heck" in claims_fuer("drivetrain", claims_c))

print("\n=== D) Claim steht wenige neutrale Saetze nach der Zielmodell-Nennung ===")
r_d = treffer(
    "https://www.fachmedium.de/testmarke-modellz-f", "Testmarke Modellz F im Test",
    "Die Testmarke Modellz F ist seit 2021 erhaeltlich. Viele Fahrer loben das "
    "Fahrverhalten im Alltag. Das Fahrzeug leistet 150 PS.")
claims_d = _identitaets_claims(r_d, ZIEL)
check("D1 PS-Claim nach neutralen Brueckensaetzen wird weiterhin (vererbter "
      "Kontext) erkannt", 150 in claims_fuer("horsepower", claims_d))

print("\n=== E) Modellwechsel (expliziter Konkurrent) beendet den Kontext ===")
r_e = treffer(
    "https://www.fachmedium.de/vergleich", "Testmarke Modellz F im Vergleichstest",
    "Die Testmarke Modellz F leistet 150 PS. Der Konkurrent Anderemarke "
    "Konkurrenzmodell leistet dagegen 300 PS und ist ein reiner Hybrid. "
    "Beide Fahrzeuge wurden fuer diesen Vergleichstest gefahren.")
claims_e = _identitaets_claims(r_e, ZIEL)
check("E1 eigene PS-Angabe (150) bleibt erhalten", 150 in claims_fuer("horsepower", claims_e))
check("E2 die PS-Angabe (300) des Konkurrenten wird NICHT dem Zielmodell zugerechnet",
      300 not in claims_fuer("horsepower", claims_e))
check("E3 kein Hybrid-Claim aus dem Konkurrenten-Satz",
      "HEV" not in claims_fuer("powertrain", claims_e))

print("\n=== F) Zwei unabhaengige Domains mit demselben falsch zugeordneten "
      "Sibling-Claim duerfen keinen Consensus erzeugen ===")
treffer_f = [
    treffer("https://www.fachmedium1.de/testmarke-modellz-f", "Testmarke Modellz F im Test",
            "Die Testmarke Modellz F ist ein reiner Verbrenner mit 150 PS. Die "
            "Baureihe gibt es daneben auch als Testmarke Modellz Hybrid mit "
            "Hybridantrieb."),
    treffer("https://www.fachmedium2.de/testmarke-modellz-f", "Testmarke Modellz F Daten",
            "Testmarke Modellz F: 150 PS, reiner Verbrenner. Als Testmarke Modellz "
            "Hybrid ist zusaetzlich eine Hybridvariante erhaeltlich."),
]
identitaet_f, _ = werte_identitaet_aus(treffer_f, ZIEL)
check("F1 powertrain bleibt UNKNOWN (kein Consensus aus zwei falsch zugeordneten "
      "Sibling-Claims)", identitaet_f.powertrain is None)

print()
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE CLAIM-LEVEL-VEHICLE-BINDING-TESTS GRUEN")
