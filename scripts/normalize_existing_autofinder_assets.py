"""
Einmaliges Skript: bestehende AutoFinder-VIRA_LINE_ART_V1-Assets normalisieren
(Bild-Konsistenz-Runde). Wendet `normalize_lineart_image` auf JEDE übergebene
.webp-Datei an und überschreibt sie in-place (WebP, quality=85, method=6 —
identisch zu `app.autofinder_generation.schreibe_bild`).

Betrifft NUR:
  A) kuratierte AutoFinder-Line-Art-Bilder (Frontend: public/cars/autofinder/*.webp)
  B) on-demand gecachte AutoFinder-Bilder (Backend: app/data/autofinder_images/*.webp)

KEINE anderen Bilder (Entdecken-Assets, Ersatzteile, etc.) — bewusst per
expliziter Verzeichnisangabe, kein globales Durchsuchen.

Keine Git-Backups nötig — Git selbst ist Versionshistorie für getrackte
Assets (siehe Aufgabenstellung).

Aufruf:
    python scripts/normalize_existing_autofinder_assets.py <verzeichnis> [<verzeichnis> ...]

Beispiel:
    python scripts/normalize_existing_autofinder_assets.py \
        app/data/autofinder_images \
        "../../../auto-ki-web/public/cars/autofinder"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from app.autofinder_image_normalize import normalize_lineart_image, pruefe_normalisiertes_bild  # noqa: E402


def normalisiere_verzeichnis(verzeichnis: Path) -> list[str]:
    zeilen: list[str] = []
    dateien = sorted(verzeichnis.glob("*.webp"))
    if not dateien:
        zeilen.append(f"  (keine .webp-Dateien in {verzeichnis})")
        return zeilen
    for pfad in dateien:
        with Image.open(pfad) as im:
            vorher = im.size
            normalisiert = normalize_lineart_image(im)
        qa = pruefe_normalisiertes_bild(normalisiert)
        normalisiert.save(pfad, "WEBP", quality=85, method=6)
        status = "OK" if qa.ok else f"QA-WARNUNG ({qa.grund})"
        zeilen.append(f"  [{status}] {pfad.name}  ({vorher} -> {normalisiert.size})")
    return zeilen


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    gesamt_dateien = 0
    for roh in argv:
        verzeichnis = Path(roh).resolve()
        if not verzeichnis.is_dir():
            print(f"Übersprungen (kein Verzeichnis): {verzeichnis}")
            continue
        print(f"== {verzeichnis} ==")
        zeilen = normalisiere_verzeichnis(verzeichnis)
        for z in zeilen:
            print(z)
        gesamt_dateien += len([z for z in zeilen if z.startswith("  [")])
    print(f"\nFertig — {gesamt_dateien} Datei(en) normalisiert.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
