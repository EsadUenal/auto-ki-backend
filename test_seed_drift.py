# -*- coding: utf-8 -*-
"""
SEED-DRIFT-GUARD — letzter Reproduzierbarkeits-Schutz.
KEIN Netzwerk, KEIN LLM-Call, KEIN Tavily.

Beweist:

  A/B/C) db/seed_fahrzeugdaten.sql in eine frische DB laden, den normalen
         Fahrzeug-Bootstrap + die Datenmigrationen (app/data_migrations.py)
         ausfuehren — danach darf KEINE Migration mehr eine fachliche
         Datenzeile veraendert haben. Seed-Zustand == Seed + Migrationen,
         fuer jede Tabelle der Fahrzeug-Allowlist. `schema_migrations`
         selbst ist keine Fahrzeugtabelle und wird nicht verglichen.

  Gegenprobe) Der Vergleich muss einen ECHTEN Fehler auch erkennen koennen.
         Dafuer wird NICHT die echte Seed-Datei veraendert, sondern eine
         isolierte Kopie nach dem Seed-Laden gezielt "gealtert" (ein Feld auf
         den Vor-Korrektur-Wert zurueckgesetzt, den Schritt 13 der Migration
         nachweislich korrigiert). Laeuft die Migration erneut, MUSS der
         Drift-Check das als Aenderung melden.

  Exporter) der migrierte Bestand muss sich mit dem vorhandenen Exporter
         deterministisch neu erzeugen lassen — inhaltlich identisch zur
         eingecheckten db/seed_fahrzeugdaten.sql.

Der Bootstrap laeuft ueber die ECHTEN Produktionsfunktionen
(app/database.py::ensure_tables), nicht ueber eine Nachimplementierung — sonst
wuerde der Test nur pruefen, ob die eigene Kopie der Startreihenfolge korrekt
ist, nicht der tatsaechliche Code.

    python test_seed_drift.py
"""
import hashlib
import importlib
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, ".")

FEHLER: list[str] = []


def check(name, ok, info=""):
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}" + (f"   {info}" if info else ""))
    if not ok:
        FEHLER.append(name)


BASE = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.join(BASE, "db", "seed_fahrzeugdaten.sql")

if not os.path.exists(SEED):
    print(f"[SKIP] Seed fehlt ({SEED}) — Suite uebersprungen")
    raise SystemExit(0)

import app.config as cfg                                              # noqa: E402
import app.database as database                                       # noqa: E402
import app.data_migrations as dm                                      # noqa: E402
import app.fahrzeug_seed as fs                                        # noqa: E402

FAHRZEUGTABELLEN = fs.FAHRZEUGTABELLEN


def _hashes(pfad: str) -> dict[str, tuple[int, str]]:
    """(Zeilenanzahl, Inhalts-Hash) je Fahrzeugtabelle — nicht nur Row Counts.

    Der Hash liegt ueber ALLEN Spalten aller Zeilen, sortiert (die physische
    Reihenfolge ist damit egal) und normalisiert ueber `repr`.
    """
    conn = sqlite3.connect(pfad)
    try:
        out = {}
        for t in FAHRZEUGTABELLEN:
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')]
            rows = conn.execute(f'SELECT {",".join(cols)} FROM "{t}"').fetchall()
            out[t] = (len(rows),
                      hashlib.sha256(repr(sorted(map(repr, rows))).encode()).hexdigest()[:16])
        return out
    finally:
        conn.close()


def _diff(vor: dict, nach: dict) -> dict:
    return {t: (vor.get(t), nach.get(t)) for t in FAHRZEUGTABELLEN if vor.get(t) != nach.get(t)}


def _env(pfad: str, tmp: str):
    os.environ["AUTO_KI_DB_PATH"] = pfad
    os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(tmp, "chroma")
    importlib.reload(cfg)
    importlib.reload(database)


def _restore_env(alt_db, alt_chroma):
    for k, v in (("AUTO_KI_DB_PATH", alt_db), ("AUTO_KI_CHROMA_PATH", alt_chroma)):
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    importlib.reload(cfg)
    importlib.reload(database)


_alt_db = os.environ.get("AUTO_KI_DB_PATH")
_alt_chroma = os.environ.get("AUTO_KI_CHROMA_PATH")
_tmp = tempfile.mkdtemp(prefix="vira_drift_")

try:
    # ══════════════════════════════════════════════════════════════════════
    print("=== A/B/C) Seed + Migrationen == Seed (kein fachlicher Drift) ===")
    # Lauf 1: ECHTER Bootstrap ueber ensure_tables(), aber die Datenmigrationen
    # werden fuer diesen einen Aufruf stillgelegt — so entsteht der Zwischen-
    # zustand "Schema + Seed, noch OHNE Migration", ueber genau denselben
    # Code, den die App tatsaechlich beim Start durchlaeuft (Schritte 1-5).
    _db1 = os.path.join(_tmp, "a.db")
    _echte_migrationen = dm.run_data_migrations
    dm.run_data_migrations = lambda conn: None
    try:
        _env(_db1, _tmp)
        database.ensure_tables()
    finally:
        dm.run_data_migrations = _echte_migrationen
    _nach_seed = _hashes(_db1)

    # Lauf 2: derselbe DB-Pfad, jetzt MIT den echten Datenmigrationen — Schritt 6
    # aus app/database.py::ensure_tables, unveraendert.
    _env(_db1, _tmp)
    database.ensure_tables()
    _nach_migration = _hashes(_db1)

    _diff_ab = _diff(_nach_seed, _nach_migration)
    check("A1 Seed wurde ueber den echten Bootstrap geladen (Baureihen > 400)",
          _nach_seed.get("baureihe", (0,))[0] > 400, f"n={_nach_seed.get('baureihe')}")

    _c1 = sqlite3.connect(_db1)
    check("B1 Migrationen sind ueber ensure_tables() gelaufen (Marker gesetzt)",
          bool(_c1.execute("select 1 from schema_migrations where name=?",
                           (dm.MARKER_P0_V1,)).fetchone()))
    _c1.close()

    check("C1 KEINE Fahrzeugtabelle hat sich durch die Migrationen veraendert",
          not _diff_ab, str(_diff_ab))
    for _t in FAHRZEUGTABELLEN:
        check(f"C2 {_t}: Seed-Hash == Seed+Migration-Hash",
              _nach_seed[_t] == _nach_migration[_t],
              f"{_nach_seed[_t]} vs {_nach_migration[_t]}")
    check("C3 schema_migrations ist bewusst NICHT Teil des Fahrzeug-Vergleichs",
          "schema_migrations" not in FAHRZEUGTABELLEN)

    print("\n=== Idempotenz des Drift-Checks selbst ===")
    # Ein dritter Bootstrap-Lauf auf demselben Bestand darf ebenfalls nichts mehr
    # aendern — sonst waere der A/B/C-Beweis oben nur Zufall des ersten Laufs.
    _env(_db1, _tmp)
    database.ensure_tables()
    _nach_dritt = _hashes(_db1)
    check("Dritter Bootstrap-Lauf aendert ebenfalls nichts", _nach_migration == _nach_dritt)

    # ══════════════════════════════════════════════════════════════════════
    print("\n=== Gegenprobe: der Drift-Check erkennt einen ECHTEN Fehler ===")
    # Isolierte Kopie — die echte Seed-Datei wird an keiner Stelle angefasst.
    _db2 = os.path.join(_tmp, "b.db")
    _echte_migrationen2 = dm.run_data_migrations
    dm.run_data_migrations = lambda conn: None
    try:
        _env(_db2, _tmp)
        database.ensure_tables()
    finally:
        dm.run_data_migrations = _echte_migrationen2
    _vor_manipuliert = _hashes(_db2)

    _INSIGNIA = ("opel-insignia-b-2.0-diesel-174-ps-facelift",
                "opel-insignia-b-2.0-diesel-174-ps-allrad-facelift")
    _c2 = sqlite3.connect(_db2)
    _vorher_ccm = [r[0] for r in _c2.execute(
        "select hubraum_ccm from motorvariante where variante_id in (?,?)", _INSIGNIA)]
    check("Vorbedingung: Seed traegt den korrigierten Hubraum (1995 ccm)",
          _vorher_ccm == [1995, 1995], str(_vorher_ccm))

    # Der Seed-Zeile gezielt den VOR-Korrektur-Wert zurueckgeben, den Schritt 13
    # der Migration (app/data_migrations.py::schritt13_insignia_hubraum)
    # nachweislich erkennt und behebt. Das simuliert exakt den Fall "Migration
    # wurde ergaenzt, Seed wurde vergessen neu zu erzeugen" — ohne die echte
    # Seed-Datei zu ruehren.
    _c2.execute("update motorvariante set hubraum_ccm=1998 where variante_id in (?,?)",
               _INSIGNIA)
    _c2.commit()
    _c2.close()
    _gealtert = _hashes(_db2)
    check("Zeile wurde erfolgreich gealtert (Fixture greift)",
          _gealtert["motorvariante"] != _vor_manipuliert["motorvariante"])

    _env(_db2, _tmp)
    database.ensure_tables()
    _repariert = _hashes(_db2)
    _diff_gegenprobe = _diff(_gealtert, _repariert)

    check("Gegenprobe FAIL-Fall: die Migration veraendert die gealterte Zeile",
          bool(_diff_gegenprobe), str(_diff_gegenprobe))
    check("Gegenprobe: betroffen ist genau 'motorvariante', sonst nichts",
          set(_diff_gegenprobe) == {"motorvariante"}, str(sorted(_diff_gegenprobe)))
    _c2b = sqlite3.connect(_db2)
    _nachher_ccm = [r[0] for r in _c2b.execute(
        "select hubraum_ccm from motorvariante where variante_id in (?,?)", _INSIGNIA)]
    _c2b.close()
    check("Gegenprobe: die Migration hat den Wert wieder auf 1995 ccm korrigiert",
          _nachher_ccm == [1995, 1995], str(_nachher_ccm))
    check("Gegenprobe: nach der Reparatur ist der Bestand wieder identisch mit dem "
          "unmanipulierten Referenzstand (derselbe Drift-Check erkennt auch die "
          "Abwesenheit von Drift korrekt)",
          _repariert["motorvariante"] == _nach_migration["motorvariante"])

    # ══════════════════════════════════════════════════════════════════════
    print("\n=== Exporter-Konsistenz: Neu-Export aus dem migrierten Bestand ===")
    sys.path.insert(0, os.path.join(BASE, "db"))
    import export_fahrzeug_seed as exp                                # noqa: E402

    _c3 = sqlite3.connect(_db1)
    _neu_sql, _mengen = exp.erzeuge(_c3, list(exp.allowlist()))
    _c3.close()

    def _lade_sql_in_frische_db(sql_text: str) -> dict:
        pfad = os.path.join(_tmp, f"reload_{hashlib.md5(sql_text.encode()).hexdigest()[:8]}.db")
        c = sqlite3.connect(pfad)
        c.execute("PRAGMA foreign_keys=ON")
        c.executescript(open(exp.SCHEMA_PATH, encoding="utf-8").read())
        spalten = {r[1] for r in c.execute("PRAGMA table_info(baureihe)")}
        if "verification" not in spalten:
            c.execute("ALTER TABLE baureihe ADD COLUMN verification TEXT")
        for stmt in fs._statements(sql_text):
            c.execute(stmt)
        c.commit()
        c.close()
        return _hashes(pfad)

    _hash_reexport = _lade_sql_in_frische_db(_neu_sql)
    _hash_eingecheckt = _lade_sql_in_frische_db(open(SEED, encoding="utf-8").read())

    check("Exporter erzeugt aus dem migrierten Bestand denselben Inhalt wie die "
          "eingecheckte db/seed_fahrzeugdaten.sql (kein unnoetiger Diff)",
          _hash_reexport == _hash_eingecheckt,
          str(_diff(_hash_eingecheckt, _hash_reexport)))

finally:
    _restore_env(_alt_db, _alt_chroma)
    shutil.rmtree(_tmp, ignore_errors=True)


print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER: " + ", ".join(FEHLER))
    raise SystemExit(1)
print("Alle Seed-Drift-Guard-Tests bestanden.")
