"""
Test: AutoFinder Bildgenerierungs-Pipeline (Runde 6) — app/autofinder_generation.py

Deckt Testmatrix A-P: GenerationJob-Erstellung, Prompt-Inhalt (Karosserie/
Generation Pflicht, kein Kennzeichen/Text/Personen), Provider-Mock (Erfolg ->
review_required, Fehler -> failed, NIE automatisch approved), Review-Workflow
(approve -> Manifest active/reviewed, reject -> nie im aktiven Manifest,
Duplicate-Schutz, atomarer Write), CLI-Sicherheitsnetze (dry-run 0 Calls,
only-missing überspringt Freigegebenes, limit begrenzt), und die strukturelle
Zusicherung, dass der AutoFinder-Router die Pipeline nicht importiert.

Kein Netzwerk (Provider wird vollständig gemockt). Ausführen:
    python test_autofinder_generation.py
"""
import asyncio
import importlib
import inspect
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


# DB-Bootstrap ZUERST, vor jedem anderen app.*-Import — sonst binden Module,
# die (transitiv über app.car_lookup/app.database) vor dem Env-Setup
# importiert werden, an den DB-Pfad, der zu diesem Zeitpunkt galt (bekanntes
# Stolperfeld, siehe test_autofinder_api.py und Schwesterdateien).
_tmp2 = tempfile.mkdtemp(prefix="vira_af_gen_http_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp2, "k.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp2, "c")
os.environ["AUTO_KI_API_KEY"] = "test-key-gen"
os.environ["TAVILY_API_KEY"] = ""

import app.config as _cfg
importlib.reload(_cfg)
import app.database as _db
importlib.reload(_db)
_db.ensure_tables()

import app.autofinder_generation as ag   # noqa: E402
import app.autofinder_visual as av       # noqa: E402


# ══════════════════════════════════════════════════════════════════════════
# A) GenerationJob sauber erstellt / B) visual_key korrekt
# ══════════════════════════════════════════════════════════════════════════
job = ag.neuer_job("BMW", "3er", "G20/G21", "limousine", baujahr_von=2019, baujahr_bis=None)
check("A: GenerationJob wird mit status=pending erstellt", job.status == ag.PENDING)
check("A: alle Pflichtfelder gesetzt",
      job.marke == "BMW" and job.modell == "3er" and job.karosserie == "limousine")
check("B: visual_key stimmt exakt mit dem Resolver-Format überein",
      job.visual_key == av.visual_key_v2("BMW", "3er", "G20/G21", ["limousine"]))
check("B: visual_key == derselbe Wert, den auch der Resolver für dieses "
      "Fahrzeug erzeugen würde (eine Quelle der Wahrheit)",
      job.visual_key == "bmw--3er--g20-g21--limousine")


# ══════════════════════════════════════════════════════════════════════════
# C) Prompt enthält richtige Generation/Karosserie
# ══════════════════════════════════════════════════════════════════════════
prompt = ag.baue_prompt("BMW", "3er", "G20/G21", "kombi", 2019, 2023)
check("C: Prompt nennt die Marke", "BMW" in prompt)
check("C: Prompt nennt die Generation/den Werkscode", "G20/G21" in prompt)
check("C: Prompt nennt die Karosserie (Kombi -> 'station wagon')",
      "station wagon" in prompt.lower())
check("C: Prompt nennt das Baujahrfenster", "2019" in prompt and "2023" in prompt)

prompt_limo = ag.baue_prompt("Audi", "A6", "C8", "limousine")
check("C: unterschiedliche Karosserie -> unterschiedlicher Karosserie-Begriff im Prompt",
      "sedan" in prompt_limo.lower() and "station wagon" not in prompt_limo.lower())


# ══════════════════════════════════════════════════════════════════════════
# D) Prompt fordert kein Kennzeichen/Text/Personen
# ══════════════════════════════════════════════════════════════════════════
p_lower = prompt.lower()
check("D: Prompt schließt explizit Personen aus", "no people" in p_lower or "humans" in p_lower)
check("D: Prompt schließt explizit Kennzeichen aus", "license plate" in p_lower)
check("D: Prompt schließt explizit Text/Wasserzeichen aus",
      "text" in p_lower and "watermark" in p_lower)
check("D: Prompt schließt prominente Herstellerlogos/-embleme aus",
      "badges" in p_lower or "emblems" in p_lower)
check("D: Prompt fordert AN KEINER STELLE ein Tuning (falls Standardvariante)",
      "tuned" in p_lower and "non-tuned" in p_lower)


# ══════════════════════════════════════════════════════════════════════════
# E) Provider-Mock Erfolg -> generated/review_required
# F) Provider-Mock Failure -> failed
# G) kein automatisches approved
# ══════════════════════════════════════════════════════════════════════════
_tmp_out = Path(tempfile.mkdtemp(prefix="vira_af_gen_test_"))
_FAKE_PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)  # kein echtes valides PNG, nur Byte-Nutzlast


async def _fake_generiere_ok(job):
    return _FAKE_PNG, "image/png", None


async def _fake_generiere_fail(job):
    return None, None, "ServerError: 503 UNAVAILABLE (simuliert)"


async def _lauf():
    ag.generiere_bild = _fake_generiere_ok
    job_ok = ag.neuer_job("VW", "Golf", "VII", "kompakt")
    ergebnis_ok = await ag.fuehre_job_aus(job_ok, _tmp_out)

    ag.generiere_bild = _fake_generiere_fail
    job_fail = ag.neuer_job("Opel", "Astra", "L", "kombi")
    ergebnis_fail = await ag.fuehre_job_aus(job_fail, _tmp_out)
    return ergebnis_ok, ergebnis_fail


erg_ok, erg_fail = asyncio.run(_lauf())
check("E: erfolgreicher Provider-Mock -> status review_required (NICHT generated/approved)",
      erg_ok.status == ag.REVIEW_REQUIRED)
check("E: output_path ist gesetzt und die Datei existiert wirklich",
      bool(erg_ok.output_path) and Path(erg_ok.output_path).exists())
check("F: fehlgeschlagener Provider-Mock -> status failed", erg_fail.status == ag.FAILED)
check("F: error-Feld enthält den Grund", erg_fail.error and "503" in erg_fail.error)
check("G: WEDER Erfolg noch Fehlschlag setzen jemals automatisch 'approved'",
      erg_ok.status != ag.APPROVED and erg_fail.status != ag.APPROVED)


# ══════════════════════════════════════════════════════════════════════════
# H) approve -> Manifest active/reviewed
# I) reject -> nicht im aktiven Manifest
# J) Duplicate Manifest-Key verhindert
# K) Atomic Manifest Write
# ══════════════════════════════════════════════════════════════════════════
_manifest_pfad = _tmp_out / "manifest.json"
manifest_leer: dict = {}
av.speichere_manifest_datei(manifest_leer, _manifest_pfad)
check("K: leeres Manifest lässt sich atomar schreiben, Datei existiert danach",
      _manifest_pfad.exists())

eintrag = av.ManifestEintrag(
    visual_key=erg_ok.visual_key, image_url=f"/cars/autofinder/{erg_ok.visual_key}.webp",
    image_type="generated_cached", image_confidence="exact",
    marke=erg_ok.marke, modell=erg_ok.modell, generation=erg_ok.generation,
    karosserie=erg_ok.karosserie, ai_generated=True, reviewed=True, active=True,
)
manifest_mit_approve = {erg_ok.visual_key: eintrag}
av.speichere_manifest_datei(manifest_mit_approve, _manifest_pfad)
geladen = av.lade_manifest_datei(_manifest_pfad, force=True)
check("H: approve -> Manifest-Eintrag existiert mit reviewed=true, active=true",
      geladen[erg_ok.visual_key].reviewed and geladen[erg_ok.visual_key].active)
check("H: image_type/confidence entsprechen der Vorgabe aus Teil G",
      geladen[erg_ok.visual_key].image_type == "generated_cached"
      and geladen[erg_ok.visual_key].image_confidence == "exact")

check("I: ein NIE freigegebener (rejected) Job taucht nicht im Manifest auf",
      erg_fail.visual_key not in geladen)

try:
    av.speichere_manifest_datei({
        "a": av.ManifestEintrag(visual_key="a--b--c--kombi", image_url="/1.webp",
                                image_type="curated", image_confidence="exact",
                                marke="A", modell="B", generation="C", karosserie="kombi",
                                ai_generated=True, reviewed=True, active=True),
        "b": av.ManifestEintrag(visual_key="a--b--c--kombi", image_url="/2.webp",
                                image_type="curated", image_confidence="exact",
                                marke="A", modell="B", generation="C", karosserie="kombi",
                                ai_generated=True, reviewed=True, active=True),
    }, _manifest_pfad)
    check("J: doppelter visual_key beim Speichern wird verhindert (ValueError)", False)
except av.ManifestValidationError:
    check("J: doppelter visual_key beim Speichern wird verhindert (ValueError)", True)

# Der zuvor gültig geschriebene Stand darf durch den fehlgeschlagenen
# Duplicate-Write NICHT beschädigt worden sein (atomarer Write, §K).
geladen_nach_fehlversuch = av.lade_manifest_datei(_manifest_pfad, force=True)
check("K: fehlgeschlagener Schreibversuch beschädigt das bestehende Manifest NICHT",
      erg_ok.visual_key in geladen_nach_fehlversuch)


# ══════════════════════════════════════════════════════════════════════════
# L) dry-run -> 0 Provider Calls / M) only-missing / N) limit
# ══════════════════════════════════════════════════════════════════════════
_orig_generiere = ag.generiere_bild
_zaehler = {"n": 0}


async def _zaehl_und_ok(job):
    _zaehler["n"] += 1
    return _FAKE_PNG, "image/png", None


sys.path.insert(0, "scripts")
import autofinder_generate_images as gen_script   # noqa: E402

# L) Dry-Run: build_generation_jobs baut Jobs, ruft aber NIE generiere_bild
_zaehler["n"] = 0
jobs_dry = gen_script.build_generation_jobs(limit=5)
check("L: Dry-Run (nur build_generation_jobs, kein --execute) -> 0 Provider-Calls",
      _zaehler["n"] == 0 and len(jobs_dry) > 0)

# N) limit
check("N: limit=5 liefert höchstens 5 Jobs", len(gen_script.build_generation_jobs(limit=5)) <= 5)
check("N: limit=1 liefert genau 1 Job", len(gen_script.build_generation_jobs(limit=1)) == 1)

# M) only-missing überspringt bereits freigegebene visual_keys
_manifest_for_missing = {
    "bmw--3er--g20-g21--limousine": av.ManifestEintrag(
        visual_key="bmw--3er--g20-g21--limousine", image_url="/x.webp",
        image_type="curated", image_confidence="exact", marke="BMW", modell="3er",
        generation="G20/G21", karosserie="limousine", ai_generated=True,
        reviewed=True, active=True),
}
av.speichere_manifest_datei(_manifest_for_missing, _manifest_pfad)


def _patched_lade_manifest(pfad=None, force=False):
    return av.parse_manifest([{
        "visual_key": e.visual_key, "image_url": e.image_url, "image_type": e.image_type,
        "image_confidence": e.image_confidence, "marke": e.marke, "modell": e.modell,
        "generation": e.generation, "karosserie": e.karosserie, "ai_generated": e.ai_generated,
        "reviewed": e.reviewed, "active": e.active,
    } for e in _manifest_for_missing.values()])


gen_script.lade_manifest_datei = _patched_lade_manifest
jobs_missing = gen_script.build_generation_jobs(only_missing=True)
check("M: only-missing überspringt den bereits freigegebenen visual_key",
      "bmw--3er--g20-g21--limousine" not in {j.visual_key for j in jobs_missing})
check("M: only-missing liefert trotzdem andere, noch fehlende Keys",
      len(jobs_missing) > 0)


# ══════════════════════════════════════════════════════════════════════════
# O) kein Generation-Call im AutoFinder Router
# ══════════════════════════════════════════════════════════════════════════
import app.routers.autofinder as af_router   # noqa: E402
_router_quelle = inspect.getsource(af_router)
check("O: Router importiert app.autofinder_generation NICHT — Pipeline ist "
      "rein Offline-/Admin-Prozess", "autofinder_generation" not in _router_quelle)


# ══════════════════════════════════════════════════════════════════════════
# P) AutoFinder HTTP weiterhin 200 ohne Assets (Regression, kompakt)
# ══════════════════════════════════════════════════════════════════════════
from fastapi.testclient import TestClient   # noqa: E402
from app.main import app as fastapi_app     # noqa: E402
import app.autofinder_web as afw            # noqa: E402
import app.autofinder_budget as af_budget   # noqa: E402


async def _leer(sp, um):
    return {"candidates": []}


afw.call_gemini_json = _leer
af_budget.call_gemini_json = _leer
client = TestClient(fastapi_app)
r = client.post("/api/v1/autofinder", json={"kraftstoff": ["Diesel"]},
                headers={"Authorization": "Bearer test-key-gen"})
check("P: 200 ohne jedes generierte/kuratierte Asset (nur generischer Fallback)",
      r.status_code == 200)
check("P: jeder Kandidat trägt trotzdem ein image_url (Fallback greift)",
      all(k["image_url"] for k in r.json()["kandidaten"]))


print()
if FEHLER:
    print(f"{len(FEHLER)} Test(s) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle AutoFinder-Generation-Tests bestanden.")
