"""
PRIMAERQUELLEN-PRUEFUNG RISIKOKLASSE B — Zusicherungen.
KEIN Netzwerk, KEIN LLM, KEINE DB-Mutation.

  A) Die Pruefregel
  B) Ohne Primaerquelle gibt es keine Freigabe
  C) Die drei benannten Grenzfaelle
  D) Tabellenkonsistenz und Trennung von der Fachquellen-Tabelle

    python test_kba_generation_quellen.py
"""
import sys

from app.kba_generation_audit import (
    GENERATION_CONFIRMED, GENERATIONEN, SUCCESSOR_RECALL,
)
from app.kba_generation_quellen import (
    PRIMAERQUELLEN, SOURCE_CONFIRMED, SOURCE_CONTRADICTED,
    SOURCE_CROSS_GENERATION, SOURCE_KLASSEN, SOURCE_UNCLEAR, pruefe,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, info: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f"   {info}" if info else ""))
    if not bedingung:
        _FEHLER.append(name)


def p(von, bis, ziel, start, fach=GENERATION_CONFIRMED):
    return pruefe(von, bis, ziel, start, fach)[0]


# ══════════════════════════════════════════════════════════════════════════════
print("=" * 60)
print("A) PRUEFREGEL")
print("=" * 60)

# Tiguan II: Primaerquelle nennt den Anlauf des Nachfolgers (2023).
T = "volkswagen-tiguan-ii"
check("A1 Fenster endet vor dem Nachfolgeanlauf -> SOURCE_CONFIRMED",
      p(2019, 2022, T, 2016) == SOURCE_CONFIRMED)
check("A2 Fenster beginnt im Anlaufjahr -> SOURCE_CONTRADICTED",
      p(2023, 2024, T, 2016) == SOURCE_CONTRADICTED)
check("A3 Fenster ueberspannt den Anlauf -> SOURCE_CROSS_GENERATION",
      p(2022, 2024, T, 2016) == SOURCE_CROSS_GENERATION)
check("A4 Fenster beginnt vor dem hinterlegten Start -> SOURCE_CROSS_GENERATION",
      p(2015, 2018, T, 2016) == SOURCE_CROSS_GENERATION)

# C-Klasse W206: Primaerquelle belegt laufende Produktion bis 2026.
C = "mercedes-benz-c-klasse-w206"
check("A5 laufende Produktion, Fenster darin -> SOURCE_CONFIRMED",
      p(2021, 2025, C, 2021) == SOURCE_CONFIRMED)
check("A6 laufende Produktion, Fenster darueber hinaus -> SOURCE_UNCLEAR",
      p(2021, 2027, C, 2021) == SOURCE_UNCLEAR)

check("A7 unbrauchbares Fenster -> SOURCE_UNCLEAR",
      p(2021, None, C, 2021) == SOURCE_UNCLEAR)
check("A8 inverses Fenster -> SOURCE_UNCLEAR",
      p(2022, 2021, C, 2021) == SOURCE_UNCLEAR)


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("B) KEINE FREIGABE OHNE PRIMAERQUELLE")
print("=" * 60)

# Der Ford Kuga hat eine Fachquelle, aber KEINE Herstellerquelle.
K = "ford-kuga-mk3"
check("B1 Baureihe mit Fachquelle, ohne Primaerquelle -> SOURCE_UNCLEAR",
      p(2019, 2020, K, 2019) == SOURCE_UNCLEAR)
check("B1b und sie steht wirklich in der Fachquellen-Tabelle",
      K in GENERATIONEN and K not in PRIMAERQUELLEN)
check("B2 voellig unbekannte Baureihe -> SOURCE_UNCLEAR",
      p(2020, 2021, "gibt-es-nicht", 2018) == SOURCE_UNCLEAR)
_grund = pruefe(2020, 2021, "gibt-es-nicht", 2018, GENERATION_CONFIRMED)[1]
check("B3 die Begruendung benennt die fehlende Primaerquelle",
      "primaer" in _grund.lower(), _grund)


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("C) DIE DREI BENANNTEN GRENZFAELLE")
print("=" * 60)

check("C1 BMW iX3 G08, amtliches Fenster 2025-2026 -> SOURCE_CONTRADICTED",
      p(2025, 2026, "bmw-ix3-g08", 2020, SUCCESSOR_RECALL) == SOURCE_CONTRADICTED)
check("C2 Audi Q3 II, Fenster 2025-2026 -> SOURCE_CONTRADICTED",
      p(2025, 2026, "audi-q3-ii", 2018, SUCCESSOR_RECALL) == SOURCE_CONTRADICTED)
check("C2b Audi Q3 II, Fenster 2020-2022 -> SOURCE_CONFIRMED",
      p(2020, 2022, "audi-q3-ii", 2018) == SOURCE_CONFIRMED)
check("C3 VW T-Roc A1, Fenster 2023-2023 -> SOURCE_CONFIRMED",
      p(2023, 2023, "volkswagen-t-roc-a1", 2017) == SOURCE_CONFIRMED)
check("C3b VW T-Roc A1, Fenster 2025-2026 -> SOURCE_CONTRADICTED",
      p(2025, 2026, "volkswagen-t-roc-a1", 2017) == SOURCE_CONTRADICTED)
check("C4 alle drei haben eine Primaerquelle der Stufe 1",
      all(PRIMAERQUELLEN[b]["stufe"] == 1
          for b in ("bmw-ix3-g08", "audi-q3-ii", "volkswagen-t-roc-a1")))


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("D) TABELLENKONSISTENZ")
print("=" * 60)

check("D1 jede Primaerquelle nennt eine URL",
      all(v["url"].startswith("https://") for v in PRIMAERQUELLEN.values()))
check("D2 jede Primaerquelle nennt einen Beleg",
      all(len(v["beleg"].strip()) > 30 for v in PRIMAERQUELLEN.values()))
check("D3 jede Primaerquelle traegt Stufe 1-3 (Fachquellen gehoeren hier nicht her)",
      all(v["stufe"] in (1, 2, 3) for v in PRIMAERQUELLEN.values()))
check("D4 jede Primaerquelle belegt Nachfolgeanlauf ODER laufende Produktion",
      all(v["nachfolger_ab"] is not None or v["in_produktion_bis"] is not None
          for v in PRIMAERQUELLEN.values()))
check("D5 die Primaerquellen-Tabelle ist eine Teilmenge der Fachquellen-Tabelle",
      set(PRIMAERQUELLEN) <= set(GENERATIONEN),
      str(sorted(set(PRIMAERQUELLEN) - set(GENERATIONEN))))
_konflikt = [b for b in PRIMAERQUELLEN
             if PRIMAERQUELLEN[b]["nachfolger_ab"] is not None
             and GENERATIONEN[b][1] is not None
             and PRIMAERQUELLEN[b]["nachfolger_ab"] != GENERATIONEN[b][1]]
check("D6 Primaerquelle und Fachquelle widersprechen sich beim "
      "Nachfolgeanlauf nicht", not _konflikt, str(_konflikt))
check("D7 die Klassenliste ist vollstaendig", len(SOURCE_KLASSEN) == 4)
check("D8 SOURCE_CROSS_GENERATION ist erreichbar",
      SOURCE_CROSS_GENERATION in SOURCE_KLASSEN)


# ══════════════════════════════════════════════════════════════════════════════
print()
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    sys.exit(1)
print("ALLE PRIMAERQUELLEN-TESTS GRUEN")
