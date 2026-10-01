from __future__ import annotations

"""
Zentrale Fahrzeug-Identität (Reliability-Sprint 3, §1/§2/§3).

Bisher wurde dieselbe Fahrzeug-Information an mindestens fünf Stellen unterschiedlich
interpretiert: `marktvergleich.baue_ziel` aus DB-Dicts, `ersatzteil_kompat` aus einem
Freitext-String (mit einer ABWEICHENDEN Chassis-Regex), die Query-Bauer jeweils aus den
Request-Feldern. Ergebnis: derselbe Wagen wurde je nach Pipeline anders verstanden, und
detailliertere Nutzereingaben verschlechterten die Suche, weil alle Felder starr in einen
Query-String gepresst wurden.

`VehicleIdentity` ist ab jetzt die EINE strukturierte Wahrheit. Sie wird einmal gebaut
(aus DB-Kontext ODER aus Freitext) und danach von Query-Planern, Kompatibilitätsprüfung
und Diagnose gemeinsam benutzt.

Grundsätze:
- KEIN Fahrzeug-Hardcoding. Alle Parser sind generische Muster (Motorbezeichnung,
  Leistung, Getriebe, Antrieb, Karosseriecode, Performance-/Editionsmarker).
- Performance-Marker behalten ihre KONKRETE Ausprägung: "m3" != "m4", "rs3" != "rs6",
  "amg-c63" != "amg-a45". Eine Kollabierung auf ein Familien-Kürzel ("m", "rs") war die
  Ursache dafür, dass ein Teil für eine andere Performance-Variante als bestätigt galt.
- Sub-Editionen (GTS, CRT, CS, Competition, ...) sind ein EIGENER Markerraum: ein Produkt
  für eine Sonderedition ist für das Basismodell NICHT automatisch bestätigt.
- Mehrdeutige Kürzel (bloßes "R", "N", "ST") werden BEWUSST nicht als Performance-Marker
  geführt — sie schlagen in Fließtext ("2 St.", "Nr.") falsch an. Sie landen stattdessen
  in `model_variant` und wirken über den Modell-Token-Abgleich, der für "confirmed"
  ohnehin verlangt wird.
"""

import re
from dataclasses import dataclass, field

from app.kraftstoff_powertrain import canonical_fuel

# ── Performance-Marker: (kanonischer Name, Familie, Regex) ────────────────────
# Der kanonische Name ist SPEZIFISCH (m3, rs6, amg-c63). Die Familie erlaubt die
# Unterscheidung "andere Variante derselben Performance-Linie" (-> Widerspruch) von
# "ganz andere Linie" (-> ebenfalls Widerspruch) und "nur Familie genannt, keine
# Ausprägung" (-> nicht bestätigen, aber auch nicht verwerfen).
_PERF_SPEZIFISCH: list[tuple[str, re.Pattern]] = [
    # BMW M2..M8 sowie M135/M140/M235/... — Ziffer bleibt Teil des Markers.
    ("m",   re.compile(r"\bm\s?([2-8])\b", re.I)),
    ("m",   re.compile(r"\bm\s?(135|140|235|240|340|440|550|760)\b", re.I)),
    # Audi RS3..RS7 / Renault RS
    ("rs",  re.compile(r"\brs\s?(\d)\b", re.I)),
    # Mercedes-AMG mit Modellbezeichnung ("AMG C63", "C 63 AMG", "A45 AMG").
    # Der Lookahead schützt vor der Ausstattungslinie "AMG Line" (KEIN Performance-Modell).
    ("amg", re.compile(r"\bamg\b(?!\s*[- ]?line)\s*[- ]?\s*([a-z]{1,3}\s?\d{2,3})\b", re.I)),
    ("amg", re.compile(r"\b([a-z]{1,3}\s?\d{2,3})\s*[- ]?\s*amg\b(?!\s*[- ]?line)", re.I)),
]

# Familien-Marker OHNE Ausprägung (z.B. bloßes "AMG"): belegen die Linie, aber nicht
# die konkrete Variante.
_PERF_FAMILIE: list[tuple[str, re.Pattern]] = [
    ("amg",  re.compile(r"\bamg\b(?!\s*[- ]?line)", re.I)),
    ("gti",  re.compile(r"\bgti\b", re.I)),
    ("gtd",  re.compile(r"\bgtd\b", re.I)),
    ("golfr", re.compile(r"\bgolf\s?r\b", re.I)),
    ("typer", re.compile(r"\btype[\s-]?r\b", re.I)),
    ("cupra", re.compile(r"\bcupra\b", re.I)),
    ("abarth", re.compile(r"\babarth\b", re.I)),
    ("jcw",  re.compile(r"\bjcw\b|john cooper works", re.I)),
    ("opc",  re.compile(r"\bopc\b", re.I)),
]

# ── Sub-Editionen / Sondermodelle (§3) ────────────────────────────────────────
# Eigener Markerraum: ein GTS/CRT-Teil ist für das Basismodell NICHT bestätigt.
_EDITION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("gts",         re.compile(r"\bgts\b", re.I)),
    ("crt",         re.compile(r"\bcrt\b", re.I)),
    ("csl",         re.compile(r"\bcsl\b", re.I)),
    ("cs",          re.compile(r"\bcs\b", re.I)),
    ("competition", re.compile(r"\bcompetition\b", re.I)),
    ("clubsport",   re.compile(r"\bclub\s?sport\b", re.I)),
    ("blackseries", re.compile(r"\bblack\s?series\b", re.I)),
    ("trophy",      re.compile(r"\btrophy\b", re.I)),
    # "Performance" nur als eigenständiges Sondermodell-Wort, nicht in
    # "Performance-Nachbau"/"Hochleistung" o.ä. Zusammensetzungen.
    ("performance", re.compile(r"(?<![\w-])performance(?![\w-])", re.I)),
]

# Karosserie-/Chassiscode (E92, G20, W205, 8V, B9, ...).
_RE_CHASSIS = re.compile(r"\b([a-z]\d{1,3}|\d[a-z]\d?)\b", re.I)

# Marken-Kernbegriffe (nur für Hersteller-Widerspruchsprüfung; kein Vollständigkeitsanspruch).
MARKEN = frozenset({
    "bmw", "mercedes", "benz", "audi", "volkswagen", "vw", "opel", "ford", "toyota",
    "honda", "hyundai", "kia", "seat", "skoda", "peugeot", "renault", "fiat", "volvo",
    "tesla", "porsche", "mazda", "subaru", "citroen", "mini", "jaguar", "jeep",
    "dacia", "smart", "cupra", "suzuki", "mitsubishi", "nissan", "alfa", "romeo",
    "isdera", "lancia", "lexus", "land", "rover", "chevrolet", "dodge", "chrysler",
})

_RE_HUBRAUM = re.compile(r"\b(\d[.,]\d)\s*(?:l\b|liter\b|tdi|tfsi|tsi|cdi|v\d\b)?", re.I)
_RE_CCM = re.compile(r"\b(\d{3,4})\s*(?:ccm|cm³|cm3)\b", re.I)
_RE_PS = re.compile(r"\b(\d{2,4})\s*ps\b", re.I)
_RE_KW = re.compile(r"\b(\d{2,3})\s*kw\b", re.I)
_RE_JAHR = re.compile(r"\b((?:19|20)\d{2})\b")
_RE_EZ = re.compile(r"\b(\d{1,2}\s*/\s*(?:19|20)\d{2})\b")
_RE_KM = re.compile(r"\b(\d{1,3}(?:[.\s]\d{3})+|\d{4,6})\s*km\b", re.I)
_RE_ZYLINDER = re.compile(r"\b([vrib]\s?\d{1,2})\b", re.I)   # V8, R6, B4 …

_GETRIEBE_MUSTER: list[tuple[str, tuple[str, ...]]] = [
    ("Doppelkupplung", ("dkg", "dsg", "s tronic", "s-tronic", "pdk", "doppelkupplung", "edc")),
    ("Automatik",      ("automatik", "automatic", "steptronic", "tiptronic", "wandlerautomatik",
                        "multitronic", "cvt", "9g-tronic", "7g-tronic", "g-tronic")),
    ("Schaltgetriebe", ("schaltgetriebe", "handschalter", "manuell", "manual", "6-gang schalt")),
]

_ANTRIEB_MUSTER: list[tuple[str, tuple[str, ...]]] = [
    ("Allrad",        ("allrad", "awd", "4wd", "quattro", "xdrive", "4motion", "4matic",
                       "4x4", "sh-awd", "all-wheel")),
    ("Heckantrieb",   ("heckantrieb", "hinterradantrieb", "hinterrad", "rwd", "sdrive")),
    ("Frontantrieb",  ("frontantrieb", "vorderradantrieb", "vorderrad", "fwd")),
]

_KRAFTSTOFF_MUSTER: list[tuple[str, tuple[str, ...]]] = [
    ("Diesel",  ("diesel", "tdi", "cdi", "hdi", "dci", "bluetec", "crdi", "jtd")),
    ("Hybrid",  ("hybrid", "phev", "plug-in", "plugin")),
    ("Elektro", ("elektro", "electric", "bev")),
    ("Benzin",  ("benzin", "tsi", "tfsi", "petrol", "vti", "mpi", "gdi")),
]

_KAROSSERIE_MUSTER: list[tuple[str, tuple[str, ...]]] = [
    ("Coupé",       ("coupe", "coupé")),
    ("Cabrio",      ("cabrio", "cabriolet", "roadster", "spider", "spyder")),
    ("Kombi",       ("kombi", "touring", "avant", "variant", "sportstourer", "estate", "sw")),
    ("SUV",         ("suv", "geländewagen", "crossover")),
    ("Limousine",   ("limousine", "stufenheck", "sedan", "saloon", "grand sport", "grandsport")),
    ("Schrägheck",  ("schrägheck", "fließheck", "hatchback", "sports tourer")),
    ("Van",         ("van", "minivan", "kleinbus", "tourer")),
]


def tokens(text: str) -> set[str]:
    """Alle alphanumerischen Wort-Token eines Textes (lowercase)."""
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t}


def _erste(muster: list[tuple[str, tuple[str, ...]]], text: str) -> str | None:
    t = f" {(text or '').lower()} "
    for label, keys in muster:
        if any(k in t for k in keys):
            return label
    return None


def performance_markers(text: str) -> set[str]:
    """Kanonische, SPEZIFISCHE Performance-Marker eines Textes.

    Spezifisch: "m3", "rs6", "amg-c63". Wird nur die Familie genannt (bloßes "AMG"),
    entsteht der Familien-Marker "amg" ohne Ausprägung. Der Aufrufer unterscheidet
    beides über `marker_familie()`.
    """
    out: set[str] = set()
    for familie, rx in _PERF_SPEZIFISCH:
        for m in rx.finditer(text or ""):
            wert = re.sub(r"\s+", "", m.group(1)).lower()
            out.add(f"{familie}{wert}" if familie != "amg" else f"amg-{wert}")
    for familie, rx in _PERF_FAMILIE:
        if rx.search(text or ""):
            # Familien-Marker nur ergänzen, wenn noch keine spezifische Ausprägung
            # derselben Familie gefunden wurde (sonst doppelt/unschärfer).
            if not any(marker_familie(x) == familie for x in out):
                out.add(familie)
    return out


def marker_familie(marker: str) -> str:
    """Familie eines Performance-Markers ('m3'->'m', 'amg-c63'->'amg', 'gti'->'gti')."""
    if marker.startswith("amg"):
        return "amg"
    m = re.fullmatch(r"([a-z]+)\d+", marker)
    return m.group(1) if m else marker


def ist_spezifisch(marker: str) -> bool:
    """True, wenn der Marker eine konkrete Ausprägung trägt (m3, rs6, amg-c63)."""
    return bool(re.search(r"\d", marker))


def edition_markers(text: str) -> set[str]:
    """Sub-Editions-/Sondermodell-Marker (GTS, CRT, CS, Competition, ...)."""
    return {name for name, rx in _EDITION_PATTERNS if rx.search(text or "")}


def chassis_codes(text: str) -> set[str]:
    """Karosserie-/Generationscodes eines Textes (e92, g20, w205, b9, 8v).

    Zylinderangaben (V8, V6, I4 ...) sehen formal gleich aus, sind aber Motor- statt
    Chassis-Information — werden hier bewusst ausgeschlossen."""
    out = {m.group(1).replace(" ", "").lower() for m in _RE_CHASSIS.finditer(text or "")}
    return {c for c in out if not re.fullmatch(r"[vi]\d{1,2}", c)}


def marken(text: str) -> set[str]:
    return tokens(text) & MARKEN


def _int_oder_none(wert) -> int | None:
    try:
        return int(wert) if wert not in (None, "") else None
    except (TypeError, ValueError):
        return None


@dataclass
class VehicleIdentity:
    """Strukturierte Fahrzeug-Identität — die zentrale Wahrheit für Query-Planung,
    Marktvergleich, Ersatzteilsuche und Kompatibilitätsprüfung."""

    make: str | None = None
    model: str | None = None
    model_variant: str | None = None       # z.B. "M3", "Grand Sport", "320d"
    generation: str | None = None          # z.B. "G20", "B (2017-)"
    body: str | None = None                # z.B. "Coupé", "Limousine"
    year: int | None = None
    first_registration: str | None = None  # z.B. "06/2011"
    fuel: str | None = None
    engine_name: str | None = None         # Verkaufsbezeichnung, z.B. "320d", "2.0 TDI"
    engine_code: str | None = None         # z.B. "B47D20"
    displacement: str | None = None        # z.B. "2.0", "4.0"
    horsepower: int | None = None
    kw: int | None = None
    transmission: str | None = None        # "Automatik" | "Doppelkupplung" | "Schaltgetriebe"
    drivetrain: str | None = None          # "Allrad" | "Heckantrieb" | "Frontantrieb"
    mileage: int | None = None
    performance_markers: set[str] = field(default_factory=set)
    edition_markers: set[str] = field(default_factory=set)
    chassis_codes: set[str] = field(default_factory=set)
    rohtext: str = ""                      # ursprünglicher Freitext (nur Diagnose)
    powertrain: str | None = None
    field_evidence: dict[str, dict] = field(default_factory=dict)

    # ── Abgeleitete Sichten ───────────────────────────────────────────────────

    def essenziell(self) -> str:
        """Kürzeste eindeutige Fahrzeugbezeichnung: Marke + Modell (+ Variante) + Code.

        Genau das, was eine Suchmaschine braucht — und NICHT jedes bekannte Detail.
        Mehr Nutzerdetail darf die Trefferlage nie verschlechtern (§4)."""
        teile: list[str] = []
        gesehen: set[str] = set()

        def anhaengen(wert: str | None) -> None:
            if not wert:
                return
            neu = [t for t in re.split(r"\s+", str(wert).strip()) if t and t.lower() not in gesehen]
            if neu:
                gesehen.update(t.lower() for t in neu)
                teile.append(" ".join(neu))

        anhaengen(self.make)
        anhaengen(self.model)
        anhaengen(self.model_variant)
        code = self.generation_code()
        anhaengen(code.upper() if code else None)
        return " ".join(teile).strip()

    def generation_code(self) -> str | None:
        """Reiner Generationscode ('G20' aus 'G20 (2018-)'), falls vorhanden.

        Codes, die Teil eines Performance-Markers sind (die '63' aus 'AMG C63'), zählen
        nicht als Generationscode — sonst wird die Modellbezeichnung zur Baureihe."""
        perf_text = " ".join(sorted(self.performance_markers)).replace("-", "")
        for quelle in (self.generation, *sorted(self.chassis_codes)):
            if not quelle:
                continue
            m = _RE_CHASSIS.search(quelle)
            if m and m.group(1).replace(" ", "").lower() not in perf_text:
                return m.group(1).replace(" ", "")
        return None

    def motor_kurz(self) -> str | None:
        """Kompakte Motorangabe für Queries: Verkaufsbezeichnung ODER Hubraum+Kraftstoff."""
        if self.engine_name:
            return self.engine_name
        if self.displacement and self.fuel:
            return f"{self.displacement} {self.fuel}"
        return self.displacement or self.fuel

    def leistung_kurz(self) -> str | None:
        if self.horsepower:
            return f"{self.horsepower} PS"
        if self.kw:
            return f"{self.kw} kW"
        return None

    def modell_tokens(self) -> set[str]:
        """Token, die das Modell/die Variante identifizieren (für Kompatibilitätsprüfung)."""
        out = tokens(f"{self.model or ''} {self.model_variant or ''} {self.engine_name or ''}")
        return {t for t in out if len(t) >= 2}

    def belegte_felder(self) -> list[str]:
        """Welche Felder tatsächlich gefüllt sind — für die Query-Diagnose (§9/§34)."""
        namen = ("make", "model", "model_variant", "generation", "body", "year",
                 "first_registration", "fuel", "engine_name", "engine_code",
                 "displacement", "horsepower", "kw", "transmission", "drivetrain", "mileage")
        return [n for n in namen if getattr(self, n)]

    def as_diagnose(self) -> dict:
        return {
            n: (sorted(v) if isinstance(v, set) else v)
            for n, v in self.__dict__.items() if v and n != "rohtext"
        }

    def apply_web_evidence(self, web_recherche) -> None:
        """Belegte Web-Identität in die kanonische Identität übernehmen (§5.6).

        Provenienzregeln (KaufCheck-Final-Stabilization, Invarianten 1/2/8):

          * Ein Nutzer- oder DB-Wert wird NIE überschrieben. Bestätigt Web
            denselben Wert, wird "web" in `confirmed_by` ergänzt — die
            Primärquelle bleibt die Nutzereingabe bzw. ENFAL.
          * Widerspricht Web einem vorhandenen Wert, bleibt der Wert, und der
            Konflikt wird sichtbar vermerkt (`web_conflict`), nie still gelöst.
          * Nur echte Lücken (`unknown`/`ambiguous`) füllt Web — dann mit
            `primary_source="web"` und der Confidence der Beleglage.
          * Übernommen werden ausschließlich Werte, die aus den Quellentexten
            extrahiert und per Konsens bestätigt wurden
            (`WebVehicleIdentity.feldwerte`). Die frühere Fassung kopierte die
            NUTZERANGABE "motor" in die Web-Identität und machte sie so zu einem
            scheinbar web-belegten Wert — genau die Umetikettierung einer
            Nutzereingabe, die Invariante 2 verbietet.
        """
        identitaet = getattr(web_recherche, "identitaet", None) if web_recherche else None
        if not identitaet or not getattr(identitaet, "belegt", False):
            return
        quellen_anzahl = len(getattr(identitaet, "quellen", None) or [])
        felder = getattr(identitaet, "feldwerte", None) or {}
        # Marke/Modell selbst sind durch die belegte Identität bestätigt.
        for feld in ("make", "model"):
            fe = self.field_evidence.get(feld)
            if fe and getattr(self, feld):
                if "web" not in fe.setdefault("confirmed_by", []):
                    fe["confirmed_by"].append("web")
                if "web" not in fe.setdefault("provenance", []):
                    fe["provenance"].append("web")
                if fe.get("verification_state") == "user_only":
                    fe["verification_state"] = "user_confirmed"
                    fe["status"] = "confirmed"
                    fe["confidence"] = getattr(identitaet, "confidence", None) or "mittel"
        for feld, eintrag in felder.items():
            wert = eintrag.get("value")
            if wert in (None, ""):
                continue
            conf = eintrag.get("confidence") or "niedrig"
            fe = dict(self.field_evidence.get(feld) or {})
            state = fe.get("verification_state") or ("unknown" if getattr(self, feld, None) in (None, "")
                                                    else "reference_only")
            if state in ("unknown", "ambiguous"):
                moegliche = fe.get("possible_values") or []
                if moegliche and _norm_wert(feld, wert) not in {_norm_wert(feld, x) for x in moegliche}:
                    # Web behauptet einen Wert AUSSERHALB der möglichen Menge:
                    # als Konflikt vermerken, nichts übernehmen.
                    fe["web_conflict"] = wert
                    self.field_evidence[feld] = fe
                    continue
                setattr(self, feld, wert)
                neu = _feld_eintrag(
                    wert, primary="web", confirmed_by=[], confidence=conf,
                    state="web_only", raw_user=fe.get("raw_user_value"),
                    reference=fe.get("reference_value"),
                    evidence=f"{eintrag.get('domains') or quellen_anzahl} Webquelle(n)")
                if eintrag.get("detail"):
                    neu["detail"] = eintrag["detail"]
                self.field_evidence[feld] = neu
                continue
            vorhanden = getattr(self, feld, None)
            if vorhanden in (None, ""):
                continue
            if (_norm_wert(feld, vorhanden) == _norm_wert(feld, wert)
                    or _norm_wert(feld, fe.get("raw_user_value")) == _norm_wert(feld, wert)):
                if "web" not in fe.setdefault("confirmed_by", []):
                    fe["confirmed_by"].append("web")
                if "web" not in fe.setdefault("provenance", []):
                    fe["provenance"].append("web")
                if eintrag.get("detail") and not fe.get("detail"):
                    fe["detail"] = eintrag["detail"]
                if fe.get("verification_state") == "user_only":
                    # Nutzerangabe bleibt Primärquelle, ist jetzt aber unabhängig
                    # (durch Webquellen) bestätigt.
                    fe["verification_state"] = "user_confirmed"
                    fe["status"] = "confirmed"
                    fe["confidence"] = "mittel"
                elif not fe:
                    fe.update(_feld_eintrag(vorhanden, primary="user", confirmed_by=["web"],
                                            confidence="mittel", state="user_confirmed",
                                            raw_user=vorhanden))
            else:
                fe["web_conflict"] = wert
            self.field_evidence[feld] = fe

    # ── Konstruktoren ─────────────────────────────────────────────────────────

    @classmethod
    def from_check_context(cls, baureihe: dict | None, motor_match: dict | None, req) -> "VehicleIdentity":
        """Die EINE request-lokale kanonische Identität des KaufChecks.

        PROVENIENZ-MODELL (KaufCheck-Final-Stabilization, Cluster A)
        ------------------------------------------------------------
        Vorher galt: sobald die DB einen Wert kannte, hieß das Feld
        "identified" ("ENFAL-Referenz eindeutig zugeordnet") — auch wenn der
        Nutzer genau diesen Wert selbst eingegeben hatte. Die Nutzereingabe
        verlor ihre Herkunft; DB-Daten etikettierten sie still um.

        Jetzt trägt jedes Feld:
          value              kanonischer (ggf. von der DB präzisierter) Wert
          primary_source     "user" | "enfal" | "web" | None
          confirmed_by       unabhängige Bestätigungen, z.B. ["enfal"]
          confidence         Beleglage
          verification_state user_only | user_confirmed | user_refined |
                             conflict | reference_only | plausible |
                             web_only | ambiguous | unknown
          raw_user_value     die unveränderte Nutzereingabe
          possible_values    nur bei ambiguous (z.B. mehrere Motorcodes)
        plus die bisherigen Schlüssel (status/provided_value/reference_value/
        provenance) für bestehende Konsumenten.

        Regeln: Nutzer und DB stimmen überein -> Primärquelle Nutzer, DB
        bestätigt. DB ist präziser (B48 -> B48B20) -> DB präzisiert den Wert,
        die Rohangabe bleibt erhalten. Widerspruch -> der Nutzerwert bleibt,
        der Konflikt wird ausgewiesen (nie still überschrieben).
        """
        from app.car_lookup import _antrieb_aus_text
        from app.getriebe import aus_request, aus_db
        from app.kraftstoff_powertrain import fuel_aus_freitext
        b, m = baureihe or {}, motor_match or {}
        identity = cls.from_market_context(b, m, req)
        identity.field_evidence = {}
        text = " ".join(str(getattr(req, f, None) or "") for f in
                        ("motor", "antrieb", "beschreibung", "freitext"))
        motor_roh = (getattr(req, "motor", None) or "").strip() or None
        user_ps = getattr(req, "leistung_ps", None) or _erste_zahl(_RE_PS, motor_roh or "")
        user_fuel_roh = getattr(req, "kraftstoff", None)
        user_fuel = fuel_aus_freitext(user_fuel_roh) if user_fuel_roh else None
        db_codes = motorcodes(m.get("motorcode"))
        user_code = motorcode_kandidat(motor_roh)
        evid_m = m.get("variante_id")
        evid_b = b.get("id")

        def setze(feld, wert, eintrag):
            setattr(identity, feld, wert)
            identity.field_evidence[feld] = eintrag

        # Marke / Modell: die DB normalisiert die Schreibweise; stammt die
        # Zuordnung aus der Nutzereingabe, bleibt der Nutzer Primärquelle.
        for feld, roh, db_wert in (("make", getattr(req, "marke", None), b.get("marke")),
                                   ("model", getattr(req, "modell", None), b.get("modell"))):
            if db_wert:
                setze(feld, db_wert, _feld_eintrag(
                    db_wert, primary="user" if roh else "enfal",
                    confirmed_by=["enfal"] if roh else [], confidence="hoch",
                    state="user_confirmed" if roh else "reference_only",
                    raw_user=roh, reference=db_wert, evidence=evid_b))
            elif roh:
                wert = anzeige_marke(roh) if feld == "make" else roh.strip()
                setze(feld, wert, _feld_eintrag(wert, primary="user", confidence="niedrig",
                                                state="user_only", raw_user=roh))
            else:
                setze(feld, None, _feld_eintrag(None))

        # Baujahr: Nutzerangabe; der DB-Bauzeitraum plausibilisiert nur.
        jahr = _int_oder_none(getattr(req, "baujahr", None))
        von, bis = b.get("bauzeitraum_von"), b.get("bauzeitraum_bis")
        im_zeitraum = bool(jahr and von and von <= jahr <= (bis or 9999))
        setze("year", jahr, _feld_eintrag(
            jahr, primary="user" if jahr else None,
            confirmed_by=["enfal"] if im_zeitraum else [],
            confidence=("mittel" if im_zeitraum else "niedrig") if jahr else "unbekannt",
            state=("user_confirmed" if im_zeitraum else "user_only") if jahr else "unknown",
            raw_user=getattr(req, "baujahr", None),
            reference=(f"{von}-{bis or ''}" if von else None), evidence=evid_b))

        # Generation: die DB kennt sie als Referenz — ABER manche Baureihen-
        # Datensätze fassen mehrere Werkscodes in EINEM `generation`-Feld
        # zusammen ("G20/G21", siehe app/chassis_codes.py). Nennt der Nutzer
        # selbst einen dieser Codes explizit ("... F82 Coupé ..."), ist das die
        # PRÄZISERE Angabe — sie darf nicht von der gröberen DB-Referenz
        # überschrieben werden (Release-Hardening, Invariante "User Precision").
        # Derselbe generische chassis_codes()-Extraktor, der auch Web-Titel
        # liest — keine neue, baureihenspezifische Regel.
        gen = b.get("generation")
        gen_codes = chassis_codes(gen) if gen else set()
        nutzer_codes = chassis_codes(f"{text} {getattr(req, 'modell', None) or ''}")
        praezise = sorted(gen_codes & nutzer_codes)
        if gen and len(gen_codes) > 1 and len(praezise) == 1:
            gen_praezise = praezise[0].upper()
            setze("generation", gen_praezise, _feld_eintrag(
                gen_praezise, primary="user", confirmed_by=["enfal"], confidence="hoch",
                state="user_confirmed", raw_user=gen_praezise, reference=gen, evidence=evid_b))
        else:
            setze("generation", gen, _feld_eintrag(
                gen, primary="enfal" if gen else None, confidence="hoch" if gen else "unbekannt",
                state="reference_only" if gen else "unknown", reference=gen, evidence=evid_b))

        # Leistung.
        db_ps = _int_oder_none(m.get("leistung_ps"))
        setze("horsepower", *_vergleiche("horsepower", user_ps, db_ps,
                                         getattr(req, "leistung_ps", None) or user_ps, evid_m))

        # Kraftstoffart — nie der rohe DB-Powertrainwert.
        db_fuel = canonical_fuel(m.get("kraftstoff"), m.get("bezeichnung"), m.get("motorcode"))
        setze("fuel", *_vergleiche("fuel", user_fuel, db_fuel, user_fuel_roh, evid_m))

        # Getriebe.
        user_getriebe = aus_request(req)
        db_getriebe = aus_db(m) if m else None
        setze("transmission", *_vergleiche("transmission", user_getriebe, db_getriebe,
                                           getattr(req, "getriebe", None) or user_getriebe, evid_m))
        if m.get("getriebe"):
            identity.field_evidence["transmission"]["reference_options"] = _optionen(m.get("getriebe"))

        # Antrieb.
        user_antrieb = antrieb_nutzer(req) or _antrieb_aus_text(text)
        db_antrieb = (m.get("antrieb") or None) if m else None
        setze("drivetrain", *_vergleiche("drivetrain", user_antrieb, db_antrieb, user_antrieb, evid_m))

        # Motorcode (Cluster F): ein DB-Satz mit MEHREREN Codes identifiziert
        # nicht den einen Motor dieses Fahrzeugs.
        setze("engine_code", *_motorcode_aufloesen(user_code, db_codes, evid_m))

        # Antriebsart / Elektrifizierung (Cluster D/E).
        pt_wert, pt_eintrag = _powertrain_aufloesen(b, m, req, identity.fuel, user_code)
        setze("powertrain", pt_wert, pt_eintrag)

        # Motorbezeichnung: die DB liefert die Referenzvariante, die Nutzer-
        # angabe bleibt als Rohwert erhalten. Solange die Elektrifizierung nicht
        # gesichert ist, trägt die Anzeige keinen "Mild-Hybrid"-Zusatz.
        db_name = m.get("bezeichnung") or None
        if db_name and pt_eintrag.get("verification_state") in ("ambiguous", "unknown"):
            db_name = ohne_elektrifizierung(db_name)
        if db_name:
            setze("engine_name", db_name, _feld_eintrag(
                db_name, primary="user" if motor_roh else "enfal",
                confirmed_by=["enfal"] if motor_roh else [], confidence="hoch",
                state="user_refined" if motor_roh else "reference_only",
                raw_user=motor_roh, reference=m.get("bezeichnung"), evidence=evid_m))
        elif motor_roh:
            setze("engine_name", motor_roh, _feld_eintrag(
                motor_roh, primary="user", confidence="niedrig", state="user_only",
                raw_user=motor_roh))
        else:
            setze("engine_name", None, _feld_eintrag(None))

        identity.mileage = _int_oder_none(getattr(req, "kilometerstand", None))
        # A generation-wide body/options set is not this vehicle's equipment.
        identity.body = None
        return identity

    @classmethod
    def from_market_context(cls, baureihe: dict | None, motor_match: dict | None,
                            req) -> "VehicleIdentity":
        """Aus dem Kauf-/Verkaufscheck-Kontext: DB-Baureihe + Motorvariante + Request.

        Strukturierte DB-Werte haben Vorrang; fehlt etwas, wird aus den Freitextfeldern
        des Requests (Beschreibung/Inserat/Freitext) generisch nachgeparst.
        """
        b = baureihe or {}
        m = motor_match or {}

        freitext = " ".join(str(getattr(req, f, "") or "") for f in
                            ("motor", "beschreibung", "inserat_text", "inserat_titel",
                             "freitext", "getriebe", "kraftstoff"))
        ausstattung = getattr(req, "ausstattung", None) or []
        if isinstance(ausstattung, (list, tuple)):
            freitext += " " + " ".join(str(a) for a in ausstattung)

        make = b.get("marke") or getattr(req, "marke", None)
        model = b.get("modell") or getattr(req, "modell", None)
        engine_name = m.get("bezeichnung") or getattr(req, "motor", None)

        hubraum_ccm = _int_oder_none(m.get("hubraum_ccm"))
        displacement = f"{hubraum_ccm / 1000:.1f}" if hubraum_ccm else None
        if not displacement:
            hm = _RE_HUBRAUM.search(freitext)
            displacement = hm.group(1).replace(",", ".") if hm else None

        karosserie = b.get("karosserie")
        if isinstance(karosserie, (list, tuple)):
            karosserie = karosserie[0] if karosserie else None
        body = (str(karosserie) if karosserie else None) or _erste(_KAROSSERIE_MUSTER, freitext)

        identity = cls(
            make=make,
            model=model,
            model_variant=_variant_aus_text(f"{model or ''} {engine_name or ''} {freitext}", model),
            generation=b.get("generation"),
            body=body,
            year=_int_oder_none(getattr(req, "baujahr", None)),
            first_registration=(_RE_EZ.search(freitext).group(1) if _RE_EZ.search(freitext) else None),
            # KraftstoffART, nie der rohe DB-Powertrain-Wert ('Mild-Hybrid'/
            # 'Plug-in-Hybrid' sind Antriebsarten — app/kraftstoff_powertrain.py).
            fuel=(canonical_fuel(m.get("kraftstoff"), m.get("bezeichnung"), m.get("motorcode"))
                  or getattr(req, "kraftstoff", None) or _erste(_KRAFTSTOFF_MUSTER, freitext)),
            engine_name=engine_name,
            engine_code=m.get("motorcode"),
            displacement=displacement,
            horsepower=_int_oder_none(m.get("leistung_ps")) or _erste_zahl(_RE_PS, freitext),
            kw=_int_oder_none(m.get("leistung_kw")) or _erste_zahl(_RE_KW, freitext),
            transmission=(getattr(req, "getriebe", None) or m.get("getriebe")
                          or _erste(_GETRIEBE_MUSTER, freitext)),
            drivetrain=m.get("antrieb") or _erste(_ANTRIEB_MUSTER, freitext),
            mileage=_int_oder_none(getattr(req, "kilometerstand", None)),
            rohtext=freitext.strip(),
        )
        # Marker aus allen bekannten Bezeichnern (nicht aus dem gesamten Fließtext —
        # eine Ausstattungszeile "Sportpaket" soll kein Performance-Modell erzeugen).
        bezeichner = " ".join(str(x) for x in
                              (make, model, identity.model_variant, engine_name,
                               b.get("generation"), b.get("id")) if x)
        identity.performance_markers = performance_markers(bezeichner)
        identity.edition_markers = edition_markers(bezeichner)
        identity.chassis_codes = chassis_codes(f"{b.get('generation') or ''} {b.get('id') or ''}")
        return identity

    @classmethod
    def from_text(cls, text: str) -> "VehicleIdentity":
        """Aus einem einzelnen Freitext-Feld (Ersatzteilsuche).

        Erkennt Marke, Modell/Variante, Generationscode, Motor, Leistung, Getriebe,
        Antrieb, Karosserie, Baujahr und Laufleistung — generisch, ohne Fahrzeugliste.
        """
        roh = (text or "").strip()
        gefundene_marken = marken(roh)
        make = None
        for t in re.split(r"[^A-Za-z0-9]+", roh):
            if t.lower() in gefundene_marken:
                make = t
                break

        codes = chassis_codes(roh)
        perf = performance_markers(roh)
        ed = edition_markers(roh)
        variant = _variant_aus_text(roh, None)
        # Token, die zu Markern/Varianten gehören, sind kein Modellname.
        marker_tokens = tokens(" ".join(sorted(perf) + sorted(ed) + [variant or ""]))

        model, generation = _modell_und_generation(roh, make, codes, marker_tokens)

        jahre = [int(j) for j in _RE_JAHR.findall(roh) if 1950 <= int(j) <= 2030]
        km_m = _RE_KM.search(roh)
        hub_ccm = _RE_CCM.search(roh)
        hub_l = _RE_HUBRAUM.search(roh)

        identity = cls(
            make=make,
            model=model,
            model_variant=variant,
            generation=(generation.upper() if generation else None),
            body=_erste(_KAROSSERIE_MUSTER, roh),
            year=(min(jahre) if jahre else None),
            first_registration=(_RE_EZ.search(roh).group(1) if _RE_EZ.search(roh) else None),
            fuel=_erste(_KRAFTSTOFF_MUSTER, roh),
            engine_name=_motorbezeichnung(roh),
            displacement=(f"{int(hub_ccm.group(1)) / 1000:.1f}" if hub_ccm
                          else (hub_l.group(1).replace(",", ".") if hub_l else None)),
            horsepower=_erste_zahl(_RE_PS, roh),
            kw=_erste_zahl(_RE_KW, roh),
            transmission=_erste(_GETRIEBE_MUSTER, roh),
            drivetrain=_erste(_ANTRIEB_MUSTER, roh),
            mileage=(int(re.sub(r"[.\s]", "", km_m.group(1))) if km_m else None),
            performance_markers=perf,
            edition_markers=edition_markers(roh),
            chassis_codes=codes,
            rohtext=roh,
        )
        return identity



# ── Provenienz-Hilfen (KaufCheck-Final-Stabilization) ─────────────────────────

# Alte Statuswerte bleiben für bestehende Konsumenten erhalten, abgeleitet aus
# dem neuen verification_state.
_STATUS_AUS_STATE = {
    "user_only": "provided", "user_confirmed": "confirmed", "user_refined": "confirmed",
    "conflict": "conflict", "reference_only": "identified", "web_only": "web_verified",
    "ambiguous": "ambiguous", "unknown": "unknown", "plausible": "plausible",
}


def _feld_eintrag(value=None, *, primary: str | None = None, confirmed_by=None,
                  confidence: str = "unbekannt", state: str = "unknown", raw_user=None,
                  reference=None, evidence=None, possible=None) -> dict:
    confirmed = list(confirmed_by or [])
    provenance = ([primary] if primary else []) + [c for c in confirmed if c != primary]
    eintrag = {
        "value": value, "primary_source": primary, "confirmed_by": confirmed,
        "confidence": confidence, "verification_state": state,
        "raw_user_value": raw_user,
        # Legacy-Sicht:
        "status": _STATUS_AUS_STATE.get(state, "unknown"),
        "provided_value": raw_user, "reference_value": reference,
        "provenance": provenance, "evidence": evidence,
    }
    if possible:
        eintrag["possible_values"] = list(possible)
    return eintrag


def _norm_wert(feld: str, wert) -> str:
    if wert is None:
        return ""
    t = str(wert).strip().lower()
    if feld == "drivetrain":
        return {"heckantrieb": "heck", "hinterradantrieb": "heck", "hinterrad": "heck",
                "rwd": "heck", "frontantrieb": "front", "vorderradantrieb": "front",
                "vorderrad": "front", "fwd": "front", "allradantrieb": "allrad",
                "awd": "allrad"}.get(t, t)
    if feld == "fuel":
        from app.kraftstoff_powertrain import fuel_aus_freitext
        return fuel_aus_freitext(t) or t
    if feld == "transmission":
        from app.getriebe import normalisiere
        return normalisiere(t) or t
    return re.sub(r"[\s\-]+", "", t)


def _vergleiche(feld: str, user, db, raw_user, evidence) -> tuple[object, dict]:
    """Ein Feld aus Nutzer- und DB-Wert — ohne stille Umetikettierung."""
    if user is not None and db is not None:
        if feld == "horsepower":
            gleich = abs(int(user) - int(db)) <= 2
        else:
            gleich = _norm_wert(feld, user) == _norm_wert(feld, db)
        if gleich:
            return user, _feld_eintrag(user, primary="user", confirmed_by=["enfal"],
                                       confidence="hoch", state="user_confirmed",
                                       raw_user=raw_user, reference=db, evidence=evidence)
        return user, _feld_eintrag(user, primary="user", confidence="niedrig", state="conflict",
                                   raw_user=raw_user, reference=db, evidence=evidence)
    if user is not None:
        return user, _feld_eintrag(user, primary="user", confidence="niedrig", state="user_only",
                                   raw_user=raw_user, evidence=evidence)
    if db is not None:
        return db, _feld_eintrag(db, primary="enfal", confidence="mittel", state="reference_only",
                                 reference=db, evidence=evidence)
    return None, _feld_eintrag(None)


def _optionen(getriebe) -> list[str]:
    if isinstance(getriebe, str):
        roh = getriebe.strip().strip("[]")
        return [t.strip().strip('"').strip("'") for t in roh.split(",") if t.strip()]
    return [str(g) for g in (getriebe or [])]


_RE_CODE_TRENNER = re.compile(r"\s*(?:,|/|;|\bund\b|\boder\b)\s*", re.I)


def motorcodes(roh: str | None) -> list[str]:
    """Einzelne Motorcodes eines DB-Felds ("B14XFL, D14XFL, F14XFL" -> 3 Codes).
    Klammerzusätze sind Aliasse desselben Codes, keine eigenen Motoren."""
    out: list[str] = []
    for teil in _RE_CODE_TRENNER.split(roh or ""):
        teil = re.sub(r"\([^)]*\)", "", teil).strip()
        if teil and teil not in out:
            out.append(teil)
    return out


def _code_passt(user_code: str | None, db_code: str | None) -> bool:
    u, d = _norm_wert("engine_code", user_code), _norm_wert("engine_code", db_code)
    return bool(u and d) and (d.startswith(u) or u.startswith(d))


def _motorcode_aufloesen(user_code: str | None, db_codes: list[str], evidence) -> tuple[object, dict]:
    """Drei Zustände (Cluster F): eindeutig, mehrdeutig ("mögliche Motorcodes"),
    unbekannt. Mehrere Codes werden NIE als die Identität des einen Motors
    dargestellt."""
    if user_code and db_codes:
        passend = [c for c in db_codes if _code_passt(user_code, c)]
        if len(passend) == 1:
            code = passend[0]
            state = ("user_confirmed" if _norm_wert("engine_code", code)
                     == _norm_wert("engine_code", user_code) else "user_refined")
            return code, _feld_eintrag(code, primary="user", confirmed_by=["enfal"],
                                       confidence="hoch", state=state, raw_user=user_code,
                                       reference=", ".join(db_codes), evidence=evidence)
        if len(passend) > 1:
            return user_code, _feld_eintrag(user_code, primary="user", confidence="mittel",
                                            state="user_only", raw_user=user_code,
                                            reference=", ".join(db_codes), evidence=evidence,
                                            possible=passend)
        return user_code, _feld_eintrag(user_code, primary="user", confidence="niedrig",
                                        state="conflict", raw_user=user_code,
                                        reference=", ".join(db_codes), evidence=evidence)
    if user_code:
        return user_code, _feld_eintrag(user_code, primary="user", confidence="niedrig",
                                        state="user_only", raw_user=user_code, evidence=evidence)
    if len(db_codes) == 1:
        return db_codes[0], _feld_eintrag(db_codes[0], primary="enfal", confidence="mittel",
                                          state="reference_only", reference=db_codes[0],
                                          evidence=evidence)
    if len(db_codes) > 1:
        return None, _feld_eintrag(None, confidence="niedrig", state="ambiguous",
                                   reference=", ".join(db_codes), evidence=evidence,
                                   possible=db_codes)
    return None, _feld_eintrag(None)


_RE_ELEKTRIFIZIERUNG = re.compile(r"\s*\(?\b(?:mild[\s-]?hybrid|mhev|48\s?v(?:olt)?)\b\)?", re.I)


def ohne_elektrifizierung(bezeichnung: str | None) -> str | None:
    """Variantenname ohne Mild-Hybrid-Zusatz — nur für die Anzeige, solange die
    Elektrifizierung dieses Fahrzeugs nicht gesichert ist."""
    if not bezeichnung:
        return bezeichnung
    return re.sub(r"\s{2,}", " ", _RE_ELEKTRIFIZIERUNG.sub("", bezeichnung)).strip() or bezeichnung


def _powertrain_nutzer(req) -> str | None:
    """Antriebsart aus der Nutzereingabe — dieselbe zentrale Erkennung wie
    jeder Scope-Vergleich (app/kraftstoff_powertrain.powertrain_aus_freitext)."""
    from app.kraftstoff_powertrain import powertrain_aus_freitext
    return powertrain_aus_freitext(getattr(req, "powertrain", None), getattr(req, "kraftstoff", None))


def _powertrain_aufloesen(b: dict, m: dict, req, fuel, user_code) -> tuple[object, dict]:
    """Antriebsart mit Mehrdeutigkeitsbewusstsein (Cluster D/E).

    ENFAL-Motorzeilen sind NICHT nach Modelljahr getrennt: eine Zeile
    "Mild-Hybrid" kann mehrere Motorcodes/Revisionen bündeln, von denen nicht
    alle elektrifiziert sind (48-V-Systeme kamen innerhalb einer Motorfamilie
    nachträglich). Deshalb gilt generisch:

      * Nutzerangabe -> Primärquelle Nutzer.
      * Kraftstoff Elektro -> BEV.
      * Mögliche Antriebsarten = die der zugeordneten Zeile plus aller
        Schwesterzeilen derselben Baureihe mit gleicher Leistung und
        verträglichem Kraftstoff. Eine Mild-Hybrid-Zeile, die nicht über GENAU
        EINEN, vom Nutzer genannten Motorcode belegt ist, schließt die nicht
        elektrifizierte Revision nicht aus -> {ICE, MHEV}.
      * Mehr als eine mögliche Antriebsart -> ambiguous, Wert None.
    """
    from app.kraftstoff_powertrain import canonical_powertrain, canonical_fuel
    nutzer = _powertrain_nutzer(req)
    if nutzer:
        ref = canonical_powertrain(m.get("kraftstoff")) if m else None
        if ref == nutzer:
            state = "user_confirmed"
        elif ref and not (ref == "MHEV" and nutzer == "ICE") and not (ref == "ICE" and nutzer == "MHEV"):
            state = "conflict"
        else:
            state = "user_only"
        return nutzer, _feld_eintrag(nutzer, primary="user",
                                     confirmed_by=["enfal"] if state == "user_confirmed" else [],
                                     confidence="hoch" if state == "user_confirmed" else "niedrig",
                                     state=state, raw_user=getattr(req, "powertrain", None)
                                     or getattr(req, "kraftstoff", None),
                                     reference=m.get("kraftstoff") if m else None,
                                     evidence=m.get("variante_id") if m else None)
    if str(fuel or "").lower() == "elektro":
        return "BEV", _feld_eintrag("BEV", primary="enfal" if m else "user", confidence="mittel",
                                    state="plausible" if m else "user_only",
                                    evidence=m.get("variante_id") if m else None)
    if not m:
        return None, _feld_eintrag(None)
    eigen = canonical_powertrain(m.get("kraftstoff"))
    if not eigen:
        return None, _feld_eintrag(None)
    moeglich: list[str] = [eigen]
    ps = _int_oder_none(m.get("leistung_ps"))
    eigen_fuel = canonical_fuel(m.get("kraftstoff"), m.get("bezeichnung"), m.get("motorcode"))
    ziel_fuel = str(fuel or eigen_fuel or "").lower() or None
    for s in (b.get("motoren") or []):
        if s is m or (s.get("variante_id") and s.get("variante_id") == m.get("variante_id")):
            continue
        s_ps = _int_oder_none(s.get("leistung_ps"))
        if ps is None or s_ps is None or abs(s_ps - ps) > 2:
            continue
        s_fuel = canonical_fuel(s.get("kraftstoff"), s.get("bezeichnung"), s.get("motorcode"))
        if ziel_fuel and s_fuel and s_fuel != ziel_fuel:
            continue
        pt = canonical_powertrain(s.get("kraftstoff"))
        if pt and pt not in moeglich:
            moeglich.append(pt)
    codes = motorcodes(m.get("motorcode"))
    code_belegt = bool(user_code) and len(codes) == 1 and _code_passt(user_code, codes[0])
    if eigen == "MHEV" and not code_belegt and "ICE" not in moeglich:
        moeglich.append("ICE")
    if len(moeglich) > 1:
        return None, _feld_eintrag(None, confidence="niedrig", state="ambiguous",
                                   reference=m.get("kraftstoff"), evidence=m.get("variante_id"),
                                   possible=sorted(moeglich))
    return eigen, _feld_eintrag(eigen, primary="enfal", confidence="mittel", state="plausible",
                                reference=m.get("kraftstoff"), evidence=m.get("variante_id"))


_ANTRIEB_FELD = (("Allrad", re.compile(r"allrad|4x4|awd|4wd|quattro|xdrive|4matic|4motion", re.I)),
                 ("Heck", re.compile(r"(?<![a-z])heck|hinterrad|rwd", re.I)),
                 ("Front", re.compile(r"(?<![a-z])front|vorderrad|fwd", re.I)))


def antrieb_nutzer(req) -> str | None:
    """Antrieb aus der NUTZEREINGABE: zuerst das strukturierte Feld `antrieb`
    ("Heck", "Front", "Allrad"), dann ein eindeutiges Wort im Motorfeld
    ("3.0 Biturbo Heck"). Freitext-Beschreibungen bleiben beim bestehenden,
    strengeren Parser (`car_lookup._antrieb_aus_text`)."""
    for text in (getattr(req, "antrieb", None), getattr(req, "motor", None)):
        treffer = {wert for wert, rx in _ANTRIEB_FELD if rx.search(str(text or ""))}
        if len(treffer) == 1:
            return treffer.pop()
    return None


def anzeige_marke(marke: str | None) -> str | None:
    """Anzeigeform einer Marke ohne DB-Treffer: bekannte Aliasse normalisiert,
    sonst die Nutzerschreibweise — reine Kleinschreibung ("mazda") wird zu
    "Mazda", eine bewusste Schreibweise ("BMW", "DS") bleibt erhalten."""
    if not marke:
        return None
    from app.car_lookup import normalisiere_marke
    norm = normalisiere_marke(marke)
    roh = marke.strip()
    if norm and norm != norm.casefold():
        return norm            # bekannter Alias mit kanonischer Schreibweise
    if roh.islower():
        return "-".join(t[:1].upper() + t[1:] for t in roh.split("-"))
    return roh

def _erste_zahl(rx: re.Pattern, text: str) -> int | None:
    m = rx.search(text or "")
    return int(m.group(1)) if m else None


# Einheiten-/Füllwort-Token, die nie ein Modellname oder eine Motorbezeichnung sind.
_EINHEITEN = frozenset({"ps", "kw", "km", "ccm", "cm", "l", "liter", "nm", "eur", "euro",
                        "tkm", "kmh", "zyl", "gang", "türer", "tuerer"})
_FUELLWOERTER = frozenset({"und", "mit", "ohne", "der", "die", "das", "von", "für", "fuer",
                           "gebraucht", "gebrauchtwagen", "auto", "fahrzeug", "pkw", "bj",
                           "baujahr", "ez", "erstzulassung", "modell", "typ"})


def _stopwort_tokens() -> frozenset[str]:
    """Alle Schlüsselwörter der Attribut-Muster (Getriebe/Antrieb/Kraftstoff/Karosserie)
    als Token — sie beschreiben Eigenschaften, nie den Modellnamen."""
    out: set[str] = set()
    for muster in (_GETRIEBE_MUSTER, _ANTRIEB_MUSTER, _KRAFTSTOFF_MUSTER, _KAROSSERIE_MUSTER):
        for label, keys in muster:
            out |= tokens(label)
            for k in keys:
                out |= tokens(k)
    return frozenset(out | _EINHEITEN | _FUELLWOERTER)


_STOPWORTE = _stopwort_tokens()


def _ist_chassis_token(t: str) -> bool:
    return bool(re.fullmatch(r"[a-z]\d{1,3}|\d[a-z]\d?", t, re.I))


def _ist_zylinder_token(t: str) -> bool:
    """V8/V6/V10/V12/I4/I6 sind Zylinderangaben, keine Chassis-/Generationscodes —
    formal identisch zu einem Chassis-Code ([a-z]\\d{1,3}), aber inhaltlich etwas
    anderes. 'B'/'R' bleiben ausgenommen (echte Generationscodes wie 'B9'/'R8')."""
    return bool(re.fullmatch(r"[vi]\d{1,2}", t, re.I))


# Generische, markenübergreifende Ausstattungslinien-Suffixe ("AMG Line", "S Line",
# "M Sport", "R-Line", "Black Edition") — beschreiben eine Trimlinie, nicht das Modell
# oder eine Performance-Variante ("AMG Line" != "AMG"). Werden vor der Modell-/
# Generations-Erkennung aus dem Arbeitstext entfernt.
_RE_TRIMLINIEN = re.compile(
    r"\b(amg\s?line|s\s?line|r[\s-]?line|m\s?sport|black\s?edition|design\s?line|"
    r"business\s?line|urban\s?line|style\s?line)\b", re.I,
)


def _modell_und_generation(roh: str, make: str | None, codes: set[str],
                           marker_tokens: set[str]) -> tuple[str | None, str | None]:
    """Trennt Modellbezeichnung von Generations-/Karosseriecode.

    Beides sieht formal gleich aus ('C200' vs. 'W205', 'A4' vs. 'B9'). Regel: stehen
    MEHRERE code-artige Token im Text, ist das erste die Modellbezeichnung und das
    letzte der Generationscode; steht nur eines da, ist es der Generationscode.
    """
    arbeitstext = _RE_TRIMLINIEN.sub(" ", roh)
    kandidaten: list[str] = []
    gesehen_marke = make is None
    for t in re.split(r"[^A-Za-z0-9.]+", arbeitstext):
        tl = t.lower().strip(".")
        if not tl:
            continue
        if not gesehen_marke:
            gesehen_marke = tl == (make or "").lower()
            continue
        if tl in MARKEN or tl in marker_tokens or tl in _STOPWORTE:
            continue
        if re.fullmatch(r"[\d.,/]+", tl):      # reine Zahlen (Baujahr, km, Hubraum)
            continue
        if _ist_zylinder_token(tl):            # V8/V6/I4 — Motor, kein Chassis-Code
            continue
        kandidaten.append(t)

    code_kandidaten = [t for t in kandidaten if _ist_chassis_token(t)]
    wort_kandidaten = [t for t in kandidaten if not _ist_chassis_token(t)]

    if wort_kandidaten:
        model = wort_kandidaten[0]
        generation = code_kandidaten[-1] if code_kandidaten else None
    elif len(code_kandidaten) >= 2:
        model, generation = code_kandidaten[0], code_kandidaten[-1]
    elif code_kandidaten:
        model, generation = None, code_kandidaten[0]
    else:
        model, generation = None, (sorted(codes)[0] if codes else None)
    return model, generation


# Motorcode-artiges Token aus MOTORFREITEXT ("M264" aus "2.0 Turbo M264",
# "OM651", "B48B20"). Bewusst NUR case-sensitiv auf Grossschreibung: reale
# Motorcodes werden in Inseraten fast immer in Grossbuchstaben genannt; ohne
# diese Einschraenkung wuerden beliebige Kleinbuchstaben-Woerter (z.B. "km70")
# faelschlich als Code gelten. Buchstabe(n) UND Ziffer(n) gemeinsam gefordert,
# damit reine Woerter (TURBO, DIESEL) nicht anschlagen.
_RE_MOTORCODE_KANDIDAT = re.compile(r"\b([A-Z]{1,3}\d{2,4}[A-Z]?\d{0,2})\b")
_MOTORCODE_AUSSCHLUSS = frozenset({"TDI", "TSI", "TFSI", "CDI", "HDI", "DCI", "CRDI", "JTD"})


def motorcode_kandidat(text: str | None) -> str | None:
    """Motorcode-Kandidat aus Nutzer-Freitext, wenn die DB keinen liefert
    (Production-Run Mercedes C300 W205, §11): eine explizite Nutzereingabe wie
    "M264" darf nicht verschwinden, nur weil die DB dafür keinen Motorcode
    kennt. Bleibt der Kandidat unbestätigt (Herkunft "user", keine DB-Referenz)
    — er wird nirgends als geprüfte Tatsache behandelt."""
    for m in _RE_MOTORCODE_KANDIDAT.finditer(text or ""):
        kandidat = m.group(1)
        if kandidat.upper() in _MOTORCODE_AUSSCHLUSS:
            continue
        return kandidat
    return None


def _motorbezeichnung(text: str) -> str | None:
    """Motor-Verkaufsbezeichnung aus Freitext ('320d', '2.0 TDI', '4.0 V8').

    Bewusst konservativ: eine Zahl gefolgt von einer EINHEIT ('420 PS', '115000 km')
    ist keine Motorbezeichnung, ein Karosseriecode ('W205') ebenfalls nicht.
    """
    for m in re.finditer(r"\b(\d{3}\s?[a-z]{1,3})\b", text or "", re.I):
        kandidat = re.sub(r"\s+", "", m.group(1))
        suffix = re.sub(r"^\d+", "", kandidat).lower()
        if suffix and suffix not in _EINHEITEN:
            return kandidat
    m = re.search(r"\b(\d[.,]\d)\s*(tdi|tfsi|tsi|cdi|hdi|dci|crdi|jtd|dti|cdti)\b", text or "", re.I)
    if m:
        return f"{m.group(1).replace(',', '.')} {m.group(2).upper()}"
    hub = _RE_HUBRAUM.search(text or "")
    zyl = _RE_ZYLINDER.search(text or "")
    if hub and zyl:
        return f"{hub.group(1).replace(',', '.')} {zyl.group(1).upper().replace(' ', '')}"
    return None


def _variant_aus_text(text: str, model: str | None) -> str | None:
    """Modellvariante: bevorzugt der spezifische Performance-Marker im Originaltext
    ('M3', 'RS6', 'C63 AMG'), sonst eine erkannte Ausstattungslinie ('Grand Sport')."""
    t = text or ""
    for _familie, rx in _PERF_SPEZIFISCH:
        m = rx.search(t)
        if m:
            return m.group(0).strip()
    for _familie, rx in _PERF_FAMILIE:
        m = rx.search(t)
        if m:
            return m.group(0).strip()
    # Zweiwort-Linien wie "Grand Sport", "Sports Tourer", "Country Tourer".
    m = re.search(r"\b(grand\s+sport|sports?\s+tourer|country\s+tourer|shooting\s+brake)\b", t, re.I)
    if m:
        return m.group(1)
    return None
