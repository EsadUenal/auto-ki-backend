from __future__ import annotations

"""
Fahrzeugkarten-Segmentierung für den Marktvergleich.

Problem, das dieses Modul löst
------------------------------
Die Preis-Datenpunkte des Marktvergleichs entstanden bisher aus einem Zeichenfenster
um jeden Preisanker (`marktvergleich._FENSTER`, zusätzlich abgeschnitten an der Mitte
zum Nachbarpreis). Der Offline-Nachweis der Diagnose-Persistenz hat gezeigt, dass
dieses Verfahren Karten mitten durchschneidet: ein Kartentext begann mit dem
Datumsrest der Vorgängerkarte ("05/2019 . BMW 320d …") und endete bei "EZ ", bevor
das eigene Baujahr kam. Attribute benachbarter Inserate können sich so vermischen —
und genau daraus entstand ein `sehr ähnlich`-Treffer, der den Marktmedian mittrug.

Grundsatz
---------
Ein Zeichenfenster ist eine SCHWACHE Quelle. Es darf weiterhin Informationen
liefern, aber nie einen hochwertigen Vergleich begründen. Eine belastbare
Fahrzeugkarte muss STRUKTURELL erkennbar sein — über einen eigenen Detail-Link,
eine wiederholte Listenstruktur oder wiederkehrende Fahrzeugtitel als Anker.

Was die Live-Messung gezeigt hat
--------------------------------
Zwei echte Diagnoseläufe (gespeichert unter diagnose_runs/) haben die reale
Struktur einer Trefferliste belegt: jede Fahrzeugkarte beginnt mit einem
Markdown-Heading, dessen Link WURZEL-RELATIV auf die Detailseite zeigt —

    ## [<Fahrzeugtitel>](/s-anzeige/<slug>/<listing-id>-216-<n>)
    <Beschreibung> … <Preis> … <Kilometer> … EZ <MM/JJJJ>

179 solcher Anker auf 8 Suchseiten. Die frühere Link-Erkennung verlangte
"http(s)://" und übersah sie deshalb ausnahmslos — Verfahren A griff 0×, obwohl
die stärkste denkbare Struktur vorhanden war.

Bewusst NICHT gemacht
---------------------
Es wird trotzdem keine portalspezifische Struktur hartkodiert. Der Heading-Anker
ist ein generisches Markdown-Muster, und ob der Link auf ein Inserat zeigt,
entscheidet weiterhin `web_search.ist_einzelinserat` — nach Auflösung gegen die
Quell-URL. Alle Verfahren greifen nur, wenn die Struktur im Text nachweisbar
VORHANDEN ist. Ist sie es nicht, sagt das Modul das ehrlich ("window_fallback",
`structural_confidence="low"`) statt eine Struktur zu erfinden.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from app.vehicle_identity import MARKEN
from app.web_search import ist_einzelinserat

# ── Gemeinsame Muster ────────────────────────────────────────────────────────
# Hier definiert, damit Segmentierung und Extraktion (app/marktvergleich.py)
# GARANTIERT dieselben Preisanker sehen — sonst würden Offsets auseinanderlaufen.
RE_PREIS = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d{4,6})\s*(?:€|eur\b)", re.IGNORECASE)
RE_KM = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d{2,6})\s*km\b", re.IGNORECASE)
RE_EZ = re.compile(r"ez\s*\d{1,2}/((?:19|20)\d{2})", re.IGNORECASE)
RE_BJ = re.compile(r"(?:baujahr|bj\.?|aus)\s*((?:19|20)\d{2})", re.IGNORECASE)
RE_JAHR = re.compile(r"\b((?:19|20)\d{2})\b")

PREIS_MIN = 1_500
PREIS_MAX = 250_000
KM_MAX = 500_000
JAHR_MIN = 1990
JAHR_MAX = 2027

# Markdown-Link und nackte URL — Grundlage von Verfahren A.
RE_MD_LINK = re.compile(r"\[[^\]]*\]\(([^\s)]+)\)")
# Karten-Heading einer Trefferliste: "## [Fahrzeugtitel](/s-anzeige/<slug>/<id>)".
# Real beobachtete Kartengrenze (Live-Messung, 179 Vorkommen auf 8 Suchseiten).
RE_KARTEN_HEADING = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]*\[[^\]]*\]\(([^\s)]+)\)")
RE_NACKTE_URL = re.compile(r"https?://[^\s)\]]+")
# Anzeigen-ID im Fließtext ("Anzeigen-ID: 2812345678").
RE_ANZEIGEN_ID = re.compile(
    r"(?:anzeige[nr]?[-\s]?(?:id|nr\.?)|inserat[-\s]?(?:id|nr\.?)|art\.?[-\s]?nr\.?)"
    r"\s*[:.]?\s*(\d{6,})", re.IGNORECASE)
RE_LANGE_ID = re.compile(r"(?<!\d)(\d{6,})(?!\d)")

# Verfahren B: generische Trenner zwischen Listeneinträgen, von "sehr eindeutig"
# nach "schwach" sortiert. Kein portalspezifisches Markup.
_TRENNER = (
    ("absatz",      re.compile(r"\n{2,}")),
    ("listenpunkt", re.compile(r"\n\s*[-*•·]\s+")),
    ("tabelle",     re.compile(r"\s*\|\s*")),
    ("zeile",       re.compile(r"\n")),
    ("satzpunkt",   re.compile(r"\s+\.\s+")),
)

# Verfahren C: ein Markenname startet in Trefferlisten typischerweise den
# Fahrzeugtitel einer Karte ("BMW 320d G20 …").
_RE_MARKE = re.compile(
    r"(?<![a-zäöüß])(?:" + "|".join(sorted(MARKEN, key=len, reverse=True)) + r")(?![a-zäöüß])",
    re.IGNORECASE)

# Confidence-Stufen.
HOCH, MITTEL, NIEDRIG = "high", "medium", "low"


@dataclass
class CardSegment:
    """Ein Textabschnitt, der (mutmaßlich) GENAU EIN Fahrzeugangebot beschreibt."""

    text: str
    start: int
    end: int
    structural_confidence: str          # "high" | "medium" | "low"
    segmentation_method: str            # "detail_link" | "block_structure" |
                                        # "title_anchor" | "window_fallback"
    detected_detail_url: str | None = None
    detected_listing_id: str | None = None
    # Position des Preisankers, zu dem dieses Segment gehört (Offset im Gesamttext).
    preis_offset: int = 0
    gruende: list[str] = field(default_factory=list)

    @property
    def strukturell(self) -> bool:
        """Wurde die Karte strukturell bestätigt — oder ist es nur ein Textfenster?"""
        return self.segmentation_method != "window_fallback"


# ── Hilfen ───────────────────────────────────────────────────────────────────

def _zahl(roh: str) -> int:
    return int(roh.replace(".", "").replace(" ", ""))


def _preis_anker(text: str) -> list[re.Match]:
    """Alle plausiblen Preistreffer — die Anker, um die herum segmentiert wird."""
    return [m for m in RE_PREIS.finditer(text) if PREIS_MIN <= _zahl(m.group(1)) <= PREIS_MAX]


def _distinkte_km(text: str) -> set[int]:
    werte = set()
    for m in RE_KM.finditer(text):
        k = _zahl(m.group(1))
        if 0 <= k <= KM_MAX:
            werte.add(k)
    return werte


def _distinkte_jahre(text: str) -> set[int]:
    """Plausible Baujahre. EZ-/BJ-Angaben haben Vorrang: steht eine explizite
    Erstzulassung im Text, zählen nur diese Jahre — sonst würde eine beiläufige
    Jahreszahl im Titel ("BMW 3er 2019er Modell") als zweites, konkurrierendes
    Baujahr gewertet und die Karte unnötig verworfen."""
    ez = {int(m.group(1)) for m in RE_EZ.finditer(text)}
    ez |= {int(m.group(1)) for m in RE_BJ.finditer(text)}
    ez = {j for j in ez if JAHR_MIN <= j <= JAHR_MAX}
    if ez:
        return ez
    return {int(m.group(1)) for m in RE_JAHR.finditer(text)
            if JAHR_MIN <= int(m.group(1)) <= JAHR_MAX}


def _hat_fahrzeugbezug(text: str) -> bool:
    """Nennt der Abschnitt einen Fahrzeugtitel bzw. Modellhinweis (§3)?"""
    return bool(_RE_MARKE.search(text or ""))


def aufgeloester_detail_link(link: str, basis_url: str = "") -> str | None:
    """Löst einen Markdown-Link auf und gibt ihn zurück, WENN er auf eine echte
    Inserats-Detailseite zeigt — sonst None.

    Hintergrund (Live-Messung, zwei BMW-Läufe): Trefferlisten verlinken ihre
    Fahrzeugkarten wurzel-relativ ("/s-anzeige/<slug>/<id>-216-<n>"), nicht absolut.
    Die frühere Prüfung verlangte "http(s)://" und übersah dadurch ALLE 179 real
    vorhandenen Kartenlinks — Verfahren A griff nie.

    Bewusst generisch: relative Pfade werden per `urljoin` gegen die Quell-URL
    aufgelöst und danach durch DIESELBE Detailseiten-Erkennung geschickt wie
    absolute Links (`web_search.ist_einzelinserat`). Es gibt also keinen
    portalspezifischen Sonderweg — ein relativer Link zählt nur, wenn die
    aufgelöste Adresse als konkretes Einzelinserat erkannt wird.

    Nur ABSOLUTE und WURZEL-relative Pfade werden aufgelöst. Dokument-relative
    Links ("../x", "y.html") sind in Trefferlisten unüblich und ihre Auflösung
    hängt am genauen Pfad der Quellseite — im Zweifel lieber kein Link.
    """
    if not link:
        return None
    if link.startswith(("http://", "https://")):
        return link if ist_einzelinserat(link) else None
    if not link.startswith("/") or link.startswith("//") or not basis_url:
        return None
    try:
        absolut = urljoin(basis_url, link)
    except Exception:
        return None
    return absolut if ist_einzelinserat(absolut) else None


def _detail_link(text: str, basis_url: str = "") -> str | None:
    """Erster Link im Abschnitt, der auf eine echte Inserats-Detailseite zeigt —
    als vollständige URL (relative Pfade werden gegen `basis_url` aufgelöst)."""
    for m in RE_MD_LINK.finditer(text or ""):
        treffer = aufgeloester_detail_link(m.group(1), basis_url)
        if treffer:
            return treffer
    for m in RE_NACKTE_URL.finditer(text or ""):
        if ist_einzelinserat(m.group(0)):
            return m.group(0)
    return None


# Letztes Pfadsegment einer Detail-URL, das mit der Anzeigen-ID beginnt:
# "/s-anzeige/<slug>/3465662399-216-8618" -> "3465662399". Die nachfolgenden
# kurzen Zahlen sind Kategorie-/Ortscodes, keine Inserats-ID.
_RE_ID_SEGMENT = re.compile(r"^(\d{6,})(?:-\d+)*$")


def _listing_id(text: str, detail_url: str | None) -> str | None:
    """Anzeigen-ID eines Inserats — bevorzugt aus dem Fließtext, sonst aus der URL.

    Aus der URL wird zuerst das LETZTE Pfadsegment geprüft (dort steht die ID bei
    den real beobachteten Links). Erst wenn das nicht greift, gilt der frühere
    Rückfall "längste Ziffernfolge irgendwo im Pfad" — der ist unschärfer, weil ein
    Slug selbst lange Zahlen enthalten kann ("...-120000-km-...").
    """
    m = RE_ANZEIGEN_ID.search(text or "")
    if m:
        return m.group(1)
    if not detail_url:
        return None
    try:
        p = urlparse(detail_url)
        pfad, query = p.path, p.query
    except Exception:
        pfad, query = detail_url, ""
    segmente = [s for s in pfad.split("/") if s]
    if segmente:
        treffer = _RE_ID_SEGMENT.match(segmente[-1])
        if treffer:
            return treffer.group(1)
    kandidaten = RE_LANGE_ID.findall(f"{pfad}?{query}")
    return max(kandidaten, key=len) if kandidaten else None


def validiere_karte(text: str) -> tuple[bool, list[str]]:
    """Konservative Kartenvalidierung (§3).

    Ein Abschnitt gilt nur dann als EINE Fahrzeugkarte, wenn darin plausibel
    zusammengehören: genau ein Hauptpreis, genau ein Kilometerstand, genau ein
    Baujahr/EZ und ein Fahrzeugtitel bzw. Modellhinweis. Konkurrieren mehrere
    Preise, Kilometerstände oder Baujahre ohne klare Unterteilung, ist die
    Zuordnung geraten — dann KEINE strukturelle Karte.

    Rückgabe: (gültig, Gründe). Die Gründe wandern in die Diagnose, damit später
    nachvollziehbar ist, warum ein Abschnitt nicht als Karte durchging.
    """
    gruende: list[str] = []
    preise = _preis_anker(text)
    if len(preise) != 1:
        gruende.append(f"{len(preise)} Preise im Abschnitt (genau 1 erwartet)")
    km = _distinkte_km(text)
    if len(km) != 1:
        gruende.append(f"{len(km)} Kilometerstände im Abschnitt (genau 1 erwartet)")
    jahre = _distinkte_jahre(text)
    if len(jahre) != 1:
        gruende.append(f"{len(jahre)} Baujahre im Abschnitt (genau 1 erwartet)")
    if not _hat_fahrzeugbezug(text):
        gruende.append("kein Fahrzeugtitel/Modellhinweis im Abschnitt")
    return (not gruende), gruende


# ── Verfahren A: explizite Detail-Links ──────────────────────────────────────

# Markdown-Bild unmittelbar vor einem Karten-Heading (Vorschaubild der Karte).
_RE_BILD_DAVOR = re.compile(r"!\[[^\]]*\]\([^)]*\)\s*$")


def _mit_vorschaubild(text: str, heading_pos: int) -> int:
    """Zieht die Kartengrenze vor ein direkt vorangehendes Vorschaubild.

    Real beobachtet: eine Karte besteht aus Vorschaubild, dann Heading, dann Inhalt —

        ![BMW 320d G20 Sport Line … Vorschau](https://img…)
        ## [BMW 320d G20 Sport Line](/s-anzeige/…)

    Das Bild steht also VOR dem Heading, gehört aber zu DIESER Karte. Ohne diese
    Korrektur endet die VORHERIGE Karte mit dem Alt-Text des nächsten Fahrzeugs —
    und erbt daraus dessen Motor und Generation. Genau das ist beim Testfall
    "Karte ohne eigene Angaben" aufgefallen.
    """
    m = _RE_BILD_DAVOR.search(text, 0, heading_pos)
    return m.start() if m else heading_pos


# Realstruktur (aus diagnose_runs/ belegt): eine Karte beginnt als LISTENPUNKT mit
# ihrem eigenen Vorschaublock und erst DANACH folgt ihr Heading —
#
#     * [![BMW 320d Limousine Aut. Advantage … Vorschau](https://img…)
#
#       35](/s-anzeige/bmw-320d-limousine-aut-advantage/3486808429-216-4824)
#
#       63533 Mainhausen
#
#       Heute, 09:47
#
#       ## [BMW 320d Limousine Aut. Advantage](/s-anzeige/…/3486808429-216-4824)
#
# Zwischen Bild und Heading stehen also Link, Ort und Datum — `_RE_BILD_DAVOR`
# greift hier NICHT (es verlangt das Bild unmittelbar vor dem Heading). Folge: der
# komplette Vorschaublock der FOLGENDEN Anzeige blieb am FUSS der vorherigen Karte
# hängen (forensischer Audit: "Gran Turismo" und der GT-Alt-Text landeten so in
# fremden Karten).
_RE_LISTENPUNKT = re.compile(r"(?m)^[ 	]*\*[ 	]+\[")
# Linkziel in Markdown — bewusst NUR der "](ziel)"-Teil, damit auch der Link eines
# verschachtelten Bild-Links (Bild-Link, Leerzeile, dann "NN](/s-anzeige/...)").
_RE_LINKZIEL = re.compile(r"\]\(([^\s)]+)\)")


def _vorschaublock_start(text: str, heading_pos: int, heading_link: str,
                         basis_url: str) -> int | None:
    """Beginn des Vorschau-Listenpunkts, der zu DIESER Anzeige gehört.

    Strukturelle Grenze statt Zeichenfenster: der vorangehende Listenpunkt wird nur
    dann zur Karte gezogen, wenn er selbst einen Detail-Link auf DIESELBE Anzeige
    trägt wie das Heading. Das ist ein Identitätsnachweis, keine Heuristik — ohne
    ihn bleibt die Grenze, wo sie war.
    """
    ziel = aufgeloester_detail_link(heading_link, basis_url)
    if not ziel:
        return None
    punkte = [m.start() for m in _RE_LISTENPUNKT.finditer(text, 0, heading_pos)]
    if not punkte:
        return None
    start = punkte[-1]
    for m in _RE_LINKZIEL.finditer(text, start, heading_pos):
        if aufgeloester_detail_link(m.group(1), basis_url) == ziel:
            return start
    return None


def _kartenstart(text: str, heading_pos: int, heading_link: str,
                 basis_url: str) -> int:
    """Wo beginnt die Karte zu diesem Heading? Vorschaublock > Vorschaubild > Heading."""
    vor = _vorschaublock_start(text, heading_pos, heading_link, basis_url)
    if vor is not None:
        return vor
    return _mit_vorschaubild(text, heading_pos)


def _anker_detail_links(text: str, basis_url: str = "") -> list[int]:
    """Startpositionen von Karten, die einen eigenen Detail-Link tragen.

    Zuerst wird nach KARTEN-HEADINGS gesucht ("## [Fahrzeugtitel](/s-anzeige/…)").
    Das ist die real beobachtete Kartengrenze: der Heading eröffnet die Karte, ihm
    folgen Beschreibung, Preis, Kilometer und EZ desselben Inserats. Ein Anker auf
    dem Heading hält den Fahrzeugtitel INNERHALB der Karte — er ist Teil des
    Inserats und darf dessen Motor/Generation belegen. Ein Anker mitten im Text
    (z.B. am Markennamen der Beschreibung) würde den Titel samt Link abschneiden.

    Nur wenn keine Karten-Headings vorliegen, gelten einfache Detail-Links bzw.
    nackte Detail-URLs als Anker — für Quellen ohne Heading-Struktur.
    """
    headings: list[int] = []
    for m in RE_KARTEN_HEADING.finditer(text):
        if not aufgeloester_detail_link(m.group(1), basis_url):
            continue
        start = _kartenstart(text, m.start(), m.group(1), basis_url)
        # Die Grenze darf NIE hinter den Anfang der vorherigen Karte rutschen —
        # sonst überlappten die Blöcke und die Zuordnung kippte.
        if headings and start <= headings[-1]:
            start = m.start()
        headings.append(start)
    if len(headings) >= 2:
        return headings

    positionen: list[int] = []
    for m in RE_MD_LINK.finditer(text):
        if aufgeloester_detail_link(m.group(1), basis_url):
            positionen.append(m.start())
    if len(positionen) < 2:
        # Auch nackte URLs zulassen, wenn keine Markdown-Links vorhanden sind.
        nackt = [m.start() for m in RE_NACKTE_URL.finditer(text)
                 if ist_einzelinserat(m.group(0))]
        if len(nackt) > len(positionen):
            positionen = nackt
    return positionen


# ── Verfahren C: Fahrzeugtitel als Anker ─────────────────────────────────────

def _anker_titel(text: str) -> list[int]:
    return [m.start() for m in _RE_MARKE.finditer(text)]


def _bloecke_aus_ankern(text: str, anker: list[int]) -> list[tuple[int, int]]:
    """Aus Ankerpositionen zusammenhängende, überschneidungsfreie Blöcke bilden."""
    if not anker:
        return []
    grenzen = sorted(set(anker))
    return [(grenzen[i], grenzen[i + 1] if i + 1 < len(grenzen) else len(text))
            for i in range(len(grenzen))]


def _bloecke_aus_trenner(text: str, rx: re.Pattern) -> list[tuple[int, int]]:
    bloecke: list[tuple[int, int]] = []
    pos = 0
    for m in rx.finditer(text):
        if m.start() > pos:
            bloecke.append((pos, m.start()))
        pos = m.end()
    if pos < len(text):
        bloecke.append((pos, len(text)))
    return bloecke


def _karten_aus_bloecken(text: str, bloecke: list[tuple[int, int]], methode: str,
                         confidence: str, titel_ende: int = 0,
                         basis_url: str = "") -> list[CardSegment]:
    """Validierte Karten aus Blöcken bauen — je Block höchstens EINE Karte.

    `titel_ende` schneidet die SEITENÜBERSCHRIFT aus der ersten Karte heraus. Der
    Titel einer Trefferliste ("BMW 320d G20 gebraucht kaufen") beschreibt die SUCHE,
    nicht das erste Fahrzeug — bliebe er im Kartentext, würde die erste Karte Motor
    und Generation aus der Überschrift erben. Genau das verbietet §2.
    """
    karten: list[CardSegment] = []
    for start, ende in bloecke:
        start = max(start, titel_ende)
        if start >= ende:
            continue
        abschnitt = text[start:ende]
        gueltig, gruende = validiere_karte(abschnitt)
        if not gueltig:
            continue
        anker = _preis_anker(abschnitt)
        detail_url = _detail_link(abschnitt, basis_url)
        karten.append(CardSegment(
            text=abschnitt, start=start, end=ende,
            structural_confidence=confidence, segmentation_method=methode,
            detected_detail_url=detail_url,
            detected_listing_id=_listing_id(abschnitt, detail_url),
            preis_offset=start + anker[0].start(),
            gruende=gruende,
        ))
    return karten


def segmentiere(text: str, source_url: str = "",
                titel_ende: int = 0) -> tuple[list[CardSegment], str]:
    """Zerlegt einen Treffertext in Fahrzeugkarten.

    `titel_ende`: Ende der Seitenüberschrift im Text. Bei einer Trefferliste (zwei
    oder mehr Karten) wird die Überschrift aus der ersten Karte herausgeschnitten,
    damit sie ihren Motor/ihre Generation nicht an das erste Fahrzeug vererbt (§2).
    Bei einer Seite mit genau EINEM Angebot bleibt sie erhalten — dort IST die
    Überschrift der Fahrzeugtitel.

    Rückgabe: (strukturell bestätigte Karten, verwendetes Verfahren). Eine leere
    Liste mit Verfahren "keine" bedeutet: für diesen Text ist KEINE strukturelle
    Segmentierung nachweisbar — der Aufrufer muss auf das Zeichenfenster
    zurückfallen und die Datenpunkte entsprechend abwerten (§1/§5).

    Verfahren in fester Priorität:
      A "detail_link"     — jede Karte trägt einen eigenen Inserats-Detail-Link.
      B "block_structure" — wiederholte Listen-/Blockstruktur (Absatz, Aufzählung,
                            Tabellenzeile, Zeile, Satzpunkt).
      C "title_anchor"    — wiederkehrende Fahrzeugtitel (Markenname) als Anker.
      D "keine"           — nichts davon nachweisbar.
    """
    if not text or not _preis_anker(text):
        return [], "keine"

    # A — Detail-Links
    anker = _anker_detail_links(text, source_url)
    if len(anker) >= 2:
        karten = _karten_aus_bloecken(text, _bloecke_aus_ankern(text, anker),
                                      "detail_link", HOCH, titel_ende, source_url)
        if len(karten) >= 2:
            return karten, "detail_link"

    # B — wiederholte Block-/Listenstruktur. Es gewinnt der Trenner, der die
    # MEISTEN gültigen Karten liefert; bei Gleichstand der spezifischere (die
    # Reihenfolge in _TRENNER ist von eindeutig nach schwach sortiert).
    beste: list[CardSegment] = []
    for _name, rx in _TRENNER:
        bloecke = _bloecke_aus_trenner(text, rx)
        if len(bloecke) < 2:
            continue
        karten = _karten_aus_bloecken(text, bloecke, "block_structure", MITTEL,
                                      titel_ende, source_url)
        if len(karten) > len(beste):
            beste = karten
    if len(beste) >= 2:
        return beste, "block_structure"

    # C — Fahrzeugtitel als Anker
    titel_anker = _anker_titel(text)
    if len(titel_anker) >= 2:
        karten = _karten_aus_bloecken(text, _bloecke_aus_ankern(text, titel_anker),
                                      "title_anchor", MITTEL, titel_ende, source_url)
        if len(karten) >= 2:
            return karten, "title_anchor"

    # Einzelne Karte (z.B. eine echte Detailseite mit genau einem Angebot) —
    # ebenfalls strukturell tragfähig, wenn der GESAMTE Text die Kartenprüfung
    # besteht. Ohne diesen Zweig könnte eine saubere Einzelinserat-Seite nie eine
    # bestätigte Karte liefern.
    gueltig, _gruende = validiere_karte(text)
    if gueltig:
        detail_url = (_detail_link(text, source_url)
                      or (source_url if ist_einzelinserat(source_url) else None))
        anker_einzel = _preis_anker(text)
        return [CardSegment(
            text=text, start=0, end=len(text),
            structural_confidence=HOCH if detail_url else MITTEL,
            segmentation_method="detail_link" if detail_url else "single_card",
            detected_detail_url=detail_url,
            detected_listing_id=_listing_id(text, detail_url),
            preis_offset=anker_einzel[0].start(),
        )], "detail_link" if detail_url else "single_card"

    return [], "keine"
