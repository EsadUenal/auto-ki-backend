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

# ── Mercedes-Verkaufsbezeichnungen: Präfix sauber unterscheiden ──────────────
# Root Cause: "C 200" (mit Leerzeichen) traf die DB-Variante "C200" nicht (fehlende
# Normalisierung) UND blutete per Substring in "GLC 200" durch -> GLC. Analog "CLA
# 200" -> A-Klasse (weil "a 200" Substring von "cla 200" ist). Der Präfix muss exakt
# passen; das Baujahr wählt die Generation.
MB_BAUREIHEN = [
    {"id": "mercedes-benz-c-klasse-w205", "marke": "Mercedes-Benz", "modell": "C-Klasse",
     "generation": "W205", "bauzeitraum_von": 2014, "bauzeitraum_bis": 2021},
    {"id": "mercedes-benz-c-klasse-w206", "marke": "Mercedes-Benz", "modell": "C-Klasse",
     "generation": "W206", "bauzeitraum_von": 2021, "bauzeitraum_bis": None},
    {"id": "mercedes-benz-glc-x253", "marke": "Mercedes-Benz", "modell": "GLC",
     "generation": "X253", "bauzeitraum_von": 2015, "bauzeitraum_bis": 2022},
    {"id": "mercedes-benz-e-klasse-w213", "marke": "Mercedes-Benz", "modell": "E-Klasse",
     "generation": "W213", "bauzeitraum_von": 2016, "bauzeitraum_bis": 2023},
    {"id": "mercedes-benz-cla-c117", "marke": "Mercedes-Benz", "modell": "CLA",
     "generation": "C117", "bauzeitraum_von": 2013, "bauzeitraum_bis": 2019},
    {"id": "mercedes-benz-a-klasse-w177", "marke": "Mercedes-Benz", "modell": "A-Klasse",
     "generation": "W177", "bauzeitraum_von": 2018, "bauzeitraum_bis": None},
]
MB_MOTOREN = [
    {"baureihe_id": "mercedes-benz-c-klasse-w205", "bezeichnung": "C200", "motorcode": ""},
    {"baureihe_id": "mercedes-benz-c-klasse-w205", "bezeichnung": "C220 d", "motorcode": ""},
    {"baureihe_id": "mercedes-benz-c-klasse-w205", "bezeichnung": "C300 e", "motorcode": ""},
    {"baureihe_id": "mercedes-benz-c-klasse-w206", "bezeichnung": "C200", "motorcode": ""},
    {"baureihe_id": "mercedes-benz-glc-x253", "bezeichnung": "GLC 200", "motorcode": "M 274 DE 20 AL"},
    {"baureihe_id": "mercedes-benz-glc-x253", "bezeichnung": "GLC 220 d", "motorcode": "OM 651 DE 22 LA"},
    {"baureihe_id": "mercedes-benz-e-klasse-w213", "bezeichnung": "E 220 d", "motorcode": ""},
    {"baureihe_id": "mercedes-benz-cla-c117", "bezeichnung": "CLA 200", "motorcode": "M 270 DE 16 AL"},
    {"baureihe_id": "mercedes-benz-a-klasse-w177", "bezeichnung": "A 180", "motorcode": "M 282 DE 14 LA"},
    {"baureihe_id": "mercedes-benz-a-klasse-w177", "bezeichnung": "A 200", "motorcode": "M 282 DE 14 LA"},
]
set_daten(MB_BAUREIHEN, MB_MOTOREN)

check("1: 'C 200' 2019 -> C-Klasse W205", gen_of(cl.find_baureihe("Mercedes-Benz", "C 200", 2019)) == "W205")
check("2: 'C200' 2019 -> C-Klasse W205", gen_of(cl.find_baureihe("Mercedes-Benz", "C200", 2019)) == "W205")
check("2b: 'C-200' 2019 -> C-Klasse W205", gen_of(cl.find_baureihe("Mercedes-Benz", "C-200", 2019)) == "W205")
check("3: 'C 220 d' 2019 -> C-Klasse W205", gen_of(cl.find_baureihe("Mercedes-Benz", "C 220 d", 2019)) == "W205")
check("3b: 'C220d' 2019 -> C-Klasse W205", gen_of(cl.find_baureihe("Mercedes-Benz", "C220d", 2019)) == "W205")
check("4: 'GLC 200' 2019 -> GLC (NICHT C-Klasse)",
      gen_of(cl.find_baureihe("Mercedes-Benz", "GLC 200", 2019)) == "X253")
check("4b: 'GLC200' 2019 -> GLC", gen_of(cl.find_baureihe("Mercedes-Benz", "GLC200", 2019)) == "X253")
check("5: 'E 220 d' 2019 -> E-Klasse", gen_of(cl.find_baureihe("Mercedes-Benz", "E 220 d", 2019)) == "W213")
check("6: 'CLA 200' 2019 -> CLA (NICHT A-/C-Klasse)",
      gen_of(cl.find_baureihe("Mercedes-Benz", "CLA 200", 2019)) == "C117")
check("6b: 'A 180' 2019 -> A-Klasse", gen_of(cl.find_baureihe("Mercedes-Benz", "A 180", 2019)) == "W177")
check("7: 'C 200' 2015 -> W205 / 2022 -> W206 (Baujahr wählt Generation)",
      gen_of(cl.find_baureihe("Mercedes-Benz", "C 200", 2015)) == "W205"
      and gen_of(cl.find_baureihe("Mercedes-Benz", "C 200", 2022)) == "W206")
# 'C 200' darf NIEMALS GLC ziehen (Kern-Bug)
check("Kern-Bug behoben: 'C 200' zieht nie GLC",
      "glc" not in (cl.find_baureihe("Mercedes-Benz", "C 200", 2019) or {}).get("generation", "").lower()
      and cl.find_baureihe("Mercedes-Benz", "C 200", 2019)["modell"] == "C-Klasse")

# BMW-Regression im Mercedes-Datensatz-Kontext zurücksetzen
set_daten(BAUREIHEN, MOTOREN)
check("8: BMW '320d' 2020 weiterhin -> 3er G20", gen_of(cl.find_baureihe("BMW", "320d", 2020)) == "G20")

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
