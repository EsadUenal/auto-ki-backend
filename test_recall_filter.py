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

# ══════════════════════════════════════════════════════════════════════════════
# 6) OVER-ESCALATION: Baujahr-Deckung ist KEINE Variantenaussage
#
# FLOOR-SAFETY-AUDIT (Batch A). Ein amtlich verifizierter Rueckruf, der sich nur
# auf Modell + Produktionszeitraum bezieht, darf allein wegen eines passenden
# Baujahrs NICHT auf "variant_match" steigen — sonst hebt er ueber
# `empfehlungs_floor` die Kaufempfehlung an. Vor dem Fix betraf das 260 der 269
# importierten Zeilen und 39 von 41 realen Kaufchecks.
from app.recall_filter import rueckruf_applicability   # noqa: E402

_MODELLWEIT = {
    "id": 9001, "baureihe_id": "test-baureihe", "datum": "2021-05-05",
    "betroffene_baujahre": "2019-2021",
    "mangel": "Die Verschraubung des Lenkgetriebes kann sich loesen.",
    "abhilfe": "Austausch der Schrauben am Lenkgetriebe.",
    "kba_referenz": "11331", "_trust": "verified",
}
_MIT_QUALIFIER = {**_MODELLWEIT, "id": 9002,
                  "betroffene_baujahre": "2019-2021 (Plug-in-Hybrid)"}

_diesel = {"kraftstoff": "Diesel"}
_phev = {"kraftstoff": "Plug-in-Hybrid"}

_appl, _conf, _einfluss, _ = rueckruf_applicability(
    _MODELLWEIT, True, "11331", _diesel, marke="Opel")
check("6a modellweiter verified Rueckruf + Baujahr-Treffer -> series_only",
      _appl == "series_only")
check("6b die BELEGLAGE bleibt trotzdem hoch (verifizierte amtliche Quelle)",
      _conf == "hoch")
check("6c der Wortlaut verweist weiterhin auf die FIN-Pruefung",
      "FIN" in _einfluss)

from app.empfehlungs_floor import RUECKRUF_WERKSTATT_APPLICABILITY   # noqa: E402
check("6d series_only loest keinen Werkstatt-Floor aus",
      _appl not in RUECKRUF_WERKSTATT_APPLICABILITY)

_appl_ohne_jahr, _, _, _ = rueckruf_applicability(
    _MODELLWEIT, False, "11331", _diesel, marke="Opel")
check("6e ohne Baujahr-Deckung ebenfalls series_only",
      _appl_ohne_jahr == "series_only")

# Positive Gegenprobe: mit amtlicher Antriebsbedingung bleibt variant_match.
_appl_var, _conf_var, _, _ = rueckruf_applicability(
    _MIT_QUALIFIER, True, "11331", _phev, marke="Opel")
check("6f amtliche Antriebsbedingung + passender Antrieb -> variant_match",
      _appl_var == "variant_match" and _conf_var == "hoch")
check("6g variant_match loest den Werkstatt-Floor weiterhin aus",
      _appl_var in RUECKRUF_WERKSTATT_APPLICABILITY)

_appl_ink, _, _, _ = rueckruf_applicability(
    _MIT_QUALIFIER, True, "11331", _diesel, marke="Opel")
check("6h widersprechender Antrieb -> incompatible", _appl_ink == "incompatible")

_appl_unk, _, _, _ = rueckruf_applicability(
    _MIT_QUALIFIER, True, "11331", None, marke="Opel")
check("6i Antrieb nicht erkannt -> unclear, niemals variant_match",
      _appl_unk == "unclear")

# Ohne Verifikation bleibt es bei der schwaecheren Beleglage.
_appl_unv, _conf_unv, _, _ = rueckruf_applicability(
    {**_MODELLWEIT, "_trust": "unverified_db"}, True, "11331", _diesel, marke="Opel")
check("6j unverifiziert: series_only mit mittlerer Beleglage",
      _appl_unv == "series_only" and _conf_unv == "mittel")


print()
if FEHLER:
    print(f"{len(FEHLER)} Test(s) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle Recall-Filter-Tests bestanden.")
