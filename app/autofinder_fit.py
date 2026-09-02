from __future__ import annotations

"""
AutoFinder — nutzer-verständlicher Fit-Score (Quality-Enrichment-Runde).

WARUM EIN ZWEITER SCORE
-----------------------
`app.autofinder._score_kandidat` liefert einen ADDITIVEN Ranking-Score (0..~10),
der nur die Reihenfolge bestimmt. Bei wenigen Nutzerfiltern ist er fast überall
gleich klein — als Prozentwert im Consumer-UI angezeigt suggeriert er "VIRA hat
nichts Passendes gefunden", obwohl die harten Filter alle erfüllt sind.

Dieses Modul berechnet einen GETRENNTEN, nutzer-lesbaren Fit-Score (0..100).
Er misst NICHT die absolute Begehrlichkeit eines Autos, sondern **wie gut es zu
dem passt, was der Nutzer angefragt hat**:

  - harte Filter sind ohnehin erfüllt — aber manche "passen sauberer"
    (reiner Kombi vs. Multi-Body; Leistung mittig im Fenster vs. am Rand;
    Baujahr voll im Wunschfenster vs. nur knapp überlappend),
  - weiche Kriterien (Nutzung, Jahresfahrleistung, Prioritäten),
  - Qualitätssignale (Datenvollständigkeit, bekannte Schwachpunkte, Aktualität).

KEINE ERFUNDENEN WERTE / KEIN FAKE-MINDESTWERT
---------------------------------------------
Der Score ist eine reine, deterministische Funktion aus vorhandenen
Kandidatenfeldern und der Anfrage. Es gibt KEINE pauschale Anhebung auf einen
Mindestwert (§Test E): Kandidaten unter der Schwelle werden vom Router
weggelassen, nicht hochgerechnet. `praktisch/komfortabel/familie` werden aus der
KAROSSERIEKLASSE abgeleitet (Klassifikation, kein geratenes Proxy-Feld) und
bewusst konservativ gewichtet.
"""

from dataclasses import dataclass, field
from typing import Any

# Schwelle: nur Fahrzeuge mit diesem Fit oder besser werden ausgegeben (§Punkt 2).
FIT_SCHWELLE = 80

# Roh-Fit (0..1) -> Prozent. So gewählt, dass FIT_SCHWELLE (80) einem Roh-Fit
# von ~0.71 entspricht und ein perfekter Treffer bei ~98 landet — nie glatte
# 100 (keine Scheingenauigkeit). Ein Kandidat, der mehrere weiche Kriterien
# klar verfehlt, fällt real unter 80 und wird vom Router weggelassen.
_FIT_BASIS = 35
_FIT_SPANNE = 63


@dataclass
class FitKomponente:
    label: str
    gewicht: float
    erfuellung: float   # 0..1
    positiv: bool       # True -> taugt als "Warum passt es"-Kurzgrund


@dataclass
class FitBewertung:
    score: int                       # 0..100, gerundet
    komponenten: list[FitKomponente] = field(default_factory=list)
    gruende: list[str] = field(default_factory=list)   # kurze deterministische Fit-Labels

    @property
    def erreicht_schwelle(self) -> bool:
        return self.score >= FIT_SCHWELLE


# ── Karosserie-Heuristiken für praktisch/komfortabel/familie ────────────────
# Klassifikationsbasiert (die Karosserieklasse ist bekannt), kein geratenes
# Feld. Bewusst konservativ: eine Andeutung, keine harte Aussage.
_PRAKTISCH = {"kombi": 1.0, "van": 1.0, "suv": 0.9, "kompakt": 0.6, "pickup": 0.7,
              "limousine": 0.5, "kleinwagen": 0.4, "coupe": 0.15, "cabrio": 0.1}
_FAMILIE = {"van": 1.0, "kombi": 1.0, "suv": 0.95, "limousine": 0.6, "kompakt": 0.55,
            "kleinwagen": 0.35, "pickup": 0.5, "coupe": 0.15, "cabrio": 0.05}
_KOMFORTABEL = {"limousine": 0.9, "suv": 0.8, "kombi": 0.7, "van": 0.7, "coupe": 0.6,
                "kompakt": 0.45, "pickup": 0.4, "cabrio": 0.5, "kleinwagen": 0.3}


def _karo_heuristik(klassen: list[str], tabelle: dict[str, float]) -> float:
    if not klassen:
        return 0.4
    return max(tabelle.get(k, 0.4) for k in klassen)


def _nutzung_fit(k: Any, nutzung: str) -> float:
    kraftstoff = (k.kraftstoff or "")
    karo = set(k.karosserie_klassen or [])
    ps = k.leistung_ps
    if nutzung == "langstrecke":
        s = 0.35
        if kraftstoff in ("Diesel", "Plug-in-Hybrid"):
            s += 0.4
        elif kraftstoff in ("Mild-Hybrid", "Benzin"):
            s += 0.15
        if ps is not None and ps >= 120:
            s += 0.15
        if karo & {"limousine", "kombi", "suv"}:
            s += 0.1
        return min(1.0, s)
    if nutzung == "stadt":
        s = 0.3
        if karo & {"kleinwagen", "kompakt"}:
            s += 0.4
        if kraftstoff in ("Elektro", "Mild-Hybrid", "Benzin"):
            s += 0.2
        elif kraftstoff == "Plug-in-Hybrid":
            s += 0.1
        if ps is not None and ps <= 150:
            s += 0.1
        return min(1.0, s)
    # gemischt: fast alles passt ordentlich, leichte Abwertung für Extreme.
    s = 0.8
    if karo & {"coupe", "cabrio"}:
        s -= 0.15
    if ps is not None and ps >= 400:
        s -= 0.1
    return max(0.4, min(1.0, s))


def _km_fit(k: Any, km_pro_jahr: int) -> float:
    kraftstoff = (k.kraftstoff or "")
    if km_pro_jahr >= 25000:
        if kraftstoff in ("Diesel", "Plug-in-Hybrid"):
            return 1.0
        if kraftstoff in ("Mild-Hybrid",):
            return 0.75
        if kraftstoff == "Elektro":
            return 0.7  # hohe Laufleistung + Langstrecke: Reichweite/Ladestopps
        return 0.55     # Benzin bei sehr hoher Laufleistung
    if km_pro_jahr <= 8000:
        if kraftstoff in ("Benzin", "Elektro", "Mild-Hybrid"):
            return 1.0
        if kraftstoff == "Diesel":
            return 0.5   # Diesel + wenig km + Kurzstrecke: DPF/Regeneration
        return 0.8
    # mittlere Laufleistung: neutral gut
    return 0.85


def _prioritaet_fit(k: Any, prio: str) -> float | None:
    """Gibt None zurück, wenn die Priorität mangels Daten nicht bewertbar ist —
    dann fließt sie GAR NICHT in den Score ein (kein 0-Malus für Datenlücken)."""
    ps = k.leistung_ps
    verbrauch = getattr(k, "verbrauch_l_100km", None)
    b100 = getattr(k, "beschleunigung_0_100_s", None)
    karo = list(k.karosserie_klassen or [])
    if prio == "sportlich":
        if ps is None and b100 is None:
            return None
        s = 0.2
        if ps is not None and ps >= 250:
            s += 0.45
        elif ps is not None and ps >= 180:
            s += 0.2
        if b100 is not None and b100 <= 6.5:
            s += 0.35
        elif b100 is not None and b100 <= 8.0:
            s += 0.15
        return min(1.0, s)
    if prio == "sparsam":
        if verbrauch is None and k.kraftstoff != "Elektro":
            return None
        if k.kraftstoff == "Elektro":
            return 0.95
        if verbrauch <= 4.5:
            return 1.0
        if verbrauch <= 5.5:
            return 0.85
        if verbrauch <= 7.0:
            return 0.55
        return 0.25
    if prio == "fahranfaenger":
        if ps is None:
            return None
        s = 0.2
        if karo and set(karo) & {"kleinwagen", "kompakt"}:
            s += 0.35
        if ps <= 110:
            s += 0.45
        elif ps <= 150:
            s += 0.2
        elif ps >= 250:
            s -= 0.2
        return max(0.0, min(1.0, s))
    if prio == "praktisch":
        return _karo_heuristik(karo, _PRAKTISCH)
    if prio == "familie":
        return _karo_heuristik(karo, _FAMILIE)
    if prio == "komfortabel":
        return _karo_heuristik(karo, _KOMFORTABEL)
    return None


def _leistung_position(k: Any, lo: int | None, hi: int | None) -> float:
    """Wie 'mittig' liegt die Leistung im gewünschten Fenster? Rand -> 0.7,
    Mitte -> 1.0. Ohne Fenster nicht relevant (Aufrufer ruft dann nicht auf)."""
    ps = k.leistung_ps
    if ps is None:
        return 0.5
    lo_e = lo if lo is not None else max(0, ps - 60)
    hi_e = hi if hi is not None else ps + 60
    if hi_e <= lo_e:
        return 1.0
    rel = (ps - lo_e) / (hi_e - lo_e)          # 0..1 Position im Fenster
    return 1.0 - 0.3 * abs(rel - 0.5) * 2      # Mitte 1.0, Rand 0.7


def _baujahr_ueberlappung(k: Any, req_von: int | None, req_bis: int | None) -> float:
    cv, cb = k.baujahr_von, k.baujahr_bis
    if cv is None:
        return 0.6
    cb = cb if cb is not None else 2100
    rv = req_von if req_von is not None else cv
    rb = req_bis if req_bis is not None else cb
    overlap = max(0, min(cb, rb) - max(cv, rv))
    cand_span = max(1, cb - cv)
    return max(0.4, min(1.0, 0.55 + 0.45 * (overlap / cand_span)))


def _karosserie_sauberkeit(k: Any, gewuenscht: list[str]) -> float:
    """Reiner Treffer der gewünschten Klasse -> 1.0; Multi-Body-Kandidat, der
    die Klasse nur unter mehreren trägt -> leicht abgewertet (der visuelle/
    fachliche Bezug ist dann weniger eindeutig)."""
    klassen = list(k.karosserie_klassen or [])
    if not gewuenscht or not klassen:
        return 0.85
    treffer = set(kk.lower() for kk in gewuenscht) & set(klassen)
    if not treffer:
        return 0.5   # sollte wg. Hard-Filter nicht vorkommen
    if len(klassen) == 1:
        return 1.0
    if len(klassen) == 2:
        return 0.9
    return 0.8


def _tradeoff_sauberkeit(k: Any) -> tuple[float, list[str]]:
    """0..1 — 1.0 = keine bekannten hoch eingestuften Schwachpunkte / Rückrufe.
    Nutzt die bereits vom Router/Engine gelesenen `trade_offs`-Strings; NUR als
    verified markierte bzw. verifizierte Rückrufe senken den Wert (unverified
    Schwächen dürfen den Fit nicht drücken — §Punkt 6)."""
    tos = list(getattr(k, "trade_offs", []) or [])
    schwer = [t for t in tos if "(geprüft)" in t]
    rueckrufe = [t for t in tos if "KBA-Rückruf" in t]
    s = 1.0
    s -= 0.12 * len(schwer)
    s -= 0.05 * len(rueckrufe)
    return max(0.45, s), schwer + rueckrufe


def berechne_fit(k: Any, req: Any) -> FitBewertung:
    """Deterministischer Fit-Score. `k` = app.autofinder.AutoFinderKandidat
    (oder .autofinder_web.WebKandidat mit denselben Feldern), `req` =
    app.autofinder.AutoFinderRequest."""
    komp: list[FitKomponente] = []

    # --- Was der Nutzer explizit angefragt hat ---
    # Diese Kriterien sind durch die HARTEN FILTER ohnehin erfüllt ("table
    # stakes") — deshalb bewusst niedriger gewichtet als die weichen
    # Kriterien (Nutzung, Prioritäten), die die eigentliche Passung
    # ausmachen. Nur die feinen Abstufungen (Multi-Body, Leistungs-Position)
    # tragen hier noch Differenzierung.
    if getattr(req, "karosserie", None):
        e = _karosserie_sauberkeit(k, req.karosserie)
        komp.append(FitKomponente("Gewünschte Karosserie", 0.9, e, e >= 0.9))
    if getattr(req, "kraftstoff", None):
        komp.append(FitKomponente("Gewünschter Kraftstoff", 0.6, 1.0, True))
    if getattr(req, "getriebe", None):
        komp.append(FitKomponente("Gewünschtes Getriebe", 0.5, 1.0, True))
    if getattr(req, "antrieb", None):
        komp.append(FitKomponente("Gewünschter Antrieb", 0.5, 1.0, True))
    if getattr(req, "leistung_min_ps", None) is not None or getattr(req, "leistung_max_ps", None) is not None:
        e = _leistung_position(k, req.leistung_min_ps, req.leistung_max_ps)
        komp.append(FitKomponente("Leistung im Wunschbereich", 1.0, e, e >= 0.8))
    if getattr(req, "baujahr_von", None) is not None or getattr(req, "baujahr_bis", None) is not None:
        e = _baujahr_ueberlappung(k, req.baujahr_von, req.baujahr_bis)
        komp.append(FitKomponente("Baujahr im Wunschfenster", 0.7, e, e >= 0.85))

    # --- Nutzung / Jahresfahrleistung — die Hauptdifferenzierer ---
    if getattr(req, "nutzung", None):
        e = _nutzung_fit(k, req.nutzung)
        komp.append(FitKomponente(f"Nutzung: {req.nutzung}", 1.9, e, e >= 0.75))
    if getattr(req, "km_pro_jahr", None):
        e = _km_fit(k, req.km_pro_jahr)
        komp.append(FitKomponente("Jahresfahrleistung", 1.1, e, e >= 0.8))

    # --- Prioritäten (nicht bewertbare fließen NICHT ein) — Hauptdifferenzierer ---
    _PRIO_LABEL = {
        "sportlich": "Sportlich", "sparsam": "Sparsam", "fahranfaenger": "Fahranfänger-tauglich",
        "praktisch": "Praktisch/Alltag", "familie": "Familientauglich", "komfortabel": "Komfortabel",
    }
    for prio, label in _PRIO_LABEL.items():
        if getattr(req, prio, False):
            e = _prioritaet_fit(k, prio)
            if e is None:
                continue
            komp.append(FitKomponente(label, 1.5, e, e >= 0.7))

    # Zahl der KRITERIEN, die der Nutzer wirklich vorgegeben hat (die zwei
    # Qualitätskomponenten unten zählen NICHT dazu).
    nutzer_kriterien = len(komp)

    # --- immer: Datenlage + Schwachpunkt-Sauberkeit ---
    dq = float(getattr(k, "datenqualitaet", 0.0) or 0.0)
    komp.append(FitKomponente("Datenlage vollständig", 0.6, dq, dq >= 0.85))
    to_sauber, _ = _tradeoff_sauberkeit(k)
    komp.append(FitKomponente("Wenige bekannte Schwachpunkte", 1.0, to_sauber, to_sauber >= 0.95))

    gesamt_gewicht = sum(c.gewicht for c in komp)
    if gesamt_gewicht <= 0:
        return FitBewertung(score=_FIT_BASIS, komponenten=[], gruende=[])
    fit_raw = sum(c.gewicht * c.erfuellung for c in komp) / gesamt_gewicht
    score = round(_FIT_BASIS + fit_raw * _FIT_SPANNE)
    score = max(0, min(99, score))

    # §Punkt 2: bei sehr wenig Nutzereingaben ist "Passung" nur begrenzt
    # aussagekräftig — dann NICHT künstlich Richtung 98 laufen lassen (das wäre
    # genau die irreführende Gleichmacherei, nur oben statt unten). Kein
    # Fake-Sockel: der Score wird nur GEDECKELT, nie angehoben.
    if nutzer_kriterien == 0:
        score = min(score, 85)
    elif nutzer_kriterien == 1:
        score = min(score, 90)
    elif nutzer_kriterien == 2:
        score = min(score, 94)

    gruende = [c.label for c in sorted(komp, key=lambda c: -c.gewicht * c.erfuellung)
               if c.positiv][:4]

    return FitBewertung(score=score, komponenten=komp, gruende=gruende)
