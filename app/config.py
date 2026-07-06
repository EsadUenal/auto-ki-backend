import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent

# .env im Backend-Ordner laden (vor allen os.environ.get()-Aufrufen).
# override=False: bereits gesetzte Systemvariablen haben Vorrang.
load_dotenv(BASE_DIR / ".env", override=False)

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

# Backup-Verzeichnis — datierte Kopien, letzte 10 Versionen behalten.
# Jedes Backup bekommt einen eigenen Zeitstempel: auto_ki_backup_YYYY-MM-DD_HHMM.db
# → ein gutes Backup kann nie von einem schlechten überschrieben werden.
#
# Standard (lokale Entwicklung): BASE_DIR/db/backups (liegt in OneDrive → Cloud-Sync).
# PRODUKTION: Muss per AUTO_KI_DB_BACKUP_DIR auf ein PERSISTENTES Volume zeigen —
# sonst landen die Backups im ephemeren Container-Dateisystem und sind nach jedem
# Redeploy/Neustart verloren (stiller Datenverlust im Ernstfall).
DB_BACKUP_DIR  = Path(os.environ.get("AUTO_KI_DB_BACKUP_DIR", str(BASE_DIR / "db" / "backups")))

# Legacy-Einzeldatei — nicht mehr beschrieben, bleibt für migrate_db.py --restore
# als letzter Fallback wenn DB_BACKUP_DIR leer ist.
DB_BACKUP_PATH = BASE_DIR / "db" / "auto_ki_backup.db"

# ChromaDB — ausserhalb OneDrive (HNSW-Binärdateien)
_chroma_default = _local / "chroma"
CHROMA_PATH = Path(os.environ.get("AUTO_KI_CHROMA_PATH", str(_chroma_default)))

API_KEY = os.environ.get("AUTO_KI_API_KEY", "dev-key-change-in-prod")
RATE_LIMIT = os.environ.get("AUTO_KI_RATE_LIMIT", "20/minute")

# Log-Level für die App-eigenen Logger (uvicorn-Access-Logs bleiben unberührt).
# Ohne explizite Konfiguration surft die Root-Loglevel-Vorgabe auf WARNING und
# alle log.info(...)-Meldungen der App (DB-Pfad, Backups, Retries) sind unsichtbar.
LOG_LEVEL = os.environ.get("AUTO_KI_LOG_LEVEL", "INFO").upper()

# --- Auth (Phase 2b) ---
# In Produktion: langen Zufalls-String setzen, z.B. `openssl rand -hex 32`
JWT_SECRET = os.environ.get("AUTO_KI_JWT_SECRET", "dev-jwt-secret-change-in-prod")
JWT_EXPIRE_DAYS = int(os.environ.get("AUTO_KI_JWT_EXPIRE_DAYS", "7"))

# CORS-Origins die Cookies senden dürfen (komma-getrennt in Env-Var)
# WICHTIG: "null" NICHT aufnehmen — Browser senden Origin: null aus sandboxed
# iframes und file://-Kontexten; zusammen mit allow_credentials=True würde das
# jeder lokal geöffneten HTML-Datei erlauben, authentifizierte Requests mit
# dem Nutzer-Cookie zu senden (klassische CORS-Fehlkonfiguration).
_cors_default = "http://localhost:3000,http://localhost:3001,http://localhost:5173"
CORS_ORIGINS: list[str] = [
    o.strip() for o in os.environ.get("AUTO_KI_CORS_ORIGINS", _cors_default).split(",") if o.strip()
]

# Stripe — Phase 2d
# Testmodus-Keys unter https://dashboard.stripe.com/test/apikeys
# Webhook-Secret via: stripe listen --forward-to localhost:8000/api/v1/payments/webhook
STRIPE_SECRET_KEY       = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET   = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_LIGHT      = os.environ.get("STRIPE_PRICE_LIGHT", "")       # price_xxx
STRIPE_PRICE_PRO        = os.environ.get("STRIPE_PRICE_PRO", "")         # price_xxx
STRIPE_PRICE_MAX        = os.environ.get("STRIPE_PRICE_MAX", "")         # price_xxx
STRIPE_PRICE_EINZELKAUF = os.environ.get("STRIPE_PRICE_EINZELKAUF", "")  # price_xxx (one_time)
FRONTEND_URL            = os.environ.get("FRONTEND_URL", "http://localhost:3000")

GEMINI_API_KEY      = os.environ.get("GEMINI_API_KEY", "")
LLM_MODEL           = os.environ.get("AUTO_KI_LLM_MODEL",       "gemini-2.5-flash")
FAST_LLM_MODEL      = os.environ.get("AUTO_KI_FAST_LLM_MODEL",  "gemini-2.5-flash-lite")

# Tavily Search — optional, aktiviert Echtzeit-Websuche für Preise/Rückrufe
# Free-Plan: 1.000 Abfragen/Monat, kein Kreditkarte — https://app.tavily.com/
# Windows/PowerShell: $env:TAVILY_API_KEY = "tvly-..."
# Dauerhaft: In .env eintragen: TAVILY_API_KEY=tvly-...
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
