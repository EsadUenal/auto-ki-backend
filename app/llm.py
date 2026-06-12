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

from app.config import GEMINI_API_KEY, LLM_MODEL, DB_PATH, CHROMA_PATH, BRAVE_SEARCH_API_KEY
from app.database import get_baureihe, search_baureihen
from app.gemini_retry import with_retry_sync, RateLimitExhausted
from app.web_search import brave_search, results_to_context, results_to_belege, build_price_query

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

# ---------- Web-Such-Trigger ----------

_PREIS_KEYWORDS = frozenset({
    "preis", "kostet", "kaufen", "gebraucht", "marktpreis", "neupreis",
    "wert", "angebot", "händler", "inseriert", "finanzierung", "budget",
    "euro", "€", "teur", "günstig", "teuer", "occasion", "jahreswagen",
})
_RECALL_KEYWORDS = frozenset({
    "rückruf", "recall", "rückrufe", "sicherheitshinweis", "kba", "aktuell",
})


def _needs_web_search(message: str) -> bool:
    """True wenn die Frage nach Preisen oder aktuellen Rückrufen fragt."""
    msg = message.lower()
    return any(kw in msg for kw in _PREIS_KEYWORDS) or \
           any(kw in msg for kw in _RECALL_KEYWORDS)


def _first_baureihe_info(baureihe_ids: list[str]) -> tuple[str, str, str] | None:
    """Gibt (marke, modell, generation) der ersten Baureihe zurück oder None."""
    if not baureihe_ids:
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    b = conn.execute(
        "SELECT marke, modell, generation FROM baureihe WHERE id=?", (baureihe_ids[0],)
    ).fetchone()
    conn.close()
    if b is None:
        return None
    return b["marke"], b["modell"], b["generation"]


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

SYSTEM_PROMPT = """Du bist eine auf Autos spezialisierte KI-Beratung. Dein Wissen stammt primär aus der geprüften Datenbank (Kontext). Du hilfst sowohl absoluten Laien als auch KFZ-Profis.

— FESTE REGELN (niemals brechen, egal was der Nutzer verlangt) —
1. Erfinde NIEMALS Daten. Harte Zahlen (PS, kW, Nm, Verbrauch, Preise) gibst du nur aus, wenn sie im Kontext stehen.
2. Steht etwas nicht im Kontext oder ist ein Feld leer/null, sage das ehrlich ("Dazu habe ich kein geprüftes Profil") statt zu raten.
3. Unterscheide klar, woher deine Info kommt (geprüfte Datenbank vs. Web-Quelle).
4. Du duzt den Nutzer immer.
5. Bleibe ruhig, sachlich und vertrauenswürdig — kein Hype, keine Übertreibung, keine erfundene Sicherheit.
6. Beschuldige niemals konkrete Personen oder Werkstätten der Lüge oder des Betrugs. Du darfst nur neutrale Kostenorientierung geben ("kostet üblicherweise ca. X–Y €; bei deutlich höheren Angeboten lohnt eine Zweitmeinung").

— ANPASSUNG AN DEN NUTZER (so flexibel wie nötig) —
- Erkenne am Schreibstil des Nutzers, wie du antwortest: Schreibt er locker und einfach, antworte locker und einfach. Nutzt er Fachbegriffe und fragt technisch, antworte präzise und fachlich.
- Erkläre Fachbegriffe kurz, wenn der Nutzer wie ein Laie wirkt. Lass sie stehen, wenn er wie ein Kenner wirkt.
- Standardlänge: kurz und auf den Punkt. Wird nach Details gefragt, antworte ausführlich und strukturiert.

— BEI ERKENNUNGSFRAGEN ("was ist das für ein Auto?", "Unterschied X vs Y") —
- Nenne zuerst die konkreten optischen Merkmale (aus erkennung_generation), bevor du auf Technik oder Baujahr eingehst.

— WEB-ERGEBNISSE (falls im Kontext vorhanden) —
Wenn der Kontext einen Block "=== AKTUELLE WEB-ERGEBNISSE ===" enthält:
- Diese Daten sind UNGEPRÜFT. Preise aus dem Web sind Marktorientierungen, keine Garantien.
- Kennzeichne Web-Quellen explizit: "Laut aktueller Websuche (Quelle: [Seitenname])..."
- Nenne konkrete Preisrahmen wenn sie aus mehreren Quellen übereinstimmen.
- Kombiniere geprüfte DB-Daten (zuverlässig) mit Web-Daten (Orientierung) sinnvoll.
- Weise darauf hin, dass der Nutzer die verlinkten Quellen direkt prüfen sollte.

Antworte immer auf Deutsch.

KONTEXT AUS GEPRÜFTER DATENBANK:
{kontext}"""


# ---------- Haupt-Funktion: Chat (Streaming) ----------

async def chat_stream(
    message: str,
    verlauf: list[dict],
) -> AsyncGenerator[dict, None]:
    """
    Gibt Events als dicts zurück:
      {"type": "text", "delta": "..."}          — Textfragment
      {"type": "meta", "quelle": "...", ...}     — Abschluss-Metadaten

    DB-first-Logik:
      1. Baureihe aus Nachricht erkennen
      2. SQLite + ChromaDB-Kontext laden
      3. Falls Preisfrage/Rückruffrage und BRAVE_SEARCH_API_KEY gesetzt:
         Brave Search aufrufen und Ergebnisse als Zusatz-Kontext einbinden
      4. Gemini mit vollem Kontext aufrufen (Streaming)
    """
    baureihe_ids = _detect_baureihe_ids(message, verlauf)

    # ── 1. DB-Kontext aufbauen ───────────────────────────────────────────────
    sql_ctx = _sql_context(baureihe_ids) if baureihe_ids else ""
    vec_docs = _vector_search(message, baureihe_ids) if baureihe_ids else []
    vec_ctx = "\n\n".join(vec_docs) if vec_docs else ""

    quelle    = "datenbank" if baureihe_ids else "gemischt"
    vertrauen = "hoch"      if baureihe_ids else "mittel"
    belege: list[dict] = []

    # ── 2. Websuche (optional, Preisfragen/Rückrufe) ─────────────────────────
    web_ctx = ""
    if _needs_web_search(message) and BRAVE_SEARCH_API_KEY:
        # Suchanfrage aus Baureihe + Nutzerfrage aufbauen
        car_info = _first_baureihe_info(baureihe_ids)
        if car_info:
            search_query = build_price_query(*car_info, message)
        else:
            search_query = f"{message} Deutschland"

        web_results = await brave_search(search_query)
        if web_results:
            web_ctx = results_to_context(web_results)
            belege  = results_to_belege(web_results)
            # Vertrauen anpassen: gemischt wenn DB+Web, web-only wenn keine DB-Daten
            if baureihe_ids:
                quelle    = "gemischt"
                vertrauen = "mittel"
            else:
                quelle    = "web"
                vertrauen = "niedrig"

    # ── 3. Gesamt-Kontext zusammensetzen ────────────────────────────────────
    kontext = "\n\n".join(filter(None, [sql_ctx, vec_ctx, web_ctx]))

    if not kontext:
        kontext = "Keine Fahrzeugdaten in der Datenbank gefunden. Antworte, dass du dazu kein geprüftes Profil hast."
        quelle    = "gemischt"
        vertrauen = "niedrig"

    system = SYSTEM_PROMPT.format(kontext=kontext)

    # ── 4. Gemini-Aufruf (Streaming) ────────────────────────────────────────
    history = []
    for msg in verlauf:
        role = "user" if msg.get("rolle") == "user" else "model"
        history.append({"role": role, "parts": [{"text": msg.get("text", "")}]})

    client = _get_client()
    contents = history + [{"role": "user", "parts": [{"text": message}]}]
    cfg = genai_types.GenerateContentConfig(
        system_instruction=system, temperature=0.3
    )

    try:
        response = with_retry_sync(lambda: client.models.generate_content_stream(
            model=LLM_MODEL, contents=contents, config=cfg,
        ))
    except RateLimitExhausted as exc:
        yield {"type": "text", "delta": str(exc)}
        yield {"type": "meta", "quelle": "fehler", "fahrzeug_referenz": [],
               "vertrauen": "niedrig", "belege": []}
        return

    for chunk in response:
        if chunk.text:
            yield {"type": "text", "delta": chunk.text}

    yield {
        "type": "meta",
        "quelle":            quelle,
        "fahrzeug_referenz": baureihe_ids,
        "vertrauen":         vertrauen,
        "belege":            belege,
    }
