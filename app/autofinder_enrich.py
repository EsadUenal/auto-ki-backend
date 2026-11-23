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
from app.autofinder_variante import baujahr_kontext
from app.gemini_retry import GeminiFehlgeschlagen
from app.schreibstil import STILREGEL_GEDANKENSTRICHE

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
- Jeder Kandidat ist EINE konkrete Variante. Die angegebene Karosserie, das angegebene Getriebe und der angegebene Baujahrbereich sind verbindlich: schreibe nichts, was dem widerspricht, und nenne keine andere Karosserie oder Getriebeart derselben Baureihe.
- Du erfindest KEINE Zuverlässigkeits-Behauptungen und KEINE modelltypischen Defekte. Schreibe NICHT über Schwachstellen, Motorschäden, Rückrufe oder typische Reparaturfälle dieses Modells — diese Angaben pflegt ENFAL selbst und gibt sie getrennt aus. Ein Defekt, den du nicht dieser Generation UND dieser Motorisierung zuordnen kannst, gehört nirgendwohin.
- Kein Wort wie "(ungeprüft)" oder "(geprüft)" in deinen Texten.
- Technische Eigenschaften, Ausstattung und Zahlen nennst du NUR, wenn sie in den Angaben des Kandidaten stehen. "Getriebe automatik" ist nur eine Klasse: sie sagt NICHTS über Bauart (Wandler, Doppelkupplung), Gangzahl oder Serienausstattung. Schreibe nie "serienmäßig", "Serie" oder "Standard" zu Ausstattung, Getriebe oder Assistenzsystemen.
- KEINE Absolutaussagen zu Wetter, Fahrdynamik oder Fahrsicherheit (nicht "in allen Wettersituationen", nicht "immer sicher", keine Aussage zum Eigenlenkverhalten): dafür liegen keine Daten vor.
- Verbrauch: nenne höchstens die angegebene Zahl und ihre angegebene Basis (WLTP-Wert laut Datensatz oder geschätzter Realwert). Keine andere Verbrauchszahl, und nie "gemessen", "belegt" oder "tatsächlich".
- Baujahre: Der Motorzeitraum ist NICHT hinterlegt. Schreibe nichts über Baujahre, Modellpflege oder Verfügbarkeit dieser Motorisierung in bestimmten Jahren, und behandle ein Wunschfenster nicht als Eigenschaft des Fahrzeugs.
- FAHRANFÄNGER: Wenn der Nutzer "fahranfaenger" angegeben hat, formuliere differenziert statt absolut. Erlaubt sind konkrete, überprüfbare Aussagen (übersichtliche Abmessungen, gute Bedienbarkeit, verbreitete Assistenzsysteme, Werkstattdichte). VERBOTEN sind pauschale Eignungsurteile wie "ideal für Fahranfänger", "ohne Überforderung" oder "fahrsicher für Einsteiger", sobald das Fahrzeug spürbar Leistung hat (ab etwa 150 PS). Ordne dann Leistung, Versicherungseinstufung und Fahrverhalten ausdrücklich als Anforderung ein, nicht als Vorteil. Widersprich dir nicht: nenne nicht im selben Atemzug "ideal für Einsteiger" und "verlangt Disziplin".
- Preis: Du gibst NUR eine grobe, konservative Gebrauchtwagen-Preisorientierung als BREITE Spanne (min/max in EUR) für den deutschen Markt — für GENAU DIESE Variante (Karosserie, Motorisierung, Getriebe, angegebener Baujahrbereich), nicht für die Baureihe allgemein. NIEMALS eine Einzelzahl, NIEMALS "Marktpreis"/"Marktwert"/"aktueller Preis". Wenn du unsicher bist: breitere Spanne und price_confidence LOW.

Für jeden Kandidaten:
- why_fits: 3 bis 5 konkrete, an die Nutzeranfrage (Budget, Nutzung, Jahreskilometer, Prioritäten, gewünschte Karosserie/Kraftstoff/Getriebe/Leistung) gebundene Gründe. Konkret, keine Floskeln.
- trade_offs: 2 bis 4 echte, für diesen Nutzer relevante Nachteile oder Einschränkungen (z.B. Verbrauch, Unterhalt, Kofferraum, Wertverlust, Versicherungseinstufung, Eignung fürs Nutzungsmuster) — KEINE behaupteten Defekte.
- estimated_price_min, estimated_price_max: ganze EUR-Zahlen, min < max, realistische breite Spanne.
- price_confidence: HIGH | MEDIUM | LOW | UNKNOWN.

[[STILREGEL]]

Antworte AUSSCHLIESSLICH mit diesem JSON, ohne Markdown, ohne Erklärtext:
{"candidates":[{"candidate_id":"<wie Eingabe>","why_fits":["..."],"trade_offs":["..."],"estimated_price_min":12000,"estimated_price_max":16000,"price_confidence":"MEDIUM"}]}"""


def _kandidat_block(k: Any, req: Any) -> str:
    """Beschreibt die KONKRETE empfohlene Variante.

    Vorher standen hier die Sammelangaben der Baureihe ("Karosserie
    kombi/kompakt", "Getriebe automatik/manuell", Bauzeit der ganzen
    Generation). Das Modell hat daraus zwangsläufig widersprüchliche Texte
    gebaut — etwa "als elegante Limousine verfügbar" unter einer Karte, die
    Cabrio auswies. Aufgelöste Werte zuerst, Sammelangaben gar nicht.
    """
    z = baujahr_kontext(k)
    karo = getattr(k, "karosserie_konkret", None) \
        or "/".join(k.karosserie_klassen or []) or "unbekannt"
    getr = getattr(k, "getriebe_konkret", None) \
        or "/".join(k.getriebe_klassen or []) or "unbekannt"
    if len(k.getriebe_klassen or []) > 1 and getattr(k, "getriebe_konkret", None):
        getr += f" (Datensatz führt mehrere: {'/'.join(k.getriebe_klassen)})"
    getr += " (nur die Klasse, Bauart, Gangzahl und Serienausstattung sind nicht angegeben)"
    zeilen = [
        f"candidate_id={_kandidat_id(k)}",
        f"  {k.marke} {k.modell} {getattr(k, 'generation_label', None) or k.generation or ''} "
        f"— {k.motor_bezeichnung}",
        f"  {z} | {k.kraftstoff} | Getriebe {getr} | {k.leistung_ps or '?'} PS | "
        f"Antrieb {k.antrieb or '?'} | Karosserie {karo}",
    ]
    if getattr(k, "verbrauch_l_100km", None) is not None:
        basis = getattr(k, "verbrauch_basis", None)
        basis_text = ("hinterlegter WLTP-Wert laut ENFAL-Datensatz" if basis == "wltp"
                      else "hinterlegter geschätzter Realwert (kein WLTP)" if basis == "real"
                      else "Basis unbekannt")
        zeilen.append(f"  Verbrauch ~{k.verbrauch_l_100km:.1f} l/100km ({basis_text}; nicht extern geprüft)")
    if getattr(k, "drehmoment_nm", None):
        zeilen.append(f"  Drehmoment {k.drehmoment_nm} Nm")
    if getattr(k, "beschleunigung_0_100_s", None):
        zeilen.append(f"  0–100 km/h in {k.beschleunigung_0_100_s:.1f}s")
    gr = [g for g in (getattr(k, "match_gruende", []) or [])]
    if gr:
        zeilen.append(f"  Deterministische Passungsgründe: {'; '.join(gr)}")
    # Die geprüften Schwachstellen/Rückrufe der Datenbank werden BEWUSST NICHT
    # mehr übergeben: sie erscheinen deterministisch als "bekannte Punkte" in
    # der Antwort (siehe app/routers/autofinder._bekannte_punkte). Gäbe man
    # sie hier mit, lädt das zum Umformulieren und Ausschmücken ein — genau
    # der Pfad, über den ein nicht zuordenbarer Defekt in den Text kam.
    bs = getattr(k, "budget_status", None)
    if bs and bs != "UNKNOWN":
        zeilen.append(f"  Budget-Einschätzung: {bs}")
    if getattr(k, "source_type", "") == "web_discovered":
        zeilen.append("  Herkunft: Web-Recherche (Angaben belegt, nicht ENFAL-geprüft)")
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
            # `known_points` wird NICHT mehr aus der Modellantwort übernommen —
            # auch dann nicht, wenn das Modell das Feld ungefragt mitschickt.
            # Bekannte Punkte kommen ausschließlich aus geprüften DB-Fakten.
            known_points=[],
            estimated_price_min=lo, estimated_price_max=hi, price_confidence=conf,
        )
    return ergebnis


# ══════════════════════════════════════════════════════════════════════════
# ABSICHERUNG: technische Aussagen nur aus gelieferten Angaben
# ══════════════════════════════════════════════════════════════════════════
# Der Prompt verbietet Zusatzdaten; eine Prompt-Regel ist keine Garantie. Dieser
# gezielte Filter (wie `_bereinige_einsteigeraussagen` im Router) entfernt
# einzelne SÄTZE, nicht ganze Antworten, und nur für drei Klassen unbelegter
# technischer Aussagen. Preisspannen (`estimated_price_*`) und alle übrigen
# Texte bleiben unberührt.
#
#   1. Serienausstattung: gelieferte Daten kennen keine Serie/Aufpreis-Unterscheidung.
#   2. Getriebebauart/Gangzahl: geliefert wird nur die Klasse automatik/manuell.
#      Ein Begriff darf nur stehen, wenn ihn die Motorbezeichnung selbst trägt.
#   3. Wetter-/Fahrdynamik-Absolutaussagen: dafür gibt es keine Daten.
#   4. Verbrauchszahlen: nur die gelieferte Zahl, nur mit ihrer tatsächlichen
#      Basis, nie als "belegt/gemessen/geprüft".

_SERIE_RE = re.compile(
    r"serienm[äa]ß?ig|serienmaessig|in serie\b|serienausstattung|standardm[äa]ß?ig|serienmäßige",
    re.IGNORECASE)
_GETRIEBE_BAUART_RE = re.compile(
    r"wandler|doppelkupplung|\bdsg\b|\bdct\b|\bcvt\b|steptronic|tiptronic|s[ -]?tronic"
    r"|schaltwippe|\b\d{1,2}[- ]?(?:stufen|stufig|gang|g[äa]nge)\b",
    re.IGNORECASE)
_WETTER_FAHRDYNAMIK_RE = re.compile(
    r"wettersituation|wetterlage|allen? wetter|jedem wetter|jeder witterung|allen witterungs"
    r"|bei jedem wetter|eigenlenkverhalten|stets beherrschbar|immer beherrschbar"
    r"|jederzeit (?:sicher|beherrschbar)|immer sicher|stets sicher",
    re.IGNORECASE)
_VERBRAUCH_ZAHL_RE = re.compile(
    r"(\d{1,2}(?:[.,]\d{1,2})?)\s*(?:l|liter)\s*(?:/|pro|je)\s*100\s*km", re.IGNORECASE)
_VERBRAUCH_BEHAUPTUNG_RE = re.compile(
    r"gemessen|verifiziert|nachweislich|offiziell|laut adac|ecotest|spritmonitor|tats[äa]chlich",
    re.IGNORECASE)
_WLTP_VERNEINT_RE = re.compile(r"(?:kein|nicht|ohne)e?[nrms]?\s+wltp", re.IGNORECASE)
_SATZ_TRENNER = re.compile(r"(?<=[.!?])\s+")


def _verbrauch_zahl(wert: str) -> float | None:
    try:
        return float(wert.replace(",", "."))
    except ValueError:
        return None


def _satz_unbelegt(satz: str, k: Any) -> bool:
    """True, wenn der Satz eine technische Aussage macht, die die gelieferten
    Angaben dieses Kandidaten nicht tragen."""
    if _SERIE_RE.search(satz) or _WETTER_FAHRDYNAMIK_RE.search(satz):
        return True
    motor = (getattr(k, "motor_bezeichnung", "") or "").lower()
    for m in _GETRIEBE_BAUART_RE.finditer(satz):
        if m.group(0).lower() not in motor:
            return True
    zahlen = _VERBRAUCH_ZAHL_RE.findall(satz)
    if zahlen:
        geliefert = getattr(k, "verbrauch_l_100km", None)
        basis = getattr(k, "verbrauch_basis", None)
        for z in zahlen:
            wert = _verbrauch_zahl(z)
            if geliefert is None or wert is None or abs(wert - geliefert) > 0.05:
                return True   # Verbrauchszahl, die nicht geliefert wurde
        if _VERBRAUCH_BEHAUPTUNG_RE.search(satz):
            return True       # "belegt/gemessen/..." trägt keine unserer Angaben
        # "kein WLTP" ist die korrekte Bezeichnung eines Schätzwerts, kein Verstoß.
        ohne_verneinung = _WLTP_VERNEINT_RE.sub(" ", satz)
        if re.search(r"wltp", ohne_verneinung, re.IGNORECASE) and basis != "wltp":
            return True       # falsch bezeichnete Basis
    return False


def bereinige_unbelegte_aussagen(texte: list[str], k: Any) -> list[str]:
    """Entfernt unbelegte technische SÄTZE; übrige Sätze eines Eintrags bleiben."""
    ergebnis: list[str] = []
    for text in texte:
        behalten = [s for s in _SATZ_TRENNER.split(text) if s and not _satz_unbelegt(s, k)]
        if behalten:
            ergebnis.append(" ".join(behalten))
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
    ergebnis = _validiere(roh, erlaubte)
    nach_id = {_kandidat_id(k): k for k in kandidaten}
    for cid, enr in ergebnis.items():
        k = nach_id.get(cid)
        if k is None:
            continue
        # Nur Texte werden bereinigt; die KI-Preisspanne und der Status bleiben.
        vorher = len(enr.why_fits) + len(enr.trade_offs)
        enr.why_fits = bereinige_unbelegte_aussagen(enr.why_fits, k)
        enr.trade_offs = bereinige_unbelegte_aussagen(enr.trade_offs, k)
        if len(enr.why_fits) + len(enr.trade_offs) != vorher:
            log.info("AutoFinder-Enrich: unbelegte technische Aussage(n) bei %s entfernt", cid)
    return ergebnis, False


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

# Gemeinsame Schreibstil-Regel (app/schreibstil.py) einsetzen.
# .replace statt f-String: der Prompt enthaelt ein JSON-Beispiel.
_SYSTEM_PROMPT = _SYSTEM_PROMPT.replace("[[STILREGEL]]", STILREGEL_GEDANKENSTRICHE)
