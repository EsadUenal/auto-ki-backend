from __future__ import annotations

"""
Kanonische technische Risikomenge des KaufChecks.

BEFUND AUS DEM PRODUCTION-RUN (BMW M4 F82)
------------------------------------------
Derselbe Bericht zeigte zwei Karten zum selben Bauteil:

    "Pleuellager (M4)"                              Datenqualität Niedrig
        "vorbeugender Wechsel diskutiert"
    "Pleuellager: kritischer Wartungspunkt (M4)"    Datenqualität Hoch
        "vorbeugender Wechsel empfohlen", "~50-80 tkm"

Ursache ist kein Fahrzeug, sondern die Datenstruktur: dasselbe technische Thema
steht in bis zu drei Tabellen (`schwachstelle_baureihe`, `schwachstelle_motor`,
`kritische_wartung`), und `app/evidence.py::build_insights` erzeugte je Zeile
ein eigenes Insight. Gemessen über den gesamten Bestand: 168 Motorvarianten
haben ein Motorproblem, das zugleich als Wartungspunkt geführt wird
(Steuerkette allein 74-mal), dazu 99 verschiedene Überschneidungen zwischen
Baureihen- und Motorebene (Turbolader, Steuerkette, Zündspulen, ...).

Jeder Konsument (Evidence-Karten, Key Findings, Prüfplan, Empfehlungsgründe,
LLM-Kontext) sah damit mehrere unabhängige "Risiken", und jeder deutete sie
anders. Genau daraus entstanden Doppelkarten mit widersprüchlicher Evidenz und
eine Zusammenfassung, die den Detailbereich nicht kannte.

WAS DIESES MODUL TUT
--------------------
Es führt technische Aussagen nach IDENTITÄT des Bauteils zusammen, bevor
irgendein Konsument sie sieht. `build_insights` ruft `kanonisiere` am Ende auf;
danach arbeiten alle Stellen auf derselben finalen Menge.

  * Identität = der Kern der Bauteilbezeichnung (tragende Wörter ohne
    Klammerzusatz, Füllwörter und Tätigkeiten), dazu eine Abkürzung in Klammern
    als Alias ("Partikelfilter (DPF)" und "Dieselpartikelfilter (DPF)" sind
    dasselbe). Ein Kern, der als Kopfwort in genau EINEM anderen steckt
    ("Zahnriemen" / "Zahnriemen im Ölbad"), gehört ebenfalls dazu.
    Bewusst streng: eine verpasste Zusammenführung zeigt schlimmstenfalls zwei
    Karten (der alte Zustand), eine falsche Zusammenführung würde ein Risiko
    verstecken. "Kupplung" und "Haldex-Kupplung" bleiben deshalb getrennt, und
    "AGR-Ventil" und "AGR-Kühler" sind zwei Bauteile, nicht ein Thema.

  * Evidenz wird NIE erhöht. Führend ist die am besten belegte Aussage; ihre
    Datenqualität wird übernommen. Eine schwächere Aussage bleibt als
    "weiterer Datensatz" nachvollziehbar sichtbar, hebt aber nichts an.

  * Widersprechen sich gleich gut belegte Aussagen ("diskutiert" gegenüber
    "empfohlen"), wird die vorsichtigere führend: die, die weniger behauptet.

Es gibt KEINE fahrzeug- oder bauteilspezifische Regel. Die Wortlisten unten
beschreiben Sprachmuster, keine Fahrzeuge.
"""

import re

from app.models import EvidenceQuelle, Insight

# Kategorien, die eine technische Aussage über das Fahrzeug machen und deshalb
# zusammengeführt werden dürfen. Rückrufe NICHT: sie sind amtliche Einzelvorgänge
# mit eigener Referenz, zwei Rückrufe zum selben Bauteil sind zwei Aktionen.
RISIKO_KATEGORIEN = ("schwachstelle", "motorproblem", "web_schwachstelle",
                     "wartung", "web_wartung")

# Reihenfolge bei gleicher Beleglage: die Schwachstellen-Aussage beschreibt das
# Risiko, der Wartungseintrag den Umgang damit.
_KATEGORIE_RANG = {k: n for n, k in enumerate(RISIKO_KATEGORIEN)}

_CONFIDENCE_RANG = {"hoch": 3, "mittel": 2, "niedrig": 1}
_TRUST_RANG = {"verified": 3, "web": 2, "unverified_db": 1}

_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                          "Ä": "ae", "Ö": "oe", "Ü": "ue"})

# Wörter, die kein Bauteil benennen. Steht eines davon vorn ("Undichte
# Wasserpumpe", "Elektrische Heckklappe"), trägt das nächste Wort die Identität.
_FUELLWOERTER = frozenset({
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "am", "an", "im", "in",
    "bei", "mit", "und", "oder", "von", "vom", "zur", "zum", "fuer", "auf", "aus",
    "defekt", "defekte", "defekter", "defektes", "undicht", "undichte", "undichter",
    "gerissene", "gerissener", "verschlissene", "verschlissener", "fruehe", "frueher",
    "vorzeitige", "vorzeitiger", "erhoehte", "erhoehter", "hohe", "hoher", "starke",
    "starker", "elektrische", "elektrischer", "elektrisches", "hintere", "hinterer",
    "vordere", "vorderer", "linke", "rechte", "obere", "oberer", "untere", "unterer",
    "problem", "probleme", "schaden", "schaeden", "ausfall", "fehler", "verschleiss",
    "allgemein", "system", "systeme", "motor", "motoren",
})

# Tätigkeiten in Wartungsbezeichnungen: "Ölwechsel Turbolader" spricht über den
# Turbolader, "Kühlmittelwechsel Inverter" über den Inverter.
_TAETIGKEITEN = frozenset({
    "wechsel", "oelwechsel", "kuehlmittelwechsel", "tausch", "austausch", "erneuerung",
    "ueberpruefung", "pruefung", "kontrolle", "nachfuellung", "fuellstand", "reinigung",
    "spuelung", "einstellung", "einstellen", "service", "inspektion", "wartung",
})

# Abkürzung in Klammern als Alias: "(DPF)", "(KGE)", "(REX)". Motorcodes wie
# "(N47)" enthalten Ziffern und sind KEIN Alias: sie grenzen ein, statt zu
# benennen.
_ALIAS = re.compile(r"\(([A-ZÄÖÜ]{2,5})\)")
_KLAMMER = re.compile(r"\([^)]*\)")


def _norm(text: str | None) -> str:
    return (text or "").lower().translate(_UMLAUTE)


class BauteilKern:
    """Technische Identität einer Bauteilbezeichnung.

    `kern`  die tragenden Wörter ohne Klammerzusatz, Füllwörter und Tätigkeiten
            ("Steuerkette (N47)" -> {steuerkette}, "Ölwechsel Turbolader" ->
            {turbolader}, "Zahnriemen im Ölbad" -> {zahnriemen, oelbad})
    `kopf`  das erste tragende Wort, das Bauteil im engeren Sinn
    `alias` Abkürzungen in Klammern ("(DPF)")
    """

    __slots__ = ("kern", "kopf", "alias")

    def __init__(self, kern: frozenset[str], kopf: str | None, alias: frozenset[str]):
        self.kern, self.kopf, self.alias = kern, kopf, alias


def bauteil_kern(bauteil: str | None) -> BauteilKern:
    if not bauteil:
        return BauteilKern(frozenset(), None, frozenset())
    alias = frozenset(a.lower() for a in _ALIAS.findall(bauteil))
    ohne_klammern = _KLAMMER.sub(" ", _norm(bauteil))
    woerter = [w for w in re.split(r"[^a-z0-9]+", ohne_klammern)
               if len(w) >= 3 and not w.isdigit()]
    tragend = [w for w in woerter if w not in _FUELLWOERTER and w not in _TAETIGKEITEN]
    if not tragend and woerter:
        # Nur generische Wörter ("Motor", "Elektrik"): das Wort selbst ist dann
        # die Identität. Zwei Aussagen "Elektrik" sind dasselbe Thema.
        tragend = woerter[:1]
    return BauteilKern(frozenset(tragend), tragend[0] if tragend else None, alias)


def gleiches_thema(a: str | None, b: str | None) -> bool:
    """Sprechen zwei Bauteilbezeichnungen über dasselbe technische Thema?

    Streng, weil eine falsche Zusammenführung ein Risiko verstecken würde:
      * gleicher Kern ("Steuerkette" / "Steuerkette (TSI-Motoren)"), oder
      * gleiche Abkürzung ("Partikelfilter (DPF)" / "Dieselpartikelfilter (DPF)"),
      * oder der eine Kern steckt im anderen UND ist dort das Kopfwort
        ("Zahnriemen" / "Zahnriemen im Ölbad"). "Kupplung" und
        "Haldex-Kupplung" bleiben getrennt: Kopf ist dort "Haldex".
    Die Eindeutigkeit bei mehreren Kandidaten prüft `kanonisiere`.
    """
    ka, kb = bauteil_kern(a), bauteil_kern(b)
    if not ka.kern or not kb.kern:
        return bool(ka.alias & kb.alias)
    if ka.kern == kb.kern or (ka.alias & kb.alias):
        return True
    return _teil_mit_kopf(ka, kb) or _teil_mit_kopf(kb, ka)


def _teil_mit_kopf(klein: BauteilKern, gross: BauteilKern) -> bool:
    return (bool(klein.kern) and klein.kern < gross.kern and gross.kopf in klein.kern)


# ── Art eines Wartungseintrags (Befund F) ────────────────────────────────────
#
# `kritische_wartung` ist heterogen. Neben planmäßigen Arbeiten ("Zündkerzen:
# 60.000 km") stehen dort vorbeugende Tauschempfehlungen ("Pleuellager: ~50-80
# tkm, vorbeugender Wechsel"), Zustandshinweise ("Steuerkette: Sichtprüfung ab
# 100.000 km") und Umbauten ("Kurbelnabe: Pinnen/Capture-Plate"). Der Prüfplan
# fragte bei ALLEN nach der "letzten Durchführung": bei einer Kurbelnabe ist das
# sinnlos. Die Art entscheidet jetzt, welche Frage gestellt wird.
WARTUNG_REGULAER = "regulaer"          # planmäßige Arbeit mit Intervall
WARTUNG_VERSCHLEISS = "verschleiss"    # Verschleißteil, Tausch nach Zustand
WARTUNG_VORBEUGEND = "vorbeugend"      # vorbeugender Tausch einer Schwachstelle
WARTUNG_ZUSTAND = "zustand"            # Zustandsprüfung, kein festes Intervall
WARTUNG_MODIFIKATION = "modifikation"  # Umbau, Nachrüstung, Upgrade, Stilllegung

# Gemessen über alle 1.476 Wartungszeilen (Root-Cause-Closing): 1.241 regulär,
# 212 Zustand, 21 vorbeugend, 2 Umbau. Die Wortlisten beschreiben Sprachmuster
# dieser Zeilen, keine Fahrzeuge.
_MODIFIKATION = re.compile(
    r"upgrade|nachr[üu]st|umr[üu]st|umbau|pinn|capture|verst[äa]rkt\w* (?:teil|version)"
    r"|tuning|leistungssteiger|entfern|blindstopfen|stilllegung|stillgelegt|deaktivier",
    re.IGNORECASE)
# Vorbeugend heißt: ein TAUSCH vor dem Schaden. "Präventiv prüfen" ist keine
# Vorbeugung, sondern eine Zustandsprüfung.
_VORBEUGEND = re.compile(
    r"(?:vorbeugend|pr[äa]ventiv)\w*\s+(?:\w+\s+){0,2}(?:wechsel|tausch|austausch|ersatz"
    r"|erneuer)|als paar|paarweise|gleich mit|mittausch|zusammen tauschen|zusammen wechseln",
    re.IGNORECASE)
_ZUSTAND = re.compile(
    r"zustand|sichtpr[üu]f|bei (?:ger[äa]usch|bedarf|auff[äa]llig)|nach bedarf"
    r"|kein(?:e[nm]?)? fest", re.IGNORECASE)
# "Prüfung ab 100.000 km" ist eine Zustandsprüfung, "Kontrolle alle 40.000 km"
# eine regelmäßige Arbeit: entscheidend ist das periodische Wort.
_PRUEFUNG = re.compile(r"pr[üu]f|kontroll", re.IGNORECASE)
_PERIODISCH = re.compile(r"\balle\b|j[äa]hrlich|\bjede[nrs]?\b|\bpro\b|\bmax(?:\.|imal)",
                         re.IGNORECASE)
_KM_INTERVALL = re.compile(r"\d\s*(?:tkm|km)\b|\d{2,3}\.\d{3}", re.IGNORECASE)
_ZEIT_INTERVALL = re.compile(r"\b\d+\s*(?:jahre?n?|monate?n?)\b|\balle\s+\w*\s*(?:jahre?|monate?)",
                             re.IGNORECASE)
# Verschleißteile werden nach Zustand getauscht, nicht nach Kalender.
_VERSCHLEISSTEIL = re.compile(
    r"brems(?:belag|beläge|belaege|scheibe|anlage)|kupplung(?!s?öl|s?oel)|reifen"
    r"|stoßdämpfer|stossdaempfer|wischer|auspuff", re.IGNORECASE)


def wartungsart(intervall: str | None, hinweis: str | None,
                bauteil: str | None = None) -> str:
    """Art eines Wartungseintrags aus Intervall-, Hinweis- und Bauteiltext."""
    text = f"{intervall or ''} {hinweis or ''}"
    iv = intervall or ""
    if _MODIFIKATION.search(text):
        return WARTUNG_MODIFIKATION
    if _VORBEUGEND.search(text):
        return WARTUNG_VORBEUGEND
    periodisch = bool(_KM_INTERVALL.search(iv) or _ZEIT_INTERVALL.search(iv))
    if _VERSCHLEISSTEIL.search(bauteil or "") and not (periodisch and _PERIODISCH.search(iv)):
        return WARTUNG_VERSCHLEISS
    if _ZUSTAND.search(text):
        return WARTUNG_ZUSTAND
    if _PRUEFUNG.search(iv) and not _PERIODISCH.search(iv):
        return WARTUNG_ZUSTAND
    if periodisch:
        return WARTUNG_REGULAER
    return WARTUNG_ZUSTAND


# ── Risikoart: EINE Achse für alle technischen Aussagen (Befund F, 4.3) ──────
#
# Getrennt von Beleglage (confidence/trust) und Schwere (schweregrad): die Art
# sagt, WAS für eine Aussage vorliegt, und damit, welche Frage sinnvoll ist.
# Eine Modifikation oder eine bekannte Schwachstelle ist nie eine
# "Durchführung" und bekommt keinen "Wartungsnachweis".
RISIKOART_SCHWACHSTELLE = "schwachstelle"   # bekannte Schwachstelle/Reparaturthema
RISIKOART_RUECKRUF = "rueckruf"
WARTUNGSARTEN = (WARTUNG_REGULAER, WARTUNG_VERSCHLEISS, WARTUNG_VORBEUGEND,
                 WARTUNG_ZUSTAND, WARTUNG_MODIFIKATION)


def risikoart(insight: Insight) -> str:
    kategorie = getattr(insight, "kategorie", "") or ""
    if kategorie in ("rueckruf", "web_rueckruf"):
        return RISIKOART_RUECKRUF
    if kategorie in ("wartung", "web_wartung"):
        return getattr(insight, "wartungsart", None) or WARTUNG_REGULAER
    return RISIKOART_SCHWACHSTELLE


def risikoarten(insight: Insight) -> set[str]:
    """Die Art einer Aussage samt aller in sie zusammengeführten Aussagen."""
    return {risikoart(insight), *(risikoart(n) for n in getattr(insight, "nebenbelege", None) or [])}


# ── Verbindlichkeit einer Aussage (für Widersprüche) ────────────────────────
_STARK = re.compile(
    r"\b(?:empfohlen|empfehlen|vorgeschrieben|pflicht|zwingend|muss|m[üu]ssen"
    r"|unbedingt|dringend)\b", re.IGNORECASE)
_SCHWACH = re.compile(
    r"\b(?:diskutiert|umstritten|m[öo]glich|kann|k[öo]nnen|vereinzelt|selten"
    r"|gelegentlich|teilweise)\b", re.IGNORECASE)


def verbindlichkeit(text: str | None) -> int:
    """0 = zurückhaltend, 1 = neutral, 2 = verbindlich formuliert."""
    t = text or ""
    if _SCHWACH.search(t):
        return 0
    if _STARK.search(t):
        return 2
    return 1


# ── Beleglage ────────────────────────────────────────────────────────────────

_EINZELBERICHT = re.compile(
    r"\b(vereinzelt\w*|selten\w*|gelegentlich\w*|einzelf[äa]ll\w*|in einzelnen f[äa]llen)",
    re.IGNORECASE)


def ist_bekannt(insight: Insight) -> bool:
    """Darf diese Aussage "bekannt" heißen, oder ist sie ein gemeldeter Hinweis?

    Dieselbe Regel wie der Schwachstellen-Titel in `app/evidence.py`: nur ein
    geprüfter Fakt, der nicht selbst von Einzelfällen spricht.
    """
    return (getattr(insight, "trust", None) == "verified"
            and not _EINZELBERICHT.search(getattr(insight, "beschreibung", None) or ""))


def evidenz_rang(insight: Insight) -> tuple[int, int]:
    return (_CONFIDENCE_RANG.get((insight.confidence or "").lower(), 0),
            _TRUST_RANG.get(insight.trust or "", 0))


# ── Zusammenführung ──────────────────────────────────────────────────────────

_HERKUNFT = {
    "schwachstelle": "Baureihendaten",
    "motorproblem": "Motordaten",
    "wartung": "Wartungsdaten",
    "web_schwachstelle": "Webrecherche",
    "web_wartung": "Webrecherche",
}


def _satz(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    t = t[0].upper() + t[1:]
    return t if t.endswith((".", "!", "?")) else t + "."


def _gleicher_inhalt(a: str, b: str) -> bool:
    return re.sub(r"\W+", "", _norm(a)) == re.sub(r"\W+", "", _norm(b))


def _fuehrende_aussage(gruppe: list[Insight]) -> Insight:
    """Die am besten belegte Aussage; bei Gleichstand die vorsichtiger formulierte,
    dann die Schwachstellen- vor der Wartungsaussage, dann die frühere."""
    return min(
        gruppe,
        key=lambda i: (tuple(-x for x in evidenz_rang(i)),
                       verbindlichkeit(i.beschreibung),
                       _KATEGORIE_RANG.get(i.kategorie, 99),
                       gruppe.index(i)))


def _fuehre_zusammen(gruppe: list[Insight]) -> Insight:
    fuehrend = _fuehrende_aussage(gruppe)
    weitere = [i for i in gruppe if i is not fuehrend]

    quellen: list[EvidenceQuelle] = []
    for i in (fuehrend, *weitere):
        for q in i.quellen:
            if not any((q.typ, q.ref, q.titel, q.url) == (v.typ, v.ref, v.titel, v.url)
                       for v in quellen):
                quellen.append(q)
    typen: list[str] = []
    for q in quellen:
        if q.typ not in typen:
            typen.append(q.typ)

    zusatz: list[str] = []
    gesehen = [fuehrend.beschreibung or ""]
    for i in weitere:
        text = (i.beschreibung or "").strip()
        if not text or any(_gleicher_inhalt(text, g) for g in gesehen):
            continue
        gesehen.append(text)
        zusatz.append(f"Weiterer Datensatz ({_HERKUNFT.get(i.kategorie, 'Datenbank')}): "
                      f"{_satz(text)}")
    verbindlich = {verbindlichkeit(t) for t in gesehen}
    if 0 in verbindlich and 2 in verbindlich:
        zusatz.append("Die hinterlegten Angaben sind nicht einheitlich; übernommen ist "
                      "die zurückhaltendere.")
    if fuehrend.trust != "verified" and any(i.kategorie in ("wartung", "web_wartung")
                                            for i in gruppe):
        zusatz.append("Keine der Angaben ist als Herstellervorgabe belegt.")

    schweregrad = fuehrend.schweregrad or next(
        (i.schweregrad for i in weitere if i.schweregrad), None)
    beschreibung = " ".join([_satz(fuehrend.beschreibung or ""), *zusatz]).strip()

    return fuehrend.model_copy(update={
        "quellen": quellen,
        "quellen_typen": typen,
        "beschreibung": beschreibung,
        "schweregrad": schweregrad,
        "zusammengefuehrt": [f"{i.kategorie}:{i.fakt_ref or i.id}" for i in weitere],
        "nebenbelege": [*fuehrend.nebenbelege, *weitere],
    })


def kanonisiere(insights: list[Insight]) -> list[Insight]:
    """Führt technische Aussagen gleicher Identität zu EINEM Insight zusammen.

    Reihenfolge und IDs bleiben stabil: das zusammengeführte Insight steht an
    der Stelle und unter der ID seiner führenden Aussage, die übrigen Insights
    (Rückrufe, Marktvergleich) bleiben unverändert.
    """
    kandidaten = [(n, i) for n, i in enumerate(insights)
                  if i.kategorie in RISIKO_KATEGORIEN]
    # Union-Find über gemeinsame Schlüssel.
    eltern = list(range(len(kandidaten)))

    def wurzel(x: int) -> int:
        while eltern[x] != x:
            eltern[x] = eltern[eltern[x]]
            x = eltern[x]
        return x

    kerne = [bauteil_kern(i.bauteil) for _n, i in kandidaten]

    # Stufe 1: gleicher Kern oder gleiche Abkürzung.
    besitzer: dict[object, int] = {}
    for k, kern in enumerate(kerne):
        schluessel = ([("kern", kern.kern)] if kern.kern else []) + \
                     [("alias", a) for a in kern.alias]
        for s in schluessel:
            if s in besitzer:
                eltern[wurzel(k)] = wurzel(besitzer[s])
            else:
                besitzer[s] = k

    # Stufe 2: ein Kern, der als Kopf in GENAU EINEM anderen Thema steckt
    # ("Zahnriemen" in "Zahnriemen im Ölbad"). Gibt es mehrere Kandidaten
    # ("AGR" in "AGR-Ventil" UND "AGR-Kühler"), bleibt alles getrennt: welches
    # Bauteil gemeint ist, lässt sich dann nicht entscheiden, und zwei
    # verschiedene Bauteile dürfen nie zu einem Risiko verschmelzen.
    for k, kern in enumerate(kerne):
        kandidaten_wurzeln = {wurzel(j) for j, anderer in enumerate(kerne)
                              if j != k and _teil_mit_kopf(kern, anderer)}
        kandidaten_wurzeln.discard(wurzel(k))
        if len(kandidaten_wurzeln) == 1:
            eltern[wurzel(k)] = kandidaten_wurzeln.pop()

    gruppen: dict[int, list[int]] = {}
    for k in range(len(kandidaten)):
        gruppen.setdefault(wurzel(k), []).append(k)

    ersetzt: dict[int, Insight] = {}
    entfaellt: set[int] = set()
    for mitglieder in gruppen.values():
        if len(mitglieder) < 2:
            continue
        gruppe = [kandidaten[k][1] for k in mitglieder]
        neu = _fuehre_zusammen(gruppe)
        for k in mitglieder:
            n, i = kandidaten[k]
            if i.id == neu.id:
                ersetzt[n] = neu
            else:
                entfaellt.add(n)

    return [ersetzt.get(n, i) for n, i in enumerate(insights) if n not in entfaellt]
