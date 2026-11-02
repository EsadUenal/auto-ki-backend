"""
ENFAL-Reparaturpaket N4/N5/N6/N8 (2026-11-02) -- echter KaufCheck-Lauf
"Eingefügter Text(20261101-210820).txt", Lexus RC F 2017.

N6 -- zusammengesetzte technische Nutzerangabe mit Teilbestätigung
--------------------------------------------------------------------
BEFUND (realer Bericht): "Motor: 5.0 V8, Heckantrieb (laut Inserat/
Nutzereingabe; bestätigt durch Webquellen)" -- im selben Bericht zwei Zeilen
darunter "Antrieb (Rad): Heck (laut Inserat/Nutzereingabe, nicht unabhängig
bestätigt)". Derselbe physische Fakt (Heckantrieb) gilt im selben Bericht
gleichzeitig als bestätigt und als unbestätigt.

URSACHE (kausal verifiziert, s. app/technical_research.py
`_motor_kern_bestaetigt()`-Docstring): `engine_name` übernahm bei fehlender
ENFAL-Referenz die ROHE, zusammengesetzte Nutzereingabe ("5.0 V8,
Heckantrieb") unverändert. Die "Kern"-Tokenprüfung, die gegen Quellen
abgleicht, bestand NUR aus `gemischt` ("v8") ODER `woerter` ("heckantrieb")
-- "gemischt or woerter" verwarf "woerter" VOLLSTÄNDIG, sobald "gemischt"
nicht leer war. Eine Quelle, die nur "5.0 V8" bestätigte, bestätigte dadurch
automatisch den GANZEN Rohstring inklusive "Heckantrieb".

FIX: `_motor_kern_bestaetigt()` entfernt eine erkannte Antriebsart-Phrase aus
dem für `feldwerte["engine_name"]["value"]` übernommenen Wert -- die
Antriebsart hat ihr eigenes, unverändertes Konsens-Feld ("drivetrain").
`VehicleIdentity.apply_web_evidence()` (app/vehicle_identity.py) erkennt
jetzt zusätzlich TEILBESTÄTIGUNG (`_ist_teilbestaetigt()`): ein Web-Wert, der
nur einen Teil der zusammengesetzten Nutzerangabe deckt, hebt das Feld NICHT
auf "user_confirmed", sondern trägt den bestätigten Teil separat
(`bestaetigte_teile`) -- `herkunft()` (app/anzeige.py) zeigt das jetzt
differenziert an.

N4 -- Doppelungen (kausal verifiziert statt vermutet)
--------------------------------------------------------
Erste Vermutung ("Motoröl"/"Öl- und Filterwechsel" unterscheiden sich in
Aktion/Regime) war FALSCH -- offline mit dem echten Text widerlegt: beide
ergeben `_wartung_aktion()=="ersetzen"`, `_wartung_regime()=="fest"`. Die
tatsächliche Ursache: `_anzeige_bauteil()` kann ein MEHRWORT-Synonym-Muster
("oel und filterwechsel") strukturell nie über ein einzelnes Wort finden und
fällt auf das Muster selbst als Anzeige zurück -- das erzeugt eine von
"Motoröl" abweichende `_norm(bauteil)`-Mention für denselben `schluessel`
("motoroel"), wodurch `_gleiches_objekt_mention()` die beiden Aussagen nie
zusammenführte. FIX: für die in `_WARTUNG_VOKABULAR_ERWEITERUNG` bewusst als
echte Synonyme angelegten Schlüssel wird die Objekt-Mention-Prüfung
übersprungen (NICHT für grob zusammenfassende Schlüssel wie "getriebe").

N5 -- wiederkehrende Intervalle
----------------------------------
"liegt ... bereits hinter dem Fahrzeug" benannte trotz entfernter Zahl
weiterhin EINEN einzelnen vergangenen Termin. Neuer Wortlaut: "wurde ...
rein rechnerisch mindestens einmal erreicht" -- keine Fälligkeit, keine
Rückstandsrechnung, keine Anzahl.

N8 -- Hilfetext
-------------------
Das Codebeispiel "RC F USC10" im Klärungshinweis war ausgerechnet ein laut
app-eigener Generationsauflösung NICHT gesicherter Code (gesichert ist
UXC1). Entfernt, kein Ersatzbeispiel (jedes konkrete Beispiel trägt dasselbe
Risiko).

    python test_n4_n5_n6_n8_reparaturpaket.py
"""
from app.anzeige import _web_bestaetigt, fahrzeug_titel, herkunft
from app.key_findings import _GENERATION_MODELLFELD_HINWEIS, _klaerung_aktion
from app.laufleistung import _formuliere, STATUS_DARUEBER
from app.technical_research import (
    _extrahiere_fakten, _gleiches_technisches_risiko, _motor_kern_bestaetigt,
)
from app.vehicle_identity import VehicleIdentity, _ist_teilbestaetigt

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


print("=" * 78)
print("N6) Motor-Kern-Bestätigung entfernt die Antriebsart-Phrase")
print("=" * 78)
check("A) '5.0 V8, Heckantrieb' -> '5.0 V8' (Antriebsart entfernt)",
      _motor_kern_bestaetigt("5.0 V8, Heckantrieb") == "5.0 V8",
      _motor_kern_bestaetigt("5.0 V8, Heckantrieb"))
check("B) ein Motorfeld OHNE Antriebsart bleibt unverändert (keine Überreparatur)",
      _motor_kern_bestaetigt("2.0 TDI") == "2.0 TDI")
check("C) 'Frontantrieb'/'Allradantrieb' werden ebenso generisch erkannt "
      "(keine Lexus-/Heck-Sonderregel)",
      _motor_kern_bestaetigt("1.5 TSI, Frontantrieb") == "1.5 TSI"
      and _motor_kern_bestaetigt("2.0 TFSI, Allradantrieb") == "2.0 TFSI")

print("\n" + "=" * 78)
print("N6) _ist_teilbestaetigt(): Web deckt nur einen Teil der Angabe")
print("=" * 78)
check("D) '5.0 V8' ist ein echter Teil von '5.0 V8, Heckantrieb'",
      _ist_teilbestaetigt("engine_name", "5.0 V8, Heckantrieb", "5.0 V8"))
check("E) exakte Übereinstimmung gilt NICHT als 'teilbestätigt' (das ist die "
      "bestehende Vollbestätigung, ein anderer Zweig)",
      not _ist_teilbestaetigt("engine_name", "5.0 V8", "5.0 V8"))
check("F) ein völlig anderer Wert ('3.0 TDI') ist keine Teilbestätigung von "
      "'5.0 V8, Heckantrieb' (das bleibt ein echter Konflikt, kein Teiltreffer)",
      not _ist_teilbestaetigt("engine_name", "5.0 V8, Heckantrieb", "3.0 TDI"))

print("\n" + "=" * 78)
print("N6) VehicleIdentity.apply_web_evidence() -- End-to-End am realen Fall")
print("=" * 78)


class _FakeIdentitaet:
    belegt = True
    quellen = ["pressroom.lexus.com"]
    feldwerte = {"engine_name": {"value": "5.0 V8", "confidence": "mittel", "domains": 1}}


class _FakeWebRecherche:
    identitaet = _FakeIdentitaet()


ident = VehicleIdentity(make="Lexus", model="RC F", engine_name="5.0 V8, Heckantrieb")
ident.field_evidence = {
    "engine_name": {"verification_state": "user_only", "confirmed_by": [],
                    "raw_user_value": "5.0 V8, Heckantrieb"},
}
ident.apply_web_evidence(_FakeWebRecherche())
fe = ident.field_evidence["engine_name"]

check("G) 'engine_name' bleibt 'user_only' -- KEINE Vollbestätigung des "
      "zusammengesetzten Felds durch eine nur teilweise passende Quelle",
      fe.get("verification_state") == "user_only", str(fe))
check("H) 'web' steht NICHT in confirmed_by -- _web_bestaetigt() darf dieses "
      "Feld weiterhin nicht als vollständig web-bestätigt behandeln",
      "web" not in (fe.get("confirmed_by") or []), str(fe))
check("I) der bestätigte Teil ('5.0 V8') ist separat vermerkt",
      "5.0 V8" in (fe.get("bestaetigte_teile") or []), str(fe))
check("J) _web_bestaetigt() liefert für dieses Feld weiterhin False -- die "
      "Kaufempfehlungs-Überschrift (fahrzeug_titel mit nur_web_bestaetigt=True) "
      "darf 'Heckantrieb' deshalb NICHT als web-bestätigt mitführen",
      _web_bestaetigt(ident, "engine_name") is False)
titel = fahrzeug_titel(ident, mit_jahr=False, nur_web_bestaetigt=True)
check("K) die Web-Plausibilisierungs-Überschrift lässt 'engine_name' "
      "('5.0 V8, Heckantrieb') komplett weg, statt ihn teilweise korrekt zu "
      "behaupten (additiv, keine Info verloren -- der Nutzerwert bleibt in der "
      "normalen Feldliste sichtbar)",
      "Heckantrieb" not in titel and "5.0 V8" not in titel, titel)

herkunft_text = herkunft(fe, "engine_name")
check("L) herkunft() benennt den bestätigten Teil UND lässt die restliche "
      "Angabe ausdrücklich unbestätigt -- löst den Berichtswiderspruch "
      "(Motor-Feld vs. 'Antrieb (Rad)'-Feld) auf Feldebene auf",
      "5.0 V8" in herkunft_text and "bestätigt" in herkunft_text
      and "nicht unabhängig geprüft" in herkunft_text,
      herkunft_text)
check("M) herkunft() behauptet NICHT pauschal 'bestätigt durch Webquellen' "
      "für das gesamte Feld (das wäre wieder der ursprüngliche Fehler)",
      herkunft_text != "laut Inserat/Nutzereingabe; bestätigt durch Webquellen")


print("\n" + "=" * 78)
print("N4) Motoröl / Öl- und Filterwechsel: gleicher Schlüssel, gleiches "
      "Intervall -- Objekt-Mention-Gate übersprungen, nicht pauschal entfernt")
print("=" * 78)
check("N) 'motoroel' (echter Synonym-Schlüssel) ignoriert einen Objekt-"
      "Mismatch, wenn Intervall UND Tokens ausreichend übereinstimmen",
      _gleiches_technisches_risiko(
          {"wechseln", "oel", "filter"}, {"wechseln", "oel", "filter"},
          kandidat_merkmal="alle 15 000 km", andere_merkmal="alle 15 000 km",
          kandidat_objekt="motoroel", andere_objekt="oel und filterwechsel",
          basis_schluessel="motoroel"))
check("O) 'getriebe' (bewusst grob zusammenfassender Schlüssel) bleibt "
      "UNVERÄNDERT strikt -- 'Getriebeöl' und 'Getriebefilter' dürfen "
      "weiterhin NICHT allein wegen des gemeinsamen Schlüssels verschmelzen",
      not _gleiches_technisches_risiko(
          {"wechseln"}, {"wechseln"},
          kandidat_merkmal="alle 60 000 km", andere_merkmal="alle 60 000 km",
          kandidat_objekt="getriebeoel", andere_objekt="getriebefilter",
          basis_schluessel="getriebe"))
check("P) ein abweichendes Intervall blockiert weiterhin IMMER, auch bei "
      "'motoroel' -- der Fix lockert NUR die Objekt-Prüfung, nie die "
      "Intervallprüfung",
      not _gleiches_technisches_risiko(
          {"wechseln"}, {"wechseln"},
          kandidat_merkmal="alle 15 000 km", andere_merkmal="alle 30 000 km",
          kandidat_objekt="motoroel", andere_objekt="oel und filterwechsel",
          basis_schluessel="motoroel"))


print("\n" + "=" * 78)
print("N5) Wiederkehrendes Intervall -- keine Einzeltermin-Suggestion mehr")
print("=" * 78)
text_darueber = _formuliere("Bremsen", STATUS_DARUEBER, 82_000, 15_000, None, "web_wartung")
check("Q) 'liegt ... bereits hinter dem Fahrzeug' kommt NICHT mehr vor",
      "bereits hinter dem Fahrzeug" not in text_darueber, text_darueber)
check("R) stattdessen neutral 'mindestens einmal erreicht' (keine Anzahl, "
      "kein Datum, keine Fälligkeit)",
      "mindestens einmal erreicht" in text_darueber, text_darueber)
for verbotenes_wort in ("fällig", "überfällig", "versäumt", "nicht durchgeführt"):
    check(f"S) verbotenes Wort {verbotenes_wort!r} kommt weiterhin nicht vor",
          verbotenes_wort not in text_darueber.lower(), text_darueber)
check("T) kein konkreter Rückstand in km wird berechnet (keine Ersatzrechnung)",
      "km zurück" not in text_darueber and "km Rückstand" not in text_darueber,
      text_darueber)


print("\n" + "=" * 78)
print("N8) Klärungshinweis ohne reales, ungesichertes Codebeispiel")
print("=" * 78)
check("U) 'USC10' kommt im Hinweistext nicht mehr vor",
      "USC10" not in _GENERATION_MODELLFELD_HINWEIS)
check("V) 'RC F' (oder ein anderes konkretes Fahrzeug) kommt im generischen "
      "Hinweistext nicht mehr vor -- keine Fahrzeug-/Code-Hardcode-Attrappe",
      "RC F" not in _GENERATION_MODELLFELD_HINWEIS)
check("W) der Hinweis verspricht keinen bestimmten Fahrzeugschein-Eintrag "
      "(kein 'im Fahrzeugschein steht' o.ä.)",
      "Fahrzeugschein" not in _GENERATION_MODELLFELD_HINWEIS)
check("X) der Hinweis macht weiterhin klar, dass ein ergänzter Code keinen "
      "Nachweis ersetzt",
      "kein Nachweis" in _GENERATION_MODELLFELD_HINWEIS)
aktion_text = _klaerung_aktion("die Generation", satz_praefix="Bitte", generation_betroffen=True)
check("Y) der zusammengesetzte Klärungssatz enthält den Hinweis weiterhin "
      "(Integration unverändert), aber ohne USC10/RC F",
      "kann die Recherche gezielter machen" in aktion_text
      and "USC10" not in aktion_text and "RC F" not in aktion_text,
      aktion_text)


if _FEHLER:
    print(f"\n{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nALLE N4/N5/N6/N8-TESTS GRUEN")
