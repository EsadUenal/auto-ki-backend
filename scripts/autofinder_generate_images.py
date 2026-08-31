"""
AutoFinder — Bildgenerierungs-Script (Runde 6, Teil N).

OFFLINE-/ADMIN-TOOL. Erzeugt GenerationJobs aus den intern benötigten
visual_keys (aus der kanonischen DB, Karosserie-erweitert) und führt sie
GENAU DANN wirklich aus, wenn `--execute` explizit gesetzt ist.

DEFAULT IST DRY-RUN — SICHER. Ein versehentlicher 678-Bilder-Call ist ohne
`--execute` technisch unmöglich: `generiere_bild()`/`fuehre_job_aus()` wird im
Dry-Run-Zweig gar nicht importiert/aufgerufen.

Beispiele:
    python scripts/autofinder_generate_images.py --limit 5
        (Dry-Run: zeigt die ersten 5 Jobs, ruft NICHTS auf)

    python scripts/autofinder_generate_images.py --visual-key bmw--3er--g20-g21--limousine --execute
        (genau EIN echter Generierungsversuch)

    python scripts/autofinder_generate_images.py --only-missing --limit 20 --execute
        (bis zu 20 Jobs für visual_keys ohne freigegebenes Asset, wirklich ausgeführt)
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.database as _db  # noqa: E402
from app.autofinder_norm import normalisiere_karosserie  # noqa: E402
from app.autofinder_generation import fuehre_job_aus, lade_jobs, neuer_job, speichere_jobs  # noqa: E402
from app.autofinder_visual import UNBEKANNTE_KAROSSERIE, lade_manifest_datei  # noqa: E402


def build_generation_jobs(*, visual_key: str | None = None, only_missing: bool = False,
                          limit: int | None = None) -> list:
    """Liest alle Baureihen aus der (bereits initialisierten) DB und baut
    GenerationJobs für jede Marke+Modell+Generation+Karosserie-Kombination —
    dieselbe Karosserie-Normalisierung wie der Resolver, keine zweite
    Taxonomie."""
    with _db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT marke, modell, generation, karosserie, bauzeitraum_von, "
            "bauzeitraum_bis FROM baureihe").fetchall()]

    manifest = lade_manifest_datei() if only_missing else {}
    jobs = []
    gesehen = set()
    for r in rows:
        klassen = normalisiere_karosserie(r["karosserie"]) or {UNBEKANNTE_KAROSSERIE}
        for karo in sorted(klassen):
            job = neuer_job(r["marke"], r["modell"], r["generation"], karo,
                            baujahr_von=r["bauzeitraum_von"], baujahr_bis=r["bauzeitraum_bis"])
            if job.visual_key in gesehen:
                continue
            gesehen.add(job.visual_key)
            if visual_key and job.visual_key != visual_key:
                continue
            if only_missing:
                eintrag = manifest.get(job.visual_key)
                if eintrag and eintrag.reviewed and eintrag.active:
                    continue
            jobs.append(job)
    if limit is not None:
        jobs = jobs[:limit]
    return jobs


async def _fuehre_aus(jobs: list, ziel_ordner: Path) -> list:
    ergebnis = []
    for job in jobs:
        print(f"  generiere {job.visual_key} ...", flush=True)
        job = await fuehre_job_aus(job, ziel_ordner)
        print(f"    -> {job.status}" + (f" ({job.error})" if job.error else f" ({job.output_path})"))
        ergebnis.append(job)
        if job.status == "failed" and job.error and ("503" in job.error or "UNAVAILABLE" in job.error):
            print("  Provider vorübergehend nicht erreichbar (503) — breche Batch ab, "
                  "kein Retry-Loop.")
            break
    return ergebnis


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--visual-key", default=None, help="nur diesen einen visual_key verarbeiten")
    ap.add_argument("--only-missing", action="store_true",
                    help="vorhandene freigegebene (reviewed+active) Assets überspringen")
    ap.add_argument("--limit", type=int, default=None, help="maximale Anzahl Jobs")
    ap.add_argument("--all", action="store_true", help="alle benötigten visual_keys (KEIN Limit)")
    ap.add_argument("--execute", action="store_true",
                    help="wirklich generieren (Default ohne dieses Flag: Dry-Run, 0 Provider-Calls)")
    ap.add_argument("--out", default=None, help="Zielordner für Rohbilder (Default: Scratchpad/Temp)")
    ap.add_argument("--jobs-file", default=None, help="Pfad zur Job-JSON (Default: app/data/...)")
    args = ap.parse_args()

    if not args.all and args.limit is None and not args.visual_key:
        print("Sicherheitsnetz: weder --all noch --limit noch --visual-key gesetzt — "
              "breche ab, statt versehentlich alles zu erzeugen.")
        sys.exit(1)

    os_temp = Path(args.out) if args.out else Path.cwd() / ".autofinder_generation_scratch"
    jobs = build_generation_jobs(visual_key=args.visual_key, only_missing=args.only_missing,
                                 limit=args.limit)
    print(f"Gebaute Jobs: {len(jobs)}" + (" (--all, kein Limit)" if args.all and args.limit is None else ""))
    for j in jobs[:10]:
        print(f"  - {j.visual_key}")
    if len(jobs) > 10:
        print(f"  ... (+{len(jobs) - 10} weitere)")

    if not args.execute:
        print("\nDRY-RUN (Default) — 0 Provider-Calls. Für echte Generierung --execute setzen.")
        return

    print(f"\nEXECUTE — starte {len(jobs)} echte Generierungsversuche nach {os_temp} ...")
    ausgefuehrt = asyncio.run(_fuehre_aus(jobs, os_temp))
    alle = lade_jobs(args.jobs_file) + ausgefuehrt
    speichere_jobs(alle, args.jobs_file)
    print(f"\n{sum(1 for j in ausgefuehrt if j.status == 'review_required')} review_required, "
          f"{sum(1 for j in ausgefuehrt if j.status == 'failed')} failed.")


if __name__ == "__main__":
    main()
