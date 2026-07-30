"""
Test: Persistenz der Analyse-Rückfragen (check_frage + /checks/{id}/fragen).

Ohne Netzwerk/Login — ruft die Router-Funktionen direkt gegen eine TEMPORAERE DB.
Prueft: Speichern & Laden pro Check, Reihenfolge, Trennung zwischen Checks,
Ownership (403 bei fremdem Nutzer) und Cascade-Delete (Fragen verschwinden mit
dem Check).

Ausfuehren:  python test_check_fragen.py
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="vira_test_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "test.db")

import app.database as db            # noqa: E402
db.ensure_tables()

import app.routers.checks as checks  # noqa: E402
from fastapi import HTTPException     # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


def neuer_user(email: str) -> int:
    with db.get_conn() as conn:
        return conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, 'x')", (email,)
        ).lastrowid


def neuer_check(user_id: int, titel: str = "BMW 320d") -> int:
    with db.get_conn() as conn:
        return conn.execute(
            "INSERT INTO checks (user_id, typ, titel, eingabe, ergebnis) VALUES (?,?,?,?,?)",
            (user_id, "kauf", titel, "{}", "{}"),
        ).lastrowid


def raises_403(fn) -> bool:
    try:
        fn()
        return False
    except HTTPException as e:
        return e.status_code == 403


uid = neuer_user("a@test.local")
cid = neuer_check(uid)

# ── leer zu Beginn ──────────────────────────────────────────────────────────
check("Leer zu Beginn", checks.list_check_fragen(cid, uid) == [])

# ── speichern & laden ───────────────────────────────────────────────────────
checks.add_check_frage(cid, checks.SaveFrageBody(frage="Warum Risiko?", antwort="Weil X."), uid)
checks.add_check_frage(cid, checks.SaveFrageBody(frage="Und der Preis?", antwort="Marktgerecht."), uid)
rows = checks.list_check_fragen(cid, uid)
check("Zwei Fragen gespeichert", len(rows) == 2)
check("Reihenfolge korrekt", rows[0]["frage"] == "Warum Risiko?" and rows[1]["frage"] == "Und der Preis?")
check("Antwort korrekt geladen", rows[0]["antwort"] == "Weil X.")

# ── Trennung zwischen Checks ────────────────────────────────────────────────
cid2 = neuer_check(uid, "Audi A4")
check("Anderer Check ist leer", checks.list_check_fragen(cid2, uid) == [])

# ── Ownership: fremder Nutzer -> 403 (lesen & schreiben) ────────────────────
uid2 = neuer_user("b@test.local")
check("Fremd lesen -> 403", raises_403(lambda: checks.list_check_fragen(cid, uid2)))
check("Fremd schreiben -> 403",
      raises_403(lambda: checks.add_check_frage(cid, checks.SaveFrageBody(frage="x", antwort="y"), uid2)))
check("Fremdzugriff hat nichts veraendert", len(checks.list_check_fragen(cid, uid)) == 2)

# ── Cascade-Delete: Check loeschen -> Fragen weg ────────────────────────────
checks.delete_check(cid, uid)
with db.get_conn() as conn:
    rest = conn.execute("SELECT COUNT(*) FROM check_frage WHERE check_id=?", (cid,)).fetchone()[0]
check("Cascade-Delete: Fragen entfernt", rest == 0)

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER: " + ", ".join(FEHLER))
    raise SystemExit(1)
print("Alle Persistenz-Tests bestanden.")
