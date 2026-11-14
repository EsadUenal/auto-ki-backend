"""
N19 -- begrenztes Paket gegen FALSCHE Schwachstellen in der Webrecherche.

Drei Ursachen (Analyse 14.11.2026, echter ADAC-Seitentext vom 08.11.2026):
 A. Garantie-/Spezifikationsangabe als Defektsignal: "Durchrostung 12 Jahre" (Garantietabelle) und
    die Teilstring-Suche "rost" in "Frostschutz".
 B. Fremdes Bauteil aus benachbartem Tabelleninhalt: in einem flachen Tabellenblock wurde das ERSTE
    Bauteilwort des ganzen Blocks ("Spurhalteassistent") zur Schwachstelle.
 C. Einzelbericht als wiederkehrende Modellschwachstelle: Reichweite der Aussage != Vertrauen in die
    Quelle; auch auf einer hochwertigen Domain belegt "bei meinem ..." nur einen Fall.

ECHT: der ADAC-Volltext (tests/fixtures/adac_lexus_rc_f_seitentext_2026-11-08.txt, Fixture vom 08.11.;
NICHT als im neuesten Live-Lauf verarbeitet belegt -- der produktive Schwachstellenpfad liest nur
Suchsnippets). Alles mit "SYNTHETISCH" ist ein markierter Gegenfall.
Nicht geändert: Englisch-Auswertung, Originalabruf-Nachladung, Budget, Rückruf-/Wartungspfad.

    python test_n19_falsche_schwachstellen.py
"""
import asyncio
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="n19f_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


_paar = threading.local()
_orig_paar, _orig_connect = socket.socketpair, socket.socket.connect


def _socketpair(*a, **k):
    _paar.aktiv = True
    try:
        return _orig_paar(*a, **k)
    finally:
        _paar.aktiv = False


def _connect(sock, adresse):
    if getattr(_paar, "aktiv", False):
        return _orig_connect(sock, adresse)
    return _gesperrt()


socket.socketpair, socket.socket.connect = _socketpair, _connect
socket.create_connection = _gesperrt
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import database as _db  # noqa: E402

_db.ensure_tables()

import app.kaufcheck as kc  # noqa: E402
import app.technical_research as tr  # noqa: E402
from app.models import KaufCheckRequest, KaufCheckResponse  # noqa: E402
from app.technical_research import (  # noqa: E402
    _extrahiere_fakten, _hat_materialschaden, _hat_strukturelles_defektsignal, _ist_einzelbericht,
    _ist_tabellenblock)

_FEHLER: list[str] = []


def check(name, bedingung, detail=""):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


ADAC_TEXT = (Path(__file__).parent / "tests" / "fixtures"
             / "adac_lexus_rc_f_seitentext_2026-11-08.txt").read_text(encoding="utf-8")
ADAC_URL = "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/"
ADAC_TITEL = "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25): Technische Daten | ADAC"
HOCH = "https://www.auto-motor-und-sport.de/gebrauchtwagen/lexus-rc-f/"   # Domain über der Schwelle


def fakten(inhalt, url=HOCH, titel="Lexus RC F", modell="RC F"):
    ab: list[dict] = []
    f = _extrahiere_fakten([{"url": url, "title": titel, "content": inhalt}], "schwachstelle",
                           marke="Lexus", modell=modell, baujahr=2017, generation="UXC1", abgelehnt=ab)
    return f, ab


def bauteile(inhalt, **kw):
    return [x.bauteil for x in fakten(inhalt, **kw)[0]]


# ── A) Garantie/Spezifikation ───────────────────────────────────────────────────────────────────
print("=== A) Garantie-/Spezifikationsangaben und Wortgrenzen ===")
GARANTIE = ("Herstellergarantien\nKategorie\nHerstellerangabe\nGarantie (Fahrzeug)\n3 Jahre oder 100.000 km\n"
            "Lackgarantie\n3 Jahre\nDurchrostung\n12 Jahre\nZusätzliche Garantien\nMobilitätsgarantie: 3 Jahre "
            "europaweit;\nSonstiges\nim ersten Jahr ohne Km-Begrenzung")
check("A1 ECHT: Garantieabschnitt des ADAC (Snippet-Ausschnitt) ist kein Defektsignal",
      not _hat_strukturelles_defektsignal(tr._norm(GARANTIE), GARANTIE))
f_adac, ab_adac = fakten(ADAC_TEXT, url=ADAC_URL, titel=ADAC_TITEL)
check("A2 ECHT: ADAC-Volltext ergibt KEINE Schwachstelle (vorher: 'Spurhalteassistent')",
      f_adac == [], str([(x.bauteil, x.aussage[:50]) for x in f_adac]))
check("A3 ECHT: die Garantiezeile erscheint nicht mehr als angenommener Claim",
      not any("Durchrostung" in (x.aussage or "") for x in f_adac))
# SYNTHETISCH
check("A4 SYNTHETISCH: 'Durchrostung 12 Jahre' (Angabe) -> kein Schaden",
      not _hat_materialschaden("Garantie: Durchrostung 12 Jahre") and not _hat_materialschaden("Durchrostung (12 Jahre)"))
check("A5 SYNTHETISCH: 'Durchrostungsgarantie', 'Korrosionsschutz', 'rostfrei' -> kein Schaden",
      not any(_hat_materialschaden(t) for t in (
          "Durchrostungsgarantie 12 Jahre", "Mit Korrosionsschutzwachs versiegelt.", "Der Auspuff ist rostfrei.",
          "Garantie gegen Durchrostung.")))
check("A6 SYNTHETISCH: Frostschutz ist kein Rostproblem",
      not _hat_materialschaden("Frostschutz im Kühlsystem prüfen.") and not _hat_materialschaden("Frostschutzmittel"))
check("A7 SYNTHETISCH: echter Rost-/Durchrostungsschaden bleibt Schaden",
      all(_hat_materialschaden(t) for t in (
          "An den Radläufen tritt Rost auf.", "Durchrostung an den Schwellern festgestellt.",
          "Korrosion am Unterboden ist verbreitet.", "Durchrostung nach 12 Jahren.", "Verrostete Bremsleitungen.")))
check("A8 SYNTHETISCH: Schaden MIT Garantiekontext bleibt Schaden",
      _hat_materialschaden("Nach fünf Jahren Durchrostung festgestellt, Garantie abgelehnt.")
      and _hat_materialschaden("Die Garantie greift nicht: Durchrostung ist an den Schwellern aufgetreten."))
f_a8, _ = fakten("Beim Lexus RC F wurde nach fünf Jahren Durchrostung an der Karosserie festgestellt, "
                 "die Garantie wurde abgelehnt.")
check("A9 SYNTHETISCH: Durchrostungsschaden mit Garantieangabe ergibt über den Pfad einen Fakt",
      len(f_a8) == 1, str([(x.bauteil, x.aussage[:60]) for x in f_a8]))
check("A10 SYNTHETISCH: Frostschutz-Satz erzeugt keinen Fakt",
      bauteile("Der Frostschutz im Kühlsystem des Lexus RC F wurde geprüft.") == [])

# ── B) Fremdes Bauteil aus Nachbarzeilen ───────────────────────────────────────────────────────
print("\n=== B) Bauteil nur aus der Defektzeile ===")
TABELLE = ("Spurhalteassistent\nSerie\nSpurwechselassistent\nSerie\nVerkehrsschild-Erkennung\nSerie\n"
           "Wasserpumpe\nundicht\nFernlichtassistent\nSerie\nKlimaanlage\nSerie")
check("B0 Der Block wird als Tabelle erkannt, Fließtext nicht",
      _ist_tabellenblock(TABELLE) and not _ist_tabellenblock("Beim RC F wird die Wasserpumpe häufig undicht."))
b1 = bauteile(TABELLE)
check("B1 SYNTHETISCH: Defektzeile 'undicht' in fremder Ausstattungstabelle -> kein 'Spurhalteassistent'",
      b1 == ["Wasserpumpe"], str(b1))
check("B2 SYNTHETISCH: Tabellenzeile ohne eigenes Bauteil in der Defektzeile erzeugt keinen Fremd-Fakt",
      bauteile("Spurhalteassistent\nSerie\nSpurwechselassistent\nSerie\nVerkehrsschild-Erkennung\nSerie\n"
               "Fernlichtassistent\nSerie\nDefekt möglich\nSerie\nKlimaanlage\nSerie") == [])
check("B3 SYNTHETISCH: Fließtext mit Bauteil und Defekt im selben Satz bleibt unverändert",
      bauteile("Beim Lexus RC F ist die Wasserpumpe häufig undicht.") == ["Wasserpumpe"])
check("B4 SYNTHETISCH: Tabellenzeile, die Bauteil UND Defekt selbst trägt, bleibt erhalten",
      "Wasserpumpe" in bauteile("Spurhalteassistent\nSerie\nSpurwechselassistent\nSerie\n"
                                "Wasserpumpe häufig undicht\nFernlichtassistent\nSerie\nKlimaanlage\nSerie"))

# ── C) Einzelbericht ────────────────────────────────────────────────────────────────────────────
print("\n=== C) Einzelbericht != wiederkehrende Modellschwachstelle (hochwertige Domain) ===")
check("C1 SYNTHETISCH: 'bei meinem RC F war die Wasserpumpe nach 90.000 km defekt' (hohe Domain) -> kein Fakt",
      bauteile("Bei meinem Lexus RC F war die Wasserpumpe nach 90.000 km defekt.") == [])
f_c1, ab_c1 = fakten("Bei meinem Lexus RC F war die Wasserpumpe nach 90.000 km defekt.")
check("C2 … und der Grund ist als 'einzelbericht' sichtbar protokolliert",
      any(a.get("grund") == "einzelbericht" for a in ab_c1))
check("C3 SYNTHETISCH: ausdrücklich wiederkehrende Aussage -> Fakt",
      bauteile("Beim Lexus RC F ist die Wasserpumpe häufig undicht.") == ["Wasserpumpe"]
      and bauteile("Beim Lexus RC F ist die undichte Wasserpumpe ein bekanntes Problem.") == ["Wasserpumpe"])
check("C4 SYNTHETISCH: Einzelbericht MIT ausdrücklicher Wiederholungsaussage -> Fakt",
      bauteile("Bei meinem Lexus RC F war die Wasserpumpe undicht, ein bekanntes Problem.") == ["Wasserpumpe"])
check("C5 SYNTHETISCH: Erkennung",
      _ist_einzelbericht("Bei mir war die Pumpe defekt.") and _ist_einzelbericht("Ein Leser berichtet von einem Defekt.")
      and not _ist_einzelbericht("Die Pumpe ist häufig defekt.")
      and not _ist_einzelbericht("Die Wasserpumpe des RC F ist undicht."))
check("C6 SYNTHETISCH: Negation -> kein Fakt",
      bauteile("Beim Lexus RC F gibt es keine bekannten Probleme mit der Wasserpumpe.") == [])
check("C7 SYNTHETISCH: fremdes Modell (GS F) -> kein Fakt",
      bauteile("Beim Lexus GS F ist die Wasserpumpe häufig undicht.", url=HOCH.replace("rc-f", "gs-f"),
               titel="Lexus GS F Gebrauchtwagen") == [])

# ── Bis zum Bericht (echter run_kaufcheck-Pfad, gestubbte Suche/LLM) ────────────────────────────
print("\n=== Bericht, Verkäuferfragen und Dokumente (gestubbter run_kaufcheck) ===")
treffer_ = lambda u, t, c: {"url": u, "title": t, "content": c}   # noqa: E731
RK_URL = ("https://www.lexus.de/content/dam/lexus/nmsc/germany/lexus-besitzer/"
          "fahrzeuginformationen/RCF_tcm-3150-1284184.pdf")
RK_VOLL = ("Lexus RC F\n(UXC1 (EU, M), ab 09/2014)\nStand: 10/2014\n"
           "Lexus RC F (UXC1 (EU, ab 09/2014)\n12V Spannungsversorgung trennen\n"
           "Lexus RC F\n(UXC1 (EU, M), ab 09/2014)")
ADAC_ID = treffer_(ADAC_URL, ADAC_TITEL,
                   "Marke Lexus Modell RC F Typ Ultimate Edition Automatik Baureihe RC (UXC1) "
                   "(12/14 - 06/25) Herstellerinterne Baureihenbezeichnung UXC1 Motorcode 2UR-GSE "
                   "Leistung maximal in PS 464 Antriebsart Hinterrad Getriebeart Automatikgetriebe")
AMS = treffer_("https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
               "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
               "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Leistung 181 PS bis 477 PS. "
               "Hubraum 1998 cm3 bis 4969 cm3. LEXUS RC F TRACK EDITION IM SUPERTEST: Was kann der "
               "V8-Sauger auf der Rennstrecke? LEXUS LC 500 UND RC F: V8-Sportler auf Abschiedstour.")
REQ = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000, motor="5.0 V8",
                       kraftstoff="Benzin", leistung_ps=477, getriebe="Automatik", antrieb="Heck", ausstattung=[])


def db_miss(marke, modell, baujahr):
    return None, {"match_art": "no_match", "konfidenz": "niedrig", "belastbar": False,
                  "fehlende_angabe": "simulierter DB-Miss, Testdouble"}


async def lauf(schwachstellen_treffer):
    routing = {"Generation Modellcode": [treffer_(RK_URL, "Lexus RC F Rettungskarte", "12V trennen")],
               "technische Daten": [ADAC_ID], "Generation Baureihe Bauzeitraum": [AMS],
               "bekannte Probleme Schwachstellen": schwachstellen_treffer}

    async def search(query, *, count=8, exclude_domains=None, include_domains=None):
        for teil, liste in routing.items():
            if teil in query:
                return [dict(h) for h in liste]
        return []

    async def extract(urls):
        g = {u: RK_VOLL for u in urls if u == RK_URL}
        return g, {"angefragt": len(urls), "aus_cache": 0, "extract_calls": 1, "extract_urls": len(urls),
                   "erfolge": len(g), "fehler": len(urls) - len(g)}

    async def gemini(system, user):
        return {"bericht": "x", "empfehlung": "kaufen_nach_besichtigung",
                "risiko_evidence_ids": [], "empfehlung_evidence_ids": []}

    async def verboten(*a, **k):
        raise AssertionError("Marktprovider darf nicht laufen")

    orig = (kc.call_gemini_json, kc.tavily_search_with_fallback, kc.marktpreis_recherche_moeglich,
            kc.vertiefe_marktrecherche, kc.find_baureihe_mit_vertrauen, tr.tavily_search, tr.hole_raw_content)
    kc.call_gemini_json = gemini
    kc.tavily_search_with_fallback = kc.vertiefe_marktrecherche = verboten
    kc.marktpreis_recherche_moeglich = lambda *a: False
    kc.find_baureihe_mit_vertrauen = db_miss
    tr.tavily_search, tr.hole_raw_content = search, extract
    try:
        return KaufCheckResponse(**await kc.run_kaufcheck(REQ))
    finally:
        (kc.call_gemini_json, kc.tavily_search_with_fallback, kc.marktpreis_recherche_moeglich,
         kc.vertiefe_marktrecherche, kc.find_baureihe_mit_vertrauen, tr.tavily_search,
         tr.hole_raw_content) = orig


def sichtbar(r):
    akt = r.kaufaktionen
    teile = [r.bericht]
    for sek in (akt.verkaeuferfragen, akt.besichtigung, akt.probefahrt, akt.dokumente):
        teile += [f"{a.titel} {a.aktion}" for a in sek.fahrzeugspezifisch + sek.basis]
    return "\n".join(teile)


def sw_insights(r):
    return [i for i in r.insights if i.kategorie in ("web_schwachstelle", "schwachstelle")]


async def pfad():
    # ECHT: kompletter ADAC-Text als Schwachstellen-Treffer
    r0 = await lauf([treffer_(ADAC_URL, ADAC_TITEL, ADAC_TEXT)])
    check("P1 ECHT: kein Schwachstellen-Insight aus dem ADAC-Volltext", sw_insights(r0) == [],
          str([i.titel for i in sw_insights(r0)]))
    check("P2 ECHT: 'Spurhalteassistent' erscheint nirgends als Schwachstelle in Bericht, Fragen, Dokumenten",
          "Spurhalteassistent" not in sichtbar(r0) and "Schwachstelle (Webrecherche)" not in r0.bericht)
    check("P3 ECHT: Identität unverändert (UXC1)", r0.vehicle_identity.get("generation") == "UXC1")
    # SYNTHETISCH: Einzelbericht (hohe Domain) -> nichts Sichtbares
    r1 = await lauf([treffer_(HOCH, "Lexus RC F Gebrauchtwagen",
                              "Bei meinem Lexus RC F war die Wasserpumpe nach 90.000 km defekt.")])
    check("P4 SYNTHETISCH: Einzelbericht (hohe Domain) -> kein Insight, keine Wasserpumpen-Frage/-Nachweis",
          sw_insights(r1) == [] and "Wasserpumpe" not in sichtbar(r1), str([i.titel for i in sw_insights(r1)]))
    # SYNTHETISCH: ausdrücklich wiederkehrend -> sichtbar bis Fragen/Nachweise
    r2 = await lauf([treffer_(HOCH, "Lexus RC F Gebrauchtwagen",
                              "Beim Lexus RC F ist die Wasserpumpe häufig undicht.")])
    check("P5 SYNTHETISCH: wiederkehrende Aussage -> Insight 'Wasserpumpe' bleibt sichtbar",
          any("Wasserpumpe" in i.titel for i in sw_insights(r2)) and "Wasserpumpe" in r2.bericht,
          str([i.titel for i in sw_insights(r2)]))
    check("P6 … mit Frage/Nachweis zur Wasserpumpe", "Wasserpumpe" in sichtbar(r2))
    check("P7 Empfehlung/Status in allen Läufen unberührt von diesem Paket (gleiche Stufe ohne Fakt)",
          (r0.empfehlung, r0.recommendation_state) == (r1.empfehlung, r1.recommendation_state),
          f"{(r0.empfehlung, r0.recommendation_state)} vs {(r1.empfehlung, r1.recommendation_state)}")


asyncio.run(pfad())

print()
if _FEHLER:
    print("FEHLER:", _FEHLER)
    raise SystemExit(1)
print("ALLE OK")
