# ENFAL — Produktions-Deployment (Railway)

Reproduzierbare Anleitung für Backend (`auto-ki-backend`) und Frontend
(`auto-ki-web`). Stand: Deployment-Block, 2026-09-14. **Stripe bleibt im
Testmodus** — Live-Zahlungen sind ein eigener Release-Schritt.

Plattform-Aussagen stützen sich auf die offizielle Railway-Doku
(docs.railway.com: Volumes, Backups, Healthchecks, Domains, Public Networking
Specs & Limits, Config as Code, Dockerfiles). Was dort nicht steht, wird hier
nicht behauptet, sondern **live gemessen** (siehe Abschnitt 8).

---

## 0. Live-Stand (gemessen 2026-09-15)

| | Wert |
|---|---|
| Backend | Railway-Projekt `successful-prosperity`, Service **`auto-ki-backend`**, Region `europe-west4-drams3a`, Volume `auto-ki-backend-volume` → `/data`, Port 8080 |
| Frontend | Railway-Projekt `delightful-prosperity`, Services **`auto-ki-web`** (→ `getenfal.de`) und **`auto-ki-app`** (→ `app.getenfal.de`), gleicher Build, Region `europe-west4-drams3a`, Port 8080 |
| Railway-Plan | „HOBBY", laut API-Limits: **1 Custom Domain pro Service**, **Volume max. 500 MB**, **Volume-Backups: 0** (nicht verfügbar), Logs 7 Tage, 1 GB RAM / 2 vCPU pro Container, 2 Projekte |
| Modus | `AUTO_KI_ENV` **nicht** in Railway gesetzt → Image-Default `production` (Startprüfung aktiv, `/docs` 404) |
| Client-IP | `AUTO_KI_CLIENT_IP_HEADER=x-real-ip`, `AUTO_KI_TRUSTED_PROXY_HOPS=1` (gemessen, siehe 8) |
| Stripe | Testmodus; genau ein Test-Webhook-Endpoint auf `/api/v1/payments/webhook` |

**Zwei Fallen, die live aufgetreten sind:**
1. `AUTO_KI_ENV=development` war in Railway gesetzt → API-Doku öffentlich,
   Bestätigungs-Token in der Registrierungsantwort, Cookie ohne `Secure`,
   keine Startprüfung. **Die Variable darf in Railway nicht existieren.** Der
   Uptime-Workflow (`auto-ki-web/.github/workflows/uptime.yml`) prüft deshalb,
   dass `/docs` 404 liefert.
2. Ein Variablenwert enthielt seinen eigenen Namen
   (`AUTO_KI_CORS_ORIGINS=AUTO_KI_CORS_ORIGINS=https://…`). Im Raw-Editor nur
   den **Wert** eintragen. Die Produktions-Startprüfung verweigert so einen Wert.

**Config-as-Code:** Railway übernimmt aus `railway.json` nicht alle Werte
(Frontend-Healthcheck, Backend-Draining fehlten im Manifest) und kündigt
`railway.json` zum **2026-12-01** ab (`railway config migrate` →
`.railway/railway.ts`). Healthcheck, Timeout, Draining und Region sind deshalb
zusätzlich direkt als Service-Einstellungen gesetzt (Railway-API
`serviceInstanceUpdate`). Die Migration ist ein offener Punkt vor Dezember.

---

## 1. Zielarchitektur

| Adresse | Railway-Service | Inhalt |
|---|---|---|
| `https://getenfal.de` | `auto-ki-web` (Frontend) | öffentliche Website — vorgerenderte Seiten `/`, `/autofinder`, `/autokosten`, `/pricing` |
| `https://app.getenfal.de` | `auto-ki-app` (zweiter Service, gleicher Build — Domain-Limit, siehe 0) | dieselbe App; Antworten tragen `X-Robots-Tag: noindex` |
| `https://api.getenfal.de` | `auto-ki-backend` (Backend) | FastAPI, Volume `/data` |
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

1. Railway-Konto + Plan. Die Doku nennt für Hobby 2 Custom Domains pro
   Service; **das Konto meldet per API aber 1** (Abschnitt 0). Deshalb bedient
   ein zweiter, identischer Frontend-Service `auto-ki-app` die App-Domain.
   Volume max. 500 MB (belegt: ~0,1 GB), Volume-Backups im Plan nicht enthalten.
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
| `AUTO_KI_BREVO_API_KEY` | ✔ für RC1 | ✔ | Brevo „API Key" (nicht der SMTP-Key) | leer = kein Versand | siehe Abschnitt 9 — Railway (Hobby-Plan) blockiert jeden SMTP-Port, daher HTTPS-API statt SMTP |
| `AUTO_KI_MAIL_FROM` | ✔ für RC1 | – | `ENFAL <noreply@getenfal.de>` | – | muss bei Brevo verifizierter Absender/Domain sein |
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
`AUTO_KI_EMAIL_VERIFIKATION_STUNDEN` (48) · `AUTO_KI_BREVO_TIMEOUT_SECONDS` (10) ·
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

### 6.3 Einträge — echte Railway-Zielwerte (angelegt 2026-09-15, Weg A)

Entscheidung: **Weg A (Cloudflare Free als DNS, STRATO bleibt Registrar).**
Die Werte stammen aus `railway domain … --json` (Railway-API), nicht erfunden.
Die TXT-Werte sind öffentliche Besitznachweise, kein Geheimnis.

| Typ | Name (Cloudflare) | Inhalt | Proxy | Zweck |
|---|---|---|---|---|
| CNAME | `@` | `w5l4oemj.up.railway.app` | DNS only (grau) | `getenfal.de` → `auto-ki-web` |
| TXT | `_railway-verify` | `railway-verify=827b5f91072d1e9f41a03ee83b16e41ba242c97c3293e579512a18b195c10629` | — | Besitzprüfung `getenfal.de` |
| CNAME | `app` | `cvebhlcq.up.railway.app` | DNS only (grau) | `app.getenfal.de` → `auto-ki-app` |
| TXT | `_railway-verify.app` | `railway-verify=80801b5838dbe0648cb6932941c81384da1680d041b48cf823fa7b9ae9488a56` | — | Besitzprüfung `app.getenfal.de` |
| CNAME | `api` | `iuwffquf.up.railway.app` | DNS only (grau) | `api.getenfal.de` → `auto-ki-backend` |
| TXT | `_railway-verify.api` | `railway-verify=6d54b7476cc0a8cfaaf6cc54102c822b958261e4bb078c2c8b9d79239176dc22` | — | Besitzprüfung `api.getenfal.de` |
| CNAME | `www` | `getenfal.de` | **Proxied (orange)** | nur Weiterleitung (Redirect-Regel unten) |

„DNS only" für `@`, `app`, `api`: Railway stellt dann selbst das
Let's-Encrypt-Zertifikat aus, und die gemessene Client-IP-Kette (Abschnitt 8)
bleibt gültig. Ein Cloudflare-Proxy davor würde `X-Real-IP` verändern.

`www` → Cloudflare-Redirect-Regel (Rules → Redirect Rules): Hostname gleich
`www.getenfal.de` → dynamisch `concat("https://getenfal.de", http.request.uri.path)`,
301, Query-String erhalten. `www` wird nicht bei Railway angelegt
(1 Custom Domain pro Service) und erzeugt keinen doppelten SEO-Inhalt.

Status prüfen: `railway domain status <domain> --service <service>`.

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

**Messergebnis 2026-09-15** (Admin-Diagnose, Key nur per `railway run` bzw. im
Container eingespielt, nie ausgegeben):

| Client | Gegenstelle | `X-Forwarded-For` | `X-Real-IP` | Limit-Schlüssel (nach Umstellung) |
|---|---|---|---|---|
| Heimanschluss (IPv4) | `100.64.0.x` (wechselnd, Vertrauensnetz) | `[Client, Railway-Edge 152.233.x.x]` | Client | Client |
| Railway-Container (Egress 152.55.x.x) | `100.64.0.x` | `[Client, Edge]` | Client | Client |

- Gefälschter `X-Forwarded-For` wird vom Edge **entfernt**, gefälschter
  `X-Real-IP` **überschrieben** — in beiden Fällen blieb der Schlüssel die
  echte Client-IP (4 Varianten × 2 Clients).
- Die Railway-Domain ist nur per IPv4 erreichbar.
- Railway-CDN ist **aus**. Wird es je eingeschaltet, muss neu gemessen werden
  (laut Forum steht dann die CDN-Adresse in `X-Real-IP`).
- Gesetzt: `AUTO_KI_CLIENT_IP_HEADER=x-real-ip`, `AUTO_KI_TRUSTED_PROXY_HOPS=1`,
  `AUTO_KI_TRUSTED_PROXY_NETS` bleibt Default (enthält `100.64.0.0/10`).

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
- **Versand läuft über die Brevo-Transaktionsmail-API per HTTPS, nicht per
  SMTP:** Railway blockiert auf dem laufenden Plan (Hobby) jeden ausgehenden
  SMTP-Port (25/465/587/2525) netzwerkseitig — gemessen per Direktverbindung
  aus dem Container (DNS ok, jeder SMTP-Port TimeoutError, HTTPS sofort
  erreichbar). Ohne `AUTO_KI_BREVO_API_KEY` warnt der Start laut, und neue
  Nutzer bekommen keine Mail. Ein Upgrade auf den Railway-Pro-Plan würde SMTP
  wieder freischalten, ist aber eine eigene (kostenpflichtige) Entscheidung.
  Vor RC1 außerdem: SPF/DKIM (ggf. DMARC) für `getenfal.de` bei Brevo
  einrichten, sonst landen die Mails im Spam.

---

## 10. Backups & Restore

**Stufe 1 — in der App (vorhanden):** alle 6 h und nach Fahrzeug-Admin-Schreib-
vorgängen eine konsistente Kopie (`sqlite3 backup()`) nach `/data/backups`,
`PRAGMA integrity_check` auf jede neue Datei (korrupte werden verworfen), die
letzten **10** bleiben. Fehler erscheinen im Log (`SQLite-Backup fehlgeschlagen`
/ `Periodisches Backup fehlgeschlagen`).
Diese Backups liegen **auf demselben Volume** — sie schützen gegen Bedien- und
Softwarefehler, nicht gegen Volumenverlust.

**Stufe 2 — Railway Volume Backups: im aktuellen Plan NICHT verfügbar**
(API-Limit `maxBackupsCount: 0`). Mit einem Plan, der sie enthält: Service →
Volume → Backups, **täglich** + **wöchentlich**. Auch dann gilt laut Doku:
nur Restore ins selbe Projekt, „Wiping a volume deletes all backups" — kein
Offsite-Ersatz.

**Stufe 3 — Offsite auf den Entwickler-PC (vorhanden):**
`scripts/offsite_backup.ps1` lädt das jeweils neueste
`/backups/auto_ki_backup_*.db` über Railways offizielles Volume-Werkzeug
(`railway volume files download`), prüft Größe und `PRAGMA integrity_check`
**vor** dem Ablegen, legt nach `%USERPROFILE%\ENFAL-Backups` ab und behält die
letzten 30. Kein Backend-Code, keine Secrets im Skript. Voraussetzungen:
Railway-CLI angemeldet + SSH-Schlüssel bei Railway registriert (beides auf dem
PC vorhanden). Automatik: Windows-Aufgabe **„ENFAL Offsite-Backup"**, täglich
21:15, nur bei angemeldetem Benutzer, verpasste Läufe werden nachgeholt
(`StartWhenAvailable`); im Aufgaben-Kontext erfolgreich getestet (Download,
Größe, `integrity_check`). Das Skript nutzt `%USERPROFILE%\.enfal\bin\railway.exe`
(Kopie der offiziellen CLI-Binärdatei): die npm-Installation unter AppData war in
der Aufgaben-Umgebung nicht sichtbar. Grenzen: läuft nur, wenn der PC an ist; die Kopien enthalten personenbezogene
Daten und liegen unverschlüsselt auf dem PC — vor dem öffentlichen Launch eine
zweite, verschlüsselte Ablage außerhalb des PCs festlegen.

Manuell:
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\offsite_backup.ps1
```
Log: `%USERPROFILE%\ENFAL-Backups\offsite_backup.log` (`OK …` / `FEHLER …`).

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
- **Live-Stand 2026-09-15:** Das zuvor gesetzte `STRIPE_WEBHOOK_SECRET` gehörte
  zu keinem existierenden Endpoint (Stripe hatte 0 Endpoints). Neu angelegt:
  genau ein Test-Endpoint mit den 6 Events; sein Secret wurde direkt per
  `railway variable set --stdin` übernommen (nie ausgegeben). Solange
  `api.getenfal.de` nicht auflöst, zeigt er auf die Railway-Adresse; danach
  nur die URL ändern (`stripe webhook_endpoints update <id> --url …`), das
  Secret bleibt.
- Live geprüft: signiertes `checkout.session.completed` → Gutschrift 1
  KaufCheck; erneute Zustellung desselben Events → bleibt 1 (Idempotenz);
  falsche Signatur → 400 + Logzeile; vom Backend erzeugte Checkout-Sessions:
  5,99 € / 8,99 € / 16,99 € mtl., Preis im Request-Body wird ignoriert,
  unbekanntes Produkt → 400.

---

## 12. Provider (Gemini / Tavily)

Die Code-Seite ist fertig (`PROVIDER_RELIABILITY.md`): Timeouts, Retry-Budget,
Call-Budgets je Aktion, Tagesquota-Erkennung, Refund genau einmal,
Parallelitätsgrenzen (32 global / 2 pro Konto). Produktion braucht:

- eigene Produktions-Keys (nicht die Entwicklungs-Keys),
- **Budget-/Nutzungsgrenzen beim Anbieter** (Google Cloud Billing-Budget mit
  Alarm + ggf. Quota-Limit für die Gemini-API; Tavily-Plan-/Nutzungslimit) —
  nicht programmatisch einrichtbar, **Go-Live-Punkt**.
- **Live-Befund 2026-09-15:** Der Produktions-Gemini-Key antwortet mit 429
  „Your prepayment credits are depleted" (beide Modelle) — das Prepaid-Guthaben
  im AI-Studio-Projekt ist aufgebraucht. Chat, Checks und AutoFinder-Anreicherung
  laufen bis zum Aufladen nicht bzw. nur im deterministischen Fallback. Die App
  wertet diesen Fehler heute als Rate-Limit (3 Versuche, kostenlos) statt als
  Kontingent-Ende; die Nutzermeldung ist dadurch „überlastet" statt „Kontingent".

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
- **Uptime-Monitor (vorhanden):** GitHub-Workflow
  `auto-ki-web/.github/workflows/uptime.yml`, alle 15 min: Backend `/health`
  (200 + `db: ok`), `/docs` `/redoc` `/openapi.json` = 404 (Produktionsmodus),
  Frontend `/healthz` + Startseite + Security-Header. Fehlschlag → GitHub-
  Benachrichtigung an das Konto, das den Workflow zuletzt geändert hat.
  Erkennt damit auch einen hängenden Prozess, den Railway nicht bemerkt
  (Healthcheck nur beim Deploy). Grenzen: GitHub-Zeitpläne laufen nicht
  sekundengenau und pausieren in öffentlichen Repos nach 60 Tagen ohne
  Aktivität.
- **Kostenschutz Railway:** `railway usage` zeigt Verbrauch; Soft/Hard-Limit
  sind derzeit **nicht** gesetzt (Dashboard → Usage).

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
