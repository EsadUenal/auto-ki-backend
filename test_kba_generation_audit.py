"""
GENERATIONSAUDIT RISIKOKLASSE B — Zusicherungen.
KEIN Netzwerk, KEIN LLM, KEINE DB-Mutation.

  A) Die Klassifikationsregel selbst
  B) Ohne recherchierte Grenze gibt es keine Freigabe
  C) Die drei namentlich benannten kritischen Muster
  D) Die Tabelle ist formal konsistent

    python test_kba_generation_audit.py
"""
import sys

from app.kba_generation_audit import (
    CROSS_GENERATION, GENERATION_CONFIRMED, GENERATION_UNCLEAR, GENERATIONEN,
    KLASSEN, SUCCESSOR_RECALL, klassifiziere,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, info: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f"   {info}" if info else ""))
    if not bedingung:
        _FEHLER.append(name)


def kl(von, bis, ziel, start):
    return klassifiziere(von, bis, ziel, start)[0]


# ══════════════════════════════════════════════════════════════════════════════
print("=" * 60)
print("A) KLASSIFIKATIONSREGEL")
print("=" * 60)

# Der Tiguan II ist der klarste Fall: belegtes Ende 2024, Nachfolger ab 2024.
T = "volkswagen-tiguan-ii"
check("A1 Fenster endet vor dem Anlauf des Nachfolgers -> CONFIRMED",
      kl(2019, 2022, T, 2016) == GENERATION_CONFIRMED)
check("A2 Fenster beginnt im Anlaufjahr des Nachfolgers -> SUCCESSOR_RECALL",
      kl(2024, 2025, T, 2016) == SUCCESSOR_RECALL)
check("A3 Fenster ueberspannt den Wechsel -> CROSS_GENERATION",
      kl(2022, 2025, T, 2016) == CROSS_GENERATION)
check("A4 Fenster beginnt vor dem hinterlegten Generationsstart -> CROSS_GENERATION",
      kl(2015, 2018, T, 2016) == CROSS_GENERATION)

# Ohne bekannten Nachfolger entscheidet das Produktionsende.
F = "ford-focus-mk4"          # Ende 2025, kein Nachfolger
check("A5 kein Nachfolger, Fenster innerhalb der Produktion -> CONFIRMED",
      kl(2020, 2023, F, 2018) == GENERATION_CONFIRMED)
check("A6 kein Nachfolger, Fenster reicht ueber das Produktionsende hinaus "
      "-> CROSS_GENERATION",
      kl(2020, 2026, F, 2018) == CROSS_GENERATION)

# Laufende Generation ohne Ende und ohne Nachfolger.
P = "volkswagen-polo-vi"
check("A7 laufende Generation ohne Nachfolger -> CONFIRMED",
      kl(2022, 2025, P, 2017) == GENERATION_CONFIRMED)

check("A8 unvollstaendiges Fenster -> UNCLEAR",
      kl(2020, None, T, 2016) == GENERATION_UNCLEAR)
check("A9 inverses Fenster -> UNCLEAR",
      kl(2021, 2020, T, 2016) == GENERATION_UNCLEAR)


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("B) KEINE FREIGABE OHNE QUELLE")
print("=" * 60)

check("B1 unbekannte Baureihe -> UNCLEAR",
      kl(2020, 2021, "gibt-es-nicht", 2018) == GENERATION_UNCLEAR)
check("B2 und niemals CONFIRMED",
      kl(2020, 2021, "gibt-es-nicht", 2018) != GENERATION_CONFIRMED)
_grund = klassifiziere(2020, 2021, "gibt-es-nicht", 2018)[1]
check("B3 die Begruendung benennt die fehlende Quelle",
      "quelle" in _grund.lower(), _grund)


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("C) DIE DREI KRITISCHEN MUSTER")
print("=" * 60)

# BMW iX3 G08: Produktion Q2/2025 beendet, Nachfolger NA5 ab Ende Oktober 2025.
check("C1 BMW iX3 G08, amtliches Fenster 2025-2026 -> SUCCESSOR_RECALL",
      kl(2025, 2026, "bmw-ix3-g08", 2020) == SUCCESSOR_RECALL)
check("C1b derselbe iX3 mit Fenster 2021-2022 -> CONFIRMED",
      kl(2021, 2022, "bmw-ix3-g08", 2020) == GENERATION_CONFIRMED)

# Audi Q3 F3: 2018-2025, Nachfolger FJ ab Sommer 2025.
check("C2 Audi Q3 II, amtliches Fenster 2025-2026 -> SUCCESSOR_RECALL",
      kl(2025, 2026, "audi-q3-ii", 2018) == SUCCESSOR_RECALL)
check("C2b Audi Q3 II mit Fenster 2020-2022 -> CONFIRMED",
      kl(2020, 2022, "audi-q3-ii", 2018) == GENERATION_CONFIRMED)

# VW T-Roc A1: Nachfolger ab November 2025.
check("C3 VW T-Roc A1, Fenster 2023-2023 -> CONFIRMED",
      kl(2023, 2023, "volkswagen-t-roc-a1", 2017) == GENERATION_CONFIRMED)
check("C3b VW T-Roc A1, Fenster 2025-2026 -> SUCCESSOR_RECALL",
      kl(2025, 2026, "volkswagen-t-roc-a1", 2017) == SUCCESSOR_RECALL)


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("D) TABELLENKONSISTENZ")
print("=" * 60)

check("D1 jede Baureihe traegt vier Felder",
      all(len(v) == 4 for v in GENERATIONEN.values()))
check("D2 jede Baureihe nennt eine Quelle",
      all((v[2] or "").strip() for v in GENERATIONEN.values()))
check("D3 jede Baureihe traegt eine Notiz",
      all((v[3] or "").strip() for v in GENERATIONEN.values()))
_jahre = [j for v in GENERATIONEN.values() for j in v[:2] if j is not None]
check("D4 alle Jahresangaben sind plausibel (1990-2035)",
      all(1990 <= j <= 2035 for j in _jahre), f"n={len(_jahre)}")
_widerspruch = [k for k, v in GENERATIONEN.items()
                if v[0] is not None and v[1] is not None and v[1] > v[0] + 2]
check("D5 kein Nachfolger startet mehr als zwei Jahre nach dem Produktionsende "
      "(sonst klafft eine unbelegte Luecke)", not _widerspruch, str(_widerspruch))
check("D6 die Klassenliste ist vollstaendig", len(KLASSEN) == 4)


# ══════════════════════════════════════════════════════════════════════════════
print()
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    sys.exit(1)
print("ALLE GENERATIONSAUDIT-TESTS GRUEN")
