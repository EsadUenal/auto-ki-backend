from __future__ import annotations

"""
Kauf-Check: Inserat-Analyse mit DB-Abgleich und Marktpreisbewertung.

Ablauf:
  1. Baureihe + Motorvariante in SQLite erkennen
  2. DB-Kontext aufbauen (Schwachstellen, Rückrufe, Motorspecs)
  3. Marktpreis per Tavily ermitteln
  4. Gemini (JSON-Modus) liefert strukturierten Bericht
"""

import asyncio
import logging

from app.car_lookup import find_baureihe, find_motor, build_db_context, call_gemini_json, _notfall_extraktion
from app.config import TAVILY_API_KEY
from app.database import get_alle_baureihen_kurz, get_alle_motorvarianten_kurz
from app.evidence import (
    build_insights, format_evidence_for_prompt, filter_evidence_ids,
    valid_evidence_ids, enrich_marktvergleich_spanne, marktvergleich_id, ergaenze_id,
)
from app.marktvergleich import analysiere_markt, baue_ziel, modell_relevant, prompt_block as markt_prompt_block
from app.marktrecherche import (
    vertiefe_marktrecherche, baue_deep_queries, research_status, RechercheUnzureichend,
    NACHRICHT_UNZUREICHEND,
)
from app.preisurteil import bewerte_preis, preis_bewertung_aus_verdict, prompt_block as preis_prompt_block
from app.key_findings import build_key_findings_kauf
from app.models import KaufCheckRequest
from app.postprocess import postprocess_answer
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


async def run_kaufcheck(req: KaufCheckRequest) -> dict:
    # 1. Baureihe erkennen (DB, blockierend) UND Marktpreis per Tavily (Netzwerk) laufen
    #    PARALLEL — die Tavily-Queries hängen nur an den Inserat-Rohdaten (req.*), nicht
    #    am Ergebnis der Baureihe-Erkennung, sind also unabhängig voneinander.
    baureihe_task = asyncio.to_thread(find_baureihe, req.marke, req.modell, req.baujahr)

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

    baureihe    = await baureihe_task
    motor_match = find_motor(baureihe, req.motor) if baureihe else None

    # 2. DB-Kontext
    db_ctx = build_db_context(baureihe, motor_match)

    web_results_roh: list[dict] = await web_results_task if web_results_task else []

    # Marktvergleich 2.0 + adaptive Recherche: Ziel-Profil (harte Modelltreue) bauen,
    # dann die Recherche adaptiv VERTIEFEN, bis genug akzeptierte, modelltreue
    # Vergleiche vorliegen (nicht anhand roher Trefferzahl aufhören — #4/#7).
    ziel = baue_ziel(baureihe, motor_match, req,
                     get_alle_baureihen_kurz() if baureihe else [],
                     get_alle_motorvarianten_kurz() if baureihe else [])
    # Adaptive, qualitäts-gesteuerte Recherche auch OHNE erkannte Baureihe, sofern
    # Marke+Modell vorliegen (§0: populäre, aber DB-unbekannte Fahrzeuge sollen die
    # Qualitätsschwelle trotzdem erreichen können).
    if TAVILY_API_KEY and req.marke and req.modell:
        deep_queries = baue_deep_queries(req, baureihe.get("generation") if baureihe else None)
        web_results_roh, marktanalyse, _diag = await vertiefe_marktrecherche(
            web_results_roh, deep_queries, ziel, req.preis_eur, US_QUELLEN_AUSSCHLUSS,
            count=10, zweck="kaufcheck-markt")
    else:
        marktanalyse = analysiere_markt(web_results_roh, ziel, req.preis_eur)

    # ── Quality-Gate (§0/§1/§4) ──────────────────────────────────────────────────
    # Reicht die Datenbasis nach voller Vertiefung nicht für einen belastbaren
    # Marktwert (kein Median / Qualität bliebe "niedrig"), wird KEIN fertiger Bericht
    # erzeugt und der teure LLM-Call gespart: research_failed -> Router erstattet das
    # Kontingent zurück. Eine niedrige Datenqualität ist kein auslieferbares Ergebnis.
    status = research_status(marktanalyse)
    if status == "research_failed":
        raise RechercheUnzureichend(marktanalyse, NACHRICHT_UNZUREICHEND)

    # Kanonisches, deterministisches Preisurteil (§6/§7/§13) — EINE Quelle der Wahrheit.
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
                              marktanalyse=marktanalyse)
    evidence_block = format_evidence_for_prompt(insights)
    markt_block = markt_prompt_block(marktanalyse)
    preis_block = preis_prompt_block(price_assessment)
    user_msg = "\n\n".join(filter(None, [_format_inserat(req), motor_status, db_ctx, web_ctx,
                                         markt_block, preis_block, evidence_block]))
    # Absichtlich KEIN try/except um Gemini-Totalausfälle (RateLimitExhausted,
    # GeminiVoruebergehendNichtErreichbar) — die propagieren bis zum Router
    # (routers/kaufcheck.py), der einheitlich das Check-Kontingent zurückerstattet
    # und eine saubere Fehlermeldung zeigt, statt hier einen wertlosen "unbekannt"-
    # Bericht als scheinbaren Erfolg (200 OK) zurückzugeben.
    result = await call_gemini_json(_SYSTEM, user_msg)
    if result.get("bericht"):
        result["bericht"] = postprocess_answer(result["bericht"])

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
    # dann durch die berechnete ersetzt. Ohne belastbare Analyse bleibt die LLM-
    # Spanne und wird nur im Insight nachgetragen.
    if marktanalyse and marktanalyse.median_eur:
        result["marktpreis_min"] = marktanalyse.spanne_min_eur
        result["marktpreis_max"] = marktanalyse.spanne_max_eur
    else:
        enrich_marktvergleich_spanne(insights, result.get("marktpreis_min"), result.get("marktpreis_max"))

    # Schicht B: vom LLM gelieferte Evidence-IDs gegen die ECHTEN Insight-IDs
    # validieren — Halluzinationen verwerfen (Backend bleibt Source of Truth).
    gueltige = valid_evidence_ids(insights)
    empfehlung_evidence_ids = filter_evidence_ids(result.get("empfehlung_evidence_ids"), gueltige, feld="empfehlung")
    preis_evidence_ids      = filter_evidence_ids(result.get("preis_evidence_ids"), gueltige, feld="preis")
    risiko_evidence_ids     = filter_evidence_ids(result.get("risiko_evidence_ids"), gueltige, feld="risiko")
    # Der Marktvergleich ist die Grundlage der Preisbewertung -> immer unter
    # "Warum diese Preisbewertung?" zeigen, auch wenn das LLM ihn nicht referenziert.
    preis_evidence_ids = ergaenze_id(preis_evidence_ids, marktvergleich_id(insights))

    # Phase 2: Kern-Erkenntnisse deterministisch aus den bereits vorhandenen Daten
    # verdichten (Marktanalyse in insights, Rückruf-Applicability, Schwachstellen,
    # Inserat-Widersprüche) — kein weiteres LLM, referenziert nur echte Insight-IDs.
    key_findings = build_key_findings_kauf(req, baureihe, motor_match, insights, price_assessment)

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
    }
