"""
Test: Marktvergleich 2.0 (app/marktvergleich) — Phase 1.

Deterministisch, kein Netzwerk/LLM. Deckt die geforderten Marktvergleich-Testfälle
1-10 ab: Vergleichbarkeit (Generation/Baujahr/km/Kraftstoff), Ausreißer-Robustheit,
Median/Quartils-Spanne, verwendete-Anzahl, nur verwendete Vergleiche in Evidence,
keine Suchseite als Einzelfahrzeug.

Ausfuehren:  python test_marktvergleich.py
"""
import os
import sys
import tempfile
from types import SimpleNamespace

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_mv_"), "test.db")
sys.path.insert(0, ".")

from app.marktvergleich import analysiere_markt, baue_ziel   # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


BAUREIHE = {"marke": "BMW", "modell": "3er", "generation": "G20/G21", "id": "bmw-3er-g20-g21"}
ALLE = [
    {"id": "bmw-3er-e90", "marke": "BMW", "modell": "3er", "generation": "E90"},
    {"id": "bmw-3er-f30", "marke": "BMW", "modell": "3er", "generation": "F30"},
    {"id": "bmw-3er-e46", "marke": "BMW", "modell": "3er", "generation": "E46"},
]
ZIEL = baue_ziel(BAUREIHE, {"kraftstoff": "Diesel"},
                 SimpleNamespace(baujahr=2020, kilometerstand=78500, kraftstoff=None), ALLE)

check("Ziel: Generation-Tokens g20/g21", ZIEL["generation_tokens"] == {"g20", "g21"})
check("Ziel: Fremd-Generationen e90/f30/e46 (aus DB abgeleitet)",
      ZIEL["fremd_generationen"] == {"e90", "f30", "e46"})

# Ein Snippet mit einer Mischung: gute G20-320d, eine 230.000-km-Karre, ein E90 (7k),
# ein F30, ein Ausreißerpreis (Fremdmodell), plus ein sauberer G20.
WEB = [
    {"url": "https://www.kleinanzeigen.de/s-autos/bmw/k0", "title": "BMW 320d 2020 kaufen",
     "content": (
         "BMW 320d G20 29.999 € 78.000 km EZ 04/2021 . "
         "BMW 320d G20 26.500 € VB 82.000 km EZ 04/2020 . "
         "BMW 320d 24.900 € 90.000 km EZ 02/2020 . "
         "BMW 320d Touring 25.500 € 70.000 km EZ 06/2020 . "
         "BMW 320d 27.000 € 75.000 km EZ 08/2020 . "
         "BMW 320i 26.000 € 80.000 km EZ 03/2020")},
    {"url": "https://suchen.mobile.de/auto/bmw-3er-e90.html", "title": "BMW E90 gebraucht",
     "content": "BMW 325i E90 8.500 € 120.000 km EZ 03/2006 . BMW 320d E90 7.699 € 173.000 km EZ 12/2007"},
    {"url": "https://autouncle.de/audi-rs4", "title": "Audi RS4",
     "content": "Audi RS4 43.092 € 60.000 km EZ 01/2019"},
    {"url": "https://kleinanzeigen.de/hoch", "title": "BMW 320d viel km",
     "content": "BMW 320d G20 18.990 € 230.000 km EZ 02/2020"},
    {"url": "https://autoscout24.at/bmw-320d-f30", "title": "BMW 320d F30 2014",
     "content": "BMW 320d F30 13.900 € 140.000 km EZ 05/2014"},
]

ma = analysiere_markt(WEB, ZIEL, 23490)
preise = sorted(b.preis_eur for b in ma.beobachtungen)

# 1: Zielfahrzeug wird korrekt bewertet (mind. mehrere sehr_aehnlich)
check("1: mehrere sehr ähnliche G20-320d erkannt", ma.anzahl_sehr_aehnlich >= 3)
# 2: 230.000 km darf nicht als sehr_ähnlich zählen (und nicht verwendet werden)
check("2: 230.000-km-Fahrzeug NICHT in verwendeten Vergleichen", 18990 not in preise)
# 3: F30 (2014, andere Generation) darf nicht als guter G20-Vergleich zählen
check("3: BMW F30 (Fremdgeneration) NICHT verwendet", 13900 not in preise)
# 4: gleicher G20 2020 mit ~80.000 km -> sehr_ähnlich
_g20 = [b for b in ma.beobachtungen if b.preis_eur == 26500]
check("4: G20 2020 ~82.000 km -> sehr_ähnlich", bool(_g20) and _g20[0].vergleichbarkeit == "sehr_aehnlich")
# 5: E90 (andere Generation, 7k, 173k km) -> ungeeignet/ausgeschlossen
check("5: E90-Billigfahrzeuge (7.699/8.500) ausgeschlossen", 7699 not in preise and 8500 not in preise)
# 6: Ausreißerpreis (Audi RS4 43.092) verzerrt die Spanne nicht
check("6: Ausreißerpreis 43.092 € nicht in verwendeten Preisen (Tukey-Trim)", 43092 not in preise)
check("6: Spanne bleibt eng/plausibel (max < 32.000)", ma.spanne_max_eur is not None and ma.spanne_max_eur < 32000)
# 7: Median korrekt (Median der verwendeten Preise, auf 100 gerundet)
import statistics as _st  # noqa: E402
_erwartet_median = round(_st.median(preise) / 100) * 100
check("7: Median = Median der verwendeten Preise", ma.median_eur == _erwartet_median)
# 8: verwendete Fahrzeuganzahl korrekt
check("8: verwendet == Anzahl Beobachtungen", ma.verwendet == len(ma.beobachtungen))
check("8: gefunden > verwendet (unpassende wurden gefiltert)", ma.gefunden > ma.verwendet)
# 9: Evidence zeigt nur tatsächlich verwendete Vergleiche
check("9: alle Beobachtungen sind sehr_ähnlich/ähnlich/bedingt (nie ungeeignet)",
      all(b.vergleichbarkeit in ("sehr_aehnlich", "aehnlich", "bedingt") for b in ma.beobachtungen))
# 10: keine allgemeine Suchseite als konkretes Vergleichsfahrzeug — Beobachtungen
# tragen die RECHERCHE-Seite als quelle_url, sind aber KEINE Einzelinserate-URLs.
check("10: Beobachtungen referenzieren nur Recherche-Domains (kein Fake-Einzelinserat)",
      all(b.quelle_domain for b in ma.beobachtungen))

# Angebotsvergleich deterministisch: 23.490 unter Median
check("Angebot: differenz_eur = angebot - median",
      ma.differenz_eur == 23490 - ma.median_eur)
check("Angebot: differenz_pct gesetzt und negativ (unter Markt)",
      ma.differenz_pct is not None and ma.differenz_pct < 0)

# Datenqualität: konservativ (heuristische Extraktion) — hier mittel, nicht hoch
check("Datenqualität konservativ (mittel bei ~6 Treffern, nicht 'hoch')",
      ma.datenqualitaet in ("niedrig", "mittel"))
check("Methode dokumentiert (Median/Quartile genannt)",
      ma.methode is not None and "Median" in ma.methode)

# ── Zu wenige Daten -> keine Scheinpräzision ────────────────────────────────
WEB_WENIG = [
    {"url": "https://kleinanzeigen.de/a", "title": "BMW 320d",
     "content": "BMW 320d G20 26.000 € 80.000 km EZ 03/2020"},
]
ma_wenig = analysiere_markt(WEB_WENIG, ZIEL, 23490)
check("Wenig: <3 Vergleiche -> kein Median (keine Scheinpräzision)", ma_wenig.median_eur is None)
check("Wenig: Datenqualität niedrig", ma_wenig.datenqualitaet == "niedrig")
check("Wenig: Methode weist auf begrenzte Datenbasis hin",
      ma_wenig.methode is not None and "begrenzt" in ma_wenig.methode.lower())

# ── Inkohärente Streuung (gemischte Preisarten) -> kein Schein-Median ────────
# Reale Falle: eine Quelle listet dasselbe Auto mit Cash-/Finanzierungs-/Gesamt-
# preisen (1.690 / 13.960 / 31.384 €) -> extrahierte Datenpunkte streuen absurd.
# (Domain bewusst NICHT 12gebrauchtwagen.de — die ist seit §Phase 1 ein real
# erkannter Aggregator, siehe test_aggregatorseiten.py; hier testen wir den
# Streuungs-Guard auf einer neutralen, unklassifizierten Domain.)
WEB_STREU = [
    {"url": "https://beispielportal.de/x", "title": "Mercedes C 200 2019",
     "content": (
         "Mercedes C 200 1.690 € 100.000 km EZ 01/2020 . "
         "Mercedes C 200 13.960 € 100.000 km EZ 02/2020 . "
         "Mercedes C 200 31.384 € 100.000 km EZ 03/2020 . "
         "Mercedes C 200 4.984 € 99.000 km EZ 04/2019 . "
         "Mercedes C 200 22.106 € 99.000 km EZ 05/2019 . "
         "Mercedes C 200 30.804 € 98.000 km EZ 06/2019")},
]
ZIEL_MB = baue_ziel(
    {"marke": "Mercedes-Benz", "modell": "C 200", "generation": "W205", "id": "mb-c-w205"},
    {"kraftstoff": "Benzin"},
    SimpleNamespace(baujahr=2019, kilometerstand=95000, kraftstoff=None), [])
ma_streu = analysiere_markt(WEB_STREU, ZIEL_MB, 24000)
check("Streuung: absurd breite Preisstreuung -> KEIN Schein-Median",
      ma_streu.median_eur is None)
check("Streuung: Datenqualität niedrig", ma_streu.datenqualitaet == "niedrig")
check("Streuung: Methode nennt die zu starke Streuung / uneinheitliche Basis",
      ma_streu.methode is not None and ("streu" in ma_streu.methode.lower() or "uneinheit" in ma_streu.methode.lower()))

# ── Keine Web-Daten -> leere, ehrliche Analyse ──────────────────────────────
ma_leer = analysiere_markt([], ZIEL, 23490)
check("Leer: gefunden=0, kein Median", ma_leer.gefunden == 0 and ma_leer.median_eur is None)

# ── Deduplizierung: dieselbe Anzeige in mehreren Snippets zählt einmal ───────
WEB_DUP = [
    {"url": "https://a.de/1", "title": "x", "content": "BMW 320d G20 26.000 € 80.000 km EZ 03/2020"},
    {"url": "https://b.de/2", "title": "y", "content": "BMW 320d G20 26.000 € 80.000 km EZ 03/2020"},
    {"url": "https://c.de/3", "title": "z", "content": "BMW 320d G20 27.000 € 76.000 km EZ 05/2020"},
    {"url": "https://d.de/4", "title": "w", "content": "BMW 320d G20 25.000 € 82.000 km EZ 02/2020"},
]
ma_dup = analysiere_markt(WEB_DUP, ZIEL, None)
check("Dedup: identische Anzeige (26.000/80.000/2020) nur einmal gezählt",
      sum(1 for b in ma_dup.beobachtungen if b.preis_eur == 26000) == 1)

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER: " + ", ".join(FEHLER))
    raise SystemExit(1)
print("Alle Marktvergleich-2.0-Tests bestanden.")
