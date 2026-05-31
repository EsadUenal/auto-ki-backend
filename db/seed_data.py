"""
Seed-Daten: BMW M4 F82 (2014-2020) und G82 (2021-heute).
Exakt nach Abschnitt 8 der Spezifikation.
Ausführen: python db/seed_data.py
"""

import sqlite3
import json
from pathlib import Path

DB_PATH = Path(__file__).parent / "auto_ki.db"


def seed(conn: sqlite3.Connection):
    # ------------------------------------------------------------------
    # EBENE 1: Baureihen
    # ------------------------------------------------------------------
    baureihen = [
        {
            "id": "bmw-m4-f82",
            "marke": "BMW",
            "modell": "M4",
            "generation": "F82",
            "bauzeitraum_von": 2014,
            "bauzeitraum_bis": 2020,
            "karosserie": json.dumps(["Coupé", "Cabrio (F83)"]),
            "segment": "Sportwagen-Coupé",
            "vorgaenger": "M3 Coupé (E92)",
            "erkennung_generation": (
                "Basiert auf 4er F32. Powerdome-Haube, breite Kotflügel, "
                "vier Endrohre (zwei Doppelrohre), CFK-Dach. "
                "KLEINE Niere — klarer Unterschied zum G82."
            ),
            "facelift_merkmale": "LCI 2017: LED-Scheinwerfer serienmäßig, OLED-Rückleuchten.",
            "adac_pannenkennziffer": None,   # nicht in Statistik (zu geringe Stückzahl)
            "tuev_maengelquote": None,        # nicht separat ausgewiesen; Zustand nutzungsabhängig
            "dekra_urteil": None,
            "euro_ncap_sterne": None,         # M4 nicht separat getestet
            "euro_ncap_jahr": None,
            "wartung_oel_km": 25000,
            "wartung_hu_intervall": "alle 2 Jahre",
            "kaufberatung": None,
            "letzte_aktualisierung": "2026-05",
        },
        {
            "id": "bmw-m4-g82",
            "marke": "BMW",
            "modell": "M4",
            "generation": "G82",
            "bauzeitraum_von": 2021,
            "bauzeitraum_bis": None,
            "karosserie": json.dumps(["Coupé", "Cabrio (G83)"]),
            "segment": "Sportwagen-Coupé",
            "vorgaenger": "M4 F82",
            "erkennung_generation": (
                "Basiert auf 4er G22. GROSSE, hochkant stehende Doppelniere "
                "(auffälligstes Merkmal!), schmale Scheinwerfer."
            ),
            "facelift_merkmale": "LCI 2024: neue Matrix-LED-Scheinwerfer, Curved Display im Innenraum.",
            "adac_pannenkennziffer": None,
            "tuev_maengelquote": None,        # zu jung für aussagekräftige HU-Statistik
            "dekra_urteil": None,
            "euro_ncap_sterne": None,
            "euro_ncap_jahr": None,
            "wartung_oel_km": None,
            "wartung_hu_intervall": "alle 2 Jahre (Neuwagen: erste HU nach 3 Jahren)",
            "kaufberatung": None,
            "letzte_aktualisierung": "2026-05",
        },
    ]

    conn.executemany(
        """INSERT OR REPLACE INTO baureihe
           (id, marke, modell, generation, bauzeitraum_von, bauzeitraum_bis,
            karosserie, segment, vorgaenger, erkennung_generation, facelift_merkmale,
            adac_pannenkennziffer, tuev_maengelquote, dekra_urteil,
            euro_ncap_sterne, euro_ncap_jahr,
            wartung_oel_km, wartung_hu_intervall, kaufberatung, letzte_aktualisierung)
           VALUES (:id,:marke,:modell,:generation,:bauzeitraum_von,:bauzeitraum_bis,
                   :karosserie,:segment,:vorgaenger,:erkennung_generation,:facelift_merkmale,
                   :adac_pannenkennziffer,:tuev_maengelquote,:dekra_urteil,
                   :euro_ncap_sterne,:euro_ncap_jahr,
                   :wartung_oel_km,:wartung_hu_intervall,:kaufberatung,:letzte_aktualisierung)""",
        baureihen,
    )

    # ------------------------------------------------------------------
    # Ausstattungslinien
    # ------------------------------------------------------------------
    ausstattungslinien = [
        # F82
        ("bmw-m4-f82", "M4 Basis", "Echtes M-Modell",
         "Serien-M-Optik", "NICHT mit 440i verwechseln (M-Sport-Optik aber B58, kein echter M)"),
        ("bmw-m4-f82", "M4 Competition", "Echtes M-Modell",
         "schwarze 666M-Felgen, Gurney-Heckspoiler, schwarze Embleme", None),
        ("bmw-m4-f82", "M4 CS (2017)", "Echtes M-Modell",
         "Sondermodell, Leichtbau", None),
        ("bmw-m4-f82", "M4 GTS (2016)", "Echtes M-Modell",
         "Wassereinspritzung, OLED, limitiert", None),
        # G82
        ("bmw-m4-g82", "M4 Basis", "Echtes M-Modell",
         "Serien-M-Optik", "NICHT mit M440i verwechseln (M-Sport-Optik aber B58)"),
        ("bmw-m4-g82", "M4 Competition", "Echtes M-Modell",
         "Competition-Embleme, größere Felgen", None),
        ("bmw-m4-g82", "M4 Competition xDrive", "Echtes M-Modell",
         "wie Competition, Allrad", None),
        ("bmw-m4-g82", "M4 CSL (2023)", "Echtes M-Modell",
         "limitiert, Leichtbau, gelbe Tagfahrlicht-Optik", None),
    ]
    conn.executemany(
        "INSERT INTO ausstattungslinie (baureihe_id,name,typ,optische_merkmale,abgrenzung) "
        "VALUES (?,?,?,?,?)",
        ausstattungslinien,
    )

    # ------------------------------------------------------------------
    # Schwachstellen Baureihe
    # ------------------------------------------------------------------
    schwachstellen_baureihe = [
        # F82
        ("bmw-m4-f82", "CFK-Dach Klarlack",
         "Klarlack blättert durch UV", "alle", "gering"),
        ("bmw-m4-f82", "Hinterachsträger-Buchse",
         "nur eine Gummibuchse, kann unter Lastwechseln reißen", "alle", "mittel"),
        ("bmw-m4-f82", "EDC-Dämpfer",
         "adaptive Dämpfer fallen durch Ölverlust/Elektrik aus", "alle", "mittel"),
        # G82
        ("bmw-m4-g82", "(allgemein)",
         "zu jung für breit dokumentierte Serienschwächen; vereinzelt Kühlsystem/Infotainment-Software gemeldet",
         "—", "gering"),
    ]
    conn.executemany(
        "INSERT INTO schwachstelle_baureihe (baureihe_id,bauteil,beschreibung,betroffene_baujahre,schweregrad) "
        "VALUES (?,?,?,?,?)",
        schwachstellen_baureihe,
    )

    # ------------------------------------------------------------------
    # Rückrufe
    # ------------------------------------------------------------------
    rueckrufe = [
        ("bmw-m4-f82", "2016", "2016-2017",
         "Verbindung Kardanwelle/Flansch kann versagen → Antriebsverlust",
         "kostenloser Kardanwellen-Tausch",
         "Herstelleraktion (auch Cabrio MY2017)"),
        # G82: keine eingetragenen Rückrufe (Web-Check empfohlen)
    ]
    conn.executemany(
        "INSERT INTO rueckruf (baureihe_id,datum,betroffene_baujahre,mangel,abhilfe,kba_referenz) "
        "VALUES (?,?,?,?,?,?)",
        rueckrufe,
    )

    # ------------------------------------------------------------------
    # EBENE 2: Motorvarianten
    # ------------------------------------------------------------------
    motorvarianten = [
        # ---- F82 Basis (S55, 431 PS) ----
        {
            "variante_id": "bmw-m4-f82-basis",
            "baureihe_id": "bmw-m4-f82",
            "bezeichnung": "M4",
            "motorcode": "S55B30",
            "kraftstoff": "Benzin",
            "hubraum_ccm": 2979,
            "zylinder": 6,
            "leistung_ps": 431,
            "leistung_kw": 317,
            "drehmoment_nm": 550,
            "getriebe": json.dumps(["6-Gang manuell", "7-Gang DKG"]),
            "antrieb": "Heck",
            "beschleunigung_0_100": 4.1,   # DKG; manuell 4.3
            "vmax_kmh": 250,
            "verbrauch_wltp": None,          # F82 unter NEFZ → kein WLTP
            "verbrauch_real": 11.5,          # ca., Spritmonitor
            "co2_g_km": 194,                 # ca. (NEFZ)
            "neupreis_ca_eur": 72000,        # ca. bei Markteinführung
            "heck_emblem": '"M4" Schriftzug + M4-Logo',
            "optische_unterscheidung": "vier Endrohre (zwei Doppelendrohre)",
        },
        # ---- F82 Competition (S55, 450 PS) ----
        {
            "variante_id": "bmw-m4-f82-competition",
            "baureihe_id": "bmw-m4-f82",
            "bezeichnung": "M4 Competition",
            "motorcode": "S55B30",
            "kraftstoff": "Benzin",
            "hubraum_ccm": 2979,
            "zylinder": 6,
            "leistung_ps": 450,
            "leistung_kw": 331,
            "drehmoment_nm": 550,
            "getriebe": json.dumps(["6-Gang manuell", "7-Gang DKG"]),
            "antrieb": "Heck",
            "beschleunigung_0_100": 4.0,
            "vmax_kmh": 250,
            "verbrauch_wltp": None,
            "verbrauch_real": None,
            "co2_g_km": None,
            "neupreis_ca_eur": None,
            "heck_emblem": None,
            "optische_unterscheidung": "schwarze 666M-Felgen, Gurney-Heckspoiler, schwarze Embleme",
        },
        # ---- G82 Basis (S58, 480 PS) ----
        {
            "variante_id": "bmw-m4-g82-basis",
            "baureihe_id": "bmw-m4-g82",
            "bezeichnung": "M4",
            "motorcode": "S58B30",
            "kraftstoff": "Benzin",
            "hubraum_ccm": 2993,
            "zylinder": 6,
            "leistung_ps": 480,
            "leistung_kw": 353,
            "drehmoment_nm": 550,
            "getriebe": json.dumps(["6-Gang manuell"]),
            "antrieb": "Heck",
            "beschleunigung_0_100": 4.2,
            "vmax_kmh": 250,
            "verbrauch_wltp": 9.6,           # ca.
            "verbrauch_real": 11.5,           # ca.
            "co2_g_km": 219,                  # ca. (WLTP)
            "neupreis_ca_eur": 86000,         # ca.
            "heck_emblem": None,
            "optische_unterscheidung": "vier Endrohre",
        },
        # ---- G82 Competition (S58, 510 PS, Heck) ----
        {
            "variante_id": "bmw-m4-g82-competition",
            "baureihe_id": "bmw-m4-g82",
            "bezeichnung": "M4 Competition",
            "motorcode": "S58B30",
            "kraftstoff": "Benzin",
            "hubraum_ccm": 2993,
            "zylinder": 6,
            "leistung_ps": 510,
            "leistung_kw": 375,
            "drehmoment_nm": 650,
            "getriebe": json.dumps(["8-Gang M Steptronic"]),
            "antrieb": "Heck",
            "beschleunigung_0_100": 3.9,
            "vmax_kmh": 250,                  # 290 mit M Driver's Package
            "verbrauch_wltp": 9.8,            # ca.
            "verbrauch_real": None,
            "co2_g_km": 224,                  # ca.
            "neupreis_ca_eur": 92000,         # ca.
            "heck_emblem": None,
            "optische_unterscheidung": None,
        },
        # ---- G82 Competition xDrive (S58, 510 PS, Allrad) ----
        {
            "variante_id": "bmw-m4-g82-competition-xdrive",
            "baureihe_id": "bmw-m4-g82",
            "bezeichnung": "M4 Competition xDrive",
            "motorcode": "S58B30",
            "kraftstoff": "Benzin",
            "hubraum_ccm": 2993,
            "zylinder": 6,
            "leistung_ps": 510,
            "leistung_kw": 375,
            "drehmoment_nm": 650,
            "getriebe": json.dumps(["8-Gang M Steptronic"]),
            "antrieb": "Allrad",
            "beschleunigung_0_100": 3.5,
            "vmax_kmh": 250,
            "verbrauch_wltp": None,
            "verbrauch_real": None,
            "co2_g_km": None,
            "neupreis_ca_eur": 97000,         # ca.
            "heck_emblem": None,
            "optische_unterscheidung": None,
        },
    ]
    conn.executemany(
        """INSERT OR REPLACE INTO motorvariante
           (variante_id, baureihe_id, bezeichnung, motorcode, kraftstoff,
            hubraum_ccm, zylinder, leistung_ps, leistung_kw, drehmoment_nm,
            getriebe, antrieb, beschleunigung_0_100, vmax_kmh,
            verbrauch_wltp, verbrauch_real, co2_g_km, neupreis_ca_eur,
            heck_emblem, optische_unterscheidung)
           VALUES (:variante_id,:baureihe_id,:bezeichnung,:motorcode,:kraftstoff,
                   :hubraum_ccm,:zylinder,:leistung_ps,:leistung_kw,:drehmoment_nm,
                   :getriebe,:antrieb,:beschleunigung_0_100,:vmax_kmh,
                   :verbrauch_wltp,:verbrauch_real,:co2_g_km,:neupreis_ca_eur,
                   :heck_emblem,:optische_unterscheidung)""",
        motorvarianten,
    )

    # ------------------------------------------------------------------
    # Schwachstellen Motor
    # ------------------------------------------------------------------
    schwachstellen_motor = [
        # S55 (F82 Basis + Competition — gleiche Schwächen)
        ("bmw-m4-f82-basis",
         "Kurbelnabe (crank hub)",
         "Pressverband ohne Passfeder kann durchrutschen → Steuerzeiten/Motorschaden; v.a. getunt & DKG",
         "v.a. 2014-2015", "präventiv 400-2500€, Motorschaden >10.000€"),
        ("bmw-m4-f82-basis",
         "Ventildeckel-/Ölfiltergehäusedichtung",
         "Öllecks ab ~60-80 tkm", "alle", "200-800€"),
        ("bmw-m4-f82-basis",
         "Charge-Pipe / J-Pipe",
         "Kunststoff reißt, Alu-Upgrade üblich", "alle", "800-1500€"),
        ("bmw-m4-f82-basis",
         "Pleuellager",
         "vorbeugender Wechsel diskutiert", "alle", "—"),
        # Competition teilt S55-Schwächen → gleiche Einträge
        ("bmw-m4-f82-competition",
         "Kurbelnabe (crank hub)",
         "Pressverband ohne Passfeder kann durchrutschen → Steuerzeiten/Motorschaden; v.a. getunt & DKG",
         "v.a. 2014-2015", "präventiv 400-2500€, Motorschaden >10.000€"),
        ("bmw-m4-f82-competition",
         "Ventildeckel-/Ölfiltergehäusedichtung",
         "Öllecks ab ~60-80 tkm", "alle", "200-800€"),
        ("bmw-m4-f82-competition",
         "Charge-Pipe / J-Pipe",
         "Kunststoff reißt, Alu-Upgrade üblich", "alle", "800-1500€"),
        ("bmw-m4-f82-competition",
         "Pleuellager",
         "vorbeugender Wechsel diskutiert", "alle", "—"),
        # S58 (G82)
        ("bmw-m4-g82-basis",
         "(S58 allgemein)",
         "closed-deck, gilt als robuster als S55; noch zu jung für breite Serienschwächen",
         "—", "—"),
    ]
    conn.executemany(
        "INSERT INTO schwachstelle_motor (variante_id,bauteil,beschreibung,baujahre,kosten_ca) "
        "VALUES (?,?,?,?,?)",
        schwachstellen_motor,
    )

    # ------------------------------------------------------------------
    # Kritische Wartung
    # ------------------------------------------------------------------
    kritische_wartung = [
        ("bmw-m4-f82-basis", "Kurbelnabe", "Zustand prüfen",
         "präventives Pinnen/Capture-Plate als 'cheap insurance'"),
        ("bmw-m4-f82-basis", "Pleuellager", "~50-80 tkm",
         "vorbeugender Wechsel empfohlen"),
        ("bmw-m4-f82-basis", "Wasserpumpe + Thermostat", "als Paar tauschen",
         "thermisch hoch belastet"),
        # Competition erbt gleiche Wartungspunkte
        ("bmw-m4-f82-competition", "Kurbelnabe", "Zustand prüfen",
         "präventives Pinnen/Capture-Plate als 'cheap insurance'"),
        ("bmw-m4-f82-competition", "Pleuellager", "~50-80 tkm",
         "vorbeugender Wechsel empfohlen"),
        ("bmw-m4-f82-competition", "Wasserpumpe + Thermostat", "als Paar tauschen",
         "thermisch hoch belastet"),
    ]
    conn.executemany(
        "INSERT INTO kritische_wartung (variante_id,bauteil,intervall,hinweis) VALUES (?,?,?,?)",
        kritische_wartung,
    )


def main():
    if not DB_PATH.exists():
        print("Fehler: Datenbank nicht gefunden. Zuerst init_db.py ausführen.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys=ON")

    # Idempotent: bestehende Seed-Daten zuerst löschen
    for table in [
        "kritische_wartung", "schwachstelle_motor",
        "rueckruf", "schwachstelle_baureihe", "ausstattungslinie", "quelle",
        "motorvariante", "baureihe",
    ]:
        conn.execute(f"DELETE FROM {table}")

    seed(conn)
    conn.commit()
    conn.close()

    # Kurze Zusammenfassung ausgeben
    conn = sqlite3.connect(DB_PATH)
    print("Eingepflegte Daten:")
    for table in ["baureihe", "ausstattungslinie", "motorvariante",
                  "schwachstelle_baureihe", "schwachstelle_motor",
                  "rueckruf", "kritische_wartung"]:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {count} Zeilen")
    conn.close()
    print("\nSeed OK.")


if __name__ == "__main__":
    main()
