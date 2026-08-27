"""
KBA-IMPORT DRY-RUN — Zusicherungen.
KEIN Netzwerk, KEIN LLM-Call, KEINE Tavily-Calls, KEINE DB-Mutation.

Der Klassifikator wird gegen feste Fixtures geprueft, nicht gegen den
Live-Export: der amtliche Bestand aendert sich taeglich, ein Test darf davon
nicht abhaengen. Nur Abschnitt G prueft die beiden namentlich benannten Faelle
gegen die echte Datenbank — und auch das nur, wenn ein Export bereitliegt.

  A) Determinismus
  B) Zielaufloesung und Generationseindeutigkeit
  C) Offene Generationen werden nicht ueberdehnt
  D) Randueberlappung reicht nicht
  E) Variantenbeschraenkung
  F) Dublettenschutz — vorhandene Rueckrufe werden nicht erneut importiert
  G) Reale Bezugsfaelle (nur mit Export)

    python test_kba_import_dryrun.py [pfad/zum/kba_export.csv]
"""
import os
import sys

from app.kba_import_kandidaten import (
    AMBIGUOUS_GENERATION, IMPORT_KLASSEN, MEDIAN_GENERATIONSDAUER,
    MIN_UEBERDECKUNG, POSSIBLE_DUPLICATE, SAFE_IMPORT,
    UNSUPPORTED_MODEL_MAPPING, VARIANT_SCOPE_UNCLEAR, _ueberdeckung,
    import_kandidaten, zeilen_bei_import,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def kba_zeile(**kw) -> dict:
    z = {
        "KBA-Referenznummer": "9001", "Rückrufcode des Herstellers": "ABC",
        "Veröffentlichungsdatum": "2020-05-01", "Marke": "OPEL",
        "Modell": "INSIGNIA",
        "Mangelbezeichnung": "Die Lenkspindel kann brechen.",
        "Produktionszeitraum von": "2018", "Produktionszeitraum bis": "2020",
        "Beschreibung der Maßnahme": "Austausch der Lenkspindel.",
        "Mögliche Eingrenzung der betroffenen Modelle": "N/A",
        "Überwachung der Rückrufaktion durch das KBA": "überwacht",
    }
    z.update(kw)
    return z


def br(**kw) -> dict:
    b = {"id": "opel-insignia-b", "marke": "Opel", "modell": "Insignia",
         "generation": "B", "bauzeitraum_von": 2017, "bauzeitraum_bis": 2022}
    b.update(kw)
    return b


def rr(**kw) -> dict:
    r = {"id": 1, "baureihe_id": "opel-insignia-b", "datum": "2020-05",
         "betroffene_baujahre": "2018-2020", "mangel": "", "abhilfe": "",
         "kba_referenz": None}
    r.update(kw)
    return r


def klasse_von(kba_rows, recalls, baureihen):
    k = import_kandidaten(kba_rows, recalls, baureihen)
    return k[0].klasse if k else None


# ══ A) Determinismus ═════════════════════════════════════════════════════════
print("\n--- A) Determinismus ---")
_kba = [kba_zeile(**{"KBA-Referenznummer": r}) for r in ("9003", "9001", "9002")]
_a = import_kandidaten(_kba, [], [br()])
_b = import_kandidaten(list(reversed(_kba)), [], [br()])
check("A1 gleiche Eingabe -> gleiche Klassen",
      [x.klasse for x in _a] == [x.klasse for x in _b])
check("A2 Reihenfolge ist stabil (nach Klasse und Referenz sortiert)",
      [x.referenz for x in _a] == [x.referenz for x in _b] == ["9001", "9002", "9003"])
check("A3 alle Klassen sind bekannt",
      all(x.klasse in IMPORT_KLASSEN for x in _a))


# ══ B) Zielaufloesung ════════════════════════════════════════════════════════
print("\n--- B) Zielaufloesung ---")
check("B1 eindeutige Baureihe -> SAFE_IMPORT",
      klasse_von([kba_zeile()], [], [br()]) == SAFE_IMPORT)
check("B2 kein VIRA-Ziel -> UNSUPPORTED_MODEL_MAPPING",
      klasse_von([kba_zeile(Modell="MOVANO")], [], [br()])
      == UNSUPPORTED_MODEL_MAPPING)
check("B3 Marke nicht in VIRA -> gar kein Kandidat",
      import_kandidaten([kba_zeile(Marke="FERRARI")], [], [br()]) == [])
check("B4 nicht ueberwacht -> gar kein Kandidat",
      import_kandidaten([kba_zeile(
          **{"Überwachung der Rückrufaktion durch das KBA": "nicht überwacht"})],
          [], [br()]) == [])
check("B5 nicht sicherheitsrelevant -> gar kein Kandidat",
      import_kandidaten([kba_zeile(
          Mangelbezeichnung="Das Radio zeigt die falsche Uhrzeit an.",
          **{"Beschreibung der Maßnahme": "Software-Update."})],
          [], [br()]) == [])

# Zwei Generationen desselben Modells im amtlichen Fenster
_zwei_gen = [br(id="opel-insignia-a", generation="A",
                bauzeitraum_von=2008, bauzeitraum_bis=2017),
             br(id="opel-insignia-b", bauzeitraum_von=2017, bauzeitraum_bis=2022)]
check("B6 zwei VIRA-Generationen im Fenster -> AMBIGUOUS_GENERATION",
      klasse_von([kba_zeile(**{"Produktionszeitraum von": "2016",
                               "Produktionszeitraum bis": "2018"})],
                 [], _zwei_gen) == AMBIGUOUS_GENERATION)

# Zwei verschiedene MODELLE sind dagegen eindeutig — je eine VIRA-Zeile.
_zwei_modelle = [br(id="bmw-x5-e70", marke="BMW", modell="X5", generation="E70",
                    bauzeitraum_von=2006, bauzeitraum_bis=2013),
                 br(id="bmw-x6-e71", marke="BMW", modell="X6", generation="E71",
                    bauzeitraum_von=2008, bauzeitraum_bis=2014)]
_mehrmodell = import_kandidaten(
    [kba_zeile(Marke="BMW", Modell="X5, X6",
               **{"Produktionszeitraum von": "2009",
                  "Produktionszeitraum bis": "2012"})], [], _zwei_modelle)
check("B7 ein Rueckruf ueber zwei MODELLE bleibt SAFE_IMPORT",
      _mehrmodell and _mehrmodell[0].klasse == SAFE_IMPORT)
check("B8 und erzeugt zwei VIRA-Zeilen (Importeinheit ist das Paar)",
      _mehrmodell and len(_mehrmodell[0].ziel_ids) == 2
      and zeilen_bei_import(_mehrmodell) == 2)


# ══ C) Offene Generationen ═══════════════════════════════════════════════════
print("\n--- C) Offene Generationen ---")
_offen = [br(id="vw-t-roc-a1", marke="Volkswagen", modell="T-Roc", generation="A1",
             bauzeitraum_von=2017, bauzeitraum_bis=None)]
check(f"C1 offene Generation + Rueckruf mehr als {MEDIAN_GENERATIONSDAUER} Jahre "
      f"spaeter -> AMBIGUOUS_GENERATION",
      klasse_von([kba_zeile(Marke="VW", Modell="T-ROC",
                            **{"Produktionszeitraum von": "2025",
                               "Produktionszeitraum bis": "2026"})],
                 [], _offen) == AMBIGUOUS_GENERATION)
check("C2 offene Generation + Rueckruf innerhalb der Median-Laufzeit -> SAFE_IMPORT",
      klasse_von([kba_zeile(Marke="VW", Modell="T-ROC",
                            **{"Produktionszeitraum von": "2019",
                               "Produktionszeitraum bis": "2020"})],
                 [], _offen) == SAFE_IMPORT)
check("C3 geschlossene Generation ist von der Regel nicht betroffen",
      klasse_von([kba_zeile(**{"Produktionszeitraum von": "2021",
                               "Produktionszeitraum bis": "2022"})],
                 [], [br()]) == SAFE_IMPORT)


# ══ D) Randueberlappung ══════════════════════════════════════════════════════
print("\n--- D) Randueberlappung ---")
check("D1 _ueberdeckung: amtliches Fenster ganz in der Baureihe -> 1.0",
      _ueberdeckung(2018, 2020, 2017, 2022) == 1.0)
check("D2 _ueberdeckung: nur ein Randjahr von sechs -> rund 17 %",
      abs(_ueberdeckung(2015, 2020, 2006, 2015) - 1 / 6) < 0.01)
check("D3 _ueberdeckung: haelftige Ueberlappung -> 0.5",
      _ueberdeckung(2017, 2018, 2011, 2017) == 0.5)
check(f"D4 Schwelle liegt bei {MIN_UEBERDECKUNG:.0%}",
      abs(MIN_UEBERDECKUNG - 2 / 3) < 0.001)

_galaxy = [br(id="ford-galaxy-2", marke="Ford", modell="Galaxy",
              generation="II", bauzeitraum_von=2006, bauzeitraum_bis=2015)]
check("D5 nur Randueberlappung -> AMBIGUOUS_GENERATION statt Fehlzuordnung",
      klasse_von([kba_zeile(Marke="FORD", Modell="GALAXY",
                            **{"Produktionszeitraum von": "2015",
                               "Produktionszeitraum bis": "2020"})],
                 [], _galaxy) == AMBIGUOUS_GENERATION)
check("D6 volle Ueberdeckung derselben Baureihe -> SAFE_IMPORT",
      klasse_von([kba_zeile(Marke="FORD", Modell="GALAXY",
                            **{"Produktionszeitraum von": "2010",
                               "Produktionszeitraum bis": "2014"})],
                 [], _galaxy) == SAFE_IMPORT)


# ══ E) Variantenbeschraenkung ════════════════════════════════════════════════
print("\n--- E) Variantenbeschraenkung ---")
for _eingr in ("Ausschließlich Fahrzeuge mit Direkt-Schalt-Getriebe (DSG)",
               "FIN-Endnummern-Bereich: 840110 bis 858840",
               "AMG 4MATIC",
               "Audi 4,0l TFSI"):
    check(f"E1 {_eingr[:38]!r} -> VARIANT_SCOPE_UNCLEAR",
          klasse_von([kba_zeile(
              **{"Mögliche Eingrenzung der betroffenen Modelle": _eingr})],
              [], [br()]) == VARIANT_SCOPE_UNCLEAR)

check("E2 eine reine KRAFTSTOFF-Eingrenzung ist abbildbar -> SAFE_IMPORT",
      klasse_von([kba_zeile(
          **{"Mögliche Eingrenzung der betroffenen Modelle": "nur Dieselfahrzeuge"})],
          [], [br()]) == SAFE_IMPORT)
check("E3 'N/A' ist keine Eingrenzung",
      klasse_von([kba_zeile()], [], [br()]) == SAFE_IMPORT)


# ══ F) Dublettenschutz ═══════════════════════════════════════════════════════
print("\n--- F) Dublettenschutz ---")
check("F1 gleiche amtliche Referenz bereits im Bestand -> gar kein Kandidat",
      import_kandidaten([kba_zeile()], [rr(kba_referenz="9001")], [br()]) == [])
check("F2 auch in Sekundaerschreibweise mit fuehrender Null",
      import_kandidaten([kba_zeile()], [rr(kba_referenz="009001")], [br()]) == [])

_gleicher_mangel = rr(mangel="Bruch der Lenkspindel moeglich.",
                      abhilfe="Lenkspindel austauschen.",
                      betroffene_baujahre="2018-2020")
check("F3 gleiche starke Bauteilgruppe + Zeitraum + Begriffe -> POSSIBLE_DUPLICATE",
      klasse_von([kba_zeile()], [_gleicher_mangel], [br()]) == POSSIBLE_DUPLICATE)

# Die Sammelgruppe elektrik_brand darf ALLEIN keine Dublette begruenden.
_brand_amtlich = kba_zeile(
    Mangelbezeichnung="Verformung der Wasserkastendichtung kann Brandgefahr ausloesen.",
    **{"Beschreibung der Maßnahme": "Dichtung ersetzen."})
_brand_vira = rr(mangel="Undichtigkeit an der Kraftstoffleitung, Brandgefahr.",
                 abhilfe="Leitung ersetzen.", betroffene_baujahre="2018-2020")
check("F4 nur die Sammelgruppe 'Brandgefahr' gemeinsam -> KEINE Dublette",
      klasse_von([_brand_amtlich], [_brand_vira], [br()]) != POSSIBLE_DUPLICATE)

_anderer_zeitraum = rr(mangel="Bruch der Lenkspindel moeglich.",
                       abhilfe="Lenkspindel austauschen.",
                       betroffene_baujahre="2005-2008")
check("F5 gleicher Mangel, aber Zeitraum weit daneben -> keine Dublette",
      klasse_von([kba_zeile()], [_anderer_zeitraum], [br()]) != POSSIBLE_DUPLICATE)


# ══ G) Reale Bezugsfaelle ════════════════════════════════════════════════════
print("\n--- G) Reale Bezugsfaelle ---")
_export = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("KBA_EXPORT")
if not _export or not os.path.exists(_export):
    print("       (uebersprungen — kein KBA-Export angegeben; "
          "Aufruf: python test_kba_import_dryrun.py <export.csv>)")
else:
    import sqlite3

    from app.kba_reconciliation import lade_kba
    _kba_alle = lade_kba(_export)
    _p = os.path.expandvars(r"%LOCALAPPDATA%\auto-ki-backend\auto_ki.db")
    _c = sqlite3.connect(_p)
    _c.row_factory = sqlite3.Row
    _recalls = [dict(r) for r in _c.execute("select * from rueckruf")]
    _brs = [dict(r) for r in _c.execute("select * from baureihe")]
    _c.close()
    _real = {k.referenz: k for k in import_kandidaten(_kba_alle, _recalls, _brs)}

    _troc = _real.get("16132R")
    check("G1 VW T-Roc 16132R (Ausfall Lenkung) ist ein Kandidat", _troc is not None)
    check("G2 und wird wegen der OFFENEN Generation nicht blind importiert",
          bool(_troc) and _troc.klasse == AMBIGUOUS_GENERATION)

    _ix3 = _real.get("16565R")
    check("G3 BMW iX3 16565R (Stromschlaggefahr Hochvolt) ist ein Kandidat",
          _ix3 is not None)
    # BEKANNTE GRENZE, bewusst als Test festgehalten: der iX3 G08 (ab 2020) hat
    # 2025 nach nur fuenf Jahren einen Nachfolger bekommen. Die Median-Regel
    # (7 Jahre) faengt das nicht ab. Der Fall bleibt SAFE_IMPORT, obwohl er sehr
    # wahrscheinlich das neue Modell betrifft — genau deshalb darf SAFE_IMPORT
    # nicht ungeprueft geschrieben werden.
    check("G4 iX3 bleibt SAFE_IMPORT — dokumentierte Grenze der Median-Regel",
          bool(_ix3) and _ix3.klasse == SAFE_IMPORT)
    check("G5 kein bereits vorhandener Rueckruf taucht als Kandidat auf",
          not ({(r.get("kba_referenz") or "").strip() for r in _recalls
                if (r.get("kba_referenz") or "").strip()} & set(_real)))
    check("G6 jede SAFE_IMPORT-Vorhersage ist series_only (nie confirmed_by_vin)",
          all(k.applicability == "series_only" for k in _real.values()
              if k.klasse == SAFE_IMPORT))


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE KBA-IMPORT-DRYRUN-TESTS GRUEN")
