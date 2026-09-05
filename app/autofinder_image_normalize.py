from __future__ import annotations

"""
AutoFinder — Normalisierung der VIRA_LINE_ART_V1-Bilder (Runde: Bild-Konsistenz).

URSACHE (bestätigt per Pixel-Analyse mehrerer kuratierter + on-demand Assets):
Gemini liefert bereits konsistent 1376x768 (16:9), aber der tatsächliche
Fahrzeuganteil an der Fläche schwankt spürbar (~66-86 % der Bildhöhe, ~78-92 %
der Bildbreite, unterschiedliche Ober-/Unterränder) — das Prompt-Template
("occupies most of the available image area") ist keine harte Garantie.
Ein reiner CSS-/Container-Fix im Frontend kann diese ASSET-seitige Varianz
nicht beheben, weil der Bildinhalt selbst unterschiedlich viel Weißraum
mitbringt. Diese Datei behebt das an der Quelle: nach jeder Generierung
(offline UND on-demand) wird jedes Bild auf denselben Fahrzeug-Flächenanteil,
dieselbe Zentrierung und denselben Canvas normalisiert — deterministisch,
ohne KI, ohne zusätzlichen Gemini-Call.

Flow:
    Rohbild (beliebige Größe, weißer Hintergrund, dunkle Linien)
    -> Bounding Box der Nicht-Weiß-Pixel bestimmen (Threshold auf Graustufen —
       das Stilprompt erzwingt reines Schwarz/Grau ohne Farbe, siehe
       autofinder_generation.PROMPT_STYLE_VERSION)
    -> kleinen Padding-Rand um die Box ergänzen
    -> proportional (NIE gestreckt) so skalieren, dass die Box in ein
       Ziel-Fenster (Breite/Höhe-Anteil des Canvas) passt
    -> mittig auf einen frischen weißen Standard-Canvas einfügen
       (horizontal exakt mittig, vertikal geometrische Mitte + kleiner
       Versatz nach unten — vermeidet ein am oberen Rand "klebendes" Auto)

Reine Bildverarbeitung (Pillow), kein Netzwerk, kein Modellaufruf, keine
Rankings-/Preis-/Match-Logik. Wird von `app.autofinder_generation.schreibe_bild`
aufgerufen — EIN Aufrufpfad für offline generierte UND on-demand erzeugte
Bilder (siehe dortiger Kommentar).
"""

import logging

from PIL import Image

log = logging.getLogger(__name__)

# Bestehender Standard-Canvas der Pipeline (16:9) — bewusst beibehalten.
STANDARD_CANVAS: tuple[int, int] = (1376, 768)

# Ziel-Bounding-Box des Fahrzeugs relativ zum Canvas (§4 der Anforderung:
# 82-88 % Breite, max. ca. 70-76 % Höhe -> jeweils die Fenster-Mitte als
# Zielwert, die tatsächliche Grenze ergibt sich aus der Skalierung).
ZIEL_BREITE_ANTEIL = 0.85
ZIEL_HOEHE_MAX_ANTEIL = 0.73

# Kleiner Puffer um die erkannte Inhalts-Box, BEVOR skaliert wird — verhindert,
# dass eine Linie exakt auf dem Canvas-Zielrand landet (§2 "Padding-Rand").
INHALT_PADDING_ANTEIL = 0.02

# Vertikaler Versatz nach unten relativ zur geometrischen Mitte (§5: "vertikal
# optisch mittig bzw. minimal tiefer als geometrische Mitte").
VERTIKALER_VERSATZ_ANTEIL = 0.02

# Pixel gilt als "Inhalt", wenn die Graustufe spürbar unter Weiß liegt.
# Anti-Aliasing erzeugt helle Grautöne knapp unter 255 — 246 lässt reines Weiß
# (auch mit leichtem WebP-Rauschen) unangetastet, erfasst aber schon schwache
# Kanten (§3, empirisch an den vorhandenen Assets geprüft: alle 39 geprüften
# Bilder ergaben damit plausible Boxen von 66-92 % Flächenanteil, keine
# Ausreißer durch Rauschen).
WEISS_SCHWELLE = 246


def _inhalts_bbox(rgb: Image.Image, schwelle: int = WEISS_SCHWELLE) -> tuple[int, int, int, int] | None:
    """Bounding Box (x0, y0, x1, y1, x1/y1 exklusiv) der Nicht-Weiß-Pixel, oder
    None, wenn keiner gefunden wurde. Reine Klassifikation für die Suche —
    verändert nie die eigentlichen Bilddaten (§B: Anti-Aliasing-Grauwerte
    bleiben im Ergebnisbild unangetastet, nur die Erkennung nutzt Graustufen)."""
    grau = rgb.convert("L")
    binaer = grau.point(lambda p: 255 if p < schwelle else 0)
    return binaer.getbbox()


def normalize_lineart_image(
    quelle: Image.Image,
    *,
    canvas_size: tuple[int, int] = STANDARD_CANVAS,
    ziel_breite_anteil: float = ZIEL_BREITE_ANTEIL,
    ziel_hoehe_max_anteil: float = ZIEL_HOEHE_MAX_ANTEIL,
) -> Image.Image:
    """
    Normalisiert ein VIRA_LINE_ART_V1-Bild: erkennt den Fahrzeuginhalt,
    skaliert ihn proportional auf einen einheitlichen Flächenanteil und
    zentriert ihn auf einem frischen weißen Standard-Canvas.

    - NIE strecken (Seitenverhältnis des Inhalts bleibt exakt erhalten).
    - Fahrzeug wird NIE abgeschnitten (die skalierte Box passt immer
      vollständig in den Ziel-Bereich, der wiederum immer in den Canvas passt).
    - Deterministisch, keine KI, keine Zufallszahlen.
    - Findet die Funktion keinen Inhalt (z. B. rein weißes Bild), wird das
      Quellbild unverändert proportional in den Canvas eingepasst und
      zentriert zurückgegeben — nie ein Fehler, nie ein leeres Ergebnis.
    """
    rgb = quelle.convert("RGB")
    canvas_w, canvas_h = canvas_size

    bbox = _inhalts_bbox(rgb)
    if bbox is None:
        log.warning("autofinder_image_normalize: kein Inhalt erkannt — Bild nur zentriert, nicht neu skaliert")
        return _zentriert_ohne_skalierung(rgb, canvas_size)

    x0, y0, x1, y1 = bbox
    box_w, box_h = x1 - x0, y1 - y0

    # kleiner Padding-Rand um die erkannte Box, geklammert an die Bildgrenzen.
    # WICHTIG für Stabilität/Idempotenz (§G/§H): die Skalierung wird weiter
    # unten anhand der EXAKTEN (ungepaddeten) Box berechnet — der Puffer ist
    # nur eine zusätzliche Sicherheitsmarge beim Zuschneiden, damit eine Linie
    # nicht exakt auf der Crop-Kante landet. Würde die Skalierung stattdessen
    # auf die gepolsterte Box bezogen, würde jeder Durchlauf den erreichten
    # Fahrzeug-Flächenanteil erneut um denselben Faktor schrumpfen lassen
    # (kein stabiler Fixpunkt) — genau das vermeidet diese Trennung.
    pad_x = box_w * INHALT_PADDING_ANTEIL
    pad_y = box_h * INHALT_PADDING_ANTEIL
    px0 = max(0, x0 - pad_x)
    py0 = max(0, y0 - pad_y)
    px1 = min(rgb.width, x1 + pad_x)
    py1 = min(rgb.height, y1 + pad_y)

    inhalt = rgb.crop((int(px0), int(py0), int(round(px1)), int(round(py1))))
    cw, ch = inhalt.size
    if box_w < 2 or box_h < 2:
        return _zentriert_ohne_skalierung(rgb, canvas_size)

    # Skalierung: bezogen auf die EXAKTE Inhalts-Box (nicht die gepolsterte
    # Crop-Größe) — die kleinere der beiden Grenzen (Breite/Höhe) gewinnt,
    # damit NIE gestreckt und NIE über die Höhen-Obergrenze hinaus vergrößert
    # wird. Der Puffer wird anschließend mit demselben Faktor mitskaliert und
    # bleibt dadurch ein kleiner, aber praktisch konstanter Rand.
    ziel_breite_px = ziel_breite_anteil * canvas_w
    ziel_hoehe_px = ziel_hoehe_max_anteil * canvas_h
    scale = min(ziel_breite_px / box_w, ziel_hoehe_px / box_h)

    neue_w = max(1, round(cw * scale))
    neue_h = max(1, round(ch * scale))
    resample = Image.LANCZOS if hasattr(Image, "LANCZOS") else Image.BICUBIC
    skaliert = inhalt.resize((neue_w, neue_h), resample)

    ausgabe = Image.new("RGB", canvas_size, (255, 255, 255))
    ziel_x = (canvas_w - neue_w) // 2
    ziel_y = (canvas_h - neue_h) // 2 + round(canvas_h * VERTIKALER_VERSATZ_ANTEIL)
    # geklammert: auch bei extremen Eingaben nie außerhalb des Canvas einfügen
    ziel_y = max(0, min(ziel_y, canvas_h - neue_h))
    ausgabe.paste(skaliert, (ziel_x, ziel_y))
    return ausgabe


def _zentriert_ohne_skalierung(rgb: Image.Image, canvas_size: tuple[int, int]) -> Image.Image:
    """Fallback für den (seltenen) Fall ohne erkennbaren Inhalt: das Bild wird
    proportional in den Canvas eingepasst und zentriert, aber nicht anhand
    einer (nicht vorhandenen) Inhalts-Box skaliert."""
    canvas_w, canvas_h = canvas_size
    scale = min(canvas_w / rgb.width, canvas_h / rgb.height)
    neue_w = max(1, round(rgb.width * scale))
    neue_h = max(1, round(rgb.height * scale))
    resample = Image.LANCZOS if hasattr(Image, "LANCZOS") else Image.BICUBIC
    skaliert = rgb.resize((neue_w, neue_h), resample)
    ausgabe = Image.new("RGB", canvas_size, (255, 255, 255))
    ausgabe.paste(skaliert, ((canvas_w - neue_w) // 2, (canvas_h - neue_h) // 2))
    return ausgabe


# ── QA nach Normalisierung (§8) ──────────────────────────────────────────────

class NormalisierungsQAResultat:
    def __init__(self, ok: bool, grund: str = "ok"):
        self.ok = ok
        self.grund = grund

    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self) -> str:  # pragma: no cover
        return f"NormalisierungsQAResultat(ok={self.ok}, grund={self.grund!r})"


def pruefe_normalisiertes_bild(
    ausgabe: Image.Image,
    *,
    canvas_size: tuple[int, int] = STANDARD_CANVAS,
) -> NormalisierungsQAResultat:
    """Mechanische Nachprüfung (§8): Canvas korrekt, Inhalts-Box vorhanden,
    nicht pathologisch klein/groß, nicht abgeschnitten (liegt durch
    Konstruktion bereits vollständig innerhalb des Canvas — hier zusätzlich
    verifiziert, falls die Funktion mit einem fremden/manipulierten Bild
    aufgerufen wird)."""
    if ausgabe.size != canvas_size:
        return NormalisierungsQAResultat(False, f"falscher Canvas {ausgabe.size} != {canvas_size}")
    bbox = _inhalts_bbox(ausgabe)
    if bbox is None:
        return NormalisierungsQAResultat(False, "keine Inhalts-Box im normalisierten Bild")

    x0, y0, x1, y1 = bbox
    canvas_w, canvas_h = canvas_size
    breiten_anteil = (x1 - x0) / canvas_w
    hoehen_anteil = (y1 - y0) / canvas_h
    if breiten_anteil < 0.5:
        return NormalisierungsQAResultat(False, f"Fahrzeug-BBox zu schmal ({breiten_anteil:.2f})")
    if hoehen_anteil < 0.35:
        return NormalisierungsQAResultat(False, f"Fahrzeug-BBox zu niedrig ({hoehen_anteil:.2f})")
    if breiten_anteil > 0.98 or hoehen_anteil > 0.95:
        return NormalisierungsQAResultat(False, f"Weißraum pathologisch klein — evtl. abgeschnitten ({breiten_anteil:.2f}x{hoehen_anteil:.2f})")
    if x0 <= 0 or y0 <= 0 or x1 >= canvas_w or y1 >= canvas_h:
        return NormalisierungsQAResultat(False, "Inhalt berührt den Canvas-Rand — evtl. abgeschnitten")
    return NormalisierungsQAResultat(True)
