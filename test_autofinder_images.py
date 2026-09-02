"""
Test: AutoFinder Bild-On-Demand — app/autofinder_images.py + Router

Deckt:
  X)  approved cached -> KEIN Generation-Call
  Y)  missing -> ensure generation
  Z)  Generation Failure -> kontrollierter Fallback (status "failed")
  AA) nie mehr als final benötigte Bilder (Deckel)
  AB) zweite gleiche Anfrage nutzt den Cache (Manifest)
  + QA: offensichtlicher Ausschuss wird verworfen, max 1 Wiederholung
  + der SUCH-Endpunkt löst NIE eine Generierung aus

Netzwerk/Provider werden gefaked. Ausführen:  python test_autofinder_images.py
"""
import asyncio
import io
import os
import sys
import tempfile
import importlib

sys.path.insert(0, ".")
FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


_tmp = tempfile.mkdtemp(prefix="vira_af_img_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "k.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")
os.environ["AUTO_KI_AUTOFINDER_IMG_DIR"] = os.path.join(_tmp, "img")
# eigene Manifeste, damit weder die kuratierte Starter-Library noch das echte
# On-Demand-Manifest angefasst werden
_manifest_pfad = os.path.join(_tmp, "manifest.json")
os.environ["AUTO_KI_AUTOFINDER_ONDEMAND"] = os.path.join(_tmp, "ondemand.json")

import app.config as _cfg; importlib.reload(_cfg)
import app.database as _db; importlib.reload(_db)
_db.ensure_tables()

import app.autofinder_visual as av
av._MANIFEST_PFAD = __import__("pathlib").Path(_manifest_pfad)
av._ONDEMAND_PFAD = __import__("pathlib").Path(os.environ["AUTO_KI_AUTOFINDER_ONDEMAND"])
av.invalidiere_manifest_cache()

import app.autofinder_images as ai
importlib.reload(ai)
ai.BILD_DIR = __import__("pathlib").Path(os.environ["AUTO_KI_AUTOFINDER_IMG_DIR"])

from PIL import Image


def _weisses_lineart_png() -> bytes:
    from PIL import ImageDraw
    im = Image.new("RGB", (1024, 576), "white")
    d = ImageDraw.Draw(im)
    # grober Fahrzeugumriss + Räder — genug schwarze Linienfläche für die QA,
    # aber weit unter der "zu viel dunkle Fläche"-Grenze.
    d.line([(120, 400), (250, 250), (700, 220), (900, 400), (120, 400)], fill=(0, 0, 0), width=6)
    d.ellipse([250, 360, 340, 450], outline=(0, 0, 0), width=6)
    d.ellipse([680, 360, 770, 450], outline=(0, 0, 0), width=6)
    d.line([(300, 250), (600, 235)], fill=(0, 0, 0), width=5)
    buf = io.BytesIO(); im.save(buf, "PNG"); return buf.getvalue()


def _schwarzes_png() -> bytes:
    buf = io.BytesIO(); Image.new("RGB", (1024, 576), "black").save(buf, "PNG"); return buf.getvalue()


_GOOD = _weisses_lineart_png()
_BAD_BG = _schwarzes_png()

_gen_calls = {"n": 0}


def _make_gen(sequence):
    """sequence: Liste von 'good'|'bad'|'503' — je Versuch."""
    idx = {"i": 0}

    async def _gen(job):
        _gen_calls["n"] += 1
        kind = sequence[min(idx["i"], len(sequence) - 1)]
        idx["i"] += 1
        if kind == "503":
            return None, None, "ServerError: 503 UNAVAILABLE"
        if kind == "bad":
            return _BAD_BG, "image/png", None
        return _GOOD, "image/png", None
    return _gen


ITEM = dict(visual_key="kia--ceed--cd--kombi", marke="Kia", modell="Ceed",
            generation="CD", karosserie="kombi", baujahr_von=2018, baujahr_bis=None)

# ── QA-Prüfung direkt ─────────────────────────────────────────────────────
ok, _ = ai.pruefe_bild(_GOOD)
check("QA: sauberes Line-Art auf Weiß besteht die Prüfung", ok)
bad_ok, grund = ai.pruefe_bild(_BAD_BG)
check(f"QA: schwarzer Hintergrund fällt durch ({grund})", not bad_ok)
check("QA: Müll-Bytes fallen durch", not ai.pruefe_bild(b"not-an-image")[0])

# ── Y) missing -> generation ─────────────────────────────────────────────
_gen_calls["n"] = 0
ai.generiere_bild = _make_gen(["good"])
res = asyncio.run(ai.ensure_images([ITEM]))
check("Y: fehlendes Bild -> genau 1 Generierungsversuch", _gen_calls["n"] == 1)
check("Y: Ergebnis status 'generated' + image_url unter /api/v1/autofinder/img/",
      res[0]["status"] == "generated" and res[0]["image_url"] == "/api/v1/autofinder/img/kia--ceed--cd--kombi")
check("Y: Datei liegt im Backend-Bild-Verzeichnis", ai.bild_pfad("kia--ceed--cd--kombi") is not None)

# ── X) + AB) zweite gleiche Anfrage -> Cache, KEIN Generierungs-Call ─────
_gen_calls["n"] = 0
ai.generiere_bild = _make_gen(["good"])
res2 = asyncio.run(ai.ensure_images([ITEM]))
check("X/AB: bereits vorhandenes (Manifest) -> 0 Generierungs-Calls", _gen_calls["n"] == 0)
check("X/AB: Ergebnis status 'ready' aus dem Cache", res2[0]["status"] == "ready")

# ── Manifest-Trennung: on-demand landet NICHT im kuratierten Manifest ──────
import json as _json
_kur = _json.load(open(_manifest_pfad, encoding="utf-8")) if os.path.exists(_manifest_pfad) else []
check("Trennung: kuratiertes Manifest bleibt unberührt (kein /api/-Eintrag)",
      all(not e.get("image_url", "").startswith("/api/") for e in _kur))
_od = _json.load(open(os.environ["AUTO_KI_AUTOFINDER_ONDEMAND"], encoding="utf-8"))
check("Trennung: der on-demand-Eintrag liegt im separaten On-Demand-Manifest",
      any(e["visual_key"] == "kia--ceed--cd--kombi" and e["image_url"].startswith("/api/") for e in _od))
check("Trennung: lade_manifest_datei führt beide zusammen",
      "kia--ceed--cd--kombi" in av.lade_manifest_datei(force=True))

# ── Z) Generation-Failure -> kontrollierter Fallback ───────────────────
_gen_calls["n"] = 0
ai.generiere_bild = _make_gen(["503"])
resz = asyncio.run(ai.ensure_images([dict(ITEM, visual_key="ford--focus--mk4--kombi",
                                          marke="Ford", modell="Focus", generation="Mk4")]))
check("Z: Provider 503 -> status 'failed', kein Crash", resz[0]["status"] == "failed")
check("Z: 503 -> sofortiger Abbruch, KEIN zweiter Versuch", _gen_calls["n"] == 1)

# ── QA + max 1 Wiederholung: erster Versuch Ausschuss, zweiter gut ─────
_gen_calls["n"] = 0
ai.generiere_bild = _make_gen(["bad", "good"])
resr = asyncio.run(ai.ensure_images([dict(ITEM, visual_key="opel--corsa--f--kleinwagen",
                                          marke="Opel", modell="Corsa", generation="F",
                                          karosserie="kleinwagen")]))
check("QA: 1. Versuch Ausschuss -> 2. Versuch, dann generated", resr[0]["status"] == "generated" and _gen_calls["n"] == 2)

_gen_calls["n"] = 0
ai.generiere_bild = _make_gen(["bad", "bad"])
resr2 = asyncio.run(ai.ensure_images([dict(ITEM, visual_key="seat--leon--kl--kombi",
                                           marke="Seat", modell="Leon", generation="KL")]))
check("QA: zweimal Ausschuss -> failed, KEINE Endlosschleife (max 2 Versuche)",
      resr2[0]["status"] == "failed" and _gen_calls["n"] == 2)

# ── AA) Deckel: nie mehr als final benötigte / max 8 ──────────────────
_gen_calls["n"] = 0
ai.generiere_bild = _make_gen(["good"])
viele = [dict(ITEM, visual_key=f"marke{i}--m--g--kombi", marke=f"Marke{i}") for i in range(20)]
resv = asyncio.run(ai.ensure_images(viele))
check("AA: mehr als 8 angefragt -> höchstens 8 verarbeitet", len(resv) <= 8 and _gen_calls["n"] <= 8)

# ── N: der SUCH-Endpunkt löst NIE eine Generierung aus ────────────────
import app.autofinder_budget as _afb
import app.autofinder_enrich as _afe


async def _leer(sp, um):
    return {"candidates": []}


_afb.call_gemini_json = _leer
_afe.call_gemini_json = _leer

_gen_calls["n"] = 0
_router_gen_orig = ai.generiere_bild
ai.generiere_bild = _make_gen(["good"])
from fastapi.testclient import TestClient
from app.main import app as _app
_c = TestClient(_app)
_r = _c.post("/api/v1/autofinder", headers={"Authorization": "Bearer dev-key-change-in-prod"},
             json={"karosserie": ["kombi"], "kraftstoff": ["Benzin"]})
check("N: der Such-Endpunkt liefert 200", _r.status_code == 200)
check("N: der Such-Endpunkt hat NULL Bildgenerierungen ausgelöst", _gen_calls["n"] == 0)

# ── Router: /images/ensure + GET /img/{key} ──────────────────────────
_H = {"Authorization": "Bearer dev-key-change-in-prod"}
ai.generiere_bild = _make_gen(["good"])
_re = _c.post("/api/v1/autofinder/images/ensure", headers=_H, json={"items": [
    {"visual_key": "vw--polo--aw--kleinwagen", "marke": "VW", "modell": "Polo",
     "generation": "AW", "karosserie": "kleinwagen"}]})
check("Router: ensure -> 200", _re.status_code == 200)
_erg = _re.json()["results"][0]
check("Router: ensure erzeugt/liefert eine URL", _erg["status"] in ("generated", "ready") and _erg["image_url"])
_img = _c.get(_erg["image_url"])
check("Router: GET /img/{key} liefert das Bild (200, image/webp)",
      _img.status_code == 200 and "image" in _img.headers.get("content-type", ""))
_img404 = _c.get("/api/v1/autofinder/img/gibt--es--nicht--kombi")
check("Router: GET /img für unbekannten Key -> 404", _img404.status_code == 404)
check("Router: ensure mit leerer items-Liste -> 200 + leeres results",
      _c.post("/api/v1/autofinder/images/ensure", headers=_H, json={"items": []}).json()["results"] == [])

print()
if FEHLER:
    print(f"{len(FEHLER)} Test(s) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle AutoFinder-Bild-On-Demand-Tests bestanden.")
