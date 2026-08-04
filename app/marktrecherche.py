from __future__ import annotations

"""
Adaptive, QUALITÄTS-GESTEUERTE Marktrecherche (Reliability-Sprint §1–§4).

Frühere Logik stoppte, sobald "mittel" erreicht war — populäre Fahrzeuge landeten
dann oft dünn ("niedrig"/"mittel"), obwohl mit weiterer Recherche eine belastbare
Basis erreichbar gewesen wäre. Das widerspricht der Gold-Anforderung (§0): eine
niedrige Datenqualität darf KEIN abgeschlossenes bezahltes Endergebnis sein.

Diese Funktion sucht in mehreren Stufen (§2), AKKUMULIERT die Treffer und bewertet
nach JEDER Stufe die Zahl der AKZEPTIERTEN, modelltreuen Vergleiche via
analysiere_markt. Sie vertieft weiter, solange die Qualität noch nicht "hoch" ist,
und endet erst, wenn "hoch" erreicht ist ODER alle Stufen ausgeschöpft sind — nie
anhand roher Trefferzahl. Kein Fahrzeug-Hardcoding.

Reicht die Qualität nach voller Vertiefung nicht (kein belastbarer Median), gilt der
Check als `research_failed` (§4): der aufrufende Flow erzeugt KEINEN fertigen Bericht
und erstattet das Kontingent zurück.
"""

import logging
import time
from urllib.parse import urlparse

from app.marktvergleich import analysiere_markt
from app.web_search import tavily_search

log = logging.getLogger(__name__)


class RechercheUnzureichend(Exception):
    """Nach voller Recherche keine belastbare Preisbasis (§4). Trägt die (schwache)
    Marktanalyse und eine freundliche Nutzermeldung mit — der Router erstattet das
    Kontingent zurück und liefert KEINEN fertigen Bericht."""

    def __init__(self, marktanalyse, nachricht: str):
        self.marktanalyse = marktanalyse
        self.nachricht = nachricht
        super().__init__(nachricht)


NACHRICHT_UNZUREICHEND = (
    "VIRA konnte für dieses Fahrzeug aktuell keine belastbare Marktdatenbasis finden — "
    "es liegen zu wenige wirklich vergleichbare Angebote vor, um einen zuverlässigen "
    "Marktwert und eine belastbare Preisbewertung zu erstellen. Dieser Check wurde "
    "deshalb NICHT abgeschlossen und NICHT von deinem Kontingent abgezogen. Du kannst "
    "es gleich erneut versuchen oder das Fahrzeug genauer angeben (Motor, Baujahr, "
    "Kilometerstand)."
)


def _norm_url(url: str) -> str:
    try:
        p = urlparse(url or "")
        return f"{p.netloc.lower()}{p.path.rstrip('/')}".removeprefix("www.")
    except Exception:
        return url or ""


def _merge(dst: list[dict], seen: set[str], results: list[dict]) -> int:
    """Fügt nur echt neue URLs hinzu (dedupliziert über Stufen). Gibt #neu zurück."""
    neu = 0
    for r in results or []:
        k = _norm_url(r.get("url", ""))
        if k and k not in seen:
            seen.add(k)
            dst.append(r)
            neu += 1
    return neu


def genug(ma) -> bool:
    """Die Recherche DARF früh enden: belastbarer Median UND HOHE Datenqualität.

    Bewusst streng auf "hoch" (nicht mehr "mittel"): populäre Fahrzeuge sollen den
    Normalfall "hoch" tatsächlich per Recherche erreichen, statt bei der ersten
    mittleren Stufe abzubrechen (§0/§3). Wird "hoch" nicht erreicht, laufen ALLE
    Stufen — erst danach entscheidet der Aufrufer über completed_medium bzw.
    research_failed.
    """
    return bool(ma and ma.median_eur and ma.datenqualitaet == "hoch")


def research_status(ma) -> str:
    """Quality-Gate-Ergebnis (§1/§14): completed_high | completed_medium | research_failed.

    - "hoch"  + belastbarer Median -> completed_high (Normalfall)
    - "mittel"+ belastbarer Median -> completed_medium (seltene Ausnahme, §3)
    - sonst (kein Median / "niedrig") -> research_failed (§4)
    """
    if ma and ma.median_eur and ma.datenqualitaet == "hoch":
        return "completed_high"
    if ma and ma.median_eur and ma.datenqualitaet == "mittel":
        return "completed_medium"
    return "research_failed"


def baue_deep_queries(req, generation: str | None = None) -> list[str]:
    """Query-Vertiefungsleiter (§2): exakt -> alternative Bezeichnungen -> Generation/
    Motor getrennt -> Baujahresfenster -> direkte Portale -> breit. Kein Hardcoding.

    Erwartet ein Objekt mit marke/modell/motor/baujahr/kilometerstand (Kauf- ODER
    Verkaufscheck-Request). Leere/None-Teile werden weggefiltert."""
    marke = getattr(req, "marke", None)
    modell = getattr(req, "modell", None)
    motor = getattr(req, "motor", None)
    bj = getattr(req, "baujahr", None)
    km = getattr(req, "kilometerstand", None)

    def q(*teile) -> str:
        return " ".join(str(t) for t in teile if t)

    queries: list[str] = []
    # 1) exakt (Motor + Baujahr + km-Fenster)
    queries.append(q(marke, modell, motor, bj,
                     f"{km // 10000 * 10000} km" if km else None,
                     "gebraucht kaufen Deutschland"))
    # 2) alternative Bezeichnung (ohne Motor, mit Baujahr)
    queries.append(q(marke, modell, bj, "Gebrauchtpreis Deutschland"))
    # 3) Generation und Motor getrennt variiert
    queries.append(q(marke, modell, generation, motor, "gebraucht Deutschland"))
    # 4) Baujahresfenster kontrolliert erweitern (±1 Jahr)
    if bj:
        queries.append(q(marke, modell, f"{bj - 1} {bj} {bj + 1}", "gebrauchtwagen Deutschland"))
    # 5) direkte Fahrzeugportale statt allgemeiner Preisübersichten
    queries.append(q(marke, modell, bj, "gebrauchtwagen mobile.de autoscout24 Angebote"))
    # 6) breit (Kilometerfenster / Restmarkt)
    queries.append(q(marke, modell, "gebrauchtwagen kaufen Deutschland Preis"))
    # 7) noch eine Portal-/Generation-Variante für Quellenvielfalt
    queries.append(q(marke, modell, generation, "gebrauchtwagen Angebote autouncle"))

    # dedupliziert, Reihenfolge erhalten, nur nicht-leere
    out: list[str] = []
    for x in queries:
        x = x.strip()
        if x and x not in out:
            out.append(x)
    return out


async def vertiefe_marktrecherche(
    initial_results: list[dict],
    deep_queries: list[str],
    ziel: dict,
    angebot_eur: int | None,
    exclude_domains: list[str] | None,
    *,
    count: int = 10,
    zweck: str = "marktvergleich",
    max_stufen: int = 7,
):
    """Vertieft die Marktrecherche adaptiv bis zur Qualitätsschwelle. Gibt
    (accumulated_results, marktanalyse, diagnose) zurück."""
    t0 = time.perf_counter()
    accumulated: list[dict] = list(initial_results or [])
    seen: set[str] = {_norm_url(r.get("url", "")) for r in accumulated}

    ma = analysiere_markt(accumulated, ziel, angebot_eur)
    stufen: list[dict] = [{
        "stufe": "initial", "roh": len(accumulated), "neu": len(accumulated),
        "akzeptiert": ma.verwendet, "quali": ma.datenqualitaet,
    }]

    if not genug(ma):
        # Stufen 1..N: höheres max_results (§2 Punkt 7) + akkumulierend, bis "hoch".
        for i, query in enumerate(deep_queries[:max_stufen], 1):
            if not query or not query.strip():
                continue
            # Ab der 4. Stufe Raw-Content aktivieren (§2 Punkt 8): mehr Text pro
            # Treffer -> mehr extrahierbare Preis-Datenpunkte, wenn die Snippets dünn sind.
            raw = i >= 4
            results = await tavily_search(query, count=count, exclude_domains=exclude_domains,
                                          include_raw_content=raw)
            neu = _merge(accumulated, seen, results)
            ma = analysiere_markt(accumulated, ziel, angebot_eur)
            stufen.append({
                "stufe": i, "query": query[:80], "roh": len(results), "neu": neu,
                "akzeptiert": ma.verwendet, "quali": ma.datenqualitaet,
            })
            if genug(ma):
                break

    status = research_status(ma)
    diagnose = {
        "zweck": zweck,
        "stufen": stufen,
        "roh_seiten": len(accumulated),
        "gefunden_datenpunkte": ma.gefunden if ma else 0,
        "akzeptiert": ma.verwendet if ma else 0,
        "verworfen": (ma.gefunden - ma.verwendet) if ma else 0,
        "datenqualitaet": ma.datenqualitaet if ma else "niedrig",
        "research_status": status,
        "domains": list(ma.quellen_domains) if ma else [],
        "dauer_ms": round((time.perf_counter() - t0) * 1000),
    }
    log.info("[RECHERCHE %s] status=%s akzeptiert=%d/%d quali=%s stufen=%d dauer=%dms domains=%s",
             zweck, status, diagnose["akzeptiert"], diagnose["gefunden_datenpunkte"],
             diagnose["datenqualitaet"], len(stufen), diagnose["dauer_ms"], diagnose["domains"])
    return accumulated, ma, diagnose
