"""
ENFAL -- "N9 Differenzial-Erkennung und Achsentrennung abschließen" (2026-11-02).

TEIL 1 (N9): "differenzial" (deutsche Schreibweise, real in lex-sw-00de.pdf:
"Vorderachsdifferenzialöl"/"Hinterachsdifferenzialöl") fehlte in
`app.kaufaktionen._KOMPONENTEN` -- nur "differential" (englische Schreibweise)
war hinterlegt. `_wartung_tabellen_segmente()` (app/technical_research.py)
setzt eine Fenstergrenze NUR an einer erkannten Vokabular-Position; ohne
Eintrag für "differenzial" begann an KEINEM der vier Differenzialöl-
Vorkommen im realen Dokument ein eigenes Fenster. Jede vorangehende
"Verteilergetriebeöl (...)"-Zeile verschmolz deshalb mit den direkt
folgenden Differenzialöl-Zeilen zu EINEM gemeinsamen Fenster, dessen ERSTE
Klammer über das gesamte Fenster entschied -- eine separat zutreffende
"Hinterachsdifferenzialöl (außer ES 300h, ...)"-Zeile (RC F NICHT in der
Ausschlussliste) ging dadurch verloren (kein erfundener Fakt, aber
unvollständige Auswertung).

FIX (app/kaufaktionen.py, `_KOMPONENTEN`, schluessel="allradantrieb"):
"differenzial" ergänzt NEBEN "differential" -- generisch, keine
Fahrzeug-/Marken-Sonderregel, dieselbe Zuordnung (schluessel
"allradantrieb") wie "verteilergetriebe"/"achsgetriebe"/... Beide
Schreibweisen bleiben nebeneinander erhalten; bereits funktionierende
Quellen mit "differential" sind unverändert.

TEIL 2 (Achsentrennung, enge Prüfung der letzten Runde): `_ziel_achsen()`
(app/technical_research.py) darf aus "Benzin" (Kraftstoffart) NIEMALS einen
Beweis für "kein Hybrid" (Elektrifizierungsart) ableiten -- das sind laut
`app/kraftstoff_powertrain.py` zwei UNABHÄNGIGE Achsen. Verifiziert unten
direkt an `_ziel_achsen()`/`scope_passt()`: "Benzin" allein liefert
`powertrains=None` (ungeklärt), nicht `{"ICE"}`. Nur eine ausdrückliche
Elektrifizierungsangabe (bestätigte Identität ODER ein explizites
"Verbrenner"/Hybrid-Wort in der Nutzer-/Motorangabe) setzt die
Antriebsart-Achse.

    python test_n9_differenzial_und_achsentrennung.py
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.kraftstoff_powertrain import scope_passt
from app.models import WebVehicleIdentity
from app.technical_research import _bauteil_vokabular, _extrahiere_fakten, _wartung_tabellen_segmente, \
    _wartung_vokabular, _ziel_achsen

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# Echter Rohtext (lex-sw-00de.pdf, Seite 16/17 -- wortgleich zu
# test_n2_n3_n7_wartung_tabellenblock.py / test_wartung_technische_bedingung_vollpfad.py).
_SICHERHEIT_BLOCK = (
    "*2:  Für Albanien, Bosnien & Herzegowina, Kanarische Inseln, Zypern, Estland, "
    "Island, Lettland, Republik Nordmazedonien, Malta, Montenegro, Serbien, "
    "Slowakei *3:  Nur für LS 500h, ES 300h und NX 350h 16 Erneuern - Inspektion "
    "SICHERHEIT Zwischencheck Hauptservice Bremspedal und Feststellbremse*1 I "
    "Bremsklötze und -scheiben I I Bremsbeläge der Feststellbremse (IS 300h, "
    "RC 300h, RC F, LC 500, LC 500h, LS 500, LS 500h) I Bremsflüssigkeit E "
    "Bremsleitungen und -schläuche I Vorder- und Hinterradaufhängung I Lenkrad, "
    "Lenkgestänge und -getriebe I Bodenmatten I I Reifen, Beleuchtung, "
    "Wischerblätter und Waschflüssigkeit I I Kugelgelenke und Staubkappen der "
    "Radaufhängung I*2 I *1:  Prüfung der Feststellbremse betrifft nur: IS 300h, "
    "RC 300h und RC F *2: LS 500, LS 500h, LC 500h ZUSÄTZLICHER TEILEAUSTAUSCH "
    "Luftfilter Alle 60.000 km / 4 Jahre Zündkerzen Platin- oder Iridiumtyp Alle "
    "90.000 km RX 300, LS 500 Alle 60,000 km Motorkühlmittel Alle 150.000 km "
    "Danach alle 90.000 km Inverter-Kühlmittel (nur Hybrid- und Hybrid "
    "Plug-in-Modelle) Alle 240.000 km Danach alle 90.000 km Verteilergetriebeöl "
    "(RX 300 4WD, RX 350, RX 350L) Alle 30.000 km / 4 Jahre Vorderachsdifferenzialöl "
    "(LS 500, LS 500h 4WD) Alle 30.000 km / 4 Jahre Hinterachsdifferenzialöl "
    "(außer ES 300h, NX 350h, NX 450h+, RX 300 2WD, RX 450h, RX 450hL, UX 200, "
    "UX 250h) Alle 30.000 km / 4 Jahre ZUSÄTZLICHE INSPEKTIONEN "
    "Nebenaggregat-Antriebsriemen (außer ES 300h, UX 250h, NX 350h, NX 450h+) "
    "Alle 105.000 km / 6 Jahre Danach alle 15.000 km / jährlich Ventilspiel "
    "(Inspektion mit Messfühler, LC 500, RC F) Alle 90.000 km / 6 Jahre "
    "Automatikgetriebeöl Alle 60.000 km / 4 Jahre Automatikgetriebe-"
    "Ölkühlerschläuche und -anschlüsse (LC 500, LS 500, RX 350, RX 350L, UX 200) "
    "Alle 45 000 km / 3 Jahre Verteilergetriebeöl (LS 500 und LS 500h 4WD) Alle "
    "45.000 km / 4 Jahre Hinterachsdifferenzialöl (NX 350h 4WD, NX 450h+, RX 450h, "
    "RX 450hL, UX 250h 4WD) Alle 45 000 km / 3 Jahre Vorderachsdifferenzialöl "
    "(ES 300h, NX 350h, NX 450h+, RX 300, RX 350, RX 350L, RX 450h, RX 450hL, "
    "UX 200, UX 250h) Alle 60.000 km / 4 Jahre Gelenkwellenschrauben (RX 300 4WD, "
    "RX 350, RX 350L) Alle 30.000 km / 4 Jahre nachziehen Aktivkohlebehälter Alle "
    "45.000 km / 2 Jahre Alle 90.000 km*3 Unterdruckpumpe für Bremskraftverstärker "
    "(LS 500, RX 300, RX 350, RX 350L, UX 200) Alle 195,000 km *3: nur für "
    "Finnland, Norwegen und Schweden 17 LEXUS CARE Für UX 300e Lexus "
    "Care-Komplettservice: alle 30.000 km oder 2 Jahre, je nachdem, was zuerst "
    "eintritt"
)
_SICHERHEIT_TREFFER = treffer(
    "https://tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf",
    "Lexus Garantie- und Serviceheft (Schweiz, Deutsch)", _SICHERHEIT_BLOCK)

# Dieselbe Quelle, aber mit der ENGLISCHEN Schreibweise ("differential" statt
# "differenzial") an genau den vier Stellen -- NUR die Schreibweise geändert,
# sonst byte-identisch. Belegt die Äquivalenz GENERISCH, nicht nur für die
# deutsche Schreibweise des realen Dokuments.
_SICHERHEIT_BLOCK_EN = _SICHERHEIT_BLOCK.replace("differenzial", "differential")
_SICHERHEIT_TREFFER_EN = treffer(
    "https://tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf",
    "Lexus Garantie- und Serviceheft (Schweiz, Deutsch)", _SICHERHEIT_BLOCK_EN)

_FITNESS_BLOCK = (
    "Für normale Einsatzbedingungen Erneuern - Inspektion - Nachziehen - Füllen "
    "FITNESS Zwischencheck Hauptservice Motoröl und -filter*1 E E Motorkühlmittel "
    "I Inverter-Kühlmittel (nur Hybrid- und Hybrid Plug-in-Modelle) I "
    "Motor-Ölkühlerschläuche und Schlauchanschlüsse (LC 500h und RC F) I "
    "Luftfilter I I Reinigungsadditiv für Einspritzventile*2 F F "
    "Antriebswellenmanschetten I Antriebswellenschrauben (RX 450h, RX 450hL, "
    "UX 250h 4WD) N N Auspuffrohre und -befestigung I Klimaanlage und Heizung I "
    "Klimaanlagenfilter E E Kraftstofftankdeckel, Kraftstoffleitungen, "
    "Leitungsanschlüsse und Tankdunst-Steuerventil I Karosserieinspektion (siehe "
    "Checkliste für Karosserieinspektion) I Einlassfilter der "
    "Hybridbatteriekühlung (RX 450h, RX 450hL, LS 500h, ES 300h, NX 350h) I I / "
    "E*3 *1:  Bei Fahrzeugen mit Ölwechsel-Managementsystem: Motoröl- und "
    "-ölfilter wechseln (mit Fitness- und Sicherheitskontrollen), wenn die "
    "Ölwechsel-Hinweismeldung erscheint (zumindest alle 15.000 km / 1 Jahr), "
    "falls für Ihr Land zutreffend"
)
_FITNESS_TREFFER = treffer(
    "https://tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf",
    "Lexus Garantie- und Serviceheft (Schweiz, Deutsch)", _FITNESS_BLOCK)


print("=" * 78)
print("TEIL 1) N9 -- Differenzial-/Differential-Erkennung")
print("=" * 78)

# ── 1) Vokabular-Äquivalenz ─────────────────────────────────────────────────
_voc = _bauteil_vokabular()
check("1) 'differenzial' UND 'differential' sind beide im Bauteil-Vokabular "
      "hinterlegt und bilden DIESELBE Komponentenidentität (schluessel) -- "
      "dieselbe Zuordnung wie 'verteilergetriebe'/'achsgetriebe', keine neue "
      "Kategorie",
      _voc.get("differenzial") is not None
      and _voc.get("differenzial") == _voc.get("differential") == "allradantrieb",
      f"differenzial={_voc.get('differenzial')!r} differential={_voc.get('differential')!r}")

# ── 2) Getrennte Fenster (statt Verschmelzung mit Verteilergetriebeöl) ──────
_wvoc = _wartung_vokabular()
_fenster_de = _wartung_tabellen_segmente(_SICHERHEIT_BLOCK, _wvoc)
_fenster_en = _wartung_tabellen_segmente(_SICHERHEIT_BLOCK_EN, _wvoc)
for label, fenster in (("deutsch ('differenzial')", _fenster_de),
                       ("englisch ('differential')", _fenster_en)):
    hinterachs = [f for f in fenster if f.lower().startswith(("hinterachsdifferenzialöl",
                                                              "hinterachsdifferentialöl"))]
    vorderachs = [f for f in fenster if f.lower().startswith(("vorderachsdifferenzialöl",
                                                              "vorderachsdifferentialöl"))]
    check(f"2) {label}: Vorder- UND Hinterachs-Zeile bekommen je ein EIGENES "
          "Fenster, keines trägt noch den vorangehenden "
          "Verteilergetriebeöl-Text",
          len(hinterachs) == 2 and len(vorderachs) == 2
          and all("verteilergetriebeöl" not in f.lower() for f in hinterachs + vorderachs),
          str(hinterachs + vorderachs))

# ── 3) Positivliste vs. "außer"-Liste -- für BEIDE Schreibweisen gleich ─────
for label, quelle in (("deutsch", _SICHERHEIT_TREFFER), ("englisch", _SICHERHEIT_TREFFER_EN)):
    abgelehnt: list[dict] = []
    fakten = _extrahiere_fakten([quelle], "wartung", marke="Lexus", modell="RC F",
                                baujahr=2017, abgelehnt=abgelehnt)
    je_bauteil = {f.bauteil: f for f in fakten}
    bauteile = list(je_bauteil)
    hinterachs_treffer = [b for b in bauteile if b.lower().startswith(("hinterachsdifferenzialöl",
                                                                       "hinterachsdifferentialöl"))]
    check(f"3) {label}) die 'außer ES 300h, ...'-Zeile (RC F NICHT gelistet) "
          "erscheint als eigener Fakt mit ihrem EIGENEN Intervall "
          "(30.000 km / 4 Jahre)",
          len(hinterachs_treffer) == 1 and "30.000" in je_bauteil[hinterachs_treffer[0]].aussage,
          str(hinterachs_treffer))
    check(f"3) {label}) KEINE Vorderachs-Zeile erscheint (beide Varianten "
          "sind Positivlisten ohne RC F)",
          not any(b.lower().startswith(("vorderachsdifferenzialöl", "vorderachsdifferentialöl"))
                  for b in bauteile), str(bauteile))
    check(f"3) {label}) die zweite 'Hinterachs...'-Zeile (Positivliste NX "
          "350h/RX 450h/..., RC F NICHT gelistet) wird korrekt UND ISOLIERT "
          "(ohne den Verteilergetriebeöl-Nachbarn) mit dem Modell-Grund "
          "abgelehnt",
          any(a.get("grund") == "wartung_zeile_fremdes_modell"
              and (a.get("satz") or "").lower().startswith(
                  ("hinterachsdifferenzialöl (nx 350h", "hinterachsdifferentialöl (nx 350h"))
              for a in abgelehnt),
          str([a for a in abgelehnt if "differen" in (a.get("satz") or "").lower()]))

# ── 4) Vollständiger Block (SICHERHEIT + FITNESS) unter wirksamem Limit ─────
abgelehnt_voll: list[dict] = []
fakten_voll = _extrahiere_fakten([_SICHERHEIT_TREFFER, _FITNESS_TREFFER], "wartung",
                                 marke="Lexus", modell="RC F", baujahr=2017,
                                 abgelehnt=abgelehnt_voll)
_bauteile_voll = [f.bauteil for f in fakten_voll]
_limit_voll = [a.get("satz") for a in abgelehnt_voll if a.get("grund") == "wartung_auswahllimit_erreicht"]
print("\n  Ausgewählt (ungeklärte Zielachse):", _bauteile_voll)
print("  Durchs Limit zurückgehalten:", [s[:55] for s in _limit_voll])
check("4) 'Hinterachsdifferenzialöl' (N9-Fix) steht jetzt UNTER den "
      "ausgewählten fünf Fakten -- vorher nie erreichbar",
      "Hinterachsdifferenzialöl" in _bauteile_voll, str(_bauteile_voll))
check("4) genau MAX_FAKTEN_JE_KATEGORIE (5) Fakten sichtbar, der Rest bleibt "
      "mit Grund zurückgehalten statt zu verschwinden -- nicht jeder "
      "korrekt extrahierte Kandidat wird automatisch sichtbar",
      len(_bauteile_voll) == 5 and len(_limit_voll) >= 1,
      f"ausgewählt={_bauteile_voll} zurückgehalten={len(_limit_voll)}")


print()
print("=" * 78)
print("TEIL 2) Achsentrennung -- Kraftstoff != Elektrifizierung")
print("=" * 78)

# ── 5) Benzin + unbekannte Elektrifizierung: KEIN erfundener ICE-Nachweis ──
_ident_unbestaetigt = WebVehicleIdentity(belegt=False)
_fuel, _pt = _ziel_achsen({"kraftstoff": "Benzin", "motor": "5.0 V8, Heckantrieb"},
                          _ident_unbestaetigt)
check("5) '_ziel_achsen()' liefert aus 'Benzin'+'5.0 V8' NUR die "
      "Kraftstoffachse ('benzin') -- KEINE Antriebsart-Achse (kein "
      "erfundenes 'ICE' aus Hubraum/Zylindern oder dem Kraftstoffwort allein)",
      _fuel == "benzin" and _pt is None, f"fuel={_fuel!r} powertrains={_pt!r}")
check("5) 'scope_passt(\"phev\", fuel=\"benzin\", powertrains=None)' bleibt "
      "UNKLAR ('None'), wird NICHT zu 'False' umgedeutet -- Benzin allein "
      "widerlegt eine Hybrid-only-Bedingung nicht",
      scope_passt("phev", _fuel, _pt) is None)
abgelehnt_5: list[dict] = []
fakten_5 = _extrahiere_fakten([_SICHERHEIT_TREFFER], "wartung", marke="Lexus", modell="RC F",
                              baujahr=2017, abgelehnt=abgelehnt_5, fuel_ziel=_fuel, powertrains_ziel=_pt)
# N7-Nachtrag (ENFAL-Testabsicherung, 2026-11): eine ungeklärte technische
# Bedingung rangiert seit diesem Fix bewusst unter einer uneingeschränkt
# gültigen Zeile gleicher Confidence -- "Inverter-Kühlmittel" verliert in
# DIESEM Block den letzten Platz an "Motorkühlmittel" (Auswahllimit, NICHT
# das technische Gate). Entscheidend bleibt: kein unbegründeter AUSSCHLUSS
# (das technische Gate lehnt die Zeile nicht ab).
check("5) Folge am echten Block: 'Inverter-Kühlmittel' (Hybrid-only) erzeugt bei "
      "Benzin+unbekannter Elektrifizierung KEINEN Wartungspunkt (N7-Wartungsauswahl: "
      "unbekannt gilt nicht als bestätigt) -- Grund 'wartung_technische_bedingung_offen', "
      "NICHT 'widerspricht' (nicht widerlegt)",
      not any(f.bauteil == "Inverter-Kühlmittel" for f in fakten_5)
      and any("inverter" in a.get("satz", "").lower()
              and a.get("grund") == "wartung_technische_bedingung_offen" for a in abgelehnt_5)
      and not any(a.get("grund") == "wartung_zeile_technische_bedingung_widerspricht"
                  for a in abgelehnt_5),
      f"fakten={[f.bauteil for f in fakten_5]!r} abgelehnt={abgelehnt_5!r}")

# ── 6) Benzin-Hybridziel vs. nachweislich nicht-hybrides Ziel ──────────────
# Über die REALISTISCHE Quelle einer bestätigten Identität (nicht nur
# manuell konstruierte frozensets) -- `identitaet.powertrain` wird exakt so
# gesetzt, wie `_identitaets_claims()`/`konsens()` es im echten Pfad täten
# (kanonischer kraftstoff_powertrain-Wert).
_ident_hybrid = WebVehicleIdentity(belegt=True, kraftstoff="benzin", powertrain="PHEV")
_fuel_h, _pt_h = _ziel_achsen({"kraftstoff": "Benzin", "motor": "5.0 V8"}, _ident_hybrid)
check("6) bestätigte Identität mit powertrain='PHEV' überschreibt die bloße "
      "Nutzerangabe -- Achse wird gesetzt, nicht weiter 'ungeklärt'",
      _pt_h == frozenset({"PHEV"}), f"powertrains={_pt_h!r}")
abgelehnt_6a: list[dict] = []
fakten_6a = _extrahiere_fakten([_SICHERHEIT_TREFFER], "wartung", marke="Lexus", modell="RC F",
                               baujahr=2017, abgelehnt=abgelehnt_6a,
                               fuel_ziel=_fuel_h, powertrains_ziel=_pt_h)
check("6) Benzin-Hybridziel (PHEV): 'Inverter-Kühlmittel' bleibt ein "
      "gültiger Fakt",
      any(f.bauteil == "Inverter-Kühlmittel" for f in fakten_6a),
      str([f.bauteil for f in fakten_6a]))

_ident_nicht_hybrid = WebVehicleIdentity(belegt=True, kraftstoff="benzin", powertrain="ICE")
_fuel_i, _pt_i = _ziel_achsen({"kraftstoff": "Benzin", "motor": "5.0 V8"}, _ident_nicht_hybrid)
check("6) bestätigte Identität mit powertrain='ICE' setzt die Achse auf "
      "ICE -- NICHT aus Benzin/V8 selbst abgeleitet, sondern aus der "
      "ausdrücklichen Identitätsbestätigung",
      _pt_i == frozenset({"ICE"}), f"powertrains={_pt_i!r}")
abgelehnt_6b: list[dict] = []
fakten_6b = _extrahiere_fakten([_SICHERHEIT_TREFFER], "wartung", marke="Lexus", modell="RC F",
                               baujahr=2017, abgelehnt=abgelehnt_6b,
                               fuel_ziel=_fuel_i, powertrains_ziel=_pt_i)
check("6) nachweislich nicht-hybrides Ziel (ICE bestätigt): "
      "'Inverter-Kühlmittel' erzeugt KEINEN Fakt mehr",
      not any(f.bauteil == "Inverter-Kühlmittel" for f in fakten_6b),
      str([f.bauteil for f in fakten_6b]))
check("6) ... und wird mit dem technischen (nicht dem Modell-) Grund "
      "abgelehnt",
      any(a.get("grund") == "wartung_zeile_technische_bedingung_widerspricht"
          and "inverter" in (a.get("satz") or "").lower() for a in abgelehnt_6b))


if _FEHLER:
    print(f"\n{len(_FEHLER)} FEHLER (Teil 1+2):")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nALLE TEIL-1/2-TESTS GRUEN\n")


# ══════════════════════════════════════════════════════════════════════════
# TEIL 3 -- vollständiger run_kaufcheck-Replay (DB-Miss an der schmalen
# Katalog-Lookup-Grenze simuliert, echte gesicherte Texte, Provider offline)
# ══════════════════════════════════════════════════════════════════════════

from app import kaufcheck as kc
from app.models import KaufCheckRequest, KaufCheckResponse
from app.technical_research import FixtureTechnicalResearchProvider, recherchiere_technisch

ROLLING_HU = treffer(
    "https://shop.rolling.hu/catalog/SCHAEFFLER/lexus-rc-c1-f-usc10",
    "LEXUS RC (_C1_) F (USC10_) Part list - Rolling Kft. webshop",
    "SCHAEFFLER - LEXUS RC (_C1_) F (USC10_). Manufacturer LEXUS. Model RC (_C1_). "
    "Type F (USC10_). Construction Year from 08.2014. Engine Output 330kW / 449HP.")
ADAC_ULTIMATE = treffer(
    "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/",
    "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25): Technische Daten, "
    "Bilder, Preise | ADAC",
    "Marke Lexus Modell RC F Typ Ultimate Edition Automatik Baureihe RC (UXC1) "
    "(12/14 - 06/25) Herstellerinterne Baureihenbezeichnung UXC1 Modellstart 08/24 "
    "Modellende 06/25 Baureihenstart 12/14 Baureihenende 06/25 Motorcode 2UR-GSE "
    "Leistung maximal in PS 464 Antriebsart Hinterrad Getriebeart Automatikgetriebe "
    "Kraftstoffart Super Hubraum 4969 ccm")
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
    "477 PS. Hubraum 1998 cm3 bis 4969 cm3.")
# SYNTHETISCHE TEST-FIXTURE (klar markiert, keine echte Veröffentlichung von
# Lexus/Toyota -- gleiches Muster wie test_replay_fortsetzung_abnahme.py):
# belegt die Antriebsart-Achse (ICE) über das generische "Verbrenner"-Wort,
# das app/kraftstoff_powertrain.py für JEDEN Hersteller auswertet.
LEXUS_SYNTHETIC_ICE = treffer(
    "https://newsroom.lexus.eu/rc-f-antriebsart",
    "Lexus RC F: Antriebsart (SYNTHETISCHE TEST-FIXTURE)",
    "[SYNTHETISCH, nicht von lexus.eu übernommen] Der Lexus RC F wird "
    "ausschließlich als reiner Verbrenner ohne jede Hybridisierung angeboten.")


def _db_miss_stub(marke, modell, baujahr):
    """Simuliert einen DB-Miss AUSSCHLIESSLICH an der schmalen Katalog-Lookup-
    Funktion -- exakt die reale 'kein Treffer'-Rückgabeform (gegen eine echte
    unbekannte Marke verifiziert). Keine echte DB-Zeile wird gelesen oder
    geändert."""
    return None, {"match_art": "no_match", "konfidenz": "niedrig", "belastbar": False,
                  "fehlende_angabe": "simulierter DB-Miss, Testdouble"}


async def _lauf(req: KaufCheckRequest, web_fixture: dict) -> tuple[KaufCheckResponse, dict]:
    orig = (kc.call_gemini_json, kc.recherchiere_technisch, kc.tavily_search_with_fallback,
            kc.marktpreis_recherche_moeglich, kc.vertiefe_marktrecherche,
            kc.find_baureihe_mit_vertrauen)

    async def gemini(system, user):
        return {"bericht": "Laut Inserat unfallfrei. KAUFEN NACH BESICHTIGUNG.",
                "empfehlung": "kaufen_nach_besichtigung", "risiko_evidence_ids": [],
                "empfehlung_evidence_ids": []}

    async def verboten(*a, **k):
        raise AssertionError("Marktprovider darf nicht laufen -- kein Netzwerk/Provider-Aufruf erlaubt")

    prov = FixtureTechnicalResearchProvider(web_fixture)
    ausgeloest = {"lief": False}

    async def technik(r, br, info, brg, mm, provider=None):
        res = await recherchiere_technisch(r, br, info, brg, mm, provider=prov)
        ausgeloest["lief"] = res is not None
        return res

    kc.call_gemini_json, kc.recherchiere_technisch = gemini, technik
    kc.tavily_search_with_fallback, kc.vertiefe_marktrecherche = verboten, verboten
    kc.marktpreis_recherche_moeglich = lambda *a: False
    kc.find_baureihe_mit_vertrauen = _db_miss_stub
    try:
        res = KaufCheckResponse(**await kc.run_kaufcheck(req))
        return res, ausgeloest
    finally:
        (kc.call_gemini_json, kc.recherchiere_technisch, kc.tavily_search_with_fallback,
         kc.marktpreis_recherche_moeglich, kc.vertiefe_marktrecherche,
         kc.find_baureihe_mit_vertrauen) = orig


async def _teil_3() -> None:
    print("=" * 78)
    print("TEIL 3) vollständiger run_kaufcheck-Replay mit simuliertem DB-Miss")
    print("=" * 78)

    r, ausgeloest = await _lauf(
        KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000,
                         motor="5.0 V8", kraftstoff="Benzin", leistung_ps=477,
                         getriebe="Automatik", antrieb="Heck", ausstattung=[]),
        web_fixture={
            "identitaet": [ROLLING_HU, ADAC_ULTIMATE, CARWIKI, AMS, LEXUS_SYNTHETIC_ICE],
            "wartung": [_SICHERHEIT_TREFFER],
            "schwachstelle": [], "rueckruf": [],
        })

    wartung_insights = [i for i in r.insights if i.kategorie == "web_wartung"]
    print(f"  Web-Wartungs-Fallback tatsächlich ausgelöst: {ausgeloest['lief']}")
    for i in wartung_insights:
        print(f"  [Insight] {i.titel} | {i.beschreibung[:80]}")

    check("T3.0) Web-Wartungs-Fallback lief tatsächlich (DB-Miss-Stub griff)",
          ausgeloest["lief"])
    check("T3.1) 'Hinterachsdifferenzialöl' (N9-Fix) erscheint jetzt als "
          "eigener Insight im vollständigen Bericht",
          any("differenzial" in i.titel.lower() for i in wartung_insights),
          str([i.titel for i in wartung_insights]))
    check("T3.2) 'Inverter-Kühlmittel' (Hybrid-only, am synthetisch belegt "
          "nicht-hybriden Ziel) erscheint NICHT als eigener Insight",
          not any("inverter" in i.titel.lower() for i in wartung_insights),
          str([i.titel for i in wartung_insights]))
    check("T3.3) 'Vorderachsdifferenzialöl'/'Bremskraftverstärker' (fremde "
          "Modelllisten) erscheinen NICHT",
          not any(w in i.titel.lower() for i in wartung_insights
                  for w in ("vorderachsdifferenzial", "bremskraftverst")))
    check("T3.4) der Bericht behauptet an keiner Stelle vollständige/"
          "vollumfängliche Auswertung",
          "vollständig ausgewertet" not in r.bericht.lower()
          and "vollumfänglich" not in r.bericht.lower())

    if _FEHLER:
        print(f"\n{len(_FEHLER)} FEHLER (Teil 3):")
        for f in _FEHLER[-10:]:
            print(f"  - {f}")
        raise SystemExit(1)
    print("\nTEIL 3 GRUEN")


asyncio.run(_teil_3())
print("\nALLE TESTS GRUEN (Teil 1 + Teil 2 + Teil 3)")
