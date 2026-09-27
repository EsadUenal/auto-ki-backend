from __future__ import annotations

"""
Konkrete Wartungsangabe aus dem Inseratstext — streng, ohne Raten.

BEFUND AUS DEM ECHTEN PRODUCTION-RUN (BMW 330i G20)
---------------------------------------------------
Im Inseratstext stand ausdrücklich:

    "Letzte Wartung laut Verkäufer bei ca. 72.000 km durchgeführt."

Der Bericht fragte trotzdem: "Wann war die letzte Wartung, und was wurde dabei
gemacht?" Eine Information, die der Nutzer gerade eingegeben hat, kam als offene
Frage zurück. Das wirkt, als hätte ENFAL den Text nicht gelesen.

Ursache: der Inseratstext ging zwar in den Prompt, aber in KEINE deterministische
Auswertung. `app/laufleistung.py` hält ausdrücklich fest, dass der Zeitpunkt des
letzten Service unbekannt ist — es gab schlicht keine Datenquelle dafür.
Dieses Modul ist diese Quelle, für genau den Fall, dass das Inserat eine
konkrete Zahl nennt.

WAS EXTRAHIERT WIRD — UND WAS NICHT
------------------------------------
Extrahiert wird NUR eine ausdrückliche Angabe: ein Auslöserwort aus dem
Wartungsumfeld (Wartung, Inspektion, Service, Kundendienst, Ölwechsel) und in
seiner unmittelbaren Nähe eine konkrete Zahl (Kilometerstand oder Datum).

  "Letzte Wartung bei ca. 72.000 km"        -> km = 72000
  "Inspektion 03/2025 durchgeführt"         -> datum = "03/2025"
  "Großer Service bei 72000 km im Mai 2025" -> km = 72000, datum = "05/2025"

NICHT extrahiert wird alles, was keine Zahl nennt:

  "regelmäßig gewartet"                     -> nichts
  "scheckheftgepflegt"                      -> nichts
  "gepflegter Zustand, alles gemacht"       -> nichts

Aus "regelmäßig gewartet" darf NIEMALS "letzte Wartung bei 72.000 km" werden.
Das ist der Kern dieses Moduls: lieber gar keine Angabe als eine erfundene.

CLAIM-SAFETY
------------
Eine extrahierte Angabe bleibt eine ANGABE. Sie belegt nicht, dass die Wartung
stattgefunden hat: es wurde keine Rechnung und kein Serviceheft gesehen. Alle
Texte dieses Moduls tragen das "laut Inserat" im Satz, und keiner von ihnen
macht `letzter_service_bekannt` wahr.

PLAUSIBILITÄT
-------------
Ein Wartungs-Kilometerstand über dem aktuellen Tacho ist kein Fund, sondern ein
Widerspruch. Er wird nicht verworfen, sondern als solcher gemeldet — das
Aufdecken widersprüchlicher Inserate ist selbst ein Ergebnis.
"""

import re

# Auslöserwörter: das Thema muss ausdrücklich Wartung sein.
_AUSLOESER = (r"wartung(?:en)?|inspektion(?:en)?|service|kundendienst|ölwechsel|oelwechsel"
              r"|ölservice|oelservice|durchsicht")

# Kilometerangabe: "72.000 km", "72000km", "72 000 km", "72 tkm", "72.000 Kilometer".
_KM = r"(\d{1,3}(?:[.\s]\d{3})+|\d{2,7})\s*(t?km|tkm|kilometer)"
# Monat/Jahr: "03/2025", "03.2025", "3/25" bewusst NICHT (zu mehrdeutig).
_MMJJJJ = r"(0?[1-9]|1[0-2])\s*[./]\s*(20\d{2})"
# Ausgeschriebener Monat, z.B. "im Mai 2025".
_MONATE = {"januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5,
           "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10,
           "november": 11, "dezember": 12}
_MONAT_WORT = r"(" + "|".join(_MONATE) + r")\s+(20\d{2})"

# Fenster um das Auslöserwort. 80 Zeichen fassen "Letzte Wartung laut Verkäufer
# bei ca. 72.000 km durchgeführt" bequem, springen aber nicht in den nächsten
# Satz über eine ganze Aufzählung hinweg.
_FENSTER = 80

# Satzgrenze. Das Fenster endet in BEIDE Richtungen am Satzende, sonst zieht
# "Fahrzeug hat 78.500 km. Wartung ist demnächst fällig." den Tachostand aus dem
# Vorsatz herein, und "Wartung fällig. Reifen bei 72.000 km gewechselt" eine
# Zahl aus dem Folgesatz.
#
# Ein Punkt ist nur dann ein Satzende, wenn danach Leerraum UND ein Großbuchstabe
# (oder das Textende) folgt. Sonst wären "ca. 72.000 km" und "72.000" selbst
# jedes Mal Satzgrenzen — genau daran ist die erste Fassung am echten Satz
# "Letzte Wartung laut Verkäufer bei ca. 72.000 km durchgeführt." gescheitert:
# sie schnitt hinter "ca." ab und fand die Zahl nicht mehr.
_SATZENDE = re.compile(r"(?:[!?\n]|\.(?=\s+[A-ZÄÖÜ]|\s*$))")

MAX_PLAUSIBEL_KM = 2_000_000


class Wartungsangabe:
    """Eine im Inserat ausdrücklich genannte Wartung.

    `km` und `datum` können einzeln fehlen — genannt wird, was dasteht.
    `rohtext` ist der Ausschnitt, aus dem die Angabe stammt; er macht die
    Extraktion im Zweifel nachvollziehbar.
    """

    __slots__ = ("km", "datum", "rohtext")

    def __init__(self, km: int | None, datum: str | None, rohtext: str):
        self.km = km
        self.datum = datum
        self.rohtext = rohtext

    def __eq__(self, other):
        return (isinstance(other, Wartungsangabe) and self.km == other.km
                and self.datum == other.datum)

    def __repr__(self):
        return f"Wartungsangabe(km={self.km}, datum={self.datum!r})"

    def anzeige(self) -> str:
        """Menschenlesbare Kurzform der ANGABE, immer als Angabe formuliert."""
        teile = []
        if self.km is not None:
            teile.append(f"bei rund {self.km:,} km".replace(",", "."))
        if self.datum:
            teile.append(f"im {self.datum}")
        return " ".join(teile)


def _zahl(roh: str) -> int | None:
    ziffern = roh.replace(".", "").replace(" ", "")
    if not ziffern.isdigit():
        return None
    return int(ziffern)


def _km_aus(fenster: str) -> int | None:
    m = re.search(_KM, fenster, re.IGNORECASE)
    if not m:
        return None
    wert = _zahl(m.group(1))
    if wert is None:
        return None
    # "72 tkm" meint 72.000 km.
    if m.group(2).lower() in ("tkm", "ttkm"):
        wert *= 1000
    # Eine nackte zweistellige Zahl vor "km" ist fast immer "72 tkm" gemeint,
    # aber das ist Raten — deshalb wird sie NICHT hochgerechnet, sondern
    # verworfen: 72 km Laufleistung bei einer Wartungsangabe ist unplausibel.
    if wert < 100 or wert > MAX_PLAUSIBEL_KM:
        return None
    return wert


def _datum_aus(fenster: str) -> str | None:
    m = re.search(_MMJJJJ, fenster)
    if m:
        return f"{int(m.group(1)):02d}/{m.group(2)}"
    m = re.search(_MONAT_WORT, fenster, re.IGNORECASE)
    if m:
        return f"{_MONATE[m.group(1).lower()]:02d}/{m.group(2)}"
    return None


def extrahiere(*texte: str | None) -> Wartungsangabe | None:
    """Die erste ausdrückliche Wartungsangabe aus den übergebenen Texten.

    Rückgabe None, wenn das Inserat keine konkrete Zahl zur Wartung nennt —
    der Normalfall. Dann bleibt die offene Verkäuferfrage bestehen.
    """
    for text in texte:
        if not text:
            continue
        for treffer in re.finditer(_AUSLOESER, text, re.IGNORECASE):
            fenster = _satzfenster(text, treffer.start(), treffer.end())
            km = _km_aus(fenster)
            datum = _datum_aus(fenster)
            if km is None and datum is None:
                continue
            return Wartungsangabe(km=km, datum=datum, rohtext=fenster.strip())
    return None


def _satzfenster(text: str, start: int, ende: int) -> str:
    """Der Satz um das Auslöserwort, zusätzlich auf `_FENSTER` begrenzt.

    Beidseitig begrenzt: eine Zahl aus dem Vor- oder Folgesatz gehört nicht zu
    dieser Wartungsangabe.
    """
    links = max(0, start - _FENSTER)
    vorher = text[links:start]
    grenzen = list(_SATZENDE.finditer(vorher))
    if grenzen:
        links += grenzen[-1].end()

    rechts = min(len(text), ende + _FENSTER)
    nachher = text[ende:rechts]
    grenze = _SATZENDE.search(nachher)
    if grenze:
        rechts = ende + grenze.start()

    return text[links:rechts]


def aus_request(req) -> Wartungsangabe | None:
    return extrahiere(getattr(req, "beschreibung", None), getattr(req, "freitext", None))


def widerspruch_km(angabe: Wartungsangabe | None, kilometerstand: int | None) -> bool:
    """True, wenn die genannte Wartung ÜBER dem aktuellen Tachostand liegt."""
    if angabe is None or angabe.km is None or not kilometerstand:
        return False
    return angabe.km > kilometerstand


def prompt_zeile(angabe: Wartungsangabe | None) -> str | None:
    if angabe is None:
        return None
    return (f"Letzte Wartung laut Inserat: {angabe.anzeige()} "
            f"(ANGABE des Inserats, kein geprüfter Nachweis)")
