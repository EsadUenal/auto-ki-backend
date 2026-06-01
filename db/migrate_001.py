"""
Migration 001: bauzeitraum_von und karosserie NOT NULL entfernen.
SQLite unterstützt kein ALTER COLUMN → Tabelle neu aufbauen.
Einmalig ausführen: python db/migrate_001.py
"""
import sqlite3
from pathlib import Path

DB = Path(__file__).parent / "auto_ki.db"
conn = sqlite3.connect(DB)

conn.executescript("""
PRAGMA foreign_keys=OFF;

CREATE TABLE IF NOT EXISTS baureihe_new (
    id                      TEXT PRIMARY KEY,
    marke                   TEXT NOT NULL,
    modell                  TEXT NOT NULL,
    generation              TEXT NOT NULL,
    bauzeitraum_von         INTEGER,
    bauzeitraum_bis         INTEGER,
    karosserie              TEXT,
    segment                 TEXT,
    vorgaenger              TEXT,
    erkennung_generation    TEXT,
    facelift_merkmale       TEXT,
    adac_pannenkennziffer   TEXT,
    tuev_maengelquote       TEXT,
    dekra_urteil            TEXT,
    euro_ncap_sterne        INTEGER,
    euro_ncap_jahr          INTEGER,
    wartung_oel_km          INTEGER,
    wartung_hu_intervall    TEXT,
    kaufberatung            TEXT,
    letzte_aktualisierung   TEXT NOT NULL
);

INSERT INTO baureihe_new SELECT * FROM baureihe;
DROP TABLE baureihe;
ALTER TABLE baureihe_new RENAME TO baureihe;

CREATE INDEX IF NOT EXISTS idx_baureihe_marke_modell ON baureihe(marke, modell);
CREATE INDEX IF NOT EXISTS idx_baureihe_generation   ON baureihe(generation);

PRAGMA foreign_keys=ON;
""")
conn.commit()
conn.close()
print("Migration 001 OK.")
