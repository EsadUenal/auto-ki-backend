"""
Integrationstest: Motoren-Call für 3 Modelle.
Prüft ob Streaming + Fallback-Modell alle drei durchbringt.
"""
import asyncio, sys, time, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from app.admin_llm import entwurf_erstellen

CASES = [
    ("Mercedes", "A-Klasse", "W177"),
    ("BMW",      "3er",      "E36"),
    ("BMW",      "1er",      "F20"),
]


async def test_one(marke: str, modell: str, generation: str) -> bool:
    label = f"{marke} {modell} {generation}"
    print(f"\n{'='*60}")
    print(f"  TEST: {label}")
    print(f"{'='*60}")
    t0 = time.time()
    try:
        d = await entwurf_erstellen(marke, modell, generation)
    except Exception as e:
        dt = time.time() - t0
        print(f"  FEHLER nach {dt:.0f}s: {type(e).__name__}: {e}")
        return False

    dt = time.time() - t0
    mot  = d.get("motoren", [])
    merr = d.get("_motorenfehler")
    erk  = (d.get("erkennung_generation") or "")[:100]

    print(f"  Zeit          : {dt:.0f}s")
    print(f"  Motoren       : {len(mot)} Varianten")
    print(f"  _motorenfehler: {merr or 'keiner'}")
    print(f"  erkennung     : {erk}")

    if mot:
        print(f"  Erste Variante: {mot[0].get('bezeichnung','?')} "
              f"| {mot[0].get('leistung_ps','?')} PS "
              f"| {mot[0].get('kraftstoff','?')}")

    ok = len(mot) > 0 and not merr
    print(f"  ERGEBNIS      : {'OK' if ok else 'FEHLGESCHLAGEN'}")
    return ok


async def main():
    print("\nTest: Streaming + Fallback-Modell fuer Motoren-Call")
    print(f"LLM_MODEL      : gemini-2.5-flash")
    print(f"FAST_LLM_MODEL : gemini-2.5-flash-lite (Fallback bei 503)")
    print(f"MAX_RETRIES_503: 5  |  RETRY_DELAY_503: 15s")

    results = []
    for marke, modell, gen in CASES:
        ok = await test_one(marke, modell, gen)
        results.append((f"{marke} {modell} {gen}", ok))

    print(f"\n{'='*60}")
    print("ZUSAMMENFASSUNG")
    print(f"{'='*60}")
    for label, ok in results:
        status = "OK  " if ok else "FAIL"
        print(f"  [{status}]  {label}")

    gesamt_ok = sum(1 for _, ok in results if ok)
    print(f"\n  {gesamt_ok}/{len(results)} bestanden")
    if gesamt_ok == len(results):
        print("  Alle Tests erfolgreich.")
    else:
        print("  ACHTUNG: Mindestens ein Test fehlgeschlagen.")


asyncio.run(main())
