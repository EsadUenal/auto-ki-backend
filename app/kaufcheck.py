from __future__ import annotations

"""
Kauf-Check: Inserat-Analyse mit DB-Abgleich und Marktpreisbewertung.

Ablauf:
  1. Baureihe + Motorvariante in SQLite erkennen (Score-basiert)
  2. DB-Kontext aufbauen (Schwachstellen, Rückrufe, Motorspecs)
  3. Marktpreis per Tavily ermitteln
  4. Gemini (JSON-Modus) liefert strukturierten Bericht
  5. Ergebnis zurückgeben
"""

import json
import logging
import re

from google import genai
from google.genai import types as genai_types

from app.config import GEMINI_API_KEY, LLM_MODEL, TAVILY_API_KEY
from app.database import get_baureihe, get_conn
from app.gemini_retry import with_retry, RateLimitExhausted
from app.models import KaufCheckRequest
from app.web_search import tavily_search, results_to_context, results_to_belege

log = logging.getLogger(__name__)

_MARKEN_ALIAS = {
    "VW": "Volkswagen",
    "MERCEDES": "Mercedes-Benz",
    "MERC": "Mercedes-Benz",
    "BENZ": "Mercedes-Benz",
    "OPEL": "Opel",
    "SKODA": "Škoda",
}


# ---------- Baureihe + Motor erkennen ----------

def _find_baureihe(marke: str | None, modell: str | None, baujahr: int | None) -> dict | None:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id,marke,modell,generation,bauzeitraum_von,bauzeitraum_bis FROM baureihe"
        ).fetchall()

    if marke:
        marke = _MARKEN_ALIAS.get(marke.upper().strip(), marke.strip())

    scored: list[tuple[int, dict]] = []
    for r in rows:
        score = 0
        if marke:
            if r["marke"].lower() == marke.lower():
                score += 4
            elif marke.lower() in r["marke"].lower() or r["marke"].lower() in marke.lower():
                score += 2
        if modell:
            ml, rl = modell.lower(), r["modell"].lower()
            if ml == rl or ml in rl or rl in ml:
                score += 4
        if baujahr and score > 0:
            bvon = r["bauzeitraum_von"] or 0
            bbis = r["bauzeitraum_bis"] or 9999
            if bvon <= baujahr <= bbis:
                score += 5
            elif abs(bvon - baujahr) <= 2 or (bbis < 9999 and abs(bbis - baujahr) <= 2):
                score += 1
        if score > 0:
            scored.append((score, dict(r)))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][1]
    log.info("Baureihe erkannt: %s (score=%d)", best["id"], scored[0][0])
    return get_baureihe(best["marke"], best["modell"], best["generation"])


def _find_motor(baureihe: dict, hint: str | None) -> dict | None:
    if not hint or not baureihe["motoren"]:
        return None
    h = hint.lower()
    for m in baureihe["motoren"]:
        bez  = m["bezeichnung"].lower()
        code = (m["motorcode"] or "").lower()
        if h in bez or bez in h or (code and h in code):
            return m
    ps_match = re.search(r"(\d{2,3})\s*(?:ps|kw)", h)
    if ps_match:
        ps = int(ps_match.group(1))
        for m in baureihe["motoren"]:
            if m.get("leistung_ps") == ps or m.get("leistung_kw") == ps:
                return m
    return None


# ---------- DB-Kontext ----------

def _build_db_context(baureihe: dict | None, motor_match: dict | None) -> str:
    if baureihe is None:
        return "Kein geprüftes DB-Profil für dieses Fahrzeug vorhanden."

    lines = [
        f"## DB-Profil: {baureihe['marke']} {baureihe['modell']} {baureihe['generation']}",
        f"Bauzeitraum: {baureihe['bauzeitraum_von']}–{baureihe['bauzeitraum_bis'] or 'heute'}",
        f"Karosserie: {', '.join(baureihe['karosserie'])}",
        f"TÜV-Mängelquote: {baureihe['tuev_maengelquote'] or 'nicht erfasst'}",
        f"ADAC-Pannenkennziffer: {baureihe['adac_pannenkennziffer'] or 'nicht erfasst'}",
        "",
    ]

    motoren = [motor_match] if motor_match else baureihe["motoren"][:5]
    for m in motoren:
        lines += [
            f"### Motor: {m['bezeichnung']} | {m.get('motorcode', '?')} | {m['kraftstoff']}",
            f"Leistung: {m['leistung_ps']} PS / {m['leistung_kw']} kW | Drehmoment: {m['drehmoment_nm']} Nm",
            f"0–100: {m['beschleunigung_0_100']} s | Vmax: {m['vmax_kmh']} km/h",
            f"Verbrauch WLTP: {m['verbrauch_wltp'] or 'kein WLTP'} l/100km | Real: {m['verbrauch_real'] or '?'} l/100km",
            f"Neupreis ca.: {m['neupreis_ca_eur'] or 'nicht erfasst'} EUR",
        ]
        if m["schwachstellen_motor"]:
            lines.append("Motorprobleme:")
            for s in m["schwachstellen_motor"]:
                lines.append(
                    f"  [{s['schweregrad']}] {s['bauteil']}: {s['beschreibung']} "
                    f"(Baujahre: {s['baujahre']}, Kosten ca.: {s['kosten_ca']})"
                )
        if m["kritische_wartung"]:
            lines.append("Kritische Wartung:")
            for w in m["kritische_wartung"]:
                lines.append(f"  - {w['bauteil']}: {w['intervall']} — {w['hinweis']}")
        lines.append("")

    if baureihe["schwachstellen_baureihe"]:
        lines.append("### Schwachstellen Baureihe:")
        for s in baureihe["schwachstellen_baureihe"]:
            lines.append(
                f"  [{s['schweregrad']}] {s['bauteil']}: {s['beschreibung']} "
                f"(Baujahre: {s['betroffene_baujahre']})"
            )
        lines.append("")

    if baureihe["rueckrufe"]:
        lines.append("### KBA-Rückrufe:")
        for r in baureihe["rueckrufe"]:
            lines.append(
                f"  {r['datum']}: {r['mangel']} — {r['abhilfe']} (Ref: {r['kba_referenz']})"
            )

    return "\n".join(lines)


# ---------- Inserat formatieren ----------

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


# ---------- Gemini (JSON-Modus) ----------

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

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY nicht gesetzt.")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


async def _call_gemini(inserat_text: str, db_ctx: str, web_ctx: str) -> dict:
    parts = [inserat_text, "", db_ctx]
    if web_ctx:
        parts += ["", web_ctx]
    user_msg = "\n".join(parts)

    cfg = genai_types.GenerateContentConfig(
        system_instruction=_SYSTEM,
        temperature=0.2,
        max_output_tokens=8192,
        response_mime_type="application/json",
    )

    client = _get_client()
    response = await with_retry(lambda: client.aio.models.generate_content(
        model=LLM_MODEL,
        contents=[{"role": "user", "parts": [{"text": user_msg}]}],
        config=cfg,
    ))

    raw = (response.text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw.strip())

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Kaufcheck JSON-Parsing fehlgeschlagen. Raw[:300]: %s", raw[:300])
        return {
            "bericht":         raw or "Analyse fehlgeschlagen.",
            "empfehlung":      "unbekannt",
            "preis_bewertung": "unbekannt",
            "marktpreis_min":  None,
            "marktpreis_max":  None,
        }


# ---------- Haupt-Funktion ----------

async def run_kaufcheck(req: KaufCheckRequest) -> dict:
    # 1. Baureihe + Motor erkennen
    baureihe    = _find_baureihe(req.marke, req.modell, req.baujahr)
    motor_match = _find_motor(baureihe, req.motor) if baureihe else None

    # 2. DB-Kontext
    db_ctx = _build_db_context(baureihe, motor_match)

    # 3. Marktpreis per Tavily (immer beim Kaufcheck sinnvoll)
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
    inserat_text = _format_inserat(req)
    try:
        result = await _call_gemini(inserat_text, db_ctx, web_ctx)
    except RateLimitExhausted as exc:
        result = {
            "bericht":         f"Gemini-Tageslimit erreicht: {exc}",
            "empfehlung":      "unbekannt",
            "preis_bewertung": "unbekannt",
            "marktpreis_min":  None,
            "marktpreis_max":  None,
        }

    # 5. Quellen-Metadaten
    hat_db  = baureihe is not None
    hat_web = bool(web_results)
    if hat_db and hat_web:
        quelle, vertrauen = "gemischt", "mittel"
    elif hat_db:
        quelle, vertrauen = "datenbank", "hoch"
    elif hat_web:
        quelle, vertrauen = "web", "niedrig"
    else:
        quelle, vertrauen = "gemischt", "niedrig"

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
