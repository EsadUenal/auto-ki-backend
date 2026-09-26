from __future__ import annotations

"""
Servicehistorie laut Inserat — strukturiert statt Ja/Nein.

VORHER: eine Checkbox `scheckheftgepflegt` (bool | None). Sie kannte drei
Zustaende, von denen das Frontend nur zwei senden konnte (`false` wurde als
`undefined` uebertragen, der False-Zweig war aus der Oberflaeche also nicht
erreichbar) — und "angekreuzt" musste gleichzeitig "vollstaendig", "teilweise"
und "irgendwas ist vorhanden" bedeuten.

JETZT: vier ausgesprochene Zustaende. Sie sind AUSNAHMSLOS Angaben des Inserats.

  vollstaendig_angegeben  Das Inserat behauptet eine vollstaendige Historie.
  teilweise               Das Inserat raeumt Luecken ein.
  umfang_unklar           Etwas ist vorhanden, der Umfang bleibt offen.
  nicht_vorhanden         Das Inserat gibt an, dass keine Historie vorliegt.
  None                    Das Inserat sagt dazu nichts.

CLAIM-SAFETY — der Kern dieses Moduls:

  "vollstaendig_angegeben" heisst NICHT, dass die Historie vollstaendig IST.
  ENFAL hat kein Serviceheft gesehen, keine Rechnung geprueft und keinen
  Herstellerdatensatz abgefragt. Deshalb tragen alle Texte dieses Moduls das
  "laut Inserat" IM SATZ und nicht als Fussnote, und keiner von ihnen entfernt
  ein Risiko: auch die Bestangabe erzeugt eine PRUEF-Aufforderung.

  Erlaubt:      "Laut Inserat wird eine vollständige Servicehistorie angegeben."
  Nicht erlaubt: "Das Fahrzeug ist lückenlos scheckheftgepflegt."
                 "Die Wartungen wurden vollständig durchgeführt."
                 "Die Historie ist nachweislich vollständig."

LEGACY: `scheckheftgepflegt` bleibt im Request-Schema und wird weiterhin
gelesen. Gespeicherte Checks und aeltere Clients senden nur dieses Feld;
`status()` bildet es ab (True -> vollstaendig_angegeben, False/None -> keine
Angabe). Bereits erzeugte Berichte werden NICHT nachtraeglich umgeschrieben.
"""

import re

VOLLSTAENDIG_ANGEGEBEN = "vollstaendig_angegeben"
TEILWEISE = "teilweise"
UMFANG_UNKLAR = "umfang_unklar"
NICHT_VORHANDEN = "nicht_vorhanden"
STATI = (VOLLSTAENDIG_ANGEGEBEN, TEILWEISE, UMFANG_UNKLAR, NICHT_VORHANDEN)

_SYNONYME = {
    VOLLSTAENDIG_ANGEGEBEN: ("vollstaendig_angegeben", "vollständig_angegeben",
                             "vollstaendig", "vollständig", "voll"),
    TEILWEISE: ("teilweise", "teilweise_vorhanden", "teil"),
    UMFANG_UNKLAR: ("umfang_unklar", "unklar", "vorhanden_unklar"),
    NICHT_VORHANDEN: ("nicht_vorhanden", "keine", "nein", "fehlt"),
}

# Kanonische Saetze. EINE Quelle fuer Prompt, Key Findings, Empfehlungsgruende
# und Checkliste — damit dieselbe Angabe nicht an vier Stellen unterschiedlich
# stark formuliert wird.
_SATZ = {
    VOLLSTAENDIG_ANGEGEBEN: "Laut Inserat wird eine vollständige Servicehistorie angegeben.",
    TEILWEISE: "Die Servicehistorie ist laut Inserat nur teilweise vorhanden.",
    UMFANG_UNKLAR: "Laut Inserat ist eine Servicehistorie vorhanden, ihr Umfang bleibt offen.",
    NICHT_VORHANDEN: "Laut Inserat liegt keine Servicehistorie vor.",
}
_KEINE_ANGABE = "Zur Servicehistorie enthält das Inserat keine klare Angabe."

# Kurzform fuer Prompt-/Wertzeilen.
_ANZEIGE = {
    VOLLSTAENDIG_ANGEGEBEN: "vollständig angegeben (laut Inserat)",
    TEILWEISE: "teilweise vorhanden (laut Inserat)",
    UMFANG_UNKLAR: "vorhanden, Umfang unklar (laut Inserat)",
    NICHT_VORHANDEN: "nicht vorhanden (laut Inserat)",
}


def normalisiere(wert: object) -> str | None:
    """Tolerante Normalisierung auf einen der vier Stati oder None.

    Unbekanntes wird None statt 422 (Konvention der optionalen Auswahlfelder).
    """
    if wert is None:
        return None
    text = str(wert).strip().lower()
    if not text:
        return None
    for status_wert, woerter in _SYNONYME.items():
        if text in woerter:
            return status_wert
    return None


def status(req) -> str | None:
    """Kanonischer Status dieses Checks, inklusive Legacy-Abbildung.

    Das neue Feld gewinnt immer. Nur wenn es fehlt, wird die alte Checkbox
    gelesen: `True` war die einzige Aussage, die sie ueberhaupt uebertragen
    konnte, und sie bedeutete "laut Inserat gepflegt" — das entspricht
    "vollstaendig angegeben". `False` und `None` waren in der alten Oberflaeche
    nicht unterscheidbar (beide kamen als fehlendes Feld an) und bleiben
    deshalb "keine Angabe": daraus eine Aussage zu machen, waere eine
    Behauptung ueber ein Inserat, das nie etwas behauptet hat.
    """
    neu = normalisiere(getattr(req, "servicehistorie", None))
    if neu:
        return neu
    if getattr(req, "scheckheftgepflegt", None) is True:
        return VOLLSTAENDIG_ANGEGEBEN
    return None


def satz(status_wert: str | None) -> str:
    """Der kanonische, claim-sichere Satz zu einem Status."""
    return _SATZ.get(status_wert or "", _KEINE_ANGABE)


def anzeige(status_wert: str | None) -> str | None:
    return _ANZEIGE.get(status_wert or "")


def prompt_zeile(status_wert: str | None) -> str | None:
    label = anzeige(status_wert)
    return f"Servicehistorie: {label}" if label else None


# ── Deterministisches Sicherheitsnetz fuer den Berichtstext ───────────────────
#
# Der Prompt verbietet die Verstaerkung bereits (app/kaufcheck.py). Dieses Netz
# greift NACH dem Modell und ist bewusst eng: es faengt nur die ZUSICHERUNG,
# nicht die Aufforderung.
#
#   gefangen:      "ist lückenlos scheckheftgepflegt"
#                  "Die Wartungen wurden vollständig durchgeführt"
#                  "Es liegt ein lückenloses Scheckheft vor"
#   nicht gefangen: "Vollständigkeit der Servicehistorie prüfen"
#                  "Nachweise für die angegebene Servicehistorie verlangen"
#                  "Laut Inserat wird eine vollständige Servicehistorie angegeben"
#
# ZWEI Schutzgitter, weil eines nicht reichte:
#
#  1. Das Praedikat. Ohne "ist/sind/wurde/wurden" wird die praedikative Form
#     nicht angefasst — eine Aufforderung ("prüfen", "verlangen") bleibt stehen.
#
#  2. Die Zuschreibung. Traegt der SATZ bereits ein "laut Inserat" (bzw. laut
#     Anzeige/Verkäufer/Angabe), ist genau das erreicht, was dieses Netz
#     herstellen soll — dann wird er unveraendert gelassen. Ohne dieses zweite
#     Gitter zerlegte das Netz die eigenen kanonischen Saetze oben: aus "Laut
#     Inserat wird eine vollständige Servicehistorie angegeben" wurde "... eine
#     Servicehistorie angegeben". Genau dieser Fall stand im Test, bevor die
#     Regel existierte.
_VERSTAERKER = r"(?:lückenlos|lückenfrei|vollständig|komplett|durchgehend|nachweislich)"

# Satzweise Verarbeitung: die Zuschreibung gilt fuer IHREN Satz, nicht fuer den
# ganzen Bericht — ein "laut Inserat" im ersten Absatz darf eine Zusicherung im
# dritten nicht freigeben.
_RE_SATZ = re.compile(r"[^.!?\n]*(?:[.!?\n]+|$)")
_RE_ZUSCHREIBUNG = re.compile(
    r"laut\s+(?:inserat|anzeige|verkäufer\w*|angabe\w*)|nach\s+angaben\s+des", re.IGNORECASE)

_RE_ZUSICHERUNG = re.compile(
    rf"\b(ist|sind|wurde|wurden)\s+{_VERSTAERKER}(?:e[mnrs]?|es)?\s+"
    r"(scheckheftgepflegt|gewartet|gepflegt|dokumentiert|nachgewiesen|belegt|durchgeführt)",
    re.IGNORECASE)

_RE_ATTRIBUTIV = re.compile(
    rf"\b{_VERSTAERKER}(?:e[mnrs]?|es)\s+"
    r"(scheckheft\w*|wartungshistorie|servicehistorie|wartungsnachweise?)",
    re.IGNORECASE)


def _gross_wie(vorlage: str, text: str) -> str:
    """Satzanfang erhalten: "Lückenloses Scheckheft" -> "Scheckheft"."""
    if vorlage[:1].isupper() and text:
        return text[:1].upper() + text[1:]
    return text


def neutralisiere_claims(text: str) -> tuple[str, list[str]]:
    """Entfernt Zusicherungen zur Servicehistorie. Rueckgabe (text, ersetzt).

    Der Satz bleibt stehen und bleibt grammatisch — es verschwindet nur der
    Beweischarakter: der Verstaerker weicht dem "laut Inserat", bzw. faellt in
    der attributiven Form ganz weg ("ein lückenloses Scheckheft" -> "ein
    Scheckheft"). Laeuft unabhaengig vom Status: auch bei "vollstaendig
    angegeben" ist die Vollstaendigkeit unbelegt, solange ENFAL keine Dokumente
    gesehen hat.
    """
    if not text:
        return text, []
    ersetzt: list[str] = []

    def _zusicherung(m: re.Match) -> str:
        neu = f"{m.group(1)} laut Inserat {m.group(2)}"
        ersetzt.append(m.group(0))
        return _gross_wie(m.group(0), neu)

    def _attributiv(m: re.Match) -> str:
        ersetzt.append(m.group(0))
        return _gross_wie(m.group(0), m.group(1))

    teile: list[str] = []
    for satz in _RE_SATZ.findall(text):
        if not satz:
            continue
        if _RE_ZUSCHREIBUNG.search(satz):
            # Die Angabe ist im Satz bereits als Angabe gekennzeichnet.
            teile.append(satz)
            continue
        satz = _RE_ZUSICHERUNG.sub(_zusicherung, satz)
        satz = _RE_ATTRIBUTIV.sub(_attributiv, satz)
        teile.append(satz)
    return "".join(teile), ersetzt
