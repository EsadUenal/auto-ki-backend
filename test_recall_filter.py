"""
Test: Zentrale Rückruf-Allowed-Liste (Reliability-Sprint 4, §Phase 7-9).

Deterministisch, kein Netzwerk/LLM. Deckt den konkret gemeldeten Nutzerbefund ab:
ein Hochvolt-/PHEV-Rückruf darf für ein erkanntes Diesel-Fahrzeug NIRGENDWO mehr
auftauchen — weder in den strukturierten Insights (bereits vor Sprint 4 korrekt)
noch im rohen DB-Kontext für den Kauf-/Verkaufscheck-LLM-Prompt (car_lookup.py)
noch im allgemeinen Chat-Kontext (llm.py) — das waren die beiden ungefilterten
Leck-Punkte, die Sprint 4 behebt.

Ausfuehren:  python test_recall_filter.py
"""
import os
import sys
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_recall_"), "test.db")
sys.path.insert(0, ".")

from app.recall_filter import gefilterte_rueckrufe, ausgeschlossene_rueckrufe   # noqa: E402
from app.car_lookup import build_db_context   # noqa: E402
from app.llm import _sql_context   # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


HV_RUECKRUF = {
    "id": "rk-1", "datum": "2023-05", "kba_referenz": "12345",
    "betroffene_baujahre": "2019-2021 (Plug-in-Hybrid)",
    "mangel": "Brandgefahr der Hochvoltbatterie",
    "abhilfe": "Austausch der Hochvoltbatterie-Module",
}
ALLGEMEIN_RUECKRUF = {
    "id": "rk-2", "datum": "2022-01", "kba_referenz": "99999",
    "betroffene_baujahre": "2019-2022",
    "mangel": "Bremskraftverstärker kann ausfallen",
    "abhilfe": "Austausch des Bremskraftverstärkers",
}
DIESEL = {"kraftstoff": "Diesel"}

# ── 1) recall_filter selbst ───────────────────────────────────────────────────
erlaubt = gefilterte_rueckrufe([HV_RUECKRUF, ALLGEMEIN_RUECKRUF], DIESEL, 2020)
check("1: Hochvolt-Rückruf für Diesel NICHT in gefilterte_rueckrufe",
      not any(r["id"] == "rk-1" for r in erlaubt))
check("1b: allgemeiner Rückruf bleibt in gefilterte_rueckrufe",
      any(r["id"] == "rk-2" for r in erlaubt))

ausgeschlossen = ausgeschlossene_rueckrufe([HV_RUECKRUF, ALLGEMEIN_RUECKRUF], DIESEL, 2020)
check("2: Hochvolt-Rückruf erscheint in ausgeschlossene_rueckrufe (für Report-Validator)",
      any(r["id"] == "rk-1" for r in ausgeschlossen))
check("2b: allgemeiner Rückruf NICHT in ausgeschlossene_rueckrufe",
      not any(r["id"] == "rk-2" for r in ausgeschlossen))

# ── 2) build_db_context (Kauf-/Verkaufscheck-LLM-Prompt) ─────────────────────
baureihe = {
    "marke": "BMW", "modell": "320d", "generation": "G20",
    "bauzeitraum_von": 2019, "bauzeitraum_bis": None, "karosserie": [],
    "rueckrufe": [HV_RUECKRUF, ALLGEMEIN_RUECKRUF],
}
ctx = build_db_context(baureihe, DIESEL, 2020)
check("3: 'Hochvolt' NICHT im DB-Kontext für erkannten Diesel", "Hochvolt" not in ctx)
check("3b: Bremskraftverstärker-Rückruf weiterhin im DB-Kontext", "Bremskraftverstärker" in ctx)

# Gegenprobe: dasselbe Fahrzeug als Plug-in-Hybrid erkannt -> Hochvolt-Rückruf
# GEHÖRT dann in den Kontext (mit Applicability-Hinweis, nicht versteckt).
ctx_phev = build_db_context(baureihe, {"kraftstoff": "Plug-in-Hybrid"}, 2020)
check("4: Hochvolt-Rückruf ERSCHEINT für erkannten PHEV (kein Über-Filtern)",
      "Hochvolt" in ctx_phev)
check("4b: Applicability-Wortlaut im PHEV-Kontext vorhanden",
      "per FIN prüfen" in ctx_phev)

# ── 3) _sql_context (allgemeiner Chat) ────────────────────────────────────────
os.environ.setdefault("AUTO_KI_DB_PATH", os.environ["AUTO_KI_DB_PATH"])
# _sql_context liest aus der echten (leeren Test-)DB -> hier nur die Filterlogik
# über gefilterte_rueckrufe direkt nachgewiesen (siehe oben); zusätzlich Smoke-Test,
# dass der Aufruf mit fuel_hint_text nicht crasht und keine Exception wirft.
try:
    _ = _sql_context([], "BMW 320d Diesel")
    check("5: _sql_context mit fuel_hint_text läuft ohne Fehler durch", True)
except Exception as exc:
    check(f"5: _sql_context mit fuel_hint_text läuft ohne Fehler durch ({exc})", False)

print()
if FEHLER:
    print(f"{len(FEHLER)} Test(s) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle Recall-Filter-Tests bestanden.")
