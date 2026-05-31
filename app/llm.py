from __future__ import annotations

"""
Gemini-Anbindung mit DB-first-Logik (Abschnitt 4 der Spezifikation).

Ablauf:
  1. Frage analysieren → welche Baureihe(n) gesucht?
  2. Daten aus SQLite + ChromaDB laden
  3. Kontext an Gemini übergeben
  4. Antwort streamen (Generator → SSE)

Harte Zahlen kommen IMMER aus SQL, NIEMALS aus dem Modell.
"""

import json
import re
import sqlite3
from pathlib import Path
from typing import AsyncGenerator

from google import genai
from google.genai import types as genai_types

from app.config import GEMINI_API_KEY, LLM_MODEL, DB_PATH, CHROMA_PATH
from app.database import get_baureihe, search_baureihen

import chromadb

# ---------- Gemini Client ----------

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY nicht gesetzt.")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


# ---------- ChromaDB ----------

_chroma: chromadb.PersistentClient | None = None


def _get_chroma():
    global _chroma
    if _chroma is None:
        _chroma = chromadb.PersistentClient(path=str(CHROMA_PATH))
    return _chroma


def _vector_search(query: str, baureihe_ids: list[str], n: int = 6) -> list[str]:
    """Sucht passende Fließtexte in ChromaDB."""
    client = _get_chroma()
    results = []
    where = {"baureihe_id": {"$in": baureihe_ids}} if baureihe_ids else None

    for col_name in ["optisches_wissen", "technisches_wissen"]:
        try:
            col = client.get_collection(col_name)
            kwargs = {"query_texts": [query], "n_results": min(n, col.count())}
            if where:
                kwargs["where"] = where
            hits = col.query(**kwargs)
            results.extend(hits["documents"][0])
        except Exception:
            pass

    return results


# ---------- DB-Kontext aufbauen ----------

def _sql_context(baureihe_ids: list[str]) -> str:
    """Liest alle harten Fakten aus SQLite und baut einen strukturierten Kontext-String."""
    parts = []
    for bid in baureihe_ids:
        # Marke/Modell/Generation aus ID ermitteln
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        b = conn.execute("SELECT marke,modell,generation FROM baureihe WHERE id=?", (bid,)).fetchone()
        conn.close()
        if b is None:
            continue
        data = get_baureihe(b["marke"], b["modell"], b["generation"])
        if data is None:
            continue

        lines = [
            f"## {data['marke']} {data['modell']} {data['generation']} (ID: {data['id']})",
            f"Bauzeitraum: {data['bauzeitraum_von']}–{data['bauzeitraum_bis'] or 'heute'}",
            f"Karosserie: {', '.join(data['karosserie'])}",
            f"Vorgänger: {data['vorgaenger'] or '—'}",
            # Optische Erkennungsmerkmale — immer fest im Kontext, nicht nur über Vektorsuche
            f"Optische Erkennungsmerkmale (erkennung_generation): {data['erkennung_generation'] or 'nicht erfasst'}",
            f"Facelift-Merkmale: {data['facelift_merkmale'] or 'nicht erfasst'}",
            f"ADAC-Pannenkennziffer: {data['adac_pannenkennziffer'] or 'nicht erfasst'}",
            f"TÜV-Mängelquote: {data['tuev_maengelquote'] or 'nicht erfasst'}",
            f"Euro-NCAP-Sterne: {data['euro_ncap_sterne'] if data['euro_ncap_sterne'] is not None else 'nicht separat getestet'}",
            "",
            "### Motorvarianten (HARTE ZAHLEN — exakt verwenden, nicht runden/schätzen):",
        ]

        for m in data["motoren"]:
            motor_lines = [
                f"  Variante: {m['bezeichnung']} ({m['variante_id']})",
                f"    Motorcode: {m['motorcode']}  |  Kraftstoff: {m['kraftstoff']}",
                f"    Leistung: {m['leistung_ps']} PS / {m['leistung_kw']} kW",
                f"    Drehmoment: {m['drehmoment_nm']} Nm",
                f"    Getriebe: {', '.join(m['getriebe'])}  |  Antrieb: {m['antrieb']}",
                f"    0-100: {m['beschleunigung_0_100']} s  |  Vmax: {m['vmax_kmh']} km/h",
                f"    Verbrauch WLTP: {m['verbrauch_wltp'] or 'kein WLTP-Wert (NEFZ-Ära)'} l/100km",
                f"    Verbrauch real (Spritmonitor): {m['verbrauch_real'] or 'nicht erfasst'} l/100km",
                f"    CO2: {m['co2_g_km'] or 'nicht erfasst'} g/km",
                f"    Neupreis ca.: {m['neupreis_ca_eur'] or 'nicht erfasst'} EUR",
            ]
            if m["schwachstellen_motor"]:
                motor_lines.append("    Bekannte Motorprobleme:")
                for s in m["schwachstellen_motor"]:
                    motor_lines.append(
                        f"      - {s['bauteil']}: {s['beschreibung']} "
                        f"(Baujahre: {s['baujahre']}, Kosten ca.: {s['kosten_ca']})"
                    )
            if m["kritische_wartung"]:
                motor_lines.append("    Kritische Wartung:")
                for w in m["kritische_wartung"]:
                    motor_lines.append(f"      - {w['bauteil']}: {w['intervall']} — {w['hinweis']}")
            lines.extend(motor_lines)

        if data["schwachstellen_baureihe"]:
            lines.append("\n### Schwachstellen Baureihe:")
            for s in data["schwachstellen_baureihe"]:
                lines.append(
                    f"  [{s['schweregrad']}] {s['bauteil']}: {s['beschreibung']} "
                    f"(Baujahre: {s['betroffene_baujahre']})"
                )

        if data["rueckrufe"]:
            lines.append("\n### KBA-Rückrufe:")
            for r in data["rueckrufe"]:
                lines.append(
                    f"  {r['datum']}: {r['mangel']} — Abhilfe: {r['abhilfe']} "
                    f"(Ref: {r['kba_referenz']})"
                )

        parts.append("\n".join(lines))

    return "\n\n---\n\n".join(parts)


# ---------- Baureihe aus Frage erkennen ----------

_KNOWN = {
    "m4": ["bmw-m4-f82", "bmw-m4-g82"],
    "f82": ["bmw-m4-f82"],
    "g82": ["bmw-m4-g82"],
    "s55": ["bmw-m4-f82"],
    "s58": ["bmw-m4-g82"],
}


def _detect_baureihe_ids(message: str, verlauf: list[dict]) -> list[str]:
    """Einfache Keyword-Erkennung. Reicht für Phase 1."""
    text = (message + " " + " ".join(m.get("text", "") for m in verlauf)).lower()
    ids: set[str] = set()
    for keyword, bid_list in _KNOWN.items():
        if keyword in text:
            ids.update(bid_list)
    return list(ids)


# ---------- System-Prompt ----------

SYSTEM_PROMPT = """Du bist Auto-KI, ein ruhiger, kompetenter Automobil-Experte.

DEINE REGELN — NICHT VERHANDELBAR:
1. Harte Zahlen (PS, kW, Nm, Verbrauch, Beschleunigung, Preis) IMMER exakt aus dem KONTEXT unten verwenden. Niemals raten, niemals selbst berechnen, niemals runden außer der Kontext rundet.
2. Wenn ein Feld im Kontext "nicht erfasst" oder "nicht separat getestet" steht: sage genau das ehrlich. Erfinde KEINEN Wert.
3. Beantworte Laien-Fragen ("woran erkenne ich...?") und Profi-Fragen ("Schwachstellen des S55?") aus denselben Daten — passe nur den Ton an.
4. Ton: sachlich, klar, kein Hype, kein Verkaufsdruck.
5. Antworte auf Deutsch.
6. Wenn du Zahlen nennst, füge immer die Einheit hinzu (PS, Nm, km/h, l/100km usw.).
7. Spekuliere NICHT über Modelle oder Generationen, die nicht im Kontext stehen.

ERKENNUNGS- UND UNTERSCHIEDS-FRAGEN (zwingend):
Wenn die Frage fragt "woran erkenne ich", "wie unterscheidet sich", "was ist der Unterschied zwischen", "wie erkenne ich" oder ähnlich — dann IMMER in dieser Reihenfolge antworten:
  a) ZUERST: die sichtbaren optischen Merkmale aus dem Feld "erkennung_generation" im Kontext — diese stehen explizit drin und müssen genannt werden.
  b) DANN: Baujahr / Generation / technische Unterschiede.
  c) NICHT umkehren. Nicht mit Baujahr oder Motorcode beginnen, wenn optische Merkmale im Kontext stehen.

KONTEXT AUS GEPRÜFTER DATENBANK:
{kontext}

WICHTIG: Der Kontext oben ist die einzige zuverlässige Quelle für Fakten. Was dort steht, ist geprüft. Was dort fehlt, ist unbekannt — sage das ehrlich."""


# ---------- Haupt-Funktion: Chat (Streaming) ----------

async def chat_stream(
    message: str,
    verlauf: list[dict],
) -> AsyncGenerator[dict, None]:
    """
    Gibt Events als dicts zurück:
      {"type": "text", "delta": "..."}          — Textfragment
      {"type": "meta", "quelle": "...", ...}     — Abschluss-Metadaten
    """
    baureihe_ids = _detect_baureihe_ids(message, verlauf)

    # DB-Kontext aufbauen
    sql_ctx = _sql_context(baureihe_ids) if baureihe_ids else ""
    vec_docs = _vector_search(message, baureihe_ids) if baureihe_ids else []
    vec_ctx = "\n\n".join(vec_docs) if vec_docs else ""

    kontext = "\n\n".join(filter(None, [sql_ctx, vec_ctx]))

    quelle = "datenbank" if baureihe_ids else "gemischt"
    vertrauen = "hoch" if baureihe_ids else "mittel"

    if not kontext:
        kontext = "Keine Fahrzeugdaten in der Datenbank gefunden. Antworte, dass du dazu kein geprüftes Profil hast."
        quelle = "gemischt"
        vertrauen = "niedrig"

    system = SYSTEM_PROMPT.format(kontext=kontext)

    # Verlauf für Gemini aufbauen
    history = []
    for msg in verlauf:
        role = "user" if msg.get("rolle") == "user" else "model"
        history.append({"role": role, "parts": [{"text": msg.get("text", "")}]})

    client = _get_client()

    # Streaming-Anfrage
    response = client.models.generate_content_stream(
        model=LLM_MODEL,
        contents=history + [{"role": "user", "parts": [{"text": message}]}],
        config=genai_types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.3,
        ),
    )

    for chunk in response:
        if chunk.text:
            yield {"type": "text", "delta": chunk.text}

    yield {
        "type": "meta",
        "quelle": quelle,
        "fahrzeug_referenz": baureihe_ids,
        "vertrauen": vertrauen,
        "belege": [],
    }
