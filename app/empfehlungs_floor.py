from __future__ import annotations

"""
Deterministischer Empfehlungs-Floor für den Kaufcheck.

PROBLEM (Bake-off Gemini 2.5 vs. 3.7): Das LLM liefert die Kaufempfehlung als
freien Enum-Wert. Es kann die im Systemprompt definierte Bedeutung von
`nur_mit_werkstattpruefung` ("bekannte, potenziell teure Schwachstellen der
Baureihe/Motorisierung vorhanden, die eine Fachprüfung vor Kauf erfordern")
korrekt vorlesen und im Bericht auch die passenden Risiken auflisten — und
trotzdem `kaufen_nach_besichtigung` in das strukturierte Feld schreiben. Eine
sicherheitsrelevante Mindestaussage darf nicht davon abhängen, ob ein
Sprachmodell seine eigene Definition diesmal anwendet.

LÖSUNG: Die MINDESTSTUFE der Empfehlung wird deterministisch aus den bereits
vorhandenen, geprüften Insights abgeleitet. Der Floor kann eine Empfehlung
ausschließlich in die VORSICHTIGERE Richtung anheben — er senkt nie.

WAS DEN FLOOR AUSLÖST (ausschließlich bereits existierende Projekt-Semantik,
keine neu erfundenen Schwellen):

  1. `motorproblem`-Insight vorhanden.
     Begründung im Projekt selbst: `key_findings.build_key_findings_kauf` erzeugt
     dafür die Aktion "Bauteil bei der WERKSTATTPRÜFUNG gezielt kontrollieren
     lassen". Die Werkstatt-Semantik ist dort bereits gesetzt — der Floor macht
     sie nur für die Empfehlung verbindlich.

  2. `schwachstelle`-Insight mit `schweregrad` in SCHWEREGRAD_WERKSTATT.
     Dieselbe Schweregrad-Menge wird bereits in `key_findings.build_key_findings_kauf`
     als "hoher Schweregrad" gewertet und in `evidence._einfluss_schwachstelle`
     mit "Erhöht das technische Kaufrisiko deutlich" belegt. Das ist wörtlich die
     Systemprompt-Definition von `nur_mit_werkstattpruefung`.

  3. `rueckruf`-Insight mit `applicability` in RUECKRUF_WERKSTATT_APPLICABILITY.
     `recall_filter.rueckruf_applicability` vergibt "variant_match" NUR bei
     Baujahr-Deckung UND plausibler, durch das KBA-Trust-Gate gelaufener
     Referenz und formuliert dafür ausdrücklich "Sicherheitsrelevant".

WAS BEWUSST NICHT AUSLÖST:

  * `series_only`-Rückrufe. Das ist die schwächere Stufe ("Für Teile der
    Baureihe gemeldet") und entsteht unter anderem genau dann, wenn die
    KBA-Referenz das Trust-Gate NICHT passiert hat oder die Baujahr-Zuordnung
    nicht eindeutig ist. Aus einer bewusst misstrauten Referenz eine
    Pflicht-Werkstattprüfung abzuleiten würde dem KBA-Trust-Gate widersprechen.
  * `unclear` / `incompatible`-Rückrufe (Betroffenheit nicht bestimmbar bzw.
    nachweislich nicht zutreffend).
  * Schwachstellen mit geringem/mittlerem Schweregrad — sonst landet praktisch
    jedes Gebrauchtfahrzeug auf `nur_mit_werkstattpruefung`.
  * `wartung`-Insights (P2-5). Ein Wartungspunkt bedeutet im Projekt
    ausdrücklich "an dieser Stelle den NACHWEIS verlangen" und gerade NICHT
    "fällig"/"überfällig" (app/laufleistung.py). Die dort erzeugte Aktion ist
    eine Dokumenten-/Verkäuferfrage, keine Werkstattprüfung. Den Floor daran zu
    hängen würde die in P2-5 bewusst entfernte Fälligkeits-Semantik durch die
    Hintertür wieder einführen.
  * Preis-/Marktsignale jeder Art. Der Floor ist vom Marktvergleich strukturell
    unabhängig und verhält sich bei `completed_no_market` identisch.

DATA-SAFETY-RUNTIME-GATE (P0) — die Trust-Vorbedingung
------------------------------------------------------
Alle drei Auslöser oben setzen voraus, dass der zugrunde liegende Fakt überhaupt
belastbar ist. Der DATA-TRUTH-AUDIT hat belegt, dass er das heute nicht ist:

  * `baureihe.verification` ist bei 421 von 421 Baureihen NULL, die Tabelle
    `quelle` enthält 0 Zeilen. Für KEINEN der rund 6.500 Faktensätze existiert
    eine gespeicherte Provenance.
  * `schwachstelle_motor` hat kein Schweregradfeld. Deshalb löste bisher JEDE
    der 2.766 Zeilen den Floor aus — eine defekte Zündspule (468 Zeilen sind
    leichte Verschleiß-/Sensorteile) genauso wie ein Kolbenringschaden. Betroffen
    waren 1.585 von 3.248 Motorvarianten (48,8 %).
  * 78,8 % der Motorproblem-Zeilen tragen keine Baujahresangabe; sie wirkten
    trotzdem floor-auslösend, weil der Floor die Confidence nie gelesen hat.
  * Kein einziger DB-Rückruf ließ sich gegen eine amtliche Quelle bestätigen.
    Das KBA-Trust-Gate prüft Format und Kollisionsfreiheit einer Nummer — das ist
    Plausibilität, NICHT inhaltliche Verifikation.
  * Motorfremde Schwachstellen hoben die Empfehlung nachweislich an (BMW 320i mit
    "Steuerkette (N47 Dieselmotoren)", BMW 525i mit "V10-Motor (M5)", Audi A3
    1.6 MPI mit "Turbolader (TFSI-Motoren)").

Der letzte Punkt ist inzwischen an der Wurzel behoben (app/motor_applicability.py
entfernt solche Sätze vollständig). Die übrigen betreffen die BELEGLAGE, nicht die
Zuordnung — und die lässt sich zur Laufzeit nicht heilen. Deshalb gilt ab jetzt:

    Nur ein Insight mit `trust == "verified"` kann den Floor auslösen.

`trust` kommt aus der BESTEHENDEN Verifikations-Architektur
(app/verification.py -> app/evidence.py::_trust_der_baureihe) und wird nicht neu
erfunden. Solange keine Baureihe einen `verification`-Eintrag trägt, greift der
Floor faktisch nicht mehr — das ist die gewollte, ehrliche Konsequenz und KEIN
Funktionsverlust an anderer Stelle: Findings, Key Findings und alle vier
Prüflisten entstehen unverändert weiter. Es entfällt ausschließlich die
automatische Verschärfung der Kaufempfehlung auf Basis unbelegter Daten.

Sobald für eine Baureihe `verification` gepflegt ist (Status "verified" MIT
gespeicherter `source`), wirkt der Floor für deren Fakten sofort wieder — ohne
Codeänderung. Genau dafür ist die Architektur erhalten geblieben.

WARUM WEB-EVIDENCE DEN FLOOR NICHT TRÄGT (§11, bewusste Entscheidung)
---------------------------------------------------------------------
Die technische Webrecherche liefert eine echte Quellenlage: `confidence == "hoch"`
bedeutet dort ≥3 unabhängige Domains bei einem Domain-Score ≥40
(app/technical_research.py::_confidence_aus_domains). Das ist belastbarer als ein
unbelegter DB-Satz — aber es ist eine Aussage über die BELEGLAGE, nicht über die
SCHWERE. Ein `web_schwachstelle`-Fakt hat keinen Schweregrad, und `applicability`
wird dort nur für Rückrufe gesetzt (und zwar auf "series_only", das schon bisher
nicht auslöst). Die Systemprompt-Definition von `nur_mit_werkstattpruefung`
verlangt aber ausdrücklich "potenziell teure Schwachstellen …, die eine Fachprüfung
erfordern" — also eine Schwereaussage. Sie ließe sich aus den vorhandenen Feldern
nur durch eine neu erfundene Schwelle herstellen. Deshalb: nein. Web-Evidence
erzeugt weiterhin Findings und Prüfpunkte, aber keine Mindestempfehlung.
"""

import logging
from dataclasses import dataclass, field

from app.models import Insight

log = logging.getLogger(__name__)

# ── Enum-Werte (ausschließlich die im Systemprompt definierten) ──────────────
KAUFEN = "kaufen"
KAUFEN_NACH_BESICHTIGUNG = "kaufen_nach_besichtigung"
NUR_MIT_WERKSTATTPRUEFUNG = "nur_mit_werkstattpruefung"
PREIS_NACHVERHANDELN = "preis_nachverhandeln"
HOHES_RISIKO = "hohes_risiko"
FINGER_WEG = "finger_weg"
UNBEKANNT = "unbekannt"

# ── Rangfolge auf der TECHNISCHEN Vorsichts-Achse (höher = vorsichtiger) ─────
# Kein String-Vergleich, sondern eine explizite Ordnung über genau die
# existierenden Enum-Werte.
#
# `preis_nachverhandeln` liegt bewusst auf DERSELBEN Stufe wie
# `kaufen_nach_besichtigung`: der Systemprompt definiert es als "Fahrzeug
# TECHNISCH UNAUFFÄLLIG, aber Preis teuer" — sein technischer Gehalt ist also
# identisch. Der Kaufcheck behandelt es an anderer Stelle bereits genau so:
# fehlen die Marktdaten, wird `preis_nachverhandeln` auf
# `kaufen_nach_besichtigung` reduziert (app/kaufcheck.py, PFAD B). Hebt der
# Floor es an, geht keine Information verloren — die Preisaussage steht
# unverändert im eigenen Feld `preis_bewertung` und im Preis-Finding.
_RANG: dict[str, int] = {
    KAUFEN: 1,
    KAUFEN_NACH_BESICHTIGUNG: 2,
    PREIS_NACHVERHANDELN: 2,
    NUR_MIT_WERKSTATTPRUEFUNG: 3,
    HOHES_RISIKO: 4,
    FINGER_WEG: 5,
}

# Schweregrade, die im Projekt bereits als "hoch" gelten
# (key_findings.build_key_findings_kauf, evidence._einfluss_schwachstelle).
SCHWEREGRAD_WERKSTATT = ("hoch", "kritisch", "sehr hoch")

# Nur die stärksten Betroffenheits-Stufen (recall_filter.rueckruf_applicability).
# "confirmed_by_vin" wird vom Code aktuell nie erzeugt, ist aber der definierte
# stärkere Wert und daher vollständigkeitshalber enthalten.
RUECKRUF_WERKSTATT_APPLICABILITY = ("confirmed_by_vin", "variant_match")

# Grund-Codes für die Nachvollziehbarkeit (§8) — stabil, testbar, nicht für den
# Nutzertext gedacht.
GRUND_MOTORPROBLEM = "motorproblem"
GRUND_SCHWACHSTELLE_HOCH = "schwachstelle_hoher_schweregrad"
GRUND_RUECKRUF_VARIANTENTREFFER = "rueckruf_variantentreffer"

# Die EINZIGE Trust-Stufe, die eine harte Mindestempfehlung tragen darf.
# Absichtlich als Tupel: kämen später weitere belastbare Herkünfte hinzu, wird
# hier EIN Wert ergänzt statt an drei Stellen eine Bedingung geändert.
TRUST_FLOOR_FAEHIG = ("verified",)


def darf_floor_tragen(insight: Insight) -> bool:
    """Darf dieser Fakt allein die Kaufempfehlung verschärfen?

    Genau eine Frage, genau eine Stelle. Der Default von `Insight.trust` ist
    "unverified_db" — eine Evidence, die ihre Herkunft nicht ausdrücklich setzt,
    kann den Floor also nicht versehentlich auslösen.
    """
    return (getattr(insight, "trust", None) or "") in TRUST_FLOOR_FAEHIG


@dataclass
class FloorBefund:
    """Warum der Floor greift — ausschließlich über EXISTIERENDE Insight-IDs.

    `stufe` ist der geforderte Mindestwert, `gruende` die auslösenden Codes,
    `evidence_ids` die belegenden Insight-IDs (echte IDs aus genau diesem Check —
    der Floor erfindet keine).
    """
    stufe: str
    gruende: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)


def _rang(empfehlung: str | None) -> int | None:
    """Rang auf der Vorsichts-Achse, oder None wenn der Wert nicht darauf liegt."""
    return _RANG.get((empfehlung or "").strip().lower())


def ist_vorsichtiger(a: str | None, b: str | None) -> bool:
    """True, wenn `a` strikt vorsichtiger ist als `b`. Werte außerhalb der
    Rangfolge (inkl. "unbekannt") sind nie vergleichbar."""
    ra, rb = _rang(a), _rang(b)
    if ra is None or rb is None:
        return False
    return ra > rb


def ermittle_floor(insights: list[Insight] | None) -> FloorBefund | None:
    """Leitet die deterministische MINDESTEMPFEHLUNG aus den Insights ab.

    Gibt None zurück, wenn keine der oben dokumentierten harten Bedingungen
    zutrifft — dann bleibt die LLM-Empfehlung unangetastet.
    """
    if not insights:
        return None

    gruende: list[str] = []
    ids: list[str] = []

    # DATA-SAFETY-RUNTIME-GATE: `darf_floor_tragen` steht in JEDER der drei
    # Bedingungen. Ein unbelegter DB-Fakt bleibt vollständig sichtbar (Findings,
    # Prüflisten, LLM-Kontext) — er verschärft nur die Empfehlung nicht mehr.
    floor_faehig = [i for i in insights if darf_floor_tragen(i)]

    # VERIFICATION-PILOT (§8) — OFFENER PUNKT, hier bewusst NICHT geaendert:
    # `schwachstelle_motor` hat kein Schweregradfeld, deshalb behandelt diese Regel
    # eine defekte Zuendspule wie einen Kolbenringschaden (der DATA-TRUTH-AUDIT hat
    # 468 von 2.766 Zeilen = 16,9 % als leichte Verschleiss-/Sensorteile gemessen).
    # Verifikation behebt die BELEGLAGE, nicht die fehlende SCHWERE. Im Pilot ist
    # das folgenlos, weil kein Motorproblem den Status `verified` erreicht hat —
    # die Frage ist damit gestellt, aber nicht eigenmaechtig entschieden.
    motorprobleme = [i for i in floor_faehig if i.kategorie == "motorproblem"]
    if motorprobleme:
        gruende.append(GRUND_MOTORPROBLEM)
        ids += [i.id for i in motorprobleme]

    schwach_hoch = [
        i for i in floor_faehig
        if i.kategorie == "schwachstelle"
        and (i.schweregrad or "").strip().lower() in SCHWEREGRAD_WERKSTATT
    ]
    if schwach_hoch:
        gruende.append(GRUND_SCHWACHSTELLE_HOCH)
        ids += [i.id for i in schwach_hoch]

    rueckrufe = [
        i for i in floor_faehig
        if i.kategorie == "rueckruf"
        and (i.applicability or "") in RUECKRUF_WERKSTATT_APPLICABILITY
    ]
    if rueckrufe:
        gruende.append(GRUND_RUECKRUF_VARIANTENTREFFER)
        ids += [i.id for i in rueckrufe]

    if not gruende:
        return None

    # Reihenfolge erhalten, Duplikate raus.
    eindeutig: list[str] = []
    for x in ids:
        if x not in eindeutig:
            eindeutig.append(x)
    return FloorBefund(stufe=NUR_MIT_WERKSTATTPRUEFUNG, gruende=gruende,
                       evidence_ids=eindeutig)


def wende_floor_an(empfehlung: str | None,
                   insights: list[Insight] | None) -> tuple[str | None, FloorBefund | None]:
    """Hebt `empfehlung` auf die deterministische Mindeststufe an, falls nötig.

    Rückgabe: (finale Empfehlung, Befund oder None). Der Befund ist nur dann
    gesetzt, wenn tatsächlich ANGEHOBEN wurde — so bleibt im Aufrufer
    unterscheidbar, ob der Floor gegriffen hat.

    Garantien:
      * senkt NIE (ist die LLM-Empfehlung bereits gleich oder vorsichtiger,
        bleibt sie unverändert);
      * lässt "unbekannt" unangetastet. "unbekannt" ist kein Punkt auf der
        Vorsichts-Achse, sondern die Aussage "keine Empfehlung möglich" (fehlende
        Kerndaten, Widerspruch im Inserat, Fantasiefahrzeug). Daraus eine
        konkrete Empfehlung zu machen hieße, eine Aussage zu erfinden, die die
        Datenlage nicht hergibt;
      * lässt unbekannte/fehlerhafte Werte unangetastet — ein Wert außerhalb der
        Rangfolge ließe sich nicht vergleichen, und ein Überschreiben könnte
        versehentlich eine VORSICHTIGERE Angabe abschwächen.
    """
    befund = ermittle_floor(insights)
    if befund is None:
        return empfehlung, None

    aktuell = (empfehlung or "").strip().lower()
    if not aktuell or aktuell == UNBEKANNT:
        return empfehlung, None
    if _rang(aktuell) is None:
        log.info("Empfehlungs-Floor: Empfehlung %r liegt nicht auf der Rangfolge — "
                 "unveraendert gelassen.", empfehlung)
        return empfehlung, None
    if not ist_vorsichtiger(befund.stufe, aktuell):
        return empfehlung, None

    log.info("Empfehlungs-Floor greift: %r -> %r (Gruende: %s, Evidence: %s)",
             aktuell, befund.stufe, ", ".join(befund.gruende),
             ", ".join(befund.evidence_ids))
    return befund.stufe, befund
