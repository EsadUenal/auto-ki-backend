from __future__ import annotations

"""
Technischer Web-Fallback — "DB FIRST, aber niemals DB ONLY".

Der DATA-TRUST-AUDIT hat belegt: Web war im Kaufcheck ursprünglich KEIN
strukturierter Fallback. Fehlte das DB-Profil, fehlte die technische Analyse
komplett. Dieses Modul schließt genau diese Lücke — und nur diese.

ABGRENZUNG ZUM MARKTPROVIDER (bewusst zwei getrennte Schichten)
  MarketDataProvider (app/market_data_provider.py) — Vergleichsangebote/Preise.
  TechnicalVehicleResearchProvider (hier) — Identität und Technik. Kennt keinen
  Preis, liefert keinen und darf keinen produzieren.

ARCHITEKTUR (KaufCheck-Final-Stabilization, Cluster H/I/M)
-----------------------------------------------------------
BEFUND (Production-Run Mazda MX-5, DB-Miss): die Recherche fand "MX-5 ND,
4. Generation", die kanonische Identität meldete trotzdem Generation, Motorcode
und Antriebsart "unbekannt". Ursache: vier Suchen (Identität, Schwachstellen,
Rückrufe, Wartung) liefen GLEICHZEITIG; die "Identität" prüfte nur, ob Marke
und Modell als Tokens auf zwei Domains stehen, extrahierte aber nichts. Die
Nutzerangabe "motor" wurde als Web-Motor ausgegeben (Umetikettierung).
Schwachstellen wurden gesammelt, bevor feststand, WELCHES Fahrzeug gemeint ist
— eine Aussage über frühe Getriebegenerationen landete als Schwachpunkt eines
2019er-Fahrzeugs.

Jetzt drei Phasen in fester Reihenfolge:

  PHASE 1 — IDENTITÄT. Nur Identitätsanfragen. Claims (Generation/Code,
      Bauzeitraum, Leistung, Hubraum, Kraftstoff, Antrieb, Getriebe, Motorcode)
      werden per Regex aus den QUELLENTEXTEN gelesen — jede Quelle muss das
      Zielfahrzeug betreffen (Entity-Alignment: Marke+Modell im Titel bzw. kein
      fremdes Fahrzeug als Hauptthema; Generationscode nur mit passendem
      Baujahresfenster). Konsens: gewichtet nach Quellenstufe (TIER 1 Hersteller/
      Behörde = 3, TIER 2 Fachmedien/ADAC/Technik = 2, TIER 3 Rest = 1), ein Wert
      braucht mindestens eine TIER-1/2-Quelle und Gewicht >= 3; ein
      widersprechender Wert mit vergleichbarem Gewicht macht das Feld UNKNOWN
      (keine Mehrheitsentscheidung gleich schwacher Quellen).
  PHASE 2 — RÜCKRUF (nur nach belegter Identität). Rückrufanfragen, bevorzugt
      amtliche/Fachquellen (Stufe >= Fachmedien). Ein Produktionsfenster im
      Quelltext grenzt ein: Baujahr außerhalb -> verworfen; innerhalb ->
      "vehicle_possible" (möglicherweise betroffen, FIN-first); ohne Fenster ->
      "series_only".
  PHASE 3 — TECHNISCHE HINWEISE (nur nach belegter Identität). Jeder Fakt trägt
      seinen Geltungsbereich: ein Jahresbereich, der das Baujahr ausschließt,
      verwirft den Fakt; ein vager Bereich ("frühe Baujahre", "vor dem Facelift")
      wird als UNAUFGELÖST gekennzeichnet statt zum "bekannten Schwachpunkt
      dieses Modells" zu werden.

SICHERHEIT
  * Webinhalte sind DATEN: sie werden ausschließlich per Regex gelesen, nie als
    Anweisung ausgeführt; an das Sprachmodell gehen nur kanonische Objekte, und
    dessen Antwort ist auf eine Auswahl bestehender Evidence-IDs begrenzt.
  * Keine freie URL-Abfrage: nur Suchanfragen über den bestehenden Tavily-Pfad
    (Budget/Timeout/Retry/Cache in app/web_search.py, harte Anfragebudgets in
    app/provider_control.py). Höchstens 2+2+4 Anfragen, keine Schleifen.
  * EPHEMERAL: nichts wird gespeichert, kein DB-Import, kein verification-Upgrade.
"""

import asyncio
import logging
import re
from typing import Protocol

from app.config import IDENTITY_DEBUG
from app.kraftstoff_powertrain import powertrain_aus_freitext
from app.models import EvidenceQuelle, TechnischeRecherche, WebFakt, WebVehicleIdentity
from app.web_search import (
    KATEGORIE_RUECKRUFE, KATEGORIE_SCHWACHSTELLEN, KATEGORIE_TECHNISCHE_DATEN,
    KATEGORIE_WARTUNG, US_QUELLEN_AUSSCHLUSS,
    _domain_von, _pressedomain_gehoert_marke, _psl_teile, _qualitaets_label,
    curate_results, ist_amtliche_quelle, registrable_domain, score_domain, tavily_search,
)

log = logging.getLogger(__name__)

TRIGGER_DB_MISS = "db_miss"
TRIGGER_IDENTITAET_UNSICHER = "identitaet_unsicher"
TRIGGER_MOTOR_FEHLT = "motor_fehlt"
TRIGGER_KONFLIKT = "konflikt"

# Release-Hardening (Root Cause 6): je-Phase-Vollständigkeit — siehe
# TechnischeRecherche.phasen_status (app/models.py) für die Begründung.
PHASE_SUCCESS = "success"
PHASE_PARTIAL = "partial"
PHASE_FAILED = "failed"
PHASE_NOT_RUN = "not_run"


def _phase_status(*, lief: bool, anfragen_gesamt: int, anfragen_fehlgeschlagen: int,
                  inhalt_schwach: bool = False) -> str:
    """Generische Statuslogik, dieselbe für jede Phase (Identität/Rückruf/
    Technik) — kein phasenspezifischer Sonderfall außer dem jeweils
    übergebenen `inhalt_schwach`-Signal.

      NOT_RUN  die Phase wurde nie versucht (z.B. Rückruf/Technik, weil die
               Identität vorher nicht reichte).
      FAILED   JEDE Anfrage dieser Phase schlug fehl — keine verwertbare
               Antwort überhaupt erhalten.
      PARTIAL  mindestens eine Anfrage schlug fehl (aber nicht alle), ODER
               alle Anfragen kamen durch, aber der Inhalt ist zu schwach, um
               als vollständige Abdeckung zu gelten (`inhalt_schwach`,
               aktuell nur für die Technik-Phase verwendet: nur Tier-3-Quellen
               oder keine Fakten trotz erfolgreicher Anfragen).
      SUCCESS  alle Anfragen kamen durch UND der Inhalt gilt nicht als
               schwach. Ausdrücklich UNABHÄNGIG davon, ob die Phase am Ende
               etwas Verwertbares fand — "0 Rückrufe gefunden" ist ein
               gültiges, vollständiges SUCCESS-Ergebnis, kein PARTIAL/FAILED.
    """
    if not lief:
        return PHASE_NOT_RUN
    if anfragen_gesamt > 0 and anfragen_fehlgeschlagen >= anfragen_gesamt:
        return PHASE_FAILED
    if anfragen_fehlgeschlagen > 0 or inhalt_schwach:
        return PHASE_PARTIAL
    return PHASE_SUCCESS

MIN_SCORE_IDENTITAET = 30
MIN_SCORE_FAKT = 30
# Rückrufe: amtlich (50), Hersteller (48) oder etablierte Fachquelle/ADAC (40).
# Ein Forum erzeugt nie einen Rückruf-Fakt.
_MIN_SCORE_RUECKRUF = 40
MIN_DOMAINS_IDENTITAET = 2
MAX_FAKTEN_JE_KATEGORIE = 5

# Quellenstufen (aus dem bestehenden Tier-System von score_domain).
TIER1_MIN_SCORE = 48      # Hersteller, Behörden/Prüforganisationen
TIER2_MIN_SCORE = 38      # ADAC/Fachmedien, technische Datenbanken
_GEWICHT = {1: 3, 2: 2, 3: 1}
MIN_KONSENS_GEWICHT = 3

# Für Rückrufquellen, die Tavily bevorzugt durchsuchen soll (Positivliste für
# EINE der beiden Rückrufanfragen; die zweite bleibt offen).
_RUECKRUF_DOMAINS = ["kba.de", "kba-online.de", "adac.de", "auto-motor-und-sport.de",
                     "autobild.de"]

_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                          "Ä": "ae", "Ö": "oe", "Ü": "ue"})


def _norm(text: str | None) -> str:
    t = (text or "").strip().lower().translate(_UMLAUTE)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _tokens(text: str | None) -> set[str]:
    return {t for t in _norm(text).split() if t}


# Identity-Hardening (Lexus-RC-F-Forensik, Folgeauftrag, Root Cause "RC F"):
# `_tokens()` zerlegt NUR an Nicht-Alphanumerisch (Leerzeichen, Bindestrich,
# Punkt) — zwischen Buchstabe und Ziffer wird NICHT getrennt. "RC F"/"RC-F"
# ergeben dadurch bereits {"rc","f"}, aber "RCF" (ohne jedes Trennzeichen)
# ergibt EIN Token "rcf" — ungleich {"rc","f"}, das harte Alignment in
# `ausgerichtet()` schlägt dann fehl, obwohl dieselbe Quelle dasselbe Fahrzeug
# meint. Gleiches Muster umgekehrt: Nutzereingabe "GR86" (ein Token) matcht
# keine Quelle, die "GR 86" mit Leerzeichen schreibt.
#
# Lösung: KEINE fuzzy/Ähnlichkeits-Suche, KEIN freies Substring-Matching
# (verworfen — "cx5" dürfte sonst in "cx50" anschlagen). Stattdessen zwei
# KONTROLLIERTE, deterministische Signaturen pro Modellname, beide aus
# `_fein_zerlegt()` abgeleitet (Zerlegung an JEDER Buchstabe<->Ziffer-Grenze,
# zusätzlich zu den bestehenden Nicht-Alphanumerisch-Trennern):
#   * die VOLLSTÄNDIG GETRENNTE Token-MENGE (z.B. {"rc","f"}, {"gr","86"})
#   * das VOLLSTÄNDIG ZUSAMMENGESCHRIEBENE Einzel-Token (z.B. "rcf", "gr86")
# Ein Treffer gilt als modell-ausgerichtet, wenn EINE dieser beiden Signaturen
# als GANZE Menge in den Ziel-Tokens enthalten ist (weiterhin Mengen-
# Inklusion, kein Teilstring-Test) — das deckt symmetrisch jede Schreibweise
# auf BEIDEN Seiten ab (Nutzereingabe UND Quelle), ohne die Tokengrenzen von
# `_tokens()` für Marke/Freitext/andere Felder anzutasten.
_RE_FEIN = re.compile(r"[a-z]+|[0-9]+")


def _fein_zerlegt(token: str) -> list[str]:
    """Spaltet EIN (bereits `_norm()`-normalisiertes, nur a-z0-9 enthaltendes)
    Token an jeder Buchstabe<->Ziffer-Grenze. "rc" -> ["rc"]; "gr86" ->
    ["gr","86"]; "86" -> ["86"]. Rein formbasiert, kein Katalog."""
    return _RE_FEIN.findall(token)


def _modell_signaturen(modell: str | None) -> list[frozenset[str]]:
    """Kontrollierte Match-Signaturen für EINEN Modellnamen (kein Katalog,
    keine Ähnlichkeitssuche). Leer, wenn `modell` keine Tokens ergibt."""
    roh = _norm(modell).split()
    if not roh:
        return []
    fein = [teil for tok in roh for teil in _fein_zerlegt(tok)]
    if not fein:
        return []
    signaturen = {frozenset(fein)}
    zusammen = "".join(fein)
    if zusammen:
        signaturen.add(frozenset({zusammen}))
    return list(signaturen)


# Identity-Hardening (Folgeauftrag, Abschnitt 2 "Alphabetische Schreib-
# varianten"): die obige Ziffer-Anker-Zerlegung löst "RC F"/"RCF"/"GR 86"/
# "GR86", aber NICHT rein alphabetische Fälle ohne jede Ziffer ("C-HR"/"CHR",
# "E-Pace"/"EPace") in der Richtung "Ziel-Modell zusammengeschrieben, Quelle
# trennt" — ein zusammengeschriebenes Token wie "chr" hat KEINEN Ziffer-Anker,
# an dem es sich wieder zerlegen ließe, und ein generisches "probiere alle
# Buchstaben-Trennstellen" wäre genau die verworfene fuzzy-/Wörterbuch-Logik
# (jedes kurze Wort könnte dann an beliebiger Stelle "passend" zerlegt werden).
#
# Lösung: Trenninformation NICHT nur aus dem Zielstring, sondern SYMMETRISCH
# auch aus der QUELLE selbst nutzen — die Quelle hat an der relevanten Stelle
# ("C-HR"/"C HR") bereits EIN EIGENES, EXPLIZITES Trennzeichen gesetzt, das
# `_norm()`/`_tokens()` ohnehin schon in zwei separate Tokens auflöst ("c","hr").
# `_quelle_konkatenationen()` fügt NUR bereits GETRENNTE, UNMITTELBAR
# BENACHBARTE Quell-Token-PAARE (Fenster 2 — bewusst NICHT 3, siehe unten)
# wieder zusammen — es wird NIEMALS ein zusammengeschriebenes Quell-Token
# selbst aufgespalten, und es wird NIE geraten: eine Konkatenation entsteht
# nur, wenn die Quelle an exakt dieser Stelle bereits ein Leerzeichen/
# Bindestrich/Punkt gesetzt hatte. Ein Treffer nur für EINZELNE Modell-
# Signaturen (`len(sig) == 1`), da ein Mengenvergleich mit >1 Element hier
# keinen Sinn ergibt (die normale Mengen-Inklusion deckt den mehrteiligen
# Fall bereits ab). Fenster bewusst auf 2 begrenzt: JEDER im Auftrag
# geforderte Fall (RC-F, C-HR, E-Pace, F-Type) ist genau zweiteilig — ein
# Fenster von 3 brächte keinen zusätzlichen Nutzen, nur zusätzliche
# Kollisionsfläche (z.B. drei kurze, unverwandte Nachbarwörter, die
# zufällig zu einem anderen Modellnamen zusammenlaufen). Strukturell
# kollisionssicher bleibt es trotzdem auch bei Fenster 3+: die Konkatenation
# von >= 2 nichtleeren Tokens ist IMMER länger als jedes ihrer Teile, und ein
# Treffer verlangt exakte Gleichheit der GESAMTEN Zeichenkette, kein
# Teilstring/Präfix-Test — die Fenstergrenze ist hier zusätzliche Vorsicht,
# keine Notwendigkeit für Korrektheit.
def _quelle_konkatenationen(text: str | None) -> set[str]:
    tokens = _norm(text).split()
    return {tokens[i] + tokens[i + 1] for i in range(len(tokens) - 1)}


def _modell_ausgerichtet(ziel_tokens: set[str], modell: str | None, *,
                         quelle_konkat: set[str] | None = None) -> bool:
    """Ob EINE der Modell-Signaturen als GANZE Menge in `ziel_tokens` steht.
    Mengen-Inklusion, kein Teilstring-Test — "cx5" matcht dadurch NICHT in
    einem Zieltext, der nur "cx50" enthält (unterschiedliche Tokens).
    `quelle_konkat` (optional): zusätzliche, aus der QUELLE SELBST abgeleitete
    Konkatenations-Signaturen (siehe `_quelle_konkatenationen()`) — deckt rein
    alphabetische, zusammengeschriebene Ziel-Modelle ab, wenn die Quelle sie
    getrennt schreibt."""
    sigs = _modell_signaturen(modell)
    if not sigs:
        return False
    if any(sig <= ziel_tokens for sig in sigs):
        return True
    if quelle_konkat:
        return any(len(sig) == 1 and next(iter(sig)) in quelle_konkat for sig in sigs)
    return False


def _tier(url: str) -> int:
    s = score_domain(url)
    if s >= TIER1_MIN_SCORE:
        return 1
    if s >= TIER2_MIN_SCORE:
        return 2
    return 3


# Lexus-RC-F-Forensik (Root Cause 1, CONFIRMED) + Identity-Hardening
# (Folgeauftrag, PSL-Generalisierung): `_ist_herstellerseite()` (app/web_search.py)
# prüft gegen eine GESCHLOSSENE Markenliste (`_HERSTELLER_MARKEN`) — fehlt eine
# Marke dort (z.B. Lexus, Infiniti, Genesis, …), erhält deren EIGENE offizielle
# Domain score_domain()==0 und zählt für `_identitaet_belegt()` nicht als
# Stützquelle. Keine neue Markenliste (ausdrücklich verworfen) — stattdessen
# eine GENERISCHE, anfragegebundene Erkennung über die Public-Suffix-bewusste
# REGISTRIERBARE Domain (app/web_search.py:`_psl_teile`): deren Kern-Label muss
# dem NORMALISIERTEN Zielmarkennamen aus der Anfrage EXAKT entsprechen — die
# Subdomain davor ist FREI (Media-/Press-/Newsroom-/Länder-Subdomains der
# echten Herstellerdomain zählen automatisch mit, ohne Sonderfall je Marke):
#   "lexus.de"            -> Label "lexus"      == "lexus"    -> True
#   "media.lexus.com"     -> Label "lexus"      == "lexus"    -> True (Subdomain frei)
#   "www.lexus.co.uk"     -> Label "lexus"      == "lexus"    -> True (PSL: ".co.uk" korrekt als Suffix erkannt)
#   "lexus-forum.de"      -> Label "lexus-forum" != "lexus"   -> False (Zusatzwort IM Markenlabel selbst)
#   "lexus.fanblog.de"    -> Label "fanblog"     != "lexus"   -> False ("lexus" ist nur Subdomain eines FREMDEN Hosts)
# Naiver `split(".")[0]` hätte bei Mehrteil-TLDs (`.co.uk`, `.com.au`) und bei
# Subdomains beliebig danebenliegen können — deshalb PSL statt Heuristik.
# Bewusst NUR additiv zur Identitätsstufe hier (`_tier_identitaet`) — ändert
# NICHT `score_domain()` selbst und wirkt damit NICHT auf Markt-/Rückruf-/
# Schwachstellen-/Wartungs-Faktenbewertung (bewusst unangetastet, Modul-Scope).
def _marke_eigene_domain(url_oder_domain: str, marke: str | None) -> bool:
    if not url_oder_domain or not marke:
        return False
    marke_norm = _norm(marke).replace(" ", "")
    if not marke_norm:
        return False
    teile = _psl_teile(url_oder_domain)
    if teile.domain and teile.domain.replace("-", "") == marke_norm:
        return True
    # F17-Fix (RC-F-Ursachenanalyse, 2026-10-25): eigenständig benannte,
    # einzeln belegte Presse-Domain (siehe `app.web_search._HERSTELLER_
    # PRESSEDOMAINS`) -- dieselbe, einzige Zuordnung wie in `score_domain()`/
    # `_ist_herstellerseite()`, keine zweite, abweichende Liste.
    return _pressedomain_gehoert_marke(url_oder_domain, marke_norm)


# F17-Folgefix (RC-F-Ursachenanalyse, Abschlussauftrag 2026-10-25): auch nach
# dem PSL-/Pressedomain-Fix in `score_domain()` bleibt für "schwachstelle"/
# "wartung" eine Scoring-Asymmetrie bestehen -- `score_domain()` kennt den
# Zielkontext (die angefragte Marke) nicht und kann deshalb NIE jede reale
# Herstellerdomain erkennen (`_HERSTELLER_MARKEN` ist und bleibt eine
# geschlossene, bewusst NICHT vervollständigte Liste, siehe
# `test_identity_manufacturer_source.py`). Der Identitäts-Primärquellenpfad
# löst exakt dasselbe Problem bereits generisch über `_marke_eigene_domain()`
# (PSL + die im Request angefragte Marke, keine Liste). Diese Funktion wendet
# DIESELBE, bereits bestehende und getestete Zuordnung zusätzlich auf die
# Schwellenwert-Entscheidung für "schwachstelle"/"wartung" an -- bewusst NICHT
# für "rueckruf" (Recall/KBA bleibt unangetastet, §15 Auftrag) und bewusst
# KEINE neue Markenliste, KEINE Modell-Sonderlogik: eine Domain wird nur dann
# höher bewertet, wenn `_marke_eigene_domain()` sie der TATSÄCHLICH
# angefragten Marke zuordnet. Ändert NICHTS an Alignment/Varianten-/
# Problemwort-Gates -- Herstellervertrauen ist weiterhin nicht dasselbe wie
# Claim-Applicability, die läuft unverändert eigenständig weiter.
def _score_domain_fuer_marke(url: str, kategorie: str, marke: str | None) -> int:
    score = score_domain(url, kategorie)
    if kategorie == KATEGORIE_RUECKRUFE:
        return score
    if score < TIER1_MIN_SCORE and _marke_eigene_domain(url, marke):
        return TIER1_MIN_SCORE
    return score


# Identitätsquellen der Stufe 2, die die allgemeine Domainliste
# (app/web_search.py, auch für Markt und Fakten genutzt) nicht als Fachmedien
# führt: etablierte Fachpresse und technische Datenbanken. Gilt NUR für Phase 1
# (Identitäts-Claims), damit die geteilte Markt-/Faktenbewertung unverändert
# bleibt. Resume-Audit, realer Web-Smoke-Test (DB-Miss): die Seiten, die
# Generation und Leistung nannten, lagen genau hier und zählten vorher gar nicht.
_IDENTITAET_TIER2 = frozenset({
    "autozeitung.de", "auto-data.net", "motor1.com", "autohaus.de", "kfz-betrieb.vogel.de",
    "heise.de", "automobil-industrie.vogel.de",
})


def _tier_identitaet(url: str, marke: str | None = None) -> int:
    dom = _domain_von(url) or ""
    if _marke_eigene_domain(dom, marke):
        return 1
    if any(dom == d or dom.endswith("." + d) for d in _IDENTITAET_TIER2):
        return min(_tier(url), 2)
    return _tier(url)


def _ist_regionsfremd(url: str) -> bool:
    """Identity-Hardening, Abschnitt 5: dieselbe, bestehende Liste wie
    `US_QUELLEN_AUSSCHLUSS` (keine neue Liste) — markiert eine Quelle als
    regional nicht übertragbar für DETAIL-Claims (siehe `_identitaets_claims()`).
    Betrifft NICHT, ob die Quelle für die reine Marke/Modell-Existenz zählen
    darf (`_identitaet_belegt()`/`_tier_identitaet()` bleiben unverändert)."""
    dom = _domain_von(url) or ""
    return any(d in dom for d in US_QUELLEN_AUSSCHLUSS)


# ── Provider-Schnittstelle ───────────────────────────────────────────────────

class TechnicalVehicleResearchProvider(Protocol):
    """Austauschbare Quelle für technische Fahrzeugrecherche. Ein Provider MUSS
    Fehler selbst abfangen (`provider_fehler=True`) statt eine Exception
    weiterzugeben — der Kaufcheck darf an der Recherche nie scheitern."""

    async def recherchiere(self, *, marke: str | None, modell: str | None,
                           baujahr: int | None, motor: str | None,
                           ausgeloest_durch: str, ziel: dict | None = None) -> TechnischeRecherche:
        ...


# ── Trigger-Entscheidung (unverändert) ───────────────────────────────────────

def _konflikt_grund(req, motor_match: dict | None) -> str | None:
    if not motor_match:
        return None
    from app.key_findings import _kraftstoff_norm, _ps_aus_text
    from app.kraftstoff_powertrain import canonical_fuel
    ins_kraft = (_kraftstoff_norm(getattr(req, "kraftstoff", None))
                 or _kraftstoff_norm(getattr(req, "motor", None)))
    if ins_kraft == "hybrid":
        ins_kraft = None
    mot_kraft = canonical_fuel(motor_match.get("kraftstoff"), motor_match.get("bezeichnung"),
                               motor_match.get("motorcode"))
    if ins_kraft and mot_kraft and ins_kraft != mot_kraft:
        return "kraftstoff"
    ins_ps = _ps_aus_text(getattr(req, "motor", None), getattr(req, "beschreibung", None),
                          getattr(req, "freitext", None))
    mot_ps = motor_match.get("leistung_ps")
    if ins_ps and mot_ps and abs(ins_ps - mot_ps) >= 12 and abs(ins_ps - mot_ps) / mot_ps >= 0.08:
        return "leistung"
    return None


def fallback_trigger(req, baureihe_roh: dict | None, identitaet: dict,
                     baureihe_gegatet: dict | None, motor_match: dict | None) -> str | None:
    if not (getattr(req, "marke", None) and getattr(req, "modell", None)):
        return None
    if baureihe_roh is None:
        return TRIGGER_DB_MISS
    if baureihe_gegatet is None:
        return TRIGGER_IDENTITAET_UNSICHER
    if motor_match is None and (getattr(req, "motor", None) or "").strip():
        return TRIGGER_MOTOR_FEHLT
    if _konflikt_grund(req, motor_match):
        return TRIGGER_KONFLIKT
    return None


# ── Modell-/Codetrennung ─────────────────────────────────────────────────────
#
# Lexus-RC-F-Forensik (Root Cause 2, CONFIRMED): `ausgerichtet()`/
# `_identitaet_belegt()` verlangen ALLE Tokens des `modell`-Strings als
# zwingendes UND auf der Quelle. Tippt ein Nutzer einen präzisierenden
# Generations-/Chassiscode direkt ins Modellfeld ("RC F USC10", "M4 F82"),
# scheitert JEDE Quelle, die diesen Code nicht wortgenau nennt — meist ALLE,
# weil Chassiscodes selten in normalem Fließtext stehen. Es gibt dafür heute
# kein eigenes Eingabefeld (Package A rührt das Frontend bewusst noch nicht
# an) — der Nutzer hat nur das `modell`-Feld. Diese Funktion trennt einen
# optionalen, NACHGESTELLTEN Code generisch ab, OHNE Marken-/Modellkatalog:
# die zwingende Ausrichtungsprüfung verlangt danach nur noch den KERN-
# Modellnamen; der abgetrennte Code wird unten (siehe `_ziel()`) als
# Generations-KANDIDAT weitergegeben — er HEBT die Confidence, wenn eine
# Quelle ihn bestätigt (über die bestehende `_generationscodes()`/`konsens()`-
# Maschinerie, siehe dort), zerstört aber nie mehr die Mindest-Identität nur
# weil er selbst unbestätigt bleibt.
_MODELL_CODE_AUSSCHLUSS = frozenset({"ps", "kw", "tdi", "tsi", "tfsi", "fsi", "tce", "dci",
                                     "crdi", "hdi", "cdti", "vti", "thp", "bluehdi", "multijet",
                                     "gt"})


def _ist_modell_code_kandidat(token: str) -> bool:
    """Ob ein NACHGESTELLTES Token eines mehrteiligen `modell`-Strings eher
    ein zusätzlicher Code ist als Teil des eigentlichen Modellnamens. Rein
    formbasiert (kein Katalog):

      * enthält mindestens eine Ziffer — ein normales Wort ("Avant", "Touring",
        "Plaid", "Sport") hat nie eine Ziffer; das ist das robusteste hier
        verfügbare, generische Signal, UND
      * Gesamtlänge 2-8 Zeichen (ein Chassiscode ist immer kompakt), UND
      * kein bekanntes Leistungs-/Kraftstoff-/Getriebe-Kürzel.

    Bewusst NUR das letzte Token eines MEHRTEILIGEN Strings wird je geprüft
    (siehe `parse_core_model`) — ein einzelnes Token ("GT86", "C300", "A4")
    wird dadurch NIE zerlegt, weil es gar nicht erst als "letztes von
    mehreren" betrachtet wird.

    Package-A-Hardening (reine Alignment-Entlastung, s. `parse_core_model`-
    Docstring und `_ziel()`): ein rein NUMERISCHES Token mit 2-3 Ziffern
    ("992", "991") zählt ZUSÄTZLICH als Kandidat. Das betrifft NUR, ob der
    harte Modell-Alignment-Vergleich dieses Token noch mitverlangt — ein
    solcher Kandidat kann NIE zu einer bestätigten Generation werden, weil
    `_generationscodes()` für jeden Code mindestens einen Buchstaben verlangt
    (s. dort). Vier Ziffern bleiben bewusst ausgeschlossen, weil das meist
    eine inline genannte Jahreszahl ist, kein Chassiscode.

    Fortsetzungsauftrag (MX-5-NC-Alignment, Core-Model/Auxiliary-Code-
    Inkonsistenz): die bisherige Ziffernpflicht erfasste NUR Codes wie
    "USC10"/"F82"/"991" — ein rein ALPHABETISCHER Generationscode ohne Ziffer
    ("NC", "ND", "NA", "NB") wurde NIE abgetrennt, obwohl `_generationscodes()`
    (oben, Root Cause 3) genau SOLCHE Codes bereits als Generation akzeptiert
    (dort bewusst `[A-Z]{1,2}` für den ziffernlosen Fall). Eine Quelle, die
    das Zielmodell nur als Prosa ("dritte Generation") statt als Code nennt,
    verlor dadurch ihre sonst eindeutige Core-Model-Übereinstimmung komplett
    (`modell_fehlt`) -- obwohl ein fehlender Zusatzcode laut `_ziel()`-Docstring
    (Auxiliary Candidate) eine passende Core-Model-Quelle NIE ausschließen
    darf. Fix: DIESELBE, bereits bestehende und getestete Grenze wie bei
    `_generationscodes()` -- ein ziffernloser Code bleibt auf 1-2 Buchstaben
    begrenzt (ein echtes Wort/eine Ausstattungsbezeichnung hat praktisch immer
    mehr), inklusive derselben kleinen Ausschlussliste (u.a. "GT", bereits dort
    als Trimname statt Generationscode ausgeschlossen). Generisch -- keine
    Marken-/Modell-Sonderliste; betrifft jeden zweiteiligen `modell`-String
    gleichermaßen."""
    t = (token or "").strip()
    if not (2 <= len(t) <= 8) or not re.fullmatch(r"[A-Za-z0-9]+", t):
        return False
    if re.fullmatch(r"\d{2,3}", t):
        return True   # z.B. "992"/"991" -- reine Alignment-Entlastung, s. Docstring
    hat_ziffer = bool(re.search(r"\d", t))
    if not hat_ziffer:
        if len(t) > 2:
            return False   # echtes Wort/Ausstattungsbezeichnung ("Sport", "Touring"), kein Code
    elif not re.search(r"[A-Za-z]", t):
        return False
    t_norm = _norm(t)
    if t_norm in _MODELL_CODE_AUSSCHLUSS or any(t_norm.endswith(suf) for suf in _MODELL_CODE_AUSSCHLUSS):
        return False   # z.B. "350PS"/"180KW" -- Leistungsangabe, kein Code
    return True


def parse_core_model(modell: str | None) -> tuple[str | None, str | None]:
    """(kern_modell, code_kandidat). `code_kandidat` ist None, wenn kein
    abspaltbarer Kandidat erkannt wurde. Bewusst konservativ: nur das LETZTE
    Token eines mehrteiligen Strings kommt überhaupt in Frage — "RC F USC10"
    -> ("RC F", "USC10"), aber "GT86"/"C300"/"A4"/"M4" (je ein einzelnes
    Token) bleiben unverändert und unzerlegt."""
    if not modell or not modell.strip():
        return modell, None
    teile = modell.strip().split()
    if len(teile) < 2:
        return modell, None
    *kern_teile, letztes = teile
    if _ist_modell_code_kandidat(letztes):
        return " ".join(kern_teile), letztes
    return modell, None


# ── Entity-Alignment ─────────────────────────────────────────────────────────

def _modell_im_titel(titel_tokens: set[str], modell: str | None, titel_text: str | None = None) -> bool:
    konkat = _quelle_konkatenationen(titel_text) if titel_text else None
    return _modell_ausgerichtet(titel_tokens, modell, quelle_konkat=konkat)


def ausgerichtet(r: dict, marke: str | None, modell: str | None) -> tuple[bool, str | None]:
    """Ob ein Treffer das Zielfahrzeug BETRIFFT (Invariante 8).

    Regeln:
      * Marke und Modell müssen als ganze Tokens im Treffer stehen (Modell über
        die kontrollierten Signaturen aus `_modell_ausgerichtet()` — deckt
        Schreibvarianten wie "RC F"/"RC-F"/"RCF" ab, OHNE Teilstring-Matching;
        zusätzlich `_quelle_konkatenationen()` für rein alphabetische Fälle
        ohne Ziffer-Anker wie "C-HR"/"CHR").
      * Nennt der TITEL eine andere Marke (und nicht die eigene) oder die eigene
        Marke ohne das Modell, ist die Seite primär über ein anderes Fahrzeug —
        ein passender Einzelsatz im Text ändert daran nichts (Befund: Ford-
        Mustang-Artikel als MX-5-Schwachstelle).
    """
    from app.vehicle_identity import MARKEN
    marke_tokens = _tokens(marke)
    titel_text = r.get("title")
    content_text = r.get("content")
    titel_tokens = _tokens(titel_text)
    text_tokens = titel_tokens | _tokens(content_text)
    quelle_konkat = _quelle_konkatenationen(titel_text) | _quelle_konkatenationen(content_text)
    if not _modell_ausgerichtet(text_tokens, modell, quelle_konkat=quelle_konkat):
        return False, "modell_fehlt"
    if marke_tokens and not (marke_tokens & text_tokens):
        return False, "marke_fehlt"
    fremde = (titel_tokens & MARKEN) - marke_tokens
    if fremde and not (marke_tokens & titel_tokens):
        return False, f"titel_fremde_marke:{','.join(sorted(fremde))}"
    if titel_tokens and not _modell_im_titel(titel_tokens, modell, titel_text):
        # Die Seite nennt das Modell nur im Text (Sammelmeldung, Markenübersicht):
        # SCHWACH ausgerichtet. Daraus zählen nur Sätze, die das Modell selbst
        # nennen, und nur mit der niedrigsten Quellenstufe.
        return True, "schwach"
    return True, None


def _satz_nennt_modell(satz: str, modell: str | None) -> bool:
    return _modell_ausgerichtet(_tokens(satz), modell, quelle_konkat=_quelle_konkatenationen(satz))


# Identity-Hardening (Folgeauftrag, Abschnitt 1 "Primary-Source-Pfad muss
# wirklich streng sein", CORRECTED): die ursprüngliche Fassung prüfte
# `score_domain(url) >= TIER1_MIN_SCORE` — das lässt zwar KEIN Fachmedium/
# Marktplatz/Technik-DB durch (deren Basiswerte 40/38/32 liegen alle unter der
# Schwelle 48, ohne Kategorie-Boost, den dieser Aufruf nie übergibt), hat aber
# eine andere, echte Lücke: `score_domain()` erkennt "Hersteller" über
# `_ist_herstellerseite()` — eine GESCHLOSSENE Liste BEKANNTER Marken, OHNE
# Bezug zur angefragten `marke`. Eine Quelle wie "bmw.de" hätte damit auch für
# ein völlig anderes Zielfahrzeug (z.B. Lexus) als "zweifelsfrei Tier-1"
# gezählt — nur durch `ausgerichtet()` (Marke+Modell müssen im Text stehen)
# indirekt abgefangen, kein Veto in der Primärquellen-Logik selbst.
# `_marke_eigene_domain()` deckt den Hersteller-Fall bereits VOLLSTÄNDIG und
# MARKE-SPEZIFISCH ab (auch "bmw.de" für Marke "BMW", PSL-basiert, jede
# TLD/Subdomain) — der Score-Pfad bringt dafür nichts zusätzliches, nur die
# Lücke. Jetzt stattdessen EXPLIZIT nur zwei, von Natur aus marke-spezifisch
# bzw. markenunabhängig-amtliche Fälle: `ist_amtliche_quelle()` (dieselbe,
# bestehende `_AMTLICH_DOMAINS`-Liste, KBA/TÜV/DEKRA — amtlich ist amtlich,
# unabhängig von der angefragten Marke) ODER `_marke_eigene_domain()`.
def _primaerquelle(url: str, marke: str | None) -> bool:
    return ist_amtliche_quelle(url) or _marke_eigene_domain(url, marke)


def _primaerquelle_widerspruch(treffer: list[dict], marke: str | None, primaer: dict) -> bool:
    """Veto für den Einzelquellenpfad (Abschnitt 2D): gibt es im SELBEN
    Treffer-Satz eine ANDERE, ebenfalls Tier-1-starke Quelle, deren Titel eine
    FREMDE Marke trägt (derselbe `titel_fremde_marke`-Signal wie in
    `ausgerichtet()`, hier nur erneut ausgewertet) — dann gilt die
    Primärquelle als durch widersprechende Evidence entwertet. Reine
    Wiederverwendung des bestehenden Fremdmarken-Signals, keine neue Logik.

    Bewusst ANDERE (brand-AGNOSTISCHE) Tier-1-Schwelle als `_primaerquelle()`:
    dort geht es darum, ob EINE Quelle UNSER Zielfahrzeug (Marke+Modell)
    zweifelsfrei bestätigt — deshalb marke-spezifisch. Hier geht es darum, ob
    eine ANDERE Quelle ein ebenso autoritativer, aber WIDERSPRECHENDER Claim
    über ein FREMDES Fahrzeug ist — dafür zählt `score_domain()`s bestehende,
    brand-unabhängige Hersteller-/Amtlich-Erkennung (plus `_marke_eigene_domain`
    für Marken außerhalb der geschlossenen Liste) weiterhin korrekt: ein
    Veto soll bei JEDER erkennbar autoritativen Fremdmarken-Quelle greifen,
    nicht nur bei einer, die zufällig zur eigenen Zielmarke gehört."""
    from app.vehicle_identity import MARKEN
    marke_tokens = _tokens(marke)
    for r in treffer:
        if r is primaer:
            continue
        url = r.get("url") or ""
        if score_domain(url) < TIER1_MIN_SCORE and not _marke_eigene_domain(url, marke):
            continue
        titel_tokens = _tokens(r.get("title"))
        fremde = (titel_tokens & MARKEN) - marke_tokens
        if fremde and not (marke_tokens & titel_tokens):
            return True
    return False


def _identitaet_belegt(modell: str | None, marke: str | None,
                       treffer: list[dict]) -> tuple[bool, list[dict], int, str | None]:
    """Marke UND Modell als ganze Tokens (über die Modell-Signaturen) auf
    >= MIN_DOMAINS_IDENTITAET unabhängigen, hinreichend vertrauenswürdigen
    Domains — ODER (Identity-Hardening, Abschnitt 2/3) über GENAU EINE
    zweifelsfreie Primärquelle (Hersteller/amtlich), STARK ausgerichtet
    (Modell im Titel-Kontext, nicht nur "schwach" im Fließtext), ohne
    widersprechende Tier-1-Evidence im selben Treffer-Satz. Der bestehende
    Zwei-Domain-Pfad bleibt UNVERÄNDERT der primäre, breitere Weg — der
    Primärquellenpfad ist ein zusätzlicher, engerer Weg NUR für Marke+Modell
    (siehe `werte_identitaet_aus()` für die Begrenzung auf diese zwei Felder).

    Rückgabe: (belegt, stuetzende_treffer, anzahl_unabhaengiger_domains, pfad)
    `pfad` ist "zwei_domain" | "primaerquelle" | None (nicht belegt)."""
    if not _tokens(modell):
        return False, [], 0, None
    stuetzend: list[dict] = []
    domains: set[str] = set()
    primaer_kandidat: dict | None = None
    for r in treffer:
        url = r.get("url") or ""
        if (score_domain(url, KATEGORIE_TECHNISCHE_DATEN) < MIN_SCORE_IDENTITAET
                and _tier_identitaet(url, marke) > 2):
            continue
        ok, grund = ausgerichtet(r, marke, modell)
        if not ok:
            continue
        stuetzend.append(r)
        d = _domain_von(url)
        if d:
            domains.add(d)
        if primaer_kandidat is None and grund is None and _primaerquelle(url, marke):
            primaer_kandidat = r
    zwei_domain = len(domains) >= MIN_DOMAINS_IDENTITAET
    primaerquelle = (primaer_kandidat is not None
                     and not _primaerquelle_widerspruch(treffer, marke, primaer_kandidat))
    if zwei_domain:
        return True, stuetzend, len(domains), "zwei_domain"
    if primaerquelle:
        return True, stuetzend, len(domains), "primaerquelle"
    return False, stuetzend, len(domains), None


def _confidence_aus_domains(anzahl_domains: int, bester_score: int) -> str:
    if anzahl_domains >= 3 and bester_score >= 40:
        return "hoch"
    if anzahl_domains >= 2:
        return "mittel"
    return "niedrig"


def _quellen_aus(treffer: list[dict], limit: int = 3) -> list[EvidenceQuelle]:
    out: list[EvidenceQuelle] = []
    gesehen: set[str] = set()
    for r in sorted(treffer, key=lambda x: -score_domain(x.get("url") or "")):
        url = r.get("url") or ""
        d = _domain_von(url)
        if not url or d in gesehen:
            continue
        gesehen.add(d)
        out.append(EvidenceQuelle(typ="web_technik", url=url,
                                  titel=(r.get("title") or d)[:120],
                                  qualitaet=_qualitaets_label(url)))
        if len(out) >= limit:
            break
    return out


# ── Jahres-/Zeitraum-Extraktion ──────────────────────────────────────────────

_MONATE = r"(?:januar|februar|m(?:ä|ae)rz|april|mai|juni|juli|august|september|oktober|november|dezember|jan|feb|m(?:ä|ae)r|apr|jun|jul|aug|sep|okt|nov|dez)\.?"
_RE_ZEITRAUM = re.compile(
    r"(?:(?:von|zwischen|ab)\s+)?(?:" + _MONATE + r"\s+|\d{1,2}\s*/\s*)?((?:19|20)\d{2})"
    r"\s*(?:-|–|bis(?:\s+(?:einschlie(?:ß|ss)lich|zum?))?|und)\s*(?:" + _MONATE + r"\s+|\d{1,2}\s*/\s*)?"
    r"((?:19|20)\d{2}|heute)", re.IGNORECASE)
_RE_SEIT = re.compile(r"\b(?:seit|ab|since)\s+(?:" + _MONATE + r"\s+|\d{1,2}\s*/\s*)?((?:19|20)\d{2})",
                      re.IGNORECASE)
_RE_BIS = re.compile(r"\b(?:bis|vor|until)\s+(?:(?:baujahr|modelljahr|ende)\s+)?"
                     r"(?:" + _MONATE + r"\s+|\d{1,2}\s*/\s*)?((?:19|20)\d{2})", re.IGNORECASE)
_RE_VAGE = re.compile(
    r"fr(?:ü|ue)he[nr]?\s+(?:\w+\s+)?(?:baujahre?n?|modelle?n?|exemplare?n?|serien?|getriebe\w*|motoren|versionen|jahrg(?:ä|ae)nge?n?)"
    r"|erste[nr]?\s+(?:baujahre?n?|serien?|jahrg(?:ä|ae)nge?n?|modelle?n?)"
    r"|vor\s+(?:dem|der)\s+(?:facelift|modellpflege)|anfangs|anf(?:ä|ae)nglich|bis\s+zur\s+modellpflege"
    r"|(?:ä|ae)ltere[nr]?\s+(?:exemplare?n?|modelle?n?|baujahre?n?|fahrzeuge?n?|jahrg(?:ä|ae)nge?n?)"
    r"|early\s+(?:models|cars|builds)|older\s+(?:models|cars)"
    # Resume-Audit (realer Web-Smoke-Test): "die ersten beiden Getriebe-Generationen",
    # "frühe Motor-Revisionen" — eine Ordnungs-/Zeitangabe vor einer Revisions-/
    # Serienbezeichnung, auch als Kompositum und mit Zahlwort dazwischen.
    r"|(?:erste[nrm]?|fr(?:ü|ue)he[nrm]?|(?:ä|ae)ltere[nrm]?)\s+(?:(?:beiden|zwei|drei|paar)\s+)?"
    r"(?:[\w-]+\s+){0,2}?[\w-]*(?:generation|revision|version|serie|charge|ausbaustufe)\w*",
    re.IGNORECASE)


def zeitraum(text: str) -> tuple[int | None, int | None] | None:
    """(von, bis) aus einem Satz — None, wenn keine Jahresangabe."""
    m = _RE_ZEITRAUM.search(text or "")
    if m:
        von = int(m.group(1))
        bis = None if m.group(2).lower() == "heute" else int(m.group(2))
        return von, bis
    von = _RE_SEIT.search(text or "")
    bis = _RE_BIS.search(text or "")
    if von or bis:
        return (int(von.group(1)) if von else None,
                (int(bis.group(1)) - (1 if bis.group(0).lower().startswith("vor") else 0)) if bis else None)
    return None


def _im_zeitraum(baujahr: int | None, z: tuple[int | None, int | None] | None) -> bool | None:
    if z is None or baujahr is None:
        return None
    von, bis = z
    return (von is None or baujahr >= von) and (bis is None or baujahr <= bis)


# ── Phase 1: Identitäts-Claims ───────────────────────────────────────────────

_ORDINAL = {"erste": 1, "zweite": 2, "dritte": 3, "vierte": 4, "fuenfte": 5, "fünfte": 5,
            "sechste": 6, "siebte": 7, "achte": 8, "first": 1, "second": 2, "third": 3,
            "fourth": 4, "fifth": 5, "sixth": 6}
_RE_GEN_NUMMER = re.compile(r"\b(?:(\d{1,2})\.|(erste|zweite|dritte|vierte|f(?:ü|ue)nfte|sechste|siebte|achte|"
                            r"first|second|third|fourth|fifth|sixth))\s*(?:generation|gen\.)",
                            re.IGNORECASE)
_RE_PS = re.compile(r"\b(\d{2,4})\s*PS\b", re.IGNORECASE)
_RE_KW = re.compile(r"\b(\d{2,3})\s*kW\b")
# Package C (Forensik-Folgeauftrag): generische Schreibvarianten ergänzt, die
# der reale Lexus-RC-F-Fall zeigte ("5.0 V8"/"Benzin"/"Automatik" blieben
# user_only, obwohl "477 PS"/"Heck" aus denselben Quellen bestätigt wurden —
# Abnahmepunkt 7, als Vokabular-/Extraktionslücke diagnostiziert, KEIN Claim-
# Binding-/Konsens-Bug). Bewusst NUR zusätzliches, generisches Vokabular.
_RE_HUBRAUM = re.compile(r"\b(\d[.,]\d)\s*(?:-?\s*(?:l\b|liter|litre)|\s+(?=[A-Za-z]))", re.IGNORECASE)

# F12 (RC-F-Folgeauftrag, Ursachenanalyse-Bericht 2026-10-25): ein reiner
# "cm3"/"cm³"/"ccm"-Wert ("4969 cm³") blieb bisher komplett unerkannt, obwohl
# genau diese Schreibweise auf technischen Datenblättern (z.B. carwiki.de)
# haeufiger ist als die Literangabe. `ziel.get("hubraum")`/der bestehende
# Konsens-/Vergleichspfad arbeiten ausschliesslich mit der Liter-Dezimalstelle
# (siehe `_RE_HUBRAUM` oben) -- ein ccm-Treffer wird deshalb auf dieselbe
# Darstellung NORMALISIERT (kaufmaennische Rundung auf eine Nachkommastelle,
# `4969` -> "5.0", `1998` -> "2.0"), statt eine zweite, exakte ccm-Dimension
# einzufuehren. Das deckt die in der Praxis uebliche Rundungs-Konvention
# zwischen Katalog-ccm und marketingueblicher Literangabe ab, ohne neue
# Toleranz-/Bereichsvergleiche zu erfinden: zwei Quellen, die unterschiedlich
# runden (z.B. "4.9 Liter" vs. "5.0 Liter" fuer denselben 4969-ccm-Motor),
# bleiben ein echter, vom bestehenden `konsens()` zu entscheidender
# Widerspruch -- kein beliebiges Zahlen-Matching.
_RE_HUBRAUM_CCM = re.compile(r"\b(\d{3,5})\s*(?:cm\s*3|cm\s*³|ccm)\b", re.IGNORECASE)


def _ccm_als_liter(ccm_text: str) -> str:
    liter = round(int(ccm_text) / 1000, 1)
    return f"{liter:.1f}"


_KRAFTSTOFF_CLAIM = (("diesel", re.compile(r"\bdiesel\w*|\btdi\b|\bcdi\b|\bdci\b|skyactiv-?d", re.I)),
                     ("benzin", re.compile(r"\bbenzin\w*|ottomotor|\btfsi\b|\btsi\b|skyactiv-?g|petrol|gasoline",
                                           re.I)),
                     ("elektro", re.compile(r"\belektroauto|\bbatterieelektrisch|\belectric vehicle", re.I)))
_ANTRIEB_CLAIM = (("Heck", re.compile(r"hinterradantrieb|heckantrieb|hinterr(?:ä|ae)der|rear[- ]wheel", re.I)),
                  ("Front", re.compile(r"frontantrieb|vorderradantrieb|front[- ]wheel", re.I)),
                  ("Allrad", re.compile(r"allradantrieb|allrad\b|all[- ]wheel|\bawd\b|quattro|xdrive|4matic|4motion", re.I)))
_RE_GETRIEBE_MANUELL = re.compile(r"\b(\d)\s*-?\s*gang[- ]?(?:schaltgetriebe|handschaltung|manuell\w*)"
                                  r"|\b(sechs|f(?:ü|ue)nf)gang[- ]?(?:schaltgetriebe|handschaltung)"
                                  r"|\b(\d)-speed manual", re.I)
# "X-speed" ALLEIN (ohne "automatic") wird bewusst NICHT geclaimt (Package-C-
# Auftrag: "nicht jede Zahl/jedes 'speed'-Wort automatisch als Getriebe") —
# nur die direkte Kombination "X-speed automatic" sowie die bereits
# bestehenden deutschen Muster plus "automatische X-Gang"/bare "automatic
# transmission" als zusätzliche, generische Schreibvarianten.
#
# Lexus-RC-F-Forensik, Backend-Folgeauftrag (Vokabularlücke, generisch, keine
# Marken-/Modell-Sonderlogik): zwei reale, in deutschen Herstellertexten
# gängige Schreibvarianten fielen bisher durch —
#   1) ausgeschriebenes Zahlwort statt Ziffer ("Achtstufen-Automatik",
#      "Siebengang-Automatik") — bisher nur bei `_RE_GETRIEBE_MANUELL` über
#      `_ZAHLWORT` abgedeckt, hier fehlte die Entsprechung;
#   2) ein zusätzliches Marketingwort zwischen Stufenangabe und "Automatik"
#      ("Achtstufen-SPORT-Automatik") — betraf auch die bereits bestehende
#      Ziffer-Schreibweise ("8-Stufen-Sport-Automatik" griff ebenfalls nicht).
# Belegt am echten Herstellertext (toyota-media.de, 01.12.2016): "Achtstufen-
# Sport-Automatik" für Lexus RC F/GS F — reiner Vokabular-/Formfehler, keine
# Konsens- oder Trust-Änderung.
_RE_GETRIEBE_AUTO = re.compile(
    r"\b(\d{1,2})\s*-?\s*(?:stufen|gang)(?:[- ][a-zA-ZäöüÄÖÜß]{3,14})?[- ]?(?:automatik\w*|wandlerautomatik)"
    r"|\b(vier|f(?:ü|ue)nf|sechs|sieben|acht|neun|zehn|elf|zw(?:ö|oe)lf)(?:stufen|gang)"
    r"(?:[- ][a-zA-ZäöüÄÖÜß]{3,14})?[- ]?(?:automatik\w*|wandlerautomatik)"
    r"|\bautomatikgetriebe\b"
    r"|\bautomatische[rn]?\s+(\d{1,2})[- ]?gang\w*"
    r"|\b(\d{1,2})-speed\s+automatic(?:\s+transmission)?\b"
    r"|\bautomatic\s+transmission\b", re.I)

# F13 (RC-F-Folgeauftrag): ALLEINSTEHENDES "Automatik" (ohne Stufen-/Gang-
# angabe, z.B. "Benzin, 477 PS, Automatik" in Inserats-/Kurzprofiltext) wurde
# bisher NICHT erfasst -- `_RE_GETRIEBE_AUTO` oben verlangt immer eine
# Stufen-/Gangangabe oder das Kompositum "Automatikgetriebe". Eine
# Gangzahl ALLEIN beweist weiterhin keine Getriebeart (unveraendert: es gibt
# dafuer keine eigene Regel, nur `_RE_GETRIEBE_MANUELL`/`_RE_GETRIEBE_AUTO`
# oben). Generisch, markenunabhaengig ausgenommen sind die gaengigen
# deutschen "-Automatik"-Komposita, die NICHTS mit dem Getriebe zu tun haben
# (Klimaautomatik, Start-Stopp-Automatik, Scheibenwisch(er)automatik,
# Sitzheizungsautomatik, Lichtautomatik) -- sowohl unmittelbar verschmolzen
# ("Klimaautomatik") als auch mit Bindestrich ("Start-Stopp-Automatik").
_RE_AUTOMATIK_ALLEIN = re.compile(r"\bautomatik\b", re.IGNORECASE)
_AUTOMATIK_FREMDKONTEXT = re.compile(
    r"(?:klima|licht|start[- ]?stopp|scheibenwisch(?:er)?|sitzheizungs?)[- ]?$", re.IGNORECASE)

# F14 (RC-F-Folgeauftrag): Motorcode-Label und -Wert stehen auf echten
# Datenblaettern haeufig als Tabellenzeile ("Motorcode | 2UR-GSE") statt als
# Fliesstext mit Doppelpunkt -- der bisherige Separator `[:(]?` erlaubte kein
# "|" zwischen Label und Wert, eine solche Zeile wurde deshalb NIE erkannt,
# obwohl Label und Wert hier unzweideutig einander zugeordnet sind (genau
# EIN Label, genau EIN Wert, durch das Tabellen-Trennzeichen selbst
# verbunden). Eine vom Provider in GETRENNTE Bloecke zerlegte Tabelle (erst
# alle Spaltenkoepfe, dann alle Werte) bleibt davon bewusst UNBERUEHRT --
# dafuer gibt es hier keine Positions-/Reihenfolge-Heuristik: ohne einen
# erkennbaren, direkten Separator zwischen Label und Wert wird weiterhin
# NICHTS geclaimt (keine erfundene Verbindung).
#
# Haertung (im selben Auftrag gefunden, waehrend des Tests gegen eine reale
# Tabellenkopf-Zeile): der neue "|"-Separator macht eine HEADER-NEBEN-HEADER-
# Zeile ("| Motorcode | Hubraum | Leistung |", ohne Werte in der Naehe)
# erreichbar -- ohne weitere Bedingung wuerde "Hubraum" selbst als Motorcode
# geclaimt (ein reines, beliebiges Wort, genau das vom Auftrag ausgeschlossene
# "beliebige alphanumerische Codes uebernehmen").
#
# Korrektur Runde 1 (Abschlussauftrag 2026-10-25): eine Ziffernpflicht war
# KEINE tragfaehige allgemeine Regel -- reale Motorcodes sind teils rein
# alphabetisch (klassische VW/Audi-Codes wie "AHU", "ALH", "AFN", ganz ohne
# Ziffer).
#
# Korrektur Runde 2 (dieselbe Nacharbeit): Grossschreibung ALLEIN war
# ebenfalls kein verlaesslicher Unterschied -- eine in GROSSBUCHSTABEN
# gerenderte/geschriebene Kopfzeile ("MOTORCODE | HUBRAUM | LEISTUNG") waere
# davon nicht zu unterscheiden gewesen. Das tatsaechlich generische,
# STRUKTURELLE Unterscheidungsmerkmal ist die TABELLENPOSITION, nicht die
# Schreibweise: eine echte Kopfzeile besteht aus MEHREREN aufeinander-
# folgenden, rein alphabetischen Pipe-Segmenten OHNE jeden Werte-Charakter
# (keine Ziffer) -- "Motorcode | Hubraum | Leistung" UND "MOTORCODE |
# GETRIEBE | KRAFTSTOFF" tragen BEIDE dieses Muster, unabhaengig von der
# Schreibweise. Ein echter, rein alphabetischer Code (z.B. "AHU") ist
# zusaetzlich IMMER kurz (klassische VAG-Codes: genau 3 Buchstaben) --
# "Hubraum"/"Getriebe"/"Kraftstoff" sind das nie. Beide Signale sind
# unabhaengig von Gross-/Kleinschreibung (`re.IGNORECASE` auf die GANZE
# Regex, nicht mehr nur auf das Label). Ein Wert MIT Ziffer (2UR-GSE, N55,
# OM651, B47D20) bleibt von der Laengenpruefung unberuehrt -- echte Codes
# dieser Form sind laenger und das ist normal. Keine Fahrzeug-/Motorcode-
# Allowlist: beide Prüfungen sind rein strukturell/lexikalisch, kein Katalog
# gueltiger Codes.
_RE_MOTORCODE_CLAIM = re.compile(
    r"(?:motorcode|motorkennung|motorkennbuchstaben|engine code)\s*[:|(]?\s*"
    r"([A-Z0-9][A-Z0-9-]{1,9})\b", re.IGNORECASE)

# Erkennt, ob der erfasste Wert selbst der ERSTE von mindestens ZWEI
# aufeinanderfolgenden, rein alphabetischen Pipe-Segmenten ist (angewendet
# auf den Text AB der Fundstelle des Werts) -- das generische Signal fuer
# "das ist eine Mehrfach-Kopfzeile, keine einzelne Werte-Zuordnung". Ein
# Wert mit Ziffer/Bindestrich-Zahlenteil ("2UR-GSE") kann dieses Muster
# nie erfuellen (die Zeichenklasse hier ist rein alphabetisch), ein
# Zahlenwert/eine Einheit ("4969", "477PS") ebenfalls nicht.
_RE_MOTORCODE_FOLGE_HEADERZEILE = re.compile(
    r"^[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß-]{1,15}\s*\|\s*[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß-]{1,15}\b")

# Generische Ausschlussliste: eigenstaendige, fehlende/unbestimmte Werte UND
# kurze Einheiten-/Feldbezeichnungen, die sonst -- weil sie wie ein kurzer
# alphabetischer Code AUSSEHEN -- faelschlich durchrutschen koennten. Keine
# Motorcode-Allowlist (die waere das Gegenteil: eine Liste GUELTIGER Codes).
_MOTORCODE_AUSSCHLUSS = frozenset({
    "UNBEKANNT", "UNKNOWN", "KEINE", "NA", "TBD", "OFFEN", "KA", "NONE",
    "PS", "KW", "NM", "KM", "MM", "CM", "KG", "TYP", "TYPE",
})


def _ist_motorcode_wert(code: str, folgetext: str = "") -> bool:
    """Schliesst drei generische Fehlerquellen aus -- keine davon braucht
    einen Fahrzeug-/Markenbezug oder eine Codeliste:
      * ein fehlender/unbestimmter Wert oder eine kurze Einheit/Feld-
        bezeichnung (`_MOTORCODE_AUSSCHLUSS`);
      * ein rein alphabetischer "Code" ohne jede Ziffer, der LAENGER als
        ein klassischer 3-stelliger VAG-Code ist (`len > 4`) -- ein
        deutsches Tabellenkopf-/Feldwort wie "Hubraum"/"Getriebe" faellt
        darunter, "AHU"/"ALH" nicht;
      * der Wert ist selbst der Anfang einer Mehrfach-Kopfzeile (siehe
        `_RE_MOTORCODE_FOLGE_HEADERZEILE`) -- bei einer mehrdeutigen
        Kopfzeile wird KEINE Bestaetigung erzeugt, unabhaengig von der
        Schreibweise."""
    code_norm = code.upper()
    if code_norm in _MOTORCODE_AUSSCHLUSS:
        return False
    if not any(ch.isdigit() for ch in code) and len(code) > 4:
        return False
    if _RE_MOTORCODE_FOLGE_HEADERZEILE.match(folgetext):
        return False
    return True


_ZAHLWORT = {"vier": 4, "fünf": 5, "fuenf": 5, "sechs": 6, "sieben": 7, "acht": 8,
             "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12, "zwoelf": 12}


_ROEMISCH = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}
# Wörter, die einen Code als GENERATIONSbezeichnung ausweisen. Ohne diesen
# Kontext (oder ein passendes Jahresfenster) ist ein Kürzel hinter dem
# Modellnamen oft eine Ausstattungs-/Karosserie-/Leistungsbezeichnung
# ("… G 184", "… RF", "… GTI") und kein Generationscode.
_GEN_KONTEXT = re.compile(r"generation|baureihe|modellreihe|\btyps?\b|codename|werkscode"
                          r"|interne?[nr]?\s+bezeichnung|\bgen\.", re.IGNORECASE)

# Semantische Generationslücke (Fortsetzungsauftrag, Runde 3, CONFIRMED):
# "Ein Produktionsfenster belegt zeitliche Passung, aber nicht die semantische
# Rolle des Codes." `kontext` (oben) schliesst bewusst vage Woerter wie
# "Generation"/"Typ"/"Modellreihe" ein -- genau diese Vagheit erlaubte es
# bisher, dass `jahr_belegt` ALLEIN (kombiniert mit SOLCH einem vagen Wort
# irgendwo in der Naehe) jeden Code bestaetigte, der zufaellig von einem
# passenden Zeitraum begleitet wird. Eine echte Leistungsvariante (z.B. "911
# GT3") wird i.d.R. im selben Produktionszeitraum wie ihre Chassisgeneration
# gebaut/verkauft -- ein Jahresfenster um den Variantennamen ist deshalb KEIN
# Beleg dafuer, dass der Variantenname selbst eine Generation bezeichnet
# (reproduziert: "Der Porsche 911 GT3, diese Generation, wird seit 2021
# gebaut." bestaetigte bisher faelschlich generation="GT3").
#
# Eindeutige Bezeichnung ("Baureihe", "Chassis-Code"/"Chassiscode",
# "Werkscode", "Codename", "interne Bezeichnung") ist dagegen KEINE vage
# Ortsangabe, sondern eine explizite Rollenzuweisung: eine Quelle, die einen
# Code so einfuehrt, behauptet damit ausdruecklich, dass GENAU DIESER Code
# eine Baureihe/einen Chassiscode bezeichnet -- unabhaengig davon, ob zusaetz-
# lich ein Jahresfenster genannt wird. Diese Woerter sind eine STRENGE Teil-
# menge von `_GEN_KONTEXT` (ohne "generation"/"typ"/"modellreihe"/"gen." --
# genau die vagen Woerter, die den obigen Fehlalarm ausloesten).
#
# "Modellcode" (Fortsetzungsauftrag, PRAEZISIERUNG nach Gegenpruefung): KEINE
# real gefundene Quelle nennt USC10 woertlich "Modellcode"/"Chassiscode"/
# "Baureihe" -- das ist ausdruecklich NICHT belegt, nur das generische Wort
# selbst (s.u.). Real mit exakten URLs gegengeprueft (27.10.2026):
#   * shop.rolling.hu/catalog/SCHAEFFLER/lexus-rc-c1-f-usc10 -- Feld "Model":
#     "RC (C1)", Feld "Type": "F (USC10)". Getrennte Felder, kein deutsches
#     Rollenwort fuer USC10.
#   * adac.de/.../lexus/rc/xc1/339429 -- Feld "Herstellerinterne
#     Baureihenbezeichnung": "RC (UXC1) (12/14-06/25)" -- die Baureihen-
#     Rolle traegt laut ADAC der BREITERE Code UXC1 (deckt RC 300h/RC 350/
#     RC F gemeinsam ab), NICHT USC10.
#   * carwiki.de/lexus-rc-hsn-tsn-schluesselnummer/ -- Feld "Baureihe":
#     "XC1"; TSN (AJM/ALQ) variiert dagegen nach Motor/Getriebe/Karosserie
#     bei GLEICHBLEIBENDEM HSN 5013 -- TSN ist amtlicher Typschluessel,
#     keine Generationsrolle.
# USC10 bleibt damit laut realer Quellenlage ein Teiledatenbank-"Type"
# UNTERHALB der Baureihen-Ebene (XC1/UXC1) -- und ist fuer `identity.
# generation` dieser App DESHALB FALSCH, nicht nur unbelegt (Korrektur nach
# Gegenpruefung, RC-F-Identitaetsvertrag-Folgeauftrag, 2026-10-28). Die
# vorherige Begruendung hier ("UXC1 allein waere fuer die Geschwister-
# varianten-Abgrenzung zu grob") war selbst unbelegt und ist durch direkten
# Codetest widerlegt (s. test_rc_f_generation_contract_diagnose.py): die
# Abgrenzung RC 300h/RC 350 vs. RC F passiert bereits auf CORE-MODEL-Ebene
# (`_ziel()["modell"]` = "RC F", nicht bloss "RC") -- `ausgerichtet()`/
# `_modell_ausgerichtet()` verwerfen eine reine RC-300h-/RC-350-Quelle schon
# beim Alignment, BEVOR `_fremde_generation()` ueberhaupt einen Code
# vergleicht. Und `_fremde_generation()` selbst vergleicht nur rohe
# Codestrings: UXC1 unterscheidet eine "300H"/"350"-Quelle davon GENAUSO
# sicher wie USC10 (keiner der beiden Strings ist je gleich "300H"/"350").
# `identity.generation` soll laut Datenmodell (`db/schema.sql::baureihe.
# generation`) und `_ziel()`s eigenem Drei-Stufen-Vertrag (CORE MODEL /
# AUXILIARY CANDIDATE / CONFIRMED GENERATION, s. dort) die BAUREIHEN-Ebene
# tragen, nicht die Teiledatenbank-Type-Ebene -- USC10 waere damit, selbst
# bei einer kuenftigen Rollenbeleg-Quelle, der FALSCHE Wert fuer dieses Feld,
# kein Zielkonflikt zwischen Belegbarkeit und Variantenabgrenzung.
#
# OFFENE LUECKE (hier bewusst NICHT behoben, s. Uebergabe "RC-F-Identitaets-
# blockade"): die echte Baureihen-Evidenz (ADAC "RC (UXC1)", carwiki.de
# "Baureihe: XC1") ist mit dem aktuellen CORE-MODEL-Anker ("RC F") strukturell
# UNERREICHBAR -- `_generationscodes()` sucht den Code nur UNMITTELBAR hinter
# dem kompletten Core-Model-String, die realen Quellen schreiben UXC1/XC1
# aber hinter dem BLOSSEN Markennamen "RC" (ohne "F"). Direkt nachgewiesen:
# mit denselben drei Quellen liefert `_generationscodes()` fuer ADAC/
# carwiki.de keinen einzigen Codekandidaten, nur fuer das rolling.hu-Type-Feld
# (USC10) -- echter Evidenzverlust, keine erfundene Bestaetigung, und nicht
# Lexus-spezifisch: strukturell fuer jedes Modell mit eigenem Trimnamen
# (Marke+Modell+Trim vs. Marke+Modell). Eine generische Behebung muesste
# Modellname und Trimname trennen, OHNE das fahrzeug-/markenspezifisch zu
# raten -- exakt die Baureihe-vs-Variante-Frage, die `app/autofinder_
# variante.py` fuer AutoFinder bereits NICHT per Heuristik, sondern nur ueber
# belegbare Eigenschaften loest. Bleibt als Architekturentscheidung offen,
# nicht auf Verdacht umgesetzt. Die naheliegendere, nicht-spekulative Abhilfe
# fuer DIESEN konkreten Fall ist ohnehin kein Vertrags-Patch, sondern ein
# fehlender Katalogeintrag: Lexus RC fehlt komplett in `db/seed_fahrzeug
# daten.sql` (kein `baureihe`-Eintrag) -- mit einem regulaeren Eintrag
# (modell="RC", generation="UXC1", motorvariante "RC F") kaeme die Generation
# ueber den laengst bestehenden, bereits korrekten DB-Pfad
# (`VehicleIdentity.from_check_context`), ganz ohne Web-Extraktion. Reine
# Datenarbeit, kein Identitaetsvertrags-Fix -- und laut AGENTS.md nicht
# ungefragt zu starten.
# "Modellcode" selbst bleibt dennoch generisch belegt (nicht USC10-spezifisch):
# oeffentlich dokumentiert (VIN-Aufbau, carVertical-Erklaerung zu Audi/
# Mercedes-FIN) bezeichnet "Modellcode" dort ausdruecklich die Stellen, die
# das MODELL/die BAUREIHE identifizieren, UND wird explizit von
# "Aufbauvariante"/"Motortyp" (eigene Stellen) abgegrenzt -- dieselbe
# semantische Kategorie wie "Baureihe"/"Chassiscode"/"interne Bezeichnung",
# nicht die eines Varianten-/Motor-/Ausstattungscodes. Ergaenzt als
# KANDIDATENSIGNAL fuer JEDEN Code, dessen Quelle es tatsaechlich so nennt --
# NICHT als nachtraeglich erfundene Bestaetigung speziell fuer USC10. Wird
# eine solche Quelle nie gefunden, bleibt USC10 laut `_generationen_pruefen()`
# UNKNOWN -- das ist korrekt, kein Fehler. "Typcode"/"Motorcode"/
# "Variantencode" bleiben bewusst draussen (unbelegt, dass diese dieselbe
# Generations-Rolle statt einer Varianten-/Ausstattungsrolle behaupten) --
# keine Erweiterung auf Verdacht, nur die generisch belegte.
_ROLLE_BEZEICHNUNG = re.compile(
    r"baureihe|chassis-?code|werkscode|codename|modellcode"
    r"|interne?[nr]?\s+bezeichnung", re.IGNORECASE)

# Feldlabel:Wert-Rollenbeleg (RC-F-Identitaetsvertrag-Folgeauftrag, zweite
# Runde, 2026-10-28): reale Specsheet-/Tabellenquellen (carwiki.de: "Baureihe:"
# gefolgt von einem Zeilenumbruch, dann erst der Wert) trennen Label und Wert
# durch einen Doppelpunkt/Zeilenumbruch -- `_TEILSATZGRENZE_ROLLENBELEG`
# behandelt ":" bewusst als Teilsatzgrenze (sonst wuerden beliebig weit
# entfernte Saetze zusammenwachsen), wodurch das Rollenwort und der direkt
# danach stehende Code in ZWEI verschiedene Teilsaetze fallen und
# `_rollenbeleg_eigener_teilsatz()` den Beleg verliert, obwohl die Quelle ihn
# eindeutig macht. Generische, ZUSAETZLICHE (nicht ersetzende) Struktur-
# erkennung: endet der Text UNMITTELBAR vor der Code-/Modell-Anker-Fundstelle
# (Fenster `_ROLLENBELEG_FELDLABEL_FENSTER`, eng genug, dass kein zweites
# Label dazwischenpassen kann) auf eines der `_ROLLE_BEZEICHNUNG`-Woerter,
# optional gefolgt von einem Doppelpunkt und Leerraum/Zeilenumbruch, dann NICHTS
# sonst -- das ist dieselbe Rolle, nur in Tabellenform statt in einem Satz.
# Hebt KEINE Teilsatzgrenze generell auf (betrifft nur dieses enge Fenster)
# und erweitert die Rollenwortliste selbst nicht.
_RE_ROLLENBELEG_FELDLABEL = re.compile(
    r"(?:" + _ROLLE_BEZEICHNUNG.pattern + r")\s*:?\s*\Z", re.IGNORECASE)
_ROLLENBELEG_FELDLABEL_FENSTER = 40


def _rollenbeleg_feldlabel(text: str, start: int) -> bool:
    """Strukturelle Feldlabel:Wert-Form ('Baureihe:\\nRC XC1', 'Baureihe RC
    (UXC1)') als vom Teilsatz UNABHAENGIGER Rollenbeleg -- s. Kommentar oben
    bei `_RE_ROLLENBELEG_FELDLABEL`."""
    ausschnitt = text[max(0, start - _ROLLENBELEG_FELDLABEL_FENSTER):start]
    return bool(_RE_ROLLENBELEG_FELDLABEL.search(ausschnitt))


# Teilsatzgrenze für `_rollenbeleg_eigener_teilsatz()`: wie `_TEILSATZGRENZE`
# unten, aber ein Punkt direkt NACH einer Ziffer ("4.") zählt nicht als
# Grenze -- sonst würde die ordinale Zählung "4. Generation" an ihrem
# eigenen Punkt zerschnitten (s. `_rollenbeleg_eigener_teilsatz()`-Docstring).
_TEILSATZGRENZE_ROLLENBELEG = re.compile(r"[;:!?]|\bsondern\b|(?<!\d)\.", re.IGNORECASE)

# Fortsetzungsauftrag (real reproduzierter Gegenfall, Marketingbezeichnungs-
# Fall): "Der Lexus RC F USC10 ist nur die Marketingbezeichnung dieser
# Baureihe." bestaetigte bisher faelschlich einen Rollenbeleg -- das blosse
# Wort "Baureihe" (eigene Teilalternative in `_ROLLE_BEZEICHNUNG`, s.o.)
# stand zwar im selben Teilsatz wie der Code, bezeichnet dort aber NICHT den
# Code selbst als Baureihe, sondern ist das Genitivobjekt eines ANDEREN,
# vorangehenden Substantivs ("Marketingbezeichnung ... DIESER BAUREIHE" =
# "die Marketingbezeichnung VON dieser Baureihe") -- ein Verweis AUF eine
# bereits anderswo etablierte Baureihe, keine Rollenzuweisung AN den Code.
# Alle bestehenden, echten Rollenbelege ("[CODE], Chassiscode dieser
# Baureihe, ...", "diese Baureihe", "Baureihe seit 2015", "(Baureihe)", "Die
# Baureihe Modell CODE ...") haben dieses vorangehende Fremdsubstantiv NICHT
# -- "Baureihe" steht dort entweder allein/am Satzanfang oder direkt hinter
# einem der ANDEREN Rollenwoerter (die dann ohnehin schon selbst als eigener
# Treffer zaehlen, s.u.). Generischer Fix (keine Fahrzeug-/Code-Ausnahme,
# keine wachsende Liste): ein zusammengesetztes Substantiv, das auf
# "bezeichnung" ENDET (Marketingbezeichnung, Verkaufsbezeichnung,
# Handelsbezeichnung, Modellbezeichnung, ...), unmittelbar gefolgt von einem
# Genitivartikel ("der"/"dieser"/"des"/"diese"), macht aus dem NACHFOLGENDEN
# "Baureihe" ein Genitivobjekt DIESES Substantivs -- kein eigenstaendiger
# Rollenbeleg mehr fuer den Code. Betrifft NUR die bare "Baureihe"-
# Alternative: "interne Bezeichnung der Baureihe" bleibt unveraendert
# bestaetigt, weil "interne Bezeichnung" selbst schon ein eigener,
# unveraenderter Treffer in `_ROLLE_BEZEICHNUNG` ist (endet nicht auf
# "bezeichnung" MIT vorangestelltem Fremdwort -- "interne" ist kein
# Substantiv, sondern das Adjektiv der Phrase selbst).
_BAUREIHE_FREMDBEZUG = re.compile(r"\w*bezeichnung\s+(?:der|dieser|des|diese)\s*\Z", re.IGNORECASE)


def _generationscodes(r: dict, modell: str | None) -> list[dict]:
    """Generationscodes, die UNMITTELBAR hinter dem Modellnamen stehen ("MX-5 ND",
    "MX-5 (ND)", "Golf VII"), je mit

      zeitraum     Jahresfenster direkt dahinter (oder None),
      kontext      ein (vages) Generationswort in der Umgebung ("vierte
                   Generation (… ND)") bzw. die Form "Modell IV (ND …)" --
                   sonst ist das Kürzel oft eine Ausstattungs-/Karosserie-/
                   Leistungsbezeichnung. Allein NIE hinreichend (s.
                   `_generationen_pruefen()`).
      rollenbeleg  eine EINDEUTIGE Bezeichnung ("Baureihe …", "Chassis-Code
                   …", "Werkscode …", "Codename …", "interne Bezeichnung …")
                   in der Umgebung -- eine explizite Rollenzuweisung, kein
                   bloss benachbartes Wort (s. Moduldocstring-Block oben).
      nummer       Generationsnummer aus "Modell IV (ND …)" (IV = 4).

    Eine römische Zahl mit folgendem Klammercode ist die NUMMER, der
    Klammercode der CODE derselben Generation (vorher entstand daraus "IV" als
    Code, und die Seite galt sogar als "andere Generation")."""
    text = f"{r.get('title') or ''}. {r.get('content') or ''}"
    modell_rx = r"[\s-]*".join(re.escape(t) for t in re.split(r"[\s-]+", (modell or "").strip()) if t)
    if not modell_rx:
        return []
    out: list[dict] = []
    # Modellname case-insensitiv, der Generationscode selbst in Großbuchstaben.
    #
    # Lexus-RC-F-Forensik (Root Cause 3, CONFIRMED): die vorherige Fassung
    # begrenzte JEDEN Code auf max. 2 Buchstaben (`[A-Z]{1,2}\d{0,3}`) — ein
    # willkürliches, an europäischen Konventionen (F82, W205, B9) orientiertes
    # Limit, das japanische/US-Konventionen mit 3+ Buchstaben-Präfix (Lexus
    # "USC10") STRUKTURELL nie erfassen konnte, selbst bei perfekter Quelle.
    # Statt die Zahl einfach auf 3 zu erhöhen (verworfen — repariert nur diesen
    # einen Fall): die eigentlich unterscheidende, generische Eigenschaft ist
    # NICHT die Buchstabenzahl, sondern ob Ziffern vorhanden sind. Ein Code MIT
    # Ziffern ("USC10", "F82", "W205") ist durch die Ziffer bereits eindeutig
    # von einem normalen Wort unterscheidbar — dort darf der Buchstaben-Präfix
    # länger sein. Ein Code OHNE Ziffern ("ND", "RF") bleibt bewusst auf 1-2
    # Buchstaben begrenzt, sonst würden normale Wörter ("Test", "Plus") mit-
    # erfasst. Beide Zweige durchlaufen weiterhin unverändert die bestehende
    # Jahr-/Kontext-Bestätigung (`_generationen_pruefen` unten) — dieses Gate,
    # nicht die Capture-Breite, ist der eigentliche Schutz gegen Fehltreffer.
    for m in re.finditer(r"(?i:" + modell_rx + r")\s*\(?\s*([A-Z]{1,4}\d{1,4}|[A-Z]{1,2}|[IVX]{1,4})\b\)?", text):
        code = m.group(1)
        if code.upper() in ("PS", "KW", "TDI", "TSI", "GT", "S", "I"):
            continue
        ende = m.end()
        nummer = None
        if code in _ROEMISCH:
            folge = re.match(r"\s*\(\s*([A-Z]{1,4}\d{1,4}|[A-Z]{1,2})\b", text[m.end():])
            if folge:
                nummer, code = _ROEMISCH[code], folge.group(1)
                ende = m.end() + folge.end()
        umgebung = text[ende: ende + 70]
        fenster = text[max(0, m.start() - 60): m.start()] + " " + text[m.start(): ende + 30]
        kontext = bool(nummer) or bool(_GEN_KONTEXT.search(fenster))
        # Eindeutige Bezeichnung ODER ordinale Generationszaehlung ("die
        # vierte Generation", "4. Generation" -- wie die Klammer-Roemisch-
        # Zaehlung "Modell IV (CODE)" eine explizite Zaehlung, kein vages
        # Wort), gebunden an den EIGENEN Teilsatz der Code-Nennung (nicht nur
        # Zeichen-Reichweite -- s. `_rollenbeleg_eigener_teilsatz()`-
        # Docstring) UND selbst nicht verneint.
        rollenbeleg = (_rollenbeleg_eigener_teilsatz(text, m.start(), ende)
                      or _rollenbeleg_feldlabel(text, m.start()))
        # `start`/`ende` additiv (RC-F-Identitaetsvertrag-Folgeauftrag,
        # 2026-10-28): bestehende Aufrufer lesen weiterhin nur ihre bekannten
        # Schluessel, `_modellfamilie_zugehoerigkeit_belegt()` unten braucht
        # aber die Fundstelle, um einen ueber ein Modell-PRAEFIX gefundenen
        # Code gegen das volle Zielmodell zu verankern.
        out.append({"code": code, "zeitraum": zeitraum(umgebung), "kontext": kontext,
                    "nummer": nummer, "rollenbeleg": rollenbeleg, "start": m.start(), "ende": ende})
    # Arabische Ordnungszahl-Generationszaehlung in EIGENER Klammer ("RC (1.
    # Generation) (XC1)") -- Fortsetzungsauftrag (Feldlabel-/Struktur-
    # Nachfolgeauftrag, 2026-10-28), real bei auto-motor-und-sport.de gefunden:
    # dieselbe strukturelle Eindeutigkeit wie die bestehende roemische Form
    # oben ("Modell IV (CODE)"), nur mit einer arabischen Ordnungszahl in
    # einer EIGENEN Klammer VOR der Codeklammer, statt der roemischen Zahl
    # direkt davor im selben Klammerausdruck. Reine Zusatzerkennung -- die
    # Hauptschleife oben matcht hier gar nicht (der Codegruppen-Zwang direkt
    # nach dem Modell schlaegt fehl, sobald "(1." statt eines Buchstaben
    # folgt), es geht also kein bestehender Treffer verloren oder doppelt.
    for m in re.finditer(r"(?i:" + modell_rx + r")\s*\(\s*(\d{1,2})\.\s*generation\s*\)\s*"
                         r"\(\s*([A-Z]{1,4}\d{1,4}|[A-Z]{1,2})\b\s*\)", text, re.IGNORECASE):
        nummer_ord, code = int(m.group(1)), m.group(2)
        ende = m.end()
        umgebung = text[ende: ende + 70]
        out.append({"code": code, "zeitraum": zeitraum(umgebung), "kontext": True,
                    "nummer": nummer_ord,
                    "rollenbeleg": _rollenbeleg_eigener_teilsatz(text, m.start(), ende)
                                  or _rollenbeleg_feldlabel(text, m.start()),
                    "start": m.start(), "ende": ende})
    return out


# Familien-Zugehoerigkeitsbeleg (RC-F-Identitaetsvertrag-Folgeauftrag,
# 2026-10-28): `_generationscodes()` verankert bisher AUSSCHLIESSLICH auf dem
# vollen Core-Model-String (`ziel["modell"]`, z.B. "RC F") -- ein real
# belegter Baureihen-/Generationscode, den eine Quelle hinter dem BLOSSEN
# Markennamen/der Modellfamilie schreibt ("RC (UXC1)", nicht "RC F (UXC1)"),
# bleibt dadurch strukturell unerreichbar, selbst wenn dieselbe Quelle das
# Zielmodell direkt daneben nennt (real reproduziert: ADAC-Fahrzeugdatenblatt
# "Modell RC F" / "Baureihe RC (UXC1) (12/14-06/25)" als zwei Zeilen DERSELBEN
# Tabelle; carwiki.de HSN/TSN-Uebersicht "Baureihe: RC XC1" mit "Lexus RC F
# Carbon Automatik" als Tabellenzeile DERSELBEN Baureihen-Uebersicht).
#
# Weder blosses Abschneiden des letzten Modell-Tokens noch Namensaehnlichkeit
# duerfen das fuer sich bestaetigen (s. Auftrag) -- deshalb zwei UNABHAENGIGE
# Bedingungen, beide muessen gelten:
#   1. Ein Code wird -- wie bisher -- nur durch den bestehenden, unveraenderten
#      Rollenbeleg-/Struktur-Mechanismus (`_rollenbeleg_eigener_teilsatz()`/
#      `_generationen_pruefen()`) bestaetigt, jetzt zusaetzlich auch verankert
#      auf einem PRAEFIX des Modellnamens (`_modellfamilie_praefixe()` --
#      reine, aussagefreie Kandidaten, analog zu `auxiliary_code` in `_ziel()`,
#      NIEMALS allein hinreichend).
#   2. ZUSAETZLICH muss DIESELBE Quelle einen echten ZUGEHOERIGKEITSBELEG
#      liefern (`_modellfamilie_zugehoerigkeit_belegt()`), NICHT nur
#      raeumliche Naehe: das VOLLE Zielmodell muss innerhalb von
#      ±`_FAMILIE_FENSTER` Zeichen genannt sein, OHNE dass dazwischen eine
#      echte Absatz-/Ueberschriftengrenze, eine FREMDE Marke oder eine
#      Verneinung liegt (Gegenpruefung, 2026-10-28: eine Vergleichsseite, ein
#      Navigations-/Querverweis oder eine ausdrueckliche Verneinung duerfen
#      die Zuordnung NICHT allein aus Namensnaehe herstellen). Die Quelle
#      belegt damit SELBST, dass das Zielmodell zu dieser Baureihe/Generation
#      gehoert, statt dass unser Code das aus Namensaehnlichkeit schliesst.
#   Codes ohne Ziffer (z.B. ein blosses "F" aus "RC F", das die Praefix-
#   Verankerung auf "RC" sonst als Code einfangen würde) werden für den
#   Praefix-Pfad verworfen -- ein kürzerer Anker ist unschärfer als der volle
#   Core-Model-Anker, der bereits ziffernlose 1-2-Buchstaben-Codes zulässt
#   (s. `_generationscodes()`-Kommentar, Root Cause 3); die Ziffer bleibt hier
#   die zusätzliche Eindeutigkeit, die den kürzeren Anker aufwiegt.
# Betrifft NUR die Generation (s.u. Aufrufer) -- Motor-/Getriebe-/Antriebs-/
# Wartungs-/Defekt-Claims bleiben unveraendert an die bestehende Claim-Level-
# Vehicle-Binding (`_segment_kontext_saetze()`) gebunden, von dieser
# Erweiterung unberuehrt.
_FAMILIE_FENSTER = 1000


def _modellfamilie_praefixe(modell: str | None) -> list[str]:
    """Strukturelle PRAEFIXE des Modellnamens ('RC F' -> ['RC']), OHNE jede
    eigene Aussagekraft -- reine Kandidaten fuer `_generationscodes()`-
    Verankerung, analog zu `auxiliary_code` in `_ziel()`. Ein Praefix allein
    bestaetigt NIE eine Baureihen-Zugehoerigkeit (das waere reine
    Namensaehnlichkeit); erst `_modellfamilie_zugehoerigkeit_belegt()`
    (eigene, unabhaengige Quellenaussage, nicht nur raeumliche Naehe)
    entscheidet, ob ein hierueber gefundener Code
    uebernommen wird. Einwortmodelle ("RC", "911") liefern keine Praefixe --
    es gibt dort nichts abzutrennen."""
    teile = [t for t in re.split(r"[\s-]+", (modell or "").strip()) if t]
    return [" ".join(teile[:i]) for i in range(1, len(teile))]


def _modell_regex(modell: str | None) -> str:
    return r"[\s-]*".join(re.escape(t) for t in re.split(r"[\s-]+", (modell or "").strip()) if t)


# Fortsetzungsauftrag (Gegenpruefung, 2026-10-28): reine raeumliche Naehe
# ("das Zielmodell steht irgendwo innerhalb von N Zeichen") reichte nicht als
# Zugehoerigkeitsbeleg -- eine Vergleichsseite kann das Zielmodell nahe am
# Baureihencode EINES ANDEREN Fahrzeugs nennen, ein Navigations-/Querverweis
# kann das Zielmodell nennen, waehrend der Code zum Hauptartikel ueber ein
# ANDERES Fahrzeug gehoert, oder die Quelle kann die Zuordnung ausdruecklich
# VERNEINEN. `_modellfamilie_zugehoerigkeit_belegt()` ersetzt die reine
# Fenstersuche durch drei generische, bereits anderswo im Modul genutzte
# Pruefungen -- keine neue Erkennung, keine Allowlist:
#   * echte Absatz-/Ueberschriftengrenze (`_ABSATZ_ODER_UEBERSCHRIFT_TRENNER`,
#     bereits fuer Segmentgrenzen genutzt) begrenzt den Suchraum -- ein
#     Navigationsblock/Querverweis vor einer echten Abschnittsgrenze zieht
#     den Hauptartikel-Code nicht mehr heran, und umgekehrt.
#   * eine FREMDE Marke (dieselbe `MARKEN`-Menge wie in `ausgerichtet()`)
#     zwischen Codefundstelle und Zielmodell-Nennung verwirft den Fund --
#     der Vergleichsseiten-Fall.
#   * eine Verneinung IM TEILSATZ der gefundenen Zielmodell-Nennung
#     (dieselbe `_verneint_im_teilsatz()`/`_verneint_nach_position()`-Logik
#     wie jeder andere Claim im Modul) verwirft den Fund ebenfalls.
# Gegenpruefung (Folgeauftrag, zweite Praezisierungsrunde, 2026-10-28):
# "gleicher Absatz + keine fremde Marke + keine Verneinung" sind reine
# AUSSCHLUSSPRUEFUNGEN -- sie lassen "Zielmodell steht irgendwo im selben
# Block" als FUND durchgehen, auch wenn ein ANDERES Modell DERSELBEN Marke
# (z.B. eine zweite Tabellenzeile "Modell RC 350, Baureihe RC (XYZ)" direkt
# vor unserem Zielmodell) dem Code naeher steht als unser Zielmodell. Reale
# Specsheets (ADAC) und Uebersichten (carwiki.de) markieren "wer gemeint
# ist" aber durch ihr EIGENES Feldlabel ("Modell"/"Fahrzeug") -- das ist der
# POSITIVE Beleg, den `_modellnennungen_kandidaten()` einsammelt.
#
# Positiv-Regel: die ZUM CODE NAECHSTGELEGENE Feldlabel-Modellnennung (egal
# in welcher Richtung; ODER die exakte Zielmodell-Nennung selbst, falls
# naeher) muss mit dem Zielmodell KOMPATIBEL sein (identisch, ODER eine
# Teilmengenbeziehung wie "RC" zu "RC F" -- dieselbe Teilmengen-Idee wie
# `familien_kompatibel()` in app/autofinder_identity.py, hier lokal
# nachgebildet, kein Modulimport zwischen AutoFinder und KaufCheck). Ist die
# naechstgelegene Nennung ein ANDERES, NICHT kompatibles Modell (z.B.
# "RC 350" bei Zielmodell "RC F"), ist der Code dessen Zeile/Record
# zugeordnet -- kein Fund, unabhaengig davon, ob das Zielmodell WEITER WEG
# ebenfalls vorkommt. Zusaetzlich muss das Zielmodell SELBST (nicht nur ein
# kompatibler Praefix wie "RC") im selben, absatzbegrenzten Fenster
# vorkommen -- ein blosser Familienname allein ("Fahrzeug: RC") bestaetigt
# nichts, er macht nur den naheliegenden Kandidaten nicht-widersprechend.
#
# BEWUSST NUR Feldlabel, KEINE "Marke + folgende Grossbuchstaben-Woerter"-
# Heuristik (anders als der F16-Fix bei `_RE_GROSSGESCHRIEBENE_FOLGE"): im
# Deutschen werden ALLE Substantive grossgeschrieben, nicht nur Eigennamen
# ("Lexus RC Generationen", "Lexus RC Baureihen") -- eine solche Heuristik
# zieht hier beliebige Substantive der Umgebung als scheinbar "fremdes
# Modell" ein (reproduzierter Gegenfall: ein Seitentitel "Lexus RC
# Generationen" wurde faelschlich als eigenstaendiges, inkompatibles Modell
# gelesen). Reale Positivfaelle (ADAC/carwiki.de) sind beide Feldlabel-
# gestuetzt; wo kein Feldlabel vorliegt (z.B. auto-motor-und-sport.de), trägt
# die separate EXAKTE Zielmodell-Erwaehnung (`modell_treffer` unten) den
# Fund bereits allein.
_MODELLNENNUNG_LABELWORT = frozenset(("modell", "model", "fahrzeug", "marke", "baureihe", "typ"))
_RE_MODELLNENNUNG_FELD = re.compile(
    r"\b(?i:modell|model|fahrzeug)\s*:?\s+([A-ZÄÖÜ0-9][\wÄÖÜäöüß-]*"
    r"(?:\s+(?i:(?!modell|model|fahrzeug|marke|baureihe|typ))[A-ZÄÖÜ0-9][\wÄÖÜäöüß-]*){0,3})")


def _modellnennungen_kandidaten(text: str) -> list[tuple[int, int, frozenset]]:
    """Feldlabel-Kandidaten fuer 'hier wird EIN Modell genannt': ein
    Feldlabel ("Modell"/"Model"/"Fahrzeug", wie ADAC/carwiki.de es selbst
    nennen) gefolgt von seinem Wert. Label-Woerter selbst (Modell/Model/
    Fahrzeug/Marke/Baureihe/Typ) zaehlen nie als Wert -- sonst waere
    "Fahrzeug: Modell" bereits wegen des Worts "Modell" ein scheinbar
    fremdes Modell. Case-insensitiv nur fuer das Feldlabel/die Ausschluss-
    woerter selbst (`(?i:...)`, gescoped) -- der Wert bleibt bewusst
    gross-/kleinschreibungssensitiv (verhindert, dass ein beliebiges
    grossgeschriebenes deutsches Substantiv wie "Generationen" als
    fortgesetzter Modellname mitgelesen wird, s. Moduldocstring oben)."""
    kandidaten: list[tuple[int, int, frozenset]] = []
    for m in _RE_MODELLNENNUNG_FELD.finditer(text):
        tok = frozenset(_tokens(m.group(1)))
        if tok and not tok <= _MODELLNENNUNG_LABELWORT:
            kandidaten.append((m.start(1), m.end(1), tok))
    return kandidaten


_RE_SATZGRENZE_EIGENE = re.compile(r"[.;!?]")


def _modellfamilie_zugehoerigkeit_belegt(text: str, marke: str | None, modell: str | None,
                                         start: int, ende: int,
                                         fenster: int = _FAMILIE_FENSTER) -> bool:
    from app.vehicle_identity import MARKEN
    modell_rx = _modell_regex(modell)
    if not modell_rx:
        return False
    marke_tokens = _tokens(marke)
    ziel_tokens = frozenset(_tokens(modell))
    modell_treffer = re.compile(r"(?i:" + modell_rx + r")\b")

    vor_anfang = max(0, start - fenster)
    vorspann = _ABSATZ_ODER_UEBERSCHRIFT_TRENNER.split(text[vor_anfang:start])[-1]
    vor_offset = start - len(vorspann)
    nach_grenze = ende + fenster
    nachspann = _ABSATZ_ODER_UEBERSCHRIFT_TRENNER.split(text[ende:nach_grenze])[0]
    nach_offset = ende

    # Schritt 0 (echte Fliesstext-Saetze): der EIGENE, durch Satzendezeichen
    # begrenzte Satz des Codes (nicht Doppelpunkt -- der trennt Feldlabel
    # von Wert INNERHALB desselben Records, s.u.) ist autoritativ, falls er
    # selbst eine Fahrzeug-Aussage traegt. Dieselbe Satzanfangs-Konstruktion
    # wie der F16-Fix ("Der/Die/Das <Marke> <Folge>", `_RE_SATZANFANG_
    # NOMINALPHRASE`/`_RE_GROSSGESCHRIEBENE_FOLGE`) -- NICHT aber dessen
    # strikte `_modell_ausgerichtet()`-Pruefung: die verlangt exakte
    # Uebereinstimmung und wuerde "Die Lexus RC (UXC1), Chassiscode dieser
    # Baureihe, ..." (gefundene Folge nur "RC", ein kompatibler Praefix,
    # keine andere Variante) faelschlich als Widerspruch werten (real
    # reproduziert). Hier zaehlt die TEILMENGEN-Kompatibilitaet wie beim
    # Feldlabel-Pfad: "RC" ⊆ "RC F" ist neutral, "RC 350"/"RC Convertible"
    # (NICHT kompatibel) ist ein definitiver Widerspruch, unabhaengig davon,
    # was WEITER WEG noch folgt (behebt den Gegenfall "zwei Modellfamilien
    # im selben Absatz" in Fliesstextform, ohne den F16-Mechanismus selbst
    # anzufassen).
    eigen_vor = [m.end() for m in _RE_SATZGRENZE_EIGENE.finditer(text, vor_anfang, start)]
    eigen_satz_start = eigen_vor[-1] if eigen_vor else vor_anfang
    eigen_nach = _RE_SATZGRENZE_EIGENE.search(text, ende, nach_grenze)
    eigen_satz_ende = eigen_nach.start() if eigen_nach else nach_grenze
    eigener_satz = text[eigen_satz_start:eigen_satz_ende]
    nach_marke = _RE_SATZANFANG_NOMINALPHRASE.match(eigener_satz)
    if nach_marke and _tokens(nach_marke.group(1)) <= marke_tokens:
        folge = _RE_GROSSGESCHRIEBENE_FOLGE.match(eigener_satz[nach_marke.end():])
        if folge:
            gefunden = frozenset(_tokens(folge.group(1)))
            if gefunden and not (gefunden <= ziel_tokens or ziel_tokens <= gefunden):
                return False

    # Schritt 1 (Feldlabel-Eigentuemer, Tabellen-/Specsheet-Form ohne
    # Satzendezeichen, z.B. ADAC/carwiki.de): die ZUM CODE naechstgelegene
    # "Modell:"/"Fahrzeug:"-Nennung entscheidet, welchem Record der Code
    # gehoert. RUECKWAERTS hat Vorrang -- Identitaetsfelder stehen in realen
    # Specsheets VOR Klassifikationsfeldern wie "Baureihe" (ADAC: "Modell
    # RC F ... Baureihe RC (UXC1)"; carwiki.de: "Fahrzeug: RC ... Baureihe:
    # RC XC1") --, NICHT reine Zeichendistanz (die haengt vom Zufall
    # unterschiedlich langer Feldnamen ab und fuehrt sonst in die falsche
    # Zeile, s. Gegenpruefung "Tabelle ohne Leerzeilen").
    rueck_feld = _modellnennungen_kandidaten(vorspann)
    vor_feld = _modellnennungen_kandidaten(nachspann)
    eigentuemer_tok = (max(rueck_feld, key=lambda k: k[1])[2] if rueck_feld
                       else (min(vor_feld, key=lambda k: k[0])[2] if vor_feld else None))
    if eigentuemer_tok is not None and not (eigentuemer_tok <= ziel_tokens
                                            or ziel_tokens <= eigentuemer_tok):
        return False   # naechstgelegenes Feldlabel nennt ein ANDERES Modell.

    # Schritt 2: das Zielmodell muss SELBST (nicht nur ein kompatibler
    # Praefix wie "RC") im Fenster vorkommen -- ohne Verneinung und ohne
    # fremde Marke dazwischen.
    exakte = ([(vor_offset + m.start(), vor_offset + m.end()) for m in modell_treffer.finditer(vorspann)]
             + [(nach_offset + m.start(), nach_offset + m.end()) for m in modell_treffer.finditer(nachspann)])
    if not exakte:
        return False
    exakte.sort(key=lambda se: max(se[0] - ende, start - se[1], 0))
    s0, e0 = exakte[0]
    if _verneint_im_teilsatz(text, s0) or _verneint_nach_position(text, s0):
        return False
    zwischen_start, zwischen_ende = (ende, s0) if s0 >= ende else (e0, start)
    if _tokens(text[zwischen_start:zwischen_ende]) & MARKEN - marke_tokens:
        return False   # fremde Marke zwischen Code und Zielmodell-Nennung.
    return True


# Negationssichere Identity-Claims (Backend-Folgeauftrag, reproduziert):
# "Der RC F wird nicht mit einer Achtstufen-Automatik ausgeliefert, sondern
# mit einem Sechsganggetriebe." erzeugte bisher trotzdem einen Automatik-
# Claim; "Der RC F faehrt nicht mit Diesel, sondern mit Benzin." erzeugte
# BEIDE Kraftstoff-Claims. Ursache: `_KRAFTSTOFF_CLAIM`/`_ANTRIEB_CLAIM`/
# `_RE_GETRIEBE_MANUELL`/`_RE_GETRIEBE_AUTO`/`_RE_MOTORCODE_CLAIM` pruefen nur
# `rx.search(text)`/`finditer(text)`, ohne jede Verneinungspruefung. Dieselbe
# Klasse von Fehler wurde fuer die Powertrain-Achse bereits generisch geloest
# (app/kraftstoff_powertrain.py::_positiv_erwaehnt) — hier dieselbe Idee auf
# die uebrigen, regexbasierten Identity-Claims angewendet (eigene, kleine
# Funktion statt Import, weil hier zusaetzlich die Matchposition fuer die
# Wertbildung (Stufenzahl/Gangzahl/Motorcode) gebraucht wird, nicht nur ein
# Bool).
#
# Scope bewusst klein, aber NICHT rein interpunktionsbasiert (Backend-
# Folgeauftrag, Nachbesserung): die erste Fassung teilte den "eigenen
# Teilsatz" an JEDEM Komma — das griff bei "nicht Diesel, sondern Benzin",
# aber (a) nicht bei "nicht Diesel sondern Benzin" OHNE Komma (das Komma war
# der einzige Grenzmarker, "sondern" selbst wurde nie erkannt) und (b) zu
# AGGRESSIV bei einem eingeschobenen Nebensatz OHNE "sondern", z. B. "nicht
# mit einer, auch optional angebotenen, Automatik" — das Komma vor "auch"
# beendete den verneinten Teilsatz faelschlich VORZEITIG, obwohl "Automatik"
# weiterhin im Skopus von "nicht" liegt. Jetzt: Teilsatzgrenzen sind
# Satzzeichen (. ; : ! ?) UND das Wort "sondern" selbst — NICHT mehr jedes
# Komma. Das loest beide Faelle gleichzeitig: "sondern" (mit oder ohne
# Komma davor) startet immer einen neuen, unverneinten Teilsatz; ein
# Komma OHNE "sondern" trennt dagegen nicht mehr und die Verneinung bleibt
# ueber den ganzen (durch das Zeitfenster begrenzten) Rest des Satzes gueltig
# — "lieber unbestaetigt als erfunden" bei unklarer Reichweite, wie gefordert.
#
# "nicht nur X" ist KEINE Verneinung von X (Ergaenzung, kein Ausschluss —
# "nicht nur mit Allrad, auch mit Frontantrieb lieferbar" bejaht Allrad).
# "nicht ohne X" hebt sich doppelt auf und zaehlt ebenfalls nicht als
# Verneinung von X. Jede andere Verneinung im eigenen Teilsatz fuehrt zu
# "nicht bestaetigt" (nie zu einer erfundenen Gegenbehauptung — ein verneinter
# Hybrid-Claim macht aus einem Fahrzeug nie automatisch einen bestaetigten
# Verbrenner; das entscheidet ausschliesslich `powertrain_aus_freitext()`
# ueber eine eigene, ausdrueckliche Formulierung).
_VERNEINUNGSWORT = re.compile(
    r"\bnicht\s+nur\b"
    r"|\bnicht\s+ohne\b"
    r"|\b(?:nicht|kein\w*|ohne|statt|anstatt)\b", re.IGNORECASE)
_VERNEINUNGS_AUSNAHME = re.compile(r"\A(?:nicht\s+nur|nicht\s+ohne)\Z", re.IGNORECASE)
_TEILSATZGRENZE = re.compile(r"[.;:!?]|\bsondern\b", re.IGNORECASE)
# Grosszuegig genug fuer einen eingeschobenen, durch Kommas abgetrennten
# Nebensatz ("nicht mit einer, auch optional angebotenen, Automatik" — rund
# 45 Zeichen von "nicht" bis zum Treffer); durch die Teilsatzgrenze oben
# weiterhin auf den eigenen Teilsatz begrenzt, kein unbeschraenktes Scannen.
_NEGATIONS_FENSTER = 70


def _verneint_im_teilsatz(text: str, start: int) -> bool:
    """Ob die Textstelle `start` (Beginn eines Claim-Treffers) im eigenen
    Teilsatz verneint wird. Siehe Moduldocstring-Block oben fuer die Regeln
    ("nicht nur"/"nicht ohne" zaehlen NICHT als Verneinung; Teilsatzgrenzen
    sind Satzzeichen ODER das Wort "sondern", NICHT jedes Komma)."""
    vorspann = text[max(0, start - _NEGATIONS_FENSTER):start]
    grenzen = [m.end() for m in _TEILSATZGRENZE.finditer(vorspann)]
    eigener_teilsatz = vorspann[grenzen[-1]:] if grenzen else vorspann
    for v in _VERNEINUNGSWORT.finditer(eigener_teilsatz):
        if _VERNEINUNGS_AUSNAHME.match(v.group()):
            continue
        return True
    return False


def _verneint_nach_position(text: str, start: int) -> bool:
    """Spiegelbild zu `_verneint_im_teilsatz()`: ob eine Verneinung NACH
    `start` im selben Teilsatz steht ("Das gilt NICHT für den RC F" --
    anders als beim Problemwort-Fall steht die Verneinung hier typischerweise
    HINTER dem Rückbezugswort, nicht davor)."""
    rest = text[start:start + _NEGATIONS_FENSTER]
    grenze = _TEILSATZGRENZE.search(rest)
    eigener_teilsatz = rest[:grenze.start()] if grenze else rest
    for v in _VERNEINUNGSWORT.finditer(eigener_teilsatz):
        if _VERNEINUNGS_AUSNAHME.match(v.group()):
            continue
        return True
    return False


def _rollenbeleg_eigener_teilsatz(text: str, start: int, ende: int) -> bool:
    """Ob im EIGENEN Teilsatz der Code-Nennung [start, ende) (dieselbe
    Teilsatzgrenze wie bei der Verneinungspruefung, `_TEILSATZGRENZE`
    oben: Satzzeichen ODER "sondern", NICHT jedes Komma) eine eindeutige
    Bezeichnung (`_ROLLE_BEZEICHNUNG`) oder eine ordinale Generationszaehlung
    (`_RE_GEN_NUMMER`) steht -- UND diese selbst nicht verneint ist.

    Fortsetzungsauftrag (Abschlusspruefung, konkreter Gegenfall): eine reine
    Zeichen-Reichweite (±60/70 Zeichen) ohne Teilsatzgrenze liess eine
    Bezeichnung, die eine Quelle AUSDRUECKLICH einem ANDEREN Code zuordnet,
    trotzdem an den naheliegenden Zielcode binden -- "Die interne Bezeichnung
    der Baureihe ist 991, nicht GT3; der 911 GT3 ist nur die Topausstattung."
    bestaetigte bisher faelschlich generation="GT3", weil "Baureihe" nur
    ±60 Zeichen vor der "911 GT3"-Nennung lag, OBWOHL ein Satzzeichen (";")
    dazwischen einen eigenen, abgeschlossenen Teilsatz beendet. Jetzt bindet
    eine Bezeichnung NUR noch an einen Code im SELBEN Teilsatz -- dieselbe,
    bereits bestehende und getestete Teilsatzgrenze wie bei jeder anderen
    Verneinungspruefung in dieser Datei, keine neue Heuristik. Zusaetzlich
    gilt eine im eigenen Teilsatz VERNEINTE Bezeichnung ("kein eigenstaendiger
    Chassiscode") nicht als Beleg (`_verneint_im_teilsatz`/
    `_verneint_nach_position`, ebenfalls bestehend).

    Eigene Teilsatzgrenze (`_TEILSATZGRENZE_ROLLENBELEG`), NICHT die geteilte
    `_TEILSATZGRENZE`: eine ordinale Zaehlung wie "4. Generation" enthaelt
    selbst einen Punkt ("4.") -- die geteilte Grenze haette genau DIESEN
    Punkt faelschlich als Satzende gelesen und "4." vom nachfolgenden
    "Generation" abgeschnitten, wodurch `_RE_GEN_NUMMER` im verbleibenden
    Teilsatz nie mehr traf (reproduziert: "Die 4. Generation des Mazda MX-5
    (ND, seit 2015): ..." verlor dadurch ihren eigenen Rollenbeleg). Ein
    Punkt direkt NACH einer Ziffer zaehlt deshalb hier nicht als Grenze."""
    vor_grenzen = [m.end() for m in _TEILSATZGRENZE_ROLLENBELEG.finditer(text[:start])]
    teilsatz_start = vor_grenzen[-1] if vor_grenzen else 0
    nach_grenze = _TEILSATZGRENZE_ROLLENBELEG.search(text[ende:])
    teilsatz_ende = ende + (nach_grenze.start() if nach_grenze else len(text) - ende)
    teilsatz = text[teilsatz_start:teilsatz_ende]
    for rx in (_ROLLE_BEZEICHNUNG, _RE_GEN_NUMMER):
        for m in rx.finditer(teilsatz):
            if (m.group().strip().lower() == "baureihe"
                    and _BAUREIHE_FREMDBEZUG.search(teilsatz[:m.start()])):
                continue   # Genitivobjekt eines fremden Substantivs, s.o. -- kein Beleg
            if not (_verneint_im_teilsatz(teilsatz, m.start())
                    or _verneint_nach_position(teilsatz, m.start())):
                return True
    return False


def _identitaets_claims(r: dict, ziel: dict) -> list[dict]:
    """Alle Identitäts-Claims EINES ausgerichteten Treffers."""
    url = r.get("url") or ""
    dom, tier = _domain_von(url), _tier_identitaet(url, ziel.get("marke"))
    if _ist_regionsfremd(url):
        # Identity-Hardening, Abschnitt 5: die IdentitätsPHASE darf internationale
        # Quellen für Marke/Modell/Generation-EXISTENZ nutzen (siehe `recherchiere()`,
        # wo `US_QUELLEN_AUSSCHLUSS` NUR noch für Phase 2/3 gilt) — aber eine solche
        # Quelle darf hier, wo Claims für DETAIL-Felder (Generation/PS/Hubraum/
        # Kraftstoff/Antrieb/Getriebe/Motorcode) entstehen, NIE als "stark" (Tier<=2)
        # zählen: sonst würde z.B. eine US-Herstellerseite allein schon genügen, um
        # eine deutsche Detail-Spezifikation zu bestätigen (`konsens()`/`_GEWICHT`
        # lassen bereits EINE Tier-1/2-Quelle für ein Feld genügen). Die Deckelung
        # wirkt NUR hier (Claim-Gewichtung), NICHT auf `_identitaet_belegt()` — die
        # Existenzfrage selbst bleibt für internationale Quellen offen.
        tier = max(tier, 3)
    _, grund = ausgerichtet(r, ziel.get("marke"), ziel.get("modell"))
    if grund == "schwach":
        # Nur Sätze, die das Modell nennen; niedrigste Stufe.
        tier = 3
        text = " ".join(s for s in _saetze(f"{r.get('title') or ''}. {r.get('content') or ''}")
                        if _satz_nennt_modell(s, ziel.get("modell")))
    else:
        # Claim-Level Vehicle Binding (Forensik-Folgeauftrag, Package A,
        # CONFIRMED Root Cause): ein Treffer, dessen TITEL das Zielmodell
        # nennt ("stark" ausgerichtet), ließ hier bisher den GESAMTEN
        # Dokumenttext (Titel + Content) in jede folgende Claim-Regex
        # (PS/Hubraum/Kraftstoff/Antrieb/Antriebsart/Getriebe/Motorcode)
        # einfließen. Dokument-Alignment beantwortet nur "handelt diese
        # Seite vom Zielmodell" — es bindet NICHT jeden einzelnen Satz an
        # das Zielmodell. Eine Seite über "Lexus RC F" kann im selben
        # Fließtext eine Sibling-Variante derselben Baureihe (z.B. den
        # Hybrid RC 300h) beschreiben; ein dortiger "Hybrid"-Claim wurde
        # bisher unverändert dem Zielmodell zugerechnet (Lexus-RC-F-
        # Forensik, Abnahmepunkt 1/2). Betrifft generisch JEDES Modell mit
        # einer namensnahen Sibling-Variante (ICE/Hybrid-Geschwister wie
        # 330i/330e, Golf/Golf GTE, nicht nur Lexus).
        #
        # Fix: dieselbe, bereits produktiv genutzte und getestete Segment-/
        # Widerspruchs-Bindung wie in `_extrahiere_fakten()`
        # (rueckruf/schwachstelle/wartung) wiederverwenden, statt eine
        # zweite, parallele Architektur zu bauen. `titel_stark=True` erlaubt
        # weiterhin Kontext-Vererbung innerhalb eines unwidersprochenen
        # Segments (ein starker Titel darf einem neutralen Folgesatz ohne
        # erneute Modellnennung Kontext leihen) — ein Satz, der EXPLIZIT ein
        # anderes Fahrzeug/eine andere Variante nennt, beendet das Segment
        # sofort (`_claim_widerspricht_fahrzeug_lokal`) und wird NICHT mehr
        # als Zielmodell-Claim gewertet, unabhängig vom starken Titel.
        #
        # Der Zwei-Domain-/Primärquellen-Pfad für die reine Marke+Modell-
        # EXISTENZ (`_identitaet_belegt()`) bleibt davon unberührt — diese
        # Funktion liefert ausschließlich die Detail-Claims (PS/Hubraum/
        # Kraftstoff/Antrieb/Antriebsart/Getriebe/Motorcode/Generation), nie
        # die Existenzfrage selbst.
        satz_mit_grenze = _saetze_mit_segmentgrenze(f"{r.get('title') or ''}. {r.get('content') or ''}")
        lokale_saetze = _segment_kontext_saetze(
            [s for s, _ in satz_mit_grenze], marke=ziel.get("marke"), modell=ziel.get("modell"),
            titel_stark=True, segment_marker=[g for _, g in satz_mit_grenze],
            url=url, kategorie="identitaet")
        text = " ".join(lokale_saetze)
    baujahr = ziel.get("baujahr")
    claims: list[dict] = []

    def add(feld, wert, **extra):
        claims.append({"feld": feld, "wert": wert, "domain": dom, "tier": tier, "url": url, **extra})

    # Eine Seite, deren TITEL die Leistung des Zielfahrzeugs nennt, handelt von
    # genau dieser Motorisierung: ihr Generationscode wiegt eine Stufe mehr.
    ps = ziel.get("leistung_ps")
    varianten_bonus = 1 if ps and re.search(rf"\b{int(ps)}\s*(?:PS|hp)\b", r.get("title") or "",
                                            re.IGNORECASE) else 0
    for g in _generationscodes(r, ziel.get("modell")) if grund != "schwach" else []:
        passt = _im_zeitraum(baujahr, g["zeitraum"])
        if passt is False:
            add("generation", g["code"], verworfen="baujahr_ausserhalb", zeitraum=g["zeitraum"])
            continue
        add("generation", g["code"], zeitraum=g["zeitraum"], jahr_belegt=passt is True,
            kontext=g["kontext"], generationsnummer_strukturell=bool(g["nummer"]),
            rollenbeleg=g["rollenbeleg"], bonus=varianten_bonus)
        if g["nummer"]:
            add("generation_nummer", f"{g['nummer']}. Generation", zeitraum=g["zeitraum"],
                jahr_belegt=passt is True)
    # Familien-Zugehoerigkeitsbeleg (s. Moduldocstring-Block bei
    # `_modellfamilie_praefixe()`/`_modellfamilie_zugehoerigkeit_belegt()`):
    # zusaetzlich zum exakten Core-Model-Anker oben auch auf einem Modell-
    # PRAEFIX verankerte Codes zulassen -- NUR ziffertragend und NUR, wenn
    # `_modellfamilie_zugehoerigkeit_belegt()` die Zuordnung eigenstaendig
    # bestaetigt (nicht nur raeumliche Naehe). Laeuft BEWUSST auch fuer
    # `grund == "schwach"` (anders als der exakte Anker oben): reale
    # Baureihen-Felder (carwiki.de/auto-motor-und-sport.de) stehen oft auf
    # Seiten, deren TITEL nur die Modellfamilie ("Lexus RC"), nicht das
    # konkrete Zielmodell ("RC F") nennt -- die eigene, strengere
    # Zugehoerigkeitspruefung (Absatzgrenze/Fremdmarke/Verneinung) ersetzt
    # hier die Titel-Ausrichtung als Schutz, statt sie zu lockern.
    # `voller_text` ist bewusst der UNGEFILTERTE Dokumenttext (wie `r` fuer
    # die exakte Verankerung oben, nicht die Claim-Level-Vehicle-Binding-
    # `text`-Variable) -- dieselbe Quelle, auf der auch `_generationscodes()`
    # selbst arbeitet.
    voller_text = f"{r.get('title') or ''}. {r.get('content') or ''}"
    exakte_codes = {g["code"] for g in _generationscodes(r, ziel.get("modell"))}
    for praefix in _modellfamilie_praefixe(ziel.get("modell")):
        for g in _generationscodes(r, praefix):
            if g["code"] in exakte_codes or not re.search(r"\d", g["code"]):
                continue
            if not _modellfamilie_zugehoerigkeit_belegt(
                    voller_text, ziel.get("marke"), ziel.get("modell"), g["start"], g["ende"]):
                continue
            exakte_codes.add(g["code"])
            passt = _im_zeitraum(baujahr, g["zeitraum"])
            if passt is False:
                add("generation", g["code"], verworfen="baujahr_ausserhalb", zeitraum=g["zeitraum"])
                continue
            add("generation", g["code"], zeitraum=g["zeitraum"], jahr_belegt=passt is True,
                kontext=g["kontext"], generationsnummer_strukturell=bool(g["nummer"]),
                rollenbeleg=g["rollenbeleg"], bonus=varianten_bonus)
            if g["nummer"]:
                add("generation_nummer", f"{g['nummer']}. Generation", zeitraum=g["zeitraum"],
                    jahr_belegt=passt is True)
    for m in _RE_GEN_NUMMER.finditer(text):
        nr = m.group(1) or _ORDINAL.get(_norm(m.group(2)).replace(" ", ""))
        if nr:
            umgebung = text[max(0, m.start() - 60): m.end() + 60]
            z = zeitraum(umgebung)
            passt = _im_zeitraum(baujahr, z)
            if passt is not False:
                add("generation_nummer", f"{int(nr)}. Generation", zeitraum=z, jahr_belegt=passt is True)
    for m in _RE_PS.finditer(text):
        add("horsepower", int(m.group(1)))
    for m in _RE_HUBRAUM.finditer(text):
        add("displacement", m.group(1).replace(",", "."))
    for m in _RE_HUBRAUM_CCM.finditer(text):
        add("displacement", _ccm_als_liter(m.group(1)))
    for wert, rx in _KRAFTSTOFF_CLAIM:
        if any(not _verneint_im_teilsatz(text, m.start()) for m in rx.finditer(text)):
            add("fuel", wert)
    for wert, rx in _ANTRIEB_CLAIM:
        if any(not _verneint_im_teilsatz(text, m.start()) for m in rx.finditer(text)):
            add("drivetrain", wert)
    # Root Cause 5 (Stufe 2): Elektrifizierungsgrad NUR aus denselben
    # ausdrücklichen Wörtern, die app/kraftstoff_powertrain.py bereits für
    # Nutzer-/Inseratstext verlangt ("reiner Verbrenner"/"ICE", "Mild-Hybrid",
    # "Plug-in-Hybrid", "BEV"/"elektrisch") — wiederverwendet, nicht neu
    # erfunden. "Benzin"/"Diesel" allein lösen hier bewusst NICHTS aus (siehe
    # `powertrain_aus_freitext`-Docstring): der Kraftstoff-Claim oben ist eine
    # ANDERE Achse und impliziert nie die Elektrifizierung.
    pt = powertrain_aus_freitext(text)
    if pt:
        add("powertrain", pt)
    for m in _RE_GETRIEBE_MANUELL.finditer(text):
        if _verneint_im_teilsatz(text, m.start()):
            continue
        gaenge = m.group(1) or _ZAHLWORT.get(_norm(m.group(2) or "")) or m.group(3)
        add("transmission", "manuell", detail=f"{gaenge}-Gang" if gaenge else None)
    _automatik_stufen_spans: list[tuple[int, int]] = []
    for m in _RE_GETRIEBE_AUTO.finditer(text):
        _automatik_stufen_spans.append(m.span())
        if _verneint_im_teilsatz(text, m.start()):
            continue
        stufen = (m.group(1) or _ZAHLWORT.get(_norm(m.group(2) or "")) or m.group(3) or m.group(4))
        add("transmission", "automatik", detail=f"{stufen}-Stufen" if stufen else None)
    for m in _RE_AUTOMATIK_ALLEIN.finditer(text):
        # Bereits über die staerkere Stufenangabe erfasst (z.B. "Achtstufen-
        # Automatik") -- nicht als zweiten, inhaltsgleichen Claim derselben
        # Textstelle zaehlen.
        if any(a <= m.start() < b for a, b in _automatik_stufen_spans):
            continue
        if _verneint_im_teilsatz(text, m.start()):
            continue
        vor = text[max(0, m.start() - 30):m.start()]
        if _AUTOMATIK_FREMDKONTEXT.search(vor):
            continue
        add("transmission", "automatik")
    for m in _RE_MOTORCODE_CLAIM.finditer(text):
        if not _ist_motorcode_wert(m.group(1), text[m.start(1):]):
            continue
        if _verneint_im_teilsatz(text, m.start()):
            continue
        add("engine_code", m.group(1).upper())
    return claims


def konsens(claims: list[dict], feld: str, *, bevorzugt=None) -> tuple[object, dict] | None:
    """Gewichteter Konsens EINES Felds (siehe Modulkopf).

    `bevorzugt` (optional): der Nutzerwert. Werte, die ihn bestätigen, werden
    nicht bevorzugt GEWICHTET — aber bei Mehrfachnennungen einer Seite (z.B.
    eine Übersicht mit allen Leistungsstufen) zählt eine Domain für einen Wert
    nur einmal, und ein Nutzerwert, den eine Quelle nennt, ist kein Konflikt
    zu einer anderen Leistungsstufe derselben Seite.
    """
    je_wert: dict[str, dict] = {}
    for c in claims:
        if c["feld"] != feld or c.get("verworfen"):
            continue
        key = str(c["wert"]).lower()
        e = je_wert.setdefault(key, {"wert": c["wert"], "domains": {}, "bonus": {}, "details": []})
        alt = e["domains"].get(c["domain"])
        if alt is None or c["tier"] < alt:
            e["domains"][c["domain"]] = c["tier"]
        # Je Domain höchstens EIN Bonus (z.B. Seite über genau diese Motorisierung).
        e["bonus"][c["domain"]] = max(e["bonus"].get(c["domain"], 0), int(c.get("bonus") or 0))
        if c.get("detail"):
            e["details"].append(c["detail"])
    if not je_wert:
        return None
    bewertet = []
    for key, e in je_wert.items():
        gewicht = sum(_GEWICHT[t] + e["bonus"].get(d, 0) for d, t in e["domains"].items())
        stark = any(t <= 2 for t in e["domains"].values())
        bewertet.append((gewicht, stark, key, e))
    bewertet.sort(key=lambda x: -x[0])
    if bevorzugt is not None:
        # Mehrwertige Felder (Leistung/Hubraum): eine Quelle nennt oft ALLE
        # Leistungsstufen. Bestätigt wird hier nur, ob der Nutzerwert selbst
        # belegt ist — nicht, welcher Wert "gewinnt".
        for gewicht, stark, key, e in bewertet:
            if key == str(bevorzugt).lower() and stark and gewicht >= MIN_KONSENS_GEWICHT:
                return e["wert"], {"domains": len(e["domains"]), "gewicht": gewicht,
                                   "detail": _haeufigstes(e["details"]),
                                   "domain_namen": set(e["domains"])}
        return None
    gewicht, stark, key, e = bewertet[0]
    if not stark or gewicht < MIN_KONSENS_GEWICHT:
        return None
    for g2, stark2, _k2, _e2 in bewertet[1:]:
        if stark2 and g2 * 2 >= gewicht:
            return None       # Konflikt vergleichbar starker Quellen -> UNKNOWN
    return e["wert"], {"domains": len(e["domains"]), "gewicht": gewicht,
                       "detail": _haeufigstes(e["details"]), "domain_namen": set(e["domains"])}


def _haeufigstes(werte: list[str]) -> str | None:
    if not werte:
        return None
    return max(set(werte), key=werte.count)


def _conf(info: dict) -> str:
    return "hoch" if info["gewicht"] >= 6 else "mittel" if info["gewicht"] >= 4 else "niedrig"


def _generationen_pruefen(claims: list[dict], bekannter_code: str | None = None) -> None:
    """Verwirft (in place) Generations-Claims ohne Beleg für DIESES Fahrzeug.

      * Ein Code, dessen Jahresfenster in irgendeiner Quelle das Baujahr
        ausschließt und in keiner Quelle einschließt, ist überall ausgeschlossen
        (eine Vergleichsseite "NA/ND" nennt beide, eine andere grenzt NA ein).
        Jahresfenster bleiben damit ein Widerspruchs-, aber kein
        Bestätigungsbeleg (s. Runde 3 unten).
      * Ein Code ohne strukturelle Generationszählung UND ohne eindeutige
        Bezeichnung (`rollenbeleg`, s.u.) zählt nicht: er ist dann oft eine
        Ausstattungs-, Karosserie- oder Leistungsbezeichnung ("… G 184",
        "… RF", "… RS") -- ein bloßes Jahresfenster allein beweist das
        Gegenteil NICHT mehr (Runde 3). Vorher entstanden daraus Konflikte,
        die jede Generation auf "unbekannt" setzten.

    `bekannter_code` (Package-A-Hardening, RC-W6-B4-Nachfolge): ein aus dem
    `modell`-Feld rein FORMBASIERT abgetrennter Kandidat (`parse_core_model`/
    `_ziel`, dort als `auxiliary_code` geführt). Er ist ABSICHTLICH KEIN
    Freifahrschein mehr: ein bloßer String-Treffer gegen `bekannter_code`
    befreit eine Quelle NICHT von der Jahr-/Struktur-Pflicht — sonst würde ein
    reiner Trimname/eine reine Variantenbezeichnung, die der Nutzer (ohne es
    zu wissen) ins Modellfeld geschrieben hat ("911 GT3", "Golf R32"), bei
    JEDER Quelle, die das Fahrzeug nur beim Namen nennt ("911 GT3 im Test"),
    sofort als "bestätigte Generation" durchgehen — obwohl GT3/R32 echte
    Leistungsvarianten sind, keine Chassis-/Baureihengenerationen. Der
    Parameter bleibt nur zur Kennzeichnung erhalten (`passend_zum_kandidat`),
    OHNE Wirkung auf das Verwerfen: ein Code braucht weiterhin ENTWEDER eine
    strukturelle Generationszählung ODER eine eindeutige Bezeichnung
    (s.u., Runde 3) — exakt dieselbe Schwelle wie jeder andere Code,
    unabhängig davon, ob er zufällig mit der Nutzereingabe übereinstimmt UND
    unabhängig davon, ob er eine Ziffer enthält. Ein ANDERER, abweichender
    Code bleibt unverändert an dieselbe Schwelle gebunden — ein expliziter Widerspruch
    (zwei vergleichbar stark belegte, aber unterschiedliche Codes) wird
    weiterhin von `konsens()` als Konflikt erkannt, nie blind übernommen.

    Fortsetzungsauftrag (falsche Generationsbestätigung, konkret abgeschlossen
    -- Runde 2, gemeinsamer Pfad für alphabetische UND numerische Codes):
    reproduziert UNABHÄNGIG von `bekannter_code`/der Modellfeld-Abtrennung
    (empirisch geprüft: tritt identisch auf, wenn der Nutzer den Zusatz gar
    nicht erst eingibt) UND unabhängig davon, ob der Code eine Ziffer enthält
    ("RS"/"ST"/"FR" ebenso wie der bereits vorbestehende "911 GT3"-Fall) —
    `kontext` allein (ein Generationswort IRGENDWO im ±60/30-Zeichen-Fenster,
    z.B. "…, diese Generation, …") bestätigte bisher JEDEN gefundenen Code,
    ohne dass dieses Wort inhaltlich überhaupt zum Code gehören muss. Eine
    erste Fassung dieses Fixes unterschied nach Ziffer-Vorhandensein — das war
    FALSCH: eine Ziffer im Code ist kein semantischer Nachweis einer
    Generation (ein Trimname kann ebenso eine Ziffer tragen wie ein echter
    Chassiscode kein; s. Abschlussbericht). `kontext` (die bloße Wortsuche)
    ist deshalb jetzt für KEINEN Code mehr allein ausreichend — unabhängig
    von Ziffern, Marke oder Modell.

    Runde 2 liess als Evidenz `jahr_belegt` (passendes Produktionsfenster)
    ODER `generationsnummer_strukturell` (STRUKTURELLE "Modell IV (CODE)"-
    Schreibweise) gelten, jeweils ALLEIN hinreichend.

    Fortsetzungsauftrag (verbleibende semantische Generationslücke, Runde 3,
    CONFIRMED): genau das war noch zu weit. Ein Produktionsfenster belegt nur
    zeitliche Passung ("der Code existierte/wurde gebaut, als das Zielfahrzeug
    gebaut wurde") -- NICHT, dass der Code semantisch eine Generation statt
    einer Leistungs-/Ausstattungsvariante bezeichnet. Eine Variante wird so gut
    wie immer INNERHALB des Produktionsfensters ihrer eigenen Chassisgeneration
    gebaut; ein Jahresfenster um "GT3" herum ist deshalb kein Beleg dafür, dass
    "GT3" selbst die Generation ist (reproduziert: "Der Porsche 911 GT3, diese
    Generation, wird seit 2021 gebaut." bestätigte bisher fälschlich
    generation="GT3" -- exakt derselbe, bereits in Runde 2 als bloße
    Wortnachbarschaft erkannte Fehler, nur diesmal durch ein zusätzliches,
    aber ebenso unspezifisches Jahresfenster verschleiert). `jahr_belegt`
    entfällt deshalb jetzt als EIGENSTÄNDIGER Bestätigungspfad; es bleibt
    Beleg für zeitliche WIDERSPRÜCHE (ein Code, den eine Quelle fürs Baujahr
    ausschließt, bleibt ausgeschlossen -- unverändert, s. `aus`/`ein` unten),
    ist aber für sich genommen keine Rollenbestätigung mehr.

    Verbleiben zwei GENERISCHE, tatsächlich eindeutige Evidenzpfade für die
    ROLLE selbst, unabhängig von jedem Jahresfenster:
      * `generationsnummer_strukturell` (`g["nummer"]`, `_generationscodes()`)
                          die STRUKTURELLE "Modell IV (CODE)"-Schreibweise —
                          eine römische Zahl UNMITTELBAR vor dem Code in
                          Klammern ist eine explizite Generationszählung,
                          kein bloß benachbartes Wort.
      * `rollenbeleg`     (`g["rollenbeleg"]`, `_generationscodes()`) eine
                          EINDEUTIGE Bezeichnung ("Baureihe …", "Chassis-Code
                          …"/"Chassiscode …", "Werkscode …", "Codename …",
                          "interne Bezeichnung …") in der Umgebung des Codes
                          -- eine explizite Rollenzuweisung durch die Quelle,
                          kein vages Wort wie "Generation"/"Typ"/"Modellreihe"
                          (`_ROLLE_BEZEICHNUNG` ist eine STRENGE Teilmenge von
                          `_GEN_KONTEXT`, genau ohne diese vagen Wörter).
    Beide Pfade zählen auch OHNE Produktionsjahresfenster (eine eindeutige
    Bezeichnung braucht kein Jahr, um die Rolle zu belegen) UND für
    ziffernlose wie zifferntragende Codes gleich -- kein Unterschied nach
    Marke, Modell oder Ziffern. Reine Wortnachbarschaft (`kontext`) UND ein
    bloßes Jahresfenster (`jahr_belegt`) reichen -- einzeln wie zusammen --
    für KEINEN Code mehr aus, auch nicht für digit-tragende Codes wie
    "F82"/"GT3".

    Betrifft NUR das Verwerfen/Bestätigen der Generation selbst:
    `auxiliary_code`/Modell-/Variantendaten bleiben vollständig erhalten
    (reichern Queries weiter an), ein so verworfener Code wird UNKNOWN
    (`"ohne_generationskontext"`), nicht gelöscht. Explizite Widersprüche
    (zwei vergleichbar starke, aber unterschiedliche belegte Codes) bleiben
    unverändert Sache von `konsens()` -- diese Funktion verwirft nur
    UNBELEGTE Claims, nie einen Konflikt zwischen zwei belegten."""
    gen = [c for c in claims if c["feld"] == "generation"]
    aus = {c["wert"] for c in gen if c.get("verworfen") == "baujahr_ausserhalb"}
    ein = {c["wert"] for c in gen if c.get("jahr_belegt")}
    bekannt_norm = (bekannter_code or "").strip().upper()
    for c in gen:
        if c.get("verworfen"):
            continue
        if bekannt_norm and str(c["wert"]).upper() == bekannt_norm:
            c["passend_zum_kandidat"] = True
        if c["wert"] in aus - ein:
            c["verworfen"] = "baujahr_ausserhalb_andere_quelle"
        elif not (c.get("generationsnummer_strukturell") or c.get("rollenbeleg")):
            c["verworfen"] = "ohne_generationskontext"


def werte_identitaet_aus(treffer: list[dict], ziel: dict) -> tuple[WebVehicleIdentity, list[dict]]:
    """Phase-1-Auswertung: belegte Identität + per Konsens akzeptierte Felder."""
    marke, modell = ziel.get("marke"), ziel.get("modell")
    belegt, stuetzend, domains, pfad = _identitaet_belegt(modell, marke, treffer)
    abgelehnt: list[dict] = []
    for r in treffer:
        ok, grund = ausgerichtet(r, marke, modell)
        if not ok:
            abgelehnt.append({"url": r.get("url"), "grund": grund})
    if not belegt:
        return WebVehicleIdentity(belegt=False, marke=marke, modell=modell,
                                  belegende_domains=domains, abgelehnte_claims=abgelehnt), abgelehnt

    # Claims kommen aus JEDER ausgerichteten, nicht gesperrten Quelle: unbekannte
    # Domains zählen mit Gewicht 1 (Stufe 3). Das ändert nichts an der Schwelle —
    # ein Wert braucht weiterhin eine Quelle der Stufe 1/2 und Gewicht >= 3, und
    # nur Stufe-1/2-Quellen können einen Konflikt auslösen —, aber unabhängige
    # Spezifikationsseiten bestätigen jetzt einen Nutzerwert, statt ungelesen zu
    # bleiben (realer Web-Smoke-Test: sieben Seiten nannten die Leistung, gezählt
    # wurde keine).
    claim_quellen = [r for r in treffer if score_domain(r.get("url") or "") >= 0
                     and ausgerichtet(r, marke, modell)[0]]
    claims = [c for r in claim_quellen for c in _identitaets_claims(r, ziel)]
    _generationen_pruefen(claims, ziel.get("auxiliary_code"))
    abgelehnt += [{"url": c["url"], "feld": c["feld"], "wert": c["wert"], "grund": c["verworfen"]}
                  for c in claims if c.get("verworfen")]
    feldwerte: dict[str, dict] = {}
    akzeptiert: list[dict] = []

    def uebernehme(feld, ergebnis, *, zielfeld=None, wert=None):
        if ergebnis is None:
            return None
        w, info = ergebnis
        w = wert if wert is not None else w
        # §F PASS-GATE-Folgeauftrag: registrierbare Domainnamen (NIE volle
        # URLs) je bestätigtem Feld mitführen -- bisher trug `feldwerte` nur
        # die ANZAHL ("2 Webquelle(n)"), nie WELCHE Domains. Reine
        # Durchreichung von `konsens()`s bereits berechnetem `domain_namen`
        # (s. dort) -- keine neue Datenerhebung.
        feldwerte[zielfeld or feld] = {"value": w, "confidence": _conf(info),
                                       "domains": info["domains"], "detail": info.get("detail"),
                                       "domain_namen": info.get("domain_namen") or set()}
        akzeptiert.append({"feld": zielfeld or feld, "wert": w, "domains": info["domains"],
                           "gewicht": info["gewicht"]})
        return w

    # Generation: nur ein Code, der zum Baujahr passt (jahr_belegt) oder
    # unwidersprochen per Konsens gestützt ist — und nur Codes mit Jahresfenster
    # oder Generationskontext (`_generationen_pruefen`).
    gen_claims = [c for c in claims if c["feld"] == "generation" and not c.get("verworfen")]
    mit_jahr = [c for c in gen_claims if c.get("jahr_belegt")]
    generation = uebernehme("generation", konsens(mit_jahr, "generation")
                            or konsens(gen_claims, "generation"))
    gen_nr = konsens([c for c in claims if c["feld"] == "generation_nummer"], "generation_nummer")
    if gen_nr and generation:
        feldwerte["generation"]["detail"] = gen_nr[0]
    elif gen_nr and not generation:
        uebernehme("generation_nummer", gen_nr, zielfeld="generation")

    ps = ziel.get("leistung_ps")
    leistung = uebernehme("horsepower", konsens(claims, "horsepower", bevorzugt=ps)) if ps else None
    hub = ziel.get("hubraum")
    if hub:
        uebernehme("displacement", konsens(claims, "displacement", bevorzugt=hub))
    kraftstoff = uebernehme("fuel", konsens(claims, "fuel"))
    antrieb = uebernehme("drivetrain", konsens(claims, "drivetrain"))
    # Root Cause 5 (Stufe 2): wie jedes andere Feld per gewichtetem Konsens —
    # zwei vergleichbar starke, widersprechende Quellen ergeben `None` (siehe
    # `konsens()`), genau wie bei `fuel`/`generation`. Kein Sonderfall.
    powertrain = uebernehme("powertrain", konsens(claims, "powertrain"))
    getriebe = uebernehme("transmission", konsens(claims, "transmission"))
    motorcode = uebernehme("engine_code", konsens(claims, "engine_code"))
    if ziel.get("baujahr") and mit_jahr and generation:
        feldwerte["year"] = {"value": ziel["baujahr"], "confidence": feldwerte["generation"]["confidence"],
                             "domains": feldwerte["generation"]["domains"]}
    # Motorbezeichnung: nur BESTÄTIGT (nie aus der Nutzereingabe kopiert), wenn
    # die markanten Tokens der Nutzerangabe in >= 2 starken Quellen stehen.
    motor = ziel.get("motor")
    motor_bestaetigt = None
    if motor and _tokens(motor):
        # Kern der Motorangabe: Bezeichnungen mit Buchstaben UND Ziffern ("316d",
        # "QX12"); sonst markante Wörter (>= 4 Buchstaben, z.B. "skyactiv").
        toks = _tokens(motor)
        gemischt = {x for x in toks if re.search(r"\d", x) and re.search(r"[a-z]", x)}
        woerter = {x for x in toks if x.isalpha() and len(x) >= 4
                   and x not in ("turbo", "benzin", "diesel", "motor", "biturbo")}
        kern = gemischt or woerter
        # Hubraumangabe (§C Folgeauftrag, PASS-GATE-Fund): eine Hubraumangabe
        # muss zusätzlich im selben Text stehen -- bisher über zwei EINZELNE,
        # im Quelltext als SEPARATE Tokens erwartete Ziffern ("5","0" aus
        # "5.0"). Schreibweisen ohne Trennzeichen vor der Einheit ("5.0L",
        # "5.0-Liter") verschmelzen die zweite Ziffer mit der Einheit zu
        # EINEM Token ("0l") -- der Tokenvergleich scheiterte dann, OBWOHL
        # die Quelle den Hubraum korrekt nennt (Produktions-Befund: "5.0-liter
        # V8" wurde dadurch nicht erkannt). Jetzt: dieselbe, bereits an
        # anderer Stelle produktiv genutzte `_RE_HUBRAUM`-Erkennung
        # (Claim-Extraktion weiter oben) -- Wertvergleich statt Tokenvergleich,
        # robust gegen Schreibvarianten. Kein Rückschritt: ohne Hubraumangabe
        # im Nutzerwert bleibt das Verhalten exakt wie bisher (kein
        # Zusatzkriterium).
        hub = re.search(r"\b(\d)[.,](\d)\b", motor)
        hub_norm = f"{hub.group(1)}.{hub.group(2)}" if hub else None

        def _hat_hubraum_beleg(text: str) -> bool:
            if not hub_norm:
                return True
            return any(m.group(1).replace(",", ".") == hub_norm for m in _RE_HUBRAUM.finditer(text or ""))

        def _motor_quelle_passt(r: dict) -> bool:
            text = f"{r.get('title') or ''} {r.get('content') or ''}"
            return kern <= _tokens(text) and _hat_hubraum_beleg(text)

        doms = {_domain_von(r.get("url") or "") for r in claim_quellen
                if _motor_quelle_passt(r) and _tier_identitaet(r.get("url") or "", marke) <= 2}
        if (kern or hub_norm) and (len(doms) >= 2 or any(
                _tier_identitaet(r.get("url") or "", marke) == 1
                for r in claim_quellen if _motor_quelle_passt(r))):
            motor_bestaetigt = motor
            feldwerte["engine_name"] = {"value": motor, "confidence": "mittel",
                                        "domains": len(doms) or 1, "domain_namen": doms}

    bester = max((score_domain(r.get("url") or "") for r in stuetzend), default=0)
    conf = _confidence_aus_domains(domains, bester)
    kern_felder = sum(1 for f in ("generation", "horsepower", "engine_name") if f in feldwerte)
    identitaet_konf = ("hoch" if conf == "hoch" and kern_felder >= 2 else
                       "mittel" if "generation" in feldwerte and kern_felder >= 2 else "niedrig")
    getriebe_detail = (feldwerte.get("transmission") or {}).get("detail")
    return WebVehicleIdentity(
        belegt=True, marke=marke, modell=modell,
        generation=generation if isinstance(generation, str) else (feldwerte.get("generation") or {}).get("value"),
        generation_nummer=(gen_nr[0] if gen_nr else None),
        motor=motor_bestaetigt, kraftstoff=kraftstoff, leistung_ps=leistung,
        antrieb=antrieb, powertrain=powertrain,
        getriebe=getriebe, getriebe_detail=getriebe_detail, motorcode=motorcode,
        confidence=conf, belegende_domains=domains, quellen=_quellen_aus(stuetzend),
        feldwerte=feldwerte, identitaet_konfidenz=identitaet_konf,
        akzeptierte_claims=akzeptiert, abgelehnte_claims=abgelehnt,
        identitaets_pfad=pfad,
    ), abgelehnt


# ── Phase 2/3: Fakten mit Geltungsbereich ────────────────────────────────────

_PROBLEM_WORTE = ("defekt", "problem", "schwachstelle", "verschleiss", "verschleiß",
                  "ausfall", "undicht", "riss", "bruch", "schaden", "haeufig",
                  "häufig", "anfaellig", "anfällig", "typisch", "bekannt")
_RUECKRUF_WORTE = ("rueckruf", "rückruf", "recall", "rueckrufaktion", "rückrufaktion")

# Package C / RC-W3 (Root Cause: reiner Substring-Treffer gegen `_PROBLEM_WORTE`
# ohne jede Prüfung der SATZRICHTUNG). Ein Satz, der ein Problemwort enthält,
# aber seine eigene Aussage relativiert oder verneint ("... muss NICHT auf
# einen Unfall hindeuten", "... ist unproblematisch", "... kann bereits so vom
# Band gekommen sein"), ist kein positiver Defekt-Claim — er sagt genau das
# Gegenteil. Generisch (kein Marken-/Modell-/Bauteilwort): jede dieser
# Wendungen, unabhängig vom Satzinhalt sonst.
_RELATIVIERUNG = re.compile(
    r"muss(?:te)?\s+nicht\b"
    r"|nicht\s+(?:unbedingt|zwingend|notwendigerweise)\b"
    r"|kein\w*\s+(?:bekannt\w*\s+)?(?:problem|hinweis|grund)\b"
    r"|kein\s+grund\s+zur\s+sorge\b"
    r"|deutet\s+nicht\s+(?:unbedingt\s+)?auf\b"
    r"|unproblematisch\w*"
    r"|unbedenklich\w*"
    r"|unkritisch\w*"
    r"|harmlos\w*"
    r"|konstruktionsbedingt\b"
    r"|werksseitig\s+so\b"
    r"|(?:ab|von)\s+werk\s+(?:so|bereits)\b"
    r"|bereits\s+so\s+vom\s+band\b",
    re.IGNORECASE)


# RC-W3 (struktureller Coverage-Fix): ein kleiner, geschlossener Satz
# zusätzlicher Defekt-/Verschleiss-Signale für Fälle OHNE jedes Wort aus
# `_PROBLEM_WORTE` — bewusst klein (keine Ontologie, keine Symptome, keine
# vergleichenden Leistungs-/Verbrauchsaussagen, kein freies Kausalverb):
#   * Material/Zustand: Korrosion/Rost sind, anders als z.B. "Rasseln" oder
#     "Problem", in der Praxis NIE eine neutrale Beschreibung — ihr einziges
#     Risiko ist die VERNEINUNG ("kein Rost"), die unten generisch abgefangen
#     wird, nicht eine andere, harmlose Bedeutung des Wortes selbst.
#   * Verschleiss-Veränderung ("wird länger/porös/undicht/locker"): nur mit
#     EINEM der fest gelisteten Verschleiss-Adjektive, nicht mit beliebigen
#     Vergleichsformen ("geringer", "höher" bleiben bewusst aussen vor).
#   * "Spiel" ist allein zu mehrdeutig (Zweideutigkeit mit "Spiel"=Spielen) —
#     nur zusammen mit einem Erwerbs-Verb ("bekommt/hat/zeigt/entwickelt
#     Spiel") ein Signal.
_STRUKTUR_MATERIAL_WORTE = ("korrosion", "rost", "durchrostung")
_STRUKTUR_VERSCHLEISS_ADJ = ("laenger", "poroes", "undicht", "locker", "lose", "sproede")
_RE_STRUKTUR_VERSCHLEISS = re.compile(
    r"\b(?:" + "|".join(_STRUKTUR_VERSCHLEISS_ADJ) + r")\w*\s+(?:wird|werden)\b"
    # Negations-Stresstest (Fall N): "wird mit der Zeit porös" — das haeufige
    # Zeit-Idiom darf das generische Fuellwort-Budget nicht aufbrauchen, sonst
    # geht die normale, 0-2-Woerter-Formulierung ("wird deutlich laenger")
    # verloren, wenn stattdessen dieses eine Idiom extra zugelassen wuerde.
    r"|\b(?:wird|werden)\s+(?:mit\s+der\s+zeit\s+|im\s+laufe\s+der\s+zeit\s+|nach\s+und\s+nach\s+)?"
    r"(?:\w+\s+){0,2}(?:" + "|".join(_STRUKTUR_VERSCHLEISS_ADJ) + r")\w*\b",
    re.IGNORECASE)
_RE_STRUKTUR_SPIEL = re.compile(
    r"\b(?:bekomm\w*|hat|haben|zeig\w*|entwickel\w*)\s+(?:\w+\s+){0,2}spiel\b", re.IGNORECASE)


def _hat_strukturelles_defektsignal(n: str, satz: str) -> bool:
    """True, wenn der Satz eines der oben genannten, kleinen strukturellen
    Signale enthält — zusätzlich zu, nicht statt, `_PROBLEM_WORTE`. `n` ist
    die bereits normalisierte Form des Satzes (siehe `_norm`)."""
    return (any(w in n for w in _STRUKTUR_MATERIAL_WORTE)
            or bool(_RE_STRUKTUR_VERSCHLEISS.search(satz))
            or bool(_RE_STRUKTUR_SPIEL.search(satz)))


# RC-W3: generische Verneinung ÜBER jedes bekannte Defekt-/Verschleisswort
# (bestehend aus `_PROBLEM_WORTE` UND neu aus den obigen Struktursignalen),
# statt nur die feste Phrasenliste in `_RELATIVIERUNG` zu erweitern. "kein(e)
# <Defektwort>" bzw. "nicht <Verschleiss-Adjektiv>" galt VORHER für KEIN
# Wort generisch (auch "kein Schaden"/"kein Defekt" wären mit der alten,
# rein phrasenbasierten `_RELATIVIERUNG` nicht erkannt worden) — das wird
# hier für die gesamte Wortliste auf einmal geschlossen, nicht pro Wort.
_DEFEKT_NOMEN_FUER_NEGATION = _PROBLEM_WORTE + _STRUKTUR_MATERIAL_WORTE + ("spiel",)
_RE_NEGATION_NOMEN = re.compile(
    r"\bkein\w*\s+(?:\w+\s+){0,2}(?:" + "|".join(_DEFEKT_NOMEN_FUER_NEGATION) + r")\w*\b",
    re.IGNORECASE)
_RE_NEGATION_ADJ = re.compile(
    r"\bnicht\s+(?:\w+\s+){0,2}(?:" + "|".join(_STRUKTUR_VERSCHLEISS_ADJ) + r")\w*\b",
    re.IGNORECASE)

# RC-W3 (Negations-Stresstest nach Korrosion/Rost-Aufnahme): "kein(e) X" und
# "nicht <Adjektiv>" allein reichen nicht — reale Quellen formulieren Abwesenheit
# auch als "X ist nicht vorhanden/feststellbar", "ohne X", "frei von X",
# "X ausgeschlossen" und "weder X noch Y". Jedes dieser Muster ist generisch
# (kein Fahrzeug-/Markenwort) und bewusst eng gefasst:
#   * "ohne"/"frei von" mit max. 1 Füllwort, DAMIT "Ohne rechtzeitige Reparatur
#     kann Rost die Struktur schwächen" (4 Wörter bis "Rost") NICHT mit-matcht —
#     ein echter Claim, kein negierter.
#   * "nicht vorhanden/feststellbar/erkennbar/..." ist als Phrase selbst schon
#     eindeutig Abwesenheit, unabhängig vom vorausgehenden Wort — kein Risiko
#     für "nicht nur ..." o.ä. (andere Fortsetzung, kein Match).
_RE_ABWESENHEIT_PHRASE = re.compile(
    r"\bnicht\s+(?:\w+\s+){0,2}(?:vorhanden|feststellbar|erkennbar|nachweisbar|sichtbar|auffindbar)\b",
    re.IGNORECASE)
_RE_OHNE_FREI_VON = re.compile(
    r"\bohne\s+(?:\w+\s+){0,1}(?:" + "|".join(_DEFEKT_NOMEN_FUER_NEGATION) + r")\w*\b"
    r"|\bfrei\s+von\s+(?:\w+\s+){0,1}(?:" + "|".join(_DEFEKT_NOMEN_FUER_NEGATION) + r")\w*\b",
    re.IGNORECASE)
_RE_AUSGESCHLOSSEN = re.compile(
    r"\b(?:" + "|".join(_DEFEKT_NOMEN_FUER_NEGATION) + r")\w*\s+(?:\w+\s+){0,3}ausgeschlossen\b",
    re.IGNORECASE)
_RE_WEDER_NOCH = re.compile(
    # Multi-Claim-Stresstest: "weder porös noch undicht" nennt zwei Adjektive,
    # keine Nomen — ohne die Verschleiss-Adjektivliste hier mit aufzunehmen,
    # rutscht diese Verneinung durch (gefundener False Positive).
    r"\bweder\s+(?:\w+\s+){0,1}"
    r"(?:" + "|".join(_DEFEKT_NOMEN_FUER_NEGATION + _STRUKTUR_VERSCHLEISS_ADJ) + r")\w*\b",
    re.IGNORECASE)


def _ist_benign_relativiert(satz: str) -> bool:
    """True, wenn der Satz seine eigene Problem-/Defektaussage relativiert oder
    verneint. Wirkt NUR auf den geprüften Satz selbst (Satz-, nicht
    Absatzebene) — ein echter Defekt-Claim im Nachbarsatz derselben Quelle
    bleibt davon unberührt."""
    n = _norm(satz)
    return bool(_RELATIVIERUNG.search(satz or "")
                or _RE_NEGATION_NOMEN.search(n)
                or _RE_NEGATION_ADJ.search(n)
                or _RE_ABWESENHEIT_PHRASE.search(n)
                or _RE_OHNE_FREI_VON.search(n)
                or _RE_AUSGESCHLOSSEN.search(n)
                or _RE_WEDER_NOCH.search(n))


# RC-W3 (Mixed-Polarity / Claim-Segmentierung): `_ist_benign_relativiert()`
# prüft EINEN Textabschnitt als Ganzes. Ein Satz wie "Korrosion ist nicht
# feststellbar, ABER die Dichtung ist undicht." enthält zwei durch ein
# Kontrastwort verbundene, GEGENSÄTZLICHE Teilaussagen — die erste benign,
# die zweite ein eigener, echter Claim; ein Satz kann auch 3+ solche
# Teilaussagen enthalten ("... aber ..., jedoch ..., während ..."). Eine
# erste Version dieses Fixes (Einmal-Split am ERSTEN Kontrastwort, Rest als
# EIN Block weiterverarbeitet) versagte bereits bei einem dritten Claim: der
# "Rest" blieb seinerseits ein gemischter Block, der als Ganzes wieder
# benign ODER wieder akzeptiert werden konnte, nie beides getrennt — und es
# konnte strukturell nie mehr als EIN WebFakt aus einem Satz entstehen.
#
# `_segmentiere_claims()` zerlegt stattdessen VOLLSTÄNDIG (nicht nur am
# ersten Treffer) in beliebig viele Claim-Segmente, in EINEM linearen
# `re.split()`-Durchlauf (kein Rekursion, keine Schleife, kein
# Endlosrisiko). Jedes Segment wird danach vom Aufrufer UNABHÄNGIG
# bewertet (benign/Problem-/Struktursignal/Komponente/Zeitraum) — ein
# benigner Claim verwirft nur sich selbst, ein echter Claim wird nur für
# sich selbst akzeptiert.
#
# Zwei Trenner-Arten:
#   1. Kontrastkonnektoren (aber/jedoch/allerdings/während/hingegen/dagegen/
#      dafür/zugleich/außerdem/zudem/dennoch/trotzdem) trennen IMMER — sie
#      signalisieren per Definition einen Wechsel der Aussage.
#   2. "und"/"sowie"/"oder" trennen NUR, wenn unmittelbar danach ein neuer
#      Subjekt-Claim beginnt (Artikel/Possessiv + Nomen: "... und DIE
#      WASSERPUMPE fällt aus") — sonst bleibt es ein zusammenhängender Claim
#      ("wird porös UND undicht", "Wasserpumpe UND Thermostat wurden
#      ersetzt"). Das ist eine bewusst kleine, generische Heuristik (keine
#      Grammatik-Analyse): sie erkennt nicht JEDEN Fall korrekt, vermeidet
#      aber das Hauptrisiko (Adjektiv-/Objekt-Aufzählung fälschlich als
#      neuer Claim).
_KONTRAST_KONNEKTOREN = ("aber", "jedoch", "allerdings", "waehrend", "während",
                        "hingegen", "dagegen", "dafuer", "dafür", "zugleich",
                        "ausserdem", "außerdem", "zudem", "dennoch", "trotzdem")
_RE_KONTRASTTEILUNG = re.compile(r"\b(?:" + "|".join(_KONTRAST_KONNEKTOREN) + r")\b",
                                 re.IGNORECASE)
_NEUER_CLAIM_ANKER = (r"(?:der|die|das|den|dem|des|ein|eine|einer|eines|einem|einen|"
                     r"sein|seine|ihr|ihre)\s+\w+")
_RE_UND_ALS_TRENNER = re.compile(r"\b(?:und|sowie|oder)\b\s+(?=" + _NEUER_CLAIM_ANKER + r")",
                                 re.IGNORECASE)
# Multi-Claim-Stresstest: "Die Wasserpumpe UND der Thermostat wurden ersetzt."
# erfüllt den Anker oben (Artikel+Nomen nach "und"), ist aber ein
# zusammengesetztes SUBJEKT vor EINEM gemeinsamen Prädikat, kein zweiter
# Claim — anders als "Die Dichtung ist undicht UND die Wasserpumpe zeigt
# einen Defekt" (zwei VOLLSTÄNDIGE Teilsätze). Unterscheidung: hat der Teil
# VOR "und" bereits sein eigenes finites Verb, ist es ein abgeschlossener
# Teilsatz — sonst meist nur ein Aufzählungs-Subjekt vor dem gemeinsamen
# Prädikat. Kleine, geschlossene Liste gängiger finiter Verbformen (kein
# Fahrzeugwort, keine Grammatik-Engine).
_RE_FINITES_VERB_ANKER = re.compile(
    r"\b(?:ist|sind|war|waren|wird|werden|wurde|wurden|hat|haben|hatte|hatten|"
    r"kann|k(?:ö|oe)nnen|konnte|muss|m(?:ü|ue)ssen|musste|sollte|sollten|"
    r"zeigt|zeigen|zeigte|tritt|treten|trat|f(?:ä|ae)llt|fallen|fiel|"
    r"bekommt|bekommen|bekam|entwickelt|entwickeln|entwickelte)\b",
    re.IGNORECASE)


def _und_segmente(text: str) -> list[str]:
    """Teilt `text` an "und/sowie/oder" + neuer Subjekt-Claim — aber NUR an
    Trennpunkten, an denen der Teil DAVOR (seit dem letzten Trennpunkt)
    bereits ein eigenes finites Verb trägt (siehe oben). Ein linearer
    Durchlauf über alle Treffer, keine Rekursion."""
    segmente: list[str] = []
    pos = 0
    for m in _RE_UND_ALS_TRENNER.finditer(text):
        vorher = text[pos:m.start()]
        if _RE_FINITES_VERB_ANKER.search(vorher):
            segmente.append(text[pos:m.start()])
            pos = m.end()
    segmente.append(text[pos:])
    return segmente
# Release-Review: eine frühere Fassung begrenzte die Segmentzahl künstlich
# (_MAX_CLAIM_SEGMENTE) und fiel bei Überschreitung zunächst sogar auf
# `[satz]` zurück — das MERGT bereits korrekt getrennte Claims wieder zu
# einem Block und bringt genau das Problem zurück, das die Segmentierung
# lösen soll (Polarität wirkt wieder quer über Claims, echte Claims gehen
# verloren). Ein reiner Truncation-Cap verwarf danach immerhin nur die
# Segmente über der Grenze, aber IMMER NOCH echte Information ohne
# nachweisbaren Grund.
#
# Performance-Messung (siehe Sessionbericht): `re.split()` + die lineare
# `_und_segmente()`-Suche bleiben bei 2000 (!) künstlich verketteten Claims
# in einem 71 KB "Satz" bei 4,6 ms — weit unterhalb jeder Tavily-Snippet-
# Realität (einzelne Suchtreffer liegen im Bereich einiger hundert bis
# wenige tausend Zeichen, zusätzlich auf `max_results=8` Treffer pro
# Kategorie begrenzt). Es gibt keinen nachweisbaren Grund für ein
# künstliches Claim-Anzahl-Limit, das reale Information verlieren könnte —
# daher: KEIN Cap. Die eigentliche, sinnvolle Grenze ist die ohnehin
# vorhandene, begrenzte Eingabegröße (Suchtreffer-Anzahl und -Länge), nicht
# eine willkürliche Segmentzahl.
def _segmentiere_claims(satz: str) -> list[str]:
    """Zerlegt `satz` vollständig in eigenständige Claim-Segmente (siehe
    Moduldocstring oben). Liefert immer mindestens `[satz]` — nur wenn KEIN
    Trenner gefunden wird, bleibt der ganze Satz EIN Segment (identisch zum
    Verhalten ohne diesen Fix). Kein Limit auf die Segmentzahl (siehe
    Performance-Begründung oben) — jedes gefundene Segment wird behalten."""
    if not satz:
        return [satz]
    segmente: list[str] = []
    for grobstueck in _RE_KONTRASTTEILUNG.split(satz):
        segmente.extend(_und_segmente(grobstueck))
    segmente = [s.strip(" ,;.") for s in segmente if s and s.strip(" ,;.")]
    return segmente or [satz]


# Release-Review (Risiko 2): explizite ALLOWLIST statt `!= "rueckruf"`. Eine
# Negativ-Prüfung hätte JEDE heutige oder künftige Kategorie außer "rueckruf"
# automatisch in die neue Claim-Semantik gezogen, auch ohne bewussste
# Entscheidung. RC-W3 betrifft ausschließlich die technische Schwachstellen-/
# Wartungs-Extraktion — "rueckruf" (Package A, bereits freigegeben/CLOSED)
# bleibt auf dem alten, satzweisen Pfad; jede andere/künftige Kategorie
# bekommt NICHT automatisch neue Semantik, sondern müsste hier explizit
# aufgenommen werden.
_CLAIM_SEGMENTIERUNG_KATEGORIEN = frozenset({"schwachstelle", "wartung"})


_INTERVALL = re.compile(
    r"(?:alle\s+)?(\d{1,3}(?:[.\s]\d{3})+|\d{4,6})\s*km"
    r"|(?:alle\s+)?(\d{1,3})\s*(monate|jahre?)", re.IGNORECASE)
# Satzgrenzen: Satzzeichen, Zeilenumbrüche und Markdown-Überschriften. Sonst
# verschmolzen Navigations-/Überschriftenfragmente ("Mazda MX-5 Rückruf ###
# Software …") zu einem scheinbaren Satz und wurden zum "Fakt".
_SATZ = re.compile(r"(?<=[.!?])\s+|\s*\r?\n\s*\r?\n\s*|\s*#{2,}\s*")

# Release-Hardening (Production Smoke 2, Root Cause 3): ein Satz, der sein
# eigenes Thema nicht selbst nennt, sondern auf einen VORHERGEHENDEN Satz
# verweist ("Das gilt häufig auch für ein schief stehendes Lenkrad."), wurde
# bisher wie jeder andere Satz isoliert gegen `_PROBLEM_WORTE`/Vokabular
# geprüft. "häufig" + "Lenkrad" reichten dafür bereits aus — ohne den
# vorhergehenden Satz, der das eigentliche Bauteil/Problem nennt, auf das sich
# "Das gilt auch für …" bezieht. Generisch (kein Wort über ein konkretes
# Bauteil oder Fahrzeug): jeder Satz, der mit einem solchen Rückbezug beginnt,
# braucht den UNMITTELBAR VORHERGEHENDEN Satz als Kontext, um überhaupt als
# eigenständiger Claim zu gelten. Ohne auflösbaren Vorgänger (erster Satz
# eines Treffers, oder der Vorgänger ist selbst nur ein Rückbezug) bleibt der
# Satz unvollständig und erzeugt keinen Fakt — siehe `_KONTEXT_AUFLOESEN`.
_AE = "(?:ä|ae)"
_KONTEXTABHAENGIG = re.compile(
    r"^\s*(?:"
    r"das\s+gilt\w*"
    r"|dies\s+(?:gilt|betrifft)\w*"
    r"|dieses\s+problem\b"
    r"|diese[rs]\s+(?:gilt|betrifft)\w*"
    r"|dasselbe\s+gilt\w*"
    r"|gleiches\s+gilt\w*"
    r"|ebenso\s+(?:gilt|betrifft|verh" + _AE + r"lt)\w*"
    r"|genauso\s+(?:gilt|betrifft)\w*"
    r"|auch\s+hier\b"
    r"|dabei\s+(?:kommt|handelt|tritt|zeigt)\w*"
    r"|hierbei\s+(?:kommt|handelt|tritt)\w*"
    r")", re.IGNORECASE)


def _kontext_aufloesen(saetze: list[str], idx: int) -> str | None:
    """Löst einen rückbezüglichen Satz mit seinem Vorgänger auf, oder meldet
    `None`, wenn kein belastbarer Bezug existiert (Satz bleibt dann unvollständig
    und wird NICHT als eigenständiger Claim verwendet — siehe Moduldocstring
    Root Cause 3)."""
    satz = saetze[idx]
    if not _KONTEXTABHAENGIG.match(satz):
        return satz
    if idx == 0:
        return None
    vorgaenger = saetze[idx - 1]
    if _KONTEXTABHAENGIG.match(vorgaenger):
        return None          # der Vorgänger selbst ist unaufgelöst -> keine Kette
    return f"{vorgaenger} {satz}"


def _bauteil_vokabular() -> dict[str, str]:
    from app.kaufaktionen import _KOMPONENTEN
    return {muster: eintrag["schluessel"]
            for eintrag in _KOMPONENTEN for muster in eintrag["muster"]}


# Lexus-RC-F-Forensik (Root Cause 5, CONFIRMED): `_bauteil_vokabular()` (aus
# `app.kaufaktionen._KOMPONENTEN`, mit "schwachstelle" GETEILT) kennt kein
# generisches "Motoröl/Ölwechsel"-Muster — nur Symptom-Komposita wie
# "oelverlust"/"oelverbrauch"/"oelpumpe"/"oelwanne". Eine der häufigsten,
# best-dokumentiertesten Wartungsaussagen überhaupt ("Motoröl alle X km
# wechseln") erreichte dadurch NIE `treffer_voc` — unabhängig von
# Quellenqualität. Bewusst NICHT in `_KOMPONENTEN` selbst ergänzt (Auftrag:
# "nicht blind erweitern, wenn dadurch Schwachstellen-Erkennung sich
# ändert") — eine separate, NUR für kategorie=="wartung" gemergte
# Erweiterung. Kein markenspezifischer Begriff.
_WARTUNG_VOKABULAR_ERWEITERUNG: dict[str, str] = {
    "motoroel": "motoroel", "oelwechsel": "motoroel", "motorenoel": "motoroel",
}


def _wartung_vokabular() -> dict[str, str]:
    """`_bauteil_vokabular()` PLUS die Motoröl-Erweiterung oben — NUR für
    kategorie=="wartung" verwenden, nie für "schwachstelle"/"rueckruf".

    Reihenfolge WICHTIG: `_vokabular_match()` gibt den ERSTEN Treffer nach
    Dict-Iterationsreihenfolge zurück (reiner Teilstring-Test, kein Longest-
    Match). Die Erweiterung MUSS vor `_bauteil_vokabular()` stehen — sonst
    gewinnt das bereits bestehende, generische Muster "motor" (Schlüssel
    "motor") den Teilstring-Wettlauf gegen "motoroel"/"motorenoel" (beide
    enthalten "motor" als Präfix), und jede Motoröl-Aussage würde fälschlich
    unter dem allgemeinen "motor"-Schlüssel landen statt unter "motoroel" —
    zwei an sich übereinstimmende Quellen würden dann nie derselben Gruppe
    zugeordnet (empirisch beim Testaufbau gefunden und hier verhindert)."""
    return {**_WARTUNG_VOKABULAR_ERWEITERUNG, **_bauteil_vokabular()}


def _saetze(text: str, *, max_len: int | None = 300) -> list[str]:
    """`max_len` (Default 300, unverändertes Alt-Verhalten für ALLE
    bestehenden Aufrufer, insb. Recall/Package A): obere Zeichengrenze pro
    Satz. RC-W3-Fund: ein realistischer, langer Multi-Claim-Satz (>300
    Zeichen) verschwand dadurch VOLLSTÄNDIG, bevor `_segmentiere_claims()`
    überhaupt lief — kein Kürzen, kein Mitten-im-Wort-Split, einfach weg,
    ohne jede Diagnose. Für die Kategorien, die Claim-Segmentierung nutzen
    (`schwachstelle`/`wartung`), wird `max_len=None` übergeben: die
    Sicherheitsgrenze gegen pathologischen Text liegt dort bereits bei
    `_segmentiere_claims()`/`_MAX_CLAIM_SEGMENTE`, eine zusätzliche
    Zeichengrenze auf Satzebene ist redundant und kostet nur echte Claims."""
    return [s.strip() for s in _SATZ.split(text or "")
            if len(s.strip()) >= 20 and (max_len is None or len(s.strip()) <= max_len)
            and len(s.split()) >= 5]


# Package C / RC-W3 (Root Cause H): "erste beiden Getriebe-Generationen" ist im
# Satz ein einziges Bindestrich-Kompositum — der Fund-Regex oben griff deshalb
# "Getriebe-Generationen" komplett als Bauteilnamen ab, statt nur "Getriebe".
# Dieselbe Scope-/Zeitangabe (Generation/Revision/Version/Serie/Charge/
# Ausbaustufe) wird bereits separat als `vage`/`geltungsbereich` abgebildet
# (siehe `_RE_VAGE`) — sie darf nicht zusätzlich im Bauteilnamen selbst
# landen. Generisch: JEDES Bauteilwort mit einer solchen Endung verliert nur
# diese Endung, nie markenspezifisch.
_BAUTEIL_SCOPE_ENDUNG = re.compile(
    r"-?(?:generation|revision|version|serie|charge|ausbaustufe)\w*$", re.IGNORECASE)


def _anzeige_bauteil(satz: str, muster: str) -> str:
    """Das Bauteil so, wie die Quelle es nennt ("Kraftstoffpumpe", "Verdeck") —
    nie der interne Dedup-Schlüssel ("einspritzung", "dach_fenster") und ohne
    eine angehängte Generations-/Revisions-/Serienangabe (die gehört in den
    Geltungsbereich, nicht in den Bauteilnamen)."""
    for wort in re.findall(r"[A-Za-zÄÖÜäöüß][\wÄÖÜäöüß-]*", satz or ""):
        if muster in _norm(wort).replace(" ", ""):
            kern = _BAUTEIL_SCOPE_ENDUNG.sub("", wort) or wort
            return kern[:1].upper() + kern[1:]
    return muster[:1].upper() + muster[1:]


def _fremde_generation(r: dict, modell: str | None, generation: str | None) -> bool:
    """Nennt der TITEL eine andere Generation als die belegte, ist die Seite
    über ein anderes Fahrzeug (z.B. "MX-5 NC Gebrauchtwagen-Check" für ein ND)."""
    if not generation:
        return False
    # Einzelbuchstaben ("MX-5 G 184") sind Leistungs-/Ausstattungskürzel, keine
    # Generation; "Modell IV (ND …)" liefert den Code ND (siehe _generationscodes).
    codes = {g["code"].upper() for g in _generationscodes({"title": r.get("title"), "content": ""}, modell)
             if len(g["code"]) > 1 or g["code"] in _ROEMISCH or g["kontext"]}
    return bool(codes) and generation.upper() not in codes


# Generische Bauteil-Endungen (deutsche Komposita) für Rückrufsätze, deren
# Bauteil nicht im Prüfplan-Vokabular steht. Keine Fahrzeug- oder Markenwörter.
_BAUTEIL_ENDUNGEN = ("leitung", "pumpe", "geraet", "gerät", "einheit", "modul", "airbag", "gurt",
                     "schloss", "bremse", "lenkung", "sensor", "kabel", "kabelbaum", "tank",
                     "schlauch", "ventil", "dichtung", "schraube", "mutter", "achse", "feder",
                     "stange", "lager", "gelenk", "getriebe", "kupplung", "leuchte", "scheinwerfer",
                     "batterie", "speicher", "software", "verschraubung", "halterung", "befestigung",
                     "rahmen", "sitz", "lehne", "generator", "steuerung", "regelung", "anlage")


def _rueckruf_bauteil(satz: str) -> str | None:
    """Erstes Kompositum mit Bauteil-Endung im Satz ("Kraftstoffleitungs-Rückruf"
    -> "Kraftstoffleitung")."""
    for wort in re.findall(r"[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜa-zäöüß][a-zäöüß]+)*", satz or ""):
        for teil in wort.split("-"):
            kl = teil.lower()
            for kandidat in (kl, kl[:-1] if kl.endswith("s") else None):
                if kandidat and len(kandidat) >= 5 and kandidat.endswith(_BAUTEIL_ENDUNGEN):
                    return teil[:1].upper() + (teil[1:-1] if kandidat != kl else teil[1:])
    return None


_RE_BETROFFEN = re.compile(r"betroffen|betrifft|betreffen|gebaut|produziert|hergestellt"
                           r"|produktionszeitraum|baujahr|modelljahr", re.IGNORECASE)
_RE_NICHT_BETROFFEN = re.compile(r"nicht\s+betroffen|nicht\s+betrifft|ausgenommen", re.IGNORECASE)


def _artikel_geltung(text: str, baujahr: int | None, hubraum=None,
                     leistung_ps: int | None = None) -> tuple[bool | None, str | None, tuple | None]:
    """Geltungsbereich eines Rückruf-ARTIKELS, nicht nur eines Satzes.

    BEFUND (realer Web-Smoke-Test, DB-Miss): der Rückrufsatz ("Rückruf wegen der
    Kraftstoffleitung") und der Scope-Satz ("Betroffen sind ausschließlich
    Modelle mit 1,5-Liter-Benzinmotor aus den Baujahren 2015 bis 2018") stehen
    in verschiedenen Sätzen. Die satzweise Prüfung sah den Scope nie; aus einem
    anderen Satz desselben Artikels entstand ein "series_only"-Rückruf.

    Gelesen werden nur Sätze mit Betroffenheits-/Produktionsbezug (ohne
    ausdrückliche Ausnahme "nicht betroffen"). Rückgabe (passt, grund, fenster):
    False = der Artikel schließt dieses Fahrzeug aus (Baujahr, Hubraum oder
    Leistung), True = ein Fenster deckt das Baujahr, None = offen."""
    from app.recall_filter import _RE_SCOPE_HUBRAUM, _RE_SCOPE_PS
    saetze = [s for s in _saetze(text) if _RE_BETROFFEN.search(s) and not _RE_NICHT_BETROFFEN.search(s)]
    fenster = [z for z in (zeitraum(s) for s in saetze) if z]
    deckend = [z for z in fenster if _im_zeitraum(baujahr, z)] if baujahr else []
    if baujahr and fenster and not deckend:
        return False, "baujahr_ausserhalb_artikel", fenster[0]
    hubs = {h.replace(",", ".") for s in saetze for h in _RE_SCOPE_HUBRAUM.findall(s)}
    if hubraum and hubs and str(hubraum).replace(",", ".") not in hubs:
        return False, "hubraum_ausserhalb_artikel", None
    ps_scope = {int(p) for s in saetze for p in _RE_SCOPE_PS.findall(s)}
    if leistung_ps and ps_scope and not any(abs(int(leistung_ps) - p) <= 3 for p in ps_scope):
        return False, "leistung_ausserhalb_artikel", None
    if deckend:
        return True, None, deckend[0]
    return None, None, None


# ── RC-W2: Sentence-Level-Fremdmarken-Prüfung (Release-Hardening) ───────────
#
# BEFUND (Forensik-Audit, realer Web-Smoke-Test): ein als Mazda-MX-5 stark
# ausgerichteter Artikel (Titel nennt Marke+Modell) enthielt in einem "Mehr
# zum Thema"-Block einen Satz über einen VW/Skoda-Lenkungsrückruf. Die
# Artikel-Ebene (`ausgerichtet()`) prüft nur Titel+Gesamttext, nie den
# einzelnen Satz, der am Ende zu einem Fakt wird — ein völlig fremder Rückruf
# wurde dadurch dem Zielfahrzeug zugeschrieben.
#
# Generische Regel (keine Marken-Sonderfälle): nennt der SATZ, der zu einem
# Rückruf-Fakt werden soll, eine ANDERE bekannte Marke (dieselbe `MARKEN`-
# Liste, die bereits `ausgerichtet()` verwendet), ohne auch die eigene Marke
# oder das eigene Modell zu nennen, ist der Satz fremdthematisch und wird
# verworfen. Nennt der Satz BEIDE (eigene und fremde Marke, z.B. ein Vergleich
# mit einem Schwestermodell), wird NICHT verworfen — das wäre eine aggressive
# Überreaktion, die echte Sätze verliert.
def _fremde_marke_satz(satz: str, marke: str | None, modell: str | None) -> str | None:
    from app.vehicle_identity import MARKEN
    satz_tokens = _tokens(satz)
    marke_tokens = _tokens(marke)
    modell_tokens = _tokens(modell)
    fremde = (satz_tokens & MARKEN) - marke_tokens
    if not fremde:
        return None
    if marke_tokens & satz_tokens:
        return None
    if modell_tokens and modell_tokens <= satz_tokens:
        return None
    return ",".join(sorted(fremde))


# ── RC-W4: lokale Modellkontext-Vererbung für schwach ausgerichtete Treffer ──
#
# BEFUND (Offline-Alignment-Matrix, Fälle C/D): nennt der TITEL eines Treffers
# das Modell nicht ("schwach" ausgerichtet), musste bisher JEDER einzelne Satz
# das Modell selbst erneut nennen (`_satz_nennt_modell`) — ein Folgesatz, der
# nur aus dem UNMITTELBAR VORHERIGEN Satz seinen Fahrzeugbezug erbt ("Beim
# Golf ... . Die Wasserpumpe kann undicht werden."), verlor diesen Bezug
# ersatzlos, obwohl der Artikel eindeutig weiter vom selben Fahrzeug spricht.
#
# FIX: ein Satz, der das Modell selbst nennt, ETABLIERT lokalen Kontext für
# GENAU die nächsten `_RC_W4_MAX_KONTEXT_DISTANZ` Sätze DESSELBEN Treffers —
# nicht grenzenlos (siehe Review-Korrektur unten) — und wird durch einen Satz
# mit EXPLIZITEM Widerspruch (fremde Marke, oder eine der engen "ein anderes
# Fahrzeug"-Konstruktionen wie "Beim Passat...") sofort zurückgesetzt. Ohne
# jemals etablierten Kontext bleibt das bisherige Verhalten exakt erhalten
# (Reject). Diese Vererbung gilt NUR für `grund == "schwach"` — titel-
# ausgerichtete Treffer sind von diesem Fix unverändert (§7 Auftrag).
#
# REVIEW-KORREKTUR: eine erste Fassung ließ den Kontext bis zum NÄCHSTEN
# expliziten Widerspruch unbegrenzt weiterleben (beliebig viele neutrale
# Zwischensätze). Das wurde als zu riskant verworfen — "lokal" heißt NICHT
# "result-weit dauerhaft". Jetzt zählt eine Distanz (Sätze seit der letzten
# EXPLIZITEN Modellnennung, NICHT seit der letzten geerbten Akzeptanz — ein
# geerbter Satz verlängert die Kette nicht, er nennt das Modell ja nicht
# selbst). `_RC_W4_MAX_KONTEXT_DISTANZ = 1`: NUR der unmittelbar auf eine
# Modellnennung folgende Satz darf erben — bewusst die striktere der beiden
# im Auftrag vorgeschlagenen Optionen (Distanz 1 "bevorzugt" vs. Distanz 2
# "maximal"), weil ein zusätzlicher neutraler Brückensatz nicht sicher von
# einem Themen-/Absatzwechsel unterscheidbar ist, ohne eine neue, fehler-
# anfällige Segment-Erkennung einzuführen — Priorität liegt auf False-
# Negative- statt False-Positive-Vermeidung (§9 Auftrag).
#
# Bewusste Begrenzung (kein Fahrzeug-/Modell-Katalog, keine neue große NER-
# Logik): "anderes Modell derselben Marke" wird NUR über die engen, in echten
# Vergleichsartikeln empirisch üblichen Konstruktionen "beim/bei dem X" und
# "für den/die/das X" erkannt (reale Beispiele: "Beim Golf...", "Beim
# Passat...", "Für den Passat..."). Eine BARE "Der/Die/Das X"-Erkennung ohne
# Präposition wurde BEWUSST NICHT ergänzt: das ist die normale Form jedes
# deutschen Satzanfangs mit Subjekt (auch "Der Besitzer...", "Die Werkstatt...",
# "Die Wasserpumpe..." — letzteres wäre sonst fälschlich als Widerspruch
# erkannt worden), ohne Modellkatalog nicht sicher von einer anderen
# Fahrzeug-Erwähnung unterscheidbar — genau der Fall, in dem der Auftrag
# "Kontext lieber konservativ verfallen lassen" verlangt. Die Distanz-1-TTL
# fängt den Großteil des Restrisikos ab: ein nicht erkannter "Der Passat
# hat X"-Satz kann nur an GENAU Distanz 1 fälschlich erben; ab Distanz 2 ist
# er durch die TTL ohnehin schon ausgeschlossen (siehe Abschlussbericht).
# Ein Bauteilname nach der Präposition ("Beim Turbolader...") zählt NICHT als
# anderes Fahrzeug — reuse des bestehenden Bauteil-Vokabulars.
#
# Produktions-Forensik (Lexus-RC-F-PASS-GATE, §A Folgeauftrag Punkt 5/6,
# generisch): ein Satz, der das Zielmodell UND eine Geschwister-Variante
# derselben Baureihe in EINER kontrastiven Konstruktion nennt ("Anders als
# der/die/das X ...", "Im Gegensatz zum X ...", "Unlike the X ...", "Compared
# to the X ...") wurde bisher NICHT als Fremdfahrzeug-Signal erkannt, weil
# weder "beim/für den X" noch die satzanfangs-Nominalphrase zutrafen -- ein
# positiver Claim (z.B. Hybrid/Elektrifizierung), der inhaltlich zu X gehört,
# aber im selben, durch die eigene Modellnennung akzeptierten Satz steht,
# wurde dadurch fälschlich dem Zielmodell zugerechnet. GENERISCH für JEDE
# Baureihe mit ICE/Hybrid- oder sonstigen Geschwistervarianten, kein
# Marken-/Modell-Hardcode -- nur das sprachliche Kontrastmuster selbst.
_RE_ANDERES_SUBJEKT = re.compile(
    # "zur"/"zum" (verschmolzene Form von "zu der"/"zu dem") NEBEN der
    # getrennten Form -- sonst würde "im Gegensatz ZUR X" (grammatisch die
    # häufigere Form) nicht erkannt.
    r"\b(?:[Bb]ei(?:m|\s+dem)|[Ff]ür\s+(?:den|die|das)|"
    r"[Aa]nders\s+als|[Ii]m\s+[Gg]egensatz\s+zu(?:r|m)?|[Ii]m\s+[Uu]nterschied\s+zu(?:r|m)?|"
    r"[Vv]erglichen\s+mit|[Uu]nlike|[Cc]ompared\s+to|[Ii]n\s+contrast\s+to)"
    r"\s+(?:der|den|die|das|dem|the)?\s*"
    # Nameplate-Geschwister sind oft >1 Token ("RC 300h", "3er Touring") --
    # bis zu 2 weitere, direkt folgende Code-Tokens mitfangen (GROSSBUCHSTABE
    # oder Ziffer am Wortanfang -- ein normales Fließtext-Wort wie "bietet"
    # darf NICHT mitgefangen werden), sonst würde nur "RC" erfasst, das als
    # Präfix-Token des Zielmodells ("RC F") fälschlich als "eigenes Modell"
    # durchgewunken würde (§6 Auftrag: Geschwistervarianten derselben
    # Baureihe dürfen sich nicht gegenseitig kontaminieren).
    r"([A-ZÄÖÜ][\wÄÖÜäöüß-]*(?:\s+[A-ZÄÖÜ0-9][\wÄÖÜäöüß0-9-]*){0,2})")
# Häufige, idiomatische Füllwörter nach "beim"/"für den" X, die KEIN
# Fahrzeug-Subjekt sind (Aktivitäten/Nominalisierungen/Akteure, kein
# Modellkatalog).
_KEIN_FAHRZEUG_SUBJEKT_LOKAL = frozenset({
    "kauf", "verkauf", "start", "anlassen", "kaltstart", "fahren", "bremsen",
    "beschleunigen", "parken", "tanken", "service", "wechsel", "einbau",
    "ausbau", "pruefen", "test", "fahrzeug", "auto", "modell", "vorgaenger",
    "nachfolger", "facelift", "besitzer", "vorbesitzer", "eigentuemer",
    "fahrer", "haendler", "verkaeufer", "mechaniker", "werkstatt", "pruefer",
    "gutachter", "tuev", "anfahren", "abbremsen", "lenken", "schalten",
    "hochschalten", "runterschalten", "warmlaufen", "abstellen", "beladen",
})
_RC_W4_MAX_KONTEXT_DISTANZ = 1

# ── F18-Fix (RC-F-Ursachenanalyse, 2026-10-25): bedingte Rückwärts-Vererbung ──
#
# Befund (real an carwiki.de/lexus-rc-oelwechsel geprüft, siehe Analyse-Bericht
# vom selben Datum): der bisherige Kontext wird AUSSCHLIESSLICH vorwärts
# vererbt (`abstand_seit_nennung`/`kontext_aktiv` werden erst nach einer
# Modellnennung wahr). Eine reale Mehrvarianten-Seite mit allgemeiner
# Übersicht VOR der Tabelle ("Normal: Wechseln 15000 km / 12 Monate ...")
# verliert dadurch Werte, die VOR der ersten Modellnennung stehen — auch dann,
# wenn Wert UND Modellnennung im selben, durchgehenden Abschnitt stehen.
#
# KEINE pauschale symmetrische Rückwärtsvererbung (§ Auftrag): ein Wert vor
# der Modellnennung wird NUR übernommen, wenn der UNMITTELBAR FOLGENDE Satz
# GENAU das Zielmodell nennt — NICHT, wenn dieser Satz mehrere Varianten/
# Motorisierungen AUFZÄHLT (§ Auftrag: "Aufzählungen mehrerer Motorisierungen
# dürfen keine einzelne Variante vermeintlich bestätigen"). Das ist exakt der
# reale carwiki.de-Fall: der auf die Intervallbox folgende Satz nennt "RC
# 200t, RC 300 (ASC10), RC 300h (AVC10), RC 350 UND RC F (USC10)" in einem
# Zug — eine Aufzählung, keine Einzelzuordnung. Dieser Fall bleibt nach dem
# Fix bewusst UNGEKLÄRT (kein Kandidat), weil die tatsächlich verfügbare
# Struktur (Fließtext ohne erhaltene Tabellen-Spaltenzuordnung) keine sichere
# Zuordnung zu GENAU "RC F" trägt — der frühere Abnahmepunkt "carwiki muss
# mindestens einen RC-F-Kandidaten liefern" gilt deshalb NICHT mehr
# bedingungslos (siehe Umsetzungsbericht).
#
# Reichweite bewusst auf GENAU EINEN Satz begrenzt (symmetrisch zum
# bestehenden Vorwärtsfenster `_RC_W4_MAX_KONTEXT_DISTANZ`), bewusst KEIN
# rekursives Weiterreichen: ein rückwärts-erbender Satz gibt den Kontext
# NICHT an einen dritten, noch früheren Satz weiter. Ein echter Segment-
# bruch (`segment_marker`, wo verfügbar) zwischen den beiden Sätzen
# unterbindet die Vererbung ebenso wie ein fremdes Modell/ein Widerspruch im
# Zwischensatz selbst (beides bereits an der Einsatzstelle geprüft, siehe
# `_wartung_kandidat_saetze`/`_segment_kontext_saetze`).


def _core_modell_praefix(modell: str | None) -> str | None:
    """Der wiederkehrende Familien-/Baureihenname vor einer konkreten
    Variantenbezeichnung ("RC" bei "RC F", "Golf" bei "Golf GTI") — bewusst
    NICHT über `parse_core_model()` (das verlangt für das NACHGESTELLTE
    Token mindestens eine Ziffer, siehe `_ist_modell_code_kandidat` — "F"
    in "RC F" erfüllt das NIE, der real relevante Fall würde sonst
    unentdeckt bleiben). Hier reicht das einfache ERSTE Wort eines mehr-
    teiligen Modellnamens als Familienpräfix. `None` bei einteiligen
    Modellnamen ("320i") — dort greift `_ist_varianten_aufzaehlung()` unten
    NICHT (dokumentierte Grenze: ohne zerlegbaren Präfix gibt es kein
    generisches Signal für eine Aufzählung mehrerer Varianten desselben
    Namens)."""
    teile = (modell or "").strip().split()
    return teile[0] if len(teile) >= 2 else None


def _ist_varianten_aufzaehlung(satz: str, modell: str | None) -> bool:
    """True, wenn der Satz den Familien-/Baureihennamen des Zielmodells MEHR
    ALS EINMAL als eigenes Wort nennt — ein generisches, markenunabhängiges
    Indiz dafür, dass hier mehrere Varianten/Motorisierungen derselben
    Baureihe aufgezählt werden (nicht EINE davon gemeint ist)."""
    praefix = _core_modell_praefix(modell)
    if not praefix:
        return False
    return len(re.findall(rf"\b{re.escape(praefix)}\b", satz, re.IGNORECASE)) > 1


# Korrektur (Abschlussauftrag 2026-10-25): "der folgende Satz nennt das
# Zielmodell" allein beweist KEINE Zuordnung der vorherigen Aussage -- das
# wäre dieselbe reine Satznachbarschafts-Annahme nur in die andere Richtung
# (ein Satz, der das Zielmodell nur als NEUES Thema einführt, z.B. "Der
# Lexus RC F ist ein Hochleistungscoupe.", bindet die vorherige, allgemeine
# Aussage NICHT an sich). Rückwärts-Vererbung verlangt deshalb ZUSÄTZLICH
# einen generischen, erkennbaren RÜCKBEZUG auf eine vorherige Aussage ("gilt
# (auch) für", "das/dies gilt/betrifft", "ebenso/genauso für") -- fehlt
# dieser Rückbezug im tatsächlich verfügbaren Text, bleibt der Fall
# UNGEKLÄRT (kein Claim), statt eine Zuordnung zu erfinden.
_RUECKREFERENZ = re.compile(
    r"\bgilt\s+(?:auch\s+|gleicherma(?:ß|ss)en\s+|ebenfalls\s+)?(?:f(?:ü|ue)r|bei(?:m)?)\b"
    r"|\bgleiches\s+gilt\b"
    r"|\b(?:das|dies(?:e[rs]?)?)\s+(?:gilt|betrifft|trifft\s+zu)\b"
    r"|\bebenso\s+(?:f(?:ü|ue)r|beim|gilt)\b"
    r"|\bgenauso\s+(?:f(?:ü|ue)r|beim)\b", re.IGNORECASE)


def _ist_echte_rueckreferenz(folge: str, modell: str | None) -> bool:
    """True NUR, wenn der folgende Satz GENAU das Zielmodell nennt (keine
    Aufzählung mehrerer Varianten, siehe `_ist_varianten_aufzaehlung`) UND
    einen generischen Rückbezug auf eine vorherige Aussage trägt UND dieser
    Rückbezug selbst nicht verneint ist ("gilt NICHT für" schließt das
    Zielmodell explizit AUS, statt eine Zuordnung zu bestätigen)."""
    if not _satz_nennt_modell(folge, modell):
        return False
    if _ist_varianten_aufzaehlung(folge, modell):
        return False
    m = _RUECKREFERENZ.search(folge)
    if not m:
        return False
    return not (_verneint_im_teilsatz(folge, m.start())
               or _verneint_nach_position(folge, m.start()))


def _rueckwaerts_erbfaehige_indizes(satz_liste: list[str], *, modell: str | None,
                                    segment_marker: list[bool] | None = None) -> set[int]:
    """Indizes von Sätzen OHNE eigene Modellnennung, die dem unmittelbar
    FOLGENDEN Satz vertrauen dürfen — nur wenn dieser folgende Satz eine
    ECHTE Rückreferenz trägt (siehe `_ist_echte_rueckreferenz()`), nicht nur
    irgendeine Modellnennung, und kein echter Segmentbruch dazwischenliegt."""
    erbfaehig: set[int] = set()
    for idx in range(len(satz_liste) - 1):
        if segment_marker and segment_marker[idx + 1]:
            continue
        if _satz_nennt_modell(satz_liste[idx], modell):
            continue
        if _ist_echte_rueckreferenz(satz_liste[idx + 1], modell):
            erbfaehig.add(idx)
    return erbfaehig


def _anderes_fahrzeug_lokal(satz: str, modell: str | None) -> str | None:
    """Enge Erkennung eines ANDEREN, explizit genannten Fahrzeug-Subjekts in
    der "beim/für den X"-Konstruktion (oder einer kontrastiven Konstruktion,
    s. `_RE_ANDERES_SUBJEKT`) — None, wenn keine solche Konstruktion vorliegt
    oder X das Zielmodell/ein Bauteil/ein bekanntes Füllwort ist. Die
    Zielmodell-Prüfung nutzt dieselbe robuste Signatur wie überall sonst im
    Modul (`_modell_ausgerichtet`), NICHT nur einfache Token-Mengen-
    Zugehörigkeit -- sonst würde "Anders als der RC F ..." (X = das
    Zielmodell SELBST, mehrwortig genannt) fälschlich als anderes Fahrzeug
    gelten, weil der GANZE normalisierte Ausdruck kein einzelnes Element der
    `_tokens(modell)`-Menge ist."""
    m = _RE_ANDERES_SUBJEKT.search(satz)
    if not m:
        return None
    wort = _norm(m.group(1))
    wort_tokens = _tokens(m.group(1))
    modell_tokens = _tokens(modell)
    if (not wort or wort in modell_tokens or wort in _KEIN_FAHRZEUG_SUBJEKT_LOKAL
            or _modell_ausgerichtet(wort_tokens, modell, quelle_konkat=_quelle_konkatenationen(m.group(1)))):
        return None
    vokabular = _bauteil_vokabular()
    if any(wort in muster or wort == schluessel for muster, schluessel in vokabular.items()):
        return None
    return wort


# RC-W4 (Review-Korrektur 2): die "beim/für den X"-Erkennung allein lässt eine
# bare Konstruktion wie "Der Passat hat Probleme ..." unentdeckt durch — bei
# Distanz 1 (direkt nächster Satz) ist das ein echtes False-Positive-Risiko
# (anders als bei Distanz >=2, wo die TTL es ohnehin abfängt). Eine naive
# "Der/Die/Das X"-Regel würde aber jedes technische Subjekt ("Die Wasserpumpe",
# "Der Motor", "Das Getriebe") zerstören. Lösung: Satzanfang "Der/Die/Das X"
# wird NUR dann als unsicher/widersprüchlich behandelt, wenn X WEDER das
# Zielmodell NOCH ein bekanntes Bauteil (bestehendes Bauteil-Vokabular) NOCH
# ein bekannter Nicht-Fahrzeug-Akteur (`_KEIN_FAHRZEUG_SUBJEKT_LOKAL`, z.B.
# "Fahrer", "Werkstatt") ist. Ohne Fahrzeugkatalog lässt sich ein übrig
# bleibendes X ("Passat", "Tiguan", aber z.B. auch ein unbekanntes Wort) nicht
# sicher als "anderes Fahrzeug" bestätigen — §5 Auftrag verlangt in diesem
# Fall explizit: nicht raten, Kontextvererbung lieber konservativ blockieren.
_RE_SATZANFANG_NOMINALPHRASE = re.compile(r"^\s*[Dd](?:er|ie|as)\s+([A-ZÄÖÜ][\wÄÖÜäöüß-]*)")

# F16-Folgefix (real reproduzierter Gegenfall, Fremdvariante mit tatsaechlich
# extrahierbarer Antriebsangabe bei passender Herstellerdomain): "Die Lexus
# RC 300h ist ein Vollhybrid..." bestaetigte bisher faelschlich einen
# Antriebs-Claim fuer das Zielmodell "RC F", weil `_RE_SATZANFANG_
# NOMINALPHRASE` (oben, Signatur bewusst unveraendert) laut eigener
# Definition NUR das ERSTE Wort nach "Der/Die/Das" einfaengt -- bei "Die
# Lexus RC 300h ist ..." also NUR "Lexus", NIE den direkt folgenden,
# tatsaechlich ABWEICHENDEN Modellnamen "RC 300h". Trifft `unsicher` exakt
# die eigene Marke, konnte die bisherige Pruefung (`bool(eigene_tokens &
# marke_tokens)`) deshalb NIE unterscheiden, ob direkt danach dasselbe
# Zielmodell weitergenannt wird ("Lexus RC F") oder ein ANDERES ("Lexus RC
# 300h") -- beide ergeben identisch `unsicher == marke`.
#
# Generischer Fix (kein Fahrzeug-/Code-Ausschluss, keine Sonderfallliste):
# NUR in genau diesem einen Fall (die Satzanfangs-Phrase ist exakt die
# Marke) werden die unmittelbar NACHFOLGENDEN grossgeschriebenen/
# codeartigen Tokens (begrenzt auf wenige, s.u.) zusaetzlich eingesammelt
# und mit derselben, bereits bestehenden `_modell_ausgerichtet()`-Pruefung
# gegen das Zielmodell abgeglichen -- keine neue Heuristik, nur eine
# vollstaendigere Eingabe fuer die bereits vorhandene Pruefung. Folgt nach
# der Marke nichts Grossgeschriebenes mehr ("Die Lexus ist auch mit X
# erhaeltlich." -- reine Markenfortsetzung ohne neuen Modellnamen), bleibt
# die bisherige Kulanz unveraendert erhalten.
_RE_GROSSGESCHRIEBENE_FOLGE = re.compile(
    r"\s+([A-ZÄÖÜ0-9][\wÄÖÜäöüß-]*(?:\s+[A-ZÄÖÜ0-9][\wÄÖÜäöüß-]*){0,3})")


def _unsichere_satzanfangs_nominalphrase(satz: str, modell: str | None) -> str | None:
    """Satz beginnt mit "Der/Die/Das X", X ist nicht als Zielmodell, Bauteil
    oder bekannter Nicht-Fahrzeug-Akteur identifizierbar — konservativ als
    nicht vererbbar behandelt (siehe Kommentar oben)."""
    m = _RE_SATZANFANG_NOMINALPHRASE.match(satz)
    if not m:
        return None
    wort = _norm(m.group(1))
    modell_tokens = _tokens(modell)
    if not wort or wort in modell_tokens or wort in _KEIN_FAHRZEUG_SUBJEKT_LOKAL:
        return None
    vokabular = _bauteil_vokabular()
    if any(wort in muster or wort == schluessel for muster, schluessel in vokabular.items()):
        return None
    return wort


def _fremdfahrzeug_explizit_lokal(satz: str, marke: str | None,
                                  modell: str | None) -> str | None:
    """Die beiden ZUVERLÄSSIGEN, NIEDRIG-False-Positive-Signale aus
    `_claim_widerspricht_fahrzeug_lokal()` -- explizite fremde Marke
    (`_fremde_marke_satz`) und die enge "beim/für den <anderes Fahrzeug>"-
    Konstruktion (`_anderes_fahrzeug_lokal`). BEWUSST OHNE die weichere
    `_unsichere_satzanfangs_nominalphrase`-Heuristik (dritter Zweig dort):
    die ist auf den "schwach"-Kontext kalibriert (siehe Docstring dort) und
    erzeugt bei normalen, satzanfangstehenden Bauteilnamen echter Ziel-
    fahrzeug-Sätze False Positives (z.B. "Die Zündkerzen werden beim 2.0
    SKYACTIV-G ... gewechselt." -- kein anderes Fahrzeug, nur ein der
    Funktion unbekanntes Bauteilwort am Satzanfang). Für Seiten mit STARKER
    Artikel-Ausrichtung (Titel nennt Marke+Modell bereits) bleibt deshalb nur
    dieser engere, generische Fremdfahrzeug-Check aktiv (Fortsetzungsauftrag,
    F18-Konkretisierung) -- die volle, striktere Prüfung inkl. Nominalphrase
    bleibt dem bestehenden "schwach"-Pfad vorbehalten."""
    fremde_marke = _fremde_marke_satz(satz, marke, modell)
    if fremde_marke:
        return f"fremde_marke_lokal:{fremde_marke}"
    anderes = _anderes_fahrzeug_lokal(satz, modell)
    if anderes:
        return f"anderes_fahrzeug_lokal:{anderes}"
    return None


def _claim_widerspricht_fahrzeug_lokal(satz: str, marke: str | None,
                                       modell: str | None) -> str | None:
    """Expliziter Widerspruch auf Satzebene (schlägt geerbten Kontext IMMER):
    fremde Marke (bestehende `_fremde_marke_satz`-Logik), die enge "beim/für
    den <anderes Fahrzeug>"-Konstruktion, oder eine unsichere, nicht als
    Bauteil/Akteur klassifizierbare Satzanfangs-Nominalphrase."""
    fremde_marke = _fremde_marke_satz(satz, marke, modell)
    if fremde_marke:
        return f"fremde_marke_lokal:{fremde_marke}"
    unsicher = _unsichere_satzanfangs_nominalphrase(satz, modell)
    # Claim-Level Vehicle Binding (Forensik-Folgeauftrag, Package A): zwei
    # generische False-Positives von `_unsichere_satzanfangs_nominalphrase()`
    # (ihre eigene, bereits getestete 2-Parameter-Signatur bleibt UNVERÄNDERT,
    # siehe test_technical_research_context_alignment.py) blockierten hier
    # Sätze, die tatsächlich das Zielfahrzeug beschreiben:
    #   1) "Die <Marke> <Modell> ist ..." -- `unsicher` ist die EIGENE Marke,
    #      nicht der Modellname; die Funktion prüft nur gegen `modell`.
    #   2) "Der <Modell-mit-Bindestrich> ..." (z.B. "Der MX-5 wird ...") --
    #      `unsicher` ist der normalisierte, LEERZEICHEN-getrennte Modellname
    #      ("mx 5"), aber `modell_tokens` ist eine Menge EINZELNER Wörter
    #      ({"mx","5"}) -- "mx 5" als GANZER String steht nie darin, selbst
    #      wenn es exakt das Zielmodell ist.
    # Beide Fälle sind kein Widerspruch: `unsicher`s eigene Tokens werden hier
    # robust (dieselbe Signatur-Logik wie überall sonst im Modul,
    # `_modell_ausgerichtet`) gegen Marke UND Modell geprüft. Eine FREMDE
    # Marke/ein fremdes Modell bleibt weiterhin über `_fremde_marke_satz()`
    # (oben) bzw. `_anderes_fahrzeug_lokal()` (unten) erkannt und schlägt
    # immer zu -- diese Prüfung hier lockert NUR den False-Positive für die
    # EIGENE Marke/das EIGENE Modell.
    if unsicher:
        eigene_tokens = _tokens(unsicher)
        ist_eigene_marke_oder_modell = _modell_ausgerichtet(eigene_tokens, modell)
        if not ist_eigene_marke_oder_modell and eigene_tokens <= _tokens(marke):
            # s. `_RE_GROSSGESCHRIEBENE_FOLGE`-Kommentar oben: `unsicher` ist
            # exakt die Marke -- ohne weitere Prüfung käme JEDE direkt
            # folgende, abweichende Modellbezeichnung ungeprüft durch.
            nach_marke = _RE_SATZANFANG_NOMINALPHRASE.match(satz)
            folge = _RE_GROSSGESCHRIEBENE_FOLGE.match(satz[nach_marke.end():]) if nach_marke else None
            ist_eigene_marke_oder_modell = (
                not folge or _modell_ausgerichtet(eigene_tokens | _tokens(folge.group(1)), modell))
        # Abschlussauftrag (2026-10-25), real reproduzierter Fall: ein Satz
        # wie "Der Ölwechsel erfolgt alle 60.000 Kilometer beim Lexus RC F."
        # nennt das Zielmodell EXPLIZIT -- die Satzanfangs-Nominalphrase
        # ("Ölwechsel") ist dabei nur deshalb "unsicher", weil sie weder im
        # (schwachstelle-geteilten) Bauteil-Vokabular noch in der kleinen
        # Nicht-Fahrzeug-Akteur-Liste steht, NICHT weil der Satz tatsächlich
        # von einem anderen Fahrzeug handelt. Eine explizite, direkte
        # Nennung des Zielmodells IRGENDWO im Satz ist eine stärkere,
        # positive Bestätigung als diese rein lexikalische, konservative
        # Vermutung sie widerlegen kann -- `_fremde_marke_satz()` (oben) UND
        # `_anderes_fahrzeug_lokal()` (unten) bleiben UNVERÄNDERT und
        # erkennen ein tatsächlich anderes Fahrzeug weiterhin unabhängig
        # davon (Fremdmodellschutz bleibt erhalten): diese Lockerung gilt
        # NUR für die "unsichere Nominalphrase"-Vermutung selbst.
        if not ist_eigene_marke_oder_modell and not _satz_nennt_modell(satz, modell):
            return f"unsichere_nominalphrase_lokal:{unsicher}"
    anderes = _anderes_fahrzeug_lokal(satz, modell)
    if anderes:
        return f"anderes_fahrzeug_lokal:{anderes}"
    return None


# ── RC-W4 Runde 2: Segment-Kontext statt fixer Distanz-1-TTL ───────────────
#
# BEFUND (Auftrag "RC-W4 neu aufrollen"): die Distanz-1-TTL (`_RC_W4_MAX_
# KONTEXT_DISTANZ = 1`) verliert Kontext bereits nach GENAU einem neutralen
# Brückensatz ("Beim Golf ... . Viele Fahrer berichten davon. Die Wasserpumpe
# kann undicht werden." -- der dritte Satz würde verworfen). Zu konservativ:
# eine fixe Satzanzahl kann einen echten Themenwechsel nicht von einem
# gewöhnlich langen, neutralen Absatz unterscheiden.
#
# STRUKTUR-AUDIT (vor der Implementierung geprüft, siehe Abschlussbericht):
# Tavily-`content`-Snippets liefern in der Praxis so gut wie NIE zuverlässige
# `\n\n`-Absatzgrenzen (verifiziert an echten, in dieser Sitzungsreihe
# gespeicherten Treffern) -- `_SATZ` (oben) behandelt `\n\n` zwar technisch
# als Trenner, dieses Signal ist aber in echten Snippets fast nie vorhanden
# und damit keine verlässliche Grundlage. Eine HTML-/Absatzstruktur ist zu
# diesem Zeitpunkt (nach `_saetze()`) ohnehin schon vollständig zu einer
# flachen Satzliste verarbeitet -- es gibt keine Absatzinformation mehr, die
# man "wiederherstellen" könnte.
#
# NEUE REGEL (inhaltsgetrieben statt distanzgetrieben): ein Satz, der das
# Modell selbst nennt -- ODER, bei einem titel-stark ausgerichteten Treffer,
# der Treffer von Anfang an -- etabliert lokalen Fahrzeugkontext für ALLE
# nachfolgenden Sätze DESSELBEN Treffers, UNBEGRENZT in der Satzanzahl.
# Kontext endet NICHT nach einer fixen Distanz, sondern NUR durch einen
# echten inhaltlichen Kontextbruch -- exakt dieselbe, bereits bestehende und
# gut getestete `_claim_widerspricht_fahrzeug_lokal()`-Prüfung (fremde Marke,
# "beim/für den <anderes Fahrzeug>", unsichere Satzanfangs-Nominalphrase).
# KEINE neue Erkennung, KEIN Fahrzeug-/Modell-Katalog -- dieselben drei
# Signale wie bisher, nur ohne die zusätzliche künstliche Distanz-Schranke.
#
# KEINE unbegrenzte result-weite Vererbung (§2 Auftrag): ein Kontextbruch
# wirkt weiterhin sofort und vollständig -- danach ist wieder eine explizite
# Modellnennung nötig, bevor erneut geerbt werden darf (siehe Test F/H in der
# Testmatrix). Das unterscheidet dies von der in Runde 1 bereits VERWORFENEN
# "einmal erkannt -> für immer" Variante.
#
# NEU (§10 Auftrag): bisher lief die Widerspruchsprüfung NUR für `grund ==
# "schwach"` -- ein titel-stark ausgerichteter Treffer ("VW Golf 8
# Gebrauchtwagen-Test") akzeptierte JEDEN Satz ungeprüft, auch einen
# expliziten Vergleichssatz über ein anderes Fahrzeug. Jetzt gilt dieselbe
# Widerspruchsprüfung auch hier: ein expliziter Fremdfahrzeug-Satz schlägt
# den Titel-Kontext, bis das Zielmodell erneut explizit genannt wird.
#
# SCOPE (§15 Auftrag -- Recall/KBA und der Wartungs-Evidence-Layer bleiben
# unangetastet): NUR für `kategorie in _CLAIM_SEGMENTIERUNG_KATEGORIEN`
# (schwachstelle/wartung) in `_extrahiere_fakten()` aktiv. "rueckruf" nutzt
# weiterhin `_distanz1_kontext_saetze()` -- ein unveränderter Port der alten
# Distanz-1-Logik, siehe dort. Der Wartungs-Evidence-Candidate-Pfad
# (`_wartung_kandidat_saetze()`, app/technical_research.py, soeben committed)
# bleibt ebenfalls unverändert bei der alten Distanz-1-Logik -- er ist Teil
# des gerade freigegebenen Wartungs-Evidence-Layers und wird in dieser Runde
# bewusst nicht angefasst, auch wenn er dieselbe TTL-Einschränkung hätte.
#
# STRUKTURAUDIT (§3/§11 Auftrag): echte Tavily-`content`-Snippets liefern so
# gut wie NIE ein doppeltes Newline oder eine Markdown-Überschrift (an über
# 20 real gespeicherten Treffern verschiedener Domains verifiziert, siehe
# Abschlussbericht) -- `_saetze_mit_segmentgrenze()` baut deshalb KEINE neue,
# dort real nicht vorkommende Absatzlogik, sondern macht nur das bestehende
# Signal nutzbar, das `_SATZ` (oben) schon immer als Trenner kennt (es ging
# bisher nur verloren, weil `_saetze()` eine flache Liste ohne Markierung
# zurückgibt). Verteidigung in der Tiefe für den seltenen Fall, dass dieses
# Signal doch vorhanden ist -- NICHT der Regelfall, auf den sich die
# Segment-Logik stützt (das bleibt `_claim_widerspricht_fahrzeug_lokal`).
_ABSATZ_ODER_UEBERSCHRIFT_TRENNER = re.compile(r"\r?\n\s*\r?\n|#{2,}")


def _saetze_mit_segmentgrenze(text: str) -> list[tuple[str, bool]]:
    """Wie `_saetze(text, max_len=None)`, zusätzlich mit einem Flag pro Satz:
    True für den ERSTEN Satz nach einer echten Absatz-/Überschriften-Grenze
    im Originaltext (siehe Kommentar oben). `_saetze()` selbst bleibt für
    alle anderen Aufrufer unverändert -- nur diese Funktion liest zusätzlich
    die Trennerposition mit."""
    out: list[tuple[str, bool]] = []
    for i, chunk in enumerate(_ABSATZ_ODER_UEBERSCHRIFT_TRENNER.split(text or "")):
        for j, s in enumerate(_saetze(chunk, max_len=None)):
            out.append((s, i > 0 and j == 0))
    return out


def _segment_kontext_saetze(satz_liste: list[str], *, marke: str | None, modell: str | None,
                            titel_stark: bool, pruefe_widerspruch_bei_titel_stark: bool = True,
                            segment_marker: list[bool] | None = None,
                            abgelehnt: list[dict] | None = None,
                            url: str | None = None, kategorie: str | None = None
                            ) -> list[str]:
    """Liefert die Sätze EINES Treffers, die lokalen Fahrzeugkontext haben --
    entweder durch eigene Modellnennung oder durch Vererbung innerhalb des
    aktuellen, noch nicht widersprochenen Segments (siehe Moduldocstring
    oben). `kontext_aktiv` ist rein lokal für DIESEN Aufruf/Treffer -- nie
    über den Aufruf hinaus gespeichert, also keine result- oder
    treffer-übergreifende Vererbung.

    `segment_marker[idx] == True` (optional, siehe `_saetze_mit_segmentgrenze`):
    eine ECHTE Absatz-/Überschriften-Grenze im Originaltext liegt direkt vor
    diesem Satz -- das Segment endet dort IMMER (Verteidigung in der Tiefe),
    unabhängig davon, ob der erste Satz des neuen Segments selbst einen
    Widerspruch enthält. Fällt zurück auf den Baseline-Kontext (`titel_stark`
    für stark ausgerichtete Treffer, "kein Kontext" für schwache) -- ein
    titel-starker Treffer darf nach einem Absatzwechsel wieder vom Titel
    profitieren, ein schwacher braucht erneut eine eigene Bestätigung.

    `pruefe_widerspruch_bei_titel_stark=False` (Regression-Fund, Pre-Commit-
    Review Runde 2): die §10-Erweiterung ("auch ein starker Titel wird durch
    einen expliziten Fremdfahrzeug-Satz geschlagen") ist NUR sicher, wenn ein
    echtes Zielmodell bekannt ist -- `_unsichere_satzanfangs_nominalphrase()`
    kann ein unbekanntes Bauteil-Kompositum ("Verdeckmechanismus",
    "Getriebeöl") sonst nie als "das ist mein Zielmodell" ausschließen und
    würde es faelschlich als Fremdfahrzeug werten. Caller setzt dies
    deshalb nur `True`, wenn `kategorie=="schwachstelle"` UND `marke`/
    `modell` tatsächlich gesetzt sind (identische Vorbedingung, die
    `ausgerichtet()` für den schwach-Fall ohnehin schon verlangt) -- "wartung"
    (Wartungs-Evidence-Layer, §15 Auftrag nicht angefasst) bleibt IMMER
    `False` und damit exakt beim alten, ungeprüften Verhalten für titel-
    starke Treffer."""
    kontext_aktiv = titel_stark
    pruefe_widerspruch = (not titel_stark) or pruefe_widerspruch_bei_titel_stark
    grund_blockiert = "fremdfahrzeug_segment" if titel_stark else "schwach_ausgerichtet_ohne_modellnennung"
    # F18-Fix (RC-F-Ursachenanalyse, 2026-10-25): bedingte, NICHT pauschale
    # Rückwärts-Vererbung -- siehe Docstring bei `_rueckwaerts_erbfaehige_indizes()`.
    erbfaehig = _rueckwaerts_erbfaehige_indizes(satz_liste, modell=modell,
                                                segment_marker=segment_marker)
    ergebnis: list[str] = []
    for idx in range(len(satz_liste)):
        if segment_marker and segment_marker[idx]:
            kontext_aktiv = titel_stark
        satz = _kontext_aufloesen(satz_liste, idx)
        if satz is None:
            if abgelehnt is not None:
                abgelehnt.append({"url": url, "kategorie": kategorie,
                                  "grund": "kontextabhaengiger_satz_ohne_bezug",
                                  "satz": satz_liste[idx][:120]})
            continue
        if not pruefe_widerspruch:
            ergebnis.append(satz)
            continue
        if _claim_widerspricht_fahrzeug_lokal(satz, marke, modell):
            # Expliziter Widerspruch schlägt geerbten ODER Titel-Kontext IMMER
            # und beendet das aktuelle Segment -- erst eine erneute explizite
            # Modellnennung darf wieder erben (kein "sticky" Modellbezug über
            # einen Fahrzeugwechsel hinweg, siehe Test F/H).
            kontext_aktiv = False
            if abgelehnt is not None:
                abgelehnt.append({"url": url, "kategorie": kategorie,
                                  "grund": grund_blockiert, "satz": satz[:120]})
            continue
        if _satz_nennt_modell(satz, modell):
            kontext_aktiv = True
            ergebnis.append(satz)
            continue
        if kontext_aktiv:
            log.debug("RC-W4(v2): Claim akzeptiert via Segment-Kontext "
                     "(titel_stark=%s): %r", titel_stark, satz[:120])
            ergebnis.append(satz)
            continue
        if idx in erbfaehig:
            log.debug("F18-Fix: Claim akzeptiert via Rueckwaerts-Kontext "
                     "(naechster Satz nennt GENAU das Zielmodell): %r", satz[:120])
            ergebnis.append(satz)
            continue
        if abgelehnt is not None:
            abgelehnt.append({"url": url, "kategorie": kategorie,
                              "grund": grund_blockiert, "satz": satz[:120]})
    return ergebnis


def _distanz1_kontext_saetze(satz_liste: list[str], *, marke: str | None, modell: str | None,
                             grund: str | None, abgelehnt: list[dict], url: str,
                             kategorie: str) -> list[str]:
    """EXAKTER Verhaltens-Port der ursprünglichen RC-W4-Distanz-1-TTL (Runde 1)
    -- NUR noch für kategorie=="rueckruf" in Gebrauch (Recall/KBA bleibt
    bewusst unangetastet, siehe Moduldocstring oben). schwachstelle/wartung
    nutzen jetzt `_segment_kontext_saetze()`."""
    ergebnis: list[str] = []
    abstand_seit_nennung: int | None = None
    for idx in range(len(satz_liste)):
        satz = _kontext_aufloesen(satz_liste, idx)
        if satz is None:
            abgelehnt.append({"url": url, "kategorie": kategorie,
                              "grund": "kontextabhaengiger_satz_ohne_bezug",
                              "satz": satz_liste[idx][:120]})
            continue
        if grund == "schwach":
            widerspruch = _claim_widerspricht_fahrzeug_lokal(satz, marke, modell)
            if widerspruch:
                abstand_seit_nennung = None
                abgelehnt.append({"url": url, "kategorie": kategorie,
                                  "grund": "schwach_ausgerichtet_ohne_modellnennung",
                                  "satz": satz[:120]})
                continue
            if _satz_nennt_modell(satz, modell):
                abstand_seit_nennung = 0
            else:
                abstand = None if abstand_seit_nennung is None else abstand_seit_nennung + 1
                if abstand is None or abstand > _RC_W4_MAX_KONTEXT_DISTANZ:
                    abstand_seit_nennung = abstand
                    abgelehnt.append({"url": url, "kategorie": kategorie,
                                      "grund": "schwach_ausgerichtet_ohne_modellnennung",
                                      "satz": satz[:120]})
                    continue
                log.debug("RC-W4: Claim akzeptiert via alignment_source=local_context, "
                         "context_distance=%d: %r", abstand, satz[:120])
                abstand_seit_nennung = abstand
        ergebnis.append(satz)
    return ergebnis


# ── RC-W1: Substanz-Schwelle gegen scope-lose Teaser/Index-Seiten ───────────
#
# BEFUND (Forensik-Audit, realer Web-Smoke-Test): eine Kategorie-/Teaser-Seite
# ("Mazda MX-5 ► aktuelle Artikel & Tests") nennt nur die Schlagzeile eines
# Rückrufs ("Kraftstoffleitungs-Rückruf für Mazda MX-5 (ND)"), ohne jede
# Scope-Information. Die reine Schlagzeile wurde bisher trotzdem zu einem
# eigenen "series_only"-Fakt — "fehlende Scope-Information" wurde faktisch wie
# "Scope = gesamte Baureihe" behandelt.
#
# Generische Regel: eine Rückruf-Aussage ohne jede Scope-Information braucht
# mindestens etwas inhaltlichen Überschuss über die reine Kombination aus
# Marke + Modell + Bauteil + dem Rückruf-Vokabular hinaus — sonst ist sie
# strukturell nicht von einer bloßen Schlagzeile zu unterscheiden. Ein
# Mindestmaß von 3 verbleibenden, inhaltstragenden Tokens (kein Fahrzeug-,
# Marken- oder Funktionswort) gilt generisch für JEDE Marke/jedes Bauteil.
#
# RC-W6 (Abschnitt 9): nach `app.recall_event_matching` extrahiert — derselbe
# Event-Gleichheits-Abgleich wird jetzt auch von `app.kaufcheck` fuer die
# KBA-vs-Web-Quellenprioritaet wiederverwendet, statt ihn dort ein zweites
# Mal (und ggf. abweichend) zu implementieren. Lokale Aliase, damit der
# restliche Code dieser Datei unveraendert bleibt.
from app.recall_event_matching import gleiches_rueckruf_ereignis as _gleiches_rueckruf_ereignis
from app.recall_event_matching import inhaltstokens as _inhaltstokens
from app.recall_event_matching import MIN_GLEICHHEIT_TOKENS as _MIN_GLEICHHEIT_TOKENS


# Fortsetzungsauftrag (Rückruf-Doppelzählung, z.B. "Kraftstoffpumpe" UND
# "Pulsationsdämpfer" aus DERSELBEN Quell-URL/Defektkette/Aktion): bisher
# dedupte `_extrahiere_fakten()` Rückrufe AUSSCHLIESSLICH über den
# Bauteil-Vokabularschlüssel (`schluessel`, s.u.) — zwei verschiedene
# Bauteilbegriffe in EINEM Artikel über EINE Rückrufaktion erzeugten zwei
# eigenständige Kandidaten und damit zwei WebFakt/Insight-Objekte.
#
# Fix: dieselbe, bereits bestehende und für den Cross-Source-Vergleich
# genutzte Ereignisgleichheit (`gleiches_rueckruf_ereignis`, konservative
# Mindest-Token-Überlappung) entscheidet jetzt AUCH innerhalb einer Quelle,
# ob ein neuer Bauteil-Treffer in einen bereits bestehenden Kandidaten
# DERSELBEN URL gebündelt wird. Weder "gleiche URL" noch "gleiches Bauteil"
# allein genügt (beide bleiben wie zuvor kein hinreichendes Kriterium) —
# zwei eigenständige Aktionen auf derselben Seite (kein Tokenüberlapp)
# bleiben zwei Kandidaten. Bei Unklarheit (keine Überlappung) wird NICHT
# gebündelt — derselbe konservative Grundsatz wie bei `ausgeschlossene_themen`
# oben. Keine Fahrzeug-/Bauteil-/Kampagnen-Hardcodes.
# Gegenprüfung (Fortsetzungsauftrag, "Rückruf-Bündelung — gezielte Abschluss-
# prüfung"): `gleiches_rueckruf_ereignis` (Token-Überlappung) allein beweist
# KEINE sichere Kampagnenidentität — zwei Meldungen mit stark ähnlichem,
# weitgehend formelhaftem Text, aber EXPLIZIT unterschiedlichen Kampagnen-/
# Referenznummern, kollidierten sonst sowohl über den normalen Bauteil-
# Vokabularschlüssel (derselbe Bauteilbegriff in beiden Sätzen) als auch über
# die neue Ereignisgleichheits-Bündelung (hohe Textüberlappung abzüglich der
# Zahl selbst). Reproduziert: zwei Sätze mit "Referenznummer 12345" bzw.
# "Referenznummer 67890", sonst identischer Formulierung, derselben URL und
# demselben Bauteil ("Kraftstoffpumpe") ergaben fälschlich EINEN Fakt.
# Generische, kleine Regex für die üblichen deutschen Bezeichnungen einer
# Rückruf-/Kampagnenkennung — keine Fahrzeug-/Marken-/Bauteil-Sonderliste.
_RE_RUECKRUF_REFERENZ = re.compile(
    r"(?:referenznummer|r(?:ü|ue)ckrufcode|r(?:ü|ue)ckrufnummer|aktenzeichen|"
    r"kampagnennummer|kampagnen-?nr\.?|vorgangsnummer|ref\.?-?nr\.?)\s*:?\s*"
    r"([A-Za-z0-9][A-Za-z0-9\-/]{1,19})", re.IGNORECASE)


def _rueckruf_referenz(claim: str) -> str | None:
    """Explizit genannte Kampagnen-/Referenznummer eines Rückruf-Claims, falls
    vorhanden — rein lexikalisch, keine Prüfung gegen eine echte Datenbank
    (das bleibt `app/kba_reconciliation.py`/`app/recall_filter.py` vorbehalten,
    hier geht es nur darum, zwei TEXTLICH unterschiedlich referenzierte
    Meldungen nicht stillschweigend zu verschmelzen)."""
    m = _RE_RUECKRUF_REFERENZ.search(claim or "")
    return m.group(1).upper() if m else None


def _rueckruf_kandidat_schluessel(kandidaten: dict, schluessel: str, url: str,
                                  kandidat_tokens: set[str],
                                  referenz: str | None) -> object:
    """Schlüssel für einen Rückruf-Kandidaten DERSELBEN Quell-URL.

    Zwei generische Sperren, BEIDE unabhängig von Fahrzeug-/Bauteil-/
    Kampagnen-Sonderlisten:

      1. Expliziter Referenz-/Kampagnennummer-Konflikt (`_rueckruf_referenz`):
         trägt ein bestehender Kandidat derselben URL eine ANDERE explizite
         Nummer, gilt er NIE als Ziel — auch nicht bei gleichem Bauteil-
         Schlüssel oder hoher Textüberlappung (sonst kollidieren zwei Meldungen
         mit nur einer unterschiedlichen Nummer sonst identischen,
         formelhaften Textes).
      2. Ereignisgleichheit (`gleiches_rueckruf_ereignis`) gilt jetzt AUCH für
         den schlichten Bauteil-Vokabularschlüssel selbst, nicht nur für
         Bündelung über unterschiedliche Bauteilbegriffe: zwei Sätze, die
         ZUFÄLLIG denselben Bauteilbegriff treffen ("Kraftstoffpumpe wird
         undicht" / "Kraftstoffpumpe hat Kabelbruch"), aber inhaltlich zu
         wenig teilen, gelten NICHT automatisch als dieselbe Meldung mehr
         (reproduzierter Befund: stille Kollision, zweiter Claim ohne jeden
         Ablehnungs-Eintrag verschluckt).

    Ohne gültiges Ziel entsteht bei blockiertem `schluessel` ein eigener,
    disambiguierter Schlüssel (Tupel) statt der bisherigen stillen Kollision —
    NIE eine spekulative Zusammenführung bei unklarer Aktionsidentität.

    Die Ereignisgleichheits-Sperre auf dem schlichten `schluessel` greift NUR,
    wenn der bestehende Kandidat bereits SUBSTANZIELLEN Inhalt trägt (mind.
    `MIN_GLEICHHEIT_TOKENS` eigene Tokens in mindestens einem Beitrag) — eine
    knappe Kopf-/Titelzeile ohne jeden inhaltlichen Überschuss (z.B. "Rückruf
    Lexus RC F: Kraftstoffpumpe") hat nichts, dem ein folgender, ausführlicher
    Satz zum selben Bauteil widersprechen könnte, und bündelt wie bisher."""
    basis_blockiert = False
    for key, e in kandidaten.items():
        if not any((t.get("url") or "") == url for t in e.get("treffer") or ()):
            continue
        vorhandene_referenzen = [r for r in (e.get("referenzen") or []) if r]
        konflikt = bool(referenz and vorhandene_referenzen and referenz not in vorhandene_referenzen)
        if konflikt:
            if key == schluessel:
                basis_blockiert = True
            continue
        bestehende_tokens = e.get("ereignis_tokens") or []
        if _gleiches_rueckruf_ereignis(kandidat_tokens, bestehende_tokens):
            return key
        if key == schluessel:
            hat_substanz = any(len(ts) >= _MIN_GLEICHHEIT_TOKENS for ts in bestehende_tokens)
            if hat_substanz:
                basis_blockiert = True
            else:
                return key
    if basis_blockiert:
        return (schluessel, referenz) if referenz else (schluessel, frozenset(kandidat_tokens))
    return schluessel


# RC-W3 (Release-Review, Risiko 3 + Dedupe-Semantik-Nachprüfung): `kandidaten.
# setdefault(schluessel, ...)` dedupte bisher AUSSCHLIESSLICH über die
# Komponente — zwei fachlich UNTERSCHIEDLICHE Risiken derselben Komponente
# ("Die Wasserpumpe ist undicht" UND "die Wasserpumpe hat einen Lagerschaden")
# teilten sich denselben Schlüssel; `setdefault()` verwarf den zweiten Claim
# dadurch stillschweigend (kein Eintrag in `abgelehnt`, einfach verschluckt).
#
# Eine erste Fassung dieses Fixes verwendete dafür `_inhaltstokens()` +
# `_gleiches_rueckruf_ereignis()` wieder — dieselbe Prüfung, die bereits für
# den Rückruf-Cross-Source-Vergleich existiert. Das war FALSCH: diese
# Funktion ist auf längere, scope-tragende Rückruf-Artikelsätze kalibriert
# (`MIN_GLEICHHEIT_TOKENS = 2`, absolute Mindestanzahl). Beweis dafür: "Die
# Wasserpumpe ist undicht." gegen SICH SELBST verglichen bleibt bei genau
# EINEM übrigen Inhaltstoken ("undicht", nach Abzug von Bauteil "Wasserpumpe"
# und Füllwort "ist") — unterhalb der Schwelle. Die Funktion hätte also
# selbst zwei WORTGLEICHE kurze technische Claims als "verschieden" gewertet.
# Technische Schwachstellen-/Wartungs-Einzelsätze sind typischerweise viel
# kürzer als Rückruf-Artikelsätze — ein absoluter Schwellenwert von 2 passt
# hier nicht.
#
# `_gleiches_technisches_risiko()` ersetzt das für Schwachstelle/Wartung
# durch ein GRÖSSENRELATIVES Kriterium (mindestens die Hälfte der kleineren
# Tokenmenge muss übereinstimmen) — funktioniert auch für Ein-Wort-Reste,
# bleibt aber bei Überlappung 0 (komplett andere Aussage) immer bei
# "verschieden". Kein Stemming, keine Ontologie: "undicht" und
# "undichtigkeit" bzw. "verliert Kühlmittel" werden NICHT als dieselbe
# Aussage erkannt (0 gemeinsame Tokens) — bewusst konservativ, siehe
# Abschlussbericht. "rueckruf" bleibt unverändert bei
# `_gleiches_rueckruf_ereignis()`, nicht Teil dieses Fixes.
# Dedupe-Nachprüfung (Wartungs-Matrix, Fall B): ein Wartungssatz ist textuell
# fast immer fast identisch zu einem anderen DESSELBEN Bauteils ("sollte alle
# ... km gewechselt werden") — das WIRKLICH unterscheidende Merkmal ist fast
# nur das Intervall selbst, das als einzelnes Token unter vielen geteilten
# "Boilerplate"-Tokens (sollte/alle/km/gewechselt/werden) in der reinen
# Überlappungsquote untergeht. "alle 60.000 km" vs. "alle 90.000 km" würden
# sonst trotz WIDERSPRÜCHLICHEM Intervall als dasselbe Risiko gelten. Ein
# optionales HARTES Unterscheidungsmerkmal (hier: der exakte Intervalltext)
# erzwingt "verschieden", wenn beide Seiten eines tragen und es abweicht —
# unabhängig von der sonstigen Textüberlappung.
# Adversarialer Stresstest (Release-Review): bei kurzen Claims kann ein
# einzelnes generisches, inhaltsarmes Verb/Wort bereits die Hälfte der
# kleineren Tokenmenge ausmachen und so zwei fachlich UNTERSCHIEDLICHE
# Risiken fälschlich zusammenführen — konkret nachgewiesen für "zeigt" ("W.
# zeigt Verschleiss" vs. "W. zeigt Lagerschaden" -> faelschlich gleich) und
# ebenso fuer "hat"/"haeufig"/"schaden"/"defekt"/"tritt"/"kann". NUR diese
# empirisch nachgewiesenen Wörter werden hier herausgefiltert — bewusst KEINE
# spekulative Erweiterung (z.B. "stark"/"deutlich"/"früh"/"problem" zeigten in
# denselben Tests KEINEN Fehleffekt und bleiben daher drin). Diese Liste gilt
# NUR für diesen Comparator, nicht für `_inhaltstokens()` selbst (Rückruf und
# `_ist_substanziell()` bleiben unverändert).
_TECHNISCHE_DEDUPE_NIEDRIGINFO = frozenset({
    "zeigt", "zeigen", "tritt", "treten", "kann", "koennen",
    "hat", "haben", "schaden", "schaeden", "defekt", "haeufig",
})


# Release-Review (Getriebeöl/Getriebefilter-Kollision): die kanonische
# Oberkomponente (`schluessel`, z.B. "getriebe") ist NICHT dieselbe Identität
# wie das konkrete Wartungs-/Schwachstellenobjekt ("Getriebeöl" vs.
# "Getriebefilter" matchen beide nur "getriebe"). `_anzeige_bauteil()`
# bewahrt diese konkrete Mention bereits korrekt — der alte Fehler war, sie
# anschließend über `_inhaltstokens(..., bauteil=...)` als "kern" WIEDER
# herauszufiltern, bevor der Dedupe-Vergleich sie je sah. Fix: die konkrete
# Mention wird hier als ZWEITES hartes Unterscheidungsmerkmal übergeben
# (zusätzlich zum Wartungsintervall) — unterscheiden sich zwei konkrete
# Mentions, gelten sie als verschieden, UNABHÄNGIG von der sonstigen
# Textüberlappung — genau wie beim Intervall.
#
# Release-Review (Folgefund): eine reine PRÄFIX-Regel ("kürzeres Wort ist
# Anfang des längeren") verwechselt echte Wortformen ("Getriebeöl" vs.
# "Getriebeöls") mit fachlich ANDEREN Komposita, die zufällig mit demselben
# Wort beginnen ("Kühlmittel" vs. "Kühlmittelpumpe", "Getriebeöl" vs.
# "Getriebeölstand") — JEDES längere Kompositum, das mit dem kürzeren Wort
# beginnt, würde sonst als dasselbe Objekt gelten. Ersetzt durch eine
# kleine, geschlossene Flexions-Endungsliste (s/es/en/e/er): zwei Mentions
# gelten nur gleich, wenn nach Abzug HÖCHSTENS einer dieser Endungen
# identische Stämme übrig bleiben — ein Kompositum wie "Kühlmittelpumpe"
# wird dadurch NIE zu "Kühlmittel" (der Unterschied liegt mitten im Wort,
# nicht an einer Flexionsendung). Keine Morphologie-Engine: bleibt eine
# echte Wortform (z.B. Umlaut-Pluralverschiebung "Belag"/"Beläge") dennoch
# ungematcht, gilt bewusst der konservative Grundsatz — lieber ein
# gelegentlicher Doppel-Fakt als ein False Merge.
_OBJEKT_FLEXIONS_ENDUNGEN = ("es", "en", "er", "e", "s")


def _objekt_stammformen(wort: str) -> set[str]:
    formen = {wort}
    for endung in _OBJEKT_FLEXIONS_ENDUNGEN:
        if len(wort) >= len(endung) + 4 and wort.endswith(endung):
            formen.add(wort[:-len(endung)])
    return formen


def _gleiches_objekt_mention(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return True  # keine Mention auf einer Seite -> kein Widerspruch, nicht blockieren
    if a == b:
        return True
    return bool(_objekt_stammformen(a) & _objekt_stammformen(b))


def _gleiches_technisches_risiko(kandidat_tokens: set[str], andere_tokens: set[str], *,
                                 kandidat_merkmal: str | None = None,
                                 andere_merkmal: str | None = None,
                                 kandidat_objekt: str | None = None,
                                 andere_objekt: str | None = None) -> bool:
    if (kandidat_merkmal is not None and andere_merkmal is not None
            and kandidat_merkmal != andere_merkmal):
        return False
    if not _gleiches_objekt_mention(kandidat_objekt, andere_objekt):
        return False
    kandidat_tokens = kandidat_tokens - _TECHNISCHE_DEDUPE_NIEDRIGINFO
    andere_tokens = andere_tokens - _TECHNISCHE_DEDUPE_NIEDRIGINFO
    if not kandidat_tokens or not andere_tokens:
        return kandidat_tokens == andere_tokens
    ueberlappung = len(kandidat_tokens & andere_tokens)
    if ueberlappung == 0:
        return False
    kleinere = min(len(kandidat_tokens), len(andere_tokens))
    return ueberlappung * 2 >= kleinere


def _wartungsintervall_merkmal(claim: str) -> str | None:
    m = _INTERVALL.search(claim)
    return _norm(m.group(0)) if m else None


# Adversarialer Stresstest (Wartungs-Kollisionen C/D): bei GLEICHEM Intervall
# überdeckt das Wartungs-Boilerplate ("sollte alle X km gewechselt werden")
# selbst dann noch einen echten Unterschied, wenn (C) zwei VERSCHIEDENE
# Komponenten denselben groben Vokabular-Schlüssel teilen ("Getriebeöl" UND
# "Getriebefilter" matchen beide nur "getriebe") oder (D) dieselbe Komponente
# bei identischem Intervall eine ANDERE Arbeit beschreibt ("gewechselt" vs.
# "Füllstand geprüft") — die Zahl/Einheit selbst ("60.000", "km") bleibt nach
# Abzug der Füllwörter übrig und dominiert die Überlappung. Fix: für Wartung
# werden zusätzlich die eigenen Wartungs-Füllwörter UND die Tokens des
# bereits separat geprüften Intervalltexts selbst aus dem Vergleich entfernt
# — bewusst konservativ: ein Nebeneffekt ist, dass zwei reine SYNONYME
# ("wechseln"/"erneuern" bei identischem Intervall) jetzt eher als 2 Fakten
# statt 1 enden können. Lieber ein gelegentliches Doppelrisiko als eine
# falsche Fusion zweier echter Unterschiede (siehe Abschlussbericht).
_WARTUNG_DEDUPE_NIEDRIGINFO = frozenset({"sollte", "sollten", "wird", "werden", "alle",
                                        "spaetestens"})


def _wartung_vergleichs_tokens(tokens: set[str], merkmal: str | None) -> set[str]:
    bereinigt = tokens - _WARTUNG_DEDUPE_NIEDRIGINFO
    if merkmal:
        bereinigt = bereinigt - _tokens(merkmal)
    return bereinigt


def _kandidat_schluessel_fuer_risiko(kandidaten: dict, basis_schluessel: str, claim: str,
                                     marke: str | None, modell: str | None,
                                     bauteil: str | None, *,
                                     kategorie: str | None = None) -> tuple:
    kandidat_tokens = _inhaltstokens(claim, marke, modell, bauteil)
    kandidat_merkmal = _wartungsintervall_merkmal(claim) if kategorie == "wartung" else None
    kandidat_objekt = _norm(bauteil) if bauteil else None
    if kategorie == "wartung":
        kandidat_tokens = _wartung_vergleichs_tokens(kandidat_tokens, kandidat_merkmal)
    i = 0
    while True:
        key = (basis_schluessel, i)
        bestehend = kandidaten.get(key)
        if bestehend is None:
            return key
        bestehende_tokens = _inhaltstokens(bestehend["aussage"], marke, modell,
                                           bestehend["bauteil"])
        bestehendes_merkmal = (_wartungsintervall_merkmal(bestehend["aussage"])
                               if kategorie == "wartung" else None)
        bestehendes_objekt = _norm(bestehend["bauteil"]) if bestehend["bauteil"] else None
        if kategorie == "wartung":
            bestehende_tokens = _wartung_vergleichs_tokens(bestehende_tokens, bestehendes_merkmal)
        if _gleiches_technisches_risiko(kandidat_tokens, bestehende_tokens,
                                        kandidat_merkmal=kandidat_merkmal,
                                        andere_merkmal=bestehendes_merkmal,
                                        kandidat_objekt=kandidat_objekt,
                                        andere_objekt=bestehendes_objekt):
            return key
        i += 1


def _ist_substanziell(satz: str, marke: str | None, modell: str | None,
                      bauteil: str | None) -> bool:
    return len(_inhaltstokens(satz, marke, modell, bauteil)) >= 3


def _extrahiere_fakten(treffer: list[dict], kategorie: str, *,
                       marke: str | None = None, modell: str | None = None,
                       baujahr: int | None = None, generation: str | None = None,
                       abgelehnt: list[dict] | None = None, hubraum=None,
                       leistung_ps: int | None = None) -> list[WebFakt]:
    """Strukturierte Fakten aus Snippets — je Bauteil höchstens einer.

    Jeder Treffer muss das Zielfahrzeug betreffen (Entity-Alignment, inkl.
    Generation, falls belegt). Jeder Satz trägt seinen Geltungsbereich:
      * Jahresbereich schließt das Baujahr aus -> Satz verworfen.
      * Jahresbereich deckt das Baujahr -> "covered".
      * vage Eingrenzung ("frühe Getriebe", "vor dem Facelift") -> "unresolved";
        der Fakt bleibt sichtbar, aber ausdrücklich unaufgelöst und nie mit
        Confidence über "niedrig".
      * Rückrufe: Produktionsfenster deckt das Baujahr -> "vehicle_possible",
        ohne Fenster -> "series_only".
    """
    # Root Cause 5: Motoröl-Erweiterung NUR für "wartung" (siehe
    # `_wartung_vokabular()`) — "schwachstelle"/"rueckruf" bleiben exakt beim
    # bisherigen, geteilten `_bauteil_vokabular()`.
    vokabular = _wartung_vokabular() if kategorie == "wartung" else _bauteil_vokabular()
    min_score = _MIN_SCORE_RUECKRUF if kategorie == "rueckruf" else MIN_SCORE_FAKT
    kandidaten: dict[str, dict] = {}
    abgelehnt = abgelehnt if abgelehnt is not None else []

    # RC-W1 (Release-Hardening, Cross-Source-Review): welche Rückruf-Bauteil-
    # Themen wurden durch einen ausgerichteten Artikel mit explizitem Scope-
    # Widerspruch (Baujahr/Hubraum/Leistung) bereits ausgeschlossen? Eine
    # SPÄTERE, scope-lose Quelle zum SELBEN Thema (z.B. eine Kategorie-/
    # Teaser-Seite, die nur die Schlagzeile derselben Meldung zeigt) darf
    # diesen Ausschluss nicht wieder aufheben.
    #
    # Bauteil ALLEIN ist dafür NICHT genug (Review-Fund): zwei völlig
    # unabhängige Kraftstoffleitungs-Rückrufe (anderer Defekt, andere
    # Kampagne) dürfen sich nicht gegenseitig sperren, nur weil sie dasselbe
    # Bauteil nennen. Gespeichert werden deshalb je Bauteil-Schlüssel die
    # INHALTSTRAGENDEN Tokens der ausschliessenden Aussage — der eigentliche
    # Gleichheits-Check (`_gleiches_rueckruf_ereignis`) erfolgt erst am
    # Einsatzort, UND nur für Kandidaten, die selbst keinen eigenen,
    # passenden Scope tragen (`passt is not True` — ein Kandidat mit eigenem
    # Scope-Beleg wird NIE durch eine andere Quelle gesperrt).
    ausgeschlossene_themen: dict[str, list[set[str]]] = {}
    if kategorie == "rueckruf":
        for r in treffer:
            url_x = r.get("url") or ""
            if score_domain(url_x, _WEB_KATEGORIE[kategorie]) < min_score:
                continue
            ok_x, _ = ausgerichtet(r, marke, modell) if (marke or modell) else (True, None)
            if not ok_x:
                continue
            text_x = f"{r.get('title') or ''}. {r.get('content') or ''}"
            passt_x, _grund_x, _fenster_x = _artikel_geltung(text_x, baujahr, hubraum, leistung_ps)
            if passt_x is not False:
                continue
            for satz_x in _saetze(text_x):
                if not any(w in _norm(satz_x) for w in _RUECKRUF_WORTE):
                    continue
                voc_x = [(m, s) for m, s in vokabular.items() if m in _norm(satz_x)][:1]
                muster_x, schluessel_x = voc_x[0] if voc_x else (None, None)
                bauteil_x = (_anzeige_bauteil(satz_x, muster_x) if muster_x
                            else _rueckruf_bauteil(satz_x))
                if not schluessel_x and bauteil_x:
                    schluessel_x = "rr:" + _norm(bauteil_x).replace(" ", "")
                if schluessel_x:
                    ausgeschlossene_themen.setdefault(schluessel_x, []).append(
                        _inhaltstokens(satz_x, marke, modell, bauteil_x))

    for r in treffer:
        url = r.get("url") or ""
        score = _score_domain_fuer_marke(url, _WEB_KATEGORIE[kategorie], marke)
        if score < min_score:
            # RC-W3 (Observability, kein Verhaltenswechsel): bisher stille
            # Verwerfung ganzer Treffer unterhalb der Quellenschwelle.
            abgelehnt.append({"url": url, "kategorie": kategorie, "grund": "source_quality"})
            continue
        ok, grund = ausgerichtet(r, marke, modell) if (marke or modell) else (True, None)
        if not ok:
            abgelehnt.append({"url": url, "kategorie": kategorie, "grund": grund})
            continue
        if _fremde_generation(r, modell, generation):
            abgelehnt.append({"url": url, "kategorie": kategorie, "grund": "andere_generation"})
            continue
        artikel_passt, artikel_fenster = None, None
        if kategorie == "rueckruf":
            artikel_passt, grund_a, artikel_fenster = _artikel_geltung(
                f"{r.get('title') or ''}. {r.get('content') or ''}", baujahr, hubraum, leistung_ps)
            if artikel_passt is False:
                abgelehnt.append({"url": url, "kategorie": kategorie, "grund": grund_a})
                continue
        # RC-W3 (Release-Risiko 1): die obere 300-Zeichen-Grenze von `_saetze()`
        # verwarf einen realistischen, langen Multi-Claim-Satz VOLLSTÄNDIG,
        # bevor die Claim-Segmentierung ihn überhaupt sehen konnte — nur für
        # die Kategorien in `_CLAIM_SEGMENTIERUNG_KATEGORIEN` aufgehoben
        # (`max_len=None`); "rueckruf" behält die Grenze unverändert.
        # RC-W4 Runde 2: Segment-Kontext (schwachstelle/wartung) statt der
        # alten Distanz-1-TTL -- siehe Moduldocstring bei
        # `_segment_kontext_saetze()`. "rueckruf" bleibt bewusst auf dem
        # unveränderten `_distanz1_kontext_saetze()`-Port (Recall/KBA nicht
        # angefasst, §15 Auftrag).
        if kategorie in _CLAIM_SEGMENTIERUNG_KATEGORIEN:
            satz_markiert = _saetze_mit_segmentgrenze(f"{r.get('title') or ''}. {r.get('content') or ''}")
            satz_liste = [s for s, _ in satz_markiert]
            erlaubte_saetze = _segment_kontext_saetze(
                satz_liste, marke=marke, modell=modell, titel_stark=(grund != "schwach"),
                # §10-Erweiterung NUR für schwachstelle MIT echtem Zielmodell
                # -- "wartung" (Wartungs-Evidence-Layer, §15 Auftrag) bleibt
                # beim alten, ungeprüften Verhalten für titel-starke Treffer
                # (siehe Docstring/Regression-Fund bei `_segment_kontext_saetze`).
                pruefe_widerspruch_bei_titel_stark=(kategorie == "schwachstelle"
                                                    and bool(marke or modell)),
                segment_marker=[m for _, m in satz_markiert],
                abgelehnt=abgelehnt, url=url, kategorie=kategorie)
        else:
            # RC-W3 (Release-Risiko 1): die obere 300-Zeichen-Grenze von
            # `_saetze()` verwarf einen realistischen, langen Multi-Claim-
            # Satz VOLLSTÄNDIG, bevor die Claim-Segmentierung ihn überhaupt
            # sehen konnte -- nur für die Kategorien in
            # `_CLAIM_SEGMENTIERUNG_KATEGORIEN` aufgehoben (oben, `max_len=
            # None`); "rueckruf" behält die Grenze unverändert.
            satz_liste = _saetze(f"{r.get('title') or ''}. {r.get('content') or ''}", max_len=300)
            erlaubte_saetze = _distanz1_kontext_saetze(
                satz_liste, marke=marke, modell=modell, grund=grund,
                abgelehnt=abgelehnt, url=url, kategorie=kategorie)
        for satz in erlaubte_saetze:
            # RC-W3 (Claim-Segmentierung): ein Satz kann mehrere, auch
            # gegensätzliche Claims enthalten ("Korrosion ist nicht
            # feststellbar, aber die Dichtung ist undicht."). Jedes Segment
            # wird jetzt UNABHÄNGIG durch die gesamte restliche Prüfkette
            # geschickt — ein benignes Segment verwirft nur sich selbst, ein
            # echtes Segment wird nur für sich selbst akzeptiert, und mehrere
            # echte Segmente desselben Satzes können mehrere Kandidaten
            # erzeugen (vorher strukturell unmöglich: höchstens EIN Kandidat
            # pro Satz). Ohne Trenner liefert `_segmentiere_claims` `[satz]`
            # unverändert — identisches Verhalten zu vorher.
            #
            # Release-Scope (Review-Risiko 2): EXPLIZITE Allowlist statt
            # "!= rueckruf" — eine Negativ-Prüfung hätte jede heutige oder
            # künftige Kategorie automatisch in die neue Claim-Semantik
            # gezogen. "rueckruf" (Package A, bereits freigegeben/CLOSED)
            # bleibt auf dem alten, satzweisen Pfad; jede andere/künftige
            # Kategorie bekommt NICHT automatisch neue Semantik.
            claim_segmente = (_segmentiere_claims(satz)
                              if kategorie in _CLAIM_SEGMENTIERUNG_KATEGORIEN
                              else [satz])
            for claim in claim_segmente:
                if kategorie == "rueckruf":
                    fremde_marke = _fremde_marke_satz(claim, marke, modell)
                    if fremde_marke:
                        abgelehnt.append({"url": url, "kategorie": kategorie,
                                          "grund": f"fremde_marke_satz:{fremde_marke}",
                                          "satz": claim[:120]})
                        continue
                n = _norm(claim)
                if kategorie == "schwachstelle" and _ist_benign_relativiert(claim):
                    abgelehnt.append({"url": url, "kategorie": kategorie,
                                      "grund": "benign_relativiert", "satz": claim[:120]})
                    continue
                if kategorie == "schwachstelle" and not any(w in n for w in _PROBLEM_WORTE):
                    # RC-W3 (struktureller Coverage-Fix): ohne klassisches
                    # Problemwort zählt zusätzlich das kleine, geschlossene
                    # Struktursignal-Set (Material/Zustand, Verschleiss-
                    # Veränderung, "Spiel" mit Erwerbs-Verb) — siehe
                    # `_hat_strukturelles_defektsignal`. Nur interne Diagnose
                    # (log.debug), keine neue, user-sichtbare Ausgabe.
                    if _hat_strukturelles_defektsignal(n, claim):
                        log.debug("Technische Recherche: Claim akzeptiert via "
                                 "structural_damage_signal statt no_problem_signal: %r",
                                 claim[:120])
                    else:
                        abgelehnt.append({"url": url, "kategorie": kategorie,
                                          "grund": "no_problem_signal", "satz": claim[:120]})
                        continue
                if kategorie == "rueckruf" and not any(w in n for w in _RUECKRUF_WORTE):
                    abgelehnt.append({"url": url, "kategorie": kategorie,
                                      "grund": "no_recall_signal", "satz": claim[:120]})
                    continue
                if kategorie == "wartung" and not _INTERVALL.search(claim):
                    abgelehnt.append({"url": url, "kategorie": kategorie,
                                      "grund": "no_maintenance_interval", "satz": claim[:120]})
                    continue
                z = zeitraum(claim) if kategorie != "wartung" else None
                passt = _im_zeitraum(baujahr, z)
                if passt is None and artikel_passt is True:
                    # Das Claim-Segment selbst nennt kein Fenster, der Artikel schon.
                    passt, z = True, artikel_fenster
                if passt is False:
                    abgelehnt.append({"url": url, "kategorie": kategorie, "grund": "baujahr_ausserhalb",
                                      "satz": claim[:120]})
                    continue
                vage = _RE_VAGE.search(claim) if kategorie != "rueckruf" else None
                treffer_voc = [(m, s) for m, s in vokabular.items() if m in n][:1]
                if not treffer_voc and kategorie == "rueckruf":
                    # Rückrufe betreffen oft Teile außerhalb des Prüfplan-Vokabulars
                    # ("Kraftstoffleitung"). Ein Rückruf darf daran nicht verloren
                    # gehen: das Bauteil wird dann aus dem Claim-Segment selbst gelesen.
                    teil = _rueckruf_bauteil(claim)
                    if teil:
                        treffer_voc = [(None, "rr:" + _norm(teil).replace(" ", ""))]
                if not treffer_voc:
                    # RC-W3 (Observability, kein Verhaltenswechsel): bisher stille
                    # Verwerfung, wenn das Claim-Segment zwar ein Problem-/
                    # Rückrufwort bzw. Wartungsintervall enthält, aber kein
                    # bekanntes Bauteil (und, nur bei "rueckruf", auch kein
                    # generischer Bauteil-Fallback).
                    abgelehnt.append({"url": url, "kategorie": kategorie,
                                      "grund": "no_component", "satz": claim[:120]})
                    continue
                for muster, schluessel in treffer_voc:
                    if (kategorie == "rueckruf" and passt is not True
                            and schluessel in ausgeschlossene_themen):
                        bauteil_kandidat = (_anzeige_bauteil(claim, muster) if muster
                                            else _rueckruf_bauteil(claim))
                        kandidat_tokens = _inhaltstokens(claim, marke, modell, bauteil_kandidat)
                        if _gleiches_rueckruf_ereignis(kandidat_tokens, ausgeschlossene_themen[schluessel]):
                            abgelehnt.append({"url": url, "kategorie": kategorie,
                                              "grund": "rueckruf_thema_bereits_ausgeschlossen",
                                              "satz": claim[:120]})
                            continue
                    bauteil_claim = (_anzeige_bauteil(claim, muster) if muster
                                     else _rueckruf_bauteil(claim))
                    kandidat_key = (
                        _kandidat_schluessel_fuer_risiko(kandidaten, schluessel, claim,
                                                         marke, modell, bauteil_claim,
                                                         kategorie=kategorie)
                        if kategorie in _CLAIM_SEGMENTIERUNG_KATEGORIEN else schluessel)
                    ereignis_tokens, referenz = None, None
                    if kategorie == "rueckruf":
                        # Rückruf-Doppelzählung (Fortsetzungsauftrag): ein zweiter
                        # Bauteilbegriff derselben Quell-URL wird nur dann in einen
                        # bestehenden Kandidaten gebündelt, wenn er NACHWEISLICH
                        # dieselbe Aktion beschreibt (siehe
                        # `_rueckruf_kandidat_schluessel` oben) — nicht schon durch
                        # gleiche URL oder gleiches Bauteil, UND NIE bei einer
                        # explizit anderen Kampagnen-/Referenznummer (Gegenprüfung).
                        ereignis_tokens = _inhaltstokens(claim, marke, modell, bauteil_claim)
                        referenz = _rueckruf_referenz(claim)
                        kandidat_key = _rueckruf_kandidat_schluessel(
                            kandidaten, schluessel, url, ereignis_tokens, referenz)
                    eintrag = kandidaten.setdefault(
                        kandidat_key, {"aussage": claim, "bauteil": bauteil_claim,
                                      "treffer": [], "ereignis_tokens": [], "referenzen": [],
                                      "bauteile": [bauteil_claim] if bauteil_claim else [],
                                      "zeitraum": z, "passt": passt, "vage": vage.group(0) if vage else None})
                    if kategorie == "rueckruf":
                        eintrag["ereignis_tokens"].append(ereignis_tokens)
                        if referenz:
                            eintrag["referenzen"].append(referenz)
                        if bauteil_claim and bauteil_claim not in eintrag["bauteile"]:
                            eintrag["bauteile"].append(bauteil_claim)
                    if r not in eintrag["treffer"]:
                        eintrag["treffer"].append(r)
                    if passt is True and eintrag["passt"] is not True:
                        eintrag.update(aussage=claim, zeitraum=z, passt=True, vage=None)
                    break

    fakten: list[WebFakt] = []
    for schluessel, e in kandidaten.items():
        quellen = _quellen_aus(e["treffer"])
        if not quellen:
            continue
        domains = len({_domain_von(q.url or "") for q in quellen})
        bester = max(score_domain(q.url or "") for q in quellen)
        confidence = _confidence_aus_domains(domains, bester)
        # Quellenstufe zählt, nicht nur die Anzahl: eine einzelne amtliche bzw.
        # Herstellerquelle (TIER 1) ist belastbarer als ein einzelner Blog und
        # steht mindestens auf "mittel". Eine vage Eingrenzung senkt unten
        # trotzdem wieder auf "niedrig".
        if confidence == "niedrig" and any(_tier(q.url or "") == 1 for q in quellen):
            confidence = "mittel"
        geltung, bereich = None, None
        if e["zeitraum"] is not None:
            von, bis = e["zeitraum"]
            bereich = f"{von or ''}–{bis or 'heute'}".strip("–")
            geltung = "covered" if e["passt"] else "unresolved"
        if e["vage"] and e["passt"] is not True:
            geltung, bereich = "unresolved", e["vage"]
            confidence = "niedrig"
        scope_text = None
        if kategorie == "rueckruf":
            # Release-Hardening: die zentrale Scope-Policy (recall_filter.rueckruf_scope)
            # darf nicht nur den EINEN gewaehlten Anzeige-Satz (`aussage`) sehen —
            # ein Kraftstoff-/Leistungs-/Motorcode-/Hubraum-Scope kann im Nachbarsatz
            # desselben Artikels stehen (siehe `_artikel_geltung`, dieselbe Ursache).
            # Alle betroffenheits-tragenden Saetze aller beitragenden Treffer bilden
            # deshalb die normalisierte Scope-Grundlage.
            scope_saetze = [s for r in e["treffer"]
                            for s in _saetze(f"{r.get('title') or ''}. {r.get('content') or ''}")
                            if _RE_BETROFFEN.search(s)]
            scope_text = " ".join(scope_saetze)[:2000] or None
            # RC-W1: ohne Scope-Match UND ohne jede Scope-Information (kein
            # betroffenheits-tragender Satz in irgendeiner beitragenden
            # Quelle) ist "series_only" nur gerechtfertigt, wenn die Aussage
            # selbst mehr ist als eine blosse Schlagzeile (Marke+Modell+
            # Bauteil+"Rückruf", ohne jeden inhaltlichen Überschuss). Fehlende
            # Scope-Information ist NICHT dasselbe wie "gesamte Baureihe
            # betroffen" — siehe Moduldocstring `_ist_substanziell`.
            if (e["passt"] is not True and not scope_text
                    and not _ist_substanziell(e["aussage"], marke, modell, e["bauteil"])):
                quelle_url = e["treffer"][0].get("url") if e["treffer"] else None
                abgelehnt.append({"url": quelle_url, "kategorie": kategorie,
                                  "grund": "rueckruf_ohne_substanz", "satz": e["aussage"][:120]})
                continue
            applicability = "vehicle_possible" if e["passt"] is True else "series_only"
        else:
            applicability = None
        # Rückruf-Doppelzählung (Fortsetzungsauftrag): wurden mehrere Bauteil-
        # begriffe derselben Aktion gebündelt (`_rueckruf_kandidat_schluessel`),
        # zeigt EIN WebFakt/Insight beide Begriffe an, statt den zweiten
        # stillschweigend zu verlieren. `e["bauteil"]` (die ERSTE Mention)
        # bleibt unverändert die
        # Grundlage für die interne Substanzprüfung oben.
        bauteil_anzeige = e["bauteil"]
        if kategorie == "rueckruf" and len(e.get("bauteile") or []) > 1:
            bauteil_anzeige = " / ".join(e["bauteile"])
        fakten.append(WebFakt(
            kategorie=kategorie, bauteil=bauteil_anzeige, aussage=e["aussage"],
            confidence=confidence, applicability=applicability, quellen=quellen,
            geltungsbereich=bereich, geltung_fuer_fahrzeug=geltung, scope_text=scope_text,
        ))
    fakten.sort(key=lambda f: ({"hoch": 0, "mittel": 1, "niedrig": 2}[f.confidence],
                               f.bauteil or ""))
    return fakten[:MAX_FAKTEN_JE_KATEGORIE]


_WEB_KATEGORIE = {
    "schwachstelle": KATEGORIE_SCHWACHSTELLEN,
    "rueckruf": KATEGORIE_RUECKRUFE,
    "wartung": KATEGORIE_WARTUNG,
}


# ── Wartungs-Evidence-Schicht (Source-Trust-Fix, Scope: NUR kategorie=="wartung") ──
#
# Forensik-Befund (Audit-Sitzung Wartungs-Quellenvertrauen, s.o.):
# `_extrahiere_fakten()` verwirft JEDEN Treffer mit
# `score_domain(url, kategorie) < MIN_SCORE_FAKT` komplett (grund=
# "source_quality"), BEVOR dessen Inhalt ueberhaupt gelesen wird. Fuer
# "wartung" traf das real Werkstatt-/Teileportale, die im Audit die
# konkretesten motor-/generationsspezifischen Werte lieferten, WAEHREND
# Hersteller-Landingpages (score>=48, PRIMARY) haeufig GAR KEINE modellscharfe
# Zahl nennen. Diese Schicht AENDERT NICHTS an `_extrahiere_fakten()` und
# dessen bestehendem Gate -- Treffer mit score >= MIN_SCORE_FAKT laufen
# UNVERAENDERT durch den bestehenden Pfad. Betroffen sind AUSSCHLIESSLICH
# Treffer UNTERHALB der Schwelle, UND AUSSCHLIESSLICH kategorie=="wartung".
#
# HAERTUNGSRUNDE (Review-Befund): "2 Secondary-Quellen stimmen überein" wurde
# in einer ersten Fassung DIREKT als bestätigter WebFakt behandelt. Das ist zu
# großzügig -- app/evidence.py zeigt JEDEN Eintrag aus `fakten` ungefiltert
# als User-Insight (kategorie="web_wartung", risk_type="maintenance"); es
# gibt dort KEIN Confidence-Gate. Root-Domain-Verschiedenheit beweist zudem
# KEINE echte Unabhängigkeit (zwei Portale können dieselbe Aftermarket-
# Faustregel kopieren). Deshalb jetzt zwei GETRENNTE Konzepte:
#
#   AGREEMENT    -- rein mechanisch: stimmen >=2 unabhängige, intern
#                   konsistente Source-Families im selben Themen-Key überein?
#                   Ergebnis: MATCH (ja) / CONFLICT (widersprechen sich) /
#                   UNVERIFIED (0-1 Family).
#   VERIFICATION -- darf dieser MATCH tatsächlich ein User-sichtbarer,
#                   harter WebFakt werden? Ein reines Secondary/Unknown-
#                   MATCH bleibt CORROBORATED (nur intern/Observability,
#                   NIE in `fakten`), außer mindestens eine zusätzliche
#                   Bedingung greift (siehe `_wartung_verification_status`).
#
# UNKNOWN vs. SECONDARY (§6 Auftrag): "Sonstige Quelle"/"Nachrichten" (per
# `_qualitaets_label`, score_domain) sind NICHT automatisch "secondary" --
# ohne eine neue Domainliste gibt es dafür kein zuverlässiges generisches
# Signal auf Domain-Ebene. Stattdessen wird die SELBSTBESCHREIBUNG der Seite
# geprüft (Titel enthält ein Wartungs-/Service-Wort, z.B. "Inspektion",
# "Wartungsplan") -- ein generisches Textsignal, kein Firmen-Hardcode. Ohne
# dieses Signal bleibt die Quelle UNKNOWN; UNKNOWN+UNKNOWN wird NIE VERIFIED
# (§6), selbst nicht über die Provenienz-/Cross-Tier-Bedingungen unten.

WARTUNG_SOURCE_PRIMARY = "primary"
WARTUNG_SOURCE_STRONG_SECONDARY = "strong_secondary"
WARTUNG_SOURCE_SECONDARY = "secondary"
WARTUNG_SOURCE_UNKNOWN = "unknown"
WARTUNG_SOURCE_COMMUNITY = "community"

WARTUNG_STATUS_UNVERIFIED = "unverified"
WARTUNG_STATUS_CORROBORATED = "corroborated"
WARTUNG_STATUS_CONFLICT = "conflict"
WARTUNG_STATUS_VERIFIED = "verified"
# Adaptive-Evidence-Research (Folgeauftrag, §6 Auftrag): eigener Status,
# NICHT dasselbe wie WARTUNG_STATUS_CORROBORATED oben (das bleibt bewusst
# NIE user-visible -- 2 Families ohne Herstellerbeleg, siehe Docstring
# `_wartung_verification_status`). MULTI_SOURCE verlangt STRENGER >=3
# unabhängige, nicht-COMMUNITY Families mit gleichem Wert UND wird erst
# user-sichtbar, wenn der Aufrufer zusätzlich bestätigt, dass eine gezielte
# Source-Upgrade-Nachrecherche (§5 Auftrag) keinen Herstellerbeleg gefunden
# hat (`erlaube_multi_source=True` -- siehe `_wartung_candidate_fakten`).
# Erzeugt NIE "verified", sondern einen eigenen, schwächeren, als solchen
# gekennzeichneten Fakt (`WebFakt.beleglage="mehrfach_unabhaengig"`).
WARTUNG_STATUS_MULTI_SOURCE = "multi_source"
MIN_FAMILIES_MULTI_SOURCE = 3

# Mapping auf die BESTEHENDEN, bereits produktiv genutzten Labels aus
# `_qualitaets_label()` (app/web_search.py) -- keine neue Domainliste, keine
# Verhaltensaenderung an score_domain()/_qualitaets_label() selbst (§3 Auftrag
# der ersten Runde). "Nachschlagewerk" (= Wikipedia-artige, redaktionell
# gepflegte Nachschlagequelle) zählt als SECONDARY; "Sonstige Quelle" und
# "Nachrichten" (keine erkennbare Fach-/Amtsautorität UND kein redaktionelles
# Nachschlagewerk) werden NICHT pauschal SECONDARY, sondern per Titel-Signal
# geprüft (siehe `_wartung_source_type`).
#
# Reichweiten-Hinweis (Pre-Commit-Review): PRIMARY/STRONG_SECONDARY sind in
# dieser Map vollständigkeitshalber enthalten (sie dokumentieren, wohin die
# BESTEHENDEN Top-Tiers gehören), werden aber von `_wartung_source_type()" in
# der Praxis NIE zurückgegeben -- jeder Aufrufer in diesem Modul prüft vorher
# `score_domain(...) < MIN_SCORE_FAKT`, und genau diese beiden Tiers liegen
# IMMER darüber. Absichtlich nicht entfernt: eine Map ohne sie wäre für
# künftige Leser verwirrend ("wo bleiben Hersteller/Amtlich?").
_WARTUNG_SOURCE_TYPE_MAP = {
    "Amtlich/Prüforganisation": WARTUNG_SOURCE_PRIMARY,
    "Hersteller": WARTUNG_SOURCE_PRIMARY,
    "Fachmedien": WARTUNG_SOURCE_STRONG_SECONDARY,
    "Technik-Hersteller": WARTUNG_SOURCE_STRONG_SECONDARY,
    "Community/Erfahrungsbericht": WARTUNG_SOURCE_COMMUNITY,
    "Nachschlagewerk": WARTUNG_SOURCE_SECONDARY,
}

# Generisches Titel-Signal für "das ist eine Wartungs-/Serviceseite" -- prüft
# die SELBSTBESCHREIBUNG der Seite (ihren Titel), nicht den Domainnamen eines
# einzelnen Anbieters (§6/§13 Auftrag: kein Firmen-Hardcode). Bewusst klein
# und auf Wartungs-/Inspektionsvokabular beschränkt.
_WARTUNG_TITEL_SERVICE_SIGNAL = re.compile(
    r"inspektion|wartung|serviceplan|werkstatt|ölwechsel|oelwechsel|"
    r"ersatzteil|reparatur\b", re.IGNORECASE)


def _wartung_source_type(r: dict) -> str:
    url = r.get("url") or ""
    mapped = _WARTUNG_SOURCE_TYPE_MAP.get(_qualitaets_label(url))
    if mapped:
        return mapped
    if _WARTUNG_TITEL_SERVICE_SIGNAL.search(r.get("title") or ""):
        return WARTUNG_SOURCE_SECONDARY
    return WARTUNG_SOURCE_UNKNOWN


# Zwei-teilige Public-Suffixe, die im deutschsprachigen Kfz-Kontext vorkommen
# -- KEIN vollstaendiger Public-Suffix-Abgleich, nur genug, um z.B.
# "club.autodoc.de" und "autodoc.de" als eine Quellfamilie zu erkennen, ohne
# einen einzelnen Anbieter hart zu kodieren (§8/§13 Auftrag der ersten Runde).
_ZWEITEILIGE_TLD = frozenset({"co.uk", "org.uk", "co.at", "or.at", "co.nz", "com.au"})


def _source_family(url: str) -> str:
    """Registrierbare Root-Domain (eTLD+1-Näherung). WICHTIG (Härtungsrunde,
    §7 Auftrag): `source_family` beweist NUR das Gegenteil seiner Aussage --
    gleiche Family => sicher NICHT unabhängig. Verschiedene Family bedeutet
    NICHT automatisch "nachgewiesen unabhängig" (zwei Portale können dieselbe
    Quelle kopieren) -- siehe `_wartung_verification_status`, wo eine
    Family-Verschiedenheit nur zu CORROBORATED, nie direkt zu VERIFIED führt."""
    dom = _domain_von(url) or ""
    labels = dom.split(".")
    if len(labels) <= 2:
        return dom
    tail2 = ".".join(labels[-2:])
    if tail2 in _ZWEITEILIGE_TLD and len(labels) >= 3:
        return ".".join(labels[-3:])
    return tail2


# Motor-Kennzeichnung (Hubraum + Kraftstoff-/Antriebskürzel) -- bewusst
# geschlossenes, kleines Set gängiger Kürzel. Erkennt NUR eine EXPLIZITE
# Kennzeichnung; ein Claim ohne jede solche Angabe gilt als NICHT
# widersprechend (unresolved), NICHT als Konflikt -- kein Fahrzeug-Hardcode,
# rein lexikalisch.
_MOTOR_KENNZEICHEN = re.compile(
    r"(\d[.,]\d)\s*[- ]?(tdi|tsi|tfsi|fsi|tce|dci|crdi|hdi|cdti|skyactiv-g|skyactiv-d|"
    r"vti|thp|bluehdi|multijet)", re.IGNORECASE)


def _motor_kennzeichen(text: str | None) -> set[str]:
    return {f"{h.replace(',', '.')}:{s.lower()}" for h, s in _MOTOR_KENNZEICHEN.findall(text or "")}


def _motor_widerspricht(claim: str, motor_ziel: str | None) -> bool:
    """True NUR bei einer EXPLIZITEN, anderen Motor-Kennzeichnung im Claim als
    der Zielmotorisierung. Weder Ziel noch Claim mit erkennbarer Kennzeichnung
    -> kein Widerspruch (konservativ, siehe Moduldocstring)."""
    ziel_kennzeichen = _motor_kennzeichen(motor_ziel)
    if not ziel_kennzeichen:
        return False
    claim_kennzeichen = _motor_kennzeichen(claim)
    if not claim_kennzeichen:
        return False
    return not (claim_kennzeichen & ziel_kennzeichen)


# Getriebe-Kennzeichnung (§5 Auftrag, Härtungsrunde): NUR eine explizite
# EXKLUSIVITÄTS-Formulierung ("nur Schaltgetriebe", "ausschließlich DSG")
# gilt als Widerspruch -- eine bloße Erwähnung ("mit DSG erhältlich") ist
# keine Ausschluss-Aussage. Generisch, kein Modell-/Hersteller-Hardcode.
_GETRIEBE_ART = re.compile(r"\b(dsg|automatik|tiptronic|wandlerautomatik|cvt|"
                           r"schaltgetriebe|handschalter)\b", re.IGNORECASE)
_GETRIEBE_AUTOMATIK_WORTE = frozenset({"dsg", "automatik", "tiptronic", "wandlerautomatik", "cvt"})


def _getriebe_art(text: str | None) -> str | None:
    m = _GETRIEBE_ART.search(text or "")
    if not m:
        return None
    return "automatik" if m.group(1).lower() in _GETRIEBE_AUTOMATIK_WORTE else "schalter"


def _getriebe_widerspricht(claim: str, getriebe_ziel: str | None) -> bool:
    ziel_art = _getriebe_art(getriebe_ziel)
    if not ziel_art:
        return False
    nur = re.search(r"\b(?:nur|ausschlie(?:ß|ss)lich)\b[^.;]{0,25}?\b"
                    r"(dsg|automatik|tiptronic|wandlerautomatik|cvt|schaltgetriebe|handschalter)\b",
                    claim or "", re.IGNORECASE)
    if not nur:
        return False
    claim_art = "automatik" if nur.group(1).lower() in _GETRIEBE_AUTOMATIK_WORTE else "schalter"
    return claim_art != ziel_art


# Markt-/Region-Signal (§5 Auftrag): ENFAL zielt implizit auf den deutsch-/
# europäischen Markt -- eine Quelle, die sich EXPLIZIT als US-/Nordamerika-
# spezifisch zu erkennen gibt, wird nicht blind als Beleg für hiesige
# Intervalle verwertet (US-Modelljahre/-Ausstattungen weichen ab, vgl.
# US_QUELLEN_AUSSCHLUSS in app/web_search.py für denselben Gedanken bei
# Domains). Rein lexikalisch auf den Claim-Text selbst, kein Domain-Hardcode.
_US_MARKT_SIGNAL = re.compile(
    r"\bus[- ]?market\b|united states|\busa\b|amerikanisch|nordamerika", re.IGNORECASE)


def _ist_us_markt_claim(claim: str) -> bool:
    return bool(_US_MARKT_SIGNAL.search(claim or ""))


# Service-Regime (§9/§10 Auftrag): LongLife/flexible Intervalle duerfen NICHT
# gegen ein festes Intervall als Widerspruch gewertet werden -- sie bilden
# stattdessen eine EIGENE Gruppe. Ebenso "prüfen" vs. "ersetzen" (`_aktion`):
# unterschiedliche WartungsHANDLUNGEN beim gleichen Intervall sind KEIN
# Widerspruch, sondern zwei unterschiedliche Aussagen -- auch sie bilden
# eigene Gruppen statt gegeneinander verglichen zu werden. Rein lexikalisch;
# ohne Erkennung bleibt "fest"/"unbestimmt" der neutrale Default.
_WARTUNG_REGIME_LONGLIFE = re.compile(r"long\s*life|flexibel|flexible", re.IGNORECASE)
_WARTUNG_AKTION_ERSETZEN = re.compile(r"wechsel|ersetz|tausch|erneuer", re.IGNORECASE)
_WARTUNG_AKTION_PRUEFEN = re.compile(r"pr(?:ü|ue)f|kontroll|inspizier|sichtpr(?:ü|ue)f", re.IGNORECASE)


def _wartung_regime(claim: str) -> str:
    return "longlife" if _WARTUNG_REGIME_LONGLIFE.search(claim or "") else "fest"


def _wartung_aktion(claim: str) -> str:
    if _WARTUNG_AKTION_ERSETZEN.search(claim or ""):
        return "ersetzen"
    if _WARTUNG_AKTION_PRUEFEN.search(claim or ""):
        return "pruefen"
    return "unbestimmt"


# Intervall-Normalisierung (§8 Auftrag, Härtungsrunde): "alle 120.000 km",
# "120000 km" und "120.000 Kilometer" sollen als DASSELBE km-Intervall
# gelten -- der alte strikte Textvergleich (`_wartungsintervall_merkmal`,
# weiterhin UNVERÄNDERT für den bestehenden Schwachstelle/Wartung-Dedupe-Pfad
# in Gebrauch) hätte sie als verschieden behandelt. NUR Ziffern-Trennzeichen
# und "Kilometer"/"km"-Schreibvarianten werden vereinheitlicht -- keine große
# NLP-Engine. "prüfen bei X" vs. "ersetzen bei X" bleiben über `_wartung_aktion`
# (oben) getrennt, nicht über diese Funktion.
_WARTUNG_INTERVALL_ZAHL = re.compile(
    r"(\d{1,3}(?:[.\s]\d{3})*|\d{4,6})\s*(km|kilometer)"
    r"|(\d{1,3})\s*(monate?|jahre?)", re.IGNORECASE)


def _wartung_merkmal_normalisiert(claim: str) -> str | None:
    """Lexus-RC-F-Forensik (Root Cause, §10 Auftrag): ".search()" fand bisher
    NUR das ERSTE Intervall im Claim -- "Motoröl alle 15.000 km oder 12
    Monate" verlor die Monatsangabe stillschweigend. ".finditer()" sammelt
    jetzt ALLE Intervall-Angaben; mehrere werden zu einer deterministischen,
    sortierten (Reihenfolge im Satz darf das Gruppieren nicht beeinflussen),
    deduplizierten Signatur zusammengefasst. Für den (weiterhin häufigsten)
    Fall mit genau EINEM Intervall ist die Ausgabe byte-identisch zu vorher."""
    teile: list[str] = []
    for m in _WARTUNG_INTERVALL_ZAHL.finditer(claim or ""):
        if m.group(1):
            zahl = re.sub(r"[.\s]", "", m.group(1))
            teile.append(f"{zahl}km")
        else:
            einheit = "monate" if m.group(4).lower().startswith("monat") else "jahre"
            teile.append(f"{m.group(3)}{einheit}")
    if not teile:
        return None
    return "+".join(sorted(set(teile)))


# Provenienz-SELBSTAUSKUNFT (§1/§7 Auftrag, Härtungsrunde 3): "laut Hersteller",
# "Original-Herstellerdaten" (real bei repareo.de beobachtet) ist eine reine
# BEHAUPTUNG der Quelle selbst -- kein Nachweis. Getrennt in zwei Signale:
#   provenance_claimed  = Quelle behauptet Herstellerherkunft (Textmuster).
#   provenance_verified = eine konkrete, technisch nachprüfbare Primärquelle
#                         (z.B. verlinktes Herstellerdokument) ist tatsächlich
#                         vorhanden. Es gibt HEUTE keine Infrastruktur, das zu
#                         prüfen -- bleibt deshalb bewusst IMMER False. NUR
#                         `provenance_verified` dürfte Verification beeinflussen;
#                         da es nie True wird, beeinflusst Provenienz die
#                         VERIFIED-Entscheidung aktuell NICHT mehr (nur noch als
#                         Observability-Feld `provenance_claimed` geloggt).
_WARTUNG_PROVENIENZ_HINWEIS = re.compile(
    r"laut\s+hersteller|herstellerangabe|werksvorgabe|laut\s+serviceplan|"
    r"laut\s+betriebsanleitung|laut\s+wartungsplan|offizielle\s+angabe|"
    r"original[- ]?herstellerdaten", re.IGNORECASE)


def _provenienz_behauptet(claim: str) -> bool:
    """`provenance_claimed` -- NUR Observability, beeinflusst KEINE Entscheidung.
    Ein Gegenstück `provenance_verified` existiert bewusst NICHT als Funktion:
    es gibt heute keine Infrastruktur, eine Herkunfts-Selbstauskunft technisch
    zu verifizieren (z.B. ein verlinktes Herstellerdokument gegenzuprüfen) --
    eine Funktion, die nur immer `False` liefert, wäre toter Code. Die Policy-
    Entscheidung selbst steht in `_wartung_verification_status()`: Bedingung B
    (Provenienz hebt CORROBORATED auf VERIFIED) wurde ENTFERNT, nicht nur
    deaktiviert -- siehe dortigen Kommentar."""
    return bool(_WARTUNG_PROVENIENZ_HINWEIS.search(claim or ""))


def _vokabular_match(normalisierter_text: str, vokabular: dict[str, str]) -> tuple[str, str] | None:
    """Liefert (muster, bauteil_schluessel) des ERSTEN passenden Vokabular-
    Eintrags, oder None. Eine Stelle für den Candidate-Loop UND die Cross-
    Tier-Signatur-Ableitung (Pre-Commit-Review: vorher an zwei Stellen
    dasselbe Listen-Comprehension dupliziert)."""
    for muster, schluessel in vokabular.items():
        if muster in normalisierter_text:
            return muster, schluessel
    return None


def _wartung_signature(bauteil_key: str, aktion: str, regime: str, merkmal: str | None
                       ) -> tuple[str, str, str, str] | None:
    """Gemeinsame 'dieselbe Wartungsaussage'-Signatur (§6 Auftrag, Härtungsrunde
    3): Bauteil/Wartungsposten + Aktion (prüfen/ersetzen) + Regime (fest/
    longlife) + normalisiertes Intervall MÜSSEN ALLE VIER übereinstimmen.
    Ein zufälliges Substring-Vorkommen desselben Komponentenbegriffs reicht
    NICHT (§2/§3 Auftrag: "Getriebeöl prüfen" vs. "Getriebeöl wechseln" bei
    gleichem Intervall ist NICHT dieselbe Aussage)."""
    if not merkmal:
        return None
    return (bauteil_key, aktion, regime, merkmal)


def _wartung_signature_aus_text(text: str, vokabular: dict[str, str]
                                ) -> tuple[str, str, str, str] | None:
    """Leitet dieselbe Signatur aus einem rohen Aussagetext ab (für
    Haupt-Pfad-WebFakt-Objekte, die keine strukturierten Felder tragen --
    siehe Moduldocstring §12 Auftrag: keine Model-Migration)."""
    treffer = _vokabular_match(_norm(text), vokabular)
    if not treffer:
        return None
    _, schluessel = treffer
    return _wartung_signature(schluessel, _wartung_aktion(text), _wartung_regime(text),
                              _wartung_merkmal_normalisiert(text))


def _cross_tier_treffer(signatur: tuple, motor_ziel: str | None, getriebe_ziel: str | None,
                        haupt_fakten: list[WebFakt], vokabular: dict[str, str]) -> WebFakt | None:
    """Bedingung A (§2/§5 Auftrag, Härtungsrunde 3): liefert den bereits vom
    BESTEHENDEN, unveränderten Pfad (PRIMARY/STRONG_SECONDARY) erzeugten
    WebFakt, der STRUKTURELL dieselbe `_wartung_signature` trägt (Bauteil UND
    Aktion UND Regime UND Intervall -- nicht nur ein zufälliges Substring-
    Vorkommen), oder `None`. Zusätzlich: die Hauptpfad-Aussage darf der
    Motor-/Getriebe-Zielvorgabe nicht explizit widersprechen --
    `_extrahiere_fakten()` prüft das selbst nicht, deshalb hier als
    zusätzliches, additives Gate (ändert NICHTS an `_extrahiere_fakten()`
    selbst). Gibt den TREFFER-Fakt zurück statt nur `True`/`False`, damit der
    Aufrufer ihn um zusätzliche Candidate-Quellen ANREICHERN kann statt einen
    zweiten, inhaltsgleichen WebFakt zu erzeugen (Pre-Commit-Review §6E:
    kein doppelter Fact durch alten + neuen Pfad)."""
    if not signatur:
        return None
    for f in haupt_fakten:
        if f.kategorie != "wartung":
            continue
        if _motor_widerspricht(f.aussage, motor_ziel):
            continue
        if _getriebe_widerspricht(f.aussage, getriebe_ziel):
            continue
        if _ist_us_markt_claim(f.aussage):
            continue
        if _wartung_signature_aus_text(f.aussage, vokabular) == signatur:
            return f
    return None


def _wartung_kandidat_saetze(r: dict, marke: str | None, modell: str | None,
                             abgelehnt: list[dict]) -> list[str]:
    """Liefert die für Wartungs-Candidates nutzbaren Sätze EINES Treffers --
    dieselbe Entity-Alignment-/RC-W4-Kontext-Logik wie `_extrahiere_fakten()`
    (dupliziert, siehe Moduldocstring oben)."""
    url = r.get("url") or ""
    ok, grund = ausgerichtet(r, marke, modell)
    if not ok:
        abgelehnt.append({"url": url, "kategorie": "wartung", "grund": grund,
                          "source_type": _wartung_source_type(r)})
        return []
    satz_mit_grenze = _saetze_mit_segmentgrenze(f"{r.get('title') or ''}. {r.get('content') or ''}")
    satz_liste = [s for s, _ in satz_mit_grenze]
    segment_marker = [g for _, g in satz_mit_grenze]
    # F18-Fix (RC-F-Ursachenanalyse, 2026-10-25): bedingte, NICHT pauschale
    # Rückwärts-Vererbung -- siehe Docstring bei `_rueckwaerts_erbfaehige_indizes()`.
    # Ein echter Segmentbruch (Absatz/Überschrift) unterbindet die Vererbung
    # genauso wie im Schwachstelle-Pfad (`_segment_kontext_saetze`).
    erbfaehig = _rueckwaerts_erbfaehige_indizes(satz_liste, modell=modell, segment_marker=segment_marker)
    ergebnis: list[str] = []
    abstand_seit_nennung: int | None = None
    for idx in range(len(satz_liste)):
        satz = _kontext_aufloesen(satz_liste, idx)
        if satz is None:
            continue
        # Fortsetzungsauftrag (F18-Konkretisierung): der explizite Fremd-
        # fahrzeug-/Fremdvarianten-Widerspruch (NUR die beiden harten Signale,
        # siehe `_fremdfahrzeug_explizit_lokal`) galt bisher NUR bei
        # "schwacher" Artikel-Ausrichtung. Eine über den Titel STARK
        # ausgerichtete Seite (grund=None) kann im Fließtext trotzdem eine
        # andere Variante nennen ("Beim Lexus RC 300h gilt ... alle 10.000
        # km", im selben Artikel wie die korrekte RC-F-Aussage) -- ohne diese
        # Prüfung zählt dieser Satz allein wegen des Artikeltitels, vergiftet
        # die Quelle als intern inkonsistent und wirft dadurch auch ihre
        # eigene, korrekte RC-F-Aussage weg (reproduzierter Befund). Bewusst
        # NUR der enge Fremdfahrzeug-Check, NICHT die volle
        # `_claim_widerspricht_fahrzeug_lokal` (deren dritter, weicherer
        # Nominalphrasen-Zweig bleibt auf den "schwach"-Pfad beschränkt --
        # siehe dortigen Docstring; sonst False Positives bei gewöhnlichen,
        # der Funktion unbekannten Bauteilwörtern am Satzanfang).
        if _fremdfahrzeug_explizit_lokal(satz, marke, modell):
            abstand_seit_nennung = None
            continue
        if grund == "schwach":
            if _claim_widerspricht_fahrzeug_lokal(satz, marke, modell):
                abstand_seit_nennung = None
                continue
            if _satz_nennt_modell(satz, modell):
                abstand_seit_nennung = 0
            elif idx in erbfaehig:
                pass
            else:
                abstand = None if abstand_seit_nennung is None else abstand_seit_nennung + 1
                if abstand is None or abstand > _RC_W4_MAX_KONTEXT_DISTANZ:
                    abstand_seit_nennung = abstand
                    continue
                abstand_seit_nennung = abstand
        ergebnis.append(satz)
    return ergebnis


def _gruppiere_wartung_kandidaten(kandidaten: list[dict]) -> dict[tuple[str, str, str], list[dict]]:
    gruppen: dict[tuple[str, str, str], list[dict]] = {}
    for k in kandidaten:
        gruppen.setdefault((k["bauteil_key"], k["regime"], k["aktion"]), []).append(k)
    return gruppen


def _wartung_verification_status(gruppe: list[dict], *, motor_ziel: str | None,
                                 getriebe_ziel: str | None, haupt_fakten: list[WebFakt],
                                 vokabular: dict[str, str]
                                 ) -> tuple[str, dict[str, str], list[str], bool, WebFakt | None]:
    """AGREEMENT (mechanisch) getrennt von VERIFICATION (Policy). Rückgabe:
    (status, {family: wert} der zählenden Stimmen, Families mit interner
    Inkonsistenz, provenance_claimed, ziel_fakt -- NUR bei VERIFIED gesetzt,
    s.u.).

    status in (UNVERIFIED, CONFLICT, CORROBORATED, MULTI_SOURCE, VERIFIED):
      UNVERIFIED     0-1 zählende Family.
      CONFLICT       >=2 Families, UNTERSCHIEDLICHE Werte.
      CORROBORATED   2 Families, GLEICHER Wert, aber KEINE Zusatzbedingung
                     erfüllt -- bleibt NICHT user-visible (app/evidence.py
                     zeigt jeden Eintrag aus `fakten` ungefiltert).
      MULTI_SOURCE   >=3 unabhängige, NICHT-COMMUNITY Families, GLEICHER Wert,
                     kein Herstellerbeleg (Adaptive-Evidence-Research, §6
                     Auftrag) -- wird NUR user-visible, wenn der Aufrufer
                     `erlaube_multi_source=True` setzt (erst nach einer
                     gezielten, ergebnislosen Source-Upgrade-Nachrecherche,
                     §5 Auftrag). Erzeugt dann einen EIGENEN, klar als solchen
                     gekennzeichneten Fakt (`beleglage="mehrfach_unabhaengig"`,
                     confidence="mittel") -- NIE "verified".
      VERIFIED       >=2 Families, gleicher Wert, UND dieselbe STRUKTURIERTE
                     `_wartung_signature` ist bereits über den bestehenden
                     PRIMARY/STRONG_SECONDARY-Pfad belegt (Bedingung A) --
                     `ziel_fakt` ist genau DIESER bereits existierende
                     WebFakt (der Aufrufer reichert ihn um die Candidate-
                     Quellen an, statt einen zweiten, inhaltsgleichen
                     WebFakt zu erzeugen; §6E Pre-Commit-Review). NIE, wenn
                     eine beitragende Family UNKNOWN ist (§6 Auftrag Runde
                     2). Härtungsrunde 3 (§1 Auftrag): eine reine Provenienz-
                     SELBSTAUSKUNFT ("laut Hersteller") erzeugt KEIN VERIFIED
                     mehr -- das war zu großzügig (jede Seite kann das
                     behaupten, ohne es zu belegen). Da Bedingung A damit der
                     EINZIGE verbleibende VERIFIED-Pfad ist, kann der
                     Candidate-Pfad aktuell NIE einen komplett neuen, zuvor
                     unbelegten Wartungsfakt sichtbar machen -- nur einen
                     bereits vom Hauptpfad belegten um weitere Quellen
                     anreichern. `provenance_claimed` bleibt nur Logging.
    """
    nicht_community = [k for k in gruppe if k["source_type"] != WARTUNG_SOURCE_COMMUNITY]
    werte_je_family: dict[str, set[str]] = {}
    for k in nicht_community:
        werte_je_family.setdefault(k["family"], set()).add(k["merkmal"])
    inkonsistent = [fam for fam, werte in werte_je_family.items() if len(werte) > 1]
    stimmen = {fam: next(iter(werte)) for fam, werte in werte_je_family.items() if len(werte) == 1}
    distinct_werte = set(stimmen.values())

    if len(distinct_werte) > 1:
        return WARTUNG_STATUS_CONFLICT, stimmen, inkonsistent, False, None
    if len(stimmen) < 2:
        return WARTUNG_STATUS_UNVERIFIED, stimmen, inkonsistent, False, None

    beitraege = [k for k in gruppe if k["family"] in stimmen]
    provenance_claimed = any(_provenienz_behauptet(k["claim"]) for k in beitraege)
    tiers = {k["source_type"] for k in beitraege}
    if WARTUNG_SOURCE_UNKNOWN in tiers:
        # §6 Auftrag Runde 2: UNKNOWN (auch gemischt mit SECONDARY) wird NIE VERIFIED.
        return WARTUNG_STATUS_CORROBORATED, stimmen, inkonsistent, provenance_claimed, None
    signatur = _wartung_signature(gruppe[0]["bauteil_key"], gruppe[0]["aktion"],
                                  gruppe[0]["regime"], next(iter(distinct_werte)))
    ziel_fakt = _cross_tier_treffer(signatur, motor_ziel, getriebe_ziel, haupt_fakten, vokabular)
    if ziel_fakt is not None:
        return WARTUNG_STATUS_VERIFIED, stimmen, inkonsistent, provenance_claimed, ziel_fakt   # Bedingung A
    # Bedingung B (Provenienz) wurde in Härtungsrunde 3 bewusst ENTFERNT statt
    # nur deaktiviert (§1 Auftrag) -- eine reine Selbstauskunft ("laut
    # Hersteller") hebt CORROBORATED nicht mehr auf VERIFIED. `provenance_claimed`
    # bleibt nur ein Logging-Signal (siehe `_provenienz_behauptet()`-Docstring).
    # Adaptive-Evidence-Research (§6 Auftrag): >=3 unabhängige, nicht-COMMUNITY
    # Families mit identischem Wert sind eine STRENGERE Stufe als das einfache
    # 2-Family-CORROBORATED oben -- der Aufrufer entscheidet (via
    # `erlaube_multi_source`), ob daraus bereits ein sichtbarer Fakt wird.
    if len(stimmen) >= MIN_FAMILIES_MULTI_SOURCE:
        return WARTUNG_STATUS_MULTI_SOURCE, stimmen, inkonsistent, provenance_claimed, None
    return WARTUNG_STATUS_CORROBORATED, stimmen, inkonsistent, provenance_claimed, None


def _wartung_candidate_fakten(treffer: list[dict], *, marke: str | None, modell: str | None,
                              generation: str | None, motor: str | None,
                              getriebe: str | None = None,
                              haupt_fakten: list[WebFakt] | None = None,
                              erlaube_multi_source: bool = False,
                              ) -> tuple[list[WebFakt], list[dict], bool]:
    """Candidate-Evidence-Pfad NUR für Treffer UNTERHALB von MIN_SCORE_FAKT
    (siehe Moduldocstring oben). Treffer darüber werden hier NICHT nochmal
    verarbeitet -- die laufen unverändert durch `_extrahiere_fakten()`.
    `haupt_fakten`: die vom BESTEHENDEN Pfad für "wartung" bereits erzeugten
    WebFakt-Objekte -- für die Cross-Tier-Bedingung A gelesen UND (bei
    VERIFIED) direkt per Objekt-Mutation um Candidate-Quellen angereichert
    (siehe unten), nie ersetzt.

    Bedingung A (Cross-Tier, VERIFIED) bleibt unverändert: setzt einen
    bereits in `haupt_fakten` vorhandenen Treffer voraus, der dann
    angereichert statt neu erzeugt wird. NEU (Adaptive-Evidence-Research,
    §6 Auftrag): `erlaube_multi_source=True` erlaubt zusätzlich einen
    EIGENSTÄNDIGEN, aber klar schwächer gekennzeichneten Fakt für Gruppen mit
    >=3 unabhängigen Families (WARTUNG_STATUS_MULTI_SOURCE) OHNE Hersteller-
    beleg -- der Aufrufer (`recherchiere()`) setzt dieses Flag erst, NACHDEM
    eine gezielte Source-Upgrade-Nachrecherche (§5 Auftrag) ergebnislos blieb.
    Mit `erlaube_multi_source=False` (Default, erste Suchrunde) bleibt das
    Verhalten exakt wie bisher: nur Anreicherung, nie ein neuer Fakt.

    Dritter Rückgabewert `eskalierbar`: True, wenn mindestens eine Gruppe
    MULTI_SOURCE- oder CORROBORATED-Stufe erreicht hat, aber (noch) keinen
    sichtbaren Fakt erzeugt hat -- das Signal für den Aufrufer, eine gezielte
    Nachrecherche für GENAU diese Kategorie zu erwägen."""
    haupt_fakten = haupt_fakten or []
    abgelehnt: list[dict] = []
    kandidaten: list[dict] = []
    # Root Cause 5: Motoröl-Erweiterung (siehe `_wartung_vokabular()`) --
    # dieser Pfad ist ohnehin ausschliesslich für kategorie=="wartung".
    vokabular = _wartung_vokabular()

    for r in treffer:
        url = r.get("url") or ""
        if _score_domain_fuer_marke(url, KATEGORIE_WARTUNG, marke) >= MIN_SCORE_FAKT:
            continue   # bestehender Pfad greift bereits -- hier nicht doppelt verarbeiten
        if _fremde_generation(r, modell, generation):
            abgelehnt.append({"url": url, "kategorie": "wartung", "grund": "andere_generation",
                              "source_type": _wartung_source_type(r)})
            continue
        source_type = _wartung_source_type(r)
        family = _source_family(url)
        for satz in _wartung_kandidat_saetze(r, marke, modell, abgelehnt):
            for claim in _segmentiere_claims(satz):
                # Eigene Intervall-Erkennung statt des alten `_INTERVALL` (nur
                # "km", kein "Kilometer") -- siehe `_wartung_merkmal_normalisiert`
                # (§8 Auftrag, Härtungsrunde). `_INTERVALL` bleibt für den
                # BESTEHENDEN Pfad (`_extrahiere_fakten`) unverändert in Nutzung.
                merkmal = _wartung_merkmal_normalisiert(claim)
                if not merkmal:
                    continue
                treffer_voc = _vokabular_match(_norm(claim), vokabular)
                if not treffer_voc:
                    # Root Cause 5/Observability (§12 Auftrag): vorher
                    # stilles Verschwinden ohne jeden Reject-Eintrag, obwohl
                    # ein Wartungsintervall erkannt wurde -- derselbe
                    # Reject-Grund wie im Hauptpfad (`_extrahiere_fakten`),
                    # keine neue Logging-Architektur.
                    abgelehnt.append({"url": url, "kategorie": "wartung", "grund": "no_component",
                                      "satz": claim[:120], "source_type": source_type})
                    continue
                muster, schluessel = treffer_voc
                if _motor_widerspricht(claim, motor):
                    abgelehnt.append({"url": url, "kategorie": "wartung",
                                      "grund": "wartung_motor_widerspricht",
                                      "satz": claim[:120], "source_type": source_type})
                    continue
                if _getriebe_widerspricht(claim, getriebe):
                    abgelehnt.append({"url": url, "kategorie": "wartung",
                                      "grund": "wartung_getriebe_widerspricht",
                                      "satz": claim[:120], "source_type": source_type})
                    continue
                if _ist_us_markt_claim(claim):
                    abgelehnt.append({"url": url, "kategorie": "wartung",
                                      "grund": "wartung_us_markt_claim",
                                      "satz": claim[:120], "source_type": source_type})
                    continue
                kandidaten.append({
                    "bauteil_key": schluessel, "bauteil": _anzeige_bauteil(claim, muster),
                    "regime": _wartung_regime(claim), "aktion": _wartung_aktion(claim),
                    "merkmal": merkmal, "family": family, "source_type": source_type,
                    "url": url, "claim": claim, "treffer": r,
                })

    fakten: list[WebFakt] = []
    eskalierbar = False
    for gruppe in _gruppiere_wartung_kandidaten(kandidaten).values():
        status, stimmen, inkonsistent, provenance_claimed, ziel_fakt = _wartung_verification_status(
            gruppe, motor_ziel=motor, getriebe_ziel=getriebe, haupt_fakten=haupt_fakten,
            vokabular=vokabular)
        for fam in inkonsistent:
            for k in gruppe:
                if k["family"] == fam:
                    abgelehnt.append({"url": k["url"], "kategorie": "wartung",
                                      "grund": "wartung_quelle_intern_inkonsistent",
                                      "satz": k["claim"][:120], "source_type": k["source_type"]})
        if status == WARTUNG_STATUS_CONFLICT:
            for k in gruppe:
                abgelehnt.append({"url": k["url"], "kategorie": "wartung",
                                  "grund": "wartung_candidate_conflict",
                                  "satz": k["claim"][:120], "source_type": k["source_type"]})
        elif status == WARTUNG_STATUS_VERIFIED and ziel_fakt is not None:
            # §6E Pre-Commit-Review: Bedingung A setzt per Definition einen
            # bereits user-sichtbaren Hauptpfad-Fakt voraus (`ziel_fakt`) --
            # ein ZWEITER, inhaltsgleicher WebFakt würde als Duplikat-Insight
            # erscheinen (app/evidence.py hat keine Wartungs-Dedupe-Logik).
            # Stattdessen wird `ziel_fakt` (dieselbe Objektinstanz, die auch
            # im Rückgabewert von `phase_fakten()` steht) um die zusätzlichen,
            # unabhängig bestätigenden Candidate-Quellen angereichert.
            beitraege = [k for k in gruppe if k["family"] in stimmen]
            neue_quellen = _quellen_aus([k["treffer"] for k in beitraege])
            vorhandene_urls = {q.url for q in ziel_fakt.quellen}
            for q in neue_quellen:
                if q.url not in vorhandene_urls:
                    ziel_fakt.quellen.append(q)
                    vorhandene_urls.add(q.url)
        elif status == WARTUNG_STATUS_MULTI_SOURCE and erlaube_multi_source:
            # Adaptive-Evidence-Research (§6 Auftrag): EIGENSTÄNDIGER, aber
            # klar schwächer gekennzeichneter Fakt -- NIE "verified", NIE
            # vermischt mit dem Hauptpfad-Fakt-Mechanismus oben (kein
            # `ziel_fakt`, da keiner existiert -- genau das ist die
            # Voraussetzung für diesen Zweig).
            beitraege = [k for k in gruppe if k["family"] in stimmen]
            fakten.append(WebFakt(
                kategorie="wartung", bauteil=beitraege[0]["bauteil"],
                aussage=beitraege[0]["claim"], confidence="mittel",
                beleglage="mehrfach_unabhaengig",
                quellen=_quellen_aus([k["treffer"] for k in beitraege])))
        elif status == WARTUNG_STATUS_CORROBORATED:
            # Produktentscheidung (Fortsetzungsauftrag, "Gestützte Wartungs-
            # hinweise sichtbar machen"): CORROBORATED wird jetzt IMMER sicht-
            # bar gemacht, NICHT mehr nur intern geloggt -- der Evidenzstatus
            # selbst bleibt dabei unverändert CORROBORATED (eigener
            # `beleglage`-Wert, NIE "verified", NIE vermischt mit dem
            # Hauptpfad-Fakt-Mechanismus oben). Ausnahme bleibt bestehen: eine
            # Gruppe, die NUR über den UNKNOWN-Tier-Fallback in
            # `_wartung_verification_status` (§6 Auftrag Runde 2: "UNKNOWN wird
            # NIE VERIFIED") auf CORROBORATED kam, bleibt UNSICHTBAR -- dieser
            # Schutz galt bereits vor dieser Produktentscheidung und wird hier
            # nicht gelockert (keine Quellenaufwertung einer nicht
            # klassifizierbaren Domain).
            beitraege = [k for k in gruppe if k["family"] in stimmen]
            tiers = {k["source_type"] for k in beitraege}
            if WARTUNG_SOURCE_UNKNOWN in tiers:
                for k in beitraege:
                    abgelehnt.append({"url": k["url"], "kategorie": "wartung",
                                      "grund": "wartung_corroborated_unknown_tier",
                                      "satz": k["claim"][:120], "source_type": k["source_type"],
                                      "bauteil": k["bauteil"], "merkmal": k["merkmal"],
                                      "provenance_claimed": provenance_claimed})
            else:
                fakten.append(WebFakt(
                    kategorie="wartung", bauteil=beitraege[0]["bauteil"],
                    aussage=beitraege[0]["claim"], confidence="niedrig",
                    beleglage="uebereinstimmend_unbestaetigt",
                    quellen=_quellen_aus([k["treffer"] for k in beitraege])))
        elif status == WARTUNG_STATUS_MULTI_SOURCE:
            # MULTI_SOURCE (>=3 Families) bleibt NUR dann (noch) nicht sichtbar,
            # wenn der Aufrufer die Source-Upgrade-Nachrecherche noch nicht
            # versucht/erlaubt hat -- `eskalierbar=True` signalisiert genau
            # diesen Fall an den Aufrufer (§1/§6 Auftrag). Unveraendert.
            eskalierbar = True
            for k in gruppe:
                if k["family"] in stimmen:
                    abgelehnt.append({"url": k["url"], "kategorie": "wartung",
                                      "grund": "wartung_multi_source_pending_upgrade",
                                      "satz": k["claim"][:120],
                                      "source_type": k["source_type"], "bauteil": k["bauteil"],
                                      "merkmal": k["merkmal"],
                                      "provenance_claimed": provenance_claimed})
        else:
            for k in gruppe:
                abgelehnt.append({"url": k["url"], "kategorie": "wartung",
                                  "grund": "wartung_candidate_unverified",
                                  "satz": k["claim"][:120], "source_type": k["source_type"]})

    log.debug("Wartungs-Evidence: %d Candidate-Claims, %d verified, %d Themen-Gruppen, "
             "source_type_mix=%s", len(kandidaten), len(fakten),
             len({(k["bauteil_key"], k["regime"], k["aktion"]) for k in kandidaten}),
             {t: sum(1 for k in kandidaten if k["source_type"] == t)
              for t in (WARTUNG_SOURCE_SECONDARY, WARTUNG_SOURCE_UNKNOWN, WARTUNG_SOURCE_COMMUNITY)})
    return fakten, abgelehnt, eskalierbar


# ── Weakness-Claim-Signatur (Adaptive-Evidence-Research, Folgeauftrag §B) ──
#
# Befund (Auftrag): "gleicher Claim" war bisher ausschließlich über die
# Bauteil-Gruppierung (`bauteil_key`) approximiert -- "Getriebe ruckelt kalt",
# "Getriebe verliert Öl" und "Getriebelager verschleißt" landen alle unter
# demselben Bauteil ("Getriebe"), sind aber KEINE dieselbe Schwachstelle. Eine
# generische, kleine Fehler-/Symptomtyp-Klassifikation -- analog zu
# `_wartung_aktion()` oben (derselbe Musterregex-Ansatz, kein LLM-Call) --
# ergänzt `bauteil_key` um eine zweite Achse. Bewusst klein, bewusst NICHT
# erschöpfend: wenn der Fehlertyp eines Claims nicht sicher bestimmbar ist,
# gibt die Funktion `None` zurück -- der Aufrufer darf einen solchen Claim
# dann mit KEINEM anderen zusammenführen (eigene, isolierte Gruppe), statt
# ihn blind dem Bauteil zuzuordnen (§ Auftrag: "Wenn Claim-Gleichheit nicht
# zuverlässig bestimmbar ist: NICHT zusammenführen. UNKNOWN/separate
# Candidates ist besser.").
_SCHWACHSTELLE_FAILURE_UNDICHT = re.compile(
    r"undicht|leck\w*|verliert\s+(?:\w+\s+){0,2}(?:oel|öl|wasser|fluessigkeit|flüssigkeit|kuehlmittel|kühlmittel)|"
    r"(?:oel|öl)verlust|austritt", re.IGNORECASE)
_SCHWACHSTELLE_FAILURE_VERSCHLEISS = re.compile(
    r"verschlei(?:ss|ß)\w*|abnutzung|ausgeschlagen|vorzeitig\w*\s+verschlei", re.IGNORECASE)
_SCHWACHSTELLE_FAILURE_GERAEUSCH = re.compile(
    r"ger(?:a|ä)usch\w*|klapper\w*|rassel\w*|knarz\w*|pfeif\w*|brumm\w*|quietsch\w*", re.IGNORECASE)
_SCHWACHSTELLE_FAILURE_AUSFALL = re.compile(
    r"ausfall|totalausfall|funktioniert\s+nicht|kaputt|bricht\b|bruch\b|versagt\w*", re.IGNORECASE)
_SCHWACHSTELLE_FAILURE_VIBRATION = re.compile(
    r"ruckel\w*|vibrat\w*|rupf\w*|zuckt|stottert|rucken\b", re.IGNORECASE)
_SCHWACHSTELLE_FAILURE_ELEKTRISCH = re.compile(
    r"fehlercode\w*|steuerger(?:a|ä)t\w*\s*(?:defekt|ausfall|problem)|sensor\w*\s*(?:defekt|ausfall)|"
    r"elektrisch\w*\s*(?:defekt|ausfall|problem)", re.IGNORECASE)
_SCHWACHSTELLE_FAILURE_KORROSION = re.compile(r"korrosion|rost\w*|durchrostung", re.IGNORECASE)
# Reihenfolge relevant: spezifischere Muster vor generischen (z.B. Korrosion
# vor einem generischen "Ausfall"-Treffer im selben Satz).
_SCHWACHSTELLE_FAILURE_MODES: tuple[tuple[str, re.Pattern], ...] = (
    ("korrosion", _SCHWACHSTELLE_FAILURE_KORROSION),
    ("undicht", _SCHWACHSTELLE_FAILURE_UNDICHT),
    ("geraeusch", _SCHWACHSTELLE_FAILURE_GERAEUSCH),
    ("vibration", _SCHWACHSTELLE_FAILURE_VIBRATION),
    ("elektrisch", _SCHWACHSTELLE_FAILURE_ELEKTRISCH),
    ("verschleiss", _SCHWACHSTELLE_FAILURE_VERSCHLEISS),
    ("ausfall", _SCHWACHSTELLE_FAILURE_AUSFALL),
)


def _schwachstelle_failure_mode(claim: str) -> str | None:
    """Grobe, generische Fehler-/Symptomtyp-Klassifikation EINES Claim-Satzes.
    `None`, wenn kein bekanntes Muster sicher zutrifft -- das ist ein
    legitimes, HÄUFIGES Ergebnis (kein Fallback auf "irgendein Typ"), und der
    Aufrufer behandelt `None` als "nicht zusammenführbar" (s. Docstring
    oben), nie als Wildcard, die zu allem passt."""
    if not claim:
        return None
    for name, pattern in _SCHWACHSTELLE_FAILURE_MODES:
        if pattern.search(claim):
            return name
    return None


# ── Schwachstellen-Evidence-Schicht (Package B, Forensik-Folgeauftrag) ─────
#
# Production-Befund (Lexus RC F): 6 von 8 kuratierten "schwachstelle"-Treffern
# scheiterten an `score_domain(...) < MIN_SCORE_FAKT` -- VOR jeder Claim-
# Bewertung (Faktenphase-Log: "source_trust: 2 bestanden/6 verworfen"). Analog
# zur bereits bestehenden Wartungs-Evidence-Schicht oben (nicht neu erfunden,
# bewusst klein): ein niedriger Domain-Score vernichtet einen potenziell
# relevanten Treffer nicht mehr automatisch, BEVOR sein Claim ueberhaupt
# gelesen wurde. Treffer mit Score >= MIN_SCORE_FAKT werden hier NICHT
# nochmal verarbeitet -- die laufen unveraendert durch `_extrahiere_fakten()`.
#
# DIESELBE Sicherheitsregel wie bei Wartung, bewusst NICHT aufgeweicht: ein
# Low-Trust-Candidate (Forenbeitrag, unbekannte Quelle) kann NIEMALS allein --
# auch nicht durch mehrere uebereinstimmende Low-Trust-Quellen -- eine NEUE,
# unabhaengig sichtbare "typische Schwachstelle" erzeugen. Er kann
# AUSSCHLIESSLICH einen bereits im Hauptpfad (Score >= MIN_SCORE_FAKT)
# entstandenen, user-sichtbaren WebFakt DESSELBEN Bauteils um zusaetzliche,
# unabhaengig bestaetigende Quellen anreichern (reine Beleg-/Transparenz-
# Staerkung) -- derselbe Objekt-Mutations-Mechanismus wie bei Wartung
# (`ziel_fakt.quellen.append(...)`), nie ein zweiter, inhaltsgleicher
# WebFakt. Ohne einen solchen Hauptpfad-Anker bleibt ein Candidate IMMER nur
# Observability (`abgelehnt`), nie ein eigenstaendiger Fakt.
def _schwachstelle_candidate_fakten(treffer: list[dict], *, marke: str | None, modell: str | None,
                                    generation: str | None,
                                    haupt_fakten: list[WebFakt] | None = None,
                                    erlaube_multi_source: bool = False,
                                    ) -> tuple[list[WebFakt], list[dict], bool]:
    """Candidate-Evidence-Pfad NUR für Treffer UNTERHALB von MIN_SCORE_FAKT
    (siehe Modulkommentar oben). Mit `erlaube_multi_source=False` (Default,
    erste Suchrunde) unverändert: ein Candidate kann nur ein bestehendes
    `haupt_fakten`-Objekt per Mutation anreichern, nie selbst einen neuen
    WebFakt erzeugen.

    NEU (Adaptive-Evidence-Research, §6 Auftrag): mit `erlaube_multi_source=True`
    (erst nach ergebnisloser Source-Upgrade-Nachrecherche, §5 Auftrag) darf
    eine Gruppe OHNE Hauptpfad-Anker trotzdem einen EIGENSTÄNDIGEN, klar
    schwächeren Fakt erzeugen, wenn >=3 unabhängige, nicht-COMMUNITY Families
    (`MIN_FAMILIES_MULTI_SOURCE`) denselben Bauteil-Claim unabhängig
    berichten (`beleglage="mehrfach_unabhaengig"`, confidence="mittel", NIE
    "verified" -- dieselbe Vorsicht, nur eine Stufe schwächer als Bedingung A
    bei Wartung). Eine einzelne COMMUNITY-Quelle (Forenbeitrag) zählt dabei
    NIE als eigene Family und kann diese Schwelle folglich nie allein
    erreichen (§6 Auftrag: "keine Einzelanekdote dominiert").

    Dritter Rückgabewert `eskalierbar`: True, wenn mindestens eine Gruppe
    >=2 unabhängige Families erreicht, aber (noch) keinen sichtbaren Fakt
    erzeugt hat -- Signal für den Aufrufer, gezielt nachzurecherchieren."""
    haupt_fakten = haupt_fakten or []
    abgelehnt: list[dict] = []
    kandidaten: list[dict] = []
    vokabular = _bauteil_vokabular()

    for r in treffer:
        url = r.get("url") or ""
        if _score_domain_fuer_marke(url, KATEGORIE_SCHWACHSTELLEN, marke) >= MIN_SCORE_FAKT:
            continue   # bestehender Pfad greift bereits -- hier nicht doppelt verarbeiten
        ok, grund = ausgerichtet(r, marke, modell)
        if not ok:
            abgelehnt.append({"url": url, "kategorie": "schwachstelle", "grund": grund})
            continue
        if _fremde_generation(r, modell, generation):
            abgelehnt.append({"url": url, "kategorie": "schwachstelle", "grund": "andere_generation"})
            continue
        family = _source_family(url)
        satz_liste = _saetze(f"{r.get('title') or ''}. {r.get('content') or ''}", max_len=None)
        lokale = _segment_kontext_saetze(
            satz_liste, marke=marke, modell=modell, titel_stark=(grund != "schwach"),
            abgelehnt=abgelehnt, url=url, kategorie="schwachstelle")
        for satz in lokale:
            for claim in _segmentiere_claims(satz):
                n = _norm(claim)
                if _ist_benign_relativiert(claim):
                    abgelehnt.append({"url": url, "kategorie": "schwachstelle",
                                      "grund": "benign_relativiert", "satz": claim[:120]})
                    continue
                if not any(w in n for w in _PROBLEM_WORTE) and not _hat_strukturelles_defektsignal(n, claim):
                    abgelehnt.append({"url": url, "kategorie": "schwachstelle",
                                      "grund": "no_problem_signal", "satz": claim[:120]})
                    continue
                treffer_voc = [(m, s) for m, s in vokabular.items() if m in n][:1]
                if not treffer_voc:
                    abgelehnt.append({"url": url, "kategorie": "schwachstelle",
                                      "grund": "no_component", "satz": claim[:120]})
                    continue
                muster, schluessel = treffer_voc[0]
                kandidaten.append({"bauteil_key": schluessel, "bauteil": _anzeige_bauteil(claim, muster),
                                   "failure_mode": _schwachstelle_failure_mode(claim),
                                   "family": family, "source_type": _wartung_source_type(r),
                                   "url": url, "claim": claim, "treffer": r})

    # Weakness-Claim-Signatur (Folgeauftrag §B): Gruppierung über
    # (bauteil_key, failure_mode) statt nur bauteil_key -- "Getriebe ruckelt
    # kalt"/"Getriebe verliert Öl"/"Getriebelager verschleißt" bilden damit
    # DREI getrennte Gruppen, nie eine. Ein Claim OHNE sicher bestimmbaren
    # Fehlertyp (`failure_mode is None`) bekommt eine EINDEUTIGE, nie
    # kollidierende Gruppe für sich allein (per-Kandidat, nicht per-Bauteil)
    # -- er wird dadurch NIE mit einem anderen Claim zusammengeführt, auch
    # nicht mit einem anderen ebenfalls unbestimmten (§ Auftrag: "Wenn
    # Claim-Gleichheit nicht zuverlässig bestimmbar ist: NICHT
    # zusammenführen").
    gruppen: dict[tuple, list[dict]] = {}
    for idx, k in enumerate(kandidaten):
        key = (k["bauteil_key"], k["failure_mode"]) if k["failure_mode"] else (k["bauteil_key"], f"_unbestimmt_{idx}")
        gruppen.setdefault(key, []).append(k)

    fakten: list[WebFakt] = []
    eskalierbar = False
    for gruppe in gruppen.values():
        bauteil_text = gruppe[0]["bauteil"]
        failure_mode = gruppe[0]["failure_mode"]
        # Hauptpfad-Anker (Bedingung A für Schwachstellen): bauteilgleich
        # ALLEIN reicht nicht mehr (§ Auftrag, dieselbe Sicherheitslücke wie
        # bei der Multi-Source-Gruppierung) -- wenn der Hauptpfad-Fakt selbst
        # einen sicher bestimmbaren Fehlertyp trägt UND der Candidate seinen
        # eigenen ebenfalls sicher bestimmt hat, müssen beide übereinstimmen.
        # Ist der Fehlertyp auf EINER Seite nicht bestimmbar, bleibt das
        # bisherige, bauteilbasierte Verhalten erhalten (kein Rückschritt für
        # den unveränderten Bestandsfall).
        ziel_fakt = next((f for f in haupt_fakten
                          if f.kategorie == "schwachstelle" and f.bauteil == bauteil_text
                          and not (failure_mode and _schwachstelle_failure_mode(f.aussage)
                                   and _schwachstelle_failure_mode(f.aussage) != failure_mode)),
                         None)
        if ziel_fakt is not None:
            neue_quellen = _quellen_aus([k["treffer"] for k in gruppe])
            vorhandene_urls = {q.url for q in ziel_fakt.quellen}
            for q in neue_quellen:
                if q.url not in vorhandene_urls:
                    ziel_fakt.quellen.append(q)
                    vorhandene_urls.add(q.url)
            continue
        # Kein Hauptpfad-Anker. Adaptive-Evidence-Research (§6 Auftrag):
        # erst prüfen, ob >=3 unabhängige, nicht-COMMUNITY Families denselben
        # Bauteil-Claim unabhängig berichten -- eine einzelne COMMUNITY-Quelle
        # zählt NIE als eigene Family (s. Docstring). Dieselbe, bei Wartung
        # bereits etablierte Kategorie-Mindestqualität (§6 Auftrag "Kategorie-
        # spezifische Mindestqualität"): UNKNOWN (nicht klassifizierbare
        # Quelle) zählt ebenfalls nicht als eigenständige Family.
        nicht_community = [k for k in gruppe
                          if k["source_type"] not in (WARTUNG_SOURCE_COMMUNITY, WARTUNG_SOURCE_UNKNOWN)]
        families = {k["family"] for k in nicht_community}
        if len(families) >= MIN_FAMILIES_MULTI_SOURCE and erlaube_multi_source:
            beitraege = [k for k in nicht_community if k["family"] in families]
            fakten.append(WebFakt(
                kategorie="schwachstelle", bauteil=bauteil_text, aussage=beitraege[0]["claim"],
                confidence="mittel", beleglage="mehrfach_unabhaengig",
                quellen=_quellen_aus([k["treffer"] for k in beitraege])))
            continue
        if len(families) >= 2:
            # Nahe an/über der Schwelle, aber (noch) nicht erlaubt bzw. nicht
            # genug Families -- Observability-Signal fuer eine gezielte
            # Source-Upgrade-Nachrecherche (§1/§5/§6 Auftrag), bleibt aber
            # (noch) nicht sichtbar.
            eskalierbar = True
            for k in gruppe:
                abgelehnt.append({"url": k["url"], "kategorie": "schwachstelle",
                                  "grund": "schwachstelle_multi_source_pending_upgrade",
                                  "satz": k["claim"][:120], "source_type": k["source_type"],
                                  "bauteil": bauteil_text, "failure_mode": failure_mode})
            continue
        for k in gruppe:
            abgelehnt.append({"url": k["url"], "kategorie": "schwachstelle",
                              "grund": "schwachstelle_candidate_ohne_hauptpfad_anker",
                              "satz": k["claim"][:120], "source_type": k["source_type"]})
    return fakten, abgelehnt, eskalierbar


# ── Gemeinsame, phasenweise Auswertung ───────────────────────────────────────

def _ziel(marke, modell, baujahr, motor, ziel: dict | None) -> dict:
    # Lexus-RC-F-Forensik (Root Cause 2/3, CONFIRMED) + Package-A-Hardening
    # (Folgeauftrag): ein vom Nutzer ins `modell`-Feld getippter Generations-/
    # Chassiscode ("RC F USC10") wird hier EINMALIG, zentral abgetrennt (siehe
    # `parse_core_model`) — danach arbeitet die GESAMTE Recherche (Queries,
    # Alignment, Fakten-Extraktion) einheitlich mit dem Kern-Modellnamen, ohne
    # dass jede einzelne Aufrufstelle das selbst wissen müsste.
    #
    # DREI STUFEN, bewusst nicht mehr vermischt:
    #   CORE MODEL           z["modell"] -- einzige Pflichtangabe fuer das
    #                        harte Alignment (`ausgerichtet()`).
    #   AUXILIARY CANDIDATE  z["auxiliary_code"] -- rein formbasiert abge-
    #                        trennt, OHNE jede Aussagekraft. Darf Queries
    #                        anreichern (s. Aufrufer in `recherchiere()`),
    #                        darf NIEMALS allein eine Generation bestaetigen
    #                        und darf NIEMALS `modell_fehlt` ausloesen.
    #   CONFIRMED GENERATION z["generation"] -- NUR, wenn der Aufrufer sie
    #                        explizit uebergibt (z.B. eine bereits amtlich/
    #                        per DB bekannte Baureihen-Generation). Der
    #                        Kandidat wird hier NICHT mehr automatisch
    #                        hochgestuft (Befund: das liess einen reinen
    #                        Trimnamen wie "GT3"/"R32" bei jeder Quelle, die
    #                        das Fahrzeug nur beim Namen nennt, sofort als
    #                        "bestaetigte Generation" durchgehen UND liess
    #                        `_fremde_generation()` Quellen mit der
    #                        tatsaechlichen, korrekten Generation faelschlich
    #                        als "andere Generation" verwerfen).
    # Der Kandidat durchlaeuft weiterhin die BESTEHENDE, unveraenderte
    # Bestaetigungs-/Konflikt-Logik (`_generationscodes()`/
    # `_generationen_pruefen()`/`konsens()`, siehe deren Docstrings) — nur
    # ohne den frueheren Freifahrschein eines bloßen String-Treffers.
    kern_modell, code_kandidat = parse_core_model(modell)
    z = {"marke": marke, "modell": kern_modell, "baujahr": baujahr, "motor": (motor or "").strip() or None}
    z.update({k: v for k, v in (ziel or {}).items() if v is not None})
    if not z.get("auxiliary_code") and code_kandidat:
        z["auxiliary_code"] = code_kandidat
    if not z.get("leistung_ps") and motor:
        m = _RE_PS.search(motor)
        z["leistung_ps"] = int(m.group(1)) if m else None
    if not z.get("hubraum") and motor:
        m = re.search(r"\b(\d[.,]\d)\b", motor)
        z["hubraum"] = m.group(1).replace(",", ".") if m else None
    return z


def phase_identitaet(treffer: list[dict], ziel: dict) -> tuple[WebVehicleIdentity, list[dict]]:
    return werte_identitaet_aus(treffer, ziel)


def phase_fakten(roh: dict[str, list[dict]], ziel: dict, identitaet: WebVehicleIdentity,
                 kategorien: tuple[str, ...], abgelehnt: list[dict],
                 *, stats: dict[str, dict] | None = None,
                 erlaube_multi_source: frozenset[str] = frozenset()) -> list[WebFakt]:
    """`stats` (optional, Fact-Phase-Observability, Folgeauftrag): wenn
    übergeben, trägt diese Funktion je Kategorie `{"roh": N, "curated": M}`
    ein — rein additiv, ändert nichts an Auswahl/Extraktion selbst. Rückwärts-
    kompatibel: bestehende Aufrufer (u.a. Tests, `FixtureTechnicalResearchProvider`)
    lassen `stats` weg und verhalten sich byte-identisch wie vorher.

    `erlaube_multi_source` (Adaptive-Evidence-Research, §6 Auftrag): Menge
    der Kategorien ("schwachstelle"/"wartung"), für die eine vorherige,
    gezielte Source-Upgrade-Nachrecherche (§5 Auftrag) bereits ergebnislos
    lief -- NUR für diese Kategorien dürfen die Candidate-Evidence-Schichten
    einen eigenständigen MULTI_SOURCE-Fakt erzeugen (s. dort). Leer (Default)
    == bisheriges Verhalten, byte-identisch."""
    fakten: list[WebFakt] = []
    eskalierbar: dict[str, bool] = {}
    for kategorie in kategorien:
        roh_kategorie = roh.get(kategorie) or []
        treffer = curate_results(roh_kategorie,
                                 kategorie=_WEB_KATEGORIE[kategorie], max_results=8)
        if stats is not None:
            stats[kategorie] = {"roh": len(roh_kategorie), "curated": len(treffer)}
        fakten += _extrahiere_fakten(treffer, kategorie, marke=ziel.get("marke"),
                                     modell=ziel.get("modell"), baujahr=ziel.get("baujahr"),
                                     generation=identitaet.generation or ziel.get("generation"),
                                     abgelehnt=abgelehnt, hubraum=ziel.get("hubraum"),
                                     leistung_ps=ziel.get("leistung_ps"))
        if kategorie == "wartung":
            # Source-Trust-Fix (Scope: NUR wartung) -- siehe Moduldocstring
            # oben. Rein additiv: verarbeitet ausschliesslich Treffer, die
            # `_extrahiere_fakten()` bereits (unveraendert) verworfen hat.
            cand_fakten, cand_abgelehnt, cand_eskalierbar = _wartung_candidate_fakten(
                treffer, marke=ziel.get("marke"), modell=ziel.get("modell"),
                generation=identitaet.generation or ziel.get("generation"),
                motor=ziel.get("motor"), getriebe=ziel.get("getriebe"),
                haupt_fakten=[f for f in fakten if f.kategorie == "wartung"],
                erlaube_multi_source="wartung" in erlaube_multi_source)
            bisherige_wartung = sum(1 for f in fakten if f.kategorie == "wartung")
            fakten += cand_fakten[:max(0, MAX_FAKTEN_JE_KATEGORIE - bisherige_wartung)]
            abgelehnt += cand_abgelehnt
            eskalierbar["wartung"] = cand_eskalierbar
        elif kategorie == "schwachstelle":
            # Package B (Forensik-Folgeauftrag), analog zur Wartungs-Evidence-
            # Schicht oben: rein additiv, verarbeitet ausschliesslich Treffer,
            # die `_extrahiere_fakten()` bereits (unveraendert) verworfen hat.
            cand_fakten, cand_abgelehnt, cand_eskalierbar = _schwachstelle_candidate_fakten(
                treffer, marke=ziel.get("marke"), modell=ziel.get("modell"),
                generation=identitaet.generation or ziel.get("generation"),
                haupt_fakten=[f for f in fakten if f.kategorie == "schwachstelle"],
                erlaube_multi_source="schwachstelle" in erlaube_multi_source)
            fakten += cand_fakten
            abgelehnt += cand_abgelehnt
            eskalierbar["schwachstelle"] = cand_eskalierbar
        if stats is not None and kategorie in eskalierbar:
            stats[kategorie]["eskalierbar"] = eskalierbar[kategorie]
    return fakten


def _identitaet_reicht(identitaet: WebVehicleIdentity) -> bool:
    """Schwelle für Phase 2/3: Marke+Modell belegt — über den Zwei-Domain-Pfad
    ODER (Identity-Hardening) über den engeren Primärquellenpfad (siehe
    `identitaet.identitaets_pfad`). Die Genauigkeit der Fakten folgt danach
    der belegten Generation (Geltungsbereich) — unverändert."""
    return bool(identitaet and identitaet.belegt)


# Identity-Hardening, Abschnitt 8 "Observability": Standard-INFO-Zeile pro
# Identitätsphase (IMMER, kein Flag) + optionale Pro-Treffer-Tabelle NUR hinter
# `AUTO_KI_IDENTITY_DEBUG` (app/config.py). Keine Secrets/Env/Request-Payloads —
# nur registrierbare Domain (nie volle URL/Query-String), Tier, Score,
# Alignment-Code, accepted/rejected.
def _log_identitaet_phase(treffer: list[dict], marke: str | None, modell: str | None,
                          identitaet: WebVehicleIdentity) -> None:
    gesamt = len(treffer)
    aligned = 0
    reject_histogramm: dict[str, int] = {}
    for r in treffer:
        ok, grund = ausgerichtet(r, marke, modell)
        if ok:
            aligned += 1
        else:
            kategorie = (grund or "unbekannt").split(":", 1)[0]
            reject_histogramm[kategorie] = reject_histogramm.get(kategorie, 0) + 1
    log.info(
        "Identitätsphase: %d Treffer, %d ausgerichtet, %d verworfen (%s) — "
        "belegt=%s pfad=%s domains=%d",
        gesamt, aligned, gesamt - aligned,
        ", ".join(f"{k}={n}" for k, n in sorted(reject_histogramm.items())) or "-",
        identitaet.belegt, identitaet.identitaets_pfad or "-", identitaet.belegende_domains,
    )
    if not IDENTITY_DEBUG:
        return
    for r in treffer:
        url = r.get("url") or ""
        ok, grund = ausgerichtet(r, marke, modell)
        log.info(
            "Identity-Debug: domain=%s tier=%d score=%d alignment=%s accepted=%s grund=%s",
            registrable_domain(url), _tier_identitaet(url, marke),
            score_domain(url, KATEGORIE_TECHNISCHE_DATEN), "ok" if grund is None else grund,
            ok, grund or "-",
        )


# Fact-Phase-Observability (Folgeauftrag, Abschnitt 2): BEFUND — die bisherige
# Zusammenfassung ("'%s' belegt (...), %d Fakten, %d verworfen, ...") fasst
# rueckruf+schwachstelle+wartung in EINER Zahl zusammen. Beim realen Lexus-
# RC-F-Request (0 Fakten, 98 verworfen gesamt) ließ sich dadurch NICHT sagen,
# auf welcher Stufe (Search/Alignment/Source-Trust/Extraction/Konsens) welche
# Kategorie tatsächlich verlor.
#
# Bewusst KEINE Architekturänderung (Auftrag §2: "nicht umbauen, nur loggen,
# was mit minimalem Aufwand verfügbar ist"): jede Zahl hier kommt aus bereits
# vorhandenen Werten (`stats` aus `phase_fakten()`, dem bereits bestehenden,
# je-Treffer mit `kategorie`+`grund` gefüllten `abgelehnt`-Mechanismus, und
# der finalen `fakten`-Liste) — keine neue Zählstruktur in der eigentlichen
# Extraktion.
#
# FIX (Forensik-Folgeauftrag, Package F, CONFIRMED Bug): die ursprüngliche
# Fassung behandelte JEDEN `abgelehnt`-Eintrag als genau EINEN disjunkten
# Treffer-Reject und zog ihn von `curated` ab. Tatsächlich entstehen
# `abgelehnt`-Einträge auf ZWEI unterschiedlichen, nicht austauschbaren
# Ebenen: (1) Treffer-Ebene — `source_quality`/Alignment-Gründe, GENAU EIN
# Eintrag je Treffer, direkt in `_extrahiere_fakten()`s Haupt-Loop VOR jeder
# Satz-Verarbeitung — und (2) Claim-/Satz-Ebene — alles danach
# (`no_component`, `no_recall_signal`, `schwach_ausgerichtet_ohne_
# modellnennung`, wartung_*/schwachstelle_candidate_*, …), potenziell
# MEHRFACH pro Treffer UND (nur "wartung"/"schwachstelle") zusätzlich aus dem
# separaten Candidate-Evidence-Pfad für DENSELBEN, bereits source_quality-
# verworfenen Treffer. Die alte Subtraktion ergab dadurch reale negative
# Zwischenwerte (Production-Fund "wartung": weiterverarbeitet=-5). FIX: beide
# Treffer-Ebenen-Zähler werden über EINDEUTIGE URLs gebildet (ein Treffer
# zählt nie doppelt, nie negativ), Claim-/Satz-Ebene bleibt eine STRIKT
# GETRENNTE, eigene Einheit — nie gegen die Treffer-Zähler verrechnet.
_ALIGNMENT_GRUENDE = ("modell_fehlt", "marke_fehlt", "titel_fremde_marke", "andere_generation")


def _log_fakten_phase(kategorie: str, anfragen: int, stats: dict, abgelehnt_delta: list[dict],
                      fakten: list) -> None:
    eigene_rejects = [a for a in abgelehnt_delta if a.get("kategorie") == kategorie]
    roh = (stats.get(kategorie) or {}).get("roh", 0)
    curated = (stats.get(kategorie) or {}).get("curated", 0)

    # Treffer-Ebene, über eindeutige URLs gebildet: eine URL zählt in GENAU
    # einem Bucket (source_trust_reject ODER alignment_reject ODER bestanden),
    # nie in mehreren — das verhindert die Doppelzählung, die vorher zu
    # negativen Werten führte.
    source_trust_reject_urls = {a.get("url") for a in eigene_rejects if a.get("grund") == "source_quality"}
    alignment_reject_urls = {a.get("url") for a in eigene_rejects
                             if str(a.get("grund") or "").split(":", 1)[0] in _ALIGNMENT_GRUENDE
                             and a.get("url") not in source_trust_reject_urls}
    source_trust_reject = len(source_trust_reject_urls)
    alignment_reject = len(alignment_reject_urls)
    source_trust_pass = max(0, curated - source_trust_reject)
    alignment_pass = max(0, source_trust_pass - alignment_reject)

    # Claim-/Satz-Ebene: eigene Einheit, kann mehrfach pro Treffer entstehen —
    # bewusst NIE von einem Treffer-Zähler oben abgezogen.
    claim_rejects = [a for a in eigene_rejects
                     if a.get("grund") != "source_quality"
                     and str(a.get("grund") or "").split(":", 1)[0] not in _ALIGNMENT_GRUENDE]
    finale = sum(1 for f in fakten if f.kategorie == kategorie)

    # `stiller_verlust` bleibt erhalten, aber NUR in der Treffer-Ebene (gleiche
    # Einheit wie `alignment_pass`, siehe Package-F-Auftrag "nur berechnen,
    # wenn Zähler dieselbe Einheit besitzen"): wie viele Treffer haben BEIDE
    # Treffer-Gates bestanden, aber weder einen Claim-Reject NOCH eine finale
    # Quelle hinterlassen -- ein Treffer, der spurlos verschwindet (z.B. ein
    # Vokabular-Nichttreffer ohne eigenen Log-Eintrag im Haupt-Pfad). Über
    # eindeutige URLs gebildet, nie negativ.
    claim_reject_urls = {a.get("url") for a in claim_rejects}
    finale_urls = {q.url for f in fakten if f.kategorie == kategorie
                  for q in (getattr(f, "quellen", None) or [])}
    stiller_verlust = max(0, alignment_pass - len(claim_reject_urls | finale_urls))

    reject_histogramm: dict[str, int] = {}
    for a in eigene_rejects:
        g = str(a.get("grund") or "unbekannt").split(":", 1)[0]
        reject_histogramm[g] = reject_histogramm.get(g, 0) + 1
    log.info(
        "Faktenphase[%s]: %d Anfragen, %d Rohtreffer, %d kuratiert -- "
        "Treffer-Ebene: source_trust %d bestanden/%d verworfen, "
        "alignment %d bestanden/%d verworfen, stiller_verlust=%d -- "
        "Claim-Ebene (eigene Einheit, keine Treffer-Verrechnung): %d Claim-Rejects, "
        "finale_fakten=%d — rejects(%s)",
        kategorie, anfragen, roh, curated,
        source_trust_pass, source_trust_reject, alignment_pass, alignment_reject, stiller_verlust,
        len(claim_rejects), finale,
        ", ".join(f"{k}={n}" for k, n in sorted(reject_histogramm.items())) or "-",
    )


# Identity-Hardening, Abschnitt 5 "International Sources": der US-/Regionen-
# Ausschluss gilt WEITERHIN unverändert für Rückruf-/Technik-Phasen (Detail-/
# Faktenkategorien) — NICHT mehr für die Identitätsphase selbst, deren Frage
# "existiert Marke/Modell/Generation" ist, nicht "welche regionsspezifische
# Ausstattung gilt". Der Schutz vor regionsfremden DETAIL-Claims bleibt
# bestehen — er wirkt jetzt in `_identitaets_claims()` (`_ist_regionsfremd()`-
# Tier-Deckel), nicht mehr über den Ausschluss aus dem Ergebnis-Set selbst.
# Eigene, direkt testbare Funktion statt Inline-Ausdruck in der `suche()`-
# Closure (Abschnitt 9 "Tests").
def _exclude_domains_fuer(phase: str, include_domains) -> list[str] | None:
    if include_domains or phase == "identitaet":
        return None
    return US_QUELLEN_AUSSCHLUSS


# ── Adaptive-Evidence-Research: Mehrrunden-State-Machine (§A Folgeauftrag) ──
#
# Zielarchitektur (PHASE 1-6 des ursprünglichen Auftrags), jetzt als kleine,
# generische State-Machine statt genau einer festen Zusatzrunde:
#   Runde 1  Standardqueries (unverändert, oben in `recherchiere()`).
#   Runde 2  Source-Upgrade: andere Sprache/anderer Source-Type (Hersteller-/
#            Handbuch-/TSB-nah, §5 Auftrag) -- NUR für Kategorien mit Gap
#            (`eskalierbar`).
#   Runde 3  Claim-/komponentenspezifische Bestätigung -- NUR, wenn Runde 2
#            für GENAU DIESE Kategorie einen echten Informationsgewinn
#            gebracht hat (`_hat_fortschritt()`, s. unten). Keine Runde 4.
# Kein unbegrenztes Suchen: maximal 2 Zusatzrunden, hart in der Schleife in
# `TavilyTechnicalResearchProvider.recherchiere()` verdrahtet
# (`_MAX_ADAPTIVE_RUNDEN`), nicht konfigurierbar wachsend.
_MAX_ADAPTIVE_RUNDEN = 3   # Runde 1 (Standard) + maximal 2 adaptive Zusatzrunden


def _eskalations_queries(kategorie: str, *, breit: str, jahr: str | None, basis: str,
                         bauteil_hint: str | None = None, merkmal_hint: str | None = None,
                         runde: int = 2) -> list[tuple[str, str]]:
    """Queries für Runde 2 (Source-Upgrade) oder Runde 3 (Claim-Bestätigung).

    Runde 2, zwei Achsen pro Kategorie, bewusst ORTHOGONAL zu den
    Runde-1-Queries (§9 Auftrag: keine Synonym-Variante derselben Suche):
      source_upgrade_official  -- Hersteller-/Handbuch-/TSB-nahe Formulierung
                                  (§5 Auftrag), auf Deutsch wie Runde 1.
      source_upgrade_en        -- englischsprachig, oft die Sprache
                                  offizieller Manuals/TSBs/internationaler
                                  Fachforen.
    Runde 3: NOCH enger -- der konkrete, aus Runde 1/2 bekannte Claim
    (Bauteil + Merkmal/Fehlertyp) wird explizit zur Bestätigung gesucht,
    statt erneut breit nach dem Bauteil allein zu suchen (sonst wäre Runde 3
    nur eine Synonym-Variante von Runde 2). Komponentenorientiert (§3
    Auftrag), wenn ein konkretes Bauteil/Merkmal aus den bisherigen Runden
    bekannt ist (`bauteil_hint`/`merkmal_hint`) -- sonst generisch
    markenweit. Keine Fahrzeughardcodes."""
    teil = " ".join(filter(None, [bauteil_hint, merkmal_hint]))
    if runde >= 3:
        return [
            ("claim_bestaetigung_official",
             " ".join(filter(None, [basis, teil, "bestätigt Herstellerangabe"]))),
            ("claim_bestaetigung_en",
             " ".join(filter(None, [breit, teil, "confirmed specification"]))),
        ]
    if kategorie == "schwachstelle":
        # "Rückrufaktion" bewusst NICHT im Wortfeld: ein Rückruf ist ein
        # anderes, eigenes Konzept (Pflichtmaßnahme vs. bekannte
        # Schwachstelle) -- diese Query bleibt klar auf Werkstatt-/
        # Herstellerwissen zur Schwachstelle selbst fokussiert, keine
        # versehentliche Vermischung mit der Rückruf-Phase.
        return [
            ("source_upgrade_official",
             " ".join(filter(None, [basis, jahr, "Werkstattinformation technischer Hinweis Herstellerangabe",
                                    bauteil_hint]))),
            ("source_upgrade_en",
             " ".join(filter(None, [breit, "technical service bulletin OR manufacturer statement",
                                    bauteil_hint]))),
        ]
    if kategorie == "wartung":
        return [
            ("source_upgrade_official",
             " ".join(filter(None, [basis, "Betriebsanleitung Serviceheft Wartungsplan", bauteil_hint]))),
            ("source_upgrade_en",
             " ".join(filter(None, [breit, "owner's manual service schedule", bauteil_hint]))),
        ]
    return []


_ESKALATIONS_GRUENDE = ("_multi_source_pending_upgrade", "_corroborated_not_verified")


def _bauteil_hint_aus_abgelehnt(abgelehnt: list[dict], kategorie: str) -> str | None:
    """Häufigstes `bauteil` unter den eskalationsrelevanten Einträgen EINER
    Kategorie -- rein additive Observability-Auswertung (kein neues
    Datenmodell), macht die Eskalationsquery komponentenspezifisch, wenn ein
    klarer Schwerpunkt erkennbar ist."""
    bauteile = [a.get("bauteil") for a in abgelehnt
               if a.get("kategorie") == kategorie
               and (a.get("grund") or "").endswith(_ESKALATIONS_GRUENDE)
               and a.get("bauteil")]
    if not bauteile:
        return None
    return max(set(bauteile), key=bauteile.count)


def _merkmal_hint_aus_abgelehnt(abgelehnt: list[dict], kategorie: str) -> str | None:
    """Analog zu `_bauteil_hint_aus_abgelehnt()`, aber für das konkrete
    Merkmal/den Fehlertyp (`merkmal` bei Wartung, `failure_mode` bei
    Schwachstelle) -- macht Runde-3-Queries claim-spezifisch statt nur
    komponentenspezifisch."""
    feld = "merkmal" if kategorie == "wartung" else "failure_mode"
    werte = [a.get(feld) for a in abgelehnt
            if a.get("kategorie") == kategorie
            and (a.get("grund") or "").endswith(_ESKALATIONS_GRUENDE)
            and a.get(feld)]
    if not werte:
        return None
    return max(set(werte), key=werte.count)


def _zustand_kategorie(abgelehnt: list[dict], kategorie: str) -> dict:
    """Generische Zusammenfassung des aktuellen Beleg-Standes EINER Kategorie
    -- für den Fortschritts-Vergleich zwischen zwei adaptiven Runden
    (`_hat_fortschritt()`). Baut AUSSCHLIESSLICH auf bereits vorhandenen
    `abgelehnt`-Einträgen auf (kein neues Datenmodell, keine zweite
    Buchhaltung): `families` = unabhängige, eskalationsrelevante Source
    Families; `n_kandidaten` = Anzahl unabhängiger, eskalationsrelevanter
    Candidate-URLs (bewusst NACH URL dedupliziert, nicht die rohe
    Eintragsanzahl -- `abgelehnt` wächst über Runden kumulativ, und jede
    Runde wertet den GESAMTEN, bisher gesammelten Treffer-Pool erneut aus;
    ohne diese Deduplizierung würde das bloße Wiederauftauchen bereits
    bekannter Kandidaten in einer Folgerunde fälschlich als "neuer
    Candidate Claim" gezählt); `bester_score` = höchster `score_domain()`-
    Wert unter den dazugehörigen URLs (ein verbesserter Source-Tier, auch
    unterhalb von MIN_SCORE_FAKT, zählt als Fortschritt)."""
    relevant = [a for a in abgelehnt if a.get("kategorie") == kategorie
               and (a.get("grund") or "").endswith(_ESKALATIONS_GRUENDE) and a.get("url")]
    urls = {a["url"] for a in relevant}
    families = {_source_family(u) for u in urls}
    bester_score = max((score_domain(u, _WEB_KATEGORIE.get(kategorie)) for u in urls), default=-1)
    return {"families": len(families), "n_kandidaten": len(urls), "bester_score": bester_score}


def _hat_fortschritt(vorher: dict, nachher: dict) -> bool:
    """Stop-Bedingung C (§A Auftrag): eine komplette Runde ohne neue Source
    Family UND ohne neuen Candidate Claim UND ohne besseren Source-Type
    bringt KEINEN Fortschritt -- dann keine weitere Runde für diese
    Kategorie."""
    return (nachher["families"] > vorher["families"]
            or nachher["n_kandidaten"] > vorher["n_kandidaten"]
            or nachher["bester_score"] > vorher["bester_score"])


# ── Null-Kandidaten-Gap (PASS-GATE-Folgeauftrag §B) ────────────────────────
#
# Befund (Lexus-RC-F-Production-Test): `eskalierbar` (oben) setzt voraus,
# dass Runde 1 bereits >=2 Candidate-Claims zu EINER Gruppe gesammelt hat.
# Scheitern dagegen schon die Treffer SELBST an Alignment/Source-Trust (im
# Produktionsfall: 7/8 bzw. 5/8 an `modell_fehlt` -- die Rohtreffer nannten
# das Zielmodell nicht hinreichend explizit), entstehen NULL Kandidaten --
# `eskalierbar` bleibt False, und die bisherige Logik eskaliert NIE, obwohl
# noch Suchraum/Budget übrig ist. Dieses neue Signal ist UNABHÄNGIG von
# `eskalierbar`: "es gab Rohtreffer (die Suche lief ins Leere, nicht weil
# nichts gefunden wurde, sondern weil nichts davon verwertbar war), aber
# NICHTS davon erreichte auch nur die Candidate-Gruppierung". Es rechtfertigt
# GENAU DIESELBE, bereits vorhandene Source-Upgrade-Query (§5/§6 Auftrag,
# vorige Runde) -- keine neue Suchlogik, nur ein zusätzlicher Auslöser für
# dieselbe Runde-2-Maschinerie. Kein Freibrief fürs volle Budget: Runde 3
# bleibt weiterhin an echten Fortschritt gebunden (`_hat_fortschritt`).
def _ist_null_kandidaten_gap(fakten: list[WebFakt], fakten_stats: dict[str, dict],
                             abgelehnt: list[dict], kategorie: str) -> bool:
    """True, wenn Runde 1 für `kategorie` kuratierte Rohtreffer hatte (eine
    neue Suchrichtung ist also prinzipiell sinnvoll -- bei 0 Rohtreffern gäbe
    es nichts, dessen Scheitern eine Zusatzrunde rechtfertigt), aber WEDER
    ein sichtbarer Fakt NOCH auch nur ein einziger Candidate-Claim entstanden
    ist."""
    if any(f.kategorie == kategorie for f in fakten):
        return False
    if fakten_stats.get(kategorie, {}).get("curated", 0) <= 0:
        return False
    return _zustand_kategorie(abgelehnt, kategorie)["n_kandidaten"] == 0


# ── Identity-Spec-Gap-Recherche (§C Folgeauftrag) ──────────────────────────
#
# Befund (RC-F-Test): Marke/Modell/Leistung/Antrieb ließen sich bestätigen,
# andere grundlegende Specs teilweise nicht. Statt den KOMPLETTEN
# Identity-Prozess zu wiederholen ("Marke/Modell bereits stark bestätigt ->
# keine unnötige Zusatzsuche", § Auftrag), zielt diese Runde NUR auf die
# nach Runde 1 konkret OFFENEN, web-prinzipiell-belegbaren Felder -- und
# bündelt sie in GENAU EINEM DE- und EINEM EN-Query-Paar, egal wie viele
# Felder offen sind (§ Auftrag: "Queries sinnvoll bündeln, nicht 1 Call pro
# Feld"). GENAU EINE Zusatzrunde, kein Mehrrunden-Loop wie bei Weakness/
# Wartung -- Identity ist die Vorbedingung für alles Weitere, nicht selbst
# ein Kandidat für mehrfaches Nachbohren.
#
_IDENTITY_SPEC_FELDER: tuple[tuple[str, str], ...] = (
    ("generation", "generation"), ("fuel", "kraftstoff"), ("drivetrain", "antrieb"),
    ("transmission", "getriebe"), ("powertrain", "powertrain"))
# Produktions-Forensik (Lexus-RC-F-PASS-GATE, §A Folgeauftrag, CONFIRMED Root
# Cause): "Hybrid Elektrifizierung"/"hybrid powertrain" als Suchbegriff für das
# OFFENE `powertrain`-Feld hat die Query selbst Richtung Hybrid-Inhalt
# vorentschieden -- bei einer Marke mit breitem Hybrid-Portfolio (hier: Lexus)
# fand genau diese Formulierung bevorzugt Hybrid-Seiten, unabhängig von der
# tatsächlichen Antriebsart des Zielmodells. GENERISCH für JEDE Marke mit
# ICE/Hybrid-Geschwistermodellen, nicht nur Lexus. Jetzt neutral: fragt nach
# "Antriebstechnologie"/"Motorisierung", nicht nach einer bestimmten Technologie
# -- die Suche soll ERGEBNISOFFEN sein, nicht ergebnisgerichtet.
_IDENTITY_SPEC_WORTE_DE = {
    "generation": "Generation Modellcode", "fuel": "Kraftstoff Antriebsart",
    "drivetrain": "Radantrieb", "transmission": "Getriebe",
    "powertrain": "Antriebstechnologie Motorisierung"}
_IDENTITY_SPEC_WORTE_EN = {
    "generation": "generation model code", "fuel": "fuel type", "drivetrain": "drivetrain",
    "transmission": "transmission", "powertrain": "drivetrain technology"}


def _identity_spec_gaps(identitaet: WebVehicleIdentity) -> list[str]:
    """Web-prinzipiell-belegbare Identitätsfelder, die nach Runde 1 offen
    sind (s. Moduldocstring oben für die bewusst ausgeschlossenen Felder)."""
    return [feld for feld, attr in _IDENTITY_SPEC_FELDER if getattr(identitaet, attr) is None]


# ── Confirm-only-Felder (§2 Folgeauftrag) ──────────────────────────────────
#
# Befund (Auftrag, Präzisierung des vorherigen Ausschlusses): `engine_name`/
# `displacement`/`horsepower` sind NICHT grundsätzlich von adaptiver
# Nachrecherche ausgeschlossen -- NUR das "Web erfindet selbst einen
# konkreten Motor" ist ausgeschlossen. Hat der NUTZER bereits einen Wert
# angegeben (`ziel.get("motor"/"hubraum"/"leistung_ps")`) und ist er nach
# Runde 1 noch nicht unabhängig web-bestätigt, ist das ein legitimer
# BESTÄTIGUNGS-Gap: dieselbe Query-Bündelung wie oben darf GENAU DIESEN
# Claim gezielt zur Bestätigung durchsuchen. Ohne Nutzerwert entsteht HIER
# KEIN Gap -- die Suche erfindet nie selbst eine konkrete Motor-/Leistungs-/
# Hubraumangabe (unverändert gegenüber der vorherigen Runde).
#
# Semantik bleibt bewusst unverändert (kein Reporting-Eingriff, § Auftrag
# "Nicht anfassen: Reporting"): ein hierüber bestätigter Wert durchläuft
# GENAU denselben bestehenden Confirm-Pfad (`werte_identitaet_aus()` ->
# `identitaet.motor`/`leistung_ps`/`feldwerte["displacement"]`) wie ein in
# Runde 1 bestätigter Wert -- "diese Modell-/Variantenkonfiguration mit
# diesem Claim ist belegt", NIE "dieses konkrete Fahrzeug besitzt garantiert
# diese Konfiguration". Der bestehende Hinweis ("am Fahrzeug nicht geprüft")
# in app/kaufcheck_bericht.py/app/anzeige.py bleibt unverändert -- diese
# Runde liefert nur zusätzliche ROHE Suchtreffer in denselben, unveränderten
# Bewertungspfad, keine neue Darstellung.
_IDENTITY_CONFIRM_FELDER = ("engine_name", "displacement", "horsepower")


def _identity_confirm_gaps(ziel: dict, identitaet: WebVehicleIdentity) -> list[str]:
    """Confirm-only-Felder mit einem vorhandenen, aber nach Runde 1 noch
    nicht web-bestätigten Nutzerwert. OHNE Nutzerwert (`ziel.get(...)` leer)
    entsteht HIER KEIN Gap -- s. Moduldocstring oben."""
    gaps = []
    if ziel.get("motor") and not identitaet.motor:
        gaps.append("engine_name")
    if ziel.get("hubraum") and "displacement" not in identitaet.feldwerte:
        gaps.append("displacement")
    if ziel.get("leistung_ps") and not identitaet.leistung_ps:
        gaps.append("horsepower")
    return gaps


def _identity_confirm_claim_text(feld: str, ziel: dict) -> str | None:
    if feld == "engine_name":
        return ziel.get("motor")
    if feld == "displacement":
        return f"{ziel['hubraum']} Liter" if ziel.get("hubraum") else None
    if feld == "horsepower":
        return f"{ziel['leistung_ps']} PS" if ziel.get("leistung_ps") else None
    return None


def _identity_spec_gap_queries(breit: str, jahr: str | None, offene_felder: list[str],
                               *, ziel: dict | None = None,
                               confirm_felder: list[str] | None = None) -> list[tuple[str, str]]:
    """Baut GENAU EIN DE/EN-Query-Paar für ALLE offenen Identitätsfelder --
    sowohl die web-eigenständig auffindbaren (`offene_felder`, s.
    `_identity_spec_gaps`) als auch die Confirm-only-Felder mit vorhandenem
    Nutzerwert (`confirm_felder`, s. `_identity_confirm_gaps`) -- NIE ein Call
    pro Feld, egal wie viele Felder insgesamt offen sind (§ Auftrag:
    "Queries sinnvoll bündeln")."""
    confirm_felder = confirm_felder or []
    if not offene_felder and not confirm_felder:
        return []
    confirm_claims = [c for f in confirm_felder
                      if (c := _identity_confirm_claim_text(f, ziel or {}))]
    # Eigene Wortwahl ("Spezifikation(en)"), bewusst NICHT "technische Daten"
    # -- das ist die Runde-1-Formulierung (`q_ident` oben); ein Test/Log, der
    # Runde-1-Identitätsqueries an dieser Phrase erkennt, darf diese
    # gebündelte Zusatzquery nicht fälschlich mitzählen.
    de = " ".join(filter(None, [breit, jahr] + [_IDENTITY_SPEC_WORTE_DE[f] for f in offene_felder]
                         + confirm_claims + ["Spezifikation"]))
    en = " ".join(filter(None, [breit] + [_IDENTITY_SPEC_WORTE_EN[f] for f in offene_felder]
                         + confirm_claims + ["engine specifications" if confirm_claims
                                             else "specifications"]))
    return [("identity_spec_gap_de", de), ("identity_spec_gap_en", en)]


# ── Tavily-Implementierung ───────────────────────────────────────────────────

class TavilyTechnicalResearchProvider:
    """Phasenweise Recherche über den BESTEHENDEN Tavily-Pfad (Cache, Retry,
    Budgets, Timeouts in app/web_search.py/app/provider_control.py)."""

    def __init__(self, *, count: int = 8):
        self._count = count

    async def recherchiere(self, *, marke, modell, baujahr, motor,
                           ausgeloest_durch, ziel: dict | None = None) -> TechnischeRecherche:
        z = _ziel(marke, modell, baujahr, motor, ziel)
        # `z["modell"]` statt des rohen `modell`-Parameters: `_ziel()` hat einen
        # etwaigen Generations-/Chassiscode bereits abgetrennt (siehe dort) —
        # Queries sollen mit dem Kern-Modellnamen suchen, nicht mit einem Code,
        # der in normalem Webtext ohnehin fast nie vorkommt.
        breit = " ".join(filter(None, [marke, z["modell"]]))
        jahr = str(baujahr) if baujahr else None
        anfragen = 0
        fehler = False
        abgelehnt: list[dict] = []
        phasen: list[str] = []
        # Root Cause 6: Anfragen/Fehlschläge JE PHASE, nicht nur ein geteiltes
        # Bool — Grundlage für `phasen_status` unten.
        anf_gesamt = {"identitaet": 0, "rueckruf": 0, "technik": 0}
        anf_fehler = {"identitaet": 0, "rueckruf": 0, "technik": 0}

        async def suche(query, *, phase, **kw):
            nonlocal anfragen, fehler
            anfragen += 1
            anf_gesamt[phase] += 1
            exclude = _exclude_domains_fuer(phase, kw.get("include_domains"))
            try:
                return await tavily_search(query, count=self._count,
                                           exclude_domains=exclude,
                                           include_domains=kw.get("include_domains"))
            except Exception as exc:                    # pragma: no cover — Schutznetz
                log.warning("Technische Teilrecherche fehlgeschlagen (%s)", type(exc).__name__)
                fehler = True
                anf_fehler[phase] += 1
                return []

        # PHASE 1 — Identität
        phasen.append("identitaet")
        motor_ps = " ".join(filter(None, [z.get("motor"),
                                          f"{z['leistung_ps']} PS" if z.get("leistung_ps") else None]))
        q_ident = [" ".join(filter(None, [breit, jahr, motor_ps, "technische Daten"])),
                   " ".join(filter(None, [breit, jahr, "Generation Baureihe Bauzeitraum"]))]
        ident_treffer = [r for liste in await asyncio.gather(*[suche(q, phase="identitaet") for q in q_ident])
                         for r in liste]
        identitaet, abgelehnt_i = phase_identitaet(ident_treffer, z)
        abgelehnt += abgelehnt_i
        _log_identitaet_phase(ident_treffer, marke, z["modell"], identitaet)
        status_identitaet = _phase_status(lief=True, anfragen_gesamt=anf_gesamt["identitaet"],
                                          anfragen_fehlgeschlagen=anf_fehler["identitaet"])
        if not _identitaet_reicht(identitaet):
            log.info("Technische Recherche: Identität '%s' NICHT belegt — keine Rückruf-/"
                     "Technikphase (%d Anfragen)", breit, anfragen)
            return TechnischeRecherche(ausgeloest_durch=ausgeloest_durch, identitaet=identitaet,
                                       provider_fehler=fehler and not ident_treffer,
                                       phasen=phasen, abgelehnte_fakten=abgelehnt,
                                       anfragen=anfragen,
                                       phasen_status={"identitaet": status_identitaet,
                                                     "rueckruf": PHASE_NOT_RUN,
                                                     "technik": PHASE_NOT_RUN})

        # Identity-Spec-Gap-Recherche (§C Folgeauftrag, s. Moduldocstring bei
        # `_identity_spec_gaps`/`_identity_confirm_gaps`): Marke/Modell sind
        # belegt (sonst wären wir oben schon zurückgekehrt) -- jetzt NUR die
        # konkret offenen, web-belegbaren Felder PLUS die offenen Confirm-
        # only-Felder mit vorhandenem Nutzerwert (§2 Folgeauftrag) gezielt
        # nachfragen, GENAU EINE gebündelte Zusatzrunde (2 Queries,
        # unabhängig von der Gesamtzahl offener Felder).
        offene_spec_felder = _identity_spec_gaps(identitaet)
        offene_confirm_felder = _identity_confirm_gaps(z, identitaet)
        if offene_spec_felder or offene_confirm_felder:
            spec_intents = _identity_spec_gap_queries(
                breit, jahr, offene_spec_felder, ziel=z, confirm_felder=offene_confirm_felder)
            spec_treffer_je_intent = await asyncio.gather(
                *[suche(q, phase="identitaet") for _, q in spec_intents])
            neue_spec_treffer = [r for t in spec_treffer_je_intent for r in t]
            if neue_spec_treffer:
                ident_treffer = ident_treffer + neue_spec_treffer
                identitaet, abgelehnt_i2 = phase_identitaet(ident_treffer, z)
                abgelehnt += abgelehnt_i2
                # §A Auftrag Punkt 7: dieselbe Claim-/Source-Diagnostik wie
                # Runde 1 (Alignment-Histogramm, Pfad, Domainzahl + im
                # IDENTITY_DEBUG-Fall die Pro-Domain-Zeilen) -- bisher endete
                # die Beobachtbarkeit nach Runde 1, Runde 2 war eine
                # Diagnostik-Blackbox (Forensik-Fund, konkret dafür
                # verantwortlich, dass der HEV-Fehlbefund nicht auf Domain-
                # Ebene nachvollziehbar war).
                _log_identitaet_phase(ident_treffer, marke, z["modell"], identitaet)
                geloest = [f for f, attr in _IDENTITY_SPEC_FELDER
                          if f in offene_spec_felder and getattr(identitaet, attr) is not None]
                geloest += [f for f in offene_confirm_felder
                           if f not in _identity_confirm_gaps(z, identitaet)]
                status_identitaet = _phase_status(lief=True, anfragen_gesamt=anf_gesamt["identitaet"],
                                                  anfragen_fehlgeschlagen=anf_fehler["identitaet"])
            else:
                geloest = []
            log.info("research_phase=identity_specs round=2 gap=%s query_count=%d "
                     "new_raw_results=%d fields_resolved=%s stop_reason=%s",
                     ",".join(offene_spec_felder + offene_confirm_felder), len(spec_intents),
                     len(neue_spec_treffer), ",".join(geloest) or "none",
                     "resolved" if geloest else "no_new_evidence")

        # Query-Anreicherung (Abschnitt 4 Auftrag): bestätigte Generation hat
        # Vorrang; ohne sie darf der rein formbasierte Auxiliary-Kandidat die
        # Suche trotzdem anreichern -- "query richness" ist KEIN Alignment-
        # Anspruch, nur ein breiteres Netz. Nie umgekehrt: der Kandidat fließt
        # NIE in `generation=` einer Scope-/Applicability-Prüfung ein (siehe
        # `_ziel()`-Docstring).
        gen = identitaet.generation or z.get("generation") or z.get("auxiliary_code")
        basis = " ".join(filter(None, [breit, gen]))
        # PHASE 2 — Rückrufe (amtlich/Fachquellen bevorzugt)
        phasen.append("rueckruf")
        rr = await asyncio.gather(
            # Ohne Baujahr: ein Rückruf wird Jahre NACH der Produktion veröffentlicht;
            # ob das Baujahr betroffen ist, entscheidet das Produktionsfenster im
            # Quelltext (_extrahiere_fakten). Mit Baujahr fand die Suche vor allem
            # Meldungen aus dem Baujahr selbst.
            suche(" ".join(filter(None, [basis, "Rückruf"])), phase="rueckruf"),
            suche(" ".join(filter(None, [breit, "Rückruf Rückrufaktion"])),
                  phase="rueckruf", include_domains=_RUECKRUF_DOMAINS))
        roh = {"rueckruf": [r for liste in rr for r in liste]}
        status_rueckruf = _phase_status(lief=True, anfragen_gesamt=anf_gesamt["rueckruf"],
                                        anfragen_fehlgeschlagen=anf_fehler["rueckruf"])
        # PHASE 3 — technische Hinweise
        phasen.append("technik")
        # Schwachstelle: zwei ORTHOGONALE Search-Intents, beide werden IMMER
        # ausgeführt — kein Early Exit zwischen ihnen (RC-W5-Query-Audit,
        # Scratch-Beweis: die bisherigen Q1/Q2 waren nur Synonym-Varianten
        # desselben Intents ["typische Probleme Schwachstellen" ≈ "bekannte
        # Schwachstellen"] und haben per `tavily_search_with_fallback`
        # "erster Treffer gewinnt" nachweislich echte, komplementäre Fakten
        # verpasst — ein Live-A/B-Test fand z. B. bei identischem Fahrzeug
        # über den zweiten Intent 4 zusätzliche valide Fakten, die der erste
        # nie fand, UND umgekehrt).
        sw_intents = (
            ("technical_weakness",
             " ".join(filter(None, [basis, jahr, "bekannte Probleme Schwachstellen"]))),
            ("used_car_review", f"{breit} Gebrauchtwagen Test TÜV Kaufberatung"),
        )
        # RC-W5 (Wartung, Design-Audit + Entscheidung): dieselbe Lehre gilt hier.
        # Der bisherige `tavily_search_with_fallback([Q1, Q2])`-Pfad prüfte nur,
        # ob Q1 ROH nichtleer war — VOR jeder Curation/Extraktion. Lieferte Q1
        # ausschliesslich Treffer, die `_extrahiere_fakten()`/der Wartungs-
        # Evidence-Layer (Commit 55873be) vollständig verwarfen (z.B.
        # "source_quality", "no_maintenance_interval"), wurde Q2 trotzdem NIE
        # ausgeführt — und ausgerechnet der Evidence-Layer verlor damit die
        # zweite Source-Family, die einen CORROBORATED-Fakt zu VERIFIED heben
        # (Cross-Tier-Bedingung A) oder einen CONFLICT aufdecken könnte.
        # Entscheidung: kein Coverage-Score, kein Early-Exit — Q1 und Q2 sind
        # zwei Basis-Suchzugänge zum selben Wartungsraum, nicht Primary+Fallback.
        # Qualität geht vor einem gesparten Tavily-Call.
        wa_intents = (
            ("service_interval",
             " ".join(filter(None, [basis, "Wartungsintervall Serviceintervall"]))),
            ("inspection_interval", f"{breit} Inspektionsintervall"),
        )
        technik_intents = sw_intents + wa_intents
        technik_treffer_je_intent = await asyncio.gather(
            *[suche(q, phase="technik") for _, q in technik_intents])
        sw_treffer_je_intent = technik_treffer_je_intent[:len(sw_intents)]
        wa_treffer_je_intent = technik_treffer_je_intent[len(sw_intents):]
        # Observability (nur log.debug — keine User-UI, keine zweite
        # Extraction-Pipeline): pro Intent Rohtreffer- und High-Quality-Domain-
        # Zahl, BEVOR die Listen zusammengeführt werden. Die eigentliche
        # Faktenbildung läuft weiterhin EINMAL unten, auf der je Kategorie
        # vereinten Liste, durch die unveränderte Pipeline (curate_results +
        # _extrahiere_fakten/Wartungs-Evidence-Layer inkl. bestehender
        # RC-W3-Dedupe/Agreement/Conflict-Logik).
        for (intent, q), treffer in zip(sw_intents, sw_treffer_je_intent):
            hoch = sum(1 for r in treffer
                      if score_domain(r.get("url") or "", _WEB_KATEGORIE["schwachstelle"]) >= MIN_SCORE_FAKT)
            log.debug("Schwachstelle-Intent '%s' (%r): %d Rohtreffer, %d High-Quality-Domains",
                     intent, q, len(treffer), hoch)
        for (intent, q), treffer in zip(wa_intents, wa_treffer_je_intent):
            hoch = sum(1 for r in treffer
                      if score_domain(r.get("url") or "", _WEB_KATEGORIE["wartung"]) >= MIN_SCORE_FAKT)
            log.debug("Wartung-Intent '%s' (%r): %d Rohtreffer, %d High-Quality-Domains",
                     intent, q, len(treffer), hoch)
        roh["schwachstelle"] = [r for treffer in sw_treffer_je_intent for r in treffer]
        roh["wartung"] = [r for treffer in wa_treffer_je_intent for r in treffer]
        fakten_stats: dict[str, dict] = {}
        abgelehnt_vor_fakten = len(abgelehnt)
        fakten = phase_fakten(roh, z, identitaet, ("rueckruf", "schwachstelle", "wartung"),
                              abgelehnt, stats=fakten_stats)
        abgelehnt_delta = abgelehnt[abgelehnt_vor_fakten:]
        for kat, anz_anfragen in (("rueckruf", 2), ("schwachstelle", len(sw_intents)),
                                  ("wartung", len(wa_intents))):
            _log_fakten_phase(kat, anz_anfragen, fakten_stats, abgelehnt_delta, fakten)

        # PHASE 2+ (Adaptive-Evidence-Research, §A Folgeauftrag): echte
        # Mehrrunden-State-Machine statt genau einer festen Zusatzrunde --
        # s. Moduldocstring bei `_MAX_ADAPTIVE_RUNDEN` oben. Identity/Rückruf
        # eskalieren hier NICHT: Identity ist die Voraussetzung für alles
        # Weitere (s. oben, früher Return) und Rückruf darf laut Trust-System
        # (app/fakt_verifikation.py, AGENTS.md) ohnehin nie allein über
        # Multi-Source-Corroboration "verifiziert" werden -- nur amtliche/
        # Herstellerquellen zählen dort.
        #
        # `zustand_vorher[kat]`: Schnappschuss VOR der jeweils nächsten Runde
        # -- Grundlage für `_hat_fortschritt()` (Stop-Bedingung C). Wird nach
        # jeder Runde für die jeweilige Kategorie aktualisiert.
        zustand_vorher: dict[str, dict] = {
            kat: _zustand_kategorie(abgelehnt, kat) for kat in ("schwachstelle", "wartung")}
        # Kategorien, die in Runde 2 tatsächlich Fortschritt gebracht haben --
        # NUR diese dürfen in Runde 3 weitersuchen (§A Auftrag: Runde 3 nur,
        # "wenn Round 2 tatsächlich neue Evidence geliefert hat").
        fortschritt_runde2: set[str] = set()
        for runde_nr in range(2, _MAX_ADAPTIVE_RUNDEN + 1):
            null_kandidaten_kategorien = frozenset(
                kat for kat in ("schwachstelle", "wartung")
                if runde_nr == 2 and _ist_null_kandidaten_gap(fakten, fakten_stats, abgelehnt, kat))
            aktive_kategorien = frozenset(
                kat for kat in ("schwachstelle", "wartung")
                if (fakten_stats.get(kat, {}).get("eskalierbar") or kat in null_kandidaten_kategorien)
                and (runde_nr == 2 or kat in fortschritt_runde2))
            if not aktive_kategorien:
                break
            for kat in aktive_kategorien:
                gap_grund = "null_kandidaten" if kat in null_kandidaten_kategorien else "pending_upgrade"
                fakt_state_vorher = "resolved" if any(f.kategorie == kat for f in fakten) else "open"
                bauteil_hint = _bauteil_hint_aus_abgelehnt(abgelehnt, kat)
                merkmal_hint = _merkmal_hint_aus_abgelehnt(abgelehnt, kat)
                esk_intents = _eskalations_queries(kat, breit=breit, jahr=jahr, basis=basis,
                                                   bauteil_hint=bauteil_hint,
                                                   merkmal_hint=merkmal_hint, runde=runde_nr)
                esk_treffer_je_intent = await asyncio.gather(
                    *[suche(q, phase="technik") for _, q in esk_intents])
                neue_treffer = [r for treffer in esk_treffer_je_intent for r in treffer]
                roh[kat] = roh[kat] + neue_treffer
                fakten = [f for f in fakten if f.kategorie != kat]
                runde_fakten = phase_fakten({kat: roh[kat]}, z, identitaet, (kat,), abgelehnt,
                                            stats=fakten_stats, erlaube_multi_source=frozenset({kat}))
                fakten += runde_fakten
                zustand_nachher = _zustand_kategorie(abgelehnt, kat)
                fortschritt = _hat_fortschritt(zustand_vorher[kat], zustand_nachher)
                # Stop-Bedingung D (§A Auftrag): ein in dieser Runde
                # entstandener Konflikt setzt `eskalierbar` für die
                # betroffene Gruppe zurück (s. Candidate-Evidence-Schichten,
                # CONFLICT setzt `eskalierbar` nie) -- bleibt die Kategorie
                # danach nicht mehr eskalierbar, OBWOHL diese Runde neue
                # Evidence brachte (`fortschritt=True`), ist das ein
                # aufgelöster Konflikt/Abschluss, keine Fortsetzung.
                noch_offen = bool(fakten_stats.get(kat, {}).get("eskalierbar"))
                if runde_fakten:
                    stop_reason = "resolved"
                elif not noch_offen and zustand_nachher["n_kandidaten"] == 0:
                    # Null-Kandidaten-Pfad (§B Auftrag): auch die Recovery-
                    # Suche fand nichts Verwertbares -- kein Konflikt, nur
                    # weiterhin keine brauchbare Quelle. Eigenes Label, damit
                    # es nicht wie ein aufgelöster Konflikt aussieht.
                    stop_reason = "no_recovery"
                elif not noch_offen:
                    stop_reason = "conflict_or_closed"
                elif not fortschritt:
                    stop_reason = "no_new_evidence"
                elif runde_nr >= _MAX_ADAPTIVE_RUNDEN:
                    stop_reason = "max_round_reached"
                else:
                    stop_reason = "continue"
                if stop_reason == "continue":
                    fortschritt_runde2.add(kat)
                log.info("research_phase=%s round=%d gap=%s query_count=%d "
                         "new_source_families=%d new_candidates=%d source_tier_improved=%s "
                         "fact_state_before=%s fact_state_after=%s %s=%s",
                         kat, runde_nr, gap_grund, len(esk_intents),
                         zustand_nachher["families"] - zustand_vorher[kat]["families"],
                         zustand_nachher["n_kandidaten"] - zustand_vorher[kat]["n_kandidaten"],
                         zustand_nachher["bester_score"] > zustand_vorher[kat]["bester_score"],
                         fakt_state_vorher, "resolved" if runde_fakten else "open",
                         "continue_reason" if stop_reason == "continue" else "stop_reason", stop_reason)
                zustand_vorher[kat] = zustand_nachher

        # Inhalts-Schwäche der Technik-Phase (Root Cause 6, Test 3): Anfragen
        # können sauber durchgekommen sein und trotzdem nur duenne, einstufige
        # Belege liefern. Dieselbe Tier-Pruefung wie ueberall sonst im Modul
        # (`_tier`), kein neues Kriterium.
        technik_fakten = [f for f in fakten if f.kategorie in ("schwachstelle", "wartung")]
        technik_stark = any(_tier(q.url or "") <= 2 for f in technik_fakten for q in f.quellen)
        status_technik = _phase_status(lief=True, anfragen_gesamt=anf_gesamt["technik"],
                                       anfragen_fehlgeschlagen=anf_fehler["technik"],
                                       inhalt_schwach=not technik_stark)
        log.info("Technische Recherche: '%s' belegt (Gen=%s, Konf=%s), %d Fakten, %d verworfen, "
                 "%d Anfragen, Phasenstatus=%s", breit, gen, identitaet.identitaet_konfidenz, len(fakten),
                 len(abgelehnt), anfragen,
                 {"identitaet": status_identitaet, "rueckruf": status_rueckruf, "technik": status_technik})
        return TechnischeRecherche(ausgeloest_durch=ausgeloest_durch, identitaet=identitaet,
                                   fakten=fakten, provider_fehler=fehler, phasen=phasen,
                                   abgelehnte_fakten=abgelehnt, anfragen=anfragen,
                                   phasen_status={"identitaet": status_identitaet,
                                                 "rueckruf": status_rueckruf,
                                                 "technik": status_technik})


# ── Fixture-Provider (Tests) ─────────────────────────────────────────────────

class FixtureTechnicalResearchProvider:
    """Deterministischer Provider für Tests — kein Netzwerk. Durchläuft DIESELBE
    phasenweise Auswertung wie der Tavily-Provider: die Fixture-Liste
    "identitaet" speist Phase 1, "rueckruf" Phase 2, "schwachstelle"/"wartung"
    Phase 3 — und Phase 2/3 laufen nur nach belegter Identität.

    `fehler_phasen` (Root Cause 6, additiv): Phasennamen, deren Provider-Aufruf
    als vollständig fehlgeschlagen simuliert wird — unabhängig von `treffer`,
    das für diese Phase ignoriert wird. Default leer: bisheriges Verhalten
    unverändert. `fehler=True` bleibt der bestehende Totalausfall VOR Phase 1."""

    def __init__(self, treffer: dict[str, list[dict]] | None = None, *, fehler: bool = False,
                fehler_phasen: frozenset[str] | None = None):
        self._treffer = treffer or {}
        self._fehler = fehler
        self._fehler_phasen = fehler_phasen or frozenset()
        self.phasen_aufrufe: list[str] = []

    async def recherchiere(self, *, marke, modell, baujahr, motor,
                           ausgeloest_durch, ziel: dict | None = None) -> TechnischeRecherche:
        if self._fehler:
            return TechnischeRecherche(ausgeloest_durch=ausgeloest_durch, provider_fehler=True,
                                       phasen_status={"identitaet": PHASE_FAILED,
                                                     "rueckruf": PHASE_NOT_RUN,
                                                     "technik": PHASE_NOT_RUN})
        z = _ziel(marke, modell, baujahr, motor, ziel)
        abgelehnt: list[dict] = []
        self.phasen_aufrufe.append("identitaet")
        ident_fehler = "identitaet" in self._fehler_phasen
        ident_treffer = [] if ident_fehler else list(self._treffer.get("identitaet") or [])
        identitaet, abgelehnt_i = phase_identitaet(ident_treffer, z)
        abgelehnt += abgelehnt_i
        status_identitaet = PHASE_FAILED if ident_fehler else PHASE_SUCCESS
        if not _identitaet_reicht(identitaet):
            return TechnischeRecherche(ausgeloest_durch=ausgeloest_durch, identitaet=identitaet,
                                       phasen=["identitaet"], abgelehnte_fakten=abgelehnt,
                                       provider_fehler=ident_fehler,
                                       phasen_status={"identitaet": status_identitaet,
                                                     "rueckruf": PHASE_NOT_RUN,
                                                     "technik": PHASE_NOT_RUN})
        self.phasen_aufrufe += ["rueckruf", "technik"]
        rueckruf_fehler = "rueckruf" in self._fehler_phasen
        technik_fehler = "technik" in self._fehler_phasen
        roh = {
            "rueckruf": [] if rueckruf_fehler else list(self._treffer.get("rueckruf") or []),
            "schwachstelle": [] if technik_fehler else list(self._treffer.get("schwachstelle") or []),
            "wartung": [] if technik_fehler else list(self._treffer.get("wartung") or []),
        }
        fakten = phase_fakten(roh, z, identitaet, ("rueckruf", "schwachstelle", "wartung"), abgelehnt)
        status_rueckruf = PHASE_FAILED if rueckruf_fehler else PHASE_SUCCESS
        if technik_fehler:
            status_technik = PHASE_FAILED
        else:
            # Dieselbe Tier-Pruefung wie der echte Provider (Root Cause 6,
            # Test 3): nur Tier-3-Belege oder gar keine Technik-Fakten trotz
            # "erfolgreicher" Suche -> PARTIAL, kein stillschweigendes SUCCESS.
            technik_fakten = [f for f in fakten if f.kategorie in ("schwachstelle", "wartung")]
            technik_stark = any(_tier(q.url or "") <= 2 for f in technik_fakten for q in f.quellen)
            status_technik = PHASE_SUCCESS if technik_stark else PHASE_PARTIAL
        return TechnischeRecherche(ausgeloest_durch=ausgeloest_durch, identitaet=identitaet,
                                   fakten=fakten, phasen=["identitaet", "rueckruf", "technik"],
                                   abgelehnte_fakten=abgelehnt,
                                   provider_fehler=rueckruf_fehler or technik_fehler,
                                   phasen_status={"identitaet": status_identitaet,
                                                 "rueckruf": status_rueckruf,
                                                 "technik": status_technik})


# ── Öffentliche Fassade ──────────────────────────────────────────────────────

def _ziel_aus_request(req) -> dict:
    from app.getriebe import aus_request
    from app.vehicle_identity import antrieb_nutzer
    return {"leistung_ps": getattr(req, "leistung_ps", None),
            "kraftstoff": getattr(req, "kraftstoff", None),
            "getriebe": aus_request(req), "antrieb": antrieb_nutzer(req)}


async def recherchiere_technisch(req, baureihe_roh, identitaet, baureihe_gegatet,
                                 motor_match, *, provider=None) -> TechnischeRecherche | None:
    """Führt den Fallback aus, WENN ein Trigger vorliegt — sonst None. Fängt jede
    Provider-Ausnahme ab (§17)."""
    trigger = fallback_trigger(req, baureihe_roh, identitaet, baureihe_gegatet, motor_match)
    if trigger is None:
        return None
    provider = provider or TavilyTechnicalResearchProvider()
    # Ist die Baureihe belastbar zugeordnet (Trigger "Motor fehlt"/"Konflikt"),
    # recherchiert der Fallback das KANONISCHE Modell der Datenbank, nicht die
    # Rohangabe: "Astra L" hieße sonst, dass jede Quelle das Token "L" tragen
    # muss (realer Smoke-Test: alle Quellen als "modell_fehlt" verworfen). Die
    # bekannte Generation geht als Ziel mit (Generationsabgleich der Fakten).
    marke, modell = getattr(req, "marke", None), getattr(req, "modell", None)
    ziel = _ziel_aus_request(req)
    if baureihe_gegatet:
        marke = baureihe_gegatet.get("marke") or marke
        modell = baureihe_gegatet.get("modell") or modell
        ziel["generation"] = baureihe_gegatet.get("generation")
    try:
        return await provider.recherchiere(
            marke=marke, modell=modell,
            baujahr=getattr(req, "baujahr", None), motor=getattr(req, "motor", None),
            ausgeloest_durch=trigger, ziel=ziel)
    except TypeError:
        # Ältere Provider ohne `ziel`-Parameter.
        try:
            return await provider.recherchiere(
                marke=marke, modell=modell,
                baujahr=getattr(req, "baujahr", None), motor=getattr(req, "motor", None),
                ausgeloest_durch=trigger)
        except Exception as exc:
            log.warning("Technischer Web-Fallback fehlgeschlagen (%s)", type(exc).__name__)
            return TechnischeRecherche(ausgeloest_durch=trigger, provider_fehler=True)
    except Exception as exc:
        log.warning("Technischer Web-Fallback fehlgeschlagen (%s)", type(exc).__name__)
        return TechnischeRecherche(ausgeloest_durch=trigger, provider_fehler=True)


def technical_coverage(baureihe_gegatet, recherche: TechnischeRecherche | None) -> str:
    hat_web = bool(recherche and recherche.identitaet and recherche.identitaet.belegt)
    if baureihe_gegatet is not None:
        return "db_plus_web" if (hat_web or (recherche and recherche.fakten)) else "db"
    if hat_web:
        return "web"
    return "partial"


# Kompatibilität: frühere Helfer (Tests/Diagnose importieren sie).
def _kraftstoff_aus_treffern(treffer: list[dict]) -> str | None:
    gefunden = {wert for r in treffer for wert, rx in _KRAFTSTOFF_CLAIM
                if rx.search(f"{r.get('title') or ''} {r.get('content') or ''}")}
    return gefunden.pop() if len(gefunden) == 1 else None


def _leistung_aus_treffern(treffer: list[dict], motor_hint: str | None) -> int | None:
    m = _RE_PS.search(motor_hint or "")
    if not m:
        return None
    wert = int(m.group(1))
    for r in treffer:
        if any(int(x) == wert for x in _RE_PS.findall(f"{r.get('title') or ''} {r.get('content') or ''}")):
            return wert
    return None
