import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent

# .env im Backend-Ordner laden (vor allen os.environ.get()-Aufrufen).
# override=False: bereits gesetzte Systemvariablen haben Vorrang.
load_dotenv(BASE_DIR / ".env", override=False)

# ---------------------------------------------------------------------------
# Datenbankpfade
# ---------------------------------------------------------------------------
# Sowohl SQLite als auch ChromaDB werden ausserhalb von OneDrive gespeichert.
# Begründung:
#   - ChromaDB: OneDrive entfernt HNSW-Binärdateien → Index-Korruption
#   - SQLite (WAL-Modus): OneDrive kann -wal/-shm-Dateien inkonsistent synchen →
#     Backup-Restore liefert korrupte oder veraltete Daten
#
# Standard für beide: %LOCALAPPDATA%\auto-ki-backend\  (nicht synchronisiert)
# Überschreiben: Umgebungsvariablen AUTO_KI_DB_PATH / AUTO_KI_CHROMA_PATH
#
# Backup:
#   Nach jedem erfolgreichen Save wird eine konsistente Sicherungskopie nach
#   DB_BACKUP_PATH geschrieben (via sqlite3.Connection.backup()).
#   Diese Kopie liegt IN OneDrive und wird automatisch in die Cloud synchronisiert.
#   Im Notfall (Datenverlust): DB_BACKUP_PATH → DB_PATH kopieren.
# ---------------------------------------------------------------------------

_local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "auto-ki-backend"

# Live-Datenbank — ausserhalb OneDrive (kein Sync-Risiko)
_db_default = _local / "auto_ki.db"
DB_PATH = Path(os.environ.get("AUTO_KI_DB_PATH", str(_db_default)))

# Ursprünglicher Pfad in OneDrive — wird für automatische Migration benötigt
DB_LEGACY_PATH = BASE_DIR / "db" / "auto_ki.db"

# Backup-Verzeichnis — datierte Kopien, letzte 10 Versionen behalten.
# Jedes Backup bekommt einen eigenen Zeitstempel: auto_ki_backup_YYYY-MM-DD_HHMM.db
# → ein gutes Backup kann nie von einem schlechten überschrieben werden.
#
# Standard (lokale Entwicklung): BASE_DIR/db/backups (liegt in OneDrive → Cloud-Sync).
# PRODUKTION: Muss per AUTO_KI_DB_BACKUP_DIR auf ein PERSISTENTES Volume zeigen —
# sonst landen die Backups im ephemeren Container-Dateisystem und sind nach jedem
# Redeploy/Neustart verloren (stiller Datenverlust im Ernstfall).
DB_BACKUP_DIR  = Path(os.environ.get("AUTO_KI_DB_BACKUP_DIR", str(BASE_DIR / "db" / "backups")))

# Periodisches Backup-Intervall (Sekunden). Die bisherige Backup-Logik lief NUR
# nach Fahrzeug-Admin-Schreibvorgängen (save_fahrzeug/patch_luecken) — Nutzer-
# registrierungen, Chats und Käufe lösten nie ein Backup aus. Ein periodischer
# Timer (siehe app.main) sichert stattdessen die komplette DB in festem Takt,
# unabhängig davon ob/wann zuletzt ein Fahrzeug gepflegt wurde.
# Default: 6 Stunden. 0 oder negativ deaktiviert den periodischen Timer.
DB_BACKUP_INTERVAL_SECONDS = int(os.environ.get("AUTO_KI_DB_BACKUP_INTERVAL_SECONDS", "21600"))

# Legacy-Einzeldatei — nicht mehr beschrieben, bleibt für migrate_db.py --restore
# als letzter Fallback wenn DB_BACKUP_DIR leer ist.
DB_BACKUP_PATH = BASE_DIR / "db" / "auto_ki_backup.db"

# ChromaDB — ausserhalb OneDrive (HNSW-Binärdateien)
_chroma_default = _local / "chroma"
CHROMA_PATH = Path(os.environ.get("AUTO_KI_CHROMA_PATH", str(_chroma_default)))

# ---------------------------------------------------------------------------
# Laufzeitumgebung — EINZIGE Stelle, an der Entwicklung und Produktion
# unterschieden werden.
# ---------------------------------------------------------------------------
# Lokal ohne Angabe: "development" (bequemer HTTP-Betrieb, Dev-Defaults erlaubt).
# Das Produktions-Image (Dockerfile) setzt AUTO_KI_ENV=production fest.
#
# Fail-closed: JEDER andere Wert als "development" gilt als Produktion. Ein
# Tippfehler ("prod", "Production ") darf nie versehentlich die lockeren
# Entwicklungsregeln aktivieren.
ENVIRONMENT = os.environ.get("AUTO_KI_ENV", "development").strip().lower()
IS_PRODUCTION = ENVIRONMENT != "development"

# Oeffentlich bekannte Entwicklungs-Defaults. Stehen im Code und in der Doku —
# in Produktion also so gut wie gar kein Secret (siehe validiere_produktion()).
DEV_API_KEY = "dev-key-change-in-prod"
DEV_JWT_SECRET = "dev-jwt-secret-change-in-prod"

# Consumer-Key: Kauf-/Verkaufscheck, Chat, AutoFinder, /fahrzeug.
# ACHTUNG: Das Frontend bettet diesen Key als VITE_API_KEY in das oeffentliche
# JS-Bundle ein. Er ist damit fuer jeden Besucher lesbar und darf NIEMALS mehr
# freischalten als die Consumer-Routen.
API_KEY = os.environ.get("AUTO_KI_API_KEY", DEV_API_KEY).strip()

# Admin-Key: ausschliesslich fuer app/routers/admin.py (Fahrzeugdaten schreiben,
# LLM-Entwuerfe). Bewusst OHNE Default und OHNE Fallback auf API_KEY: fehlt er,
# sind die Admin-Endpunkte geschlossen (siehe app/auth.verify_admin_key).
# Niemals als VITE_-Variable setzen — er gehoert nie ins Frontend.
ADMIN_API_KEY = os.environ.get("AUTO_KI_ADMIN_API_KEY", "").strip()

# Mindestlaenge fuer Secrets in Produktion (`openssl rand -hex 32` = 64 Zeichen).
MIN_SECRET_LEN = 32

RATE_LIMIT = os.environ.get("AUTO_KI_RATE_LIMIT", "20/minute")

# ---------------------------------------------------------------------------
# Client-IP hinter einem Reverse-Proxy (Security Block 2, P1-7)
# ---------------------------------------------------------------------------
# Rate-Limits und die anonymen Kontingente haengen an der Client-IP. Steht ein
# Proxy davor (Railway), ist `request.client.host` dessen Adresse — dann teilen
# sich ALLE Nutzer einen Zaehler, und ein Einzelner kann die Limits fuer alle
# verbrauchen.
#
# Die Header-Auswertung ist BEWUSST standardmaessig AUS (0 Hops): ein frei
# gesetzter X-Forwarded-For waere sonst ein Freifahrtschein an jedem Limit
# vorbei. Eingeschaltet wird sie erst, wenn fuer die konkrete Umgebung belegt
# ist, wie viele vertrauenswuerdige Proxys davorstehen.
#
#   AUTO_KI_TRUSTED_PROXY_HOPS=1        Anzahl eigener/vorgelagerter Proxys.
#   AUTO_KI_CLIENT_IP_HEADER=x-real-ip  Header mit der echten Client-IP
#                                       (Default x-forwarded-for).
#   AUTO_KI_TRUSTED_PROXY_NETS=...      Netze, aus denen ein Proxy sprechen darf.
#
# Header werden NUR ausgewertet, wenn die direkte Gegenstelle in einem dieser
# Netze liegt. Ein Angreifer, der die App direkt erreicht, kann seine IP damit
# nicht faelschen. Default sind die privaten Netze plus der CGNAT-Bereich
# 100.64.0.0/10, aus dem interne Plattform-Proxys ueblicherweise sprechen.
TRUSTED_PROXY_HOPS = int(os.environ.get("AUTO_KI_TRUSTED_PROXY_HOPS", "0"))
CLIENT_IP_HEADER = os.environ.get("AUTO_KI_CLIENT_IP_HEADER", "x-forwarded-for").strip().lower()
_PROXY_NETS_DEFAULT = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8,100.64.0.0/10,::1/128,fc00::/7"
TRUSTED_PROXY_NETS: list[str] = [
    n.strip() for n in os.environ.get("AUTO_KI_TRUSTED_PROXY_NETS", _PROXY_NETS_DEFAULT).split(",") if n.strip()
]

# ---------------------------------------------------------------------------
# Kostenloses KI-Chat-Kontingent (Consumer V1)
# ---------------------------------------------------------------------------
# EINZIGE Stelle, an der die Free-Grenze steht — Router und Gate lesen nur von
# hier (keine verstreuten Zahlen im Code).
#
# Der Wert ist BEWUSST eine Produktgrenze, keine aus Kosten hergeleitete Zahl:
# im Repo existiert keine belastbare Kostenbasis pro Chat-Anfrage (kein
# Token-/Preis-Tracking), also wird hier auch keine vorgetaeuscht. 20 Anfragen
# pro UTC-Kalendertag decken echte Consumer-Nutzung (ein Fahrzeug durchfragen)
# komfortabel ab und deckeln zugleich automatisierten Dauerabruf.
# Kalibrierbar ohne Codeaenderung: AUTO_KI_CHAT_FREE_LIMIT_TAEGLICH=<n>.
# 0 oder negativ deaktiviert das Tageslimit vollstaendig.
# ---------------------------------------------------------------------------
# Consumer Pricing V1 FINAL — Monatskontingente
# ---------------------------------------------------------------------------
# EINZIGE Stelle, an der die Grenzen stehen. Router und Gates lesen nur von
# hier; im uebrigen Code gibt es bewusst keine verstreuten Zahlen.
#
# Umstellung von TAG auf MONAT: ein Tageslimit deckelt den Missbrauch, sagt dem
# Nutzer aber nichts ueber den Wert seines Tarifs ("20 pro Tag" klingt nach
# unbegrenzt, kostet aber real ein Vielfaches eines Abos). Monatskontingente
# sind die Einheit, in der das Produkt verkauft wird — Anzeige, Abrechnung und
# Limit sprechen damit dieselbe Sprache.
#
# Reset: UTC-Kalendermonat (YYYY-MM). Bewusst NICHT der Stripe-Abrechnungs-
# zeitraum: der Zaehler bliebe sonst fuer Free-Nutzer undefiniert und fuer
# Plus-Nutzer waere die Grenze je nach Kaufdatum verschoben. Die
# CHECK-Kontingente von Plus folgen dagegen sehr wohl dem Stripe-Zeitraum
# (siehe app/plus.py) — dort ist es die bezahlte Leistung.
CHAT_FREE_LIMIT_MONATLICH = int(os.environ.get("AUTO_KI_CHAT_FREE_LIMIT_MONATLICH", "20"))
CHAT_PLUS_LIMIT_MONATLICH = int(os.environ.get("AUTO_KI_CHAT_PLUS_LIMIT_MONATLICH", "100"))
AUTOFINDER_FREE_LIMIT_MONATLICH = int(os.environ.get("AUTO_KI_AUTOFINDER_FREE_LIMIT_MONATLICH", "5"))
AUTOFINDER_PLUS_LIMIT_MONATLICH = int(os.environ.get("AUTO_KI_AUTOFINDER_PLUS_LIMIT_MONATLICH", "50"))

# AutoFinder OHNE Login: reine Demo, gezaehlt je UTC-Tag und IP.
#
# Der IP-Anker taugt fuer eine Demo, aber NICHT fuer ein Monatskontingent: hinter
# Buero-NAT, Schul-/Hotel-WLAN oder Mobilfunk-CGNAT teilen sich beliebig viele
# Menschen eine Adresse und damit einen Zaehler. Bei 5 Suchen im MONAT waere der
# Ausprobier-Pfad dort nach kurzer Zeit dauerhaft zu. Ein TAEGLICHER Demo-Zugang
# regeneriert sich dagegen von selbst — geteilte Adressen bleiben nutzbar, und
# Dauerabruf ueber dieselbe IP bleibt trotzdem gedeckelt.
#
# Das eigentliche Free-Kontingent (5/Monat) haengt ausschliesslich am KONTO.
AUTOFINDER_ANONYM_DEMO_PRO_TAG = int(os.environ.get("AUTO_KI_AUTOFINDER_ANONYM_DEMO_PRO_TAG", "1"))

# VIRA Plus: monatlich enthaltene Check-Kontingente (Reset je bezahltem
# Abrechnungszeitraum, KEIN Uebertrag).
PLUS_KAUFCHECKS_PRO_MONAT = int(os.environ.get("AUTO_KI_PLUS_KAUFCHECKS", "5"))
PLUS_VERKAUFSCHECKS_PRO_MONAT = int(os.environ.get("AUTO_KI_PLUS_VERKAUFSCHECKS", "1"))

# Log-Level für die App-eigenen Logger (uvicorn-Access-Logs bleiben unberührt).
# Ohne explizite Konfiguration surft die Root-Loglevel-Vorgabe auf WARNING und
# alle log.info(...)-Meldungen der App (DB-Pfad, Backups, Retries) sind unsichtbar.
LOG_LEVEL = os.environ.get("AUTO_KI_LOG_LEVEL", "INFO").upper()

# --- Auth (Phase 2b) ---
# In Produktion: langen Zufalls-String setzen, z.B. `openssl rand -hex 32`
JWT_SECRET = os.environ.get("AUTO_KI_JWT_SECRET", DEV_JWT_SECRET).strip()
JWT_EXPIRE_DAYS = int(os.environ.get("AUTO_KI_JWT_EXPIRE_DAYS", "7"))

# ---------------------------------------------------------------------------
# Eingabegrenzen und Abuse-Schutz (Security Block 3)
# ---------------------------------------------------------------------------
# P2-4: bcrypt hasht hoechstens 72 BYTES. Alles darueber wirft in bcrypt 5 einen
# Fehler (vorher: HTTP 500). Die Grenze ist deshalb eine Eingabepruefung, kein
# Wechsel des Hash-Verfahrens.
PASSWORT_MAX_BYTES = 72
PASSWORT_MIN_ZEICHEN = 8
# RFC 5321: 254 Zeichen sind die maximale Laenge einer E-Mail-Adresse.
EMAIL_MAX_ZEICHEN = 254

# P2-8: Serverseitige Groessengrenzen fuer gespeicherte Inhalte. Grosszuegig
# gewaehlt — ein echter Check-Bericht liegt bei wenigen zehntausend Zeichen —,
# aber endlich, damit die 8-MB-Request-Grenze nicht zur Speichergrenze wird.
CHECK_TITEL_MAX = 200
CHECK_EINGABE_MAX_ZEICHEN = int(os.environ.get("AUTO_KI_CHECK_EINGABE_MAX", "100000"))
CHECK_ERGEBNIS_MAX_ZEICHEN = int(os.environ.get("AUTO_KI_CHECK_ERGEBNIS_MAX", "400000"))
NACHRICHT_MAX_ZEICHEN = int(os.environ.get("AUTO_KI_NACHRICHT_MAX", "20000"))
CONVERSATION_TITEL_MAX = 200

# P2-7: Fehlgeschlagene Check-Laeufe kosten Provider-Geld, auch wenn das
# Kontingent korrekt zurueckerstattet wird. Ein eigener TAGESZAEHLER begrenzt
# deshalb die VERSUCHE — unabhaengig vom Guthaben und ohne Rueckerstattung.
# Grosszuegig: ein normaler Nutzer kommt hier nie an.
CHECK_VERSUCHE_PRO_TAG = int(os.environ.get("AUTO_KI_CHECK_VERSUCHE_PRO_TAG", "8"))

# ---------------------------------------------------------------------------
# Externe Provider: Reliability- und Kostenbudgets
# ---------------------------------------------------------------------------
# Alle Werte beschreiben REALE HTTP-Aufrufe; Wiederholungen zählen mit. Die
# Feature-Budgets sind bewusst großzügiger als der Normalpfad, aber endlich.
# Damit können weder adaptive Suchleitern noch verschachtelte Fallbacks aus
# einer einzelnen Nutzeraktion unkontrolliert Provider-Kosten erzeugen.
GEMINI_TIMEOUT_SECONDS = float(os.environ.get("AUTO_KI_GEMINI_TIMEOUT_SECONDS", "45"))
GEMINI_TOTAL_TIMEOUT_SECONDS = float(os.environ.get("AUTO_KI_GEMINI_TOTAL_TIMEOUT_SECONDS", "75"))
GEMINI_STREAM_TIMEOUT_SECONDS = float(os.environ.get("AUTO_KI_GEMINI_STREAM_TIMEOUT_SECONDS", "75"))
GEMINI_MAX_ATTEMPTS = int(os.environ.get("AUTO_KI_GEMINI_MAX_ATTEMPTS", "3"))
GEMINI_RETRY_BASE_SECONDS = float(os.environ.get("AUTO_KI_GEMINI_RETRY_BASE_SECONDS", "1"))
GEMINI_RETRY_CAP_SECONDS = float(os.environ.get("AUTO_KI_GEMINI_RETRY_CAP_SECONDS", "8"))

TAVILY_CONNECT_TIMEOUT_SECONDS = float(os.environ.get("AUTO_KI_TAVILY_CONNECT_TIMEOUT_SECONDS", "5"))
TAVILY_READ_TIMEOUT_SECONDS = float(os.environ.get("AUTO_KI_TAVILY_READ_TIMEOUT_SECONDS", "15"))
TAVILY_TOTAL_TIMEOUT_SECONDS = float(os.environ.get("AUTO_KI_TAVILY_TOTAL_TIMEOUT_SECONDS", "20"))
TAVILY_MAX_ATTEMPTS = int(os.environ.get("AUTO_KI_TAVILY_MAX_ATTEMPTS", "3"))
TAVILY_RETRY_BASE_SECONDS = float(os.environ.get("AUTO_KI_TAVILY_RETRY_BASE_SECONDS", "1"))
TAVILY_MAX_RESULTS = int(os.environ.get("AUTO_KI_TAVILY_MAX_RESULTS", "20"))

PROVIDER_GLOBAL_MAX_CONCURRENT = int(os.environ.get("AUTO_KI_PROVIDER_GLOBAL_MAX_CONCURRENT", "32"))
PROVIDER_USER_MAX_CONCURRENT = int(os.environ.get("AUTO_KI_PROVIDER_USER_MAX_CONCURRENT", "2"))
PROVIDER_ADMIN_IMAGE_BATCH_MAX = int(os.environ.get("AUTO_KI_PROVIDER_ADMIN_IMAGE_BATCH_MAX", "20"))

# Maximale echte Provider-Aufrufe pro Aktion (inklusive Retries und Tavily
# Extract). Werte sind per ENV überschreibbar, die Namen werden in
# PROVIDER_RELIABILITY.md dokumentiert.
PROVIDER_FEATURE_LIMITS: dict[str, dict[str, int]] = {
    "chat": {"gemini": int(os.environ.get("AUTO_KI_PROVIDER_CHAT_GEMINI_MAX", "3")),
             "tavily": int(os.environ.get("AUTO_KI_PROVIDER_CHAT_TAVILY_MAX", "9"))},
    "autofinder": {"gemini": int(os.environ.get("AUTO_KI_PROVIDER_AUTOFINDER_GEMINI_MAX", "6")),
                    "tavily": int(os.environ.get("AUTO_KI_PROVIDER_AUTOFINDER_TAVILY_MAX", "6"))},
    "kaufcheck": {"gemini": int(os.environ.get("AUTO_KI_PROVIDER_KAUFCHECK_GEMINI_MAX", "4")),
                   "tavily": int(os.environ.get("AUTO_KI_PROVIDER_KAUFCHECK_TAVILY_MAX", "16"))},
    "verkaufscheck": {"gemini": int(os.environ.get("AUTO_KI_PROVIDER_VERKAUFSCHECK_GEMINI_MAX", "4")),
                       "tavily": int(os.environ.get("AUTO_KI_PROVIDER_VERKAUFSCHECK_TAVILY_MAX", "16"))},
    "inseratsoptimierung": {"gemini": int(os.environ.get("AUTO_KI_PROVIDER_INSERAT_GEMINI_MAX", "3")),
                            "tavily": 0},
    "analyse_frage": {"gemini": int(os.environ.get("AUTO_KI_PROVIDER_ANALYSE_FRAGE_GEMINI_MAX", "3")),
                       "tavily": 0},
    "ersatzteile": {"gemini": int(os.environ.get("AUTO_KI_PROVIDER_ERSATZTEILE_GEMINI_MAX", "3")),
                     "tavily": int(os.environ.get("AUTO_KI_PROVIDER_ERSATZTEILE_TAVILY_MAX", "6"))},
    "admin": {"gemini": int(os.environ.get("AUTO_KI_PROVIDER_ADMIN_GEMINI_MAX", "8")),
              "tavily": 0},
    "admin_image_batch": {"gemini": PROVIDER_ADMIN_IMAGE_BATCH_MAX,
                          "tavily": 0},
    "unscoped": {"gemini": int(os.environ.get("AUTO_KI_PROVIDER_UNSCOPED_GEMINI_MAX", "3")),
                 "tavily": int(os.environ.get("AUTO_KI_PROVIDER_UNSCOPED_TAVILY_MAX", "3"))},
}

GEMINI_CHAT_MAX_OUTPUT_TOKENS = int(os.environ.get("AUTO_KI_GEMINI_CHAT_MAX_OUTPUT_TOKENS", "2048"))
GEMINI_ANALYSE_MAX_OUTPUT_TOKENS = int(os.environ.get("AUTO_KI_GEMINI_ANALYSE_MAX_OUTPUT_TOKENS", "2048"))
GEMINI_JSON_MAX_OUTPUT_TOKENS = int(os.environ.get("AUTO_KI_GEMINI_JSON_MAX_OUTPUT_TOKENS", "16384"))
GEMINI_AUX_MAX_OUTPUT_TOKENS = int(os.environ.get("AUTO_KI_GEMINI_AUX_MAX_OUTPUT_TOKENS", "4096"))
GEMINI_MAX_INPUT_CHARS = int(os.environ.get("AUTO_KI_GEMINI_MAX_INPUT_CHARS", "120000"))

# P2-5: Gratis-Kontingente (Chat, AutoFinder, Analyse-Rueckfragen) erst nach
# bestaetigter E-Mail. Bezahlte Leistungen bleiben unberuehrt.
EMAIL_VERIFIKATION_AKTIV = os.environ.get("AUTO_KI_EMAIL_VERIFIKATION", "1").strip() not in ("0", "false", "no")
EMAIL_VERIFIKATION_GUELTIG_STUNDEN = int(os.environ.get("AUTO_KI_EMAIL_VERIFIKATION_STUNDEN", "48"))

# ---------------------------------------------------------------------------
# API-Dokumentation (P2-2)
# ---------------------------------------------------------------------------
# In Produktion aus: /docs, /redoc und /openapi.json listen sonst oeffentlich
# jede Route inklusive der Admin-Endpunkte. Lokal bleibt sie eingeschaltet.
DOCS_AKTIV = not IS_PRODUCTION

# Auth-Cookie nur ueber HTTPS senden. Lokal (http://localhost) wuerde der Browser
# ein Secure-Cookie verwerfen — deshalb folgt das Flag der Umgebung, nicht einer
# eigenen Einstellung.
COOKIE_SECURE = IS_PRODUCTION

# CORS-Origins die Cookies senden dürfen (komma-getrennt in Env-Var)
# WICHTIG: "null" NICHT aufnehmen — Browser senden Origin: null aus sandboxed
# iframes und file://-Kontexten; zusammen mit allow_credentials=True würde das
# jeder lokal geöffneten HTML-Datei erlauben, authentifizierte Requests mit
# dem Nutzer-Cookie zu senden (klassische CORS-Fehlkonfiguration).
_cors_default = "http://localhost:3000,http://localhost:3001,http://localhost:5173"
CORS_ORIGINS: list[str] = [
    o.strip() for o in os.environ.get("AUTO_KI_CORS_ORIGINS", _cors_default).split(",") if o.strip()
]
# Für die Start-Warnung in app.main: True, wenn AUTO_KI_CORS_ORIGINS nie gesetzt
# wurde und die App noch auf den reinen Dev-Localhost-Default läuft.
CORS_IS_DEFAULT: bool = "AUTO_KI_CORS_ORIGINS" not in os.environ

# Stripe — Phase 2d
# Testmodus-Keys unter https://dashboard.stripe.com/test/apikeys
# Webhook-Secret via: stripe listen --forward-to localhost:8000/api/v1/payments/webhook
STRIPE_SECRET_KEY       = os.environ.get("STRIPE_SECRET_KEY", "")
# .strip(): ein nur aus Leerzeichen bestehender Wert ist KEIN Secret. Leer heisst
# fuer den Webhook: geschlossen (siehe payments.stripe_webhook).
STRIPE_WEBHOOK_SECRET   = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_PRICE_LIGHT      = os.environ.get("STRIPE_PRICE_LIGHT", "")       # price_xxx
STRIPE_PRICE_PRO        = os.environ.get("STRIPE_PRICE_PRO", "")         # price_xxx
STRIPE_PRICE_MAX        = os.environ.get("STRIPE_PRICE_MAX", "")         # price_xxx
STRIPE_PRICE_EINZELKAUF = os.environ.get("STRIPE_PRICE_EINZELKAUF", "")  # price_xxx (one_time)

# Consumer Pricing V1 — getrennte Einmalprodukte je Check-Art.
# Bewusst ZWEI eigene Price-IDs statt eines gemeinsamen "Einzelkauf"-Preises:
# ein gekaufter KaufCheck darf keinen VerkaufsCheck freischalten (und umgekehrt).
# STRIPE_PRICE_EINZELKAUF bleibt fuer Bestandskaeufe/Legacy erhalten und schreibt
# weiterhin auf das generische Kontingent.
STRIPE_PRICE_KAUFCHECK     = os.environ.get("STRIPE_PRICE_KAUFCHECK", "")      # price_xxx (one_time, 5,99 EUR)
STRIPE_PRICE_VERKAUFSCHECK = os.environ.get("STRIPE_PRICE_VERKAUFSCHECK", "")  # price_xxx (one_time, 8,99 EUR)
# VIRA Plus — das EINZIGE neu beworbene Abo. Recurring/monatlich.
# Die Legacy-Preise LIGHT/PRO/MAX bleiben ausschliesslich fuer
# Bestandskunden konfiguriert und werden nirgends mehr angeboten.
STRIPE_PRICE_PLUS = os.environ.get("STRIPE_PRICE_PLUS", "")  # price_xxx (recurring monthly, 16,99 EUR)
FRONTEND_URL            = os.environ.get("FRONTEND_URL", "http://localhost:3000")

# Stripe-LIVE ist ein eigener Release-Schritt. Bis dahin verweigert Produktion
# den Start mit einem Live-Key (siehe deployment_fehler) — ein versehentlich
# eingetragener sk_live_... loest so keine echten Zahlungen aus.
STRIPE_LIVE_ERLAUBT = os.environ.get("AUTO_KI_STRIPE_LIVE_ERLAUBT", "0").strip() == "1"

# ---------------------------------------------------------------------------
# E-Mail-Versand (Bestaetigungslinks) — Brevo-API per HTTPS
# ---------------------------------------------------------------------------
# Kein SMTP: Railway blockiert auf dem laufenden Plan (Hobby) jeden
# ausgehenden SMTP-Port (25/465/587/2525) netzwerkseitig, HTTPS (443) ist
# dagegen offen — daher die Transaktionsmail-API des ohnehin eingerichteten
# Anbieters Brevo statt eines SMTP-Sockets (siehe app/mailer.py). Ohne
# AUTO_KI_BREVO_API_KEY wird nichts verschickt — die App laeuft weiter,
# meldet das beim Start aber laut (siehe app.main._warn_if_insecure_defaults).
BREVO_API_KEY = os.environ.get("AUTO_KI_BREVO_API_KEY", "").strip()
BREVO_TIMEOUT_SECONDS = float(os.environ.get("AUTO_KI_BREVO_TIMEOUT_SECONDS", "10"))
# Absender, z.B. "ENFAL <noreply@getenfal.de>" — muss ein bei Brevo
# verifizierter Absender/eine verifizierte Domain sein, sonst weist Brevo die
# Mail zurueck.
MAIL_FROM     = os.environ.get("AUTO_KI_MAIL_FROM", "").strip()
MAIL_AKTIV    = bool(BREVO_API_KEY and MAIL_FROM)

GEMINI_API_KEY      = os.environ.get("GEMINI_API_KEY", "")
# Migration Gemini 2.5 Flash -> 3.7 Flash (Consumer-Bake-off + Retest bestanden:
# Empfehlungs-Floor, Report-Sync, Wartungs-Guard — siehe Commits 7cc9b95/dbbb660
# auf kaufcheck-planb). EIN zentraler Wert für alle Consumer-Aufrufer
# (Kauf-/Verkaufscheck, Ersatzteil, Chat — geteilt über app/car_lookup.py und
# app/llm.py) und für app/admin_llm.py (interne Tooling-Nutzung derselben
# Konstante — bewusst keine zweite parallele Modellkonfiguration).
LLM_MODEL           = os.environ.get("AUTO_KI_LLM_MODEL",       "gemini-3.7-flash")
# "gemini-2.5-flash-lite" liefert für diesen API-Key inzwischen 404 ("no longer
# available to new users") — Google hat das Modell für neue Nutzer gesperrt.
# "gemini-flash-lite-latest" ist der von Google gepflegte Alias auf das jeweils
# aktuelle Lite-Modell und dadurch nicht von künftigen Modell-Absetzungen betroffen.
FAST_LLM_MODEL      = os.environ.get("AUTO_KI_FAST_LLM_MODEL",  "gemini-flash-lite-latest")

# Tavily Search — optional, aktiviert Echtzeit-Websuche für Preise/Rückrufe
# Free-Plan: 1.000 Abfragen/Monat, kein Kreditkarte — https://app.tavily.com/
# Windows/PowerShell: $env:TAVILY_API_KEY = "tvly-..."
# Dauerhaft: In .env eintragen: TAVILY_API_KEY=tvly-...
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")

# ---------------------------------------------------------------------------
# Source-Policy: Freigabe automatischer Marktpreis-Quellen
# ---------------------------------------------------------------------------
# PRODUCTION-DEFAULT: LEER. Keine reale Marktplatz-Domain ist automatisch fuer die
# Marktpreisbildung freigegeben, solange sie nicht ausdruecklich konfiguriert
# wurde — auch dann nicht, wenn sie technisch erreichbar ist und fachlich
# einwandfreie Inserate liefert.
#
# Das ist eine PRODUKT-/FREIGABE-Entscheidung, keine Rechtsbewertung und keine
# Qualitaetsaussage ueber die Inserate. Welche Quellen ueber offizielle Such-APIs
# bzw. Nutzungsrechte qualifiziert werden, klaert eine eigene Etappe.
#
# Aktivierung ohne Codeaenderung (Etappe 2+), kommaseparierte Domainliste:
#     AUTO_KI_ALLOWED_MARKET_SOURCES=beispiel-api.de,zweite-quelle.de
#
# Der Marktvergleich verwirft Treffer nicht freigegebener Quellen mit einem
# EIGENEN, neutral formulierten Grund (app/web_search.SOURCE_POLICY_GRUND) —
# getrennt von fachlichen Ablehnungen wie "anderes Modell".
_ROH_MARKET_SOURCES = os.environ.get("AUTO_KI_ALLOWED_MARKET_SOURCES", "")
ALLOWED_MARKET_SOURCES = frozenset(
    teil.strip().lower() for teil in _ROH_MARKET_SOURCES.split(",") if teil.strip()
)


# ---------------------------------------------------------------------------
# Produktions-Startpruefung
# ---------------------------------------------------------------------------
def produktions_fehler(
    *,
    is_production: bool | None = None,
    jwt_secret: str | None = None,
    api_key: str | None = None,
    admin_api_key: str | None = None,
    webhook_secret: str | None = None,
) -> list[str]:
    """Liste der Konfigurationsfehler, mit denen Produktion NICHT starten darf.

    Leer = startbereit. In Entwicklung immer leer (Dev-Defaults sind dort
    gewollt). Parameter nur fuer Tests; ohne Angabe gelten die Modulwerte.
    Die Meldungen nennen nur Variablennamen, NIE Werte.
    """
    is_production = IS_PRODUCTION if is_production is None else is_production
    if not is_production:
        return []
    jwt_secret = JWT_SECRET if jwt_secret is None else jwt_secret.strip()
    api_key = API_KEY if api_key is None else api_key.strip()
    admin_api_key = ADMIN_API_KEY if admin_api_key is None else admin_api_key.strip()
    webhook_secret = STRIPE_WEBHOOK_SECRET if webhook_secret is None else webhook_secret.strip()

    fehler: list[str] = []
    if not jwt_secret or jwt_secret == DEV_JWT_SECRET or len(jwt_secret) < MIN_SECRET_LEN:
        fehler.append(f"AUTO_KI_JWT_SECRET fehlt, ist der Dev-Default oder kuerzer als {MIN_SECRET_LEN} Zeichen.")
    if not api_key or api_key == DEV_API_KEY:
        fehler.append("AUTO_KI_API_KEY fehlt oder ist der Dev-Default.")
    if jwt_secret and jwt_secret == api_key:
        # Der Consumer-Key steht im Frontend-Bundle — als JWT-Secret koennte
        # damit jeder Login-Tokens faelschen.
        fehler.append("AUTO_KI_JWT_SECRET darf nicht gleich AUTO_KI_API_KEY sein.")
    if not webhook_secret:
        fehler.append("STRIPE_WEBHOOK_SECRET fehlt — Zahlungen koennten nicht verifiziert werden.")
    # Admin-Key fehlt = Admin geschlossen, das ist ein zulaessiger Zustand.
    # Ist er gesetzt, muss er aber ein echtes, eigenes Secret sein.
    if admin_api_key:
        if admin_api_key == api_key:
            fehler.append("AUTO_KI_ADMIN_API_KEY darf nicht gleich AUTO_KI_API_KEY sein (der steht im Frontend).")
        if admin_api_key == DEV_API_KEY or len(admin_api_key) < MIN_SECRET_LEN:
            fehler.append(f"AUTO_KI_ADMIN_API_KEY ist der Dev-Default oder kuerzer als {MIN_SECRET_LEN} Zeichen.")
    return fehler


def deployment_fehler(
    *,
    is_production: bool | None = None,
    cors_origins: list[str] | None = None,
    frontend_url: str | None = None,
    stripe_secret_key: str | None = None,
    stripe_live_erlaubt: bool | None = None,
) -> list[str]:
    """Konfigurationsfehler der Produktions-INFRASTRUKTUR (Domains, Stripe-Modus).

    Getrennt von produktions_fehler() (Secrets), damit beide einzeln testbar
    bleiben. In Entwicklung immer leer. Meldungen nennen nie Secret-Werte.
    """
    is_production = IS_PRODUCTION if is_production is None else is_production
    if not is_production:
        return []
    cors_origins = CORS_ORIGINS if cors_origins is None else cors_origins
    frontend_url = FRONTEND_URL if frontend_url is None else frontend_url
    stripe_secret_key = STRIPE_SECRET_KEY if stripe_secret_key is None else stripe_secret_key
    stripe_live_erlaubt = STRIPE_LIVE_ERLAUBT if stripe_live_erlaubt is None else stripe_live_erlaubt

    fehler: list[str] = []
    # CORS mit allow_credentials=True: jede erlaubte Origin darf mit dem
    # Nutzer-Cookie Anfragen stellen. In Produktion deshalb nur echte
    # HTTPS-Domains — kein "*", kein "null", kein http://, kein localhost.
    if not cors_origins:
        fehler.append("AUTO_KI_CORS_ORIGINS ist leer.")
    for origin in cors_origins:
        o = origin.strip().lower()
        if o in ("*", "null") or not o.startswith("https://") or "localhost" in o or "127.0.0.1" in o:
            fehler.append(f"AUTO_KI_CORS_ORIGINS enthaelt eine in Produktion unzulaessige Origin: {origin!r}")
    # FRONTEND_URL landet in Stripe-Redirects und Bestaetigungslinks.
    fu = (frontend_url or "").strip().lower()
    if not fu.startswith("https://") or "localhost" in fu or "127.0.0.1" in fu:
        fehler.append("FRONTEND_URL muss in Produktion eine https://-Adresse der echten Domain sein.")
    # Bis zur ausdruecklichen Freigabe (Release-Schritt "Stripe LIVE") bleibt
    # Stripe im Testmodus. Ein versehentlich eingetragener Live-Key darf nicht
    # still echte Zahlungen ausloesen.
    if (stripe_secret_key or "").strip().startswith(("sk_live_", "rk_live_")) and not stripe_live_erlaubt:
        fehler.append("STRIPE_SECRET_KEY ist ein Live-Key, aber AUTO_KI_STRIPE_LIVE_ERLAUBT ist nicht 1.")
    return fehler


def validiere_produktion() -> None:
    """Bricht den Start ab, wenn Produktion unsicher konfiguriert ist."""
    fehler = produktions_fehler() + deployment_fehler()
    if fehler:
        raise RuntimeError(
            "Unsichere Produktionskonfiguration (AUTO_KI_ENV=%s) — Start verweigert:\n  - %s"
            % (ENVIRONMENT, "\n  - ".join(fehler))
        )
