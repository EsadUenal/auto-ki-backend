"""
Modellanker fuer kurze Modellcodes (Audi A3/A4/Q5 …) — deterministisch, KEIN Netzwerk.

P1-Befund der Etappe-1-Fahrzeugmatrix (2026-08-19): Ein Audi-A3-Ziel verwarf ALLE
drei korrekten A3-Karten mit "Zielmodell auf der Karte nicht belegt (rs3)", ein
A4-Ziel entsprechend mit "(rs4)".

Ursachenkette:
  1. `_modell_tokens("A3")` liefert set() — die Laengenschwelle (>= 3 Zeichen)
     verwirft den zweistelligen Modellcode.
  2. `_modell_anker` hatte damit keinen direkten Nutzeranker.
  3. Das Supplement in `baue_ziel` ergaenzte daraufhin die STRUKTURIERTEN Token
     aus `modell_tokens` — das sind bei Audi die Motorvarianten-Namen der
     Baureihe. Uebrig blieb ausgerechnet der Performance-Variantenname 'rs3'
     bzw. 'rs4', der damit die Rolle des Zielmodells uebernahm.

Zwei Aenderungen beheben das:
  - `_modell_anker` erkennt kurze Modellcodes (Buchstabenpraefix + Ziffern als
    VOLLSTAENDIGER Token). Eine Ziffer ist zwingend — rein alphabetische Kuerzel
    wie "GT", "RS" oder "ST" sind Ausstattungszusaetze und nie ein Modellanker.
  - Das Supplement laeuft nur noch, wenn KEIN direkter Nutzeranker existiert.
    Ein Variantenname darf den ausdruecklich genannten Basismodellnamen weder
    ersetzen noch erweitern.

    python test_modellanker_kurzcode.py
"""
import os
import sys
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_anker_"), "test.db")
sys.path.insert(0, ".")

# §Source-Policy: Der Production-Default gibt KEINE Marktquelle zum Preisbilden
# frei (app/config.ALLOWED_MARKET_SOURCES ist leer). Dieser Test prueft die
# ANALYSE-ENGINE und braucht dafuer die historischen/synthetischen Testdomains —
# die Freigabe gilt ausschliesslich in diesem Testprozess und ist KEINE
# produktive Qualifikation der Quelle. Siehe _source_policy_testharness.py.
import _source_policy_testharness  # noqa: E402,F401

from types import SimpleNamespace                                        # noqa: E402

from app.marktvergleich import (                                         # noqa: E402
    _bewerte, _eindeutige_karosserie, _extrahiere_aus_text, _modell_anker,
    baue_ziel,
)

_fails: list[str] = []


def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        _fails.append(name)


def anker(marke, modell):
    return sorted(_modell_anker(marke, modell))


# ══ A-D) Kurze Modellcodes werden direkter Anker ═════════════════════════════
check("A: Audi A3 -> Anker 'a3'", anker("Audi", "A3") == ["a3"])
check("A2: Audi 'A3 8V' -> 'a3' (Modell vor Generationscode)",
      anker("Audi", "A3 8V") == ["a3"])
check("A3: Audi 'A3 Sportback' -> 'a3' (nicht der Karosseriezusatz)",
      anker("Audi", "A3 Sportback") == ["a3"])
check("B: Audi A4 -> 'a4'", anker("Audi", "A4") == ["a4"])
check("B2: Audi 'A4 B9' -> 'a4' (nicht 'b9')", anker("Audi", "A4 B9") == ["a4"])
check("C: Audi A6 -> 'a6'", anker("Audi", "A6") == ["a6"])
check("C2: Audi A8 -> 'a8'", anker("Audi", "A8") == ["a8"])
check("D: Audi Q3 -> 'q3'", anker("Audi", "Q3") == ["q3"])
for q in ("Q2", "Q5", "Q7", "Q8"):
    check(f"D: Audi {q} -> '{q.lower()}'", anker("Audi", q) == [q.lower()])

# ══ E-F) Performance-Varianten bleiben eigenstaendig ═════════════════════════
check("E: Audi RS3 bleibt 'rs3' (kein Rueckstufen auf a3)",
      anker("Audi", "RS3") == ["rs3"])
check("E2: Audi RS4 bleibt 'rs4'", anker("Audi", "RS4") == ["rs4"])
check("F: Audi S3 bleibt 's3'", anker("Audi", "S3") == ["s3"])
check("F2: Audi S4 bleibt 's4'", anker("Audi", "S4") == ["s4"])

# ══ G-H) Kein Variantenname als Anker bei explizitem Basismodell ═════════════
A3_BR = {"id": "audi-a3-typ-8v", "marke": "Audi", "modell": "A3", "generation": "Typ 8V"}
A4_BR = {"id": "audi-a4-b9", "marke": "Audi", "modell": "A4", "generation": "B9"}
A3_MOTOREN = [
    {"baureihe_id": "audi-a3-typ-8v", "bezeichnung": "2.0 TDI", "motorcode": "CRBC"},
    {"baureihe_id": "audi-a3-typ-8v", "bezeichnung": "RS3", "motorcode": "CZGB"},
    {"baureihe_id": "audi-a3-typ-8v", "bezeichnung": "S3", "motorcode": "CJXC"},
]
A4_MOTOREN = [
    {"baureihe_id": "audi-a4-b9", "bezeichnung": "2.0 TDI (40 TDI)", "motorcode": "DETA"},
    {"baureihe_id": "audi-a4-b9", "bezeichnung": "RS4 (2.9 TFSI)", "motorcode": "DECA"},
]


def audi_ziel(br, motoren, modell, motor="2.0 TDI", kraftstoff="Diesel"):
    req = SimpleNamespace(marke="Audi", modell=modell, baujahr=2017,
                          kilometerstand=88_000, motor=motor, kraftstoff=kraftstoff,
                          getriebe="Automatik", preis_eur=15_900)
    return baue_ziel(br, {"bezeichnung": motor, "kraftstoff": kraftstoff,
                          "leistung_ps": 150}, req, [br], motoren)


_z_a3 = audi_ziel(A3_BR, A3_MOTOREN, "A3 8V")
check("G: A3-Ziel hat Anker 'a3' — NICHT 'rs3'",
      _z_a3["modell_anker_user"] == {"a3"})
check("G2: 'rs3' taucht im A3-Anker gar nicht auf",
      "rs3" not in (_z_a3["modell_anker_user"] or set()))
_z_a4 = audi_ziel(A4_BR, A4_MOTOREN, "A4 B9")
check("H: A4-Ziel hat Anker 'a4' — NICHT 'rs4'",
      _z_a4["modell_anker_user"] == {"a4"})
check("H2: 'rs4' taucht im A4-Anker gar nicht auf",
      "rs4" not in (_z_a4["modell_anker_user"] or set()))


# ══ I) A3 vs A4 Konflikt ═════════════════════════════════════════════════════
def karte(titel, lid, preis, km, ez, beschr):
    bild = f"https://img.kleinanzeigen.de/api/v1/prod-ads/images/aa/{lid}-uuid"
    return (f"* [![{titel} Vorschau]({bild})\n\n"
            f"  20](/s-anzeige/x/{lid}-216-1111)\n\n  12307 Berlin\n\n  Heute\n\n"
            f"  ## [{titel}](/s-anzeige/x/{lid}-216-1111)\n\n  {beschr}\n\n"
            f"  {preis} €\n\n  {km} km   EZ {ez}\n")


URL = "https://www.kleinanzeigen.de/s-autos/audi"
TITEL = "Audi A3 gebraucht kaufen | kleinanzeigen.de"
KARTEN = (karte("Audi A3 8V 2.0 TDI Sportback", "9201", "15.900", "88.000",
                "06/2017", "Audi A3 8V, 2.0 TDI, Diesel")
          + karte("Audi A4 Avant 2.0 TDI", "9205", "18.900", "90.000",
                  "06/2017", "Audi A4 Avant, 2.0 TDI Diesel")
          + karte("Audi A6 Avant 2.0 TDI", "9211", "24.900", "85.000",
                  "06/2017", "Audi A6 Avant, 2.0 TDI Diesel")
          + karte("BMW 320d G20 Limousine", "9212", "28.900", "80.000",
                  "06/2017", "BMW 320d, Diesel"))
_roh = _extrahiere_aus_text(TITEL + "\n\n## Ergebnisse\n\n" + KARTEN, URL,
                            "market_category",
                            grenzen=(len(TITEL) + 1, len(TITEL) + 2),
                            seiten_body=_eindeutige_karosserie(URL))
_bew = {b.preis_eur: _bewerte(b, _z_a3) for b in _roh}
check("I: die echte A3-Karte ueberlebt (vorher: an 'rs3' gestorben)",
      15900 in _bew and _bew[15900].vergleichbarkeit != "ungeeignet")
check("I2: Audi A4 bleibt beim A3-Ziel draussen",
      18900 in _bew and _bew[18900].vergleichbarkeit == "ungeeignet")
check("I3: Audi A6 bleibt draussen",
      24900 in _bew and _bew[24900].vergleichbarkeit == "ungeeignet")
check("I4: BMW 320d bleibt draussen",
      28900 in _bew and _bew[28900].vergleichbarkeit == "ungeeignet")


# ══ J) Kurze Nicht-Modell-Kuerzel werden NIE Anker ═══════════════════════════
for marke, modell, verboten in [("Ford", "Focus ST", "st"),
                                ("Ford", "Fiesta ST", "st"),
                                ("Ford", "Focus ST-Line", "st"),
                                ("Opel", "Insignia ST", "st"),
                                ("BMW", "3er GT", "gt"),
                                ("BMW", "5er GT", "gt"),
                                ("Audi", "A3 RS", "rs")]:
    check(f"J: '{modell}' -> '{verboten}' wird kein Anker",
          verboten not in anker(marke, modell))
check("J2: 'Focus ST' behaelt den echten Modellnamen",
      anker("Ford", "Focus ST") == ["focus"])
check("J3: 'Insignia ST' behaelt 'insignia'",
      anker("Opel", "Insignia ST") == ["insignia"])
check("J4: '3er GT' behaelt '3er'", anker("BMW", "3er GT") == ["3er"])


# ══ K) Wortgrenzen — kein Teilstring-Match ═══════════════════════════════════
check("K: 'Sa3lon' liefert nicht 'a3'", "a3" not in anker("Audi", "Sa3lon"))
check("K2: 'xa3' liefert nicht 'a3'", "a3" not in anker("Audi", "xa3"))
check("K3: 'A33' liefert nicht 'a3'", "a3" not in anker("Audi", "A33"))
check("K4: 'A3' selbst liefert 'a3'", anker("Audi", "A3") == ["a3"])


# ══ L) Bestehende Anker unveraendert ═════════════════════════════════════════
for marke, modell, erwartet in [("BMW", "320d G20", ["320d"]),
                                ("BMW", "330i G20", ["330i"]),
                                ("BMW", "M4 F82", ["f82"]),
                                ("Opel", "Insignia Grand Sport", ["insignia"]),
                                ("Volkswagen", "Golf VII", ["golf"]),
                                ("Ford", "Focus", ["focus"])]:
    check(f"L: {marke} {modell} -> {erwartet}", anker(marke, modell) == erwartet)

print()
if _fails:
    print(f"{len(_fails)} FEHLER: " + "; ".join(_fails))
    sys.exit(1)
print("Alle Pruefungen bestanden.")
