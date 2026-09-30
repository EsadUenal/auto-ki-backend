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
