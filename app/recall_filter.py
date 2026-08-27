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

KBA-REFERENZ-TRUST-GATE (DATA-TRUST-AUDIT)

Der Audit hat gemessen: von 759 Rückrufzeilen tragen 589 eine `kba_referenz`.
Davon sind 200 Zeilen (76 unterschiedliche Referenzwerte) MARKENÜBERGREIFEND
mehrfach vergeben — dieselbe Nummer steht z.B. bei BMW, VW, Opel, Ford UND Seat.
Eine amtliche KBA-Aktionsnummer ist je Aktion eindeutig; taucht sie bei
verschiedenen Herstellern auf, ist mindestens einer der Datensätze falsch —
welcher, ist unbekannt. Zusätzlich enthält das Feld erkennbare Platzhalter/
Test-Artefakte: sequenzielle Ziffernfolgen ("1234567", "9876543", "012345") und
einen 64-stelligen Hex-Block aus fast nur Nullen. Bisher konnte allein die
BLOSSE ANWESENHEIT einer `kba_referenz` einen Rückruf auf die höchste
Ohne-VIN-Stufe ("variant_match", confidence "hoch") heben und dem Nutzer eine
amtlich wirkende Nummer zeigen — unabhängig davon, ob die Nummer plausibel war.

Die drei Funktionen `kba_referenz_vertrauenswuerdig` / `kba_referenz_anzeige` /
der `marke`-Parameter von `rueckruf_applicability` schließen das: eine
unplausible oder markenübergreifend kollidierende Referenz zählt für die
Vertrauensstufe wie eine FEHLENDE Referenz (Rückfall auf "series_only" statt
"variant_match") und wird nirgends mehr angezeigt — weder im Evidence-`ref`,
noch im DB-Kontext-Prompt, noch in Kaufaktionen (letztere erben das über die
bereits gegateten Insights, siehe app/evidence.py). Der zugrunde liegende
Rückrufdatensatz selbst wird NICHT verworfen: Mangel, Abhilfe und Baujahr
bleiben als konservativer Baureihen-Hinweis ("Kann Fahrzeuge dieser Baureihe
betreffen — per FIN prüfen") vollständig erhalten. Mehrfachverwendung
DERSELBEN Marke ist ausdrücklich KEIN Fehler (eine Rückrufaktion kann mehrere
Modelle eines Herstellers betreffen, z.B. bei geteiltem Zulieferer/Bauteil) und
senkt die Vertrauensstufe nicht.

Dies ist ein reines Laufzeit-Gate — keine DB wird verändert, keine Zeile
gelöscht, keine Migration ausgeführt. Die Datengrundlage bleibt vollständig
erhalten; nur die WIRKUNG einer nicht plausiblen Referenz wird begrenzt.
"""

import logging
import re

from app.database import get_rueckruf_referenzen_kurz

log = logging.getLogger(__name__)

_JAHR = re.compile(r"\b(?:19|20)\d{2}\b")
_BEREICH = re.compile(r"[-–]|bis")
_ALLGEMEIN = {"", "alle", "alle baujahre", "-", "n/a", "unbekannt", "diverse"}


# ── KBA-Referenz-Plausibilität ────────────────────────────────────────────────
#
# Format-Regeln DATENGETRIEBEN aus den 589 tatsächlich befüllten Referenzen
# hergeleitet (siehe Modulkopf), nicht vermutet:
#   567  rein numerisch, 3–8 Stellen ("9600", "011400")
#     8  Zifferngruppen mit Leerzeichen/Bindestrich ("64-0034", "80 14 11")
#     6  ein Buchstabe + Ziffern, 6–8 Stellen ("8A800000") — Mercedes-Schreibweise
#    12  erkennbare Platzhalter: sequenzielle Ziffernfolgen ("1234567", "9876543",
#        "012345") — real vorkommende Testdaten, kein einziger Treffer im
#        plausiblen Rest
#     4  eindeutig implausibel: ein Freitext-Satz (37 Zeichen) und ein
#        64-stelliger Hex-Block aus fast nur Nullen (3 Varianten)
#
# Der laengste real vorkommende plausible Wert ist 8 Zeichen lang; die Grenze
# unten liegt bewusst grosszuegig bei 12, um kuenftige, etwas laengere aber
# echte Formate nicht sofort zu verwerfen — sie faengt nur die beiden
# eindeutigen Ausreisser oben ab.
_MAX_REFERENZ_LAENGE = 12
_MIN_REFERENZ_LAENGE = 3
_SEQUENZ_MIN_LAENGE = 5
_TRENNER = re.compile(r"[\s-]+")
_ALNUM_MUSTER = re.compile(r"[0-9]?[A-Za-z][0-9]{5,7}")


def _ist_sequentiell(ziffern: str) -> bool:
    """Erkennt Platzhalter-/Testwerte wie '1234567' oder '9876543': jede Ziffer
    genau +1 bzw. -1 zur vorigen, über die GESAMTE Länge. Ab 5 Ziffern geprüft —
    kürzer wären zu viele echte Nummern zufällig betroffen (z.B. '123' wäre ein
    plausibler realer Wert, kein Testmuster)."""
    if len(ziffern) < _SEQUENZ_MIN_LAENGE:
        return False
    diffs = {int(b) - int(a) for a, b in zip(ziffern, ziffern[1:])}
    return diffs in ({1}, {-1})


def kba_referenz_format_plausibel(kba: str | None) -> bool:
    """Reine Formatprüfung — OHNE Kenntnis anderer Referenzen/Marken.

    Lehnt ab: leer, zu lang (Freitext/Hex-Platzhalter), zu kurz, und erkennbar
    sequenzielle Ziffernfolgen. Akzeptiert sowohl die reine Ziffernform als auch
    die beobachteten Varianten mit Leerzeichen/Bindestrich-Trennern und die
    einbuchstabige alphanumerische Form.
    """
    kba = (kba or "").strip()
    if not kba or len(kba) > _MAX_REFERENZ_LAENGE:
        return False
    kern = _TRENNER.sub("", kba)
    if kern.isdigit():
        return len(kern) >= _MIN_REFERENZ_LAENGE and not _ist_sequentiell(kern)
    return bool(_ALNUM_MUSTER.fullmatch(kern))


def _referenz_marken_index() -> dict[str, set[str]]:
    """Normalisierte Referenz -> Menge der Marken, die sie tragen — aus der
    gecachten DB-Liste (app.database.get_rueckruf_referenzen_kurz, 60s TTL).
    Neu aufgebaut bei jedem Aufruf (billig: <600 Zeilen), damit kein eigener
    Cache mit eigener Invalidierungslogik entsteht.

    Ein DB-Fehler (z.B. Tabelle noch nicht angelegt, Verbindung weg) darf die
    Formatprüfung — die primäre Sicherung — nicht mit hinunterreißen: dann gilt
    einfach kein bekannter Kollisionsfall (leerer Index), nicht "Referenz
    ungültig". Dieselbe Vorsicht wie bei app/fahrzeugkontext.py::_vorgaenger.
    """
    try:
        zeilen = get_rueckruf_referenzen_kurz()
    except Exception:
        log.info("recall_filter: Referenz-Kollisionsindex nicht verfügbar (DB-Fehler)")
        return {}
    index: dict[str, set[str]] = {}
    for zeile in zeilen:
        ref = (zeile.get("kba_referenz") or "").strip().upper()
        marke = (zeile.get("marke") or "").strip()
        if ref and marke:
            index.setdefault(ref, set()).add(marke)
    return index


def kba_referenz_kollidiert_markenuebergreifend(kba: str, marke: str | None) -> bool:
    """Ob dieselbe Referenz bei einer ANDEREN Marke als `marke` auftaucht.

    Mehrfachverwendung innerhalb DERSELBEN Marke zählt ausdrücklich NICHT als
    Kollision (§2 des Auftrags) — eine Rückrufaktion kann mehrere Modelle eines
    Herstellers betreffen. Ohne bekannte Marke (marke=None) kann keine Kollision
    geprüft werden -> gilt als unauffällig (die Formatprüfung bleibt trotzdem
    wirksam).
    """
    if not marke:
        return False
    marken = _referenz_marken_index().get(kba.strip().upper())
    if not marken:
        return False
    return bool(marken - {marke.strip()})


def kba_referenz_vertrauenswuerdig(kba: str | None, marke: str | None = None) -> bool:
    """Ob diese KBA-Referenz die Vertrauensstufe eines Rückrufs heben und dem
    Nutzer als belastbare Nummer gezeigt werden darf.

    Zwei unabhängige, konservative Bedingungen — beide müssen gelten:
      1. Format plausibel (siehe `kba_referenz_format_plausibel`).
      2. Keine markenübergreifende Kollision (siehe
         `kba_referenz_kollidiert_markenuebergreifend`).
    """
    kba = (kba or "").strip()
    if not kba_referenz_format_plausibel(kba):
        return False
    return not kba_referenz_kollidiert_markenuebergreifend(kba, marke)


def kba_referenz_anzeige(kba: str | None, marke: str | None = None) -> str | None:
    """Die Referenz, so wie sie angezeigt und für die Vertrauensstufe genutzt
    werden darf — oder None, wenn sie das Plausibilitätsgate nicht besteht.

    EIN zentraler Punkt, an dem alle Aufrufer (Evidence, DB-Kontext-Prompt,
    Kaufaktionen über die Insight-Quelle) dieselbe Entscheidung sehen — keine
    zweite, abweichende Anzeige-Logik irgendwo im Code.
    """
    kba = (kba or "").strip()
    return kba if kba and kba_referenz_vertrauenswuerdig(kba, marke) else None


def referenz_ist_belegt(r: dict | None) -> bool:
    """Ist die `kba_referenz` dieses Rückrufs INHALTLICH belegt — nicht nur
    formatplausibel?

    RECALL-VERIFICATION-PILOT (§9 des Auftrags: "Format plausibel != inhaltlich
    verified"). Die drei Funktionen `kba_referenz_format_plausibel` /
    `kba_referenz_kollidiert_markenuebergreifend` / `kba_referenz_vertrauenswuerdig`
    prüfen SCHREIBWEISE und Eindeutigkeit einer Nummer. Beides sind
    Plausibilitätsaussagen: eine frei erfundene, aber sechsstellige und nur einmal
    vergebene Nummer besteht sie mühelos. Der DATA-TRUTH-AUDIT hat genau das
    gemessen — kein einziges Referenzformat des Bestands entsprach echten
    KBA-Nummern, und trotzdem passierten 567 Werte die Formatprüfung.

    Belegt ist eine Referenz erst, wenn der Fakt selbst gegen eine amtliche Quelle
    geprüft und mit `status='verified'` in `fakt_verifikation` hinterlegt wurde.
    Diese Information hängt bereits als `_trust` am Rückruf-Dict (gesetzt von
    `app/fakt_verifikation.py::annotiere` über `app/database.py::get_baureihe`) —
    es wird hier weder eine neue Quelle gelesen noch eine neue Regel erfunden.

    WIRKUNG: nur ein `verified` Rückruf kann die stärkste Ohne-VIN-Stufe
    "variant_match" erreichen. Ein ungeprüfter bleibt auf "series_only" ("Für Teile
    der Baureihe gemeldet — per FIN prüfen"), bleibt aber vollständig sichtbar. Das
    macht die Floor-Vorbedingung aus `app/empfehlungs_floor.py` strukturell wahr:
    dort verlangt `darf_floor_tragen` ohnehin `trust == "verified"` — beide Bedingungen
    fallen jetzt zusammen, statt unabhängig voneinander gelten zu müssen.

    ZWEITE, GENAUSO WICHTIGE WIRKUNG: das Aufräumen unbelegter Referenzen wird
    nebenwirkungsfrei. Der Kollisionsindex ist bestandsabhängig — löscht man eine
    erfundene Nummer bei Fahrzeug A, kann dieselbe Nummer bei Fahrzeug B dadurch
    "kollisionsfrei" und damit vertrauenswürdig werden. Ohne dieses Gate hätte die
    Bereinigung der Pilot-Rückrufe zwei unbeteiligte Baureihen (BMW 5er G30,
    Mercedes S-Klasse W222) still auf "variant_match"/Confidence "hoch" gehoben.

    Fehlt `_trust` ganz (Aufrufer, die Rückruf-Dicts selbst bauen), gilt der Fakt
    als ungeprüft — fail-safe in die vorsichtige Richtung.
    """
    return ((r or {}).get("_trust") or "").strip().lower() == "verified"


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
#
# BUGFIX (Insignia-Nachtrag): bis hierher wurde per reinem Substring-Vergleich
# gesucht (`any(w in text for w in _HV_WORTE)`). Damit traf "elektro" auch in
# "elektronisch", "Elektronik" und "elektromechanisch" — Wörter, die ELEKTRONIK
# beschreiben, nicht einen Hochvolt-ANTRIEB. Folge: jeder Rückruf, der ein
# elektronisches Steuergerät nennt, galt als Hochvolt-/PHEV-Rückruf und wurde
# für jedes Verbrennerfahrzeug als "incompatible" VOLLSTÄNDIG ausgeblendet —
# aus Findings, Kaufaktionen und jedem LLM-Prompt.
#
# Gemessen an der Datenbank betraf das 8 Rückrufe, darunter mehrere
# sicherheitsrelevante: Ausfall der Lenkunterstützung (Audi Q7, Audi RS 7, VW
# Tiguan, Mercedes A-Klasse), fehlerhafte Programmierung der elektronischen
# Feststellbremse (Toyota Corolla) und des elektronischen Stabilitätsprogramms
# (Toyota Camry, Toyota Hilux). Keiner davon war je für einen Verbrenner
# sichtbar. Aufgefallen ist es, weil der amtlich belegte Insignia-Rückruf
# KBA 12223 ("... weil das elektronische Bremssteuermodul nicht korrekt
# konfiguriert ist") aus demselben Grund unsichtbar blieb.
#
# Die Erkennung läuft jetzt über ein Muster mit Wortanfangs-Grenze und einem
# ausdrücklichen Ausschluss für "elektronisch*"/"Elektronik*"/
# "elektromechanisch*". Alles andere bleibt unverändert: "Hochvoltbatterie",
# "Elektromotor", "Plug-in-Hybrid" usw. treffen weiterhin.
#
# BEWUSST NICHT MIT BEHOBEN: "elektrisch" ist zusätzlich semantisch zu breit —
# eine "elektrische Kraftstoffpumpe" oder eine "elektrische Servolenkung" ist
# 12-Volt-Technik in einem ganz gewöhnlichen Verbrenner, kein Hochvoltsystem.
# Das betrifft weitere 29 Rückrufe. Diese Änderung wäre eine BEDEUTUNGSfrage,
# keine Fehlerkorrektur, und gehört in eine eigene Prüfung — siehe Bericht.
_HV_MUSTER = re.compile(
    r"(?<![a-zäöüß])(?:"
    r"hochvolt|hochspannung|plug-?\s?in|plugin|phev|hybrid|"
    r"elektro(?!nisch|nik|mechanisch)|elektrisch"
    r")",
    re.IGNORECASE,
)


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


def rueckruf_applicability(r: dict, passt: bool | None, kba: str, motor_match: dict | None,
                           marke: str | None = None):
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

    `marke` (optional, KBA-Trust-Gate): nur mit ihr kann eine markenübergreifende
    Kollision der `kba_referenz` erkannt werden (siehe Modulkopf). Eine unplausible
    oder kollidierende Referenz zählt hier wie eine FEHLENDE — sie hebt die Stufe
    nicht auf "variant_match", der Rückruf selbst bleibt konservativ als
    "series_only" erhalten.
    """
    text = " ".join(filter(None, [r.get("mangel"), r.get("abhilfe"), r.get("betroffene_baujahre")]))
    ist_hv_rueckruf = bool(_HV_MUSTER.search(text))
    qualifier = _paren_qualifier(r.get("betroffene_baujahre"))       # z.B. "phev"
    fahrzeug_kraftstoff = _norm_kraftstoff((motor_match or {}).get("kraftstoff"))
    # KBA-Trust-Gate: eine unplausible/kollidierende Referenz zählt wie keine.
    # RECALL-PILOT (§9): und eine bloß FORMATPLAUSIBLE zählt ebenfalls wie keine.
    kba_ok = bool(kba_referenz_anzeige(kba, marke)) and referenz_ist_belegt(r)

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
            # Passende Variante + Baujahr-Deckung + PLAUSIBLE KBA-Referenz -> stärkste
            # OHNE-VIN erreichbare Stufe: "kann diese Variante betreffen", nicht
            # "betrifft".
            if passt is True and kba_ok:
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
    if passt is True and kba_ok:
        return ("variant_match", "hoch",
                f"Sicherheitsrelevant — Durchführung der Rückrufaktion per FIN prüfen. {_HINWEIS_FIN}", "")
    if passt is True:
        return ("series_only", "mittel",
                f"Sicherheitsrelevant — Durchführung der Rückrufaktion prüfen. {_HINWEIS_FIN}", "")
    return ("series_only", "mittel",
            f"Sicherheitsrelevant — Baujahr-Zuordnung nicht eindeutig. {_HINWEIS_FIN}", "")


def _annotiere(r: dict, motor_match: dict | None, baujahr: int | None,
               marke: str | None = None) -> dict:
    """Baut EINE annotierte Kopie eines Rückruf-Datensatzes: Original-Felder +
    applicability/confidence/einfluss/variant_hinweis + ein fertig formatierter
    `text` (für Prompt-/DB-Kontext-Einbettung, MIT Applicability-Formulierung statt
    nacktem mangel/abhilfe).

    KBA-Trust-Gate: `kba_referenz_anzeige` ist die EINZIGE Referenz, die Aufrufer
    dem Nutzer zeigen dürfen (Roh-`kba_referenz` bleibt über `**r` zwar im Dict,
    aber ausschließlich für Diagnosezwecke — nicht für die Anzeige gedacht)."""
    passt = _baujahr_passt(r.get("betroffene_baujahre"), baujahr)
    kba = (r.get("kba_referenz") or "").strip()
    kba_anzeige = kba_referenz_anzeige(kba, marke)
    applicability, confidence, einfluss, variant_hinweis = rueckruf_applicability(
        r, passt, kba, motor_match, marke=marke)
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
        "kba_referenz_anzeige": kba_anzeige,
    }


def gefilterte_rueckrufe(rueckrufe: list[dict] | None, motor_match: dict | None,
                         baujahr: int | None, marke: str | None = None) -> list[dict]:
    """Die EINE zentrale Allowed-List (§Phase 7): nur Rückrufe, die dieses Fahrzeug
    laut Datenlage betreffen KÖNNTEN — Baujahr-eindeutig-unpassende UND Antriebs-
    widersprüchliche ("incompatible", z.B. Hochvolt-Rückruf bei erkanntem Diesel)
    Rückrufe werden entfernt. Jeder zurückgegebene Eintrag trägt zusätzlich
    `applicability`/`confidence`/`einfluss`/`text` (fertig für Insights, Key
    Findings, DB-Kontext, LLM-Prompt, Chat-Kontext, Risikoübersicht — EIN Aufruf,
    EIN Ergebnis für alle Konsumenten).

    `marke` (optional, KBA-Trust-Gate): ermöglicht die markenübergreifende
    Kollisionsprüfung der `kba_referenz`. Ohne sie bleibt die Formatprüfung
    trotzdem wirksam — nur die Kollisionsprüfung entfällt dann."""
    out = []
    for r in rueckrufe or []:
        passt = _baujahr_passt(r.get("betroffene_baujahre"), baujahr)
        if passt is False:
            continue
        annotiert = _annotiere(r, motor_match, baujahr, marke=marke)
        if annotiert["applicability"] == "incompatible":
            continue
        out.append(annotiert)
    return out


def ausgeschlossene_rueckrufe(rueckrufe: list[dict] | None, motor_match: dict | None,
                              baujahr: int | None, marke: str | None = None) -> list[dict]:
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
        annotiert = _annotiere(r, motor_match, baujahr, marke=marke)
        if annotiert["applicability"] == "incompatible":
            out.append({**annotiert, "ausschlussgrund": "antrieb_unpassend"})
    return out
