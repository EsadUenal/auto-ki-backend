# Vira — Security-Header- & Backup-Audit, Runde 2

Stand: 2026-07-09. Umfasst ausschließlich die zwei angefragten Bereiche —
keine UI-, Businesslogik- oder Performance-Änderungen. Gilt für
`auto-ki-backend` (dieses Repo) und `auto-ki-web` (Frontend-Repo).

---

## Teil 1: Security-Header-Audit

### Aktueller Zustand (nach dieser Runde)

| Header | Backend | Frontend (nginx) |
|---|---|---|
| X-Content-Type-Options | `nosniff` | `nosniff` |
| X-Frame-Options | `DENY` | `DENY` |
| Referrer-Policy | `strict-origin-when-cross-origin` | `strict-origin-when-cross-origin` |
| Strict-Transport-Security | `max-age=63072000; includeSubDomains` | `max-age=63072000; includeSubDomains` |
| Permissions-Policy | `geolocation=(), microphone=(), camera=()` **(neu)** | `geolocation=(), microphone=(), camera=()` |
| Cross-Origin-Opener-Policy | `same-origin` **(neu)** | `same-origin` **(neu)** |
| Cross-Origin-Resource-Policy | *bewusst nicht gesetzt* | `same-origin` **(neu)** |
| Cross-Origin-Embedder-Policy | *bewusst nicht gesetzt* | *bewusst nicht gesetzt* |
| Content-Security-Policy | *bewusst nicht aktiviert* | *bewusst nicht aktiviert* |

### Gefundene Probleme (vor dieser Runde)

1. Backend hatte kein `Permissions-Policy` (Frontend schon) — Inkonsistenz.
2. Weder Backend noch Frontend setzten `Cross-Origin-Opener-Policy`.
3. Weder Backend noch Frontend setzten `Cross-Origin-Resource-Policy`.
4. `Cross-Origin-Embedder-Policy` und `Content-Security-Policy` fehlten
   komplett (wie erwartet — noch nie aktiviert).

### Behoben

- **Permissions-Policy** auf dem Backend ergänzt (rein additiv, auf
  JSON-Antworten praktisch wirkungslos, aber konsistent).
- **Cross-Origin-Opener-Policy: same-origin** auf beiden Seiten ergänzt.
  Geprüft vor dem Einbau: kein `window.open()` im Frontend-Code, Stripe-
  Checkout läuft als Full-Page-Redirect (`window.location.href = url`,
  keine Popup-Abhängigkeit), externe Links (`ErsatzteileView.tsx`,
  `SourceBadge.tsx`) nutzen bereits `rel="noopener noreferrer"`. Keine
  Interaktion mit Stripe, Railway oder Gemini identifiziert (Gemini/Tavily
  laufen ausschließlich Server-zu-Server, nie im Browser).
- **Cross-Origin-Resource-Policy: same-origin** — **nur auf dem
  Frontend** ergänzt.

### Bewusst NICHT ergänzt (mit Begründung)

- **Cross-Origin-Resource-Policy auf dem Backend**: Das Frontend lädt
  E-Book-PDFs (und alle JSON-Antworten) per `fetch()` mit
  `credentials:'include'` **cross-origin** (Frontend- und Backend-Domain
  sind unterschiedlich). `same-origin`/`same-site` auf dem Backend hätte
  diese Downloads geblockt — ein bestätigtes Regressionsrisiko, kein
  theoretisches. Deshalb bewusst ausgelassen.
- **Cross-Origin-Embedder-Policy** (beide Seiten): `require-corp` würde
  verlangen, dass JEDE Cross-Origin-Antwort (Chat, Auth, Käufe — alles
  cross-origin zwischen Frontend und Backend) einen kompatiblen CORP-
  Header trägt. Da das Backend bewusst keinen CORP-Header setzt (s.o.),
  würde COEP auf dem Frontend sofort sämtliche API-Aufrufe blockieren.
  Kein technischer Nutzen (kein SharedArrayBuffer/WASM-Threading im
  Einsatz) steht diesem Risiko gegenüber — ausgelassen.
- **Content-Security-Policy**: wie angewiesen nicht aktiviert.

### CSP-Vorschlag für später (nicht implementiert)

Wenn CSP künftig aktiviert werden soll, zuerst im **Report-Only-Modus**
(`Content-Security-Policy-Report-Only`) mehrere Tage laufen lassen und
die Browser-Konsole/Report-Endpoint auf Verstöße prüfen, bevor auf
Enforcement umgestellt wird. Ausgangspunkt (ungetestet, nur Vorschlag):

```
default-src 'self';
script-src 'self';
style-src 'self' 'unsafe-inline';
img-src 'self' data:;
connect-src 'self' https://<backend-domain>;
frame-ancestors 'none';
base-uri 'self';
form-action 'self';
```

Begründung der einzelnen Direktiven:
- `style-src 'unsafe-inline'` ist nötig, weil React inline `style={{...}}`
  verwendet (kein Nonce-System im Build vorhanden) — ohne das bricht das
  komplette Layout.
- `img-src data:` ist nötig für base64-kodierte Bilder (Kaufcheck/
  Verkaufscheck-Screenshots werden als `bild_base64` verarbeitet, ggf.
  auch clientseitig als Vorschau gerendert).
- `connect-src` muss die tatsächliche Backend-Domain enthalten, sonst
  schlagen alle `fetch()`-Aufrufe fehl.
- `script-src 'self'` **ohne** `unsafe-inline`/`unsafe-eval` sollte mit
  einem Standard-Vite-Build funktionieren (kein bekannter Inline-Script-
  Bedarf), ist aber vor Enforcement zu verifizieren.
- Kein `frame-src`/`child-src` nötig — keine Iframes im Code gefunden.
- Falls Stripe.js jemals clientseitig eingebunden wird (aktuell nicht der
  Fall — reiner Redirect-Flow), müsste `script-src`/`connect-src`/
  `frame-src` um `https://js.stripe.com` bzw. `https://checkout.stripe.com`
  erweitert werden.

### Verifikation

- Backend: `TestClient`-Aufruf gegen `/health` zeigt alle sechs
  erwarteten Header korrekt, CORP/COEP/CSP bestätigt abwesend.
- Frontend: `nginx.conf`-Syntax gegen das bestehende, bereits produktiv
  genutzte Direktiven-Muster im selben Server-Block abgeglichen (kein
  Docker/nginx-Binary in dieser Umgebung verfügbar — siehe bereits im
  Deployment-Report dokumentierte offene Docker-Build-Verifikation).
- Bestehende Regressionstests (`test_security_audit.py`,
  `test_abo_guard.py`, `test_einwilligung.py`) weiterhin grün.

### Verbleibende Risiken

- CSP bleibt deaktiviert — kein Schutz gegen XSS-basiertes Script-
  Injection über Content-Security-Policy (andere Schutzmaßnahmen wie
  React's eingebautes Escaping greifen weiterhin).
- COEP bleibt deaktiviert — keine Cross-Origin-Isolation (irrelevant
  ohne SharedArrayBuffer/WASM-Threading, aktuell nicht genutzt).
- nginx-Konfiguration nie gegen ein echtes nginx-Binary getestet (Docker
  nicht verfügbar in dieser Umgebung) — bereits bekannter, dokumentierter
  offener Punkt vor dem ersten Live-Deploy.

### Deployment Ready (Security-Header): **JA**

---

## Teil 2: Backup-Audit

### Aktueller Zustand

- **Speicherort**: `AUTO_KI_DB_BACKUP_DIR` (Env-Var). Im Docker-Image auf
  `/data/backups` vorbelegt. Funktioniert auf Railway **dauerhaft nur**,
  wenn ein persistentes Volume auf `/data` gemountet ist (bereits in
  DEPLOYMENT.md/GO_LIVE_CHECKLIST.md als Pflichtschritt dokumentiert) —
  ohne Volume ist `/data` ephemer und jedes Backup geht bei Redeploy/
  Neustart verloren.
- **Auslöser**: zwei, beide schreiben in dasselbe Verzeichnis:
  1. Ereignisgesteuert — nach jedem Fahrzeug-Admin-Schreibvorgang
     (`save_fahrzeug`/`patch_luecken`).
  2. Periodisch — alle `AUTO_KI_DB_BACKUP_INTERVAL_SECONDS` (Default 6h),
     deckt auch reinen Nutzer-Traffic (Accounts, Chats, Käufe) ab.
- **Aufbewahrung**: letzte 10 Versionen (`_BACKUP_KEEP = 10`), ältere
  werden automatisch anhand des sortierten Dateinamens (Zeitstempel)
  gelöscht.
- **Mechanismus**: `sqlite3.Connection.backup()` — die offizielle SQLite-
  Online-Backup-API (page-level, korrekt für WAL-Mode-Quelldatenbanken
  konzipiert), keine rohe Dateikopie.

### Gefundene Probleme (vor dieser Runde)

1. **Keine Integritätsprüfung nach dem Schreiben.** Ein durch einen
   I/O-Fehler (Diskfehler, abgebrochener Schreibvorgang) beschädigtes
   Backup wäre unbemerkt als "neuestes Backup" in der Rotation
   verblieben — entdeckt erst im echten Notfall beim Restore-Versuch.
2. **`PRAGMA integrity_check` kann selbst eine Exception werfen** (nicht
   nur ein "not ok"-Ergebnis liefern) bei schwerer Korruption — beim
   Nachbau dieses Szenarios im Test bestätigt
   (`sqlite3.DatabaseError: database disk image is malformed`). Ein
   naiver Integritäts-Check hätte das nicht abgefangen.
3. **`migrate_db.py --restore` hatte keinen Fallback**: Bei korruptem
   neuestem Backup brach der Restore-Vorgang sofort ab (`sys.exit(1)`),
   selbst wenn 9 weitere, intakte Backups im selben Verzeichnis lagen.

### Behoben

- `_backup_sqlite()` (`app/db_writer.py`) führt nach jedem Schreiben
  `PRAGMA integrity_check` aus. Bei Nicht-"ok" **oder** einer harten
  `sqlite3.DatabaseError` wird die Backup-Datei sofort gelöscht und als
  Fehler geloggt — sie landet nie in der Rotation, nie als "neuestes
  Backup" für einen künftigen Restore-Versuch.
- `migrate_db.py --restore` (`cmd_restore()`) durchläuft jetzt alle
  vorhandenen Backups neuest-zuerst und verwendet automatisch das erste
  mit `integrity_check == "ok"`, statt bei einem korrupten neuesten
  Backup abzubrechen. Dieselbe DatabaseError-Falle wurde auch hier
  abgefangen.

### Beantwortung der einzelnen Prüffragen

| Frage | Antwort |
|---|---|
| Wo werden Backups gespeichert? | `AUTO_KI_DB_BACKUP_DIR` (Default `/data/backups` im Docker-Image) |
| Funktioniert das auf Railway dauerhaft? | Nur mit gemountetem Volume auf `/data` (Pflicht, bereits dokumentiert) |
| Wie viele Backups werden aufgehoben? | 10 |
| Werden alte automatisch gelöscht? | Ja, nach jeder erfolgreichen Sicherung |
| Was passiert bei einem Fehler? | Wird geloggt (`log.warning`/`log.error`), nie weitergeworfen |
| Kann die Anwendung trotzdem weiterlaufen? | Ja — durchgängig non-raising, sowohl im periodischen Task als auch im ereignisgesteuerten Pfad |
| Kann ein Backup beschädigt werden? | Theoretisch ja (I/O-Fehler) — wird jetzt erkannt und die Datei sofort verworfen (vorher: unbemerkt) |
| Kann ein Backup während eines Schreibvorgangs entstehen? | Ja, by design — sicher dank SQLite-Online-Backup-API (page-level, WAL-aware) |
| Ist der Restore-Prozess dokumentiert? | Ja: Kurzanleitung in DEPLOYMENT.md + getestetes Skript `migrate_db.py --restore` mit doppelter Integritätsprüfung (vor + nach Restore) und jetzt automatischem Fallback |
| Gibt es Race Conditions? | Aktuell **nein** — Begründung unten |
| Ist SQLite-WAL berücksichtigt? | Ja — `get_conn()` aktiviert WAL, Backup-API ist WAL-sicher |

### Race-Condition-Analyse (kein Code-Fix — kein Bug gefunden)

Beide Backup-Auslöser (periodischer `asyncio`-Task in `app.main` sowie
die `async def`-Admin-Endpunkte, die `save_fahrzeug`/`patch_luecken`
synchron aufrufen) laufen **blockierend auf demselben Event-Loop-Thread**.
Bei einem einzelnen uvicorn-Worker (`railway.json`: `numReplicas: 1`)
können sie sich dadurch strukturell nie zeitlich überlappen — Python
kann in einem Event-Loop nicht zwei blockierende Aufrufe gleichzeitig
ausführen. Es gibt daher aktuell **keine** Race Condition auf die
Zieldatei (Zeitstempel-Kollision bei zwei Backups in derselben Minute
ist ausgeschlossen, weil sie nie gleichzeitig laufen können).

**Diese Sicherheit ist an die aktuelle Architektur gekoppelt**, nicht an
einen expliziten Lock-Mechanismus:
- Sie hält, solange alle DB-schreibenden Endpunkte `async def` bleiben
  (nicht `def` — sonst würde Starlette sie in einen Thread-Pool
  auslagern, was echte Parallelität zum Event-Loop-Thread einführen
  würde).
- Sie hält, solange `numReplicas: 1` bleibt. Bei horizontaler Skalierung
  (mehrere Container-Instanzen) liefe je Instanz ein eigener periodischer
  Backup-Task — zwei Instanzen könnten dann bei identischem
  Minuten-Zeitstempel kollidieren. Das ist durch die ohnehin bestehende
  SQLite-Single-Writer-Architektur bereits ausgeschlossen (`numReplicas:
  1` ist für dieses Projekt kein Optimierungs-, sondern ein
  Korrektheits-Constraint, bereits in DEPLOYMENT_REPORT.md vermerkt).

Kein Code-Fix vorgenommen, da kein tatsächlicher Bug vorliegt — nur zur
Vollständigkeit dokumentiert, falls sich die Architektur künftig ändert.

### Verifikation

Alle Tests mit isolierten Temp-Verzeichnissen und echten SQLite-Dateien
(kein Mock der eigentlichen Backup-Logik):
- Erfolgspfad: Backup wird erstellt, `integrity=ok`, bleibt erhalten.
- Korruptes Backup (simulierte `DatabaseError` beim `integrity_check`)
  wird von der echten `_backup_sqlite()`-Funktion erkannt und gelöscht.
- `cmd_restore()` weicht bei korruptem neuestem Backup automatisch auf
  das nächst-ältere, intakte Backup aus — End-to-End mit drei
  Backup-Dateien getestet (eine davon absichtlich korrumpiert).
- Bestehende Regressionstests (`test_security_audit.py`,
  `test_abo_guard.py`, `test_einwilligung.py`) sowie App-Start/
  Healthcheck weiterhin grün.
- `migrate_db.py --list` gegen die echten lokalen Backups lief
  unverändert fehlerfrei durch.

### Verbleibende Risiken

- **Backups liegen auf demselben Volume wie die Live-DB** (bereits im
  ersten Deployment-Report als Risiko vermerkt) — ein Volume-Totalausfall
  nimmt beides gleichzeitig mit. Externe Off-Site-Sicherung weiterhin
  nicht Teil dieses Repos (siehe GO_LIVE_CHECKLIST.md Punkt 7).
  Kein Code-Fix möglich, erfordert eine Railway-Volume-Snapshot- oder
  Off-Site-Kopie-Lösung außerhalb der Anwendung.
- Die Race-Freiheit ist strukturell, nicht durch einen expliziten Lock
  erzwungen (s.o.) — bei künftigen Architekturänderungen (Multi-Replica,
  Thread-Pool-Endpunkte für DB-Writes) erneut prüfen.
- Kein automatisierter, wiederkehrender Restore-Test (Disaster-Recovery-
  Drill) — nur der einmalige manuelle Testlauf in dieser Session.

### Deployment Ready (Backup): **JA**

---

## Gesamtfazit dieser Runde

Beide Bereiche sind **deployment-ready**. Alle in dieser Runde gefundenen
Probleme wurden minimal und ohne Refactoring behoben, jeweils mit
begründetem Vorher/Nachher, isoliertem Regressionstest und eigenem
Commit. Keine UI-, Businesslogik- oder Performance-Änderungen
vorgenommen.
