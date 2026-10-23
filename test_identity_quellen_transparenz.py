"""
PASS-GATE-Folgeauftrag §F: konkrete Quellen pro Identitätsfeld statt nur
einer Zahl ("2 Webquelle(n)").

Befund: `konsens()`/`uebernehme()` (app/technical_research.py) verwarfen die
tatsächlichen Domainnamen bisher und behielten nur `len(domains)`. Die
Produktions-UI konnte deshalb nie zeigen, WELCHE Quelle(n) einen
Identitätsclaim (Motor/PS/Antrieb/Getriebe/Hybrid) tatsächlich stützen.

Jetzt: `konsens()` liefert zusätzlich `domain_namen` (ein Set registrierbarer
Domains, NIE volle URLs), `uebernehme()`/die Motor-Bestätigung reichen es in
`WebVehicleIdentity.feldwerte` durch, `VehicleIdentity.apply_web_evidence()`
reicht es weiter in `field_evidence[feld]["domain_namen"]`, und
`kaufcheck_bericht.bericht()` rendert pro Feld eine zusätzliche "Quellen:
..."-Zeile, NUR wenn tatsächlich Domainnamen vorliegen -- nie erfunden.

GENERISCH getestet, kein Fahrzeug-/Marken-Hardcode. KEIN Netzwerk.

    python test_identity_quellen_transparenz.py
"""
from app.models import TechnischeRecherche
from app.technical_research import werte_identitaet_aus
from app.vehicle_identity import VehicleIdentity

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url: str, titel: str, inhalt: str) -> dict:
    return {"url": url, "title": titel, "content": inhalt}


ZIEL = dict(marke="Testmarke", modell="Modellz", baujahr=2017, motor=None, leistung_ps=None,
           auxiliary_code=None, hubraum=None, generation=None, getriebe=None)


def identity_aus_web(treffer_liste: list[dict], ziel: dict) -> VehicleIdentity:
    web_identitaet, _ = werte_identitaet_aus(treffer_liste, ziel)
    identity = VehicleIdentity(make=ziel.get("marke"), model=ziel.get("modell"))
    identity.apply_web_evidence(TechnischeRecherche(identitaet=web_identitaet))
    return identity, web_identitaet


print("=== 1) Web-bestätigtes Feld ohne vorherigen Nutzerwert -> konkrete "
      "Domains im field_evidence ===")
t1 = treffer("https://www.adac.de/testmarke-modellz", "Testmarke Modellz Heckantrieb",
            "Die Testmarke Modellz hat Heckantrieb.")
t2 = treffer("https://www.auto-motor-und-sport.de/testmarke-modellz", "Testmarke Modellz Daten",
            "Der Testmarke Modellz hat serienmäßig Heckantrieb.")
identity1, web1 = identity_aus_web([t1, t2], ZIEL)
check("1a WebVehicleIdentity.feldwerte traegt domain_namen",
      web1.feldwerte.get("drivetrain", {}).get("domain_namen") == {"adac.de", "auto-motor-und-sport.de"},
      str(web1.feldwerte.get("drivetrain")))
check("1b field_evidence reicht dieselben Domains weiter (sortiert, als Liste)",
      identity1.field_evidence.get("drivetrain", {}).get("domain_namen")
      == ["adac.de", "auto-motor-und-sport.de"],
      str(identity1.field_evidence.get("drivetrain")))

print("\n=== 2) Bereits vorhandener Nutzerwert, web-bestätigt -> Domains auch im "
      "'confirmed_by'-Zweig ===")
identity2 = VehicleIdentity(make="Testmarke", model="Modellz", drivetrain="Heck")
identity2.field_evidence["drivetrain"] = {"value": "Heck", "primary_source": "user",
                                          "confirmed_by": [], "verification_state": "user_only",
                                          "confidence": "mittel", "raw_user_value": "Heck"}
web_identitaet2, _ = werte_identitaet_aus([t1, t2], ZIEL)
identity2.apply_web_evidence(TechnischeRecherche(identitaet=web_identitaet2))
check("2a verification_state wechselt zu user_confirmed",
      identity2.field_evidence["drivetrain"]["verification_state"] == "user_confirmed")
check("2b Domains sind auch hier nachvollziehbar (nicht nur beim web_only-Zweig)",
      identity2.field_evidence["drivetrain"].get("domain_namen")
      == ["adac.de", "auto-motor-und-sport.de"],
      str(identity2.field_evidence["drivetrain"]))

print("\n=== 3) Kein Beleg -> keine erfundene Domain ===")
identity3 = VehicleIdentity(make="Testmarke", model="Modellz")
check("3a ohne Web-Recherche keine domain_namen (nichts erfunden)",
      "domain_namen" not in (identity3.field_evidence.get("drivetrain") or {}))

print("\n=== 4) Motor-Confirm-Pfad (eigener Code-Zweig, nicht konsens()) traegt "
      "ebenfalls Domains ===")
ziel_motor = dict(marke="Testmarke", modell="Modellz", baujahr=2017, motor="5.0 V8",
                  leistung_ps=477, auxiliary_code=None, hubraum=None, generation=None, getriebe=None)
t3 = treffer("https://www.testmarke.de/modellz-daten", "Testmarke Modellz Technische Daten",
            "Die Testmarke Modellz wird von einem 5.0-liter V8 angetrieben.")
t4 = treffer("https://www.auto-motor-und-sport.de/testmarke-modellz", "Testmarke Modellz im Test",
            "Der Testmarke Modellz hat einen 5.0L V8 unter der Haube.")
_, web_motor = identity_aus_web([t3, t4], ziel_motor)
check("4a engine_name traegt domain_namen aus dem eigenen Motor-Bestaetigungs-Pfad",
      bool(web_motor.feldwerte.get("engine_name", {}).get("domain_namen")),
      str(web_motor.feldwerte.get("engine_name")))

print("\n=== 5) Nur V8 ohne expliziten Kraftstoffbeleg -> Kraftstoff bleibt UNKNOWN "
      "(§C PASS-GATE-Folgeauftrag: V8 NIE automatisch mit Benzin gleichsetzen) ===")
t5 = treffer("https://www.testmarke.de/modellz-daten", "Testmarke Modellz Technische Daten",
            "Die Testmarke Modellz wird von einem 5.0-liter V8 mit 477 PS angetrieben.")
t6 = treffer("https://www.auto-motor-und-sport.de/testmarke-modellz", "Testmarke Modellz im Test",
            "Der Testmarke Modellz hat einen 5.0L V8 mit 477 PS unter der Haube.")
_, web_ohne_kraftstoffwort = identity_aus_web([t5, t6], dict(ZIEL, motor="5.0 V8", leistung_ps=477))
check("5a kein 'fuel'-Claim ohne explizites Kraftstoffwort (kein Raten aus V8/Hubraum)",
      "fuel" not in web_ohne_kraftstoffwort.feldwerte,
      str(web_ohne_kraftstoffwort.feldwerte))
check("5b engine_name/horsepower werden TROTZDEM korrekt bestaetigt (zeigt: das ist "
      "kein allgemeiner Extraktionsausfall, nur Kraftstoff bleibt bewusst offen)",
      web_ohne_kraftstoffwort.motor == "5.0 V8" and web_ohne_kraftstoffwort.leistung_ps == 477)

print()
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE IDENTITY-QUELLEN-TRANSPARENZ-TESTS GRUEN")
