"""
ENFAL -- Testabsicherung N10/N11 (echter KaufCheck 04.11.2026, Lexus RC F).

N10 (app/technical_research.py): `_ist_modellcode_segment()`/
`_RE_MODELLCODE_SEGMENT` erkannten als Trenner zwischen zwei Codeteilen
("LC 500") bisher NUR das normale Leerzeichen (Zeichenklasse `[ -]`). PDF-/
Web-Extraktion setzt bei kurzen Modellcodes verbreitet ein geschütztes
Leerzeichen (NBSP, U+00A0) oder ein schmales geschütztes Leerzeichen
(U+202F) ein, um dort keinen Zeilenumbruch zuzulassen -- generisches
Extraktionsartefakt, keine Lexus-/Marken-Besonderheit. Traf das zu, erkannte
`_ist_modellcode_segment()` KEINEN Modellcode mehr, `_zeilen_scope_passt()`
fiel auf `None` (ungeklärt) zurück statt auf `False` (fremdes Modell), und
eine Zeile mit einer fremden Positivliste wurde fälschlich übernommen --
offline reproduziert in der letzten Analyserunde. FIX: die Trennzeichen-
klasse akzeptiert jetzt generisch jedes Unicode-Whitespace (`\\s`) statt nur
" ".

WICHTIG (Statusgrenze, unverändert aus dem Analysebericht): dass dieser
Unicode-Fehler GENAU SO im echten Lauf vom 04.11.2026 ausgelöst wurde, ist
OHNE den damaligen Rohtext NICHT bewiesen -- nur offline reproduziert. Dieser
Test sichert die Korrektheit des Mechanismus ab, behauptet aber nicht, den
damaligen Produktionsfehler bitgenau nachgestellt zu haben.

N11 (app/kaufaktionen.py): `_schluessel()` fiel für `_KOMPONENTEN`-Einträge
ohne `je_bauteil` (z.B. "automatikgetriebe") auf die grobe Prüfklasse
zurück -- richtig für Besichtigung/Probefahrt/Schwachstelle (eine gemeinsame
Symptomprüfung mehrerer benannter Teile), falsch für zwei eigenständige,
unterschiedlich befristete Wartungsintervall-Nachweise ("Automatikgetriebeöl"
60.000 km/4 Jahre vs. "Automatikgetriebe-Ölkühlerschläuche und -anschlüsse"
45.000 km/3 Jahre), die dadurch in der Verkäuferfrage/Dokumentenanforderung
unter "Gilt auch für" zusammengelegt wurden. FIX: eine neue, NUR für
Wartungsintervall-Aktionen genutzte `_schluessel_wartungsintervall()`
schlüsselt IMMER bauteilgenau -- unabhängig vom `je_bauteil`-Flag der
geteilten Tabelle, die für Besichtigung/Probefahrt/Schwachstelle unverändert
bleibt.

Deterministisch (alle Provider gestubbt), kein Netzwerk/LLM/bezahlter
Aufruf, isolierte DB (DB-Miss nur am bestehenden Lookup simuliert).

    python scripts/test_isolated.py test_n10_n11_scope_dedup_absicherung.py
"""
import asyncio
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from app import kaufcheck as kc
from app.kaufaktionen import build_kaufaktionen
from app.laufleistung import _wartungshinweise
from app.models import Insight, KaufCheckRequest, KaufCheckResponse, Laufleistungskontext
from app.technical_research import (
    FixtureTechnicalResearchProvider, _extrahiere_fakten, _ist_modellcode_segment,
    _zeilen_scope_passt, recherchiere_technisch,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# ── Echter Rohtext (lex-sw-00de.pdf, S. 16/17) -- wortgleich mit ───────────
# test_wartung_technische_bedingung_vollpfad.py / test_n2_n3_n7_wartung_
# tabellenblock.py, inkl. relevanter Nachbarzeilen (Verteilergetriebeöl,
# Differenzialöl, Ventilspiel, Zündkerzen, Motorkühlmittel).
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
    "Danach alle 90.000 km Verteilergetriebeöl "
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


def _mit_nbsp(text: str, zeichen: str) -> str:
    """Ersetzt in den vier Modellcodes der Ölkühlerschläuche-Klammer das
    normale Leerzeichen durch `zeichen` (NBSP/schmales NBSP) -- NUR dort,
    der Rest des echten Dokumenttexts bleibt unverändert."""
    return (text.replace("LC 500, LS 500, RX 350, RX 350L, UX 200",
                         f"LC{zeichen}500, LS{zeichen}500, RX{zeichen}350, "
                         f"RX{zeichen}350L, UX{zeichen}200"))


_NBSP, _NNBSP = " ", " "

print("=" * 78)
print("TEIL A) N10 -- Unicode-Leerzeichen in Modell-Scope-Klammern (isoliert)")
print("=" * 78)

for bezeichnung, zeichen in (("normales Leerzeichen", " "), ("NBSP U+00A0", _NBSP),
                             ("schmales NBSP U+202F", _NNBSP)):
    block = _mit_nbsp(_SICHERHEIT_BLOCK, zeichen) if zeichen != " " else _SICHERHEIT_BLOCK
    abgelehnt: list[dict] = []
    fakten = _extrahiere_fakten([treffer("https://tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf",
                                         "Lexus Garantie- und Serviceheft (Schweiz, Deutsch)",
                                         block)],
                                "wartung", marke="Lexus", modell="RC F", baujahr=2017,
                                abgelehnt=abgelehnt)
    bauteile = [f.bauteil for f in fakten]
    check(f"A1 ({bezeichnung}): 'Automatikgetriebe-Ölkühlerschläuche' (fremde "
          f"Positivliste ohne RC F) erzeugt KEINEN Fakt",
          not any("lkühlerschläuche" in (b or "") for b in bauteile), str(bauteile))
    check(f"A2 ({bezeichnung}): die Zeile wird mit dem Modell-Grund abgelehnt "
          f"('wartung_zeile_fremdes_modell'), nicht stillschweigend verworfen",
          any(a.get("grund") == "wartung_zeile_fremdes_modell"
              and "lkühlerschläuche" in a.get("satz", "") for a in abgelehnt),
          str([a for a in abgelehnt if "lkühlerschläuche" in a.get("satz", "")]))
    check(f"A3 ({bezeichnung}): 'Automatikgetriebeöl' (kein Scope, gilt für "
          f"jedes Modell) bleibt weiterhin ein gültiger Fakt -- der Fix "
          f"schließt nicht pauschal mehr aus",
          any(b == "Automatikgetriebeöl" for b in bauteile), str(bauteile))
    check(f"A4 ({bezeichnung}): 'Hinterachsdifferenzialöl' (Ausschlussliste "
          f"'außer ES 300h, ...', RC F NICHT gelistet -> gilt für RC F) "
          f"bleibt weiterhin ein gültiger Fakt -- Ausnahmelisten-Semantik bleibt erhalten",
          any(b == "Hinterachsdifferenzialöl" for b in bauteile), str(bauteile))

# Positiv-/Negativkontrollen direkt an `_zeilen_scope_passt()`, unabhängig von
# der Tabellensegmentierung -- isoliert, ob die Mitgliedschaftsprüfung selbst
# (nicht nur der Ausschluss) unter NBSP semantisch korrekt bleibt.
_OELKUEHLER_NBSP = _mit_nbsp(
    "Automatikgetriebe-Ölkühlerschläuche und -anschlüsse (LC 500, LS 500, RX 350, "
    "RX 350L, UX 200) Alle 45 000 km / 3 Jahre", _NBSP)
check("A5 Positivkontrolle: dieselbe NBSP-Klammer lässt ein TATSÄCHLICH "
      "gelistetes Modell ('LC 500') weiterhin zu (True, nicht nur 'nicht False')",
      _zeilen_scope_passt(_OELKUEHLER_NBSP, "LC 500")[0] is True)
check("A6 Negativkontrolle bleibt bestehen: 'RC F' (nicht gelistet) wird "
      "mit NBSP korrekt ausgeschlossen (False)",
      _zeilen_scope_passt(_OELKUEHLER_NBSP, "RC F")[0] is False)
_DIFFERENZIAL_AUSSCHLUSS_NBSP = _mit_nbsp(
    "Hinterachsdifferenzialöl (außer ES 300h, NX 350h, NX 450h+, RX 300 2WD, "
    "RX 450h, RX 450hL, UX 200, UX 250h) Alle 30.000 km / 4 Jahre", _NBSP
).replace("ES 300h", "ES 300h")  # (bereits durch _mit_nbsp ersetzt)
check("A7 Ausnahmeliste mit NBSP: 'RC F' (NICHT in der Ausschlussliste) "
      "bleibt zugelassen (True)",
      _zeilen_scope_passt(_DIFFERENZIAL_AUSSCHLUSS_NBSP, "RC F")[0] is True)
check("A8 Ausnahmeliste mit NBSP: 'ES 300h' (IN der Ausschlussliste) wird "
      "korrekt ausgeschlossen (False)",
      _zeilen_scope_passt(_DIFFERENZIAL_AUSSCHLUSS_NBSP, "ES 300h")[0] is False)
check("A9 '_ist_modellcode_segment()' erkennt 'LC 500' mit NBSP direkt als Modellcode",
      _ist_modellcode_segment(f"LC{_NBSP}500"))
check("A10 '_ist_modellcode_segment()' erkennt 'RX 350L' mit schmalem NBSP direkt als Modellcode",
      _ist_modellcode_segment(f"RX{_NNBSP}350L"))

if _FEHLER:
    print(f"\n{len(_FEHLER)} FEHLER (Teil A):")
    for f in _FEHLER[-10:]:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nTEIL A GRUEN")


# ══════════════════════════════════════════════════════════════════════════
# TEIL B -- N10 über den vorgesehenen Webpfad, DB-Miss kontrolliert simuliert,
# Elektrifizierung wie im echten Lauf UNBEKANNT (keine Hybrid-/ICE-Quelle).
# ══════════════════════════════════════════════════════════════════════════

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
    "Leistung maximal in PS 464 Antriebsart Hinterrad Getriebeart Automatikgetriebe")
CARWIKI = treffer(
    "https://carwiki.de/lexus-rc-hsn-tsn-schluesselnummer/",
    "Lexus RC HSN & TSN | Tabelle aller Schluesselnummern",
    "Uebersicht: Alle Schluesselnummern (HSN/TSN) der Lexus RC Baureihen. "
    "Hersteller: Lexus Fahrzeug: RC Baureihe: RC XC1 Karosserie: Coupe "
    "MODELL HSN SCHLUESSELNUMMER TSN SCHLUESSELNUMMER HUBRAUM LEISTUNG BAUJAHR "
    "Lexus RC F Carbon Automatik 5013 AJM 4969 cm3 477 PS 2014 bis 2025")
AMS = treffer(
    "https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
    "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
    "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Leistung 181 PS bis "
    "477 PS. Hubraum 1998 cm3 bis 4969 cm3.")
# Markeneigene Domain (wie im echten Lauf vom 04.11.2026 "media.lexus.co.uk"/
# "pressroom.lexus.com") -- noetig fuer `_identitaet_belegt()`s Domain-/
# Tier-Schwelle (direkt gegengeprueft: ohne eine markeneigene Domain bleibt
# "belegt" hier False und unterdrueckt JEDE Wartungs-Ausrichtung). Bewusst
# KEINE Kraftstoff-/Hybrid-/Antriebsart-Aussage -- Elektrifizierung bleibt
# dadurch wie im echten Lauf "nicht sicher bekannt".
LEXUS_PRESS_MODELLSEITE = treffer(
    "https://pressroom.lexus.com/models/rc-f/",
    "Lexus RC F -- Models | Lexus Pressroom",
    "Lexus RC F. Engine 2UR-GSE 5.0-liter V8. Horsepower 472 hp. Transmission "
    "8-speed Direct Shift automatic.")
_SICHERHEIT_NBSP_TREFFER = treffer(
    "https://tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf",
    "Lexus Garantie- und Serviceheft (Schweiz, Deutsch)",
    _mit_nbsp(_SICHERHEIT_BLOCK, _NBSP))


def _db_miss_stub(marke, modell, baujahr):
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


async def _teil_b() -> None:
    print("\n" + "=" * 78)
    print("TEIL B) N10 über den vollständigen run_kaufcheck-Webpfad "
          "(DB-Miss simuliert, Elektrifizierung unbekannt)")
    print("=" * 78)

    r, ausgeloest = await _lauf(
        KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000,
                         motor="5.0 V8, Heckantrieb", kraftstoff="Benzin", leistung_ps=477,
                         getriebe="Automatik", antrieb="Heck", ausstattung=[]),
        web_fixture={
            "identitaet": [ROLLING_HU, ADAC_ULTIMATE, CARWIKI, AMS, LEXUS_PRESS_MODELLSEITE],
            "wartung": [_SICHERHEIT_NBSP_TREFFER],
            "schwachstelle": [], "rueckruf": [],
        })

    powertrain = r.vehicle_identity.get("field_evidence", {}).get("powertrain", {}).get("value")
    bericht_lower = r.bericht.lower()
    frage_texte = " ".join(a.titel + " " + a.aktion
                           for a in r.kaufaktionen.verkaeuferfragen.fahrzeugspezifisch)
    dokument_texte = " ".join(a.titel + " " + a.aktion
                              for a in r.kaufaktionen.dokumente.fahrzeugspezifisch)
    wartung_insights = [i for i in r.insights if i.kategorie == "web_wartung"]
    for i in wartung_insights:
        print(f"  [Insight] {i.titel} | {i.beschreibung[:90]}")

    check("B0) der Web-Wartungs-Fallback lief tatsächlich (DB-Miss-Stub hat gegriffen)",
          ausgeloest["lief"])
    check("B1) Elektrifizierung bleibt wie im echten Lauf vom 04.11.2026 "
          "unbekannt (keine Hybrid-/ICE-Quelle in dieser Fixture)",
          powertrain is None, f"powertrain={powertrain!r}")
    check("B2) 'Automatikgetriebe-Ölkühlerschläuche' (fremde Positivliste mit "
          "NBSP, ohne RC F) erzeugt KEINEN WebFakt/Insight",
          not any("lkühlerschläuche" in i.titel.lower() for i in wartung_insights),
          str([i.titel for i in wartung_insights]))
    check("B3) ... und folglich auch KEINE Verkäuferfrage",
          "lkühlerschläuche" not in frage_texte.lower())
    check("B4) ... und folglich auch KEINE Dokumentenanforderung",
          "lkühlerschläuche" not in dokument_texte.lower())
    check("B5) ... und erscheint auch nicht im Fließtext des Berichts",
          "lkühlerschläuche" not in bericht_lower)
    check("B6) 'Automatikgetriebeöl' (korrekt, ohne Modell-Scope) bleibt "
          "weiterhin ein gültiger, sichtbarer Wartungshinweis",
          any("automatikgetriebeöl" in i.titel.lower() for i in wartung_insights),
          str([i.titel for i in wartung_insights]))

    if _FEHLER:
        print(f"\n{len(_FEHLER)} FEHLER (Teil B):")
        for f in _FEHLER[-10:]:
            print(f"  - {f}")
        raise SystemExit(1)
    print("\nTEIL B GRUEN")


asyncio.run(_teil_b())


# ══════════════════════════════════════════════════════════════════════════
# TEIL C -- N11, GEZIELT ISOLIERT von N10: zwei bereits korrekt zugeordnete
# ("belegt gültige"), fachlich unterschiedliche Automatikgetriebe-Wartungs-
# punkte direkt bis zur Aktionsgenerierung -- die Insights werden direkt
# konstruiert (realer Verarbeitungspfad `build_kaufaktionen`, kein
# vorweggenommenes Ergebnis), damit ein eventueller N10-Fehltreffer diesen
# Gruppierungsfehler nicht verdecken kann.
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 78)
print("TEIL C) N11 -- fachlich unterschiedliche Wartungen bleiben getrennt")
print("=" * 78)

INSIGHT_OEL = Insight(
    id="web-wartung-oel", kategorie="web_wartung",
    titel="Automatikgetriebeöl: Wartungsangabe aus der Webrecherche",
    beschreibung="Automatikgetriebeöl Alle 60.000 km / 4 Jahre.",
    confidence="niedrig", bauteil="Automatikgetriebeöl")
INSIGHT_KUEHLER = Insight(
    id="web-wartung-kuehler", kategorie="web_wartung",
    titel="Automatikgetriebe-Ölkühlerschläuche: Wartungsangabe aus der Webrecherche",
    beschreibung=("Automatikgetriebe-Ölkühlerschläuche und -anschlüsse "
                 "Alle 45.000 km / 3 Jahre."),
    confidence="niedrig", bauteil="Automatikgetriebe-Ölkühlerschläuche und -anschlüsse")

REQ_MIN = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000,
                          motor="5.0 V8, Heckantrieb", kraftstoff="Benzin", leistung_ps=477,
                          getriebe="Automatik", antrieb="Heck", ausstattung=[])

# ── C1: nur `_aus_web_evidence()` (kein Laufleistungskontext) ──────────────
aktionen_c1 = build_kaufaktionen(REQ_MIN, None, None, [INSIGHT_OEL, INSIGHT_KUEHLER])
fragen_c1 = aktionen_c1.verkaeuferfragen.fahrzeugspezifisch
dokumente_c1 = aktionen_c1.dokumente.fahrzeugspezifisch
fragen_oel_c1 = [a for a in fragen_c1 if "web-wartung-oel" in a.evidence_ids]
fragen_kuehler_c1 = [a for a in fragen_c1 if "web-wartung-kuehler" in a.evidence_ids]

check("C1a) beide Wartungen erzeugen ZWEI eigenständige Verkäuferfragen "
      "(nicht eine gemeinsame unter 'Gilt auch für')",
      len({a.id for a in fragen_c1 if a.kategorie == "web_wartung"}) == 2,
      str([(a.id, a.evidence_ids) for a in fragen_c1 if a.kategorie == "web_wartung"]))
check("C1b) die Ölkühlerschläuche-Frage trägt NICHT den Zusatz 'Gilt auch für'",
      fragen_kuehler_c1 and "Gilt auch für" not in fragen_kuehler_c1[0].aktion,
      fragen_kuehler_c1[0].aktion if fragen_kuehler_c1 else "FEHLT")
check("C1c) die Öl-Frage trägt NICHT den Zusatz 'Gilt auch für'",
      fragen_oel_c1 and "Gilt auch für" not in fragen_oel_c1[0].aktion,
      fragen_oel_c1[0].aktion if fragen_oel_c1 else "FEHLT")
check("C1d) jede Frage trägt NUR ihre eigene Evidence-ID (korrekter Evidenzbezug)",
      fragen_oel_c1 and fragen_oel_c1[0].evidence_ids == ["web-wartung-oel"]
      and fragen_kuehler_c1 and fragen_kuehler_c1[0].evidence_ids == ["web-wartung-kuehler"],
      str([(a.id, a.evidence_ids) for a in fragen_c1 if a.kategorie == "web_wartung"]))
check("C1e) dieselbe Trennung gilt auch für die Dokumentenanforderungen",
      len({a.id for a in dokumente_c1 if a.kategorie == "web_wartung"}) == 2,
      str([(a.id, a.evidence_ids) for a in dokumente_c1 if a.kategorie == "web_wartung"]))

# ── C2: über den tatsächlich im echten Bericht genutzten Pfad --
# `_aus_laufleistung()` mit einem Laufleistungskontext, beide Intervalle
# bereits erreicht (82.000 km > 60.000 und > 45.000). ─────────────────────
hinweise_c2 = _wartungshinweise([INSIGHT_OEL, INSIGHT_KUEHLER], 82_000)
check("C2 Vorbedingung) beide Wartungshinweise wurden tatsächlich gebildet "
      "(sonst testet C2 nichts)", len(hinweise_c2) == 2, str(hinweise_c2))
kontext_c2 = Laufleistungskontext(kilometerstand=82_000, wartungshinweise=hinweise_c2)
aktionen_c2 = build_kaufaktionen(REQ_MIN, None, None, [INSIGHT_OEL, INSIGHT_KUEHLER],
                                 laufleistungskontext=kontext_c2)
fragen_c2 = aktionen_c2.verkaeuferfragen.fahrzeugspezifisch
dokumente_c2 = aktionen_c2.dokumente.fahrzeugspezifisch
fragen_wartung_c2 = [a for a in fragen_c2 if a.kategorie == "wartung"]
check("C2a) auch über den laufleistungsbezogenen Pfad bleiben es ZWEI "
      "eigenständige Verkäuferfragen",
      len({a.id for a in fragen_wartung_c2}) == 2,
      str([(a.id, a.evidence_ids) for a in fragen_wartung_c2]))
check("C2b) keine der beiden Fragen trägt 'Gilt auch für'",
      all("Gilt auch für" not in a.aktion for a in fragen_wartung_c2),
      str([a.aktion for a in fragen_wartung_c2]))
check("C2c) dieselbe Trennung gilt für die Dokumentenanforderungen",
      len({a.id for a in dokumente_c2 if a.kategorie == "wartung"}) == 2,
      str([(a.id, a.evidence_ids) for a in dokumente_c2 if a.kategorie == "wartung"]))

# ── C3 (Positivkontrolle): echte Duplikate derselben Wartungsaussage ───────
# bleiben weiterhin zu EINER Aktion zusammengeführt -- der Fix schaltet die
# Deduplizierung nicht pauschal ab. Zwei Insights mit IDENTISCHEM Bauteiltext
# (wie es die bestehende WebFakt-Dedupe oberhalb, N4-Fix, für echte
# Duplikate garantiert) müssen weiterhin verschmelzen.
INSIGHT_OEL_DUPLIKAT = Insight(
    id="web-wartung-oel-zweite-quelle", kategorie="web_wartung",
    titel="Automatikgetriebeöl: Wartungsangabe aus der Webrecherche",
    beschreibung="Automatikgetriebeöl Alle 60.000 km / 4 Jahre (zweite Quelle, dieselbe Aussage).",
    confidence="niedrig", bauteil="Automatikgetriebeöl")
aktionen_c3 = build_kaufaktionen(REQ_MIN, None, None,
                                 [INSIGHT_OEL, INSIGHT_OEL_DUPLIKAT, INSIGHT_KUEHLER])
fragen_c3 = [a for a in aktionen_c3.verkaeuferfragen.fahrzeugspezifisch if a.kategorie == "web_wartung"]
fragen_oel_c3 = [a for a in fragen_c3 if "Automatikgetriebeöl" in a.titel or a.id.endswith("automatikgetriebeoel")]
check("C3a) zwei Insights zum IDENTISCHEN Bauteil ('Automatikgetriebeöl') "
      "bleiben weiterhin EINE Aktion (keine pauschale Abschaltung der Dedup)",
      len({a.id for a in fragen_c3}) == 2, str([(a.id, a.evidence_ids) for a in fragen_c3]))
oel_aktion_c3 = next(a for a in fragen_c3 if "web-wartung-oel" in a.evidence_ids)
check("C3b) die zusammengeführte Öl-Aktion trägt BEIDE Evidence-IDs "
      "(echtes Duplikat -> Evidenz bleibt vollständig, keine Information verloren)",
      "web-wartung-oel" in oel_aktion_c3.evidence_ids
      and "web-wartung-oel-zweite-quelle" in oel_aktion_c3.evidence_ids,
      str(oel_aktion_c3.evidence_ids))
check("C3c) Ölkühlerschläuche bleibt davon unberührt, weiterhin eine eigene Aktion",
      any("web-wartung-kuehler" in a.evidence_ids for a in fragen_c3))

print("\nTEIL C GRUEN" if not _FEHLER else "")
if _FEHLER:
    print(f"\n{len(_FEHLER)} FEHLER (Teil C):")
    for f in _FEHLER[-10:]:
        print(f"  - {f}")
    raise SystemExit(1)

print("\nALLE TESTS GRUEN (Teil A + Teil B + Teil C)")
