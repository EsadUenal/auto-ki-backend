from __future__ import annotations

"""
Report-Validator gegen die Rückruf-Allowed-List (Reliability-Sprint 4, §Phase 8).

Letztes Sicherheitsnetz NACH dem Gemini-Call: auch wenn `build_db_context`/
`_sql_context` (app/recall_filter.py, §Phase 7) ausgeschlossene Rückrufe nicht mehr
in den Prompt geben, kann ein LLM Begriffe aus anderswo im Kontext (z.B. dem
DB-Profil/Schwachstellen-Text) frei kombinieren oder halluzinieren. Dieser
Validator scannt den FERTIGEN Freitext-Bericht (inkl. Besichtigungscheckliste,
Teil desselben `bericht`-Strings) nach Kernbegriffen der für dieses Fahrzeug
AUSGESCHLOSSENEN Rückrufe (aus `mangel`/`abhilfe`) und entfernt NUR eindeutig
zuordenbare Sätze/Zeilen — keine allgemeinen Wörter, keine ganzen fachlich
korrekten Absätze.

Bewusst NICHT als zweiter Gemini-Call (Kosten/Latenz) — reine deterministische
Textbereinigung, im selben Stil wie app/postprocess.py (nur kosmetisch, kein Fakt
wird ergänzt).
"""

import logging
import re

log = logging.getLogger(__name__)

_SATZ_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORT = re.compile(r"[A-Za-zÄÖÜäöüß]{6,}")
_ZEILEN_PRAEFIX = re.compile(r"^(\s*(?:[-*]\s*(?:\[[ xX]\]\s*)?|\d+[.)]\s*))")

# Häufige lange, aber fachlich UNSPEZIFISCHE Wörter aus KBA-Rückruftexten — dürfen
# niemals allein einen Satz ausschließen. Reliability-Sprint-4-Live-Befund: der
# `abhilfe`-Text folgt einem generischen Baukasten ("Prüfung und ggf. Austausch
# des/der X"), der wortgleich auch in ERLAUBTEN Rückrufen desselben Fahrzeugs
# steht — ein Match auf 'Prüfung' hätte sonst auch die legitime FIN-Prüf-
# Empfehlung selbst entfernt. Deshalb: (1) Kernbegriffe NUR aus `mangel`
# extrahieren (die konkrete Defektbeschreibung, nicht die generische Abhilfe-
# Floskel), UND (2) zusätzlich gegen die Begriffe der ERLAUBTEN Rückrufe
# differenzieren (siehe pruefe_bericht) — ein Begriff, der auch in einem
# erlaubten Rückruf vorkommt, ist nicht eindeutig zuordenbar und bleibt erlaubt.
_STOPWORT_LANG = {
    "sollte", "sollten", "können", "koennen", "müssen", "muessen", "werden",
    "worden", "wurden", "diesem", "dieser", "dieses", "einem", "einer", "eines",
    "sowie", "wichtig", "achten", "prüfen", "pruefen", "prüfung", "pruefung",
    "überprüfung", "ueberpruefung", "checkliste", "empfehlung", "empfohlen",
    "fahrzeug", "fahrzeugs", "besichtigung", "vorhanden", "möglich", "moeglich",
    "generell", "insgesamt", "bereich", "bereits", "bezüglich", "beziehen",
    "gegebenenfalls", "insbesondere", "möglicher", "moeglicher", "ausfall",
    "austausch", "werkstatt", "sicherheit", "sicherheitsrelevant", "kontrolle",
    "reparatur", "rückrufaktion", "rueckrufaktion", "durchführung",
    "durchfuehrung", "fahrgestellnummer", "betroffen", "betroffenheit",
    "hersteller", "rückruf", "rueckruf", "aufgrund", "fehlerhafte",
    "fehlerhaften", "fehlerhaftes",
}


def _kernbegriffe(recall: dict, *, nur_mangel: bool = True) -> set[str]:
    """Signifikante (>=6 Zeichen, nicht generische) Wörter aus einem Rückruf —
    standardmäßig NUR aus `mangel` (die konkrete Defektbeschreibung, z.B.
    'hochvoltbatterie', 'brandgefahr'). `abhilfe` wird bewusst NICHT einbezogen
    (siehe Modul-Kommentar oben — generische Floskeln). Für die Gegenprobe gegen
    ERLAUBTE Rückrufe (`nur_mangel=False`) zählt sicherheitshalber auch `abhilfe`,
    damit ein dort auftauchender Begriff den Ausschluss zuverlässig entschärft."""
    felder = [recall.get("mangel")] if nur_mangel else [recall.get("mangel"), recall.get("abhilfe")]
    text = " ".join(filter(None, felder))
    woerter = {w.lower() for w in _WORT.findall(text)}
    return woerter - _STOPWORT_LANG


def pruefe_bericht(bericht: str, ausgeschlossene_rueckrufe: list[dict] | None,
                   erlaubte_rueckrufe: list[dict] | None = None) -> tuple[str, list[str]]:
    """Entfernt aus `bericht` (inkl. Checkliste) jeden Satz/jede Zeile, die einen
    eindeutigen Kernbegriff eines für dieses Fahrzeug AUSGESCHLOSSENEN Rückrufs
    enthält — UND der nicht auch in einem für dieses Fahrzeug weiterhin ERLAUBTEN
    Rückruf vorkommt (Differenzmenge, verhindert False Positives durch generische
    Rückruf-Textbausteine, siehe Modul-Kommentar).

    Zeilen OHNE Treffer bleiben byte-identisch erhalten. Eine Zeile mit Treffer
    wird satzweise bereinigt; bleibt danach nichts übrig (typisch bei einer
    Checklisten-/Bullet-Zeile = ein Satz), fällt die ganze Zeile weg.

    Gibt (bereinigter_bericht, warnungen) zurück — `warnungen` nur für Logging,
    NICHT für die Anzeige."""
    if not bericht or not ausgeschlossene_rueckrufe:
        return bericht, []

    verboten: set[str] = set()
    for r in ausgeschlossene_rueckrufe:
        verboten |= _kernbegriffe(r, nur_mangel=True)
    erlaubte_begriffe: set[str] = set()
    for r in erlaubte_rueckrufe or []:
        erlaubte_begriffe |= _kernbegriffe(r, nur_mangel=False)
    verboten -= erlaubte_begriffe
    if not verboten:
        return bericht, []

    warnungen: list[str] = []
    neue_zeilen: list[str] = []
    for zeile in bericht.split("\n"):
        saetze = _SATZ_SPLIT.split(zeile) if zeile.strip() else [zeile]
        behalten: list[str] = []
        treffer_in_zeile = False
        for satz in saetze:
            treffer = next((w for w in verboten if w in satz.lower()), None)
            if treffer:
                treffer_in_zeile = True
                warnungen.append(f"Satz wegen ausgeschlossenem Rückruf-Begriff {treffer!r} entfernt: {satz.strip()[:100]!r}")
                continue
            behalten.append(satz)

        if not treffer_in_zeile:
            neue_zeilen.append(zeile)   # unverändert — kein Risiko, nichts zu tun.
            continue

        rest = " ".join(s.strip() for s in behalten if s.strip()).strip()
        if not rest:
            continue   # ganze Zeile (z.B. Checklisten-Bullet) entfernen.
        praefix_match = _ZEILEN_PRAEFIX.match(zeile)
        praefix = praefix_match.group(1) if praefix_match else ""
        if praefix and not rest.startswith(praefix.strip()):
            neue_zeilen.append(f"{praefix}{rest}")
        else:
            neue_zeilen.append(rest)

    bereinigt = "\n".join(neue_zeilen)
    if warnungen:
        log.warning("Report-Validator: %d Satz/Zeile wegen ausgeschlossener Rückrufe entfernt: %s",
                    len(warnungen), "; ".join(warnungen))
    return bereinigt, warnungen
