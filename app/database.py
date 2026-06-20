from __future__ import annotations

import logging
import sqlite3
import json
from contextlib import contextmanager
from app.config import DB_PATH

log = logging.getLogger(__name__)

# ── Schema ────────────────────────────────────────────────────────────────────
# Alle CREATE TABLE IF NOT EXISTS hier gebündelt.
# ensure_tables() wird beim App-Start aufgerufen → Tabellen existieren IMMER,
# egal auf welche DB_PATH zeigt oder ob Migrationen vorher liefen.

_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    email               TEXT    UNIQUE NOT NULL,
    password_hash       TEXT    NOT NULL,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    abo_typ             TEXT    NOT NULL DEFAULT 'none'
                                CHECK(abo_typ IN ('none','light','pro','max')),
    checks_verbleibend  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT    NOT NULL DEFAULT 'Neuer Chat',
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations(user_id);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT    NOT NULL CHECK(role IN ('user','assistant')),
    content         TEXT    NOT NULL,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);

CREATE TABLE IF NOT EXISTS checks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    typ         TEXT    NOT NULL CHECK(typ IN ('kauf','verkauf')),
    titel       TEXT    NOT NULL,
    eingabe     TEXT    NOT NULL,
    ergebnis    TEXT    NOT NULL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_checks_user_id ON checks(user_id);

CREATE TABLE IF NOT EXISTS stripe_events (
    event_id     TEXT PRIMARY KEY,
    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Fügt neue Spalten zur users-Tabelle hinzu (idempotent, sicher mehrfach ausführbar)."""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "stripe_customer_id" not in existing:
        conn.execute("ALTER TABLE users ADD COLUMN stripe_customer_id TEXT")
    if "stripe_subscription_id" not in existing:
        conn.execute("ALTER TABLE users ADD COLUMN stripe_subscription_id TEXT")
    conn.commit()


def ensure_tables() -> None:
    """Erstellt beim App-Start alle Tabellen (idempotent, CREATE IF NOT EXISTS).
    Loggt den exakten DB-Pfad — so ist immer nachvollziehbar, welche Datei geöffnet wird."""
    db_path = str(DB_PATH)
    log.info("=== DB_PATH (aktiv): %s ===", db_path)
    print(f"[DB] Aktiver Pfad: {db_path}")   # auch ohne Log-Config sichtbar

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
        _migrate_schema(conn)
        tables = sorted(
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        )
        log.info("DB-Tabellen nach ensure_tables(): %s", tables)
        print(f"[DB] Tabellen: {tables}")
    finally:
        conn.close()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def _parse_json_field(value: str | None) -> list:
    if value is None:
        return []
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return [value]


def get_baureihe(marke: str, modell: str, generation: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM baureihe WHERE marke=? AND modell=? AND generation=?",
            (marke, modell, generation),
        ).fetchone()

        if row is None:
            return None

        result = dict(row)
        baureihe_id = result["id"]

        result["karosserie"] = _parse_json_field(result.get("karosserie"))

        result["ausstattungslinien"] = [
            dict(r) for r in conn.execute(
                "SELECT name,typ,optische_merkmale,abgrenzung FROM ausstattungslinie WHERE baureihe_id=?",
                (baureihe_id,),
            ).fetchall()
        ]

        result["schwachstellen_baureihe"] = [
            dict(r) for r in conn.execute(
                "SELECT bauteil,beschreibung,betroffene_baujahre,schweregrad "
                "FROM schwachstelle_baureihe WHERE baureihe_id=?",
                (baureihe_id,),
            ).fetchall()
        ]

        result["rueckrufe"] = [
            dict(r) for r in conn.execute(
                "SELECT datum,betroffene_baujahre,mangel,abhilfe,kba_referenz "
                "FROM rueckruf WHERE baureihe_id=?",
                (baureihe_id,),
            ).fetchall()
        ]

        result["quellen"] = [
            dict(r) for r in conn.execute(
                "SELECT quelle,url,abrufdatum FROM quelle WHERE baureihe_id=?",
                (baureihe_id,),
            ).fetchall()
        ]

        motoren_rows = conn.execute(
            "SELECT * FROM motorvariante WHERE baureihe_id=?",
            (baureihe_id,),
        ).fetchall()

        motoren = []
        for m in motoren_rows:
            motor = dict(m)
            motor["getriebe"] = _parse_json_field(motor.get("getriebe"))

            motor["schwachstellen_motor"] = [
                dict(r) for r in conn.execute(
                    "SELECT bauteil,beschreibung,baujahre,kosten_ca "
                    "FROM schwachstelle_motor WHERE variante_id=?",
                    (motor["variante_id"],),
                ).fetchall()
            ]

            motor["kritische_wartung"] = [
                dict(r) for r in conn.execute(
                    "SELECT bauteil,intervall,hinweis "
                    "FROM kritische_wartung WHERE variante_id=?",
                    (motor["variante_id"],),
                ).fetchall()
            ]

            motoren.append(motor)

        result["motoren"] = motoren
        return result


def search_baureihen(query_marke: str | None = None, query_modell: str | None = None) -> list[dict]:
    """Suche für Chat-Endpunkt: gibt kompakte Übersicht zurück."""
    with get_conn() as conn:
        sql = "SELECT id,marke,modell,generation,bauzeitraum_von,bauzeitraum_bis,segment FROM baureihe WHERE 1=1"
        params: list = []
        if query_marke:
            sql += " AND LOWER(marke)=LOWER(?)"
            params.append(query_marke)
        if query_modell:
            sql += " AND LOWER(modell)=LOWER(?)"
            params.append(query_modell)
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
