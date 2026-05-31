"""
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
