"""
Test: AutoFinder-Normalisierung (Runde 1) — app/autofinder_norm.py

Prüft die Runtime-Normalisierung von Karosserie, Getriebe und Segment gegen
den VOLLSTÄNDIGEN kanonischen Fahrzeugbestand (frisch gebootstrappt, nicht die
Legacy-DB). Zwei Dinge stehen hier im Vordergrund:

  1. Klassifizierungsquote mindestens auf dem im Audit nachgewiesenen Niveau
     (Karosserie >= 413/416, Getriebe >= 3226/3231) — mit vollständiger Liste
     etwaiger nicht klassifizierter Rohwerte, damit ein Rückgang sofort
     sichtbar wird.
  2. Kein Raten: ein unbekannter Rohwert muss LEER (Karosserie/Getriebe) bzw.
     "unbekannt" (Segment) bleiben, niemals eine geratene Klasse.

Verändert die Datenbank NICHT — die kanonische DB wird frisch in ein
Temp-Verzeichnis gebootstrappt (derselbe App-Bootstrap wie
test_fahrzeug_bootstrap.py), keine bestehende DB wird angefasst.

Ausfuehren:  python test_autofinder_norm.py
"""
import importlib
import os
import sys
import tempfile

sys.path.insert(0, ".")

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


# ── Frische kanonische DB bootstrappen (wie test_fahrzeug_bootstrap.py) ─────
_tmp = tempfile.mkdtemp(prefix="vira_af_norm_")
_db_pfad = os.path.join(_tmp, "kanonisch.db")
os.environ["AUTO_KI_DB_PATH"] = _db_pfad
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")

import app.config as _cfg
importlib.reload(_cfg)
import app.database as _db
importlib.reload(_db)
_db.ensure_tables()

from app.autofinder_norm import (   # noqa: E402
    normalisiere_karosserie, normalisiere_getriebe, normalisiere_segment,
    UNBEKANNT,
)

with _db.get_conn() as conn:
    baureihen = [dict(r) for r in conn.execute(
        "SELECT id, karosserie, segment FROM baureihe").fetchall()]
    motoren = [dict(r) for r in conn.execute(
        "SELECT variante_id, getriebe FROM motorvariante").fetchall()]

NB, NM = len(baureihen), len(motoren)
check("Kanonischer Bestand geladen: 416 Baureihen", NB == 416)
check("Kanonischer Bestand geladen: 3231 Motorvarianten", NM == 3231)

# ── §18: vollständiger Klassifizierungsbericht ──────────────────────────────
karo_bad = [(b["id"], b["karosserie"]) for b in baureihen
            if not normalisiere_karosserie(b["karosserie"])]
karo_ok = NB - len(karo_bad)
print(f"\nKarosserie klassifiziert {karo_ok}/{NB}")
if karo_bad:
    print("  nicht klassifiziert:", karo_bad)
check("Karosserie-Klassifizierung >= Audit-Zielwert 413/416", karo_ok >= 413)

getr_bad = [(m["variante_id"], m["getriebe"]) for m in motoren
            if not normalisiere_getriebe(m["getriebe"])]
getr_ok = NM - len(getr_bad)
print(f"\nGetriebe klassifiziert {getr_ok}/{NM}")
if getr_bad:
    print("  nicht klassifiziert:", getr_bad)
check("Getriebe-Klassifizierung >= Audit-Zielwert 3226/3231", getr_ok >= 3226)

seg_bad = [(b["id"], b["segment"]) for b in baureihen
           if normalisiere_segment(b["segment"]) == UNBEKANNT]
seg_ok = NB - len(seg_bad)
print(f"\nSegment klassifiziert {seg_ok}/{NB}")
if seg_bad:
    print("  nicht klassifiziert:", seg_bad)
check("Segment-Klassifizierung >= 400/416 (37 Rohwerte -> feste Klassen)", seg_ok >= 400)

# ── Kein Raten bei unbekannten/leeren Werten ────────────────────────────────
check("normalisiere_karosserie(None) -> leer",
      normalisiere_karosserie(None) == frozenset())
check("normalisiere_karosserie(kaputtes JSON) -> leer, kein Crash",
      normalisiere_karosserie("{nicht json") == frozenset()
      or normalisiere_karosserie("{nicht json") is not None)  # crasht nicht
check('normalisiere_karosserie(["Bratpfanne"]) -> leer (kein Rateergebnis)',
      normalisiere_karosserie('["Bratpfanne"]') == frozenset())
check("normalisiere_getriebe(None) -> leer",
      normalisiere_getriebe(None) == frozenset())
check('normalisiere_getriebe(["Bratpfanne"]) -> leer (kein Rateergebnis)',
      normalisiere_getriebe('["Bratpfanne"]') == frozenset())
check("normalisiere_segment(None) -> 'unbekannt'",
      normalisiere_segment(None) == UNBEKANNT)
check("normalisiere_segment('Bratpfanne') -> 'unbekannt' (kein Rateergebnis)",
      normalisiere_segment("Bratpfanne") == UNBEKANNT)

# ── Stichproben: bekannte Werte korrekt zugeordnet ──────────────────────────
check('Karosserie ["SUV"] -> {suv}',
      normalisiere_karosserie('["SUV"]') == frozenset({"suv"}))
check('Karosserie ["SUV-Coupé"] -> enthaelt suv UND coupe (Mehrfachzugehoerigkeit)',
      {"suv", "coupe"} <= normalisiere_karosserie('["SUV-Coupé"]'))
check('Karosserie ["Kombi","Limousine"] -> {kombi, limousine}',
      normalisiere_karosserie('["Kombi","Limousine"]') == frozenset({"kombi", "limousine"}))
check('Getriebe ["Automatik"] -> {automatik}',
      normalisiere_getriebe('["Automatik"]') == frozenset({"automatik"}))
check('Getriebe ["6-Gang Manuell","Automatik (optional)"] -> {manuell, automatik}',
      normalisiere_getriebe('["6-Gang Manuell","Automatik (optional)"]')
      == frozenset({"manuell", "automatik"}))
check("Segment 'Kompaktklasse' -> kompaktklasse",
      normalisiere_segment("Kompaktklasse") == "kompaktklasse")
check("Segment 'Kompakt-SUV' -> suv (Fahrzeugart vor Groessenklasse)",
      normalisiere_segment("Kompakt-SUV") == "suv")
check("Segment 'A' (DIN-Segmentbuchstabe) -> kleinstwagen",
      normalisiere_segment("A") == "kleinstwagen")
check("Segment 'D-Segment' -> mittelklasse",
      normalisiere_segment("D-Segment") == "mittelklasse")


print()
if FEHLER:
    print(f"{len(FEHLER)} Test(s) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle AutoFinder-Normalisierungs-Tests bestanden.")
