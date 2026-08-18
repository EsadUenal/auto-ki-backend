"""
Schreibvarianten von Markennamen.

Warum das KEIN Fahrzeugwissen ist
---------------------------------
"VW" und "Volkswagen" bezeichnen denselben Hersteller — das ist Rechtschreibung,
keine Aussage über ein Fahrzeug. Deshalb fällt diese Tabelle NICHT unter die
Vertrauensregel für DB-Fakten (app/verification.py): sie stammt nicht aus der
generierten Fahrzeug-DB, sondern ist im Code gepflegt und einzeln nachvollziehbar.

Ein falscher Eintrag hier könnte höchstens zwei Marken verwechseln; er könnte
keine Fahrzeugidentität erfinden. Modell-, Generations- und Motorangaben bleiben
davon unberührt und weiterhin trust-pflichtig.

Bestehende Kopien
-----------------
`app/car_lookup.py` und `app/llm.py` führen historisch je eine eigene Alias-Tabelle
mit demselben Inhalt. Diese Datei ist als gemeinsame Quelle gedacht; die beiden
Kopien bleiben vorerst unangetastet, um den Lookup-Pfad nicht zu bewegen.
"""
from __future__ import annotations

import re

# alias (lowercase) -> kanonischer Markenname
MARKEN_ALIAS: dict[str, str] = {
    "vw": "Volkswagen",
    "mercedes": "Mercedes-Benz",
    "merc": "Mercedes-Benz",
    "benz": "Mercedes-Benz",
    "skoda": "Škoda",
    "škoda": "Škoda",
}


def _worte(text: str) -> set[str]:
    return {t for t in re.split(r"[^0-9a-zA-ZäöüßÄÖÜŠš]+", (text or "").lower()) if t}


def marke_tokens(marke: str | None) -> set[str]:
    """Alle Schreibvarianten einer Marke als Wort-Token.

    "Volkswagen" -> {"volkswagen", "vw"}
    "Mercedes-Benz" -> {"mercedes", "benz", "merc"}

    Ohne diese Erweiterung findet der Marken-Anker im Inserat die Marke nicht:
    Anzeigen schreiben "VW Passat", das Formular sagt "Volkswagen".
    """
    tokens = _worte(marke)
    if not tokens:
        return tokens
    kanonisch = (marke or "").strip().lower()
    for alias, kanon in MARKEN_ALIAS.items():
        kl = kanon.lower()
        if kl == kanonisch or _worte(kanon) & tokens:
            tokens.add(alias)
            tokens |= _worte(kanon)
    return tokens
