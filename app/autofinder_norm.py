from __future__ import annotations

"""
AutoFinder — Runtime-Normalisierung (Runde 1 Fundament).

WOZU
----
`baureihe.karosserie` und `motorvariante.getriebe` sind JSON-Arrays aus
Freitext-Rohwerten ("Schrägheck 5-Türer", "AMG SPEEDSHIFT TCT 9G", …),
`baureihe.segment` ist ein einzelnes Freitextfeld mit 37 verschiedenen
Rohwerten. Für harte AutoFinder-Filter ("zeig mir SUVs", "nur Automatik")
braucht es ein kleines, festes Klassen-Vokabular statt 37+ Rohtexte.

Diese Datei übersetzt NUR zur Laufzeit — sie schreibt nichts in die
Datenbank und verändert keine Rohwerte. Ein Wert, der zu keinem bekannten
Muster passt, wird NICHT geraten: er bleibt `UNBEKANNT`. Das ist Absicht
(§3 der Produktspezifikation) — ein AutoFinder, der "SUV" auf einen Wert
rät, der eigentlich ein Pickup ist, wäre ein Datenfehler mit Kundenkontakt.

Eine Baureihe/Motorisierung kann in MEHREREN Klassen gleichzeitig stehen
(z.B. "SUV-Coupé" → {suv, coupe}) — Rückgabewert ist deshalb ein
`frozenset[str]`, kein Einzelwert.
"""

import json
import logging

log = logging.getLogger(__name__)

UNBEKANNT = "unbekannt"

# ============================================================
# KAROSSERIE (Körperform, aus baureihe.karosserie — JSON-Array)
# ============================================================

KLEINWAGEN = "kleinwagen"
KOMPAKT = "kompakt"
LIMOUSINE = "limousine"
KOMBI = "kombi"
SUV = "suv"
VAN = "van"
COUPE = "coupe"
CABRIO = "cabrio"
PICKUP = "pickup"

KAROSSERIE_KLASSEN = (
    KLEINWAGEN, KOMPAKT, LIMOUSINE, KOMBI, SUV, VAN, COUPE, CABRIO, PICKUP,
)

# Reihenfolge ist bewusst: spezifischere Muster (van/pickup/suv) VOR den
# generischen "…limousine"/"…heck"-Mustern geprüft, damit z.B.
# "Großraumlimousine" als Van erkannt wird und nicht über das Teilwort
# "limousine" fälschlich zur Limousine wird.
_KAROSSERIE_MUSTER: tuple[tuple[str, tuple[str, ...]], ...] = (
    (PICKUP, ("pickup", "pick-up", "single cab", "double cab",
              "xtra cab", "extra cab")),
    (VAN, ("van", "großraum", "grossraum", "hochdachkombi", "kastenwagen",
           "transporter", "mpv")),
    (SUV, ("suv", "geländewagen", "gelaendewagen", "crossover", "offroad")),
    (COUPE, ("coupé", "coupe")),
    (CABRIO, ("cabrio", "roadster", "spider", "spyder", "targa")),
    (KOMBI, ("kombi", "touring", "avant", "variant", "sportstourer",
             "shooting brake", "estate", "allroad", "caravan", "steilheck")),
    (KLEINWAGEN, ("kleinwagen", "kleinstwagen")),
    (KOMPAKT, ("schrägheck", "schraegheck", "fließheck", "fliessheck",
               "stufenheck", "sportback", "kompaktwagen", "compact",
               "türer", "tuerer", "türig", "tuerig")),
    (LIMOUSINE, ("limousine", "stufenheck", "sedan", "saloon")),
)


def _roh_werte(feld_json: str | None) -> list[str]:
    """JSON-Array robust in eine Liste roher Textwerte auflösen.

    Kaputtes/leeres JSON zählt als leere Liste — nie als Fehler, der die
    Filterung abbricht (dieselbe Fehlertoleranz wie `app/verification.py`).
    """
    if not feld_json:
        return []
    try:
        arr = json.loads(feld_json)
    except (ValueError, TypeError):
        return [str(feld_json)]
    if isinstance(arr, list):
        return [str(x) for x in arr if x is not None]
    if isinstance(arr, str):
        return [arr]
    return []


def normalisiere_karosserie(karosserie_json: str | None) -> frozenset[str]:
    """Rohe `baureihe.karosserie` → Menge bekannter Klassen aus `KAROSSERIE_KLASSEN`.

    Leer, wenn kein Rohwert vorhanden ist oder KEIN Muster passt — das ist
    `UNBEKANNT`, nicht `set()` mit stillschweigender Bedeutung "nichts
    davon". Aufrufer prüfen explizit auf Leere und behandeln sie als
    "nicht klassifizierbar", nicht als "trifft auf keine Klasse zu".
    """
    treffer: set[str] = set()
    for roh in _roh_werte(karosserie_json):
        s = roh.lower()
        for klasse, muster in _KAROSSERIE_MUSTER:
            if any(m in s for m in muster):
                treffer.add(klasse)
    return frozenset(treffer)


# ============================================================
# GETRIEBE (aus motorvariante.getriebe — JSON-Array)
# ============================================================

AUTOMATIK = "automatik"
MANUELL = "manuell"

GETRIEBE_KLASSEN = (AUTOMATIK, MANUELL)

_AUTOMATIK_WOERTER = (
    "automatik", "automatic", "dsg", "s tronic", "s-tronic", "tiptronic",
    "steptronic", "multitronic", "dct", "dkg", "g-tronic", "gtronic",
    "easytronic", "cvt", "wandler", "doppelkupplung", "pdk", "powershift",
    "tronic", "edc", "xtronic", "geartronic", "stufenlos", "e-cvt",
    "reduktion", "speedshift", "smg", "selespeed",
)
_MANUELL_WOERTER = (
    "manuell", "manual", "schaltgetriebe", "schalt", "handschalt",
)


def normalisiere_getriebe(getriebe_json: str | None) -> frozenset[str]:
    """Rohe `motorvariante.getriebe` → Teilmenge von {"automatik","manuell"}.

    Kann BEIDE enthalten (Baureihe wird mit Wahlgetriebe angeboten) oder leer
    sein (kein bekanntes Muster — z.B. seltene Exotenbezeichnungen). Leer
    bedeutet UNBEKANNT, nicht "kein Getriebe".
    """
    treffer: set[str] = set()
    for roh in _roh_werte(getriebe_json):
        s = roh.lower()
        if any(w in s for w in _AUTOMATIK_WOERTER):
            treffer.add(AUTOMATIK)
        if any(w in s for w in _MANUELL_WOERTER):
            treffer.add(MANUELL)
    return frozenset(treffer)


# ============================================================
# SEGMENT (aus baureihe.segment — EIN Freitextwert, 37 Rohvarianten)
# ============================================================

SEG_KLEINSTWAGEN = "kleinstwagen"
SEG_KLEINWAGEN = "kleinwagen"
SEG_KOMPAKTKLASSE = "kompaktklasse"
SEG_MITTELKLASSE = "mittelklasse"
SEG_OBERE_MITTELKLASSE = "obere_mittelklasse"
SEG_OBERKLASSE = "oberklasse"
SEG_SUV = "suv"
SEG_VAN = "van"
SEG_SPORTWAGEN = "sportwagen"
SEG_PICKUP = "pickup"
SEG_NUTZFAHRZEUG = "nutzfahrzeug"

SEGMENT_KLASSEN = (
    SEG_KLEINSTWAGEN, SEG_KLEINWAGEN, SEG_KOMPAKTKLASSE, SEG_MITTELKLASSE,
    SEG_OBERE_MITTELKLASSE, SEG_OBERKLASSE, SEG_SUV, SEG_VAN,
    SEG_SPORTWAGEN, SEG_PICKUP, SEG_NUTZFAHRZEUG,
)

# Reihenfolge ist die Entscheidung: SUV/Van/Pickup/Sportwagen/Nutzfahrzeug
# zuerst geprüft, weil ihre Rohwerte oft eine Größenklasse ALS Präfix tragen
# ("Kompakt-SUV", "Obere Mittelklasse SUV", "Kompakt-Pickup") — die
# Fahrzeugart ist dort die aussagekräftigere Information als die Größe.
_SEGMENT_MUSTER: tuple[tuple[str, tuple[str, ...]], ...] = (
    (SEG_SUV, ("suv", "geländewagen", "gelaendewagen")),
    (SEG_VAN, ("van", "hochdachkombi", "großraumlimousine", "grossraumlimousine")),
    (SEG_PICKUP, ("pick-up", "pickup")),
    (SEG_SPORTWAGEN, ("sportwagen", "sportcoupé", "sportcoupe", "pony car",
                       "roadster")),
    (SEG_NUTZFAHRZEUG, ("nutzfahrzeug",)),
    (SEG_OBERKLASSE, ("oberklasse", "luxusklasse")),
    (SEG_OBERE_MITTELKLASSE, ("obere mittelklasse",)),
    (SEG_MITTELKLASSE, ("mittelklasse",)),
    (SEG_KOMPAKTKLASSE, ("kompaktklasse", "premium-kompaktwagen")),
    (SEG_KLEINSTWAGEN, ("kleinstwagen",)),
    (SEG_KLEINWAGEN, ("kleinwagen",)),
)

# Amtliche EU-Segmentbuchstaben, wo die DB nur den Buchstaben trägt statt des
# Klartexts. Deterministische Fach-Zuordnung (DIN-Segmentschema), kein Raten:
# A=Kleinstwagen, B=Kleinwagen, C=Kompaktklasse, D=Mittelklasse.
_SEGMENT_BUCHSTABEN = {
    "A": SEG_KLEINSTWAGEN,
    "B": SEG_KLEINWAGEN,
    "C": SEG_KOMPAKTKLASSE,
    "D": SEG_MITTELKLASSE,
    "C-SEGMENT": SEG_KOMPAKTKLASSE,
    "D-SEGMENT": SEG_MITTELKLASSE,
}


def normalisiere_segment(segment: str | None) -> str:
    """Rohes `baureihe.segment` → EINE Klasse aus `SEGMENT_KLASSEN`, oder
    `UNBEKANNT` wenn kein Muster passt (z.B. seltene Nischenbezeichnungen)."""
    if not segment or not str(segment).strip():
        return UNBEKANNT
    roh = str(segment).strip()
    direkt = _SEGMENT_BUCHSTABEN.get(roh.upper())
    if direkt:
        return direkt
    s = roh.lower()
    for klasse, muster in _SEGMENT_MUSTER:
        if any(m in s for m in muster):
            return klasse
    return UNBEKANNT
