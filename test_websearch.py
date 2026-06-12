"""
Test: Brave Search Integration — echte Preisabfrage.

Verwendung:
    python test_websearch.py

Benötigt: BRAVE_SEARCH_API_KEY in .env oder als Umgebungsvariable gesetzt.
"""
import sys
import io
import os
import asyncio

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

# .env laden
_env = {}
try:
    for line in open(".env", encoding="utf-8").read().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            _env[k.strip()] = v.strip().strip('"\'')
    for k, v in _env.items():
        os.environ.setdefault(k, v)
except FileNotFoundError:
    pass

SEP = "=" * 70

async def main():
    from app.config import BRAVE_SEARCH_API_KEY
    from app.web_search import brave_search, results_to_context, results_to_belege, build_price_query

    print(SEP)
    print("Test: Brave Search Integration")
    print(SEP)

    if not BRAVE_SEARCH_API_KEY:
        print("""
FEHLER: BRAVE_SEARCH_API_KEY nicht gesetzt.

So bekommst du den Brave Search API-Key:
  1. Gehe zu: https://api.search.brave.com/
  2. Klicke auf "Sign Up" → kostenloses Konto erstellen
  3. Im Dashboard: "Create New Subscription" → Plan "Free AI" wählen
     → 2.000 Abfragen/Monat, kein Kreditkarte nötig
  4. API-Key kopieren (beginnt mit "BSA...")

API-Key als Umgebungsvariable setzen:

  PowerShell (nur aktuelle Session):
      $env:BRAVE_SEARCH_API_KEY = "BSA_dein_key_hier"

  Dauerhaft (in .env eintragen — empfohlen):
      Öffne die .env-Datei im Projektordner und ergänze:
      BRAVE_SEARCH_API_KEY=BSA_dein_key_hier

  Dauerhaft (Windows-Systemvariable):
      [System] → [Umgebungsvariablen] → Neu → Name: BRAVE_SEARCH_API_KEY
""")
        sys.exit(1)

    print(f"  API-Key: {BRAVE_SEARCH_API_KEY[:8]}...{BRAVE_SEARCH_API_KEY[-4:]}")
    print()

    # ── Test 1: Direkte Brave-Suche ──────────────────────────────────────────
    ANFRAGE = "VW Golf 8 Gebrauchtpreis 2021 50000 km"

    print(f"[1] Direkte Brave-Suche: {ANFRAGE!r}")
    print("-" * 70)
    results = await brave_search(ANFRAGE, count=5)

    if not results:
        print("  FEHLER: Keine Ergebnisse. Key ungültig oder kein Netz.")
        sys.exit(1)

    print(f"  {len(results)} Ergebnisse empfangen:")
    for i, r in enumerate(results, 1):
        print(f"\n  [{i}] {r.get('title', '?')}")
        print(f"       URL  : {r.get('url', '?')}")
        print(f"       Desc : {(r.get('description') or '')[:120]}")
        if r.get("page_age"):
            print(f"       Alter: {r['page_age']}")

    # ── Test 2: Context-Rendering ─────────────────────────────────────────────
    print()
    print("[2] LLM-Kontext-Block:")
    print("-" * 70)
    ctx = results_to_context(results)
    print(ctx)

    # ── Test 3: Belege-Struktur ───────────────────────────────────────────────
    print()
    print("[3] API-Belege (quelle='web', für Frontend):")
    print("-" * 70)
    import json
    belege = results_to_belege(results)
    print(json.dumps(belege, ensure_ascii=False, indent=2))

    # ── Test 4: Query-Builder ─────────────────────────────────────────────────
    print()
    print("[4] Query-Builder Beispiele:")
    print("-" * 70)
    cases = [
        ("Volkswagen", "Golf", "8", "Was kostet ein Golf 8 2021 mit 50000 km?"),
        ("BMW", "3er", "G20", "Gebrauchtpreis BMW 3er G20 Diesel"),
        ("Toyota", "Yaris", "IV", "aktueller Rückruf Toyota Yaris"),
    ]
    for marke, modell, gen, frage in cases:
        q = build_price_query(marke, modell, gen, frage)
        print(f"  Frage: {frage!r}")
        print(f"  Query: {q!r}")
        print()

    # ── Test 5: Vollständiger chat_stream-Aufruf ───────────────────────────────
    print()
    print("[5] Vollständiger chat_stream-Aufruf (Golf 8 Preis):")
    print("-" * 70)
    from app.llm import chat_stream
    full_text = []
    meta = {}
    async for event in chat_stream("Was kostet ein VW Golf 8 Gebraucht, Baujahr 2021 50000 km?", []):
        if event["type"] == "text":
            full_text.append(event["delta"])
        elif event["type"] == "meta":
            meta = event

    print("Antwort:")
    print("".join(full_text))
    print()
    print(f"quelle    : {meta.get('quelle')}")
    print(f"vertrauen : {meta.get('vertrauen')}")
    print(f"belege    : {len(meta.get('belege', []))} Quellen")
    for b in meta.get("belege", []):
        print(f"  [{b['typ']}] {b['titel'][:60]} — {b['url'][:80]}")

    print()
    print(SEP)
    print("Alle Tests abgeschlossen.")
    print(SEP)


if __name__ == "__main__":
    asyncio.run(main())
