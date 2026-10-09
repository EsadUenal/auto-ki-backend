"""
RC-W6 — Canonical-only KBA-Kandidaten (app.kba_canonical_import).

Fixture-basiert, deterministisch, KEIN echter KBA-Export, KEIN Netzwerk,
KEINE DB-Mutation. Die amtlichen Spaltennamen entsprechen exakt dem echten
KBA-CSV-Export (dieselben, die app.kba_import_kandidaten.ImportKandidat
bereits liest).

    python test_kba_canonical_import.py
"""
from app.kba_canonical_import import (
    CANONICAL_NOT_SAFETY_RELEVANT, CANONICAL_REVIEW, CANONICAL_SAFE_IMPORT,
    canonical_kandidaten, zaehle_klassen, zeile_fuer_insert,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def kba_zeile(referenz, marke, modell, mangel, *, von="2017", bis="2020",
              ueberwacht="überwacht", eingrenzung="", massnahme="Austausch",
              datum="2022-01-28") -> dict:
    return {
        "KBA-Referenznummer": referenz, "Marke": marke, "Modell": modell,
        "Mangelbezeichnung": mangel, "Beschreibung der Maßnahme": massnahme,
        "Produktionszeitraum von": von, "Produktionszeitraum bis": bis,
        "Mögliche Eingrenzung der betroffenen Modelle": eingrenzung,
        "Überwachung der Rückrufaktion durch das KBA": ueberwacht,
        "Veröffentlichungsdatum": datum,
    }


VIRA_MARKEN = {"BMW", "AUDI", "VW"}   # entspricht kba_marke()-normalisierten Werten


# ══════════════════════════════════════════════════════════════════════════
print("\n=== A) Katalogisierte Marke -> KEIN canonical-only Kandidat ===")
_a = [kba_zeile("1", "BMW", "3", "Bremse kann versagen")]
_kands_a = canonical_kandidaten(_a, VIRA_MARKEN)
check("A1 BMW ist katalogisiert -> bestehender Baureihen-Pfad, kein canonical-Kandidat",
      _kands_a == [])


# ══════════════════════════════════════════════════════════════════════════
print("\n=== B) Nicht katalogisierte Marke, sicherheitsrelevant -> CANONICAL_SAFE_IMPORT ===")
_b = [kba_zeile("11362", "MAZDA", "MX-5",
               "Aufgrund einer fehlerhaften Kraftstoffpumpe kann es zu einem Motorausfall kommen.")]
_kands_b = canonical_kandidaten(_b, VIRA_MARKEN)
check("B1 genau ein Kandidat", len(_kands_b) == 1)
if _kands_b:
    check("B2 klasse = CANONICAL_SAFE_IMPORT", _kands_b[0].klasse == CANONICAL_SAFE_IMPORT)
    check("B3 canonical_make = 'MAZDA'", _kands_b[0].canonical_make == "MAZDA")
    check("B4 canonical_nameplate = 'MX-5'", _kands_b[0].canonical_nameplate == "MX-5")
    zeile = zeile_fuer_insert(_kands_b[0])
    check("B5 zeile_fuer_insert: baureihe_id ist None", zeile["baureihe_id"] is None)
    check("B6 zeile_fuer_insert: betroffene_baujahre = '2017-2020'",
          zeile["betroffene_baujahre"] == "2017-2020")


# ══════════════════════════════════════════════════════════════════════════
print("\n=== C) Nicht sicherheitsrelevant -> CANONICAL_NOT_SAFETY_RELEVANT (nicht verworfen) ===")
_c = [kba_zeile("14795R", "MAZDA", "MX-5", "Fehlfunktion Einparkhilfe")]
_kands_c = canonical_kandidaten(_c, VIRA_MARKEN)
check("C1 Kandidat bleibt sichtbar (kein stiller Drop)", len(_kands_c) == 1)
if _kands_c:
    check("C2 klasse = CANONICAL_NOT_SAFETY_RELEVANT",
          _kands_c[0].klasse == CANONICAL_NOT_SAFETY_RELEVANT)
    check("C3 Begründung vorhanden (auditierbar)", bool(_kands_c[0].begruendung))


# ══════════════════════════════════════════════════════════════════════════
print("\n=== D) Weder überwacht noch sicherheitsrelevant -> stiller Drop (unverändertes Verhalten) ===")
_d = [kba_zeile("999", "MAZDA", "MX-5", "Kosmetischer Lackfehler", ueberwacht="nicht überwacht")]
_kands_d = canonical_kandidaten(_d, VIRA_MARKEN)
check("D1 kein Kandidat (dieselbe Vorpruefung wie import_kandidaten, Zeile 616)",
      _kands_d == [])


# ══════════════════════════════════════════════════════════════════════════
print("\n=== E) Mehrmodellige amtliche Zeile -> JE TOKEN ein eigener Kandidat ===")
_e = [kba_zeile("8038", "MAZDA", "2, CX-3, MX-5",
               "Fehlerhafte Steuerung des Gleichstromwandlers kann zum Kurzschluss führen.")]
_kands_e = canonical_kandidaten(_e, VIRA_MARKEN)
check("E1 genau 3 Kandidaten (einer je Modelltoken)", len(_kands_e) == 3)
check("E2 Nameplates sind '2', 'CX-3', 'MX-5' -- nie als ein Blob",
      {k.canonical_nameplate for k in _kands_e} == {"2", "CX-3", "MX-5"})
check("E3 alle drei tragen dieselbe KBA-Referenz",
      all(k.referenz == "8038" for k in _kands_e))


# ══════════════════════════════════════════════════════════════════════════
print("\n=== F) Unabbildbare Variantenbedingung -> CANONICAL_REVIEW, kein stiller Drop ===")
_f = [kba_zeile("777", "MAZDA", "MX-5", "Der Bremsschlauch kann undicht werden.",
               eingrenzung="Nur Fahrzeuge mit 2,0 Liter Motorcode ABC123")]
_kands_f = canonical_kandidaten(_f, VIRA_MARKEN)
check("F1 Kandidat bleibt sichtbar", len(_kands_f) == 1)
if _kands_f:
    check("F2 klasse = CANONICAL_REVIEW (unabbildbare Eingrenzung)",
          _kands_f[0].klasse == CANONICAL_REVIEW)


# ══════════════════════════════════════════════════════════════════════════
print("\n=== G) Dedupe: bereits importiertes Paar wird NICHT erneut vorgeschlagen ===")
_g_quelle = [kba_zeile("11362", "MAZDA", "MX-5", "Kraftstoffpumpe")]
_bestand = {("11362", "MAZDA", "MX-5")}
_kands_g = canonical_kandidaten(_g_quelle, VIRA_MARKEN, _bestand)
check("G1 bereits importiertes Paar -> 0 neue Kandidaten (Idempotenz)", _kands_g == [])

# Dieselbe Referenz, ANDERES Nameplate -> bleibt eigener Kandidat (nicht
# versehentlich mitunterdrückt).
_g2_quelle = [kba_zeile("11362", "MAZDA", "MX-5, CX-5", "Kraftstoffpumpe")]
_kands_g2 = canonical_kandidaten(_g2_quelle, VIRA_MARKEN, _bestand)
check("G2 anderes Nameplate derselben Referenz bleibt eigener Kandidat (nicht verschmolzen)",
      len(_kands_g2) == 1 and _kands_g2[0].canonical_nameplate == "CX-5")


# ══════════════════════════════════════════════════════════════════════════
print("\n=== H) zaehle_klassen() ===")
_h = _a + _b + _c + _d + _e + _f  # type: ignore[operator]
_alle = canonical_kandidaten(_g_quelle + _b + _c + _f, VIRA_MARKEN)
zaehlung = zaehle_klassen(_alle)
check("H1 Zählung stimmt mit tatsächlicher Klassenverteilung überein",
      sum(zaehlung.values()) == len(_alle)
      and zaehlung[CANONICAL_SAFE_IMPORT] == sum(1 for k in _alle if k.klasse == CANONICAL_SAFE_IMPORT))


print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE KBA-CANONICAL-IMPORT-TESTS GRUEN")
