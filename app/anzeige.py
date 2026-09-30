from __future__ import annotations

"""
Gemeinsamer Anzeige-Formatter für kanonische KaufCheck-Felder.

BEFUNDE (Production-Runs)
-------------------------
  * "Fahrzeug eindeutig zugeordnet: BMW M4 F82 M4 ..." — die Hauptüberschrift
    war dedupliziert, die Empfehlungsgründe setzten dieselben Felder erneut
    per String-Verkettung zusammen.
  * "2.0 TFSI, 190 PS, Frontantrieb, 190 PS" — die Vergleichstabelle hängte
    die Leistung an einen Motortext, der sie bereits enthielt.
  * "mazda" — die Anzeige nutzte den casefold-Vergleichsschlüssel.
  * "Datenbasis" oben "Inserat + Web", unten "Datenbank + Web".
  * Kostenhinweis "300" ohne Einheit und ohne "ca.".

ROOT CAUSE: jede Berichtsstelle baute ihren Text selbst aus Rohfeldern.
REGEL: Text, der aus kanonischen Feldern entsteht, entsteht HIER — mit
Token-Deduplizierung, Anzeigeformen und Herkunftstexten aus EINER Quelle.
"""

import re

_KRAFTSTOFF = {"benzin": "Benzin", "diesel": "Diesel", "elektro": "Elektro"}
_POWERTRAIN = {"ICE": "Verbrenner", "MHEV": "Mild-Hybrid", "HEV": "Hybrid",
               "PHEV": "Plug-in-Hybrid", "BEV": "Elektro"}
_GETRIEBE = {"automatik": "Automatik", "manuell": "Schaltgetriebe"}
_QUELLE = {"enfal": "ENFAL-Fahrzeugdaten", "web": "Webquellen", "user": "Inserat/Nutzereingabe"}


# ── Token-Deduplizierung ─────────────────────────────────────────────────────

def _token_key(t: str) -> str:
    return re.sub(r"[^\wäöüß]+", "", t.casefold())


def ohne_wiederholung(*segmente, trenner: str = " ") -> str:
    """Verbindet Segmente; jedes Wort-Token erscheint höchstens einmal
    (global über alle Segmente, auch innerhalb eines Segments). Leere Segmente
    entfallen, es entsteht kein doppelter Trenner."""
    gesehen: set[str] = set()
    teile: list[str] = []
    for seg in segmente:
        if seg in (None, ""):
            continue
        neu = []
        # Klammergruppen ("(40 TFSI)") sind EINE Einheit: sie entfallen nur,
        # wenn alle ihre Tokens schon gezeigt wurden — sonst bleiben sie
        # vollständig stehen (kein "(40" ohne schließende Klammer).
        for einheit in re.findall(r"\([^)]*\)|\S+", str(seg)):
            if einheit.startswith("("):
                keys = {_token_key(w) for w in einheit.strip("()").split()} - {""}
                if not keys or keys <= gesehen:
                    continue
                gesehen |= keys
                neu.append(einheit)
                continue
            key = _token_key(einheit)
            if not key or key in gesehen:
                continue
            gesehen.add(key)
            neu.append(einheit)
        if neu:
            teile.append(" ".join(neu).strip(" ,;·"))
    return trenner.join(t for t in teile if t)


def liste_ohne_wiederholung(*segmente, trenner: str = ", ") -> str:
    """Wie `ohne_wiederholung`, aber ein Segment entfällt nur, wenn ALLE seine
    Tokens schon gezeigt wurden ("2.0 TFSI, 190 PS" + "190 PS" -> einmal)."""
    gesehen: set[str] = set()
    out: list[str] = []
    for seg in segmente:
        if seg in (None, ""):
            continue
        for teil in re.split(r"\s*,\s*", str(seg)):
            keys = {_token_key(w) for w in teil.split() if _token_key(w)}
            if not keys or keys <= gesehen:
                continue
            gesehen |= keys
            out.append(teil.strip())
    return trenner.join(out)


# ── Feldwerte ────────────────────────────────────────────────────────────────

def wert(feld: str, value, fe: dict | None = None) -> str:
    """Anzeigeform eines kanonischen Felds, inklusive Mehrdeutigkeit."""
    fe = fe or {}
    state = fe.get("verification_state")
    moegliche = fe.get("possible_values") or []
    if state == "ambiguous" and moegliche:
        if feld == "engine_code":
            return "nicht eindeutig (mögliche Motorcodes: " + ", ".join(moegliche) + ")"
        if feld == "powertrain":
            return ("nicht sicher bestimmbar (möglich: "
                    + ", ".join(_POWERTRAIN.get(p, p) for p in moegliche) + ")")
        return "nicht eindeutig (möglich: " + ", ".join(str(x) for x in moegliche) + ")"
    if value in (None, ""):
        return "nicht sicher bekannt"
    if feld == "fuel":
        return _KRAFTSTOFF.get(str(value).lower(), str(value))
    if feld == "powertrain":
        return _POWERTRAIN.get(str(value), str(value))
    if feld == "transmission":
        basis = _GETRIEBE.get(str(value).lower(), str(value))
        detail = fe.get("detail")
        return f"{basis} ({detail})" if detail else basis
    if feld == "horsepower":
        return f"{value} PS"
    return str(value)


def herkunft(fe: dict | None) -> str:
    """Herkunftssatz eines Felds — die Nutzereingabe verliert nie ihre Herkunft."""
    fe = fe or {}
    state = fe.get("verification_state") or {
        "provided": "user_only", "identified": "reference_only", "web_verified": "web_only",
        "plausible": "plausible"}.get(fe.get("status"), "unknown")
    bestaetigt = [_QUELLE[c] for c in fe.get("confirmed_by") or [] if c in _QUELLE]
    raw = fe.get("raw_user_value")
    if state == "user_only":
        return "laut Inserat/Nutzereingabe, nicht unabhängig bestätigt"
    if state == "user_confirmed":
        return "laut Inserat/Nutzereingabe; bestätigt durch " + " und ".join(bestaetigt or ["ENFAL-Fahrzeugdaten"])
    if state == "user_refined":
        zusatz = f" („{raw}“)" if raw else ""
        weitere = [b for b in bestaetigt if b != "ENFAL-Fahrzeugdaten"]
        return (f"laut Inserat/Nutzereingabe{zusatz}; durch ENFAL-Fahrzeugdaten präzisiert"
                + (", bestätigt durch " + " und ".join(weitere) if weitere else ""))
    if state == "conflict":
        ref = fe.get("reference_value")
        return ("laut Inserat/Nutzereingabe; ENFAL-Fahrzeugdaten nennen abweichend "
                + (f"„{ref}“" if ref else "einen anderen Wert") + ": vor Ort klären")
    if state == "reference_only":
        extra = " und Webquellen" if "Webquellen" in bestaetigt else ""
        return f"aus ENFAL-Fahrzeugdaten{extra} (Referenz), am Fahrzeug nicht geprüft"
    if state == "plausible":
        return "aus ENFAL-Fahrzeugdaten plausibilisiert, nicht gesichert"
    if state == "web_only":
        belege = fe.get("evidence")
        return ("über Webquellen plausibilisiert" + (f" ({belege})" if belege else "")
                + ", am Fahrzeug nicht geprüft")
    if state == "ambiguous":
        if fe.get("web_conflict"):
            return "ENFAL-Daten mehrdeutig, Webquellen widersprechen: nur per FIN/Fahrzeugschein klärbar"
        return "ENFAL-Daten decken mehrere Varianten ab: per FIN/Fahrzeugschein klären"
    return "nicht sicher bekannt"


def feldzeile(label: str, feld: str, value, fe: dict | None) -> str:
    anzeige = wert(feld, value, fe)
    if anzeige.startswith("nicht ") or (fe or {}).get("verification_state") in (None, "unknown"):
        if (fe or {}).get("verification_state") in (None, "unknown"):
            return f"- {label}: nicht sicher bekannt."
        return f"- {label}: {anzeige} ({herkunft(fe)})."
    web_konflikt = (fe or {}).get("web_conflict")
    zusatz = f"; Webquellen nennen abweichend „{web_konflikt}“" if web_konflikt else ""
    return f"- {label}: {anzeige} ({herkunft(fe)}{zusatz})."


# ── Fahrzeugtitel ────────────────────────────────────────────────────────────

def fahrzeug_titel(identity, *, mit_jahr: bool = True, mit_motor: bool = True) -> str:
    """EINE deduplizierte Fahrzeugbezeichnung für jede Berichtsstelle."""
    if identity is None:
        return ""
    code = identity.engine_code if (identity.field_evidence.get("engine_code", {})
                                    .get("verification_state") not in ("ambiguous",)) else None
    ps = f"{identity.horsepower} PS" if (mit_motor and identity.horsepower) else None
    return ohne_wiederholung(
        identity.make, identity.model, identity.model_variant, identity.generation,
        str(identity.year) if (mit_jahr and identity.year) else None,
        identity.engine_name if mit_motor else None,
        code if mit_motor else None, ps)


# ── Kosten ───────────────────────────────────────────────────────────────────

# Tausenderform NUR mit mindestens einer Gruppe ("1.500"), sonst die volle
# Ziffernfolge ("1500") — sonst würde "1500-3000" als "150" gelesen.
_ZAHL = r"\d{1,3}(?:[.\s]\d{3})+(?:,\d+)?|\d+(?:,\d+)?"
_BETRAG = re.compile(rf"(?P<a>{_ZAHL})(?:\s*(?:-|–|bis)\s*(?P<b>{_ZAHL}))?\s*(?P<e>€|eur\b|euro\b)?",
                     re.IGNORECASE)
_FREMDWAEHRUNG = re.compile(r"\$|usd|£|gbp|chf|dollar|pfund", re.IGNORECASE)


def _zahl(text: str) -> int | None:
    t = text.replace(".", "").replace(" ", "").split(",")[0]
    return int(t) if t.isdigit() else None


def kosten_anzeige(roh: str | None) -> str | None:
    """Kostenangabe in EINER Form: "ca. 300 €" / "ca. 300–500 €".

    Fremdwährung, Prozent- oder Zeitangaben und unplausible Beträge ergeben
    "Kostenangabe nicht verifiziert" statt eines geratenen Euro-Betrags. Ohne
    Ziffer: None (kein Kostenhinweis)."""
    t = (roh or "").strip()
    if not t or not re.search(r"\d", t):
        return None
    if _FREMDWAEHRUNG.search(t) or re.search(r"%|km\b|monat|jahr", t, re.IGNORECASE):
        return "Kostenangabe nicht verifiziert"
    m = _BETRAG.search(t)
    if not m:
        return "Kostenangabe nicht verifiziert"
    a = _zahl(m.group("a"))
    b = _zahl(m.group("b")) if m.group("b") else None
    if a is None or a < 20 or a > 100_000 or (b is not None and (b < a or b > 200_000)):
        return "Kostenangabe nicht verifiziert"

    def eur(n: int) -> str:
        return f"{n:,}".replace(",", ".")
    return f"ca. {eur(a)}–{eur(b)} €" if b else f"ca. {eur(a)} €"
