"""
DIAGNOSE-Skript: Motoren-Call (Call B) isoliert für Mercedes A-Klasse W177.
Kein Retry, kein Fix — alles roh auf stdout.
"""
import sys, traceback, json, io
# UTF-8 erzwingen, damit → und Umlaute in der Konsole nicht crashen
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from google.genai import types as genai_types

from app.llm import _get_client
from app.config import LLM_MODEL
from app.admin_llm import _SYS_MOTOREN, _cfg_with

# ---------- Eingabe ----------
MARKE      = "Mercedes"
MODELL     = "A-Klasse"
GENERATION = "W177"
BID        = f"{MARKE}-{MODELL}-{GENERATION}".lower().replace(" ", "-")

PROMPT = (
    f"Liste alle Motorvarianten des {MARKE} {MODELL} {GENERATION} auf.\n"
    f"baureihe_id für variante_id-Prefix: {BID}\n"
    "Unbekannte Zahlen → null."
)

print("=" * 70)
print("DIAGNOSE: Motoren-Call isoliert")
print("=" * 70)
print(f"\n[PROMPT AN MODELL]\n{PROMPT}\n")
print(f"[SYSTEM INSTRUCTION]\n{_SYS_MOTOREN}\n")
print("=" * 70)
print(f"Modell: {LLM_MODEL}")

cfg = _cfg_with(_SYS_MOTOREN)
client = _get_client()
contents = [{"role": "user", "parts": [{"text": PROMPT}]}]

resp = None
exc_info = None

try:
    resp = client.models.generate_content(
        model=LLM_MODEL,
        contents=contents,
        config=cfg,
    )
except Exception:
    exc_info = sys.exc_info()

print("\n" + "=" * 70)
print("[EXCEPTION]")
if exc_info and exc_info[0] is not None:
    print(f"  Typ     : {exc_info[0].__name__}")
    print(f"  Nachricht: {exc_info[1]}")
    print("\n  --- Traceback (vollständig) ---")
    traceback.print_exception(*exc_info)
else:
    print("  Keine Exception.")

print("\n" + "=" * 70)
if resp is not None:
    # finish_reason
    try:
        fr = resp.candidates[0].finish_reason if resp.candidates else "KEINE CANDIDATES"
        print(f"[finish_reason] {fr}")
    except Exception as e:
        print(f"[finish_reason] FEHLER beim Lesen: {e}")

    # resp.text
    print("\n[resp.text]")
    try:
        txt = resp.text
        if txt is None:
            print("  → None")
        elif txt.strip() == "":
            print("  → LEER (leerer String)")
        else:
            print(txt)
    except Exception as e:
        print(f"  FEHLER beim Lesen von resp.text: {e}")

    # token usage
    print("\n[Token Usage]")
    try:
        meta = resp.usage_metadata
        print(f"  prompt_token_count    : {meta.prompt_token_count}")
        print(f"  candidates_token_count: {meta.candidates_token_count}")
        print(f"  thoughts_token_count  : {getattr(meta, 'thoughts_token_count', 'N/A')}")
        print(f"  total_token_count     : {meta.total_token_count}")
    except Exception as e:
        print(f"  FEHLER: {e}")

    # candidates roh
    print("\n[candidates roh]")
    try:
        for i, cand in enumerate(resp.candidates or []):
            print(f"  Candidate {i}:")
            print(f"    finish_reason : {cand.finish_reason}")
            print(f"    safety_ratings: {cand.safety_ratings}")
            if cand.content and cand.content.parts:
                for j, part in enumerate(cand.content.parts):
                    pt = getattr(part, 'text', None)
                    tt = getattr(part, 'thought', None)
                    if tt:
                        print(f"    Part {j} [THOUGHT, {len(tt)} Zeichen]: {tt[:200]}...")
                    elif pt is not None:
                        if pt.strip():
                            print(f"    Part {j} [text, {len(pt)} Zeichen]: {pt[:500]}")
                        else:
                            print(f"    Part {j} [text]: LEER")
                    else:
                        print(f"    Part {j}: {part}")
            else:
                print("    content.parts: LEER oder kein Content")
    except Exception as e:
        print(f"  FEHLER: {e}")
else:
    print("  (kein Response-Objekt — Exception trat vor Rückgabe auf)")

print("\n" + "=" * 70)
print("DIAGNOSE ENDE")
