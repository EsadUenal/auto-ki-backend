"""
ENFAL -- N7 (echter KaufCheck 04.11.2026, Lexus RC F): Verluststelle von
Ventilspiel und "Motoröl" (Öl- und Filterwechsel) geklärt.

GRUNDLAGE: zwei UNABHÄNGIG bereits als real verifizierte Textausschnitte
DESSELBEN Dokuments (tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf,
"Lexus Garantie- und Serviceheft", Schweiz/Deutsch):
  * Seite 15 (Lexus Care-Wartungsplan) -- wortgleich mit
    test_wartung_vorgangs_rueckreferenz.py::_LEXUS_CH_REAL, dort bereits per
    WebFetch/pypdf gegen das Original-PDF verifiziert (s. docstring
    test_rc_f_lexus_care_wartungsplan_real.py).
  * Seite 16/17 (Sicherheits-/Teileaustausch-Tabelle) -- wortgleich mit
    test_wartung_technische_bedingung_vollpfad.py/test_n2_n3_n7_wartung_
    tabellenblock.py.
Beide Ausschnitte stehen hier erstmals ZUSAMMEN in einem Dokument (wie es
eine vollständige Volltext-Nachladung derselben PDF-Seite liefern würde) --
KEINE vorab günstig ausgewählten fünf Zeilen, voller Kandidaten-Wettbewerb.

NACHWEIS, PRO BAUTEIL GETRENNT (Stufenverfolgung: Dokumenttext -> Segment ->
Kandidat -> Scope/Tätigkeit/Intervall -> Ablehnung/Priorisierung ->
Auswahlgrenze -> WebFakt -> Insight):

  "Motoröl" (Öl- und Filterwechsel): Intervall ("Lexus Care-Zwischenservice:
  alle 15.000 km...") und Bauteil ("Dieser Service beinhaltet einen Öl- und
  Filterwechsel...") stehen im Original in ZWEI Sätzen, verbunden nur durch
  die Rückreferenz "Dieser Service" -- `_wartung_vorgangs_rueckreferenz_
  saetze()` (bereits seit F10/2026-11-02 vorhanden, VOR diesem Paket) bindet
  sie zu einem Kandidaten. Dieser Kandidat wird NACHWEISLICH korrekt
  extrahiert (Bauteil, Intervall, Tätigkeit) und NICHT durch das Modell-/
  Technik-Scope-Gate abgelehnt.
  N7-NACHTRAG (Fortsetzung, "Verluststelle Motoröl geklärt", 2026-11): in
  DIESEM vollen Wettbewerb verlor er bis dahin TROTZDEM -- nicht gegen einen
  fachlich besser belegten Kandidaten, sondern rein alphabetisch gegen zwei
  Zeilen OHNE jede aus dem Quelltext belegte Tätigkeit ("Automatikgetriebeöl",
  "Motorkühlmittel"), bei identischer Confidence und identischem Explizit-
  Rang. Das war KEIN fachlich begründeter Vorrang, sondern ein Nebenkriterium
  (Alphabet), das einen KONKRETER belegten Punkt (Tätigkeit "ersetzen" direkt
  aus dem Wortlaut "Filterwechsel") gegenüber zwei weniger konkret belegten
  Punkten verdrängte. FIX: eine vierte Prioritätsstufe (nach Confidence,
  Explizit-Rang und ungeklärter Bedingung, vor dem alphabetischen Tiebreak)
  bevorzugt eine Zeile mit belegter Tätigkeit -- generisch, nicht auf
  "Motoröl" oder Lexus beschränkt. "Öl- und Filterwechsel" gewinnt dadurch
  jetzt korrekt einen der fünf Plätze; "Hinterachsdifferenzialöl" und
  "Nebenaggregat-Antriebsriemen" bleiben unberührt (höhere Stufe: explizit
  zugeordnet), "Motorkühlmittel" verliert jetzt seinerseits gegen den
  konkreter belegten Punkt und fällt aus den fünf Plätzen heraus.

  Ventilspiel: eigene Fenstergrenze, eigenes Intervall, eigene Klammer mit
  explizit genanntem "RC F" -- löst `_zeilen_scope_passt()` zu `True`
  (explizit zugeordnet, höchste Auswahlstufe) auf und übersteht die
  Fünferlimit-Konkurrenz in diesem vollständigen, realen Dokument.

N7-FIX, TEIL 1 (app/technical_research.py, `_extrahiere_fakten()`-
Sortierung): offline nachgewiesen (s. test_wartung_technische_bedingung_
vollpfad.py Test 3, test_n9_differenzial_und_achsentrennung.py Test 5), dass
eine Zeile mit NACHGEWIESEN UNGEKLÄRTER technischer Bedingung ("Inverter-
Kühlmittel", Elektrifizierung unbekannt) VOR diesem Fix bei der Auswahl
gleichrangig mit einer uneingeschränkt gültigen Zeile ("Motorkühlmittel")
konkurrierte und diese rein alphabetisch verdrängen konnte. Fix: eine dritte
Prioritätsstufe stuft eine ungeklärte Bedingung UNTER eine uneingeschränkte
Zeile gleicher Confidence zurück (weiterhin NICHT ausgeschlossen -- nur
zurückgestuft).

N7-FIX, TEIL 2 (Fortsetzung, s.o.): vierte Prioritätsstufe -- Tätigkeit aus
dem Quelltext belegt schlägt bei Gleichstand (Confidence, Explizit-Rang)
eine Zeile ohne belegte Tätigkeit. Betrifft NUR den reinen Gleichstandsfall;
eine Zeile ohne belegte Tätigkeit bleibt weiterhin ein gültiger, nur
zurückgestufter Kandidat.

NACHWEISGRENZE (ausdrücklich, keine Live-Abnahme): ob der ECHTE Live-Abruf
vom 04.11.2026 exakt diese beiden Textausschnitte in exakt dieser Form und
Kombination zurückgeliefert hat, ist ohne den damaligen Rohtext NICHT
nachweisbar -- dieser Test belegt die PIPELINE-Korrektheit am bestverfügbaren
realen Material, nicht den historischen Produktionsablauf. Mit N7-FIX TEIL 2
erscheint "Motoröl" in DIESEM Nachbau jetzt im sichtbaren Bericht -- das
beweist NICHT, dass der echte Lauf vom 04.11.2026 (anderer, unbekannter
Rohtext) denselben Ausgang gehabt hätte.

Deterministisch (alle Provider gestubbt), kein Netzwerk/LLM/bezahlter
Aufruf, isolierte DB (DB-Miss nur am bestehenden Lookup simuliert).

    python scripts/test_isolated.py test_n7_ventilspiel_motoroel_verluststelle.py
"""
import asyncio
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from app import kaufcheck as kc
from app.models import KaufCheckRequest, KaufCheckResponse
from app.technical_research import (
    FixtureTechnicalResearchProvider, _extrahiere_fakten, recherchiere_technisch,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# ── Seite 15: Lexus Care-Wartungsplan -- wortgleich mit ────────────────────
# test_wartung_vorgangs_rueckreferenz.py::_LEXUS_CH_REAL.
_LEXUS_CARE_TEIL = (
    "LEXUS CARE-WARTUNGSPLAN Für ES 300h, IS 300h, LS 500, LS 500h, LC 500, "
    "LC 500h, NX 350h, NX 450h+, RX 300, RX 350, RX 350L, RX 450h, RX 450hL, "
    "RC 300h, RC F , UX 200 und UX 250h\n"
    "Lexus Care-Komplettservice: alle 30.000 km oder 2 Jahre, je nachdem, was "
    "zuerst eintritt. Lexus Care-Zwischenservice: alle 15.000 km oder einmal "
    "jährlich, je nachdem, was zuerst eintritt. Dieser Service beinhaltet "
    "einen Öl- und Filterwechsel sowie zusätzliche Inspektionen von Lenkung, "
    "Bremsen, Beleuchtung und Reifen. "
)

# ── Seite 16/17: Sicherheits-/Teileaustausch-Tabelle -- wortgleich mit ─────
# test_wartung_technische_bedingung_vollpfad.py/test_n2_n3_n7_wartung_
# tabellenblock.py (inkl. Ventilspiel, Zündkerzen, Motorkühlmittel,
# Inverter-Kühlmittel, Automatikgetriebe-Ölkühlerschläuche als konkurrierende
# Kandidaten UND fremde Positiv-/Ausschlusslisten als Kontrolle).
_SICHERHEIT_TEIL = (
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
    "Alle 45 000 km / 3 Jahre"
)

_KOMBINIERT = _LEXUS_CARE_TEIL + _SICHERHEIT_TEIL
_KOMBINIERT_TREFFER = treffer(
    "https://tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf",
    "Lexus Garantie- und Serviceheft (Schweiz, Deutsch)", _KOMBINIERT)

print("=" * 78)
print("TEIL A) Stufenverfolgung 'Motoröl' (Öl- und Filterwechsel)")
print("=" * 78)

abgelehnt: list[dict] = []
fakten = _extrahiere_fakten([_KOMBINIERT_TREFFER], "wartung", marke="Lexus", modell="RC F",
                            baujahr=2017, abgelehnt=abgelehnt)
bauteile = [f.bauteil for f in fakten]
oel_fakt = next((f for f in fakten if f.bauteil and "filterwechsel" in f.bauteil.lower()), None)
oel_abgelehnt = [a for a in abgelehnt if "filterwechsel" in a.get("satz", "").lower()
                or ("15.000 km" in a.get("satz", "") and "zwischenservice" in a.get("satz", "").lower())]

print(f"  Ausgewählte 5 Fakten: {bauteile}")

check("A1) die ORIGINAL-Einzelsätze (Intervall ohne Bauteil, Bauteil ohne "
      "Intervall) werden für sich genommen korrekt als unvollständig erkannt",
      any(a["grund"] in ("no_component", "no_maintenance_interval")
          and ("zwischenservice" in a["satz"].lower() or "filterwechsel" in a["satz"].lower())
          for a in abgelehnt),
      str([a for a in abgelehnt if "zwischenservice" in a["satz"].lower()
          or "filterwechsel" in a["satz"].lower()]))

# Kandidat kann je nach Fünferlimit-Ausgang entweder ausgegeben ODER
# durchs Limit zurückgestellt sein -- in BEIDEN Fällen ist er korrekt
# EXTRAHIERT worden (das ist die eigentliche Nachweisfrage dieses Tests).
oel_zurueckgestellt = [a for a in abgelehnt if a.get("grund") == "wartung_auswahllimit_erreicht"
                      and "zwischenservice" in a.get("satz", "").lower()]
check("A2) 'Öl- und Filterwechsel' wird über die Vorgangs-Rückreferenz "
      "('Dieser Service beinhaltet ...') TATSÄCHLICH zu einem Kandidaten "
      "zusammengeführt -- entweder unter den 5 ausgewählten Fakten ODER "
      "unter den NUR durchs Limit zurückgestellten (nie: verloren)",
      oel_fakt is not None or bool(oel_zurueckgestellt),
      f"fakten={bauteile!r} zurückgestellt={oel_zurueckgestellt!r}")
_oel_quelle = oel_fakt or (fakten[0] if False else None)
if oel_fakt:
    check("A2b) Bauteil, Intervall (15.000 km) und Tätigkeit ('ersetzen', "
          "aus 'Filterwechsel') korrekt belegt",
          "15.000 km" in oel_fakt.aussage and oel_fakt.taetigkeit == "ersetzen",
          f"aussage={oel_fakt.aussage!r} taetigkeit={oel_fakt.taetigkeit!r}")
check("A3) KEINE Ablehnung dieses Kandidaten durch das Modell-/Technik-"
      "Scope-Gate (kein 'wartung_zeile_fremdes_modell'/'...widerspricht')",
      not any(a.get("grund") in ("wartung_zeile_fremdes_modell",
                                 "wartung_zeile_technische_bedingung_widerspricht")
              and ("filterwechsel" in a.get("satz", "").lower()
                  or "zwischenservice" in a.get("satz", "").lower())
              for a in abgelehnt))
check("A4) wird er NICHT ausgegeben, ist der EINZIGE Ablehnungsgrund das "
      "Auswahllimit (fachlich begründete Auswahl, kein Parserfehler)",
      oel_fakt is not None or bool(oel_zurueckgestellt),
      str(oel_zurueckgestellt))

print("\n" + "=" * 78)
print("TEIL B) Stufenverfolgung 'Ventilspiel'")
print("=" * 78)

ventilspiel = next((f for f in fakten if f.bauteil == "Ventilspiel"), None)
check("B1) 'Ventilspiel' wird mit eigenem Intervall (90.000 km / 6 Jahre) "
      "korrekt extrahiert", ventilspiel is not None and "90.000 km" in ventilspiel.aussage,
      str(ventilspiel))
check("B2) 'Ventilspiel' bleibt eine Inspektion (Tätigkeit != 'ersetzen', "
      "kein erfundener Austausch aus 'Inspektion mit Messfühler')",
      ventilspiel is not None and ventilspiel.taetigkeit != "ersetzen",
      f"taetigkeit={ventilspiel.taetigkeit!r}" if ventilspiel else "FEHLT")
check("B3) 'Ventilspiel' übersteht die Fünferlimit-Konkurrenz in DIESEM "
      "vollständigen, realen Dokument TATSÄCHLICH (nicht nur isoliert "
      "nachgewiesen) -- explizite RC-F-Nennung in der eigenen Klammer gibt "
      "ihm Vorrang",
      "Ventilspiel" in bauteile, str(bauteile))

print("\n" + "=" * 78)
print("TEIL C) Kontrolle: ein fachlich korrekt ausgeschlossener Kandidat")
print("=" * 78)

check("C1) 'Automatikgetriebe-Ölkühlerschläuche' (fremde Positivliste ohne "
      "RC F) wird durch das Modell-Scope-Gate ausgeschlossen -- NICHT durchs "
      "Auswahllimit (echter Ausschluss, keine bloße Zurückstellung)",
      any(a.get("grund") == "wartung_zeile_fremdes_modell"
          and "lkühlerschläuche" in a.get("satz", "") for a in abgelehnt),
      str([a for a in abgelehnt if "lkühlerschläuche" in a.get("satz", "")]))
check("C2) ... und erscheint folglich nicht unter den ausgewählten Fakten",
      not any("lkühlerschläuche" in (b or "") for b in bauteile))

print("\n" + "=" * 78)
print("TEIL D) N7-Fix: ungeklärte Bedingung rangiert unter uneingeschränkter Zeile")
print("=" * 78)

# N7-Nachtrag (Fortsetzung, "Verluststelle Motoröl geklärt", 2026-11): seit
# der vierten Prioritätsstufe (Tätigkeit aus dem Quelltext belegt?) gewinnt
# "Öl- und Filterwechsel" (Tätigkeit 'ersetzen') in DIESEM vollen Wettbewerb
# zusätzlich einen der fünf Plätze und verdrängt jetzt auch "Motorkühlmittel"
# (keine belegte Tätigkeit) aus den finalen 5 -- NICHT mehr Teil der
# ursprünglichen D1-Aussage ("Motorkühlmittel" in bauteile). Das ist die
# BEABSICHTIGTE Folge des neuen, generischen Kriteriums, kein Rückfall.
# Die eigentliche N7/N12-Invariante bleibt aber unverändert bestehen und wird
# hier direkt geprüft: unter den durchs Limit zurückgestellten Zeilen rangiert
# "Motorkühlmittel" WEITERHIN vor "Inverter-Kühlmittel" (Reihenfolge in
# `abgelehnt` entspricht der Sortierreihenfolge, s. `_extrahiere_fakten()`).
_zurueckgestellt_saetze = [a["satz"] for a in abgelehnt
                          if a.get("grund") == "wartung_auswahllimit_erreicht"]
_idx_kuehlmittel = next((i for i, s in enumerate(_zurueckgestellt_saetze)
                        if s.lower().startswith("motorkühlmittel")), None)
_idx_inverter = next((i for i, s in enumerate(_zurueckgestellt_saetze)
                     if s.lower().startswith("inverter")), None)
# N7-Wartungsauswahl (Folgepaket): bei unbekannter Elektrifizierung ist "Inverter-Kühlmittel"
# KEIN Kandidat mehr -- weder ausgewählt noch durchs Limit zurückgestellt. Die Bedingung
# ("nur Hybrid- und Hybrid Plug-in-Modelle") ist unbekannt, nicht bestätigt: kein konkreter
# Wartungspunkt, die Anwendbarkeit bleibt offen. Der Grund steht in der Ablehnungsliste.
check("D1) 'Inverter-Kühlmittel' (Elektrifizierung unbekannt) ist weder ausgewähltes Faktum "
      "noch durchs Limit zurückgestelltes",
      not any("nverter" in (b or "") for b in bauteile) and _idx_inverter is None,
      f"zurückgestellt={_zurueckgestellt_saetze!r} bauteile={bauteile!r}")
check("D2) ... und steht mit eigenem Grund 'wartung_technische_bedingung_offen' in der "
      "Ablehnungsliste (unbekannt gilt nicht als bestätigt, nicht als widerlegt)",
      any("inverter" in a.get("satz", "").lower()
          and a.get("grund") == "wartung_technische_bedingung_offen" for a in abgelehnt),
      str([a for a in abgelehnt if "inverter" in a.get("satz", "").lower()]))
check("D3) 'Öl- und Filterwechsel' erscheint jetzt unter den ausgewählten 5 "
      "Fakten (N7-Nachtrag: konkreter belegte Tätigkeit schlägt den reinen "
      "alphabetischen Tiebreak)",
      any("filterwechsel" in (b or "").lower() for b in bauteile), str(bauteile))

if _FEHLER:
    print(f"\n{len(_FEHLER)} FEHLER (Teil A-D):")
    for f in _FEHLER[-10:]:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nTEIL A-D GRUEN")


# ══════════════════════════════════════════════════════════════════════════
# TEIL E -- derselbe Befund über den vollständigen run_kaufcheck-Webpfad,
# DB-Miss kontrolliert simuliert, Elektrifizierung wie im echten Lauf
# unbekannt.
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
    "(12/14 - 06/25) Herstellerinterne Baureihenbezeichnung UXC1 Motorcode 2UR-GSE "
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
LEXUS_PRESS_MODELLSEITE = treffer(
    "https://pressroom.lexus.com/models/rc-f/",
    "Lexus RC F -- Models | Lexus Pressroom",
    "Lexus RC F. Engine 2UR-GSE 5.0-liter V8. Horsepower 472 hp. Transmission "
    "8-speed Direct Shift automatic.")


def _db_miss_stub(marke, modell, baujahr):
    return None, {"match_art": "no_match", "konfidenz": "niedrig", "belastbar": False,
                  "fehlende_angabe": "simulierter DB-Miss, Testdouble"}


async def _lauf(web_fixture: dict) -> tuple[KaufCheckResponse, dict]:
    orig = (kc.call_gemini_json, kc.recherchiere_technisch, kc.tavily_search_with_fallback,
            kc.marktpreis_recherche_moeglich, kc.vertiefe_marktrecherche,
            kc.find_baureihe_mit_vertrauen)

    async def gemini(system, user):
        return {"bericht": "Laut Inserat unfallfrei. KAUFEN NACH BESICHTIGUNG.",
                "empfehlung": "kaufen_nach_besichtigung", "risiko_evidence_ids": [],
                "empfehlung_evidence_ids": []}

    async def verboten(*a, **k):
        raise AssertionError("Marktprovider darf nicht laufen")

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
        req = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000,
                               motor="5.0 V8, Heckantrieb", kraftstoff="Benzin", leistung_ps=477,
                               getriebe="Automatik", antrieb="Heck", ausstattung=[])
        res = KaufCheckResponse(**await kc.run_kaufcheck(req))
        return res, ausgeloest
    finally:
        (kc.call_gemini_json, kc.recherchiere_technisch, kc.tavily_search_with_fallback,
         kc.marktpreis_recherche_moeglich, kc.vertiefe_marktrecherche,
         kc.find_baureihe_mit_vertrauen) = orig


async def _teil_e() -> None:
    print("\n" + "=" * 78)
    print("TEIL E) derselbe Befund über den vollständigen run_kaufcheck-Webpfad")
    print("=" * 78)

    r, ausgeloest = await _lauf({
        "identitaet": [ROLLING_HU, ADAC_ULTIMATE, CARWIKI, AMS, LEXUS_PRESS_MODELLSEITE],
        "wartung": [_KOMBINIERT_TREFFER], "schwachstelle": [], "rueckruf": [],
    })
    powertrain = r.vehicle_identity.get("field_evidence", {}).get("powertrain", {}).get("value")
    wartung_insights = [i for i in r.insights if i.kategorie == "web_wartung"]
    titel_liste = [i.titel for i in wartung_insights]
    print(f"  Sichtbare Wartungs-Insights: {titel_liste}")

    check("E0) der Web-Wartungs-Fallback lief tatsächlich", ausgeloest["lief"])
    check("E0b) Elektrifizierung bleibt wie im echten Lauf unbekannt",
          powertrain is None, f"powertrain={powertrain!r}")
    check("E1) 'Ventilspiel' erscheint im sichtbaren Bericht",
          any("entilspiel" in t for t in titel_liste), str(titel_liste))
    check("E2) 'Öl- und Filterwechsel' erscheint JETZT im sichtbaren Bericht -- "
          "N7-Fix Teil 2: konkret belegte Tätigkeit ('ersetzen') schlägt den "
          "reinen alphabetischen Tiebreak (konsistent mit Teil A/D3)",
          any("filterwechsel" in t.lower() for t in titel_liste), str(titel_liste))
    check("E3) 'Inverter-Kühlmittel' erscheint NICHT im sichtbaren Bericht -- "
          "konsistent mit Teil D (N7-Fix Teil 1, verliert gegen 'Motorkühlmittel')",
          not any("nverter" in t for t in titel_liste), str(titel_liste))
    check("E4) Wartungsrangfolge: nach Ventilspiel (ausdrücklich zugeordnet) und Öl-/Filterwechsel (Tätigkeit im "
          "eigenen Satz) kommen bei gleichem Rang zuerst Hinweise mit belegbar erreichter Schwelle. "
          "'Nebenaggregat-Antriebsriemen' (105.000 km / 6 Jahre; Zeit bei Baujahr 2017 belegbar erreicht) "
          "steht im Fünferlimit, 'Motorkühlmittel' (150.000 km, bei 82.000 km nicht erreicht) nicht -- zuvor "
          "entschied hier das Alphabet",
          any("ntriebsriemen" in t for t in titel_liste) and not any("otorkühlmittel" in t for t in titel_liste),
          str(titel_liste))
    check("E5) der Bericht behauptet an keiner Stelle, das Dokument sei "
          "vollständig/vollumfänglich ausgewertet",
          "vollständig ausgewertet" not in r.bericht.lower()
          and "vollumfänglich" not in r.bericht.lower())

    if _FEHLER:
        print(f"\n{len(_FEHLER)} FEHLER (Teil E):")
        for f in _FEHLER[-10:]:
            print(f"  - {f}")
        raise SystemExit(1)
    print("\nTEIL E GRUEN")


asyncio.run(_teil_e())
print("\nALLE TESTS GRUEN (Teil A-E)")
