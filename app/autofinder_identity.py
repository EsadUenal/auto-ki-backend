from __future__ import annotations

"""
AutoFinder — semantische Kandidaten-Identität (Consumer-Release-Härtung).

WARUM DIESE DATEI EXISTIERT
---------------------------
Drei reale Browser-Befunde, alle mit derselben Wurzel — es gab bisher KEINE
Fahrzeugidentität quer über die beiden Herkünfte, nur String-/ID-Vergleiche:

  1. "Ford Focus Mk4 / 2.3 EcoBoost ST (280 PS)" (interne DB) und
     "Ford Focus Vierte Generation / 2.3 EcoBoost ST 280 PS" (Web-Recherche)
     erschienen BEIDE in den Top-Ergebnissen. Ursache:
     `autofinder_web.merge_und_diversifiziere` gruppiert über
     `baureihe_id or candidate_id`. Interne Kandidaten haben eine
     `baureihe_id`, Web-Kandidaten haben `baureihe_id is None` und fallen auf
     ihre eigene `candidate_id` zurück — zwei verschiedene Gruppenschlüssel,
     also zwei Karten für dasselbe reale Auto.

  2. "Volvo S60/V60" wurde als KOMBI-Empfehlung mit dem kombinierten
     Modellnamen ausgegeben. Ursache: die Web-Discovery übernimmt `modell`
     wörtlich aus der Gemini-Antwort; ein Modell namens "S60/V60" existiert
     aber nicht — es sind zwei Karosserievarianten derselben Baureihe.

  3. Generation-Synonyme ("Mk4", "Mk IV", "4. Generation", "Vierte
     Generation") erzeugten vier verschiedene Kandidatenidentitäten.

GRUNDSATZ
---------
Konservativ statt schönreden (§7 der Anforderung): im Zweifel wird NICHT
zusammengeworfen und NICHT umbenannt. Jede Regel hier greift nur, wenn sie
eindeutig auflösbar ist; sonst bleibt der Kandidat unverändert stehen.

Diese Datei ist reine, deterministische Logik: kein Netz, kein Gemini, keine
DB, kein Ranking. Sie verändert weder Fit-Score noch Preislogik noch
Reihenfolge — sie entscheidet nur, ob zwei Einträge DASSELBE Fahrzeug meinen
und wie der Modellname dem Consumer gegenüber heißen darf.
"""

import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

# ══════════════════════════════════════════════════════════════════════════
# GENERATION-NORMALISIERUNG (§TEIL 3)
# ══════════════════════════════════════════════════════════════════════════
#
# Bewusst KEINE universelle NLP-Lösung, sondern genau die Muster, die in den
# realen Daten vorkommen (416 Baureihen der kanonischen DB + die von der
# Web-Discovery gelieferten deutschen/englischen Ordinalformen):
#
#     "Mk4" / "Mk 4" / "MK IV" / "Mark IV"
#     "4. Generation" / "4. Gen"
#     "Vierte Generation" / "Fourth generation" / "4th generation"
#     "Fourth generation (SN95)"   -> Ordinalteil zählt, Chassiscode in Klammern
#
# Chassis-/Typcodes ("G20", "W214", "Typ 8Y", "B9", "MQ4") sind bereits
# eindeutige Identitäten und werden NUR geslugt, nie in eine Ordinalzahl
# umgedeutet. Unbekannte Strings werden ebenfalls nur geslugt — nie geraten.

_ORDINALWORT: dict[str, int] = {
    # deutsch
    "erste": 1, "zweite": 2, "dritte": 3, "vierte": 4, "fuenfte": 5, "fünfte": 5,
    "sechste": 6, "siebte": 7, "siebente": 7, "achte": 8, "neunte": 9, "zehnte": 10,
    # englisch
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}

_ROEMISCH: dict[str, int] = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5,
    "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
}


def _slug(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")


def normalisiere_generation(generation: str | None) -> str:
    """Stabiles Identitäts-Token für eine Generationsangabe.

    Ordinalformen (egal ob "Mk4", "MK IV", "4. Generation", "Vierte
    Generation", "Fourth generation (SN95)") werden zu `gen4`. Alles andere
    — insbesondere Chassiscodes — wird nur normalisiert durchgereicht
    (`g20`, `w214`, `typ-8y`). Leer/None -> "".

    NIE erfinden: was nicht sicher als Ordinal erkennbar ist, bleibt der
    eigene Slug und kollidiert damit nur mit sich selbst.
    """
    roh = str(generation or "").strip()
    if not roh:
        return ""
    text = roh.lower().replace("ü", "ue").replace("ö", "oe").replace("ä", "ae")

    # "Mk4", "Mk 4", "Mark 4", "MK IV", "Mark IV"
    m = re.search(r"\bm(?:k|ark)\.?\s*([0-9]{1,2}|[ivx]{1,4})\b", text)
    if m:
        nummer = _als_zahl(m.group(1))
        if nummer:
            return f"gen{nummer}"

    # "4. Generation", "4 Generation", "4th generation", "4. Gen"
    m = re.search(r"\b([0-9]{1,2})\s*(?:\.|st|nd|rd|th)?\s*(?:generation|gen)\b", text)
    if m:
        return f"gen{int(m.group(1))}"

    # "Vierte Generation", "Fourth generation"
    m = re.search(r"\b([a-z]+)\s+(?:generation|gen)\b", text)
    if m and m.group(1) in _ORDINALWORT:
        return f"gen{_ORDINALWORT[m.group(1)]}"

    # römisch direkt vor "Generation": "IV. Generation"
    m = re.search(r"\b([ivx]{1,4})\.?\s*(?:generation|gen)\b", text)
    if m and m.group(1) in _ROEMISCH:
        return f"gen{_ROEMISCH[m.group(1)]}"

    return _slug(roh)


def _als_zahl(rohteil: str) -> int | None:
    if rohteil.isdigit():
        return int(rohteil)
    return _ROEMISCH.get(rohteil)


# ══════════════════════════════════════════════════════════════════════════
# MULTI-BODY-MODELLNAMEN (§TEIL 4)
# ══════════════════════════════════════════════════════════════════════════
#
# Ein kombinierter Modellname ("S60/V60", "A4 / A4 Avant") beschreibt zwei
# Karosserievarianten derselben Baureihe. Für den Consumer ist das falsch:
# ein Fahrzeug namens "S60/V60" gibt es nicht.
#
# Die Auflösung ist bewusst KEINE markenspezifische Fahrzeugliste, sondern
# eine generische Tabelle von KAROSSERIE-NAMENSMARKERN, wie Hersteller sie
# quer über Marken verwenden. Sie greift nur, wenn genau EIN Namensteil zur
# gesuchten Karosserie passt — sonst bleibt der Name unverändert.

_BODY_MARKER: dict[str, tuple[str, ...]] = {
    "kombi": ("avant", "variant", "touring", "sportbrake", "sports tourer", "estate",
              "kombi", "sw", "shooting brake", "turnier", "caravan", "wagon"),
    "limousine": ("limousine", "sedan", "saloon", "berline", "stufenheck"),
    "kompakt": ("hatchback", "schraegheck", "schrägheck", "sportback", "gt line"),
    "suv": ("suv", "cross country", "allroad", "crossover"),
    "coupe": ("coupe", "coupé", "fastback"),
    "cabrio": ("cabrio", "cabriolet", "convertible", "roadster", "spider", "spyder"),
    "van": ("van", "tourer", "mpv"),
    "pickup": ("pickup", "pick-up", "doppelkabine"),
}

# Einbuchstabiges Präfix + Zahl ("S60", "V60", "V90", "S90"): eine bei
# mehreren Herstellern gebräuchliche Systematik, in der der Buchstabe die
# Karosserie kennzeichnet. BEWUSST eng gefasst — der Teil muss exakt
# "Buchstabe + Ziffern" sein, damit z.B. "V-Klasse" (Van, kein Kombi) NICHT
# fälschlich als Kombi-Marker gelesen wird.
_PRAEFIX_MARKER: dict[str, str] = {"s": "limousine", "v": "kombi"}

_TRENNER = re.compile(r"\s*[/|]\s*|\s+oder\s+|\s+or\s+", re.IGNORECASE)


def ist_kombinierter_modellname(modell: str | None) -> bool:
    """True, wenn der Modellname mehrere Modellbezeichnungen zusammenfasst."""
    return len(_teile(modell)) > 1


def _teile(modell: str | None) -> list[str]:
    roh = str(modell or "").strip()
    if not roh:
        return []
    return [t.strip() for t in _TRENNER.split(roh) if t and t.strip()]


def _teil_passt_zu_karosserie(teil: str, karosserie: str) -> bool:
    t = teil.lower()
    for marker in _BODY_MARKER.get(karosserie, ()):
        if marker in t:
            return True
    m = re.fullmatch(r"([a-z])\s*([0-9]{1,3})", t)
    if m and _PRAEFIX_MARKER.get(m.group(1)) == karosserie:
        return True
    return False


def consumer_modellname(modell: str | None, karosserie: str | None) -> str:
    """Projiziert einen kombinierten Modellnamen auf die EINE Bezeichnung,
    die zur angezeigten Karosserie gehört ("S60/V60" + kombi -> "V60").

    Gibt den Namen UNVERÄNDERT zurück, wenn
      - er gar nicht kombiniert ist,
      - keine Karosserie bekannt ist,
      - kein oder mehr als ein Namensteil zur Karosserie passt.

    Damit wird nie geraten: eine Projektion findet nur bei eindeutiger
    Auflösung statt (§TEIL 4 "nur wenn Daten/eindeutige Regel das belastbar
    erlauben").
    """
    teile = _teile(modell)
    if len(teile) < 2 or not karosserie:
        return str(modell or "")
    treffer = [t for t in teile if _teil_passt_zu_karosserie(t, karosserie)]
    if len(treffer) == 1:
        return treffer[0]
    return str(modell or "")


def modell_familie(modell: str | None) -> str:
    """Identitäts-Schlüssel der Modellfamilie.

    Ein kombinierter Name ("S60/V60") und seine Einzelteile ("V60") müssen
    dieselbe Familie ergeben, damit interne und Web-Treffer desselben
    Fahrzeugs überhaupt aufeinandertreffen können. Deshalb: alle Teile
    normalisieren, sortieren und zusammenfassen; ein einzelner Teil ist eine
    Teilmenge und wird beim Vergleich über `familien_kompatibel` behandelt.
    """
    teile = [_slug(t) for t in _teile(modell)]
    return "|".join(sorted(t for t in teile if t))


def familien_kompatibel(a: str, b: str) -> bool:
    """Zwei Modellfamilien meinen dasselbe, wenn sie identisch sind oder die
    eine eine Teilmenge der anderen ist ("v60" ⊂ "s60|v60")."""
    if not a or not b:
        return False
    if a == b:
        return True
    mengen_a, mengen_b = set(a.split("|")), set(b.split("|"))
    return mengen_a <= mengen_b or mengen_b <= mengen_a


# ══════════════════════════════════════════════════════════════════════════
# SEMANTISCHE FAHRZEUGIDENTITÄT (§TEIL 2 / §TEIL 7)
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Identitaet:
    marke: str
    familie: str
    generation: str
    karosserie: frozenset[str]
    leistung_ps: int | None
    kraftstoff: str
    antrieb: str
    baujahr_von: int | None


def identitaet(kandidat: Any) -> Identitaet:
    """Baut die semantische Identität aus den STRUKTURIERTEN Feldern.

    Bewusst nicht aus der Motor-Bezeichnung (Freitext: "2.3 EcoBoost ST
    (280 PS)" vs. "2.3 EcoBoost ST 280 PS"), sondern aus Leistung +
    Kraftstoff + Antrieb — dieselbe Philosophie wie der bestehende interne
    Dedupe-Schlüssel in `app.autofinder._dedupe_schluessel`.
    """
    return Identitaet(
        marke=_slug(getattr(kandidat, "marke", "")),
        familie=modell_familie(getattr(kandidat, "modell", "")),
        generation=normalisiere_generation(getattr(kandidat, "generation", None)),
        karosserie=frozenset(_slug(k) for k in (getattr(kandidat, "karosserie_klassen", None) or [])),
        leistung_ps=getattr(kandidat, "leistung_ps", None),
        kraftstoff=_slug(getattr(kandidat, "kraftstoff", "")),
        antrieb=_slug(getattr(kandidat, "antrieb", "")),
        baujahr_von=getattr(kandidat, "baujahr_von", None),
    )


# Zwei Kandidaten ohne übereinstimmendes Generation-Token gelten trotzdem als
# dasselbe Fahrzeug, wenn Marke/Familie/Karosserie/Motor passen UND die
# Bauzeiträume praktisch identisch beginnen. Das fängt den Fall
# "Chassiscode gegen Ordinalform" ab (z.B. "G20" vs. "Siebte Generation"),
# ohne echte Generationswechsel zu verschmelzen: aufeinanderfolgende
# Generationen starten Jahre auseinander, nicht im selben Jahr.
_BAUJAHR_TOLERANZ = 1


def _karosserie_kompatibel(a: frozenset[str], b: frozenset[str]) -> bool:
    """Überschneidung genügt; eine leere Menge (unbekannt) blockiert nicht.

    Ein interner Multi-Body-Datensatz ({limousine, kombi}) und ein
    Web-Treffer, der nur die Kombi-Variante kennt ({kombi}), meinen dasselbe
    Fahrzeug. Zwei DISJUNKTE Mengen ({limousine} vs. {kombi}) dagegen nicht —
    die werden NICHT zusammengeworfen (§TEIL 10 D).
    """
    if not a or not b:
        return True
    return bool(a & b)


def _motor_kompatibel(a: Identitaet, b: Identitaet) -> bool:
    if a.kraftstoff and b.kraftstoff and a.kraftstoff != b.kraftstoff:
        return False
    if a.leistung_ps is not None and b.leistung_ps is not None and a.leistung_ps != b.leistung_ps:
        return False
    # Antrieb trennt nur, wenn er auf BEIDEN Seiten bekannt ist ("missing !=
    # different" — ein Web-Treffer ohne Antriebsangabe ist kein zweites Auto).
    if a.antrieb and b.antrieb and a.antrieb != b.antrieb:
        return False
    return True


def _baujahr_praktisch_gleich(a: Identitaet, b: Identitaet) -> bool:
    if a.baujahr_von is None or b.baujahr_von is None:
        return False
    return abs(a.baujahr_von - b.baujahr_von) <= _BAUJAHR_TOLERANZ


def ist_dasselbe_fahrzeug(a: Any, b: Any) -> bool:
    """Semantische Gleichheit zweier Kandidaten — quer über interne DB und
    Web-Recherche, unabhängig von Schreibweisen.

    Bedingungen (alle müssen gelten):
      Marke gleich, Modellfamilie kompatibel, Karosserie kompatibel,
      Motor kompatibel — UND entweder dasselbe Generation-Token oder
      praktisch derselbe Bauzeitraum-Beginn.
    """
    ia, ib = identitaet(a), identitaet(b)
    if not ia.marke or ia.marke != ib.marke:
        return False
    if not familien_kompatibel(ia.familie, ib.familie):
        return False
    if not _karosserie_kompatibel(ia.karosserie, ib.karosserie):
        return False
    if not _motor_kompatibel(ia, ib):
        return False
    if ia.generation and ib.generation and ia.generation == ib.generation:
        return True
    return _baujahr_praktisch_gleich(ia, ib)


# ══════════════════════════════════════════════════════════════════════════
# FINALER DEDUPE (§TEIL 7)
# ══════════════════════════════════════════════════════════════════════════

def _rang(kandidat: Any, fit: float) -> tuple:
    """Wer gewinnt bei einem Duplikat — deterministisch, nie zufällig:
      1. interne DB vor Web-Recherche
      2. höhere Datenqualität, dann mehr Belege
      3. besserer User-Fit
      4. stabiler Tie-Break über die Kandidaten-ID
    """
    intern = 0 if getattr(kandidat, "source_type", "internal_db") == "internal_db" else 1
    dq = float(getattr(kandidat, "datenqualitaet", 0.0) or 0.0)
    belege = int(getattr(kandidat, "evidence_count", 0) or 0)
    kid = (getattr(kandidat, "candidate_id", None)
           or getattr(kandidat, "variante_id", None) or "")
    return (intern, -dq, -belege, -fit, kid)


def dedupe_semantisch(
    eintraege: Sequence[tuple[float, Any]],
) -> tuple[list[tuple[float, Any]], list[str]]:
    """Entfernt semantische Duplikate aus einer bereits sortierten Liste.

    `eintraege` ist eine Folge von `(fit_score, kandidat)`. Rückgabe:
    `(behaltene_eintraege_in_ursprungsreihenfolge, entfernte_ids)`.

    Die Reihenfolge der Überlebenden bleibt exakt die Eingabereihenfolge —
    dieser Schritt sortiert NICHT um, er streicht nur. Innerhalb einer
    Duplikatgruppe entscheidet `_rang`, nicht die Position.
    """
    gruppen: list[list[int]] = []
    for idx, (_fit, kand) in enumerate(eintraege):
        for gruppe in gruppen:
            if ist_dasselbe_fahrzeug(eintraege[gruppe[0]][1], kand):
                gruppe.append(idx)
                break
        else:
            gruppen.append([idx])

    behalten_idx: set[int] = set()
    entfernt: list[str] = []
    for gruppe in gruppen:
        gewinner = min(gruppe, key=lambda i: _rang(eintraege[i][1], eintraege[i][0]))
        behalten_idx.add(gewinner)
        for i in gruppe:
            if i == gewinner:
                continue
            kand = eintraege[i][1]
            entfernt.append(getattr(kand, "candidate_id", None)
                            or getattr(kand, "variante_id", None) or "")

    return [e for i, e in enumerate(eintraege) if i in behalten_idx], entfernt


def dedupe_kandidatenliste(kandidaten: Iterable[Any]) -> list[Any]:
    """Bequemlichkeits-Variante ohne Fit-Scores (gleiche Semantik)."""
    paare = [(0.0, k) for k in kandidaten]
    behalten, _ = dedupe_semantisch(paare)
    return [k for _f, k in behalten]
