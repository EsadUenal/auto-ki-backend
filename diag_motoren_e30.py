"""
Diagnose: Motoren-Call BMW 3er E30 — Rohzustand, kein Fix.

Zeigt für beide Modelle (flash streaming + flash-lite non-streaming):
  - exakter Prompt
  - resp.text (vollständig, ungekürzt)
  - finish_reason
  - prompt_feedback / block_reason
  - safety_ratings
  - token usage (prompt / output / thinking)
"""
import sys, io, os, traceback
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from google import genai
from google.genai import types as genai_types
from app.config import LLM_MODEL, FAST_LLM_MODEL

SEP = "=" * 70

# ── exakt derselbe System-Prompt wie in admin_llm._SYS_MOTOREN ──────────────
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

# ── exakt derselbe Prompt wie in entwurf_erstellen ───────────────────────────
BID    = "bmw-3er-e30"
PROMPT = (
    f"Liste alle Motorvarianten des BMW 3er E30 auf.\n"
    f"baureihe_id für variante_id-Prefix: {BID}\n"
    "Unbekannte Zahlen → null."
)


def dump_resp_full(resp, label: str):
    print(f"\n{'─'*70}")
    print(f"  {label}")
    print(f"{'─'*70}")

    # ── prompt_feedback ──────────────────────────────────────────────────
    pf = getattr(resp, "prompt_feedback", None)
    if pf is not None:
        br = getattr(pf, "block_reason", None)
        print(f"  prompt_feedback.block_reason : {br!r}")
        for r in getattr(pf, "safety_ratings", []):
            print(f"    pf_safety {r.category!r} prob={r.probability!r}")
    else:
        print("  prompt_feedback              : (kein Attribut / None)")

    # ── candidates ───────────────────────────────────────────────────────
    cands = getattr(resp, "candidates", None) or []
    if not cands:
        print("  candidates                   : LEER — kein Candidate!")
    else:
        c = cands[0]
        print(f"  finish_reason                : {getattr(c, 'finish_reason', '?')!r}")
        for r in (getattr(c, "safety_ratings", None) or []):
            print(f"    safety {r.category!r} prob={r.probability!r} blocked={getattr(r,'blocked','?')!r}")

    # ── resp.text VOLLSTÄNDIG ────────────────────────────────────────────
    raw = getattr(resp, "text", "<kein .text>")
    if raw is None:
        print("  resp.text                    : None  ← GEBLOCKT / KEIN CONTENT")
    elif raw == "":
        print("  resp.text                    : ''  ← LEER")
    else:
        print(f"  resp.text (len={len(raw)}):")
        print("  ┌─ ANFANG ─────────────────────────────────────────────────")
        # Volltext — max 8000 Zeichen (mehr als genug für Motorliste)
        print(raw[:8000])
        if len(raw) > 8000:
            print(f"  ... (GEKÜRZT — Gesamtlänge={len(raw)})")
        print("  └─ ENDE ───────────────────────────────────────────────────")

    # ── token usage ──────────────────────────────────────────────────────
    um = getattr(resp, "usage_metadata", None)
    if um:
        print(f"  usage: prompt={getattr(um,'prompt_token_count','?')}  "
              f"output={getattr(um,'candidates_token_count','?')}  "
              f"thinking={getattr(um,'thoughts_token_count','?')}  "
              f"total={getattr(um,'total_token_count','?')}")


def call_nonstream(client, model: str, max_tokens: int, thinking_budget: int):
    cfg = genai_types.GenerateContentConfig(
        system_instruction=_SYS_MOTOREN,
        temperature=0.1,
        max_output_tokens=max_tokens,
        thinking_config=genai_types.ThinkingConfig(thinking_budget=thinking_budget),
    )
    contents = [{"role": "user", "parts": [{"text": PROMPT}]}]
    print(f"\n  → POST generate_content (non-streaming, max_tokens={max_tokens}, thinking={thinking_budget})")
    resp = client.models.generate_content(model=model, contents=contents, config=cfg)
    dump_resp_full(resp, f"NON-STREAMING {model}")
    return resp


def call_streaming(client, model: str, max_tokens: int, thinking_budget: int):
    cfg = genai_types.GenerateContentConfig(
        system_instruction=_SYS_MOTOREN,
        temperature=0.1,
        max_output_tokens=max_tokens,
        thinking_config=genai_types.ThinkingConfig(thinking_budget=thinking_budget),
    )
    contents = [{"role": "user", "parts": [{"text": PROMPT}]}]
    print(f"\n  → POST generate_content_stream (streaming, max_tokens={max_tokens}, thinking={thinking_budget})")
    chunks = []
    last_chunk = None
    stream = client.models.generate_content_stream(model=model, contents=contents, config=cfg)
    for chunk in stream:
        last_chunk = chunk
        if chunk.text:
            chunks.append(chunk.text)
    text = "".join(chunks)
    print(f"  Stream-Chunks: {len(chunks)}, Gesamtlänge: {len(text)}")
    if last_chunk:
        dump_resp_full(last_chunk, f"STREAMING (letzter Chunk) {model}")
    else:
        print("  KEIN Chunk empfangen.")
    return text


def main():
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        p = __import__("pathlib").Path(".env")
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.startswith("GEMINI_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"\'')
    if not api_key:
        print("FEHLER: GEMINI_API_KEY nicht gesetzt."); sys.exit(1)

    client = genai.Client(api_key=api_key)

    print(SEP)
    print("DIAGNOSE: Motoren-Call BMW 3er E30")
    print(SEP)
    print(f"\nPrompt (User):\n  {PROMPT!r}")
    print(f"\nPrimärmodell  : {LLM_MODEL}")
    print(f"Fallbackmodell: {FAST_LLM_MODEL}")

    # ── 1. Flash Streaming (primärer Call wie in _call_motoren) ──────────
    print(f"\n{SEP}")
    print(f"[1] PRIMÄR: {LLM_MODEL} — STREAMING (wie _call_motoren)")
    print(SEP)
    try:
        call_streaming(client, LLM_MODEL, max_tokens=6144, thinking_budget=512)
    except Exception as e:
        print(f"  EXCEPTION: {type(e).__name__}: {e}")
        traceback.print_exc()

    # ── 2. Flash-Lite non-streaming (Fallback wie in _call_sync_fallback) ─
    print(f"\n{SEP}")
    print(f"[2] FALLBACK: {FAST_LLM_MODEL} — NON-STREAMING, thinking=0, max=16384")
    print(SEP)
    try:
        call_nonstream(client, FAST_LLM_MODEL, max_tokens=16384, thinking_budget=0)
    except Exception as e:
        print(f"  EXCEPTION: {type(e).__name__}: {e}")
        traceback.print_exc()

    # ── 3. Flash non-streaming ohne Thinking (Kontrollfall) ──────────────
    print(f"\n{SEP}")
    print(f"[3] KONTROLL: {LLM_MODEL} — NON-STREAMING, thinking=0, max=16384")
    print(SEP)
    try:
        call_nonstream(client, LLM_MODEL, max_tokens=16384, thinking_budget=0)
    except Exception as e:
        print(f"  EXCEPTION: {type(e).__name__}: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
