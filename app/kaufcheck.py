from __future__ import annotations

"""
Kauf-Check: Inserat-Analyse mit DB-Abgleich und Marktpreisbewertung.

Ablauf:
  1. Baureihe + Motorvariante in SQLite erkennen
  2. DB-Kontext aufbauen (Schwachstellen, Rückrufe, Motorspecs)
  3. Marktpreis per Tavily ermitteln
  4. Gemini (JSON-Modus) liefert strukturierten Bericht
"""

import logging

from app.car_lookup import find_baureihe, find_motor, build_db_context, call_gemini_json
from app.config import TAVILY_API_KEY
from app.gemini_retry import RateLimitExhausted
from app.models import KaufCheckRequest
from app.web_search import tavily_search, results_to_context, results_to_belege

log = logging.getLogger(__name__)

_SYSTEM = """\
Du bist ein erfahrener KFZ-Kaufberater. Du analysierst ein Fahrzeug-Inserat und gibst eine sachliche, konkrete Kaufentscheidung zurück.

Du erhältst:
1. INSERAT-DATEN — Angaben aus dem Inserat
2. DB-PROFIL — geprüfte Fakten (Schwachstellen, Rückrufe, Specs) — zuverlässig
3. WEB-ERGEBNISSE — aktuelle Marktpreise aus Tavily — Orientierung, ungeprüft

AUSGABE: Ausschließlich gültiges JSON, kein Text davor oder danach.

{
  "bericht": "<Markdown-Bericht, Details unten>",
  "empfehlung": "kaufen" | "verhandeln" | "finger_weg" | "unbekannt",
  "preis_bewertung": "guter_deal" | "fair" | "zu_teuer" | "unbekannt",
  "marktpreis_min": <integer EUR oder null>,
  "marktpreis_max": <integer EUR oder null>
}

BERICHT-STRUKTUR (Markdown im "bericht"-Feld):

## Fahrzeug erkannt
Kurzzeile: Was wurde identifiziert (Baureihe, Motor, Baujahr).

## (a) Inserat im Vergleich
Tabelle mit mindestens 6 Zeilen:
| Kriterium | Inserat-Angabe | DB-/Markterwartung | Plausibilität |
Plausibilität: ✓ Plausibel / ⚠ Prüfen / ❌ Unplausibel
Mindest-Kriterien: Baujahr, Kilometerstand, Motor/Leistung, Kraftstoff, Preis, Getriebe (falls bekannt).

## (b) Risiken & Besichtigungs-Checkliste
- Bekannte Schwachstellen aus der DB mit Schweregrad und Baujahren
- Konkrete Checkliste (Markdown-Checkboxen) was bei der Besichtigung zu prüfen ist
- Auf motorspezifische Schwachstellen hinweisen

## (c) Preis-Einschätzung
- Marktspanne aus Web-Quellen (immer mit "laut Websuche (ungeprüft)" kennzeichnen)
- Einordnung des Inseratspreises in die Spanne
- Falls kein Web: ehrlich kommunizieren
- marktpreis_min und marktpreis_max als Integer-Zahlen befüllen (nur wenn aus Web ableitbar)

## (d) Fazit & Empfehlung
Begründete Empfehlung in Fettdruck: **KAUFEN / VERHANDELN / FINGER WEG**
Kurze Begründung (2–4 Sätze). Ggf. Verhandlungsspanne nennen.

REGELN:
1. Erfinde keine Zahlen — Specs nur aus DB-Kontext verwenden.
2. Web-Preise immer als ungeprüft kennzeichnen.
3. Sei direkt — keine leeren Phrasen.
4. Schreibe ausschließlich auf Deutsch.
5. Das JSON-Feld "bericht" darf Zeilenumbrüche (\\n) enthalten.\
"""


def _format_inserat(req: KaufCheckRequest) -> str:
    if req.freitext:
        return f"INSERAT-TEXT:\n{req.freitext}"

    lines = ["INSERAT-DATEN:"]
    if req.marke:          lines.append(f"Marke:          {req.marke}")
    if req.modell:         lines.append(f"Modell:         {req.modell}")
    if req.baujahr:        lines.append(f"Baujahr:        {req.baujahr}")
    if req.kilometerstand: lines.append(f"Kilometerstand: {req.kilometerstand:,} km".replace(",", "."))
    if req.motor:          lines.append(f"Motor:          {req.motor}")
    if req.kraftstoff:     lines.append(f"Kraftstoff:     {req.kraftstoff}")
    if req.preis_eur:      lines.append(f"Preis:          {req.preis_eur:,} €".replace(",", "."))
    if req.ausstattung:    lines.append(f"Ausstattung:    {', '.join(req.ausstattung)}")
    if req.beschreibung:   lines.append(f"Beschreibung:   {req.beschreibung}")
    return "\n".join(lines)


async def run_kaufcheck(req: KaufCheckRequest) -> dict:
    # 1. Baureihe + Motor erkennen
    baureihe    = find_baureihe(req.marke, req.modell, req.baujahr)
    motor_match = find_motor(baureihe, req.motor) if baureihe else None

    # 2. DB-Kontext
    db_ctx = build_db_context(baureihe, motor_match)

    # 3. Marktpreis per Tavily
    web_results: list[dict] = []
    if TAVILY_API_KEY:
        q_parts: list[str] = []
        if req.marke:          q_parts.append(req.marke)
        if req.modell:         q_parts.append(req.modell)
        if req.motor:          q_parts.append(req.motor)
        if req.baujahr:        q_parts.append(str(req.baujahr))
        if req.kilometerstand: q_parts.append(f"{req.kilometerstand // 1000 * 1000} km")
        q_parts.append("Gebrauchtpreis Deutschland")
        web_results = await tavily_search(" ".join(q_parts), count=5)

    web_ctx = results_to_context(web_results)
    belege  = results_to_belege(web_results)

    # 4. Gemini-Analyse
    user_msg = "\n\n".join(filter(None, [_format_inserat(req), db_ctx, web_ctx]))
    try:
        result = await call_gemini_json(_SYSTEM, user_msg)
    except RateLimitExhausted as exc:
        result = {"bericht": f"Gemini-Tageslimit erreicht: {exc}",
                  "empfehlung": "unbekannt", "preis_bewertung": "unbekannt"}

    hat_db, hat_web = baureihe is not None, bool(web_results)
    if hat_db and hat_web:   quelle, vertrauen = "gemischt", "mittel"
    elif hat_db:             quelle, vertrauen = "datenbank", "hoch"
    elif hat_web:            quelle, vertrauen = "web", "niedrig"
    else:                    quelle, vertrauen = "gemischt", "niedrig"

    return {
        "bericht":          result.get("bericht", ""),
        "empfehlung":       result.get("empfehlung", "unbekannt"),
        "preis_bewertung":  result.get("preis_bewertung", "unbekannt"),
        "marktpreis_min":   result.get("marktpreis_min"),
        "marktpreis_max":   result.get("marktpreis_max"),
        "baureihe_erkannt": baureihe["id"] if baureihe else None,
        "motor_erkannt":    motor_match["variante_id"] if motor_match else None,
        "quelle":           quelle,
        "vertrauen":        vertrauen,
        "belege":           belege,
    }
