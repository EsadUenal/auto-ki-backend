"""
Reparatur: Rückruf-Doppelzählung im Web-Fallback (Fortsetzungsauftrag).

BEFUND (lokale Abnahme, reproduziert über `_extrahiere_fakten`, kategorie=
"rueckruf"): zwei Bauteilbegriffe DERSELBEN Rückrufmeldung und DERSELBEN
Quell-URL ("Kraftstoffpumpe" UND "Pulsationsdämpfer", real vorkommendes
Fehlerbild -- ein defekter Pulsationsdämpfer in der Kraftstoffpumpe führt zu
deren Ausfall) erzeugten vorher ZWEI eigenständige WebFakt-/Insight-Objekte,
weil `_extrahiere_fakten()` für "rueckruf" ausschließlich über den
Bauteil-Vokabularschlüssel dedupte -- unabhängig davon, ob beide Sätze
dieselbe Aktion beschreiben.

FIX (app/technical_research.py, `_rueckruf_buendel_ziel`): ein zweiter
Bauteilbegriff derselben Quell-URL wird nur dann in den bestehenden
Kandidaten gebündelt, wenn er NACHWEISLICH dieselbe Rückrufmeldung
beschreibt -- über dieselbe, bereits bestehende und für den
Cross-Source-Vergleich genutzte Ereignisgleichheit
(`app.recall_event_matching.gleiches_rueckruf_ereignis`, konservative
Mindest-Token-Überlappung). Weder "gleiche URL" noch "gleiches Bauteil"
allein genügt; bei fehlender Überlappung bleibt es bei getrennten Fakten
(Fall B unten) -- keine spekulative Zusammenführung.

    python scripts/test_isolated.py test_rueckruf_dedupe_webfallback.py
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import kaufcheck as kc
from app.models import KaufCheckRequest, KaufCheckResponse
from app.technical_research import (
    FixtureTechnicalResearchProvider, _extrahiere_fakten, recherchiere_technisch,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


# ── Fall A: EINE Aktion, zwei Bauteilbegriffe derselben Defektkette ────────
# "Kraftstoffpumpe" (Vokabular-Treffer) und "Pulsationsdämpfer" (Vokabular-
# Treffer "daempfer" -> Fahrwerksgruppe) landen ohne Fix auf zwei
# verschiedenen Schlüsseln, obwohl derselbe Satzzusammenhang ("wodurch der
# Motor während der Fahrt ausgehen kann") beide eindeutig als EINE Meldung
# ausweist.
_TREFFER_A = [{
    "url": "https://www.kba.de/rueckrufe/lexus-kraftstoffpumpe",
    "title": "Rückruf Lexus RC F: Kraftstoffpumpe",
    "content": (
        "Rückruf für den Lexus RC F: Fahrzeuge der Baujahre 2015 bis 2017, deren "
        "Kraftstoffpumpe ausfallen kann, wodurch der Motor während der Fahrt ausgehen "
        "kann. Der Rückruf nennt als Ursache einen defekten Pulsationsdämpfer in der "
        "Kraftstoffpumpe, wodurch der Motor während der Fahrt ausgehen kann."),
}]
_abgelehnt_a: list[dict] = []
_fakten_a = _extrahiere_fakten(_TREFFER_A, kategorie="rueckruf", marke="Lexus", modell="RC F",
                               baujahr=2017, generation=None, abgelehnt=_abgelehnt_a)

check("A1: genau EIN WebFakt für die eine Rückrufaktion (vorher: zwei)",
      len(_fakten_a) == 1, str([f.bauteil for f in _fakten_a]))
if _fakten_a:
    check("A2: beide Bauteilbegriffe bleiben im gebündelten Fakt sichtbar (keiner geht verloren)",
          "Kraftstoffpumpe" in _fakten_a[0].bauteil and "Pulsationsd" in _fakten_a[0].bauteil,
          repr(_fakten_a[0].bauteil))
    check("A3: Quellen/Evidence bleiben erhalten (mindestens eine Quelle zitiert)",
          len(_fakten_a[0].quellen) >= 1)


# ── Fall B: ZWEI eigenständige Aktionen auf derselben Seite/URL ────────────
# Gleiche URL, aber inhaltlich unabhängige Meldungen (Kraftstoffpumpe vs.
# Bremsleitung, kein gemeinsamer Satzzusammenhang) -- müssen GETRENNT
# bleiben. Reine URL-Gleichheit darf niemals allein zur Bündelung führen.
_TREFFER_B = [{
    "url": "https://www.kba.de/rueckrufe/lexus-zwei-aktionen",
    "title": "Rückrufe Lexus RC F",
    "content": (
        "Rückruf 1 für den Lexus RC F: Fahrzeuge, deren Kraftstoffpumpe ausfallen kann, "
        "wodurch der Motor während der Fahrt ausgehen kann. Rückruf 2 für den Lexus RC F: "
        "unabhängig vom ersten Rückruf können Scheuerstellen an der Bremsleitung zu "
        "Undichtigkeit führen."),
}]
_abgelehnt_b: list[dict] = []
_fakten_b = _extrahiere_fakten(_TREFFER_B, kategorie="rueckruf", marke="Lexus", modell="RC F",
                               baujahr=2017, generation=None, abgelehnt=_abgelehnt_b)

check("B1: zwei eigenständige Aktionen bleiben ZWEI WebFakten (keine Übermergung)",
      len(_fakten_b) == 2, str([f.bauteil for f in _fakten_b]))
check("B2: beide Bauteile sind einzeln vertreten, keiner wurde mit dem anderen verschmolzen",
      {f.bauteil for f in _fakten_b} == {"Kraftstoffpumpe", "Bremsleitung"},
      str([f.bauteil for f in _fakten_b]))


# ── Fall C: dieselbe Aktion, aber aus ZWEI verschiedenen URLs ──────────────
# Bündelung ist strikt auf dieselbe Quell-URL beschränkt (Fortsetzungsauftrag:
# "gleiche URL ... allein reicht nicht" heißt nicht "URL spielt keine Rolle"
# -- ohne Bezug zur selben Seite bleibt eine Ereignisgleichheit über Quellen
# hinweg weiterhin Sache des bestehenden Cross-Source-Pfads, nicht dieser
# Innerhalb-einer-Quelle-Bündelung).
_TREFFER_C = [
    {"url": "https://www.kba.de/rueckrufe/lexus-kraftstoffpumpe-a",
     "title": "Rückruf Lexus RC F: Kraftstoffpumpe",
     "content": ("Rückruf für den Lexus RC F: Fahrzeuge der Baujahre 2015 bis 2017, deren "
                 "Kraftstoffpumpe ausfallen kann, wodurch der Motor während der Fahrt ausgehen "
                 "kann.")},
    {"url": "https://www.autobild.de/rueckrufe/lexus-pulsationsdaempfer-b",
     "title": "Rückruf Lexus RC F: Pulsationsdämpfer",
     "content": ("Der Rückruf für Fahrzeuge der Baujahre 2015 bis 2017 nennt als Ursache einen "
                 "defekten Pulsationsdämpfer in der Kraftstoffpumpe, wodurch der Motor während "
                 "der Fahrt ausgehen kann.")},
]
_abgelehnt_c: list[dict] = []
_fakten_c = _extrahiere_fakten(_TREFFER_C, kategorie="rueckruf", marke="Lexus", modell="RC F",
                               baujahr=2017, generation=None, abgelehnt=_abgelehnt_c)
check("C1: zwei verschiedene Quell-URLs bündeln NICHT über diesen Pfad "
      "(bleibt Sache des bestehenden Cross-Source-Vergleichs)",
      len(_fakten_c) == 2, str([(f.bauteil, [q.url for q in f.quellen]) for f in _fakten_c]))


# ── Fall D: End-to-End über run_kaufcheck -- Bericht, Zählung, ──────────────
# Verkäuferfragen und Dokumentenanforderungen bündeln sich mit der Aktion
# selbst (ein WebFakt -> ein Insight -> je EINE Verkäuferfrage/Dokumenten-
# anforderung aus app/kaufaktionen.py, nicht zwei).
async def _lauf(req: KaufCheckRequest, web_fixture: dict) -> KaufCheckResponse:
    orig = (kc.call_gemini_json, kc.recherchiere_technisch, kc.tavily_search_with_fallback,
            kc.marktpreis_recherche_moeglich, kc.vertiefe_marktrecherche)

    async def gemini(system, user):
        return {"bericht": "KAUFEN.", "empfehlung": "kaufen",
                "risiko_evidence_ids": [], "empfehlung_evidence_ids": []}

    async def verboten(*a, **k):
        raise AssertionError("Marktprovider darf nicht laufen")

    prov = FixtureTechnicalResearchProvider(web_fixture)

    async def technik(r, br, info, brg, mm, provider=None):
        return await recherchiere_technisch(r, br, info, brg, mm, provider=prov)

    kc.call_gemini_json, kc.recherchiere_technisch = gemini, technik
    kc.tavily_search_with_fallback, kc.vertiefe_marktrecherche = verboten, verboten
    kc.marktpreis_recherche_moeglich = lambda *a: False
    try:
        return KaufCheckResponse(**await kc.run_kaufcheck(req))
    finally:
        (kc.call_gemini_json, kc.recherchiere_technisch, kc.tavily_search_with_fallback,
         kc.marktpreis_recherche_moeglich, kc.vertiefe_marktrecherche) = orig


_D_WEB = {
    "identitaet": [
        {"url": "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc-f/",
         "title": "Lexus RC F: Technische Daten",
         "content": "Der Lexus RC F ist ein Sportcoupe mit 5.0-Liter-V8-Motor und 477 PS."},
        {"url": "https://www.auto-motor-und-sport.de/lexus-rc-f-test/",
         "title": "Lexus RC F im Test",
         "content": "Der Lexus RC F leistet 477 PS aus einem 5.0-Liter-V8-Saugmotor."},
    ],
    "rueckruf": _TREFFER_A,
    "schwachstelle": [], "wartung": [],
}
_r_d = asyncio.run(_lauf(KaufCheckRequest(
    marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000, motor="5.0 V8",
    kraftstoff="Benzin", leistung_ps=477, getriebe="Automatik", antrieb="Heck",
    ausstattung=[]), web_fixture=_D_WEB))

_web_rueckruf_insights = [i for i in _r_d.insights if i.kategorie == "web_rueckruf"]
check("D1: genau EIN web_rueckruf-Insight im sichtbaren Bericht (vorher: zwei)",
      len(_web_rueckruf_insights) == 1, str([i.titel for i in _web_rueckruf_insights]))
if _web_rueckruf_insights:
    check("D2: beide Bauteilbegriffe im Insight-Titel/Beschreibung sichtbar",
          "Kraftstoffpumpe" in _web_rueckruf_insights[0].titel + _web_rueckruf_insights[0].beschreibung
          and "Pulsationsd" in _web_rueckruf_insights[0].titel + _web_rueckruf_insights[0].beschreibung,
          _web_rueckruf_insights[0].titel + " | " + _web_rueckruf_insights[0].beschreibung)

_vf_rueckruf = [a for a in (_r_d.kaufaktionen.verkaeuferfragen.fahrzeugspezifisch
                           + _r_d.kaufaktionen.verkaeuferfragen.basis)
               if a.kategorie == "web_rueckruf"]
_dok_rueckruf = [a for a in (_r_d.kaufaktionen.dokumente.fahrzeugspezifisch
                            + _r_d.kaufaktionen.dokumente.basis)
                if a.kategorie == "web_rueckruf"]
check("D3: genau EINE Verkäuferfrage zur Rückrufaktion (vorher: zwei)",
      len(_vf_rueckruf) == 1, str([a.titel for a in _vf_rueckruf]))
check("D4: genau EINE Dokumentenanforderung zur Rückrufaktion (vorher: zwei)",
      len(_dok_rueckruf) == 1, str([a.titel for a in _dok_rueckruf]))


print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE RUECKRUF-DEDUPE-TESTS GRUEN")
