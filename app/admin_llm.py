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
from google.genai.errors import ServerError

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
6. Standard-Fahrzeugdaten — häufig gefragte Werte, ausfüllen wenn bekannt (sonst null):
   - "tankgroesse_liter": Kraftstofftank in Litern (bei Elektro: null)
   - "kofferraum_liter": Kofferraumvolumen in Litern (Basis-Konfiguration)
   - "batteriekapazitaet_kwh": Batteriekapazität in kWh — NUR bei Elektro/Plug-in-Hybrid, sonst null
   - "anhaengelast_gebremst_kg" / "anhaengelast_ungebremst_kg": zulässige Anhängelast in kg
   - "abgasnorm": z.B. "Euro 6d-ISC-FCM" (bei Elektro: null)
   - "felgengroesse_serie": Serienbereifung + optionale Größen, z.B. "17 Zoll (Serie), bis 20 Zoll optional"

Ausgabe-Schema (Array):
[{"variante_id":"...","bezeichnung":"...","motorcode":null,"kraftstoff":"...",
  "hubraum_ccm":null,"zylinder":null,"leistung_ps":null,"leistung_kw":null,
  "drehmoment_nm":null,"getriebe":["..."],"antrieb":"...","beschleunigung_0_100":null,
  "vmax_kmh":null,"verbrauch_wltp":null,"verbrauch_real":null,"co2_g_km":null,
  "neupreis_ca_eur":null,"heck_emblem":null,"optische_unterscheidung":null,
  "tankgroesse_liter":null,"kofferraum_liter":null,"batteriekapazitaet_kwh":null,
  "anhaengelast_gebremst_kg":null,"anhaengelast_ungebremst_kg":null,
  "abgasnorm":null,"felgengroesse_serie":null,
  "schwachstellen_motor":[{"bauteil":"...","beschreibung":"...","baujahre":null,"kosten_ca":null}],
  "kritische_wartung":[{"bauteil":"...","intervall":"...","hinweis":null}]}]"""


def _resp_diagnose(resp) -> str:
    """
    Extrahiert diagnostisch relevante Felder aus einem GenerateContentResponse
    und gibt einen kompakten Log-String zurück, z.B.:

      "text=None | finish=SAFETY | safety[DANGEROUS_CONTENT]=HIGH+BLOCKED | block=SAFETY"
      "text='' | finish=MAX_TOKENS | tokens_out=800"
      "text=ok(len=312) | finish=STOP"

    Zweck: überall wo resp.text leer/None ist, den WAHREN Grund loggen
    statt nur "leere Antwort". Deckt Safety-Filter, MAX_TOKENS-Abschnitte
    und fehlende Candidates ab.
    """
    parts: list[str] = []

    # ── resp.text ─────────────────────────────────────────────────────────
    raw = getattr(resp, "text", "<kein .text>")
    if raw is None:
        parts.append("text=None")          # Safety-Block oder kein Content-Part
    elif raw == "":
        parts.append("text=''")            # leerer String
    else:
        parts.append(f"text=ok(len={len(raw)})")

    # ── candidates[0] ────────────────────────────────────────────────────
    cands = getattr(resp, "candidates", None) or []
    if not cands:
        parts.append("candidates=LEER")
    else:
        c = cands[0]
        fr = getattr(c, "finish_reason", None)
        parts.append(f"finish={fr!r}" if fr is not None else "finish=?")

        for r in getattr(c, "safety_ratings", []):
            prob = str(getattr(r, "probability", "?")).replace("HarmProbability.", "")
            blocked = getattr(r, "blocked", False)
            # Nur auffällige Werte loggen (NEGLIGIBLE/LOW/UNSPECIFIED überspringen)
            if prob not in ("NEGLIGIBLE", "LOW", "UNSPECIFIED"):
                cat = str(getattr(r, "category", "?")).replace("HarmCategory.", "")
                parts.append(f"safety[{cat}]={prob}" + ("+BLOCKED" if blocked else ""))

    # ── prompt_feedback ──────────────────────────────────────────────────
    pf = getattr(resp, "prompt_feedback", None)
    if pf:
        br = getattr(pf, "block_reason", None)
        if br:
            parts.append(f"block_reason={br!r}")

    # ── Token-Counts (MAX_TOKENS-Diagnose) ────────────────────────────────
    um = getattr(resp, "usage_metadata", None)
    if um:
        out = getattr(um, "candidates_token_count", None)
        if out is not None:
            parts.append(f"tokens_out={out}")

    return " | ".join(parts)


def _cfg_with(system: str) -> genai_types.GenerateContentConfig:
    """Erstellt eine Call-Config mit spezifischem System-Prompt (Haupt-Modell)."""
    import dataclasses
    return genai_types.GenerateContentConfig(
        system_instruction=system,
        temperature=_CALL_CFG.temperature,
        max_output_tokens=_CALL_CFG.max_output_tokens,
        thinking_config=_CALL_CFG.thinking_config,
    )


def _cfg_fallback(system: str) -> genai_types.GenerateContentConfig:
    """Config für Fallback-Modell (flash-lite) — Thinking deaktiviert, kein Budget-Fraß.
    max_output_tokens=16384: BMW 1er F20 generiert 14000+ Zeichen (~5000+ Tokens) für alle Varianten."""
    return genai_types.GenerateContentConfig(
        system_instruction=system,
        temperature=0.1,
        max_output_tokens=16384,
        thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
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
        log.warning(
            "_call_sync [%s] Versuch %d/2: leere/None Antwort. %s",
            LLM_MODEL, versuch, _resp_diagnose(resp),
        )
    raise ValueError(f"Leere Antwort nach 2 Versuchen ({LLM_MODEL}).")


def _call_stream_collect(
    client,
    prompt: str,
    system: str,
    model: str,
    max_output_tokens: int | None = None,
) -> str:
    """
    Streaming-Generate-Call — sammelt vollständigen Text aus dem Stream.

    Streaming ist bei Gemini-Überlast robuster als non-streaming:
    der erste Token kommt schneller durch, bevor der 503 greift.
    Bis zu 2 Versuche bei leerem oder nicht-JSON-haltigem Ergebnis.

    max_output_tokens: überschreibt _CALL_CFG.max_output_tokens wenn angegeben.
    Notwendig für den Motoren-Call: E30 & Co brauchen >14.000 Zeichen (~5.000 Token),
    der Standard-Wert von 6144 führt zu finish_reason=MAX_TOKENS und abgeschnittenem JSON.
    """
    if model == LLM_MODEL and max_output_tokens is not None:
        cfg = genai_types.GenerateContentConfig(
            system_instruction=system,
            temperature=_CALL_CFG.temperature,
            max_output_tokens=max_output_tokens,
            thinking_config=_CALL_CFG.thinking_config,
        )
    else:
        cfg = _cfg_with(system) if model == LLM_MODEL else _cfg_fallback(system)
    contents = [{"role": "user", "parts": [{"text": prompt}]}]

    for versuch in range(1, 3):
        buffer: list[str] = []
        last_chunk = None   # letzter Chunk → enthält finish_reason / usage_metadata
        # Default-Argument-Trick: model/cfg/contents werden zum Lambda-Erstellungszeitpunkt
        # gebunden, nicht erst bei Aufruf durch with_retry_sync.
        stream = with_retry_sync(
            lambda m=model, c=cfg, ct=contents: client.models.generate_content_stream(
                model=m, contents=ct, config=c,
            )
        )
        for chunk in stream:
            last_chunk = chunk
            if chunk.text:
                buffer.append(chunk.text)
        text = "".join(buffer)
        stripped = text.strip()
        if not stripped:
            diag = _resp_diagnose(last_chunk) if last_chunk else "kein Chunk empfangen"
            log.warning(
                "_call_stream_collect [%s] Versuch %d/2: leere/None Antwort. %s",
                model, versuch, diag,
            )
            continue
        if "{" not in stripped and "[" not in stripped:
            diag = _resp_diagnose(last_chunk) if last_chunk else ""
            log.warning(
                "_call_stream_collect [%s] Versuch %d/2: kein JSON in Antwort: %r  %s",
                model, versuch, stripped[:120], diag,
            )
            continue
        # Vollständigkeitsprüfung: parsbares JSON → kein abgeschnittenes Chunk zurückgeben
        try:
            _extract_json(stripped)
            return text  # valides, vollständiges JSON
        except Exception as json_err:
            diag = _resp_diagnose(last_chunk) if last_chunk else ""
            log.warning(
                "_call_stream_collect [%s] Versuch %d/2: JSON-Fehler (abgeschnitten?): %s | len=%d  %s",
                model, versuch, json_err, len(stripped), diag,
            )

    raise ValueError(f"Kein valides JSON nach 2 Versuchen ({model}).")


def _call_sync_fallback(client, prompt: str, system: str) -> str:
    """
    Non-streaming Fallback-Call mit FAST_LLM_MODEL (flash-lite).

    Non-streaming ist für flash-lite zuverlässiger: das Modell ist weniger
    ausgelastet als flash, und die Antwort kann in einem Block abgerufen werden.
    thinking_budget=0 verhindert Token-Fraß.
    Bis zu 2 Versuche bei leerem oder nicht-JSON-haltigem Ergebnis.
    """
    cfg = _cfg_fallback(system)
    contents = [{"role": "user", "parts": [{"text": prompt}]}]
    for versuch in range(1, 3):
        resp = with_retry_sync(lambda: client.models.generate_content(
            model=FAST_LLM_MODEL, contents=contents, config=cfg,
        ))
        text = resp.text or ""
        stripped = text.strip()
        if not stripped:
            log.warning(
                "_call_sync_fallback [%s] Versuch %d/2: leere/None Antwort. %s",
                FAST_LLM_MODEL, versuch, _resp_diagnose(resp),
            )
            continue
        if "{" not in stripped and "[" not in stripped:
            log.warning(
                "_call_sync_fallback [%s] Versuch %d/2: kein JSON: %r  %s",
                FAST_LLM_MODEL, versuch, stripped[:120], _resp_diagnose(resp),
            )
            continue
        # Vollständigkeitsprüfung: parsbares JSON oder abgeschnitten?
        try:
            _extract_json(stripped)
            return text  # valides, vollständiges JSON
        except Exception as json_err:
            log.warning(
                "_call_sync_fallback [%s] Versuch %d/2: JSON-Fehler: %s  %s",
                FAST_LLM_MODEL, versuch, json_err, _resp_diagnose(resp),
            )
    raise ValueError(f"Kein valides JSON nach 2 Versuchen ({FAST_LLM_MODEL}).")


def _call_motoren(client, prompt: str) -> str:
    """
    Motoren-Call mit automatischem Fallback auf FAST_LLM_MODEL.

    Strategie:
      1. Primär: LLM_MODEL (gemini-3.7-flash) via Streaming + with_retry_sync (5×15s)
         Streaming übersteht 503-Überlast besser als non-streaming.
      2. Fallback bei 503 ODER leerem/ungültigem JSON:
         FAST_LLM_MODEL (gemini-2.5-flash-lite) non-streaming, thinking_budget=0.
         Flash-Lite ist weniger ausgelastet → zuverlässigerer Fallback.
    """
    # Primär: Flash via Streaming
    primary_exc: Exception | None = None
    try:
        return _call_stream_collect(client, prompt, _SYS_MOTOREN, LLM_MODEL,
                                    max_output_tokens=16384)
    except ServerError as exc:
        if exc.code != 503:
            raise  # andere 5xx (401, 400 …) sofort weitergeben
        primary_exc = exc
        log.warning(
            "Motoren-Call %s nach allen Retries 503 — Fallback auf %s.",
            LLM_MODEL, FAST_LLM_MODEL,
        )
    except ValueError as exc:
        # Leere Antwort oder kein JSON vom primären Modell → auch Fallback
        primary_exc = exc
        log.warning(
            "Motoren-Call %s kein valides JSON (%s) — Fallback auf %s.",
            LLM_MODEL, exc, FAST_LLM_MODEL,
        )

    # Fallback: Flash-Lite non-streaming (thinking_budget=0 → kein Token-Fraß)
    log.info("Motoren-Fallback: %s (non-streaming)", FAST_LLM_MODEL)
    return _call_sync_fallback(client, prompt, _SYS_MOTOREN)


# ------------------------------------------------------------------ #
#  Entwurf — zwei fokussierte Calls, dann zusammenführen             #
# ------------------------------------------------------------------ #

async def entwurf_erstellen(marke: str, modell: str, generation: str) -> dict:
    """
    Zwei separate, fokussierte LLM-Calls:
      Call A — Ebene-1-Daten (ohne Motoren): non-streaming
      Call B — Motorvarianten-Array:          Streaming + Fallback-Modell bei 503

    Jeder Call ist kleiner, unabhängig retry-fähig und deutlich unter
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

    # Call A — Ebene 1 (non-streaming, 2 Versuche, dann flash-lite Fallback)
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

    # Fallback: flash-lite wenn primäres Modell nach 2 Versuchen scheitert
    if ebene1 is None:
        log.warning(
            "Ebene-1-Call %s erschöpft — Fallback auf %s. (Fehler: %s)",
            LLM_MODEL, FAST_LLM_MODEL, last_err,
        )
        try:
            text = _call_sync_fallback(client, prompt_e1, _SYS_EBENE1)
            ebene1 = _extract_json(text)
        except Exception as e:
            last_err = e
            log.warning("Ebene-1-Call Fallback fehlgeschlagen: %s", e)

    if ebene1 is None:
        raise ValueError(f"Antwort unvollständig, bitte erneut versuchen. (Ebene 1: {last_err})")

    # Call B — Motoren als Streaming + Fallback-Modell bei 503
    motoren_err: str | None = None
    motoren: list = []
    try:
        text = _call_motoren(client, prompt_mo)
        result = _extract_json(text)
        if isinstance(result, list):
            motoren = result
        else:
            raise ValueError(f"Motorenliste ist kein Array: {type(result)}")
    except Exception as e:
        last_err = e
        motoren_err = str(e)
        log.warning("Motoren-Call fehlgeschlagen (inkl. Fallback): %s", e)

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
      Phase 2: Motoren als Streaming + Fallback-Modell bei 503
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

    # Fallback: flash-lite wenn primäres Modell für Phase 1 gescheitert ist
    if ebene1 is None:
        log.warning(
            "Stream E1 %s erschöpft — Fallback auf %s (non-streaming).", LLM_MODEL, FAST_LLM_MODEL
        )
        yield json.dumps({"status": "fallback", "modell": FAST_LLM_MODEL})
        try:
            text = _call_sync_fallback(client, prompt_e1, _SYS_EBENE1)
            ebene1 = _extract_json(text)
        except Exception as e:
            log.warning("Stream E1 Fallback fehlgeschlagen: %s", e)
            yield json.dumps({"error": f"Antwort unvollständig, bitte erneut versuchen. ({e})"})
            return

    # ---- Phase 2: Motoren — Streaming + Fallback-Modell bei 503 ----
    yield json.dumps({"status": "motoren"})
    motoren: list = []
    motoren_err: str | None = None

    try:
        text = _call_motoren(client, prompt_mo)
        result = _extract_json(text)
        if isinstance(result, list):
            motoren = result
        else:
            raise ValueError(f"Kein Array: {type(result)}")
    except Exception as e:
        motoren_err = str(e)
        log.warning("Stream Motoren-Call fehlgeschlagen (inkl. Fallback): %s", e)

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
#  Generationen-Liste (Flash-Lite primär, Flash-Fallback)            #
# ------------------------------------------------------------------ #

async def generationen_auflisten(anfrage: str) -> list[dict]:
    """
    Listet Generationen für einen Batch-Prompt auf.

    Strategie (analog zu _call_motoren):
      1. Primär:  FAST_LLM_MODEL (flash-lite), thinking_budget=0 → schnell, < 3 s
      2. Fallback: LLM_MODEL (flash), thinking_budget=0, mehr Output-Tokens
         — greift automatisch wenn flash-lite leer/nicht-JSON/503 liefert

    JSON-Validierung: Antwort muss '[' oder '{' enthalten UND parsebar sein.
    """
    client = _get_client()
    contents = [{"role": "user", "parts": [{"text": anfrage}]}]

    def _versuche(model: str, max_output_tokens: int) -> list | None:
        """
        Bis zu 2 Versuche mit einem Modell.
        Gibt die Liste zurück oder None wenn alle Versuche scheitern.
        """
        cfg = genai_types.GenerateContentConfig(
            system_instruction=_BATCH_SYSTEM,
            temperature=0.0,
            max_output_tokens=max_output_tokens,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
        )
        for versuch in range(1, 3):
            try:
                resp = with_retry_sync(
                    lambda m=model, c=cfg: client.models.generate_content(
                        model=m, contents=contents, config=c,
                    )
                )
                text = resp.text or ""
                stripped = text.strip()

                # 1. Leer (text=None oder text="") → wahren Grund loggen
                if not stripped:
                    log.warning(
                        "generationen_auflisten [%s] Versuch %d/2: leere/None Antwort. %s",
                        model, versuch, _resp_diagnose(resp),
                    )
                    continue

                # 2. Kein JSON-Marker → Modell hat Prosa geantwortet
                if "{" not in stripped and "[" not in stripped:
                    log.warning(
                        "generationen_auflisten [%s] Versuch %d/2: kein JSON-Marker: %r  %s",
                        model, versuch, stripped[:100], _resp_diagnose(resp),
                    )
                    continue

                # 3. Parsebar und Liste?
                result = _extract_json(stripped)
                if isinstance(result, list):
                    log.info(
                        "generationen_auflisten [%s] OK: %d Generationen.", model, len(result)
                    )
                    return result
                log.warning(
                    "generationen_auflisten [%s] Versuch %d/2: kein Array (%s).  %s",
                    model, versuch, type(result).__name__, _resp_diagnose(resp),
                )

            except Exception as exc:
                # Fehlertyp explizit loggen (ServerError 503, JSONDecodeError, etc.)
                log.warning(
                    "generationen_auflisten [%s] Versuch %d/2: %s: %s",
                    model, versuch, type(exc).__name__, exc,
                )

        return None  # beide Versuche gescheitert

    # ── Primär: Flash-Lite (schnell) ──────────────────────────────────
    # Budget großzügig bemessen: breite Anfragen wie "alle Renault Modelle"
    # brauchen für ~55 Generationen bereits ~1950 Output-Tokens (empirisch
    # gemessen) — bei knappem Budget bricht die JSON-Antwort mitten im Array
    # ab und wird unparsebar (JSONDecodeError), statt sauber gekürzt zu sein.
    result = _versuche(FAST_LLM_MODEL, max_output_tokens=3000)
    if result is not None:
        return result

    log.warning(
        "generationen_auflisten: %s fehlgeschlagen — Fallback auf %s.",
        FAST_LLM_MODEL, LLM_MODEL,
    )

    # ── Fallback: Flash (mehr Kapazität) ──────────────────────────────
    result = _versuche(LLM_MODEL, max_output_tokens=6000)
    if result is not None:
        return result

    raise ValueError(
        "Generationen-Liste konnte nicht geladen werden — bitte erneut versuchen."
    )


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


# ------------------------------------------------------------------ #
#  Lücken-Entwurf — nur fehlende Felder per Gemini generieren        #
# ------------------------------------------------------------------ #

_SYS_LUECKEN = """Du bist ein präziser Automobil-Datenbankassistent.
Antworte NUR mit einem JSON-Objekt, keine Markdown-Fences, kein Text davor/danach.
Erzeuge exakt die angeforderten Felder — nichts anderes.

PFLICHTREGELN:
1. Nur gesicherte, bekannte Fakten. Bei Unsicherheit → leeres Array oder null, NIEMALS raten.
2. kaufberatung: Deutscher Fließtext als Markdown (~200–350 Wörter). Praktische Hinweise
   für den Gebrauchtkauf — worauf prüfen, typische Mängel, Laufleistungs-Erwartung,
   welche Ausstattung bevorzugen, Preiseinschätzung.
3. schwachstellen_baureihe: Array mit bekannten, dokumentierten Serienfehlern.
   Format: {"bauteil":"...","beschreibung":"...","betroffene_baujahre":"...","schweregrad":"gering|mittel|hoch"}
   Nur echte, belegte Schwachstellen. Wenn keine bekannt → [].
4. rueckrufe: Bekannte KBA-Rückrufe in Deutschland.
   Format: {"datum":"MM/YYYY","betroffene_baujahre":"...","mangel":"...","abhilfe":"...","kba_referenz":null}
   Wenn keine bekannt → [].

Ausgabe-Schema — enthält nur die angeforderten Felder:
{"kaufberatung": null_oder_text, "schwachstellen_baureihe": [], "rueckrufe": []}
Felder die NICHT angefordert wurden: weglassen."""


async def luecken_entwurf_erstellen(
    marke: str,
    modell: str,
    generation: str,
    fehlende_felder: list[str],
    kontext: dict,
) -> dict:
    """
    Generiert via Gemini NUR die fehlenden Felder einer bestehenden Baureihe.

    `fehlende_felder`: Liste der zu generierenden Felder
        ("kaufberatung", "schwachstellen_baureihe", "rueckrufe")
    `kontext`: Vorhandene Baureihen-Daten aus der DB (bauzeitraum, segment, …)
        als Anker — verhindert, dass Gemini falsches Modell generiert.
    """
    client = _get_client()

    # Kontext-Zusammenfassung für den Prompt
    bj_von = kontext.get("bauzeitraum_von") or "?"
    bj_bis = kontext.get("bauzeitraum_bis") or "heute"
    segment = kontext.get("segment") or "?"
    erk = kontext.get("erkennung_generation") or ""

    kontext_block = (
        f"Fahrzeug: {marke} {modell} {generation}\n"
        f"Bauzeitraum: {bj_von}–{bj_bis} | Segment: {segment}\n"
    )
    if erk:
        kontext_block += f"Erkennungsmerkmale: {erk}\n"

    felder_block = ", ".join(fehlende_felder)
    prompt = (
        f"{kontext_block}\n"
        f"Erzeuge folgende fehlende Felder: {felder_block}\n"
        "Nur belegte Fakten. Leeres Array wenn unklar. Kein Erfinden."
    )

    log.info(
        "luecken_entwurf_erstellen: %s %s %s — Felder: %s",
        marke, modell, generation, felder_block,
    )

    # Primär: Haupt-Modell (streaming für Robustheit)
    try:
        text = _call_stream_collect(client, prompt, _SYS_LUECKEN, LLM_MODEL,
                                    max_output_tokens=4096)
        return _extract_json(text)
    except Exception as exc:
        log.warning(
            "luecken_entwurf %s fehlgeschlagen — Fallback auf %s. (%s)",
            LLM_MODEL, FAST_LLM_MODEL, exc,
        )

    # Fallback: Flash-Lite
    text = _call_sync_fallback(client, prompt, _SYS_LUECKEN)
    return _extract_json(text)
