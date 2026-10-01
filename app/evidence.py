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
    RECALL_STATE_AUS_APPLICABILITY,
    rueckruf_scope as _rueckruf_scope,
    RECALL_NOT_APPLICABLE as _RECALL_NOT_APPLICABLE,
)
from app.ausstattung_praesenz import (
    ABSENT as PRAESENZ_ABSENT, PRESENT as PRAESENZ_PRESENT, UNKNOWN as PRAESENZ_UNKNOWN,
    bedingung as praesenz_bedingung, praesenz,
)
# DATA-SAFETY-RUNTIME-GATE: zentrale Allowed-List für Baureihen-Schwachstellen,
# geteilt mit build_db_context (car_lookup.py) — analog zu recall_filter.
from app.motor_applicability import gefilterte_schwachstellen, varianten_applicability
from app.vehicle_identity import VehicleIdentity
from app.fin_hinweis import recall_status, recall_handlung
from app.risikothemen import WARTUNG_REGULAER, WARTUNG_VERSCHLEISS, kanonisiere, wartungsart
from app.verification import is_verified

log = logging.getLogger(__name__)


def _als_satz(text: str | None) -> str:
    """Freitext aus der Datenbank als Satz: großer Anfang, Satzzeichen am Ende."""
    t = (text or "").strip()
    if not t:
        return ""
    t = t[0].upper() + t[1:]
    return t if t.endswith((".", "!", "?")) else t + "."

# ── Trust-Stufen (siehe models.Insight.trust) ────────────────────────────────
TRUST_VERIFIED = "verified"
TRUST_UNVERIFIED_DB = "unverified_db"
TRUST_WEB = "web"
TRUST_USER = "user"
TRUST_ABGELEITET = "abgeleitet"

from app.rueckruf_titel import rueckruf_kurztitel  # noqa: E402

# Titel "<Bauteil>: <Art>" (neu) bzw. "<Bauteil> — <Art>" (bis RC1, noch in
# gespeicherten Ergebnissen und Fixtures). Beide Formen werden verstanden.
_TITEL_TRENNER = re.compile(r"\s+—\s+|:\s+")


def titel_bauteil(titel: str | None) -> str:
    """Bauteil-Teil eines Insight-Titels, unabhängig vom Trennzeichen."""
    return _TITEL_TRENNER.split(titel or "", 1)[0].strip()


def datenqualitaet(fakt: dict | None, trust: str, passt: bool | None,
                   beschreibung: str | None) -> str:
    """Datenqualität (confidence) einer Schwachstellen-Aussage aus der BELEGSTÄRKE.

    KaufCheck RC1 (Closing): "Software/Infotainment" stand auf "hoch", weil der
    Fakt verifiziert war und das Baujahr passte. Die Verifikation stützt sich aber
    nur auf Sekundärquellen (Stufe B), und die Beschreibung selbst spricht von
    "vereinzelten Berichten". "Hoch" darf weder aus der Baujahrpassung noch aus
    der bloßen Existenz einer Verifikation oder URL entstehen.

    Regeln (generisch, für Baureihen- und Motor-Schwachstellen):
      * nicht verifiziert                                   -> "niedrig"
      * verifiziert, aber schwach belegt                    -> "mittel"
      * "hoch" nur, wenn ALLES zutrifft:
          - starke Quelle: Primärquelle (Stufe A) ODER mindestens zwei
            unabhängige Sekundärquellen (Stufe B)
          - Baujahr eindeutig gedeckt (passt is True)
          - die Aussage ist kein Einzelbericht ("vereinzelt", "selten", …)
    Stufe C (Community/Foren) trägt nie "hoch".
    """
    if trust != TRUST_VERIFIED:
        return "niedrig"
    v = (fakt or {}).get("_verifikation") or {}
    stufe = str(v.get("quelle_stufe") or "").strip().upper()
    quellen = [q for q in re.split(r"[;\n]", str(v.get("quelle") or "")) if q.strip()]
    stark = stufe == "A" or (stufe == "B" and len(quellen) >= 2)
    einzel = bool(_EINZELBERICHT.search(beschreibung or ""))
    if stark and passt is True and not einzel:
        return "hoch"
    return "mittel"

# Formulierungen, mit denen eine DB-Beschreibung selbst sagt, dass es sich um
# Einzelberichte handelt — dann ist es keine "bekannte Schwachstelle".
_EINZELBERICHT = re.compile(
    r"\b(vereinzelt\w*|selten\w*|gelegentlich\w*|einzelf[äa]ll\w*|in einzelnen f[äa]llen)",
    re.IGNORECASE)


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


def _einfluss_schwachstelle(schweregrad: str | None, check_typ: str,
                            bekannt: bool = True) -> str:
    s = (schweregrad or "").strip().lower()
    if check_typ == "verkauf":
        return "Wertmindernd: beim Verkauf offen kommunizieren."
    # Root-Cause-Closing (Befund E/J): ein ungeprüfter Hinweis darf nicht wie ein
    # festgestelltes Risiko klingen. Schwere und Beleglage bleiben getrennte
    # Achsen; der Satz nennt deshalb beide, statt die Schwere allein sprechen zu
    # lassen.
    if not bekannt:
        if s in ("hoch", "kritisch", "sehr hoch"):
            return ("Laut Datenbank potenziell schwerwiegend, aber nicht geprüft: gezielt "
                    "nachfragen und prüfen lassen, nicht als festgestellten Mangel werten.")
        return ("Gemeldeter Hinweis, nicht geprüft: gezielt nachfragen, nicht als "
                "festgestellten Mangel werten.")
    if s in ("hoch", "kritisch", "sehr hoch"):
        return "Erhöht das technische Kaufrisiko deutlich."
    if s in ("mittel", "moderat"):
        return "Moderates technisches Risiko."
    return "Zu beachtender Schwachpunkt vor dem Kauf."


def _fakt_ref(tabelle: str, fakt: dict | None) -> str | None:
    """Herkunftszeile eines DB-Fakts, z.B. "schwachstelle_motor#6"."""
    fid = (fakt or {}).get("id")
    return f"{tabelle}#{fid}" if fid is not None else None


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
    identity: VehicleIdentity | None = None,
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
    identity = identity or VehicleIdentity.from_check_context(baureihe, motor_match, req)
    # The legacy engine/family gate also sees known request attributes even when
    # no unique motor row was found. No motor-specific facts are fabricated.
    #
    # BEWUSST NICHT `identity.fuel`: seit der Fuel-/Powertrain-Trennung
    # (app/kraftstoff_powertrain.py) ist `identity.fuel` REIN die Kraftstoffart
    # (Benzin/Diesel/Elektro) und für 'Mild-Hybrid'/'Plug-in-Hybrid' bewusst oft
    # None, wenn sich die Kraftstoffart nicht ableiten lässt. Die HV-/PHEV-
    # Rückruf-Erkennung (`recall_filter._norm_kraftstoff`/`_HAT_HOCHVOLT`)
    # braucht dagegen genau das ANTRIEBS-Signal ('Mild-Hybrid' vs. 'Plug-in-
    # Hybrid' vs. 'Elektro') — eine andere Dimension. Nutzerangabe hat weiterhin
    # Vorrang, sonst der unveränderte DB-Rohwert (nie `identity.fuel`).
    applicability_motor = {**(motor_match or {}),
                          "kraftstoff": (getattr(req, "kraftstoff", None)
                                        or (motor_match or {}).get("kraftstoff")),
                          # Final-Stabilization (Cluster D): beide Herkünfte
                          # getrennt — die Nutzerangabe ist eine KRAFTSTOFFart,
                          # der DB-Rohwert trägt die Antriebsart
                          # (recall_filter._fahrzeug_achsen).
                          "_kraftstoff_db": (motor_match or {}).get("kraftstoff"),
                          "_kraftstoff_nutzer": getattr(req, "kraftstoff", None)}

    def allowed(fakt):
        return fakt.get("_trust") != "rejected" and varianten_applicability(fakt, identity)[0] != "incompatible"
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

    # ── 1) Schwachstellen der Baureihe (ENFAL-DB) ───────────────────────────────
    # DATA-SAFETY-RUNTIME-GATE: `gefilterte_schwachstellen` entfernt vorher alle
    # Sätze, deren Freitext sie auf eine nachweislich ANDERE Motorisierung
    # eingrenzt (z.B. "Steuerkette (N47 Dieselmotoren)" an einem Benziner). Diese
    # Sätze erzeugen damit weder Evidence noch Kaufaktion noch Floor — exakt wie
    # ein "incompatible"-Rückruf.
    for s in gefilterte_schwachstellen(
            (baureihe or {}).get("schwachstellen_baureihe"), applicability_motor, baureihe):
        if not allowed(s):
            continue
        # Plain consumable wear belongs in the general inspection catalogue.
        # Early failure, corrosion or a concrete defect remains a risk.
        if (re.fullmatch(r"Bremsen|Bremsbeläge|Bremsscheiben|Reifen|Wischer", s.get("bauteil") or "", re.I)
                and re.search(r"verschleiß|verschleiss|abnutzung", s.get("beschreibung") or "", re.I)
                and not re.search(r"vorzeitig|ungewöhnlich|überdurchschnittlich|übermäßig|defekt|ausfall|korrosion|riss", s.get("beschreibung") or "", re.I)):
            continue
        passt = _baujahr_passt(s.get("betroffene_baujahre"), baujahr)
        if passt is False:
            continue  # gilt nachweislich nicht für dieses Baujahr -> nicht ausgeben
        # VERIFICATION-PILOT: PRO FAKT, nicht mehr pro Kategorie. Eine geprüfte
        # Schwachstelle zieht die ungeprüften derselben Baureihe nicht mehr mit.
        trust_schwachstelle = _trust_des_fakts(s, baureihe, "schwachstellen")
        quellen = [EvidenceQuelle(typ="datenbank", ref=s.get("bauteil"),
                                  titel=_db_quellentitel("ENFAL-Fahrzeugdatenbank",
                                                         trust_schwachstelle))]
        beschreibung_s = (s.get("beschreibung") or "").strip()
        # RC1: "bekannte Schwachstelle" nur, wenn der Fakt verifiziert ist UND die
        # Beschreibung selbst nicht von Einzelberichten spricht. "Vereinzelt
        # gemeldete Software-Bugs" oder "selten Knarzgeraeusche" sind Hinweise,
        # keine bekannte Baureihen-Schwachstelle.
        bekannt = (trust_schwachstelle == TRUST_VERIFIED
                   and not _EINZELBERICHT.search(beschreibung_s))
        insights.append(Insight(
            id=_id("schwachstelle"),
            kategorie="schwachstelle",
            risk_type="known_weakness",
            titel=(f"{s.get('bauteil') or 'Schwachstelle'}: "
                   f"{'bekannte Schwachstelle' if bekannt else 'gemeldeter Hinweis'}"),
            beschreibung=beschreibung_s,
            quellen_typen=_typen(quellen),
            quellen=quellen,
            # confidence = Datenqualitaet = BELEGLAGE, nie Schweregrad. Frueher hing
            # sie allein an der Baujahr-Deckung: ein nie geprueften DB-Eintrag mit
            # passendem Baujahr erschien als "Datenqualitaet hoch". Jetzt traegt nur
            # ein verifizierter Fakt "hoch" (bzw. "mittel" ohne Baujahrbezug); ein
            # unbelegter Eintrag ist "niedrig".
            confidence=datenqualitaet(s, trust_schwachstelle, passt, beschreibung_s),
            schweregrad=(s.get("schweregrad") or None),
            trust=trust_schwachstelle,
            einfluss=_einfluss_schwachstelle(s.get("schweregrad"), check_typ, bekannt),
            bauteil=(s.get("bauteil") or None),
            fakt_ref=_fakt_ref("schwachstelle_baureihe", s),
        ))

    # ── 2) Rückrufe (KBA-Daten) ────────────────────────────────────────────────
    for r in (baureihe or {}).get("rueckrufe") or []:
        if not allowed(r):
            continue
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
            quellen_titel = ("Rückrufhinweis aus der ENFAL-Fahrzeugdatenbank ("
                             "nicht amtlich bestätigt)")
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
            {**r, "_trust": trust_rueckruf,
             "_ausstattung": getattr(req, "ausstattung", None),
             "_freitext": " ".join(str(getattr(req, f, None) or "") for f in ("beschreibung", "freitext"))},
            passt, kba, applicability_motor, marke=marke,
            identity=identity if check_typ == "kauf" else None)
        # §8/§27: Rückruf betrifft eine eindeutig andere Motorisierung (z.B. Hochvolt-/
        # PHEV-Rückruf bei erkanntem Diesel) -> VOLLSTÄNDIG aus den sichtbaren Findings
        # entfernen (nicht als "unklare Betroffenheit" darstellen, nicht in "Was jetzt?").
        if applicability == "incompatible":
            continue
        beschr = (r.get("mangel") or "").strip()
        if r.get("abhilfe"):
            # Amtlicher Mangeltext bleibt unverändert; ein Punkt nur, wenn er fehlt.
            beschr = f"{beschr}{'' if beschr.endswith(('.', '!', '?')) else '.'} Abhilfe: {r['abhilfe'].strip()}"
        if r.get("datum"):
            beschr = f"{beschr} (Rückruf {r['datum']})"
        if variant_hinweis:
            beschr = f"{beschr}. {variant_hinweis}"
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
        # KaufCheck RC1: früher "Präfix: <amtlicher Text>[:70]" — ein hart
        # abgeschnittener Halbsatz. Jetzt ein kurzer Titel aus Bauteil und Folge
        # (app/rueckruf_titel.py); der volle amtliche Text steht unverändert in
        # `beschreibung`.
        kurz = rueckruf_kurztitel(r.get("mangel"))
        if applicability in ("confirmed_by_vin", "variant_match"):
            titel = f"{kurz} ({praefix})"
        else:
            titel = f"{kurz} ({praefix}, Baureihe)"
        insights.append(Insight(
            id=_id("rueckruf"),
            kategorie="rueckruf",
            risk_type="recall",
            kurztitel=kurz,
            titel=titel,
            beschreibung=beschr.strip(" —"),
            quellen_typen=_typen(quellen),
            quellen=quellen,
            confidence=r_conf,
            applicability=applicability,
            recall_status=recall_status(applicability),
            recall_state=RECALL_STATE_AUS_APPLICABILITY.get(applicability),
            trust=trust_rueckruf,
            einfluss=recall_handlung(recall_status(applicability)),
        ))

    # ── 3) Motorspezifische Probleme (nur bei ERKANNTEM Motor) ─────────────────
    if motor_match:
        for s in motor_match.get("schwachstellen_motor") or []:
            if not allowed(s):
                continue
            passt = _baujahr_passt(s.get("baujahre"), baujahr)
            if passt is False:
                continue
            # VERIFICATION-PILOT: PRO MOTORPROBLEM.
            trust_motorproblem = _trust_des_fakts(s, baureihe, "motorprobleme")
            quellen = [EvidenceQuelle(typ="motorvarianten", ref=motor_match.get("bezeichnung"),
                                      titel=_db_quellentitel("ENFAL-Motorvariantendaten",
                                                             trust_motorproblem))]
            kosten = s.get("kosten_ca")
            # "—" oder "Herstellergarantie" sind keine Kostenangabe: vorher entstand
            # daraus "Mögliche Reparaturkosten ca. —.". Final-Stabilization
            # (Cluster L): EIN Kostenformat für alle Ausgaben ("ca. 300 €",
            # "ca. 300–500 €" oder "Kostenangabe nicht verifiziert").
            from app.anzeige import kosten_anzeige
            kosten = kosten_anzeige(str(kosten)) if kosten else None
            titel_mp = (f"{s.get('bauteil') or 'Motorproblem'} "
                        f"({motor_match.get('bezeichnung') or 'Motor'})")
            if check_typ == "verkauf":
                einfluss = "Wertrelevant. Zustand des Bauteils belegen."
            else:
                # Root-Cause-Closing (Befund E/J): dieselbe Trennung wie bei den
                # Baureihen-Schwachstellen. Ein ungeprüfter Motorhinweis heißt
                # "gemeldeter Hinweis" und klingt nicht wie ein festgestelltes Risiko.
                bekannt_mp = (trust_motorproblem == TRUST_VERIFIED
                              and not _EINZELBERICHT.search(s.get("beschreibung") or ""))
                titel_mp += f": {'bekanntes Motorproblem' if bekannt_mp else 'gemeldeter Hinweis'}"
                if bekannt_mp:
                    einfluss = (f"Mögliche Reparaturkosten: {kosten}. Das erhöht das "
                                f"technische Risiko." if kosten else "Erhöht das technische Risiko.")
                else:
                    einfluss = ("Gemeldeter Hinweis, nicht geprüft: gezielt nachfragen, nicht "
                                "als festgestellten Mangel werten."
                                + (f" Hinterlegte Kostenangabe: {kosten}." if kosten else ""))
            insights.append(Insight(
                id=_id("motorproblem"),
                kategorie="motorproblem",
                risk_type="known_weakness",
                titel=titel_mp,
                beschreibung=(s.get("beschreibung") or "").strip(),
                quellen_typen=_typen(quellen),
                quellen=quellen,
                confidence=datenqualitaet(s, trust_motorproblem, passt, s.get("beschreibung")),
                trust=trust_motorproblem,
                einfluss=einfluss,
                bauteil=(s.get("bauteil") or None),
                fakt_ref=_fakt_ref("schwachstelle_motor", s),
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
            if not allowed(w):
                continue
            bauteil = (w.get("bauteil") or "").strip()
            if not bauteil:
                continue
            # VERIFICATION-PILOT: PRO WARTUNGSPUNKT. Nur ein als Herstellerintervall
            # verifizierter Eintrag darf spaeter auch so genannt werden (siehe unten).
            trust_wartung = _trust_des_fakts(w, baureihe, "wartung")
            quellen = [EvidenceQuelle(typ="motorvarianten", ref=bauteil,
                                      titel=_db_quellentitel("ENFAL-Wartungsdaten",
                                                             trust_wartung))]
            art = wartungsart(w.get("intervall"), w.get("hinweis"), bauteil)
            # Der Hinweis ist Freitext ("thermisch hoch belastet") und stand
            # bisher ohne Satzzeichen vor dem Intervallsatz: "... belastet
            # Hinterlegter Wartungshinweis: ...".
            teile = [_als_satz(w.get("hinweis"))]
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
            beschreibung_w = " ".join(t for t in teile if t).strip()
            bekannt_w = (trust_wartung == TRUST_VERIFIED
                         and not _EINZELBERICHT.search(beschreibung_w))
            motor_name = motor_match.get("bezeichnung") or "Motor"
            # Root-Cause-Closing (Befund E): der Titel lautete für JEDEN Eintrag
            # "kritischer Wartungspunkt", auch für ungeprüfte Community-Hinweise
            # ("cheap insurance"). Ob ein Punkt kritisch ist, sagt die Tabelle
            # nicht, sie heißt nur so. Der Titel nennt jetzt Art und Beleglage.
            if art == WARTUNG_REGULAER:
                art_titel = "Wartungspunkt" if bekannt_w else "hinterlegter Wartungshinweis"
            else:
                art_titel = "Hinweis" if bekannt_w else "gemeldeter Hinweis"
            if art == WARTUNG_REGULAER:
                einfluss_w = "Vor dem Kauf Durchführung und Nachweis klären."
            elif art == WARTUNG_VERSCHLEISS:
                einfluss_w = "Vor dem Kauf Zustand und letzten Tausch klären."
            else:
                einfluss_w = ("Vor dem Kauf klären, ob daran bereits gearbeitet wurde, und "
                              "Belege zeigen lassen.")
            if not bekannt_w:
                einfluss_w += " Nicht als Herstellervorgabe belegt."
            insights.append(Insight(
                id=_id("wartung"),
                kategorie="wartung",
                risk_type="wear" if art == WARTUNG_VERSCHLEISS else "maintenance",
                titel=f"{bauteil}: {art_titel} ({motor_name})",
                beschreibung=beschreibung_w,
                quellen_typen=_typen(quellen),
                quellen=quellen,
                # Root-Cause-Closing (Befund E): hier stand pauschal "hoch", weil die
                # Daten an der erkannten Motorvariante hängen. Das ist die
                # ZUORDNUNG, nicht die BELEGLAGE, und machte jeden der 1.476
                # ungeprüften Einträge zur scheinbar belastbaren Angabe (gemessen:
                # 0 davon verifiziert). Jetzt dieselbe Regel wie für Schwachstellen
                # und Motorprobleme. Die Variantenzuordnung ist die Applicability:
                # sie steht als `passt=True` darin, weil ein Wartungseintrag nur bei
                # eindeutig erkanntem Motor überhaupt entsteht.
                confidence=datenqualitaet(w, trust_wartung, True, beschreibung_w),
                trust=trust_wartung,
                einfluss=einfluss_w,
                bauteil=bauteil,
                fakt_ref=_fakt_ref("kritische_wartung", w),
                wartungsart=art,
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
            if not allowed({"bauteil": fakt.bauteil, "beschreibung": fakt.aussage}):
                continue
            if not fakt.quellen:
                continue          # ohne Quelle keine Evidence — nie
            # Final-Stabilization (Release-Hardening, Cluster A/B): ein Web-Rückruf
            # lief bisher nur durch die Baujahr-/Hubraum-/Leistungs-Prüfung des
            # ARTIKELS (app/technical_research.py::_artikel_geltung) — nie durch den
            # Varianten-Scope-Check (Kraftstoff/Antriebsart/Motorcode/Ausstattung),
            # den DB-Rückrufe über app.recall_filter.rueckruf_scope bereits
            # durchlaufen. Ein Diesel-only-Web-Rückruf konnte dadurch bei einem
            # sicher benzinbetriebenen Fahrzeug erscheinen. Beide Herkünfte (DB,
            # Web) laufen jetzt durch DIESELBE Scope-Funktion — keine zweite,
            # schwächere Parallelprüfung. Ein bekannter harter Widerspruch
            # (NOT_APPLICABLE) schließt den Web-Rückruf genauso aus wie einen
            # DB-Rückruf; ohne bekannten Gegenbeweis bleibt er sichtbar.
            if fakt.kategorie == "rueckruf":
                # `scope_text` (alle betroffenheits-tragenden Saetze des Artikels,
                # siehe app/technical_research.py) gibt der zentralen Policy mehr
                # als nur den einen Anzeige-Satz zu sehen — ein Scope im Nachbarsatz
                # bleibt so nicht unsichtbar. `_ausstattung`/`_freitext` wie beim
                # DB-Rückruf (oben) mitgeben, sonst bleibt der Ausstattungs-Teil
                # der Scope-Pruefung fuer Web-Rueckrufe strukturell blind, obwohl
                # dieselbe Funktion ihn fuer DB-Rueckrufe bereits auswertet.
                scope_state, _ = _rueckruf_scope(
                    {"mangel": fakt.aussage, "scope_text": getattr(fakt, "scope_text", None),
                     "_ausstattung": getattr(req, "ausstattung", None),
                     "_freitext": " ".join(str(getattr(req, f, None) or "")
                                           for f in ("beschreibung", "freitext"))},
                    identity)
                if scope_state == _RECALL_NOT_APPLICABLE:
                    log.info("Web-Rückruf '%s' entfällt: Variantenbedingung widerspricht "
                             "der kanonischen Identität.", fakt.bauteil)
                    continue
            insights.append(Insight(
                id=_id(f"web-{fakt.kategorie}"),
                kategorie=f"web_{fakt.kategorie}",
                risk_type=("recall" if fakt.kategorie == "rueckruf" else
                           "maintenance" if fakt.kategorie == "wartung" else "known_weakness"),
                titel=_WEB_TITEL[fakt.kategorie].format(
                    bauteil=(fakt.bauteil or "Fahrzeug").replace("_", " "))
                + (" (Geltung für dieses Baujahr nicht belegt)"
                   if getattr(fakt, "geltung_fuer_fahrzeug", None) == "unresolved" else ""),
                beschreibung=_web_beschreibung(fakt, baujahr),
                quellen_typen=_typen(fakt.quellen),
                quellen=list(fakt.quellen),
                # Confidence kommt aus der QUELLENLAGE (Anzahl unabhängiger Domains
                # + Tier), nie aus dem Inhalt — dieselbe Trennung wie oben.
                confidence=fakt.confidence,
                applicability=fakt.applicability,
                recall_status=recall_status(fakt.applicability) if fakt.kategorie == "rueckruf" else None,
                recall_state=(RECALL_STATE_AUS_APPLICABILITY.get(fakt.applicability)
                              if fakt.kategorie == "rueckruf" else None),
                geltungsbereich=getattr(fakt, "geltungsbereich", None),
                geltung_fuer_fahrzeug=getattr(fakt, "geltung_fuer_fahrzeug", None),
                # §11: Web-Evidence trägt eine echte Quellenlage (URL + Domain-
                # Qualität + Anzahl unabhängiger Domains) und bekommt deshalb eine
                # EIGENE Trust-Stufe — sie ist weder ein ungeprüfter DB-Satz noch
                # eine verifizierte Herstellerangabe. Floor-fähig ist sie NICHT,
                # siehe Begründung in app/empfehlungs_floor.py.
                trust=TRUST_WEB,
                einfluss=_WEB_EINFLUSS[fakt.kategorie],
                bauteil=((fakt.bauteil or "").replace("_", " ") or None),
            ))

    # ── 7) Kanonische Risikomenge (Root-Cause-Closing, Befund C/J) ─────────────
    # Dasselbe technische Thema aus verschiedenen Tabellen wird hier zu EINEM
    # Insight zusammengeführt, bevor irgendein Konsument es sieht: Evidence-
    # Karten, Key Findings, Prüfplan, Empfehlungsgründe, Laufleistung und der
    # LLM-Kontext arbeiten damit auf derselben finalen Menge. Die Evidenz wird
    # dabei nie erhöht (app/risikothemen.py). Nur KaufCheck: der Verkaufscheck
    # hat einen eigenen Evidence-Filter und bleibt unverändert.
    if check_typ == "kauf":
        insights = kanonisiere(insights)
        insights = wende_praesenz_an(insights, identity, req)

    return insights


_PRAESENZ_KATEGORIEN = ("schwachstelle", "motorproblem", "wartung", "rueckruf",
                        "web_schwachstelle", "web_wartung", "web_rueckruf")


def wende_praesenz_an(insights: list[Insight], identity, req) -> list[Insight]:
    """Präsenz abhängiger Komponenten EINMAL an der kanonischen Risikomenge
    festhalten (Cluster C, Invariante 5; app/ausstattung_praesenz.py).

      CONFIRMED_ABSENT  -> Insight entfällt (keine Karte, keine Aktion, kein Floor,
                           kein LLM-Kontext): DKG-Punkt am Schaltwagen, AdBlue am
                           Benziner, Hochvolt am Verbrenner.
      UNKNOWN           -> Titel wird bedingt ("Falls EDC vorhanden: …"); alle
                           Konsumenten (Karten, Key Findings, Prüfplan, Bericht,
                           Frontend) lesen genau dieses Insight.
      CONFIRMED_PRESENT -> unverändert.
    """
    ausstattung = getattr(req, "ausstattung", None)
    freitext = " ".join(str(getattr(req, f, None) or "") for f in ("beschreibung", "freitext"))
    out: list[Insight] = []
    for i in insights:
        if i.kategorie not in _PRAESENZ_KATEGORIEN:
            out.append(i)
            continue
        if i.kategorie in ("rueckruf", "web_rueckruf"):
            text = " ".join(filter(None, [i.kurztitel, i.beschreibung.split(" Abhilfe:")[0]]))
        else:
            text = " ".join(filter(None, [i.bauteil, i.titel.split(":")[0]]))
        state, abh, bez = praesenz(text, identity, ausstattung, freitext)
        if abh is None:
            out.append(i)
            continue
        if state == PRAESENZ_ABSENT:
            log.info("Kanonische Risikomenge: %s entfällt (%s nicht vorhanden)", i.id, abh.klasse)
            continue
        if state == PRAESENZ_UNKNOWN:
            praefix = praesenz_bedingung(bez, abh)
            titel = i.titel if i.titel.startswith("Falls ") else praefix + i.titel
            out.append(i.model_copy(update={"presence_state": PRAESENZ_UNKNOWN,
                                            "equipment_dependency": bez or abh.klasse,
                                            "titel": titel}))
            continue
        out.append(i.model_copy(update={"presence_state": PRAESENZ_PRESENT,
                                        "equipment_dependency": bez or abh.klasse}))
    return out


# Titel-/Einfluss-Vorlagen für Web-Evidence. Die Formulierung macht die Herkunft
# im Klartext sichtbar ("laut Webrecherche") — der Nutzer soll den Unterschied zur
# geprüften Fahrzeugdatenbank ohne Badge erkennen können.
_WEB_TITEL = {
    "schwachstelle": "{bauteil}: Hinweis aus der Webrecherche",
    "rueckruf": "Rückruf-Hinweis aus der Webrecherche ({bauteil})",
    "wartung": "{bauteil}: Wartungsangabe aus der Webrecherche",
}
def _web_beschreibung(fakt, baujahr) -> str:
    """Web-Aussage plus ihr Geltungsbereich — nie ein Fakt ohne Scope-Hinweis,
    wenn die Quelle selbst eingrenzt (Cluster I)."""
    text = fakt.aussage
    geltung = getattr(fakt, "geltung_fuer_fahrzeug", None)
    bereich = getattr(fakt, "geltungsbereich", None)
    if fakt.kategorie == "rueckruf" and fakt.applicability == "vehicle_possible":
        text += (f" Das Baujahr {baujahr or ''} liegt im genannten Produktionszeitraum: das "
                 "Fahrzeug ist möglicherweise betroffen. Nur die FIN-Prüfung klärt das.").replace("  ", " ")
    elif geltung == "unresolved":
        bereich = (bereich[:1].lower() + bereich[1:]) if bereich and not bereich[:1].isdigit() else bereich
        text += (f" Laut Quelle betrifft das {bereich or 'nur einen Teil der Fahrzeuge'}; ob dieses "
                 f"Fahrzeug{f' (Baujahr {baujahr})' if baujahr else ''} dazu gehört, ist nicht belegt.")
    elif geltung == "covered" and bereich:
        text += f" Laut Quelle betrifft das den Zeitraum {bereich}, der das Baujahr einschließt."
    return text


_WEB_EINFLUSS = {
    "schwachstelle": "Aus Webquellen belegt, nicht aus der "
                     "Fahrzeugdatenbank: vor dem Kauf gezielt prüfen.",
    "rueckruf": "Aus Webquellen belegt. Betroffenheit ausschließlich anhand der "
                "FIN beim Hersteller oder einer Vertragswerkstatt der Marke klären.",
    "wartung": "Aus Webquellen belegte Intervallangabe. Nachweis der Durchführung "
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
        beschr = ("Nur begrenzte, nicht eindeutig vergleichbare Web-Daten gefunden: "
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

# Root-Cause-Closing (Befund E): hier stand "(DB, geprüft)" an JEDER
# Datenbank-Aussage, auch an den 1.476 ungeprüften Wartungseinträgen und den
# ungeprüften Motorproblemen. Das Modell bekam einen Community-Hinweis damit als
# geprüfte Tatsache mit "Confidence: hoch" geliefert und begründete darauf die
# Kaufempfehlung. Das Label folgt jetzt der tatsächlichen Beleglage.
_EVIDENCE_TYP_LABEL = {
    "schwachstelle": "Schwachstelle",
    "rueckruf":      "Rückruf (KBA)",
    "motorproblem":  "Motorproblem",
    "marktvergleich": "Marktvergleich (Websuche)",
    "wartung":       "Wartungshinweis",
    "web_schwachstelle": "Schwachstelle (Webrecherche)",
    "web_wartung":   "Wartungsangabe (Webrecherche)",
    "web_rueckruf":  "Rückruf-Hinweis (Webrecherche)",
}
_DB_AUSSAGEN = ("schwachstelle", "motorproblem", "wartung")


def _evidence_label(i: Insight) -> str:
    basis = _EVIDENCE_TYP_LABEL.get(i.kategorie, i.kategorie)
    if i.kategorie in _DB_AUSSAGEN:
        herkunft = ("ENFAL-DB, geprüft" if i.trust == TRUST_VERIFIED
                    else "ENFAL-DB, ungeprüft: nur als gemeldeter Hinweis verwenden")
        return f"{basis} ({herkunft})"
    return basis


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
        "=== VERFÜGBARE EVIDENCE (Schicht A, vom Backend bereitgestellt) ===",
        "Referenziere in den *_evidence_ids-Feldern NUR IDs aus dieser Liste: sonst leere Liste.",
        "Bei Rückrufen (kategorie=rueckruf) gilt die angegebene Betroffenheits-Formulierung "
        "WÖRTLICH: schreibe NIEMALS 'betrifft dein Fahrzeug' ohne FIN-Prüfung.",
        "Confidence ist die BELEGLAGE der Aussage (hoch/mittel/niedrig), nicht ihre Schwere. "
        "Eine Aussage mit Confidence niedrig ist ein ungeprüfter, gemeldeter Hinweis: nenne "
        "sie so, stelle sie nie als festgestellte Tatsache, Pflichtwartung oder "
        "Herstellervorgabe dar und stütze die Kaufempfehlung nicht auf sie.",
    ]
    for i in insights:
        label = _evidence_label(i)
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
