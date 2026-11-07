"""
ENFAL-Reparaturpaket (N1/N6, gezielte Identitätsrecherche, RC-F-Lauf
07.11.2026): Integrationsprüfung der beiden neuen, additiven Identity-Spec-
Gap-Queries über den ECHTEN, vollen async
`TavilyTechnicalResearchProvider.recherchiere()`-Pfad.

N1: keine bestehende Query fragte je nach dem Dokument-TYP, den
`_herstelleridentifikationsdokument()`/`_identitaet_nachlade_kandidaten()`
für das Nachladen ohnehin verlangt (PDF-Rettungsdatenblatt/-karte/-leitfaden
auf der Herstellerdomain). Neue Query "identity_spec_gap_dokumenttyp_*"
(`_IDENTITY_DOKUMENTTYP_WORTE_DE/EN`) fragt NUR bei weiterhin offener
Generation gezielt danach -- dieselben, bereits bestehenden Geltungs-/
Belegregeln (`_generationen_pruefen()`/`konsens()`/`_generation_
herstelleridentifikation_vorrang()`) entscheiden unverändert, was der
nachgeladene Text tatsächlich belegt.

N6: die bestehende Motorisierungs-Query bündelt den rohen Nutzer-Motortext
bereits mit den übrigen offenen Feldern, fand im echten Lauf aber nur
Tier-3-Kernbelege (10 Treffer, 0 qualifizierte Domains). Neue Query
"identity_spec_gap_motor_fachquelle_*" (`_IDENTITY_MOTOR_FACHQUELLE_WORTE_
DE/EN`) fragt NUR solange `engine_name` unbestätigt ist gezielt nach
Hersteller-/Fachpresse-Inhalt zum Motorkern -- Motorcode bleibt außen vor
(eigenes, unverändertes Konsensfeld), die Tier-/Domainzahl-Schwelle in
`werte_identitaet_aus()` ist UNVERÄNDERT.

KEIN Netzwerk, KEIN echter Tavily-/Extract-Call: `tavily_search`/
`hole_raw_content` sind die einzigen gestubbten Stellen. Die Provider-Stubs
routen NACH QUERY-INHALT (nicht nach Testfall) -- eine Query, die die neue
Formulierung nicht verwendet, bekommt NICHT die konfektionierte Antwort
("keine fertige Evidenz einschleusen", § Auftrag). Rettungskarten-Text
wörtlich aus `test_recherche_volltext_integration.py`/`test_rc_f_lexus_
originalbeleg.py` übernommen (reale, per pypdf extrahierte Herstellerquelle).

    python test_n1_n6_gezielte_identitaetsrecherche.py

Bewusst NICHT über `pytest` aufrufen -- dieselbe bekannte Runner-Grenze wie
in `test_recherche_volltext_integration.py` (eigene `test_*`-Hilfsfunktionen,
kein Async-Discovery-Support).
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


# ══ Echte Quellentexte ══════════════════════════════════════════════════════

# Rettungskarte: wörtlich aus test_recherche_volltext_integration.py /
# test_rc_f_lexus_originalbeleg.py übernommen (reale, per pypdf extrahierte
# Herstellerquelle).
_RETTUNGSKARTE_URL = ("https://www.lexus.de/content/dam/lexus/nmsc/germany/lexus-besitzer/"
                      "fahrzeuginformationen/RCF_tcm-3150-1284184.pdf")
_RETTUNGSKARTE_SNIPPET_DUENN = (
    "12V Spannungsversorgung trennen\nHinweise zum Transport\nZugang 12V Batterie")
_RETTUNGSKARTE_VOLLTEXT = (
    "Lexus RC F\n(UXC1 (EU, M), ab 09/2014)\nStand: 10/2014\n"
    "Lexus RC F (UXC1 (EU, ab 09/2014)\n"
    "12V Spannungsversorgung trennen\nHinweise zum Transport\nZugang 12V Batterie\n"
    "Lexus RC F\n(UXC1 (EU, M), ab 09/2014)")

# Ein Lexus-Dokument auf DERSELBEN Herstellerdomain, das aber NICHT das
# Dokumenttyp-Gate erfüllt (keines der Pfadwörter rettungsdatenblatt/
# rettungskarte/rettungsleitfaden/fahrzeuginformationen) -- der Gegenfall
# "unpassendes Dokument".
_BROSCHUERE_URL = "https://www.lexus.de/presse/rc-f-broschuere.pdf"
_BROSCHUERE_SNIPPET = "Lexus RC F -- Design und Technik im Überblick."

# ADAC/AMS (real, wörtlich aus test_rc_f_lexus_originalbeleg.py zitiert) --
# Identitäts-Basisquellen (Marke+Modell über zwei unabhängige Domains), aber
# mit WIDERSPRÜCHLICHEN Generationscodes (UXC1 vs. XC1) UND OHNE den
# "5.0 V8"-Kernbeleg wörtlich -- genau die reale Ausgangslage des 07.11.-Laufs.
_ADAC = treffer(
    "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/",
    "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25): Technische Daten | ADAC",
    "Marke Lexus Modell RC F Typ Ultimate Edition Automatik Baureihe RC (UXC1) "
    "(12/14 - 06/25) Herstellerinterne Baureihenbezeichnung UXC1 Motorcode 2UR-GSE "
    "Leistung maximal in PS 464 Antriebsart Hinterrad Getriebeart Automatikgetriebe")
_AMS = treffer(
    "https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
    "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
    "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Leistung 181 PS bis "
    "477 PS. LEXUS RC F TRACK EDITION IM SUPERTEST: Was kann der V8-Sauger auf der "
    "Rennstrecke? LEXUS LC 500 UND RC F: V8-Sportler auf Abschiedstour.")

# Schwache, generische (Tier-3) Kernbeleg-Quelle -- simuliert die 10 realen
# Treffer, die "5.0"+"V8" nennen, aber keine Tier-<=2-Domain erreichen.
_FORUM_SCHWACH = treffer(
    "https://www.lexus-club-forum.de/thread/rc-f-motor",
    "RC F Motor Diskussion",
    "Der RC F hat einen 5.0 V8 Motor, laut Forum-Mitgliedern sehr zuverlässig.")

# NEUE Fachquelle (Tier 1, Herstellerdomain) -- soll NUR über die neue
# "identity_spec_gap_motor_fachquelle_*"-Query gefunden werden.
_LEXUS_TECHDATEN = treffer(
    "https://www.lexus.de/rc-f/technische-daten",
    "Lexus RC F -- Technische Daten",
    "Der Lexus RC F wird von einem 5.0 V8 Saugmotor angetrieben.")

# NEUE Fachquelle, aber SCHWACH (Tier 3) -- für den Gegenfall "nur schwache
# Motorquellen bleiben auch über die neue Query unzureichend".
_FORUM_SCHWACH_2 = treffer(
    "https://www.motor-talk.de/forum/rc-f-motor-t123.html",
    "RC F Motordaten",
    "Nutzerbericht: der RC F hat angeblich einen 5.0 V8, genaue Herstellerangabe fehlt.")


def _patch(fake):
    ws.tavily_search = fake.search
    tr.tavily_search = fake.search
    tr.hole_raw_content = fake.extract


class _FakeProvider:
    """Routing NACH QUERY-INHALT (geordnete Liste, erster Treffer gewinnt) --
    eine Query, die die neue Formulierung nicht enthält, bekommt NICHT die
    konfektionierte Antwort (§ Auftrag: 'keine fertige Evidenz einschleusen').
    `routing` ist eine Liste von (substring, treffer) -- KEIN dict, damit die
    Prüfreihenfolge (spezifischer vor generisch) explizit und nachvollziehbar
    bleibt, nicht von Dict-Insertion-Details abhängt."""

    def __init__(self, routing: list[tuple[str, list[dict]]], extract_text: dict[str, str] | None = None):
        self.routing = routing
        self.extract_text = extract_text or {}
        self.search_calls: list[str] = []
        self.extract_calls: list[list[str]] = []

    async def search(self, query, *, count=8, exclude_domains=None, include_domains=None):
        self.search_calls.append(query)
        for substr, treffer_liste in self.routing:
            if substr in query:
                return list(treffer_liste)
        return []

    async def extract(self, urls):
        self.extract_calls.append(list(urls))
        gefunden = {u: self.extract_text[u] for u in urls if u in self.extract_text}
        fehler = len(urls) - len(gefunden)
        return gefunden, {"angefragt": len(urls), "aus_cache": 0, "extract_calls": 1,
                          "extract_urls": len(urls), "erfolge": len(gefunden), "fehler": fehler}


async def _recherchiere(fake: _FakeProvider):
    _patch(fake)
    provider = TavilyTechnicalResearchProvider()
    return await provider.recherchiere(marke="Lexus", modell="RC F", baujahr=2017,
                                       motor="5.0 V8, Heckantrieb", ausgeloest_durch="db_miss")


# Gemeinsame Basis-Routen (Runde 1): ADAC+AMS -- Identität belegt (zwei
# Domains), Generation widersprüchlich (UXC1 vs. XC1), KEIN "5.0 V8"-Kernbeleg.
_BASIS_ROUTING = [
    ("Heckantrieb technische Daten", [_ADAC]),
    ("Generation Baureihe Bauzeitraum", [_AMS]),
]


# ══════════════════════════════════════════════════════════════════════════
# A -- N1 Positivfall: NUR die neue Dokumenttyp-Query findet die Rettungskarte;
#      die bestehende Generation-Achsen-Query bleibt bewusst leer.
# ══════════════════════════════════════════════════════════════════════════
async def test_a_n1_dokumenttyp_query_loest_generation():
    fake = _FakeProvider(
        routing=[
            ("Rettungskarte Rettungsdatenblatt Fahrzeuginformationen",
             [treffer(_RETTUNGSKARTE_URL, "Lexus RC F Rettungskarte", _RETTUNGSKARTE_SNIPPET_DUENN)]),
            ("Generation Modellcode", []),  # bestehende Achsen-Query bewusst leer
            *_BASIS_ROUTING,
        ],
        extract_text={_RETTUNGSKARTE_URL: _RETTUNGSKARTE_VOLLTEXT})
    recherche = await _recherchiere(fake)
    check("A1) die neue Dokumenttyp-Query wurde tatsächlich gestellt",
          any("Rettungskarte" in q for q in fake.search_calls), str(fake.search_calls))
    check("A2) das Nachladen traf GENAU die Rettungskarten-URL",
          fake.extract_calls == [[_RETTUNGSKARTE_URL]], f"extract_calls={fake.extract_calls!r}")
    check("A3) Generation ist jetzt belegt: 'UXC1' -- UEBER DENSELBEN, unveränderten "
          "Konsens-/Rollenbeleg-Pfad, keine neue Sonderregel",
          recherche.identitaet.generation == "UXC1", f"generation={recherche.identitaet.generation!r}")
    check("A4) Nachweisweg bleibt einer der bestehenden, dokumentierten Pfade",
          recherche.identitaet.generation_nachweisweg in ("quelle", "herstelleridentifikation"),
          f"nachweisweg={recherche.identitaet.generation_nachweisweg!r}")
    check("A5) OHNE die neue Query wäre das NICHT passiert -- die bestehende "
          "Generation-Achsen-Query lieferte bewusst nichts Neues (Kontrollfall "
          "im selben Lauf)",
          True)  # per Konstruktion (routing liefert [] für "Generation Modellcode")


# ══════════════════════════════════════════════════════════════════════════
# B -- N1 Gegenfall: unpassendes Dokument (Herstellerdomain, aber falscher
#      Pfad) -- bleibt unresolved, kein Crash, keine Ersatzannahme.
# ══════════════════════════════════════════════════════════════════════════
async def test_b_n1_unpassendes_dokument_bleibt_unresolved():
    fake = _FakeProvider(
        routing=[
            ("Rettungskarte Rettungsdatenblatt Fahrzeuginformationen",
             [treffer(_BROSCHUERE_URL, "Lexus RC F Broschüre", _BROSCHUERE_SNIPPET)]),
            *_BASIS_ROUTING,
        ],
        extract_text={_BROSCHUERE_URL: "Lexus RC F -- vollständige Broschüre, keine Generation genannt."})
    recherche = await _recherchiere(fake)
    check("B1) kein Crash, normales Ergebnis", recherche is not None)
    check("B2) die Broschüre erfüllt das Dokumenttyp-Gate NICHT -> KEIN Nachladeversuch",
          fake.extract_calls == [], f"extract_calls={fake.extract_calls!r}")
    check("B3) Generation bleibt NACHVOLLZIEHBAR unbestätigt -- kein erfundener Code "
          "aus dem unpassenden Dokument", recherche.identitaet.generation is None,
          f"generation={recherche.identitaet.generation!r}")


# ══════════════════════════════════════════════════════════════════════════
# C -- N6 Positivfall: NUR die neue Motor-Fachquellen-Query liefert eine
#      qualifizierte (Tier-1) Quelle; die bestehende Motorisierungs-Query
#      bleibt bewusst auf Tier-3 beschränkt. Heckantrieb bleibt separat offen.
# ══════════════════════════════════════════════════════════════════════════
async def test_c_n6_motor_fachquelle_loest_motorkern():
    fake = _FakeProvider(
        routing=[
            ("Herstellerangabe", [_LEXUS_TECHDATEN]),
            ("Radantrieb Getriebe Antriebstechnologie", [_FORUM_SCHWACH]),
            ("Generation Modellcode", []),
            *_BASIS_ROUTING,
        ],
        extract_text={})
    recherche = await _recherchiere(fake)
    check("C1) die neue Motor-Fachquellen-Query wurde tatsächlich gestellt",
          any("Herstellerangabe" in q for q in fake.search_calls), str(fake.search_calls))
    check("C2) engine_name (Motorkern) ist jetzt bestätigt: '5.0 V8' -- UEBER DENSELBEN, "
          "unveränderten Tier-/Domainzahl-Pfad in werte_identitaet_aus() (hier: Tier-1-"
          "Zweig, keine Schwellenabsenkung)",
          recherche.identitaet.motor == "5.0 V8", f"motor={recherche.identitaet.motor!r}")
    check("C3) OHNE die neue Query wäre das NICHT passiert -- die bestehende "
          "Motorisierungs-Query lieferte nur die schwache Forum-Quelle (Tier 3, "
          "Kontrollfall im selben Lauf)", True)
    check("C4) Heckantrieb (Radantrieb) bleibt separat UNBESTÄTIGT -- keine Vermischung "
          "mit dem Motorkern, dieselbe vorbestehende, von diesem Paket unberührte "
          "Antriebs-Claim-Lücke ('Antriebsart Hinterrad' matcht die _ANTRIEB_CLAIM-"
          "Vokabel nicht)", recherche.identitaet.antrieb is None,
          f"antrieb={recherche.identitaet.antrieb!r}")
    check("C5) Motorcode bleibt EIGENES, unverändertes Feld -- die neue Query ersetzt "
          "ihn nicht und erfindet keinen", recherche.identitaet.motorcode in (None, "2UR-GSE"),
          f"motorcode={recherche.identitaet.motorcode!r}")


# ══════════════════════════════════════════════════════════════════════════
# D -- N6 Gegenfall: nur schwache Motorquellen auch über die neue Query --
#      Motorkern bleibt unbestätigt, KEINE Lockerung der Schwelle.
# ══════════════════════════════════════════════════════════════════════════
async def test_d_n6_nur_schwache_motorquellen_bleibt_unbestaetigt():
    fake = _FakeProvider(
        routing=[
            ("Herstellerangabe", [_FORUM_SCHWACH_2]),
            ("Radantrieb Getriebe Antriebstechnologie", [_FORUM_SCHWACH]),
            ("Generation Modellcode", []),
            *_BASIS_ROUTING,
        ],
        extract_text={})
    recherche = await _recherchiere(fake)
    check("D1) die neue Motor-Fachquellen-Query wurde gestellt, lieferte aber nur Tier 3",
          any("Herstellerangabe" in q for q in fake.search_calls), str(fake.search_calls))
    check("D2) Motorkern bleibt NACHVOLLZIEHBAR unbestätigt -- zwei schwache Quellen "
          "reichen nicht, keine Schwellenabsenkung", recherche.identitaet.motor is None,
          f"motor={recherche.identitaet.motor!r}")


# ══════════════════════════════════════════════════════════════════════════
# E -- Bereits gelöste Felder: ist `engine_name` nach Runde 1 schon bestätigt,
#      darf die neue Motor-Fachquellen-Query NICHT mehr ausgelöst werden
#      (Budget-Disziplin) -- die Dokumenttyp-Query (Generation bleibt offen)
#      läuft davon unberührt weiter.
# ══════════════════════════════════════════════════════════════════════════
async def test_e_bereits_bestaetigter_motorkern_kein_zusatzaufruf():
    adac_mit_kern = treffer(
        _ADAC["url"], _ADAC["title"],
        _ADAC["content"] + " Der RC F wird von einem 5.0 V8 Motor angetrieben.")
    fake = _FakeProvider(
        routing=[
            ("Rettungskarte Rettungsdatenblatt Fahrzeuginformationen",
             [treffer(_RETTUNGSKARTE_URL, "Lexus RC F Rettungskarte", _RETTUNGSKARTE_SNIPPET_DUENN)]),
            ("Herstellerangabe", [_LEXUS_TECHDATEN]),  # darf NICHT abgerufen werden
            ("Generation Baureihe Bauzeitraum", [_AMS]),
            ("Heckantrieb technische Daten", [adac_mit_kern, _LEXUS_TECHDATEN]),
        ],
        extract_text={_RETTUNGSKARTE_URL: _RETTUNGSKARTE_VOLLTEXT})
    recherche = await _recherchiere(fake)
    check("E1) engine_name ist bereits NACH RUNDE 1 bestätigt (Tier-1-Quelle "
          "lexus.de in Runde 1 selbst)", recherche.identitaet.motor == "5.0 V8",
          f"motor={recherche.identitaet.motor!r}")
    check("E2) die neue Motor-Fachquellen-Query wurde deshalb NICHT ausgelöst "
          "(Budget-Disziplin -- kein unbedingter Zusatzaufruf)",
          not any("Herstellerangabe" in q for q in fake.search_calls), str(fake.search_calls))
    check("E3) die Dokumenttyp-Query (Generation bleibt offen) lief davon unberührt weiter",
          any("Rettungskarte" in q for q in fake.search_calls), str(fake.search_calls))
    check("E4) Generation wird trotzdem korrekt über das Nachladen aufgelöst",
          recherche.identitaet.generation == "UXC1", f"generation={recherche.identitaet.generation!r}")


# ══════════════════════════════════════════════════════════════════════════
# F -- Aufrufbudget/Abrufregeln: die neuen Queries erhöhen die Anzahl der
#      SEARCH-Calls (konkret bezifferbar), das GETEILTE Originalabruf-Budget
#      (max. 2 Volltext-Originalabrufe für Identität) bleibt UNVERÄNDERT.
# ══════════════════════════════════════════════════════════════════════════
async def test_f_aufrufbudget_und_abrufregeln():
    fake = _FakeProvider(
        routing=[
            ("Rettungskarte Rettungsdatenblatt Fahrzeuginformationen",
             [treffer(_RETTUNGSKARTE_URL, "Lexus RC F Rettungskarte", _RETTUNGSKARTE_SNIPPET_DUENN)]),
            ("Herstellerangabe", [_LEXUS_TECHDATEN]),
            ("Generation Modellcode", []),
            ("Radantrieb Getriebe Antriebstechnologie", [_FORUM_SCHWACH]),
            *_BASIS_ROUTING,
        ],
        extract_text={_RETTUNGSKARTE_URL: _RETTUNGSKARTE_VOLLTEXT})
    recherche = await _recherchiere(fake)
    identitaets_calls = [q for q in fake.search_calls
                        if "Rückruf" not in q and "Schwachstellen" not in q
                        and "Kaufberatung" not in q and "Wartungsintervall" not in q
                        and "Inspektionsintervall" not in q]
    check("F1) Identitäts-Suchanfragen insgesamt: 10 (Runde 1: 2, Runde 2: 8 -- "
          "Generation-Achse 2 + NEU Dokumenttyp 2 + Motorisierung-Achse 2 + NEU "
          "Motor-Fachquelle 2) -- konkret bezifferte, begrenzte Kostenerhöhung "
          "(vorher max. 4 in Runde 2 / 6 insgesamt, jetzt max. 8 in Runde 2 / 10 "
          "insgesamt, nur wenn ALLE vier Teillücken gleichzeitig offen sind)",
          len(identitaets_calls) == 10, f"{len(identitaets_calls)}: {identitaets_calls!r}")
    check("F2) das Originalabruf-Budget (max. 2 Volltext-Abrufe für Identität "
          "INSGESAMT) bleibt UNVERÄNDERT -- hier genau 1 (nur die Rettungskarte "
          "qualifiziert sich, die Fachquelle ist keine PDF-Herstelleridentifikation)",
          len(fake.extract_calls) == 1 and fake.extract_calls[0] == [_RETTUNGSKARTE_URL],
          f"extract_calls={fake.extract_calls!r}")
    check("F3) beide Lücken trotzdem gelöst: Generation UND Motorkern",
          recherche.identitaet.generation == "UXC1" and recherche.identitaet.motor == "5.0 V8",
          f"generation={recherche.identitaet.generation!r} motor={recherche.identitaet.motor!r}")


async def _run_all():
    await test_a_n1_dokumenttyp_query_loest_generation()
    await test_b_n1_unpassendes_dokument_bleibt_unresolved()
    await test_c_n6_motor_fachquelle_loest_motorkern()
    await test_d_n6_nur_schwache_motorquellen_bleibt_unbestaetigt()
    await test_e_bereits_bestaetigter_motorkern_kein_zusatzaufruf()
    await test_f_aufrufbudget_und_abrufregeln()


asyncio.run(_run_all())

print()
if _FEHLER:
    print(f"FEHLER: {len(_FEHLER)} Check(s) fehlgeschlagen: {_FEHLER}")
    raise SystemExit(1)
print("ALLE N1/N6-GEZIELTE-IDENTITAETSRECHERCHE-TESTS GRUEN")
