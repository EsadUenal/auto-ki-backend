"""
Finale Quellenpruefung der Risikoklasse B — Report.

    python kba_primaerquellen_report.py <pfad/zum/kba_export.csv> [--csv datei]

LESEND. Keine Migration, keine DB-Mutation, kein Netzwerk, kein LLM.

Zwei Teile:

  1. Die GENERATION_CONFIRMED-Zeilen aus dem Fachquellen-Audit werden gegen die
     Hersteller-/Primaerquellen in `app/kba_generation_quellen.py` neu
     klassifiziert. Eine Fachquelle allein traegt keinen Import.

  2. Die Mischziel-Zeilen — B-Rueckrufe, deren Zielbaureihe GESCHLOSSEN ist —
     werden gegen den Bestand und gegen Batch A aufgeloest: bereits importiert,
     Dublette, oder weiterhin fehlend (und warum).
"""
import collections
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.kba_generation_audit import (  # noqa: E402
    GENERATION_CONFIRMED, GENERATIONEN,
)
from app.kba_generation_quellen import (  # noqa: E402
    PRIMAERQUELLEN, SOURCE_CONFIRMED, SOURCE_KLASSEN, pruefe,
)
from app.kba_reconciliation import normalisiere_referenz  # noqa: E402
from kba_generation_audit_report import faelle  # noqa: E402


def bestand():
    from app.config import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT id, baureihe_id, mangel, kba_referenz FROM rueckruf")]
    conn.close()
    return rows


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    kba_pfad = sys.argv[1]

    B, zeilen, br = faelle(kba_pfad)
    offen = [z for z in zeilen if z["offen"]]
    misch = [z for z in zeilen if not z["offen"]]
    conf = [z for z in offen if z["klasse"] == GENERATION_CONFIRMED]

    # ── 1) Primaerquellen-Pruefung ──────────────────────────────────────────
    for z in conf:
        z["squelle"], z["sgrund"] = pruefe(z["von"], z["bis"], z["ziel"],
                                           z["start"], z["klasse"])

    print("=" * 78)
    print("FINALE QUELLENPRUEFUNG RISIKOKLASSE B (rein lesend)")
    print("=" * 78)
    baureihen = sorted({z["ziel"] for z in conf})
    mit_primaer = sorted(b for b in baureihen if b in PRIMAERQUELLEN)
    print(f"GENERATION_CONFIRMED-Zeilen (Fachquellen) : {len(conf)}")
    print(f"beteiligte Baureihen                      : {len(baureihen)}")
    print(f"davon mit Hersteller-/Primaerquelle       : {len(mit_primaer)}")
    print()

    s = collections.Counter(z["squelle"] for z in conf)
    print("--- KLASSIFIKATION NACH PRIMAERQUELLE (Zeilen) ---")
    for k in SOURCE_KLASSEN:
        print(f"  {k:26} {s[k]:4}")
    r = collections.defaultdict(set)
    for z in conf:
        r[z["squelle"]].add(z["ref"])
    print("--- dieselbe Sicht nach Rueckrufen ---")
    for k in SOURCE_KLASSEN:
        print(f"  {k:26} {len(r[k]):4}")
    print()

    print("--- BAUREIHEN MIT PRIMAERQUELLE ---")
    print(f"{'baureihe_id':40} {'Hersteller':14} {'Modell':16} {'von':>4} "
          f"{'NF ab':>6} {'prod bis':>8} {'St':>2} {'Zeilen':>6} bestaetigt")
    for bid in mit_primaer:
        b = br[bid]
        q = PRIMAERQUELLEN[bid]
        n = sum(1 for z in conf if z["ziel"] == bid)
        ok = sum(1 for z in conf
                 if z["ziel"] == bid and z["squelle"] == SOURCE_CONFIRMED)
        print(f"{bid:40} {b['marke'][:14]:14} {b['modell'][:16]:16} "
              f"{str(b['bauzeitraum_von']):>4} "
              f"{str(q['nachfolger_ab'] or '-'):>6} "
              f"{str(q['in_produktion_bis'] or '-'):>8} {q['stufe']:>2} "
              f"{n:>6} {ok}/{n}")
        print(f"    {q['url']}")
        print(f"    {q['beleg'][:150]}")
    print()

    print("--- BAUREIHEN OHNE PRIMAERQUELLE (alle Zeilen SOURCE_UNCLEAR) ---")
    ohne = collections.Counter(z["ziel"] for z in conf
                               if z["ziel"] not in PRIMAERQUELLEN)
    for bid, n in sorted(ohne.items()):
        b = br[bid]
        fq = GENERATIONEN.get(bid)
        print(f"  {bid:42} {n:3} Zeile(n)  Fachquelle: "
              f"{(fq[2][:60] if fq else '-')}")
    print()

    sicher = [z for z in conf if z["squelle"] == SOURCE_CONFIRMED]
    print(f"SICHER IMPORTIERBAR: {len({z['ref'] for z in sicher})} Rueckrufe / "
          f"{len(sicher)} VIRA-Zeilen")
    print()

    # ── Die drei benannten Grenzfaelle, unabhaengig von der Vorklassifikation ─
    print("--- GRENZFAELLE GEGEN PRIMAERQUELLE (alle Zeilen, nicht nur die "
          "vorklassifizierten) ---")
    for bid in ("bmw-ix3-g08", "audi-q3-ii", "volkswagen-t-roc-a1"):
        q = PRIMAERQUELLEN.get(bid)
        print(f"  {bid}  Primaerquelle: {'ja (Stufe %d)' % q['stufe'] if q else 'nein'}")
        if q:
            print(f"      {q['url']}")
            print(f"      {q['beleg']}")
        for z in [x for x in offen if x["ziel"] == bid]:
            k, g = pruefe(z["von"], z["bis"], z["ziel"], z["start"], z["klasse"])
            print(f"      KBA {z['ref']:8} {z['von']}-{z['bis']}  "
                  f"Fachklasse={z['klasse']:20} Primaer={k}")
            print(f"          {g}")
    print()

    # ── 2) Mischziel-Zeilen ─────────────────────────────────────────────────
    from app.kba_batch_a_daten import ZEILEN as BATCH_A

    a_paare = {(z["baureihe_id"], normalisiere_referenz(z["kba_referenz"]))
               for z in BATCH_A}
    ist = bestand()
    ist_paare = {(r["baureihe_id"], normalisiere_referenz(r["kba_referenz"]))
                 for r in ist if (r["kba_referenz"] or "").strip()}
    ist_text = collections.defaultdict(set)
    for r in ist:
        ist_text[r["baureihe_id"]].add((r["mangel"] or "").strip().lower())

    print("=" * 78)
    print(f"MISCHZIEL-ZEILEN: {len(misch)}")
    print("=" * 78)
    print("B-Rueckrufe, deren Zielbaureihe GESCHLOSSEN ist. Sie folgen sachlich "
          "der Batch-A-Logik,\nwurden dort aber nie betrachtet: `klasse_a()` "
          "verlangt, dass ALLE Ziele eines\nRueckrufs geschlossen sind — ein "
          "einziges offenes Ziel schliesst den ganzen\nRueckruf aus.\n")
    # Mangeltexte der amtlichen Rueckrufe, um eine inhaltliche Dublette gegen
    # den Bestand erkennen zu koennen (nicht nur ueber die Referenznummer).
    amtlich_text = {}
    for k in B:
        amtlich_text[normalisiere_referenz(k.referenz)] = (k.mangel or "").strip().lower()

    zaehl = collections.Counter()
    for z in misch:
        ref = normalisiere_referenz(z["ref"])
        paar = (z["ziel"], ref)
        in_a = paar in a_paare
        im_bestand = paar in ist_paare
        dublette = amtlich_text.get(ref, "\x00") in ist_text.get(z["ziel"], set())
        status = ("durch Batch A importiert" if in_a else
                  "im Bestand vorhanden (andere Herkunft)" if im_bestand else
                  "inhaltliche Dublette (gleicher Mangeltext)" if dublette else
                  "weiterhin fehlend")
        zaehl[status] += 1
        z["mischstatus"] = status
        z["dublette"] = dublette
    for k2, v in sorted(zaehl.items()):
        print(f"  {k2:44} {v:3}")
    print()
    fehlend = [z for z in misch if z["mischstatus"] == "weiterhin fehlend"]
    print(f"--- WEITERHIN FEHLEND: {len(fehlend)} Zeilen ---")
    print(f"{'KBA':10} {'Baureihe':40} {'Fenster':10} Dublette")
    for z in sorted(fehlend, key=lambda x: (x["ziel"], x["ref"])):
        print(f"  KBA {z['ref']:8} {z['ziel']:40} {z['von']}-{z['bis']}  "
              f"{'JA' if z['dublette'] else 'NEIN'}")
    print()
    print("Grund fuer ALLE: der zugehoerige amtliche Rueckruf nennt mindestens "
          "ein Modell,\ndessen VIRA-Generation offen ist. Batch A hat solche "
          "Rueckrufe vollstaendig\nausgelassen — auch die geschlossenen Ziele "
          "derselben Aktion.")

    if "--csv" in sys.argv:
        import csv
        ziel_csv = sys.argv[sys.argv.index("--csv") + 1]
        with open(ziel_csv, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["ref", "marke", "modell", "von", "bis", "baureihe",
                        "vira_von", "quelle_klasse", "grund"])
            for z in conf:
                w.writerow([z["ref"], z["marke"], z["modell"], z["von"], z["bis"],
                            z["ziel"], z["start"], z["squelle"], z["sgrund"]])
        print(f"\nFallliste geschrieben: {ziel_csv}")


if __name__ == "__main__":
    main()
