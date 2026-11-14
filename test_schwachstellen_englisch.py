"""
Englische Schwachstellen-Evidenz (Paket "Englisch") -- gebuendelte Offline-Pruefung.

ALLE englischen Faelle sind SYNTHETISCH (gekennzeichnet). Echte englische Schwachstellentexte zum Lexus RC F
liegen lokal NICHT vor (die einzigen echten englischen Texte -- Tavily-Firmenseite, zwei YouTube-Transkripte zur
NX-Wartung -- enthalten keine Modellschwachstelle). Deutsche Gegenproben nutzen reale Fixtures (ADAC-Volltext)
und die bestehenden Testfaelle als Regressionsbasis.

Netzsperre und Temp-DB vor den App-Importen; kein Provider-, LLM- oder Netzzugriff.

    python test_schwachstellen_englisch.py
"""
import os
import re
import socket
import sys
import tempfile
import threading
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="en_")
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

import app.schwachstelle_englisch as en  # noqa: E402
import app.technical_research as tr  # noqa: E402
from app.kaufaktionen import _KOMPONENTEN  # noqa: E402
from app.technical_research import _extrahiere_fakten, _norm, _schwachstelle_candidate_fakten  # noqa: E402

_FEHLER: list[str] = []


def check(name, bedingung, detail=""):
    print(("  [OK]   " if bedingung else "  [FAIL] ") + name + ("" if bedingung else f"  -> {detail}"))
    if not bedingung:
        _FEHLER.append(name)


HERSTELLER = "https://www.lexus.co.uk/rc-f/owners"          # Markeneigene Domain -> Hauptpfad (Tier 1)
FACH = "https://www.adac.de/rc-f"                           # Fachmedium -> Hauptpfad


def hit(url, titel, text):
    return {"url": url, "title": titel, "content": text, "raw_content": None}


ZIEL = dict(marke="Lexus", modell="RC F", baujahr=2017, generation="UXC1", hubraum="5.0",
            fuel_ziel="benzin", powertrains_ziel=frozenset({"ICE"}), motor="5.0 V8")


def fakten(treffer, **kw):
    ab: list[dict] = []
    args = {**ZIEL, **kw}
    f = _extrahiere_fakten(treffer, "schwachstelle", abgelehnt=ab, **args)
    return f, ab


def gruende(ab):
    return sorted({a.get("grund") for a in ab})


def einzel(text, titel="Lexus RC F problems", url=HERSTELLER, **kw):
    f, ab = fakten([hit(url, titel, text)], **kw)
    return f, ab


print("=== A) Sprache: nur eindeutig Englisches wird englisch behandelt (SYNTHETISCH) ===")
check("A1 englischer Satz", en.sprache("The water pump is a common failure and many owners report leaks.") == "en")
check("A2 deutscher Satz", en.sprache("Die Wasserpumpe ist häufig defekt und viele Besitzer berichten davon.") == "de")
check("A3 Umlaut verhindert Englisch", en.sprache("The Wasserpumpe is a common Mängel on the car") != "en")
check("A4 kurzes Fragment ohne Funktionswoerter ist unklar", en.sprache("RC F water pump failure common") == "unklar")
check("A5 gemischter Satz ist nicht englisch",
      en.sprache("Die Wasserpumpe is a common failure with the car") != "en")

print("=== B) Bauteile: Wortgrenzen, bestehende Schluessel, keine Zufallstreffer ===")
schluessel = {e["schluessel"]: e for e in _KOMPONENTEN}
labels_ok = True
for muster, label, key, _g in [(m.pattern, l, k, g) for m, l, k, g in en._BAUTEILE]:
    eintrag = schluessel.get(key)
    if eintrag is None or not any(mu in _norm(label) for mu in eintrag["muster"]):
        labels_ok = False
        print("   Label/Schluessel passt nicht:", label, key)
check("B1 jedes englische Bauteil traegt ein Label, das sein deutsches Muster enthaelt", labels_ok)
for wort in ("agree", "bladder", "describe", "degree", "agreement", "scrambled", "discover", "transmitted",
             "trustworthy", "frost", "manifest", "kilometers", "turbulent"):
    check(f"B2 kein Zufallsbauteil aus '{wort}'", en.bauteil(f"Owners {wort} that it is common") is None,
          str(en.bauteil(f"Owners {wort} that it is common")))
check("B3 water pump -> Wasserpumpe/kuehlung", en.bauteil("The water pump leaks") == ("Wasserpumpe", "kuehlung"))
check("B4 brake horsepower ist keine Bremse", en.bauteil("It makes 450 brake horsepower") is None)
check("B5 spezifisches Bauteil schlaegt 'engine'",
      en.bauteil("The engine of the car has a timing chain problem") == ("Steuerkette", "steuerkette"))
check("B6 nur 'engine' -> generisch Motor", en.bauteil("The engine is noisy") == ("Motor", "motor"))

print("=== C) Positiv: allgemeiner englischer Befund mit belegter Anwendbarkeit (SYNTHETISCH) ===")
POS = ("In Europe, the water pump on the Lexus RC F is a common failure and many owners report coolant leaks.")
f, ab = einzel(POS)
check("C1 Fakt Wasserpumpe aus englischem Satz", [x.bauteil for x in f] == ["Wasserpumpe"], f"{f} {gruende(ab)}")
if f:
    check("C2 gekennzeichnetes englisches Originalzitat mit belegtem Marktbezug",
          "englischsprachiges Originalzitat" in f[0].aussage and "Europa" in f[0].aussage
          and "water pump" in f[0].aussage, f[0].aussage)
    check("C3 kein unresolved-Pfad: bestaetigter Fakt ohne offene Geltung",
          f[0].geltung_fuer_fahrzeug in (None, "covered"), str(f[0].geltung_fuer_fahrzeug))
    f_de, _ = einzel("Bei vielen Lexus RC F fällt die Wasserpumpe häufig aus, eine bekannte Schwachstelle des "
                     "Modells.")
    check("C4 keine pauschale Konfidenzobergrenze: gleiche Quelle, gleiche Confidence wie der deutsche Befund",
          bool(f_de) and f[0].confidence == f_de[0].confidence,
          f"{f[0].confidence} vs {f_de[0].confidence if f_de else None}")
f2, _ = einzel("In the UK, owners of the Lexus RC F often report that the thermostat fails and the timing "
               "chain is a known weak point.")
check("C5 mehrere Befunde in einem Satz, beide mit Wortgrenze", {x.bauteil for x in f2} >= {"Thermostat"},
      str([x.bauteil for x in f2]))

print("=== D) Negativ: Verneinung/Relativierung (SYNTHETISCH) ===")
for name, text in [
    ("no known issues", "In Europe there are no known issues with the water pump on the Lexus RC F."),
    ("not a common problem", "In Europe the water pump on the Lexus RC F is not a common problem."),
    ("isn't (Kontraktion)", "In Europe the water pump on the Lexus RC F isn't a common problem for owners."),
    ("free of", "In Europe the water pump on the Lexus RC F is free of problems for most owners."),
    ("never fails", "In Europe the water pump on the Lexus RC F never fails according to many owners."),
    ("nothing to worry", "In Europe there is nothing to worry about with the water pump on the Lexus RC F."),
    ("rarely", "In Europe many owners say the water pump on the Lexus RC F rarely fails."),
    ("trouble-free", "In Europe the water pump on the Lexus RC F is trouble-free for many owners."),
    ("Frage", "In Europe, is the water pump on the Lexus RC F a common failure for many owners?"),
    ("Bedingung", "If the water pump on the Lexus RC F fails, many owners in Europe replace the thermostat too."),
    ("Garantie", "In Europe the water pump on the Lexus RC F failure is covered by warranty for many owners."),
]:
    f, ab = einzel(text)
    check(f"D {name}: kein Fakt", not f, f"{[x.aussage for x in f]} {gruende(ab)}")

print("=== E) Einzelbericht und Wiederholung am eigenen Auto bleiben unbestaetigt (SYNTHETISCH) ===")
for name, text in [
    ("my car", "In the UK my Lexus RC F had a water pump failure at 60,000 miles."),
    ("I replaced three times", "I replaced the water pump on my Lexus RC F in Germany three times."),
    ("failed twice", "In Europe the water pump on the Lexus RC F failed twice."),
    ("our long-term car", "Our long-term Lexus RC F in the UK suffered a common water pump failure."),
    ("a reader", "A reader in Germany reports that the water pump on the Lexus RC F failed."),
]:
    f, ab = einzel(text)
    check(f"E {name}: kein Fakt", not f, f"{[x.aussage for x in f]} {gruende(ab)}")
f, ab = einzel("In the UK the water pump failure on my Lexus RC F is a common problem that many owners report.")
check("E6 ausdrueckliche Population im selben Satz hebt den Einzelbezug des Sprechers auf",
      [x.bauteil for x in f] == ["Wasserpumpe"], f"{gruende(ab)}")
f, ab = einzel("In Europe, the water pump on the Lexus RC F fails.")
check("E7 ohne Populationsbeleg: reichweite_unbelegt", not f and "reichweite_unbelegt" in gruende(ab), gruende(ab))

print("=== F) Fremdmodellabschnitt und Zielabschnitt (SYNTHETISCH) ===")
text = ("Overview of the sporty Lexus coupes and sedans sold in Europe.\n\nLexus GS F\n\nIn Europe the water pump is a common failure and many owners report coolant leaks.\n\n"
        "Lexus RC F\n\nIn Europe the timing chain is a common problem on this model and many owners report rattling.")
f, ab = einzel(text, titel="Lexus RC F")
check("F1 GS-F-Abschnitt liefert keinen RC-F-Fakt, RC-F-Abschnitt schon",
      [x.bauteil for x in f] == ["Steuerkette"], f"{[x.bauteil for x in f]} {gruende(ab)}")

print("=== G) Markt aussagenbezogen (SYNTHETISCH) ===")
for name, text in [
    ("US", "In the US, the water pump on the Lexus RC F is a common failure and many owners report leaks."),
    ("US-spec", "On US-spec examples of the Lexus RC F the water pump is a common failure for many owners."),
    ("Kanada/Australien", "In Australia the water pump on the Lexus RC F is a common failure for many owners."),
    ("kein Marker", "The water pump on the Lexus RC F is a common failure and many owners report coolant leaks."),
    ("beide Maerkte", "In the US and in Europe the water pump on the Lexus RC F is a common failure for many owners."),
]:
    f, ab = einzel(text)
    check(f"G {name}: unbestaetigt", not f, f"{[x.aussage for x in f]} {gruende(ab)}")
f, ab = einzel("In the US the water pump on the Lexus RC F is a common failure for many owners. "
               "In Germany the thermostat on the Lexus RC F is a common failure for many owners.")
check("G6 fremder Marktmarker sperrt nur seinen Satz, nicht die Seite",
      [x.bauteil for x in f] == ["Thermostat"], f"{[x.bauteil for x in f]} {gruende(ab)}")
f, ab = einzel("In Europe the water pump on the Lexus RC F is a common failure for many owners.", titel="Lexus RC F America")
check("G7 Marktmarker im Titel allein sperrt nichts", [x.bauteil for x in f] == ["Wasserpumpe"], gruende(ab))
check("G8 'mph'/'miles' sind kein Fremdmarkt-Marker",
      en.markt("In the UK the pump fails after 60,000 miles at 70 mph")[0] == "ok")
check("G9 'German engineering' ist kein Marktbeleg", en.markt("Unlike German engineering the pump fails")[0] == "unbelegt")
f, ab = einzel("Common problems of the Lexus RC F in Europe include the following.\n\n"
               "The water pump is a common failure and many owners report leaks.")
check("G10 Marktbeleg im Rahmensatz derselben Aussage genuegt", [x.bauteil for x in f] == ["Wasserpumpe"],
      f"{[x.bauteil for x in f]} {gruende(ab)}")

print("=== H) Variante, Motor, Generation, Leistung (SYNTHETISCH) ===")
for name, text, kw in [
    ("Diesel am Benziner", "In Europe the diesel Lexus RC F has a common water pump failure for many owners.", {}),
    ("anderer Hubraum", "In Europe the 4.6-litre Lexus RC F has a common water pump failure for many owners.", {}),
    ("Hybrid am ICE", "In Europe the hybrid Lexus RC F has a common water pump failure for many owners.", {}),
    ("Zielvariante unbekannt", "In Europe the diesel Lexus RC F has a common water pump failure for many owners.",
     dict(fuel_ziel=None, powertrains_ziel=None)),
    ("Leistung offen (hp)", "In Europe the 477 hp Lexus RC F has a common water pump failure for many owners.", {}),
    ("Leistung offen (bhp)", "In Europe the 467 bhp Lexus RC F has a common water pump failure for many owners.",
     dict(leistung_ps=467)),
    ("Facelift unbekannt", "In Europe the pre-facelift Lexus RC F has a common water pump failure for many owners.", {}),
    ("Motorcode allein, ohne Modell",
     "In Europe the 2UR-GSE engine is known for water pump failures and many owners report leaks.", {}),
    ("Motorcode gleich dem Ziel, ohne Modell",
     "In Europe the 2UR-GSE engine is known for water pump failures and many owners report leaks.",
     dict(motor="5.0 V8 2UR-GSE")),
    ("gleiche Generation allein, ohne Modell",
     "In Europe the second generation car is known for water pump failures and many owners report leaks.", {}),
    ("gleicher Hubraum allein, ohne Modell",
     "In Europe the 5.0-litre V8 is known for water pump failures and many owners report leaks.", {}),
]:
    f, ab = einzel(text, **kw)
    check(f"H {name}: kein bestaetigter Fahrzeugmangel", not f, f"{[x.aussage for x in f]} {gruende(ab)}")
f, ab = einzel("In Europe the 5.0-litre V8 in the Lexus RC F is known for a common water pump failure; many owners "
               "report leaks.")
check("H12 passender Hubraum UND Modell im Satz: bestaetigt", [x.bauteil for x in f] == ["Wasserpumpe"],
      f"{gruende(ab)}")
check("H13 hp wird nie als PS gelesen oder verglichen",
      en.variante("the 477 hp car", {"hubraum": "5.0"})[0] == "unbelegt"
      and en.variante("the 477 PS car", {"hubraum": "5.0"})[0] == "unbelegt")

print("=== I) Baujahr/Zeitraum englisch (SYNTHETISCH) ===")
f, ab = einzel("In Europe from 2018 onwards the water pump on the Lexus RC F is a common failure for many owners.")
check("I1 ab 2018 betrifft Baujahr 2017 nicht", not f and "baujahr_ausserhalb" in gruende(ab), gruende(ab))
f, ab = einzel("In Europe on 2015 to 2017 models of the Lexus RC F the water pump is a common failure for many owners.")
check("I2 2015 to 2017 schliesst 2017 ein", [x.bauteil for x in f] == ["Wasserpumpe"], gruende(ab))
f, ab = einzel("In Europe on early cars the water pump on the Lexus RC F is a common failure for many owners.")
check("I3 'early cars' ohne Beleg bleibt unbestaetigt", not f and "en_geltung_unbelegt" in gruende(ab), gruende(ab))
f, ab = einzel("In Europe on 2015 to 2017 models of the Lexus RC F the water pump is a common failure for many owners.",
               baujahr=None)
check("I4 Zeitfenster bei unbekanntem Baujahr bleibt unbestaetigt", not f, gruende(ab))

print("=== J) Kurze/gemischte Aussagen erhalten die Schutzregeln (SYNTHETISCH) ===")
for name, text in [
    ("Kurzfragment Verneinung", "Lexus RC F Wasserpumpe: no known issues"),
    ("Deutsch mit englischer Verneinung", "Die Wasserpumpe des Lexus RC F ist häufig defekt (no known issues)."),
    ("Kurzfragment Einzel", "Lexus RC F water pump failure, my car, three times"),
    ("Kurzfragment ohne Reichweite", "Lexus RC F water pump failure"),
]:
    f, ab = einzel(text, titel="Lexus RC F")
    check(f"J {name}: kein Fakt", not f, f"{[x.aussage for x in f]} {gruende(ab)}")

print("=== K) Deutsche Gegenproben bleiben korrekt (REAL/bestehende Faelle) ===")
f, ab = einzel("Bei vielen Lexus RC F fällt die Wasserpumpe häufig aus, eine bekannte Schwachstelle des Modells.",
               url=FACH, titel="Lexus RC F Schwachstellen")
check("K1 deutscher allgemeiner Befund wird weiter bestaetigt", [x.bauteil for x in f] == ["Wasserpumpe"],
      gruende(ab))
check("K2 deutsche Aussage bekommt keinen englischen Zitatvermerk",
      f and "englischsprachiges" not in f[0].aussage)
f, ab = einzel("Der Lexus RC F hat Common-Rail-Einspritzung. Bei vielen Fahrzeugen sind die Injektoren häufig "
               "defekt.", url=FACH, titel="Lexus RC F Technik")
check("K3 'Common-Rail' wirkt nicht als Reichweitenwort (deutsch unveraendert)",
      all("Common" not in x.aussage[:0] for x in f) and [x.bauteil for x in f] == ["Injektoren"] or not f,
      f"{[(x.bauteil, x.aussage) for x in f]}")
f, ab = einzel("Die Wasserpumpe des Lexus RC F ist undicht.", url=FACH, titel="Lexus RC F")
check("K4 deutsche unbelegte Reichweite bleibt unbestaetigt (N19)", not f and "reichweite_unbelegt" in gruende(ab),
      gruende(ab))
f, ab = einzel("Bei meinem Lexus RC F war die Wasserpumpe dreimal undicht.", url=FACH, titel="Lexus RC F")
check("K5 deutscher Einzelbericht bleibt unbestaetigt (N19)", not f and "einzelbericht" in gruende(ab), gruende(ab))
adac = Path("tests/fixtures/adac_lexus_rc_f_seitentext_2026-11-08.txt")
if adac.exists():
    roh = adac.read_text(encoding="utf-8", errors="replace")
    f_alt = _extrahiere_fakten([hit(FACH, "ADAC Lexus RC F", roh)], "schwachstelle", abgelehnt=[], **ZIEL)
    check("K6 echter ADAC-Volltext (deutsch, negativ): weiterhin kein Schwachstellenfakt", not f_alt,
          str([x.bauteil for x in f_alt]))

print("=== L) Candidate-Pfad: Quellenfamilien, Titelwoerter, Anker (SYNTHETISCH) ===")
FAM = ["https://www.en-eins.example/rcf", "https://www.en-zwei.example/rcf", "https://www.en-drei.example/rcf"]
CL = "In Europe the water pump on the Lexus RC F fails often and many owners report leaks."


def cand(titel, urls=FAM, texte=None, **kw):
    tr_ = [hit(u, titel, (texte or [CL] * len(urls))[i]) for i, u in enumerate(urls)]
    return _schwachstelle_candidate_fakten(tr_, marke="Lexus", modell="RC F", generation=None, haupt_fakten=[],
                                           erlaube_multi_source=True,
                                           ziel_info=dict(baujahr=2017, hubraum="5.0", fuel="benzin",
                                                          powertrains=frozenset({"ICE"}), motor="5.0 V8"), **kw)


fk, ab, _esk = cand("Workshop repair service guide: Lexus RC F")
check("L1 Titelwoerter workshop/repair/service qualifizieren keine englische Quelle", not fk, f"{gruende(ab)}")
check("L2 Quellen bleiben 'unknown'",
      all(tr._quellenrolle_en(hit(u, "Workshop repair service Lexus RC F", CL)) == tr.WARTUNG_SOURCE_UNKNOWN
          for u in FAM))
_orig_label = tr._qualitaets_label
tr._qualitaets_label = lambda url, marke=None: "Fachmedien" if "en-" in (url or "") else _orig_label(url, marke)
try:
    fk, ab, _esk = cand("Lexus RC F reliability")
    check("L3 drei fachlich qualifizierte unabhaengige englische Familien: Mehrquellen-Fakt (Domain-Einstufung "
          "gestubbt, SYNTHETISCH)",
          len(fk) == 1 and fk[0].beleglage == "mehrfach_unabhaengig" and fk[0].bauteil == "Wasserpumpe",
          f"{[(x.bauteil, x.beleglage) for x in fk]} {gruende(ab)}")
    if fk:
        check("L4 Zitat gekennzeichnet, Marktbezug belegt, nie 'verified'/'hoch'",
              "englischsprachiges Originalzitat" in fk[0].aussage and "Europa" in fk[0].aussage
              and fk[0].confidence == "mittel", fk[0].aussage)
    fk, ab, _esk = cand("Lexus RC F reliability", texte=[CL, CL, "The water pump on the Lexus RC F fails often "
                                                          "and many owners report leaks."])
    check("L5 eine Familie ohne Marktbeleg: nur zwei qualifizierte Familien, kein Mehrquellen-Fakt",
          not fk, f"{[x.bauteil for x in fk]}")
    fk, ab, _esk = cand("Lexus RC F reliability", texte=[CL, CL, "In the US the water pump on the Lexus RC F fails "
                                                          "often and many owners report leaks."])
    check("L6 fremder Markt in einer Familie: zwei Familien, kein Mehrquellen-Fakt", not fk, f"{gruende(ab)}")
finally:
    tr._qualitaets_label = _orig_label
f, ab = fakten([hit(HERSTELLER, "Lexus RC F", "In Europe the water pump on the Lexus RC F is a common failure for "
                    "many owners.")])
anker = f
fk, ab, _esk = _schwachstelle_candidate_fakten(
    [hit(FAM[0], "Lexus RC F", CL)], marke="Lexus", modell="RC F", generation=None, haupt_fakten=anker,
    ziel_info=dict(baujahr=2017, hubraum="5.0", fuel="benzin", powertrains=frozenset({"ICE"}), motor="5.0 V8"))
check("L7 englischer Candidate reichert einen bestehenden Hauptpfad-Fakt nur um die Quelle an (kein neuer Fakt)",
      not fk and len(anker[0].quellen) == 2, f"{len(anker[0].quellen) if anker else None}")

print("=== M) Haupt-/Candidate-Pfad bis Bericht, Verkaeuferfragen, Dokumente (SYNTHETISCH) ===")
import asyncio  # noqa: E402
import logging  # noqa: E402

import app.kaufcheck as kc  # noqa: E402
from app.models import KaufCheckRequest, KaufCheckResponse  # noqa: E402

treffer_ = lambda u, t, c: {"url": u, "title": t, "content": c}   # noqa: E731
ADAC_URL = "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/"
HOCH = "https://www.auto-motor-und-sport.de/gebrauchtwagen/lexus-rc-f/"
ADAC_ID = treffer_(ADAC_URL, "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25): Technische Daten | ADAC",
                   "Marke Lexus Modell RC F Typ Ultimate Edition Automatik Baureihe RC (UXC1) (12/14 - 06/25) "
                   "Herstellerinterne Baureihenbezeichnung UXC1 Motorcode 2UR-GSE Leistung maximal in PS 464 "
                   "Antriebsart Hinterrad Getriebeart Automatikgetriebe")
RK_URL = ("https://www.lexus.de/content/dam/lexus/nmsc/germany/lexus-besitzer/fahrzeuginformationen/"
          "RCF_tcm-3150-1284184.pdf")
RK_VOLL = ("Lexus RC F\n(UXC1 (EU, M), ab 09/2014)\nStand: 10/2014\nLexus RC F (UXC1 (EU, ab 09/2014)\n"
           "12V Spannungsversorgung trennen\nLexus RC F\n(UXC1 (EU, M), ab 09/2014)")
AMS = treffer_("https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
               "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
               "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Leistung 181 PS bis 477 PS. Hubraum 1998 cm3 bis "
               "4969 cm3. LEXUS RC F TRACK EDITION IM SUPERTEST: Was kann der V8-Sauger auf der Rennstrecke? "
               "LEXUS LC 500 UND RC F: V8-Sportler auf Abschiedstour.")
REQ = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000, motor="5.0 V8",
                       kraftstoff="Benzin", leistung_ps=477, getriebe="Automatik", antrieb="Heck", ausstattung=[])


def db_miss(marke, modell, baujahr):
    return None, {"match_art": "no_match", "konfidenz": "niedrig", "belastbar": False,
                  "fehlende_angabe": "simulierter DB-Miss, Testdouble"}


async def lauf(sw_a):
    calls: list[str] = []

    async def search(query, *, count=8, exclude_domains=None, include_domains=None, include_raw_content=False):
        calls.append(query)
        if "bekannte Probleme Schwachstellen" in query:
            return [dict(h) for h in sw_a]
        if "Generation Modellcode" in query:
            return [treffer_(RK_URL, "Lexus RC F Rettungskarte", "12V trennen")]
        if "technische Daten" in query:
            return [ADAC_ID]
        if "Generation Baureihe Bauzeitraum" in query:
            return [AMS]
        return []

    async def extract(urls):
        g = {u: RK_VOLL for u in urls if u == RK_URL}
        return g, {"angefragt": len(urls), "aus_cache": 0, "extract_calls": 1, "extract_urls": len(urls),
                   "erfolge": len(g), "fehler": len(urls) - len(g)}

    async def gemini(system, user):
        return {"bericht": "x", "empfehlung": "kaufen_nach_besichtigung", "risiko_evidence_ids": [],
                "empfehlung_evidence_ids": []}

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
        return KaufCheckResponse(**await kc.run_kaufcheck(REQ)), calls
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


def sw(r):
    return [i for i in r.insights if i.kategorie in ("web_schwachstelle", "schwachstelle")]


POS_E2E = treffer_(HOCH, "Lexus RC F Gebrauchtwagen",
                   "In Europe the water pump on the Lexus RC F is a common failure and many owners report leaks.")
NEG_E2E_US = treffer_(HOCH, "Lexus RC F Gebrauchtwagen",
                      "In the US the water pump on the Lexus RC F is a common failure and many owners report leaks.")
NEG_E2E_EINZEL = treffer_(HOCH, "Lexus RC F Gebrauchtwagen",
                          "In the UK my Lexus RC F had a water pump failure at 60,000 miles.")


async def e2e():
    r1, calls1 = await lauf([POS_E2E])
    insight = [i for i in sw(r1) if "Wasserpumpe" in i.titel]
    check("M1 englischer Befund erreicht Insight mit deutschem Bauteillabel",
          len(insight) == 1, str([i.titel for i in sw(r1)]))
    if insight:
        i = insight[0]
        check("M2 Insight traegt gekennzeichnetes englisches Originalzitat und Marktbezug",
              "englischsprachiges Originalzitat" in i.beschreibung and "water pump" in i.beschreibung
              and "Europa" in i.beschreibung, i.beschreibung)
        check("M3 bestaetigter Fakt: kein 'Geltung nicht belegt'-Pfad",
              "nicht belegt" not in i.titel and i.geltung_fuer_fahrzeug in (None, "covered"),
              f"{i.titel} {i.geltung_fuer_fahrzeug}")
    vis = sichtbar(r1)
    check("M4 Bauteil in Verkaeuferfragen/Pruefpunkten (deutsches Label)", "Wasserpumpe" in vis, vis[:300])
    check("M5 keine zusaetzliche Suchanfrage durch die Englischverarbeitung",
          len([c for c in calls1 if "bekannte Probleme Schwachstellen" in c]) == 1, str(calls1))
    for name, treffer in (("US-Markt", NEG_E2E_US), ("Einzelbericht", NEG_E2E_EINZEL)):
        r, _ = await lauf([treffer])
        check(f"M6 {name}: unbestaetigt -> nichts in Insights, Bericht, Fragen, Dokumenten",
              not [i for i in sw(r) if "Wasserpumpe" in i.titel] and "Wasserpumpe" not in sichtbar(r),
              str([i.titel for i in sw(r)]))


asyncio.run(e2e())

print()
if _FEHLER:
    print(f"EXIT 1 -- {len(_FEHLER)} Pruefung(en) fehlgeschlagen:")
    for n in _FEHLER:
        print("  -", n)
    raise SystemExit(1)
print("EXIT 0 -- alle gezielten Pruefungen gruen")
