# Vira Backend — Produktions-Image (Railway / beliebiger Docker-Host)
# ---------------------------------------------------------------------------
# Build:  docker build -t vira-backend .
# Run:    docker run -p 8000:8000 --env-file .env -v vira-data:/data vira-backend
# ---------------------------------------------------------------------------
# Debian-Release fest gepinnt (reproduzierbar); das Entrypoint-Skript nutzt
# setpriv aus util-linux (Essential-Paket, in bookworm /usr/bin/setpriv).
FROM python:3.11-slim-bookworm

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

# Non-root-Benutzer + persistentes Datenverzeichnis.
# Railway haengt Volumes root-eigen ein (offizielle Doku: Images mit Non-root-
# UID bekommen dort Rechteprobleme). Deshalb startet der Container als root,
# docker-entrypoint.sh korrigiert die Eigentuemerschaft von /data und wechselt
# DANN per setpriv auf appuser — die App selbst laeuft nie als root.
RUN useradd -m -u 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data \
    && chmod 0755 /app/docker-entrypoint.sh

# Embedding-Modell (ChromaDB, ONNX all-MiniLM-L6-v2) schon beim Build laden:
# sonst laedt jeder Containerstart es erneut aus dem Internet, bevor die App
# bereit ist. Liegt im Home von appuser (Path.home() zur Importzeit).
ENV HOME=/home/appuser
USER appuser
RUN python -c "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction as E; E()(['warmup'])"
USER root

# AUTO_KI_ENV=production: Startpruefung der Secrets (app/config.py
# validiere_produktion) + Secure-Auth-Cookie. Fest im Image, damit ein
# vergessener Railway-Wert nie zu den lockeren Entwicklungsregeln fuehrt.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AUTO_KI_ENV=production \
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
# --no-proxy-headers: uvicorn wuerde X-Forwarded-For SELBST auswerten und
# request.client ueberschreiben, sobald die Gegenstelle in --forwarded-allow-ips
# steht (Default: 127.0.0.1). Damit haette ein Request von loopback seine IP frei
# faelschen koennen, BEVOR app/client_ip.py ueberhaupt gefragt wird. Die
# Entscheidung, welchem Proxy zu trauen ist, gehoert an EINE Stelle
# (AUTO_KI_TRUSTED_PROXY_*) — deshalb hier abgeschaltet.
# exec: uvicorn ersetzt die Shell und bekommt SIGTERM direkt (laufende
# Requests enden sauber innerhalb von drainingSeconds, siehe railway.json).
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["sh", "-c", "exec uvicorn app.main:app --no-proxy-headers --host 0.0.0.0 --port ${PORT:-8000}"]
