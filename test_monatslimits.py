"""
Test: Monatliche Kontingente fuer KI-Chat und AutoFinder (Consumer V1 FINAL).

  AD) Free 20 Chat-Nachrichten / Monat
  AE) die 21. wird blockiert
  AF) Plus 100
  AG) die 101. wird blockiert
  AH) neuer Monat -> Reset
  AI) KEIN Tagesreset mehr (Zaehler haengt am Monat, nicht am Tag)
  AJ) clientseitig nicht umgehbar
  AK) Free 5 AutoFinder-Suchen / Monat
  AL) die 6. wird blockiert
  AM) Plus 50
  AN) die 51. wird blockiert
  AO) neuer Monat -> Reset
  AP) Autokosten hat serverseitig ueberhaupt keinen Zaehler

Ausfuehren:  python test_monatslimits.py
"""
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone

_TMP = tempfile.mkdtemp(prefix="vira_ml_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "test.db")

import app.database as db          # noqa: E402
db.ensure_tables()

import app.usage_limit as ul       # noqa: E402
from app import plus               # noqa: E402
from app.config import (           # noqa: E402
    AUTOFINDER_FREE_LIMIT_MONATLICH, AUTOFINDER_PLUS_LIMIT_MONATLICH,
    CHAT_FREE_LIMIT_MONATLICH, CHAT_PLUS_LIMIT_MONATLICH,
)
from fastapi import HTTPException  # noqa: E402
import app.routers.user_auth as auth  # noqa: E402

FEHLER = []


def check(name, cond, info=""):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}{(' — ' + info) if info else ''}")
    if not cond:
        FEHLER.append(name)


class _Client:
    def __init__(self, host): self.host = host


class _Req:
    def __init__(self, cookies=None, host="203.0.113.5"):
        self.cookies = cookies or {}
        self.client = _Client(host)


def neuer_user(email, plus_aktiv=False, abo="none"):
    with db.get_conn() as conn:
        cur = conn.execute("INSERT INTO users (email, password_hash, abo_typ) VALUES (?,?,?)",
                           (email, "x", abo))
        conn.commit()
        uid = cur.lastrowid
    if plus_aktiv:
        ende = int((datetime.now(timezone.utc) + timedelta(days=25)).timestamp())
        plus.grant_periode(uid, f"sub_{uid}", None, ende)
    return uid


def req_fuer(uid, host="203.0.113.5"):
    return _Req(cookies={"auth_token": auth._make_token(uid, f"u{uid}@test.de")}, host=host)


check("Grenzen kommen zentral aus der Konfiguration",
      (CHAT_FREE_LIMIT_MONATLICH, CHAT_PLUS_LIMIT_MONATLICH,
       AUTOFINDER_FREE_LIMIT_MONATLICH, AUTOFINDER_PLUS_LIMIT_MONATLICH) == (20, 100, 5, 50),
      f"{CHAT_FREE_LIMIT_MONATLICH}/{CHAT_PLUS_LIMIT_MONATLICH}/"
      f"{AUTOFINDER_FREE_LIMIT_MONATLICH}/{AUTOFINDER_PLUS_LIMIT_MONATLICH}")

# ── AD/AE) Free-Chat ─────────────────────────────────────────────────────────
uid = neuer_user("chat-free@test.de")
r = req_fuer(uid)
for _ in range(CHAT_FREE_LIMIT_MONATLICH):
    ul.require_chat_kontingent(r)
check("AD: Free darf 20 Chat-Nachrichten im Monat",
      ul.stand(f"user:{uid}", ul.ART_CHAT) == 20)
try:
    ul.require_chat_kontingent(r)
    check("AE: die 21. wird blockiert", False)
except HTTPException as e:
    d = e.detail["fehler"]
    check("AE: die 21. wird blockiert (429)", e.status_code == 429)
    check("AE: strukturierter Fehler mit Monatstext",
          d["code"] == "monatslimit_erreicht" and "diesen Monat" in d["nachricht"], d["nachricht"])
    check("AE: Text nennt keine Technik/kein Fehler-Praefix",
          not any(w in d["nachricht"].lower() for w in ("429", "fehler:", "gemini", "api", "json")))
    check("AE: Plus-CTA ist sinnvoll und wird signalisiert", d["plus_hilft"] is True)

# ── AF/AG) Plus-Chat ─────────────────────────────────────────────────────────
uid_p = neuer_user("chat-plus@test.de", plus_aktiv=True)
rp = req_fuer(uid_p, host="198.51.100.1")
for _ in range(CHAT_PLUS_LIMIT_MONATLICH):
    ul.require_chat_kontingent(rp)
check("AF: Plus darf 100 Chat-Nachrichten im Monat",
      ul.stand(f"user:{uid_p}", ul.ART_CHAT) == 100)
try:
    ul.require_chat_kontingent(rp)
    check("AG: die 101. wird blockiert", False)
except HTTPException as e:
    d = e.detail["fehler"]
    check("AG: die 101. wird blockiert (429)", e.status_code == 429)
    check("AG: Plus-Text ohne sinnlose Plus-CTA",
          d["plus_hilft"] is False and "monatliches" in d["nachricht"].lower(), d["nachricht"])

# ── AH/AI) Reset-Semantik ────────────────────────────────────────────────────
naechster = (datetime.now(timezone.utc).replace(day=1) + timedelta(days=32)).strftime("%Y-%m")
check("AH: im naechsten Monat ist wieder alles frei",
      ul.verbrauche(f"user:{uid}", ul.ART_CHAT, CHAT_FREE_LIMIT_MONATLICH, monat=naechster) is True)
check("AH: der Zaehler des Vormonats bleibt unberuehrt",
      ul.stand(f"user:{uid}", ul.ART_CHAT) == 20)
check("AI: die Periode ist ein Kalendermonat (YYYY-MM), kein Tag",
      len(ul.monat_utc()) == 7 and ul.monat_utc().count("-") == 1, ul.monat_utc())
check("AI: es gibt keine Tagesfunktion mehr im Modul",
      not hasattr(ul, "heute_utc"))
with db.get_conn() as conn:
    spalten = {r2[1] for r2 in conn.execute("PRAGMA table_info(usage_monat)").fetchall()}
check("AI: der Zaehler haengt an monat_utc", "monat_utc" in spalten and "tag_utc" not in spalten)

# ── AJ) Nicht umgehbar ───────────────────────────────────────────────────────
uid_j = neuer_user("umgehung@test.de")
for _ in range(CHAT_FREE_LIMIT_MONATLICH):
    ul.require_chat_kontingent(req_fuer(uid_j, host="1.1.1.1"))
try:
    ul.require_chat_kontingent(req_fuer(uid_j, host="9.9.9.9"))
    check("AJ: IP-Wechsel umgeht die Kontogrenze nicht", False)
except HTTPException:
    check("AJ: IP-Wechsel umgeht die Kontogrenze nicht", True)
check("AJ: ein gefaelschtes Cookie faellt auf die IP zurueck",
      ul._schluessel(_Req(cookies={"auth_token": "gefaelscht"}, host="9.9.9.9"), None) == "ip:9.9.9.9")

treffer = []
barriere = threading.Barrier(8)


def parallel():
    barriere.wait()
    treffer.append(ul.verbrauche("user:parallel", ul.ART_CHAT, 3))


ts_ = [threading.Thread(target=parallel) for _ in range(8)]
for t in ts_:
    t.start()
for t in ts_:
    t.join()
check("AJ: 8 parallele Anfragen bei Grenze 3 -> genau 3 kommen durch",
      sum(1 for x in treffer if x) == 3)

# ── AK/AL) Free-AutoFinder ───────────────────────────────────────────────────
uid_a = neuer_user("af-free@test.de")
ra = req_fuer(uid_a, host="203.0.113.77")
for _ in range(AUTOFINDER_FREE_LIMIT_MONATLICH):
    ul.require_autofinder_kontingent(ra)
check("AK: Free darf 5 AutoFinder-Suchen im Monat",
      ul.stand(f"user:{uid_a}", ul.ART_AUTOFINDER) == 5)
try:
    ul.require_autofinder_kontingent(ra)
    check("AL: die 6. wird blockiert", False)
except HTTPException as e:
    d = e.detail["fehler"]
    check("AL: die 6. wird blockiert (429)", e.status_code == 429)
    check("AL: Text nennt 5 Suchen und den Monat",
          "5 kostenlosen AutoFinder-Suchen" in d["nachricht"] and "diesen Monat" in d["nachricht"],
          d["nachricht"])

# anonym: eigener IP-Anker, ebenfalls begrenzt
anon = _Req(host="203.0.113.200")
for _ in range(AUTOFINDER_FREE_LIMIT_MONATLICH):
    ul.require_autofinder_kontingent(anon)
try:
    ul.require_autofinder_kontingent(anon)
    check("AL: auch anonym greift das Limit", False)
except HTTPException as e:
    check("AL: auch anonym greift das Limit (429)", e.status_code == 429)
    check("AL: anonymer Text bietet Anmeldung ODER Plus an",
          "Melde dich an" in e.detail["fehler"]["nachricht"], e.detail["fehler"]["nachricht"])

# ── AM/AN) Plus-AutoFinder ───────────────────────────────────────────────────
uid_ap = neuer_user("af-plus@test.de", plus_aktiv=True)
rap = req_fuer(uid_ap, host="198.51.100.9")
for _ in range(AUTOFINDER_PLUS_LIMIT_MONATLICH):
    ul.require_autofinder_kontingent(rap)
check("AM: Plus darf 50 AutoFinder-Suchen im Monat",
      ul.stand(f"user:{uid_ap}", ul.ART_AUTOFINDER) == 50)
try:
    ul.require_autofinder_kontingent(rap)
    check("AN: die 51. wird blockiert", False)
except HTTPException as e:
    check("AN: die 51. wird blockiert (429)", e.status_code == 429)

# ── AO) Reset ────────────────────────────────────────────────────────────────
check("AO: im naechsten Monat wieder frei",
      ul.verbrauche(f"user:{uid_a}", ul.ART_AUTOFINDER,
                    AUTOFINDER_FREE_LIMIT_MONATLICH, monat=naechster) is True)

# ── Legacy-Abo bleibt ausgenommen ────────────────────────────────────────────
uid_l = neuer_user("legacy@test.de", abo="pro")
rl = req_fuer(uid_l, host="198.51.100.44")
for _ in range(CHAT_FREE_LIMIT_MONATLICH * 3):
    ul.require_chat_kontingent(rl)
check("Legacy-Abokunde wird nicht gedeckelt (keine Leistungskuerzung)",
      ul.stand(f"user:{uid_l}", ul.ART_CHAT) == 0)

# ── Uebernommen aus dem abgeloesten Tageslimit-Test ──────────────────────────
# Diese beiden Zusicherungen gab es nur dort; sie gelten unveraendert weiter und
# duerfen mit der Umstellung auf Monatszaehlung nicht verloren gehen.
uid_t = neuer_user("toepfe@test.de")
for _ in range(CHAT_FREE_LIMIT_MONATLICH):
    ul.verbrauche(f"user:{uid_t}", ul.ART_CHAT, CHAT_FREE_LIMIT_MONATLICH)
check("Chat ausgeschoepft, Analyse-Rueckfragen bleiben unberuehrt (eigener Topf)",
      ul.verbrauche(f"user:{uid_t}", ul.ART_ANALYSE_FRAGE, CHAT_PLUS_LIMIT_MONATLICH) is True
      and ul.stand(f"user:{uid_t}", ul.ART_CHAT) == CHAT_FREE_LIMIT_MONATLICH
      and ul.stand(f"user:{uid_t}", ul.ART_ANALYSE_FRAGE) == 1)

check("Limit 0 deaktiviert die Grenze vollstaendig",
      all(ul.verbrauche("user:aus", ul.ART_CHAT, 0) for _ in range(50)))
check("deaktiviertes Limit zaehlt auch nicht mit",
      ul.stand("user:aus", ul.ART_CHAT) == 0)

# ── AP) Autokosten hat keinen Zaehler ────────────────────────────────────────
import ast, inspect, pathlib  # noqa: E402
ul_quelle = inspect.getsource(ul)
check("AP: das Limit-Modul kennt ueberhaupt keine Autokosten-Art",
      "autokosten" not in ul_quelle.lower())
router_dir = pathlib.Path("app/routers")
treffer_ak = [p.name for p in router_dir.glob("*.py")
              if "autokosten" in p.read_text(encoding="utf-8").lower()]
check("AP: kein Backend-Router kennt Autokosten (rein im Frontend)",
      not treffer_ak, str(treffer_ak))

# AutoFinder-Router: Kontingent verdrahtet, Rate-Limit weiterhin da
af_quelle = (router_dir / "autofinder.py").read_text(encoding="utf-8")
af_baum = ast.parse(af_quelle)
aufrufe = {n.func.id for n in ast.walk(af_baum)
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
check("AutoFinder ruft das Monatskontingent auf",
      "require_autofinder_kontingent" in aufrufe)
check("AutoFinder behaelt zusaetzlich sein Rate-Limit",
      "@limiter.limit(_AUTOFINDER_RATE_LIMIT)" in af_quelle)

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER:")
    for f in FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("Alle Monatslimit-Tests bestanden.")
