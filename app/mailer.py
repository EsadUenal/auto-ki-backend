"""
E-Mail-Versand fuer Bestaetigungslinks (P2-5, Deployment-Block).

Anbieterneutral per SMTP (Standardbibliothek, keine neue Abhaengigkeit). Welcher
Anbieter dahinter steht, ist reine Konfiguration (AUTO_KI_SMTP_*).

Regeln:
- Verschluesselt oder gar nicht: Port 465 = implizites TLS, sonst STARTTLS mit
  Zertifikatspruefung. Bietet der Server kein STARTTLS an, wird NICHT im Klartext
  gesendet.
- Der Token steht nur im Link, nie im Log. Die Empfaengeradresse steht ebenfalls
  nicht im Log (personenbezogen) — der Aufrufer loggt die user_id.
- Der Link traegt den Token im Fragment (#token=...). Fragmente schickt der
  Browser nie an einen Server, der Token taucht damit in keinem Access-Log auf.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from app import config

log = logging.getLogger(__name__)

BETREFF_BESTAETIGUNG = "Bitte bestätige deine E-Mail-Adresse für ENFAL"


def bestaetigungslink(token: str) -> str:
    return f"{config.FRONTEND_URL.rstrip('/')}/email-bestaetigen#token={token}"


def _bestaetigungsmail(empfaenger: str, token: str) -> EmailMessage:
    link = bestaetigungslink(token)
    stunden = config.EMAIL_VERIFIKATION_GUELTIG_STUNDEN
    msg = EmailMessage()
    msg["Subject"] = BETREFF_BESTAETIGUNG
    msg["From"] = config.MAIL_FROM
    msg["To"] = empfaenger
    msg.set_content(
        "Hallo,\n\n"
        "bitte bestätige deine E-Mail-Adresse für dein ENFAL-Konto:\n\n"
        f"{link}\n\n"
        f"Der Link ist {stunden} Stunden gültig und kann einmal verwendet werden.\n"
        "Wenn du dich nicht bei ENFAL registriert hast, kannst du diese Nachricht ignorieren.\n\n"
        "ENFAL\n"
    )
    return msg


def _senden(msg: EmailMessage) -> None:
    kontext = ssl.create_default_context()
    if config.SMTP_PORT == 465:
        with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT,
                              timeout=config.SMTP_TIMEOUT_SECONDS, context=kontext) as smtp:
            if config.SMTP_USER:
                smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(msg)
        return
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=config.SMTP_TIMEOUT_SECONDS) as smtp:
        smtp.ehlo()
        # starttls() wirft SMTPNotSupportedError, wenn der Server kein STARTTLS
        # anbietet — gewollt: kein Klartext-Fallback.
        smtp.starttls(context=kontext)
        smtp.ehlo()
        if config.SMTP_USER:
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.send_message(msg)


def sende_bestaetigungsmail(empfaenger: str, token: str, *, user_id: int | None = None) -> bool:
    """Verschickt den Bestaetigungslink. True = vom SMTP-Server angenommen.

    Wirft nie: ein Mailproblem darf weder die Registrierung noch den Server
    stoeren. Der Grund landet (ohne Adresse, ohne Token) im Log.
    """
    if not config.MAIL_AKTIV:
        log.error("Bestaetigungsmail NICHT versendet (user_id=%s): kein SMTP konfiguriert "
                  "(AUTO_KI_SMTP_HOST / AUTO_KI_MAIL_FROM).", user_id)
        return False
    try:
        _senden(_bestaetigungsmail(empfaenger, token))
    except Exception as exc:
        log.error("Bestaetigungsmail fehlgeschlagen (user_id=%s): %s", user_id, type(exc).__name__)
        return False
    log.info("Bestaetigungsmail versendet (user_id=%s).", user_id)
    return True
