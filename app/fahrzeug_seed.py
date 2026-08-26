from __future__ import annotations

"""
Bootstrap des produktneutralen Fahrzeugdatenbestands.

DAS PROBLEM
-----------
Der Fahrzeugdatenbestand lag weder im Repository (`.gitignore`) noch im
Docker-Image (`.dockerignore`). Erschwerend: `app/database.py::_SCHEMA_SQL` legt
ausschliesslich die APP-Tabellen an — die Fahrzeugtabellen stehen in der
getrennten Datei `db/schema.sql`, die bisher nur der manuelle Aufruf
`python db/init_db.py` ausgefuehrt hat. Ein frischer Server hatte damit weder
Fahrzeugtabellen noch Fahrzeugdaten, und die P0-Datenmigration konnte nichts
korrigieren, weil es nichts zu korrigieren gab.

DIE LOESUNG
-----------
Zwei Schritte, beide idempotent, beide beim App-Start:

  ensure_fahrzeug_schema()  wendet `db/schema.sql` an. Die Datei benutzt
                            durchgehend CREATE TABLE/INDEX IF NOT EXISTS und ist
                            damit auf einer bestehenden Produktionsdatenbank ein
                            reines No-Op.

  seed_fahrzeugdaten()      laedt `db/seed_fahrzeugdaten.sql` — ABER NUR, wenn
                            der Fahrzeugbestand nachweislich leer ist.

DIE WICHTIGSTE ZUSICHERUNG
--------------------------
Auf einer bestehenden Datenbank wird NICHTS ueberschrieben. Der Seed laeuft
ausschliesslich, wenn ALLE Fahrzeugtabellen leer sind — nicht nur `baureihe`.
Ein halb befuellter Bestand (abgebrochener Import, laufende Pflege) fuehrt zum
Ueberspringen mit deutlicher Logmeldung, nicht zum Vermischen zweier Staende.

Nutzerdaten werden hier nie beruehrt: der Seed enthaelt ausschliesslich die acht
Tabellen aus `db/schema.sql`, und der Exporter leitet seine Allowlist genau
daraus ab (siehe db/export_fahrzeug_seed.py).

REIHENFOLGE BEIM START (app/database.py::ensure_tables)
------------------------------------------------------
    1. App-Schema            _SCHEMA_SQL
    2. Fahrzeug-Schema       db/schema.sql            <- hier
    3. Spalten-Migrationen   _migrate_schema          (ergaenzt baureihe.verification)
    4. App-Seeds             Poster / Ebook / chassis_codes
    5. Fahrzeug-Seed         db/seed_fahrzeugdaten.sql <- hier, nur wenn leer
    6. Datenmigrationen      app/data_migrations.py
    7. App bereit

Schritt 2 MUSS vor Schritt 3 stehen: `_migrate_schema` haengt `verification` an
`baureihe` an und braucht die Tabelle dafuer. Schritt 5 MUSS nach Schritt 3
stehen, weil der Seed diese Spalte mitliefert. Schritt 6 zuletzt, damit die
Korrekturen auf einem vorhandenen Bestand laufen.
"""

import logging
import os
import sqlite3

log = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(_BASE, "db", "schema.sql")
SEED_PATH = os.path.join(_BASE, "db", "seed_fahrzeugdaten.sql")

MARKER_SEED_V1 = "fahrzeug_seed_v1"

# Dieselben acht Tabellen, die db/schema.sql definiert und der Exporter
# ausliefert. Hier ausgeschrieben, damit der Leerheits-Test nicht davon abhaengt,
# dass die Schemadatei zur Laufzeit parsbar ist.
FAHRZEUGTABELLEN = (
    "baureihe", "motorvariante", "ausstattungslinie", "quelle",
    "rueckruf", "schwachstelle_baureihe", "schwachstelle_motor", "kritische_wartung",
    # Die kuratierten Einzelfakt-Verifikationen sind ebenfalls produktneutrale
    # Fahrzeugdaten und muessen einen frischen Server mit erreichen — sonst haette
    # der dort dieselben Fakten, aber keine Vertrauensstufen.
    "fakt_verifikation",
)


def ensure_fahrzeug_schema(conn: sqlite3.Connection) -> None:
    """Fahrzeugtabellen anlegen. Idempotent (CREATE ... IF NOT EXISTS)."""
    if not os.path.exists(SCHEMA_PATH):
        log.error("Fahrzeug-Schema fehlt: %s — Fahrzeugtabellen werden nicht angelegt.",
                  SCHEMA_PATH)
        return
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()


def _vorhandene_tabellen(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _bestand(conn: sqlite3.Connection) -> dict[str, int]:
    return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            for t in FAHRZEUGTABELLEN}


def _marker_gesetzt(conn: sqlite3.Connection, marker: str) -> bool:
    try:
        return conn.execute("SELECT 1 FROM schema_migrations WHERE name=?",
                            (marker,)).fetchone() is not None
    except sqlite3.Error:
        return False


def _statements(text: str):
    """Zerlegt eine SQL-Datei in vollstaendige Anweisungen.

    Bewusst ueber `sqlite3.complete_statement` statt ueber Zeilen: mindestens ein
    Textfeld im Bestand enthaelt einen Zeilenumbruch, ein zeilenweiser Split
    wuerde diese Anweisung zerreissen. `complete_statement` kennt die
    Quoting-Regeln und schliesst erst ab, wenn die Anweisung wirklich vollstaendig
    ist. `executescript` waere die bequemere Alternative, committet aber implizit
    und nimmt uns damit die Transaktionskontrolle.
    """
    puffer = ""
    for zeile in text.splitlines(keepends=True):
        if not puffer and (not zeile.strip() or zeile.lstrip().startswith("--")):
            continue
        puffer += zeile
        if sqlite3.complete_statement(puffer):
            yield puffer.strip()
            puffer = ""
    if puffer.strip():
        yield puffer.strip()


def seed_fahrzeugdaten(conn: sqlite3.Connection) -> bool:
    """Kanonischen Fahrzeug-Seed laden, wenn der Bestand leer ist.

    Rueckgabe: True, wenn tatsaechlich geseedet wurde.

    Der Marker wird auch dann gesetzt, wenn bereits Daten vorhanden waren — die
    Aufgabe "es gibt einen Grundbestand" ist dann ja erfuellt. NICHT gesetzt wird
    er, solange die Tabellen fehlen oder der Seed nicht geladen werden konnte.
    """
    if _marker_gesetzt(conn, MARKER_SEED_V1):
        return False

    fehlend = set(FAHRZEUGTABELLEN) - _vorhandene_tabellen(conn)
    if fehlend:
        log.warning("Fahrzeug-Seed uebersprungen: Tabellen fehlen (%s). Marker bleibt "
                    "ungesetzt, der naechste Start versucht es erneut.", sorted(fehlend))
        return False

    bestand = _bestand(conn)
    belegt = {t: n for t, n in bestand.items() if n}
    if belegt:
        # Bestehende Datenbank: nichts anfassen. Das ist der Normalfall in
        # Produktion und der wichtigste Schutz dieses Moduls.
        log.info("Fahrzeug-Seed nicht noetig — Bestand vorhanden (%s). Es wird nichts "
                 "importiert oder ueberschrieben.", belegt)
        conn.execute("INSERT INTO schema_migrations (name) VALUES (?)", (MARKER_SEED_V1,))
        conn.commit()
        return False

    if not os.path.exists(SEED_PATH):
        log.error("Fahrzeug-Seed fehlt: %s — die Datenbank bleibt ohne Fahrzeugdaten.",
                  SEED_PATH)
        return False

    with open(SEED_PATH, encoding="utf-8") as f:
        text = f.read()

    try:
        conn.execute("BEGIN")
        n = 0
        for stmt in _statements(text):
            conn.execute(stmt)
            n += 1
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        eigene = [r for r in fk if r[0] in FAHRZEUGTABELLEN]
        if eigene:
            raise RuntimeError(f"{len(eigene)} FK-Verletzung(en) im Seed: {eigene[:3]}")
        danach = _bestand(conn)
        if not danach.get("baureihe"):
            raise RuntimeError("Seed hat keine Baureihen erzeugt")
        conn.execute("INSERT INTO schema_migrations (name) VALUES (?)", (MARKER_SEED_V1,))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        log.error("Fahrzeug-Seed ABGEBROCHEN (%s) — Datenbank unveraendert, Marker nicht "
                  "gesetzt, naechster Start versucht es erneut.", exc)
        return False

    log.info("Fahrzeug-Seed geladen: %d Anweisungen, Bestand %s", n, danach)
    return True


def bootstrap_fahrzeugdaten(conn: sqlite3.Connection) -> None:
    """Schema + Seed in der richtigen Reihenfolge. Fuer den App-Start."""
    ensure_fahrzeug_schema(conn)
    seed_fahrzeugdaten(conn)