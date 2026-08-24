from __future__ import annotations

"""
Deterministisches Postprocessing für Gemini-Antworten (Final Polish).

Läuft NACH der Gemini-Ausgabe und VOR der Auslieferung an den Nutzer — für
Chat, Diagnose (= Chat-Untermodus, kein eigener Endpunkt), Kaufcheck und
Verkaufscheck gleichermaßen (siehe postprocess_answer()).

Harte Grenze (bewusst so gehalten): NUR eindeutig sichere, rein kosmetische
Transformationen. Kein Fakt wird ergänzt, kein Satz wird umformuliert, keine
inhaltliche Aussage wird verändert. Jede Regel hier arbeitet ausschließlich
auf Whitespace, Aufzählungszeichen und exakten Duplikat-/Floskel-Mustern —
nichts davon kann eine Zahl, einen Fachbegriff oder eine Empfehlung verändern.

Absichtlich NICHT implementiert (zu riskant für "niemals Inhalt verändern"):
  - Umformulierung "unnatürlicher Satzanfänge" — das ist Stilkorrektur, keine
    Kosmetik, und kann Bedeutung verschieben. Bleibt Aufgabe des Prompts.
  - Erkennung inhaltlicher Wiederholungen (zwei unterschiedlich formulierte
    Sätze mit gleicher Aussage) — nicht deterministisch von echten,
    beabsichtigten Wiederholungen (z. B. Tabellen mit ähnlichen Zeilen) zu
    unterscheiden. Nur EXAKT identische, unmittelbar aufeinanderfolgende
    Zeilen werden entfernt (siehe _entferne_duplikatzeilen).
"""

import logging
import re

log = logging.getLogger(__name__)

# ---------- Code-Fences schützen ----------
# Dreifach-Backtick-Blöcke (falls die KI doch mal Code/Rohtext einbettet) werden
# von JEDER Transformation ausgenommen — Whitespace innerhalb von Code kann
# bedeutungstragend sein (Einrückung, Tabellen-Ausrichtung in Rohtext).
_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)


# ---------- 1) Einleitungsfloskeln ----------
# Nur Muster, die REIN als Gesprächseinstieg dienen und beim Entfernen keine
# Information verlieren — ausschließlich am absoluten Textanfang geprüft.
_EINLEITUNGSFLOSKELN: list[re.Pattern] = [
    re.compile(r"^sehr gerne[,!.:]?\s+", re.IGNORECASE),
    re.compile(r"^gerne[,!.:]?\s+", re.IGNORECASE),
    re.compile(r"^klar[,!.:]?\s+", re.IGNORECASE),
    re.compile(r"^natürlich[,!.:]?\s+", re.IGNORECASE),
    re.compile(r"^kein problem[,!.:]?\s+", re.IGNORECASE),
    re.compile(r"^ich helfe dir( sehr)?( gerne)?( weiter)?[,!.:]?\s+", re.IGNORECASE),
    re.compile(r"^gerne helfe ich dir( weiter)?[,!.:]?\s+", re.IGNORECASE),
    re.compile(
        r"^hier ist (deine|die|eine|der|das)\s+(analyse|antwort|einschätzung|übersicht|bericht)[:.]?\s*",
        re.IGNORECASE,
    ),
]

# ---------- 2) Schlussfloskeln ----------
# Nur am absoluten Textende geprüft, damit inhaltliche Sätze mit ähnlichen
# Wörtern (z. B. eine echte Rückfrage "Hast du noch weitere Angaben?") nicht
# getroffen werden.
_SCHLUSSFLOSKELN: list[re.Pattern] = [
    re.compile(r"ich hoffe,?\s*(das|dies|die antwort)?\s*(konnte\s+)?(dir\s+)?hilft?(\s+dir)?[!.]?\s*$", re.IGNORECASE),
    re.compile(
        r"bei (weiteren\s+)?fragen stehe ich (dir\s+)?(gerne|jederzeit)(\s+weiterhin)?(\s+gerne)?\s+zur verfügung[!.]?\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"lass es mich wissen,?\s*falls[^\n]*$", re.IGNORECASE),
    re.compile(r"falls du (noch\s+)?fragen hast,?\s*(melde dich|frag(\s+mich)?\s+gerne)[^\n]*$", re.IGNORECASE),
    re.compile(r"wenn du (noch\s+)?fragen hast,?\s*(lass es mich wissen|melde dich)[^\n]*$", re.IGNORECASE),
]

_MAX_FLOSKEL_ITERATIONEN = 3  # Schutz gegen pathologische Endlosschleifen bei Mehrfachtreffern

# Aufzählungszeichen vereinheitlichen: "*" und "•" -> "-" (rein visuell, ändert
# an der Aufzählung selbst nichts). Nur am Zeilenanfang (ggf. mit Einrückung).
_BULLET_MUSTER = re.compile(r"^(\s*)[\*•]\s+")

# Tabellenzeilen (Markdown-Pipes) werden von Space-Kollaps und Duplikat-Check
# ausgenommen — dort kann Spaltenausrichtung beabsichtigt sein.
_IST_TABELLENZEILE = re.compile(r"^\s*\|")


def _entferne_floskeln(text: str) -> str:
    for _ in range(_MAX_FLOSKEL_ITERATIONEN):
        neu = text
        for muster in _EINLEITUNGSFLOSKELN:
            neu = muster.sub("", neu, count=1)
        if neu == text:
            break
        text = neu
    for _ in range(_MAX_FLOSKEL_ITERATIONEN):
        neu = text
        for muster in _SCHLUSSFLOSKELN:
            neu = muster.sub("", neu, count=1).rstrip()
        if neu == text:
            break
        text = neu
    return text


def _bereinige_zeilen(segment: str) -> str:
    """Zeilenweise Kosmetik: Trailing-Whitespace, Bullet-Vereinheitlichung,
    doppelte Leerzeichen, exakte Duplikat-Zeilen — alles ohne Tabellenzeilen."""
    zeilen = segment.split("\n")
    ergebnis: list[str] = []
    vorherige_inhaltliche_zeile: str | None = None

    for zeile in zeilen:
        zeile = zeile.rstrip()

        if _IST_TABELLENZEILE.match(zeile):
            ergebnis.append(zeile)
            vorherige_inhaltliche_zeile = None
            continue

        zeile = _BULLET_MUSTER.sub(r"\1- ", zeile)

        # Doppelte Leerzeichen im Text kollabieren, Einrückung am Zeilenanfang erhalten.
        fuehrend_match = re.match(r"^(\s*)(.*)$", zeile, re.DOTALL)
        einrueckung, rest = fuehrend_match.group(1), fuehrend_match.group(2)
        rest = re.sub(r" {2,}", " ", rest)
        zeile = einrueckung + rest

        # Exakte, unmittelbar aufeinanderfolgende Duplikat-Zeilen entfernen
        # (z. B. wenn die KI einen Satz versehentlich zweimal ausgibt).
        if zeile.strip() and zeile == vorherige_inhaltliche_zeile:
            continue

        ergebnis.append(zeile)
        vorherige_inhaltliche_zeile = zeile if zeile.strip() else None

    return "\n".join(ergebnis)


# ---------- 3) Verkaufsdauer-Guard (Reliability-Sprint 3, §26) ----------
#
# Die STRUKTURIERTEN Felder (verkaufsdauer_schnell/empfohlen/maximal) sind bereits
# hart auf die drei Kategorien erzwungen (app/preisurteil.py, app/verkaufscheck.py —
# nie eine erfundene Zahl). Die Lücke war ausschließlich der freie Bericht-Text: der
# Prompt instruiert das LLM, keine konkreten Tage/Wochen/Monate zu erfinden, aber
# nichts prüfte das NACH der Generierung — ein "innerhalb von 3–4 Wochen" konnte
# unverändert durchrutschen.
#
# Sicherheitsnetz: nur Sätze, die GLEICHZEITIG (a) eine Zeitspanne (Tage/Wochen/
# Monate) UND (b) ein eindeutiges Verkaufs-/Vermarktungs-Kontextwort enthalten,
# werden angefasst — TÜV-Fristen ("TÜV in 6 Monaten"), Wartungsintervalle ("Wartung
# vor 3 Monaten") oder Garantiezeiträume bleiben unverändert, da ihnen das
# Verkaufs-Kontextwort fehlt.
_VERKAUF_KONTEXT_WORTE = (
    "verkauf", "vermarkt", "inserat", "interessent", "käufer", "kaeufer",
    "angebot online", "standzeit", "absetz", "veräußer", "veraeusser",
    "anzeige online", "annonc", "nachfrage", "resonanz", "anfragen",
    "preisanpassung",
)

# Zusätzlich: Preis-Verb (senken/reduzieren/anpassen) an anderer Stelle im Satz —
# separat geprüft (nicht als Teilstring-Phrase), da es im Fließtext selten direkt
# neben "Preis" steht ("den Preis schrittweise um 250 € senken").
_PREIS_VERB_WORTE = ("senken", "reduzieren", "runter", "nachlass")

# Zeitspanne mit optionaler einleitender Präposition — wird bei Treffer durch eine
# neutrale, nicht-quantifizierte Formulierung ersetzt (KEINE Zahl mehr).
_RE_VERKAUFSDAUER = re.compile(
    r"(?:innerhalb\s+(?:von\s+)?|binnen\s+|nach\s+(?:etwa\s+|ca\.?\s+|circa\s+)?|"
    r"in\s+(?:etwa\s+|ca\.?\s+|circa\s+)?)?"
    r"\d+\s*(?:[-–—]\s*\d+\s*)?"
    r"(?:tage?n?|wochen?|monate?n?)\b",
    re.IGNORECASE,
)

_VERKAUFSDAUER_ERSATZ = "über einen angemessenen Zeitraum"

# Sätze grob trennen (Punkt/Ausrufe-/Fragezeichen + Whitespace) — bewusst simpel,
# reicht für die Kontext-Prüfung (kein vollständiger Satzparser nötig). Häufige
# Abkürzungen vor dem Punkt werden ausgenommen, damit z.B. "ca." keine Satzgrenze
# erzeugt und "Nach ca. 2 Wochen" als EIN Satz erhalten bleibt.
_RE_SATZGRENZE = re.compile(
    r"(?<!\bca)(?<!\bbzw)(?<!\bz\.B)(?<!\betc)(?<!\bd\.h)(?<=[.!?])\s+", re.IGNORECASE)


def entferne_erfundene_verkaufsdauer(text: str) -> str:
    """Ersetzt erfundene, konkrete Verkaufsdauer-Zeitangaben im Freitext (§26) durch
    eine neutrale Formulierung — NUR in Sätzen mit eindeutigem Verkaufs-/
    Vermarktungs-Kontext, um TÜV-/Wartungs-/Garantiefristen etc. nicht zu berühren.
    Gibt den Text unverändert zurück, wenn kein solcher Satz gefunden wird."""
    if not text:
        return text

    teile = _CODE_FENCE.split(text)
    fences = _CODE_FENCE.findall(text)

    def _bereinige_teil(teil: str) -> str:
        zeilen = teil.split("\n")
        neue_zeilen = []
        for zeile in zeilen:
            if _IST_TABELLENZEILE.match(zeile):
                neue_zeilen.append(zeile)
                continue
            saetze = _RE_SATZGRENZE.split(zeile)
            neue_saetze = []
            for satz in saetze:
                low = satz.lower()
                hat_kontext = any(w in low for w in _VERKAUF_KONTEXT_WORTE) or (
                    "preis" in low and any(w in low for w in _PREIS_VERB_WORTE)
                )
                if hat_kontext and _RE_VERKAUFSDAUER.search(satz):
                    satz = _RE_VERKAUFSDAUER.sub(_VERKAUFSDAUER_ERSATZ, satz)
                    log.info("Verkaufsdauer-Guard: erfundene Zeitangabe im Bericht ersetzt.")
                neue_saetze.append(satz)
            neue_zeilen.append(" ".join(neue_saetze))
        return "\n".join(neue_zeilen)

    bereinigt = [_bereinige_teil(t) for t in teile]
    zusammengesetzt: list[str] = []
    for i, teil in enumerate(bereinigt):
        zusammengesetzt.append(teil)
        if i < len(fences):
            zusammengesetzt.append(fences[i])
    return "".join(zusammengesetzt)


# ---------- Wartungs-Fälligkeits-Guard (P2-5, Bake-off-Nachbesserung) ----------
# P2-5 (app/laufleistung.py): kein Feld im System kennt den Zeitpunkt des letzten
# Service, weder im Inserat noch in der Fahrzeugdatenbank. `laufleistung.prompt_block`
# verbietet dem Modell deshalb im Prompt bereits, einen Wartungspunkt als "fällig",
# "überfällig", "versäumt" oder "nicht durchgeführt" darzustellen — ein Wartungspunkt
# bedeutet ausschließlich "Nachweis verlangen". Der Bake-off (Gemini 3.7) hat gezeigt,
# dass ein Modell dieses Verbot trotzdem gelegentlich verletzt ("der fällige
# Zahnriemenwechsel"). Dieser Guard ist das Sicherheitsnetz NACH dem Call — exakt
# derselbe satzweise Aufbau wie `entferne_erfundene_verkaufsdauer` oben.
#
# Bewusst NUR innerhalb erkennbarem Wartungskontext (§ Aufgabenstellung: "NICHT
# global jedes Wort 'fällig' überall blind ersetzen"): eine völlig unabhängige
# Verwendung — z.B. eine fällige Zahlung — bleibt unangetastet.
_WARTUNG_KONTEXT_WORTE = (
    "wartung", "service", "intervall", "inspektion", "scheckheft", "nachweis",
    "zahnriemen", "steuerkette", "kettentrieb", "ölwechsel", "oelwechsel",
    "motoröl", "motoroel", "getriebeöl", "getriebeoel", "kühlmittel", "kuehlmittel",
    "bremsflüssigkeit", "bremsfluessigkeit", "zündkerzen", "zuendkerzen",
    "glühkerzen", "gluehkerzen", "kupplung", "luftfilter", "kraftstofffilter",
    "partikelfilter", "dpf", "vanos", "nockenwellenversteller", "keilriemen",
    "keilrippenriemen", "ventilspiel", "adblue", "haldex",
)

# Endungs-erhaltende Ersetzung: die Übereinstimmung der Endung macht die Ersetzung
# grammatisch korrekt in BEIDEN Stellungen — prädikativ ("... ist überfällig" ->
# "... ist zu prüfen") und attributiv ("überfälliger Zahnriemenwechsel" ->
# "zu prüfender Zahnriemenwechsel", exakt das im Auftrag genannte Beispiel).
_RE_FAELLIG = re.compile(r"\b(?:über)?f[äa]llig(e|er|es|en)?\b", re.IGNORECASE)
_FAELLIG_ERSATZ = {
    None: "zu prüfen", "": "zu prüfen", "e": "zu prüfende", "er": "zu prüfender",
    "es": "zu prüfendes", "en": "zu prüfenden",
}
_RE_VERSAEUMT = re.compile(r"\bvers[äa]umt(e|er|es|en)?\b", re.IGNORECASE)
_VERSAEUMT_ERSATZ = {
    None: "nicht nachgewiesen", "": "nicht nachgewiesen", "e": "nicht nachgewiesene",
    "er": "nicht nachgewiesener", "es": "nicht nachgewiesenes", "en": "nicht nachgewiesenen",
}
_RE_NICHT_DURCHGEFUEHRT = re.compile(
    r"\bnicht\s+durchgef[üu]hrt(\s+worden)?\b", re.IGNORECASE)


def _ersetze_faellig(match: re.Match) -> str:
    return _FAELLIG_ERSATZ.get(match.group(1), "zu prüfen")


def _ersetze_versaeumt(match: re.Match) -> str:
    return _VERSAEUMT_ERSATZ.get(match.group(1), "nicht nachgewiesen")


def neutralisiere_wartungs_faelligkeit(text: str) -> str:
    """Neutralisiert Fälligkeits-Behauptungen zu Wartungspunkten im Freitext —
    NUR in Sätzen mit erkennbarem Wartungskontext (siehe `_WARTUNG_KONTEXT_WORTE`).
    Sätze ohne diesen Kontext (z.B. eine fällige Zahlung) bleiben unverändert."""
    if not text:
        return text

    teile = _CODE_FENCE.split(text)
    fences = _CODE_FENCE.findall(text)

    def _hat_wartungskontext(satz: str) -> bool:
        low = satz.lower()
        return any(w in low for w in _WARTUNG_KONTEXT_WORTE)

    def _bereinige_teil(teil: str) -> str:
        zeilen = teil.split("\n")
        neue_zeilen = []
        for zeile in zeilen:
            if _IST_TABELLENZEILE.match(zeile):
                neue_zeilen.append(zeile)
                continue
            saetze = _RE_SATZGRENZE.split(zeile)
            neue_saetze = []
            for satz in saetze:
                if _hat_wartungskontext(satz):
                    neu = _RE_FAELLIG.sub(_ersetze_faellig, satz)
                    neu = _RE_VERSAEUMT.sub(_ersetze_versaeumt, neu)
                    neu = _RE_NICHT_DURCHGEFUEHRT.sub("nicht nachweisbar", neu)
                    if neu != satz:
                        log.info("Wartungs-Fälligkeits-Guard: Wortlaut im Bericht neutralisiert.")
                    satz = neu
                neue_saetze.append(satz)
            neue_zeilen.append(" ".join(neue_saetze))
        return "\n".join(neue_zeilen)

    bereinigt = [_bereinige_teil(t) for t in teile]
    zusammengesetzt: list[str] = []
    for i, teil in enumerate(bereinigt):
        zusammengesetzt.append(teil)
        if i < len(fences):
            zusammengesetzt.append(fences[i])
    return "".join(zusammengesetzt)


# ---------- No-Market-Preisurteil-Guard (P1-b, KaufCheck-Backend-Freeze) ----------
# app/preisurteil.no_market_prompt_block verbietet dem Modell im Prompt bereits
# JEDES Preisurteil (auch indirekt: "wirkt attraktiv", "erscheint hoch") und JEDE
# konkrete Marktspanne, wenn `bewerte_preis` keinen belastbaren Median liefert.
# Dieser Guard ist das Sicherheitsnetz NACH dem Call, exakt derselbe satzweise
# Aufbau wie `neutralisiere_wartungs_faelligkeit` oben. NUR aufgerufen, wenn der
# Aufrufer (app/kaufcheck.py) bereits weiss, dass kein belastbarer Markt vorliegt
# (`markt_verfuegbar is False`) — bei vorhandener Markt-Evidence (PFAD A) wird
# diese Funktion gar nicht erst gerufen, das echte Preisurteil bleibt unberührt.
#
# Zwei Auslöser, bewusst unterschiedlich streng:
#   1. Qualitative Marktpreis-Urteilswörter (günstig/teuer/marktgerecht/...) —
#      IMMER ein Marktpreisurteil, unabhängig vom übrigen Satzkontext. Diese
#      Wörter sind praktisch nie neutral gemeint und decken sich wörtlich mit
#      dem im Prompt verbotenen Vokabular.
#   2. Eine konkrete Euro-Zahl/-Spanne NUR dann als Marktpreisbehauptung werten,
#      wenn der Satz ZUSÄTZLICH einen Marktkontext-Begriff enthält — sonst wäre
#      jede grundierte Reparaturkosten- oder Inseratspreis-Nennung betroffen
#      (§3 der Aufgabenstellung: "nicht global jedes Euro-Zeichen entfernen").
_MARKTPREIS_KONTEXT_WORTE = (
    "marktpreis", "marktwert", "marktvergleich", "marktspanne", "vergleichspreis",
    "median", "preiseinschätzung", "preiseinschaetzung", "preisbewertung",
    "marktniveau", "markttrend", "marktlage",
)
_RE_MARKTURTEIL_WORT = re.compile(
    r"\b(?:extrem\s+)?g[üu]nstig(?:e|er|es|en)?\b|\b(?:extrem\s+)?teuer(?:e|er|es|en)?\b"
    r"|\bmarktgerecht(?:e|er|es|en)?\b|\bangemessen(?:e|er|es|en)?\b"
    r"|\b[üu]berteuert(?:e|er|es|en)?\b|\bfair(?:e|er|es|en)?\b|\bschn[äa]ppchen\b",
    re.IGNORECASE)
_RE_MARKT_RICHTUNG = re.compile(
    r"\b[üu]ber\s+(?:dem\s+)?markt(?:preis|wert)?\b|\bunter\s+(?:dem\s+)?markt(?:preis|wert)?\b",
    re.IGNORECASE)
_RE_EURO_ZAHL = re.compile(
    r"\d[\d.,]*\s*(?:[-–—]\s*\d[\d.,]*\s*)?(?:€|EUR)\b", re.IGNORECASE)

_NO_MARKET_NEUTRALSATZ = (
    "Eine belastbare Marktpreisbewertung ist für diesen Check nicht verfügbar."
)


def _hat_marktkontext(text: str) -> bool:
    low = text.lower()
    return any(w in low for w in _MARKTPREIS_KONTEXT_WORTE)


def _ist_marktpreisurteil(satz: str, *, zeile_hat_marktkontext: bool = False) -> bool:
    """`zeile_hat_marktkontext`: Marktkontext-Treffer ueber die GESAMTE Zeile, nicht
    nur diesen einzelnen Satz. `_RE_SATZGRENZE` trennt bei "ca." mitten im Satz
    (z.B. "Der Marktwert betraegt ca." | "14.000 EUR.") — ohne den Zeilenkontext
    wuerde das Marktkontext-Wort und die Euro-Zahl dann in zwei verschiedenen
    "Saetzen" landen und keiner der beiden triggert allein."""
    if _RE_MARKTURTEIL_WORT.search(satz) or _RE_MARKT_RICHTUNG.search(satz):
        return True
    if _RE_EURO_ZAHL.search(satz) and (zeile_hat_marktkontext or _hat_marktkontext(satz)):
        return True
    return False


def neutralisiere_no_market_preisurteil(text: str) -> str:
    """Entfernt konkrete Marktpreisbehauptungen und -urteile aus dem Freitext —
    NUR aufzurufen, wenn für diesen Check KEINE belastbare Markt-Evidence vorliegt
    (research_status == "completed_no_market"). Andere Kostenangaben (z.B.
    Reparaturkosten aus der Fahrzeugdatenbank) bleiben unberührt, ebenso
    Markdown-Tabellenzeilen (der Inserats-Preis in der Vergleichstabelle ist
    KEIN Marktpreisurteil)."""
    if not text:
        return text

    teile = _CODE_FENCE.split(text)
    fences = _CODE_FENCE.findall(text)

    def _bereinige_teil(teil: str) -> str:
        zeilen = teil.split("\n")
        neue_zeilen = []
        for zeile in zeilen:
            if _IST_TABELLENZEILE.match(zeile):
                neue_zeilen.append(zeile)
                continue
            saetze = _RE_SATZGRENZE.split(zeile) if zeile.strip() else [zeile]
            zeile_hat_marktkontext = _hat_marktkontext(zeile)
            neue_saetze = []
            ersetzt_in_zeile = False
            for satz in saetze:
                if satz.strip() and _ist_marktpreisurteil(
                        satz, zeile_hat_marktkontext=zeile_hat_marktkontext):
                    if not ersetzt_in_zeile:
                        neue_saetze.append(_NO_MARKET_NEUTRALSATZ)
                        ersetzt_in_zeile = True
                    log.info("No-Market-Preisurteil-Guard: Satz im Bericht neutralisiert.")
                else:
                    neue_saetze.append(satz)
            neue_zeilen.append(" ".join(neue_saetze))
        return "\n".join(neue_zeilen)

    bereinigt = [_bereinige_teil(t) for t in teile]
    zusammengesetzt: list[str] = []
    for i, teil in enumerate(bereinigt):
        zusammengesetzt.append(teil)
        if i < len(fences):
            zusammengesetzt.append(fences[i])
    return "".join(zusammengesetzt)


def postprocess_answer(text: str) -> str:
    """
    Zentrale Postprocessing-Funktion für ALLE KI-Antworten (Chat, Diagnose,
    Kaufcheck, Verkaufscheck). Rein kosmetisch und deterministisch:

      1. Zeilenumbrüche normalisieren (\\r\\n / \\r -> \\n)
      2. Trailing Whitespace pro Zeile entfernen
      3. Aufzählungszeichen vereinheitlichen (*, • -> -)
      4. Doppelte Leerzeichen im Fließtext kollabieren (Tabellen ausgenommen)
      5. Exakte, unmittelbar aufeinanderfolgende Duplikat-Zeilen entfernen
      6. Mehr als eine Leerzeile auf genau eine Leerzeile kollabieren
      7. Bekannte Einleitungs-/Schlussfloskeln entfernen (nur am Textanfang/-ende)
      8. Führende/nachfolgende Leerzeichen am Gesamttext entfernen

    Code-Fences (```...```) werden von 2–6 ausgenommen, um technischen Inhalt
    nicht zu verändern. Verändert niemals Zahlen, Fachbegriffe oder die
    inhaltliche Aussage eines Satzes.
    """
    if not text:
        return text

    text = text.replace("\r\n", "\n").replace("\r", "\n")

    teile = _CODE_FENCE.split(text)
    fences = _CODE_FENCE.findall(text)
    bereinigt = [_bereinige_zeilen(teil) for teil in teile]

    zusammengesetzt: list[str] = []
    for i, teil in enumerate(bereinigt):
        zusammengesetzt.append(teil)
        if i < len(fences):
            zusammengesetzt.append(fences[i])
    text = "".join(zusammengesetzt)

    # Mehr als eine Leerzeile -> genau eine Leerzeile (max. zwei aufeinanderfolgende \n)
    text = re.sub(r"\n{3,}", "\n\n", text)

    text = text.strip()
    text = _entferne_floskeln(text)
    text = text.strip()

    return text
