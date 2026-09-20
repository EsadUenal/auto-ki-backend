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
import threading
import time
from pathlib import Path
from typing import AsyncGenerator

log = logging.getLogger(__name__)

from google import genai
from google.genai import types as genai_types

from app.config import (
    GEMINI_API_KEY, LLM_MODEL, DB_PATH, CHROMA_PATH, TAVILY_API_KEY,
    GEMINI_TIMEOUT_SECONDS, GEMINI_STREAM_TIMEOUT_SECONDS,
    GEMINI_CHAT_MAX_OUTPUT_TOKENS, GEMINI_ANALYSE_MAX_OUTPUT_TOKENS,
    GEMINI_MAX_INPUT_CHARS, GEMINI_CHAT_HISTORY_RESERVE_CHARS,
    CHAT_MAX_KONTEXT_FAHRZEUGE,
)
from app.database import get_baureihe, search_baureihen, get_alle_baureihen_kurz, get_alle_motorvarianten_kurz
from app.gemini_retry import with_retry, GeminiFehlgeschlagen, KI_UEBERLASTET_NACHRICHT
from app.recall_filter import gefilterte_rueckrufe
from app.motor_applicability import gefilterte_schwachstellen, _norm_kraftstoff
from app.web_search import (
    tavily_search, results_to_context, results_to_belege, curate_results,
    US_QUELLEN_AUSSCHLUSS as _US_QUELLEN_AUSSCHLUSS,
    KATEGORIE_TECHNISCHE_DATEN, KATEGORIE_MARKTPREISE, KATEGORIE_RUECKRUFE,
    KATEGORIE_SCHWACHSTELLEN, KATEGORIE_DIAGNOSE,
)

# Obergrenze für Quellen, die dem Nutzer in einer normalen Chat-Antwort gezeigt
# werden ("nicht mehr Quellen anzeigen als nötig" — Final Polish Quellenqualität).
# Gilt für die GESAMTE Antwort (über alle erkannten Fahrzeuge hinweg), nicht pro
# Fahrzeug — bei Mehrfahrzeug-Nachrichten sonst 3-5 Quellen PRO Auto.
MAX_CHAT_QUELLEN = 5

_SCHWACHSTELLEN_KEYWORDS = frozenset({
    "schwachstelle", "schwachstellen", "problem", "probleme", "defekt",
    "schaden", "typisch", "bekannte mängel", "mangel", "mängel", "anfällig",
})
_DIAGNOSE_KEYWORDS = frozenset({
    "geräusch", "klapper", "klopf", "quietsch", "pfeif", "schleif",
    "warnleuchte", "kontrollleuchte", "ruckel", "stotter", "springt nicht an",
    "startet nicht", "anlasser", "riecht", "raucht", "vibrier",
})


def _bestimme_kategorie(message: str) -> str:
    """
    Ordnet eine Chat-/Diagnose-Nachricht einer Quellen-Kategorie zu, damit die
    Websuche thematisch passende Domains bevorzugt (siehe web_search.py
    _KATEGORIE_BOOST): Rückrufe -> KBA/Hersteller, Marktpreise -> Marktplätze,
    Schwachstellen -> Fachmedien/Community, Diagnose -> Technik/Fachmedien,
    sonst technische Daten -> Hersteller/Technik-Datenbanken.
    Reihenfolge = Priorität bei mehrdeutigen Nachrichten.
    """
    msg = message.lower()
    if any(kw in msg for kw in _RECALL_KEYWORDS):
        return KATEGORIE_RUECKRUFE
    if any(kw in msg for kw in _PREIS_KEYWORDS):
        return KATEGORIE_MARKTPREISE
    if any(kw in msg for kw in _DIAGNOSE_KEYWORDS):
        return KATEGORIE_DIAGNOSE
    if any(kw in msg for kw in _SCHWACHSTELLEN_KEYWORDS):
        return KATEGORIE_SCHWACHSTELLEN
    return KATEGORIE_TECHNISCHE_DATEN

# Obergrenze für parallele Websuchen pro Chat-Nachricht (Mehrfahrzeug-Anfragen) — schützt
# die Tavily-Quote und die Antwortzeit bei pathologischen Nachrichten mit sehr vielen
# genannten Fahrzeugen. Deutlich über dem im Phase-1-Test verwendeten Fall (5 Fahrzeuge).
MAX_PARALLELE_SUCHEN = 8

# Wie viele der juengsten Verlaufsnachrichten die Fahrzeugerkennung heranzieht,
# wenn die aktuelle Nachricht selbst kein Fahrzeug nennt. Eine Folgefrage bezieht
# sich auf das zuletzt Besprochene — der komplette Verlauf einer langen Sitzung
# haette sonst jedes je erwaehnte Auto erneut in den Kontext gezogen.
_VERLAUF_ERKENNUNG_NACHRICHTEN = 6

# Notfall-Mindestgarantie für das Gesprächsgedächtnis, falls das Zeichenbudget
# trotz Reservierung aufgebraucht ist (z.B. exotische Env-Konfiguration).
_HISTORY_MINDEST_NACHRICHTEN = 2
_HISTORY_MINDEST_ZEICHEN = 2_000

import chromadb

# ---------- Gemini Client ----------

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY nicht gesetzt.")
        # Ohne expliziten Timeout kann eine gestörte Verbindung den Request unbegrenzt
        # hängen lassen (weder Retry noch Fehlermeldung würden je greifen). HttpOptions.
        # timeout ist in Millisekunden (SDK-intern verifiziert); der zentrale Wert
        # gilt konsistent fuer alle Consumer-Aufrufe.
        _client = genai.Client(
            api_key=GEMINI_API_KEY,
            http_options=genai_types.HttpOptions(timeout=int(GEMINI_TIMEOUT_SECONDS * 1000)),
        )
    return _client


# ---------- ChromaDB ----------

_chroma: chromadb.PersistentClient | None = None
_chroma_cols: dict[str, chromadb.Collection] = {}

# Gesetzt, solange der Start-Bootstrap (app.main) die Vektordatenbank auf einem
# frischen Volume aufbaut. Solange oeffnet NIEMAND den Chroma-Pfad: ein Client
# legte dort sonst eine leere chroma.sqlite3 an und hielte den Index danach
# veraltet im Prozess-Cache. Die Vektorsuche liefert bis dahin schlicht nichts.
CHROMA_AUFBAU = threading.Event()


def chroma_neu_laden() -> None:
    """Verwirft den gecachten Client (nach einem Neuaufbau am selben Pfad)."""
    global _chroma
    _chroma = None
    _chroma_cols.clear()


def _get_chroma():
    global _chroma
    if _chroma is None:
        _chroma = chromadb.PersistentClient(path=str(CHROMA_PATH))
    return _chroma


def _get_col(name: str) -> chromadb.Collection:
    """Collection-Objekt einmalig laden und danach aus Cache holen."""
    if CHROMA_AUFBAU.is_set():
        raise RuntimeError("Vektordatenbank wird gerade aufgebaut")
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

def _sql_context(baureihe_ids: list[str], fuel_hint_text: str | None = None) -> str:
    """Liest alle harten Fakten aus SQLite und baut einen strukturierten Kontext-String.

    §Phase 7 (Reliability-Sprint 4): Rückrufe liefen hier bisher KOMPLETT ungefiltert
    aus der DB in den Chat-Prompt (derselbe Leck-Punkt wie build_db_context im Kauf-/
    Verkaufscheck) — ein Hochvolt-/PHEV-Rückruf konnte im allgemeinen Chat also auch
    für ein erkanntes Diesel-Fahrzeug auftauchen. Der Chat kennt anders als Kauf-/
    Verkaufscheck KEINE strukturierte Motorisierung (`fahrzeug_kontext` ist reiner
    Freitext, `ChatRequest.fahrzeug_kontext`) — `fuel_hint_text` (Nutzer-Nachricht +
    Fahrzeug-Kontext-Freitext) wird deshalb best-effort auf einen Kraftstoff-Begriff
    geprüft (dieselbe `_norm_kraftstoff`-Erkennung wie evidence.py/recall_filter.py).
    Wird kein Kraftstoff erkannt, filtert `gefilterte_rueckrufe` trotzdem: variantens-
    pezifische Rückrufe landen dann als "unclear" (mit FIN-Hinweis) im Kontext statt
    roh — nie als unqualifizierte Tatsachenbehauptung."""
    erkannter_kraftstoff = _norm_kraftstoff(fuel_hint_text) if fuel_hint_text else None
    fuel_motor_match = {"kraftstoff": erkannter_kraftstoff} if erkannter_kraftstoff else None
    parts = []
    # EINE Batch-Abfrage für marke/modell/generation aller Fahrzeuge statt einer
    # eigenen Connection + Query pro Fahrzeug (relevant bei Mehrfahrzeug-Nachrichten).
    infos = _baureihe_infos(baureihe_ids)
    for bid in baureihe_ids:
        info = infos.get(bid)
        if info is None:
            continue
        marke, modell, generation = info
        data = get_baureihe(marke, modell, generation)
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

        # DATA-SAFETY-RUNTIME-GATE: dieselbe Motor-Allowed-List wie im Kaufcheck
        # (app/motor_applicability.py). Ohne sie bliebe hier exakt das Leck offen,
        # das Reliability-Sprint 4 bei den Rueckrufen schliessen musste: die
        # strukturierten Insights waeren gefiltert, der Chat-Prompt bekaeme die
        # motorfremde Schwachstelle weiterhin roh zu sehen.
        _schwachstellen_baureihe = gefilterte_schwachstellen(
            data["schwachstellen_baureihe"], fuel_motor_match, data)
        if _schwachstellen_baureihe:
            lines.append("\n### Schwachstellen Baureihe:")
            for s in _schwachstellen_baureihe:
                lines.append(
                    f"  [{s['schweregrad']}] {s['bauteil']}: {s['beschreibung']} "
                    f"(Baujahre: {s['betroffene_baujahre']})"
                )

        # §Phase 7: zentrale Allowed-List statt rohem DB-Dump (s.o.).
        # KBA-Trust-Gate: `kba_referenz_anzeige` ist None bei unplausibler/
        # kollidierender Referenz — dann kein "(Ref: …)" statt einer Falschangabe.
        erlaubte_rueckrufe = gefilterte_rueckrufe(data["rueckrufe"], fuel_motor_match, None,
                                                  marke=data["marke"])
        if erlaubte_rueckrufe:
            lines.append("\n### KBA-Rückrufe (nur für dieses Fahrzeug relevante):")
            for r in erlaubte_rueckrufe:
                ref = r.get("kba_referenz_anzeige")
                # Ein fehlendes Datum darf nicht als "None:" im Prompt landen —
                # Batch A laesst es bewusst leer, wo der amtliche Datensatz nur
                # den Sammelstempel des Erstbefuellungslaufs traegt.
                praefix = f"  {r['datum']}: " if r.get("datum") else "  "
                lines.append(praefix + r["text"] + (f" (Ref: {ref})" if ref else ""))

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
    # Pseudo-Belege: "(Quelle: Allgemeines Kfz-Wissen)" sieht aus wie eine geprüfte
    # Quelle, ist aber keine. Der System-Prompt verbietet sie — Modelle halten sich
    # daran nicht zu 100%, deshalb zusätzlich auf Code-Ebene entfernen.
    (re.compile(
        r"\s*[\(\[]\s*Quellen?\s*[:\-]?\s*"
        r"(allgemeines?\s+)?(kfz|fahrzeug|auto)?[\s\-]*"
        r"(wissen|allgemeinwissen|fachwissen|erfahrungswerte?|erfahrung|modellwissen)"
        r"\s*[\)\]]",
        re.IGNORECASE), ""),
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


def _needs_web_search(
    message: str,
    baureihe_ids: list[str],
    verlauf: list[dict] | None = None,
    sql_ctx: str = "",
) -> bool:
    """
    Entscheidet OB überhaupt eine Kfz-relevante Frage vorliegt — NICHT mehr, ob ein
    bestimmtes Themenfeld (Preis/Spec/Rückruf) betroffen ist.

    Frühere Version prüfte nur enge Keyword-Kategorien (Preis/Rückruf/Standard-Spec)
    und ließ die Websuche bei allem anderen aus — z.B. bei "Welche Ausstattungslinien
    gab es?" oder "Wie schnell ist der 0-100?", wenn genau dieses Feld in der DB
    "nicht erfasst" war. Ergebnis: Die KI beendete die Antwort mit "nicht erfasst"/
    "kein geprüftes Profil", OBWOHL die Info öffentlich verfügbar gewesen wäre, weil
    schlicht nie eine Websuche ausgeführt wurde.

    Neue Regel: Ist die Nachricht überhaupt Kfz-relevant (Baureihe erkannt ODER
    Auto-Keyword/allgemeine Fahrzeugfrage vorhanden), wird IMMER zusätzlich das Web
    durchsucht. Das Ergebnis landet als Kontext-Block im Prompt; die KI entscheidet
    dann (siehe SYSTEM_PROMPT Regel 8/9), ob und wie sie DB- und Web-Daten kombiniert.
    Nur echter Smalltalk ganz ohne Kfz-Bezug bleibt ausgenommen (kein unnötiger Call).

    FAST PATH (Performance): Ist das DB-Profil für das erkannte Fahrzeug bereits
    VOLLSTÄNDIG (kein einziges "nicht erfasst" im aufgebauten SQL-Kontext), bringt eine
    zusätzliche Websuche für Spec-Fragen keinen Mehrwert — jedes denkbare Datenfeld ist
    ja bereits abgedeckt. Die Websuche läuft in diesem Fall trotzdem weiter, wenn die
    Frage nach Preis/Rückruf fragt, da diese Angaben SICH ÄNDERN (aktueller Marktpreis,
    neue Rückrufaktionen) und die DB dafür naturgemäß nie "vollständig" sein kann.
    Betrifft nur den Fall mit erkanntem Fahrzeug UND vollständigem Profil — bei jeder
    Unvollständigkeit oder unbekanntem Fahrzeug bleibt das bisherige Verhalten exakt
    gleich (IMMER Websuche), keine Änderung an der eigentlichen Trigger-Logik.
    """
    msg = message.lower()
    if baureihe_ids:
        ist_preis_oder_rueckruf_frage = (
            any(kw in msg for kw in _PREIS_KEYWORDS) or any(kw in msg for kw in _RECALL_KEYWORDS)
        )
        db_profil_vollstaendig = bool(sql_ctx) and "nicht erfasst" not in sql_ctx
        if db_profil_vollstaendig and not ist_preis_oder_rueckruf_frage:
            return False  # Fast Path: DB deckt bereits alles ab, keine aktuellen Daten nötig
        return True  # Fahrzeug erkannt → ergänzend das Web prüfen (siehe oben)
    return (
        any(kw in msg for kw in _PREIS_KEYWORDS)
        or any(kw in msg for kw in _RECALL_KEYWORDS)
        or any(kw in msg for kw in _SPEC_KEYWORDS)
        or any(kw in msg for kw in _AUTO_KEYWORDS)
        or any(kw in msg for kw in _ALLGEMEINE_FAHRZEUGFRAGE_KEYWORDS)
    )


def _baureihe_infos(baureihe_ids: list[str]) -> dict[str, tuple[str, str, str]]:
    """Lädt (marke, modell, generation) für mehrere Baureihen-IDs auf einmal (Batch statt
    einer Query pro Fahrzeug — relevant bei Mehrfahrzeug-Anfragen mit paralleler Websuche)."""
    if not baureihe_ids:
        return {}
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    platzhalter = ",".join("?" * len(baureihe_ids))
    rows = conn.execute(
        f"SELECT id, marke, modell, generation FROM baureihe WHERE id IN ({platzhalter})",
        baureihe_ids,
    ).fetchall()
    conn.close()
    return {r["id"]: (r["marke"], r["modell"], r["generation"]) for r in rows}


# Marken-Aliase für Text-Matching (Nutzer schreibt oft Kurzform/Alias statt DB-Wert)
_MARKEN_ALIAS = {
    "vw": "volkswagen",
    "mercedes": "mercedes-benz",
    "merc": "mercedes-benz",
    "benz": "mercedes-benz",
    "skoda": "škoda",
}
_ROEMISCH = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6",
             "vii": "7", "viii": "8", "ix": "9", "x": "10", "xi": "11", "xii": "12"}
_ROEMISCH_INV = {v: k for k, v in _ROEMISCH.items()}
# Manche Marken (Skoda, Ford, Seat, Hyundai) speichern Generationen als ausgeschriebenes
# Ordinalwort statt Chassis-Code/Ziffer — Nutzer schreiben aber meist die Ziffer
# ("Octavia 3" statt "Octavia Dritte Generation").
_ORDINAL_DE = {"1": "erste", "2": "zweite", "3": "dritte", "4": "vierte", "5": "fünfte",
               "6": "sechste", "7": "siebte", "8": "achte", "9": "neunte", "10": "zehnte"}
_ORDINAL_DE_INV = {v: k for k, v in _ORDINAL_DE.items()}
_ORDINAL_EN_INV = {"first": "1", "second": "2", "third": "3", "fourth": "4",
                    "fifth": "5", "sixth": "6", "seventh": "7", "eighth": "8"}
_MK_MUSTER = re.compile(r"^mk\s*(\d+)", re.IGNORECASE)


def _wort_in_text(wort: str, text: str) -> bool:
    """Ganzwort-Suche (verhindert dass z.B. 'a4' fälschlich in 'a45' matched)."""
    if not wort:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(wort) + r"(?![a-z0-9])", text) is not None


def _nur_hubraum_treffer(ziffer: str, text: str) -> bool:
    """
    Prüft, ob JEDE Ganzwort-Fundstelle einer reinen Ziffer (z.B. "2") in Wirklichkeit Teil
    einer Hubraum-/Dezimalangabe ist (z.B. "2.0 TDI", "1,6 TSI") statt einer eigenständigen
    Generationsangabe.

    Nötig weil _gen_varianten() aus "Mk2"/"II" auch die bloße Ziffer "2" als Match-Variante
    ableitet (damit z.B. "Golf 7" die DB-Generation "VII" findet). Bei einer Mehrfahrzeug-
    Nachricht wie "Audi A4 B9 2.0 TDI ... Ford Fiesta MK7 ..." matcht diese bloße Ziffer "2"
    aus dem Audi-Motorzusatz sonst fälschlich "Ford Fiesta Mk2"/"Golf II" irgendwo sonst in
    derselben Nachricht — obwohl damit offensichtlich nur der Hubraum gemeint war.
    """
    treffer = list(re.finditer(r"(?<![a-z0-9])" + re.escape(ziffer) + r"(?![a-z0-9])", text))
    if not treffer:
        return False
    for m in treffer:
        davor  = text[max(0, m.start() - 3):m.start()]
        danach = text[m.end():m.end() + 3]
        ist_dezimal = bool(re.match(r"\s*[.,]\s*\d", danach)) or bool(re.search(r"\d\s*[.,]\s*$", davor))
        if not ist_dezimal:
            return False  # mindestens eine "echte" Fundstelle -> kein reiner Hubraum-Treffer
    return True  # alle Fundstellen waren Hubraum-Dezimalzahlen


def _gen_treffer(generation: str, text: str) -> bool:
    """Prüft ob EINE der Schreibweisen einer Generation im Text vorkommt — reine Ziffern-
    Varianten (siehe _gen_varianten) zählen dabei nicht, wenn sie ausschließlich als
    Hubraum-Dezimalzahl auftreten (siehe _nur_hubraum_treffer)."""
    for gv in _gen_varianten(generation):
        if not gv or not _wort_in_text(gv, text):
            continue
        if gv.isdigit() and len(gv) <= 2 and _nur_hubraum_treffer(gv, text):
            continue
        return True
    return False


def _gen_varianten(generation: str) -> set[str]:
    """Alle Schreibweisen einer Generation, unter denen Nutzer sie typischerweise erwähnen."""
    varianten = {generation} if generation else set()
    if generation in _ROEMISCH:
        varianten.add(_ROEMISCH[generation])
    if generation in _ROEMISCH_INV:
        varianten.add(_ROEMISCH_INV[generation])
    for wort, ziffer in {**_ORDINAL_DE_INV, **_ORDINAL_EN_INV}.items():
        if generation.startswith(wort):
            varianten.add(ziffer)
    mk = _MK_MUSTER.match(generation)
    if mk:
        varianten.add(mk.group(1))
    return varianten


def _kanon_marke(marke: str | None) -> str:
    """
    Normalisiert DB-Markenwerte auf eine kanonische Form (z.B. "VW" -> "volkswagen").
    Nötig weil die Baureihen-Tabelle dieselbe Marke inkonsistent geschrieben enthält
    (fast alle VW-Modelle: marke="Volkswagen", einzelne Zeile marke="VW") — ohne
    Normalisierung entstehen daraus zwei getrennte Gruppen bei der Generations-Eingrenzung,
    wodurch der "keine Generation im Text passt" Fallback fälschlich die per Tippfehler
    isolierte Zeile mit ausgibt (z.B. Golf 8 bei einer reinen Golf-7-Anfrage).
    """
    m = (marke or "").strip().lower()
    return _MARKEN_ALIAS.get(m, m)


def _marke_treffer(marke: str, text: str) -> bool:
    return _wort_in_text(marke, text) or any(
        _wort_in_text(alias, text) for alias, kanon in _MARKEN_ALIAS.items() if kanon == marke
    )


# Generische Motor-/Kraftstoff-Familienkürzel — kommen in Bezeichnungen wie "2.0 TDI",
# "2.0TDI" (ohne Leerzeichen) oder "1.6 TSI 150 PS" vor und sind KEINE eindeutige
# Kennung, egal wie sie geschrieben werden. Deshalb Blacklist statt Form-Heuristik.
_GENERISCHE_MOTORFAMILIEN = frozenset({
    "tdi", "tsi", "tfsi", "fsi", "cdi", "hdi", "dci", "crdi", "vtec",
    "mpi", "tce", "cdti", "dtec", "bluehdi", "bluetdi", "cgi", "tdci",
    # Antriebs- und Motorfamilien, die eine Marke quer ueber viele Baureihen
    # verbaut. "1.8 Hybrid" ist genauso wenig eine eindeutige Kennung wie
    # "2.0 TDI": ohne diese Eintraege zog eine Corolla-Nennung zusaetzlich
    # Prius und C-HR in den Kontext, weil alle drei diese Bezeichnung fuehren.
    "hybrid", "mhev", "phev", "ecoboost", "skyactiv", "ecotec", "ecoblue",
    "multijet", "puretech", "bluetec", "jtd", "thp", "gdi", "vvt", "vvti",
})


def _ist_distinktive_bezeichnung(bez: str) -> bool:
    """
    Nur eine eindeutige Modellbezeichnung mit Ziffer (z.B. "320d", "c220d", "m340i")
    gilt als verlässliche Kennung. Enthält die Bezeichnung ein generisches Kraftstoff-
    /Motorfamilien-Kürzel (siehe _GENERISCHE_MOTORFAMILIEN) — egal ob mit oder ohne
    Leerzeichen geschrieben — ist sie KEINE eindeutige Kennung: praktisch jede Marke im
    VW-Konzern (VW/Audi/Seat/Škoda) verbaut z.B. "2.0 TDI"/"2.0TDI" in Dutzenden
    Baureihen. Ohne diesen Filter würde z.B. eine Tiguan-Frage mit "2.0 TDI" auch
    Seat Leon, Audi A6 usw. fälschlich mit-matchen.
    """
    if len(bez) < 3 or not any(c.isdigit() for c in bez):
        return False
    # Reine Hubraum-Angabe ohne jede weitere Kennung (z.B. "2.0", "1.6", "1,9") ist
    # genauso generisch wie "2.0 TDI" — nur ohne Kraftstoff-Suffix im Text.
    if re.fullmatch(r"\d[.,]\d+\s*(l|liter)?", bez.strip()):
        return False
    normalisiert = bez.replace(" ", "").replace(".", "").replace(",", "")
    return not any(fam in normalisiert for fam in _GENERISCHE_MOTORFAMILIEN)


def _suche_baureihen_in_text(text: str) -> list[str]:
    """
    Findet Baureihen-IDs per DB-Abgleich gegen ALLE Baureihen (nicht nur eine feste
    Handvoll) — Marke, Modell, Generation/Chassis-Code UND Motorbezeichnung/-code
    (z.B. "320d", "EA888"), damit Anfragen zu jeder der Baureihen in der DB erkannt
    werden, nicht nur zu einem einzelnen fest verdrahteten Modell.

    Match-Regel:
      - Modell-Name als eigenständiges Wort im Text (z.B. "Golf", "Octavia") ODER
      - Marke UND Generation/Chassis-Code beide im Text — nur wenn dieser Code für
        diese Marke eindeutig einem Modell zugeordnet ist (siehe gen_ambig unten;
        verhindert dass z.B. Audis "C7" bei A6/RS6/RS7 alle drei mit-matcht)
    Wird beim Modell-Treffer ZUSÄTZLICH eine konkrete Generation im Text genannt
    (z.B. "Golf 7"), wird innerhalb dieser Modell-Gruppe auf die genannte Generation
    eingegrenzt — sonst würden pauschal alle 8 Golf-Generationen zurückkommen.
    Motorbezeichnung/-code wird analog behandelt: nur mit Markentreffer + eindeutiger
    (nicht generischer) Bezeichnung, ebenfalls generationsweise eingegrenzt.
    """
    # Gecacht (60s TTL, siehe database.py) statt bei jedem Aufruf die komplette Tabelle neu
    # zu lesen — bei Mehrfahrzeug-Nachrichten läuft diese Funktion einmal PRO Text-Segment.
    baureihen = get_alle_baureihen_kurz()
    motoren = get_alle_motorvarianten_kurz()

    marke_by_id = {b["id"]: _kanon_marke(b["marke"]) for b in baureihen}
    generation_by_id = {b["id"]: (b["generation"] or "").lower() for b in baureihen}

    # Mehrdeutige (marke, generation)-Paare ermitteln: derselbe Chassis-Code über
    # mehrere Modelle hinweg (z.B. Audi "C7" bei A6, RS6 Avant, RS7 Sportback).
    # Dort reicht Marke+Generation allein nicht — das Modell muss zusätzlich genannt werden.
    # Marke wird kanonisiert (z.B. "VW" -> "volkswagen"), damit inkonsistente DB-Schreibweisen
    # derselben Marke (siehe _kanon_marke) nicht künstlich zwei getrennte Gruppen erzeugen —
    # sonst greift unten der "keine Generation passt in der Gruppe" Fallback fälschlich auf
    # die per Tippfehler isolierte Zeile zu und liefert z.B. "Golf 8" bei einer "Golf 7"-Frage.
    gen_modelle: dict[tuple[str, str], set[str]] = {}
    for b in baureihen:
        key = (_kanon_marke(b["marke"]), (b["generation"] or "").lower())
        gen_modelle.setdefault(key, set()).add((b["modell"] or "").lower())

    # Kandidaten je (marke, modell)-Gruppe sammeln, um pro Gruppe auf eine im
    # Text genannte Generation eingrenzen zu können.
    gruppen: dict[tuple[str, str], list[dict]] = {}
    einzel_treffer: set[str] = set()  # marke+generation-Treffer ohne Modell-Erwähnung

    for b in baureihen:
        marke = _kanon_marke(b["marke"])
        modell = (b["modell"] or "").lower()
        generation = (b["generation"] or "").lower()

        # Ganzwort-Suche (nicht nur Substring!) — sonst würden kurze Modellnamen wie
        # "A6", "X1", "Q3" (2 Zeichen) nie zuverlässig matchen oder fälschlich in
        # längeren Zahlenfolgen anschlagen. len>=2 lässt genau diese kurzen, aber
        # eindeutigen Kennungen zu.
        modell_treffer = len(modell) >= 2 and _wort_in_text(modell, text)
        marke_treffer = _marke_treffer(marke, text)
        gen_treffer = _gen_treffer(generation, text)

        if modell_treffer:
            gruppen.setdefault((marke, modell), []).append({"id": b["id"], "gen_treffer": gen_treffer})
        elif marke_treffer and gen_treffer and len(gen_modelle.get((marke, generation), set())) <= 1:
            einzel_treffer.add(b["id"])

    ids: set[str] = set(einzel_treffer)
    for rows in gruppen.values():
        gen_matches = [r for r in rows if r["gen_treffer"]]
        chosen = gen_matches if gen_matches else rows
        ids.update(r["id"] for r in chosen)

    # Motorbezeichnung/-code abgleichen (z.B. "320d", "EA888") — nur wenn die
    # zugehörige Marke im Text vorkommt UND die Bezeichnung eindeutig ist (siehe
    # _ist_distinktive_bezeichnung). Ergebnisse werden wie bei Modell-Treffern je
    # (marke, bezeichnung)-Gruppe auf eine genannte Generation eingegrenzt.
    motor_gruppen: dict[tuple[str, str], list[dict]] = {}
    for m in motoren:
        bez  = (m["bezeichnung"] or "").strip().lower()
        code = (m["motorcode"] or "").strip().lower()
        bez_treffer  = _ist_distinktive_bezeichnung(bez) and _wort_in_text(bez, text)
        code_treffer = len(code) >= 4 and _wort_in_text(code, text)
        if not (bez_treffer or code_treffer):
            continue
        b_marke = marke_by_id.get(m["baureihe_id"], "")
        if not _marke_treffer(b_marke, text):
            continue
        b_generation = generation_by_id.get(m["baureihe_id"], "")
        gen_treffer_motor = _gen_treffer(b_generation, text)
        motor_gruppen.setdefault((b_marke, bez or code), []).append(
            {"id": m["baureihe_id"], "gen_treffer": gen_treffer_motor}
        )

    for rows in motor_gruppen.values():
        gen_matches = [r for r in rows if r["gen_treffer"]]
        chosen = gen_matches if gen_matches else rows
        ids.update(r["id"] for r in chosen)

    # Sortiert statt Set-Reihenfolge: die Treffermenge steuert weiter unten, welche
    # Fahrzeuge in Kontext und Websuche kommen (beides gedeckelt). Eine von der
    # Hash-Reihenfolge abhaengige Auswahl waere zwischen zwei identischen Anfragen
    # nicht reproduzierbar und in Tests nicht pruefbar.
    return sorted(ids)


# Trennt eine Nachricht in einzelne Fahrzeug-Segmente auf: Zeilenumbrüche IMMER, sonst
# Satzenden (. ? !) gefolgt von Leerraum. Bei einer Mehrfahrzeug-Nachricht ("BMW 320d G20\n
# Golf 7 GTI\n...") landet dadurch jedes Fahrzeug in seinem eigenen Segment.
_SATZTRENNER = re.compile(r"[\r\n]+|(?<=[.?!])\s+")


def _erkenne_segmente(text: str) -> list[str]:
    teile = [t.strip() for t in _SATZTRENNER.split(text) if t.strip()]
    return teile if teile else ([text.strip()] if text.strip() else [])


def _reihum(nennungen: list[tuple[str, list[str]]]) -> list[tuple[str, str]]:
    """Ordnet Baureihen-Treffer reihum ueber die Nennungen statt nennungsweise.

    Der nachgelagerte Deckel (CHAT_MAX_KONTEXT_FAHRZEUGE, MAX_PARALLELE_SUCHEN)
    schneidet am Ende ab. Bei nennungsweiser Reihenfolge verbrauchte EINE unscharfe
    Nennung ("Toyota Corolla 1.8 Hybrid" trifft mehrere Generationen) das komplette
    Budget, und ein spaeter im selben Text genanntes, eindeutig getroffenes Fahrzeug
    ("Ford Focus Mk4") fiel ganz heraus. Reihum bekommt jede Nennung zuerst ihren
    besten Treffer, bevor irgendeine einen zweiten bekommt.
    """
    treffer: dict[str, str] = {}
    tiefe = max((len(ids) for _, ids in nennungen), default=0)
    for i in range(tiefe):
        for segment, ids in nennungen:
            if i < len(ids):
                treffer.setdefault(ids[i], segment)
    return list(treffer.items())


def _erkenne_fahrzeuge(message: str, verlauf: list[dict]) -> list[tuple[str, str]]:
    """
    Erkennt Fahrzeuge samt dem Text-Segment, in dem sie genannt wurden.

    KERNFIX gegen Übermatching bei Mehrfahrzeug-Nachrichten: Jedes Segment (Zeile/Satz)
    wird EINZELN gegen die DB geprüft statt die ganze Nachricht als einen Textblob zu
    behandeln. Sonst kann ein generischer Token aus Segment A (z.B. die Ziffer "2" aus
    "Audi A4 B9 2.0 TDI") fälschlich ein Fahrzeug aus Segment B matchen (z.B. "Ford Fiesta
    Mk2"), sobald irgendwo in der GESAMTEN Nachricht auch "ford" vorkommt.

    Rückgabe: Liste von (baureihe_id, segment_text) in Erwähnungsreihenfolge — bei
    Mehrfachnennung derselben Baureihe wird nur das ERSTE Segment behalten (Basis für die
    Websuche: ein Kontext-Schnipsel reicht, und Mehrfachsuche für dieselbe Baureihe wird
    dadurch automatisch vermieden).

    Verlauf wird NUR einbezogen wenn die aktuelle Nachricht selbst Kfz-Kontext hat
    (Auto-Keyword vorhanden) — verhindert falsche DB-Badges bei Smalltalk wie
    'bro wie gehts?' nach einem vorherigen Kfz-Gespräch.
    """
    nennungen: list[tuple[str, list[str]]] = []
    for segment in _erkenne_segmente(message):
        ids = _suche_baureihen_in_text(segment.lower())
        if ids:
            nennungen.append((segment, ids))
    if nennungen:
        return _reihum(nennungen)

    # Verlauf nur hinzuziehen wenn die aktuelle Nachricht Kfz-Kontext zeigt
    # (echte Folgefrage wie "Motoren?"/"Und wie groß ist der Tank?", nicht Smalltalk
    # wie "bro wie gehts?"). Auch Spec-/Preis-/Rückruf-Keywords zählen als Kfz-Kontext —
    # sonst verliert eine reine Folgefrage wie "Und der Tank?" (kein klassisches
    # Auto-Keyword) den Fahrzeugbezug aus dem Verlauf komplett.
    msg_lower = message.lower()
    if not any(
        kw in msg_lower
        for kw in (*_AUTO_KEYWORDS, *_SPEC_KEYWORDS, *_PREIS_KEYWORDS, *_RECALL_KEYWORDS)
    ):
        return []

    # Derselbe Segment-Fix wie oben fuer die aktuelle Nachricht — er fehlte hier.
    # Der Verlauf wurde zu EINEM Textblob zusammengeklebt und als Ganzes gegen die
    # DB geprueft: ein Gespraech ueber "Toyota Corolla" und "Ford Focus Mk4" matchte
    # dadurch zusaetzlich Supra, Hilux, Mustang und Kuga, weil Marken-Token aus der
    # einen Zeile mit Zifferntoken aus einer anderen kombiniert wurden. Jede
    # Verlaufsnachricht wird deshalb einzeln und segmentweise geprueft.
    for msg in reversed(verlauf[-_VERLAUF_ERKENNUNG_NACHRICHTEN:]):
        for segment in _erkenne_segmente(msg.get("text", "")):
            ids = _suche_baureihen_in_text(segment.lower())
            if ids:
                nennungen.append((message, ids))
    return _reihum(nennungen)


def _detect_baureihe_ids(message: str, verlauf: list[dict]) -> list[str]:
    """Kompatibilitäts-Wrapper um _erkenne_fahrzeuge() — nur die IDs, ohne Segment-Text."""
    return [bid for bid, _ in _erkenne_fahrzeuge(message, verlauf)]


# ---------- Prompt-Budget ----------

# Trennzeichen, an denen _kuerze_kontext schneiden darf, von grob nach fein.
_KONTEXT_TRENNER = ("\n\n---\n\n", "\n\n", "\n")


def _kuerze_kontext(kontext: str, budget: int) -> str:
    """Kuerzt den DB-/Web-Kontext auf ``budget`` Zeichen — an Blockgrenzen.

    Der Verlauf bekommt sein Budget ZUERST (GEMINI_CHAT_HISTORY_RESERVE_CHARS);
    der Kontext ist das, was gekuerzt werden darf. Vorher war es umgekehrt: der
    Kontext durfte beliebig wachsen und der Verlauf fiel als Restgroesse lautlos
    auf 0 — das Modell verlor mitten im Gespraech sein Gedaechtnis, waehrend
    dieselbe Historie die Websuche noch gesteuert hatte (Quellenchips waren da,
    die Erinnerung nicht).

    Geschnitten wird an der groebsten Grenze, die noch passt, damit kein halbes
    Fahrzeugprofil und kein angeschnittener Satz im Prompt landet.
    """
    if budget <= 0:
        return ""
    if len(kontext) <= budget:
        return kontext
    for trenner in _KONTEXT_TRENNER:
        kopf = kontext[:budget]
        schnitt = kopf.rfind(trenner)
        if schnitt > budget // 3:
            return kopf[:schnitt].rstrip()
    return kontext[:budget].rstrip()


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
8. JEDES konkret angefragte Datenfeld (nicht nur Tankgröße/Kofferraum/Anhängelast — das gilt für JEDE Zahl oder Eigenschaft, z. B. auch 0–100-Zeit, Vmax, Ausstattungslinien, Getriebeoptionen, Rückrufe, Motorcodes): Nutze zuerst die harten Zahlen aus dem DB-Kontext. Steht dort "nicht erfasst" oder fehlt das Feld komplett, prüfe IMMER zuerst den Block "=== AKTUELLE WEB-ERGEBNISSE ===" (dieser ist bei jeder Kfz-Frage automatisch mitgeliefert, sobald ein Fahrzeug erkannt wurde) und übernimm den Web-Wert, klar als Web-Quelle gekennzeichnet.
9. ABSOLUTES VERBOT: Beende eine Antwort NIEMALS mit "nicht erfasst", "kein geprüftes Profil" o. ä., ohne vorher den Web-Ergebnisse-Block im Kontext tatsächlich geprüft und genutzt zu haben. Ist ein Web-Block vorhanden und enthält irgendeinen brauchbaren Hinweis zum gefragten Wert, MUSST du ihn verwenden — auch wenn er nur ungefähr oder aus einer Quelle mit geringerer Sicherheit stammt. "Kein geprüftes Profil"/"nicht erfasst" ist NUR erlaubt, wenn WEDER die Datenbank NOCH der Web-Block (falls vorhanden) einen verwertbaren Hinweis zum konkret gefragten Feld liefern.

— GESPRÄCHSGEDÄCHTNIS (wichtig) —
- Du hast Zugriff auf den bisherigen Gesprächsverlauf. Nutze ihn aktiv.
- Kurze Folgefragen ("Motoren?", "Und der Verbrauch?", "Was kostet das?") beziehen sich IMMER auf das zuletzt besprochene Fahrzeug — nie auf ein unbekanntes neues Modell.
- Wenn der Kontext kein Profil enthält, aber der Verlauf ein Fahrzeug nennt, beantworte die Frage trotzdem auf Basis des Verlaufs + allg. Kfz-Wissens.
- Stelle eine kurze Rückfrage NUR wenn du wirklich nicht weißt, worauf sich die Frage bezieht.

— QUELLENANGABEN IM TEXT (hart) —
- Schreibe NIEMALS eine Quellenangabe in den Fließtext, die keine echte, nachprüfbare Quelle ist. Verboten sind insbesondere Formulierungen wie "(Quelle: Allgemeines Kfz-Wissen)", "(Quelle: Erfahrungswerte)", "(Quelle: Allgemeinwissen)" oder Ähnliches. Sie sehen aus wie ein geprüfter Beleg, sind aber keiner.
- Woher eine Aussage kommt, drückst du sprachlich aus, nicht als Klammer-Beleg: geprüfte Modelldaten nennst du direkt, Web-Erkenntnisse mit höchstens einem unaufdringlichen Hinweis ("Aktuelle Marktangebote zeigen…"), allgemeines Fachwissen als das, was es ist ("als Faustregel gilt…", "erfahrungsgemäß…"). Die echten Quellen werden dem Nutzer automatisch unterhalb der Antwort angezeigt.

— ZUVERLÄSSIGKEIT UND SCHWACHSTELLEN (differenziert statt absolut) —
- Vermeide absolute Zuverlässigkeits-Urteile ("eines der zuverlässigsten Autos überhaupt", "praktisch unkaputtbar", "hält ewig"). Formuliere abgestuft und mit Bezug: "gilt in seiner Klasse als überdurchschnittlich zuverlässig — worauf du trotzdem achten solltest: …".
- Zuverlässigkeit ist IMMER variantenabhängig. Beziehe, soweit bekannt, Generation, Motorvariante, Baujahrsspanne und gegebenenfalls Getriebe ein, statt über eine ganze Baureihe zu pauschalieren.
- Fragt der Nutzer ausdrücklich nach Zuverlässigkeit, Schwachstellen oder Kaufrisiken, darfst du einen dir bekannten motor- oder baujahrsspezifischen Risikopunkt NICHT weglassen, nur weil er die Empfehlung relativiert. Gilt der Punkt nur für bestimmte Varianten oder Baujahre, sage genau das dazu.
- Überlade die Antwort trotzdem NICHT mit Warnlisten: nenne die wenigen Punkte, die für Kaufentscheidung und Folgekosten wirklich relevant sind — nicht jeden theoretisch denkbaren Defekt.
- Steht im Kontext nichts zu einem Risiko, erfinde keins. Kennst du einen Punkt nur als allgemein bekanntes Fachwissen, benenne ihn als solchen ("gilt je nach Motorvariante als bekannter Prüfpunkt") und empfiehl die konkrete Prüfung.

— ANTRIEBSART: NUR LIEFERN, WAS GEFRAGT IST (transparent abweichen) —
- Nennt der Nutzer eine Antriebsart oder Getriebeart (Benziner, Diesel, Hybrid, Elektro, Automatik, Schaltgetriebe), halte dich zuerst daran.
- Ein abweichender Vorschlag ist erlaubt, wenn er sachlich besser passt — aber NUR ausdrücklich gekennzeichnet, nicht stillschweigend als Erfüllung des Wunsches. Zum Beispiel: "Falls für dich auch ein Benzin-Hybrid infrage kommt: …". Ein Vollhybrid ist kein reiner Benziner und darf nicht als solcher durchgehen.
- Dasselbe gilt für Budget und Karosserieform: abweichen ja, verschweigen nein.

— FACHBEGRIFFE PRÄZISE WÄHLEN —
- Nutze die technisch korrekte Bezeichnung statt der naheliegenden Alltagsbezeichnung, wenn beides auseinanderfällt. Beispiel Vollhybrid: die 12-V-Batterie versorgt dort das Bordnetz und dreht den Verbrenner NICHT über einen klassischen Anlasser — korrekt ist 12-V-Bordnetz- beziehungsweise Hilfsbatterie, nicht "Starterbatterie". Analog überall dort, wo die Bauart die übliche Bezeichnung unzutreffend macht.

— ANPASSUNG AN DEN NUTZER (so flexibel wie nötig) —
- Erkenne am Schreibstil des Nutzers, wie du antwortest: Schreibt er locker und einfach, antworte locker und einfach. Nutzt er Fachbegriffe und fragt technisch, antworte präzise und fachlich.
- Erkläre Fachbegriffe kurz, wenn der Nutzer wie ein Laie wirkt. Lass sie stehen, wenn er wie ein Kenner wirkt.
- Standardlänge: kurz und auf den Punkt. Wird nach Details gefragt, antworte ausführlich und strukturiert.

— STIL: DIREKT UND NATÜRLICH (gilt für JEDE Antwort) —
- Steig sofort mit der wichtigsten Information ein. KEINE Einleitungsfloskeln wie "Gerne", "Natürlich", "Klar", "Kein Problem", "Ich helfe dir gerne weiter" — und keine Wiederholung der Frage als Einleitungssatz.
- Wichtigste Information zuerst, Begründung/Kontext danach — nie umgekehrt.
- Kein Floskel-Schluss ("Ich hoffe, das hilft!", "Bei weiteren Fragen stehe ich gerne zur Verfügung!", "Lass es mich wissen, falls..."). Die Antwort endet mit der letzten inhaltlichen Aussage.
- Fehlt eine Angabe (weder DB noch Web liefern sie): das in einem knappen Nebensatz benennen und SOFORT mit dem Nützlichen weitermachen (Richtwert, Faustregel, gezielte Rückfrage) — keine längere Entschuldigung oder Erklärung, warum die Info fehlt.
- Bei mehreren Fahrzeugen/Werten in einer Antwort: nicht denselben Satzbau für jeden Punkt wiederholen — natürlich variieren, keine identischen Formulierungsschablonen.
- Keine Wiederholung derselben Information in zwei Sätzen oder Absätzen.

— EINFACHE FAKTENFRAGEN (z. B. "Wie groß ist der Tank?", "Wie viel PS hat der 320d?", "Welche Felgengröße ist Serie?") —
- Antworte kompakt: 1–3 Sätze oder eine kurze Liste. Keine Einleitung, keine Wiederholung der Frage, keine unaufgeforderte Zusatz-Erklärung.
- Ausführliche Antworten mit Zwischenüberschriften sind nur für komplexe Anfragen angemessen (Vergleiche, Kaufberatung, umfassende Erklärungen wie "Erzähl mir alles über…").

— DIAGNOSE-MODUS (Geräusche, Warnleuchten, Leistungsverlust, Startprobleme, "mein Auto macht komische Sachen" o. ä.) —
Du bist hier ein Diagnose-Assistent, kein Lexikon. Bei einer unklaren Problembeschreibung NIEMALS sofort eine lange Liste möglicher Ursachen aufzählen — das hilft dem Nutzer nicht und wirkt wie ein Ursachen-Dump.
1. Reicht die Beschreibung nicht für eine sinnvolle Eingrenzung, stelle ZUERST 2–4 gezielte Rückfragen — OHNE Einleitungssatz davor (auch nicht "Damit ich dir helfen kann" oder "Um das einzugrenzen"), keine Vorab-Ursachenliste. Steig direkt mit der ersten Frage ein. Passe die Fragen dynamisch an das Problem an, z. B.:
   - Geräusch: Art (Klopfen/Quietschen/Klappern/Pfeifen/Schleifen), wann (Kaltstart, Bremsen, Lenken, Beschleunigen, Kurvenfahrt), Lokalisierung (vorne/hinten/Motor/Rad), seit wann, wird es schlimmer.
   - Warnleuchte: IMMER sofort nach Farbe (gelb/orange/rot), Symbol und Verhalten (leuchtet dauerhaft oder blinkt) fragen — das ist die wichtigste Information, ohne sie ist jede Einschätzung reine Spekulation.
   - Leistungsverlust: wann tritt es auf (unter Last, Autobahn, Kaltstart), ruckelt/stottert der Motor, ist eine Kontrollleuchte an, seit wann.
   - Startprobleme: dreht der Anlasser durch oder passiert gar nichts, Klick-Geräusch beim Startversuch, Batterie/Kälte-Zusammenhang, seit wann.
2. Erst wenn genug Antworten vorliegen (aus dieser Nachricht oder dem Verlauf), grenze auf die 1–3 wahrscheinlichsten Ursachen ein (wahrscheinlichste zuerst) — keine erschöpfende Liste aller theoretisch denkbaren Defekte.
3. Umgangssprachliche Beschreibungen ("der spinnt", "komisches Geräusch") verstehst du inhaltlich genauso, gehst aber identisch vor: zuerst gezielt nachfragen, nicht raten.
4. Auch wenn Web-Ergebnisse zum Symptom im Kontext stehen: nutze sie erst NACH den Rückfragen zur Einordnung, nicht um vorab eine lange Ursachenliste zu generieren.
5. Bei sicherheitsrelevanten Symptomen (Bremsen, Lenkung, rote Warnleuchte) die Dringlichkeit klar benennen (z. B. "nicht mehr weiterfahren, sofort in einer Werkstatt abklären lassen") statt sie im selben Ton wie ein kosmetisches Problem abzuhandeln.

— KAUFBERATUNG IM GESPRÄCH (z. B. "Welches Auto soll ich kaufen?", nicht der separate Kauf-Check-Tab) —
Frage zuerst nur die wichtigsten Eckdaten kompakt ab (z. B. Budget, Nutzung/km pro Jahr, gewünschte Fahrzeugklasse, neu oder gebraucht) statt sofort eine lange Empfehlungsliste zu liefern — OHNE Einleitungssatz davor (auch nicht "Damit ich dir ein passendes Auto empfehlen kann"), steig direkt mit der ersten Frage ein. Erst mit diesen Angaben eine konkrete, kurze Empfehlung geben.

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
- Bevorzuge europäische/deutsche Spezifikationen (WLTP, EU-Ausstattung, km/h, Liter) gegenüber US-Marktdaten (mph, US-Gallonen, US-Ausstattungslinien) — diese unterscheiden sich häufig vom hiesigen Modell. Wirkt ein Web-Ergebnis wie eine US-spezifische Angabe, kennzeichne das kurz oder nutze es nicht.
- Prüfe, ob die Web-Quelle zur RICHTIGEN Modellgeneration passt (z.B. nicht Vorgänger- oder Nachfolgegeneration verwechseln) — steht die Generation im DB-Kontext, gleiche sie mit der Quelle ab, bevor du den Wert übernimmst.

Antworte immer auf Deutsch. Steig direkt mit der wichtigsten Information oder — bei Rückfragen — direkt mit der ersten Frage ein. KEIN Einleitungssatz davor, egal welcher: nicht "Gerne", "Hier ist...", "Damit ich dir helfen kann", "Um das einzugrenzen" o.ä. Kein Floskel-Schluss.
{web_hinweis}
KONTEXT AUS GEPRÜFTER DATENBANK:
{kontext}"""

# Wird nur angehängt, wenn tatsächlich ein Web-Ergebnisblock im Kontext steht (hat_web=True).
# Direkt vor dem Kontext platziert (höchste Recency im Prompt) statt nur in Regel 8/9 weiter
# oben — bei langen System-Prompts verlieren frühere Anweisungen an Gewicht gegenüber dem,
# was unmittelbar vor dem eigentlichen Kontext steht. Das war die eigentliche Ursache dafür,
# dass das Modell trotz vorhandener Web-Ergebnisse gelegentlich "kein geprüftes Profil"/
# "nicht erfasst" antwortete, obwohl weiter oben (Regel 9) bereits das Gegenteil gefordert war.
_WEB_HINWEIS = """
— LETZTER UND WICHTIGSTER HINWEIS VOR DEM KONTEXT —
Der Kontext unten enthält einen Block "=== AKTUELLE WEB-ERGEBNISSE ===". Prüfe ihn für JEDES
angefragte Datenfeld, das im DB-Teil als "nicht erfasst" markiert ist oder dort ganz fehlt.
Enthält der Web-Block einen verwertbaren Hinweis zu genau diesem Feld, MUSST du ihn in deiner
Antwort verwenden (klar als Web-Quelle gekennzeichnet) — auch wenn er nur ungefähr ist.
"Kein geprüftes Profil" oder "nicht erfasst" als Antwort ist in diesem Fall NICHT erlaubt.
"""


# ---------- Abbruchgrund des Modells ----------

# Sichtbarer Abschluss, wenn das Modell am Output-Limit stoppt. Eine Antwort darf
# nie stumm mitten im Satz enden — der Nutzer muss erkennen, dass etwas fehlt,
# und wissen, wie er weiterkommt.
_ABGESCHNITTEN_HINWEIS = (
    "\n\n---\n\n*Diese Antwort wurde gekürzt, weil sie die maximale Länge erreicht hat. "
    "Frag gezielt nach dem, was dir noch fehlt — z. B. nach einem einzelnen Fahrzeug —, "
    "dann bekommst du den Rest vollständig.*"
)


def _finish_reason(chunk) -> object | None:
    """Liest den Abbruchgrund aus einem Stream-Chunk, tolerant gegenüber SDK-Formen."""
    kandidaten = getattr(chunk, "candidates", None)
    if not kandidaten:
        return None
    return getattr(kandidaten[0], "finish_reason", None)


def _ist_abgeschnitten(finish_reason) -> bool:
    """True, wenn das Modell wegen des Output-Limits gestoppt hat (nicht regulär)."""
    if finish_reason is None:
        return False
    return str(getattr(finish_reason, "name", finish_reason)).upper().endswith("MAX_TOKENS")


# ---------- Haupt-Funktion: Chat (Streaming) ----------

async def chat_stream(
    message: str,
    verlauf: list[dict],
    fahrzeug_kontext: str | None = None,
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
    fahrzeuge = _erkenne_fahrzeuge(message, verlauf)  # [(baureihe_id, segment_text), ...]
    # Discover-Fast-Path: ist ein Fahrzeug vorausgewählt (Entdecken-Seite) und die
    # Text-Erkennung findet nichts (typische Folgefrage ohne Modellnennung), die
    # Baureihe DETERMINISTISCH aus dem übergebenen Kontext übernehmen — kein erneutes
    # Raten, zuverlässiger DB-Kontext -> seltener unnötige Websuche. Der Kontext wird
    # pro Request übergeben (kein geteilter Cache) -> kein Leak zwischen Fahrzeugen.
    if not fahrzeuge and fahrzeug_kontext:
        for bid in _suche_baureihen_in_text(fahrzeug_kontext.lower()):
            fahrzeuge.append((bid, fahrzeug_kontext))
    # Deckel gegen Kontext-Explosion: die Text-Erkennung matcht grosszuegig (eine
    # Corolla-Nennung trifft mehrere Generationen, dazu Prius/C-HR). Ohne Deckel
    # landeten in einer Mehrfahrzeug-Frage >100k Zeichen Fahrzeugprofile im
    # System-Prompt. Die Reihenfolge ist die Erwaehnungsreihenfolge — der Deckel
    # behaelt also das zuerst Genannte.
    if len(fahrzeuge) > CHAT_MAX_KONTEXT_FAHRZEUGE:
        print(f"[TIMING] detect_baureihe: {len(fahrzeuge)} Treffer -> auf "
              f"{CHAT_MAX_KONTEXT_FAHRZEUGE} begrenzt", flush=True)
        fahrzeuge = fahrzeuge[:CHAT_MAX_KONTEXT_FAHRZEUGE]
    baureihe_ids = [bid for bid, _ in fahrzeuge]
    print(f"[TIMING] detect_baureihe: {_ms(t_detect)} -> ids={baureihe_ids} (ctx={bool(fahrzeug_kontext)})", flush=True)

    # ── 1. DB-Kontext aufbauen ───────────────────────────────────────────────
    # Status nur zeigen wenn tatsächlich eine Baureihe erkannt wurde — bei normalem
    # Smalltalk bleibt die neutrale "Denke nach…"-Ladeanimation ohne technisches Label.
    if baureihe_ids:
        yield {"type": "status", "text": "Prüfe Datenbank…"}
        await asyncio.sleep(0)

    t_db = time.perf_counter()
    if baureihe_ids:
        # SQLite (_sql_context) und ChromaDB (_vector_search) sind unabhängige,
        # blockierende Aufrufe — parallel in Threads statt nacheinander ausführen.
        # §Phase 7: Freitext (Nutzer-Nachricht + Fahrzeug-Kontext) für die best-effort
        # Kraftstofferkennung im Rückruf-Filter — kein strukturierter Motor im Chat.
        _fuel_hint = f"{message} {fahrzeug_kontext or ''}"
        sql_ctx, vec_docs = await asyncio.gather(
            asyncio.to_thread(_sql_context, baureihe_ids, _fuel_hint),
            asyncio.to_thread(_vector_search, message, baureihe_ids),
        )
    else:
        sql_ctx, vec_docs = "", []
    vec_ctx = "\n\n".join(vec_docs) if vec_docs else ""
    print(f"[TIMING] db+vector: {_ms(t_db)} (sql={len(sql_ctx)} chars, vec={len(vec_docs)} docs)", flush=True)

    quelle    = "datenbank" if baureihe_ids else "gemischt"
    vertrauen = "hoch"      if baureihe_ids else "mittel"
    belege: list[dict] = []

    # ── 2. Websuche: EIN Fahrzeug = eine eigene Suche, alle parallel ────────
    # Kernfix gegen den Mehrfahrzeug-Bug: vorher gab es nur EINE Tavily-Suche pro
    # Nachricht, verankert an einem beliebigen (nicht mal deterministisch ersten)
    # erkannten Fahrzeug — bei 5 genannten Autos bekamen 4 davon dadurch NIE
    # Web-Daten. Jetzt bekommt jedes erkannte Fahrzeug seine eigene, auf sein
    # Text-Segment fokussierte Suche; alle Suchen laufen parallel (asyncio.gather),
    # damit die Antwortzeit bei Mehrfahrzeug-Anfragen nicht linear mit der Anzahl
    # Fahrzeuge steigt.
    web_ctx = ""
    if _needs_web_search(message, baureihe_ids, verlauf, sql_ctx) and TAVILY_API_KEY:
        yield {"type": "status", "text": "Durchsuche das Web…"}
        await asyncio.sleep(0)

        t_web = time.perf_counter()
        kategorie = _bestimme_kategorie(message)

        if fahrzeuge:
            # Begrenzung gegen pathologische Massen-Anfragen (z.B. 50 Fahrzeuge in einer
            # Nachricht) — schützt die Tavily-Quote und die Antwortzeit.
            begrenzt = fahrzeuge[:MAX_PARALLELE_SUCHEN]
            infos = _baureihe_infos([bid for bid, _ in begrenzt])

            async def _suche_fuer_fahrzeug(bid: str, segment: str) -> tuple[str, list[dict]]:
                info = infos.get(bid)
                if info is None:
                    return bid, []
                marke, modell, generation = info
                flat_segment = " ".join(segment.split())[:150]
                # "Deutschland" explizit ergänzen, damit europäische statt US-Marktdaten
                # bevorzugt werden (US-Modelljahre/-Ausstattungen weichen oft ab).
                query = f"{marke} {modell} {generation} {flat_segment} Deutschland"[:250]
                # count=3 statt Tavily-Default 5: für einfache Chat-Faktenfragen (im
                # Gegensatz zum Kaufcheck, der bewusst 5 Quellen für Marktbreite nutzt)
                # reichen 3 diverse Quellen, kombiniert mit dem DB-Kontext, aus.
                results = await tavily_search(query, count=3, exclude_domains=_US_QUELLEN_AUSSCHLUSS)
                # Robuster Fallback: liefert die spezifische Query nichts, mit breiterer
                # Query nachsuchen statt komplett leer zu bleiben.
                if not results:
                    results = await tavily_search(
                        f"{marke} {modell} Deutschland", count=3, exclude_domains=_US_QUELLEN_AUSSCHLUSS
                    )
                if not results:
                    results = await tavily_search(
                        f"{marke} {modell} {generation} technische Daten", count=3,
                        exclude_domains=_US_QUELLEN_AUSSCHLUSS,
                    )
                # Quellenqualität: Social Media raus, Duplikate raus, nach
                # Vertrauenswürdigkeit sortiert, pro Fahrzeug auf 3 begrenzt
                # (Grobfilter — der globale Cap über alle Fahrzeuge folgt unten).
                return bid, curate_results(results, kategorie=kategorie, max_results=3)

            ergebnisse = await asyncio.gather(
                *(_suche_fuer_fahrzeug(bid, segment) for bid, segment in begrenzt)
            )

            bloecke: list[str] = []
            alle_roh: list[dict] = []
            for bid, results in ergebnisse:
                if not results:
                    continue
                marke, modell, generation = infos[bid]
                block = results_to_context(results)
                if block:
                    bloecke.append(f"### Web-Ergebnisse für: {marke} {modell} {generation}\n{block}")
                alle_roh.extend(results)

            web_ctx = "\n\n".join(bloecke)
            # Globaler Cap über ALLE Fahrzeuge hinweg (nicht pro Fahrzeug) — bei
            # Mehrfahrzeug-Nachrichten bekommt der Nutzer sonst 3-5 Quellen PRO
            # Auto statt insgesamt "so viele wie nötig" (siehe MAX_CHAT_QUELLEN).
            belege = results_to_belege(curate_results(alle_roh, kategorie=kategorie, max_results=MAX_CHAT_QUELLEN))
            print(
                f"[TIMING] tavily (parallel, {len(begrenzt)} Fahrzeuge): {_ms(t_web)} "
                f"-> {sum(len(r) for _, r in ergebnisse)} Ergebnisse gesamt, {len(belege)} nach Kuration",
                flush=True,
            )
        else:
            # Kein Fahrzeug erkannt (z.B. reine Preis-/Rückruf-Frage ohne Modellbezug) —
            # eine einzelne Suche über die Gesamtnachricht wie bisher.
            flat_msg = " ".join(message.split())[:150]
            verlauf_text = " ".join(m.get("text", "") for m in verlauf[-2:])[:60]
            search_query = f"{flat_msg} {verlauf_text} Deutschland"[:250]
            web_results = await tavily_search(search_query, count=3, exclude_domains=_US_QUELLEN_AUSSCHLUSS)
            web_results = curate_results(web_results, kategorie=kategorie, max_results=MAX_CHAT_QUELLEN)
            if web_results:
                web_ctx = results_to_context(web_results)
                belege = results_to_belege(web_results)
            print(f"[TIMING] tavily (ohne Fahrzeug): {_ms(t_web)} -> {len(web_results)} Ergebnisse", flush=True)

        if web_ctx:
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

    # Prompt-Budget: Verlauf zuerst reservieren, Kontext bekommt den Rest.
    web_hinweis = _WEB_HINWEIS if hat_web else ""
    rahmen_len = len(SYSTEM_PROMPT.format(kontext="", web_hinweis=web_hinweis))
    kontext_budget = (
        GEMINI_MAX_INPUT_CHARS
        - GEMINI_CHAT_HISTORY_RESERVE_CHARS
        - rahmen_len
        - len(message)
    )
    if len(kontext) > kontext_budget:
        print(f"[TIMING] kontext gekuerzt: {len(kontext)} -> {max(0, kontext_budget)} chars "
              f"(Verlaufsbudget {GEMINI_CHAT_HISTORY_RESERVE_CHARS} bleibt reserviert)", flush=True)
        kontext = _kuerze_kontext(kontext, kontext_budget)

    system = SYSTEM_PROMPT.format(
        kontext=kontext,
        web_hinweis=web_hinweis,
    )

    # ── 4. Gemini-Aufruf (Streaming) ────────────────────────────────────────
    history = []
    # Neueste Historie behalten, aber das serverseitig erzeugte Tokenvolumen
    # unabhängig von der Zahl maximal langer Einzelnachrichten hart deckeln.
    # Der Kontext wurde oben bereits so gekürzt, dass hier mindestens
    # GEMINI_CHAT_HISTORY_RESERVE_CHARS übrig bleiben.
    rest = max(0, GEMINI_MAX_INPUT_CHARS - len(system) - len(message))
    ausgewaehlt = []
    for i, msg in enumerate(reversed(verlauf)):
        text = msg.get("text", "")
        if not text:
            continue
        # Mindestgarantie: der jüngste Gesprächsschritt (letzte Frage + letzte
        # Antwort) kommt IMMER mit, auch wenn die Budgetrechnung durch eine
        # abweichende Env-Konfiguration entgleist. Ohne diese Garantie ist eine
        # Folgefrage im selben Chat wertlos — das war der eigentliche Defekt.
        if rest <= 0:
            if i >= _HISTORY_MINDEST_NACHRICHTEN:
                break
            rest = _HISTORY_MINDEST_ZEICHEN
        if len(text) > rest:
            # Angeschnittene Nachricht als solche kennzeichnen, damit das Modell
            # den Anfang nicht als vollständigen Gesprächsbeitrag liest. Die
            # Markierung zählt zum Budget, sonst wächst der Prompt bei jeder
            # gekürzten Nachricht ein Stück über die Grenze hinaus.
            marke = "[…] "
            text = marke + text[-max(0, rest - len(marke)):]
        ausgewaehlt.append((msg, text))
        rest -= len(text)
    for msg, text in reversed(ausgewaehlt):
        role = "user" if msg.get("rolle") == "user" else "model"
        history.append({"role": role, "parts": [{"text": text}]})
    print(f"[TIMING] verlauf: {len(verlauf)} Nachrichten -> {len(history)} im Prompt", flush=True)

    client = _get_client()
    contents = history + [{"role": "user", "parts": [{"text": message}]}]
    cfg = genai_types.GenerateContentConfig(
        system_instruction=system, temperature=0.3,
        max_output_tokens=GEMINI_CHAT_MAX_OUTPUT_TOKENS,
    )

    t_gemini_init = time.perf_counter()
    try:
        # Async statt sync-Client: with_retry_sync() nutzte bisher time.sleep()
        # während eines 429/503-Backoffs — das blockiert innerhalb einer async
        # Funktion den KOMPLETTEN Event-Loop und damit ALLE gleichzeitigen
        # Nutzer-Requests, nicht nur den eigenen. Der async-Client + await
        # with_retry() (asyncio.sleep) gibt den Loop währenddessen frei.
        response = await with_retry(lambda: client.aio.models.generate_content_stream(
            model=LLM_MODEL, contents=contents, config=cfg,
        ))
    except GeminiFehlgeschlagen as exc:
        log.warning("Chat: Gemini-Totalausfall beim Start des Streams: %s", exc)
        yield {"type": "text", "delta": KI_UEBERLASTET_NACHRICHT}
        yield {"type": "meta", "quelle": "fehler", "fahrzeug_referenz": [],
               "vertrauen": "niedrig", "belege": []}
        return
    except Exception as exc:
        # Sicherheitsnetz: JEDER sonst unerwartete Fehler (z.B. ein Bug in der
        # SDK-Fehlerklassifizierung) darf den Generator nie ungefangen abbrechen —
        # der Nutzer soll immer eine verständliche Meldung statt eines
        # abgebrochenen Streams ohne jede Antwort sehen.
        log.exception("Chat: unerwarteter Fehler beim Start des Gemini-Streams")
        yield {"type": "text", "delta": KI_UEBERLASTET_NACHRICHT}
        yield {"type": "meta", "quelle": "fehler", "fahrzeug_referenz": [],
               "vertrauen": "niedrig", "belege": []}
        return
    print(f"[TIMING] gemini iterator erstellt: {_ms(t_gemini_init)}", flush=True)

    first_token = True
    t_first_token = time.perf_counter()
    token_count = 0
    # Rolling-Buffer: Text wird erst geflusht wenn genug Puffer vorhanden ist, damit
    # ein Jargon-Begriff (z.B. "ungeprüft") nicht über zwei Chunks hinweg zerschnitten
    # und dadurch am Filter vorbeigeschmuggelt wird. FLUSH_TAIL > längster Begriff.
    _FLUSH_TAIL = 24
    scrub_buf = ""
    finish_reason = None
    try:
        async with asyncio.timeout(GEMINI_STREAM_TIMEOUT_SECONDS):
            async for chunk in response:
                # Abbruchgrund mitlesen: Gemini liefert ihn am letzten Chunk. Wurde
                # er — wie bisher — ignoriert, endete eine am Output-Limit
                # abgeschnittene Antwort LAUTLOS mitten im Satz und sah für den
                # Nutzer aus wie eine vollständige Antwort.
                fr = _finish_reason(chunk)
                if fr is not None:
                    finish_reason = fr
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
    except Exception as exc:
        # Fehler MITTEN im Stream (503, Netzwerkabbruch, o.ä.) — der bereits
        # gesendete Teiltext bleibt für den Nutzer sichtbar (bessere UX als ihn
        # zu verwerfen), ergänzt um einen kurzen, verständlichen Hinweis statt
        # eines rohen Fehlertexts. Der Generator wird NIE ungefangen abgebrochen.
        log.warning("Chat: Fehler während des Streamens nach %s: %s", _ms(t0), exc)
        scrub_buf += f"\n\n*{KI_UEBERLASTET_NACHRICHT}*"

    if scrub_buf:
        yield {"type": "text", "delta": _scrub_jargon(scrub_buf)}

    abgeschnitten = _ist_abgeschnitten(finish_reason)
    if abgeschnitten:
        # Kein stilles Ende mitten im Satz: der Nutzer sieht, dass hier etwas
        # fehlt, und bekommt einen konkreten nächsten Schritt. Bewusst KEIN
        # automatischer Fortsetzungs-Request — das wäre ein zweiter, für den
        # Nutzer unsichtbarer Modellaufruf pro Antwort.
        log.info("Chat: Antwort am Output-Limit abgeschnitten (finish_reason=%s)", finish_reason)
        yield {"type": "text", "delta": _ABGESCHNITTEN_HINWEIS}

    print(f"[TIMING] GESAMT: {_ms(t0)} ({token_count} chunks, quelle={quelle}, "
          f"finish_reason={finish_reason})", flush=True)

    yield {
        "type": "meta",
        "quelle":            quelle,
        "fahrzeug_referenz": baureihe_ids,
        "vertrauen":         vertrauen,
        "belege":            belege,
        "abgeschnitten":     abgeschnitten,
    }


# ---------- Kontextgebundene Rückfragen zu einer Check-Analyse ----------

# Obergrenze für den mitgeschickten Analysetext im Prompt (Sicherheitsnetz gegen
# Kosten/Latenz; das Request-Modell begrenzt zusätzlich auf _MAX_TEXT_LEN).
_ANALYSE_KONTEXT_MAX = 8_000

_ANALYSE_SYSTEM = """Du bist ENFALs Analyse-Assistent. Der Nutzer hat gerade das Ergebnis eines {check_label} zu einem konkreten Fahrzeug erhalten (siehe unten). Deine EINZIGE Aufgabe: Fragen zu GENAU DIESER Analyse und den darin behandelten Themen beantworten.

— WAS DU BEANTWORTEST (großzügig auslegen) —
- Fragen zur Analyse selbst ("Warum wurde Punkt X als Risiko eingestuft?", "Was bedeutet die Empfehlung?").
- Vertiefende Fragen zu Konzepten, die in der Analyse vorkommen oder eng dazugehören ("Wie hoch sind typische Kosten für einen Zahnriemenwechsel?", "Was ist eine Schadstoffklasse?").
- Klärung von Fachbegriffen aus der Analyse.
- Folgefragen, die auf den Ergebnissen aufbauen.
- Vergleichs- und Alternativfragen zu ähnlichen Fahrzeugen ("Sollte ich lieber einen RS6 kaufen?", "Ist ein E63 besser?", "Wie schlägt sich der M5 gegen den RS6?"): NIEMALS ablehnen. Antworte kurz und mit klarem Bezug zur aktuellen Analyse (die 1–2 entscheidenden Punkte), und verweise für den ausführlichen Vergleich auf den normalen KI-Chat, z. B.: "Für einen ausführlichen Vergleich nutze gerne den KI-Chat."

— GRENZFÄLLE (nur am Rande mit der Analyse verwandt) —
Beantworte sie kurz und hänge GENAU diesen Hinweis an: "Für tiefergehende Fragen zum Thema nutze gerne den KI-Chat."

— WAS DU HÖFLICH ABLEHNST —
Nur klar themenfremde Fragen ohne jeden Bezug zu diesem Fahrzeug, zu Autos allgemein oder zur Analyse (z. B. Kochen, Politik, Allgemeinwissen). Antworte dann mit GENAU diesem Satz und sonst NICHTS:
"Diese Frage bezieht sich nicht auf die Analyse. Nutze den KI-Chat für allgemeine Fragen."

PRINZIP: Im Zweifel hilfreich antworten, NICHT hyperstreng. Frag dich: "Hat das plausibel mit diesem Auto oder dieser Analyse zu tun?" Wenn ja → beantworten.

— NICHT WIEDERHOLEN (für einen natürlichen Gesprächsfluss) —
- Einschränkungen, die du oder die Analyse bereits erklärt hast (z. B. ein Modell-Mismatch, fehlende Marktdaten, allgemeine Unsicherheiten), NICHT in jeder Antwort erneut aufwärmen.
- Erwähne eine solche Einschränkung nur dann wieder, wenn sie für GENAU DIESE Frage wirklich entscheidend ist — sonst weglassen.
- Fasse NICHT die ganze Analyse oder frühere Antworten erneut zusammen. Setze voraus, dass der Nutzer die Analyse und den bisherigen Verlauf kennt, und geh direkt auf das Neue ein.

— STIL & LÄNGE —
- Duze den Nutzer. Sachlich und ruhig, kein Hype. KEINE Einleitungsfloskeln ("Gerne", "Natürlich", "Klar") und KEIN Floskel-Schluss. Steig direkt mit der Antwort ein. Antworte auf Deutsch.
- Geh DIREKT auf die konkrete Frage ein — nur das, was die Frage wirklich braucht.
- Standardlänge: kurz, normalerweise 2–5 Absätze (oft reicht weniger). Länger antworten NUR, wenn der Nutzer ausdrücklich mehr Detailtiefe verlangt.
- Strukturiere mit Markdown, wo es die Antwort klarer macht: **Fettung** für zentrale Begriffe und Kernaussagen, Aufzählungen (mit "- ") für mehrere Punkte, Faktoren oder Schritte, und kurze fette Zwischenüberschriften bei längeren, mehrteiligen Antworten. Eine kurze, einfache Antwort bleibt sauberer Fließtext — erzwinge dort keine Überschriften oder Listen.
- Erfinde KEINE konkreten modellspezifischen Zahlen, die nicht in der Analyse stehen; allgemeine Orientierungswerte/Faustregeln darfst du nennen und als solche kennzeichnen.

=== DIE ANALYSE ({check_label}) ===
{analyse_kontext}"""

_ANALYSE_CHECK_LABELS = {
    "kauf":       "Kauf-Checks",
    "verkauf":    "Verkaufs-Checks",
    "ersatzteil": "einer Ersatzteil-Suche",
}


async def analyse_frage_stream(
    analyse_kontext: str,
    frage: str,
    verlauf: list[dict],
    check_typ: str = "kauf",
) -> AsyncGenerator[dict, None]:
    """Beantwortet eine kontextgebundene Rückfrage zu einer Check-Analyse.

    Events: {"type": "text", "delta": "..."} — reiner Text-Stream (kein Meta/keine
    Websuche; der Kontext ist der bereits erstellte Analysetext).
    """
    import asyncio

    check_label = _ANALYSE_CHECK_LABELS.get(check_typ, "Checks")
    system = _ANALYSE_SYSTEM.format(
        check_label=check_label,
        analyse_kontext=(analyse_kontext or "")[:_ANALYSE_KONTEXT_MAX],
    )

    history = []
    rest = max(0, GEMINI_MAX_INPUT_CHARS - len(system) - len(frage))
    ausgewaehlt = []
    for msg in reversed(verlauf):
        text = msg.get("text", "")
        if rest <= 0:
            break
        text = text[-rest:]
        ausgewaehlt.append((msg, text))
        rest -= len(text)
    for msg, text in reversed(ausgewaehlt):
        role = "user" if msg.get("rolle") == "user" else "model"
        history.append({"role": role, "parts": [{"text": text}]})

    client = _get_client()
    contents = history + [{"role": "user", "parts": [{"text": frage}]}]
    cfg = genai_types.GenerateContentConfig(
        system_instruction=system, temperature=0.3,
        max_output_tokens=GEMINI_ANALYSE_MAX_OUTPUT_TOKENS,
    )

    try:
        response = await with_retry(lambda: client.aio.models.generate_content_stream(
            model=LLM_MODEL, contents=contents, config=cfg,
        ))
    except GeminiFehlgeschlagen as exc:
        log.warning("Analyse-Frage: Gemini-Totalausfall beim Start des Streams: %s", exc)
        yield {"type": "text", "delta": KI_UEBERLASTET_NACHRICHT}
        return
    except Exception:
        log.exception("Analyse-Frage: unerwarteter Fehler beim Start des Gemini-Streams")
        yield {"type": "text", "delta": KI_UEBERLASTET_NACHRICHT}
        return

    _FLUSH_TAIL = 24
    scrub_buf = ""
    try:
        async with asyncio.timeout(GEMINI_STREAM_TIMEOUT_SECONDS):
            async for chunk in response:
                if chunk.text:
                    scrub_buf += chunk.text
                    if len(scrub_buf) > _FLUSH_TAIL * 2:
                        safe, scrub_buf = scrub_buf[:-_FLUSH_TAIL], scrub_buf[-_FLUSH_TAIL:]
                        yield {"type": "text", "delta": _scrub_jargon(safe)}
                        await asyncio.sleep(0)
    except Exception as exc:
        log.warning("Analyse-Frage: Fehler während des Streamens: %s", exc)
        scrub_buf += f"\n\n*{KI_UEBERLASTET_NACHRICHT}*"

    if scrub_buf:
        yield {"type": "text", "delta": _scrub_jargon(scrub_buf)}
