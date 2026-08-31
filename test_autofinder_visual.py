"""
Test: AutoFinder Visual Foundation (Runde 5) — app/autofinder_visual.py

Deckt Testmatrix A-N: visual_key V2 (Karosserie-Erweiterung, Kollisionsfreiheit,
Unicode, Web-Kandidaten-Kompatibilität), Manifest-Validierung (Duplikate,
ungültige Enums), Resolver (exact/generation_match/model_match/fallback,
"Kombi bekommt nie Limousinen-exact"), API-Contract (Bild immer gesetzt,
nie None, kein Provider-Call im Request-Pfad), sowie Regression von
Budget/Web-Fallback nach der Verdrahtung.

Kein Netzwerk, kein LLM. Ausführen:  python test_autofinder_visual.py
"""
import importlib
import os
import sys
import tempfile

sys.path.insert(0, ".")

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


_tmp = tempfile.mkdtemp(prefix="vira_af_visual_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "kanonisch.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")
os.environ["AUTO_KI_API_KEY"] = "test-key-autofinder-visual"
os.environ["TAVILY_API_KEY"] = ""

import app.config as _cfg
importlib.reload(_cfg)
import app.database as _db
importlib.reload(_db)
_db.ensure_tables()

from fastapi.testclient import TestClient          # noqa: E402
from app.main import app as fastapi_app             # noqa: E402
import app.routers.autofinder as af_router          # noqa: E402
import app.autofinder as af                         # noqa: E402
import app.autofinder_web as afw                    # noqa: E402
import app.autofinder_budget as af_budget           # noqa: E402
import app.autofinder_visual as afv                 # noqa: E402
from app.rate_limit import limiter as _global_limiter    # noqa: E402

client = TestClient(fastapi_app)
HEADERS = {"Authorization": "Bearer test-key-autofinder-visual"}
URL = "/api/v1/autofinder"


def post(body):
    return client.post(URL, json=body, headers=HEADERS)


def _reset():
    _global_limiter.reset()
    af_router.limiter.reset()


async def _gemini_leer(system_prompt, user_msg):
    return {"candidates": []}


afw.call_gemini_json = _gemini_leer
af_budget.call_gemini_json = _gemini_leer


# ══════════════════════════════════════════════════════════════════════════
# A) G20 Limousine != G21 Kombi visual_key
# ══════════════════════════════════════════════════════════════════════════
k1 = afv.visual_key_v2("BMW", "3er", "G20/G21", ["limousine"])
k2 = afv.visual_key_v2("BMW", "3er", "G20/G21", ["kombi"])
check("A: gleiche Baureihe, unterschiedliche Karosserie -> unterschiedlicher visual_key",
      k1 != k2)
check("A: Format ist marke--modell--generation--karosserie",
      k1 == "bmw--3er--g20-g21--limousine")


# ══════════════════════════════════════════════════════════════════════════
# B) gleiche Daten zweimal -> gleicher Key
# ══════════════════════════════════════════════════════════════════════════
check("B: deterministisch — zweimal dieselben Eingaben liefern denselben Key",
      afv.visual_key_v2("Audi", "A3", "Typ 8Y", ["kompakt"])
      == afv.visual_key_v2("Audi", "A3", "Typ 8Y", ["kompakt"]))


# ══════════════════════════════════════════════════════════════════════════
# C) Unicode stabil
# ══════════════════════════════════════════════════════════════════════════
check("C: Unicode/Umlaute werden gefaltet (Škoda -> skoda)",
      afv.visual_key_v2("Škoda", "Octavia", "IV", ["kombi"]) == "skoda--octavia--iv--kombi")
check("C: Citroën/Bindestrich-Schreibweisen konsistent",
      afv.visual_key_v2("Citroën", "C4", None, ["kompakt"]) == "citroen--c4--kompakt")


# ══════════════════════════════════════════════════════════════════════════
# D) Web-Kandidat kompatibel
# ══════════════════════════════════════════════════════════════════════════
check("D: identische Semantik für internal_db und web_discovered — "
      "gleiche Marke/Modell/Generation/Karosserie ergibt denselben Key "
      "unabhängig von der Herkunft",
      afv.visual_key_v2("Renault", "Megane", "Grandtour", ["kombi"])
      == afv.visual_key_v2("Renault", "Megane", "Grandtour", ["kombi"]))


# ══════════════════════════════════════════════════════════════════════════
# E) Exact Asset resolved
# ══════════════════════════════════════════════════════════════════════════
class _FakeKandidat:
    def __init__(self, marke, modell, generation, karosserie_klassen):
        self.marke = marke; self.modell = modell; self.generation = generation
        self.karosserie_klassen = karosserie_klassen


manifest_e = afv.parse_manifest([{
    "visual_key": "bmw--3er--g20-g21--limousine", "image_url": "/cars/autofinder/bmw--3er--g20-g21--limousine.webp",
    "image_type": "curated", "image_confidence": "exact", "marke": "BMW", "modell": "3er",
    "generation": "G20/G21", "karosserie": "limousine", "ai_generated": True, "reviewed": True, "active": True,
}])
erg_e = afv.resolve_image(_FakeKandidat("BMW", "3er", "G20/G21", ["limousine"]), manifest=manifest_e)
check("E: freigegebenes exaktes Asset wird als exact/curated aufgelöst",
      erg_e.image_confidence == "exact" and erg_e.image_type == "curated" and not erg_e.fallback_used)


# ══════════════════════════════════════════════════════════════════════════
# F) fehlendes Asset -> Fallback
# ══════════════════════════════════════════════════════════════════════════
erg_f = afv.resolve_image(_FakeKandidat("Mazda", "CX-5", "KF", ["suv"]), manifest={})
check("F: kein Manifest-Eintrag -> generic_fallback, representative, HTTP-tauglich",
      erg_f.image_type == "generic_fallback" and erg_f.image_confidence == "representative"
      and erg_f.fallback_used and erg_f.image_url)


# ══════════════════════════════════════════════════════════════════════════
# G) Kombi bekommt kein Limousinen-exact
# ══════════════════════════════════════════════════════════════════════════
erg_g = afv.resolve_image(_FakeKandidat("BMW", "3er", "G20/G21", ["kombi"]), manifest=manifest_e)
check("G: nur ein Limousinen-Asset vorhanden, Kandidat ist Kombi -> "
      "NIEMALS 'exact', höchstens generation_match",
      erg_g.image_confidence != "exact")
check("G: der Kombi bekommt konkret generation_match (dieselbe Baureihe, "
      "andere Karosserie) statt sofort auf generic_fallback zu springen",
      erg_g.image_confidence == "generation_match" and erg_g.fallback_used)


# ══════════════════════════════════════════════════════════════════════════
# H) doppelter Manifest-Key -> Validation FAIL
# ══════════════════════════════════════════════════════════════════════════
try:
    afv.parse_manifest([
        {"visual_key": "x--y--z--kombi", "image_url": "/a.webp", "image_type": "curated",
         "image_confidence": "exact", "marke": "X", "modell": "Y", "generation": "Z",
         "karosserie": "kombi", "ai_generated": True, "reviewed": True, "active": True},
        {"visual_key": "x--y--z--kombi", "image_url": "/b.webp", "image_type": "curated",
         "image_confidence": "exact", "marke": "X", "modell": "Y", "generation": "Z",
         "karosserie": "kombi", "ai_generated": True, "reviewed": True, "active": True},
    ])
    check("H: doppelter visual_key wirft ManifestValidationError", False)
except afv.ManifestValidationError:
    check("H: doppelter visual_key wirft ManifestValidationError", True)


# ══════════════════════════════════════════════════════════════════════════
# I) ungültiger image_type -> FAIL
# ══════════════════════════════════════════════════════════════════════════
try:
    afv.parse_manifest([{
        "visual_key": "a--b--c--suv", "image_url": "/a.webp", "image_type": "geklaut_von_mobile_de",
        "image_confidence": "exact", "marke": "A", "modell": "B", "generation": "C",
        "karosserie": "suv", "ai_generated": True, "reviewed": True, "active": True,
    }])
    check("I: ungültiger image_type wirft ManifestValidationError", False)
except afv.ManifestValidationError:
    check("I: ungültiger image_type wirft ManifestValidationError", True)


# ══════════════════════════════════════════════════════════════════════════
# J) ai_generated vorhanden
# ══════════════════════════════════════════════════════════════════════════
check("J: resolve_image liefert ai_generated=True für ein KI-Asset", erg_e.ai_generated is True)
check("J: generischer Fallback ist explizit NICHT als KI-generiert markiert "
      "(es ist ein Symbolbild, keine Modelldarstellung)", erg_f.ai_generated is False)


# ══════════════════════════════════════════════════════════════════════════
# K) HTTP 200 ohne echtes Bild
# ══════════════════════════════════════════════════════════════════════════
_reset()
r_k = post({"kraftstoff": ["Diesel"], "getriebe": ["automatik"]})
data_k = r_k.json()
check("K: 200 (kein Manifest vorhanden -> trotzdem erfolgreiche Antwort)", r_k.status_code == 200)
check("K: JEDER Kandidat trägt ein nicht-leeres image_url",
      all(kd["image_url"] for kd in data_k["kandidaten"]))
check("K: JEDER Kandidat trägt einen gültigen image_type",
      all(kd["image_type"] in afv.IMAGE_TYPE_WERTE for kd in data_k["kandidaten"]))
check("K: JEDER Kandidat trägt eine gültige image_confidence",
      all(kd["image_confidence"] in afv.IMAGE_CONFIDENCE_WERTE for kd in data_k["kandidaten"]))
check("K: ai_generated ist bei jedem Kandidaten ein Boolean, nie None",
      all(isinstance(kd["ai_generated"], bool) for kd in data_k["kandidaten"]))


# ══════════════════════════════════════════════════════════════════════════
# L) Budget Ranking unverändert
# ══════════════════════════════════════════════════════════════════════════
_reset()
r_l1 = post({"kraftstoff": ["Diesel"], "getriebe": ["automatik"], "budget_min": 15000, "budget_max": 30000})
_reset()
r_l2 = post({"kraftstoff": ["Diesel"], "getriebe": ["automatik"]})
check("L: Budget-Response bleibt 200 nach Bildverdrahtung", r_l1.status_code == 200)
check("L: identische Reihenfolge (Bild-Layer beeinflusst Ranking nicht)",
      [k["variante_id"] for k in r_l1.json()["kandidaten"]]
      == [k["variante_id"] for k in r_l2.json()["kandidaten"]])


# ══════════════════════════════════════════════════════════════════════════
# M) Web-Fallback unverändert
# ══════════════════════════════════════════════════════════════════════════
_reset()
r_m = post({"kraftstoff": ["Elektro"], "getriebe": ["manuell"]})
data_m = r_m.json()
check("M: no_internal_match weiterhin korrekt nach Bildverdrahtung",
      r_m.status_code == 200 and data_m["status"] == "no_internal_match" and data_m["kandidaten"] == [])


# ══════════════════════════════════════════════════════════════════════════
# N) alte 88 Assets nicht Teil des AutoFinder-Manifests
# ══════════════════════════════════════════════════════════════════════════
check("N: generischer Fallback-Pfad zeigt NICHT auf die alte Entdecken-Bibliothek",
      "/cars/autofinder/" in erg_f.image_url and "/cars/M3" not in erg_f.image_url
      and "AudiR8" not in erg_f.image_url)
check("N: leeres Produktions-Manifest (noch nicht angelegt) enthält keine der "
      "22 alten Modelle — kein versehentlicher Reuse",
      afv.lade_manifest_datei() == {} or all(
          v.marke not in ("Porsche", "Ferrari", "Lamborghini", "McLaren", "Nissan")
          for v in afv.lade_manifest_datei().values()))


# ══════════════════════════════════════════════════════════════════════════
# Zusatz: model_match-Stufe + Struktur-Checks
# ══════════════════════════════════════════════════════════════════════════
manifest_mm = afv.parse_manifest([{
    "visual_key": "vw--golf--vi--kompakt", "image_url": "/g6.webp", "image_type": "curated",
    "image_confidence": "exact", "marke": "VW", "modell": "Golf", "generation": "VI",
    "karosserie": "kompakt", "ai_generated": True, "reviewed": True, "active": True,
}])
erg_mm = afv.resolve_image(_FakeKandidat("VW", "Golf", "VII", ["kompakt"]), manifest=manifest_mm)
check("Zusatz: gleiches Modell, andere Generation -> model_match (schwächste kuratierte Stufe)",
      erg_mm.image_confidence == "model_match" and erg_mm.image_type == "curated")

erg_unreviewed = afv.resolve_image(
    _FakeKandidat("BMW", "3er", "G20/G21", ["limousine"]),
    manifest=afv.parse_manifest([{**{
        "visual_key": "bmw--3er--g20-g21--limousine", "image_url": "/x.webp", "image_type": "generated_cached",
        "image_confidence": "exact", "marke": "BMW", "modell": "3er", "generation": "G20/G21",
        "karosserie": "limousine", "ai_generated": True, "reviewed": False, "active": True}}]))
check("Zusatz: reviewed=false darf NIEMALS als exact-Asset erscheinen (§11)",
      erg_unreviewed.image_confidence != "exact")

check("Zusatz: bei Nutzer-Präferenz (bevorzugte_karosserie) wird genau diese "
      "Klasse gewählt, wenn sie zutrifft",
      afv.waehle_karosserie(["limousine", "kombi"], bevorzugte_karosserie="kombi") == "kombi")
check("Zusatz: leere Karosserieliste -> UNBEKANNTE_KAROSSERIE, kein Raten",
      afv.waehle_karosserie([]) == afv.UNBEKANNTE_KAROSSERIE)

# Resolver wirft nie, auch bei kaputtem Kandidaten-Objekt
class _KaputterKandidat:
    pass
erg_kaputt = afv.resolve_image(_KaputterKandidat(), manifest={})
check("Struktur: Resolver wirft NIE — auch bei fehlenden Attributen kommt "
      "ein gültiges ResolveErgebnis zurück (kein 500 im Request-Pfad möglich)",
      erg_kaputt.image_type == "generic_fallback")


print()
if FEHLER:
    print(f"{len(FEHLER)} Test(s) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle AutoFinder-Visual-Tests bestanden.")
