# ENFAL — Produktions-Deployment (Railway)

Reproduzierbare Anleitung für Backend (`auto-ki-backend`) und Frontend
(`auto-ki-web`). Stand: Deployment-Block, 2026-09-14. **Stripe bleibt im
Testmodus** — Live-Zahlungen sind ein eigener Release-Schritt.

Plattform-Aussagen stützen sich auf die offizielle Railway-Doku
(docs.railway.com: Volumes, Backups, Healthchecks, Domains, Public Networking
Specs & Limits, Config as Code, Dockerfiles). Was dort nicht steht, wird hier
nicht behauptet, sondern **live gemessen** (siehe Abschnitt 8).

---

## 1. Zielarchitektur

| Adresse | Railway-Service | Inhalt |
|---|---|---|
| `https://getenfal.de` | `enfal-web` (Frontend) | öffentliche Website — vorgerenderte Seiten `/`, `/autofinder`, `/autokosten`, `/pricing` |
| `https://app.getenfal.de` | `enfal-web` (derselbe Service) | dieselbe App; Antworten tragen `X-Robots-Tag: noindex` |
| `https://api.getenfal.de` | `enfal-api` (Backend) | FastAPI, Volume `/data` |
| `https://www.getenfal.de` | — | nur Weiterleitung auf `getenfal.de` (DNS-Ebene, siehe 6) |

**Ein** Frontend-Build für Website und App: Landingpage und App sind heute
dieselbe React-App (ein Router, ein Bundle). Eine zweite Codebasis wäre reiner
Mehraufwand. Die Trennung ist deshalb eine Host-Regel in `nginx.conf`:

- `getenfal.de` wird indexiert (Canonicals zeigen ohnehin dorthin).
- `app.getenfal.de` liefert dieselben Seiten mit `X-Robots-Tag: noindex, nofollow`.
- Login funktioniert auf **beiden** Hosts gleich (Cookie liegt host-only auf
  `api.getenfal.de`, siehe 7).

Das ist die bewusst gewählte **Zwischenlösung**. Eine harte Trennung (App-Pfade
auf `getenfal.de` → 301 auf `app.getenfal.de`) bräuchte eine gepflegte Liste
aller App-Routen in nginx und ist kein Deployment-Blocker. Später als eigener
Schritt.

Datenhaltung: **SQLite + ChromaDB auf einem Railway-Volume** (`/data`).
Für RC1/Closed Beta vertretbar: 4 MB Datenbank, WAL-Modus, `busy_timeout` 5 s,
ein Prozess, eine Replica. Keine PostgreSQL-Migration. Grenze: Railway erlaubt
**keine Replicas mit Volumes** und hat bei jedem Redeploy eine kurze Downtime
(offizielle Doku) — für eine Beta akzeptabel, für Skalierung später neu bewerten.

---

## 2. Voraussetzungen (einmalige Nutzeraktionen)

1. Railway-Konto + Plan. **Hobby reicht** für diese Architektur:
   2 Custom Domains pro Service (Frontend: `getenfal.de` + `app.getenfal.de`,
   Backend: `api.getenfal.de`), Volume bis 5 GB (belegt: ~0,1 GB).
2. Railway mit GitHub verbinden (Zugriff auf `EsadUenal/auto-ki-backend` und
   `EsadUenal/auto-ki-web`).
3. DNS-Entscheidung für die Hauptdomain treffen (Abschnitt 6).
4. Secrets erzeugen (Abschnitt 4) und in Railway eintragen — nie ins Repo.

---

## 3. Backend-Service `enfal-api`

### 3.1 Anlegen
1. Railway-Projekt → **Deploy from GitHub repo** → `auto-ki-backend`, Branch `master`.
2. `Dockerfile` + `railway.json` werden erkannt: Healthcheck `/health`
   (Timeout 300 s), Restart `ON_FAILURE` (10 Versuche), `drainingSeconds: 90`,
   1 Replica.

### 3.2 Volume (Pflicht)
Service → **Volumes → New Volume**, Mount-Pfad **`/data`**.
Ohne Volume sind nach jedem Redeploy alle Nutzer, Käufe, Checks und Backups weg.

Railway hängt Volumes **root-eigen** ein; Images mit Nicht-root-Benutzer haben
dort laut Doku Rechteprobleme. Lösung im Image (nicht `RAILWAY_RUN_UID=0`):
`docker-entrypoint.sh` startet als root, korrigiert nur die Eigentümer unter
`/data` und wechselt dann per `setpriv` auf `appuser` (UID 10001). Die App läuft
nie als root.

### 3.3 Was auf dem Volume liegt (muss Redeploys überleben)

| Pfad | Inhalt |
|---|---|
| `/data/auto_ki.db` (+ `-wal`, `-shm`) | **alle** Nutzer- und Geschäftsdaten: Konten, Passwort-Hashes, E-Mail-Bestätigung, Check-Historie, Conversations/Messages, Kontingente (Kauf-/VerkaufsCheck, Plus-Zeitraum), Zahlungen (`kauf_zahlung`, `stripe_events`), E-Book-/Poster-Bestellungen, Einwilligungen, Händlerfahrzeuge, Nutzungszähler — plus der Fahrzeugbestand |
| `/data/chroma/` | Vektordatenbank (aus SQLite ableitbar) |
| `/data/backups/` | datierte SQLite-Backups (Abschnitt 10) |

Nicht persistent (bewusst): das ONNX-Embedding-Modell liegt im Image
(`/home/appuser/.cache`), E-Book-PDFs liegen im Image (`static/`).

### 3.4 Erster Start auf leerem Volume
- **SQLite** baut sich selbst: Schema, voller Fahrzeugbestand, Datenmigrationen
  (`ensure_tables()`, siehe `db/README_bootstrap.md`). Kein Upload nötig.
- **ChromaDB** wird automatisch aus SQLite aufgebaut — **im Hintergrund**,
  gemessen ~10–15 Minuten (≈21.000 Einträge × ~40 ms Embedding). Die App ist
  sofort bereit (Healthcheck grün), der Chat antwortet in dieser Zeit ohne
  Fließtext-Vektorwissen (DB-Fakten sind voll da). Log:
  `Chroma-Bootstrap: ... Neuaufbau aus N Baureihen` → `Chroma-Bootstrap abgeschlossen`.
  Wird der Container mittendrin beendet, beginnt der nächste Start neu (kein
  halber Index).
- Die alte Anleitung „DB hochladen / `railway run python rebuild_chroma.py`" ist
  überholt: `railway run` führt Befehle **lokal** mit Railway-Variablen aus,
  nicht im Container.

### 3.5 Variablen → Abschnitt 4. Danach **Deploy**.

---

## 4. ENV-Matrix

Legende: **P** = Pflicht in Produktion · **G** = geheim · **D** = Default ist in
Produktion in Ordnung. Werte hier sind **Beispiele**, nie echte Keys.
`openssl rand -hex 32` erzeugt ein 64-Zeichen-Secret.

### 4.1 Backend (`enfal-api`) — Pflicht / sicherheitsrelevant

| Variable | P | G | Beispiel / Produktionswert | Default ok? | Hinweis |
|---|---|---|---|---|---|
| `AUTO_KI_ENV` | ✔ | – | `production` | im Image fest | nicht überschreiben; jeder Wert ≠ `development` gilt als Produktion |
| `AUTO_KI_JWT_SECRET` | ✔ | ✔ | 64 Hex-Zeichen | ✘ | ≥ 32 Zeichen, ≠ API-Key — sonst Startverweigerung |
| `AUTO_KI_API_KEY` | ✔ | (öffentlich) | 48+ Zeichen | ✘ | steht als `VITE_API_KEY` im Frontend-Bundle, öffnet nur Consumer-Routen |
| `AUTO_KI_ADMIN_API_KEY` | für Proxy-Messung | ✔ | 64 Hex-Zeichen | leer = Admin zu | ≥ 32 Zeichen, ≠ Consumer-Key, **nie** als `VITE_` |
| `AUTO_KI_CORS_ORIGINS` | ✔ | – | `https://getenfal.de,https://app.getenfal.de` | ✘ (localhost) | nur `https://`, kein `*`/`null`/localhost — sonst Startverweigerung |
| `FRONTEND_URL` | ✔ | – | `https://app.getenfal.de` | ✘ | Stripe-Rückkehr + Bestätigungslinks; muss `https://` sein |
| `STRIPE_SECRET_KEY` | ✔ | ✔ | `sk_test_…` | – | **Testmodus**. `sk_live_`/`rk_live_` → Startverweigerung |
| `STRIPE_WEBHOOK_SECRET` | ✔ | ✔ | `whsec_…` (Test-Endpoint) | ✘ | ohne → Startverweigerung |
| `STRIPE_PRICE_KAUFCHECK` | ✔ | – | `price_…` (Test, 5,99 €) | – | |
| `STRIPE_PRICE_VERKAUFSCHECK` | ✔ | – | `price_…` (Test, 8,99 €) | – | |
| `STRIPE_PRICE_PLUS` | ✔ | – | `price_…` (Test, 16,99 € mtl.) | – | |
| `AUTO_KI_STRIPE_LIVE_ERLAUBT` | – | – | **nicht setzen** (= 0) | ✔ | erst im Schritt „Stripe LIVE" auf `1` |
| `GEMINI_API_KEY` | ✔ | ✔ | – | – | eigener Produktions-Key mit Budgetgrenze (Abschnitt 12) |
| `TAVILY_API_KEY` | ✔ | ✔ | `tvly-…` | – | dito |
| `AUTO_KI_SMTP_HOST` | ✔ für RC1 | – | `smtp.<anbieter>` | leer = kein Versand | siehe Abschnitt 9 |
| `AUTO_KI_SMTP_PORT` | – | – | `587` (STARTTLS) oder `465` (TLS) | 587 | nie Klartext |
| `AUTO_KI_SMTP_USER` | ✔ für RC1 | – | `noreply@getenfal.de` | – | |
| `AUTO_KI_SMTP_PASSWORD` | ✔ für RC1 | ✔ | – | – | |
| `AUTO_KI_MAIL_FROM` | – | – | `ENFAL <noreply@getenfal.de>` | = SMTP_USER | |
| `AUTO_KI_TRUSTED_PROXY_HOPS` | ✔ | – | `0` bis zur Messung | 0 | Abschnitt 8 |
| `AUTO_KI_CLIENT_IP_HEADER` | nach Messung | – | `x-real-ip` | `x-forwarded-for` | Abschnitt 8 |
| `AUTO_KI_TRUSTED_PROXY_NETS` | nach Messung | – | gemessenes Proxy-Netz | private Netze + 100.64.0.0/10 | Abschnitt 8 |
| `RAILWAY_DEPLOYMENT_DRAINING_SECONDS` | – | – | – | – | **nicht nötig**, `railway.json` setzt `drainingSeconds: 90` |

### 4.2 Backend — im Image gesetzt (nicht überschreiben)

| Variable | Wert im Image |
|---|---|
| `AUTO_KI_DB_PATH` | `/data/auto_ki.db` |
| `AUTO_KI_CHROMA_PATH` | `/data/chroma` |
| `AUTO_KI_DB_BACKUP_DIR` | `/data/backups` |
| `PORT` | von Railway injiziert (Fallback 8000) |

### 4.3 Backend — Betrieb, Defaults sind produktionstauglich

`AUTO_KI_LOG_LEVEL` (INFO) · `AUTO_KI_RATE_LIMIT` (20/minute) ·
`AUTO_KI_DB_BACKUP_INTERVAL_SECONDS` (21600 = 6 h) · `AUTO_KI_JWT_EXPIRE_DAYS` (7) ·
`AUTO_KI_EMAIL_VERIFIKATION` (1 — **nie 0 in Produktion**) ·
`AUTO_KI_EMAIL_VERIFIKATION_STUNDEN` (48) · `AUTO_KI_SMTP_TIMEOUT_SECONDS` (15) ·
`AUTO_KI_CHECK_VERSUCHE_PRO_TAG` (8) · `AUTO_KI_CHECK_EINGABE_MAX` /
`_ERGEBNIS_MAX` / `AUTO_KI_NACHRICHT_MAX` · Monatskontingente
`AUTO_KI_CHAT_FREE_LIMIT_MONATLICH` (20), `_PLUS_` (100),
`AUTO_KI_AUTOFINDER_FREE_LIMIT_MONATLICH` (5), `_PLUS_` (50),
`AUTO_KI_AUTOFINDER_ANONYM_DEMO_PRO_TAG` (1), `AUTO_KI_PLUS_KAUFCHECKS` (5),
`AUTO_KI_PLUS_VERKAUFSCHECKS` (1) · Modelle `AUTO_KI_LLM_MODEL`
(gemini-3.7-flash), `AUTO_KI_FAST_LLM_MODEL`, `AUTO_KI_IMAGE_MODEL` ·
Provider-Budgets `AUTO_KI_GEMINI_*`, `AUTO_KI_TAVILY_*`, `AUTO_KI_PROVIDER_*`
(siehe `PROVIDER_RELIABILITY.md`) · `AUTO_KI_ALLOWED_MARKET_SOURCES` (**leer
lassen** — Marktquellen-Freigabe ist eine eigene Etappe).

**Nicht setzen:** `STRIPE_PRICE_LIGHT/PRO/MAX/EINZELKAUF` (Altprodukte),
`MOBILE_DE_*` (Provider nicht freigegeben).

### 4.4 Frontend (`enfal-web`) — Build-Variablen

Vite bettet `VITE_*` **zur Build-Zeit** ins öffentliche Bundle ein. Railway
reicht Service-Variablen an die `ARG`s im Dockerfile durch. Nach einer Änderung
**neu bauen** (Redeploy mit Build), nicht nur neu starten.

| Variable | P | Wert | Hinweis |
|---|---|---|---|
| `VITE_API_BASE_URL` | ✔ | `https://api.getenfal.de` | Build bricht ab bei fehlend / nicht https / localhost |
| `VITE_API_KEY` | ✔ | = `AUTO_KI_API_KEY` | öffentlich lesbar — deshalb nur der Consumer-Key |

`scripts/verify-build.mjs` bricht den Build außerdem ab, wenn eine
`VITE_`-Variable einen verbotenen Namen trägt (`ADMIN`, `SECRET`, `STRIPE`,
`GEMINI`, `TAVILY`, `JWT`, `WEBHOOK`, `PASSWORD`, `SMTP`) oder einen Wert, der
wie ein echtes Secret aussieht (`sk_…`, `whsec_…`, `AIza…`, `tvly-…`), und
prüft das fertige `dist/` auf localhost-Adressen, Secret-Muster, alte
getvira-Adressen, Canonicals und das Bild-Backup. **Im Frontend darf niemals
landen:** Admin-Key, Stripe-Secret, Webhook-Secret, Gemini-/Tavily-Key,
JWT-Secret, SMTP-Zugang.

---

## 5. Frontend-Service `enfal-web`

1. Railway-Projekt → **New Service → GitHub repo** → `auto-ki-web`, Branch `master`.
2. Variablen aus 4.4 setzen, dann deployen.
3. Zweistufiges Image: Node-Build (mit Wächter vor/nach `npm run build`) →
   `nginx:1.27-alpine`. Healthcheck `/healthz`.
4. nginx: vorgerenderte Seiten direkt, alle übrigen Pfade → `spa.html`
   (noindex, clientseitiges Routing, eigene 404-Seite). Security-Header kommen
   aus `nginx-security-headers.conf` und gelten in **jeder** location (nginx
   vererbt `add_header` nicht — früher fehlten sie auf `/index.html` und
   `/assets/`). HTML: `Cache-Control: no-cache`; `/assets/`: 1 Jahr `immutable`.
5. Build-Kontext: Railway baut aus dem Git-Klon; `.dockerignore` schließt
   zusätzlich `public/cars/_backup/` (~100 MB, gitignored), `dist/`, `.env*` aus.

---

## 6. Domains, DNS (STRATO), HTTPS

### 6.1 Wie Railway Domains verbindet (offizielle Doku)
Pro Custom Domain zeigt Railway **einen CNAME** (Ziel wie `xxxx.up.railway.app`)
und **einen TXT-Eintrag** zur Besitzprüfung. **Beide sind Pflicht** — fehlt der
TXT, antwortet die Domain mit 404. Zertifikat: Let's Encrypt automatisch,
meist innerhalb einer Stunde nach DNS-Änderung, Verlängerung automatisch.
HTTP → HTTPS: Railway leitet `GET` per **301** um.

**Die Zielwerte stehen erst nach dem Anlegen im Railway-Dashboard fest**
(Service → Settings → Networking → Custom Domain). Hier stehen deshalb bewusst
keine erfundenen Werte.

### 6.2 Die STRATO-Einschränkung
STRATO erlaubt CNAME-Einträge **nur für Subdomains**, nicht für die
Hauptdomain selbst (STRATO-FAQ „DNS-Einträge verwalten"). Railway verlangt für
eine Hauptdomain CNAME-Flattening/ALIAS und empfiehlt sonst, die Nameserver zu
Cloudflare zu verlegen. Für `getenfal.de` selbst gibt es deshalb zwei Wege —
**Nutzerentscheidung:**

| | **A: DNS zu Cloudflare (empfohlen)** | **B: DNS bleibt bei STRATO** |
|---|---|---|
| Hauptdomain | `getenfal.de` CNAME (geflattet) → Railway | geht nicht direkt auf Railway |
| Öffentliche Website | `https://getenfal.de` wie geplant | `https://www.getenfal.de` wird Hauptadresse; `getenfal.de` per STRATO-Weiterleitung auf www |
| Folgearbeit | keine | Canonicals/Sitemap/robots von `getenfal.de` auf `www.getenfal.de` umstellen (`src/seo/seo.ts`); HTTPS der STRATO-Weiterleitung prüfen |
| Kosten | Cloudflare Free | keine |
| Wichtig | Proxy (orange Wolke) **aus** lassen oder SSL/TLS = **Full** (sonst `ERR_TOO_MANY_REDIRECTS`, Railway-Doku) — und: ein Cloudflare-Proxy verändert die Client-IP-Header, die Messung in 8 muss dann **mit** Proxy erfolgen | — |

### 6.3 Einträge (Weg A; bei B entfällt Zeile 1, Zeile 4 wird Hauptadresse)

| Typ | Host | Ziel | Zweck |
|---|---|---|---|
| CNAME | `@` (geflattet) | Railway-Ziel von `enfal-web` für `getenfal.de` | Website |
| TXT | laut Railway-Dashboard | laut Railway-Dashboard | Besitzprüfung `getenfal.de` |
| CNAME | `app` | Railway-Ziel von `enfal-web` für `app.getenfal.de` | App |
| TXT | laut Railway-Dashboard | laut Railway-Dashboard | Besitzprüfung `app.getenfal.de` |
| CNAME | `api` | Railway-Ziel von `enfal-api` für `api.getenfal.de` | Backend |
| TXT | laut Railway-Dashboard | laut Railway-Dashboard | Besitzprüfung `api.getenfal.de` |
| CNAME | `www` | `getenfal.de` + Redirect-Regel `www` → `https://getenfal.de` (301) | nur Weiterleitung |

`www` wird **nicht** als dritte Domain am Frontend-Service angelegt (Hobby:
2 Domains/Service); die Weiterleitung passiert auf DNS-/Cloudflare-Ebene.

Bestehende MX-/Mail-Einträge bei STRATO nicht anfassen (und bei Weg A zu
Cloudflare mitnehmen), sonst bricht E-Mail der Domain.

### 6.4 HTTPS-Kette
Railway terminiert TLS (TLS 1.2/1.3), leitet HTTP→HTTPS um, setzt
`X-Forwarded-Proto: https`. Zusätzlich senden Backend und nginx
`Strict-Transport-Security: max-age=63072000; includeSubDomains`. Das
Auth-Cookie ist in Produktion `Secure`. Im Build prüft der Wächter, dass keine
localhost-/http-API-Adresse eingebettet ist.

---

## 7. CORS und Auth-Cookie über Subdomains

- `AUTO_KI_CORS_ORIGINS=https://getenfal.de,https://app.getenfal.de` — genau die
  zwei Frontend-Origins, `allow_credentials=True`. Kein `*`, kein localhost
  (Start wird sonst verweigert).
- Auth-Cookie `auth_token`: `HttpOnly`, `Secure`, `SameSite=Lax`, **ohne
  `Domain`-Attribut** → host-only auf `api.getenfal.de`.
- Warum das über Subdomains funktioniert: `getenfal.de`, `app.getenfal.de` und
  `api.getenfal.de` haben dieselbe registrierbare Domain (`.de` ist die
  Public-Suffix) und sind damit **same-site**. `SameSite=Lax` sperrt nur
  cross-site-Subrequests; ein `fetch(..., {credentials: 'include'})` von
  `app.getenfal.de` an `api.getenfal.de` ist cross-origin, aber same-site →
  das Cookie wird gesetzt und mitgeschickt. Es ist auch kein Drittanbieter-Cookie
  (Browser-Sperren für Third-Party-Cookies greifen nicht).
- Ein `Domain=.getenfal.de` ist **nicht** nötig und wäre schlechter (das Cookie
  ginge dann an jede Subdomain).
- Live zu bestätigen (Abschnitt 13): Login auf `app.getenfal.de` und
  `getenfal.de`, Cookie im Browser unter `api.getenfal.de`, `/me` liefert den Nutzer.

---

## 8. Client-IP hinter dem Railway-Proxy (PFLICHT nach dem ersten Deploy)

Rate-Limits, Login-Drosselung und die anonyme AutoFinder-Demo hängen an der
Client-IP. Ohne Konfiguration zählt nur die Proxy-Adresse: **alle** Nutzer
teilen sich einen Zähler (sicher gegen Fälschung, aber ein Einzelner kann die
Limits für alle verbrauchen).

Offizielle Railway-Doku (Public Networking → Specs & Limits): Railway setzt
`X-Real-IP` „zur Identifikation der Remote-IP des Clients",
`X-Forwarded-Proto` (immer https) und `X-Forwarded-Host`. **Zu
`X-Forwarded-For` und zur Anzahl der Proxy-Hops sagt die Doku nichts**; im
Support-Forum widersprechen sich Angaben (u. a. X-Real-IP = CDN-IP bei CDN-Pfad).
Deshalb wird **gemessen**, nicht geraten:

1. `AUTO_KI_ADMIN_API_KEY` setzen, deployen.
2. Von **zwei Geräten mit verschiedenen öffentlichen IPs** (z. B. WLAN + Handy
   im Mobilfunk):
   ```bash
   curl -s -H "Authorization: Bearer $AUTO_KI_ADMIN_API_KEY" https://api.getenfal.de/api/v1/admin/client-ip
   ```
   Eigene öffentliche IP jeweils vergleichen (z. B. `curl -s https://api.ipify.org`).
3. Auswerten (`gegenstelle`, `x-real-ip`, `x-forwarded-for`,
   `gegenstelle_ist_vertrauenswuerdiger_proxy`):
   - echte IP in `x-real-ip` → `AUTO_KI_CLIENT_IP_HEADER=x-real-ip`, `AUTO_KI_TRUSTED_PROXY_HOPS=1`
   - echte IP als **letzter** Eintrag in `x-forwarded-for` → `AUTO_KI_TRUSTED_PROXY_HOPS=1`
   - `gegenstelle_ist_vertrauenswuerdiger_proxy: false` → zuerst
     `AUTO_KI_TRUSTED_PROXY_NETS` auf das gemessene Proxy-Netz setzen
4. Wiederholen: `verwendeter_limit_schluessel` = echte IP, verschieden je Gerät.
5. **Spoofing-Gegenprobe:** mit `-H "X-Forwarded-For: 1.2.3.4" -H "X-Real-IP: 1.2.3.4"`
   darf sich der Schlüssel **nicht** auf 1.2.3.4 ändern. Schlägt das fehl:
   zurück auf `0` und als Blocker melden.

Der Server läuft mit `uvicorn --no-proxy-headers`, damit diese Entscheidung an
genau einer Stelle fällt (`app/client_ip.py`). Das Flag muss bleiben.

---

## 9. E-Mail-Bestätigung

Neue Konten sind unbestätigt; die kostenlosen LLM-Kontingente (Chat,
AutoFinder, Rückfragen) gibt es erst nach Bestätigung. Bezahltes ist nie betroffen.

- Versand: `app/mailer.py`, **anbieterneutral per SMTP** (Standardbibliothek).
  Port 465 = TLS, sonst STARTTLS mit Zertifikatsprüfung; ohne STARTTLS wird
  **nicht** im Klartext gesendet. Versand nach der Antwort (blockiert die
  Registrierung nicht). Token und Adresse stehen nie im Log.
- Link: `FRONTEND_URL/email-bestaetigen#token=…` — Token im Fragment, taucht in
  keinem Server-Log auf. Frontend-Seite `/email-bestaetigen` löst ihn ein und
  bietet angemeldet „Neuen Link senden".
- Produktion gibt den Token **nie** in einer API-Antwort zurück (Test D-3).
- **Offen (RC1-Blocker):** Anbieterentscheidung + SMTP-Zugang. Ohne
  `AUTO_KI_SMTP_HOST` warnt der Start laut, und neue Nutzer bekommen keine Mail.
  Möglich ist jedes Postfach mit SMTP-Zugang (z. B. eine Adresse der eigenen
  Domain beim Mailanbieter) oder ein Versanddienst. Vor RC1 außerdem: SPF/DKIM
  (ggf. DMARC) für `getenfal.de` beim Mailanbieter einrichten, sonst landen die
  Mails im Spam.

---

## 10. Backups & Restore

**Stufe 1 — in der App (vorhanden):** alle 6 h und nach Fahrzeug-Admin-Schreib-
vorgängen eine konsistente Kopie (`sqlite3 backup()`) nach `/data/backups`,
`PRAGMA integrity_check` auf jede neue Datei (korrupte werden verworfen), die
letzten **10** bleiben. Fehler erscheinen im Log (`SQLite-Backup fehlgeschlagen`
/ `Periodisches Backup fehlgeschlagen`).
Diese Backups liegen **auf demselben Volume** — sie schützen gegen Bedien- und
Softwarefehler, nicht gegen Volumenverlust.

**Stufe 2 — Railway Volume Backups (Dashboard, einmalig einschalten):**
Service → Volume → Backups: Zeitplan **täglich** (6 Tage) + **wöchentlich**
(27 Tage). Inkrementell, Kosten nach Volumen. Restore per Klick (legt ein neues
Volume an, das alte bleibt ungemountet). Einschränkungen laut Doku: nur
Wiederherstellung ins selbe Projekt/Environment; **„Wiping a volume deletes all
backups"** — also kein Offsite-Backup.

**Stufe 3 — Offsite (FEHLT, Go-Live-Punkt):** regelmäßige Kopie eines
Backups aus `/data/backups` an einen Speicher außerhalb von Railway. Braucht
eine Anbieter-/Kostenentscheidung (z. B. S3-kompatibler Speicher) — hier
bewusst nicht eingebaut.

**Restore aus Stufe 1:**
1. Service stoppen (oder Wartung ankündigen).
2. Per `railway ssh` in den Container: gewünschte `auto_ki_backup_*.db` aus
   `/data/backups` prüfen (`sqlite3 … "PRAGMA integrity_check"` → `ok`), die
   aktuelle `auto_ki.db` (+ `-wal`/`-shm`) nach `/data/restore_alt_<datum>/`
   verschieben, Backup nach `/data/auto_ki.db` kopieren.
3. Service neu starten. Chroma bleibt gültig, solange sich der Fahrzeugbestand
   nicht unterscheidet; sonst `/data/chroma` umbenennen → der Start baut neu auf.
4. `/health` → `{"status":"ok","db":"ok"}`, Login mit Testkonto prüfen.

---

## 11. Stripe (Testmodus)

- Test-Keys (`sk_test_…`), Test-Price-IDs (KaufCheck 5,99 €, VerkaufsCheck
  8,99 €, Plus 16,99 € mtl.). Preise kommen **serverseitig** aus den Price-IDs.
- Webhook im Stripe-Dashboard **(Testmodus)** → Entwickler → Webhooks →
  Endpoint `https://api.getenfal.de/api/v1/payments/webhook`, Events:
  `checkout.session.completed`, `invoice.paid`, `customer.subscription.deleted`,
  `charge.refunded`, `charge.dispute.created`, `charge.dispute.funds_withdrawn`
  (= die in `app/routers/payments.py` verarbeiteten Events). Das `whsec_…` als
  `STRIPE_WEBHOOK_SECRET`.
- Signaturprüfung: ohne Secret 503 (Stripe stellt später erneut zu), falsche
  Signatur 400 **mit Logzeile** `Stripe-Webhook abgelehnt: ungueltige Signatur`.
  Idempotenz über `stripe_events` (event_id PRIMARY KEY, Claim nur bei Erfolg).
- Live-Keys werden in dieser Phase **vom Start abgelehnt**
  (`AUTO_KI_STRIPE_LIVE_ERLAUBT` ungesetzt).

---

## 12. Provider (Gemini / Tavily)

Die Code-Seite ist fertig (`PROVIDER_RELIABILITY.md`): Timeouts, Retry-Budget,
Call-Budgets je Aktion, Tagesquota-Erkennung, Refund genau einmal,
Parallelitätsgrenzen (32 global / 2 pro Konto). Produktion braucht:

- eigene Produktions-Keys (nicht die Entwicklungs-Keys),
- **Budget-/Nutzungsgrenzen beim Anbieter** (Google Cloud Billing-Budget mit
  Alarm + ggf. Quota-Limit für die Gemini-API; Tavily-Plan-/Nutzungslimit) —
  nicht programmatisch einrichtbar, **Go-Live-Punkt**.

---

## 13. Logs, Monitoring, Neustarts

- **Logs:** stdout/stderr → Railway-Dashboard (Deploy- und Laufzeitlogs).
  Format `Zeit LEVEL modul: meldung`, Level per `AUTO_KI_LOG_LEVEL`.
  Nie im Log: Passwörter, JWT, API-Keys, Bestätigungstoken, Mailadressen im
  Mailer, Chat-/Check-Inhalte.
- **Wichtige Logzeilen zum Suchen:**
  `Unsichere Produktionskonfiguration` (Start verweigert) ·
  `Kein E-Mail-Versand konfiguriert` · `Bestaetigungsmail fehlgeschlagen` ·
  `SQLite-Backup` · `Periodisches Backup fehlgeschlagen` ·
  `provider_retry` / `provider_event` / Tagesquota · `Stripe-Webhook abgelehnt` ·
  `Zahlung ... rueckabgewickelt` · `Chroma-Bootstrap` · `Unhandled error`.
- **Healthcheck:** `/health` (Backend) — 200 `{"status":"ok","db":"ok"}`, **503**
  wenn die Datenbank nicht antwortet; keine Pfade, keine Tabellen. `/healthz`
  (Frontend). **Railway nutzt den Healthcheck nur beim Deploy**, nicht
  dauerhaft (offizielle Doku).
- **Absturz:** Prozessende → Neustart (`ON_FAILURE`, max. 10 Versuche).
- **Graceful Shutdown:** `drainingSeconds: 90` (Railway-Default wäre 0 s =
  sofort SIGKILL); uvicorn bekommt SIGTERM direkt (`exec`), beendet laufende
  Requests (Gemini-Gesamtbudget 75 s). Mit Volume gibt es bei jedem Redeploy
  trotzdem eine kurze Downtime.
- **Lücke:** ein hängender, aber nicht abgestürzter Prozess wird nicht erkannt.
  Ein externer Uptime-Monitor auf `/health` (z. B. ein kostenloser Dienst) wäre
  die Ergänzung — Nutzerentscheidung, nicht eingebaut.

---

## 14. Live-Prüfung nach dem Deploy

```bash
curl -sI http://api.getenfal.de/health          # 301 -> https
curl -s  https://api.getenfal.de/health         # {"status":"ok","db":"ok"}
curl -sI https://api.getenfal.de/docs           # 404 (keine API-Doku)
curl -sI https://getenfal.de/                   # 200, 7 Security-Header, kein X-Robots-Tag
curl -sI https://app.getenfal.de/               # 200, X-Robots-Tag: noindex, nofollow
curl -s  https://getenfal.de/robots.txt
curl -s  https://getenfal.de/sitemap.xml
curl -sI https://getenfal.de/assets/<datei>.js  # Security-Header + immutable
curl -s  https://getenfal.de/login | grep robots  # noindex
curl -sI -H "Origin: https://evil.example" https://api.getenfal.de/health   # kein Access-Control-Allow-Origin
```

Im Browser: Registrieren → Bestätigungsmail → Link → Kontingente frei;
Login auf beiden Hosts; Cookie liegt unter `api.getenfal.de`
(HttpOnly/Secure/Lax); Konsole ohne CORS-Fehler; Test-Kauf mit Stripe-Testkarte
`4242 4242 4242 4242` → Webhook 200 → Guthaben da. Danach Proxy-Messung (8).

---

## 15. Lokaler Docker-Testlauf (optional)

```bash
docker build -t enfal-api .
docker run -p 8000:8000 --env-file .env -v enfal-data:/data enfal-api
curl http://localhost:8000/health
```
(`--env-file` braucht dann Produktionswerte gemäß 4.1, sonst verweigert der Start.)
