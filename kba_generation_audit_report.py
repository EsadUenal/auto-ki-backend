"""
Generationsaudit der Risikoklasse B — Report.

    python kba_generation_audit_report.py <pfad/zum/kba_export.csv> [--klasse X]

LESEND. Keine Migration, keine DB-Mutation, kein Netzwerk, kein LLM.

Klasse B = `SAFE_IMPORT` aus `app/kba_import_kandidaten.py`, deren Zielbaureihe
`bauzeitraum_bis IS NULL` traegt. Fuer jede Zeile wird gegen die recherchierten
Generationsgrenzen in `app/kba_generation_audit.py` entschieden, ob der amtliche
Produktionszeitraum ueberhaupt noch in diese Generation fallen kann.
"""
import collections
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.kba_generation_audit import (  # noqa: E402
    CROSS_GENERATION, GENERATION_CONFIRMED, GENERATION_UNCLEAR, GENERATIONEN,
    KLASSEN, SUCCESSOR_RECALL, klassifiziere,
)
from app.kba_import_batch_a import klasse_a  # noqa: E402
from app.kba_import_kandidaten import SAFE_IMPORT, import_kandidaten  # noqa: E402
from app.kba_reconciliation import lade_kba  # noqa: E402


def lade_vira():
    from app.config import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    recalls = [dict(r) for r in conn.execute("SELECT * FROM rueckruf ORDER BY id")]
    baureihen = [dict(r) for r in conn.execute("SELECT * FROM baureihe")]
    conn.close()
    return recalls, baureihen


def _nachfolger_in_vira(b, baureihen):
    """Fuehrt VIRA selbst schon eine SPAETERE Generation desselben Modells?

    Der Dry-Run haette einen solchen Fall normalerweise als
    AMBIGUOUS_GENERATION aussortiert — er tut es aber nur, wenn die
    Nachfolgegeneration auch die Ueberdeckungsschwelle erreicht. Genau die
    Restmenge ist hier interessant.
    """
    marke, modell = b["marke"].strip().lower(), b["modell"].strip().lower()
    spaeter = [x for x in baureihen
               if x["id"] != b["id"]
               and x["marke"].strip().lower() == marke
               and x["modell"].strip().lower() == modell
               and (x["bauzeitraum_von"] or 0) > (b["bauzeitraum_von"] or 0)]
    if not spaeter:
        return None
    return min(spaeter, key=lambda x: x["bauzeitraum_von"])


def faelle(kba_pfad):
    recalls, baureihen = lade_vira()
    br = {b["id"]: b for b in baureihen}
    kand = import_kandidaten(lade_kba(kba_pfad), recalls, baureihen)
    a_refs = {k.referenz for k in klasse_a(kand, baureihen)}
    B = [k for k in kand if k.klasse == SAFE_IMPORT and k.referenz not in a_refs]

    out = []
    for k in B:
        for ziel in k.ziel_ids:
            b = br[ziel]
            if b.get("bauzeitraum_bis") is not None:
                # Mischfall: dieser Zielbaureihe fehlt nichts, sie ist
                # geschlossen. Sie gehoert sachlich zur Klasse-A-Logik und wird
                # hier nur ausgewiesen, nicht klassifiziert.
                out.append({"ref": k.referenz, "marke": k.marke, "modell": k.modell,
                            "von": k.prod_von, "bis": k.prod_bis, "ziel": ziel,
                            "start": b["bauzeitraum_von"], "offen": False,
                            "klasse": None, "grund": "Zielgeneration ist "
                            "geschlossen (Mischfall eines B-Rueckrufs)"})
                continue
            kl, grund = klassifiziere(k.prod_von, k.prod_bis, ziel,
                                      b["bauzeitraum_von"])
            nf_vira = _nachfolger_in_vira(b, baureihen)
            fakt = GENERATIONEN.get(ziel)
            out.append({"ref": k.referenz, "marke": k.marke, "modell": k.modell,
                        "von": k.prod_von, "bis": k.prod_bis, "ziel": ziel,
                        "start": b["bauzeitraum_von"], "offen": True,
                        "reales_ende": fakt[0] if fakt else None,
                        "nachfolger_ab": fakt[1] if fakt else None,
                        "quelle": fakt[2] if fakt else "",
                        "nachfolger_in_vira": nf_vira["id"] if nf_vira else None,
                        "klasse": kl, "grund": grund})
    return B, out, br


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    kba_pfad = sys.argv[1]
    nur = sys.argv[sys.argv.index("--klasse") + 1] if "--klasse" in sys.argv else None

    B, zeilen, br = faelle(kba_pfad)
    if "--csv" in sys.argv:
        import csv
        ziel_csv = sys.argv[sys.argv.index("--csv") + 1]
        spalten = ["ref", "marke", "modell", "von", "bis", "ziel", "start",
                   "reales_ende", "nachfolger_ab", "nachfolger_in_vira",
                   "klasse", "grund", "quelle"]
        with open(ziel_csv, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=spalten, delimiter=";",
                               extrasaction="ignore")
            w.writeheader()
            for z in zeilen:
                if z["offen"]:
                    w.writerow(z)
        print(f"Vollstaendige Fallliste geschrieben: {ziel_csv}")
        print()
    offen = [z for z in zeilen if z["offen"]]
    geschlossen = [z for z in zeilen if not z["offen"]]

    print("=" * 78)
    print("GENERATIONSAUDIT RISIKOKLASSE B (rein lesend)")
    print("=" * 78)
    print(f"B-Rueckrufe                        : {len(B)}")
    print(f"geplante VIRA-Zeilen               : {len(zeilen)}")
    print(f"  davon offene Zielgeneration      : {len(offen)}")
    print(f"  davon geschlossenes Mischziel    : {len(geschlossen)}")
    print(f"betroffene offene Baureihen        : {len({z['ziel'] for z in offen})}")
    print(f"davon mit recherchierter Grenze    : "
          f"{len({z['ziel'] for z in offen if z['ziel'] in GENERATIONEN})}")
    mit_nf = [z for z in offen if z["nachfolger_in_vira"]]
    print(f"Zeilen, deren Nachfolgegeneration in VIRA bereits existiert: "
          f"{len(mit_nf)}")
    for z in mit_nf:
        print(f"    KBA {z['ref']:8} {z['ziel']:34} -> {z['nachfolger_in_vira']}"
              f"  [{z['klasse']}]")
    print()

    z = collections.Counter(x["klasse"] for x in offen)
    print("--- KLASSIFIKATION (Zeilen) ---")
    for kl in KLASSEN:
        print(f"  {kl:22} {z[kl]:4}")
    print()
    r = collections.defaultdict(set)
    for x in offen:
        r[x["klasse"]].add(x["ref"])
    print("--- KLASSIFIKATION (Rueckrufe, Mehrfachnennung moeglich) ---")
    for kl in KLASSEN:
        print(f"  {kl:22} {len(r[kl]):4}")
    print()

    marken = collections.Counter(br[x["ziel"]]["marke"] for x in offen)
    print(f"--- MARKEN ({len(marken)}) ---")
    for m, n in marken.most_common():
        gut = sum(1 for x in offen
                  if br[x["ziel"]]["marke"] == m and x["klasse"] == GENERATION_CONFIRMED)
        print(f"  {m:16} {n:4} Zeilen, davon {gut:4} GENERATION_CONFIRMED")
    print()

    print("--- NICHT IMPORTIERBAR: SUCCESSOR_RECALL + CROSS_GENERATION ---")
    for x in offen:
        if x["klasse"] in (SUCCESSOR_RECALL, CROSS_GENERATION):
            print(f"  [{x['klasse']:17}] KBA {x['ref']:8} {x['ziel']:38} "
                  f"{x['von']}-{x['bis']}")
            print(f"      {x['grund']}")
    print()

    unklar = [x for x in offen if x["klasse"] == GENERATION_UNCLEAR]
    je_ziel = collections.Counter(x["ziel"] for x in unklar)
    print(f"--- GENERATION_UNCLEAR: {len(unklar)} Zeilen auf "
          f"{len(je_ziel)} Baureihen ---")
    for zid, n in sorted(je_ziel.items()):
        recherchiert = "" if zid not in GENERATIONEN else "  (Grenze bekannt, "\
                                                          "Fenster unbrauchbar)"
        print(f"  {zid:44} {n:3} Zeile(n){recherchiert}")

    if nur:
        print()
        print("=" * 78)
        print(f"DETAIL: {nur}")
        print("=" * 78)
        for x in offen:
            if x["klasse"] == nur:
                print(f"KBA {x['ref']:8} {x['marke']} {x['modell'][:34]}")
                print(f"    amtlich {x['von']}-{x['bis']}  ->  {x['ziel']} "
                      f"(ab {x['start']})")
                print(f"    {x['grund']}")
                print()


if __name__ == "__main__":
    main()
