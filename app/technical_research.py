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
_RE_ANDERES_SUBJEKT = re.compile(
    r"\b(?:[Bb]ei(?:m|\s+dem)|[Ff]ür\s+(?:den|die|das))\s+([A-ZÄÖÜ][\wÄÖÜäöüß-]*)")
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


def _anderes_fahrzeug_lokal(satz: str, modell: str | None) -> str | None:
    """Enge Erkennung eines ANDEREN, explizit genannten Fahrzeug-Subjekts in
    der "beim/für den X"-Konstruktion — None, wenn keine solche Konstruktion
    vorliegt oder X das Zielmodell/ein Bauteil/ein bekanntes Füllwort ist."""
    m = _RE_ANDERES_SUBJEKT.search(satz)
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
    if unsicher:
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
                    eintrag = kandidaten.setdefault(
                        kandidat_key, {"aussage": claim, "bauteil": bauteil_claim,
                                      "treffer": [],
                                      "zeitraum": z, "passt": passt, "vage": vage.group(0) if vage else None})
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
    m = _WARTUNG_INTERVALL_ZAHL.search(claim or "")
    if not m:
        return None
    if m.group(1):
        zahl = re.sub(r"[.\s]", "", m.group(1))
        return f"{zahl}km"
    einheit = "monate" if m.group(4).lower().startswith("monat") else "jahre"
    return f"{m.group(3)}{einheit}"


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
    satz_liste = _saetze(f"{r.get('title') or ''}. {r.get('content') or ''}", max_len=None)
    ergebnis: list[str] = []
    abstand_seit_nennung: int | None = None
    for idx in range(len(satz_liste)):
        satz = _kontext_aufloesen(satz_liste, idx)
        if satz is None:
            continue
        if grund == "schwach":
            if _claim_widerspricht_fahrzeug_lokal(satz, marke, modell):
                abstand_seit_nennung = None
                continue
            if _satz_nennt_modell(satz, modell):
                abstand_seit_nennung = 0
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

    status in (UNVERIFIED, CONFLICT, CORROBORATED, VERIFIED):
      UNVERIFIED     0-1 zählende Family.
      CONFLICT       >=2 Families, UNTERSCHIEDLICHE Werte.
      CORROBORATED   >=2 Families, GLEICHER Wert, aber KEINE Zusatzbedingung
                     erfüllt -- bleibt NICHT user-visible (app/evidence.py
                     zeigt jeden Eintrag aus `fakten` ungefiltert).
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
    return WARTUNG_STATUS_CORROBORATED, stimmen, inkonsistent, provenance_claimed, None


def _wartung_candidate_fakten(treffer: list[dict], *, marke: str | None, modell: str | None,
                              generation: str | None, motor: str | None,
                              getriebe: str | None = None,
                              haupt_fakten: list[WebFakt] | None = None,
                              ) -> tuple[list[WebFakt], list[dict]]:
    """Candidate-Evidence-Pfad NUR für Treffer UNTERHALB von MIN_SCORE_FAKT
    (siehe Moduldocstring oben). Treffer darüber werden hier NICHT nochmal
    verarbeitet -- die laufen unverändert durch `_extrahiere_fakten()`.
    `haupt_fakten`: die vom BESTEHENDEN Pfad für "wartung" bereits erzeugten
    WebFakt-Objekte -- für die Cross-Tier-Bedingung A gelesen UND (bei
    VERIFIED) direkt per Objekt-Mutation um Candidate-Quellen angereichert
    (siehe unten), nie ersetzt.

    Der zurückgegebene `fakten`-Teil bleibt nach heutigem Stand IMMER leer:
    Bedingung B (Provenienz) wurde entfernt, und Bedingung A (einziger
    verbleibender VERIFIED-Pfad) setzt per Definition einen bereits in
    `haupt_fakten` vorhandenen Treffer voraus, der dann angereichert statt
    neu erzeugt wird. Der Rückgabewert bleibt trotzdem `list[WebFakt]` (statt
    ersatzlos entfernt), damit eine künftige, unabhängig von einem
    Hauptpfad-Treffer gültige Verification-Bedingung (z.B. ein echter
    `provenance_verified`-Mechanismus) ohne Signatur-Änderung andockt."""
    haupt_fakten = haupt_fakten or []
    abgelehnt: list[dict] = []
    kandidaten: list[dict] = []
    vokabular = _bauteil_vokabular()

    for r in treffer:
        url = r.get("url") or ""
        if score_domain(url, KATEGORIE_WARTUNG) >= MIN_SCORE_FAKT:
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
        elif status == WARTUNG_STATUS_CORROBORATED:
            # §3/§4 Auftrag: CORROBORATED bleibt bewusst NICHT user-visible
            # (kein Eintrag in `fakten`) -- nur Observability. `provenance_claimed`
            # wird mitgeloggt (§7 Auftrag), beeinflusst aber NICHT den Status.
            for k in gruppe:
                if k["family"] in stimmen:
                    abgelehnt.append({"url": k["url"], "kategorie": "wartung",
                                      "grund": "wartung_corroborated_not_verified",
                                      "satz": k["claim"][:120], "source_type": k["source_type"],
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
    return fakten, abgelehnt


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
        if kategorie == "wartung":
            # Source-Trust-Fix (Scope: NUR wartung) -- siehe Moduldocstring
            # oben. Rein additiv: verarbeitet ausschliesslich Treffer, die
            # `_extrahiere_fakten()` bereits (unveraendert) verworfen hat.
            cand_fakten, cand_abgelehnt = _wartung_candidate_fakten(
                treffer, marke=ziel.get("marke"), modell=ziel.get("modell"),
                generation=identitaet.generation or ziel.get("generation"),
                motor=ziel.get("motor"), getriebe=ziel.get("getriebe"),
                haupt_fakten=[f for f in fakten if f.kategorie == "wartung"])
            bisherige_wartung = sum(1 for f in fakten if f.kategorie == "wartung")
            fakten += cand_fakten[:max(0, MAX_FAKTEN_JE_KATEGORIE - bisherige_wartung)]
            abgelehnt += cand_abgelehnt
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
        # Schwachstelle: zwei ORTHOGONALE Search-Intents, beide werden IMMER
        # ausgeführt — kein Early Exit zwischen ihnen (RC-W5-Query-Audit,
        # Scratch-Beweis: die bisherigen Q1/Q2 waren nur Synonym-Varianten
        # desselben Intents ["typische Probleme Schwachstellen" ≈ "bekannte
        # Schwachstellen"] und haben per `tavily_search_with_fallback`
        # "erster Treffer gewinnt" nachweislich echte, komplementäre Fakten
        # verpasst — ein Live-A/B-Test fand z. B. bei identischem Fahrzeug
        # über den zweiten Intent 4 zusätzliche valide Fakten, die der erste
        # nie fand, UND umgekehrt). Der spätere intelligente RC-W5-Early-Exit
        # entscheidet erst zwischen weiteren Suchrunden, nicht zwischen diesen
        # beiden Basis-Intents. Wartung bleibt unverändert beim bestehenden
        # Fallback — dafür existiert noch kein entsprechender A/B-Beweis.
        sw_intents = (
            ("technical_weakness",
             " ".join(filter(None, [basis, jahr, "bekannte Probleme Schwachstellen"]))),
            ("used_car_review", f"{breit} Gebrauchtwagen Test TÜV Kaufberatung"),
        )
        sw_treffer_je_intent, wa = await asyncio.gather(
            asyncio.gather(*[suche(q, phase="technik") for _, q in sw_intents]),
            tavily_search_with_fallback([" ".join(filter(None, [basis, "Wartungsintervall Serviceintervall"])),
                                         f"{breit} Inspektionsintervall"],
                                        count=self._count, exclude_domains=US_QUELLEN_AUSSCHLUSS),
            return_exceptions=True)
        if isinstance(sw_treffer_je_intent, Exception):           # pragma: no cover — Schutznetz
            fehler = True
            sw_treffer_je_intent = [[] for _ in sw_intents]
        # `suche()` zählt die beiden Schwachstelle-Calls bereits einzeln in
        # anfragen/anf_gesamt["technik"] mit — hier kommt nur die Wartung-
        # Fallback-Gruppe als EIN weiterer Anfrage-Block hinzu (wie bisher).
        anfragen += 1
        anf_gesamt["technik"] += 1
        if isinstance(wa, Exception):
            anf_fehler["technik"] += 1
            fehler = True
            wa = []
        # Observability (RC-W5-Vorbereitung, nur log.debug — keine User-UI,
        # keine zweite Extraction-Pipeline): pro Intent Rohtreffer- und
        # High-Quality-Domain-Zahl, BEVOR die Listen zusammengeführt werden.
        # Die eigentliche Faktenbildung läuft weiterhin EINMAL unten, auf der
        # vereinten Liste, durch die unveränderte Pipeline (curate_results +
        # _extrahiere_fakten inkl. bestehender RC-W3-Dedupe).
        for (intent, q), treffer in zip(sw_intents, sw_treffer_je_intent):
            hoch = sum(1 for r in treffer
                      if score_domain(r.get("url") or "", _WEB_KATEGORIE["schwachstelle"]) >= MIN_SCORE_FAKT)
            log.debug("Schwachstelle-Intent '%s' (%r): %d Rohtreffer, %d High-Quality-Domains",
                     intent, q, len(treffer), hoch)
        roh["schwachstelle"] = [r for treffer in sw_treffer_je_intent for r in treffer]
        roh["wartung"] = wa if isinstance(wa, list) else []
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
