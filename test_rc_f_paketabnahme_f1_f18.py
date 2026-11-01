"""
ENFAL-Abnahmeauftrag ("vollstaendige lokale Paketabnahme gegen F1-F18",
2026-11): EIN vollstaendiger, isolierter Replay ueber den ECHTEN
`run_kaufcheck`-Produktionspfad -- NICHT nur die Identitaets-Schicht
(`werte_identitaet_aus()`), sondern Bericht, Empfehlung, key_findings,
Vergleichstabelle und Verkaeuferfragen, wie sie ein echter Nutzer sehen
wuerde.

Nur EXTERNE PROVIDER (Gemini-LLM, Tavily-Suche, Marktrecherche) werden durch
nachvollziehbare, klar als SYNTHETISCH gekennzeichnete Stubs ersetzt -- die
technische Web-Identitaets-/Fakten-Pipeline (`technical_research.py`,
`vehicle_identity.py`, `empfehlungs_policy.py`, `kaufempfehlung_sync.py`,
`kaufcheck_bericht.py`) laeuft UNVERAENDERT und bekommt ausschliesslich
woertliche, bereits in frueheren Runden verifizierte REALE Quellentexte
(ADAC, carwiki.de, auto-motor-und-sport.de, Rolling Kft., Toyota Japan,
toyota-media.de -- unveraendert aus
test_rc_f_herstellerquelle_vollstaendiger_replay.py zitiert -- PLUS das
Lexus-Original-Rettungsdatenblatt aus test_rc_f_lexus_originalbeleg.py).

Kein field_evidence wird manuell vorbestaetigt, keine fertige Identitaet
oder Empfehlung wird injiziert -- beides entsteht ausschliesslich aus dem
echten Lauf von `run_kaufcheck()` mit diesen Quellen.

STOP-Grenzen: kein bezahlter Aufruf, keine Production-DB, keine
Veroeffentlichung. "Rueckruf"/"Schwachstelle" bleiben LEER (F8/F10 historisch
belegt bzw. offen, s. Abschlussbericht) -- keine neue Recherche in dieser
Runde. Dieser Offline-Replay beweist WEDER echte Live-Auffindbarkeit der
Quellen durch Tavily NOCH die Qualitaet eines echten Gemini-Berichtstexts
(der Gemini-Stub liefert eine feste, erkennbar synthetische Berichtszeile).

    python scripts/test_isolated.py test_rc_f_paketabnahme_f1_f18.py
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import kaufcheck as kc
from app.models import KaufCheckRequest, KaufCheckResponse
from app.technical_research import FixtureTechnicalResearchProvider, recherchiere_technisch

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def t(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


async def _lauf(req: KaufCheckRequest, web_fixture: dict) -> KaufCheckResponse:
    orig = (kc.call_gemini_json, kc.recherchiere_technisch, kc.tavily_search_with_fallback,
            kc.marktpreis_recherche_moeglich, kc.vertiefe_marktrecherche)

    # SYNTHETISCH, klar gekennzeichnet: ersetzt NUR den bezahlten Gemini-
    # Aufruf durch eine feste, erkennbare Zeile -- keine Qualitaetsaussage
    # ueber echten Gemini-Output.
    async def gemini(system, user):
        return {"bericht": "[SYNTHETISCHER GEMINI-STUB] Laut Inserat unfallfrei.",
                "empfehlung": "kaufen_nach_besichtigung", "risiko_evidence_ids": [],
                "empfehlung_evidence_ids": []}

    async def verboten(*a, **k):
        raise AssertionError("Externer Provider (Tavily/Marktrecherche) darf in diesem "
                             "Offline-Replay nicht laufen")

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


# ── Reale Quellen (woertlich, unveraendert aus vorigen Runden zitiert) ─────
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
    "Köln, 1. Dezember 2016. Lexus hat die Fahrstabilität und den Fahrkomfort der "
    "beiden F-Modelle GS F und RC F mit der neuesten Generation des adaptiv "
    "variablen Fahrwerks AVS aufgewertet. Der Lexus GS F und der RC F werden beide "
    "von einem 5,0 Liter V8-Saugmotor angetrieben, der 351 kW / 477 PS leistet und "
    "an eine Achtstufen-Sport-Automatik gekoppelt ist.")
CARWIKI_OELWECHSEL = t(
    "https://carwiki.de/lexus-rc-oelwechsel/",
    "Lexus RC Ölwechsel | Kosten, Intervalle, Ölmenge & Ölfilter",
    "MODELL ÖLSPEZIFIKATION ÖLMENGE ÖLWECHSEL-INTERVALL EMPFOHLENES ÖL BAUREIHE "
    "BAUZEITRAUM Lexus RC ACEA C3 | API SN | MB-Approval 229.31/ 229.51 | Renault "
    "RN0700/0710 | dexos2® 9,3 Liter (Servicebefüllung) | Füllmenge / Ölmenge "
    "(insgesamt) 10,8 Liter (Trockenfüllung) | Filterkapazität 1,1 Liter (insgesamt) "
    "Normal: Wechseln 15000 km / 12 Monate | Schwere Einsatzbedingungen: Wechseln "
    "7500 km / 6 Monate Castrol EDGE 0W-30 RC F, USC10 2014-2015. "
    "7 Einträge · Quelle: CarWiki Datenbank (powered by Auto-Data.net)")
# NEU in dieser Abnahme: echtes Lexus-Original-Rettungsdatenblatt (selbst
# heruntergeladen, pypdf-Text, s. test_rc_f_lexus_originalbeleg.py).
LEXUS_PDF = t(
    "https://www.lexus.de/content/dam/lexus/nmsc/germany/lexus-besitzer/"
    "fahrzeuginformationen/RCF_tcm-3150-1284184.pdf",
    "Lexus RC F Rettungskarte",
    "Lexus RC F\n(UXC1 (EU, M), ab 09/2014)\nStand: 10/2014\n"
    "Lexus RC F (UXC1 (EU, ab 09/2014)")

ALLE_IDENTITAETSQUELLEN = [ROLLING_HU, ADAC_ULTIMATE, ADAC_AUTOMATIK, CARWIKI, AMS,
                           TOYOTA_JAPAN, TOYOTA_MEDIA_DE, LEXUS_PDF]


async def main() -> None:
    web_fixture = {"identitaet": ALLE_IDENTITAETSQUELLEN,
                  "wartung": [CARWIKI_OELWECHSEL], "schwachstelle": [], "rueckruf": []}
    r = await _lauf(KaufCheckRequest(
        marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000, motor="5.0 V8",
        kraftstoff="Benzin", leistung_ps=477, getriebe="Automatik", antrieb="Heck",
        ausstattung=[]), web_fixture)

    vi = r.vehicle_identity
    fe = vi.get("field_evidence", {})
    gen_fe = fe.get("generation", {})

    # `abgelehnte_claims`/`abgelehnte_fakten` erscheinen NICHT im finalen
    # KaufCheckResponse (vi hat diese Schluessel nicht, s. app/models.py
    # KaufCheckResponse -- kein Feld dafuer) -- nur in der dazwischenliegenden
    # `TechnischeRecherche`. Fuer die Beobachtbarkeit in dieser Abnahme wird
    # GENAU DERSELBE Provider direkt (statt ueber run_kaufcheck) aufgerufen --
    # kein zweiter, abweichender Pfad, nur zusaetzliche Einsicht.
    direkt = FixtureTechnicalResearchProvider(web_fixture)
    recherche = await direkt.recherchiere(
        marke="Lexus", modell="RC F", baujahr=2017, motor="5.0 V8", ausgeloest_durch="db_miss")
    abgelehnt = list(recherche.abgelehnte_fakten) + list(
        (recherche.identitaet.abgelehnte_claims if recherche.identitaet else []) or [])

    print("=" * 70)
    for zeile in r.bericht.splitlines():
        if "Generation" in zeile:
            print("Bericht-Zeile:", zeile)
    print("recommendation_state:", r.recommendation_state, "| empfehlung:", r.empfehlung)
    print("generation:", vi.get("generation"), "| cluster:", vi.get("generation_cluster"),
          "| nachweisweg:", vi.get("generation_nachweisweg"),
          "| abweichend:", vi.get("generation_abweichende_kennungen"))
    print("field_evidence[generation]:", gen_fe)
    print("=" * 70)

    # ── 1) UXC1 erreicht die Identitaet ueber den TATSAECHLICHEN Produktpfad ─
    check("1a) generation == 'UXC1' ueber den echten run_kaufcheck-Pfad "
          "(nicht manuell injiziert)",
          vi.get("generation") == "UXC1", f"generation={vi.get('generation')!r}")
    check("1b) Nachweisweg 'herstelleridentifikation' bleibt bis in "
          "vehicle_identity.field_evidence['generation'] erhalten",
          gen_fe.get("nachweisweg") == "herstelleridentifikation",
          f"field_evidence[generation]={gen_fe!r}")
    check("1c) abweichende Kennung 'XC1' (carwiki.de/auto-motor-und-sport.de) "
          "bleibt bis in field_evidence['generation'] erhalten",
          {d["code"] for d in (gen_fe.get("abweichende_kennungen") or [])} == {"XC1"},
          f"abweichende_kennungen={gen_fe.get('abweichende_kennungen')!r}")
    # `generation_cluster`/`generation_abweichende_kennungen` sind Felder von
    # `WebVehicleIdentity` (Web-Recherche-Zwischenergebnis) -- die KANONISCHE
    # `VehicleIdentity` (dieses `vi`-Dict, der tatsaechliche API-Response)
    # fuehrt sie NICHT als eigene Top-Level-Schluessel, nur eingebettet in
    # `field_evidence["generation"]` (bereits in 1b/1c gezeigt). Check 1d
    # prueft deshalb GENAU DORT, nicht an einem nicht-existierenden Schluessel.
    check("1d) KEINE Codegleichsetzung bis in den echten API-Response: "
          "field_evidence['generation']['cluster'] bleibt exakt ['UXC1'], "
          "'XC1' erscheint NIE darin",
          gen_fe.get("cluster") == ["UXC1"], f"cluster={gen_fe.get('cluster')!r}")

    # ── 2) Generationsfilter: tatsaechlicher Befund in DIESEM Quellensatz,
    #      PLUS eine explizite, separat gekennzeichnete Demonstration des
    #      Mechanismus (Auftrag: "zeige, welche Fakten ausgeschlossen
    #      werden" -- in diesem realen Quellensatz ist der ehrliche Befund,
    #      dass KEINE der vorhandenen Wartungs-/Schwachstellenquellen ueber
    #      die Generation ausgeschlossen wird; die Demonstration zeigt, dass
    #      der Mechanismus trotzdem wirksam ist, wenn er greifen wuerde). ──
    andere_generation = [a for a in abgelehnt if a.get("grund") == "andere_generation"]
    source_quality = [a for a in abgelehnt if a.get("grund") == "source_quality"]
    check("2a) ehrlicher Befund fuer DIESEN realen Quellensatz: die einzige "
          "abgelehnte Wartungsquelle (carwiki.de/Oelwechsel) wird NICHT "
          "wegen 'andere_generation' verworfen, sondern wegen "
          "'source_quality' (Quellenschwelle fuer die Kategorie 'wartung', "
          "UNABHAENGIG von UXC1/XC1 -- bereits VOR dem Generationsfilter "
          "geprueft, s. app/technical_research.py Zeile ~4231-4243). Keine "
          "unbemerkte, durch diese Runde neu verursachte Verlust-Illusion",
          len(andere_generation) == 0 and any(
              "lexus-rc-oelwechsel" in (a.get("url") or "") for a in source_quality),
          f"andere_generation={andere_generation!r} source_quality={source_quality!r}")
    from app.technical_research import _fremde_generation
    demo_fremd = t("https://www.example-fachmedium.de/lexus-rc-usc10-wartung",
                   "Lexus RC USC10 Wartungsplan",
                   "Der Lexus RC USC10: alle 15.000 km wird ein Oelwechsel durchgefuehrt.")
    check("2b) DEMONSTRATION (nicht Teil dieses realen Quellensatzes, klar "
          "gekennzeichnet): wuerde eine Wartungsquelle den Code 'USC10' "
          "(dritte, von UXC1 UND XC1 abweichende Schreibweise) direkt am "
          "Modellnamen fuehren, wird sie vom Generationsfilter tatsaechlich "
          "als fremd erkannt und ausgeschlossen -- der Mechanismus wirkt, "
          "wird in diesem konkreten Quellensatz aber nicht ausgeloest",
          _fremde_generation(demo_fremd, "RC", "UXC1", ["UXC1"]) is True)

    # ── 3) Keine unbelegten Codealiases in der sichtbaren Identitaet ───────
    check("3) kein 'USC10' und kein 'C1' (ROLLING_HU-Schreibweise) irgendwo "
          "in field_evidence['generation']['cluster'/'abweichende_kennungen'] "
          "-- nur tatsaechlich im Konflikt stehende, exakt belegte Codes "
          "erscheinen dort",
          gen_fe.get("cluster") == ["UXC1"]
          and {d["code"] for d in (gen_fe.get("abweichende_kennungen") or [])} == {"XC1"},
          f"cluster={gen_fe.get('cluster')!r} "
          f"abweichend={gen_fe.get('abweichende_kennungen')!r}")

    # ── 4) Wartungshinweise + Marktvorbehalt tatsaechlich sichtbar ─────────
    hat_marktvorbehalt = "keine belastbare Marktpreis" in r.bericht
    hat_wartungsabschnitt = "Wartungsintervalle" in r.bericht or "Ölwechsel" in r.bericht
    check("4a) sichtbarer Marktvorbehalt im Bericht (keine erfundene "
          "Marktpreisbewertung ohne Marktdaten)", hat_marktvorbehalt)
    print("--- Wartungs-/Schwachstellen-Satz im Bericht ---")
    for zeile in r.bericht.splitlines():
        if "Wartung" in zeile or "Schwachstelle" in zeile or "Ölwechsel" in zeile:
            print(" ", zeile)
    check("4b) der Bericht macht eine EXPLIZITE Aussage zu Wartungsintervallen "
          "(bestaetigt ODER ausdruecklich 'keine ausreichend belastbaren "
          "... bestaetigt') -- keine stillschweigende Leerstelle",
          hat_wartungsabschnitt)

    # ── 5) Empfehlung und Bericht widersprechen sich nicht ────────────────
    ueberschrift_limited = "eingeschränkt" in r.bericht.lower() or "einschränkung" in r.bericht.lower()
    check("5) wenn recommendation_state LIMITED_ANALYSIS/INSUFFICIENT_IDENTITY "
          "ist, nennt der Bericht auch sichtbar eine Einschraenkung (keine "
          "widersprechende 'alles normal'-Formulierung) -- bzw. bei NORMAL "
          "keine widersprechende Einschraenkungs-Ueberschrift",
          (r.recommendation_state != "NORMAL" and ueberschrift_limited)
          or (r.recommendation_state == "NORMAL" and not ueberschrift_limited),
          f"recommendation_state={r.recommendation_state!r} "
          f"ueberschrift_limited={ueberschrift_limited!r}")

    # ── 6) Keine Ueberfaelligkeit / unbegruendete Risikoverschaerfung ──────
    hat_ueberfaellig_behauptung = "überfällig" in r.bericht.lower()
    check("6) keine Ueberfaelligkeits-Behauptung ohne Service-Historie im "
          "Request (kilometerstand allein ohne letzten Service-Zeitpunkt "
          "darf keine 'ueberfaellig'-Aussage erzeugen)",
          not hat_ueberfaellig_behauptung)

    print()
    print("key_findings Titel:", [f.titel for f in r.key_findings])
    print()
    if _FEHLER:
        print(f"FEHLER: {len(_FEHLER)} Check(s) fehlgeschlagen: {_FEHLER}")
        raise SystemExit(1)
    print("ALLE PAKETABNAHME-CHECKS GRUEN")


asyncio.run(main())
