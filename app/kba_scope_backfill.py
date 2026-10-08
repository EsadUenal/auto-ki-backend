from __future__ import annotations

"""
Backfill: amtliche Eingrenzung/Produktionsfenster auf BESTEHENDEN kanonischen
`rueckruf`-Zeilen nachtragen (Root-Cause-Audit RC-2).

WARUM DIESES MODUL EXISTIERT
----------------------------
`app/database.py::_migrate_schema` legt die additiven Spalten
`eingrenzung_amtlich`/`prod_von_amtlich`/`prod_bis_amtlich` an, aber nur
NEUE Importe (`app/kba_import_batch_a.py`, `app/kba_active_generation.py`,
`app/kba_conditional_scope.py`) befüllen sie ab sofort. Die ~1.900 bereits
vor diesem Fix importierten Zeilen bleiben sonst dauerhaft NULL — nicht
falsch (sie wurden nur zugelassen, weil ihre amtliche Eingrenzung ohnehin
trivial war), aber unnötig unvollständig für eine künftige Auswertung.

Dieses Kommando trägt die amtlichen Werte für GENAU DIESE Bestandszeilen
nach, anhand derselben vertrauenswürdigen Identität wie der laufende Sync:
die amtliche KBA-Referenz, mit derselben markenübergreifenden
Kollisionsprüfung wie überall sonst im Projekt
(`app.recall_filter.kba_referenz_kollidiert_markenuebergreifend`).

SICHERHEIT
----------
  * Betrifft AUSSCHLIESSLICH die drei neuen, additiven Spalten. `mangel`,
    `abhilfe`, `betroffene_baujahre`, `baureihe_id`, `kba_referenz` werden
    NIE verändert — das bleibt `plane_aktualisierungen()`/`apply_sync()`
    vorbehalten.
  * NUR Zeilen mit einer FORMAT-PLAUSIBLEN, nicht markenübergreifend
    kollidierenden Referenz werden angefasst. Alles andere bleibt NULL und
    wird als "unmatched"/"conflict" ausgewiesen, nicht geraten.
  * NUR Zeilen, die aktuell `eingrenzung_amtlich IS NULL` sind — bereits
    (durch einen künftigen Sync) befüllte Zeilen werden nie überschrieben.
    Das macht einen zweiten Lauf automatisch idempotent: 0 "eligible", sobald
    alles einmal nachgetragen ist.
  * Dry-Run ist der Default. `--apply` schreibt, in EINER Transaktion,
    Rollback bei jedem Fehler (FAIL CLOSED, wie `app.kba_recall_refresh`).

NUTZUNG
-------
    python -m app.kba_scope_backfill <pfad-zum-export.csv> [--apply]

Ohne `--apply`: lädt den Export, druckt den Plan (eligible/conflicts/
unmatched/review nötig), schreibt NICHTS.
Mit `--apply`: schreibt denselben Plan idempotent.
"""

import csv
import io
import logging
import sys

log = logging.getLogger(__name__)


def _lade_offizielle_zeilen(pfad: str) -> dict[str, dict]:
    """Referenz (normalisiert) -> amtliche CSV-Zeile. Bei mehreren Zeilen
    derselben normalisierten Referenz gewinnt die erste (identisch zum
    bestehenden Muster in `kba_recall_refresh.plane_aktualisierungen`)."""
    from app.kba_reconciliation import normalisiere_referenz

    rohbytes = open(pfad, "rb").read()
    text = rohbytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    out: dict[str, dict] = {}
    for row in reader:
        ref = normalisiere_referenz(row.get("KBA-Referenznummer"))
        if ref and ref not in out:
            out[ref] = row
    return out


def plane_backfill(export_pfad: str, recalls_voll: list[dict],
                   baureihen: list[dict]) -> dict:
    """Reiner Plan — liest nur, schreibt nichts.

    Rückgabe: {"updates": [...], "konflikte": [...], "unmatched": [...],
    "bereits_befuellt": n}."""
    from app.kba_import_kandidaten import ImportKandidat
    from app.kba_reconciliation import kba_marke, normalisiere_referenz
    from app.recall_filter import (
        kba_referenz_format_plausibel, kba_referenz_kollidiert_markenuebergreifend,
    )

    offiziell = _lade_offizielle_zeilen(export_pfad)
    baureihe_marke = {b["id"]: b.get("marke") for b in baureihen}

    updates, konflikte, unmatched = [], [], []
    bereits_befuellt = 0

    for r in recalls_voll:
        ref_roh = (r.get("kba_referenz") or "").strip()
        if not ref_roh or not kba_referenz_format_plausibel(ref_roh):
            continue  # keine vertrauenswuerdige Referenz -> kein Rueckschluss moeglich
        # Bereits nachgetragen (von einem frueheren Backfill-Lauf ODER weil die
        # Zeile ohnehin schon ueber einen neuen Import kam) -> idempotent ueberspringen.
        if r.get("eingrenzung_amtlich") is not None:
            bereits_befuellt += 1
            continue

        ref_norm = normalisiere_referenz(ref_roh)
        amtlich = offiziell.get(ref_norm)
        if amtlich is None:
            unmatched.append({"id": r["id"], "kba_referenz": ref_roh,
                              "grund": "Referenz nicht im aktuellen Export gefunden "
                                       "(amtlicher Fall evtl. aelter/entfernt)"})
            continue

        marke_baureihe = baureihe_marke.get(r["baureihe_id"])
        marke_amtlich = kba_marke((amtlich.get("Marke") or ""))
        if marke_baureihe and kba_marke(marke_baureihe) != marke_amtlich:
            konflikte.append({"id": r["id"], "kba_referenz": ref_roh,
                              "grund": f"Marke der Baureihe ({marke_baureihe}) stimmt "
                                       f"nicht mit der amtlichen Marke ({amtlich.get('Marke')}) "
                                       f"ueberein"})
            continue
        if kba_referenz_kollidiert_markenuebergreifend(ref_roh, marke_baureihe):
            konflikte.append({"id": r["id"], "kba_referenz": ref_roh,
                              "grund": "Referenz kollidiert markenuebergreifend im Bestand "
                                       "(KBA-Trust-Gate) — Nachtrag waere nicht vertrauenswuerdig"})
            continue

        kand = ImportKandidat(amtlich)
        # Release-Gate-Fix: NIE `kand.eingrenzung or None` hier — anders als bei
        # einem Neuimport (wo "schon im Bestand" ueber die (Referenz, Baureihe)-
        # Paarung entschieden wird, nicht ueber diese Spalte) ist fuer DIESEN
        # Nachtrag die Spalte selbst der Fortschrittsmarker: "bereits_befuellt"
        # oben UND die serverseitige Guard-Klausel in `apply_backfill()` lesen
        # `eingrenzung_amtlich IS NULL` als "noch nie nachgetragen". Ein
        # trivialer amtlicher Text ("N/A"/leer) wuerde ueber `or None` erneut
        # NULL schreiben -- dieselbe Zeile bliebe nach jedem Lauf wieder
        # "eligible" (nicht idempotent). Die leere Zeichenkette markiert
        # "geprueft, amtlich keine Eingrenzung" und verhaelt sich zur Laufzeit
        # (`recall_filter.rueckruf_scope()`: `str(... or "")`) identisch zu NULL.
        updates.append({
            "id": r["id"], "kba_referenz": ref_roh,
            "eingrenzung_amtlich": kand.eingrenzung,
            "prod_von_amtlich": kand.prod_von,
            "prod_bis_amtlich": kand.prod_bis,
        })

    return {"updates": updates, "konflikte": konflikte, "unmatched": unmatched,
            "bereits_befuellt": bereits_befuellt}


def apply_backfill(conn, plan: dict) -> dict:
    """Schreibt `plan["updates"]` idempotent — NUR die drei additiven Spalten,
    NUR für Zeilen, deren `eingrenzung_amtlich` noch NULL ist (serverseitig
    per WHERE nochmals abgesichert, nicht nur im Plan). Transaktional: der
    Aufrufer committet/rollbackt (siehe `app.kba_recall_refresh.apply_sync`
    für dasselbe Muster)."""
    aktualisiert = 0
    for u in plan["updates"]:
        cur = conn.execute(
            "UPDATE rueckruf SET eingrenzung_amtlich=?, prod_von_amtlich=?, "
            "prod_bis_amtlich=? WHERE id=? AND eingrenzung_amtlich IS NULL",
            (u["eingrenzung_amtlich"], u["prod_von_amtlich"], u["prod_bis_amtlich"],
             u["id"]))
        if cur.rowcount:
            aktualisiert += 1
    return {"aktualisiert": aktualisiert}


def main() -> None:
    """`python -m app.kba_scope_backfill <export.csv> [--apply]`."""
    from app.database import get_alle_baureihen_kurz, get_alle_rueckrufe_fuer_sync, get_conn

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = sys.argv[1:]
    apply_ = "--apply" in args
    pfad_args = [a for a in args if a != "--apply"]
    if not pfad_args:
        log.error("Nutzung: python -m app.kba_scope_backfill <pfad-zum-export.csv> [--apply]")
        raise SystemExit(2)
    export_pfad = pfad_args[0]

    baureihen = get_alle_baureihen_kurz()
    recalls = get_alle_rueckrufe_fuer_sync()
    plan = plane_backfill(export_pfad, recalls, baureihen)

    log.info("=== KBA-Scope-Backfill (RC-2) — Plan ===")
    log.info("bereits befuellt (uebersprungen):     %d", plan["bereits_befuellt"])
    log.info("eligible fuer Nachtrag:                %d", len(plan["updates"]))
    log.info("Konflikte (Marke/Kollision):           %d", len(plan["konflikte"]))
    log.info("unmatched (Referenz nicht im Export):  %d", len(plan["unmatched"]))
    for k in plan["konflikte"][:10]:
        log.info("  KONFLIKT id=%s ref=%s: %s", k["id"], k["kba_referenz"], k["grund"])
    for u in plan["unmatched"][:10]:
        log.info("  UNMATCHED id=%s ref=%s: %s", u["id"], u["kba_referenz"], u["grund"])

    if not apply_:
        log.info("Dry-Run (Default) — keine Zeile geschrieben. "
                 "Mit --apply wird genau dieser Plan idempotent angewendet.")
        return

    with get_conn() as conn:
        ergebnis = apply_backfill(conn, plan)
    log.info("=== --apply abgeschlossen: %d Zeile(n) aktualisiert ===", ergebnis["aktualisiert"])


if __name__ == "__main__":
    main()
