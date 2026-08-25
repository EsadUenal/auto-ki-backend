"""
LEGACY/DEV — nicht der Produktionspfad.

Aus der Frühphase des Projekts, als es nur die Demo-Baureihen aus
`seed_data.py` gab. Seit dem Fahrzeugdaten-Bootstrap (app/fahrzeug_seed.py,
app/data_migrations.py) macht `app/database.py::ensure_tables()` das hier
automatisch beim App-Start — inklusive vollem, korrigiertem Fahrzeugbestand aus
`db/seed_fahrzeugdaten.sql` statt nur zwei Demo-Baureihen. Details:
db/README_bootstrap.md.

Bleibt für schnelle lokale Handarbeit nützlich (z.B. eine isolierte Test-DB ohne
Umweg über die App), ist aber kein zweiter Produktions-Bootstrap-Pfad.

Initialisiert SQLite-Datenbank und ChromaDB-Collections.
Ausführen: python db/init_db.py
"""

import sqlite3
from pathlib import Path
from vector_schema import get_client, get_collections

DB_PATH = Path(__file__).parent / "auto_ki.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_sqlite():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
    print(f"OK SQLite initialisiert: {DB_PATH}")


def init_chroma():
    client = get_client()
    cols = get_collections(client)
    for name, col in cols.items():
        print(f"OK ChromaDB Collection: '{name}' ({col.count()} Dokumente)")


if __name__ == "__main__":
    init_sqlite()
    init_chroma()
    print("\nDatenbank bereit.")
