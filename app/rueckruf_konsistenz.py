from __future__ import annotations

"""
Kein Sicherheitsrückruf darf aus "## Kritische Risiken" verschwinden.

BEFUND AUS DEM ECHTEN PRODUCTION-RUN (BMW 330i G20)
---------------------------------------------------
Oben im Bericht standen drei Rückrufe:

    Spurstange (Bruchgefahr) · Gurtschloss · Starterrelais (Brandgefahr)

In der ausführlichen Analyse unter "## Kritische Risiken" standen dann:

    Spurstange · Gurtschloss · Software/Infotainment

Der Brandgefahr-Rückruf war weg, dafür stand dort ein Softwarethema. Für den
Leser sieht das aus, als hätte ENFAL den dritten Rückruf zurückgenommen.

ROOT CAUSE
----------
Der Abschnitt "## Kritische Risiken" wird vom LLM geschrieben. Der Systemprompt
gibt ihm zwei Anweisungen, die miteinander kollidieren:

    "Priorisiert absteigend: zuerst sicherheitsrelevante/teure Schwachstellen
     (hoher Schweregrad, KBA-Rückrufe) ..."
    "Maximal 3-5 wichtigste Punkte, keine erschöpfende Liste."

Mit drei Rückrufen plus Schwachstellen ist die Obergrenze sofort erreicht, und
welche Punkte durchfallen, entscheidet allein das Modell. Es hat eine
Software-Schwachstelle über einen amtlichen Brandgefahr-Rückruf gestellt.

Das ist KEIN Fehler der Rückrufdaten: die drei Rückrufe waren korrekt erkannt,
korrekt gefiltert und oben korrekt ausgewiesen. Es ist ein Konsistenzfehler
zwischen zwei Abschnitten desselben Berichts.

DER FIX — ZWEI SCHICHTEN
------------------------
1. Der Prompt sagt jetzt ausdrücklich, dass die Obergrenze ERST NACH allen
   Rückrufen gilt (app/kaufcheck.py).
2. Dieses Modul ist das deterministische Netz DANACH. Es prüft, ob jeder für
   dieses Fahrzeug zugelassene Rückruf im Abschnitt vorkommt, und ergänzt
   fehlende.

WAS DIESES MODUL NICHT TUT
--------------------------
Es erfindet nichts. Der ergänzte Text entsteht ausschließlich aus dem bereits
geprüften und gefilterten Rückruf-Insight desselben Laufs: Kurztitel und
KBA-Referenz, beides schon im Bericht weiter oben. Es entfernt nichts, es
sortiert nichts um und es fasst keinen vom Modell geschriebenen Satz an. Es
bläst auch nicht alle Findings auf: ergänzt werden ausschließlich RÜCKRUFE,
keine Schwachstellen und keine Wartungspunkte.

Fehlt der Abschnitt ganz (unvollständiger Bericht, anderes Format), wird nichts
verändert: ein Abschnitt wird nicht neu erfunden.
"""

import logging
import re

log = logging.getLogger(__name__)

# Die Überschrift, wie sie der Systemprompt vorgibt. Tolerant gegenüber der
# Zahl der Rauten und nachlaufenden Leerzeichen, damit eine kleine Abweichung
# des Modells das Netz nicht abschaltet.
_RE_ABSCHNITT = re.compile(r"^#{2,4}\s*Kritische Risiken\s*$", re.IGNORECASE | re.MULTILINE)
_RE_NAECHSTE_UEBERSCHRIFT = re.compile(r"^#{1,4}\s+\S", re.MULTILINE)

# Wortzeichen für den Abgleich: Groß-/Kleinschreibung und Interpunktion egal.
_RE_WORT = re.compile(r"[a-zäöüß]+")


def _woerter(text: str) -> set[str]:
    return set(_RE_WORT.findall((text or "").lower()))


def _abschnitt_grenzen(bericht: str) -> tuple[int, int] | None:
    """(start, ende) des Inhalts unter der Überschrift, oder None."""
    kopf = _RE_ABSCHNITT.search(bericht)
    if not kopf:
        return None
    start = kopf.end()
    weiter = _RE_NAECHSTE_UEBERSCHRIFT.search(bericht, start)
    return start, (weiter.start() if weiter else len(bericht))


def _bereits_genannt(abschnitt: str, kurztitel: str, kba: str | None) -> bool:
    """Steht dieser Rückruf schon im Abschnitt?

    Zwei unabhängige Wege, weil das Modell frei formuliert:
      * die KBA-Referenz taucht wörtlich auf, oder
      * das BAUTEIL aus dem Kurztitel taucht auf.

    Der Kurztitel hat die Form "<Bauteil>: <Folge>" (app/rueckruf_titel.py).
    Verglichen wird der Bauteilteil, weil das Modell die Folge anders
    formulieren darf ("Brandgefahr" vs. "Gefahr eines Brandes"), das Bauteil
    aber benennen muss, damit der Punkt überhaupt erkennbar ist.
    """
    if kba and kba.lower() in abschnitt.lower():
        return True
    bauteil = (kurztitel or "").split(":", 1)[0]
    kern = {w for w in _woerter(bauteil) if len(w) >= 5}
    if not kern:
        # Sehr kurzer oder rein numerischer Titel: dann trägt nur die Referenz.
        return False
    vorhanden = _woerter(abschnitt)
    # Teilwortabgleich, damit "Starterrelais" auch "Starterrelais-Rückruf" trifft.
    return all(any(k in w or w in k for w in vorhanden) for k in kern)


def ergaenze_fehlende_rueckrufe(bericht: str, rueckruf_insights: list) -> tuple[str, list[str]]:
    """Fehlende Rückrufe unter "## Kritische Risiken" ergänzen.

    `rueckruf_insights` sind die bereits gefilterten Rückruf-Insights DIESES
    Laufs (kategorie == "rueckruf"). Nur sie können ergänzt werden.

    Rückgabe (bericht, ergaenzte_kurztitel).
    """
    # Eigene Vertragspruefung statt Vertrauen auf den Aufrufer: ergaenzt werden
    # ausschliesslich Rueckrufe. Eine Schwachstelle oder ein Wartungspunkt, der
    # versehentlich mit uebergeben wird, darf hier nicht als Rueckruf im Bericht
    # landen.
    rueckruf_insights = [i for i in (rueckruf_insights or [])
                         if getattr(i, "kategorie", None) == "rueckruf"]
    if not bericht or not rueckruf_insights:
        return bericht, []
    grenzen = _abschnitt_grenzen(bericht)
    if grenzen is None:
        log.info("Rueckruf-Konsistenz: Abschnitt 'Kritische Risiken' nicht gefunden, "
                 "Bericht bleibt unveraendert")
        return bericht, []

    start, ende = grenzen
    abschnitt = bericht[start:ende]

    zeilen: list[str] = []
    ergaenzt: list[str] = []
    for i in rueckruf_insights:
        kurztitel = _kurztitel(i)
        kba = _kba(i)
        if _bereits_genannt(abschnitt, kurztitel, kba):
            continue
        ref = f" (KBA-Referenz {kba})" if kba else ""
        zeilen.append(f"- **Rückruf: {kurztitel}**{ref}. "
                      f"Ob genau dieses Fahrzeug betroffen ist und ob die Aktion bereits "
                      f"ausgeführt wurde, lässt sich nur anhand der FIN klären.")
        ergaenzt.append(kurztitel)

    if not zeilen:
        return bericht, []

    log.info("Rueckruf-Konsistenz: %d Rueckruf(e) fehlten in 'Kritische Risiken' und "
             "wurden ergaenzt: %s", len(ergaenzt), ergaenzt)
    # Am ENDE des Abschnitts anhängen: der vom Modell geschriebene Text bleibt
    # unangetastet und in seiner Reihenfolge.
    neu = abschnitt.rstrip("\n") + "\n" + "\n".join(zeilen) + "\n"
    return bericht[:start] + neu + bericht[ende:], ergaenzt


def _kurztitel(insight) -> str:
    """Kurzer Bauteil-Titel des Rückrufs, wie ihn der Bericht oben verwendet."""
    roh = (getattr(insight, "kurztitel", None) or getattr(insight, "titel", None) or "").strip()
    # Der Insight-Titel trägt teils ein Präfix ("KBA-Rückruf (Baureihe): ...").
    for trenner in ("): ", ": "):
        if trenner in roh and roh.lower().startswith(("kba", "rückruf", "rueckruf")):
            roh = roh.split(trenner, 1)[1].strip()
            break
    return roh


def _kba(insight) -> str | None:
    ref = getattr(insight, "ref", None)
    if ref:
        return str(ref).strip() or None
    for q in getattr(insight, "quellen", None) or []:
        r = getattr(q, "ref", None)
        if r:
            return str(r).strip() or None
    return None
