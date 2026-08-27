"""
Qualitaetsreport des KBA-Gesamtabgleichs — LESEND, keine Mutation.

    python kba_abgleich_report.py <pfad/zum/kba_export.csv> [--detail KLASSE]

Gibt die Verteilung ueber die fuenf Match-Klassen aus, dazu Dublettenverdacht
und amtliche Rueckrufe, die im Bestand fehlen. Das ist die Entscheidungs-
grundlage nach §13 des Auftrags: erst der Report, dann (vielleicht) die
Migration.
"""
import collections
import sqlite3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.kba_reconciliation import (  # noqa: E402
    CONTRADICTED, CORRECTABLE, EXACT, KLASSEN, NO_MATCH, PARTIAL,
    abgleich, fehlende_amtliche, lade_kba, normalisiere_referenz,
)


def lade_vira():
    p = os.path.expandvars(r"%LOCALAPPDATA%\auto-ki-backend\auto_ki.db")
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    recalls = [dict(r) for r in conn.execute("SELECT * FROM rueckruf ORDER BY id")]
    baureihen = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM baureihe")}
    verifs = {r["fakt_id"]: dict(r) for r in conn.execute(
        "SELECT * FROM fakt_verifikation WHERE fakt_art='rueckruf'")}
    conn.close()
    return recalls, baureihen, verifs


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    kba_pfad = sys.argv[1]
    detail = None
    if "--detail" in sys.argv:
        detail = sys.argv[sys.argv.index("--detail") + 1]

    kba = lade_kba(kba_pfad)
    recalls, baureihen, verifs = lade_vira()
    befunde = abgleich(recalls, baureihen, kba)

    print("=" * 78)
    print("KBA-GESAMTABGLEICH — QUALITAETSREPORT (keine Mutation)")
    print("=" * 78)
    print(f"Amtlicher KBA-Export      : {len(kba)} Rueckrufe")
    print(f"VIRA-Rueckrufbestand      : {len(recalls)} Zeilen")
    print(f"VIRA-Baureihen mit RR     : {len({r['baureihe_id'] for r in recalls})}")
    print(f"Bereits fact-level geprueft: {len(verifs)}")
    print()

    zaehler = collections.Counter(b.klasse for b in befunde)
    print("--- MATCH-KLASSEN ---")
    for k in KLASSEN:
        n = zaehler[k]
        print(f"  {k:24} {n:4}  ({n * 100 / len(befunde):5.1f} %)")
    print()

    # Automatisierbar waeren nur EXACT + CORRECTABLE
    auto = zaehler[EXACT] + zaehler[CORRECTABLE]
    print(f"  automatisch verwertbar (EXACT+CORRECTABLE): {auto} "
          f"({auto * 100 / len(befunde):.1f} %)")
    print()

    # ── Dublettenverdacht: mehrere VIRA-Zeilen -> dieselbe amtliche Referenz ──
    je_ref = collections.defaultdict(list)
    for b in befunde:
        if b.klasse in (EXACT, CORRECTABLE) and b.bester:
            je_ref[b.bester.referenz].append(b)
    dubletten = {ref: lst for ref, lst in je_ref.items() if len(lst) > 1}
    print(f"--- DUBLETTENVERDACHT: {len(dubletten)} amtliche Rueckrufe werden von "
          f"mehreren VIRA-Zeilen beansprucht ---")
    for ref, lst in sorted(dubletten.items(), key=lambda x: -len(x[1]))[:15]:
        ids = ", ".join(f"#{b.fakt_id}" for b in lst)
        gleiche_br = len({b.baureihe_id for b in lst}) == 1
        print(f"  KBA {ref:8} <- {ids}"
              f"{'  (SELBE Baureihe = echte Dublette)' if gleiche_br else '  (verschiedene Baureihen)'}")
    print()

    # ── Fehlende amtliche Rueckrufe ──────────────────────────────────────────
    fehlend = fehlende_amtliche(recalls, baureihen, kba)
    print(f"--- FEHLENDE AMTLICHE RUECKRUFE (eindeutig zuordenbar): "
          f"{len(fehlend)} ---")
    marken = collections.Counter(
        (k.get("Marke") or "").strip() for k, _b in fehlend)
    for m, n in marken.most_common(15):
        print(f"  {m:20} {n}")
    print()

    # ── Referenzlage im Bestand ──────────────────────────────────────────────
    amtliche_refs = {normalisiere_referenz(k.get("KBA-Referenznummer"))
                     for k in kba}
    mit_ref = [r for r in recalls if (r.get("kba_referenz") or "").strip()]
    echt = [r for r in mit_ref
            if normalisiere_referenz(r["kba_referenz"]) in amtliche_refs]
    print("--- REFERENZLAGE IM BESTAND ---")
    print(f"  VIRA-Zeilen mit kba_referenz          : {len(mit_ref)}")
    print(f"  davon Nummer existiert amtlich ueberhaupt: {len(echt)}")
    print(f"  davon frei erfunden                    : {len(mit_ref) - len(echt)}")
    print()

    if detail:
        print("=" * 78)
        print(f"DETAIL: {detail}")
        print("=" * 78)
        for b in befunde:
            if b.klasse != detail:
                continue
            best = b.bester
            print(f"#{b.fakt_id:4} {b.marke} {b.modell} {b.generation}")
            print(f"     VIRA : {b.mangel[:88]}")
            print(f"     jahre={b.vira_baujahre!r} datum={b.vira_datum!r} "
                  f"ref={b.vira_referenz!r}")
            if best and b.klasse in (EXACT, CORRECTABLE, CONTRADICTED):
                print(f"     KBA {best.referenz}: "
                      f"{(best.kba.get('Mangelbezeichnung') or '')[:84]}")
                print(f"     prod={best.kba.get('Produktionszeitraum von')}-"
                      f"{best.kba.get('Produktionszeitraum bis')} "
                      f"datum={best.kba.get('Veröffentlichungsdatum')} "
                      f"gruppen={sorted(best.starke_gruppen)} "
                      f"tokens={sorted(best.tokens_gemeinsam)[:5]}")
            print(f"     -> {b.begruendung[:100]}")
            print()


if __name__ == "__main__":
    main()
