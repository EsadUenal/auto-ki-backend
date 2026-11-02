"""
ENFAL-Reparaturpaket N2/N3/N7, vollständige Absicherung (2026-11-02) -- echter
KaufCheck-Lauf "Eingefügter Text(20261101-210820).txt", Lexus RC F 2017.

TEIL 1 (bereits im Vorpaket behoben): `_wartung_tabellen_segmente()` zerlegt
einen tabellenartigen, verbfreien Wartungsblock in ein Fenster pro Bauteil-
Vorkommen, statt den gesamten Block als EINEN Claim zu behandeln -- ein
Intervall zählt nur noch innerhalb des eigenen Fensters (oder einer per "*N"
verknüpften eigenen Fußnote).

TEIL 2 (dieser Auftrag, bisher offen): "Intervall korrekt gelesen" ist NICHT
dasselbe wie "für das Zielfahrzeug gültig". Reales Beispiel aus demselben
Block: "Unterdruckpumpe für Bremskraftverstärker (LS 500, RX 300, RX 350,
RX 350L, UX 200) Alle 195,000 km" -- Bauteil und Intervall werden korrekt
zusammen gelesen, die Zeile gilt aber laut ihrer EIGENEN Klammerliste
ausdrücklich NUR für LS 500/RX 300/RX 350/RX 350L/UX 200, NICHT für RC F.
Die bisherige dokumentweite Ausrichtung (`ausgerichtet()`) prüft nur, ob
Marke/Modell IRGENDWO auf der Seite stehen -- nicht, ob DIESE ZEILE sie
nennt. Ohne zeilenbezogene Prüfung hätte dieser Fall unverändert einen
falschen RC-F-Fakt erzeugt.

FIX: `_zeilen_scope_passt()` (app/technical_research.py) liest eine
Klammerliste INNERHALB des bereits berechneten Zeilenfensters generisch als
Modell-Scope (Positivliste oder "außer"-Ausschlussliste) und lehnt eine Zeile
nur dann ab, wenn das Zielmodell ausdrücklich NICHT darin vorkommt (Positiv-
liste) bzw. ausdrücklich AUSGESCHLOSSEN ist. Keine erkennbare Scope-Klammer
(z.B. eine reine Zeit-/Fußnotenklammer) blockiert NICHT -- ungeklärt bleibt
ungeklärt, kein automatischer Ausschluss.

TEIL 3 (dieser Auftrag, bisher offen): die Auswahl bei MEHR als
`MAX_FAKTEN_JE_KATEGORIE` gültigen, korrekt zugeordneten Kandidaten wählte
rein alphabetisch -- ein fachlich relevanter Hinweis mit AUSDRÜCKLICHER
Zielmodell-Zuordnung konnte dadurch zugunsten eines generisch zugelassenen,
aber alphabetisch früheren Hinweises verdrängt werden, ohne dass das
irgendwo sichtbar wurde. FIX: Extraktion -> Zeilen-Scope -> Dedupe (alles
UNVERÄNDERT vorher) -- DANACH Relevanzsortierung (explizit zugeordnete Zeile
vor generisch zugelassener, erst dann alphabetisch) -- DANACH erst die
Begrenzung. Zurückgehaltene Kandidaten landen mit Grund
"wartung_auswahllimit_erreicht" in `abgelehnt`, statt zu verschwinden. Nur
für "wartung" -- "rueckruf"/"schwachstelle" bleiben unverändert.

    python test_n2_n3_n7_wartung_tabellenblock.py
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.technical_research import _extrahiere_fakten, _wartung_aktion

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# ── Echter Rohtext: SICHERHEIT-Tabelle (Seite 16/17) ──────────────────────────
# Wortgleich aus dem Berichts-Abschnitt "Bremspedal: Wartungsangabe aus der
# Webrecherche" / "Hinterlegtes Intervall" übernommen (Lauf 20261101-210820).
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

# Echter Rohtext: FITNESS-Tabelle mit eigener, per "*1" verknüpfter Fußnote
# (Motoröl) -- derselbe Bericht, Abschnitt "Motoröl: Wartungsangabe ...".
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


# ── 1) SICHERHEIT-Block allein: Zeilenscope + Extraktion ─────────────────────
abgelehnt_s: list[dict] = []
fakten_s = _extrahiere_fakten([_SICHERHEIT_TREFFER], "wartung", marke="Lexus", modell="RC F",
                              baujahr=2017, abgelehnt=abgelehnt_s)
_je_bauteil_s = {f.bauteil: f for f in fakten_s}
_gruende_s = {a.get("grund") for a in abgelehnt_s}

print("=" * 78)
print("1) Echter SICHERHEIT-Tabellenblock allein (Seiten 16/17)")
print("=" * 78)
for f in fakten_s:
    print(f"  - {f.bauteil!r}: {f.aussage[:90]!r}")
print("  abgelehnt:", sorted(_gruende_s))

check("N2) kein Fakt verbindet 'Bremspedal' mit dem fremden Luftfilter-Intervall "
      "(60.000 km) -- und 'Bremspedal' erzeugt in dieser Quelle überhaupt "
      "keinen Fakt (die einzige eigene Fußnote nennt nur eine Fahrzeugliste, "
      "kein Intervall)",
      not any(b and "bremspedal" in b.lower() for b in _je_bauteil_s))
check("N2) die reinen SICHERHEIT-Spalten-Checkpunkte ohne eigene Zahl "
      "('Bremsklötze', 'Bremsbeläge', 'Bremsflüssigkeit', 'Bremsleitungen') "
      "erben kein fremdes Intervall",
      not any(b and "brems" in b.lower() and "kraftverstaerker" not in b.lower().replace("ä", "ae")
              for b in _je_bauteil_s))
check("N2-NACHTRAG, zentral) 'Bremskraftverstärker' erzeugt TROTZ korrekt "
      "gelesenem eigenem Intervall (195,000 km) KEINEN Fakt für RC F -- die "
      "Zeile nennt in ihrer eigenen Klammer ausdrücklich nur LS 500/RX 300/"
      "RX 350/RX 350L/UX 200, RC F steht NICHT darin ('Intervall korrekt "
      "gelesen' != 'für das Zielfahrzeug gültig')",
      not any(b and "kraftverst" in b.lower() for b in _je_bauteil_s))
check("N2-NACHTRAG) die Bremskraftverstärker-Zeile wird dafür ausdrücklich mit "
      "Grund 'wartung_zeile_fremdes_modell' abgelehnt, nicht nur stillschweigend "
      "verworfen",
      any(a.get("grund") == "wartung_zeile_fremdes_modell"
          and "Bremskraftverst" in (a.get("satz") or "") for a in abgelehnt_s),
      str([a.get("satz") for a in abgelehnt_s if a.get("grund") == "wartung_zeile_fremdes_modell"]))
check("N2-NACHTRAG, passende Modellliste) 'Ventilspiel' nennt in seiner EIGENEN "
      "Klammer ausdrücklich auch 'RC F' (Positivliste, Zielmodell dabei) und "
      "bleibt deshalb zugelassen, mit seinem EIGENEN Intervall (90.000 km / "
      "6 Jahre)",
      any(b and "ventilspiel" in b.lower() and "90.000" in f.aussage
          for b, f in _je_bauteil_s.items()),
      str([(b, f.aussage) for b, f in _je_bauteil_s.items() if b and "ventil" in b.lower()]))
check("N2-NACHTRAG, explizite Ausnahme) 'Nebenaggregat-Antriebsriemen' nennt "
      "eine AUSSCHLUSSLISTE ('außer ES 300h, UX 250h, NX 350h, NX 450h+'), die "
      "RC F NICHT enthält -- die Zeile gilt deshalb weiterhin generell "
      "(richtig zugelassen, keine Übertragung einer falschen Einschränkung)",
      any(b and "antriebsriemen" in b.lower() for b in _je_bauteil_s))
check("Tätigkeit bleibt korrekt: 'Ventilspiel' ist eine Inspektion -- "
      "_wartung_aktion() erfindet daraus KEINEN Austausch ('ersetzen')",
      _wartung_aktion(_je_bauteil_s.get("Ventilspiel", type("", (), {"aussage": ""})).aussage
                       if "Ventilspiel" in _je_bauteil_s else "") != "ersetzen")
check("N7) 'Automatikgetriebeöl' wird korrekt mit seinem EIGENEN Intervall "
      "(60.000 km / 4 Jahre) erkannt, nicht mit der nachfolgenden "
      "Automatikgetriebe-Ölkühlerschläuche-Zeile (45.000 km, andere "
      "Klammerliste ohne RC F) vermischt oder verwechselt",
      any(b and b.lower() == "automatikgetriebeöl" and "60.000" in f.aussage
          and "45.000" not in f.aussage
          for b, f in _je_bauteil_s.items()),
      str([(b, f.aussage) for b, f in _je_bauteil_s.items() if b and "getriebe" in b.lower()]))
check("N2-NACHTRAG) 'Automatikgetriebe-Ölkühlerschläuche' (eigene Klammerliste "
      "LC 500/LS 500/RX 350/RX 350L/UX 200, RC F NICHT dabei) erzeugt selbst "
      "KEINEN Fakt",
      not any(b and "oelkuehlerschlaeuche" in b.lower().replace("ö", "oe").replace("ü", "ue")
              for b in _je_bauteil_s))
check("N3) kein Fakt trägt den gesamten Blockumfang (Fremdmodelle UX 300e / "
      "RX 300 / LS 500 tauchen in KEINER Aussage als eigener Fließtext-Kontext "
      "auf -- jedes Fenster bleibt auf seine eigene Zeile begrenzt)",
      not any("UX 300e" in f.aussage or "LEXUS CARE" in f.aussage for f in fakten_s))
check("Mehrdeutige/fremde Zeilen werden tatsächlich mit Grund abgelehnt, nicht "
      "stillschweigend verworfen",
      {"no_maintenance_interval", "wartung_zeile_fremdes_modell"} <= _gruende_s,
      str(sorted(_gruende_s)))
check("Auswahl, bereits am SOLO-Block) dieser einzelne Treffer liefert bereits "
      "SIEBEN gültige, korrekt zugeordnete Kandidaten (u.a. 'Zündkerzen', "
      "generisch über Vokabular 'zuendkerz' erkannt; seit der N9-Folgerunde "
      "(app/kaufaktionen.py, 'differenzial' ergänzt) zusätzlich "
      "'Hinterachsdifferenzialöl', vorher durch die fehlende Fenstergrenze "
      "nie erreichbar) gegen MAX_FAKTEN_JE_KATEGORIE=5 -- die zwei "
      "überzähligen (Motorkühlmittel, Zündkerzen) werden deshalb "
      "erwartungsgemäß diagnostisch zurückgehalten, nicht stillschweigend "
      "verworfen",
      "wartung_auswahllimit_erreicht" in _gruende_s
      and sum(1 for a in abgelehnt_s if a.get("grund") == "wartung_auswahllimit_erreicht") == 2,
      str([a.get("satz") for a in abgelehnt_s if a.get("grund") == "wartung_auswahllimit_erreicht"]))


# ── 2) FITNESS-Block allein: Fußnoten-Fall ───────────────────────────────────
abgelehnt_f: list[dict] = []
fakten_f = _extrahiere_fakten([_FITNESS_TREFFER], "wartung", marke="Lexus", modell="RC F",
                              baujahr=2017, abgelehnt=abgelehnt_f)

print()
print("=" * 78)
print("2) Echter FITNESS-Tabellenblock allein (eigene Fußnote, Motoröl)")
print("=" * 78)
for f in fakten_f:
    print(f"  - {f.bauteil!r}: {f.aussage[:90]!r}")

check("Fußnoten-Fall) 'Motoröl' erhält über die EIGENE, per *1 verknüpfte "
      "Fußnotendefinition korrekt 15.000 km (1 Jahr)",
      any(b and "motor" in b.lower() and "15.000" in f.aussage
          for b, f in {f.bauteil: f for f in fakten_f}.items()),
      str([(f.bauteil, f.aussage) for f in fakten_f]))


# ── 3) BEIDE Blöcke zusammen: mehr gültige Kandidaten als das Limit ─────────
# Realistisches Szenario: zwei Seiten desselben Dokuments als zwei Treffer in
# EINEM Recherchedurchlauf -- 7 (SICHERHEIT) + 1 (FITNESS) = 8 gültige,
# korrekt zugeordnete Kandidaten gegen MAX_FAKTEN_JE_KATEGORIE=5 (seit der
# N9-Folgerunde zählt 'Hinterachsdifferenzialöl' zusätzlich mit).
abgelehnt_c: list[dict] = []
fakten_c = _extrahiere_fakten([_SICHERHEIT_TREFFER, _FITNESS_TREFFER], "wartung",
                              marke="Lexus", modell="RC F", baujahr=2017, abgelehnt=abgelehnt_c)
_bauteile_c = [f.bauteil for f in fakten_c]
_limit_gruende_c = [a for a in abgelehnt_c if a.get("grund") == "wartung_auswahllimit_erreicht"]

print()
print("=" * 78)
print("3) SICHERHEIT + FITNESS zusammen -- mehr gültige Kandidaten als das Limit")
print("=" * 78)
for f in fakten_c:
    print(f"  - {f.bauteil!r}: {f.aussage[:70]!r}")
print("  durchs Limit zurückgehalten:")
for a in _limit_gruende_c:
    print(f"    - {(a.get('satz') or '')[:70]!r}")

check("Auswahl) genau MAX_FAKTEN_JE_KATEGORIE (5) Fakten werden tatsächlich "
      "angezeigt, nicht mehr und nicht weniger",
      len(fakten_c) == 5, str(_bauteile_c))
check("Auswahl) die über das Limit hinausgehenden, an sich gültigen und "
      "korrekt zugeordneten Kandidaten (hier drei: 'Motoröl' aus dem "
      "FITNESS-Treffer, 'Motorkühlmittel' und 'Zündkerzen' aus dem "
      "SICHERHEIT-Treffer) werden NICHT stillschweigend verworfen, sondern "
      "mit Grund 'wartung_auswahllimit_erreicht' diagnostisch festgehalten",
      len(_limit_gruende_c) == 3, str(_limit_gruende_c))
check("Auswahl) 'Ventilspiel' (ausdrücklich für RC F zugeordnet) wird NICHT "
      "durchs Limit verdrängt -- eine explizite Zuordnung hat Vorrang vor rein "
      "alphabetischer Auswahl",
      "Ventilspiel" in _bauteile_c, str(_bauteile_c))
check("Auswahl) KEINE pauschale Lexus-/Bauteil-Bevorzugung -- die Priorisierung "
      "wirkt generisch über 'explizit zugeordnet' und bleibt deterministisch "
      "(wiederholter Aufruf liefert dieselbe Auswahl)",
      [f.bauteil for f in _extrahiere_fakten(
          [_SICHERHEIT_TREFFER, _FITNESS_TREFFER], "wartung", marke="Lexus",
          modell="RC F", baujahr=2017, abgelehnt=[])] == _bauteile_c)


if _FEHLER:
    print(f"\n{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nALLE N2/N3/N7-ZEILENSCOPE-UND-AUSWAHL-TESTS GRUEN "
      "(Teil A, isoliert -- Teil B, voller run_kaufcheck-Replay, folgt unten)")


# ══════════════════════════════════════════════════════════════════════════
# TEIL B: vollständiger run_kaufcheck-Replay mit dem realen Dokument
# ══════════════════════════════════════════════════════════════════════════
# Kein alleiniger Nachweis über isolierte Funktionsaufrufe -- der Auftrag
# verlangt ausdrücklich zusätzlich den vollständigen Pfad: Karten,
# Verkäuferfragen und Dokumentenanforderungen, wie sie im echten Bericht
# erscheinen würden. Isolierter Pfad wie in den übrigen RC-F-Replays dieses
# Repos (Gemini/Tavily/Marktrecherche gestubbt bzw. gesperrt).

from app import kaufcheck as kc
from app.models import KaufCheckRequest, KaufCheckResponse

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


async def _lauf(req: KaufCheckRequest, web_fixture: dict) -> KaufCheckResponse:
    from app.technical_research import FixtureTechnicalResearchProvider, recherchiere_technisch
    orig = (kc.call_gemini_json, kc.recherchiere_technisch, kc.tavily_search_with_fallback,
            kc.marktpreis_recherche_moeglich, kc.vertiefe_marktrecherche)

    async def gemini(system, user):
        return {"bericht": "Laut Inserat unfallfrei. KAUFEN NACH BESICHTIGUNG.",
                "empfehlung": "kaufen_nach_besichtigung", "risiko_evidence_ids": [],
                "empfehlung_evidence_ids": []}

    async def verboten(*a, **k):
        raise AssertionError("Marktprovider darf nicht laufen -- kein Netzwerk/Provider-Aufruf erlaubt")

    prov = FixtureTechnicalResearchProvider(web_fixture)

    async def technik(r, br, info, brg, mm, provider=None):
        return await recherchiere_technisch(r, br, info, brg, mm, provider=prov)

    kc.call_gemini_json, kc.recherchiere_technisch = gemini, technik
    kc.tavily_search_with_fallback, kc.vertiefe_marktrecherche = verboten, verboten
    kc.marktpreis_recherche_moeglich = lambda *a: False
    try:
        return KaufCheckResponse(**await kc.run_kaufcheck(req))
    finally:
        (kc.call_gemini_json, kc.recherchiere_technisch, kc.tavily_search_with_fallback,
         kc.marktpreis_recherche_moeglich, kc.vertiefe_marktrecherche) = orig


async def _teil_b() -> None:
    print()
    print("=" * 78)
    print("TEIL B) Versuch eines vollständigen run_kaufcheck-Replays")
    print("=" * 78)

    # BEFUND (geprüft, nicht vermutet): für einen normalen Lexus-RC-F-Request
    # liefert `fallback_trigger()` in dieser Umgebung `None` -- die lokale
    # ENFAL-Seed-Datenbank kennt Lexus RC F inzwischen über den DB-Pfad
    # (Baureihe + Motor gematcht), direkt geprüft per Instrumentierung von
    # `recherchiere_technisch()` (liefert `None`, bevor überhaupt ein
    # Web-Treffer gelesen wird) -- auch über `scripts/test_isolated.py`
    # (eigene, frische DB -- dieselbe Seed-Datei bootstrapt dort identisch).
    # Das unterscheidet sich vom ECHTEN Produktionslauf ("keine ENFAL-
    # Baureihen-Zuordnung"). Diesen DB-Zustand zu ändern ist laut Auftrag
    # §8 (STOP) ausdrücklich verboten ("keine DB-/Konfigänderungen"); ein
    # Request mit stark abweichendem Modellnamen zwingt zwar den DB-Miss-
    # Trigger, zerstört dabei aber die Identitäts-Zuordnung selbst (die
    # Fixture-Quellen passen dann nicht mehr zu "RC F <Zusatz>") und würde
    # keinen echten Nachweis mehr liefern, nur eine andere Täuschung.
    #
    # Der vollständige Pfad (Karten/Verkäuferfragen/Dokumente) lässt sich in
    # DIESER Umgebung für GENAU dieses Fahrzeug deshalb nicht ehrlich über
    # den Web-Wartungspfad auslösen. Teil A (oben) bleibt der primäre,
    # gründliche Nachweis -- direkt auf dem echten Blocktext, über dieselbe
    # Produktionsfunktion (`_extrahiere_fakten()`), die der volle Pfad
    # ebenfalls aufruft. Dieser Teil B prüft ersatzweise defensiv: FALLS der
    # Web-Pfad doch läuft (abweichende lokale DB-Stände künftiger Checkouts),
    # darf KEINE falsche Fahrzeugzuordnung im fertigen Bericht ankommen.
    ausgeloest = {"lief": False}

    async def _technik_beobachtet(r, br, info, brg, mm, provider=None):
        from app.technical_research import recherchiere_technisch
        res = await recherchiere_technisch(r, br, info, brg, mm, provider=provider)
        ausgeloest["lief"] = res is not None
        return res

    orig_technik = kc.recherchiere_technisch
    kc.recherchiere_technisch = _technik_beobachtet
    try:
        r = await _lauf(
            KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000,
                             motor="5.0 V8", kraftstoff="Benzin", leistung_ps=477,
                             getriebe="Automatik", antrieb="Heck", ausstattung=[]),
            web_fixture={
                "identitaet": [ROLLING_HU, ADAC_ULTIMATE, CARWIKI, AMS],
                "wartung": [_SICHERHEIT_TREFFER, _FITNESS_TREFFER],
                "schwachstelle": [], "rueckruf": [],
            })
    finally:
        kc.recherchiere_technisch = orig_technik

    print(f"  Web-Wartungs-Fallback tatsächlich ausgelöst: {ausgeloest['lief']}")
    bericht_lower = r.bericht.lower()
    frage_texte = " ".join(a.titel + " " + a.aktion
                           for a in r.kaufaktionen.verkaeuferfragen.fahrzeugspezifisch)
    dokument_texte = " ".join(a.titel + " " + a.aktion
                              for a in r.kaufaktionen.dokumente.fahrzeugspezifisch)
    wartung_insights = [i for i in r.insights if i.kategorie == "web_wartung"]
    for i in wartung_insights:
        print(f"  [Insight] {i.titel} | {i.beschreibung[:70]}")

    check("B1) keine Karte, Verkäuferfrage oder Dokumentenanforderung im "
          "fertigen Bericht behauptet ein Bremspedal-Intervall von 60.000 km "
          "(der Luftfilter-Zeile) -- unabhängig davon, ob der Web-Pfad lief",
          not any("bremspedal" in i.titel.lower() and "60.000" in i.beschreibung
                  for i in wartung_insights))
    check("B2) keine Karte, Verkäuferfrage oder Dokumentenanforderung nennt "
          "'Bremskraftverstärker' -- die Zeile ist ausdrücklich nicht für "
          "RC F -- unabhängig davon, ob der Web-Pfad lief",
          "bremskraftverst" not in bericht_lower
          and "bremskraftverst" not in frage_texte.lower()
          and "bremskraftverst" not in dokument_texte.lower())
    if ausgeloest["lief"]:
        check("B3) der Web-Pfad lief tatsächlich -- dann muss mindestens ein "
              "korrekt zugeordneter Wartungshinweis (Ventilspiel, "
              "Automatikgetriebeöl oder Motoröl) als Insight erscheinen",
              any(any(w in i.titel.lower() for w in ("ventilspiel", "automatikgetriebe", "motor"))
                  for i in wartung_insights),
              str([i.titel for i in wartung_insights]))
    else:
        print("  B3) ÜBERSPRUNGEN -- Web-Wartungs-Fallback lief in dieser "
              "Umgebung nicht (DB-Pfad löste bereits auf, s. Kommentar oben). "
              "Kein Nachweis über Insights möglich, aber auch kein falscher "
              "bestanden -- Teil A bleibt der massgebliche Nachweis.")
    check("B4) der Bericht behauptet an keiner Stelle, das Dokument sei "
          "vollständig/vollumfänglich ausgewertet (das Auswahllimit kann "
          "weiterhin relevante Fakten zurückhalten -- s. Abschlussbericht)",
          "vollständig ausgewertet" not in bericht_lower
          and "vollumfänglich" not in bericht_lower)

    if _FEHLER:
        print(f"\n{len(_FEHLER)} FEHLER (Teil B):")
        for f in _FEHLER[-10:]:
            print(f"  - {f}")
        raise SystemExit(1)
    print("\nTEIL B GRUEN (B3 ggf. uebersprungen, s.o.)")


if __name__ == "__main__":
    asyncio.run(_teil_b())
