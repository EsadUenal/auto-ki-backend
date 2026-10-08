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
  H) Mehrfach erreichbare Baureihen — Determinismus bei Alias-Token
  I) RC-5 — "nicht sicherheitsrelevant" darf nie mehr stumm verwerfen

    python test_kba_import_dryrun.py [pfad/zum/kba_export.csv]
"""
import os
import sys

from app.kba_import_kandidaten import (
    AMBIGUOUS_GENERATION, IMPORT_KLASSEN, MEDIAN_GENERATIONSDAUER,
    MIN_UEBERDECKUNG, NOT_SAFETY_RELEVANT, POSSIBLE_DUPLICATE, SAFE_IMPORT,
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
# Audit RC-5 (Root-Cause-Closing): vor dem Fix war dies ein stiller `continue`
# — DER exakte Mechanismus, der die beiden belegten Audi-Anhaengevorrichtungs-
# Rueckrufe KBA 8718/10703 nie klassifiziert, nie in `kba_rueckruf_review`
# sichtbar gemacht hat. "Nicht sicherheitsrelevant" ist jetzt eine EXPLIZITE,
# auditierbare Klasse — der Kandidat verschwindet nicht, er wird nur nicht
# automatisch uebernommen (siehe Abschnitt I fuer die ausfuehrlichen Tests).
check("B5 nicht sicherheitsrelevant -> EIN Kandidat mit expliziter Klasse "
      "(kein stiller Drop mehr)",
      [k.klasse for k in import_kandidaten([kba_zeile(
          Mangelbezeichnung="Das Radio zeigt die falsche Uhrzeit an.",
          **{"Beschreibung der Maßnahme": "Software-Update."})],
          [], [br()])] == [NOT_SAFETY_RELEVANT])

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


# ══ H) Mehrfach erreichbare Baureihen (Determinismus, KBA-Paar-Closing) ═══════
print("\n--- H) Mehrfach erreichbare Baureihen ---")
# Realer Fall aus dem KBA-Gesamtexport (u.a. KBA 13099, Modell "A3, S3, Q2, RS3"):
# `MODELL_MAP[("AUDI", "RS 3 SPORTBACK")] = {"RS 3", "RS3", "A3"}` indiziert die
# RS3-Baureihe zusaetzlich unter dem breiten Alias "A3". Der Token "A3" trifft
# dadurch ZWEI Baureihen, der Token "RS3" trifft NUR die RS3 — dieselbe
# Baureihe ist also ueber zwei Token erreichbar, von denen nur einer mehrdeutig
# ist. Vor dem KBA-Paar-Closing-Fix entschied die zufaellige Set-Iterations-
# reihenfolge von `_modelltokens()`, WELCHER der beiden Token zuerst
# verarbeitet wurde, und damit ob das Paar (KBA-Referenz, RS3) als SAFE_IMPORT
# oder als AMBIGUOUS_GENERATION galt — bei GLEICHEN Eingabedaten, je nach
# Prozessstart (PYTHONHASHSEED). Gemessen: 11 amtliche Datensaetze im
# KBA-Gesamtexport vom 2026-08-27 sind auf diese Weise betroffen.
#
# Match-Staerke (Ebene A, Audi-A4-B9-Root-Cause-Fund, s. app.kba_reconciliation.
# match_tier): derselbe Mechanismus wie bei A4/RS4-Avant — "A3" ist der EIGENE,
# kanonische Name von `audi-a3-8v` (Tier 1, direkter Nameplate-Treffer), aber
# fuer `audi-rs-3-sportback-8v` NUR ueber die `MODELL_MAP`-Alias-Zuordnung
# erreichbar (Tier 2). `audi-a3-8v` gewinnt den Token "A3" deshalb jetzt
# eindeutig — NICHT durch einen Sonderfall, sondern durch dieselbe generische
# Regel, die auch KBA 9831/10206 fuer `audi-a4-b9` aufloest (s. Abschnitt I7/I7c
# unten). `audi-rs-3-sportback-8v` bleibt davon unberuehrt: es gewinnt "RS3"
# weiterhin als einziger Insasse (unveraendert, H2).
_a3 = br(id="audi-a3-8v", marke="Audi", modell="A3", generation="8V",
         bauzeitraum_von=2012, bauzeitraum_bis=2020)
_rs3 = br(id="audi-rs-3-sportback-8v", marke="Audi", modell="RS 3 Sportback",
          generation="8V", bauzeitraum_von=2015, bauzeitraum_bis=2020)
_rs3_kba = kba_zeile(Marke="AUDI", Modell="A3, S3, Q2, RS3",
                     **{"Produktionszeitraum von": "2018",
                        "Produktionszeitraum bis": "2019"})


def _paare_von(kba_rows, baureihen):
    k = import_kandidaten(kba_rows, [], baureihen)
    return {bid: kl for bid, kl, _g in k[0].paare} if k else {}


_paare_h = _paare_von([_rs3_kba], [_a3, _rs3])
check("H1 die A3-Baureihe gewinnt jetzt den Token 'A3' eindeutig (Ebene A: "
      "direkter Nameplate-Treffer schlaegt RS3s Alias-Treffer)",
      _paare_h.get("audi-a3-8v") == SAFE_IMPORT)
check("H2 die RS3-Baureihe ist SAFE_IMPORT (zusaetzlich ueber 'RS3' eindeutig "
      "erreichbar)",
      _paare_h.get("audi-rs-3-sportback-8v") == SAFE_IMPORT)

# Dieselben Daten, Modell-Token in umgekehrter Reihenfolge im amtlichen Text:
# das Ergebnis muss BYTEGLEICH bleiben (Determinismus ist keine Frage der
# Eingabereihenfolge und keine Frage des Zufalls).
_paare_h_rev = _paare_von(
    [kba_zeile(Marke="AUDI", Modell="RS3, Q2, S3, A3",
              **{"Produktionszeitraum von": "2018",
                 "Produktionszeitraum bis": "2019"})],
    [_a3, _rs3])
check("H3 gleiches Ergebnis bei umgekehrter Token-Reihenfolge im amtlichen Text",
      _paare_h == _paare_h_rev)

# Zehn Wiederholungen im selben Prozess: die Aggregation ist jetzt
# mengenbasiert (nicht mehr "wer zuerst kommt"), das Ergebnis darf nie kippen.
check("H4 zehn Wiederholungen liefern immer dasselbe Ergebnis",
      all(_paare_von([_rs3_kba], [_a3, _rs3]) == _paare_h for _ in range(10)))


# ══ I) RC-5 — "nicht sicherheitsrelevant" darf nie mehr stumm verwerfen ═══════
print("\n--- I) RC-5: kein stiller Drop mehr ---")

# I1: generische (nicht markengebundene) Bauteilgruppen-Erweiterung — JEDE
# Marke mit einer Anhaengevorrichtung/-kupplung profitiert, nicht nur Audi.
check("I1 'anhaenger'-Bauteilgruppe (generisch, jede Marke) macht sicherheitsrelevant "
      "-> wird klassifiziert, nicht NOT_SAFETY_RELEVANT",
      klasse_von([kba_zeile(
          Marke="SKODA", Modell="OCTAVIA",
          Mangelbezeichnung="Bruch der Anhaengerkupplung moeglich.")],
          [], [br(id="skoda-octavia", marke="Skoda", modell="Octavia")])
      != NOT_SAFETY_RELEVANT)

# I2: die neue Konsequenz-Regel ("Verlust ... Verbindung/Kupplung/Befestigung/
# Halterung") isoliert geprueft — FIKTIVES Bauteil, das KEINER bestehenden
# Bauteilgruppe entspricht, damit ausschliesslich der Folge-Pfad greift.
_fiktiv_verlust = kba_zeile(
    Mangelbezeichnung="Bruch der Dachtraeger-Halteklammer kann zum Verlust der "
                      "Verbindung zum Dachgepaecktraeger fuehren.")
check("I2 generische 'Verlust der Verbindung'-Folge wird erkannt, auch ohne "
      "bekannte Bauteilgruppe",
      klasse_von([_fiktiv_verlust], [], [br()]) != NOT_SAFETY_RELEVANT)
check("I2b ... tatsaechlich ueber die FOLGE, nicht ueber eine Bauteilgruppe "
      "(isolierter Nachweis)",
      not (bauteilgruppen_fn := __import__(
          "app.kba_reconciliation", fromlist=["bauteilgruppen"]
      ).bauteilgruppen)(_fiktiv_verlust["Mangelbezeichnung"]) & {
          "airbag", "gurt", "bremse_hydr", "bremse_mech", "bremse_elektr",
          "lenkung", "fahrwerk", "rad", "hochvolt", "elektrik_brand",
          "kraftstoff", "anhaenger"})

# I3/I4: ein echtes Komfort-/Infotainment-Beispiel bleibt korrekt NICHT
# sicherheitsrelevant — der Filter wurde nicht einfach abgeschaltet — UND
# die Ablehnung ist jetzt auditierbar (eigene Klasse + Begruendung).
_komfort = import_kandidaten([kba_zeile(
    Mangelbezeichnung="Das Radio zeigt die falsche Uhrzeit an.",
    **{"Beschreibung der Maßnahme": "Software-Update."})], [], [br()])
check("I3 echtes Komfortbeispiel bleibt NOT_SAFETY_RELEVANT (Filter nicht "
      "einfach abgeschaltet)",
      len(_komfort) == 1 and _komfort[0].klasse == NOT_SAFETY_RELEVANT)
check("I4 die Ablehnung traegt eine nachvollziehbare Begruendung",
      bool(_komfort) and "keine" in _komfort[0].begruendung
      and _komfort[0].klasse != SAFE_IMPORT)

# I5: kein bare-drop-Pfad mehr — fuer JEDEN ueberwachten, markenbekannten
# Kandidaten liefert import_kandidaten() mindestens ein Ergebnis.
for _label, _zeile in (
    ("Komfort", kba_zeile(Mangelbezeichnung="Das Radio zeigt die falsche "
                                            "Uhrzeit an.")),
    ("Sicherheitsrelevant", kba_zeile()),
    ("Anhaenger", kba_zeile(Mangelbezeichnung="Bruch der Anhaengerkupplung.")),
    ("Verlust-Verbindung", _fiktiv_verlust),
):
    check(f"I5 kein stiller Drop ({_label})",
          len(import_kandidaten([_zeile], [], [br()])) == 1)

# I6: Reihenfolge-Unabhaengigkeit bei gemischten Klassen (sicherheitsrelevant
# UND nicht-sicherheitsrelevant im selben Lauf).
_gemischt = [kba_zeile(**{"KBA-Referenznummer": "9001"}),
            kba_zeile(**{"KBA-Referenznummer": "9002"},
                      Mangelbezeichnung="Das Radio zeigt die falsche Uhrzeit an."),
            kba_zeile(**{"KBA-Referenznummer": "9003"},
                      Mangelbezeichnung="Bruch der Anhaengerkupplung.")]
_g1 = {k.referenz: k.klasse for k in import_kandidaten(_gemischt, [], [br()])}
_g2 = {k.referenz: k.klasse for k in import_kandidaten(list(reversed(_gemischt)), [], [br()])}
check("I6 gemischte Klassen sind reihenfolge-unabhaengig", _g1 == _g2)
check("I6b alle drei Referenzen sind vertreten (keine verschwindet)",
      set(_g1) == {"9001", "9002", "9003"})

# I7/I8: die beiden REALEN, belegten Audi-Anhaengevorrichtungs-Rueckrufe
# (KBA 8718/10703) als benannte Regressions-Fixtures — exakte Feldwerte aus
# dem amtlichen KBA-Gesamtexport (abgerufen fuer den Root-Cause-Audit dieser
# Serie), NICHT als Laufzeit-Bedingung irgendwo im Produktcode verwendet.
# Minimaler Baureihenbestand: audi-a4-b9 ist nur ueber den Token "A4"
# erreichbar, der auch die offene RS-4-Avant-B9-Generation trifft (Audi
# fuehrt RS4 amtlich haeufig schlicht als "A4", siehe Abschnitt H fuer
# denselben Mechanismus bei RS3/A3).
#
# Match-Staerke (Ebene A, Audi-A4-B9-Root-Cause-Fund): "A4" ist der EIGENE
# Name von audi-a4-b9 (Tier 1), fuer audi-rs-4-avant-b9 dagegen nur per
# MODELL_MAP-Alias erreichbar (Tier 2) — fuer BEIDE Referenzen (8718 UND
# 9831/10206, die eigentlichen Proof-Faelle dieses Fixes) identisch. Ob
# audi-rs-4-avant-b9 dabei ueberhaupt als Mitbewerber antritt, haengt NICHT
# von der Match-Staerke ab, sondern vom VORGESCHALTETEN Ueberdeckungs-Filter
# (MIN_UEBERDECKUNG, 2/3): 8718s schmales Fenster (2018-2018) wird von der
# offenen RS4-Avant-Generation zu 100 % abgedeckt -> Mitbewerb, Ebene A
# entscheidet -> audi-a4-b9 gewinnt. 10703s breiteres Fenster (2015-2018)
# wird dagegen nur zu 50 % abgedeckt (< 2/3-Schwelle) -> RS4-Avant tritt gar
# nicht erst an, audi-a4-b9 ist von vornherein der einzige Insasse (I8b,
# unveraendert). Zwei unabhaengige Gates, zufaellig unterschiedlicher
# Ausgang je nach Fensterbreite — kein Sonderfall fuer 8718 oder 10703.
_audi_a4_familie = [
    br(id="audi-a4-b9", marke="Audi", modell="A4", generation="B9",
       bauzeitraum_von=2015, bauzeitraum_bis=2023),
    br(id="audi-rs-4-avant-b9", marke="Audi", modell="RS 4 Avant",
       generation="B9", bauzeitraum_von=2017, bauzeitraum_bis=None),
]
_kba_8718 = kba_zeile(
    **{"KBA-Referenznummer": "8718", "Rückrufcode des Herstellers": "66K3"},
    Marke="AUDI", Modell="A6, A7, A4, A5",
    Mangelbezeichnung="Bruch des Sperrbolzens der Anhängevorrichtung kann "
                      "zum Verlust der Fahrzeugverbindung führen.",
    **{"Produktionszeitraum von": "2018", "Produktionszeitraum bis": "2018",
       "Beschreibung der Maßnahme": "Prüfung, ob im Schwenkmechanismus ein "
                                     "Sperrbolzen aus der betroffenen Charge "
                                     "verbaut ist, falls ja, erfolgt der "
                                     "Austausch des gesamten Schwenkmoduls",
       "Mögliche Eingrenzung der betroffenen Modelle": "keine"})
_kba_10703 = kba_zeile(
    **{"KBA-Referenznummer": "10703", "Rückrufcode des Herstellers": "66M7"},
    Marke="AUDI", Modell="A6, A7, A4, A5",
    Mangelbezeichnung="Bruch des Sperrbolzens der Anhängevorrichtung kann "
                      "zum Verlust der Fahrzeugverbindung führen.",
    **{"Produktionszeitraum von": "2015", "Produktionszeitraum bis": "2018",
       "Beschreibung der Maßnahme": "Überprüfung und ggf. Austausch der "
                                     "Anhängevorrichtung",
       "Mögliche Eingrenzung der betroffenen Modelle": "keine"})

_r8718 = import_kandidaten([_kba_8718], [], _audi_a4_familie)
check("I7 KBA 8718 verschwindet nicht mehr (mindestens ein Kandidat)",
      len(_r8718) == 1)
check("I7b KBA 8718 ist jetzt sicherheitsrelevant klassifiziert ('anhaenger' "
      "ist keine NOT_SAFETY_RELEVANT-Ablehnung mehr)",
      _r8718[0].klasse != NOT_SAFETY_RELEVANT)
_a4_paar_8718 = dict((bid, kl) for bid, kl, _g in _r8718[0].paare)
check("I7c das Paar (8718, audi-a4-b9) gewinnt jetzt den Token 'A4' eindeutig "
      "(Ebene A: direkter Nameplate-Treffer schlaegt RS4-Avants Alias-Treffer "
      "— dieselbe Regel, die auch 9831/10206 aufloest, kein Sonderfall fuer "
      "8718)",
      _a4_paar_8718.get("audi-a4-b9") == SAFE_IMPORT)
check("I7d das Paar (8718, audi-rs-4-avant-b9) bleibt korrekt AMBIGUOUS_GENERATION "
      "— die schwaechere Alternative wird NICHT ausgeschlossen, nur nicht "
      "kanonisch",
      _a4_paar_8718.get("audi-rs-4-avant-b9") == AMBIGUOUS_GENERATION)

_r10703 = import_kandidaten([_kba_10703], [], _audi_a4_familie)
check("I8 KBA 10703 verschwindet nicht mehr (mindestens ein Kandidat)",
      len(_r10703) == 1)
_a4_paar_10703 = dict((bid, kl) for bid, kl, _g in _r10703[0].paare)
check("I8b das Paar (10703, audi-a4-b9) ist SAFE_IMPORT — fuer DIESES "
      "breitere Produktionsfenster (2015-2018) deckt die offene "
      "RS-4-Avant-B9-Generation nur 50 % ab (< 2/3-Schwelle) und faellt "
      "damit nicht als Alternative ins Gewicht; audi-a4-b9 ist eindeutig",
      _a4_paar_10703.get("audi-a4-b9") == SAFE_IMPORT)


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE KBA-IMPORT-DRYRUN-TESTS GRUEN")
