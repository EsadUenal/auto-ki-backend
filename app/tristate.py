from __future__ import annotations

"""
Zentrales Tri-State-Modell für Inseratsangaben (KaufCheck-Final-Stabilization).

BEFUND (Production-Run Opel Astra K)
------------------------------------
Eingabe "Unfallstatus: nicht angegeben" plus Freitext "Keine eindeutige Angabe
zu früheren Unfallschäden oder Nachlackierungen." endete als "Laut Inserat
unfallfrei" samt der Verkäuferfrage "Gab es trotz der Angabe „unfallfrei“ …?".

ROOT CAUSE
----------
Jedes Feld hatte seine eigene, ad-hoc gebaute String-Heuristik
(`bekannte_fakten.unfall_status`, `tuning_status`, …). Die Unfall-Heuristik
suchte das Wort "unfallfrei" irgendwo im Text und kannte als Unsicherheits-
marker nur "unbekannt|unklar|nicht vollständig bekannt" DIREKT hinter
"Unfall". "Unfallfrei: nicht angegeben", "Ob das Fahrzeug unfallfrei ist, ist
unklar" oder "keine eindeutige Angabe" wurden dadurch zu einer positiven
Behauptung — eine UNKNOWN-Angabe konnte zu einem Fakt mutieren.

DAS MODELL
----------
Genau drei Zustände, nie ein vierter "verifiziert":

  CLAIMED_PRESENT  Das Inserat BEHAUPTET das Merkmal ("Unfallschaden repariert",
                   "Stage 1").
  CLAIMED_ABSENT   Das Inserat BEHAUPTET die Abwesenheit ("unfallfrei", "kein
                   Tuning vorhanden"). Bleibt eine Behauptung — ENFAL prüft sie
                   nicht und nennt sie nie "belegt". `eingeschraenkt=True`, wenn
                   die Aussage selbst eingeschränkt ist ("keine Unfallschäden
                   bekannt", "soweit bekannt").
  UNKNOWN          Keine Aussage ODER eine ausdrückliche Nichtaussage
                   ("nicht angegeben", "keine eindeutige Angabe", "unbekannt",
                   "Kein Tuning angegeben", "Ob … unfallfrei ist, ist unklar").

REGELN (für jedes Feld gleich, nur das Vokabular ist feldspezifisch)
-------------------------------------------------------------------
1. Der Text wird in Teilsätze zerlegt. Ein Teilsatz, der das Thema nennt UND
   einen Unsicherheitsmarker trägt, ist UNKNOWN — egal welche Schlagworte sonst
   darin stehen ("Keine eindeutige Angabe zu Unfallschäden" ist KEINE Verneinung).
2. Ein UNKNOWN-Teilsatz dominiert jede Behauptung desselben Themas: ein Inserat,
   das sich selbst widerspricht, liefert keinen Fakt.
3. Behauptungen in beide Richtungen zum selben Thema ergeben UNKNOWN
   (`konflikt=True`) — ENFAL entscheidet nicht, welche stimmt.
4. Eine strukturierte Angabe (Auswahlfeld) gilt vor dem Freitext, wird aber von
   einer ausdrücklichen Unsicherheit im Freitext überstimmt (ein veraltetes
   Häkchen darf keine Unfallfreiheit behaupten, wenn der Text sie offenlässt).
"""

import re
from dataclasses import dataclass, field

CLAIMED_PRESENT = "claimed_present"
CLAIMED_ABSENT = "claimed_absent"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class TriState:
    state: str = UNKNOWN
    # "structured" | "text" | None — woher die Aussage stammt.
    quelle: str | None = None
    # Nur bei CLAIMED_ABSENT: die Aussage ist selbst eingeschränkt
    # ("keine Unfallschäden bekannt", "soweit bekannt").
    eingeschraenkt: bool = False
    # Widersprüchliche Aussagen zum selben Thema -> UNKNOWN.
    konflikt: bool = False
    beleg: str | None = None

    @property
    def unknown(self) -> bool:
        return self.state == UNKNOWN

    def as_dict(self) -> dict:
        return {"state": self.state, "quelle": self.quelle,
                "eingeschraenkt": self.eingeschraenkt, "konflikt": self.konflikt,
                "verified": False}


# ── Gemeinsame Marker ────────────────────────────────────────────────────────

_U = "(?:ä|ae)"
_O = "(?:ö|oe)"

# Ausdrückliche Nichtaussagen. Bewusst feldunabhängig.
_UNBEKANNT = re.compile(
    r"nicht\s+angegeben|nicht\s+genannt|nicht\s+erw" + _U + r"hnt|nicht\s+dokumentiert"
    r"|keine\s+(?:\w+\s+){0,2}?angaben?\b|ohne\s+angaben?\b|k\.\s?a\.|\bk\s?a\b(?=\W*$)|\bn/?a\b"
    r"|unbekannt|nicht\s+bekannt|unklar|ungekl" + _U + r"rt|offen\s+geblieben"
    r"|nicht\s+(?:vollst" + _U + r"ndig|eindeutig|sicher|abschlie(?:ß|ss)end)\s+"
    r"(?:bekannt|gekl" + _U + r"rt|belegt|angegeben|nachvollziehbar)"
    r"|keine\s+(?:\w+\s+){0,1}?informationen?\b|liegen\s+(?:hierzu\s+|dazu\s+)?keine\s+\w+\s+vor"
    r"|wei(?:ß|ss)\s+(?:ich\s+)?nicht|nicht\s+sicher|kann\s+(?:ich\s+)?nicht\s+(?:sagen|beurteilen)"
    r"|kein(?:e|en|er)?\s+(?:[\w-]+\s+){0,3}?angegeben",
    re.IGNORECASE)

# Ein Teilsatz, der eine Frage oder Bedingung formuliert ("Ob …", "Falls …"),
# ist nie eine Behauptung.
_KEINE_BEHAUPTUNG = re.compile(r"^\s*(?:ob|falls|wenn|sofern|eventuell|evtl\.?|vielleicht|m" + _O
                               + r"glicherweise)\b", re.IGNORECASE)

# Einschränkung einer Negativaussage.
_EINGESCHRAENKT = re.compile(r"\bbekannt\w*|soweit\s+bekannt|laut\s+vor(?:besitzer|halter)"
                             r"|nach\s+(?:unserem|meinem)\s+wissen|meines\s+wissens",
                             re.IGNORECASE)

_TEILSATZ = re.compile(r"[.;!\n]+|,\s*(?=[a-zäöüß])|\s+[-–]\s+|,\s+(?=ob\b)", re.IGNORECASE)


@dataclass(frozen=True)
class Feld:
    """Vokabular EINES Themas. Nur das Vokabular ist feldspezifisch — die
    Auswertung ist für alle Felder dieselbe (`bewerte`)."""
    name: str
    thema: re.Pattern
    vorhanden: re.Pattern         # Behauptung des Merkmals
    abwesend: re.Pattern          # Behauptung der Abwesenheit
    # Strukturierte Auswahlwerte -> Zustand (lowercase, getrimmt).
    auswahl: dict = field(default_factory=dict)


def _rx(muster: str) -> re.Pattern:
    return re.compile(muster, re.IGNORECASE)


UNFALL = Feld(
    "unfall",
    thema=_rx(r"unfall\w*|vorsch" + _U + r"d\w*|karosseriesch" + _U + r"d\w*|blechsch" + _U + r"d\w*"),
    vorhanden=_rx(r"nicht\s+unfallfrei|unfallschaden\s+(?:vorhanden|repariert|behoben)|unfallwagen"
                  r"|unfall\s+(?:repariert|behoben|gehabt)|reparierte[rn]?\s+unfall\w*"
                  r"|hatte\s+(?:einen|mehrere)\s+unf" + _U + r"lle?|unfallbesch" + _U + r"digt"
                  r"|vorschaden\s+(?:vorhanden|repariert|behoben)|mit\s+unfallschaden"
                  r"|(?:leichter|schwerer|kleiner)\s+unfallschaden"),
    abwesend=_rx(r"(?<!nicht\s)unfallfrei|(?<!nicht\s)unfallschadenfrei|ohne\s+unfall\w*|kein(?:e|en)?\s+unf" + _U + r"lle?\b"
                 r"|kein(?:e|en)?\s+(?:unfall|vor|karosserie|blech)sch" + _U + r"d\w*"
                 r"|kein(?:e|en)?\s+unfallwagen"),
    auswahl={"ja": CLAIMED_ABSENT, "true": CLAIMED_ABSENT, "unfallfrei": CLAIMED_ABSENT,
             "nein": CLAIMED_PRESENT, "false": CLAIMED_PRESENT, "unfall": CLAIMED_PRESENT,
             "unfallschaden": CLAIMED_PRESENT},
)

TUNING = Feld(
    "tuning",
    thema=_rx(r"tuning|getunt|leistungssteigerung|kennfeld\w*|chip\w*|stage\s*[123]|umbau\w*"
              r"|softwareoptimierung|software-optimierung|serienzustand|originalzustand"),
    vorhanden=_rx(r"chiptuning|stage\s*[123]|(?<!keine\s)leistungssteigerung(?:\s+(?:durchgef(?:ü|ue)hrt|vorhanden))?|tuning\s+vorhanden|(?<!nicht\s)getunt|kennfeldoptimierung"
                  r"|softwareoptimierung|software-optimierung|umbauten\s+vorhanden|umgebaut"),
    abwesend=_rx(r"kein(?:e|en)?\s+(?:tuning|umbauten|leistungssteigerung|chiptuning)"
                 r"|nicht\s+getunt|ohne\s+(?:tuning|umbauten)|serienzustand|originalzustand"
                 r"|unver" + _U + r"ndert"),
    auswahl={"nein": CLAIMED_ABSENT, "kein": CLAIMED_ABSENT, "keines": CLAIMED_ABSENT,
             "keine": CLAIMED_ABSENT, "serie": CLAIMED_ABSENT, "serienzustand": CLAIMED_ABSENT,
             "kein tuning": CLAIMED_ABSENT, "original": CLAIMED_ABSENT,
             "ja": CLAIMED_PRESENT},
)

NACHLACKIERUNG = Feld(
    "nachlackierung",
    thema=_rx(r"nachlackier\w*|lackier\w*|originallack|teillackier\w*"),
    vorhanden=_rx(r"(?<!keine\s)(?<!ohne\s)(?:nach|teil|neu)lackiert|nachlackierung(?:en)?\s+(?:an|am|vorhanden)"
                  r"|(?:teilweise|neu)\s+lackiert|lackiert\s+(?:an|am|wurde|wurden)"),
    abwesend=_rx(r"kein(?:e|en)?\s+(?:nach|teil)?lackierung(?:en)?|ohne\s+(?:nach)?lackierung\w*"
                 r"|originallack|nicht\s+nachlackiert"),
)

REPARATUREN = Feld(
    "reparaturen",
    thema=_rx(r"reparatur\w*|repariert|instandsetz\w*|instand\s+gesetzt"),
    vorhanden=_rx(r"(?<!nicht\s)repariert|reparatur\w*\s+(?:an|am|der|des|wurde|wurden)|instand\s+gesetzt"
                  r"|instandsetzung\s+(?:an|am|der|des)"),
    abwesend=_rx(r"kein(?:e|en)?\s+reparatur\w*|nicht\s+repariert|ohne\s+reparatur\w*"),
)

IMPORT = Feld(
    "import",
    thema=_rx(r"\b(?:re)?import\w*|eu-?(?:fahrzeug|neuwagen)|deutsche[srn]?\s+(?:fahrzeug|auslieferung|erstzulassung)"),
    vorhanden=_rx(r"(?<!kein\s)(?<!keine\s)\b(?:re)?import(?:fahrzeug|wagen)?\b|eu-?(?:fahrzeug|neuwagen)"),
    abwesend=_rx(r"kein(?:e|en)?\s+(?:re)?import\w*|deutsche[srn]?\s+(?:fahrzeug|auslieferung|erstzulassung)"),
    auswahl={"nein": CLAIMED_ABSENT, "kein": CLAIMED_ABSENT, "import": CLAIMED_PRESENT,
             "reimport": CLAIMED_PRESENT, "ja": CLAIMED_PRESENT},
)

MAENGEL = Feld(
    "maengel",
    thema=_rx(r"m" + _U + r"ngel\w*|defekt\w*|sch" + _U + r"den\b|funktioniert\s+nicht"),
    vorhanden=_rx(r"(?<!keine\s)(?<!ohne\s)defekt\b|funktioniert\s+nicht|m" + _U + r"ngel\s+(?:an|am|vorhanden)"
                  r"|(?<!keine\s)(?<!ohne\s)defekte[rs]?\s+\w+"),
    abwesend=_rx(r"kein(?:e|en)?\s+(?:\w+\s+)?(?:m" + _U + r"ngel|defekte|sch" + _U + r"den)"
                 r"|m" + _U + r"ngelfrei|ohne\s+(?:\w+\s+)?m" + _U + r"ngel"),
)

SERVICE = Feld(
    "service",
    thema=_rx(r"scheckheft\w*|servicehistorie|serviceheft|wartungsnachweis\w*|inspektionsnachweis\w*"),
    vorhanden=_rx(r"scheckheftgepflegt|scheckheft\s+(?:vorhanden|gepflegt|liegt\s+vor)"
                  r"|servicehistorie\s+(?:vorhanden|liegt\s+vor|vollst" + _U + r"ndig)"),
    abwesend=_rx(r"kein(?:e|en)?\s+(?:scheckheft|servicehistorie|serviceheft|wartungsnachweis\w*)"
                 r"|ohne\s+(?:scheckheft|servicehistorie|serviceheft)"),
)

FELDER: dict[str, Feld] = {f.name: f for f in (UNFALL, TUNING, NACHLACKIERUNG, REPARATUREN,
                                               IMPORT, MAENGEL, SERVICE)}


_KA = re.compile(r"(?<![a-zäöüß])k\.\s?a\.?(?![a-zäöüß])", re.IGNORECASE)


def teilsaetze(text: str | None) -> list[str]:
    """Teilsätze. "k.A." wird vorher ausgeschrieben (sonst zerlegt der Punkt es),
    und ein Fragezeichen bleibt am Teilsatz: eine Frage ist nie eine Behauptung."""
    t = _KA.sub("keine Angabe", text or "")
    t = re.sub(r"\?", "?.", t)
    return [s.strip() for s in _TEILSATZ.split(t) if s and s.strip() and s.strip() != "?"]


def ist_unbekannt_angabe(text: str | None) -> bool:
    """Ob ein (Feld-)Text ausdrücklich keine Aussage macht."""
    return bool(text) and bool(_UNBEKANNT.search(text))


def _teilsatz_zustand(feld: Feld, satz: str) -> TriState | None:
    """Zustand EINES Teilsatzes zu EINEM Thema — oder None (Thema nicht genannt)."""
    if not (feld.thema.search(satz) or feld.vorhanden.search(satz) or feld.abwesend.search(satz)):
        return None
    if _UNBEKANNT.search(satz) or _KEINE_BEHAUPTUNG.search(satz) or "?" in satz:
        return TriState(UNKNOWN, "text", beleg=satz)
    # Verneinte Formen stehen im Abwesenheits-Vokabular und werden ZUERST
    # geprüft ("kein Tuning vorhanden" enthält "Tuning vorhanden"). Umgekehrt
    # schließen die Abwesenheitsmuster verneinte Abwesenheit selbst aus
    # ("nicht unfallfrei" ist eine Behauptung des Merkmals).
    if feld.abwesend.search(satz):
        return TriState(CLAIMED_ABSENT, "text",
                        eingeschraenkt=bool(_EINGESCHRAENKT.search(satz)), beleg=satz)
    if feld.vorhanden.search(satz):
        return TriState(CLAIMED_PRESENT, "text", beleg=satz)
    return None


def aus_text(feld: Feld, text: str | None) -> TriState:
    """Zustand eines Themas aus Freitext (Regeln 1-3 im Modulkopf)."""
    zustaende = [z for z in (_teilsatz_zustand(feld, s) for s in teilsaetze(text)) if z]
    if not zustaende:
        return TriState(UNKNOWN)
    if any(z.state == UNKNOWN for z in zustaende):
        return next(z for z in zustaende if z.state == UNKNOWN)
    arten = {z.state for z in zustaende}
    if len(arten) > 1:
        return TriState(UNKNOWN, "text", konflikt=True)
    return zustaende[0]


def aus_auswahl(feld: Feld, wert: str | None) -> TriState:
    """Zustand aus einem strukturierten Feld (Auswahl oder kurzer Feldtext)."""
    roh = (wert or "").strip()
    if not roh:
        return TriState(UNKNOWN)
    if ist_unbekannt_angabe(roh):
        return TriState(UNKNOWN, "structured", beleg=roh)
    direkt = feld.auswahl.get(roh.lower())
    if direkt:
        return TriState(direkt, "structured", beleg=roh)
    # Kurzer Feldtext ("Stage 1", "kein Tuning vorhanden"): dieselbe Teilsatz-
    # Auswertung, das Thema ist durch das Feld selbst gesetzt.
    z = aus_text(feld, roh)
    if z.state != UNKNOWN or z.konflikt or z.quelle:
        return TriState(z.state, "structured", z.eingeschraenkt, z.konflikt, roh)
    # Ein nicht einordenbarer, aber gefüllter Feldtext zum Thema (z.B. Tuning:
    # "Sportauspuff") ist eine Behauptung des Merkmals, keine Abwesenheit.
    if feld.name == "tuning":
        return TriState(CLAIMED_PRESENT, "structured", beleg=roh)
    return TriState(UNKNOWN, "structured", beleg=roh)


def bewerte(feld: Feld, auswahl: str | None, freitext: str | None) -> TriState:
    """Gesamtzustand: Auswahl vor Freitext, ausdrückliche Unsicherheit im
    Freitext überstimmt eine Auswahl-Behauptung (Regel 4)."""
    a = aus_auswahl(feld, auswahl)
    t = aus_text(feld, freitext)
    if t.state == UNKNOWN and t.quelle == "text" and not t.konflikt:
        return t if a.state != UNKNOWN or not a.quelle else a
    if a.state != UNKNOWN:
        if t.state not in (UNKNOWN, a.state):
            return TriState(a.state, a.quelle, a.eingeschraenkt, True, a.beleg)
        return a
    if a.quelle and a.state == UNKNOWN:
        # Das Auswahlfeld sagt ausdrücklich "keine Angabe" — eine Behauptung im
        # Freitext bleibt trotzdem eine Behauptung des Inserats.
        return t if t.state != UNKNOWN else a
    return t


def request_text(req) -> str:
    return " \n".join(str(getattr(req, f, None) or "") for f in ("beschreibung", "freitext"))


def fakten_aus_request(req) -> dict[str, TriState]:
    """Alle Tri-State-Themen eines Requests — EIN Aufruf, EIN Ergebnis."""
    text = request_text(req)
    return {
        "unfall": bewerte(UNFALL, getattr(req, "unfallfrei", None), text),
        "tuning": bewerte(TUNING, getattr(req, "tuning", None), text),
        "nachlackierung": bewerte(NACHLACKIERUNG, getattr(req, "vorschaeden", None), text),
        "reparaturen": bewerte(REPARATUREN, None, text),
        "import": bewerte(IMPORT, getattr(req, "import_status", None), text),
        "maengel": bewerte(MAENGEL, None, text),
        "service": bewerte(SERVICE, None, text),
    }
