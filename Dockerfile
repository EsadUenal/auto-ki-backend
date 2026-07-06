# Vira Backend — Produktions-Image (Railway / beliebiger Docker-Host)
# ---------------------------------------------------------------------------
# Build:  docker build -t vira-backend .
# Run:    docker run -p 8000:8000 --env-file .env -v vira-data:/data vira-backend
# ---------------------------------------------------------------------------
FROM python:3.11-slim

# Laufzeit-Systempakete:
#   libgomp1 — von chromadb/onnxruntime (lokales Embedding) zur Laufzeit benötigt
#   curl     — für den Container-Healthcheck (unten)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Abhängigkeiten zuerst installieren (nutzt den Docker-Layer-Cache: ändert sich
# nur der App-Code, muss pip nicht erneut alles herunterladen).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App-Code kopieren (.dockerignore hält .env, lokale DB, Tests etc. draußen).
COPY . .

# Non-root-Benutzer + persistentes Datenverzeichnis. Wird /data als frisches
# Named Volume gemountet, erbt es die hier gesetzte Eigentümerschaft (Docker-
# Semantik für leere Volumes) — appuser kann also schreiben.
RUN useradd -m -u 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data
USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AUTO_KI_DB_PATH=/data/auto_ki.db \
    AUTO_KI_CHROMA_PATH=/data/chroma \
    AUTO_KI_DB_BACKUP_DIR=/data/backups

EXPOSE 8000

# Container-eigener Healthcheck (Railway nutzt zusätzlich railway.json →
# healthcheckPath; für lokalen/anderen Docker-Betrieb ist dieser hier aktiv).
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT:-8000}/health" || exit 1

# Railway injiziert $PORT; Fallback 8000 für lokalen docker run.
# Ein einzelner uvicorn-Worker: SQLite + der In-Memory-Cache (60s TTL) sind
# prozesslokal — mehrere Worker hätten je einen eigenen Cache und würden die
# SQLite-Schreiblast erhöhen. Skalierung erfolgt über Replicas, nicht Worker.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
