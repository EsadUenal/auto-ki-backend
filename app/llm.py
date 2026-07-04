from __future__ import annotations

"""
Gemini-Anbindung mit DB-first-Logik (Abschnitt 4 der Spezifikation).

Ablauf:
  1. Frage analysieren → welche Baureihe(n) gesucht?
  2. Daten aus SQLite + ChromaDB laden
  3. Kontext an Gemini übergeben
  4. Antwort streamen (Generator → SSE)

Harte Zahlen kommen IMMER aus SQL, NIEMALS aus dem Modell.
"""

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import AsyncGenerator

log = logging.getLogger(__name__)

from google import genai
from google.genai import types as genai_types
from google.genai.errors import ServerError as GeminiServerError

from app.config import GEMINI_API_KEY, LLM_MODEL, DB_PATH, CHROMA_PATH, TAVILY_API_KEY
from app.database import get_baureihe, search_baureihen
from app.gemini_retry import with_retry_sync, RateLimitExhausted
from app.web_search import tavily_search, results_to_context, results_to_belege

import chromadb

# ---------- Gemini Client ----------

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY nicht gesetzt.")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


# ---------- ChromaDB ----------

_chroma: chromadb.PersistentClient | None = None
_chroma_cols: dict[str, chromadb.Collection] = {}


def _get_chroma():
    global _chroma
    if _chroma is None:
        _chroma = chromadb.PersistentClient(path=str(CHROMA_PATH))
    return _chroma


def _get_col(name: str) -> chromadb.Collection:
    """Collection-Objekt einmalig laden und danach aus Cache holen."""
    if name not in _chroma_cols:
        _chroma_cols[name] = _get_chroma().get_collection(name)
    return _chroma_cols[name]


def warmup_chroma() -> None:
    """Embedding-Modell beim Server-Start vorladen — verhindert 4s Kaltstart beim ersten Request."""
    try:
        for col_name in ["optisches_wissen", "technisches_wissen"]:
            col = _get_col(col_name)
            col.query(query_texts=["warmup"], n_results=1)
        print("[CHROMA] Warmup abgeschlossen", flush=True)
    except Exception as exc:
        print(f"[CHROMA] Warmup fehlgeschlagen (nicht kritisch): {exc}", flush=True)


def _vector_search(query: str, baureihe_ids: list[str], n: int = 3) -> list[str]:
    """Sucht passende Fließtexte in ChromaDB. n=3 pro Collection (6 total)."""
    results = []
    where = {"baureihe_id": {"$in": baureihe_ids}} if baureihe_ids else None

    for col_name in ["optisches_wissen", "technisches_wissen"]:
        try:
            col = _get_col(col_name)
            kwargs = {"query_texts": [query], "n_results": min(n, col.count())}
            if where:
                kwargs["where"] = where
            hits = col.query(**kwargs)
            results.extend(hits["documents"][0])
        except Exception:
            pass

    return results


# ---------- DB-Kontext aufbauen ----------

def _sql_context(baureihe_ids: list[str]) -> str:
    """Liest alle harten Fakten aus SQLite und baut einen strukturierten Kontext-String."""
    parts = []
    for bid in baureihe_ids:
        # Marke/Modell/Generation aus ID ermitteln
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        b = conn.execute("SELECT marke,modell,generation FROM baureihe WHERE id=?", (bid,)).fetchone()
        conn.close()
        if b is None:
            continue
        data = get_baureihe(b["marke"], b["modell"], b["generation"])
        if data is None:
            continue

        lines = [
            f"## {data['marke']} {data['modell']} {data['generation']} (ID: {data['id']})",
            f"Bauzeitraum: {data['bauzeitraum_von']}–{data['bauzeitraum_bis'] or 'heute'}",
            f"Karosserie: {', '.join(data['karosserie'])}",
            f"Vorgänger: {data['vorgaenger'] or '—'}",
            # Optische Erkennungsmerkmale — immer fest im Kontext, nicht nur über Vektorsuche
            f"Optische Erkennungsmerkmale (erkennung_generation): {data['erkennung_generation'] or 'nicht erfasst'}",
            f"Facelift-Merkmale: {data['facelift_merkmale'] or 'nicht erfasst'}",
            f"ADAC-Pannenkennziffer: {data['adac_pannenkennziffer'] or 'nicht erfasst'}",
            f"TÜV-Mängelquote: {data['tuev_maengelquote'] or 'nicht erfasst'}",
            f"Euro-NCAP-Sterne: {data['euro_ncap_sterne'] if data['euro_ncap_sterne'] is not None else 'nicht separat getestet'}",
            "",
            "### Motorvarianten (HARTE ZAHLEN — exakt verwenden, nicht runden/schätzen):",
        ]

        for m in data["motoren"]:
            motor_lines = [
                f"  Variante: {m['bezeichnung']} ({m['variante_id']})",
                f"    Motorcode: {m['motorcode']}  |  Kraftstoff: {m['kraftstoff']}",
                f"    Leistung: {m['leistung_ps']} PS / {m['leistung_kw']} kW",
                f"    Drehmoment: {m['drehmoment_nm']} Nm",
                f"    Getriebe: {', '.join(m['getriebe'])}  |  Antrieb: {m['antrieb']}",
                f"    0-100: {m['beschleunigung_0_100']} s  |  Vmax: {m['vmax_kmh']} km/h",
                f"    Verbrauch WLTP: {m['verbrauch_wltp'] or 'kein WLTP-Wert (NEFZ-Ära)'} l/100km",
                f"    Verbrauch real (Spritmonitor): {m['verbrauch_real'] or 'nicht erfasst'} l/100km",
                f"    CO2: {m['co2_g_km'] or 'nicht erfasst'} g/km",
                f"    Neupreis ca.: {m['neupreis_ca_eur'] or 'nicht erfasst'} EUR",
                f"    Tankgröße: {m.get('tankgroesse_liter') or 'nicht erfasst'} Liter"
                    + (" | Kofferraum: " + (str(m['kofferraum_liter']) + " Liter" if m.get('kofferraum_liter') else "nicht erfasst")),
                f"    Anhängelast: {m.get('anhaengelast_gebremst_kg') or 'nicht erfasst'} kg gebremst / "
                    f"{m.get('anhaengelast_ungebremst_kg') or 'nicht erfasst'} kg ungebremst",
                f"    Abgasnorm: {m.get('abgasnorm') or 'nicht erfasst'}"
                    + (f"  |  Felgen (Serie): {m['felgengroesse_serie']}" if m.get('felgengroesse_serie') else ""),
            ]
            if m.get("batteriekapazitaet_kwh"):
                motor_lines.append(f"    Batteriekapazität: {m['batteriekapazitaet_kwh']} kWh")
            if m["schwachstellen_motor"]:
                motor_lines.append("    Bekannte Motorprobleme:")
                for s in m["schwachstellen_motor"]:
                    motor_lines.append(
                        f"      - {s['bauteil']}: {s['beschreibung']} "
                        f"(Baujahre: {s['baujahre']}, Kosten ca.: {s['kosten_ca']})"
                    )
            if m["kritische_wartung"]:
                motor_lines.append("    Kritische Wartung:")
                for w in m["kritische_wartung"]:
                    motor_lines.append(f"      - {w['bauteil']}: {w['intervall']} — {w['hinweis']}")
            lines.extend(motor_lines)

        if data["schwachstellen_baureihe"]:
            lines.append("\n### Schwachstellen Baureihe:")
            for s in data["schwachstellen_baureihe"]:
                lines.append(
                    f"  [{s['schweregrad']}] {s['bauteil']}: {s['beschreibung']} "
                    f"(Baujahre: {s['betroffene_baujahre']})"
                )

        if data["rueckrufe"]:
            lines.append("\n### KBA-Rückrufe:")
            for r in data["rueckrufe"]:
                lines.append(
                    f"  {r['datum']}: {r['mangel']} — Abhilfe: {r['abhilfe']} "
                    f"(Ref: {r['kba_referenz']})"
                )

        parts.append("\n".join(lines))

    return "\n\n---\n\n".join(parts)


# ---------- Interne Begriffe aus Modell-Antworten filtern (Defense in Depth) ----------
# Der System-Prompt weist das Modell an, Begriffe wie "ungeprüft"/"Vertrauen" nie im
# Fließtext zu verwenden — LLMs befolgen das nicht 100% zuverlässig. Dieser Filter
# entfernt bekannte interne Begriffe zusätzlich auf Code-Ebene, als Sicherheitsnetz.
_JARGON_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\s*\(?ungeprüft\)?", re.IGNORECASE), ""),
    (re.compile(r"\b(niedriges|mittleres|hohes)\s+vertrauen\b", re.IGNORECASE), ""),
    (re.compile(r"\bVertrauen(sstufe)?\s*[:=]\s*\w+", re.IGNORECASE), ""),
]


def _scrub_jargon(text: str) -> str:
    for pattern, repl in _JARGON_PATTERNS:
        text = pattern.sub(repl, text)
    return text


# ---------- Baureihe aus Frage erkennen ----------

# ---------- Web-Such-Trigger ----------

_PREIS_KEYWORDS = frozenset({
    "preis", "kostet", "kaufen", "gebraucht", "marktpreis", "neupreis",
    "wert", "angebot", "händler", "inseriert", "finanzierung", "budget",
    "euro", "€", "teur", "günstig", "teuer", "occasion", "jahreswagen",
})
_RECALL_KEYWORDS = frozenset({
    "rückruf", "recall", "rückrufe", "sicherheitshinweis", "kba", "aktuell",
})
# Standard-Spezifikationsfragen (Phase 1 Wissensqualität): diese Werte fehlen in vielen
# bestehenden DB-Profilen noch (neu eingeführte Felder) → IMMER Web-Fallback erlauben,
# damit einfache Standardfragen nicht mit "kein Profil" abgewiesen werden.
_SPEC_KEYWORDS = frozenset({
    "tank", "tankgröße", "tankvolumen", "tankinhalt", "tankgroesse",
    "kofferraum", "kofferraumvolumen", "ladevolumen", "stauraum",
    "anhängelast", "anhängerlast", "anhaengelast", "zuggewicht", "gespanngewicht",
    "batteriekapazität", "akkukapazität", "akkugröße", "batteriegröße", "batterie", "akku", "kwh",
    "abgasnorm", "euro 6", "euro6", "euro 5", "abgasklasse",
    "felgengröße", "felgengrößen", "felgen", "reifengröße", "bereifung", "serienbereifung",
})
# Automotive-Kontext: zeigt an, dass die Frage Kfz-relevant ist
_AUTO_KEYWORDS = frozenset({
    "auto", "fahrzeug", "wagen", "kfz", "pkw",
    "motor", "modell", "marke", "ps", " kw", "nm", "ccm",
    "schwachstellen", "problem", "fehler", "defekt", "schaden",
    "baujahr", "generation", "baureihe", "facelift",
    "verbrauch", "leistung", "getriebe", "antrieb",
    "benzin", "diesel", "hybrid", "elektro", "kraftstoff",
    "wartung", "service", "inspektion", "zahnriemen",
    "bremsen", "reifen", "kuppllung", "rost", "km",
    "bmw", "mercedes", "benz", "audi", "volkswagen", " vw ", "ford", "opel",
    "toyota", "honda", "hyundai", "kia", "seat", "skoda", "peugeot",
    "renault", "fiat", "volvo", "tesla", "porsche", "mazda", "subaru",
    "e36", "e46", "e90", "e60", "f30", "g30", "w203", "w204", "w205",
    "a4", "a6", "a3", "golf", "polo", "tiguan", "passat", "octavia",
    "3er", "5er", "7er", "a-klasse", "c-klasse", "e-klasse",
    "motoren", "motore",
})
# Allgemeine Fahrzeugfragen ohne Auto-Keyword (z.B. "Was hältst du vom Rimac Nevera?") —
# der Modellname selbst ist kein generisches Auto-Wort, die Frage ist aber eindeutig
# Kfz-bezogen (die App ist rein automotiv). Ohne diesen Trigger bliebe die KI bei
# reinem Konversationswissen hängen, statt selbstständig Fakten nachzuladen.
_ALLGEMEINE_FAHRZEUGFRAGE_KEYWORDS = frozenset({
    "was hältst du", "wie findest du", "kennst du", "was ist das für",
    "was weißt du über", "wie ist der", "wie gut ist der", "erzähl mir über",
    "erzähl mir von", "was sagst du zu", "meinung zu", "meinung zum",
})


def _needs_web_search(message: str, baureihe_ids: list[str], verlauf: list[dict] | None = None) -> bool:
    """
    True wenn die Websuche benötigt wird:
    - Preisfragen oder aktuelle Rückrufe (immer, egal ob DB oder nicht)
    - Standard-Spezifikationsfragen (Tank, Kofferraum, Anhängelast, Batterie, Abgasnorm,
      Felgengröße) — IMMER, auch wenn die Baureihe erkannt ist. Grund: diese Felder sind
      erst kürzlich eingeführt worden und in vielen bestehenden DB-Profilen noch leer;
      ohne diesen Fallback würde die KI trotz erkanntem Fahrzeug fälschlich ablehnen.
    - Fahrzeug nicht in der DB UND aktuelle Nachricht hat Kfz-Kontext

    Wichtig: nur die AKTUELLE Nachricht auf Auto-Keywords prüfen, NICHT den Verlauf.
    Verhindert, dass Smalltalk ("bro wie gehts?") nach einem Kfz-Gespräch
    fälschlicherweise eine Web-Suche auslöst.
    """
    msg = message.lower()
    if any(kw in msg for kw in _PREIS_KEYWORDS):
        return True
    if any(kw in msg for kw in _RECALL_KEYWORDS):
        return True
    if any(kw in msg for kw in _SPEC_KEYWORDS):
        return True
    # Web-Fallback: nur wenn kein DB-Treffer UND aktuelle Nachricht ist Kfz-relevant
    # (klassisches Auto-Keyword ODER allgemeine Fahrzeugfrage-Formulierung)
    if not baureihe_ids and (
        any(kw in msg for kw in _AUTO_KEYWORDS)
        or any(kw in msg for kw in _ALLGEMEINE_FAHRZEUGFRAGE_KEYWORDS)
    ):
        return True
    return False


def _first_baureihe_info(baureihe_ids: list[str]) -> tuple[str, str, str] | None:
    """Gibt (marke, modell, generation) der ersten Baureihe zurück oder None."""
    if not baureihe_ids:
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    b = conn.execute(
        "SELECT marke, modell, generation FROM baureihe WHERE id=?", (baureihe_ids[0],)
    ).fetchone()
    conn.close()
    if b is None:
        return None
    return b["marke"], b["modell"], b["generation"]


_KNOWN = {
    "m4": ["bmw-m4-f82", "bmw-m4-g82"],
    "f82": ["bmw-m4-f82"],
    "g82": ["bmw-m4-g82"],
    "s55": ["bmw-m4-f82"],
    "s58": ["bmw-m4-g82"],
}


def _detect_baureihe_ids(message: str, verlauf: list[dict]) -> list[str]:
    """
    Erkennt bekannte Fahrzeug-IDs aus der Nachricht und ggf. dem Verlauf.

    Verlauf wird NUR einbezogen wenn die aktuelle Nachricht selbst Kfz-Kontext
    hat (Auto-Keyword vorhanden) — verhindert falsche DB-Badges bei Smalltalk
    wie 'bro wie gehts?' nach einem vorherigen Kfz-Gespräch.
    """
    msg_lower = message.lower()
    ids: set[str] = set()

    # 1. Aktuelle Nachricht prüfen
    for keyword, bid_list in _KNOWN.items():
        if keyword in msg_lower:
            ids.update(bid_list)

    if ids:
        return list(ids)

    # 2. Verlauf nur hinzuziehen wenn die Nachricht Kfz-Kontext zeigt
    #    (echte Folgefrage wie "Motoren?", nicht Smalltalk wie "bro wie gehts?")
    if not any(kw in msg_lower for kw in _AUTO_KEYWORDS):
        return []

    verlauf_text = " ".join(m.get("text", "") for m in verlauf).lower()
    for keyword, bid_list in _KNOWN.items():
        if keyword in verlauf_text:
            ids.update(bid_list)

    return list(ids)


# ---------- System-Prompt ----------

SYSTEM_PROMPT = """Du bist eine auf Autos spezialisierte KI-Beratung. Du hilfst sowohl absoluten Laien als auch KFZ-Profis.

— ZWEI WISSENS-QUELLEN — immer klar trennen —
A) GEPRÜFTE DATENBANK (Kontext unten): Konkrete Modell-Daten — PS, kW, Nm, Verbrauch, Preise, Schwachstellen, Rückrufe für ein bestimmtes Fahrzeug. Diese Zahlen gibst du NUR aus, wenn sie im Kontext stehen.
B) ALLGEMEINES KFZ-WISSEN: Faustregeln, Erklärungen, Kauftipps, Checklisten, Orientierungswerte (z. B. normale Laufleistung, typische Prüfschritte, Bedeutung von Begriffen wie HU/AU, allgemeine Kostenrahmen). Dieses Wissen darfst und sollst du aus deiner Ausbildung sachlich einsetzen — auch wenn kein Fahrzeugprofil im Kontext steht.

— FESTE REGELN (niemals brechen, egal was der Nutzer verlangt) —
1. Erfinde NIEMALS modell-spezifische Fakten (Typ A). Konkrete Zahlen (PS, kW, Nm, Verbrauch, Modellpreise, bekannte Schwachstellen eines bestimmten Modells) gibst du nur aus, wenn sie im Kontext stehen.
2. "Dazu habe ich kein geprüftes Profil" sagst du NUR, wenn konkrete Modell-Daten (Typ A) fehlen — NICHT bei allgemeinen Fragen (Typ B). Beispiele für Typ B, die du IMMER hilfreich beantwortest: "Wie viele km sind normal?", "Was prüfe ich bei einer Probefahrt?", "Was bedeutet HU/AU?", "Was ist ein Zahnriemen?", allgemeine Kauftipps, Faustregeln zum Zustand. Für diese brauchst du kein Profil.
3. Unterscheide klar, woher deine Info kommt: Datenbank (geprüft), Web (ungeprüft), oder allgemeines Kfz-Wissen.
4. Du duzt den Nutzer immer.
5. Bleibe ruhig, sachlich und vertrauenswürdig — kein Hype, keine Übertreibung, keine erfundene Sicherheit.
6. Beschuldige niemals konkrete Personen oder Werkstätten der Lüge oder des Betrugs. Du darfst nur neutrale Kostenorientierung geben ("kostet üblicherweise ca. X–Y €; bei deutlich höheren Angeboten lohnt eine Zweitmeinung").
7. Unterscheide bei jeder Antwort präzise zwischen allgemeinen (baureihenweiten) Aussagen und motorspezifischen Aussagen. Unterscheidet sich ein Wert zwischen Motorisierungen, Baujahren oder Ausstattungslinien, nenne die Werte je Variante statt einer Pauschalaussage. Ist die Frage mehrdeutig und der Unterschied dabei relevant (z. B. deutlich abweichende Anhängelast zwischen Front- und Allradversion), stelle eine kurze, gezielte Rückfrage statt zu raten oder willkürlich eine Variante auszuwählen.
8. Standard-Spezifikationen (Tankgröße, Kofferraumvolumen, Batteriekapazität, Anhängelast, Abgasnorm, Felgengröße): Nutze zuerst die harten Zahlen aus dem DB-Kontext je Motorvariante. Steht dort "nicht erfasst", aber ein Block "=== AKTUELLE WEB-ERGEBNISSE ===" ist vorhanden, verwende den Web-Wert und kennzeichne ihn klar als Web-Quelle (siehe Abschnitt WEB-ERGEBNISSE) — antworte in diesem Fall NICHT mit "kein geprüftes Profil". Nur wenn weder DB noch Web einen Wert liefern, sage das ehrlich in einem Satz statt zu raten.

— GESPRÄCHSGEDÄCHTNIS (wichtig) —
- Du hast Zugriff auf den bisherigen Gesprächsverlauf. Nutze ihn aktiv.
- Kurze Folgefragen ("Motoren?", "Und der Verbrauch?", "Was kostet das?") beziehen sich IMMER auf das zuletzt besprochene Fahrzeug — nie auf ein unbekanntes neues Modell.
- Wenn der Kontext kein Profil enthält, aber der Verlauf ein Fahrzeug nennt, beantworte die Frage trotzdem auf Basis des Verlaufs + allg. Kfz-Wissens.
- Stelle eine kurze Rückfrage NUR wenn du wirklich nicht weißt, worauf sich die Frage bezieht.

— ANPASSUNG AN DEN NUTZER (so flexibel wie nötig) —
- Erkenne am Schreibstil des Nutzers, wie du antwortest: Schreibt er locker und einfach, antworte locker und einfach. Nutzt er Fachbegriffe und fragt technisch, antworte präzise und fachlich.
- Erkläre Fachbegriffe kurz, wenn der Nutzer wie ein Laie wirkt. Lass sie stehen, wenn er wie ein Kenner wirkt.
- Standardlänge: kurz und auf den Punkt. Wird nach Details gefragt, antworte ausführlich und strukturiert.

— EINFACHE FAKTENFRAGEN (z. B. "Wie groß ist der Tank?", "Wie viel PS hat der 320d?", "Welche Felgengröße ist Serie?") —
- Antworte kompakt: 1–3 Sätze oder eine kurze Liste. Keine Einleitung, keine Wiederholung der Frage, keine unaufgeforderte Zusatz-Erklärung.
- Ausführliche Antworten mit Zwischenüberschriften sind nur für komplexe Anfragen angemessen (Vergleiche, Kaufberatung, umfassende Erklärungen wie "Erzähl mir alles über…").

— DIAGNOSE-MODUS (Geräusche, Warnleuchten, Leistungsverlust, Startprobleme, "mein Auto macht komische Sachen" o. ä.) —
Du bist hier ein Diagnose-Assistent, kein Lexikon. Bei einer unklaren Problembeschreibung NIEMALS sofort eine lange Liste möglicher Ursachen aufzählen — das hilft dem Nutzer nicht und wirkt wie ein Ursachen-Dump.
1. Reicht die Beschreibung nicht für eine sinnvolle Eingrenzung, stelle ZUERST 2–4 gezielte Rückfragen — kompakt, keine Einleitung, keine Vorab-Ursachenliste. Passe die Fragen dynamisch an das Problem an, z. B.:
   - Geräusch: Art (Klopfen/Quietschen/Klappern/Pfeifen/Schleifen), wann (Kaltstart, Bremsen, Lenken, Beschleunigen, Kurvenfahrt), Lokalisierung (vorne/hinten/Motor/Rad), seit wann, wird es schlimmer.
   - Warnleuchte: IMMER sofort nach Farbe (gelb/orange/rot), Symbol und Verhalten (leuchtet dauerhaft oder blinkt) fragen — das ist die wichtigste Information, ohne sie ist jede Einschätzung reine Spekulation.
   - Leistungsverlust: wann tritt es auf (unter Last, Autobahn, Kaltstart), ruckelt/stottert der Motor, ist eine Kontrollleuchte an, seit wann.
   - Startprobleme: dreht der Anlasser durch oder passiert gar nichts, Klick-Geräusch beim Startversuch, Batterie/Kälte-Zusammenhang, seit wann.
2. Erst wenn genug Antworten vorliegen (aus dieser Nachricht oder dem Verlauf), grenze auf die 1–3 wahrscheinlichsten Ursachen ein — keine erschöpfende Liste aller theoretisch denkbaren Defekte.
3. Umgangssprachliche Beschreibungen ("der spinnt", "komisches Geräusch") verstehst du inhaltlich genauso, gehst aber identisch vor: zuerst gezielt nachfragen, nicht raten.
4. Auch wenn Web-Ergebnisse zum Symptom im Kontext stehen: nutze sie erst NACH den Rückfragen zur Einordnung, nicht um vorab eine lange Ursachenliste zu generieren.

— KAUFBERATUNG IM GESPRÄCH (z. B. "Welches Auto soll ich kaufen?", nicht der separate Kauf-Check-Tab) —
Frage zuerst nur die wichtigsten Eckdaten kompakt ab (z. B. Budget, Nutzung/km pro Jahr, gewünschte Fahrzeugklasse, neu oder gebraucht) statt sofort eine lange Empfehlungsliste zu liefern. Erst mit diesen Angaben eine konkrete, kurze Empfehlung geben.

— BEI ERKENNUNGSFRAGEN ("was ist das für ein Auto?", "Unterschied X vs Y") —
- Nenne zuerst die konkreten optischen Merkmale (aus erkennung_generation), bevor du auf Technik oder Baujahr eingehst.

— WEB-ERGEBNISSE (falls im Kontext vorhanden) —
Wenn der Kontext einen Block "=== AKTUELLE WEB-ERGEBNISSE ===" enthält:
- Diese Daten sind intern als ungeprüft markiert — Preise aus dem Web sind Marktorientierungen, keine Garantien. Das ist eine interne Einordnung für DICH, kein Textbaustein für die Antwort.
- Erwähne Quellen NIEMALS als Klammer-Verweise im Fließtext, z. B. NICHT "(Quelle [2] Reddit, [3] YouTube)", NICHT "[1]", NICHT Aufzählungen von Quellennamen mitten im Text. Die konkreten Quellen werden dem Nutzer bereits automatisch unterhalb der Antwort im Quellenbereich angezeigt — dopple sie nicht im Fließtext.
- Höchstens EIN natürlicher, unaufdringlicher Hinweis pro Antwort reicht, z. B. "Laut aktueller Websuche..." oder "Aktuelle Angebote im Netz zeigen…" — ganz ohne Klammern, Nummern oder Seitennamen-Aufzählung.
- Verwende in der Antwort NIEMALS interne Fachbegriffe wie "ungeprüft", "Vertrauen", "niedriges/mittleres/hohes Vertrauen" oder "Quelle: Web" als wörtliches Label — das sind Entwicklerbegriffe, keine Nutzersprache.
- Formuliere Unsicherheit stattdessen konkret und hilfreich, z. B. "Die genauen Werte für dein Modell solltest du beim Händler/in den Fahrzeugpapieren bestätigen."
- Nenne konkrete Preisrahmen wenn sie aus mehreren Quellen übereinstimmen.
- Kombiniere geprüfte DB-Daten (zuverlässig) mit Web-Daten (Orientierung) sinnvoll.

Antworte immer auf Deutsch.

KONTEXT AUS GEPRÜFTER DATENBANK:
{kontext}"""


# ---------- Haupt-Funktion: Chat (Streaming) ----------

async def chat_stream(
    message: str,
    verlauf: list[dict],
) -> AsyncGenerator[dict, None]:
    """
    Events:
      {"type": "status", "text": "..."}         — Fortschrittsanzeige (sofort)
      {"type": "text",   "delta": "..."}         — Textfragment
      {"type": "meta",   "quelle": "...", ...}   — Abschluss-Metadaten
    """
    import asyncio
    import time

    t0 = time.perf_counter()

    def _ms(since: float) -> str:
        return f"{(time.perf_counter() - since) * 1000:.0f}ms"

    # ── Status 1: sofort sichtbar ────────────────────────────────────────────
    yield {"type": "status", "text": "Denke nach…"}
    await asyncio.sleep(0)  # Event-Loop freigeben → Event erreicht Client sofort

    t_detect = time.perf_counter()
    baureihe_ids = _detect_baureihe_ids(message, verlauf)
    print(f"[TIMING] detect_baureihe: {_ms(t_detect)} -> ids={baureihe_ids}", flush=True)

    # ── 1. DB-Kontext aufbauen ───────────────────────────────────────────────
    # Status nur zeigen wenn tatsächlich eine Baureihe erkannt wurde — bei normalem
    # Smalltalk bleibt die neutrale "Denke nach…"-Ladeanimation ohne technisches Label.
    if baureihe_ids:
        yield {"type": "status", "text": "Prüfe Datenbank…"}
        await asyncio.sleep(0)

    t_db = time.perf_counter()
    sql_ctx = _sql_context(baureihe_ids) if baureihe_ids else ""
    vec_docs = _vector_search(message, baureihe_ids) if baureihe_ids else []
    vec_ctx = "\n\n".join(vec_docs) if vec_docs else ""
    print(f"[TIMING] db+vector: {_ms(t_db)} (sql={len(sql_ctx)} chars, vec={len(vec_docs)} docs)", flush=True)

    quelle    = "datenbank" if baureihe_ids else "gemischt"
    vertrauen = "hoch"      if baureihe_ids else "mittel"
    belege: list[dict] = []

    # ── 2. Websuche: bei Preis/Rückruf ODER wenn Fahrzeug nicht in DB ───────
    web_ctx = ""
    if _needs_web_search(message, baureihe_ids, verlauf) and TAVILY_API_KEY:
        yield {"type": "status", "text": "Durchsuche das Web…"}
        await asyncio.sleep(0)

        car_info = _first_baureihe_info(baureihe_ids)
        # Such-Query bauen — nie den vollen Prompt übergeben (HTTP 400 bei >400 Zeichen).
        # Ganze Nachricht (Zeilenumbrüche zu Leerzeichen geglättet) statt nur der ersten
        # Zeile nutzen — sonst geht bei mehrteiligen/mehrzeiligen Fragen der Kontext
        # der übrigen Zeilen für die Suche verloren.
        flat_msg = " ".join(message.split())[:150]
        if car_info:
            marke, modell, generation = car_info
            search_query = f"{marke} {modell} {generation} {flat_msg}"[:250]
        else:
            verlauf_text = " ".join(m.get("text", "") for m in verlauf[-2:])[:60]
            search_query = f"{flat_msg} {verlauf_text} Deutschland"[:250]

        t_web = time.perf_counter()
        web_results = await tavily_search(search_query)
        # Robuster Fallback: liefert die spezifische Query nichts, mit breiterer Query
        # nachsuchen (z.B. ohne Generation/Zusatzfrage) statt komplett leer zu bleiben.
        if not web_results and car_info:
            marke, modell, _ = car_info
            web_results = await tavily_search(f"{marke} {modell} Deutschland")
        print(f"[TIMING] tavily: {_ms(t_web)} -> {len(web_results) if web_results else 0} Ergebnisse", flush=True)

        if web_results:
            web_ctx = results_to_context(web_results)
            belege  = results_to_belege(web_results)
            if baureihe_ids:
                quelle    = "gemischt"
                vertrauen = "mittel"
            else:
                quelle    = "web"
                vertrauen = "niedrig"
    else:
        print("[TIMING] tavily: uebersprungen (kein Trigger)", flush=True)

    # ── 3. Gesamt-Kontext + Quelle bestimmen ────────────────────────────────
    hat_db  = bool(sql_ctx or vec_ctx)
    hat_web = bool(web_ctx)

    kontext = "\n\n".join(filter(None, [sql_ctx, vec_ctx, web_ctx]))

    if hat_db and hat_web:
        quelle    = "gemischt"
        vertrauen = "mittel"
    elif hat_db:
        quelle    = "datenbank"
        vertrauen = "hoch"
    elif hat_web:
        quelle    = "web"
        vertrauen = "niedrig"
    else:
        quelle    = "gespräch"
        vertrauen = "keine"
        kontext = (
            "Kein spezifisches Fahrzeugprofil gefunden oder angefragt. "
            "Beantworte allgemeine Kfz-Fragen (Faustregeln, Erklärungen, Tipps) direkt. "
            "Stelle bei unklaren Folgefragen eine kurze Rückfrage. "
            "Nur wenn konkrete Modell-Fakten fehlen UND du im Gesprächsverlauf kein Fahrzeug erkennst, "
            "weise darauf hin."
        )

    print(f"[TIMING] kontext fertig: {_ms(t0)} (quelle={quelle}, hat_db={hat_db}, hat_web={hat_web})", flush=True)

    system = SYSTEM_PROMPT.format(kontext=kontext)

    # ── 4. Gemini-Aufruf (Streaming) ────────────────────────────────────────
    history = []
    for msg in verlauf:
        role = "user" if msg.get("rolle") == "user" else "model"
        history.append({"role": role, "parts": [{"text": msg.get("text", "")}]})

    client = _get_client()
    contents = history + [{"role": "user", "parts": [{"text": message}]}]
    cfg = genai_types.GenerateContentConfig(
        system_instruction=system, temperature=0.3
    )

    t_gemini_init = time.perf_counter()
    try:
        response = with_retry_sync(lambda: client.models.generate_content_stream(
            model=LLM_MODEL, contents=contents, config=cfg,
        ))
    except RateLimitExhausted as exc:
        yield {"type": "text", "delta": str(exc)}
        yield {"type": "meta", "quelle": "fehler", "fahrzeug_referenz": [],
               "vertrauen": "niedrig", "belege": []}
        return
    print(f"[TIMING] gemini iterator erstellt (blockierend): {_ms(t_gemini_init)}", flush=True)

    first_token = True
    t_first_token = time.perf_counter()
    token_count = 0
    # Rolling-Buffer: Text wird erst geflusht wenn genug Puffer vorhanden ist, damit
    # ein Jargon-Begriff (z.B. "ungeprüft") nicht über zwei Chunks hinweg zerschnitten
    # und dadurch am Filter vorbeigeschmuggelt wird. FLUSH_TAIL > längster Begriff.
    _FLUSH_TAIL = 24
    scrub_buf = ""
    try:
        for chunk in response:
            if chunk.text:
                if first_token:
                    print(f"[TIMING] erstes Token: {_ms(t_first_token)} (seit Start: {_ms(t0)})", flush=True)
                    first_token = False
                token_count += 1
                scrub_buf += chunk.text
                if len(scrub_buf) > _FLUSH_TAIL * 2:
                    safe, scrub_buf = scrub_buf[:-_FLUSH_TAIL], scrub_buf[-_FLUSH_TAIL:]
                    yield {"type": "text", "delta": _scrub_jargon(safe)}
                    await asyncio.sleep(0)
    except GeminiServerError as exc:
        msg = "KI momentan ausgelastet, bitte nochmal versuchen." if exc.code == 503 else f"Gemini-Fehler: {exc}"
        scrub_buf += f"\n\n*{msg}*"
        print(f"[TIMING] GeminiServerError {exc.code} nach {_ms(t0)}", flush=True)

    if scrub_buf:
        yield {"type": "text", "delta": _scrub_jargon(scrub_buf)}

    print(f"[TIMING] GESAMT: {_ms(t0)} ({token_count} chunks, quelle={quelle})", flush=True)

    yield {
        "type": "meta",
        "quelle":            quelle,
        "fahrzeug_referenz": baureihe_ids,
        "vertrauen":         vertrauen,
        "belege":            belege,
    }
