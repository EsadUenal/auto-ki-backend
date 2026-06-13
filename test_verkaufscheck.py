"""
Test: POST /api/v1/verkaufscheck — BMW 3er G20 320d Beispiel.

Verwendung:
    python test_verkaufscheck.py

Benötigt: GEMINI_API_KEY (+ optional TAVILY_API_KEY für Marktpreise).
"""
import sys
import io
import os
import asyncio

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
    from app.verkaufscheck import run_verkaufscheck
    from app.models import VerkaufsCheckRequest

    print(SEP)
    print("Test: Verkaufs-Check — BMW 3er G20 320d, 2020, 80.000 km, M Sport")
    print(SEP)

    req = VerkaufsCheckRequest(
        marke          = "BMW",
        modell         = "3er",
        baujahr        = 2020,
        kilometerstand = 80_000,
        motor          = "320d",
        kraftstoff     = "Diesel",
        ausstattung    = ["M Sport", "Navigations-Professional", "Driving Assistant Plus",
                          "Sitzheizung", "LED-Scheinwerfer", "Automatik", "Panoramadach"],
        beschreibung   = (
            "Scheckheftgepflegt beim BMW-Händler, unfallfrei, Nichtraucher, "
            "2 Vorbesitzer, TÜV bis 12/2025. Winterräder auf Stahlfelgen inklusive. "
            "Leichte Gebrauchsspuren im Innenraum, keine Kratzer außen."
        ),
        maengel        = ["Hintere Bremsbeläge sollten beim nächsten Service erneuert werden"],
        preis_vorstellung = 29_500,
    )

    print("\nFahrzeug-Eingabe:")
    print(f"  Marke/Modell : {req.marke} {req.modell} {req.motor}")
    print(f"  Baujahr      : {req.baujahr} | KM: {req.kilometerstand:,} km".replace(",", "."))
    print(f"  Ausstattung  : {', '.join(req.ausstattung)}")
    print(f"  Zustand      : {req.beschreibung}")
    print(f"  Mängel       : {', '.join(req.maengel)}")
    print(f"  Preisvorst.  : {req.preis_vorstellung:,} €".replace(",", "."))
    print()
    print("Analyse läuft (DB + Websuche + Gemini)…")
    print("-" * 72)

    result = await run_verkaufscheck(req)

    # ── Bericht ──────────────────────────────────────────────────────────────
    print("\nBERICHT:\n")
    print(result["bericht"])

    # ── Strukturierte Felder ──────────────────────────────────────────────────
    print()
    print("-" * 72)

    def fmt(val: int | None) -> str:
        return f"{val:,} €".replace(",", ".") if val else "—"

    print(f"Schnellverkauf      : {fmt(result['schnellverkaufs_preis'])}  "
          f"(ca. {result['verkaufsdauer_tage_schnell'] or '?'} Tage)")
    print(f"Empfohlener Preis   : {fmt(result['empfohlener_preis'])}")
    print(f"Maximalpreis        : {fmt(result['maximal_preis'])}  "
          f"(ca. {result['verkaufsdauer_tage_maximal'] or '?'} Tage)")
    print(f"Marktspanne (Web)   : {fmt(result['marktpreis_min'])} – {fmt(result['marktpreis_max'])}")
    print(f"baureihe_erkannt    : {result['baureihe_erkannt']}")
    print(f"motor_erkannt       : {result['motor_erkannt']}")
    print(f"quelle / vertrauen  : {result['quelle']} / {result['vertrauen']}")
    print(f"belege              : {len(result['belege'])} Web-Quellen")
    for b in result["belege"]:
        print(f"  [{b['typ']}] {b['titel'][:55]:55s}  {b['url'][:65]}")

    print()
    print(SEP)
    print("Test abgeschlossen.")
    print(SEP)


if __name__ == "__main__":
    asyncio.run(main())
