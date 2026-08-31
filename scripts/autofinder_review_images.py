"""
AutoFinder — Review-CLI (Runde 6, Teil H).

Kleines internes Tool, KEINE Admin-Webapp. Einziger Weg, ein generiertes Bild
in das produktive Manifest aufzunehmen — "generiert" heißt nie automatisch
"freigegeben".

    python scripts/autofinder_review_images.py list
    python scripts/autofinder_review_images.py approve <visual_key>
    python scripts/autofinder_review_images.py reject <visual_key> [--grund "..."]

`approve` schreibt einen Manifest-Eintrag (reviewed=true, active=true,
image_type=generated_cached, image_confidence=exact, ai_generated=true) und
validiert VOR dem Schreiben gegen Duplikate/kaputte Enums
(`autofinder_visual.speichere_manifest_datei`). `reject` markiert den Job nur
als `rejected` — es entsteht NIE ein aktiver Manifest-Eintrag.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.autofinder_generation import APPROVED, GENERATED, REJECTED, REVIEW_REQUIRED, lade_jobs, speichere_jobs  # noqa: E402
from app.autofinder_visual import ManifestEintrag, lade_manifest_datei, speichere_manifest_datei  # noqa: E402


def _finde_job(jobs, visual_key):
    kandidaten = [j for j in jobs if j.visual_key == visual_key
                  and j.status in (GENERATED, REVIEW_REQUIRED)]
    return kandidaten[-1] if kandidaten else None


def cmd_list(args) -> None:
    jobs = lade_jobs(args.jobs_file)
    offen = [j for j in jobs if j.status in (GENERATED, REVIEW_REQUIRED)]
    if not offen:
        print("Keine Jobs mit review_required.")
        return
    for j in offen:
        print(f"{j.visual_key:45s} {j.marke} {j.modell} {j.generation or '-':10s} "
              f"{j.karosserie:10s} -> {j.output_path}")


def cmd_approve(args) -> None:
    jobs = lade_jobs(args.jobs_file)
    job = _finde_job(jobs, args.visual_key)
    if job is None:
        print(f"Kein review_required-Job für {args.visual_key!r} gefunden.")
        sys.exit(1)
    if not job.output_path:
        print(f"Job {args.visual_key!r} hat keinen output_path — kann nicht freigegeben werden.")
        sys.exit(1)

    manifest = dict(lade_manifest_datei())
    manifest[job.visual_key] = ManifestEintrag(
        visual_key=job.visual_key,
        # Finaler Auslieferungspfad (Frontend-Konvention, §F) — der lokale
        # output_path bleibt nur Belegpfad in den Jobs, nicht im Manifest.
        image_url=f"/cars/autofinder/{job.visual_key}.webp",
        image_type="generated_cached", image_confidence="exact",
        marke=job.marke, modell=job.modell, generation=job.generation,
        karosserie=job.karosserie, ai_generated=True, reviewed=True, active=True,
        provider=job.provider, model=job.model, created_at=job.created_at,
    )
    speichere_manifest_datei(manifest, args.manifest_file)
    job.markiere(APPROVED, output_path=job.output_path)
    speichere_jobs(jobs, args.jobs_file)
    print(f"Freigegeben: {job.visual_key} -> {manifest[job.visual_key].image_url}")
    print("HINWEIS: Die eigentliche Bilddatei muss noch manuell nach "
          f"public/cars/autofinder/{job.visual_key}.webp kopiert werden — "
          "dieses CLI schreibt nur das Manifest, keine Frontend-Assets.")


def cmd_reject(args) -> None:
    jobs = lade_jobs(args.jobs_file)
    job = _finde_job(jobs, args.visual_key)
    if job is None:
        print(f"Kein review_required-Job für {args.visual_key!r} gefunden.")
        sys.exit(1)
    job.markiere(REJECTED, error=args.grund)
    speichere_jobs(jobs, args.jobs_file)
    manifest = lade_manifest_datei()
    if job.visual_key in manifest:
        print(f"WARNUNG: {job.visual_key} steht bereits im Manifest — "
              "reject entfernt bestehende Freigaben nicht automatisch.")
    print(f"Abgelehnt: {job.visual_key}" + (f" ({args.grund})" if args.grund else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jobs-file", default=None)
    ap.add_argument("--manifest-file", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(func=cmd_list)
    p_approve = sub.add_parser("approve")
    p_approve.add_argument("visual_key")
    p_approve.set_defaults(func=cmd_approve)
    p_reject = sub.add_parser("reject")
    p_reject.add_argument("visual_key")
    p_reject.add_argument("--grund", default=None)
    p_reject.set_defaults(func=cmd_reject)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
