"""
Inserat-Edge-Cases: Teilerubrik und Leistungsabweichung — deterministisch, KEIN Netzwerk.

Zwei real belegte Lücken aus dem Edge-Case-Audit der gespeicherten Läufe:

  1. TEILE/ZUBEHÖR. Kleinanzeigen kodiert die Rubrik im letzten Pfadsegment der
     Detailseite: /s-anzeige/<slug>/<id>-<rubrik>-<n>. Über alle gespeicherten
     Karten: 159x "-216-" (Autos), 8x "-223-" (Auto-Teile & Reifen). Bisher fielen
     Teileanzeigen nur zufällig durch — über ein Fremdmodell-Token aus einem
     Shop-Link ("pro"), einen Händlergruß ("liebe Mercedes-Fahrer") oder eine
     fremde Generation im Produkttitel. Ein "BMW 320d G20 Motor B47D20 komplett"
     für 2.500 € mit 118.000 km wurde als sehr_aehnlich eingestuft.

  2. LEISTUNG. Die PS-Prüfung lief nur, solange die Motorbezeichnung NICHT
     bestätigt war. Sobald "320d" im Text stand, galt die Leistung als geklärt —
     ein "Stage 1 (225 PS)" wurde als serienmäßiger 190-PS-320d gewertet. Der
     Code extrahiert die 225 PS korrekt und verwarf sie ungenutzt (reales
     Listing 3460474635).

Bewusst NICHT gebaut (Audit: kein realer Fall): Mehrpreis-Logik (0 von 316 Karten
mit mehr als einem Euro-Wert), Mehrfach-km-Logik (0 Konflikte in 159 sauber
segmentierten Karten) und jede Tuning-SCHLÜSSELWORT-Regel.

    python test_inserat_edge_cases.py
"""
import os
import sys
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_edge_"), "test.db")
sys.path.insert(0, ".")

# §Source-Policy: Der Production-Default gibt KEINE Marktquelle zum Preisbilden
# frei (app/config.ALLOWED_MARKET_SOURCES ist leer). Dieser Test prueft die
# ANALYSE-ENGINE und braucht dafuer die historischen/synthetischen Testdomains —
# die Freigabe gilt ausschliesslich in diesem Testprozess und ist KEINE
# produktive Qualifikation der Quelle. Siehe _source_policy_testharness.py.
import _source_policy_testharness  # noqa: E402,F401

from types import SimpleNamespace                                        # noqa: E402

from app.marktvergleich import (                                         # noqa: E402
    _bewerte, _eindeutige_karosserie, _extrahiere_aus_text, analysiere_markt,
    baue_ziel, kleinanzeigen_kategorie, nicht_fahrzeug_rubrik,
)

_fails: list[str] = []


def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        _fails.append(name)


# ── Zielprofil: BMW 320d G20, 190 PS ───────────────────────────────────────
BAUREIHE = {"id": "bmw-3er-g20-g21", "marke": "BMW", "modell": "3er",
            "generation": "G20/G21", "karosserie": '["Limousine", "Touring"]',
            "chassis_codes": {"G20": "Limousine", "G21": "Touring"}}
ALLE_B = [BAUREIHE, {"id": "bmw-3er-f30", "marke": "BMW", "modell": "3er",
                     "generation": "F30", "karosserie": '["Limousine"]'}]
ALLE_M = [{"baureihe_id": "bmw-3er-g20-g21", "bezeichnung": "320d", "motorcode": "B47D20"},
          {"baureihe_id": "bmw-3er-g20-g21", "bezeichnung": "330i"}]
MOTOR = {"bezeichnung": "320d", "kraftstoff": "Diesel", "leistung_ps": 190,
         "motorcode": "B47D20"}
# Die Zielleistung (190 PS) stammt hier aus der Motorvariante der DB. Nach der
# Vertrauensregel (app/verification.py) traegt eine UNGEPRUEFTE DB-Angabe keine harte
# Ablehnung — deshalb nennt der Nutzer die Leistung hier ausdruecklich mit. Genau so
# kommt sie auch aus dem echten Formular ("320d 190 PS"). Der Fall ohne Nutzerangabe
# wird unten getrennt geprueft.
REQ = SimpleNamespace(marke="BMW", modell="3er G20", motor="320d 190 PS",
                      kraftstoff="Diesel", baujahr=2019, kilometerstand=120_000)
ZIEL = baue_ziel(BAUREIHE, MOTOR, REQ, ALLE_B, ALLE_M)
check("Vorbedingung: Zielleistung ist 190 PS", ZIEL["leistung_ps"] == 190)

URL = "https://www.kleinanzeigen.de/s-autos/bmw-320d-g20/k0c216+autos.typ_s:limousine"
TITEL = "BMW 320d G20 gebraucht kaufen"


def karte(titel, slug, lid, rubrik, preis, km, ez, beschreibung):
    """Reale Kleinanzeigen-Kartenstruktur mit Rubrik im Detailpfad."""
    return ("* [![" + titel + " Berlin Vorschau](https://img.kleinanzeigen.de/x/"
            + lid + ".jpg)\n\n"
            "  20](/s-anzeige/" + slug + "/" + lid + "-" + rubrik + "-1111)\n\n"
            "  12307 Berlin\n\n  Heute, 10:59\n\n"
            "  ## [" + titel + "](/s-anzeige/" + slug + "/" + lid + "-" + rubrik + "-1111)\n\n"
            "  " + beschreibung + "\n\n  " + preis + " €\n\n  " + km + " km   EZ " + ez + "\n")


def bewerte_seite(raw):
    roh = _extrahiere_aus_text(TITEL + "\n\n" + raw, URL, "market_category",
                               grenzen=(len(TITEL) + 1, len(TITEL) + 2),
                               seiten_body=_eindeutige_karosserie(URL))
    return {b.preis_eur: _bewerte(b, ZIEL) for b in roh}


# ══ 1 — Kategorie-Parsing ══════════════════════════════════════════════════
check("Parsing: absolute Fahrzeug-URL -> 216",
      kleinanzeigen_kategorie(
          "https://www.kleinanzeigen.de/s-anzeige/bmw-320d/3484786731-216-8139") == "216")
check("Parsing: absolute Teile-URL -> 223",
      kleinanzeigen_kategorie(
          "https://www.kleinanzeigen.de/s-anzeige/racechip-gts/3250280937-223-9454") == "223")
check("Parsing: wurzel-relative Fahrzeug-URL -> 216",
      kleinanzeigen_kategorie("/s-anzeige/bmw-320d-g20/3400000001-216-1111") == "216")
check("Parsing: wurzel-relative Teile-URL -> 223",
      kleinanzeigen_kategorie("/s-anzeige/bmw-320d-g20-motor/3400000005-223-1111") == "223")
check("Parsing: alte Domain ebay-kleinanzeigen.de wird mitgelesen",
      kleinanzeigen_kategorie(
          "https://www.ebay-kleinanzeigen.de/s-anzeige/felgen/3479024498-223-9454") == "223")
# Keine zufaellige Zahl irgendwo in einer URL:
check("Parsing: FREMDE Domain mit gleicher Zahlenform -> None",
      kleinanzeigen_kategorie(
          "https://www.autoscout24.de/angebote/bmw-320d-3484786731-223-8139") is None)
check("Parsing: fremde Domain mit 223 und 216 im Pfad -> None",
      kleinanzeigen_kategorie("https://www.mobile.de/x/223/y-216-9") is None)
check("Parsing: Kleinanzeigen-SUCHSEITE ist keine Detailseite -> None",
      kleinanzeigen_kategorie(
          "https://www.kleinanzeigen.de/s-autos/bmw/k0c216+autos.typ_s:limousine") is None)
check("Parsing: relativer Pfad ohne /s-anzeige/ -> None",
      kleinanzeigen_kategorie("/irgendwas/3400000001-223-1111") is None)
check("Parsing: leer/None -> None",
      kleinanzeigen_kategorie("") is None and kleinanzeigen_kategorie(None) is None)
check("Rubrik: 223 ist eine Nicht-Fahrzeugrubrik",
      nicht_fahrzeug_rubrik("/s-anzeige/x/3400000005-223-1111") == "Auto-Teile & Reifen")
check("Rubrik: 216 ist KEINE Nicht-Fahrzeugrubrik",
      nicht_fahrzeug_rubrik("/s-anzeige/x/3400000001-216-1111") is None)
# Denylist, keine Allowlist: unbekannte Rubriken bleiben zugelassen, weil
# Kleinanzeigen weitere FAHRZEUG-Rubriken fuehrt (Motorrad, Wohnmobil, Nutzfahrzeug).
check("Rubrik: unbekannte Rubrik wird NICHT vorschnell verworfen",
      nicht_fahrzeug_rubrik("/s-anzeige/x/3400000001-217-1111") is None)

# ══ 2 — Teilerubrik ist kein Vergleichsfahrzeug ════════════════════════════
SEITE_TEILE = (
    "## Ergebnisse\n\n"
    + karte("RaceChip GTS Black für BMW 320d G20 190 PS", "racechip-gts-black-320d-g20",
            "3400000004", "223", "1.500", "118.000", "05/2019",
            "Leistungssteigerung für Ihren BMW, Versand möglich")
    + karte("BMW 320d G20 Motor B47D20 komplett", "bmw-320d-g20-motor-b47d20",
            "3400000005", "223", "2.500", "118.000", "05/2019",
            "Ausgebauter Motor, gelaufene Strecke wie angegeben")
    + karte("BMW G20 Original M Felgen 19 Zoll", "bmw-g20-original-m-felgen-19-zoll",
            "3400000006", "223", "1.699", "118.000", "05/2019",
            "Herzlich Willkommen liebe BMW-Fahrer, Aktionspreis, Versand möglich")
    + karte("BMW 320d G20 Sport Line", "bmw-320d-g20-sport-line",
            "3400000001", "216", "24.900", "118.000", "05/2019",
            "Scheckheftgepflegt, zwei Vorbesitzer"))
P = bewerte_seite(SEITE_TEILE)
for preis, was in ((1500, "RaceChip"), (2500, "ausgebauter Motor"), (1699, "Felgensatz")):
    b = P.get(preis)
    check("Teile: %s (%d €) wird verworfen" % (was, preis),
          b is not None and b.vergleichbarkeit == "ungeeignet")
    check("Teile: %s nennt die Rubrik als Grund" % was,
          b is not None and "Nicht-Fahrzeugkategorie" in b.acceptance_reason)
check("Teile: der ausgebaute Motor ist NICHT mehr sehr_aehnlich (Audit-Regression)",
      P[2500].vergleichbarkeit != "sehr_aehnlich")
check("Gegenprobe: das echte -216-Fahrzeug bleibt zulaessig",
      P[24900].vergleichbarkeit == "sehr_aehnlich")
check("Gegenprobe: es wird NICHT wegen der Rubrik abgelehnt",
      "Nicht-Fahrzeugkategorie" not in P[24900].acceptance_reason)
ma = analysiere_markt([{"url": URL, "title": TITEL, "content": "",
                        "raw_content": SEITE_TEILE}], ZIEL, None)
check("Teile: keine Teileanzeige taucht in den Beobachtungen auf",
      all(b.preis_eur not in (1500, 2500, 1699) for b in (ma.beobachtungen or [])))

# ══ 3 — Leistungsprüfung ═══════════════════════════════════════════════════
SEITE_PS = (
    "## Ergebnisse\n\n"
    + karte("BMW 320d G20 Sport Line", "bmw-320d-g20-sport-line", "3400000101", "216",
            "24.900", "118.000", "05/2019", "Serienzustand, 190 PS, Scheckheft")
    + karte("BMW 320d G20 Stage 1", "bmw-320d-g20-stage-1", "3400000102", "216",
            "25.400", "119.000", "05/2019", "Stage 1 (225 PS) Chiptuning, Scheckheft")
    + karte("BMW 320d G20 Stage 1 Umbau", "bmw-320d-g20-stage-1-umbau", "3400000103", "216",
            "25.900", "117.000", "05/2019", "Stage 1 Chiptuning verbaut, Scheckheft")
    + karte("BMW 330i G20 M Sport", "bmw-330i-g20-m-sport", "3400000104", "216",
            "31.900", "116.000", "05/2019", "330i mit 258 PS, Scheckheft"))
Q = bewerte_seite(SEITE_PS)
check("A: 190 PS -> normale Behandlung",
      Q[24900].horsepower == 190 and Q[24900].vergleichbarkeit == "sehr_aehnlich")
check("B: 225 PS -> Leistungsabweichung greift, obwohl 320d bestaetigt ist",
      Q[25400].horsepower == 225 and Q[25400].vergleichbarkeit == "ungeeignet"
      and "abweichende Motorleistung" in Q[25400].acceptance_reason)
check("B: die bestaetigte Verkaufsbezeichnung schaltet die Pruefung NICHT mehr ab",
      "320d" in (Q[25400].engine_variant or "320d"))
check("C: Stage 1 OHNE PS-Angabe erzeugt keine erfundene Leistungszahl",
      Q[25900].horsepower is None)
check("C: Stage 1 ohne PS-Angabe wird nicht wegen Leistung verworfen",
      "abweichende Motorleistung" not in Q[25900].acceptance_reason)
check("C: Verhalten ohne PS-Angabe bleibt unveraendert (weiterhin verwertbar)",
      Q[25900].vergleichbarkeit == "sehr_aehnlich")
check("D: falsche Motorbezeichnung bleibt hart ausgeschlossen",
      Q[31900].vergleichbarkeit == "ungeeignet"
      and "Motorvariante" in Q[31900].acceptance_reason)
# Toleranzgrenze: max(15% von 190, 25) = 28,5 PS
SEITE_GRENZE = (
    "## Ergebnisse\n\n"
    + karte("BMW 320d G20 LCI", "bmw-320d-g20-lci", "3400000105", "216",
            "24.100", "118.500", "05/2019", "Serie mit 215 PS laut Schein")
    + karte("BMW 320d G20 Basis", "bmw-320d-g20-basis", "3400000106", "216",
            "24.200", "118.400", "05/2019", "Serie mit 150 PS laut Schein"))
R = bewerte_seite(SEITE_GRENZE)
# Toleranz = max(15 % von 190, 25) = 28,5 PS. 215 liegt mit 25 PS Abstand knapp
# darunter, 150 mit 40 PS deutlich darueber.
check("Toleranz: 215 PS (25 PS Abstand) bleibt zulaessig",
      R[24100].horsepower == 215 and R[24100].vergleichbarkeit != "ungeeignet")
check("Toleranz: 150 PS (40 PS Abstand) wird verworfen",
      R[24200].horsepower == 150
      and "abweichende Motorleistung" in R[24200].acceptance_reason)

# ══ 4 — bewusst NICHT gebaut ═══════════════════════════════════════════════
SEITE_VB = ("## Ergebnisse\n\n"
            + karte("BMW 320d G20 Advantage", "bmw-320d-g20-advantage", "3400000201",
                    "216", "24.900", "118.000", "05/2019", "Preis VB, Scheckheft"))
V = bewerte_seite(SEITE_VB)
check("Kein Mehrpreis-Umbau: VB bleibt ein Zusatz zum selben Preis",
      list(V.keys()) == [24900])

# ── §DB-Trust: ohne Nutzer-Leistungsangabe traegt die ungepruefte DB keine harte
# Ablehnung mehr. Die Abweichung bleibt sichtbar, senkt aber nur die Stufe.
REQ_OHNE_PS = SimpleNamespace(marke="BMW", modell="3er G20", motor="320d",
                              kraftstoff="Diesel", baujahr=2019, kilometerstand=120_000)
ZIEL_OHNE_PS = baue_ziel(BAUREIHE, MOTOR, REQ_OHNE_PS, ALLE_B, ALLE_M)
roh_ps = _extrahiere_aus_text(TITEL + chr(10) + chr(10) + SEITE_PS, URL,
                              "market_category",
                              grenzen=(len(TITEL) + 1, len(TITEL) + 2),
                              seiten_body=_eindeutige_karosserie(URL))
Q2 = {b.preis_eur: _bewerte(b, ZIEL_OHNE_PS) for b in roh_ps}
check("Trust: ohne Nutzerangabe ist die Zielleistung nicht hart",
      ZIEL_OHNE_PS["leistung_hart"] is False)
check("Trust: 225 PS wird dann abgewertet statt hart verworfen",
      Q2[25400].vergleichbarkeit != "sehr_aehnlich"
      and "abweichende Motorleistung" in Q2[25400].acceptance_reason
      and "ungeprüfte Zielangabe" in Q2[25400].acceptance_reason)

print()
if _fails:
    print(str(len(_fails)) + " FEHLGESCHLAGEN:")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("Alle Inserat-Edge-Case-Tests bestanden.")
