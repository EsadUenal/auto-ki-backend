"""
Marktvergleich Ende-zu-Ende — deterministisch, KEIN Netzwerk.

Anders als test_card_segmenter.py (Segmentierung isoliert) und
test_marktanalyse_single_source.py (einzelne Regeln) fährt dieser Test den
VOLLSTÄNDIGEN Produktionspfad:

    vertiefe_marktrecherche  ->  analysiere_markt  ->  research_status
                             ->  Diagnose-Persistenz (DiagnoseRecorder)

Gestubbt wird ausschließlich die externe Suche (`tavily_search_mit_status`). Alles
danach — Query-Stufen, best_so_far, Kartensegmentierung, Validierung, Dedup,
Deckelung, Ausreißer-Trim, Datenqualität, Marktabdeckung, Quality-Gate und die
Diagnoseablage — läuft echt.

Nicht gestubbt und trotzdem hermetisch: `ziel` wird direkt über `baue_ziel` aus
synthetischen Referenzdaten gebaut statt über die Fahrzeug-DB. Das ist derselbe
Aufruf, den app/kaufcheck.py macht — nur ohne Abhängigkeit von einer befüllten
lokalen Datenbank.

    python test_marktvergleich_e2e.py
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, ".")

# §Source-Policy: Der Production-Default gibt KEINE Marktquelle zum Preisbilden
# frei (app/config.ALLOWED_MARKET_SOURCES ist leer). Dieser Test prueft die
# ANALYSE-ENGINE und braucht dafuer die historischen/synthetischen Testdomains —
# die Freigabe gilt ausschliesslich in diesem Testprozess und ist KEINE
# produktive Qualifikation der Quelle. Siehe _source_policy_testharness.py.
import _source_policy_testharness  # noqa: E402,F401
sys.path.insert(0, "scripts")
os.environ.setdefault("AUTO_KI_DB_PATH",
                      os.path.join(tempfile.mkdtemp(prefix="vira_e2e_"), "test.db"))

from types import SimpleNamespace                                        # noqa: E402

import app.marktrecherche as mr                                          # noqa: E402
from app.marktrecherche import (                                         # noqa: E402
    QueryStufe, research_status, vertiefe_marktrecherche,
)
from app.marktvergleich import (                                         # noqa: E402
    _bewerte, _extrahiere_aus_text, analysiere_markt, baue_ziel,
)

import diagnose_marktanalyse as dm                                       # noqa: E402
from diagnose_store import DiagnoseRecorder                              # noqa: E402

_fails: list[str] = []


def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        _fails.append(name)


# ══ Zielfahrzeug: BMW 320d G20, 2019, 190 PS, Diesel, 120.000 km ═══════════
BAUREIHE = {"marke": "BMW", "modell": "3er", "generation": "G20/G21",
            "id": "bmw-3er-g20-g21", "karosserie": ["Limousine", "Kombi"]}
ALLE_BAUREIHEN = [BAUREIHE,
                  {"id": "bmw-3er-f30", "marke": "BMW", "modell": "3er", "generation": "F30"},
                  {"id": "bmw-5er-g30", "marke": "BMW", "modell": "5er", "generation": "G30"}]
ALLE_MOTOREN = [
    {"baureihe_id": "bmw-3er-g20-g21", "bezeichnung": "320d", "motorcode": "B47D20"},
    {"baureihe_id": "bmw-3er-g20-g21", "bezeichnung": "320i", "motorcode": "B48B20"},
    {"baureihe_id": "bmw-3er-g20-g21", "bezeichnung": "330i", "motorcode": "B48B20O1"},
    {"baureihe_id": "bmw-5er-g30", "bezeichnung": "520d"},
]
MOTOR = {"bezeichnung": "320d", "kraftstoff": "Diesel", "leistung_ps": 190,
         "motorcode": "B47D20"}
# "G20" steht ausdrücklich in der Nutzerangabe -> G21 ist damit Fremdgeneration.
REQ = SimpleNamespace(marke="BMW", modell="3er G20", motor="320d", kraftstoff="Diesel",
                      getriebe="Automatik", baujahr=2019, kilometerstand=120_000,
                      preis_eur=24_900)
ZIEL = baue_ziel(BAUREIHE, MOTOR, REQ, ALLE_BAUREIHEN, ALLE_MOTOREN)

LISTEN_URL = "https://www.kleinanzeigen.de/s-autos/bmw-320d-g20/k0c216"
SEITENTITEL = "BMW 320d G20 gebraucht kaufen"

# ── Synthetischer raw_content ───────────────────────────────────────────────
# (1) drei sauber getrennte 320d-G20-Karten, (2) eine 330i-Karte direkt daneben,
# (3) eine Karte ohne Motorangabe (und ohne eigenen Generationscode),
# (4) eine bewusst nicht segmentierbare Marktlage-Passage mit mehreren Preisen,
#     Kilometerangaben und Jahren — die kann nur das Zeichenfenster auflösen.
RAW = (
    "BMW 320d G20 Limousine Diesel 24.900 € 118.000 km EZ 05/2019\n"
    "BMW 320d G20 Limousine Diesel 25.400 € 121.000 km EZ 06/2019\n"
    "BMW 320d G20 Limousine Diesel 25.900 € 117.000 km EZ 07/2019\n"
    "BMW 320d G20 Limousine Diesel 24.500 € 124.000 km EZ 03/2019\n"
    "BMW 320d G20 Limousine Diesel 25.600 € 115.000 km EZ 08/2019\n"
    "BMW 330i G20 Limousine Benzin 31.900 € 119.000 km EZ 05/2019\n"
    "BMW 3er Limousine 25.100 € 123.000 km EZ 04/2019\n"
    # Bewusst nicht segmentierbar: zwei Preise, zwei Kilometerangaben und zwei
    # Jahre in EINEM Block. Die Kilometerangaben liegen absichtlich NAHE am
    # Zielwert, damit die Fallback-Punkte nicht schon an der Laufleistung
    # scheitern — sonst wäre die Aussage "Fallback wird nie ähnlich" leer.
    "Marktlage BMW 3er Angebote von 21.500 € bis 27.800 € bei 118.000 km bis "
    "122.000 km aus den Jahren 2019 bis 2021 je nach Ausstattung"
)
ANZAHL_GUTE_KARTEN = 5
SEITE = {"url": LISTEN_URL, "title": SEITENTITEL, "content": "", "raw_content": RAW}

STUFEN = [QueryStufe(query=f"BMW 3er G20 320d Testquery {i}", include_domains=None,
                     label=f"stufe-{i}") for i in range(1, 4)]


class _TavilyStub:
    """Ersetzt ausschließlich die externe Suche. Liefert in jeder Stufe DIESELBE
    Seite — damit wird nebenbei bewiesen, dass die mehrfache Verarbeitung derselben
    Karte keine Dubletten erzeugt."""

    def __init__(self):
        self.aufrufe: list[str] = []

    async def __call__(self, query, *a, **kw):
        self.aufrufe.append(query)
        return [dict(SEITE)], False


stub = _TavilyStub()
_orig = mr.tavily_search_mit_status
mr.tavily_search_mit_status = stub
try:
    ERGEBNISSE, MA, DIAG = asyncio.run(vertiefe_marktrecherche(
        [], STUFEN, ZIEL, REQ.preis_eur, None, count=20, zweck="e2e-test"))
finally:
    mr.tavily_search_mit_status = _orig

VERWENDET = list(MA.beobachtungen)
KONTEXT = list(MA.kontext_beobachtungen)
ALLE = VERWENDET + KONTEXT
NACH_PREIS = {b.preis_eur: b for b in ALLE}
PREISE = [b.preis_eur for b in VERWENDET]

check("E2E: die Suche wurde tatsächlich über die echte Recherche aufgerufen",
      len(stub.aufrufe) >= 1)
check("E2E: es entsteht ein belastbares Ergebnis", MA.median_eur is not None)

# ══ 2. Endergebnis ══════════════════════════════════════════════════════════

# Die echten 320d bleiben getrennte Listings.
check("alle 320d-Karten bleiben getrennte Listings",
      sorted(PREISE) == [24500, 24900, 25400, 25600, 25900])
check("sie haben ebenso viele verschiedene listing_key",
      len({b.listing_key for b in VERWENDET}) == ANZAHL_GUTE_KARTEN)

# Preis, km und Baujahr stammen jeweils NUR aus der eigenen Karte.
ERWARTET = {24900: (118_000, 2019), 25400: (121_000, 2019), 25900: (117_000, 2019),
            24500: (124_000, 2019), 25600: (115_000, 2019)}
check("Preis/km/Baujahr stammen je Karte aus der eigenen Karte",
      all((b.kilometerstand, b.baujahr) == ERWARTET[b.preis_eur] for b in VERWENDET))
check("kein Kilometerstand wandert zwischen den Karten",
      sorted(b.kilometerstand for b in VERWENDET)
      == [115_000, 117_000, 118_000, 121_000, 124_000])

# Die 330i-Karte beeinflusst keinen 320d.
check("der 330i ist nicht im Median", 31900 not in PREISE)
check("der 330i taucht auch nicht als Kontext auf",
      31900 not in [b.preis_eur for b in KONTEXT])
check("keine 320d-Karte erbt die 330i-Bezeichnung",
      all(b.engine_variant == "320d" for b in VERWENDET))
check("keine 320d-Karte erbt den Benzin-Kraftstoff des 330i",
      all(b.fuel in (None, "diesel") for b in VERWENDET))

# Die Karte ohne Motor erbt nichts von Nachbarkarten oder Seitentitel.
ohne_motor = NACH_PREIS.get(25100)
check("die Karte ohne Motorangabe wurde erfasst", ohne_motor is not None)
check("sie erbt KEINE Motorbezeichnung", ohne_motor and ohne_motor.engine_variant is None)
check("sie erbt KEINEN Generationscode (weder Nachbar noch Seitentitel)",
      ohne_motor and ohne_motor.generation is None)
check("sie ist höchstens conditional", ohne_motor and ohne_motor.vergleichbarkeit == "bedingt")
check("sie ist nicht im Median", 25100 not in PREISE)

# window_fallback erreicht nie "ähnlich"/"sehr ähnlich".
fallback = [b for b in ALLE if b.window_fallback_used]
check("die nicht segmentierbare Passage erzeugt window_fallback-Punkte", bool(fallback))
check("window_fallback ist nie 'ähnlich' oder 'sehr ähnlich'",
      all(b.vergleichbarkeit not in ("sehr_aehnlich", "aehnlich") for b in fallback))
check("window_fallback trägt extraction_source='window_fallback'",
      all(b.extraction_source == "window_fallback" for b in fallback))
check("window_fallback hat niedrige structural_confidence",
      all(b.structural_confidence == "low" for b in fallback))

# Fallback-Daten verfälschen den strukturell bestätigten Median nicht.
check("kein window_fallback-Punkt trägt den Median",
      all(not b.window_fallback_used for b in VERWENDET))
check("es war kein Conditional-Fallback nötig", MA.fallback_bedingt is False)
check("die Marktspanne stammt allein aus den strukturellen Karten",
      MA.spanne_min_eur >= 24500 and MA.spanne_max_eur <= 25900)

# listing_key: stabil für strukturelle Karten, schwacher Fingerprint im Fallback.
check("strukturelle Karten nutzen den Card-Hash",
      all(b.listing_key.startswith("card:") for b in VERWENDET))
check("der Card-Hash steckt nicht bloß Preis/km/Baujahr",
      all(str(b.preis_eur) not in b.listing_key for b in VERWENDET))
check("Fallback-Punkte nutzen NUR den schwachen Fingerprint",
      all(b.listing_key.startswith("v:") for b in fallback))
check("kein Fallback-Punkt bekommt einen Card-Hash",
      not any(b.listing_key.startswith("card:") for b in fallback))

# Stabilität: derselbe Eingang -> dieselben Schlüssel.
MA2 = analysiere_markt([dict(SEITE)], ZIEL, REQ.preis_eur)
check("listing_key ist über Läufe hinweg stabil",
      sorted(b.listing_key for b in MA2.beobachtungen)
      == sorted(b.listing_key for b in VERWENDET))

# Dieselbe Karte lief durch drei Query-Stufen — keine Dublette.
check("dieselbe Seite in drei Stufen erzeugt keine Dubletten",
      len(VERWENDET) == ANZAHL_GUTE_KARTEN
      and len({b.listing_key for b in VERWENDET}) == ANZAHL_GUTE_KARTEN)
# 5 gute Karten + 330i + Karte ohne Motor + 2 Fallback-Preise = 9 Datenpunkte.
# Drei Query-Stufen lieferten dieselbe Seite — ohne Dedup waeren es 27.
check("auch die Rohzählung ist dedupliziert (keine 3x-Vervielfachung)",
      MA.gefunden == 9)

# Bestehende Fixes bleiben wirksam.
check("Single-Source: eine Plattform -> Marktabdeckung eingeschraenkt",
      MA.marktabdeckung == "eingeschraenkt" and MA.anzahl_domains == 1)
check("Single-Source: eine Plattform blockiert kein Ergebnis",
      research_status(MA) in ("completed_medium", "completed_high"))
check("Single-Source deckelt das Gesamtvertrauen auf medium",
      research_status(MA) == "completed_medium")
check("best_so_far-Verlauf wird geführt", len(DIAG["best_so_far"]) >= 2)
check("G20/G21: G21 ist Fremdgeneration", "g21" in ZIEL["fremd_generationen"])

# ══ 5. Herkunft bleibt unterscheidbar (gleicher Preis/km/Baujahr) ═══════════
# Eine strukturelle Karte und eine Fallback-Passage mit EXAKT identischem
# Preis/km/Baujahr dürfen nicht dieselbe starke Identität bekommen.
# Zwei saubere Karten sind nötig, damit die Segmentierung überhaupt greift —
# danach folgt der mehrdeutige Block, der denselben Preis/km/Baujahr trägt.
KOLLISION = (
    "BMW 320d G20 Limousine Diesel 24.900 € 118.000 km EZ 05/2019\n"
    "BMW 320d G20 Limousine Diesel 25.400 € 121.000 km EZ 06/2019\n"
    "Weitere Angebote in der Region 24.900 € oder 22.100 € jeweils rund "
    "118.000 km aus 2019 mit unterschiedlicher Ausstattung"
)
k_text = f"{SEITENTITEL}\n\n{KOLLISION}"
k_roh = _extrahiere_aus_text(k_text, LISTEN_URL, "market_category",
                             grenzen=(len(SEITENTITEL) + 1, len(SEITENTITEL) + 2))
k_bewertet = [_bewerte(b, ZIEL) for b in k_roh]
k_struktur = [b for b in k_bewertet if not b.window_fallback_used]
k_fallback = [b for b in k_bewertet if b.window_fallback_used]
check("Kollision: die strukturellen Karten wurden erkannt", len(k_struktur) == 2)
check("Kollision: die Passage liefert Fallback-Punkte", len(k_fallback) >= 1)
strukt_24900 = next(b for b in k_struktur if b.preis_eur == 24900)
gleich = [b for b in k_fallback if (b.preis_eur, b.kilometerstand, b.baujahr)
          == (strukt_24900.preis_eur, strukt_24900.kilometerstand, strukt_24900.baujahr)]
check("Kollision: ein Fallback-Punkt hat wirklich denselben Preis/km/Baujahr", bool(gleich))
check("Kollision: die Schlüssel sind trotzdem verschieden",
      gleich and gleich[0].listing_key != strukt_24900.listing_key)
check("Kollision: strukturell behält den stabilen Card-Key",
      strukt_24900.listing_key.startswith("card:"))
check("Kollision: der Fallback behält den schwachen Fingerprint",
      gleich and gleich[0].listing_key.startswith("v:"))
check("Kollision: die Herkunft bleibt unterscheidbar",
      gleich and gleich[0].segmentation_method == "window_fallback"
      and strukt_24900.segmentation_method != "window_fallback")
ma_kollision = analysiere_markt([{"url": LISTEN_URL, "title": SEITENTITEL,
                                  "content": "", "raw_content": KOLLISION}],
                                ZIEL, None)
behalten = [b for b in list(ma_kollision.beobachtungen) + list(ma_kollision.kontext_beobachtungen)
            if b.preis_eur == 24900]
check("Kollision: nach dem Dedup bleibt genau EIN 24.900-€-Punkt", len(behalten) == 1)
check("Kollision: und zwar die strukturell bestätigte Karte",
      behalten and not behalten[0].window_fallback_used)

# ══ 3. Diagnose Ende-zu-Ende ════════════════════════════════════════════════
TMP = tempfile.mkdtemp(prefix="vira_e2e_diag_")
rec = DiagnoseRecorder("e2e", 1, eingabe={"marke": "BMW", "modell": "3er G20"}, ordner=TMP)
rec.merke_suche(query=stub.aufrufe[0], stufe="1:stufe-1", results=ERGEBNISSE)

verwendet_keys = {b.listing_key for b in VERWENDET}
kontext_keys = {b.listing_key for b in KONTEXT}
schon = set()
for k in dm.karten_mit_text(ERGEBNISSE, ZIEL):
    b = k["beobachtung"]
    rec.merke_karte(source_result_url=k["url"], card_index=k["card_index"],
                    card_text=k["card_text"], beobachtung=b,
                    acceptance_status=dm._acceptance_status(b, verwendet_keys,
                                                            kontext_keys, schon),
                    card_hash=k["card_hash"])
rec.merke_zusammenfassung(research_status=DIAG["research_status"],
                          median_eur=MA.median_eur, verwendet=MA.verwendet)
pfad = rec.schreibe()
DATEN = json.loads(pfad.read_text(encoding="utf-8"))
KARTEN = DATEN["karten"]

PFLICHT = ("segmentation_method", "structural_confidence", "start_offset",
           "end_offset", "window_fallback_used", "extraction_source", "listing_key")
check("Diagnose: jede Karte trägt alle geforderten Herkunftsfelder",
      all(all(f in k for f in PFLICHT) for k in KARTEN))
check("Diagnose: Offsets sind gesetzt",
      all(k["start_offset"] is not None and k["end_offset"] is not None for k in KARTEN))

verwendet_karten = [k for k in KARTEN if k["acceptance_status"] == "verwendet"]
check("Diagnose: genau so viele 'verwendet'-Karten wie Marktbeobachtungen",
      len(verwendet_karten) == len(VERWENDET))
check("Diagnose: dieselben listing_key wie in der Marktanalyse",
      {k["listing_key"] for k in verwendet_karten} == verwendet_keys)

kopf = DATEN["segmentierung"]
check("Diagnose-Kopf: methoden_verwendet passt zu den Marktbeobachtungen",
      sum(kopf["methoden_verwendet"].values()) == len(VERWENDET))
erwartete_methoden: dict[str, int] = {}
for b in VERWENDET:
    erwartete_methoden[b.segmentation_method] = erwartete_methoden.get(b.segmentation_method, 0) + 1
check("Diagnose-Kopf: die Methodenzähler stimmen inhaltlich",
      kopf["methoden_verwendet"] == erwartete_methoden)
check("Diagnose-Kopf: kein window_fallback unter den verwendeten Karten",
      kopf["verwendet_window_fallback"] == 0
      and "window_fallback" not in kopf["methoden_verwendet"])
check("Diagnose-Kopf: strukturell = true, weil das Ergebnis echte Cards trägt",
      kopf["strukturell"] is True)
check("Diagnose-Kopf: der volle Audit-Trail zählt mehr Karten als verwendet",
      kopf["karten_gesamt"] > kopf["verwendet_gesamt"])
check("Diagnose-Kopf: window_fallback taucht im Gesamt-Trail sehr wohl auf",
      kopf["methoden_alle"].get("window_fallback", 0) >= 1)

# Der Kern: eine Fallback-Karte darf nach Mapping/Serialisierung NICHT wie eine
# strukturelle aussehen.
fb_karten = [k for k in KARTEN if k["window_fallback_used"]]
check("Diagnose: Fallback-Karten sind in der Datei erhalten", bool(fb_karten))
check("Diagnose: keine Fallback-Karte sieht strukturell aus",
      all(k["segmentation_method"] == "window_fallback"
          and k["structural_confidence"] == "low"
          and k["extraction_source"] == "window_fallback"
          and k["segmentierung_strukturell"] is False
          and k["card_hash"] is None
          and k["listing_key"].startswith("v:") for k in fb_karten))
check("Diagnose: keine strukturelle Karte sieht wie ein Fallback aus",
      all(k["segmentation_method"] != "window_fallback"
          and k["structural_confidence"] in ("high", "medium")
          and k["segmentierung_strukturell"] is True
          for k in KARTEN if not k["window_fallback_used"]))
check("Diagnose: Herkunft überlebt die JSON-Serialisierung unverändert",
      {k["listing_key"]: k["segmentation_method"] for k in verwendet_karten}
      == {b.listing_key: b.segmentation_method for b in VERWENDET})

shutil.rmtree(TMP, ignore_errors=True)

# ══ 4. Serialisierung der Marktanalyse selbst ═══════════════════════════════
# Der Check wird als Pydantic-Modell gespeichert und später wieder geladen. Dabei
# darf die Herkunft der Beobachtungen nicht verloren gehen.
roh_dump = MA.model_dump()
zurueck = type(MA).model_validate(roh_dump)
check("Serialisierung: Beobachtungen überleben den Round-Trip",
      len(zurueck.beobachtungen) == len(VERWENDET))
check("Serialisierung: Segmentierungsfelder überleben den Round-Trip",
      [(b.listing_key, b.segmentation_method, b.structural_confidence,
        b.start_offset, b.end_offset, b.window_fallback_used, b.extraction_source)
       for b in zurueck.beobachtungen]
      == [(b.listing_key, b.segmentation_method, b.structural_confidence,
           b.start_offset, b.end_offset, b.window_fallback_used, b.extraction_source)
          for b in VERWENDET])
check("Serialisierung: Marktabdeckung und Fallback-Flag überleben",
      zurueck.marktabdeckung == MA.marktabdeckung
      and zurueck.fallback_bedingt == MA.fallback_bedingt)
check("Serialisierung: auch die Kontext-Beobachtungen behalten ihre Herkunft",
      [b.window_fallback_used for b in zurueck.kontext_beobachtungen]
      == [b.window_fallback_used for b in KONTEXT])
# Ein alter, vor dieser Etappe gespeicherter Check darf weiterhin ladbar sein.
alt = {k: v for k, v in roh_dump.items()
       if k not in ("marktabdeckung", "anzahl_domains", "fallback_bedingt",
                    "kontext_beobachtungen")}
for b in alt.get("beobachtungen", []):
    for feld in ("segmentation_method", "structural_confidence", "start_offset",
                 "end_offset", "window_fallback_used", "listing_key"):
        b.pop(feld, None)
alt_geladen = type(MA).model_validate(alt)
check("Rückwärtskompatibilität: alte Checks ohne die neuen Felder bleiben ladbar",
      len(alt_geladen.beobachtungen) == len(VERWENDET))
check("Rückwärtskompatibilität: fehlende Herkunft gilt konservativ als Fallback",
      all(b.window_fallback_used is True and b.segmentation_method == "window_fallback"
          for b in alt_geladen.beobachtungen))

print()
if _fails:
    print(f"{len(_fails)} Test(s) fehlgeschlagen.")
    sys.exit(1)
print("Alle Marktvergleich-E2E-Tests bestanden.")
