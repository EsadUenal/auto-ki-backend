"""
F10-Folgeauftrag (2026-11-01, "vorhandenen Lexus-Wartungsplan gezielt
sichern"): reale Quelle, lokal aus dem Original-PDF extrahiert (nicht nur
aus einem Google-Snippet übernommen).

Quelle: Lexus Schweiz, offizielles "Garantie- und Serviceheft" /
"Lexus Care"-Programm, deutsche Ausgabe.
URL: https://tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf
Abruf: 2026-11-01, per WebFetch direkt von tools.lexus.ch (Download,
lokal mit pypdf extrahiert -- keine Anmeldung, keine Zugriffssperre
umgangen).
Dokument-Metadaten (PDF, 24 Seiten): CreationDate 2021-08-17,
ModDate 2021-12-02 (Adobe InDesign 16.3). Kein explizites Editions-/
Versionsdatum im sichtbaren Text -- nur dieses technische Metadatum.
Geltungsbereich: Lexus Schweiz ("lex-sw-00de" = Lexus Schweiz, Deutsch).
Die Fußnote zu Seite 15 (*2, Einspritzventil-Additiv) nennt zusätzlich
diverse EU-/EWR-Länder (Albanien, Zypern, Estland, Lettland, Malta,
Montenegro, Serbien, Slowakei u. a.) -- das Dokument ist damit INHALTLICH
eine breitere europäische Lexus-Care-Unterlage, lokalisiert für den
Schweizer Markt. Schweiz ist NICHT automatisch gleich Deutschland/EU
(Hinweis aus dem Auftrag) -- das wird hier NICHT behauptet, nur die
Domain-/Markenzugehörigkeit (Lexus) und der tatsächliche Dokumentinhalt.

Seite 4 (Modellübersicht, Produktionsstart): "RC F (September 2014 ~)" --
EIN durchgehender Eintrag ohne Jahres-/Variantentrennung, Produktionsstart
September 2014, kein Enddatum. Deckt das Zieljahr 2017 anhand der eigenen
Modellspanne des Dokuments ab (anders als die bereits bekannte globale
Toyota-Pressemitteilung, die KEINE solche Spanne nennt).

Seite 15/16 (Lexus Care-Wartungsplan, gilt laut Kopfzeile ausdrücklich u. a.
für "RC F"): Komplettservice alle 30.000 km/2 Jahre, Zwischenservice alle
15.000 km/1 Jahr (inkl. Öl- und Filterwechsel) -- UNABHÄNGIG von carwiki.de
(andere Tabellenstruktur: "Lexus Care-Zwischenservice" statt "Ölwechsel-
Intervall", andere Herkunft/Seite, keine erkennbare Kopie derselben
Tabelle). Ventilspiel (LC 500, RC F) alle 90.000 km/6 Jahre, Automatik-
getriebeöl alle 60.000 km/4 Jahre (kein Modellvorbehalt in der Zeile selbst
-- gilt laut Kopfzeile desselben Abschnitts für ALLE dort genannten Modelle,
RC F eingeschlossen). KEINE numerische "erschwerte Bedingungen"-Angabe im
Dokument (nur allgemeiner Verweis auf die Lexus-Vertragswerkstatt) --
corroboriert NICHT carwiki.des konkrete 7.500-km/6-Monate-Angabe für
erschwerte Bedingungen; nur der Normalintervall (15.000 km/12 Monate) wird
unabhängig bestätigt.

Verbatim-Auszug (Umlaute aus der PDF-Textextraktion wiederhergestellt,
pypdf gab an deren Stelle U+FFFD zurück -- keine inhaltliche Änderung).

NACHGEWIESENER BEFUND (2026-11-01, real geprüft, nicht nur vermutet): dieses
echte Dokument deckt EINEN zuvor unbekannten Verarbeitungs-/Vokabular-Fehler
auf UND zeigt zusätzlich eine SEPARATE, nicht behobene Grenze auf.

  1) VOKABULAR-LÜCKE (behoben, s. `_WARTUNG_VOKABULAR_ERWEITERUNG`,
     app/technical_research.py): "Öl- und Filterwechsel" (gängige deutsche
     Kurzform für "Ölwechsel und Filterwechsel") normalisiert zu "oel und
     filterwechsel" -- das zusammenhängende Teilwort "oelwechsel" kommt
     darin NIE vor, der Bauteil-Vokabular-Treffer schlug deshalb IMMER fehl
     ("no_component"), unabhängig von Marke/Modell/Quelle. Generischer
     Sprachmuster-Fix, kein Fahrzeug-Sonderfall (Regression:
     test_wartung_motoroel_vokabular.py, Abschnitt I).

  2) VERBLEIBENDE GRENZE (NICHT behoben, bewusst nicht in dieser Runde
     angegangen): im realen Dokument stehen Intervall ("Lexus Care-
     Zwischenservice: alle 15.000 km...") und Bauteil ("Dieser Service
     beinhaltet einen Öl- und Filterwechsel...") in ZWEI AUFEINANDER-
     FOLGENDEN Sätzen, verbunden nur durch die Rückreferenz "Dieser
     Service" -- `_extrahiere_fakten()` prüft Intervall UND Bauteil nur
     INNERHALB je eines einzelnen Satzes/Claim-Segments, nie über eine
     solche Rückreferenz hinweg. Deshalb bleibt dieser reale, einzeln
     geprüfte Fund WEITERHIN unsichtbar (kein `web_wartung`-Insight) --
     NICHT weil die Quelle ungeeignet wäre, sondern wegen dieser separaten,
     generischen Pipeline-Grenze. Ein Fix dafür (satzübergreifende
     Rückreferenz-Auflösung) wäre eine größere, nicht mehr "begrenzte"
     Änderung an der Claim-Segmentierung (betrifft wartung UND
     schwachstelle gleichermaßen) -- bewusst NICHT in dieser Runde
     umgesetzt, hier nur präzise benannt.

Temporäres, isoliertes Diagnoseskript -- läuft über den ECHTEN
Produktionspfad `run_kaufcheck`; nur Gemini/Tavily/Marktrecherche sind
gestubbt bzw. gesperrt (identisches Muster wie die übrigen RC-F-Replays).

    python scripts/test_isolated.py test_rc_f_lexus_care_wartungsplan_real.py
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import kaufcheck as kc
from app.models import KaufCheckRequest, KaufCheckResponse


def t(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# Bereits bekannte, real abgerufene Identitätsquellen (wortgleich aus
# test_rc_f_herstellerquelle_vollstaendiger_replay.py) -- hier nur zur
# Isolierung der Identität, damit ausschliesslich die neue Wartungsquelle
# geprüft wird. Nicht per Import wiederverwendet, um dessen Modul-Top-Level-
# Replay-Aufruf nicht ungewollt mitzutriggern.
ROLLING_HU = t("https://shop.rolling.hu/catalog/SCHAEFFLER/lexus-rc-c1-f-usc10",
              "LEXUS RC (_C1_) F (USC10_) Part list - Rolling Kft. webshop",
              "SCHAEFFLER - LEXUS RC (_C1_) F (USC10_). Manufacturer LEXUS. Model RC (_C1_). "
              "Type F (USC10_). Construction Year from 08.2014. Engine Output 330kW / 449HP.")
ADAC_ULTIMATE = t("https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/",
      "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25): Technische Daten, "
      "Bilder, Preise | ADAC",
      "Marke Lexus Modell RC F Typ Ultimate Edition Automatik Baureihe RC (UXC1) "
      "(12/14 - 06/25) Herstellerinterne Baureihenbezeichnung UXC1 Modellstart 08/24 "
      "Modellende 06/25 Baureihenstart 12/14 Baureihenende 06/25 Motorcode 2UR-GSE "
      "Leistung maximal in PS 464 Antriebsart Hinterrad Getriebeart Automatikgetriebe "
      "Kraftstoffart Super Hubraum 4969 ccm")
ADAC_AUTOMATIK = t("https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/288844/",
      "Lexus RC F Automatik (01/18 - 03/19): Technische Daten, Bilder, Preise | ADAC",
      "Marke Lexus Modell RC F Typ Automatik Baureihe RC (UXC1) (12/14 - 06/25) "
      "Herstellerinterne Baureihenbezeichnung UXC1 Modellstart 01/18 Modellende 03/19 "
      "Baureihenstart 12/14 Baureihenende 06/25 HSN/TSN Schluesselnummern 5013/ALQ "
      "Motorcode 2UR-GSE Leistung maximal in PS 464 Antriebsart Hinterrad "
      "Getriebeart Automatikgetriebe Kraftstoffart Super Hubraum 4969 ccm")
CARWIKI = t("https://carwiki.de/lexus-rc-hsn-tsn-schluesselnummer/",
           "Lexus RC HSN & TSN | Tabelle aller Schluesselnummern",
           "Uebersicht: Alle Schluesselnummern (HSN/TSN) der Lexus RC Baureihen. "
           "Hersteller: Lexus Fahrzeug: RC Baureihe: RC XC1 Karosserie: Coupe "
           "MODELL HSN SCHLUESSELNUMMER TSN SCHLUESSELNUMMER KRAFTSTOFF HUBRAUM LEISTUNG BAUJAHR "
           "Lexus RC F Carbon Automatik 5013 AJM Benzin 4969 cm3 477 PS 2014 bis 2025")
AMS = t("https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
       "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
       "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Leistung 181 PS bis "
       "477 PS. Hubraum 1998 cm3 bis 4969 cm3. LEXUS RC F TRACK EDITION IM SUPERTEST: "
       "Was kann der V8-Sauger auf der Rennstrecke? LEXUS LC 500 UND RC F: "
       "V8-Sportler auf Abschiedstour.")
TOYOTA_JAPAN = t(
    "https://global.toyota/en/detail/4101939",
    "Lexus Launches All-new 'RC F' High-performance Coupe in Japan | Toyota Motor "
    "Corporation Official Global Website",
    "Tokyo, October 23, 2014-Lexus' all-new high-performance coupe, the \"RC F\", "
    "went on sale today in Japan. Engine Transmission Driveline Price RC F Carbon "
    "Exterior package 2UR-GSE (V8 5.0-liter) 8-Speed SPDS Rear-wheel drive "
    "10,300,000 yen 9,530,000 yen.")
TOYOTA_MEDIA_DE = t(
    "https://www.toyota-media.de/blog/lexus-modelle/artikel/neues-adaptiv-variables-fahrwerk-fuer-lexus-gs-f-und-rc-f/text",
    "NEUES ADAPTIV VARIABLES FAHRWERK FÜR LEXUS F-MODELLE",
    "Köln, 1. Dezember 2016. Der Lexus GS F und der RC F werden beide von einem "
    "5,0 Liter V8-Saugmotor angetrieben, der 351 kW / 477 PS leistet und an eine "
    "Achtstufen-Sport-Automatik gekoppelt ist.")

LEXUS_CH_MODELLUEBERSICHT = t(
    "https://tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf#page=4",
    "Lexus Garantie- und Serviceheft (Schweiz, Deutsch) -- Betroffene Modelle,"
    " Lexus Care-Wartungsplan und zusätzliche Inspektionen",
    # EIN einziges Dokument (eine URL, ein Titel) -- Seite 4/15/16 desselben
    # PDFs, in Originalreihenfolge aneinandergefügt. Wichtig: NICHT als
    # DREI fiktive "#page=N"-Fragment-URLs auf derselben Basis-URL (frühere
    # Fassung dieser Fixture): `curate_results()`/`_normalisiere_url()`
    # streift den URL-Fragment-Anteil für die Exact-URL-Dedupe ab -- drei
    # Treffer mit identischer Basis-URL wurden dadurch als Duplikate
    # behandelt und auf NUR den ersten reduziert, bevor die Wartungs-
    # Extraktion die Seiten 15/16 überhaupt sah. Reiner Fixture-/Test-
    # Konstruktionsfehler (ein reales Web-Suchergebnis liefert eine
    # mehrseitige PDF nie als mehrere Fragment-URLs), keine Produktänderung
    # nötig -- hier richtiggestellt: EIN Treffer, voller zusammenhängender
    # Text, wortgleich inkl. Original-Zeilenumbruch OHNE Satzpunkt zwischen
    # der Modellliste und "Lexus Care-Komplettservice" (reale PDF-
    # Formatierung, per pypdf extrahiert).
    "BETROFFENE MODELLE Produktionsstart RC 300h (Oktober 2015 ~) RC F "
    "(September 2014 ~) LC 500 (März 2017 ~) Falls für Ihren Markt zutreffend.\n\n"
    "LEXUS CARE-WARTUNGSPLAN Für ES 300h, IS 300h, LS 500, LS 500h, LC 500, "
    "LC 500h, NX 350h, NX 450h+, RX 300, RX 350, RX 350L, RX 450h, RX 450hL, "
    "RC 300h, RC F , UX 200 und UX 250h\n"
    "Lexus Care-Komplettservice: alle 30.000 km oder 2 Jahre, je nachdem, was "
    "zuerst eintritt. Lexus Care-Zwischenservice: alle 15.000 km oder einmal "
    "jährlich, je nachdem, was zuerst eintritt. Dieser Service beinhaltet "
    "einen Öl- und Filterwechsel sowie zusätzliche Inspektionen von Lenkung, "
    "Bremsen, Beleuchtung und Reifen.\n\n"
    "Lexus RC F zusätzliche Inspektionen laut Lexus Care-Wartungsplan: "
    "Ventilspiel (Inspektion mit Messfühler, LC 500, RC F) alle 90.000 km "
    "oder 6 Jahre. Automatikgetriebeöl alle 60.000 km oder 4 Jahre.")


async def _lauf(req: KaufCheckRequest, web_fixture: dict) -> KaufCheckResponse:
    from app.technical_research import FixtureTechnicalResearchProvider, recherchiere_technisch
    orig = (kc.call_gemini_json, kc.recherchiere_technisch, kc.tavily_search_with_fallback,
            kc.marktpreis_recherche_moeglich, kc.vertiefe_marktrecherche)

    async def gemini(system, user):
        return {"bericht": "Laut Inserat unfallfrei. KAUFEN NACH BESICHTIGUNG.",
                "empfehlung": "kaufen_nach_besichtigung", "risiko_evidence_ids": [],
                "empfehlung_evidence_ids": []}

    async def verboten(*a, **k):
        raise AssertionError("Marktprovider darf nicht laufen")

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


async def main() -> None:
    r = await _lauf(
        KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000,
                         motor="5.0 V8", kraftstoff="Benzin", leistung_ps=477,
                         getriebe="Automatik", antrieb="Heck", ausstattung=[]),
        web_fixture={
            "identitaet": [ROLLING_HU, ADAC_ULTIMATE, ADAC_AUTOMATIK, CARWIKI, AMS,
                          TOYOTA_JAPAN, TOYOTA_MEDIA_DE],
            "wartung": [LEXUS_CH_MODELLUEBERSICHT],
            "schwachstelle": [], "rueckruf": [],
        })
    print("recommendation_state:", r.recommendation_state, "| empfehlung:", r.empfehlung)
    print("\n--- insights (web_wartung) ---")
    wartung_insights = [i for i in r.insights if i.kategorie == "web_wartung"]
    if not wartung_insights:
        print("  (keine)")
    for i in wartung_insights:
        print(f"  [{i.kategorie}] trust={i.trust} confidence={i.confidence} | {i.titel}")
        print(f"      {i.beschreibung}")
    print("\n--- Bericht-Ausschnitt (Wartung) ---")
    for zeile in r.bericht.splitlines():
        if "wartung" in zeile.lower() or "Wartungsintervall" in zeile or "Service" in zeile:
            print(" ", zeile)


if __name__ == "__main__":
    asyncio.run(main())
