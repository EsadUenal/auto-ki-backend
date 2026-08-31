from __future__ import annotations

"""
AutoFinder — Visual Foundation (Runde 5).

visual_key V2, ein Manifest fuer kuratierte/generierte Fahrzeugbilder, und ein
deterministischer Resolver, der jedem AutoFinder-Kandidaten (intern ODER
web_discovered) genau ein Bild zuordnet — echt, oder ein ehrlich gekennzeichnetes
Symbolbild. Kein Kandidat bleibt je ohne Bildfeld.

GRUNDREGEL: EXAKT HEISST EXAKT
--------------------------------
Ein Kombi bekommt NIE eine Limousine als "exact" ausgegeben, selbst wenn beide
zur selben Baureihe/Generation gehoeren. Karosserie ist Teil des visual_key —
ein Treffer ohne passende Karosserie ist bestenfalls `generation_match`, nie
`exact`.

KEIN GENERIERUNGS-CALL IM REQUEST-PFAD
-----------------------------------------
`resolve_image()` liest AUSSCHLIESSLICH das im Prozess gecachte Manifest. Kein
Dateisystemscan, kein Provider-Call, keine Exception, die je bis zum Router
durchschlagen darf — fehlt ein Bild, ist das Ergebnis der generische Fallback,
nicht ein Fehler.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.autofinder_norm import KAROSSERIE_KLASSEN

log = logging.getLogger(__name__)

# ── image_type / image_confidence — geschlossenes Vokabular ────────────────
IMAGE_TYPE_CURATED = "curated"
IMAGE_TYPE_GENERATED_CACHED = "generated_cached"
IMAGE_TYPE_GENERIC_FALLBACK = "generic_fallback"
IMAGE_TYPE_WERTE = (IMAGE_TYPE_CURATED, IMAGE_TYPE_GENERATED_CACHED, IMAGE_TYPE_GENERIC_FALLBACK)

CONF_EXACT = "exact"
CONF_GENERATION_MATCH = "generation_match"
CONF_MODEL_MATCH = "model_match"
CONF_REPRESENTATIVE = "representative"
IMAGE_CONFIDENCE_WERTE = (CONF_EXACT, CONF_GENERATION_MATCH, CONF_MODEL_MATCH, CONF_REPRESENTATIVE)

UNBEKANNTE_KAROSSERIE = "unbekannt"

# Deterministische Prioritaet, wenn ein Kandidat MEHRERE Karosserieklassen
# traegt (z.B. eine Baureihe mit Limousine+Kombi+Van) und keine vom Nutzer
# angefragte Klasse vorliegt, die die Wahl eindeutig macht. Reine Tie-Break-
# Reihenfolge, keine neue Taxonomie — die Klassen selbst kommen unveraendert
# aus app.autofinder_norm.KAROSSERIE_KLASSEN.
_KAROSSERIE_PRIORITAET = KAROSSERIE_KLASSEN  # ("kleinwagen","kompakt","limousine","kombi","suv","van","coupe","cabrio","pickup")


def _slug(text: str) -> str:
    """URL-/Dateiname-sicher, Unicode-faltend (dieselbe Grundidee wie
    `app.autofinder_web._normalisiere_beleg`, hier auf Identifier statt
    Fliesstext angewendet)."""
    umlaute = str(text or "").lower().translate(str.maketrans({
        "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
        "á": "a", "à": "a", "â": "a", "ã": "a", "å": "a",
        "é": "e", "è": "e", "ê": "e", "ë": "e",
        "í": "i", "ì": "i", "î": "i", "ï": "i",
        "ó": "o", "ò": "o", "ô": "o", "õ": "o",
        "ú": "u", "ù": "u", "û": "u",
        "ç": "c", "ñ": "n", "š": "s", "ž": "z", "č": "c",
    }))
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", umlaute)).strip("-")


def waehle_karosserie(karosserie_klassen: list[str] | None, *,
                       bevorzugte_karosserie: str | None = None) -> str:
    """Genau EINE Karosserieklasse fuer den visual_key (§2).

    Reihenfolge: (1) die vom Nutzer tatsaechlich angefragte Klasse, wenn sie
    zu den Klassen dieses Kandidaten gehoert — das ist die Klasse, die den
    Nutzer nachweislich interessiert UND fachlich zutrifft; (2) sonst die
    erste Klasse nach `_KAROSSERIE_PRIORITAET`, deterministisch, kein Raten;
    (3) `UNBEKANNTE_KAROSSERIE`, wenn der Kandidat gar keine Klasse traegt.
    """
    klassen = set(karosserie_klassen or [])
    if bevorzugte_karosserie and bevorzugte_karosserie in klassen:
        return bevorzugte_karosserie
    for k in _KAROSSERIE_PRIORITAET:
        if k in klassen:
            return k
    return UNBEKANNTE_KAROSSERIE


def visual_key_v2(marke: str, modell: str, generation: str | None,
                   karosserie_klassen: list[str] | None, *,
                   bevorzugte_karosserie: str | None = None) -> str:
    """Marke + Modell + Generation + Karosserie, deterministisch und
    kollisionsarm. Identische Semantik fuer internal_db- und
    web_discovered-Kandidaten — beide liefern dieselben vier Werte, keine
    DB-ID fliesst ein.

    Beispiel: bmw--3er--g20-g21--limousine
    """
    karo = waehle_karosserie(karosserie_klassen, bevorzugte_karosserie=bevorzugte_karosserie)
    teile = [_slug(marke), _slug(modell), _slug(generation or ""), karo]
    return "--".join(t for t in teile if t)


# ══════════════════════════════════════════════════════════════════════════
# MANIFEST
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class ManifestEintrag:
    visual_key: str
    image_url: str
    image_type: str
    image_confidence: str
    marke: str
    modell: str
    generation: str | None
    karosserie: str
    ai_generated: bool
    reviewed: bool
    active: bool
    provider: str | None = None
    model: str | None = None
    created_at: str | None = None
    version: int = 1


class ManifestValidationError(ValueError):
    """Wird beim Laden eines strukturell fehlerhaften Manifests geworfen —
    NIE zur Laufzeit einer einzelnen Anfrage, siehe Moduldoc."""


def _validiere_eintrag(roh: dict, index: int) -> ManifestEintrag:
    fehlt = [f for f in ("visual_key", "image_url", "image_type", "image_confidence",
                          "marke", "modell", "karosserie") if not roh.get(f)]
    if fehlt:
        raise ManifestValidationError(f"Eintrag #{index}: Pflichtfeld(er) fehlen: {fehlt}")
    if roh["image_type"] not in IMAGE_TYPE_WERTE:
        raise ManifestValidationError(
            f"Eintrag #{index} ({roh['visual_key']}): ungueltiger image_type {roh['image_type']!r}")
    if roh["image_confidence"] not in IMAGE_CONFIDENCE_WERTE:
        raise ManifestValidationError(
            f"Eintrag #{index} ({roh['visual_key']}): ungueltige image_confidence {roh['image_confidence']!r}")
    if roh["karosserie"] not in (*KAROSSERIE_KLASSEN, UNBEKANNTE_KAROSSERIE):
        raise ManifestValidationError(
            f"Eintrag #{index} ({roh['visual_key']}): unbekannte Karosserieklasse {roh['karosserie']!r}")
    return ManifestEintrag(
        visual_key=roh["visual_key"], image_url=roh["image_url"],
        image_type=roh["image_type"], image_confidence=roh["image_confidence"],
        marke=roh["marke"], modell=roh["modell"], generation=roh.get("generation"),
        karosserie=roh["karosserie"], ai_generated=bool(roh.get("ai_generated", True)),
        reviewed=bool(roh.get("reviewed", False)), active=bool(roh.get("active", False)),
        provider=roh.get("provider"), model=roh.get("model"),
        created_at=roh.get("created_at"), version=int(roh.get("version", 1)),
    )


def parse_manifest(rohliste: list[dict]) -> dict[str, ManifestEintrag]:
    """Parst + validiert eine Manifest-Liste. Wirft `ManifestValidationError`
    bei doppeltem `visual_key`, fehlenden Pflichtfeldern oder ungueltigem
    Enum-Wert — ein kaputtes Manifest darf nie still falsche/doppelte
    Eintraege in den Resolver durchreichen (§14 Test H/I)."""
    ergebnis: dict[str, ManifestEintrag] = {}
    for i, roh in enumerate(rohliste):
        eintrag = _validiere_eintrag(roh, i)
        if eintrag.visual_key in ergebnis:
            raise ManifestValidationError(f"doppelter visual_key: {eintrag.visual_key!r}")
        ergebnis[eintrag.visual_key] = eintrag
    return ergebnis


# In-Memory-Cache — EIN Laden pro Prozess/Aenderung, kein Dateisystemzugriff
# pro Request (§5 Vorgabe). `lade_manifest_datei` invalidiert bei Bedarf
# explizit (Tests/Admin), sonst bleibt der erste geladene Stand aktiv.
_MANIFEST_CACHE: dict[str, ManifestEintrag] | None = None
_MANIFEST_PFAD = Path(__file__).resolve().parent / "data" / "autofinder_visual_manifest.json"


def lade_manifest_datei(pfad: Path | str | None = None, *, force: bool = False) -> dict[str, ManifestEintrag]:
    """Laedt (und cached) das Manifest von der Platte. Eine fehlende Datei
    ist KEIN Fehler — leeres Manifest, jeder Kandidat faellt auf den
    generischen Fallback zurueck (V1 vor der ersten Kuratierung)."""
    global _MANIFEST_CACHE
    if _MANIFEST_CACHE is not None and not force:
        return _MANIFEST_CACHE
    ziel = Path(pfad) if pfad else _MANIFEST_PFAD
    if not ziel.exists():
        log.info("AutoFinder-Visual: kein Manifest unter %s — nur generische Fallbacks aktiv.", ziel)
        _MANIFEST_CACHE = {}
        return _MANIFEST_CACHE
    try:
        rohliste = json.loads(ziel.read_text(encoding="utf-8"))
        _MANIFEST_CACHE = parse_manifest(rohliste)
    except Exception:
        log.exception("AutoFinder-Visual: Manifest konnte nicht geladen werden — "
                      "falle auf leeres Manifest zurueck (nur generische Fallbacks).")
        _MANIFEST_CACHE = {}
    return _MANIFEST_CACHE


def invalidiere_manifest_cache() -> None:
    global _MANIFEST_CACHE
    _MANIFEST_CACHE = None


# ══════════════════════════════════════════════════════════════════════════
# GENERISCHER FALLBACK — eine Silhouette je Karosserieklasse
# ══════════════════════════════════════════════════════════════════════════
# Noch keine echten Assets (Teil 6: "noch keine aufwendigen Fahrzeugbilder
# noetig"). Der Pfad ist bereits final, damit ein spaeteres Hinzufuegen der
# Dateien selbst keine Code-Aenderung mehr braucht.
_FALLBACK_BASISPFAD = "/cars/autofinder/fallback"


def _generischer_fallback(karosserie: str) -> "ResolveErgebnis":
    key = karosserie if karosserie in KAROSSERIE_KLASSEN else UNBEKANNTE_KAROSSERIE
    return ResolveErgebnis(
        image_url=f"{_FALLBACK_BASISPFAD}/{key}.webp",
        image_type=IMAGE_TYPE_GENERIC_FALLBACK,
        image_confidence=CONF_REPRESENTATIVE,
        resolved_visual_key=f"fallback--{key}",
        fallback_used=True,
        ai_generated=False,
    )


# ══════════════════════════════════════════════════════════════════════════
# RESOLVER
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class ResolveErgebnis:
    image_url: str
    image_type: str
    image_confidence: str
    resolved_visual_key: str
    fallback_used: bool
    ai_generated: bool


def _freigegeben(eintrag: ManifestEintrag | None) -> bool:
    """Teil 11: reviewed=false darf NIE als exact-public Asset erscheinen."""
    return eintrag is not None and eintrag.reviewed and eintrag.active


def resolve_image(kandidat: Any, *, bevorzugte_karosserie: str | None = None,
                   manifest: dict[str, ManifestEintrag] | None = None) -> ResolveErgebnis:
    """Deterministischer Resolver (§5). Wirft NIE — jeder interne Fehler
    endet im generischen Fallback, damit ein Bildproblem nie die
    AutoFinder-Antwort gefaehrdet (§7)."""
    try:
        manifest = manifest if manifest is not None else lade_manifest_datei()
        marke = getattr(kandidat, "marke", "") or ""
        modell = getattr(kandidat, "modell", "") or ""
        generation = getattr(kandidat, "generation", None)
        karo_klassen = getattr(kandidat, "karosserie_klassen", None) or []
        karo = waehle_karosserie(karo_klassen, bevorzugte_karosserie=bevorzugte_karosserie)

        # Stufe 1: EXAKTER visual_key (Marke+Modell+Generation+Karosserie)
        exact_key = visual_key_v2(marke, modell, generation, karo_klassen,
                                   bevorzugte_karosserie=bevorzugte_karosserie)
        eintrag = manifest.get(exact_key)
        if _freigegeben(eintrag):
            return ResolveErgebnis(
                image_url=eintrag.image_url, image_type=eintrag.image_type,
                image_confidence=CONF_EXACT, resolved_visual_key=exact_key,
                fallback_used=False, ai_generated=eintrag.ai_generated,
            )

        # Stufe 2: kompatibles Asset derselben Baureihe, ANDERE Karosserie —
        # NIE als exact, hoechstens generation_match (§ Grundregel oben).
        _slug_m, _slug_mo = _slug(marke), _slug(modell)
        _slug_g = _slug(generation or "")
        for kand_eintrag in manifest.values():
            if not _freigegeben(kand_eintrag):
                continue
            if _slug(kand_eintrag.marke) == _slug_m and _slug(kand_eintrag.modell) == _slug_mo \
                    and _slug(kand_eintrag.generation or "") == _slug_g:
                return ResolveErgebnis(
                    image_url=kand_eintrag.image_url, image_type=kand_eintrag.image_type,
                    image_confidence=CONF_GENERATION_MATCH, resolved_visual_key=kand_eintrag.visual_key,
                    fallback_used=True, ai_generated=kand_eintrag.ai_generated,
                )

        # Stufe 3: gleiches Modell, andere Generation — schwaechste
        # kuratierte Stufe, danach nur noch der generische Fallback.
        for kand_eintrag in manifest.values():
            if not _freigegeben(kand_eintrag):
                continue
            if _slug(kand_eintrag.marke) == _slug_m and _slug(kand_eintrag.modell) == _slug_mo:
                return ResolveErgebnis(
                    image_url=kand_eintrag.image_url, image_type=kand_eintrag.image_type,
                    image_confidence=CONF_MODEL_MATCH, resolved_visual_key=kand_eintrag.visual_key,
                    fallback_used=True, ai_generated=kand_eintrag.ai_generated,
                )

        return _generischer_fallback(karo)
    except Exception:
        log.exception("AutoFinder-Visual: Resolver-Fehler — generischer Fallback.")
        try:
            return _generischer_fallback(waehle_karosserie(getattr(kandidat, "karosserie_klassen", None)))
        except Exception:
            return _generischer_fallback(UNBEKANNTE_KAROSSERIE)
