"""
Regressionstests Deployment-Infrastruktur (Railway-Produktion).

  D-1  Produktion verweigert den Start bei unzulaessigen Domains/CORS/Stripe-Live.
  D-2  E-Mail-Versand: nur verschluesselt, Token nie im Log, Fehler nie fatal.
  D-3  Registrierung/Resend stossen den Versand an; Produktion leakt keinen Token.
  D-4  /health: 200 gesund, 503 ohne Datenbank, keine internen Details.
  D-5  Chroma-Bootstrap: frisches Volume wird aufgebaut, bestehender Index nie.
  D-6  Stripe-Webhook: ungueltige Signatur ist im Log sichtbar.
  D-7  Image-/Plattform-Konfiguration (Dockerfile, Entrypoint, railway.json).
  D-8  Client-IP nach Railway-Doku (X-Real-IP) + Spoofing-Gegenprobe.

Kein Netzwerk, kein SMTP-Server, keine Provider: alles gemockt.
Ausfuehren: python test_deployment_config.py
"""
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_HIER = os.path.dirname(os.path.abspath(__file__))
_TMP = tempfile.mkdtemp(prefix="enfal_deploy_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_TMP, "chroma")
os.environ["AUTO_KI_DB_BACKUP_DIR"] = os.path.join(_TMP, "bk")
os.environ["AUTO_KI_ENV"] = "development"
os.environ["AUTO_KI_RATE_LIMIT"] = "2000/minute"
sys.path.insert(0, _HIER)

import app.database as db  # noqa: E402
db.ensure_tables()

from fastapi.testclient import TestClient  # noqa: E402

import app.config as config  # noqa: E402
import app.main as main  # noqa: E402
import app.mailer as mailer  # noqa: E402
import app.routers.user_auth as auth_modul  # noqa: E402

FEHLER = []


def check(name, cond, detail=""):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        FEHLER.append(name)


class LogFang(logging.Handler):
    def __init__(self):
        super().__init__()
        self.zeilen = []

    def emit(self, record):
        self.zeilen.append(record.getMessage())


def fange_logs(name):
    h = LogFang()
    logging.getLogger(name).addHandler(h)
    return h


# ════════════════════════════════════════════════════════════════════════════
# D-1  deployment_fehler()
# ════════════════════════════════════════════════════════════════════════════
GUT = dict(is_production=True, cors_origins=["https://getenfal.de", "https://app.getenfal.de"],
           frontend_url="https://app.getenfal.de", stripe_secret_key="sk_test_" + "x" * 24,
           stripe_live_erlaubt=False)
check("D-1: vollstaendige Produktionswerte bestehen", config.deployment_fehler(**GUT) == [],
      str(config.deployment_fehler(**GUT)))
check("D-1: Entwicklung prueft nichts",
      config.deployment_fehler(**{**GUT, "is_production": False, "cors_origins": ["*"]}) == [])
for name, origins in {
    "Wildcard *": ["*"],
    "null": ["null"],
    "http://": ["http://getenfal.de"],
    "localhost": ["https://localhost:3000"],
    "127.0.0.1": ["https://127.0.0.1"],
    "leer": [],
    "fremde neben echter http-Domain": ["https://getenfal.de", "http://evil.example"],
}.items():
    check(f"D-1: CORS {name} -> Start verweigert",
          any("CORS" in f for f in config.deployment_fehler(**{**GUT, "cors_origins": origins})))
for url in ("http://app.getenfal.de", "https://localhost:3000", ""):
    check(f"D-1: FRONTEND_URL {url!r} -> Start verweigert",
          any("FRONTEND_URL" in f for f in config.deployment_fehler(**{**GUT, "frontend_url": url})))
live = "sk_live_" + "y" * 24
f_live = config.deployment_fehler(**{**GUT, "stripe_secret_key": live})
check("D-1: Stripe-Live-Key ohne Freigabe -> Start verweigert", any("Live-Key" in f for f in f_live))
check("D-1: Meldung nennt den Key-Wert nicht", not any(live in f for f in f_live))
check("D-1: Restricted-Live-Key ebenso gesperrt",
      bool(config.deployment_fehler(**{**GUT, "stripe_secret_key": "rk_live_" + "y" * 24})))
check("D-1: Live-Key mit AUTO_KI_STRIPE_LIVE_ERLAUBT=1 zulaessig",
      config.deployment_fehler(**{**GUT, "stripe_secret_key": live, "stripe_live_erlaubt": True}) == [])


def subprozess(py_code, env_extra, timeout=180):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AUTO_KI_", "STRIPE_", "FRONTEND_URL"))}
    tmp = tempfile.mkdtemp(prefix="enfal_deploy_sub_")
    env.update({"AUTO_KI_DB_PATH": os.path.join(tmp, "t.db"),
                "AUTO_KI_CHROMA_PATH": os.path.join(tmp, "chroma"),
                "AUTO_KI_DB_BACKUP_DIR": os.path.join(tmp, "bk"),
                "PYTHONIOENCODING": "utf-8"})
    env.update(env_extra)
    py_code = "import dotenv\ndotenv.load_dotenv = lambda *a, **k: False\n" + py_code
    return subprocess.run([sys.executable, "-c", py_code], cwd=_HIER, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=timeout)


PROD = {"AUTO_KI_ENV": "production", "AUTO_KI_JWT_SECRET": "j" * 64, "AUTO_KI_API_KEY": "c" * 48,
        "STRIPE_WEBHOOK_SECRET": "whsec_" + "w" * 32,
        "AUTO_KI_CORS_ORIGINS": "https://getenfal.de,https://app.getenfal.de",
        "FRONTEND_URL": "https://app.getenfal.de"}
PRUEFE = "import app.config as c\ntry:\n    c.validiere_produktion(); print('START_OK')\nexcept RuntimeError as e:\n    print('VERWEIGERT', e)\n"
r = subprozess(PRUEFE, PROD)
check("D-1: echte Produktions-ENV besteht validiere_produktion()", "START_OK" in r.stdout, r.stdout + r.stderr[-300:])
r = subprozess(PRUEFE, {k: v for k, v in PROD.items() if k != "AUTO_KI_CORS_ORIGINS"})
check("D-1: Produktion OHNE AUTO_KI_CORS_ORIGINS (localhost-Default) -> verweigert",
      "VERWEIGERT" in r.stdout and "localhost" in r.stdout, r.stdout + r.stderr[-300:])
r = subprozess(PRUEFE, {k: v for k, v in PROD.items() if k != "FRONTEND_URL"})
check("D-1: Produktion OHNE FRONTEND_URL -> verweigert", "VERWEIGERT" in r.stdout, r.stdout + r.stderr[-300:])
r = subprozess(PRUEFE, {**PROD, "STRIPE_SECRET_KEY": "sk_live_" + "z" * 24})
check("D-1: Produktion mit Stripe-Live-Key -> verweigert (Testmode-Phase)",
      "VERWEIGERT" in r.stdout and "sk_live_zzz" not in r.stdout, r.stdout + r.stderr[-300:])

# ════════════════════════════════════════════════════════════════════════════
# D-2  Mailer
# ════════════════════════════════════════════════════════════════════════════
import smtplib  # noqa: E402

PROTOKOLL = []


class FakeSMTP:
    starttls_fehler = False

    def __init__(self, host, port, timeout=None, context=None):
        PROTOKOLL.append(("connect", type(self).__name__, host, port, timeout))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        PROTOKOLL.append(("ehlo",))

    def starttls(self, context=None):
        if FakeSMTP.starttls_fehler:
            raise smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server.")
        PROTOKOLL.append(("starttls", context is not None))

    def login(self, user, pw):
        PROTOKOLL.append(("login", user))

    def send_message(self, msg):
        PROTOKOLL.append(("send", msg))


class FakeSMTPSSL(FakeSMTP):
    pass


_orig = (smtplib.SMTP, smtplib.SMTP_SSL)
smtplib.SMTP, smtplib.SMTP_SSL = FakeSMTP, FakeSMTPSSL
_cfg_alt = {k: getattr(config, k) for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD",
                                            "MAIL_FROM", "MAIL_AKTIV", "FRONTEND_URL")}
try:
    logs = fange_logs("app.mailer")
    config.MAIL_AKTIV = False
    ok = mailer.sende_bestaetigungsmail("kunde@example.com", "TOKEN_OHNE_SMTP_123", user_id=7)
    check("D-2: ohne SMTP -> kein Versand, Rueckgabe False", ok is False and not PROTOKOLL)
    check("D-2: ohne SMTP -> Fehler im Log (mit user_id)", any("NICHT versendet" in z and "7" in z for z in logs.zeilen))

    config.SMTP_HOST, config.SMTP_PORT, config.SMTP_USER = "smtp.example.test", 587, "noreply@getenfal.de"
    config.SMTP_PASSWORD, config.MAIL_FROM, config.MAIL_AKTIV = "pw", "ENFAL <noreply@getenfal.de>", True
    config.FRONTEND_URL = "https://app.getenfal.de/"
    token = "TOKEN_" + "k" * 40
    ok = mailer.sende_bestaetigungsmail("kunde@example.com", token, user_id=8)
    schritte = [s[0] for s in PROTOKOLL]
    check("D-2: Port 587 -> STARTTLS vor Login und Versand",
          ok and schritte.index("starttls") < schritte.index("login") < schritte.index("send"), str(schritte))
    check("D-2: STARTTLS mit Zertifikatspruefung (SSL-Kontext)", ("starttls", True) in PROTOKOLL)
    msg = [s[1] for s in PROTOKOLL if s[0] == "send"][0]
    text = msg.get_content()
    check("D-2: Link zeigt auf FRONTEND_URL mit Token im Fragment",
          f"https://app.getenfal.de/email-bestaetigen#token={token}" in text, text)
    check("D-2: Empfaenger/Absender korrekt", msg["To"] == "kunde@example.com" and "getenfal.de" in msg["From"])
    check("D-2: Timeout gesetzt", any(s[0] == "connect" and s[4] for s in PROTOKOLL))
    check("D-2: Token und Adresse NIE im Log",
          not any(token in z or "kunde@example.com" in z for z in logs.zeilen), str(logs.zeilen))

    PROTOKOLL.clear()
    config.SMTP_PORT = 465
    ok = mailer.sende_bestaetigungsmail("kunde@example.com", token, user_id=9)
    check("D-2: Port 465 -> implizites TLS (SMTP_SSL), kein STARTTLS",
          ok and PROTOKOLL[0][1] == "FakeSMTPSSL" and not any(s[0] == "starttls" for s in PROTOKOLL), str(PROTOKOLL[:2]))

    PROTOKOLL.clear()
    config.SMTP_PORT = 587
    FakeSMTP.starttls_fehler = True
    ok = mailer.sende_bestaetigungsmail("kunde@example.com", token, user_id=10)
    check("D-2: Server ohne STARTTLS -> KEIN Klartextversand, False, kein Crash",
          ok is False and not any(s[0] in ("send", "login") for s in PROTOKOLL), str(PROTOKOLL))
    FakeSMTP.starttls_fehler = False
    check("D-2: Header-Injection ueber Empfaenger unmoeglich",
          mailer.sende_bestaetigungsmail("a@b.de\nBcc: x@y.de", token, user_id=11) is False)
finally:
    smtplib.SMTP, smtplib.SMTP_SSL = _orig
    for k, v in _cfg_alt.items():
        setattr(config, k, v)

# ════════════════════════════════════════════════════════════════════════════
# D-3  Registrierung / Resend
# ════════════════════════════════════════════════════════════════════════════
H = {"Authorization": f"Bearer {config.API_KEY}"}
AUFRUFE = []
_orig_send = auth_modul._mail_im_hintergrund
auth_modul._mail_im_hintergrund = lambda email, token, user_id=None: AUFRUFE.append((email, token, user_id))
try:
    c = TestClient(main.app, raise_server_exceptions=False)
    auth_modul.limiter.reset()
    r = c.post("/api/v1/auth/register", json={"email": "neu@example.com", "password": "passwort123",
                                              "agb_akzeptiert": True})
    check("D-3: Registrierung 201", r.status_code == 201, r.text[:200])
    body = r.json()
    check("D-3: Versand genau einmal angestossen, mit derselben user_id",
          len(AUFRUFE) == 1 and AUFRUFE[0][0] == "neu@example.com" and AUFRUFE[0][2] == body.get("id"), str(AUFRUFE))
    check("D-3: verschickter Token = eingeloester Token (Dev-Testweg)",
          AUFRUFE and AUFRUFE[0][1] == body.get("verifikationstoken_dev"))
    r = c.post("/api/v1/auth/resend-verification")
    check("D-3: Resend stoesst neuen Versand an", r.status_code == 200 and len(AUFRUFE) == 2, r.text[:200])
    check("D-3: Resend-Token unterscheidet sich (alter verfaellt)", len(AUFRUFE) == 2 and AUFRUFE[1][1] != AUFRUFE[0][1])
    alt, neu = AUFRUFE[0][1], AUFRUFE[1][1]
    r_alt = c.post("/api/v1/auth/verify-email", json={"token": alt})
    r_neu = c.post("/api/v1/auth/verify-email", json={"token": neu})
    check("D-3: alter Token nach Resend ungueltig", r_alt.status_code == 400, r_alt.text[:120])
    check("D-3: neuer Token bestaetigt die Adresse", r_neu.status_code == 200 and r_neu.json().get("email_verified"))
    r = c.post("/api/v1/auth/resend-verification")
    check("D-3: bestaetigtes Konto -> kein weiterer Versand", r.json().get("email_verified") is True and len(AUFRUFE) == 2)
finally:
    auth_modul._mail_im_hintergrund = _orig_send

import threading  # noqa: E402

_angekommen = threading.Event()
_mail_args = []
_orig_mailer = auth_modul.sende_bestaetigungsmail
auth_modul.sende_bestaetigungsmail = lambda e, t, user_id=None: (_mail_args.append((e, t, user_id)), _angekommen.set())
try:
    auth_modul._mail_im_hintergrund("x@example.com", "TOK", 5)
    check("D-3: Hintergrund-Versand ruft den Mailer (eigener Thread)",
          _angekommen.wait(5) and _mail_args == [("x@example.com", "TOK", 5)], str(_mail_args))
finally:
    auth_modul.sende_bestaetigungsmail = _orig_mailer
check("D-3: Endpunkt-Signaturen unveraendert (Direktaufrufer bleiben kompatibel)",
      list(__import__("inspect").signature(auth_modul.register).parameters) == ["body", "response", "request"])

PROD_REG = (
    "import json\nfrom fastapi.testclient import TestClient\n"
    "import app.database as db; db.ensure_tables()\n"
    "import app.main as m, app.routers.user_auth as a\n"
    "a._mail_im_hintergrund = lambda *x, **k: print('VERSAND')\n"
    # https: in Produktion ist das Auth-Cookie Secure und reist nur ueber HTTPS.
    "c = TestClient(m.app, base_url='https://testserver')\n"
    "r = c.post('/api/v1/auth/register', json={'email':'p@example.com','password':'passwort123','agb_akzeptiert':True})\n"
    "print('ANTWORT', json.dumps(r.json()))\n"
    "print('COOKIE', r.headers.get('set-cookie','').lower().replace(' ',''))\n"
    "r = c.post('/api/v1/auth/resend-verification')\n"
    "print('RESEND', json.dumps(r.json()))\n"
)
r = subprozess(PROD_REG, PROD)
zeilen = {z.split(" ", 1)[0]: z.split(" ", 1)[1] for z in r.stdout.splitlines() if " " in z}
check("D-3 PROD: Registrierung ohne Token in der Antwort",
      "ANTWORT" in zeilen and "verifikationstoken" not in zeilen["ANTWORT"], r.stdout[-300:] + r.stderr[-300:])
check("D-3 PROD: Resend ohne Token in der Antwort",
      "RESEND" in zeilen and "verifikationstoken" not in zeilen["RESEND"], r.stdout[-300:])
ck = zeilen.get("COOKIE", "")
check("D-3 PROD: Auth-Cookie Secure + HttpOnly + SameSite=Lax, ohne Domain-Attribut (host-only api.*)",
      "secure" in ck and "httponly" in ck and "samesite=lax" in ck and "domain=" not in ck, ck)
check("D-3 PROD: Versand trotzdem angestossen (2x)", r.stdout.count("VERSAND") == 2, r.stdout[-300:])

# ════════════════════════════════════════════════════════════════════════════
# D-4  /health
# ════════════════════════════════════════════════════════════════════════════
c = TestClient(main.app, raise_server_exceptions=False)
r = c.get("/health")
check("D-4: /health 200 bei gesunder DB", r.status_code == 200 and r.json() == {"status": "ok", "db": "ok"}, r.text)
check("D-4: /health ohne Pfade/Tabellen", _TMP not in r.text and "tables" not in r.text)
_alt_db = main.DB_PATH
main.DB_PATH = Path(_TMP)  # ein Verzeichnis ist keine SQLite-Datei -> connect scheitert
try:
    r = c.get("/health")
    check("D-4: /health 503 ohne erreichbare DB (Railway gated den Deploy)", r.status_code == 503, r.text)
    check("D-4: 503-Antwort ohne Fehlertext/Pfad", _TMP not in r.text and r.json().get("db") == "fehler")
finally:
    main.DB_PATH = _alt_db

# ════════════════════════════════════════════════════════════════════════════
# D-5  Chroma-Bootstrap (Neuaufbau-Prozess gemockt — der echte dauert Minuten)
# ════════════════════════════════════════════════════════════════════════════
aufbau = []


def fake_rebuild(ziel=None):
    aufbau.append(ziel)
    Path(ziel).mkdir(parents=True, exist_ok=True)
    (Path(ziel) / "chroma.sqlite3").write_bytes(b"x")
    return {}


_orig_rebuild, _orig_chroma = main._chroma_aufbau_ausfuehren, main.CHROMA_PATH
main._chroma_aufbau_ausfuehren = fake_rebuild
try:
    frisch = Path(_TMP) / "vol" / "chroma"
    main.CHROMA_PATH = frisch
    main._chroma_bootstrap()
    check("D-5: frisches Volume -> Aufbau in Nachbarverzeichnis",
          len(aufbau) == 1 and Path(aufbau[0]).name == "chroma.bootstrap", str(aufbau))
    check("D-5: nach Erfolg am echten Pfad, Baustelle weg",
          (frisch / "chroma.sqlite3").exists() and not (frisch.parent / "chroma.bootstrap").exists())
    main._chroma_bootstrap()
    check("D-5: bestehender Index wird NICHT erneut aufgebaut", len(aufbau) == 1)

    def kaputt(ziel=None):
        Path(ziel).mkdir(parents=True, exist_ok=True)
        (Path(ziel) / "chroma.sqlite3").write_bytes(b"halb")
        raise RuntimeError("Abbruch mitten im Aufbau")

    main._chroma_aufbau_ausfuehren = kaputt
    abbruch = Path(_TMP) / "vol2" / "chroma"
    main.CHROMA_PATH = abbruch
    try:
        main._chroma_bootstrap()
        crash = False
    except Exception:
        crash = True
    check("D-5: Fehler im Aufbau ist nicht fatal", not crash)
    check("D-5: abgebrochener Aufbau hinterlaesst KEINEN halben Index am echten Pfad",
          not (abbruch / "chroma.sqlite3").exists() and not (abbruch.parent / "chroma.bootstrap").exists())
    main._chroma_aufbau_ausfuehren = fake_rebuild
    aufbau.clear()
    main.CHROMA_PATH = abbruch
    main._chroma_bootstrap()
    check("D-5: naechster Start baut nach Abbruch erneut auf", len(aufbau) == 1 and (abbruch / "chroma.sqlite3").exists())

    leer_db = Path(_TMP) / "leer.db"
    import sqlite3
    with sqlite3.connect(leer_db) as k:
        k.execute("CREATE TABLE baureihe (id TEXT)")
    _alt_db = main.DB_PATH
    main.DB_PATH, main.CHROMA_PATH = leer_db, Path(_TMP) / "vol3" / "chroma"
    aufbau.clear()
    main._chroma_bootstrap()
    check("D-5: ohne Fahrzeugbestand kein Aufbau", aufbau == [])
    main.DB_PATH = _alt_db

    # Echter App-Start auf frischem Volume: Aufbau im Hintergrund, App sofort bereit.
    import threading
    import app.llm as llm
    freigabe = threading.Event()

    def langsam(ziel=None):
        freigabe.wait(30)
        return fake_rebuild(ziel)

    main._chroma_aufbau_ausfuehren = langsam
    start_pfad = Path(_TMP) / "vol4" / "chroma"
    main.CHROMA_PATH = start_pfad
    aufbau.clear()
    with TestClient(main.app) as sc:
        gesund = sc.get("/health").status_code
        gesperrt = llm.CHROMA_AUFBAU.is_set()
        treffer = llm._vector_search("Steuerkette", [], n=3)
        check("D-5: App-Start wartet NICHT auf den Aufbau (Health 200 waehrenddessen)", gesund == 200)
        check("D-5: waehrend des Aufbaus ist die Vektorsuche gesperrt", gesperrt and treffer == [])
        check("D-5: waehrend des Aufbaus entsteht am echten Pfad keine leere Chroma-Datei",
              not (start_pfad / "chroma.sqlite3").exists())
        freigabe.set()
        for t in threading.enumerate():
            if t.name == "chroma-bootstrap":
                t.join(30)
        check("D-5: nach dem Aufbau Sperre aufgehoben, Index am echten Pfad",
              not llm.CHROMA_AUFBAU.is_set() and (start_pfad / "chroma.sqlite3").exists() and len(aufbau) == 1)
finally:
    main._chroma_aufbau_ausfuehren, main.CHROMA_PATH = _orig_rebuild, _orig_chroma

# ════════════════════════════════════════════════════════════════════════════
# D-6  Stripe-Webhook: ungueltige Signatur sichtbar
# ════════════════════════════════════════════════════════════════════════════
import app.routers.payments as pay  # noqa: E402

_alt_secret = pay.STRIPE_WEBHOOK_SECRET
pay.STRIPE_WEBHOOK_SECRET = "whsec_" + "t" * 32
logs = fange_logs("app.routers.payments")
try:
    r = c.post("/api/v1/payments/webhook", content=b'{"id":"evt_x"}', headers={"stripe-signature": "t=1,v1=falsch"})
    check("D-6: falsche Signatur -> 400", r.status_code == 400, r.text[:120])
    check("D-6: falsche Signatur steht im Log (Monitoring)", any("ungueltige Signatur" in z for z in logs.zeilen))
    check("D-6: Log ohne Secret", not any("tttttttt" in z for z in logs.zeilen))
finally:
    pay.STRIPE_WEBHOOK_SECRET = _alt_secret

# ════════════════════════════════════════════════════════════════════════════
# D-7  Image- und Plattformkonfiguration
# ════════════════════════════════════════════════════════════════════════════
docker = open(os.path.join(_HIER, "Dockerfile"), encoding="utf-8").read()
entry_roh = open(os.path.join(_HIER, "docker-entrypoint.sh"), "rb").read()
entry = entry_roh.decode("utf-8")
rj = json.load(open(os.path.join(_HIER, "railway.json"), encoding="utf-8"))
dign = open(os.path.join(_HIER, ".dockerignore"), encoding="utf-8").read().split()
gattr = open(os.path.join(_HIER, ".gitattributes"), encoding="utf-8").read()

check("D-7: Basis-Image fest auf Debian bookworm gepinnt", "FROM python:3.11-slim-bookworm" in docker)
check("D-7: Entrypoint gesetzt", 'ENTRYPOINT ["/app/docker-entrypoint.sh"]' in docker)
check("D-7: Start ohne --reload, mit --no-proxy-headers, per exec",
      "exec uvicorn app.main:app --no-proxy-headers" in docker and "--reload" not in docker)
check("D-7: ein Worker (kein --workers)", "--workers" not in docker)
check("D-7: Entrypoint wechselt per setpriv auf appuser",
      "setpriv --reuid=appuser --regid=appuser --init-groups" in entry and entry.rstrip().endswith('exec "$@"'))
check("D-7: Entrypoint chownt nur /data", "chown appuser:appuser" in entry and "/app" not in entry.split("set -eu", 1)[1])
check("D-7: Entrypoint mit LF (sonst '/bin/sh^M' im Linux-Container)", b"\r\n" not in entry_roh)
check("D-7: .gitattributes erzwingt LF fuer *.sh", "*.sh text eol=lf" in gattr)
check("D-7: Embedding-Modell im Image vorgeladen", "DefaultEmbeddingFunction" in docker and "ENV HOME=/home/appuser" in docker)
check("D-7: AUTO_KI_ENV=production + /data-Pfade im Image",
      all(x in docker for x in ("AUTO_KI_ENV=production", "AUTO_KI_DB_PATH=/data/auto_ki.db",
                                "AUTO_KI_CHROMA_PATH=/data/chroma", "AUTO_KI_DB_BACKUP_DIR=/data/backups")))
check("D-7: railway.json Healthcheck /health", rj["deploy"]["healthcheckPath"] == "/health")
check("D-7: drainingSeconds deckt das Gemini-Gesamtbudget ab",
      rj["deploy"].get("drainingSeconds", 0) >= config.GEMINI_TOTAL_TIMEOUT_SECONDS, str(rj["deploy"]))
check("D-7: eine Replica (Volumes erlauben keine Replicas)", rj["deploy"].get("numReplicas", 1) == 1)
for muster in (".env", ".env.*", "*.db", "wiederherstellungspasswort.txt", "diagnose_runs/", "test_*.py"):
    check(f"D-7: .dockerignore schliesst {muster} aus", muster in dign)
check("D-7: .dockerignore laesst db/-Seed im Image", "db/" not in dign)

# ════════════════════════════════════════════════════════════════════════════
# D-8  Client-IP nach Railway-Doku (X-Real-IP) + Spoofing
# ════════════════════════════════════════════════════════════════════════════
import app.client_ip as cip  # noqa: E402
from starlette.requests import Request  # noqa: E402


def anfrage(gegenstelle, headers):
    return Request({"type": "http", "client": (gegenstelle, 1234),
                    "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]})


_alt = (cip.TRUSTED_PROXY_HOPS, cip.CLIENT_IP_HEADER)
try:
    cip.TRUSTED_PROXY_HOPS, cip.CLIENT_IP_HEADER = 0, "x-forwarded-for"
    check("D-8: Default 0 Hops -> Header ignoriert (Railway-Proxy-IP zaehlt)",
          cip.klient_ip(anfrage("100.64.0.5", {"X-Real-IP": "203.0.113.9", "X-Forwarded-For": "1.2.3.4"})) == "100.64.0.5")
    cip.TRUSTED_PROXY_HOPS, cip.CLIENT_IP_HEADER = 1, "x-real-ip"
    check("D-8: x-real-ip + 1 Hop + Proxy im Vertrauensnetz -> echte Client-IP",
          cip.klient_ip(anfrage("100.64.0.5", {"X-Real-IP": "203.0.113.9"})) == "203.0.113.9")
    check("D-8: Spoofing: gefaelschter X-Forwarded-For aendert x-real-ip-Schluessel nicht",
          cip.klient_ip(anfrage("100.64.0.5", {"X-Real-IP": "203.0.113.9", "X-Forwarded-For": "1.2.3.4"})) == "203.0.113.9")
    check("D-8: Spoofing: Direktzugriff aus fremdem Netz -> Header wertlos",
          cip.klient_ip(anfrage("198.51.100.7", {"X-Real-IP": "1.2.3.4"})) == "198.51.100.7")
    check("D-8: Muell im Header -> Gegenstelle",
          cip.klient_ip(anfrage("100.64.0.5", {"X-Real-IP": "nicht-eine-ip"})) == "100.64.0.5")
finally:
    cip.TRUSTED_PROXY_HOPS, cip.CLIENT_IP_HEADER = _alt

print()
print(f"{'ALLE OK' if not FEHLER else f'{len(FEHLER)} FEHLER'}")
sys.exit(1 if FEHLER else 0)
