"""Kurzer, verständlicher Titel für einen Rückruf — deterministisch aus dem amtlichen Text.

WARUM
-----
Die Rückrufzeilen tragen nur den amtlichen Mangeltext der KBA-Datenbank
(Median 110 Zeichen, 321 von 374 länger als 70). Er wurde bisher als Titel
benutzt und hart abgeschnitten: "KBA-Rückruf (Baureihe): Aufgrund fehlerhafter
Auslegung kann es bei hohen Belastungen zu einem". Ein eigenes Kurzfeld gibt es
nicht.

WIE
---
Titel = "<Bauteil>: <Folge>", beides aus dem amtlichen Text abgeleitet:

  * Bauteil: das WORT aus dem Text, das ein bekanntes Bauteil enthält — so
    bleibt die amtliche Präzision erhalten ("Kraftstofffilterheizung",
    "Radlagergehäuse", "Starterrelais"). Genitiv-Endungen werden entfernt
    ("des Gurtschlosses" -> "Gurtschloss"). Allgemeine Begriffe (Sensorik,
    Software, Leitung, Verschraubung) zählen nur, wenn kein konkretes Bauteil
    genannt ist.
  * Folge: nach Schwere geordnet — Brand vor Bruch vor Kontrollverlust vor
    Fehlauslösung vor Ausfall usw. Die schwerste genannte Folge gewinnt.

Kurze amtliche Texte (<= 60 Zeichen) bleiben unverändert. Der vollständige
amtliche Text wird NIE verändert und bleibt in der Detailansicht erhalten;
dieses Modul liefert nur die sichtbare Überschrift.
Kein Fahrzeug- oder Herstellersonderfall, keine KI.
"""
from __future__ import annotations

import re

MAX_TITEL = 60

# Konkrete Bauteile (Wortbestandteile, klein, ohne Umlaut-Ersatz).
_BAUTEILE = (
    "airbag", "gasgenerator", "gurtschloss", "gurtstraffer", "gurt", "kopfstütze", "sitz",
    "spurstange", "querlenker", "lenkung", "lenkgetriebe", "lenksäule", "lenkrad",
    "radlager", "radnabe", "radverschraubung", "radschraube", "felge", "reifen", "achse",
    "bremse", "bremsscheibe", "bremsbelag", "bremsleitung", "bremsschlauch", "bremssattel",
    "bremspedal", "bremskraft", "bremskraftverstärker", "hauptbremszylinder", "handbremse",
    "feststellbremse",
    "starterrelais", "starter", "anlasser", "generator", "batterie", "hochvolt",
    "kraftstoff", "tank", "einspritz", "kraftstoffpumpe", "kraftstoffleitung",
    "turbo", "motor", "ölleitung", "ölpumpe", "ölfilter", "kurbelwelle", "nockenwelle",
    "zahnriemen", "steuerkette", "ausgleichswelle", "kühl", "wasserpumpe",
    "abgasrückführung", "agr", "katalysator", "partikelfilter", "auspuff",
    "getriebe", "kupplung", "antriebswelle", "kardanwelle", "differenzial",
    "scheinwerfer", "leuchte", "blinker", "scheibenwischer", "wischer",
    "tür", "motorhaube", "heckklappe", "schiebedach", "fenster", "spiegel",
    "stoßfänger", "anhängerkupplung", "tankband", "federbein", "feder", "stoßdämpfer",
    "notruf", "ecall", "kamera", "kombiinstrument", "tempomat",
    "esp", "abs", "stabilitätsprogramm", "heizer", "heizung", "waschdüse", "zündung",
)
# Allgemeine Begriffe — nur ohne konkretes Bauteil.
_ALLGEMEIN = ("steuergerät", "software", "sensor", "leitung", "kabel", "verschraubung",
              "schraube", "halter", "modul", "stecker", "steckverbindung", "dichtung")

# (Muster, Folge) — nach Schwere geordnet, die erste Treffer-Folge gewinnt.
_FOLGEN: tuple[tuple[str, str], ...] = (
    (r"brand|feuer|entzünd", "Brandgefahr"),
    (r"bruch|brech|bricht|reiß|riss\b|risse", "Bruchgefahr"),
    (r"kontrollverlust|lenkungsverlust|kontrolle über", "Gefahr des Kontrollverlusts"),
    (r"(?:auslösung|auslösen|löst .{0,20}aus)", "AUSLOESUNG"),
    (r"lösen|löst sich|herabfall|ablös|abfallen", "kann sich lösen"),
    (r"austritt|undicht|leck", "Undichtigkeit"),
    (r"überhitz", "Überhitzung"),
    (r"rückhaltewirkung|schutzwirkung", "eingeschränkte Schutzwirkung"),
    (r"ausfall|fällt .{0,15}aus|funktionsstörung|funktioniert nicht", "möglicher Ausfall"),
    (r"blockier", "kann blockieren"),
    (r"bremsweg|bremswirkung|bremsleistung|bremskraft", "verminderte Bremswirkung"),
    (r"verletzung", "Verletzungsgefahr"),
    (r"unsicher\w* fahrzust", "unsichere Fahrzustände"),
    (r"unfallgefahr|unfallrisiko|unfäll|unfall", "Unfallgefahr"),
    (r"beeinträchtig|funktionseinschränk|eingeschränkt", "eingeschränkte Funktion"),
    (r"anzeige", "fehlerhafte Anzeige"),
)

_KEINE_GENITIV_ENDUNG = ("ais", "ss", "us", "is", "as", "os")


def _grundform(wort: str) -> str:
    """Genitiv-/Plural-s entfernen, ohne Nominativ-s anzutasten ("Starterrelais")."""
    w = wort
    low = w.lower()
    if low.endswith(("sses", "zes", "ßes")):
        return w[:-2]
    if low.endswith("s") and len(w) > 5 and not low.endswith(_KEINE_GENITIV_ENDUNG):
        return w[:-1]
    return w


def _bauteil(text: str) -> str | None:
    woerter = re.findall(r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-/()]*", text)
    for liste in (_BAUTEILE, _ALLGEMEIN):
        for wort in woerter:
            low = wort.lower().strip("()")
            if len(low) < 3:
                continue
            # Kurze Stämme ("abs", "esp", "agr") nur als eigenes Wort bzw. Wortanfang
            # mit Bindestrich — sonst trifft "abs" auch "Abstand" oder "Abschaltung".
            if any((low == stamm or low.startswith(stamm + "-")) if len(stamm) <= 3
                   else stamm in low for stamm in liste):
                w = _grundform(wort.strip("()/-"))
                return w[:1].upper() + w[1:]
    return None


def _folge(text: str, bauteil: str | None) -> str | None:
    low = text.lower()
    for muster, folge in _FOLGEN:
        if re.search(muster, low):
            if folge != "AUSLOESUNG":
                return folge
            airbag, straffer = "airbag" in low, "gurtstraffer" in low
            b = (bauteil or "").lower()
            if "airbag" in b or "gurtstraffer" in b or "gasgenerator" in b:
                return "fehlerhafte Auslösung"
            if airbag and straffer:
                return "fehlerhafte Airbag- und Gurtstraffer-Auslösung"
            if airbag:
                return "fehlerhafte Airbag-Auslösung"
            if straffer:
                return "fehlerhafte Gurtstraffer-Auslösung"
            return "fehlerhafte Auslösung"
    return None


def rueckruf_kurztitel(mangel: str | None) -> str:
    """Kurzer Titel aus dem amtlichen Mangeltext. Der Text selbst bleibt unverändert."""
    text = " ".join((mangel or "").split())
    if not text:
        return "Rückruf"
    if len(text) <= MAX_TITEL:
        return text.rstrip(".")
    bauteil = _bauteil(text)
    folge = _folge(text, bauteil)
    if bauteil and folge:
        titel = f"{bauteil}: {folge}"
    elif bauteil:
        titel = f"{bauteil}: sicherheitsrelevanter Mangel"
    elif folge:
        titel = f"Rückruf wegen {folge}" if folge[0].isupper() else f"Rückruf: {folge}"
    else:
        titel = "Sicherheitsrelevanter Rückruf"
    return titel if len(titel) <= MAX_TITEL else f"{bauteil}: {folge}"[:MAX_TITEL].rsplit(" ", 1)[0]
