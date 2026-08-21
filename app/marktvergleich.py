from __future__ import annotations

"""
Marktvergleich 2.0 — deterministische, ehrliche Preisbewertung (Phase 1).

Problem des alten Standes: die Marktspanne (marktpreis_min/max) wurde vollständig
vom LLM aus rohen Tavily-Snippets ERFUNDEN, und der Marktvergleich-Insight zeigte
allgemeine Suchseiten als scheinbare "Vergleichsfahrzeuge" — auch dann, wenn die
verlinkte Seite Fahrzeuge anderer Generation/Baujahr/Motorisierung/km enthielt
(die berüchtigte "7.000-€-Karre" neben einer 23.000-€-Spanne). Das beschädigt
Vertrauen.

Datenlage (real geprüft): Tavily (search_depth=basic) liefert überwiegend Such-/
Übersichtsseiten, KEINE sauberen Einzelinserate je URL (raw_content=null). Die
Snippet-TEXTE enthalten aber häufig mehrere echte Angebote als "Preis + km + EZ".
Wir können daraus deterministisch einzelne Preis-DATENPUNKTE extrahieren, ihre
Vergleichbarkeit gegen das Zielfahrzeug bewerten (Generation/Baujahr/km/Kraftstoff)
und daraus robuste Statistik (Median + Quartils-Spanne) berechnen.

Ehrlichkeitsgrundsätze:
- Keine erfundenen Fahrzeuge — nur was aus dem Snippet-Text extrahierbar war.
- Fremde Generationen (z.B. E90/F30 beim G20) werden verworfen, nicht mitgemittelt.
- Ausreißer verzerren die Spanne nicht (Median + Quartile statt min/max).
- Wenige/schwache Daten -> niedrige Datenqualität, KEINE Scheinpräzision.
- Eine allgemeine Suchseite bleibt Recherchequelle, wird aber NIE als einzelnes
  Vergleichsfahrzeug ausgegeben.
"""

import hashlib
import json
import logging
import re
import statistics
from urllib.parse import urlparse

from app.market_card_segmenter import segmentiere
from app.models import Marktanalyse, Preisbeobachtung
from app.marken import marke_tokens as _marken_schreibvarianten
from app.verification import is_verified
from app.web_search import ist_info_domain as _ist_info_domain
from app.web_search import ist_einzelinserat as _ist_einzelinserat
from app.web_search import ist_kategorieseite as _ist_kategorieseite_intern
from app.web_search import ist_marktplatz_domain as _ist_marktplatz_domain
from app.web_search import SOURCE_POLICY_GRUND as _SOURCE_POLICY_GRUND
from app.web_search import darf_preisbildend_sein as _darf_preisbildend_sein

log = logging.getLogger(__name__)

# ── Plausibilitätsgrenzen (Sicherheitsnetz gegen Fehl-Extraktion) ────────────
_PREIS_MIN = 1_500
_PREIS_MAX = 250_000
_KM_MAX = 500_000
_JAHR_MIN = 1990
_JAHR_MAX = 2027   # aktuelles Jahr + 1 (EZ-Neuwagen)

# Zeichen-Fenster um einen Preis-Treffer, in dem km/Baujahr als zum selben Angebot
# gehörend gewertet werden. Bewusst eng — lieber ein Feld None lassen (reduziert die
# Vergleichbarkeit) als km/Baujahr eines NACHBAR-Inserats falsch zuzuordnen.
_FENSTER = 130

# Vergleichbarkeits-Stufen (absteigend). Index = "Abwertungs-Distanz".
_STUFEN = ["sehr_aehnlich", "aehnlich", "bedingt", "ungeeignet"]
_UNGEEIGNET = len(_STUFEN) - 1

# Maximale relative Breite des typischen Marktbereichs (Spanne/Median). Ist die
# Streuung der "vergleichbaren" Preise größer, sind die extrahierten Datenpunkte
# in Wahrheit NICHT kohärent (z.B. gemischte Cash-/Finanzierungs-/Gesamtpreise
# derselben Anzeige, oder Fehl-Assoziation Preis↔km) — dann KEIN Schein-Median
# ausgeben, sondern ehrlich auf die unzuverlässige Datenbasis hinweisen.
_MAX_REL_SPANNE = 0.6

# §9: Marker im Preisumfeld, die einen Preis als Finanzierungs-/Leasing-/Monatsrate
# ODER Neuwagen-/Listenpreis entlarven — solche Werte sind KEINE Gebrauchtwagen-
# Gesamtpreise und dürfen nicht in den Marktmedian einfließen.
_FINANZ_MARKERS = (
    "leasing", "finanzierung", "monatlich", "mtl.", "mtl ", "/monat", "pro monat",
    "im monat", "anzahlung", "monatsrate", "€/mon", "eur/mon", "raten ab", "/mon.",
    "neuwagen", "neupreis", "listenpreis", "uvp", "ab werk",
)

# Preis: Zahl mit Tausenderpunkten ODER 4–6 Ziffern, GEFOLGT von € / EUR.
_RE_PREIS = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d{4,6})\s*(?:€|eur\b)", re.IGNORECASE)
# Kilometer: Zahl (mit/ohne Tausenderpunkt), gefolgt von km.
_RE_KM = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d{2,6})\s*km\b", re.IGNORECASE)
# Baujahr / Erstzulassung: "EZ 04/2020" (zuverlässigstes Signal) sowie
# "Baujahr 2020" / "aus 2020" / "BJ 2020".
_RE_EZ = re.compile(r"ez\s*\d{1,2}/((?:19|20)\d{2})", re.IGNORECASE)
_RE_BJ = re.compile(r"(?:baujahr|bj\.?|aus)\s*((?:19|20)\d{2})", re.IGNORECASE)
_RE_JAHR = re.compile(r"\b((?:19|20)\d{2})\b")

# Generations-/Chassis-Code (BMW E/F/G/U.., Mercedes W/S/C/A/X.., Audi B/C/D..).
_RE_CODE = re.compile(r"\b([a-z]\d{2,3})\b", re.IGNORECASE)

# §Wortgrenzen (Live-Audit Insignia B): Die Erkennung lief frueher als reines
# Teilstring-Match. "elektro" traf damit den Wortanfang von "Elektron.", so dass
# die Ausstattungszeile "Elektron. Stabilitaets-Programm Plus (ESP)" ein
# Diesel-Inserat (3488196893, Insignia B 2.0 CDTI) hart als Elektroauto verwarf.
#
# Loesung ist dieselbe wie bei der Karosserie-Erkennung: unicode-sichere
# Wortgrenzen plus EXPLIZIT gelistete Komposita. Kein Sonderfall fuer "Elektron.",
# sondern die generelle Regel, dass ein Kraftstoffbegriff ein vollstaendiger
# Fachbegriff sein muss. "elektronisch", "Elektronik" und "elektrisch" stehen
# damit konstruktiv draussen — sie sind keine Kraftstoffangaben, sondern
# Ausstattung.
_KRAFTSTOFF_WORTE = {
    # Reihenfolge: spezifisch vor allgemein (ein Plug-in-Hybrid ist kein Benziner).
    "hybrid": ("hybrid", "hybridantrieb", "vollhybrid", "mildhybrid",
               "phev", "plug-in", "plugin", "plug in"),
    "elektro": ("elektro", "elektroauto", "elektrofahrzeug", "elektroantrieb",
                "electric", "ev", "bev"),
    "diesel": ("diesel", "dieselmotor", "dieselfahrzeug", "tdi", "cdi", "hdi",
               "dci", "bluetec", "cdti", "crdi", "jtd"),
    "benzin": ("benzin", "benziner", "benzinmotor", "tsi", "tfsi", "gti",
               "petrol", "mpi", "gdi"),
}
_KRAFTSTOFF_RE: tuple[tuple[str, re.Pattern], ...] = tuple(
    # Die Vorgrenze sperrt bewusst NUR Buchstaben, keine Ziffern: Kraftstoff-
    # Kuerzel stehen regelmaessig direkt am Hubraum ("2.0CDTI", "1.6TDI"), und
    # eine Ziffernsperre verlor im Korpus 11 echte Diesel. Fuer den Fehlerfall
    # ("Elektron.") entscheidet ohnehin die NACH-Grenze.
    (norm, re.compile(r"(?<![a-zäöüß])(?:"
                      + "|".join(re.escape(k) for k in keys)
                      + r")(?![a-zäöüß])", re.IGNORECASE))
    for norm, keys in _KRAFTSTOFF_WORTE.items()
)

# ── Karosserie-Erkennung im Preisumfeld (§5) ─────────────────────────────────
# Bewusst LOKAL definiert statt aus app/vehicle_identity importiert: dort liegt
# "Sports Tourer" unter "Schrägheck" (für den Insignia-Kombi falsch), und
# vehicle_identity wird von der Ersatzteil-Pipeline mitbenutzt, die in dieser
# Etappe nicht angefasst werden soll. Wort-Grenzen sind zwingend — ein reines
# Teilstring-Match würde "van" in "Avant" finden.
_KAROSSERIE_WORTE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("kombi",       ("kombi", "touring", "avant", "variant", "sports tourer", "sportstourer",
                     "estate", "shooting brake", "caravan", "sportbrake")),
    ("cabrio",      ("cabrio", "cabriolet", "roadster", "spider", "spyder")),
    ("coupe",       ("coupé", "coupe")),
    ("suv",         ("suv", "geländewagen", "gelaendewagen", "crossover", "allroad")),
    ("van",         ("van", "minivan", "kleinbus", "hochdachkombi")),
    ("limousine",   ("limousine", "stufenheck", "sedan", "saloon", "grand sport", "grandsport",
                     "fastback", "grand coupé", "gran coupe")),
    ("schraegheck", ("schrägheck", "schraegheck", "fließheck", "fliessheck", "hatchback")),
)
_KAROSSERIE_RE: tuple[tuple[str, re.Pattern], ...] = tuple(
    (label, re.compile(r"(?<![a-zäöüß0-9])(?:" + "|".join(re.escape(k) for k in keys)
                       + r")(?![a-zäöüß])", re.IGNORECASE))
    for label, keys in _KAROSSERIE_WORTE
)

_RE_PS = re.compile(r"\b(\d{2,4})\s*ps\b", re.IGNORECASE)
_GETRIEBE_WORTE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("automatik", ("automatik", "automatic", "steptronic", "tiptronic", "wandlerautomatik",
                   "dsg", "dkg", "s tronic", "s-tronic", "pdk", "doppelkupplung", "edc",
                   "multitronic", "cvt", "g-tronic")),
    ("schaltgetriebe", ("schaltgetriebe", "handschalter", "manuell", "schaltung")),
)

# Inserats-ID in einer URL: eine zusammenhängende Ziffernfolge >= 6 Stellen
# (kleinanzeigen /s-anzeige/<slug>/2345678901-216-1234, mobile.de ?id=412345678,
# autoscout24 /angebote/<slug>-<id>). Konservativ: kürzere Zahlen (Baujahr,
# Seitennummer, Motorbezeichnung) sind KEINE Inserats-ID.
_RE_LISTING_ID = re.compile(r"(?<!\d)(\d{6,})(?!\d)")


def _zahl(roh: str) -> int:
    return int(roh.replace(".", "").replace(" ", ""))


def _domain(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
    except Exception:
        return ""
    return netloc[4:] if netloc.startswith("www.") else netloc


def _generation_tokens(text: str) -> list[str]:
    """Baureihen-Kürzel wie 'g20', 'e90' als lowercase-Token-Liste."""
    if not text:
        return []
    out: list[str] = []
    for t in re.split(r"[^a-z0-9]+", text.lower()):
        if re.fullmatch(r"[a-z]\d{2,3}", t):
            out.append(t)
    return out


def _wort_tokens(text: str) -> set[str]:
    """Alle alphanumerischen Wort-Token eines Textes (lowercase)."""
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t}


def _modell_tokens(name: str) -> set[str]:
    """Unterscheidungskräftige Modell-Token aus einem Modellnamen ODER einer
    Motorvarianten-Bezeichnung — strukturiert, KEIN Fahrzeug-Hardcoding.

    Behalten werden nur Token mit mindestens einem Buchstaben und Länge >= 3
    ('insignia', 'mokka', 'golf', 'glc', '3er', '320d', 'c200'). Bewusst verworfen:
    reine Zahlen ('200', '220' — mehrdeutig über Modelle hinweg) und Einzelbuchstaben
    ('c', 'e' — die Mercedes-Klassenbuchstaben sind ohne Zahl nicht trennscharf).
    So bleibt 'GLC' von 'C-Klasse', '5er' von '3er', 'Passat' von 'Golf' trennbar.
    """
    out: set[str] = set()
    for t in re.split(r"[^a-z0-9]+", (name or "").lower()):
        if len(t) >= 3 and not t.isdigit() and re.search(r"[a-z]", t):
            out.add(t)
    return out


def _num_modell_tokens(name: str) -> set[str]:
    """3-stellige Modell-Zahlen aus einer Bezeichnung ('320d'->'320', '530i'->'530',
    'c200'->'200', '911'->'911').

    Markenzahlen sind marken-INTERN unterscheidungskräftig (BMW 320 vs 520), aber
    marken-ÜBERGREIFEND mehrdeutig. Deshalb werden sie NUR marken-skopiert als
    Fremdsignal genutzt (siehe baue_ziel/marke_tokens) — nie markenübergreifend."""
    return set(re.findall(r"\d{3}", (name or "").lower()))


def _marke_tokens(marke: str) -> set[str]:
    """Marken-Wörter (>=2 Zeichen, lowercase) für den marken-skopierten Zahlenabgleich.
    'Mercedes-Benz' -> {'mercedes','benz'}, 'BMW' -> {'bmw'}."""
    return {t for t in re.split(r"[^a-z0-9]+", (marke or "").lower()) if len(t) >= 2}


# ── Fahrzeugvarianten (§Identität: Familie / Motor / Variante) ───────────────
# Der Audit von `_ist_fremdmodell` hat eine Ebenenverwechslung belegt: `modell_tokens`
# mischt Modellfamilie ("3er"), Motorbezeichnung ("320d") und — als Zerfallsprodukt
# mehrwortiger Modellnamen — Variantenwörter ("gran", "turismo"). Ein Treffer auf
# MOTORebene konnte dadurch einen Widerspruch auf VARIANTENebene neutralisieren:
# "BMW 320d Gran Turismo" galt als Zielfahrzeug, weil "320d" passt.
#
# Varianten werden deshalb als PHRASEN geführt, nie als Einzeltoken. Das Vokabular
# stammt ausschließlich aus vorhandenen DB-Daten (keine erfundenen Synonyme):
#   (a) Modellnamen derselben Marke mit gemeinsamem Familienwort — die Differenz
#       ist die Variante ("6er" / "6er Gran Turismo" -> "Gran Turismo";
#       "2er Coupé" / "2er Gran Coupé" -> "Coupé" bzw. "Gran Coupé").
#   (b) die `karosserie`-Arrays der Baureihen — sie bestätigen einwortige Varianten
#       gegen und erweitern die ERLAUBTE Menge des Ziels.
#
# Einwortige Phrasen werden NUR übernommen, wenn sie zusätzlich als Karosseriewert
# in der DB vorkommen. Genau daran scheitern die gefährlichen Kurzformen: "GT" (aus
# "e-tron GT") und "RS" (aus "TT RS") sind weder lang genug noch Karosseriewerte und
# fallen konstruktiv heraus — kein GT-Hack, keine Ausnahmeliste.
_VARIANTE_MIN_LEN = 3


def _phrase_re(phrase: str) -> re.Pattern:
    """Unicode-sichere Wortgrenzen. Eine ASCII-Grenze ([A-Za-z0-9]) behandelt Umlaute
    als Trennzeichen — "geprägt", "verfügt" und "trägt" enthielten dadurch ein
    vermeintlich freistehendes "gt". `\w` ist in Python 3 Unicode-fähig und schließt
    das aus."""
    return re.compile(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", re.IGNORECASE)


_PHRASE_CACHE: dict[str, re.Pattern] = {}


def _phrase_kommt_vor(phrase: str, text: str) -> bool:
    rx = _PHRASE_CACHE.get(phrase)
    if rx is None:
        rx = _PHRASE_CACHE[phrase] = _phrase_re(phrase)
    return bool(rx.search(text or ""))


def _karosserie_werte(alle_baureihen) -> set[str]:
    """Alle in der DB vorkommenden Karosseriebezeichnungen (lowercase)."""
    out: set[str] = set()
    for r in alle_baureihen or []:
        for k in _karosserie_liste(r):
            k = k.strip().lower()
            if len(k) >= _VARIANTE_MIN_LEN:
                out.add(k)
    return out


def _karosserie_liste(baureihe: dict) -> list[str]:
    roh = (baureihe or {}).get("karosserie")
    if isinstance(roh, list):
        return [str(x) for x in roh]
    if isinstance(roh, str) and roh.strip():
        try:
            geladen = json.loads(roh)
            if isinstance(geladen, list):
                return [str(x) for x in geladen]
        except (ValueError, TypeError):
            return []
    return []


def _variantenteil(modell: str) -> str:
    """Der Teil eines Modellnamens hinter dem Familienwort, ohne führende Zahlen.
    "6er Gran Turismo" -> "gran turismo"; "RS 3 Sportback" -> "sportback"."""
    teile = (modell or "").split()[1:]
    while teile and teile[0].isdigit():
        teile = teile[1:]
    return " ".join(teile).strip().lower()


def _variantenvokabular(alle_baureihen) -> set[str]:
    """Variantenphrasen, die eine FREMDE Fahrzeugvariante belegen können (§4a).

    Nur Phrasen aus Modellnamen-Familien mit mindestens zwei Mitgliedern. Einwortige
    Phrasen brauchen zusätzlich die Bestätigung als DB-Karosseriewert.
    """
    karosserien = _karosserie_werte(alle_baureihen)
    familien: dict[tuple[str, str], set[str]] = {}
    for r in alle_baureihen or []:
        modell = (r.get("modell") or "").strip()
        if not modell:
            continue
        familie = modell.split()[0].lower()
        familien.setdefault(((r.get("marke") or "").lower(), familie), set()).add(modell)

    vokabular: set[str] = set()
    for mitglieder in familien.values():
        if len(mitglieder) < 2:
            continue
        for modell in mitglieder:
            phrase = _variantenteil(modell)
            if not phrase:
                continue
            worte = phrase.split()
            if not any(len(w) >= _VARIANTE_MIN_LEN and not w.isdigit() for w in worte):
                continue
            if len(worte) == 1 and phrase not in karosserien:
                # Einwortige Kurzform ohne Karosserie-Bestätigung ("gt", "rs",
                # "life", "cross", "e-tron") -> kein belastbares Variantensignal.
                continue
            vokabular.add(phrase)
    return vokabular


_RE_MD_BILD = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_RE_MD_ZIEL = re.compile(r"\]\([^)\s]*\)")


def _varianten_zone(text: str) -> str:
    """Der Textbereich, in dem eine Fahrzeugvariante das EIGENE Fahrzeug belegen darf.

    Zugelassen sind eigener Titel/Heading und die eigene (von der Plattform bereits
    gekürzte) Kartenbeschreibung — beides nur innerhalb des strukturell isolierten
    Kartensegments. Entfernt werden Vorschaubild-Syntax und Link-Ziele: ein Slug wie
    "/s-anzeige/bmw-320d-gran-turismo/..." ist Adresse, kein Fahrzeugtext.

    Bewusst NICHT hier: Nachbarkarte, Seitenfuß, Empfehlungen, die globale Suchseite
    und die ungekürzte Detailseitenbeschreibung.
    """
    ohne_bild = _RE_MD_BILD.sub(" ", text or "")
    return _RE_MD_ZIEL.sub(" ", ohne_bild)


def _ist_fremdvariante(text: str, ziel: dict) -> str | None:
    """Belegt der EIGENE Kartentext eine Fahrzeugvariante, die nicht zum Ziel gehört?

    Spezifität entscheidet: "Gran Coupé" wird nicht dadurch erlaubt, dass "Coupé"
    erlaubt ist — die längere Phrase ist die genauere Aussage über das Fahrzeug.
    """
    fremd = ziel.get("fremd_varianten") or set()
    if not text or not fremd:
        return None
    erlaubt = ziel.get("ziel_varianten") or set()
    treffer = [p for p in fremd if _phrase_kommt_vor(p, text)]
    if not treffer:
        return None
    # Nur die maximalen Phrasen bewerten ("gran coupé" schlägt "coupé").
    maximal = [p for p in treffer if not any(p != q and p in q for q in treffer)]
    ziel_body = ziel.get("ziel_body_norm") or set()
    for phrase in sorted(maximal):
        if phrase in erlaubt:
            continue
        # Karosserie-Synonym des Ziels (Touring <-> Kombi) — aber nur, wenn die
        # gefundene Phrase nicht SPEZIFISCHER ist als eine erlaubte Variante.
        norm = _karosserie_im_text(phrase)
        if (norm and norm in ziel_body
                and not any(zv in phrase for zv in erlaubt if zv != phrase)):
            continue
        return phrase
    return None


def _ist_fremdmodell(worte: set[str], ziel: dict, text: str | None = None) -> str | None:
    """Zentrale, strukturierte Fremdmodell-Erkennung (alpha + marken-skopierte Zahl).

    Rückgabe: das erkannte Fremd-Token (Grund) oder None. Ein Text ist NUR dann fremd,
    wenn er ein Fremdmodell nennt und KEIN Zielmodell-Signal trägt. Für Zahlen zusätzlich
    marken-skopiert: die Zielmarke muss im Text stehen (kein markenübergreifender
    Zahlen-Fehlschluss wie Peugeot 508 vs BMW).
    """
    # §Variante vor Motor: eine eindeutige FREMDVARIANTE im eigenen, strukturell
    # isolierten Kartentext darf NICHT dadurch neutralisiert werden, dass die
    # Motorbezeichnung passt. "BMW 220d Gran Coupé" ist für ein 2er Coupé kein
    # Zielfahrzeug, auch wenn "220d" zur Zielbaureihe gehört. Ohne `text` (z.B. bei
    # der Quellenanzeige über modell_relevant) bleibt alles wie bisher.
    variante = _ist_fremdvariante(text, ziel) if text else None
    if variante:
        return variante

    # §Direct evidence: nennt das Inserat selbst ein anderes Modell? Braucht keine
    # DB-Liste — nur die Nutzerangabe und den eigenen Kartentext.
    widerspruch = _modell_widerspruch(text, ziel) if text else None
    if widerspruch:
        return widerspruch

    ziel_model = ziel.get("modell_tokens") or set()
    if worte & ziel_model:
        return None  # klares Zielsignal -> nie verwerfen
    # (a) Alpha-Fremdmodell (mokka, 5er, glc, passat, 520d …)
    alpha = worte & (ziel.get("fremd_modelle") or set())
    if alpha:
        return sorted(alpha)[0]
    # (b) Marken-skopierte Fremd-Zahl (BMW 520 im 320er-Check) — nur wenn die
    #     Zielmarke im Text steht und keine Zielzahl vorkommt.
    marke_tokens = ziel.get("marke_tokens") or set()
    ziel_num = ziel.get("ziel_num") or set()
    fremd_num = ziel.get("fremd_num") or set()
    if (marke_tokens & worte) and (fremd_num & worte) and not (ziel_num & worte):
        return sorted(fremd_num & worte)[0]
    return None


def _kraftstoff_im_text(text: str) -> str | None:
    """Normierter Kraftstoff aus einem Text, oder None.

    Wortgrenzen sind zwingend (siehe _KRAFTSTOFF_WORTE): ein Teilstring-Match
    fand "elektro" in "Elektron." und machte aus einem CDTI-Diesel ein E-Auto.
    """
    for norm, rx in _KRAFTSTOFF_RE:
        if rx.search(text or ""):
            return norm
    return None


def _karosserie_im_text(text: str) -> str | None:
    """Normierte Karosserieform aus einem Text ('Grand Sport' -> 'limousine',
    'Sports Tourer'/'Touring' -> 'kombi'), oder None wenn nicht belegbar (§5)."""
    for label, rx in _KAROSSERIE_RE:
        if rx.search(text or ""):
            return label
    if _insignia_st_kombi(text):
        return "kombi"
    return None


# ── "ST" — Opel-Insignia-Fachjargon, kein globales Karosseriewort (§ST-Fix) ───
# Live-Fund: Listing 3488368020 "Opel Insignia 2.0 CDTI Business Edition ST" blieb
# body=unknown und wurde dadurch nicht gegen die Ziel-Karosserie (Grand Sport /
# Limousine) abgewertet. "ST" IST bei Opel Insignia die Kurzform von "Sports
# Tourer" (Kombi) — aber bei anderen Marken eine Ausstattungs-/Leistungs-
# bezeichnung (Ford Focus ST, Fiesta ST, ST-Line) und dort KEIN Karosseriesignal.
#
# Deshalb kein Eintrag in `_KAROSSERIE_WORTE` (das gälte markenübergreifend),
# sondern eine eng kontextgebundene Zusatzregel: "ST" zählt nur als Kombi-Hinweis,
# wenn DERSELBE Text bereits "Insignia" nennt — "Insignia" ist als Modellname
# eindeutig genug, ohne dass "Opel" zusätzlich verlangt werden muss (reale
# Inserate lassen die Marke im Fließtext oft weg). "ST-Line"/"ST Line" (Ford-
# Ausstattungspaket) und "St." (Abkürzung, z.B. Ortsnamen wie "St. Wendel") sind
# ausdrücklich ausgenommen.
#
# §4: Diese Funktion bekommt ausschließlich den bereits isolierten Kartentext
# (`fenster`/`karte.text`) — dieselbe Textquelle, die auch der übrigen
# Karosserie-/Motor-/Kraftstoffprüfung zugrunde liegt. Kein Zugriff auf Nachbar-
# text, Seitentitel oder URL; ein Body-Leak aus der Nachbaranzeige ist damit
# strukturell ausgeschlossen — dieselbe Garantie, die _varianten_zone für die
# Motorprüfung liefert.
_RE_INSIGNIA_WORT = re.compile(r"(?<![a-zäöüß0-9])insignia(?![a-zäöüß])",
                               re.IGNORECASE)
_RE_ST_KUERZEL = re.compile(
    r"(?<![a-zäöüß0-9])st(?![a-zäöüß0-9])(?!\.)(?!-?\s*line)", re.IGNORECASE)


def _insignia_st_kombi(text: str) -> bool:
    """True, wenn der EIGENE Text 'ST' als Opel-Insignia-Sports-Tourer-Kürzel
    trägt — nur dann, wenn derselbe Text auch 'Insignia' nennt."""
    if not text or not _RE_INSIGNIA_WORT.search(text):
        return False
    return bool(_RE_ST_KUERZEL.search(text))


# ── §5: Such-Intent der Quellseite ───────────────────────────────────────────
# Eine Seite, deren Suchbegriff ein BAUTEIL statt eines Fahrzeugs benennt
# (kleinanzeigen.de/s-autos/g20-scheinwerfer/...), listet Teileangebote — deren
# Preise sind keine Fahrzeugmarktpreise. Sie liegen oft trotzdem über der
# Plausibilitätsuntergrenze und tragen Baujahr/Modell im Text, sind also für die
# Datenpunkt-Validierung unauffällig. Der Ausschluss muss deshalb auf SEITEN-Ebene
# passieren.
#
# Generisch gelöst: kein Fahrzeug- und kein Domain-Hardcoding, sondern eine
# Kategorie-Wortliste für Bauteile/Zubehör (analog zu _FINANZ_MARKERS). Bewusst
# NICHT enthalten sind mehrdeutige Wörter, die auch in echten Fahrzeuginseraten
# vorkommen ("motor", "getriebe", "sitze") — nur eindeutige Teile-Suchbegriffe
# sowie die klaren Sammelbegriffe.
_TEILE_INTENT_MARKER = (
    "ersatzteil", "gebrauchtteil", "autoteile", "zubehoer", "zubehör",
    "scheinwerfer", "ruecklicht", "rücklicht", "stossstange", "stoßstange",
    "kotfluegel", "kotflügel", "motorhaube", "auspuff", "katalysator",
    "turbolader", "einspritzduese", "einspritzdüse", "zylinderkopf",
    "steuerkette", "zahnriemen", "kupplung", "bremsscheibe", "bremsbelag",
    "stossdaempfer", "stoßdämpfer", "querlenker", "radlager", "lichtmaschine",
    "anlasser", "wasserpumpe", "kuehler", "kühler", "felge", "alufelge",
    "reifen", "kompletraeder", "kompletträder", "dachtraeger", "dachträger",
    "anhaengerkupplung", "anhängerkupplung", "navi", "steuergeraet", "steuergerät",
    "motorschaden", "austauschmotor", "teilespender", "schlachtfest",
)
_RE_TEILE_INTENT = re.compile(
    r"(?<![a-zäöüß])(?:" + "|".join(_TEILE_INTENT_MARKER) + r")", re.IGNORECASE)


def ist_teile_suchseite(url: str, titel: str | None = None) -> bool:
    """True, wenn Suchbegriff/Titel der Seite auf Ersatzteile oder Zubehör zielt
    statt auf ein Fahrzeug (§5). Solche Seiten liefern KEINE Marktbeobachtungen.

    Geprüft werden Pfad und Query der URL (dort steht bei Marktplätzen der
    Suchbegriff) sowie der Seitentitel. Der Domainname selbst wird bewusst
    ausgeklammert — sonst würde ein Teilehändler-Domainname eine ansonsten
    gültige Fahrzeugseite mitreißen.
    """
    pfad = ""
    try:
        p = urlparse(url or "")
        pfad = f"{p.path} {p.query}"
    except Exception:
        pfad = url or ""
    return bool(_RE_TEILE_INTENT.search(pfad) or _RE_TEILE_INTENT.search(titel or ""))


def _eindeutige_karosserie(text: str) -> str | None:
    """Karosserieform NUR, wenn der Text genau EINE Form nennt (§5).

    Für Signale auf SEITEN-Ebene (URL + Titel einer Trefferliste) gedacht: eine nach
    Kombi gefilterte Suchseite beschreibt jede Karte darauf als Kombi, auch wenn die
    einzelne Karte ihre Karosserie nicht wiederholt. Nennt die Seite mehrere Formen
    ("Grand Sport und Sports Tourer"), gilt das Signal als mehrdeutig und wird
    verworfen — lieber kein Karosseriesignal als ein falsches.
    """
    treffer = {label for label, rx in _KAROSSERIE_RE if rx.search(text or "")}
    return next(iter(treffer)) if len(treffer) == 1 else None


def _getriebe_im_text(text: str) -> str | None:
    t = f" {(text or '').lower()} "
    for label, keys in _GETRIEBE_WORTE:
        if any(k in t for k in keys):
            return label
    return None


def _ps_im_text(text: str) -> int | None:
    m = _RE_PS.search(text or "")
    if not m:
        return None
    ps = int(m.group(1))
    return ps if 30 <= ps <= 1500 else None


# Verkaufsbezeichnung im Listing-Text: optionaler Buchstabenpräfix, DREI Ziffern,
# optionaler Buchstabensuffix — und mindestens ein Buchstabe insgesamt. Trifft
# "320d", "330i", "m340i", "530e"; trifft bewusst NICHT "360" (Kameraausstattung),
# "2019" (Baujahr), "b47d20" (Motorcode) oder "19zoll".
_RE_VERKAUFSBEZEICHNUNG = re.compile(r"^(?=.*[a-z])[a-z]{0,2}\d{3}[a-z]{0,2}$")


# Kurzer Modellcode: Buchstabenpräfix + Ziffern als VOLLSTÄNDIGER Token
# ("A3", "A4", "Q5", "S3", "RS3"). Diese Form ist ein echter Modellname, fällt aber
# durch die Längenschwelle von `_modell_tokens` (>= 3 Zeichen).
#
# Bewusst wird eine ZIFFER verlangt: rein alphabetische Kürzel wie "GT", "RS", "ST"
# oder "M" sind Ausstattungs-/Karosseriezusätze und dürfen NIE zum Modellanker
# werden (Ford Focus ST, Insignia ST, BMW GT). Die Ziffer ist genau das Merkmal,
# das eine Modellfamilie von einem Kürzel trennt.
_RE_MODELLCODE_KURZ = re.compile(r"^[a-z]{1,3}\d{1,2}$")


def _modell_anker(marke: str, modell: str) -> set[str]:
    """Belastbarer POSITIVER Modellanker aus einem Marke/Modell-Paar (§6).

    "Opel" + "Insignia Grand Sport" -> {'insignia'}, "BMW" + "320d G20" -> {'320d'},
    "Audi" + "A3 8V" -> {'a3'}, "Audi" + "RS3" -> {'rs3'}.

    Drei Stufen, von spezifisch nach allgemein:

      1. STRUKTURIERTE Bezeichnungen ab 3 Zeichen, die Buchstabe UND Ziffer
         kombinieren ("320d", "c200", "w205"). Sie sind per Konstruktion
         trennscharf; bei mehreren gewinnt die längste.
      2. KURZE Modellcodes ("A3", "Q5", "S3"). Sie fallen durch die
         Längenschwelle von `_modell_tokens`, sind aber vollwertige Modellnamen.
         Ohne diese Stufe hatte ein Audi-Ziel GAR KEINEN direkten Anker — in der
         Fahrzeugmatrix übernahm dann ein zufälliger Varianten-Token aus der DB
         ('rs3' für A3, 'rs4' für A4) und verwarf jedes korrekte Inserat.
      3. Sonst der LÄNGSTE Wort-Token. Zusätze wie "Grand", "Sport" oder "Avant"
         beschreiben Karosserie bzw. Ausstattung und stehen auch in fremden
         Inseraten; der längste Token ist regelmäßig der eigentliche Modellname.

    Markenwörter fallen immer heraus ("Opel Insignia" -> nur 'insignia'), sonst
    gälte jede Anzeige derselben Marke als Treffer.

    Bewusst KEIN Fremdmodell-Lexikon: gesucht wird ausschließlich eine POSITIVE
    Übereinstimmung. Lässt sich kein Anker ableiten, bleibt die Menge leer — dann
    wird kein Match erfunden, und das Sicherheitsgate in `_bewerte` schließt.
    """
    marken = _marke_tokens(marke or "")
    alle = _wort_tokens(modell or "") - marken

    strukturiert = {t for t in alle if len(t) >= 3
                    and any(c.isdigit() for c in t) and any(c.isalpha() for c in t)}
    if strukturiert:
        laenge = max(len(t) for t in strukturiert)
        return {t for t in strukturiert if len(t) == laenge}

    # In TEXTREIHENFOLGE, nicht als Menge: reale Eingaben nennen erst das Modell,
    # dann die Generation ("A4 B9", "A3 8V"). Beide haben dieselbe Kurzform, nur
    # der erste Treffer ist der Modellname — sonst geriete "b9" als Anker mit
    # hinein und verlangte den Generationscode auf jeder Karte.
    for t in re.split(r"[^a-z0-9]+", (modell or "").lower()):
        if t and t not in marken and _RE_MODELLCODE_KURZ.match(t):
            return {t}

    tokens = _modell_tokens(modell or "") - marken
    if not tokens:
        return set()
    laenge = max(len(t) for t in tokens)
    return {t for t in tokens if len(t) == laenge}


def _modell_kennungen_user(req, gen_tokens: set[str] | None = None) -> dict:
    """Strukturierte Familienkennung AUS DER NUTZEREINGABE — ohne DB.

    Liefert `praefix` (Mercedes-Stil: "C 200 d" -> "c") und/oder `familie`
    (Audi-Stil: "A4" -> ("a", "4")). Fehlt beides, ist kein Modellvergleich
    beweisbar und es wird nie hart abgelehnt.
    """
    roh = " ".join(str(getattr(req, f, "") or "") for f in
                   ("modell", "motor", "generation", "inserat_titel"))
    teile = [t for t in re.split(r"[^a-z0-9]+", roh.lower()) if t]
    # Ein Generationscode hat dieselbe Form wie eine Mercedes-Kennung ("G20" sieht
    # aus wie "C200"). Er darf keine Modellfamilie definieren — sonst gälte beim
    # BMW-Ziel plötzlich das Präfix "g", und jeder fremde Code ("F30", "M340i")
    # wäre ein Modellwiderspruch.
    gesperrt = set(gen_tokens or ())
    out: dict = {}
    for kandidat in _kennungen(teile):
        if kandidat in gesperrt:
            continue
        m = _RE_KENNUNG_PRAEFIX.match(kandidat)
        if m and "praefix" not in out:
            out["praefix"] = m.group(1)
        k = _RE_FAMILIE_KURZ.match(kandidat)
        if k and "familie" not in out:
            out["familie"] = (k.group(1), k.group(2))
    return out


def _modell_evidenz_user(req) -> set[str]:
    """Modell-/Familienbezeichner AUS DER NUTZEREINGABE — ohne jede DB-Ergänzung.

    Zusätzlich zu den Einzelwörtern werden benachbarte Buchstaben-/Ziffernpaare
    verschmolzen: Verkäufer schreiben "C200", das Formular oft "C 200 d". Ohne diese
    Normalisierung gälte die eigene C-Klasse als fremdes Modell.
    """
    roh = " ".join(str(getattr(req, f, "") or "") for f in
                   ("modell", "motor", "generation", "karosserie", "inserat_titel"))
    teile = [t for t in re.split(r"[^a-z0-9]+", roh.lower()) if t]
    out = {t for t in teile if len(t) >= 2 and re.search(r"[a-z]", t)}
    for i, t in enumerate(teile[:-1]):
        if t.isalpha() and len(t) <= 3 and teile[i + 1].isdigit():
            fusion = t + teile[i + 1]
            out.add(fusion)
            if i + 2 < len(teile) and teile[i + 2].isalpha() and len(teile[i + 2]) <= 2:
                out.add(fusion + teile[i + 2])
    return out


# Verkaufsbezeichnung mit Buchstaben-PRÄFIX (Mercedes "C200", "GLC220d",
# "E220d"): Präfix = Modellfamilie, Zahl+Rest = Motorisierung.
_RE_KENNUNG_PRAEFIX = re.compile(r"^([a-z]{1,3})(\d{2,3})([a-z]*)$")
# Kurzform-Familie mit ein- bis zweistelliger Zahl (Audi "A4"/"A6", "Q5", "X3").
_RE_FAMILIE_KURZ = re.compile(r"^([a-z]{1,3})(\d{1,2})$")


def _kennungen(teile: list[str]) -> list[str]:
    """Tokens plus verschmolzene Buchstaben/Ziffern-Paare ("glc"+"220" -> "glc220")."""
    out = list(teile)
    for i, t in enumerate(teile[:-1]):
        if t.isalpha() and len(t) <= 3 and teile[i + 1].isdigit():
            fusion = t + teile[i + 1]
            out.append(fusion)
            if i + 2 < len(teile) and teile[i + 2].isalpha() and len(teile[i + 2]) <= 2:
                out.append(fusion + teile[i + 2])
    return out


def _modell_widerspruch(text: str, ziel: dict) -> str | None:
    """Nennt das Inserat SELBST eine andere Modellfamilie als der Nutzer?

    BEWEIS statt Vermutung: ein Hard-Reject entsteht nur, wenn Nutzerangabe UND
    Inserat beide eine strukturierte Familienkennung tragen und diese sich
    widersprechen — "C200d" gegen "GLC220d" (Präfix c vs glc), "A4" gegen "A6"
    (gleiches Präfix, andere Zahl). Die gemeinsamen Motorbestandteile ("220", "40",
    "TDI", "d") entscheiden dabei nie.

    Alles andere bleibt UNKNOWN. Ein unbekanntes Token hinter der Marke ist KEIN
    Fremdmodell: "BMW **Limousine** Advantage" (Karosserie), "BMW **3GT**"
    (Variante) und "Opel **Mokka**" (reiner Name) sind strukturell nicht
    unterscheidbar. Ohne verifiziertes Modelllexikon wird hier nichts geraten —
    lieber ein False Negative als ein falscher Ausschluss.
    """
    marken = ziel.get("marke_tokens") or set()
    ziel_kenn = ziel.get("modell_kennungen_user") or {}
    if not marken or not ziel_kenn or not text:
        return None
    teile = [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]
    for i, t in enumerate(teile):
        if t not in marken:
            continue
        rest = [x for x in teile[i + 1:i + 4] if x not in marken]
        for kandidat in _kennungen(rest)[:4]:
            m = _RE_KENNUNG_PRAEFIX.match(kandidat)
            if m and "praefix" in ziel_kenn:
                if m.group(1) != ziel_kenn["praefix"]:
                    return kandidat
                return None
            k = _RE_FAMILIE_KURZ.match(kandidat)
            if k and "familie" in ziel_kenn:
                zp, zn = ziel_kenn["familie"]
                if k.group(1) == zp and k.group(2) != zn:
                    return kandidat
                if k.group(1) == zp:
                    return None
        return None
    return None


# Zahlenangabe mit Einheit: "174cv", "194ps", "125kw", "000km" haben exakt die
# FORM einer Verkaufsbezeichnung. Der Live-Audit (Opel Insignia B) hat 28 harte
# Fehlablehnungen allein hierdurch belegt — darunter "174cv", also die ZIEL-
# leistung in spanischer Schreibweise.
_RE_MESSWERT = re.compile(r"\d(ps|kw|km|cv|hp|ch|nm|kg|ccm|cm|mm)$")


def _fremde_bezeichnungen_im_text(text: str, ziel_motor: set[str]) -> set[str]:
    """Motorbezeichnungen, die der SICHTBARE EIGENE Kartentext nennt und die nicht
    zum Ziel gehören.

    DIREKTE Evidenz: Nutzerangabe gegen Inseratstext. Kommt ohne die DB-Liste aller
    Motorvarianten aus — ein "330d" im eigenen Kartentext widerspricht dem Ziel
    "320d" auch dann, wenn unsere Fahrzeug-DB gar nicht weiß, dass es einen 330d
    gibt. Greift naturgemäß nur bei zusammengeschriebenen Bezeichnungen; bei
    "C 220 d" übernehmen weiterhin Kraftstoff und Leistung.

    §Rauschen (Live-Audit Insignia B, 57 von 95 harten Motor-Ablehnungen): Dass ein
    Token die FORM einer Verkaufsbezeichnung hat, ist KEIN Beweis. Drei Filter
    machen daraus einen:

      1. Nur die Variantenzone — Vorschaubild-Syntax und Link-Ziele sind entfernt.
         Kleinanzeigen bettet in jede Karte eine Bild-URL mit UUID ein; deren
         Hex-Gruppen ("928c", "443d", "b803") haben exakt die Form "320d" und
         standen für 29 harte Fehlablehnungen.
      2. Keine Messwerte mit Einheit (_RE_MESSWERT).
      3. Strukturelle Vergleichbarkeit: gleiche Ziffernzahl wie die Nutzer-
         Verkaufsbezeichnung. "320d" gegen "330d" ist ein Vergleich, "320d" gegen
         "12x" oder "1234e" ist keiner.
    """
    if not ziel_motor or not text:
        return set()
    ziffern = {sum(c.isdigit() for c in t) for t in ziel_motor}
    treffer: set[str] = set()
    for w in _wort_tokens(_varianten_zone(text)):
        if not _RE_VERKAUFSBEZEICHNUNG.match(w) or _RE_MESSWERT.search(w):
            continue
        if sum(c.isdigit() for c in w) not in ziffern:
            continue
        treffer.add(w)
    return treffer - ziel_motor


def _motor_tokens(bezeichnung: str) -> set[str]:
    """Trennscharfe Motor-Verkaufsbezeichnungen einer Motorvariante (§5).

    NUR Token, die Ziffer UND Buchstabe kombinieren ('320d', '330i', '530e'), sind
    zwischen den Motorvarianten DERSELBEN Baureihe unterscheidungskräftig. Reine
    Technik-Kürzel ('tdi', 'cdti', 'diesel') teilen sich alle Varianten einer
    Baureihe — als Fremdsignal wären sie unbrauchbar und würden korrekte Angebote
    fälschlich verwerfen. Ist danach nichts übrig (z.B. Opel-Bezeichnungen wie
    '2.0 Diesel'), bleibt die Motor-Token-Prüfung für dieses Fahrzeug ehrlich
    INAKTIV — die Motorabgrenzung läuft dann über Kraftstoff und Leistung.
    """
    out: set[str] = set()
    for t in re.split(r"[^a-z0-9]+", (bezeichnung or "").lower()):
        if len(t) >= 3 and re.search(r"\d", t) and re.search(r"[a-z]", t):
            out.add(t)
    return out


def _jahr_aus_text(text: str) -> int | None:
    """Erstes plausibles Modelljahr in einem Freitext (z.B. Facelift-Beschreibung)."""
    for m in _RE_JAHR.finditer(text or ""):
        j = int(m.group(1))
        if _JAHR_MIN <= j <= _JAHR_MAX:
            return j
    return None


def _kanonische_url(url: str) -> str:
    """Domain + Pfad ohne Query/Fragment/Trailing-Slash — stabile Identität einer
    Detailseite (§4). Query-Parameter sind bei Portalen häufig Tracking-Beiwerk
    und würden dasselbe Inserat sonst mehrfach zählen."""
    try:
        p = urlparse(url or "")
    except Exception:
        return (url or "").strip().lower()
    netloc = p.netloc.lower().removeprefix("www.")
    return f"{netloc}{p.path.rstrip('/')}".lower()


# Detail-Link innerhalb eines Kartentexts (§3). Tavilys `raw_content` liefert
# Seiteninhalte teils als Markdown — dann steht der Link der Fahrzeugkarte als
# [Titel](https://…/s-anzeige/…) oder als nackte URL im Kartentext, obwohl das
# Suchergebnis selbst nur die Trefferlisten-URL trug.
_RE_MD_LINK = re.compile(r"\[[^\]]*\]\((https?://[^\s)]+)\)")
_RE_NACKTE_URL = re.compile(r"https?://[^\s)\]]+")
# Anzeigen-ID als Freitext ("Anzeigen-ID: 2812345678", "Art.-Nr. 123456789").
_RE_ANZEIGEN_ID = re.compile(
    r"(?:anzeige[nr]?[-\s]?(?:id|nr\.?)|inserat[-\s]?(?:id|nr\.?)|art\.?[-\s]?nr\.?)"
    r"\s*[:.]?\s*(\d{6,})", re.IGNORECASE)


def _karten_identitaet(kartentext: str) -> tuple[str | None, str | None]:
    """Sucht im ISOLIERTEN Kartentext nach einer stabilen Inseratskennung (§3).

    Rückgabe: (detail_url, listing_id) — beides optional. Es wird ausschließlich
    der bereits von den Nachbarkarten getrennte Textausschnitt durchsucht, damit
    nicht der Link des Nachbarangebots übernommen wird.
    """
    if not kartentext:
        return None, None
    treffer = _RE_MD_LINK.search(kartentext) or _RE_NACKTE_URL.search(kartentext)
    detail_url = None
    if treffer:
        kandidat = treffer.group(1) if treffer.lastindex else treffer.group(0)
        # Nur echte Detailseiten übernehmen — ein Link auf die Trefferliste, eine
        # Kategorie oder ein Bild-CDN ist keine Inseratsidentität.
        if _ist_einzelinserat(kandidat):
            detail_url = kandidat
    m = _RE_ANZEIGEN_ID.search(kartentext)
    listing_id = m.group(1) if m else (_listing_id_aus_url(detail_url) if detail_url else None)
    return detail_url, listing_id


def _karten_hash(kartentext: str) -> str:
    """Stabiler Hash über den KOMPLETTEN isolierten Kartentext (§3).

    Fällt an, wenn weder Detail-Link noch Anzeigen-ID auffindbar sind. Deutlich
    tragfähiger als Preis+Baujahr+Kilometer allein, weil Titel, Ausstattungs- und
    Ortsangaben der Karte mit einfließen — zwei zufällig preis-, jahr- und
    kilometergleiche Fahrzeuge werden dadurch nicht mehr verschmolzen.

    Normalisiert wird auf Kleinschreibung und einfache Leerzeichen, damit
    Formatierungsunterschiede derselben Karte nicht zu zwei Identitäten führen.
    """
    norm = re.sub(r"\s+", " ", (kartentext or "").strip().lower())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


# ── Kleinanzeigen-Anzeigenkategorie aus der eigenen Detail-URL ───────────────
# Kleinanzeigen kodiert die Rubrik im letzten Pfadsegment der Detailseite:
#     /s-anzeige/<slug>/<listing-id>-<kategorie>-<n>
# Über alle gespeicherten Karten hinweg (Audit der Edge Cases): 159x "-216-"
# (Autos) und 8x "-223-" (Auto-Teile & Reifen), keine Ausnahme. Damit lässt sich
# eine Teile-/Zubehöranzeige an einem PLATTFORMDATUM erkennen statt an Wörtern wie
# "Felgen", "Motor" oder "RaceChip" — die im Text eines vollständigen Fahrzeugs
# genauso legitim vorkommen.
#
# Bewusst als DENYLIST: verworfen wird nur, was nachweislich KEINE Fahrzeugrubrik
# ist. Eine unbekannte Kategorie bleibt zugelassen — Kleinanzeigen führt weitere
# Fahrzeugrubriken (Motorräder, Wohnmobile, Nutzfahrzeuge), die in unseren Daten
# nicht vorkamen. Eine Allowlist würde sie stillschweigend mit ausschließen.
_KLEINANZEIGEN_HOSTS = ("kleinanzeigen.de", "ebay-kleinanzeigen.de")
_KLEINANZEIGEN_NICHT_FAHRZEUG = {
    "223": "Auto-Teile & Reifen",
}
# Nur der bekannte finale Detailpfad — keine beliebige Zahl irgendwo in der URL.
_RE_KLEINANZEIGEN_DETAIL = re.compile(
    r"/s-anzeige/[^/]+/(\d{6,})-(\d{1,4})-(\d+)/?$")


def kleinanzeigen_kategorie(url: str) -> str | None:
    """Rubrik-ID einer Kleinanzeigen-Detailseite, oder None.

    Greift nur bei Kleinanzeigen — absolut (mit Host) oder wurzel-relativ, wie die
    Links in den Trefferlisten vorkommen. Bei jeder anderen Domain bewusst None,
    damit eine zufällig ähnlich aufgebaute Fremd-URL nicht fehlinterpretiert wird.
    """
    if not url:
        return None
    try:
        p = urlparse(url)
    except Exception:
        return None
    if p.netloc:
        host = p.netloc.lower().split(":")[0]
        if not any(host == h or host.endswith("." + h) for h in _KLEINANZEIGEN_HOSTS):
            return None
    elif not url.startswith("/s-anzeige/"):
        # Ohne Host ist nur der bekannte wurzel-relative Detailpfad eindeutig.
        return None
    treffer = _RE_KLEINANZEIGEN_DETAIL.search(p.path or "")
    return treffer.group(2) if treffer else None


def nicht_fahrzeug_rubrik(url: str) -> str | None:
    """Belegt die eigene Detail-URL eine Rubrik, die KEIN Fahrzeug anbietet?
    Rückgabe: Klartextname der Rubrik, sonst None."""
    return _KLEINANZEIGEN_NICHT_FAHRZEUG.get(kleinanzeigen_kategorie(url) or "")


def _listing_id_aus_url(url: str) -> str | None:
    """Längste ID-artige Ziffernfolge (>=6 Stellen) aus Pfad/Query — die
    Inserats-ID der gängigen Portale (§4). None, wenn keine vorhanden."""
    if not url:
        return None
    try:
        p = urlparse(url)
        rest = f"{p.path}?{p.query}"
    except Exception:
        rest = url
    treffer = _RE_LISTING_ID.findall(rest)
    if not treffer:
        return None
    return max(treffer, key=len)


def _ist_finanzierungspreis(fenster: str) -> bool:
    """True, wenn das Preisumfeld einen Finanzierungs-/Leasing-/Monats- oder
    Neuwagenpreis-Marker enthält (§9) — dann ist der Betrag KEIN belastbarer
    Gebraucht-Gesamtpreis und wird nicht als Marktbeobachtung gezählt."""
    t = fenster.lower()
    return any(m in t for m in _FINANZ_MARKERS)


def _naechster(treffer: list[re.Match], pos: int) -> re.Match | None:
    """Der zum Preis (an Position `pos`) nächstgelegene Treffer innerhalb _FENSTER."""
    best = None
    best_dist = _FENSTER + 1
    for m in treffer:
        d = abs(m.start() - pos)
        if d <= _FENSTER and d < best_dist:
            best, best_dist = m, d
    return best


def _extrahiere_aus_text(text: str, url: str, source_type: str = "unknown",
                         *, grenzen: tuple[int, int] | None = None,
                         seiten_body: str | None = None) -> list[Preisbeobachtung]:
    """Alle (Preis[, km][, Baujahr])-Datenpunkte aus EINEM Snippet-Text.

    Assoziation rein positionsbasiert: km/Baujahr werden nur übernommen, wenn sie
    innerhalb eines engen Fensters um den Preis stehen. Ordnung/Reihenfolge der
    Felder variiert je Portal — das enge Fenster hält Fehlzuordnungen klein; im
    Zweifel bleibt ein Feld None (senkt später die Vergleichbarkeit).

    `source_type` (§10-§13): "listing" | "category" | "unknown" — wird unverändert an
    jede extrahierte Beobachtung durchgereicht (bestimmt später, ob sie Richtung
    HIGH/Quellenvielfalt zählen darf).

    `grenzen` (§3, optional): (titel_ende, content_ende) als Zeichenpositionen im
    zusammengesetzten Text — daraus wird je Datenpunkt `extraction_source`
    ("title" | "snippet" | "raw_content") bestimmt. Ohne Angabe gilt "snippet".

    `seiten_body` (§5, optional): eindeutige Karosserieform der QUELLSEITE (aus URL
    und Titel). Eine nach Kombi gefilterte Trefferliste beschreibt jede Karte darauf
    als Kombi — die einzelne Karte wiederholt das meist nicht. Wird nur als
    Rückfall verwendet, wenn die Karte selbst keine Karosserie nennt.
    """
    if not text:
        return []
    km_treffer = list(_RE_KM.finditer(text))
    ez_treffer = list(_RE_EZ.finditer(text))
    bj_treffer = list(_RE_BJ.finditer(text))
    jahr_treffer = list(_RE_JAHR.finditer(text))
    domain = _domain(url)

    preis_treffer = list(_RE_PREIS.finditer(text))
    # §3: Merkmals-Segmente. Steht eine Portal-Trefferliste in EINEM Snippet
    # ("… 320d 24.900 € 118.000 km . 320i 27.900 € 119.000 km …"), dann reicht ein
    # symmetrisches ±_FENSTER-Umfeld über die Nachbaranzeige hinweg — die
    # Motorbezeichnung des Nachbarn landet im eigenen Umfeld und macht die harte
    # Motor-/Kraftstoff-/Karosserie-Prüfung (§5) wirkungslos. Die Merkmale EINES
    # Angebots liegen zwischen den beiden benachbarten Preisen; deshalb wird das
    # Merkmalsfenster zusätzlich an der Mitte zum Vor- und Nachbarpreis
    # abgeschnitten. Die km-/Baujahr-Zuordnung bleibt davon unberührt (sie nutzt
    # weiterhin das positionsnächste Vorkommen im vollen ±_FENSTER, siehe
    # _naechster) — dieses Verhalten ist empirisch kalibriert und trägt die
    # Attributvollständigkeit, die für HOCH nötig ist.
    grenzen_preise = [m.start() for m in preis_treffer]

    def _merkmalsfenster(idx: int, pos: int) -> str:
        start = max(0, pos - _FENSTER)
        ende = min(len(text), pos + _FENSTER)
        if idx > 0:
            start = max(start, (grenzen_preise[idx - 1] + pos) // 2)
        if idx + 1 < len(grenzen_preise):
            ende = min(ende, (pos + grenzen_preise[idx + 1]) // 2)
        return text[start:ende]

    # ── Strukturelle Kartensegmentierung (Vorrang vor dem Zeichenfenster) ────
    # Zuerst versucht der Segmenter, echte Fahrzeugkarten abzugrenzen. Gelingt das,
    # stammen Preis, Kilometerstand UND Baujahr eines Datenpunkts garantiert aus
    # DERSELBEN Karte — kein Attribut kann mehr vom Nachbarinserat stammen. Gelingt
    # es nicht, springt das alte Zeichenfenster ein; solche Punkte werden später
    # hart auf "bedingt" gedeckelt (§1/§5, siehe _bewerte).
    karten, _verfahren = segmentiere(text, url, titel_ende=(grenzen[0] if grenzen else 0))
    karte_je_preis: dict[int, object] = {k.preis_offset: k for k in karten}

    out: list[Preisbeobachtung] = []
    for idx, pm in enumerate(preis_treffer):
        preis = _zahl(pm.group(1))
        if not (_PREIS_MIN <= preis <= _PREIS_MAX):
            continue
        pos = pm.start()
        karte = karte_je_preis.get(pos)

        if karte is not None:
            # Attribute AUSSCHLIESSLICH aus der eigenen Karte lesen.
            km = next((k for k in (_zahl(m.group(1)) for m in _RE_KM.finditer(karte.text))
                       if 0 <= k <= _KM_MAX), None)
            jahr = None
            for rx in (_RE_EZ, _RE_BJ, _RE_JAHR):
                jahr = next((int(m.group(1)) for m in rx.finditer(karte.text)
                             if _JAHR_MIN <= int(m.group(1)) <= _JAHR_MAX), None)
                if jahr is not None:
                    break
            fenster = karte.text
        else:
            km_m = _naechster(km_treffer, pos)
            km = None
            if km_m:
                k = _zahl(km_m.group(1))
                if 0 <= k <= _KM_MAX:
                    km = k

            jahr = None
            for treffer in (ez_treffer, bj_treffer, jahr_treffer):   # zuverlässigste Quelle zuerst
                jm = _naechster(treffer, pos)
                if jm:
                    y = int(jm.group(1))
                    if _JAHR_MIN <= y <= _JAHR_MAX:
                        jahr = y
                        break

            # Lokales Merkmalsfenster (Generation/Motor/Kraftstoff/Karosserie/Leistung).
            fenster = _merkmalsfenster(idx, pos)
        # §9: Finanzierungs-/Leasing-/Monats-/Neuwagenpreise nicht als Marktbeobachtung.
        if _ist_finanzierungspreis(fenster):
            continue
        out.append(_roh_beobachtung(preis, km, jahr, domain, url, fenster, source_type,
                                    _extraction_source(pos, grenzen), seiten_body,
                                    karte, pos))
    return out


def _extraction_source(pos: int, grenzen: tuple[int, int] | None) -> str:
    """Aus welchem Abschnitt des zusammengesetzten Treffertexts der Datenpunkt stammt."""
    if not grenzen:
        return "snippet"
    titel_ende, content_ende = grenzen
    if pos < titel_ende:
        return "title"
    if pos < content_ende:
        return "snippet"
    return "raw_content"


# ── Karosserie-Provenance (§Listing-Evidence) ────────────────────────────────
# Der Suchseiten-Kontext (URL-Filter "autos.typ_s:limousine", Seitentitel) beschreibt
# die TREFFERLISTE, nicht das einzelne Inserat. Er darf deshalb keine Listing-
# Identität setzen. Der forensische Audit hat den Schaden belegt: ein BMW 320d GT
# (3GT/F34) lag auf einer nach "Limousine" gefilterten Seite, erbte daraus
# body=limousine, wurde über die Chassiscode-Zuordnung zu G20 inferiert und war als
# einziger "sehr ähnlich"-Treffer der stärkste Anker des gesamten Medians.
_BODY_EVIDENCE_VERTRAUT = ("card", "detail")


def _body_mit_provenance(fenster: str, seiten_body: str | None, strukturell: bool,
                         ist_detailseite: bool) -> dict:
    """Karosserie samt Herkunft. Der Wert bleibt erhalten — die Herkunft entscheidet,
    wer ihn als Identität verwenden darf."""
    eigen = _karosserie_im_text(fenster)
    if eigen:
        if ist_detailseite:
            return {"body": eigen, "body_evidence": "detail"}
        if strukturell:
            return {"body": eigen, "body_evidence": "card"}
        # Ohne Kartengrenze ist das Fenster kein Inserat — der Wert kann aus einer
        # Nachbaranzeige stammen und taugt nicht als Identität.
        return {"body": eigen, "body_evidence": "window_fallback"}
    if seiten_body:
        # Ist die Seite SELBST das Inserat, beschreiben URL und Titel dieses eine
        # Fahrzeug — dann ist der Wert listing-eigen.
        return {"body": seiten_body,
                "body_evidence": "detail" if ist_detailseite else "page_context"}
    return {"body": None, "body_evidence": "unknown"}


def _identitaets_body(b: Preisbeobachtung) -> str | None:
    """Die Karosserie, soweit sie als LISTING-EIGENE Evidence gelten darf.

    Alles andere (Suchseiten-Kontext, bloßes Zeichenfenster) bleibt als Kontext am
    Datenpunkt sichtbar, zählt hier aber als unbekannt.
    """
    return b.body if b.body_evidence in _BODY_EVIDENCE_VERTRAUT else None


# Roh-Beobachtung trägt das lokale Textfenster vorübergehend in `gruende[0]` mit,
# damit die Vergleichbarkeits-Bewertung darauf zugreifen kann (wird dort entfernt).
def _roh_beobachtung(preis, km, jahr, domain, url, fenster, source_type="unknown",
                     extraction_source="snippet", seiten_body=None,
                     karte=None, preis_pos=None) -> Preisbeobachtung:
    # §3: aus dem Preisumfeld belegbare Fahrzeugmerkmale mitführen — ausschließlich
    # das, was wirklich im Text steht. Ziel-abhängige Felder (make/model/generation/
    # engine_variant) setzt erst _bewerte(), weil dafür das Zielprofil nötig ist.
    # §3: Identität in absteigender Belastbarkeit — (1) Anzeigen-ID, (2) kanonische
    # Detail-URL, (3) Hash über den kompletten isolierten Kartentext. Die Stufen 1
    # und 2 werden zuerst aus dem KARTENTEXT gesucht (Tavilys raw_content trägt bei
    # Trefferlisten häufig den Detail-Link jeder Karte mit, auch wenn das
    # Suchergebnis selbst nur die Listen-URL hatte) und erst danach aus der
    # Seiten-URL, sofern die Seite selbst ein Einzelinserat ist.
    strukturell = karte is not None
    karten_url, karten_id = _karten_identitaet(fenster)
    if strukturell:
        karten_url = karte.detected_detail_url or karten_url
        karten_id = karte.detected_listing_id or karten_id
    ist_detailseite = source_type == "listing"
    detail_url = karten_url or (url if ist_detailseite else None)
    listing_id = karten_id or (_listing_id_aus_url(url) if ist_detailseite else None)
    if listing_id and domain:
        listing_key = f"id:{domain}:{listing_id}"
    elif detail_url:
        listing_key = f"url:{_kanonische_url(detail_url)}"
    elif strukturell:
        # §4: Hash NUR über eine strukturell bestätigte Karte. Über ein bloßes
        # Zeichenfenster wäre er eine Identität für möglicherweise vermischten Text
        # — also eine Scheingenauigkeit.
        listing_key = f"card:{_karten_hash(fenster)}"
    else:
        # Ohne strukturelle Abgrenzung bleibt nur der schwache Fahrzeug-
        # Fingerabdruck. Er reicht als Dublettenbremse, nicht als Identität.
        listing_key = f"v:{preis}:{km}:{jahr}"
    return Preisbeobachtung(
        preis_eur=preis, kilometerstand=km, baujahr=jahr,
        quelle_domain=domain, quelle_url=url, source_type=source_type,
        listing_key=listing_key, listing_id=listing_id, detail_url=detail_url,
        **_body_mit_provenance(fenster, seiten_body, strukturell, ist_detailseite),
        fuel=_kraftstoff_im_text(fenster),
        horsepower=_ps_im_text(fenster),
        transmission=_getriebe_im_text(fenster),
        # §5: Stammt der Punkt nur aus einem Zeichenfenster, wird das an der
        # Herkunft sichtbar gemacht — nicht hinter einer Abschnittsangabe versteckt.
        extraction_source=extraction_source if strukturell else "window_fallback",
        segmentation_method=(karte.segmentation_method if strukturell else "window_fallback"),
        structural_confidence=(karte.structural_confidence if strukturell else "low"),
        start_offset=(karte.start if strukturell else
                      (max(0, preis_pos - _FENSTER) if preis_pos is not None else None)),
        end_offset=(karte.end if strukturell else
                    (preis_pos + _FENSTER if preis_pos is not None else None)),
        window_fallback_used=not strukturell,
        vergleichbarkeit="", gruende=[f"\x00{fenster}"],
    )


def _inferiere_generation(chassis_codes: dict[str, str],
                          body: str | None) -> tuple[str | None, str | None]:
    """Leitet den Chassiscode aus der Karosserie ab — nur bei EINDEUTIGKEIT.

    `chassis_codes` ist die bereits normalisierte Zuordnung der Baureihenfamilie
    ({"g20": "limousine", "g21": "kombi"}), `body` die normalisierte Karosserie des
    Inserats. Rückgabe: (code, Begründung) oder (None, None).

    Bewusst streng: passt kein oder mehr als ein Code, wird NICHT geraten. Zwei
    Codes können auf dieselbe normalisierte Karosserie fallen (etwa "Coupé" und
    "Gran Coupé"); dann ist die Karosserie schlicht kein taugliches
    Unterscheidungsmerkmal für diese Familie.
    """
    if not chassis_codes or not body:
        return None, None
    passend = sorted(code for code, karo in chassis_codes.items() if karo == body)
    if len(passend) != 1:
        return None, None
    code = passend[0]
    familie = "/".join(c.upper() for c in sorted(chassis_codes))
    return code, (f"Baureihenfamilie {familie}; Karosserie {body} ist innerhalb der "
                  f"hinterlegten Code-Zuordnung eindeutig {code.upper()}.")


def _km_fenster(ziel_km: int) -> tuple[float, float, float]:
    """Relative Kilometer-Fenster (§7) um den Zielwert: (sehr ähnlich, ähnlich,
    bedingt) als absolute Toleranz in km.

    Prozentual statt starr absolut, weil dieselbe absolute Abweichung bei 30.000 km
    ein anderes Fahrzeug beschreibt als bei 200.000 km. Zusätzliche absolute
    Mindestfenster, damit geringe Kilometerstände keine unbrauchbar engen Bereiche
    erzeugen (bei 40.000 km wären 15 % nur ±6.000 km).

    Beispiel 120.000 km -> sehr ähnlich ±18.000 (102.000-138.000),
    ähnlich ±30.000 (90.000-150.000), bedingt ±42.000 (78.000-162.000).
    """
    return (max(0.15 * ziel_km, 10_000),
            max(0.25 * ziel_km, 15_000),
            max(0.35 * ziel_km, 25_000))


def _domain_kurz(url: str) -> str:
    """Domain ohne www./Schema — nur fuer lesbare Begruendungstexte."""
    try:
        return (urlparse(url or "").netloc or "").lower().removeprefix("www.")
    except Exception:
        return ""


def _bewerte(b: Preisbeobachtung, ziel: dict) -> Preisbeobachtung:
    """Deterministische Vergleichbarkeit einer Beobachtung gegen das Zielfahrzeug.

    Grundsatz dieser Etappe (§8): BREIT suchen, STRENG validieren. Die Suchquery
    muss nicht jedes Kriterium tragen — jedes gefundene Fahrzeug wird hier hart
    gegen die Zielidentität geprüft. Harte Ausschlusskriterien (§5): fremdes Modell,
    fremde Generation/Baureihe, andere Motorvariante, anderer Kraftstoff,
    widersprüchliche Leistung, weit entferntes Baujahr, weit entfernte Laufleistung.
    Weiche Kriterien senken nur die Ähnlichkeitsstufe (Karosserie, Facelift-Grenze,
    fehlende Attribute).
    """
    fenster = ""
    if b.gruende and b.gruende[0].startswith("\x00"):
        fenster = b.gruende[0][1:]
    worte = _wort_tokens(fenster)
    gruende: list[str] = []
    stufe = 0  # 0 = sehr_aehnlich
    # Similarity-Startwert; jede Unschärfe zieht ab, eine bestätigte Motorvariante
    # gibt einen kleinen Bonus. Rein informativ/diagnostisch — die Aufnahme in den
    # Median entscheidet weiterhin die Stufe.
    sim = 1.0

    def ab(n: int):
        nonlocal stufe
        stufe = min(_UNGEEIGNET, stufe + n)

    def hoechstens_bedingt():
        """§6: Deckelt auf "bedingt" — die Beobachtung darf die normale
        Preisstatistik nicht mittragen, bleibt aber als Fallback/Kontext erhalten."""
        nonlocal stufe
        stufe = max(stufe, _STUFEN.index("bedingt"))

    def verwirf(grund: str) -> Preisbeobachtung:
        b.vergleichbarkeit = "ungeeignet"
        b.gruende = [grund]
        b.similarity = 0.0
        b.acceptance_reason = f"verworfen: {grund}"
        return b

    # ── NICHT-FAHRZEUGRUBRIK (zuerst) ────────────────────────────────────────
    # Die eigene Detailanzeige liegt in einer Teile-/Zubehörrubrik. Ein RaceChip,
    # ein Felgensatz oder ein ausgebauter Motor ist kein Vergleichsfahrzeug — auch
    # dann nicht, wenn Preis, Kilometerstand und Motorbezeichnung sauber im Text
    # stehen (Audit: ein "BMW 320d G20 Motor B47D20 komplett" für 2.500 € wurde
    # als sehr_aehnlich eingestuft). Bisher fielen solche Anzeigen nur zufällig
    # durch — über ein Fremdmodell-Token aus einem Shop-Link oder eine fremde
    # Generation im Produkttitel.
    rubrik = nicht_fahrzeug_rubrik(b.detail_url)
    if rubrik:
        return verwirf(f"Nicht-Fahrzeugkategorie der Detailanzeige ({rubrik})")

    # ── §Source-Policy (zuerst, noch vor jeder fachlichen Pruefung) ───────────
    # Ohne ausdrueckliche Erlaubnis/API-Lizenz darf eine Quelle nicht automatisiert
    # preisbildend werden. Das ist KEINE Aussage ueber das Inserat — es kann
    # fachlich einwandfrei sein. Die Pruefung steht deshalb ganz vorn und traegt
    # einen eigenen Wortlaut, damit sie im Funnel nicht mit fachlichen Ablehnungen
    # ("falsches Modell", "ungeeignetes Fahrzeug") vermischt wird.
    quelle = b.detail_url or b.quelle_url or ""
    if quelle and not _darf_preisbildend_sein(quelle):
        return verwirf(f"{_SOURCE_POLICY_GRUND} ({_domain_kurz(quelle)})")

    # ── HARTE MODELLTREUE (zuerst) — Root-Cause #5 ────────────────────────────
    # Nennt das lokale Preis-Umfeld ein FREMDES Modell (anderes Modell irgendeiner
    # Marke, inkl. Motor-Verkaufsbezeichnung '520d' UND marken-skopierter Zahl '520')
    # und NICHT das Zielmodell, wird der Datenpunkt hart verworfen.
    # ── §1/§5: unsichere Herkunft deckelt die Vergleichbarkeit ────────────────
    # Konnte die Fahrzeugkarte NICHT strukturell abgegrenzt werden, stammt der
    # Datenpunkt aus einem Zeichenfenster um den Preis. Ein solches Fenster kann
    # nachweislich Attribute benachbarter Inserate enthalten (Offline-Nachweis der
    # Diagnose-Persistenz: ein Kartentext begann mit dem Datumsrest der
    # Vorgängerkarte). Solche Punkte dürfen deshalb NIE "sehr ähnlich" oder
    # "ähnlich" werden — sie bleiben Kontext bzw. Fallback, tragen die normale
    # Preisstatistik aber nicht mit.
    if getattr(b, "window_fallback_used", False):
        hoechstens_bedingt(); sim -= 0.25
        gruende.append("Fahrzeugkarte nicht strukturell abgegrenzt (Zeichenfenster)")

    # §Evidence-Zone: die Variantenprüfung sieht NUR strukturell abgegrenzte Karten.
    # Ein bloßes Zeichenfenster kann Text der Nachbaranzeige enthalten — daraus darf
    # keine harte Fremdvarianten-Entscheidung folgen.
    zone = None if getattr(b, "window_fallback_used", False) else _varianten_zone(fenster)
    fremd_grund = _ist_fremdmodell(worte, ziel, zone)
    if fremd_grund:
        return verwirf(f"anderes Modell im Preisumfeld ({fremd_grund})")
    if worte & (ziel.get("marke_tokens") or set()):
        b.make = ziel.get("marke_name")
    if worte & (ziel.get("modell_tokens") or set()):
        b.model = ziel.get("modell_name")

    # ── HARTE MOTORVARIANTE (§5) ──────────────────────────────────────────────
    # Ein 320d darf nicht gegen einen 320i oder 330d gerechnet werden. Beide sind
    # Motorvarianten DERSELBEN Baureihe und damit modell-seitig unauffällig — die
    # Abgrenzung ist deshalb ein eigener Schritt. Sie greift nur, wenn die
    # Zielmotorisierung überhaupt eine trennscharfe Verkaufsbezeichnung hat
    # (siehe _motor_tokens) — sonst übernehmen Kraftstoff und Leistung (unten).
    ziel_motor = ziel.get("ziel_motor_tokens") or set()
    fremd_motor = ziel.get("fremd_motor_tokens") or set()
    # §Trust/§1: Der interne MOTORCODE (F20DTH, B47D20, OM651) stammt aus der
    # ungeprüften Fahrzeug-DB und steht in Kleinanzeigen praktisch nie. Er wird
    # deshalb GETRENNT geführt und darf nur BESTÄTIGEN, wenn die Karte ihn selbst
    # nennt — nie die harte Prüfung scharfschalten und nie eine Abwertung
    # auslösen, wenn er fehlt. Vorher floss er in `ziel_motor_tokens` und deckelte
    # damit im Live-Audit 100 % aller Insignia-B-Karten auf "nicht belegt".
    motorcode = ziel.get("motorcode_tokens") or set()
    bestaetigend = ziel_motor | motorcode
    motor_bestaetigt = bool(bestaetigend & worte)
    if ziel_motor and ziel.get("motor_hart", True):
        # DB-Wissen nur bei Verifikation (fremd_motor ist sonst leer) PLUS die
        # direkte Bezeichnung aus dem Inserat selbst — letztere braucht keine
        # Fahrzeug-DB, nur Nutzerangabe und Kartentext.
        fremde = (fremd_motor & worte) | _fremde_bezeichnungen_im_text(fenster, ziel_motor)
        if fremde and not motor_bestaetigt:
            return verwirf(f"andere Motorvariante ({sorted(fremde)[0]})")
    if motor_bestaetigt:
        b.engine_variant = sorted(bestaetigend & worte)[0]
        gruende.append(f"Motorvariante bestätigt ({b.engine_variant})")
        sim = min(1.0, sim + 0.05)
    elif ziel_motor:
        # §2: Der Motor MUSS auf Kartenebene belegt sein. Suchquery und
        # Seitenüberschrift ("BMW 320d gebraucht kaufen") beschreiben die SUCHE,
        # nicht das einzelne Fahrzeug — eine Trefferliste zu "320d" enthält
        # regelmäßig auch andere Motorisierungen. Ohne eigene Motorangabe ist die
        # Karte deshalb kein vollwertiger Vergleich, sondern höchstens conditional.
        # Keine Vererbung aus Titel oder Query.
        hoechstens_bedingt(); sim -= 0.20
        gruende.append("Motorisierung auf der Karte nicht belegt")

    # ── HARTER KRAFTSTOFF (§5): Diesel ist nicht Benzin ───────────────────────
    ziel_kr = _kraftstoff_im_text(ziel.get("kraftstoff") or "")
    if ziel_kr and b.fuel and b.fuel != ziel_kr:
        if not ziel.get("kraftstoff_hart", True):
            ab(2); sim -= 0.20
            gruende.append(f"anderer Kraftstoff ({b.fuel} statt {ziel_kr}, ungeprüfte Zielangabe)")
        else:
            return verwirf(f"anderer Kraftstoff ({b.fuel} statt {ziel_kr})")
    if ziel_kr and b.fuel == ziel_kr:
        gruende.append(f"Kraftstoff bestätigt ({ziel_kr})")

    # ── LEISTUNG (§5): keine exakte Zahl verlangt, aber kein Widerspruch ──────
    # Eng verwandte Motorisierungen streuen um wenige PS (Modelljahr-Updates,
    # Overboost-Angaben). Ein echter Motorwechsel (174 PS Diesel vs. 250 PS
    # Benziner) liegt weit außerhalb. Ist die Motorvariante über die
    # Verkaufsbezeichnung bereits bestätigt, gilt die Leistung als geklärt.
    # §Leistung: Verkaufsbezeichnung und tatsächliche Leistung sind GETRENNTE
    # Evidenz. Früher schaltete eine bestätigte Bezeichnung ("320d") die Prüfung
    # ab — ein nachträglich leistungsgesteigertes Fahrzeug ("Stage 1 (225 PS)")
    # galt damit als serienmäßiger 320d. Die Bezeichnung sagt nichts darüber, was
    # unter der Haube tatsächlich anliegt.
    ziel_ps = ziel.get("leistung_ps")
    if ziel_ps and b.horsepower:
        toleranz = max(0.15 * ziel_ps, 25)
        if abs(b.horsepower - ziel_ps) > toleranz:
            if not ziel.get("leistung_hart", True):
                ab(2); sim -= 0.20
                gruende.append(f"abweichende Motorleistung ({b.horsepower} PS statt "
                               f"{ziel_ps} PS, ungeprüfte Zielangabe)")
            else:
                return verwirf(
                    f"abweichende Motorleistung ({b.horsepower} PS statt {ziel_ps} PS)")
        gruende.append("Leistung passt")

    codes = _generation_tokens(fenster)
    ziel_gen = ziel.get("generation_tokens") or set()
    fremd_gen = ziel.get("fremd_generationen") or set()
    hat_ziel = any(c in ziel_gen for c in codes)
    hat_fremd = any(c in fremd_gen for c in codes)

    if hat_fremd and not hat_ziel:
        return verwirf(f"andere Generation ({next(c for c in codes if c in fremd_gen).upper()})")
    if hat_ziel:
        b.generation = next(c for c in codes if c in ziel_gen).upper()
        b.generation_evidence = "explicit_card"
        gruende.append(f"Generation bestätigt ({b.generation})")
    elif ziel_gen:
        # Die Karte nennt keinen Code. Bevor sie deshalb abgewertet wird: lässt er
        # sich aus der Karosserie ABLEITEN? Das geht nur, wenn für die
        # Baureihenfamilie eine geprüfte Chassiscode/Karosserie-Zuordnung hinterlegt
        # ist (app/chassis_codes.py) und genau EIN Code zur Karosserie der Karte
        # passt. Eine explizite Angabe hat immer Vorrang — dieser Zweig läuft nur,
        # wenn die Karte gar keinen Code trägt (hat_ziel/hat_fremd sind beide falsch,
        # widersprechende Codes wurden oben bereits hart verworfen).
        # §Provenance: NUR listing-eigene Karosserie-Evidence darf einen Chassiscode
        # ableiten. Ein URL-Filter der Trefferliste ist kein Merkmal des Fahrzeugs.
        inferiert, grund = _inferiere_generation(ziel.get("chassis_codes") or {},
                                                 _identitaets_body(b))
        if inferiert and inferiert in ziel_gen:
            b.generation = inferiert.upper()
            b.generation_evidence = "inferred_database"
            b.generation_inference_reason = grund
            gruende.append(f"Generation abgeleitet ({b.generation})")
        elif inferiert and inferiert in fremd_gen:
            b.generation_evidence = "inferred_database"
            b.generation_inference_reason = grund
            return verwirf(f"andere Generation ({inferiert.upper()}, aus Karosserie abgeleitet)")
        else:
            # §6: Zielgeneration bekannt, Karte belegt sie nicht und sie ist auch
            # nicht eindeutig ableitbar. Der Code in Seitentitel oder Suchbegriff
            # sagt nichts über das einzelne Fahrzeug. Höchstens conditional.
            hoechstens_bedingt(); sim -= 0.15
            gruende.append("Generation auf der Karte nicht belegt")
    else:
        # §Trust/§4: Für dieses Fahrzeug kennt die Datenbasis gar keinen
        # Generationscode (z.B. Opel Insignia B). Das ist eine Lücke UNSERER
        # Referenzdaten — das Inserat dafür abzuwerten bestrafte fremdes
        # Nichtwissen und traf im Live-Audit 100 % aller Insignia-B-Karten
        # (166 von 166 weichen Ablehnungen trugen diesen Baustein).
        # Unknown bleibt unknown: nicht belohnen, NICHT bestrafen.
        gruende.append("Zielgeneration unbekannt (nicht bewertet)")

    ziel_bj = ziel.get("baujahr")
    if b.baujahr is not None and ziel_bj:
        d = abs(b.baujahr - ziel_bj)
        if d <= 1:
            gruende.append(f"Baujahr passt (±{d})")
        elif d == 2:
            ab(1); gruende.append("Baujahr ±2 Jahre")
        else:
            return verwirf(f"Baujahr weicht stark ab ({b.baujahr} vs {ziel_bj})")
        sim -= min(0.20, 0.10 * d)
        # §6: Facelift-/Modellpflege-Grenzen haben Vorrang vor der ±1/±2-Regel.
        # Liegt zwischen Ziel- und Vergleichsbaujahr eine dokumentierte Modellpflege,
        # ist das Fahrzeug trotz nominell passendem Jahresabstand nicht mehr
        # "sehr ähnlich" (anderes Gesicht, andere Ausstattung, anderer Marktpreis).
        fl = ziel.get("facelift_jahr")
        if fl and (b.baujahr < fl) != (ziel_bj < fl):
            ab(1); sim -= 0.10
            gruende.append(f"andere Modellpflege-Phase (Facelift {fl})")
    else:
        ab(1); sim -= 0.15
        gruende.append("Baujahr unbekannt")

    ziel_km = ziel.get("kilometerstand")
    if b.kilometerstand is not None and ziel_km:
        d = abs(b.kilometerstand - ziel_km)
        f_sehr, f_aehn, f_bedingt = _km_fenster(ziel_km)
        if d <= f_sehr:
            gruende.append("Laufleistung vergleichbar")
        elif d <= f_aehn:
            ab(1); gruende.append("Laufleistung mäßig abweichend")
        elif d <= f_bedingt:
            ab(2); gruende.append("Laufleistung deutlich abweichend")
        else:
            return verwirf(f"Laufleistung weicht stark ab ({b.kilometerstand:,} km)".replace(",", "."))
        # Entfernung vom Zielwert fließt stufenlos in die Similarity ein (§7).
        sim -= min(0.25, 0.25 * (d / f_aehn))
    else:
        ab(1); sim -= 0.15
        gruende.append("Laufleistung unbekannt")

    # ── KAROSSERIE (§5): weiches Kriterium ────────────────────────────────────
    # Für "sehr ähnlich" soll die Karosserie identisch sein (Grand Sport ↔ Grand
    # Sport, Touring ↔ Touring). Eine andere Karosserie ist kein harter Ausschluss,
    # taugt aber nur noch als schwächerer Vergleich.
    ziel_body = ziel.get("karosserie")
    if ziel_body and b.body and b.body != ziel_body:
        # Abwertung bleibt für JEDE Herkunft: eine nach Kombi gefilterte Trefferliste
        # ist ein belastbares Gegen-Indiz. Nur das Aufwerten ist unzulässig (§Provenance).
        ab(2); sim -= 0.20
        gruende.append(f"andere Karosserie ({b.body} statt {ziel_body})")
    elif ziel_body and _identitaets_body(b) == ziel_body:
        gruende.append(f"Karosserie bestätigt ({ziel_body})")

    # ── §5 SICHERHEITSGATE: Ziel ohne trennscharfe Verkaufsbezeichnung ────────
    # Nannte der Nutzer keine echte Motorbezeichnung ("2.0 Diesel 174 PS" statt
    # "320d"), trägt die Motorprüfung nichts zur Identität bei. Ohne Ersatz würde
    # jedes Fahrzeug mit passendem Baujahr/km vergleichbar — im Live-Audit wurde
    # bei abgeschaltetem Motor-Token ein Ford Transit Werkstattwagen (29.999 €)
    # zum "bedingten" Vergleich für einen Opel Insignia (§9).
    #
    # Als Nachweis der Zielidentität genügt EINES von beiden:
    #   a) die Karte nennt den Modellanker selbst (Nutzerangabe vs. Listing), oder
    #   b) die Karte nennt den Ziel-Generationscode selbst ("W205") — das ist die
    #      spezifischere Aussage und wurde oben bereits als `explicit_card` belegt.
    # Dazu der Kraftstoff, falls der NUTZER ihn angegeben hat (§5 B).
    #
    # Läuft bewusst NACH der Generationsprüfung: sonst fiele ein Fahrzeug durch,
    # dessen Identität über den Chassiscode einwandfrei belegt ist.
    if not ziel.get("motor_hart", True):
        anker = ziel.get("modell_anker_user") or set()
        sichtbar = _wort_tokens(_varianten_zone(fenster))
        beleg = sorted(anker & sichtbar)
        if not beleg and b.generation_evidence != "explicit_card":
            if not anker:
                return verwirf("keine direkte Zielidentität prüfbar (weder "
                               "Motorbezeichnung noch Modellanker)")
            return verwirf("Zielmodell auf der Karte nicht belegt "
                           f"({'/'.join(sorted(anker))})")
        # §5 B: `kraftstoff_hart` markiert genau "vom Nutzer angegeben" (oder
        # verifizierte DB). Eine bloß aus der ungeprüften Motorvariante geerbte
        # Zielangabe darf hier nichts fordern.
        if ziel_kr and ziel.get("kraftstoff_hart", True) and b.fuel != ziel_kr:
            return verwirf(f"Kraftstoff auf der Karte nicht bestätigt (Ziel {ziel_kr})")
        if beleg:
            gruende.append(f"Zielmodell belegt ({beleg[0]})")

    b.vergleichbarkeit = _STUFEN[stufe]
    b.similarity = round(max(0.0, min(1.0, sim)), 3)
    b.gruende = gruende
    b.acceptance_reason = f"{b.vergleichbarkeit}: " + ", ".join(gruende)
    return b


def _zaehlt_als_fahrzeug(b: Preisbeobachtung) -> bool:
    """Darf dieser Datenpunkt als KONKRETES Vergleichsfahrzeug in Median, Quartile
    und Datenqualität eingehen (§3)?

      - "listing"  : Detailseite eines Einzelinserats -> ja.
      - "unknown"  : Seitentyp nicht klassifizierbar -> ja (die eigentliche Prüfung
                     macht die Fahrzeugvalidierung in _bewerte).
      - "market_category": Suchergebnisseite eines ECHTEN Marktplatzes. Sie zeigt
                     einzelne, real inserierte Fahrzeugkarten — erlaubt, ABER NUR
                     wenn die Karte sauber getrennt extrahiert wurde. Als Nachweis
                     dafür gilt, dass Preis, Kilometerstand UND Baujahr alle drei im
                     EIGENEN Merkmalssegment des Datenpunkts standen (siehe
                     _merkmalsfenster). Fehlt km oder Baujahr, wäre die Zuordnung
                     geraten ("Snippet enthält mehrere Preise + mehrere
                     Kilometerzahlen") — dann bleibt der Punkt Hintergrundquelle.
      - "category" : Kategorie-/Filterseite einer Aggregator-/Ratgeberdomain ohne
                     einzeln geprüfte Inserate (12gebrauchtwagen.de u.ä.) -> nie.

    Diese Regel ersetzt den pauschalen Kategorieseiten-Ausschluss aus
    Reliability-Sprint 4. Der dort belegte Missstand (eine reine Modell-Suchseite
    fließt als "Vergleichsfahrzeug" in den Median) bleibt ausgeschlossen — neu ist
    nur, dass eine echte Marktplatz-Trefferliste mit vollständig attribuierten
    Karten wieder zählen darf, weil die Merkmals-Segmentierung inzwischen sicher
    stellt, dass jede Karte ausschließlich mit ihren EIGENEN Merkmalen bewertet wird.
    """
    if b.source_type == "category":
        return False
    if b.source_type == "market_category":
        return b.baujahr is not None and b.kilometerstand is not None
    return True


def _cap_pro_url(beob: list[Preisbeobachtung], max_pro_url: int = 5,
                 max_pro_url_karten: int = 12) -> list[Preisbeobachtung]:
    """Begrenzt den Beitrag EINER Rechercheseite (§9/§12): Aggregat-/Übersichtsseiten
    liefern oft viele, teils mis-assoziierte Preis/km/Baujahr-Tripel (drei verschiedene
    Preise mit identischem km — der km gehört in Wahrheit nur zu EINEM Inserat). Ohne
    Deckelung dominiert eine einzelne verrauschte Seite die Statistik und verbreitert
    die Streuung künstlich. Reihenfolge (spezifischste zuerst) bleibt erhalten.

    Zwei Deckel (§3/§9): eine Marktplatz-Trefferliste zeigt legitim ein Dutzend
    sauber getrennter Fahrzeugkarten — für sie wäre eine Deckelung bei 5 der
    eigentliche Grund, warum ein verbreitetes Fahrzeug die Sechser-Schwelle einer
    einzelnen Plattform nie erreicht. Datenpunkte mit vollständigem Baujahr UND
    Kilometerstand (= sauber getrennte Karte) bekommen deshalb den höheren Deckel;
    unvollständige Punkte, bei denen eine Fehlzuordnung möglich ist, bleiben bei 5.
    """
    zaehler: dict[str, int] = {}
    out: list[Preisbeobachtung] = []
    for b in beob:
        u = b.quelle_url or ""
        karte = b.baujahr is not None and b.kilometerstand is not None
        zaehler[u] = zaehler.get(u, 0) + 1
        if zaehler[u] <= (max_pro_url_karten if karte else max_pro_url):
            out.append(b)
    return out


# Plausibilitätsband für den "bedingt"-Fallback (§B), relativ zum Referenzmedian.
# BEWUSST enger als das allgemeine Sanity-Band in _trim_ausreisser (0,55-1,65): ein
# nur bedingt passender Datenpunkt trägt weniger Beleg, darf die Statistik aber
# gleichwertig beeinflussen — deshalb muss er näher am belegten Marktniveau liegen.
_BEDINGT_BAND_LO = 0.65
_BEDINGT_BAND_HI = 1.50


def _plausible_bedingte(bedingt: list[Preisbeobachtung],
                        gute: list[Preisbeobachtung]) -> list[Preisbeobachtung]:
    """Welche "bedingt" passenden Beobachtungen dürfen als Fallback (§B) in die
    Preisstatistik?

    Der Fallback greift nur bei sehr dünner Datenlage — genau dann ist ein einzelner
    unplausibler Wert am gefährlichsten, weil er den Median und vor allem die
    Untergrenze der Marktspanne dominiert (realer Insignia-Befund: ein Punkt mit
    9.999 € zog die Spanne um mehrere Tausend Euro nach unten).

    Zwei Fälle, beide ohne fahrzeugspezifische Preisannahme:
      - Es gibt bereits gute Beobachtungen -> deren Median ist die Referenz. Nur
        "bedingt"-Punkte innerhalb des Plausibilitätsbands zählen.
      - Es gibt gar keine guten -> die "bedingt"-Punkte werden gegen ihren EIGENEN
        Median geprüft (robuster Kern), damit wenigstens grobe Fehl-Extraktionen
        herausfallen. Bleibt kein tragfähiger Kern (< 3), gibt es keinen Fallback.
    """
    if not bedingt:
        return []
    referenz = [b.preis_eur for b in (gute or bedingt)]
    if not referenz:
        return []
    med = statistics.median(referenz)
    if med <= 0:
        return []
    lo, hi = med * _BEDINGT_BAND_LO, med * _BEDINGT_BAND_HI
    behalten = [b for b in bedingt if lo <= b.preis_eur <= hi]
    if gute:
        return behalten
    # Ohne jede gute Beobachtung braucht der reine "bedingt"-Kern eigene Substanz.
    return behalten if len(behalten) >= 3 else []


def _trim_ausreisser(beob: list[Preisbeobachtung]) -> tuple[list, list]:
    """Robuster Kern der Preisbeobachtungen um den Median (MAD + relatives Sanity-Band).

    Ersetzt den reinen Tukey-Zaun: bei der aus Snippet-Text extrahierten Datenbasis
    treten NICHT nur einzelne Ausreißer auf, sondern strukturelles Rauschen (Fehl-
    Extraktionen wie ein 1.631-€-Fragment, mis-assoziierte Aggregat-Preise, gemischte
    Trims). Der Median-basierte MAD-Zaun ist gegen solche Kontamination robuster als
    der Quartils-basierte Tukey-Zaun und identifiziert den kohärenten Marktkern —
    genau das, was für einen belastbaren Marktwert (§0/§3) nötig ist.

    - Relatives Sanity-Band: ein WIRKLICH vergleichbares Fahrzeug liegt selten unter
      ~55 % oder über ~165 % des Medians (entfernt Fragmente/Fremdpreise hart).
    - MAD-Zaun (median. absolute Abweichung, skaliert): entfernt den bimodalen
      Rand (z.B. deutlich teurere, unmarkierte höherwertige Angebote).
    Gibt (behalten, entfernt) zurück. Bricht NICHT vorschnell ab, wenn viel Rauschen
    vorliegt — der kohärente Kern IST das Ziel; bleibt zu wenig übrig, greift später
    der Streuungs-Guard und stuft ehrlich als unzuverlässig ein.
    """
    if len(beob) < 4:
        return beob, []
    preise = [b.preis_eur for b in beob]
    med = statistics.median(preise)
    if med <= 0:
        return beob, []
    lo_band, hi_band = med * 0.55, med * 1.65
    abw = statistics.median([abs(p - med) for p in preise])
    if abw > 0:
        lo_mad, hi_mad = med - 3.0 * 1.4826 * abw, med + 3.0 * 1.4826 * abw
        lo, hi = max(lo_band, lo_mad), min(hi_band, hi_mad)
    else:
        lo, hi = lo_band, hi_band
    behalten = [b for b in beob if lo <= b.preis_eur <= hi]
    entfernt = [b for b in beob if not (lo <= b.preis_eur <= hi)]
    # Bleibt ein zu kleiner Kern übrig, war die Filterung zu aggressiv für diese
    # Datenlage -> lieber ungetrimmt weitergeben (der Streuungs-Guard entscheidet dann).
    if len(behalten) < max(4, len(beob) // 4):
        return beob, []
    return behalten, entfernt


def _typischer_bereich(preise: list[int]) -> tuple[int, int]:
    """Robuster typischer Marktbereich = unteres/oberes Quartil (Ausreißer-fest),
    auf 100 € gerundet. Bei sehr wenigen Werten min/max des verwendeten Sets."""
    s = sorted(preise)
    if len(s) >= 4:
        q = statistics.quantiles(s, n=4, method="inclusive")  # [p25, p50, p75]
        lo, hi = q[0], q[2]
    else:
        lo, hi = s[0], s[-1]
    return _r100(lo), _r100(hi)


def _r100(x: float) -> int:
    return int(round(x / 100.0) * 100)


def _marktabdeckung(domains) -> str:
    """Wie viele unabhängige Plattformen haben tatsächlich zum Median beigetragen (§10).

    BEWUSST getrennt von der Datenqualität (§2): Domainanzahl und Datenqualität sind
    nicht dasselbe. Acht eindeutig validierte BMW-320d-G20-Angebote von einer einzigen
    Plattform sind eine BESSERE Basis als acht gemischte BMW-3er-Angebote aus vier
    Domains. Die Plattformvielfalt beschreibt deshalb nur, wie breit der Gesamtmarkt
    abgetastet wurde — sie deckelt später das Gesamtvertrauen (siehe
    marktrecherche.research_status), entwertet aber keine saubere Datenbasis.
    """
    n = len({d for d in (domains or []) if d})
    if n >= 3:
        return "breit"
    if n == 2:
        return "gut"
    return "eingeschraenkt"


def _datenqualitaet(verwendet: list[Preisbeobachtung], median: int | None,
                    lo: int | None, hi: int | None) -> str:
    """Datenqualität = wie zuverlässig und wie ähnlich sind die EINZELNEN
    Fahrzeugbeobachtungen (§2/§9) — NICHT, von wie vielen Plattformen sie stammen.

      - Anzahl eindeutiger, deduplizierter, valider Beobachtungen
      - Anteil wirklich passender Treffer (sehr ähnlich / ähnlich)
      - Attribut-VOLLständigkeit (Baujahr UND km je Datenpunkt — siehe Empirie-Hinweis)
      - kontrollierte Preisstreuung (Quartilsspanne relativ zum Median)
      - Anteil konkreter Einzelfahrzeuge (Listing-URL oder Baujahr+km am Datenpunkt)

    SINGLE-SOURCE-MODUS (§9, Kernänderung dieser Etappe): die frühere Bedingung
    `quellenvielfalt >= 2` ist hier ENTFALLEN. Sie hat den realen BMW-320d-G20-Fall
    (konstant valide, aber ausschließlich kleinanzeigen.de-Angebote) trotz sauberer
    Datenlage auf "niedrig" gedrückt und damit einen verbreiteten Fahrzeugfall in
    research_failed laufen lassen. Die Plattformvielfalt wird stattdessen getrennt
    als Marktabdeckung geführt (_marktabdeckung) und deckelt dort das
    Gesamtvertrauen auf MEDIUM, solange nur eine Plattform beigetragen hat — das ist
    die ehrliche Aussage ("Daten gut, Marktabdeckung eingeschränkt") statt eines
    pauschalen Abbruchs.

    HARTE INVARIANTE (§14, nicht verhandelbar): 0 "sehr_aehnlich" + 0 "aehnlich" kann
    NIEMALS "hoch" ergeben — ausschließlich "bedingt" passende Treffer sind fachlich
    keine belastbare Basis für die höchste Qualitätsstufe, unabhängig von Trefferzahl
    oder Quellenvielfalt.

    EMPIRIE-HINWEIS (Reliability-Sprint 3, per Live-Diagnose mit scripts/
    diagnose_recherche.py): Tavilys Basic-Suche liefert für diese Query-Art so gut
    wie NIE echte Einzelinserat-Detail-URLs (source_type=="listing") — real
    beobachtet: 0 von 22 verwendeten Datenpunkten beim BMW-320d-Testfall, obwohl die
    Basis inhaltlich stark war (21/23 mit Baujahr UND km, Median stabil). Ein hartes
    `listing_n`-Gate für HOCH wäre daher für praktisch JEDES Fahrzeug unerreichbar
    gewesen — das hätte §0 (verbreitete Fahrzeuge erreichen normalerweise HOCH)
    direkt verletzt. Statt der (mit dieser Datenquelle strukturell fast nie
    erreichbaren) Seiten-URL-Klassifikation zählt daher das stärkste tatsächlich
    beobachtbare Signal für "das ist ein konkretes Einzelfahrzeug": Baujahr UND
    Kilometerstand GEMEINSAM am selben Datenpunkt extrahiert (nicht nur eines von
    beiden) — eine Zeile wie "BMW 320d 2019, 89.000 km, 24.900 €" innerhalb einer
    Such-/Kategorieseite ist inhaltlich ein echtes Einzelangebot, auch wenn die
    Trägerseite selbst eine Kategorieseite ist. Echte Einzelinserat-URLs (falls
    Tavily sie doch liefert) zählen zusätzlich mit voller Kraft.
    """
    n = len(verwendet)
    if n == 0 or not median:
        return "niedrig"
    mit_attr = sum(1 for b in verwendet if b.baujahr is not None or b.kilometerstand is not None)
    attr_ratio = mit_attr / n
    rel_spanne = (hi - lo) / median if (lo is not None and hi is not None and median) else 1.0
    sehr_n = sum(1 for b in verwendet if b.vergleichbarkeit == "sehr_aehnlich")
    aehn_n = sum(1 for b in verwendet if b.vergleichbarkeit == "aehnlich")
    listing_n = sum(1 for b in verwendet if b.source_type == "listing")
    # §Phase 1-3/6: Kategorie-/Aggregatorseiten-Herkunft darf selbst mit vollem
    # Baujahr+km-Attribut NICHT als "konkretes Einzelfahrzeug" zählen (harte
    # Abgrenzung von der reinen "unknown"-Fallback-Heuristik) — in der echten
    # Pipeline erreichen solche Punkte `verwendet` inzwischen ohnehin nicht mehr
    # (analysiere_markt filtert source_type=="category" bereits vor `kandidaten`
    # heraus); diese Bedingung ist ein zusätzliches Sicherheitsnetz für direkte
    # Aufrufer dieser Funktion (z.B. Tests).
    beide_n = sum(1 for b in verwendet
                  if b.baujahr is not None and b.kilometerstand is not None
                  and b.source_type != "category")
    # "Konkretes Einzelfahrzeug"-Signal: echte Listing-URL ODER vollständiges
    # Baujahr+km-Paar am Datenpunkt (siehe Empirie-Hinweis oben) — je nachdem, was
    # mehr zählt (nie doppelt gezählt).
    konkret_n = max(listing_n, beide_n)

    passend_n = sehr_n + aehn_n

    if passend_n == 0:
        # Ausschließlich "bedingt" passende Treffer (§14-Beispiel: Insignia-Fall,
        # 7/7 bedingt). Maximal "mittel" — und nur mit einer minimalen Basis aus
        # konkreten Einzelfahrzeugen, sonst "niedrig".
        if n >= 4 and konkret_n >= 2 and rel_spanne <= _MAX_REL_SPANNE:
            return "mittel"
        return "niedrig"

    # Alle Datenpunkte in `verwendet` sind mindestens "ähnlich" (Baujahr/km im Rahmen,
    # kein Fremdmodell, kein fremder Motor/Kraftstoff). Die Qualitätssignale sind
    # daher: Menge, Anteil wirklich passender Treffer, Attributvollständigkeit, ENGE
    # Streuung UND ein Mindestmaß an konkreten Einzelfahrzeug-Datenpunkten (§12: reine
    # Kategorie-/Statistik-Angaben ohne jedes Einzelfahrzeug-Attribut dürfen HOCH nicht
    # allein tragen). Quellenvielfalt geht hier bewusst NICHT ein (§2/§9, s.o.).
    #
    # Single-Source-fähiger Normalfall: die in §9 genannte Untergrenze von SECHS
    # eindeutigen, deduplizierten, validen und fachlich passenden Beobachtungen,
    # überwiegend attributvollständig und mit enger Streuung. Bewusst KEINE
    # Ausnahme darunter: fünf gute Treffer bleiben "mittel" — die Sechs ist die
    # vereinbarte Schwelle, ab der eine Einzelplattform-Basis als hochwertig gilt.
    if (n >= 6 and passend_n >= max(3, n * 0.5) and attr_ratio >= 0.6
            and rel_spanne <= 0.30 and konkret_n >= n * 0.5):
        return "hoch"
    # Belastbare Basis, aber nicht "hoch" — weniger Treffer oder etwas breitere
    # (noch kontrollierte) Streuung.
    if n >= 4 and rel_spanne <= _MAX_REL_SPANNE:
        return "mittel"
    return "niedrig"


def modell_relevant(r: dict, ziel: dict) -> bool:
    """Best-effort-Filter für die ANGEZEIGTEN Rechercheseiten (belege/Kontext):
    verwirft eine Seite, die klar ein FREMDES Modell nennt und NICHT das Zielmodell
    (z.B. 'BMW 4er' / 'Mercedes C-Klasse' im 3er-Check). Neutrale Seiten (kein klares
    Modellsignal) bleiben als Recherchequelle erhalten. Die harte Vergleichs-Filterung
    läuft ohnehin datenpunktgenau in _bewerte — dies verbessert nur die Quellenanzeige.
    """
    worte = _wort_tokens(f"{r.get('title', '')} {r.get('content', '')}")
    if worte & (ziel.get("modell_tokens") or set()):
        return True
    # Fremdmodell (alpha ODER marken-skopierte Zahl) klar erkannt -> raus.
    return _ist_fremdmodell(worte, ziel) is None


def baue_ziel(baureihe: dict | None, motor_match: dict | None, req,
              alle_baureihen: list[dict] | None,
              alle_motorvarianten: list[dict] | None = None) -> dict:
    """Baut das Ziel-Profil für die Vergleichbarkeitsbewertung.

    `fremd_generationen` = Generations-Kürzel ANDERER Baureihen desselben Modells
    (z.B. E90/F30/E46 beim 3er) — daten-getrieben aus der DB, kein Hardcoding. Ein
    Datenpunkt, dessen Snippet ein Fremd-Kürzel trägt, wird als 'ungeeignet'
    verworfen (die berüchtigte E90-7.000-€-Karre beim G20).

    `modell_tokens` / `fremd_modelle` = HARTE Modelltreue (Root-Cause #5): trennt
    das Zielmodell strukturiert von ALLEN anderen Modellen (jeder Marke) inkl. deren
    Motor-Verkaufsbezeichnungen. Ein Opel Mokka darf nie in den Insignia-Vergleich,
    ein 5er nie in den 3er, ein GLC nie in die C-Klasse — daten-getrieben, kein
    Modell-Hardcoding.
    """
    gen: set[str] = set()
    if baureihe:
        gen |= set(_generation_tokens(baureihe.get("generation", "")))
        gen |= set(_generation_tokens(baureihe.get("id", "")))
    fremd: set[str] = set()
    if baureihe:
        bm = (baureihe.get("marke") or "").lower()
        bmod = (baureihe.get("modell") or "").lower()
        for r in alle_baureihen or []:
            if (r.get("marke", "").lower() == bm and r.get("modell", "").lower() == bmod
                    and r.get("id") != baureihe.get("id")):
                # §Trust: eine FREMDE Generation aus einer anderen DB-Zeile darf nur
                # dann hart verwerfen, wenn dieser Fakt verifiziert ist. Ungeprüfte
                # Zeilen (der Normalfall) tragen sonst falsche Ausschlüsse — belegt
                # an `bmw-8er-e63-e64`, das die Codes der 6er-Reihe führt.
                if not is_verified(r, "generation"):
                    continue
                fremd |= set(_generation_tokens(r.get("generation", "")))
                fremd |= set(_generation_tokens(r.get("id", "")))
    fremd -= gen

    # ── §1: EXPLIZITE Zielgeneration hat Vorrang ─────────────────────────────
    # Eine DB-Baureihe fasst häufig mehrere Karosseriecodes zusammen ("G20/G21" =
    # Limousine und Touring, "F30/F31" ebenso). Beide landen dadurch in
    # `generation_tokens` und gelten gleichermaßen als Zielsignal — ein G21 würde
    # den Median eines ausdrücklich gesuchten G20 mitbestimmen. Nennt der Nutzer den
    # Code selbst (im Modell-, Motor-, Titel- oder Freitextfeld), ist das die
    # verbindliche Zielgeneration: die übrigen Codes derselben Baureihe werden zu
    # FREMD-Generationen und damit hart verworfen. Ohne explizite Angabe bleibt es
    # beim bisherigen Verhalten — dann ist die Baureihe als Ganzes das Ziel, und
    # etwas zu verwerfen, was der Nutzer nie ausgeschlossen hat, wäre falsch.
    explizit = set(_generation_tokens(" ".join(
        str(getattr(req, f, "") or "") for f in
        ("generation", "modell", "motor", "inserat_titel", "karosserie", "freitext")))) & gen
    if explizit and explizit != gen:
        fremd |= gen - explizit
        gen = explizit

    # ── Modelltreue: Ziel- vs. Fremd-Modell-Token (inkl. Motorbezeichnungen) ──
    ziel_id = baureihe.get("id") if baureihe else None
    modell_tokens: set[str] = set()
    fremd_modelle: set[str] = set()
    marke_tokens: set[str] = set()
    ziel_num: set[str] = set()
    fremd_num: set[str] = set()
    if baureihe:
        bm = (baureihe.get("marke") or "").lower()
        # Schreibvarianten mitnehmen ("VW" in der Anzeige, "Volkswagen" im Formular).
        marke_tokens = (_marke_tokens(baureihe.get("marke", ""))
                        | _marken_schreibvarianten(baureihe.get("marke", "")))
        id_marke = {r.get("id"): (r.get("marke") or "").lower() for r in (alle_baureihen or [])}
        _baureihe_nach_id = {r.get("id"): r for r in (alle_baureihen or [])}

        modell_tokens |= _modell_tokens(baureihe.get("modell", ""))
        ziel_num |= _num_modell_tokens(baureihe.get("modell", ""))
        for m in alle_motorvarianten or []:
            if m.get("baureihe_id") == ziel_id:
                modell_tokens |= _modell_tokens(m.get("bezeichnung", ""))
                ziel_num |= _num_modell_tokens(m.get("bezeichnung", ""))
        # §Trust: FREMDMODELL-Wissen stammt aus hunderten ungeprüften DB-Zeilen und
        # erzeugte damit die meisten harten Ablehnungen überhaupt (163 in einem
        # einzigen BMW-Lauf) — inklusive Störtoken wie "mercedes", "mit", "paket",
        # "auto" oder "pro", die aus Modellnamen und Motorbezeichnungen zerfallen
        # sind. Ungeprüfte Zeilen liefern deshalb kein Fremdsignal mehr.
        # §Trust/§14: Hier wird bewusst zwischen VOKABULAR und FAKTENVERTRAUEN
        # getrennt.
        #
        # `baureihe.modell` ist ein NAME ("Mokka", "5er", "GLC", "Passat"). Dass ein
        # solcher Name existiert, ist unabhängig davon, ob die Fahrzeugdaten dieser
        # Zeile stimmen — und die Modelltreue (Root-Cause #5) hängt daran: ein Opel
        # Mokka darf nie in den Insignia-Median. Namen bleiben deshalb Vokabular.
        #
        # `motorvariante.bezeichnung` dagegen zerfällt beim Tokenisieren in
        # Bruchstücke: aus "Mercedes-Maybach EQS 680 4MATIC" wurde das Fremdtoken
        # "mercedes", aus anderen Bezeichnungen "mit", "paket", "auto", "pro". Diese
        # Token haben im Audit korrekte Inserate verworfen. Sie gelten nur noch bei
        # verifizierter Motorvariantenliste.
        # §Trust: auch ein MODELLNAME aus der alten generierten DB ist ohne Nachweis
        # unverified und darf allein keinen harten Ausschluss tragen. Die
        # Modelltreue leistet jetzt der direkte Vergleich Nutzerangabe <-> Inserat
        # (_modell_widerspruch) — ohne jede DB-Liste.
        for r in alle_baureihen or []:
            if r.get("id") != ziel_id and is_verified(r, "modell"):
                fremd_modelle |= _modell_tokens(r.get("modell", ""))
                # Fremd-Zahlen NUR aus Baureihen DERSELBEN Marke (markeninterner Zahlenraum).
                if (r.get("marke") or "").lower() == bm:
                    fremd_num |= _num_modell_tokens(r.get("modell", ""))
        for m in alle_motorvarianten or []:
            if (m.get("baureihe_id") != ziel_id
                    and is_verified(_baureihe_nach_id.get(m.get("baureihe_id"), {}),
                                    "motorvarianten")):
                fremd_modelle |= _modell_tokens(m.get("bezeichnung", ""))
                if id_marke.get(m.get("baureihe_id")) == bm:
                    fremd_num |= _num_modell_tokens(m.get("bezeichnung", ""))
        # Alles, was auch zum Ziel gehört, ist KEIN Fremdsignal (gemeinsame Trim-/
        # Klassenwörter, geteilte Zahlen wie Mercedes C200/GLC200 -> beide '200').
        fremd_modelle -= modell_tokens
        fremd_num -= ziel_num

    # ── Motorvarianten-Abgrenzung INNERHALB der Zielbaureihe (§5) ─────────────
    # 320d vs 320i vs 330d sind modell-seitig identisch (alle "3er") und werden von
    # der Modelltreue-Prüfung oben bewusst NICHT getrennt. Für den Marktwert ist der
    # Unterschied aber entscheidend, deshalb ein eigener Token-Raum: Zielmotor gegen
    # die übrigen Motorvarianten DERSELBEN Baureihe.
    ziel_motor_tokens = _motor_tokens((motor_match or {}).get("bezeichnung") or "")
    if not ziel_motor_tokens:
        # Kein Motor-Match aus der DB -> Nutzerangabe (z.B. "320d") als Rückfall.
        ziel_motor_tokens = _motor_tokens(str(getattr(req, "motor", "") or ""))
    # §2: Auch der MOTORCODE (B47D20, OM651, CJCA) bestätigt die Motorisierung
    # eindeutig — manche Inserate nennen ihn statt der Verkaufsbezeichnung.
    # §Trust/§1: Er stammt aber aus der UNGEPRÜFTEN Fahrzeug-DB und steht in
    # Kleinanzeigen praktisch nie. Früher floss er in `ziel_motor_tokens` und
    # aktivierte damit die harte Motorprüfung; beim Opel Insignia B (F20DTH) war
    # das die alleinige Ursache für "Motorisierung auf der Karte nicht belegt" auf
    # 100 % der Karten und am Ende für research_failed. Er wird deshalb GETRENNT
    # geführt und wirkt nur noch bestätigend.
    motorcode_tokens = _motor_tokens((motor_match or {}).get("motorcode") or "")
    # §Trust/§9: Woher stammt die Zielmotorisierung? Eine Nutzerangabe ("320d")
    # ist DIREKTE Evidenz und trägt harte Entscheidungen unabhängig vom DB-Zustand.
    # Nur echte Verkaufsbezeichnungen zählen als Nutzer-Motorevidenz — "3er" ist
    # das Modell, keine Motorisierung, und würde sonst jede Angabe hart machen.
    motor_user_tokens = {t for t in _motor_tokens(" ".join(
        str(getattr(req, f, "") or "") for f in ("motor", "modell")))
        if _RE_VERKAUFSBEZEICHNUNG.match(t)}
    # Eine ZIEL-Verkaufsbezeichnung existiert nur, wenn der Nutzer eine Motorisierung
    # genannt hat: `motor_match` ist das Ergebnis von find_motor(baureihe, req.motor).
    # Sie ist damit selbst Nutzerevidenz — und für den direkten Abgleich gegen die
    # Bezeichnung im Inserat brauchen wir die DB-Liste der übrigen Varianten nicht.
    # §2: Hart widersprechen darf nur DIREKTE Nutzerevidenz. `motor_match` ist das
    # Ergebnis von find_motor(baureihe, req.motor) und damit die von der Nutzer-
    # angabe BESTÄTIGTE Verkaufsbezeichnung — sie bleibt hart. Der MOTORCODE ist
    # dagegen ein eigenes DB-Feld, das der Nutzer nie nennt; er steckt jetzt in
    # `motorcode_tokens` und taucht hier bewusst NICHT mehr auf. Genau darüber
    # aktivierte der Opel Insignia B (F20DTH) früher die harte Prüfung, obwohl der
    # Nutzer nur "2.0 Diesel 174 PS" angegeben hatte.
    motor_hart = bool(motor_user_tokens or ziel_motor_tokens)

    # §5/§6: Positiver Modellanker für das Sicherheitsgate. Zuerst die konkrete
    # NUTZERANGABE ("Insignia Grand Sport"). Fehlt sie, tritt die Selbstbezeichnung
    # der Zielbaureihe ein — das ist kein Fremdmodell-Lexikon, sondern der Name des
    # gesuchten Fahrzeugs selbst, und er wird ausschließlich POSITIV geprüft
    # (Karte nennt ihn -> Evidenz; Karte nennt ihn nicht -> keine Evidenz).
    modell_anker = _modell_anker(str(getattr(req, "marke", "") or ""),
                                 str(getattr(req, "modell", "") or ""))
    if not modell_anker:
        # §4/§5: Nur OHNE direkten Nutzeranker tritt die Selbstbezeichnung der
        # Zielbaureihe ein, ergänzt um alle STRUKTURIERTEN Zielbezeichnungen
        # ("c200", "320d") — nötig für Modellnamen, deren Wortform nichts hergibt
        # (aus "C-Klasse" wird sonst der Anker 'klasse', während die Inserate
        # "C200" schreiben). Generische Wörter aus `modell_tokens` ('diesel',
        # 'turbo', 'facelift') bleiben draußen; sie träfen jede Fremdanzeige.
        #
        # Hat der Nutzer sein Modell dagegen SELBST genannt, ist das direkte
        # Evidenz und bleibt allein maßgeblich. Früher lief dieses Supplement
        # immer mit; bei Audi (wo der direkte Anker mangels Kurzcode-Erkennung
        # leer blieb) übernahm dadurch ein zufälliger Performance-Variantenname
        # aus der ungeprüften DB die Rolle des Zielmodells — 'rs3' für ein A3-
        # Ziel, 'rs4' für ein A4-Ziel. Ein Variantenname darf den ausdrücklich
        # genannten Basismodellnamen weder ersetzen noch erweitern.
        modell_anker = _modell_anker(str((baureihe or {}).get("marke") or ""),
                                     str((baureihe or {}).get("modell") or ""))
        modell_anker |= {t for t in modell_tokens
                         if len(t) >= 3 and any(c.isdigit() for c in t)
                         and any(c.isalpha() for c in t)}

    # §Trust/§14: die Liste der ÜBRIGEN Motorvarianten stammt aus der ungeprüften
    # DB. Sie darf nicht mehr allein hart verwerfen — die direkte Bezeichnung im
    # Inserat (_fremde_bezeichnungen_im_text) übernimmt diese Aufgabe evidenzbasiert.
    fremd_motor_tokens: set[str] = set()
    if ziel_motor_tokens and ziel_id and is_verified(baureihe, "motorvarianten"):
        for m in alle_motorvarianten or []:
            if m.get("baureihe_id") == ziel_id:
                fremd_motor_tokens |= _motor_tokens(m.get("bezeichnung", ""))
                fremd_motor_tokens |= _motor_tokens(m.get("motorcode") or "")
        fremd_motor_tokens -= ziel_motor_tokens

    # Zielleistung. §5/§Trust: Die EXPLIZITE Nutzerangabe hat Vorrang vor der
    # ungepruefeten DB-Motorvariante. Frueher gewann die DB — bei einer falsch
    # aufgeloesten Baureihe (belegt an `vw-golf-8`: einzige Variante 2.0 TSI /
    # 245 PS) wurde damit die Nutzerangabe "150 PS" still auf 245 PS gehoben und
    # anschliessend jedes korrekte Inserat wegen "abweichender Motorleistung"
    # verworfen. Prioritaet: expliziter Userinput > DB-Fallback.
    leistung_user = _ps_im_text(" ".join(
        str(getattr(req, f, "") or "") for f in ("motor", "leistung_ps", "modell")))
    leistung_ps = leistung_user
    if not leistung_ps:
        try:
            leistung_ps = int((motor_match or {}).get("leistung_ps") or 0) or None
        except (TypeError, ValueError):
            leistung_ps = None
    # §Trust/§15: harte Leistungsablehnung nur mit Nutzerangabe oder verifizierter DB.
    leistung_hart = bool(leistung_user) or is_verified(baureihe, "motorvarianten")

    # Zielkarosserie: NUR aus den Angaben des Nutzers ableiten. Das DB-Feld
    # `karosserie` listet alle Karosserien der Baureihe (z.B. Limousine UND Kombi)
    # und ist deshalb kein Zielwert — außer die Baureihe kennt genau eine.
    body_quelle = " ".join(str(getattr(req, f, "") or "") for f in
                           ("karosserie", "modell", "inserat_titel", "motor"))
    karosserie = _karosserie_im_text(body_quelle)
    if not karosserie and baureihe:
        db_karosserien = baureihe.get("karosserie")
        if isinstance(db_karosserien, (list, tuple)) and len(db_karosserien) == 1:
            karosserie = _karosserie_im_text(str(db_karosserien[0]))

    # Facelift-Grenze (§6): aus dem freien DB-Text der Baureihe, kein Hardcoding.
    facelift_jahr = _jahr_aus_text((baureihe or {}).get("facelift_merkmale") or "")

    # ── Chassiscode -> Karosserie der Zielbaureihenfamilie ────────────────────
    # Erlaubt es, den Generationscode eines Inserats abzuleiten, das ihn nicht
    # selbst nennt ("Limousine" -> G20, "Touring" -> G21). Die Karosseriewerte
    # werden über die ZENTRALE Normalisierung geführt, damit Touring==Kombi und
    # Cabrio==Cabriolet gelten, ohne eine zweite Synonymliste zu pflegen.
    # Kollabieren dabei zwei Codes auf dieselbe normalisierte Karosserie (z.B.
    # "Coupé" und "Gran Coupé" -> beide "coupe"), bleibt die Ableitung für diese
    # Familie folgenlos — die Eindeutigkeitsprüfung in _bewerte schlägt dann fehl.
    chassis_codes: dict[str, str] = {}
    for code, karo in ((baureihe or {}).get("chassis_codes") or {}).items():
        norm = _karosserie_im_text(str(karo))
        if norm:
            chassis_codes[str(code).lower()] = norm

    # ── Fahrzeugvarianten (§Identität) ───────────────────────────────────────
    # Getrennt von `modell_tokens` (Familie + Motor gemischt) und von
    # `ziel_motor_tokens` (Motor). `familie_tokens` führt die Familienebene
    # ausdrücklich für sich — heute nur diagnostisch, damit die Ebenen im Zielprofil
    # sichtbar sind und die Variantenlogik nicht erneut Motorwissen aufbauen muss.
    familie_tokens = _modell_tokens((baureihe or {}).get("modell", ""))
    ziel_varianten = {v.strip().lower() for v in _karosserie_liste(baureihe or {})
                      if len(v.strip()) >= _VARIANTE_MIN_LEN}
    eigener_teil = _variantenteil((baureihe or {}).get("modell", ""))
    if eigener_teil:
        ziel_varianten.add(eigener_teil)
    ziel_body_norm = {n for n in (_karosserie_im_text(v) for v in ziel_varianten) if n}
    # §Trust/§13: die erlaubte Variantenmenge des Ziels stammt aus dem ungeprüften
    # `karosserie`-Array. Wäre sie unvollständig, würde eine EIGENE Variante als
    # fremd gelten und ein korrektes Inserat hart fliegen. Ohne Verifikation bleibt
    # die Variantenprüfung deshalb inaktiv.
    if is_verified(baureihe, "karosserie"):
        fremd_varianten = _variantenvokabular(alle_baureihen) - ziel_varianten
    else:
        fremd_varianten = set()

    # §5/§Trust: Nutzerangabe zuerst. Vorher stand die DB-Motorvariante vorn —
    # eine falsch aufgeloeste Baureihe konnte damit ein ausdrueckliches "Diesel"
    # des Nutzers in "Benzin" verwandeln (P0-Befund `vw-golf-8`, dessen einzige
    # Variante ein 2.0-TSI-Benziner ist). Danach haette die harte
    # Kraftstoffpruefung jedes echte Diesel-Inserat verworfen.
    kraftstoff = getattr(req, "kraftstoff", None) or (motor_match or {}).get("kraftstoff")
    # §Trust/§15: dasselbe Prinzip für den Kraftstoff.
    kraftstoff_hart = bool(getattr(req, "kraftstoff", None)) or is_verified(
        baureihe, "motorvarianten")
    return {
        "generation_tokens": gen,
        "fremd_generationen": fremd,
        "modell_tokens": modell_tokens,
        "fremd_modelle": fremd_modelle,
        "familie_tokens": familie_tokens,
        "ziel_varianten": ziel_varianten,
        "ziel_body_norm": ziel_body_norm,
        "fremd_varianten": fremd_varianten,
        "marke_tokens": marke_tokens,
        "ziel_num": ziel_num,
        "fremd_num": fremd_num,
        "marke_name": (baureihe or {}).get("marke") or getattr(req, "marke", None),
        "modell_name": (baureihe or {}).get("modell") or getattr(req, "modell", None),
        "ziel_motor_tokens": ziel_motor_tokens,
        "motorcode_tokens": motorcode_tokens,
        "modell_anker_user": modell_anker,
        "fremd_motor_tokens": fremd_motor_tokens,
        "leistung_ps": leistung_ps,
        "karosserie": karosserie,
        "facelift_jahr": facelift_jahr,
        # §Trust/§12: die Chassiscode-Zuordnung ist die einzige POSITIVE DB-Inference
        # ("Limousine also G20"). Sie wurde im Projekt geprüft, aber ohne
        # gespeicherte Quelle — das reicht nach unserer eigenen Regel nicht für
        # harte Wirkung. Ohne Verifikation bleibt die Generation unbekannt, sofern
        # das Inserat sie nicht selbst nennt.
        "chassis_codes": chassis_codes if is_verified(baureihe, "chassis_codes") else {},
        "modell_evidenz_user": _modell_evidenz_user(req),
        "modell_kennungen_user": _modell_kennungen_user(req, gen | fremd),
        "motor_hart": motor_hart,
        "leistung_hart": leistung_hart,
        "kraftstoff_hart": kraftstoff_hart,
        "baujahr": getattr(req, "baujahr", None),
        "kilometerstand": getattr(req, "kilometerstand", None),
        "kraftstoff": kraftstoff,
    }


def prompt_block(ma: Marktanalyse | None) -> str:
    """Kompakter, VERBINDLICHER Marktvergleich-Block für den LLM-Prompt — damit der
    Bericht dieselben (deterministisch berechneten) Zahlen nennt und keine eigene
    Spanne erfindet. Leer, wenn keine belastbare Analyse vorliegt."""
    if not ma or not ma.median_eur:
        return ""
    lines = [
        "=== DETERMINISTISCHER MARKTVERGLEICH (Backend-berechnet — VERBINDLICH) ===",
        f"Robust aus {ma.verwendet} vergleichbaren Web-Preisangaben berechnet "
        f"({ma.anzahl_sehr_aehnlich} sehr ähnlich, {ma.anzahl_aehnlich} ähnlich):",
        f"- Median-Marktwert: {ma.median_eur} €",
        f"- Typischer Marktbereich (robuste Quartile): {ma.spanne_min_eur}–{ma.spanne_max_eur} €",
        f"- Datenqualität dieser Basis: {ma.datenqualitaet}",
        # §2/§9: Datenqualität und Marktabdeckung getrennt ausweisen — "gute Daten,
        # aber nur eine Plattform" ist eine andere Aussage als "gute Daten aus dem
        # ganzen Markt", und der Bericht darf das nicht verwischen.
        f"- Marktabdeckung: {ma.marktabdeckung} ({ma.anzahl_domains} Plattform"
        f"{'en' if ma.anzahl_domains != 1 else ''}: {', '.join(ma.quellen_domains) or 'unbekannt'})",
        f"Verwende GENAU diese Spanne: setze marktpreis_min={ma.spanne_min_eur} und "
        f"marktpreis_max={ma.spanne_max_eur}. Erfinde KEINE davon abweichende Spanne. "
        f"Nenne im Bericht den Median und den typischen Marktbereich; leite die "
        f"Preisbewertung aus der Lage des Angebotspreises zu dieser Spanne ab.",
    ]
    return "\n".join(lines)


# ── Deduplizierung (§4 / §Identität) ─────────────────────────────────────────
# Der frühere Ablauf deduplizierte über zwei Schlüssel gleichzeitig und nach dem
# Prinzip "wer zuerst kommt, gewinnt". Der forensische Audit hat gezeigt, was das
# kostet: ein anonymes, als ungeeignet bewertetes Textfragment besetzte den
# Fahrzeug-Fingerabdruck (Preis+km+Baujahr) und blockierte damit dauerhaft das
# später gefundene, vollständig identifizierte Inserat mit Detail-Link und
# explizitem Generationsbeleg. Drei echte G20-Vergleiche gingen so ersatzlos
# verloren (3484786731, 3484778742, 3480860991) — und der Blocker selbst trug
# nie etwas zum Median bei.
#
# Neue Ordnung:
#   1. STABILE Identität (Anzeigen-ID, sonst kanonische Detail-URL) schlägt alles.
#      Zwei verschiedene stabile IDs sind zwei verschiedene Inserate — auch bei
#      identischem Preis, Kilometerstand und Baujahr.
#   2. Der Fingerabdruck bleibt Dublettenbremse, aber NUR für Beobachtungen ohne
#      stabile Identität. Er kann ein identifiziertes Inserat nicht mehr verdrängen.
#   3. Gehören mehrere Beobachtungen zu derselben stabilen Identität, gewinnt der
#      qualitativ beste Repräsentant — nicht der erste.

_CONFIDENCE_RANG = {"high": 2, "medium": 1, "low": 0}


def _fingerabdruck(b: Preisbeobachtung) -> tuple:
    return (b.preis_eur, b.kilometerstand, b.baujahr)


def _identitaets_key(b: Preisbeobachtung) -> str | None:
    """Stabile Identität einer Beobachtung — oder None, wenn sie keine hat.

    Die Anzeigen-ID hat Vorrang vor der Detail-URL; trägt eine URL erkennbar eine
    Anzeigen-ID, wird auf den ID-Schlüssel normalisiert. Sonst gälten "id:…" und
    "url:…" derselben Anzeige als zwei Inserate.
    """
    if b.listing_id:
        return f"id:{b.quelle_domain}:{b.listing_id}"
    if b.detail_url:
        aus_url = _listing_id_aus_url(b.detail_url)
        if aus_url:
            return f"id:{b.quelle_domain}:{aus_url}"
        return f"url:{_kanonische_url(b.detail_url)}"
    return None


def _repraesentant_rang(b: Preisbeobachtung) -> tuple:
    """Deterministische QUALITÄTSordnung für die Repräsentantenwahl (§4).

    Bewertet AUSSCHLIESSLICH Daten- und Strukturqualität. Bewusst NICHT enthalten:
    `vergleichbarkeit`, `similarity`, Preisnähe zum Median, Zielgeneration — und
    auch `generation_evidence` nicht, denn "explicit_card" wird nur gesetzt, wenn
    die Karte die ZIELgeneration nennt. Jedes dieser Merkmale würde den Gewinner
    danach auswählen, wie gut er ins gewünschte Marktbild passt, und die Statistik
    zu unseren Gunsten verzerren.
    """
    return (
        1 if b.listing_id else 0,
        1 if b.detail_url else 0,
        # "api_structured" (Etappe 3) steht hier gleichrangig neben "detail_link":
        # beides heißt "dieser Datenpunkt gehört nachweislich zu genau EINEM
        # Inserat". Ohne diese Gleichstellung verlöre ein strukturierter
        # API-Datensatz die Repräsentantenwahl gegen einen aus HTML gelesenen —
        # obwohl er die stärkere Herkunft hat.
        1 if b.segmentation_method in ("detail_link", "api_structured") else 0,
        _CONFIDENCE_RANG.get(b.structural_confidence, 0),
        0 if b.window_fallback_used else 1,
        1 if b.body_evidence in _BODY_EVIDENCE_VERTRAUT else 0,
        sum(1 for w in (b.baujahr, b.kilometerstand, b.body, b.fuel,
                        b.horsepower, b.transmission, b.engine_variant)
            if w is not None),
    )


_KONFLIKT_FELDER = ("generation", "body", "preis_eur")


def _identitaets_konflikte(gruppen: dict) -> list[dict]:
    """Widersprüche zwischen Beobachtungen DERSELBEN stabilen Anzeige.

    Bewusst nur dokumentierend (§5): es wird nichts stillschweigend zurechtgebogen
    und keine Konflikt-Engine gebaut. Der Repräsentant wird weiterhin rein nach
    Datenqualität gewählt — der Konflikt wird sichtbar gemacht.
    """
    konflikte: list[dict] = []
    for key, gruppe in gruppen.items():
        if len(gruppe) < 2:
            continue
        for feld in _KONFLIKT_FELDER:
            werte = {getattr(b, feld) for b in gruppe if getattr(b, feld) is not None}
            if len(werte) > 1:
                konflikte.append({"listing_key": key, "feld": feld,
                                  "werte": sorted(str(w) for w in werte)})
    return konflikte


def _dedupliziere(bewertet: list[Preisbeobachtung]) -> tuple[list[Preisbeobachtung],
                                                            list[dict]]:
    """Beobachtungen auf je ein Inserat zusammenführen. Reihenfolge des ERSTEN
    Auftretens bleibt erhalten (nachgelagerte Deckelungen hängen daran)."""
    gruppen: dict[str, list[Preisbeobachtung]] = {}
    ohne_identitaet: list[Preisbeobachtung] = []
    plaetze: list[tuple[str, object]] = []
    for b in bewertet:
        key = _identitaets_key(b)
        if key:
            if key not in gruppen:
                gruppen[key] = []
                plaetze.append(("id", key))
            gruppen[key].append(b)
        else:
            plaetze.append(("anonym", len(ohne_identitaet)))
            ohne_identitaet.append(b)

    # max() liefert bei Gleichstand das ERSTE Maximum -> Ergebnis bleibt stabil.
    vertreter = {k: max(v, key=_repraesentant_rang) for k, v in gruppen.items()}
    fp_identifiziert = {_fingerabdruck(b) for b in vertreter.values()}

    uniq: list[Preisbeobachtung] = []
    gesehen_fp: set[tuple] = set()
    gesehen_karte: set[str] = set()
    for art, ref in plaetze:
        if art == "id":
            uniq.append(vertreter[ref])
            continue
        b = ohne_identitaet[ref]
        # Dieselbe strukturell abgegrenzte Karte darf nur einmal zählen.
        karten_key = b.listing_key or ""
        if karten_key.startswith("card:"):
            if karten_key in gesehen_karte:
                continue
            gesehen_karte.add(karten_key)
        fp = _fingerabdruck(b)
        # Fingerabdruck-Bremse: ein anonymes Fragment zählt nicht zusätzlich, wenn
        # dasselbe Fahrzeug bereits identifiziert vorliegt oder ein anderes anonymes
        # Fragment es schon abgedeckt hat. Umgekehrt gilt das NICHT mehr.
        if fp in fp_identifiziert or fp in gesehen_fp:
            continue
        gesehen_fp.add(fp)
        uniq.append(b)

    konflikte = _identitaets_konflikte(gruppen)
    for k in konflikte:
        log.warning("Identitaetskonflikt %s: %s = %s", k["listing_key"], k["feld"],
                    k["werte"])
    return uniq, konflikte


def analysiere_markt(web_results: list[dict], ziel: dict, angebot_eur: int | None) -> Marktanalyse:
    """Baut die deterministische Marktanalyse aus den rohen Tavily-Treffern.

    `ziel` erwartet: generation_tokens:set, fremd_generationen:set, baujahr:int|None,
    kilometerstand:int|None, kraftstoff:str|None. `angebot_eur` = Angebots-/Wunschpreis.
    """
    roh: list[Preisbeobachtung] = []
    for r in web_results or []:
        url = r.get("url", "") or ""
        # §9: Informations-/Ratgeber-/Nachschlage-Seiten (z.B. fahrzeugschein.de) sind
        # KEINE konkreten Vergleichsinserate — ihre Preisangaben zählen nicht in den
        # Median. Sie bleiben höchstens Hintergrundquelle (Kontext/belege), tragen aber
        # keinen Marktdatenpunkt bei.
        if _ist_info_domain(url):
            continue
        titel = r.get("title", "") or ""
        # §5: Zielt die Seite auf Ersatzteile/Zubehör statt auf ein Fahrzeug, liefert
        # sie KEINE Marktbeobachtung — unabhängig davon, wie sauber die Preise
        # darauf extrahierbar wären.
        if ist_teile_suchseite(url, titel):
            continue
        # §11: Herkunftsart EINMAL pro Seite bestimmen (Einzelinserat vs. Kategorie-/
        # Suchseite vs. unbekannt) — bestimmt später, ob die daraus extrahierten
        # Datenpunkte Richtung Quellenvielfalt/HIGH zählen dürfen.
        if _ist_einzelinserat(url, titel):
            source_type = "listing"
        elif _ist_kategorieseite_intern(url, titel):
            # §3: Eine Suchergebnisseite eines ECHTEN Marktplatzes zeigt einzelne,
            # real inserierte Fahrzeugkarten und darf mehrere Beobachtungen liefern —
            # sofern die Karten sauber getrennt extrahierbar sind (siehe
            # _zaehlt_als_fahrzeug). Eine Kategorie-/Filterseite einer Aggregator-
            # oder Ratgeberdomain (12gebrauchtwagen.de u.ä.) darf das NICHT.
            source_type = "market_category" if _ist_marktplatz_domain(url) else "category"
        else:
            source_type = "unknown"
        # Raw-Content (falls angefordert) mitverwenden — mehr Text = mehr extrahierbare
        # Preis-Datenpunkte. Groß gedeckelt gegen pathologische Seitengrößen.
        raw = (r.get("raw_content") or "")[:20_000]
        inhalt = r.get("content", "") or ""
        text = f"{titel}\n{inhalt}\n{raw}"
        # Abschnittsgrenzen im zusammengesetzten Text -> extraction_source je Datenpunkt (§3).
        grenzen = (len(titel) + 1, len(titel) + 1 + len(inhalt) + 1)
        # §Phase 2: die enge Zeichen-Fenster-Zuordnung (_FENSTER=130, s.o.) ist die
        # bestehende Absicherung dafür, dass km/Baujahr eines Preises NICHT einem
        # NACHBAR-Fahrzeug im selben Snippet zugeordnet werden — ein Snippet mit
        # mehreren sauber je-Preis attribuierten Fahrzeugen (z.B. eine Portal-
        # Trefferliste mit "Preis X km Y EZ Z . Preis X2 km Y2 EZ Z2 . ...") bleibt
        # daher weiterhin je Fahrzeug einzeln zählbar (das ist die tragende
        # Sprint-3-Empirie, ohne die kaum ein Fahrzeug HOCH/MITTEL erreichen würde).
        # Der eigentliche Aggregator-/Kategorie-Fall (12gebrauchtwagen.de u.ä. —
        # eine Modell-/Motor-Suchseite OHNE einzeln geprüfte Inserate) wird bereits
        # auf URL-Ebene erkannt (ist_kategorieseite/ist_generische_suchseite) und
        # dort als source_type="category" markiert, s.o.
        # §5: Karosserie-Signal der SEITE (URL + Titel) — eine nach Kombi gefilterte
        # Trefferliste beschreibt jede Karte darauf als Kombi, auch wenn die Karte es
        # nicht wiederholt. Nur bei Eindeutigkeit, und nur als Rückfall.
        seiten_body = _eindeutige_karosserie(f"{url} {titel}")
        roh.extend(_extrahiere_aus_text(text, url, source_type, grenzen=grenzen,
                                        seiten_body=seiten_body))

    bewertet = [_bewerte(b, ziel) for b in roh]
    uniq, _konflikte = _dedupliziere(bewertet)

    nutzbar = [b for b in uniq if _zaehlt_als_fahrzeug(b)]
    hintergrund = [b for b in uniq if not _zaehlt_als_fahrzeug(b)]
    hintergrund_domains: list[str] = []
    for b in hintergrund:
        if b.quelle_domain and b.quelle_domain not in hintergrund_domains:
            hintergrund_domains.append(b.quelle_domain)

    sehr = [b for b in nutzbar if b.vergleichbarkeit == "sehr_aehnlich"]
    aehn = [b for b in nutzbar if b.vergleichbarkeit == "aehnlich"]
    bedingt = [b for b in nutzbar if b.vergleichbarkeit == "bedingt"]

    # ── Preisstatistik: "bedingt" darf sie nicht verzerren (§A/§B) ───────────
    # A) Gibt es MINDESTENS DREI gute Beobachtungen (sehr ähnlich + ähnlich), werden
    #    Median, Quartile und Marktspanne AUSSCHLIESSLICH aus diesen berechnet.
    #    "bedingt" passende Punkte bleiben reiner Kontext und verändern die
    #    Preisstatistik nicht — ein einzelner zweifelhafter Billigtreffer soll die
    #    Untergrenze nicht nach unten ziehen.
    # B) Nur bei WENIGER als drei guten Beobachtungen springt "bedingt" als Fallback
    #    ein — dann aber erst nach einer eigenen Plausibilitätsprüfung
    #    (_plausible_bedingte) und mit Deckelung des Gesamtvertrauens auf
    #    completed_medium (siehe fallback_bedingt / marktrecherche.research_status).
    kandidaten = sehr + aehn
    fallback = False
    if len(kandidaten) < 3:
        zugelassen = _plausible_bedingte(bedingt, kandidaten)
        if zugelassen:
            kandidaten = kandidaten + zugelassen
            fallback = True

    # Rausch-Reduktion (TIGHTENING der Relevanz, KEIN Loosening): Datenpunkte OHNE
    # jedes Attribut (weder Baujahr NOCH km extrahierbar) stammen oft aus Übersichts-
    # zeilen ohne echten Fahrzeugbezug und verbreitern die Preisspanne künstlich —
    # was den Streuungs-Guard fälschlich auslöst und ein populäres Fahrzeug auf
    # "niedrig" drückt. Tragen genügend Datenpunkte ein Attribut, werden die
    # attribut-losen verworfen (nur wenn danach noch eine belastbare Basis bleibt).
    mit_attr = [b for b in kandidaten if b.baujahr is not None or b.kilometerstand is not None]
    if len(mit_attr) >= 5:
        kandidaten = mit_attr

    # §9/§12: Beitrag einer einzelnen (oft aggregierenden) Rechercheseite deckeln,
    # damit eine verrauschte Übersichtsseite die Statistik nicht dominiert.
    kandidaten = _cap_pro_url(kandidaten)

    # Robusten Marktkern bilden (MAD + Sanity-Band) — bevor gezählt/gemittelt/angezeigt wird.
    verwendet, _entfernt = _trim_ausreisser(kandidaten)

    # §A: "bedingt" passende Beobachtungen, die die Preisstatistik NICHT beeinflusst
    # haben, bleiben als transparenter Kontext erhalten (gefunden, aber bewusst nicht
    # eingerechnet) — sie tauchen nie in Median/Quartilen/Datenqualität auf.
    verwendet_keys = {id(b) for b in verwendet}
    kontext = [b for b in bedingt if id(b) not in verwendet_keys]

    # Domains NUR der tatsächlich verwendeten Vergleiche (keine Quelle nennen, die
    # nichts Verwertbares beigetragen hat) — order-preserving dedupliziert.
    domains: list[str] = []
    for b in verwendet:
        if b.quelle_domain and b.quelle_domain not in domains:
            domains.append(b.quelle_domain)

    ma = Marktanalyse(
        gefunden=len(uniq),
        # Kategorie-Zähler beschreiben die TATSÄCHLICH verwendeten (post-Trim)
        # Vergleiche — nicht die Rohmenge (ehrliche "X sehr ähnlich · Y ähnlich").
        anzahl_sehr_aehnlich=sum(1 for b in verwendet if b.vergleichbarkeit == "sehr_aehnlich"),
        anzahl_aehnlich=sum(1 for b in verwendet if b.vergleichbarkeit == "aehnlich"),
        anzahl_bedingt=sum(1 for b in verwendet if b.vergleichbarkeit == "bedingt"),
        angebot_eur=angebot_eur,
        quellen_domains=domains,
        hintergrund_domains=hintergrund_domains,
        # §2/§10: getrennt von der Datenqualität geführt.
        anzahl_domains=len(domains),
        marktabdeckung=_marktabdeckung(domains),
        # §A/§B: griff der "bedingt"-Fallback, und was blieb reiner Kontext?
        fallback_bedingt=fallback,
        kontext_beobachtungen=kontext,
    )

    def _unzuverlaessig(grund: str) -> Marktanalyse:
        # KEINE Scheinpräzision. Median/Spanne bleiben None; die Datenpunkte werden
        # dennoch als (schwache) Beobachtungen ausgewiesen (Transparenz).
        ma.verwendet = len(verwendet)
        ma.datenqualitaet = "niedrig"
        ma.methode = grund
        ma.beobachtungen = verwendet
        return ma

    if len(verwendet) < 3:
        return _unzuverlaessig(
            "Zu wenige vergleichbare Preisangaben aus der Websuche für eine belastbare "
            "Median-/Quartils-Berechnung — Marktanalyse auf begrenzter Datenbasis."
        )

    preise = [b.preis_eur for b in verwendet]
    median = _r100(statistics.median(preise))
    lo, hi = _typischer_bereich(preise)

    # Plausibilitätsprüfung Streuung: ist der typische Marktbereich relativ zum
    # Median zu breit, sind die Datenpunkte in Wahrheit nicht kohärent (Fehl-
    # Assoziation / gemischte Preisarten). Dann ehrlich als unzuverlässig ausweisen
    # statt einen bedeutungslosen Median zu präsentieren.
    if median and (hi - lo) / median > _MAX_REL_SPANNE:
        return _unzuverlaessig(
            f"Die gefundenen Vergleichspreise streuen zu stark "
            f"({lo:,}–{hi:,} €) für einen belastbaren Marktwert — die Web-Datenbasis "
            f"ist uneinheitlich (z.B. gemischte Angebots-/Finanzierungspreise). "
            f"Nur grobe Orientierung möglich.".replace(",", ".")
        )

    ma.verwendet = len(verwendet)
    ma.median_eur = median
    ma.spanne_min_eur = lo
    ma.spanne_max_eur = hi
    ma.datenqualitaet = _datenqualitaet(verwendet, median, lo, hi)
    ma.beobachtungen = verwendet
    if angebot_eur:
        ma.differenz_eur = angebot_eur - median
        ma.differenz_pct = round((angebot_eur - median) / median * 100, 1)
    ma.methode = (
        f"Median und typischer Marktbereich (unteres–oberes Quartil, ausreißer-robust) "
        f"aus {len(verwendet)} vergleichbaren Preisangaben"
        + (" (inkl. bedingt vergleichbarer, da wenige Daten)" if fallback else "")
        + f"; aus {len(uniq)} extrahierten Datenpunkten der Websuche gefiltert."
    )
    return ma
