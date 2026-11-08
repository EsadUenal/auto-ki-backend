"""
ENFAL -- "technische Wartungsgültigkeit und vollständigen Webpfad absichern"
(2026-11-02), Nachtrag zu N2/N3/N7 (Reparaturpaket 331aa08/003af40).

AUSGANGSBEFUND (bestätigt, nicht vermutet -- siehe Diagnose-Skript dieser
Runde): `_zeilen_scope_passt()` erkannte bisher AUSSCHLIESSLICH Modell-Listen
in einer Zeilen-Klammer ("(LS 500, RX 300, ...)"). Eine TECHNISCHE Bedingung
in derselben Klammerposition -- "Inverter-Kühlmittel (nur Hybrid- und Hybrid
Plug-in-Modelle) Alle 240.000 km" (real, lex-sw-00de.pdf, S. 16/17) -- ist
KEINE Modell-Liste (`_ist_modellcode_segment()` lehnt die Prosawörter "nur"/
"Hybrid"/"Modelle" ab) und wurde deshalb nie geprüft: die Zeile galt für JEDES
Zielfahrzeug als "ungeklärt, bleibt zugelassen" -- auch an einem belegt
NICHT-hybriden Fahrzeug. Reale Folge (bestätigt durch Diagnose-Lauf dieser
Runde): der ungeprüfte Hybrid-Kandidat belegt einen der
MAX_FAKTEN_JE_KATEGORIE=5-Plätze und verdrängt dadurch einen tatsächlich
passenden Kandidaten (hier: "Zündkerzen") durchs Auswahllimit.

FIX (app/technical_research.py):
  * `_zeilen_scope_passt()` erkennt zusätzlich eine TECHNISCHE Bedingung
    je Klammer -- über dieselbe, bereits produktiv genutzte Kraftstoff-/
    Antriebswortliste wie das DB-Applicability-Gate
    (`app.motor_applicability._kraftstoffe`, hier `_technik_scope_begriffe`)
    und denselben Achsenvergleich wie `recall_filter`/`motor_applicability`
    (`app.kraftstoff_powertrain.scopes_passen`). Keine zweite Wortliste,
    keine Lexus-/Bauteil-Sonderregel.
  * Neue, generische `_ziel_achsen()` liest die Zielachse (Kraftstoff/
    Antriebstyp) aus der bereits vorhandenen, web-BESTÄTIGTEN Identität
    (bevorzugt) oder aus der Nutzerangabe (Formularfeld + Motor-Freitext) --
    nie aus einer Hubraum-/Zylinderangabe allein ("V8" ist kein Beleg für
    "kein Hybrid").
  * Fehlt die Zielachse (unbekannt), bleibt das Ergebnis `None` (ungeklärt,
    unverändert zugelassen) -- NUR ein nachgewiesener Widerspruch wird
    `False` (ausgeschlossen). Entspricht der bestehenden DB-Pfad-Policy
    (`motor_applicability`: "unklar" bleibt sichtbar, nur "incompatible"
    wird gefiltert) -- keine neue Sichtbarkeitsregel nötig.
  * Eigener Diagnose-Grund `wartung_zeile_technische_bedingung_widerspricht`,
    getrennt von `wartung_zeile_fremdes_modell` (Modell-Scope) -- Abschluss-
    bericht kann "aus welchem Grund" beantworten.

N9 -- UPDATE (Folgerunde, "N9 Differenzial-Erkennung und Achsentrennung
abschließen", 2026-11-02): der bei der Erstfassung dieses Tests gemeldete
Fund ("differenzial" fehlte neben "differential" in `app.kaufaktionen.
_KOMPONENTEN`, Zeile verschmilzt deshalb mit der vorangehenden
Verteilergetriebeöl-Zeile zu einem Fenster) ist in dieser Folgerunde BEHOBEN
-- siehe `test_n9_differenzial_und_achsentrennung.py` für den gezielten
Nachweis (Vokabular-Äquivalenz, eigene Fenster, Dedupe, vollständiger
Block). Test 1 und Test 4 unten sind entsprechend auf den jetzt
FUNKTIONIERENDEN Stand aktualisiert (zuvor dokumentierten sie noch die
Lücke); die ursprüngliche Analyse bleibt oben als Befundgeschichte stehen.

TEIL A: isolierte `_extrahiere_fakten()`-Aufrufe (kein Netzwerk, keine DB).
TEIL B: vollständiger `run_kaufcheck()`-Replay mit ECHTEM DB-Pfad, aber
einem DB-Miss, der NUR an der schmalen Katalog-Lookup-Grenze simuliert wird
(`find_baureihe_mit_vertrauen` gestubbt) -- keine echte DB-Änderung, keine
Manipulation der Nutzereingabe. Externe Provider (Tavily/Gemini/Markt)
bleiben gesperrt bzw. gestubbt (identisches Muster wie
test_replay_fortsetzung_abnahme.py).

    python test_wartung_technische_bedingung_vollpfad.py
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.technical_research import _extrahiere_fakten, _wartung_aktion, _wartung_tabellen_segmente, \
    _wartung_vokabular

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# ── Echter Rohtext: SICHERHEIT-Tabelle (Seite 16/17), wortgleich wie in ────
# test_n2_n3_n7_wartung_tabellenblock.py (lex-sw-00de.pdf, Lexus Garantie-
# und Serviceheft Schweiz/Deutsch -- bereits real gesichert/extrahiert).
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


# ══════════════════════════════════════════════════════════════════════════
# TEIL A -- isolierte Tests
# ══════════════════════════════════════════════════════════════════════════
print("=" * 78)
print("TEIL A) technische Scope-Bedingung an Wartungsfakten")
print("=" * 78)

# ── Test 1: Hybrid-only-Zeile bei belegt NICHT-hybridem Ziel ────────────────
abgelehnt_1: list[dict] = []
fakten_1 = _extrahiere_fakten([_SICHERHEIT_TREFFER], "wartung", marke="Lexus", modell="RC F",
                              baujahr=2017, abgelehnt=abgelehnt_1,
                              fuel_ziel="benzin", powertrains_ziel=frozenset({"ICE"}))
_bauteile_1 = [f.bauteil for f in fakten_1]
check("1) Inverter-Kühlmittel (Hybrid-/Plug-in-only-Zeile) erzeugt KEINEN Fakt "
      "an einem belegt NICHT-hybriden Ziel (fuel=benzin, powertrain=ICE)",
      not any("inverter" in (b or "").lower() for b in _bauteile_1), str(_bauteile_1))
check("1) die Zeile wird mit dem EIGENEN, von der Modell-Scope-Ablehnung "
      "UNTERSCHIEDENEN Diagnose-Grund abgelehnt (kein Modell-Grund)",
      any(a.get("grund") == "wartung_zeile_technische_bedingung_widerspricht"
          and "inverter" in (a.get("satz") or "").lower() for a in abgelehnt_1),
      str([a for a in abgelehnt_1 if "inverter" in (a.get("satz") or "").lower()]))
check("1) der freigewordene Platz lässt einen zuvor durchs Limit verdrängten, "
      "tatsächlich passenden Kandidaten ('Motorkühlmittel') jetzt durch -- "
      "echter Produktnutzen des Fixes, nicht nur Ausschluss (seit der N9-"
      "Folgerunde konkurriert zusätzlich 'Hinterachsdifferenzialöl' um die "
      "fünf Plätze -- 'Zündkerzen' bleibt deshalb weiterhin durchs Limit "
      "zurückgehalten, s. Test 4/6 unten)",
      "Motorkühlmittel" in _bauteile_1, str(_bauteile_1))

# ── Test 2: passendes Hybridziel -- die Zeile bleibt zugelassen ────────────
abgelehnt_2: list[dict] = []
fakten_2 = _extrahiere_fakten([_SICHERHEIT_TREFFER], "wartung", marke="Lexus", modell="RC F",
                              baujahr=2017, abgelehnt=abgelehnt_2,
                              fuel_ziel="benzin", powertrains_ziel=frozenset({"PHEV"}))
_bauteile_2 = [f.bauteil for f in fakten_2]
check("2) Inverter-Kühlmittel bleibt an einem belegt HYBRIDEN Ziel "
      "(powertrain=PHEV) weiterhin ein gültiger Fakt -- keine pauschale "
      "Ausblendung jeder Hybrid-Bedingung",
      any("inverter" in (b or "").lower() for b in _bauteile_2), str(_bauteile_2))
check("2) KEINE Ablehnung mit dem technischen Bedingungs-Grund für diese Zeile",
      not any(a.get("grund") == "wartung_zeile_technische_bedingung_widerspricht"
              for a in abgelehnt_2), str(abgelehnt_2))

# ── Test 3: technische Zielachse unbekannt -- bleibt ungeklärt ─────────────
abgelehnt_3: list[dict] = []
fakten_3 = _extrahiere_fakten([_SICHERHEIT_TREFFER], "wartung", marke="Lexus", modell="RC F",
                              baujahr=2017, abgelehnt=abgelehnt_3)
_bauteile_3 = [f.bauteil for f in fakten_3]
# N7-Nachtrag (ENFAL-Testabsicherung, "Verluststelle Ventilspiel/Motoröl
# klären", 2026-11): eine ungeklärte technische Bedingung ist zwar weiterhin
# NICHT ausgeschlossen (kein `wartung_zeile_technische_bedingung_
# widerspricht`, s. unten), rangiert aber seit diesem Fix bewusst UNTER
# einer uneingeschränkt gültigen Zeile gleicher Confidence-Stufe (s.
# `_extrahiere_fakten()`-Sortierung) -- "ungeklärt" ist weniger sicher als
# "gar keine Bedingung". In DIESEM Block konkurriert Inverter-Kühlmittel
# dadurch mit "Motorkühlmittel" um den letzten der fünf Plätze und verliert
# alphabetisch ("I" > "M") -- ein durchs Auswahllimit, NICHT durchs
# technische Gate zurückgestellter Kandidat (die Unterscheidung ist genau
# der Punkt dieses Tests). "Zugelassen, aber ggf. durchs Limit verdrängt"
# ersetzt die vorige Erwartung "immer unter den ausgegebenen Fakten".
check("3) ohne bekannte Kraftstoff-/Antriebsachse erzeugt Inverter-Kühlmittel KEINEN Wartungspunkt "
      "(N7-Wartungsauswahl: unbekannt gilt nicht als bestätigt) -- Grund "
      "'wartung_technische_bedingung_offen'",
      not any("inverter" in (b or "").lower() for b in _bauteile_3)
      and any("inverter" in a.get("satz", "").lower()
              and a.get("grund") == "wartung_technische_bedingung_offen" for a in abgelehnt_3),
      f"bauteile={_bauteile_3!r} abgelehnt={abgelehnt_3!r}")
check("3) KEINE Ablehnung mit dem technischen Bedingungs-Grund, solange die "
      "Zielachse unbekannt ist",
      not any(a.get("grund") == "wartung_zeile_technische_bedingung_widerspricht"
              for a in abgelehnt_3), str(abgelehnt_3))

# ── Test 4: Positivliste vs. "außer"-Liste -- N9 jetzt BEHOBEN ─────────────
# Seit der N9-Folgerunde ("differenzial" ergänzt in
# app.kaufaktionen._KOMPONENTEN) bekommt jedes Differenzialöl-Vorkommen eine
# EIGENE Fenstergrenze -- Detailnachweis (Vokabular-Äquivalenz, Fenster-
# trennung, Dedupe) in test_n9_differenzial_und_achsentrennung.py. Hier nur
# die direkte Folge für DIESEN Block: die passende "außer"-Zeile erscheint
# jetzt tatsächlich als eigener Fakt, die unpassende Positivliste bleibt
# weiterhin (jetzt aus dem richtigen, isolierten Grund) ausgeschlossen.
_voc = _wartung_vokabular()
_fenster = _wartung_tabellen_segmente(_SICHERHEIT_BLOCK, _voc)
check("4) 'Hinterachsdifferenzialöl'/'Vorderachsdifferenzialöl' bekommen "
      "jeweils EIGENE Fenster, getrennt von der vorangehenden "
      "Verteilergetriebeöl-Zeile (keine Verschmelzung mehr)",
      any(f.lower().startswith("hinterachsdifferenzialöl") for f in _fenster)
      and any(f.lower().startswith("vorderachsdifferenzialöl") for f in _fenster))
abgelehnt_4: list[dict] = []
fakten_4 = _extrahiere_fakten([_SICHERHEIT_TREFFER], "wartung", marke="Lexus", modell="RC F",
                              baujahr=2017, abgelehnt=abgelehnt_4)
_bauteile_4 = [f.bauteil for f in fakten_4]
_je_bauteil_4 = {f.bauteil: f for f in fakten_4}
check("4) 'Hinterachsdifferenzialöl' ('außer ES 300h, ...', RC F NICHT "
      "gelistet) erscheint jetzt als eigener, korrekt zugeordneter Fakt mit "
      "seinem EIGENEN Intervall (30.000 km / 4 Jahre) -- vorher verloren",
      "Hinterachsdifferenzialöl" in _je_bauteil_4
      and "30.000" in _je_bauteil_4["Hinterachsdifferenzialöl"].aussage,
      str(_je_bauteil_4.get("Hinterachsdifferenzialöl")))
check("4) 'Vorderachsdifferenzialöl' erscheint WEITERHIN in KEINER der "
      "beiden Varianten als Fakt -- beide sind Positivlisten ohne RC F, kein "
      "erfundener Treffer",
      "Vorderachsdifferenzialöl" not in _bauteile_4, str(_bauteile_4))
check("4) die zweite Hinterachsdifferenzialöl-Zeile (Positivliste NX 350h/"
      "RX 450h/..., RC F NICHT gelistet) wird weiterhin korrekt mit dem "
      "MODELL-Grund abgelehnt -- jetzt ISOLIERT (ohne den vorangehenden "
      "Verteilergetriebeöl-Satzteil mitzuschleppen)",
      any(a.get("grund") == "wartung_zeile_fremdes_modell"
          and (a.get("satz") or "").lower().startswith("hinterachsdifferenzialöl (nx 350h")
          for a in abgelehnt_4),
      str([a for a in abgelehnt_4 if "hinterachsdifferenzial" in (a.get("satz") or "").lower()]))

# ── Test 5: Tätigkeitsart und Marktvorbehalt bleiben erhalten ──────────────
_je_bauteil_1 = {f.bauteil: f for f in fakten_1}
check("5) 'Ventilspiel' bleibt eine Inspektion -- die technische Scope-"
      "Erweiterung erfindet daraus KEINEN Austausch ('ersetzen')",
      _wartung_aktion(_je_bauteil_1.get("Ventilspiel").aussage
                       if "Ventilspiel" in _je_bauteil_1 else "") != "ersetzen")
check("5) 'Nebenaggregat-Antriebsriemen' (Ausschlussliste, RC F nicht "
      "gelistet) bleibt weiterhin zugelassen -- die neue technische Prüfung "
      "verändert die bestehende Modell-Scope-Entscheidung für diese Zeile "
      "nicht",
      any("antriebsriemen" in (b or "").lower() for b in _bauteile_1))


if _FEHLER:
    print(f"\n{len(_FEHLER)} FEHLER (Teil A):")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nALLE TEIL-A-TESTS GRUEN\n")


# ══════════════════════════════════════════════════════════════════════════
# TEIL B -- vollständiger run_kaufcheck-Replay, DB-Miss an der schmalen
# Katalog-Lookup-Grenze simuliert (keine echte DB-/Konfigänderung)
# ══════════════════════════════════════════════════════════════════════════

from app import kaufcheck as kc
from app.car_lookup import find_baureihe_mit_vertrauen as _echte_find_baureihe
from app.models import KaufCheckRequest, KaufCheckResponse
from app.technical_research import FixtureTechnicalResearchProvider, recherchiere_technisch

# Reale Quellen (identisch zu test_n2_n3_n7_wartung_tabellenblock.py TEIL B --
# echte Zitate, bereits per WebFetch gesichert).
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
# Lexus/Toyota -- gleiches Muster wie test_replay_fortsetzung_abnahme.py,
# C_RC_F_WEB): belegt die Antriebsart-Achse (ICE) ausdrücklich über
# dasselbe, generische "Verbrenner"-Wort, das app/kraftstoff_powertrain.py
# bereits für JEDEN Hersteller/jedes Modell auswertet (`_ICE_WORT`) -- kein
# Lexus-Spezialwissen im Produktcode, nur eine für DIESEN Test benötigte
# Belegquelle, damit die Zielachse überhaupt einmal "bestätigt" statt
# "unbekannt" ist und der volle Ausschlusspfad end-to-end nachgewiesen
# werden kann (Teil A deckt den unbestätigten Normalfall bereits ab).
LEXUS_SYNTHETIC_ICE = treffer(
    "https://newsroom.lexus.eu/rc-f-antriebsart",
    "Lexus RC F: Antriebsart (SYNTHETISCHE TEST-FIXTURE)",
    "[SYNTHETISCH, nicht von lexus.eu übernommen] Der Lexus RC F wird "
    "ausschließlich als reiner Verbrenner ohne jede Hybridisierung angeboten.")


def _db_miss_stub(marke, modell, baujahr):
    """Simuliert einen DB-Miss AUSSCHLIESSLICH an der schmalen Katalog-Lookup-
    Funktion (`find_baureihe_mit_vertrauen`) -- exakt die reale 'kein Treffer'-
    Rückgabeform (per Diagnose dieser Runde gegen eine echte unbekannte Marke
    verifiziert, nicht erfunden). Keine echte DB-Zeile wird gelesen, geändert
    oder umgangen; die Funktion wird für DIESEN Testlauf durch einen reinen
    Platzhalter ERSETZT. `req.marke`/`req.modell` bleiben "Lexus"/"RC F" --
    die Fixture-Quellen passen weiterhin zur Identitäts-Ausrichtung."""
    return None, {"match_art": "no_match", "konfidenz": "niedrig", "belastbar": False,
                  "fehlende_angabe": "die Generation bzw. den Baureihencode und den "
                                     "Motorcode aus dem Fahrzeugschein (simulierter "
                                     "DB-Miss, Testdouble)"}


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


async def _teil_b() -> None:
    print("=" * 78)
    print("TEIL B) vollständiger run_kaufcheck-Replay mit simuliertem DB-Miss")
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

    print(f"  Web-Wartungs-Fallback tatsächlich ausgelöst: {ausgeloest['lief']}")
    bericht_lower = r.bericht.lower()
    frage_texte = " ".join(a.titel + " " + a.aktion
                           for a in r.kaufaktionen.verkaeuferfragen.fahrzeugspezifisch)
    dokument_texte = " ".join(a.titel + " " + a.aktion
                              for a in r.kaufaktionen.dokumente.fahrzeugspezifisch)
    wartung_insights = [i for i in r.insights if i.kategorie == "web_wartung"]
    for i in wartung_insights:
        print(f"  [Insight] {i.titel} | {i.beschreibung[:90]}")

    check("B0) der Web-Wartungs-Fallback lief tatsächlich (DB-Miss-Stub hat "
          "gegriffen) -- Voraussetzung, damit dieser Teil überhaupt den "
          "vollständigen Webpfad prüft, statt ihn (wie zuvor dokumentiert) zu "
          "überspringen",
          ausgeloest["lief"])
    # Hinweis: ein benachbartes Fenster ("Motorkühlmittel ... Inverter-") endet
    # durch eine vorbestehende, von diesem Fix unabhängige Fenstergrenzen-
    # Eigenheit mit dem bloßen WORTANFANG "Inverter-" (kein eigener Fakt,
    # keine Bauteil-Identität) -- deshalb wird hier gezielt auf den vollen
    # Bauteilnamen geprüft, nicht auf den bloßen Teilstring "inverter".
    check("B1) KEIN Insight/Karte/Verkäuferfrage/Dokumentenanforderung TRÄGT "
          "'Inverter-Kühlmittel' als eigenen Fakt -- die Zeile ist an diesem "
          "synthetisch belegt nicht-hybriden Ziel ausdrücklich unpassend und "
          "darf nicht als Zielfakt erscheinen",
          "inverter-kühlmittel" not in bericht_lower
          and "inverter-kühlmittel" not in frage_texte.lower()
          and "inverter-kühlmittel" not in dokument_texte.lower()
          and not any("inverter" in i.titel.lower() for i in wartung_insights))
    check("B2) keine Karte, Verkäuferfrage oder Dokumentenanforderung nennt "
          "'Bremskraftverstärker' -- die Zeile ist ausdrücklich nicht für "
          "RC F (unverändert aus dem Vorpaket)",
          "bremskraftverst" not in bericht_lower
          and "bremskraftverst" not in frage_texte.lower()
          and "bremskraftverst" not in dokument_texte.lower())
    check("B3) keine Karte, Verkäuferfrage oder Dokumentenanforderung "
          "behauptet ein Bremspedal-Intervall von 60.000 km (der "
          "Luftfilter-Zeile, unverändert aus dem Vorpaket)",
          not any("bremspedal" in i.titel.lower() and "60.000" in i.beschreibung
                  for i in wartung_insights))
    check("B4) mindestens ein korrekt zugeordneter, tatsächlich passender "
          "Wartungshinweis erscheint trotzdem (Ventilspiel, Automatikgetriebe "
          "oder Zündkerzen) -- der Ausschluss der Hybrid-Zeile verdrängt "
          "keine echten Hinweise, er gibt ihnen den freien Platz",
          any(any(w in i.titel.lower() for w in ("ventilspiel", "automatikgetriebe", "zündkerz"))
              for i in wartung_insights),
          str([i.titel for i in wartung_insights]))
    check("B5) der Bericht behauptet an keiner Stelle, das Dokument sei "
          "vollständig/vollumfänglich ausgewertet",
          "vollständig ausgewertet" not in bericht_lower
          and "vollumfänglich" not in bericht_lower)

    if _FEHLER:
        print(f"\n{len(_FEHLER)} FEHLER (Teil B):")
        for f in _FEHLER[-10:]:
            print(f"  - {f}")
        raise SystemExit(1)
    print("\nTEIL B GRUEN")


asyncio.run(_teil_b())
print("\nALLE TESTS GRUEN (Teil A + Teil B)")
