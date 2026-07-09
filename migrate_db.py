"""
Standalone-Skript: SQLite-Datenbank migrieren, sichern und wiederherstellen.

Verwendung:
    python migrate_db.py              # Migration + erstes datiertes Backup anlegen
    python migrate_db.py --restore    # Notfall: neuestes datiertes Backup → Live-DB
    python migrate_db.py --list       # Alle vorhandenen Backups auflisten

Backups liegen in DB_BACKUP_DIR (OneDrive/db/backups/):
  auto_ki_backup_YYYY-MM-DD_HHMM.db
  Die letzten 10 Versionen werden behalten — ältere automatisch gelöscht.
  Ein gutes Backup kann so nie von einem schlechten überschrieben werden.
"""
import sys
import io
import sqlite3
import shutil
import time
import argparse
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from app.config import DB_PATH, DB_LEGACY_PATH, DB_BACKUP_DIR, DB_BACKUP_PATH

_BACKUP_KEEP = 10


def _check_db(path) -> tuple[int, int, str]:
    conn = sqlite3.connect(path)
    n_b = conn.execute("SELECT COUNT(*) FROM baureihe").fetchone()[0]
    n_m = conn.execute("SELECT COUNT(*) FROM motorvariante").fetchone()[0]
    ic  = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()
    return n_b, n_m, ic


def _copy_db(src: Path, dst: Path, label: str):
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
    print(f"  Fertig in {dt:.1f}s — {sz:,} bytes ({sz // 1024} KB)")


def _latest_backup() -> Path | None:
    """Neueste datierte Backup-Datei aus DB_BACKUP_DIR; None wenn leer."""
    if not DB_BACKUP_DIR.exists():
        return None
    files = sorted(DB_BACKUP_DIR.glob("auto_ki_backup_*.db"))
    return files[-1] if files else None


def _create_dated_backup() -> Path:
    """Erstellt eine neue datierte Backup-Datei; rotiert auf _BACKUP_KEEP."""
    DB_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    dst = DB_BACKUP_DIR / f"auto_ki_backup_{ts}.db"
    _copy_db(DB_PATH, dst, f"Live → {dst.name}")
    n_b, n_m, ic = _check_db(dst)
    print(f"  Integrität: {ic} | {n_b} Baureihen, {n_m} Motoren")

    all_backups = sorted(DB_BACKUP_DIR.glob("auto_ki_backup_*.db"))
    deleted = all_backups[:-_BACKUP_KEEP]
    for old in deleted:
        old.unlink(missing_ok=True)
        print(f"  Altes Backup gelöscht: {old.name}")
    return dst


def cmd_list():
    print("=" * 65)
    print("Vorhandene Backups")
    print("=" * 65)
    print(f"\nBackup-Verzeichnis: {DB_BACKUP_DIR}")
    if not DB_BACKUP_DIR.exists():
        print("  (Verzeichnis nicht vorhanden — noch kein Backup erstellt)")
        return

    files = sorted(DB_BACKUP_DIR.glob("auto_ki_backup_*.db"), reverse=True)
    if not files:
        print("  (leer)")
        return

    print(f"\n  {'Dateiname':<38} {'Größe':>8}  {'Baureihen':>10}  {'Integrität'}")
    print(f"  {'-'*38} {'-'*8}  {'-'*10}  {'-'*10}")
    for f in files:
        sz = f.stat().st_size
        try:
            n_b, n_m, ic = _check_db(f)
            marker = "  ← neueste" if f == files[0] else ""
            print(f"  {f.name:<38} {sz//1024:>6} KB  {n_b:>10}  {ic}{marker}")
        except Exception as e:
            print(f"  {f.name:<38} (Lesefehler: {e})")

    # Legacy-Einzeldatei
    if DB_BACKUP_PATH.exists():
        sz2 = DB_BACKUP_PATH.stat().st_size
        try:
            n_b2, n_m2, ic2 = _check_db(DB_BACKUP_PATH)
            print(f"\n  Legacy: {DB_BACKUP_PATH.name}  {sz2//1024} KB  {n_b2} Baureihen  {ic2}")
        except Exception:
            pass


def cmd_migrate():
    print("=" * 65)
    print("SQLite-Migration: OneDrive → LocalAppData")
    print("=" * 65)

    print(f"\nPfade:")
    print(f"  Live-DB (Ziel)      : {DB_PATH}")
    print(f"  Legacy-DB (Quelle)  : {DB_LEGACY_PATH}")
    print(f"  Backup-Verzeichnis  : {DB_BACKUP_DIR}")

    live_ok   = DB_PATH.exists()
    legacy_ok = DB_LEGACY_PATH.exists()

    print(f"\nZustand:")
    print(f"  Live-DB vorhanden   : {'JA' if live_ok else 'NEIN'}")
    print(f"  Legacy-DB vorhanden : {'JA' if legacy_ok else 'NEIN'}")

    if live_ok:
        n_b, n_m, ic = _check_db(DB_PATH)
        print(f"\n  Live-DB: {n_b} Baureihen, {n_m} Motoren, integrity={ic}")
        if n_b > 0 and ic == "ok":
            print("\n  Live-DB ist bereits vorhanden und intakt.")
            print("  Migration nicht nötig — erstelle nur datiertes Backup.")
        else:
            print(f"\n  WARNUNG: Live-DB hat Probleme (integrity={ic}, baureihen={n_b}).")
    else:
        if legacy_ok:
            print("\n  Live-DB fehlt — starte Migration aus Legacy-DB.")
            _copy_db(DB_LEGACY_PATH, DB_PATH, "Legacy → Live")
            n_b, n_m, ic = _check_db(DB_PATH)
            print(f"  Integritätsprüfung: {ic} | {n_b} Baureihen, {n_m} Motoren")
            if ic != "ok":
                print(f"  FEHLER: integrity_check={ic!r} — Migration fehlgeschlagen!")
                sys.exit(1)
            print("  Migration erfolgreich.")
        else:
            print("\n  Weder Live-DB noch Legacy-DB vorhanden.")
            print("  Frische Installation — DB wird bei erstem Start angelegt.")
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            return

    print(f"\n  Erstelle datiertes Backup ...")
    dst = _create_dated_backup()

    print("\n" + "=" * 65)
    print("Fertig.")
    print(f"  Live-DB : {DB_PATH}")
    print(f"  Backup  : {dst}  (von OneDrive synchronisiert)")
    print("\nHinweis: Die alte auto_ki.db in OneDrive wurde NICHT gelöscht.")


def cmd_restore():
    print("=" * 65)
    print("NOTFALL-WIEDERHERSTELLUNG: Backup → Live-DB")
    print("=" * 65)

    # Neuestes datiertes Backup bevorzugen — bei Korruption automatisch das
    # nächst-ältere versuchen, statt sofort abzubrechen (im echten Notfall
    # ist "der letzte Versuch schlägt fehl" das denkbar schlechteste
    # Verhalten, wenn 9 weitere, gute Backups im selben Verzeichnis liegen).
    kandidaten = sorted(DB_BACKUP_DIR.glob("auto_ki_backup_*.db"), reverse=True) \
        if DB_BACKUP_DIR.exists() else []

    src = None
    n_b = n_m = 0
    ic = "kein Kandidat geprüft"
    for kandidat in kandidaten:
        # Bei schwerer Beschädigung wirft schon die SELECT-Abfrage in
        # _check_db() eine sqlite3.DatabaseError, bevor PRAGMA integrity_check
        # überhaupt erreicht wird — auch das zählt als korrupt, sonst würde
        # der Restore-Versuch hier abstürzen statt zum nächsten Kandidaten
        # weiterzugehen (gleiches Muster wie db_writer._backup_sqlite).
        try:
            n_b, n_m, ic = _check_db(kandidat)
        except sqlite3.DatabaseError as exc:
            ic = f"Prüfung fehlgeschlagen: {exc}"
        if ic == "ok":
            src = kandidat
            if kandidat != kandidaten[0]:
                print(f"\nHinweis: neuestes Backup war korrupt — verwende stattdessen: {kandidat.name}")
            else:
                print(f"\nNeuestes Backup: {kandidat.name}")
            break
        print(f"  Übersprungen (korrupt, integrity={ic!r}): {kandidat.name}")

    if src is None:
        # Fallback auf Legacy-Einzeldatei
        if DB_BACKUP_PATH.exists():
            src = DB_BACKUP_PATH
            n_b, n_m, ic = _check_db(src)
            print(f"\nKein intaktes datiertes Backup gefunden — verwende Legacy: {src.name}")
        else:
            print("\nFEHLER: Kein intaktes Backup gefunden (weder datiert noch legacy).")
            sys.exit(1)

    print(f"Backup-Status: {n_b} Baureihen, {n_m} Motoren, integrity={ic}")
    if ic != "ok":
        print(f"FEHLER: Backup korrupt (integrity={ic!r}) — Wiederherstellung abgebrochen.")
        sys.exit(1)

    if DB_PATH.exists():
        broken = DB_PATH.parent / (DB_PATH.name + ".broken")
        print(f"\nSichere aktuelle Live-DB als: {broken.name}")
        shutil.move(str(DB_PATH), str(broken))

    print(f"\nWiederherstellung: {src.name} → {DB_PATH.name}")
    _copy_db(src, DB_PATH, "Backup → Live")

    n_b2, n_m2, ic2 = _check_db(DB_PATH)
    print(f"Ergebnis: {n_b2} Baureihen, {n_m2} Motoren, integrity={ic2}")
    if ic2 == "ok":
        print("\nWiederherstellung erfolgreich.")
        print("Nächster Schritt: python rebuild_chroma.py (ChromaDB neu aufbauen)")
    else:
        print(f"\nFEHLER: Wiederhergestellte DB ist korrupt (integrity={ic2!r}).")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="SQLite-Datenbank migrieren, sichern und wiederherstellen."
    )
    parser.add_argument("--restore", action="store_true",
                        help="Notfall: neuestes datiertes Backup → Live-DB")
    parser.add_argument("--list", action="store_true",
                        help="Alle vorhandenen Backups auflisten")
    args = parser.parse_args()

    if args.restore:
        cmd_restore()
    elif args.list:
        cmd_list()
    else:
        cmd_migrate()
