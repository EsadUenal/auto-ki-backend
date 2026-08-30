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

from app.car_lookup import call_gemini_json
from app.gemini_retry import GeminiFehlgeschlagen

log = logging.getLogger(__name__)

# ── Budget-Status ────────────────────────────────────────────────────────────
IN_BUDGET = "IN_BUDGET"
NEAR_BUDGET = "NEAR_BUDGET"
OUT_OF_BUDGET = "OUT_OF_BUDGET"
BUDGET_UNKNOWN = "UNKNOWN"
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
    zeitraum = f"{k.baujahr_von or '?'}–{k.baujahr_bis or 'heute'}"
    karosserie = "/".join(k.karosserie_klassen) if k.karosserie_klassen else "unbekannt"
    getriebe = "/".join(k.getriebe_klassen) if k.getriebe_klassen else "unbekannt"
    return (
        f"- candidate_id={_kandidat_id(k)} | {k.marke} {k.modell} {k.generation or ''} "
        f"{k.motor_bezeichnung} | Baujahre {zeitraum} | {k.kraftstoff} | "
        f"Getriebe {getriebe} | {k.leistung_ps or '?'} PS | Karosserie {karosserie}"
    )


def _baue_user_message(kandidaten: list[Any], *, budget_min: int | None,
                        budget_max: int | None, baujahr_von: int | None,
                        baujahr_bis: int | None, kilometer_max: int | None) -> str:
    """Baut den kompakten Prompt-Kontext (§3): NUR die in §3 gelisteten Felder,
    kein DB-Kontext, keine Schwachstellen, keine Rückrufe, kein Webinhalt."""
    zeilen = ["Budget des Nutzers:"]
    if budget_min is not None and budget_max is not None:
        zeilen.append(f"  {budget_min}–{budget_max} EUR")
    elif budget_max is not None:
        zeilen.append(f"  bis {budget_max} EUR")
    else:
        zeilen.append(f"  ab {budget_min} EUR")
    if baujahr_von is not None or baujahr_bis is not None:
        zeilen.append(f"  gewünschtes Baujahrfenster: {baujahr_von or '?'}–{baujahr_bis or '?'}")
    if kilometer_max is not None:
        zeilen.append(f"  maximaler Kilometerstand: {kilometer_max}")
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
    kilometer_max: int | None = None,
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
        kilometer_max=kilometer_max,
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
