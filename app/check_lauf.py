"""
Herkunftsnachweis fuer Check-Laeufe (Security Block 2, P1-4).

WARUM
-----
`POST /checks` speichert, was der Client schickt — Titel, Eingabe, Ergebnis.
Das ist als Verlaufsspeicher richtig, taugt aber nicht als Beleg dafuer, dass je
ein bezahlter Check gelaufen ist: ein Konto ohne jedes Guthaben kann sich einen
"VerkaufsCheck" frei erfinden. Folgewerkzeuge, die daran haengen (heute die
Inserats-Optimierung, ein LLM-Aufruf), waeren damit gratis.

WIE
---
Der Check-Router legt nach einem ERFOLGREICHEN Lauf hier eine Zeile an und gibt
ihre `lauf_id` in der Antwort zurueck. Beim Speichern reicht der Client sie
mit; der Server loest sie GENAU EINMAL ein und vermerkt sie am Check. Die ID ist
damit kein Geheimnis, sondern ein serverseitig ausgestellter, einmal gueltiger
Beleg: Wer sie besitzt, hat den Lauf auch bezahlt.

Bewusst einmalig einloesbar: sonst liesse sich ein einziger bezahlter Lauf an
beliebig viele gespeicherte Checks haengen.

Historische Checks (vor dieser Aenderung gespeichert) haben keinen Nachweis und
bekommen auch keinen — ein Backfill waere eine erfundene Herkunft. Sie lassen
sich weiterhin ansehen, loeschen und befragen; nur die Inserats-Optimierung
verlangt den Beleg.
"""
from __future__ import annotations

import logging
import uuid

from app.database import get_conn

log = logging.getLogger(__name__)

TYPEN = ("kauf", "verkauf")


def erzeuge(user_id: int, typ: str) -> str | None:
    """Stellt nach einem erfolgreichen Check-Lauf einen Nachweis aus.

    Best-effort: schlaegt das Schreiben fehl, bekommt der Nutzer trotzdem seine
    Analyse (er hat dafuer bezahlt). Er kann den Check dann nur ohne Nachweis
    speichern — sichtbar als fehlende Inserats-Optimierung, nie als stiller
    Vollzugriff.
    """
    if typ not in TYPEN:
        raise ValueError(f"Unbekannter Check-Typ: {typ!r}")
    lauf_id = uuid.uuid4().hex
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO check_lauf (lauf_id, user_id, typ) VALUES (?,?,?)",
                (lauf_id, user_id, typ),
            )
            conn.commit()
    except Exception:
        log.exception("Lauf-Nachweis konnte nicht angelegt werden (user_id=%s, typ=%s)", user_id, typ)
        return None
    return lauf_id


def einloese(lauf_id: str | None, user_id: int, typ: str) -> str | None:
    """Loest einen Nachweis ein. Rueckgabe: die ID bei Erfolg, sonst None.

    Atomar ueber `WHERE eingeloest_at IS NULL` — zwei parallele Speichervorgaenge
    mit derselben ID koennen nicht beide gewinnen. Ein fremder, unbekannter,
    bereits verbrauchter oder typfremder Wert fuehrt NICHT zu einem Fehler,
    sondern nur dazu, dass der Check ohne Nachweis gespeichert wird.
    """
    if not lauf_id or typ not in TYPEN:
        return None
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "UPDATE check_lauf SET eingeloest_at = CURRENT_TIMESTAMP "
                "WHERE lauf_id=? AND user_id=? AND typ=? AND eingeloest_at IS NULL",
                (str(lauf_id), user_id, typ),
            )
            conn.commit()
        return str(lauf_id) if cur.rowcount == 1 else None
    except Exception:
        log.exception("Lauf-Nachweis konnte nicht eingeloest werden (user_id=%s)", user_id)
        return None
