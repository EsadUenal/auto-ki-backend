"""
Test: Kategorie-/Aggregatorseiten (Reliability-Sprint 4, §Phase 1-3).

Deterministisch, kein Netzwerk/LLM. Deckt den konkret gemeldeten Nutzerbefund ab:
eine allgemeine Modell-/Motor-Suchseite (12gebrauchtwagen.de, zweispurig.at u.ä.)
darf NICHT als einzelnes Vergleichsfahrzeug in Median/Quartile/Datenqualität
einfließen — bleibt aber als Hintergrundquelle sichtbar (hintergrund_domains).

Ausfuehren:  python test_aggregatorseiten.py
"""
import os
import sys
import tempfile
from types import SimpleNamespace

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_agg_"), "test.db")
sys.path.insert(0, ".")

# §Source-Policy: Der Production-Default gibt KEINE Marktquelle zum Preisbilden
# frei (app/config.ALLOWED_MARKET_SOURCES ist leer). Dieser Test prueft die
# ANALYSE-ENGINE und braucht dafuer die historischen/synthetischen Testdomains —
# die Freigabe gilt ausschliesslich in diesem Testprozess und ist KEINE
# produktive Qualifikation der Quelle. Siehe _source_policy_testharness.py.
import _source_policy_testharness  # noqa: E402,F401

from app.marktvergleich import analysiere_markt, baue_ziel   # noqa: E402
from app.web_search import ist_kategorieseite, ist_einzelinserat   # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


BAUREIHE = {"marke": "BMW", "modell": "3er", "generation": "G20/G21", "id": "bmw-3er-g20-g21"}
ZIEL = baue_ziel(BAUREIHE, {"kraftstoff": "Diesel"},
                 SimpleNamespace(baujahr=2019, kilometerstand=120000, kraftstoff=None), [])

# ── 1) 12gebrauchtwagen.de-artige allgemeine Modell-/Motor-Suchseite ─────────
check("1: 12gebrauchtwagen.de/kraftstoff/diesel/bmw/3er wird als Kategorieseite erkannt",
      ist_kategorieseite("https://www.12gebrauchtwagen.de/kraftstoff/diesel/bmw/3er") is True)
check("1b: dieselbe URL ist KEIN Einzelinserat",
      ist_einzelinserat("https://www.12gebrauchtwagen.de/kraftstoff/diesel/bmw/3er") is False)

WEB = [
    # Allgemeine 12gebrauchtwagen-Suchseite mit mehreren Preisen (der gemeldete Fall).
    {"url": "https://www.12gebrauchtwagen.de/kraftstoff/diesel/bmw/3er",
     "title": "BMW 3er Diesel gebraucht — 12gebrauchtwagen.de",
     "content": ("BMW 320d G20 24.900 € 118.000 km EZ 03/2019 . "
                 "BMW 330d G20 28.900 € 95.000 km EZ 06/2019 . "
                 "BMW 318d G20 21.900 € 130.000 km EZ 01/2019")},
    # Echte, einzeln attribuierte Kleinanzeigen-Trefferliste (Sprint-3-Realfall,
    # bleibt tragfähig — source_type="unknown", nicht als Kategorieseite erkannt).
    {"url": "https://www.kleinanzeigen.de/s-bmw-320d/k0",
     "title": "BMW 320d 2019 kaufen",
     "content": ("BMW 320d G20 25.900 € 115.000 km EZ 02/2019 . "
                 "BMW 320d G20 24.500 € 122.000 km EZ 04/2019 . "
                 "BMW 320d G20 26.200 € 108.000 km EZ 01/2019 . "
                 "BMW 320d G20 23.900 € 128.000 km EZ 06/2019")},
]

ma = analysiere_markt(WEB, ZIEL, 24900)
preise = sorted(b.preis_eur for b in ma.beobachtungen)

check("2: Preise von der 12gebrauchtwagen-Seite NICHT im Median (24.900/28.900/21.900 raus)",
      24900 not in preise and 28900 not in preise and 21900 not in preise)
check("2b: Kleinanzeigen-Preise weiterhin verwendet",
      any(p in preise for p in (25900, 24500, 26200, 23900)))
check("3: 12gebrauchtwagen.de erscheint in hintergrund_domains",
      any("12gebrauchtwagen" in d for d in ma.hintergrund_domains))
check("4: 12gebrauchtwagen.de NICHT in den median-tragenden quellen_domains",
      not any("12gebrauchtwagen" in d for d in ma.quellen_domains))
check("5: Median stammt ausschließlich aus der Kleinanzeigen-Basis",
      ma.median_eur is not None and 23000 <= ma.median_eur <= 27000)

print()
if FEHLER:
    print(f"{len(FEHLER)} Test(s) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle Aggregatorseiten-Tests bestanden.")
