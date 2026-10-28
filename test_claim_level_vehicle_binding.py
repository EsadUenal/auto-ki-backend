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

print("\n=== G) PASS-GATE-Folgeauftrag §A/§H.2-3: Sibling-Variante IM SELBEN "
      "Satz wie das Zielmodell (kontrastive Konstruktion) ===")
treffer_g_sibling = [
    treffer("https://www.fachmedium1.de/testmarke-modellz-f", "Testmarke Modellz F im Test",
            "Anders als die Testmarke Modellz Hybrid bietet die Testmarke Modellz F "
            "einen reinen V8-Verbrenner mit 480 PS."),
    treffer("https://www.fachmedium2.de/testmarke-modellz-f", "Testmarke Modellz F Daten",
            "Im Gegensatz zur Testmarke Modellz Hybrid hat die Testmarke Modellz F "
            "keinen Elektromotor, nur einen V8."),
]
claims_g = [c for r in treffer_g_sibling for c in _identitaets_claims(r, ZIEL)]
check("G1 KEIN powertrain-Claim aus dem Sibling-Satz trotz Nennung IM SELBEN Satz "
      "wie das Zielmodell (§A Auftrag Punkt 5/6, PASS-GATE-Fund)",
      "HEV" not in claims_fuer("powertrain", claims_g))
identitaet_g, _ = werte_identitaet_aus(treffer_g_sibling, ZIEL)
check("G2 identity.powertrain bleibt entsprechend None (kein Consensus aus "
      "Sibling-Satz-Kontamination)", identitaet_g.powertrain is None)

print("\n=== H) Hybrid-Erwaehnung neben passender Modellnennung OHNE eindeutige "
      "Variantenbindung -> bleibt UNKNOWN, kein Raten ===")
treffer_h_ambiguous = [
    treffer("https://www.fachmedium1.de/testmarke-modellz-f", "Testmarke Modellz F Uebersicht",
            "Die Baureihe Testmarke Modellz bietet verschiedene Antriebe. Manche "
            "Haendler bieten Hybridversionen an."),
]
claims_h = [c for r in treffer_h_ambiguous for c in _identitaets_claims(r, ZIEL)]
check("H1 keine eindeutige powertrain-Zuordnung bei vager, nicht variantengebundener "
      "Hybrid-Erwaehnung", "HEV" not in claims_fuer("powertrain", claims_h))

print("\n=== I) F16-Gegenfall (Fortsetzungsauftrag, KLAR SYNTHETISCH, echte zur Marke "
      "passende Herstellerdomain) ===")
# SYNTHETISCH -- kein realer Pressetext (der echte newsroom.lexus.eu-Fall ist
# unveraendert als Regression in test_rc_f_schwachstelle_wartung_reparatur
# paket.py::F16 erhalten, dort OHNE extrahierbares Antriebsvokabular). Dieser
# Gegenfall prueft dieselbe Kontamination MIT tatsaechlich extrahierbarer
# Antriebs-/Getriebeangabe, auf einer echten, zur angefragten Marke
# passenden Herstellerdomain (lexus.de, Tier 1 ueber `_marke_eigene_domain()`
# -- s. F17 in test_rc_f_schwachstelle_wartung_reparaturpaket.py), bis zum
# VOLLSTAENDIGEN `werte_identitaet_aus()`-Ergebnis, nicht nur isoliert ueber
# `_identitaets_claims()`. Der Satzanfang "Der Lexus RC 300h ist ..." traf
# bis zu diesem Fix die Marken-Kulanz in `_claim_widerspricht_fahrzeug_
# lokal()` (das Zielmodell "RC F" wird dort NIE genannt) -- keine RC-F-/
# F-SPORT-Sonderlogik im Fix, derselbe generische Mechanismus wie Fall A-H.
ZIEL_RC_F = dict(marke="Lexus", modell="RC F", baujahr=2017, motor=None)
treffer_i = [
    treffer("https://www.lexus.de/rc-f", "Lexus RC F",
            "Der Lexus RC 300h ist ein Vollhybrid mit stufenlosem Getriebe. "
            "Der Lexus RC F hat Heckantrieb und ein Achtstufen-Automatikgetriebe."),
    treffer("https://www.auto-motor-und-sport.de/lexus-rc-f", "Lexus RC F im Test",
            "Der Lexus RC 300h ist ein Vollhybrid mit stufenlosem Getriebe. "
            "Der Lexus RC F hat 477 PS, Heckantrieb und ein "
            "Achtstufen-Automatikgetriebe."),
]
claims_i = [c for r in treffer_i for c in _identitaets_claims(r, ZIEL_RC_F)]
check("I1 KEIN powertrain-Claim (HEV) aus der Fremdvariante 'RC 300h'",
      "HEV" not in claims_fuer("powertrain", claims_i))
check("I2 Antriebs-Claim (Heckantrieb) der tatsaechlichen Zielvariante 'RC F' "
      "bleibt erhalten -- gemischte Seite liefert die korrekte Zielmodell"
      "angabe weiterhin", "Heck" in claims_fuer("drivetrain", claims_i))
check("I3 Getriebe-Claim (Automatik) der tatsaechlichen Zielvariante bleibt erhalten",
      "automatik" in claims_fuer("transmission", claims_i))
identitaet_i, _ = werte_identitaet_aus(treffer_i, ZIEL_RC_F)
check("I4 identity.powertrain bleibt None (kein Hybrid-Consensus aus der "
      "Fremdvariante) -- vollstaendiges werte_identitaet_aus()-Ergebnis",
      identitaet_i.powertrain is None)
check("I5 identity.antrieb (Heckantrieb) wird trotzdem korrekt bestaetigt",
      identitaet_i.antrieb == "Heck")
check("I6 identity.getriebe (Automatik) wird trotzdem korrekt bestaetigt",
      identitaet_i.getriebe == "automatik")

print()
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE CLAIM-LEVEL-VEHICLE-BINDING-TESTS GRUEN")
