"""
VIRA Plus — monatliches Abo (Consumer Pricing V1 FINAL)

16,99 €/Monat, monatlich kündbar. Enthalten je bezahltem Abrechnungszeitraum:
5 KaufChecks, 1 VerkaufsCheck, 50 AutoFinder-Suchen, 100 KI-Chat-Nachrichten.

ABGRENZUNG ZU GEKAUFTEM GUTHABEN
--------------------------------
Plus-Kontingente und einzeln gekaufte Checks liegen in getrennten Spalten und
werden nie vermischt:

  plus_kaufchecks_verbleibend       monatlich, wird je Zeitraum ZURÜCKGESETZT
  kaufchecks_verbleibend            einmalig gekauft, verfällt NIE

Das ist keine Redundanz, sondern der fachliche Kern: „5 KaufChecks pro Monat"
ist eine Leistung, die mit dem Monat endet; ein für 5,99 € gekaufter KaufCheck
ist Eigentum des Nutzers und überlebt Abschluss, Kündigung und Auslaufen des
Abos. In einem gemeinsamen Zähler wäre beides nicht mehr auseinanderzuhalten —
der Nutzer würde beim Monatswechsel gekauftes Guthaben verlieren.

WARUM KEIN abo_typ='plus'
-------------------------
`users.abo_typ` trägt einen CHECK-Constraint (none/light/pro/max), den SQLite
nicht per ALTER ändern kann. Ein Tabellen-Rebuild auf der Live-Datenbank wäre
ein vermeidbares Risiko für Bestandskunden. Plus wird deshalb über eigene
Spalten geführt; die Legacy-Abos bleiben vollständig unberührt.

AKTIVITÄT
---------
Plus gilt als aktiv, solange `plus_period_end` in der Zukunft liegt. Diese
Grenze stammt aus dem von Stripe als bezahlt gemeldeten Zeitraum — nicht aus
einem lokal gesetzten Flag und nicht aus einem Redirect. Läuft der Zeitraum ab,
ohne dass eine neue Zahlung eintrifft (Kündigung oder fehlgeschlagene
Verlängerung), endet Plus von selbst: es braucht keinen Aufräum-Job, der ein
Flag zurücksetzt.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.config import PLUS_KAUFCHECKS_PRO_MONAT, PLUS_VERKAUFSCHECKS_PRO_MONAT
from app.database import get_conn

log = logging.getLogger(__name__)


def _jetzt_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _ts_iso(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def ist_aktiv(row) -> bool:
    """True, wenn der bezahlte Plus-Zeitraum noch läuft.

    `row` ist eine users-Zeile (sqlite3.Row oder dict) mit `plus_period_end`.
    """
    if row is None:
        return False
    try:
        ende = row["plus_period_end"]
    except (KeyError, IndexError, TypeError):
        return False
    if not ende:
        return False
    return str(ende) > _jetzt_iso()


def status(user_id: int) -> dict:
    """Plus-Status eines Nutzers für /auth/me, /payments/status und Settings."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT plus_kaufchecks_verbleibend, plus_verkaufschecks_verbleibend, "
            "plus_period_start, plus_period_end, plus_kuendigt_zum "
            "FROM users WHERE id=?", (user_id,)
        ).fetchone()
    if row is None:
        return {"plus_aktiv": False}
    aktiv = ist_aktiv(row)
    return {
        "plus_aktiv": aktiv,
        "plus_kaufchecks_verbleibend": row["plus_kaufchecks_verbleibend"] if aktiv else 0,
        "plus_verkaufschecks_verbleibend": row["plus_verkaufschecks_verbleibend"] if aktiv else 0,
        "plus_period_end": row["plus_period_end"],
        "plus_kuendigt_zum": row["plus_kuendigt_zum"],
    }


def grant_periode(user_id: int, subscription_id: str | None,
                  period_start: int | None, period_end: int | None) -> None:
    """Schaltet einen bezahlten Abrechnungszeitraum frei.

    SETZT die Monatskontingente auf den Sollwert — sie werden NICHT addiert.
    Damit gilt beides gleichzeitig:

      * Nicht verbrauchte Plus-Checks verfallen zum Monatswechsel (kein Übertrag:
        aus 3 übrigen + 5 neuen werden 5, nicht 8).
      * Der Vorgang ist idempotent. Trifft dasselbe Stripe-Event zweimal ein —
        etwa durch einen Webhook-Retry —, steht danach immer noch 5/1 und nicht
        10/2. Ein `+=` wäre hier ein Doppel-Gutschrift-Bug, den keine
        Event-Deduplizierung mehr auffangen könnte, sobald sie einmal versagt.

    GEKAUFTES Guthaben (kaufchecks_verbleibend / verkaufschecks_verbleibend)
    wird hier bewusst NICHT angefasst.
    """
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET plus_kaufchecks_verbleibend=?, plus_verkaufschecks_verbleibend=?, "
            "plus_period_start=?, plus_period_end=?, plus_subscription_id=COALESCE(?, plus_subscription_id), "
            "plus_kuendigt_zum=NULL "
            "WHERE id=?",
            (PLUS_KAUFCHECKS_PRO_MONAT, PLUS_VERKAUFSCHECKS_PRO_MONAT,
             _ts_iso(period_start), _ts_iso(period_end), subscription_id, user_id),
        )
        conn.commit()
    log.info("Plus-Zeitraum freigeschaltet: user_id=%s bis %s (%s/%s Checks)",
             user_id, _ts_iso(period_end), PLUS_KAUFCHECKS_PRO_MONAT, PLUS_VERKAUFSCHECKS_PRO_MONAT)


def merke_kuendigung(user_id: int, kuendigt_zum: str | None) -> None:
    """Hält fest, dass Plus zum Periodenende endet (Anzeige in den Einstellungen).

    Die Leistung läuft bis dahin unverändert weiter — deshalb wird hier weder
    ein Kontingent gekürzt noch `plus_period_end` verschoben.
    """
    with get_conn() as conn:
        conn.execute("UPDATE users SET plus_kuendigt_zum=? WHERE id=?", (kuendigt_zum, user_id))
        conn.commit()


def beende(subscription_id: str) -> None:
    """Beendet Plus sofort (Stripe meldet die Subscription als gelöscht).

    Setzt die MONATLICHEN Kontingente auf 0 und den Zeitraum auf abgelaufen.
    Gekauftes Guthaben bleibt ausdrücklich erhalten — es gehört dem Nutzer und
    hat mit dem Abo nichts zu tun.
    """
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET plus_kaufchecks_verbleibend=0, plus_verkaufschecks_verbleibend=0, "
            "plus_period_end=NULL, plus_period_start=NULL, plus_kuendigt_zum=NULL, "
            "plus_subscription_id=NULL "
            "WHERE plus_subscription_id=?",
            (subscription_id,),
        )
        conn.commit()
