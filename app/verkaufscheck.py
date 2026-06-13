from __future__ import annotations

"""
Verkaufs-Check: Optimale Preisstrategie für den Fahrzeugverkauf.

Ablauf:
  1. Baureihe + Motor in SQLite erkennen
  2. DB-Kontext aufbauen (Neupreis, Specs, Ausstattungslinien)
  3. Marktpreise vergleichbarer Fahrzeuge per Tavily ermitteln
  4. Gemini (JSON-Modus) liefert Preisspanne + Verkaufsstrategie-Bericht
"""

import logging

from app.car_lookup import find_baureihe, find_motor, build_db_context, call_gemini_json
from app.config import TAVILY_API_KEY
from app.gemini_retry import RateLimitExhausted
from app.models import VerkaufsCheckRequest
from app.web_search import tavily_search, results_to_context, results_to_belege

log = logging.getLogger(__name__)

_SYSTEM = """\
Du bist ein erfahrener KFZ-Verkaufsberater. Du hilfst dem Nutzer, seinen Wagen optimal zu vermarkten und einen fairen Preis zu erzielen.

Du erhältst:
1. FAHRZEUG-DATEN — Angaben des Eigentümers über sein Fahrzeug und dessen Zustand
2. DB-PROFIL — geprüfte Fakten (Neupreis, Specs, Ausstattungslinien) — zuverlässig
3. WEB-ERGEBNISSE — aktuelle Marktpreise vergleichbarer Fahrzeuge aus Tavily — Orientierung, ungeprüft

AUSGABE: Ausschließlich gültiges JSON. WICHTIG: Zuerst die Zahlfelder, dann "bericht".

{
  "schnellverkaufs_preis": <integer EUR, PFLICHT wenn ableitbar, sonst null>,
  "maximal_preis": <integer EUR, PFLICHT wenn ableitbar, sonst null>,
  "empfohlener_preis": <integer EUR, PFLICHT wenn ableitbar, sonst null>,
  "verkaufsdauer_tage_schnell": <integer Tage, z.B. 14 für 2 Wochen, PFLICHT wenn ableitbar>,
  "verkaufsdauer_tage_maximal": <integer Tage, z.B. 90 für 3 Monate, PFLICHT wenn ableitbar>,
  "marktpreis_min": <integer EUR aus Web, null wenn kein Web>,
  "marktpreis_max": <integer EUR aus Web, null wenn kein Web>,
  "bericht": "<vollständiger Markdown-Bericht — Struktur unten>"
}

BERICHT-STRUKTUR (Markdown im "bericht"-Feld, Zeilenumbrüche als \\n):

## Fahrzeug erkannt
Baureihe, Motor, Baujahr — eine Zeile.

## (a) Marktvergleich
Aktuelle Vergleichspreise aus den Web-Quellen (immer "laut Websuche (ungeprüft)" kennzeichnen).
Einordnung dieses Fahrzeugs in den Markt: Was hebt es hervor, was drückt den Preis?

## (b) Empfohlene Preisspanne

| Preis-Kategorie | Betrag (€) | Erwartete Verkaufszeit |
|-----------------|-----------|------------------------|
| Schnellverkauf | XX.XXX | ~X–Y Wochen |
| Empfohlener Preis | XX.XXX | ~X–Y Monate |
| Maximalpreis | XX.XXX | ~X–Y Monate |

Kurze Begründung der Spanne.
Falls Preisvorstellung angegeben: einordnen (realistisch / zu hoch / zu niedrig).

## (c) Preis-Optimierungstipps

**Was im Inserat betonen:**
Konkrete wertsteigernde Punkte aus Ausstattung und Zustand.

**Was vor dem Verkauf lohnt (Kosten → Mehrwert):**
Nur Maßnahmen bei denen der Mehrwert die Kosten übersteigt.

**Was NICHT lohnt:**
Zu teure Maßnahmen im Verhältnis zum Mehrwert.

## (d) Verkaufsstrategie & Zeitplan
- Wo inserieren: konkrete Empfehlung mit Begründung
- Preisstrategie: wann und um wie viel senken
- Verhandlungstipps

REGELN:
1. Neupreis und Specs nur aus DB-Profil.
2. Web-Preise immer als "laut Websuche (ungeprüft)" kennzeichnen.
3. Konkrete Zahlen — keine "könnte"-Phrasen ohne Zahl.
4. Alle Zahlfelder MÜSSEN als Integer stehen — nicht im Bericht verstecken.
5. Auf Deutsch schreiben.\
"""


def _format_fahrzeug(req: VerkaufsCheckRequest) -> str:
    if req.freitext:
        return f"MEIN FAHRZEUG (Freitext):\n{req.freitext}"

    lines = ["MEIN FAHRZEUG:"]
    if req.marke:            lines.append(f"Marke:              {req.marke}")
    if req.modell:           lines.append(f"Modell:             {req.modell}")
    if req.baujahr:          lines.append(f"Baujahr:            {req.baujahr}")
    if req.kilometerstand:   lines.append(f"Kilometerstand:     {req.kilometerstand:,} km".replace(",", "."))
    if req.motor:            lines.append(f"Motor:              {req.motor}")
    if req.kraftstoff:       lines.append(f"Kraftstoff:         {req.kraftstoff}")
    if req.ausstattung:      lines.append(f"Ausstattung:        {', '.join(req.ausstattung)}")
    if req.beschreibung:     lines.append(f"Zustand:            {req.beschreibung}")
    if req.maengel:          lines.append(f"Bekannte Mängel:    {', '.join(req.maengel)}")
    if req.preis_vorstellung:lines.append(f"Preisvorstellung:   {req.preis_vorstellung:,} €".replace(",", "."))
    return "\n".join(lines)


async def run_verkaufscheck(req: VerkaufsCheckRequest) -> dict:
    # 1. Baureihe + Motor erkennen
    baureihe    = find_baureihe(req.marke, req.modell, req.baujahr)
    motor_match = find_motor(baureihe, req.motor) if baureihe else None

    # 2. DB-Kontext
    db_ctx = build_db_context(baureihe, motor_match)

    # 3. Marktpreise per Tavily (vergleichbare Angebote)
    web_results: list[dict] = []
    if TAVILY_API_KEY:
        q_parts: list[str] = []
        if req.marke:          q_parts.append(req.marke)
        if req.modell:         q_parts.append(req.modell)
        if req.motor:          q_parts.append(req.motor)
        if req.baujahr:        q_parts.append(str(req.baujahr))
        if req.kilometerstand: q_parts.append(f"{req.kilometerstand // 1000 * 1000} km")
        q_parts.append("Gebrauchtpreis verkaufen Deutschland")
        web_results = await tavily_search(" ".join(q_parts), count=5)

    web_ctx = results_to_context(web_results)
    belege  = results_to_belege(web_results)

    # 4. Gemini-Analyse
    user_msg = "\n\n".join(filter(None, [_format_fahrzeug(req), db_ctx, web_ctx]))
    try:
        result = await call_gemini_json(_SYSTEM, user_msg)
    except RateLimitExhausted as exc:
        result = {"bericht": f"Gemini-Tageslimit erreicht: {exc}"}

    hat_db, hat_web = baureihe is not None, bool(web_results)
    if hat_db and hat_web:   quelle, vertrauen = "gemischt", "mittel"
    elif hat_db:             quelle, vertrauen = "datenbank", "hoch"
    elif hat_web:            quelle, vertrauen = "web", "niedrig"
    else:                    quelle, vertrauen = "gemischt", "niedrig"

    return {
        "bericht":                     result.get("bericht", ""),
        "schnellverkaufs_preis":        result.get("schnellverkaufs_preis"),
        "maximal_preis":               result.get("maximal_preis"),
        "empfohlener_preis":           result.get("empfohlener_preis"),
        "verkaufsdauer_tage_schnell":  result.get("verkaufsdauer_tage_schnell"),
        "verkaufsdauer_tage_maximal":  result.get("verkaufsdauer_tage_maximal"),
        "marktpreis_min":              result.get("marktpreis_min"),
        "marktpreis_max":              result.get("marktpreis_max"),
        "baureihe_erkannt":            baureihe["id"] if baureihe else None,
        "motor_erkannt":               motor_match["variante_id"] if motor_match else None,
        "quelle":                      quelle,
        "vertrauen":                   vertrauen,
        "belege":                      belege,
    }
