import asyncio, sys, time
sys.path.insert(0, ".")
from app.admin_llm import entwurf_erstellen

async def test(marke, modell, gen):
    print(f"  {marke} {modell} {gen} ...", end=" ", flush=True)
    t0 = time.time()
    try:
        d = await entwurf_erstellen(marke, modell, gen)
        dt = time.time() - t0
        erk = (d.get("erkennung_generation") or "")[:70]
        mot = len(d.get("motoren", []))
        print(f"OK {dt:.0f}s | {mot} Motoren")
        print(f"      erkennung: {erk}")
        return True
    except Exception as e:
        print(f"FAIL {time.time()-t0:.0f}s | {e}")
        return False

async def main():
    cases = [
        ("BMW", "3er", "E46"),
        ("BMW", "3er", "G20"),
        ("BMW", "i4",  "G26"),
        ("BMW", "1er", "F20"),
    ]
    ok = sum([await test(*c) for c in cases])
    print(f"\n{ok}/{len(cases)} OK")

asyncio.run(main())
