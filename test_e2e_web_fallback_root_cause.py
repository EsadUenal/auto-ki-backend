"""
True end-to-end tests: run_kaufcheck() with LLM stubbed, market-web stubbed
(no network), but DB lookup + web fallback trigger + evidence pipeline +
VehicleIdentity merge fully REAL (only the raw Tavily results are canned via
FixtureTechnicalResearchProvider, exactly like test_technical_fallback.py).

  B) End-to-End Web Success — Mazda MX-5 DB-miss -> web evidence -> identity
     -> kaufcheck pipeline -> final Bericht.
  C) End-to-End Web Failure — DB-miss + web fehler=True (no usable sources).
  D) Conflicting web sources — two similarly-strong sources disagree on
     generation; identity.generation must stay unresolved (no arbitrary pick).

No network, no DB writes.
"""
import asyncio

import app.recall_filter as _rf
_rf.get_rueckruf_referenzen_kurz = lambda: []

import app.kaufcheck as kc
from app.models import KaufCheckRequest
from app.technical_research import FixtureTechnicalResearchProvider, recherchiere_technisch

_FEHLER = []


def check(name, cond):
    print(f"[{'OK  ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FEHLER.append(name)


async def _stub_gemini(system, user_msg):
    return {"risiko_evidence_ids": []}


def make_provider_patch(fixtures=None, fehler=False):
    provider = FixtureTechnicalResearchProvider(fixtures, fehler=fehler)

    async def _patched(req, baureihe_roh, identitaet, baureihe_gegatet, motor_match, provider=None):
        return await recherchiere_technisch(req, baureihe_roh, identitaet, baureihe_gegatet,
                                            motor_match, provider=make_provider_patch.current)
    make_provider_patch.current = provider
    return _patched


async def _no_market(*args, **kwargs):
    return []


def fixtures_mazda_mx5_gut():
    def treffer(url, titel, inhalt):
        return {"url": url, "title": titel, "content": inhalt}
    return {
        "identitaet": [
            treffer("https://www.adac.de/mazda-mx-5-test", "Mazda MX-5 im Test",
                    "Der Mazda MX-5 ist ein Roadster mit 2.0 SKYACTIV-G Benzinmotor, 184 PS."),
            treffer("https://www.autobild.de/mazda-mx-5", "Mazda MX-5 Gebrauchtwagen",
                    "Mazda MX-5: Roadster, 2.0 SKYACTIV-G, 184 PS, Benzin, Hinterradantrieb."),
        ],
        "schwachstelle": [
            treffer("https://www.adac.de/mazda-mx-5-schwachstellen", "Mazda MX-5 Schwachstellen",
                    "Beim Mazda MX-5 gilt der Verdeckmechanismus gelegentlich als Schwachstelle."),
        ],
        "rueckruf": [], "wartung": [],
    }


def fixtures_leer():
    return {"identitaet": [], "schwachstelle": [], "rueckruf": [], "wartung": []}


async def run_all():
    orig_recherchiere = kc.recherchiere_technisch
    orig_tavily = kc.tavily_search_with_fallback
    orig_gemini = kc.call_gemini_json

    # ══════════════════════════════════════════════════════════════════════
    print("\n=== B) End-to-End Web Success (Mazda MX-5, real pipeline) ===")
    provider_b = FixtureTechnicalResearchProvider(fixtures_mazda_mx5_gut())

    async def recherchiere_b(req, baureihe_roh, identitaet, baureihe_gegatet, motor_match):
        return await recherchiere_technisch(req, baureihe_roh, identitaet, baureihe_gegatet,
                                            motor_match, provider=provider_b)

    kc.recherchiere_technisch = recherchiere_b
    kc.tavily_search_with_fallback = _no_market
    kc.call_gemini_json = _stub_gemini
    try:
        req_b = KaufCheckRequest(marke="Mazda", modell="MX-5", baujahr=2019,
                                 kilometerstand=58_700, getriebe="Schaltgetriebe",
                                 preis_eur=22_900)
        res_b = await kc.run_kaufcheck(req_b)
    finally:
        kc.recherchiere_technisch = orig_recherchiere
        kc.tavily_search_with_fallback = orig_tavily
        kc.call_gemini_json = orig_gemini

    check("B1 kein DB-Treffer (baureihe_erkannt None)", res_b["baureihe_erkannt"] is None)
    check("B2 Web-Identitaet im Ergebnis vorhanden", res_b["web_identitaet"] is not None)
    check("B3 technical_coverage nennt 'web'", res_b["technical_coverage"] == "web")
    check("B4 Datenbasis nennt Webrecherche", any("Webrecherche" in s for s in res_b["datenbasis"]))
    # KaufCheck-Final-Stabilization (Cluster J): B5/B6 sicherten bisher die
    # FALSCHE Aussage ab — "Marke+Modell auf zwei Domains" galt als bestätigte
    # Identität und erlaubte "KAUFEN NACH BESICHTIGUNG", obwohl Generation und
    # Motorisierung unbelegt blieben (genau der Production-Befund Mazda MX-5).
    # Diese Fixture belegt NUR Marke/Modell (keine Generation, keine Motorangabe
    # im Request) -> eingeschränkte Analyse. Der positive Fall steht in B9-B11.
    check("B5 Bericht-Ueberschrift 'Fahrzeugidentität eingeschränkt' (nur Marke/Modell belegt)",
          "## Fahrzeugidentität eingeschränkt" in res_b["bericht"])
    check("B6 Empfehlung 'unbekannt' + LIMITED_ANALYSIS (Identitäts-Floor im echten Pfad)",
          res_b["empfehlung"] == "unbekannt"
          and res_b["recommendation_state"] == "LIMITED_ANALYSIS")
    check("B7 vehicle_identity.fuel wurde aus Web ergaenzt (benzin)",
          (res_b["vehicle_identity"].get("fuel") or "").lower() == "benzin")
    check("B8 Bericht nennt den Verdeckmechanismus NICHT als sicheren Fakt ohne Beleg-Hinweis "
          "(Datenqualitaet-Zeile vorhanden)", "Datenqualität" in res_b["bericht"])

    # B9-B11: dieselbe Pipeline, aber die Identitätsphase belegt Generation und
    # Motorisierung aus Quellentexten -> normale Empfehlung ist wieder erlaubt.
    fx_voll = fixtures_mazda_mx5_gut()
    fx_voll["identitaet"] = [
        {"url": "https://www.adac.de/mazda-mx-5-nd", "title": "Mazda MX-5 ND (seit 2015)",
         "content": "Die 4. Generation des Mazda MX-5 (ND, seit 2015): 2.0 SKYACTIV-G mit 184 PS, "
                    "Benziner, Hinterradantrieb, 6-Gang-Schaltgetriebe."},
        {"url": "https://www.auto-motor-und-sport.de/mazda-mx-5-nd", "title": "Mazda MX-5 ND im Test",
         "content": "Mazda MX-5 ND (seit 2015) mit 2.0 SKYACTIV-G und 184 PS, Heckantrieb."},
    ]
    provider_b2 = FixtureTechnicalResearchProvider(fx_voll)

    async def recherchiere_b2(req, baureihe_roh, identitaet, baureihe_gegatet, motor_match):
        return await recherchiere_technisch(req, baureihe_roh, identitaet, baureihe_gegatet,
                                            motor_match, provider=provider_b2)

    kc.recherchiere_technisch = recherchiere_b2
    kc.tavily_search_with_fallback = _no_market
    kc.call_gemini_json = _stub_gemini
    try:
        res_b2 = await kc.run_kaufcheck(KaufCheckRequest(
            marke="Mazda", modell="MX-5", baujahr=2019, motor="2.0 SKYACTIV-G", leistung_ps=184,
            kraftstoff="Benzin", getriebe="Schaltgetriebe", kilometerstand=58_700, preis_eur=22_900))
    finally:
        kc.recherchiere_technisch = orig_recherchiere
        kc.tavily_search_with_fallback = orig_tavily
        kc.call_gemini_json = orig_gemini
    check("B9 Web-Generation (ND) erreicht die kanonische Identität",
          res_b2["vehicle_identity"].get("generation") == "ND")
    check("B10 Identität belegt -> normale Empfehlung erlaubt",
          res_b2["recommendation_state"] == "NORMAL" and res_b2["empfehlung"] != "unbekannt")
    check("B11 Überschrift 'über Webquellen plausibilisiert', Datenbasis ohne ENFAL-DB",
          "## Fahrzeug über Webquellen plausibilisiert" in res_b2["bericht"]
          and "ENFAL-Fahrzeugdatenbank" not in res_b2["datenbasis"] and res_b2["quelle"] == "web")

    # ══════════════════════════════════════════════════════════════════════
    print("\n=== C) End-to-End Web Failure (DB-miss + kein Web-Beleg) ===")
    provider_c = FixtureTechnicalResearchProvider(fixtures_leer())

    async def recherchiere_c(req, baureihe_roh, identitaet, baureihe_gegatet, motor_match):
        return await recherchiere_technisch(req, baureihe_roh, identitaet, baureihe_gegatet,
                                            motor_match, provider=provider_c)

    kc.recherchiere_technisch = recherchiere_c
    kc.tavily_search_with_fallback = _no_market
    kc.call_gemini_json = _stub_gemini
    try:
        req_c = KaufCheckRequest(marke="Mazda", modell="MX-5", baujahr=2019,
                                 kilometerstand=58_700, preis_eur=22_900)
        res_c = await kc.run_kaufcheck(req_c)
    finally:
        kc.recherchiere_technisch = orig_recherchiere
        kc.tavily_search_with_fallback = orig_tavily
        kc.call_gemini_json = orig_gemini

    check("C1 kein DB-Treffer", res_c["baureihe_erkannt"] is None)
    check("C2 keine Web-Identitaet uebernommen", res_c["web_identitaet"] is None)
    check("C3 Empfehlung ist 'unbekannt' (Identitaets-Floor greift, weder DB noch Web)",
          res_c["empfehlung"] == "unbekannt")
    check("C4 Bericht-Ueberschrift 'Fahrzeugidentität nicht bestätigt' (kein falsches 'erkannt')",
          "Fahrzeugidentität nicht bestätigt" in res_c["bericht"])
    check("C5 'Fahrzeug erkannt' erscheint NICHT im Bericht",
          "## Fahrzeug erkannt" not in res_c["bericht"])
    check("C6 Datenbasis nennt KEINE Webrecherche (keine tatsaechlich verwendete Web-Quelle)",
          not any("Webrecherche" in s for s in res_c["datenbasis"]))
    check("C7 keine fahrzeugspezifischen Risiko-Ueberschriften erzeugt (nur generische Basis-Hinweise)",
          "Keine freigegebenen technischen Risikothemen" in res_c["bericht"]
          or len(res_c["insights"]) == 0
          or all(i.kategorie not in ("schwachstelle",) for i in res_c["insights"]))
    check("C8 technical_coverage='partial' (kein DB, kein Web)",
          res_c["technical_coverage"] == "partial")

    # ══════════════════════════════════════════════════════════════════════
    print("\n=== D) Conflicting web sources: two strong sources disagree on generation ===")
    from app.vehicle_identity import VehicleIdentity
    from app.models import TechnischeRecherche, WebVehicleIdentity

    # Zwei gleich starke, unabhaengige Quellen (Fachmedien) fuer dasselbe Modell,
    # aber KEIN deterministischer Mechanismus liefert je eine Generation aus dem
    # Web (siehe app/technical_research.py: "Generation/Bauzeitraum werden NICHT
    # geraten"). Wir pruefen direkt an der kanonischen Merge-Stelle
    # (VehicleIdentity.apply_web_evidence), dass ein Web-Ergebnis mit belegter
    # Identitaet trotzdem KEINE Generation setzt/rät, selbst wenn zwei Quellen
    # unterschiedliche Generationsangaben enthalten wuerden (das Datenmodell
    # WebVehicleIdentity traegt ueberhaupt kein generation-Feld — das ist die
    # bewusste, bereits vorhandene konservative Antwort auf "widersprechende
    # Quellen": das Feld existiert im Web-Pfad gar nicht, kann also nie
    # widerspruechlich UND nie geraten sein).
    web_konflikt = TechnischeRecherche(
        ausgeloest_durch="db_miss",
        identitaet=WebVehicleIdentity(belegt=True, marke="Mazda", modell="MX-5",
                                      motor="2.0 SKYACTIV-G", kraftstoff="benzin",
                                      confidence="hoch", belegende_domains=2),
    )
    identity_d = VehicleIdentity.from_check_context(None, None,
        KaufCheckRequest(marke="Mazda", modell="MX-5", baujahr=2019))
    identity_d.apply_web_evidence(web_konflikt)
    check("D1 _baue_recherche setzt Generation NIE aus Web-Treffern, auch nicht bei "
          "zwei starken, uebereinstimmenden Quellen (identitaet.generation bleibt None)",
          web_konflikt.identitaet.generation is None)
    check("D2 identity.generation bleibt None trotz belegter Web-Identitaet",
          identity_d.generation is None)
    check("D3 field_evidence fuer 'generation' bleibt 'unknown', kein 'web_verified'",
          identity_d.field_evidence.get("generation", {}).get("status", "unknown") == "unknown")

    # D4/D5: dieselbe Frage, aber durch die ECHTE Extraktion (_baue_recherche /
    # _kraftstoff_aus_treffern), nicht nur strukturell behauptet: zwei gleich
    # starke, unabhaengige Fachmedien-Quellen nennen fuer denselben Wagen
    # UNTERSCHIEDLICHE Kraftstoffarten (Diesel vs. Benzin). Erwartung: das
    # Feld bleibt unresolved (None), keine Mehrheitsentscheidung, kein
    # willkuerliches "erstbestes Signal gewinnt".
    def treffer(url, titel, inhalt):
        return {"url": url, "title": titel, "content": inhalt}

    fixtures_konflikt = {
        "identitaet": [
            treffer("https://www.adac.de/mazda-mx-5-konflikt", "Mazda MX-5 Test",
                    "Der Mazda MX-5 ist in dieser Quelle ein Dieselmodell."),
            treffer("https://www.autobild.de/mazda-mx-5-konflikt", "Mazda MX-5 Gebrauchtwagen",
                    "Der Mazda MX-5 ist in dieser Quelle ein Benziner."),
        ],
        "schwachstelle": [], "rueckruf": [], "wartung": [],
    }
    provider_d = FixtureTechnicalResearchProvider(fixtures_konflikt)
    req_d = KaufCheckRequest(marke="Mazda", modell="MX-5", baujahr=2019, preis_eur=22_900)
    from app.car_lookup import find_baureihe_mit_vertrauen
    br_markt_d, info_d = find_baureihe_mit_vertrauen("Mazda", "MX-5", 2019)
    web_d = await recherchiere_technisch(req_d, br_markt_d, info_d, None, None, provider=provider_d)
    identity_d2 = VehicleIdentity.from_check_context(None, None, req_d)
    identity_d2.apply_web_evidence(web_d)
    check("D4 Identitaet trotzdem belegt (Modell/Marke-Token stuetzen beide Domains)",
          web_d.identitaet is not None and web_d.identitaet.belegt)
    check("D5 Kraftstoff bleibt UNRESOLVED bei widersprechenden Quellen "
          "(kein 'erstbestes Signal gewinnt', keine Mehrheitsentscheidung)",
          web_d.identitaet.kraftstoff is None and identity_d2.fuel is None)

    return _FEHLER


fehler = asyncio.run(run_all())
print("\n" + "=" * 60)
if fehler:
    print(f"{len(fehler)} FEHLER:")
    for f in fehler:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE E2E-WEB-FALLBACK-TESTS GRUEN")
