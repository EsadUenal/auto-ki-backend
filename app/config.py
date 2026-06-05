import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent

DB_PATH = BASE_DIR / "db" / "auto_ki.db"
CHROMA_PATH = BASE_DIR / "db" / "chroma"

API_KEY = os.environ.get("AUTO_KI_API_KEY", "dev-key-change-in-prod")
RATE_LIMIT = os.environ.get("AUTO_KI_RATE_LIMIT", "20/minute")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
LLM_MODEL      = os.environ.get("AUTO_KI_LLM_MODEL",       "gemini-2.5-flash")
FAST_LLM_MODEL = os.environ.get("AUTO_KI_FAST_LLM_MODEL",  "gemini-2.5-flash-lite")
