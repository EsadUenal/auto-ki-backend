"""
Test: Tageslimit fuer kostenlose Funktionen (app/usage_limit.py)

Prueft OHNE Netzwerk und OHNE LLM gegen eine TEMPORAERE DB:

  R) unterhalb der Grenze -> erlaubt
  S) Grenze erreicht      -> sauber blockiert (429, strukturierter Fehler)
  T) neuer Reset-Zeitraum -> wieder erlaubt (UTC-Kalendertag)
  U) clientseitig nicht umgehbar: der Zaehler haengt am Konto bzw. an der IP,
     nicht an einem vom Client kontrollierten Wert; parallele Requests koennen
     die Grenze nicht ueberschreiten
  +) Chat und Analyse-Rueckfragen zaehlen in getrennte Toepfe
  +) Abo-Kunden sind ausgenommen (keine Leistungskuerzung im Bestand)
  +) AutoFinder/Autokosten bleiben unberuehrt

Ausfuehren:  python test_usage_limit.py
"""
import os
import tempfile
import threading

_TMP = tempfile.mkdtemp(prefix="vira_limit_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["AUTO_KI_CHAT_FREE_LIMIT_TAEGLICH"] = "5"

import app.database as db      # noqa: E402
db.ensure_tables()

import app.usage_limit as ul   # noqa: E402
from app.config import CHAT_FREE_LIMIT_TAEGLICH  # noqa: E402
from fastapi import HTTPException                # noqa: E402

FEHLER = []
LIMIT = CHAT_FREE_LIMIT_TAEGLICH


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeRequest:
    """Minimaler Request: Cookies + Client-IP, mehr braucht das Gate nicht."""
    def __init__(self, cookies=None, host="203.0.113.7"):
        self.cookies = cookies or {}
        self.client = _FakeClient(host)


def neuer_user(email: str, abo: str = "none") -> int:
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, abo_typ) VALUES (?,?,?)",
            (email, "x", abo),
        )
        conn.commit()
        return cur.lastrowid


check("Grenze kommt aus der zentralen Konfiguration (ENV-steuerbar)", LIMIT == 5)

# ── R) Unterhalb der Grenze -> erlaubt ───────────────────────────────────────
erlaubt = [ul.verbrauche("user:r", ul.ART_CHAT, LIMIT) for _ in range(LIMIT)]
check("R: alle Anfragen unterhalb der Grenze sind erlaubt", all(erlaubt))
check("R: der Zaehler steht danach exakt auf der Grenze",
      ul.stand("user:r", ul.ART_CHAT) == LIMIT)

# ── S) Grenze erreicht -> blockiert ──────────────────────────────────────────
check("S: die naechste Anfrage ueber der Grenze wird abgelehnt",
      ul.verbrauche("user:r", ul.ART_CHAT, LIMIT) is False)
check("S: der Zaehler waechst danach nicht weiter",
      ul.stand("user:r", ul.ART_CHAT) == LIMIT)

# HTTP-Vertrag: strukturierter Fehler, kein roher Stacktrace.
req = _FakeRequest(host="198.51.100.4")
for _ in range(LIMIT):
    ul.require_chat_kontingent(req)
try:
    ul.require_chat_kontingent(req)
    check("S: Gate wirft bei erreichter Grenze", False)
except HTTPException as e:
    detail = e.detail
    check("S: Gate antwortet mit 429", e.status_code == 429)
    check("S: Fehler ist strukturiert (code + nachricht)",
          isinstance(detail, dict)
          and detail["fehler"]["code"] == "tageslimit_erreicht"
          and "Morgen" in detail["fehler"]["nachricht"])
    check("S: Nachricht nennt keine Provider-/Technikbegriffe",
          not any(w in detail["fehler"]["nachricht"].lower()
                  for w in ("gemini", "api", "token", "quota", "429", "stripe")))

# ── T) Neuer Reset-Zeitraum -> wieder erlaubt ────────────────────────────────
check("T: am naechsten UTC-Kalendertag ist wieder alles frei",
      ul.verbrauche("user:r", ul.ART_CHAT, LIMIT, tag="2099-01-01") is True)
check("T: der Zaehler des Vortags bleibt davon unberuehrt",
      ul.stand("user:r", ul.ART_CHAT) == LIMIT)
check("T: Reset-Grenze ist ein UTC-Kalendertag im Format YYYY-MM-DD",
      len(ul.heute_utc()) == 10 and ul.heute_utc().count("-") == 2)

# ── U) Clientseitig nicht umgehbar ───────────────────────────────────────────
# Der Schluessel wird serverseitig aus Cookie bzw. Verbindungs-IP gebildet; es
# gibt keinen vom Client gesetzten Wert, der ihn beeinflusst.
uid = neuer_user("u@test.de")
import app.routers.user_auth as auth   # noqa: E402
token = auth._make_token(uid, "u@test.de")

req_user = _FakeRequest(cookies={"auth_token": token}, host="203.0.113.9")
check("U: eingeloggt wird am Konto gezaehlt, nicht an der IP",
      ul._schluessel(req_user) == f"user:{uid}")

req_anon = _FakeRequest(host="203.0.113.9")
check("U: anonym wird an der Verbindungs-IP gezaehlt",
      ul._schluessel(req_anon) == "ip:203.0.113.9")

check("U: ein gefaelschtes/ungueltiges Cookie faellt auf die IP zurueck "
      "(erzeugt keinen neuen Zaehler nach Wahl des Clients)",
      ul._schluessel(_FakeRequest(cookies={"auth_token": "gefaelscht"},
                                  host="203.0.113.9")) == "ip:203.0.113.9")

# Ein Konto-Wechsel der IP umgeht die Grenze nicht.
for _ in range(LIMIT):
    ul.require_chat_kontingent(_FakeRequest(cookies={"auth_token": token}, host="1.1.1.1"))
try:
    ul.require_chat_kontingent(_FakeRequest(cookies={"auth_token": token}, host="2.2.2.2"))
    check("U: IP-Wechsel umgeht die Kontogrenze nicht", False)
except HTTPException:
    check("U: IP-Wechsel umgeht die Kontogrenze nicht", True)

# Parallele Requests koennen die Grenze nicht ueberschreiten.
treffer = []
barriere = threading.Barrier(8)


def parallel():
    barriere.wait()
    treffer.append(ul.verbrauche("user:parallel", ul.ART_CHAT, 3))


threads = [threading.Thread(target=parallel) for _ in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("U: 8 parallele Anfragen bei Grenze 3 -> genau 3 kommen durch",
      sum(1 for x in treffer if x) == 3)
check("U: der Zaehler steht danach exakt auf der Grenze",
      ul.stand("user:parallel", ul.ART_CHAT) == 3)

# ── Getrennte Toepfe ─────────────────────────────────────────────────────────
for _ in range(LIMIT):
    ul.verbrauche("user:topf", ul.ART_CHAT, LIMIT)
check("Chat ausgeschoepft, Analyse-Rueckfragen bleiben davon unberuehrt",
      ul.verbrauche("user:topf", ul.ART_ANALYSE_FRAGE, LIMIT) is True)
check("Rueckfragen zu einem bezahlten Check zaehlen in einen eigenen Topf",
      ul.stand("user:topf", ul.ART_CHAT) == LIMIT
      and ul.stand("user:topf", ul.ART_ANALYSE_FRAGE) == 1)

# ── Abo-Kunden sind ausgenommen ──────────────────────────────────────────────
uid_abo = neuer_user("abo@test.de", abo="pro")
token_abo = auth._make_token(uid_abo, "abo@test.de")
req_abo = _FakeRequest(cookies={"auth_token": token_abo})
for _ in range(LIMIT * 3):
    ul.require_chat_kontingent(req_abo)
check("Abo-Kunde wird nicht gedeckelt (keine Leistungskuerzung im Bestand)",
      ul.stand(f"user:{uid_abo}", ul.ART_CHAT) == 0)

# ── Limit abschaltbar ────────────────────────────────────────────────────────
check("Limit 0 deaktiviert die Grenze vollstaendig",
      all(ul.verbrauche("user:aus", ul.ART_CHAT, 0) for _ in range(50)))
check("deaktiviertes Limit zaehlt auch nicht mit",
      ul.stand("user:aus", ul.ART_CHAT) == 0)

# ── AutoFinder / Autokosten bleiben kostenlos ────────────────────────────────
import ast       # noqa: E402
import inspect   # noqa: E402
import app.routers.autofinder as af  # noqa: E402

af_quelle = inspect.getsource(af)
af_baum = ast.parse(af_quelle)

# Bewusst ueber den SYNTAXBAUM statt ueber Zeichenfolgen: der Router ERWAEHNT
# `require_check_access` in seinem Docstring ("bewusst OHNE Check-Gate"). Eine
# reine Substring-Pruefung wuerde daran scheitern und damit das Gegenteil dessen
# messen, was sie messen soll.
af_importiert = {
    alias.name.split(".")[0] if isinstance(k, ast.Import) else alias.name
    for k in ast.walk(af_baum) if isinstance(k, (ast.Import, ast.ImportFrom))
    for alias in k.names
} | {
    k.module for k in ast.walk(af_baum)
    if isinstance(k, ast.ImportFrom) and k.module
}
af_aufrufe = {
    n.func.id for n in ast.walk(af_baum)
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
}

gate_namen = {"require_check_access", "require_kaufcheck_access",
              "require_verkaufscheck_access", "gutschrift"}
check("P: AutoFinder importiert kein Check-Gate und keine Gutschrift",
      not (gate_namen & af_importiert) and "app.check_gate" not in af_importiert)
check("P: AutoFinder ruft kein Check-Gate auf",
      not (gate_namen & af_aufrufe))
check("P: AutoFinder behaelt seinen serverseitigen Missbrauchs-Deckel (Rate-Limit)",
      "@limiter.limit(_AUTOFINDER_RATE_LIMIT)" in af_quelle)
check("P: AutoFinder zaehlt nicht gegen das Chat-Tageslimit",
      "app.usage_limit" not in af_importiert)

# ── Ergebnis ─────────────────────────────────────────────────────────────────
print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER:")
    for f in FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("Alle Limit-Tests bestanden.")
