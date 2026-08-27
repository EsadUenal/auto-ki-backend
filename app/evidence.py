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
    kba_referenz_anzeige,
    RUECKRUF_APPLICABILITY_TEXT,
)
# DATA-SAFETY-RUNTIME-GATE: zentrale Allowed-List für Baureihen-Schwachstellen,
# geteilt mit build_db_context (car_lookup.py) — analog zu recall_filter.
from app.motor_applicability import gefilterte_schwachstellen
from app.verification import is_verified

log = logging.getLogger(__name__)

# ── Trust-Stufen (siehe models.Insight.trust) ────────────────────────────────
TRUST_VERIFIED = "verified"
TRUST_UNVERIFIED_DB = "unverified_db"
TRUST_WEB = "web"
TRUST_USER = "user"
TRUST_ABGELEITET = "abgeleitet"


def _trust_der_baureihe(baureihe: dict | None, fakt: str) -> str:
    """Trust-Stufe einer DB-Faktenart für DIESE Baureihe.

    Einziger Übersetzer zwischen der bestehenden Verifikations-Architektur
    (app/verification.py, Stufen unverified/reviewed/verified/rejected) und der
    Trust-Achse der Evidence. `reviewed` zählt hier bewusst NICHT als verified —
    dieselbe Regel wie im Marktvergleich: ohne gespeicherten Nachweis keine harte
    Wirkung.
    """
    return TRUST_VERIFIED if is_verified(baureihe, fakt) else TRUST_UNVERIFIED_DB


def _trust_des_fakts(fakt: dict | None, baureihe: dict | None, fakt_art_fallback: str) -> str:
    """Trust-Stufe EINES Fahrzeugfakts.

    VERIFICATION-PILOT: `app/database.py::get_baureihe` haengt jedem Fakt bereits
    `_trust` an (aus app/fakt_verifikation.py, inkl. Fingerprint-Pruefung). Diese
    Einzelfakt-Entscheidung hat Vorrang.

    Der Rueckfall auf die BAUREIHEN-weite `verification` bleibt erhalten, damit
    bestehende Verifikationen und alle Aufrufer mit selbst gebauten Fakt-Dicts
    (Tests, Fixtures) unveraendert funktionieren. Er kann nur ANHEBEN, wenn die
    Baureihe fuer die ganze Faktenart ausdruecklich verified ist — das ist die
    alte, grobe Semantik und bleibt bewusst moeglich.
    """
    if isinstance(fakt, dict) and fakt.get("_trust"):
        einzel = fakt["_trust"]
        if einzel == TRUST_VERIFIED:
            return TRUST_VERIFIED
    return _trust_der_baureihe(baureihe, fakt_art_fallback)


def _db_quellentitel(basis: str, trust: str) -> str:
    """Quellentitel für einen DB-Fakt.

    Das Wort "(geprüft)" wird NUR angehängt, wenn für diese Faktenart tatsächlich
    eine Verifikation mit Quelle hinterlegt ist. Vorher stand es unbedingt an
    allen DB-Quellen, obwohl 0 von 421 Baureihen einen `verification`-Eintrag
    tragen und die Tabelle `quelle` leer ist — eine Behauptung ohne Grundlage.
    """
    return f"{basis} (geprüft)" if trust == TRUST_VERIFIED else basis


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
    web_recherche=None,
) -> list[Insight]:
    """Baut die Liste nachvollziehbarer Insights aus deterministischen Daten.

    `web_belege` ist die fertige Belege-Liste (results_to_belege): dicts mit
    typ/titel/url/snippet/qualitaet. `req` ist der Kauf-/Verkaufscheck-Request.

    `web_recherche` (optional, technischer Web-Fallback): eine
    `TechnischeRecherche` aus app/technical_research.py. Ihre Fakten werden als
    EIGENE Kategorien (`web_schwachstelle`/`web_rueckruf`/`web_wartung`) mit
    `typ="web_technik"`-Quellen ausgegeben — nie vermischt mit der geprüften
    Fahrzeugdatenbank. Nur für den Kaufcheck; der Verkaufscheck bleibt unberührt.
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

    # ── 1) Schwachstellen der Baureihe (VIRA-DB) ───────────────────────────────
    # DATA-SAFETY-RUNTIME-GATE: `gefilterte_schwachstellen` entfernt vorher alle
    # Sätze, deren Freitext sie auf eine nachweislich ANDERE Motorisierung
    # eingrenzt (z.B. "Steuerkette (N47 Dieselmotoren)" an einem Benziner). Diese
    # Sätze erzeugen damit weder Evidence noch Kaufaktion noch Floor — exakt wie
    # ein "incompatible"-Rückruf.
    for s in gefilterte_schwachstellen(
            (baureihe or {}).get("schwachstellen_baureihe"), motor_match, baureihe):
        passt = _baujahr_passt(s.get("betroffene_baujahre"), baujahr)
        if passt is False:
            continue  # gilt nachweislich nicht für dieses Baujahr -> nicht ausgeben
        # VERIFICATION-PILOT: PRO FAKT, nicht mehr pro Kategorie. Eine geprüfte
        # Schwachstelle zieht die ungeprüften derselben Baureihe nicht mehr mit.
        trust_schwachstelle = _trust_des_fakts(s, baureihe, "schwachstellen")
        quellen = [EvidenceQuelle(typ="datenbank", ref=s.get("bauteil"),
                                  titel=_db_quellentitel("VIRA-Fahrzeugdatenbank",
                                                         trust_schwachstelle))]
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
            trust=trust_schwachstelle,
            einfluss=_einfluss_schwachstelle(s.get("schweregrad"), check_typ),
        ))

    # ── 2) Rückrufe (KBA-Daten) ────────────────────────────────────────────────
    for r in (baureihe or {}).get("rueckrufe") or []:
        passt = _baujahr_passt(r.get("betroffene_baujahre"), baujahr)
        if passt is False:
            continue
        kba = (r.get("kba_referenz") or "").strip()
        marke = (baureihe or {}).get("marke")
        # KBA-Trust-Gate (DATA-TRUST-AUDIT): eine unplausible oder markenübergreifend
        # kollidierende Referenz wird NICHT als Quelle gezeigt — `kba_anzeige` ist
        # dann None, exakt wie eine fehlende Referenz. Der Rohwert `kba` bleibt nur
        # zur Weitergabe an `_rueckruf_applicability` erhalten (die dieselbe Prüfung
        # intern noch einmal anwendet, um die Stufe zu bestimmen).
        kba_anzeige = kba_referenz_anzeige(kba, marke)
        # kba_referenz ist die KONKRETE, pro-Rückruf gültige Quelle -> bleibt am Insight
        # (nur wenn plausibel — siehe oben).
        #
        # §6 DATA-SAFETY-RUNTIME-GATE — die Trennung, die im Code sichtbar bleiben
        # muss: das KBA-Trust-Gate prüft FORMAT und Kollisionsfreiheit der Nummer.
        # Das ist eine Plausibilitätsaussage, KEINE inhaltliche Verifikation. Der
        # Audit konnte keinen einzigen DB-Rückruf gegen eine amtliche Quelle
        # bestätigen; solange die Baureihe für "rueckrufe" nicht ausdrücklich
        # verified ist, heißt die Quelle deshalb "Rückrufhinweis" und nicht
        # "KBA-Rückrufdatenbank" — und trägt keinen Floor.
        # VERIFICATION-PILOT: PRO RUECKRUF. Ein amtlich belegter Rueckruf macht
        # die uebrigen, unbelegten Rueckrufe derselben Baureihe nicht mit-
        # vertrauenswuerdig.
        trust_rueckruf = _trust_des_fakts(r, baureihe, "rueckrufe")
        if trust_rueckruf == TRUST_VERIFIED:
            # RECALL-PILOT (§13): "KBA" darf nur dastehen, wo tatsächlich eine
            # amtlich bestätigte KBA-Referenz vorliegt. Ein Rückruf kann sehr wohl
            # belegt sein, ohne dass eine deutsche Aktionsnummer auffindbar ist —
            # der BMW-Hochvoltspeicher-Rückruf vom Oktober 2020 ist über die
            # amtliche NHTSA-Datenbank (20V-601) und mehrere Fachmedien belegt,
            # trägt aber keine KBA-Nummer. Ihn als "KBA-Rückruf" auszuweisen wäre
            # dieselbe Sorte falscher Amtlichkeit, gegen die das Trust-Gate
            # überhaupt gebaut wurde.
            quellen_titel = ("KBA-Rückrufdatenbank" if kba_anzeige
                             else "Amtlich belegter Rückruf (keine KBA-Referenz hinterlegt)")
        else:
            quellen_titel = ("Rückrufhinweis aus der VIRA-Fahrzeugdatenbank — "
                             "nicht amtlich bestätigt")
            kba_anzeige = None      # keine scheinbar amtliche Nummer anzeigen
        quellen = [EvidenceQuelle(typ="rueckruf_kba", ref=kba_anzeige, titel=quellen_titel)]
        # Phase 1B: Varianten-/Antriebs-Zuordnung -> applicability (getrennt von
        # confidence & severity). Ein Hochvolt-/PHEV-Rückruf wird NICHT als direkt
        # zutreffend für einen reinen Diesel markiert.
        # RECALL-PILOT §9: `recall_filter.referenz_ist_belegt` liest den Trust vom
        # Rückruf-Dict. `_trust_des_fakts` kennt darüber hinaus den Rückfall auf die
        # BAUREIHENWEITE `verification` (Alt-Mechanismus, siehe _trust_der_baureihe).
        # Damit Applicability und Insight-Trust nicht auseinanderlaufen, bekommt die
        # Applicability-Berechnung genau den Wert, der gleich auch am Insight steht —
        # statt eine zweite, schwächere Trust-Ermittlung zu benutzen.
        applicability, r_conf, r_einfluss, variant_hinweis = _rueckruf_applicability(
            {**r, "_trust": trust_rueckruf}, passt, kba, motor_match, marke=marke)
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
        #
        # §6: Das Präfix "KBA-Rückruf" behauptet eine amtliche Meldung. Solange die
        # Rückrufdaten dieser Baureihe nicht verified sind, heißt es "Rückrufhinweis"
        # — die Aussage bleibt inhaltlich vollständig erhalten, sie gibt sich nur
        # nicht mehr als amtlich bestätigt aus.
        # §13/§6: drei Stufen statt zwei — "KBA-Rückruf" nur mit amtlicher Nummer,
        # "Rückruf" für einen belegten Rückruf ohne KBA-Referenz, "Rückrufhinweis"
        # für alles Ungeprüfte.
        if trust_rueckruf == TRUST_VERIFIED:
            praefix = "KBA-Rückruf" if kba_anzeige else "Rückruf"
        else:
            praefix = "Rückrufhinweis"
        if applicability in ("confirmed_by_vin", "variant_match"):
            titel = f"{praefix}: {(r.get('mangel') or 'Rückrufaktion')[:80]}".rstrip(": ").strip()
        else:
            titel = f"{praefix} (Baureihe): {(r.get('mangel') or 'Rückrufaktion')[:70]}".rstrip(": ").strip()
        insights.append(Insight(
            id=_id("rueckruf"),
            kategorie="rueckruf",
            titel=titel,
            beschreibung=beschr.strip(" —"),
            quellen_typen=_typen(quellen),
            quellen=quellen,
            confidence=r_conf,
            applicability=applicability,
            trust=trust_rueckruf,
            einfluss=r_einfluss,
        ))

    # ── 3) Motorspezifische Probleme (nur bei ERKANNTEM Motor) ─────────────────
    if motor_match:
        for s in motor_match.get("schwachstellen_motor") or []:
            passt = _baujahr_passt(s.get("baujahre"), baujahr)
            if passt is False:
                continue
            # VERIFICATION-PILOT: PRO MOTORPROBLEM.
            trust_motorproblem = _trust_des_fakts(s, baureihe, "motorprobleme")
            quellen = [EvidenceQuelle(typ="motorvarianten", ref=motor_match.get("bezeichnung"),
                                      titel=_db_quellentitel("VIRA-Motorvariantendaten",
                                                             trust_motorproblem))]
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
                trust=trust_motorproblem,
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
            # VERIFICATION-PILOT: PRO WARTUNGSPUNKT. Nur ein als Herstellerintervall
            # verifizierter Eintrag darf spaeter auch so genannt werden (siehe unten).
            trust_wartung = _trust_des_fakts(w, baureihe, "wartung")
            quellen = [EvidenceQuelle(typ="motorvarianten", ref=bauteil,
                                      titel=_db_quellentitel("VIRA-Wartungsdaten",
                                                             trust_wartung))]
            teile = [(w.get("hinweis") or "").strip()]
            if w.get("intervall"):
                # §8 DATA-SAFETY-RUNTIME-GATE: "Vorgesehenes Intervall" behauptet eine
                # Herstellervorgabe. Der Audit hat gemessen, dass 284 von 1.497
                # Einträgen (19,0 %) gar kein Intervall enthalten, sondern einen
                # Erfahrungs-/Prüfhinweis ("Sichtprüfung ab 100.000 km", "Kein fester
                # Intervall", "~50-80 tkm", "Zustand prüfen"). Der neutrale Wortlaut
                # deckt beide Fälle ehrlich ab; die präzise Formulierung kommt zurück,
                # sobald der Eintrag als Herstellerintervall verifiziert ist.
                # P2-5 bleibt unberührt: keine Fälligkeits-Behauptung.
                wortlaut = ("Vorgesehenes Intervall" if trust_wartung == TRUST_VERIFIED
                            else "Hinterlegter Wartungshinweis")
                teile.append(f"{wortlaut}: {str(w['intervall']).strip()}.")
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
                trust=trust_wartung,
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

    # ── 6) Technische Web-Recherche (Fallback bei fehlendem DB-Profil) ─────────
    # GANZ AM ENDE — aus demselben Grund, aus dem die Wartungssektion vor dem
    # Marktvergleich steht: der `_id`-Zähler ist global. Hier gilt zusätzlich, dass
    # Web-Evidence nur in genau den Fällen entsteht, in denen der DB-Pfad nichts
    # geliefert hat; die vorherigen Kategorien sind dann ohnehin leer und es
    # verschiebt sich nichts.
    #
    # Eigene Kategorien mit `web_`-Präfix (§11): eine Web-Schwachstelle darf im
    # Frontend NIEMALS wie eine geprüfte DB-Schwachstelle aussehen. Auch die
    # Quellen tragen `typ="web_technik"` statt `datenbank`/`rueckruf_kba`.
    if web_recherche is not None and check_typ == "kauf":
        for fakt in web_recherche.fakten:
            if not fakt.quellen:
                continue          # ohne Quelle keine Evidence — nie
            insights.append(Insight(
                id=_id(f"web-{fakt.kategorie}"),
                kategorie=f"web_{fakt.kategorie}",
                titel=_WEB_TITEL[fakt.kategorie].format(
                    bauteil=(fakt.bauteil or "Fahrzeug").replace("_", " ")),
                beschreibung=fakt.aussage,
                quellen_typen=_typen(fakt.quellen),
                quellen=list(fakt.quellen),
                # Confidence kommt aus der QUELLENLAGE (Anzahl unabhängiger Domains
                # + Tier), nie aus dem Inhalt — dieselbe Trennung wie oben.
                confidence=fakt.confidence,
                applicability=fakt.applicability,
                # §11: Web-Evidence trägt eine echte Quellenlage (URL + Domain-
                # Qualität + Anzahl unabhängiger Domains) und bekommt deshalb eine
                # EIGENE Trust-Stufe — sie ist weder ein ungeprüfter DB-Satz noch
                # eine verifizierte Herstellerangabe. Floor-fähig ist sie NICHT,
                # siehe Begründung in app/empfehlungs_floor.py.
                trust=TRUST_WEB,
                einfluss=_WEB_EINFLUSS[fakt.kategorie],
            ))

    return insights


# Titel-/Einfluss-Vorlagen für Web-Evidence. Die Formulierung macht die Herkunft
# im Klartext sichtbar ("laut Webrecherche") — der Nutzer soll den Unterschied zur
# geprüften Fahrzeugdatenbank ohne Badge erkennen können.
_WEB_TITEL = {
    "schwachstelle": "{bauteil} — Hinweis aus der Webrecherche",
    "rueckruf": "Rückruf-Hinweis aus der Webrecherche ({bauteil})",
    "wartung": "{bauteil} — Wartungsangabe aus der Webrecherche",
}
_WEB_EINFLUSS = {
    "schwachstelle": "Aus Webquellen belegt, nicht aus der "
                     "Fahrzeugdatenbank — vor dem Kauf gezielt prüfen.",
    "rueckruf": "Aus Webquellen belegt — Betroffenheit ausschließlich anhand der "
                "FIN beim Hersteller/KBA klären.",
    "wartung": "Aus Webquellen belegte Intervallangabe — Nachweis der Durchführung "
               "verlangen.",
}


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
            trust=TRUST_ABGELEITET,
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
        trust=TRUST_ABGELEITET,
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
