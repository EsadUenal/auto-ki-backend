from __future__ import annotations

"""
Deterministischer Abgleich des VIRA-Rueckrufbestands gegen den amtlichen
KBA-Fahrzeug-Rueckrufexport.

WAS DIESES MODUL IST
--------------------
Ein reines ANALYSE-Modul. Es liest, vergleicht und klassifiziert — es schreibt
nichts. Datenkorrekturen laufen wie im Recall-Pilot ueber
`app/data_migrations.py`; dieses Modul liefert nur die Entscheidungsgrundlage.

DIE QUELLE
----------
    https://www.kba-online.de/rrdb/buerger/api/rueckruf/export?format=csv&type=cars

Der amtliche Gesamtexport (Semikolon-getrennt, UTF-8, 18 Spalten). Er ist die
EINZIGE zugelassene Wahrheitsquelle fuer diesen Abgleich — insbesondere sind
die bestehenden `rueckruf.kba_referenz`-Werte in VIRA ausdruecklich NICHT
vertrauenswuerdig: der DATA-TRUTH-AUDIT hat gemessen, dass kein einziges
Referenzformat des Bestands echten KBA-Nummern entsprach und 200 Zeilen
markenuebergreifend kollidierende Nummern tragen.

DAS MATCHING-PROBLEM
--------------------
Der KBA-Export nennt Marke und Modell, aber KEINE Generation. `Modell` ist eine
komma-getrennte Namensliste in Grossbuchstaben ("X5, X6", "C-KLASSE",
"ZAFIRA, VIVARO"). VIRA dagegen fuehrt Baureihen MIT Generation
(mercedes-benz-c-klasse-w205). Die Generation muss deshalb ueber die
UEBERSCHNEIDUNG der Produktionszeitraeume aufgeloest werden — genau die
Mehrdeutigkeit, an der im Recall-Pilot der Insignia-Fall #546 gescheitert ist.

WARUM KEINE TEXTAEHNLICHKEIT ALS WAHRHEIT
------------------------------------------
Eine reine Fuzzy-Similarity ueber Mangeltexte wuerde hier systematisch luegen:
26 % der VIRA-Mangeltexte sind laut Audit byte-identische Wiederholungen, und
Formulierungen wie "Moeglicher Ausfall der Servolenkung" passen auf Dutzende
unabhaengiger Rueckrufe verschiedener Hersteller. Deshalb:

  * Marke und Modell sind HARTE Gates. Ohne sie gibt es keinen Kandidaten.
  * Der Bauzeitraum der BAUREIHE ist ein HARTES Gate (Generationsaufloesung).
  * Der Mangel wird ueber BAUTEILGRUPPEN verglichen, nicht ueber Zeichen-
    aehnlichkeit: eine kuratierte, im Modul vollstaendig sichtbare
    Begriffsliste ordnet beiden Texten Bauteil-/Systemgruppen zu. Nur eine
    Uebereinstimmung in der Gruppe zaehlt als inhaltlicher Treffer.
  * `EXACT` und `CORRECTABLE` verlangen IMMER einen EINDEUTIGEN Kandidaten.
    Bleiben mehrere uebrig, ist das Ergebnis hoechstens `PARTIAL`.
"""

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field

KBA_EXPORT_URL = (
    "https://www.kba-online.de/rrdb/buerger/api/rueckruf/export?format=csv&type=cars"
)

# ── Match-Klassen (Vorgabe des Auftrags) ─────────────────────────────────────
EXACT = "EXACT_OFFICIAL_MATCH"
CORRECTABLE = "CORRECTABLE_MATCH"
PARTIAL = "PARTIAL_MATCH"
CONTRADICTED = "CONTRADICTED"
NO_MATCH = "NO_MATCH"

KLASSEN = (EXACT, CORRECTABLE, PARTIAL, CONTRADICTED, NO_MATCH)


# ── Namensraum-Abbildung VIRA -> KBA ─────────────────────────────────────────
MARKE_MAP = {
    "VOLKSWAGEN": "VW",
    "MERCEDES-AMG": "MERCEDES-BENZ",
}

# VIRA-Modellname -> Menge moeglicher KBA-Modelltokens. Nur die Faelle, in denen
# die Normalisierung ("3er" -> "3", Umlautfaltung) nicht ausreicht. Jeder
# Eintrag ist gegen die tatsaechlich im Export vorkommenden Tokens geprueft; wo
# der Export das Modell gar nicht kennt, steht bewusst NICHTS — dann bleibt die
# Zeile NO_MATCH, statt auf ein aehnliches Modell zu rutschen.
MODELL_MAP = {
    ("AUDI", "RS 3 SPORTBACK"): {"RS 3", "RS3", "A3"},
    ("AUDI", "RS 4 AVANT"): {"RS4", "A4"},
    ("AUDI", "RS 6"): {"RS6", "A6"},
    ("AUDI", "RS 6 AVANT"): {"RS6", "A6"},
    ("AUDI", "RS 7 SPORTBACK"): {"RS7", "A7"},
    ("AUDI", "TT RS"): {"TT"},
    ("BMW", "2ER ACTIVE TOURER"): {"2"},
    ("BMW", "2ER COUPE"): {"2", "M2"},
    ("MERCEDES-BENZ", "GT"): {"AMG GT"},
    ("MERCEDES-BENZ", "CLA SHOOTING BRAKE"): {"CLA", "CLA-KLASSE"},
    ("MERCEDES-BENZ", "GL-KLASSE"): {"GL"},
    ("MERCEDES-BENZ", "GLE COUPE"): {"GLE"},
    ("MERCEDES-BENZ", "ML-KLASSE"): {"ML", "M-KLASSE"},
    ("OPEL", "ZAFIRA LIFE"): {"ZAFIRA"},
    # Skoda Enyaq kommt im Export nicht vor -> bewusst kein Mapping.
}


# ── Bauteil-/Systemgruppen ───────────────────────────────────────────────────
#
# Kuratiert, vollstaendig sichtbar, bewusst grob. Eine Gruppe gilt als
# getroffen, wenn EINES ihrer Muster im gefalteten Text vorkommt. Verglichen
# wird die Schnittmenge der Gruppen, nicht die Zeichenaehnlichkeit — "Ausfall
# der Servolenkung" und "Bruch der Spurstange" landen so in verschiedenen
# Gruppen, obwohl beide zum Lenkbereich gehoeren.
BAUTEILGRUPPEN: dict[str, tuple[str, ...]] = {
    "airbag": ("airbag", "gasgenerator", "rueckhaltesystem", "gurtstraffer"),
    "gurt": ("gurt", "gurtschloss", "sicherheitsgurt"),
    "bremse_hydr": ("bremsschlauch", "bremsleitung", "hauptbremszylinder",
                    "bremsfluessigkeit", "bremskraftverstaerker",
                    "bremskraftausgleich", "bremskraftverteilung"),
    "bremse_mech": ("bremsscheibe", "bremsbelag", "bremssattel", "bremspedal",
                    "feststellbremse", "handbremse"),
    "bremse_elektr": ("abs", "esp", "bremssteuermodul", "ebcm", "bremsregelung",
                      "stabilitaetsprogramm"),
    "lenkung": ("lenkung", "servolenkung", "lenkgetriebe", "lenkrad",
                "lenksaeule", "lenkspindel", "lenkunterstuetzung"),
    "fahrwerk": ("spurstange", "querlenker", "federbein", "stossdaempfer",
                 "achsschenkel", "radaufhaengung", "traggelenk"),
    "rad": ("felge", "radschraube", "radbolzen", "radnabe", "radlager",
            "reifen", "radverschraubung"),
    "motor_mech": ("kolben", "pleuel", "kurbelwelle", "nockenwelle",
                   "steuerkette", "zahnriemen", "ventil", "zylinderkopf"),
    "motor_oel": ("oelleitung", "oelrueckl", "oelzulauf", "oelaustritt",
                  "oelwanne", "motoroel"),
    "kraftstoff": ("kraftstoff", "benzinleitung", "dieselleitung", "tank",
                   "einspritz", "kraftstoffpumpe", "kraftstoffleitung"),
    "abgas": ("abgas", "nox", "abschalteinrichtung", "partikelfilter",
              "katalysator", "agr", "emission"),
    "turbo": ("turbolader", "ladeluft"),
    "kuehlung": ("kuehlmittel", "kuehler", "wasserpumpe", "thermostat"),
    "elektrik_brand": ("kurzschluss", "brandgefahr", "schmoren", "ueberhitz",
                       "masseverbindung", "kabelbaum", "relais", "sicherung"),
    "hochvolt": ("hochvolt", "hochspannung", "traktionsbatterie",
                 "antriebsbatterie", "hv batterie"),
    "getriebe": ("getriebe", "kupplung", "waehlhebel", "parksperre",
                 "kardanwelle", "antriebswelle"),
    "karosserie": ("karosserie", "heckklappe", "motorhaube", "schiebedach",
                   "windschutzscheibe", "verklebung", "sitz"),
    "licht": ("scheinwerfer", "ruecklicht", "blinker", "beleuchtung",
              "bremsleuchte", "fahrtrichtungsanzeiger"),
    "software": ("software", "steuergeraet", "programmierung", "codierung",
                 "kalibrierung"),
    "notruf": ("ecall", "notruf", "kommunikationsmodul"),
    "anhaenger": ("anhaengevorrichtung", "anhaengerkupplung", "kupplungskugel"),
}


# SCHWACHE Gruppen benennen kein Bauteil, sondern ein Symptom ("Kurzschluss",
# "Brandgefahr") oder eine Querschnittstechnik ("Software", "Steuergeraet").
# Sie kommen in hunderten voellig unabhaengiger Rueckrufe vor und duerfen
# deshalb ALLEIN keinen inhaltlichen Treffer begruenden.
#
# Der erste Validierungslauf gegen die 15 handgeprueften Pilotzeilen hat genau
# das belegt: ueber die Gruppe "software" wurde der Servolenkungs-Rueckruf des
# Insignia (#543) faelschlich einem Lambda-Wert-Rueckruf zugeordnet, ueber
# "elektrik_brand" der Sitzheizungs-Rueckruf (#547) einem Tankband-Rueckruf.
# Beide Zuordnungen waeren als CORRECTABLE_MATCH in die Datenbank gelaufen.
SCHWACHE_GRUPPEN = frozenset({"software", "elektrik_brand", "karosserie", "rad"})

STARKE_GRUPPEN = frozenset(set(BAUTEILGRUPPEN) - SCHWACHE_GRUPPEN)

# Stoppwoerter fuer den distinktiven Tokenvergleich: haeufige Rueckruf-Floskeln,
# die zwischen zwei beliebigen Datensaetzen immer uebereinstimmen wuerden.
# Zwei Sorten Stoppwoerter, beide noetig:
#   (a) Satzbau-Floskeln, die in jedem zweiten Rueckruftext stehen;
#   (b) generische QUALITAETSADJEKTIVE und Querschnittstechnik ("fehlerhaft",
#       "software", "update"). Sie benennen kein Bauteil und stiften keine
#       Identitaet. Der Validierungslauf hat das an einem konkreten Fall belegt:
#       der Airbag-Rueckruf #121 wurde allein ueber das gemeinsame Wort
#       "fehlerhafte" einem voellig anderen amtlichen Rueckruf zugeordnet
#       (Steuergeraete-CODIERUNG statt Steuergeraete-BEFESTIGUNG).
_STOPP = frozenset("""
moeglicher moegliche moeglichen moeglichem fahrzeug fahrzeuge fahrzeugen
betroffenen betroffene betroffener betroffenes bestimmten koennen kommen
fuehren fuehrt werden wurde dadurch wodurch aufgrund unter umstaenden einem
einer eines nicht kann ausfall austausch pruefung ueberpruefung ggf
gegebenenfalls erhoehte erhoehten folge daraus infolge sowie diesem diesen
dieser welche welches sich
fehlerhaft fehlerhafte fehlerhaften fehlerhafter fehlerhaftes
mangelhaft mangelhafte mangelhaften mangelhafter mangelhaftes
unzureichend unzureichende unzureichenden unzureichender unzureichendes
defekt defekte defekten defekter defektes moeglicherweise
software update updates aktualisierung funktion funktionen bauteil bauteile
produktionsfehler herstellungsfehler fehler fehlern fehlfunktion
problem probleme problemen schaden schaeden ursache
""".split())


def distinktive_tokens(text: str | None) -> set[str]:
    """Inhaltstragende Woerter ab 6 Zeichen, ohne Rueckruf-Floskeln.

    Exakte Mengen-Schnittmenge, KEINE Zeichenaehnlichkeit: zwei Datensaetze
    teilen ein Token nur dann, wenn beide dasselbe Wort schreiben.
    """
    return {w for w in _falte(text).split()
            if len(w) >= 6 and w not in _STOPP}


def _falte(text: str | None) -> str:
    """Kleinschreibung, Umlaute aufgeloest, Satzzeichen zu Leerzeichen."""
    t = (text or "").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"),
                 ("ß", "ss"), ("é", "e")):
        t = t.replace(a, b)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", t)


def _falte_modell(text: str | None) -> str:
    """Wie `_falte`, aber der BINDESTRICH bleibt erhalten.

    Der KBA-Export schreibt Modelle als "C-KLASSE", "GLC-KLASSE", "T-ROC",
    "E-TRON". Wuerde der Bindestrich wie in Fliesstext zu einem Leerzeichen,
    liefe kein einziges dieser Modelle je auf einen Treffer — im ersten
    Validierungslauf waren dadurch alle Mercedes-Klasse-Baureihen faelschlich
    NO_MATCH.
    """
    t = (text or "").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"),
                 ("ß", "ss"), ("é", "e")):
        t = t.replace(a, b)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"[^a-z0-9\-]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# Kurze Muster duerfen NICHT als Teilzeichenkette treffen. "abs" (das
# Bremssystem) steckt sonst in "ABSchaltung", "ABSicherung" und "ABStand";
# "agr" in "AGRegat"; "rad" in "gRADe". Deutsche Komposita verlangen aber
# umgekehrt Teilzeichenketten-Treffer ("bremspedal" in "Bremspedalplatte"),
# deshalb die Laengengrenze statt einer generellen Wortgrenze. Die Grenze bei
# 4 Zeichen ist an den tatsaechlich vorkommenden Mustern gewaehlt: alles
# Kuerzere ist ein Kuerzel (abs, esp, agr, nox, ebcm), alles Laengere ein
# echtes Wort.
_KURZ = 4


def _muster_trifft(muster: str, gefaltet: str, woerter: set[str]) -> bool:
    if len(muster) <= _KURZ:
        return muster in woerter          # nur als ganzes Wort
    return muster in gefaltet             # Kompositum-tauglich


def bauteilgruppen(text: str | None) -> set[str]:
    """Alle Bauteilgruppen, die dieser Text anspricht."""
    t = _falte(text)
    w = set(t.split())
    return {g for g, muster in BAUTEILGRUPPEN.items()
            if any(_muster_trifft(m, t, w) for m in muster)}


def _modelltokens(kba_modell: str | None) -> set[str]:
    return {t.strip().upper() for t in (kba_modell or "").split(",") if t.strip()}


def kba_marke(marke: str) -> str:
    m = (marke or "").strip().upper()
    return MARKE_MAP.get(m, m)


def _vira_modellkandidaten(marke: str, modell: str) -> set[str]:
    """Mit welchen KBA-Modelltokens darf diese VIRA-Baureihe matchen?"""
    km = kba_marke(marke)
    vm = _falte_modell(modell).upper()
    explizit = MODELL_MAP.get((km, vm))
    if explizit:
        return set(explizit)
    kand = {vm}
    ohne_er = re.sub(r"ER$", "", vm)        # BMW "3ER" -> "3"
    if ohne_er != vm and ohne_er:
        kand.add(ohne_er)
    return kand


_JAHR = re.compile(r"\b(?:19|20)\d{2}\b")


def _jahre(text: str | None) -> list[int]:
    return [int(m.group()) for m in _JAHR.finditer(text or "")]


def _kba_jahr(wert: str | None) -> int | None:
    j = _jahre(wert)
    return j[0] if j else None


def vira_zeitraum(recall: dict, baureihe: dict) -> tuple[int | None, int | None]:
    """Der Zeitraum, den diese VIRA-Zeile beansprucht.

    Bevorzugt `betroffene_baujahre`. Fehlt dort eine Jahreszahl (allgemeine
    Angabe wie "Alle"), klammert der Bauzeitraum der Baureihe.
    """
    jahre = _jahre(recall.get("betroffene_baujahre"))
    if jahre:
        return min(jahre), max(jahre)
    return baureihe.get("bauzeitraum_von"), baureihe.get("bauzeitraum_bis")


def _ueberlappt(a_von, a_bis, b_von, b_bis) -> bool:
    """Zeitraum-Ueberschneidung. Offene Enden zaehlen als 'bis heute'."""
    a_von = a_von or 1900
    a_bis = a_bis or 2100
    b_von = b_von or 1900
    b_bis = b_bis or 2100
    return a_von <= b_bis and b_von <= a_bis


def normalisiere_referenz(ref: str | None) -> str:
    """KBA-Referenz auf die amtliche Schreibweise bringen (ohne fuehrende Null).

    Der Export fuehrt "12223", Sekundaerquellen schreiben oft "012223". Fuer den
    Abgleich zaehlt der ziffernnormalisierte Wert.
    """
    r = re.sub(r"[^0-9A-Za-z]", "", (ref or "")).upper()
    return r.lstrip("0") or r


@dataclass
class Kandidat:
    """Ein KBA-Datensatz, der fuer eine VIRA-Zeile in Frage kommt."""
    kba: dict
    modell_treffer: str
    zeitraum_ok: bool
    gruppen_gemeinsam: set[str] = field(default_factory=set)
    tokens_gemeinsam: set[str] = field(default_factory=set)
    # Alle distinktiven Tokens des AMTLICHEN Datensatzes — Grundlage fuer die
    # Dokumentfrequenz innerhalb der Kandidatenmenge.
    _alle_tokens: set = field(default_factory=set)
    referenz_treffer: bool = False

    @property
    def referenz(self) -> str:
        return (self.kba.get("KBA-Referenznummer") or "").strip()

    @property
    def starke_gruppen(self) -> set[str]:
        return self.gruppen_gemeinsam & STARKE_GRUPPEN

    # Nur die Tokens, die INNERHALB der Kandidatenmenge selten sind. Wird von
    # `klassifiziere` nachtraeglich gesetzt, weil dafuer alle Kandidaten dieser
    # VIRA-Zeile bekannt sein muessen.
    tokens_trennscharf: set = field(default_factory=set)

    @property
    def inhaltlich(self) -> bool:
        """Inhaltlicher Treffer — zwei UNABHAENGIGE Bedingungen, beide noetig.

        (1) mindestens eine gemeinsame STARKE Bauteilgruppe, und
        (2) mindestens ein gemeinsames TRENNSCHARFES Wort.

        Warum die Trennschaerfe noetig ist: die zweite Validierungsrunde hat
        drei falsche Zuordnungen gezeigt, bei denen das einzige gemeinsame Wort
        der Bauteilname selbst war ("airbag", "batterie"). Das ist zirkulaer —
        genau dieses Wort hat ja schon die Gruppe bestimmt, es fuegt kein
        unabhaengiges Signal hinzu. Ein Wort, das in fast jedem Kandidaten
        dieser Baureihe vorkommt, unterscheidet die Kandidaten nicht.
        """
        return bool(self.starke_gruppen) and bool(self.tokens_trennscharf)

    @property
    def staerke(self) -> tuple:
        """Deterministische Rangfolge fuer die Eindeutigkeitspruefung."""
        return (self.referenz_treffer, self.zeitraum_ok,
                len(self.starke_gruppen), len(self.tokens_trennscharf),
                len(self.tokens_gemeinsam))


# ── Belegstaerke ─────────────────────────────────────────────────────────────
#
# Die Match-KLASSE sagt, WELCHER amtliche Rueckruf gemeint ist. Die
# BELEGSTAERKE sagt, wie sicher das ist — und entscheidet, ob am Ende
# `verified` oder nur `partially_verified` geschrieben werden darf.
#
# Warum getrennt: nach drei Runden Nachschaerfen blieben Faelle uebrig, in denen
# genau EIN trennscharfes Wort die Zuordnung traegt (Audi A4 B9 "Beifahrerairbag
# loest nicht aus" gegen den amtlichen "Befestigung des Beifahrerairbag-Moduls").
# Solche Faelle sind plausibel, aber nicht belegt: dieselben Worte, anderer
# Mechanismus. Weiter am Matcher zu drehen wuerde nur die Grenze verschieben.
# Ehrlicher ist, sie als das auszuweisen, was sie sind — ein Thementreffer.
BELEG_STARK = "stark"      # Referenztreffer ODER >= 2 trennscharfe Begriffe
BELEG_SCHWACH = "schwach"  # genau 1 trennscharfer Begriff
BELEG_KEINER = "keiner"


@dataclass
class Befund:
    """Das Ergebnis fuer EINE VIRA-Rueckrufzeile."""
    fakt_id: int
    baureihe_id: str
    marke: str
    modell: str
    generation: str
    mangel: str
    vira_referenz: str | None
    vira_datum: str | None
    vira_baujahre: str | None
    klasse: str
    kandidaten: list = field(default_factory=list)
    begruendung: str = ""

    @property
    def bester(self):
        return self.kandidaten[0] if self.kandidaten else None

    @property
    def belegstaerke(self) -> str:
        """Wie gut ist die Zuordnung belegt? Steuert verified vs partially."""
        c = self.bester
        if c is None or self.klasse not in (EXACT, CORRECTABLE):
            return BELEG_KEINER
        if c.referenz_treffer or len(c.tokens_trennscharf) >= 2:
            return BELEG_STARK
        return BELEG_SCHWACH

    @property
    def darf_verified(self) -> bool:
        """Nur A und sauber belegte B-Faelle (§4 des Auftrags)."""
        return (self.klasse in (EXACT, CORRECTABLE)
                and self.belegstaerke == BELEG_STARK)


def lade_kba(pfad: str) -> list[dict]:
    """Liest den amtlichen Export."""
    with io.open(pfad, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def kba_index(kba: list[dict]) -> dict[str, list[dict]]:
    """Marke -> Datensaetze."""
    idx: dict[str, list[dict]] = {}
    for r in kba:
        idx.setdefault((r.get("Marke") or "").strip().upper(), []).append(r)
    return idx


def _abweichungen(recall: dict, kand: Kandidat) -> list[str]:
    """Welche VIRA-Felder weichen vom amtlichen Datensatz ab?"""
    abw = []
    amtl_ref = (kand.kba.get("KBA-Referenznummer") or "").strip()
    if (recall.get("kba_referenz") or "").strip() != amtl_ref:
        abw.append("Referenz")
    amtl_datum = (kand.kba.get("Veröffentlichungsdatum") or "").strip()[:7]
    if (recall.get("datum") or "").strip()[:7] != amtl_datum:
        abw.append("Datum")
    k_von = _kba_jahr(kand.kba.get("Produktionszeitraum von"))
    k_bis = _kba_jahr(kand.kba.get("Produktionszeitraum bis"))
    v_j = _jahre(recall.get("betroffene_baujahre"))
    # Eine VIRA-Angabe, die INNERHALB des amtlichen Fensters liegt, ist keine
    # Abweichung, sondern eine bewusste Verengung — genau so wurde der
    # Insignia-Rueckruf 12223 im Pilot auf den Bauzeitraum der Baureihe
    # zugeschnitten (amtlich 2016-2020, gespeichert 2017-2020). Als Fehler
    # gilt nur, was ausserhalb liegt oder ganz fehlt.
    if not v_j:
        abw.append("Bauzeitraum fehlt")
    elif min(v_j) < (k_von or 1900) or max(v_j) > (k_bis or 2100):
        abw.append("Bauzeitraum")
    return abw


def klassifiziere(recall: dict, baureihe: dict, kba: list[dict],
                  idx: dict[str, list[dict]] | None = None) -> Befund:
    """Ordnet EINE VIRA-Rueckrufzeile genau einer Match-Klasse zu."""
    idx = idx if idx is not None else kba_index(kba)
    km = kba_marke(baureihe["marke"])
    kandidaten_modelle = _vira_modellkandidaten(baureihe["marke"], baureihe["modell"])
    v_von, v_bis = vira_zeitraum(recall, baureihe)
    v_text = " ".join(filter(None, [recall.get("mangel"), recall.get("abhilfe")]))
    v_gruppen = bauteilgruppen(v_text)
    v_tokens = distinktive_tokens(v_text)
    v_ref = normalisiere_referenz(recall.get("kba_referenz"))

    befund = Befund(
        fakt_id=recall["id"], baureihe_id=recall["baureihe_id"],
        marke=baureihe["marke"], modell=baureihe["modell"],
        generation=baureihe.get("generation") or "",
        mangel=recall.get("mangel") or "",
        vira_referenz=(recall.get("kba_referenz") or "").strip() or None,
        vira_datum=recall.get("datum"),
        vira_baujahre=recall.get("betroffene_baujahre"),
        klasse=NO_MATCH,
    )

    # ── Gate 1: Marke ───────────────────────────────────────────────────────
    marken_zeilen = idx.get(km, [])
    if not marken_zeilen:
        befund.begruendung = f"Marke {km!r} kommt im amtlichen Export nicht vor"
        return befund

    # ── Gate 2 + 3: Modell und Bauzeitraum der Baureihe ─────────────────────
    for k in marken_zeilen:
        treffer = _modelltokens(k.get("Modell")) & kandidaten_modelle
        if not treffer:
            continue
        k_von = _kba_jahr(k.get("Produktionszeitraum von"))
        k_bis = _kba_jahr(k.get("Produktionszeitraum bis"))
        # Der amtliche Rueckruf muss zum Bauzeitraum der BAUREIHE passen —
        # das ist die Generationsaufloesung.
        if not _ueberlappt(baureihe.get("bauzeitraum_von"),
                           baureihe.get("bauzeitraum_bis"), k_von, k_bis):
            continue
        gruppen = bauteilgruppen(
            " ".join(filter(None, [k.get("Mangelbezeichnung"),
                                   k.get("Beschreibung der Maßnahme")])))
        k_text = " ".join(filter(None, [k.get("Mangelbezeichnung"),
                                        k.get("Beschreibung der Maßnahme")]))
        befund.kandidaten.append(Kandidat(
            kba=k, modell_treffer=sorted(treffer)[0],
            zeitraum_ok=_ueberlappt(v_von, v_bis, k_von, k_bis),
            gruppen_gemeinsam=v_gruppen & gruppen,
            tokens_gemeinsam=v_tokens & distinktive_tokens(k_text),
            _alle_tokens=distinktive_tokens(k_text),
            referenz_treffer=bool(v_ref) and normalisiere_referenz(
                k.get("KBA-Referenznummer")) == v_ref,
        ))

    if not befund.kandidaten:
        befund.begruendung = (
            f"kein amtlicher Rueckruf fuer {km} {sorted(kandidaten_modelle)} "
            f"im Bauzeitraum der Baureihe")
        return befund

    # ── Trennschaerfe: Dokumentfrequenz je Token INNERHALB der Kandidaten ───
    # Ein Wort, das in fast allen amtlichen Rueckrufen dieser Baureihe steht,
    # kann sie nicht auseinanderhalten. Die Schwelle ist bewusst streng: ein
    # Token zaehlt nur, wenn es in hoechstens einem Viertel der Kandidaten
    # vorkommt (mindestens aber in einem — sonst waere bei zwei Kandidaten
    # nichts mehr trennscharf).
    n = len(befund.kandidaten)
    df: dict[str, int] = {}
    for cand in befund.kandidaten:
        for tok in cand._alle_tokens:
            df[tok] = df.get(tok, 0) + 1
    grenze = max(1, n // 4)
    for cand in befund.kandidaten:
        cand.tokens_trennscharf = {t for t in cand.tokens_gemeinsam
                                   if df.get(t, 0) <= grenze}

    befund.kandidaten.sort(key=lambda c: c.staerke, reverse=True)

    ref_treffer = [c for c in befund.kandidaten if c.referenz_treffer]
    inhaltlich = [c for c in befund.kandidaten if c.zeitraum_ok and c.inhaltlich]

    # ── Fall 1: die VIRA-Referenz existiert amtlich bei DIESEM Fahrzeug ─────
    if ref_treffer:
        c = ref_treffer[0]
        if c.inhaltlich:
            abw = _abweichungen(recall, c)
            befund.klasse = EXACT if not abw else CORRECTABLE
            befund.begruendung = ("Referenz und Bauteilgruppe amtlich bestaetigt"
                                  + ("" if not abw else
                                     f"; abweichend: {', '.join(abw)}"))
        else:
            befund.klasse = CONTRADICTED
            befund.begruendung = (
                f"Referenz {c.referenz} existiert amtlich fuer dieses Fahrzeug, "
                f"beschreibt aber einen anderen Mangel: "
                f"{(c.kba.get('Mangelbezeichnung') or '')[:70]}")
        return befund

    # ── Fall 2: die VIRA-Referenz gehoert amtlich zu einem ANDEREN Fahrzeug ──
    if v_ref:
        fremd = [k for k in kba
                 if normalisiere_referenz(k.get("KBA-Referenznummer")) == v_ref]
        if fremd:
            f = fremd[0]
            befund.klasse = CONTRADICTED
            befund.begruendung = (
                f"VIRA-Referenz {recall.get('kba_referenz')!r} gehoert amtlich zu "
                f"{(f.get('Marke') or '').strip()} "
                f"{(f.get('Modell') or '').strip()}")
            return befund

    # ── Fall 3: genau EIN inhaltlich passender Kandidat ─────────────────────
    if inhaltlich:
        inhaltlich.sort(key=lambda c: c.staerke, reverse=True)
        c = inhaltlich[0]
        # EINDEUTIGKEIT: der beste Kandidat muss STRIKT staerker sein als jeder
        # andere inhaltliche Treffer. Bei Gleichstand ist die Zuordnung eine
        # Muenzwurf-Entscheidung und darf niemals verified werden.
        eindeutig = len(inhaltlich) == 1 or c.staerke > inhaltlich[1].staerke
        if eindeutig:
            abw = _abweichungen(recall, c)
            befund.klasse = EXACT if not abw else CORRECTABLE
            befund.begruendung = (
                "eindeutiger Kandidat ueber Modell + Bauzeitraum + Bauteilgruppe "
                f"({', '.join(sorted(c.starke_gruppen))}) + trennscharfe Begriffe "
                f"({', '.join(sorted(c.tokens_trennscharf)[:4])})"
                + ("" if not abw else f"; abweichend: {', '.join(abw)}"))
            befund.kandidaten = [c] + [x for x in befund.kandidaten if x is not c]
            return befund
        befund.klasse = PARTIAL
        befund.begruendung = (
            f"{len(inhaltlich)} amtliche Rueckrufe gleich stark — Zuordnung "
            f"nicht eindeutig (bester: {c.referenz})")
        befund.kandidaten = inhaltlich + [x for x in befund.kandidaten
                                          if x not in inhaltlich]
        return befund

    # ── Fall 4: Kandidaten da, aber keiner inhaltlich passend ───────────────
    befund.klasse = PARTIAL
    befund.begruendung = (
        f"{len(befund.kandidaten)} amtliche Rueckrufe fuer diese Baureihe, aber "
        f"keiner mit passender Bauteilgruppe"
        + ("" if any(c.zeitraum_ok for c in befund.kandidaten)
           else " und keiner im beanspruchten Zeitraum"))
    return befund


def abgleich(recalls: list[dict], baureihen: dict[str, dict],
             kba: list[dict]) -> list[Befund]:
    """Klassifiziert den gesamten Bestand."""
    idx = kba_index(kba)
    return [klassifiziere(r, baureihen[r["baureihe_id"]], kba, idx)
            for r in recalls]


def fehlende_amtliche(recalls: list[dict], baureihen: dict[str, dict],
                      kba: list[dict]) -> list[tuple[dict, dict]]:
    """Amtliche Rueckrufe, die einer VIRA-Baureihe eindeutig zuordenbar sind,
    aber im Bestand fehlen.

    "Eindeutig zuordenbar" heisst hier streng: der amtliche Datensatz nennt
    GENAU EIN Modell (keine Sammelliste wie "X5, X6"), und dieses Modell passt
    auf GENAU EINE VIRA-Baureihe im ueberlappenden Bauzeitraum. Alles andere
    bliebe eine Generationsvermutung.
    """
    # Welche amtlichen Referenzen deckt VIRA bereits ab?
    gedeckt = {normalisiere_referenz(r.get("kba_referenz"))
               for r in recalls if (r.get("kba_referenz") or "").strip()}
    gedeckt.discard("")

    # Baureihen je (KBA-Marke, Modelltoken)
    ziel: dict[tuple[str, str], list[dict]] = {}
    for b in baureihen.values():
        km = kba_marke(b["marke"])
        for tok in _vira_modellkandidaten(b["marke"], b["modell"]):
            ziel.setdefault((km, tok), []).append(b)

    out = []
    for k in kba:
        ref = normalisiere_referenz(k.get("KBA-Referenznummer"))
        if ref in gedeckt:
            continue
        tokens = _modelltokens(k.get("Modell"))
        if len(tokens) != 1:
            continue                      # Sammeleintrag -> nicht eindeutig
        tok = next(iter(tokens))
        km = (k.get("Marke") or "").strip().upper()
        kandidaten = ziel.get((km, tok), [])
        k_von = _kba_jahr(k.get("Produktionszeitraum von"))
        k_bis = _kba_jahr(k.get("Produktionszeitraum bis"))
        passend = [b for b in kandidaten
                   if _ueberlappt(b.get("bauzeitraum_von"),
                                  b.get("bauzeitraum_bis"), k_von, k_bis)]
        if len(passend) == 1:
            out.append((k, passend[0]))
    return out
