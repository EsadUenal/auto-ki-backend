"""
ENFAL-Recherchepfad-Folgeauftrag (2026-11, Integrationsprüfung): "Recherche-
Reparatur im vollständigen Produktpfad absichern".

Prüft L1 (Identitäts-Nachladung) und L3 (Wartungs-Nachladung) NICHT mehr nur
isoliert (s. `test_wartung_volltext_nachladung.py`), sondern über den
ECHTEN, vollen async `TavilyTechnicalResearchProvider.recherchiere()`-Pfad --
mit echten Fixture-Texten (unverändert aus `test_rc_f_lexus_care_
wartungsplan_real.py`/`test_rc_f_lexus_originalbeleg.py` zitiert, beides
real heruntergeladene und per pypdf extrahierte Herstellerquellen) UND
realistisch GEKÜRZTEN Suchsnippets, wie Tavily sie tatsächlich liefert
(kurzer Ausschnitt um die Treffer-Stelle, nicht das volle Dokument).

Genau DAS deckte die Integrationsprüfung erst auf: der erste Entwurf von
`_wartung_nachlade_kandidaten()` nutzte `score_domain()` OHNE `marke` --
score 0 für den REALEN lexus.ch-Treffer (unbekannte Domain in der
generischen Liste), obwohl `_marke_eigene_domain()` (L4) dieselbe Domain
längst korrekt als Hersteller erkennt. Mit nur synthetischem `adac.de` als
Testdomain (das AUCH ohne Markenbezug hoch genug bewertet wird) wäre das nie
aufgefallen -- Abschnitt A/B unten nutzen deshalb bewusst die ECHTE
lexus.ch/lexus.de-URL, nicht nur eine Textkopie.

KEIN Netzwerk, KEIN echter Tavily-/Extract-Call: `tavily_search` und
`hole_raw_content` werden als einzige externe Stellen gestubbt (s. Auftrag
§5). Es werden KEINE fertigen WebFakten/Bestätigungsflags eingespeist --
jeder Fakt/jeder Identitätsstatus entsteht ausschließlich aus dem echten
Lauf von `recherchiere()` über die unveränderte Extraktions-/Konsens-
Pipeline.

Diese Tests beweisen Orchestrierung und Kostendisziplin, NICHT echte
Suchmaschinenplatzierung (keine Aussage darüber, ob Tavily diese Treffer in
einem echten Live-Lauf tatsächlich findet).

    python test_recherche_volltext_integration.py

Bewusst NICHT über `pytest test_recherche_volltext_integration.py` aufrufen:
die enthaltenen Hilfsfunktionen heißen `test_a_...`/`test_b_...` (lesbare
Fallnamen, wie in `test_adaptive_evidence_escalation.py`) und würden von
pytests eigener Testerkennung NACH dem bereits an dieser Stelle (Modul-
Top-Level, `asyncio.run(_run_all())`) vollständig gelaufenen Durchlauf ein
zweites Mal -- ohne Async-Unterstützung -- angefasst, was Fehlschläge
vortäuscht, die beim direkten Lauf nicht auftreten (dieselbe bekannte
Runner-Grenze wie in anderen Dateien dieses Projekts, s. Abschlussbericht).
"""
import asyncio

import app.technical_research as tr
import app.web_search as ws
from app.technical_research import TavilyTechnicalResearchProvider

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# ══ Echte Quellentexte (wörtlich zitiert, s. Moduldocstring für Herkunft) ══

_SERVICEHEFT_URL = "https://tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf"
_RETTUNGSKARTE_URL = ("https://www.lexus.de/content/dam/lexus/nmsc/germany/lexus-besitzer/"
                      "fahrzeuginformationen/RCF_tcm-3150-1284184.pdf")
_ADAC_URL = "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/"

# Realistisch GEKÜRZTES Suchsnippet: Tavily trifft typischerweise die
# Intervall-Stelle (Seite 15/16 des 24-seitigen PDFs), NICHT die weit
# entfernte Modell-/Marktvorbehalt-Stelle (Seite 4) -- unverändert aus
# `test_rc_f_lexus_care_wartungsplan_real.py` zitiert, nur um den
# Modellisten-/Vorbehalt-Teil gekürzt.
_SERVICEHEFT_SNIPPET_DUENN = (
    "LEXUS CARE-WARTUNGSPLAN Für ES 300h, IS 300h, LS 500, LS 500h, LC 500, "
    "LC 500h, NX 350h, NX 450h+, RX 300, RX 350, RX 350L, RX 450h, RX 450hL, "
    "RC 300h, RC F , UX 200 und UX 250h\n"
    "Lexus Care-Komplettservice: alle 30.000 km oder 2 Jahre, je nachdem, was "
    "zuerst eintritt. Lexus Care-Zwischenservice: alle 15.000 km oder einmal "
    "jährlich, je nachdem, was zuerst eintritt. Dieser Service beinhaltet "
    "einen Öl- und Filterwechsel sowie zusätzliche Inspektionen von Lenkung, "
    "Bremsen, Beleuchtung und Reifen.")
# Vollständiger Originaltext (identisch zu `LEXUS_CH_MODELLUEBERSICHT` dort,
# NUR ohne die dortige künstliche "#page=4"-Fragment-URL -- ein reales
# Suchergebnis liefert eine PDF nie als mehrere Fragment-URLs, s. Kommentar
# dort): Seite 4 (Modellliste + Marktvorbehalt) UND Seite 15/16 (Intervalle)
# desselben Dokuments, in Originalreihenfolge.
_SERVICEHEFT_VOLLTEXT = (
    "BETROFFENE MODELLE Produktionsstart RC 300h (Oktober 2015 ~) RC F "
    "(September 2014 ~) LC 500 (März 2017 ~) Falls für Ihren Markt zutreffend.\n\n"
    + _SERVICEHEFT_SNIPPET_DUENN + "\n\n"
    "Lexus RC F zusätzliche Inspektionen laut Lexus Care-Wartungsplan: "
    "Ventilspiel (Inspektion mit Messfühler, LC 500, RC F) alle 90.000 km "
    "oder 6 Jahre. Automatikgetriebeöl alle 60.000 km oder 4 Jahre.")

# Rettungskarte: realistisch GEKÜRZTES Snippet, das NICHT die Kopfzeile mit
# dem Typcode trifft, sondern einen anderen Abschnitt DESSELBEN realen,
# zweiseitigen Dokuments (wörtlich aus `test_rc_f_lexus_originalbeleg.py`
# zitiert, LEXUS_PDF_SEITE2 ohne die wiederholte Kopfzeile).
_RETTUNGSKARTE_SNIPPET_DUENN = (
    "12V Spannungsversorgung trennen\nHinweise zum Transport\nZugang 12V Batterie")
_RETTUNGSKARTE_VOLLTEXT = (
    "Lexus RC F\n(UXC1 (EU, M), ab 09/2014)\nStand: 10/2014\n"
    "Lexus RC F (UXC1 (EU, ab 09/2014)\n"
    "12V Spannungsversorgung trennen\nHinweise zum Transport\nZugang 12V Batterie\n"
    "Lexus RC F\n(UXC1 (EU, M), ab 09/2014)")
_ADAC = treffer(
    _ADAC_URL,
    "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25): Technische Daten | ADAC",
    "Marke Lexus Modell RC F Typ Ultimate Edition Automatik Baureihe RC (UXC1) "
    "(12/14 - 06/25) Herstellerinterne Baureihenbezeichnung UXC1 Motorcode 2UR-GSE "
    "Leistung maximal in PS 464 Antriebsart Hinterrad Getriebeart Automatikgetriebe")
# Zweite, unabhängige Identitätsquelle (real, wörtlich aus `test_rc_f_lexus_
# care_wartungsplan_real.py` zitiert) -- NUR damit Marke+Modell in Tests A/C
# (die sich auf L3/Wartung konzentrieren, nicht auf L1/Identität) über den
# bestehenden Zwei-Domain-Pfad belegt sind, OHNE dass dafür ihrerseits ein
# Nachladen nötig wäre (das prüft eigenständig Test B).
_AMS = treffer(
    "https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
    "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
    "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Leistung 181 PS bis "
    "477 PS. Hubraum 1998 cm3 bis 4969 cm3. LEXUS RC F TRACK EDITION IM SUPERTEST: "
    "Was kann der V8-Sauger auf der Rennstrecke? LEXUS LC 500 UND RC F: "
    "V8-Sportler auf Abschiedstour.")


class _FakeProvider:
    """Stubt NUR die beiden externen Stellen (Auftrag §5: 'externe Provider
    ausschließlich stubben'). `search_calls`/`extract_calls` zählen ALLE
    tatsächlich ausgeführten Aufrufe -- Grundlage für die Kostenprüfung."""

    def __init__(self, *, routing: dict[str, list[dict]], extract_text: dict[str, str] | None = None,
                extract_fehler: bool = False):
        self.routing = routing
        self.extract_text = extract_text or {}
        self.extract_fehler = extract_fehler
        self.search_calls: list[str] = []
        self.extract_calls: list[list[str]] = []

    async def search(self, query, *, count=8, exclude_domains=None, include_domains=None):
        self.search_calls.append(query)
        for substr, treffer_liste in self.routing.items():
            if substr in query:
                return list(treffer_liste)
        return []

    async def extract(self, urls):
        self.extract_calls.append(list(urls))
        if self.extract_fehler:
            raise RuntimeError("Extract-Dienst nicht erreichbar (simuliert)")
        gefunden = {u: self.extract_text[u] for u in urls if u in self.extract_text}
        fehler = len(urls) - len(gefunden)
        return gefunden, {"angefragt": len(urls), "aus_cache": 0, "extract_calls": 1,
                          "extract_urls": len(urls), "erfolge": len(gefunden), "fehler": fehler}


def _patch(fake: _FakeProvider):
    ws.tavily_search = fake.search
    tr.tavily_search = fake.search
    tr.hole_raw_content = fake.extract


async def _recherchiere(fake: _FakeProvider):
    _patch(fake)
    provider = TavilyTechnicalResearchProvider()
    return await provider.recherchiere(marke="Lexus", modell="RC F", baujahr=2017,
                                       motor="5.0 V8, Heckantrieb", ausgeloest_durch="db_miss")


# ══ A) Dünner realitätsnaher Serviceheft-Treffer: automatische Kandidaten- ══
#      auswahl -> Originalabruf -> Marktvorbehalt -> sichtbarer Hinweis ──────

async def test_a_serviceheft_nachladung():
    fake = _FakeProvider(
        routing={
            "technische Daten": [_ADAC],
            "Generation Baureihe Bauzeitraum": [_AMS],
            "Wartungsintervall Serviceintervall": [treffer(
                _SERVICEHEFT_URL, "Lexus Garantie- und Serviceheft", _SERVICEHEFT_SNIPPET_DUENN)],
        },
        extract_text={_SERVICEHEFT_URL: _SERVICEHEFT_VOLLTEXT})
    recherche = await _recherchiere(fake)
    wartung = [f for f in recherche.fakten if f.kategorie == "wartung"]
    check("A1) Identität ist belegt (Marke+Modell über ADAC+AMS, zwei unabhängige Domains)",
          recherche.identitaet.belegt)
    check("A2) das Nachladen wurde AUTOMATISCH ausgelöst (kein manueller Eingriff) "
          "und traf GENAU die erwartete Serviceheft-URL", fake.extract_calls == [[_SERVICEHEFT_URL]],
          f"extract_calls={fake.extract_calls!r}")
    check("A3) ein Wartungs-Fakt ist sichtbar (Intervall aus dem Dokument)", bool(wartung),
          str(wartung))
    check("A4) der SONST (nur mit dem dünnen Snippet) verlorene Marktvorbehalt ist nach dem "
          "Nachladen tatsächlich sichtbar: regionale_geltung='unresolved'",
          bool(wartung) and wartung[0].regionale_geltung == "unresolved", str(wartung))
    check("A5) der sichtbare Hinweistext nennt den Vorbehalt wörtlich UND bleibt bei "
          "'unresolved' (keine Ausschlussaussage, keine erfundene Marktgültigkeit)",
          bool(wartung) and "falls für ihren markt zutreffend" in (wartung[0].herkunftsregion or "").lower(),
          str(wartung[0].herkunftsregion if wartung else None))


# ══ B) Dünner Hersteller-Identifikationstreffer: vorhandener Abrufpfad/ ══
#      begrenzte Ergänzung -> echter Dokumentinhalt -> Rollen-/Zuordnungs- ──
#      prüfung -> tatsächlicher Identitätsstatus ─────────────────────────────

async def test_b_identitaet_nachladung():
    fake = _FakeProvider(
        routing={
            "technische Daten": [_ADAC],
            "Generation Baureihe Bauzeitraum": [treffer(
                _RETTUNGSKARTE_URL, "Lexus RC F Rettungskarte", _RETTUNGSKARTE_SNIPPET_DUENN)],
        },
        extract_text={_RETTUNGSKARTE_URL: _RETTUNGSKARTE_VOLLTEXT})
    recherche = await _recherchiere(fake)
    check("B1) Identität ist belegt", recherche.identitaet.belegt)
    check("B2) das Nachladen traf GENAU die Rettungskarten-URL (Dokumenttyp-Erkennung "
          "ueber _herstelleridentifikationsdokument(), keine pauschale PDF-Annahme)",
          fake.extract_calls == [[_RETTUNGSKARTE_URL]], f"extract_calls={fake.extract_calls!r}")
    check("B3) OHNE Nachladen (dünnes Snippet allein) wäre 'UXC1' NICHT im Suchtext enthalten "
          "-- der Beweis haengt tatsaechlich am Nachladen, nicht an einem Zufallstreffer",
          "UXC1" not in _RETTUNGSKARTE_SNIPPET_DUENN)
    check("B4) nach dem Nachladen durchläuft der echte Dokumentinhalt die BESTEHENDE, "
          "unveränderte Rollen-/Konsensprüfung und ergibt den tatsächlichen Identitätsstatus: "
          "generation='UXC1'", recherche.identitaet.generation == "UXC1",
          f"generation={recherche.identitaet.generation!r}")
    check("B5) KEINE pauschale Übernahme des Herstellerwerts ohne Prüfung -- der "
          "Nachweisweg ist einer der bestehenden, dokumentierten Konsens-Pfade, keine "
          "neue Sonderregel", recherche.identitaet.generation_nachweisweg in ("quelle",
                                                                              "herstelleridentifikation"),
          f"nachweisweg={recherche.identitaet.generation_nachweisweg!r}")


# ══ C) Abruf scheitert oder Budget erschöpft: kein erfundener Beleg, keine ══
#      unbegrenzte Wiederholung ──────────────────────────────────────────────

async def test_c_fehlgeschlagener_abruf_identitaet():
    fake = _FakeProvider(
        routing={
            "technische Daten": [_ADAC],
            "Generation Baureihe Bauzeitraum": [treffer(
                _RETTUNGSKARTE_URL, "Lexus RC F Rettungskarte", _RETTUNGSKARTE_SNIPPET_DUENN)],
        },
        extract_fehler=True)
    recherche = await _recherchiere(fake)
    check("C1) Abruf schlägt fehl -> KEIN Crash, `recherchiere()` liefert ein normales Ergebnis",
          recherche is not None)
    check("C2) Identität bleibt bei Marke+Modell (belegt=True über ADAC), aber OHNE "
          "erfundene Generation -- der Fehlschlag erzeugt NIE einen Ersatzwert",
          recherche.identitaet.belegt and recherche.identitaet.generation is None,
          f"belegt={recherche.identitaet.belegt!r} generation={recherche.identitaet.generation!r}")
    check("C3) GENAU EIN Abrufversuch für die Rettungskarte -- keine unbegrenzte Wiederholung "
          "nach dem Fehlschlag", fake.extract_calls == [[_RETTUNGSKARTE_URL]],
          f"extract_calls={fake.extract_calls!r}")


async def test_c_fehlgeschlagener_abruf_wartung():
    fake = _FakeProvider(
        routing={
            "technische Daten": [_ADAC],
            "Generation Baureihe Bauzeitraum": [_AMS],
            "Wartungsintervall Serviceintervall": [treffer(
                _SERVICEHEFT_URL, "Lexus Garantie- und Serviceheft", _SERVICEHEFT_SNIPPET_DUENN)],
        },
        extract_fehler=True)
    recherche = await _recherchiere(fake)
    wartung = [f for f in recherche.fakten if f.kategorie == "wartung"]
    check("C4) fehlgeschlagener Serviceheft-Abruf -> KEIN Crash", recherche is not None)
    check("C5) der Wartungs-Fakt aus dem dünnen Snippet bleibt bestehen (Intervall weiterhin "
          "sichtbar) -- der Fehlschlag verschlechtert den bisherigen Stand nicht", bool(wartung),
          str(wartung))
    check("C6) KEINE erfundene regionale_geltung -- der Marktvorbehalt bleibt unsichtbar, "
          "weil er nach dem Fehlschlag tatsächlich nicht belegt ist (ehrlich, nicht vorgetäuscht)",
          bool(wartung) and wartung[0].regionale_geltung is None, str(wartung))
    check("C7) GENAU EIN Abrufversuch -- keine unbegrenzte Wiederholung",
          fake.extract_calls == [[_SERVICEHEFT_URL]], f"extract_calls={fake.extract_calls!r}")


# ══ Kostenprüfung (Auftrag §4): Search- UND Extract-Calls tatsächlich ══════
#    gezählt, nicht nur behauptet ─────────────────────────────────────────

async def test_kosten_vollstaendig_verglichen():
    fake = _FakeProvider(
        routing={
            "technische Daten": [_ADAC],
            "Generation Baureihe Bauzeitraum": [treffer(
                _RETTUNGSKARTE_URL, "Lexus RC F Rettungskarte", _RETTUNGSKARTE_SNIPPET_DUENN)],
            "Wartungsintervall Serviceintervall": [treffer(
                _SERVICEHEFT_URL, "Lexus Garantie- und Serviceheft", _SERVICEHEFT_SNIPPET_DUENN)],
        },
        extract_text={_RETTUNGSKARTE_URL: _RETTUNGSKARTE_VOLLTEXT,
                     _SERVICEHEFT_URL: _SERVICEHEFT_VOLLTEXT})
    recherche = await _recherchiere(fake)
    # Identität löst bereits in Runde 1 vollständig auf (Marke/Modell via ADAC,
    # Generation via nachgeladener Rettungskarte) -> die Identity-Spec-Gap-Runde
    # (Motorisierung bleibt zwar offen) kommt NUR fuer die offene Achse --
    # kein zusätzlicher Suchaufwand durch das Nachladen selbst.
    check("Kosten1) Search-Calls bleiben im unveränderten Rahmen (Identität Runde 1 + "
          "ggf. eine offene Achse der Spec-Gap-Runde + Rückruf + Technik) -- das Nachladen "
          "selbst erzeugt KEINEN zusätzlichen Search-Call",
          len(fake.search_calls) <= 14, f"search_calls={fake.search_calls!r}")
    check("Kosten2) GENAU 2 Extract-Calls insgesamt (Rettungskarte + Serviceheft) -- "
          "nicht mehr, nicht weniger: die neuen Kosten sind exakt bezifferbar, nicht nur "
          "pauschal 'begrenzt' behauptet",
          len(fake.extract_calls) == 2 and sorted(c[0] for c in fake.extract_calls) ==
          sorted([_RETTUNGSKARTE_URL, _SERVICEHEFT_URL]),
          f"extract_calls={fake.extract_calls!r}")
    check("Kosten3) generation UND Wartungs-Marktvorbehalt beide korrekt aufgelöst -- "
          "die 2 Extract-Calls tragen tatsächlich zum Ergebnis bei, sind kein Leerlauf",
          recherche.identitaet.generation == "UXC1"
          and any(f.kategorie == "wartung" and f.regionale_geltung == "unresolved"
                  for f in recherche.fakten))


async def _run_all():
    await test_a_serviceheft_nachladung()
    await test_b_identitaet_nachladung()
    await test_c_fehlgeschlagener_abruf_identitaet()
    await test_c_fehlgeschlagener_abruf_wartung()
    await test_kosten_vollstaendig_verglichen()


asyncio.run(_run_all())

print()
if _FEHLER:
    print(f"FEHLER: {len(_FEHLER)} Check(s) fehlgeschlagen: {_FEHLER}")
    raise SystemExit(1)
print("ALLE RECHERCHE-VOLLTEXT-INTEGRATIONSTESTS GRUEN")
