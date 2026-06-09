import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent

DB_PATH = BASE_DIR / "db" / "auto_ki.db"

# ChromaDB ausserhalb von OneDrive/Cloud-Sync speichern —
# HNSW-Binärdateien werden von OneDrive als temporäre Dateien behandelt
# und entfernt, was zur Index-Korruption führt.
# Standard: %LOCALAPPDATA%\auto-ki-backend\chroma  (nicht synchronisiert)
# Überschreiben: Umgebungsvariable AUTO_KI_CHROMA_PATH setzen.
_chroma_default = (
    Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    / "auto-ki-backend"
    / "chroma"
)
CHROMA_PATH = Path(os.environ.get("AUTO_KI_CHROMA_PATH", str(_chroma_default)))

API_KEY = os.environ.get("AUTO_KI_API_KEY", "dev-key-change-in-prod")
RATE_LIMIT = os.environ.get("AUTO_KI_RATE_LIMIT", "20/minute")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
LLM_MODEL      = os.environ.get("AUTO_KI_LLM_MODEL",       "gemini-2.5-flash")
FAST_LLM_MODEL = os.environ.get("AUTO_KI_FAST_LLM_MODEL",  "gemini-2.5-flash-lite")
