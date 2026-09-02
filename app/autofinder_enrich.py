from __future__ import annotations

"""
AutoFinder — Top-Ergebnis-Enrichment via Gemini (Quality-Enrichment-Runde).

WAS DAS IST
-----------
Nach dem deterministischen Ranking + Fit-Filter (app/autofinder.py,
app/autofinder_fit.py) steht die finale, kleine Kandidatenliste fest. Dieses
Modul reichert GENAU DIESE Liste in EINEM einzigen strukturierten Gemini-Call
an um:

  why_fits        3–5 konkrete, an die Nutzeranfrage gebundene Gründe
  trade_offs      2–4 ehrliche, relevante Nachteile / Einschränkungen
  known_points    bis zu 3 gestützte bekannte Punkte (DB-Fakten, klar benannt)
  estimated_price_min / estimated_price_max / price_confidence
                  grobe, konservative Gebrauchtwagen-Preisorientierung —
                  KEINE Live-Marktdaten, KEIN Portal, KEIN Median, KEINE
                  Einzelzahl, NIE als "Marktpreis" bezeichnet.

WAS GEMINI NICHT DARF
---------------------
- keine Kandidaten hinzufügen/entfernen/umbenennen, keine technischen Daten ändern
- keine erfundenen Zuverlässigkeits-Behauptungen ("Reliability-Claims")
- als `rejected` gesperrte Fakten nie verwenden (werden gar nicht erst übergeben)
- unverifizierte Schwächen nicht als konkreten, sicheren Fahrzeugmangel darstellen
- das Wort "(ungeprüft)" / "(geprüft)" erscheint nicht in der Consumer-Ausgabe

AUSFALLSICHERHEIT
-----------------
`enrich_kandidaten()` fängt JEDEN Fehler (Provider 503/504 über
`GeminiFehlgeschlagen`, kaputtes JSON, leere Antwort) und gibt eine leere
Zuordnung + `ausgefallen=True` zurück. Der Router baut daraus einen
deterministischen Fallback (why_fits aus match_gruende, trade_offs nur aus
verifizierten DB-Fakten, kein Preis) und einen neutralen Hinweis. Kein 500.

KOSTEN
------
GENAU EIN Gemini-Call pro Suche für ALLE finalen Kandidaten zusammen. Nutzt
`app.car_lookup.call_gemini_json` unverändert (bestehendes Retry/Backoff).
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.car_lookup import call_gemini_json
from app.gemini_retry import GeminiFehlgeschlagen

log = logging.getLogger(__name__)

PRICE_CONF_WERTE = ("HIGH", "MEDIUM", "LOW", "UNKNOWN")

_MAX_WHY = 5
_MIN_WHY = 3
_MAX_TRADE = 4
_MAX_KNOWN = 3

# Grobe Plausibilitätsgrenzen für die Preisorientierung (EUR). Werte außerhalb
# werden verworfen — keine 900-EUR- und keine 900.000-EUR-"Schätzung".
_PREIS_MIN_UNTERGRENZE = 500
_PREIS_MAX_OBERGRENZE = 400_000


@dataclass
class Enrichment:
    why_fits: list[str] = field(default_factory=list)
    trade_offs: list[str] = field(default_factory=list)
    known_points: list[str] = field(default_factory=list)
    estimated_price_min: int | None = None
    estimated_price_max: int | None = None
    price_confidence: str = "UNKNOWN"


def _kandidat_id(k: Any) -> str:
    return getattr(k, "candidate_id", None) or getattr(k, "variante_id", None) or ""


# "(ungeprüft)" / "(geprüft)" nie im Consumer-Text (§Punkt 6 / Test W).
_LABEL_RE = re.compile(r"\s*\((?:un)?geprüft\)\s*:?\s*", re.IGNORECASE)


def strip_pruef_label(text: str) -> str:
    return _LABEL_RE.sub(" ", text).strip(" :–-")


_SYSTEM_PROMPT = """Du erklärst einem Autokäufer, warum bereits feststehende Fahrzeug-Empfehlungen zu SEINER konkreten Anfrage passen, und ordnest ehrlich ihre Nachteile ein.

STRIKTE REGELN:
- Du fügst KEINE Fahrzeuge hinzu, entfernst keine und änderst KEINE technischen Daten. Du antwortest nur zu den gegebenen candidate_id-Werten, unverändert übernommen.
- Du erfindest KEINE Zuverlässigkeits-Behauptungen. Nenne einen Schwachpunkt nur, wenn er dir als Kontext gegeben wurde ODER allgemein sehr gut belegt und modelltypisch ist.
- Als "bekannter Punkt" gegebene Hinweise ohne Prüfvermerk NICHT als sicheren, konkreten Defekt dieses Fahrzeugs darstellen — allenfalls vorsichtig als "wird gelegentlich berichtet".
- Kein Wort wie "(ungeprüft)" oder "(geprüft)" in deinen Texten.
- Preis: Du gibst NUR eine grobe, konservative Gebrauchtwagen-Preisorientierung als BREITE Spanne (min/max in EUR) für den deutschen Markt, basierend auf Marke/Modell/Generation/Motor/Baujahr/plausibler Laufleistung aus allgemeinem Wissen. NIEMALS eine Einzelzahl, NIEMALS "Marktpreis"/"Marktwert"/"aktueller Preis". Wenn du unsicher bist: breitere Spanne und price_confidence LOW.

Für jeden Kandidaten:
- why_fits: 3 bis 5 konkrete, an die Nutzeranfrage (Budget, Nutzung, Jahreskilometer, Prioritäten, gewünschte Karosserie/Kraftstoff/Getriebe/Leistung) gebundene Gründe. Konkret, keine Floskeln.
- trade_offs: 2 bis 4 echte, für diesen Nutzer relevante Nachteile oder Einschränkungen (z.B. Verbrauch, Unterhalt, Kofferraum, Wertverlust, typische Reparaturkosten, Eignung fürs Nutzungsmuster).
- known_points: 0 bis 3 gestützte, klar benannte bekannte Punkte (z.B. modelltypische Schwachstelle, Rückruf-Thema) — nur wenn gut belegt, sonst leere Liste.
- estimated_price_min, estimated_price_max: ganze EUR-Zahlen, min < max, realistische breite Spanne.
- price_confidence: HIGH | MEDIUM | LOW | UNKNOWN.

Antworte AUSSCHLIESSLICH mit diesem JSON, ohne Markdown, ohne Erklärtext:
{"candidates":[{"candidate_id":"<wie Eingabe>","why_fits":["..."],"trade_offs":["..."],"known_points":["..."],"estimated_price_min":12000,"estimated_price_max":16000,"price_confidence":"MEDIUM"}]}"""


def _kandidat_block(k: Any, req: Any) -> str:
    z = f"{k.baujahr_von or '?'}–{k.baujahr_bis or 'heute'}"
    karo = "/".join(k.karosserie_klassen or []) or "unbekannt"
    getr = "/".join(k.getriebe_klassen or []) or "unbekannt"
    zeilen = [
        f"candidate_id={_kandidat_id(k)}",
        f"  {k.marke} {k.modell} {k.generation or ''} — {k.motor_bezeichnung}",
        f"  Baujahre {z} | {k.kraftstoff} | Getriebe {getr} | {k.leistung_ps or '?'} PS | "
        f"Antrieb {k.antrieb or '?'} | Karosserie {karo}",
    ]
    if getattr(k, "verbrauch_l_100km", None) is not None:
        zeilen.append(f"  Verbrauch ~{k.verbrauch_l_100km:.1f} l/100km")
    if getattr(k, "drehmoment_nm", None):
        zeilen.append(f"  Drehmoment {k.drehmoment_nm} Nm")
    if getattr(k, "beschleunigung_0_100_s", None):
        zeilen.append(f"  0–100 km/h in {k.beschleunigung_0_100_s:.1f}s")
    gr = [g for g in (getattr(k, "match_gruende", []) or [])]
    if gr:
        zeilen.append(f"  Deterministische Passungsgründe: {'; '.join(gr)}")
    # Schwachpunkt-Kontext: Prüf-Label bleibt hier drin, damit Gemini die
    # Unsicherheit kennt — es wird angewiesen, das Wort NICHT auszugeben.
    tos = [t for t in (getattr(k, "trade_offs", []) or [])]
    if tos:
        zeilen.append(f"  Bekannter Schwachpunkt-Kontext: {'; '.join(tos)}")
    bs = getattr(k, "budget_status", None)
    if bs and bs != "UNKNOWN":
        zeilen.append(f"  Budget-Einschätzung: {bs}")
    if getattr(k, "source_type", "") == "web_discovered":
        zeilen.append("  Herkunft: Web-Recherche (Angaben belegt, nicht VIRA-geprüft)")
    return "\n".join(zeilen)


def _baue_user_message(kandidaten: list[Any], req: Any) -> str:
    zeilen = ["Anfrage des Nutzers:"]
    if getattr(req, "budget_min", None) or getattr(req, "budget_max", None):
        lo, hi = getattr(req, "budget_min", None), getattr(req, "budget_max", None)
        zeilen.append(f"  Budget: {lo or 'ab 0'}{'–' if lo and hi else ''}{hi or ''} EUR")
    if getattr(req, "nutzung", None):
        zeilen.append(f"  Nutzung: {req.nutzung}")
    if getattr(req, "km_pro_jahr", None):
        zeilen.append(f"  Jahresfahrleistung: ~{req.km_pro_jahr} km")
    prios = [p for p in ("sportlich", "sparsam", "fahranfaenger", "praktisch", "komfortabel", "familie")
             if getattr(req, p, False)]
    if prios:
        zeilen.append(f"  Prioritäten: {', '.join(prios)}")
    for feld, label in (("karosserie", "Karosserie"), ("kraftstoff", "Kraftstoff"),
                        ("getriebe", "Getriebe"), ("antrieb", "Antrieb")):
        v = getattr(req, feld, None)
        if v:
            zeilen.append(f"  Gewünscht {label}: {', '.join(v)}")
    if getattr(req, "leistung_min_ps", None) or getattr(req, "leistung_max_ps", None):
        zeilen.append(f"  Leistung: {getattr(req, 'leistung_min_ps', None) or '?'}–{getattr(req, 'leistung_max_ps', None) or '?'} PS")
    zeilen.append("")
    zeilen.append(f"Finale Kandidaten ({len(kandidaten)}) — NUR diese candidate_id sind gültig:")
    for k in kandidaten:
        zeilen.append(_kandidat_block(k, req))
        zeilen.append("")
    return "\n".join(zeilen)


def _clean_liste(roh: Any, cap: int) -> list[str]:
    if not isinstance(roh, list):
        return []
    out: list[str] = []
    for x in roh:
        if not isinstance(x, str):
            continue
        s = strip_pruef_label(x.strip())
        if s and s not in out:
            out.append(s)
        if len(out) >= cap:
            break
    return out


def _parse_preis(lo: Any, hi: Any, conf: Any) -> tuple[int | None, int | None, str]:
    try:
        lo_i = int(round(float(lo))) if lo is not None else None
        hi_i = int(round(float(hi))) if hi is not None else None
    except (TypeError, ValueError):
        return None, None, "UNKNOWN"
    if lo_i is None or hi_i is None:
        return None, None, "UNKNOWN"
    if not (_PREIS_MIN_UNTERGRENZE <= lo_i < hi_i <= _PREIS_MAX_OBERGRENZE):
        return None, None, "UNKNOWN"
    c = conf if conf in PRICE_CONF_WERTE else "UNKNOWN"
    return lo_i, hi_i, c


def _validiere(roh: Any, erlaubte_ids: set[str]) -> dict[str, Enrichment]:
    ergebnis: dict[str, Enrichment] = {}
    if not isinstance(roh, dict):
        return ergebnis
    eintraege = roh.get("candidates")
    if not isinstance(eintraege, list):
        return ergebnis
    for e in eintraege:
        if not isinstance(e, dict):
            continue
        cid = e.get("candidate_id")
        if not isinstance(cid, str) or cid not in erlaubte_ids or cid in ergebnis:
            continue
        why = _clean_liste(e.get("why_fits"), _MAX_WHY)
        if len(why) < _MIN_WHY:
            # zu dünn -> diesen Kandidaten dem deterministischen Fallback überlassen
            continue
        lo, hi, conf = _parse_preis(e.get("estimated_price_min"),
                                    e.get("estimated_price_max"),
                                    e.get("price_confidence"))
        ergebnis[cid] = Enrichment(
            why_fits=why,
            trade_offs=_clean_liste(e.get("trade_offs"), _MAX_TRADE),
            known_points=_clean_liste(e.get("known_points"), _MAX_KNOWN),
            estimated_price_min=lo, estimated_price_max=hi, price_confidence=conf,
        )
    return ergebnis


async def enrich_kandidaten(kandidaten: list[Any], req: Any) -> tuple[dict[str, Enrichment], bool]:
    """EIN Gemini-Call für alle finalen Kandidaten. Rückgabe
    `(zuordnung, ausgefallen)`. Fehlende IDs -> Aufrufer nutzt Fallback."""
    if not kandidaten:
        return {}, False
    erlaubte = {_kandidat_id(k) for k in kandidaten}
    user_msg = _baue_user_message(kandidaten, req)
    try:
        roh = await call_gemini_json(_SYSTEM_PROMPT, user_msg)
    except GeminiFehlgeschlagen as exc:
        log.warning("AutoFinder-Enrich: Gemini-Aufruf fehlgeschlagen (%s) — deterministischer Fallback", exc)
        return {}, True
    except Exception:
        log.exception("AutoFinder-Enrich: unerwarteter Fehler — deterministischer Fallback")
        return {}, True
    return _validiere(roh, erlaubte), False


def deterministischer_fallback(k: Any) -> Enrichment:
    """Ohne Gemini: why_fits aus den deterministischen Passungsgründen,
    trade_offs NUR aus verifizierten DB-Fakten (keine unverified Schwächen,
    kein Prüf-Label), kein Preis (§Enrichment-Failure)."""
    why = [strip_pruef_label(g) for g in (getattr(k, "match_gruende", []) or [])][:_MAX_WHY]
    tos_roh = [t for t in (getattr(k, "trade_offs", []) or [])]
    trade_offs = [strip_pruef_label(t) for t in tos_roh
                  if "(geprüft)" in t or "KBA-Rückruf" in t][:_MAX_TRADE]
    return Enrichment(
        why_fits=why,
        trade_offs=trade_offs,
        known_points=[],
        estimated_price_min=None, estimated_price_max=None, price_confidence="UNKNOWN",
    )
