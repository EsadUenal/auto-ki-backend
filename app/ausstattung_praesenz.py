from __future__ import annotations

"""
Präsenz abhängiger Komponenten: CONFIRMED_PRESENT / UNKNOWN / CONFIRMED_ABSENT
(KaufCheck-Final-Stabilization, Cluster C, Invariante 5).

BEFUND (Production-Runs)
------------------------
  * BMW M4 (Ausstattung leer): "EDC" erschien als konkreter Prüfpunkt.
  * Mercedes C300 (COMAND nicht angegeben): COMAND erschien konkret.
  * Opel Astra K (Ausstattung leer): IntelliLink erschien konkret.

ROOT CAUSE
----------
Die Bedingung "nur falls verbaut" hing an EINER Stelle
(`kaufaktionen._aus_schwachstellen`, Besichtigung/Probefahrt) und an einer
festen Stringliste. Verkäuferfragen, Dokumente, Motorprobleme, Web-Hinweise,
Key Findings, Risikotitel und Bericht kannten sie nicht — jeder Renderer
konnte das Bauteil als Fakt über DIESES Fahrzeug darstellen. Dieselbe Lücke
galt für Komponenten, die an einer Fahrzeugeigenschaft hängen: ein DKG-Punkt
an einem Schaltwagen, ein AdBlue-Punkt an einem Benziner, ein Hochvolt-Punkt
an einem Verbrenner.

MODELL
------
Jede Komponente kann EINE Abhängigkeit tragen (`Abhaengigkeit`):

  art="ausstattung"  optionale Ausstattung; Präsenz nur aus dem Inserat
                     (Ausstattungsliste/Beschreibung) belegbar.
  art="getriebe" | "antrieb" | "kraftstoff" | "powertrain"
                     Präsenz folgt aus der KANONISCHEN Identität (inklusive
                     "mehrdeutig": dann gilt die Menge der möglichen Werte).

Die Präsenz wird EINMAL am kanonischen Risiko (Insight) festgehalten
(`presence_state`, `equipment_dependency`); CONFIRMED_ABSENT entfernt das
Risiko aus der kanonischen Menge, UNKNOWN macht Titel und jede daraus
abgeleitete Aktion bedingt ("Falls EDC vorhanden: …"). Kein Renderer muss
die Regel neu kennen — er liest das Insight.
"""

import re
from dataclasses import dataclass

PRESENT = "confirmed_present"
UNKNOWN = "unknown"
ABSENT = "confirmed_absent"

_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                          "Ä": "ae", "Ö": "oe", "Ü": "ue"})


def _norm(text: str | None) -> str:
    t = (text or "").lower().translate(_UMLAUTE)
    t = re.sub(r"[^a-z0-9&]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


@dataclass(frozen=True)
class Abhaengigkeit:
    art: str
    klasse: str                     # kurze Klassenbezeichnung ("adaptive Dämpfer")
    muster: re.Pattern              # erkennt die Komponente im Risikotext (normalisiert)
    erfordert: frozenset = frozenset()   # nur Attribut-Abhängigkeiten
    nachweis: re.Pattern | None = None   # nur Ausstattung: Nachweis im Inserat (normalisiert)


def _rx(m: str) -> re.Pattern:
    return re.compile(m)


# Reihenfolge = Priorität (spezifisch vor allgemein). Vokabular ist
# fahrzeugunabhängig: es beschreibt Komponentenklassen, keine Modelle.
ABHAENGIGKEITEN: tuple[Abhaengigkeit, ...] = (
    # ── an der Fahrzeugeigenschaft ──────────────────────────────────────────
    Abhaengigkeit("powertrain", "Hochvolt-System",
                  _rx(r"hochvolt|hv batterie|traktionsbatterie|antriebsbatterie|ladebuchse|ladedose"
                      r"|ladegeraet|ladeanschluss|ladekabel|ladeklappe|traktionsmotor"),
                  frozenset({"PHEV", "BEV", "HEV"})),
    Abhaengigkeit("powertrain", "Mild-Hybrid-System",
                  _rx(r"\b48 ?v\b|48 volt|riemenstartergenerator|startergenerator|mild hybrid|mhev"),
                  frozenset({"MHEV"})),
    Abhaengigkeit("kraftstoff", "Dieselkomponente",
                  _rx(r"adblue|\bscr\b|dieselpartikel|\bdpf\b|russpartikel|gluehkerz|gluehanlage"
                      r"|pumpe duese|tandempumpe"),
                  frozenset({"diesel"})),
    Abhaengigkeit("kraftstoff", "Benzinerkomponente",
                  _rx(r"zuendkerz|zuendspul|ottopartikel|\bopf\b"),
                  frozenset({"benzin"})),
    Abhaengigkeit("getriebe", "Automatik-/Doppelkupplungsgetriebe",
                  # "wandler" nur als eigenes Wort bzw. Drehmomentwandler — ein
                  # "Spannungswandler" (Bordnetz) ist keine Getriebekomponente.
                  _rx(r"doppelkupplung|\bdkg\b|\bdsg\b|s ?tronic|powershift|mechatronik|\bpdk\b"
                      r"|\bwandler|drehmomentwandler|automatikgetriebe|getriebe automatik|\bcvt\b"
                      r"|multitronic|steptronic"
                      r"|tiptronic|\d ?g ?tronic"),
                  frozenset({"automatik"})),
    Abhaengigkeit("getriebe", "Schaltgetriebe-Kupplung",
                  _rx(r"(?<![a-z])kupplung(?!.*(?:haldex|visko|kompressor|klima))|kupplungspedal"
                      r"|schaltgetriebe"),
                  frozenset({"manuell"})),
    Abhaengigkeit("antrieb", "Allradantrieb",
                  _rx(r"haldex|allrad(?!lenkung)|xdrive|quattro|4matic|4motion|verteilergetriebe|visko"),
                  frozenset({"allrad"})),
    # ── optionale Ausstattung ───────────────────────────────────────────────
    Abhaengigkeit("ausstattung", "adaptive Dämpfer",
                  _rx(r"\bedc\b|adaptiv\w* (?:daempf|fahrwerk)|adaptivdaempf|\bdcc\b|daempferregel"
                      r"|verstelldaempf|dynamic damper|magnetic ride|elektronische daempf"),
                  nachweis=_rx(r"\bedc\b|adaptiv\w* (?:daempf|fahrwerk|m fahrwerk)|\bdcc\b"
                               r"|daempferregel|verstelldaempf|dynamic damper|magnetic ride")),
    Abhaengigkeit("ausstattung", "Luftfederung",
                  _rx(r"luftfeder|niveaureg|airmatic|air suspension"),
                  nachweis=_rx(r"luftfeder|niveaureg|airmatic|air suspension")),
    # Nur Systeme, die es als OPTION neben einem Basisgerät gibt (Navigations-/
    # Infotainment-Produktlinien). Das Gattungswort "Infotainment" und die
    # Grundsystem-Namen einer Marke (die jedes Fahrzeug der Baureihe hat) sind
    # KEINE Ausstattungsabhängigkeit — sonst würde jeder Software-Hinweis bedingt.
    Abhaengigkeit("ausstattung", "Navigations-/Infotainmentsystem",
                  _rx(r"comand|intellilink|uconnect|sensus|\bnavi\w*"),
                  nachweis=_rx(r"comand|intellilink|uconnect|sensus|\bnavi\w*")),
    Abhaengigkeit("ausstattung", "Panorama-/Schiebedach",
                  _rx(r"panorama|schiebedach|glasdach|hubdach|sonnendach|faltdach"),
                  nachweis=_rx(r"panorama|schiebedach|glasdach|hubdach|sonnendach|faltdach")),
    Abhaengigkeit("ausstattung", "Head-up-Display",
                  _rx(r"head ?up|\bhud\b"), nachweis=_rx(r"head ?up|\bhud\b")),
    Abhaengigkeit("ausstattung", "adaptives Licht",
                  _rx(r"matrix|laserlicht|multibeam|adaptiv\w* (?:licht|scheinwerfer)|kurvenlicht"),
                  nachweis=_rx(r"matrix|laserlicht|multibeam|adaptiv\w* (?:licht|scheinwerfer)"
                               r"|kurvenlicht")),
    Abhaengigkeit("ausstattung", "Premium-Soundsystem",
                  _rx(r"burmester|harman|bang ?(?:&|und) ?olufsen|\bb ?& ?o\b|bowers|meridian|\bbose\b"
                      r"|premium ?sound"),
                  nachweis=_rx(r"burmester|harman|bang ?(?:&|und) ?olufsen|\bb ?& ?o\b|bowers"
                               r"|meridian|\bbose\b|premium ?sound")),
    Abhaengigkeit("ausstattung", "Standheizung",
                  _rx(r"standheizung|standlueftung"), nachweis=_rx(r"standheizung|standlueftung")),
    Abhaengigkeit("ausstattung", "Anhängerkupplung",
                  _rx(r"anhaengerkupplung|\bahk\b"), nachweis=_rx(r"anhaengerkupplung|\bahk\b")),
    Abhaengigkeit("ausstattung", "Hinterachs-/Aktivlenkung",
                  _rx(r"hinterachslenkung|allradlenkung|aktivlenkung|integral ?aktivlenkung"),
                  nachweis=_rx(r"hinterachslenkung|allradlenkung|aktivlenkung")),
    Abhaengigkeit("ausstattung", "Komfortsitze",
                  _rx(r"massagesitz|massagefunktion|sitzbelueftung|sitzklimatisierung"),
                  nachweis=_rx(r"massage|sitzbelueftung|sitzklimatisierung|belueftete sitze")),
    Abhaengigkeit("ausstattung", "elektrische Heckklappe",
                  _rx(r"elektrische heckklappe|heckklappenantrieb"),
                  nachweis=_rx(r"elektrische heckklappe|heckklappe elektrisch|easy close")),
)


def abhaengigkeit(text: str | None) -> tuple[Abhaengigkeit | None, str | None]:
    """Erste passende Abhängigkeit + die im ORIGINALTEXT gefundene Bezeichnung
    (für "Falls EDC vorhanden" statt einer Klassenbezeichnung)."""
    n = _norm(text)
    if not n:
        return None, None
    for a in ABHAENGIGKEITEN:
        treffer = [_original_bezeichnung(text or "", m.group(0)) for m in a.muster.finditer(n)]
        treffer = [t for t in treffer if t]
        if treffer or a.muster.search(n):
            # Die SPEZIFISCHSTE Bezeichnung gewinnt: ein Produktname ("IntelliLink",
            # "COMAND", "EDC") vor einem Gattungswort ("Infotainmentsystem").
            spezifisch = [t for t in treffer if t.isupper() or any(c.isupper() for c in t[1:])]
            return a, (spezifisch or treffer or [a.klasse])[0]
    return None, None


def _original_bezeichnung(text: str, treffer_norm: str) -> str | None:
    """Das Wort im Originaltext, das dem normalisierten Treffer entspricht."""
    for wort in re.findall(r"[\wÄÖÜäöüß&-]+", text):
        if _norm(wort).startswith(treffer_norm.strip()) or treffer_norm.strip() in _norm(wort):
            return wort.strip("-")
    return None


def _moegliche_werte(identity, feld: str) -> set[str] | None:
    """Bekannte Werte eines Identitätsfelds; bei Mehrdeutigkeit die Menge der
    möglichen Werte; None = unbekannt."""
    if identity is None:
        return None
    fe = (getattr(identity, "field_evidence", None) or {}).get(feld) or {}
    if fe.get("verification_state") == "ambiguous" and fe.get("possible_values"):
        return {str(v) for v in fe["possible_values"]}
    wert = getattr(identity, feld, None)
    if wert in (None, ""):
        return None
    return {str(wert)}


_ATTR_FELD = {"getriebe": "transmission", "antrieb": "drivetrain", "kraftstoff": "fuel",
              "powertrain": "powertrain"}


def _normiere_attr(art: str, wert: str) -> str:
    w = wert.strip()
    if art == "getriebe":
        from app.getriebe import normalisiere
        return normalisiere(w) or w.lower()
    if art == "antrieb":
        return {"heck": "heck", "heckantrieb": "heck", "front": "front", "frontantrieb": "front",
                "allrad": "allrad", "allradantrieb": "allrad"}.get(w.lower(), w.lower())
    if art == "kraftstoff":
        from app.kraftstoff_powertrain import fuel_aus_freitext
        return fuel_aus_freitext(w) or w.lower()
    return w.upper()


def praesenz(text: str | None, identity=None, ausstattung=None,
             freitext: str | None = None) -> tuple[str, Abhaengigkeit | None, str | None]:
    """(presence_state, Abhängigkeit, Bezeichnung) einer Komponente.

    Ohne Abhängigkeit: PRESENT (die Komponente gehört zu jedem Fahrzeug dieser
    Art, z.B. Bremsen) — dann bleibt alles wie bisher."""
    a, bezeichnung = abhaengigkeit(text)
    if a is None:
        return PRESENT, None, None
    if a.art == "ausstattung":
        inserat = _norm(" ".join([*(ausstattung or []), freitext or ""]))
        if a.nachweis and a.nachweis.search(inserat):
            if re.search(r"(?:ohne|kein\w*|nicht vorhanden)\s+(?:\w+\s+){0,2}?(?:"
                         + a.nachweis.pattern + ")", inserat):
                return ABSENT, a, bezeichnung
            return PRESENT, a, bezeichnung
        return UNKNOWN, a, bezeichnung
    werte = _moegliche_werte(identity, _ATTR_FELD[a.art])
    if not werte:
        return UNKNOWN, a, bezeichnung
    normiert = {_normiere_attr(a.art, w) for w in werte}
    erlaubt = {_normiere_attr(a.art, e) for e in a.erfordert}
    if normiert <= erlaubt:
        return PRESENT, a, bezeichnung
    if not (normiert & erlaubt):
        return ABSENT, a, bezeichnung
    return UNKNOWN, a, bezeichnung


def bedingung(bezeichnung: str | None, a: Abhaengigkeit | None) -> str:
    """Der EINE Präfix für bedingte Punkte: "Falls EDC vorhanden: "."""
    name = bezeichnung or (a.klasse if a else "diese Ausstattung")
    verb = "verbaut" if a is not None and a.art != "ausstattung" else "vorhanden"
    return f"Falls {name} {verb}: "
