from __future__ import annotations

"""
Kanonische Trennung von Kraftstoffart (fuel) und Antriebsart (powertrain).

BEFUND (Production-Runs Audi A4 B9, Mercedes C300 W205)
---------------------------------------------------------
`motorvariante.kraftstoff` ist in der Datenbank EIN Feld mit dem Wertebereich
'Benzin','Diesel','Elektro','Plug-in-Hybrid','Mild-Hybrid' (db/schema.sql). Das
vermischt zwei unabhängige Dimensionen in einer Spalte:

    fuel_type        WAS verbrennt wird       -> Benzin | Diesel | (keins bei BEV)
    powertrain_type  WELCHE Elektrifizierung   -> ICE | MHEV | PHEV | BEV

Ein "Mild-Hybrid"-Eintrag IST ein Benziner oder ein Diesel — das Feld sagt nur
nicht mehr, welcher, weil die Spalte die Powertrain-Information trägt statt der
Kraftstoffart. Mehrere Stellen im Code verglichen bislang den rohen DB-Wert
direkt gegen die Inserat-/Nutzerangabe ("Benzin") und meldeten einen
Kraftstoff-Widerspruch, wo keiner besteht — ein Benzin-Mild-Hybrid ist KEIN
Widerspruch zu "Benzin".

Dieses Modul ist die EINE Stelle, die diese Trennung herstellt. Für
Mild-Hybrid/Plug-in-Hybrid-Einträge wird die tatsächliche Kraftstoffart aus der
Motor-Verkaufsbezeichnung/dem Motorcode abgeleitet (TDI/CDI -> Diesel, TFSI/TSI
-> Benzin) — denselben Signalen, die bereits in `app/vehicle_identity.py` und
`app/key_findings.py` für die Kraftstofferkennung aus Freitext verwendet
werden. Lässt sich die Kraftstoffart nicht ableiten, bleibt sie None statt
geraten (Section 3: "lieber 'nicht sicher bekannt' als erfunden").

KEINE DB-Migration: die Spalte `motorvariante.kraftstoff` bleibt unverändert.
Dieses Modul liest sie nur und liefert zwei saubere, unabhängige Werte.
"""

import re

FUEL_BENZIN = "benzin"
FUEL_DIESEL = "diesel"
FUEL_ELEKTRO = "elektro"

POWERTRAIN_ICE = "ICE"
POWERTRAIN_MHEV = "MHEV"
POWERTRAIN_PHEV = "PHEV"
POWERTRAIN_BEV = "BEV"

# Reihenfolge relevant: spezifischere Signale (Diesel-Kürzel) vor dem generischen
# "benzin"-Fallback, damit z.B. "TDI" nicht durch ein zufällig enthaltenes "di"
# falsch einsortiert wird.
_FUEL_HINWEISE: tuple[tuple[str, tuple[str, ...]], ...] = (
    (FUEL_DIESEL, ("diesel", "tdi", "cdi", "hdi", "dci", "bluetec", "crdi", "jtd",
                  "dti", "cdti", "d4d", "dtec")),
    # FUEL_ELEKTRO wird NICHT ueber Teilzeichenketten erkannt (N17-Folgefix:
    # "elektro" steckt in "Elektromotor"), sondern in `fuel_aus_freitext()` ueber
    # `_ELEKTRO_KLASSE`/"EV" -- an dieser Stelle der Reihenfolge.
    (FUEL_ELEKTRO, ()),
    (FUEL_BENZIN, ("benzin", "tsi", "tfsi", "petrol", "otto", "vti", "mpi", "gdi",
                  "tce", "puretech", "skyactiv-g")),
)

# Die vier erlaubten Rohwerte der DB-Spalte `motorvariante.kraftstoff`, normiert.
_DB_ROHWERT_FUEL = {
    "benzin": FUEL_BENZIN,
    "diesel": FUEL_DIESEL,
    "elektro": FUEL_ELEKTRO,
}
_DB_ROHWERT_POWERTRAIN = {
    "benzin": POWERTRAIN_ICE,
    "diesel": POWERTRAIN_ICE,
    "mild-hybrid": POWERTRAIN_MHEV,
    "plug-in-hybrid": POWERTRAIN_PHEV,
    "elektro": POWERTRAIN_BEV,
}


def _norm(wert: str | None) -> str:
    return (wert or "").strip().lower()


def fuel_aus_freitext(*texte: str | None) -> str | None:
    """Kraftstoffart aus Freitext (Motorbezeichnung, Motorcode, Inserattext).

    Nie mehr als EINE Dimension: liefert ausschließlich Benzin/Diesel/Elektro,
    nie eine Powertrain-Kategorie. Reine Fahrzeugbeschreibung, kein Fahrzeug-
    Hardcoding — dieselben generischen Kürzel wie in `vehicle_identity.py`.
    """
    for text in texte:
        t = _norm(text)
        if not t:
            continue
        for fuel, keys in _FUEL_HINWEISE:
            if fuel == FUEL_ELEKTRO:
                # Nur eine ausdrueckliche, unverneinte Fahrzeugklasse -- und nicht
                # neben einem Hybridbezug (zwei Klassen im selben Text, z.B. "als
                # Vollhybrid und als Elektroauto"): ein Bauteilwort wie
                # "Elektromotor" ist keine Kraftstoffart.
                if ((_positiv_erwaehnt(_ELEKTRO_KLASSE, t) or _positiv_erwaehnt(_EV_KUERZEL, t))
                        and not _positiv_erwaehnt(_HYBRIDBEZUG_IRGENDEINER, t)):
                    return fuel
                continue
            if any(k in t for k in keys):
                return fuel
    return None


def canonical_fuel(db_kraftstoff: str | None, bezeichnung: str | None = None,
                    motorcode: str | None = None) -> str | None:
    """Tatsächliche Kraftstoffart der Motorvariante — nie 'Mild-Hybrid'/'Plug-in-
    Hybrid' als Rückgabewert, das sind Powertrain-, keine Kraftstoffangaben.

    Bei Benzin/Diesel/Elektro liefert der DB-Rohwert direkt das Ergebnis. Bei
    Mild-Hybrid/Plug-in-Hybrid trägt die Spalte selbst keine Kraftstoffart mehr
    — sie wird aus Bezeichnung/Motorcode abgeleitet. Ohne ableitbares Signal
    bleibt das Ergebnis None (nicht geraten).
    """
    roh = _norm(db_kraftstoff)
    direkt = _DB_ROHWERT_FUEL.get(roh)
    if direkt:
        return direkt
    return fuel_aus_freitext(bezeichnung, motorcode)


def canonical_powertrain(db_kraftstoff: str | None) -> str | None:
    """Antriebsart (Elektrifizierungsgrad) aus dem DB-Rohwert. None, wenn der
    Rohwert leer oder außerhalb des bekannten Wertebereichs liegt."""
    return _DB_ROHWERT_POWERTRAIN.get(_norm(db_kraftstoff))


def ist_fuel_widerspruch(a: str | None, b: str | None) -> bool:
    """True nur bei zwei echten, unterschiedlichen Kraftstoffarten.

    Fehlt eine Seite (z.B. weil sie sich aus einem Mild-Hybrid-Eintrag nicht
    ableiten ließ), ist das KEIN Widerspruch — nur eine fehlende Vergleichsbasis.
    """
    return bool(a) and bool(b) and a != b


# ── Scope-Vergleich auf GETRENNTEN Achsen (KaufCheck-Final-Stabilization, D) ──
#
# BEFUND (Resume-Audit): drei Stellen verglichen einen Scope aus dem gemischten
# Vokabular ("Benziner", "Diesel", "Mild-Hybrid", "Plug-in-Hybrid", "Elektro")
# per Gleichheit gegen EINEN Fahrzeugwert, der selbst mal Kraftstoff, mal
# Antriebsart war:
#   * recall_filter.rueckruf_applicability: Nutzerangabe "Benzin" gegen einen
#     "(Plug-in-Hybrid)"-/Hochvolt-Rückruf -> "incompatible" — ein Brandgefahr-
#     Rückruf verschwand bei einem Benzin-PHEV.
#   * motor_applicability.schwachstelle_applicability: Scope "(Benzinmotoren)"
#     gegen den DB-Rohwert "Mild-Hybrid" eines TFSI -> ausgeschlossen.
#   * car_lookup._motor_kraftstoff_kompatibel: Nutzerangabe "Benzin" schloss die
#     "Plug-in-Hybrid"-Zeile eines Benzin-PHEV aus der Motorzuordnung aus.
#
# REGEL: jeder Scope-Begriff gehört zu GENAU einer Achse. "benzin"/"diesel"
# werden nur gegen die Kraftstoffart geprüft, "mild"/"phev"/"elektro" nur gegen
# die Antriebsart (Hochvolt-Gruppe PHEV/HEV/BEV wie bisher gleichgesetzt). Eine
# unbekannte Achse ergibt None (unklar), nie einen Ausschluss. Eine Nutzer-
# angabe "Benzin" sagt NICHTS über die Elektrifizierung; der DB-Rohwert
# "Benzin"/"Diesel" dagegen schon (die Spalte trägt bei Hybriden die
# Antriebsart, also heißt "Benzin" dort ICE).

HOCHVOLT_POWERTRAINS = frozenset({POWERTRAIN_PHEV, "HEV", POWERTRAIN_BEV})
SCOPE_FUEL = frozenset({FUEL_BENZIN, FUEL_DIESEL})


# Release-Hardening (Continuation, negation-sichere Powertrain-Erkennung):
# BEFUND — `powertrain_aus_freitext` prüfte Hybrid-Vokabular VOR dem
# ICE-Vokabular und ganz ohne Verneinungsprüfung. "ohne Hybridisierung"
# enthält die Teilzeichenkette "hybrid" und wurde dadurch als "HEV"
# zurückgegeben — das genaue Gegenteil der Aussage. Dieselbe Falle gilt für
# jedes andere Verneinungswort ("kein Mild-Hybrid", "ohne Plug-in-Hybrid",
# "nicht elektrifiziert").
#
# LÖSUNG: jede Wortgruppe wird für sich geprüft (`_positiv_erwaehnt`), und ein
# Treffer zählt nur, wenn im selben Teilsatz UNMITTELBAR davor KEIN
# Verneinungswort steht. Eine Verneinung liefert NIE automatisch das
# Gegenteil (z.B. ICE) — nur eine ausdrückliche ICE-/Verbrenner-Formulierung
# tut das. "ohne Hybridisierung" ohne eine solche Formulierung bleibt UNKNOWN
# (None) statt erfunden — dieselbe Regel wie überall sonst in diesem Modul
# ("lieber 'nicht sicher bekannt' als erfunden", s. Moduldocstring).
#
# Backend-Folgeauftrag (dieselbe Haertung wie app/technical_research.py
# ::_verneint_im_teilsatz, hier nachgezogen): "nicht nur Hybrid, sondern auch
# Verbrenner erhaeltlich" behauptete bisher NUR "ICE" -- die Hybrid-Variante
# ging stillschweigend verloren, weil "nicht nur" wie eine normale Verneinung
# zaehlte. "nicht ohne Hybridisierung" (doppelte Verneinung, bejaht Hybrid)
# lieferte bisher ebenfalls None statt die Bejahung stehen zu lassen. Beides
# bewusst NICHT als Verneinung gewertet; jede andere Verneinung bleibt
# unveraendert streng.
_VERNEINUNG = re.compile(
    r"\bnicht\s+nur\b"
    r"|\bnicht\s+ohne\b"
    r"|\b(?:kein|keine|keinen|keinem|keiner|ohne|nicht)\b", re.IGNORECASE)
_VERNEINUNGS_AUSNAHME = re.compile(r"\A(?:nicht\s+nur|nicht\s+ohne)\Z", re.IGNORECASE)

_PHEV_WORT = re.compile(r"plug[\s-]?in[\s-]?hybrid|\bphev\b", re.IGNORECASE)
_MHEV_WORT = re.compile(r"mild[\s-]?hybrid|\bmhev\b|\b48\s?v\b", re.IGNORECASE)
# F16-Folgefix (RC-F-Ursachenanalyse, real nachgeprueft an newsroom.lexus.eu,
# "The RC F SPORT features an upgraded LDH system ... Electric Power
# Steering"): das bisherige bare "elektrisch\w*|electric" erkannte jedes
# gewoehnliche 12V-Bauteil als Hochvolt-/BEV-Antrieb ("elektrische
# Servolenkung", "elektrische Kraftstoffpumpe", "Electric Power Steering",
# "Electric Parking Brake") -- Serienausstattung in praktisch jedem
# Verbrenner, keine Elektrifizierung. DIESELBE Erkenntnis fuehrte bereits zum
# "elektrisch"-Ausschluss in `app/recall_filter.py::_HV_MUSTER` (dort als
# Folge-Fix vor dem Insignia-012223-Merge dokumentiert: 29 betroffene
# Rueckrufe, u.a. "elektrische Servolenkung"/"elektrische Kraftstoffpumpe"/
# "elektrische Feststellbremse"). `powertrain_aus_freitext()` liest u.a.
# `eingrenzung_amtlich` fuer dieselbe Antriebsart-Scope-Entscheidung
# (app/recall_filter.py, `pt_wort = powertrain_aus_freitext(eingr_amtlich)`)
# -- derselbe Fehlklassifikations-Risikopfad war hier unverändert offen.
# Anders als bei `_HV_MUSTER` kann "elektrisch" hier nicht einfach entfallen:
# "Das Fahrzeug faehrt vollelektrisch"/"Dieses Modell ist vollelektrisch"
# muss weiterhin BEV ergeben (s. test_powertrain_negation.py, Fall I1).
# Generische Abgrenzung statt Bauteil-Liste: "elektrisch" zaehlt nur, wenn es
# den ANTRIEB selbst bezeichnet (Praefix "voll-"/"rein"/"ausschliesslich"/
# "nur" direkt vor "elektrisch", oder "elektrisch angetrieben"/"elektrischer
# Antrieb"/"elektrisches Fahrzeug") -- ein bloss NACHGESTELLTES Bauteilwort
# ("elektrische Servolenkung") faellt NICHT mehr darunter, weil "elektrisch"
# dort das Bauteil beschreibt, nicht den Antrieb.
# N17-Folgefix (BEV-Fehlausloeser durch Bauteilwoerter): das bisher hier
# enthaltene, unbeschraenkte `elektro\w*` las JEDES Kompositum als Fahrzeug-
# klasse BEV -- "Elektromotor" (steckt in jedem Hybrid), "Elektro-Fensterheber",
# "2.5 Hybrid mit Elektromotor". Ein Antriebsbauteil bestimmt die Fahrzeugklasse
# nicht. Jetzt zwei getrennte Wortgruppen:
#   * `_ELEKTRO_KLASSE`: ausdrueckliche FAHRZEUGKLASSEN-Bezeichnungen
#     (Elektroauto/-fahrzeug, batterieelektrisch, BEV, "electric vehicle", das
#     alleinstehende Klassenwort "Elektro"/"Electric" als Feldwert am Ende).
#   * `_ELEKTRO_MODUS`: Formulierungen fuer elektrisches FAHREN ("vollelektrisch",
#     "rein elektrisch", "elektrisch angetrieben"). Sie belegen BEV nur, wenn der
#     Text KEINEN Hybridbezug hat -- ein Vollhybrid "faehrt bis 2 km rein
#     elektrisch" (F16-Logik fuer "elektrische Servolenkung" bleibt erhalten).
_ELEKTRO_KLASSE = re.compile(
    r"\bbev\b"
    r"|elektro[\s-]?(?:auto|fahrzeug|wagen|pkw|version|variante|modell)\w*"
    r"|batterie[\s-]?elektrisch\w*"
    r"|elektrisch\w*\s+fahrzeug\w*"
    r"|electric\s+vehicle|all[\s-]?electric|fully[\s-]?electric"
    r"|\b(?:elektro|electric)\b(?=\s*(?:$|[,.;:)/|]))",
    re.IGNORECASE)
_ELEKTRO_MODUS = re.compile(
    r"(?:voll|rein|ausschlie(?:ß|ss)lich|nur)[\s-]?elektrisch\w*"
    r"|elektrisch\w*\s+(?:angetrieben\w*|antrieb\w*)",
    re.IGNORECASE)
_EV_KUERZEL = re.compile(r"\bev\b", re.IGNORECASE)
_BEV_WORT = re.compile(_ELEKTRO_KLASSE.pattern + "|" + _ELEKTRO_MODUS.pattern, re.IGNORECASE)
# Backend-Folgeauftrag (Praezision statt Prioritaetsreihenfolge): "HEV" ist im
# Datenmodell (s. Moduldocstring: ICE|MHEV|PHEV|BEV) kein eigener, klar
# abgegrenzter Wert, sondern der Sammelbegriff fuer den NICHT-Plug-in-,
# NICHT-Mild-Vollhybrid. Ein generisches "Hybrid"/"hybridisiert"/
# "elektrifiziert" im Text sagt NICHT, welche Unterklasse gemeint ist -- es
# koennte genauso ein Mild- oder Plug-in-Hybrid sein, der nur nicht so genannt
# wurde. Bisher griff `_HEV_WORT` auf genau diese generischen Woerter zu und
# behauptete damit eine Praezision ("HEV"), die der Text gar nicht hergibt --
# das war reine PRIORITAETSREIHENFOLGE (PHEV vor MHEV vor BEV vor HEV-generisch
# vor ICE), keine fachliche Eindeutigkeit. Jetzt: `_HEV_WORT` verlangt eine
# TATSAECHLICH spezifische Vollhybrid-Formulierung ("HEV", "Vollhybrid") --
# ein bloss generischer Hybridbezug ohne Unterklassen-Marker faellt durch alle
# Pruefungen und bleibt None (spezifische Klasse unbekannt), statt erfunden zu
# werden. Explizite PHEV-/MHEV-/BEV-Formulierungen sind davon unberuehrt --
# die bleiben weiterhin spezifisch und werden weiterhin erkannt.
_HEV_WORT = re.compile(r"\bhev\b|voll[\s-]?hybrid\w*", re.IGNORECASE)
_ICE_WORT = re.compile(r"\bice\b|verbrenn\w*", re.IGNORECASE)

# Wie weit vor einem Treffer nach einem Verneinungswort gesucht wird — großzügig
# genug für "ohne jede Elektrifizierung" (23 Zeichen), aber durch die
# Teilsatzgrenze unten ohnehin auf den eigenen Teilsatz begrenzt.
_VERNEINUNGS_FENSTER = 40


def _positiv_erwaehnt(pattern: re.Pattern, text: str) -> bool:
    """True, wenn `pattern` im Text vorkommt UND an KEINER Fundstelle im
    selben Teilsatz unmittelbar vorher verneint wird."""
    for m in pattern.finditer(text):
        vorspann = text[max(0, m.start() - _VERNEINUNGS_FENSTER):m.start()]
        # Nur der eigene Teilsatz zählt — eine Verneinung in einem FRÜHEREN
        # Teilsatz ("Nicht bekannt. Hybrid?") darf diesen Treffer nicht
        # mitreißen.
        eigener_teilsatz = re.split(r"[.,;:!?]", vorspann)[-1]
        verneint = False
        for v in _VERNEINUNG.finditer(eigener_teilsatz):
            if _VERNEINUNGS_AUSNAHME.match(v.group()):
                continue
            verneint = True
            break
        if not verneint:
            return True
    return False


def powertrain_aus_freitext(*texte: str | None) -> str | None:
    """Antriebsart NUR aus ausdrücklichen, NICHT verneinten
    Elektrifizierungswörtern. "Benzin"/"Diesel" liefern bewusst nichts — und
    eine Verneinung ("ohne Hybridisierung", "kein Mild-Hybrid") liefert NIE
    automatisch das Gegenteil, nur eine eigene explizite ICE-/
    Verbrenner-Formulierung tut das.

    N17-Folgefix: nennt der Text MEHRERE unterschiedliche Fahrzeugklassen
    (z.B. "als Vollhybrid und als Elektroauto", "Mild-Hybrid oder Plug-in-
    Hybrid"), wird keine davon willkuerlich per Reihenfolge bestaetigt -> None.
    Ein elektrischer Fahrmodus ("rein elektrisch") belegt BEV nur ohne jeden
    Hybridbezug im Text."""
    roh = " ".join(_norm(t) for t in texte if t)
    if not roh.strip():
        return None
    hybrid_klassen = [k for k, w in ((POWERTRAIN_PHEV, _PHEV_WORT), (POWERTRAIN_MHEV, _MHEV_WORT),
                                     ("HEV", _HEV_WORT)) if _positiv_erwaehnt(w, roh)]
    bev_klasse = _positiv_erwaehnt(_ELEKTRO_KLASSE, roh)
    if len(hybrid_klassen) + (1 if bev_klasse else 0) > 1:
        return None
    if hybrid_klassen:
        return hybrid_klassen[0]
    hybridbezug = _positiv_erwaehnt(_HYBRIDBEZUG_IRGENDEINER, roh)
    if bev_klasse:
        # Ausdrueckliche BEV-Klasse neben einem (unspezifischen) Hybridbezug:
        # zwei Klassen im selben Text, keine davon belegt.
        return None if hybridbezug else POWERTRAIN_BEV
    if not hybridbezug and _positiv_erwaehnt(_ELEKTRO_MODUS, roh):
        return POWERTRAIN_BEV
    # Derselbe Grundsatz fuer den Verbrenner: in einem Text mit Hybridbezug
    # ("kombiniert Elektromotor und Verbrenner") ist "Verbrenner" ein Bauteil
    # des Hybridsystems, keine Fahrzeugklasse ICE.
    if not hybridbezug and _positiv_erwaehnt(_ICE_WORT, roh):
        return POWERTRAIN_ICE
    return None


# Backend-Folgeauftrag (Nebenwirkung der Powertrain-Präzisionskorrektur
# oben): `powertrain_aus_freitext()` liefert jetzt korrekt `None` sowohl für
# "kein Hybridbezug im Text" ALS AUCH für "Hybridbezug genannt, aber nicht
# klassenspezifisch" ("Hybridantrieb" ohne Mild-/Plug-in-/Vollhybrid-Zusatz).
# Ein Aufrufer, der zwischen beiden Fällen unterscheiden MUSS (z.B.
# app/recall_filter.py::rueckruf_scope — ein amtlicher Rückruftext mit einer
# NICHT auflösbaren Antriebsart-Bedingung ist eine OFFENE Scope-Dimension,
# keine fehlende), bekommt dafür diese separate, generische Prüfung — reine
# Wortmengen-Vereinigung aller bereits bestehenden Muster, keine neue Logik.
_HYBRIDBEZUG_IRGENDEINER = re.compile(
    _PHEV_WORT.pattern + "|" + _MHEV_WORT.pattern + "|" + _HEV_WORT.pattern
    + r"|hybridisier\w*|\bhybrid\w*|elektrifizier\w*", re.IGNORECASE)


def hybridbezug_erwaehnt(*texte: str | None) -> bool:
    """True, wenn IRGENDEIN (auch generischer, nicht klassenspezifischer)
    Hybrid-/Elektrifizierungsbezug unverneint im Text steht — unabhängig
    davon, ob `powertrain_aus_freitext()` daraus eine konkrete Klasse ableiten
    kann. Sagt NICHTS über eine bestimmte Klasse aus, nur "irgendeine
    Antriebsart-Bedingung ist genannt"."""
    roh = " ".join(_norm(t) for t in texte if t)
    if not roh.strip():
        return False
    return _positiv_erwaehnt(_HYBRIDBEZUG_IRGENDEINER, roh)


def fahrzeug_achsen(*, identity=None, db_kraftstoff: str | None = None,
                    bezeichnung: str | None = None, motorcode: str | None = None,
                    nutzer_text: str | None = None) -> tuple[str | None, frozenset | None]:
    """(Kraftstoffart, mögliche Antriebsarten) eines Fahrzeugs.

    Mit kanonischer Identität gilt ausschließlich sie (inklusive einer
    mehrdeutigen Antriebsart als Menge der möglichen Werte). Ohne Identität:
    Nutzertext für die Kraftstoffart, DB-Zeile für beide Achsen."""
    if identity is not None:
        fuel = fuel_aus_freitext(getattr(identity, "fuel", None))
        fe = (getattr(identity, "field_evidence", None) or {}).get("powertrain") or {}
        if fe.get("verification_state") == "ambiguous" and fe.get("possible_values"):
            pts = frozenset(str(p).upper() for p in fe["possible_values"])
        elif getattr(identity, "powertrain", None):
            pts = frozenset({str(identity.powertrain).upper()})
        else:
            pts = None
        if pts is None and fuel == FUEL_ELEKTRO:
            pts = frozenset({POWERTRAIN_BEV})
        return fuel, pts
    fuel = fuel_aus_freitext(nutzer_text) or canonical_fuel(db_kraftstoff, bezeichnung, motorcode)
    pt = powertrain_aus_freitext(nutzer_text) or canonical_powertrain(db_kraftstoff)
    if pt is None and fuel == FUEL_ELEKTRO:
        pt = POWERTRAIN_BEV
    return fuel, (frozenset({pt}) if pt else None)


def scope_passt(scope: str | None, fuel: str | None, powertrains: frozenset | None) -> bool | None:
    """Passt ein Scope-Begriff (Vokabular von `recall_filter._norm_kraftstoff`:
    benzin|diesel|mild|phev|elektro) zum Fahrzeug? True/False/None (unklar)."""
    if not scope:
        return None
    if scope in SCOPE_FUEL:
        if fuel in (FUEL_BENZIN, FUEL_DIESEL, FUEL_ELEKTRO):
            return fuel == scope
        if powertrains and powertrains <= {POWERTRAIN_BEV}:
            return False
        return None
    if scope == "mild":
        if powertrains:
            if powertrains <= {POWERTRAIN_MHEV}:
                return True
            if POWERTRAIN_MHEV not in powertrains:
                return False
        return None
    if scope == SCOPE_BEV_EXKLUSIV:
        # Ausdrücklich batterieelektrisch: Hochvolt allein genügt NICHT.
        if powertrains:
            if powertrains <= {POWERTRAIN_BEV}:
                return True
            if POWERTRAIN_BEV not in powertrains:
                return False
        return None
    if scope == SCOPE_ELEKTRO_KLASSE:
        # "Elektrofahrzeug"/"(Elektro)" ohne eindeutige Exklusivität: BEV passt,
        # ein Nicht-Hochvolt-Antrieb nicht, HEV/PHEV bleiben offen.
        if powertrains:
            if powertrains <= {POWERTRAIN_BEV}:
                return True
            if not (powertrains & HOCHVOLT_POWERTRAINS):
                return False
        return None
    if scope in ("phev", "elektro"):
        if powertrains:
            if powertrains <= HOCHVOLT_POWERTRAINS:
                return True
            if not (powertrains & HOCHVOLT_POWERTRAINS):
                return False
            return None
        if fuel == FUEL_ELEKTRO:
            return True
        return None
    return None


def scopes_passen(scopes, fuel: str | None, powertrains: frozenset | None) -> bool | None:
    """Mehrere Scope-Begriffe (ODER): ein Treffer genügt, ausgeschlossen nur,
    wenn JEDER Begriff sicher nicht passt."""
    ergebnisse = [scope_passt(s, fuel, powertrains) for s in scopes or ()]
    if any(e is True for e in ergebnisse):
        return True
    if ergebnisse and all(e is False for e in ergebnisse):
        return False
    return None


# ── Klassen-Scope eines Rückruftexts (N17-Folgefix: BEV-exklusiv vs. Hochvolt) ──
#
# BEFUND: Die amtliche Eingrenzung wurde auf EINE Klasse reduziert und diese in
# den Sammelscope "phev"/"elektro" (= ganze Hochvolt-Gruppe PHEV/HEV/BEV)
# übersetzt. "Ausschließlich batterieelektrische Fahrzeuge" galt dadurch auch für
# ein bestätigtes HEV/PHEV als möglich; ein Satz "… sind nicht betroffen" wurde
# als positive Eingrenzung gelesen (die Verneinung steht HINTER dem Klassenwort).
#
# REGEL:
#   * Sätze, die eine Klasse AUSNEHMEN ("… nicht betroffen", "ausgenommen"),
#     tragen keinen positiven Scope (Erläuterungs-/Vergleichssätze).
#   * Aus den übrigen Sätzen: die Menge der ausdrücklich genannten Klassen.
#   * BEV-Klasse in zwei Stärken: BEV-EXKLUSIV nur bei eindeutig batterie-
#     elektrischer Formulierung ("batterieelektrisch", "BEV", "rein/voll-
#     elektrisch", "reine Elektrofahrzeuge"); "Elektrofahrzeug"/"Elektroauto"
#     allein ist UNKLAR (kann PHEV einschließen) -> HEV/PHEV bleiben offen.
_BEV_EXKLUSIV = re.compile(
    r"\bbev\b|batterie[\s-]?elektrisch\w*|battery[\s-]?electric"
    r"|(?:voll|rein|ausschlie(?:ß|ss)lich|nur)[\s-]?elektrisch\w*"
    r"|\breine?[nrs]?\s+elektro[\s-]?(?:fahrzeug|auto)\w*"
    r"|all[\s-]?electric|fully[\s-]?electric",
    re.IGNORECASE)
_AUSNAHME_SATZ = re.compile(r"\bnicht\b[^.;!?]*\bbetroffen|\bausgenommen\b", re.IGNORECASE)

SCOPE_BEV_EXKLUSIV = "bev"
SCOPE_ELEKTRO_KLASSE = "elektro_klasse"


def scope_klassen(text: str | None) -> tuple[frozenset, bool, bool]:
    """(ausdrücklich genannte Fahrzeugklassen, generischer Hybridbezug ohne
    Unterklasse, BEV-Nennung eindeutig exklusiv) aus den POSITIVEN Sätzen eines
    Rückruf-Eingrenzungstexts."""
    saetze = [s for s in re.split(r"(?<=[.;!?])\s+", text or "")
              if s.strip() and not _AUSNAHME_SATZ.search(s)]
    roh = _norm(" ".join(saetze))
    if not roh:
        return frozenset(), False, False
    klassen = {k for k, w in ((POWERTRAIN_PHEV, _PHEV_WORT), (POWERTRAIN_MHEV, _MHEV_WORT),
                              ("HEV", _HEV_WORT)) if _positiv_erwaehnt(w, roh)}
    hybridbezug = _positiv_erwaehnt(_HYBRIDBEZUG_IRGENDEINER, roh)
    if _positiv_erwaehnt(_ELEKTRO_KLASSE, roh) or (not hybridbezug and _positiv_erwaehnt(_ELEKTRO_MODUS, roh)):
        klassen.add(POWERTRAIN_BEV)
    generisch = hybridbezug and not (klassen - {POWERTRAIN_BEV})
    exklusiv = POWERTRAIN_BEV in klassen and _positiv_erwaehnt(_BEV_EXKLUSIV, roh)
    return frozenset(klassen), generisch, exklusiv


def klassen_scope_passt(klassen: frozenset, generisch_hybrid: bool,
                        powertrains: frozenset | None) -> bool | None:
    """Mehrere ausdrücklich genannte Klassen (oder BEV neben einem generischen
    Hybridbezug): passt nur eine GENANNTE Klasse; ein klarer Nicht-Hochvolt-
    Antrieb ist ausgeschlossen; jede andere Hochvolt-Klasse bleibt offen."""
    if not powertrains:
        return None
    if powertrains <= klassen:
        return True
    if powertrains & klassen:
        return None
    if powertrains <= {POWERTRAIN_ICE}:
        return False
    if powertrains <= {POWERTRAIN_MHEV} and klassen <= HOCHVOLT_POWERTRAINS and not generisch_hybrid:
        return False
    return None
