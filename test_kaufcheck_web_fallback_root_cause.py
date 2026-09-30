"""
Regressionstests: Root-Cause-Fixes "DATABASE-FIRST + Web-Fallback" (Auftrag
kaufcheck-web-fallback-root-cause).

KEIN Netzwerk, KEIN echter Gemini-/Tavily-Call. Alle Web-Szenarien laufen über
den FixtureTechnicalResearchProvider (dieselbe Fixture-Basis wie
test_technical_fallback.py).

  A) Fuel vs. Powertrain (app/kraftstoff_powertrain.py)
  B) Provenance: Nutzer-Motorcode (M264) übersteht fehlende DB-Referenz
  C) Optionale Ausstattung (EDC) nur mit "falls vorhanden" ohne Bestätigung
  D) Inspection-Mapping: COMAND -> Infotainment, nicht Motor/Getriebe
  E) Tuning-Tri-State: "nicht angegeben" bleibt UNKNOWN
  F) Web-Identität erreicht die KANONISCHE VehicleIdentity (Mazda-MX-5-Fall)
  G) Web nicht belegt -> keine Web-Identität übernommen, ehrliche Degradierung
  H) DB vs. Web-Konflikt: DB gewinnt, Web wird nicht übernommen
  I) Datenbasis nennt "Webrecherche" NUR bei tatsächlicher Verwendung
  J) Fehlende-Angabe-Hinweis ist dynamisch (nennt nur wirklich Fehlendes)
  K) Identitäts-Floor: weder DB noch Web bestätigt -> Empfehlung "unbekannt"

    python test_kaufcheck_web_fallback_root_cause.py
"""
import asyncio

import app.recall_filter as _rf

# Fixture-Isolation wie in test_technical_fallback.py: keine Live-DB-Lesevorgänge.
_rf.get_rueckruf_referenzen_kurz = lambda: []

from app.bekannte_fakten import tuning_status
from app.car_lookup import find_baureihe_mit_vertrauen, find_motor, _fehlende_angabe_none
from app.evidence import build_insights
from app.kaufaktionen import _ist_optionale_ausstattung, _komponente, build_kaufaktionen
from app.kaufcheck_bericht import datenbasis
from app.kraftstoff_powertrain import (
    canonical_fuel, canonical_powertrain, fuel_aus_freitext, ist_fuel_widerspruch,
)
from app.models import KaufCheckRequest, TechnischeRecherche, WebVehicleIdentity
from app.technical_research import (
    FixtureTechnicalResearchProvider, _extrahiere_fakten, recherchiere_technisch,
)
from app.vehicle_identity import VehicleIdentity, motorcode_kandidat

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    status = "OK  " if bedingung else "FAIL"
    print(f"[{status}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def treffer(url: str, titel: str, inhalt: str) -> dict:
    return {"url": url, "title": titel, "content": inhalt}


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== A) Fuel vs. Powertrain ===")

check("A1 Mild-Hybrid-Benziner: Kraftstoffart ableitbar = benzin",
      canonical_fuel("Mild-Hybrid", "2.0 TFSI (35 TFSI)", "CZAC") == "benzin")
check("A2 Mild-Hybrid-Diesel: Kraftstoffart ableitbar = diesel",
      canonical_fuel("Mild-Hybrid", "2.0 TDI (35 TDI)", "DEUB") == "diesel")
check("A3 Mild-Hybrid ohne ableitbares Signal -> None (nicht geraten)",
      canonical_fuel("Mild-Hybrid", "Basismotor", None) is None)
check("A4 Benzin+Mild-Hybrid ist KEIN Kraftstoff-Widerspruch",
      not ist_fuel_widerspruch(fuel_aus_freitext("Benzin"),
                               canonical_fuel("Mild-Hybrid", "2.0 TFSI", None)))
check("A5 Diesel+Mild-Hybrid ist KEIN Kraftstoff-Widerspruch",
      not ist_fuel_widerspruch(fuel_aus_freitext("Diesel"),
                               canonical_fuel("Mild-Hybrid", "2.0 TDI", None)))
check("A6 Benzin vs. echter Diesel bleibt ein Widerspruch",
      ist_fuel_widerspruch(fuel_aus_freitext("Benzin"), canonical_fuel("Diesel")))
check("A7 Plug-in-Hybrid -> Powertrain PHEV, nie als Kraftstoff",
      canonical_powertrain("Plug-in-Hybrid") == "PHEV" and canonical_fuel("Plug-in-Hybrid", "330e") is None)
check("A8 Direkte Kraftstoffwerte unverändert (Benzin/Diesel/Elektro)",
      canonical_fuel("Benzin") == "benzin" and canonical_fuel("Diesel") == "diesel"
      and canonical_fuel("Elektro") == "elektro")

# Identity-Ebene: Mercedes C300 W205 (Test 4) — Benzin-Mild-Hybrid, Nutzer sagt Benzin.
_baureihe_c300 = {"marke": "Mercedes-Benz", "modell": "C-Klasse", "generation": "W205"}
_motor_c300 = {"bezeichnung": "C 300 (2.0 Turbo, Mild-Hybrid)", "motorcode": None,
              "kraftstoff": "Mild-Hybrid", "leistung_ps": 258, "antrieb": "Heck"}
_req_c300 = KaufCheckRequest(marke="Mercedes-Benz", modell="C-Klasse", baujahr=2019,
                             kilometerstand=73_600, motor="2.0 Turbo M264",
                             kraftstoff="Benzin", leistung_ps=258, preis_eur=31_900)
_identity_c300 = VehicleIdentity.from_check_context(_baureihe_c300, _motor_c300, _req_c300)
check("A9 Identity.fuel bleibt 'Benzin' (Nutzerwert), keine Mild-Hybrid-Verwechslung",
      (_identity_c300.fuel or "").strip().lower() == "benzin")
check("A10 Identity.powertrain = MHEV", _identity_c300.powertrain == "MHEV")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== B) Provenance: Nutzer-Motorcode übersteht fehlende DB-Referenz ===")

check("B1 M264 aus Freitext extrahiert", motorcode_kandidat("2.0 Turbo M264") == "M264")
check("B2 TDI/TSI werden NICHT als Motorcode missverstanden",
      motorcode_kandidat("2.0 TDI") is None and motorcode_kandidat("2.0 TFSI") is None)
check("B3 Identity.engine_code = 'M264' (DB kennt keinen Code)",
      _identity_c300.engine_code == "M264")
check("B4 Provenance von engine_code ist 'user', nicht stillschweigend verloren",
      _identity_c300.field_evidence.get("engine_code", {}).get("provenance") == ["user"])

# DB-Code hat weiterhin Vorrang, wenn vorhanden.
_motor_mit_code = {**_motor_c300, "motorcode": "M264-ECHT-DB"}
_identity_db_code = VehicleIdentity.from_check_context(_baureihe_c300, _motor_mit_code, _req_c300)
check("B5 vorhandener DB-Code wird NICHT durch Freitext-Kandidat ersetzt",
      _identity_db_code.engine_code == "M264-ECHT-DB")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== C) Optionale Ausstattung (EDC) — §8 ===")

check("C1 EDC als optionale Ausstattung erkannt", _ist_optionale_ausstattung("EDC-Dämpfer"))
check("C2 Bremsscheibe ist KEINE optionale Ausstattung", not _ist_optionale_ausstattung("Bremsscheiben vorne"))

_baureihe_m4 = {"marke": "BMW", "modell": "M4", "generation": "F82",
               "schwachstellen_baureihe": [
                   {"bauteil": "EDC-Dämpfer", "beschreibung": "Adaptive Dämpfer können undicht werden.",
                    "schweregrad": "mittel", "betroffene_baujahre": None},
               ]}
_motor_m4 = {"bezeichnung": "M4", "motorcode": "S55B30", "kraftstoff": "Benzin",
            "leistung_ps": 431, "antrieb": "Heck", "schwachstellen_motor": []}


def _req_m4(ausstattung=None):
    return KaufCheckRequest(marke="BMW", modell="M4", baujahr=2016, kilometerstand=69_500,
                            motor="S55B30", kraftstoff="Benzin", leistung_ps=431,
                            getriebe="Schaltgetriebe", preis_eur=43_500,
                            ausstattung=ausstattung or [])


_ins_m4_ohne = build_insights(_baureihe_m4, _motor_m4, [], _req_m4())
_ka_m4_ohne = build_kaufaktionen(_req_m4(), _baureihe_m4, _motor_m4, _ins_m4_ohne)
_edc_besichtigung = next((a for a in _ka_m4_ohne.besichtigung.fahrzeugspezifisch
                          if "edc" in (a.titel or "").lower() or "edc" in (a.aktion or "").lower()
                          or "dämpfer" in (a.aktion or "").lower()), None)
check("C3 EDC ohne bestätigte Ausstattung: Aktion existiert",
      _edc_besichtigung is not None)
check("C4 ... aber als 'falls vorhanden' gekennzeichnet, nicht als sichere Aussage",
      _edc_besichtigung is not None and "falls" in (_edc_besichtigung.aktion or "").lower())

_ins_m4_mit = build_insights(_baureihe_m4, _motor_m4, [], _req_m4(["EDC", "M Sportsitze"]))
_ka_m4_mit = build_kaufaktionen(_req_m4(["EDC", "M Sportsitze"]), _baureihe_m4, _motor_m4, _ins_m4_mit)
_edc_bestaetigt = next((a for a in _ka_m4_mit.besichtigung.fahrzeugspezifisch
                        if "dämpfer" in (a.aktion or "").lower()), None)
check("C5 EDC MIT bestätigter Ausstattung: kein 'falls vorhanden'-Vorbehalt mehr",
      _edc_bestaetigt is not None and "falls" not in (_edc_bestaetigt.aktion or "").lower())


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== D) Inspection-Mapping: COMAND -> Infotainment ===")

_komp_comand = _komponente("COMAND-System")
check("D1 COMAND wird der Infotainment-Klasse zugeordnet", _komp_comand is not None
      and _komp_comand["schluessel"] == "infotainment")
check("D2 Infotainment-Probefahrttext existiert NICHT (kein Motor-/Getriebe-Test)",
      _komp_comand is not None and _komp_comand.get("probefahrt") is None)
check("D3 Besichtigungstext nennt Display/Bedienelemente, keine Beschleunigung",
      _komp_comand is not None and "beschleunig" not in (_komp_comand.get("besichtigung") or "").lower())


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== E) Tuning-Tri-State ===")

_req_tuning_na = KaufCheckRequest(marke="Mercedes-Benz", modell="C-Klasse", baujahr=2019,
                                  kilometerstand=73_600, preis_eur=31_900, tuning="nicht angegeben")
check("E1 'nicht angegeben' -> UNKNOWN (nicht 'present')", tuning_status(_req_tuning_na) == "unknown")

_req_tuning_keine = KaufCheckRequest(marke="Mercedes-Benz", modell="C-Klasse", baujahr=2019,
                                     kilometerstand=73_600, preis_eur=31_900, tuning="keine Angaben")
check("E2 'keine Angaben' -> UNKNOWN", tuning_status(_req_tuning_keine) == "unknown")

_req_tuning_ja = KaufCheckRequest(marke="Mercedes-Benz", modell="C-Klasse", baujahr=2019,
                                  kilometerstand=73_600, preis_eur=31_900, tuning="Chiptuning Stage 2")
check("E3 echte Angabe bleibt 'present'", tuning_status(_req_tuning_ja) == "present")

_req_tuning_nein = KaufCheckRequest(marke="Mercedes-Benz", modell="C-Klasse", baujahr=2019,
                                    kilometerstand=73_600, preis_eur=31_900, tuning="kein Tuning")
check("E4 explizite Verneinung bleibt 'absent'", tuning_status(_req_tuning_nein) == "absent")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== F) Web-Identität erreicht die kanonische VehicleIdentity (Mazda MX-5) ===")


def fixtures_mazda_mx5() -> dict:
    return {
        "identitaet": [
            treffer("https://www.adac.de/mazda-mx-5-test", "Mazda MX-5 im Test",
                    "Der Mazda MX-5 ist ein Roadster mit 2.0 SKYACTIV-G Benzinmotor."),
            treffer("https://www.autobild.de/mazda-mx-5", "Mazda MX-5 Gebrauchtwagen",
                    "Mazda MX-5: Roadster, 2.0 SKYACTIV-G, Benzin, Hinterradantrieb."),
        ],
        "schwachstelle": [], "rueckruf": [], "wartung": [],
    }


def _lauf(marke, modell, baujahr, motor=None, fixtures=None, fehler=False, **kw):
    req = KaufCheckRequest(marke=marke, modell=modell, baujahr=baujahr, motor=motor, **kw)
    br_markt, info = find_baureihe_mit_vertrauen(marke, modell, baujahr)
    mo_markt = find_motor(br_markt, motor) if br_markt else None
    br, mo = (br_markt, mo_markt) if info["belastbar"] else (None, None)
    provider = FixtureTechnicalResearchProvider(fixtures, fehler=fehler)
    web = asyncio.run(recherchiere_technisch(req, br_markt, info, br, mo, provider=provider))
    identity = VehicleIdentity.from_check_context(br, mo, req)
    identity.apply_web_evidence(web)
    return dict(req=req, info=info, br=br, mo=mo, web=web, identity=identity)



# Bewusst OHNE eigene Kraftstoffangabe des Nutzers: nur so ist das Feld vor dem
# Web-Fallback wirklich "unknown" und der Merge testet eine ECHTE Lücke statt
# eine bereits vom Nutzer belegte Angabe nur zu wiederholen (`kraftstoff` ist
# das einzige Feld, das `technical_research.py` tatsächlich unabhängig aus dem
# Webtext ableitet — `motor`/`leistung_ps` bestätigen nur eine vorhandene
# Nutzerangabe, siehe `_leistung_aus_treffern`).
_f = _lauf("Mazda", "MX-5", 2019, kilometerstand=58_700, getriebe="Schaltgetriebe",
          preis_eur=22_900, fixtures=fixtures_mazda_mx5())
check("F1 DB-Miss (Mazda nicht in ENFAL-DB)", _f["br"] is None)
check("F2 Web-Identität belegt (2 unabhängige Fachmedien-Domains)",
      _f["web"] is not None and _f["web"].identitaet is not None and _f["web"].identitaet.belegt)
check("F3 Kraftstoffangabe war vor dem Merge unbekannt",
      _f["req"].kraftstoff is None)
check("F4 Identity.fuel aus Web ergänzt ('benzin' aus dem Webtext abgeleitet)",
      _f["identity"].fuel == "benzin")
check("F5 Provenance des ergänzten Feldes ist 'web', nicht 'enfal'",
      _f["identity"].field_evidence.get("fuel", {}).get("provenance") == ["web"])
check("F6 kein Feld erhält die DB-Herkunft 'identified' durch Web",
      all(fe.get("status") != "identified"
          for name, fe in _f["identity"].field_evidence.items()
          if fe.get("provenance") == ["web"]))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== G) Web nicht belegt -> keine Übernahme, ehrliche Degradierung ===")


def fixtures_ohne_treffer() -> dict:
    return {"identitaet": [], "schwachstelle": [], "rueckruf": [], "wartung": []}


_g = _lauf("Mazda", "MX-5", 2019, motor="2.0 SKYACTIV-G", kraftstoff="Benzin",
          kilometerstand=58_700, preis_eur=22_900, fixtures=fixtures_ohne_treffer())
check("G1 Web NICHT belegt (keine Treffer)",
      _g["web"] is not None and (_g["web"].identitaet is None or not _g["web"].identitaet.belegt))
check("G2 Identity.generation bleibt unbekannt (keine Erfindung)",
      _g["identity"].generation is None)
check("G3 apply_web_evidence ist ein sicheres No-Op ohne belegte Identität",
      _g["identity"].field_evidence.get("engine_name", {}).get("provenance") != ["web"])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== H) DB vs. Web-Konflikt: DB gewinnt ===")

# Web behauptet einen anderen Motor als die (sichere) DB-Referenz — darf NICHT
# übernommen werden, auch wenn `apply_web_evidence` aufgerufen wird.
_web_konflikt = TechnischeRecherche(
    ausgeloest_durch="test",
    identitaet=WebVehicleIdentity(belegt=True, marke="BMW", modell="330i",
                                  motor="Fantasiemotor XYZ", kraftstoff="diesel",
                                  leistung_ps=999, confidence="hoch", belegende_domains=3),
)
_baureihe_330i = {"marke": "BMW", "modell": "3er", "generation": "G20"}
_motor_330i = {"bezeichnung": "330i", "motorcode": "B48B20", "kraftstoff": "Benzin",
              "leistung_ps": 258, "antrieb": "Heck"}
_req_330i = KaufCheckRequest(marke="BMW", modell="3er", baujahr=2020, kilometerstand=64_200,
                             motor="330i", kraftstoff="Benzin", leistung_ps=258, preis_eur=29_900)
_identity_330i = VehicleIdentity.from_check_context(_baureihe_330i, _motor_330i, _req_330i)
_identity_330i.apply_web_evidence(_web_konflikt)
check("H1 DB-Motor bleibt '330i', Web-Fantasiemotor wird NICHT übernommen",
      _identity_330i.engine_name == "330i")
check("H2 Kraftstoff bleibt 'Benzin', Web-Diesel-Behauptung verworfen",
      (_identity_330i.fuel or "").strip().lower() == "benzin")
check("H3 Leistung bleibt 258 PS, Web-999-PS-Behauptung verworfen",
      _identity_330i.horsepower == 258)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== I) Datenbasis nennt 'Webrecherche' nur bei tatsächlicher Verwendung ===")

_sources_ohne = datenbasis(None, [], [{"url": "https://example.com/x"}],
                           identity=_g["identity"], markt_verfuegbar=False)
check("I1 Web-Treffer OHNE Marktverfügbarkeit und ohne Web-Identität -> kein 'Webrecherche'",
      "Webrecherche" not in _sources_ohne)

_sources_markt = datenbasis(None, [], [{"url": "https://example.com/x"}],
                            identity=_g["identity"], markt_verfuegbar=True)
check("I2 Web-Treffer MIT tatsächlich verfügbarem Marktpreis -> 'Webrecherche' erscheint",
      "Webrecherche" in _sources_markt)

_sources_identity = datenbasis(None, [], [], identity=_f["identity"], markt_verfuegbar=False)
check("I3 belegte Web-Identität ohne Markttreffer -> 'Webrecherche' erscheint trotzdem",
      "Webrecherche" in _sources_identity)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== J) Fehlende-Angabe-Hinweis ist dynamisch ===")

check("J1 alle drei Angaben vorhanden, kein DB-Treffer -> nennt NICHT 'Marke, Modell, Erstzulassung'",
      "Marke" not in _fehlende_angabe_none("Mazda", "MX-5", 2019))
check("J2 ... sondern einen ehrlichen Hinweis auf die fehlende Verifizierung",
      "nicht sicher verifiziert" in _fehlende_angabe_none("Mazda", "MX-5", 2019))
check("J3 fehlt wirklich nur die Marke, wird genau das genannt",
      _fehlende_angabe_none(None, "MX-5", 2019) == "Marke")
check("J4 fehlen mehrere Angaben, werden sie aufgezählt",
      _fehlende_angabe_none(None, None, 2019) == "Marke und Modell")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== K) Identitäts-Floor: weder DB noch Web bestätigt ===")

check("K1 MX-5 ohne Web-Beleg: DB-Match nicht belastbar", not _g["info"]["belastbar"])
_web_g_belegt = bool(_g["web"] and _g["web"].identitaet and _g["web"].identitaet.belegt)
check("K2 ... und Web ebenfalls nicht belegt -> Kriterium für Identitäts-Floor erfüllt",
      not _web_g_belegt)
check("K3 MX-5 MIT Web-Beleg (Szenario F): Floor-Kriterium NICHT erfüllt",
      _f["info"]["belastbar"] or bool(_f["web"] and _f["web"].identitaet and _f["web"].identitaet.belegt))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== L) Faktenextraktion: fremder Treffer wird NICHT dem gesuchten Fahrzeug zugeschrieben ===")
# BEFUND (Real-Web-Smoke-Test Verifikationsrunde): eine echte Tavily-Suche nach
# "Mazda MX-5 typische Probleme Schwachstellen" lieferte u.a. einen fremden
# Artikel ("Ford Mustang V8 als Gebrauchtwagen: typische Schwachstellen"), der
# denselben Domain-Score und dieselben Bauteil-/Problem-Worte trifft ("Motor",
# "verkokte Ansaugklappen") wie ein echter MX-5-Treffer — und wurde dadurch
# faelschlich als MX-5-Schwachstelle "Motor" extrahiert. Fixture reproduziert
# genau diesen Fall deterministisch.

_fremdtreffer_mustang = treffer(
    "https://www.auto-motor-und-sport.de/sportwagen/gebrauchtwagen/ford-mustang-v8",
    "Ford Mustang V8 als Gebrauchtwagen: typische Schwachstellen und Tipps zum Kauf",
    "Verkokte Ansaugklappen: Ein haeufiges Problem bei den V8-Motoren sind "
    "verkokte und klemmende Ansaugklappen. Ein bekanntes Motor-Problem.",
)
_echttreffer_mx5 = treffer(
    "https://www.autobild.de/artikel/mazda-mx-5-nd-gebrauchtwagen-test",
    "Mazda MX-5 Gebrauchtwagen-Test",
    "Der Mazda MX-5 gilt als zuverlaessig, vereinzelt wird ein Kupplungsproblem "
    "als Schwachstelle genannt.",
)
_fakten_gemischt = _extrahiere_fakten(
    [_fremdtreffer_mustang, _echttreffer_mx5], "schwachstelle", marke="Mazda", modell="MX-5")
check("L1 kein Fakt aus dem fremden Mustang-Treffer (Modell-Token fehlt dort)",
      not any("mustang" in (q.url or "").lower()
              for f in _fakten_gemischt for q in f.quellen))
check("L2 der echte MX-5-Treffer liefert weiterhin einen Fakt (Regel filtert nicht zu aggressiv)",
      any("kupplung" in (f.bauteil or "") for f in _fakten_gemischt)
      or any("autobild" in (q.url or "") for f in _fakten_gemischt for q in f.quellen))
_fakten_nur_fremd = _extrahiere_fakten(
    [_fremdtreffer_mustang], "schwachstelle", marke="Mazda", modell="MX-5")
check("L3 ausschliesslich fremder Treffer -> gar kein Fakt (statt Fehlzuschreibung)",
      _fakten_nur_fremd == [])


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE WEB-FALLBACK-ROOT-CAUSE-TESTS GRUEN")
