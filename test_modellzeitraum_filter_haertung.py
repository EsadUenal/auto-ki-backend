"""
Jahresfilter-Haertungsauftrag (29.10.2026, Fortsetzung von Commit 3cf18a9,
"Datenblatt-eigenes Modellstart/Modellende bindet Detail-Claims ans Zieljahr").

Dauerhafte Regressionssuite fuer `app.technical_research._datenblatt_zeitraum()`
und seine Verdrahtung in `_identitaets_claims()`. Deckt genau die im Auftrag
genannten Faelle ab:

  A) Zieljahr ausserhalb des konkreten Datenblattzeitraums -> betroffene
     Nicht-Generation-Claims werden verworfen (`baujahr_ausserhalb_datenblatt`).
  B) Zieljahr innerhalb -> Claims bleiben erhalten.
  C) Eine allgemeine, im selben Dokument genannte Baureihenspanne (z.B.
     "Baureihe RC (UXC1) (12/14 - 06/25)") ueberschreibt den engeren,
     explizit angegebenen Variantenzeitraum NICHT.
  D) Mehrere VERSCHIEDENE Modellstart/Modellende-Paare auf einer
     Vergleichsseite werden NICHT pauschal als EIN Quellenzeitraum behandelt
     (Ambiguitaet -> keine Bindung an ein geratenes Fenster). Fortsetzungs-
     auftrag (29.10.2026, nach Commit 272c423): eine erkannte, aber nicht
     aufloesbare Zuordnung ist dabei KEIN "keine Zeitangabe" -- sie liefert
     das Sentinel `ZEITRAUM_MEHRDEUTIG` und wird wie ein belegter Ausschluss
     behandelt (eigener Grund `baujahr_datenblatt_mehrdeutig`), NICHT wie
     eine unbedenkliche Luecke durchgereicht (s. test_zielgueltigkeit_
     mehrdeutigkeit_kausal.py fuer den kausalen Vorher/Nachher-Nachweis).
  E) MM/YY (zwei- UND vierstellig) wird korrekt geparst.
  F) Ein fehlendes Modellende (laufende/aktuelle Variante) erfindet KEINEN
     abgeschlossenen Zeitraum -- kein Ausschluss ohne beide Grenzen.
  G) Ein Zieljahr, das genau auf die Grenze des Fensters faellt (teilweise
     Ueberlappung, da nur das Baujahr, nie der Monat bekannt ist), wird NICHT
     automatisch vollstaendig ausgeschlossen (inklusive Grenzen).

Alle Fixtures sind generisch (erfundene Marke/Modell) ausser dort, wo explizit
die reale ADAC-Textstruktur reproduziert wird (identisch zu bereits in
test_rc_f_evidenzluecken_vertieft.py/test_rc_f_real_replay_identitaetsluecke.py
verwendeten, per Browser verifizierten Original-Zitaten).

    python test_modellzeitraum_filter_haertung.py
"""
from app.technical_research import (
    _datenblatt_zeitraum, _identitaets_claims, _ziel, ZEITRAUM_MEHRDEUTIG,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url: str, titel: str, inhalt: str) -> dict:
    return {"url": url, "title": titel, "content": inhalt}


def claims_fuer(feld, claims):
    return [c for c in claims if c["feld"] == feld]


print("=" * 70)
print("A) Zieljahr ausserhalb des Datenblattzeitraums -> Claims verworfen")
print("=" * 70)
# Reale ADAC-Textstruktur (RC F Ultimate Edition, Modellstart 08/24-06/25),
# Zieljahr 2017 -- wortgleich mit test_rc_f_evidenzluecken_vertieft.py.
ADAC_ULTIMATE = treffer(
    "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/",
    "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25): Technische Daten, Bilder, Preise | ADAC",
    "Marke Lexus Modell RC F Typ Ultimate Edition Automatik Baureihe RC (UXC1) "
    "(12/14 - 06/25) Herstellerinterne Baureihenbezeichnung UXC1 Modellstart 08/24 "
    "Modellende 06/25 Baureihenstart 12/14 Baureihenende 06/25 Motorcode 2UR-GSE "
    "Leistung maximal in PS 464 Antriebsart Hinterrad Getriebeart Automatikgetriebe "
    "Kraftstoffart Super Hubraum 4969 ccm")
z_2017 = _ziel("Lexus", "RC F", 2017, "5.0 V8", None)
claims_a = _identitaets_claims(ADAC_ULTIMATE, z_2017)
fuel_a = claims_fuer("fuel", claims_a)
drivetrain_a = claims_fuer("drivetrain", claims_a)
engine_code_a = claims_fuer("engine_code", claims_a)
check("A1 fuel-Claim ist vorhanden, aber verworfen",
      bool(fuel_a) and all(c.get("verworfen") == "baujahr_ausserhalb_datenblatt" for c in fuel_a),
      str(fuel_a))
check("A2 drivetrain-Claim verworfen", bool(drivetrain_a) and
      all(c.get("verworfen") == "baujahr_ausserhalb_datenblatt" for c in drivetrain_a), str(drivetrain_a))
check("A3 engine_code-Claim verworfen", bool(engine_code_a) and
      all(c.get("verworfen") == "baujahr_ausserhalb_datenblatt" for c in engine_code_a), str(engine_code_a))

print("\n" + "=" * 70)
print("B) Zieljahr innerhalb des Datenblattzeitraums -> Claims bleiben erhalten")
print("=" * 70)
z_2024 = _ziel("Lexus", "RC F", 2024, "5.0 V8", None)
claims_b = _identitaets_claims(ADAC_ULTIMATE, z_2024)
fuel_b = claims_fuer("fuel", claims_b)
check("B1 fuel-Claim nicht verworfen, wenn Baujahr 2024 im Fenster 08/24-06/25 liegt",
      bool(fuel_b) and all(not c.get("verworfen") for c in fuel_b), str(fuel_b))
drivetrain_b = claims_fuer("drivetrain", claims_b)
check("B2 drivetrain-Claim ebenfalls nicht verworfen",
      bool(drivetrain_b) and all(not c.get("verworfen") for c in drivetrain_b), str(drivetrain_b))

print("\n" + "=" * 70)
print("C) Allgemeine Baureihenspanne (12/14-06/25, deckt 2017) ueberschreibt NICHT "
      "den engeren Variantenzeitraum (08/24-06/25, deckt 2017 NICHT)")
print("=" * 70)
check("C1 trotz Baureihenspanne 12/14-06/25 (deckt 2017) bleibt der engere "
      "Modellstart/Modellende-Ausschluss fuer 2017 wirksam",
      bool(fuel_a) and fuel_a[0].get("verworfen") == "baujahr_ausserhalb_datenblatt",
      "s. A1 -- dieselbe Quelle, Baureihenspanne haette 2017 faelschlich gedeckt")

print("\n" + "=" * 70)
print("D) Vergleichsseite mit ZWEI VERSCHIEDENEN Modellstart/Modellende-Paaren "
      "-> keine pauschale Bindung an EIN Fenster")
print("=" * 70)
VERGLEICHSSEITE = treffer(
    "https://www.adac.de/vergleich/lexus-rc-f-varianten/",
    "Lexus RC F Varianten im Vergleich: Automatik vs. Ultimate Edition | ADAC",
    "Lexus RC F Automatik Modellstart 01/18 Modellende 03/19 Motorcode 2UR-GSE "
    "Antriebsart Hinterrad Kraftstoffart Super. Lexus RC F Ultimate Edition "
    "Modellstart 08/24 Modellende 06/25 Motorcode 2UR-GSE Antriebsart Hinterrad "
    "Kraftstoffart Super.")
check("D1 _datenblatt_zeitraum() liefert das Sentinel ZEITRAUM_MEHRDEUTIG bei "
      "zwei verschiedenen Fenstern (NICHT None -- Mehrdeutigkeit ist kein "
      "'keine Zeitangabe')", _datenblatt_zeitraum(
          f"{VERGLEICHSSEITE['title']}. {VERGLEICHSSEITE['content']}") == ZEITRAUM_MEHRDEUTIG)
claims_d = _identitaets_claims(VERGLEICHSSEITE, z_2017)
fuel_d = claims_fuer("fuel", claims_d)
check("D2 fuel-Claims der Vergleichsseite werden VERWORFEN (eigener Grund "
      "'baujahr_datenblatt_mehrdeutig') -- eine erkannte, aber nicht "
      "aufloesbare Varianten-Zuordnung darf keinen Wert zielgueltig "
      "durchreichen, statt eines der beiden Fenster zu raten (s. "
      "test_zielgueltigkeit_mehrdeutigkeit_kausal.py fuer den Nachweis, dass "
      "dies vor dem Fix tatsaechlich einen falschen Wert bestaetigt haette)",
      bool(fuel_d) and all(c.get("verworfen") == "baujahr_datenblatt_mehrdeutig"
                           for c in fuel_d), str(fuel_d))

print("\n" + "=" * 70)
print("E) MM/YY-Format: zwei- UND vierstellige Jahresangabe korrekt geparst")
print("=" * 70)
check("E1 zweistellig (08/24 -> 2024)", _datenblatt_zeitraum("Modellstart 08/24 Modellende 06/25")
      == (2024, 2025))
check("E2 vierstellig (01/2018 -> 2018)", _datenblatt_zeitraum("Modellstart 01/2018 Modellende 03/2019")
      == (2018, 2019))

print("\n" + "=" * 70)
print("F) Fehlendes Modellende (laufende/aktuelle Variante) -> kein erfundener "
      "abgeschlossener Zeitraum, kein Ausschluss")
print("=" * 70)
LAUFENDE_VARIANTE = treffer(
    "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/999999/",
    "Lexus RC F Automatik: Technische Daten | ADAC",
    "Marke Lexus Modell RC F Typ Automatik Baureihe RC (UXC1) (12/14 - 06/25) "
    "Modellstart 01/23 Baureihenstart 12/14 Baureihenende 06/25 Motorcode 2UR-GSE "
    "Antriebsart Hinterrad Kraftstoffart Super")
check("F1 ohne 'Modellende' liefert _datenblatt_zeitraum() None (kein erfundenes Ende)",
      _datenblatt_zeitraum(f"{LAUFENDE_VARIANTE['title']}. {LAUFENDE_VARIANTE['content']}") is None)
claims_f = _identitaets_claims(LAUFENDE_VARIANTE, z_2017)
fuel_f = claims_fuer("fuel", claims_f)
check("F2 fuel-Claim dieser laufenden Variante bleibt unverworfen (2017 liegt "
      "zwar vor Modellstart 01/23, aber ohne Modellende ist das Datenblatt "
      "nicht als GESCHLOSSENES, abweichendes Fenster belegt)",
      bool(fuel_f) and all(not c.get("verworfen") for c in fuel_f), str(fuel_f))

print("\n" + "=" * 70)
print("G) Grenzjahr-Ueberlappung: Zieljahr faellt auf die Fenstergrenze selbst "
     "-> NICHT automatisch vollstaendig ausgeschlossen (nur Baujahr, kein Monat bekannt)")
print("=" * 70)
check("G1 Baujahr == Modellstart-Jahr (von) liegt inklusive im Fenster",
      _datenblatt_zeitraum("Modellstart 09/17 Modellende 06/19") == (2017, 2019))
GRENZJAHR_START = treffer(
    "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/111111/",
    "Lexus RC F Automatik: Technische Daten | ADAC",
    "Marke Lexus Modell RC F Typ Automatik Modellstart 09/17 Modellende 06/19 "
    "Motorcode 2UR-GSE Antriebsart Hinterrad Kraftstoffart Super")
claims_g1 = _identitaets_claims(GRENZJAHR_START, z_2017)
fuel_g1 = claims_fuer("fuel", claims_g1)
check("G2 Zieljahr 2017 == Modellstart-Jahr 2017 wird NICHT ausgeschlossen "
      "(inklusive Grenze, Monat des Zielfahrzeugs unbekannt)",
      bool(fuel_g1) and all(not c.get("verworfen") for c in fuel_g1), str(fuel_g1))
GRENZJAHR_ENDE = treffer(
    "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/222222/",
    "Lexus RC F Automatik: Technische Daten | ADAC",
    "Marke Lexus Modell RC F Typ Automatik Modellstart 01/15 Modellende 03/17 "
    "Motorcode 2UR-GSE Antriebsart Hinterrad Kraftstoffart Super")
claims_g2 = _identitaets_claims(GRENZJAHR_ENDE, z_2017)
fuel_g2 = claims_fuer("fuel", claims_g2)
check("G3 Zieljahr 2017 == Modellende-Jahr 2017 wird ebenfalls NICHT ausgeschlossen",
      bool(fuel_g2) and all(not c.get("verworfen") for c in fuel_g2), str(fuel_g2))
# Echter, unzweideutiger Ausschluss bleibt moeglich: mindestens ein volles
# Jahr Abstand zur naechsten Fenstergrenze.
GANZ_AUSSERHALB = treffer(
    "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/333333/",
    "Lexus RC F Automatik: Technische Daten | ADAC",
    "Marke Lexus Modell RC F Typ Automatik Modellstart 01/19 Modellende 03/21 "
    "Motorcode 2UR-GSE Antriebsart Hinterrad Kraftstoffart Super")
claims_g4 = _identitaets_claims(GANZ_AUSSERHALB, z_2017)
fuel_g4 = claims_fuer("fuel", claims_g4)
check("G4 Zieljahr 2017 bei eindeutigem Abstand (Fenster 2019-2021) bleibt "
      "korrekt ausgeschlossen -- die Lockerung an der Grenze hebt den "
      "eindeutigen Fall nicht auf",
      bool(fuel_g4) and all(c.get("verworfen") == "baujahr_ausserhalb_datenblatt"
                            for c in fuel_g4), str(fuel_g4))

print()
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE JAHRESFILTER-HAERTUNGSTESTS GRUEN")
