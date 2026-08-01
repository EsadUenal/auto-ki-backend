from __future__ import annotations

"""
Provenance / Evidence — Phase 1 (Vertrauen & Nachvollziehbarkeit).

Baut strukturierte, NACHVOLLZIEHBARE Erkenntnisse (Insights) aus den Daten, die
Kauf-/Verkaufscheck bereits deterministisch abgefragt haben — NICHT aus dem, was
das LLM behauptet. Grundsatz: Eine Quelle (`datenbank`, `rueckruf_kba`, `web`, …)
wird nur dann angegeben, wenn sie die Aussage tatsächlich gestützt hat.

Was hier NICHT passiert (bewusst, Schicht A):
- Keine LLM-Erkenntnisse (Empfehlung/Preisbewertung) — die sind reine KI-Ableitung
  und werden hier nicht als DB/Web ausgegeben. (Verknüpfung = späterer Schritt.)
- Keine scheinpräzisen Prozentwerte — Confidence ist dreistufig.
"""

import re

from app.models import EvidenceQuelle, Insight

_JAHR = re.compile(r"\b(?:19|20)\d{2}\b")
_BEREICH = re.compile(r"[-–]|bis")
_ALLGEMEIN = {"", "alle", "alle baujahre", "-", "n/a", "unbekannt", "diverse"}


def _jahre(text: str | None) -> list[int]:
    return [int(y) for y in _JAHR.findall(text or "")]


def _baujahr_passt(betroffene: str | None, baujahr: int | None) -> bool | None:
    """Ob `baujahr` in die Baujahr-Angabe fällt.

    True  = fällt eindeutig hinein
    False = fällt eindeutig NICHT hinein (Insight ist für dieses Inserat irrelevant)
    None  = nicht bestimmbar (allgemeine Angabe oder kein Baujahr) -> als bedingt werten
    """
    if betroffene is None:
        return None
    t = betroffene.strip().lower()
    if t in _ALLGEMEIN:
        return None
    if baujahr is None:
        return None
    jahre = _jahre(betroffene)
    if not jahre:
        return None
    if _BEREICH.search(t):
        return min(jahre) <= baujahr <= max(jahre)
    return baujahr in jahre


def _typen(quellen: list[EvidenceQuelle]) -> list[str]:
    """Eindeutige Quellen-Typen in stabiler Reihenfolge."""
    out: list[str] = []
    for q in quellen:
        if q.typ not in out:
            out.append(q.typ)
    return out


def _einfluss_schwachstelle(schweregrad: str | None, check_typ: str) -> str:
    s = (schweregrad or "").strip().lower()
    if check_typ == "verkauf":
        return "Wertmindernd — beim Verkauf offen kommunizieren."
    if s in ("hoch", "kritisch", "sehr hoch"):
        return "Erhöht das technische Kaufrisiko deutlich."
    if s in ("mittel", "moderat"):
        return "Moderates technisches Risiko."
    return "Zu beachtender Schwachpunkt vor dem Kauf."


def build_insights(
    baureihe: dict | None,
    motor_match: dict | None,
    web_belege: list[dict] | None,
    req,
    *,
    check_typ: str = "kauf",
    marktpreis_min: int | None = None,
    marktpreis_max: int | None = None,
) -> list[Insight]:
    """Baut die Liste nachvollziehbarer Insights aus deterministischen Daten.

    `web_belege` ist die fertige Belege-Liste (results_to_belege): dicts mit
    typ/titel/url/snippet/qualitaet. `req` ist der Kauf-/Verkaufscheck-Request.
    """
    insights: list[Insight] = []
    baujahr = getattr(req, "baujahr", None)
    # HINWEIS zu DB-Quellen-URLs (Tabelle `quelle`): diese sind ausschließlich per
    # `baureihe_id` verknüpft — es gibt KEINE Relation zu einer einzelnen
    # Schwachstelle/Rückruf/Motorproblem. Eine allgemeine Baureihen-URL darf daher
    # NICHT als konkreter Beleg für eine spezifische Aussage ausgegeben werden
    # (bloße Zugehörigkeit zur selben Baureihe ist keine Aussage→Quelle-Verknüpfung).
    # Die Herkunft "datenbank" bleibt erhalten, ohne fremde URL als Scheinbeweis.
    zaehler = {"n": 0}

    def _id(prefix: str) -> str:
        zaehler["n"] += 1
        return f"{prefix}-{zaehler['n']}"

    # ── 1) Schwachstellen der Baureihe (VIRA-DB, redaktionell geprüft) ──────────
    for s in (baureihe or {}).get("schwachstellen_baureihe") or []:
        passt = _baujahr_passt(s.get("betroffene_baujahre"), baujahr)
        if passt is False:
            continue  # gilt nachweislich nicht für dieses Baujahr -> nicht ausgeben
        quellen = [EvidenceQuelle(typ="datenbank", ref=s.get("bauteil"),
                                  titel="VIRA-Fahrzeugdatenbank (geprüft)")]
        insights.append(Insight(
            id=_id("schwachstelle"),
            kategorie="schwachstelle",
            titel=f"{s.get('bauteil') or 'Schwachstelle'} — bekannte Schwachstelle",
            beschreibung=(s.get("beschreibung") or "").strip(),
            quellen_typen=_typen(quellen),
            quellen=quellen,
            # confidence NUR aus Provenance (Baujahr-Deckung bei erkannter Baureihe),
            # NIE aus schweregrad.
            confidence="hoch" if passt is True else "mittel",
            schweregrad=(s.get("schweregrad") or None),
            einfluss=_einfluss_schwachstelle(s.get("schweregrad"), check_typ),
        ))

    # ── 2) Rückrufe (KBA-Daten) ────────────────────────────────────────────────
    for r in (baureihe or {}).get("rueckrufe") or []:
        passt = _baujahr_passt(r.get("betroffene_baujahre"), baujahr)
        if passt is False:
            continue
        kba = (r.get("kba_referenz") or "").strip()
        # kba_referenz ist die KONKRETE, pro-Rückruf gültige Quelle -> bleibt am Insight.
        quellen = [EvidenceQuelle(
            typ="rueckruf_kba",
            ref=kba or None,
            titel="KBA-Rückrufdatenbank" if kba else "KBA-Rückruf (Referenz nicht hinterlegt)",
        )]
        beschr = (r.get("mangel") or "").strip()
        if r.get("abhilfe"):
            beschr = f"{beschr} — Abhilfe: {r['abhilfe'].strip()}"
        if r.get("datum"):
            beschr = f"{beschr} (Rückruf {r['datum']})"
        insights.append(Insight(
            id=_id("rueckruf"),
            kategorie="rueckruf",
            titel=f"KBA-Rückruf: {(r.get('mangel') or 'Rückrufaktion')[:80]}".rstrip(": ").strip(),
            beschreibung=beschr.strip(" —"),
            quellen_typen=_typen(quellen),
            quellen=quellen,
            # KBA-Referenz + exakte Baujahr-Deckung = amtlich belastbar -> hoch.
            confidence="hoch" if (passt is True and kba) else "mittel",
            einfluss="Sicherheitsrelevant — Durchführung der Rückrufaktion nachweisen/prüfen.",
        ))

    # ── 3) Motorspezifische Probleme (nur bei ERKANNTEM Motor) ─────────────────
    if motor_match:
        for s in motor_match.get("schwachstellen_motor") or []:
            passt = _baujahr_passt(s.get("baujahre"), baujahr)
            if passt is False:
                continue
            quellen = [EvidenceQuelle(typ="motorvarianten", ref=motor_match.get("bezeichnung"),
                                      titel="VIRA-Motorvariantendaten (geprüft)")]
            kosten = s.get("kosten_ca")
            if check_typ == "verkauf":
                einfluss = "Wertrelevant — Zustand des Bauteils belegen."
            else:
                einfluss = (f"Mögliche Reparaturkosten ca. {kosten} — erhöht das technische Risiko."
                            if kosten else "Erhöht das technische Risiko.")
            insights.append(Insight(
                id=_id("motorproblem"),
                kategorie="motorproblem",
                titel=f"{s.get('bauteil') or 'Motorproblem'} ({motor_match.get('bezeichnung') or 'Motor'})",
                beschreibung=(s.get("beschreibung") or "").strip(),
                quellen_typen=_typen(quellen),
                quellen=quellen,
                confidence="hoch" if passt is True else "mittel",
                einfluss=einfluss,
            ))

    # ── 4) Marktvergleich (Websuche — ungeprüft) ───────────────────────────────
    web_belege = web_belege or []
    web_quellen = [
        EvidenceQuelle(typ="web", url=b.get("url"), titel=b.get("titel"), qualitaet=b.get("qualitaet"))
        for b in web_belege if b.get("url")
    ]
    if web_quellen:
        # Confidence bewusst KONSERVATIV und NICHT allein aus der Trefferzahl:
        # Ob die gefundenen Angebote wirklich vergleichbar sind (Modell/Motorisierung/
        # Baujahr/Kilometerstand/Karosserie/Kraftstoff), lässt sich aus der aktuellen
        # Web-Ergebnisstruktur (nur Titel + gekürzter Snippet) NICHT zuverlässig
        # deterministisch nachweisen. Deshalb: mehrere gute Marktplatzquellen = maximal
        # "mittel"; "hoch" wird NICHT über die Anzahl vergeben (kein LLM, keine
        # Scheinpräzision). "hoch" bliebe echter, verifizierbarer Vergleichbarkeit
        # vorbehalten (nicht Teil von Schicht A).
        marktplatz = sum(1 for b in web_belege if b.get("qualitaet") == "Marktplatz")
        conf = "mittel" if marktplatz >= 2 else "niedrig"
        spanne = ""
        if marktpreis_min or marktpreis_max:
            spanne = f" Ermittelte Marktspanne: {marktpreis_min}–{marktpreis_max} €."
        insights.append(Insight(
            id=_id("marktvergleich"),
            kategorie="marktvergleich",
            titel="Marktvergleich (aktuelle Websuche)",
            beschreibung=(f"Preis-Orientierung aus {len(web_quellen)} Web-Angeboten "
                          f"({marktplatz} Marktplatz-Quellen).{spanne} "
                          f"Web-Daten sind nicht redaktionell geprüft."),
            quellen_typen=_typen(web_quellen) + ["marktvergleich"],
            quellen=web_quellen,
            confidence=conf,
            einfluss="Grundlage der Preisstrategie." if check_typ == "verkauf" else "Grundlage der Preisbewertung.",
        ))

    return insights
