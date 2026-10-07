"""
Root-Cause-Audit RC-4/S1 — Paarweise Admission fuer OFFENE UND GESCHLOSSENE
Generationen (Vereinheitlichung von "Mechanismus B").

Synthetische Marken/Modelle fuer den generischen Nachweis; EINE reale,
oeffentlich bekannte Reproduktionsform (Astra-K-Gasgenerator-Fallklasse,
KBA 6625/6490/6665/11331 aus dem Root-Cause-Audit) als benannte
Regressions-Fixtur — kein Laufzeitbezug auf diese Referenzen im Produktcode.

  A) Geschlossene Generation: ein sicheres Paar wird NICHT mehr durch ein
     ambiges ANDERES Ziel desselben Datensatzes heruntergezogen
  B) Ein GENUINE ambiges Paar (geschlossen) bleibt weiterhin review-only
  C) Offenes-Generations-Verhalten bleibt unveraendert (Regression)
  D) Astra-K-Fallklasse (benannte Regressionsfixtur, mehrere Modelle,
     eine geschlossene Zielgeneration unter ihnen)

    python test_kba_pair_level_admission.py
"""
from app.kba_active_generation import ergaenzende_zeilen
from app.kba_import_batch_a import klasse_a
from app.kba_import_kandidaten import AMBIGUOUS_GENERATION, SAFE_IMPORT, import_kandidaten

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def kba_zeile(**kw) -> dict:
    z = {
        "KBA-Referenznummer": "70001", "Rückrufcode des Herstellers": "ABC",
        "Veröffentlichungsdatum": "2020-05-01", "Marke": "TESTMARKE",
        "Modell": "ZETA, ETA",
        "Mangelbezeichnung": "Fehler im Gasgenerator des Fahrerairbags kann bei "
                            "Airbagauslösung zu unkontrollierter Entfaltung führen.",
        "Produktionszeitraum von": "2016", "Produktionszeitraum bis": "2016",
        "Beschreibung der Maßnahme": "Austausch des Gasgenerators.",
        "Mögliche Eingrenzung der betroffenen Modelle": "N/A",
        "Überwachung der Rückrufaktion durch das KBA": "überwacht",
    }
    z.update(kw)
    return z


def br(**kw) -> dict:
    b = {"id": "testmarke-zeta-z1", "marke": "Testmarke", "modell": "Zeta",
         "generation": "Z1", "bauzeitraum_von": 2015, "bauzeitraum_bis": 2021}
    b.update(kw)
    return b


# ══ A) Geschlossenes, sicheres Paar wird nicht mehr heruntergezogen ═════════
print("\n--- A) geschlossene Generation: sicheres Paar bleibt sicher trotz ambigem Nachbar-Ziel ---")
# ZETA: EINDEUTIG (nur eine VIRA-Generation im amtlichen Fenster 2016).
# ETA:  MEHRDEUTIG (zwei VIRA-Generationen desselben Modells ueberdecken 2016).
_baureihen = [
    br(id="testmarke-zeta-z1"),
    br(id="testmarke-eta-e1", marke="Testmarke", modell="Eta", generation="E1",
       bauzeitraum_von=2010, bauzeitraum_bis=2017),
    br(id="testmarke-eta-e2", marke="Testmarke", modell="Eta", generation="E2",
       bauzeitraum_von=2016, bauzeitraum_bis=2023),
]
_kand = import_kandidaten([kba_zeile()], [], _baureihen)
check("A0 Vorbedingung: der Kandidat ist candidatenweit AMBIGUOUS_GENERATION "
      "(wegen ETA E1/E2), NICHT SAFE_IMPORT",
      len(_kand) == 1 and _kand[0].klasse == AMBIGUOUS_GENERATION)
_paare = dict((bid, kl) for bid, kl, _g in _kand[0].paare)
check("A0b aber das Paar (Referenz, ZETA) ist FUER SICH SAFE_IMPORT",
      _paare.get("testmarke-zeta-z1") == SAFE_IMPORT)
check("A0c VORBEDINGUNG: klasse_a() (kandidatenweit) liefert fuer diesen "
      "Kandidaten GAR NICHTS — er wuerde ohne den S1-Fix verloren gehen",
      klasse_a(_kand, _baureihen) == [])

_zeilen, _ausschluesse = ergaenzende_zeilen(_kand, _baureihen, [])
check("A1 das ZETA-Paar wird jetzt TROTZDEM admittiert (geschlossene Generation, "
      "paarweise statt kandidatenweit)",
      any(z["baureihe_id"] == "testmarke-zeta-z1" for z in _zeilen))
check("A2 das ambige ETA-Paar wird NICHT admittiert",
      not any(z["baureihe_id"] in ("testmarke-eta-e1", "testmarke-eta-e2") for z in _zeilen))
check("A3 genau EINE Zeile insgesamt (keine Verdopplung)", len(_zeilen) == 1)


# ══ B) Ein GENUINE ambiges Paar (geschlossen) bleibt review-only ════════════
print("\n--- B) echte Mehrdeutigkeit auch bei geschlossenen Generationen bleibt review-only ---")
_kba_beide_ambig = kba_zeile(**{"KBA-Referenznummer": "70002", "Modell": "ETA"})
_kand_b = import_kandidaten([_kba_beide_ambig], [], _baureihen)
check("B0 Vorbedingung: beide ETA-Ziele sind mehrdeutig (zwei Generationen "
      "desselben Modells, kein eindeutiger Alias-Token)",
      len(_kand_b) == 1 and _kand_b[0].klasse == AMBIGUOUS_GENERATION
      and all(kl == AMBIGUOUS_GENERATION for _b, kl, _g in _kand_b[0].paare))
_zeilen_b, _ = ergaenzende_zeilen(_kand_b, _baureihen, [])
check("B1 keine Zeile wird admittiert — S1 admittiert NIEMALS ein echt "
      "mehrdeutiges Paar, auch nicht geschlossen",
      _zeilen_b == [])


# ══ C) Offenes-Generations-Verhalten bleibt unveraendert (Regression) ══════
print("\n--- C) offene Generation: unveraendertes Verhalten (Regressionsschutz) ---")
_baureihen_offen = [br(id="testmarke-zeta-z1", bauzeitraum_bis=None)]
_kand_c = import_kandidaten(
    [kba_zeile(**{"KBA-Referenznummer": "70003", "Modell": "ZETA",
                 "Produktionszeitraum von": "2030", "Produktionszeitraum bis": "2031"})],
    [], _baureihen_offen)
_zeilen_c, _ausschluesse_c = ergaenzende_zeilen(_kand_c, _baureihen_offen, [])
check("C1 Tor A6 wirkt weiterhin: ein Fenster weit nach Generationsstart plus "
      "Median-Ueberschreitung wird ueber die kandidatenweite AMBIGUOUS_GENERATION "
      "abgefangen, nicht hier zusaetzlich dupliziert",
      _zeilen_c == [] or all(z["baureihe_id"] != "testmarke-zeta-z1" for z in _zeilen_c))

_kand_c2 = import_kandidaten(
    [kba_zeile(**{"KBA-Referenznummer": "70004", "Modell": "ZETA",
                 "Produktionszeitraum von": "2016", "Produktionszeitraum bis": "2016"})],
    [], _baureihen_offen)
_zeilen_c2, _ = ergaenzende_zeilen(_kand_c2, _baureihen_offen, [])
check("C2 offene Generation, Fenster ab Generationsstart -> weiterhin admittiert "
      "(Tor A6 besteht, unveraendert)",
      any(z["baureihe_id"] == "testmarke-zeta-z1" for z in _zeilen_c2))


# ══ D) Astra-K-Fallklasse (benannte Regressionsfixtur) ═══════════════════════
print("\n--- D) reale Fallklasse: mehrmodelliger Gasgenerator-Rueckruf, eine "
      "geschlossene Zielgeneration unter mehreren ---")
# Reproduziert die Form von KBA 6625/6490/6665/11331 (Root-Cause-Audit): ein
# Gasgenerator-Rueckruf ueber mehrere Modelle derselben Marke, von denen EINES
# (hier: "ASTRA_TEST") nur eine VIRA-Generation im Fenster hat, waehrend ein
# ANDERES Modell ("MERIVA_TEST") mehrdeutig ist.
_baureihen_astra = [
    br(id="opeltest-astra-k", marke="Opeltest", modell="Astra", generation="K",
       bauzeitraum_von=2015, bauzeitraum_bis=2021),
    br(id="opeltest-meriva-a", marke="Opeltest", modell="Meriva", generation="A",
       bauzeitraum_von=2010, bauzeitraum_bis=2017),
    br(id="opeltest-meriva-b", marke="Opeltest", modell="Meriva", generation="B",
       bauzeitraum_von=2016, bauzeitraum_bis=2023),
]
_kba_astra = kba_zeile(
    **{"KBA-Referenznummer": "70005", "Marke": "OPELTEST", "Modell": "MERIVA, ASTRA",
       "Produktionszeitraum von": "2016", "Produktionszeitraum bis": "2016"})
_kand_astra = import_kandidaten([_kba_astra], [], _baureihen_astra)
check("D0 Vorbedingung: candidatenweit AMBIGUOUS_GENERATION (Meriva A/B "
      "ueberdeckt 2016 doppelt)", _kand_astra and _kand_astra[0].klasse == AMBIGUOUS_GENERATION)
_zeilen_astra, _ = ergaenzende_zeilen(_kand_astra, _baureihen_astra, [])
check("D1 die Astra-K-Zeile (geschlossene Generation, fuer sich eindeutig) "
      "wird jetzt admittiert",
      any(z["baureihe_id"] == "opeltest-astra-k" for z in _zeilen_astra))
check("D2 keine der beiden Meriva-Generationen wird admittiert",
      not any(z["baureihe_id"] in ("opeltest-meriva-a", "opeltest-meriva-b")
              for z in _zeilen_astra))


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE KBA-PAIR-LEVEL-ADMISSION-TESTS GRUEN")
