# Auto-KI Backend — Phase 1 (Wissens-KI)

Auf Autos spezialisiertes KI-Backend. Antwortet primär aus einer geprüften Datenbank (SQLite + ChromaDB), LLM nur zur Formulierung. Harte Zahlen (PS, Nm, Verbrauch) kommen immer aus der DB, nie aus dem Modell.

## Voraussetzungen

- Python 3.10+
- Google Gemini API Key

## Einrichten

```powershell
# Abhängigkeiten installieren
python -m pip install -r requirements.txt
```

Die Datenbank braucht **keinen** manuellen Init-Schritt mehr: `app.main` legt
beim ersten Start automatisch Schema, vollen Fahrzeugbestand (416 Baureihen,
korrigiert) und Datenmigrationen an — siehe `db/README_bootstrap.md`.
`db/init_db.py`/`db/seed_data.py` sind Legacy/Dev-Skripte aus der Frühphase
(nur 2 Demo-Baureihen) und kein zweiter Produktionspfad.

## Server starten

```powershell
$env:GEMINI_API_KEY  = "dein-gemini-key"
$env:AUTO_KI_API_KEY = "dein-eigener-api-key"   # optional, Default: dev-key-change-in-prod

python -m uvicorn app.main:app --port 8000 --reload
```

## Testen

**Browser-Oberfläche** (empfohlen zum ersten Ausprobieren):
```
tests/test_chat.html  →  im Browser öffnen (API läuft auf localhost:8000)
```

**Automatisierte Golden Questions:**
```powershell
python tests/golden_questions.py
```

**Direkt per PowerShell:**
```powershell
$h = @{ "Authorization" = "Bearer dev-key-change-in-prod"; "Content-Type" = "application/json" }

# Rohe Fahrzeugdaten (kein LLM)
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/fahrzeug" -Method POST -Headers $h `
  -Body '{"marke":"BMW","modell":"M4","generation":"G82"}'

# KI-Chat (non-streaming)
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/chat" -Method POST -Headers $h `
  -Body '{"message":"Wie viel PS hat der M4 Competition G82?","verlauf":[],"stream":false}'
```

## Endpunkte

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| POST | `/api/v1/fahrzeug` | Rohe DB-Daten, kein LLM |
| POST | `/api/v1/chat` | KI-Antwort (Gemini 2.5 Flash), SSE-Streaming |
| GET | `/health` | Statuscheck |

## Umgebungsvariablen

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `GEMINI_API_KEY` | — | Pflicht für `/chat` |
| `AUTO_KI_API_KEY` | `dev-key-change-in-prod` | API-Schlüssel für alle Endpunkte |
| `AUTO_KI_RATE_LIMIT` | `20/minute` | Rate-Limit für `/chat` |
| `AUTO_KI_LLM_MODEL` | `gemini-2.5-flash` | Gemini-Modell |

## Projektstruktur

```
auto-ki-backend/
├── app/
│   ├── main.py          # FastAPI App, Exception Handler, UTF-8
│   ├── config.py        # Env-Variablen
│   ├── auth.py          # API-Key-Prüfung
│   ├── database.py      # SQLite-Abfragen
│   ├── llm.py           # Gemini-Anbindung, DB-first-Logik, System-Prompt
│   ├── models.py        # Pydantic Request/Response
│   ├── utf8.py          # UTF-8 JSON Response
│   └── routers/
│       ├── fahrzeug.py  # POST /api/v1/fahrzeug
│       └── chat.py      # POST /api/v1/chat
├── db/
│   ├── schema.sql       # SQLite-Schema (Ebene 1 Baureihe, Ebene 2 Motor)
│   ├── init_db.py       # DB anlegen
│   ├── seed_data.py     # BMW M4 F82 + G82 eintragen
│   ├── seed_vectors.py  # Fließtexte in ChromaDB übertragen
│   └── vector_schema.py # ChromaDB Collections
├── tests/
│   ├── test_chat.html       # Browser-Testoberfläche
│   └── golden_questions.py  # Automatisierte Tests
├── requirements.txt
└── README.md
```

## Nächste Schritte (Phase 2+)

- Weitere Modelle einpflegen (gängigste DE/EU)
- `POST /api/v1/bewertung` — Preiseinschätzung
- `POST /api/v1/kaufberatung`
- Bildverarbeitung (`bild_base64` im Chat-Request ist schon vorbereitet)
