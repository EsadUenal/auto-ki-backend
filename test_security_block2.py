"""
Regressionstests Security Fix Block 2 (Audit-Funde P1-4, P1-6, P1-7).

  P1-4  Inserats-Optimierung nur fuer einen Check aus einem echten,
        serverseitig gelaufenen VerkaufsCheck (Nachweis in `check_lauf`).
  P1-6  Ein Kontingent wird erst nach Validierung/Auth/API-Key entnommen und bei
        JEDEM Fehler genau einmal erstattet — ohne Double-Spend zu schwaechen.
  P1-7  Rate-Limit-Schluessel ist die echte Client-IP hinter einem
        vertrauenswuerdigen Proxy, aber niemals ein frei gesetzter Header.

Kein Netzwerk: temporaere DB, Gemini/Recherche gemockt.
Ausfuehren: python test_security_block2.py
"""
import ast
import asyncio
import ipaddress
import os
import sys
import tempfile
import threading

_HIER = os.path.dirname(os.path.abspath(__file__))
_TMP = tempfile.mkdtemp(prefix="vira_block2_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_TMP, "chroma")
os.environ["AUTO_KI_DB_BACKUP_DIR"] = os.path.join(_TMP, "bk")
os.environ["AUTO_KI_ENV"] = "development"
os.environ["AUTO_KI_RATE_LIMIT"] = "1000/minute"
sys.path.insert(0, _HIER)

import app.database as db  # noqa: E402
db.ensure_tables()

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as main  # noqa: E402
import app.routers.checks as r_checks  # noqa: E402
import app.routers.kaufcheck as r_kauf  # noqa: E402
import app.routers.verkaufscheck as r_verk  # noqa: E402
from app import check_gate  # noqa: E402
from app.config import API_KEY  # noqa: E402
from app.gemini_retry import GeminiFehlgeschlagen  # noqa: E402
from app.marktrecherche import RechercheUnzureichend  # noqa: E402
from app.models import InseratOptimierung  # noqa: E402

FEHLER = []


def check(name, cond, detail=""):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        FEHLER.append(name)


H = {"Authorization": f"Bearer {API_KEY}"}


def client():
    return TestClient(main.app, raise_server_exceptions=False)


def code(resp):
    try:
        return resp.json()["fehler"]["code"]
    except Exception:
        return None


def registriere(c, email):
    r = c.post("/api/v1/auth/register",
               json={"email": email, "password": "passwort123", "agb_akzeptiert": True})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def setze(uid, spalte, wert):
    with db.get_conn() as conn:
        conn.execute(f"UPDATE users SET {spalte}=? WHERE id=?", (wert, uid))
        conn.commit()


def stand(uid, spalte="verkaufschecks_verbleibend"):
    with db.get_conn() as conn:
        return conn.execute(f"SELECT {spalte} FROM users WHERE id=?", (uid,)).fetchone()[0]


# Gemini/Recherche vollstaendig ersetzen — kein Netzwerk, deterministisch.
ERGEBNIS_V = {"bericht": "## Bericht", "quelle": "web", "vertrauen": "hoch"}
ERGEBNIS_K = {"bericht": "## Bericht", "empfehlung": "kaufen",
              "preis_bewertung": "marktgerecht", "quelle": "web", "vertrauen": "hoch"}


async def lauf_verkauf(body, retry=False):
    return dict(ERGEBNIS_V)


async def lauf_kauf(body, retry=False):
    return dict(ERGEBNIS_K)


r_verk.run_verkaufscheck = lauf_verkauf
r_kauf.run_kaufcheck = lauf_kauf

INSERAT_AUFRUFE = []


async def falsches_inserat(body):
    INSERAT_AUFRUFE.append(body)
    return InseratOptimierung.model_construct(titel="t", beschreibung="b")


r_checks.run_inserat_optimierung = falsches_inserat

BODY_V = {"marke": "BMW", "modell": "3er", "baujahr": 2018, "kilometerstand": 90000,
          "motor": "320d", "preis_vorstellung": 15000}
BODY_K = {"marke": "BMW", "modell": "3er", "baujahr": 2018, "kilometerstand": 90000,
          "motor": "320d", "preis_eur": 15000}


# ============================================================================
# P1-4 — Inserats-Optimierung nur nach echtem VerkaufsCheck
# ============================================================================
A = client()
uid_a = registriere(A, "a@block2.test")

# A) Fake-Check ohne jedes Guthaben
r = A.post("/api/v1/checks", json={"typ": "verkauf", "titel": "fake", "eingabe": {}, "ergebnis": {}})
fake_id = r.json()["id"]
INSERAT_AUFRUFE.clear()
o = A.post(f"/api/v1/checks/{fake_id}/inserat-optimierung", json=BODY_V)
check("P1-4 A: Fake-Check -> Inserats-Optimierung blockiert",
      o.status_code == 403 and code(o) == "kein_echter_verkaufscheck",
      f"HTTP {o.status_code} {code(o)}")
check("P1-4 A: kein LLM-Aufruf", not INSERAT_AUFRUFE)

# C) Echter Lauf: Credit -> VerkaufsCheck -> speichern -> Optimierung
setze(uid_a, "verkaufschecks_verbleibend", 1)
r = A.post("/api/v1/verkaufscheck", json=BODY_V, headers=H)
check("P1-4 C: echter VerkaufsCheck laeuft", r.status_code == 200,
      f"HTTP {r.status_code} {r.text[:120]}")
lauf_id = r.json().get("lauf_id")
check("P1-4 C: Antwort enthaelt einen Lauf-Nachweis", bool(lauf_id))
check("P1-4 C: Credit wurde verbraucht", stand(uid_a) == 0)
s = A.post("/api/v1/checks", json={"typ": "verkauf", "titel": "echt", "eingabe": BODY_V,
                                   "ergebnis": r.json(), "lauf_id": lauf_id})
echt_id = s.json()["id"]
INSERAT_AUFRUFE.clear()
o = A.post(f"/api/v1/checks/{echt_id}/inserat-optimierung", json=BODY_V)
check("P1-4 C: echter Check -> Optimierung funktioniert weiterhin", o.status_code == 200,
      f"HTTP {o.status_code} {o.text[:120]}")
check("P1-4 C: genau ein LLM-Aufruf", len(INSERAT_AUFRUFE) == 1)

# D) Fake-Check mit KOPIERTEN Ergebnisdaten eines echten Laufs
r2 = A.post("/api/v1/checks", json={"typ": "verkauf", "titel": "kopie", "eingabe": BODY_V,
                                    "ergebnis": r.json()})
kopie_id = r2.json()["id"]
INSERAT_AUFRUFE.clear()
o = A.post(f"/api/v1/checks/{kopie_id}/inserat-optimierung", json=BODY_V)
check("P1-4 D: kopierte Ergebnisdaten reichen nicht", o.status_code == 403 and not INSERAT_AUFRUFE)

# D2) derselbe Nachweis ein zweites Mal -> nicht erneut einloesbar
r3 = A.post("/api/v1/checks", json={"typ": "verkauf", "titel": "zweitnutzung", "eingabe": BODY_V,
                                    "ergebnis": r.json(), "lauf_id": lauf_id})
o = A.post(f"/api/v1/checks/{r3.json()['id']}/inserat-optimierung", json=BODY_V)
check("P1-4 D: ein Nachweis laesst sich nur EINMAL einloesen", o.status_code == 403)

# E) Manipulierte Felder erzeugen keine Berechtigung
INSERAT_AUFRUFE.clear()
manipuliert = {"typ": "verkauf", "titel": "manipuliert", "eingabe": {},
               "ergebnis": {"paid": True, "verified": True, "source": "server"},
               "kind": "verkauf", "paid": True, "verified": True, "source": "server",
               "lauf_id": "f" * 32}
m = A.post("/api/v1/checks", json=manipuliert)
o = A.post(f"/api/v1/checks/{m.json()['id']}/inserat-optimierung", json=BODY_V)
check("P1-4 E: erfundene Felder + erfundene lauf_id -> blockiert",
      o.status_code == 403 and not INSERAT_AUFRUFE, f"HTTP {o.status_code}")

# B) Fremder echter VerkaufsCheck
B = client()
uid_b = registriere(B, "b@block2.test")
INSERAT_AUFRUFE.clear()
o = B.post(f"/api/v1/checks/{echt_id}/inserat-optimierung", json=BODY_V)
check("P1-4 B: fremder echter Check -> 403",
      o.status_code == 403 and code(o) == "forbidden", f"HTTP {o.status_code} {code(o)}")
check("P1-4 B: kein LLM-Aufruf", not INSERAT_AUFRUFE)

# Fremder Nachweis laesst sich nicht auf das eigene Konto einloesen
setze(uid_b, "verkaufschecks_verbleibend", 1)
rb = B.post("/api/v1/verkaufscheck", json=BODY_V, headers=H)
fremder_nachweis = rb.json()["lauf_id"]
sa = A.post("/api/v1/checks", json={"typ": "verkauf", "titel": "fremd", "eingabe": {},
                                    "ergebnis": {}, "lauf_id": fremder_nachweis})
o = A.post(f"/api/v1/checks/{sa.json()['id']}/inserat-optimierung", json=BODY_V)
check("P1-4 B: fremder Lauf-Nachweis ist fuer das eigene Konto wertlos", o.status_code == 403)

# Typbindung: ein KaufCheck-Nachweis schaltet keinen VerkaufsCheck frei
setze(uid_a, "kaufchecks_verbleibend", 1)
rk = A.post("/api/v1/kaufcheck", json=BODY_K, headers=H)
kauf_nachweis = rk.json().get("lauf_id")
check("P1-4: KaufCheck stellt ebenfalls einen Nachweis aus", bool(kauf_nachweis))
sk = A.post("/api/v1/checks", json={"typ": "verkauf", "titel": "typtausch", "eingabe": {},
                                    "ergebnis": {}, "lauf_id": kauf_nachweis})
o = A.post(f"/api/v1/checks/{sk.json()['id']}/inserat-optimierung", json=BODY_V)
check("P1-4: KaufCheck-Nachweis schaltet keine VerkaufsCheck-Optimierung frei", o.status_code == 403)

# F) Bestandschecks: Anzeigen/Loeschen/Rueckfragen bleiben heil
with db.get_conn() as conn:
    conn.execute("INSERT INTO checks (user_id, typ, titel, eingabe, ergebnis) VALUES (?,?,?,?,?)",
                 (uid_a, "verkauf", "historisch", "{}", '{"bericht":"alt"}'))
    conn.commit()
    alt_id = conn.execute("SELECT id FROM checks WHERE titel='historisch'").fetchone()[0]
g = A.get(f"/api/v1/checks/{alt_id}")
liste = A.get("/api/v1/checks")
frage = A.post(f"/api/v1/checks/{alt_id}/fragen", json={"frage": "f", "antwort": "a"})
fragen = A.get(f"/api/v1/checks/{alt_id}/fragen")
check("P1-4 F: historischer Check bleibt les- und nutzbar",
      g.status_code == 200 and liste.status_code == 200
      and frage.status_code == 201 and fragen.status_code == 200,
      f"{g.status_code}/{liste.status_code}/{frage.status_code}/{fragen.status_code}")
o = A.post(f"/api/v1/checks/{alt_id}/inserat-optimierung", json=BODY_V)
check("P1-4 F: historischer Check ohne Nachweis wird NICHT still freigeschaltet",
      o.status_code == 403 and code(o) == "kein_echter_verkaufscheck")
d = A.delete(f"/api/v1/checks/{alt_id}")
check("P1-4 F: historischer Check bleibt loeschbar", d.status_code == 204)


# ============================================================================
# P1-6 — Credit erst nach Validierung, Rueckerstattung bei jedem Fehler
# ============================================================================
C = client()
uid_c = registriere(C, "c@block2.test")

# Struktur: keine Kontingent-Entnahme mehr in einer FastAPI-Dependency
for modul in ("app/routers/kaufcheck.py", "app/routers/verkaufscheck.py"):
    baum = ast.parse(open(os.path.join(_HIER, modul), encoding="utf-8").read())
    depends = [n for n in ast.walk(baum)
               if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "Depends"]
    namen = [getattr(d.args[0], "id", "") for d in depends if d.args]
    check(f"P1-6 Struktur: {modul} entnimmt kein Kontingent per Dependency",
          not any("access" in n or "entnehme" in n for n in namen), str(namen))

# 1) ungueltiger Datentyp
setze(uid_c, "verkaufschecks_verbleibend", 1)
r = C.post("/api/v1/verkaufscheck", json={**BODY_V, "baujahr": "keine-zahl"}, headers=H)
check("P1-6 1: ungueltiger Datentyp -> 422, Credit bleibt",
      r.status_code == 422 and stand(uid_c) == 1, f"HTTP {r.status_code}, Credits={stand(uid_c)}")

# 2) zu langer Freitext
r = C.post("/api/v1/verkaufscheck", json={**BODY_V, "beschreibung": "x" * 9000}, headers=H)
check("P1-6 2: zu langer Text -> abgelehnt, Credit bleibt",
      r.status_code == 422 and stand(uid_c) == 1, f"HTTP {r.status_code}, Credits={stand(uid_c)}")

# 3) falscher API-Key
r = C.post("/api/v1/verkaufscheck", json=BODY_V, headers={"Authorization": "Bearer falsch"})
check("P1-6 3: falscher API-Key -> 403, Credit bleibt",
      r.status_code == 403 and stand(uid_c) == 1, f"HTTP {r.status_code}, Credits={stand(uid_c)}")

# nicht eingeloggt -> 401
anon = client()
r = anon.post("/api/v1/verkaufscheck", json=BODY_V, headers=H)
check("P1-6: ohne Login -> 401", r.status_code == 401)


def wirf(exc):
    async def _f(body, retry=False):
        raise exc
    return _f


# 4) Gemini-Ausfall
r_verk.run_verkaufscheck = wirf(GeminiFehlgeschlagen("simuliert"))
r = C.post("/api/v1/verkaufscheck", json=BODY_V, headers=H)
check("P1-6 4: Gemini-Ausfall -> 503, Credit erstattet",
      r.status_code == 503 and stand(uid_c) == 1, f"HTTP {r.status_code}, Credits={stand(uid_c)}")

# 4b) RechercheUnzureichend
r_verk.run_verkaufscheck = wirf(RechercheUnzureichend(None, "keine belastbaren Marktdaten"))
r = C.post("/api/v1/verkaufscheck", json=BODY_V, headers=H)
check("P1-6 4b: research_failed -> Credit erstattet, kein Lauf-Nachweis",
      r.status_code == 200 and r.json().get("research_status") == "research_failed"
      and r.json().get("lauf_id") is None and stand(uid_c) == 1,
      f"HTTP {r.status_code}, Credits={stand(uid_c)}")

# 5) unerwarteter interner Fehler NACH der Entnahme
r_verk.run_verkaufscheck = wirf(RuntimeError("unerwartet"))
r = C.post("/api/v1/verkaufscheck", json=BODY_V, headers=H)
check("P1-6 5: unerwarteter 500 -> Credit erstattet",
      r.status_code == 500 and stand(uid_c) == 1, f"HTTP {r.status_code}, Credits={stand(uid_c)}")

# 5b) Timeout
r_verk.run_verkaufscheck = wirf(asyncio.TimeoutError())
r = C.post("/api/v1/verkaufscheck", json=BODY_V, headers=H)
check("P1-6 5b: Timeout -> Credit erstattet", stand(uid_c) == 1, f"Credits={stand(uid_c)}")

# 5c) Fehler beim Ausstellen des Nachweises kostet den bezahlten Check nicht
r_verk.run_verkaufscheck = lauf_verkauf
orig_erzeuge = r_verk.erzeuge_lauf_nachweis
r_verk.erzeuge_lauf_nachweis = lambda *a, **k: None
r = C.post("/api/v1/verkaufscheck", json=BODY_V, headers=H)
check("P1-6 5c: fehlender Nachweis bricht den bezahlten Check nicht ab",
      r.status_code == 200 and r.json().get("lauf_id") is None and stand(uid_c) == 0,
      f"HTTP {r.status_code}, Credits={stand(uid_c)}")
r_verk.erzeuge_lauf_nachweis = orig_erzeuge

# 6) erfolgreicher Check verbraucht
setze(uid_c, "verkaufschecks_verbleibend", 1)
r = C.post("/api/v1/verkaufscheck", json=BODY_V, headers=H)
check("P1-6 6: erfolgreicher Check -> Credit verbraucht",
      r.status_code == 200 and stand(uid_c) == 0, f"HTTP {r.status_code}, Credits={stand(uid_c)}")

# 7) Double-Spend: 1 Credit, 20 parallele Entnahmen
setze(uid_c, "verkaufschecks_verbleibend", 1)
setze(uid_c, "checks_verbleibend", 0)
erfolge, abgelehnt = [], []


def entnimm():
    try:
        erfolge.append(check_gate.entnehme_verkaufscheck(uid_c))
    except HTTPException:
        abgelehnt.append(1)


faeden = [threading.Thread(target=entnimm) for _ in range(20)]
for f in faeden:
    f.start()
for f in faeden:
    f.join()
check("P1-6 7: 1 Credit / 20 parallel -> genau eine Entnahme",
      len(erfolge) == 1 and len(abgelehnt) == 19 and stand(uid_c) == 0,
      f"Erfolge={len(erfolge)}, abgelehnt={len(abgelehnt)}, Rest={stand(uid_c)}")

# 8) Refund mehrfach -> kein zusaetzliches Kontingent
zugriff = erfolge[0]
check_gate.refund_check_credit(zugriff)
check_gate.refund_check_credit(zugriff)
check_gate.refund_check_credit(zugriff)
check("P1-6 8: dreifacher Refund erhoeht das Kontingent nur einmal",
      stand(uid_c) == 1, f"Credits={stand(uid_c)}")

# 8b) Zwei getrennte Entnahmen erstatten getrennt
setze(uid_c, "verkaufschecks_verbleibend", 2)
z1 = check_gate.entnehme_verkaufscheck(uid_c)
z2 = check_gate.entnehme_verkaufscheck(uid_c)
check_gate.refund_check_credit(z1)
check_gate.refund_check_credit(z2)
check_gate.refund_check_credit(z1)
check("P1-6 8b: zwei Entnahmen -> genau zwei Rueckerstattungen",
      stand(uid_c) == 2, f"Credits={stand(uid_c)}")

# MAX-Abo: kein Dekrement, kein Refund-Gewinn
uid_max = registriere(client(), "max@block2.test")
setze(uid_max, "abo_typ", "max")
zmax = check_gate.entnehme_verkaufscheck(uid_max)
check_gate.refund_check_credit(zmax)
check_gate.refund_check_credit(zmax)
check("P1-6: MAX-Abo erzeugt durch Refunds kein gezaehltes Guthaben",
      zmax.quelle == check_gate.QUELLE_UNBEGRENZT and stand(uid_max) == 0)

r_verk.run_verkaufscheck = lauf_verkauf


# ============================================================================
# P1-7 — Client-IP hinter dem Proxy
# ============================================================================
import app.client_ip as cip  # noqa: E402
import app.rate_limit as rl  # noqa: E402
import app.routers.analyse_frage as r_frage  # noqa: E402
import app.routers.autofinder as r_af  # noqa: E402
import app.routers.chat as r_chat  # noqa: E402
import app.routers.user_auth as r_auth  # noqa: E402
import app.usage_limit as ul  # noqa: E402


def anfrage(peer, headers=None):
    from starlette.requests import Request
    roh = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "http_version": "1.1", "method": "POST", "scheme": "http",
                    "path": "/x", "raw_path": b"/x", "query_string": b"", "root_path": "",
                    "headers": roh, "client": (peer, 1234), "server": ("testserver", 80),
                    "app": None})


def mit(hops=0, header="x-forwarded-for", netze=None):
    cip.TRUSTED_PROXY_HOPS = hops
    cip.CLIENT_IP_HEADER = header
    if netze is not None:
        cip._TRUSTED = [ipaddress.ip_network(n) for n in netze]


PROXY = "100.64.0.7"      # CGNAT — im Default-Vertrauensnetz
ORIG = (cip.TRUSTED_PROXY_HOPS, cip.CLIENT_IP_HEADER)

# Default: nichts wird aus Headern uebernommen
mit(hops=0)
check("P1-7: Default (0 Hops) ignoriert X-Forwarded-For vollstaendig",
      cip.klient_ip(anfrage("203.0.113.9", {"x-forwarded-for": "1.2.3.4"})) == "203.0.113.9")

# A/B) Vertrauenswuerdiger Proxy, zwei verschiedene Clients -> zwei Schluessel
mit(hops=1)
a_key = cip.klient_ip(anfrage(PROXY, {"x-forwarded-for": "198.51.100.1"}))
b_key = cip.klient_ip(anfrage(PROXY, {"x-forwarded-for": "198.51.100.2"}))
check("P1-7 A: Client-IP 1 hinter vertrauenswuerdigem Proxy", a_key == "198.51.100.1", a_key)
check("P1-7 B: Client-IP 2 bekommt einen anderen Schluessel",
      b_key == "198.51.100.2" and a_key != b_key, b_key)

# C) Direkter Angreifer mit selbst gesetztem Header
direkt = cip.klient_ip(anfrage("203.0.113.9", {"x-forwarded-for": "9.9.9.9"}))
check("P1-7 C: direkter Request kann die IP nicht faelschen", direkt == "203.0.113.9", direkt)
verschiedene = {cip.klient_ip(anfrage("203.0.113.9", {"x-forwarded-for": f"9.9.9.{i}"}))
                for i in range(5)}
check("P1-7 C: wechselnde gefaelschte Header ergeben denselben Schluessel",
      verschiedene == {"203.0.113.9"})

# C2) Vorangestellter Client-Wert hinter dem Proxy wird ignoriert
getarnt = cip.klient_ip(anfrage(PROXY, {"x-forwarded-for": "9.9.9.9, 198.51.100.5"}))
check("P1-7 C: nur der vom Proxy angehaengte Eintrag zaehlt", getarnt == "198.51.100.5", getarnt)

# Fehlerfaelle fallen immer auf die Gegenstelle zurueck
check("P1-7: fehlender Header -> Gegenstelle", cip.klient_ip(anfrage(PROXY)) == PROXY)
check("P1-7: unsinniger Header -> Gegenstelle",
      cip.klient_ip(anfrage(PROXY, {"x-forwarded-for": "kein-ip-wert"})) == PROXY)
check("P1-7: leerer Header -> Gegenstelle",
      cip.klient_ip(anfrage(PROXY, {"x-forwarded-for": ""})) == PROXY)
mit(hops=2)
check("P1-7: zu kurze Kette bei 2 Hops -> Gegenstelle",
      cip.klient_ip(anfrage(PROXY, {"x-forwarded-for": "198.51.100.1"})) == PROXY)
check("P1-7: 2 Hops nehmen den zweiten Eintrag von rechts",
      cip.klient_ip(anfrage(PROXY, {"x-forwarded-for": "198.51.100.1, 10.1.2.3"})) == "198.51.100.1")
mit(hops=1, header="x-real-ip")
check("P1-7: x-real-ip wird als ein Wert gelesen",
      cip.klient_ip(anfrage(PROXY, {"x-real-ip": "198.51.100.77"})) == "198.51.100.77")
check("P1-7: x-real-ip vom direkten Client zaehlt nicht",
      cip.klient_ip(anfrage("203.0.113.9", {"x-real-ip": "198.51.100.77"})) == "203.0.113.9")
mit(hops=ORIG[0], header=ORIG[1])

# D) Produktlimits unveraendert
from app.config import (AUTOFINDER_ANONYM_DEMO_PRO_TAG,  # noqa: E402
                        AUTOFINDER_FREE_LIMIT_MONATLICH,
                        AUTOFINDER_PLUS_LIMIT_MONATLICH,
                        CHAT_FREE_LIMIT_MONATLICH,
                        CHAT_PLUS_LIMIT_MONATLICH)
check("P1-7 D: Chat-Limits unveraendert (20/100 pro Monat)",
      (CHAT_FREE_LIMIT_MONATLICH, CHAT_PLUS_LIMIT_MONATLICH) == (20, 100))
check("P1-7 D: AutoFinder-Limits unveraendert (5/50 pro Monat, 1 Demo/Tag)",
      (AUTOFINDER_FREE_LIMIT_MONATLICH, AUTOFINDER_PLUS_LIMIT_MONATLICH,
       AUTOFINDER_ANONYM_DEMO_PRO_TAG) == (5, 50, 1))
quellen = {m: open(os.path.join(_HIER, "app", "routers", f"{m}.py"), encoding="utf-8").read()
           for m in ("chat", "kaufcheck", "verkaufscheck", "user_auth", "analyse_frage")}
check("P1-7 D: Route-Limits unveraendert",
      '@limiter.limit("20/minute")' in quellen["chat"]
      and '@limiter.limit("10/minute")' in quellen["kaufcheck"]
      and '@limiter.limit("10/minute")' in quellen["verkaufscheck"]
      and '@limiter.limit("10/minute")' in quellen["user_auth"]
      and '@limiter.limit("20/minute")' in quellen["analyse_frage"])

# Alle Limiter nutzen dieselbe Schluesselquelle
check("P1-7: alle Limiter teilen dieselbe Schluesselfunktion",
      all(limiter._key_func is cip.limit_schluessel for limiter in
          (rl.limiter, r_af.limiter, r_chat.limiter, r_auth.limiter, r_frage.limiter,
           r_kauf.limiter, r_verk.limiter)))

# Anonymer Kontingent-Anker nutzt dieselbe Quelle
mit(hops=1)
check("P1-7: anonymer Kontingent-Anker folgt der echten Client-IP",
      ul._schluessel(anfrage(PROXY, {"x-forwarded-for": "198.51.100.3"}), None) == "ip:198.51.100.3")
check("P1-7: eingeloggt bleibt der Anker das Konto",
      ul._schluessel(anfrage(PROXY, {"x-forwarded-for": "198.51.100.3"}), 42) == "user:42")
mit(hops=ORIG[0], header=ORIG[1])

# Der Server darf X-Forwarded-For nicht schon vor uns auswerten: uvicorn
# ueberschreibt request.client, wenn die Gegenstelle in --forwarded-allow-ips
# steht (Default 127.0.0.1). Im Live-Retest liess sich die IP dadurch von
# loopback aus faelschen, obwohl app/client_ip.py sie verworfen haette.
_dockerfile = open(os.path.join(_HIER, "Dockerfile"), encoding="utf-8").read()
check("P1-7: Produktions-Image startet uvicorn mit --no-proxy-headers",
      "--no-proxy-headers" in _dockerfile,
      "uvicorn wuerde XFF sonst selbst auswerten und request.client ueberschreiben")

# Diagnose-Endpunkt ist admin-geschuetzt
d = client().get("/api/v1/admin/client-ip", headers=H)
check("P1-7: Diagnose-Endpunkt ist fuer den Consumer-Key gesperrt",
      d.status_code == 403, f"HTTP {d.status_code}")
import app.auth as auth_modul  # noqa: E402
auth_modul.ADMIN_API_KEY = "A" * 48
d = client().get("/api/v1/admin/client-ip", headers={"Authorization": "Bearer " + "A" * 48})
check("P1-7: Diagnose-Endpunkt liefert dem Admin den verwendeten Schluessel",
      d.status_code == 200 and "verwendeter_limit_schluessel" in d.json(), f"HTTP {d.status_code}")
auth_modul.ADMIN_API_KEY = ""


# -- Ergebnis ---------------------------------------------------------------
print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER:")
    for f in FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("Alle Security-Block-2-Tests bestanden.")
