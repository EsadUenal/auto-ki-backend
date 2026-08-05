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

log = logging.getLogger(__name__)

_JAHR = re.compile(r"\b(?:19|20)\d{2}\b")
_BEREICH = re.compile(r"[-–]|bis")
_ALLGEMEIN = {"", "alle", "alle baujahre", "-", "n/a", "unbekannt", "diverse"}

# ── Rückruf-Varianten-Zuordnung (Phase 1B) ───────────────────────────────────
# Die Tabelle `rueckruf` ist NUR an baureihe_id gekoppelt (kein motorvariante/
# kraftstoff/antrieb-Feld). Die Varianten-Einschränkung steht — wenn überhaupt —
# als FREITEXT im mangel/abhilfe/betroffene_baujahre (z.B. "(Plug-in-Hybrid)",
# "Hochvoltbatterie"). Daraus leiten wir DETERMINISTISCH ab, ob ein Rückruf einen
# bestimmten Antrieb/Kraftstoff adressiert — ohne Raten, ohne LLM.

# Signalwörter, die einen Rückruf auf Hochvolt-/Hybrid-/Elektro-Antrieb eingrenzen.
_HV_WORTE = ("hochvolt", "hochspannung", "plug-in", "plug in", "plugin", "phev",
             "hybrid", "elektro", "elektrisch")

# Normierung des Kraftstoffs (Motorvariante ODER Rückruf-Freitext-Qualifier).
def _norm_kraftstoff(text: str | None) -> str | None:
    t = (text or "").lower()
    if not t:
        return None
    if "mild" in t:
        return "mild"          # Mild-Hybrid (48V) — NICHT das Hochvolt-System eines PHEV/BEV
    if any(k in t for k in ("plug-in", "plug in", "plugin", "phev")):
        return "phev"
    if "elektro" in t or "electric" in t:
        return "elektro"
    if "hybrid" in t:
        return "phev"          # generisches "Hybrid" -> Hochvolt-Antrieb (nicht Mild s.o.)
    if "diesel" in t:
        return "diesel"
    if "benzin" in t or "petrol" in t:
        return "benzin"
    return None


def _paren_qualifier(betroffene: str | None) -> str | None:
    """Antriebs-Qualifier aus einem Klammerzusatz wie '2019-2020 (Plug-in-Hybrid)'."""
    if not betroffene:
        return None
    m = re.search(r"\(([^)]*)\)", betroffene)
    return _norm_kraftstoff(m.group(1)) if m else None


# Antriebe, die ein Hochvolt-System besitzen (für den Abgleich mit HV-Rückrufen).
_HAT_HOCHVOLT = {"phev", "elektro"}


# Reliability-Sprint 3 (§27/§28): "exakt" wurde bisher als "Betrifft dein Fahrzeug"
# angezeigt — allein aus Baujahr-Text-Match + vorhandener KBA-Referenznummer, OHNE
# jede VIN-/FIN-Prüfung (die es im System nicht gibt). Das war zu sicher formuliert.
# Neue vierstufige Semantik (fünfte Stufe reserviert, aktuell unerreichbar):
#   confirmed_by_vin — NUR nach echter VIN-Prüfung. Wird vom Code aktuell NIE erzeugt
#                       (keine VIN-Erfassung im System) — bewusst kein Fake-Feature.
#   variant_match     — Baujahr/Antriebs-Variante passt, KBA-Referenz vorhanden.
#   series_only       — Baujahr passt bzw. allgemeiner Baureihen-Rückruf, aber ohne
#                        die belastbarste Kombination aus Variante+Referenz.
#   unclear           — Betroffenheit nicht bestimmbar.
#   incompatible       — Antriebs-/Variantenwiderspruch, wird vollständig ausgeblendet.
_HINWEIS_FIN = "Betroffenheit anhand der FIN beim Hersteller/KBA prüfen."


def _rueckruf_applicability(r: dict, passt: bool | None, kba: str, motor_match: dict | None):
    """Bestimmt, WIE SICHER ein Rückruf GENAU DIESES Fahrzeug betrifft.

    Rückgabe: (applicability, confidence, einfluss, variant_hinweis)
      applicability: "variant_match" | "series_only" | "unclear" | "incompatible"
                      (theoretisch auch "confirmed_by_vin" — aktuell nie erzeugt)
      confidence:    Beleglage des Insights ("hoch"|"mittel"|"niedrig")
      einfluss:      Handlungshinweis
      variant_hinweis: Zusatztext für die Beschreibung (oder "")

    Strikt getrennte Konzepte:
      severity  = wie schlimm            (eigenes Feld, hier NICHT berührt)
      confidence= wie gut belegt         (Beleglage/Provenance)
      applicability = betrifft dieses Fahrzeug  (Varianten-/Antriebs-Zuordnung) —
        OHNE VIN-Prüfung niemals "confirmed_by_vin"/eine "Betrifft dein Fahrzeug
        garantiert"-Aussage (§27).
    """
    text = " ".join(filter(None, [r.get("mangel"), r.get("abhilfe"), r.get("betroffene_baujahre")]))
    ist_hv_rueckruf = any(w in text.lower() for w in _HV_WORTE)
    qualifier = _paren_qualifier(r.get("betroffene_baujahre"))       # z.B. "phev"
    fahrzeug_kraftstoff = _norm_kraftstoff((motor_match or {}).get("kraftstoff"))

    # Der Rückruf grenzt sich auf einen bestimmten Antrieb ein (Klammer-Qualifier
    # ODER klarer Hochvolt-/Hybrid-Bezug).
    scope = qualifier or ("phev" if ist_hv_rueckruf else None)
    if scope:
        if fahrzeug_kraftstoff is None:
            # Motor nicht erkannt -> Varianten-Betroffenheit NICHT bestimmbar.
            return ("unclear", "niedrig",
                    f"Betroffenheit unklar — der Rückruf betrifft bestimmte Varianten. {_HINWEIS_FIN}",
                    "Für die Baureihe hinterlegt; die genaue Variantenbetroffenheit ist ohne erkannte Motorisierung nicht gesichert.")
        matcht = (
            fahrzeug_kraftstoff == scope
            or (scope in _HAT_HOCHVOLT and fahrzeug_kraftstoff in _HAT_HOCHVOLT)
        )
        if matcht:
            # Passende Variante + Baujahr-Deckung + KBA-Referenz -> stärkste OHNE-VIN
            # erreichbare Stufe: "kann diese Variante betreffen", nicht "betrifft".
            if passt is True and kba:
                return ("variant_match", "hoch",
                        f"Sicherheitsrelevant — Durchführung der Rückrufaktion per FIN prüfen. {_HINWEIS_FIN}", "")
            return ("series_only", "mittel",
                    f"Sicherheitsrelevant — Durchführung der Rückrufaktion prüfen. {_HINWEIS_FIN}", "")
        # Klarer Antriebs-Widerspruch (§8): z.B. Hochvolt-/PHEV-Rückruf, Fahrzeug ist
        # nachweislich Diesel. Die Motorisierung ist ERKANNT und passt eindeutig NICHT
        # -> "incompatible". build_insights entfernt solche Rückrufe VOLLSTÄNDIG aus
        # den sichtbaren Findings (kein Anzeigen als "unklare Betroffenheit").
        scope_label = {"phev": "Plug-in-Hybrid-/Hochvolt-Varianten", "elektro": "Elektro-Varianten",
                       "diesel": "Diesel-Varianten", "benzin": "Benzin-Varianten",
                       "mild": "Mild-Hybrid-Varianten"}.get(scope, "bestimmte Varianten")
        return ("incompatible", "hoch",
                f"Betrifft laut Datenlage {scope_label} — die erkannte Motorisierung gehört nicht dazu.",
                f"Dieser Rückruf betrifft {scope_label}; die erkannte Motorisierung passt eindeutig nicht dazu.")

    # Kein Antriebs-Scope erkennbar -> allgemeiner Baureihen-Rückruf (z.B. Bremse,
    # Lenkung): gilt unabhängig von der Motorisierung.
    if passt is True and kba:
        return ("variant_match", "hoch",
                f"Sicherheitsrelevant — Durchführung der Rückrufaktion per FIN prüfen. {_HINWEIS_FIN}", "")
    if passt is True:
        return ("series_only", "mittel",
                f"Sicherheitsrelevant — Durchführung der Rückrufaktion prüfen. {_HINWEIS_FIN}", "")
    return ("series_only", "mittel",
            f"Sicherheitsrelevant — Baujahr-Zuordnung nicht eindeutig. {_HINWEIS_FIN}", "")


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

    # ── 4) Marktvergleich (Marktvergleich 2.0 — deterministisch) ───────────────
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
}


# §27/§28: Wording, das das LLM WÖRTLICH für die jeweilige Rückruf-Betroffenheits-
# stufe übernehmen muss — verhindert, dass der Freitext-Bericht eine sicherere
# Aussage trifft ("betrifft dein Fahrzeug") als die tatsächlich geprüfte Stufe.
RUECKRUF_APPLICABILITY_TEXT: dict[str, str] = {
    "confirmed_by_vin": "Für dieses Fahrzeug per FIN bestätigt",
    "variant_match": "Kann Fahrzeuge dieser Variante betreffen — per FIN prüfen",
    "series_only": "Für Teile der Baureihe gemeldet — per FIN prüfen",
    "unclear": "Betroffenheit unklar — per FIN prüfen",
}


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
