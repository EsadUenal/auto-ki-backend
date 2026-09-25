"""
Test: Closed-Beta-Invite-System (Release-Schritt 10)

Prueft OHNE Netzwerk, OHNE echten Mailversand und OHNE Stripe gegen eine
TEMPORAERE DB, dass eine Einladung genau einmal genau ein Paket freischaltet
und in jedem anderen Fall gar nichts.

Abgedeckte Punkte der Auftragsmatrix (§24):
  A) Invite erstellen -> DB speichert keinen Raw Token
  B) gueltiger Token + richtige Mail -> +1 KaufCheck +1 VerkaufsCheck
  C) wiederholter Redeem -> keine weiteren Credits
  D) zwei parallele Redeems -> exakt einmal +1/+1
  E) falsche Account-Mail -> 0/0 und Invite bleibt offen
  F) abgelaufen -> 0/0
  G) ungueltiger/unbekannter Token -> 0/0
  H) bereits eingeloest (anderes Konto) -> 0/0 zusaetzlich
  I) vorhandene Credits bleiben erhalten (addiert, nicht ersetzt)
  J) keine Plus-Aktivierung
  K) AutoFinder-Limit unveraendert
  L) Chat-Limit unveraendert
  M) keine Stripe-Objekte beruehrt
  N) Fehler bei der zweiten Gutschrift -> weder halber Grant noch redeemed
  O) neue Einladung fuer dieselbe offene Adresse -> alte wird ungueltig
  P) bereits eingeloeste Adresse bekommt keine neue V1-Einladung
  Q) E-Mail-Normalisierung (Gross/Klein, Leerzeichen) trifft dasselbe Konto
  R) email_verified wird NICHT veraendert (kein Verifikations-Bypass)

Ausfuehren:  python test_beta_invite.py
"""
import os
import tempfile
import threading

# WICHTIG: temporaere DB VOR dem Import der app-Module setzen.
_TMP = tempfile.mkdtemp(prefix="enfal_beta_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["FRONTEND_URL"] = "https://app.example.test"

import app.database as db          # noqa: E402
db.ensure_tables()

import app.beta_invite as beta     # noqa: E402
import app.check_gate as gate      # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


# ── Helfer ───────────────────────────────────────────────────────────────────

def neuer_user(email: str, kauf: int = 0, verkauf: int = 0, verifiziert: int = 0) -> int:
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, kaufchecks_verbleibend, "
            "verkaufschecks_verbleibend, email_verified) VALUES (?,?,?,?,?)",
            (email, "x", kauf, verkauf, verifiziert),
        )
        conn.commit()
        return cur.lastrowid


def stand(uid: int) -> tuple[int, int]:
    with db.get_conn() as conn:
        r = conn.execute(
            "SELECT kaufchecks_verbleibend, verkaufschecks_verbleibend FROM users WHERE id=?",
            (uid,),
        ).fetchone()
    return (r["kaufchecks_verbleibend"], r["verkaufschecks_verbleibend"])


def code_ohne_doku(pfad: str) -> str:
    """Quelltext eines Moduls OHNE Kommentare und Docstrings.

    Noetig, weil ein simples `"stripe" in datei` genau das findet, was der
    Docstring dieses Moduls ueber Stripe ERKLAERT — und damit eine Zusicherung
    prueft, die gar nicht im Code steht. Gewertet werden nur Bezeichner und
    echte String-Literale (z.B. SQL), nie Dokumentation.
    """
    import ast
    baum = ast.parse(open(pfad, encoding="utf-8").read())
    docstrings = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            erstes = knoten.body[0] if knoten.body else None
            if (isinstance(erstes, ast.Expr) and isinstance(erstes.value, ast.Constant)
                    and isinstance(erstes.value.value, str)):
                docstrings.add(id(erstes.value))
    teile = []
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Name):
            teile.append(knoten.id)
        elif isinstance(knoten, ast.Attribute):
            teile.append(knoten.attr)
        elif isinstance(knoten, (ast.Import, ast.ImportFrom)):
            teile.append(getattr(knoten, "module", "") or "")
            teile.extend(a.name for a in knoten.names)
        elif isinstance(knoten, ast.Constant) and isinstance(knoten.value, str):
            if id(knoten) not in docstrings:
                teile.append(knoten.value)
    return " ".join(teile)


def invite_zeile(token: str):
    with db.get_conn() as conn:
        return conn.execute(
            "SELECT * FROM beta_invite WHERE token_hash=?", (beta._token_hash(token),)
        ).fetchone()


# ── A) Erstellen speichert keinen Raw Token ──────────────────────────────────
token_a = beta.erzeuge_einladung("Tester-A@Example.de")
with db.get_conn() as conn:
    alle = conn.execute("SELECT * FROM beta_invite").fetchall()
spalten_inhalt = " ".join(str(v) for z in alle for v in tuple(z))
check("A: Roh-Token steht NICHT in der Datenbank", token_a not in spalten_inhalt)
check("A: stattdessen liegt der SHA-256-Hash dort",
      invite_zeile(token_a)["token_hash"] == beta._token_hash(token_a))
check("A: Adresse wurde normalisiert gespeichert (strip+lower)",
      invite_zeile(token_a)["email"] == "tester-a@example.de")
check("A: Token hat hohe Entropie (>=40 Zeichen urlsafe)", len(token_a) >= 40)
check("A: Link traegt den Token im Fragment (nie im Access-Log)",
      beta.einladungslink(token_a) == f"https://app.example.test/beta#token={token_a}")

# ── B) Gueltig + richtige Mail -> +1/+1 ──────────────────────────────────────
uid_a = neuer_user("tester-a@example.de")
check("B: Konto startet ohne Check-Berechtigungen", stand(uid_a) == (0, 0))
erg = beta.loese_ein(token_a, uid_a)
check("B: Status 'aktiviert'", erg.status == beta.AKTIVIERT)
check("B: genau +1 KaufCheck und +1 VerkaufsCheck", stand(uid_a) == (1, 1))
check("B: Antwort nennt das Paket (1/1)", (erg.kaufchecks, erg.verkaufschecks) == (1, 1))
check("B: Einladung ist als eingeloest markiert", invite_zeile(token_a)["eingeloest_at"] is not None)
check("B: Einladung haelt fest, WER eingeloest hat",
      invite_zeile(token_a)["eingeloest_von"] == uid_a)

# ── C) Wiederholter Redeem -> keine weiteren Credits ─────────────────────────
erg2 = beta.loese_ein(token_a, uid_a)
check("C: zweiter Versuch meldet 'bereits_aktiviert'", erg2.status == beta.BEREITS_AKTIVIERT)
check("C: keine weiteren Credits", stand(uid_a) == (1, 1))
erg3 = beta.loese_ein(token_a, uid_a)
check("C: auch der dritte Versuch vergibt nichts", stand(uid_a) == (1, 1) and erg3.status == beta.BEREITS_AKTIVIERT)

# ── D) Zwei parallele Redeems -> exakt einmal ────────────────────────────────
token_d = beta.erzeuge_einladung("parallel@example.de")
uid_d = neuer_user("parallel@example.de")
ergebnisse = []
barriere = threading.Barrier(2)


def redeem_parallel():
    barriere.wait()
    try:
        ergebnisse.append(beta.loese_ein(token_d, uid_d).status)
    except Exception as exc:                     # noqa: BLE001
        # Eine SQLite-Schreibkollision ist ein zulaessiger Ausgang — sie darf
        # nur niemals zu einem zweiten Paket fuehren.
        ergebnisse.append(f"exception:{type(exc).__name__}")


t1, t2 = threading.Thread(target=redeem_parallel), threading.Thread(target=redeem_parallel)
t1.start(); t2.start(); t1.join(); t2.join()
check("D: zwei parallele Einloesungen -> das Paket flieSSt genau einmal",
      stand(uid_d) == (1, 1))
check("D: hoechstens eine Einloesung meldet Erfolg",
      len([e for e in ergebnisse if e == beta.AKTIVIERT]) == 1)

# ── E) Falsche Account-Mail -> 0/0, Invite bleibt offen ──────────────────────
token_e = beta.erzeuge_einladung("eingeladen@example.de")
uid_fremd = neuer_user("jemand-anders@example.de")
erg_e = beta.loese_ein(token_e, uid_fremd)
check("E: falsches Konto bekommt nichts", stand(uid_fremd) == (0, 0))
check("E: generische Ablehnung (kein eigener Fehlercode)", erg_e.status == beta.NICHT_VERWENDBAR)
check("E: Einladung wurde NICHT verbraucht", invite_zeile(token_e)["eingeloest_at"] is None)
# ... und die eingeladene Person kann sie danach noch normal nutzen:
uid_e = neuer_user("eingeladen@example.de")
check("E: die eingeladene Person kann danach weiterhin einloesen",
      beta.loese_ein(token_e, uid_e).status == beta.AKTIVIERT and stand(uid_e) == (1, 1))

# ── F) Abgelaufen -> 0/0 ─────────────────────────────────────────────────────
token_f = beta.erzeuge_einladung("abgelaufen@example.de")
uid_f = neuer_user("abgelaufen@example.de")
with db.get_conn() as conn:
    conn.execute("UPDATE beta_invite SET laeuft_ab_at='2000-01-01 00:00:00' WHERE token_hash=?",
                 (beta._token_hash(token_f),))
    conn.commit()
erg_f = beta.loese_ein(token_f, uid_f)
check("F: abgelaufene Einladung vergibt nichts", stand(uid_f) == (0, 0))
check("F: gleiche generische Antwort wie bei falschem Konto",
      erg_f.status == beta.NICHT_VERWENDBAR)

# ── G) Unbekannter / kaputter Token -> 0/0 ───────────────────────────────────
uid_g = neuer_user("unbekannt@example.de")
for wert in ("", "   ", "nicht-existent", "x" * 600, "../../etc/passwd"):
    erg_g = beta.loese_ein(wert, uid_g)
    if erg_g.status != beta.NICHT_VERWENDBAR or stand(uid_g) != (0, 0):
        check(f"G: Token {wert[:20]!r} vergibt nichts", False)
        break
else:
    check("G: unbekannte, leere und ueberlange Token vergeben nichts", True)

# ── H) Bereits eingeloest, anderes Konto -> 0/0 und kein Hinweis ─────────────
uid_h = neuer_user("spaeter@example.de")
erg_h = beta.loese_ein(token_a, uid_h)   # token_a gehoert tester-a und ist verbraucht
check("H: fremdes Konto auf verbrauchter Einladung bekommt nichts", stand(uid_h) == (0, 0))
check("H: Antwort ist generisch, NICHT 'bereits_aktiviert'",
      erg_h.status == beta.NICHT_VERWENDBAR)

# ── I) Vorhandene Credits bleiben erhalten (addieren, nicht ersetzen) ────────
token_i = beta.erzeuge_einladung("bestand@example.de")
uid_i = neuer_user("bestand@example.de", kauf=2, verkauf=3)
beta.loese_ein(token_i, uid_i)
check("I: 2/3 vorher -> 3/4 nachher (addiert, nicht ersetzt)", stand(uid_i) == (3, 4))

# ── J/K/L) Kein Plus, keine Aenderung an AutoFinder-/Chat-Limits ─────────────
with db.get_conn() as conn:
    r = conn.execute(
        "SELECT abo_typ, plus_period_end, plus_kaufchecks_verbleibend, "
        "plus_verkaufschecks_verbleibend, checks_verbleibend FROM users WHERE id=?",
        (uid_i,),
    ).fetchone()
check("J: kein Plus aktiviert (abo_typ unveraendert 'none')", r["abo_typ"] == "none")
check("J: kein Plus-Zeitraum gesetzt", r["plus_period_end"] is None)
check("J: keine Plus-Monatskontingente vergeben",
      (r["plus_kaufchecks_verbleibend"], r["plus_verkaufschecks_verbleibend"]) == (0, 0))
check("J: der generische Legacy-Topf wurde nicht benutzt", r["checks_verbleibend"] == 0)

import app.usage_limit as ul  # noqa: E402
# Nicht die Konstanten gegen sich selbst pruefen, sondern den Zustand des
# Kontos, das gerade ein Beta-Paket bekommen hat: es darf danach immer noch
# als Free-Nutzer gelten.
hat_abo, plus_aktiv = ul._nutzer_zustand(uid_i)
check("K/L: das Konto gilt nach dem Einloesen weiterhin als Free-Nutzer",
      (hat_abo, plus_aktiv) == (False, False))
check("K: AutoFinder-Limit bleibt das Free-Kontingent (5)",
      ul.limit_fuer(ul.ART_AUTOFINDER, plus_aktiv) == ul.AUTOFINDER_FREE_LIMIT_MONATLICH)
check("L: Chat-Limit bleibt das Free-Kontingent (20)",
      ul.limit_fuer(ul.ART_CHAT, plus_aktiv) == ul.CHAT_FREE_LIMIT_MONATLICH)

with db.get_conn() as conn:
    verbrauch = conn.execute("SELECT COUNT(*) AS n FROM usage_monat").fetchone()["n"]
check("K/L: das Einloesen legt keine Nutzungszaehler an", verbrauch == 0)

# ── M) Keine Stripe-Objekte ──────────────────────────────────────────────────
with db.get_conn() as conn:
    z = conn.execute("SELECT COUNT(*) AS n FROM kauf_zahlung").fetchone()["n"]
    e = conn.execute("SELECT COUNT(*) AS n FROM stripe_events").fetchone()["n"]
check("M: keine Zahlung und kein Stripe-Event entstanden", z == 0 and e == 0)
import app.beta_invite as _bi_quelle  # noqa: E402
# Nur echter Code, ohne Kommentare/Docstrings (die erklaeren genau diese
# Zusicherung und wuerden eine naive Textsuche immer "finden").
beta_code = code_ohne_doku(_bi_quelle.__file__).lower()
check("M: das Beta-Modul spricht nirgends mit Stripe", "stripe" not in beta_code)

# ── N) Fehler bei der zweiten Gutschrift -> kein halber Grant ────────────────
token_n = beta.erzeuge_einladung("rollback@example.de")
uid_n = neuer_user("rollback@example.de")
_echt = gate.gutschrift_in_transaktion
_zaehler = {"n": 0}


def _kaputt(conn, user_id, produkt, anzahl=1):
    _zaehler["n"] += 1
    if _zaehler["n"] == 2:              # die ZWEITE Gutschrift scheitert
        raise RuntimeError("Simulierter Fehler bei der zweiten Gutschrift")
    return _echt(conn, user_id, produkt, anzahl)


beta.gutschrift_in_transaktion = _kaputt
try:
    beta.loese_ein(token_n, uid_n)
    gescheitert = False
except RuntimeError:
    gescheitert = True
finally:
    beta.gutschrift_in_transaktion = _echt

check("N: der simulierte Fehler schlaegt durch (wird nicht verschluckt)", gescheitert)
check("N: KEIN halber Grant — beide Credits sind zurueckgerollt", stand(uid_n) == (0, 0))
check("N: die Einladung ist NICHT verbraucht und bleibt nutzbar",
      invite_zeile(token_n)["eingeloest_at"] is None)
check("N: nach dem Fehler funktioniert die Einladung normal",
      beta.loese_ein(token_n, uid_n).status == beta.AKTIVIERT and stand(uid_n) == (1, 1))

# ── O) Neue Einladung fuer dieselbe offene Adresse -> alte ungueltig ─────────
token_o1 = beta.erzeuge_einladung("ersatz@example.de")
token_o2 = beta.erzeuge_einladung("ersatz@example.de")
check("O: der alte Token ist entwertet", invite_zeile(token_o1)["entwertet_at"] is not None)
check("O: der neue Token ist offen", invite_zeile(token_o2)["entwertet_at"] is None)
uid_o = neuer_user("ersatz@example.de")
erg_o1 = beta.loese_ein(token_o1, uid_o)
check("O: der ersetzte Link vergibt nichts mehr",
      erg_o1.status == beta.NICHT_VERWENDBAR and stand(uid_o) == (0, 0))
check("O: der neue Link funktioniert",
      beta.loese_ein(token_o2, uid_o).status == beta.AKTIVIERT and stand(uid_o) == (1, 1))

# ── P) Bereits eingeloeste Adresse bekommt keine neue V1-Einladung ───────────
try:
    beta.erzeuge_einladung("ersatz@example.de")
    check("P: zweites V1-Paket fuer dieselbe Adresse wird abgelehnt", False)
except beta.EinladungAbgelehnt:
    check("P: zweites V1-Paket fuer dieselbe Adresse wird abgelehnt (EinladungAbgelehnt)", True)
check("P: die Credits der Adresse sind unveraendert geblieben", stand(uid_o) == (1, 1))

# Zweite Verteidigungslinie: selbst eine von Hand eingeschleuste zweite offene
# Einladung derselben Adresse darf kein zweites Paket freischalten.
import secrets as _secrets  # noqa: E402
_roh = _secrets.token_urlsafe(32)
with db.get_conn() as conn:
    conn.execute(
        "INSERT INTO beta_invite (token_hash, email, paket, laeuft_ab_at) VALUES (?,?,?,?)",
        (beta._token_hash(_roh), "ersatz@example.de", beta.PAKET_V1, "2099-01-01 00:00:00"),
    )
    conn.commit()
erg_p = beta.loese_ein(_roh, uid_o)
check("P: eingeschleuste Zweit-Einladung vergibt kein zweites Paket",
      erg_p.status == beta.NICHT_VERWENDBAR and stand(uid_o) == (1, 1))
check("P: dabei wurde sie auch nicht als eingeloest markiert (Rollback)",
      invite_zeile(_roh)["eingeloest_at"] is None)

# ── Q) E-Mail-Normalisierung trifft dasselbe Konto ───────────────────────────
token_q = beta.erzeuge_einladung("  Gross.Klein@Example.DE  ")
uid_q = neuer_user("gross.klein@example.de")
check("Q: Einladung mit Grossbuchstaben/Leerzeichen trifft das normalisierte Konto",
      beta.loese_ein(token_q, uid_q).status == beta.AKTIVIERT and stand(uid_q) == (1, 1))
check("Q: keine eigene Gmail-Punkt-/Alias-Regel erfunden",
      beta.normalisiere_email("a.b+x@gmail.com") == "a.b+x@gmail.com")

# ── R) Kein Verifikations-Bypass ─────────────────────────────────────────────
token_r = beta.erzeuge_einladung("unverifiziert@example.de")
uid_r = neuer_user("unverifiziert@example.de", verifiziert=0)
beta.loese_ein(token_r, uid_r)
with db.get_conn() as conn:
    verif = conn.execute("SELECT email_verified FROM users WHERE id=?", (uid_r,)).fetchone()
check("R: email_verified bleibt 0 — die Einladung bestaetigt keine Adresse",
      verif["email_verified"] == 0)
check("R: die Check-Credits sind trotzdem da (gleiche Regel wie bei Stripe-Kauf)",
      stand(uid_r) == (1, 1))
check("R: das Beta-Modul fasst email_verified im Code nirgends an",
      "email_verified" not in beta_code)

# ── Ergebnis ─────────────────────────────────────────────────────────────────
print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER:")
    for f in FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("Alle Closed-Beta-Invite-Tests bestanden.")
