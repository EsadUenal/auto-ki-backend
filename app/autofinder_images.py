from __future__ import annotations

"""
AutoFinder — Bild-On-Demand (§Punkt 1 Quality-Enrichment-Runde).

Nach dem Ranking hat das Frontend eine finale Kandidatenliste. Für Kandidaten
OHNE freigegebenes `generated_cached`-Bild ruft es GENAU EINMAL
`POST /api/v1/autofinder/images/ensure` mit den fehlenden visual_keys.

Dieses Modul:
  - überspringt bereits vorhandene (Manifest reviewed+active) Keys,
  - erzeugt fehlende Bilder über die bestehende VIRA_LINE_ART_V1-Pipeline
    (`app.autofinder_generation`), 1 Versuch + maximal 1 Wiederholung,
  - prüft jedes Bild mit einer kleinen automatischen Qualitätsprüfung
    (gültig / plausible Größe / weißer Hintergrund / nicht leer),
  - schreibt akzeptierte Bilder in ein backend-lokales Verzeichnis und ins
    Manifest (image_url `/api/v1/autofinder/img/<key>`), damit die NÄCHSTE
    Suche sie über `resolve_image` direkt liefert (Cache),
  - gibt bei endgültigem Fehlschlag `status="failed"` zurück -> Frontend
    zeigt weiterhin das Symbolbild.

WICHTIG: NICHT aus dem Such-Endpunkt aufrufen — der bleibt bildgenerierungsfrei.
Nur der dedizierte Ensure-Endpunkt (und Admin-Skripte) nutzen dieses Modul.
Der Import von `app.autofinder_generation` erfolgt hier auf Modulebene; der
Router importiert `app.autofinder_images` bewusst LAZY im Ensure-Handler.
"""

import asyncio
import io
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.autofinder_generation import generiere_bild, neuer_job, schreibe_bild
from app.autofinder_norm import KAROSSERIE_KLASSEN
from app.autofinder_visual import (
    UNBEKANNTE_KAROSSERIE,
    ManifestEintrag,
    lade_manifest_datei,
    speichere_manifest_datei,
    visual_key_v2,
)

log = logging.getLogger(__name__)

# Backend-lokales Verzeichnis für on-demand erzeugte Bilder (die Starter-
# Library liegt weiterhin im Frontend-public/). Überschreibbar für Tests.
BILD_DIR = Path(os.environ.get(
    "AUTO_KI_AUTOFINDER_IMG_DIR",
    str(Path(__file__).resolve().parent / "data" / "autofinder_images"),
))

# URL-Präfix, unter dem der Router diese Bilder ausliefert (GET). Das Frontend
# erkennt an "/api/" -> Backend-Origin voranstellen.
URL_PREFIX = "/api/v1/autofinder/img"

_MAX_VERSUCHE = 2   # 1 Versuch + maximal 1 Wiederholung (§Punkt 1)
_ENSURE_DECKEL = 8  # nie mehr als die tatsächlich fehlenden finalen Bilder


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Kleine automatische Qualitätsprüfung ────────────────────────────────────

def pruefe_bild(bild_bytes: bytes) -> tuple[bool, str]:
    """Mechanische Mindestprüfung (kein Vision-Modell): gültiges Bild,
    landschaftsformatige plausible Größe, überwiegend weißer Hintergrund,
    nicht leer (Line-Art vorhanden). Modell-/Generations-/Logo-Prüfung leistet
    der Prompt — hier wird nur offensichtlicher Ausschuss abgefangen."""
    try:
        from PIL import Image
    except ImportError:
        return True, "PIL nicht verfügbar — Prüfung übersprungen"
    try:
        with Image.open(io.BytesIO(bild_bytes)) as im:
            im = im.convert("RGB")
            w, h = im.size
            if w < 400 or h < 240:
                return False, f"zu klein ({w}x{h})"
            if not (1.2 <= w / h <= 2.2):
                return False, f"unerwartetes Seitenverhältnis ({w}x{h})"
            klein = im.resize((48, 27))
            px = list(klein.getdata())
            ecken = [px[0], px[47], px[48 * 26], px[48 * 27 - 1]]
            if not all(min(p) > 235 for p in ecken):
                return False, "Hintergrund nicht weiß"
            dunkel = sum(1 for p in px if sum(p) < 3 * 180)
            anteil = dunkel / len(px)
            if anteil < 0.01:
                return False, "kein erkennbarer Fahrzeugumriss (fast leer)"
            if anteil > 0.55:
                return False, "zu viel dunkle Fläche (kein sauberes Line-Art)"
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, f"ungültiges Bild: {exc}"


# ── Ensure ─────────────────────────────────────────────────────────────────

def _normalisiere_karo(karo: str) -> str:
    k = (karo or "").strip().lower()
    return k if k in KAROSSERIE_KLASSEN else UNBEKANNTE_KAROSSERIE


async def _erzeuge_einen(item: dict[str, Any], ziel: Path) -> dict[str, Any]:
    """Erzeugt EIN Bild mit bis zu _MAX_VERSUCHE Anläufen + QA. Gibt ein
    AutoFinderImageResult-kompatibles Dict zurück."""
    karo = _normalisiere_karo(item.get("karosserie", ""))
    job = neuer_job(item["marke"], item["modell"], item.get("generation"), karo,
                    baujahr_von=item.get("baujahr_von"), baujahr_bis=item.get("baujahr_bis"))
    vkey = item["visual_key"]

    letzte_fehler = "unbekannt"
    for versuch in range(1, _MAX_VERSUCHE + 1):
        bytes_, mime, fehler = await generiere_bild(job)
        if fehler or not bytes_:
            letzte_fehler = fehler or "leere Antwort"
            if fehler and ("503" in fehler or "504" in fehler or "UNAVAILABLE" in fehler):
                log.warning("AutoFinder-Images: Provider 503/504 bei %s — Abbruch, kein Retry", vkey)
                break
            continue
        ok, grund = pruefe_bild(bytes_)
        if not ok:
            letzte_fehler = f"QA fehlgeschlagen: {grund}"
            log.info("AutoFinder-Images: %s Versuch %d verworfen (%s)", vkey, versuch, grund)
            continue
        try:
            pfad = schreibe_bild(bytes_, mime or "image/png", ziel, vkey)
        except Exception as exc:  # noqa: BLE001
            return {"visual_key": vkey, "status": "failed", "error": f"Schreiben: {exc}"}
        return {"visual_key": vkey, "status": "generated", "pfad": str(pfad),
                "image_url": f"{URL_PREFIX}/{vkey}",
                "image_type": "generated_cached", "ai_generated": True}

    return {"visual_key": vkey, "status": "failed", "error": letzte_fehler}


def _manifest_eintrag(item: dict[str, Any], vkey: str) -> ManifestEintrag:
    return ManifestEintrag(
        visual_key=vkey,
        image_url=f"{URL_PREFIX}/{vkey}",
        image_type="generated_cached",
        image_confidence="exact",
        marke=item["marke"], modell=item["modell"], generation=item.get("generation"),
        karosserie=_normalisiere_karo(item.get("karosserie", "")),
        ai_generated=True, reviewed=True, active=True,
        provider="google-genai", model=os.environ.get("AUTO_KI_IMAGE_MODEL", "gemini-3.1-flash-lite-image"),
        created_at=_jetzt(),
    )


async def ensure_images(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sorgt dafür, dass für jeden visual_key ein Bild existiert. Vorhandene
    (Manifest reviewed+active) werden übersprungen; nur fehlende werden
    erzeugt. Akzeptierte Bilder landen im Manifest (Cache für die nächste
    Suche). Rückgabe: Liste AutoFinderImageResult-kompatibler Dicts."""
    items = items[:_ENSURE_DECKEL]
    manifest = dict(lade_manifest_datei())
    ergebnisse: list[dict[str, Any]] = []
    zu_erzeugen: list[dict[str, Any]] = []

    for item in items:
        vkey = item.get("visual_key") or ""
        eintrag = manifest.get(vkey)
        if eintrag and eintrag.reviewed and eintrag.active:
            ergebnisse.append({
                "visual_key": vkey, "status": "ready", "image_url": eintrag.image_url,
                "image_type": eintrag.image_type, "ai_generated": eintrag.ai_generated,
            })
        else:
            zu_erzeugen.append(item)

    if not zu_erzeugen:
        return ergebnisse

    BILD_DIR.mkdir(parents=True, exist_ok=True)
    # Sequentiell — je EIN Provider-Call, kein paralleler Ansturm.
    neu_akzeptiert: list[tuple[dict, dict]] = []
    for item in zu_erzeugen:
        res = await _erzeuge_einen(item, BILD_DIR)
        ergebnisse.append({k: v for k, v in res.items() if k != "pfad" and k != "error"})
        if res["status"] == "generated":
            neu_akzeptiert.append((item, res))

    # Manifest EINMAL aktualisieren (atomarer Write, erneute Validierung).
    if neu_akzeptiert:
        for item, res in neu_akzeptiert:
            manifest[res["visual_key"]] = _manifest_eintrag(item, res["visual_key"])
        try:
            speichere_manifest_datei(manifest)
        except Exception:
            log.exception("AutoFinder-Images: Manifest-Schreiben fehlgeschlagen — "
                          "Bilder erzeugt, aber nicht gecacht")

    return ergebnisse


def bild_pfad(visual_key: str) -> Path | None:
    """Für den GET-Ausliefer-Endpunkt: Pfad zu einem on-demand erzeugten Bild
    (WebP bevorzugt), oder None. Kein Path-Traversal: nur der Basename zählt."""
    safe = Path(visual_key).name
    for ext in (".webp", ".png", ".jpg"):
        p = BILD_DIR / f"{safe}{ext}"
        if p.is_file():
            return p
    return None
