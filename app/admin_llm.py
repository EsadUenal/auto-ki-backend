"""
LLM-gestützte Admin-Funktionen:
  - entwurf_erstellen()   : Fahrzeug-Schema-Entwurf (einmalig, non-streaming)
  - entwurf_stream()      : Fahrzeug-Schema-Entwurf als SSE-Stream
  - generationen_auflisten(): Batch — schnelle Liste via Flash-Lite
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import AsyncGenerator

log = logging.getLogger(__name__)

from google.genai import types as genai_types

from app.llm import _get_client
from app.config import LLM_MODEL, FAST_LLM_MODEL
from app.gemini_retry import with_retry_sync, RateLimitExhausted  # noqa: F401

# ------------------------------------------------------------------ #
#  System-Prompts                                                     #
# ------------------------------------------------------------------ #

_SCHEMA_SYSTEM = """Du bist ein präziser Automobil-Datenbankassistent.
Deine Aufgabe: Ein vollständiges Fahrzeug-Datenprofil als valides JSON erstellen.

PFLICHTREGELN:
1. Antworte NUR mit einem einzigen JSON-Objekt, kein Text davor oder danach, keine Markdown-Fences.
2. Unsichere oder geschätzte Werte (Preise, Verbrauch, CO2): als Zahl eintragen UND den Schlüssel "hinweise" im Wurzelobjekt mit einem Eintrag ergänzen, z.B. {"neupreis_ca_eur": "ca."}.
3. Unbekannte Felder → null. Niemals raten oder erfinden. Lieber null als falsch.
4. Harte Zahlen (PS, kW, Nm, Hubraum, 0-100, Vmax) nur eintragen, wenn du sie mit hoher Sicherheit kennst. Sonst null.
5. IDs automatisch generieren: baureihe_id = "{marke}-{modell}-{generation}" in Kleinbuchstaben mit Bindestrichen. variante_id = "{baureihe_id}-{bezeichnung-slug}".
6. Das Feld "letzte_aktualisierung" immer auf das heutige Datum (YYYY-MM) setzen.
7. "kraftstoff" nur aus: Benzin, Diesel, Elektro, Plug-in-Hybrid, Mild-Hybrid.
8. "antrieb" nur aus: Heck, Front, Allrad.
9. "schweregrad" in schwachstellen_baureihe nur aus: gering, mittel, hoch.
10. "typ" in ausstattungslinien nur aus: Basis, Ausstattungslinie, M Performance, Echtes M-Modell.

OPTISCHE FELDER — BESONDERE PFLICHTREGELN (häufigste Fehlerquelle):
11. "erkennung_generation": NIEMALS nur ein interner Baucode (z.B. "F48" oder "G01"). IMMER ein
    ausführlicher Fließtext mit mind. 2–3 konkreten, sichtbaren Merkmalen, z.B.:
    Scheinwerferform, Niere/Grille-Design, Karosserieproportionen, Heckleuchtenform,
    markante Unterscheidung zum Vorgänger. Beispiel für guten Wert:
    "Charakteristisch sind die flachen, schmalen Scheinwerfer mit L-förmigem Tagfahrlicht,
    die breite zweigeteilte Niere und der coupéartige Dachabschluss. Gegenüber dem E84
    deutlich keilförmigere Silhouette mit höherer Gürtellinie."
12. "facelift_merkmale": Ebenso konkreter Fließtext — welche Teile wurden neu gestaltet,
    was änderte sich sichtbar? Kein null wenn ein Facelift stattfand.
13. "optische_unterscheidung" bei Motorvarianten: Beschreibt sichtbare Unterschiede zur
    Basisvariante (andere Endrohre, Stoßstänger, Felgen, Embleme). Kein null wenn Unterschiede
    existieren, kein Baucode.

AUSGABE-SCHEMA (exakt diese Struktur, alle Felder vorhanden):
{
  "id": "...",
  "marke": "...",
  "modell": "...",
  "generation": "...",
  "bauzeitraum_von": <Zahl|null>,
  "bauzeitraum_bis": <Zahl|null>,
  "karosserie": ["..."],
  "segment": "...",
  "vorgaenger": <"..."|null>,
  "erkennung_generation": "...",
  "facelift_merkmale": <"..."|null>,
  "adac_pannenkennziffer": <"..."|null>,
  "tuev_maengelquote": <"..."|null>,
  "dekra_urteil": <"..."|null>,
  "euro_ncap_sterne": <0-5|null>,
  "euro_ncap_jahr": <Zahl|null>,
  "wartung_oel_km": <Zahl|null>,
  "wartung_hu_intervall": <"..."|null>,
  "kaufberatung": <"..."|null>,
  "letzte_aktualisierung": "YYYY-MM",
  "ausstattungslinien": [
    {"name":"...","typ":"...","optische_merkmale":"...","abgrenzung":<"..."|null>}
  ],
  "schwachstellen_baureihe": [
    {"bauteil":"...","beschreibung":"...","betroffene_baujahre":"...","schweregrad":"gering|mittel|hoch"}
  ],
  "rueckrufe": [
    {"datum":"...","betroffene_baujahre":"...","mangel":"...","abhilfe":"...","kba_referenz":<"..."|null>}
  ],
  "quellen": [],
  "motoren": [
    {
      "variante_id": "...",
      "bezeichnung": "...",
      "motorcode": <"..."|null>,
      "kraftstoff": "...",
      "hubraum_ccm": <Zahl|null>,
      "zylinder": <Zahl|null>,
      "leistung_ps": <Zahl|null>,
      "leistung_kw": <Zahl|null>,
      "drehmoment_nm": <Zahl|null>,
      "getriebe": ["..."],
      "antrieb": "Heck|Front|Allrad",
      "beschleunigung_0_100": <Zahl|null>,
      "vmax_kmh": <Zahl|null>,
      "verbrauch_wltp": <Zahl|null>,
      "verbrauch_real": <Zahl|null>,
      "co2_g_km": <Zahl|null>,
      "neupreis_ca_eur": <Zahl|null>,
      "heck_emblem": <"..."|null>,
      "optische_unterscheidung": <"..."|null>,
      "schwachstellen_motor": [
        {"bauteil":"...","beschreibung":"...","baujahre":<"..."|null>,"kosten_ca":<"..."|null>}
      ],
      "kritische_wartung": [
        {"bauteil":"...","intervall":"...","hinweis":<"..."|null>}
      ]
    }
  ],
  "hinweise": {}
}"""

# Flash-Lite braucht nur minimale Instruktionen — kein großes Schema
_BATCH_SYSTEM = (
    "Antworte NUR mit einem JSON-Array, keine Markdown-Fences, kein Text.\n"
    'Format: [{"marke":"...","modell":"...","generation":"...","baujahr_von":YYYY,"baujahr_bis":YYYY_oder_null}]\n'
    "Nur eigenständige Generationen, keine Facelift-Unterversionen."
)


def _extract_json(text: str) -> dict | list:
    """
    Extrahiert das erste vollständige JSON-Objekt oder -Array aus dem Text.
    Robust gegen Markdown-Fences, Präambel-Text und Nachtext.
    """
    text = text.strip()

    # 1. Markdown-Code-Block bevorzugt (```json ... ```)
    m = re.search(r"```(?:json)?\s*([\[{].*?)\s*```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))

    # 2. Früheste öffnende Klammer ({  oder [) bestimmen — nicht die Reihenfolge in der Schleife
    pos_obj = text.find("{")
    pos_arr = text.find("[")
    if pos_obj == -1 and pos_arr == -1:
        return json.loads(text)  # letzter Ausweg

    if pos_arr == -1 or (pos_obj != -1 and pos_obj < pos_arr):
        candidates = [("{", "}")]
    elif pos_obj == -1 or pos_arr < pos_obj:
        candidates = [("[", "]")]
    else:
        candidates = [("{", "}")]  # gleichauf: Dict bevorzugen

    for start_char, end_char in candidates:
        idx = text.find(start_char)
        depth = 0
        in_str = False
        escape = False
        for i, ch in enumerate(text[idx:], start=idx):
            if escape:
                escape = False
                continue
            if ch == "\\" and in_str:
                escape = True
                continue
            if ch == '"' and not escape:
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == start_char:
                depth += 1
            elif ch == end_char:
                depth -= 1
                if depth == 0:
                    return json.loads(text[idx : i + 1])

    return json.loads(text)


def _entwurf_cfg() -> genai_types.GenerateContentConfig:
    """Konfiguration für Entwurf-Calls: kein Thinking, Token-Cap."""
    return genai_types.GenerateContentConfig(
        system_instruction=_SCHEMA_SYSTEM,
        temperature=0.1,
        # E46 brauchte 7193 Output + 995 Thinking = 8188 → 8192er-Limit gerissen.
        # 16384 gibt genug Puffer für große Baureihen (viele Motoren/Schwachstellen).
        max_output_tokens=16384,
        thinking_config=genai_types.ThinkingConfig(thinking_budget=1024),
    )


# ------------------------------------------------------------------ #
#  Entwurf (non-streaming, für direkte JSON-Rückgabe)                 #
# ------------------------------------------------------------------ #

async def entwurf_erstellen(marke: str, modell: str, generation: str) -> dict:
    client = _get_client()
    heute  = date.today().strftime("%Y-%m")
    prompt = _entwurf_prompt(marke, modell, generation, heute)
    cfg    = _entwurf_cfg()

    last_err: Exception | None = None
    for versuch in range(1, 3):   # max. 2 Versuche
        response = with_retry_sync(lambda: client.models.generate_content(
            model=LLM_MODEL,
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            config=cfg,
        ))
        text = response.text or ""
        if not text.strip():
            last_err = ValueError("Leere Antwort vom Modell")
            log.warning("Entwurf Versuch %d/%d: leere Antwort – wiederhole.", versuch, 2)
            continue
        try:
            return _finalize(text, marke, modell, generation, heute)
        except json.JSONDecodeError as e:
            last_err = e
            log.warning("Entwurf Versuch %d/%d: JSON-Fehler (%s) – wiederhole.", versuch, 2, e)

    raise ValueError(
        f"Antwort unvollständig, bitte erneut versuchen. "
        f"(Details: {last_err})"
    ) from last_err


# ------------------------------------------------------------------ #
#  Entwurf Streaming (SSE — gibt rohen JSON-Text häppchenweise aus)   #
# ------------------------------------------------------------------ #

async def entwurf_stream(
    marke: str, modell: str, generation: str
) -> AsyncGenerator[str, None]:
    """
    Streamt den rohen JSON-Text als Fragmente.
    Letztes Event: {"done": true, "json": <geparstes Dict>}
    Bei Fehler: {"error": "..."}
    """
    client  = _get_client()
    heute   = date.today().strftime("%Y-%m")
    prompt  = _entwurf_prompt(marke, modell, generation, heute)
    cfg     = _entwurf_cfg()
    buffer  = []

    try:
        stream = with_retry_sync(lambda: client.models.generate_content_stream(
            model=LLM_MODEL,
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            config=cfg,
        ))

        for chunk in stream:
            if chunk.text:
                buffer.append(chunk.text)
                yield json.dumps({"delta": chunk.text}, ensure_ascii=False)

        full_text = "".join(buffer)
        if not full_text.strip():
            yield json.dumps({"error": "Antwort unvollständig, bitte erneut versuchen. (Leere Antwort)"})
            return
        try:
            data = _finalize(full_text, marke, modell, generation, heute)
            yield json.dumps({"done": True, "json": data}, ensure_ascii=False)
        except json.JSONDecodeError as e:
            yield json.dumps({"error": f"Antwort unvollständig, bitte erneut versuchen. (JSON: {e})"})

    except RateLimitExhausted as e:
        yield json.dumps({"error": str(e)})


# ------------------------------------------------------------------ #
#  Generationen-Liste (Flash-Lite, schnell)                           #
# ------------------------------------------------------------------ #

async def generationen_auflisten(anfrage: str) -> list[dict]:
    """
    Nutzt gemini-2.0-flash-lite — kein Thinking-Overhead, < 2 s für eine Liste.
    """
    client = _get_client()

    cfg = genai_types.GenerateContentConfig(
        system_instruction=_BATCH_SYSTEM,
        temperature=0.0,       # maximale Determiniertheit für Faktenlisten
        max_output_tokens=400, # eine Liste mit 15 Generationen braucht ~200 Token
    )
    response = with_retry_sync(lambda: client.models.generate_content(
        model=FAST_LLM_MODEL,
        contents=[{"role": "user", "parts": [{"text": anfrage}]}],
        config=cfg,
    ))

    return _extract_json(response.text)


# ------------------------------------------------------------------ #
#  Hilfsfunktionen                                                    #
# ------------------------------------------------------------------ #

def _entwurf_prompt(marke: str, modell: str, generation: str, heute: str) -> str:
    return (
        f"Erstelle ein vollständiges Datenprofil für: {marke} {modell} {generation}.\n"
        f"Heutiges Datum (letzte_aktualisierung): {heute}.\n"
        "Unbekannte Felder → null. Unsichere Zahlen in 'hinweise' markieren."
    )


def _finalize(text: str, marke: str, modell: str, generation: str, heute: str) -> dict:
    data = _extract_json(text)
    data.setdefault("marke", marke)
    data.setdefault("modell", modell)
    data.setdefault("generation", generation)
    data.setdefault("hinweise", {})
    data.setdefault("letzte_aktualisierung", heute)
    return data
