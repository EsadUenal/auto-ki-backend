"""
Tägliche Nutzungsgrenzen für kostenlose Funktionen (Consumer V1).

ZWECK
-----
Kostenkontrolle und Missbrauchsschutz für den kostenlosen KI-Chat — NICHT
Schikane für normale Nutzung. Die Grenze steht an EINER Stelle
(`app.config.CHAT_FREE_LIMIT_TAEGLICH`); dieses Modul kapselt Zählung und
Reset-Semantik, damit im restlichen Code keine Zahlen und keine Datums-Logik
verstreut liegen.

WARUM NICHT NUR DAS BESTEHENDE RATE-LIMIT
-----------------------------------------
`app.rate_limit` (slowapi) begrenzt Anfragen PRO MINUTE pro IP. Das bremst
Lastspitzen, deckelt aber keinen Dauerabruf: 20/Minute sind über einen Tag
hinweg 28.800 Anfragen. Für ein kostenlos nutzbares LLM-Feature braucht es
zusätzlich eine Tagesgrenze. Beide ergänzen sich und ersetzen sich nicht.

SCHLÜSSEL
---------
Der Chat-Endpunkt verlangt historisch KEINEN Login (nur den API-Key), das
Frontend guardet die Route lediglich. Damit die Grenze trotzdem greift, wird
per Nutzer gezählt, wenn ein gültiges Auth-Cookie vorliegt, sonst per IP:

    'user:<id>'   eingeloggt   — folgt dem Konto über Geräte/IP-Wechsel hinweg
    'ip:<adresse>' anonym      — bester verfügbarer Anker ohne Konto

RESET
-----
UTC-Kalendertag. Bewusst kein gleitendes 24-h-Fenster: der Reset-Zeitpunkt ist
für den Nutzer damit benennbar ("morgen wieder") und braucht keine
Countdown-Logik in der UI. Der Tag ist Teil des Primärschlüssels — ein neuer
Tag ist automatisch ein neuer Zähler, es läuft kein Aufräum-Job dagegen.

ATOMARITÄT
----------
Hochzählen und Prüfen passieren in EINER SQL-Anweisung (UPSERT mit
`WHERE anzahl < limit`). Ein „lesen, prüfen, dann schreiben" wäre zwischen zwei
parallelen Requests umgehbar — beide läsen denselben Wert und beide kämen
durch.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import HTTPException, Request

from app.config import CHAT_FREE_LIMIT_TAEGLICH
from app.database import get_conn

log = logging.getLogger(__name__)

ART_CHAT = "chat"
# Rueckfragen zu einer bereits bezahlten Check-Analyse zaehlen in einen EIGENEN
# Topf: sie gehoeren zum gekauften Produkt und duerfen das kostenlose
# Chat-Kontingent nicht aufbrauchen (und umgekehrt). Gleiche Hoehe, getrennte
# Buchhaltung — ein Missbrauchsdeckel bleibt damit auf beiden Wegen bestehen.
ART_ANALYSE_FRAGE = "analyse_frage"


def heute_utc() -> str:
    """Aktueller UTC-Kalendertag als 'YYYY-MM-DD' (Reset-Grenze)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _schluessel(request: Request) -> str:
    """Zähl-Anker: Konto wenn erkennbar, sonst IP."""
    user_id = _user_id_aus_cookie(request)
    if user_id is not None:
        return f"user:{user_id}"
    client = getattr(request, "client", None)
    return f"ip:{getattr(client, 'host', None) or 'unbekannt'}"


def _user_id_aus_cookie(request: Request) -> int | None:
    """Liest die user_id aus dem Auth-Cookie — ohne Login zu erzwingen.

    Ein fehlendes oder ungültiges Cookie ist hier KEIN Fehler: der Endpunkt ist
    auch anonym nutzbar, dann greift die IP-Zählung.
    """
    token = request.cookies.get("auth_token")
    if not token:
        return None
    try:
        from app.routers.user_auth import _decode_token
        return int(_decode_token(token)["sub"])
    except Exception:
        return None


def _hat_abo(user_id: int) -> bool:
    """True, wenn der Nutzer ein laufendes Legacy-Abo besitzt.

    Zahlende Bestandskunden hatten den Chat bisher ohne Tagesgrenze. Sie hier
    nachträglich zu deckeln wäre eine Leistungskürzung an einem bestehenden
    Vertrag — deshalb bleiben sie ausgenommen.
    """
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT abo_typ FROM users WHERE id=?", (user_id,)).fetchone()
        return bool(row) and row["abo_typ"] != "none"
    except Exception:
        log.exception("Abo-Prüfung für Tageslimit fehlgeschlagen (user_id=%s)", user_id)
        return False


def verbrauche(schluessel: str, art: str, limit: int, tag: str | None = None) -> bool:
    """Zählt eine Nutzung. True = erlaubt, False = Tagesgrenze erreicht.

    limit <= 0 deaktiviert die Grenze (dann wird auch nicht gezählt).
    """
    if limit <= 0:
        return True
    tag = tag or heute_utc()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO usage_taeglich (schluessel, art, tag_utc, anzahl) VALUES (?,?,?,1) "
            "ON CONFLICT(schluessel, art, tag_utc) DO UPDATE SET anzahl = anzahl + 1 "
            "WHERE anzahl < ?",
            (schluessel, art, tag, limit),
        )
        conn.commit()
    return cur.rowcount == 1


def stand(schluessel: str, art: str, tag: str | None = None) -> int:
    """Aktueller Zählerstand (für Tests/Diagnose)."""
    tag = tag or heute_utc()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT anzahl FROM usage_taeglich WHERE schluessel=? AND art=? AND tag_utc=?",
            (schluessel, art, tag),
        ).fetchone()
    return row["anzahl"] if row else 0


ANALYSE_LIMIT_NACHRICHT = (
    "Du hast heute sehr viele Rückfragen gestellt. "
    "Morgen kannst du wieder weiterfragen."
)

CHAT_LIMIT_NACHRICHT = (
    "Dein kostenloses Tageslimit für den KI-Chat ist erreicht. "
    "Morgen kannst du wieder weiterfragen."
)


def require_chat_kontingent(request: Request) -> None:
    """Erzwingt die Chat-Tagesgrenze. Wirft HTTP 429 mit strukturiertem Fehler.

    Reihenfolge: Abo-Kunden sind ausgenommen, alle anderen zählen gegen
    `CHAT_FREE_LIMIT_TAEGLICH`.
    """
    user_id = _user_id_aus_cookie(request)
    if user_id is not None and _hat_abo(user_id):
        return

    schluessel = f"user:{user_id}" if user_id is not None else _schluessel(request)
    if verbrauche(schluessel, ART_CHAT, CHAT_FREE_LIMIT_TAEGLICH):
        return

    raise HTTPException(
        status_code=429,
        detail={
            "fehler": {
                "code": "tageslimit_erreicht",
                "nachricht": CHAT_LIMIT_NACHRICHT,
            }
        },
    )


def require_analyse_frage_kontingent(request: Request) -> None:
    """Erzwingt die Tagesgrenze für Rückfragen zu einer Check-Analyse.

    Eigener Zähler (`ART_ANALYSE_FRAGE`), damit das kostenlose Chat-Kontingent
    unberührt bleibt. Abo-Kunden sind wie beim Chat ausgenommen.
    """
    user_id = _user_id_aus_cookie(request)
    if user_id is not None and _hat_abo(user_id):
        return

    schluessel = f"user:{user_id}" if user_id is not None else _schluessel(request)
    if verbrauche(schluessel, ART_ANALYSE_FRAGE, CHAT_FREE_LIMIT_TAEGLICH):
        return

    raise HTTPException(
        status_code=429,
        detail={
            "fehler": {
                "code": "tageslimit_erreicht",
                "nachricht": ANALYSE_LIMIT_NACHRICHT,
            }
        },
    )
