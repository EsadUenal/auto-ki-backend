from __future__ import annotations

"""
Technischer Web-Fallback — "DB FIRST, aber niemals DB ONLY".

Der DATA-TRUST-AUDIT hat belegt: Web ist im Kaufcheck heute KEIN strukturierter
Fallback. Tavily-Treffer haben genau zwei Ziele — extrahierte Preise für die
Marktanalyse und einen Rohtext-Block für den Gemini-Prompt. Für Fahrzeugidentität,
Motor, Schwachstellen, Rückrufe und Wartung gibt es keinerlei Web-Pfad: fehlt das
DB-Profil, fehlt die technische Analyse komplett.

Dieses Modul schließt genau diese Lücke — und nur diese.

ABGRENZUNG ZUM MARKTPROVIDER (bewusst zwei getrennte Schichten)

  MarketDataProvider (app/market_data_provider.py)
      Vergleichsangebote und Preise. Unverändert.
  TechnicalVehicleResearchProvider (hier)
      Fahrzeugidentität und technische Fakten. Kennt keinen Preis, liefert keinen
      und darf keinen produzieren.

DIE HARTE GRENZE: KEINE ERFUNDENE IDENTITÄT

"Immer analysieren" heißt NICHT "bei jedem unbekannten String irgendein Auto
raten". Der Identity-Trust-Fix (Commit 26b8707) hat gezeigt, wohin das führt:
"BMW iX7" wurde zu `bmw-x7-g07` und erzeugte acht fahrzeugspezifische
Schwachstellen-Aktionen für ein Fahrzeug, das es nicht gibt.

Erster Job des Fallbacks ist deshalb die FRAGE, nicht die Antwort: Lässt sich die
Eingabe überhaupt als reales Serienfahrzeug belegen? Die Prüfung ist
deterministisch und token-exakt (`_identitaet_belegt`): der Modellname des Nutzers
muss als GANZES Token in Titel oder Text von mindestens zwei UNABHÄNGIGEN,
hinreichend vertrauenswürdigen Domains vorkommen. Eine Suche nach "BMW iX7"
liefert X7-Seiten — deren Titel enthält "x7", aber nicht "ix7". Die Identität gilt
damit als nicht belegt, und es entsteht kein Fahrzeugprofil. Der Kaufcheck läuft
trotzdem weiter: mit den Nutzerangaben, den Basis-Prüfplänen und einem klaren
Hinweis auf die widersprüchliche Bezeichnung.

QUELLENGEBUNDENHEIT

Jeder strukturierte Web-Fakt trägt mindestens eine konkrete URL, eine Kategorie
und eine Confidence. Ohne belastbare Quelle entsteht kein Fakt — das ist der
Unterschied zwischen Recherche und LLM-Erinnerung. Die Quellenhierarchie kommt aus
dem BEREITS vorhandenen Tier-System in `app/web_search.py::score_domain`
(amtlich > Hersteller > Fachmedien > Technik > … > Community); sie wird hier nicht
neu erfunden, sondern nur angewandt. Ein einzelnes Forum kann Kontext liefern,
aber nie dieselbe Stufe erreichen wie KBA oder Hersteller.

EPHEMERAL — KEINE DB-MUTATION

Nichts hiervon wird gespeichert. Keine neue Baureihe, kein überschriebener Fakt,
kein `verification`-Upgrade, kein Schwachstellen-Import. Der Web-Kontext gilt für
DIESEN Check. Eine spätere persistente DB-Verifikation ist ein eigener Workflow
mit eigener Freigabe.

NICHT ENTHALTEN (bewusst)

  - Marktpreise: bleiben vollständig in der bestehenden Marktanalyse.
  - Wartungsfälligkeit ("Service ist fällig"): gehört zu P2-5. Hier entsteht
    ausschließlich das belegte Intervall als Fakt, nie eine Fälligkeitsaussage.
  - Google Search Grounding: die Provider-Abstraktion ist genau dafür da, aber
    angebunden ist in diesem Schritt nur der bestehende Tavily-Pfad.
"""

import asyncio
import logging
import re
from typing import Protocol

from app.models import EvidenceQuelle, TechnischeRecherche, WebFakt, WebVehicleIdentity
from app.web_search import (
    KATEGORIE_RUECKRUFE, KATEGORIE_SCHWACHSTELLEN, KATEGORIE_TECHNISCHE_DATEN,
    KATEGORIE_WARTUNG, US_QUELLEN_AUSSCHLUSS,
    _domain_von, _qualitaets_label, curate_results, score_domain,
    tavily_search_with_fallback,
)

log = logging.getLogger(__name__)

# ── Auslöser des Fallbacks ───────────────────────────────────────────────────
# Bewusst eine kleine, geschlossene Menge: ein guter DB-Treffer wird NICHT
# zusätzlich recherchiert (keine unnötige Latenz, kein unnötiges Tavily-Budget).
TRIGGER_DB_MISS = "db_miss"                     # find_baureihe fand nichts
TRIGGER_IDENTITAET_UNSICHER = "identitaet_unsicher"   # Identity-Trust-Gate hat gegatet
TRIGGER_MOTOR_FEHLT = "motor_fehlt"             # Baureihe sicher, Motor trotz Angabe unerkannt
TRIGGER_KONFLIKT = "konflikt"                   # harter Widerspruch Nutzerangabe <-> DB

# Domain-Score-Schwellen (Tier-System aus app/web_search.py::score_domain).
# 30 liegt oberhalb von Nachschlagewerk (18), Community (12) und Nachrichten (22)
# und unterhalb von Marktplatz (32) — es lässt also Hersteller, amtliche Stellen,
# Fachmedien und Technikquellen zu und hält reine Foren- und Wiki-Treffer draußen.
MIN_SCORE_IDENTITAET = 30
# Für harte technische Fakten (Schwachstelle/Wartung) dieselbe Schwelle; Rückrufe
# verlangen zusätzlich eine amtliche/Hersteller-Quelle (siehe `_MIN_SCORE_RUECKRUF`).
MIN_SCORE_FAKT = 30
_MIN_SCORE_RUECKRUF = 45      # nur amtlich (50) und Hersteller (48)

# Wie viele unabhängige Domains die Identität stützen müssen. Zwei ist die kleinste
# Zahl, die eine einzelne SEO-/Fehlerseite nicht allein durchkommen lässt — dieselbe
# Logik wie die Domain-Vielfalt-Anforderung der Marktanalyse (§14 Sprint 3).
MIN_DOMAINS_IDENTITAET = 2

MAX_FAKTEN_JE_KATEGORIE = 5


# ── Normalisierung (bewusst identisch zu app/kaufaktionen.py::_norm) ─────────
_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                          "Ä": "ae", "Ö": "oe", "Ü": "ue"})


def _norm(text: str | None) -> str:
    t = (text or "").strip().lower().translate(_UMLAUTE)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _tokens(text: str | None) -> set[str]:
    return {t for t in _norm(text).split() if t}


# ── Provider-Schnittstelle ───────────────────────────────────────────────────

class TechnicalVehicleResearchProvider(Protocol):
    """Austauschbare Quelle für technische Fahrzeugrecherche.

    Bewusst NICHT dieselbe Schnittstelle wie `MarketDataProvider`: dort geht es um
    Vergleichsangebote und Preise, hier um Identität und Technik. Ein gemeinsames
    Interface würde beide Verantwortlichkeiten vermischen und die Preis-Trennung
    aufweichen, die P0-1 mühsam hergestellt hat.

    Ein Provider MUSS Fehler selbst abfangen und über `provider_fehler=True`
    melden, statt eine Exception nach oben zu geben — der Kaufcheck darf an einer
    ausgefallenen Recherche niemals scheitern.
    """

    async def recherchiere(self, *, marke: str | None, modell: str | None,
                           baujahr: int | None, motor: str | None,
                           ausgeloest_durch: str) -> TechnischeRecherche:
        ...


# ── Trigger-Entscheidung ─────────────────────────────────────────────────────

def _konflikt_grund(req, motor_match: dict | None) -> str | None:
    """Harter, deterministisch erkennbarer Widerspruch zwischen Nutzerangabe und
    erkannter DB-Motorisierung.

    Bewusst nur die beiden Fälle, die `app/key_findings.py::_widerspruch_findings`
    bereits als Widerspruchs-Finding ausgibt — dieselbe Schwelle, keine zweite,
    abweichende Konfliktlogik. Ist ein solcher Konflikt sichtbar, ist unklar, WER
    recht hat; eine Recherche kann das klären helfen.
    """
    if not motor_match:
        return None
    from app.key_findings import _kraftstoff_norm, _ps_aus_text
    ins_kraft = (_kraftstoff_norm(getattr(req, "kraftstoff", None))
                 or _kraftstoff_norm(getattr(req, "motor", None)))
    mot_kraft = _kraftstoff_norm(motor_match.get("kraftstoff"))
    if ins_kraft and mot_kraft and ins_kraft != mot_kraft:
        return "kraftstoff"
    ins_ps = _ps_aus_text(getattr(req, "motor", None), getattr(req, "beschreibung", None),
                          getattr(req, "freitext", None))
    mot_ps = motor_match.get("leistung_ps")
    if ins_ps and mot_ps and abs(ins_ps - mot_ps) >= 12 and abs(ins_ps - mot_ps) / mot_ps >= 0.08:
        return "leistung"
    return None


def fallback_trigger(req, baureihe_roh: dict | None, identitaet: dict,
                     baureihe_gegatet: dict | None, motor_match: dict | None) -> str | None:
    """Ob und warum der technische Web-Fallback laufen soll — oder None.

    Reihenfolge = Dringlichkeit. Ein sicherer, vollständiger DB-Treffer liefert
    None und löst damit KEINE zusätzliche Recherche aus (§16: keine Latenz ohne
    Trigger).
    """
    if not (getattr(req, "marke", None) and getattr(req, "modell", None)):
        # Ohne Marke UND Modell gibt es nichts, wonach sich sinnvoll suchen ließe.
        return None
    if baureihe_roh is None:
        return TRIGGER_DB_MISS
    if baureihe_gegatet is None:
        # Der Rohtreffer existiert, das Identity-Trust-Gate hat ihn aber verworfen.
        return TRIGGER_IDENTITAET_UNSICHER
    if motor_match is None and (getattr(req, "motor", None) or "").strip():
        # Baureihe sicher, Nutzer hat konkret einen Motor genannt, die DB kennt ihn
        # nicht — genau die Lücke, die heute stumm bleibt.
        return TRIGGER_MOTOR_FEHLT
    if _konflikt_grund(req, motor_match):
        return TRIGGER_KONFLIKT
    return None


# ── Identitätsprüfung ────────────────────────────────────────────────────────

def _identitaet_belegt(modell: str | None, marke: str | None,
                       treffer: list[dict]) -> tuple[bool, list[dict], int]:
    """Ob die Eingabe als reales Fahrzeug belegt ist.

    Regel (deterministisch, ohne LLM): Marke UND Modell müssen als GANZE Tokens in
    Titel oder Text eines Treffers vorkommen, und das auf mindestens
    `MIN_DOMAINS_IDENTITAET` unterschiedlichen Domains oberhalb der Score-Schwelle.

    Warum token-exakt und nicht per Teilstring: exakt daran ist der Matcher in
    `find_baureihe` gescheitert. "ix7" enthält "x7" als Teilstring, ist aber ein
    anderes Fahrzeug. Sucht man nach "BMW iX7", liefern die Treffer Titel wie
    "BMW X7" — Token "x7", nicht "ix7". Die Identität bleibt damit korrekt unbelegt.

    Rückgabe: (belegt, stuetzende_treffer, anzahl_unabhaengiger_domains)
    """
    modell_tokens = _tokens(modell)
    marke_tokens = _tokens(marke)
    if not modell_tokens:
        return False, [], 0

    stuetzend: list[dict] = []
    domains: set[str] = set()
    for r in treffer:
        url = r.get("url") or ""
        if score_domain(url, KATEGORIE_TECHNISCHE_DATEN) < MIN_SCORE_IDENTITAET:
            continue
        text_tokens = _tokens(f"{r.get('title') or ''} {r.get('content') or ''}")
        if not modell_tokens <= text_tokens:
            continue
        # Die Marke muss ebenfalls vorkommen — sonst würde ein "Duster"-Treffer
        # einer beliebigen anderen Marke die Identität stützen.
        if marke_tokens and not (marke_tokens & text_tokens):
            continue
        stuetzend.append(r)
        d = _domain_von(url)
        if d:
            domains.add(d)
    return len(domains) >= MIN_DOMAINS_IDENTITAET, stuetzend, len(domains)


def _confidence_aus_domains(anzahl_domains: int, bester_score: int) -> str:
    """Confidence aus Quellenlage — nie aus dem Inhalt einer Aussage.

    Dieselbe Trennung wie in `app/evidence.py`: Confidence beschreibt die
    Beleglage, nicht die Schwere oder Plausibilität eines Fakts.
    """
    if anzahl_domains >= 3 and bester_score >= 40:
        return "hoch"
    if anzahl_domains >= 2:
        return "mittel"
    return "niedrig"


def _quellen_aus(treffer: list[dict], limit: int = 3) -> list[EvidenceQuelle]:
    """Belege als EvidenceQuelle — typ="web_technik", damit sie im Frontend NICHT
    mit den geprüften DB-Quellen (`datenbank`, `rueckruf_kba`) verwechselbar sind."""
    out: list[EvidenceQuelle] = []
    gesehen: set[str] = set()
    for r in sorted(treffer, key=lambda x: -score_domain(x.get("url") or "")):
        url = r.get("url") or ""
        d = _domain_von(url)
        if not url or d in gesehen:
            continue
        gesehen.add(d)
        out.append(EvidenceQuelle(typ="web_technik", url=url,
                                  titel=(r.get("title") or d)[:120],
                                  qualitaet=_qualitaets_label(url)))
        if len(out) >= limit:
            break
    return out


# ── Faktenextraktion (deterministisch, konservativ) ──────────────────────────
#
# Es wird NICHTS aus dem Fließtext "verstanden" — es werden nur Aussagen
# übernommen, in denen ein bekanntes Bauteil UND ein Problem-/Intervall-Signal
# gemeinsam auftreten. Alles andere bleibt liegen. Lieber kein Fakt als ein
# falscher (dieselbe Regel wie im gesamten Kaufcheck).

_PROBLEM_WORTE = ("defekt", "problem", "schwachstelle", "verschleiss", "verschleiß",
                  "ausfall", "undicht", "riss", "bruch", "schaden", "haeufig",
                  "häufig", "anfaellig", "anfällig", "typisch", "bekannt")
_RUECKRUF_WORTE = ("rueckruf", "rückruf", "recall", "rueckrufaktion", "rückrufaktion")
_INTERVALL = re.compile(
    r"(?:alle\s+)?(\d{1,3}(?:[.\s]\d{3})+|\d{4,6})\s*km"
    r"|(?:alle\s+)?(\d{1,3})\s*(monate|jahre?)", re.IGNORECASE)

_SATZ = re.compile(r"(?<=[.!?])\s+")


def _bauteil_vokabular() -> dict[str, str]:
    """Bekannte Bauteile -> kanonischer Schlüssel, aus der BEREITS vorhandenen
    Wissenstabelle in `app/kaufaktionen.py`.

    Bewusst dieselbe Quelle wie die Kaufaktionen: so trifft ein Web-Fakt zum
    Turbolader denselben Dedup-Schlüssel wie ein DB-Fakt zum Turbolader, und die
    bestehende Zusammenführung greift ohne Sonderfall.
    """
    from app.kaufaktionen import _KOMPONENTEN
    return {muster: eintrag["schluessel"]
            for eintrag in _KOMPONENTEN for muster in eintrag["muster"]}


def _saetze(text: str) -> list[str]:
    return [s.strip() for s in _SATZ.split(text or "") if 20 <= len(s.strip()) <= 300]


def _extrahiere_fakten(treffer: list[dict], kategorie: str) -> list[WebFakt]:
    """Baut strukturierte Fakten aus den Snippets — je Bauteil höchstens einen.

    Mehrere Treffer zum selben Bauteil werden zu EINEM Fakt zusammengeführt, dessen
    Confidence mit der Zahl unabhängiger Domains steigt. Ein einzelnes Forum
    erreicht damit nie "hoch".
    """
    vokabular = _bauteil_vokabular()
    min_score = _MIN_SCORE_RUECKRUF if kategorie == "rueckruf" else MIN_SCORE_FAKT
    # schluessel -> {"aussage": str, "bauteil": str, "treffer": [...]}
    kandidaten: dict[str, dict] = {}

    for r in treffer:
        url = r.get("url") or ""
        score = score_domain(url, _WEB_KATEGORIE[kategorie])
        if score < min_score:
            continue
        for satz in _saetze(f"{r.get('title') or ''}. {r.get('content') or ''}"):
            n = _norm(satz)
            if kategorie == "schwachstelle" and not any(w in n for w in _PROBLEM_WORTE):
                continue
            if kategorie == "rueckruf" and not any(w in n for w in _RUECKRUF_WORTE):
                continue
            if kategorie == "wartung" and not _INTERVALL.search(satz):
                continue
            for muster, schluessel in vokabular.items():
                if muster not in n:
                    continue
                eintrag = kandidaten.setdefault(
                    schluessel, {"aussage": satz, "bauteil": muster, "treffer": []})
                if r not in eintrag["treffer"]:
                    eintrag["treffer"].append(r)
                break

    fakten: list[WebFakt] = []
    for schluessel, eintrag in kandidaten.items():
        quellen = _quellen_aus(eintrag["treffer"])
        if not quellen:
            continue          # ohne Quelle kein Fakt
        domains = len({_domain_von(q.url or "") for q in quellen})
        bester = max(score_domain(q.url or "") for q in quellen)
        fakten.append(WebFakt(
            kategorie=kategorie,
            bauteil=schluessel,
            aussage=eintrag["aussage"],
            confidence=_confidence_aus_domains(domains, bester),
            # §9: Ohne FIN-Prüfung ist eine konkrete Betroffenheit nie belegbar —
            # dieselbe konservative Semantik wie app/recall_filter.py.
            applicability="series_only" if kategorie == "rueckruf" else None,
            quellen=quellen,
        ))
    fakten.sort(key=lambda f: ({"hoch": 0, "mittel": 1, "niedrig": 2}[f.confidence],
                               f.bauteil or ""))
    return fakten[:MAX_FAKTEN_JE_KATEGORIE]


_WEB_KATEGORIE = {
    "schwachstelle": KATEGORIE_SCHWACHSTELLEN,
    "rueckruf": KATEGORIE_RUECKRUFE,
    "wartung": KATEGORIE_WARTUNG,
}


# ── Tavily-Implementierung ───────────────────────────────────────────────────

class TavilyTechnicalResearchProvider:
    """Technische Recherche über den BESTEHENDEN Tavily-Pfad.

    Bewusst kein neuer HTTP-Client, kein zweiter Cache, keine eigene Retry-Logik:
    alles läuft über `app.web_search.tavily_search_with_fallback` und die dort
    bereits erprobte Fehler-/Cache-Behandlung. Dieses Modul steuert nur die
    Query-Planung, die Kategoriewahl und die Auswertung.

    Vier Suchen laufen parallel (Identität, Schwachstellen, Rückrufe, Wartung).
    Sie greifen bewusst die vier technischen Kategorien auf, die in
    `app/web_search.py` seit jeher definiert, vom Kaufcheck aber nie genutzt
    wurden — der Kaufcheck suchte bislang ausschließlich mit
    `KATEGORIE_MARKTPREISE`.
    """

    def __init__(self, *, count: int = 8):
        self._count = count

    async def recherchiere(self, *, marke, modell, baujahr, motor,
                           ausgeloest_durch) -> TechnischeRecherche:
        basis = " ".join(filter(None, [marke, modell, str(baujahr) if baujahr else None]))
        breit = " ".join(filter(None, [marke, modell]))
        aufgaben = {
            "identitaet":    [f"{basis} technische Daten Motor", f"{breit} technische Daten"],
            "schwachstelle": [f"{basis} typische Probleme Schwachstellen",
                              f"{breit} bekannte Schwachstellen"],
            "rueckruf":      [f"{basis} Rückruf KBA", f"{breit} Rückrufaktion"],
            "wartung":       [f"{basis} Wartungsintervall Serviceintervall",
                              f"{breit} Inspektionsintervall"],
        }
        try:
            ergebnisse = await asyncio.gather(*[
                tavily_search_with_fallback(qs, count=self._count,
                                            exclude_domains=US_QUELLEN_AUSSCHLUSS)
                for qs in aufgaben.values()
            ], return_exceptions=True)
        except Exception as exc:                      # pragma: no cover — Schutznetz
            log.warning("Technische Recherche fehlgeschlagen (%s): %s", type(exc).__name__, exc)
            return TechnischeRecherche(ausgeloest_durch=ausgeloest_durch, provider_fehler=True)

        roh: dict[str, list[dict]] = {}
        fehler = False
        for name, res in zip(aufgaben, ergebnisse):
            if isinstance(res, Exception):
                log.warning("Technische Teilrecherche '%s' fehlgeschlagen: %s", name, res)
                fehler = True
                roh[name] = []
            else:
                roh[name] = res or []

        # Identität aus ALLEN Treffern belegen — ein Rückruf- oder Wartungstreffer
        # bestätigt das Fahrzeug genauso gut wie ein Datenblatt.
        alle = [r for liste in roh.values() for r in liste]
        return _baue_recherche(marke, modell, baujahr, motor, ausgeloest_durch, roh, alle, fehler)


def _baue_recherche(marke, modell, baujahr, motor, ausgeloest_durch,
                    roh: dict[str, list[dict]], alle: list[dict],
                    provider_fehler: bool) -> TechnischeRecherche:
    """Gemeinsame Auswertung für alle Provider — hält die Regeln an EINER Stelle."""
    belegt, stuetzend, domains = _identitaet_belegt(modell, marke, alle)
    if not belegt:
        log.info("Technische Recherche: Identität '%s %s' NICHT belegt "
                 "(%d stützende Domains) — kein Fahrzeugprofil", marke, modell, domains)
        return TechnischeRecherche(
            ausgeloest_durch=ausgeloest_durch,
            identitaet=WebVehicleIdentity(belegt=False, marke=marke, modell=modell,
                                          belegende_domains=domains),
            provider_fehler=provider_fehler,
        )

    bester = max((score_domain(r.get("url") or "") for r in stuetzend), default=0)
    identitaet = WebVehicleIdentity(
        belegt=True,
        marke=marke,
        modell=modell,
        # Generation/Bauzeitraum werden NICHT geraten: solange keine belastbare
        # deterministische Ableitung existiert, bleiben sie leer statt gefüllt.
        motor=(motor or "").strip() or None,
        kraftstoff=_kraftstoff_aus_treffern(stuetzend),
        leistung_ps=_leistung_aus_treffern(stuetzend, motor),
        confidence=_confidence_aus_domains(domains, bester),
        belegende_domains=domains,
        quellen=_quellen_aus(stuetzend),
    )

    fakten: list[WebFakt] = []
    for kategorie in ("schwachstelle", "rueckruf", "wartung"):
        treffer = curate_results(roh.get(kategorie) or [],
                                 kategorie=_WEB_KATEGORIE[kategorie], max_results=8)
        fakten += _extrahiere_fakten(treffer, kategorie)

    log.info("Technische Recherche: '%s %s' belegt (%d Domains, confidence=%s), %d Fakten",
             marke, modell, domains, identitaet.confidence, len(fakten))
    return TechnischeRecherche(ausgeloest_durch=ausgeloest_durch, identitaet=identitaet,
                               fakten=fakten, provider_fehler=provider_fehler)


_KRAFTSTOFFE = (("diesel", ("diesel", "tdi", "cdi", "hdi", "dci")),
                ("elektro", ("elektro", "electric", "bev")),
                ("hybrid", ("hybrid", "phev", "plug in")),
                ("benzin", ("benzin", "tsi", "tfsi", "petrol")))
_PS = re.compile(r"(\d{2,3})\s*ps\b", re.IGNORECASE)


def _kraftstoff_aus_treffern(treffer: list[dict]) -> str | None:
    """Kraftstoff nur, wenn ALLE gefundenen Signale übereinstimmen.

    Widersprechen sich die Treffer (eine Baureihe mit Diesel UND Benziner), bleibt
    das Feld leer — eine Mehrheitsentscheidung wäre hier geraten, nicht belegt.
    """
    gefunden: set[str] = set()
    for r in treffer:
        n = _norm(f"{r.get('title') or ''} {r.get('content') or ''}")
        for norm, keys in _KRAFTSTOFFE:
            if any(k in n for k in keys):
                gefunden.add(norm)
    return gefunden.pop() if len(gefunden) == 1 else None


def _leistung_aus_treffern(treffer: list[dict], motor_hint: str | None) -> int | None:
    """PS-Zahl nur, wenn sie auch in der NUTZERANGABE steht.

    Ohne diesen Abgleich würde die erstbeste PS-Zahl einer Übersichtsseite
    übernommen — die aber meist die stärkste Motorisierung der Baureihe nennt, nicht
    die des Inserats. Die Web-Quelle bestätigt hier also die Nutzerangabe, sie
    ersetzt sie nicht.
    """
    m = _PS.search(motor_hint or "")
    if not m:
        return None
    wert = int(m.group(1))
    for r in treffer:
        if any(int(x) == wert for x in _PS.findall(f"{r.get('title') or ''} {r.get('content') or ''}")):
            return wert
    return None


# ── Fixture-Provider (Tests) ─────────────────────────────────────────────────

class FixtureTechnicalResearchProvider:
    """Deterministischer Provider für Tests — kein Netzwerk.

    Bekommt dieselben Roh-Trefferlisten, die Tavily liefern würde (`{title, url,
    content}`), und durchläuft DIESELBE Auswertung wie der Tavily-Provider
    (`_baue_recherche`). Dadurch testen die Fixtures die echte Logik und nicht
    eine vereinfachte Nachbildung.

    `fehler=True` simuliert einen Providerausfall.
    """

    def __init__(self, treffer: dict[str, list[dict]] | None = None, *, fehler: bool = False):
        self._treffer = treffer or {}
        self._fehler = fehler

    async def recherchiere(self, *, marke, modell, baujahr, motor,
                           ausgeloest_durch) -> TechnischeRecherche:
        if self._fehler:
            return TechnischeRecherche(ausgeloest_durch=ausgeloest_durch, provider_fehler=True)
        roh = {k: list(self._treffer.get(k) or [])
               for k in ("identitaet", "schwachstelle", "rueckruf", "wartung")}
        alle = [r for liste in roh.values() for r in liste]
        return _baue_recherche(marke, modell, baujahr, motor, ausgeloest_durch,
                               roh, alle, provider_fehler=False)


# ── Öffentliche Fassade ──────────────────────────────────────────────────────

async def recherchiere_technisch(req, baureihe_roh, identitaet, baureihe_gegatet,
                                 motor_match, *, provider=None) -> TechnischeRecherche | None:
    """Führt den Fallback aus, WENN ein Trigger vorliegt — sonst None.

    Fängt jede Provider-Ausnahme ab: ein Recherchefehler darf den Kaufcheck nie
    abbrechen (§17). Im Fehlerfall entsteht ein Ergebnis mit
    `provider_fehler=True` und ohne Identität — der Check läuft mit DB-Daten bzw.
    Nutzerangaben und Basis-Prüfplänen weiter.
    """
    trigger = fallback_trigger(req, baureihe_roh, identitaet, baureihe_gegatet, motor_match)
    if trigger is None:
        return None
    provider = provider or TavilyTechnicalResearchProvider()
    try:
        return await provider.recherchiere(
            marke=getattr(req, "marke", None), modell=getattr(req, "modell", None),
            baujahr=getattr(req, "baujahr", None), motor=getattr(req, "motor", None),
            ausgeloest_durch=trigger)
    except Exception as exc:
        log.warning("Technischer Web-Fallback fehlgeschlagen (%s): %s", type(exc).__name__, exc)
        return TechnischeRecherche(ausgeloest_durch=trigger, provider_fehler=True)


def technical_coverage(baureihe_gegatet, recherche: TechnischeRecherche | None) -> str:
    """Woher die technischen Fahrzeugdaten dieses Checks stammen (§18)."""
    hat_web = bool(recherche and recherche.identitaet and recherche.identitaet.belegt)
    if baureihe_gegatet is not None:
        return "db_plus_web" if (hat_web or (recherche and recherche.fakten)) else "db"
    if hat_web:
        return "web"
    return "partial"
