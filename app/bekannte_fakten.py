from __future__ import annotations

"""
Bekannte Fakten aus dem Inserat: EINE Quelle für "was ENFAL schon weiß".

BEFUND (Production-Run BMW M4 F82)
----------------------------------
Das Inserat nannte "Letzte Wartung laut Verkäufer bei ca. 64.000 km". Der
Prüfplan fragte trotzdem "Wann war die letzte Wartung, und was wurde dabei
gemacht?". Derselbe Fehler galt nach dem 330i-Closing als behoben.

Ursache war nicht die Extraktion, sondern die Obergrenze des Prüfplans: die
geschärfte Frage entstand als fahrzeugspezifische Aktion mit Rang 340 und fiel
bei jedem Fahrzeug mit vielen Schwachstellen aus `MAX_SPEZIFISCH_PRO_BEREICH`
heraus. Sobald sie herausfiel, kam die allgemeine Katalogfrage zurück, denn
deren Ausblendung (`deckt`) hing daran, dass die spezifische Aktion ÜBERLEBT.
Dieselbe Fehlerklasse hatte vorher schon den Vorbesitzer getroffen.

DIE REGEL
---------
Ein bekannter Fakt darf nirgends wieder als unbekannt erscheinen, egal welche
Obergrenze, welcher Rückfall oder welcher Textgenerator gerade greift. Deshalb
schärft ein bekannter Fakt die KATALOGTEXTE selbst: der Basis-Katalog kennt
keine Obergrenze, die geschärfte Fassung ist immer da. Die fahrzeugspezifischen
Aktionen bleiben zusätzlich bestehen; überlebt eine, blendet `deckt` den
Katalogpunkt wie bisher aus.

Für den Freitext des Modells gibt es ein enges Netz (`bereinige_bericht`): es
ersetzt nur eine FRAGE, die einen bekannten Fakt als unbekannt behandelt.

Alle Texte bleiben ANGABEN des Inserats: "laut Inserat", kein Nachweis.
"""

import re
from dataclasses import dataclass

from app.servicehistorie import (
    NICHT_VORHANDEN as SH_NICHT_VORHANDEN, TEILWEISE as SH_TEILWEISE,
    VOLLSTAENDIG_ANGEGEBEN as SH_VOLLSTAENDIG, status as servicehistorie_status,
)
from app.wartungsangabe import (
    Wartungsangabe, aus_request as wartungsangabe_aus_request, widerspruch_km,
)

UNFALLFREI = "unfallfrei"
UNFALL = "unfall"
UNKNOWN = "unknown"

# Tri-State (KaufCheck-Final-Stabilization): die frühere, pro Feld eigene
# String-Heuristik ist ersetzt durch das EINE zentrale Modell in
# app/tristate.py. Diese Funktionen bleiben als stabile Fassade (Rückgabewerte
# unverändert), damit alle bisherigen Konsumenten dieselbe Semantik bekommen:
# eine ausdrückliche Nichtangabe ("nicht angegeben", "keine eindeutige
# Angabe", "Unfallfrei: nicht angegeben", "Ob … unfallfrei ist, ist unklar")
# kann nie mehr zu einer Behauptung werden.
from app import tristate as _ts
from app.tristate import ist_unbekannt_angabe  # noqa: F401  (öffentliche API)


def unfall_detail(req) -> "_ts.TriState":
    return _ts.bewerte(_ts.UNFALL, getattr(req, "unfallfrei", None), _ts.request_text(req))


def unfall_status(req) -> str:
    z = unfall_detail(req)
    if z.state == _ts.CLAIMED_ABSENT:
        return UNFALLFREI
    if z.state == _ts.CLAIMED_PRESENT:
        return UNFALL
    return UNKNOWN


def tuning_status(req) -> str:
    z = _ts.bewerte(_ts.TUNING, getattr(req, "tuning", None), _ts.request_text(req))
    return {"claimed_absent": "absent", "claimed_present": "present"}.get(z.state, UNKNOWN)



@dataclass(frozen=True)
class BekannteFakten:
    letzte_wartung: Wartungsangabe | None = None
    kilometerstand: int | None = None
    hu_bis: str | None = None
    unfall: str = UNKNOWN
    vorbesitzer: int | None = None
    servicehistorie: str | None = None
    # Tri-State-Details aller Inseratsthemen (app/tristate.py). Nur Angaben,
    # nie ein geprüfter Befund ("verified" existiert in diesem Modell nicht).
    unfall_eingeschraenkt: bool = False
    angaben: dict | None = None

    @property
    def wartung_widerspruch(self) -> bool:
        return widerspruch_km(self.letzte_wartung, self.kilometerstand)


def aus_request(req) -> BekannteFakten:
    km = getattr(req, "kilometerstand", None)
    unfall = unfall_status(req)
    angaben = _ts.fakten_aus_request(req)
    return BekannteFakten(
        letzte_wartung=wartungsangabe_aus_request(req),
        kilometerstand=km if isinstance(km, int) else None,
        hu_bis=(getattr(req, "tuev_bis", None) or "").strip() or None,
        unfall=unfall,
        vorbesitzer=getattr(req, "vorbesitzer", None),
        servicehistorie=servicehistorie_status(req),
        unfall_eingeschraenkt=angaben["unfall"].eingeschraenkt,
        angaben={k: v.as_dict() for k, v in angaben.items()},
    )


# ── Geschärfte Katalogtexte ──────────────────────────────────────────────────

def wartung_frage(f: BekannteFakten) -> tuple[str, str] | None:
    """(Frage, Erläuterung) zur bekannten Wartungsangabe, sonst None."""
    w = f.letzte_wartung
    if w is None:
        return None
    if f.wartung_widerspruch:
        return ("Wie passt die genannte Wartung zum angegebenen Kilometerstand?",
                f"Das Inserat nennt eine letzte Wartung {w.anzeige()}, der angegebene "
                f"Kilometerstand liegt mit {f.kilometerstand:,} km darunter. ".replace(",", ".")
                + "Eine der beiden Angaben stimmt nicht: vor der Besichtigung klären und am "
                  "Beleg nachvollziehen.")
    return (f"Was umfasste die im Inserat genannte Wartung {w.anzeige()}?",
            f"Das Inserat nennt eine letzte Wartung {w.anzeige()}. Offen bleibt der Umfang: "
            f"nach den ausgeführten Arbeiten, der Werkstatt und dem Beleg fragen und beides "
            f"bei der Besichtigung zeigen lassen.")


def basistexte(f: BekannteFakten) -> dict[tuple[str, str], tuple[str | None, str]]:
    """Katalogpunkte, die ein bekannter Fakt schärft: {(bereich, schluessel):
    (neuer Titel oder None, neuer Text)}.

    Ersetzt wird nur, was den Fakt sonst als unbekannt behandeln würde oder ihn
    konkret prüfbar macht. Es entsteht kein zusätzlicher Punkt.
    """
    texte: dict[tuple[str, str], tuple[str | None, str]] = {}

    if f.vorbesitzer is not None:
        texte[("dokumente", "zb2")] = (None, (
            f"Teil II nennt den letzten Halter und die Zahl der Vorhalter. Das Inserat "
            f"gibt {f.vorbesitzer} Vorbesitzer an: Beides muss zusammenpassen, sonst nach "
            f"dem Grund fragen. Ein Eigentumsnachweis ist das Dokument nicht. Ohne Teil II "
            f"sollte kein Kauf stattfinden."))

    frage = wartung_frage(f)
    if frage is not None:
        texte[("verkaeuferfragen", "wartung")] = frage
        if not f.wartung_widerspruch:
            texte[("dokumente", "wartungsrechnungen")] = (None, (
                f"Rechnungen belegen den tatsächlichen Umfang der Arbeiten deutlich besser "
                f"als ein Stempel im Heft. Das gilt auch für die im Inserat genannte Wartung "
                f"{f.letzte_wartung.anzeige()}: die Angabe allein belegt nicht, dass die "
                f"Arbeiten ausgeführt wurden."))

    if f.hu_bis:
        texte[("verkaeuferfragen", "hu")] = (
            "Was stand im Prüfbericht der letzten Hauptuntersuchung?",
            f"Laut Inserat läuft die HU bis {f.hu_bis}. Den Bericht zeigen lassen: dort "
            f"stehen auch Mängel, die ohne Beanstandung vermerkt wurden.")

    if f.unfall == UNFALLFREI and f.unfall_eingeschraenkt:
        texte[("verkaeuferfragen", "unfall")] = (
            "Welche Schäden, Nachlackierungen oder Reparaturen sind Ihnen bekannt?",
            "Das Inserat nennt keine bekannten Unfallschäden, schränkt das aber selbst ein "
            "(„bekannt“). Schäden, Nachlackierungen und Reparaturen ausdrücklich erfragen "
            "und die Antwort im Kaufvertrag festhalten.")
    elif f.unfall == UNFALLFREI:
        texte[("verkaeuferfragen", "unfall")] = (
            "Gab es trotz der Angabe „unfallfrei“ Schäden, Nachlackierungen oder ersetzte "
            "Teile?",
            "Das Inserat gibt das Fahrzeug als unfallfrei an. Auch kleine, fachgerecht "
            "reparierte Schäden erfragen und die Antwort im Kaufvertrag festhalten.")
    elif f.unfall == UNFALL:
        texte[("verkaeuferfragen", "unfall")] = (
            "Welcher Schaden lag vor, und wie und wo wurde er repariert?",
            "Das Inserat weist das Fahrzeug als nicht unfallfrei aus. Schadensumfang, "
            "Werkstatt und Reparaturweg erfragen und die Unterlagen dazu zeigen lassen.")
    else:
        texte[("verkaeuferfragen", "unfall")] = (
            "Welche Schäden, Nachlackierungen oder Reparaturen gab es?",
            "Die Unfallhistorie ist nicht vollständig bekannt. Vor dem Kauf Schäden, "
            "Nachlackierungen und Reparaturhistorie klären und vorhandene Unterlagen zeigen lassen.")
        texte[("dokumente", "kaufvertrag")] = (
            None, "Die unklare Unfallhistorie und alle zugesicherten Angaben im Kaufvertrag "
                  "festhalten. Vorhandene Schadensgutachten und Reparaturrechnungen prüfen.")

    if f.servicehistorie == SH_NICHT_VORHANDEN:
        texte[("dokumente", "serviceheft")] = (
            "Nach einzelnen Serviceunterlagen fragen",
            "Laut Inserat liegt keine Servicehistorie vor. Falls doch einzelne Rechnungen "
            "oder ein digitales Serviceprotokoll existieren, diese auf Datum, Kilometerstand "
            "und Werkstatt durchsehen.")
    elif f.servicehistorie == SH_TEILWEISE:
        texte[("dokumente", "serviceheft")] = (None, (
            "Die Servicehistorie ist laut Inserat nur teilweise vorhanden. Vorhandene "
            "Einträge auf Datum, Kilometerstand und Werkstattstempel durchsehen und "
            "festhalten, welche Zeiträume fehlen."))
    elif f.servicehistorie == SH_VOLLSTAENDIG:
        texte[("dokumente", "serviceheft")] = (None, (
            "Laut Inserat wird eine vollständige Servicehistorie angegeben. Auf durchgehende "
            "Einträge mit Datum, Kilometerstand und Werkstattstempel achten: die Angabe "
            "allein ist kein Nachweis."))
    return texte


# ── Fragen, die einen bekannten Fakt als unbekannt behandeln ─────────────────
#
# Genutzt vom Netz für den Berichtstext und von den Tests als Invariante. Die
# Muster fangen die FRAGE nach dem Fakt, nicht jede Erwähnung: "Beleg zur
# letzten Wartung zeigen lassen" ist keine Frage nach dem Zeitpunkt.
_FRAGE_LETZTE_WARTUNG = re.compile(
    r"wann\s+(?:war|erfolgte|fand|wurde)\s+(?:die\s+|der\s+|das\s+)?"
    r"(?:letzte[nr]?\s+|zuletzt\s+)(?:wartung|inspektion|service|kundendienst|ölwechsel"
    r"|oelwechsel)[^?\n]*\?"
    r"|wann\s+wurde\s+(?:das\s+fahrzeug|der\s+wagen|es|er)\s+(?:zuletzt\s+)?"
    r"(?:gewartet|inspiziert)[^?\n]*\?",
    re.IGNORECASE)
_FRAGE_HU = re.compile(
    r"(?:wann\s+(?:war|erfolgte|ist|läuft|laeuft)\s+(?:die\s+)?(?:letzte\s+|nächste\s+)?"
    r"(?:hu|hauptuntersuchung|tüv|tuev)\b|bis\s+wann\s+läuft\s+(?:die\s+)?(?:hu|tüv))"
    r"[^?\n]*\?",
    re.IGNORECASE)
_FRAGE_VORBESITZER = re.compile(
    r"wie\s+viele\s+(?:vorbesitzer|vorhalter|halter)[^?\n]*\?", re.IGNORECASE)


def unbekannt_fragen(text: str, f: BekannteFakten) -> list[str]:
    """Stellen in `text`, die einen BEKANNTEN Fakt als unbekannt erfragen."""
    treffer: list[str] = []
    if f.letzte_wartung is not None:
        treffer += [m.group(0) for m in _FRAGE_LETZTE_WARTUNG.finditer(text or "")]
    if f.hu_bis:
        treffer += [m.group(0) for m in _FRAGE_HU.finditer(text or "")]
    if f.vorbesitzer is not None:
        treffer += [m.group(0) for m in _FRAGE_VORBESITZER.finditer(text or "")]
    return treffer


def bereinige_bericht(bericht: str, f: BekannteFakten) -> tuple[str, list[str]]:
    """Ersetzt im Berichtstext jede Frage, die einen bekannten Fakt als
    unbekannt behandelt, durch die geschärfte Frage. Der übrige Text bleibt
    unverändert; ohne bekannten Fakt passiert nichts."""
    if not bericht:
        return bericht, []
    ersetzt: list[str] = []
    text = bericht

    frage = wartung_frage(f)
    if frage is not None:
        def _wartung(m: re.Match) -> str:
            ersetzt.append(m.group(0))
            return frage[0]
        text = _FRAGE_LETZTE_WARTUNG.sub(_wartung, text)

    if f.hu_bis:
        def _hu(m: re.Match) -> str:
            ersetzt.append(m.group(0))
            return (f"Was stand im Prüfbericht der letzten Hauptuntersuchung (HU laut Inserat "
                    f"bis {f.hu_bis})?")
        text = _FRAGE_HU.sub(_hu, text)

    if f.vorbesitzer is not None:
        def _vb(m: re.Match) -> str:
            ersetzt.append(m.group(0))
            return (f"Stimmt die Angabe von {f.vorbesitzer} Vorbesitzern mit der "
                    f"Zulassungsbescheinigung Teil II überein?")
        text = _FRAGE_VORBESITZER.sub(_vb, text)
    return text, ersetzt
