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

    # ── Konstruktoren ─────────────────────────────────────────────────────────

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
            fuel=m.get("kraftstoff") or getattr(req, "kraftstoff", None) or _erste(_KRAFTSTOFF_MUSTER, freitext),
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
