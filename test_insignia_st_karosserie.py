"""
"ST" als Opel-Insignia-Karosseriesignal — deterministisch, KEIN Netzwerk.

Live-Fund (Offline-Replay des Insignia-Mitschnitts, 2026-08-19): Listing
3488368020 "Opel Insignia 2.0 CDTI Business Edition ST Navi/AHK/" blieb
body=unknown und wurde dadurch NICHT gegen die Ziel-Karosserie (Grand
Sport/Limousine) abgewertet — es war der einzige "gute" Datenpunkt des gesamten
Insignia-Live-Retests.

"ST" IST bei Opel Insignia die Kurzform von "Sports Tourer" (Kombi). Bei anderen
Marken ist "ST" eine Ausstattungs-/Leistungsbezeichnung (Ford Focus ST, Fiesta
ST, ST-Line) und KEIN Karosseriesignal. Deshalb kein globaler Wortlisteneintrag
(app/marktvergleich._insignia_st_kombi), sondern eine eng kontextgebundene Regel:
"ST" zaehlt nur als Kombi-Hinweis, wenn DERSELBE Text "Insignia" nennt.

    python test_insignia_st_karosserie.py
"""
import os
import sys
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_st_"), "test.db")
sys.path.insert(0, ".")

# §Source-Policy: Der Production-Default gibt KEINE Marktquelle zum Preisbilden
# frei (app/config.ALLOWED_MARKET_SOURCES ist leer). Dieser Test prueft die
# ANALYSE-ENGINE und braucht dafuer die historischen/synthetischen Testdomains —
# die Freigabe gilt ausschliesslich in diesem Testprozess und ist KEINE
# produktive Qualifikation der Quelle. Siehe _source_policy_testharness.py.
import _source_policy_testharness  # noqa: E402,F401

from types import SimpleNamespace                                        # noqa: E402

from app.marktvergleich import (                                         # noqa: E402
    _bewerte, _eindeutige_karosserie, _extrahiere_aus_text,
    _insignia_st_kombi, _karosserie_im_text, baue_ziel,
)

_fails: list[str] = []


def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        _fails.append(name)


# ══ A-D) Positive: Opel Insignia "ST" wird kombi ═════════════════════════════
check("A: 'Opel Insignia ST 2.0 CDTI' -> kombi",
      _karosserie_im_text("Opel Insignia ST 2.0 CDTI") == "kombi")
check("B: 'Opel Insignia B ST' -> kombi",
      _karosserie_im_text("Opel Insignia B ST") == "kombi")
check("B2: 'Insignia 2.0 CDTI Business Edition ST' (ohne 'Opel') -> kombi",
      _karosserie_im_text("Insignia 2.0 CDTI Business Edition ST") == "kombi")
check("C: 'Opel Insignia Sports Tourer' -> kombi (bestehende Regel)",
      _karosserie_im_text("Opel Insignia Sports Tourer") == "kombi")
check("D: 'Opel Insignia Sportstourer' -> kombi (bestehende Regel)",
      _karosserie_im_text("Opel Insignia Sportstourer") == "kombi")

# ══ E-G) Negative: "ST" bei anderen Marken bleibt unberührt ══════════════════
check("E: 'Ford Focus ST' -> NICHT automatisch kombi",
      _karosserie_im_text("Ford Focus ST") is None)
check("F: 'Ford Fiesta ST' -> NICHT kombi",
      _karosserie_im_text("Ford Fiesta ST") is None)
check("G: 'Ford Focus ST-Line' -> NICHT kombi",
      _karosserie_im_text("Ford Focus ST-Line") is None)
check("G2: 'Ford Focus ST Line' (mit Leerzeichen) -> NICHT kombi",
      _karosserie_im_text("Ford Focus ST Line") is None)
check("G3: 'Seat Leon ST' bleibt unberührt (kein Insignia-Kontext)",
      _karosserie_im_text("Seat Leon ST") is None)

# ══ H) Kein Body-Leak aus einer Nachbaranzeige ═══════════════════════════════
# _insignia_st_kombi bekommt (wie _varianten_zone fuer die Motorprüfung) NUR den
# bereits isolierten Kartentext — hier direkt am Beispiel geprüft: ein Fahrzeug
# ohne eigenes "Insignia" bleibt unberührt, selbst wenn "ST" im selben String
# auftaucht (simuliert ein Fragment, in dem "Insignia" nur beim Nachbarn stünde).
check("H: Text ohne eigenes 'Insignia' bekommt kein Kombi-Signal aus 'ST' allein",
      _karosserie_im_text("BMW 320d ST Sonderausstattung") is None)
check("H2: _insignia_st_kombi direkt: kein 'Insignia' -> False",
      _insignia_st_kombi("Ford Focus ST 2020") is False)

# ══ I) Groß-/Kleinschreibung ══════════════════════════════════════════════════
check("I: Kleinschreibung 'opel insignia st' -> kombi",
      _karosserie_im_text("opel insignia st") == "kombi")
check("I2: Großschreibung 'OPEL INSIGNIA ST' -> kombi",
      _karosserie_im_text("OPEL INSIGNIA ST") == "kombi")

# ══ J) Wortgrenze: "st" mitten im Wort matcht nicht ══════════════════════════
check("J: 'BEST Angebot Opel Insignia' -> KEIN Match durch 'BEST'",
      _karosserie_im_text("Opel Insignia BEST Angebot") is None)
check("J2: 'Liste der Ausstattung' (Insignia im Kontext) -> kein Fehlmatch",
      _karosserie_im_text("Opel Insignia - Liste der Ausstattung") is None)
check("J3: 'Opel Insignia St. Wendel' (Ortsname mit Punkt) -> kein Match",
      _karosserie_im_text("Opel Insignia St. Wendel") is None)
check("J4: 'Opel Insignia Stufenheck' -> eigenständige Karosserie 'limousine', "
      "nicht durch 'st'-Präfix verfälscht",
      _karosserie_im_text("Opel Insignia Stufenheck") == "limousine")


# ══ Realer Regressionsfall: Listing 3488368020 ═══════════════════════════════
def karte(titel, lid, preis, km, ez, beschr):
    bild = ("https://img.kleinanzeigen.de/api/v1/prod-ads/images/6b/"
            "6b50c294-46b9-443d-9c70-864665bca6a6")
    return ("* [![" + titel + " Vorschau](" + bild + ")\n\n"
            "  13](/s-anzeige/x/" + lid + "-216-8144)\n\n  72488 Sigmaringen\n\n"
            "  Gestern, 20:36\n\n"
            "  ## [" + titel + "](/s-anzeige/x/" + lid + "-216-8144)\n\n"
            "  " + beschr + "\n\n  " + preis + " €\n\n  " + km + " km   EZ " + ez + "\n")


URL = "https://www.kleinanzeigen.de/s-autos/opel-insignia/k0c216"
TITEL = "Opel Insignia gebraucht kaufen | kleinanzeigen.de"
NACHBAR = karte("Opel Insignia Business Edition", "3488000001", "13.500",
                "130.000", "01/2021", "Diesel Automatik")
TEXT = (TITEL + "\n\n## Ergebnisse\n\n"
        + karte("Opel Insignia 2.0 CDTI Business Edition ST Navi/AHK/",
                "3488368020", "12.900", "134.800", "10/2021",
                "Innenausstattung- Android Auto - Armlehne - Beheizbares Lenkrad")
        + NACHBAR)
roh = _extrahiere_aus_text(TEXT, URL, "market_category",
                           grenzen=(len(TITEL) + 1, len(TITEL) + 2),
                           seiten_body=_eindeutige_karosserie(URL))
karte_3488368020 = next((b for b in roh if b.preis_eur == 12900), None)

check("Regression: Listing 3488368020 wird strukturell extrahiert",
      karte_3488368020 is not None)
check("Regression: body=kombi (vorher: unknown)",
      karte_3488368020 is not None and karte_3488368020.body == "kombi")

BAUREIHE = {"id": "opel-insignia-b", "marke": "Opel", "modell": "Insignia",
            "generation": "B"}
MOTOR = {"bezeichnung": "2.0 Diesel (174 PS) (Facelift)", "motorcode": "F20DTH",
         "kraftstoff": "Diesel", "leistung_ps": 174}
REQ = SimpleNamespace(marke="Opel", modell="Insignia Grand Sport", baujahr=2020,
                      kilometerstand=115_000, motor="2.0 Diesel 174 PS",
                      kraftstoff="Diesel", getriebe="Automatik", preis_eur=17_900)
ZIEL = baue_ziel(BAUREIHE, MOTOR, REQ, [BAUREIHE], [])
bewertet = _bewerte(karte_3488368020, ZIEL) if karte_3488368020 else None
check("Regression: gegen Ziel 'Grand Sport' wird die Karosserie regulär "
      "abgewertet (andere Karosserie: kombi statt limousine)",
      bewertet is not None
      and any("andere Karosserie" in g for g in bewertet.gruende)
      and "kombi" in [g for g in bewertet.gruende if "andere Karosserie" in g][0])
check("Regression: der Nachbar (ohne eigenes 'ST') bleibt unberührt",
      any(b.preis_eur == 13500 and b.body != "kombi" for b in roh))

print()
if _fails:
    print(f"{len(_fails)} FEHLER: " + "; ".join(_fails))
    sys.exit(1)
print("Alle Pruefungen bestanden.")
