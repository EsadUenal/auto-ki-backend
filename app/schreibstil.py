"""
Gemeinsame Schreibstil-Regel fuer alle generierten Nutzertexte.

WARUM ZENTRAL
-------------
KaufCheck, VerkaufsCheck, Inserats-Optimierung, KI-Chat und die
AutoFinder-Begruendungen schreiben alle deutschen Fliesstext fuer denselben
Nutzer. Stilregeln, die in fuenf Prompts einzeln formuliert sind, driften
auseinander: im KaufCheck stand bereits "Gedankenstriche sparsam", anderswo gar
nichts. Eine Konstante haelt die Regel an einer Stelle und macht sichtbar,
wenn eine Oberflaeche sie nicht verwendet.

ZWEI SCHICHTEN: PROMPT UND NETZ
-------------------------------
Die Regel gehoert zuerst in die Erzeugung: derselbe Strich trennt mal einen
Einschub (dort passt ein Komma), mal eine Erklaerung (dort passt ein
Doppelpunkt), und in "2019-2021" ist er korrekte Typografie. Ein Modell, das die
Regel kennt, waehlt das richtige Zeichen selbst.

Die Erzeugung ist aber nicht die einzige Quelle. Der Production-Run BMW M4 F82
zeigte "KLEINE Niere — klarer Unterschied zum G82": ein Text aus der
FAHRZEUGDATENBANK, den kein Prompt je gesehen hat, direkt im Fahrzeugprofil.
Deshalb gibt es zusaetzlich `entferne_gedankenstriche` als Netz fuer ALLE
Nutzertexte eines Ergebnisses. Es ist bewusst vorsichtig:

  * Zahlenbereiche bleiben Bereiche ("12.000 – 22.000 €" -> "12.000–22.000 €").
  * Bindestriche (E-Mail, KI-Chat, 2.0-TDI) werden nie angefasst.
  * Ein Strich-Paar um einen Einschub wird zu Kommas, ein Strich vor einem
    Anschlusswort ("— auch", "— sofern") zu einem Komma, jeder andere zu einem
    Doppelpunkt ("Niere — klarer Unterschied" -> "Niere: klarer Unterschied").
  * Ein Strich als leere Tabellenzelle wird zu "keine Angabe".
"""
from __future__ import annotations

import re

# Genau eine Zeile, damit sie sich in jede vorhandene STIL-Liste einfuegt, ohne
# deren Aufbau zu veraendern. Die Beispiele sind Absicht: die blosse Anweisung
# "keine Gedankenstriche" fuehrt sonst zu Saetzen, die mit Komma
# aneinandergehaengt werden, statt zu richtigem Deutsch.
STILREGEL_GEDANKENSTRICHE = (
    "Schreibe natürliches Deutsch und verwende KEINE langen Gedankenstriche "
    "(– oder —) als Stilmittel. Nutze stattdessen das Satzzeichen, das an der "
    "Stelle grammatisch richtig ist: Punkt für zwei eigenständige Aussagen, "
    "Komma oder Klammern für einen Einschub, Doppelpunkt vor einer Aufzählung "
    "oder Erklärung. Variiere dabei, statt jeden Satz nach demselben Muster zu "
    "bauen. Normale Bindestriche in Wörtern (E-Mail, KI-Chat, 2.0-TDI) bleiben "
    "selbstverständlich erhalten, ebenso Zahlenbereiche wie 2019-2021."
)

# Ein rhetorischer Strich ist ein langer Strich (– oder —) mit Leerraum davor
# und danach. Diesen Test verwenden auch die Regressionstests.
RE_RHETORISCH = re.compile(r"\s[—–]\s")

_EINHEIT = r"(?:€|EUR|km|tkm|PS|kW|Nm|%|Jahre?|Monate?)"
# Eine Zahl endet nie auf einem Trennzeichen: "2020." am Satzende ist "2020".
_ZAHL = r"\d(?:[\d.,]*\d)?"
_BEREICH_OHNE_EINHEIT = re.compile(rf"({_ZAHL})\s*[–—]\s*({_ZAHL})")
_BEREICH_MIT_EINHEIT = re.compile(
    rf"({_ZAHL})\s?({_EINHEIT})\s*[–—]\s*({_ZAHL})\s?\2(?![\wäöüß])")


def _zahl(text: str) -> float | None:
    try:
        return float(text.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _bereich(m: re.Match) -> str:
    """Ein Strich zwischen zwei Zahlen ist nur dann ein Bereich, wenn beide
    Zahlen gleich geschrieben sind und aufsteigen ("60 – 80", "12.000 – 22.000").
    "Baujahr 2016 — 69.500 km" ist kein Bereich und bleibt für die normale
    Behandlung stehen."""
    links, rechts = m.group(1), m.group(2)
    a, b = _zahl(links), _zahl(rechts)
    if a is not None and b is not None and ("." in links) == ("." in rechts) and b >= a:
        return f"{links}–{rechts}"
    return m.group(0)
_LEERE_ZELLE = re.compile(r"\|\s*[–—]\s*(?=\|)")
_ZELLENANFANG = re.compile(r"(\|\s*)[–—]\s+")
_ZEILENANFANG = re.compile(r"^(\s*)[–—]\s+", re.MULTILINE)
_STRICH = re.compile(r"(?<=\S)\s+[–—]\s+(?=\S)")
_STRICH_OHNE_LEERRAUM = re.compile(r"(?<=[^\W\d_])—(?=[^\W\d_])")
# Nach diesen Wörtern setzt der Strich einen Satz fort: dort passt ein Komma.
_ANSCHLUSSWORTE = frozenset({
    "sofern", "wenn", "weil", "da", "denn", "aber", "doch", "also", "und", "oder",
    "sondern", "obwohl", "falls", "solange", "bevor", "nachdem", "damit", "sodass",
    "zumal", "insbesondere", "etwa", "auch", "jedoch", "allerdings", "wie", "ohne",
    "mit", "bei", "nur", "nicht", "je", "vor", "vor allem", "zum", "zur", "was", "wobei",
})


def _zeile_bereinigen(zeile: str) -> str:
    zeile = _BEREICH_MIT_EINHEIT.sub(r"\1–\3 \2", zeile)
    zeile = _BEREICH_OHNE_EINHEIT.sub(_bereich, zeile)
    zeile = _LEERE_ZELLE.sub("| keine Angabe ", zeile)
    zeile = _ZELLENANFANG.sub(r"\1", zeile)
    treffer = list(_STRICH.finditer(zeile))
    if not treffer:
        return _STRICH_OHNE_LEERRAUM.sub(", ", zeile)
    # Zwei Striche im selben Satz umschließen einen Einschub.
    paar = set()
    for a, b in zip(treffer, treffer[1:]):
        dazwischen = zeile[a.end():b.start()]
        if not re.search(r"[.!?]", dazwischen) and len(dazwischen) <= 80:
            paar.update({a.start(), b.start()})
    teile, pos = [], 0
    for m in treffer:
        folgt = zeile[m.end():].lstrip()
        wort = re.match(r"[\wäöüÄÖÜß]+", folgt)
        wort = wort.group(0).lower() if wort else ""
        ersatz = ", " if (m.start() in paar or wort in _ANSCHLUSSWORTE) else ": "
        teile.append(zeile[pos:m.start()])
        teile.append(ersatz)
        pos = m.end()
    teile.append(zeile[pos:])
    return _STRICH_OHNE_LEERRAUM.sub(", ", "".join(teile))


def entferne_gedankenstriche(text: str | None) -> str | None:
    """Ersetzt rhetorische Gedankenstriche durch das passende Satzzeichen.

    Arbeitet zeilenweise, damit ein Strich nie über eine Zeile hinweg gedeutet
    wird. Text ohne langen Strich kommt unverändert zurück.
    """
    if not text or ("—" not in text and "–" not in text):
        return text
    text = _ZEILENANFANG.sub(r"\1- ", text)
    return "\n".join(_zeile_bereinigen(z) for z in text.split("\n"))


# Felder, die keine Nutzertexte sind (IDs, Aufzählungswerte, Quellen). Quellen
# und Belege bleiben unverändert: das sind Titel fremder Seiten, keine
# ENFAL-Texte, und ein Zitat darf nicht umformuliert werden.
_NICHT_BEREINIGEN = frozenset({
    "id", "url", "ref", "kategorie", "typ", "status", "trust", "confidence",
    "applicability", "schweregrad", "prioritaet", "bereich", "wartungsart", "fakt_ref",
    "herkunft", "lauf_id", "research_status", "empfehlung", "preis_bewertung", "quelle",
    "vertrauen", "baureihe_erkannt", "motor_erkannt", "identitaet_konfidenz",
    "identitaet_match_art", "technical_coverage", "verdict", "position_in_range",
    "evidence_id", "icon", "baureihe_id", "servicehistorie", "gruppe_id",
})
_NICHT_ABSTEIGEN = frozenset({
    "belege", "quellen", "quellen_typen", "evidence_ids", "empfehlung_evidence_ids",
    "preis_evidence_ids", "risiko_evidence_ids", "zusammengefuehrt", "nebenbelege",
    "marktanalyse", "web_identitaet",
})


def bereinige_nutzertexte(obj):
    """Wendet `entferne_gedankenstriche` auf alle Nutzertexte einer Struktur an.

    Versteht Strings, Listen, Dicts und Pydantic-Modelle; gibt eine bereinigte
    Kopie zurück und lässt IDs, Aufzählungswerte und Quellen unangetastet.
    """
    from pydantic import BaseModel

    if isinstance(obj, str):
        return entferne_gedankenstriche(obj)
    if isinstance(obj, list):
        return [bereinige_nutzertexte(x) for x in obj]
    if isinstance(obj, dict):
        return {k: (v if k in _NICHT_BEREINIGEN or k in _NICHT_ABSTEIGEN
                    else bereinige_nutzertexte(v)) for k, v in obj.items()}
    if isinstance(obj, BaseModel):
        neu = {}
        for name in type(obj).model_fields:
            if name in _NICHT_BEREINIGEN or name in _NICHT_ABSTEIGEN:
                continue
            wert = getattr(obj, name)
            bereinigt = bereinige_nutzertexte(wert)
            if bereinigt is not wert and bereinigt != wert:
                neu[name] = bereinigt
        return obj.model_copy(update=neu) if neu else obj
    return obj
