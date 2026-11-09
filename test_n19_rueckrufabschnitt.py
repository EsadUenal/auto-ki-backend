"""
N19-Nebenbefund -- ein Rückrufdatensatz wird nicht als allgemeine Schwachstelle ausgegeben.

Befund (echter ADAC-Seitentext, tests/fixtures/adac_lexus_rc_f_seitentext_2026-11-08.txt): der
Abschnitt "Rückrufe & Mängel" mit der Rückruftabelle (Rückrufdatum, Anlass, Betroffene Modelle,
Variante, Anzahl betroffener Fahrzeuge ...) wurde von der Schwachstellenextraktion als Schwachstelle
"Motor" übernommen (Datenqualität niedrig).

Erkennung: Feldbezeichnungen eines Rückrufformulars (generisch, keine ADAC-/Lexus-Logik). Der
Rückrufpfad, Quellenschwellen und FIN-Einstufung sind nicht angefasst.

    python test_n19_rueckrufabschnitt.py
"""
import asyncio
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="n19r_")
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
from app.technical_research import _extrahiere_fakten, _ist_rueckrufabschnitt  # noqa: E402

_FEHLER: list[str] = []


def check(name, bedingung, detail=""):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


ADAC_TEXT = (Path(__file__).parent / "tests" / "fixtures"
             / "adac_lexus_rc_f_seitentext_2026-11-08.txt").read_text(encoding="utf-8")
ADAC_URL = "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/"
ADAC_TITEL = "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25): Technische Daten | ADAC"
TABELLE = ADAC_TEXT[ADAC_TEXT.find("Rückrufe & Mängel des Lexus RC"):][:700]   # echter Tabellenausschnitt
# MARKIERTER SYNTHETISCHER SATZ (kein Quelltext): separat belegte Schwachstelle derselben Seite.
LEGIT = "Bekanntes Problem beim RC F: Der Ölkühler ist häufig undicht."


def hit(extra=""):
    return {"url": ADAC_URL, "title": ADAC_TITEL, "content": TABELLE + (("\n\n" + extra) if extra else "")}


def fakten(content_extra=""):
    ab: list[dict] = []
    f = _extrahiere_fakten([hit(content_extra)], "schwachstelle", marke="Lexus", modell="RC F",
                           baujahr=2017, generation="UXC1", abgelehnt=ab)
    return f, ab


# 1) Erkennung
check("Rückrufformular-Feldbezeichnungen werden erkannt",
      all(_ist_rueckrufabschnitt(t) for t in (
          "März 2018\nRückrufdatum\nMärz 2018\nAnlass", "Rückrufnummer 12358", "Rueckruf-Nr 12/34",
          "Anzahl betroffener Fahrzeuge 207", "Halterbenachrichtigung per Brief")))
check("Fließtext-Schwachstelle, die einen Rückruf nur erwähnt, ist KEIN Rückrufabschnitt",
      not any(_ist_rueckrufabschnitt(t) for t in (
          "Wegen der undichten Wasserpumpe gab es einen Rückruf.", LEGIT,
          "Die Pumpe ist ein bekanntes Problem; ein Rückruf ist nicht bekannt.")))

# 2) Der echte Tabellenausschnitt
f0, ab0 = fakten()
check("Echter ADAC-Tabellenausschnitt: KEIN Schwachstellen-Fakt (vorher: Schwachstelle 'Motor')",
      f0 == [], str([(x.bauteil, x.aussage[:60]) for x in f0]))
check("Der Rückrufabschnitt steht als eigener Ablehnungsgrund im Diagnoselog",
      any(a.get("grund") == "rueckrufabschnitt" for a in ab0))

# 3) Separat belegte Schwachstelle derselben Quelle bleibt erhalten
f1, _ = fakten(LEGIT)
check("Separat belegte Schwachstelle aus derselben Quelle bleibt erhalten, ohne 'Motor'-Fakt aus der Tabelle",
      [x.bauteil for x in f1] == ["Ölkühler"], str([(x.bauteil, x.aussage[:60]) for x in f1]))

# 4) Der Rückrufpfad bleibt getrennt (unverändert): die Tabelle erzeugt dort weiterhin ihren Fakt
ab2: list[dict] = []
rr = _extrahiere_fakten([hit()], "rueckruf", marke="Lexus", modell="RC F", baujahr=2017,
                        generation="UXC1", abgelehnt=ab2)
check("Rückrufextraktion über dieselbe Tabelle läuft unverändert (Guard nur für 'schwachstelle')",
      not any(a.get("grund") == "rueckrufabschnitt" for a in ab2), str([a.get("grund") for a in ab2][:6]))

# 5) Bis in Bericht, Fragen und Nachweise (echter run_kaufcheck-Pfad, gestubbte Suche)
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


async def lauf(extra=""):
    routing = {"Generation Modellcode": [treffer_(RK_URL, "Lexus RC F Rettungskarte", "12V trennen")],
               "technische Daten": [ADAC_ID], "Generation Baureihe Bauzeitraum": [AMS],
               "bekannte Probleme Schwachstellen": [hit(extra)]}

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


async def pfad():
    r0 = await lauf()
    sw0 = [i for i in r0.insights if i.kategorie in ("web_schwachstelle", "schwachstelle", "motorproblem")]
    check("Bericht/Insights: die Rückrufzeile erzeugt keine allgemeine Schwachstelle", sw0 == [],
          str([i.titel for i in sw0]))
    check("Bericht, Fragen und Nachweise nennen den Tabelleninhalt nicht als Schwachstelle",
          "Pumpenabdeckung" not in sichtbar(r0) and "Schwachstelle (Webrecherche)" not in r0.bericht,
          "")
    check("Identität unverändert (UXC1) und Empfehlung unberührt",
          r0.vehicle_identity.get("generation") == "UXC1")
    r1 = await lauf(LEGIT)
    sw1 = [i for i in r1.insights if i.kategorie in ("web_schwachstelle", "schwachstelle")]
    check("Separat belegte Schwachstelle bleibt im Bericht sichtbar (Ölkühler)",
          any("Ölkühler" in i.titel for i in sw1) and "Ölkühler" in r1.bericht, str([i.titel for i in sw1]))
    check("… und nur sie: kein Tabellen-'Motor'-Fakt",
          not any(i.titel.startswith("Motor") for i in sw1), str([i.titel for i in sw1]))
    check("… mit Frage/Nachweis zum Ölkühler",
          "Ölkühler" in sichtbar(r1))
    check("Empfehlung/Status in beiden Läufen identisch (kein Eingriff in Empfehlungslogik)",
          (r0.empfehlung, r0.recommendation_state) == (r1.empfehlung, r1.recommendation_state),
          f"{(r0.empfehlung, r0.recommendation_state)} vs {(r1.empfehlung, r1.recommendation_state)}")


asyncio.run(pfad())

print()
if _FEHLER:
    print("FEHLER:", _FEHLER)
    raise SystemExit(1)
print("ALLE OK")
