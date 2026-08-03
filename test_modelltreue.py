"""
Test: Harte Modelltreue im Marktvergleich (Root-Cause #5) — deterministisch, kein LLM.

Kein Fahrzeug-Hardcoding: die Trennung erfolgt strukturell über Modell-Token aus der
DB (Modellnamen + Motor-Verkaufsbezeichnungen). Regression gegen die gemeldeten Fälle:
Opel Mokka darf nicht in den Insignia-Median, 5er nicht in den 3er, GLC nicht in die
C-Klasse, Passat nicht in den Golf. Gültige Zieltreffer bleiben erhalten.

Ausfuehren:  python test_modelltreue.py
"""
import os
import sys
import tempfile
from types import SimpleNamespace

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_mt_"), "test.db")
sys.path.insert(0, ".")

from app.marktvergleich import analysiere_markt, baue_ziel, modell_relevant   # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


def preise(ma):
    return sorted(b.preis_eur for b in ma.beobachtungen)


# ══ 1) Opel Insignia — Opel Mokka darf NICHT hinein (Marke ohne Chassis-Code) ══
INSIGNIA = {"marke": "Opel", "modell": "Insignia", "generation": "B", "id": "opel-insignia-b"}
OPEL_ALLE = [
    INSIGNIA,
    {"id": "opel-mokka-a", "marke": "Opel", "modell": "Mokka", "generation": "A"},
    {"id": "opel-corsa-f", "marke": "Opel", "modell": "Corsa", "generation": "F"},
    {"id": "opel-astra-k", "marke": "Opel", "modell": "Astra", "generation": "K"},
]
ZIEL_INS = baue_ziel(INSIGNIA, {"kraftstoff": "Diesel"},
                     SimpleNamespace(baujahr=2019, kilometerstand=90000, kraftstoff=None),
                     OPEL_ALLE, [])
check("Insignia: modell_tokens enthält 'insignia'", "insignia" in ZIEL_INS["modell_tokens"])
check("Insignia: fremd_modelle enthält 'mokka'/'corsa'/'astra'",
      {"mokka", "corsa", "astra"} <= ZIEL_INS["fremd_modelle"])

WEB_INS = [
    {"url": "https://kleinanzeigen.de/insignia", "title": "Opel Insignia B 2.0 CDTI",
     "content": (
         "Opel Insignia 2.0 CDTI 18.500 € 85.000 km EZ 05/2019 . "
         "Opel Insignia Grand Sport 17.900 € 92.000 km EZ 03/2019 . "
         "Opel Insignia 2.0 Diesel 19.200 € 80.000 km EZ 06/2019 . "
         "Opel Insignia 16.900 € 98.000 km EZ 02/2019")},
    {"url": "https://mobile.de/opel-mokka", "title": "Opel Mokka gebraucht kaufen",
     "content": "Opel Mokka 1.4 Turbo 12.900 € 60.000 km EZ 03/2019 . Opel Mokka X 11.500 € 70.000 km EZ 01/2019"},
]
ma_ins = analysiere_markt(WEB_INS, ZIEL_INS, 18000)
check("1: Opel Mokka (12.900/11.500) NICHT im Insignia-Vergleich",
      12900 not in preise(ma_ins) and 11500 not in preise(ma_ins))
check("1: gültige Insignia-Preise verwendet (mind. 3)", len(preise(ma_ins)) >= 3)
check("1: Median im Insignia-Bereich (16.000–20.000)",
      ma_ins.median_eur is not None and 16000 <= ma_ins.median_eur <= 20000)
check("1: keine verwendete Beobachtung stammt von der Mokka-Seite",
      all("mokka" not in (b.quelle_url or "") for b in ma_ins.beobachtungen))

# ══ 2) BMW 3er — 5er (520d) darf NICHT hinein (Motorbezeichnung trennt) ═══════
BMW3 = {"marke": "BMW", "modell": "3er", "generation": "G20", "id": "bmw-3er-g20"}
BMW_ALLE = [BMW3, {"id": "bmw-5er-g30", "marke": "BMW", "modell": "5er", "generation": "G30"}]
BMW_MOTOR = [
    {"baureihe_id": "bmw-3er-g20", "bezeichnung": "320d", "motorcode": "B47"},
    {"baureihe_id": "bmw-3er-g20", "bezeichnung": "330i", "motorcode": "B48"},
    {"baureihe_id": "bmw-5er-g30", "bezeichnung": "520d", "motorcode": "B47"},
    {"baureihe_id": "bmw-5er-g30", "bezeichnung": "530i", "motorcode": "B48"},
]
ZIEL_3 = baue_ziel(BMW3, {"kraftstoff": "Diesel"},
                   SimpleNamespace(baujahr=2020, kilometerstand=70000, kraftstoff=None),
                   BMW_ALLE, BMW_MOTOR)
check("2: fremd_modelle enthält '5er' und '520d'", {"5er", "520d"} <= ZIEL_3["fremd_modelle"])
check("2: '320d' bleibt Zieltoken (nicht fälschlich fremd)", "320d" in ZIEL_3["modell_tokens"] and "320d" not in ZIEL_3["fremd_modelle"])
WEB_3 = [
    {"url": "https://a.de/3er", "title": "BMW 320d G20",
     "content": ("BMW 320d G20 29.000 € 65.000 km EZ 04/2020 . "
                 "BMW 320d 27.500 € 72.000 km EZ 06/2020 . "
                 "BMW 320d G20 28.200 € 68.000 km EZ 05/2020")},
    {"url": "https://b.de/5er", "title": "BMW 520d G30",
     "content": "BMW 520d G30 34.900 € 60.000 km EZ 03/2020 . BMW 520d 33.500 € 66.000 km EZ 05/2020"},
]
ma_3 = analysiere_markt(WEB_3, ZIEL_3, 28000)
check("2: BMW 520d (34.900/33.500) NICHT im 3er-Vergleich",
      34900 not in preise(ma_3) and 33500 not in preise(ma_3))
check("2: 320d-Preise verwendet", len(preise(ma_3)) >= 3 and max(preise(ma_3)) < 31000)

# ══ 3) Mercedes C-Klasse — GLC darf NICHT hinein ═════════════════════════════
MBC = {"marke": "Mercedes-Benz", "modell": "C-Klasse", "generation": "W205", "id": "mb-c-w205"}
MB_ALLE = [MBC, {"id": "mb-glc-x253", "marke": "Mercedes-Benz", "modell": "GLC", "generation": "X253"}]
MB_MOTOR = [
    {"baureihe_id": "mb-c-w205", "bezeichnung": "C200", "motorcode": "M264"},
    {"baureihe_id": "mb-c-w205", "bezeichnung": "C220d", "motorcode": "OM654"},
    {"baureihe_id": "mb-glc-x253", "bezeichnung": "GLC220d", "motorcode": "OM654"},
    {"baureihe_id": "mb-glc-x253", "bezeichnung": "GLC300", "motorcode": "M264"},
]
ZIEL_C = baue_ziel(MBC, {"kraftstoff": "Benzin"},
                   SimpleNamespace(baujahr=2019, kilometerstand=64000, kraftstoff=None),
                   MB_ALLE, MB_MOTOR)
check("3: fremd_modelle enthält 'glc'/'glc220d'", "glc" in ZIEL_C["fremd_modelle"])
check("3: 'c200' bleibt Zieltoken", "c200" in ZIEL_C["modell_tokens"])
WEB_C = [
    {"url": "https://a.de/ckl", "title": "Mercedes C 200 W205",
     "content": ("Mercedes C200 W205 26.500 € 60.000 km EZ 05/2019 . "
                 "Mercedes C200 25.900 € 66.000 km EZ 04/2019 . "
                 "Mercedes C200 27.200 € 58.000 km EZ 06/2019")},
    {"url": "https://b.de/glc", "title": "Mercedes GLC 220 d",
     "content": "Mercedes GLC220d 38.900 € 55.000 km EZ 03/2019 . Mercedes GLC300 41.000 € 50.000 km EZ 05/2019"},
]
ma_c = analysiere_markt(WEB_C, ZIEL_C, 26000)
check("3: Mercedes GLC (38.900/41.000) NICHT in C-Klasse-Vergleich",
      38900 not in preise(ma_c) and 41000 not in preise(ma_c))
check("3: C200-Preise verwendet", len(preise(ma_c)) >= 3 and max(preise(ma_c)) < 30000)

# ══ 4) VW Golf — Passat darf NICHT hinein ════════════════════════════════════
GOLF = {"marke": "Volkswagen", "modell": "Golf", "generation": "7", "id": "vw-golf-7"}
VW_ALLE = [GOLF, {"id": "vw-passat-b8", "marke": "Volkswagen", "modell": "Passat", "generation": "B8"}]
ZIEL_G = baue_ziel(GOLF, {"kraftstoff": "Benzin"},
                   SimpleNamespace(baujahr=2018, kilometerstand=70000, kraftstoff=None), VW_ALLE, [])
check("4: fremd_modelle enthält 'passat'", "passat" in ZIEL_G["fremd_modelle"])
WEB_G = [
    {"url": "https://a.de/golf", "title": "VW Golf 7",
     "content": ("VW Golf 1.5 TSI 16.900 € 65.000 km EZ 05/2018 . "
                 "VW Golf 7 15.500 € 72.000 km EZ 03/2018 . "
                 "VW Golf 17.200 € 60.000 km EZ 06/2018")},
    {"url": "https://b.de/passat", "title": "VW Passat B8",
     "content": "VW Passat Variant 21.900 € 60.000 km EZ 04/2018 . VW Passat 20.500 € 68.000 km EZ 05/2018"},
]
ma_g = analysiere_markt(WEB_G, ZIEL_G, 16500)
check("4: VW Passat (21.900/20.500) NICHT im Golf-Vergleich",
      21900 not in preise(ma_g) and 20500 not in preise(ma_g))
check("4: Golf-Preise verwendet", len(preise(ma_g)) >= 3 and max(preise(ma_g)) < 19000)

# ══ 5) Gültiges Zielmodell wird NICHT fälschlich verworfen ═══════════════════
check("5: reine Zielmodell-Daten liefern belastbaren Median (kein Over-Reject)",
      ma_ins.median_eur and ma_3.median_eur and ma_c.median_eur and ma_g.median_eur)

# ══ 6) modell_relevant: fachfremde Rechercheseiten aus der Quellenanzeige raus ══
# Realistischer: der DB-Bestand kennt weitere Modelle (4er, C-Klasse) als Fremdmodelle.
BMW_ALLE_BREIT = BMW_ALLE + [
    {"id": "bmw-4er-g22", "marke": "BMW", "modell": "4er", "generation": "G22"},
    {"id": "mb-c-w205", "marke": "Mercedes-Benz", "modell": "C-Klasse", "generation": "W205"},
]
ZIEL_3B = baue_ziel(BMW3, {"kraftstoff": "Diesel"},
                    SimpleNamespace(baujahr=2020, kilometerstand=70000, kraftstoff=None),
                    BMW_ALLE_BREIT, BMW_MOTOR)
check("6: 'BMW 4er' Seite ist im 3er-Check NICHT modell-relevant",
      modell_relevant({"title": "BMW 4er Reihe Gran Coupe gebraucht", "content": ""}, ZIEL_3B) is False)
check("6b: 'Mercedes C-Klasse' Seite ist im 3er-Check NICHT relevant",
      modell_relevant({"title": "Mercedes C-Klasse W205 Kaufberatung", "content": ""}, ZIEL_3B) is False)
check("6c: 'BMW 320d G20' Seite IST relevant",
      modell_relevant({"title": "BMW 320d G20 gebraucht kaufen", "content": ""}, ZIEL_3B) is True)
check("6d: neutrale Seite ohne klares Fremdsignal bleibt erhalten",
      modell_relevant({"title": "Gebrauchtwagen Preise Deutschland", "content": ""}, ZIEL_3B) is True)

# ══ 7) NUMERISCHE Modellnamen (marken-skopiert): 'BMW 520' != 'BMW 320d' ══════
check("7: ziel_num enthält '320', fremd_num enthält '520'",
      "320" in ZIEL_3["ziel_num"] and "520" in ZIEL_3["fremd_num"])
check("7b: '520' ist NICHT in ziel_num (nicht fälschlich Ziel)", "520" not in ZIEL_3["ziel_num"])
# Rechercheseite mit bloßer Zahl '520' (ohne 'd') wird als fremd erkannt
check("7c: 'BMW 520 aus 2018' Seite NICHT relevant (marken-skopierte Zahl)",
      modell_relevant({"title": "BMW 520 aus 2018 gebraucht kaufen AutoScout24", "content": ""}, ZIEL_3) is False)
check("7d: 'BMW 320 Limousine' (bloße Zielzahl) BLEIBT relevant",
      modell_relevant({"title": "BMW 320 Limousine gebraucht", "content": ""}, ZIEL_3) is True)
# Preis-Datenpunkt aus einem '520'-Umfeld wird im Vergleich hart verworfen
WEB_520 = [
    {"url": "https://a.de/3er", "title": "BMW 320d G20",
     "content": ("BMW 320d G20 29.000 € 65.000 km EZ 04/2020 . "
                 "BMW 320d 27.500 € 72.000 km EZ 06/2020 . "
                 "BMW 320d G20 28.200 € 68.000 km EZ 05/2020")},
    {"url": "https://b.de/520", "title": "BMW 520 Limousine",
     "content": "BMW 520 34.900 € 60.000 km EZ 03/2020 . BMW 520 33.500 € 66.000 km EZ 05/2020"},
]
ma_520 = analysiere_markt(WEB_520, ZIEL_3, 28000)
check("7e: bloße '520'-Preise (34.900/33.500) NICHT im 320d-Median",
      34900 not in preise(ma_520) and 33500 not in preise(ma_520))
# Marken-übergreifende Zahl darf NICHT fälschlich verwerfen (kein Peugeot-508-Fehlschluss)
check("7f: fremd_num ist marken-skopiert (nur BMW-intern, kein markenfremder Zahl-Fehlschluss)",
      modell_relevant({"title": "Peugeot 508 gebraucht", "content": "Peugeot 508 20.000 €"}, ZIEL_3) is True)

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER: " + ", ".join(FEHLER))
    raise SystemExit(1)
print("Alle Modelltreue-Tests bestanden.")
