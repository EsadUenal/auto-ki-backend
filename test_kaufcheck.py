"""
Test: POST /api/v1/kaufcheck — BMW 3er G20 320d Beispiel-Inserat.

Verwendung:
    python test_kaufcheck.py

Benötigt: GEMINI_API_KEY (+ optional TAVILY_API_KEY für Marktpreise).
"""
import sys
import io
import os
import asyncio
import json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

try:
    for line in open(".env", encoding="utf-8").read().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"\''))
except FileNotFoundError:
    pass

SEP = "=" * 72


async def main():
    from app.kaufcheck import run_kaufcheck
    from app.models import KaufCheckRequest

    print(SEP)
    print("Test: Kauf-Check — BMW 3er G20 320d, 2020, 80.000 km, 28.000 €")
    print(SEP)

    req = KaufCheckRequest(
        marke          = "BMW",
        modell         = "3er",
        baujahr        = 2020,
        kilometerstand = 80_000,
        motor          = "320d",
        kraftstoff     = "Diesel",
        preis_eur      = 28_000,
        ausstattung    = ["M Sport", "Navigations-Professional", "Driving Assistant Plus",
                          "Sitzheizung", "LED-Scheinwerfer", "Automatik"],
        beschreibung   = (
            "Gepflegter BMW 320d, Privatverkauf, Scheckheftgepflegt beim BMW-Händler, "
            "Nichtraucher, kein Unfall, 2 Vorbesitzer. TÜV neu 12/2025. "
            "Winterräder inklusive. Fahrzeug befindet sich in München."
        ),
    )

    print("\nInserat-Eingabe:")
    print(f"  Marke/Modell : {req.marke} {req.modell} {req.motor} ({req.kraftstoff})")
    print(f"  Baujahr      : {req.baujahr}")
    print(f"  Kilometerstand: {req.kilometerstand:,} km".replace(",", "."))
    print(f"  Preis        : {req.preis_eur:,} €".replace(",", "."))
    print(f"  Ausstattung  : {', '.join(req.ausstattung)}")
    print()
    print("Analyse läuft (DB + Websuche + Gemini)…")
    print("-" * 72)

    result = await run_kaufcheck(req)

    # ── Bericht ──────────────────────────────────────────────────────────────
    print("\nBERICHT:\n")
    print(result["bericht"])

    # ── Strukturierte Felder ──────────────────────────────────────────────────
    print()
    print("-" * 72)
    print(f"empfehlung       : {result['empfehlung']}")
    print(f"preis_bewertung  : {result['preis_bewertung']}")
    mmin = result.get("marktpreis_min")
    mmax = result.get("marktpreis_max")
    if mmin and mmax:
        print(f"Marktpreis-Spanne: {mmin:,} – {mmax:,} €".replace(",", "."))
    print(f"baureihe_erkannt : {result['baureihe_erkannt']}")
    print(f"motor_erkannt    : {result['motor_erkannt']}")
    print(f"quelle           : {result['quelle']}")
    print(f"vertrauen        : {result['vertrauen']}")
    print(f"belege           : {len(result['belege'])} Web-Quellen")
    for b in result["belege"]:
        print(f"  [{b['typ']}] {b['titel'][:55]:55s}  {b['url'][:65]}")

    print()
    print(SEP)
    print("Test abgeschlossen.")
    print(SEP)


if __name__ == "__main__":
    asyncio.run(main())
