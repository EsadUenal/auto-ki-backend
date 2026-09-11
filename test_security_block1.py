"""
Regressionstests Security Fix Block 1 (Audit-Funde P0-1, P1-1, P1-2, P1-3, P1-5).

  P0-1  Admin-Routen akzeptieren nur AUTO_KI_ADMIN_API_KEY — nie den oeffentlichen
        Consumer-Key aus dem Frontend-Bundle; ohne Admin-Key sind sie geschlossen.
  P1-1  Stripe-Webhook ist fail-closed: ohne Secret wird kein Event verarbeitet.
  P1-2  Produktion startet nicht mit fehlenden/Default-Secrets.
  P1-3  Auth-Cookie ist in Produktion Secure (lokal nicht, sonst kein HTTP-Login).
  P1-5  Legacy-Produkte (abo light/pro/max, einzelkauf) sind nicht mehr neu
        kaufbar; bestehende Legacy-Rechte bleiben.

Kein Netzwerk: temporaere DB, Stripe gemockt, keine Gemini-/Tavily-Aufrufe.
Umgebungsabhaengige Faelle laufen in Subprozessen (config wird beim Import gelesen).

Ausfuehren: python test_security_block1.py
"""
import ast
import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import time

_HIER = os.path.dirname(os.path.abspath(__file__))
_TMP = tempfile.mkdtemp(prefix="vira_block1_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_TMP, "chroma")
os.environ["AUTO_KI_DB_BACKUP_DIR"] = os.path.join(_TMP, "bk")
os.environ["AUTO_KI_ENV"] = "development"
os.environ["AUTO_KI_RATE_LIMIT"] = "1000/minute"

import app.database as db  # noqa: E402
db.ensure_tables()

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.auth as auth  # noqa: E402
import app.config as config  # noqa: E402
import app.main as main  # noqa: E402
import app.routers.payments as pay  # noqa: E402
from app import check_gate  # noqa: E402

FEHLER = []


def check(name, cond, detail=""):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FEHLER.append(name)


def client():
    return TestClient(main.app, raise_server_exceptions=False)


def code(resp):
    try:
        return resp.json()["fehler"]["code"]
    except Exception:
        return None


def neuer_user(email, **felder):
    spalten = {"abo_typ": "none", "checks_verbleibend": 0, **felder}
    namen = ", ".join(spalten)
    with db.get_conn() as conn:
        cur = conn.execute(
            f"INSERT INTO users (email, password_hash, {namen}) VALUES (?, ?, {', '.join('?' * len(spalten))})",
            (email, "x", *spalten.values()),
        )
        conn.commit()
        return cur.lastrowid


def spalte(uid, name):
    with db.get_conn() as conn:
        return conn.execute(f"SELECT {name} FROM users WHERE id=?", (uid,)).fetchone()[0]


def subprozess(py_code, env_extra, timeout=120):
    """Fuehrt py_code in einem frischen Interpreter mit eigener Umgebung aus."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AUTO_KI_", "STRIPE_"))}
    tmp = tempfile.mkdtemp(prefix="vira_block1_sub_")
    env.update({
        "AUTO_KI_DB_PATH": os.path.join(tmp, "t.db"),
        "AUTO_KI_CHROMA_PATH": os.path.join(tmp, "chroma"),
        "AUTO_KI_DB_BACKUP_DIR": os.path.join(tmp, "bk"),
        "PYTHONIOENCODING": "utf-8",
    })
    env.update(env_extra)
    # Hermetisch: app.config ruft load_dotenv() auf und wuerde sonst die lokale
    # .env des Entwicklers nachladen (z.B. ein echtes JWT-Secret) — "fehlt" waere
    # dann nicht mehr fehlend. Im Subprozess zaehlt nur die hier gesetzte Umgebung.
    py_code = "import dotenv\ndotenv.load_dotenv = lambda *a, **k: False\n" + py_code
    return subprocess.run([sys.executable, "-c", py_code], cwd=_HIER, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=timeout)


# Starke Test-Secrets (nur fuer diesen Test, keine echten Werte).
S_JWT = "t" * 64
S_API = "c" * 48
S_ADMIN = "a" * 48
S_WH = "whsec_" + "w" * 32
PROD_OK = {"AUTO_KI_ENV": "production", "AUTO_KI_JWT_SECRET": S_JWT, "AUTO_KI_API_KEY": S_API,
           "AUTO_KI_ADMIN_API_KEY": S_ADMIN, "STRIPE_WEBHOOK_SECRET": S_WH}


# ════════════════════════════════════════════════════════════════════════════
# P0-1 — Admin vom Consumer-Key getrennt
# ════════════════════════════════════════════════════════════════════════════
CONSUMER = {"Authorization": f"Bearer {auth.API_KEY}"}
ADMIN_ROUTEN = {
    "/api/v1/admin/entwurf":           {"marke": "BMW", "modell": "3er", "generation": "G20"},
    "/api/v1/admin/entwurf-stream":    {"marke": "BMW", "modell": "3er", "generation": "G20"},
    "/api/v1/admin/batch":             {"anfrage": "alle BMW 3er"},
    "/api/v1/admin/speichern":         {"daten": {}},
    "/api/v1/admin/luecken-entwurf":   {"marke": "BMW", "modell": "3er", "generation": "G20"},
    "/api/v1/admin/luecken-speichern": {"baureihe_id": "x", "daten": {}},
}

# Struktur: jede Admin-Route prueft den Admin-Key, keine den Consumer-Key.
# AST statt Textsuche — Kommentare/Docstrings koennen das Ergebnis nicht verfaelschen.
_baum = ast.parse(open(os.path.join(_HIER, "app", "routers", "admin.py"), encoding="utf-8").read())
_aufrufe = [n.func.id for n in ast.walk(_baum)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
_routen = [f for f in ast.walk(_baum) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
           and any(isinstance(d, ast.Call) and getattr(d.func, "attr", "") in ("get", "post", "put", "patch", "delete")
                   for d in f.decorator_list)]
_ohne_admin = [f.name for f in _routen
               if not any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "verify_admin_key"
                          for n in ast.walk(f))]
check("P0-1 Struktur: admin.py ruft verify_api_key nirgends auf", "verify_api_key" not in _aufrufe)
check("P0-1 Struktur: alle Admin-Routen rufen verify_admin_key auf",
      len(_routen) == len(ADMIN_ROUTEN) and not _ohne_admin, f"Routen={len(_routen)}, ohne={_ohne_admin}")

auth.ADMIN_API_KEY = S_ADMIN   # Admin in diesem Prozess "konfiguriert"
c = client()

# A) Consumer-Key auf Consumer-Route: bisheriges Verhalten (kein 401/403)
r = c.post("/api/v1/fahrzeug", json={"marke": "Gibtsnicht", "modell": "X", "generation": "Y"}, headers=CONSUMER)
check("P0-1 A: Consumer-Key auf /fahrzeug weiterhin akzeptiert", r.status_code == 404 and code(r) == "nicht_gefunden",
      f"HTTP {r.status_code} {code(r)}")
r = c.post("/api/v1/fahrzeug", json={"marke": "x", "modell": "x", "generation": "x"},
           headers={"Authorization": "Bearer falsch"})
check("P0-1 A: falscher Consumer-Key auf /fahrzeug weiterhin 403", r.status_code == 403)

# B) Consumer-Key auf JEDER Admin-Route -> 403
for pfad, body in ADMIN_ROUTEN.items():
    r = c.post(pfad, json=body, headers=CONSUMER)
    check(f"P0-1 B: Consumer-Key auf {pfad} -> 403", r.status_code == 403, f"HTTP {r.status_code}")

# C) falscher Admin-Key -> 403; kein Header -> 401
for pfad, body in ADMIN_ROUTEN.items():
    r = c.post(pfad, json=body, headers={"Authorization": "Bearer " + "a" * 47 + "b"})
    check(f"P0-1 C: falscher Admin-Key auf {pfad} -> 403", r.status_code == 403, f"HTTP {r.status_code}")
r = c.post("/api/v1/admin/speichern", json={"daten": {}})
check("P0-1 C: Admin ohne Authorization-Header -> 401", r.status_code == 401)

# D) richtiger Admin-Key -> Auth bestanden (scheitert erst an der Fachvalidierung)
r = c.post("/api/v1/admin/speichern", json={"daten": {}}, headers={"Authorization": f"Bearer {S_ADMIN}"})
check("P0-1 D: richtiger Admin-Key besteht die Auth (danach 422 Fachvalidierung)",
      r.status_code == 422 and code(r) == "validierung", f"HTTP {r.status_code} {code(r)}")

# E) Admin-Key fehlt -> geschlossen, auch fuer den Consumer-Key und leere Tokens
auth.ADMIN_API_KEY = ""
for pfad, body in ADMIN_ROUTEN.items():
    r = c.post(pfad, json=body, headers=CONSUMER)
    check(f"P0-1 E: ohne Admin-Key ist {pfad} geschlossen", r.status_code == 403, f"HTTP {r.status_code}")
r = c.post("/api/v1/admin/speichern", json={"daten": {}}, headers={"Authorization": "Bearer "})
check("P0-1 E: leerer Bearer-Token oeffnet geschlossenes Admin nicht", r.status_code in (401, 403))

# E2) Admin-Key versehentlich = Consumer-Key -> trotzdem geschlossen
auth.ADMIN_API_KEY = auth.API_KEY
r = c.post("/api/v1/admin/speichern", json={"daten": {}}, headers=CONSUMER)
check("P0-1 E: Admin-Key gleich Consumer-Key -> Admin bleibt geschlossen", r.status_code == 403)
auth.ADMIN_API_KEY = S_ADMIN

# E3) Config: kein Default, kein Fallback
r = subprozess("import app.config as c; print(repr(c.ADMIN_API_KEY))", {"AUTO_KI_ENV": "development"})
check("P0-1 E: ADMIN_API_KEY ohne Env ist leer (kein Fallback auf API_KEY)", r.stdout.strip() == "''",
      r.stdout + r.stderr[-300:])


# ════════════════════════════════════════════════════════════════════════════
# P1-1 — Stripe-Webhook fail-closed
# ════════════════════════════════════════════════════════════════════════════
def signiert(payload: str, secret: str) -> dict:
    t = int(time.time())
    sig = hmac.new(secret.encode(), f"{t}.{payload}".encode(), hashlib.sha256).hexdigest()
    return {"stripe-signature": f"t={t},v1={sig}", "content-type": "application/json"}


def fake_checkout(uid, eid, produkt="kaufcheck", typ="check"):
    return json.dumps({"id": eid, "object": "event", "type": "checkout.session.completed",
                       "data": {"object": {"id": "cs_" + eid, "object": "checkout.session",
                                           "metadata": {"user_id": str(uid), "typ": typ, "produkt": produkt}}}})


def events_anzahl():
    with db.get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM stripe_events").fetchone()[0]


uid_wh = neuer_user("webhook@test.de")

# Secret korrekt + gueltige Signatur -> akzeptiert (1 Credit)
pay.STRIPE_WEBHOOK_SECRET = S_WH
p = fake_checkout(uid_wh, "evt_ok_1")
r = c.post("/api/v1/payments/webhook", content=p, headers=signiert(p, S_WH))
check("P1-1: korrektes Secret + gueltige Signatur -> akzeptiert",
      r.status_code == 200 and spalte(uid_wh, "kaufchecks_verbleibend") == 1,
      f"HTTP {r.status_code}, kaufchecks={spalte(uid_wh, 'kaufchecks_verbleibend')}")

# falsche Signatur -> abgelehnt
p = fake_checkout(uid_wh, "evt_bad_1")
r = c.post("/api/v1/payments/webhook", content=p, headers=signiert(p, "whsec_falsch"))
check("P1-1: falsche Signatur -> 400, kein Credit",
      r.status_code == 400 and spalte(uid_wh, "kaufchecks_verbleibend") == 1)

# leeres / whitespace Secret -> abgelehnt, exakt 0 Aenderung
for name, secret in (("leer", ""), ("whitespace", "   \t "), ("None", None)):
    pay.STRIPE_WEBHOOK_SECRET = secret
    vor_events = events_anzahl()
    for produkt, typ in (("verkaufscheck", "check"), (None, "plus")):
        p = fake_checkout(uid_wh, f"evt_forged_{name}_{typ}", produkt=produkt or "", typ=typ)
        # Faelschung: "Signatur" mit dem (leeren) Secret selbst berechnet
        r = c.post("/api/v1/payments/webhook", content=p, headers=signiert(p, (secret or "").strip()))
        check(f"P1-1: Secret {name}: gefaelschtes {typ}-Event -> 503", r.status_code == 503 and
              code(r) == "webhook_nicht_konfiguriert", f"HTTP {r.status_code}")
    check(f"P1-1: Secret {name}: 0 neue Credits",
          spalte(uid_wh, "kaufchecks_verbleibend") == 1 and spalte(uid_wh, "verkaufschecks_verbleibend") == 0)
    check(f"P1-1: Secret {name}: kein Plus",
          not spalte(uid_wh, "plus_subscription_id") and not spalte(uid_wh, "plus_period_end"))
    check(f"P1-1: Secret {name}: keine DB-Veraenderung (stripe_events)", events_anzahl() == vor_events)
pay.STRIPE_WEBHOOK_SECRET = S_WH

# Config: whitespace in der Env wird zu leer
r = subprozess("import app.config as c; print(repr(c.STRIPE_WEBHOOK_SECRET))",
               {"AUTO_KI_ENV": "development", "STRIPE_WEBHOOK_SECRET": "   "})
check("P1-1: STRIPE_WEBHOOK_SECRET aus Leerzeichen gilt als leer", r.stdout.strip() == "''", r.stderr[-300:])


# ════════════════════════════════════════════════════════════════════════════
# P1-2 — Produktion startet nicht mit unsicheren Secrets
# ════════════════════════════════════════════════════════════════════════════
pf = config.produktions_fehler
gut = dict(is_production=True, jwt_secret=S_JWT, api_key=S_API, admin_api_key=S_ADMIN, webhook_secret=S_WH)
check("P1-2: vollstaendige Produktionskonfiguration ist startbereit", pf(**gut) == [], str(pf(**gut)))
check("P1-2: ohne Admin-Key startbereit (Admin dann geschlossen)", pf(**{**gut, "admin_api_key": ""}) == [])
check("P1-2: Entwicklung erlaubt Dev-Defaults",
      pf(is_production=False, jwt_secret=config.DEV_JWT_SECRET, api_key=config.DEV_API_KEY,
         admin_api_key="", webhook_secret="") == [])

FAELLE = {
    "JWT fehlt":                {"jwt_secret": ""},
    "JWT Dev-Default":          {"jwt_secret": config.DEV_JWT_SECRET},
    "JWT zu kurz":              {"jwt_secret": "kurz-aber-nicht-leer"},
    "JWT nur Leerzeichen":      {"jwt_secret": "     "},
    "API-Key fehlt":            {"api_key": ""},
    "API-Key Dev-Default":      {"api_key": config.DEV_API_KEY},
    "JWT gleich API-Key":       {"jwt_secret": S_API, "api_key": S_API},
    "Webhook-Secret fehlt":     {"webhook_secret": ""},
    "Webhook nur Leerzeichen":  {"webhook_secret": "  "},
    "Admin gleich API-Key":     {"admin_api_key": S_API},
    "Admin zu kurz":            {"admin_api_key": "kurz"},
    "Admin Dev-Default":        {"admin_api_key": config.DEV_API_KEY},
}
for name, aenderung in FAELLE.items():
    fehler = pf(**{**gut, **aenderung})
    check(f"P1-2: Produktion verweigert: {name}", len(fehler) >= 1)
    # Meldungen nennen nie den Wert selbst
    werte = [v for v in aenderung.values() if len(v.strip()) >= 8]
    check(f"P1-2: Meldung ohne Secret-Wert: {name}", not any(w in " ".join(fehler) for w in werte))

# Echter App-Start (Startup-Event) in Produktion mit bekanntem JWT-Default
START = (
    "from fastapi.testclient import TestClient\n"
    "import app.main as m\n"
    "try:\n"
    "    with TestClient(m.app):\n"
    "        print('GESTARTET')\n"
    "except RuntimeError as e:\n"
    "    print('VERWEIGERT', str(e).splitlines()[0])\n"
)
r = subprozess(START, {**PROD_OK, "AUTO_KI_JWT_SECRET": config.DEV_JWT_SECRET})
check("P1-2: Produktion mit JWT-Dev-Default -> Start verweigert", "VERWEIGERT" in r.stdout and "GESTARTET" not in r.stdout,
      r.stdout[-300:] + r.stderr[-300:])
env_ohne = {k: v for k, v in PROD_OK.items() if k != "AUTO_KI_JWT_SECRET"}
r = subprozess(START, env_ohne)
check("P1-2: Produktion ohne JWT-Secret -> Start verweigert", "VERWEIGERT" in r.stdout, r.stdout[-300:] + r.stderr[-300:])
r = subprozess(START, {**PROD_OK, "AUTO_KI_API_KEY": ""})
check("P1-2: Produktion ohne API-Key -> Start verweigert", "VERWEIGERT" in r.stdout, r.stdout[-300:] + r.stderr[-300:])
r = subprozess(START, {**PROD_OK, "STRIPE_WEBHOOK_SECRET": ""})
check("P1-2: Produktion ohne Webhook-Secret -> Start verweigert", "VERWEIGERT" in r.stdout, r.stdout[-300:] + r.stderr[-300:])
r = subprozess("import app.config as c; print(c.IS_PRODUCTION); c.validiere_produktion(); print('OK')", PROD_OK)
check("P1-2: vollstaendige Produktionskonfiguration besteht validiere_produktion()",
      r.stdout.split() == ["True", "OK"], r.stdout + r.stderr[-300:])
r = subprozess("import app.config as c; print(c.IS_PRODUCTION)", {"AUTO_KI_ENV": "prod"})
check("P1-2: Tippfehler in AUTO_KI_ENV gilt als Produktion (fail-closed)", r.stdout.strip() == "True")
r = subprozess("import app.config as c; print(c.IS_PRODUCTION)", {})
check("P1-2: ohne AUTO_KI_ENV = Entwicklung (lokal bequem)", r.stdout.strip() == "False")
_docker = open(os.path.join(_HIER, "Dockerfile"), encoding="utf-8").read().split()
check("P1-2: Produktions-Image setzt AUTO_KI_ENV=production", "AUTO_KI_ENV=production" in _docker)


# ════════════════════════════════════════════════════════════════════════════
# P1-3 — Secure-Cookie
# ════════════════════════════════════════════════════════════════════════════
def cookie_attribute(set_cookie: str) -> set[str]:
    return {teil.strip().split("=")[0].lower() for teil in set_cookie.split(";")[1:]}


r = client().post("/api/v1/auth/register", json={"email": "dev@test.de", "password": "passwort123", "agb_akzeptiert": True})
attr = cookie_attribute(r.headers.get("set-cookie", ""))
check("P1-3 DEV: Registrierung setzt Cookie ohne Secure (HTTP-Login lokal)", r.status_code == 201 and "secure" not in attr, str(attr))
check("P1-3 DEV: HttpOnly + SameSite=lax bleiben", "httponly" in attr and "samesite=lax" in r.headers["set-cookie"].lower())

COOKIE_PROD = (
    "import json\n"
    "import app.database as db; db.ensure_tables()\n"
    "from fastapi.testclient import TestClient\n"
    "import app.main as m\n"
    "c = TestClient(m.app)\n"
    "r1 = c.post('/api/v1/auth/register', json={'email':'p@test.de','password':'passwort123','agb_akzeptiert':True})\n"
    "c2 = TestClient(m.app, base_url='https://testserver')\n"
    "r2 = c2.post('/api/v1/auth/login', json={'email':'p@test.de','password':'passwort123'})\n"
    "me = c2.get('/api/v1/auth/me')\n"
    "r3 = c2.post('/api/v1/auth/logout')\n"
    "print(json.dumps({'reg':[r1.status_code, r1.headers.get('set-cookie','')],\n"
    "  'login':[r2.status_code, r2.headers.get('set-cookie','')], 'me': me.status_code,\n"
    "  'logout':[r3.status_code, r3.headers.get('set-cookie','')]}))\n"
)
r = subprozess(COOKIE_PROD, PROD_OK)
try:
    ergebnis = json.loads(r.stdout.strip().splitlines()[-1])
except Exception:
    ergebnis = None
check("P1-3 PROD: Subprozess lieferte Ergebnis", ergebnis is not None, r.stdout[-300:] + r.stderr[-500:])
if ergebnis:
    for schritt in ("reg", "login", "logout"):
        status, sc = ergebnis[schritt]
        a = cookie_attribute(sc)
        check(f"P1-3 PROD: {schritt} -> Set-Cookie mit Secure, HttpOnly, SameSite=lax",
              status in (200, 201) and "secure" in a and "httponly" in a and "samesite=lax" in sc.lower(), f"{status} {sc}")
    check("P1-3 PROD: Login funktioniert (Cookie wird ueber HTTPS zurueckgeschickt, /me 200)", ergebnis["me"] == 200)


# ════════════════════════════════════════════════════════════════════════════
# P1-5 — Legacy-Produkte nicht mehr kaufbar, Bestandsrechte bleiben
# ════════════════════════════════════════════════════════════════════════════
class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


ERZEUGT, KUNDEN = [], []
pay.stripe.checkout.Session.create = staticmethod(lambda **kw: ERZEUGT.append(kw) or _Obj(url="https://checkout.test/x", id="cs_x"))
pay.stripe.Customer.create = staticmethod(lambda **kw: KUNDEN.append(kw) or _Obj(id="cus_block1"))
pay.stripe.Subscription.list = staticmethod(lambda **kw: _Obj(data=[]))
pay._CHECK_PRICE = {"kaufcheck": lambda: "price_T_kauf", "verkaufscheck": lambda: "price_T_verkauf"}
pay.STRIPE_PRICE_PLUS = "price_T_plus"


def einwilligungen(uid):
    with db.get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM einwilligung WHERE user_id=?", (uid,)).fetchone()[0]


def kaufe(uid, **felder):
    ERZEUGT.clear()
    body = pay.CheckoutBody(agb_akzeptiert=True, widerruf_verzicht=True, **felder)
    return pay.create_checkout_session(body, user_id=uid)


uid_k = neuer_user("kauf@test.de")
for felder, preis, modus in (({"typ": "check", "produkt": "kaufcheck"}, "price_T_kauf", "payment"),
                             ({"typ": "check", "produkt": "verkaufscheck"}, "price_T_verkauf", "payment"),
                             ({"typ": "plus"}, "price_T_plus", "subscription")):
    antwort = kaufe(uid_k, **felder)
    check(f"P1-5: Checkout {felder} erlaubt",
          antwort.get("url") and ERZEUGT and ERZEUGT[0]["line_items"][0]["price"] == preis and ERZEUGT[0]["mode"] == modus)

uid_l = neuer_user("legacykauf@test.de")
for felder in ({"typ": "abo", "abo_typ": "light"}, {"typ": "abo", "abo_typ": "pro"},
               {"typ": "abo", "abo_typ": "max"}, {"typ": "einzelkauf"}, {"typ": "gratis"},
               {"typ": ""}, {"typ": "PLUS"}):
    ERZEUGT.clear(); KUNDEN.clear()
    try:
        kaufe(uid_l, **felder)
        check(f"P1-5: {felder} abgelehnt", False, "kein Fehler")
    except HTTPException as e:
        check(f"P1-5: {felder} -> 400 produkt_nicht_verfuegbar",
              e.status_code == 400 and e.detail["fehler"]["code"] == "produkt_nicht_verfuegbar")
    check(f"P1-5: {felder}: keine Stripe-Session, kein Stripe-Kunde", not ERZEUGT and not KUNDEN)
check("P1-5: abgelehnte Kaeufe protokollieren keine Einwilligung", einwilligungen(uid_l) == 0)

# Ueber HTTP: direkter API-Aufruf "MAX kaufen" mit gueltigem Login
cl = client()
cl.post("/api/v1/auth/register", json={"email": "angreifer@test.de", "password": "passwort123", "agb_akzeptiert": True})
r = cl.post("/api/v1/payments/checkout-session",
            json={"typ": "abo", "abo_typ": "max", "agb_akzeptiert": True, "widerruf_verzicht": True})
check("P1-5: HTTP-Direktkauf MAX -> 400", r.status_code == 400 and code(r) == "produkt_nicht_verfuegbar", f"HTTP {r.status_code}")

# Bestandsrechte: Legacy-Nutzer behalten, was das System ihnen heute gibt
uid_pro = neuer_user("pro@test.de", abo_typ="pro", checks_verbleibend=4, stripe_subscription_id="sub_pro_1")
z = check_gate._entnehme(uid_pro, "verkauf")
check("P1-5 Bestand: pro-Nutzer nutzt generisches Guthaben weiter",
      z.quelle == "checks_verbleibend" and spalte(uid_pro, "checks_verbleibend") == 3)
uid_max = neuer_user("max@test.de", abo_typ="max", stripe_subscription_id="sub_max_1")
check("P1-5 Bestand: MAX-Nutzer bleibt unbegrenzt", check_gate._entnehme(uid_max, "kauf").quelle == check_gate.QUELLE_UNBEGRENZT)
pay._verarbeite_event("invoice.paid", _Obj(billing_reason="subscription_cycle", subscription="sub_pro_1"))
check("P1-5 Bestand: Renewal eines laufenden pro-Abos setzt Kontingent weiter zurueck",
      spalte(uid_pro, "checks_verbleibend") == pay._ABO_CHECKS["pro"])
pay.stripe.Subscription.modify = staticmethod(lambda sid, **kw: _Obj(id=sid, cancel_at=int(time.time()) + 86400))
antwort = pay.cancel_subscription(user_id=uid_pro)
check("P1-5 Bestand: laufendes Legacy-Abo bleibt kuendbar", antwort["ok"] and antwort["plus"] is False)
uid_ek = neuer_user("einzel@test.de")
pay._verarbeite_checkout_session(_Obj(id="cs_alt_einzel", metadata={"user_id": str(uid_ek), "typ": "einzelkauf"}))
check("P1-5 Bestand: bereits bezahlte Alt-Einzelkauf-Session wird weiter gutgeschrieben",
      spalte(uid_ek, "checks_verbleibend") == 1)


# ── Ergebnis ────────────────────────────────────────────────────────────────
print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER:")
    for f in FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("Alle Security-Block-1-Tests bestanden.")
