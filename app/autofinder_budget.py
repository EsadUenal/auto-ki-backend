from __future__ import annotations

"""
AutoFinder — Budget-Plausibilität via Gemini (Runde 3).

WAS DAS IST — UND WAS NICHT
----------------------------
Gemini ist hier AUSSCHLIESSLICH ein Plausibilitäts-Urteil über eine bereits
abgeschlossene Kandidatenliste. Gemini ist:

  KEIN Fahrzeugfinder      — die Kandidaten stehen VOR diesem Modul bereits
                             vollständig fest (harte Filter + Foundation-Score
                             + Dedupe + Diversität, siehe app/autofinder.py).
  KEINE technische Quelle  — Motor, Leistung, Kraftstoff, Getriebe etc. kommen
                             ausschließlich aus der DB und werden von Gemini
                             nur zurückgelesen, nie neu bewertet oder verändert.
  KEINE Marktpreis-Quelle  — es gibt kein Preisfeld im Schema, keine
                             Preisspanne, kein Median. Nur eine grobe
                             Kategorie (IN_BUDGET/NEAR_BUDGET/OUT_OF_BUDGET/
                             UNKNOWN) + wie sicher sich Gemini dabei ist.

WAS GEMINI TUN DARF
--------------------
Für jede übergebene candidate_id GENAU EINEN budget_status +
GENAU EINE confidence zurückgeben. Sonst nichts. Die Antwort wird strikt
gegen die tatsächlich übergebenen IDs validiert (`_validiere_antwort`):
unbekannte IDs, Duplikate und ungültige Enum-Werte werden VERWORFEN, nie
repariert oder geraten. Fehlt eine ID komplett in der Antwort, bleibt sie
beim Aufrufer UNKNOWN — das ist der Normalfall, kein Fehlerzustand.

AUSFALLSICHERHEIT (§8 der Produktspezifikation)
-------------------------------------------------
AutoFinder ist ein kostenloses Traffic-Feature — es darf NIE an einem
Gemini-Ausfall scheitern. `bewerte_budget()` fängt jeden Fehler (Provider-
Fehler über `app.gemini_retry.GeminiFehlgeschlagen`, kaputtes JSON, leere
Antwort, jede sonstige Exception) und gibt eine LEERE Zuordnung zurück — der
Aufrufer behandelt das identisch zu "Gemini hat zu keinem Kandidaten etwas
gesagt": alle bleiben UNKNOWN, das Foundation-Ranking bleibt unverändert
nutzbar, kein 500, kein Credit-Verlust.

KOSTEN
------
GENAU EIN Gemini-Call pro Suche, NIE einer pro Kandidat — die komplette
Shortlist (bis zu 15 Kandidaten, siehe app/routers/autofinder.py) geht in
EINEM Prompt. Kein Tavily, kein Search Grounding, kein neuer Gemini-Client —
`app.car_lookup.call_gemini_json` (bestehende Infrastruktur: `gemini-3.7-
flash`, `thinking_budget=0`, JSON-Modus, bestehendes Retry/Backoff) wird
unverändert wiederverwendet.
"""

import logging
from typing import Any

from app.autofinder_variante import baujahr_kontext, wunschfenster_text
from app.car_lookup import call_gemini_json
from app.gemini_retry import GeminiFehlgeschlagen

log = logging.getLogger(__name__)

# ── Budget-Status ────────────────────────────────────────────────────────────
IN_BUDGET = "IN_BUDGET"
NEAR_BUDGET = "NEAR_BUDGET"
OUT_OF_BUDGET = "OUT_OF_BUDGET"
BUDGET_UNKNOWN = "UNKNOWN"
# AF-14: nur ABGELEITET (Preisspanne ganz unter budget_min), nie ein gueltiger Gemini-Wert: bewusst nicht in
# BUDGET_STATUS_WERTE, damit die Validierung der Modellantwort unveraendert bleibt.
BELOW_BUDGET = "BELOW_BUDGET"
BUDGET_STATUS_WERTE = (IN_BUDGET, NEAR_BUDGET, OUT_OF_BUDGET, BUDGET_UNKNOWN)

# ── Confidence ───────────────────────────────────────────────────────────────
CONF_HIGH = "HIGH"
CONF_MEDIUM = "MEDIUM"
CONF_LOW = "LOW"
CONF_UNKNOWN = "UNKNOWN"
CONFIDENCE_WERTE = (CONF_HIGH, CONF_MEDIUM, CONF_LOW, CONF_UNKNOWN)

# ── Score-Wirkung — bewusst STRENG BEGRENZT (§6) ────────────────────────────
# Die additive Foundation-Score-Spannweite realer Suchen liegt typischerweise
# bei 0-10 Punkten (siehe app/autofinder.py::_score_kandidat — einzelne
# Kriterien vergeben 1-3 Punkte). Der Budget-Ausschlag bleibt bewusst UNTER
# dem, was ein einzelnes starkes Foundation-Kriterium bewirken kann: Budget
# darf zwischen technisch gleichwertigen Kandidaten den Ausschlag geben, aber
# NIE einen technisch klar schlechter passenden Kandidaten an einem klar
# besseren vorbeiziehen.
BUDGET_BONUS_IN = 1.5
BUDGET_BONUS_NEAR = 0.5
BUDGET_MALUS_OUT = -1.5
BUDGET_ADJUSTMENT: dict[str, float] = {
    IN_BUDGET: BUDGET_BONUS_IN,
    NEAR_BUDGET: BUDGET_BONUS_NEAR,
    OUT_OF_BUDGET: BUDGET_MALUS_OUT,
    BUDGET_UNKNOWN: 0.0,
    BELOW_BUDGET: 0.0,   # keine Ueberschreitung -> kein Malus
}


def _kandidat_id(k: Any) -> str:
    """Kanonische Kandidaten-ID quer ueber beide Herkuenfte.

    Interne DB-Kandidaten (`app.autofinder.AutoFinderKandidat`) tragen
    `variante_id`. Web-Kandidaten (Runde 4, `app.autofinder_web.WebKandidat`)
    tragen stattdessen eine eigene stabile `candidate_id` und bewusst KEINE
    erfundene DB-ID — deshalb hier eine Regel statt zweier Sonderfaelle.
    Bewusst per `getattr` und ohne Import aus `app.autofinder_web`: die
    Budget-Schicht muss nichts ueber Web-Discovery wissen.
    """
    return getattr(k, "candidate_id", None) or getattr(k, "variante_id", None) or ""


def budget_angegeben(budget_min: int | None, budget_max: int | None) -> bool:
    """§7: Gemini wird NUR aufgerufen, wenn der Nutzer tatsächlich ein
    Budgetfenster angegeben hat — mindestens eine der beiden Grenzen."""
    return budget_min is not None or budget_max is not None


_SYSTEM_PROMPT = """Du bewertest AUSSCHLIESSLICH, ob bereits feststehende, dir vorgegebene Fahrzeugkandidaten grob zum angegebenen Gebrauchtwagen-Budget passen könnten.

Du bist KEIN Fahrzeugfinder und KEINE Marktpreisquelle:
- Du darfst NIEMALS einen konkreten Preis, eine Preisspanne oder einen Marktwert nennen.
- Du darfst NIEMALS Kandidaten hinzufügen, entfernen, umbenennen oder ihre technischen Daten verändern.
- Du antwortest NUR zu den dir gegebenen candidate_id-Werten, unverändert übernommen aus der Eingabe.

Für jeden Kandidaten schätzt du aus allgemeinem Wissen über gebrauchte Fahrzeuge dieser Marke/Modell/Generation/Motorisierung/Baujahr grob ein, ob ein typisches Gebrauchtwagenangebot dafür eher:
- IN_BUDGET liegt (üblicherweise im angegebenen Budgetfenster),
- NEAR_BUDGET liegt (üblicherweise knapp außerhalb, aber nah dran),
- OUT_OF_BUDGET liegt (üblicherweise klar außerhalb),
- oder UNKNOWN (du hast keine ausreichend sichere Einschätzung — nutze das lieber als zu raten).

Gib zusätzlich eine confidence (HIGH/MEDIUM/LOW/UNKNOWN) an, wie sicher du dir bei dieser groben Einschätzung bist.

Antworte AUSSCHLIESSLICH mit folgendem JSON-Format, ohne Erklärtext, ohne Markdown, ohne Preisfeld:
{"candidates": [{"candidate_id": "<genau wie in der Eingabe>", "budget_status": "IN_BUDGET|NEAR_BUDGET|OUT_OF_BUDGET|UNKNOWN", "confidence": "HIGH|MEDIUM|LOW|UNKNOWN"}]}

Ein Eintrag pro candidate_id aus der Eingabe. Keine zusätzlichen Felder, keine zusätzlichen Kandidaten."""


def _formatiere_kandidat_zeile(k: Any) -> str:
    """Beschreibt die KONKRETE empfohlene Variante, nicht die Sammelangaben
    der Baureihe: eine Preiseinschätzung für "Kompakt/Kombi, Automatik oder
    Schaltgetriebe, 2012–2020" wäre eine Einschätzung für kein Fahrzeug."""
    zeitraum = baujahr_kontext(k)
    karo_konkret = getattr(k, "karosserie_konkret", None)
    getr_konkret = getattr(k, "getriebe_konkret", None)
    karosserie = karo_konkret or (
        "/".join(k.karosserie_klassen) if k.karosserie_klassen else "unbekannt")
    getriebe = getr_konkret or (
        "/".join(k.getriebe_klassen) if k.getriebe_klassen else "unbekannt")
    # F3 (GAS-1): ein Gasfahrzeug wird mit seinem Kraftstoff-Hinweis beschrieben ("primär Erdgas (CNG),
    # Benzin nur als Reserve" bzw. "Gasfahrzeug …"), nicht mit der Datensatzklasse "Benzin" — dieselbe
    # Darstellung wie im Enrichment-Prompt (app/autofinder_enrich._kraftstoff_text).
    kraftstoff = getattr(k, "kraftstoff_hinweis", None) or k.kraftstoff
    return (
        f"- candidate_id={_kandidat_id(k)} | {k.marke} {k.modell} {k.generation or ''} "
        f"{k.motor_bezeichnung} | {zeitraum} | {kraftstoff} | "
        f"Getriebe {getriebe} | {k.leistung_ps or '?'} PS | Karosserie {karosserie}"
    )


def _baue_user_message(kandidaten: list[Any], *, budget_min: int | None,
                        budget_max: int | None, baujahr_von: int | None,
                        baujahr_bis: int | None) -> str:
    """Baut den kompakten Prompt-Kontext (§3): NUR die in §3 gelisteten Felder,
    kein DB-Kontext, keine Schwachstellen, keine Rückrufe, kein Webinhalt."""
    zeilen = ["Budget des Nutzers:"]
    if budget_min is not None and budget_max is not None:
        zeilen.append(f"  {budget_min}–{budget_max} EUR")
    elif budget_max is not None:
        zeilen.append(f"  bis {budget_max} EUR")
    else:
        zeilen.append(f"  ab {budget_min} EUR")
    fenster = wunschfenster_text(baujahr_von, baujahr_bis)
    if fenster:
        zeilen.append(f"  {fenster}")
    zeilen.append("")
    zeilen.append("Kandidaten (NUR diese candidate_id-Werte sind gültig):")
    zeilen.extend(_formatiere_kandidat_zeile(k) for k in kandidaten)
    return "\n".join(zeilen)


def _validiere_antwort(roh: Any, erlaubte_ids: set[str]) -> dict[str, tuple[str, str]]:
    """§4/§10/§11: strikte Validierung gegen die tatsächlich übergebenen IDs.

    Gibt {candidate_id: (budget_status, confidence)} zurück — NUR für Einträge,
    die (a) eine tatsächlich angefragte ID tragen, (b) noch nicht vorher
    gesehen wurden (erster Treffer gewinnt, kein Überschreiben durch ein
    Duplikat) und (c) gültige Enum-Werte für BEIDE Felder tragen. Jede
    Abweichung führt zum Verwerfen DIESES Eintrags, nie zum Rest-Abbruch und
    nie zu einem geratenen Ersatzwert."""
    ergebnis: dict[str, tuple[str, str]] = {}
    if not isinstance(roh, dict):
        return ergebnis
    eintraege = roh.get("candidates")
    if not isinstance(eintraege, list):
        return ergebnis
    for eintrag in eintraege:
        if not isinstance(eintrag, dict):
            continue
        cid = eintrag.get("candidate_id")
        status = eintrag.get("budget_status")
        conf = eintrag.get("confidence")
        if not isinstance(cid, str) or cid not in erlaubte_ids:
            continue
        if cid in ergebnis:
            continue
        if status not in BUDGET_STATUS_WERTE or conf not in CONFIDENCE_WERTE:
            continue
        ergebnis[cid] = (status, conf)
    return ergebnis


async def bewerte_budget(
    kandidaten: list[Any],
    *,
    budget_min: int | None,
    budget_max: int | None,
    baujahr_von: int | None = None,
    baujahr_bis: int | None = None,
) -> tuple[dict[str, tuple[str, str]], bool]:
    """Führt GENAU EINEN Gemini-Call für die komplette übergebene Shortlist aus.

    Rückgabe `(zuordnung, gemini_ausgefallen)`. `zuordnung` enthält nur
    validierte Einträge; eine fehlende ID ist vom Aufrufer als UNKNOWN zu
    behandeln (kein Eintrag ist der Normalfall, kein Fehlerzustand — siehe
    §11 Test C/E). `gemini_ausgefallen=True` heißt: der komplette Call ist
    fehlgeschlagen, `zuordnung` ist dann leer und der Aufrufer sollte einen
    neutralen Warnhinweis anzeigen (§8)."""
    if not kandidaten:
        return {}, False

    erlaubte_ids = {_kandidat_id(k) for k in kandidaten}
    user_msg = _baue_user_message(
        kandidaten,
        budget_min=budget_min, budget_max=budget_max,
        baujahr_von=baujahr_von, baujahr_bis=baujahr_bis,
    )

    try:
        roh = await call_gemini_json(_SYSTEM_PROMPT, user_msg)
    except GeminiFehlgeschlagen as exc:
        log.warning("AutoFinder-Budget: Gemini-Aufruf fehlgeschlagen, Fallback UNKNOWN für alle: %s", exc)
        return {}, True
    except Exception:
        log.exception("AutoFinder-Budget: unerwarteter Fehler beim Gemini-Aufruf, Fallback UNKNOWN für alle")
        return {}, True

    return _validiere_antwort(roh, erlaubte_ids), False


def budget_adjustment_fuer(status: str) -> float:
    """Begrenzte Score-Wirkung (§6) für einen validierten/gefehlten Status.
    Unbekannte/fehlende Werte fallen auf UNKNOWN -> 0.0 (neutral, §7 Test 7)."""
    return BUDGET_ADJUSTMENT.get(status, 0.0)


# ══════════════════════════════════════════════════════════════════════════
# KONSISTENZ MIT DER PREISORIENTIERUNG (Consumer-Release-Härtung)
# ══════════════════════════════════════════════════════════════════════════
#
# PROBLEM (realer Browser-Befund): `budget_status` und die angezeigte
# Preisorientierung stammen aus ZWEI unabhängigen Gemini-Calls —
# `bewerte_budget()` (grobe Kategorie, keine Zahlen) und
# `app.autofinder_enrich` (estimated_price_min/max). Beide wurden bisher
# unverändert nebeneinander ausgegeben. Ergebnis: eine Karte konnte
# gleichzeitig "Im Budget" behaupten UND "ca. 27.000–39.000 €" anzeigen,
# obwohl der Nutzer maximal 25.000 € angegeben hatte.
#
# LÖSUNG: Sobald eine Preisspanne vorliegt, ist SIE die belastbarere Aussage
# — sie ist die Zahl, die der Nutzer tatsächlich sieht. Der Consumer-Status
# wird dann deterministisch aus Spanne + Budgetfenster abgeleitet, statt der
# Kategorie aus dem separaten Call zu vertrauen.
#
# KEIN FAKE: die Spanne bleibt eine KI-Schätzung ohne Live-Marktdaten. Es
# wird nichts hinzuerfunden — fehlt die Spanne, bleibt der ursprüngliche
# Status unverändert stehen.

# Wie weit darf die Spanne über dem Budget liegen und trotzdem noch
# "nahe am Budget" heißen? 15 % ist bewusst konservativ: darüber hinaus
# wird ehrlich "Über Budget" gesagt statt schönzureden.
NEAR_BUDGET_TOLERANZ = 0.15

_CONF_RANG = {CONF_HIGH: 3, CONF_MEDIUM: 2, CONF_LOW: 1, CONF_UNKNOWN: 0}


# AF-14: Lage der Preisspanne zum Budgetfenster. Fuenf Faelle, bewusst getrennt:
LAGE_UNTERHALB = "unterhalb"            # ganze Spanne unter der Budgetuntergrenze
LAGE_UNTEN = "ueberlappt_unten"         # Spanne beginnt unter der Untergrenze, endet im Budget
LAGE_INNERHALB = "innerhalb"            # ganze Spanne im Budgetfenster
LAGE_OBEN = "ueberlappt_oben"           # Spanne reicht ueber die Obergrenze, beginnt aber im Budget
LAGE_OBERHALB = "oberhalb"              # ganze Spanne ueber der Obergrenze


def budget_lage(preis_min: int, preis_max: int, budget_min: int | None, budget_max: int) -> str:
    """Ordnet die Preisspanne dem Budgetfenster zu. Spannt sie das ganze Fenster auf (beginnt darunter,
    endet darueber), zaehlt sie als "ueberlappt_oben": ein Teil liegt ueber der Obergrenze."""
    if preis_min > budget_max:
        return LAGE_OBERHALB
    if preis_max > budget_max:
        return LAGE_OBEN
    untergrenze = budget_min if budget_min is not None else 0
    if preis_max < untergrenze:
        return LAGE_UNTERHALB
    if preis_min < untergrenze:
        return LAGE_UNTEN
    return LAGE_INNERHALB


def konsolidiere_budget_status(
    budget_status: str,
    budget_confidence: str,
    *,
    preis_min: int | None,
    preis_max: int | None,
    preis_confidence: str = CONF_UNKNOWN,
    budget_min: int | None,
    budget_max: int | None,
) -> tuple[str, str]:
    """Leitet den WIDERSPRUCHSFREIEN Consumer-Budgetstatus ab.

    Regeln (deterministisch, konservativ):
      - kein Budgetfenster mit Obergrenze  -> Status unveraendert
      - keine Preisspanne vorhanden        -> Status unveraendert
        (kein erfundener Status aus einer Spanne, die es nicht gibt)
      - Spanne innerhalb des Fensters oder NACH UNTEN ueberlappend (beginnt unter der Untergrenze,
        endet im Budget)                   -> IN_BUDGET: es liegt keine Ueberschreitung vor. Die
        Untergrenze bleibt ein Nutzerwunsch (sie geht weiter in beide Prompts und `budget_lage`),
        ein Teil der Spanne unter ihr macht daraus keine "Naehe"- oder Ueberschreitungsaussage.
      - Spanne GANZ unter der Untergrenze -> BELOW_BUDGET ("Unter deinem Budgetrahmen"): nicht
        "Im Budget", keine Ueberschreitung, kein Malus. Beruehrt die Spanne die Untergrenze
        (preis_max == budget_min) oder fehlt budget_min, gilt sie als innerhalb.
      - Spanne reicht ueber die Obergrenze, beginnt im Budget (`ueberlappt_oben`) -> NEAR_BUDGET
      - Spanne startet knapp darueber      -> NEAR_BUDGET
      - Spanne startet deutlich darueber   -> OUT_OF_BUDGET

    Die Confidence des abgeleiteten Status ist hoechstens so hoch wie die der
    Preisspanne, auf der er beruht — eine LOW-Schaetzung darf keinen
    HIGH-sicheren Budgetstatus erzeugen.
    """
    if budget_max is None:
        return budget_status, budget_confidence
    if preis_min is None or preis_max is None:
        return budget_status, budget_confidence

    lage = budget_lage(preis_min, preis_max, budget_min, budget_max)
    if lage in (LAGE_INNERHALB, LAGE_UNTEN):
        neuer = IN_BUDGET
    elif lage == LAGE_UNTERHALB:
        # Ganz unter dem Mindestbudget: nicht "Im Budget" (die Abweichung vom Wunschfenster bleibt
        # sichtbar) und keine Ueberschreitung -> eigener Zustand ohne Malus.
        neuer = BELOW_BUDGET
    elif lage == LAGE_OBEN:
        # Die guenstigsten Angebote liegen im Budget, das typische Angebot aber nicht mehr sicher.
        neuer = NEAR_BUDGET
    elif preis_min <= budget_max * (1 + NEAR_BUDGET_TOLERANZ):
        neuer = NEAR_BUDGET
    else:
        neuer = OUT_OF_BUDGET

    if neuer == budget_status:
        return budget_status, budget_confidence

    conf = preis_confidence if preis_confidence in CONFIDENCE_WERTE else CONF_UNKNOWN
    if _CONF_RANG.get(budget_confidence, 0) and _CONF_RANG.get(conf, 0):
        conf = budget_confidence if _CONF_RANG[budget_confidence] < _CONF_RANG[conf] else conf
    return neuer, conf
