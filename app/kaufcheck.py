from __future__ import annotations

"""
Kauf-Check: Inserat-Analyse mit DB-Abgleich und Marktpreisbewertung.

Ablauf:
  1. Baureihe + Motorvariante in SQLite erkennen
  2. DB-Kontext aufbauen (Schwachstellen, Rückrufe, Motorspecs)
  3. Marktpreis per Tavily ermitteln — OPTIONALES ZUSATZMODUL (siehe unten)
  4. Gemini (JSON-Modus) liefert strukturierten Bericht

Marktpreis-Entkopplung (P0-1): Der Kaufcheck hat zwei gleichwertige Pfade.

  PFAD A — belastbare Marktdaten vorhanden
      research_status = completed_high | completed_medium
      Median, kanonisches Preisurteil, Preis-Finding, verbindlicher Preis-Block
      im Prompt. Verhalten unverändert.

  PFAD B — kein belastbarer Marktvergleich
      research_status = completed_no_market
      Die technische Kaufanalyse läuft VOLLSTÄNDIG durch (Baureihe, Motor,
      Schwachstellen, Rückrufe, Insights, Key-Findings, Empfehlung, Bericht) —
      es entsteht nur KEINE Preisaussage. `completed_no_market` bedeutet
      ausdrücklich "Check erfolgreich, Marktpreis nicht verfügbar", NICHT
      "Analysefehler": es gibt keine Kontingent-Rückerstattung.

Die Marktanalyse selbst (marktvergleich/marktrecherche/preisurteil, Provider,
Source-Policy) ist davon unberührt und bleibt vollständig erhalten. Wird später
ein produktiver Provider freigeschaltet, greift PFAD A ohne weiteren Umbau.
"""

import asyncio
import logging

from app.car_lookup import (
    find_baureihe_mit_vertrauen, find_motor, build_db_context, call_gemini_json,
    _notfall_extraktion,
)
from app.config import TAVILY_API_KEY
from app.database import get_alle_baureihen_kurz, get_alle_motorvarianten_kurz
from app.empfehlungs_floor import wende_floor_an
from app.kaufempfehlung_sync import synchronisiere_kaufempfehlung
from app.fahrzeugkontext import build_fahrzeugkontext
from app.laufleistung import (
    build_laufleistungskontext, prompt_block as laufleistung_prompt_block,
)
from app.technical_research import recherchiere_technisch, technical_coverage
from app.evidence import (
    build_insights, format_evidence_for_prompt, filter_evidence_ids,
    valid_evidence_ids, enrich_marktvergleich_spanne, marktvergleich_id, ergaenze_id,
)
from app.marktvergleich import analysiere_markt, baue_ziel, modell_relevant, prompt_block as markt_prompt_block
from app.marktrecherche import (
    vertiefe_marktrecherche, baue_deep_queries, baue_rare_queries, research_status,
    marktpreis_recherche_moeglich,
)
from app.preisurteil import (
    bewerte_preis, preis_bewertung_aus_verdict, no_market_prompt_block,
    prompt_block as preis_prompt_block,
)
from app.kaufaktionen import build_kaufaktionen
from app.hu_termin import bewerte_hu, prompt_zeile as hu_prompt_zeile, bereinige_bericht as hu_bereinige
from app.markt_quellen import geeignete_marktquellen
from app.empfehlung_gruende import baue_empfehlung_gruende
from app.fahrzeugkontext import generationslabel
from app.postprocess import neutralisiere_preiszeile_ohne_markt
from app.key_findings import build_key_findings_kauf
from app.models import KaufCheckRequest
from app.vehicle_identity import VehicleIdentity
from app.kaufcheck_bericht import (
    kontext as kanonischer_kontext, bericht as kanonischer_bericht,
    datenbasis_objekt,
)
from app.empfehlungs_policy import entscheide as entscheide_empfehlung
from app.anzeige import fahrzeug_titel
from app.postprocess import (
    postprocess_answer, entferne_erfundene_verkaufsdauer, neutralisiere_wartungs_faelligkeit,
    neutralisiere_no_market_preisurteil,
)
from app.recall_filter import ausgeschlossene_rueckrufe, gefilterte_rueckrufe
from app.report_validator import pruefe_bericht
from app.web_search import (
    tavily_search_with_fallback, results_to_context, results_to_belege, curate_results,
    KATEGORIE_MARKTPREISE, US_QUELLEN_AUSSCHLUSS,
)
from app.schreibstil import (
    STILREGEL_GEDANKENSTRICHE, bereinige_nutzertexte, entferne_gedankenstriche,
)
from app.bekannte_fakten import (
    aus_request as bekannte_fakten_aus_request,
    bereinige_bericht as bekannte_fakten_bereinige_bericht,
)
from app.risikothemen import RISIKO_KATEGORIEN, bauteil_kern
from app.vergleichstabelle import (
    als_markdown as vergleich_als_markdown, baue_zeilen as baue_vergleichszeilen,
    setze_in_bericht as setze_vergleichstabelle,
)
from app.getriebe import (
    aus_request as getriebe_aus_request, prompt_zeile as getriebe_prompt_zeile,
)
from app.verkaeuferart import (
    aus_request as verkaeuferart_aus_request,
    prompt_zeile as verkaeuferart_prompt_zeile,
)
from app.wartungsangabe import (
    aus_request as wartungsangabe_aus_request,
    prompt_zeile as wartungsangabe_prompt_zeile,
)
from app.rueckruf_konsistenz import ergaenze_fehlende_rueckrufe
from app.servicehistorie import (
    neutralisiere_claims as servicehistorie_neutralisieren,
    prompt_zeile as servicehistorie_prompt_zeile,
    status as servicehistorie_status,
)

# Marktpreis-Quellen für den Kaufcheck: nur so viele wie wirklich nötig, um eine
# belastbare Preisspanne zu begründen (Final Polish Quellenqualität) — statt
# pauschal aller 5 abgefragten Treffer.
_MAX_KAUFCHECK_QUELLEN = 4

# Bekannte Abweichungen des Modells vom vorgegebenen preis_bewertung-Enum
# (siehe _SYSTEM) auf den nächstliegenden Schema-Wert abgebildet.
_PREIS_BEWERTUNG_SYNONYME = {
    "guter_deal": "guenstig",
}

log = logging.getLogger(__name__)

_SYSTEM = """Du bereitest die Darstellung eines bereits deterministisch geprüften KaufChecks vor.
Fahrzeugidentität, Feldherkunft, Unfallstatus, Ausstattung und Canonical Risk Set sind verbindlich.
Keine zweite Fahrzeugerkennung. Keine neuen Risiken, Rückrufe, Ausstattungen oder technischen Fakten.
Keine Severity-/Confidence-Änderung, keine qualitative Laufleistungsbewertung ohne Referenz.
series_only bedeutet weder fahrzeugbezogene Betroffenheit noch einen offenen Rückruf.
Die FIN-Prüfung klärt zuerst Betroffenheit und, falls betroffen, anschließend Durchführung.
Eine Inseratsangabe darfst du NICHT verstärken: "scheckheftgepflegt" ist NICHT "lückenlose Wartungshistorie".
Ein ungeprüfter Wartungshinweis ist KEINE starre Herstellervorgabe.
Nenne den Motorcode exakt so, wie er im DB-Kontext steht.
[[STILREGEL]]
Gib ausschließlich JSON mit risiko_evidence_ids aus: eine Liste vorhandener IDs für die Darstellung.
Freier Berichtstext, Preise und Empfehlungen werden nicht übernommen.
"""


def _heute():
    """Aktuelles Datum — eigene Funktion, damit Tests es festsetzen können."""
    import datetime as _dt
    return _dt.date.today()


def _format_inserat(req: KaufCheckRequest) -> str:
    if req.freitext:
        return f"INSERAT-TEXT:\n{req.freitext}"

    lines = ["INSERAT-DATEN:"]
    if req.marke:          lines.append(f"Marke:          {req.marke}")
    if req.modell:         lines.append(f"Modell:         {req.modell}")
    if req.baujahr:        lines.append(f"Baujahr:        {req.baujahr}")
    if req.kilometerstand: lines.append(f"Kilometerstand: {req.kilometerstand:,} km".replace(",", "."))
    if req.motor:          lines.append(f"Motor:          {req.motor}")
    if req.kraftstoff:     lines.append(f"Kraftstoff:     {req.kraftstoff}")
    if req.leistung_ps:    lines.append(f"Leistung:       {req.leistung_ps} PS")
    if req.preis_eur:      lines.append(f"Preis:          {req.preis_eur:,} €".replace(",", "."))
    if req.ausstattung:    lines.append(f"Ausstattung:    {', '.join(req.ausstattung)}")
    if req.beschreibung:   lines.append(f"Beschreibung:   {req.beschreibung}")
    if req.unfallfrei:     lines.append(f"Unfallfrei:     {req.unfallfrei}")
    if req.vorbesitzer is not None: lines.append(f"Vorbesitzer:    {req.vorbesitzer}")
    if req.tuev_bis:       lines.append(f"TÜV bis:        {req.tuev_bis}")
    # Getriebe / Verkäufer / Servicehistorie: die kanonischen Zeilen aus den
    # jeweiligen Modulen — dieselbe Formulierung wie in Checkliste und Key Findings,
    # inklusive des "laut Inserat" IM Satz. `getriebe_aus_request` löst dabei in
    # derselben Rangfolge auf wie der Prüfplan (Nutzerangabe > Freitext > DB), damit
    # Bericht und Checkliste nie von verschiedenen Getriebearten ausgehen.
    #
    # Die alte Zeile "Scheckheftgepflegt: ja/nein" ist ersetzt: `servicehistorie_status`
    # liest das neue Feld UND bildet die alte Checkbox ab, ein gespeicherter Alt-Check
    # erzeugt also weiterhin eine Servicehistorie-Zeile.
    lines.append(getriebe_prompt_zeile(getriebe_aus_request(req)))
    lines.append(verkaeuferart_prompt_zeile(verkaeuferart_aus_request(req)))
    lines.append(servicehistorie_prompt_zeile(servicehistorie_status(req)))
    # LIVE-RUN-BEFUND: eine im Inseratstext ausdruecklich genannte letzte Wartung
    # kam nirgends strukturiert an — der Bericht fragte danach, obwohl sie dastand.
    lines.append(wartungsangabe_prompt_zeile(wartungsangabe_aus_request(req)))
    return "\n".join(z for z in lines if z)


async def run_kaufcheck(req: KaufCheckRequest, retry: bool = False) -> dict:
    """`retry` (§22/§33): True, wenn dies ein "Erneut versuchen" nach research_failed
    ist — erzwingt frische Tavily-Calls statt einer identischen gecachten Antwort."""
    # 1. Baureihe erkennen (DB, blockierend) UND Marktpreis per Tavily (Netzwerk) laufen
    #    PARALLEL — die Tavily-Queries hängen nur an den Inserat-Rohdaten (req.*), nicht
    #    am Ergebnis der Baureihe-Erkennung, sind also unabhängig voneinander.
    baureihe_task = asyncio.to_thread(find_baureihe_mit_vertrauen, req.marke, req.modell, req.baujahr)

    # Cost-Gate (RC1): Steht schon vor dem ersten Request fest, dass keine
    # freigegebene Quelle eine Preisbewertung tragen kann, entfällt die
    # Marktrecherche komplett. Fachlich ändert sich nichts: das Ergebnis wäre
    # ohnehin "keine belastbare Marktpreisbewertung" (PFAD B weiter unten).
    # Wird später eine Quelle freigegeben, läuft der Pfad unverändert wieder an.
    markt_recherche = marktpreis_recherche_moeglich(req.marke, req.modell)
    if not markt_recherche:
        log.info("Kaufcheck: keine fuer die Preisbildung freigegebene Quelle, "
                 "Markt-Webrecherche entfaellt (0 Tavily-Calls).")

    web_results_task: asyncio.Task[list[dict]] | None = None
    if markt_recherche:
        # Marktpreis per Tavily — kaskadierende Queries: spezifisch → breiter,
        # damit auch bei seltenen Modellen/Ausstattungen möglichst immer Ergebnisse kommen.
        q_spezifisch = " ".join(filter(None, [
            req.marke, req.modell, req.motor,
            str(req.baujahr) if req.baujahr else None,
            f"{req.kilometerstand // 1000 * 1000} km" if req.kilometerstand else None,
            "Gebrauchtpreis Deutschland",
        ]))
        q_mittel = " ".join(filter(None, [
            req.marke, req.modell, str(req.baujahr) if req.baujahr else None,
            "Gebrauchtpreis Deutschland",
        ]))
        q_breit = f"{req.marke} {req.modell} Gebrauchtpreis Deutschland"
        web_results_task = asyncio.ensure_future(
            tavily_search_with_fallback(
                # count=8: Tavily "basic" liefert bis zu max_results Treffer für 1
                # Credit — mehr Snippets = mehr extrahierbare Preis-Datenpunkte für
                # den Marktvergleich (Kosten bleiben identisch).
                [q_spezifisch, q_mittel, q_breit], count=8,
                exclude_domains=US_QUELLEN_AUSSCHLUSS,
            )
        )

    # ── Identity-Trust-Gate ───────────────────────────────────────────────────
    # Der Trust-Audit hat belegt, dass ein reiner Teilstring-Treffer bisher dieselbe
    # Wirkung hatte wie ein exakter: "BMW iX7" (existiert nicht) wurde zu
    # `bmw-x7-g07` und erzeugte acht fahrzeugspezifische Schwachstellen-Aktionen.
    # `find_baureihe_mit_vertrauen` liefert jetzt zusaetzlich, WIE der Treffer
    # zustande kam.
    #
    # Zwei Sichten auf dasselbe Ergebnis:
    #   `baureihe_markt` — der Rohtreffer, unveraendert wie bisher. Er geht
    #       ausschliesslich in die Marktrecherche (`baue_ziel`, `VehicleIdentity`).
    #       Dort existiert bereits eine eigene Trust-Schicht (app/verification.py:
    #       ungeprueft = nur weich), und die Etappe-1-Marktanalyse soll durch dieses
    #       Ticket nicht regressieren.
    #   `baureihe` — die GEGATETE Sicht. Ist die Zuordnung nicht belastbar, ist sie
    #       None und wird damit behandelt wie "kein DB-Profil vorhanden": keine
    #       Schwachstellen, keine Motorprobleme, keine kritische Wartung, keine
    #       Rueckruf-Betroffenheit, keine fahrzeugspezifischen Kaufaktionen, kein
    #       Fahrzeugkontext, kein DB-Profil im Prompt.
    #
    # Der Check bricht dabei NICHT ab: Inserat-Daten, Marktrecherche, LLM-Bericht
    # und die allgemeinen Basis-Pruefplaene laufen vollstaendig weiter.
    baureihe_markt, identitaet = await baureihe_task
    motor_markt = find_motor(baureihe_markt, req.motor, req.modell, req=req) if baureihe_markt else None
    if identitaet["belastbar"]:
        baureihe, motor_match = baureihe_markt, motor_markt
    else:
        log.info("Kaufcheck: Baureihe nicht belastbar zugeordnet (match=%s) — "
                 "fahrzeugspezifische DB-Aussagen werden unterdrueckt",
                 identitaet["match_art"])
        baureihe, motor_match = None, None

    # ── Technischer Web-Fallback ("DB FIRST, aber niemals DB ONLY") ───────────
    # Laeuft NUR bei einem Trigger: DB-Miss, gegatete Identitaet, fehlender Motor
    # trotz konkreter Nutzerangabe, oder harter Widerspruch Nutzer<->DB. Ein
    # sicherer, vollstaendiger DB-Treffer loest KEINE Recherche aus — weder Latenz
    # noch Tavily-Budget.
    #
    # Wichtig: der Fallback erfindet keine Identitaet. `_identitaet_belegt` verlangt
    # den Modellnamen als GANZES Token auf mindestens zwei unabhaengigen,
    # hinreichend vertrauenswuerdigen Domains. "BMW iX7" liefert X7-Seiten (Token
    # "x7", nicht "ix7") und bleibt damit korrekt unbelegt.
    #
    # Fehler des Providers werden intern abgefangen (provider_fehler=True) — der
    # Check laeuft weiter, nur ohne Web-Ergaenzung.
    web_recherche = await recherchiere_technisch(
        req, baureihe_markt, identitaet, baureihe, motor_match)
    if web_recherche is not None:
        log.info("Kaufcheck: technischer Web-Fallback (grund=%s, belegt=%s, fakten=%d, fehler=%s)",
                 web_recherche.ausgeloest_durch,
                 bool(web_recherche.identitaet and web_recherche.identitaet.belegt),
                 len(web_recherche.fakten), web_recherche.provider_fehler)

    # 2. DB-Kontext
    #
    # P1-4: Der Fahrzeugkontext (Segment, Generations-/Facelift-Merkmale, Vorgänger,
    # Wartungsintervalle) stammt aus Feldern, die der Kaufcheck bislang gar nicht
    # gelesen hat. Er wird aus dem BEREITS geladenen `baureihe`-Dict gebaut — kein
    # zusätzlicher DB-Zugriff — und dem LLM als ausdrücklich ERGÄNZENDER Kontext
    # mitgegeben, nicht als Evidence.
    #
    # Ausdrücklich NICHT enthalten: `kaufberatung`. Das Feld ist nur bei 22 % der
    # Baureihen befüllt und werblich formuliert ("exzellente Kombination aus
    # sportlicher Fahrdynamik") — genau die Marketingsprache, die `_SYSTEM` oben
    # verbietet. Es würde den Bericht zuverlässig verschlechtern.
    #
    # Der Kontext hängt an KEINER Marktinformation: bei `completed_no_market`
    # entsteht exakt derselbe Block wie bei vorhandenem Marktpreis.
    # RC1: Werkscode passend zur im Inserat genannten Karosserie ("G20" statt
    # "G20/G21"), wiederverwendet aus der AutoFinder-Auflösung. Die Baureihe wird
    # für DIESEN Check mit dem aufgelösten Label weitergegeben (DB-Kontext,
    # Kaufaktionen); der Marktpfad arbeitet weiter mit `baureihe_markt`.
    karosserie_text = " ".join(filter(None, [req.modell, req.beschreibung, req.freitext]))
    if baureihe:
        label = generationslabel(baureihe, karosserie_text)
        if label and label != baureihe.get("generation"):
            baureihe = {**baureihe, "generation": label}
    fahrzeugkontext = build_fahrzeugkontext(baureihe)
    # Der DB-Kontext für das Modell entsteht weiter unten, NACH `build_insights`:
    # er zeigt die kanonische Risikomenge mit Beleglage (Root-Cause-Closing).
    # RC1: HU-Termin deterministisch gegen HEUTE bewerten (nie im Modell).
    heute = _heute()
    hu = bewerte_hu(req.tuev_bis, heute=heute, baujahr=req.baujahr)

    web_results_roh: list[dict] = await web_results_task if web_results_task else []

    # Marktvergleich 2.0 + adaptive Recherche: Ziel-Profil (harte Modelltreue) bauen,
    # dann die Recherche adaptiv VERTIEFEN, bis genug akzeptierte, modelltreue
    # Vergleiche vorliegen (nicht anhand roher Trefferzahl aufhören — #4/#7).
    # Marktpfad bewusst mit dem ROHTREFFER (siehe Identity-Trust-Gate oben):
    # unveraendertes Verhalten der Etappe-1-Marktanalyse.
    ziel = baue_ziel(baureihe_markt, motor_markt, req,
                     get_alle_baureihen_kurz() if baureihe_markt else [],
                     get_alle_motorvarianten_kurz() if baureihe_markt else [])
    # Adaptive, qualitäts-gesteuerte Recherche auch OHNE erkannte Baureihe, sofern
    # Marke+Modell vorliegen (§0: populäre, aber DB-unbekannte Fahrzeuge sollen die
    # Qualitätsschwelle trotzdem erreichen können).
    identity = VehicleIdentity.from_check_context(baureihe, motor_match, req)
    # §5.6: eine belegte Web-Identität (technischer Fallback, s.o.) ergänzt NUR
    # echte Lücken der kanonischen Identität — DB/Nutzerangabe bleiben Guardrail.
    identity.apply_web_evidence(web_recherche)
    if markt_recherche:
        deep_queries = baue_deep_queries(identity)
        rare_queries = baue_rare_queries(identity)
        # §Phase 0/13 (gemessen, scripts/diagnose_provider_matrix.py): max_results
        # 20 statt 10 verdoppelt die extrahierbaren Preis-Datenpunkte bei BMW 320d
        # (48->86) und Insignia (104->229) OHNE Mehrkosten (Tavily "basic" ist pro
        # Request, nicht pro Ergebnis, abgerechnet) und ohne den Latenz-/
        # Zeitüberschreitungs-Nachteil von search_depth="advanced" (2-4x langsamer,
        # ein Lauf schlug in der Messung sogar fehl). "advanced" bleibt daher NICHT
        # produktiv verdrahtet — siehe app/web_search.py::tavily_search(search_depth=).
        web_results_roh, marktanalyse, diag = await vertiefe_marktrecherche(
            web_results_roh, deep_queries, ziel, req.preis_eur, US_QUELLEN_AUSSCHLUSS,
            count=20, zweck="kaufcheck-markt", rare_queries=rare_queries, bypass_cache=retry)
    else:
        marktanalyse = analysiere_markt(web_results_roh, ziel, req.preis_eur)
        diag = {"research_failure_grund": "technical_failure" if not TAVILY_API_KEY else "data_exhausted"}

    # ── Quality-Gate (§0/§17/§21) + Marktpreis-Entkopplung (KaufCheck-P0-1) ──────
    # `markt_status` bewertet AUSSCHLIESSLICH die Marktrecherche (unveraendert:
    # app/marktrecherche.research_status). "research_failed" heisst dort weiterhin
    # "kein belastbarer Median" — diese Regel wurde NICHT gelockert.
    #
    # Was sich geaendert hat, ist die REAKTION darauf. Frueher brach der gesamte
    # Kaufcheck ab (`raise RechercheUnzureichend`) und verwarf damit alles, was
    # bereits deterministisch feststand: erkannte Baureihe, erkannte Motorvariante,
    # baujahrgefilterte Schwachstellen, geprüfte Rückrufe, Insights, Widerspruchs-
    # Findings. Der Nutzer bekam fuer ein Fahrzeug ohne Marktdaten GAR NICHTS —
    # obwohl der technische Teil der Kaufberatung vollstaendig vorlag.
    #
    # Jetzt gilt: der Marktvergleich ist ein OPTIONALES ZUSATZMODUL des Kaufchecks.
    #   PFAD A (markt_verfuegbar): unveraendert — Median, kanonisches Preisurteil,
    #           Preis-Finding, verbindlicher Preis-Block im Prompt.
    #   PFAD B (kein belastbarer Markt): technische Analyse laeuft vollstaendig
    #           weiter, aber es entsteht KEINE Preisaussage. Statt des Preis-Blocks
    #           bekommt das Modell einen expliziten No-Market-Block.
    #
    # Der Check-Status ist deshalb NICHT identisch mit dem Markt-Status:
    # "completed_no_market" heisst "Kaufcheck fachlich erfolgreich abgeschlossen,
    # Marktpreis nicht verfuegbar" — es ist ausdruecklich KEIN Analysefehler und
    # loest keine Kontingent-Rueckerstattung aus (der Nutzer erhaelt ein
    # vollstaendiges technisches Ergebnis).
    markt_status = research_status(marktanalyse)
    markt_verfuegbar = markt_status != "research_failed"
    status = markt_status if markt_verfuegbar else "completed_no_market"
    if not markt_verfuegbar:
        log.info("Kaufcheck ohne Marktdaten (grund=%s) — technische Analyse laeuft weiter",
                 diag.get("research_failure_grund", "data_exhausted"))

    # Kanonisches, deterministisches Preisurteil (§6/§7/§13) — EINE Quelle der Wahrheit.
    # Ohne belastbaren Median liefert `bewerte_preis` von sich aus verdict="unbekannt"
    # ohne Median/Spanne/Differenz — kein Dummy-Preis, kein Angebotspreis als
    # Marktwert, kein DB-Neupreis. Das Objekt existiert trotzdem, damit die
    # Response-Struktur fuer das Frontend unveraendert bleibt.
    price_assessment = bewerte_preis(marktanalyse, req.preis_eur, check_typ="kauf")

    # Quellenqualität für LLM-Kontext/Belege: fachfremde Modell-Seiten aussortieren
    # (kein 'BMW 4er'/'Mercedes C-Klasse' als 3er-Quelle), dann Marktplätze bevorzugt,
    # Social Media/Duplikate raus, auf so viele Quellen wie nötig begrenzt.
    # RC1: als Marktquelle zählt nur, was POSITIV zum Fahrzeug passt — keine
    # Fehler-/Sperrseiten, Teileshops, Leasingangebote oder fachfremden Seiten
    # (app/markt_quellen.py). Vorher liess `modell_relevant` alles ohne
    # Modellsignal als "neutral" durch.
    web_relevant = [r for r in geeignete_marktquellen(web_results_roh, ziel, req.marke)
                    if modell_relevant(r, ziel)]
    web_results = curate_results(web_relevant, kategorie=KATEGORIE_MARKTPREISE, max_results=_MAX_KAUFCHECK_QUELLEN)
    web_ctx = results_to_context(web_results)
    belege  = results_to_belege(web_results)

    # 4. Gemini-Analyse
    motor_status = (
        f"MOTOR-STATUS: erkannt ({motor_match['bezeichnung']})" if motor_match
        else "MOTOR-STATUS: keine eindeutige ENFAL-Zuordnung; vorhandene Motorangabe bleibt Inseratsangabe"
    )
    # Phase 1 Schicht B: Evidence deterministisch VOR dem LLM bauen (Marktvergleich
    # 2.0 ist jetzt bereits vor dem LLM berechnet) und dem LLM kompakt zum
    # Referenzieren mitgeben. Die IDs sind stabil, sodass die vom LLM referenzierten
    # IDs anschließend gegen genau diese Insights validiert werden können.
    insights = build_insights(baureihe, motor_match, belege, req, check_typ="kauf",
                              marktanalyse=marktanalyse, web_recherche=web_recherche, identity=identity)
    evidence_block = format_evidence_for_prompt(insights)
    # Root-Cause-Closing (Befund C/E, 4.5): der DB-Kontext zeigt dem Modell die
    # KANONISCHE Risikomenge mit Beleglage, nicht mehr die drei Rohlisten. Der
    # Stilfilter läuft über den ganzen Block, damit Datenbanktexte mit
    # Gedankenstrich nicht als Vorlage in den Bericht wandern.
    db_ctx = kanonischer_kontext(identity, req, insights)
    # P2-5: Laufleistungs- und Wartungskontext. Bekommt NUR Request und Insights —
    # weder Marktanalyse noch Preis (§13), damit eine Preisaussage aus der
    # Laufleistung strukturell unmoeglich bleibt und PFAD B (`completed_no_market`)
    # exakt denselben Kontext liefert wie PFAD A (§14).
    #
    # Der Prompt-Block traegt seine Verbote selbst mit: ohne ein ausdrueckliches
    # "der letzte Service ist NICHT bekannt" formuliert ein Modell aus
    # "Wartungspunkt 120.000 km" + "Fahrzeug 160.000 km" zuverlaessig eine
    # Faelligkeit, die durch keine Datenquelle gedeckt ist.
    laufleistungskontext = build_laufleistungskontext(req, insights)
    laufleistung_block = laufleistung_prompt_block(laufleistungskontext)
    # PFAD A: verbindliche Markt-/Preisbloecke wie bisher.
    # PFAD B: EIN expliziter No-Market-Block statt beider. Ohne ihn wuerde das
    # Modell die Preisanweisungen aus `_SYSTEM` ("leite eine grobe marktpreis_min/
    # max-Spanne ab") weiter befolgen und aus den Web-Snippets eine Spanne
    # konstruieren — beide Blockfunktionen liefern bei fehlendem Median lediglich
    # einen Leerstring, schweigen allein reicht hier also nicht.
    if markt_verfuegbar:
        markt_block = markt_prompt_block(marktanalyse)
        preis_block = preis_prompt_block(price_assessment)
    else:
        markt_block = no_market_prompt_block()
        preis_block = ""
    datum_zeile = f"HEUTIGES DATUM: {heute.month:02d}/{heute.year}"
    user_msg = "\n\n".join(filter(None, [datum_zeile, _format_inserat(req),
                                         hu_prompt_zeile(hu, heute), motor_status, db_ctx, web_ctx,
                                         laufleistung_block, markt_block,
                                         preis_block, evidence_block]))
    # Absichtlich KEIN try/except um Gemini-Totalausfälle (RateLimitExhausted,
    # GeminiVoruebergehendNichtErreichbar) — die propagieren bis zum Router
    # (routers/kaufcheck.py), der einheitlich das Check-Kontingent zurückerstattet
    # und eine saubere Fehlermeldung zeigt, statt hier einen wertlosen "unbekannt"-
    # Bericht als scheinbaren Erfolg (200 OK) zurückzugeben.
    result = await call_gemini_json(_SYSTEM, user_msg)
    # Only ID selections cross the LLM boundary. All assertions remain in the
    # canonical objects, including the recommendation's existing safety floor.
    # Der technische KANDIDAT der Empfehlung. Ob er ausgegeben werden darf,
    # entscheidet ausschließlich die zentrale Policy weiter unten
    # (app/empfehlungs_policy.py), nach dem Risiko-Floor.
    result = {"risiko_evidence_ids": result.get("risiko_evidence_ids", []),
              "empfehlung": "kaufen_nach_besichtigung" if req.marke and req.modell and req.baujahr else "unbekannt"}

    # Preisbewertung deterministisch aus dem KANONISCHEN Preisurteil ableiten (§6/§13)
    # — NICHT mehr vom LLM. So kann das Frontend-Badge (preis_bewertung) niemals dem
    # kanonischen Verdikt/Bericht widersprechen (der zentrale 320d-Widerspruch).
    preis_wert = preis_bewertung_aus_verdict(price_assessment.verdict)

    # Marktpreis-Spanne: liegt eine belastbare deterministische Marktanalyse vor,
    # ist SIE die Wahrheit (robuster Median/Quartilsbereich) — die LLM-Spanne wird
    # dann durch die berechnete ersetzt.
    if marktanalyse and marktanalyse.median_eur:
        result["marktpreis_min"] = marktanalyse.spanne_min_eur
        result["marktpreis_max"] = marktanalyse.spanne_max_eur
    elif markt_verfuegbar:
        # Markt gilt als verfuegbar, aber ohne eigenen Median (kann nach dem
        # Quality-Gate praktisch nicht mehr vorkommen) — bisheriges Verhalten:
        # LLM-Spanne stehen lassen und nur im Insight nachtragen.
        enrich_marktvergleich_spanne(insights, result.get("marktpreis_min"), result.get("marktpreis_max"))
    else:
        # PFAD B: kein belastbarer Markt -> die Preisfelder bleiben leer, egal was
        # das Modell geliefert hat. Letzte Verteidigungslinie gegen eine erfundene
        # Spanne: der No-Market-Block verbietet sie im Prompt, `_notfall_extraktion`
        # darf sie oben nicht rekonstruieren, und hier werden sie endgueltig
        # genullt. Kein Dummy-Wert, kein Angebotspreis, kein DB-Neupreis.
        result["marktpreis_min"] = None
        result["marktpreis_max"] = None
        # "preis_nachverhandeln" ist laut System-Prompt definiert als "Fahrzeug
        # technisch unauffaellig, aber Preis teuer/extrem teuer" — die Preishaelfte
        # dieser Aussage ist ohne Marktdaten nicht belegbar. Statt die Empfehlung
        # ganz zu verwerfen (das wuerde auch die belegte technische Haelfte
        # wegwerfen) bleibt genau der technische Teil stehen: technisch unauffaellig,
        # vor dem Kauf besichtigen.
        if result.get("empfehlung") == "preis_nachverhandeln":
            log.info("Kaufcheck ohne Marktdaten: Empfehlung 'preis_nachverhandeln' auf "
                     "'kaufen_nach_besichtigung' reduziert (Preisteil nicht belegbar)")
            result["empfehlung"] = "kaufen_nach_besichtigung"

    # ── Deterministischer Empfehlungs-Floor ─────────────────────────────────────
    # BEWUSST als LETZTER Eingriff auf `empfehlung`: danach senkt nichts mehr ab.
    # Der Bake-off (2.5 vs. 3.7) hat gezeigt, dass ein Modell die im Systemprompt
    # definierte Bedeutung von "nur_mit_werkstattpruefung" korrekt kennen, die
    # passenden Risiken im Bericht auflisten — und trotzdem das strukturierte Feld
    # milder setzen kann. Die sicherheitsrelevante Mindeststufe haengt deshalb
    # nicht mehr am Modell, sondern an den bereits geprueften Insights.
    #
    # Der Floor kann ausschliesslich ANHEBEN, nie senken, und ist strukturell
    # unabhaengig vom Marktpreis: er sieht nur `insights` — weder `marktanalyse`
    # noch `price_assessment` noch `req.preis_eur`. PFAD B (completed_no_market)
    # verhaelt sich damit identisch zu PFAD A.
    empfehlung_final, floor_befund = wende_floor_an(result.get("empfehlung"), insights)
    # ── Zentrale Empfehlungs-Policy (Identity Floor, Cluster J) ────────────────
    # NACH dem Risiko-Floor, als einzige Stelle, die über den Zustand der
    # Empfehlung entscheidet. Sie liest die KANONISCHE Identität (dieselbe, die
    # der Bericht anzeigt): ohne belegte Generation und Motorisierung gibt es
    # keine Freigabe-Empfehlung ("Analyse eingeschränkt"). Die frühere
    # Zwei-Boolean-Prüfung (DB belastbar ODER Web "belegt") griff beim
    # DB-Miss praktisch nie, weil "belegt" nur Marke+Modell-Tokens prüfte.
    entscheidung = entscheide_empfehlung(empfehlung_final, identity, web_recherche=web_recherche)
    if entscheidung.empfehlung != empfehlung_final:
        log.info("Kaufcheck: Empfehlung %s -> %s (Identitätsstufe %s, offen: %s)",
                 empfehlung_final, entscheidung.empfehlung, entscheidung.identitaet.stufe,
                 ", ".join(entscheidung.identitaet.fehlend))
    empfehlung_final = entscheidung.empfehlung
    result["empfehlung"] = empfehlung_final
    # Bericht/Feld-Konsistenz: IMMER, nicht nur wenn der Floor angehoben hat.
    # `wende_floor_an` deckt nur EINEN Weg ab, wie Bericht und Feld auseinander-
    # laufen koennen (Floor hebt an, Bericht zeigt noch die alte Stufe) — das LLM
    # kann aber auch ganz ohne Floor-Beteiligung ein Feld liefern, das zu seinem
    # EIGENEN Bericht widerspricht (z.B. Feld "kaufen", Bericht "KAUFEN NACH
    # BESICHTIGUNG"). Das finale, bereits vollstaendig deterministisch bereinigte
    # `empfehlung_final` ist in JEDEM Fall die Wahrheit — die Ueberschrift muss
    # dazu passen, unabhaengig davon, WARUM sie zuvor abwich. `synchronisiere_
    # kaufempfehlung` ist idempotent (stimmt die Ueberschrift schon, aendert sich
    # nichts) und greift nur die fettgedruckte Zeile an — der Rest des Berichts
    # bleibt unangetastet.
    if result.get("bericht"):
        result["bericht"] = synchronisiere_kaufempfehlung(result["bericht"], empfehlung_final)

    # Schicht B: vom LLM gelieferte Evidence-IDs gegen die ECHTEN Insight-IDs
    # validieren — Halluzinationen verwerfen (Backend bleibt Source of Truth).
    gueltige = valid_evidence_ids(insights)
    empfehlung_evidence_ids = filter_evidence_ids(result.get("empfehlung_evidence_ids"), gueltige, feld="empfehlung")
    preis_evidence_ids      = filter_evidence_ids(result.get("preis_evidence_ids"), gueltige, feld="preis")
    risiko_evidence_ids     = filter_evidence_ids(result.get("risiko_evidence_ids"), gueltige, feld="risiko")
    # Der Marktvergleich ist die Grundlage der Preisbewertung -> immer unter
    # "Warum diese Preisbewertung?" zeigen, auch wenn das LLM ihn nicht referenziert.
    preis_evidence_ids = ergaenze_id(preis_evidence_ids, marktvergleich_id(insights))
    # Erklaerbarkeit des Floors (§8): hat er angehoben, sind SEINE Belege die
    # Begruendung der finalen Empfehlung — also gehoeren sie unter "Warum diese
    # Empfehlung?". Es sind ausschliesslich echte, bereits validierte Insight-IDs
    # aus genau diesem Check; kein neues Nutzerfeld noetig, kein unsichtbarer
    # Override.
    # RC1: "Warum diese Empfehlung?" darf nicht dieselben Punkte zeigen wie
    # "Warum diese Risiken?". Was bereits als Risiko referenziert ist, erklärt
    # die Empfehlung nicht und fällt dort heraus. Ausnahme: hat der Floor die
    # Empfehlung angehoben, SIND seine Belege der Grund (unten wieder ergänzt).
    empfehlung_evidence_ids = [i for i in empfehlung_evidence_ids
                               if i not in set(risiko_evidence_ids)]
    # ROOT-CAUSE-CLOSING (4.4): "Belege zur Empfehlung" zeigt nur, was eine
    # Empfehlung tragen darf. Ein ungeprüfter technischer Hinweis (Datenqualität
    # niedrig) bleibt als Risiko sichtbar, erscheint aber nicht als Beleg der
    # Kaufempfehlung. Sonst würde er sie unbemerkt begründen.
    _schwach_belegt = {i.id for i in insights
                       if i.kategorie in RISIKO_KATEGORIEN and i.confidence == "niedrig"}
    empfehlung_evidence_ids = [i for i in empfehlung_evidence_ids if i not in _schwach_belegt]
    if floor_befund is not None:
        for _fid in floor_befund.evidence_ids:
            empfehlung_evidence_ids = ergaenze_id(empfehlung_evidence_ids, _fid)

    # Phase 2: Kern-Erkenntnisse deterministisch aus den bereits vorhandenen Daten
    # verdichten (Marktanalyse in insights, Rückruf-Applicability, Schwachstellen,
    # Inserat-Widersprüche) — kein weiteres LLM, referenziert nur echte Insight-IDs.
    key_findings = build_key_findings_kauf(
        req, baureihe, motor_match, insights, price_assessment, identitaet=identitaet,
        web_belegt=bool(web_recherche and web_recherche.identitaet
                        and web_recherche.identitaet.belegt),
        identity=identity)

    # P1-3: deterministische Kaufaktionen (Besichtigung / Probefahrt / Verkaeufer-
    # fragen / Dokumente) aus DENSELBEN bereits aufbereiteten Daten — keine neuen
    # DB-Lookups, kein zweiter Gemini-Call, der Berichtstext ist ausdruecklich KEINE
    # Quelle (§18/§4).
    #
    # Bewusst OHNE Markt-/Preisparameter (§15): `build_kaufaktionen` bekommt weder
    # `marktanalyse` noch `price_assessment` noch `req.preis_eur` als Preissignal —
    # eine Preis- oder Nachverhandlungsaktion ist damit strukturell nicht
    # konstruierbar. PFAD B (`completed_no_market`) liefert deshalb exakt dieselben
    # technischen Aktionen wie PFAD A.
    kaufaktionen = build_kaufaktionen(req, baureihe, motor_match, insights,
                                     laufleistungskontext=laufleistungskontext)

    empfehlung_gruende = baue_empfehlung_gruende(
        req, baureihe, motor_match, insights, key_findings, result.get("empfehlung", "unbekannt"),
        markt_verfuegbar, getattr(price_assessment, "label", None), hu=hu,
        generation=(baureihe or {}).get("generation"), identity=identity)

    # EINE Datenbasis für Berichtszeile, Quellen-Chip (`quelle`) und `vertrauen`.
    basis = datenbasis_objekt(baureihe, insights, belege,
                              identity=identity, markt_verfuegbar=markt_verfuegbar)
    sources = basis["labels"]
    quelle, vertrauen = basis["quelle"], basis["vertrauen"]
    result["bericht"] = kanonischer_bericht(
        req, identity, baureihe, motor_match, insights, kaufaktionen, empfehlung_gruende,
        result["empfehlung"], price_assessment, markt_verfuegbar, laufleistungskontext,
        hu, sources, fahrzeugkontext, entscheidung=entscheidung, web_recherche=web_recherche)

    # ROOT-CAUSE-CLOSING (Befund K, 4.7): die Schreibstil-Regel gilt für JEDEN
    # Nutzertext des Ergebnisses: Bericht des Modells, Datenbanktexte, Key
    # Findings, Prüfplan, Empfehlungsgründe, Fahrzeugkontext. Quellen und Belege
    # fremder Seiten bleiben unverändert (app/schreibstil.py).
    return bereinige_nutzertexte({
        "vehicle_identity": identity.as_diagnose(),
        "accident_status": bekannte_fakten_aus_request(req).unfall,
        "datenbasis": sources,
        "recommendation_state": entscheidung.state,
        "empfehlung_anzeige": entscheidung.anzeige,
        "empfehlung_hinweis": entscheidung.hinweis,
        "identitaet_aufloesung": {"stufe": entscheidung.identitaet.stufe,
                                  "fehlend": list(entscheidung.identitaet.fehlend),
                                  "quellen": list(entscheidung.identitaet.quellen)},
        "anzeige_titel": fahrzeug_titel(identity) or None,
        "risiko_titel": "Relevante Risiken und Hinweise",
        "bericht":          result.get("bericht", ""),
        "empfehlung":       result.get("empfehlung", "unbekannt"),
        "preis_bewertung":  preis_wert,
        "price_assessment": price_assessment,
        "research_status":  status,
        "marktpreis_min":   result.get("marktpreis_min"),
        "marktpreis_max":   result.get("marktpreis_max"),
        "baureihe_erkannt": baureihe["id"] if baureihe else None,
        "motor_erkannt":    motor_match["variante_id"] if motor_match else None,
        "quelle":           quelle,
        "vertrauen":        vertrauen,
        "belege":           belege,
        "insights":         insights,
        "empfehlung_evidence_ids": empfehlung_evidence_ids,
        "empfehlung_gruende":      empfehlung_gruende,
        "hu_pruefung":             (None if hu is None else {
            "angabe": hu.anzeige, "status": hu.status,
            "monate_bis_faellig": hu.monate_bis_faellig, "hinweis": hu.hinweis}),
        "preis_evidence_ids":      preis_evidence_ids,
        "risiko_evidence_ids":     risiko_evidence_ids,
        "key_findings":            key_findings,
        "kaufaktionen":            kaufaktionen,
        "fahrzeugkontext":         fahrzeugkontext,
        "laufleistungskontext":    laufleistungskontext,
        "identitaet_konfidenz":    identitaet["konfidenz"],
        "identitaet_match_art":    identitaet["match_art"],
        "technical_coverage":      technical_coverage(baureihe, web_recherche),
        "web_identitaet":          (web_recherche.identitaet
                                    if web_recherche and web_recherche.identitaet
                                    and web_recherche.identitaet.belegt else None),
        # Root Cause 6: je-Phase-Status roh mitgeben, nicht nur in Prosa
        # versteckt — Konsumenten (Diagnose, künftige UI) müssen nicht den
        # Berichtstext parsen, um SUCCESS/PARTIAL/FAILED/NOT_RUN zu lesen.
        "web_recherche_phasen_status": (web_recherche.phasen_status if web_recherche else {}),
    })

# Die gemeinsame Schreibstil-Regel (app/schreibstil.py) wird hier eingesetzt.
# .replace statt f-String, weil der Prompt JSON-Klammern enthaelt.
_SYSTEM = _SYSTEM.replace("[[STILREGEL]]", STILREGEL_GEDANKENSTRICHE)
