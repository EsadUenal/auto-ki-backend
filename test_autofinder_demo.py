"""
Test: AutoFinder-Zugang ohne Login (anonyme Tages-Demo) und mit Konto.

Der IP-Anker kann kein Monatskontingent tragen: hinter Buero-NAT,
Schul-/Hotel-WLAN oder Mobilfunk-CGNAT teilen sich beliebig viele Menschen eine
Adresse. Fuenf Suchen pro MONAT waeren dort nach kurzer Zeit fuer alle
aufgebraucht. Anonym gilt deshalb EINE Demo-Suche pro UTC-Tag und IP; das
eigentliche Free-Kontingent (5/Monat) haengt ausschliesslich am Konto.

  A) anonym: die 1. Suche ist erlaubt
  B) anonym: die 2. im selben Zeitraum wird blockiert
  C) eine andere IP hat ihren eigenen Demo-Zugang
  D) Demo verbraucht -> Registrierung -> das Konto hat trotzdem volle 5
  E) ein eingeloggter Free-Nutzer ignoriert den anonymen IP-Zaehler
  F) Free: 5 erlaubt
  G) Free: die 6. blockiert
  H) Plus: 50 erlaubt
  I) Plus: die 51. blockiert
  J) neuer Monat -> Reset Free
  K) neuer Monat -> Reset Plus
  L) kein Fingerprinting
  M) keine clientseitige Autoritaet

Ausfuehren:  python test_autofinder_demo.py
"""
import ast
import os
import pathlib
import tempfile
from datetime import datetime, timedelta, timezone

_TMP = tempfile.mkdtemp(prefix="vira_afdemo_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "test.db")

import app.database as db             # noqa: E402
db.ensure_tables()

import app.usage_limit as ul          # noqa: E402
from app import plus                  # noqa: E402
from app.config import (              # noqa: E402
    AUTOFINDER_ANONYM_DEMO_PRO_TAG,
    AUTOFINDER_FREE_LIMIT_MONATLICH,
    AUTOFINDER_PLUS_LIMIT_MONATLICH,
)
from fastapi import HTTPException     # noqa: E402
import app.routers.user_auth as auth  # noqa: E402

FEHLER = []


def check(name, cond, info=""):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}{(' — ' + info) if info else ''}")
    if not cond:
        FEHLER.append(name)


class _Client:
    def __init__(self, host):
        self.host = host


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


def blockiert(request):
    """(wurde_blockiert, fehlerdetail) — fuehrt genau EINEN Versuch aus."""
    try:
        ul.require_autofinder_kontingent(request)
        return False, None
    except HTTPException as e:
        return True, e.detail["fehler"]


check("Demo-Grenze kommt zentral aus der Konfiguration",
      AUTOFINDER_ANONYM_DEMO_PRO_TAG == 1, str(AUTOFINDER_ANONYM_DEMO_PRO_TAG))

# ── A/B) Anonyme Demo ────────────────────────────────────────────────────────
IP_A = "203.0.113.10"
anon = _Req(host=IP_A)
gesperrt, _ = blockiert(anon)
check("A: die erste anonyme Suche ist erlaubt", not gesperrt)
check("A: sie wurde im Tages-Topf gezaehlt",
      ul.stand_tag(f"ip:{IP_A}", ul.ART_AUTOFINDER_DEMO) == 1)

gesperrt, d = blockiert(anon)
check("B: die zweite anonyme Suche am selben Tag wird blockiert", gesperrt)
check("B: eigener Fehlercode fuer die Demo",
      d and d["code"] == "demo_limit_erreicht", d and d["code"])
check("B: Text ist der spezifizierte Demo-Satz",
      d and d["nachricht"] == "Du hast deine kostenlose AutoFinder-Demo genutzt.",
      d and d["nachricht"])
check("B: Subtext nennt die 5 Suchen pro Monat nach Anmeldung",
      d and "kostenlos an" in d["hinweis"] and "5 AutoFinder-Suchen pro Monat" in d["hinweis"],
      d and d["hinweis"])
check("B: Anmeldung ist der Weg nach vorn, NICHT Plus",
      d and d["anmelden_hilft"] is True and d["plus_hilft"] is False)
check("B: kein Statuscode, kein Provider, kein 'Fehler:'-Praefix",
      d and not any(w in (d["nachricht"] + d["hinweis"]).lower()
                    for w in ("429", "fehler:", "gemini", "tavily", "api", "json")))
check("B: die Demo hat den Monatstopf nicht angefasst",
      ul.stand(f"ip:{IP_A}", ul.ART_AUTOFINDER) == 0)

# ── C) Andere IP, eigener Demo-Zugang ────────────────────────────────────────
IP_B = "198.51.100.77"
gesperrt, _ = blockiert(_Req(host=IP_B))
check("C: eine andere IP hat ihren eigenen Demo-Zugang", not gesperrt)
check("C: der Zaehler der ersten IP blieb davon unberuehrt",
      ul.stand_tag(f"ip:{IP_A}", ul.ART_AUTOFINDER_DEMO) == 1)

# ── D) Demo verbraucht, danach registriert ───────────────────────────────────
# Dieselbe IP wie die verbrauchte Demo (IP_A) — der Nutzer sitzt ja weiterhin
# im selben Netz. Sein Konto muss trotzdem mit vollen 5 Suchen starten.
uid_neu = neuer_user("nach-demo@test.de")
r_neu = req_fuer(uid_neu, host=IP_A)
erlaubt = 0
for _ in range(AUTOFINDER_FREE_LIMIT_MONATLICH):
    gesperrt, _ = blockiert(r_neu)
    if not gesperrt:
        erlaubt += 1
check("D: nach der Registrierung stehen volle 5 Suchen zur Verfuegung",
      erlaubt == AUTOFINDER_FREE_LIMIT_MONATLICH, f"{erlaubt}/5")
check("D: die verbrauchte Demo war kein Vorschuss auf das Kontingent",
      ul.stand(f"user:{uid_neu}", ul.ART_AUTOFINDER) == 5)

# ── E) Eingeloggt ignoriert den anonymen IP-Zaehler ──────────────────────────
# IP_A hat ihre Tages-Demo laengst verbraucht. Ein frisches Konto aus genau
# diesem Netz darf davon nichts merken.
uid_e = neuer_user("teilt-ip@test.de")
gesperrt, _ = blockiert(req_fuer(uid_e, host=IP_A))
check("E: eingeloggt wird der verbrauchte IP-Demo-Zaehler ignoriert", not gesperrt)
check("E: gezaehlt wurde am Konto, nicht an der IP",
      ul.stand(f"user:{uid_e}", ul.ART_AUTOFINDER) == 1)
check("E: der Demo-Zaehler der IP wurde dabei nicht erhoeht",
      ul.stand_tag(f"ip:{IP_A}", ul.ART_AUTOFINDER_DEMO) == 1)

# Umgekehrt: zwei Konten hinter DERSELBEN IP teilen sich nichts.
uid_e2 = neuer_user("teilt-ip-2@test.de")
for _ in range(AUTOFINDER_FREE_LIMIT_MONATLICH):
    blockiert(req_fuer(uid_e2, host=IP_A))
gesperrt, _ = blockiert(req_fuer(uid_e, host=IP_A))
check("E: ein zweites Konto hinter derselben IP verbraucht nichts vom ersten",
      not gesperrt, "erstes Konto weiterhin nutzbar")

# ── F/G) Free-Kontingent ─────────────────────────────────────────────────────
uid_f = neuer_user("af-free@test.de")
r_f = req_fuer(uid_f, host="192.0.2.31")
for _ in range(AUTOFINDER_FREE_LIMIT_MONATLICH):
    ul.require_autofinder_kontingent(r_f)
check("F: Free darf 5 AutoFinder-Suchen im Monat",
      ul.stand(f"user:{uid_f}", ul.ART_AUTOFINDER) == 5)

gesperrt, d = blockiert(r_f)
check("G: die 6. wird blockiert", gesperrt)
check("G: Monatslimit-Code, nicht Demo-Code",
      d and d["code"] == "monatslimit_erreicht", d and d["code"])
check("G: Text nennt 5 Suchen und den Monat",
      d and "5 kostenlosen AutoFinder-Suchen" in d["nachricht"] and "diesen Monat" in d["nachricht"],
      d and d["nachricht"])
check("G: eingeloggt wird auf Plus verwiesen, nicht auf Anmeldung",
      d and d["plus_hilft"] is True and "anmelden_hilft" not in d)

# ── H/I) Plus-Kontingent ─────────────────────────────────────────────────────
uid_p = neuer_user("af-plus@test.de", plus_aktiv=True)
r_p = req_fuer(uid_p, host="192.0.2.44")
for _ in range(AUTOFINDER_PLUS_LIMIT_MONATLICH):
    ul.require_autofinder_kontingent(r_p)
check("H: Plus darf 50 AutoFinder-Suchen im Monat",
      ul.stand(f"user:{uid_p}", ul.ART_AUTOFINDER) == 50)

gesperrt, d = blockiert(r_p)
check("I: die 51. wird blockiert", gesperrt)
check("I: Plus-Text ohne weitere Plus-Werbung",
      d and d["plus_hilft"] is False, d and d["nachricht"])

# ── J/K) Monatswechsel ───────────────────────────────────────────────────────
naechster = (datetime.now(timezone.utc).replace(day=1) + timedelta(days=32)).strftime("%Y-%m")
check("J: im neuen Monat ist der Free-Zaehler wieder bei 0",
      ul.stand(f"user:{uid_f}", ul.ART_AUTOFINDER, naechster) == 0)
check("J: und die erste Suche des neuen Monats geht durch",
      ul.verbrauche(f"user:{uid_f}", ul.ART_AUTOFINDER, AUTOFINDER_FREE_LIMIT_MONATLICH, naechster))
check("K: im neuen Monat ist auch der Plus-Zaehler wieder bei 0",
      ul.stand(f"user:{uid_p}", ul.ART_AUTOFINDER, naechster) == 0)
check("K: und die erste Plus-Suche des neuen Monats geht durch",
      ul.verbrauche(f"user:{uid_p}", ul.ART_AUTOFINDER, AUTOFINDER_PLUS_LIMIT_MONATLICH, naechster))

# Und die Demo regeneriert taeglich, nicht monatlich.
morgen = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
check("J/K: die anonyme Demo regeneriert am naechsten TAG",
      ul.verbrauche_tag(f"ip:{IP_A}", ul.ART_AUTOFINDER_DEMO,
                        AUTOFINDER_ANONYM_DEMO_PRO_TAG, morgen))

# ── L) Kein Fingerprinting ───────────────────────────────────────────────────
ul_quelle = pathlib.Path("app/usage_limit.py").read_text(encoding="utf-8")
baum = ast.parse(ul_quelle)


def _ohne_doku(quelle: str) -> str:
    """Der reine CODE des Moduls — ohne Kommentare und ohne Docstrings.

    Wichtig fuer die Wortsuche unten: das Modul ERKLAERT ausfuehrlich, warum es
    kein Fingerprinting betreibt. Eine Suche ueber den Rohtext wuerde genau
    diese Erklaerung finden und damit sich selbst widerlegen. Geprueft gehoert,
    was ausgefuehrt wird.
    """
    b = ast.parse(quelle)
    for knoten in ast.walk(b):
        if isinstance(knoten, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (knoten.body and isinstance(knoten.body[0], ast.Expr)
                    and isinstance(knoten.body[0].value, ast.Constant)
                    and isinstance(knoten.body[0].value.value, str)):
                knoten.body.pop(0)
    return ast.unparse(b)


nur_code = _ohne_doku(ul_quelle).lower()
verboten = ("user_agent", "user-agent", "fingerprint", "canvas", "accept-language",
            "x-forwarded", "screen", "webgl", "device_id", "referer")
treffer = [w for w in verboten if w in nur_code]
check("L: der ausgefuehrte Code liest kein einziges Fingerprinting-Merkmal",
      not treffer, str(treffer))

# Welche Request-Bestandteile das Modul ueberhaupt anfasst: Attributzugriffe
# UND die Namen aus getattr(...) — `client.host` wird bewusst defensiv per
# getattr gelesen und waere als reine Attributsuche unsichtbar.
attribute = {n.attr for n in ast.walk(baum) if isinstance(n, ast.Attribute)}
getattr_namen = {n.args[1].value for n in ast.walk(baum)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "getattr" and len(n.args) >= 2
                 and isinstance(n.args[1], ast.Constant) and isinstance(n.args[1].value, str)}
zugriffe = attribute | getattr_namen
check("L: der anonyme Anker ist die Client-Adresse (client.host)",
      "client" in zugriffe and "host" in getattr_namen, str(sorted(getattr_namen)))
check("L: das Modul fasst KEINE Request-Header an",
      "headers" not in zugriffe)

# ── M) Keine clientseitige Autoritaet ────────────────────────────────────────
# Ein selbst gesetztes Cookie ohne gueltige Signatur darf nicht zum Konto-Topf
# fuehren — sonst koennte sich jeder aus der Demo herausschreiben.
def _konto_zaehler() -> set:
    """Alle vorhandenen Konto-Zaehler als Menge (Schluessel, Art, Monat, Stand)."""
    with db.get_conn() as conn:
        return {tuple(r) for r in conn.execute(
            "SELECT schluessel, art, monat_utc, anzahl FROM usage_monat "
            "WHERE schluessel LIKE 'user:%'")}


vorher = _konto_zaehler()
gefaelscht = _Req(cookies={"auth_token": "ich.bin.user.1"}, host="192.0.2.99")
gesperrt, _ = blockiert(gefaelscht)
check("M: gefaelschtes Cookie -> erste Suche laeuft als anonyme Demo", not gesperrt)
gesperrt, d = blockiert(gefaelscht)
check("M: die zweite wird als Demo blockiert, nicht als Konto durchgewunken",
      gesperrt and d["code"] == "demo_limit_erreicht")
# Nicht auf eine feste user_id pruefen: die waere von der Reihenfolge der
# uebrigen Testfaelle abhaengig. Entscheidend ist, dass sich KEIN einziger
# Konto-Zaehler veraendert hat.
check("M: das gefaelschte Cookie hat keinen Konto-Zaehler angelegt oder erhoeht",
      _konto_zaehler() == vorher)

# Der Zaehlstand selbst liegt serverseitig: kein Request-Feld beeinflusst ihn.
check("M: verbrauche_tag nimmt nur serverseitige Werte entgegen",
      "request" not in [a.arg for a in ast.parse(ul_quelle).body
                        if isinstance(a, ast.FunctionDef) and a.name == "verbrauche_tag"
                        for a in a.args.args])

# Der Router prueft das Kontingent weiterhin selbst.
af_quelle = pathlib.Path("app/routers/autofinder.py").read_text(encoding="utf-8")
aufrufe = {n.func.id for n in ast.walk(ast.parse(af_quelle))
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
check("M: der AutoFinder-Router erzwingt das Kontingent serverseitig",
      "require_autofinder_kontingent" in aufrufe)

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER:")
    for f in FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("Alle AutoFinder-Demo-Tests bestanden.")
