from __future__ import annotations

"""
Verkäuferart: "privat" | "haendler".

WAS SIE BEEINFLUSST — und was ausdruecklich nicht:

  Sie beeinflusst NICHT: technische Fahrzeugdaten, Motorauflösung, Rueckruf-
  zuordnung, Schwachstellen, Marktmodell, Preisurteil. Kein Modul dieses Passes
  gibt die Verkäuferart an eine dieser Stellen weiter. Wer ein Auto verkauft,
  aendert nichts daran, was es ist.

  Sie beeinflusst: welche Unterlagen man sich sinnvollerweise zeigen laesst und
  welche Fragen vor der Besichtigung tragen. Ein Autohaus fuehrt Rechnung,
  Firmenanschrift und meist eine eigene Aufbereitungsdokumentation; ein
  Privatverkäufer fuehrt Fahrzeugpapiere und Ausweis. Das sind reine
  Unterlagenfragen.

KEINE RECHTSAUSSAGEN. Bewusst NICHT enthalten: Gewährleistung, Sachmängelhaftung,
Garantie, Ruecktritt, Haftungsausschluss, Widerrufsrecht, Unterschiede zwischen
Verbrauchsgueterkauf und Privatkauf. ENFAL hat dafuer keine gepruefte fachliche
Grundlage im System, und der Auftrag verbietet unbelegte Rechtsberatung
ausdruecklich.

KEINE PAUSCHALWERTUNG. Es gibt hier kein "Händler = sicher" und kein
"Privat = riskant". Beide Zweige formulieren dieselbe Haltung: Angaben mit
Unterlagen abgleichen.
"""

PRIVAT = "privat"
HAENDLER = "haendler"
ARTEN = (PRIVAT, HAENDLER)

_SYNONYME = {
    PRIVAT: ("privat", "privatverkauf", "privatverkaeufer", "privatverkäufer", "private"),
    HAENDLER: ("haendler", "händler", "handler", "dealer", "gewerblich", "autohaus",
               "gewerbe", "kfz-betrieb"),
}

_ANZEIGE = {PRIVAT: "Privatverkäufer", HAENDLER: "Händler"}


def normalisiere(wert: object) -> str | None:
    """Tolerante Normalisierung auf "privat" | "haendler" | None.

    Unbekanntes wird None statt 422 — alte Clients und gespeicherte Formulare
    duerfen den Check nicht an der Eingabegrenze zerlegen.
    """
    if wert is None:
        return None
    text = str(wert).strip().lower()
    if not text:
        return None
    for art, woerter in _SYNONYME.items():
        if text in woerter:
            return art
    return None


def aus_request(req) -> str | None:
    return normalisiere(getattr(req, "verkaeuferart", None))


def anzeige(art: str | None) -> str | None:
    return _ANZEIGE.get(art or "")


def prompt_zeile(art: str | None) -> str | None:
    """Prompt-Zeile fuer den Inseratsblock.

    Formuliert als Angabe des Inserats, nicht als geprueftes Merkmal: ENFAL hat
    weder Handelsregister noch Halterdaten gesehen.
    """
    label = anzeige(art)
    return f"Verkäufer:      {label} (laut Inserat)" if label else None
