from __future__ import annotations

"""
Deterministisches Postprocessing für Gemini-Antworten (Final Polish).

Läuft NACH der Gemini-Ausgabe und VOR der Auslieferung an den Nutzer — für
Chat, Diagnose (= Chat-Untermodus, kein eigener Endpunkt), Kaufcheck und
Verkaufscheck gleichermaßen (siehe postprocess_answer()).

Harte Grenze (bewusst so gehalten): NUR eindeutig sichere, rein kosmetische
Transformationen. Kein Fakt wird ergänzt, kein Satz wird umformuliert, keine
inhaltliche Aussage wird verändert. Jede Regel hier arbeitet ausschließlich
auf Whitespace, Aufzählungszeichen und exakten Duplikat-/Floskel-Mustern —
nichts davon kann eine Zahl, einen Fachbegriff oder eine Empfehlung verändern.

Absichtlich NICHT implementiert (zu riskant für "niemals Inhalt verändern"):
  - Umformulierung "unnatürlicher Satzanfänge" — das ist Stilkorrektur, keine
    Kosmetik, und kann Bedeutung verschieben. Bleibt Aufgabe des Prompts.
  - Erkennung inhaltlicher Wiederholungen (zwei unterschiedlich formulierte
    Sätze mit gleicher Aussage) — nicht deterministisch von echten,
    beabsichtigten Wiederholungen (z. B. Tabellen mit ähnlichen Zeilen) zu
    unterscheiden. Nur EXAKT identische, unmittelbar aufeinanderfolgende
    Zeilen werden entfernt (siehe _entferne_duplikatzeilen).
"""

import re

# ---------- Code-Fences schützen ----------
# Dreifach-Backtick-Blöcke (falls die KI doch mal Code/Rohtext einbettet) werden
# von JEDER Transformation ausgenommen — Whitespace innerhalb von Code kann
# bedeutungstragend sein (Einrückung, Tabellen-Ausrichtung in Rohtext).
_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)


# ---------- 1) Einleitungsfloskeln ----------
# Nur Muster, die REIN als Gesprächseinstieg dienen und beim Entfernen keine
# Information verlieren — ausschließlich am absoluten Textanfang geprüft.
_EINLEITUNGSFLOSKELN: list[re.Pattern] = [
    re.compile(r"^sehr gerne[,!.:]?\s+", re.IGNORECASE),
    re.compile(r"^gerne[,!.:]?\s+", re.IGNORECASE),
    re.compile(r"^klar[,!.:]?\s+", re.IGNORECASE),
    re.compile(r"^natürlich[,!.:]?\s+", re.IGNORECASE),
    re.compile(r"^kein problem[,!.:]?\s+", re.IGNORECASE),
    re.compile(r"^ich helfe dir( sehr)?( gerne)?( weiter)?[,!.:]?\s+", re.IGNORECASE),
    re.compile(r"^gerne helfe ich dir( weiter)?[,!.:]?\s+", re.IGNORECASE),
    re.compile(
        r"^hier ist (deine|die|eine|der|das)\s+(analyse|antwort|einschätzung|übersicht|bericht)[:.]?\s*",
        re.IGNORECASE,
    ),
]

# ---------- 2) Schlussfloskeln ----------
# Nur am absoluten Textende geprüft, damit inhaltliche Sätze mit ähnlichen
# Wörtern (z. B. eine echte Rückfrage "Hast du noch weitere Angaben?") nicht
# getroffen werden.
_SCHLUSSFLOSKELN: list[re.Pattern] = [
    re.compile(r"ich hoffe,?\s*(das|dies|die antwort)?\s*(konnte\s+)?(dir\s+)?hilft?(\s+dir)?[!.]?\s*$", re.IGNORECASE),
    re.compile(
        r"bei (weiteren\s+)?fragen stehe ich (dir\s+)?(gerne|jederzeit)(\s+weiterhin)?(\s+gerne)?\s+zur verfügung[!.]?\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"lass es mich wissen,?\s*falls[^\n]*$", re.IGNORECASE),
    re.compile(r"falls du (noch\s+)?fragen hast,?\s*(melde dich|frag(\s+mich)?\s+gerne)[^\n]*$", re.IGNORECASE),
    re.compile(r"wenn du (noch\s+)?fragen hast,?\s*(lass es mich wissen|melde dich)[^\n]*$", re.IGNORECASE),
]

_MAX_FLOSKEL_ITERATIONEN = 3  # Schutz gegen pathologische Endlosschleifen bei Mehrfachtreffern

# Aufzählungszeichen vereinheitlichen: "*" und "•" -> "-" (rein visuell, ändert
# an der Aufzählung selbst nichts). Nur am Zeilenanfang (ggf. mit Einrückung).
_BULLET_MUSTER = re.compile(r"^(\s*)[\*•]\s+")

# Tabellenzeilen (Markdown-Pipes) werden von Space-Kollaps und Duplikat-Check
# ausgenommen — dort kann Spaltenausrichtung beabsichtigt sein.
_IST_TABELLENZEILE = re.compile(r"^\s*\|")


def _entferne_floskeln(text: str) -> str:
    for _ in range(_MAX_FLOSKEL_ITERATIONEN):
        neu = text
        for muster in _EINLEITUNGSFLOSKELN:
            neu = muster.sub("", neu, count=1)
        if neu == text:
            break
        text = neu
    for _ in range(_MAX_FLOSKEL_ITERATIONEN):
        neu = text
        for muster in _SCHLUSSFLOSKELN:
            neu = muster.sub("", neu, count=1).rstrip()
        if neu == text:
            break
        text = neu
    return text


def _bereinige_zeilen(segment: str) -> str:
    """Zeilenweise Kosmetik: Trailing-Whitespace, Bullet-Vereinheitlichung,
    doppelte Leerzeichen, exakte Duplikat-Zeilen — alles ohne Tabellenzeilen."""
    zeilen = segment.split("\n")
    ergebnis: list[str] = []
    vorherige_inhaltliche_zeile: str | None = None

    for zeile in zeilen:
        zeile = zeile.rstrip()

        if _IST_TABELLENZEILE.match(zeile):
            ergebnis.append(zeile)
            vorherige_inhaltliche_zeile = None
            continue

        zeile = _BULLET_MUSTER.sub(r"\1- ", zeile)

        # Doppelte Leerzeichen im Text kollabieren, Einrückung am Zeilenanfang erhalten.
        fuehrend_match = re.match(r"^(\s*)(.*)$", zeile, re.DOTALL)
        einrueckung, rest = fuehrend_match.group(1), fuehrend_match.group(2)
        rest = re.sub(r" {2,}", " ", rest)
        zeile = einrueckung + rest

        # Exakte, unmittelbar aufeinanderfolgende Duplikat-Zeilen entfernen
        # (z. B. wenn die KI einen Satz versehentlich zweimal ausgibt).
        if zeile.strip() and zeile == vorherige_inhaltliche_zeile:
            continue

        ergebnis.append(zeile)
        vorherige_inhaltliche_zeile = zeile if zeile.strip() else None

    return "\n".join(ergebnis)


def postprocess_answer(text: str) -> str:
    """
    Zentrale Postprocessing-Funktion für ALLE KI-Antworten (Chat, Diagnose,
    Kaufcheck, Verkaufscheck). Rein kosmetisch und deterministisch:

      1. Zeilenumbrüche normalisieren (\\r\\n / \\r -> \\n)
      2. Trailing Whitespace pro Zeile entfernen
      3. Aufzählungszeichen vereinheitlichen (*, • -> -)
      4. Doppelte Leerzeichen im Fließtext kollabieren (Tabellen ausgenommen)
      5. Exakte, unmittelbar aufeinanderfolgende Duplikat-Zeilen entfernen
      6. Mehr als eine Leerzeile auf genau eine Leerzeile kollabieren
      7. Bekannte Einleitungs-/Schlussfloskeln entfernen (nur am Textanfang/-ende)
      8. Führende/nachfolgende Leerzeichen am Gesamttext entfernen

    Code-Fences (```...```) werden von 2–6 ausgenommen, um technischen Inhalt
    nicht zu verändern. Verändert niemals Zahlen, Fachbegriffe oder die
    inhaltliche Aussage eines Satzes.
    """
    if not text:
        return text

    text = text.replace("\r\n", "\n").replace("\r", "\n")

    teile = _CODE_FENCE.split(text)
    fences = _CODE_FENCE.findall(text)
    bereinigt = [_bereinige_zeilen(teil) for teil in teile]

    zusammengesetzt: list[str] = []
    for i, teil in enumerate(bereinigt):
        zusammengesetzt.append(teil)
        if i < len(fences):
            zusammengesetzt.append(fences[i])
    text = "".join(zusammengesetzt)

    # Mehr als eine Leerzeile -> genau eine Leerzeile (max. zwei aufeinanderfolgende \n)
    text = re.sub(r"\n{3,}", "\n\n", text)

    text = text.strip()
    text = _entferne_floskeln(text)
    text = text.strip()

    return text
