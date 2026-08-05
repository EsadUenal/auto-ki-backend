from __future__ import annotations

"""
Zentrale Rückruf-Allowed-Liste (Reliability-Sprint 4, §Phase 7).

EIN neutrales Modul (bewusst NICHT in app/evidence.py versteckt, um jeden Kreis-
Import mit den Aufrufern zu vermeiden — car_lookup.py/llm.py/kaufcheck.py/
verkaufscheck.py importieren alle von HIER, evidence.py importiert ebenfalls von
HIER), das für ein Fahrzeug (Baureihen-Rückrufe + erkannte Motorisierung) EINE
einzige, deterministische Entscheidung trifft: welche Rückrufe dieses Fahrzeug
überhaupt betreffen können (`gefilterte_rueckrufe`) und welche eindeutig NICHT
zutreffen (`ausgeschlossene_rueckrufe`).

Der Bug, den dieses Modul behebt: `app/evidence.py::build_insights` filterte
Hochvolt-/PHEV-Rückrufe für die STRUKTURIERTEN Insights/Key-Findings bereits
korrekt heraus — aber `app/car_lookup.py::build_db_context` (Kauf-/Verkaufscheck-
LLM-Prompt) und `app/llm.py::_sql_context` (allgemeiner Chat) lasen dieselbe
`rueckrufe`-DB-Spalte UNGEFILTERT und kippten sie roh in den Gemini-Prompt — das
LLM bekam den Hochvolt-Rückruf trotzdem zu sehen und schrieb ihn in Bericht/
Checkliste. Ab sofort nutzen ALLE Aufrufer ausschließlich `gefilterte_rueckrufe`
aus diesem Modul — keine zweite, ungefilterte Rückrufliste mehr irgendwo im Code.

Die Tabelle `rueckruf` ist NUR an baureihe_id gekoppelt (kein motorvariante_id/
kraftstoff/antrieb-Feld). Die Varianten-Einschränkung steht — wenn überhaupt — als
FREITEXT im mangel/abhilfe/betroffene_baujahre (z.B. "(Plug-in-Hybrid)",
"Hochvoltbatterie"). Daraus leiten wir DETERMINISTISCH ab, ob ein Rückruf einen
bestimmten Antrieb/Kraftstoff adressiert — ohne Raten, ohne LLM.
"""

import re

_JAHR = re.compile(r"\b(?:19|20)\d{2}\b")
_BEREICH = re.compile(r"[-–]|bis")
_ALLGEMEIN = {"", "alle", "alle baujahre", "-", "n/a", "unbekannt", "diverse"}


def _jahre(text: str | None) -> list[int]:
    return [int(y) for y in _JAHR.findall(text or "")]


def _baujahr_passt(betroffene: str | None, baujahr: int | None) -> bool | None:
    """Ob `baujahr` in die Baujahr-Angabe fällt.

    True  = fällt eindeutig hinein
    False = fällt eindeutig NICHT hinein (Rückruf ist für dieses Fahrzeug irrelevant)
    None  = nicht bestimmbar (allgemeine Angabe oder kein Baujahr) -> als bedingt werten
    """
    if betroffene is None:
        return None
    t = betroffene.strip().lower()
    if t in _ALLGEMEIN:
        return None
    if baujahr is None:
        return None
    jahre = _jahre(betroffene)
    if not jahre:
        return None
    if _BEREICH.search(t):
        return min(jahre) <= baujahr <= max(jahre)
    return baujahr in jahre


# Signalwörter, die einen Rückruf auf Hochvolt-/Hybrid-/Elektro-Antrieb eingrenzen.
_HV_WORTE = ("hochvolt", "hochspannung", "plug-in", "plug in", "plugin", "phev",
             "hybrid", "elektro", "elektrisch")


# Normierung des Kraftstoffs (Motorvariante ODER Rückruf-Freitext-Qualifier).
def _norm_kraftstoff(text: str | None) -> str | None:
    t = (text or "").lower()
    if not t:
        return None
    if "mild" in t:
        return "mild"          # Mild-Hybrid (48V) — NICHT das Hochvolt-System eines PHEV/BEV
    if any(k in t for k in ("plug-in", "plug in", "plugin", "phev")):
        return "phev"
    if "elektro" in t or "electric" in t:
        return "elektro"
    if "hybrid" in t:
        return "phev"          # generisches "Hybrid" -> Hochvolt-Antrieb (nicht Mild s.o.)
    if "diesel" in t:
        return "diesel"
    if "benzin" in t or "petrol" in t:
        return "benzin"
    return None


def _paren_qualifier(betroffene: str | None) -> str | None:
    """Antriebs-Qualifier aus einem Klammerzusatz wie '2019-2020 (Plug-in-Hybrid)'."""
    if not betroffene:
        return None
    m = re.search(r"\(([^)]*)\)", betroffene)
    return _norm_kraftstoff(m.group(1)) if m else None


# Antriebe, die ein Hochvolt-System besitzen (für den Abgleich mit HV-Rückrufen).
_HAT_HOCHVOLT = {"phev", "elektro"}

# Reliability-Sprint 3 (§27/§28): "exakt" wurde bisher als "Betrifft dein Fahrzeug"
# angezeigt — allein aus Baujahr-Text-Match + vorhandener KBA-Referenznummer, OHNE
# jede VIN-/FIN-Prüfung (die es im System nicht gibt). Das war zu sicher formuliert.
# Vierstufige Semantik (fünfte Stufe reserviert, aktuell unerreichbar):
#   confirmed_by_vin — NUR nach echter VIN-Prüfung. Wird vom Code aktuell NIE erzeugt
#                       (keine VIN-Erfassung im System) — bewusst kein Fake-Feature.
#   variant_match     — Baujahr/Antriebs-Variante passt, KBA-Referenz vorhanden.
#   series_only       — Baujahr passt bzw. allgemeiner Baureihen-Rückruf, aber ohne
#                        die belastbarste Kombination aus Variante+Referenz.
#   unclear           — Betroffenheit nicht bestimmbar.
#   incompatible       — Antriebs-/Variantenwiderspruch, wird vollständig ausgeblendet.
_HINWEIS_FIN = "Betroffenheit anhand der FIN beim Hersteller/KBA prüfen."

RUECKRUF_APPLICABILITY_TEXT: dict[str, str] = {
    "confirmed_by_vin": "Für dieses Fahrzeug per FIN bestätigt",
    "variant_match": "Kann Fahrzeuge dieser Variante betreffen — per FIN prüfen",
    "series_only": "Für Teile der Baureihe gemeldet — per FIN prüfen",
    "unclear": "Betroffenheit unklar — per FIN prüfen",
}


def rueckruf_applicability(r: dict, passt: bool | None, kba: str, motor_match: dict | None):
    """Bestimmt, WIE SICHER ein Rückruf GENAU DIESES Fahrzeug betrifft.

    Rückgabe: (applicability, confidence, einfluss, variant_hinweis)
      applicability: "variant_match" | "series_only" | "unclear" | "incompatible"
                      (theoretisch auch "confirmed_by_vin" — aktuell nie erzeugt)
      confidence:    Beleglage des Insights ("hoch"|"mittel"|"niedrig")
      einfluss:      Handlungshinweis
      variant_hinweis: Zusatztext für die Beschreibung (oder "")

    Strikt getrennte Konzepte:
      severity  = wie schlimm            (eigenes Feld, hier NICHT berührt)
      confidence= wie gut belegt         (Beleglage/Provenance)
      applicability = betrifft dieses Fahrzeug  (Varianten-/Antriebs-Zuordnung) —
        OHNE VIN-Prüfung niemals "confirmed_by_vin"/eine "Betrifft dein Fahrzeug
        garantiert"-Aussage (§27).
    """
    text = " ".join(filter(None, [r.get("mangel"), r.get("abhilfe"), r.get("betroffene_baujahre")]))
    ist_hv_rueckruf = any(w in text.lower() for w in _HV_WORTE)
    qualifier = _paren_qualifier(r.get("betroffene_baujahre"))       # z.B. "phev"
    fahrzeug_kraftstoff = _norm_kraftstoff((motor_match or {}).get("kraftstoff"))

    # Der Rückruf grenzt sich auf einen bestimmten Antrieb ein (Klammer-Qualifier
    # ODER klarer Hochvolt-/Hybrid-Bezug).
    scope = qualifier or ("phev" if ist_hv_rueckruf else None)
    if scope:
        if fahrzeug_kraftstoff is None:
            # Motor nicht erkannt -> Varianten-Betroffenheit NICHT bestimmbar.
            return ("unclear", "niedrig",
                    f"Betroffenheit unklar — der Rückruf betrifft bestimmte Varianten. {_HINWEIS_FIN}",
                    "Für die Baureihe hinterlegt; die genaue Variantenbetroffenheit ist ohne erkannte Motorisierung nicht gesichert.")
        matcht = (
            fahrzeug_kraftstoff == scope
            or (scope in _HAT_HOCHVOLT and fahrzeug_kraftstoff in _HAT_HOCHVOLT)
        )
        if matcht:
            # Passende Variante + Baujahr-Deckung + KBA-Referenz -> stärkste OHNE-VIN
            # erreichbare Stufe: "kann diese Variante betreffen", nicht "betrifft".
            if passt is True and kba:
                return ("variant_match", "hoch",
                        f"Sicherheitsrelevant — Durchführung der Rückrufaktion per FIN prüfen. {_HINWEIS_FIN}", "")
            return ("series_only", "mittel",
                    f"Sicherheitsrelevant — Durchführung der Rückrufaktion prüfen. {_HINWEIS_FIN}", "")
        # Klarer Antriebs-Widerspruch (§8): z.B. Hochvolt-/PHEV-Rückruf, Fahrzeug ist
        # nachweislich Diesel. Die Motorisierung ist ERKANNT und passt eindeutig NICHT
        # -> "incompatible". Solche Rückrufe werden VOLLSTÄNDIG aus den sichtbaren
        # Findings UND aus jedem LLM-Prompt entfernt (kein Anzeigen als "unklare
        # Betroffenheit").
        scope_label = {"phev": "Plug-in-Hybrid-/Hochvolt-Varianten", "elektro": "Elektro-Varianten",
                       "diesel": "Diesel-Varianten", "benzin": "Benzin-Varianten",
                       "mild": "Mild-Hybrid-Varianten"}.get(scope, "bestimmte Varianten")
        return ("incompatible", "hoch",
                f"Betrifft laut Datenlage {scope_label} — die erkannte Motorisierung gehört nicht dazu.",
                f"Dieser Rückruf betrifft {scope_label}; die erkannte Motorisierung passt eindeutig nicht dazu.")

    # Kein Antriebs-Scope erkennbar -> allgemeiner Baureihen-Rückruf (z.B. Bremse,
    # Lenkung): gilt unabhängig von der Motorisierung.
    if passt is True and kba:
        return ("variant_match", "hoch",
                f"Sicherheitsrelevant — Durchführung der Rückrufaktion per FIN prüfen. {_HINWEIS_FIN}", "")
    if passt is True:
        return ("series_only", "mittel",
                f"Sicherheitsrelevant — Durchführung der Rückrufaktion prüfen. {_HINWEIS_FIN}", "")
    return ("series_only", "mittel",
            f"Sicherheitsrelevant — Baujahr-Zuordnung nicht eindeutig. {_HINWEIS_FIN}", "")


def _annotiere(r: dict, motor_match: dict | None, baujahr: int | None) -> dict:
    """Baut EINE annotierte Kopie eines Rückruf-Datensatzes: Original-Felder +
    applicability/confidence/einfluss/variant_hinweis + ein fertig formatierter
    `text` (für Prompt-/DB-Kontext-Einbettung, MIT Applicability-Formulierung statt
    nacktem mangel/abhilfe)."""
    passt = _baujahr_passt(r.get("betroffene_baujahre"), baujahr)
    kba = (r.get("kba_referenz") or "").strip()
    applicability, confidence, einfluss, variant_hinweis = rueckruf_applicability(r, passt, kba, motor_match)
    beschr = (r.get("mangel") or "").strip()
    if r.get("abhilfe"):
        beschr = f"{beschr} — Abhilfe: {r['abhilfe'].strip()}"
    if r.get("datum"):
        beschr = f"{beschr} (Rückruf {r['datum']})"
    wortlaut = RUECKRUF_APPLICABILITY_TEXT.get(applicability, applicability)
    text = f"{beschr} [{wortlaut}]" + (f" — {variant_hinweis}" if variant_hinweis else "")
    return {
        **r,
        "passt_baujahr": passt,
        "applicability": applicability,
        "confidence": confidence,
        "einfluss": einfluss,
        "variant_hinweis": variant_hinweis,
        "text": text,
    }


def gefilterte_rueckrufe(rueckrufe: list[dict] | None, motor_match: dict | None,
                         baujahr: int | None) -> list[dict]:
    """Die EINE zentrale Allowed-List (§Phase 7): nur Rückrufe, die dieses Fahrzeug
    laut Datenlage betreffen KÖNNTEN — Baujahr-eindeutig-unpassende UND Antriebs-
    widersprüchliche ("incompatible", z.B. Hochvolt-Rückruf bei erkanntem Diesel)
    Rückrufe werden entfernt. Jeder zurückgegebene Eintrag trägt zusätzlich
    `applicability`/`confidence`/`einfluss`/`text` (fertig für Insights, Key
    Findings, DB-Kontext, LLM-Prompt, Chat-Kontext, Risikoübersicht — EIN Aufruf,
    EIN Ergebnis für alle Konsumenten)."""
    out = []
    for r in rueckrufe or []:
        passt = _baujahr_passt(r.get("betroffene_baujahre"), baujahr)
        if passt is False:
            continue
        annotiert = _annotiere(r, motor_match, baujahr)
        if annotiert["applicability"] == "incompatible":
            continue
        out.append(annotiert)
    return out


def ausgeschlossene_rueckrufe(rueckrufe: list[dict] | None, motor_match: dict | None,
                              baujahr: int | None) -> list[dict]:
    """Komplement zu `gefilterte_rueckrufe` — Rückrufe, die für dieses Fahrzeug
    NACHWEISLICH NICHT gelten (Baujahr-unpassend ODER applicability=="incompatible").
    Grundlage für den Report-Validator (§Phase 8): welche Begriffe dürfen im
    fertigen LLM-Bericht NICHT auftauchen."""
    out = []
    for r in rueckrufe or []:
        passt = _baujahr_passt(r.get("betroffene_baujahre"), baujahr)
        if passt is False:
            out.append({**r, "ausschlussgrund": "baujahr_unpassend"})
            continue
        annotiert = _annotiere(r, motor_match, baujahr)
        if annotiert["applicability"] == "incompatible":
            out.append({**annotiert, "ausschlussgrund": "antrieb_unpassend"})
    return out
