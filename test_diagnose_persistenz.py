"""
Diagnose-Persistenz (scripts/diagnose_store.py) — deterministisch, KEIN Netzwerk.

Prüft die Voraussetzung für die spätere manuelle Live-Abnahme: ein Diagnoselauf
muss vollständig und dauerhaft als JSON ablegbar sein, inklusive des isolierten
Kartentexts jeder einzelnen Fahrzeugkarte. Ohne das war der letzte BMW-Audit nicht
endgültig belegbar, weil Tavilys Antworten nur 300 s im Prozess-Cache liegen.

Geprüft wird:
  - alle geforderten Felder je Suchergebnis und je Karte sind vorhanden
  - jede Karte ist einzeln nachvollziehbar (Quelle + Index + eigener Kartentext)
  - die fehlende STRUKTURELLE Kartensegmentierung ist explizit markiert
  - Secrets werden nicht mitgeschrieben
  - Zielordner ist per .gitignore ausgeschlossen
  - ohne `schreibe()` entsteht keine Datei

    python test_diagnose_persistenz.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
os.environ.setdefault("AUTO_KI_DB_PATH",
                      os.path.join(tempfile.mkdtemp(prefix="vira_diag_"), "test.db"))

from app.models import Preisbeobachtung                                   # noqa: E402
from diagnose_store import (                                              # noqa: E402
    DIAGNOSE_ORDNER, SEGMENTIERUNG_HINWEIS, DiagnoseRecorder, _ohne_secrets,
)

_fails: list[str] = []


def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        _fails.append(name)


TMP = tempfile.mkdtemp(prefix="vira_diagrun_")

RESULTS = [
    {"url": "https://www.kleinanzeigen.de/s-autos/bmw-320d-2019/k0c216",
     "title": "BMW 320d G20 gebraucht kaufen",
     "content": "BMW 320d G20 24.900 € 118.000 km EZ 05/2019 . BMW 320d G20 25.400 € 121.000 km EZ 06/2019",
     "raw_content": "…voller Seitentext… api_key: tvly-GEHEIMESCHLUESSEL123456 …"},
    {"url": "https://suchen.mobile.de/auto/bmw-3er.html",
     "title": "BMW 3er", "content": "BMW 320d G20 26.100 € 115.000 km EZ 03/2019",
     "raw_content": ""},
]


def beobachtung(preis, km, jahr, key, stufe="sehr_aehnlich", *, strukturell=True):
    return Preisbeobachtung(
        preis_eur=preis, kilometerstand=km, baujahr=jahr,
        quelle_domain="kleinanzeigen.de", quelle_url=RESULTS[0]["url"],
        vergleichbarkeit=stufe, listing_key=key, listing_id="2812345678",
        detail_url="https://www.kleinanzeigen.de/s-anzeige/bmw-320d/2812345678-216-1",
        make="BMW", model="3er", generation="G20", body="limousine", fuel="diesel",
        engine_variant="320d", horsepower=190, transmission="automatik",
        similarity=0.94, source_type="market_category",
        extraction_source="raw_content" if strukturell else "window_fallback",
        segmentation_method="block_structure" if strukturell else "window_fallback",
        structural_confidence="medium" if strukturell else "low",
        start_offset=10 if strukturell else 0, end_offset=95 if strukturell else 260,
        window_fallback_used=not strukturell,
        acceptance_reason="sehr_aehnlich: Motorvariante bestätigt (320d)")


rec = DiagnoseRecorder("bmw", 1, eingabe={"marke": "BMW", "modell": "320d G20"}, ordner=TMP)
rec.merke_suche(query="BMW 3er G20 320d 2019 gebraucht", stufe="3:portal-eng", results=RESULTS)
rec.merke_karte(RESULTS[0]["url"], 0, "BMW 320d G20 24.900 € 118.000 km EZ 05/2019",
                beobachtung(24900, 118000, 2019, "id:kleinanzeigen.de:2812345678"),
                "verwendet", card_hash="a1b2c3d4e5f60718")
rec.merke_karte(RESULTS[0]["url"], 1, "BMW 320d G20 25.400 € 121.000 km EZ 06/2019",
                beobachtung(25400, 121000, 2019, "v:25400:121000:2019", "bedingt",
                            strukturell=False),
                "conditional", card_hash=None)
rec.merke_zusammenfassung(research_status="completed_medium", median_eur=24900)

# ── Ohne schreibe() darf nichts auf der Platte liegen ───────────────────────
check("Ohne schreibe() entsteht keine Datei", not os.listdir(TMP))

pfad = rec.schreibe()
check("schreibe() legt genau eine Datei an", len(os.listdir(TMP)) == 1)
check("Dateiname trägt Testcase und Lauf", "bmw" in pfad.name and "lauf1" in pfad.name)

daten = json.loads(pfad.read_text(encoding="utf-8"))

# ── Suchergebnisse: geforderte Felder ───────────────────────────────────────
PFLICHT_SUCHE = ("timestamp", "testcase", "query", "query_stage", "url", "domain",
                 "title", "content", "raw_content")
check("Suchergebnisse gespeichert", len(daten["suchergebnisse"]) == 2)
check("Suchergebnis trägt alle geforderten Felder",
      all(f in daten["suchergebnisse"][0] for f in PFLICHT_SUCHE))
check("raw_content wird mitgespeichert (der eigentliche Zweck)",
      "voller Seitentext" in daten["suchergebnisse"][0]["raw_content"])
check("Domain abgeleitet", daten["suchergebnisse"][0]["domain"] == "kleinanzeigen.de")
check("Query-Stufe festgehalten", daten["suchergebnisse"][0]["query_stage"] == "3:portal-eng")

# Dieselbe Seite über mehrere Stufen soll die Datei nicht aufblähen.
rec.merke_suche(query="andere Query", stufe="4:fenster-jahr-km", results=RESULTS)
check("Dieselbe URL wird nicht doppelt abgelegt", len(rec.suchergebnisse) == 2)

# ── Karten: geforderte Felder ───────────────────────────────────────────────
PFLICHT_KARTE = ("source_result_url", "card_index", "card_text", "listing_id",
                 "detail_url", "listing_key", "card_hash", "make", "model",
                 "generation", "body", "fuel", "engine_variant", "horsepower",
                 "transmission", "year", "mileage", "price", "similarity",
                 "extraction_source", "acceptance_status", "acceptance_reason",
                 # §7 — Segmentierungsherkunft je Karte
                 "segmentation_method", "structural_confidence", "start_offset",
                 "end_offset", "window_fallback_used")
karten = daten["karten"]
check("Karten gespeichert", len(karten) == 2)
check("Karte trägt alle geforderten Felder",
      all(f in karten[0] for f in PFLICHT_KARTE))
fehlend = [f for f in PFLICHT_KARTE if f not in karten[0]]
check("keine Pflichtfelder fehlen (Diagnose)", not fehlend)

# ── Karten einzeln nachvollziehbar ──────────────────────────────────────────
check("Karten sind über Quelle + Index einzeln adressierbar",
      {(k["source_result_url"], k["card_index"]) for k in karten}
      == {(RESULTS[0]["url"], 0), (RESULTS[0]["url"], 1)})
check("jede Karte trägt ihren EIGENEN isolierten Kartentext",
      karten[0]["card_text"] != karten[1]["card_text"]
      and "24.900" in karten[0]["card_text"] and "25.400" in karten[1]["card_text"])
check("der Kartentext enthält nicht den Preis der Nachbarkarte",
      "25.400" not in karten[0]["card_text"] and "24.900" not in karten[1]["card_text"])
check("acceptance_status je Karte unterschiedlich abgelegt",
      [k["acceptance_status"] for k in karten] == ["verwendet", "conditional"])
check("acceptance_reason mitgeschrieben", "Motorvariante" in karten[0]["acceptance_reason"])
check("listing_id/detail_url der Karte gespeichert",
      karten[0]["listing_id"] == "2812345678" and karten[0]["detail_url"].endswith("216-1"))
check("card_hash getrennt vom listing_key gespeichert",
      karten[0]["card_hash"] == "a1b2c3d4e5f60718"
      and karten[0]["listing_key"].startswith("id:"))

# ── §7: Segmentierungsherkunft je Karte nachvollziehbar ─────────────────────
check("Hinweistext erklärt die Verfahren", SEGMENTIERUNG_HINWEIS in daten["segmentierung"]["hinweis"])
check("Kopf meldet: das Ergebnis trägt strukturelle Karten",
      daten["segmentierung"]["strukturell"] is True)
# Getrennte Zählungen: der volle Audit-Trail vs. die tatsächlich verwendeten Karten.
check("Kopf zählt den vollen Audit-Trail",
      daten["segmentierung"]["karten_gesamt"] == 2
      and daten["segmentierung"]["methoden_alle"]
      == {"block_structure": 1, "window_fallback": 1})
check("Kopf zählt die VERWENDETEN Karten getrennt",
      daten["segmentierung"]["verwendet_gesamt"] == 1
      and daten["segmentierung"]["methoden_verwendet"] == {"block_structure": 1})
check("Kopf trennt strukturelle von Fallback-Karten unter den verwendeten",
      daten["segmentierung"]["verwendet_strukturell"] == 1
      and daten["segmentierung"]["verwendet_window_fallback"] == 0)
check("strukturelle Karte trägt Methode und Confidence",
      karten[0]["segmentation_method"] == "block_structure"
      and karten[0]["structural_confidence"] == "medium"
      and karten[0]["window_fallback_used"] is False)
check("Fallback-Karte ist als solche erkennbar",
      karten[1]["segmentation_method"] == "window_fallback"
      and karten[1]["structural_confidence"] == "low"
      and karten[1]["window_fallback_used"] is True
      and karten[1]["extraction_source"] == "window_fallback")
check("Kartengrenzen (Offsets) gespeichert",
      karten[0]["start_offset"] == 10 and karten[0]["end_offset"] == 95)
check("Vorbehalt steht auch an JEDER einzelnen Karte",
      [k["segmentierung_strukturell"] for k in karten] == [True, False])

# ── Keine Secrets ───────────────────────────────────────────────────────────
roh = pfad.read_text(encoding="utf-8")
check("Tavily-Schlüssel wird nicht gespeichert", "tvly-GEHEIMESCHLUESSEL123456" not in roh)
check("die Stelle ist als redigiert erkennbar", "[REDACTED]" in roh)
check("Google-/OpenAI-artige Schlüssel werden ebenfalls redigiert",
      "AIza" not in _ohne_secrets("key AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ01")
      and "sk-" not in _ohne_secrets("token sk-ABCDEFGHIJKLMNOPQRSTUV"))
check("Redaktion wirkt rekursiv über Listen/Dicts",
      _ohne_secrets({"a": ["tvly-ABCDEFGHIJKL"]})["a"][0] == "[REDACTED]")
check("harmloser Text bleibt unverändert",
      _ohne_secrets("BMW 320d G20 24.900 €") == "BMW 320d G20 24.900 €")

# ── Zusammenfassung + Metadaten ─────────────────────────────────────────────
check("Zusammenfassung gespeichert", daten["zusammenfassung"]["median_eur"] == 24900)
check("Eingabeparameter des Testfalls gespeichert", daten["eingabe"]["modell"] == "320d G20")
check("Zeitstempel gesetzt", bool(daten["gestartet"]) and bool(daten["geschrieben"]))
check("Schema-Kennung gesetzt", daten["schema"] == "vira-diagnose/2")

# ── Zielordner ist ausgeschlossen ───────────────────────────────────────────
gitignore = open(".gitignore", encoding="utf-8").read()
check("Diagnoseordner steht in .gitignore", "diagnose_runs/" in gitignore)
check("Default-Ordner heißt diagnose_runs", DIAGNOSE_ORDNER.name == "diagnose_runs")
check("Default-Ordner wird nicht schon beim Import angelegt",
      not DIAGNOSE_ORDNER.exists() or DIAGNOSE_ORDNER.is_dir())

shutil.rmtree(TMP, ignore_errors=True)

print()
if _fails:
    print(f"{len(_fails)} Test(s) fehlgeschlagen.")
    sys.exit(1)
print("Alle Diagnose-Persistenz-Tests bestanden.")
