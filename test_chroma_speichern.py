"""
Test: Speichern in SQLite + ChromaDB — prüft Eindeutigkeit und Idempotenz.

Szenarien:
  1. Mercedes S-Klasse W116 (war crashend wegen doppelter Chroma-IDs)
  2. BMW 3er E36 (viele Motoren, motorcode=null bei mehreren Varianten)
  3. Erneutes Speichern beider Autos — muss ohne Fehler klappen (upsert)
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from app.db_writer import save_fahrzeug

# ── Hilfsfunktion ──────────────────────────────────────────────────────────────

def _motor(bezeichnung, motorcode=None, ps=None, kraftstoff="Benzin",
           sw_bauteile=None):
    """Minimal-Motor-Dict für Tests."""
    schwachstellen = []
    for b in (sw_bauteile or []):
        schwachstellen.append({
            "bauteil": b, "beschreibung": f"Bekanntes Problem: {b}",
            "baujahre": None, "kosten_ca": None,
        })
    return {
        "bezeichnung": bezeichnung,
        "motorcode": motorcode,
        "kraftstoff": kraftstoff,
        "leistung_ps": ps,
        "leistung_kw": round(ps / 1.36) if ps else None,
        "hubraum_ccm": None, "zylinder": None, "drehmoment_nm": None,
        "getriebe": ["Schaltgetriebe"], "antrieb": "Heck",
        "beschleunigung_0_100": None, "vmax_kmh": None,
        "verbrauch_wltp": None, "verbrauch_real": None, "co2_g_km": None,
        "neupreis_ca_eur": None, "heck_emblem": None, "optische_unterscheidung": None,
        "schwachstellen_motor": schwachstellen,
        "kritische_wartung": [],
    }


# ── Testdaten ──────────────────────────────────────────────────────────────────

W116 = {
    "marke": "Mercedes-Benz", "modell": "S-Klasse", "generation": "W116",
    "id": "mercedes-benz-s-klasse-w116",
    "bauzeitraum_von": 1972, "bauzeitraum_bis": 1980,
    "karosserie": ["Limousine"], "segment": "Oberklasse",
    "vorgaenger": "W108", "erkennung_generation":
        "Klassische Stufenheck-Limousine mit langer Motorhaube, verchromten "
        "Stoßfängern und markanten Rechteck-Scheinwerfern. Erkennbar an der "
        "hohen Gürtellinie und dem breiten C-Säulen-Dreiecksfenster.",
    "facelift_merkmale": None,
    "adac_pannenkennziffer": None, "tuev_maengelquote": None,
    "dekra_urteil": None, "euro_ncap_sterne": None, "euro_ncap_jahr": None,
    "wartung_oel_km": 10000, "wartung_hu_intervall": "2 Jahre",
    "kaufberatung": None, "letzte_aktualisierung": "2025-06",
    "ausstattungslinien": [
        {"name": "280 S", "typ": "Basis",
         "optische_merkmale": "Einfache Chromleisten, schmale Reifen",
         "abgrenzung": None},
        {"name": "450 SEL 6.9", "typ": "Ausstattungslinie",
         "optische_merkmale": "Tieferlegung, breitere Spur, Spiegelkappen in Wagenfarbe",
         "abgrenzung": "Hydropneumatik-Federung"},
    ],
    "schwachstellen_baureihe": [
        {"bauteil": "Unterbodenschutz", "beschreibung": "Korrosion am Unterboden",
         "betroffene_baujahre": "1972–1976", "schweregrad": "mittel"},
        {"bauteil": "Einspritzanlage", "beschreibung": "Bosch D-Jetronic altert",
         "betroffene_baujahre": "alle", "schweregrad": "gering"},
    ],
    "rueckrufe": [],
    "quellen": [],
    "hinweise": {},
    # Motoren mit NULL-Motorcode → war Quelle der Duplikate
    "motoren": [
        _motor("280 S",    motorcode=None,  ps=160,
               sw_bauteile=["Nockenwelle", "Vergaser"]),
        _motor("280 SE",   motorcode=None,  ps=185,
               sw_bauteile=["Nockenwelle", "Einspritzpumpe"]),
        _motor("350 SE",   motorcode=None,  ps=200,
               sw_bauteile=["Nockenwelle"]),
        _motor("450 SE",   motorcode=None,  ps=225,
               sw_bauteile=["Nockenwelle", "Ölpumpe"]),
        _motor("450 SEL",  motorcode=None,  ps=225,
               sw_bauteile=["Nockenwelle", "Ölpumpe"]),
        _motor("450 SEL 6.9", motorcode=None, ps=286,
               sw_bauteile=["Hydropneumatik", "Ölpumpe"]),
    ],
}

E36 = {
    "marke": "BMW", "modell": "3er", "generation": "E36",
    "id": "bmw-3er-e36",
    "bauzeitraum_von": 1990, "bauzeitraum_bis": 2000,
    "karosserie": ["Limousine", "Coupe", "Cabrio", "Touring", "Compact"],
    "segment": "Mittelklasse",
    "vorgaenger": "E30",
    "erkennung_generation":
        "Runde, aerodynamische Karosserie mit fließenden Linien. Klappscheinwerfer "
        "entfallen, stattdessen feste Einfach-Scheinwerfer. Breitere Spur als E30.",
    "facelift_merkmale": "1996: Neue Scheinwerfer, überarbeitete Stoßfänger",
    "adac_pannenkennziffer": None, "tuev_maengelquote": None,
    "dekra_urteil": None, "euro_ncap_sterne": None, "euro_ncap_jahr": None,
    "wartung_oel_km": 15000, "wartung_hu_intervall": "2 Jahre",
    "kaufberatung": None, "letzte_aktualisierung": "2025-06",
    "ausstattungslinien": [],
    "schwachstellen_baureihe": [
        {"bauteil": "Kühlsystem",
         "beschreibung": "Plastik-Kühlmittelflansch bricht häufig",
         "betroffene_baujahre": "alle", "schweregrad": "hoch"},
    ],
    "rueckrufe": [],
    "quellen": [],
    "hinweise": {},
    # Viele Motoren mit motorcode=None → typischer Kollisionsfall
    "motoren": [
        _motor("316i", motorcode=None, ps=102,
               sw_bauteile=["Kühlmittelflansch"]),
        _motor("318i", motorcode=None, ps=118,
               sw_bauteile=["Kühlmittelflansch", "Vanos"]),
        _motor("320i", motorcode=None, ps=150,
               sw_bauteile=["Kühlmittelflansch"]),
        _motor("323i", motorcode=None, ps=170,
               sw_bauteile=["Kühlmittelflansch", "Vanos"]),
        _motor("325i", motorcode=None, ps=192,
               sw_bauteile=["Kühlmittelflansch"]),
        _motor("328i", motorcode=None, ps=193,
               sw_bauteile=["Kühlmittelflansch"]),
        _motor("316g",  motorcode=None, ps=102, kraftstoff="Benzin",
               sw_bauteile=["Gasanlage"]),
        _motor("318tds", motorcode=None, ps=90, kraftstoff="Diesel",
               sw_bauteile=["Einspritzpumpe"]),
        _motor("325tds", motorcode=None, ps=143, kraftstoff="Diesel",
               sw_bauteile=["Einspritzpumpe"]),
        _motor("M3",    motorcode="S50", ps=286,
               sw_bauteile=["Vanos", "Lagerringe"]),
    ],
}


# ── Tests ──────────────────────────────────────────────────────────────────────

def run(label: str, fn):
    print(f"\n  {label} ...", end=" ", flush=True)
    try:
        result = fn()
        print(f"OK  → {result}")
        return True
    except Exception as e:
        print(f"FEHLER: {type(e).__name__}: {e}")
        return False


print("=" * 60)
print("TEST: ChromaDB-Speichern — Eindeutigkeit und Idempotenz")
print("=" * 60)

results = []

# Erster Speichervorgang
results.append(run("W116 erstmals speichern",     lambda: save_fahrzeug(W116)))
results.append(run("E36  erstmals speichern",     lambda: save_fahrzeug(E36)))

# Zweiter Speichervorgang (upsert-Test)
results.append(run("W116 erneut speichern (upsert-Test)", lambda: save_fahrzeug(W116)))
results.append(run("E36  erneut speichern (upsert-Test)", lambda: save_fahrzeug(E36)))

# Drittes Speichern (dreifach = sicher)
results.append(run("W116 drittes Speichern",      lambda: save_fahrzeug(W116)))

print(f"\n{'='*60}")
ok = sum(results)
print(f"  {ok}/{len(results)} Tests bestanden")
if ok == len(results):
    print("  Alle OK — ChromaDB-IDs eindeutig, upsert funktioniert.")
else:
    print("  ACHTUNG: Mindestens ein Test fehlgeschlagen.")
print("=" * 60)
