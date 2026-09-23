from __future__ import annotations

"""
Claim-Sicherheit: Verkäuferangaben nie stärker formulieren als eingegeben.

VerkaufsCheck RC1. Im echten Golf-GTI-Check wurde aus "scheckheftgepflegt" ein
"lückenloses Scheckheft" und aus "unfallfrei laut Verkäufer" eine "bestehende
Unfallfreiheit". Der KaufCheck regelt das bisher nur im Prompt (app/kaufcheck.py,
Regel "formuliere sie NIE stärker als eingegeben"). Hier greift zusätzlich ein
deterministisches Sicherheitsnetz NACH dem Modell:

  "scheckheftgepflegt"               ≠ "lückenlose Wartungshistorie"
  "unfallfrei laut Verkäufer"        ≠ "nachweislich unfallfrei"
  "2 Vorbesitzer"                    ≠ "amtlich bestätigt"
  "keine bekannten technischen Mängel" ≠ "technisch mängelfrei"

Es werden nur VERSTÄRKUNGEN entschärft. Die Angabe selbst bleibt stehen.
"""

import re

# Verstärker vor Wartungsbegriffen: "lückenloses Scheckheft", "vollständige
# Wartungshistorie", "komplett nachweisbare Servicehistorie". Ein bis zwei Wörter
# Abstand sind erlaubt ("Lückenlose Dokumentation der Wartungshistorie"), damit das
# Netz nicht nur die unmittelbare Wortfolge fängt — begrenzt, um nicht über eine
# echte Satzgrenze zu springen.
_WARTUNG_NOMEN = r"(?:scheckheft\w*|wartungs\w*|service\w*|historie|inspektions\w*)"
_RE_WARTUNG_VERSTAERKT = re.compile(
    rf"\b(?:lückenlos|vollständig|komplett|durchgehend|lückenfrei)\w*\s+"
    rf"(?:(?:nachweisbar|dokumentiert|belegt|gepflegt)\w*\s+)?(?:\w+\s+){{0,2}}?({_WARTUNG_NOMEN})",
    re.IGNORECASE)
_RE_WARTUNG_PRAEDIKAT = re.compile(
    rf"\b({_WARTUNG_NOMEN})\s+(?:ist|sind|wurde|wurden)\s+(?:lückenlos|vollständig|komplett)\w*"
    r"(?:\s+(?:nachgewiesen|dokumentiert|belegt|vorhanden))?",
    re.IGNORECASE)

# Unfallfreiheit als Tatsache statt als Angabe.
_RE_UNFALL_VERSTAERKT = re.compile(
    r"\b(?:nachweislich|nachgewiesen|garantiert|zertifiziert|belegt|bestätigt|geprüft|"
    r"verifiziert|amtlich)\w*\s+(unfallfrei\w*)",
    re.IGNORECASE)
_RE_UNFALL_BESTEHEND = re.compile(
    r"\b(?:die\s+)?(?:bestehende|nachgewiesene|belegte|bestätigte|garantierte)\s+Unfallfreiheit\b",
    re.IGNORECASE)

# Technischer Zustand als Garantie statt als Kenntnisstand.
_RE_TECHNIK_VERSTAERKT = re.compile(
    r"\btechnisch\s+(?:einwandfrei|mängelfrei|makellos|perfekt|top|tadellos|fehlerfrei)\w*",
    re.IGNORECASE)
_RE_MAENGELFREI = re.compile(r"\b(?:völlig|absolut|komplett)?\s*mängelfrei\w*", re.IGNORECASE)

# Vorbesitzer als amtlich bestätigte Tatsache.
_RE_VORBESITZER_VERSTAERKT = re.compile(
    r"\b(?:amtlich|nachweislich|belegt|bestätigt|verifiziert)\w*\s+(\d+)\s+(Vorbesitzer\w*|Halter\w*)",
    re.IGNORECASE)

# RC1 Live-Closing: "Originalzustand ohne Tuning oder Umbauten" aus einer Eingabe
# wie "Keine." — das beweist keinen geprüften Originalzustand, nur, dass der
# Verkäufer keine Umbauten angegeben hat. Ein optionales Prädikat ("ist"/"sind")
# wird mitgefasst, damit die Ersetzung grammatisch stimmt ("ist im Originalzustand"
# -> "hat keine bekannten Umbauten" statt des holprigen "ist keine ...").
_RE_ORIGINALZUSTAND = re.compile(
    r"\b(?:(ist|sind)\s+)?(?:im\s+)?Originalzustand\b(?:\s+ohne\s+Tuning(?:\s+oder\s+Umbauten)?|"
    r"\s+ohne\s+Umbauten(?:\s+oder\s+Tuning)?)?",
    re.IGNORECASE)
_ORIGINALZUSTAND_VERBFORM = {"ist": "hat", "sind": "haben"}


def _gross_wie(vorlage: str, text: str) -> str:
    """Satzanfang erhalten: "Lückenloses Scheckheft" -> "Scheckheft"."""
    if vorlage[:1].isupper() and text:
        return text[:1].upper() + text[1:]
    return text


def entschaerfe_verstaerkungen(text: str, *, stimme: str = "bericht") -> tuple[str, list[str]]:
    """Entschärft verstärkte Verkäuferangaben. Rückgabe (text, geänderte_stellen).

    stimme="bericht": ENFAL spricht über das Fahrzeug -> "(laut deiner Angabe)".
    stimme="inserat": der Verkäufer spricht selbst -> ohne Zusatz, nur ohne
                      Verstärkung ("unfallfrei" statt "nachweislich unfallfrei").
    """
    if not text:
        return text, []
    geaendert: list[str] = []
    zusatz = " (laut deiner Angabe)" if stimme == "bericht" else ""

    def _ersetze(rx: re.Pattern, bau) -> None:
        nonlocal text

        def _sub(m: re.Match) -> str:
            neu = bau(m)
            if neu != m.group(0):
                geaendert.append(m.group(0))
            return neu
        text = rx.sub(_sub, text)

    _ersetze(_RE_WARTUNG_VERSTAERKT, lambda m: _gross_wie(m.group(0), m.group(1)))
    _ersetze(_RE_WARTUNG_PRAEDIKAT,
             lambda m: _gross_wie(m.group(0), f"{m.group(1)} ist angegeben"))
    def _unfall(m: re.Match) -> str:
        wort = m.group(1)
        if not zusatz:
            return _gross_wie(m.group(0), wort.lower())
        if wort.lower() == "unfallfrei":            # prädikativ: "... ist unfallfrei"
            return _gross_wie(m.group(0), wort.lower()) + zusatz
        # attributiv ("unfallfreies Fahrzeug"): Zusatz davor, sonst holpert der Satz
        return _gross_wie(m.group(0), "laut deiner Angabe " + wort.lower())
    _ersetze(_RE_UNFALL_VERSTAERKT, _unfall)
    _ersetze(_RE_UNFALL_BESTEHEND,
             lambda m: _gross_wie(m.group(0), "die angegebene Unfallfreiheit"))
    _ersetze(_RE_TECHNIK_VERSTAERKT,
             lambda m: _gross_wie(m.group(0), "ohne bekannte technische Mängel"))
    _ersetze(_RE_MAENGELFREI,
             lambda m: (" " if m.group(0).startswith(" ") else "")
             + _gross_wie(m.group(0).strip(), "ohne bekannte Mängel"))
    _ersetze(_RE_VORBESITZER_VERSTAERKT,
             lambda m: f"{m.group(1)} {m.group(2)}" + zusatz)
    def _original(m: re.Match) -> str:
        verb = (m.group(1) or "").lower()
        kern = "keine bekannten Umbauten" + zusatz
        praefix = _ORIGINALZUSTAND_VERBFORM.get(verb, verb)
        return _gross_wie(m.group(0), f"{praefix} {kern}" if praefix else kern)
    _ersetze(_RE_ORIGINALZUSTAND, _original)
    return text, geaendert
