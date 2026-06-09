"""
Diagnose: generationen_auflisten — Safety-Filter / leere Antworten identifizieren.

Ruft für jeden Testfall BEIDE Modelle direkt auf (kein Wrapper, kein resp.text or "").
Loggt vollständig:
  - Exakter Prompt (System + User)
  - resp.prompt_feedback (inkl. block_reason)
  - resp.candidates[0].finish_reason
  - resp.candidates[0].safety_ratings (alle Kategorien)
  - resp.text (roh, ungekürzt)
  - resp.usage_metadata (Token-Counts)

Fix NICHTS — nur Diagnose.
"""
import sys
import io
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

os.environ.setdefault("GEMINI_API_KEY", __import__("dotenv").dotenv_values().get("GEMINI_API_KEY", ""))

from google import genai
from google.genai import types as genai_types

from app.config import FAST_LLM_MODEL, LLM_MODEL

# Exakt derselbe System-Prompt wie in admin_llm.py
_BATCH_SYSTEM = (
    "Antworte NUR mit einem JSON-Array, keine Markdown-Fences, kein Text.\n"
    'Format: [{"marke":"...","modell":"...","generation":"...","baujahr_von":YYYY,"baujahr_bis":YYYY_oder_null}]\n'
    "Nur eigenständige Generationen, keine Facelift-Unterversionen."
)

# Alle bekannten Problemkandidaten (aus vorherigen Logs)
CASES = [
    "alle Mercedes GLA Generationen",
    "alle Mercedes GLB Generationen",
    "alle Mercedes GLC Generationen",
    "alle Mercedes G-Klasse Generationen",
    "alle Mercedes AMG GT Generationen",
    "alle Mercedes EQA Generationen",
    "alle Mercedes EQB Generationen",
    "alle Mercedes EQE Generationen",
    "alle Mercedes EQS Generationen",
]

SEP = "=" * 70


def _dump_resp(resp, model: str, user_prompt: str):
    """Vollständige Rohausgabe eines GenerateContentResponse."""
    print(f"\n{'─' * 70}")
    print(f"  MODELL   : {model}")
    print(f"  PROMPT   : {user_prompt!r}")
    print(f"{'─' * 70}")

    # ── prompt_feedback ──────────────────────────────────────────────
    pf = getattr(resp, "prompt_feedback", None)
    if pf is not None:
        br = getattr(pf, "block_reason", None)
        print(f"  prompt_feedback.block_reason : {br!r}")
        pf_ratings = getattr(pf, "safety_ratings", [])
        if pf_ratings:
            print(f"  prompt_feedback.safety_ratings:")
            for r in pf_ratings:
                print(f"    {r.category!r:55s} prob={r.probability!r}")
    else:
        print("  prompt_feedback              : (kein Attribut)")

    # ── candidates ───────────────────────────────────────────────────
    cands = getattr(resp, "candidates", [])
    if not cands:
        print("  candidates                   : LEER (kein Candidate!)")
    else:
        c = cands[0]
        fr = getattr(c, "finish_reason", None)
        print(f"  finish_reason                : {fr!r}")

        sr = getattr(c, "safety_ratings", [])
        if sr:
            print(f"  safety_ratings (candidate[0]):")
            for r in sr:
                print(f"    {r.category!r:55s} prob={r.probability!r} blocked={getattr(r, 'blocked', '?')!r}")
        else:
            print("  safety_ratings               : [] (leer)")

        # finish_message / grounding metadata falls vorhanden
        fm = getattr(c, "finish_message", None)
        if fm:
            print(f"  finish_message               : {fm!r}")

    # ── resp.text ────────────────────────────────────────────────────
    raw_text = getattr(resp, "text", "<kein .text-Attribut>")
    if raw_text is None:
        print("  resp.text                    : None  ← LEER/GEBLOCKT")
    elif raw_text == "":
        print("  resp.text                    : ''  ← LEER")
    else:
        preview = raw_text[:300].replace("\n", "↵")
        print(f"  resp.text (len={len(raw_text)})        : {preview!r}")

    # ── usage_metadata ───────────────────────────────────────────────
    um = getattr(resp, "usage_metadata", None)
    if um:
        print(f"  usage_metadata               : "
              f"prompt={getattr(um,'prompt_token_count','?')}  "
              f"output={getattr(um,'candidates_token_count','?')}  "
              f"thinking={getattr(um,'thoughts_token_count','?')}")


def call_raw(client, model: str, user_prompt: str, max_output_tokens: int):
    """Direkter API-Call — KEIN resp.text or '', kein Wrapper."""
    cfg = genai_types.GenerateContentConfig(
        system_instruction=_BATCH_SYSTEM,
        temperature=0.0,
        max_output_tokens=max_output_tokens,
        thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
    )
    contents = [{"role": "user", "parts": [{"text": user_prompt}]}]
    try:
        resp = client.models.generate_content(
            model=model,
            contents=contents,
            config=cfg,
        )
        _dump_resp(resp, model, user_prompt)
        return getattr(resp, "text", None)
    except Exception as exc:
        print(f"\n  EXCEPTION [{model}]: {type(exc).__name__}: {exc}")
        return None


def main():
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        # Fallback: .env-Datei direkt lesen
        env_path = __import__("pathlib").Path(".env")
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("GEMINI_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not api_key:
        print("FEHLER: GEMINI_API_KEY nicht gesetzt.")
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    print(SEP)
    print("DIAGNOSE: generationen_auflisten — Raw Response")
    print(f"Primärmodell  : {FAST_LLM_MODEL}")
    print(f"Fallbackmodell: {LLM_MODEL}")
    print(SEP)
    print(f"\nSystem-Prompt (exakt wie in admin_llm.py):")
    print(f"  {_BATCH_SYSTEM!r}")
    print()

    blocked_cases = []
    empty_cases   = []
    ok_cases      = []

    for anfrage in CASES:
        print(f"\n{SEP}")
        print(f"  ANFRAGE: {anfrage!r}")
        print(SEP)

        # ── Primär: Flash-Lite ────────────────────────────────────────
        print("\n[1] PRIMÄR — flash-lite (max_tokens=800):")
        text_lite = call_raw(client, FAST_LLM_MODEL, anfrage, max_output_tokens=800)

        # ── Fallback: Flash ──────────────────────────────────────────
        print("\n[2] FALLBACK — flash (max_tokens=1600):")
        text_flash = call_raw(client, LLM_MODEL, anfrage, max_output_tokens=1600)

        # Klassifizierung
        if text_lite is None and text_flash is None:
            blocked_cases.append(anfrage)
        elif not (text_lite or "").strip() and not (text_flash or "").strip():
            empty_cases.append(anfrage)
        else:
            ok_cases.append(anfrage)

    # ── Zusammenfassung ──────────────────────────────────────────────
    print(f"\n\n{SEP}")
    print("ZUSAMMENFASSUNG")
    print(SEP)
    print(f"  OK (beide Modelle liefern Text)   : {len(ok_cases)}")
    for a in ok_cases:
        print(f"    OK   {a!r}")
    print(f"  LEER (text='' bei beiden)          : {len(empty_cases)}")
    for a in empty_cases:
        print(f"    LEER {a!r}")
    print(f"  FEHLER (Exception/None bei beiden) : {len(blocked_cases)}")
    for a in blocked_cases:
        print(f"    FAIL {a!r}")


if __name__ == "__main__":
    main()
