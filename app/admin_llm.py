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


# ------------------------------------------------------------------ #
#  Zwei-Call-Konfigurationen                                          #
# ------------------------------------------------------------------ #

_CALL_CFG = genai_types.GenerateContentConfig(
    # Gemeinsame Einstellungen für beide fokussierten Calls.
    # Kein globaler system_instruction — wird per-Call gesetzt.
    temperature=0.1,
    max_output_tokens=6144,      # pro Call max. 6 k Tokens → weit unter Limit
    thinking_config=genai_types.ThinkingConfig(thinking_budget=512),
)

_SYS_EBENE1 = """Du bist ein präziser Automobil-Datenbankassistent.
Antworte NUR mit einem JSON-Objekt, keine Markdown-Fences, kein Text davor/danach.

PFLICHTREGELN:
1. Unbekannte Felder → null. Niemals raten.
2. Unsichere Zahlen (Preise, CO2): Zahl eintragen + Feld in "hinweise" vermerken.
3. id = "{marke}-{modell}-{generation}" in Kleinbuchstaben/Bindestrichen.
4. "erkennung_generation": mind. 2–3 konkrete sichtbare Merkmale (Scheinwerfer, Niere,
   Karosserie, Unterschied zum Vorgänger). NIEMALS nur ein Baucode.
5. "facelift_merkmale": konkreter Fließtext, null nur wenn kein Facelift stattfand.
6. "kraftstoff": Benzin|Diesel|Elektro|Plug-in-Hybrid|Mild-Hybrid
7. "schweregrad": gering|mittel|hoch
8. "typ" (Ausstattungslinie): Basis|Ausstattungslinie|M Performance|Echtes M-Modell

Ausgabe-Schema (OHNE motoren-Feld):
{"id":"...","marke":"...","modell":"...","generation":"...",
 "bauzeitraum_von":ZAHL_ODER_NULL,"bauzeitraum_bis":ZAHL_ODER_NULL,
 "karosserie":["..."],"segment":"...","vorgaenger":"..._oder_null",
 "erkennung_generation":"PFLICHT_FLIESSTEXT","facelift_merkmale":"..._oder_null",
 "adac_pannenkennziffer":null,"tuev_maengelquote":null,"dekra_urteil":null,
 "euro_ncap_sterne":ZAHL_ODER_NULL,"euro_ncap_jahr":ZAHL_ODER_NULL,
 "wartung_oel_km":ZAHL_ODER_NULL,"wartung_hu_intervall":"..._oder_null",
 "kaufberatung":null,"letzte_aktualisierung":"YYYY-MM",
 "ausstattungslinien":[{"name":"...","typ":"...","optische_merkmale":"...","abgrenzung":null}],
 "schwachstellen_baureihe":[{"bauteil":"...","beschreibung":"...","betroffene_baujahre":"...","schweregrad":"..."}],
 "rueckrufe":[{"datum":"...","betroffene_baujahre":"...","mangel":"...","abhilfe":"...","kba_referenz":null}],
 "quellen":[],"hinweise":{}}"""

_SYS_MOTOREN = """Du bist ein präziser Automobil-Datenbankassistent.
Antworte NUR mit einem JSON-Array von Motorvarianten, keine Markdown-Fences, kein Text davor/danach.

PFLICHTREGELN:
1. Unbekannte Zahlen → null. Nur sichere Werte eintragen.
2. variante_id = "{baureihe_id}-{bezeichnung-slug}" in Kleinbuchstaben/Bindestrichen.
3. "kraftstoff": Benzin|Diesel|Elektro|Plug-in-Hybrid|Mild-Hybrid
4. "antrieb": Heck|Front|Allrad
5. "optische_unterscheidung": sichtbare Unterschiede zur Basis (Endrohre, Embleme, Stoßstänger).
   Kein null wenn Unterschiede existieren, niemals nur ein Baucode.

Ausgabe-Schema (Array):
[{"variante_id":"...","bezeichnung":"...","motorcode":null,"kraftstoff":"...",
  "hubraum_ccm":null,"zylinder":null,"leistung_ps":null,"leistung_kw":null,
  "drehmoment_nm":null,"getriebe":["..."],"antrieb":"...","beschleunigung_0_100":null,
  "vmax_kmh":null,"verbrauch_wltp":null,"verbrauch_real":null,"co2_g_km":null,
  "neupreis_ca_eur":null,"heck_emblem":null,"optische_unterscheidung":null,
  "schwachstellen_motor":[{"bauteil":"...","beschreibung":"...","baujahre":null,"kosten_ca":null}],
  "kritische_wartung":[{"bauteil":"...","intervall":"...","hinweis":null}]}]"""


def _cfg_with(system: str) -> genai_types.GenerateContentConfig:
    """Erstellt eine Call-Config mit spezifischem System-Prompt."""
    import dataclasses
    return genai_types.GenerateContentConfig(
        system_instruction=system,
        temperature=_CALL_CFG.temperature,
        max_output_tokens=_CALL_CFG.max_output_tokens,
        thinking_config=_CALL_CFG.thinking_config,
    )


def _call_sync(client, prompt: str, system: str) -> str:
    """Führt einen einzelnen Generate-Call aus, bis zu 2 Versuche bei leerem Text."""
    cfg = _cfg_with(system)
    contents = [{"role": "user", "parts": [{"text": prompt}]}]
    for versuch in range(1, 3):
        resp = with_retry_sync(lambda: client.models.generate_content(
            model=LLM_MODEL, contents=contents, config=cfg,
        ))
        text = resp.text or ""
        if text.strip():
            return text
        log.warning("_call_sync Versuch %d/2: leere Antwort.", versuch)
    raise ValueError("Leere Antwort nach 2 Versuchen.")


# ------------------------------------------------------------------ #
#  Entwurf — zwei fokussierte Calls, dann zusammenführen             #
# ------------------------------------------------------------------ #

async def entwurf_erstellen(marke: str, modell: str, generation: str) -> dict:
    """
    Zwei separate, fokussierte LLM-Calls:
      Call A — Ebene-1-Daten (ohne Motoren): ~1500–2500 Token Output
      Call B — Motorvarianten-Array:          ~1500–3000 Token Output

    Jeder Call ist kleiner, unabhängiger Retry-fähig und deutlich unter
    dem 6144-Token-Limit. Zusammen zuverlässiger als ein großer Monolith-Call.
    """
    client = _get_client()
    heute  = date.today().strftime("%Y-%m")
    bid    = f"{marke}-{modell}-{generation}".lower().replace(" ", "-")

    prompt_e1 = (
        f"Erstelle Ebene-1-Daten (OHNE Motoren) für: {marke} {modell} {generation}.\n"
        f"id={bid}, letzte_aktualisierung={heute}.\n"
        "Unbekannte Felder → null. Unsichere Zahlen in 'hinweise' vermerken."
    )
    prompt_mo = (
        f"Liste alle Motorvarianten des {marke} {modell} {generation} auf.\n"
        f"baureihe_id für variante_id-Prefix: {bid}\n"
        "Unbekannte Zahlen → null."
    )

    # Call A — Ebene 1
    last_err: Exception | None = None
    ebene1: dict | None = None
    for _ in range(2):
        try:
            text = _call_sync(client, prompt_e1, _SYS_EBENE1)
            ebene1 = _extract_json(text)
            break
        except Exception as e:
            last_err = e
            log.warning("Ebene-1-Call fehlgeschlagen: %s – wiederhole.", e)

    if ebene1 is None:
        raise ValueError(f"Antwort unvollständig, bitte erneut versuchen. (Ebene 1: {last_err})")

    # Call B — Motoren (Fehler werden NICHT still geschluckt)
    motoren_err: str | None = None
    motoren: list = []
    for versuch in range(1, 3):
        try:
            text = _call_sync(client, prompt_mo, _SYS_MOTOREN)
            result = _extract_json(text)
            if isinstance(result, list):
                motoren = result
                motoren_err = None
                break
            else:
                raise ValueError(f"Motorenliste ist kein Array: {type(result)}")
        except Exception as e:
            last_err = e
            motoren_err = str(e)
            log.warning("Motoren-Call Versuch %d/2 fehlgeschlagen: %s", versuch, e)

    # Zusammenführen — Motorenfehler als eigenes Feld mitgeben (nicht still verwerfen)
    ebene1["motoren"] = motoren
    if motoren_err and not motoren:
        ebene1["_motorenfehler"] = (
            "Motorvarianten konnten nicht geladen werden, bitte Entwurf neu versuchen. "
            f"(Details: {motoren_err})"
        )
    ebene1.setdefault("marke", marke)
    ebene1.setdefault("modell", modell)
    ebene1.setdefault("generation", generation)
    ebene1.setdefault("hinweise", {})
    ebene1.setdefault("letzte_aktualisierung", heute)
    return ebene1


# ------------------------------------------------------------------ #
#  Entwurf Streaming — streamt Ebene 1, dann Motoren, dann done      #
# ------------------------------------------------------------------ #

async def entwurf_stream(
    marke: str, modell: str, generation: str
) -> AsyncGenerator[str, None]:
    """
    Zwei-Phasen-Stream:
      Phase 1: Ebene-1-Daten streamen (sofortiges Feedback)
      Phase 2: Motoren non-streaming (einfacher, robuster)
      Letztes Event: {"done": true, "json": <zusammengeführtes Dict>}
    """
    client = _get_client()
    heute  = date.today().strftime("%Y-%m")
    bid    = f"{marke}-{modell}-{generation}".lower().replace(" ", "-")

    prompt_e1 = (
        f"Erstelle Ebene-1-Daten (OHNE Motoren) für: {marke} {modell} {generation}.\n"
        f"id={bid}, letzte_aktualisierung={heute}.\n"
        "Unbekannte Felder → null. Unsichere Zahlen in 'hinweise' vermerken."
    )
    prompt_mo = (
        f"Liste alle Motorvarianten des {marke} {modell} {generation} auf.\n"
        f"baureihe_id für variante_id-Prefix: {bid}\n"
        "Unbekannte Zahlen → null."
    )
    cfg_e1 = _cfg_with(_SYS_EBENE1)
    cfg_mo = _cfg_with(_SYS_MOTOREN)
    contents_e1 = [{"role": "user", "parts": [{"text": prompt_e1}]}]

    # ---- Phase 1: Ebene-1 streamen ----
    buffer: list[str] = []
    ebene1: dict | None = None

    for versuch in range(1, 3):
        buffer = []
        try:
            stream = with_retry_sync(lambda: client.models.generate_content_stream(
                model=LLM_MODEL, contents=contents_e1, config=cfg_e1,
            ))
            for chunk in stream:
                if chunk.text:
                    buffer.append(chunk.text)
                    if versuch == 1:
                        yield json.dumps({"delta": chunk.text}, ensure_ascii=False)

            full = "".join(buffer)
            if not full.strip():
                log.warning("Stream E1 Versuch %d/2: leer.", versuch)
                yield json.dumps({"status": "retry", "versuch": versuch})
                continue

            ebene1 = _extract_json(full)
            break

        except RateLimitExhausted as e:
            yield json.dumps({"error": str(e)})
            return
        except json.JSONDecodeError as e:
            log.warning("Stream E1 Versuch %d/2: JSON-Fehler – %s", versuch, e)
            yield json.dumps({"status": "retry", "versuch": versuch})
        except Exception as e:
            log.warning("Stream E1 Versuch %d/2: Fehler – %s", versuch, e)
            yield json.dumps({"status": "retry", "versuch": versuch})

    if ebene1 is None:
        yield json.dumps({"error": "Antwort unvollständig, bitte erneut versuchen."})
        return

    # ---- Phase 2: Motoren (non-streaming, Fehler werden sichtbar) ----
    yield json.dumps({"status": "motoren"})
    motoren: list = []
    motoren_err: str | None = None

    for versuch in range(1, 3):
        try:
            text = _call_sync(client, prompt_mo, cfg_mo.system_instruction)
            result = _extract_json(text)
            if isinstance(result, list):
                motoren = result
                motoren_err = None
                break
            else:
                raise ValueError(f"Kein Array: {type(result)}")
        except Exception as e:
            motoren_err = str(e)
            log.warning("Stream Motoren-Call Versuch %d/2: %s", versuch, e)

    # ---- Zusammenführen und done senden ----
    ebene1["motoren"] = motoren
    ebene1.setdefault("marke", marke)
    ebene1.setdefault("modell", modell)
    ebene1.setdefault("generation", generation)
    ebene1.setdefault("hinweise", {})
    ebene1.setdefault("letzte_aktualisierung", heute)

    if motoren_err and not motoren:
        # Motorenfehler sichtbar machen — done wird trotzdem gesendet, aber
        # mit _motorenfehler-Flag damit admin.html Speichern blockieren kann
        ebene1["_motorenfehler"] = (
            "Motorvarianten konnten nicht geladen werden, bitte Entwurf neu versuchen."
        )

    yield json.dumps({"done": True, "json": ebene1}, ensure_ascii=False)


# ------------------------------------------------------------------ #
#  Generationen-Liste (Flash-Lite, schnell)                           #
# ------------------------------------------------------------------ #

async def generationen_auflisten(anfrage: str) -> list[dict]:
    """
    Nutzt FAST_LLM_MODEL (gemini-2.5-flash-lite) — kein Thinking-Overhead, < 3 s.
    Retry-Loop: bis zu 2 Versuche bei leerer oder nicht-parsebarer Antwort.
    """
    client = _get_client()

    cfg = genai_types.GenerateContentConfig(
        system_instruction=_BATCH_SYSTEM,
        temperature=0.0,
        max_output_tokens=800,   # 400 → 800: Puffer falls Thinking Tokens abgezogen werden
        # Thinking explizit deaktivieren — sonst frisst es das Token-Budget und
        # hinterlässt keinen Platz für die eigentliche Ausgabe → leerer resp.text
        thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
    )
    contents = [{"role": "user", "parts": [{"text": anfrage}]}]

    last_err: Exception | None = None
    for versuch in range(1, 3):
        response = with_retry_sync(lambda: client.models.generate_content(
            model=FAST_LLM_MODEL, contents=contents, config=cfg,
        ))
        text = response.text or ""
        if not text.strip():
            last_err = ValueError("Leere Antwort")
            log.warning("generationen_auflisten Versuch %d/2: leere Antwort.", versuch)
            continue
        try:
            return _extract_json(text)
        except Exception as e:
            last_err = e
            log.warning("generationen_auflisten Versuch %d/2: Parse-Fehler %s.", versuch, e)

    raise ValueError(
        f"Generationen-Liste konnte nicht geladen werden, bitte erneut versuchen. "
        f"(Details: {last_err})"
    ) from last_err


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
