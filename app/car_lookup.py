from __future__ import annotations

"""
Gemeinsame Hilfsfunktionen für Kauf-Check und Verkaufs-Check:
  - Baureihe + Motor per Score-Matching in der DB erkennen
  - DB-Kontext-String aufbauen (Specs, Schwachstellen, Rückrufe)
  - Gemini (JSON-Modus) aufrufen
"""

import json
import logging
import re

from google import genai
from google.genai import types as genai_types

from app.config import GEMINI_API_KEY, LLM_MODEL
from app.database import get_baureihe, get_conn
from app.gemini_retry import with_retry

log = logging.getLogger(__name__)

_MARKEN_ALIAS = {
    "VW": "Volkswagen",
    "MERCEDES": "Mercedes-Benz",
    "MERC": "Mercedes-Benz",
    "BENZ": "Mercedes-Benz",
    "OPEL": "Opel",
    "SKODA": "Škoda",
}


# ---------- Fahrzeug-Erkennung ----------

def find_baureihe(marke: str | None, modell: str | None, baujahr: int | None) -> dict | None:
    """Findet die Baureihe mit dem höchsten Treffer-Score."""
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


def find_motor(baureihe: dict, hint: str | None) -> dict | None:
    """Findet die passende Motorvariante per Textabgleich."""
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
        val = int(ps_match.group(1))
        for m in baureihe["motoren"]:
            if m.get("leistung_ps") == val or m.get("leistung_kw") == val:
                return m
    return None


# ---------- DB-Kontext ----------

def build_db_context(baureihe: dict | None, motor_match: dict | None) -> str:
    """Baut den strukturierten DB-Kontext-String (Specs, Schwachstellen, Rückrufe)."""
    if baureihe is None:
        return "Kein geprüftes DB-Profil für dieses Fahrzeug vorhanden."

    lines = [
        f"## DB-Profil: {baureihe.get('marke','')} {baureihe.get('modell','')} {baureihe.get('generation','')}",
        f"Bauzeitraum: {baureihe.get('bauzeitraum_von','?')}–{baureihe.get('bauzeitraum_bis') or 'heute'}",
        f"Karosserie: {', '.join(baureihe.get('karosserie') or [])}",
        f"TÜV-Mängelquote: {baureihe.get('tuev_maengelquote') or 'nicht erfasst'}",
        f"ADAC-Pannenkennziffer: {baureihe.get('adac_pannenkennziffer') or 'nicht erfasst'}",
        "",
    ]

    if baureihe.get("ausstattungslinien"):
        lines.append("Ausstattungslinien:")
        for a in baureihe["ausstattungslinien"]:
            lines.append(f"  {a.get('name','?')} ({a.get('typ','?')}): {a.get('abgrenzung') or ''}")
        lines.append("")

    motoren = [motor_match] if motor_match else baureihe.get("motoren", [])[:5]
    for m in motoren:
        lines += [
            f"### Motor: {m.get('bezeichnung','?')} | {m.get('motorcode','?')} | {m.get('kraftstoff','?')}",
            f"Leistung: {m.get('leistung_ps','?')} PS / {m.get('leistung_kw','?')} kW | Drehmoment: {m.get('drehmoment_nm','?')} Nm",
            f"0–100: {m.get('beschleunigung_0_100','?')} s | Vmax: {m.get('vmax_kmh','?')} km/h",
            f"Verbrauch WLTP: {m.get('verbrauch_wltp') or 'kein WLTP'} l/100km | Real: {m.get('verbrauch_real','?')} l/100km",
            f"Neupreis ca.: {m.get('neupreis_ca_eur') or 'nicht erfasst'} EUR",
        ]
        # schwachstelle_motor hat KEIN schweregrad-Feld — nur bauteil/beschreibung/baujahre/kosten_ca
        if m.get("schwachstellen_motor"):
            lines.append("Motorprobleme:")
            for s in m["schwachstellen_motor"]:
                lines.append(
                    f"  {s.get('bauteil','?')}: {s.get('beschreibung','?')} "
                    f"(Baujahre: {s.get('baujahre','?')}, Kosten ca.: {s.get('kosten_ca','?')})"
                )
        if m.get("kritische_wartung"):
            lines.append("Kritische Wartung:")
            for w in m["kritische_wartung"]:
                lines.append(f"  - {w.get('bauteil','?')}: {w.get('intervall','?')} — {w.get('hinweis','?')}")
        lines.append("")

    # schwachstelle_baureihe HAT schweregrad — trotzdem .get() für Robustheit
    if baureihe.get("schwachstellen_baureihe"):
        lines.append("### Schwachstellen Baureihe:")
        for s in baureihe["schwachstellen_baureihe"]:
            schweregrad = s.get("schweregrad")
            prefix = f"[{schweregrad}] " if schweregrad else ""
            lines.append(
                f"  {prefix}{s.get('bauteil','?')}: {s.get('beschreibung','?')} "
                f"(Baujahre: {s.get('betroffene_baujahre','?')})"
            )
        lines.append("")

    if baureihe.get("rueckrufe"):
        lines.append("### KBA-Rückrufe:")
        for r in baureihe["rueckrufe"]:
            lines.append(
                f"  {r.get('datum','?')}: {r.get('mangel','?')} — {r.get('abhilfe','?')} (Ref: {r.get('kba_referenz','?')})"
            )

    return "\n".join(lines)


# ---------- Gemini JSON-Aufruf ----------

_client: genai.Client | None = None


def get_gemini_client() -> genai.Client:
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY nicht gesetzt.")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def _escape_json_strings(raw: str) -> str:
    """
    Escapt literal Newlines/Tabs die INNERHALB von JSON-String-Werten stehen.
    Gemini gibt bei langen Texten manchmal raw \\n statt \\\\n aus.
    Einfache State-Machine: verfolgt ob wir in einem String sind.
    """
    out: list[str] = []
    in_str = False
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and in_str and i + 1 < len(raw):
            out.append(ch)
            out.append(raw[i + 1])
            i += 2
            continue
        if ch == '"':
            in_str = not in_str
            out.append(ch)
        elif in_str and ch == "\n":
            out.append("\\n")
        elif in_str and ch == "\r":
            out.append("\\r")
        elif in_str and ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


async def call_gemini_json(system_prompt: str, user_msg: str) -> dict:
    """
    Ruft Gemini im JSON-Modus auf und gibt das geparste Dict zurück.
    Zwei-Stufen-Parsing: erst normal, dann mit Newline-Repair für
    den häufigen Fall dass Gemini literal \\n in Stringwerten ausgibt.
    """
    cfg = genai_types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=0.2,
        max_output_tokens=16384,
        response_mime_type="application/json",
    )
    client = get_gemini_client()
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
        try:
            return json.loads(_escape_json_strings(raw))
        except json.JSONDecodeError:
            log.warning("Gemini JSON-Parsing fehlgeschlagen (beide Versuche). Raw[:300]: %s", raw[:300])
            return {"bericht": raw or "Analyse fehlgeschlagen."}
