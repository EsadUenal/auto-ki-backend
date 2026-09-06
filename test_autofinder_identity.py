"""
Test: AutoFinder Candidate Identity + Budget/Preis-Konsistenz
      (app/autofinder_identity.py, app/autofinder_budget.py::konsolidiere_*)

Deckt die Testmatrix A-O der Consumer-Release-Härtung:
  A) Focus "Mk4" vs. "Vierte Generation"      -> max. 1 Consumer-Ergebnis
  B) gleiche Generation, andere Schreibweise  -> gleiche Identity
  C) echte unterschiedliche Generation        -> NICHT dedupliziert
  D) gleiche Baureihe, echte andere Karosserie-> NICHT zusammengeworfen
  E) Volvo Kombi     -> Consumer-Modell konsistent zur Karosserie (V60)
  F) Volvo Limousine -> entsprechend konsistent (S60)
  G) Motor bleibt an die richtige Candidate Identity gebunden
  H) internal + web derselbe reale Kandidat   -> genau einmal
  I) internal gewinnt gegen schwächeres Web-Duplikat
  J) Budget max 25k + Preisspanne ab 27k      -> NICHT "Im Budget"
  K) Spanne überlappt das Budget              -> NEAR_BUDGET
  L) Spanne komplett im Budget                -> IN_BUDGET
  M) keine Preisspanne                        -> Status unverändert (kein Fake)
  N) Fit-Score bleibt unverändert (Dedupe sortiert nicht um)
  O) Image-Ready-Pool enthält keine semantischen Duplikate

Reine Logik, kein Netz, kein Gemini, keine DB.
Ausführen:  python test_autofinder_identity.py
"""
import sys

sys.path.insert(0, ".")
FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


import app.autofinder_identity as ident  # noqa: E402
from app.autofinder_budget import (  # noqa: E402
    IN_BUDGET, NEAR_BUDGET, OUT_OF_BUDGET, BUDGET_UNKNOWN,
    CONF_HIGH, CONF_LOW, CONF_MEDIUM, CONF_UNKNOWN,
    konsolidiere_budget_status,
)


class Kand:
    """Minimaler Stellvertreter für einen Engine-/Web-Kandidaten — trägt genau
    die Felder, die die Identitäts-Logik liest."""

    def __init__(self, marke, modell, generation, *, karosserie=(), leistung_ps=None,
                 kraftstoff="Benzin", antrieb=None, baujahr_von=None,
                 source_type="internal_db", datenqualitaet=0.5, evidence_count=0,
                 motor="", cid="x"):
        self.marke = marke
        self.modell = modell
        self.generation = generation
        self.karosserie_klassen = list(karosserie)
        self.leistung_ps = leistung_ps
        self.kraftstoff = kraftstoff
        self.antrieb = antrieb
        self.baujahr_von = baujahr_von
        self.source_type = source_type
        self.datenqualitaet = datenqualitaet
        self.evidence_count = evidence_count
        self.motor_bezeichnung = motor
        self.variante_id = cid if source_type == "internal_db" else None
        self.candidate_id = cid if source_type != "internal_db" else None


# ══════════════════════════════════════════════════════════════════════════
# B) Generation-Normalisierung — Synonyme ergeben dieselbe Identity
# ══════════════════════════════════════════════════════════════════════════
_SYNONYME_GEN4 = ["Mk4", "Mk 4", "MK IV", "Mark IV", "4. Generation",
                  "Vierte Generation", "Fourth generation", "4th generation",
                  "Fourth generation (SN95)"]
for _s in _SYNONYME_GEN4:
    check(f"B: {_s!r} -> gen4", ident.normalisiere_generation(_s) == "gen4")

check("B: 'Dritte Generation' -> gen3", ident.normalisiere_generation("Dritte Generation") == "gen3")
check("B: 'Mk1' und 'Erste Generation' sind identisch",
      ident.normalisiere_generation("Mk1") == ident.normalisiere_generation("Erste Generation"))
check("B: Chassiscode bleibt eigenständig (G20 != gen7)",
      ident.normalisiere_generation("G20") == "g20")
check("B: unbekannter String wird nur geslugt, nie geraten",
      ident.normalisiere_generation("Typ 8Y") == "typ-8y")
check("B: leere Generation -> leeres Token", ident.normalisiere_generation(None) == "")

# Chassiscodes derselben Marke bleiben unterscheidbar
check("B: G20 und G21 bleiben verschiedene Tokens",
      ident.normalisiere_generation("G20") != ident.normalisiere_generation("G21"))


# ══════════════════════════════════════════════════════════════════════════
# A) + H) + I) Ford Focus: internal "Mk4" vs. web "Vierte Generation"
# ══════════════════════════════════════════════════════════════════════════
_focus_intern = Kand("Ford", "Focus", "Mk4", karosserie=["limousine", "kombi"],
                     leistung_ps=280, kraftstoff="Benzin", antrieb="Front",
                     baujahr_von=2018, source_type="internal_db",
                     datenqualitaet=1.0, motor="2.3 EcoBoost ST (280 PS)",
                     cid="ford-focus-mk4-2.3-ecoboost-st-280ps")
_focus_web = Kand("Ford", "Focus", "Vierte Generation", karosserie=["kombi"],
                  leistung_ps=280, kraftstoff="Benzin", antrieb=None,
                  baujahr_von=2018, source_type="web_discovered",
                  datenqualitaet=0.4, evidence_count=2,
                  motor="2.3 EcoBoost ST 280 PS", cid="web--ford--focus--vierte-generation")

check("A/H: internal Mk4 und web 'Vierte Generation' sind dasselbe Fahrzeug",
      ident.ist_dasselbe_fahrzeug(_focus_intern, _focus_web))

_behalten, _entfernt = ident.dedupe_semantisch([(93.0, _focus_intern), (91.0, _focus_web)])
check("A: aus zwei Focus-Einträgen bleibt genau EINER", len(_behalten) == 1)
check("I: der interne DB-Kandidat gewinnt gegen das Web-Duplikat",
      _behalten[0][1] is _focus_intern)
check("I: die entfernte ID wird benannt (nachvollziehbar, nicht still)",
      _entfernt == ["web--ford--focus--vierte-generation"])

# Auch wenn der Web-Kandidat den besseren Fit hat, gewinnt die interne Quelle
_behalten2, _ = ident.dedupe_semantisch([(99.0, _focus_web), (80.0, _focus_intern)])
check("I: internal_db schlägt web_discovered auch bei besserem Web-Fit",
      len(_behalten2) == 1 and _behalten2[0][1] is _focus_intern)


# ══════════════════════════════════════════════════════════════════════════
# C) Echte unterschiedliche Generation -> NICHT dedupliziert
# ══════════════════════════════════════════════════════════════════════════
_focus_mk3 = Kand("Ford", "Focus", "Mk3", karosserie=["kombi"], leistung_ps=280,
                  kraftstoff="Benzin", baujahr_von=2011, cid="ford-focus-mk3-x")
check("C: Mk3 und Mk4 sind NICHT dasselbe Fahrzeug",
      not ident.ist_dasselbe_fahrzeug(_focus_intern, _focus_mk3))
_b3, _ = ident.dedupe_semantisch([(93.0, _focus_intern), (85.0, _focus_mk3)])
check("C: beide Generationen bleiben erhalten", len(_b3) == 2)

# Verschiedene Chassiscodes derselben Marke/Modellfamilie bleiben getrennt,
# auch wenn Motor und Karosserie identisch sind
_g20 = Kand("BMW", "3er", "G20", karosserie=["limousine"], leistung_ps=190,
            kraftstoff="Diesel", baujahr_von=2019, cid="bmw-g20")
_g21 = Kand("BMW", "3er", "G21", karosserie=["limousine"], leistung_ps=190,
            kraftstoff="Diesel", baujahr_von=2025, cid="bmw-g21")
check("C: unterschiedliche Chassiscodes mit klarem Baujahrsabstand bleiben getrennt",
      not ident.ist_dasselbe_fahrzeug(_g20, _g21))


# ══════════════════════════════════════════════════════════════════════════
# D) Gleiche Baureihe, echte unterschiedliche Karosserie -> nicht zusammen
# ══════════════════════════════════════════════════════════════════════════
_a4_lim = Kand("Audi", "A4", "B9", karosserie=["limousine"], leistung_ps=190,
               kraftstoff="Diesel", baujahr_von=2015, cid="audi-a4-b9-lim")
_a4_avant = Kand("Audi", "A4 Avant", "B9", karosserie=["kombi"], leistung_ps=190,
                 kraftstoff="Diesel", baujahr_von=2015, cid="audi-a4-b9-avant")
check("D: Limousine und Kombi derselben Generation werden NICHT zusammengeworfen",
      not ident.ist_dasselbe_fahrzeug(_a4_lim, _a4_avant))
_b4, _ = ident.dedupe_semantisch([(90.0, _a4_lim), (90.0, _a4_avant)])
check("D: beide Karosserien bleiben in der Ausgabe", len(_b4) == 2)

# Multi-Body-DB-Zeile ({limousine, kombi}) und Web-Treffer, der nur die
# Kombi-Variante kennt: Überschneidung -> dasselbe Fahrzeug (kein Duplikat)
check("D: Multi-Body-Datensatz überschneidet sich korrekt mit Einzel-Karosserie",
      ident.ist_dasselbe_fahrzeug(_focus_intern, _focus_web))

# Unbekannte Karosserie blockiert nicht (missing != different)
_focus_web_ohne_karo = Kand("Ford", "Focus", "Vierte Generation", karosserie=[],
                            leistung_ps=280, kraftstoff="Benzin", baujahr_von=2018,
                            source_type="web_discovered", cid="web--focus--ohne-karo")
check("D: fehlende Karosserieangabe verhindert die Erkennung nicht",
      ident.ist_dasselbe_fahrzeug(_focus_intern, _focus_web_ohne_karo))


# ══════════════════════════════════════════════════════════════════════════
# E) + F) Volvo S60/V60 — Consumer-Modellname passend zur Karosserie
# ══════════════════════════════════════════════════════════════════════════
check("E: 'S60/V60' + kombi -> 'V60'",
      ident.consumer_modellname("S60/V60", "kombi") == "V60")
check("F: 'S60/V60' + limousine -> 'S60'",
      ident.consumer_modellname("S60/V60", "limousine") == "S60")
check("E: 'S90/V90' + kombi -> 'V90'",
      ident.consumer_modellname("S90/V90", "kombi") == "V90")
check("E: 'A4 / A4 Avant' + kombi -> 'A4 Avant'",
      ident.consumer_modellname("A4 / A4 Avant", "kombi") == "A4 Avant")
check("E: 'Golf / Golf Variant' + kombi -> 'Golf Variant'",
      ident.consumer_modellname("Golf / Golf Variant", "kombi") == "Golf Variant")

# Konservativ: nicht auflösbar -> unverändert, nie geraten
check("E: ohne Karosserie bleibt der Name unverändert",
      ident.consumer_modellname("S60/V60", None) == "S60/V60")
check("E: nicht auflösbare Kombination bleibt unverändert",
      ident.consumer_modellname("S60/V60", "suv") == "S60/V60")
check("E: einfacher Modellname bleibt unangetastet",
      ident.consumer_modellname("Focus", "kombi") == "Focus")
check("E: zwei echte verschiedene Modelle werden NICHT projiziert",
      ident.consumer_modellname("911/Boxster", "coupe") == "911/Boxster")
check("E: 'V-Klasse' wird NICHT als Kombi-Marker missdeutet (Van)",
      ident.consumer_modellname("V-Klasse/Vito", "kombi") == "V-Klasse/Vito")

# Die Familie bleibt über die Projektion hinweg vergleichbar
check("E: 'V60' und 'S60/V60' gehören zur selben Modellfamilie",
      ident.familien_kompatibel(ident.modell_familie("V60"),
                                 ident.modell_familie("S60/V60")))
_volvo_web = Kand("Volvo", "S60/V60", "Generation 3", karosserie=["kombi"],
                  leistung_ps=310, kraftstoff="Benzin", baujahr_von=2018,
                  source_type="web_discovered", cid="web--volvo--s60-v60")
_volvo_web2 = Kand("Volvo", "V60", "3. Generation", karosserie=["kombi"],
                   leistung_ps=310, kraftstoff="Benzin", baujahr_von=2018,
                   source_type="web_discovered", cid="web--volvo--v60")
check("E: 'S60/V60' und 'V60' derselben Generation sind dasselbe Fahrzeug",
      ident.ist_dasselbe_fahrzeug(_volvo_web, _volvo_web2))


# ══════════════════════════════════════════════════════════════════════════
# G) Motor bleibt an die richtige Identity gebunden
# ══════════════════════════════════════════════════════════════════════════
_focus_150 = Kand("Ford", "Focus", "Mk4", karosserie=["kombi"], leistung_ps=150,
                  kraftstoff="Benzin", baujahr_von=2018, cid="ford-focus-mk4-150")
check("G: gleiche Generation, andere Leistung -> eigenständiger Kandidat",
      not ident.ist_dasselbe_fahrzeug(_focus_intern, _focus_150))
_focus_diesel = Kand("Ford", "Focus", "Mk4", karosserie=["kombi"], leistung_ps=280,
                     kraftstoff="Diesel", baujahr_von=2018, cid="ford-focus-mk4-d")
check("G: gleiche Leistung, anderer Kraftstoff -> eigenständiger Kandidat",
      not ident.ist_dasselbe_fahrzeug(_focus_intern, _focus_diesel))
_b5, _ = ident.dedupe_semantisch([(93.0, _focus_intern), (90.0, _focus_150),
                                   (88.0, _focus_diesel)])
check("G: drei echte Motorvarianten bleiben drei Kandidaten", len(_b5) == 3)
check("G: der Motortext des Gewinners bleibt unverändert",
      _b5[0][1].motor_bezeichnung == "2.3 EcoBoost ST (280 PS)")


# ══════════════════════════════════════════════════════════════════════════
# N) + O) Reihenfolge/Fit unverändert, Pool ohne Duplikate
# ══════════════════════════════════════════════════════════════════════════
_liste = [(95.0, _focus_intern), (93.0, _a4_lim), (91.0, _focus_web),
          (89.0, _a4_avant), (85.0, _focus_mk3)]
_behalten_n, _entfernt_n = ident.dedupe_semantisch(_liste)
check("N: die Fit-Scores der Überlebenden bleiben unverändert",
      [f for f, _k in _behalten_n] == [95.0, 93.0, 89.0, 85.0])
check("N: die Eingangsreihenfolge bleibt erhalten (Dedupe sortiert nicht um)",
      [k for _f, k in _behalten_n] == [_focus_intern, _a4_lim, _a4_avant, _focus_mk3])
check("O: der Pool enthält danach keine zwei semantisch gleichen Kandidaten",
      all(not ident.ist_dasselbe_fahrzeug(a[1], b[1])
          for i, a in enumerate(_behalten_n) for b in _behalten_n[i + 1:]))
check("O: leere Eingabe bleibt leer", ident.dedupe_semantisch([]) == ([], []))


# ══════════════════════════════════════════════════════════════════════════
# J) - M) Budget vs. Preisorientierung
# ══════════════════════════════════════════════════════════════════════════
# J) realer Browser-Befund: Budget max 25.000, Spanne 27.000-39.000
_s, _c = konsolidiere_budget_status(
    IN_BUDGET, CONF_HIGH, preis_min=27000, preis_max=39000,
    preis_confidence=CONF_MEDIUM, budget_min=None, budget_max=25000)
check("J: 25k Budget + Spanne ab 27k ist NICHT 'Im Budget'", _s != IN_BUDGET)
check("J: der abgeleitete Status ist NEAR_BUDGET (knapp darüber, ehrlich)",
      _s == NEAR_BUDGET)
check("J: die Confidence stammt aus der Preisspanne, nicht mehr aus HIGH",
      _c == CONF_MEDIUM)

# deutlich darüber -> OUT_OF_BUDGET
_s, _c = konsolidiere_budget_status(
    IN_BUDGET, CONF_HIGH, preis_min=45000, preis_max=60000,
    preis_confidence=CONF_LOW, budget_min=None, budget_max=25000)
check("J: Spanne weit über Budget -> OUT_OF_BUDGET", _s == OUT_OF_BUDGET)

# K) Spanne überlappt das Budget
_s, _c = konsolidiere_budget_status(
    IN_BUDGET, CONF_HIGH, preis_min=22000, preis_max=31000,
    preis_confidence=CONF_MEDIUM, budget_min=None, budget_max=25000)
check("K: überlappende Spanne -> NEAR_BUDGET", _s == NEAR_BUDGET)

# L) komplett im Budget
_s, _c = konsolidiere_budget_status(
    NEAR_BUDGET, CONF_MEDIUM, preis_min=14000, preis_max=19000,
    preis_confidence=CONF_MEDIUM, budget_min=10000, budget_max=25000)
check("L: Spanne komplett im Budget -> IN_BUDGET", _s == IN_BUDGET)
_s2, _ = konsolidiere_budget_status(
    IN_BUDGET, CONF_HIGH, preis_min=14000, preis_max=19000,
    preis_confidence=CONF_LOW, budget_min=None, budget_max=25000)
check("L: bereits korrekter Status bleibt inkl. seiner Confidence stehen",
      (_s2, _) == (IN_BUDGET, CONF_HIGH))

# M) keine Preisspanne -> nichts erfinden
for _preis in ((None, None), (None, 30000), (27000, None)):
    _s, _c = konsolidiere_budget_status(
        IN_BUDGET, CONF_HIGH, preis_min=_preis[0], preis_max=_preis[1],
        preis_confidence=CONF_UNKNOWN, budget_min=None, budget_max=25000)
    check(f"M: unvollständige Preisspanne {_preis} -> Status unverändert",
          (_s, _c) == (IN_BUDGET, CONF_HIGH))

# ohne Budget-Obergrenze gibt es keinen Widerspruch zu bereinigen
_s, _c = konsolidiere_budget_status(
    BUDGET_UNKNOWN, CONF_UNKNOWN, preis_min=27000, preis_max=39000,
    preis_confidence=CONF_HIGH, budget_min=None, budget_max=None)
check("M: ohne Budget-Obergrenze bleibt der Status UNKNOWN",
      (_s, _c) == (BUDGET_UNKNOWN, CONF_UNKNOWN))

# unterhalb des Mindestbudgets ist nicht automatisch "Im Budget"
_s, _c = konsolidiere_budget_status(
    IN_BUDGET, CONF_HIGH, preis_min=4000, preis_max=7000,
    preis_confidence=CONF_MEDIUM, budget_min=20000, budget_max=30000)
check("M: Spanne komplett unter dem Mindestbudget -> nicht 'Im Budget'",
      _s == NEAR_BUDGET)


# ══════════════════════════════════════════════════════════════════════════
# Verdrahtung im Router (strukturell)
# ══════════════════════════════════════════════════════════════════════════
_router = open("app/routers/autofinder.py", encoding="utf-8").read()
check("Router: semantischer Dedupe läuft im finalen Pfad",
      "dedupe_semantisch(" in _router)
check("Router: Dedupe steht VOR dem Pool-Cap (Image-Ready sieht keine Duplikate)",
      _router.index("dedupe_semantisch(") < _router.index("final = mit_fit[:_KANDIDATEN_POOL]"))
check("Router: Budget/Preis-Konsolidierung wird angewendet",
      "konsolidiere_budget_status(" in _router)
check("Router: Konsolidierung nutzt die Enrichment-Preisspanne",
      "preis_min=enr.estimated_price_min" in _router)
check("Router: Consumer-Modellname wird projiziert",
      "consumer_modellname(" in _router and "modell=modell_out" in _router)
check("Identity-Modul ist frei von Netz-/Gemini-/DB-Abhängigkeiten",
      not any(t in open("app/autofinder_identity.py", encoding="utf-8").read()
              for t in ("call_gemini", "tavily", "get_conn", "requests.", "httpx.")))


print()
if FEHLER:
    print(f"{len(FEHLER)} Test(s) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle AutoFinder-Candidate-Identity-Tests bestanden.")
