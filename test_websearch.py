"""
Test: Tavily Search Integration — echte Preisabfrage.

Verwendung:
    python test_websearch.py

Benötigt: TAVILY_API_KEY in .env oder als Umgebungsvariable gesetzt.
"""
import sys
import io
import os
import asyncio
import json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

# .env laden
try:
    for line in open(".env", encoding="utf-8").read().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"\''))
except FileNotFoundError:
    pass

SEP = "=" * 70


async def main():
    from app.config import TAVILY_API_KEY
    from app.web_search import tavily_search, results_to_context, results_to_belege, build_price_query

    print(SEP)
    print("Test: Tavily Search Integration")
    print(SEP)

    if not TAVILY_API_KEY:
        print("""
FEHLER: TAVILY_API_KEY nicht gesetzt.

Dauerhaft in .env eintragen (empfohlen):
    TAVILY_API_KEY=tvly-dein_key_hier

PowerShell (nur aktuelle Session):
    $env:TAVILY_API_KEY = "tvly-dein_key_hier"
""")
        sys.exit(1)

    # Niemals einen Teil des Secrets ausgeben — auch nicht gekuerzt/maskiert.
    # Ein Praefix+Suffix-Fragment (frueher: TAVILY_API_KEY[:8]+"..."+[-4:]) reicht
    # bei kurzen Keys zur Rekonstruktion und gehoert grundsaetzlich nicht in
    # Test-Output. Nur ein reiner Vorhanden-Check.
    print(f"  API-Key: gesetzt ({len(TAVILY_API_KEY)} Zeichen)")
    print()

    # ── Test 1: Direkte Tavily-Suche ─────────────────────────────────────────
    ANFRAGE = "VW Golf 8 Gebrauchtpreis 2021 50000 km Deutschland"

    print(f"[1] Direkte Tavily-Suche: {ANFRAGE!r}")
    print("-" * 70)
    results = await tavily_search(ANFRAGE, count=5)

    if not results:
        print("  FEHLER: Keine Ergebnisse. Key ungültig oder kein Netz.")
        sys.exit(1)

    print(f"  {len(results)} Ergebnisse empfangen:")
    for i, r in enumerate(results, 1):
        score = r.get("score", "?")
        print(f"\n  [{i}] {r.get('title', '?')}  (score={score:.3f})" if isinstance(score, float) else f"\n  [{i}] {r.get('title', '?')}")
        print(f"       URL     : {r.get('url', '?')}")
        print(f"       Content : {(r.get('content') or '')[:150]}")
        if r.get("published_date"):
            print(f"       Datum   : {r['published_date']}")

    # ── Test 2: LLM-Kontext-Block ─────────────────────────────────────────────
    print()
    print("[2] LLM-Kontext-Block (wird so an Gemini übergeben):")
    print("-" * 70)
    print(results_to_context(results))

    # ── Test 3: Belege-Struktur (API-Response) ────────────────────────────────
    print()
    print("[3] API-Belege (quelle='web', Frontend-Quell-Links):")
    print("-" * 70)
    print(json.dumps(results_to_belege(results), ensure_ascii=False, indent=2))

    # ── Test 4: Query-Builder ─────────────────────────────────────────────────
    print()
    print("[4] Query-Builder:")
    print("-" * 70)
    for marke, modell, gen, frage in [
        ("Volkswagen", "Golf", "8",   "Was kostet ein Golf 8 2021 mit 50000 km?"),
        ("BMW",        "3er",  "G20", "Gebrauchtpreis BMW 3er G20 Diesel"),
        ("Toyota",     "Yaris","IV",  "aktueller Rückruf Toyota Yaris"),
    ]:
        q = build_price_query(marke, modell, gen, frage)
        print(f"  Frage : {frage!r}")
        print(f"  Query : {q!r}")
        print()

    # ── Test 5: Vollständiger chat_stream-Aufruf ──────────────────────────────
    print()
    print("[5] chat_stream (Volltest — Golf 8 Gebrauchtpreis):")
    print("-" * 70)
    from app.llm import chat_stream

    full_text, meta = [], {}
    async for event in chat_stream(
        "Was kostet ein VW Golf 8 Gebraucht, Baujahr 2021 ca. 50000 km?", []
    ):
        if event["type"] == "text":
            full_text.append(event["delta"])
        elif event["type"] == "meta":
            meta = event

    print("Antwort der KI:")
    print("".join(full_text))
    print()
    print(f"quelle    : {meta.get('quelle')}")
    print(f"vertrauen : {meta.get('vertrauen')}")
    n_belege = len(meta.get("belege", []))
    print(f"belege    : {n_belege} Web-Quellen")
    for b in meta.get("belege", []):
        print(f"  [{b['typ']}] {b['titel'][:55]:55s}  {b['url'][:70]}")

    print()
    print(SEP)
    print("Alle Tests abgeschlossen." + ("  ✓" if n_belege else "  ⚠ belege leer"))
    print(SEP)


if __name__ == "__main__":
    asyncio.run(main())
