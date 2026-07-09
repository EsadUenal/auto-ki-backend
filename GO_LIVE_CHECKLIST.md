# Vira — Go-Live-Checkliste

Alle Punkte, die **unmittelbar vor dem ersten Live-Deploy** durchgeführt
werden müssen. Reihenfolge ist absichtlich so gewählt (Backend vor Frontend,
Daten vor Zahlungen vor Traffic).

## 1. Railway — Backend-Service

- [ ] Neues Railway-Projekt → Service aus `auto-ki-backend`-Repo anlegen
      (Dockerfile + railway.json werden automatisch erkannt).
- [ ] **Persistentes Volume** anlegen, Mount-Pfad `/data` (PFLICHT — sonst
      sind SQLite + ChromaDB + Backups nach jedem Redeploy weg).
- [ ] Environment-Variablen setzen:
  - [ ] `AUTO_KI_JWT_SECRET` — `openssl rand -hex 32`
  - [ ] `AUTO_KI_API_KEY` — `openssl rand -hex 32` (anderer Wert als JWT-Secret)
  - [ ] `AUTO_KI_CORS_ORIGINS` — echte Frontend-Domain(s), z. B.
        `https://vira.de,https://www.vira.de`
  - [ ] `AUTO_KI_DB_PATH=/data/auto_ki.db` (im Dockerfile bereits Default)
  - [ ] `AUTO_KI_CHROMA_PATH=/data/chroma` (im Dockerfile bereits Default)
  - [ ] `AUTO_KI_DB_BACKUP_DIR=/data/backups` (im Dockerfile bereits Default)
  - [ ] `AUTO_KI_DB_BACKUP_INTERVAL_SECONDS` — Default 21600 (6h) i.d.R. ok
  - [ ] `FRONTEND_URL` — echte Frontend-URL (für Stripe-Redirects)
- [ ] Nach dem ersten erfolgreichen Deploy: Daten-Seeding durchführen
      (`railway run python rebuild_chroma.py` nachdem `auto_ki.db` aufs
      Volume kopiert wurde — siehe DEPLOYMENT.md Abschnitt 1.4).
- [ ] `GET /health` prüfen → `tables` zeigt alle Fachtabellen, `db_path`
      zeigt `/data/auto_ki.db`.

## 2. Railway — Frontend-Service

- [ ] Zweiten Railway-Service aus `auto-ki-web`-Repo anlegen (eigenes
      Dockerfile + railway.json).
- [ ] **Build Args** setzen (Service → Settings → Build — NICHT normale
      Runtime-Variablen, Vite bettet sie zur Build-Zeit ins Bundle ein):
  - [ ] `VITE_API_BASE_URL` = echte Backend-Domain (`https://api.vira.de` o.ä.)
  - [ ] `VITE_API_KEY` = identisch zu `AUTO_KI_API_KEY` oben
- [ ] Nach dem Deploy: Backend-`AUTO_KI_CORS_ORIGINS`/`FRONTEND_URL` mit der
      finalen Frontend-Domain abgleichen, Backend redeployen falls nötig.
- [ ] Docker-Build **lokal vorab verifizieren**: `docker build .` in beiden
      Repos — in dieser Session konnte der Build mangels Docker-Daemon nicht
      ausgeführt werden.

## 3. Domain & DNS

- [ ] Domain(s) beschafft/vorhanden.
- [ ] In Railway: Custom Domain je Service hinterlegen (Backend + Frontend,
      ggf. Subdomain `api.` fürs Backend).
- [ ] DNS-Einträge (CNAME auf die von Railway angezeigte Ziel-Domain) beim
      Domain-Registrar setzen.
- [ ] DNS-Propagation abwarten (`dig`/`nslookup` prüfen).

## 4. HTTPS

- [ ] Railway stellt TLS-Zertifikate automatisch aus, sobald DNS korrekt
      zeigt (Let's Encrypt) — kein eigenes Zertifikat/nginx-TLS-Setup nötig.
- [ ] Prüfen: `https://` beider Domains lädt ohne Zertifikatswarnung.
- [ ] Prüfen: HTTP → HTTPS-Redirect funktioniert (Railway macht das
      standardmäßig).

## 5. API-Keys (Produktions-Werte)

- [ ] **Gemini**: `GEMINI_API_KEY` — Produktions-/Billing-fähiger Key
      (nicht der Dev-/Free-Tier-Key, falls unterschiedlich).
- [ ] **Tavily**: `TAVILY_API_KEY` — Kontingent für erwarteten Live-Traffic
      prüfen (Free-Plan: 1.000 Abfragen/Monat).

## 6. Stripe Live

- [ ] Stripe-Dashboard auf **Live-Modus** umschalten.
- [ ] Live-Produkte + -Preise anlegen (LIGHT/PRO/MAX/Einzelkauf) — Live-
      Price-IDs unterscheiden sich von den Test-IDs.
- [ ] Env-Variablen in Railway auf Live-Werte umstellen:
  - [ ] `STRIPE_SECRET_KEY` = `sk_live_...`
  - [ ] `STRIPE_PRICE_LIGHT` / `STRIPE_PRICE_PRO` / `STRIPE_PRICE_MAX` /
        `STRIPE_PRICE_EINZELKAUF` = Live-Price-IDs
- [ ] **Stripe-Webhook (Live)**:
  - [ ] Stripe-Dashboard (Live-Modus) → Entwickler → Webhooks → Endpoint
        hinzufügen: `https://<backend-domain>/api/v1/payments/webhook`
  - [ ] Events: mindestens `checkout.session.completed`, `invoice.paid`,
        `customer.subscription.deleted`
  - [ ] Erzeugtes `whsec_...` als `STRIPE_WEBHOOK_SECRET` in Railway setzen.
- [ ] Bekannten Launch-Guard prüfen: Parallel-Abo-Sperre (`_hat_laufendes_abo`)
      ist bereits aktiv (aus vorherigem Audit) — keine weitere Aktion nötig,
      nur zur Erinnerung im Hinterkopf behalten.

## 7. Backup & Recovery

- [ ] Periodisches Backup läuft automatisch (alle 6h, siehe Report) —
      keine manuelle Aktion nötig, nur bestätigen: nach ~10 Min Betrieb
      Log auf `SQLite-Backup erstellt` prüfen (bei kürzerem Testintervall
      via `AUTO_KI_DB_BACKUP_INTERVAL_SECONDS` temporär verifizieren).
- [ ] **Externe Sicherung einrichten** — Backups liegen sonst auf demselben
      Volume wie die Live-DB (Single Point of Failure). Empfehlung: Railway-
      Volume-Snapshot aktivieren, falls verfügbar, oder ein periodischer Job,
      der `/data/backups` extern kopiert.
- [ ] Restore-Weg einmal **trocken durchspielen** (auf einem Test-Service,
      nicht live): neueste `auto_ki_backup_*.db` → `/data/auto_ki.db`,
      Service neu starten, `rebuild_chroma.py`, `/health` prüfen.

## 8. Monitoring & Logging

- [ ] Railway-Log-Stream für beide Services im Blick behalten (insbesondere
      die Start-Warnungen bei fehlenden Secrets — `AUTO_KI_API_KEY`,
      `AUTO_KI_JWT_SECRET`, `AUTO_KI_CORS_ORIGINS` — dürfen beim Live-Start
      NICHT mehr erscheinen).
- [ ] `AUTO_KI_LOG_LEVEL=INFO` (Default) — bei Bedarf temporär auf `DEBUG`
      für die erste Live-Stunde, danach zurück auf `INFO`.
- [ ] Kein externer APM/Error-Tracker (Sentry o.ä.) im Scope dieses Audits —
      falls gewünscht, ist das ein separates, bewusstes Vorhaben.

## 9. Healthcheck

- [ ] Backend: `GET https://<backend-domain>/health` → `{"status":"ok", ...}`,
      `tables` zeigt alle Fachtabellen.
- [ ] Frontend: `GET https://<frontend-domain>/healthz` → `ok` (nginx-Endpoint).
- [ ] Railway-Deploy-Status beider Services: "Healthy" (nicht nur "Deployed").

## 10. Smoke-Test (nach Go-Live, vor öffentlicher Bekanntgabe)

- [ ] Frontend lädt, kein CORS-Fehler in der Browser-Konsole.
- [ ] Registrierung eines Test-Accounts (AGB-Checkbox pflicht — aus
      vorherigem Audit bereits serverseitig erzwungen).
- [ ] Login funktioniert, Cookie wird gesetzt.
- [ ] Chat-Funktion liefert eine Antwort (bestätigt Gemini-Key + ChromaDB-
      Seeding funktionieren).
- [ ] Kaufcheck / Verkaufscheck einmal durchklicken.
- [ ] `/impressum`, `/datenschutz`, `/agb`, `/widerruf` laden direkt per
      URL (bestätigt SPA-Fallback funktioniert) — **Impressum zeigt noch
      Platzhalter, bis der Text nachgereicht wird**.

## 11. Testkauf (Stripe Live)

- [ ] Einen echten Testkauf mit einer eigenen Kreditkarte durchführen
      (kleinster Betrag, z. B. Einzelkauf).
- [ ] Zahlung im Stripe-Dashboard (Live-Modus) als erfolgreich sehen.
- [ ] Webhook-Zustellung im Stripe-Dashboard prüfen (Status 200, kein Retry).
- [ ] Bestellung erscheint korrekt in der App (E-Book-Download-Link /
      Abo-Status).
- [ ] Testkauf ggf. erstatten (Stripe-Dashboard → Zahlung → Erstatten).

---

**Erst wenn alle Punkte oben abgehakt sind, gilt Vira als live-bereit.**
Das offene Impressum blockiert die *technische* Inbetriebnahme nicht, aber
**rechtlich darf die Seite in Deutschland nicht ohne gültiges Impressum
öffentlich beworben/zugänglich gemacht werden** — das bitte vor der
tatsächlichen Bekanntgabe/Bewerbung nachholen.
