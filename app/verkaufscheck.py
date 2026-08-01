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
from app.evidence import build_insights
from app.models import VerkaufsCheckRequest
from app.postprocess import postprocess_answer
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
  "bericht": "<vollständiger Markdown-Bericht — Struktur unten>"
}

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

| Preis-Kategorie | Betrag (€) | Erwartete Verkaufszeit |
|-----------------|-----------|------------------------|
| Schnellverkauf | XX.XXX | ~X–Y Wochen |
| Empfohlener Preis | XX.XXX | ~X–Y Monate |
| Maximalpreis | XX.XXX | ~X–Y Monate |

Kurze Begründung der Spanne.
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
- Erfinde KEINE präzisen Verkaufszeiten. Gib verkaufsdauer_* nur als grobe Orientierung an und nur, wenn eine belastbare Grundlage (Marktnachfrage aus den Quellen) existiert — sonst grob halten und die Unsicherheit benennen.

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


async def run_verkaufscheck(req: VerkaufsCheckRequest) -> dict:
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
                [q_spezifisch, q_mittel, q_breit], count=5,
                exclude_domains=US_QUELLEN_AUSSCHLUSS,
            )
        )

    baureihe    = await baureihe_task
    motor_match = find_motor(baureihe, req.motor) if baureihe else None

    # 2. DB-Kontext
    db_ctx = build_db_context(baureihe, motor_match)

    web_results: list[dict] = await web_results_task if web_results_task else []
    web_results = curate_results(web_results, kategorie=KATEGORIE_MARKTPREISE, max_results=_MAX_VERKAUFSCHECK_QUELLEN)
    web_ctx = results_to_context(web_results)
    belege  = results_to_belege(web_results)

    # 4. Gemini-Analyse
    # Absichtlich KEIN try/except um Gemini-Totalausfälle (RateLimitExhausted,
    # GeminiVoruebergehendNichtErreichbar) — die propagieren bis zum Router
    # (routers/verkaufscheck.py), der einheitlich das Check-Kontingent
    # zurückerstattet und eine saubere Fehlermeldung zeigt.
    user_msg = "\n\n".join(filter(None, [_format_fahrzeug(req), db_ctx, web_ctx]))
    result = await call_gemini_json(_SYSTEM, user_msg)
    if result.get("bericht"):
        result["bericht"] = postprocess_answer(result["bericht"])
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

    # Phase 1: nachvollziehbare Erkenntnisse (Provenance) aus den bereits
    # abgefragten deterministischen Daten — additiv, ändert nichts am Bericht.
    insights = build_insights(
        baureihe, motor_match, belege, req,
        check_typ="verkauf",
        marktpreis_min=result.get("marktpreis_min"),
        marktpreis_max=result.get("marktpreis_max"),
    )

    return {
        "bericht":                     result.get("bericht", ""),
        "schnellverkaufs_preis":        result.get("schnellverkaufs_preis"),
        "maximal_preis":               result.get("maximal_preis"),
        "empfohlener_preis":           result.get("empfohlener_preis"),
        "verkaufsdauer_tage_schnell":  result.get("verkaufsdauer_tage_schnell"),
        "verkaufsdauer_tage_maximal":  result.get("verkaufsdauer_tage_maximal"),
        "marktpreis_min":              result.get("marktpreis_min"),
        "marktpreis_max":              result.get("marktpreis_max"),
        "baureihe_erkannt":            baureihe["id"] if baureihe else None,
        "motor_erkannt":               motor_match["variante_id"] if motor_match else None,
        "quelle":                      quelle,
        "vertrauen":                   vertrauen,
        "belege":                      belege,
        "insights":                    insights,
    }
