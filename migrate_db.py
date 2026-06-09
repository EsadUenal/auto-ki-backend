"""
Standalone-Skript: SQLite-Datenbank aus OneDrive-Pfad nach LocalAppData migrieren.

Verwendung:
    python migrate_db.py

Wann verwenden:
  - Erste Einrichtung nach dem Update (verschiebt auto_ki.db aus OneDrive)
  - Nach Datenverlust: python migrate_db.py --restore  (Backup → Live-DB)
  - Zur Diagnose: zeigt wo DB_PATH und DB_BACKUP_PATH liegen

Was passiert:
  1. Prüft, ob Live-DB (DB_PATH) und Legacy-DB (OneDrive) vorhanden sind
  2. Falls Live-DB fehlt aber Legacy vorhanden: kopiert via sqlite3.Connection.backup()
  3. Prüft PRAGMA integrity_check der neuen Kopie
  4. Erstellt sofort ein Backup nach DB_BACKUP_PATH (OneDrive)
  5. Live-DB in OneDrive wird NICHT gelöscht (sicherheitshalber)

--restore:
  Kopiert DB_BACKUP_PATH → DB_PATH (Notfall-Wiederherstellung aus Cloud-Backup).
"""
import sys
import io
import sqlite3
import shutil
import time
import argparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from app.config import DB_PATH, DB_LEGACY_PATH, DB_BACKUP_PATH


def _check_db(path) -> tuple[int, int, str]:
    """Gibt (baureihen, motoren, integrity) zurück."""
    conn = sqlite3.connect(path)
    n_b = conn.execute("SELECT COUNT(*) FROM baureihe").fetchone()[0]
    n_m = conn.execute("SELECT COUNT(*) FROM motorvariante").fetchone()[0]
    ic  = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()
    return n_b, n_m, ic


def _copy_db(src, dst, label: str):
    """Kopiert SQLite via .backup() — garantiert konsistent."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    print(f"  Kopiere {label} ...")
    t0 = time.time()
    s = sqlite3.connect(src)
    d = sqlite3.connect(dst)
    s.backup(d)
    d.close()
    s.close()
    dt = time.time() - t0
    sz = dst.stat().st_size
    print(f"  Fertig in {dt:.1f}s — {sz:,} bytes ({sz//1024} KB)")


def cmd_migrate():
    print("=" * 65)
    print("SQLite-Migration: OneDrive → LocalAppData")
    print("=" * 65)

    print(f"\nPfade:")
    print(f"  Live-DB (Ziel)    : {DB_PATH}")
    print(f"  Legacy-DB (Quelle): {DB_LEGACY_PATH}")
    print(f"  Backup (OneDrive) : {DB_BACKUP_PATH}")

    # --- Zustand ermitteln ---
    live_ok   = DB_PATH.exists()
    legacy_ok = DB_LEGACY_PATH.exists()
    backup_ok = DB_BACKUP_PATH.exists()

    print(f"\nZustand:")
    print(f"  Live-DB vorhanden : {'JA' if live_ok else 'NEIN'}")
    print(f"  Legacy-DB vorhand.: {'JA' if legacy_ok else 'NEIN'}")
    print(f"  Backup vorhanden  : {'JA' if backup_ok else 'NEIN'}")

    if live_ok:
        n_b, n_m, ic = _check_db(DB_PATH)
        print(f"\n  Live-DB: {n_b} Baureihen, {n_m} Motoren, integrity={ic}")
        if n_b > 0 and ic == "ok":
            print("\n  Live-DB ist bereits vorhanden und intakt.")
            print("  Migration nicht nötig — erstelle nur Backup.")
        else:
            print(f"\n  WARNUNG: Live-DB existiert aber hat Probleme (integrity={ic}, baureihen={n_b}).")
    else:
        if legacy_ok:
            print(f"\n  Live-DB fehlt — starte Migration aus Legacy-DB.")
            _copy_db(DB_LEGACY_PATH, DB_PATH, "Legacy → Live")
            n_b, n_m, ic = _check_db(DB_PATH)
            print(f"  Integritätsprüfung: {ic}")
            print(f"  Ergebnis: {n_b} Baureihen, {n_m} Motoren")
            if ic != "ok":
                print(f"  FEHLER: integrity_check={ic!r} — Migration fehlgeschlagen!")
                sys.exit(1)
            print("  Migration erfolgreich.")
        else:
            print("\n  Weder Live-DB noch Legacy-DB vorhanden.")
            print("  Frische Installation — DB wird bei erstem Start angelegt.")
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            print(f"  Verzeichnis angelegt: {DB_PATH.parent}")
            return

    # --- Backup erstellen ---
    print(f"\n  Erstelle Backup → {DB_BACKUP_PATH.name} ...")
    _copy_db(DB_PATH, DB_BACKUP_PATH, "Live → Backup")
    n_b2, n_m2, ic2 = _check_db(DB_BACKUP_PATH)
    print(f"  Backup-Integrität : {ic2} | {n_b2} Baureihen, {n_m2} Motoren")

    print("\n" + "=" * 65)
    print("Fertig.")
    print(f"  Live-DB    : {DB_PATH}")
    print(f"  Backup     : {DB_BACKUP_PATH}  (von OneDrive synchronisiert)")
    print("\nHinweis: Die alte auto_ki.db in OneDrive wurde NICHT gelöscht.")
    print("Sie können sie nach erfolgreicher Prüfung manuell entfernen.")


def cmd_restore():
    print("=" * 65)
    print("NOTFALL-WIEDERHERSTELLUNG: Backup → Live-DB")
    print("=" * 65)

    if not DB_BACKUP_PATH.exists():
        print(f"\nFEHLER: Backup nicht gefunden: {DB_BACKUP_PATH}")
        sys.exit(1)

    n_b, n_m, ic = _check_db(DB_BACKUP_PATH)
    print(f"\nBackup-Status: {n_b} Baureihen, {n_m} Motoren, integrity={ic}")
    if ic != "ok":
        print(f"FEHLER: Backup korrupt (integrity={ic!r}) — Wiederherstellung abgebrochen.")
        sys.exit(1)

    if DB_PATH.exists():
        broken = DB_PATH.parent / (DB_PATH.name + ".broken")
        print(f"\nSichere defekte Live-DB als: {broken.name}")
        shutil.move(str(DB_PATH), str(broken))

    print(f"\nWiederherstellung: {DB_BACKUP_PATH.name} → {DB_PATH.name}")
    _copy_db(DB_BACKUP_PATH, DB_PATH, "Backup → Live")

    n_b2, n_m2, ic2 = _check_db(DB_PATH)
    print(f"Ergebnis: {n_b2} Baureihen, {n_m2} Motoren, integrity={ic2}")
    if ic2 == "ok":
        print("\nWiederherstellung erfolgreich.")
    else:
        print(f"\nFEHLER: Wiederhergestellte DB ist korrupt (integrity={ic2!r}).")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SQLite-Datenbank migrieren oder wiederherstellen.")
    parser.add_argument("--restore", action="store_true",
                        help="Notfall: Backup → Live-DB wiederherstellen")
    args = parser.parse_args()

    if args.restore:
        cmd_restore()
    else:
        cmd_migrate()
