from __future__ import annotations

"""
Motor-Applicability-Gate für Baureihen-Schwachstellen (DATA-SAFETY-RUNTIME-GATE).

DER BEFUND (DATA-TRUTH-AUDIT)
-----------------------------
`schwachstelle_baureihe` hängt ausschließlich an `baureihe_id`. Eine Einschränkung
auf eine bestimmte Motorisierung steht — wenn überhaupt — als FREITEXT im
`bauteil`-Feld. Gemessen: 257 von 1.460 Zeilen (17,6 %) tragen so eine
Einschränkung, 193 davon direkt im `bauteil`; **98 davon haben Schweregrad `hoch`**
und konnten damit bisher den Empfehlungs-Floor auslösen.

Drei reproduzierte Fehlzuordnungen:

  BMW 320i F30 (Benziner)  -> "Steuerkette (N47 Dieselmotoren)"   [Floor ausgelöst]
  BMW 525i E60 (R6)        -> "V10-Motor (M5)"                    [Floor ausgelöst]
  Audi A3 1.6 MPI (Sauger) -> "Turbolader (TFSI-Motoren)"         [Floor ausgelöst]

In allen drei Fällen ist der DB-Satz für sich genommen richtig — er gilt nur einer
ANDEREN Motorisierung. Der Nutzer bekam eine `kritisch`-priorisierte Kaufaktion und
eine verschärfte Kaufempfehlung für ein Bauteil, das sein Fahrzeug nicht besitzt.

DAS PRINZIP (bewusst wie app/recall_filter.py, nicht wie eine NLP-Engine)
------------------------------------------------------------------------
Dies ist ein reines LAUFZEIT-Gate. Es wird keine DB-Zeile geändert, gelöscht oder
umgeschrieben. Entschieden wird ausschließlich, ob ein vorhandener Satz DIESES
Fahrzeug betreffen KANN — mit derselben Dreistufigkeit wie bei Rückrufen:

  "kompatibel"    -> unverändertes Verhalten
  "unklar"        -> Hinweis bleibt erhalten, trägt aber keine harte Wirkung
  "incompatible"  -> vollständig raus: keine Evidence, keine Kaufaktion, kein
                      Floor, nicht in den LLM-Kontext

KEINE ERFUNDENEN WISSENSTABELLEN
--------------------------------
Es gibt hier bewusst KEINE gepflegte Liste "welcher Motorcode ist Diesel". Eine
solche Liste wäre genau die Art unbelegten Zusatzwissens, die der Audit im
Datenbestand kritisiert hat. Stattdessen werden drei Signale ausgewertet, die
alle aus BEREITS VORHANDENEN, strukturierten Daten stammen:

  1. KRAFTSTOFF — explizite Wörter ("Dieselmotoren", "Benziner", "Elektro") im
     Scope, verglichen mit `motorvariante.kraftstoff`. Normalisierung über
     `recall_filter._norm_kraftstoff` (dieselbe Funktion, die die Rückruf-
     Applicability benutzt — keine zweite Kraftstoff-Semantik im Code).

  2. BAUART — Zylinderangaben ("V10", "Sechszylinder") im Scope, verglichen mit
     `motorvariante.zylinder`.

  3. KONTRAST INNERHALB DER BAUREIHE — ein Variantenkürzel im Scope (z. B. "TFSI",
     "N47", "M5") gilt nur dann als ausschließend, wenn es in DERSELBEN Baureihe
     tatsächlich unterscheidet: mindestens eine Schwestervariante trägt es,
     die erkannte Variante nicht. Das Urteil kommt damit aus der Motorliste der
     Baureihe selbst, nicht aus Zusatzwissen dieses Moduls.

REIHENFOLGE (wichtig, sonst entstehen Fehlausschlüsse)
------------------------------------------------------
Ein EXPLIZITER Kraftstoff-Treffer beweist Zugehörigkeit und schlägt den
Kontrast-Test. Beispiel: Insignia B 2.0 Diesel mit der Schwachstelle
"Dieselmotoren (1.6 CDTI, 2.0 CDTI)" — die Bezeichnung des Fahrzeugs lautet
"2.0 Diesel (174 PS)" und enthält das Kürzel "CDTI" NICHT. Ohne diese Reihenfolge
würde der Kontrast-Test den Satz fälschlich ausschließen, obwohl das explizite
Wort "Dieselmotoren" die Zugehörigkeit belegt.

Aus demselben Grund darf ein Kürzel NICHT auf einen Kraftstoff zurückgerechnet
werden ("TFSI ⇒ Benzin"), um daraus KOMPATIBILITÄT abzuleiten: der A3 1.6 MPI ist
ebenfalls ein Benziner und wäre damit fälschlich als betroffen bestätigt worden.
Kürzel wirken ausschließlich über den Kontrast-Test.
"""

import logging
import re

from app.recall_filter import _norm_kraftstoff

log = logging.getLogger(__name__)

KOMPATIBEL = "kompatibel"
UNKLAR = "unklar"
INKOMPATIBEL = "incompatible"

# Antriebe mit Hochvolt-System — dieselbe Gleichsetzung wie in recall_filter
# (ein "Hybrid"-Scope schließt einen PHEV nicht aus).
_HAT_HOCHVOLT = {"phev", "elektro"}

# Explizite Kraftstoff-/Antriebswörter. NUR diese begründen einen Kraftstoff-Scope;
# Variantenkürzel tun es ausdrücklich nicht (siehe Modulkopf).
#
# Die Wörter werden mit VOLLEN Wortgrenzen gesucht, nicht als Teilstring. Der
# Smoke-Test des Gates hat gezeigt, warum das nötig ist: "Elektronik/Infotainment"
# enthält den Teilstring "elektro" und hätte als Elektro-Scope gegolten — eine
# harmlose Infotainment-Schwachstelle wäre an jedem Verbrenner ausgeblendet worden.
# Deshalb stehen hier die tatsächlichen Vollformen; Komposita wie "Diesel-Modelle"
# trennt die Wortgrenze am Bindestrich ohnehin korrekt ab.
_KRAFTSTOFF_WORTE = (
    "dieselmotoren", "dieselmotor", "diesel",
    "benzinmotoren", "benzinmotor", "benziner", "benzin", "ottomotor",
    "plug-in-hybrid", "plugin", "phev",
    "mild-hybrid", "mildhybrid",
    "hybrid", "elektro", "elektromotor", "elektrisch",
)
_KRAFTSTOFF_RE = {
    wort: re.compile(rf"(?<![A-Za-zÄÖÜäöü]){re.escape(wort)}(?![A-Za-zÄÖÜäöü])",
                     re.IGNORECASE)
    for wort in _KRAFTSTOFF_WORTE
}

# Zylinderangaben. "V10" & Co. sowie die ausgeschriebenen deutschen Formen.
_ZYLINDER_WORTE = {
    "dreizylinder": 3, "vierzylinder": 4, "fünfzylinder": 5, "fuenfzylinder": 5,
    "sechszylinder": 6, "achtzylinder": 8, "zehnzylinder": 10, "zwölfzylinder": 12,
    "zwoelfzylinder": 12,
}
_ZYLINDER_CODE = re.compile(r"\b[VWR](\d{1,2})\b")

# Klammerzusätze sind der Ort, an dem die Einschränkung in dieser DB tatsächlich
# steht (gemessen: 193 von 257 Fällen im `bauteil`-Feld, überwiegend in Klammern).
_KLAMMER = re.compile(r"\(([^)]*)\)")

# Kandidaten für ein Variantenkürzel: GROSSBUCHSTABEN/Ziffern-Token wie "TFSI",
# "N47", "M5". Ein normal geschriebenes Wort ("Automatik", "Vorderachse") ist
# bewusst KEIN Kandidat — sonst würde jeder Getriebe- oder Achsenzusatz als
# Motorkürzel missgedeutet.
#
# Der Bindestrich TRENNT (er ist kein Token-Zeichen): "TFSI-Motoren" muss in
# "TFSI" + "Motoren" zerfallen, sonst scheitert die Formprüfung am kleingeschriebenen
# zweiten Teil und der Scope bliebe unerkannt — genau der Fall
# "Turbolader (TFSI-Motoren)" aus dem Audit.
_TOKEN = re.compile(r"[A-Za-zÄÖÜäöü0-9][A-Za-zÄÖÜäöü0-9.]*")
_KUERZEL_FORM = re.compile(r"^[A-Z0-9][A-Z0-9.]*$")
# Rein numerische Token ("2.0", "1.4") sind HUBRAUMANGABEN, keine Motorfamilien.
# Sie dürfen weder Zugehörigkeit belegen noch ausschließen: die Bezeichnung
# "2.0 FSI (150 PS)" trägt "2.0" und hätte den Scope "(1.4 TFSI, 1.8 TFSI,
# 2.0 TFSI)" sonst fälschlich als passend bestätigt — obwohl ein 2.0 FSI kein
# 2.0 TFSI ist. Genau dieser Fall steht im Audit als P0.
_NUR_ZAHL = re.compile(r"^[0-9][0-9.]*$")


def _scope_text(s: dict) -> str:
    """Der Text, aus dem eine Motor-Einschränkung gelesen werden darf.

    Bewusst NUR das `bauteil`-Feld plus alle Klammerzusätze aus `beschreibung`.
    Eine beiläufige Erwähnung im Fließtext ("… auch bei Dieselmotoren bekannt")
    ist KEINE Einschränkung und darf keinen Ausschluss auslösen — sonst würde ein
    allgemeiner Fahrwerksmangel an einem Benziner verschwinden, nur weil im
    Beschreibungstext das Wort "Diesel" vorkommt.
    """
    bauteil = (s.get("bauteil") or "").strip()
    klammern = " ".join(_KLAMMER.findall(s.get("beschreibung") or ""))
    return f"{bauteil} {klammern}".strip()


def _kraftstoffe(text: str) -> set[str]:
    """Alle explizit genannten Kraftstoffe/Antriebe — normalisiert."""
    out: set[str] = set()
    for wort, muster in _KRAFTSTOFF_RE.items():
        if muster.search(text):
            norm = _norm_kraftstoff(wort)
            if norm:
                out.add(norm)
    return out


def _zylinder(text: str) -> set[int]:
    """Alle im Scope genannten Zylinderzahlen."""
    out: set[int] = set()
    t = text.lower()
    for wort, n in _ZYLINDER_WORTE.items():
        if wort in t:
            out.add(n)
    for treffer in _ZYLINDER_CODE.findall(text):
        n = int(treffer)
        if 2 <= n <= 16:
            out.add(n)
    return out


def _kuerzel(text: str) -> list[str]:
    """Variantenkürzel-Kandidaten aus dem Scope.

    Gefiltert wird auf die FORM (Großbuchstaben/Ziffern), nicht auf eine Liste
    bekannter Motorfamilien — dieses Modul führt bewusst kein eigenes Motorwissen.
    """
    out: list[str] = []
    for roh in _TOKEN.findall(text):
        tok = roh.strip(".")
        if len(tok) < 2 or not _KUERZEL_FORM.match(tok):
            continue
        if _NUR_ZAHL.match(tok):          # Hubraum-/Jahresangabe, kein Motorkürzel
            continue
        if tok.lower() in _ZYLINDER_WORTE or _ZYLINDER_CODE.fullmatch(tok):
            continue
        if tok not in out:
            out.append(tok)
    return out


def _variantentext(m: dict | None) -> str:
    return f"{(m or {}).get('bezeichnung') or ''} {(m or {}).get('motorcode') or ''}"


def _traegt(text: str, kuerzel: str) -> bool:
    """Ob `text` das Kürzel als eigenständiges Token trägt.

    Wortgrenzen sind entscheidend: "2.0 FSI" darf NICHT als Treffer für "TFSI"
    gelten (und umgekehrt) — genau daran hing der reproduzierte Audi-Fehlbefund.
    """
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(kuerzel)}(?![A-Za-z0-9])",
                     text, re.IGNORECASE) is not None


def _unterscheidet_in_baureihe(kuerzel: str, baureihe: dict | None,
                               variante_id: str | None) -> bool:
    """Trennt dieses Kürzel die Motorvarianten DIESER Baureihe tatsächlich?

    True nur, wenn mindestens eine ANDERE Variante derselben Baureihe das Kürzel
    trägt. Damit stammt das Urteil aus der Motorliste der Baureihe und nicht aus
    Zusatzwissen dieses Moduls. Bei weniger als zwei Varianten ist "unterscheidend"
    bedeutungslos -> False.
    """
    motoren = (baureihe or {}).get("motoren") or []
    if len(motoren) < 2:
        return False
    return any(
        m.get("variante_id") != variante_id and _traegt(_variantentext(m), kuerzel)
        for m in motoren
    )


def schwachstelle_applicability(s: dict, motor_match: dict | None,
                                baureihe: dict | None) -> tuple[str, str]:
    """Betrifft diese Baureihen-Schwachstelle die erkannte Motorisierung?

    Rückgabe: (applicability, grund) mit applicability aus
    {"kompatibel", "unklar", "incompatible"}. `grund` ist ein kurzer, stabiler
    Code für Logging/Tests — kein Nutzertext.

    Ohne erkannte Motorvariante wird NIE ausgeschlossen: dann ist die Zugehörigkeit
    schlicht nicht bestimmbar ("unklar"), genau wie bei Rückrufen ohne erkannten
    Motor. Ein Ausschluss braucht einen positiven Widerspruchsbeweis, nie das
    bloße Fehlen von Daten.
    """
    scope = _scope_text(s)
    if not scope:
        return KOMPATIBEL, "kein_scope"

    scope_kraftstoffe = _kraftstoffe(scope)
    scope_zylinder = _zylinder(scope)
    kuerzel = _kuerzel(scope)
    hat_scope = bool(scope_kraftstoffe or scope_zylinder or kuerzel)
    if not hat_scope:
        return KOMPATIBEL, "kein_scope"

    if not motor_match:
        return UNKLAR, "motor_unbekannt"

    fahrzeug_kraftstoff = _norm_kraftstoff(motor_match.get("kraftstoff"))
    fahrzeug_zylinder = motor_match.get("zylinder")

    # ── 1) Harte Widersprüche ────────────────────────────────────────────────
    if scope_kraftstoffe and fahrzeug_kraftstoff:
        passt = fahrzeug_kraftstoff in scope_kraftstoffe or (
            fahrzeug_kraftstoff in _HAT_HOCHVOLT and bool(scope_kraftstoffe & _HAT_HOCHVOLT)
        )
        if not passt:
            return INKOMPATIBEL, "kraftstoff_widerspruch"

    if scope_zylinder and isinstance(fahrzeug_zylinder, int) and fahrzeug_zylinder > 0:
        if fahrzeug_zylinder not in scope_zylinder:
            return INKOMPATIBEL, "zylinder_widerspruch"

    # ── 2) Positive Zugehörigkeit — schlägt den Kontrast-Test ────────────────
    # Reihenfolge ist bewusst so (siehe Modulkopf): ein explizit passender
    # Kraftstoff belegt die Zugehörigkeit auch dann, wenn die Bezeichnung des
    # Fahrzeugs das im Scope genannte Kürzel nicht wörtlich führt.
    if scope_kraftstoffe and fahrzeug_kraftstoff in scope_kraftstoffe:
        return KOMPATIBEL, "kraftstoff_treffer"
    if scope_zylinder and fahrzeug_zylinder in scope_zylinder:
        return KOMPATIBEL, "zylinder_treffer"

    variantentext = _variantentext(motor_match)
    if any(_traegt(variantentext, k) for k in kuerzel):
        return KOMPATIBEL, "kuerzel_treffer"

    # ── 3) Kontrast innerhalb der Baureihe ───────────────────────────────────
    for k in kuerzel:
        if _unterscheidet_in_baureihe(k, baureihe, motor_match.get("variante_id")):
            log.info("Motor-Applicability: '%s' schließt Variante %r aus (Kürzel %r "
                     "unterscheidet in Baureihe %r)", (s.get("bauteil") or "")[:60],
                     motor_match.get("variante_id"), k, (baureihe or {}).get("id"))
            return INKOMPATIBEL, "kuerzel_kontrast"

    return KOMPATIBEL, "kein_widerspruch"


def gefilterte_schwachstellen(schwachstellen: list[dict] | None,
                              motor_match: dict | None,
                              baureihe: dict | None) -> list[dict]:
    """Die EINE zentrale Allowed-List für Baureihen-Schwachstellen.

    Analog zu `recall_filter.gefilterte_rueckrufe`: alle Aufrufer (Evidence,
    LLM-DB-Kontext) lesen dieselbe Entscheidung, damit nirgends eine zweite,
    ungefilterte Liste entsteht — genau der Fehler, den Reliability-Sprint 4 bei
    den Rückrufen beheben musste.

    Die zurückgegebenen Kopien tragen zusätzlich `motor_applicability` und
    `motor_applicability_grund`. Baujahres-Filterung passiert NICHT hier: die
    liegt unverändert bei den Aufrufern (P0-2).
    """
    out: list[dict] = []
    for s in schwachstellen or []:
        applicability, grund = schwachstelle_applicability(s, motor_match, baureihe)
        if applicability == INKOMPATIBEL:
            continue
        out.append({**s, "motor_applicability": applicability,
                    "motor_applicability_grund": grund})
    return out


def ausgeschlossene_schwachstellen(schwachstellen: list[dict] | None,
                                   motor_match: dict | None,
                                   baureihe: dict | None) -> list[dict]:
    """Komplement zu `gefilterte_schwachstellen` — für Diagnose und Tests."""
    out: list[dict] = []
    for s in schwachstellen or []:
        applicability, grund = schwachstelle_applicability(s, motor_match, baureihe)
        if applicability == INKOMPATIBEL:
            out.append({**s, "motor_applicability": applicability,
                        "motor_applicability_grund": grund})
    return out
