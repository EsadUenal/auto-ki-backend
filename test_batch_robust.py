"""
Test: generationen_auflisten mit Fallback-Modell.
Prüft Mercedes GLA, G-Klasse, EQS und AMG GT — alle müssen eine
nicht-leere Generationen-Liste liefern.
"""
import asyncio, sys, io, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from app.admin_llm import generationen_auflisten

CASES = [
    ("Mercedes GLA",    "alle Mercedes GLA Generationen"),
    ("Mercedes G-Klasse", "alle Mercedes G-Klasse Generationen"),
    ("Mercedes EQS",    "alle Mercedes EQS Generationen"),
    ("Mercedes AMG GT", "alle Mercedes AMG GT Generationen"),
]


async def test_one(label: str, anfrage: str) -> bool:
    print(f"\n  [{label}]")
    print(f"  Anfrage: {anfrage!r}")
    t0 = time.time()
    try:
        result = await generationen_auflisten(anfrage)
    except Exception as e:
        dt = time.time() - t0
        print(f"  FEHLER nach {dt:.0f}s: {type(e).__name__}: {e}")
        return False

    dt = time.time() - t0
    print(f"  Zeit: {dt:.0f}s | {len(result)} Generationen gefunden")
    for g in result:
        gen  = g.get("generation", "?")
        von  = g.get("baujahr_von", "?")
        bis  = g.get("baujahr_bis") or "heute"
        print(f"    - {g.get('marke','')} {g.get('modell','')} {gen} ({von}–{bis})")

    ok = len(result) > 0
    print(f"  Ergebnis: {'OK' if ok else 'LEER — FEHLGESCHLAGEN'}")
    return ok


async def main():
    print("=" * 65)
    print("TEST: generationen_auflisten — Fallback-Modell")
    print(f"Primär: gemini-2.5-flash-lite | Fallback: gemini-2.5-flash")
    print("=" * 65)

    results = []
    for label, anfrage in CASES:
        ok = await test_one(label, anfrage)
        results.append((label, ok))

    print(f"\n{'=' * 65}")
    print("ZUSAMMENFASSUNG")
    print("=" * 65)
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
