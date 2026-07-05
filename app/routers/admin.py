from __future__ import annotations

import logging
import re
import traceback

from fastapi import APIRouter, Request, HTTPException, status
from pydantic import BaseModel

from fastapi.responses import StreamingResponse
from google.genai.errors import ServerError

from app.auth import verify_api_key
from app.admin_llm import entwurf_erstellen, entwurf_stream, generationen_auflisten, luecken_entwurf_erstellen
from app.db_writer import save_fahrzeug, patch_luecken
from app.database import get_conn, invalidate_referenzdaten_cache
from app.utf8 import UTF8JSONResponse
from app.gemini_retry import RateLimitExhausted

# Römische ↔ arabische Ziffern (Generationsbezeichnungen)
_ROM_TO_ARA: dict[str, str] = {
    "i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5",
    "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10",
    "xi": "11", "xii": "12",
}
_ARA_TO_ROM: dict[str, str] = {v: k for k, v in _ROM_TO_ARA.items()}

# VW-Markennamen-Aliase
_VW_ALIASES = {"vw", "volkswagen"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _gen_variants(gen_raw: str, modell: str) -> list[str]:
    """Gibt alle sinnvollen Schreibweisen einer Generation zurück."""
    g = gen_raw.strip().lower()
    # Modell-Prefix entfernen (z.B. "golf 8" → "8")
    m = modell.strip().lower()
    if g.startswith(m):
        g = g[len(m):].strip()
    candidates = {g, gen_raw.strip().lower()}
    # Arabisch ↔ Römisch
    if g in _ROM_TO_ARA:
        candidates.add(_ROM_TO_ARA[g])
    if g in _ARA_TO_ROM:
        candidates.add(_ARA_TO_ROM[g])
    return list(candidates)


def _marke_variants(marke: str) -> list[str]:
    m = marke.strip().lower()
    if m in _VW_ALIASES:
        return list(_VW_ALIASES)
    return [m]


def _find_baureihe(conn, marke: str, modell: str, generation: str):
    """
    Tolerante Baureihen-Suche: exakt → ID-Slug → Groß/Klein → VW-Alias → Generationsvarianten.
    Gibt sqlite3.Row oder None zurück.
    """
    # 1. Exakter Match
    row = conn.execute(
        "SELECT * FROM baureihe WHERE marke=? AND modell=? AND generation=?",
        (marke, modell, generation),
    ).fetchone()
    if row:
        return row

    # 2. Slug-basierte ID-Suche (z.B. "VW Golf 8" → "vw-golf-8")
    slug_id = _slug(f"{marke} {modell} {generation}")
    row = conn.execute("SELECT * FROM baureihe WHERE id=?", (slug_id,)).fetchone()
    if row:
        return row

    # 3. Alle Varianten von Marke + Generation durchprobieren
    marke_vars = _marke_variants(marke)
    modell_norm = modell.strip().lower()
    gen_vars = _gen_variants(generation, modell)

    for mv in marke_vars:
        for gv in gen_vars:
            row = conn.execute(
                "SELECT * FROM baureihe WHERE LOWER(marke)=? AND LOWER(modell)=? AND LOWER(generation)=?",
                (mv, modell_norm, gv),
            ).fetchone()
            if row:
                return row

    # 4. Slug-Varianten (alle Kombis aus Marke- und Generations-Varianten)
    for mv in marke_vars:
        for gv in gen_vars:
            sid = _slug(f"{mv} {modell_norm} {gv}")
            row = conn.execute("SELECT * FROM baureihe WHERE id=?", (sid,)).fetchone()
            if row:
                return row

    # 5. Nur Marke+Modell, dann beste Generations-Übereinstimmung suchen
    for mv in marke_vars:
        candidates = conn.execute(
            "SELECT * FROM baureihe WHERE LOWER(marke)=? AND LOWER(modell)=?",
            (mv, modell_norm),
        ).fetchall()
        if candidates:
            for r in candidates:
                rgen = r["generation"].lower()
                for gv in gen_vars:
                    if gv and (gv in rgen or rgen in gv):
                        return r

    return None

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", default_response_class=UTF8JSONResponse)


# ---------- Request-Modelle ----------

class EntwurfRequest(BaseModel):
    marke: str
    modell: str
    generation: str


class BatchRequest(BaseModel):
    anfrage: str   # z.B. "alle BMW 4er Generationen"


class SpeichernRequest(BaseModel):
    daten: dict    # das geprüfte Fahrzeug-JSON


class LueckenEntwurfRequest(BaseModel):
    marke: str
    modell: str
    generation: str
    # Optional: explizit angeben welche Felder generiert werden sollen.
    # Wenn None → automatisch aus DB erkennen (kaufberatung, schwachstellen, rueckrufe)
    felder: list[str] | None = None


class LueckenSpeichernRequest(BaseModel):
    baureihe_id: str
    daten: dict    # nur die zu patchenden Felder (kaufberatung / schwachstellen / rueckrufe)


# ---------- Endpunkte ----------

def _llm_error(exc: Exception) -> HTTPException:
    """Wandelt LLM-Fehler in passende HTTP-Fehler um. Loggt immer den vollen Traceback."""
    log.error(
        "Admin-LLM-Fehler [%s]: %s\n%s",
        type(exc).__name__, exc, traceback.format_exc(),
    )

    if isinstance(exc, RateLimitExhausted):
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"fehler": {"code": "rate_limit", "nachricht": str(exc)}},
        )
    if isinstance(exc, ValueError) and "unvollständig" in str(exc):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"fehler": {"code": "antwort_unvollstaendig", "nachricht": str(exc)}},
        )
    if isinstance(exc, ServerError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"fehler": {"code": "llm_nicht_erreichbar",
                               "nachricht": f"Gemini nicht erreichbar: {exc}"}},
        )
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={"fehler": {"code": "llm_fehler", "nachricht": str(exc)}},
    )


@router.post("/entwurf", summary="LLM erstellt Schema-Entwurf — vollständig (non-streaming)")
async def entwurf(body: EntwurfRequest, request: Request):
    verify_api_key(request)
    try:
        data = await entwurf_erstellen(body.marke, body.modell, body.generation)
    except Exception as e:
        raise _llm_error(e)
    return {"entwurf": data}


async def _sse_entwurf(marke: str, modell: str, generation: str):
    async for fragment in entwurf_stream(marke, modell, generation):
        yield f"data: {fragment}\n\n"
    yield "data: [DONE]\n\n"


@router.post("/entwurf-stream", summary="LLM erstellt Schema-Entwurf — SSE-Streaming")
async def entwurf_stream_endpoint(body: EntwurfRequest, request: Request):
    verify_api_key(request)
    return StreamingResponse(
        _sse_entwurf(body.marke, body.modell, body.generation),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/batch", summary="LLM listet Generationen auf (Batch-Vorbereitung)")
async def batch(body: BatchRequest, request: Request):
    verify_api_key(request)
    try:
        generationen = await generationen_auflisten(body.anfrage)
    except Exception as e:
        raise _llm_error(e)
    return {"generationen": generationen}


@router.post("/speichern", summary="Geprüftes JSON in SQLite + ChromaDB schreiben")
async def speichern(body: SpeichernRequest, request: Request):
    verify_api_key(request)

    daten = body.daten
    if not all(k in daten for k in ("marke", "modell", "generation")):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"fehler": {"code": "validierung", "nachricht": "marke, modell und generation sind Pflicht."}},
        )

    try:
        bid = save_fahrzeug(daten)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"fehler": {"code": "db_fehler", "nachricht": str(e)}},
        )

    # Neue/geänderte Baureihe muss sofort für Chat/Kauf-/Verkaufscheck sichtbar sein,
    # nicht erst nach Ablauf des 60s-Referenzdaten-Caches (siehe database.py).
    invalidate_referenzdaten_cache()

    return {
        "gespeichert": True,
        "baureihe_id": bid,
        "nachricht": f"{daten['marke']} {daten['modell']} {daten['generation']} erfolgreich gespeichert.",
    }


# ── Fehlende Felder erkennen und Entwurf generieren ──────────────────────────

_LUECKEN_FELDER = ("kaufberatung", "schwachstellen_baureihe", "rueckrufe")


@router.post("/luecken-entwurf", summary="Nur fehlende Felder einer bestehenden Baureihe per Gemini nachgenerieren")
async def luecken_entwurf(body: LueckenEntwurfRequest, request: Request):
    """
    1. Liest die bestehende Baureihe aus der DB.
    2. Erkennt automatisch, welche Felder leer sind (kaufberatung, schwachstellen, rückrufe).
    3. Lässt Gemini nur diese Felder als Entwurf generieren — keine bestehenden Daten berührt.
    4. Gibt Entwurf zurück zur Prüfung. Speichern erst via /admin/luecken-speichern.
    """
    verify_api_key(request)

    # Baureihe aus DB lesen — tolerante Suche (VW=Volkswagen, 8=VIII, Groß/Klein egal)
    with get_conn() as conn:
        row = _find_baureihe(conn, body.marke, body.modell, body.generation)
        if row is None:
            raise HTTPException(
                status_code=404,
                detail={"fehler": {"code": "nicht_gefunden",
                                   "nachricht": (
                                       f"'{body.marke} {body.modell} {body.generation}' "
                                       "nicht in der Datenbank gefunden. "
                                       "Tipp: Marke, Modell und Generation genauer angeben."
                                   )}},
            )
        b = dict(row)
        bid = b["id"]

        schw_count = conn.execute(
            "SELECT COUNT(*) FROM schwachstelle_baureihe WHERE baureihe_id=?", (bid,)
        ).fetchone()[0]
        ruf_count = conn.execute(
            "SELECT COUNT(*) FROM rueckruf WHERE baureihe_id=?", (bid,)
        ).fetchone()[0]

    # Fehlende Felder automatisch erkennen (oder Request übernehmen)
    if body.felder:
        fehlende = [f for f in body.felder if f in _LUECKEN_FELDER]
    else:
        fehlende = []
        if not b.get("kaufberatung"):
            fehlende.append("kaufberatung")
        if schw_count == 0:
            fehlende.append("schwachstellen_baureihe")
        if ruf_count == 0:
            fehlende.append("rueckrufe")

    if not fehlende:
        return {
            "baureihe_id": bid,
            "fehlende_felder": [],
            "entwurf": None,
            "nachricht": "Alle Felder bereits befüllt — kein Entwurf nötig.",
        }

    kontext = {
        "bauzeitraum_von": b.get("bauzeitraum_von"),
        "bauzeitraum_bis": b.get("bauzeitraum_bis"),
        "segment":         b.get("segment"),
        "erkennung_generation": b.get("erkennung_generation"),
    }

    try:
        entwurf = await luecken_entwurf_erstellen(
            body.marke, body.modell, body.generation, fehlende, kontext
        )
    except Exception as e:
        raise _llm_error(e)

    return {
        "baureihe_id": bid,
        "fehlende_felder": fehlende,
        "entwurf": entwurf,
        "nachricht": (
            f"Entwurf für {len(fehlende)} Feld(er) erstellt: {', '.join(fehlende)}. "
            "Prüfe und speichere via POST /admin/luecken-speichern."
        ),
    }


@router.post("/luecken-speichern", summary="Geprüfte Lücken-Felder in DB schreiben (kein Überschreiben bestehender Daten)")
async def luecken_speichern(body: LueckenSpeichernRequest, request: Request):
    """
    Schreibt nur die geprüften Lücken-Felder in die DB.
    Bestehende Motoren, Ausstattungslinien und andere Felder bleiben unverändert.

    Erlaubte Felder in `daten`: kaufberatung, schwachstellen_baureihe, rueckrufe.
    """
    verify_api_key(request)

    # Sicherstellen, dass die Baureihe existiert
    with get_conn() as conn:
        exists = conn.execute(
            "SELECT id FROM baureihe WHERE id=?", (body.baureihe_id,)
        ).fetchone()
    if not exists:
        raise HTTPException(
            status_code=404,
            detail={"fehler": {"code": "nicht_gefunden",
                               "nachricht": f"Baureihe '{body.baureihe_id}' nicht in der Datenbank."}},
        )

    # Nur erlaubte Felder durchlassen
    erlaubt = {k: v for k, v in body.daten.items() if k in _LUECKEN_FELDER}
    if not erlaubt:
        raise HTTPException(
            status_code=422,
            detail={"fehler": {"code": "keine_erlaubten_felder",
                               "nachricht": f"Erlaubte Felder: {', '.join(_LUECKEN_FELDER)}"}},
        )

    try:
        patch_luecken(body.baureihe_id, erlaubt)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"fehler": {"code": "db_fehler", "nachricht": str(e)}},
        )

    gespeicherte_felder = list(erlaubt.keys())
    return {
        "gespeichert": True,
        "baureihe_id": body.baureihe_id,
        "gespeicherte_felder": gespeicherte_felder,
        "nachricht": f"Lücken erfolgreich nachgefüllt: {', '.join(gespeicherte_felder)}.",
    }
