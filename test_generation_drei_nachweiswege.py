"""
ENFAL-Korrekturauftrag ("Ableitung braucht positive Zuordnung, nicht nur
fehlenden Widerspruch", 2026-11): dauerhafte, gezielte Regression gegen eine
Wiederkehr des entfernten Nachweiswegs B.

Die vorige Fassung dieser Datei (feat/79a0bea) fuehrte `_generation_cluster_
ableiten()` ein: mehrere unterschiedlich geschriebene Generationscodes
("UXC1", "XC1") wurden zu EINEM Cluster zusammengefuehrt, sobald KEIN Beleg
sie zeitlich AUSSCHLOSS und mindestens einer das Zielbaujahr bestaetigte.
Das behandelte Abwesenheit eines Widerspruchs wie einen Beleg fuer
Zugehoerigkeit -- fehlt bei einem Code jede Zeitangabe (RC-F-Befund: ADAC
nennt "UXC1" ganz ohne geparstes Zeitfenster), ist seine Zuordnung UNBEKANNT,
nicht "vertraeglich". Die Funktion wurde deshalb vollstaendig entfernt (s.
Kommentarblock in `app/technical_research.py` vor `_generationen_pruefen()`).

Verbleiben zwei fachlich getrennte, weiterhin gueltige Nachweiswege:

A) Nutzerangabe unabhaengig geprueft -- `auxiliary_code` (rein formbasiert aus
   dem Modellfeld abgetrennt) wird NIEMALS selbst zur Evidenz, sondern nur
   als nachtraeglicher Abgleich gegen eine bereits UNABHAENGIG (Nachweisweg
   C) bestaetigte Generation gewertet (`generation_nutzerabgleich`).
C) Direkt durch Quellen belegt -- der bestehende `konsens()`-Mechanismus
   (exakte Zeichenkette, `MIN_KONSENS_GEWICHT`, "stark"-Schwelle).

Diese Datei sichert:
  * den real belegten RC-F-Fall (ADAC "UXC1" ohne Zeitfenster vs.
    carwiki.de/AMS "XC1") bleibt UNGEKLAERT, nicht "XC1" -- unabhaengig
    davon, ob eine der Schreibweisen ein Zeitfenster traegt;
  * zwei Codes mit JEWEILS einem EIGENEN, identischen Zeitfenster werden
    trotzdem NICHT automatisch gleichgesetzt (keine Codeaequivalenz allein
    aus numerischer Fensteruebereinstimmung);
  * ein unvollstaendiger Kandidatenbestand (kein Zeitfenster irgendeiner
    Quelle) erzeugt keine Scheineindeutigkeit;
  * der Fremdgenerationsfilter (`_fremde_generation()`) akzeptiert weiterhin
    NUR die eine ueber Nachweisweg C belegte Schreibweise, nie eine unbelegt
    "mitgenommene" zweite;
  * Nachweisweg C (direkter Konsens) und Nachweisweg A (Nutzerabgleich)
    bleiben fuer den unabhaengig vom Cluster-Fall funktionierenden Teil
    unveraendert wirksam.

Synthetische Faelle sind unten ausdrueklich als "SYNTHETISCH" markiert.

    python scripts/test_isolated.py test_generation_drei_nachweiswege.py
"""
from app.technical_research import _extrahiere_fakten, _fremde_generation, _ziel, werte_identitaet_aus

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# ── Reale Quellen (unveraendert zitiert, s. test_identity_familie_zugehoerigkeit.py) ─
ADAC = treffer(
    "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/",
    "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25): Technische Daten | ADAC",
    "Marke Lexus Modell RC F Typ Ultimate Edition Automatik Baureihe RC (UXC1) "
    "(12/14 - 06/25) Herstellerinterne Baureihenbezeichnung UXC1 Motorcode 2UR-GSE "
    "Leistung maximal in PS 464 Antriebsart Hinterrad Getriebeart Automatikgetriebe")
CARWIKI = treffer(
    "https://carwiki.de/lexus-rc-hsn-tsn-schluesselnummer/",
    "Lexus RC HSN & TSN | Tabelle aller Schluesselnummern",
    "Uebersicht: Alle Schluesselnummern (HSN/TSN) der Lexus RC Baureihen. "
    "Hersteller: Lexus Fahrzeug: RC Baureihe: RC XC1 Karosserie: Coupe "
    "MODELL HSN SCHLUESSELNUMMER TSN SCHLUESSELNUMMER KRAFTSTOFF HUBRAUM LEISTUNG BAUJAHR "
    "Lexus RC F Carbon Automatik 5013 AJM Benzin 4969 cm3 477 PS 2014 bis 2025")
AMS = treffer(
    "https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
    "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
    "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Leistung 181 PS bis "
    "477 PS. LEXUS RC F TRACK EDITION IM SUPERTEST: Was kann der V8-Sauger auf der "
    "Rennstrecke? LEXUS LC 500 UND RC F: V8-Sportler auf Abschiedstour.")

# ── 1) Voller RC-F-Replay: datierter ("XC1") + undatierter ("UXC1") Code ──
# OHNE Verknuepfungsbeleg -> keine Zusammenfuehrung, generation bleibt
# UNGEKLAERT. Das ist der zentrale, in der 79a0bea-Fassung FALSCH als "XC1"
# aufgeloeste Fall.
z1 = _ziel("Lexus", "RC F", 2017, "5.0 V8", None)
ident1, _ = werte_identitaet_aus([ADAC, CARWIKI, AMS], z1)
check("1) realer RC-F-Quellensatz (ADAC+carwiki.de+AMS): ADAC 'UXC1' ohne "
      "Zeitfenster, carwiki.de/AMS 'XC1' (AMS mit Zeitfenster) -> KEINE "
      "Ableitung, generation bleibt unbekannt (kein Verknuepfungsbeleg)",
      ident1.generation is None and ident1.generation_cluster == []
      and ident1.generation_nachweisweg is None,
      f"generation={ident1.generation!r} cluster={ident1.generation_cluster!r} "
      f"nachweisweg={ident1.generation_nachweisweg!r}")

# ── 2) Nutzerangabe allein wertet sich nicht selbst auf (Nachweisweg A) ────
# Der Nutzer nennt "UXC1" (passt zu ADACs Schreibweise) -- ohne unabhaengig
# (Nachweisweg C) bestaetigte Generation bleibt der Abgleich leer.
z2 = _ziel("Lexus", "RC F UXC1", 2017, "5.0 V8", None)
ident2, _ = werte_identitaet_aus([ADAC, CARWIKI, AMS], z2)
check("2) Nutzerangabe 'UXC1' (im Modellfeld), keine unabhaengig bestaetigte "
      "Generation -> nutzerabgleich bleibt leer, keine Selbstaufwertung",
      ident2.generation is None and ident2.generation_nutzerabgleich is None,
      f"generation={ident2.generation!r} nutzerabgleich={ident2.generation_nutzerabgleich!r}")

# ── 3) Nachweisweg C funktioniert weiterhin: exakte Zeichenkette, EIN Code ─
# ADAC-Fixture auf "XC1" vereinheitlicht (sonst strukturell unveraendert) --
# alle drei Quellen nennen jetzt UEBEREINSTIMMEND "XC1", kein zweiter Code
# -> direkter Konsens bleibt unveraendert wirksam.
ADAC_XC1 = treffer(
    "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/",
    "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25): Technische Daten | ADAC",
    "Marke Lexus Modell RC F Typ Ultimate Edition Automatik Baureihe RC (XC1) "
    "(12/14 - 06/25) Herstellerinterne Baureihenbezeichnung XC1 Motorcode 2UR-GSE "
    "Leistung maximal in PS 464 Antriebsart Hinterrad Getriebeart Automatikgetriebe")
z3 = _ziel("Lexus", "RC F", 2017, "5.0 V8", None)
ident3, _ = werte_identitaet_aus([ADAC_XC1, CARWIKI, AMS], z3)
check("3) ADAC+carwiki.de+AMS nennen UEBEREINSTIMMEND 'XC1' (kein zweiter "
      "Code) -> Nachweisweg C bestaetigt direkt, cluster=['XC1'], "
      "nachweisweg='quelle'",
      ident3.generation == "XC1" and ident3.generation_cluster == ["XC1"]
      and ident3.generation_nachweisweg == "quelle",
      f"generation={ident3.generation!r} cluster={ident3.generation_cluster!r} "
      f"nachweisweg={ident3.generation_nachweisweg!r}")

# ── 4) Nachweisweg A bestaetigt sich NUR gegen eine echte Nachweisweg-C-Basis ─
z4 = _ziel("Lexus", "RC F XC1", 2017, "5.0 V8", None)
ident4, _ = werte_identitaet_aus([ADAC_XC1, CARWIKI, AMS], z4)
check("4) Nutzerangabe 'XC1' passt zur unabhaengig (Nachweisweg C) "
      "bestaetigten Generation -> nutzerabgleich='uebereinstimmend'",
      ident4.generation == "XC1" and ident4.generation_nutzerabgleich == "uebereinstimmend",
      f"generation={ident4.generation!r} nutzerabgleich={ident4.generation_nutzerabgleich!r}")

# ── 5) Gleiche Zeitfenster allein beweisen keine Codeaequivalenz ──────────
# SYNTHETISCH (generisch, kein konkretes Fahrzeug): zwei UNTERSCHIEDLICHE
# Codes mit JEWEILS demselben Zeitfenster -- selbst eine exakte Uebereinstimmung
# der Fenster darf KEINE automatische Gleichsetzung ausloesen (es gibt keinen
# Mechanismus mehr, der ueberhaupt ueber Code-Grenzen hinweg gruppiert).
IDENT_FENSTER_A = treffer(
    "https://www.adac.de/beispiel-fenster-a", "Lexus RC F Generation A | ADAC",
    "Marke Lexus Modell RC F Baureihe RC (AAA1) (2015 bis 2025) "
    "Herstellerinterne Baureihenbezeichnung AAA1")
IDENT_FENSTER_B = treffer(
    "https://www.auto-motor-und-sport.de/beispiel-fenster-b",
    "Lexus RC (1. Generation) (BBB1) im Ueberblick (ab 2015)",
    "Lexus RC (1. Generation) (BBB1) im Ueberblick (ab 2015). Der Lexus RC F "
    "leistet 477 PS.")
z5 = _ziel("Lexus", "RC F", 2017, "5.0 V8", None)
ident5, _ = werte_identitaet_aus([IDENT_FENSTER_A, IDENT_FENSTER_B], z5)
check("5) SYNTHETISCH: 'AAA1' und 'BBB1' nennen je ein identisches "
      "Zeitfenster (ab 2015) -- keine automatische Codegleichsetzung, "
      "generation bleibt unbekannt",
      ident5.generation is None, f"generation={ident5.generation!r}")

# ── 6) Unvollstaendiger Kandidatenraum: keine Scheineindeutigkeit ─────────
z6 = _ziel("Lexus", "RC F", 2017, "5.0 V8", None)
ident6, _ = werte_identitaet_aus([ADAC, CARWIKI], z6)
check("6) nur ADAC (UXC1) + carwiki.de (XC1), KEIN Zeitfenster irgendeiner "
      "Quelle belegt -> keine Ableitung",
      ident6.generation is None, f"generation={ident6.generation!r}")

# ── 7) Fremdgenerationsfilter: KEIN unbelegt "mitgenommener" Code ─────────
# Ohne Nachweisweg B traegt `generation_cluster` nie mehr als die eine ueber
# Nachweisweg C belegte Schreibweise -- eine andere, nicht belegte
# Schreibweise (z.B. "UXC1") gilt deshalb weiterhin als FREMD, selbst wenn
# sie zum selben realen Fahrzeug gehoeren mag.
check("7a) Quelle nennt eine NICHT bestaetigte Generation (NC) -> weiterhin "
      "als fremd erkannt",
      _fremde_generation(
          treffer("https://www.example-fachmedium.de/lexus-rc-nc-wartung",
                  "Lexus RC NC Wartungsplan",
                  "Der Lexus RC NC: alle 10.000 km wird ein Oelwechsel durchgefuehrt."),
          "RC", "XC1", ["XC1"]) is True)
check("7b) Quelle nennt eine andere, NICHT ueber Nachweisweg C bestaetigte "
      "Schreibweise (UXC1, obwohl real dasselbe Fahrzeug gemeint sein mag) "
      "-> OHNE Verknuepfungsbeleg weiterhin als fremd erkannt",
      _fremde_generation(
          treffer("https://x.de/uxc1", "Lexus RC UXC1 Wartung", "Der Lexus RC UXC1: Wartung."),
          "RC", "XC1", ["XC1"]) is True)
check("7c) Quelle nennt die bestaetigte Schreibweise XC1 direkt -> NICHT "
      "als fremd erkannt",
      _fremde_generation(
          treffer("https://x.de/xc1", "Lexus RC XC1 Wartung", "Der Lexus RC XC1: Wartung."),
          "RC", "XC1", ["XC1"]) is False)

# ── 8) Generische Kontrolle mit anderem Fahrzeug (kein Lexus) ─────────────
VW_A = treffer(
    "https://www.adac.de/vw-golf-a", "VW Golf GTI (05/19): Technische Daten | ADAC",
    "Marke VW Modell Golf GTI Baureihe Golf (UCD1) Herstellerinterne "
    "Baureihenbezeichnung UCD1 Modellstart 05/19")
VW_B = treffer(
    "https://carwiki.de/vw-golf-hsn-tsn", "VW Golf HSN & TSN Tabelle",
    "Hersteller: VW Fahrzeug: Golf Baureihe: Golf CD1 Karosserie: Schraegheck "
    "MODELL HSN SCHLUESSELNUMMER VW Golf GTI 0603 ABC Benzin 1984 cm3 245 PS")
VW_C = treffer(
    "https://www.auto-motor-und-sport.de/vw-golf-c",
    "VW Golf (8. Generation) (CD1) im Ueberblick (ab 2019)",
    "VW Golf (8. Generation) (CD1) im Ueberblick (ab 2019). Der VW Golf GTI leistet 245 PS.")
z8 = _ziel("VW", "Golf GTI", 2020, "2.0 TSI", None)
ident8, _ = werte_identitaet_aus([VW_A, VW_B, VW_C], z8)
check("8) generisch: VW Golf GTI (anderes Fahrzeug, 'UCD1' vs. 'CD1', "
      "strukturell identisch zum RC-F-Fall) -> ebenso UNGEKLAERT, keine "
      "Marken-/Modell-Sonderregel",
      ident8.generation is None, f"generation={ident8.generation!r}")

# ── Variantenspezifische Claims bleiben von der Entfernung unberuehrt ─────
abgelehnt_var: list[dict] = []
fakten_var = _extrahiere_fakten(
    [treffer("https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
             "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
             "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Der Lexus RC F "
             "leistet 477 PS.")],
    "wartung", marke="Lexus", modell="RC F", baujahr=2017,
    generation="XC1", generation_cluster=["XC1"], abgelehnt=abgelehnt_var)
check("Variantenschutz) ein auf eine einzelne Schreibweise begrenzter "
      "Cluster beeinflusst die PS-Claim-Pruefung nicht -- diese Quelle "
      "liefert ohnehin keinen Wartungsfakt (kein Intervall im Text), aus "
      "anderem Grund als 'andere_generation'",
      not any(a.get("grund") == "andere_generation" for a in abgelehnt_var))

print()
if _FEHLER:
    print(f"FEHLER: {len(_FEHLER)} Check(s) fehlgeschlagen: {_FEHLER}")
    raise SystemExit(1)
print("ALLE GENERATION-DREI-NACHWEISWEGE-TESTS GRUEN")
