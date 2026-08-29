"""
Reliability-Sprint §5/§15 A — Ersatzteil-Kompatibilität.

Deterministisch, KEIN Netzwerk. Prüft die strukturierte Einstufung confirmed/
uncertain/rejected und die Regel "nur confirmed darf empfohlen werden".

    python test_ersatzteil_kompat.py
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from app.ersatzteil_kompat import parse_fahrzeug, parse_bauteil, klassifiziere
from app.routers.ersatzteile import _bewerte_kompatibilitaet

_fails = []
def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        _fails.append(name)

def klass(fahrzeug, bauteil, produkt):
    return klassifiziere(parse_fahrzeug(fahrzeug), parse_bauteil(bauteil), produkt)[0]


# ── A1) M3 E92 + Bremsscheiben vorne: normaler E92 NICHT empfohlen ──────────────
k = klass("BMW M3 E92", "Bremsscheiben vorne", "Bremsscheiben BMW 3er E92 320d Vorderachse")
check("A1: normale E92-Bremsscheibe (320d) beim M3 E92 -> rejected", k == "rejected")

k = klass("BMW M3 E92", "Bremsscheiben vorne", "Bremsscheiben Satz BMW M3 E92 Vorderachse 360mm")
check("A1: M3-E92-Webtreffer ohne autoritative Fitmentquelle -> uncertain", k == "uncertain")

k = klass("BMW M3 E92", "Bremsscheiben vorne", "Bremsscheiben vorne für BMW E92")
check("A1: reine E92-Angabe ohne M3 -> nicht confirmed (uncertain)", k == "uncertain")

# ── A2) Normales E92-Modell: passende normale Teile möglich, M3-Teil abgelehnt ──
k = klass("BMW 320i E92", "Bremsscheiben vorne", "Bremsscheiben BMW 3er E92 320i Vorderachse")
check("A2: normale E92-Bremsscheibe ohne Fitmentbeweis -> uncertain", k == "uncertain")

k = klass("BMW 320i E92", "Bremsscheiben vorne", "Bremsscheiben BMW M3 E92 Hochleistung Vorderachse")
check("A2: M3-Bremsscheibe beim normalen 320i E92 -> rejected", k == "rejected")

# ── A3) Performance-Modell anderer Marke (Mercedes-AMG) ────────────────────────
k = klass("Mercedes-AMG C63 W205", "Bremsscheiben vorne", "Bremsscheiben Mercedes C-Klasse W205 C220d Vorderachse")
check("A3: normale C220d-Bremsscheibe beim C63 AMG -> rejected", k == "rejected")

k = klass("Mercedes-AMG C63 W205", "Bremsscheiben vorne", "Bremsscheiben Mercedes-AMG C63 W205 Vorderachse")
check("A3: AMG-C63-Webtreffer ohne Fitmentbeweis -> uncertain", k == "uncertain")

# "AMG Line" ist KEIN Performance-Modell -> darf ein Standardmodell nicht als Perf verwerfen
k = klass("Mercedes C200 W205 AMG Line", "Bremsscheiben vorne", "Bremsscheiben Mercedes C-Klasse W205 C200 Vorderachse")
check("A3: 'AMG Line' bleibt Standardmodell, aber ohne Fitmentbeweis uncertain", k == "uncertain")

# ── A4) Achse vorne/hinten nicht vermischen ────────────────────────────────────
k = klass("BMW 320i E92", "Bremsscheiben vorne", "Bremsscheiben BMW E92 Hinterachse")
check("A4: gesuchte Vorderachse, Produkt Hinterachse -> rejected", k == "rejected")

k = klass("BMW 320i E92", "Bremsscheiben hinten", "Bremsscheiben BMW E92 Hinterachse")
check("A4: gleiche Achse ohne Fitmentbeweis -> uncertain", k == "uncertain")

# ── Hersteller-Widerspruch ─────────────────────────────────────────────────────
k = klass("BMW 320i E92", "Bremsscheiben vorne", "Bremsscheiben Audi A4 B8 Vorderachse")
check("Hersteller-Widerspruch (Audi-Teil beim BMW) -> rejected", k == "rejected")

# ── Reliability-Sprint 3 §3/§6/§30: GTS/CRT-Sonderedition NICHT automatisch
#    confirmed für den normalen M3, und M-Nummern nicht mehr familien-kollabiert ──
k = klass("BMW M3 E92", "Bremsscheiben vorne", "ATE Bremsscheiben BMW E92 M3 GTS/CRT Vorderachse 370mm")
check("Sprint3: GTS/CRT-only-Teil beim normalen M3 -> NICHT confirmed (uncertain)", k == "uncertain")

k = klass("BMW M3 E92", "Bremsscheiben vorne", "Bremsscheiben BMW M4 F82 Vorderachse")
check("Sprint3: M4-Teil beim M3-Ziel -> rejected (M2..M8 nicht mehr kollabiert)", k == "rejected")

k = klass("Audi RS6 C7", "Bremsscheiben vorne", "Bremsscheiben Audi RS3 8V Vorderachse")
check("Sprint3: RS3-Teil beim RS6-Ziel -> rejected (RS-Nummern nicht kollabiert)", k == "rejected")

k = klass("BMW M3 E92 GTS", "Bremsscheiben vorne", "ATE Bremsscheiben BMW M3 E92 Vorderachse 360mm")
check("Sprint3: normales M3-Teil beim GTS-Zielfahrzeug -> NICHT confirmed (uncertain)", k == "uncertain")

# End-to-End (§6): Ohne autoritative Parts-Fitmentquelle darf weder das GTS/CRT-
# Teil noch der plausible M3-Webtreffer als VIRAs Empfehlung erscheinen.
ergebnisse_gts = [
    {"teilename": "ATE Bremsscheiben BMW E92 M3 GTS/CRT Vorderachse 370mm",
     "passt_fahrzeug": "BMW E92 M3 GTS/CRT", "hinweis": "", "url": "https://autodoc.de/gts", "preis_eur": 45},
    {"teilename": "ATE Bremsscheiben BMW M3 E92 Vorderachse 360mm",
     "passt_fahrzeug": "BMW M3 E92", "hinweis": "", "url": "https://autodoc.de/m3", "preis_eur": 210},
]
sichtbar_gts, idx_gts = _bewerte_kompatibilitaet("BMW M3 E92", "Bremsscheiben vorne", ergebnisse_gts)
check("Sprint3: ohne autoritative Fitmentquelle wird kein Treffer empfohlen",
      idx_gts is None)
check("Sprint3: plausibler M3-Treffer bleibt sichtbar, aber uncertain",
      any(e["teilename"].endswith("360mm") and e.get("kompatibilitaet") == "uncertain"
          for e in sichtbar_gts))
check("Sprint3: GTS/CRT-Teil bleibt sichtbar, aber als uncertain markiert",
      any("GTS" in e["teilename"] and e.get("kompatibilitaet") == "uncertain" for e in sichtbar_gts))

# ── A5) Unsichere Kompatibilität NICHT als empfohlen ───────────────────────────
ergebnisse = [
    {"teilename": "Bremsscheiben vorne", "passt_fahrzeug": "", "hinweis": "", "url": "https://autodoc.de/x1", "preis_eur": 60},
    {"teilename": "Bremsscheiben BMW 3er E92 320d Vorderachse", "passt_fahrzeug": "BMW E92 320d", "hinweis": "", "url": "https://autodoc.de/x2", "preis_eur": 55},
    {"teilename": "Bremsscheiben BMW M3 E92 Vorderachse 360mm", "passt_fahrzeug": "BMW M3 E92", "hinweis": "", "url": "https://autodoc.de/x3", "preis_eur": 210},
]
sichtbar, idx = _bewerte_kompatibilitaet("BMW M3 E92", "Bremsscheiben vorne", ergebnisse)
titel_sichtbar = [e["teilename"] for e in sichtbar]
check("A5: normale 320d-Bremsscheibe (rejected) wird ausgeblendet",
      not any("320d" in t for t in titel_sichtbar))
check("A5: auch der plausible M3-Webtreffer bleibt ohne Fitmentbeweis uncertain",
      idx is None and any("M3" in e["teilename"] and e.get("kompatibilitaet") == "uncertain"
                          for e in sichtbar))
check("A5: unspezifisches Teil bleibt sichtbar, aber uncertain (nicht empfohlen)",
      any(e.get("kompatibilitaet") == "uncertain" for e in sichtbar))

# Ohne einen einzigen confirmed-Treffer -> kein empfohlener Index
ergebnisse2 = [
    {"teilename": "Bremsscheiben vorne universal", "passt_fahrzeug": "", "hinweis": "", "url": "https://autodoc.de/y1", "preis_eur": 50},
]
sichtbar2, idx2 = _bewerte_kompatibilitaet("BMW M3 E92", "Bremsscheiben vorne", ergebnisse2)
check("A5: nur unsichere Treffer -> empfohlener_index None", idx2 is None)

print()
if _fails:
    print(f"{len(_fails)} Test(s) fehlgeschlagen.")
    sys.exit(1)
print("Alle Ersatzteil-Kompatibilitäts-Tests bestanden.")
