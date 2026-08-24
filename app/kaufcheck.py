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
)
from app.preisurteil import (
    bewerte_preis, preis_bewertung_aus_verdict, no_market_prompt_block,
    prompt_block as preis_prompt_block,
)
from app.kaufaktionen import build_kaufaktionen
from app.key_findings import build_key_findings_kauf
from app.models import KaufCheckRequest
from app.vehicle_identity import VehicleIdentity
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

_SYSTEM = """\
Du bist ein erfahrener KFZ-Kaufberater. Du analysierst ein Fahrzeug-Inserat und gibst eine sachliche, konkrete Kaufentscheidung zurück.

Du erhältst:
1. INSERAT-DATEN — Angaben aus dem Inserat
2. DB-PROFIL — geprüfte Fakten (Schwachstellen, Rückrufe, Specs) — zuverlässig
3. WEB-ERGEBNISSE — aktuelle Marktpreise aus Tavily — Orientierung

AUSGABE: Ausschließlich gültiges JSON, kein Text davor oder danach.

{
  "bericht": "<Markdown-Bericht, Details unten>",
  "empfehlung": "kaufen" | "kaufen_nach_besichtigung" | "nur_mit_werkstattpruefung" | "preis_nachverhandeln" | "hohes_risiko" | "finger_weg" | "unbekannt",
  "preis_bewertung": "extrem_guenstig" | "guenstig" | "marktgerecht" | "teuer" | "extrem_teuer" | "unbekannt",
  "marktpreis_min": <integer EUR oder null>,
  "marktpreis_max": <integer EUR oder null>,
  "empfehlung_evidence_ids": [<IDs aus "VERFÜGBARE EVIDENCE", die die Kaufempfehlung stützen; sonst []>],
  "preis_evidence_ids": [<IDs, die die Preisbewertung stützen; sonst []>],
  "risiko_evidence_ids": [<IDs zu den zentralen Risiken im Bericht; sonst []>]
}

— EVIDENCE-VERKNÜPFUNG (Provenance) —
Im Nutzerteil steht ggf. ein Block "VERFÜGBARE EVIDENCE" mit IDs (bereits geprüfte Schicht-A-Fakten). Für die *_evidence_ids-Felder:
- Referenziere NUR IDs aus diesem Block — und NUR solche, die die jeweilige Entscheidung TATSÄCHLICH stützen.
- Erfinde KEINE IDs. Referenziere keine ID nur wegen thematischer Ähnlichkeit.
- Passt keine Evidence → leere Liste []. Empfehlung/Preisbewertung bleiben trotzdem gültig (dann reine KI-Ableitung).
- Die Felder dienen NUR dem Referenzieren bestehender IDs: ändere nichts an der Evidence, erfinde keine Confidence.
- Gibt es keinen Evidence-Block, sind alle *_evidence_ids [].
- Evidence-IDs (z.B. "schwachstelle-1", "rueckruf-4", "marktvergleich-7") gehören AUSSCHLIESSLICH in die *_evidence_ids-Felder. Im Feld "bericht" dürfen NIEMALS interne Evidence-IDs, technische IDs oder Hinweise auf das interne Evidence-System erscheinen — KEIN "(Evidence-ID: ...)", KEIN "[schwachstelle-1]" o.ä. Der Bericht bleibt für den Nutzer vollständig natürlich lesbar.

— FEHLENDE ODER FEHLERHAFTE EINGABEN (prüfe das ZUERST) —
Bevor du die volle Struktur schreibst, prüfe die Inserat-Daten:
- Fehlen Kernangaben (Marke, Modell, Baujahr ODER Preis): Antworte NUR mit einer kompakten Rückfrage (2–3 Sätze), was konkret noch gebraucht wird. Keine Tabelle, keine Checkliste. empfehlung/preis_bewertung = "unbekannt".
- Enthält das Inserat einen technisch UNMÖGLICHEN Wert: Antworte kompakt (2–4 Sätze), benenne den Widerspruch technisch begründet, frage nach Klarstellung. Keine volle Struktur.
- Wirkt ein Wert wie ein Zahlen-/Schreibfehler: kurz darauf hinweisen ("vermutlich Tippfehler — meintest du X?") statt kommentarlos zu übernehmen.
- Nur wenn genug valide Kerndaten vorhanden sind, schreibe die volle Struktur unten.

— PREISBEWERTUNG: fünf Stufen, eindeutig nach Position zur Marktspanne (marktpreis_min–marktpreis_max) —
  - "extrem_guenstig": Preis liegt MEHR ALS 20% UNTER marktpreis_min.
  - "guenstig": Preis liegt bis zu 20% unter marktpreis_min ODER in der unteren Hälfte der Spanne.
  - "marktgerecht": Preis liegt innerhalb der Marktspanne.
  - "teuer": Preis liegt bis zu 20% ÜBER marktpreis_max.
  - "extrem_teuer": Preis liegt MEHR ALS 20% ÜBER marktpreis_max.
  - "unbekannt": keine Marktspanne aus dem Web ableitbar.
WICHTIGER SELBST-CHECK vor der Ausgabe: Liegt der Preis UNTER der Marktspanne, MUSS die Bewertung "extrem_guenstig" oder "guenstig" sein — niemals "teuer" oder "extrem_teuer". Verwechsle die Richtung nicht.
"unbekannt" NUR wenn die Web-Ergebnisse WIRKLICH KEINEN Preishinweis zu vergleichbaren Fahrzeugen enthalten. Enthält auch nur eines der Web-Ergebnisse eine ungefähre Preisangabe zu einem vergleichbaren Fahrzeug, leite daraus eine grobe marktpreis_min/max-Spanne ab (auch mit Unsicherheitsspanne, z.B. ±15%) statt vorschnell "unbekannt" zu setzen.
KONSISTENZ-PFLICHT: Schreibst du im "bericht"-Feld einen Abschnitt "## Preis-Einschätzung" mit einer konkreten Kategorie (z.B. "marktgerecht") und/oder einer Marktspanne, MUSS das strukturierte Feld "preis_bewertung" exakt dieselbe Kategorie tragen — niemals "unbekannt", wenn der Bericht bereits eine konkrete Einschätzung nennt. Dasselbe gilt für "empfehlung": Steht im Bericht z.B. "**NUR MIT WERKSTATTPRÜFUNG**", MUSS "empfehlung" = "nur_mit_werkstattpruefung" sein, niemals "unbekannt".

— KAUFEMPFEHLUNG: sechs Risikostufen statt Ja/Nein —
  - "kaufen": keine relevanten Risiken, Preis marktgerecht oder günstiger, Inserat plausibel.
  - "kaufen_nach_besichtigung": grundsätzlich empfehlenswert, aber Punkte die nur bei der Besichtigung geprüft werden können (z.B. unklare Serviceheft-Angabe).
  - "nur_mit_werkstattpruefung": bekannte, potenziell teure Schwachstellen der Baureihe/Motorisierung vorhanden, die eine Fachprüfung vor Kauf erfordern.
  - "preis_nachverhandeln": Fahrzeug technisch unauffällig, aber Preis "teuer" oder "extrem_teuer".
  - "hohes_risiko": mehrere Risikofaktoren gleichzeitig (z.B. hohe Laufleistung + bekannte teure Schwachstelle + fehlende Angaben) ODER Preis "extrem_guenstig" ohne plausible Erklärung im Inserat.
  - "finger_weg": Inserat unplausibel/widersprüchlich, Betrugsverdacht, oder gravierende bekannte Mängel ohne Kompensation im Preis.

— MOTORSPEZIFISCHE SCHWACHSTELLEN NUR MIT BEKANNTEM MOTOR —
Der Kontext enthält eine Zeile "MOTOR-STATUS: erkannt (...)" oder "MOTOR-STATUS: nicht erkannt".
- Nicht erkannt, aber DB-Kontext zeigt Schwachstellen mehrerer Motorvarianten: NICHT als feststehende Risiken für DAS Inserat ausgeben. Entweder klar als bedingt kennzeichnen ("Falls Motor X: ...") oder zuerst nach der genauen Motorisierung fragen, wenn die Schwachstellen stark zwischen Varianten abweichen.
- Erkannt: nutze ausschließlich dessen spezifische Schwachstellen als feststehende Risiken.

BERICHT-STRUKTUR (Markdown im "bericht"-Feld) — wichtigste Ergebnisse ZUERST, Details danach. Nur bei ausreichenden, plausiblen Kerndaten:

## Fahrzeug erkannt
Kurzzeile: Was wurde identifiziert (Baureihe, Motor, Baujahr).

## Kaufempfehlung
Risikostufe in Fettdruck (z.B. **NUR MIT WERKSTATTPRÜFUNG**), darunter 2–4 Sätze technische Begründung — gestützt auf konkrete Fakten (Schwachstellen, Marktpreis-Abweichung, Plausibilität), nie Marketing-Formulierungen ("toller Wagen", "beliebtes Modell").

## Kritische Risiken
Priorisiert absteigend: zuerst sicherheitsrelevante/teure Schwachstellen (hoher Schweregrad, KBA-Rückrufe), dann mittlere, zuletzt geringe/kosmetische Punkte. Maximal 3–5 wichtigste Punkte, keine erschöpfende Liste. Motorspezifische Punkte nur gemäß Regel oben.

## Preis-Einschätzung
- Kategorie (siehe oben) + Marktspanne, Quelle transparent machen ("laut aktueller Websuche")
- Bei "extrem_guenstig": IMMER kurz erklären, wieso ein ungewöhnlich niedriger Preis oft auf Probleme hindeutet (z.B. Unfall-/Totalschaden-Vorgeschichte, fehlende Fahrzeugpapiere/Servicenachweis, Zahlungsdruck, Betrugsversuch wie Vorkasse ohne Besichtigung) — sachlich, keine Anschuldigung gegen den konkreten Verkäufer.
- Falls kein Web: ehrlich kommunizieren
- marktpreis_min und marktpreis_max als Integer-Zahlen befüllen (nur wenn aus Web ableitbar)

## Inserat im Vergleich
Tabelle mit mindestens 6 Zeilen:
| Kriterium | Inserat-Angabe | DB-/Markterwartung | Plausibilität |
Plausibilität — vier Stufen, NICHT vermischen:
  - ✓ Plausibel: passt zur DB-/Markterwartung.
  - ✏️ Vermutlich Tippfehler: Wert weicht minimal/erkennbar von einem naheliegenden korrekten Wert ab.
  - ⚠ Selten (aber möglich): ungewöhnlich, kommt aber real vor. NICHT als unplausibel werten.
  - ❌ Unmöglich: technisch ausgeschlossen.
Mindest-Kriterien: Baujahr, Kilometerstand, Motor/Leistung, Kraftstoff, Preis, Getriebe (falls bekannt).

## Besichtigungs-Checkliste
Markdown-Checkboxen, priorisiert: kritische Prüfpunkte (die im schlimmsten Fall den Kauf verhindern sollten) ZUERST, allgemeine Hinweise (Kosmetik, übliche Verschleißteile) DANACH.

REGELN:
1. Erfinde keine Zahlen — Specs nur aus DB-Kontext verwenden.
2. Kennzeichne Web-Preise transparent als Websuche-Ergebnis, ohne interne Begriffe wie "ungeprüft" oder "Vertrauen" im Text zu verwenden — das sind Entwicklerbegriffe, keine Nutzersprache.
3. Sei direkt, sachlich und neutral — keine leeren Phrasen, kein Hype, keine Marketing-Sprache. Begründungen immer technisch (Motor, Verschleiß, Marktdaten), nie emotional/werblich.
4. Schreibe ausschließlich auf Deutsch, kompakt — keine Wiederholung derselben Information in mehreren Abschnitten.
5. Das JSON-Feld "bericht" darf Zeilenumbrüche (\\n) enthalten.
6. Kein Floskel-Text vor oder nach der geforderten Struktur (kein "Gerne, hier ist die Analyse", kein "Ich hoffe, das hilft"). Der Bericht beginnt direkt mit "## Fahrzeug erkannt" und endet mit dem letzten inhaltlichen Punkt der Checkliste.\
"""


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
    if req.preis_eur:      lines.append(f"Preis:          {req.preis_eur:,} €".replace(",", "."))
    if req.ausstattung:    lines.append(f"Ausstattung:    {', '.join(req.ausstattung)}")
    if req.beschreibung:   lines.append(f"Beschreibung:   {req.beschreibung}")
    if req.unfallfrei:     lines.append(f"Unfallfrei:     {req.unfallfrei}")
    if req.vorbesitzer is not None: lines.append(f"Vorbesitzer:    {req.vorbesitzer}")
    if req.tuev_bis:       lines.append(f"TÜV bis:        {req.tuev_bis}")
    if req.scheckheftgepflegt is not None:
        lines.append(f"Scheckheftgepflegt: {'ja' if req.scheckheftgepflegt else 'nein'}")
    return "\n".join(lines)


async def run_kaufcheck(req: KaufCheckRequest, retry: bool = False) -> dict:
    """`retry` (§22/§33): True, wenn dies ein "Erneut versuchen" nach research_failed
    ist — erzwingt frische Tavily-Calls statt einer identischen gecachten Antwort."""
    # 1. Baureihe erkennen (DB, blockierend) UND Marktpreis per Tavily (Netzwerk) laufen
    #    PARALLEL — die Tavily-Queries hängen nur an den Inserat-Rohdaten (req.*), nicht
    #    am Ergebnis der Baureihe-Erkennung, sind also unabhängig voneinander.
    baureihe_task = asyncio.to_thread(find_baureihe_mit_vertrauen, req.marke, req.modell, req.baujahr)

    web_results_task: asyncio.Task[list[dict]] | None = None
    if TAVILY_API_KEY and req.marke and req.modell:
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
    motor_markt = find_motor(baureihe_markt, req.motor) if baureihe_markt else None
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
    fahrzeugkontext = build_fahrzeugkontext(baureihe)
    db_ctx = build_db_context(baureihe, motor_match, req.baujahr,
                              fahrzeugkontext=fahrzeugkontext)

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
    identity = VehicleIdentity.from_market_context(baureihe_markt, motor_markt, req)
    if TAVILY_API_KEY and req.marke and req.modell:
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
    web_relevant = [r for r in web_results_roh if modell_relevant(r, ziel)]
    web_results = curate_results(web_relevant, kategorie=KATEGORIE_MARKTPREISE, max_results=_MAX_KAUFCHECK_QUELLEN)
    web_ctx = results_to_context(web_results)
    belege  = results_to_belege(web_results)

    # 4. Gemini-Analyse
    motor_status = (
        f"MOTOR-STATUS: erkannt ({motor_match['bezeichnung']})" if motor_match
        else "MOTOR-STATUS: nicht erkannt — Inserat nennt keine eindeutige Motorisierung"
    )
    # Phase 1 Schicht B: Evidence deterministisch VOR dem LLM bauen (Marktvergleich
    # 2.0 ist jetzt bereits vor dem LLM berechnet) und dem LLM kompakt zum
    # Referenzieren mitgeben. Die IDs sind stabil, sodass die vom LLM referenzierten
    # IDs anschließend gegen genau diese Insights validiert werden können.
    insights = build_insights(baureihe, motor_match, belege, req, check_typ="kauf",
                              marktanalyse=marktanalyse, web_recherche=web_recherche)
    evidence_block = format_evidence_for_prompt(insights)
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
    user_msg = "\n\n".join(filter(None, [_format_inserat(req), motor_status, db_ctx, web_ctx,
                                         laufleistung_block, markt_block,
                                         preis_block, evidence_block]))
    # Absichtlich KEIN try/except um Gemini-Totalausfälle (RateLimitExhausted,
    # GeminiVoruebergehendNichtErreichbar) — die propagieren bis zum Router
    # (routers/kaufcheck.py), der einheitlich das Check-Kontingent zurückerstattet
    # und eine saubere Fehlermeldung zeigt, statt hier einen wertlosen "unbekannt"-
    # Bericht als scheinbaren Erfolg (200 OK) zurückzugeben.
    result = await call_gemini_json(_SYSTEM, user_msg)
    if result.get("bericht"):
        result["bericht"] = postprocess_answer(result["bericht"])
        # §26 defensiv: auch der Kaufcheck-Bericht kann einen Wiederverkaufs-Ausblick
        # enthalten — dieselbe Absicherung wie im Verkaufscheck.
        result["bericht"] = entferne_erfundene_verkaufsdauer(result["bericht"])
        # P2-5-Nachbesserung (Bake-off Gemini 3.7): unabhaengig vom Empfehlungs-Floor
        # geltendes Sicherheitsnetz — kein Feld im System kennt den Zeitpunkt des
        # letzten Service, ein "faellig"/"ueberfaellig"/"versaeumt" ist deshalb IMMER
        # unbelegt, egal welches Modell den Bericht geschrieben hat.
        result["bericht"] = neutralisiere_wartungs_faelligkeit(result["bericht"])
        # P1-b (KaufCheck-Backend-Freeze): PFAD B (kein belastbarer Markt) verbietet
        # dem Modell im Prompt bereits jedes Preisurteil (no_market_prompt_block) —
        # dieser Guard ist das Sicherheitsnetz NACH dem Call. NUR im No-Market-Pfad
        # aufgerufen: bei belastbarer Markt-Evidence (PFAD A) bleibt das echte,
        # kanonische Preisurteil unberuehrt.
        if not markt_verfuegbar:
            result["bericht"] = neutralisiere_no_market_preisurteil(result["bericht"])
        # §Phase 8: letztes Sicherheitsnetz — auch wenn db_ctx/evidence_block bereits
        # gefiltert waren (§Phase 7), kann das LLM Begriffe frei kombinieren
        # (z.B. aus dem Schwachstellen-/DB-Profil-Text). Entfernt NUR Sätze/Zeilen,
        # die eindeutig einem für dieses Fahrzeug ausgeschlossenen Rückruf zuordenbar
        # sind (z.B. Hochvolt-Rückruf bei erkanntem Diesel).
        if baureihe and baureihe.get("rueckrufe"):
            # KBA-Trust-Gate: `marke` mitgeben, damit dieselbe Applicability-
            # Formulierung entsteht wie im Prompt oben (build_db_context) — sonst
            # könnte der Bericht-Validator gegen eine andere Wortwahl prüfen als das
            # LLM tatsächlich gesehen hat.
            _ausgeschlossen = ausgeschlossene_rueckrufe(baureihe["rueckrufe"], motor_match,
                                                        req.baujahr, marke=baureihe.get("marke"))
            if _ausgeschlossen:
                _erlaubt = gefilterte_rueckrufe(baureihe["rueckrufe"], motor_match, req.baujahr,
                                                marke=baureihe.get("marke"))
                result["bericht"], _ = pruefe_bericht(result["bericht"], _ausgeschlossen, _erlaubt)

    # Sicherheitsnetz gegen Modell-Inkonsistenz: Gemini liefert gelegentlich einen
    # vollständigen Bericht mit klarer Kaufempfehlung/Preiseinschätzung im Fließtext,
    # setzt die STRUKTURIERTEN Felder aber trotzdem auf "unbekannt" (kein Parse-Fehler —
    # das JSON war syntaktisch gültig, nur inhaltlich inkonsistent zum eigenen Bericht).
    # Bei einem erkennbar vollständigen Bericht (> 200 Zeichen, enthält "Kaufempfehlung")
    # wird dann per Regex aus dem Bericht selbst nachgezogen statt "unbekannt" stehen
    # zu lassen.
    bericht_text = result.get("bericht", "")
    ist_voller_bericht = len(bericht_text) > 200 and "kaufempfehlung" in bericht_text.lower()
    if ist_voller_bericht:
        nachtrag = _notfall_extraktion(bericht_text)
        if result.get("empfehlung") in (None, "", "unbekannt"):
            result["empfehlung"] = nachtrag.get("empfehlung", result.get("empfehlung", "unbekannt"))
        # P0-1: Die Preis-Rekonstruktion liest per Regex Kategorie UND Spanne aus dem
        # BERICHTSTEXT zurueck. Im No-Market-Pfad waere genau das ein Einfallstor:
        # haelt sich das Modell nicht an den No-Market-Block und schreibt doch eine
        # Spanne in den Fliesstext, wuerde sie hier in die strukturierten Felder
        # gehoben und damit zur offiziellen VIRA-Aussage. Ohne belastbaren Markt
        # bleiben diese Felder deshalb unantastbar leer — die Empfehlungs-
        # Rekonstruktion oben (rein technisch) bleibt davon unberuehrt.
        if markt_verfuegbar:
            if result.get("preis_bewertung") in (None, "", "unbekannt"):
                result["preis_bewertung"] = nachtrag.get("preis_bewertung", result.get("preis_bewertung", "unbekannt"))
            if result.get("marktpreis_min") is None:
                result["marktpreis_min"] = nachtrag.get("marktpreis_min")
            if result.get("marktpreis_max") is None:
                result["marktpreis_max"] = nachtrag.get("marktpreis_max")

    hat_db, hat_web = baureihe is not None, bool(web_results)
    if hat_db and hat_web:   quelle, vertrauen = "gemischt", "mittel"
    elif hat_db:             quelle, vertrauen = "datenbank", "hoch"
    elif hat_web:            quelle, vertrauen = "web", "niedrig"
    else:                    quelle, vertrauen = "gemischt", "niedrig"

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
    if floor_befund is not None:
        for _fid in floor_befund.evidence_ids:
            empfehlung_evidence_ids = ergaenze_id(empfehlung_evidence_ids, _fid)

    # Phase 2: Kern-Erkenntnisse deterministisch aus den bereits vorhandenen Daten
    # verdichten (Marktanalyse in insights, Rückruf-Applicability, Schwachstellen,
    # Inserat-Widersprüche) — kein weiteres LLM, referenziert nur echte Insight-IDs.
    key_findings = build_key_findings_kauf(req, baureihe, motor_match, insights,
                                           price_assessment, identitaet=identitaet)

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

    return {
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
    }
