from __future__ import annotations

"""
Synchronisiert die fettgedruckte Risikostufen-Überschrift im "## Kaufempfehlung"-
Abschnitt des Berichts mit der FINALEN strukturierten Empfehlung.

PROBLEM: Der deterministische Empfehlungs-Floor (app/empfehlungs_floor.py) kann
die Empfehlung NACH dem LLM-Call anheben. Der Bericht wurde aber VOM LLM für die
ROHE (ungehobene) Empfehlung geschrieben — er zeigt dann z.B.
"**KAUFEN NACH BESICHTIGUNG**", während das strukturierte Feld bereits
"nur_mit_werkstattpruefung" trägt. Diese Diskrepanz darf den Nutzer nie
erreichen: Bericht und Feld müssen dieselbe Aussage treffen, und die
strukturierten Daten sind die Wahrheit (§3 der Aufgabenstellung).

LÖSUNG: die kleinstmögliche deterministische Textänderung — NUR die fettgedruckte
Überschrift direkt unter "## Kaufempfehlung" wird ersetzt. Der Rest des
Abschnitts (die technische Begründung, die weiterhin auf denselben Risiken
beruht) bleibt unverändert; ihn neu zu generieren wäre keine "kleinste Lösung"
mehr und würde Fakten riskieren, die das LLM nicht neu bewertet hat.

Wird NUR aufgerufen, wenn der Floor tatsächlich angehoben hat (siehe
`app/kaufcheck.py`) — hat er nicht gegriffen, bleibt der Bericht komplett
unangetastet.
"""

import logging
import re

log = logging.getLogger(__name__)

# Anzeige-Text je Enum-Wert — dieselbe Wortwahl, die car_lookup._EMPFEHLUNG_MUSTER
# beim Rück-Erkennen aus Freitext bereits erwartet (car_lookup.py), damit Erzeugung
# und Erkennung konsistent bleiben. "unbekannt" hat bewusst KEINEN Anzeige-Text:
# der Floor hebt "unbekannt" nie an (app/empfehlungs_floor.py), diese Funktion
# müsste also nie dafür synchronisieren.
_ANZEIGE_TEXT: dict[str, str] = {
    "kaufen": "KAUFEN",
    "kaufen_nach_besichtigung": "KAUFEN NACH BESICHTIGUNG",
    "nur_mit_werkstattpruefung": "NUR MIT WERKSTATTPRÜFUNG",
    "preis_nachverhandeln": "PREIS NACHVERHANDELN",
    "hohes_risiko": "HOHES RISIKO",
    "finger_weg": "FINGER WEG",
}

# "## Kaufempfehlung" (Systemprompt-Format, siehe app/kaufcheck.py _SYSTEM),
# gefolgt (ggf. mit Leerzeile) von der fettgedruckten Risikostufe. Nur die
# **...**-Gruppe wird ersetzt — Überschrift und der Rest des Absatzes bleiben
# byte-identisch.
_KAUFEMPFEHLUNG_UEBERSCHRIFT = re.compile(
    r"(##\s*Kaufempfehlung\s*\n+\s*\*\*)([^*\n]+)(\*\*)", re.IGNORECASE)


def synchronisiere_kaufempfehlung(bericht: str | None, finale_empfehlung: str | None) -> str | None:
    """Ersetzt ausschließlich die fettgedruckte Überschrift im
    "## Kaufempfehlung"-Abschnitt durch den zur finalen Empfehlung passenden
    Text. Gibt den Bericht unverändert zurück, wenn:
      - kein Bericht vorliegt,
      - `finale_empfehlung` keinen bekannten Anzeige-Text hat (z.B. "unbekannt"),
      - der Abschnitt im Bericht nicht gefunden wird (z.B. eine reine Rückfrage
        ohne volle Struktur — dann bleibt das strukturierte Feld maßgeblich,
        aber es gibt nichts zu synchronisieren).
    """
    if not bericht:
        return bericht
    anzeige = _ANZEIGE_TEXT.get((finale_empfehlung or "").strip().lower())
    if not anzeige:
        return bericht

    neu, n = _KAUFEMPFEHLUNG_UEBERSCHRIFT.subn(
        lambda m: f"{m.group(1)}{anzeige}{m.group(3)}", bericht, count=1)
    if n:
        log.info("Kaufempfehlung-Sync: Bericht-Überschrift auf %r synchronisiert.", anzeige)
    else:
        log.warning("Kaufempfehlung-Sync: '## Kaufempfehlung'-Überschrift nicht gefunden — "
                    "Bericht konnte nicht synchronisiert werden (strukturiertes Feld bleibt "
                    "unabhängig davon maßgeblich).")
    return neu
