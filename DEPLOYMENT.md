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
| `AUTO_KI_ENV` | `production` (im Dockerfile bereits gesetzt — nicht überschreiben) |
| `AUTO_KI_API_KEY` | langer Zufalls-String (anderer als JWT). **Öffentlich:** steht als `VITE_API_KEY` im Frontend-Bundle, schützt nur Consumer-Routen |
| `AUTO_KI_ADMIN_API_KEY` | optional; eigener Zufalls-String ≥ 32 Zeichen, NIE im Frontend. Ohne ihn sind die Admin-Endpunkte geschlossen |
| `AUTO_KI_CORS_ORIGINS` | echte Frontend-Domain, z.B. `https://app.getenfal.de` |
| `AUTO_KI_TRUSTED_PROXY_HOPS` | `0` bis zur Live-Verifikation (siehe 1.6), danach die belegte Hop-Zahl |
| `GEMINI_API_KEY` | Google-Gemini-Key |
| `TAVILY_API_KEY` | Tavily-Key |
| `STRIPE_SECRET_KEY` | **Live**-Key `sk_live_...` |
| `STRIPE_WEBHOOK_SECRET` | aus dem **Prod**-Webhook (siehe 1.5) |
| `STRIPE_PRICE_KAUFCHECK/VERKAUFSCHECK/PLUS` | Live-Price-IDs (5,99 € / 8,99 € / 16,99 € mtl.) |
| `FRONTEND_URL` | echte Frontend-URL (Stripe-Redirects) |

Mit `AUTO_KI_ENV=production` **verweigert die App den Start** (`app/config.py`
`validiere_produktion()`), wenn `AUTO_KI_JWT_SECRET` fehlt/Dev-Default/kürzer als
32 Zeichen ist, `AUTO_KI_API_KEY` fehlt/Dev-Default ist, beide gleich sind,
`STRIPE_WEBHOOK_SECRET` fehlt oder ein gesetzter `AUTO_KI_ADMIN_API_KEY` schwach
oder gleich dem Consumer-Key ist. Das Auth-Cookie ist in Produktion `Secure`.
LIGHT/PRO/MAX/EINZELKAUF werden nicht mehr verkauft — keine Price-IDs dafür setzen.

### 1.7 Offene Punkte aus dem Security-Audit

- **E-Mail-Zustellung**: Die Bestaetigung (`/api/v1/auth/verify-email`) ist
  serverseitig fertig — Token, Ablauf, Einmal-Einloesung. Der VERSAND haengt am
  spaeteren Provider-Block. Bis dahin bekommen neue Konten keine Mail und damit
  keine kostenlosen LLM-Kontingente; Kaeufe und bezahlte Checks funktionieren
  unabhaengig davon. In der Entwicklung liefert die Registrierung den Token
  direkt in der Antwort (`verifikationstoken_dev`), in Produktion nie.
  `AUTO_KI_EMAIL_VERIFIKATION=0` schaltet die Huerde ab — nur fuer Tests.

### 1.6 Client-IP hinter dem Railway-Proxy (PFLICHT nach dem ersten Deploy)

Rate-Limits, Login-Drosselung und die anonyme AutoFinder-Demo haengen an der
Client-IP. Hinter einem Proxy ist `request.client.host` dessen Adresse — dann
teilen sich **alle** Nutzer einen Zaehler und ein Einzelner kann die Limits fuer
alle verbrauchen.

Die Header-Auswertung ist **standardmaessig aus** (`AUTO_KI_TRUSTED_PROXY_HOPS=0`),
weil ein frei gesetzter `X-Forwarded-For` sonst jedes Limit aushebeln wuerde.
Railways offizielle Dokumentation beschreibt Header-Behandlung und Hop-Zahl
nicht; im Support-Forum widersprechen sich die Angaben (Edge strippt XFF und
setzt `X-Real-IP` vs. Edge haengt nur an). Deshalb wird hier nichts geraten,
sondern **einmal live gemessen**:

1. Deployen, Admin-Key setzen (`AUTO_KI_ADMIN_API_KEY`).
2. Von zwei Geraeten mit verschiedenen oeffentlichen IPs aufrufen:
   `curl -H "Authorization: Bearer $AUTO_KI_ADMIN_API_KEY" https://<domain>/api/v1/admin/client-ip`
3. Die Antwort zeigt `gegenstelle`, die eingehenden Header und den aktuell
   verwendeten `verwendeter_limit_schluessel`.
   - Steht die echte Client-IP in `x-real-ip`:
     `AUTO_KI_CLIENT_IP_HEADER=x-real-ip` + `AUTO_KI_TRUSTED_PROXY_HOPS=1`.
   - Steht sie als letzter Eintrag in `x-forwarded-for`:
     `AUTO_KI_TRUSTED_PROXY_HOPS=<Anzahl der eigenen Proxys>` (meist 1).
   - Liegt `gegenstelle` nicht in einem Vertrauensnetz
     (`gegenstelle_ist_vertrauenswuerdiger_proxy: false`), zuerst
     `AUTO_KI_TRUSTED_PROXY_NETS` auf das tatsaechliche Proxy-Netz setzen.
4. Danach den Aufruf wiederholen: `verwendeter_limit_schluessel` muss die echte
   Client-IP zeigen und sich zwischen den beiden Geraeten unterscheiden.
5. Gegenprobe gegen Spoofing: mit `-H "X-Forwarded-For: 1.2.3.4"` darf sich der
   Schluessel **nicht** auf 1.2.3.4 aendern.

Der Server laeuft dafuer bewusst mit `uvicorn --no-proxy-headers` (siehe
Dockerfile): uvicorn wuerde `X-Forwarded-For` sonst selbst auswerten und
`request.client` ueberschreiben, sobald die Gegenstelle in seiner eigenen
Trust-Liste steht — die Entscheidung gehoert an EINE Stelle
(`AUTO_KI_TRUSTED_PROXY_*`). Wird der Start-Befehl geaendert, muss dieses Flag
erhalten bleiben.

Solange Schritt 4 nicht bestaetigt ist, bleibt `AUTO_KI_TRUSTED_PROXY_HOPS=0`:
dann zaehlt weiterhin nur die Proxy-Adresse (geteiltes Limit), aber niemand kann
seine IP faelschen.

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

## 2. Frontend (Railway, eigenes Docker-Image)
Das Frontend-Repo (`auto-ki-web`) hat ein eigenes `Dockerfile` + `railway.json`
(zweistufiger Build: Vite-Build → nginx liefert `dist/` aus, inkl. SPA-Fallback
für react-router-dom `BrowserRouter` und Security-Headern).

1. Neues Railway-Projekt/Service → **Deploy from GitHub repo** → `auto-ki-web`.
2. Railway erkennt `Dockerfile` + `railway.json` automatisch.
3. **Build Args setzen** (Service → Settings → Build) — Vite bettet diese zur
   BUILD-Zeit ins JS-Bundle ein, NICHT zur Laufzeit:
   - `VITE_API_BASE_URL=https://<railway-backend-domain>`
   - `VITE_API_KEY=<AUTO_KI_API_KEY>` (identisch zum Backend — öffentlich lesbar;
     NIEMALS `AUTO_KI_ADMIN_API_KEY` als Build-Arg setzen)
   Ändert sich einer der beiden Werte später, reicht ein Redeploy NICHT —
   das Image muss neu gebaut werden (Build Args wirken nur beim Build).
4. Nach dem Deploy die Frontend-Domain in Backend-`AUTO_KI_CORS_ORIGINS`
   und `FRONTEND_URL` eintragen (Backend neu deployen, damit CORS greift).

---

## 3. Backup & Restore
- Zwei Backup-Auslöser, beide schreiben eine konsistente, datierte Kopie nach
  `AUTO_KI_DB_BACKUP_DIR` (`/data/backups`), die letzten 10 Versionen bleiben
  erhalten:
  1. **Ereignisgesteuert:** nach jedem Fahrzeug-Admin-Schreibvorgang
     (save_fahrzeug/patch_luecken).
  2. **Periodisch:** alle `AUTO_KI_DB_BACKUP_INTERVAL_SECONDS` (Default 6h) —
     deckt Nutzerregistrierungen, Chats, Checks und Käufe ab, die sonst
     zwischen zwei Admin-Aktionen ungesichert geblieben wären.
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
