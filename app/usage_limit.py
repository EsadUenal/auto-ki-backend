"""
Monatliche Nutzungsgrenzen für KI-Chat und AutoFinder (Consumer Pricing V1 FINAL).

WARUM MONAT STATT TAG
---------------------
Die Vorgängerfassung zählte pro UTC-Kalendertag. Das deckelte zwar Missbrauch,
passte aber nicht zum Produkt: verkauft wird in Monatskontingenten
("20 Nachrichten im Monat", "100 im Plus"), und ein Tageslimit sagt dem Nutzer
über seinen Tarif nichts. Zählung, Anzeige und Abrechnung sprechen jetzt
dieselbe Sprache.

Ein Tageslimit war zudem wirtschaftlich falsch kalibriert: 20 Nachrichten pro
TAG sind rund 600 pro Monat — ein Vielfaches dessen, was ein Abo trägt.

RESET
-----
UTC-Kalendermonat (`YYYY-MM`). Bewusst NICHT der Stripe-Abrechnungszeitraum:
für Free-Nutzer gäbe es keinen, und für Plus-Nutzer läge die Grenze je nach
Kaufdatum anders — beides wäre für den Nutzer schwer zu erklären. Die
CHECK-Kontingente von Plus folgen dagegen sehr wohl dem Stripe-Zeitraum
(`app/plus.py`); dort ist es die bezahlte Leistung, hier nur ein Deckel.
Der Monat ist Teil des Primärschlüssels — ein neuer Monat ist automatisch ein
neuer Zähler, es braucht keinen Aufräum-Job.

SCHLÜSSEL
---------
`/chat` und `/autofinder` verlangen historisch KEINEN Login. Damit die Grenze
trotzdem greift, wird per Konto gezählt, wenn ein gültiges Auth-Cookie vorliegt,
sonst per IP:

    'user:<id>'    eingeloggt — folgt dem Konto über Geräte/IP-Wechsel hinweg
    'ip:<adresse>' anonym     — bester verfügbarer Anker ohne Konto

Bewusst KEIN Fingerprinting: die IP ist ein Soft-Abuse-Key, kein
Identitätsmerkmal. Wer sie wechselt, umgeht das anonyme Limit — das ist
akzeptiert, weil AutoFinder ohne Login ausprobierbar bleiben soll und der
eigentliche Wert (gespeicherte Verläufe, Checks) ohnehin am Konto hängt.

WAS DIE IP NICHT DARF: EIN MONATSKONTINGENT TRAGEN
--------------------------------------------------
Für AutoFinder wurde anonym ursprünglich dasselbe Monatskontingent (5) am
IP-Anker gezählt. Das war falsch: hinter Buero-NAT, Schul-/Hotel-WLAN oder
Mobilfunk-CGNAT teilen sich beliebig viele Menschen eine Adresse. Fünf Suchen
pro MONAT sind dort nach kurzer Zeit aufgebraucht, und der Ausprobier-Pfad ohne
Login ist für alle dauerhaft zu — obwohl niemand von ihnen auch nur eine eigene
Suche hatte.

Anonym gilt deshalb: EINE Demo-Suche pro UTC-TAG und IP, in einem eigenen Topf
(`ART_AUTOFINDER_DEMO`, Tabelle `usage_taeglich`). Ein täglicher Zugang
regeneriert sich von selbst — geteilte Adressen bleiben nutzbar, Dauerabruf
über dieselbe Adresse bleibt gedeckelt.

Das Free-Kontingent (5/Monat) hängt ausschliesslich am KONTO. Beide Töpfe sind
getrennt: wer seine anonyme Demo verbraucht hat und sich danach registriert,
startet mit vollen 5 Suchen. Die Demo war kein Vorschuss darauf.

ATOMARITÄT
----------
Hochzählen und Prüfen passieren in EINER SQL-Anweisung (UPSERT mit
`WHERE anzahl < limit`). Ein „lesen, prüfen, dann schreiben" wäre zwischen zwei
parallelen Requests umgehbar.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import HTTPException, Request

from app import plus
from app.config import (
    AUTOFINDER_ANONYM_DEMO_PRO_TAG,
    AUTOFINDER_FREE_LIMIT_MONATLICH,
    AUTOFINDER_PLUS_LIMIT_MONATLICH,
    CHAT_FREE_LIMIT_MONATLICH,
    CHAT_PLUS_LIMIT_MONATLICH,
)
from app.database import get_conn

log = logging.getLogger(__name__)

ART_CHAT = "chat"
ART_AUTOFINDER = "autofinder"
# Rueckfragen zu einer bereits bezahlten Check-Analyse zaehlen in einen EIGENEN
# Topf: sie gehoeren zum gekauften Produkt und duerfen das kostenlose
# Chat-Kontingent nicht aufbrauchen (und umgekehrt).
ART_ANALYSE_FRAGE = "analyse_frage"
# AutoFinder OHNE Login. EIGENE Art und EIGENE Tabelle (usage_taeglich), damit
# der Demo-Zaehler und das Konto-Monatskontingent sich niemals beruehren: wer
# seine anonyme Demo verbraucht hat und sich danach registriert, startet mit
# vollen 5 Suchen — die Demo war kein Vorschuss darauf.
ART_AUTOFINDER_DEMO = "autofinder_demo"


def monat_utc() -> str:
    """Aktueller UTC-Kalendermonat als 'YYYY-MM' (Reset-Grenze)."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def tag_utc() -> str:
    """Aktueller UTC-Kalendertag als 'YYYY-MM-DD' (Reset-Grenze der Demo)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _user_id_aus_cookie(request: Request) -> int | None:
    """Liest die user_id aus dem Auth-Cookie — ohne Login zu erzwingen."""
    token = request.cookies.get("auth_token")
    if not token:
        return None
    try:
        from app.routers.user_auth import _decode_token
        return int(_decode_token(token)["sub"])
    except Exception:
        return None


def _schluessel(request: Request, user_id: int | None) -> str:
    if user_id is not None:
        return f"user:{user_id}"
    client = getattr(request, "client", None)
    return f"ip:{getattr(client, 'host', None) or 'unbekannt'}"


def _nutzer_zustand(user_id: int | None) -> tuple[bool, bool]:
    """(hat_legacy_abo, plus_aktiv) — beide sind von der Free-Grenze ausgenommen."""
    if user_id is None:
        return False, False
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT abo_typ, plus_period_end FROM users WHERE id=?", (user_id,)
            ).fetchone()
        if not row:
            return False, False
        return row["abo_typ"] != "none", plus.ist_aktiv(row)
    except Exception:
        log.exception("Tarifprüfung für Monatslimit fehlgeschlagen (user_id=%s)", user_id)
        return False, False


def verbrauche(schluessel: str, art: str, limit: int, monat: str | None = None) -> bool:
    """Zählt eine Nutzung. True = erlaubt, False = Monatsgrenze erreicht.

    limit <= 0 deaktiviert die Grenze (dann wird auch nicht gezählt).
    """
    if limit <= 0:
        return True
    monat = monat or monat_utc()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO usage_monat (schluessel, art, monat_utc, anzahl) VALUES (?,?,?,1) "
            "ON CONFLICT(schluessel, art, monat_utc) DO UPDATE SET anzahl = anzahl + 1 "
            "WHERE anzahl < ?",
            (schluessel, art, monat, limit),
        )
        conn.commit()
    return cur.rowcount == 1


def verbrauche_tag(schluessel: str, art: str, limit: int, tag: str | None = None) -> bool:
    """Wie `verbrauche`, aber je UTC-TAG — Tabelle `usage_taeglich`.

    Ausschliesslich fuer den anonymen AutoFinder-Demo-Zugang. Getrennte Tabelle
    statt eines Tageswerts in `usage_monat`: dort heisst die Spalte `monat_utc`
    und traegt einen Monat. Ein Tagesdatum hineinzuschreiben wuerde zwar
    funktionieren, aber jeden spaeteren Leser in die Irre fuehren.
    `usage_taeglich` existiert bereits im Schema (siehe app/database.py) und
    wurde seit der Monatsumstellung nicht mehr beschrieben — es braucht also
    weder eine Migration noch ein neues Zeitmodell.
    """
    if limit <= 0:
        return True
    tag = tag or tag_utc()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO usage_taeglich (schluessel, art, tag_utc, anzahl) VALUES (?,?,?,1) "
            "ON CONFLICT(schluessel, art, tag_utc) DO UPDATE SET anzahl = anzahl + 1 "
            "WHERE anzahl < ?",
            (schluessel, art, tag, limit),
        )
        conn.commit()
    return cur.rowcount == 1


def stand_tag(schluessel: str, art: str, tag: str | None = None) -> int:
    """Aktueller Zaehlerstand des Tages (Demo)."""
    tag = tag or tag_utc()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT anzahl FROM usage_taeglich WHERE schluessel=? AND art=? AND tag_utc=?",
            (schluessel, art, tag),
        ).fetchone()
    return row["anzahl"] if row else 0


def stand(schluessel: str, art: str, monat: str | None = None) -> int:
    """Aktueller Zählerstand des Monats."""
    monat = monat or monat_utc()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT anzahl FROM usage_monat WHERE schluessel=? AND art=? AND monat_utc=?",
            (schluessel, art, monat),
        ).fetchone()
    return row["anzahl"] if row else 0


def limit_fuer(art: str, plus_aktiv: bool) -> int:
    if art == ART_AUTOFINDER:
        return AUTOFINDER_PLUS_LIMIT_MONATLICH if plus_aktiv else AUTOFINDER_FREE_LIMIT_MONATLICH
    return CHAT_PLUS_LIMIT_MONATLICH if plus_aktiv else CHAT_FREE_LIMIT_MONATLICH


def nutzung(user_id: int) -> dict:
    """Verbrauch des laufenden Monats für die Kontoanzeige."""
    _, plus_aktiv = _nutzer_zustand(user_id)
    s = f"user:{user_id}"
    return {
        "chat_genutzt": stand(s, ART_CHAT),
        "chat_limit": limit_fuer(ART_CHAT, plus_aktiv),
        "autofinder_genutzt": stand(s, ART_AUTOFINDER),
        "autofinder_limit": limit_fuer(ART_AUTOFINDER, plus_aktiv),
        "monat": monat_utc(),
    }


# ── Nutzertexte ──────────────────────────────────────────────────────────────
# Bewusst ohne Statuscode, ohne Provider-Namen, ohne "Fehler:" — ein erreichtes
# Kontingent ist ein normaler Produktzustand, kein Defekt.

def _chat_nachricht(plus_aktiv: bool) -> str:
    if plus_aktiv:
        return "Du hast dein monatliches KI-Chat-Kontingent erreicht."
    return (f"Du hast deine {CHAT_FREE_LIMIT_MONATLICH} kostenlosen KI-Chat-Nachrichten "
            f"für diesen Monat genutzt.")


def _autofinder_nachricht(plus_aktiv: bool) -> str:
    """Kontingent-Text für EINGELOGGTE Nutzer. Anonyme bekommen `_wirf_demo`."""
    if plus_aktiv:
        return "Du hast dein monatliches AutoFinder-Kontingent erreicht."
    return (f"Du hast deine {AUTOFINDER_FREE_LIMIT_MONATLICH} kostenlosen AutoFinder-Suchen "
            f"für diesen Monat genutzt.")


def _wirf(code_nachricht: str, plus_aktiv: bool) -> None:
    raise HTTPException(
        status_code=429,
        detail={"fehler": {
            "code": "monatslimit_erreicht",
            "nachricht": code_nachricht,
            # Das Frontend blendet die Plus-CTA nur ein, wenn Plus überhaupt
            # etwas ändern würde.
            "plus_hilft": not plus_aktiv,
        }},
    )


def _wirf_demo() -> None:
    """Verbrauchte anonyme Demo — EIGENER Code, weil der Weg nach vorn ein anderer ist.

    Der Nutzer soll sich hier kostenlos anmelden, nicht ein Abo ansehen: sein
    Free-Kontingent (5/Monat) hat er noch vollstaendig vor sich. Ein
    Plus-Angebot waere an dieser Stelle sachlich falsch und wuerde wie eine
    Bezahlschranke wirken, wo gar keine ist.
    """
    raise HTTPException(
        status_code=429,
        detail={"fehler": {
            "code": "demo_limit_erreicht",
            "nachricht": "Du hast deine kostenlose AutoFinder-Demo genutzt.",
            "hinweis": (f"Melde dich kostenlos an und erhalte "
                        f"{AUTOFINDER_FREE_LIMIT_MONATLICH} AutoFinder-Suchen pro Monat."),
            "anmelden_hilft": True,
            # Plus ist hier NICHT der richtige Hinweis — siehe Docstring.
            "plus_hilft": False,
        }},
    )


def _pruefe(request: Request, art: str) -> None:
    user_id = _user_id_aus_cookie(request)
    hat_legacy_abo, plus_aktiv = _nutzer_zustand(user_id)

    # Zahlende Bestandskunden (light/pro/max) hatten nie eine solche Grenze.
    # Sie nachträglich zu deckeln wäre eine Leistungskürzung am laufenden
    # Vertrag — deshalb bleiben sie ausgenommen.
    if hat_legacy_abo:
        return

    # AutoFinder OHNE Login ist eine Demo, kein Tarif: ein Zaehler am IP-Anker
    # kann kein Monatskontingent abbilden, weil sich hinter einer geteilten
    # Adresse (Buero-NAT, Schul-/Hotel-WLAN, Mobilfunk-CGNAT) beliebig viele
    # Menschen denselben Zaehler teilen. Deshalb hier: EIN Versuch pro Tag und
    # IP, in einem EIGENEN Topf. Das Monatskontingent haengt am Konto und wird
    # unten nur fuer eingeloggte Nutzer geprueft.
    if art == ART_AUTOFINDER and user_id is None:
        if verbrauche_tag(_schluessel(request, None), ART_AUTOFINDER_DEMO,
                          AUTOFINDER_ANONYM_DEMO_PRO_TAG):
            return
        _wirf_demo()

    limit = limit_fuer(art, plus_aktiv)
    if verbrauche(_schluessel(request, user_id), art, limit):
        return

    if art == ART_AUTOFINDER:
        _wirf(_autofinder_nachricht(plus_aktiv), plus_aktiv)
    _wirf(_chat_nachricht(plus_aktiv), plus_aktiv)


def require_chat_kontingent(request: Request) -> None:
    """Erzwingt das monatliche Chat-Kontingent (Free 20, Plus 100)."""
    _pruefe(request, ART_CHAT)


def require_autofinder_kontingent(request: Request) -> None:
    """Erzwingt das AutoFinder-Kontingent.

    Eingeloggt: 5 (Free) bzw. 50 (Plus) pro UTC-Kalendermonat, gezählt
    ausschliesslich am KONTO — ein Wechsel des Netzes oder Geräts ändert daran
    nichts, und eine geteilte IP kostet niemanden sein Kontingent.

    Anonym: EINE Demo-Suche pro UTC-Tag und IP, in einem eigenen Topf. Der
    IP-Anker dient hier nur dem Abuse-Schutz, nicht der Identifikation — kein
    Fingerprinting, und weder Cookie noch localStorage sind Autorität.

    Das bestehende Rate-Limit (20/min) bleibt zusätzlich bestehen — es schützt
    gegen Lastspitzen, dieses Kontingent gegen Dauerabruf.
    """
    _pruefe(request, ART_AUTOFINDER)


def require_analyse_frage_kontingent(request: Request) -> None:
    """Monatsgrenze für Rückfragen zu einer bezahlten Check-Analyse.

    Eigener Topf, damit das kostenlose Chat-Kontingent unberührt bleibt: die
    Rückfragen gehören zum bereits bezahlten Check.
    """
    user_id = _user_id_aus_cookie(request)
    hat_legacy_abo, plus_aktiv = _nutzer_zustand(user_id)
    if hat_legacy_abo or plus_aktiv:
        return
    limit = CHAT_PLUS_LIMIT_MONATLICH   # großzügig: Teil des gekauften Produkts
    if verbrauche(_schluessel(request, user_id), ART_ANALYSE_FRAGE, limit):
        return
    _wirf("Du hast diesen Monat sehr viele Rückfragen gestellt. "
          "Nächsten Monat kannst du wieder weiterfragen.", plus_aktiv)
