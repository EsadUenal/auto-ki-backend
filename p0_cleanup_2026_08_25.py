"""
CLI zum P0-Datencleanup der Fahrzeugdatenbank.

Die Korrekturlogik liegt seit dem Reproduzierbarkeits-Schritt NICHT mehr hier,
sondern in `app/data_migrations.py`. Grund: das GIT-/DB-LIFECYCLE-AUDIT hat
gezeigt, dass der Fahrzeugdatenbestand weder im Repository (`.gitignore`) noch im
Docker-Image (`.dockerignore`) liegt — ein Einmal-Skript auf einem Entwickler-
rechner erreicht das Produktions-Volume nie. Die Korrekturen laufen deshalb als
versionierte Migration ueber `schema_migrations` automatisch beim App-Start.

Dieses Skript bleibt als BEDIENOBERFLAECHE erhalten:

    python p0_cleanup_2026_08_25.py              # Trockenlauf, aendert nichts
    python p0_cleanup_2026_08_25.py --apply      # schreibt, ohne Marker zu setzen
    python p0_cleanup_2026_08_25.py --migration  # regulaerer Migrationslauf inkl. Marker

Nuetzlich fuer: Vorabpruefung eines fremden Datenstands, gezieltes Nachziehen
einer bereits laufenden Instanz, und Diagnose. Die Schrittliste ist dieselbe wie
beim App-Start — es gibt nur EINE Implementierung.

WICHTIG: vor `--apply`/`--migration` ein Backup anlegen. Die App tut das im
regulaeren Betrieb selbst (periodisches Backup, siehe app/config.py).
"""
import argparse
import logging
import sqlite3
import sys

sys.path.insert(0, ".")

from app.config import DB_PATH  # noqa: E402
from app.data_migrations import (  # noqa: E402
    MARKER_P0_V1, SCHRITTE_P0_V1, fuehre_migration_aus, log,
    pruefe_integritaet, zaehle,
)


def main() -> int:
    # Die Korrekturlogik protokolliert ueber den Modul-Logger (sie laeuft im
    # Normalfall im App-Start). Fuer die CLI wird er sichtbar gemacht, sonst
    # bliebe der Trockenlauf stumm.
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true",
                   help="Aenderungen schreiben, aber KEINEN Migrationsmarker setzen")
    p.add_argument("--migration", action="store_true",
                   help="regulaerer Migrationslauf inkl. Marker (wie beim App-Start)")
    p.add_argument("--db", default=str(DB_PATH))
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        if args.migration:
            print(f"DB: {args.db}\nMODUS: MIGRATION (setzt Marker {MARKER_P0_V1!r})")
            angewendet = fuehre_migration_aus(conn, MARKER_P0_V1, SCHRITTE_P0_V1)
            print("\n".join(protokoll_zeilen()))
            print("\nangewendet:" if angewendet else "\nnichts zu tun (Marker bereits gesetzt "
                                                    "oder Abbruch — siehe Log)", angewendet)
            return 0

        print(f"DB: {args.db}")
        print(f"MODUS: {'APPLY (schreibend, ohne Marker)' if args.apply else 'TROCKENLAUF'}")
        vorher = zaehle(conn)
        log("\nZeilen VORHER: " + ", ".join(f"{k}={v}" for k, v in vorher.items()))
        log("\n-- Integritaet VORHER --")
        if pruefe_integritaet(conn):
            log("!! DB ist schon vor dem Cleanup nicht integer — Abbruch")
            return 2

        log("\n-- Korrekturen --")
        try:
            conn.execute("BEGIN")
            for schritt in SCHRITTE_P0_V1:
                schritt(conn, args.apply)
            log("\n-- Integritaet NACHHER (noch in der Transaktion) --")
            fehler = pruefe_integritaet(conn)
            if fehler:
                raise RuntimeError("Integritaetsverletzung: " + "; ".join(fehler))
            nachher = zaehle(conn)
            log("\nZeilen NACHHER: " + ", ".join(f"{k}={v}" for k, v in nachher.items()))
            log("DIFF:           " + (", ".join(
                f"{k}={nachher[k] - vorher[k]:+d}"
                for k in vorher if nachher[k] != vorher[k]) or "keine"))
            if args.apply:
                conn.commit()
                log("\nCOMMIT ausgefuehrt.")
            else:
                conn.rollback()
                log("\nTrockenlauf — ROLLBACK, nichts geschrieben.")
        except Exception as exc:
            conn.rollback()
            log(f"\n!! FEHLER: {exc}\n!! ROLLBACK — die Datenbank ist unveraendert.")
            return 1
    finally:
        conn.close()
    return 0


def protokoll_zeilen():
    from app.data_migrations import protokoll
    return protokoll


if __name__ == "__main__":
    sys.exit(main())