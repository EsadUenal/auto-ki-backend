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

import html
import logging
from email.utils import parseaddr

import httpx

from app import config

log = logging.getLogger(__name__)

BETREFF_BESTAETIGUNG = "Bitte bestätige deine E-Mail-Adresse für ENFAL"
BETREFF_BETA_EINLADUNG = "Deine Einladung zur ENFAL Closed Beta"
BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"

# Markenfarbe (Tailwind orange-500, identisch zu den Buttons im Frontend —
# siehe z.B. "bg-orange-500" in LoginView.tsx). Kein Logo-Bild: public/logo.svg
# ist ein reines SVG-Favicon ohne Raster-Export; SVG-<img>/data-URIs werden von
# vielen Mail-Clients (u.a. Outlook) nicht zuverlaessig gerendert. Ein
# Text-Wortmarke in der Markenfarbe ist die sichere Wahl, statt ein neues
# Bild-Asset zu erfinden.
_MARKENFARBE = "#f97316"


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


def _bestaetigungshtml(token: str) -> str:
    """Schlichtes, tabellenbasiertes HTML fuer maximale Mail-Client-Kompatibilitaet
    (Outlook rendert kein Flexbox/Grid und die meisten CSS-Selektoren ausserhalb
    von Inline-Styles nicht zuverlaessig — deshalb Inline-Styles statt <style>,
    <table> statt <div> fuers Layout). Ein CTA-Button, ein Fallback-Link, keine
    Bilder, kein Marketing-Schmuck.
    """
    link = html.escape(bestaetigungslink(token), quote=True)
    stunden = config.EMAIL_VERIFIKATION_GUELTIG_STUNDEN
    return f"""\
<!doctype html>
<html lang="de">
  <body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;padding:32px 16px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background:#ffffff;border-radius:12px;overflow:hidden;">
            <tr>
              <td style="padding:32px 32px 8px 32px;text-align:center;">
                <span style="font-size:22px;font-weight:700;color:{_MARKENFARBE};letter-spacing:0.02em;">ENFAL</span>
              </td>
            </tr>
            <tr>
              <td style="padding:16px 32px 0 32px;color:#111827;font-size:15px;line-height:1.6;">
                <p style="margin:0 0 16px 0;">Hallo,</p>
                <p style="margin:0 0 24px 0;">bitte bestätige deine E-Mail-Adresse für dein ENFAL-Konto.</p>
              </td>
            </tr>
            <tr>
              <td style="padding:0 32px;text-align:center;">
                <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 auto;">
                  <tr>
                    <td style="border-radius:10px;background:{_MARKENFARBE};">
                      <a href="{link}"
                         style="display:inline-block;padding:14px 32px;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:10px;">
                        E-Mail-Adresse bestätigen
                      </a>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:24px 32px 0 32px;color:#6b7280;font-size:13px;line-height:1.6;">
                <p style="margin:0 0 8px 0;">Falls der Button nicht funktioniert, kopiere diesen Link in deinen Browser:</p>
                <p style="margin:0 0 24px 0;word-break:break-all;">
                  <a href="{link}" style="color:{_MARKENFARBE};">{link}</a>
                </p>
              </td>
            </tr>
            <tr>
              <td style="padding:0 32px 32px 32px;color:#9ca3af;font-size:12px;line-height:1.6;border-top:1px solid #f3f4f6;">
                <p style="margin:16px 0 0 0;">Der Link ist {stunden} Stunden gültig und kann einmal verwendet werden.</p>
                <p style="margin:8px 0 0 0;">Wenn du dich nicht bei ENFAL registriert hast, kannst du diese Nachricht ignorieren.</p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


def _sende_ueber_brevo(empfaenger: str, betreff: str, text: str, html_inhalt: str | None = None) -> None:
    absender_name, absender_mail = parseaddr(config.MAIL_FROM)
    sender: dict[str, str] = {"email": absender_mail or config.MAIL_FROM}
    if absender_name:
        sender["name"] = absender_name
    payload: dict[str, object] = {
        "sender": sender,
        "to": [{"email": empfaenger}],
        "subject": betreff,
        "textContent": text,
    }
    if html_inhalt:
        payload["htmlContent"] = html_inhalt
    with httpx.Client(timeout=config.BREVO_TIMEOUT_SECONDS) as client:
        antwort = client.post(
            BREVO_API_URL,
            headers={"api-key": config.BREVO_API_KEY, "accept": "application/json"},
            json=payload,
        )
        antwort.raise_for_status()


def sende_bestaetigungsmail(empfaenger: str, token: str, *, user_id: int | None = None) -> bool:
    """Verschickt den Bestaetigungslink ueber Brevo. True = von Brevo angenommen.

    HTML mit Plain-Text-Fallback (Brevo/der Empfaenger-Client waehlt automatisch
    das passende Format). Wirft nie: ein Mailproblem darf weder die
    Registrierung noch den Server stoeren. Der Grund landet (ohne Adresse, ohne
    Token) im Log.
    """
    if not config.MAIL_AKTIV:
        log.error("Bestaetigungsmail NICHT versendet (user_id=%s): kein Mailversand konfiguriert "
                  "(AUTO_KI_BREVO_API_KEY / AUTO_KI_MAIL_FROM).", user_id)
        return False
    try:
        _sende_ueber_brevo(empfaenger, BETREFF_BESTAETIGUNG, _bestaetigungstext(token), _bestaetigungshtml(token))
    except Exception as exc:
        log.error("Bestaetigungsmail fehlgeschlagen (user_id=%s): %s", user_id, type(exc).__name__)
        return False
    log.info("Bestaetigungsmail versendet (user_id=%s).", user_id)
    return True


# ── Closed-Beta-Einladung (Release-Schritt 10) ───────────────────────────────
#
# Gleiche Bauart wie die Bestaetigungsmail: Token nur im Fragment, nie im Log,
# Inline-Styles fuer Outlook, keine Bilder. Der Text bleibt bewusst kurz —
# Testaufgaben und Feedbackfragen kommen spaeter separat, eine Mail mit einer
# Anleitung darin wuerde genau das verhindern, was wir sehen wollen: wie sich
# jemand ohne Anleitung durch ENFAL bewegt.

def _beta_text(link: str) -> str:
    return (
        "Hallo,\n\n"
        "du wurdest eingeladen, ENFAL vor dem öffentlichen Start zu testen.\n\n"
        "Über deinen persönlichen Link erhältst du nach der Anmeldung:\n"
        "- 1 KaufCheck\n"
        "- 1 VerkaufsCheck\n"
        "- Zugriff auf die übrigen kostenlosen ENFAL-Funktionen\n\n"
        f"{link}\n\n"
        "Bitte teile diesen persönlichen Link nicht weiter.\n"
        "Während der Beta entstehen keine echten Zahlungen.\n\n"
        "Wir möchten ausdrücklich ehrliches Feedback – auch wenn etwas unklar, "
        "unnötig oder schlecht gelöst ist.\n\n"
        "Viele Grüße\n"
        "ENFAL\n"
    )


def _beta_html(link: str) -> str:
    sicher = html.escape(link, quote=True)
    return f"""\
<!doctype html>
<html lang="de">
  <body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;padding:32px 16px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background:#ffffff;border-radius:12px;overflow:hidden;">
            <tr>
              <td style="padding:32px 32px 8px 32px;text-align:center;">
                <span style="font-size:22px;font-weight:700;color:{_MARKENFARBE};letter-spacing:0.02em;">ENFAL</span>
              </td>
            </tr>
            <tr>
              <td style="padding:16px 32px 0 32px;color:#111827;font-size:15px;line-height:1.6;">
                <p style="margin:0 0 16px 0;">Hallo,</p>
                <p style="margin:0 0 16px 0;">du wurdest eingeladen, ENFAL vor dem öffentlichen Start zu testen.</p>
                <p style="margin:0 0 8px 0;">Über deinen persönlichen Link erhältst du nach der Anmeldung:</p>
                <ul style="margin:0 0 24px 0;padding-left:20px;color:#374151;">
                  <li>1 KaufCheck</li>
                  <li>1 VerkaufsCheck</li>
                  <li>Zugriff auf die übrigen kostenlosen ENFAL-Funktionen</li>
                </ul>
              </td>
            </tr>
            <tr>
              <td style="padding:0 32px;text-align:center;">
                <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 auto;">
                  <tr>
                    <td style="border-radius:10px;background:{_MARKENFARBE};">
                      <a href="{sicher}"
                         style="display:inline-block;padding:14px 32px;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:10px;">
                        Closed Beta starten
                      </a>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:24px 32px 0 32px;color:#6b7280;font-size:13px;line-height:1.6;">
                <p style="margin:0 0 8px 0;">Falls der Button nicht funktioniert, kopiere diesen Link in deinen Browser:</p>
                <p style="margin:0 0 24px 0;word-break:break-all;">
                  <a href="{sicher}" style="color:{_MARKENFARBE};">{sicher}</a>
                </p>
              </td>
            </tr>
            <tr>
              <td style="padding:0 32px 32px 32px;color:#9ca3af;font-size:12px;line-height:1.6;border-top:1px solid #f3f4f6;">
                <p style="margin:16px 0 0 0;">Bitte teile diesen persönlichen Link nicht weiter.</p>
                <p style="margin:8px 0 0 0;">Während der Beta entstehen keine echten Zahlungen.</p>
                <p style="margin:8px 0 0 0;">Wir möchten ausdrücklich ehrliches Feedback – auch wenn etwas unklar, unnötig oder schlecht gelöst ist.</p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


def sende_beta_einladung(empfaenger: str, link: str) -> bool:
    """Verschickt eine Closed-Beta-Einladung. True = von Brevo angenommen.

    Bekommt den fertigen LINK, nicht den Token: so gibt es genau eine Stelle,
    die den Link baut (`app/beta_invite.py::einladungslink`), und der rohe
    Token wandert nicht durch eine zweite Signatur. Geloggt wird weder Adresse
    noch Link — beides ist personenbezogen bzw. geheim.

    Wirft nie; der Aufrufer (internes Werkzeug) entscheidet anhand von True/
    False, ob er den Link zum manuellen Versand ausgibt.
    """
    if not config.MAIL_AKTIV:
        log.error("Beta-Einladung NICHT versendet: kein Mailversand konfiguriert "
                  "(AUTO_KI_BREVO_API_KEY / AUTO_KI_MAIL_FROM).")
        return False
    try:
        _sende_ueber_brevo(empfaenger, BETREFF_BETA_EINLADUNG, _beta_text(link), _beta_html(link))
    except Exception as exc:
        log.error("Beta-Einladung fehlgeschlagen: %s", type(exc).__name__)
        return False
    log.info("Beta-Einladung versendet.")
    return True
