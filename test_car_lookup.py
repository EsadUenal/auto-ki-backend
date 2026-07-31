"""
Test: Baureihe-Erkennung (app/car_lookup.find_baureihe).

Root-Cause-Absicherung: Der eingegebene "Modell"-String ist oft eine
Motorbezeichnung ("320d"), kein Baureihen-Name ("3er"). find_baureihe muss ihn
den Motorvarianten der Baureihen zuordnen — sonst gewinnt bei fehlendem
Modell-Treffer faelschlich eine andere Baureihe derselben Marke (BMW M4) allein
ueber Marke + Baujahr.

Die DB-Zugriffe (Baureihen + Motorvarianten) werden gemockt — keine echte DB.

Ausfuehren:  python test_car_lookup.py
"""
import os
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_test_"), "test.db")

import app.car_lookup as cl   # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


def set_daten(baureihen, motoren):
    cl.get_alle_baureihen_kurz = lambda: baureihen
    cl.get_alle_motorvarianten_kurz = lambda: motoren
    # get_baureihe echot die identifizierende Zeile (marke/modell/generation) zurueck.
    cl.get_baureihe = lambda marke, modell, gen: {"marke": marke, "modell": modell, "generation": gen}


def gen_of(res):
    return (res or {}).get("generation")


# ── Voller BMW-Bestand: 3er G20, 3er F30, M4 F82 + VW Golf ──────────────────
BAUREIHEN = [
    {"id": "bmw-3er-g20", "marke": "BMW", "modell": "3er", "generation": "G20",
     "bauzeitraum_von": 2018, "bauzeitraum_bis": None},
    {"id": "bmw-3er-f30", "marke": "BMW", "modell": "3er", "generation": "F30",
     "bauzeitraum_von": 2012, "bauzeitraum_bis": 2019},
    {"id": "bmw-m4-f82", "marke": "BMW", "modell": "M4", "generation": "F82",
     "bauzeitraum_von": 2014, "bauzeitraum_bis": 2020},
    {"id": "vw-golf-vii", "marke": "Volkswagen", "modell": "Golf", "generation": "VII",
     "bauzeitraum_von": 2012, "bauzeitraum_bis": 2019},
]
MOTOREN = [
    {"baureihe_id": "bmw-3er-g20", "bezeichnung": "320d", "motorcode": "B47"},
    {"baureihe_id": "bmw-3er-g20", "bezeichnung": "330i", "motorcode": "B48"},
    {"baureihe_id": "bmw-3er-f30", "bezeichnung": "320d", "motorcode": "N47"},
    {"baureihe_id": "bmw-m4-f82", "bezeichnung": "M4 Competition", "motorcode": "S55"},
    {"baureihe_id": "vw-golf-vii", "bezeichnung": "2.0 TDI", "motorcode": "CUNA"},
]
set_daten(BAUREIHEN, MOTOREN)

# KERNFALL: 320d 2020 -> 3er G20 (NICHT M4, NICHT F30)
check("320d 2020 -> 3er G20 (nicht M4)", gen_of(cl.find_baureihe("BMW", "320d", 2020)) == "G20")
# Baujahr waehlt die richtige Generation derselben Motorbezeichnung
check("320d 2016 -> 3er F30", gen_of(cl.find_baureihe("BMW", "320d", 2016)) == "F30")
# Motorcode-Treffer
check("Motorcode B47 2020 -> 3er G20", gen_of(cl.find_baureihe("BMW", "B47", 2020)) == "G20")
# Direkter Baureihen-Name / M-Modell weiterhin korrekt
check("M4 2016 -> M4 F82", gen_of(cl.find_baureihe("BMW", "M4", 2016)) == "F82")
check("330i 2020 -> 3er G20", gen_of(cl.find_baureihe("BMW", "330i", 2020)) == "G20")
check("Golf 2015 -> Golf VII", gen_of(cl.find_baureihe("VW", "Golf", 2015)) == "VII")
# Cross-Brand-Schutz: VW-Motor darf keine BMW-Baureihe matchen
check("BMW + '2.0 TDI' -> None (kein Cross-Brand)", cl.find_baureihe("BMW", "2.0 TDI", 2019) is None)

# ── Nur M4 vorhanden (kein 3er): 320d darf NICHT das M4-Profil ziehen ───────
set_daten(
    [{"id": "bmw-m4-f82", "marke": "BMW", "modell": "M4", "generation": "F82",
      "bauzeitraum_von": 2014, "bauzeitraum_bis": 2020}],
    [{"baureihe_id": "bmw-m4-f82", "bezeichnung": "M4 Competition", "motorcode": "S55"}],
)
check("320d ohne 3er-Profil -> None (kein falsches M4)", cl.find_baureihe("BMW", "320d", 2019) is None)
check("M4 (nur M4 da) -> M4", gen_of(cl.find_baureihe("BMW", "M4", 2016)) == "F82")

# ── Fremdmarke / kein Modell ────────────────────────────────────────────────
set_daten(BAUREIHEN, MOTOREN)
check("Ferrari 488 -> None", cl.find_baureihe("Ferrari", "488", 2019) is None)
check("Nur Marke BMW (kein Modell) -> Best-Guess erlaubt", cl.find_baureihe("BMW", None, 2016) is not None)

# ── find_motor: Einheiten-/Kraftstoff-getrennter Leistungsabgleich ──────────
# Regressionsschutz: "190 PS" (320d, Diesel) darf NICHT den 330i (190 kW, Benzin)
# ziehen — sonst bekommt der Kaufcheck Benzin-Specs für einen Diesel.
G20_MOTOREN = {"motoren": [
    {"bezeichnung": "320i", "motorcode": "B48", "kraftstoff": "Benzin",
     "leistung_ps": 184, "leistung_kw": 135},
    {"bezeichnung": "330i", "motorcode": "B48", "kraftstoff": "Benzin",
     "leistung_ps": 258, "leistung_kw": 190},
    {"bezeichnung": "320d", "motorcode": "B47", "kraftstoff": "Diesel",
     "leistung_ps": 190, "leistung_kw": 140},
    {"bezeichnung": "330e", "motorcode": "B48", "kraftstoff": "Plug-in-Hybrid",
     "leistung_ps": 292, "leistung_kw": 215},
]}


def mbez(res):
    return (res or {}).get("bezeichnung")


check("find_motor '2.0 Diesel, 190 PS' -> 320d (NICHT 330i)",
      mbez(cl.find_motor(G20_MOTOREN, "2.0 Diesel, 190 PS, Automatik")) == "320d")
check("find_motor '320d' (Bezeichnung) -> 320d", mbez(cl.find_motor(G20_MOTOREN, "320d")) == "320d")
check("find_motor '330i' (Bezeichnung) -> 330i", mbez(cl.find_motor(G20_MOTOREN, "330i")) == "330i")
check("find_motor '190 PS' (nur PS) -> 320d", mbez(cl.find_motor(G20_MOTOREN, "190 PS")) == "320d")
check("find_motor '140 kW Diesel' -> 320d", mbez(cl.find_motor(G20_MOTOREN, "140 kW Diesel")) == "320d")
check("find_motor '190 kW Benzin' -> 330i", mbez(cl.find_motor(G20_MOTOREN, "190 kW Benzin")) == "330i")
check("find_motor '500 PS' (kein Treffer) -> None", cl.find_motor(G20_MOTOREN, "500 PS") is None)

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER: " + ", ".join(FEHLER))
    raise SystemExit(1)
print("Alle Car-Lookup-Tests bestanden.")
