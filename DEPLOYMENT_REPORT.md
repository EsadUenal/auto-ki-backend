# Vira — Deployment-Report

Stand: 2026-07-09. Gilt für `auto-ki-backend` (dieses Repo) und `auto-ki-web`
(Frontend-Repo). Umfasst den vollständigen Deployment-Audit + alle im Zuge
dessen behobenen produktionsrelevanten Probleme.

---

## Deployment Ready: **JA, mit Auflagen**

Beide Repos sind technisch deploybar (Dockerfile + railway.json vorhanden,
Build getestet, Healthchecks funktionieren, Security-Header aktiv, Backup
läuft periodisch). Die verbleibenden Blocker sind **keine Code-Probleme**,
sondern fehlende externe Voraussetzungen (Live-Secrets, Impressum-Text,
Docker-Build-Verifikation) — siehe unten.

---

## Was in diesem Audit geprüft und behoben wurde

| # | Fund | Schweregrad | Status |
|---|------|-------------|--------|
| 1 | Frontend-`npm run build` schlug komplett fehl (tsc-Fehler in `perf-diag.ts` + fehlende `vite-env.d.ts`) — **kein Deploy möglich** | 🔴 Kritisch | ✅ Behoben |
| 2 | Kein Dockerfile/railway.json fürs Frontend — Railway kann es gar nicht deployen | 🔴 Kritisch | ✅ Behoben |
| 3 | Kein SPA-Fallback vorgesehen — `BrowserRouter` + statischer Fileserver hätte bei jedem Reload/Deep-Link auf `/agb`, `/datenschutz` etc. einen nackten 404 geliefert | 🔴 Kritisch | ✅ Behoben (nginx `try_files`) |
| 4 | API sendete keine Security-Header (X-Content-Type-Options, X-Frame-Options, HSTS, Referrer-Policy) | 🟠 Hoch | ✅ Behoben (Backend-Middleware + Frontend-nginx) |
| 5 | Backup lief nur bei Fahrzeug-Admin-Schreibvorgängen — Nutzerdaten (Accounts, Chats, Käufe) konnten tagelang ungesichert bleiben | 🟠 Hoch | ✅ Behoben (periodischer Timer, Default 6h) |
| 6 | `.gitignore` deckte nur `.env`, nicht `.env.*` — `.env.stripe` wurde committet (geprüft: enthält nur Variablennamen als Anleitung, **keine echten Secrets**) | 🟡 Mittel | ✅ Behoben |
| 7 | Kein Start-Warnhinweis, wenn `AUTO_KI_CORS_ORIGINS` vergessen wird — stiller Totalausfall des Frontends (nur im Browser sichtbar, nicht im Server-Log) | 🟡 Mittel | ✅ Behoben |
| 8 | DEPLOYMENT.md verwies auf nicht existierende Frontend-Deploy-Optionen | 🟢 Niedrig | ✅ Behoben |

Alle Fixes sind einzeln committet (siehe Git-Log beider Repos), mit
Begründung, Regressionstest und Verifikation in der jeweiligen Commit-Message.

---

## Offene Blocker (externe Voraussetzungen, kein Code)

Diese können **nicht durch Code gelöst werden** — sie erfordern Aktionen
außerhalb des Repos:

1. **Impressum-Text fehlt** — Betreiber-Angabe folgt nach Gewerbeanmeldung
   (laut Nutzer). Rechtlich zwingend vor Live-Schaltung in Deutschland
   (§5 TMG / §5 DDG). Bis dahin zeigt `/impressum` nur den Platzhalter.
2. **Docker-Build nie tatsächlich ausgeführt** — in dieser Umgebung war kein
   Docker-Daemon verfügbar. `npm run build` und der Python-App-Import/
   Startup/Shutdown-Zyklus wurden verifiziert, der eigentliche
   `docker build`-Schritt (beide Images) noch nicht. Muss vor dem ersten
   Live-Deploy einmal lokal oder über Railways Build-Log geprüft werden.
3. **Alle Produktions-Secrets fehlen noch** — `AUTO_KI_JWT_SECRET`,
   `AUTO_KI_API_KEY`, `AUTO_KI_CORS_ORIGINS`, Stripe-Live-Keys,
   Gemini/Tavily-Keys. Die App warnt beim Start laut, wenn diese fehlen,
   startet aber trotzdem (fail-open) — s. Go-Live-Checkliste.
4. **Stripe läuft noch im Testmodus** (dokumentiert in `STRIPE_GOLIVE.md`,
   vorheriger Audit) — Umstellung auf Live-Keys + Live-Webhook ist ein
   separater, bewusster Schritt in der Go-Live-Checkliste.
5. **Daten-Seeding des Produktions-Volumes** — ein frisches Railway-Volume
   ist leer; die 469 Baureihen + ChromaDB-Embeddings müssen einmalig
   eingespielt werden (Verfahren in DEPLOYMENT.md Abschnitt 1.4).

---

## Kritische Risiken

- **SQLite als Produktions-DB bei `numReplicas=1`**: bewusste Design-
  Entscheidung (dokumentiert), funktioniert für den erwarteten Traffic,
  aber **keine horizontale Skalierung möglich**, ohne die Persistenzschicht
  zu wechseln. Kein Fix nötig für den Start, aber ein bekanntes
  Wachstumslimit.
- **Backups liegen auf demselben Volume wie die Live-DB** (dokumentiert in
  DEPLOYMENT.md). Ein Volume-Totalausfall nimmt Live-Daten UND Backups
  gleichzeitig mit. Für echte Ausfallsicherheit fehlt eine **externe**
  (Off-Site-)Sicherung — das ist mit Bordmitteln dieses Repos nicht lösbar,
  sondern erfordert einen Railway-Volume-Snapshot-Job oder einen externen
  Cron, der `/data/backups` regelmäßig woanders hin kopiert.
- **Kein automatisiertes Frontend-Test-Setup** (kein `npm test` / CI-Job) —
  Regressionen im Frontend wurden in dieser Session nur manuell per Preview
  verifiziert, nicht durch eine automatisierte Test-Pipeline abgesichert.

## Mittlere Risiken

- **Docker-Build unverifiziert** (s.o., Punkt 2 der offenen Blocker) —
  bei Bedarf vor dem ersten Deploy `docker build .` in beiden Repos lokal
  ausführen, um Build-Zeit-Fehler früh statt erst im Railway-Build-Log zu
  entdecken.
- **Frontend-Bundle-Größe**: `vite build` meldet einen Chunk >500 KB
  (526 KB minifiziert). Kein Blocker (auf Wunsch des Nutzers keine
  Performance-Optimierung vorgenommen), aber beeinflusst Ladezeit auf
  langsamen Verbindungen.
- **`public/cars/` ist ~196 MB groß** — vergrößert den Docker-Build-Context
  und damit Build-Zeit/Image-Größe spürbar. Ebenfalls unangetastet gelassen
  (keine Performance-/Asset-Optimierung angefragt).

## Niedrige Risiken

- **`.env.stripe` bleibt getrackt** (bewusst, da reine Anleitung ohne echte
  Werte) — theoretisch verwirrend, dass eine `.env.*`-Datei im Repo liegt,
  aber inhaltlich unbedenklich.
- **Kein `engines`-Feld in `package.json`** — die Node-Version ist über das
  Dockerfile (`node:20-alpine`) fest vorgegeben, lokale Entwickler-Umgebungen
  könnten theoretisch von einer anderen Node-Version abweichen.

---

## Was vor dem ersten Live-Deploy noch erledigt werden muss

1. Docker-Build beider Images einmal lokal verifizieren (`docker build .`
   in `auto-ki-backend` und `auto-ki-web`).
2. Alle Produktions-Secrets in Railway setzen (siehe Go-Live-Checkliste).
3. Persistentes Volume auf `/data` im Backend-Service anlegen.
4. Produktions-Datenbank + ChromaDB einmalig aufs Volume einspielen
   (DEPLOYMENT.md Abschnitt 1.4).
5. Stripe auf Live-Modus umstellen + Live-Webhook einrichten.
6. Domain(s) verbinden, DNS setzen, HTTPS/Zertifikat prüfen.
7. Frontend-Build-Args (`VITE_API_BASE_URL`, `VITE_API_KEY`) korrekt setzen
   und Image bauen lassen.
8. Smoke-Test + Testkauf im Live-Stripe-Modus durchführen.
9. Impressum nachreichen, sobald Gewerbeanmeldung vorliegt.

Details dazu in der beiliegenden `GO_LIVE_CHECKLIST.md`.
