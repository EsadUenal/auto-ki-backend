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

from app.kraftstoff_powertrain import powertrain_aus_freitext
from app.models import EvidenceQuelle, TechnischeRecherche, WebFakt, WebVehicleIdentity
from app.web_search import (
    KATEGORIE_RUECKRUFE, KATEGORIE_SCHWACHSTELLEN, KATEGORIE_TECHNISCHE_DATEN,
    KATEGORIE_WARTUNG, US_QUELLEN_AUSSCHLUSS,
    _domain_von, _qualitaets_label, curate_results, score_domain,
    tavily_search, tavily_search_with_fallback,
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


def _tier(url: str) -> int:
    s = score_domain(url)
    if s >= TIER1_MIN_SCORE:
        return 1
    if s >= TIER2_MIN_SCORE:
        return 2
    return 3


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


def _tier_identitaet(url: str) -> int:
    dom = _domain_von(url) or ""
    if any(dom == d or dom.endswith("." + d) for d in _IDENTITAET_TIER2):
        return min(_tier(url), 2)
    return _tier(url)


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


# ── Entity-Alignment ─────────────────────────────────────────────────────────

def _modell_im_titel(titel_tokens: set[str], modell_tokens: set[str]) -> bool:
    return bool(modell_tokens) and modell_tokens <= titel_tokens


def ausgerichtet(r: dict, marke: str | None, modell: str | None) -> tuple[bool, str | None]:
    """Ob ein Treffer das Zielfahrzeug BETRIFFT (Invariante 8).

    Regeln:
      * Marke und Modell müssen als ganze Tokens im Treffer stehen.
      * Nennt der TITEL eine andere Marke (und nicht die eigene) oder die eigene
        Marke ohne das Modell, ist die Seite primär über ein anderes Fahrzeug —
        ein passender Einzelsatz im Text ändert daran nichts (Befund: Ford-
        Mustang-Artikel als MX-5-Schwachstelle).
    """
    from app.vehicle_identity import MARKEN
    modell_tokens = _tokens(modell)
    marke_tokens = _tokens(marke)
    titel_tokens = _tokens(r.get("title"))
    text_tokens = titel_tokens | _tokens(r.get("content"))
    if not modell_tokens or not modell_tokens <= text_tokens:
        return False, "modell_fehlt"
    if marke_tokens and not (marke_tokens & text_tokens):
        return False, "marke_fehlt"
    fremde = (titel_tokens & MARKEN) - marke_tokens
    if fremde and not (marke_tokens & titel_tokens):
        return False, f"titel_fremde_marke:{','.join(sorted(fremde))}"
    if titel_tokens and not _modell_im_titel(titel_tokens, modell_tokens):
        # Die Seite nennt das Modell nur im Text (Sammelmeldung, Markenübersicht):
        # SCHWACH ausgerichtet. Daraus zählen nur Sätze, die das Modell selbst
        # nennen, und nur mit der niedrigsten Quellenstufe.
        return True, "schwach"
    return True, None


def _satz_nennt_modell(satz: str, modell: str | None) -> bool:
    return bool(_tokens(modell)) and _tokens(modell) <= _tokens(satz)


def _identitaet_belegt(modell: str | None, marke: str | None,
                       treffer: list[dict]) -> tuple[bool, list[dict], int]:
    """Marke UND Modell als ganze Tokens auf >= MIN_DOMAINS_IDENTITAET
    unabhängigen, hinreichend vertrauenswürdigen Domains — nur aus Treffern, die
    das Zielfahrzeug betreffen. Token-exakt: "ix7" ist nicht "x7".

    Rückgabe: (belegt, stuetzende_treffer, anzahl_unabhaengiger_domains)"""
    if not _tokens(modell):
        return False, [], 0
    stuetzend: list[dict] = []
    domains: set[str] = set()
    for r in treffer:
        url = r.get("url") or ""
        if (score_domain(url, KATEGORIE_TECHNISCHE_DATEN) < MIN_SCORE_IDENTITAET
                and _tier_identitaet(url) > 2):
            continue
        ok, _ = ausgerichtet(r, marke, modell)
        if not ok:
            continue
        stuetzend.append(r)
        d = _domain_von(url)
        if d:
            domains.add(d)
    return len(domains) >= MIN_DOMAINS_IDENTITAET, stuetzend, len(domains)


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
_RE_HUBRAUM = re.compile(r"\b(\d[.,]\d)\s*(?:-?\s*(?:l\b|liter)|\s+(?=[A-Za-z]))", re.IGNORECASE)
_KRAFTSTOFF_CLAIM = (("diesel", re.compile(r"\bdiesel\w*|\btdi\b|\bcdi\b|\bdci\b|skyactiv-?d", re.I)),
                     ("benzin", re.compile(r"\bbenzin\w*|ottomotor|\btfsi\b|\btsi\b|skyactiv-?g|petrol", re.I)),
                     ("elektro", re.compile(r"\belektroauto|\bbatterieelektrisch|\belectric vehicle", re.I)))
_ANTRIEB_CLAIM = (("Heck", re.compile(r"hinterradantrieb|heckantrieb|hinterr(?:ä|ae)der|rear[- ]wheel", re.I)),
                  ("Front", re.compile(r"frontantrieb|vorderradantrieb|front[- ]wheel", re.I)),
                  ("Allrad", re.compile(r"allradantrieb|allrad\b|all[- ]wheel|\bawd\b|quattro|xdrive|4matic|4motion", re.I)))
_RE_GETRIEBE_MANUELL = re.compile(r"\b(\d)\s*-?\s*gang[- ]?(?:schaltgetriebe|handschaltung|manuell\w*)"
                                  r"|\b(sechs|f(?:ü|ue)nf)gang[- ]?(?:schaltgetriebe|handschaltung)"
                                  r"|\b(\d)-speed manual", re.I)
_RE_GETRIEBE_AUTO = re.compile(r"\b(\d{1,2})\s*-?\s*(?:stufen|gang)[- ]?(?:automatik\w*|wandlerautomatik)"
                               r"|\bautomatikgetriebe\b|\b(\d{1,2})-speed automatic", re.I)
_RE_MOTORCODE_CLAIM = re.compile(r"(?:motorcode|motorkennung|motorkennbuchstaben|engine code)\s*[:(]?\s*"
                                 r"([A-Z0-9]{2,}[A-Z0-9-]*)", re.I)
_ZAHLWORT = {"sechs": 6, "fünf": 5, "fuenf": 5}


_ROEMISCH = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}
# Wörter, die einen Code als GENERATIONSbezeichnung ausweisen. Ohne diesen
# Kontext (oder ein passendes Jahresfenster) ist ein Kürzel hinter dem
# Modellnamen oft eine Ausstattungs-/Karosserie-/Leistungsbezeichnung
# ("… G 184", "… RF", "… GTI") und kein Generationscode.
_GEN_KONTEXT = re.compile(r"generation|baureihe|modellreihe|\btyps?\b|codename|werkscode"
                          r"|interne?[nr]?\s+bezeichnung|\bgen\.", re.IGNORECASE)


def _generationscodes(r: dict, modell: str | None) -> list[dict]:
    """Generationscodes, die UNMITTELBAR hinter dem Modellnamen stehen ("MX-5 ND",
    "MX-5 (ND)", "Golf VII"), je mit

      zeitraum  Jahresfenster direkt dahinter (oder None),
      kontext   ein Generationswort in der Umgebung ("vierte Generation (… ND)")
                bzw. die Form "Modell IV (ND …)" — sonst ist das Kürzel oft eine
                Ausstattungs-/Karosserie-/Leistungsbezeichnung,
      nummer    Generationsnummer aus "Modell IV (ND …)" (IV = 4).

    Eine römische Zahl mit folgendem Klammercode ist die NUMMER, der
    Klammercode der CODE derselben Generation (vorher entstand daraus "IV" als
    Code, und die Seite galt sogar als "andere Generation")."""
    text = f"{r.get('title') or ''}. {r.get('content') or ''}"
    modell_rx = r"[\s-]*".join(re.escape(t) for t in re.split(r"[\s-]+", (modell or "").strip()) if t)
    if not modell_rx:
        return []
    out: list[dict] = []
    # Modellname case-insensitiv, der Generationscode selbst in Großbuchstaben.
    for m in re.finditer(r"(?i:" + modell_rx + r")\s*\(?\s*([A-Z]{1,2}\d{0,3}|[IVX]{1,4})\b\)?", text):
        code = m.group(1)
        if code.upper() in ("PS", "KW", "TDI", "TSI", "GT", "S", "I"):
            continue
        ende = m.end()
        nummer = None
        if code in _ROEMISCH:
            folge = re.match(r"\s*\(\s*([A-Z]{1,2}\d{0,3})\b", text[m.end():])
            if folge:
                nummer, code = _ROEMISCH[code], folge.group(1)
                ende = m.end() + folge.end()
        umgebung = text[ende: ende + 70]
        kontext = bool(nummer) or bool(_GEN_KONTEXT.search(
            text[max(0, m.start() - 60): m.start()] + " " + text[m.start(): ende + 30]))
        out.append({"code": code, "zeitraum": zeitraum(umgebung), "kontext": kontext,
                    "nummer": nummer})
    return out


def _identitaets_claims(r: dict, ziel: dict) -> list[dict]:
    """Alle Identitäts-Claims EINES ausgerichteten Treffers."""
    url = r.get("url") or ""
    dom, tier = _domain_von(url), _tier_identitaet(url)
    _, grund = ausgerichtet(r, ziel.get("marke"), ziel.get("modell"))
    if grund == "schwach":
        # Nur Sätze, die das Modell nennen; niedrigste Stufe.
        tier = 3
        text = " ".join(s for s in _saetze(f"{r.get('title') or ''}. {r.get('content') or ''}")
                        if _satz_nennt_modell(s, ziel.get("modell")))
    else:
        text = f"{r.get('title') or ''}. {r.get('content') or ''}"
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
            kontext=g["kontext"], bonus=varianten_bonus)
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
    for wert, rx in _KRAFTSTOFF_CLAIM:
        if rx.search(text):
            add("fuel", wert)
    for wert, rx in _ANTRIEB_CLAIM:
        if rx.search(text):
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
        gaenge = m.group(1) or _ZAHLWORT.get(_norm(m.group(2) or "")) or m.group(3)
        add("transmission", "manuell", detail=f"{gaenge}-Gang" if gaenge else None)
    for m in _RE_GETRIEBE_AUTO.finditer(text):
        stufen = m.group(1) or m.group(2)
        add("transmission", "automatik", detail=f"{stufen}-Stufen" if stufen else None)
    for m in _RE_MOTORCODE_CLAIM.finditer(text):
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
                                   "detail": _haeufigstes(e["details"])}
        return None
    gewicht, stark, key, e = bewertet[0]
    if not stark or gewicht < MIN_KONSENS_GEWICHT:
        return None
    for g2, stark2, _k2, _e2 in bewertet[1:]:
        if stark2 and g2 * 2 >= gewicht:
            return None       # Konflikt vergleichbar starker Quellen -> UNKNOWN
    return e["wert"], {"domains": len(e["domains"]), "gewicht": gewicht,
                       "detail": _haeufigstes(e["details"])}


def _haeufigstes(werte: list[str]) -> str | None:
    if not werte:
        return None
    return max(set(werte), key=werte.count)


def _conf(info: dict) -> str:
    return "hoch" if info["gewicht"] >= 6 else "mittel" if info["gewicht"] >= 4 else "niedrig"


def _generationen_pruefen(claims: list[dict]) -> None:
    """Verwirft (in place) Generations-Claims ohne Beleg für DIESES Fahrzeug.

      * Ein Code, dessen Jahresfenster in irgendeiner Quelle das Baujahr
        ausschließt und in keiner Quelle einschließt, ist überall ausgeschlossen
        (eine Vergleichsseite "NA/ND" nennt beide, eine andere grenzt NA ein).
      * Ein Code ohne Jahresfenster UND ohne Generationskontext zählt nicht: er
        ist dann meist eine Ausstattungs-, Karosserie- oder Leistungsbezeichnung
        ("… G 184", "… RF"). Vorher entstanden daraus Konflikte, die jede
        Generation auf "unbekannt" setzten.
    """
    gen = [c for c in claims if c["feld"] == "generation"]
    aus = {c["wert"] for c in gen if c.get("verworfen") == "baujahr_ausserhalb"}
    ein = {c["wert"] for c in gen if c.get("jahr_belegt")}
    for c in gen:
        if c.get("verworfen"):
            continue
        if c["wert"] in aus - ein:
            c["verworfen"] = "baujahr_ausserhalb_andere_quelle"
        elif not (c.get("jahr_belegt") or c.get("kontext")):
            c["verworfen"] = "ohne_generationskontext"


def werte_identitaet_aus(treffer: list[dict], ziel: dict) -> tuple[WebVehicleIdentity, list[dict]]:
    """Phase-1-Auswertung: belegte Identität + per Konsens akzeptierte Felder."""
    marke, modell = ziel.get("marke"), ziel.get("modell")
    belegt, stuetzend, domains = _identitaet_belegt(modell, marke, treffer)
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
    _generationen_pruefen(claims)
    abgelehnt += [{"url": c["url"], "feld": c["feld"], "wert": c["wert"], "grund": c["verworfen"]}
                  for c in claims if c.get("verworfen")]
    feldwerte: dict[str, dict] = {}
    akzeptiert: list[dict] = []

    def uebernehme(feld, ergebnis, *, zielfeld=None, wert=None):
        if ergebnis is None:
            return None
        w, info = ergebnis
        w = wert if wert is not None else w
        feldwerte[zielfeld or feld] = {"value": w, "confidence": _conf(info),
                                       "domains": info["domains"], "detail": info.get("detail")}
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
        # "QX12"); sonst markante Wörter (>= 4 Buchstaben, z.B. "skyactiv"). Eine
        # Hubraumangabe muss zusätzlich im selben Text stehen.
        toks = _tokens(motor)
        gemischt = {x for x in toks if re.search(r"\d", x) and re.search(r"[a-z]", x)}
        woerter = {x for x in toks if x.isalpha() and len(x) >= 4
                   and x not in ("turbo", "benzin", "diesel", "motor", "biturbo")}
        kern = gemischt or woerter
        hub = re.search(r"\b(\d)[.,](\d)\b", motor)
        if hub:
            kern = kern | {hub.group(1), hub.group(2)}
        doms = {_domain_von(r.get("url") or "") for r in claim_quellen
                if kern <= _tokens(f"{r.get('title') or ''} {r.get('content') or ''}")
                and _tier_identitaet(r.get("url") or "") <= 2}
        if kern and (len(doms) >= 2 or any(_tier_identitaet(r.get("url") or "") == 1
                                           for r in claim_quellen
                                           if kern <= _tokens(f"{r.get('title')} {r.get('content')}"))):
            motor_bestaetigt = motor
            feldwerte["engine_name"] = {"value": motor, "confidence": "mittel",
                                        "domains": len(doms) or 1}

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
    ), abgelehnt


# ── Phase 2/3: Fakten mit Geltungsbereich ────────────────────────────────────

_PROBLEM_WORTE = ("defekt", "problem", "schwachstelle", "verschleiss", "verschleiß",
                  "ausfall", "undicht", "riss", "bruch", "schaden", "haeufig",
                  "häufig", "anfaellig", "anfällig", "typisch", "bekannt")
_RUECKRUF_WORTE = ("rueckruf", "rückruf", "recall", "rueckrufaktion", "rückrufaktion")
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


def _saetze(text: str) -> list[str]:
    return [s.strip() for s in _SATZ.split(text or "")
            if 20 <= len(s.strip()) <= 300 and len(s.split()) >= 5]


def _anzeige_bauteil(satz: str, muster: str) -> str:
    """Das Bauteil so, wie die Quelle es nennt ("Kraftstoffpumpe", "Verdeck") —
    nie der interne Dedup-Schlüssel ("einspritzung", "dach_fenster")."""
    for wort in re.findall(r"[A-Za-zÄÖÜäöüß][\wÄÖÜäöüß-]*", satz or ""):
        if muster in _norm(wort).replace(" ", ""):
            return wort[:1].upper() + wort[1:]
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
_FUELLWOERTER = frozenset({
    "der", "die", "das", "des", "dem", "den", "ein", "eine", "einen", "einem", "einer",
    "und", "oder", "fuer", "von", "im", "am", "zu", "ist", "sind", "auch", "nur", "bei",
    "aus", "auf", "als", "mit", "nach", "vor", "ueber", "unter", "sich", "es", "er", "sie",
})


def _inhaltstokens(satz: str, marke: str | None, modell: str | None,
                   bauteil: str | None) -> set[str]:
    """Die INHALTSTRAGENDEN Tokens eines Satzes — alles ausser Marke, Modell,
    Bauteil, dem Rückruf-Vokabular und generischen Füllwörtern. Grundlage
    sowohl für die Substanz-Schwelle (`_ist_substanziell`) als auch für den
    Event-Gleichheits-Abgleich (`_gleiches_rueckruf_ereignis`)."""
    kern = _tokens(marke) | _tokens(modell) | _tokens(bauteil)
    uebrig = _tokens(satz) - _FUELLWOERTER - {"rueckruf", "recall", "rueckrufaktion"} - kern
    # Kompositum-Flexionsformen ("kraftstoffleitungs" aus "Kraftstoffleitungs-
    # Rückruf") zählen ebenfalls zum Kern — exakte Mengendifferenz reicht dafür
    # nicht, deshalb zusätzlich ein fuzzy Präfix-Abgleich für längere Tokens.
    return {t for t in uebrig
           if not any(len(k) >= 4 and (t.startswith(k) or k.startswith(t)) for k in kern)}


def _ist_substanziell(satz: str, marke: str | None, modell: str | None,
                      bauteil: str | None) -> bool:
    return len(_inhaltstokens(satz, marke, modell, bauteil)) >= 3


# Release-Hardening (Cross-Source-Review): wie viele INHALTSTRAGENDE Tokens
# (nicht Marke/Modell/Bauteil/Rückruf-Vokabular — s.o.) zwei Rückruf-Aussagen
# teilen müssen, um als dieselbe Meldung zu gelten. Bauteil ALLEIN reicht
# nicht (siehe `_gleiches_rueckruf_ereignis`) — zwei völlig unabhängige
# Kraftstoffleitungs-Rückrufe dürfen sich nicht gegenseitig sperren. Bewusst
# konservativ: im Zweifel NICHT als dasselbe Ereignis gelten (das überlässt
# die Entscheidung dann der eigenen Scope-Prüfung des jeweiligen Kandidaten).
_MIN_GLEICHHEIT_TOKENS = 2


def _gleiches_rueckruf_ereignis(kandidat_tokens: set[str],
                                ausgeschlossene_tokens: list[set[str]]) -> bool:
    return any(len(kandidat_tokens & ex) >= _MIN_GLEICHHEIT_TOKENS
              for ex in ausgeschlossene_tokens)


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
    vokabular = _bauteil_vokabular()
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
        score = score_domain(url, _WEB_KATEGORIE[kategorie])
        if score < min_score:
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
        satz_liste = _saetze(f"{r.get('title') or ''}. {r.get('content') or ''}")
        for idx in range(len(satz_liste)):
            satz = _kontext_aufloesen(satz_liste, idx)
            if satz is None:
                abgelehnt.append({"url": url, "kategorie": kategorie,
                                  "grund": "kontextabhaengiger_satz_ohne_bezug",
                                  "satz": satz_liste[idx][:120]})
                continue
            if grund == "schwach" and not _satz_nennt_modell(satz, modell):
                continue
            if kategorie == "rueckruf":
                fremde_marke = _fremde_marke_satz(satz, marke, modell)
                if fremde_marke:
                    abgelehnt.append({"url": url, "kategorie": kategorie,
                                      "grund": f"fremde_marke_satz:{fremde_marke}",
                                      "satz": satz[:120]})
                    continue
            n = _norm(satz)
            if kategorie == "schwachstelle" and not any(w in n for w in _PROBLEM_WORTE):
                continue
            if kategorie == "rueckruf" and not any(w in n for w in _RUECKRUF_WORTE):
                continue
            if kategorie == "wartung" and not _INTERVALL.search(satz):
                continue
            z = zeitraum(satz) if kategorie != "wartung" else None
            passt = _im_zeitraum(baujahr, z)
            if passt is None and artikel_passt is True:
                # Der Satz selbst nennt kein Fenster, der Artikel schon.
                passt, z = True, artikel_fenster
            if passt is False:
                abgelehnt.append({"url": url, "kategorie": kategorie, "grund": "baujahr_ausserhalb",
                                  "satz": satz[:120]})
                continue
            vage = _RE_VAGE.search(satz) if kategorie != "rueckruf" else None
            treffer_voc = [(m, s) for m, s in vokabular.items() if m in n][:1]
            if not treffer_voc and kategorie == "rueckruf":
                # Rückrufe betreffen oft Teile außerhalb des Prüfplan-Vokabulars
                # ("Kraftstoffleitung"). Ein Rückruf darf daran nicht verloren
                # gehen: das Bauteil wird dann aus dem Satz selbst gelesen.
                teil = _rueckruf_bauteil(satz)
                if teil:
                    treffer_voc = [(None, "rr:" + _norm(teil).replace(" ", ""))]
            for muster, schluessel in treffer_voc:
                if (kategorie == "rueckruf" and passt is not True
                        and schluessel in ausgeschlossene_themen):
                    bauteil_kandidat = (_anzeige_bauteil(satz, muster) if muster
                                        else _rueckruf_bauteil(satz))
                    kandidat_tokens = _inhaltstokens(satz, marke, modell, bauteil_kandidat)
                    if _gleiches_rueckruf_ereignis(kandidat_tokens, ausgeschlossene_themen[schluessel]):
                        abgelehnt.append({"url": url, "kategorie": kategorie,
                                          "grund": "rueckruf_thema_bereits_ausgeschlossen",
                                          "satz": satz[:120]})
                        continue
                eintrag = kandidaten.setdefault(
                    schluessel, {"aussage": satz,
                                 "bauteil": (_anzeige_bauteil(satz, muster) if muster
                                             else _rueckruf_bauteil(satz)),
                                 "treffer": [],
                                 "zeitraum": z, "passt": passt, "vage": vage.group(0) if vage else None})
                if r not in eintrag["treffer"]:
                    eintrag["treffer"].append(r)
                if passt is True and eintrag["passt"] is not True:
                    eintrag.update(aussage=satz, zeitraum=z, passt=True, vage=None)
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
        fakten.append(WebFakt(
            kategorie=kategorie, bauteil=e["bauteil"], aussage=e["aussage"],
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


# ── Gemeinsame, phasenweise Auswertung ───────────────────────────────────────

def _ziel(marke, modell, baujahr, motor, ziel: dict | None) -> dict:
    z = {"marke": marke, "modell": modell, "baujahr": baujahr, "motor": (motor or "").strip() or None}
    z.update({k: v for k, v in (ziel or {}).items() if v is not None})
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
                 kategorien: tuple[str, ...], abgelehnt: list[dict]) -> list[WebFakt]:
    fakten: list[WebFakt] = []
    for kategorie in kategorien:
        treffer = curate_results(roh.get(kategorie) or [],
                                 kategorie=_WEB_KATEGORIE[kategorie], max_results=8)
        fakten += _extrahiere_fakten(treffer, kategorie, marke=ziel.get("marke"),
                                     modell=ziel.get("modell"), baujahr=ziel.get("baujahr"),
                                     generation=identitaet.generation or ziel.get("generation"),
                                     abgelehnt=abgelehnt, hubraum=ziel.get("hubraum"),
                                     leistung_ps=ziel.get("leistung_ps"))
    return fakten


def _identitaet_reicht(identitaet: WebVehicleIdentity) -> bool:
    """Schwelle für Phase 2/3: Marke+Modell auf >= 2 unabhängigen, ausgerichteten
    Domains belegt. Die Genauigkeit der Fakten folgt danach der belegten
    Generation (Geltungsbereich)."""
    return bool(identitaet and identitaet.belegt)


# ── Tavily-Implementierung ───────────────────────────────────────────────────

class TavilyTechnicalResearchProvider:
    """Phasenweise Recherche über den BESTEHENDEN Tavily-Pfad (Cache, Retry,
    Budgets, Timeouts in app/web_search.py/app/provider_control.py)."""

    def __init__(self, *, count: int = 8):
        self._count = count

    async def recherchiere(self, *, marke, modell, baujahr, motor,
                           ausgeloest_durch, ziel: dict | None = None) -> TechnischeRecherche:
        z = _ziel(marke, modell, baujahr, motor, ziel)
        breit = " ".join(filter(None, [marke, modell]))
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
            try:
                return await tavily_search(query, count=self._count,
                                           exclude_domains=None if kw.get("include_domains") else US_QUELLEN_AUSSCHLUSS,
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

        gen = identitaet.generation or z.get("generation")
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
        sw, wa = await asyncio.gather(
            tavily_search_with_fallback([" ".join(filter(None, [basis, jahr, "typische Probleme Schwachstellen"])),
                                         f"{breit} bekannte Schwachstellen"],
                                        count=self._count, exclude_domains=US_QUELLEN_AUSSCHLUSS),
            tavily_search_with_fallback([" ".join(filter(None, [basis, "Wartungsintervall Serviceintervall"])),
                                         f"{breit} Inspektionsintervall"],
                                        count=self._count, exclude_domains=US_QUELLEN_AUSSCHLUSS),
            return_exceptions=True)
        anfragen += 2
        anf_gesamt["technik"] = 2
        anf_fehler["technik"] = sum(1 for r in (sw, wa) if isinstance(r, Exception))
        roh["schwachstelle"] = sw if isinstance(sw, list) else []
        roh["wartung"] = wa if isinstance(wa, list) else []
        fehler = fehler or isinstance(sw, Exception) or isinstance(wa, Exception)
        fakten = phase_fakten(roh, z, identitaet, ("rueckruf", "schwachstelle", "wartung"), abgelehnt)
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
