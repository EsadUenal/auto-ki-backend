from __future__ import annotations

"""
AutoFinder — Bildgenerierung (Runde 6).

OFFLINE-/ADMIN-PROZESS. Diese Datei wird NIE aus dem AutoFinder-HTTP-Pfad
importiert oder aufgerufen — nur von `scripts/autofinder_generate_images.py`
und `scripts/autofinder_review_images.py`. Kein AutoFinder-Request kann eine
Bildgenerierung auslösen.

MODELL
------
Verifiziert per echtem `client.models.list()`-Aufruf gegen den produktiven
Account (2026-08-31): `gemini-3.1-flash-lite-image` ist verfügbar — das
laut offizieller Google-Preisliste günstigste Bildmodell. Kein erfundener
Name; überschreibbar via `AUTO_KI_IMAGE_MODEL`, falls Google den Namen
ändert. Genutzt wird der bestehende `client.aio.models.generate_content()`-
Pfad mit `response_modalities=["IMAGE"]` — dieselbe Aufrufform wie die
Text-Generierung in `app.car_lookup`, keine zweite Client-Architektur, aber
strukturell klar von ihr getrennt: kein gemeinsamer Code-Pfad mit Text-
Antworten, kein `call_gemini_json`-Reuse für Bilddaten.

GENERIERT != FREIGEGEBEN (§G)
-------------------------------
Jeder Lauf endet in `review_required`, NIE automatisch `approved`. Nur das
Review-CLI darf das produktive Manifest schreiben (reviewed=true,
active=true) — siehe `app.autofinder_visual`.

KEIN RETRY-LOOP (§I)
----------------------
Ein Admin-Batch-Prozess soll bei einem Fehlschlag zum nächsten Key
weitergehen, nicht minutenlang blockieren. Genau EIN Versuch pro Bild; ein
Fehler markiert den Job als `failed` und wird geloggt, nie erneut versucht.
"""

import base64
import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.genai import types as genai_types

from app.autofinder_norm import KAROSSERIE_KLASSEN
from app.autofinder_visual import UNBEKANNTE_KAROSSERIE, visual_key_v2
from app.car_lookup import get_gemini_client
from app.config import GEMINI_API_KEY

log = logging.getLogger(__name__)

IMAGE_MODEL = os.environ.get("AUTO_KI_IMAGE_MODEL", "gemini-3.1-flash-lite-image")

# ── Status ───────────────────────────────────────────────────────────────
PENDING = "pending"
GENERATED = "generated"
REVIEW_REQUIRED = "review_required"
APPROVED = "approved"
REJECTED = "rejected"
FAILED = "failed"
STATUS_WERTE = (PENDING, GENERATED, REVIEW_REQUIRED, APPROVED, REJECTED, FAILED)


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class GenerationJob:
    visual_key: str
    marke: str
    modell: str
    generation: str | None
    karosserie: str
    baujahr_von: int | None = None
    baujahr_bis: int | None = None
    provider: str = "google-genai"
    model: str = IMAGE_MODEL
    status: str = PENDING
    output_path: str | None = None
    error: str | None = None
    created_at: str = field(default_factory=_jetzt)
    updated_at: str = field(default_factory=_jetzt)

    def markiere(self, status: str, *, output_path: str | None = None, error: str | None = None) -> None:
        if status not in STATUS_WERTE:
            raise ValueError(f"unbekannter GenerationStatus: {status!r}")
        self.status = status
        self.output_path = output_path
        self.error = error
        self.updated_at = _jetzt()


def neuer_job(marke: str, modell: str, generation: str | None, karosserie: str,
              *, baujahr_von: int | None = None, baujahr_bis: int | None = None) -> GenerationJob:
    """Baut einen Job inkl. `visual_key` über dieselbe Funktion, die auch der
    Resolver nutzt (`app.autofinder_visual.visual_key_v2`) — eine Quelle der
    Wahrheit, kein zweiter Key-Algorithmus."""
    key = visual_key_v2(marke, modell, generation, [karosserie], bevorzugte_karosserie=karosserie)
    return GenerationJob(visual_key=key, marke=marke, modell=modell, generation=generation,
                         karosserie=karosserie, baujahr_von=baujahr_von, baujahr_bis=baujahr_bis)


# ══════════════════════════════════════════════════════════════════════════
# STANDARDISIERTES PROMPT-TEMPLATE (§E)
# ══════════════════════════════════════════════════════════════════════════

_KAROSSERIE_EN = {
    "kleinwagen": "small city car / supermini",
    "kompakt": "compact hatchback",
    "limousine": "sedan / saloon",
    "kombi": "station wagon / estate",
    "suv": "SUV",
    "van": "minivan / MPV",
    "coupe": "coupe",
    "cabrio": "convertible",
    "pickup": "pickup truck",
    UNBEKANNTE_KAROSSERIE: "passenger car",
}

# Stil-Version — EIN Template, keine Variation zwischen Bildern (§9 Bildaudit:
# konsistente Perspektive/Hintergrund über die gesamte Bibliothek).
#
# VIRA_LINE_ART_V1 löst den vorherigen fotorealistischen Studio-Prompt ab.
# Begründung: 2 reale Gemini-Proof-Runs (je 5 Fahrzeuge) — der fotorealistische
# Ansatz brachte prominente echte Herstellerlogos; der Line-Art-Ansatz ist
# marken­rechtlich risikoärmer, karten-tauglicher und liefert konsistentere
# Perspektive. Es gibt bewusst nur EINEN Default-Stil.
PROMPT_STYLE_VERSION = "VIRA_LINE_ART_V1"

_STIL_SUFFIX = (
    "Accurately reproduce the recognizable proportions, silhouette, body shape, "
    "roofline, headlights, grille shape and major exterior characteristics of "
    "this exact vehicle generation and body style. "
    "Do NOT mix styling elements from another generation. "
    "Full vehicle visible in a clean slight 3/4 front view. Centered tightly in "
    "frame. Vehicle occupies most of the available image area. Do not crop any "
    "part of the vehicle. "
    "Pure solid white background. Use ONLY very thin, clean BLACK linework. "
    "NO BLUE. NO COLOR. NO SHADING. NO REALISTIC TEXTURES. NO GRADIENTS. "
    "NO STREET. NO FLOOR LINE. NO ENVIRONMENT. NO PEOPLE. NO TEXT. NO WATERMARK. "
    "CRITICAL BRAND-NEUTRALIZATION: Do not draw any manufacturer logo, "
    "manufacturer emblem, brand symbol, model badge, lettering or wheel-center "
    "logos. Do not even draw an empty circular badge placeholder, badge mounting "
    "shape or emblem holder on the grille or hood. Replace any emblem location "
    "with the natural uninterrupted grille/body geometry. "
    "LICENSE PLATE: Do not draw a license plate, a blank license plate, a "
    "rectangular license-plate placeholder, or a plate frame or plate holder. "
    "The bumper/grille must continue naturally through the normal license-plate "
    "area. "
    "Do not add tuning parts. Standard non-tuned production trim. "
    "Vector-like clean technical illustration. Uniform thin line thickness. "
    "Easy to crop/isolate. Landscape 16:9 composition."
)


def baue_prompt(marke: str, modell: str, generation: str | None, karosserie: str,
                 baujahr_von: int | None = None, baujahr_bis: int | None = None) -> str:
    """Genau EIN Prompt-Template für alle AutoFinder-Bilder (Stil:
    VIRA_LINE_ART_V1). Karosserie ist PFLICHT im Text (§C/§E: "richtige
    Karosserie zwingend"), Generation/Baujahr fließen ein, soweit bekannt —
    nie erfunden, nur weitergegeben, was der Kandidat tatsächlich trägt."""
    karo_en = _KAROSSERIE_EN.get(karosserie, karosserie)
    gen_teil = f", generation/chassis code {generation}" if generation else ""
    if baujahr_von and baujahr_bis:
        jahr_teil = f", model years {baujahr_von}-{baujahr_bis}"
    elif baujahr_von:
        jahr_teil = f", model year {baujahr_von} onward"
    else:
        jahr_teil = ""
    fahrzeug = (f"Clean minimalist line-art illustration of a {marke} {modell}"
                f"{gen_teil}{jahr_teil}, {karo_en} body style.")
    return f"{fahrzeug} {_STIL_SUFFIX}"


# ══════════════════════════════════════════════════════════════════════════
# PROVIDER-AUFRUF — EIN Versuch, kein Retry-Loop (§I)
# ══════════════════════════════════════════════════════════════════════════

async def generiere_bild(job: GenerationJob) -> tuple[bytes | None, str | None, str | None]:
    """Genau EIN Generierungsversuch. Gibt (bild_bytes, mime_type, fehler)
    zurück — bei Erfolg ist `fehler` None, bei Fehlschlag sind die ersten
    beiden None. Wirft NIE — jeder Fehler wird als String zurückgegeben,
    der Aufrufer setzt daraus `GenerationJob.status = failed`."""
    if not GEMINI_API_KEY:
        return None, None, "GEMINI_API_KEY nicht gesetzt"
    prompt = baue_prompt(job.marke, job.modell, job.generation, job.karosserie,
                         job.baujahr_von, job.baujahr_bis)
    # `person_generation`/`prominent_people` sind laut SDK nur im Vertex-AI-
    # Enterprise-Modus gültig, nicht in der Gemini Developer API (empirisch
    # geprüft: ValueError bei jedem Versuch). "Keine Personen" wird deshalb
    # ausschließlich über den Prompttext durchgesetzt (siehe _STIL_SUFFIX).
    cfg = genai_types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        image_config=genai_types.ImageConfig(aspect_ratio="16:9", image_size="1K"),
    )
    try:
        client = get_gemini_client()
        response = await client.aio.models.generate_content(
            model=job.model, contents=prompt, config=cfg)
    except Exception as exc:
        return None, None, f"{type(exc).__name__}: {exc}"

    try:
        kandidaten = response.candidates or []
        for teil in (kandidaten[0].content.parts if kandidaten and kandidaten[0].content else []):
            inline = getattr(teil, "inline_data", None)
            if inline and inline.data:
                return inline.data, (inline.mime_type or "image/png"), None
    except Exception as exc:
        return None, None, f"Antwort ohne verwertbares Bild: {exc}"
    return None, None, "keine Bilddaten in der Antwort (evtl. Sicherheitsfilter)"


# ══════════════════════════════════════════════════════════════════════════
# OUTPUT — Datei schreiben (§F). WebP-Konvertierung nur wenn Pillow
# verfügbar; sonst Rohformat (PNG) speichern statt hart zu scheitern.
# ══════════════════════════════════════════════════════════════════════════

def schreibe_bild(bild_bytes: bytes, mime_type: str, ziel_ordner: Path, visual_key: str) -> Path:
    """Schreibt die Rohdaten, normalisiert (Bild-Konsistenz-Runde: einheitlicher
    Fahrzeug-Flächenanteil/Zentrierung, siehe app.autofinder_image_normalize)
    und konvertiert nach WebP — wenn Pillow vorhanden. Gibt den tatsächlich
    geschriebenen Pfad zurück. KEINE Metadaten/Secrets im Dateinamen — nur der
    `visual_key` (§F). EIN Aufrufpfad für offline generierte (§scripts) UND
    on-demand erzeugte Bilder (app.autofinder_images) — beide normalisieren
    dadurch automatisch, ohne zusätzlichen Gemini-Call."""
    ziel_ordner.mkdir(parents=True, exist_ok=True)
    roh_ext = ".png" if "png" in mime_type else (".jpg" if "jpeg" in mime_type else ".bin")
    roh_pfad = ziel_ordner / f"{visual_key}{roh_ext}"
    roh_pfad.write_bytes(bild_bytes)
    try:
        from PIL import Image

        from app.autofinder_image_normalize import normalize_lineart_image
        webp_pfad = ziel_ordner / f"{visual_key}.webp"
        with Image.open(roh_pfad) as img:
            normalisiert = normalize_lineart_image(img)
        normalisiert.save(webp_pfad, "WEBP", quality=85, method=6)
        return webp_pfad
    except ImportError:
        log.warning("AutoFinder-Generation: Pillow nicht installiert — "
                    "Rohformat (%s) wird nicht nach WebP konvertiert/normalisiert.", roh_ext)
        return roh_pfad
    except Exception:
        log.exception("AutoFinder-Generation: WebP-Konvertierung/Normalisierung fehlgeschlagen — "
                      "Rohdatei bleibt bestehen: %s", roh_pfad)
        return roh_pfad


async def fuehre_job_aus(job: GenerationJob, ziel_ordner: Path) -> GenerationJob:
    """Orchestriert EINEN Job: generieren -> schreiben -> review_required
    ODER failed. Nie approved (§G). Mutiert und gibt denselben Job zurück."""
    bild_bytes, mime_type, fehler = await generiere_bild(job)
    if fehler or not bild_bytes:
        job.markiere(FAILED, error=fehler or "leere Antwort")
        return job
    try:
        pfad = schreibe_bild(bild_bytes, mime_type or "image/png", ziel_ordner, job.visual_key)
    except Exception as exc:
        job.markiere(FAILED, error=f"Schreiben fehlgeschlagen: {exc}")
        return job
    job.markiere(REVIEW_REQUIRED, output_path=str(pfad))
    return job


# ══════════════════════════════════════════════════════════════════════════
# JOB-PERSISTENZ — datei-/JSON-basiert, atomarer Write (§D/§H)
# ══════════════════════════════════════════════════════════════════════════

_JOBS_PFAD = Path(__file__).resolve().parent / "data" / "autofinder_generation_jobs.json"


def lade_jobs(pfad: Path | str | None = None) -> list[GenerationJob]:
    ziel = Path(pfad) if pfad else _JOBS_PFAD
    if not ziel.exists():
        return []
    rohliste = json.loads(ziel.read_text(encoding="utf-8"))
    return [GenerationJob(**r) for r in rohliste]


def speichere_jobs(jobs: list[GenerationJob], pfad: Path | str | None = None) -> None:
    """Atomarer Write: erst in eine Temp-Datei im selben Verzeichnis, dann
    per `os.replace` (atomar auf demselben Dateisystem) — ein Absturz
    mitten im Schreiben kann die bestehende Datei nie beschädigen (§H/§K)."""
    ziel = Path(pfad) if pfad else _JOBS_PFAD
    ziel.parent.mkdir(parents=True, exist_ok=True)
    rohliste = [asdict(j) for j in jobs]
    fd, tmp_pfad = tempfile.mkstemp(dir=str(ziel.parent), prefix=".jobs_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(rohliste, f, ensure_ascii=False, indent=2)
        os.replace(tmp_pfad, ziel)
    except Exception:
        try:
            os.unlink(tmp_pfad)
        except OSError:
            pass
        raise
