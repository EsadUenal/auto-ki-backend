from __future__ import annotations

"""
Marktvergleich 2.0 — deterministische, ehrliche Preisbewertung (Phase 1).

Problem des alten Standes: die Marktspanne (marktpreis_min/max) wurde vollständig
vom LLM aus rohen Tavily-Snippets ERFUNDEN, und der Marktvergleich-Insight zeigte
allgemeine Suchseiten als scheinbare "Vergleichsfahrzeuge" — auch dann, wenn die
verlinkte Seite Fahrzeuge anderer Generation/Baujahr/Motorisierung/km enthielt
(die berüchtigte "7.000-€-Karre" neben einer 23.000-€-Spanne). Das beschädigt
Vertrauen.

Datenlage (real geprüft): Tavily (search_depth=basic) liefert überwiegend Such-/
Übersichtsseiten, KEINE sauberen Einzelinserate je URL (raw_content=null). Die
Snippet-TEXTE enthalten aber häufig mehrere echte Angebote als "Preis + km + EZ".
Wir können daraus deterministisch einzelne Preis-DATENPUNKTE extrahieren, ihre
Vergleichbarkeit gegen das Zielfahrzeug bewerten (Generation/Baujahr/km/Kraftstoff)
und daraus robuste Statistik (Median + Quartils-Spanne) berechnen.

Ehrlichkeitsgrundsätze:
- Keine erfundenen Fahrzeuge — nur was aus dem Snippet-Text extrahierbar war.
- Fremde Generationen (z.B. E90/F30 beim G20) werden verworfen, nicht mitgemittelt.
- Ausreißer verzerren die Spanne nicht (Median + Quartile statt min/max).
- Wenige/schwache Daten -> niedrige Datenqualität, KEINE Scheinpräzision.
- Eine allgemeine Suchseite bleibt Recherchequelle, wird aber NIE als einzelnes
  Vergleichsfahrzeug ausgegeben.
"""

import logging
import re
import statistics
from urllib.parse import urlparse

from app.models import Marktanalyse, Preisbeobachtung
from app.web_search import ist_info_domain as _ist_info_domain
from app.web_search import ist_einzelinserat as _ist_einzelinserat
from app.web_search import ist_kategorieseite as _ist_kategorieseite_intern

log = logging.getLogger(__name__)

# ── Plausibilitätsgrenzen (Sicherheitsnetz gegen Fehl-Extraktion) ────────────
_PREIS_MIN = 1_500
_PREIS_MAX = 250_000
_KM_MAX = 500_000
_JAHR_MIN = 1990
_JAHR_MAX = 2027   # aktuelles Jahr + 1 (EZ-Neuwagen)

# Zeichen-Fenster um einen Preis-Treffer, in dem km/Baujahr als zum selben Angebot
# gehörend gewertet werden. Bewusst eng — lieber ein Feld None lassen (reduziert die
# Vergleichbarkeit) als km/Baujahr eines NACHBAR-Inserats falsch zuzuordnen.
_FENSTER = 130

# Vergleichbarkeits-Stufen (absteigend). Index = "Abwertungs-Distanz".
_STUFEN = ["sehr_aehnlich", "aehnlich", "bedingt", "ungeeignet"]
_UNGEEIGNET = len(_STUFEN) - 1

# Maximale relative Breite des typischen Marktbereichs (Spanne/Median). Ist die
# Streuung der "vergleichbaren" Preise größer, sind die extrahierten Datenpunkte
# in Wahrheit NICHT kohärent (z.B. gemischte Cash-/Finanzierungs-/Gesamtpreise
# derselben Anzeige, oder Fehl-Assoziation Preis↔km) — dann KEIN Schein-Median
# ausgeben, sondern ehrlich auf die unzuverlässige Datenbasis hinweisen.
_MAX_REL_SPANNE = 0.6

# §9: Marker im Preisumfeld, die einen Preis als Finanzierungs-/Leasing-/Monatsrate
# ODER Neuwagen-/Listenpreis entlarven — solche Werte sind KEINE Gebrauchtwagen-
# Gesamtpreise und dürfen nicht in den Marktmedian einfließen.
_FINANZ_MARKERS = (
    "leasing", "finanzierung", "monatlich", "mtl.", "mtl ", "/monat", "pro monat",
    "im monat", "anzahlung", "monatsrate", "€/mon", "eur/mon", "raten ab", "/mon.",
    "neuwagen", "neupreis", "listenpreis", "uvp", "ab werk",
)

# Preis: Zahl mit Tausenderpunkten ODER 4–6 Ziffern, GEFOLGT von € / EUR.
_RE_PREIS = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d{4,6})\s*(?:€|eur\b)", re.IGNORECASE)
# Kilometer: Zahl (mit/ohne Tausenderpunkt), gefolgt von km.
_RE_KM = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d{2,6})\s*km\b", re.IGNORECASE)
# Baujahr / Erstzulassung: "EZ 04/2020" (zuverlässigstes Signal) sowie
# "Baujahr 2020" / "aus 2020" / "BJ 2020".
_RE_EZ = re.compile(r"ez\s*\d{1,2}/((?:19|20)\d{2})", re.IGNORECASE)
_RE_BJ = re.compile(r"(?:baujahr|bj\.?|aus)\s*((?:19|20)\d{2})", re.IGNORECASE)
_RE_JAHR = re.compile(r"\b((?:19|20)\d{2})\b")

# Generations-/Chassis-Code (BMW E/F/G/U.., Mercedes W/S/C/A/X.., Audi B/C/D..).
_RE_CODE = re.compile(r"\b([a-z]\d{2,3})\b", re.IGNORECASE)

_KRAFTSTOFF_WORTE = {
    "diesel": ("diesel", "tdi", "cdi", "hdi", "dci", "bluetec"),
    "benzin": ("benzin", "tsi", "tfsi", "gti", "petrol"),
    "elektro": ("elektro", "electric", "ev "),
    "hybrid": ("hybrid", "phev", "plug-in", "plugin"),
}


def _zahl(roh: str) -> int:
    return int(roh.replace(".", "").replace(" ", ""))


def _domain(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
    except Exception:
        return ""
    return netloc[4:] if netloc.startswith("www.") else netloc


def _generation_tokens(text: str) -> list[str]:
    """Baureihen-Kürzel wie 'g20', 'e90' als lowercase-Token-Liste."""
    if not text:
        return []
    out: list[str] = []
    for t in re.split(r"[^a-z0-9]+", text.lower()):
        if re.fullmatch(r"[a-z]\d{2,3}", t):
            out.append(t)
    return out


def _wort_tokens(text: str) -> set[str]:
    """Alle alphanumerischen Wort-Token eines Textes (lowercase)."""
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t}


def _modell_tokens(name: str) -> set[str]:
    """Unterscheidungskräftige Modell-Token aus einem Modellnamen ODER einer
    Motorvarianten-Bezeichnung — strukturiert, KEIN Fahrzeug-Hardcoding.

    Behalten werden nur Token mit mindestens einem Buchstaben und Länge >= 3
    ('insignia', 'mokka', 'golf', 'glc', '3er', '320d', 'c200'). Bewusst verworfen:
    reine Zahlen ('200', '220' — mehrdeutig über Modelle hinweg) und Einzelbuchstaben
    ('c', 'e' — die Mercedes-Klassenbuchstaben sind ohne Zahl nicht trennscharf).
    So bleibt 'GLC' von 'C-Klasse', '5er' von '3er', 'Passat' von 'Golf' trennbar.
    """
    out: set[str] = set()
    for t in re.split(r"[^a-z0-9]+", (name or "").lower()):
        if len(t) >= 3 and not t.isdigit() and re.search(r"[a-z]", t):
            out.add(t)
    return out


def _num_modell_tokens(name: str) -> set[str]:
    """3-stellige Modell-Zahlen aus einer Bezeichnung ('320d'->'320', '530i'->'530',
    'c200'->'200', '911'->'911').

    Markenzahlen sind marken-INTERN unterscheidungskräftig (BMW 320 vs 520), aber
    marken-ÜBERGREIFEND mehrdeutig. Deshalb werden sie NUR marken-skopiert als
    Fremdsignal genutzt (siehe baue_ziel/marke_tokens) — nie markenübergreifend."""
    return set(re.findall(r"\d{3}", (name or "").lower()))


def _marke_tokens(marke: str) -> set[str]:
    """Marken-Wörter (>=2 Zeichen, lowercase) für den marken-skopierten Zahlenabgleich.
    'Mercedes-Benz' -> {'mercedes','benz'}, 'BMW' -> {'bmw'}."""
    return {t for t in re.split(r"[^a-z0-9]+", (marke or "").lower()) if len(t) >= 2}


def _ist_fremdmodell(worte: set[str], ziel: dict) -> str | None:
    """Zentrale, strukturierte Fremdmodell-Erkennung (alpha + marken-skopierte Zahl).

    Rückgabe: das erkannte Fremd-Token (Grund) oder None. Ein Text ist NUR dann fremd,
    wenn er ein Fremdmodell nennt und KEIN Zielmodell-Signal trägt. Für Zahlen zusätzlich
    marken-skopiert: die Zielmarke muss im Text stehen (kein markenübergreifender
    Zahlen-Fehlschluss wie Peugeot 508 vs BMW).
    """
    ziel_model = ziel.get("modell_tokens") or set()
    if worte & ziel_model:
        return None  # klares Zielsignal -> nie verwerfen
    # (a) Alpha-Fremdmodell (mokka, 5er, glc, passat, 520d …)
    alpha = worte & (ziel.get("fremd_modelle") or set())
    if alpha:
        return sorted(alpha)[0]
    # (b) Marken-skopierte Fremd-Zahl (BMW 520 im 320er-Check) — nur wenn die
    #     Zielmarke im Text steht und keine Zielzahl vorkommt.
    marke_tokens = ziel.get("marke_tokens") or set()
    ziel_num = ziel.get("ziel_num") or set()
    fremd_num = ziel.get("fremd_num") or set()
    if (marke_tokens & worte) and (fremd_num & worte) and not (ziel_num & worte):
        return sorted(fremd_num & worte)[0]
    return None


def _kraftstoff_im_text(text: str) -> str | None:
    t = text.lower()
    for norm, keys in _KRAFTSTOFF_WORTE.items():
        if any(k in t for k in keys):
            return norm
    return None


def _ist_finanzierungspreis(fenster: str) -> bool:
    """True, wenn das Preisumfeld einen Finanzierungs-/Leasing-/Monats- oder
    Neuwagenpreis-Marker enthält (§9) — dann ist der Betrag KEIN belastbarer
    Gebraucht-Gesamtpreis und wird nicht als Marktbeobachtung gezählt."""
    t = fenster.lower()
    return any(m in t for m in _FINANZ_MARKERS)


def _naechster(treffer: list[re.Match], pos: int) -> re.Match | None:
    """Der zum Preis (an Position `pos`) nächstgelegene Treffer innerhalb _FENSTER."""
    best = None
    best_dist = _FENSTER + 1
    for m in treffer:
        d = abs(m.start() - pos)
        if d <= _FENSTER and d < best_dist:
            best, best_dist = m, d
    return best


def _extrahiere_aus_text(text: str, url: str, source_type: str = "unknown") -> list[Preisbeobachtung]:
    """Alle (Preis[, km][, Baujahr])-Datenpunkte aus EINEM Snippet-Text.

    Assoziation rein positionsbasiert: km/Baujahr werden nur übernommen, wenn sie
    innerhalb eines engen Fensters um den Preis stehen. Ordnung/Reihenfolge der
    Felder variiert je Portal — das enge Fenster hält Fehlzuordnungen klein; im
    Zweifel bleibt ein Feld None (senkt später die Vergleichbarkeit).

    `source_type` (§10-§13): "listing" | "category" | "unknown" — wird unverändert an
    jede extrahierte Beobachtung durchgereicht (bestimmt später, ob sie Richtung
    HIGH/Quellenvielfalt zählen darf).
    """
    if not text:
        return []
    km_treffer = list(_RE_KM.finditer(text))
    ez_treffer = list(_RE_EZ.finditer(text))
    bj_treffer = list(_RE_BJ.finditer(text))
    jahr_treffer = list(_RE_JAHR.finditer(text))
    domain = _domain(url)

    out: list[Preisbeobachtung] = []
    for pm in _RE_PREIS.finditer(text):
        preis = _zahl(pm.group(1))
        if not (_PREIS_MIN <= preis <= _PREIS_MAX):
            continue
        pos = pm.start()

        km_m = _naechster(km_treffer, pos)
        km = None
        if km_m:
            k = _zahl(km_m.group(1))
            if 0 <= k <= _KM_MAX:
                km = k

        jahr = None
        for treffer in (ez_treffer, bj_treffer, jahr_treffer):   # zuverlässigste Quelle zuerst
            jm = _naechster(treffer, pos)
            if jm:
                y = int(jm.group(1))
                if _JAHR_MIN <= y <= _JAHR_MAX:
                    jahr = y
                    break

        # Lokales Textfenster (für Generations-/Kraftstoff-Prüfung der Vergleichbarkeit).
        fenster = text[max(0, pos - _FENSTER): pos + _FENSTER]
        # §9: Finanzierungs-/Leasing-/Monats-/Neuwagenpreise nicht als Marktbeobachtung.
        if _ist_finanzierungspreis(fenster):
            continue
        out.append(_roh_beobachtung(preis, km, jahr, domain, url, fenster, source_type))
    return out


# Roh-Beobachtung trägt das lokale Textfenster vorübergehend in `gruende[0]` mit,
# damit die Vergleichbarkeits-Bewertung darauf zugreifen kann (wird dort entfernt).
def _roh_beobachtung(preis, km, jahr, domain, url, fenster, source_type="unknown") -> Preisbeobachtung:
    return Preisbeobachtung(
        preis_eur=preis, kilometerstand=km, baujahr=jahr,
        quelle_domain=domain, quelle_url=url, source_type=source_type,
        vergleichbarkeit="", gruende=[f"\x00{fenster}"],
    )


def _bewerte(b: Preisbeobachtung, ziel: dict) -> Preisbeobachtung:
    """Deterministische Vergleichbarkeit einer Beobachtung gegen das Zielfahrzeug."""
    fenster = ""
    if b.gruende and b.gruende[0].startswith("\x00"):
        fenster = b.gruende[0][1:]
    gruende: list[str] = []
    stufe = 0  # 0 = sehr_aehnlich

    def ab(n: int):
        nonlocal stufe
        stufe = min(_UNGEEIGNET, stufe + n)

    # ── HARTE MODELLTREUE (zuerst) — Root-Cause #5 ────────────────────────────
    # Nennt das lokale Preis-Umfeld ein FREMDES Modell (anderes Modell irgendeiner
    # Marke, inkl. Motor-Verkaufsbezeichnung '520d' UND marken-skopierter Zahl '520')
    # und NICHT das Zielmodell, wird der Datenpunkt hart verworfen.
    fremd_grund = _ist_fremdmodell(_wort_tokens(fenster), ziel)
    if fremd_grund:
        b.vergleichbarkeit = "ungeeignet"
        b.gruende = [f"anderes Modell im Preisumfeld ({fremd_grund})"]
        return b

    codes = _generation_tokens(fenster)
    ziel_gen = ziel.get("generation_tokens") or set()
    fremd_gen = ziel.get("fremd_generationen") or set()
    hat_ziel = any(c in ziel_gen for c in codes)
    hat_fremd = any(c in fremd_gen for c in codes)

    if hat_fremd and not hat_ziel:
        fremd = next(c for c in codes if c in fremd_gen)
        b.vergleichbarkeit = "ungeeignet"
        b.gruende = [f"andere Generation ({fremd.upper()})"]
        return b
    if hat_ziel:
        gruende.append(f"Generation bestätigt ({next(c for c in codes if c in ziel_gen).upper()})")
    else:
        ab(1)
        gruende.append("Generation nicht bestätigt")

    ziel_bj = ziel.get("baujahr")
    if b.baujahr is not None and ziel_bj:
        d = abs(b.baujahr - ziel_bj)
        if d <= 1:
            gruende.append(f"Baujahr passt (±{d})")
        elif d == 2:
            ab(1); gruende.append("Baujahr ±2 Jahre")
        else:
            b.vergleichbarkeit = "ungeeignet"
            b.gruende = [f"Baujahr weicht stark ab ({b.baujahr} vs {ziel_bj})"]
            return b
    else:
        ab(1); gruende.append("Baujahr unbekannt")

    ziel_km = ziel.get("kilometerstand")
    if b.kilometerstand is not None and ziel_km:
        d = abs(b.kilometerstand - ziel_km)
        if d <= 20_000:
            gruende.append("Laufleistung vergleichbar")
        elif d <= 40_000:
            ab(1); gruende.append("Laufleistung mäßig abweichend")
        elif d <= 70_000:
            ab(2); gruende.append("Laufleistung deutlich abweichend")
        else:
            b.vergleichbarkeit = "ungeeignet"
            b.gruende = [f"Laufleistung weicht stark ab ({b.kilometerstand:,} km)".replace(",", ".")]
            return b
    else:
        ab(1); gruende.append("Laufleistung unbekannt")

    ziel_kr = _kraftstoff_im_text(ziel.get("kraftstoff") or "")
    kr_text = _kraftstoff_im_text(fenster)
    if ziel_kr and kr_text and kr_text != ziel_kr:
        ab(1); gruende.append(f"anderer Kraftstoff ({kr_text})")

    b.vergleichbarkeit = _STUFEN[stufe]
    b.gruende = gruende
    return b


def _cap_pro_url(beob: list[Preisbeobachtung], max_pro_url: int = 5) -> list[Preisbeobachtung]:
    """Begrenzt den Beitrag EINER Rechercheseite (§9/§12): Aggregat-/Übersichtsseiten
    liefern oft viele, teils mis-assoziierte Preis/km/Baujahr-Tripel (drei verschiedene
    Preise mit identischem km — der km gehört in Wahrheit nur zu EINEM Inserat). Ohne
    Deckelung dominiert eine einzelne verrauschte Seite die Statistik und verbreitert
    die Streuung künstlich. Reihenfolge (spezifischste zuerst) bleibt erhalten."""
    zaehler: dict[str, int] = {}
    out: list[Preisbeobachtung] = []
    for b in beob:
        u = b.quelle_url or ""
        zaehler[u] = zaehler.get(u, 0) + 1
        if zaehler[u] <= max_pro_url:
            out.append(b)
    return out


def _trim_ausreisser(beob: list[Preisbeobachtung]) -> tuple[list, list]:
    """Robuster Kern der Preisbeobachtungen um den Median (MAD + relatives Sanity-Band).

    Ersetzt den reinen Tukey-Zaun: bei der aus Snippet-Text extrahierten Datenbasis
    treten NICHT nur einzelne Ausreißer auf, sondern strukturelles Rauschen (Fehl-
    Extraktionen wie ein 1.631-€-Fragment, mis-assoziierte Aggregat-Preise, gemischte
    Trims). Der Median-basierte MAD-Zaun ist gegen solche Kontamination robuster als
    der Quartils-basierte Tukey-Zaun und identifiziert den kohärenten Marktkern —
    genau das, was für einen belastbaren Marktwert (§0/§3) nötig ist.

    - Relatives Sanity-Band: ein WIRKLICH vergleichbares Fahrzeug liegt selten unter
      ~55 % oder über ~165 % des Medians (entfernt Fragmente/Fremdpreise hart).
    - MAD-Zaun (median. absolute Abweichung, skaliert): entfernt den bimodalen
      Rand (z.B. deutlich teurere, unmarkierte höherwertige Angebote).
    Gibt (behalten, entfernt) zurück. Bricht NICHT vorschnell ab, wenn viel Rauschen
    vorliegt — der kohärente Kern IST das Ziel; bleibt zu wenig übrig, greift später
    der Streuungs-Guard und stuft ehrlich als unzuverlässig ein.
    """
    if len(beob) < 4:
        return beob, []
    preise = [b.preis_eur for b in beob]
    med = statistics.median(preise)
    if med <= 0:
        return beob, []
    lo_band, hi_band = med * 0.55, med * 1.65
    abw = statistics.median([abs(p - med) for p in preise])
    if abw > 0:
        lo_mad, hi_mad = med - 3.0 * 1.4826 * abw, med + 3.0 * 1.4826 * abw
        lo, hi = max(lo_band, lo_mad), min(hi_band, hi_mad)
    else:
        lo, hi = lo_band, hi_band
    behalten = [b for b in beob if lo <= b.preis_eur <= hi]
    entfernt = [b for b in beob if not (lo <= b.preis_eur <= hi)]
    # Bleibt ein zu kleiner Kern übrig, war die Filterung zu aggressiv für diese
    # Datenlage -> lieber ungetrimmt weitergeben (der Streuungs-Guard entscheidet dann).
    if len(behalten) < max(4, len(beob) // 4):
        return beob, []
    return behalten, entfernt


def _typischer_bereich(preise: list[int]) -> tuple[int, int]:
    """Robuster typischer Marktbereich = unteres/oberes Quartil (Ausreißer-fest),
    auf 100 € gerundet. Bei sehr wenigen Werten min/max des verwendeten Sets."""
    s = sorted(preise)
    if len(s) >= 4:
        q = statistics.quantiles(s, n=4, method="inclusive")  # [p25, p50, p75]
        lo, hi = q[0], q[2]
    else:
        lo, hi = s[0], s[-1]
    return _r100(lo), _r100(hi)


def _r100(x: float) -> int:
    return int(round(x / 100.0) * 100)


def _datenqualitaet(verwendet: list[Preisbeobachtung], median: int | None,
                    lo: int | None, hi: int | None) -> str:
    """Datenqualität aus einer belastbaren MISCHUNG (§14/§15) — nicht aus einer
    starren Trefferzahl allein:

      - mehrere wirklich vergleichbare Fahrzeuge (Anzahl + Anteil sehr ähnlich/ähnlich)
      - Quellenvielfalt (mehrere unabhängige Portale/Domains)
      - Attribut-VOLLständigkeit (Baujahr UND km je Datenpunkt — siehe Empirie-Hinweis)
      - kontrollierte Preisstreuung (Quartilsspanne relativ zum Median)
      - Anteil ECHTER Einzelinserate (source_type == "listing"), sofern vorhanden

    "hoch" ist der ANGESTREBTE Normalfall für gängige Fahrzeuge und wird ehrlich nur
    vergeben, wenn diese Mischung erreicht ist. "mittel" ist die seltene Ausnahme mit
    weiterhin belastbarer Basis. Sonst "niedrig" (= nicht auslieferbar, §0/§4).

    HARTE INVARIANTE (§14, nicht verhandelbar): 0 "sehr_aehnlich" + 0 "aehnlich" kann
    NIEMALS "hoch" ergeben — ausschließlich "bedingt" passende Treffer sind fachlich
    keine belastbare Basis für die höchste Qualitätsstufe, unabhängig von Trefferzahl
    oder Quellenvielfalt.

    EMPIRIE-HINWEIS (Reliability-Sprint 3, per Live-Diagnose mit scripts/
    diagnose_recherche.py): Tavilys Basic-Suche liefert für diese Query-Art so gut
    wie NIE echte Einzelinserat-Detail-URLs (source_type=="listing") — real
    beobachtet: 0 von 22 verwendeten Datenpunkten beim BMW-320d-Testfall, obwohl die
    Basis inhaltlich stark war (21/23 mit Baujahr UND km, Median stabil). Ein hartes
    `listing_n`-Gate für HOCH wäre daher für praktisch JEDES Fahrzeug unerreichbar
    gewesen — das hätte §0 (verbreitete Fahrzeuge erreichen normalerweise HOCH)
    direkt verletzt. Statt der (mit dieser Datenquelle strukturell fast nie
    erreichbaren) Seiten-URL-Klassifikation zählt daher das stärkste tatsächlich
    beobachtbare Signal für "das ist ein konkretes Einzelfahrzeug": Baujahr UND
    Kilometerstand GEMEINSAM am selben Datenpunkt extrahiert (nicht nur eines von
    beiden) — eine Zeile wie "BMW 320d 2019, 89.000 km, 24.900 €" innerhalb einer
    Such-/Kategorieseite ist inhaltlich ein echtes Einzelangebot, auch wenn die
    Trägerseite selbst eine Kategorieseite ist. Echte Einzelinserat-URLs (falls
    Tavily sie doch liefert) zählen zusätzlich mit voller Kraft.
    """
    n = len(verwendet)
    if n == 0 or not median:
        return "niedrig"
    domains = {b.quelle_domain for b in verwendet if b.quelle_domain}
    mit_attr = sum(1 for b in verwendet if b.baujahr is not None or b.kilometerstand is not None)
    quellenvielfalt = len(domains)
    attr_ratio = mit_attr / n
    rel_spanne = (hi - lo) / median if (lo is not None and hi is not None and median) else 1.0
    sehr_n = sum(1 for b in verwendet if b.vergleichbarkeit == "sehr_aehnlich")
    aehn_n = sum(1 for b in verwendet if b.vergleichbarkeit == "aehnlich")
    listing_n = sum(1 for b in verwendet if b.source_type == "listing")
    # §Phase 1-3/6: Kategorie-/Aggregatorseiten-Herkunft darf selbst mit vollem
    # Baujahr+km-Attribut NICHT als "konkretes Einzelfahrzeug" zählen (harte
    # Abgrenzung von der reinen "unknown"-Fallback-Heuristik) — in der echten
    # Pipeline erreichen solche Punkte `verwendet` inzwischen ohnehin nicht mehr
    # (analysiere_markt filtert source_type=="category" bereits vor `kandidaten`
    # heraus); diese Bedingung ist ein zusätzliches Sicherheitsnetz für direkte
    # Aufrufer dieser Funktion (z.B. Tests).
    beide_n = sum(1 for b in verwendet
                  if b.baujahr is not None and b.kilometerstand is not None
                  and b.source_type != "category")
    # "Konkretes Einzelfahrzeug"-Signal: echte Listing-URL ODER vollständiges
    # Baujahr+km-Paar am Datenpunkt (siehe Empirie-Hinweis oben) — je nachdem, was
    # mehr zählt (nie doppelt gezählt).
    konkret_n = max(listing_n, beide_n)

    if sehr_n == 0 and aehn_n == 0:
        # Ausschließlich "bedingt" passende Treffer (§14-Beispiel: Insignia-Fall,
        # 7/7 bedingt). Maximal "mittel" — und nur mit einer minimalen Basis aus
        # konkreten Einzelfahrzeugen, sonst "niedrig".
        if n >= 4 and quellenvielfalt >= 2 and konkret_n >= 2 and rel_spanne <= _MAX_REL_SPANNE:
            return "mittel"
        return "niedrig"

    # Alle Datenpunkte in `verwendet` sind mindestens "ähnlich" (Baujahr/km im Rahmen,
    # kein Fremdmodell). Die echten Qualitätssignale sind daher: Menge, Quellenvielfalt
    # (unabhängige Portale), ENGE Streuung UND ein Mindestmaß an konkreten
    # Einzelfahrzeug-Datenpunkten (§12: reine Kategorie-/Statistik-Angaben ohne jedes
    # Einzelfahrzeug-Attribut dürfen HOCH nicht allein tragen).
    #
    # (a) Viele Treffer, >=2 Portale, überwiegend attributvollständig, sehr enge Streuung.
    if n >= 8 and quellenvielfalt >= 2 and attr_ratio >= 0.5 and rel_spanne <= 0.30 and konkret_n >= n * 0.5:
        return "hoch"
    # (b) Etwas weniger Treffer, dafür breitere Quellenvielfalt (>=3 Portale), enge Streuung.
    if n >= 6 and quellenvielfalt >= 3 and attr_ratio >= 0.5 and rel_spanne <= 0.40 and konkret_n >= n * 0.5:
        return "hoch"
    # Seltene Ausnahme (§3): belastbare Basis, aber nicht "hoch" — weniger Treffer/
    # Vielfalt oder etwas breitere (noch kontrollierte) Streuung.
    if n >= 4 and quellenvielfalt >= 2 and rel_spanne <= _MAX_REL_SPANNE:
        return "mittel"
    return "niedrig"


def modell_relevant(r: dict, ziel: dict) -> bool:
    """Best-effort-Filter für die ANGEZEIGTEN Rechercheseiten (belege/Kontext):
    verwirft eine Seite, die klar ein FREMDES Modell nennt und NICHT das Zielmodell
    (z.B. 'BMW 4er' / 'Mercedes C-Klasse' im 3er-Check). Neutrale Seiten (kein klares
    Modellsignal) bleiben als Recherchequelle erhalten. Die harte Vergleichs-Filterung
    läuft ohnehin datenpunktgenau in _bewerte — dies verbessert nur die Quellenanzeige.
    """
    worte = _wort_tokens(f"{r.get('title', '')} {r.get('content', '')}")
    if worte & (ziel.get("modell_tokens") or set()):
        return True
    # Fremdmodell (alpha ODER marken-skopierte Zahl) klar erkannt -> raus.
    return _ist_fremdmodell(worte, ziel) is None


def baue_ziel(baureihe: dict | None, motor_match: dict | None, req,
              alle_baureihen: list[dict] | None,
              alle_motorvarianten: list[dict] | None = None) -> dict:
    """Baut das Ziel-Profil für die Vergleichbarkeitsbewertung.

    `fremd_generationen` = Generations-Kürzel ANDERER Baureihen desselben Modells
    (z.B. E90/F30/E46 beim 3er) — daten-getrieben aus der DB, kein Hardcoding. Ein
    Datenpunkt, dessen Snippet ein Fremd-Kürzel trägt, wird als 'ungeeignet'
    verworfen (die berüchtigte E90-7.000-€-Karre beim G20).

    `modell_tokens` / `fremd_modelle` = HARTE Modelltreue (Root-Cause #5): trennt
    das Zielmodell strukturiert von ALLEN anderen Modellen (jeder Marke) inkl. deren
    Motor-Verkaufsbezeichnungen. Ein Opel Mokka darf nie in den Insignia-Vergleich,
    ein 5er nie in den 3er, ein GLC nie in die C-Klasse — daten-getrieben, kein
    Modell-Hardcoding.
    """
    gen: set[str] = set()
    if baureihe:
        gen |= set(_generation_tokens(baureihe.get("generation", "")))
        gen |= set(_generation_tokens(baureihe.get("id", "")))
    fremd: set[str] = set()
    if baureihe:
        bm = (baureihe.get("marke") or "").lower()
        bmod = (baureihe.get("modell") or "").lower()
        for r in alle_baureihen or []:
            if (r.get("marke", "").lower() == bm and r.get("modell", "").lower() == bmod
                    and r.get("id") != baureihe.get("id")):
                fremd |= set(_generation_tokens(r.get("generation", "")))
                fremd |= set(_generation_tokens(r.get("id", "")))
    fremd -= gen

    # ── Modelltreue: Ziel- vs. Fremd-Modell-Token (inkl. Motorbezeichnungen) ──
    ziel_id = baureihe.get("id") if baureihe else None
    modell_tokens: set[str] = set()
    fremd_modelle: set[str] = set()
    marke_tokens: set[str] = set()
    ziel_num: set[str] = set()
    fremd_num: set[str] = set()
    if baureihe:
        bm = (baureihe.get("marke") or "").lower()
        marke_tokens = _marke_tokens(baureihe.get("marke", ""))
        id_marke = {r.get("id"): (r.get("marke") or "").lower() for r in (alle_baureihen or [])}

        modell_tokens |= _modell_tokens(baureihe.get("modell", ""))
        ziel_num |= _num_modell_tokens(baureihe.get("modell", ""))
        for m in alle_motorvarianten or []:
            if m.get("baureihe_id") == ziel_id:
                modell_tokens |= _modell_tokens(m.get("bezeichnung", ""))
                ziel_num |= _num_modell_tokens(m.get("bezeichnung", ""))
        for r in alle_baureihen or []:
            if r.get("id") != ziel_id:
                fremd_modelle |= _modell_tokens(r.get("modell", ""))
                # Fremd-Zahlen NUR aus Baureihen DERSELBEN Marke (markeninterner Zahlenraum).
                if (r.get("marke") or "").lower() == bm:
                    fremd_num |= _num_modell_tokens(r.get("modell", ""))
        for m in alle_motorvarianten or []:
            if m.get("baureihe_id") != ziel_id:
                fremd_modelle |= _modell_tokens(m.get("bezeichnung", ""))
                if id_marke.get(m.get("baureihe_id")) == bm:
                    fremd_num |= _num_modell_tokens(m.get("bezeichnung", ""))
        # Alles, was auch zum Ziel gehört, ist KEIN Fremdsignal (gemeinsame Trim-/
        # Klassenwörter, geteilte Zahlen wie Mercedes C200/GLC200 -> beide '200').
        fremd_modelle -= modell_tokens
        fremd_num -= ziel_num

    kraftstoff = (motor_match or {}).get("kraftstoff") or getattr(req, "kraftstoff", None)
    return {
        "generation_tokens": gen,
        "fremd_generationen": fremd,
        "modell_tokens": modell_tokens,
        "fremd_modelle": fremd_modelle,
        "marke_tokens": marke_tokens,
        "ziel_num": ziel_num,
        "fremd_num": fremd_num,
        "baujahr": getattr(req, "baujahr", None),
        "kilometerstand": getattr(req, "kilometerstand", None),
        "kraftstoff": kraftstoff,
    }


def prompt_block(ma: Marktanalyse | None) -> str:
    """Kompakter, VERBINDLICHER Marktvergleich-Block für den LLM-Prompt — damit der
    Bericht dieselben (deterministisch berechneten) Zahlen nennt und keine eigene
    Spanne erfindet. Leer, wenn keine belastbare Analyse vorliegt."""
    if not ma or not ma.median_eur:
        return ""
    lines = [
        "=== DETERMINISTISCHER MARKTVERGLEICH (Backend-berechnet — VERBINDLICH) ===",
        f"Robust aus {ma.verwendet} vergleichbaren Web-Preisangaben berechnet "
        f"({ma.anzahl_sehr_aehnlich} sehr ähnlich, {ma.anzahl_aehnlich} ähnlich):",
        f"- Median-Marktwert: {ma.median_eur} €",
        f"- Typischer Marktbereich (robuste Quartile): {ma.spanne_min_eur}–{ma.spanne_max_eur} €",
        f"- Datenqualität dieser Basis: {ma.datenqualitaet}",
        f"Verwende GENAU diese Spanne: setze marktpreis_min={ma.spanne_min_eur} und "
        f"marktpreis_max={ma.spanne_max_eur}. Erfinde KEINE davon abweichende Spanne. "
        f"Nenne im Bericht den Median und den typischen Marktbereich; leite die "
        f"Preisbewertung aus der Lage des Angebotspreises zu dieser Spanne ab.",
    ]
    return "\n".join(lines)


def analysiere_markt(web_results: list[dict], ziel: dict, angebot_eur: int | None) -> Marktanalyse:
    """Baut die deterministische Marktanalyse aus den rohen Tavily-Treffern.

    `ziel` erwartet: generation_tokens:set, fremd_generationen:set, baujahr:int|None,
    kilometerstand:int|None, kraftstoff:str|None. `angebot_eur` = Angebots-/Wunschpreis.
    """
    roh: list[Preisbeobachtung] = []
    for r in web_results or []:
        url = r.get("url", "") or ""
        # §9: Informations-/Ratgeber-/Nachschlage-Seiten (z.B. fahrzeugschein.de) sind
        # KEINE konkreten Vergleichsinserate — ihre Preisangaben zählen nicht in den
        # Median. Sie bleiben höchstens Hintergrundquelle (Kontext/belege), tragen aber
        # keinen Marktdatenpunkt bei.
        if _ist_info_domain(url):
            continue
        # §11: Herkunftsart EINMAL pro Seite bestimmen (Einzelinserat vs. Kategorie-/
        # Suchseite vs. unbekannt) — bestimmt später, ob die daraus extrahierten
        # Datenpunkte Richtung Quellenvielfalt/HIGH zählen dürfen.
        titel = r.get("title", "")
        if _ist_einzelinserat(url, titel):
            source_type = "listing"
        elif _ist_kategorieseite_intern(url, titel):
            source_type = "category"
        else:
            source_type = "unknown"
        # Raw-Content (falls angefordert) mitverwenden — mehr Text = mehr extrahierbare
        # Preis-Datenpunkte. Groß gedeckelt gegen pathologische Seitengrößen.
        raw = (r.get("raw_content") or "")[:20_000]
        text = f"{r.get('title','')}\n{r.get('content','')}\n{raw}"
        # §Phase 2: die enge Zeichen-Fenster-Zuordnung (_FENSTER=130, s.o.) ist die
        # bestehende Absicherung dafür, dass km/Baujahr eines Preises NICHT einem
        # NACHBAR-Fahrzeug im selben Snippet zugeordnet werden — ein Snippet mit
        # mehreren sauber je-Preis attribuierten Fahrzeugen (z.B. eine Portal-
        # Trefferliste mit "Preis X km Y EZ Z . Preis X2 km Y2 EZ Z2 . ...") bleibt
        # daher weiterhin je Fahrzeug einzeln zählbar (das ist die tragende
        # Sprint-3-Empirie, ohne die kaum ein Fahrzeug HOCH/MITTEL erreichen würde).
        # Der eigentliche Aggregator-/Kategorie-Fall (12gebrauchtwagen.de u.ä. —
        # eine Modell-/Motor-Suchseite OHNE einzeln geprüfte Inserate) wird bereits
        # auf URL-Ebene erkannt (ist_kategorieseite/ist_generische_suchseite) und
        # dort als source_type="category" markiert, s.o.
        roh.extend(_extrahiere_aus_text(text, url, source_type))

    bewertet = [_bewerte(b, ziel) for b in roh]
    # Deduplizieren auf (Preis, km, Baujahr) — dieselbe Anzeige taucht in mehreren
    # Snippets/Queries auf; sonst überzählt ein Portal die Statistik.
    gesehen: set[tuple] = set()
    uniq: list[Preisbeobachtung] = []
    for b in bewertet:
        key = (b.preis_eur, b.kilometerstand, b.baujahr)
        if key in gesehen:
            continue
        gesehen.add(key)
        uniq.append(b)

    # §Phase 1-3 (Kernfix): Kategorie-/Such-/Aggregatorseiten sind KEIN konkretes
    # Vergleichsfahrzeug — ihre Datenpunkte dürfen Median, Quartile, Similarity-
    # Zählung und Datenqualität NICHT beeinflussen. Sie bleiben ausschließlich als
    # transparent ausgewiesene Hintergrundquelle erhalten (hintergrund_domains).
    nutzbar = [b for b in uniq if b.source_type != "category"]
    hintergrund = [b for b in uniq if b.source_type == "category"]
    hintergrund_domains: list[str] = []
    for b in hintergrund:
        if b.quelle_domain and b.quelle_domain not in hintergrund_domains:
            hintergrund_domains.append(b.quelle_domain)

    sehr = [b for b in nutzbar if b.vergleichbarkeit == "sehr_aehnlich"]
    aehn = [b for b in nutzbar if b.vergleichbarkeit == "aehnlich"]
    bedingt = [b for b in nutzbar if b.vergleichbarkeit == "bedingt"]

    # Für die Preisberechnung bevorzugt sehr_aehnlich + aehnlich; "bedingt" nur als
    # Fallback bei zu wenig Daten. "ungeeignet" NIE.
    kandidaten = sehr + aehn
    fallback = False
    if len(kandidaten) < 3:
        kandidaten = kandidaten + bedingt
        fallback = True

    # Rausch-Reduktion (TIGHTENING der Relevanz, KEIN Loosening): Datenpunkte OHNE
    # jedes Attribut (weder Baujahr NOCH km extrahierbar) stammen oft aus Übersichts-
    # zeilen ohne echten Fahrzeugbezug und verbreitern die Preisspanne künstlich —
    # was den Streuungs-Guard fälschlich auslöst und ein populäres Fahrzeug auf
    # "niedrig" drückt. Tragen genügend Datenpunkte ein Attribut, werden die
    # attribut-losen verworfen (nur wenn danach noch eine belastbare Basis bleibt).
    mit_attr = [b for b in kandidaten if b.baujahr is not None or b.kilometerstand is not None]
    if len(mit_attr) >= 5:
        kandidaten = mit_attr

    # §9/§12: Beitrag einer einzelnen (oft aggregierenden) Rechercheseite deckeln,
    # damit eine verrauschte Übersichtsseite die Statistik nicht dominiert.
    kandidaten = _cap_pro_url(kandidaten)

    # Robusten Marktkern bilden (MAD + Sanity-Band) — bevor gezählt/gemittelt/angezeigt wird.
    verwendet, _entfernt = _trim_ausreisser(kandidaten)

    # Domains NUR der tatsächlich verwendeten Vergleiche (keine Quelle nennen, die
    # nichts Verwertbares beigetragen hat) — order-preserving dedupliziert.
    domains: list[str] = []
    for b in verwendet:
        if b.quelle_domain and b.quelle_domain not in domains:
            domains.append(b.quelle_domain)

    ma = Marktanalyse(
        gefunden=len(uniq),
        # Kategorie-Zähler beschreiben die TATSÄCHLICH verwendeten (post-Trim)
        # Vergleiche — nicht die Rohmenge (ehrliche "X sehr ähnlich · Y ähnlich").
        anzahl_sehr_aehnlich=sum(1 for b in verwendet if b.vergleichbarkeit == "sehr_aehnlich"),
        anzahl_aehnlich=sum(1 for b in verwendet if b.vergleichbarkeit == "aehnlich"),
        anzahl_bedingt=sum(1 for b in verwendet if b.vergleichbarkeit == "bedingt"),
        angebot_eur=angebot_eur,
        quellen_domains=domains,
        hintergrund_domains=hintergrund_domains,
    )

    def _unzuverlaessig(grund: str) -> Marktanalyse:
        # KEINE Scheinpräzision. Median/Spanne bleiben None; die Datenpunkte werden
        # dennoch als (schwache) Beobachtungen ausgewiesen (Transparenz).
        ma.verwendet = len(verwendet)
        ma.datenqualitaet = "niedrig"
        ma.methode = grund
        ma.beobachtungen = verwendet
        return ma

    if len(verwendet) < 3:
        return _unzuverlaessig(
            "Zu wenige vergleichbare Preisangaben aus der Websuche für eine belastbare "
            "Median-/Quartils-Berechnung — Marktanalyse auf begrenzter Datenbasis."
        )

    preise = [b.preis_eur for b in verwendet]
    median = _r100(statistics.median(preise))
    lo, hi = _typischer_bereich(preise)

    # Plausibilitätsprüfung Streuung: ist der typische Marktbereich relativ zum
    # Median zu breit, sind die Datenpunkte in Wahrheit nicht kohärent (Fehl-
    # Assoziation / gemischte Preisarten). Dann ehrlich als unzuverlässig ausweisen
    # statt einen bedeutungslosen Median zu präsentieren.
    if median and (hi - lo) / median > _MAX_REL_SPANNE:
        return _unzuverlaessig(
            f"Die gefundenen Vergleichspreise streuen zu stark "
            f"({lo:,}–{hi:,} €) für einen belastbaren Marktwert — die Web-Datenbasis "
            f"ist uneinheitlich (z.B. gemischte Angebots-/Finanzierungspreise). "
            f"Nur grobe Orientierung möglich.".replace(",", ".")
        )

    ma.verwendet = len(verwendet)
    ma.median_eur = median
    ma.spanne_min_eur = lo
    ma.spanne_max_eur = hi
    ma.datenqualitaet = _datenqualitaet(verwendet, median, lo, hi)
    ma.beobachtungen = verwendet
    if angebot_eur:
        ma.differenz_eur = angebot_eur - median
        ma.differenz_pct = round((angebot_eur - median) / median * 100, 1)
    ma.methode = (
        f"Median und typischer Marktbereich (unteres–oberes Quartil, ausreißer-robust) "
        f"aus {len(verwendet)} vergleichbaren Preisangaben"
        + (" (inkl. bedingt vergleichbarer, da wenige Daten)" if fallback else "")
        + f"; aus {len(uniq)} extrahierten Datenpunkten der Websuche gefiltert."
    )
    return ma
