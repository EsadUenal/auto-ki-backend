"""
Root-Cause-Audit RC-1 — EINE kanonische Rückruf-Applicability-Entscheidung.

Synthetische Marke/Modell/Motor (keine realen Fahrzeuge als Testgegenstand).
Kein Netzwerk, kein Gemini-/Tavily-Aufruf.

Prüft, dass `recall_filter.gefilterte_rueckrufe` / `ausgeschlossene_rueckrufe`
und `car_lookup.build_db_context` — sobald ihnen eine `VehicleIdentity`
übergeben wird — exakt dieselbe Entscheidung treffen wie der direkte Aufruf
von `recall_filter.rueckruf_applicability`/`rueckruf_scope` (der Pfad, den
`app/evidence.py::build_insights` für `check_typ="kauf"` bereits nutzt).

  A) Rückwärtskompatibilität — ohne `identity` ändert sich NICHTS
  B) Mit `identity` schließt sich die Lücke (Motorcode-/Leistungs-Widerspruch)
  C) `gefilterte_rueckrufe` und `rueckruf_applicability` stimmen überein
  D) `build_db_context` nutzt dieselbe Entscheidung wie die Allowed-List
  E) `ausgeschlossene_rueckrufe` bleibt das exakte Komplement

    python test_recall_identity_consistency.py
"""
import app.recall_filter as _rf

_rf.get_rueckruf_referenzen_kurz = lambda: []  # kein DB-Zugriff im Test

from app.car_lookup import build_db_context
from app.models import KaufCheckRequest
from app.recall_filter import (
    ausgeschlossene_rueckrufe, gefilterte_rueckrufe, rueckruf_applicability,
)
from app.vehicle_identity import VehicleIdentity

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def motor(vid, bez, code, kraftstoff, ps, getriebe='["6-Gang Manuell"]', antrieb="Front"):
    return {"variante_id": vid, "bezeichnung": bez, "motorcode": code, "kraftstoff": kraftstoff,
            "leistung_ps": ps, "getriebe": getriebe, "antrieb": antrieb,
            "schwachstellen_motor": [], "kritische_wartung": []}


def baureihe(motoren, rueckrufe):
    return {"id": "testmarke-alpha-t1", "marke": "Testmarke", "modell": "Alpha",
            "generation": "T1", "bauzeitraum_von": 2014, "bauzeitraum_bis": 2022,
            "motoren": motoren, "schwachstellen_baureihe": [], "rueckrufe": rueckrufe}


def req(**kw):
    basis = dict(marke="Testmarke", modell="Alpha", baujahr=2018)
    basis.update(kw)
    return KaufCheckRequest(**basis)


def ident(b, m, r):
    return VehicleIdentity.from_check_context(b, m, r)


# Ein Motorcode-/Leistungsscope, den NUR `rueckruf_scope()` (identitätsbewusst)
# erkennt — die ältere Hochvolt-Keyword-Heuristik sieht hier nichts, weil der
# Text weder "Hybrid"/"Plug-in"/"Elektro" noch einen Klammerzusatz enthält.
_RECALL_MOTORCODE_SCOPE = {
    "betroffene_baujahre": "2018", "kba_referenz": None,
    "mangel": "Bei Fahrzeugen mit 150 PS kann die Ölleitung undicht werden.",
    "abhilfe": "Ölleitung ersetzen.",
}
_m125 = motor("g-1", "1.4 T (125 PS)", "QX14A", "Benzin", 125)
_b = baureihe([_m125], [_RECALL_MOTORCODE_SCOPE])
_i = ident(_b, _m125, req(kraftstoff="Benzin", leistung_ps=125, motor="1.4 QX14A"))


# ══ A) Rückwärtskompatibilität ════════════════════════════════════════════════
print("\n--- A) Rueckwaertskompatibilitaet (identity=None aendert nichts) ---")
_ohne_identity = gefilterte_rueckrufe(_b["rueckrufe"], _m125, 2018, marke="Testmarke")
check("A1 ohne identity bleibt der Leistungs-Widerspruch unerkannt (alter, "
      "dokumentierter Bug-Zustand — bestehende Aufrufer aendern sich NICHT)",
      len(_ohne_identity) == 1 and _ohne_identity[0]["applicability"] != "incompatible")
_ctx_ohne = build_db_context(_b, _m125, 2018)
check("A2 build_db_context() ohne identity verhaelt sich byteGLEICH zum "
      "bisherigen Code (Rueckruf erscheint weiterhin, Default-Parameter)",
      "Ölleitung" in _ctx_ohne)


# ══ B) Mit identity schliesst sich die Luecke ════════════════════════════════
print("\n--- B) Mit identity: derselbe Rueckruf wird jetzt korrekt ausgeschlossen ---")
_mit_identity = gefilterte_rueckrufe(_b["rueckrufe"], _m125, 2018, marke="Testmarke",
                                     identity=_i)
check("B1 mit identity schliesst gefilterte_rueckrufe den 150-PS-Rueckruf fuer "
      "ein 125-PS-Fahrzeug aus (RC-1 geschlossen)",
      _mit_identity == [])
_ausgeschlossen = ausgeschlossene_rueckrufe(_b["rueckrufe"], _m125, 2018,
                                            marke="Testmarke", identity=_i)
check("B2 ausgeschlossene_rueckrufe nennt denselben Rueckruf mit Grund "
      "'antrieb_unpassend'",
      len(_ausgeschlossen) == 1
      and _ausgeschlossen[0]["ausschlussgrund"] == "antrieb_unpassend")


# ══ C) EINE Entscheidung — gefilterte_rueckrufe == rueckruf_applicability ════
print("\n--- C) Konsistenz: gefilterte_rueckrufe und rueckruf_applicability stimmen ueberein ---")
_direkt_appl, *_ = rueckruf_applicability(
    _RECALL_MOTORCODE_SCOPE, True, "", _m125, marke="Testmarke", identity=_i)
check("C1 direkter rueckruf_applicability()-Aufruf (wie app/evidence.py) "
      "kommt auf dasselbe 'incompatible' wie gefilterte_rueckrufe oben",
      _direkt_appl == "incompatible" and _mit_identity == [])

# Gegenprobe: ein Fahrzeug, dessen Leistung zum Scope PASST, muss den Rueckruf
# ueber BEIDE Pfade behalten (keine Überfilterung).
_m150 = motor("g-2", "1.4 T (150 PS)", "QX14B", "Benzin", 150)
_b150 = baureihe([_m150], [_RECALL_MOTORCODE_SCOPE])
_i150 = ident(_b150, _m150, req(kraftstoff="Benzin", leistung_ps=150, motor="1.4 QX14B"))
_erlaubt_150 = gefilterte_rueckrufe(_b150["rueckrufe"], _m150, 2018, marke="Testmarke",
                                    identity=_i150)
check("C2 passende Leistung (150 PS) behaelt den Rueckruf (keine Ueberfilterung)",
      len(_erlaubt_150) == 1 and _erlaubt_150[0]["applicability"] in
      ("variant_match", "series_only"))


# ══ D) build_db_context nutzt dieselbe Entscheidung ══════════════════════════
print("\n--- D) build_db_context mit identity ===")
_ctx_mit = build_db_context(_b, _m125, 2018, identity=_i)
check("D1 mit identity verschwindet der 150-PS-Rueckruf auch aus dem "
      "LLM-Kontext-String — derselbe Entscheidungspfad wie Insights/Cards",
      "Ölleitung" not in _ctx_mit)
_ctx_mit_150 = build_db_context(_b150, _m150, 2018, identity=_i150)
check("D2 bei passender Leistung bleibt der Rueckruf im Kontext-String",
      "Ölleitung" in _ctx_mit_150)

# Unbekannte Leistung (kein erkannter Motor) -> CONDITIONAL/unclear, NICHT
# stillschweigend entfernt (Preserve-Uncertainty-Prinzip).
_i_unbekannt = ident(baureihe([], []), None, req())
_ctx_unbekannt = build_db_context(_b, None, 2018, identity=_i_unbekannt)
check("D3 unbekannte Leistung -> Rueckruf bleibt sichtbar (als 'unklar', nicht "
      "versteckt) statt UNKNOWN als NO_MATCH zu behandeln",
      "Ölleitung" in _ctx_unbekannt)


# ══ E) ausgeschlossene_rueckrufe bleibt das exakte Komplement ════════════════
print("\n--- E) Allowed-/Excluded-Liste bleiben komplementaer ---")
_mehrere = [
    _RECALL_MOTORCODE_SCOPE,
    {"betroffene_baujahre": "2018", "kba_referenz": None,
     "mangel": "Allgemeiner Hinweis ohne Variantenbezug.", "abhilfe": "Prüfen."},
]
_b_mehrere = baureihe([_m125], _mehrere)
_erlaubt = gefilterte_rueckrufe(_mehrere, _m125, 2018, marke="Testmarke", identity=_i)
_ausgeschl = ausgeschlossene_rueckrufe(_mehrere, _m125, 2018, marke="Testmarke", identity=_i)
check("E1 jeder Rueckruf landet in genau EINER der beiden Listen",
      len(_erlaubt) + len(_ausgeschl) == len(_mehrere))
check("E2 der variantenlose Hinweis bleibt erlaubt (series_relevant)",
      any("Allgemeiner Hinweis" in r["mangel"] for r in _erlaubt))
check("E3 der widersprüchliche Rueckruf landet in der Excluded-Liste",
      any("Ölleitung" in r["mangel"] for r in _ausgeschl))


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE RECALL-IDENTITY-CONSISTENCY-TESTS GRUEN")
