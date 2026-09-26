from __future__ import annotations

"""
Getriebeart: EINE kanonische Quelle für "automatik" | "manuell".

Vorher stand diese Logik ausschliesslich in app/kaufaktionen.py und kannte nur
zwei Quellen: Schluesselwoerter im Freitext (Motorfeld, Beschreibung, Inserats-
text) und — wenn der Freitext nichts hergab — die Getriebe-Optionen der
erkannten Motorvariante.

Damit war die Getriebeart im KaufCheck geraten: aus "320d Automatik" wurde
zuverlaessig Automatik, aus "BMW 320d, top Zustand" gar nichts, und aus einem
Inserat, das die Ausstattungsliste "Sportsitze, Schaltwippen" enthielt,
potenziell das Falsche. Genau deshalb ist `getriebe` jetzt ein strukturiertes
Eingabefeld — und die Nutzerangabe hat hier Vorrang (dieselbe Rangfolge wie bei
`kraftstoff` und `leistung_ps`: Userinput schlaegt Text, Text schlaegt
ungepruefte DB).

WAS DIE GETRIEBEART BEWIRKT (und was nicht):

  Sie wirkt fachlich auf die PROBEFAHRT-Prueftexte (app/kaufaktionen.py,
  `_BASIS_GETRIEBE`): Kupplungsgriffpunkt und Rueckwaertsgang-Kratzen sind
  Pruefungen fuer ein Schaltgetriebe, Schaltrucken und durchrutschende
  Fahrstufen solche fuer eine Automatik. Vorher mischte der fahrzeugneutrale
  Katalogtext beides.

  Sie wirkt AUSDRUECKLICH NICHT auf die Motorvarianten-Auflösung. Gemessen ueber
  alle 3.230 Zeilen der Tabelle `motorvariante` (Stand dieses Passes): 32
  Gruppen haben innerhalb einer Baureihe dieselbe Bezeichnung, und bei genau 0
  davon liessen sich die Varianten anhand der Getriebe-Optionen trennen. 1.191
  Varianten bieten ohnehin beide Getriebearten an. Eine Variantenschaerfung
  ueber das Getriebe waere damit reine Behauptung — sie findet nicht statt.

  Sie erzeugt KEINE Aussagen über typische Getriebedefekte oder
  Wartungsintervalle. Solche Aussagen entstehen im gesamten KaufCheck
  ausschliesslich aus geprueften Fahrzeugdaten (Schwachstellen, kritische
  Wartung, Rueckrufe) — nie aus der Getriebeart allein.
"""

AUTOMATIK = "automatik"
MANUELL = "manuell"
ARTEN = (AUTOMATIK, MANUELL)

# Wortlisten fuer die Texterkennung. Bewusst breit bei der Automatik (jeder
# Hersteller nennt sie anders), bewusst schmal beim Schaltgetriebe.
AUTOMATIK_WORTE = ("automatik", "automatic", "steptronic", "tiptronic", "dsg", "s tronic",
                   "s-tronic", "dkg", "doppelkupplung", "pdk", "cvt", "wandler",
                   "multitronic", "powershift", "edc", "g-tronic")
MANUELL_WORTE = ("schaltgetriebe", "handschalt", "manuell", "handschalter", "schaltung")

# Anzeigeform fuer Prompt, Bericht und Checkliste. "Schaltgetriebe" statt
# "Manuell", weil das die Sprache der Inserate ist.
_ANZEIGE = {AUTOMATIK: "Automatik", MANUELL: "Schaltgetriebe"}


def normalisiere(wert: object) -> str | None:
    """Tolerante Eingangsnormalisierung auf "automatik" | "manuell" | None.

    Unbekannte Werte werden zu None, NICHT zu einem Validierungsfehler: alte
    Frontends und gespeicherte Formulare duerfen keinen 422 erzeugen (dieselbe
    Konvention wie bei den optionalen VerkaufsCheck-Auswahlfeldern).
    """
    if wert is None:
        return None
    text = str(wert).strip().lower()
    if not text:
        return None
    if text in ARTEN:
        return text
    if any(w in text for w in AUTOMATIK_WORTE):
        return AUTOMATIK
    if any(w in text for w in MANUELL_WORTE):
        return MANUELL
    return None


def aus_text(*texte: str | None) -> str | None:
    """Getriebeart aus Freitext — nur bei EINDEUTIGEM Signal.

    Nennt derselbe Text beide Arten ("Automatik" und "Handschalter"), ist das
    kein Erkenntnisgewinn, sondern ein Widerspruch: dann None. Das Aufloesen
    passiert nicht hier, sondern sichtbar im Key Finding (app/key_findings.py).
    """
    text = " ".join(str(t or "") for t in texte).lower()
    auto = any(w in text for w in AUTOMATIK_WORTE)
    manu = any(w in text for w in MANUELL_WORTE)
    if auto != manu:
        return AUTOMATIK if auto else MANUELL
    return None


def aus_db(motor_match: dict | None) -> str | None:
    """Getriebeart aus den Optionen der erkannten Motorvariante.

    Nur wenn ALLE angebotenen Optionen dieselbe Art sind. Eine Variante, die
    Schalter UND Automatik anbietet, sagt ueber das konkrete Fahrzeug nichts.
    """
    optionen = (motor_match or {}).get("getriebe")
    if isinstance(optionen, str):
        optionen = [optionen]
    optionen = [str(o).lower() for o in (optionen or [])]
    if not optionen:
        return None
    if all(any(w in o for w in AUTOMATIK_WORTE) for o in optionen):
        return AUTOMATIK
    if all(any(w in o for w in MANUELL_WORTE) for o in optionen):
        return MANUELL
    return None


def aus_request(req, motor_match: dict | None = None) -> str | None:
    """Kanonische Getriebeart dieses Checks.

    Rangfolge: strukturierte Nutzerangabe > eindeutiger Freitext > eindeutige
    DB-Optionen. Ohne alles drei: None (dann bleiben die neutralen
    Katalogtexte stehen — es wird nichts geraten).
    """
    aus_feld = normalisiere(getattr(req, "getriebe", None))
    if aus_feld:
        return aus_feld
    aus_freitext = aus_text(getattr(req, "motor", None), getattr(req, "beschreibung", None),
                            getattr(req, "freitext", None))
    if aus_freitext:
        return aus_freitext
    return aus_db(motor_match)


def anzeige(art: str | None) -> str | None:
    return _ANZEIGE.get(art or "")


def prompt_zeile(art: str | None) -> str | None:
    """Prompt-Zeile fuer den Inseratsblock — oder None, wenn nichts bekannt ist."""
    label = anzeige(art)
    return f"Getriebe:       {label}" if label else None
