"""
Quellenbindungs-Folgeauftrag (29.10.2026, "technische Quellenbindung vor
Konsens"): verfolgt einen Fremdwert (ein im selben Satz/Segment genanntes
Vergleichsfahrzeug) durch den ECHTEN run_kaufcheck-Identitaetspfad bis zur
sichtbaren VehicleIdentity -- nicht nur bis zur rohen Claim-Liste
(`_identitaets_claims`, s. test_motorcode_kw_ps_hubraum_fremdeinheit.py
Abschnitt 3e).

Ergebnis (dokumentiert, KEIN Code-Fix noetig -- bereits bestehendes,
korrektes Verhalten): der Fremdwert bleibt ein interner Claim-Kandidat und
erreicht NIE die sichtbare Identitaet. Das Zahlenverhaeltnis (kW<->PS) allein
haette KEINE Fahrzeugbindung bewiesen -- die tatsaechliche Bindung leistet
hier die bestehende Satz-/Segmentbindung VOR `_identitaets_claims()`
(`_segment_kontext_saetze()`), zusammen mit dem Umstand, dass eine einzelne,
unter der Konsensschwelle bleibende Quelle (hier: die Vergleichsseite allein)
nie unabhaengig bestaetigt.

Positivkontrolle (Abschnitt 2): damit der Ausschluss nicht nur deshalb
"funktioniert", weil in diesem Modul grundsaetzlich NIE etwas bestaetigt
wird, bestaetigen zwei ECHTE, unabhaengige RC-F-Quellen (ADAC + carwiki.de,
OHNE Fremdfahrzeug) denselben Mechanismus in die GEGENRICHTUNG: Kraftstoff
und Antrieb werden tatsaechlich sichtbar bestaetigt.

Deterministisch (alle Provider gestubbt), kein Netzwerk/LLM/bezahlter
Aufruf, isolierte DB.

    python scripts/test_isolated.py test_fremdwert_identity_pfad.py
"""
import asyncio
import sys

sys.path.insert(0, ".")

from app import kaufcheck as kc
from app.models import KaufCheckRequest, KaufCheckResponse
from app.technical_research import FixtureTechnicalResearchProvider, recherchiere_technisch

FEHLER = []


def check(name, cond, detail=""):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}" + (f" -- {detail}" if detail else ""))
    if not cond:
        FEHLER.append(name)


def t(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


async def _lauf(req, web_fixture):
    orig = (kc.call_gemini_json, kc.recherchiere_technisch, kc.tavily_search_with_fallback,
            kc.marktpreis_recherche_moeglich, kc.vertiefe_marktrecherche)

    async def gemini(system, user):
        return {"bericht": "KAUFEN NACH BESICHTIGUNG.", "empfehlung": "kaufen_nach_besichtigung",
                "risiko_evidence_ids": [], "empfehlung_evidence_ids": []}

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


REQ = dict(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000, motor="5.0 V8",
          kraftstoff="Benzin", leistung_ps=477, getriebe="Automatik", antrieb="Heck", ausstattung=[])

# Reale, bereits anderweitig genutzte RC-F-Quelle (ADAC, 29.10.2026 per
# Browser nachgeprueft, s. test_rc_f_evidenzluecken_vertieft.py).
ADAC = t("https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/288844/",
        "Lexus RC F Automatik (01/18 - 03/19): Technische Daten, Bilder, Preise | ADAC",
        "Marke Lexus Modell RC F Typ Automatik Baureihe RC (UXC1) (12/14 - 06/25) "
        "Motorcode 2UR-GSE Leistung maximal in PS 464 Antriebsart Hinterrad "
        "Getriebeart Automatikgetriebe Kraftstoffart Super Hubraum 4969 ccm")
CARWIKI = t("https://carwiki.de/lexus-rc-hsn-tsn-schluesselnummer/",
           "Lexus RC HSN & TSN | Tabelle aller Schluesselnummern",
           "Uebersicht: Alle Schluesselnummern (HSN/TSN) der Lexus RC Baureihen. "
           "Hersteller: Lexus Fahrzeug: RC Baureihe: RC XC1 Karosserie: Coupe "
           "MODELL HSN SCHLUESSELNUMMER TSN SCHLUESSELNUMMER KRAFTSTOFF HUBRAUM LEISTUNG BAUJAHR "
           "Lexus RC F Carbon Automatik 5013 AJM Benzin 4969 cm3 477 PS 2014 bis 2025")
# Dritte reale, unabhaengige Quelle -- noetig, damit `_identitaet_belegt()`
# (Marke+Modell-EXISTENZ, ein eigenes, dem Feld-Konsens VORGELAGERTES Tor)
# ueberhaupt genug "starke" Domains sieht; mit nur ADAC+carwiki.de bleibt
# dieses Tor sonst zu (direkt gegengeprueft: belegt=False, domains=1,
# carwiki.de zaehlt dort NICHT als "stark") UND verhindert dann JEDE
# Feldbestaetigung -- unabhaengig vom Feld-Konsensgewicht. Das ist eine
# bestehende, von diesem Test unberuehrte zweite Schwelle, keine neue.
# Wortgleich mit test_rc_f_evidenzluecken_vertieft.py (dort nachweislich
# belegt=True, direkt gegengeprueft mit ADAC+carwiki.de+AMS).
ROLLING_HU = t("https://shop.rolling.hu/catalog/SCHAEFFLER/lexus-rc-c1-f-usc10",
              "LEXUS RC (_C1_) F (USC10_) Part list - Rolling Kft. webshop",
              "SCHAEFFLER - LEXUS RC (_C1_) F (USC10_). Manufacturer LEXUS. Model RC (_C1_). "
              "Type F (USC10_). Construction Year from 08.2014. Engine Output 330kW / 449HP.")
AMS = t("https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
       "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
       "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Leistung 181 PS bis "
       "477 PS. Hubraum 1998 cm3 bis 4969 cm3. LEXUS RC F TRACK EDITION IM SUPERTEST: "
       "Was kann der V8-Sauger auf der Rennstrecke? LEXUS LC 500 UND RC F: "
       "V8-Sportler auf Abschiedstour.")
# Zweite, real genutzte Quelle, die "Rear-wheel drive" nennt (ADACs "Hinterrad"
# allein bleibt unter der Konsensschwelle) -- Toyota-Primaerquelle, s.
# test_rc_f_evidenzluecken_vertieft.py / test_motorcode_kw_ps_hubraum_fremdeinheit.py.
TOYOTA_PRESSE = t(
    "https://global.toyota/en/detail/4101939",
    "Lexus Launches All-new 'RC F' High-performance Coupe in Japan | Toyota Motor "
    "Corporation Official Global Website",
    "Tokyo, October 23, 2014-Lexus' all-new high-performance coupe, the \"RC F\", "
    "went on sale today in Japan. Engine Transmission Driveline Price RC F Carbon "
    "Exterior package 2UR-GSE (V8 5.0-liter) 8-Speed SPDS Rear-wheel drive "
    "10,300,000 yen 9,530,000 yen.")
# SYNTHETISCHE Vergleichs-Quelle mit einem FREMDFAHRZEUG (BMW M4) im selben
# Satz/Segment -- erfunden fuer diesen Test, keine reale Veroeffentlichung.
VERGLEICH_FREMDWERT = t(
    "https://sportwagen-vergleich.test/rc-f-vs-m4",
    "Lexus RC F im Vergleichstest gegen den BMW M4",
    "Vergleichstest (kW[PS]/rpm): Der BMW M4 leistet 317 [431] / 7,300, "
    "der Lexus RC F leistet 351 [477] / 7,100.")


async def main():
    print("=" * 70)
    print("1) Fremdwert-Ausschluss: BMW M4 (431 PS) neben Lexus RC F (477 PS) "
          "im selben Segment")
    print("=" * 70)
    r1 = await _lauf(KaufCheckRequest(**REQ), web_fixture={
        "identitaet": [ADAC, CARWIKI, ROLLING_HU, AMS, VERGLEICH_FREMDWERT],
        "wartung": [], "schwachstelle": [], "rueckruf": []})
    vi1 = r1.vehicle_identity
    e1 = vi1.get("field_evidence", {}).get("horsepower", {})
    check("1a sichtbare Leistung bleibt der Nutzerwert 477, NICHT der Fremdwert 431",
          vi1.get("horsepower") == 477, f"horsepower={vi1.get('horsepower')}")
    check("1b Fremdwert 431 taucht NIRGENDS im field_evidence-Eintrag fuer horsepower auf",
          "431" not in str(e1), str(e1))
    check("1c Leistung bleibt NICHT faelschlich web-bestaetigt (keine ausreichende, "
          "widerspruchsfreie Quellenlage)",
          e1.get("verification_state") != "user_confirmed", str(e1))

    print("\n" + "=" * 70)
    print("2) Positivkontrolle: OHNE Fremdfahrzeug bestaetigen dieselben Mechanismen "
          "tatsaechlich (Ausschluss ist nicht nur 'bestaetigt nie etwas')")
    print("=" * 70)
    r2 = await _lauf(KaufCheckRequest(**REQ), web_fixture={
        "identitaet": [ADAC, CARWIKI, ROLLING_HU, AMS, TOYOTA_PRESSE],
        "wartung": [], "schwachstelle": [], "rueckruf": []})
    vi2 = r2.vehicle_identity
    e2_fuel = vi2.get("field_evidence", {}).get("fuel", {})
    e2_drive = vi2.get("field_evidence", {}).get("drivetrain", {})
    check("2a OHNE Fremdfahrzeug: Kraftstoff wird tatsaechlich web-bestaetigt",
          e2_fuel.get("verification_state") == "user_confirmed", str(e2_fuel))
    check("2b OHNE Fremdfahrzeug: Antrieb wird tatsaechlich web-bestaetigt",
          e2_drive.get("verification_state") == "user_confirmed", str(e2_drive))

    print("\n" + "=" * 70)
    if FEHLER:
        print(f"{len(FEHLER)} FEHLER:")
        for f in FEHLER:
            print(" -", f)
        sys.exit(1)
    print("Alle Fremdwert-Identitaetspfad-Tests bestanden.")


asyncio.run(main())
