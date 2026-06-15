"""
Migration 002 — users-Tabelle für Login & Nutzerkonten (Phase 2b).

Ausführen:
    python -m db.migrate_002_users

Idempotent: läuft mehrfach ohne Fehler (IF NOT EXISTS).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.config import DB_PATH

SQL = """
CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    email               TEXT    UNIQUE NOT NULL,
    password_hash       TEXT    NOT NULL,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,

    -- Abo-Vorbereitung (Phase 2c — noch inaktiv)
    abo_typ             TEXT    NOT NULL DEFAULT 'none'
                                CHECK(abo_typ IN ('none','light','pro','max')),
    checks_verbleibend  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
"""


def run() -> None:
    print(f"DB: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SQL)
        conn.commit()
        print("Migration 002 — users-Tabelle angelegt (oder bereits vorhanden).")
    finally:
        conn.close()


if __name__ == "__main__":
    run()
