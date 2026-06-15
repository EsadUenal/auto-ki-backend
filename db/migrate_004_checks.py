"""
Migration 004 — checks-Tabelle für gespeicherte Kauf-/Verkaufs-Checks.

Ausführen:
    python -m db.migrate_004_checks

Idempotent: läuft mehrfach ohne Fehler (IF NOT EXISTS).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.config import DB_PATH

SQL = """
CREATE TABLE IF NOT EXISTS checks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    typ         TEXT    NOT NULL CHECK(typ IN ('kauf','verkauf')),
    titel       TEXT    NOT NULL,
    eingabe     TEXT    NOT NULL,   -- JSON-Blob: Formulardaten
    ergebnis    TEXT    NOT NULL,   -- JSON-Blob: vollständiger Bericht
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_checks_user_id ON checks(user_id);
"""


def run() -> None:
    print(f"DB: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.executescript(SQL)
        conn.commit()
        print("Migration 004 — checks-Tabelle angelegt (oder bereits vorhanden).")
    finally:
        conn.close()


if __name__ == "__main__":
    run()
