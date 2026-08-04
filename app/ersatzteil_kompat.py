from __future__ import annotations

"""
Ersatzteil-Kompatibilität (Reliability-Sprint §5).

Manueller Test: BMW M3 E92 + Bremsscheiben vorne -> es wurde ein Teil für einen
NORMALEN BMW E92 als "empfohlen" dargestellt. Fachlich falsch und sicherheits-
relevant: ein Chassiscode wie E92 allein bestätigt KEINE Kompatibilität — der M3
hat andere Bremsen als ein 320i/330i.

Dieses Modul unterscheidet strukturiert (Marke, Modell, Performance-Modell wie
M3/AMG/RS/GTI, Baureihe/Karosseriecode, Achse, ggf. Maße) und stuft ein Produkt
gegen das Zielfahrzeug ein:

  - confirmed  : positiv belegte Kompatibilität — darf als "Empfohlen" erscheinen.
  - uncertain  : nicht bestätigt — NIE empfehlen, klarer FIN/OE-Hinweis.
  - rejected   : klarer Widerspruch (falsche Performance-Variante, falsche Achse,
                 anderer Hersteller) — vollständig ausblenden.

KEIN Fahrzeug-Hardcoding für BMW M3 — allgemeine Performance-Marker & Achslogik.
"""

import re

# ── Performance-/High-Performance-Modellmarker (marken-übergreifend, allgemein) ──
# Normalisiert auf einen kanonischen Token, damit "M3" des Zielfahrzeugs gegen "M3"
# im Produkt matcht — und "M4"/"AMG" als ANDERE Performance-Variante auffällt.
# Bewusst nur EINDEUTIGE Marker: mehrdeutige Kürzel wie "S", "ST", "GR", "N" würden
# in Fließtext ("2 St.", "gr", ...) falsch anschlagen und normale Fahrzeuge fälschlich
# als Performance-Modell verwerfen. Lieber wenige, sichere Marker.
_PERFORMANCE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("m",      re.compile(r"\bm\s?[2-8]\b", re.I)),              # BMW M2..M8
    ("mperf",  re.compile(r"\bm\s?(135|140|235|240|340|440|550|760)\b", re.I)),
    ("amg",    re.compile(r"\bamg\b(?!\s*[- ]?line)", re.I)),    # Mercedes-AMG (nicht "AMG Line")
    ("rs",     re.compile(r"\brs\s?\d\b", re.I)),                # Audi RS3..RS7 (mit Ziffer)
    ("gti",    re.compile(r"\bgti\b", re.I)),
    ("gtd",    re.compile(r"\bgtd\b", re.I)),
    ("golfr",  re.compile(r"\bgolf\s?r\b", re.I)),
    ("typer",  re.compile(r"\btype[\s-]?r\b", re.I)),
    ("cupra",  re.compile(r"\bcupra\b", re.I)),
    ("abarth", re.compile(r"\babarth\b", re.I)),
    ("jcw",    re.compile(r"\bjcw\b|john cooper works", re.I)),
]

# Standard-Motorcode wie "320d", "330 i", "220 d", "c220d", "e350d" — Signal, dass
# ein Produkt für eine NORMALE (Nicht-Performance-)Variante gedacht ist. Das optionale
# führende Klassen-Kürzel deckt zusammengeschriebene Codes wie "c220d" ab.
_NONPERF_MOTOR = re.compile(r"\b[a-z]?\d{3}\s?[di]\b", re.I)

# Karosserie-/Chassiscode (E92, G20, W205, 8V, ...).
_CHASSIS = re.compile(r"\b([a-z]\d{1,3}|\d[a-z]\d?)\b", re.I)

# Marken (Kernbegriffe, lowercase). Bewusst kompakt — nur zur Hersteller-Widerspruchs-
# prüfung, kein Anspruch auf Vollständigkeit.
_MARKEN = frozenset({
    "bmw", "mercedes", "benz", "audi", "volkswagen", "vw", "opel", "ford", "toyota",
    "honda", "hyundai", "kia", "seat", "skoda", "peugeot", "renault", "fiat", "volvo",
    "tesla", "porsche", "mazda", "subaru", "citroen", "mini", "jaguar", "jeep",
    "dacia", "smart", "cupra", "suzuki", "mitsubishi", "nissan", "alfa", "romeo",
})

# Achs-Signalwörter.
_VORNE = ("vorne", "vorder", "vorderachse", "front", " va ", "va-", "achse vorn")
_HINTEN = ("hinten", "hinter", "hinterachse", "rear", " ha ", "ha-", "achse hint")


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t}


def _performance_marker(text: str) -> set[str]:
    """Menge kanonischer Performance-Marker in einem Text."""
    out: set[str] = set()
    for name, rx in _PERFORMANCE_PATTERNS:
        if rx.search(text or ""):
            out.add(name)
    return out


def _achse(text: str) -> str | None:
    t = f" {(text or '').lower()} "
    hat_v = any(w in t for w in _VORNE)
    hat_h = any(w in t for w in _HINTEN)
    if hat_v and not hat_h:
        return "vorne"
    if hat_h and not hat_v:
        return "hinten"
    return None


def _marken(text: str) -> set[str]:
    return _tokens(text) & _MARKEN


def _chassis_codes(text: str) -> set[str]:
    return {m.group(1).lower() for m in _CHASSIS.finditer(text or "")}


def parse_fahrzeug(fahrzeug: str) -> dict:
    """Strukturiert das Zielfahrzeug: Marke, Modell-Token, Performance-Marker,
    Chassiscodes."""
    return {
        "marken": _marken(fahrzeug),
        "tokens": _tokens(fahrzeug),
        "performance": _performance_marker(fahrzeug),
        "chassis": _chassis_codes(fahrzeug),
    }


def parse_bauteil(bauteil: str) -> dict:
    """Strukturiert das gesuchte Bauteil: Achse (vorne/hinten), Roh-Text."""
    return {
        "achse": _achse(bauteil),
        "text": (bauteil or "").lower(),
    }


def klassifiziere(fahrzeug: dict, bauteil: dict, produkt_text: str) -> tuple[str, str]:
    """Stuft ein Produkt gegen das Zielfahrzeug ein. Gibt (kompatibilitaet, grund).

    Reihenfolge: harte Widersprüche (Hersteller, Achse, Performance-Variante) zuerst;
    danach positive Bestätigung; sonst "uncertain" (Default — nie ungeprüft empfehlen).
    """
    p = (produkt_text or "").lower()

    # ── Harte Widersprüche ────────────────────────────────────────────────────
    fz_marken = fahrzeug.get("marken") or set()
    prod_marken = _marken(p)
    if fz_marken and prod_marken and not (fz_marken & prod_marken):
        return ("rejected", f"anderer Hersteller ({', '.join(sorted(prod_marken))})")

    teil_achse = bauteil.get("achse")
    prod_achse = _achse(p)
    if teil_achse and prod_achse and teil_achse != prod_achse:
        return ("rejected", f"andere Achse (Produkt: {prod_achse}, gesucht: {teil_achse})")

    fz_perf = fahrzeug.get("performance") or set()
    prod_perf = _performance_marker(p)
    fz_chassis = fahrzeug.get("chassis") or set()
    prod_chassis = _chassis_codes(p)
    chassis_match = bool(fz_chassis & prod_chassis)

    if fz_perf:
        # Zielfahrzeug ist ein Performance-Modell (z.B. M3).
        if prod_perf & fz_perf:
            # Gleicher Performance-Marker belegt -> bestätigt (Achse widersprach nicht).
            return ("confirmed", "Performance-Variante bestätigt")
        if prod_perf and not (prod_perf & fz_perf):
            return ("rejected", "andere Performance-Variante als das Zielfahrzeug")
        # Produkt nennt KEINEN Performance-Marker.
        if _NONPERF_MOTOR.search(p):
            # Klar für eine Standardvariante (z.B. 320d) -> passt NICHT zum M3.
            return ("rejected", "für Standardvariante, nicht das Performance-Modell")
        # Nicht genug Beleg, dass es das Performance-Teil ist -> nicht bestätigen.
        return ("uncertain", "Performance-Kompatibilität nicht bestätigt")

    # Zielfahrzeug ist eine Standardvariante.
    if prod_perf:
        # Nennt das Produkt AUCH eine Standard-Motorisierung (z.B. "E90 E92 320i M3"),
        # ist es ein gemischtes Angebot -> unsicher (nicht sicher passend, nicht sicher
        # falsch). Nennt es AUSSCHLIESSLICH die Performance-Variante, passt es nicht zur
        # Standardvariante -> ablehnen.
        if _NONPERF_MOTOR.search(p):
            return ("uncertain", "Angebot mischt Performance- und Standardvarianten")
        return ("rejected", "für Performance-Variante, nicht die Standardvariante")

    if chassis_match:
        return ("confirmed", "Baureihe/Karosseriecode passend")
    if fz_marken & prod_marken and (fahrzeug.get("tokens") & _tokens(p)):
        return ("confirmed", "Marke und Modell passend")
    return ("uncertain", "Zuordnung nicht eindeutig")


HINWEIS_UNCERTAIN = "Kompatibilität nicht bestätigt – vor Bestellung per FIN/OE-Nummer prüfen."
