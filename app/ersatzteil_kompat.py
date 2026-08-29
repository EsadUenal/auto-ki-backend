from __future__ import annotations

"""
Ersatzteil-Kompatibilität (Reliability-Sprint §5/§7, Reliability-Sprint 3 §3/§6/§7/§30).

Manueller Test (Sprint 2): BMW M3 E92 + Bremsscheiben vorne -> es wurde ein Teil für
einen NORMALEN BMW E92 als "empfohlen" dargestellt. Fachlich falsch und sicherheits-
relevant: ein Chassiscode wie E92 allein bestätigt KEINE Kompatibilität — der M3 hat
andere Bremsen als ein 320i/330i.

Manueller Test (Sprint 3): trotz Fix blieb ein zweiter, subtilerer Bug bestehen — der
alte Performance-Marker "m" kollabierte M2..M8 auf EINEN Token, sodass ein Produkt für
"BMW E92 M3 GTS/CRT" (eine limitierte Sonderedition mit abweichender Bremsanlage) gegen
ein normales-M3-Zielfahrzeug fälschlich "confirmed" wurde. Dieses Modul nutzt jetzt die
zentrale, SPEZIFISCHE Marker-Erkennung aus app.vehicle_identity (m3 != m4, rs3 != rs6,
amg-c63 != amg-a45) UND einen eigenen Sub-Editions-Markerraum (GTS/CRT/Competition/...):
ein Produkt, das eine Sub-Edition nennt, die das Zielfahrzeug NICHT trägt (oder
umgekehrt), wird NICHT bestätigt.

Stuft ein Produkt gegen das Zielfahrzeug ein:

  - confirmed  : positiv belegte Kompatibilität — derzeit ohne autoritative
                 Parts-Fitmentquelle absichtlich nicht erreichbar.
  - uncertain  : nicht bestätigt — NIE empfehlen, klarer FIN/OE-Hinweis.
  - rejected   : klarer Widerspruch (falsche Performance-Variante, falsche Achse,
                 anderer Hersteller) — vollständig ausblenden.

KEIN Fahrzeug-Hardcoding — allgemeine Performance-/Editions-Marker & Achslogik,
gemeinsam mit app.vehicle_identity gepflegt (keine zweite, abweichende Chassis-Regex
mehr — Root-Cause-Risiko aus der Sprint-3-Exploration).
"""

import re

from app.vehicle_identity import (
    VehicleIdentity,
    chassis_codes as _chassis_codes,
    edition_markers as _edition_markers,
    marken as _marken,
    performance_markers as _performance_markers,
    tokens as _tokens,
)

# Standard-Motorcode wie "320d", "330 i", "220 d", "c220d", "e350d" — Signal, dass
# ein Produkt für eine NORMALE (Nicht-Performance-)Variante gedacht ist. Das optionale
# führende Klassen-Kürzel deckt zusammengeschriebene Codes wie "c220d" ab.
_NONPERF_MOTOR = re.compile(r"\b[a-z]?\d{3}\s?[di]\b", re.I)

# Achs-Signalwörter.
_VORNE = ("vorne", "vorder", "vorderachse", "front", " va ", "va-", "achse vorn")
_HINTEN = ("hinten", "hinter", "hinterachse", "rear", " ha ", "ha-", "achse hint")

# Kleine, deterministische Safety-Class-Policy. Sie steuert keine Produktlogik,
# sondern verhindert lediglich, dass die heutige Web-/LLM-Discovery bei besonders
# folgenreichen Teilen wie eine Fitmentquelle behandelt wird.
_SAFETY_MARKER = (
    "brems", "lenk", "fahrwerk", "stoßdämpf", "stossdaempf", "airbag", "srs",
    "abs", "esp", "hochvolt", "high voltage", "hybrid",
)

# Explizite Generationsangaben wie Golf VI/VII. Eine römische Zahl zählt nur
# direkt hinter einem Modellwort; sonst würde z.B. die Spannungsangabe "12 V" als
# Golf-V-Generation missverstanden. Chassiscodes kommen weiterhin aus der
# zentralen VehicleIdentity-Hilfe.
_ROEMISCHE_GENERATION = re.compile(
    r"\b([A-Za-zÄÖÜäöü][A-Za-zÄÖÜäöü0-9-]{1,20})\s+"
    r"(I|II|III|IV|V|VI|VII|VIII|IX|X)\b"
)

# Nur klar erkennbare Motorbezeichnungen. Die Muster sind bewusst eng; bei allem
# anderen bleibt die Einstufung UNKNOWN/uncertain statt einen Konflikt zu erfinden.
_MOTOR_SIGNATURE = re.compile(
    r"\b(?:\d(?:[.,]\d)\s*(?:tdi|tsi|tfsi|fsi|cdi|hdi|dci|mpi)|"
    r"[a-z]?\d{3}\s*(?:d|i|e|de))\b",
    re.I,
)


def _achse(text: str) -> str | None:
    t = f" {(text or '').lower()} "
    hat_v = any(w in t for w in _VORNE)
    hat_h = any(w in t for w in _HINTEN)
    if hat_v and not hat_h:
        return "vorne"
    if hat_h and not hat_v:
        return "hinten"
    return None


def _generationen(text: str) -> set[str]:
    return set(_chassis_codes(text)) | {
        f"roman:{m.group(1).lower()}:{m.group(2).lower()}"
        for m in _ROEMISCHE_GENERATION.finditer(text or "")
    }


def _motor_signaturen(text: str) -> set[str]:
    return {
        x.lower().replace(" ", "").replace(",", ".")
        for x in _MOTOR_SIGNATURE.findall(text or "")
    }


def _getriebe_klasse(identity: VehicleIdentity) -> str | None:
    wert = (identity.transmission or "").lower()
    if "schalt" in wert or "manual" in wert:
        return "manuell"
    if any(x in wert for x in ("automatik", "doppelkupplung", "dsg", "dct", "cvt")):
        return "automatik"
    return None


def ist_sicherheitskritisch(bauteil: str) -> bool:
    text = (bauteil or "").lower()
    return any(marker in text for marker in _SAFETY_MARKER)


def parse_fahrzeug(fahrzeug: str) -> dict:
    """Strukturiert das Zielfahrzeug: Marke, Modell-Token, SPEZIFISCHE Performance-
    Marker (m3, rs6, amg-c63 — nicht mehr familien-kollabiert), Sub-Editions-Marker
    (GTS/CRT/Competition/...) und Chassiscodes."""
    identity = VehicleIdentity.from_text(fahrzeug)
    return {
        "marken": _marken(fahrzeug),
        "tokens": _tokens(fahrzeug),
        "performance": _performance_markers(fahrzeug),
        "editions": _edition_markers(fahrzeug),
        "chassis": _chassis_codes(fahrzeug),
        "generationen": _generationen(fahrzeug),
        "motor_signaturen": _motor_signaturen(fahrzeug),
        "identity": identity,
    }


def parse_bauteil(bauteil: str) -> dict:
    """Strukturiert das gesuchte Bauteil: Achse (vorne/hinten), Roh-Text."""
    return {
        "achse": _achse(bauteil),
        "text": (bauteil or "").lower(),
        "sicherheitskritisch": ist_sicherheitskritisch(bauteil),
    }


def klassifiziere(fahrzeug: dict, bauteil: dict, produkt_text: str) -> tuple[str, str]:
    """Stuft ein Produkt gegen das Zielfahrzeug ein. Gibt (kompatibilitaet, grund).

    Reihenfolge: harte, explizite Widersprüche zuerst; danach immer "uncertain".
    Der aktuelle Datenbestand enthält keine autoritative Parts-Fitmentquelle. Deshalb
    dürfen Marke, Modell, Generation oder Chassis allein nie positiv bestätigen.
    """
    p = (produkt_text or "").lower()

    # ── Harte Widersprüche ────────────────────────────────────────────────────
    fz_marken = fahrzeug.get("marken") or set()
    prod_marken = _marken(p)
    if fz_marken and prod_marken and not (fz_marken & prod_marken):
        return ("rejected", f"anderer Hersteller ({', '.join(sorted(prod_marken))})")

    teil_achse = bauteil.get("achse")
    prod_achse = _achse(p)
    if teil_achse and prod_achse and teil_achse != prod_achse:
        return ("rejected", f"andere Achse (Produkt: {prod_achse}, gesucht: {teil_achse})")

    fz_generationen = fahrzeug.get("generationen") or set()
    prod_generationen = _generationen(produkt_text)
    if fz_generationen and prod_generationen and not (fz_generationen & prod_generationen):
        return ("rejected", "andere Generation/Baureihe")

    fz_identity = fahrzeug.get("identity")
    if not isinstance(fz_identity, VehicleIdentity):
        fz_identity = VehicleIdentity.from_text("")
    prod_identity = VehicleIdentity.from_text(produkt_text)

    fz_motoren = fahrzeug.get("motor_signaturen") or set()
    prod_motoren = _motor_signaturen(produkt_text)
    if fz_motoren and prod_motoren and not (fz_motoren & prod_motoren):
        return ("rejected", "andere ausdrücklich genannte Motorisierung")
    if (fz_identity.fuel and prod_identity.fuel
            and fz_identity.fuel.lower() != prod_identity.fuel.lower()):
        return ("rejected", "andere ausdrücklich genannte Kraftstoffart")
    if (fz_identity.displacement and prod_identity.displacement
            and fz_identity.displacement != prod_identity.displacement):
        return ("rejected", "anderer ausdrücklich genannter Hubraum")

    fz_getriebe = _getriebe_klasse(fz_identity)
    prod_getriebe = _getriebe_klasse(prod_identity)
    if fz_getriebe and prod_getriebe and fz_getriebe != prod_getriebe:
        return ("rejected", "andere ausdrücklich genannte Getriebeart")

    fz_perf = fahrzeug.get("performance") or set()
    prod_perf = _performance_markers(p)
    fz_ed = fahrzeug.get("editions") or set()
    prod_ed = _edition_markers(p)
    fz_chassis = fahrzeug.get("chassis") or set()
    prod_chassis = _chassis_codes(p)
    chassis_match = bool(fz_chassis & prod_chassis)

    if fz_perf:
        # Zielfahrzeug ist ein Performance-Modell (z.B. M3, RS6, AMG C63).
        if prod_perf & fz_perf:
            # SPEZIFISCHER Marker bestätigt (m3==m3, nicht nur "m"-Familie). Sub-
            # Edition muss trotzdem übereinstimmen (§3): ein GTS/CRT-only-Produkt ist
            # für den normalen M3 NICHT automatisch bestätigt, und umgekehrt.
            if prod_ed != fz_ed:
                if prod_ed - fz_ed:
                    return ("uncertain",
                            f"Produkt nennt Sondermodell/-edition ({', '.join(sorted(prod_ed))}), "
                            f"die am Zielfahrzeug nicht bestätigt ist")
                return ("uncertain",
                        f"Sondermodell/-edition des Zielfahrzeugs "
                        f"({', '.join(sorted(fz_ed))}) vom Produkt nicht bestätigt")
            if bauteil.get("sicherheitskritisch"):
                return ("uncertain", "sicherheitskritisches Teil ohne autoritative Fitment-Evidenz")
            return ("uncertain", "Performance-Variante genannt, Parts-Fitment nicht verifiziert")
        if prod_perf and not (prod_perf & fz_perf):
            return ("rejected", "andere Performance-Variante als das Zielfahrzeug")
        # Produkt nennt KEINEN Performance-Marker.
        if _NONPERF_MOTOR.search(p):
            # Klar für eine Standardvariante (z.B. 320d) -> passt NICHT zum M3.
            return ("rejected", "für Standardvariante, nicht das Performance-Modell")
        # Nicht genug Beleg, dass es das Performance-Teil ist -> nicht bestätigen.
        return ("uncertain", "Performance-Kompatibilität nicht bestätigt")

    # Zielfahrzeug ist eine Standardvariante.
    if prod_perf:
        # Nennt das Produkt AUCH eine Standard-Motorisierung (z.B. "E90 E92 320i M3"),
        # ist es ein gemischtes Angebot -> unsicher (nicht sicher passend, nicht sicher
        # falsch). Nennt es AUSSCHLIESSLICH die Performance-Variante, passt es nicht zur
        # Standardvariante -> ablehnen.
        if _NONPERF_MOTOR.search(p):
            return ("uncertain", "Angebot mischt Performance- und Standardvarianten")
        return ("rejected", "für Performance-Variante, nicht die Standardvariante")

    # Chassis-/Modelltreffer bleiben Diagnosehinweise, sind aber ohne eine
    # autoritative Parts-Fitmentquelle niemals eine positive Bestätigung.
    if bauteil.get("sicherheitskritisch"):
        return ("uncertain", "sicherheitskritisches Teil ohne autoritative Fitment-Evidenz")
    if chassis_match:
        return ("uncertain", "Baureihe/Karosseriecode plausibel, Parts-Fitment nicht verifiziert")
    if fz_marken & prod_marken and (fahrzeug.get("tokens") & _tokens(p)):
        return ("uncertain", "Marke/Modell plausibel, Parts-Fitment nicht verifiziert")
    return ("uncertain", "Kompatibilität nicht abschließend bestätigt")


HINWEIS_UNCERTAIN = "Kompatibilität nicht bestätigt – vor Bestellung per FIN/OE-Nummer prüfen."
