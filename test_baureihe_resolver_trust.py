"""
Baureihen-Resolver und Userinput-Prioritaet — deterministisch, KEIN Netzwerk.

P0-Befund der Etappe-1-Fahrzeugmatrix (2026-08-19): Eine Anfrage
"VW Golf VII, 2016, 2.0 TDI 150 PS, Diesel" wurde auf `vw-golf-8` aufgeloest.
Zwei unabhaengige Defekte wirkten zusammen:

  1. RESOLVER (app/car_lookup.find_baureihe): Ein fehlender Bauzeitraum wurde per
     `or 0` / `or 9999` zu einem UNIVERSELLEN Zeitraum aufgeblasen. Die undatierte
     Duplikat-Zeile `vw-golf-8` bekam damit bei JEDEM Baujahr dieselben +5 wie eine
     sauber datierte Generation und gewann den Gleichstand allein ueber die
     DB-Zeilenreihenfolge — fuer die Baujahre 1995 bis 2022.
     Zusaetzlich blieb eine explizite Generationsangabe des Nutzers ("Golf VII")
     wirkungslos: verglichen wurde nur gegen `r["modell"]` ("Golf").

  2. USERINPUT-PRIORITAET (app/marktvergleich.baue_ziel): Kraftstoff und Leistung
     wurden ZUERST aus der DB-Motorvariante genommen und erst danach aus der
     Nutzerangabe. Die einzige Motorvariante von `vw-golf-8` ist ein 2.0 TSI mit
     245 PS (Benzin) — ein ausdrueckliches "Diesel" des Nutzers wurde damit still
     zu "Benzin" und 150 PS zu 245 PS. Anschliessend haette die harte
     Kraftstoffpruefung jedes korrekte Diesel-Inserat verworfen.

Prioritaet ist jetzt durchgaengig:
    EXPLIZITER USERINPUT > VERIFIZIERTE EVIDENZ > UNVERIFIED DB-FALLBACK

Die Baureihen-Fixtures bilden den realen DB-Bestand nach (inkl. der kaputten
undatierten Zeile), damit der Test nicht vom Live-DB-Inhalt abhaengt.

    python test_baureihe_resolver_trust.py
"""
import os
import sys
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_res_"), "test.db")
sys.path.insert(0, ".")

from types import SimpleNamespace                                        # noqa: E402

import app.car_lookup as cl                                              # noqa: E402
from app.marktvergleich import baue_ziel                                 # noqa: E402

_fails: list[str] = []


def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        _fails.append(name)


def set_daten(baureihen, motoren):
    cl.get_alle_baureihen_kurz = lambda: baureihen
    cl.get_alle_motorvarianten_kurz = lambda: motoren
    cl.get_baureihe = lambda marke, modell, gen: {
        "marke": marke, "modell": modell, "generation": gen,
        "id": next((r["id"] for r in baureihen
                    if r["marke"] == marke and r["modell"] == modell
                    and r["generation"] == gen), None)}


# ── Realer DB-Bestand nachgebildet ──────────────────────────────────────────
# `vw-golf-8` ist die tatsaechlich vorhandene kaputte Zeile: Generation "8",
# BEIDE Bauzeitraum-Grenzen NULL. Sie steht bewusst VOR den datierten Zeilen —
# genau diese Reihenfolge liess sie frueher jeden Gleichstand gewinnen.
BAUREIHEN = [
    {"id": "vw-golf-8", "marke": "Volkswagen", "modell": "Golf", "generation": "8",
     "bauzeitraum_von": None, "bauzeitraum_bis": None},
    {"id": "volkswagen-golf-iv", "marke": "Volkswagen", "modell": "Golf",
     "generation": "IV", "bauzeitraum_von": 1997, "bauzeitraum_bis": 2003},
    {"id": "volkswagen-golf-v", "marke": "Volkswagen", "modell": "Golf",
     "generation": "V", "bauzeitraum_von": 2003, "bauzeitraum_bis": 2008},
    {"id": "volkswagen-golf-vi", "marke": "Volkswagen", "modell": "Golf",
     "generation": "VI", "bauzeitraum_von": 2008, "bauzeitraum_bis": 2012},
    {"id": "volkswagen-golf-vii", "marke": "Volkswagen", "modell": "Golf",
     "generation": "VII", "bauzeitraum_von": 2012, "bauzeitraum_bis": 2020},
    {"id": "volkswagen-golf-viii", "marke": "Volkswagen", "modell": "Golf",
     "generation": "VIII", "bauzeitraum_von": 2019, "bauzeitraum_bis": None},
    # Fremdmarken-Kontrollzeilen (Regression J)
    {"id": "bmw-3er-g20-g21", "marke": "BMW", "modell": "3er", "generation": "G20/G21",
     "bauzeitraum_von": 2018, "bauzeitraum_bis": None},
    {"id": "bmw-3er-f30", "marke": "BMW", "modell": "3er", "generation": "F30",
     "bauzeitraum_von": 2012, "bauzeitraum_bis": 2019},
    {"id": "bmw-m4-f82", "marke": "BMW", "modell": "M4", "generation": "F82",
     "bauzeitraum_von": 2014, "bauzeitraum_bis": 2020},
    {"id": "bmw-m4-g82", "marke": "BMW", "modell": "M4", "generation": "G82",
     "bauzeitraum_von": 2021, "bauzeitraum_bis": None},
    {"id": "audi-a3-typ-8p", "marke": "Audi", "modell": "A3", "generation": "Typ 8P",
     "bauzeitraum_von": 2003, "bauzeitraum_bis": 2012},
    {"id": "audi-a3-typ-8v", "marke": "Audi", "modell": "A3", "generation": "Typ 8V",
     "bauzeitraum_von": 2012, "bauzeitraum_bis": 2020},
]
MOTOREN = [
    # Die einzige Motorvariante der kaputten Zeile: Benziner mit 245 PS.
    {"baureihe_id": "vw-golf-8", "bezeichnung": "2.0 TSI", "motorcode": None},
    {"baureihe_id": "volkswagen-golf-vii", "bezeichnung": "2.0 TDI", "motorcode": "CUNA"},
    {"baureihe_id": "bmw-3er-g20-g21", "bezeichnung": "320d", "motorcode": "B47"},
    {"baureihe_id": "bmw-3er-g20-g21", "bezeichnung": "330i", "motorcode": "B48"},
    {"baureihe_id": "bmw-3er-f30", "bezeichnung": "320d", "motorcode": "N47"},
    {"baureihe_id": "bmw-m4-f82", "bezeichnung": "M4 Competition", "motorcode": "S55"},
    {"baureihe_id": "audi-a3-typ-8v", "bezeichnung": "2.0 TDI", "motorcode": "CRBC"},
]
set_daten(BAUREIHEN, MOTOREN)


def bid(marke, modell, baujahr):
    br = cl.find_baureihe(marke, modell, baujahr)
    return br.get("id") if br else None


# ══ A-C) Golf-Generationen loesen korrekt auf ════════════════════════════════
check("A: Golf VII 2016 -> volkswagen-golf-vii (NICHT vw-golf-8)",
      bid("Volkswagen", "Golf VII", 2016) == "volkswagen-golf-vii")
check("B: Golf VI 2010 -> volkswagen-golf-vi",
      bid("Volkswagen", "Golf VI", 2010) == "volkswagen-golf-vi")
check("C: Golf VIII 2021 -> volkswagen-golf-viii",
      bid("Volkswagen", "Golf VIII", 2021) == "volkswagen-golf-viii")
check("C2: Golf V 2006 -> volkswagen-golf-v",
      bid("Volkswagen", "Golf V", 2006) == "volkswagen-golf-v")

# Auch OHNE explizite Generationsangabe muss das Baujahr die richtige Zeile treffen.
check("C3: blosses 'Golf' 2016 -> volkswagen-golf-vii (Baujahr entscheidet)",
      bid("Volkswagen", "Golf", 2016) == "volkswagen-golf-vii")
check("C4: blosses 'Golf' 2010 -> volkswagen-golf-vi",
      bid("Volkswagen", "Golf", 2010) == "volkswagen-golf-vi")
check("C5: blosses 'Golf' 1999 -> volkswagen-golf-iv",
      bid("Volkswagen", "Golf", 1999) == "volkswagen-golf-iv")
check("C6: 'Golf VII' trifft NICHT die VIII-Zeile (kein Teilstring-Match)",
      bid("Volkswagen", "Golf VII", 2016) != "volkswagen-golf-viii")
check("C7: 'Golf VIII' trifft NICHT die VII-Zeile",
      bid("Volkswagen", "Golf VIII", 2021) != "volkswagen-golf-vii")

# ══ D) Undatierte Duplikat-Zeile schlaegt keine datierte ═════════════════════
check("D: die undatierte Zeile gewinnt fuer KEIN Baujahr mehr",
      all(bid("Volkswagen", "Golf", j) != "vw-golf-8"
          for j in (1999, 2003, 2006, 2010, 2012, 2016, 2019, 2021, 2022)))
check("D2: auch mit expliziter Generationsangabe nicht",
      all(bid("Volkswagen", f"Golf {g}", j) != "vw-golf-8"
          for g, j in (("IV", 1999), ("V", 2006), ("VI", 2010),
                       ("VII", 2016), ("VIII", 2021))))

# ══ E) Undatierte Zeile bleibt Fallback, wenn es keine Alternative gibt ══════
_ohne_datierte = [r for r in BAUREIHEN if r["id"] != "volkswagen-golf-iv"
                  and not r["id"].startswith("volkswagen-golf-")]
set_daten(_ohne_datierte, MOTOREN)
check("E: ohne datierte Alternative dient die undatierte Zeile weiter als Fallback",
      bid("Volkswagen", "Golf", 2016) == "vw-golf-8")
set_daten(BAUREIHEN, MOTOREN)


# ══ F-I) Userinput schlaegt unverified DB ════════════════════════════════════
# Widersprechende Motorvariante: die DB behauptet Benzin/245 PS.
BR_TEST = {"id": "vw-golf-8", "marke": "Volkswagen", "modell": "Golf", "generation": "8"}
MOTOR_WIDERSPRUCH = {"bezeichnung": "2.0 TSI", "motorcode": None,
                     "kraftstoff": "Benzin", "leistung_ps": 245}


def ziel_mit(motor, kraftstoff):
    req = SimpleNamespace(marke="Volkswagen", modell="Golf", baujahr=2016,
                          kilometerstand=90_000, motor=motor, kraftstoff=kraftstoff,
                          getriebe="Automatik", preis_eur=15_000)
    return baue_ziel(BR_TEST, MOTOR_WIDERSPRUCH, req, [BR_TEST], [])


_z_diesel = ziel_mit("2.0 TDI 150 PS", "Diesel")
check("F: User 'Diesel' bleibt Diesel trotz DB-Benzin",
      _z_diesel["kraftstoff"] == "Diesel")
_z_benzin = ziel_mit("1.4 TSI 125 PS", "Benzin")
check("G: User 'Benzin' bleibt Benzin (kein blindes DB-Ueberschreiben)",
      _z_benzin["kraftstoff"] == "Benzin")
check("H: User-Leistung 150 PS wird NICHT durch DB-245-PS ersetzt",
      _z_diesel["leistung_ps"] == 150)
check("H2: User-Leistung 125 PS ebenso",
      _z_benzin["leistung_ps"] == 125)
check("I: die DB-Bezeichnung '2.0 TSI' wird nicht als Zielmotor uebernommen",
      "tsi" not in "".join(_z_diesel["ziel_motor_tokens"]))

# Ohne Nutzerangabe darf die DB weiterhin als FALLBACK dienen — aber nie hart.
_z_ohne = ziel_mit("2.0 TDI", None)
check("E2: ohne Nutzerangabe bleibt der DB-Wert als Fallback erhalten",
      _z_ohne["kraftstoff"] == "Benzin" and _z_ohne["leistung_ps"] == 245)
check("E3: dieser DB-Fallback darf NICHT hart ablehnen (Trust Boundary)",
      _z_ohne["kraftstoff_hart"] is False and _z_ohne["leistung_hart"] is False)


# ══ J) Bestehende Baureihenauflösung unveraendert ════════════════════════════
for marke, modell, bj, erwartet in [
        ("BMW", "330i G20", 2020, "bmw-3er-g20-g21"),
        ("BMW", "320d G20", 2019, "bmw-3er-g20-g21"),
        ("BMW", "3er", 2019, "bmw-3er-g20-g21"),
        ("BMW", "M4 F82", 2016, "bmw-m4-f82"),
        ("BMW", "M4 G82", 2022, "bmw-m4-g82"),
        ("Audi", "A3 8V", 2017, "audi-a3-typ-8v"),
        ("Audi", "A3 8P", 2008, "audi-a3-typ-8p")]:
    check(f"J: {marke} {modell} {bj} -> {erwartet}",
          bid(marke, modell, bj) == erwartet)

print()
if _fails:
    print(f"{len(_fails)} FEHLER: " + "; ".join(_fails))
    sys.exit(1)
print("Alle Pruefungen bestanden.")
