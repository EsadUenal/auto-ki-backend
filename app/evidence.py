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

import logging
import re

from app.models import EvidenceQuelle, Insight, Marktanalyse
# §Phase 7: EINE zentrale Rückruf-Allowed-Liste/Applicability-Logik, geteilt mit
# build_db_context (car_lookup.py), _sql_context (llm.py) und dem Report-Validator
# — nicht mehr lokal in evidence.py dupliziert (siehe app/recall_filter.py).
from app.recall_filter import (
    _baujahr_passt, _jahre,
    rueckruf_applicability as _rueckruf_applicability,
    RUECKRUF_APPLICABILITY_TEXT,
)

log = logging.getLogger(__name__)


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
    marktanalyse: Marktanalyse | None = None,
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
        # Phase 1B: Varianten-/Antriebs-Zuordnung -> applicability (getrennt von
        # confidence & severity). Ein Hochvolt-/PHEV-Rückruf wird NICHT als direkt
        # zutreffend für einen reinen Diesel markiert.
        applicability, r_conf, r_einfluss, variant_hinweis = _rueckruf_applicability(
            r, passt, kba, motor_match)
        # §8/§27: Rückruf betrifft eine eindeutig andere Motorisierung (z.B. Hochvolt-/
        # PHEV-Rückruf bei erkanntem Diesel) -> VOLLSTÄNDIG aus den sichtbaren Findings
        # entfernen (nicht als "unklare Betroffenheit" darstellen, nicht in "Was jetzt?").
        if applicability == "incompatible":
            continue
        beschr = (r.get("mangel") or "").strip()
        if r.get("abhilfe"):
            beschr = f"{beschr} — Abhilfe: {r['abhilfe'].strip()}"
        if r.get("datum"):
            beschr = f"{beschr} (Rückruf {r['datum']})"
        if variant_hinweis:
            beschr = f"{beschr} — {variant_hinweis}"
        # Titel signalisiert nur bei bestbelegter (Nicht-VIN-)Stufe einen konkreten
        # Rückruf; sonst als Baureihen-Hinweis kennzeichnen. NIE "betrifft dein
        # Fahrzeug" ohne VIN-Prüfung (§27) — das steht nur im Frontend-Label, hier
        # geht es nur um die Titel-Formulierung "Rückruf" vs. "Rückruf (Baureihe)".
        if applicability in ("confirmed_by_vin", "variant_match"):
            titel = f"KBA-Rückruf: {(r.get('mangel') or 'Rückrufaktion')[:80]}".rstrip(": ").strip()
        else:
            titel = f"KBA-Rückruf (Baureihe): {(r.get('mangel') or 'Rückrufaktion')[:70]}".rstrip(": ").strip()
        insights.append(Insight(
            id=_id("rueckruf"),
            kategorie="rueckruf",
            titel=titel,
            beschreibung=beschr.strip(" —"),
            quellen_typen=_typen(quellen),
            quellen=quellen,
            confidence=r_conf,
            applicability=applicability,
            einfluss=r_einfluss,
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

    # ── 4) Kritische Wartungspunkte der erkannten Motorvariante ────────────────
    # PLATZIERUNG (bewusst, gemessen): `_id` ist EIN globaler, fortlaufender Zähler
    # über alle Kategorien. Diese Sektion steht deshalb genau hier — hinter den
    # DB-Kategorien, aber VOR dem Marktvergleich:
    #
    #   * Hinter Schwachstelle/Rückruf/Motorproblem, damit deren Nummern durch die
    #     Erweiterung unverändert bleiben (keine ID-Migration, KaufCheck-P1-3).
    #   * VOR dem Marktvergleich, weil der Marktvergleich-Insight nur bei
    #     vorhandenen Marktdaten entsteht. Stünde die Wartung dahinter, hinge ihre
    #     Nummer davon ab, ob eine Marktrecherche Ergebnisse geliefert hat — und die
    #     daraus abgeleiteten Kaufaktionen wären nicht mehr marktunabhängig (P0-1).
    #     Genau dieser Fehler ist in der ersten Fassung aufgetreten und vom Test
    #     "gleicher Fall mit/ohne Marktpreis" gefunden worden.
    #
    # Der Preis dafür ist die Nummer des Marktvergleich-Insights, die sich um die
    # Zahl der Wartungspunkte verschiebt. Das ist folgenlos: keine Stelle im Code
    # liest die Nummer einer Evidence-ID, der Marktvergleich wird ausschließlich
    # über `kategorie` gefunden (`marktvergleich_id`), und gespeicherte Alt-Checks
    # tragen ihre eigenen IDs im JSON — sie werden nie neu berechnet.
    #
    # NUR für den Kaufcheck: der Verkaufscheck bewertet den Marktwert, nicht die
    # anstehende Wartung — sein Insight-Satz bleibt dadurch unverändert.
    #
    # `kritische_wartung` hat KEINE Baujahres-Spalte (Schema: variante_id, bauteil,
    # intervall, hinweis). Die Applicability kommt deshalb ausschließlich über die
    # Motorvariante: nur bei EINDEUTIG erkanntem Motor entstehen diese Insights, und
    # ein Baujahr, das zu einer anderen Generation gehört, führt bereits in
    # `find_baureihe`/`find_motor` zu einer anderen (oder keiner) Variante. Es wird
    # hier bewusst KEINE eigene Baujahreslogik erfunden.
    if check_typ == "kauf" and motor_match:
        for w in motor_match.get("kritische_wartung") or []:
            bauteil = (w.get("bauteil") or "").strip()
            if not bauteil:
                continue
            quellen = [EvidenceQuelle(typ="motorvarianten", ref=bauteil,
                                      titel="VIRA-Wartungsdaten (geprüft)")]
            teile = [(w.get("hinweis") or "").strip()]
            if w.get("intervall"):
                teile.append(f"Vorgesehenes Intervall: {str(w['intervall']).strip()}.")
            insights.append(Insight(
                id=_id("wartung"),
                kategorie="wartung",
                titel=f"{bauteil} — kritischer Wartungspunkt ({motor_match.get('bezeichnung') or 'Motor'})",
                beschreibung=" ".join(t for t in teile if t).strip(),
                quellen_typen=_typen(quellen),
                quellen=quellen,
                # Provenance: die Daten hängen direkt an der eindeutig erkannten
                # Motorvariante -> "hoch". Kein Bezug zum Schweregrad (den gibt es
                # für Wartungspunkte gar nicht).
                confidence="hoch",
                einfluss="Vor dem Kauf Durchführung und Nachweis klären.",
            ))

    # ── 5) Marktvergleich (Marktvergleich 2.0 — deterministisch) ───────────────
    # Quellen bleiben die RECHERCHE-Seiten (typ="web") — eine allgemeine Suchseite
    # wird NICHT als einzelnes Vergleichsfahrzeug ausgegeben. Die eigentliche
    # Vergleichbarkeit/Preisbewertung kommt aus der deterministischen Marktanalyse.
    web_belege = web_belege or []
    web_quellen = [
        EvidenceQuelle(typ="web", url=b.get("url"), titel=b.get("titel"), qualitaet=b.get("qualitaet"))
        for b in web_belege if b.get("url")
    ]
    mv = _marktvergleich_insight(_id, web_quellen, marktanalyse, marktpreis_min, marktpreis_max, check_typ)
    if mv:
        insights.append(mv)


    return insights


def _verwendete_quellen(web_quellen, marktanalyse):
    """Nur die Quellen, die TATSÄCHLICH einen verwendeten Vergleichs-Datenpunkt
    beigetragen haben (Root-Cause #5b): eine URL erscheint im 'Warum?' nur, wenn aus
    ihrem Snippet ein verwendeter Preis stammt. Verhindert, dass eine kuratierte,
    aber fachfremde/Modell-fremde Seite als Marktquelle auftaucht.

    Fallback: liegen keine verwendeten Beobachtungen mit URL vor, bleiben die
    Web-Quellen als reine RECHERCHE-Quellen erhalten (kein Vergleichsfahrzeug-Anspruch).
    """
    beob = getattr(marktanalyse, "beobachtungen", None) or []
    used_urls: list[str] = []
    for b in beob:
        u = getattr(b, "quelle_url", None)
        if u and u not in used_urls:
            used_urls.append(u)
    if not used_urls:
        return _dedup_quellen(web_quellen)
    per_url = {q.url: q for q in web_quellen if getattr(q, "url", None)}
    out = []
    for u in used_urls:
        out.append(per_url.get(u) or EvidenceQuelle(typ="web", url=u, titel=_domain_titel(u)))
    # §12: nach kanonischer URL bzw. Domain+Titel deduplizieren, damit nicht dieselbe
    # Quelle doppelt erscheint (der berüchtigte "12gebrauchtwagen.de, 12gebrauchtwagen.de").
    return _dedup_quellen(out)


def _kanon_url(url: str | None) -> str:
    """Kanonische URL für die Dedup: Domain + Pfad ohne Query/Fragment/trailing Slash."""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        return f"{p.netloc.lower().removeprefix('www.')}{p.path.rstrip('/')}"
    except Exception:
        return url


def _dedup_quellen(quellen):
    """Dedupliziert EvidenceQuelle-Liste nach kanonischer URL UND nach Anzeige-Identität
    (Domain + Titel) — verhindert sowohl exakte Query-Duplikate als auch zwei
    Domain-Fallback-Einträge derselben Quelle. Reihenfolge bleibt erhalten."""
    out = []
    gesehen_url: set[str] = set()
    gesehen_anzeige: set[tuple] = set()
    for q in quellen or []:
        ku = _kanon_url(getattr(q, "url", None))
        anzeige = (_domain_titel(getattr(q, "url", "") or ""), (getattr(q, "titel", None) or "").strip().lower())
        if ku and ku in gesehen_url:
            continue
        if anzeige in gesehen_anzeige:
            continue
        if ku:
            gesehen_url.add(ku)
        gesehen_anzeige.add(anzeige)
        out.append(q)
    return out


def _domain_titel(url: str) -> str:
    try:
        from urllib.parse import urlparse
        net = urlparse(url).netloc.lower()
        return net[4:] if net.startswith("www.") else net
    except Exception:
        return url


def _marktvergleich_insight(_id, web_quellen, marktanalyse, marktpreis_min, marktpreis_max, check_typ):
    """Baut den Marktvergleich-Insight. Bevorzugt die deterministische Marktanalyse
    (Median + robuste Spanne + verwendete Datenpunkte); ohne belastbare Analyse
    bleibt ein transparenter Hinweis auf die begrenzte Web-Datenbasis."""
    einfluss = "Grundlage der Preisstrategie." if check_typ == "verkauf" else "Grundlage der Preisbewertung."
    # Nur die Quellen der tatsächlich verwendeten Vergleiche (siehe _verwendete_quellen).
    web_quellen = _verwendete_quellen(web_quellen, marktanalyse)

    if marktanalyse and marktanalyse.median_eur:
        m = marktanalyse
        teile = [
            f"{m.verwendet} vergleichbare Preisangaben ausgewertet "
            f"({m.anzahl_sehr_aehnlich} sehr ähnlich · {m.anzahl_aehnlich} ähnlich"
            + (f" · {m.anzahl_bedingt} bedingt" if m.anzahl_bedingt else "") + ").",
            f"Median: {m.median_eur:,} €.".replace(",", "."),
        ]
        if m.spanne_min_eur and m.spanne_max_eur:
            teile.append(f"Typischer Marktbereich: {m.spanne_min_eur:,}–{m.spanne_max_eur:,} €.".replace(",", "."))
        if m.angebot_eur and m.differenz_eur is not None:
            vz = "+" if m.differenz_eur >= 0 else "−"
            teile.append(f"Angebot {m.angebot_eur:,} € = {vz}{abs(m.differenz_eur):,} € "
                         f"({vz}{abs(m.differenz_pct):.1f} %) zum Median.".replace(",", "."))
        return Insight(
            id=_id("marktvergleich"),
            kategorie="marktvergleich",
            titel="Marktvergleich (aktuelle Websuche)",
            beschreibung=" ".join(teile),
            quellen_typen=_typen(web_quellen) + ["marktvergleich"] if web_quellen else ["marktvergleich"],
            quellen=web_quellen,
            confidence=m.datenqualitaet,
            einfluss=einfluss,
            marktanalyse=m,
        )

    # Fallback: keine belastbare deterministische Analyse (zu wenige oder zu stark
    # streuende Datenpunkte). Ehrlich kennzeichnen, keine Scheinpräzision.
    if not web_quellen:
        return None
    spanne = ""
    if marktpreis_min or marktpreis_max:
        spanne = f" Grobe Orientierung: {marktpreis_min}–{marktpreis_max} €."
    if marktanalyse and marktanalyse.methode:
        # Konkrete, deterministisch ermittelte Begründung (zu wenige / zu breit gestreut).
        beschr = marktanalyse.methode + spanne
    elif marktanalyse and marktanalyse.gefunden:
        beschr = (f"{marktanalyse.gefunden} Preisangaben aus der Websuche gefunden, aber zu wenige "
                  f"eindeutig vergleichbare für eine belastbare Spanne." + spanne)
    else:
        beschr = ("Nur begrenzte, nicht eindeutig vergleichbare Web-Daten gefunden — "
                  "die Marktanalyse basiert auf einer schmalen Datenbasis." + spanne)
    return Insight(
        id=_id("marktvergleich"),
        kategorie="marktvergleich",
        titel="Marktvergleich (begrenzte Web-Datenbasis)",
        beschreibung=beschr,
        quellen_typen=_typen(web_quellen) + ["marktvergleich"],
        quellen=web_quellen,
        confidence="niedrig",
        einfluss=einfluss,
        marktanalyse=marktanalyse,
    )


# ── Schicht B: Evidence dem LLM geben & referenzierte IDs validieren ─────────
#
# Das LLM darf seine Entscheidungen (Empfehlung/Preis/…) mit EXISTIERENDER Evidence
# verknüpfen — aber NIE neue Evidence erfinden. Es bekommt eine kompakte Liste der
# Schicht-A-Insight-IDs und darf ausschließlich diese referenzieren. Das Backend
# bleibt Source of Truth: gelieferte IDs werden gegen die echten Insight-IDs
# gefiltert (Halluzinationen verworfen). Confidence/Provenance ändert das LLM nicht.

_EVIDENCE_TYP_LABEL = {
    "schwachstelle": "Schwachstelle (DB, geprüft)",
    "rueckruf":      "Rückruf (KBA)",
    "motorproblem":  "Motorproblem (DB, geprüft)",
    "marktvergleich": "Marktvergleich (Websuche)",
    "wartung":       "Kritischer Wartungspunkt (DB, geprüft)",
}


# §27/§28: Wording, das das LLM WÖRTLICH für die jeweilige Rückruf-Betroffenheits-
# stufe übernehmen muss — verhindert, dass der Freitext-Bericht eine sicherere
# Aussage trifft ("betrifft dein Fahrzeug") als die tatsächlich geprüfte Stufe.
# (§Phase 7: jetzt zentral in app/recall_filter.py, hier nur re-importiert — siehe
# Modul-Header oben.)


def format_evidence_for_prompt(insights: list[Insight]) -> str:
    """Kompakter Evidence-Block für den LLM-Prompt: ID, Typ, Confidence, Titel (und
    bei Rückrufen zusätzlich die verbindliche Applicability-Formulierung, §27/§28) —
    kein aufgeblähter JSON-Blob. Leerer String, wenn keine Evidence existiert."""
    if not insights:
        return ""
    lines = [
        "=== VERFÜGBARE EVIDENCE (Schicht A, Backend-geprüft) ===",
        "Referenziere in den *_evidence_ids-Feldern NUR IDs aus dieser Liste — sonst leere Liste.",
        "Bei Rückrufen (kategorie=rueckruf) gilt die angegebene Betroffenheits-Formulierung "
        "WÖRTLICH — schreibe NIEMALS 'betrifft dein Fahrzeug' ohne FIN-Prüfung.",
    ]
    for i in insights:
        label = _EVIDENCE_TYP_LABEL.get(i.kategorie, i.kategorie)
        zeile = f"[{i.id}] {label} | Confidence: {i.confidence} | {i.titel}"
        if i.kategorie == "rueckruf" and i.applicability:
            wortlaut = RUECKRUF_APPLICABILITY_TEXT.get(i.applicability, i.applicability)
            zeile += f" | Betroffenheit: {wortlaut}"
        lines.append(zeile)
    return "\n".join(lines)


def valid_evidence_ids(insights: list[Insight]) -> set[str]:
    return {i.id for i in insights}


def marktvergleich_id(insights: list[Insight]) -> str | None:
    """ID des Marktvergleich-Insights (falls vorhanden) — damit der Marktvergleich
    zuverlässig unter 'Warum diese Preisbewertung?' erscheint, auch wenn das LLM ihn
    nicht selbst referenziert hat."""
    for i in insights:
        if i.kategorie == "marktvergleich":
            return i.id
    return None


def ergaenze_id(ids: list[str], neue: str | None) -> list[str]:
    """Fügt `neue` ans Ende hinzu, falls noch nicht enthalten (Reihenfolge erhalten)."""
    if neue and neue not in ids:
        return [*ids, neue]
    return ids


def filter_evidence_ids(ids, valid: set[str], *, feld: str = "") -> list[str]:
    """Behält nur IDs, die zu EXISTIERENDER Evidence dieses Checks gehören
    (Reihenfolge erhalten, dedupliziert). Ungültige/halluzinierte IDs werden
    verworfen und geloggt. Keine Exception — die restliche Antwort bleibt valide.
    """
    out: list[str] = []
    for x in ids or []:
        if isinstance(x, str) and x in valid:
            if x not in out:
                out.append(x)
        elif x:
            log.info("Schicht B: ungültige Evidence-ID vom LLM verworfen (Feld %s): %r", feld or "?", x)
    return out


def enrich_marktvergleich_spanne(insights: list[Insight], marktpreis_min, marktpreis_max) -> None:
    """Ergänzt die erst NACH dem LLM bekannte Marktspanne im Marktvergleich-Insight
    (gleiche ID bleibt erhalten — vom LLM referenzierte IDs bleiben gültig)."""
    if not (marktpreis_min or marktpreis_max):
        return
    for i in insights:
        if i.kategorie == "marktvergleich" and "Marktspanne" not in i.beschreibung:
            i.beschreibung = f"{i.beschreibung} Ermittelte Marktspanne: {marktpreis_min}–{marktpreis_max} €."
