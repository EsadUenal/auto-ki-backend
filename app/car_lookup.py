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


# ---------- Fahrzeug-Erkennung ----------

def find_baureihe(marke: str | None, modell: str | None, baujahr: int | None) -> dict | None:
    """Findet die Baureihe mit dem höchsten Treffer-Score."""
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
    if modell:
        ml_in   = modell.strip().lower()
        ml_norm = _norm_bezeichnung(ml_in)     # 'C 200' -> 'c200'
        ml_kenn = _parse_kennung(ml_in)         # ('c','200',…) oder None (z.B. '320d')
        for m in get_alle_motorvarianten_kurz():
            bez  = (m.get("bezeichnung") or "").strip().lower()
            code = (m.get("motorcode") or "").strip().lower()
            if _modell_trifft_motor(ml_in, ml_norm, ml_kenn, bez, code):
                motor_baureihe_ids.add(m["baureihe_id"])

    scored: list[tuple[int, bool, dict]] = []
    for r in rows:
        score = 0
        marke_ok = marke is None
        modell_getroffen = False
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
        # Motorvarianten-Treffer nur werten, wenn die Marke passt (kein Cross-Brand-Match,
        # z.B. ein VW-"2.0 TDI" darf keine BMW-Baureihe matchen).
        if marke_ok and r["id"] in motor_baureihe_ids:
            if not modell_getroffen:
                score += 4
            modell_getroffen = True
        if baujahr and score > 0:
            bvon = r["bauzeitraum_von"] or 0
            bbis = r["bauzeitraum_bis"] or 9999
            if bvon <= baujahr <= bbis:
                score += 5
            elif abs(bvon - baujahr) <= 2 or (bbis < 9999 and abs(bbis - baujahr) <= 2):
                score += 1
        if score > 0:
            scored.append((score, modell_getroffen, dict(r)))

    # Ist ein Modell angegeben, NUR Baureihen mit echtem Modell- ODER Motor-Treffer zulassen.
    # Sonst gewinnt eine andere Baureihe derselben Marke allein über Marke+Baujahr.
    # Kein Treffer -> None -> saubere Web-Analyse statt eines falschen Profils.
    if modell:
        scored = [s for s in scored if s[1]]

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][2]
    log.info("Baureihe erkannt: %s (score=%d)", best["id"], scored[0][0])
    return get_baureihe(best["marke"], best["modell"], best["generation"])


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
    """
    if not hint or not baureihe["motoren"]:
        return None
    h = hint.lower()

    # (1) Direkter Bezeichnungs-/Motorcode-Treffer — stärkstes Signal.
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

def build_db_context(baureihe: dict | None, motor_match: dict | None) -> str:
    """Baut den strukturierten DB-Kontext-String (Specs, Schwachstellen, Rückrufe)."""
    if baureihe is None:
        return "Kein geprüftes DB-Profil für dieses Fahrzeug vorhanden."

    lines = [
        f"## DB-Profil: {baureihe.get('marke','')} {baureihe.get('modell','')} {baureihe.get('generation','')}",
        f"Bauzeitraum: {baureihe.get('bauzeitraum_von','?')}–{baureihe.get('bauzeitraum_bis') or 'heute'}",
        f"Karosserie: {', '.join(baureihe.get('karosserie') or [])}",
        f"TÜV-Mängelquote: {baureihe.get('tuev_maengelquote') or 'nicht erfasst'}",
        f"ADAC-Pannenkennziffer: {baureihe.get('adac_pannenkennziffer') or 'nicht erfasst'}",
        "",
    ]

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
        if m.get("schwachstellen_motor"):
            lines.append("Motorprobleme:")
            for s in m["schwachstellen_motor"]:
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
    if baureihe.get("schwachstellen_baureihe"):
        lines.append("### Schwachstellen Baureihe:")
        for s in baureihe["schwachstellen_baureihe"]:
            schweregrad = s.get("schweregrad")
            prefix = f"[{schweregrad}] " if schweregrad else ""
            lines.append(
                f"  {prefix}{s.get('bauteil','?')}: {s.get('beschreibung','?')} "
                f"(Baujahre: {s.get('betroffene_baujahre','?')})"
            )
        lines.append("")

    if baureihe.get("rueckrufe"):
        lines.append("### KBA-Rückrufe:")
        for r in baureihe["rueckrufe"]:
            lines.append(
                f"  {r.get('datum','?')}: {r.get('mangel','?')} — {r.get('abhilfe','?')} (Ref: {r.get('kba_referenz','?')})"
            )

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
