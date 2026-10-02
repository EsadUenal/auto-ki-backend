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
    (FUEL_ELEKTRO, ("elektro", "electric", "bev", " ev")),
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
_VERNEINUNG = re.compile(r"\b(?:kein|keine|keinen|keinem|keiner|ohne|nicht)\b", re.IGNORECASE)

_PHEV_WORT = re.compile(r"plug[\s-]?in[\s-]?hybrid|\bphev\b", re.IGNORECASE)
_MHEV_WORT = re.compile(r"mild[\s-]?hybrid|\bmhev\b|\b48\s?v\b", re.IGNORECASE)
_BEV_WORT = re.compile(r"\bbev\b|elektro\w*|elektrisch\w*|electric", re.IGNORECASE)
_HEV_WORT = re.compile(r"\bhev\b|hybridisier\w*|\bhybrid\w*|elektrifizier\w*", re.IGNORECASE)
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
        if not _VERNEINUNG.search(eigener_teilsatz):
            return True
    return False


def powertrain_aus_freitext(*texte: str | None) -> str | None:
    """Antriebsart NUR aus ausdrücklichen, NICHT verneinten
    Elektrifizierungswörtern. "Benzin"/"Diesel" liefern bewusst nichts — und
    eine Verneinung ("ohne Hybridisierung", "kein Mild-Hybrid") liefert NIE
    automatisch das Gegenteil, nur eine eigene explizite ICE-/
    Verbrenner-Formulierung tut das."""
    roh = " ".join(_norm(t) for t in texte if t)
    if not roh.strip():
        return None
    if _positiv_erwaehnt(_PHEV_WORT, roh):
        return POWERTRAIN_PHEV
    if _positiv_erwaehnt(_MHEV_WORT, roh):
        return POWERTRAIN_MHEV
    if _positiv_erwaehnt(_BEV_WORT, roh):
        return POWERTRAIN_BEV
    if _positiv_erwaehnt(_HEV_WORT, roh):
        return "HEV"
    if _positiv_erwaehnt(_ICE_WORT, roh):
        return POWERTRAIN_ICE
    return None


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
