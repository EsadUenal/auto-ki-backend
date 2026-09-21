"""HU-/TÜV-Termin: deterministisch parsen, normalisieren und bewerten.

WARUM DIESES MODUL
------------------
Das Formularfeld "TÜV bis" ist Freitext. Im RC1-Test kam dort "092028" an —
Monat und Jahr ohne Trenner. Der Wert ging roh ins LLM-Prompt, und das Modell
urteilte mit seinem eigenen, veralteten Zeitgefühl: "Angabe 2028 unmöglich,
maximal 2 Jahre ab Prüfung, vermutlich Tippfehler" — im September 2026, bei
"HU neu" im Inserat. Das drückte sogar die Kaufempfehlung.

Die Plausibilität eines HU-Termins ist aber keine Ermessensfrage, sondern
Kalenderrechnung. Sie gehört deshalb in Code, nicht ins Modell:

  * Das Datum wird aus allen üblichen Schreibweisen gelesen und als "MM/JJJJ"
    normalisiert — "092028" erscheint nirgends mehr als Anzeige.
  * Bewertet wird gegen das HEUTIGE Datum (injizierbar für Tests), nie gegen
    eine im Code stehende Jahreszahl und nie gegen das Baujahr.
  * Das Ergebnis geht als fester Fakt ins Prompt; das Modell soll es nicht neu
    beurteilen.

REGELN (§29 StVZO, Pkw)
-----------------------
  * Die HU gilt bis zum Ende des Plakettenmonats.
  * Regelintervall 24 Monate; bei Neufahrzeugen 36 Monate bis zur ersten HU.
    Ein Termin bis zu 24 Monate in der Zukunft ist also bei jedem Pkw
    plausibel ("HU neu"), bis zu 36 Monate nur bei einem Fahrzeug, das zum
    Termin höchstens drei Jahre alt ist.
  * Ein vergangener Monat heißt: HU abgelaufen.
  * Alles darüber hinaus ist nicht regelkonform und wird als auffällig
    markiert — ohne dem Nutzer einen Tippfehler zu unterstellen.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

# Status-Werte (stabil — Frontend/Tests lesen sie).
PLAUSIBEL = "plausibel"
ABGELAUFEN = "abgelaufen"
UNGEWOEHNLICH_WEIT = "ungewoehnlich_weit"
UNLESBAR = "unlesbar"

_REGEL_MONATE = 24
_ERST_HU_MONATE = 36


@dataclass(frozen=True)
class HuBewertung:
    roh: str
    monat: int | None
    jahr: int | None
    status: str
    monate_bis_faellig: int | None
    anzeige: str            # "09/2028" oder der Rohwert, wenn unlesbar
    hinweis: str            # kurzer, nutzertauglicher Satz

    @property
    def lesbar(self) -> bool:
        return self.monat is not None and self.jahr is not None


def _jahr4(j: int) -> int:
    return 2000 + j if j < 100 else j


def parse_hu(roh: str | None) -> tuple[int, int] | None:
    """Liest Monat/Jahr aus den üblichen Schreibweisen. None, wenn unlesbar.

    Akzeptiert u.a.: "09/2028", "9/28", "09.2028", "09-2028", "2028-09",
    "092028", "0928", "09 2028", "Sep 2028" wird bewusst NICHT geraten.
    """
    s = (roh or "").strip()
    if not s:
        return None
    # JJJJ-MM (ISO)
    m = re.fullmatch(r"(20\d{2})\s*[-/.]\s*(0?[1-9]|1[0-2])", s)
    if m:
        return int(m.group(2)), int(m.group(1))
    # MM/JJJJ, MM.JJ, MM-JJJJ, "MM JJJJ"
    m = re.fullmatch(r"(0?[1-9]|1[0-2])\s*[/.\-\s]\s*((?:20)?\d{2})", s)
    if m:
        return int(m.group(1)), _jahr4(int(m.group(2)))
    # Nur Ziffern ohne Trenner: MMJJJJ ("092028") oder MMJJ ("0928")
    m = re.fullmatch(r"(0[1-9]|1[0-2])(20\d{2}|\d{2})", s)
    if m:
        return int(m.group(1)), _jahr4(int(m.group(2)))
    return None


def normalisiere_hu(roh: str | None) -> str | None:
    """"092028" -> "09/2028". Unlesbare Werte bleiben unverändert (nichts raten)."""
    if roh is None:
        return None
    teile = parse_hu(roh)
    if teile is None:
        return roh.strip() or None
    monat, jahr = teile
    return f"{monat:02d}/{jahr}"


def _monatsdifferenz(heute: dt.date, monat: int, jahr: int) -> int:
    return (jahr - heute.year) * 12 + (monat - heute.month)


def bewerte_hu(roh: str | None, heute: dt.date | None = None,
               baujahr: int | None = None) -> HuBewertung | None:
    """Bewertet einen HU-Termin gegen das heutige Datum. None, wenn keine Angabe."""
    if not (roh or "").strip():
        return None
    heute = heute or dt.date.today()
    teile = parse_hu(roh)
    if teile is None:
        return HuBewertung(roh=roh, monat=None, jahr=None, status=UNLESBAR,
                           monate_bis_faellig=None, anzeige=roh.strip(),
                           hinweis="HU-Termin nicht eindeutig lesbar: im Fahrzeugschein "
                                   "(Zulassungsbescheinigung Teil I) nachsehen.")
    monat, jahr = teile
    anzeige = f"{monat:02d}/{jahr}"
    diff = _monatsdifferenz(heute, monat, jahr)

    if diff < 0:
        status, hinweis = ABGELAUFEN, (
            f"HU laut Angabe seit {anzeige} abgelaufen. Vor dem Kauf neue HU verlangen "
            f"oder die Kosten dafür einplanen.")
    else:
        # Bis zu 36 Monate nur, wenn das Fahrzeug zum Termin höchstens 3 Jahre alt
        # ist (erste HU). Ohne Baujahr gilt großzügig die Neuwagenregel, damit ein
        # fehlendes Feld nie zu einer falschen Warnung führt.
        max_monate = _REGEL_MONATE
        if baujahr is None or (jahr - baujahr) <= 3:
            max_monate = _ERST_HU_MONATE
        if diff <= max_monate:
            status, hinweis = PLAUSIBEL, (
                f"HU gültig bis {anzeige} (noch {diff} Monate). Das passt zu einer frisch "
                f"durchgeführten Hauptuntersuchung. Prüfbericht trotzdem ansehen.")
        else:
            status, hinweis = UNGEWOEHNLICH_WEIT, (
                f"HU bis {anzeige} liegt {diff} Monate in der Zukunft, also mehr als das "
                f"reguläre Prüfintervall. Termin im Fahrzeugschein und Prüfbericht "
                f"abgleichen.")
    return HuBewertung(roh=roh, monat=monat, jahr=jahr, status=status,
                       monate_bis_faellig=diff, anzeige=anzeige, hinweis=hinweis)


def prompt_zeile(b: HuBewertung | None, heute: dt.date | None = None) -> str:
    """Fester Fakt fürs LLM-Prompt: Datum, Bewertung und das Verbot, neu zu urteilen."""
    if b is None:
        return ""
    heute = heute or dt.date.today()
    return (
        f"HU-/TÜV-PRÜFUNG (deterministisch, Stand {heute.month:02d}/{heute.year}): "
        f"Angabe {b.anzeige}. Bewertung: {b.status}. {b.hinweis}\n"
        "Diese Bewertung ist verbindlich: Beurteile den HU-Termin NICHT neu, "
        "unterstelle keinen Tippfehler und rechne nicht mit einem anderen aktuellen Datum."
    )


# ── Sicherheitsnetz für den LLM-Bericht ──────────────────────────────────────
#
# Das Prompt verbietet eine Neubewertung. Ein Modell kann sich trotzdem darüber
# hinwegsetzen — genau das ist im RC1-Test passiert ("Angabe 2028 unmöglich").
# Ist der Termin deterministisch PLAUSIBEL, darf der Bericht ihn nicht als
# unplausibel, unmöglich oder Tippfehler hinstellen. Entfernt wird nur, was
# eindeutig dieser falschen Aussage zuzuordnen ist; der Rest bleibt unangetastet.

_HU_WORT = r"(?:T[ÜU]V|HU\b|Hauptuntersuchung|Prüfbericht|Pruefbericht|Plakette)"
_FALSCH_WORT = (r"(?:unplausib\w*|unmöglich\w*|unmoeglich\w*|Tippfehler|nicht möglich|"
                r"kann nicht stimmen|zu weit in der Zukunft)")

_TABELLEN_ZEILE = re.compile(
    rf"^\|\s*(?:{_HU_WORT}[^|]*)\|[^\n]*$", re.IGNORECASE | re.MULTILINE)
_KLAMMER_FALSCH = re.compile(rf"\s*\([^()]*{_FALSCH_WORT}[^()]*\)", re.IGNORECASE)
_SATZ = re.compile(r"[^.!?\n]*[.!?]?")

_STATUS_ZELLE = {
    PLAUSIBEL: "✓ Plausibel",
    ABGELAUFEN: "⚠ Abgelaufen",
    UNGEWOEHNLICH_WEIT: "⚠ Ungewöhnlich weit: im Fahrzeugschein prüfen",
    UNLESBAR: "⚠ Nicht eindeutig lesbar",
}


def bereinige_bericht(bericht: str, b: HuBewertung | None) -> str:
    """Bringt HU-Aussagen im Bericht in Einklang mit der deterministischen Bewertung."""
    if not bericht or b is None:
        return bericht

    # 1) Tabellenzeile "TÜV-Gültigkeit" deterministisch neu setzen (Angabe im
    #    Format MM/JJJJ, Erwartung als Regel, Plausibilität aus dem Code).
    def _zeile(m: re.Match) -> str:
        zellen = [z.strip() for z in m.group(0).strip().strip("|").split("|")]
        kriterium = zellen[0] if zellen else "TÜV-Gültigkeit"
        return (f"| {kriterium} | {b.anzeige} | HU alle 24 Monate "
                f"(Erst-HU nach 36 Monaten) | {_STATUS_ZELLE.get(b.status, b.status)} |")
    bericht = _TABELLEN_ZEILE.sub(_zeile, bericht)

    if b.status != PLAUSIBEL:
        return bericht

    # 2) Klammerzusätze wie "(Angabe 2028 unmöglich)" entfernen.
    bericht = _KLAMMER_FALSCH.sub("", bericht)

    # 3) Ganze Sätze entfernen, die den HU-Termin als falsch hinstellen.
    hu = re.compile(_HU_WORT, re.IGNORECASE)
    falsch = re.compile(_FALSCH_WORT, re.IGNORECASE)
    zeilen_neu = []
    for zeile in bericht.split("\n"):
        if zeile.lstrip().startswith("|") or not (hu.search(zeile) and falsch.search(zeile)):
            zeilen_neu.append(zeile)
            continue
        saetze = [s for s in _SATZ.findall(zeile) if s.strip()]
        behalten = [s for s in saetze if not (hu.search(s) and falsch.search(s))]
        rest = "".join(behalten).strip()
        # Nur Listenmarker/Checkbox übrig -> Zeile ganz entfernen.
        if re.fullmatch(r"[-*]?\s*(\[[ xX]\])?\s*(\*\*[^*]*\*\*:?)?\s*", rest or ""):
            continue
        zeilen_neu.append(rest if not zeile.startswith((" ", "-", "*")) else
                          re.match(r"^\s*", zeile).group(0) + rest)
    return "\n".join(zeilen_neu)
