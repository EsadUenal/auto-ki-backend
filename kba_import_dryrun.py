"""
Import-Dry-Run: was liesse sich vom amtlichen KBA-Bestand uebernehmen?

    python kba_import_dryrun.py <pfad/zum/kba_export.csv> [--detail KLASSE] [--n 30]
                                [--db pfad.db] [--paare] [--baureihe ID]

LESEND. Keine Migration, keine DB-Mutation.

`--paare` zeigt die Entscheidung JE PAAR (Rueckruf, Baureihe) und listet die
Paare, die fuer sich sicher sind, aber unter der alten Entscheidung pro Rueckruf
verloren gingen (KaufCheck Root-Cause-Closing, Befund M). `--baureihe` grenzt
diese Liste auf eine Baureihe ein.
"""
import collections
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.kba_import_kandidaten import (  # noqa: E402
    AMBIGUOUS_GENERATION, IMPORT_KLASSEN, POSSIBLE_DUPLICATE, SAFE_IMPORT,
    UNSUPPORTED_MODEL_MAPPING, VARIANT_SCOPE_UNCLEAR, import_kandidaten,
    paare_bei_import, verlorene_paare, zeilen_bei_import,
)
from app.kba_reconciliation import lade_kba  # noqa: E402


def lade_vira(pfad: str | None = None):
    p = pfad or os.path.expandvars(r"%LOCALAPPDATA%\auto-ki-backend\auto_ki.db")
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    recalls = [dict(r) for r in conn.execute("SELECT * FROM rueckruf ORDER BY id")]
    baureihen = [dict(r) for r in conn.execute("SELECT * FROM baureihe")]
    conn.close()
    return recalls, baureihen


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    kba_pfad = sys.argv[1]
    detail = sys.argv[sys.argv.index("--detail") + 1] if "--detail" in sys.argv else None
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 30
    db = sys.argv[sys.argv.index("--db") + 1] if "--db" in sys.argv else None
    nur_baureihe = (sys.argv[sys.argv.index("--baureihe") + 1]
                    if "--baureihe" in sys.argv else None)

    kba = lade_kba(kba_pfad)
    recalls, baureihen = lade_vira(db)
    kand = import_kandidaten(kba, recalls, baureihen)

    if "--paare" in sys.argv:
        paare = collections.Counter(kl for k in kand for _b, kl, _g in k.paare)
        print("--- ENTSCHEIDUNG JE PAAR (Rueckruf, Baureihe) ---")
        for kl in IMPORT_KLASSEN:
            print(f"  {kl:26} {paare[kl]:5} Paare")
        print(f"  SAFE_IMPORT-Paare gesamt: {len(paare_bei_import(kand))}")
        verloren = [v for v in verlorene_paare(kand)
                    if not nur_baureihe or v["baureihe_id"] == nur_baureihe]
        print(f"\n--- FUER SICH SICHER, BISHER VERLOREN: {len(verloren)} Paare ---")
        print(f"  Gruende: {dict(collections.Counter(v['grund'] for v in verloren))}")
        print(f"  Baureihen: {len({v['baureihe_id'] for v in verloren})}")
        for v in verloren[:n]:
            print(f"  {v['baureihe_id']:32} KBA {v['referenz']:8} {v['prod_von']}-{v['prod_bis']}"
                  f"  {v['grund']}\n      {v['mangel'][:100]}")
        return

    print("=" * 78)
    print("KBA-IMPORT DRY-RUN (keine Mutation)")
    print("=" * 78)
    print(f"Amtlicher Export        : {len(kba)} Rueckrufe")
    print(f"VIRA-Bestand            : {len(recalls)} Rueckrufe, {len(baureihen)} Baureihen")
    print(f"Kandidaten (ueberwacht, sicherheitsrelevant, fehlend, Marke in VIRA): "
          f"{len(kand)}")
    print()

    z = collections.Counter(k.klasse for k in kand)
    print("--- IMPORT-KLASSEN ---")
    for kl in IMPORT_KLASSEN:
        zeilen = zeilen_bei_import(kand, kl)
        print(f"  {kl:26} {z[kl]:4} Rueckrufe -> {zeilen:5} VIRA-Zeilen")
    print()
    print(f"  Nur SAFE_IMPORT waere automatisch uebernehmbar: "
          f"{z[SAFE_IMPORT]} Rueckrufe / {zeilen_bei_import(kand, SAFE_IMPORT)} Zeilen")
    print()

    safe = [k for k in kand if k.klasse == SAFE_IMPORT]
    ziele = {zid for k in safe for zid in k.ziel_ids}
    print(f"--- SAFE_IMPORT im Detail ---")
    print(f"  eindeutige Zielbaureihen : {len(ziele)}")
    marken = collections.Counter(k.marke for k in safe)
    print(f"  betroffene Marken        : {len(marken)}")
    for m, c in marken.most_common():
        print(f"      {m:20} {c}")
    if safe:
        daten = sorted(k.datum for k in safe if k.datum)
        print(f"  aeltester Rueckruf       : {daten[0]}")
        print(f"  neuester Rueckruf        : {daten[-1]}")
    appl = collections.Counter(k.applicability for k in safe)
    print(f"  vorhergesagte Applicability: {dict(appl)}")
    print()

    if detail:
        print("=" * 78)
        print(f"DETAIL: {detail} (erste {n})")
        print("=" * 78)
        for k in [x for x in kand if x.klasse == detail][:n]:
            print(f"KBA {k.referenz:8} {k.datum}  {k.marke} {k.modell}")
            print(f"    prod={k.prod_von}-{k.prod_bis}  code={k.herstellercode[:40]}")
            print(f"    Mangel   : {k.mangel[:96]}")
            if k.massnahme:
                print(f"    Massnahme: {k.massnahme[:96]}")
            if k.eingrenzung:
                print(f"    Eingrenzung: {k.eingrenzung[:96]}")
            print(f"    Ziele    : {k.ziel_ids}")
            print(f"    gen_eindeutig={k.generation_eindeutig} "
                  f"variantenbeschraenkt={k.variantenbeschraenkung} "
                  f"appl={k.applicability}")
            print(f"    -> {k.begruendung[:104]}")
            print()


if __name__ == "__main__":
    main()
