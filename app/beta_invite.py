"""
Closed-Beta-Einladungen (Release-Schritt 10).

WOFUER
------
Der Gruender laedt eine Handvoll namentlich bekannter Testerinnen und Tester
ein. Jede Einladung ist ein persoenlicher Einmal-Link an GENAU EINE
E-Mail-Adresse und schaltet nach Anmeldung ein festes Paket frei:

    1 KaufCheck + 1 VerkaufsCheck

Mehr nicht. AutoFinder (5/Monat) und KI-Chat (20/Monat) laufen im normalen
KOSTENLOSEN Kontingent, Autokosten ist ohnehin unbegrenzt — dafuer braucht es
keine Freischaltung. Plus wird NICHT verschenkt: ein geschenktes Abo wuerde
genau die Frage unbeantwortet lassen, die die Beta klaeren soll, naemlich ob
Menschen das Produkt zu diesen Konditionen wollen.

WAS DAS HIER NICHT IST
----------------------
Kein oeffentliches Invite-Programm, kein Referral, kein Gutschein- oder
Promo-Code-System. Es gibt keinen Endpunkt, der Einladungen erzeugt — das
passiert ausschliesslich ueber das interne Werkzeug
`scripts/beta_invite_tool.py`. Oeffentlich erreichbar ist nur das EINLOESEN,
und das verlangt sowohl ein Login als auch den Token aus der Mail.

SICHERHEITSMODELL (identisch zur E-Mail-Verifikation, P2-5)
-----------------------------------------------------------
* Token: `secrets.token_urlsafe(32)` — 256 Bit Zufall, nicht erratbar.
* Gespeichert wird NUR `sha256(token)`. Wer die Datenbank liest, haelt keinen
  nutzbaren Link in der Hand.
* Einmal einloesbar, zeitlich begrenzt (Standard 14 Tage).
* An die normalisierte E-Mail gebunden: nur ein Konto mit exakt dieser Adresse
  kann einloesen. Die eingeladene Adresse wird dabei NIE nach aussen gegeben.
* Der Link traegt den Token im Fragment (`/beta#token=...`) — Fragmente
  schickt der Browser nie an einen Server, der Token steht damit in keinem
  Access-Log. Das ist dieselbe Loesung wie beim Bestaetigungslink
  (`app/mailer.py::bestaetigungslink`).

BESTAETIGTE ADRESSE IST PFLICHT
--------------------------------
Eingeloest wird nur, wenn `users.email_verified` gesetzt ist. Die Einladung
haengt an einer ADRESSE — dass jemand den Link oeffnet, beweist aber nur, dass
er den Link hat. Ein weitergeleiteter Link plus ein selbst angelegtes Konto mit
derselben Adresse wuerde ohne diese Pruefung genuegen. Die Bestaetigung ist der
einzige Beleg, dass Konto und eingeladenes Postfach dieselbe Person sind.

Gelesen, aber nie GESCHRIEBEN: `email_verified` wird hier ausschliesslich
geprueft. Bestaetigt wird eine Adresse weiterhin nur ueber den regulaeren Weg
(`app/routers/user_auth.py`), die Einladung baut daran keine Abkuerzung.

Eine noch unbestaetigte Adresse ist kein Fehlschlag, sondern ein Zwischenstand:
die Einladung bleibt offen, es wird nichts beansprucht und nichts teilweise
gutgeschrieben. Nach der Bestaetigung funktioniert derselbe Link unveraendert.

EXACTLY-ONCE
------------
Anspruch und Gutschrift passieren in EINER Transaktion (siehe `loese_ein`).
Doppelklick, F5, zwei Tabs oder ein Retry koennen damit kein zweites Paket
erzeugen; ein Fehler bei der zweiten Gutschrift rollt auch den Anspruch
zurueck, statt eine verbrauchte Einladung mit halbem Paket zu hinterlassen.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app import config
from app.check_gate import gutschrift_in_transaktion
from app.database import get_conn

log = logging.getLogger(__name__)

# Paketkennung in der DB-Zeile. Der INHALT steht bewusst nur hier im Code —
# eine zweite Welle mit anderem Inhalt braucht eine eigene Kennung, damit eine
# bereits eingeloeste Adresse nicht stillschweigend ein zweites Mal kassiert.
PAKET_V1 = "beta_v1"

# Das Paket selbst: Produktschluessel -> Anzahl. Die Schluessel sind exakt die,
# die `app/check_gate.py::gutschrift_in_transaktion` kennt (Whitelist dort).
PAKET_INHALT: dict[str, int] = {"kaufcheck": 1, "verkaufscheck": 1}

# 14 Tage: lang genug, dass eine Einladung auch ueber ein Wochenende oder einen
# Urlaub hinweg gueltig bleibt, kurz genug, dass ein vergessener Link nicht
# monatelang scharf bleibt. Die E-Mail-Verifikation nutzt 48 Stunden — das ist
# dort richtig (Sekunden nach der Registrierung erwartet), hier waere es zu
# knapp: der Tester bekommt die Mail nicht auf ein Ereignis hin, das er gerade
# selbst ausgeloest hat.
GUELTIG_TAGE = 14

_SQL_ZEIT = "%Y-%m-%d %H:%M:%S"


# ── Ergebnis des Einloesens ──────────────────────────────────────────────────

# Genau ein Ergebnis je Versuch. Die Trennung zwischen "schon aktiviert" und
# "nicht verwendbar" ist bewusst: den freundlichen Hinweis bekommt nur, wer die
# Einladung SELBST eingeloest hat — fuer alle anderen Faelle (falsches Konto,
# abgelaufen, unbekannt, entwertet) gibt es eine einzige, nichtssagende
# Antwort. Sonst waere der Endpunkt ein Orakel: "gehoert diese Adresse zur
# Beta?" oder "existiert dieser Token?" duerfen von aussen nicht unterscheidbar
# sein.
AKTIVIERT = "aktiviert"
BEREITS_AKTIVIERT = "bereits_aktiviert"
NICHT_VERWENDBAR = "nicht_verwendbar"
# Adresse stimmt, ist aber noch nicht bestaetigt. Eigener Status, KEIN
# Sicherheitsleck: er wird nur an ein angemeldetes Konto ausgegeben, dessen
# eigene Adresse exakt der eingeladenen entspricht. Wer hier landet, weiss also
# ohnehin schon alles, was die Antwort verraet — und braucht einen Weg heraus,
# statt einer Sackgasse ("nicht verwendbar").
EMAIL_UNBESTAETIGT = "email_unbestaetigt"


@dataclass
class Einloesung:
    status: str
    kaufchecks: int = 0
    verkaufschecks: int = 0


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _jetzt() -> str:
    return datetime.now(timezone.utc).strftime(_SQL_ZEIT)


def normalisiere_email(email: str) -> str:
    """Kanonische Form — identisch zu `user_auth._pruefe_email`.

    Bewusst NUR `strip().lower()`: keine Gmail-Punkte entfernen, kein
    `+alias` umschreiben, keine Anbieter gleichsetzen. Die Einladung muss
    exakt zu der Adresse passen, unter der das Konto in `users.email` steht —
    jede eigene Zusatzregel hier wuerde genau diese Gleichheit zerstoeren.
    """
    return email.strip().lower()


def einladungslink(token: str) -> str:
    """Persoenlicher Link. Token im Fragment: kommt nie am Server an."""
    return f"{config.FRONTEND_URL.rstrip('/')}/beta#token={token}"


# ── Erstellen (nur internes Werkzeug) ────────────────────────────────────────

class EinladungAbgelehnt(Exception):
    """Erstellen nicht moeglich — Grund steht im Text (nur fuer die Konsole)."""


class _AnspruchZuruecknehmen(Exception):
    """Interner Abbruch NACH dem Anspruch.

    Ab dem Moment, in dem `eingeloest_at` gesetzt ist, darf `loese_ein` nicht
    mehr per `return` verlassen werden: `get_conn()` committet am Ende des
    `with`-Blocks jeden normalen Austritt — die Einladung waere verbraucht und
    die Credits fehlten. Nur eine Ausnahme loest den Rollback aus.
    """


def erzeuge_einladung(email: str, *, gueltig_tage: int = GUELTIG_TAGE,
                      paket: str = PAKET_V1) -> str:
    """Erzeugt eine Einladung und gibt den ROHEN Token zurueck.

    Regeln (Welle 1: genau EIN Paket pro Adresse):
      * Wurde fuer diese Adresse bereits ein Paket EINGELOEST, wird abgelehnt.
        Ein zweites Paket muss eine bewusste Entscheidung mit eigener
        Paketkennung sein, kein Nebeneffekt eines erneuten Aufrufs.
      * Noch offene Einladungen derselben Adresse werden entwertet. Damit
        ersetzt eine Neuausstellung einen verlorenen Link sauber, statt zwei
        gueltige Links nebeneinander stehen zu lassen.

    Der rohe Token existiert nur als Rueckgabewert und danach im Link bzw. in
    der Mail — er wird nirgends gespeichert und nirgends geloggt.
    """
    ziel = normalisiere_email(email)
    if "@" not in ziel or "." not in ziel.split("@")[-1]:
        raise EinladungAbgelehnt(f"Keine gueltige E-Mail-Adresse: {email!r}")

    token = secrets.token_urlsafe(32)
    ablauf = (datetime.now(timezone.utc) + timedelta(days=gueltig_tage)).strftime(_SQL_ZEIT)

    with get_conn() as conn:
        schon = conn.execute(
            "SELECT 1 FROM beta_invite WHERE email=? AND paket=? AND eingeloest_at IS NOT NULL",
            (ziel, paket),
        ).fetchone()
        if schon:
            raise EinladungAbgelehnt(
                f"Fuer diese Adresse wurde das Paket {paket!r} bereits eingeloest. "
                "Eine zweite Ausstellung wuerde ein zweites Paket ermoeglichen."
            )
        conn.execute(
            "UPDATE beta_invite SET entwertet_at = CURRENT_TIMESTAMP "
            "WHERE email=? AND paket=? AND eingeloest_at IS NULL AND entwertet_at IS NULL",
            (ziel, paket),
        )
        conn.execute(
            "INSERT INTO beta_invite (token_hash, email, paket, laeuft_ab_at) VALUES (?,?,?,?)",
            (_token_hash(token), ziel, paket, ablauf),
        )
        conn.commit()

    log.info("beta_invite_created paket=%s gueltig_bis=%s", paket, ablauf)
    return token


# ── Einloesen (oeffentlicher Endpunkt, Login vorausgesetzt) ──────────────────

def loese_ein(token: str, user_id: int) -> Einloesung:
    """Loest eine Einladung fuer das EINGELOGGTE Konto ein.

    Alles passiert in EINER Transaktion: Anspruch (atomares UPDATE mit
    `rowcount`-Pruefung) und beide Gutschriften. Faellt irgendetwas davon aus,
    rollt `get_conn()` den gesamten Block zurueck — es gibt keinen Zustand
    "Einladung verbraucht, Credits fehlen" und keinen halben Grant.

    Rueckgabe ist absichtlich grob: nur der Fall "dieses Konto hat DIESE
    Einladung bereits eingeloest" wird benannt, alles andere ist
    `NICHT_VERWENDBAR`.
    """
    if not token or len(token) > 512:
        return Einloesung(NICHT_VERWENDBAR)

    try:
        return _loese_ein_transaktion(_token_hash(token), user_id)
    except _AnspruchZuruecknehmen:
        # Der Anspruch wurde zurueckgerollt — die Einladung ist weiterhin offen.
        return Einloesung(NICHT_VERWENDBAR)


def _loese_ein_transaktion(h: str, user_id: int) -> Einloesung:
    """Die eigentliche Transaktion. Eigene Funktion, damit der Rollback-Pfad
    (`_AnspruchZuruecknehmen`) den `with`-Block sicher verlaesst."""
    jetzt = _jetzt()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT email, paket, eingeloest_at, eingeloest_von FROM beta_invite WHERE token_hash=?",
            (h,),
        ).fetchone()
        if row is None:
            log.info("beta_invite_failed grund=unbekannt user_id=%s", user_id)
            return Einloesung(NICHT_VERWENDBAR)

        # Bereits eingeloest? Nur dem eigenen Konto gegenueber benennen.
        if row["eingeloest_at"] is not None:
            if row["eingeloest_von"] is not None and int(row["eingeloest_von"]) == user_id:
                return Einloesung(BEREITS_AKTIVIERT)
            log.info("beta_invite_failed grund=bereits_eingeloest user_id=%s", user_id)
            return Einloesung(NICHT_VERWENDBAR)

        konto = conn.execute(
            "SELECT email, email_verified FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if konto is None:
            return Einloesung(NICHT_VERWENDBAR)

        # Adressabgleich VOR dem Anspruch: ein falsches Konto darf die Einladung
        # nicht verbrauchen (sonst koennte ein Tippfehler beim Login das Paket
        # der eingeladenen Person vernichten).
        if normalisiere_email(konto["email"]) != row["email"]:
            log.info("beta_invite_failed grund=konto_passt_nicht user_id=%s", user_id)
            return Einloesung(NICHT_VERWENDBAR)

        # Bestaetigte Adresse ist Pflicht — ebenfalls VOR dem Anspruch.
        #
        # Die Einladung ist an eine ADRESSE gebunden; dass jemand sie oeffnet,
        # beweist aber nur Zugriff auf den Link, nicht auf das Postfach. Ein
        # weitergeleiteter Link plus ein selbst angelegtes Konto mit derselben
        # Adresse wuerde sonst reichen. Die Bestaetigung ist der einzige Beleg,
        # dass Konto und eingeladenes Postfach wirklich dieselbe Person sind.
        #
        # Die Einladung bleibt dabei unangetastet: kein Anspruch, kein Ablauf,
        # keine Teilgutschrift. Nach der Bestaetigung funktioniert derselbe Link
        # unveraendert weiter, es braucht keine neue Einladung.
        if not konto["email_verified"]:
            log.info("beta_invite_pending grund=email_unbestaetigt user_id=%s", user_id)
            return Einloesung(EMAIL_UNBESTAETIGT)

        # Atomarer Anspruch. Ablauf und Entwertung stehen mit in der
        # WHERE-Klausel, damit zwischen Pruefung und Anspruch keine Luecke
        # entsteht. rowcount != 1 heisst: jemand war schneller oder die
        # Einladung ist nicht mehr gueltig.
        cur = conn.execute(
            "UPDATE beta_invite SET eingeloest_at = CURRENT_TIMESTAMP, eingeloest_von = ? "
            "WHERE token_hash = ? AND eingeloest_at IS NULL AND entwertet_at IS NULL "
            "AND laeuft_ab_at > ?",
            (user_id, h, jetzt),
        )
        if cur.rowcount != 1:
            log.info("beta_invite_failed grund=abgelaufen_oder_entwertet user_id=%s", user_id)
            return Einloesung(NICHT_VERWENDBAR)

        # Welle-1-Regel, zweite Verteidigungslinie: selbst wenn jemals zwei
        # Einladungen derselben Adresse offen stuenden (das Erstellen verhindert
        # es bereits), darf das Paket nur einmal fliessen. Die Pruefung liegt
        # NACH dem Anspruch, damit sie den frisch geschriebenen Zustand mitsieht;
        # schlaegt sie an, rollt das `raise` den gesamten Block zurueck.
        doppelt = conn.execute(
            "SELECT 1 FROM beta_invite WHERE email=? AND paket=? AND token_hash<>? "
            "AND eingeloest_at IS NOT NULL",
            (row["email"], row["paket"], h),
        ).fetchone()
        if doppelt:
            log.warning("beta_invite_failed grund=paket_bereits_vergeben user_id=%s", user_id)
            raise _AnspruchZuruecknehmen

        for produkt, anzahl in PAKET_INHALT.items():
            gutschrift_in_transaktion(conn, user_id, produkt, anzahl)

    log.info("beta_invite_redeemed paket=%s user_id=%s", row["paket"], user_id)
    return Einloesung(
        AKTIVIERT,
        kaufchecks=PAKET_INHALT["kaufcheck"],
        verkaufschecks=PAKET_INHALT["verkaufscheck"],
    )
