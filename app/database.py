from __future__ import annotations

import logging
import sqlite3
import json
import time
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

-- Kontextgebundene Analyse-Rückfragen (Q&A) pro gespeichertem Check. Bleibt an
-- den Check gekoppelt und wird beim erneuten Öffnen wiederhergestellt. Löscht der
-- Nutzer den Check, verschwinden die Fragen mit (ON DELETE CASCADE).
CREATE TABLE IF NOT EXISTS check_frage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    check_id    INTEGER NOT NULL REFERENCES checks(id) ON DELETE CASCADE,
    frage       TEXT    NOT NULL,
    antwort     TEXT    NOT NULL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_check_frage_check ON check_frage(check_id);

CREATE TABLE IF NOT EXISTS stripe_events (
    event_id     TEXT PRIMARY KEY,
    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Phase 5: VIRA Dealer — Fahrzeugakte pro Händler (Bestand/Beobachtung).
-- Ownership über user_id (CASCADE: Konto weg -> Fahrzeuge weg). Verknüpfte Checks
-- sind bewusst SET NULL: ein gelöschter Kauf-/Verkaufscheck darf den Händlerbestand
-- NICHT mitlöschen (die Fahrzeugakte bleibt, nur die Analyse-Verknüpfung entfällt).
CREATE TABLE IF NOT EXISTS dealer_vehicle (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kaufcheck_id                INTEGER REFERENCES checks(id) ON DELETE SET NULL,
    verkaufscheck_id            INTEGER REFERENCES checks(id) ON DELETE SET NULL,
    marke                       TEXT,
    modell                      TEXT,
    baureihe                    TEXT,
    motor                       TEXT,
    baujahr                     INTEGER,
    kilometerstand              INTEGER,
    status                      TEXT NOT NULL DEFAULT 'beobachtung'
                                     CHECK(status IN ('beobachtung','einkauf_geplant','im_bestand','verkauft')),
    einkaufspreis               INTEGER,
    nebenkosten                 INTEGER,
    geplanter_verkaufspreis     INTEGER,
    tatsaechlicher_verkaufspreis INTEGER,
    interne_notiz               TEXT,
    created_at                  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at                  DATETIME DEFAULT CURRENT_TIMESTAMP,
    sold_at                     DATETIME
);
CREATE INDEX IF NOT EXISTS idx_dealer_vehicle_user ON dealer_vehicle(user_id);
-- Ein Kaufcheck darf pro Händler höchstens EIN Fahrzeug erzeugen (kein Duplikat bei
-- mehrfachem "Zum Händlerbereich hinzufügen"). Partieller Index: NULL-kaufcheck_id
-- (manuell angelegte Fahrzeuge) sind davon ausgenommen -> beliebig viele erlaubt.
CREATE UNIQUE INDEX IF NOT EXISTS idx_dealer_vehicle_user_kaufcheck
    ON dealer_vehicle(user_id, kaufcheck_id) WHERE kaufcheck_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS schema_migrations (
    name        TEXT PRIMARY KEY,
    applied_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS poster (
    id           TEXT PRIMARY KEY,
    titel        TEXT NOT NULL,
    beschreibung TEXT NOT NULL DEFAULT '',
    preis_normal REAL NOT NULL,
    preis_abo    REAL NOT NULL,
    bildpfad     TEXT,
    aktiv        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS poster_bestellung (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    poster_id                TEXT    NOT NULL REFERENCES poster(id),
    preis_bezahlt            REAL    NOT NULL,
    stripe_session_id        TEXT    UNIQUE NOT NULL,
    stripe_payment_intent_id TEXT,
    status                   TEXT    NOT NULL DEFAULT 'offen'
                                     CHECK(status IN ('offen','bezahlt','versendet','storniert','erstattet')),
    paid_at                  DATETIME,
    created_at               DATETIME DEFAULT CURRENT_TIMESTAMP,
    adresse_name             TEXT    NOT NULL,
    adresse_strasse          TEXT    NOT NULL,
    adresse_plz              TEXT    NOT NULL,
    adresse_ort              TEXT    NOT NULL,
    adresse_land             TEXT    NOT NULL DEFAULT 'DE'
);
CREATE INDEX IF NOT EXISTS idx_poster_bestellung_user ON poster_bestellung(user_id);
CREATE INDEX IF NOT EXISTS idx_poster_bestellung_session ON poster_bestellung(stripe_session_id);

CREATE TABLE IF NOT EXISTS gespeicherte_adresse (
    user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    strasse    TEXT NOT NULL,
    plz        TEXT NOT NULL,
    ort        TEXT NOT NULL,
    land       TEXT NOT NULL DEFAULT 'DE',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ebook (
    id           TEXT PRIMARY KEY,
    titel        TEXT NOT NULL,
    untertitel   TEXT NOT NULL DEFAULT '',
    beschreibung TEXT NOT NULL DEFAULT '',
    zielgruppe   TEXT NOT NULL DEFAULT '',
    preis_normal REAL NOT NULL,
    preis_abo    REAL NOT NULL,
    aktiv        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS ebook_bestellung (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ebook_id                 TEXT    NOT NULL REFERENCES ebook(id),
    preis_bezahlt            REAL    NOT NULL,
    stripe_session_id        TEXT    UNIQUE NOT NULL,
    stripe_payment_intent_id TEXT,
    status                   TEXT    NOT NULL DEFAULT 'offen'
                                     CHECK(status IN ('offen','bezahlt','storniert','erstattet')),
    paid_at                  DATETIME,
    created_at               DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ebook_bestellung_user    ON ebook_bestellung(user_id);
CREATE INDEX IF NOT EXISTS idx_ebook_bestellung_session ON ebook_bestellung(stripe_session_id);

-- Nachweis erteilter Einwilligungen (AGB/Datenschutz-Zustimmung, Widerrufs-Verzicht
-- bei sofortiger Ausfuehrung). Append-only-Audit-Log: pro Zustimmung eine Zeile mit
-- Zeitstempel und Kontext (Registrierung / konkreter Kauf) — belastbarer Nachweis.
CREATE TABLE IF NOT EXISTS einwilligung (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER REFERENCES users(id) ON DELETE CASCADE,
    art           TEXT    NOT NULL,   -- 'agb_datenschutz' | 'widerruf_verzicht'
    kontext       TEXT    NOT NULL,   -- 'registrierung' | 'ebook:<id>' | 'abo:<typ>' | 'einzelkauf'
    akzeptiert_am DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_einwilligung_user ON einwilligung(user_id);
"""


_POSTER_SEED = [
    ("bmw-m3",              "BMW M3 — Iconic Stance",            "Minimalistisches Kunstposter des BMW M3 in klassischer Seitenansicht. Hochformat, Druckqualität 300 dpi.",                          24.99, 19.99),
    ("porsche-911",         "Porsche 911 — Timeless",            "Das ikonische Silhouetten-Poster des Porsche 911. Schlichte Linien, maximale Wirkung.",                                             24.99, 19.99),
    ("mercedes-amg-gt",     "Mercedes-AMG GT — Black Series",    "Dramatische Frontalansicht des AMG GT Black Series in Studiobeleuchtung.",                                                          29.99, 23.99),
    ("audi-r8",             "Audi R8 — Quattro Legend",          "Der R8 in einer dramatischen Dreiviertelansicht. Perfekt für Technik-Enthusiasten.",                                               24.99, 19.99),
    ("lamborghini-huracan", "Lamborghini Huracán — Fire & Form", "Futuristisches Design-Poster mit dem Huracán als skulpturales Objekt. Limitierte Auflage.",                                       34.99, 27.99),
    ("ferrari-488",         "Ferrari 488 — Rosso Corsa",         "Ferrari 488 in Rosso Corsa auf neutralem Hintergrund. Klassisches Rennfoto-Feeling.",                                             34.99, 27.99),
    ("mclaren-720s",        "McLaren 720S — Speed Art",          "Aerodynamische Formsprache des 720S in abstrakter Darstellung. Für moderne Wände.",                                               29.99, 23.99),
    ("nissan-gtr",          "Nissan GT-R — Godzilla",            "Der legendäre GT-R in nächtlicher Kulisse. Neonakzente treffen Motorsport-Erbe.",                                                 22.99, 17.99),
]


_EBOOK_SEED = [
    (
        "kauf-kein-risiko",
        "Kauf kein Risiko",
        "Der ehrliche Gebrauchtwagen-Guide",
        "Alles was du wissen musst, bevor du einen Gebrauchtwagen kaufst: Worauf achten, wie verhandeln, welche Fallen es gibt — direkt, ehrlich, ohne Blatt vor dem Mund.",
        "Gebrauchtwagenkäufer",
        17.99, 16.19,
    ),
    (
        "dein-erstes-auto",
        "Dein erstes Auto",
        "Der ehrliche Ratgeber für Erstkäufer",
        "Der Ratgeber für alle, die ihr erstes Auto kaufen: Budget planen, richtige Wahl treffen, typische Anfängerfehler vermeiden — verständlich erklärt.",
        "Erstkäufer 18–30",
        14.99, 13.49,
    ),
    (
        "elektro-oder-verbrenner",
        "Elektro oder Verbrenner?",
        "Die ehrliche Entscheidungshilfe",
        "Kein Marketing, keine Ideologie: Eine sachliche Analyse was Elektro und Verbrenner im Alltag wirklich bedeuten — Kosten, Reichweite, Ladeinfrastruktur, Restwert.",
        "Alle Autokäufer 2026",
        19.99, 17.99,
    ),
]


def _seed_ebook(conn: sqlite3.Connection) -> None:
    """Füllt ebook-Tabelle mit Initialdaten (nur wenn leer)."""
    if conn.execute("SELECT COUNT(*) FROM ebook").fetchone()[0] > 0:
        return
    conn.executemany(
        "INSERT INTO ebook (id, titel, untertitel, beschreibung, zielgruppe, preis_normal, preis_abo) VALUES (?,?,?,?,?,?,?)",
        _EBOOK_SEED,
    )
    conn.commit()
    log.info("Ebook-Seed: %d Einträge angelegt.", len(_EBOOK_SEED))


def _seed_poster(conn: sqlite3.Connection) -> None:
    """Füllt poster-Tabelle mit Initialdaten (nur wenn leer)."""
    if conn.execute("SELECT COUNT(*) FROM poster").fetchone()[0] > 0:
        return
    conn.executemany(
        "INSERT INTO poster (id, titel, beschreibung, preis_normal, preis_abo) VALUES (?,?,?,?,?)",
        _POSTER_SEED,
    )
    conn.commit()
    log.info("Poster-Seed: %d Einträge angelegt.", len(_POSTER_SEED))


_MOTORVARIANTE_NEUE_SPALTEN = {
    # Standard-Fahrzeugdaten, nach denen Nutzer häufig fragen (Phase 1 Wissensqualität).
    # Auf motorvariante-Ebene, da Tankgröße/Kofferraum/Anhängelast oft je Motorisierung
    # (Kraftstoffart, Antrieb, PHEV-Batterie) variieren — nicht nur je Baureihe.
    "tankgroesse_liter":          "INTEGER",
    "kofferraum_liter":           "INTEGER",
    "batteriekapazitaet_kwh":     "REAL",     # nur BEV/PHEV, sonst NULL
    "anhaengelast_gebremst_kg":   "INTEGER",
    "anhaengelast_ungebremst_kg": "INTEGER",
    "abgasnorm":                  "TEXT",     # z.B. "Euro 6d-ISC-FCM"
    "felgengroesse_serie":        "TEXT",     # z.B. "17 Zoll (Serie), bis 20 Zoll optional"
}


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Fügt neue Spalten zur users-Tabelle hinzu (idempotent, sicher mehrfach ausführbar)."""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "stripe_customer_id" not in existing:
        conn.execute("ALTER TABLE users ADD COLUMN stripe_customer_id TEXT")
    if "stripe_subscription_id" not in existing:
        conn.execute("ALTER TABLE users ADD COLUMN stripe_subscription_id TEXT")
    if "deleted_at" not in existing:
        conn.execute("ALTER TABLE users ADD COLUMN deleted_at DATETIME")
    if "abo_kuendigt_zum" not in existing:
        conn.execute("ALTER TABLE users ADD COLUMN abo_kuendigt_zum TEXT")
    if "ist_haendler" not in existing:
        # Phase 5: Dealer-Berechtigung (orthogonal zum abo_typ/Check-Kontingent).
        # DEFAULT 0 -> alle Bestandsnutzer bleiben normale Kunden; Freischaltung
        # erfolgt gezielt (z.B. Admin/Stripe-Händlertarif später).
        conn.execute("ALTER TABLE users ADD COLUMN ist_haendler INTEGER NOT NULL DEFAULT 0")
    if "ersatzteil_suchen_verbleibend" not in existing:
        # DEFAULT 1 gilt auch für bestehende Zeilen → 1 Gratis-Suche für alle Bestandsnutzer.
        # Bestehende Abo-Kunden (light/pro) werden unten per Backfill auf ihr echtes Kontingent gehoben,
        # da für sie kein neues Stripe-Event feuert, das den Wert sonst setzen würde.
        conn.execute("ALTER TABLE users ADD COLUMN ersatzteil_suchen_verbleibend INTEGER NOT NULL DEFAULT 1")

    # Einmaliger Backfill: bestehende Abo-Kunden bekamen durch obigen DEFAULT 1 fälschlich
    # nur 1 statt ihres Abo-Kontingents (light=5, pro=20, max=unbegrenzt). Läuft nur einmal
    # (Marker in schema_migrations), damit spätere manuelle Anpassungen nicht überschrieben werden.
    already_ran = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name='ersatzteil_quota_backfill'"
    ).fetchone()
    if not already_ran:
        conn.execute("UPDATE users SET ersatzteil_suchen_verbleibend=5  WHERE abo_typ='light'")
        conn.execute("UPDATE users SET ersatzteil_suchen_verbleibend=20 WHERE abo_typ='pro'")
        conn.execute("UPDATE users SET ersatzteil_suchen_verbleibend=0  WHERE abo_typ='max'")
        conn.execute("INSERT INTO schema_migrations (name) VALUES ('ersatzteil_quota_backfill')")
        log.info("Ersatzteil-Quota-Backfill für bestehende Abo-Kunden ausgeführt.")

    # motorvariante-Tabelle existiert nicht in _SCHEMA_SQL (wird separat via db/schema.sql
    # angelegt) — Migration daher defensiv nur ausführen wenn die Tabelle bereits existiert.
    mv_table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='motorvariante'"
    ).fetchone()
    if mv_table_exists:
        mv_existing = {r[1] for r in conn.execute("PRAGMA table_info(motorvariante)").fetchall()}
        for spalte, sql_typ in _MOTORVARIANTE_NEUE_SPALTEN.items():
            if spalte not in mv_existing:
                conn.execute(f"ALTER TABLE motorvariante ADD COLUMN {spalte} {sql_typ}")

    conn.commit()


def ensure_tables() -> None:
    """Erstellt beim App-Start alle Tabellen (idempotent, CREATE IF NOT EXISTS).
    Loggt den exakten DB-Pfad — so ist immer nachvollziehbar, welche Datei geöffnet wird."""
    db_path = str(DB_PATH)
    log.info("=== DB_PATH (aktiv): %s ===", db_path)
    print(f"[DB] Aktiver Pfad: {db_path}")   # auch ohne Log-Config sichtbar

    # Schritt 1: Schema via executescript (eigene Connection, danach schließen)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()

    # Schritt 2: Spalten-Migration + Seed in FRISCHER Connection (vermeidet sqlite3-Modul-Bug
    # nach executescript, bei dem DDL-Statements in derselben Connection ignoriert werden)
    conn2 = sqlite3.connect(db_path)
    try:
        _migrate_schema(conn2)
        _seed_poster(conn2)
        _seed_ebook(conn2)
        tables = sorted(
            r[0] for r in conn2.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        )
        log.info("DB-Tabellen nach ensure_tables(): %s", tables)
        print(f"[DB] Tabellen: {tables}")
    finally:
        conn2.close()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    # WAL: mehrere gleichzeitige Leser + ein Schreiber, statt sich gegenseitig zu blockieren.
    # busy_timeout: bei kurzzeitiger Schreibsperre (paralleler Request) automatisch retry
    # statt sofortigem "database is locked" — wichtig bei vielen gleichzeitigen Nutzern.
    # Beide PRAGMAs sind idempotent (kein Effekt wenn bereits gesetzt).
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
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

        # Schwachstellen/Wartung für ALLE Motorvarianten dieser Baureihe in je EINER
        # Abfrage statt 2 Abfragen PRO Motor (vorher z.B. 16 Extra-Queries bei 8
        # Motorvarianten) — gleiche Daten, nur in Python nach variante_id gruppiert.
        variante_ids = [m["variante_id"] for m in motoren_rows]
        schwachstellen_by_variante: dict[str, list[dict]] = {}
        wartung_by_variante: dict[str, list[dict]] = {}
        if variante_ids:
            platzhalter = ",".join("?" * len(variante_ids))
            for r in conn.execute(
                f"SELECT variante_id,bauteil,beschreibung,baujahre,kosten_ca "
                f"FROM schwachstelle_motor WHERE variante_id IN ({platzhalter})",
                variante_ids,
            ).fetchall():
                d = dict(r)
                vid = d.pop("variante_id")
                schwachstellen_by_variante.setdefault(vid, []).append(d)
            for r in conn.execute(
                f"SELECT variante_id,bauteil,intervall,hinweis "
                f"FROM kritische_wartung WHERE variante_id IN ({platzhalter})",
                variante_ids,
            ).fetchall():
                d = dict(r)
                vid = d.pop("variante_id")
                wartung_by_variante.setdefault(vid, []).append(d)

        motoren = []
        for m in motoren_rows:
            motor = dict(m)
            motor["getriebe"] = _parse_json_field(motor.get("getriebe"))
            motor["schwachstellen_motor"] = schwachstellen_by_variante.get(motor["variante_id"], [])
            motor["kritische_wartung"] = wartung_by_variante.get(motor["variante_id"], [])
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


# ── Kurzlebiger In-Memory-Cache für Referenzdaten (Performance) ─────────────────
# Fahrzeugerkennung (Chat: llm._suche_baureihen_in_text, Kauf-/Verkaufscheck:
# car_lookup.find_baureihe) liest bei JEDER Anfrage die KOMPLETTE baureihe- und
# motorvariante-Tabelle, um lokal (in Python) zu matchen/scoren. Bei Mehrfahrzeug-
# Nachrichten (mehrere Text-Segmente) passierte das sogar mehrfach PRO Request.
# Diese Tabellen ändern sich nur über die Admin-Oberfläche (selten, nie während
# eines normalen Chat-/Check-Requests) — ein kurzes TTL von 60s spart die
# wiederholten Full-Table-Scans + Connection-Overhead, ohne dass Nutzer je einen
# veralteten Stand sehen (Admin-Schreibvorgänge rufen zusätzlich sofort
# invalidate_referenzdaten_cache() auf, siehe app/routers/admin.py).
_REF_CACHE_TTL_S = 60.0
_ref_cache: dict[str, tuple[float, list[dict]]] = {}


def _cached_alle(key: str, sql: str) -> list[dict]:
    now = time.monotonic()
    eintrag = _ref_cache.get(key)
    if eintrag is not None and (now - eintrag[0]) < _REF_CACHE_TTL_S:
        return eintrag[1]
    with get_conn() as conn:
        daten = [dict(r) for r in conn.execute(sql).fetchall()]
    _ref_cache[key] = (now, daten)
    return daten


def get_alle_baureihen_kurz() -> list[dict]:
    """id,marke,modell,generation,bauzeitraum_von,bauzeitraum_bis für ALLE Baureihen —
    gecacht (siehe oben), identische Spalten wie die bisherigen Direktabfragen in
    llm._suche_baureihen_in_text() und car_lookup.find_baureihe()."""
    return _cached_alle(
        "baureihen",
        "SELECT id,marke,modell,generation,bauzeitraum_von,bauzeitraum_bis FROM baureihe",
    )


def get_alle_motorvarianten_kurz() -> list[dict]:
    """baureihe_id,bezeichnung,motorcode für ALLE Motorvarianten — gecacht (siehe oben)."""
    return _cached_alle(
        "motorvarianten",
        "SELECT baureihe_id, bezeichnung, motorcode FROM motorvariante",
    )


def invalidate_referenzdaten_cache() -> None:
    """Nach Admin-Schreibvorgängen (neue/geänderte Baureihe) aufrufen, damit die
    Fahrzeugerkennung sofort den aktuellen Stand sieht statt bis zu 60s zu warten."""
    _ref_cache.clear()
