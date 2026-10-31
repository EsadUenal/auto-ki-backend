"""
ENFAL-Folgeauftrag ("Generation über drei belastbare Nachweiswege auflösen",
2026-11): dauerhafte, gezielte Regression für die drei Nachweiswege einer
`identity.generation`-Auflösung.

A) Nutzerangabe unabhängig geprüft -- `auxiliary_code` (rein formbasiert aus
   dem Modellfeld abgetrennt) wird NIEMALS selbst zur Evidenz, sondern nur
   als nachträglicher Abgleich gegen eine bereits UNABHÄNGIG (B/C)
   bestätigte Generation gewertet (`generation_nutzerabgleich`).
B) Eindeutig aus belegten Zuordnungsdaten abgeleitet -- NEU,
   `_generation_cluster_ableiten()` (app/technical_research.py): gruppiert
   Generations-Claims nicht mehr nur nach EXAKTER Zeichenkette (das bleibt
   Nachweisweg C), sondern erkennt mehrere Schreibweisen als DIESELBE
   Generation, wenn KEIN belegtes Zeitfenster sie ausschließt UND
   mindestens eines davon das Zielbaujahr tatsächlich bestätigt. Schreibt
   NIE eine Ordinalangabe ("1. Generation") in das chassiscode-förmige
   Feld -- nachgelagerte Verbraucher (`_fremde_generation()`) erwarten
   weiterhin genau solche Werte.
C) Direkt durch Quellen belegt -- UNVERÄNDERT der bestehende
   `konsens()`-Mechanismus (exakte Zeichenkette, `MIN_KONSENS_GEWICHT`,
   "stark"-Schwelle), bleibt bevorzugt und wird zuerst versucht.

BEFUND (real, Lexus RC F, Baujahr 2017): ADAC nennt "UXC1", carwiki.de und
auto-motor-und-sport.de nennen "XC1". Nur auto-motor-und-sport.de trägt ein
tatsächlich geparstes, das Zielbaujahr bestätigendes Zeitfenster ("ab
2015"). Keine der drei Quellen widerspricht einer anderen zeitlich -- vorher
blieb die Generation trotzdem UNKNOWN (zwei je zu schwache Zeichenketten-
Gruppen), jetzt wird sie über Nachweisweg B als "XC1" abgeleitet, mit
`generation_cluster=["UXC1","XC1"]` (beide Schreibweisen bleiben erhalten,
keine Codegleichsetzung wird behauptet).

GEGENPRÜFUNG (verhindert Überauflösung): zwei Schreibweisen mit JEWEILS
einem EIGENEN, unterschiedlichen belegten Zeitfenster (selbst bei
numerischer Überschneidung) werden NICHT zusammengeführt -- das wäre eine
echte, nicht auflösbare Mehrdeutigkeit (zwei reale Generationen kommen
infrage), keine blosse Schreibweisen-Frage.

Synthetische Fälle sind unten ausdrücklich als "SYNTHETISCH" markiert.

    python scripts/test_isolated.py test_generation_drei_nachweiswege.py
"""
from app.technical_research import (
    _extrahiere_fakten, _fremde_generation, _generation_cluster_ableiten, _ziel, werte_identitaet_aus,
)

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

# ── 3) Belegte Zuordnungsdaten lassen genau eine Generation uebrig (Pfad B) ─
z3 = _ziel("Lexus", "RC F", 2017, "5.0 V8", None)
ident3, _ = werte_identitaet_aus([ADAC, CARWIKI, AMS], z3)
check("3) realer RC-F-Quellensatz (ADAC+carwiki.de+AMS) -> Nachweisweg B "
      "leitet 'XC1' ab, Cluster bewahrt beide Schreibweisen",
      ident3.generation == "XC1" and set(ident3.generation_cluster) == {"UXC1", "XC1"}
      and ident3.generation_nachweisweg == "abgeleitet",
      f"generation={ident3.generation!r} cluster={ident3.generation_cluster!r} "
      f"nachweisweg={ident3.generation_nachweisweg!r}")

# ── 1) Nutzer nennt eine existierende, passend belegte Generation (Pfad A) ──
z1 = _ziel("Lexus", "RC F UXC1", 2017, "5.0 V8", None)
ident1, _ = werte_identitaet_aus([ADAC, CARWIKI, AMS], z1)
check("1) Nutzerangabe 'UXC1' (im Modellfeld) passt zur unabhaengig "
      "abgeleiteten Generation -> nutzerabgleich='uebereinstimmend'",
      ident1.generation == "XC1" and ident1.generation_nutzerabgleich == "uebereinstimmend",
      f"generation={ident1.generation!r} nutzerabgleich={ident1.generation_nutzerabgleich!r}")

# ── 6) Nur uebereinstimmende Nutzerangabe: keine unabhaengige Bestaetigung ──
# SYNTHETISCH: dieselben drei Quellen OHNE das AMS-Zeitfenster -- der
# Nutzer nennt "UXC1" (passt zu ADACs Wert), aber OHNE das anker-fenster
# tragende AMS bleibt Nachweisweg B unerfuellt. Der Nutzerabgleich darf sich
# dann NICHT selbst zur Bestaetigung aufwerten.
z6 = _ziel("Lexus", "RC F UXC1", 2017, "5.0 V8", None)
ident6, _ = werte_identitaet_aus([ADAC, CARWIKI], z6)
check("6) SYNTHETISCH: nur ADAC+carwiki (kein belegtes Zeitfenster), "
      "Nutzer nennt zufaellig passendes 'UXC1' -> bleibt UNBESTAETIGT "
      "(Nutzerangabe allein zaehlt nicht als unabhaengige Evidenz)",
      ident6.generation is None and ident6.generation_nutzerabgleich is None,
      f"generation={ident6.generation!r} nutzerabgleich={ident6.generation_nutzerabgleich!r}")

# ── 2) Generation existiert, aber Zielbaujahr ausserhalb des belegten Fensters ─
z2 = _ziel("Lexus", "RC F", 2010, "5.0 V8", None)
ident2, _ = werte_identitaet_aus([ADAC, CARWIKI, AMS], z2)
check("2) dieselben Quellen, Zielbaujahr 2010 (ausserhalb des einzigen "
      "belegten Fensters 'ab 2015') -> keine Ableitung",
      ident2.generation is None, f"generation={ident2.generation!r}")

# ── 5) Unvollstaendiger Kandidatenraum: keine Scheineindeutigkeit ──────────
z5 = _ziel("Lexus", "RC F", 2017, "5.0 V8", None)
ident5, abgelehnt5 = werte_identitaet_aus([ADAC, CARWIKI], z5)
check("5) nur ADAC (UXC1) + carwiki.de (XC1), KEIN Zeitfenster irgendeiner "
      "Quelle belegt -> keine Ableitung (Kandidatenraum nicht abgegrenzt)",
      ident5.generation is None, f"generation={ident5.generation!r}")

# ── 4) / 7) / 8) Zwei Generationen bleiben moeglich -- Jahresueberlappung ──
# SYNTHETISCH (generisch, kein konkretes Fahrzeug): zwei Codes mit JEWEILS
# EIGENEM, unterschiedlichem Zeitfenster -- auch bei numerischer
# Ueberschneidung (2018-2022) ein echter, nicht aufloesbarer Widerspruch.
UEBERLAPPEND_A = treffer(
    "https://www.adac.de/beispiel-a", "Lexus RC F Generation A | ADAC",
    "Marke Lexus Modell RC F Baureihe RC (UXC1) (2014 bis 2022) "
    "Herstellerinterne Baureihenbezeichnung UXC1")
UEBERLAPPEND_B = treffer(
    "https://www.auto-motor-und-sport.de/beispiel-b", "Lexus RC Generationen Uebersicht",
    "Die Lexus RC (UXC9), Chassiscode dieser Baureihe, deckt 2018 bis 2025 ab. "
    "Der Lexus RC F gehoert zu dieser Baureihe.")
z4 = _ziel("Lexus", "RC F", 2019, "5.0 V8", None)
ident4, abgelehnt4 = werte_identitaet_aus([UEBERLAPPEND_A, UEBERLAPPEND_B], z4)
check("4/7/8) SYNTHETISCH: 'UXC1' (2014-2022) vs. 'UXC9' (2018-2025), "
      "Baujahr 2019 -- Fenster ueberschneiden sich zahlenmaessig, sind aber "
      "ZWEI unterschiedliche belegte Fenster -> KEINE Ableitung trotz "
      "Ueberschneidung (Jahresueberlappung allein genuegt nicht)",
      ident4.generation is None, f"generation={ident4.generation!r}")

# ── 9) Fremdgenerationsfilter bleibt wirksam (mit Cluster) ─────────────────
FREMD_QUELLE = treffer(
    "https://www.example-fachmedium.de/lexus-rc-nc-wartung",
    "Lexus RC NC Wartungsplan",
    "Der Lexus RC NC: alle 10.000 km wird ein Oelwechsel durchgefuehrt.")
ZUGEHOERIGE_QUELLE = treffer(
    "https://www.example-fachmedium.de/lexus-rc-xc1-wartung",
    "Lexus RC XC1 Wartungsplan",
    "Der Lexus RC XC1: alle 15.000 km wird ein Oelwechsel durchgefuehrt.")
check("9a) Quelle nennt eine NICHT im Cluster enthaltene Generation (NC) "
      "-> weiterhin als fremd erkannt",
      _fremde_generation(FREMD_QUELLE, "RC", "XC1", ["UXC1", "XC1"]) is True)
check("9b) Quelle nennt eine IM Cluster enthaltene Schreibweise (UXC1, "
      "obwohl der Anzeigewert 'XC1' ist) -> NICHT als fremd erkannt",
      _fremde_generation(
          treffer("https://x.de/uxc1", "Lexus RC UXC1 Wartung", "Der Lexus RC UXC1: Wartung."),
          "RC", "XC1", ["UXC1", "XC1"]) is False)
check("9c) Quelle nennt die zum Cluster gehoerige Schreibweise XC1 direkt "
      "-> NICHT als fremd erkannt",
      _fremde_generation(ZUGEHOERIGE_QUELLE, "RC", "XC1", ["UXC1", "XC1"]) is False)

# ── 10) Generische Kontrolle mit anderem Fahrzeug (kein Lexus) ─────────────
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
z10 = _ziel("VW", "Golf GTI", 2020, "2.0 TSI", None)
ident10, _ = werte_identitaet_aus([VW_A, VW_B, VW_C], z10)
check("10) generisch: VW Golf GTI (anderes Fahrzeug, 'UCD1' vs. 'CD1') -> "
      "Nachweisweg B leitet ebenso ab, keine Marken-/Modell-Sonderregel",
      ident10.generation == "CD1" and set(ident10.generation_cluster) == {"UCD1", "CD1"}
      and ident10.generation_nachweisweg == "abgeleitet",
      f"generation={ident10.generation!r} cluster={ident10.generation_cluster!r}")

# ── Verbraucher-Schutz: niemals eine Ordinalangabe in das Feld schreiben ───
gen_nummer_claim = {"feld": "generation_nummer", "wert": "1. Generation", "domain": "x.de",
                    "tier": 2, "url": "https://x.de", "zeitraum": None}
wert_b, cluster_b, info_b = _generation_cluster_ableiten([], None)
check("Verbraucherschutz) _generation_cluster_ableiten() mit leeren Claims "
      "liefert None, nie eine erfundene oder ordinale Zeichenkette",
      wert_b is None and cluster_b == [], f"wert={wert_b!r} cluster={cluster_b!r}")

# ── Variantenspezifische Claims bleiben von Nachweisweg B unberuehrt ──────
abgelehnt_var: list[dict] = []
fakten_var = _extrahiere_fakten(
    [treffer("https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
             "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
             "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Der Lexus RC F "
             "leistet 477 PS.")],
    "wartung", marke="Lexus", modell="RC F", baujahr=2017,
    generation="XC1", generation_cluster=["UXC1", "XC1"], abgelehnt=abgelehnt_var)
check("Variantenschutz) 'XC1'/'UXC1'-Cluster beeinflusst die PS-Claim-"
      "Pruefung nicht -- diese Quelle liefert ohnehin keinen Wartungsfakt "
      "(kein Intervall im Text), aus anderem Grund als 'andere_generation'",
      not any(a.get("grund") == "andere_generation" for a in abgelehnt_var))

print()
if _FEHLER:
    print(f"FEHLER: {len(_FEHLER)} Check(s) fehlgeschlagen: {_FEHLER}")
    raise SystemExit(1)
print("ALLE GENERATION-DREI-NACHWEISWEGE-TESTS GRUEN")
