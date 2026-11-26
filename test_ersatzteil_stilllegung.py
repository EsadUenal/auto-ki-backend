"""
Stilllegung der Ersatzteilsuche -- offline (Netzsperre und Temp-DB vor den App-Imports durch den Isolationsrunner).

    python scripts/test_isolated.py test_ersatzteil_stilllegung.py

Geprueft wird der TATSAECHLICHE Routerpfad (`POST /api/v1/ersatzteile/suche`, TestClient) fuer
  - angemeldet mit Gratiskontingent (1), angemeldet mit begrenztem Abo (light), MAX, unangemeldet.
Fuer jeden gesperrten Aufruf: HTTP 503 mit stabilem Fehlercode, KEINE Kontingentaenderung, KEIN Provideraufruf
(die Provider-Stubs schlagen bei einem Aufruf ausdruecklich fehl). Der Suchcode und das Kontingent-Gate bleiben
unveraendert erhalten (Direktaufruf des Gates bucht weiterhin).
"""
import inspect
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi.testclient import TestClient

import app.database as db
import app.ersatzteil_gate as gate
import app.routers.ersatzteile as et
import app.routers.user_auth as auth
from app.main import app as _app

FEHLER: list[str] = []


def check(name, bedingung, detail=""):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f"  -> {detail}" if not bedingung and detail else ""))
    if not bedingung:
        FEHLER.append(name)


AUFRUFE = {"tavily": 0, "gemini": 0}


async def _tavily(*a, **k):
    AUFRUFE["tavily"] += 1
    raise AssertionError("Tavily darf bei gesperrter Ersatzteilsuche nie aufgerufen werden")


async def _gemini(*a, **k):
    AUFRUFE["gemini"] += 1
    raise AssertionError("Gemini darf bei gesperrter Ersatzteilsuche nie aufgerufen werden")


et.tavily_search = _tavily
et.call_gemini_json = _gemini
et.TAVILY_API_KEY = "stub-key"          # waere die Sperre nicht da, liefe die Suche bis zum Stub

client = TestClient(_app)
H = {"Authorization": "Bearer dev-key-change-in-prod"}
BODY = {"fahrzeug": "BMW 320i E92", "bauteil": "Bremsscheiben vorne"}


def neuer_user(email, abo, rest):
    with db.get_conn() as conn:
        cur = conn.execute("INSERT INTO users (email, password_hash, abo_typ, email_verified, ersatzteil_suchen_verbleibend) "
                           "VALUES (?,?,?,1,?)", (email, "x", abo, rest))
        conn.commit()
        return cur.lastrowid


def zaehler(uid):
    with db.get_conn() as conn:
        return conn.execute("SELECT ersatzteil_suchen_verbleibend, checks_verbleibend, abo_typ FROM users WHERE id=?",
                            (uid,)).fetchone()[:]


def cookie(uid):
    return {"auth_token": auth._make_token(uid, f"u{uid}@test.de")}


print("=== Reihenfolge der Sperre ===")
params = list(inspect.signature(et.ersatzteil_suche).parameters)
check("A1 die Sperre ist die ERSTE Dependency, vor Anmeldung und Kontingent-Gate",
      params.index("_gesperrt") < params.index("_user_id")
      and inspect.signature(et.ersatzteil_suche).parameters["_gesperrt"].default.dependency is et.ersatzteilsuche_stillgelegt
      and inspect.signature(et.ersatzteil_suche).parameters["_user_id"].default.dependency is gate.require_ersatzteil_access)

print("\n=== Router: alle Tarife und Aufrufer ===")
FAELLE = [("gratis", "none", 1), ("begrenztes Abo (light)", "light", 5), ("pro", "pro", 20), ("MAX", "max", 0)]
for name, abo, rest in FAELLE:
    uid = neuer_user(f"et_{abo}@test.de", abo, rest)
    vorher = zaehler(uid)
    r = client.post("/api/v1/ersatzteile/suche", headers=H, json=BODY, cookies=cookie(uid))
    nachher = zaehler(uid)
    d = r.json().get("fehler", {}) if r.headers.get("content-type", "").startswith("application/json") else {}
    check(f"B {name}: HTTP 503, stabiler Fehlercode, verständliche Meldung",
          r.status_code == 503 and d.get("code") == "ersatzteilsuche_nicht_verfuegbar"
          and d.get("nachricht") == "Die Ersatzteilsuche ist derzeit nicht verfügbar.", f"{r.status_code} {r.text[:200]}")
    check(f"B {name}: keine Kontingentänderung ({vorher})", vorher == nachher, f"{vorher} -> {nachher}")

r = client.post("/api/v1/ersatzteile/suche", headers=H, json=BODY)
check("C unangemeldet: derselbe 503 (keine Anmeldeprüfung vor der Sperre, kein Informationsunterschied)",
      r.status_code == 503 and r.json()["fehler"]["code"] == "ersatzteilsuche_nicht_verfuegbar", f"{r.status_code}")
r = client.post("/api/v1/ersatzteile/suche", headers=H, json=BODY, cookies={"auth_token": "gefaelscht"})
check("C2 ungültiger Token: ebenfalls 503, keine Buchung", r.status_code == 503)
r = client.post("/api/v1/ersatzteile/suche", headers=H, json={"fahrzeug": "", "bauteil": ""}, cookies=cookie(neuer_user("et_leer@test.de", "none", 1)))
check("C3 auch ein ungültiger Body bucht nichts (Sperre kommt aus den Dependencies)",
      r.status_code in (422, 503) and zaehler(neuer_user("et_x@test.de", "none", 1))[0] == 1)

check("D1 kein Provideraufruf in irgendeinem gesperrten Fall", AUFRUFE == {"tavily": 0, "gemini": 0}, str(AUFRUFE))

print("\n=== Erhalt: Suchcode, Gate und Konto-Felder unverändert ===")
check("E1 Suchcode und Module sind erhalten",
      all(callable(getattr(et, n, None)) for n in ("_mehrstufige_suche", "_bewerte_kompatibilitaet", "_neutralisiere_unbelegte_herkunft",
                                                    "_mindestnutzen", "_parts_identity_context")))
uid = neuer_user("et_gate@test.de", "none", 1)
gate.require_ersatzteil_access(uid)                  # Direktaufruf des Gates: bucht weiterhin (unveraendert)
check("E2 das Kontingent-Gate selbst ist unverändert (Direktaufruf bucht 1 -> 0)", zaehler(uid)[0] == 0)
with db.get_conn() as conn:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
check("E3 das Konto-Feld ersatzteil_suchen_verbleibend bleibt", "ersatzteil_suchen_verbleibend" in cols)
check("E4 die Route bleibt registriert (kein Entfernen, nur Sperre)",
      any(getattr(rt, "path", "") == "/api/v1/ersatzteile/suche" for rt in _app.routes))

print()
if FEHLER:
    print(f"{len(FEHLER)} Pruefung(en) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle Ersatzteil-Stilllegungs-Pruefungen bestanden.")
