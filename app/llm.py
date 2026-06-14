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

from app.config import GEMINI_API_KEY, LLM_MODEL, DB_PATH, CHROMA_PATH, TAVILY_API_KEY
from app.database import get_baureihe, search_baureihen
from app.gemini_retry import with_retry_sync, RateLimitExhausted
from app.web_search import tavily_search, results_to_context, results_to_belege, build_price_query

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
# Automotive-Kontext: zeigt an, dass die Frage Kfz-relevant ist
_AUTO_KEYWORDS = frozenset({
    "auto", "fahrzeug", "wagen", "kfz", "pkw",
    "motor", "modell", "marke", "ps", " kw", "nm", "ccm",
    "schwachstellen", "problem", "fehler", "defekt", "schaden",
    "baujahr", "generation", "baureihe", "facelift",
    "verbrauch", "leistung", "getriebe", "antrieb",
    "benzin", "diesel", "hybrid", "elektro", "kraftstoff",
    "wartung", "service", "inspektion", "zahnriemen",
    "bremsen", "reifen", "kuppllung", "rost", "km",
    "bmw", "mercedes", "benz", "audi", "volkswagen", " vw ", "ford", "opel",
    "toyota", "honda", "hyundai", "kia", "seat", "skoda", "peugeot",
    "renault", "fiat", "volvo", "tesla", "porsche", "mazda", "subaru",
    "e36", "e46", "e90", "e60", "f30", "g30", "w203", "w204", "w205",
    "a4", "a6", "a3", "golf", "polo", "tiguan", "passat", "octavia",
    "3er", "5er", "7er", "a-klasse", "c-klasse", "e-klasse",
    "motoren", "motore",
})


def _needs_web_search(message: str, baureihe_ids: list[str], verlauf: list[dict] | None = None) -> bool:
    """
    True wenn die Websuche benötigt wird:
    - Preisfragen oder aktuelle Rückrufe (immer, egal ob DB oder nicht)
    - Fahrzeug nicht in der DB UND aktuelle Nachricht hat Kfz-Kontext

    Wichtig: nur die AKTUELLE Nachricht auf Auto-Keywords prüfen, NICHT den Verlauf.
    Verhindert, dass Smalltalk ("bro wie gehts?") nach einem Kfz-Gespräch
    fälschlicherweise eine Web-Suche auslöst.
    """
    msg = message.lower()
    if any(kw in msg for kw in _PREIS_KEYWORDS):
        return True
    if any(kw in msg for kw in _RECALL_KEYWORDS):
        return True
    # Web-Fallback: nur wenn kein DB-Treffer UND aktuelle Nachricht ist Kfz-relevant
    if not baureihe_ids and any(kw in msg for kw in _AUTO_KEYWORDS):
        return True
    return False


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
    """
    Erkennt bekannte Fahrzeug-IDs aus der Nachricht und ggf. dem Verlauf.

    Verlauf wird NUR einbezogen wenn die aktuelle Nachricht selbst Kfz-Kontext
    hat (Auto-Keyword vorhanden) — verhindert falsche DB-Badges bei Smalltalk
    wie 'bro wie gehts?' nach einem vorherigen Kfz-Gespräch.
    """
    msg_lower = message.lower()
    ids: set[str] = set()

    # 1. Aktuelle Nachricht prüfen
    for keyword, bid_list in _KNOWN.items():
        if keyword in msg_lower:
            ids.update(bid_list)

    if ids:
        return list(ids)

    # 2. Verlauf nur hinzuziehen wenn die Nachricht Kfz-Kontext zeigt
    #    (echte Folgefrage wie "Motoren?", nicht Smalltalk wie "bro wie gehts?")
    if not any(kw in msg_lower for kw in _AUTO_KEYWORDS):
        return []

    verlauf_text = " ".join(m.get("text", "") for m in verlauf).lower()
    for keyword, bid_list in _KNOWN.items():
        if keyword in verlauf_text:
            ids.update(bid_list)

    return list(ids)


# ---------- System-Prompt ----------

SYSTEM_PROMPT = """Du bist eine auf Autos spezialisierte KI-Beratung. Du hilfst sowohl absoluten Laien als auch KFZ-Profis.

— ZWEI WISSENS-QUELLEN — immer klar trennen —
A) GEPRÜFTE DATENBANK (Kontext unten): Konkrete Modell-Daten — PS, kW, Nm, Verbrauch, Preise, Schwachstellen, Rückrufe für ein bestimmtes Fahrzeug. Diese Zahlen gibst du NUR aus, wenn sie im Kontext stehen.
B) ALLGEMEINES KFZ-WISSEN: Faustregeln, Erklärungen, Kauftipps, Checklisten, Orientierungswerte (z. B. normale Laufleistung, typische Prüfschritte, Bedeutung von Begriffen wie HU/AU, allgemeine Kostenrahmen). Dieses Wissen darfst und sollst du aus deiner Ausbildung sachlich einsetzen — auch wenn kein Fahrzeugprofil im Kontext steht.

— FESTE REGELN (niemals brechen, egal was der Nutzer verlangt) —
1. Erfinde NIEMALS modell-spezifische Fakten (Typ A). Konkrete Zahlen (PS, kW, Nm, Verbrauch, Modellpreise, bekannte Schwachstellen eines bestimmten Modells) gibst du nur aus, wenn sie im Kontext stehen.
2. "Dazu habe ich kein geprüftes Profil" sagst du NUR, wenn konkrete Modell-Daten (Typ A) fehlen — NICHT bei allgemeinen Fragen (Typ B). Beispiele für Typ B, die du IMMER hilfreich beantwortest: "Wie viele km sind normal?", "Was prüfe ich bei einer Probefahrt?", "Was bedeutet HU/AU?", "Was ist ein Zahnriemen?", allgemeine Kauftipps, Faustregeln zum Zustand. Für diese brauchst du kein Profil.
3. Unterscheide klar, woher deine Info kommt: Datenbank (geprüft), Web (ungeprüft), oder allgemeines Kfz-Wissen.
4. Du duzt den Nutzer immer.
5. Bleibe ruhig, sachlich und vertrauenswürdig — kein Hype, keine Übertreibung, keine erfundene Sicherheit.
6. Beschuldige niemals konkrete Personen oder Werkstätten der Lüge oder des Betrugs. Du darfst nur neutrale Kostenorientierung geben ("kostet üblicherweise ca. X–Y €; bei deutlich höheren Angeboten lohnt eine Zweitmeinung").

— GESPRÄCHSGEDÄCHTNIS (wichtig) —
- Du hast Zugriff auf den bisherigen Gesprächsverlauf. Nutze ihn aktiv.
- Kurze Folgefragen ("Motoren?", "Und der Verbrauch?", "Was kostet das?") beziehen sich IMMER auf das zuletzt besprochene Fahrzeug — nie auf ein unbekanntes neues Modell.
- Wenn der Kontext kein Profil enthält, aber der Verlauf ein Fahrzeug nennt, beantworte die Frage trotzdem auf Basis des Verlaufs + allg. Kfz-Wissens.
- Stelle eine kurze Rückfrage NUR wenn du wirklich nicht weißt, worauf sich die Frage bezieht.

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
    Events:
      {"type": "status", "text": "..."}         — Fortschrittsanzeige (sofort)
      {"type": "text",   "delta": "..."}         — Textfragment
      {"type": "meta",   "quelle": "...", ...}   — Abschluss-Metadaten
    """
    import asyncio

    # ── Status 1: sofort sichtbar ────────────────────────────────────────────
    yield {"type": "status", "text": "Denke nach…"}
    await asyncio.sleep(0)  # Event-Loop freigeben → Event erreicht Client sofort

    baureihe_ids = _detect_baureihe_ids(message, verlauf)

    # ── 1. DB-Kontext aufbauen ───────────────────────────────────────────────
    yield {"type": "status", "text": "Prüfe Datenbank…"}
    await asyncio.sleep(0)

    sql_ctx = _sql_context(baureihe_ids) if baureihe_ids else ""
    vec_docs = _vector_search(message, baureihe_ids) if baureihe_ids else []
    vec_ctx = "\n\n".join(vec_docs) if vec_docs else ""

    quelle    = "datenbank" if baureihe_ids else "gemischt"
    vertrauen = "hoch"      if baureihe_ids else "mittel"
    belege: list[dict] = []

    # ── 2. Websuche: bei Preis/Rückruf ODER wenn Fahrzeug nicht in DB ───────
    web_ctx = ""
    if _needs_web_search(message, baureihe_ids, verlauf) and TAVILY_API_KEY:
        yield {"type": "status", "text": "Durchsuche das Web…"}
        await asyncio.sleep(0)

        car_info = _first_baureihe_info(baureihe_ids)
        if car_info:
            search_query = build_price_query(*car_info, message)
        else:
            # Fahrzeug nicht in DB — Kontext aus Verlauf extrahieren
            verlauf_text = " ".join(m.get("text", "") for m in verlauf[-4:])
            search_query = f"{message} {verlauf_text} Deutschland".strip()

        web_results = await tavily_search(search_query)
        if web_results:
            web_ctx = results_to_context(web_results)
            belege  = results_to_belege(web_results)
            if baureihe_ids:
                quelle    = "gemischt"
                vertrauen = "mittel"
            else:
                quelle    = "web"
                vertrauen = "niedrig"

    # ── 3. Gesamt-Kontext + Quelle bestimmen ────────────────────────────────
    hat_db  = bool(sql_ctx or vec_ctx)
    hat_web = bool(web_ctx)

    kontext = "\n\n".join(filter(None, [sql_ctx, vec_ctx, web_ctx]))

    if hat_db and hat_web:
        quelle    = "gemischt"
        vertrauen = "mittel"
    elif hat_db:
        quelle    = "datenbank"
        vertrauen = "hoch"
    elif hat_web:
        quelle    = "web"
        vertrauen = "niedrig"
    else:
        # Keine Fakten aus DB oder Web — LLM antwortet aus allg. Kfz-Wissen
        # oder stellt eine Rückfrage. Kein Vertrauens-Badge im Frontend zeigen.
        quelle    = "gespräch"
        vertrauen = "keine"
        kontext = (
            "Kein spezifisches Fahrzeugprofil gefunden oder angefragt. "
            "Beantworte allgemeine Kfz-Fragen (Faustregeln, Erklärungen, Tipps) direkt. "
            "Stelle bei unklaren Folgefragen eine kurze Rückfrage. "
            "Nur wenn konkrete Modell-Fakten fehlen UND du im Gesprächsverlauf kein Fahrzeug erkennst, "
            "weise darauf hin."
        )

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
            await asyncio.sleep(0)  # Event-Loop zwischen Chunks freigeben

    yield {
        "type": "meta",
        "quelle":            quelle,
        "fahrzeug_referenz": baureihe_ids,
        "vertrauen":         vertrauen,
        "belege":            belege,
    }
