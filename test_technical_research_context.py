"""
Context-preserving fact extraction (Release-Hardening, Root Cause 3).

BEFUND (Production Smoke 2, Mazda MX-5 Web-Fallback)
------------------------------------------------------
Ein AUTO-BILD-Satz ("Das gilt häufig auch für ein schief stehendes Lenkrad.")
wurde isoliert gegen `_PROBLEM_WORTE` ("häufig") und das Bauteil-Vokabular
("Lenkrad") geprüft und dadurch zu einem eigenständigen Risk "Lenkrad" mit
Priorität "Wichtig", Besichtigungsaktion und Verkäuferfrage — obwohl der Satz
sein eigenes Thema nicht selbst nennt, sondern auf einen vorhergehenden Satz
verweist, den die Analyse nie gelesen hat.

GENERISCHE REGEL (app/technical_research.py::_KONTEXTABHAENGIG,
`_kontext_aufloesen`): ein Satz, der mit einem Rückbezug beginnt ("Das gilt
...", "Dies betrifft ...", "Dabei kommt es ...", "Auch hier ...", "Dasselbe
gilt ...", "Dieses Problem ..."), wird NUR zusammen mit seinem unmittelbar
VORHERGEHENDEN Satz als Claim verwendet. Ohne auflösbaren Vorgänger (erster
Satz eines Treffers — bei Tavily-Snippets der Normalfall, wenn der Ausschnitt
mitten im Artikel beginnt —, oder der Vorgänger ist selbst nur ein Rückbezug)
bleibt der Satz unvollständig und erzeugt KEINEN Fakt.

Kein Wort über ein konkretes Bauteil, Fahrzeug oder die Marke steht in der
Regel selbst — Abschnitt C prüft denselben Mechanismus deshalb zusätzlich an
einem völlig anderen Bauteil (Kupplung statt Lenkrad), damit kein
Symptom-Patch für genau diesen einen Satz entsteht.

    python test_technical_research_context.py
"""
import re

from app.technical_research import _KONTEXTABHAENGIG, _extrahiere_fakten, _kontext_aufloesen

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def treffer(url: str, titel: str, inhalt: str) -> dict:
    return {"url": url, "title": titel, "content": inhalt}


# ══════════════════════════════════════════════════════════════════════════
print("\n=== A) Reproduktion: der reale AUTO-BILD-Satz, ohne auflösbaren Vorgänger ===")
# Tavily liefert oft nur ein Snippet, das mitten im Artikel beginnt — hier
# bewusst OHNE den (unbekannten) Satz, auf den "Das gilt ..." sich bezieht.
treffer_lenkrad_ohne_kontext = [
    treffer("https://www.autobild.de/mazda-mx-5-ratgeber", "",
            "Das gilt häufig auch für ein schief stehendes Lenkrad."),
]
abgelehnt_a: list[dict] = []
fakten_a = _extrahiere_fakten(treffer_lenkrad_ohne_kontext, "schwachstelle", abgelehnt=abgelehnt_a)
check("A1 kein eigenständiger 'Lenkrad'-Fakt ohne auflösbaren Vorgängersatz",
      not any((f.bauteil or "").lower().startswith("lenkrad") for f in fakten_a))
check("A2 ueberhaupt kein Fakt aus dem isolierten Fragment",
      len(fakten_a) == 0)
check("A3 Ablehnungsgrund dokumentiert den fehlenden Bezug",
      any(a.get("grund") == "kontextabhaengiger_satz_ohne_bezug" for a in abgelehnt_a))


# ══════════════════════════════════════════════════════════════════════════
print("\n=== B) Derselbe Rückbezug MIT auflösbarem Vorgängersatz -> vollständiger Claim ===")
treffer_lenkrad_mit_kontext = [
    treffer("https://www.autobild.de/mazda-mx-5-ratgeber", "",
            "Eine nicht richtig fixierte Lenksäule kann sich bei älteren Exemplaren lösen. "
            "Das gilt häufig auch für ein schief stehendes Lenkrad."),
]
abgelehnt_b: list[dict] = []
fakten_b = _extrahiere_fakten(treffer_lenkrad_mit_kontext, "schwachstelle", abgelehnt=abgelehnt_b)
check("B1 mit Vorgängersatz entsteht ein Fakt", len(fakten_b) == 1)
if fakten_b:
    check("B2 die Aussage enthält BEIDE Sätze (Kontext erhalten, nicht nur das Fragment)",
          "Lenksäule" in fakten_b[0].aussage and "Lenkrad" in fakten_b[0].aussage)


# ══════════════════════════════════════════════════════════════════════════
print("\n=== C) Generische Probe an einem VÖLLIG ANDEREN Bauteil (Kupplung) ===")
# Keine Lenkrad-/Mazda-Spezialregel: derselbe Mechanismus muss auch hier greifen.
treffer_kupplung_ohne_kontext = [
    treffer("https://www.adac.de/ratgeber-gebrauchtwagen", "",
            "Dies betrifft häufig auch eine ungewöhnlich schwergängige Kupplung."),
]
fakten_c1 = _extrahiere_fakten(treffer_kupplung_ohne_kontext, "schwachstelle")
check("C1 kein eigenständiger 'Kupplung'-Fakt ohne Vorgängersatz",
      len(fakten_c1) == 0)

treffer_kupplung_mit_kontext = [
    treffer("https://www.adac.de/ratgeber-gebrauchtwagen", "",
            "Ein verschlissenes Ausrücklager verursacht beim Kuppeln ein deutliches Knarzen. "
            "Dies betrifft häufig auch eine ungewöhnlich schwergängige Kupplung."),
]
fakten_c2 = _extrahiere_fakten(treffer_kupplung_mit_kontext, "schwachstelle")
check("C2 mit Vorgängersatz entsteht derselbe Mechanismus für ein anderes Bauteil",
      len(fakten_c2) == 1)
if fakten_c2:
    check("C3 Aussage enthält den Vorgängersatz (Ausrücklager)",
          "ausrücklager" in fakten_c2[0].aussage.lower())


# ══════════════════════════════════════════════════════════════════════════
print("\n=== D) Vollständiger, eigenständiger Satz bleibt unverändert nutzbar ===")
treffer_standalone = [
    treffer("https://www.autobild.de/mazda-mx-5-schwachstellen", "",
            "Der Verdeckmechanismus ist eine bekannte Schwachstelle und kann bei Nässe klemmen."),
]
fakten_d = _extrahiere_fakten(treffer_standalone, "schwachstelle")
check("D1 ein vollständiger Satz ohne Rückbezug erzeugt weiterhin einen Fakt",
      len(fakten_d) == 1)


# ══════════════════════════════════════════════════════════════════════════
print("\n=== E) _kontext_aufloesen: Grenzfälle direkt ===")
check("E1 erster Satz im Treffer, selbst ein Rückbezug -> None (kein Vorgänger)",
      _kontext_aufloesen(["Das gilt auch für die Dichtung."], 0) is None)
check("E2 Vorgänger ist SELBST nur ein Rückbezug -> keine Kette, None",
      _kontext_aufloesen(["Auch hier zeigt sich ein Problem.",
                          "Das gilt ebenfalls für die Dichtung."], 1) is None)
check("E3 normaler, nicht rückbezüglicher Satz bleibt unverändert",
      _kontext_aufloesen(["Ein ganz normaler Satz ohne Rückbezug steht hier."], 0)
      == "Ein ganz normaler Satz ohne Rückbezug steht hier.")
check("E4 Rückbezug mit normalem Vorgänger wird zusammengeführt",
      _kontext_aufloesen(["Der Motor zeigt gelegentlich Zündaussetzer.",
                          "Dasselbe gilt für den Nachfolgemotor."], 1)
      == "Der Motor zeigt gelegentlich Zündaussetzer. Dasselbe gilt für den Nachfolgemotor.")


# ══════════════════════════════════════════════════════════════════════════
print("\n=== F) Regex-Abdeckung der Rückbezugs-Varianten ===")
check("F1 erkennt die im Auftrag genannten Varianten",
      all(_KONTEXTABHAENGIG.match(s) for s in (
          "Das gilt ebenfalls für ein anderes Bauteil.",
          "Dies betrifft auch die Nachfolgegeneration.",
          "Dabei kommt es häufig zu Ausfällen.",
          "Auch hier zeigt sich derselbe Effekt.",
          "Dasselbe gilt für den Diesel.",
          "Dieses Problem tritt bei hoher Last auf.",
      )))
check("F2 lässt normale, eigenständige Sätze unberührt",
      not any(_KONTEXTABHAENGIG.match(s) for s in (
          "Der Motor hat einen bekannten Steuerkettenschaden.",
          "Diese Leistungsangabe bezieht sich auf den Basismotor.",
          "Die Kupplung gilt als robust und langlebig.",
      )))


# ══════════════════════════════════════════════════════════════════════════
print("\n=== G) VOLLE Pipeline: Production Smoke 2 (Mazda MX-5), echte run_kaufcheck() ===")
# Beweist den Fix nicht nur auf Extraktionsebene (A-D), sondern am Ende der
# gesamten Kette: kein "Lenkrad"-Risk, keine Besichtigungsaktion, keine
# Verkäuferfrage im fertigen Bericht. KEIN Netzwerk, KEIN echter LLM-Call
# (LLM/Markt gestubbt, wie test_e2e_web_fallback_root_cause.py).
import asyncio

import app.recall_filter as _rf
_rf.get_rueckruf_referenzen_kurz = lambda: []

import app.kaufcheck as kc
from app.models import KaufCheckRequest
from app.technical_research import FixtureTechnicalResearchProvider, recherchiere_technisch


async def _stub_gemini_g(system, user_msg):
    return {"risiko_evidence_ids": []}


async def _no_market_g(*a, **kw):
    return []


def _treffer_g(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


def _fixtures_smoke2():
    return {
        "identitaet": [
            _treffer_g("https://www.adac.de/mazda-mx-5-nd", "Mazda MX-5 ND (seit 2015)",
                      "Die 4. Generation des Mazda MX-5 (ND, seit 2015): 2.0 SKYACTIV-G mit "
                      "184 PS, Benziner, Hinterradantrieb."),
            _treffer_g("https://www.auto-motor-und-sport.de/mazda-mx-5-nd", "Mazda MX-5 ND im Test",
                      "Mazda MX-5 ND (seit 2015) mit 2.0 SKYACTIV-G und 184 PS, Heckantrieb."),
        ],
        # Exakt der reale Produktionsfall: der AUTO-BILD-Treffer liefert NUR den
        # rückbezüglichen Satz, der eigentliche Antezedens-Satz (worüber "Das
        # gilt..." überhaupt spricht) steht nicht im abgerufenen Ausschnitt —
        # der Normalfall bei Tavily-Snippets, die mitten im Artikel beginnen.
        "schwachstelle": [
            _treffer_g("https://www.autobild.de/mazda-mx-5-ratgeber",
                      "Mazda MX-5 ND Gebrauchtwagen-Ratgeber",
                      "Das gilt häufig auch für ein schief stehendes Lenkrad."),
        ],
        "rueckruf": [], "wartung": [],
    }


async def _run_smoke2():
    orig_recherchiere = kc.recherchiere_technisch
    orig_tavily = kc.tavily_search_with_fallback
    orig_gemini = kc.call_gemini_json
    provider = FixtureTechnicalResearchProvider(_fixtures_smoke2())

    async def _recherchiere(req, baureihe_roh, identitaet, baureihe_gegatet, motor_match):
        return await recherchiere_technisch(req, baureihe_roh, identitaet, baureihe_gegatet,
                                            motor_match, provider=provider)

    kc.recherchiere_technisch = _recherchiere
    kc.tavily_search_with_fallback = _no_market_g
    kc.call_gemini_json = _stub_gemini_g
    try:
        # Exakte Eingabe aus "Production Smoke 2" im Auftrag.
        req = KaufCheckRequest(
            marke="Mazda", modell="MX-5", baujahr=2019, kilometerstand=58_700,
            motor="2.0 SKYACTIV-G", kraftstoff="Benzin", leistung_ps=184,
            getriebe="Schaltgetriebe", preis_eur=22_900, unfallfrei="",
            vorbesitzer=2, tuev_bis="07/2027", verkaeuferart="haendler",
            servicehistorie="vollstaendig",
            beschreibung="Servicehistorie vollständig vorhanden. Tuning/Leistungssteigerung "
                        "nicht angegeben.")
        return await kc.run_kaufcheck(req)
    finally:
        kc.recherchiere_technisch = orig_recherchiere
        kc.tavily_search_with_fallback = orig_tavily
        kc.call_gemini_json = orig_gemini


_res_g = asyncio.run(_run_smoke2())
check("G1 kein Insight mit Bauteil 'Lenkrad'",
      not any((getattr(i, "bauteil", None) or "").lower().startswith("lenkrad")
              for i in _res_g["insights"]))
# "Lenkrad" taucht legitim im generischen Besichtigungs-Grundkatalog auf
# ("Innenraum-Verschleiß... Lenkrad, Schaltknauf, Pedalgummis ansehen") — das
# ist KEIN Web-Fakt und keine Eskalation, bleibt also erlaubt. Verboten ist
# die isolierte Behauptung selbst (der unvollständige Satz als Claim) und ihr
# Titel-Template als Web-Hinweis.
check("G2 die unvollständige Behauptung selbst ('schief stehendes Lenkrad') "
      "erscheint NICHT im Bericht",
      "schief stehendes lenkrad" not in _res_g["bericht"].lower())
check("G2b kein Web-Hinweis-Titel nennt 'Lenkrad'",
      not re.search(r"lenkrad[^\n]*webrecherche|webrecherche[^\n]*lenkrad",
                    _res_g["bericht"], re.IGNORECASE))
check("G3 die Web-Identität wurde trotzdem aufgelöst (Fix betrifft nur den einen "
      "unvollständigen Satz, nicht die übrige Recherche)",
      (_res_g["vehicle_identity"].get("fuel") or "").lower() == "benzin")


print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE CONTEXT-EXTRACTION-TESTS GRUEN")
