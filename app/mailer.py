"""
E-Mail-Versand fuer Bestaetigungslinks (P2-5, Deployment-Block).

Ueber die Brevo-Transaktionsmail-API per HTTPS (Port 443), nicht per SMTP.
Grund: Railway blockiert auf dem laufenden Plan (Hobby) jeden ausgehenden
SMTP-Port (25, 465, 587, 2525) netzwerkseitig — gemessen per Direktverbindung
aus dem laufenden Container (DNS ok, jeder SMTP-Port TimeoutError, HTTPS auf
demselben Host sofort erreichbar). SMTP ist auf dieser Plattform/diesem Plan
technisch nicht nutzbar; die HTTPS-API des ohnehin eingerichteten Anbieters
Brevo ist der funktionierende Weg, ohne den Railway-Plan zu wechseln.

Regeln:
- Der Token steht nur im Link, nie im Log. Die Empfaengeradresse steht ebenfalls
  nicht im Log (personenbezogen) — der Aufrufer loggt die user_id.
- Der Link traegt den Token im Fragment (#token=...). Fragmente schickt der
  Browser nie an einen Server, der Token taucht damit in keinem Access-Log auf.
- Der API-Key steht nur im Request-Header, nie im geloggten Body.
"""
from __future__ import annotations

import logging
from email.utils import parseaddr

import httpx

from app import config

log = logging.getLogger(__name__)

BETREFF_BESTAETIGUNG = "Bitte bestätige deine E-Mail-Adresse für ENFAL"
BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


def bestaetigungslink(token: str) -> str:
    return f"{config.FRONTEND_URL.rstrip('/')}/email-bestaetigen#token={token}"


def _bestaetigungstext(token: str) -> str:
    link = bestaetigungslink(token)
    stunden = config.EMAIL_VERIFIKATION_GUELTIG_STUNDEN
    return (
        "Hallo,\n\n"
        "bitte bestätige deine E-Mail-Adresse für dein ENFAL-Konto:\n\n"
        f"{link}\n\n"
        f"Der Link ist {stunden} Stunden gültig und kann einmal verwendet werden.\n"
        "Wenn du dich nicht bei ENFAL registriert hast, kannst du diese Nachricht ignorieren.\n\n"
        "ENFAL\n"
    )


def _sende_ueber_brevo(empfaenger: str, betreff: str, text: str) -> None:
    absender_name, absender_mail = parseaddr(config.MAIL_FROM)
    sender: dict[str, str] = {"email": absender_mail or config.MAIL_FROM}
    if absender_name:
        sender["name"] = absender_name
    payload = {
        "sender": sender,
        "to": [{"email": empfaenger}],
        "subject": betreff,
        "textContent": text,
    }
    with httpx.Client(timeout=config.BREVO_TIMEOUT_SECONDS) as client:
        antwort = client.post(
            BREVO_API_URL,
            headers={"api-key": config.BREVO_API_KEY, "accept": "application/json"},
            json=payload,
        )
        antwort.raise_for_status()


def sende_bestaetigungsmail(empfaenger: str, token: str, *, user_id: int | None = None) -> bool:
    """Verschickt den Bestaetigungslink ueber Brevo. True = von Brevo angenommen.

    Wirft nie: ein Mailproblem darf weder die Registrierung noch den Server
    stoeren. Der Grund landet (ohne Adresse, ohne Token) im Log.
    """
    if not config.MAIL_AKTIV:
        log.error("Bestaetigungsmail NICHT versendet (user_id=%s): kein Mailversand konfiguriert "
                  "(AUTO_KI_BREVO_API_KEY / AUTO_KI_MAIL_FROM).", user_id)
        return False
    try:
        _sende_ueber_brevo(empfaenger, BETREFF_BESTAETIGUNG, _bestaetigungstext(token))
    except Exception as exc:
        log.error("Bestaetigungsmail fehlgeschlagen (user_id=%s): %s", user_id, type(exc).__name__)
        return False
    log.info("Bestaetigungsmail versendet (user_id=%s).", user_id)
    return True
