"""
ENFAL -- Testabsicherung N12/N13 (echter KaufCheck 04.11.2026, Lexus RC F).

N12 (app/technical_research.py, app/models.py, app/evidence.py,
app/kaufaktionen.py, app/laufleistung.py): `_zeilen_scope_passt()` setzte
`fand_scope = True` bereits dann, wenn EINE technische Bedingung
("nur Hybrid- und Hybrid Plug-in-Modelle") im Fenster gefunden wurde --
UNABHÄNGIG davon, ob die Zielachse (Kraftstoff/Elektrifizierung) überhaupt
bekannt war. Eine UNGEKLÄRTE Bedingung galt dadurch bei der späteren Auswahl
(`zeile_explizit_zugeordnet`) als genauso "bestätigt zugeordnet" wie eine
tatsächlich AUFGELÖSTE, passende Bedingung -- und konnte einen eindeutig
zugeordneten Kandidaten aus dem Fünferlimit verdrängen (N7-Verstärkung).
Zusätzlich transportierte NICHTS die Unsicherheit bis zur Verkäuferfrage/
zum Dokument: "Wurde X gemacht?" klang unbedingt, obwohl die Zugehörigkeit
selbst offen war.

FIX:
  * `_zeilen_scope_passt()` liefert jetzt `(ergebnis, ungeklaerte_technische_
    bedingung)`. Eine Bedingung mit unbekannter Zielachse (`scopes_passen(...)
    is None`) trägt NICHT mehr zu `fand_scope` bei (kein "explizit
    zugeordnet" mehr) und wird separat als Klammertext zurückgegeben.
  * WebFakt/Insight/Wartungshinweis tragen jetzt `technische_bedingung`
    (Klammertext) und `technische_bedingung_status` ("unresolved"|`None`) --
    neue, von Baujahr-/Regions-Geltung unabhängige dritte Achse, exakt nach
    demselben, bereits bestehenden Muster.
  * `_aus_web_evidence()`/`_aus_laufleistung()` (app/kaufaktionen.py) fragen
    bei "unresolved" ZUERST nach der Anwendbarkeit selbst, nicht nach einer
    Durchführung -- ein Warnsatz allein in der Insight-Beschreibung reichte
    nicht, weil Verkäuferfrage/Dokument die tatsächlich handlungsleitenden
    Texte sind.

N13 (dieselben Dateien): die belegte Tätigkeit (`_wartung_aktion()`:
"ersetzen"/"pruefen"/"unbestimmt") wurde bereits bei der Extraktion bestimmt,
aber nirgends weitergereicht -- `_aus_laufleistung()`/`_aus_web_evidence()`
fragten immer tätigkeitsneutral "Wurde X gemacht?". FIX: `taetigkeit` wird
bis zum WebFakt/Insight/Wartungshinweis durchgereicht; "ersetzen"/"pruefen"
wählen jetzt ein passendes Verb ("gewechselt"/"geprüft") und einen passenden
Dokumenttitel ("Wechselnachweis"/"Prüfnachweis"); "unbestimmt" bleibt beim
bisherigen, ehrlich neutralen "gemacht"/"Wartungsnachweis" -- KEIN
Rückschluss aus dem Bauteilnamen.

Dedup-Härtung (§3 Auftrag, "ob der neue N11-Schlüssel ... zusammenführt"):
`_schluessel_wartungsintervall()` nimmt jetzt zusätzlich die normalisierte
Intervall-Signatur UND die belegte Tätigkeit in den Dedup-Schlüssel auf
(`_wartung_merkmal_normalisiert()`/`_wartung_aktion()`, dieselben bereits
produktiv genutzten Funktionen) -- zwei fachlich unterschiedliche Wartungen
desselben Bauteils (anderes Intervall ODER andere Tätigkeit) verschmelzen
dadurch nicht mehr allein wegen des Bauteilnamens; echte Duplikate
(identische Aussage, auch textlich leicht abweichend formuliert) bleiben
weiterhin zusammengeführt.

Deterministisch (alle Provider gestubbt), kein Netzwerk/LLM/bezahlter
Aufruf, isolierte DB (DB-Miss nur am bestehenden Lookup simuliert).

    python scripts/test_isolated.py test_n12_n13_wartung_anwendbarkeit_taetigkeit.py
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
    FixtureTechnicalResearchProvider, _extrahiere_fakten, _zeilen_scope_passt,
    recherchiere_technisch,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# Echter Rohtext (lex-sw-00de.pdf, S. 16/17), wortgleich mit den N2/N3/N7/
# N10/N11-Tests, inkl. relevanter Nachbarzeilen. N7-Nachtrag: "Luftfilter"/
# "Zündkerzen"/"Motorkühlmittel" (ZUSÄTZLICHER TEILEAUSTAUSCH) bewusst
# ausgelassen -- mit ihnen konkurrieren in DIESEM Block mehr uneingeschränkt
# gültige Kandidaten um die fünf Plätze, als der N7-Fix (ungeklärte Bedingung
# rangiert unter uneingeschränkter Zeile gleicher Confidence) für Inverter-
# Kühlmittel übrig lässt -- das ist dann eine KORREKTE, in
# test_wartung_technische_bedingung_vollpfad.py/test_n9_... bereits
# eigenständig regressionsgeprüfte Auswahlentscheidung, aber sie würde HIER
# verdecken, was Teil C tatsächlich zeigen soll: die N12-UI-Behandlung einer
# ungeklärten Bedingung, die tatsächlich einen der fünf Plätze erreicht.
# Bleiben ausschließlich reale Zeilen desselben Dokuments, keine erfundenen.
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
    "Inverter-Kühlmittel (nur Hybrid- und Hybrid "
    "Plug-in-Modelle) Alle 240.000 km Danach alle 90.000 km Verteilergetriebeöl "
    "(RX 300 4WD, RX 350, RX 350L) Alle 30.000 km / 4 Jahre Vorderachsdifferenzialöl "
    "(LS 500, LS 500h 4WD) Alle 30.000 km / 4 Jahre Hinterachsdifferenzialöl "
    "(außer ES 300h, NX 350h, NX 450h+, RX 300 2WD, RX 450h, RX 450hL, UX 200, "
    "UX 250h) Alle 30.000 km / 4 Jahre ZUSÄTZLICHE INSPEKTIONEN "
    "Nebenaggregat-Antriebsriemen (außer ES 300h, UX 250h, NX 350h, NX 450h+) "
    "Alle 105.000 km / 6 Jahre Danach alle 15.000 km / jährlich Ventilspiel "
    "(Inspektion mit Messfühler, LC 500, RC F) Alle 90.000 km / 6 Jahre "
    "Automatikgetriebeöl Alle 60.000 km / 4 Jahre"
)
_SICHERHEIT_TREFFER = treffer(
    "https://tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf",
    "Lexus Garantie- und Serviceheft (Schweiz, Deutsch)", _SICHERHEIT_BLOCK)

_INVERTER_SATZ = ("Inverter-Kühlmittel (nur Hybrid- und Hybrid Plug-in-Modelle) Alle "
                 "240.000 km Danach alle 90.000 km")

print("=" * 78)
print("TEIL A) N12 -- Anwendbarkeitsstatus: bestätigt / widersprechend / ungeklärt")
print("=" * 78)

# A1: Zielachse unbekannt -- die Bedingung bleibt ungeklärt, NICHT bestätigt.
ergebnis_1, bedingung_1 = _zeilen_scope_passt(_INVERTER_SATZ, "RC F",
                                              fuel_ziel=None, powertrains_ziel=None)
check("A1 Zielachse unbekannt: Ergebnis ist NICHT False (keine erfundene "
      "Ablehnung) und NICHT das alte, fälschlich bestätigende True",
      ergebnis_1 is None, f"ergebnis={ergebnis_1!r}")
check("A2 ... und die ungeklärte Bedingung wird als Klammertext zurückgegeben",
      bedingung_1 is not None and "Hybrid" in bedingung_1, f"bedingung={bedingung_1!r}")

# A2: Zielachse bekannt UND passend (PHEV) -- bestätigt, keine Unsicherheit.
ergebnis_2, bedingung_2 = _zeilen_scope_passt(
    _INVERTER_SATZ, "RC F", fuel_ziel="benzin", powertrains_ziel=frozenset({"PHEV"}))
check("A3 Zielachse bekannt und passend (PHEV): Ergebnis bestätigt (True)",
      ergebnis_2 is True, f"ergebnis={ergebnis_2!r}")
check("A4 ... keine ungeklärte Bedingung mehr (bereits aufgelöst)",
      bedingung_2 is None, f"bedingung={bedingung_2!r}")

# A3: Zielachse bekannt UND widersprechend (ICE) -- nachweislich ausgeschlossen.
ergebnis_3, bedingung_3 = _zeilen_scope_passt(
    _INVERTER_SATZ, "RC F", fuel_ziel="benzin", powertrains_ziel=frozenset({"ICE"}))
check("A5 Zielachse bekannt und widersprechend (ICE): Ergebnis schließt aus (False)",
      ergebnis_3 is False, f"ergebnis={ergebnis_3!r}")

# A4: derselbe Dreiklang auch End-to-End über `_extrahiere_fakten()`.
# N7-Nachtrag (ENFAL-Testabsicherung, "Verluststelle Ventilspiel/Motoröl
# klären", 2026-11): eine ungeklärte technische Bedingung rangiert seit dem
# N7-Fix bewusst UNTER einer uneingeschränkt gültigen Zeile gleicher
# Confidence (`_extrahiere_fakten()`-Sortierung) -- in DIESEM vollen Block
# konkurriert "Inverter-Kühlmittel" (unbekannt-Fall) mit "Motorkühlmittel"
# um den letzten der fünf Plätze und verliert alphabetisch. Das ist KEIN
# Ausschluss durchs technische Gate (das bleibt unverändert "ungeklärt,
# nicht ausgeschlossen", s. Teil A oben) -- nur eine Auswahllimit-Folge.
# "unbekannt" prüft deshalb "vorhanden ODER durchs Limit zurückgestellt";
# PHEV/ICE sind von dieser Konkurrenz nicht betroffen (PHEV rangiert dank
# aufgelöster, passender Bedingung ganz oben; ICE wird ohnehin ausgeschlossen).
for bezeichnung, fuel_ziel, pt_ziel, erwartet_webfakt, erwartet_status in (
    ("unbekannt", None, None, None, "unresolved"),
    ("passend (PHEV)", "benzin", frozenset({"PHEV"}), True, None),
    ("widersprechend (ICE)", "benzin", frozenset({"ICE"}), False, None),
):
    abgelehnt: list[dict] = []
    fakten = _extrahiere_fakten([_SICHERHEIT_TREFFER], "wartung", marke="Lexus", modell="RC F",
                                baujahr=2017, abgelehnt=abgelehnt,
                                fuel_ziel=fuel_ziel, powertrains_ziel=pt_ziel)
    inverter = [f for f in fakten if "nverter" in (f.bauteil or "")]
    if erwartet_webfakt is None:
        # N7-Wartungsauswahl (Folgepaket): bei UNBEKANNTER Elektrifizierung entsteht KEIN konkreter
        # Wartungspunkt (und damit weder Hinweis, Frage noch Nachweis). Die Zeile wird mit eigenem
        # Grund festgehalten -- die Anwendbarkeit bleibt offen, nichts gilt als bestätigt.
        offen = any("nverter" in a.get("satz", "")
                    and a.get("grund") == "wartung_technische_bedingung_offen"
                    for a in abgelehnt)
        check(f"A6 ({bezeichnung}): Inverter-Kühlmittel erzeugt keinen Wartungspunkt und steht "
              f"mit Grund 'wartung_technische_bedingung_offen' in der Ablehnungsliste",
              not inverter and offen,
              f"fakten={[f.bauteil for f in fakten]!r} abgelehnt={abgelehnt!r}")
    else:
        check(f"A6 ({bezeichnung}): Inverter-Kühlmittel als WebFakt vorhanden={bool(inverter)} "
              f"(erwartet {erwartet_webfakt})",
              bool(inverter) == erwartet_webfakt, str([f.bauteil for f in fakten]))
    if inverter:
        status = inverter[0].technische_bedingung_status
        check(f"A7 ({bezeichnung}): technische_bedingung_status={status!r} "
              f"(erwartet {erwartet_status!r})",
              status == erwartet_status)
        if erwartet_status == "unresolved":
            check(f"A8 ({bezeichnung}): technische_bedingung ist gesetzt und nennt 'Hybrid'",
                  inverter[0].technische_bedingung and "Hybrid" in inverter[0].technische_bedingung)

if _FEHLER:
    print(f"\n{len(_FEHLER)} FEHLER (Teil A):")
    for f in _FEHLER[-10:]:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nTEIL A GRUEN")


print("\n" + "=" * 78)
print("TEIL B) N13 -- belegte Tätigkeit wird erkannt, unbekannte nicht geraten")
print("=" * 78)

from app.technical_research import _wartung_aktion  # noqa: E402  (nach Teil A, bewusst spät)

check("B1 explizites 'prüfen' im Quelltext -> 'pruefen'",
      _wartung_aktion("Die Zündkerzen werden bei jeder Inspektion geprüft.") == "pruefen")
check("B2 explizites 'wechseln' im Quelltext -> 'ersetzen'",
      _wartung_aktion("Der Zahnriemen sollte bei diesem Motor alle 120.000 km "
                      "gewechselt werden.") == "ersetzen")
check("B3 kein Verb im realen Dokumentfragment -> 'unbestimmt' (NICHT geraten)",
      _wartung_aktion("Automatikgetriebeöl Alle 60.000 km / 4 Jahre") == "unbestimmt",
      "echtes lex-sw-00de.pdf-Fragment ohne Tätigkeitsverb")


if _FEHLER:
    print(f"\n{len(_FEHLER)} FEHLER (Teil B):")
    for f in _FEHLER[-10:]:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nTEIL B GRUEN")


# ══════════════════════════════════════════════════════════════════════════
# TEIL C -- N12 über den vollständigen run_kaufcheck-Webpfad, DB-Miss
# kontrolliert simuliert, Elektrifizierung wie im echten Lauf UNBEKANNT.
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
# Markeneigene Domain, wie im echten Lauf ("media.lexus.co.uk"/"pressroom.
# lexus.com") -- noetig fuer `_identitaet_belegt()`s Domain-/Tier-Schwelle
# (direkt gegengeprueft, s. test_n10_n11_scope_dedup_absicherung.py). Bewusst
# KEINE Kraftstoff-/Hybrid-/Antriebsart-Aussage -- Elektrifizierung bleibt
# dadurch wie im echten Lauf "nicht sicher bekannt".
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


async def _teil_c() -> None:
    print("\n" + "=" * 78)
    print("TEIL C) N12 über den vollständigen run_kaufcheck-Webpfad "
          "(DB-Miss simuliert, Elektrifizierung unbekannt)")
    print("=" * 78)

    r, ausgeloest = await _lauf({
        "identitaet": [ROLLING_HU, ADAC_ULTIMATE, CARWIKI, AMS, LEXUS_PRESS_MODELLSEITE],
        "wartung": [_SICHERHEIT_TREFFER], "schwachstelle": [], "rueckruf": [],
    })
    powertrain = r.vehicle_identity.get("field_evidence", {}).get("powertrain", {}).get("value")
    check("C0) der Web-Wartungs-Fallback lief tatsächlich", ausgeloest["lief"])
    check("C0b) Elektrifizierung bleibt wie im echten Lauf unbekannt",
          powertrain is None, f"powertrain={powertrain!r}")

    wartung_insights = [i for i in r.insights if i.kategorie == "web_wartung"]
    inverter_insights = [i for i in wartung_insights if "nverter" in i.titel]
    # N7-Wartungsauswahl (Folgepaket): bei UNBEKANNTER Elektrifizierung entsteht kein konkreter
    # Inverter-Wartungspunkt -- weder Insight noch Frage noch Nachweis. Die Anwendbarkeit bleibt
    # offen (Grund 'wartung_technische_bedingung_offen' in der Ablehnungsliste, s. Teil A). Der
    # Anwendbarkeits-Pfad in app/kaufaktionen.py bleibt für bestätigt-passende/handgebaute
    # Insights bestehen und wird in Teil D unverändert geprüft.
    check("C1) Inverter-Kühlmittel erzeugt KEINEN Insight (Elektrifizierung unbekannt)",
          not inverter_insights, str([(i.titel, i.technische_bedingung_status) for i in wartung_insights]))

    frage_texte = {a.id: a.titel + " " + a.aktion
                  for a in r.kaufaktionen.verkaeuferfragen.fahrzeugspezifisch}
    dok_texte = {a.id: a.titel + " " + a.aktion
                for a in r.kaufaktionen.dokumente.fahrzeugspezifisch}
    inverter_fragen = [t for i, t in frage_texte.items() if "nverter" in i or "inverter" in t.lower()]
    inverter_dok = [t for i, t in dok_texte.items() if "nverter" in i or "inverter" in t.lower()]

    check("C2) keine Verkäuferfrage zu Inverter-Kühlmittel", not inverter_fragen, str(inverter_fragen))
    check("C3) kein Nachweisdokument zu Inverter-Kühlmittel", not inverter_dok, str(inverter_dok))
    check("C4) der Inverter taucht im Bericht an keiner Stelle auf (keine erfundene Aussage)",
          "inverter" not in r.bericht.lower())

    # Kontrolle: 'Automatikgetriebeöl' (keine technische Bedingung) bleibt eine normale,
    # unbedingte Wartungsfrage -- seit der Abschnitts-Tätigkeit als Prüfung formuliert.
    oel_fragen = [t for i, t in frage_texte.items() if "automatikgetriebeoel" in i]
    check("C5 Kontrolle) 'Automatikgetriebeöl' bleibt eine normale Frage "
          "ohne Anwendbarkeits-Umleitung (Tätigkeit 'prüfen' aus dem Abschnittskopf)",
          oel_fragen and "bereits geprüft" in oel_fragen[0].lower()
          and "trifft die bedingung" not in oel_fragen[0].lower(),
          str(oel_fragen))
    if _FEHLER:
        print(f"\n{len(_FEHLER)} FEHLER (Teil C):")
        for f in _FEHLER[-10:]:
            print(f"  - {f}")
        raise SystemExit(1)
    print("\nTEIL C GRUEN")


asyncio.run(_teil_c())


# ══════════════════════════════════════════════════════════════════════════
# TEIL D -- N12 über den laufleistungsbezogenen Pfad: das Intervall GILT ALS
# ERREICHT (rein rechnerisch), die technische Anwendbarkeit bleibt trotzdem
# ungeklärt -- keine erfundene Fälligkeit allein aus dem Kilometerstand.
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 78)
print("TEIL D) N12 -- Kilometerintervall 'erreicht', Anwendbarkeit bleibt vorrangig")
print("=" * 78)

INSIGHT_HYBRID_UNGEKLAERT = Insight(
    id="web-wartung-hybrid", kategorie="web_wartung",
    titel="Inverter-Kühlmittel: Wartungsangabe aus der Webrecherche",
    beschreibung=_INVERTER_SATZ.replace("240.000", "60.000"),  # Intervall klein genug, um "erreicht" zu sein
    confidence="niedrig", bauteil="Inverter-Kühlmittel",
    technische_bedingung="nur Hybrid- und Hybrid Plug-in-Modelle",
    technische_bedingung_status="unresolved")

REQ_MIN = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000,
                          motor="5.0 V8, Heckantrieb", kraftstoff="Benzin", leistung_ps=477,
                          getriebe="Automatik", antrieb="Heck", ausstattung=[])

hinweise_d = _wartungshinweise([INSIGHT_HYBRID_UNGEKLAERT], 82_000)
check("D1 Vorbedingung) das Intervall gilt rein rechnerisch als erreicht "
      "(sonst testet D nichts)", len(hinweise_d) == 1, str(hinweise_d))
if hinweise_d:
    check("D2) die technische Bedingung wurde in den Wartungshinweis übernommen",
          hinweise_d[0].technische_bedingung_status == "unresolved")
kontext_d = Laufleistungskontext(kilometerstand=82_000, wartungshinweise=hinweise_d)
aktionen_d = build_kaufaktionen(REQ_MIN, None, None, [INSIGHT_HYBRID_UNGEKLAERT],
                                laufleistungskontext=kontext_d)
fragen_d = [a for a in aktionen_d.verkaeuferfragen.fahrzeugspezifisch if a.kategorie == "wartung"]
check("D3) trotz rechnerisch erreichtem Intervall fragt die Aktion ZUERST "
      "nach der Anwendbarkeit, nicht nach einer unbedingten Durchführung",
      fragen_d and "trifft die bedingung" in fragen_d[0].titel.lower(),
      str([a.titel for a in fragen_d]))
check("D4) keine erfundene Fälligkeitsbehauptung ('fällig'/'überfällig') in "
      "Frage oder Hinweis",
      fragen_d and not any(w in (fragen_d[0].titel + fragen_d[0].aktion).lower()
                           for w in ("fällig", "überfällig")))

if _FEHLER:
    print(f"\n{len(_FEHLER)} FEHLER (Teil D):")
    for f in _FEHLER[-10:]:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nTEIL D GRUEN")


# ══════════════════════════════════════════════════════════════════════════
# TEIL E -- N13 Ende-zu-Ende: Kontrolle und Wechsel bleiben unterscheidbar,
# unbekannte Tätigkeit wird nicht geraten.
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 78)
print("TEIL E) N13 -- Kontrolle/Wechsel unterscheidbar, unbekannte Tätigkeit bleibt offen")
print("=" * 78)

INSIGHT_PRUEFEN = Insight(
    id="web-wartung-pruef", kategorie="web_wartung",
    titel="Zündkerzen: Wartungsangabe aus der Webrecherche",
    beschreibung="Die Zündkerzen werden bei jeder Inspektion alle 90.000 km geprüft.",
    confidence="niedrig", bauteil="Zündkerzen", taetigkeit="pruefen")
INSIGHT_WECHSELN = Insight(
    id="web-wartung-wechsel", kategorie="web_wartung",
    titel="Zahnriemen: Wartungsangabe aus der Webrecherche",
    beschreibung="Der Zahnriemen sollte bei diesem Motor alle 120.000 km gewechselt werden.",
    confidence="niedrig", bauteil="Zahnriemen", taetigkeit="ersetzen")
INSIGHT_UNBESTIMMT = Insight(
    id="web-wartung-unbestimmt", kategorie="web_wartung",
    titel="Automatikgetriebeöl: Wartungsangabe aus der Webrecherche",
    beschreibung="Automatikgetriebeöl Alle 60.000 km / 4 Jahre.",
    confidence="niedrig", bauteil="Automatikgetriebeöl", taetigkeit=None)

aktionen_e = build_kaufaktionen(
    REQ_MIN, None, None, [INSIGHT_PRUEFEN, INSIGHT_WECHSELN, INSIGHT_UNBESTIMMT])
fragen_e = {a.evidence_ids[0]: a for a in aktionen_e.verkaeuferfragen.fahrzeugspezifisch
           if a.kategorie == "web_wartung"}
dok_e = {a.evidence_ids[0]: a for a in aktionen_e.dokumente.fahrzeugspezifisch
        if a.kategorie == "web_wartung"}

check("E1) Kontrolle ('pruefen') -> Verkäuferfrage nennt 'geprüft', nicht 'gemacht'",
      "web-wartung-pruef" in fragen_e and "geprüft" in fragen_e["web-wartung-pruef"].titel,
      fragen_e.get("web-wartung-pruef").titel if "web-wartung-pruef" in fragen_e else "FEHLT")
check("E2) Wechsel ('ersetzen') -> Verkäuferfrage nennt 'gewechselt', nicht 'gemacht'",
      "web-wartung-wechsel" in fragen_e and "gewechselt" in fragen_e["web-wartung-wechsel"].titel,
      fragen_e.get("web-wartung-wechsel").titel if "web-wartung-wechsel" in fragen_e else "FEHLT")
check("E3) unbestimmte Tätigkeit -> bleibt beim neutralen 'gemacht' (KEIN "
      "Rückschluss aus dem Bauteilnamen 'Automatikgetriebeöl' auf 'gewechselt')",
      "web-wartung-unbestimmt" in fragen_e
      and "gemacht" in fragen_e["web-wartung-unbestimmt"].titel
      and "gewechselt" not in fragen_e["web-wartung-unbestimmt"].titel,
      fragen_e.get("web-wartung-unbestimmt").titel if "web-wartung-unbestimmt" in fragen_e else "FEHLT")
check("E4) Dokumenttitel unterscheiden sich entsprechend (Prüf-/Wechsel-/Wartungsnachweis)",
      "web-wartung-pruef" in dok_e and "Prüfnachweis" in dok_e["web-wartung-pruef"].titel
      and "web-wartung-wechsel" in dok_e and "Wechselnachweis" in dok_e["web-wartung-wechsel"].titel
      and "web-wartung-unbestimmt" in dok_e and "Wartungsnachweis" in dok_e["web-wartung-unbestimmt"].titel,
      str({k: v.titel[:40] for k, v in dok_e.items()}))
check("E5) alle drei bleiben DREI eigenständige Verkäuferfragen (keine "
      "Vermischung über Tätigkeit)",
      len(fragen_e) == 3, str(list(fragen_e)))

if _FEHLER:
    print(f"\n{len(_FEHLER)} FEHLER (Teil E):")
    for f in _FEHLER[-10:]:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nTEIL E GRUEN")


# ══════════════════════════════════════════════════════════════════════════
# TEIL F -- Dedup-Härtung: echte Duplikate bleiben zusammengeführt; gleiches
# Bauteil + gleiches Intervall, aber NACHWEISLICH unterschiedliche Tätigkeit,
# verschmilzt NICHT mehr (direkter Schlüssel-Test, unabhängig von TEIL E).
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 78)
print("TEIL F) Dedup -- echte Duplikate bleiben zusammengeführt, unterschiedliche "
      "Tätigkeit am selben Bauteil/Intervall nicht mehr")
print("=" * 78)

INSIGHT_BREMSFL_PRUEFEN = Insight(
    id="web-wartung-bf-pruef", kategorie="web_wartung",
    titel="Bremsflüssigkeit: Wartungsangabe aus der Webrecherche",
    beschreibung="Bremsflüssigkeit wird alle 30.000 km geprüft.",
    confidence="niedrig", bauteil="Bremsflüssigkeit", taetigkeit="pruefen")
INSIGHT_BREMSFL_WECHSELN = Insight(
    id="web-wartung-bf-wechsel", kategorie="web_wartung",
    titel="Bremsflüssigkeit: Wartungsangabe aus der Webrecherche",
    beschreibung="Bremsflüssigkeit wird alle 30.000 km gewechselt.",
    confidence="niedrig", bauteil="Bremsflüssigkeit", taetigkeit="ersetzen")
INSIGHT_BREMSFL_DUPLIKAT = Insight(
    id="web-wartung-bf-wechsel-2", kategorie="web_wartung",
    titel="Bremsflüssigkeit: Wartungsangabe aus der Webrecherche",
    beschreibung="Bremsflüssigkeit wird alle 30.000 km gewechselt (zweite, "
                "unabhängige Quelle).",
    confidence="niedrig", bauteil="Bremsflüssigkeit", taetigkeit="ersetzen")

aktionen_f = build_kaufaktionen(
    REQ_MIN, None, None,
    [INSIGHT_BREMSFL_PRUEFEN, INSIGHT_BREMSFL_WECHSELN, INSIGHT_BREMSFL_DUPLIKAT])
fragen_f = [a for a in aktionen_f.verkaeuferfragen.fahrzeugspezifisch if a.kategorie == "web_wartung"]
check("F1) 'prüfen 30.000 km' und 'wechseln 30.000 km' bleiben ZWEI "
      "eigenständige Aktionen (gleiches Bauteil UND gleiches Intervall, aber "
      "nachweislich unterschiedliche Tätigkeit)",
      len({a.id for a in fragen_f}) == 2, str([(a.id, a.evidence_ids) for a in fragen_f]))
wechsel_aktion = next((a for a in fragen_f if "web-wartung-bf-wechsel" in a.evidence_ids), None)
check("F2) die beiden 'wechseln'-Quellen (echtes Duplikat, leicht "
      "unterschiedlich formuliert) bleiben zusammengeführt -- beide "
      "Evidence-IDs vorhanden",
      wechsel_aktion is not None
      and "web-wartung-bf-wechsel" in wechsel_aktion.evidence_ids
      and "web-wartung-bf-wechsel-2" in wechsel_aktion.evidence_ids,
      wechsel_aktion.evidence_ids if wechsel_aktion else "FEHLT")
pruef_aktion = next((a for a in fragen_f if "web-wartung-bf-pruef" in a.evidence_ids), None)
check("F3) die 'prüfen'-Aktion trägt NICHT die Evidence-ID der 'wechseln'-Quellen",
      pruef_aktion is not None and "web-wartung-bf-wechsel" not in pruef_aktion.evidence_ids,
      pruef_aktion.evidence_ids if pruef_aktion else "FEHLT")

if _FEHLER:
    print(f"\n{len(_FEHLER)} FEHLER (Teil F):")
    for f in _FEHLER[-10:]:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nTEIL F GRUEN")

print("\nALLE TESTS GRUEN (Teil A-F)")
