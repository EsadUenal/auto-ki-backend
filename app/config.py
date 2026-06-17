import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Datenbankpfade
# ---------------------------------------------------------------------------
# Sowohl SQLite als auch ChromaDB werden ausserhalb von OneDrive gespeichert.
# Begründung:
#   - ChromaDB: OneDrive entfernt HNSW-Binärdateien → Index-Korruption
#   - SQLite (WAL-Modus): OneDrive kann -wal/-shm-Dateien inkonsistent synchen →
#     Backup-Restore liefert korrupte oder veraltete Daten
#
# Standard für beide: %LOCALAPPDATA%\auto-ki-backend\  (nicht synchronisiert)
# Überschreiben: Umgebungsvariablen AUTO_KI_DB_PATH / AUTO_KI_CHROMA_PATH
#
# Backup:
#   Nach jedem erfolgreichen Save wird eine konsistente Sicherungskopie nach
#   DB_BACKUP_PATH geschrieben (via sqlite3.Connection.backup()).
#   Diese Kopie liegt IN OneDrive und wird automatisch in die Cloud synchronisiert.
#   Im Notfall (Datenverlust): DB_BACKUP_PATH → DB_PATH kopieren.
# ---------------------------------------------------------------------------

_local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "auto-ki-backend"

# Live-Datenbank — ausserhalb OneDrive (kein Sync-Risiko)
_db_default = _local / "auto_ki.db"
DB_PATH = Path(os.environ.get("AUTO_KI_DB_PATH", str(_db_default)))

# Ursprünglicher Pfad in OneDrive — wird für automatische Migration benötigt
DB_LEGACY_PATH = BASE_DIR / "db" / "auto_ki.db"

# Backup-Verzeichnis IN OneDrive — datierte Kopien, letzte 10 Versionen behalten.
# Jedes Backup bekommt einen eigenen Zeitstempel: auto_ki_backup_YYYY-MM-DD_HHMM.db
# → ein gutes Backup kann nie von einem schlechten überschrieben werden.
DB_BACKUP_DIR  = BASE_DIR / "db" / "backups"

# Legacy-Einzeldatei — nicht mehr beschrieben, bleibt für migrate_db.py --restore
# als letzter Fallback wenn DB_BACKUP_DIR leer ist.
DB_BACKUP_PATH = BASE_DIR / "db" / "auto_ki_backup.db"

# ChromaDB — ausserhalb OneDrive (HNSW-Binärdateien)
_chroma_default = _local / "chroma"
CHROMA_PATH = Path(os.environ.get("AUTO_KI_CHROMA_PATH", str(_chroma_default)))

API_KEY = os.environ.get("AUTO_KI_API_KEY", "dev-key-change-in-prod")
RATE_LIMIT = os.environ.get("AUTO_KI_RATE_LIMIT", "20/minute")

# --- Auth (Phase 2b) ---
# In Produktion: langen Zufalls-String setzen, z.B. `openssl rand -hex 32`
JWT_SECRET = os.environ.get("AUTO_KI_JWT_SECRET", "dev-jwt-secret-change-in-prod")
JWT_EXPIRE_DAYS = int(os.environ.get("AUTO_KI_JWT_EXPIRE_DAYS", "7"))

# CORS-Origins die Cookies senden dürfen (komma-getrennt in Env-Var)
_cors_default = "http://localhost:3000,http://localhost:3001,http://localhost:5173,null"
CORS_ORIGINS: list[str] = [
    o.strip() for o in os.environ.get("AUTO_KI_CORS_ORIGINS", _cors_default).split(",") if o.strip()
]

GEMINI_API_KEY      = os.environ.get("GEMINI_API_KEY", "")
LLM_MODEL           = os.environ.get("AUTO_KI_LLM_MODEL",       "gemini-2.5-flash")
FAST_LLM_MODEL      = os.environ.get("AUTO_KI_FAST_LLM_MODEL",  "gemini-2.5-flash-lite")

# Tavily Search — optional, aktiviert Echtzeit-Websuche für Preise/Rückrufe
# Free-Plan: 1.000 Abfragen/Monat, kein Kreditkarte — https://app.tavily.com/
# Windows/PowerShell: $env:TAVILY_API_KEY = "tvly-..."
# Dauerhaft: In .env eintragen: TAVILY_API_KEY=tvly-...
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
