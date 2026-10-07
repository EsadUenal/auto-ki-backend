"""
Root-Cause-Audit RC-3 — konditionale Admission fuer amtlich eingegrenzte,
aber baureihen-sichere Paare.

Synthetische Marken/Modelle (keine realen Fahrzeuge als Testgegenstand).

  A) Baureihen-sicheres Paar mit echter Eingrenzung wird konditional admittiert
  B) Generations-mehrdeutiges Paar bleibt review-only (keine Baureihen-Garantie)
  C) A3/A4/A0 gelten unveraendert auch im konditionalen Pfad
  D) Die unbedingten Pfade (SAFE_IMPORT) bleiben unberuehrt

    python test_kba_conditional_scope.py
"""
from app.kba_conditional_scope import (
    _nicht_abbildbare_eingrenzung, ergaenzende_konditionale_zeilen,
    paare_konditionale_eingrenzung,
)
from app.kba_import_kandidaten import SAFE_IMPORT, VARIANT_SCOPE_UNCLEAR, import_kandidaten

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def kba_zeile(**kw) -> dict:
    z = {
        "KBA-Referenznummer": "50001", "Rückrufcode des Herstellers": "ABC",
        "Veröffentlichungsdatum": "2020-05-01", "Marke": "TESTMARKE",
        "Modell": "GAMMA",
        "Mangelbezeichnung": "Durch Feuchtigkeitseintritt in den Riemenstartergenerator "
                            "kann es zu Kurzschluss kommen.",
        "Produktionszeitraum von": "2017", "Produktionszeitraum bis": "2020",
        "Beschreibung der Maßnahme": "Austausch des Generators.",
        "Mögliche Eingrenzung der betroffenen Modelle":
            "Es sind ausschließlich Fahrzeuge mit 2.0 TFSI und Mild-Hybrid-System betroffen",
        "Überwachung der Rückrufaktion durch das KBA": "überwacht",
    }
    z.update(kw)
    return z


def br(**kw) -> dict:
    b = {"id": "testmarke-gamma-g1", "marke": "Testmarke", "modell": "Gamma",
         "generation": "G1", "bauzeitraum_von": 2015, "bauzeitraum_bis": 2023}
    b.update(kw)
    return b


# ══ A) Baureihen-sicheres Paar mit echter Eingrenzung ═══════════════════════
print("\n--- A) Baureihen-sicheres Paar wird konditional admittiert ---")
_baureihen = [br()]
_kand = import_kandidaten([kba_zeile()], [], _baureihen)
# Hinweis: `klassifiziere_kandidat()`s `kand.variantenbeschraenkung`-Heuristik
# (fuer die Dry-Run-Anzeige) haelt dieses Paar wegen des Worts "Hybrid" fuer
# "ueber den Kraftstoff-Klammerzusatz aufloesbar" und meldet es deshalb als
# SAFE_IMPORT, OBWOHL der volle Satz ("...2.0 TFSI UND Mild-Hybrid-System...")
# tatsaechlich nicht abbildbar ist — Tor A2 selbst (siehe `_nicht_abbildbare_
# eingrenzung`) ist strenger und erkennt das korrekt. Genau DIESE Diskrepanz
# ist der Grund, warum `paare_konditionale_eingrenzung()` nicht blind auf
# `kl == VARIANT_SCOPE_UNCLEAR` vertraut (siehe dessen Docstring).
check("A0 Vorbedingung: die kandidatenweite Dry-Run-Klasse ist SAFE_IMPORT "
      "(bekannte Heuristik-Luecke von klassifiziere_kandidat — siehe oben), "
      "aber Tor A2 selbst erkennt die Eingrenzung trotzdem als nicht trivial",
      len(_kand) == 1 and _kand[0].klasse == SAFE_IMPORT
      and _nicht_abbildbare_eingrenzung(_kand[0]))

_vorauswahl = paare_konditionale_eingrenzung(_kand)
check("A1 das Paar (Referenz, testmarke-gamma-g1) ist in der Vorauswahl",
      _vorauswahl.get(_kand[0]) == ["testmarke-gamma-g1"])

_zeilen, _ausschluesse = ergaenzende_konditionale_zeilen(_kand, _baureihen, [])
check("A2 genau EINE konditionale Zeile wird vorgeschlagen", len(_zeilen) == 1)
check("A3 die Zeile traegt den rohen amtlichen Eingrenzungstext",
      bool(_zeilen) and "Mild-Hybrid" in _zeilen[0]["eingrenzung_amtlich"])
check("A4 die Zeile traegt das amtliche (ungeschnittene) Produktionsfenster",
      bool(_zeilen) and _zeilen[0]["prod_von_amtlich"] == 2017
      and _zeilen[0]["prod_bis_amtlich"] == 2020)
check("A5 keine Ausschluesse fuer dieses saubere Paar", _ausschluesse == [])


# ══ B) Generations-mehrdeutiges Paar bleibt review-only ═════════════════════
print("\n--- B) Baureihen-unsicheres Paar wird NICHT konditional admittiert ---")
_baureihen_ambig = [
    br(id="testmarke-gamma-g1", bauzeitraum_von=2015, bauzeitraum_bis=2023),
    # Ueberdeckung des amtlichen Fensters (2017-2020) durch g0: 2017-2019 = 3
    # von 4 Jahren = 75 % >= 2/3-Schwelle -> echte, plausible Alternative (wie
    # der reale RS-4-Avant-B9-Fall im Hauptaudit).
    br(id="testmarke-gamma-g0", generation="G0", bauzeitraum_von=2010, bauzeitraum_bis=2019),
]
_kand_ambig = import_kandidaten([kba_zeile(**{"KBA-Referenznummer": "50002"})], [],
                                _baureihen_ambig)
_vorauswahl_ambig = paare_konditionale_eingrenzung(_kand_ambig)
check("B1 ein Paar, dessen Baureihen-Zuordnung mehrdeutig ist (zwei Generationen "
      "im amtlichen Fenster), taucht NICHT in der konditionalen Vorauswahl auf "
      "(RC-4-Sicherheitsgrenze: nur baureihen-sichere Paare)",
      not any("testmarke-gamma-g1" in ziele or "testmarke-gamma-g0" in ziele
              for ziele in _vorauswahl_ambig.values()))
_zeilen_ambig, _ = ergaenzende_konditionale_zeilen(_kand_ambig, _baureihen_ambig, [])
check("B2 entsprechend keine konditionale Zeile fuer das mehrdeutige Paar",
      _zeilen_ambig == [])


# ══ C) A3/A4/A0 gelten unveraendert ══════════════════════════════════════════
print("\n--- C) bestehende Sicherheitstore bleiben im konditionalen Pfad wirksam ---")
# A3: eine BESTEHENDE kanonische Zeile OHNE eigene vertrauenswuerdige Referenz
# (Altbestand) mit identischem Mangeltext auf derselben Baureihe. Zwei Zeilen
# mit je EIGENER, unterschiedlicher, format-plausibler Referenz waeren nach
# `_a3_dublette()`s Regel 2 ausdruecklich KEINE Dublette (die amtliche Referenz
# ist die starke Identitaet, Fix aus `98104df`) — der Textabgleich ist der
# Rueckfall ausschliesslich fuer referenzlose Altzeilen.
_bestehend = [{"id": 1, "baureihe_id": "testmarke-gamma-g1", "datum": "2019-01",
              "betroffene_baujahre": "2017-2020",
              "mangel": kba_zeile()["Mangelbezeichnung"], "abhilfe": "Austausch des Generators.",
              "kba_referenz": None}]
_kand_dup = import_kandidaten([kba_zeile(**{"KBA-Referenznummer": "50011"})],
                              _bestehend, _baureihen)
_zeilen_dup, _ausschluesse_dup = ergaenzende_konditionale_zeilen(_kand_dup, _baureihen,
                                                                 _bestehend)
check("C1 A3-Dublettenschutz greift auch im konditionalen Pfad (gleicher Mangeltext "
      "+ Zeitraum auf derselben Baureihe -> keine Zeile)",
      _zeilen_dup == [] and any("A3" in a[-1] for a in _ausschluesse_dup))

# A0: fehlendes Produktionsdatum
_kand_a0 = import_kandidaten(
    [kba_zeile(**{"KBA-Referenznummer": "50003", "Produktionszeitraum von": "",
                 "Produktionszeitraum bis": ""})], [], _baureihen)
if _kand_a0:
    _zeilen_a0, _ausschluesse_a0 = ergaenzende_konditionale_zeilen(_kand_a0, _baureihen, [])
    check("C2 A0 (fehlendes Produktionsfenster) blockiert auch den konditionalen Pfad",
          _zeilen_a0 == [])
else:
    check("C2 A0 (fehlendes Produktionsfenster) — Kandidat erreicht die "
          "Klassifikation gar nicht erst (ebenfalls sicher)", True)


# ══ D) Unbedingte SAFE_IMPORT-Paare bleiben unberuehrt ══════════════════════
print("\n--- D) SAFE_IMPORT-Paare (triviale Eingrenzung) bleiben vom neuen Pfad unberuehrt ---")
_kand_safe = import_kandidaten(
    [kba_zeile(**{"KBA-Referenznummer": "50004",
                 "Mögliche Eingrenzung der betroffenen Modelle": "N/A"})],
    [], _baureihen)
check("D0 Vorbedingung: dieser Kandidat ist ganz normal SAFE_IMPORT",
      len(_kand_safe) == 1 and _kand_safe[0].klasse == SAFE_IMPORT)
_vorauswahl_safe = paare_konditionale_eingrenzung(_kand_safe)
check("D1 ein SAFE_IMPORT-Paar taucht nicht in der konditionalen Vorauswahl auf "
      "(es braucht den neuen Pfad nicht — es ist bereits unbedingt sicher)",
      _vorauswahl_safe == {})


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE KBA-CONDITIONAL-SCOPE-TESTS GRUEN")
