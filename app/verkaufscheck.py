from __future__ import annotations

"""
Verkaufs-Check: Optimale Preisstrategie für den Fahrzeugverkauf.

Ablauf:
  1. Baureihe + Motor in SQLite erkennen
  2. DB-Kontext aufbauen (Neupreis, Specs, Ausstattungslinien)
  3. Marktpreise vergleichbarer Fahrzeuge per Tavily ermitteln
  4. Gemini (JSON-Modus) liefert Preisspanne + Verkaufsstrategie-Bericht
"""

import asyncio
import logging

from app.car_lookup import find_baureihe, find_motor, build_db_context, call_gemini_json
from app.config import TAVILY_API_KEY
from app.database import get_alle_baureihen_kurz, get_alle_motorvarianten_kurz
from app.evidence import (
    build_insights, format_evidence_for_prompt, filter_evidence_ids,
    valid_evidence_ids, enrich_marktvergleich_spanne, marktvergleich_id, ergaenze_id,
)
from app.marktvergleich import analysiere_markt, baue_ziel, modell_relevant, prompt_block as markt_prompt_block
from app.marktrecherche import (
    vertiefe_marktrecherche, baue_deep_queries, baue_rare_queries, research_status,
    RechercheUnzureichend, nachricht_unzureichend,
)
from app.preisurteil import (
    bewerte_preis, verkaufs_strategie, verkaufs_prompt_block, prompt_block as preis_prompt_block,
)
from app.key_findings import build_key_findings_verkauf
from app.inserat import build_listing_analyse
from app.models import VerkaufsCheckRequest
from app.vehicle_identity import VehicleIdentity
from app.postprocess import postprocess_answer, entferne_erfundene_verkaufsdauer
from app.recall_filter import ausgeschlossene_rueckrufe, gefilterte_rueckrufe
from app.report_validator import pruefe_bericht
from app.web_search import (
    tavily_search_with_fallback, results_to_context, results_to_belege, curate_results,
    KATEGORIE_MARKTPREISE, US_QUELLEN_AUSSCHLUSS,
)

_MAX_VERKAUFSCHECK_QUELLEN = 4

log = logging.getLogger(__name__)

_SYSTEM = """\
Du bist ein erfahrener KFZ-Verkaufsberater. Du hilfst dem Nutzer, seinen Wagen optimal zu vermarkten und einen fairen Preis zu erzielen.

Du erhältst:
1. FAHRZEUG-DATEN — Angaben des Eigentümers über sein Fahrzeug und dessen Zustand
2. DB-PROFIL — geprüfte Fakten (Neupreis, Specs, Ausstattungslinien) — zuverlässig
3. WEB-ERGEBNISSE — aktuelle Marktpreise vergleichbarer Fahrzeuge aus Tavily — Orientierung

AUSGABE: Ausschließlich gültiges JSON. WICHTIG: Zuerst die Zahlfelder, dann "bericht".

{
  "schnellverkaufs_preis": <integer EUR, PFLICHT wenn ableitbar, sonst null>,
  "maximal_preis": <integer EUR, PFLICHT wenn ableitbar, sonst null>,
  "empfohlener_preis": <integer EUR, PFLICHT wenn ableitbar, sonst null>,
  "verkaufsdauer_tage_schnell": <integer Tage, z.B. 14 für 2 Wochen, PFLICHT wenn ableitbar>,
  "verkaufsdauer_tage_maximal": <integer Tage, z.B. 90 für 3 Monate, PFLICHT wenn ableitbar>,
  "marktpreis_min": <integer EUR aus Web, null wenn kein Web>,
  "marktpreis_max": <integer EUR aus Web, null wenn kein Web>,
  "preis_evidence_ids": [<IDs aus "VERFÜGBARE EVIDENCE", die die Preisempfehlung stützen; sonst []>],
  "strategie_evidence_ids": [<IDs, die Verkaufsstrategie/Marktpositionierung stützen; sonst []>],
  "argument_evidence_ids": [<IDs zu zentralen Verkaufsargumenten/Risiken; sonst []>],
  "bericht": "<vollständiger Markdown-Bericht — Struktur unten>"
}

— EVIDENCE-VERKNÜPFUNG (Provenance) —
Im Nutzerteil steht ggf. ein Block "VERFÜGBARE EVIDENCE" mit IDs (bereits geprüfte Schicht-A-Fakten). Für die *_evidence_ids-Felder:
- Referenziere NUR IDs aus diesem Block — und NUR solche, die die jeweilige Entscheidung TATSÄCHLICH stützen.
- Erfinde KEINE IDs. Referenziere keine ID nur wegen thematischer Ähnlichkeit.
- Passt keine Evidence → leere Liste []. Preisempfehlung/Strategie bleiben trotzdem gültig (dann reine KI-Ableitung).
- Die Felder dienen NUR dem Referenzieren bestehender IDs: ändere nichts an der Evidence, erfinde keine Confidence.
- Gibt es keinen Evidence-Block, sind alle *_evidence_ids [].
- Evidence-IDs (z.B. "schwachstelle-1", "rueckruf-4", "marktvergleich-7") gehören AUSSCHLIESSLICH in die *_evidence_ids-Felder. Im Feld "bericht" dürfen NIEMALS interne Evidence-IDs, technische IDs oder Hinweise auf das interne Evidence-System erscheinen — KEIN "(Evidence-ID: ...)", KEIN "[schwachstelle-1]" o.ä. Der Bericht bleibt für den Nutzer vollständig natürlich lesbar.

— FEHLENDE ODER FEHLERHAFTE EINGABEN (prüfe das ZUERST) —
- Fehlen Kernangaben (Marke, Modell, Baujahr ODER Kilometerstand): Antworte NUR mit einer kompakten Rückfrage (2–3 Sätze), was konkret noch gebraucht wird, alle Zahlfelder null. Keine volle Struktur.
- Widersprechen sich Angaben technisch UNMÖGLICH (z.B. Motorvariante existierte für dieses Baujahr nachweislich nicht): kompakte, technisch begründete Rückfrage statt voller Bericht.
- Wirkt ein Wert wie ein Zahlen-/Schreibfehler: kurz benennen ("vermutlich Tippfehler — meintest du X?") statt kommentarlos zu übernehmen.
- Ungewöhnliche, aber real mögliche Werte (z.B. sehr hohe Laufleistung, sehr junges Fahrzeug mit hohem km-Stand durch Vielfahrer) sind KEIN Fehler — als "selten, aber möglich" einordnen und in der Preisspanne berücksichtigen, nicht ablehnen.
- Nur bei ausreichenden, plausiblen Kerndaten die volle Struktur unten schreiben.
- Preisfelder (marktpreis_min/max, schnellverkaufs_preis, empfohlener_preis, maximal_preis) NUR dann komplett null lassen, wenn wirklich KEIN Preishinweis aus DB-Neupreis oder Web-Ergebnissen ableitbar ist. Enthält auch nur eines der Web-Ergebnisse eine ungefähre Preisangabe zu einem vergleichbaren Fahrzeug, leite daraus eine grobe Spanne ab (auch mit Unsicherheit) statt vorschnell null zu setzen.

BERICHT-STRUKTUR (Markdown im "bericht"-Feld, Zeilenumbrüche als \\n) — nur bei ausreichenden, plausiblen Kerndaten:

## Fahrzeug erkannt
Baureihe, Motor, Baujahr — eine Zeile.

## (a) Marktvergleich
Aktuelle Vergleichspreise aus den Web-Quellen (transparent als "laut aktueller Websuche" kennzeichnen).
Einordnung dieses Fahrzeugs in den Markt: technische Gründe, was den Preis hebt oder drückt (Ausstattung, Laufleistung, Zustand, Marktnachfrage) — keine Marketing-Formulierungen.

## (b) Empfohlene Preisspanne

| Preis-Kategorie | Betrag (€) | Erwartete Vermarktung |
|-----------------|-----------|------------------------|
| Schnellverkauf | XX.XXX | voraussichtlich schneller |
| Empfohlener Preis | XX.XXX | durchschnittliche Vermarktungsdauer |
| Maximalpreis | XX.XXX | wahrscheinlich längere Vermarktung |

Übernimm die Beträge und die Vermarktungs-Kategorien GENAU aus dem Block
"DETERMINISTISCHE PREISSTRATEGIE". Kurze Begründung der Spanne.
Falls Preisvorstellung angegeben: einordnen (realistisch / zu hoch / zu niedrig).

## (c) Preis-Optimierungstipps

**Was im Inserat betonen:**
Konkrete wertsteigernde Punkte aus Ausstattung und Zustand.

**Was vor dem Verkauf lohnt (Kosten → Mehrwert):**
Nur Maßnahmen bei denen der Mehrwert die Kosten übersteigt.

**Was NICHT lohnt:**
Zu teure Maßnahmen im Verhältnis zum Mehrwert.

## (d) Verkaufsstrategie & Zeitplan
- Wo inserieren: konkrete Empfehlung mit Begründung
- Preisstrategie: wann und um wie viel senken
- Verhandlungstipps

— PREISLOGIK (verbindlich) —
- Verankere schnellverkaufs_preis, empfohlener_preis und maximal_preis an der aus dem Web ermittelten Marktspanne (marktpreis_min–marktpreis_max).
- empfohlener_preis ODER maximal_preis dürfen NUR dann ÜBER marktpreis_max liegen, wenn es durch KONKRETE, wirklich vergleichbare Angebote oder klar quantifizierte wertsteigernde Faktoren (z.B. seltene Ausstattung mit belegbarem Aufpreis) nachvollziehbar begründet ist. Nenne die Abweichung dann ausdrücklich in EURO UND PROZENT (z.B. "+1.500 €, ~7 % über der Markt-Obergrenze") samt konkreter Begründung. Pauschalaussagen wie "gute Ausstattung rechtfertigt das" sind KEINE Begründung.
- Sind die Web-Daten schwach, widersprüchlich oder nicht direkt vergleichbar (andere Karosserie/Motorvariante, stark abweichende Laufleistung/Baujahr, sehr breite Streuung): setze KEINE künstlich weite Marktspanne, kennzeichne die Unsicherheit deutlich im Bericht und lege empfohlener_preis/maximal_preis dann NICHT deutlich über marktpreis_max.
- Erfinde KEINE präzisen Verkaufszeiten (keine Tage/Wochen/Monate). Für die Vermarktungsdauer verwende AUSSCHLIESSLICH die drei Kategorien aus dem Block "DETERMINISTISCHE PREISSTRATEGIE" ("voraussichtlich schneller" | "durchschnittliche Vermarktungsdauer" | "wahrscheinlich längere Vermarktung"). Die Zahlfelder verkaufsdauer_tage_* bleiben null.
- Die drei Preise (Schnellverkauf/Empfohlen/Maximal) sind vom Backend deterministisch aus dem Marktvergleich vorgegeben — übernimm sie unverändert und erzeuge KEINE eigenen Preise.

REGELN:
1. Neupreis und Specs nur aus DB-Profil.
2. Web-Preise transparent als Websuche-Ergebnis kennzeichnen, ohne interne Begriffe wie "ungeprüft" oder "Vertrauen" im Text zu verwenden — das sind Entwicklerbegriffe, keine Nutzersprache. Keine Klammer-Quellenverweise wie "(Quelle [1])" im Fließtext — Quellen erscheinen automatisch im Quellenbereich.
3. Konkrete Zahlen — keine "könnte"-Phrasen ohne Zahl.
4. Alle Zahlfelder MÜSSEN als Integer stehen — nicht im Bericht verstecken.
5. Sachlich und neutral bleiben — Begründungen immer technisch (Zustand, Laufleistung, Marktdaten, Ausstattung), nie werblich/emotional ("toller Wagen", "beliebtes Modell").
6. Auf Deutsch schreiben.
7. Kein Floskel-Text vor oder nach der geforderten Struktur (kein "Gerne, hier ist die Analyse", kein "Ich hoffe, das hilft"). Der Bericht beginnt direkt mit "## Fahrzeug erkannt" und endet mit dem letzten inhaltlichen Punkt der Verkaufsstrategie.\
"""


def _format_fahrzeug(req: VerkaufsCheckRequest) -> str:
    if req.freitext:
        return f"MEIN FAHRZEUG (Freitext):\n{req.freitext}"

    lines = ["MEIN FAHRZEUG:"]
    if req.marke:            lines.append(f"Marke:              {req.marke}")
    if req.modell:           lines.append(f"Modell:             {req.modell}")
    if req.baujahr:          lines.append(f"Baujahr:            {req.baujahr}")
    if req.kilometerstand:   lines.append(f"Kilometerstand:     {req.kilometerstand:,} km".replace(",", "."))
    if req.motor:            lines.append(f"Motor:              {req.motor}")
    if req.kraftstoff:       lines.append(f"Kraftstoff:         {req.kraftstoff}")
    if req.ausstattung:      lines.append(f"Ausstattung:        {', '.join(req.ausstattung)}")
    if req.beschreibung:     lines.append(f"Zustand:            {req.beschreibung}")
    if req.maengel:          lines.append(f"Bekannte Mängel:    {', '.join(req.maengel)}")
    if req.preis_vorstellung:lines.append(f"Preisvorstellung:   {req.preis_vorstellung:,} €".replace(",", "."))
    if req.unfallfrei:       lines.append(f"Unfallfrei:         {req.unfallfrei}")
    if req.vorbesitzer is not None: lines.append(f"Vorbesitzer:        {req.vorbesitzer}")
    if req.tuev_bis:         lines.append(f"TÜV bis:            {req.tuev_bis}")
    if req.scheckheftgepflegt is not None:
        lines.append(f"Scheckheftgepflegt: {'ja' if req.scheckheftgepflegt else 'nein'}")
    return "\n".join(lines)


# Kleine Überschreitungen (Rundung, Marktrauschen) sind kein Widerspruch;
# darüber hinaus wird der Aufschlag transparent gemacht.
_PREIS_TOLERANZ_PCT = 2.0


def _nennt_abweichung(bericht: str) -> bool:
    """True, wenn der Bericht eine Preisabweichung über der Marktspanne bereits
    transparent mit Prozent-Angabe UND Markt-Obergrenzen-Bezug benennt."""
    b = bericht.lower()
    hat_markt_bezug = any(
        s in b for s in ("über der markt", "über dem markt", "markt-obergrenze", "marktobergrenze")
    )
    return "%" in b and hat_markt_bezug


def _preis_konsistenz_hinweis(
    bericht: str, empfohlener_preis, maximal_preis, marktpreis_max
) -> str:
    """Hängt einen transparenten Hinweis an den Bericht, wenn empfohlener_preis oder
    maximal_preis SPÜRBAR (> Toleranz) über der aus der Websuche ermittelten Markt-
    Obergrenze liegen und der Bericht die Abweichung nicht bereits mit Euro- UND
    Prozent-Angabe begründet.

    Deterministisch (reine Zahlenlogik) — verändert die vom Modell genannten Preise
    NICHT, macht die Abweichung nur explizit (Euro + Prozent) und kennzeichnet die
    Unsicherheit. Ohne belastbare Markt-Obergrenze (kein Web) passiert nichts.
    """
    if not bericht or not isinstance(marktpreis_max, int) or marktpreis_max <= 0:
        return bericht

    treffer: list[tuple[str, int, int, float]] = []
    for label, preis in (("empfohlene Preis", empfohlener_preis), ("Maximalpreis", maximal_preis)):
        if not isinstance(preis, int) or preis <= marktpreis_max:
            continue
        diff = preis - marktpreis_max
        pct = diff / marktpreis_max * 100
        if pct >= _PREIS_TOLERANZ_PCT:
            treffer.append((label, preis, diff, pct))

    if not treffer or _nennt_abweichung(bericht):
        return bericht

    def _eur(n: int) -> str:
        return f"{n:,} €".replace(",", ".")

    saetze = [
        f"Der {label} ({_eur(preis)}) liegt {_eur(diff)} (~{pct:.0f} %) über der aus der "
        f"Websuche ermittelten Markt-Obergrenze ({_eur(marktpreis_max)})."
        for label, preis, diff, pct in treffer
    ]
    hinweis = (
        "\n\n> **Hinweis zur Preiseinordnung:** " + " ".join(saetze) +
        " Ein Preis oberhalb der Marktspanne ist nur bei wirklich vergleichbaren Angeboten "
        "realistisch erzielbar; ohne belastbare Vergleichsdaten sollte eher die Markt-"
        "Obergrenze als Zielpreis dienen."
    )
    return bericht + hinweis


async def run_verkaufscheck(req: VerkaufsCheckRequest, retry: bool = False) -> dict:
    """`retry` (§22/§33): True, wenn dies ein "Erneut versuchen" nach research_failed
    ist — erzwingt frische Tavily-Calls statt einer identischen gecachten Antwort."""
    # 1. Baureihe erkennen (DB, blockierend) UND Marktpreise per Tavily (Netzwerk) laufen
    #    PARALLEL — die Tavily-Queries hängen nur an den Fahrzeug-Rohdaten (req.*), nicht
    #    am Ergebnis der Baureihe-Erkennung, sind also unabhängig voneinander.
    baureihe_task = asyncio.to_thread(find_baureihe, req.marke, req.modell, req.baujahr)

    web_results_task: asyncio.Task[list[dict]] | None = None
    if TAVILY_API_KEY and req.marke and req.modell:
        # Marktpreise per Tavily (vergleichbare Angebote) — kaskadierende Queries:
        # spezifisch → breiter, damit auch bei seltenen Modellen Ergebnisse kommen.
        q_spezifisch = " ".join(filter(None, [
            req.marke, req.modell, req.motor,
            str(req.baujahr) if req.baujahr else None,
            f"{req.kilometerstand // 1000 * 1000} km" if req.kilometerstand else None,
            "Gebrauchtpreis verkaufen Deutschland",
        ]))
        q_mittel = " ".join(filter(None, [
            req.marke, req.modell, str(req.baujahr) if req.baujahr else None,
            "Gebrauchtpreis Deutschland",
        ]))
        q_breit = f"{req.marke} {req.modell} Gebrauchtpreis Deutschland"
        web_results_task = asyncio.ensure_future(
            tavily_search_with_fallback(
                # count=8: mehr Snippets für den Marktvergleich (gleicher Credit-Preis).
                [q_spezifisch, q_mittel, q_breit], count=8,
                exclude_domains=US_QUELLEN_AUSSCHLUSS,
            )
        )

    baureihe    = await baureihe_task
    motor_match = find_motor(baureihe, req.motor) if baureihe else None

    # 2. DB-Kontext
    db_ctx = build_db_context(baureihe, motor_match, req.baujahr)

    web_results_roh: list[dict] = await web_results_task if web_results_task else []

    # Marktvergleich 2.0 + adaptive Recherche (harte Modelltreue via baue_ziel; die
    # Recherche wird vertieft, bis genug akzeptierte, modelltreue Vergleiche vorliegen).
    ziel = baue_ziel(baureihe, motor_match, req,
                     get_alle_baureihen_kurz() if baureihe else [],
                     get_alle_motorvarianten_kurz() if baureihe else [])
    identity = VehicleIdentity.from_market_context(baureihe, motor_match, req)
    if TAVILY_API_KEY and req.marke and req.modell:
        deep_queries = baue_deep_queries(identity)
        rare_queries = baue_rare_queries(identity)
        # §Phase 0/13: siehe Kommentar in kaufcheck.py — max_results 20 statt 10,
        # gemessen deutlich mehr extrahierbare Datenpunkte ohne Mehrkosten.
        web_results_roh, marktanalyse, diag = await vertiefe_marktrecherche(
            web_results_roh, deep_queries, ziel, req.preis_vorstellung, US_QUELLEN_AUSSCHLUSS,
            count=20, zweck="verkaufscheck-markt", rare_queries=rare_queries, bypass_cache=retry)
    else:
        marktanalyse = analysiere_markt(web_results_roh, ziel, req.preis_vorstellung)
        diag = {"research_failure_grund": "technical_failure" if not TAVILY_API_KEY else "data_exhausted"}

    # ── Quality-Gate (§0/§17/§21) ────────────────────────────────────────────────
    # Reicht die Datenbasis nicht für einen belastbaren Marktwert, wird KEINE
    # Preisstrategie erzeugt und der LLM-Call gespart -> research_failed. §25 verbietet
    # ausdrücklich, eine niedrige Qualität einfach zu akzeptieren und trotzdem exakte
    # Preise/Verkaufszeiten auszugeben.
    status = research_status(marktanalyse)
    if status == "research_failed":
        grund = diag.get("research_failure_grund", "data_exhausted")
        raise RechercheUnzureichend(marktanalyse, nachricht_unzureichend(identity, grund), grund)

    # Kanonisches Preisurteil + deterministische Preisstrategie (§6/§10/§13).
    price_assessment = bewerte_preis(marktanalyse, req.preis_vorstellung, check_typ="verkauf")
    strategie = verkaufs_strategie(marktanalyse)

    # Fachfremde Modell-Seiten aus den ANGEZEIGTEN Quellen/Kontext aussortieren.
    web_relevant = [r for r in web_results_roh if modell_relevant(r, ziel)]
    web_results = curate_results(web_relevant, kategorie=KATEGORIE_MARKTPREISE, max_results=_MAX_VERKAUFSCHECK_QUELLEN)
    web_ctx = results_to_context(web_results)
    belege  = results_to_belege(web_results)

    # 4. Gemini-Analyse
    # Absichtlich KEIN try/except um Gemini-Totalausfälle (RateLimitExhausted,
    # GeminiVoruebergehendNichtErreichbar) — die propagieren bis zum Router
    # (routers/verkaufscheck.py), der einheitlich das Check-Kontingent
    # zurückerstattet und eine saubere Fehlermeldung zeigt.
    # Phase 1 Schicht B: Evidence deterministisch VOR dem LLM bauen (Marktpreis noch
    # unbekannt -> None, wird nach dem LLM ergänzt) und dem LLM kompakt zum
    # Referenzieren mitgeben (stabile IDs -> anschließend backend-validierbar).
    insights = build_insights(baureihe, motor_match, belege, req, check_typ="verkauf",
                              marktanalyse=marktanalyse)
    evidence_block = format_evidence_for_prompt(insights)
    markt_block = markt_prompt_block(marktanalyse)
    preis_block = preis_prompt_block(price_assessment)
    strategie_block = verkaufs_prompt_block(strategie)
    user_msg = "\n\n".join(filter(None, [_format_fahrzeug(req), db_ctx, web_ctx,
                                         markt_block, preis_block, strategie_block, evidence_block]))
    result = await call_gemini_json(_SYSTEM, user_msg)
    if result.get("bericht"):
        result["bericht"] = postprocess_answer(result["bericht"])
        # §26: Sicherheitsnetz NACH der Prompt-Regel — entfernt eine konkret erfundene
        # Verkaufsdauer-Zahl ("innerhalb von 3-4 Wochen"), falls das LLM sie trotz
        # Anweisung erzeugt hat. Nur in Sätzen mit Verkaufs-/Vermarktungskontext.
        result["bericht"] = entferne_erfundene_verkaufsdauer(result["bericht"])
        # §Phase 8: letztes Sicherheitsnetz gegen ausgeschlossene Rückrufe im
        # Freitext-Bericht (dieselbe Absicherung wie im Kaufcheck, siehe kaufcheck.py).
        if baureihe and baureihe.get("rueckrufe"):
            _ausgeschlossen = ausgeschlossene_rueckrufe(baureihe["rueckrufe"], motor_match, req.baujahr)
            if _ausgeschlossen:
                _erlaubt = gefilterte_rueckrufe(baureihe["rueckrufe"], motor_match, req.baujahr)
                result["bericht"], _ = pruefe_bericht(result["bericht"], _ausgeschlossen, _erlaubt)
        # Preis-Konsistenz: liegt empfohlener/maximaler Preis spürbar über der
        # Markt-Obergrenze ohne transparente Begründung, den Aufschlag deterministisch
        # in Euro UND Prozent kenntlich machen (verändert die Preise selbst nicht).
        result["bericht"] = _preis_konsistenz_hinweis(
            result["bericht"],
            result.get("empfohlener_preis"),
            result.get("maximal_preis"),
            result.get("marktpreis_max"),
        )

    hat_db, hat_web = baureihe is not None, bool(web_results)
    if hat_db and hat_web:   quelle, vertrauen = "gemischt", "mittel"
    elif hat_db:             quelle, vertrauen = "datenbank", "hoch"
    elif hat_web:            quelle, vertrauen = "web", "niedrig"
    else:                    quelle, vertrauen = "gemischt", "niedrig"

    # Marktpreis-Spanne deterministisch verankern, wenn belastbare Analyse vorliegt
    # (robuster Median/Quartilsbereich statt LLM-Spanne); sonst LLM-Spanne nachtragen.
    if marktanalyse and marktanalyse.median_eur:
        result["marktpreis_min"] = marktanalyse.spanne_min_eur
        result["marktpreis_max"] = marktanalyse.spanne_max_eur
    else:
        enrich_marktvergleich_spanne(insights, result.get("marktpreis_min"), result.get("marktpreis_max"))

    # §10/§11/§13: Preisstrategie DETERMINISTISCH aus dem validierten Marktvergleich
    # verankern — die vom LLM genannten Preise werden NICHT übernommen. Verkaufszeiten
    # nur als Kategorie; keine erfundenen Tageszahlen.
    if strategie:
        result["schnellverkaufs_preis"] = strategie["schnellverkaufs_preis"]
        result["empfohlener_preis"] = strategie["empfohlener_preis"]
        result["maximal_preis"] = strategie["maximal_preis"]
        result["verkaufsdauer_schnell"] = strategie["verkaufsdauer_schnell"]
        result["verkaufsdauer_empfohlen"] = strategie["verkaufsdauer_empfohlen"]
        result["verkaufsdauer_maximal"] = strategie["verkaufsdauer_maximal"]
    result["verkaufsdauer_tage_schnell"] = None
    result["verkaufsdauer_tage_maximal"] = None

    # Schicht B: vom LLM gelieferte Evidence-IDs gegen die ECHTEN Insight-IDs
    # validieren — Halluzinationen verwerfen (Backend bleibt Source of Truth).
    gueltige = valid_evidence_ids(insights)
    preis_evidence_ids     = filter_evidence_ids(result.get("preis_evidence_ids"), gueltige, feld="preis")
    strategie_evidence_ids = filter_evidence_ids(result.get("strategie_evidence_ids"), gueltige, feld="strategie")
    argument_evidence_ids  = filter_evidence_ids(result.get("argument_evidence_ids"), gueltige, feld="argument")
    # Der Marktvergleich ist Grundlage der Preisstrategie -> immer unter der
    # Preisbegründung zeigen, auch wenn das LLM ihn nicht referenziert.
    preis_evidence_ids = ergaenze_id(preis_evidence_ids, marktvergleich_id(insights))
    # §29: "Warum diese Strategie?" darf keine Karte zeigen, die bereits unter "Warum
    # dieser Preis?" steht (identischer Marktvergleich-Insight) — sonst erscheint die
    # komplette Marktbegründung zweimal identisch auf derselben Seite. Nur wirklich
    # ZUSÄTZLICHE Evidence bleibt in strategie_evidence_ids.
    strategie_evidence_ids = [i for i in strategie_evidence_ids if i not in preis_evidence_ids]

    # Phase 2: Kern-Erkenntnisse deterministisch verdichten (Marktposition, fehlende
    # Angaben, wertsteigernde Ausstattung, Markt-Datenqualität) — kein weiteres LLM.
    key_findings = build_key_findings_verkauf(req, baureihe, motor_match, insights, price_assessment)

    # Phase 4: deterministische Inseratsanalyse (Qualität, fehlende Angaben,
    # Verkaufsargumente, Widersprüche, Titelvorschlag) — kein LLM. Die optimierte
    # LLM-Inseratsversion wird NICHT hier erzeugt, sondern on-demand über einen
    # separaten Endpoint (Kosten/Geschwindigkeit).
    listing_analyse = build_listing_analyse(req, baureihe, motor_match, insights)

    return {
        "bericht":                     result.get("bericht", ""),
        "schnellverkaufs_preis":        result.get("schnellverkaufs_preis"),
        "maximal_preis":               result.get("maximal_preis"),
        "empfohlener_preis":           result.get("empfohlener_preis"),
        "verkaufsdauer_tage_schnell":  result.get("verkaufsdauer_tage_schnell"),
        "verkaufsdauer_tage_maximal":  result.get("verkaufsdauer_tage_maximal"),
        "verkaufsdauer_schnell":       result.get("verkaufsdauer_schnell"),
        "verkaufsdauer_empfohlen":     result.get("verkaufsdauer_empfohlen"),
        "verkaufsdauer_maximal":       result.get("verkaufsdauer_maximal"),
        "price_assessment":            price_assessment,
        "research_status":             status,
        "marktpreis_min":              result.get("marktpreis_min"),
        "marktpreis_max":              result.get("marktpreis_max"),
        "baureihe_erkannt":            baureihe["id"] if baureihe else None,
        "motor_erkannt":               motor_match["variante_id"] if motor_match else None,
        "quelle":                      quelle,
        "vertrauen":                   vertrauen,
        "belege":                      belege,
        "insights":                    insights,
        "preis_evidence_ids":          preis_evidence_ids,
        "strategie_evidence_ids":      strategie_evidence_ids,
        "argument_evidence_ids":       argument_evidence_ids,
        "key_findings":                key_findings,
        "listing_analyse":             listing_analyse,
        "inserat_optimierung":         None,   # on-demand, separater Endpoint
    }
