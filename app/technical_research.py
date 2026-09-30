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
        if score_domain(url, KATEGORIE_TECHNISCHE_DATEN) < MIN_SCORE_IDENTITAET:
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
    r"|early\s+(?:models|cars|builds)|older\s+(?:models|cars)", re.IGNORECASE)


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


def _generationscodes(r: dict, modell: str | None) -> list[tuple[str, tuple | None]]:
    """(Code, Zeitraum) für jeden Generationscode, der UNMITTELBAR hinter dem
    Modellnamen steht ("MX-5 ND", "MX-5 (ND)", "Golf VII"), samt eines
    Jahresfensters in derselben Umgebung."""
    text = f"{r.get('title') or ''}. {r.get('content') or ''}"
    modell_rx = r"[\s-]*".join(re.escape(t) for t in re.split(r"[\s-]+", (modell or "").strip()) if t)
    if not modell_rx:
        return []
    out: list[tuple[str, tuple | None]] = []
    # Modellname case-insensitiv, der Generationscode selbst in Großbuchstaben.
    for m in re.finditer(r"(?i:" + modell_rx + r")\s*\(?\s*([A-Z]{1,2}\d{0,3}|[IVX]{1,4})\b\)?", text):
        code = m.group(1)
        if code.upper() in ("PS", "KW", "TDI", "TSI", "GT", "S", "I"):
            continue
        umgebung = text[m.end(): m.end() + 70]
        out.append((code, zeitraum(umgebung)))
    return out


def _identitaets_claims(r: dict, ziel: dict) -> list[dict]:
    """Alle Identitäts-Claims EINES ausgerichteten Treffers."""
    url = r.get("url") or ""
    dom, tier = _domain_von(url), _tier(url)
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

    for code, z in _generationscodes(r, ziel.get("modell")):
        passt = _im_zeitraum(baujahr, z)
        if passt is False:
            add("generation", code, verworfen="baujahr_ausserhalb", zeitraum=z)
        else:
            add("generation", code, zeitraum=z, jahr_belegt=passt is True)
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
        e = je_wert.setdefault(key, {"wert": c["wert"], "domains": {}, "details": []})
        alt = e["domains"].get(c["domain"])
        if alt is None or c["tier"] < alt:
            e["domains"][c["domain"]] = c["tier"]
        if c.get("detail"):
            e["details"].append(c["detail"])
    if not je_wert:
        return None
    bewertet = []
    for key, e in je_wert.items():
        gewicht = sum(_GEWICHT[t] for t in e["domains"].values())
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

    claims = [c for r in stuetzend for c in _identitaets_claims(r, ziel)]
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
    # unwidersprochen per Konsens gestützt ist.
    gen_claims = [c for c in claims if c["feld"] == "generation"]
    mit_jahr = [c for c in gen_claims if c.get("jahr_belegt")]
    generation = uebernehme("generation", konsens(mit_jahr or gen_claims, "generation"))
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
        doms = {_domain_von(r.get("url") or "") for r in stuetzend
                if kern <= _tokens(f"{r.get('title') or ''} {r.get('content') or ''}")
                and _tier(r.get("url") or "") <= 2}
        if kern and (len(doms) >= 2 or any(_tier(r.get("url") or "") == 1 for r in stuetzend
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
        antrieb=antrieb, getriebe=getriebe, getriebe_detail=getriebe_detail, motorcode=motorcode,
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
_SATZ = re.compile(r"(?<=[.!?])\s+")


def _bauteil_vokabular() -> dict[str, str]:
    from app.kaufaktionen import _KOMPONENTEN
    return {muster: eintrag["schluessel"]
            for eintrag in _KOMPONENTEN for muster in eintrag["muster"]}


def _saetze(text: str) -> list[str]:
    return [s.strip() for s in _SATZ.split(text or "") if 20 <= len(s.strip()) <= 300]


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
    codes = {c.upper() for c, _ in _generationscodes({"title": r.get("title"), "content": ""}, modell)}
    return bool(codes) and generation.upper() not in codes


def _extrahiere_fakten(treffer: list[dict], kategorie: str, *,
                       marke: str | None = None, modell: str | None = None,
                       baujahr: int | None = None, generation: str | None = None,
                       abgelehnt: list[dict] | None = None) -> list[WebFakt]:
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
        for satz in _saetze(f"{r.get('title') or ''}. {r.get('content') or ''}"):
            if grund == "schwach" and not _satz_nennt_modell(satz, modell):
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
            if passt is False:
                abgelehnt.append({"url": url, "kategorie": kategorie, "grund": "baujahr_ausserhalb",
                                  "satz": satz[:120]})
                continue
            vage = _RE_VAGE.search(satz) if kategorie != "rueckruf" else None
            for muster, schluessel in vokabular.items():
                if muster not in n:
                    continue
                eintrag = kandidaten.setdefault(
                    schluessel, {"aussage": satz, "bauteil": _anzeige_bauteil(satz, muster),
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
        if kategorie == "rueckruf":
            applicability = "vehicle_possible" if e["passt"] is True else "series_only"
        else:
            applicability = None
        fakten.append(WebFakt(
            kategorie=kategorie, bauteil=e["bauteil"], aussage=e["aussage"],
            confidence=confidence, applicability=applicability, quellen=quellen,
            geltungsbereich=bereich, geltung_fuer_fahrzeug=geltung,
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
                                     generation=identitaet.generation, abgelehnt=abgelehnt)
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

        async def suche(query, **kw):
            nonlocal anfragen, fehler
            anfragen += 1
            try:
                return await tavily_search(query, count=self._count,
                                           exclude_domains=None if kw.get("include_domains") else US_QUELLEN_AUSSCHLUSS,
                                           include_domains=kw.get("include_domains"))
            except Exception as exc:                    # pragma: no cover — Schutznetz
                log.warning("Technische Teilrecherche fehlgeschlagen (%s)", type(exc).__name__)
                fehler = True
                return []

        # PHASE 1 — Identität
        phasen.append("identitaet")
        motor_ps = " ".join(filter(None, [z.get("motor"),
                                          f"{z['leistung_ps']} PS" if z.get("leistung_ps") else None]))
        q_ident = [" ".join(filter(None, [breit, jahr, motor_ps, "technische Daten"])),
                   " ".join(filter(None, [breit, jahr, "Generation Baureihe Bauzeitraum"]))]
        ident_treffer = [r for liste in await asyncio.gather(*[suche(q) for q in q_ident])
                         for r in liste]
        identitaet, abgelehnt_i = phase_identitaet(ident_treffer, z)
        abgelehnt += abgelehnt_i
        if not _identitaet_reicht(identitaet):
            log.info("Technische Recherche: Identität '%s' NICHT belegt — keine Rückruf-/"
                     "Technikphase (%d Anfragen)", breit, anfragen)
            return TechnischeRecherche(ausgeloest_durch=ausgeloest_durch, identitaet=identitaet,
                                       provider_fehler=fehler and not ident_treffer,
                                       phasen=phasen, abgelehnte_fakten=abgelehnt,
                                       anfragen=anfragen)

        gen = identitaet.generation
        basis = " ".join(filter(None, [breit, gen]))
        # PHASE 2 — Rückrufe (amtlich/Fachquellen bevorzugt)
        phasen.append("rueckruf")
        rr = await asyncio.gather(
            suche(" ".join(filter(None, [basis, "Rückruf", jahr]))),
            suche(" ".join(filter(None, [breit, "Rückruf Rückrufaktion", jahr])),
                  include_domains=_RUECKRUF_DOMAINS))
        roh = {"rueckruf": [r for liste in rr for r in liste]}
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
        roh["schwachstelle"] = sw if isinstance(sw, list) else []
        roh["wartung"] = wa if isinstance(wa, list) else []
        fehler = fehler or isinstance(sw, Exception) or isinstance(wa, Exception)
        fakten = phase_fakten(roh, z, identitaet, ("rueckruf", "schwachstelle", "wartung"), abgelehnt)
        log.info("Technische Recherche: '%s' belegt (Gen=%s, Konf=%s), %d Fakten, %d verworfen, "
                 "%d Anfragen", breit, gen, identitaet.identitaet_konfidenz, len(fakten),
                 len(abgelehnt), anfragen)
        return TechnischeRecherche(ausgeloest_durch=ausgeloest_durch, identitaet=identitaet,
                                   fakten=fakten, provider_fehler=fehler, phasen=phasen,
                                   abgelehnte_fakten=abgelehnt, anfragen=anfragen)


# ── Fixture-Provider (Tests) ─────────────────────────────────────────────────

class FixtureTechnicalResearchProvider:
    """Deterministischer Provider für Tests — kein Netzwerk. Durchläuft DIESELBE
    phasenweise Auswertung wie der Tavily-Provider: die Fixture-Liste
    "identitaet" speist Phase 1, "rueckruf" Phase 2, "schwachstelle"/"wartung"
    Phase 3 — und Phase 2/3 laufen nur nach belegter Identität."""

    def __init__(self, treffer: dict[str, list[dict]] | None = None, *, fehler: bool = False):
        self._treffer = treffer or {}
        self._fehler = fehler
        self.phasen_aufrufe: list[str] = []

    async def recherchiere(self, *, marke, modell, baujahr, motor,
                           ausgeloest_durch, ziel: dict | None = None) -> TechnischeRecherche:
        if self._fehler:
            return TechnischeRecherche(ausgeloest_durch=ausgeloest_durch, provider_fehler=True)
        z = _ziel(marke, modell, baujahr, motor, ziel)
        abgelehnt: list[dict] = []
        self.phasen_aufrufe.append("identitaet")
        identitaet, abgelehnt_i = phase_identitaet(list(self._treffer.get("identitaet") or []), z)
        abgelehnt += abgelehnt_i
        if not _identitaet_reicht(identitaet):
            return TechnischeRecherche(ausgeloest_durch=ausgeloest_durch, identitaet=identitaet,
                                       phasen=["identitaet"], abgelehnte_fakten=abgelehnt)
        self.phasen_aufrufe += ["rueckruf", "technik"]
        roh = {k: list(self._treffer.get(k) or []) for k in ("rueckruf", "schwachstelle", "wartung")}
        fakten = phase_fakten(roh, z, identitaet, ("rueckruf", "schwachstelle", "wartung"), abgelehnt)
        return TechnischeRecherche(ausgeloest_durch=ausgeloest_durch, identitaet=identitaet,
                                   fakten=fakten, phasen=["identitaet", "rueckruf", "technik"],
                                   abgelehnte_fakten=abgelehnt)


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
    try:
        return await provider.recherchiere(
            marke=getattr(req, "marke", None), modell=getattr(req, "modell", None),
            baujahr=getattr(req, "baujahr", None), motor=getattr(req, "motor", None),
            ausgeloest_durch=trigger, ziel=_ziel_aus_request(req))
    except TypeError:
        # Ältere Provider ohne `ziel`-Parameter.
        try:
            return await provider.recherchiere(
                marke=getattr(req, "marke", None), modell=getattr(req, "modell", None),
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
