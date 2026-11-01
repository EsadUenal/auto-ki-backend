"""
ENFAL-Reparaturpaket zum Production-KaufCheck vom 01.11.2026 (Lexus RC F):
gezielte Regressionen für die drei begrenzten, autorisierten Backend-Fixes
L2, L4, L5a -- plus eine klar als DIAGNOSTISCH gekennzeichnete, isolierte
Nachprüfung der Query-Konstruktion fuer L1 (keine Behauptung, die
Quellensuche sei damit geloest -- das bleibt offen, s. Abschlussbericht).

L2 (app/laufleistung.py::_formuliere(), STATUS_DARUEBER-Zweig): die vorige
Fassung rechnete `kilometerstand - von_km` ("liegt rund 67.000 km zurück" bei
82.000 km Laufleistung, 15.000-km-Intervall) -- rechnerisch korrekt, aber
KEIN fachlich zulässiger Wartungsrückstand ohne bekannten Bezug zur
tatsächlichen Servicehistorie (ein wiederkehrendes Intervall ohne `bis_km`
wurde seit Fahrzeugbeginn unbekannt oft durchlaufen). Die Zahl entfaellt
jetzt vollstaendig -- auch NICHT durch eine Modulo-Rechnung ersetzt (die
kennt den letzten Service ebenso wenig) -- Intervall und Beleg-Frage bleiben.

L4 (app/web_search.py::_qualitaets_label()): "lexus.ch" erschien als
"Sonstige Quelle" statt "Hersteller", weil `_ist_herstellerseite()` nur die
geschlossene `_HERSTELLER_MARKEN`-Liste kennt. `_marke_eigene_domain()`
(bereits bestehend, bisher nur fuer die Vertrauensschwelle genutzt) wurde
NICHT dupliziert, sondern nach app/web_search.py verschoben (zirkulaerer
Import sonst: app/technical_research.py importiert bereits von
app/web_search.py) und von dort re-importiert -- EINE Logik, keine zweite
Markenliste, keine Aenderung an Schwelle/Tier/Confidence (Check 4c).

L5a (app/empfehlung_gruende.py): der Sammelsatz "Gemeldete Rückrufe gelten
für Teile der Baureihe." stand unabhaengig davon, ob ueberhaupt ein
ENFAL-Katalogeintrag bekannt ist (canonical-only) UND unabhaengig davon, ob
die Gruppe wirklich einheitlich "series_only" war. Nutzt jetzt den
bestehenden, generischen `rueckruf_wortlaut()` (app/recall_filter.py) je
tatsaechlicher (applicability, baureihe_bekannt)-Kombination; eine gemischte
Gruppe bekommt eine neutrale, nicht ueberziehende Formulierung statt einer
fuer einen Teil der Gruppe falschen Pauschalaussage.

    python scripts/test_isolated.py test_production_01_11_reparaturpaket.py
"""
from app.empfehlung_gruende import baue_empfehlung_gruende
from app.laufleistung import _formuliere, _wartungshinweise, STATUS_DARUEBER, STATUS_IM_BEREICH
from app.models import Insight
from app.recall_filter import rueckruf_wortlaut
from app.technical_research import _marke_eigene_domain as _marke_eigene_domain_reimport
from app.technical_research import _quellen_aus
from app.web_search import _marke_eigene_domain, _qualitaets_label

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


# ── L2: wiederkehrendes Intervall bei hoher Laufleistung, keine Servicehistorie ─

VERBOTENE_WORTE = ("fällig", "überfällig", "versäumt", "nicht durchgeführt", "fehlt")


def _enthaelt_km_ruecksstand(text: str) -> bool:
    """Heuristik fuer den konkreten, beanstandeten Bugmuster: eine Zahl
    unmittelbar vor 'zurück' (z.B. '67.000 km zurück')."""
    import re
    return bool(re.search(r"\d[\d.]*\s*km\s+zur[uü]ck", text))


for bauteil, von_km, bis_km, km in (
    ("Oel und filterwechsel", 15_000, None, 82_000),      # realer Produktionsfall
    ("Zahnriemen", 90_000, 120_000, 250_000),             # andere Wartungsart, Spanne
    ("Automatikgetriebeöl", 60_000, None, 82_000),        # zweite reale Angabe desselben Laufs
):
    text = _formuliere(bauteil, STATUS_DARUEBER, km, von_km, bis_km, "web_wartung")
    check(f"L2a) '{bauteil}' (Intervall {von_km}, Laufleistung {km}): kein "
          f"berechneter km-Rückstand im Text",
          not _enthaelt_km_ruecksstand(text), text)
    check(f"L2b) '{bauteil}': keine der laut Docstring verbotenen Fälligkeits-"
          f"Wörter", not any(w in text.lower() for w in VERBOTENE_WORTE), text)
    check(f"L2c) '{bauteil}': das hinterlegte Intervall bleibt genannt",
          f"{von_km:,}".replace(",", ".") in text, text)

# STATUS_IM_BEREICH bleibt unveraendert (Regressionsschutz: der Fix betrifft
# NUR den STATUS_DARUEBER-Zweig).
im_bereich_text = _formuliere("Ventilspiel", STATUS_IM_BEREICH, 82_000, 90_000, None, "web_wartung")
check("L2d) STATUS_IM_BEREICH bleibt unveraendert (kein Fix-Ziel)",
      "im relevanten Bereich" in im_bereich_text, im_bereich_text)

# End-to-End: dieselbe Zahl darf auch nicht in den abhängigen Verkäuferfragen-/
# Dokumenttexten auftauchen (app/kaufaktionen.py baut sie als f"{w.hinweis} ...").
REAL_WARTUNG_INSIGHT = Insight(
    id="web-wartung-1", kategorie="web_wartung", titel="Oel und filterwechsel: Wartungsangabe",
    beschreibung=("[...] Lexus Care-Zwischenservice: alle 15.000 km oder einmal jährlich, "
                 "je nachdem, was zuerst eintritt. Dieser Service beinhaltet einen Öl- und "
                 "Filterwechsel sowie zusätzliche Inspektionen von Lenkung, Bremsen, "
                 "Beleuchtung und Reifen."),
    confidence="niedrig", bauteil="Oel und filterwechsel")
hinweise = _wartungshinweise([REAL_WARTUNG_INSIGHT], 82_000)
check("L2e) echter Produktionsfall: _wartungshinweise() liefert genau einen "
      "Hinweis mit status='darueber'",
      len(hinweise) == 1 and hinweise[0].status == STATUS_DARUEBER,
      f"hinweise={hinweise!r}")
if hinweise:
    verkaeuferfrage_aktion = f"{hinweise[0].hinweis} Nach Datum, Kilometerstand und Beleg fragen."
    dokument_text = (f"{hinweise[0].hinweis} Rechnung oder Eintrag im Serviceheft mit Datum und "
                     f"Kilometerstand zeigen lassen.")
    check("L2f) Verkäuferfragen-Text (app/kaufaktionen.py-Muster) enthält "
          "keinen berechneten km-Rückstand",
          not _enthaelt_km_ruecksstand(verkaeuferfrage_aktion), verkaeuferfrage_aktion)
    check("L2g) Dokumenten-Text (app/kaufaktionen.py-Muster) enthält "
          "keinen berechneten km-Rückstand",
          not _enthaelt_km_ruecksstand(dokument_text), dokument_text)

# ── L4: Herstellerlabel und Vertrauensprüfung konsistent ───────────────────

check("L4a) lexus.ch OHNE Zielmarke bleibt rückwärtskompatibel 'Sonstige "
      "Quelle' (kein stillschweigender Verhaltenswechsel für Aufrufer ohne "
      "Marke, z.B. results_to_belege())",
      _qualitaets_label("https://tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf") == "Sonstige Quelle")
check("L4b) lexus.ch MIT Zielmarke 'Lexus' wird jetzt als 'Hersteller' erkannt",
      _qualitaets_label("https://tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf", "Lexus") == "Hersteller")
check("L4c) lexus.ch MIT falscher Zielmarke 'BMW' bleibt 'Sonstige Quelle' -- "
      "keine pauschale Herstellerdomain-Aufwertung, nur fuer die TATSAECHLICH "
      "angefragte Marke",
      _qualitaets_label("https://tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf", "BMW") == "Sonstige Quelle")
check("L4d) bereits bekannte Herstellerdomain (bmw.de, geschlossene Liste) "
      "bleibt unveraendert 'Hersteller' -- keine Regression",
      _qualitaets_label("https://www.bmw.de/x") == "Hersteller")
check("L4e) EINE Logik: der Re-Import in app.technical_research liefert "
      "dasselbe Ergebnis wie app.web_search._marke_eigene_domain -- keine "
      "zweite, abweichende Implementierung",
      _marke_eigene_domain("https://lexus.ch/x", "Lexus")
      == _marke_eigene_domain_reimport("https://lexus.ch/x", "Lexus") is True)
quellen_mit_marke = _quellen_aus(
    [{"url": "https://tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf",
     "title": "Lexus Garantie- und Serviceheft", "content": "..."}],
    marke="Lexus")
check("L4f) end-to-end über _quellen_aus(): die erzeugte EvidenceQuelle trägt "
      "jetzt qualitaet='Hersteller', NICHT nur die direkte Funktion",
      len(quellen_mit_marke) == 1 and quellen_mit_marke[0].qualitaet == "Hersteller",
      f"quellen={quellen_mit_marke!r}")

# ── L5a: canonical-only, bekannte Baureihe, gemischte Rückrufgruppen ───────


def _rueckruf_insight(applicability: str, baureihe_bekannt: bool, suffix: str) -> Insight:
    return Insight(
        id=f"rueckruf-{suffix}", kategorie="rueckruf", titel=f"Mangel {suffix}",
        beschreibung="Amtlicher Rückruftext.", confidence="hoch",
        applicability=applicability, rueckruf_baureihe_bekannt=baureihe_bekannt)


def _rueckruf_satz(insights: list[Insight]) -> str | None:
    gruende = baue_empfehlung_gruende(
        req=None, baureihe=None, motor_match=None, insights=insights, key_findings=[],
        empfehlung="kaufen_nach_besichtigung", markt_verfuegbar=False, preis_label=None)
    return next((g for g in gruende if g.startswith("Gemeldete Rückrufe")), None)


canonical_only = [_rueckruf_insight("series_only", False, "a"),
                  _rueckruf_insight("series_only", False, "b")]
satz_canonical = _rueckruf_satz(canonical_only)
check("L5a) canonical-only (kein ENFAL-Katalogeintrag, realer RC-F-Fall): "
      "'dieses Modells', NICHT die 'Teile der Baureihe'-Formulierung (der "
      "Klarstellungszusatz '(keine ENFAL-Baureihen-Zuordnung)' darf das Wort "
      "'Baureihe' selbst noch enthalten, das ist kein Scope-Anspruch)",
      satz_canonical is not None and "dieses Modells" in satz_canonical
      and "Teile der Baureihe" not in satz_canonical, f"satz={satz_canonical!r}")

bekannte_baureihe = [_rueckruf_insight("series_only", True, "c")]
satz_bekannt = _rueckruf_satz(bekannte_baureihe)
check("L5b) bekannte Baureihe (ENFAL-Katalogeintrag vorhanden): weiterhin "
      "'Baureihe' -- der generische Wortlaut unterscheidet beide Fälle korrekt",
      satz_bekannt is not None and "Baureihe" in satz_bekannt
      and "dieses Modells" not in satz_bekannt, f"satz={satz_bekannt!r}")

nur_unklar = [_rueckruf_insight("unclear", False, "d")]
satz_unklar = _rueckruf_satz(nur_unklar)
check("L5c) ausschließlich 'unclear': 'Betroffenheit unklar', NICHT "
      "'Baureihe'/'Modell' (unclear behauptet keinen Teil-Scope)",
      satz_unklar is not None and "Betroffenheit unklar" in satz_unklar
      and "Baureihe" not in satz_unklar and "Modell" not in satz_unklar,
      f"satz={satz_unklar!r}")

gemischt = [_rueckruf_insight("series_only", False, "e"), _rueckruf_insight("unclear", False, "f")]
satz_gemischt = _rueckruf_satz(gemischt)
check("L5d) gemischte Gruppe (series_only UND unclear zusammen): neutrale "
      "Formulierung, KEINE Pauschalaussage, die für einen Teil der Gruppe "
      "falsch wäre",
      satz_gemischt is not None and "unterschiedlicher Betroffenheitseinstufung" in satz_gemischt,
      f"satz={satz_gemischt!r}")

# ── L1 (DIAGNOSTISCH, keine Loesung): tatsaechliche Live-Query-Konstruktion ─
# Zeigt NUR die vom bestehenden Code tatsaechlich konstruierten Tavily-
# Query-Strings fuer exakt die Production-Eingabe (kombiniertes Motor-/
# Antriebsfeld "5.0 V8, Heckantrieb", mehrere gleichzeitig offene
# Identitaetsfelder). KEIN Netzwerkzugriff (tavily_search gestubbt, liefert
# nur Mindest-Treffer zur Phasen-Freischaltung). Beweist NICHT, warum die
# echte Live-Suche diesmal keine Herstellerquelle fand -- das bleibt ohne
# Rohantworten offen (s. Abschlussbericht) -- zeigt nur, dass die Query
# selbst strukturell lang, mehrdeutig gebuendelt und kommabehaftet ist.
import asyncio

import app.technical_research as tr

_AUFGEZEICHNETE_QUERIES: list[str] = []


async def _fake_tavily_search(query, *, count=8, exclude_domains=None, include_domains=None):
    _AUFGEZEICHNETE_QUERIES.append(query)
    if "technische Daten" in query:
        return [{"url": "https://www.adac.de/lexus-rc-f-diagnose",
                 "title": "Lexus RC F Test | ADAC", "content": "Der Lexus RC F im Fahrbericht."}]
    if "Bauzeitraum" in query:
        return [{"url": "https://www.auto-motor-und-sport.de/lexus-rc-f-diagnose",
                 "title": "Lexus RC F Daten", "content": "Der Lexus RC F mit technischen Daten."}]
    return []


async def _diagnose_query_konstruktion() -> None:
    orig = tr.tavily_search
    tr.tavily_search = _fake_tavily_search
    try:
        prov = tr.TavilyTechnicalResearchProvider()
        await prov.recherchiere(marke="Lexus", modell="RC F", baujahr=2017,
                                motor="5.0 V8, Heckantrieb", ausgeloest_durch="db_miss",
                                ziel={"marke": "Lexus", "modell": "RC F", "baujahr": 2017,
                                      "motor": "5.0 V8, Heckantrieb", "leistung_ps": 477})
    finally:
        tr.tavily_search = orig


asyncio.run(_diagnose_query_konstruktion())
print("--- L1 DIAGNOSTISCH: tatsächlich konstruierte Tavily-Queries ---")
for i, q in enumerate(_AUFGEZEICHNETE_QUERIES, 1):
    print(f"  [{i}] {q!r}")
spec_gap_queries = [q for q in _AUFGEZEICHNETE_QUERIES if "Spezifikation" in q or "specifications" in q]
check("L1-DIAGNOSE) die Round-2-Spec-Gap-Query buendelt bei mehreren offenen "
      "Identitätsfeldern (hier: generation+fuel+drivetrain+transmission+"
      "powertrain) ALLE davon PLUS das kommabehaftete Motorfeld in EINE "
      "lange Query -- strukturell belegt, NICHT als bewiesene Ursache fuer "
      "den konkreten Production-Lauf behauptet (dafuer fehlen die "
      "Tavily-Rohantworten)",
      bool(spec_gap_queries) and "," in spec_gap_queries[0]
      and sum(1 for w in ("Generation", "Kraftstoff", "Antriebsart", "Getriebe")
              if w in spec_gap_queries[0]) >= 3,
      f"spec_gap_queries={spec_gap_queries!r}")

print()
if _FEHLER:
    print(f"FEHLER: {len(_FEHLER)} Check(s) fehlgeschlagen: {_FEHLER}")
    raise SystemExit(1)
print("ALLE REPARATURPAKET-CHECKS GRUEN")
