"""
Internes Werkzeug: Closed-Beta-Einladungen erzeugen und (optional) versenden.

Es gibt bewusst KEINEN HTTP-Endpunkt dafuer. Fuer vier bis fuenf namentlich
bekannte Testerinnen und Tester waere eine Weboberflaeche oder eine
admin-geschuetzte Route zusaetzliche Angriffsflaeche ohne jeden Nutzen.

AUSFUEHREN (im Repo-Ordner, mit derselben Umgebung wie die App):

    # 1. Nur anzeigen, was passieren wuerde — schreibt nichts:
    python scripts/beta_invite_tool.py --dry-run tester@example.de

    # 2. Einladung anlegen, Link auf der Konsole ausgeben (kein Mailversand):
    python scripts/beta_invite_tool.py tester@example.de

    # 3. Einladung anlegen UND per Brevo verschicken:
    python scripts/beta_invite_tool.py --senden tester@example.de

    # 4. Mehrere auf einmal:
    python scripts/beta_invite_tool.py --senden a@x.de b@y.de c@z.de

    # 5. Stand aller Einladungen ansehen (ohne Token, ohne Link):
    python scripts/beta_invite_tool.py --liste

WICHTIG — der ausgegebene Link IST das Geheimnis:
  * Ohne --senden landet er auf der Konsole. Nicht in einen Chat, ein Ticket
    oder ein Dokument kopieren, das andere lesen koennen.
  * Mit --senden geht er direkt an die eingeladene Adresse und wird NICHT
    ausgegeben.
  * Gespeichert wird nur sein SHA-256-Hash; ein verlorener Link laesst sich
    nicht wiederherstellen, nur durch einen neuen ersetzen (dabei verfaellt
    der alte automatisch).

DATENBANK: dieselbe wie die App — ueber AUTO_KI_DB_PATH bzw. den Standardpfad
aus app/config.py. Auf dem Server heisst das: in der Umgebung ausfuehren, in
der auch die App laeuft, sonst landet die Einladung in einer anderen Datei.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import beta_invite            # noqa: E402
from app.database import ensure_tables, get_conn   # noqa: E402
from app.mailer import sende_beta_einladung        # noqa: E402


def _liste() -> int:
    """Stand aller Einladungen. Zeigt weder Token noch Link."""
    with get_conn() as conn:
        zeilen = conn.execute(
            "SELECT email, paket, erstellt_at, laeuft_ab_at, eingeloest_at, "
            "eingeloest_von, entwertet_at FROM beta_invite ORDER BY erstellt_at"
        ).fetchall()
    if not zeilen:
        print("Keine Einladungen vorhanden.")
        return 0
    print(f"{len(zeilen)} Einladung(en):\n")
    for z in zeilen:
        if z["eingeloest_at"]:
            zustand = f"eingeloest am {z['eingeloest_at']} (user_id={z['eingeloest_von']})"
        elif z["entwertet_at"]:
            zustand = f"entwertet am {z['entwertet_at']} (durch Neuausstellung ersetzt)"
        else:
            zustand = f"offen, gueltig bis {z['laeuft_ab_at']}"
        print(f"  {z['email']:<40} {z['paket']:<10} {zustand}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Closed-Beta-Einladungen erzeugen (1 KaufCheck + 1 VerkaufsCheck je Adresse).",
    )
    p.add_argument("emails", nargs="*", help="Eine oder mehrere E-Mail-Adressen")
    p.add_argument("--senden", action="store_true",
                   help="Einladung per Brevo verschicken (ohne dies: Link nur auf der Konsole)")
    p.add_argument("--dry-run", action="store_true",
                   help="Nur zeigen, was passieren wuerde — schreibt nichts, versendet nichts")
    p.add_argument("--liste", action="store_true", help="Stand aller Einladungen anzeigen")
    p.add_argument("--tage", type=int, default=beta_invite.GUELTIG_TAGE,
                   help=f"Gueltigkeit in Tagen (Standard: {beta_invite.GUELTIG_TAGE})")
    args = p.parse_args()

    ensure_tables()

    if args.liste:
        return _liste()

    if not args.emails:
        p.error("Bitte mindestens eine E-Mail-Adresse angeben (oder --liste).")

    paket = ", ".join(f"{anzahl}x {produkt}" for produkt, anzahl in beta_invite.PAKET_INHALT.items())
    print(f"Paket {beta_invite.PAKET_V1}: {paket} — gueltig {args.tage} Tage\n")

    fehler = 0
    for roh in args.emails:
        ziel = beta_invite.normalisiere_email(roh)
        if args.dry_run:
            print(f"[dry-run] wuerde Einladung erzeugen fuer: {ziel}")
            continue
        try:
            token = beta_invite.erzeuge_einladung(ziel, gueltig_tage=args.tage)
        except beta_invite.EinladungAbgelehnt as exc:
            print(f"[uebersprungen] {ziel}: {exc}")
            fehler += 1
            continue

        link = beta_invite.einladungslink(token)
        if args.senden:
            if sende_beta_einladung(ziel, link):
                print(f"[versendet] {ziel}")
            else:
                # Die Einladung ist bereits angelegt und gueltig — der Link wird
                # ausgegeben, damit sie nicht verloren ist.
                print(f"[MAILFEHLER] {ziel} — Einladung existiert, Link manuell senden:")
                print(f"             {link}")
                fehler += 1
        else:
            print(f"[angelegt] {ziel}")
            print(f"           {link}")

    if args.dry_run:
        print("\nNichts geschrieben (--dry-run).")
    return 1 if fehler else 0


if __name__ == "__main__":
    raise SystemExit(main())
