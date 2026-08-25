from __future__ import annotations

"""
Gemeinsame Hilfsfunktionen für Kauf-Check und Verkaufs-Check:
  - Baureihe + Motor per Score-Matching in der DB erkennen
  - DB-Kontext-String aufbauen (Specs, Schwachstellen, Rückrufe)
  - Gemini (JSON-Modus) aufrufen
"""

import json
import logging
import re

from google import genai
from google.genai import types as genai_types

from app.config import GEMINI_API_KEY, LLM_MODEL
from app.database import get_baureihe, get_conn, get_alle_baureihen_kurz, get_alle_motorvarianten_kurz
from app.gemini_retry import with_retry, GeminiFehlgeschlagen
from app.recall_filter import gefilterte_rueckrufe, _baujahr_passt
from app.motor_applicability import gefilterte_schwachstellen

log = logging.getLogger(__name__)

_MARKEN_ALIAS = {
    "VW": "Volkswagen",
    "MERCEDES": "Mercedes-Benz",
    "MERC": "Mercedes-Benz",
    "BENZ": "Mercedes-Benz",
    "OPEL": "Opel",
    "SKODA": "Škoda",
}


# ---------- Verkaufsbezeichnungs-Normalisierung / -Erkennung ----------
#
# Typische Nutzereingaben im "Modell"-Feld sind Verkaufsbezeichnungen wie
# "C 200", "C200", "C-200", "GLC 200", "E 220 d", "CLA 200". Ohne Normalisierung
# schlägt der Motorvarianten-Abgleich fehl ("c 200" != DB-Bezeichnung "C200"),
# und ein naiver Substring-Vergleich lässt kurze Präfixe in längere durchbluten
# ("c 200" ist Substring von "glc 200" -> C-Klasse-Eingabe trifft fälschlich GLC).

def _norm_bezeichnung(s: str) -> str:
    """Kleinschreibung, Leer-/Bindestriche entfernt: 'C 200'/'C-200' -> 'c200'."""
    return re.sub(r"[\s\-]+", "", (s or "").lower())


# Verkaufsbezeichnung = Buchstaben-Präfix (Baureihe: C/E/A/S/GLC/CLA/…) + 2–3-
# stellige Klassenzahl (180/200/220/300/43/63) + optionaler Rest (d/e/4matic/…).
# Bewusst NICHT auf 1-stellige Zahlen (Audi "A4") — das sind keine Mercedes-
# Klassenkennungen und sollen den generischen Pfad nutzen.
_KENNUNG = re.compile(r"^([a-z]{1,3})(\d{2,3})([a-z0-9]*)$")


def _parse_kennung(s: str) -> tuple[str, str, str] | None:
    """Zerlegt eine normalisierte Verkaufsbezeichnung in (präfix, klasse, rest).

    'C 200' -> ('c','200',''); 'GLC 200' -> ('glc','200',''); 'E 220 d' ->
    ('e','220','d'); 'C200 4MATIC' -> ('c','200','4matic'). Kein Präfix/keine
    passende Struktur (z.B. BMW '320d', Motorcode 'B47D20') -> None.
    """
    m = _KENNUNG.match(_norm_bezeichnung(s))
    if not m:
        return None
    return (m.group(1), m.group(2), m.group(3))


def _modell_trifft_motor(ml_in: str, ml_norm: str,
                         ml_kenn: tuple[str, str, str] | None,
                         bez: str, code: str) -> bool:
    """Ob die Modell-Eingabe zu einer Motorvariante (bezeichnung/motorcode) passt.

    Kernregel: Ist die Eingabe eine Verkaufsbezeichnung (Präfix+Klasse), MUSS der
    Buchstaben-Präfix EXAKT übereinstimmen ('c' != 'glc', 'cla' != 'a') — kein
    Durchbluten kurzer Präfixe in längere. Für nicht-kennungsartige Eingaben
    (BMW '320d', Motorcodes) bleibt der normalisierte Exakt-/Teilstring-Match.
    """
    code_norm = _norm_bezeichnung(code)
    if code_norm and len(code_norm) >= 3 and ml_norm == code_norm:
        return True

    bez_norm = _norm_bezeichnung(bez)
    if not bez_norm:
        return False

    if ml_kenn is not None:
        # Verkaufsbezeichnung -> präfix-exakter Klassen-Abgleich.
        bez_kenn = _parse_kennung(bez)
        if bez_kenn is None:
            return False
        return ml_kenn[0] == bez_kenn[0] and ml_kenn[1] == bez_kenn[1]

    # Nicht-Kennung (numerische/technische Eingabe): normalisierter Exakt-Match,
    # oder Teilstring nur bei hinreichend spezifischer Eingabe (>=3 Zeichen), damit
    # kein 1-Zeichen-Präfix wie 'c' in 'glc200' blutet.
    if ml_norm == bez_norm:
        return True
    if len(ml_norm) >= 3 and (ml_norm in bez_norm or bez_norm in ml_norm):
        return True
    return False


def _motor_exakt(ml_norm: str, ml_kenn, bez: str, code: str) -> bool:
    """Ob die Modelleingabe eine Motorvariante EXAKT trifft (nicht nur teilweise).

    `_modell_trifft_motor` akzeptiert bewusst auch Teilstrings, damit unvollstaendige
    Eingaben noch greifen. Genau dort entstehen aber falsche Fahrzeuge: die Eingabe
    "Golf GTI" (normalisiert "golfgti") enthaelt die Bezeichnung "GTI" der Baureihe
    `volkswagen-up-up` — der Kaufcheck landete damit beim VW up!. Ebenso trifft
    "e-tron" die Bezeichnung "RS e-tron GT".

    Diese Funktion unterscheidet deshalb den belastbaren Exakttreffer vom
    Teilstring; sie AENDERT das Matching nicht, sie bewertet es nur.
    """
    code_norm = _norm_bezeichnung(code)
    if code_norm and len(code_norm) >= 3 and ml_norm == code_norm:
        return True
    bez_norm = _norm_bezeichnung(bez)
    if not bez_norm:
        return False
    if ml_kenn is not None:
        # Verkaufsbezeichnung: praefix- UND klassengenau ist bereits exakt genug
        # ("C 200" -> "C200", "GLC 200" -> "GLC 200").
        bez_kenn = _parse_kennung(bez)
        return bez_kenn is not None and ml_kenn[0] == bez_kenn[0] and ml_kenn[1] == bez_kenn[1]
    return ml_norm == bez_norm


# ---------- Identitaets-Vertrauen (Identity-Trust-Gate) ----------
#
# Der Trust-Audit hat einen False-Positive-Pfad belegt: von acht erfundenen
# Modellnamen loesten sieben auf eine reale Baureihe auf ("BMW iX7" -> bmw-x7-g07,
# "Audi A4711" -> audi-a4-b9). Ursache ist die Substring-Regel im Scoring
# (`ml in rl or rl in ml`, +4 Punkte) in Verbindung damit, dass der Score zwar
# geloggt, danach aber verworfen wurde — der Aufrufer bekam ein blankes dict und
# konnte einen exakten Treffer nicht von einem zufaelligen Teilstring unterscheiden.
#
# Die Substring-Logik wird NICHT entfernt (sie traegt legitime Eingaben wie
# "3er Touring" oder "A4 Avant"). Stattdessen wird die MATCH-ART festgehalten und
# dem Aufrufer zurueckgegeben, der daraus selbst entscheidet.
#
# Gemessene Trennschaerfe ueber legitime und erfundene Eingaben:
#   token_inner ("x7" steckt IN "ix7", "a4" IN "a4711") trifft ausschliesslich
#   FALSCHE Zuordnungen — kein einziger legitimer Fall im Bestand nutzt diesen
#   Pfad. Er kann deshalb bedenkenlos als unsicher gelten.

MATCH_EXACT = "exact"                 # normalisiertes Modell == DB-Modell
MATCH_MOTOR_ALIAS = "motor_alias"     # Motorbezeichnung/-code EXAKT getroffen
MATCH_GENERATION = "generation_match" # Nutzer nennt die Generation, sie trifft
MATCH_STRONG = "strong"               # Substring auf Tokengrenze, Rest erklaerbar
MATCH_AMBIGUOUS = "ambiguous"         # mehrere gleichwertige Kandidaten
MATCH_SUBSTRING = "substring_only"    # Substring mit unerklaertem Restwort
MATCH_TOKEN_INNER = "token_inner"     # Treffer steckt INNERHALB eines Tokens
MATCH_MARKE_ONLY = "marke_only"       # kein Modell angegeben — reiner Marken-Rateweg
MATCH_NONE = "no_match"

# Nur diese Arten duerfen eine fahrzeugspezifische Aussage tragen.
MATCH_VERTRAUENSWUERDIG = frozenset({MATCH_EXACT, MATCH_MOTOR_ALIAS,
                                     MATCH_GENERATION, MATCH_STRONG})

# Guete der Match-Arten (hoeher = belastbarer). Wird nur fuer die
# Mehrdeutigkeits-Pruefung gebraucht: ein exakter Treffer wird nicht dadurch
# unsicher, dass eine schwaecher passende Zeile zufaellig denselben Score erreicht
# ("Audi Q8" steht punktgleich neben "Q8 e-tron" und "RS Q8").
_MATCH_RANG = {
    MATCH_EXACT: 4,
    MATCH_MOTOR_ALIAS: 3,
    MATCH_GENERATION: 3,
    MATCH_STRONG: 2,
    MATCH_SUBSTRING: 1,
    MATCH_MARKE_ONLY: 1,
    MATCH_TOKEN_INNER: 0,
    MATCH_AMBIGUOUS: 0,
    MATCH_NONE: 0,
}

KONFIDENZ_HOCH = "hoch"
KONFIDENZ_NIEDRIG = "niedrig"

_TOKEN = re.compile(r"[^a-z0-9]+")


def _tokens(s: str | None) -> list[str]:
    return [t for t in _TOKEN.split((s or "").lower()) if t]


_karosserie_vokabular_cache: frozenset[str] | None = None


def _karosserie_vokabular() -> frozenset[str]:
    """Alle Aufbau-/Karosseriewoerter, die IRGENDWO in der DB vorkommen.

    Dient als Erklaerung fuer Restwoerter: "3er Touring" -> Rest {"touring"} ist
    ein bekannter Aufbau, die Eingabe bleibt damit vertrauenswuerdig. "Golf XV" ->
    Rest {"xv"} ist es nicht.

    Bewusst DATENGETRIEBEN statt als hartkodierte Wortliste — das Vokabular waechst
    mit der DB mit und behauptet nichts, was nicht im Bestand steht. Bewusst GLOBAL
    statt pro Baureihe: ein Kombi heisst je nach Hersteller Touring, Avant, Variant
    oder Kombi, und die einzelne Zeile fuehrt meist nur ihre eigene Schreibweise.
    """
    global _karosserie_vokabular_cache
    if _karosserie_vokabular_cache is not None:
        return _karosserie_vokabular_cache
    woerter: set[str] = set()
    for r in get_alle_baureihen_kurz():
        roh = r.get("karosserie")
        werte = roh if isinstance(roh, list) else None
        if werte is None and isinstance(roh, str) and roh.strip():
            try:
                geladen = json.loads(roh)
                werte = geladen if isinstance(geladen, list) else [roh]
            except (ValueError, TypeError):
                werte = [roh]
        for v in werte or []:
            woerter.update(_tokens(str(v)))
    _karosserie_vokabular_cache = frozenset(w for w in woerter if len(w) >= 1)
    return _karosserie_vokabular_cache


def _substring_art(modell: str, r_modell: str) -> str | None:
    """Klassifiziert einen Substring-Treffer — oder None, wenn keiner vorliegt.

    Drei Faelle:
      MATCH_EXACT        exakt gleich
      MATCH_TOKEN_INNER  das DB-Modell steckt INNERHALB eines Nutzer-Tokens
                         (bzw. umgekehrt) — "x7" in "ix7", "a4" in "a4711".
                         Gemessen: ausschliesslich falsche Zuordnungen.
      MATCH_STRONG       Tokengrenze eingehalten und jedes ueberzaehlige Wort ist
                         ein bekanntes Aufbauwort -> "3er Touring", "A4 Avant"
      MATCH_SUBSTRING    Tokengrenze eingehalten, aber ein Restwort bleibt
                         unerklaert -> "Golf XV", "Corolla Hyperdrive"
    """
    ml, rl = (modell or "").strip().lower(), (r_modell or "").strip().lower()
    if not ml or not rl:
        return None
    if ml == rl:
        return MATCH_EXACT
    if not (ml in rl or rl in ml):
        return None
    ml_t, rl_t = _tokens(ml), _tokens(rl)
    # Tokengrenze: die Tokenfolge der kuerzeren Seite muss vollstaendig in der
    # laengeren als GANZE Tokens vorkommen. Sonst klebt der Treffer im Wortinneren.
    kurz, lang = (rl_t, ml_t) if len(rl_t) <= len(ml_t) else (ml_t, rl_t)
    if not set(kurz) <= set(lang):
        return MATCH_TOKEN_INNER
    rest = [t for t in lang if t not in kurz]
    if not rest:
        return MATCH_EXACT          # gleiche Tokens, andere Schreibweise
    vokabular = _karosserie_vokabular()
    if all(t in vokabular for t in rest):
        return MATCH_STRONG
    return MATCH_SUBSTRING


# ---------- Fahrzeug-Erkennung ----------

def _find_baureihe_scored(marke: str | None, modell: str | None,
                          baujahr: int | None) -> tuple[dict | None, str]:
    """Kern der Baureihen-Erkennung: liefert Treffer UND Match-Art.

    Das Scoring selbst ist gegenueber der Vorfassung UNVERAENDERT — es wird nur
    zusaetzlich festgehalten, warum ein Kandidat gewonnen hat. Damit liefert
    `find_baureihe` weiterhin exakt dieselbe Baureihe wie bisher."""
    # Gecacht (60s TTL, siehe database.py) statt bei jedem Kauf-/Verkaufscheck die
    # komplette Tabelle neu zu lesen.
    rows = get_alle_baureihen_kurz()

    if marke:
        marke = _MARKEN_ALIAS.get(marke.upper().strip(), marke.strip())

    # Der eingegebene "Modell"-String ist häufig KEIN Baureihen-Name ("3er"), sondern eine
    # Motorbezeichnung ("320d") oder ein Motorcode ("B47"). Diese den zugehörigen Baureihen
    # zuordnen — sonst findet z.B. "320d" nie den 3er (dessen modell "3er" heißt) und die
    # Erkennung fällt auf einen reinen Marke+Baujahr-Treffer zurück (der eigentliche Bug:
    # "320d" -> BMW M4, weil M4 dieselbe Marke hat und das Baujahr im Bauzeitraum liegt).
    motor_baureihe_ids: set[str] = set()
    # Zusaetzlich (Identity-Trust-Gate): welche Baureihen wurden EXAKT ueber
    # Bezeichnung/Motorcode getroffen — im Unterschied zu einem blossen Teilstring.
    motor_exakt_ids: set[str] = set()
    if modell:
        ml_in   = modell.strip().lower()
        ml_norm = _norm_bezeichnung(ml_in)     # 'C 200' -> 'c200'
        ml_kenn = _parse_kennung(ml_in)         # ('c','200',…) oder None (z.B. '320d')
        for m in get_alle_motorvarianten_kurz():
            bez  = (m.get("bezeichnung") or "").strip().lower()
            code = (m.get("motorcode") or "").strip().lower()
            if _modell_trifft_motor(ml_in, ml_norm, ml_kenn, bez, code):
                motor_baureihe_ids.add(m["baureihe_id"])
                if _motor_exakt(ml_norm, ml_kenn, bez, code):
                    motor_exakt_ids.add(m["baureihe_id"])

    # §4: Nennt der Nutzer die Generation selbst ("Golf VII", "330i G20", "A3 8V"),
    # ist das direkte Evidenz und muss die Baureihenwahl mittragen. Bisher wurde
    # NUR gegen `r["modell"]` verglichen ("Golf") — die Generationsangabe blieb
    # wirkungslos. Verglichen wird tokenweise und EXAKT: ein Teilstring-Match
    # wuerde "VII" in "VIII" finden und damit genau die falsche Generation
    # bevorzugen.
    gen_tokens_user = {t for t in re.split(r"[^a-z0-9]+", (modell or "").lower()) if t}

    # Match-Art je Kandidat (Identity-Trust-Gate): das Scoring bleibt UNVERAENDERT,
    # es wird nur zusaetzlich festgehalten, WARUM ein Kandidat getroffen hat.
    scored: list[tuple[int, bool, bool, dict]] = []
    arten: dict[str, str] = {}
    for r in rows:
        score = 0
        marke_ok = marke is None
        modell_getroffen = False
        art = MATCH_NONE
        if marke:
            if r["marke"].lower() == marke.lower():
                score += 4
                marke_ok = True
            elif marke.lower() in r["marke"].lower() or r["marke"].lower() in marke.lower():
                score += 2
                marke_ok = True
        if modell:
            ml, rl = modell.lower(), r["modell"].lower()
            if ml == rl or ml in rl or rl in ml:
                score += 4
                modell_getroffen = True
                art = _substring_art(modell, r["modell"]) or MATCH_SUBSTRING
        # Motorvarianten-Treffer nur werten, wenn die Marke passt (kein Cross-Brand-Match,
        # z.B. ein VW-"2.0 TDI" darf keine BMW-Baureihe matchen).
        if marke_ok and r["id"] in motor_baureihe_ids:
            if not modell_getroffen:
                score += 4
            modell_getroffen = True
            # Ein EXAKT normalisierter Treffer auf Bezeichnung/Motorcode ist ein
            # starkes Signal ("320d", "C 200" -> "C200", "TT RS" -> "TTRS").
            # Ein blosser Teilstring ist es NICHT: "Golf GTI" trifft ueber
            # bez "GTI" die Baureihe `volkswagen-up-up`, "e-tron" trifft
            # "RS e-tron GT". Beides sind falsche Fahrzeuge — deshalb wird nur
            # der Exakttreffer hochgestuft.
            if r["id"] in motor_exakt_ids:
                art = MATCH_MOTOR_ALIAS
            elif art in (MATCH_NONE, MATCH_SUBSTRING, MATCH_TOKEN_INNER):
                art = MATCH_SUBSTRING
        # §4: explizite Generationsangabe des Nutzers (nur bei passender Marke).
        if marke_ok and gen_tokens_user:
            r_gen_tokens = {t for t in re.split(r"[^a-z0-9]+",
                                                (r.get("generation") or "").lower()) if t}
            if r_gen_tokens & gen_tokens_user:
                score += 3
                # Die Generation ist die spezifischste Angabe, die ein Nutzer
                # machen kann ("Golf VII", "3er G20") — sie hebt einen sonst nur
                # substring-artigen Treffer auf eine belastbare Stufe.
                if art != MATCH_EXACT:
                    art = MATCH_GENERATION
        # §2/§3: Ein FEHLENDER Bauzeitraum bedeutet UNBEKANNT, nicht "passt zu
        # jedem Baujahr". Frueher wurde `None` per `or 0` / `or 9999` zu einem
        # universellen Zeitraum aufgeblasen — eine undatierte Zeile bekam damit
        # bei JEDEM Baujahr dieselben +5 wie eine sauber datierte und gewann den
        # Gleichstand allein ueber die DB-Zeilenreihenfolge (belegt an
        # `vw-golf-8`, das so die Baujahre 1995-2022 an sich zog).
        #
        # Der Punktwert bleibt unveraendert, damit undatierte Zeilen weiterhin
        # als Fallback dienen koennen (§8). Neu ist nur `jahr_belegt`: es
        # unterscheidet einen ECHTEN Datumstreffer von einem geschenkten und
        # entscheidet den Gleichstand.
        jahr_belegt = False
        jahr_ausserhalb = False
        if baujahr and score > 0:
            bvon_roh, bbis_roh = r["bauzeitraum_von"], r["bauzeitraum_bis"]
            datiert = bvon_roh is not None or bbis_roh is not None
            bvon = bvon_roh or 0
            bbis = bbis_roh or 9999
            if bvon <= baujahr <= bbis:
                score += 5
                jahr_belegt = datiert
            elif abs(bvon - baujahr) <= 2 or (bbis < 9999 and abs(bbis - baujahr) <= 2):
                score += 1
                jahr_ausserhalb = datiert
            else:
                # Weder im Zeitraum noch im Toleranzband: bei DATIERTER Zeile ein
                # klarer Widerspruch zur Identitaet (nur festgehalten, der Score
                # bleibt wie bisher unveraendert).
                jahr_ausserhalb = datiert
        if score > 0:
            if not modell:
                # Ohne Modellangabe bleibt nur Marke + Baujahr. Das ist ein
                # Rateweg, kein Treffer — er darf keine fahrzeugspezifische
                # Aussage tragen (bisher lieferte er dieselbe Sicherheit wie ein
                # exakter Modelltreffer).
                art = MATCH_MARKE_ONLY
            # §11: Ein Baujahr, das klar ausserhalb eines DATIERTEN Bauzeitraums
            # liegt, widerlegt die Identitaet — unabhaengig davon, wie gut der
            # Modellname passt (Audit-Fall "G20 mit Baujahr 1995"). Es wird KEINE
            # zweite Baujahresengine gebaut: es sind exakt die Werte aus dem
            # Scoring oben, nur nicht mehr verworfen.
            if baujahr and jahr_ausserhalb:
                art = MATCH_SUBSTRING if art in MATCH_VERTRAUENSWUERDIG else art
            arten[r["id"]] = art
            scored.append((score, modell_getroffen, jahr_belegt, dict(r)))

    # Ist ein Modell angegeben, NUR Baureihen mit echtem Modell- ODER Motor-Treffer zulassen.
    # Sonst gewinnt eine andere Baureihe derselben Marke allein über Marke+Baujahr.
    # Kein Treffer -> None -> saubere Web-Analyse statt eines falschen Profils.
    if modell:
        scored = [s for s in scored if s[1]]

    if not scored:
        return None, MATCH_NONE

    # Gleichstand: eine Zeile mit ECHT belegtem Bauzeitraum schlaegt eine
    # undatierte. Ohne diesen Schluessel entschied die DB-Zeilenreihenfolge.
    scored.sort(key=lambda x: (x[0], x[2]), reverse=True)
    best = scored[0][3]
    art = arten.get(best["id"], MATCH_NONE)

    # Mehrdeutigkeit: teilen sich mehrere Kandidaten die Spitze (gleicher Score UND
    # gleiche Baujahres-Belegung), entschied bisher allein die Zeilenreihenfolge der
    # DB — ein stiller Muenzwurf zwischen verschiedenen Fahrzeugen.
    #
    # Zwei Einschraenkungen halten die Pruefung praezise:
    #   * Nur VERSCHIEDENE Modelle zaehlen. Zwei Generationen desselben Modells
    #     (X1 F48/U11 im Wechseljahr) sind keine unklare Identitaet — dafuer gibt es
    #     die Baujahres-Applicability aus P0-2.
    #   * Ein Kandidat, der die Eingabe BESSER trifft als alle anderen der Spitze,
    #     gewinnt eindeutig. Sonst wuerde "Audi Q8" mehrdeutig, nur weil
    #     "Q8 e-tron" und "RS Q8" punktgleich danebenstehen.
    spitze = [x for x in scored if (x[0], x[2]) == (scored[0][0], scored[0][2])]
    # `token_inner` bleibt erhalten: die Diagnose "die Eingabe passt zu keinem
    # bekannten Modell" ist fuer den Nutzer konkreter als "mehrdeutig", und sie
    # wuerde sonst immer verdeckt (bei schlechtestem Rang gilt jeder Kandidat als
    # gleichwertig). Beide Arten gaten ohnehin identisch.
    if len(spitze) > 1 and art != MATCH_TOKEN_INNER:
        rang_best = _MATCH_RANG.get(art, 0)
        gleichwertig = [x for x in spitze
                        if _MATCH_RANG.get(arten.get(x[3]["id"], MATCH_NONE), 0) >= rang_best]
        modelle = {(x[3]["marke"].lower(), x[3]["modell"].lower()) for x in gleichwertig}
        if len(modelle) > 1:
            art = MATCH_AMBIGUOUS

    log.info("Baureihe erkannt: %s (score=%d, jahr_belegt=%s, match=%s)",
             best["id"], scored[0][0], scored[0][2], art)
    treffer = get_baureihe(best["marke"], best["modell"], best["generation"])
    return treffer, art


def find_baureihe(marke: str | None, modell: str | None, baujahr: int | None) -> dict | None:
    """Findet die Baureihe mit dem hoechsten Treffer-Score.

    Signatur und Rueckgabewert bewusst UNVERAENDERT: diese Funktion wird vom
    Verkaufscheck, von drei Diagnoseskripten und von zwei Testdateien genutzt.
    Wer die Verlaesslichkeit der Zuordnung braucht (Kaufcheck), nimmt
    `find_baureihe_mit_vertrauen`.
    """
    return _find_baureihe_scored(marke, modell, baujahr)[0]


def identitaet_konfidenz(match_art: str) -> str:
    """Darf diese Match-Art eine fahrzeugspezifische Aussage tragen?"""
    return KONFIDENZ_HOCH if match_art in MATCH_VERTRAUENSWUERDIG else KONFIDENZ_NIEDRIG


# Was dem Nutzer bei unsicherer Zuordnung am ehesten weiterhilft — je Match-Art
# genau EIN konkreter Hinweis, keine Aufzaehlung aller denkbaren Angaben.
FEHLENDE_ANGABE = {
    MATCH_TOKEN_INNER: "die exakte Modellbezeichnung (die Eingabe passt zu keinem "
                       "bekannten Modell dieser Marke)",
    MATCH_SUBSTRING:   "die genaue Modell- und Generationsbezeichnung "
                       "(z.B. \u201eGolf VII\u201c oder \u201e3er G20\u201c)",
    MATCH_AMBIGUOUS:   "die Generation bzw. den Baureihencode — mehrere Modelle "
                       "passen gleich gut",
    MATCH_MARKE_ONLY:  "das Modell (bisher liegt nur die Marke vor)",
    MATCH_NONE:        "Marke, Modell und Erstzulassung",
}


def find_baureihe_mit_vertrauen(marke: str | None, modell: str | None,
                                baujahr: int | None) -> tuple[dict | None, dict]:
    """Wie `find_baureihe`, liefert zusaetzlich die Verlaesslichkeit der Zuordnung.

    Rueckgabe: (baureihe|None, info) mit info = {
        "match_art":  eine der MATCH_*-Konstanten,
        "konfidenz":  "hoch" | "niedrig",
        "belastbar":  bool — darf eine fahrzeugspezifische Aussage tragen,
        "fehlende_angabe": str|None — was dem Nutzer zur Klaerung fehlt,
    }

    Die Baureihe wird auch bei niedriger Konfidenz MITGELIEFERT: der Aufrufer
    entscheidet, wofuer er sie noch verwendet (der Kaufcheck nutzt sie weiterhin
    fuer die Marktrecherche, aber nicht mehr fuer fahrzeugspezifische Aussagen).
    """
    treffer, art = _find_baureihe_scored(marke, modell, baujahr)
    if treffer is None:
        art = MATCH_NONE
    belastbar = art in MATCH_VERTRAUENSWUERDIG
    return treffer, {
        "match_art": art,
        "konfidenz": identitaet_konfidenz(art),
        "belastbar": belastbar,
        "fehlende_angabe": None if belastbar else FEHLENDE_ANGABE.get(art),
    }


# Kraftstoff aus einem freien Motor-Hint ableiten ("2.0 Diesel, 190 PS" -> diesel).
# Der normierte Wert wird gegen das kraftstoff-Feld der Motorvariante gematcht.
_KRAFTSTOFF_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("diesel",  ("diesel", "tdi", "cdi", "hdi", "dci", "bluetec")),
    ("hybrid",  ("hybrid", "phev", "plug-in", "plug in", "plugin")),
    ("elektro", ("elektro", "electric", " ev", "bev")),
    ("benzin",  ("benzin", "tsi", "tfsi", "gti", "otto", "petrol", "mpi")),
]


def _kraftstoff_aus_hint(h: str) -> str | None:
    """Normierter Kraftstoff aus dem Hint, oder None. Reihenfolge: spezifisch vor
    Benzin (ein 'Plug-in-Hybrid' soll nicht als Benzin durchgehen)."""
    for norm, keys in _KRAFTSTOFF_HINTS:
        if any(k in h for k in keys):
            return norm
    return None


def find_motor(baureihe: dict, hint: str | None) -> dict | None:
    """Findet die passende Motorvariante per Textabgleich.

    Signalreihenfolge: (1) direkte Bezeichnung/Motorcode, (2) Leistung — dabei die
    EINHEIT im Hint respektieren (eine PS-Angabe nur gegen leistung_ps, eine
    kW-Angabe nur gegen leistung_kw) und zusätzlich per Kraftstoff eingrenzen.

    Ohne diese Einheiten-Trennung kollidiert z.B. "190 PS" (320d, Diesel) mit
    "190 kW" (330i = 258 PS, Benzin): stand der 330i in der Motorliste vorn,
    gewann er über leistung_kw==190 und der Kaufcheck bekam Benzin-Specs für einen
    Diesel — widersprüchlicher Kontext, der die Analyse verfälscht.

    NORMALISIERUNG (Befund aus dem Technical-Web-Fallback-Test): Die
    Baureihenerkennung normalisiert Bezeichnungen seit jeher über
    `_norm_bezeichnung` ("C 200" -> "c200"), `find_motor` verglich dagegen rohe
    Strings. Für "Mercedes-Benz C 200" wurde die Baureihe deshalb gefunden, die
    real vorhandene Variante "C200" aber NICHT ("c 200" ist weder Teilstring von
    "c200" noch umgekehrt) — der Motor galt als unbekannt und löste unnötig den
    technischen Web-Fallback aus.

    Gemessen über alle 3.248 Motorvarianten und drei realistische Schreibweisen
    (Original, klein, ohne Leerzeichen): 1.515 der 9.624 Kombinationen fanden
    ihren eigenen Motor nicht, weitere 875 trafen den FALSCHEN — letzteres, weil
    die Teilstring-Suche vor jeder Exaktprüfung lief und z.B. "1.8 T quattro
    (150 PS)" an der Zeile "1.8 T (150 PS)" hängenblieb.

    Der Fix ist bewusst minimal: VOR der bestehenden Teilstring-Suche laufen zwei
    neue Durchgänge, die ausschließlich auf NORMALISIERTE GLEICHHEIT prüfen. Die
    bisherige Logik bleibt als Rückfallebene unverändert erhalten.

    Bewusst KEIN normalisierter Teilstring-Vergleich: die DB führt "C200" (Benzin,
    184 PS) und "C200 d" (Diesel, 136 PS) als getrennte Varianten. Normalisiert
    steckt "c200" in "c200d" — ein Teilstring-Vergleich auf der normalisierten Form
    würde je nach Zeilenreihenfolge den Diesel für einen Benziner ausgeben. Nur
    Gleichheit ist hier sicher.
    """
    if not hint or not baureihe["motoren"]:
        return None
    h = hint.lower()
    # Dieselbe Normalisierung wie in der Baureihenerkennung (§2: kein zweiter,
    # fast identischer Helper) — Leerzeichen und Bindestriche raus, klein.
    # Die Originalwerte bleiben unangetastet; normalisiert wird nur die
    # VERGLEICHSFORM.
    h_norm = _norm_bezeichnung(hint)

    if h_norm:
        # (1a) Normalisierte GLEICHHEIT der Bezeichnung — das präziseste Signal.
        #      "C 200" == "C200", "C 220 d" == "C220 d", "E 300 de" == "E300de".
        for m in baureihe["motoren"]:
            if _norm_bezeichnung(m["bezeichnung"]) == h_norm:
                return m
        # (1b) Normalisierte Gleichheit des Motorcodes. Mindestlänge 3 wie in
        #      `_modell_trifft_motor` — kürzere Codes wären zu unspezifisch.
        for m in baureihe["motoren"]:
            code_norm = _norm_bezeichnung(m.get("motorcode"))
            if code_norm and len(code_norm) >= 3 and code_norm == h_norm:
                return m

    # (1c) Bisheriger direkter Bezeichnungs-/Motorcode-Treffer — unverändert als
    #      Rückfallebene für Teileingaben ("320d" -> "320d xDrive").
    for m in baureihe["motoren"]:
        bez  = m["bezeichnung"].lower()
        code = (m["motorcode"] or "").lower()
        if h in bez or bez in h or (code and h in code):
            return m

    # (2) Leistungs-Treffer. Kandidaten zuerst per Kraftstoff eingrenzen (falls im
    #     Hint genannt) — sonst kann ein Benziner mit gleicher kW-Zahl einen Diesel
    #     mit gleicher PS-Zahl verdrängen.
    kandidaten = baureihe["motoren"]
    kraftstoff = _kraftstoff_aus_hint(h)
    if kraftstoff:
        gefiltert = [m for m in kandidaten if kraftstoff in (m.get("kraftstoff") or "").lower()]
        if gefiltert:
            kandidaten = gefiltert

    ps_match = re.search(r"(\d{2,3})\s*ps\b", h)
    if ps_match:
        val = int(ps_match.group(1))
        for m in kandidaten:
            if m.get("leistung_ps") == val:
                return m
    kw_match = re.search(r"(\d{2,3})\s*kw\b", h)
    if kw_match:
        val = int(kw_match.group(1))
        for m in kandidaten:
            if m.get("leistung_kw") == val:
                return m

    # (3) Keine Leistungsangabe, aber der Kraftstoff grenzt eindeutig auf genau
    #     einen Motor ein -> diesen nehmen.
    if kraftstoff and len(kandidaten) == 1:
        return kandidaten[0]

    return None


# ---------- DB-Kontext ----------

def build_db_context(baureihe: dict | None, motor_match: dict | None, baujahr: int | None = None,
                     fahrzeugkontext=None) -> str:
    """Baut den strukturierten DB-Kontext-String (Specs, Schwachstellen, Rückrufe).

    §Phase 7 (Reliability-Sprint 4): Rückrufe laufen NICHT mehr ungefiltert aus der
    DB in den Prompt — das war der Hauptleck-Punkt, durch den z.B. ein Hochvolt-/
    PHEV-Rückruf trotz erkanntem Diesel im LLM-Bericht auftauchen konnte, obwohl
    dieselbe Applicability-Prüfung ihn für die strukturierten Insights bereits
    korrekt herausfilterte. `gefilterte_rueckrufe` ist die EINE zentrale
    Allowed-List (app/recall_filter.py), die hier wie in evidence.py verwendet
    wird — inkl. der Applicability-Formulierung im Text (das LLM erfährt nicht nur
    DASS ein Rückruf gilt, sondern WIE sicher).

    KaufCheck-P0-2 (derselbe Bug-Musterfall, jetzt für Schwachstellen statt
    Rückrufe): `app/evidence.py::build_insights` filtert Baureihen- und
    Motor-Schwachstellen bereits über `_baujahr_passt` — ein Baujahr, das
    NACHWEISLICH nicht in die angegebene Baujahresspanne fällt, wird für die
    strukturierten Insights übersprungen. Dieser Prompt-Text tat das bislang
    NICHT und zeigte dem LLM jede Schwachstelle unabhängig vom angefragten
    Baujahr — Bericht und Insights konnten dadurch unterschiedliche Probleme
    nennen. Beide Ebenen (Baureihe UND Motor) nutzen jetzt dieselbe zentrale
    `_baujahr_passt`-Funktion (app/recall_filter.py) mit exakt derselben Regel
    wie evidence.py: nur ein eindeutiges `False` (nachweislich nicht zutreffend)
    schließt aus. `True` (eindeutig zutreffend), "Alle Baujahre" UND eine
    unklare/fehlende Angabe (beides `None`) bleiben — bewusst konservativ, keine
    strengere oder lockerere Regel als die bestehende.

    KaufCheck-P1-4 — `fahrzeugkontext` (optional, Vorgabe None): ein
    `Fahrzeugkontext`-Objekt (app/fahrzeugkontext.py) mit Segment, Generations-/
    Facelift-Merkmalen, Vorgänger und Wartungsintervallen. Der Audit hat gezeigt,
    dass diese seit jeher gepflegten DB-Felder den LLM-Kontext bislang GAR NICHT
    erreichten.

    Der Parameter ist bewusst OPT-IN und nicht automatisch aus `baureihe`
    abgeleitet: diese Funktion wird von Kauf- UND Verkaufscheck geteilt. Nur der
    Kaufcheck übergibt den Kontext; der Verkaufscheck-Prompt bleibt dadurch
    unverändert (dort ist der Marktwert das Produkt, nicht die Fahrzeugkunde)."""
    if baureihe is None:
        return "Kein DB-Profil für dieses Fahrzeug vorhanden."

    lines = [
        f"## DB-Profil: {baureihe.get('marke','')} {baureihe.get('modell','')} {baureihe.get('generation','')}",
        f"Bauzeitraum: {baureihe.get('bauzeitraum_von','?')}–{baureihe.get('bauzeitraum_bis') or 'heute'}",
        f"Karosserie: {', '.join(baureihe.get('karosserie') or [])}",
        f"TÜV-Mängelquote: {baureihe.get('tuev_maengelquote') or 'nicht erfasst'}",
        f"ADAC-Pannenkennziffer: {baureihe.get('adac_pannenkennziffer') or 'nicht erfasst'}",
        "",
    ]

    # P1-4: ergänzender Fahrzeugkontext, klar als NICHT-Evidence gekennzeichnet.
    # `prompt_block` liefert einen Leerstring, wenn nichts vorliegt — es entstehen
    # also keine leeren Abschnitte und keine "nicht erfasst"-Zeilen.
    if fahrzeugkontext is not None:
        from app.fahrzeugkontext import prompt_block as _fk_block
        _block = _fk_block(fahrzeugkontext)
        if _block:
            lines += [_block, ""]

    if baureihe.get("ausstattungslinien"):
        lines.append("Ausstattungslinien:")
        for a in baureihe["ausstattungslinien"]:
            lines.append(f"  {a.get('name','?')} ({a.get('typ','?')}): {a.get('abgrenzung') or ''}")
        lines.append("")

    motoren = [motor_match] if motor_match else baureihe.get("motoren", [])[:5]
    for m in motoren:
        lines += [
            f"### Motor: {m.get('bezeichnung','?')} | {m.get('motorcode','?')} | {m.get('kraftstoff','?')}",
            f"Leistung: {m.get('leistung_ps','?')} PS / {m.get('leistung_kw','?')} kW | Drehmoment: {m.get('drehmoment_nm','?')} Nm",
            f"0–100: {m.get('beschleunigung_0_100','?')} s | Vmax: {m.get('vmax_kmh','?')} km/h",
            f"Verbrauch WLTP: {m.get('verbrauch_wltp') or 'kein WLTP'} l/100km | Real: {m.get('verbrauch_real','?')} l/100km",
            f"Neupreis ca.: {m.get('neupreis_ca_eur') or 'nicht erfasst'} EUR",
            f"Tankgröße: {m.get('tankgroesse_liter') or 'nicht erfasst'} Liter | "
                f"Kofferraum: {m.get('kofferraum_liter') or 'nicht erfasst'} Liter",
            f"Anhängelast: {m.get('anhaengelast_gebremst_kg') or 'nicht erfasst'} kg gebremst / "
                f"{m.get('anhaengelast_ungebremst_kg') or 'nicht erfasst'} kg ungebremst | "
                f"Abgasnorm: {m.get('abgasnorm') or 'nicht erfasst'}",
        ]
        if m.get("batteriekapazitaet_kwh"):
            lines.append(f"Batteriekapazität: {m['batteriekapazitaet_kwh']} kWh")
        if m.get("felgengroesse_serie"):
            lines.append(f"Felgen (Serie): {m['felgengroesse_serie']}")
        # schwachstelle_motor hat KEIN schweregrad-Feld — nur bauteil/beschreibung/baujahre/kosten_ca
        # KaufCheck-P0-2: dieselbe Baujahres-Applicability wie evidence.build_insights
        # (nur ein eindeutiges False schließt aus — "Alle"/unklar/fehlend bleibt).
        motorprobleme = [s for s in (m.get("schwachstellen_motor") or [])
                         if _baujahr_passt(s.get("baujahre"), baujahr) is not False]
        if motorprobleme:
            lines.append("Motorprobleme:")
            for s in motorprobleme:
                lines.append(
                    f"  {s.get('bauteil','?')}: {s.get('beschreibung','?')} "
                    f"(Baujahre: {s.get('baujahre','?')}, Kosten ca.: {s.get('kosten_ca','?')})"
                )
        if m.get("kritische_wartung"):
            lines.append("Kritische Wartung:")
            for w in m["kritische_wartung"]:
                lines.append(f"  - {w.get('bauteil','?')}: {w.get('intervall','?')} — {w.get('hinweis','?')}")
        lines.append("")

    # schwachstelle_baureihe HAT schweregrad — trotzdem .get() für Robustheit
    # KaufCheck-P0-2: dieselbe Baujahres-Applicability wie evidence.build_insights.
    #
    # DATA-SAFETY-RUNTIME-GATE: zusätzlich dieselbe Motor-Allowed-List wie
    # evidence.build_insights (app/motor_applicability.py). Ohne diesen Aufruf
    # entstünde exakt der Fehler, den Reliability-Sprint 4 bei den Rückrufen
    # beheben musste: die strukturierten Insights wären sauber gefiltert, während
    # der LLM-Prompt die motorfremde Schwachstelle weiterhin roh zu sehen bekäme
    # und sie in Bericht und Checkliste schreiben würde.
    schwachstellen_baureihe = [
        s for s in gefilterte_schwachstellen(
            baureihe.get("schwachstellen_baureihe"), motor_match, baureihe)
        if _baujahr_passt(s.get("betroffene_baujahre"), baujahr) is not False
    ]
    if schwachstellen_baureihe:
        lines.append("### Schwachstellen Baureihe:")
        for s in schwachstellen_baureihe:
            schweregrad = s.get("schweregrad")
            prefix = f"[{schweregrad}] " if schweregrad else ""
            lines.append(
                f"  {prefix}{s.get('bauteil','?')}: {s.get('beschreibung','?')} "
                f"(Baujahre: {s.get('betroffene_baujahre','?')})"
            )
        lines.append("")

    # §Phase 7: NUR die zentral gefilterte Allowed-List (kein Antriebs-Widerspruch,
    # kein Baujahr-Ausschluss) — mit Applicability-Wortlaut statt nacktem Text.
    # KBA-Trust-Gate: `marke` aktiviert die markenübergreifende Kollisionsprüfung;
    # `kba_referenz_anzeige` (statt der rohen `kba_referenz`) ist None, wenn die
    # Referenz das Plausibilitätsgate nicht besteht — dann wird gar kein "(Ref: …)"
    # angehängt, statt eine unplausible Nummer anzuzeigen.
    erlaubte_rueckrufe = gefilterte_rueckrufe(baureihe.get("rueckrufe"), motor_match, baujahr,
                                              marke=baureihe.get("marke"))
    if erlaubte_rueckrufe:
        lines.append("### KBA-Rückrufe (nur für dieses Fahrzeug relevante):")
        for r in erlaubte_rueckrufe:
            ref = r.get("kba_referenz_anzeige")
            lines.append(f"  {r.get('datum','?')}: {r['text']}" + (f" (Ref: {ref})" if ref else ""))

    return "\n".join(lines)


# ---------- Gemini JSON-Aufruf ----------

_client: genai.Client | None = None


def get_gemini_client() -> genai.Client:
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY nicht gesetzt.")
        # Ohne expliziten Timeout kann eine gestörte Verbindung den Request unbegrenzt
        # hängen lassen. HttpOptions.timeout ist in Millisekunden (SDK-intern
        # verifiziert) — 90s statt 60s wie im Chat, da max_output_tokens=16384 für
        # Kauf-/Verkaufscheck-Berichte spürbar länger dauern kann.
        _client = genai.Client(
            api_key=GEMINI_API_KEY,
            http_options=genai_types.HttpOptions(timeout=90_000),
        )
    return _client


_JSON_STRING_ENDE = re.compile(r'^\s*[,\]\}:]')


def _escape_json_strings(raw: str) -> str:
    """
    Escapt literal Newlines/Tabs die INNERHALB von JSON-String-Werten stehen.
    Gemini gibt bei langen Texten manchmal raw \\n statt \\\\n aus.

    Behandelt außerdem nicht escapte Anführungszeichen MITTEN in einem String
    (z.B. schreibt Gemini im Fließtext `auf "Vollausstattung" prüfen` mit
    literalen Zitat-Anführungszeichen statt `\\"Vollausstattung\\"`). Ein reines
    Toggle bei jedem `"` würde den String an dieser Stelle fälschlich beenden und
    den Rest des JSON zerstören. Deshalb: bei einem `"` innerhalb eines bereits
    offenen Strings nur dann wirklich schließen, wenn direkt danach (ggf. nach
    Leerraum) ein JSON-Strukturzeichen (`,` `]` `}` `:`) oder das Textende folgt —
    sonst ist es ein literales Zitat-Zeichen und wird escaped.
    """
    out: list[str] = []
    in_str = False
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == "\\" and in_str and i + 1 < n:
            out.append(ch)
            out.append(raw[i + 1])
            i += 2
            continue
        if ch == '"':
            if not in_str:
                in_str = True
                out.append(ch)
            else:
                rest = raw[i + 1:]
                if rest.strip() == "" or _JSON_STRING_ENDE.match(rest):
                    in_str = False
                    out.append(ch)
                else:
                    out.append('\\"')
            i += 1
            continue
        elif in_str and ch == "\n":
            out.append("\\n")
        elif in_str and ch == "\r":
            out.append("\\r")
        elif in_str and ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


# Gemini vergisst gelegentlich das Komma zwischen dem "bericht"-Wert (der oft mit einem
# Satzzeichen endet, z.B. "...prüfen.") und dem nächsten Schema-Key — z.B.
# `..."\n  "empfehlung": ...` statt `...",\n  "empfehlung": ...`. Muss VOR
# _escape_json_strings laufen: die Anführungszeichen-Heuristik dort erkennt so ein
# Schema-Key-Anführungszeichen ohne vorheriges Komma sonst fälschlich als literales
# Zitat mitten im String und escaped es, statt den String korrekt zu beenden.
_FEHLENDES_KOMMA_MUSTER = re.compile(
    r'"(\s*)"(bericht|empfehlung|preis_bewertung|marktpreis_min|marktpreis_max)":'
)


def _repariere_fehlendes_komma(raw: str) -> str:
    return _FEHLENDES_KOMMA_MUSTER.sub(r'",\1"\2":', raw)


# Fängt den Fall ab, dass Gemini trotz response_mime_type=json gelegentlich reines
# Markdown ohne JSON-Hülle liefert. Die Informationen stehen dann trotzdem im Text —
# nur nicht in den strukturierten Feldern. Statt alles auf "unbekannt" fallen zu lassen,
# versuchen wir sie per Regex aus dem Markdown-Bericht selbst zu rekonstruieren.
_EMPFEHLUNG_MUSTER: list[tuple[re.Pattern, str]] = [
    (re.compile(r"finger\s*weg", re.IGNORECASE), "finger_weg"),
    (re.compile(r"hohes?\s*risiko", re.IGNORECASE), "hohes_risiko"),
    (re.compile(r"preis\s*nachverhandeln", re.IGNORECASE), "preis_nachverhandeln"),
    (re.compile(r"nur\s*mit\s*werkstattpr[üu]fung", re.IGNORECASE), "nur_mit_werkstattpruefung"),
    (re.compile(r"kaufen\s*nach\s*besichtigung", re.IGNORECASE), "kaufen_nach_besichtigung"),
    (re.compile(r"\bkaufen\b", re.IGNORECASE), "kaufen"),
]
_PREIS_BEWERTUNG_MUSTER: list[tuple[re.Pattern, str]] = [
    (re.compile(r"extrem\s*g[üu]nstig", re.IGNORECASE), "extrem_guenstig"),
    (re.compile(r"extrem\s*teuer", re.IGNORECASE), "extrem_teuer"),
    (re.compile(r"\bg[üu]nstig\b", re.IGNORECASE), "guenstig"),
    (re.compile(r"marktgerecht", re.IGNORECASE), "marktgerecht"),
    (re.compile(r"\bteuer\b", re.IGNORECASE), "teuer"),
]
_MARKTPREIS_SPANNE_MUSTER = re.compile(
    r"(\d[\d.]{2,7})\s*(?:€|eur)?\s*(?:bis|und|–|-)\s*(\d[\d.]{2,7})\s*(?:€|eur)"
)


def _extrahiere_bericht_string(raw: str) -> str | None:
    """Löst den inneren Markdown-Wert des Feldes "bericht" aus einer (evtl. kaputten
    oder abgeschnittenen) JSON-Hülle heraus, damit der Nutzer NIE die rohe JSON-
    Struktur `{"bericht": "..."}` zu sehen bekommt.

    - Ist der Rohtext keine JSON-Hülle (beginnt nicht mit "{"), wird er unverändert
      zurückgegeben (das Modell lieferte dann reines Markdown).
    - Findet sich keine "bericht"-Zeichenkette, wird None zurückgegeben.
    """
    if not raw:
        return None
    if not raw.lstrip().startswith("{"):
        return raw
    m = re.search(r'"bericht"\s*:\s*"', raw)
    if not m:
        return None
    i, n = m.end(), len(raw)
    buf: list[str] = []
    while i < n:
        ch = raw[i]
        if ch == "\\" and i + 1 < n:            # bereits escapte Sequenz übernehmen
            buf.append(raw[i:i + 2])
            i += 2
            continue
        if ch == '"':
            rest = raw[i + 1:]
            # Echtes String-Ende: dahinter folgt ein JSON-Strukturzeichen oder das
            # (abgeschnittene) Textende. Sonst ein literales Zitat im Fließtext.
            if rest.strip() == "" or re.match(r"\s*[,}\]]", rest):
                break
            buf.append('\\"')
            i += 1
            continue
        buf.append(ch)
        i += 1
    inner = "".join(buf)
    try:
        return json.loads('"' + inner + '"')
    except json.JSONDecodeError:
        return (inner.replace("\\n", "\n").replace("\\r", "\r")
                     .replace("\\t", "\t").replace('\\"', '"').replace("\\\\", "\\"))


def _notfall_extraktion(raw: str) -> dict:
    """Best-effort Rekonstruktion der Strukturfelder, wenn alle JSON-Parse-Versuche
    fehlschlagen (siehe call_gemini_json).

    Der "bericht" wird IMMER als sauberer Markdown-Text zurückgegeben — niemals als
    rohe oder abgeschnittene JSON-Hülle. Lässt sich kein Bericht bergen, steht dort
    eine verständliche Meldung statt interner JSON-Strukturen.
    """
    bericht_text = _extrahiere_bericht_string(raw)
    if not bericht_text or bericht_text.lstrip().startswith("{"):
        bericht_text = (
            "Die Analyse konnte diesmal nicht vollständig erstellt werden. "
            "Bitte starte den Kauf-Check in einem Moment erneut."
        )
    ergebnis: dict = {"bericht": bericht_text}

    empf_abschnitt_match = re.search(r"##\s*Kaufempfehlung(.{0,200})", bericht_text, re.IGNORECASE | re.DOTALL)
    such_text_empf = empf_abschnitt_match.group(1) if empf_abschnitt_match else bericht_text
    for pattern, wert in _EMPFEHLUNG_MUSTER:
        if pattern.search(such_text_empf):
            ergebnis["empfehlung"] = wert
            break

    preis_abschnitt_match = re.search(r"##\s*Preis-Einsch[äa]tzung(.{0,300})", bericht_text, re.IGNORECASE | re.DOTALL)
    such_text_preis = preis_abschnitt_match.group(1) if preis_abschnitt_match else bericht_text
    for pattern, wert in _PREIS_BEWERTUNG_MUSTER:
        if pattern.search(such_text_preis):
            ergebnis["preis_bewertung"] = wert
            break

    spanne_match = _MARKTPREIS_SPANNE_MUSTER.search(such_text_preis)
    if spanne_match:
        try:
            ergebnis["marktpreis_min"] = int(spanne_match.group(1).replace(".", ""))
            ergebnis["marktpreis_max"] = int(spanne_match.group(2).replace(".", ""))
        except ValueError:
            pass

    return ergebnis


def _ist_abgeschnitten(response) -> bool:
    """True, wenn Gemini die Antwort wegen des Token-Limits abgeschnitten hat
    (finish_reason MAX_TOKENS). Eine solche Antwort enthält unvollständiges JSON."""
    try:
        fr = response.candidates[0].finish_reason
    except (AttributeError, IndexError, TypeError):
        return False
    if fr is None:
        return False
    return getattr(fr, "name", str(fr)).upper().endswith("MAX_TOKENS")


async def call_gemini_json(system_prompt: str, user_msg: str) -> dict:
    """
    Ruft Gemini im JSON-Modus auf und gibt das geparste Dict zurück.
    Zwei-Stufen-Parsing: erst normal, dann mit Newline-Repair für
    den häufigen Fall dass Gemini literal \\n in Stringwerten ausgibt.
    """
    cfg = genai_types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=0.2,
        max_output_tokens=16384,
        response_mime_type="application/json",
        # Gemini 2.5 Flash "denkt" per Default dynamisch und praktisch unbegrenzt;
        # diese Thinking-Tokens zählen gegen max_output_tokens. Bei einem langen
        # Kauf-/Verkaufscheck-Bericht zehrt das Denken das Budget auf, sodass die
        # eigentliche JSON-Antwort mitten im Bericht (z.B. in einer Tabelle)
        # abgeschnitten wird (finish_reason MAX_TOKENS) — das JSON ist dann
        # unparsebar. Ohne Thinking steht das gesamte Budget der Antwort zur
        # Verfügung (dasselbe Muster nutzt admin_llm für die strukturierte
        # Fahrzeug-Generierung mit demselben 16k-Budget).
        thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
    )
    client = get_gemini_client()

    async def _ein_versuch():
        return await with_retry(lambda: client.aio.models.generate_content(
            model=LLM_MODEL,
            contents=[{"role": "user", "parts": [{"text": user_msg}]}],
            config=cfg,
        ))

    # Eine durch das Token-Limit abgeschnittene Antwort (finish_reason MAX_TOKENS)
    # NIEMALS als Erfolg durchreichen: das JSON ist unvollständig, alle Parse-/
    # Repair-Versuche scheitern und der Nutzer bekäme sonst rohen, mitten im Bericht
    # abgebrochenen JSON-Text zu sehen. Kontrollierter Ablauf: EIN Retry (mit
    # deaktiviertem Thinking sollte die Antwort ins Budget passen), danach sauberer
    # Fehlschlag — der Router erstattet das Kontingent und zeigt eine verständliche
    # Meldung statt eines kaputten Berichts.
    response = await _ein_versuch()
    if _ist_abgeschnitten(response):
        log.warning("Gemini JSON-Antwort abgeschnitten (MAX_TOKENS) — einmaliger kontrollierter Retry.")
        response = await _ein_versuch()
        if _ist_abgeschnitten(response):
            log.warning(
                "Gemini JSON-Antwort auch nach Retry abgeschnitten (MAX_TOKENS) — "
                "melde Fehlschlag, kein Roh-Dump."
            )
            raise GeminiFehlgeschlagen(
                "Gemini-Antwort wurde durch das Token-Limit abgeschnitten (auch nach Retry)."
            )

    # Manche Antworten liefern KEINEN Text (z.B. durch Safety-Filter blockiert,
    # oder .text wirft selbst eine Exception bei fehlenden candidates) — das ist
    # kein Python-Fehler, aber genauso wertlos für den Nutzer wie ein 429/503.
    # Einheitlich als GeminiFehlgeschlagen behandeln, damit der Aufrufer (Kauf-/
    # Verkaufscheck) das Check-Kontingent zurückerstatten kann.
    try:
        raw = (response.text or "").strip()
    except Exception as exc:
        raise GeminiFehlgeschlagen(f"Gemini-Antwort ohne verwertbaren Text: {exc}") from exc
    if not raw:
        raise GeminiFehlgeschlagen("Gemini hat eine leere Antwort geliefert.")

    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw.strip())

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        try:
            return json.loads(_escape_json_strings(raw))
        except json.JSONDecodeError:
            try:
                # Komma-Reparatur muss auf dem ROHTEXT laufen (siehe Docstring von
                # _repariere_fehlendes_komma), danach erst die Anführungszeichen-Reparatur.
                return json.loads(_escape_json_strings(_repariere_fehlendes_komma(raw)))
            except json.JSONDecodeError:
                log.warning("Gemini JSON-Parsing fehlgeschlagen (alle Versuche). Raw[:300]: %s", raw[:300])
                return _notfall_extraktion(raw)
