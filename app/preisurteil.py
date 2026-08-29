from __future__ import annotations

"""
Kanonisches Preisurteil (Reliability-Sprint §6/§7/§13).

Ein VIRA-Check hatte gleichzeitig widersprüchliche Preisaussagen ("Marktgerecht"
UND "Deutlich über Marktpreis" UND "21,5 % über Median" UND "innerhalb der
Marktspanne"). Ursache: mehrere Stellen (LLM-Bericht, strukturiertes Feld, Key
Findings) bewerteten dieselben Zahlen JEWEILS EIGENSTÄNDIG.

Dieses Modul erzeugt aus der DETERMINISTISCHEN Marktanalyse (Median + robuste
Quartilsspanne) GENAU EIN Preisurteil (`PriceAssessment`). Alle Ausgabebereiche
verwenden dieses Objekt — es gibt keine zweite Bewertung.

Kernregel (§7): Medianabweichung UND Lage in der Spanne werden GEMEINSAM
berücksichtigt. Innerhalb einer sehr breiten Spanne zu liegen macht ein Fahrzeug
NICHT automatisch marktgerecht — liegt der Preis deutlich über dem Median (aber
noch in der Spanne), ist es "oberes Marktsegment" mit Nachverhandlungsempfehlung.
"""

import logging

from app.models import Marktanalyse, PriceAssessment

log = logging.getLogger(__name__)

# Schwellen der Medianabweichung (Prozent). Bewusst zentral & benannt.
_PCT_DEUTLICH = 20.0   # >= 20 % Abweichung = "deutlich"
_PCT_SPUERBAR = 8.0    # >= 8 %  Abweichung = spürbar (nicht mehr "nah am Median")

_LABEL = {
    "deutlich_unter":  "Deutlich unter Marktpreis",
    "unter":           "Unter Marktpreis",
    "marktgerecht":    "Marktgerecht",
    "oberes_segment":  "Oberes Marktsegment",
    "ueber":           "Über Marktpreis",
    "deutlich_ueber":  "Deutlich über Marktpreis",
    "unbekannt":       "Preis nicht bewertbar",
}


def _position_in_range(preis: int, lo: int | None, hi: int | None) -> str:
    """Wo liegt der Preis relativ zur robusten Quartilsspanne [lo, hi]?"""
    if lo is None or hi is None:
        return "unbekannt"
    if preis < lo:
        return "unter_spanne"
    if preis > hi:
        return "ueber_spanne"
    if hi == lo:
        return "mittig"
    rel = (preis - lo) / (hi - lo)
    if rel <= 1 / 3:
        return "unteres_drittel"
    if rel >= 2 / 3:
        return "oberes_drittel"
    return "mittig"


def _verdict(pct: float, position: str) -> str:
    """Kombiniert Medianabweichung (pct) UND Lage in der Spanne (position) zu einem
    von sechs Verdikten. Reihenfolge der Prüfungen ist bewusst: Lage AUSSERHALB der
    Spanne dominiert; innerhalb der Spanne entscheidet die Medianabweichung."""
    # Außerhalb der typischen Spanne -> klares Über/Unter-Markt-Urteil.
    if position == "ueber_spanne":
        return "deutlich_ueber" if pct >= _PCT_DEUTLICH else "ueber"
    if position == "unter_spanne":
        return "deutlich_unter" if pct <= -_PCT_DEUTLICH else "unter"

    # Innerhalb der Spanne: Medianabweichung entscheidet.
    if pct <= -_PCT_DEUTLICH:
        return "deutlich_unter"
    if pct <= -_PCT_SPUERBAR:
        return "unter"
    if pct >= _PCT_SPUERBAR:
        # Deutlich über dem Median, aber noch in der Spanne -> oberes Marktsegment
        # (NICHT "marktgerecht" — das war der zentrale Widerspruch, §6).
        return "oberes_segment"
    return "marktgerecht"


def _recommendation(verdict: str, check_typ: str) -> str:
    """Deterministische Handlungsempfehlung je Verdikt und Check-Typ."""
    kauf = check_typ != "verkauf"
    if verdict == "deutlich_unter":
        return ("Auffällig günstig — Fahrzeughistorie, Papiere und Zustand vor Zahlung besonders "
                "sorgfältig prüfen." if kauf else
                "Sehr niedrig angesetzt — deutlicher Spielraum nach oben.")
    if verdict == "unter":
        return ("Günstiges Angebot — Zustand und Historie prüfen." if kauf else
                "Unter dem Markt angesetzt — höherer Startpreis ist realistisch.")
    if verdict == "marktgerecht":
        return ("Preis liegt im markttypischen Bereich." if kauf else
                "Realistisch angesetzt — nah am Markt.")
    if verdict == "oberes_segment":
        return ("Am oberen Ende des Marktes — Nachverhandlung empfohlen." if kauf else
                "Ambitioniert am oberen Marktrand — nur mit belegbaren Vorteilen haltbar, sonst "
                "verlängert es die Verkaufszeit.")
    if verdict == "ueber":
        return ("Über dem Marktniveau — Preis nachverhandeln oder Alternativen prüfen." if kauf else
                "Über dem Markt — für einen zügigen Verkauf senken.")
    if verdict == "deutlich_ueber":
        return ("Deutlich über dem Markt — Nachverhandlung dringend empfohlen." if kauf else
                "Deutlich über dem Markt — so kaum verkäuflich, klar senken.")
    return ""


def _begruendung(verdict: str, pct: float, position: str) -> str:
    """Ein Satz, der Median-Lage UND Spannen-Lage konsistent zusammenführt (§6)."""
    abw = f"{abs(pct):.1f}".replace(".", ",")
    if verdict in ("deutlich_unter", "unter"):
        lage = "innerhalb der Marktspanne" if position not in ("unter_spanne",) else "unter der Marktspanne"
        return f"Rund {abw} % unter dem Median, {lage}."
    if verdict == "marktgerecht":
        return f"Nur {abw} % vom Median entfernt, mittig in der Marktspanne."
    if verdict == "oberes_segment":
        return (f"Innerhalb der Marktspanne, aber rund {abw} % über dem Median und am oberen "
                f"Ende des Marktes.")
    if verdict in ("ueber", "deutlich_ueber"):
        return f"Rund {abw} % über dem Median und oberhalb der typischen Marktspanne."
    return ""


def bewerte_preis(marktanalyse: Marktanalyse | None, angebot_eur: int | None,
                  *, check_typ: str = "kauf") -> PriceAssessment:
    """Erzeugt das kanonische Preisurteil aus der deterministischen Marktanalyse.

    Ohne belastbaren Median (oder ohne Angebotspreis) bleibt das Urteil "unbekannt" —
    keine Scheinbewertung. Da die Quality-Gate-Pipeline einen fertigen Check ohnehin
    nur mit belastbarem Median ausliefert, ist "unbekannt" im Normalfall nicht mehr
    Teil eines abgeschlossenen bezahlten Ergebnisses.
    """
    if not marktanalyse or not marktanalyse.median_eur:
        return PriceAssessment(verdict="unbekannt", label=_LABEL["unbekannt"],
                               confidence=(marktanalyse.datenqualitaet if marktanalyse else "niedrig"))
    m = marktanalyse
    median = m.median_eur
    lo, hi = m.spanne_min_eur, m.spanne_max_eur

    if not angebot_eur:
        # Kein Angebotspreis genannt (z.B. Verkaufscheck ohne Preisvorstellung) —
        # Median/Spanne stehen fest, aber es gibt keine Positionsbewertung.
        return PriceAssessment(
            verdict="unbekannt", label="Marktwert ermittelt (kein Angebotspreis)",
            median_eur=median, lower_bound_eur=lo, upper_bound_eur=hi,
            confidence=m.datenqualitaet,
            recommendation=("Vergleiche deinen Zielpreis mit dem Median."
                            if check_typ == "verkauf" else ""),
            begruendung=f"Marktmedian vergleichbarer Fahrzeuge: {median} €.",
        )

    diff = angebot_eur - median
    pct = round(diff / median * 100, 1)
    position = _position_in_range(angebot_eur, lo, hi)
    verdict = _verdict(pct, position)
    return PriceAssessment(
        verdict=verdict,
        label=_LABEL[verdict],
        median_eur=median,
        lower_bound_eur=lo,
        upper_bound_eur=hi,
        difference_eur=diff,
        difference_percent=pct,
        position_in_range=position,
        confidence=m.datenqualitaet,
        recommendation=_recommendation(verdict, check_typ),
        begruendung=_begruendung(verdict, pct, position),
    )


# ── Abbildung auf das bestehende 5-stufige preis_bewertung-Enum (Kaufcheck) ──────
# Das Frontend-Badge liest weiterhin `preis_bewertung`. Damit es NIE dem kanonischen
# Urteil widerspricht, wird es aus dem Verdikt abgeleitet (nicht mehr vom LLM).
_VERDICT_ZU_PREIS_BEWERTUNG = {
    "deutlich_unter": "extrem_guenstig",
    "unter":          "guenstig",
    "marktgerecht":   "marktgerecht",
    "oberes_segment": "teuer",       # oberes Ende der Spanne -> nachverhandeln
    "ueber":          "teuer",
    "deutlich_ueber": "extrem_teuer",
    "unbekannt":      "unbekannt",
}


def preis_bewertung_aus_verdict(verdict: str) -> str:
    return _VERDICT_ZU_PREIS_BEWERTUNG.get(verdict, "unbekannt")


# ── Deterministische Verkaufs-Preisstrategie (§10/§11/§13) ──────────────────────
# Verkaufszeiten NUR als Kategorie — keine scheinpräzisen Tageszahlen ohne echte
# historische Standzeitdaten (§11).
KAT_SCHNELL = "voraussichtlich schneller"
KAT_DURCHSCHNITT = "durchschnittliche Vermarktungsdauer"
KAT_LAENGER = "wahrscheinlich längere Vermarktung"


def verkaufs_strategie(marktanalyse: Marktanalyse | None) -> dict | None:
    """Preisstrategie AUSSCHLIESSLICH aus dem validierten Marktvergleich (§10/§13):

      Schnellverkauf = untere Quartilsgrenze (zügiger Verkauf)
      Empfohlener Preis = Median
      Maximalpreis = obere Quartilsgrenze (oberes realistisches Ende)

    Keine vom LLM frei erzeugten Preise oder Verkaufszeiten. Ohne belastbaren Median
    (kommt nach dem Quality-Gate im finalen Check nicht mehr vor) -> None.
    """
    if not marktanalyse or not marktanalyse.median_eur:
        return None
    m = marktanalyse
    median = m.median_eur
    lo = m.spanne_min_eur if m.spanne_min_eur else median
    hi = m.spanne_max_eur if m.spanne_max_eur else median
    # Monotonie erzwingen: schnell <= empfohlen <= maximal.
    schnell = min(lo, median)
    maximal = max(hi, median)
    return {
        "schnellverkaufs_preis": schnell,
        "empfohlener_preis": median,
        "maximal_preis": maximal,
        "verkaufsdauer_schnell": KAT_SCHNELL,
        "verkaufsdauer_empfohlen": KAT_DURCHSCHNITT,
        "verkaufsdauer_maximal": KAT_LAENGER,
    }


def verkaufs_prompt_block(strategie: dict | None) -> str:
    """VERBINDLICHER Preisstrategie-Block für den Verkaufscheck-Prompt: exakt diese
    Preise und Vermarktungs-Kategorien in der Berichtstabelle verwenden (§10/§11/§13)."""
    if not strategie:
        return ""
    s = strategie
    return "\n".join([
        "=== DETERMINISTISCHE PREISSTRATEGIE (Backend-berechnet — VERBINDLICH) ===",
        "In der Preistabelle GENAU diese Werte und KEINE anderen Zahlen verwenden:",
        f"- Schnellverkauf: {s['schnellverkaufs_preis']} € — Vermarktung: {s['verkaufsdauer_schnell']}",
        f"- Empfohlener Preis: {s['empfohlener_preis']} € — Vermarktung: {s['verkaufsdauer_empfohlen']}",
        f"- Maximalpreis: {s['maximal_preis']} € — Vermarktung: {s['verkaufsdauer_maximal']}",
        "Verkaufszeiten NUR als diese Kategorien angeben — ERFINDE KEINE Tages-/Wochen-"
        "Zahlen. Erzeuge KEINE eigenen Preise und keinen abweichenden Median.",
    ])


def verkaufs_no_market_prompt_block() -> str:
    """VERBINDLICHER Block für den VerkaufsCheck, wenn KEINE belastbare
    Marktdatenbasis vorliegt (P1 #2).

    Gegenstück zu `verkaufs_prompt_block`: dort gibt das Backend dem Modell die
    deterministische Preisstrategie vor — hier sagt es ihm, dass es GAR KEINE
    Preisaussage gibt. Ohne diesen Block würde das Modell die Preisregeln aus dem
    System-Prompt ("leite eine grobe Spanne ab", "## (b) Empfohlene Preisspanne")
    weiter befolgen und aus den Web-Snippets eine Marktspanne konstruieren — genau
    die Halluzination, die der deterministische Marktvergleich verhindern soll.

    Wie beim KaufCheck-Gegenstück (`no_market_prompt_block`) muss das Modell
    ausdrücklich wissen, dass ggf. im Prompt stehende Web-Preise bereits geprüft
    und als nicht belastbar verworfen wurden — sonst wirkt der Widerspruch wie ein
    Kontextfehler und wird "hilfsbereit" überschrieben.
    """
    return "\n".join([
        "=== KEINE BELASTBARE MARKTDATENBASIS (Backend-geprüft — VERBINDLICH) ===",
        "Für dieses Fahrzeug liegen KEINE belastbaren aktuellen Marktpreisdaten vor.",
        "Der deterministische Marktvergleich hat kein verwertbares Ergebnis geliefert.",
        "",
        "Falls im Abschnitt WEB-ERGEBNISSE Preisangaben stehen: diese wurden bereits",
        "geprüft und als NICHT belastbare Vergleichsbasis verworfen (zu wenige",
        "modelltreue Angebote, zu hohe Streuung oder ungeeignete Quelle). Sie sind",
        "KEINE Marktpreisgrundlage.",
        "",
        "Daraus folgt VERBINDLICH:",
        "- Nenne KEINEN Marktpreis, KEINEN Median und KEINE Marktspanne.",
        "- Setze marktpreis_min, marktpreis_max, schnellverkaufs_preis,",
        "  empfohlener_preis und maximal_preis ALLE auf null.",
        "- Nenne KEINE Verkaufsdauer und KEINE Euro-/Prozent-Abweichung zum Markt.",
        "- Stufe eine ggf. genannte Preisvorstellung NICHT ein — weder als",
        "  realistisch, zu hoch, zu niedrig, günstig, marktgerecht noch ambitioniert.",
        "  Auch keine indirekte Andeutung.",
        "- Im Abschnitt '## (a) Marktvergleich' schreibe NUR ein bis zwei sachliche",
        "  Sätze, dass derzeit keine belastbaren Vergleichspreise ermittelt werden",
        "  konnten.",
        "- Lass den Abschnitt '## (b) Empfohlene Preisspanne' samt Preistabelle WEG",
        "  oder schreibe dort NUR, dass die Preisempfehlung mangels Marktdaten offen",
        "  bleibt. KEINE Beträge, KEINE Tabelle mit Zahlen.",
        "",
        "Alles Übrige führst du davon unberührt VOLLSTÄNDIG durch: Fahrzeug erkannt,",
        "wertsteigernde Ausstattung, Zustands- und Mängeltransparenz, Inserats-",
        "Optimierungstipps und die Verkaufsstrategie (Kanäle, Ablauf, Verhandlung) —",
        "nur eben ohne konkrete Preiszahlen.",
    ])


def no_market_prompt_block() -> str:
    """VERBINDLICHER Block für den Fall, dass KEINE belastbare Marktdatenbasis
    vorliegt (KaufCheck-P0-1).

    Gegenstück zu `prompt_block`: dort sagt das Backend dem Modell, welches
    Preisurteil es zu übernehmen hat — hier sagt es ihm, dass es GAR KEINES gibt.
    Ohne diesen Block würde das Modell den Preisteil aus dem System-Prompt
    (`## Preis-Einschätzung`, "leite eine grobe Spanne ab") weiterhin befolgen und
    aus den Web-Snippets eine Marktspanne konstruieren — genau die Halluzination,
    die der deterministische Marktvergleich verhindern soll.

    Wichtig zur Formulierung: die Web-Ergebnisse im Prompt können sehr wohl Preise
    enthalten. Sie wurden geprüft und als nicht belastbar VERWORFEN (zu wenige
    modelltreue Vergleiche, zu hohe Streuung, oder Quelle nicht freigegeben). Das
    Modell muss das ausdrücklich wissen, sonst wirkt der Widerspruch
    ("hier stehen Preise, aber es gibt keine Marktdaten") wie ein Fehler im
    Kontext und wird "hilfsbereit" überschrieben.
    """
    return "\n".join([
        "=== KEINE BELASTBARE MARKTDATENBASIS (Backend-geprüft — VERBINDLICH) ===",
        "Für dieses Fahrzeug liegen KEINE belastbaren aktuellen Marktpreisdaten vor.",
        "Der deterministische Marktvergleich hat kein verwertbares Ergebnis geliefert.",
        "",
        "Falls im Abschnitt WEB-ERGEBNISSE Preisangaben stehen: diese wurden bereits",
        "geprüft und als NICHT belastbare Vergleichsbasis verworfen (zu wenige",
        "modelltreue Angebote, zu hohe Streuung oder ungeeignete Quelle). Sie sind",
        "KEINE Marktpreisgrundlage.",
        "",
        "Daraus folgt VERBINDLICH:",
        "- Nenne KEINEN Marktpreis, KEINEN Median und KEINE Marktspanne.",
        "- Setze marktpreis_min = null und marktpreis_max = null.",
        "- Setze preis_bewertung = \"unbekannt\".",
        "- Stufe den Angebotspreis NICHT ein — weder als günstig, fair, marktgerecht,",
        "  angemessen, teuer noch überteuert. Auch keine indirekte Andeutung",
        "  (\"wirkt attraktiv\", \"erscheint hoch\", \"liegt im Rahmen\").",
        "- Leite KEINE Preisdifferenz, KEINE Prozentangabe zum Markt und KEINE",
        "  Verkaufsdauer daraus ab.",
        "- Schreibe im Abschnitt '## Preis-Einschätzung' NUR: dass für dieses Fahrzeug",
        "  aktuell keine belastbare Marktpreisbasis ermittelt werden konnte und die",
        "  Preisbewertung deshalb offen bleibt. Ein bis zwei Sätze, sachlich.",
        "- In der Tabelle '## Inserat im Vergleich' bleibt die Zeile 'Preis' in der",
        "  Spalte DB-/Markterwartung 'nicht verfügbar'; bewerte die Plausibilität dort",
        "  NICHT anhand des Preises.",
        "",
        "Die TECHNISCHE Kaufberatung führst du davon unberührt VOLLSTÄNDIG durch:",
        "Fahrzeugidentität, Schwachstellen, Motorprobleme, Rückrufe, Risiken,",
        "Besichtigungs-Checkliste und Kaufempfehlung. Die Kaufempfehlung stützt sich",
        "in diesem Fall AUSSCHLIESSLICH auf technische Kriterien und die Plausibilität",
        "des Inserats — nicht auf den Preis. Die Stufe \"preis_nachverhandeln\" ist",
        "hier NICHT zulässig, da sie eine Preisbewertung voraussetzt.",
    ])


def prompt_block(pa: PriceAssessment | None) -> str:
    """VERBINDLICHER Preis-Block für den LLM-Prompt: das Modell soll GENAU dieses
    Urteil im Bericht wiedergeben und es NICHT neu bewerten (§13)."""
    if not pa or pa.verdict == "unbekannt" or not pa.median_eur:
        return ""
    lines = [
        "=== KANONISCHES PREISURTEIL (Backend-berechnet — VERBINDLICH, NICHT NEU BEWERTEN) ===",
        f"- Median-Marktwert: {pa.median_eur} €",
    ]
    if pa.lower_bound_eur and pa.upper_bound_eur:
        lines.append(f"- Typischer Marktbereich: {pa.lower_bound_eur}–{pa.upper_bound_eur} €")
    if pa.difference_eur is not None:
        vz = "+" if pa.difference_eur >= 0 else "−"
        lines.append(f"- Angebotspreis liegt {vz}{abs(pa.difference_eur)} € "
                     f"({vz}{abs(pa.difference_percent):.1f} %) zum Median")
    lines += [
        f"- VERDIKT (verbindlich): {pa.label}",
        f"- Begründung: {pa.begruendung}",
        f"- Empfehlung: {pa.recommendation}",
        "",
        "Im Abschnitt '## Preis-Einschätzung' MUSST du genau dieses Verdikt und diese "
        "Zahlen verwenden. Nenne KEINE abweichende Kategorie, KEINEN abweichenden Median "
        "und KEINE eigene Spanne. Wenn der Preis in der Spanne, aber klar über dem Median "
        "liegt, ist das NICHT 'marktgerecht', sondern 'oberes Marktsegment'.",
    ]
    return "\n".join(lines)
