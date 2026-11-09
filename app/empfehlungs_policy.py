from __future__ import annotations

"""
EINE zentrale Empfehlungs-Policy: keine Empfehlung ist sicherer als die
Fahrzeugidentität, auf der sie steht (KaufCheck-Final-Stabilization, Cluster J).

BEFUND (Production-Run Mazda MX-5, DB-Miss)
-------------------------------------------
Der Bericht zeigte "Fahrzeugidentität eingeschränkt", Generation, Motorcode
und Antriebsart "nicht sicher bekannt" — und trotzdem "KAUFEN NACH
BESICHTIGUNG".

ROOT CAUSE (im echten Produktionspfad `run_kaufcheck`)
------------------------------------------------------
1. Die Empfehlung wurde fest auf "kaufen_nach_besichtigung" gesetzt, sobald
   Marke, Modell und Baujahr im Request standen.
2. Der vorherige "Identitäts-Floor" prüfte nur zwei Booleans: DB-Treffer
   belastbar ODER `web_recherche.identitaet.belegt`. "belegt" bedeutete
   lediglich: Marke und Modell stehen als Token auf zwei Domains. Für einen
   MX-5 ist das praktisch immer wahr — der Floor griff nie, obwohl Generation
   und Motorisierung unaufgelöst blieben.
3. Die Überschrift ("Fahrzeugidentität eingeschränkt") und die Empfehlung
   wurden aus VERSCHIEDENEN Signalen berechnet und konnten sich widersprechen.

REGEL
-----
Die Identitätsstufe wird aus der KANONISCHEN Identität berechnet (dieselbe,
die der Bericht anzeigt):

  verified      Marke+Modell unabhängig bestätigt (DB oder Web) UND Generation
                bekannt UND Motorisierung belegt (DB-Variante oder
                Web-bestätigte Leistung).
  partial       Marke+Modell bestätigt, Generation oder Motorisierung offen.
  insufficient  Marke+Modell nicht unabhängig bestätigt.

Nur `verified` erlaubt eine normale Empfehlung. Sonst:
  recommendation_state = LIMITED_ANALYSIS bzw. INSUFFICIENT_IDENTITY,
  Empfehlung "unbekannt" ("keine Empfehlung möglich"). Eine VORSICHTIGERE
  Stufe (Werkstattprüfung, hohes Risiko), die der Floor aus belegten Risiken
  gesetzt hat, bleibt erhalten — Vorsicht ist nie "zu selbstsicher".
Wird die Identität später über Webquellen ausreichend bestätigt, ist die
normale Empfehlung wieder erlaubt — ohne Sonderpfad.
"""

from dataclasses import dataclass, field

VERIFIED = "verified"
PARTIAL = "partial"
INSUFFICIENT = "insufficient"

STATE_NORMAL = "NORMAL"
STATE_LIMITED = "LIMITED_ANALYSIS"
STATE_INSUFFICIENT = "INSUFFICIENT_IDENTITY"

# Doppelpunkt statt Gedankenstrich: die zentrale Schreibstil-Regel
# (app/schreibstil.py) gilt auch für diese Anzeige.
ANZEIGE_LIMITED = "Analyse eingeschränkt: Fahrzeugvariante nicht vollständig verifiziert"
ANZEIGE_INSUFFICIENT = "Analyse eingeschränkt: Fahrzeugidentität nicht bestätigt"
# Release-Hardening (Research Confidence): eigene Anzeige für den Fall, dass
# die Identität selbst voll bestätigt ist, aber eine sicherheits-/technisch
# relevante Web-Recherchephase nicht abgeschlossen werden konnte — NICHT
# dieselbe Ursache wie ANZEIGE_LIMITED (dort fehlt die Fahrzeugvariante
# selbst), ANZEIGE_LIMITED hier zu verwenden wäre irreführend.
ANZEIGE_LIMITED_FORSCHUNG = "Analyse eingeschränkt: Recherche nicht vollständig abgeschlossen"

# Empfehlungen, die eine Freigabe ausdrücken. Nur diese werden bei
# unvollständiger Identität zurückgenommen.
_MILDE = {"kaufen", "kaufen_nach_besichtigung", "preis_nachverhandeln"}

_ANZEIGE_NORMAL = {
    "kaufen": "Kaufen", "kaufen_nach_besichtigung": "Kaufen nach Besichtigung",
    "nur_mit_werkstattpruefung": "Nur mit Werkstattprüfung",
    "preis_nachverhandeln": "Preis nachverhandeln", "hohes_risiko": "Hohes Risiko",
    "finger_weg": "Finger weg", "unbekannt": "Keine Empfehlung möglich",
}

_BESTAETIGT = ("user_confirmed", "user_refined", "reference_only", "plausible", "web_only")


def _bestaetigt(fe: dict | None) -> bool:
    fe = fe or {}
    if fe.get("verification_state") in _BESTAETIGT:
        return True
    return bool(set(fe.get("confirmed_by") or []) & {"enfal", "web"})


@dataclass(frozen=True)
class IdentitaetsAufloesung:
    stufe: str
    fehlend: tuple[str, ...] = ()
    quellen: tuple[str, ...] = field(default_factory=tuple)


def identitaet_aufloesen(identity) -> IdentitaetsAufloesung:
    fe = identity.field_evidence if identity is not None else {}
    quellen = sorted({q for e in fe.values() for q in ([e.get("primary_source")]
                      + list(e.get("confirmed_by") or [])) if q in ("enfal", "web")})
    if not (_bestaetigt(fe.get("make")) and _bestaetigt(fe.get("model"))
            and identity.make and identity.model):
        return IdentitaetsAufloesung(INSUFFICIENT, ("Marke/Modell",), tuple(quellen))
    fehlend: list[str] = []
    # Generation, Facelift, Modelljahr und Motorcode sind UNTERSCHIEDLICHE
    # Dinge (RC-F-Folgeauftrag, 2026-10-25) -- dieser Block bleibt bewusst
    # unverändert: Generation wird NUR durch eine eigene, bestätigte
    # Generationsangabe belegt, NIE durch PS oder Motorcode (keine Lockerung
    # des bestehenden Gates).
    if not (identity.generation and _bestaetigt(fe.get("generation"))):
        fehlend.append("Generation")

    # Generation/Motorisierung-Asymmetrie (RC-F-Folgeauftrag, 2026-10-25,
    # Ursachenanalyse-Bericht vom selben Datum): bestätigte LEISTUNG (PS)
    # beantwortet nicht "welcher Motor" -- mehrere, unterschiedliche Motoren
    # können dieselbe PS-Zahl haben. `motor_belegt` darf deshalb NICHT mehr
    # allein über `horsepower` wahr werden (bisherige Formulierung tat genau
    # das) -- das erklärte am realen Lexus-RC-F-Lauf, warum nur "Generation"
    # als Blocker genannt wurde, obwohl Motor/Kraftstoff/Getriebe/Antrieb
    # ebenfalls unbestätigt blieben. "Motorisierung" braucht jetzt eine
    # ECHTE Motor-Identität: Motorbezeichnung ODER Motorcode, jeweils
    # tatsächlich bestätigt (nicht nur user_only/reine Nutzer-/Modell-
    # referenz) -- ENFAL- und Web-Provenienz zählen GLEICH (die bisherige
    # "enfal in provenance"-Bedingung schloss einen rein web-bestätigten
    # Motor aus, ohne fachlichen Grund). Getriebe/Antriebsart bleiben davon
    # UNBERÜHRT -- sie sind eigene, separat im Bericht ausgewiesene
    # Applicability-Achsen (app/vergleichstabelle.py), kein zusätzliches
    # Pflichtkriterium für DIESE Gate-Stufe.
    def _motor_merkmal_belegt(feld: str) -> bool:
        wert = getattr(identity, feld, None)
        eintrag = fe.get(feld) or {}
        return bool(wert) and _bestaetigt(eintrag) and eintrag.get("verification_state") != "user_only"

    motor_belegt = _motor_merkmal_belegt("engine_name") or _motor_merkmal_belegt("engine_code")
    if not motor_belegt:
        fehlend.append("Motorisierung")
    return IdentitaetsAufloesung(VERIFIED if not fehlend else PARTIAL, tuple(fehlend),
                                 tuple(quellen))


def motorisierung_beleg_text(identity) -> str:
    """Benennt, WAS die Motorisierungsprüfung von `identitaet_aufloesen` trägt
    (reine Anzeige, gleiche Bedingung wie dort; ändert keine Stufe)."""
    fe = identity.field_evidence if identity is not None else {}

    def belegt(feld: str) -> bool:
        eintrag = fe.get(feld) or {}
        return bool(getattr(identity, feld, None)) and _bestaetigt(eintrag) \
            and eintrag.get("verification_state") != "user_only"

    code = identity.engine_code if belegt("engine_code") else None
    name = identity.engine_name if belegt("engine_name") else None
    if code and name:
        return f"Motorcode {code} und Motorbezeichnung „{name}“ belegt"
    if name:
        return f"Motorbezeichnung „{name}“ belegt, Motorcode nicht belegt"
    if code:
        teile = (fe.get("engine_name") or {}).get("bestaetigte_teile")
        if teile:
            return (f"Motorcode {code} belegt; von der Motorangabe nur „"
                    + ", ".join(teile) + "“ bestätigt, übrige Angabe nicht geprüft")
        return (f"nur über den Motorcode {code} belegt; die angegebene "
                "Motorbezeichnung (Hubraum, Bauart) ist dadurch nicht bestätigt")
    return ""


@dataclass(frozen=True)
class Empfehlungsentscheidung:
    empfehlung: str
    state: str
    anzeige: str
    hinweis: str | None
    identitaet: IdentitaetsAufloesung
    # N18: Datenarten, zu denen die Technikrecherche lief, aber nichts Belastbares
    # lieferte. Leer = keine solche Lücke. Reine Anzeige-Information; Stufe und
    # `state` hängen NICHT daran.
    technik_luecke: tuple[str, ...] = ()


# ── Release-Hardening (Research Confidence) ─────────────────────────────────
#
# BEFUND: eine VOLL BESTÄTIGTE Identität (Marke/Modell/Generation/Motorisierung
# belegt) konnte trotzdem auf einer Web-Recherche beruhen, bei der die
# Rückruf- oder Technik-Phase NICHT erfolgreich ausgeführt wurde
# (TechnischeRecherche.phasen_status, app/technical_research.py) — der
# Bericht zeigte dann dieselbe Sicherheit wie bei vollständig abgeschlossener
# Recherche. "0 Rückrufe gefunden" (Phase erfolgreich, Ergebnis leer) und
# "Rückrufrecherche konnte nicht ausgeführt werden" (Phase fehlgeschlagen)
# duerfen nicht dieselbe Empfehlungssicherheit tragen.
#
# REGEL: nur FAILED senkt die Stufe — PARTIAL (duenne, aber vorhandene
# Abdeckung) hat bereits eine eigene, niedrigere Confidence auf jedem
# einzelnen Web-Insight (app/evidence.py) und braucht keine zusätzliche
# Eskalation hier; NOT_RUN bedeutet entweder "Identität hat nicht gereicht"
# (dann greift bereits der Zweig unten) oder "kein Web-Fallback noetig"
# (reiner DB-Treffer — kein Forschungsproblem). Die Empfehlung selbst bleibt
# UNVERÄNDERT (kein automatisches "Finger weg", kein Zuruecksetzen auf
# "unbekannt" wie bei unzureichender Identität) — nur `state` und `hinweis`
# machen die Lücke sichtbar.
_FORSCHUNGSPHASEN_LABEL = {
    "rueckruf": "Die Rückrufrecherche über Webquellen konnte nicht vollständig ausgeführt werden",
    "technik": "Die Recherche zu technischen Schwachstellen über Webquellen konnte nicht "
              "vollständig ausgeführt werden",
}


def forschung_unvollstaendig_hinweis(web_recherche) -> str | None:
    """None, wenn keine sicherheits-/technisch relevante Recherchephase
    fehlgeschlagen ist (FAILED). Sonst ein Hinweistext, der GENAU benennt,
    welche Phase betroffen ist — nie eine pauschale Unsicherheitsfloskel."""
    status = getattr(web_recherche, "phasen_status", None) or {}
    betroffen = [n for n in ("rueckruf", "technik") if status.get(n) == "failed"]
    if not betroffen:
        return None
    saetze = ". ".join(_FORSCHUNGSPHASEN_LABEL[n] for n in betroffen)
    return (f"{saetze}. Das bedeutet NICHT, dass hierzu nichts vorliegt: die vorhandene "
           f"Einschätzung bleibt bestehen, gilt aber nur so weit wie die tatsächlich "
           f"abgeschlossene Recherche — die offene Prüfung sollte separat nachgeholt werden.")


# ── N18: Technikrecherche gelaufen, aber weder Schwachstellen noch Wartung ─────
#
# BEFUND (Live-Bericht 20261108-091413, Lexus RC F): Identität belegt, die
# Technikrecherche lief (partial), lieferte aber 0 Schwachstellen- und 0
# Wartungsbefunde. Die Überschrift zeigte trotzdem unverändert "KAUFEN NACH
# BESICHTIGUNG", die Begründung nannte das eine "rein technische Einschätzung":
# fehlende Abdeckung sah aus wie ein unauffälliger technischer Befund.
#
# REGEL: gilt nur für den bisherigen Kandidaten "kaufen_nach_besichtigung" bei
# VERIFIED-Identität ohne fehlgeschlagene Recherchephase (Vorrang haben die
# strengeren Warnungen). Nur die ANZEIGE und der Hinweis ändern sich; Stufe
# (`empfehlung`), `state`, Floor und Identitätsprüfung bleiben unberührt.
# "Befund vorhanden" heißt: ein Insight der Kategorie, dessen Trust nicht
# "unverified_db" ist (geprüfter DB-Befund oder Webbefund), ODER ein Web-Fakt der
# Kategorie. Ungeprüfte DB-Einträge zählen nicht.
ANZEIGE_TECHNIK_EINGESCHRAENKT = ("Analyse eingeschränkt: keine bestätigten Schwachstellen- "
                                  "und Wartungsdaten")
_SCHWACHSTELLEN_KATEGORIEN = ("schwachstelle", "motorproblem", "web_schwachstelle")
_WARTUNGS_KATEGORIEN = ("wartung", "web_wartung")


def technik_datenluecke(web_recherche, insights=None) -> tuple[str, ...]:
    """("Schwachstellen", "Wartung"), wenn die Technikphase lief (SUCCESS/PARTIAL)
    und WEDER Schwachstellen- NOCH Wartungsbefunde vorliegen, sonst ()."""
    status = getattr(web_recherche, "phasen_status", None) or {}
    if status.get("technik") not in ("success", "partial"):
        return ()
    fakt_kat = {getattr(f, "kategorie", None) for f in (getattr(web_recherche, "fakten", None) or [])}
    # Ein DB-Insight zählt nur als Befund, wenn sein Trust (Verifikations-Architektur,
    # app/evidence.py) nicht "unverified_db" ist. Der Default eines Insights ohne
    # gesetzten Trust ist "unverified_db": ein ungeprüfter Datenbankeintrag hebt die
    # Einschränkung nicht auf (er bleibt als Hinweis sichtbar, belegt aber nichts).
    insight_kat = {getattr(i, "kategorie", None) for i in (insights or [])
                   if (getattr(i, "trust", None) or "unverified_db") != "unverified_db"}
    vorhanden = fakt_kat | insight_kat
    if vorhanden & (set(_SCHWACHSTELLEN_KATEGORIEN) | set(_WARTUNGS_KATEGORIEN)):
        return ()
    return ("Schwachstellen", "Wartung")


_TECHNIK_LUECKE_HINWEIS = (
    "Die Technikrecherche lief, ergab aber weder belastbare Schwachstellen- noch belastbare "
    "Wartungsdaten. Das ist keine Entwarnung: zu Schwachstellen und Wartung liegt keine "
    "belegte Aussage vor. Die Stufe „Kaufen nach Besichtigung“ sagt dazu nichts aus.")


def entscheide(kandidat: str | None, identity, *, web_recherche=None,
               insights=None) -> Empfehlungsentscheidung:
    """Die finale Empfehlung — die EINZIGE Stelle, die über den Zustand
    entscheidet. Bericht, API-Feld und Frontend lesen dieses Ergebnis."""
    aufl = identitaet_aufloesen(identity)
    kandidat = (kandidat or "unbekannt").strip().lower()
    if aufl.stufe == VERIFIED:
        forschungs_hinweis = forschung_unvollstaendig_hinweis(web_recherche)
        if forschungs_hinweis is None:
            luecke = (technik_datenluecke(web_recherche, insights)
                      if kandidat == "kaufen_nach_besichtigung" else ())
            if luecke:
                return Empfehlungsentscheidung(kandidat, STATE_NORMAL, ANZEIGE_TECHNIK_EINGESCHRAENKT,
                                               _TECHNIK_LUECKE_HINWEIS, aufl, technik_luecke=luecke)
            return Empfehlungsentscheidung(kandidat, STATE_NORMAL,
                                           _ANZEIGE_NORMAL.get(kandidat, kandidat), None, aufl)
        # Empfehlung bleibt UNVERÄNDERT — nur state/anzeige/hinweis machen die
        # unvollständige Recherche sichtbar (siehe Regel oben).
        return Empfehlungsentscheidung(
            kandidat, STATE_LIMITED,
            f"{_ANZEIGE_NORMAL.get(kandidat, kandidat)} ({ANZEIGE_LIMITED_FORSCHUNG})",
            forschungs_hinweis, aufl)
    state = STATE_LIMITED if aufl.stufe == PARTIAL else STATE_INSUFFICIENT
    anzeige_basis = ANZEIGE_LIMITED if aufl.stufe == PARTIAL else ANZEIGE_INSUFFICIENT
    offen = ", ".join(aufl.fehlend)
    # Package D5 (Forensik-Folgeauftrag): "Nicht sicher belegt: Generation."
    # kann wie "alles andere ist sicher" gelesen werden, obwohl weitere
    # Felder (Baujahr/Kraftstoff/Getriebe) separat in der Feldliste/
    # Vergleichstabelle als user_only/unbekannt ausgewiesen werden können.
    # Der hier benannte Scope (Marke/Modell/Generation/Motorisierung) bleibt
    # bewusst UNVERÄNDERT — nur die Formulierung macht deutlicher, dass dies
    # die BLOCKIERENDE Lücke für die Empfehlungsstufe ist, keine vollständige
    # Unsicherheitsliste.
    hinweis = (f"Blockierende Identitätslücke: {offen}. Fahrzeugspezifische Aussagen sind "
               f"deshalb eingeschränkt; eine Kaufempfehlung ist erst nach Klärung "
               f"(Fahrzeugschein, FIN) möglich.")
    if kandidat in _MILDE or kandidat == "unbekannt":
        return Empfehlungsentscheidung("unbekannt", state, anzeige_basis, hinweis, aufl)
    # Vorsichtigere Stufe bleibt, der Zustand bleibt trotzdem sichtbar begrenzt.
    return Empfehlungsentscheidung(kandidat, state,
                                   f"{_ANZEIGE_NORMAL.get(kandidat, kandidat)} ({anzeige_basis})",
                                   hinweis, aufl)
