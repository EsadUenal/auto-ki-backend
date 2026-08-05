from __future__ import annotations

"""
Phase 5 — VIRA Dealer: Berechtigung + deterministische Ableitungen.

Kein LLM, keine neue Analyse-Engine: die VIRA-Signale (Empfehlung, Preisbewertung,
Marktmedian, Risiken, Key Findings) werden AUS DEN BEREITS GESPEICHERTEN Kauf-/
Verkaufscheck-Ergebnissen gelesen. Der Margenrechner ist reine Zahlenlogik; fehlende
Grundwerte -> None (nie eine erfundene 0-€-Marge).
"""

import logging

from fastapi import Depends, HTTPException

from app.database import get_conn
from app.entitlements import has_dealer_access
from app.models import (
    DealerFinance, DealerTriage, DealerVehicle, DealerViraKauf, DealerViraVerkauf,
)
from app.routers.user_auth import get_current_user_id

log = logging.getLogger(__name__)


# ── Berechtigung ─────────────────────────────────────────────────────────────

def require_dealer(user_id: int = Depends(get_current_user_id)) -> int:
    """FastAPI-Dependency: nur Konten mit effektiver Dealer-Berechtigung. 403 sonst.

    Effektive Regel (eine Quelle: entitlements.has_dealer_access): abo_typ == "max"
    ODER manueller ist_haendler-Override. Wird bei JEDER Anfrage aus dem aktuellen
    Account abgeleitet — nach einer MAX-Kündigung entfällt der Zugriff automatisch.
    Backend-seitige Prüfung — Frontend-Verstecken allein reicht nicht (§15/§28)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT abo_typ, ist_haendler FROM users WHERE id=? AND deleted_at IS NULL", (user_id,)
        ).fetchone()
    if not row or not has_dealer_access(row["abo_typ"], row["ist_haendler"]):
        raise HTTPException(
            status_code=403,
            detail={"fehler": {"code": "dealer_required",
                               "nachricht": "Dieser Bereich ist nur für VIRA-Dealer-Konten (MAX-Tarif) verfügbar."}},
        )
    return user_id


# ── Margenrechner (deterministisch) ──────────────────────────────────────────

def berechne_finanzen(v: dict) -> DealerFinance:
    """Gesamteinsatz / mögliche / realisierte Bruttomarge aus den eingegebenen Zahlen.

    gesamteinsatz  = einkaufspreis + nebenkosten           (nur wenn Einkauf gesetzt)
    mögliche Marge = geplanter Verkauf − Gesamteinsatz      (nur wenn beide vorhanden)
    realisierte    = tatsächlicher Verkauf − Gesamteinsatz  (nur wenn beide vorhanden)
    Marge-%        = Marge / Verkaufspreis · 100
    Fehlt ein Grundwert -> None (keine erfundene 0-€-Marge). Negative Marge bleibt negativ.
    """
    ek = v.get("einkaufspreis")
    nk = v.get("nebenkosten")
    gp = v.get("geplanter_verkaufspreis")
    tp = v.get("tatsaechlicher_verkaufspreis")

    gesamt = ek + (nk or 0) if ek is not None else None

    moeg = moeg_pct = None
    if gesamt is not None and gp is not None:
        moeg = gp - gesamt
        moeg_pct = round(moeg / gp * 100, 1) if gp > 0 else None

    real = real_pct = None
    if gesamt is not None and tp is not None:
        real = tp - gesamt
        real_pct = round(real / tp * 100, 1) if tp > 0 else None

    return DealerFinance(
        einkaufspreis=ek, nebenkosten=nk, gesamteinsatz=gesamt,
        geplanter_verkaufspreis=gp,
        moegliche_bruttomarge=moeg, moegliche_marge_pct=moeg_pct,
        tatsaechlicher_verkaufspreis=tp,
        realisierte_bruttomarge=real, realisierte_marge_pct=real_pct,
    )


# ── VIRA-Signale aus verknüpften Checks lesen (nichts neu berechnen) ─────────

def _marktanalyse(ergebnis: dict) -> dict | None:
    for i in ergebnis.get("insights") or []:
        if i.get("kategorie") == "marktvergleich":
            return i.get("marktanalyse")
    return None


_EMPF_MAP = {
    "kaufen": "kaufen",
    "kaufen_nach_besichtigung": "nach_pruefung",
    "nur_mit_werkstattpruefung": "nach_pruefung",
    "preis_nachverhandeln": "vorsicht",
    "hohes_risiko": "vorsicht",
    "finger_weg": "nicht_empfohlen",
}
_PREIS_MAP = {
    "extrem_guenstig": "guenstig", "guenstig": "guenstig",
    "marktgerecht": "marktgerecht",
    "teuer": "teuer", "extrem_teuer": "teuer",
}


# Reliability-Sprint 3 (§27/§36): neue Werte (variant_match/series_only/
# confirmed_by_vin) UND die alten Vor-Sprint-3-Werte (exakt/wahrscheinlich) — alte
# gespeicherte Checks werden beim Laden NICHT migriert (routers/checks.py liest das
# JSON roh), also müssen beide Wertemengen hier erkannt werden.
_RUECKRUF_RELEVANT_WERTE = ("confirmed_by_vin", "variant_match", "series_only", "exakt", "wahrscheinlich")


def _risiko_signal(ergebnis: dict) -> str:
    """Deterministische Risikoampel aus insights/key_findings des Kaufchecks."""
    insights = ergebnis.get("insights") or []
    kfs = ergebnis.get("key_findings") or []
    rueckruf_relevant = any(
        i.get("kategorie") == "rueckruf" and (i.get("applicability") or "").lower() in _RUECKRUF_RELEVANT_WERTE
        for i in insights
    )
    schwach_hoch = any(
        i.get("kategorie") == "schwachstelle" and (i.get("schweregrad") or "").lower() in ("hoch", "kritisch", "sehr hoch")
        for i in insights
    )
    motorproblem = any(i.get("kategorie") == "motorproblem" for i in insights)
    hat_kritisch = any((f.get("stufe") or "").lower() == "kritisch" for f in kfs)
    hat_warnung = any((f.get("stufe") or "").lower() == "warnung" for f in kfs)

    if not ergebnis:
        return "unklar"
    if hat_kritisch or schwach_hoch or motorproblem:
        return "erhoeht"
    if rueckruf_relevant:
        return "pruefen"
    if hat_warnung:
        return "mittel"
    if ergebnis.get("baureihe_erkannt"):
        return "gering"
    return "unklar"


def _risiko_hinweise(ergebnis: dict) -> list[str]:
    """Kurze Risiko-Stichpunkte aus den Key Findings (kritisch/warnung) + relevanten
    Rückrufen — für die Anzeige am Dealer-Fahrzeug."""
    out: list[str] = []
    for f in ergebnis.get("key_findings") or []:
        if (f.get("stufe") or "").lower() in ("kritisch", "warnung"):
            t = (f.get("titel") or "").strip()
            if t and t not in out:
                out.append(t)
    # Generischen Rückruf-Hinweis nur ergänzen, wenn kein Key Finding Rückrufe bereits
    # abdeckt (sonst doppelt: "2 relevante Rückrufe" + "Relevanter Rückruf").
    if not any("rückruf" in h.lower() for h in out):
        for i in ergebnis.get("insights") or []:
            if i.get("kategorie") == "rueckruf" and (i.get("applicability") or "").lower() in _RUECKRUF_RELEVANT_WERTE:
                out.append("Relevanter Rückruf")
                break
    return out[:4]


def lese_kauf_vira(kaufcheck_id: int | None, ergebnis: dict | None) -> DealerViraKauf:
    if not ergebnis:
        return DealerViraKauf(vorhanden=False, kaufcheck_id=kaufcheck_id)
    ma = _marktanalyse(ergebnis) or {}
    return DealerViraKauf(
        vorhanden=True,
        kaufcheck_id=kaufcheck_id,
        empfehlung=ergebnis.get("empfehlung"),
        preis_bewertung=ergebnis.get("preis_bewertung"),
        markt_median=ma.get("median_eur"),
        markt_min=ergebnis.get("marktpreis_min"),
        markt_max=ergebnis.get("marktpreis_max"),
        risiko_hinweise=_risiko_hinweise(ergebnis),
        key_findings_count=len(ergebnis.get("key_findings") or []),
    )


def lese_verkauf_vira(verkaufscheck_id: int | None, ergebnis: dict | None) -> DealerViraVerkauf:
    if not ergebnis:
        return DealerViraVerkauf(vorhanden=False, verkaufscheck_id=verkaufscheck_id)
    ma = _marktanalyse(ergebnis) or {}
    la = ergebnis.get("listing_analyse") or {}
    return DealerViraVerkauf(
        vorhanden=True,
        verkaufscheck_id=verkaufscheck_id,
        empfohlener_preis=ergebnis.get("empfohlener_preis"),
        markt_median=ma.get("median_eur"),
        inserat_qualitaet=la.get("qualitaet"),
        hat_optimierung=bool(ergebnis.get("inserat_optimierung")),
    )


# ── Triage + "braucht Aufmerksamkeit" (deterministisch) ──────────────────────

def _triage(vira: DealerViraKauf, ergebnis: dict | None, finanzen: DealerFinance) -> DealerTriage:
    empf = _EMPF_MAP.get((vira.empfehlung or "").lower(), "unklar") if vira.vorhanden else "unklar"
    preis = _PREIS_MAP.get((vira.preis_bewertung or "").lower(), "unklar") if vira.vorhanden else "unklar"
    risiko = _risiko_signal(ergebnis) if ergebnis else "unklar"
    return DealerTriage(empfehlung=empf, preis=preis, risiko=risiko,
                        marge_eur=finanzen.moegliche_bruttomarge)


def _aufmerksamkeit(v: dict, vira: DealerViraKauf, ergebnis: dict | None,
                    finanzen: DealerFinance, triage: DealerTriage) -> list[str]:
    gruende: list[str] = []
    # Kaufcheck-Warnungen (kritisch/warnung)
    if vira.risiko_hinweise:
        gruende.append("Kaufcheck-Warnung: " + vira.risiko_hinweise[0])
    # Preis deutlich über Markt
    if triage.preis == "teuer":
        gruende.append("Preis über Marktniveau")
    # Negative Marge (geplant ODER realisiert)
    marge = finanzen.realisierte_bruttomarge if finanzen.realisierte_bruttomarge is not None else finanzen.moegliche_bruttomarge
    if marge is not None and marge < 0:
        gruende.append("Negative Marge")
    # Im Bestand ohne Einkaufspreis
    if v.get("status") == "im_bestand" and v.get("einkaufspreis") is None:
        gruende.append("Einkaufspreis fehlt")
    # Unklare Datenlage (Check vorhanden, aber schwaches Vertrauen)
    if ergebnis and (ergebnis.get("vertrauen") or "").lower() == "niedrig":
        gruende.append("Datenlage unklar")
    # dedupe, ruhige Begrenzung
    ausgabe: list[str] = []
    for g in gruende:
        if g not in ausgabe:
            ausgabe.append(g)
    return ausgabe[:4]


# ── Zusammenbau ──────────────────────────────────────────────────────────────

def build_dealer_vehicle(v: dict, kauf_ergebnis: dict | None,
                         verkauf_ergebnis: dict | None) -> DealerVehicle:
    """Baut das vollständige DealerVehicle-Antwortobjekt aus der DB-Zeile (dict) und
    den bereits geladenen (optionalen) Check-Ergebnissen."""
    finanzen = berechne_finanzen(v)
    vira = lese_kauf_vira(v.get("kaufcheck_id"), kauf_ergebnis)
    verkauf = lese_verkauf_vira(v.get("verkaufscheck_id"), verkauf_ergebnis)
    triage = _triage(vira, kauf_ergebnis, finanzen)
    gruende = _aufmerksamkeit(v, vira, kauf_ergebnis, finanzen, triage)
    return DealerVehicle(
        id=v["id"],
        marke=v.get("marke"), modell=v.get("modell"), baureihe=v.get("baureihe"),
        motor=v.get("motor"), baujahr=v.get("baujahr"), kilometerstand=v.get("kilometerstand"),
        status=v["status"], interne_notiz=v.get("interne_notiz"),
        kaufcheck_id=v.get("kaufcheck_id"), verkaufscheck_id=v.get("verkaufscheck_id"),
        created_at=v.get("created_at"), updated_at=v.get("updated_at"), sold_at=v.get("sold_at"),
        finanzen=finanzen, vira=vira, verkauf=verkauf, triage=triage,
        braucht_aufmerksamkeit=bool(gruende), aufmerksamkeit_gruende=gruende,
    )
