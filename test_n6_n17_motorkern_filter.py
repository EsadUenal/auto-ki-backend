"""
N6/N17-Fixpaket 1+2 (Motorkern-Belege durch dieselben Filter wie Claims; sichere
Motorkern-Teilbestaetigung im Motorisierungsgate).

Quellentexte: REAL, wortgleich aus test_rc_f_herstellerquelle_vollstaendiger_replay.py
(Browser/Wayback 29.10.2026). Mit "SYNTHETISCH" gekennzeichnete Gegenproben sind
erfunden und beweisen nur die Filterlogik, keine Fahrzeugfakten.

    python scripts/test_isolated.py test_n6_n17_motorkern_filter.py
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import kaufcheck as kc
from app.models import KaufCheckRequest, KaufCheckResponse
from app.technical_research import (
    FixtureTechnicalResearchProvider, recherchiere_technisch, _ziel, _identitaets_claims,
    werte_identitaet_aus,
)
from app.empfehlungs_policy import identitaet_aufloesen, motorisierung_beleg_text, motorkern_teilbestaetigt
from app.vehicle_identity import VehicleIdentity

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if (detail and not ok) else ""))
    if not ok:
        FAILS.append(name)


def t(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# ── Bereits bekannte, reale Quellen (wortgleich aus
# test_rc_f_evidenzluecken_vertieft.py) ────────────────────────────────────
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

# ── NEU: deutsche Toyota/Lexus-Primaerquelle (woertlich, Browser verifiziert
# 29.10.2026 ueber Wayback-Machine-Snapshot vom 26.09.2020) ────────────────
TOYOTA_MEDIA_DE = t(
    "https://www.toyota-media.de/blog/lexus-modelle/artikel/neues-adaptiv-variables-fahrwerk-fuer-lexus-gs-f-und-rc-f/text",
    "NEUES ADAPTIV VARIABLES FAHRWERK FÜR LEXUS F-MODELLE",
    "Köln, 1. Dezember 2016. Lexus hat die Fahrstabilität und den Fahrkomfort der "
    "beiden F-Modelle GS F (Kraftstoffverbrauch kombiniert 11,2/100km; CO2-"
    "Emissionen 260g/km) und RC F (Kraftstoffverbrauch kombiniert 10,8/100km; "
    "CO2-Emissionen 251g/km) mit der neuesten Generation des adaptiv variablen "
    "Fahrwerks AVS aufgewertet. Der Lexus GS F und der RC F werden beide von "
    "einem 5,0 Liter V8-Saugmotor angetrieben, der 351 kW / 477 PS leistet und "
    "an eine Achtstufen-Sport-Automatik gekoppelt ist. Neben dem neuen adaptiv "
    "variablen Fahrwerk bietet Lexus den GS F jetzt mit einer neuen grauen "
    "(Graphitgrau) und den RC F mit einer neuen schwarzen Lackierung "
    "(Graphitschwarz) an. Der RC F ist nun zudem mit 19-Zoll Leichtmetallrädern "
    "im neuen Doppelspeichen-Design erhältlich. "
    # Woertlich die Marken-Boilerplate am Artikelende (Browser verifiziert) --
    # bewusst MIT uebernommen: prueft, dass der allgemeine Hybrid-Markentext
    # ("umfassende Palette von Hybridfahrzeugen", "Lexus CT ... Vollhybrid")
    # KEINEN powertrain=HEV-Claim fuer den RC F erzeugt (Auftrag: "Der "
    # allgemeine Hybrid-Markentext im Artikel darf RC F nicht als Hybrid "
    # bestaetigen").
    "Lexus: Die 1989 eingeführte Marke Lexus ist weltweit bekannt für "
    "Produktqualität, die Maßstäbe setzt. Lexus ist der erste und bis heute "
    "einzige Hersteller von Premium-Fahrzeugen mit einer umfassenden Palette "
    "von Hybridfahrzeugen. Diese umfasst den ersten Premium-Kompaktwagen mit "
    "Vollhybridantrieb Lexus CT (Kraftstoffverbrauch kombiniert 3,6-3,8l/100km; "
    "CO2-Emissionen kombiniert 82-88g/100km), die elegante Oberklasse-Limousine "
    "Lexus GS, die Luxus-Limousine Lexus LS, den Premium-SUV Lexus RX, den "
    "kompakten Premium-SUV Lexus NX, das Sportcoupé Lexus RC sowie die "
    "Sportlimousine Lexus IS mit unterschiedlichen Motorvarianten. Ein "
    "besonders dynamisches Fahrerlebnis verspricht die auf Rennstrecken "
    "abgestimmte F-Modellreihe, die derzeit aus dem RC F (Kraftstoffverbrauch "
    "kombiniert 10,8/100km; CO2-Emissionen 251g/km) und dem GS F "
    "(Kraftstoffverbrauch kombiniert 11,2/100km; CO2-Emissionen 260g/km) "
    "besteht. Die Designsprache „L-finesse“ verstärkt traditionelle "
    "Markenwerte wie makellose Fertigungsqualität, luxuriöse Interieurs sowie "
    "den Einsatz fortschrittlichster Technologien und trägt damit zur "
    "weltweiten Positionierung der Marke Lexus bei. Seit 1990 ist die Marke "
    "auf dem deutschen Markt vertreten: Exklusive Lexus Foren bieten den "
    "Kunden einen erstklassigen, individuellen Service.")


# ── SYNTHETISCH (nur Filterlogik) ────────────────────────────────────────────
def synth(satz: str, titel: str = "Lexus RC F Presseinformation") -> dict:
    return t("https://www.toyota-media.de/blog/lexus-modelle/artikel/synthetisch/text", titel, satz)


SYN_AUSSERHALB = synth(
    "Lexus RC F Datenblatt Modellstart 01/18 Modellende 03/19. Der RC F wird von einem "
    "5,0 Liter V8-Saugmotor angetrieben.")                       # Fenster NACH Baujahr 2017
SYN_INNERHALB = synth(
    "Lexus RC F Datenblatt Modellstart 01/16 Modellende 12/17. Der RC F wird von einem "
    "5,0 Liter V8-Saugmotor angetrieben.")                       # Positivkontrolle
SYN_GETRENNT = synth(
    "Der RC F ist ein V8-Sportcoupe. Die Track Edition des RC F hat 5,0 Liter Hubraum.")
SYN_MEHRDEUTIG = synth(
    "Der RC F fährt wahlweise mit einem 5,0 Liter V8 oder einem 3,5 Liter V8 Saugmotor.")


async def _lauf(req, web_fixture):
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


def voll(motor, quellen, baujahr=2017, modell="RC F"):
    return asyncio.run(_lauf(KaufCheckRequest(
        marke="Lexus", modell=modell, baujahr=baujahr, kilometerstand=82000, motor=motor,
        kraftstoff="Benzin", leistung_ps=477, getriebe="Automatik", antrieb="Heck", ausstattung=[]),
        {"identitaet": quellen, "wartung": [], "schwachstelle": [], "rueckruf": []}))


def fe(r, feld):
    return r.vehicle_identity.get("field_evidence", {}).get(feld, {})


def motorisierung_luecke(r) -> bool:
    if "Blockierende Identitätslücke" not in r.bericht:
        return False
    zeile = r.bericht.split("Blockierende Identitätslücke", 1)[1].split("\n", 1)[0]
    return "Motorisierung" in zeile


def kern(motor, quellen, baujahr=2017, modell="RC F"):
    w, _ = werte_identitaet_aus(quellen, _ziel("Lexus", modell, baujahr, motor, None))
    return w


HERSTELLER_DE = [CARWIKI, AMS, TOYOTA_MEDIA_DE]
RC = "5.0 V8, Heckantrieb"

print("== A: Filter wirkt auf den Motorkern ==")
w = kern(RC, [ROLLING_HU, ADAC_ULTIMATE, ADAC_AUTOMATIK, CARWIKI, AMS, TOYOTA_JAPAN])
check("A1 nur fremdmarktgedeckelte Japanquelle bestaetigt den Motorkern NICHT", w.motor is None,
      repr(w.motor))
w = kern(RC, HERSTELLER_DE)
check("A2 qualifizierter deutscher Herstellerbeleg bestaetigt den Motorkern", w.motor is not None)
check("A2b Kern vollstaendig belegt", bool((w.feldwerte.get("engine_name") or {}).get("kern_vollstaendig")))
check("A3 SYNTHETISCH: Datenblatt-Fenster NACH dem Zieljahr -> nicht bestaetigt",
      kern(RC, [SYN_AUSSERHALB]).motor is None)
check("A3b SYNTHETISCH Positivkontrolle: Fenster umfasst das Zieljahr -> bestaetigt",
      kern(RC, [SYN_INNERHALB]).motor is not None)
check("A4 falscher Hubraum 4.0 -> nicht bestaetigt", kern("4.0 V8, Heckantrieb", HERSTELLER_DE).motor is None)
check("A5 fremdes Modell IS F -> Identitaet nicht belegt, nichts bestaetigt",
      kern(RC, HERSTELLER_DE, modell="IS F").motor is None)
check("A6 SYNTHETISCH: Hubraum und Bauart in verschiedenen Saetzen -> nicht bestaetigt",
      kern(RC, [SYN_GETRENNT]).motor is None)
check("A7 SYNTHETISCH: zwei Hubraeume im selben Satz (mehrdeutig) -> nicht bestaetigt",
      kern(RC, [SYN_MEHRDEUTIG]).motor is None)

print("\n== B: Gate, Anzeige, Teilbestaetigung (volle run_kaufcheck-Strecke) ==")
r = voll(RC, HERSTELLER_DE)
e = fe(r, "engine_name")
check("B1 engine_name bleibt user_only (keine Vollbestaetigung)", e.get("verification_state") == "user_only",
      str(e.get("verification_state")))
check("B1b Teilbestaetigung 5.0 V8 vermerkt", e.get("bestaetigte_teile") == ["5.0 V8"], str(e.get("bestaetigte_teile")))
check("B1c Motorkern-Flag gesetzt", e.get("motorkern_teilbestaetigt") is True)
check("B1d Gate: keine Motorisierungsluecke mehr", not motorisierung_luecke(r))
check("B1e Anzeige nennt den bestaetigten Teil und den ungeprueften Rest",
      "„5.0 V8“ durch Webquellen bestätigt, übrige Angabe nicht unabhängig geprüft" in r.bericht)
check("B2 Antrieb bleibt separat unbestaetigt (user_only)", fe(r, "drivetrain").get("verification_state") == "user_only")
check("B3 Kraftstoff bleibt unbestaetigt", fe(r, "fuel").get("verification_state") == "user_only")
check("B3b Elektrifizierung bleibt UNKNOWN", fe(r, "powertrain").get("verification_state") == "unknown"
      and not r.vehicle_identity.get("powertrain"))
check("B3c Bericht: Elektrifizierung 'nicht sicher bekannt'",
      "Antriebsart (Elektrifizierung): nicht sicher bekannt" in r.bericht)
r_jp = voll(RC, [ROLLING_HU, ADAC_ULTIMATE, ADAC_AUTOMATIK, CARWIKI, AMS, TOYOTA_JAPAN])
check("B4 nur Japanquelle: Motorkern-Flag NICHT gesetzt",
      fe(r_jp, "engine_name").get("motorkern_teilbestaetigt") is not True)
check("B4b nur Japanquelle: Motorisierungsluecke bleibt", motorisierung_luecke(r_jp))
r_k = voll("5.0 V8 Kompressor, Heckantrieb", HERSTELLER_DE)
check("B5 zusaetzliche unbestaetigte Motoreigenschaft im Rest: kein Flag",
      fe(r_k, "engine_name").get("motorkern_teilbestaetigt") is not True)
check("B5b ... und Motorisierungsluecke bleibt", motorisierung_luecke(r_k))
r_plain = voll("5.0 V8", HERSTELLER_DE)
check("B6 Eingabe ohne Antriebszusatz: unveraendert user_confirmed",
      fe(r_plain, "engine_name").get("verification_state") == "user_confirmed")
r_2020 = voll("5.0 V8, Heckantrieb", [SYN_AUSSERHALB])
check("B7 SYNTHETISCH ausserhalb Zieljahr bis zur Ausgabe: Luecke bleibt", motorisierung_luecke(r_2020))

print("\n== C: Hybrid-Werbetext erzeugt keinen RC-F-Powertrain-Claim ==")
cl = _identitaets_claims(TOYOTA_MEDIA_DE, _ziel("Lexus", "RC F", 2017, RC, None))
check("C1 kein powertrain-Claim aus dem Markentext", not [c for c in cl if c["feld"] == "powertrain"])

print("\n== D: bestehender Motorcode-Weg und Gate-Grenzen (direkt am Gate) ==")


def ident(engine_name_fe, code_fe=None, code=None):
    v = VehicleIdentity.__new__(VehicleIdentity)
    v.make, v.model, v.generation = "Lexus", "RC F", "XC1"
    v.engine_name, v.engine_code = "5.0 V8, Heckantrieb", code
    best = {"verification_state": "user_confirmed", "confirmed_by": ["web"]}
    v.field_evidence = {"make": dict(best), "model": dict(best), "generation": dict(best),
                        "engine_name": engine_name_fe}
    if code_fe:
        v.field_evidence["engine_code"] = code_fe
    return v


nur_user = {"verification_state": "user_only", "confirmed_by": []}
teil = {"verification_state": "user_only", "confirmed_by": [], "bestaetigte_teile": ["5.0 V8"],
        "motorkern_teilbestaetigt": True}
check("D1 Motorcode belegt, engine_name user_only: Gate weiter erfuellt (alter Weg)",
      "Motorisierung" not in identitaet_aufloesen(
          ident(nur_user, {"verification_state": "web_only", "confirmed_by": ["web"]}, "2UR-GSE")).fehlend)
check("D2 weder Code noch Kern: Motorisierung fehlt", "Motorisierung" in identitaet_aufloesen(ident(nur_user)).fehlend)
check("D3 Teilbestaetigung: Gate erfuellt", "Motorisierung" not in identitaet_aufloesen(ident(teil)).fehlend)
check("D4 Teilbestaetigung + Webkonflikt: Gate NICHT erfuellt",
      "Motorisierung" in identitaet_aufloesen(ident(dict(teil, web_conflict="4.0 V8"))).fehlend)
check("D5 Flag ohne bestaetigte Teile: nicht anerkannt",
      not motorkern_teilbestaetigt(ident({"verification_state": "user_only", "motorkern_teilbestaetigt": True})))
check("D6 Beleg-Text benennt Teilbestaetigung ehrlich",
      "Antriebsart) nicht geprüft" in motorisierung_beleg_text(ident(teil)))

print("\n== E: Voll- und Teilbestaetigung folgen einer Regel (Zusatzmerkmale) ==")
SYN_KOMPRESSOR = synth("Der RC F wird von einem 5,0 Liter V8 Kompressor-Motor angetrieben.")


def zeile(motor, quellen):
    r = voll(motor, quellen)
    e = fe(r, "engine_name")
    return r, e.get("verification_state"), e.get("bestaetigte_teile"), not motorisierung_luecke(r)


TEIL_TXT = "„5.0 V8“ durch Webquellen bestätigt, übrige Angabe nicht unabhängig geprüft"
for motor, state, teile, gate in (
        ("5.0 V8", "user_confirmed", None, True),
        ("5.0 V8, Heckantrieb", "user_only", ["5.0 V8"], True),
        ("5.0 V8 Kompressor", "user_only", ["5.0 V8"], False),
        ("5.0 V8 Kompressor, Heckantrieb", "user_only", ["5.0 V8"], False),
        ("5.0 V8 Biturbo", "user_only", ["5.0 V8"], False)):       # neutrale Gegenprobe
    r, st, tl, g = zeile(motor, HERSTELLER_DE)
    print(f"     {motor!r:36s} state={st} teile={tl} gate_frei={g}")
    check(f"E {motor!r}: Feldstatus", st == state, str(st))
    check(f"E {motor!r}: bestaetigte Teile", tl == teile, str(tl))
    check(f"E {motor!r}: Motorisierungsgate", g == gate, str(g))
    if teile:
        check(f"E {motor!r}: Anzeige nennt nur den belegten Teil", TEIL_TXT in r.bericht)
    check(f"E {motor!r}: Zusatzmerkmal nie als bestaetigt angezeigt",
          "Kompressor“ durch Webquellen" not in r.bericht and "Biturbo“ durch Webquellen" not in r.bericht)

print("  -- belegter Zusatz (SYNTHETISCH: Text nennt Kompressor ausdruecklich)")
for motor, state, gate in (("5.0 V8 Kompressor", "user_confirmed", True),
                           ("5.0 V8 Kompressor, Heckantrieb", "user_only", True)):
    r, st, tl, g = zeile(motor, [SYN_KOMPRESSOR, CARWIKI, AMS])
    check(f"E+ {motor!r}: belegter Zusatz nicht pauschal abgelehnt (Status)", st == state, str(st))
    check(f"E+ {motor!r}: Gate", g == gate, str(g))
check("E+ Hubraum und Zusatz in verschiedenen Saetzen: nichts bestaetigt",
      kern("5.0 V8 Kompressor", [SYN_GETRENNT]).motor is None)

print("\n== F: Zusatzwort-Pruefung gegen Negation und falsche Zuordnung (SYNTHETISCH) ==")
F_FAELLE = {
    "ohne":      synth("Der RC F wird von einem 5,0 Liter V8 Saugmotor ohne Kompressor angetrieben."),
    "nicht":     synth("Der RC F wird von einem 5,0 Liter V8 angetrieben, nicht die Kompressor-Variante."),
    "kein":      synth("Der RC F hat einen 5,0 Liter V8 Motor, kein Kompressor."),
    "andere":    synth("Der RC F wird von einem 5,0 Liter V8 angetrieben, die Sonderedition dagegen "
                       "von einem 5,0 Liter V8 Kompressor."),
    "alternativ": synth("Der RC F wird von einem 5,0 Liter V8 angetrieben; alternativ ist ein Kompressor "
                         "nur fuer die Sonderedition erhaeltlich."),
}
for name, quelle in F_FAELLE.items():
    for motor in ("5.0 V8 Kompressor", "5.0 V8 Kompressor, Heckantrieb"):
        r, st, tl, g = zeile(motor, [quelle, CARWIKI, AMS])
        print(f"     {name:10s} {motor!r:34s} state={st} teile={tl} gate_frei={g}")
        check(f"F {name} / {motor!r}: Kompressor nicht bestaetigt (Status user_only)", st == "user_only", str(st))
        check(f"F {name} / {motor!r}: nur '5.0 V8' als Teil", tl == ["5.0 V8"], str(tl))
        check(f"F {name} / {motor!r}: Gate nicht freigegeben", g is False, str(g))
        check(f"F {name} / {motor!r}: Anzeige nennt Kompressor nie als bestaetigt",
              "Kompressor“ durch Webquellen" not in r.bericht and "5.0 V8 Kompressor“ durch" not in r.bericht)

SYN_MIT = synth("Der RC F wird von einem 5,0 Liter V8 Saugmotor mit Kompressor angetrieben.")
for motor, state, gate in (("5.0 V8 Kompressor", "user_confirmed", True),
                           ("5.0 V8 Kompressor, Heckantrieb", "user_only", True)):
    r, st, tl, g = zeile(motor, [SYN_MIT, CARWIKI, AMS])
    print(f"     mit        {motor!r:34s} state={st} teile={tl} gate_frei={g}")
    check(f"F mit / {motor!r}: positiv belegter Zusatz bestaetigt (Status)", st == state, str(st))
    check(f"F mit / {motor!r}: Gate", g == gate, str(g))
    if state == "user_only":
        check(f"F mit / {motor!r}: Teil nennt den belegten Zusatz", tl == ["5.0 V8 Kompressor"], str(tl))

print("\nERGEBNIS:", "ALLE OK" if not FAILS else f"{len(FAILS)} FEHLER: {FAILS}")
raise SystemExit(1 if FAILS else 0)
