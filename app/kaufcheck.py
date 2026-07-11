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
  "marktpreis_max": <integer EUR oder null>
}

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
                [q_spezifisch, q_mittel, q_breit], count=5,
                exclude_domains=US_QUELLEN_AUSSCHLUSS,
            )
        )

    baureihe    = await baureihe_task
    motor_match = find_motor(baureihe, req.motor) if baureihe else None

    # 2. DB-Kontext
    db_ctx = build_db_context(baureihe, motor_match)

    web_results: list[dict] = await web_results_task if web_results_task else []
    # Quellenqualität: Marktplätze (mobile.de/AutoScout24/AutoUncle) bevorzugt,
    # Social Media/Duplikate raus, auf so viele Quellen wie nötig begrenzt.
    web_results = curate_results(web_results, kategorie=KATEGORIE_MARKTPREISE, max_results=_MAX_KAUFCHECK_QUELLEN)
    web_ctx = results_to_context(web_results)
    belege  = results_to_belege(web_results)

    # 4. Gemini-Analyse
    motor_status = (
        f"MOTOR-STATUS: erkannt ({motor_match['bezeichnung']})" if motor_match
        else "MOTOR-STATUS: nicht erkannt — Inserat nennt keine eindeutige Motorisierung"
    )
    user_msg = "\n\n".join(filter(None, [_format_inserat(req), motor_status, db_ctx, web_ctx]))
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

    # Sicherheitsnetz gegen Schema-Abweichung: Gemini driftet trotz fest
    # vorgegebenem Enum (siehe _SYSTEM oben) gelegentlich zu naheliegenden,
    # nicht im Schema stehenden Synonymen (z.B. "guter_deal" statt "guenstig").
    # Ohne diese Normalisierung landet der rohe Snake-Case-Wert unübersetzt im
    # Frontend, statt als lesbarer Text angezeigt zu werden.
    preis_wert = _PREIS_BEWERTUNG_SYNONYME.get(
        result.get("preis_bewertung", "unbekannt"), result.get("preis_bewertung", "unbekannt")
    )

    return {
        "bericht":          result.get("bericht", ""),
        "empfehlung":       result.get("empfehlung", "unbekannt"),
        "preis_bewertung":  preis_wert,
        "marktpreis_min":   result.get("marktpreis_min"),
        "marktpreis_max":   result.get("marktpreis_max"),
        "baureihe_erkannt": baureihe["id"] if baureihe else None,
        "motor_erkannt":    motor_match["variante_id"] if motor_match else None,
        "quelle":           quelle,
        "vertrauen":        vertrauen,
        "belege":           belege,
    }
