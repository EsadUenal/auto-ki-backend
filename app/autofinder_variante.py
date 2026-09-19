from __future__ import annotations

"""
AutoFinder — Auflösung einer KONKRETEN empfohlenen Variante.

DAS PROBLEM, DAS DIESES MODUL LÖST
-----------------------------------
Das Datenmodell führt drei zentrale Eigenschaften auf BAUREIHEN-Ebene:

    baureihe.karosserie        JSON-Array ALLER Karosserien der Generation
    baureihe.bauzeitraum_*     Bauzeit der GENERATION, nicht der Motorisierung
    baureihe.generation        teils ein Werkscode, der nur EINE Karosserie meint

Die Empfehlung wird aber als konkrete MOTORVARIANTE dargestellt. Ohne
Auflösung erbt jede Motorisierung die Sammelmenge der Generation — daraus
entstanden real beobachtete Falschaussagen:

  * VW Golf VII GTI (Schrägheck) wurde als "Kombi" ausgegeben, weil die
    BAUREIHE Golf VII auch als Variant existiert.
  * Audi A3 8V 2.0 TFSI wurde mit "Cabrio / Kompakt / Limousine" beschriftet.
  * "Automatik / Schaltgetriebe" stand als konkretes Ergebnis auf der Karte,
    obwohl der Nutzer Automatik verlangt hatte.
  * Die Bauzeit der Generation (2012–2020) stand als Baujahr der Variante da.

WAS HIER BELEGBAR IST — UND WAS NICHT
--------------------------------------
Es wird NICHTS geraten. Die Karosserie einer Variante gilt nur dann als
belegt, wenn sie aus vorhandenen Daten FOLGT:

  1. Die Variantenbezeichnung nennt sie selbst ("GTD Variant", "C 220 d
     Coupé", "2.0 TDI Avant") -> Quelle `bezeichnung`.
  2. Die Baureihe führt überhaupt nur EINE Karosserie -> Quelle
     `baureihe_eindeutig`.
  3. Sonst: die Baureihenmenge ABZÜGLICH der Klassen, die innerhalb genau
     dieser Baureihe variantenseitig kodiert sind. Nennt mindestens eine
     Schwestervariante eine Klasse explizit im Namen, dann ist die Baureihe
     erkennbar so gepflegt, dass diese Karosserie eine EIGENE Variantenzeile
     bekommt — für Varianten ohne diesen Namensbestandteil ist sie damit
     nicht belegbar. Genau das trennt den Golf VII GTI (es gibt eine eigene
     Zeile "GTD Variant") vom SEAT Leon 2.0 TSI (dort kodiert KEINE Zeile
     eine Karosserie im Namen, die Kombi-Verfügbarkeit bleibt also eine
     Eigenschaft der Baureihe).

Bleibt danach mehr als eine Klasse übrig, ist die Karosserie dieser Variante
MEHRDEUTIG. Dann wird sie nicht als konkrete Empfehlung behauptet — außer der
Nutzer hat selbst gefiltert und der Schnitt mit seinem Wunsch ist eindeutig;
dann ist die angezeigte Karosserie das, was er gesucht hat und was die
Baureihe nachweislich anbietet (Quelle `nutzerwunsch`).

SICHERUNG GEGEN LEERMENGEN
---------------------------
Regel 3 darf nie alles wegnehmen: würde der Abzug die Menge leeren, gilt die
Kodierungsannahme für diese Baureihe erkennbar nicht, und es bleibt bei der
vollen Baureihenmenge. Lieber mehrdeutig als falsch leer.
"""

import json
import logging
import re
from dataclasses import dataclass, field

from app.autofinder_norm import normalisiere_karosserie_text

log = logging.getLogger(__name__)

# Quellen der Karosserie-Auflösung — absteigend belastbar.
Q_BEZEICHNUNG = "bezeichnung"          # die Variante nennt die Karosserie selbst
Q_BAUREIHE_EINDEUTIG = "baureihe_eindeutig"   # die Baureihe hat nur diese eine
Q_NUTZERWUNSCH = "nutzerwunsch"        # Schnitt aus Wunsch und Belegbarem ist eindeutig
Q_MEHRDEUTIG = "mehrdeutig"            # nicht auflösbar — nichts behaupten

# Quellen der Getriebe-Auflösung.
QG_EINDEUTIG = "variante_eindeutig"    # die Variante bietet nur dieses eine
QG_NUTZERWUNSCH = "nutzerwunsch"       # der Nutzer hat gewählt, die Variante bietet es
QG_MEHRDEUTIG = "mehrdeutig"


@dataclass(frozen=True)
class KarosserieAufloesung:
    konkret: str | None            # genau EINE Klasse, oder None wenn nicht belegbar
    quelle: str
    belegbar: frozenset[str]       # was für DIESE Variante belegbar ist
    baureihe: frozenset[str]       # was die BAUREIHE insgesamt anbietet (Kontext)

    @property
    def ist_konkret(self) -> bool:
        return self.konkret is not None


@dataclass(frozen=True)
class GetriebeAufloesung:
    konkret: str | None
    quelle: str
    verfuegbar: tuple[str, ...]


@dataclass(frozen=True)
class BaujahrAufloesung:
    """`von`/`bis` sind der für die Suche RELEVANTE Ausschnitt der Bauzeit —
    nicht eine behauptete Bauzeit der einzelnen Motorisierung. Welche Motoren
    in welchem Jahr lieferbar waren, steht nicht in der Datenbank und wird
    deshalb auch nicht behauptet."""
    von: int | None
    bis: int | None
    generation_von: int | None
    generation_bis: int | None

    @property
    def eingeschraenkt(self) -> bool:
        return (self.von, self.bis) != (self.generation_von, self.generation_bis)


# ══════════════════════════════════════════════════════════════════════════
# KAROSSERIE
# ══════════════════════════════════════════════════════════════════════════

def karosserie_aus_bezeichnung(bezeichnung: str | None) -> frozenset[str]:
    """Nennt die Variantenbezeichnung selbst eine Karosserie? ("GTD Variant")

    Bewusst dieselbe Mustererkennung wie für `baureihe.karosserie` — keine
    zweite Synonymliste, die auseinanderlaufen könnte.
    """
    return normalisiere_karosserie_text(bezeichnung)


def variantenseitig_kodierte_klassen(bezeichnungen) -> frozenset[str]:
    """Welche Karosserien kodiert DIESE Baureihe über die Variantennamen?

    Die Vereinigung über alle Varianten der Baureihe. Ist sie leer, pflegt die
    Baureihe ihre Karosserien nicht über die Variantennamen — dann darf aus
    dem Fehlen eines Namensbestandteils nichts geschlossen werden.
    """
    treffer: set[str] = set()
    for b in bezeichnungen:
        treffer |= karosserie_aus_bezeichnung(b)
    return frozenset(treffer)


def belegbare_karosserien(
    baureihe_klassen: frozenset[str],
    bezeichnung: str | None,
    kodierte_klassen: frozenset[str],
) -> frozenset[str]:
    """Welche Karosserien sind für GENAU DIESE Motorvariante belegbar?

    Siehe Modul-Docstring, Regeln 1–3 plus Leermengen-Sicherung.
    """
    eigen = karosserie_aus_bezeichnung(bezeichnung)
    if eigen:
        # Regel 1: der Name sagt es. Auf die Baureihenmenge einschränken, wo
        # diese überhaupt bekannt ist — ein Namenstreffer, den die Baureihe
        # gar nicht führt, ist eher ein Fehltreffer der Mustererkennung.
        if baureihe_klassen:
            geschnitten = eigen & baureihe_klassen
            if geschnitten:
                return geschnitten
        return eigen

    if not baureihe_klassen:
        return frozenset()
    if len(baureihe_klassen) == 1:
        return baureihe_klassen           # Regel 2

    rest = baureihe_klassen - kodierte_klassen   # Regel 3
    return rest or baureihe_klassen              # Leermengen-Sicherung


def loese_karosserie(
    baureihe_klassen: frozenset[str],
    bezeichnung: str | None,
    kodierte_klassen: frozenset[str],
    gewuenscht: list[str] | None = None,
) -> KarosserieAufloesung:
    """Die konkrete Karosserie dieser Empfehlung — oder ehrlich `None`.

    `gewuenscht` ist die Karosserie-Auswahl des Nutzers in SEINER Reihenfolge:
    bleibt nach dem Schnitt mehr als eine Klasse übrig, entscheidet seine
    eigene Priorität, nicht ein erfundenes Ranking.
    """
    belegbar = belegbare_karosserien(baureihe_klassen, bezeichnung, kodierte_klassen)
    eigen = karosserie_aus_bezeichnung(bezeichnung)

    if len(belegbar) == 1:
        einzig = next(iter(belegbar))
        quelle = Q_BEZEICHNUNG if eigen else Q_BAUREIHE_EINDEUTIG
        return KarosserieAufloesung(einzig, quelle, belegbar, baureihe_klassen)

    wunsch = [w.strip().lower() for w in (gewuenscht or []) if w and w.strip()]
    if wunsch:
        schnitt = [w for w in wunsch if w in belegbar]
        if len(schnitt) >= 1:
            # Der Nutzer hat gefiltert; die Baureihe bietet das nachweislich an.
            # Bei mehreren gewünschten Klassen entscheidet SEINE Reihenfolge.
            return KarosserieAufloesung(schnitt[0], Q_NUTZERWUNSCH, belegbar, baureihe_klassen)

    return KarosserieAufloesung(None, Q_MEHRDEUTIG, belegbar, baureihe_klassen)


# ══════════════════════════════════════════════════════════════════════════
# GETRIEBE
# ══════════════════════════════════════════════════════════════════════════

def loese_getriebe(verfuegbar, gewuenscht: list[str] | None = None) -> GetriebeAufloesung:
    """`motorvariante.getriebe` ist eine ANGEBOTSLISTE ("Manuell", "DSG").

    Als konkrete Empfehlung darf nur EIN Getriebe stehen: das vom Nutzer
    gewünschte (sofern die Variante es bietet), sonst das einzige vorhandene.
    Bietet die Variante mehrere und der Nutzer hat nichts gewählt, bleibt es
    ehrlich mehrdeutig — dann ist die Angebotsliste die richtige Aussage.
    """
    liste = tuple(sorted({g.strip().lower() for g in (verfuegbar or []) if g and g.strip()}))
    if not liste:
        return GetriebeAufloesung(None, QG_MEHRDEUTIG, ())
    wunsch = [w.strip().lower() for w in (gewuenscht or []) if w and w.strip()]
    schnitt = [w for w in wunsch if w in liste]
    if len(schnitt) == 1:
        return GetriebeAufloesung(schnitt[0], QG_NUTZERWUNSCH, liste)
    if len(liste) == 1:
        return GetriebeAufloesung(liste[0], QG_EINDEUTIG, liste)
    return GetriebeAufloesung(None, QG_MEHRDEUTIG, liste)


# ══════════════════════════════════════════════════════════════════════════
# BAUJAHRE
# ══════════════════════════════════════════════════════════════════════════

def loese_baujahre(gen_von: int | None, gen_bis: int | None,
                   req_von: int | None, req_bis: int | None) -> BaujahrAufloesung:
    """Der für die Anfrage relevante Ausschnitt der Generationsbauzeit.

    Sucht jemand 2016–2022 und die Generation lief 2012–2020, ist "2012–2020"
    als Empfehlung irreführend — relevant sind 2016–2020. Es wird KEIN Jahr
    erfunden: das Ergebnis ist immer eine Teilmenge der Generationsbauzeit.
    """
    if gen_von is None and gen_bis is None:
        return BaujahrAufloesung(None, None, None, None)
    von = gen_von
    bis = gen_bis
    if req_von is not None and von is not None:
        von = max(von, req_von)
    if req_bis is not None:
        bis = req_bis if bis is None else min(bis, req_bis)
    if von is not None and bis is not None and von > bis:
        # Kein Überschneidungsfenster — dann nichts einengen (der Hard-Filter
        # hätte den Kandidaten ohnehin verworfen).
        von, bis = gen_von, gen_bis
    return BaujahrAufloesung(von, bis, gen_von, gen_bis)


# ══════════════════════════════════════════════════════════════════════════
# MOTORBEZEICHNUNG
# ══════════════════════════════════════════════════════════════════════════

# Eine Bezeichnung, die NUR die Leistung wiederholt ("245 PS", "(180 kW)"),
# sagt über den Motor nichts, was nicht schon im Leistungsfeld steht — als
# "Motor / Ausführung" auf der Karte sieht sie aber wie eine aus. Betroffen
# sind aktuell zwei Datensätze; die Regel ist trotzdem allgemein formuliert,
# damit ein künftiger solcher Eintrag nicht wieder durchrutscht.
_NUR_LEISTUNG_RE = re.compile(r"^\s*\(?\s*\d+\s*(?:ps|kw)\s*\)?\s*$", re.IGNORECASE)


def motor_ist_aussagekraeftig(bezeichnung: str | None) -> bool:
    if not bezeichnung or not bezeichnung.strip():
        return False
    return not _NUR_LEISTUNG_RE.match(bezeichnung)


def loese_motorbezeichnung(bezeichnung: str | None, hubraum_ccm: int | None,
                           kraftstoff: str | None) -> tuple[str | None, bool]:
    """(Anzeigename, hergeleitet) für die Motor-/Ausführungsangabe.

    Sagt die gepflegte Bezeichnung etwas aus, wird sie unverändert genommen.
    Sonst wird aus vorhandenen Feldern eine belegbare Beschreibung gebildet:
    der Hubraum in Litern plus Kraftstoffart. Es wird KEIN Handelsname
    erfunden — "2.0 TSI" stünde nirgends in den Daten, "2.0 l Benzin" folgt
    direkt aus `hubraum_ccm` und `kraftstoff`. Fehlt auch der Hubraum, gibt es
    keine Angabe statt einer erfundenen.
    """
    if motor_ist_aussagekraeftig(bezeichnung):
        return bezeichnung, False
    if hubraum_ccm:
        liter = f"{round(hubraum_ccm / 1000, 1):.1f}".replace(".", ",")
        teile = [f"{liter} l"]
        if kraftstoff:
            teile.append(str(kraftstoff))
        return " ".join(teile), True
    return None, True


# ══════════════════════════════════════════════════════════════════════════
# GENERATIONSBEZEICHNUNG (Werkscode je Karosserie)
# ══════════════════════════════════════════════════════════════════════════

def loese_generationslabel(generation: str | None, chassis_codes_json: str | None,
                           karosserie: str | None) -> str | None:
    """Werkscode passend zur aufgelösten Karosserie, sofern geprüft hinterlegt.

    Manche Baureihen fassen mehrere Werkscodes in einem Datensatz zusammen
    (Mercedes A-Klasse: W177 = Schrägheck, V177 = Limousine). Steht "W177" auf
    einer Empfehlung, die als Limousine ausgewiesen ist, ist das eine falsche
    Kombination. `baureihe.chassis_codes` löst genau das auf — aber nur, wo
    eine geprüfte Zuordnung hinterlegt ist (app/chassis_codes.py). Ohne
    Zuordnung bleibt das Label unverändert; es wird nie ein Code geraten.
    """
    if not chassis_codes_json or not karosserie:
        return generation
    try:
        mapping = json.loads(chassis_codes_json)
    except (ValueError, TypeError):
        return generation
    if not isinstance(mapping, dict):
        return generation
    treffer = [code for code, karo in mapping.items()
               if karosserie in normalisiere_karosserie_text(str(karo))]
    if len(treffer) == 1:
        return treffer[0]
    return generation


# ══════════════════════════════════════════════════════════════════════════
# LEERE / BEDEUTUNGSLOSE ANFRAGE
# ══════════════════════════════════════════════════════════════════════════

# `kilometer_max` steht bewusst NICHT in dieser Liste: der Wert wird nirgends
# ausgewertet (kein Gebrauchtwagen-Datenbestand). Eine Suche, die NUR eine
# Kilometerangabe trägt, hat damit kein verwertbares Kriterium — und darf
# deshalb weder Kontingent noch Provider-Kosten verursachen.
VERWERTBARE_LISTENFELDER = (
    "karosserie", "kraftstoff", "getriebe", "antrieb",
    "marken_bevorzugt", "marken_ausschliessen",
)
VERWERTBARE_WERTFELDER = (
    "budget_min", "budget_max", "baujahr_von", "baujahr_bis",
    "leistung_min_ps", "leistung_max_ps", "nutzung", "km_pro_jahr",
)
VERWERTBARE_FLAGS = (
    "sportlich", "sparsam", "fahranfaenger", "praktisch", "komfortabel", "familie",
)

LEERE_SUCHE_MELDUNG = (
    "Für eine Empfehlung braucht ENFAL mindestens eine Angabe — zum Beispiel "
    "Budget, Karosserie, Kraftstoff, Getriebe oder wofür du das Auto nutzt. "
    "Wähle mindestens ein Kriterium aus und starte die Suche erneut."
)


def zaehle_kriterien(req) -> int:
    """Wie viele tatsächlich AUSGEWERTETE Kriterien trägt die Anfrage?"""
    n = 0
    for feld in VERWERTBARE_LISTENFELDER:
        if getattr(req, feld, None):
            n += 1
    for feld in VERWERTBARE_WERTFELDER:
        if getattr(req, feld, None) is not None:
            n += 1
    for feld in VERWERTBARE_FLAGS:
        if getattr(req, feld, False):
            n += 1
    return n


def hat_verwertbares_kriterium(req) -> bool:
    """False = leere bzw. inhaltlich bedeutungslose Suche.

    Wird VOR Kontingentverbrauch und VOR jedem Provider-Aufruf geprüft.
    """
    return zaehle_kriterien(req) > 0


# ══════════════════════════════════════════════════════════════════════════
# FINALE VALIDIERUNG DER AUSGABE
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class Verstoss:
    kandidat: str
    feld: str
    erwartet: str
    tatsaechlich: str


def pruefe_hardfilter_einhaltung(kandidat, req) -> list[Verstoss]:
    """Letztes Gate vor der Auslieferung: hält die FERTIGE Antwort noch die
    harten Filter ein?

    Der Hard-Filter läuft früh in der Engine; danach laufen Merge, Dedupe,
    Budget-Umsortierung, Enrichment und die Variantenauflösung. Diese Prüfung
    stellt sicher, dass nichts davon einen Kandidaten durchreicht, der den
    Wunsch nachweislich verletzt — insbesondere kann ein Modellvorschlag aus
    einer KI-Antwort nicht an der Validierung vorbei in die Ausgabe geraten.
    """
    verstoesse: list[Verstoss] = []
    kid = getattr(kandidat, "candidate_id", "") or ""

    karo_wunsch = {k.strip().lower() for k in (getattr(req, "karosserie", None) or [])}
    if karo_wunsch:
        gezeigt = {k.strip().lower() for k in (getattr(kandidat, "karosserie", None) or [])}
        if gezeigt and not (gezeigt & karo_wunsch):
            verstoesse.append(Verstoss(kid, "karosserie", "/".join(sorted(karo_wunsch)),
                                       "/".join(sorted(gezeigt))))

    getr_wunsch = {g.strip().lower() for g in (getattr(req, "getriebe", None) or [])}
    if getr_wunsch:
        gezeigt = {g.strip().lower() for g in (getattr(kandidat, "getriebe", None) or [])}
        if gezeigt and not (gezeigt & getr_wunsch):
            verstoesse.append(Verstoss(kid, "getriebe", "/".join(sorted(getr_wunsch)),
                                       "/".join(sorted(gezeigt))))

    kraft_wunsch = {k.strip().lower() for k in (getattr(req, "kraftstoff", None) or [])}
    if kraft_wunsch:
        ist = (getattr(kandidat, "kraftstoff", "") or "").strip().lower()
        if ist and ist not in kraft_wunsch:
            verstoesse.append(Verstoss(kid, "kraftstoff", "/".join(sorted(kraft_wunsch)), ist))

    antr_wunsch = {a.strip().lower() for a in (getattr(req, "antrieb", None) or [])}
    if antr_wunsch:
        ist = (getattr(kandidat, "antrieb", "") or "").strip().lower()
        if ist and ist not in antr_wunsch:
            verstoesse.append(Verstoss(kid, "antrieb", "/".join(sorted(antr_wunsch)), ist))

    ps = getattr(kandidat, "leistung_ps", None)
    lo = getattr(req, "leistung_min_ps", None)
    hi = getattr(req, "leistung_max_ps", None)
    if ps is not None:
        if lo is not None and ps < lo:
            verstoesse.append(Verstoss(kid, "leistung_ps", f">= {lo}", str(ps)))
        if hi is not None and ps > hi:
            verstoesse.append(Verstoss(kid, "leistung_ps", f"<= {hi}", str(ps)))

    return verstoesse
