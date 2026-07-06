# Vira — Deployment (Railway)

Backend läuft als Docker-Container auf **Railway** (Railway terminiert HTTPS +
Reverse-Proxy selbst — kein nginx/certbot nötig). Das **Frontend** ist ein
statischer Vite-Build und wird als Static Site ausgeliefert (Railway Static,
Netlify oder Vercel).

Diese Anleitung setzt ein Railway-Konto + verbundenes Git-Repo voraus.

---

## 1. Backend auf Railway

### 1.1 Service anlegen
1. Neues Projekt → **Deploy from GitHub repo** → `auto-ki-backend` auswählen.
2. Railway erkennt `Dockerfile` + `railway.json` automatisch
   (Builder = DOCKERFILE, Healthcheck = `/health`, Restart = ON_FAILURE).

### 1.2 Persistentes Volume (PFLICHT)
SQLite, ChromaDB und die Backups **müssen** auf einem persistenten Volume liegen —
sonst sind nach jedem Redeploy/Neustart alle Nutzer, Chats, Checks und Backups weg.

1. Service → **Variables/Settings → Volumes → New Volume**
2. Mount-Pfad: **`/data`**

Das Dockerfile setzt bereits passend vor:
`AUTO_KI_DB_PATH=/data/auto_ki.db`, `AUTO_KI_CHROMA_PATH=/data/chroma`,
`AUTO_KI_DB_BACKUP_DIR=/data/backups`.

### 1.3 Environment-Variablen
Unter **Variables** setzen (siehe `.env.example` für die vollständige Liste).
**Pflicht vor Launch:**

| Variable | Wert |
|----------|------|
| `AUTO_KI_JWT_SECRET` | langer Zufalls-String (`openssl rand -hex 32`) |
| `AUTO_KI_API_KEY` | langer Zufalls-String (anderer als JWT) |
| `AUTO_KI_CORS_ORIGINS` | echte Frontend-Domain, z.B. `https://vira.de` |
| `GEMINI_API_KEY` | Google-Gemini-Key |
| `TAVILY_API_KEY` | Tavily-Key |
| `STRIPE_SECRET_KEY` | **Live**-Key `sk_live_...` |
| `STRIPE_WEBHOOK_SECRET` | aus dem **Prod**-Webhook (siehe 1.5) |
| `STRIPE_PRICE_LIGHT/PRO/MAX/EINZELKAUF` | Live-Price-IDs |
| `FRONTEND_URL` | echte Frontend-URL (Stripe-Redirects) |

Ohne `AUTO_KI_JWT_SECRET`/`AUTO_KI_API_KEY` startet die App zwar, warnt aber laut
im Log und nutzt öffentlich bekannte Dev-Defaults → Tokens fälschbar. Nicht launchen,
bevor beide gesetzt sind.

### 1.4 ⚠️ Daten-Seeding (PFLICHT — sonst leere Wissensdatenbank)
Ein frisches Volume ist leer. `ensure_tables()` legt beim Start nur die **leeren**
Tabellen an — die **469 Baureihen** und die ChromaDB-Embeddings sind NICHT
enthalten. Ohne Seeding beantwortet Vira Fahrzeugfragen ohne DB-Wissen.

Einmalig nach dem ersten erfolgreichen Deploy:

1. **SQLite-DB hochladen:** die lokale, befüllte `auto_ki.db`
   (aus `%LOCALAPPDATA%\auto-ki-backend\auto_ki.db`) nach `/data/auto_ki.db`
   auf das Volume bringen — z.B. per Railway-CLI:
   ```
   railway run bash        # Shell im Container-Kontext
   # dann die DB via railway volume / scp / ein temporäres Upload-Skript einspielen
   ```
   (Railway hat kein direktes „Datei-Upload"-UI für Volumes — üblicher Weg:
   ein einmaliges Admin-Endpoint/Skript, oder `railway run` mit einem Copy-Schritt.)
2. **ChromaDB aus SQLite neu aufbauen** (baut `/data/chroma`):
   ```
   railway run python rebuild_chroma.py
   ```
   Das Skript liest `AUTO_KI_DB_PATH`/`AUTO_KI_CHROMA_PATH` und befüllt beide
   Collections aus der SQLite-DB. SQLite bleibt unverändert.
3. `/health` prüfen → `tables` sollte alle Fachtabellen zeigen.

### 1.5 Stripe-Webhook (Prod)
1. Stripe-Dashboard (Live-Modus) → **Entwickler → Webhooks → Endpoint hinzufügen**
2. URL: `https://<railway-backend-domain>/api/v1/payments/webhook`
3. Events: mindestens `checkout.session.completed`, `invoice.paid`,
   `customer.subscription.deleted` (an die real abonnierten Events anpassen).
4. Das erzeugte `whsec_...` als `STRIPE_WEBHOOK_SECRET` in Railway setzen.

---

## 2. Frontend (Static Site)
1. Build lokal oder via CI: `npm ci && npm run build` → Output in `dist/`.
2. Als Static Site deployen (Railway Static / Netlify / Vercel), `dist/` als Root.
3. Env-Variablen des Frontends (Build-Zeit):
   - `VITE_API_BASE_URL=https://<railway-backend-domain>`
   - `VITE_API_KEY=<AUTO_KI_API_KEY>` (identisch zum Backend)
4. Nach dem Deploy die Frontend-Domain in Backend-`AUTO_KI_CORS_ORIGINS`
   und `FRONTEND_URL` eintragen.

---

## 3. Backup & Restore
- Nach jedem erfolgreichen DB-Schreibvorgang wird automatisch eine konsistente,
  datierte Kopie nach `AUTO_KI_DB_BACKUP_DIR` (`/data/backups`) geschrieben,
  die letzten 10 Versionen bleiben erhalten.
- **Wichtig:** Diese Backups liegen auf **demselben** Volume wie die Live-DB.
  Für echte Ausfallsicherheit zusätzlich regelmäßig `/data/backups` **extern**
  sichern (Railway-Volume-Snapshot oder periodischer Off-Site-Kopie-Job).
- **Restore:** neueste `auto_ki_backup_*.db` aus `/data/backups` nach
  `/data/auto_ki.db` kopieren, Service neu starten, dann `rebuild_chroma.py`.

---

## 4. Betrieb / Verifikation
- **Healthcheck:** `GET /health` → `{"status":"ok", ...}`. Railway nutzt ihn
  für Deploy-Gating; das Dockerfile zusätzlich als Container-HEALTHCHECK.
- **Restart:** `ON_FAILURE`, max. 10 Versuche (railway.json). Uvicorn behandelt
  SIGTERM sauber; SQLite-Connections sind pro-Request → kein offener Zustand
  beim Neustart. WAL wird beim nächsten Zugriff automatisch konsolidiert.
- **Logs:** App-Level-Logs sind via `AUTO_KI_LOG_LEVEL` (Default INFO) sichtbar,
  inkl. aktivem DB-Pfad, Backup-Meldungen und Gemini-Retries.
- **Ressourcen:** Ein uvicorn-Worker. Skalierung über Railway-Replicas, nicht
  über Worker (SQLite + In-Memory-Cache sind prozesslokal).

---

## 5. Lokaler Docker-Testlauf (optional)
```
docker build -t vira-backend .
docker run -p 8000:8000 --env-file .env -v vira-data:/data vira-backend
curl http://localhost:8000/health
```
