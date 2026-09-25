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
# Offizielle ENFAL-Kommunikation, kein Entwicklerartefakt. Gestaltet in der
# Designsprache der App: Dark Navy (Kopfband, wie der Login-Hintergrund), Creme
# (Flaeche, wie die Sidebar) und Orange (CTA, wie jeder Primaerbutton).
#
# ROBUSTHEIT STATT EFFEKT
#   * Kein Bild, auch kein Logo. `public/logo.svg` ist ein SVG-Favicon ohne
#     Raster-Export, und SVG in <img> rendern viele Clients (u.a. Outlook)
#     nicht. Die Wortmarke in Markenfarbe faellt nie aus.
#   * Jede Flaeche bekommt eine EXPLIZITE Hintergrund- UND Textfarbe. Dunkle
#     Clients invertieren sonst einzelne Bereiche und lassen grauen Text auf
#     grauem Grund zurueck.
#   * Layout aus <table> mit Inline-Styles: keine Flexbox, kein Grid, keine
#     Media Queries, die ein Client verwerfen koennte. Die Karte ist
#     fluessigkeitsbreit mit max-width und passt damit auch auf ein Telefon.
#   * Der CTA ist ein Textlink auf farbiger Zelle. Faellt die Farbe weg, bleibt
#     ein verstaendlicher Link; die URL steht zusaetzlich im Klartext darunter.
#
# Der Preheader ist die Zeile, die Mailclients neben dem Betreff anzeigen. Ohne
# ihn zeigen sie den ersten sichtbaren Text, hier also "Hallo,".

_NAVY = "#0d1117"
_CREME = "#f6f2ec"
_TEXT = "#26221c"
_TEXT_LEISE = "#6b645a"
_RAND = "#e7e3dd"

BETA_PREHEADER = ("Deine persönliche Einladung zur ENFAL Closed Beta: "
                  "1 KaufCheck, 1 VerkaufsCheck und die kostenlosen Funktionen.")

_BETA_VORTEILE = (
    "1 KaufCheck",
    "1 VerkaufsCheck",
    "5 AutoFinder-Suchen pro Monat",
    "20 KI-Chat-Nachrichten pro Monat",
    "Autokosten-Rechner unbegrenzt",
)

_BETA_SCHRITTE = (
    "Öffne deinen persönlichen Einladungslink.",
    "Melde dich mit dieser E-Mail-Adresse an oder erstelle ein Konto.",
    "Bestätige deine E-Mail-Adresse.",
    "Aktiviere anschließend deine Beta-Einladung.",
)


def beta_einladungstext(link: str) -> str:
    """Plaintext-Fassung. Vollstaendig und eigenstaendig lesbar, nicht nur ein
    Hinweis auf das HTML: manche Clients zeigen ausschliesslich diesen Teil."""
    vorteile = "\n".join(f"  - {v}" for v in _BETA_VORTEILE)
    schritte = "\n".join(f"  {i}. {t}" for i, t in enumerate(_BETA_SCHRITTE, 1))
    return (
        "Hallo,\n\n"
        "du wurdest persönlich eingeladen, ENFAL vor dem öffentlichen Start zu testen.\n\n"
        "Deine Beta beinhaltet:\n"
        f"{vorteile}\n\n"
        "So kommst du hinein:\n"
        f"{schritte}\n\n"
        "Dein persönlicher Einladungslink:\n"
        f"{link}\n\n"
        "Diese Einladung ist persönlich und an die Empfängeradresse gebunden. "
        "Bitte leite den Link nicht weiter.\n\n"
        "Für die Closed Beta entstehen keine echten Zahlungen.\n\n"
        "Wir wünschen uns ausdrücklich ehrliches Feedback. Wenn etwas unklar, "
        "unnötig oder schlecht gelöst ist, möchten wir das wissen.\n\n"
        "Viele Grüße\n"
        "ENFAL\n"
    )


def _beta_zeilen(eintraege, marker_farbe: str, marker_breite: str,
                 schrift: str, nummeriert: bool) -> str:
    """Eine zweispaltige Liste als Tabellenzeilen (Marker links, Text rechts).

    Als Tabelle statt <ul>, weil Mailclients Listeneinzug sehr unterschiedlich
    rendern und Outlook die Marker teilweise ganz verschluckt.
    """
    zeilen = []
    for i, text in enumerate(eintraege, 1):
        marker = f"{i}." if nummeriert else "&#10003;"
        gewicht = "700"
        zeilen.append(
            f"""
                  <tr>
                    <td width="{marker_breite}" valign="top" style="padding:0 0 10px 0;color:{marker_farbe};"""
            f"""font-size:{schrift};font-weight:{gewicht};line-height:1.6;">{marker}</td>
                    <td valign="top" style="padding:0 0 10px 0;color:{_TEXT};font-size:{schrift};"""
            f"""line-height:1.6;">{html.escape(text)}</td>
                  </tr>"""
        )
    return "".join(zeilen)


def _beta_einladungshtml(link: str) -> str:
    sicher = html.escape(link, quote=True)
    vorteile = _beta_zeilen(_BETA_VORTEILE, _MARKENFARBE, "22", "15px", nummeriert=False)
    schritte = _beta_zeilen(_BETA_SCHRITTE, _TEXT_LEISE, "26", "14px", nummeriert=True)
    return f"""\
<!doctype html>
<html lang="de">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="color-scheme" content="light">
    <title>Deine Einladung zur ENFAL Closed Beta</title>
  </head>
  <body style="margin:0;padding:0;background:{_CREME};color:{_TEXT};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;font-size:1px;line-height:1px;">{html.escape(BETA_PREHEADER)}</div>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{_CREME};padding:32px 16px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:520px;background:#ffffff;border:1px solid {_RAND};border-radius:14px;overflow:hidden;">

            <tr>
              <td align="center" style="background:{_NAVY};padding:26px 32px;">
                <div style="font-size:21px;font-weight:700;letter-spacing:0.16em;color:#ffffff;">ENFAL</div>
                <div style="margin-top:7px;font-size:11px;letter-spacing:0.18em;text-transform:uppercase;color:{_MARKENFARBE};font-weight:700;">Closed Beta</div>
              </td>
            </tr>

            <tr>
              <td style="padding:32px 32px 0 32px;color:{_TEXT};font-size:15px;line-height:1.65;">
                <p style="margin:0 0 16px 0;">Hallo,</p>
                <p style="margin:0 0 24px 0;">du wurdest persönlich eingeladen, ENFAL vor dem öffentlichen Start zu testen.</p>
                <p style="margin:0 0 12px 0;font-weight:600;">Deine Beta beinhaltet:</p>
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">{vorteile}
                </table>
              </td>
            </tr>

            <tr>
              <td style="padding:24px 32px 0 32px;">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:{_CREME};border-radius:10px;">
                  <tr>
                    <td style="padding:18px 20px;">
                      <p style="margin:0 0 12px 0;color:{_TEXT};font-size:14px;font-weight:600;">So kommst du hinein:</p>
                      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">{schritte}
                      </table>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>

            <tr>
              <td align="center" style="padding:28px 32px 8px 32px;">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;">
                  <tr>
                    <td style="border-radius:10px;background:{_MARKENFARBE};">
                      <a href="{sicher}" style="display:inline-block;padding:15px 38px;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:10px;">Closed Beta starten</a>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>

            <tr>
              <td style="padding:16px 32px 0 32px;color:{_TEXT_LEISE};font-size:13px;line-height:1.6;">
                <p style="margin:0 0 6px 0;">Falls der Button nicht funktioniert, kopiere diesen Link in deinen Browser:</p>
                <p style="margin:0;word-break:break-all;"><a href="{sicher}" style="color:{_MARKENFARBE};">{sicher}</a></p>
              </td>
            </tr>

            <tr>
              <td style="padding:24px 32px 32px 32px;">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                  <tr><td style="border-top:1px solid {_RAND};font-size:0;line-height:0;">&nbsp;</td></tr>
                </table>
                <p style="margin:18px 0 0 0;color:{_TEXT_LEISE};font-size:12px;line-height:1.7;">Diese Einladung ist persönlich und an die Empfängeradresse gebunden. Bitte leite den Link nicht weiter.</p>
                <p style="margin:8px 0 0 0;color:{_TEXT_LEISE};font-size:12px;line-height:1.7;">Für die Closed Beta entstehen keine echten Zahlungen.</p>
                <p style="margin:8px 0 0 0;color:{_TEXT_LEISE};font-size:12px;line-height:1.7;">Wir wünschen uns ausdrücklich ehrliches Feedback. Wenn etwas unklar, unnötig oder schlecht gelöst ist, möchten wir das wissen.</p>
              </td>
            </tr>

          </table>

          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:520px;">
            <tr>
              <td align="center" style="padding:18px 16px 0 16px;color:{_TEXT_LEISE};font-size:11px;line-height:1.6;">ENFAL</td>
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
    noch Link, beides ist personenbezogen bzw. geheim.

    Wirft nie; der Aufrufer (internes Werkzeug) entscheidet anhand von True/
    False, ob er den Link zum manuellen Versand ausgibt.
    """
    if not config.MAIL_AKTIV:
        log.error("Beta-Einladung NICHT versendet: kein Mailversand konfiguriert "
                  "(AUTO_KI_BREVO_API_KEY / AUTO_KI_MAIL_FROM).")
        return False
    try:
        _sende_ueber_brevo(empfaenger, BETREFF_BETA_EINLADUNG,
                           beta_einladungstext(link), _beta_einladungshtml(link))
    except Exception as exc:
        log.error("Beta-Einladung fehlgeschlagen: %s", type(exc).__name__)
        return False
    log.info("Beta-Einladung versendet.")
    return True
