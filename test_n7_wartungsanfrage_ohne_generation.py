"""
N7 -- die Wartungsanfragen enthalten den Generationscode nicht.

Befund (zwei echte Tavily-Suchen, 08.11.2026, tests/fixtures/live_wartung_treffer_2026-11-08.json):
  * "Lexus RC F Wartungsintervall Serviceintervall"        -> Lexus-Serviceheft (tools.lexus.ch) Platz 1
  * "Lexus RC F UXC1 Wartungsintervall Serviceintervall"   -> Serviceheft nicht unter den 8 Treffern
Fix: die erste Wartungsanfrage nutzt `breit` statt `basis` (Generation). Die zweite Wartungsanfrage
tat das schon. Rückruf- und Schwachstellenanfragen bleiben unverändert.

Echter run_kaufcheck()-Webpfad (echter TavilyTechnicalResearchProvider); nur Suche/Extract/DB-Lookup/
Gemini gestubbt; die Suche antwortet mit den AUFGEZEICHNETEN Treffern je nach Anfragetext, d. h.
eine Anfrage mit "UXC1" bekäme die verdrängenden Treffer. Netz + DB über scripts/test_isolated.py.

    python scripts/test_isolated.py test_n7_wartungsanfrage_ohne_generation.py
"""
import asyncio
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import app.kaufcheck as kc  # noqa: E402
import app.technical_research as tr  # noqa: E402
from app.models import KaufCheckRequest, KaufCheckResponse  # noqa: E402
from test_rc_f_lexus_care_wartungsplan_real import LEXUS_CH_MODELLUEBERSICHT  # noqa: E402

_FEHLER: list[str] = []


def check(name, bedingung, detail=""):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


REC = json.loads((Path(__file__).parent / "tests" / "fixtures"
                  / "live_wartung_treffer_2026-11-08.json").read_text(encoding="utf-8"))
OHNE = [treffer(h["url"], h["title"], h["content"] or "") for h in REC["ohne UXC1"]]
MIT = [treffer(h["url"], h["title"], h["content"] or "") for h in REC["mit UXC1"]]
SERVICEHEFT = LEXUS_CH_MODELLUEBERSICHT["url"].split("#")[0]
# Abruftext: NUR die beiden echten Absätze (Seite 4 "Betroffene Modelle", Seite 15 Care-Wartungsplan). Der dritte Absatz
# der alten Fixture ("Lexus RC F zusätzliche Inspektionen laut Lexus Care-Wartungsplan: Ventilspiel ... Automatikgetriebeöl ...")
# ist KEIN Originalwortlaut und wird nicht mehr verwendet -- die Originalzeilen stehen in den Tabellenblöcken (Seite 16/17).
SERVICEHEFT_TEXT = "\n\n".join(LEXUS_CH_MODELLUEBERSICHT["content"].split("\n\n")[:2])


def _rohtext_bloecke():
    """Die echten Rohtext-Blöcke FITNESS und SICHERHEIT (lex-sw-00de.pdf, Seite 16/17) aus dem bestehenden
    Test, per ast gelesen (kein Import-Seiteneffekt). Wortgleich in test_n2_n3_n7/test_n9/test_n10."""
    import ast
    quelle = (Path(__file__).parent / "test_n2_n3_n7_wartung_tabellenblock.py").read_text(encoding="utf-8")
    werte = {}
    for n in ast.parse(quelle).body:
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in ("_FITNESS_BLOCK", "_SICHERHEIT_BLOCK"):
            werte[n.targets[0].id] = ast.literal_eval(n.value)
    return werte["_FITNESS_BLOCK"], werte["_SICHERHEIT_BLOCK"]


_FITNESS, _SICHERHEIT = _rohtext_bloecke()
# Volltext = bisheriger Abruftext + die beiden Wartungstabellen (Seite 16/17) desselben Dokuments
SERVICEHEFT_VOLLTEXT = SERVICEHEFT_TEXT + "\n\n" + _FITNESS + "\n\n" + _SICHERHEIT
SERVICE_SEITE = "https://www.lexus.de/lexus-besitzer/service-wartung"

_RK_URL = ("https://www.lexus.de/content/dam/lexus/nmsc/germany/lexus-besitzer/"
           "fahrzeuginformationen/RCF_tcm-3150-1284184.pdf")
_RK_DUENN = "12V Spannungsversorgung trennen\nHinweise zum Transport\nZugang 12V Batterie"
_RK_VOLL = ("Lexus RC F\n(UXC1 (EU, M), ab 09/2014)\nStand: 10/2014\n"
            "Lexus RC F (UXC1 (EU, ab 09/2014)\n"
            "12V Spannungsversorgung trennen\nHinweise zum Transport\nZugang 12V Batterie\n"
            "Lexus RC F\n(UXC1 (EU, M), ab 09/2014)")
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
    "477 PS. Hubraum 1998 cm3 bis 4969 cm3. LEXUS RC F TRACK EDITION IM SUPERTEST: "
    "Was kann der V8-Sauger auf der Rennstrecke? LEXUS LC 500 UND RC F: "
    "V8-Sportler auf Abschiedstour.")

REQ = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000,
                       motor="5.0 V8", kraftstoff="Benzin", leistung_ps=477,
                       getriebe="Automatik", antrieb="Heck", ausstattung=[])


class Fake:
    def __init__(self, routing, extract_text):
        self.routing, self.extract_text = routing, extract_text
        self.search_calls: list[str] = []
        self.extract_calls: list[list[str]] = []

    async def search(self, query, *, count=8, exclude_domains=None, include_domains=None, **_kw):
        self.search_calls.append(query)
        for substr, liste in self.routing.items():     # Reihenfolge = Vorrang (spezifischer zuerst)
            if substr in query:
                return list(liste)
        return []

    async def extract(self, urls):
        self.extract_calls.append(list(urls))
        gefunden = {u: self.extract_text[u] for u in urls if u in self.extract_text}
        return gefunden, {"angefragt": len(urls), "aus_cache": 0, "extract_calls": 1,
                          "extract_urls": len(urls), "erfolge": len(gefunden),
                          "fehler": len(urls) - len(gefunden)}


def db_miss(marke, modell, baujahr):
    return None, {"match_art": "no_match", "konfidenz": "niedrig", "belastbar": False,
                  "fehlende_angabe": "simulierter DB-Miss, Testdouble"}


async def lauf(generation_bestaetigt: bool, wartung_routing=True, serviceheft_text=None):
    routing = {
        # Wartung: "UXC1 Wartungsintervall" ZUERST -- würde die Anfrage den Code tragen,
        # bekäme sie die aufgezeichneten, verdrängenden Treffer.
        **({"UXC1 Wartungsintervall": MIT,
            "Wartungsintervall Serviceintervall": OHNE,
            "Inspektionsintervall": [h for h in OHNE if h["url"] == SERVICE_SEITE]} if wartung_routing else {}),
        "technische Daten": [_ADAC],
        "Generation Baureihe Bauzeitraum": [_AMS],
        **({"Generation Modellcode": [treffer(_RK_URL, "Lexus RC F Rettungskarte", _RK_DUENN)]}
           if generation_bestaetigt else {}),
    }
    fake = Fake(routing, {_RK_URL: _RK_VOLL, SERVICEHEFT: serviceheft_text or SERVICEHEFT_TEXT})
    orig = (kc.call_gemini_json, kc.tavily_search_with_fallback, kc.marktpreis_recherche_moeglich,
            kc.vertiefe_marktrecherche, kc.find_baureihe_mit_vertrauen, tr.tavily_search, tr.hole_raw_content)

    async def gemini(system, user):
        return {"bericht": "Laut Inserat unfallfrei. KAUFEN NACH BESICHTIGUNG.",
                "empfehlung": "kaufen_nach_besichtigung", "risiko_evidence_ids": [],
                "empfehlung_evidence_ids": []}

    async def verboten(*a, **k):
        raise AssertionError("Marktprovider darf nicht laufen")

    kc.call_gemini_json = gemini
    kc.tavily_search_with_fallback, kc.vertiefe_marktrecherche = verboten, verboten
    kc.marktpreis_recherche_moeglich = lambda *a: False
    kc.find_baureihe_mit_vertrauen = db_miss
    tr.tavily_search, tr.hole_raw_content = fake.search, fake.extract
    try:
        r = KaufCheckResponse(**await kc.run_kaufcheck(REQ))
    finally:
        (kc.call_gemini_json, kc.tavily_search_with_fallback, kc.marktpreis_recherche_moeglich,
         kc.vertiefe_marktrecherche, kc.find_baureihe_mit_vertrauen, tr.tavily_search,
         tr.hole_raw_content) = orig
    return fake, r


def wartung_qs(fake):
    return sorted(q for q in fake.search_calls if "Wartungsintervall" in q or "Inspektionsintervall" in q)


def aktionen(r):
    k = r.kaufaktionen
    return {n: sorted(str(a) for a in getattr(k, n).fahrzeugspezifisch + getattr(k, n).basis)
            for n in ("besichtigung", "probefahrt", "verkaeuferfragen", "dokumente")}


def wartung_insights(r):
    return [i for i in r.insights if i.kategorie == "web_wartung"]


async def lauf_alle_kandidaten(generation_bestaetigt: bool, serviceheft_text):
    """DIAGNOSE: dasselbe, aber das Fünferlimit der Wartungsfakten nur IM TESTPROZESS aufgehoben, um
    alle gültigen Kandidaten zu sehen. Das Produktlimit bleibt unverändert (wird sofort zurückgesetzt)."""
    alt = tr.MAX_FAKTEN_JE_KATEGORIE
    tr.MAX_FAKTEN_JE_KATEGORIE = 99
    try:
        return await lauf(generation_bestaetigt, serviceheft_text=serviceheft_text)
    finally:
        tr.MAX_FAKTEN_JE_KATEGORIE = alt


def titel_liste(insights):
    return [i.titel.split(":")[0] for i in insights]


def ids(liste):
    return [a.id for a in liste]


async def main():
    print("--- 1) Anfragen: mit und ohne bestätigte Generation identisch ---")
    f_mit, r_mit = await lauf(True)
    f_ohne, r_ohne = await lauf(False)
    gen_mit, gen_ohne = r_mit.vehicle_identity.get("generation"), r_ohne.vehicle_identity.get("generation")
    check("A0) Kontrolle: Lauf 1 bestätigt UXC1, Lauf 2 nicht", gen_mit == "UXC1" and gen_ohne is None,
          f"{gen_mit!r} / {gen_ohne!r}")
    qm, qo = wartung_qs(f_mit), wartung_qs(f_ohne)
    print("   Wartungsanfragen (Generation bestätigt):", qm)
    print("   Wartungsanfragen (Generation offen):    ", qo)
    check("A1) beide Wartungsanfragen sind mit und ohne bestätigte Generation wortgleich",
          len(qm) == 2 and qm == qo, f"{qm} / {qo}")
    check("A2) keine Wartungsanfrage enthält den Generationscode",
          not any("UXC1" in q for q in qm + qo))
    check("A3) Rückruf und Schwachstelle tragen die bestätigte Generation weiterhin (unverändert)",
          any("UXC1" in q and "Rückruf" in q for q in f_mit.search_calls)
          and any("UXC1" in q and "Schwachstellen" in q for q in f_mit.search_calls),
          str([q for q in f_mit.search_calls if "UXC1" in q]))
    check("A4) ohne bestätigte Generation steht in KEINER Folgeanfrage ein Code",
          not any("UXC1" in q for q in f_ohne.search_calls))

    print("\n--- 2) Replay mit dem Abruftext (Seite 4 + Care-Plan; ohne den früheren erfundenen Satz) ---")
    check("B0) der Abruftext enthält den früher erfundenen Ventilspiel-Satz NICHT mehr",
          "zusätzliche Inspektionen laut Lexus Care-Wartungsplan" not in SERVICEHEFT_TEXT
          and "Ventilspiel" not in SERVICEHEFT_TEXT)
    check("B1) UXC1 bleibt im Bericht bestätigt", gen_mit == "UXC1", str(gen_mit))
    check("B2) das Serviceheft (Lexus Schweiz) wurde für die Wartung nachgeladen",
          any(SERVICEHEFT in c for c in f_mit.extract_calls), str(f_mit.extract_calls))
    ins = wartung_insights(r_mit)
    for i in ins:
        print(f"   [{i.kategorie}] trust={i.trust} | {i.titel} | {i.beschreibung[:140]}")
    check("B3) der Care-Zwischenservice (Öl- und Filterwechsel) steht im Bericht, mit Bedingung und Marktvorbehalt",
          len(ins) == 1 and "je nachdem, was zuerst eintritt" in ins[0].beschreibung
          and 'falls für Ihren Markt zutreffend' in ins[0].beschreibung
          and "nicht bestätigt" in ins[0].beschreibung,
          str(titel_liste(ins)))

    print("\n--- 3) Gegenprobe: ohne Serviceheft-Treffer gäbe es diese Hinweise nicht ---")
    f_nur_mit, r_nur_mit = await lauf(True, wartung_routing=False)
    check("C1) ohne Wartungstreffer: kein Serviceheft-Abruf und keine Serviceheft-Hinweise",
          not any(SERVICEHEFT in c for c in f_nur_mit.extract_calls)
          and not wartung_insights(r_nur_mit))

    print("\n--- 4) Abnahme mit dem Volltext (nur der Quelltext getauscht; echte Tabellenblöcke) ---")
    check("D0) der Volltext enthält die ORIGINALZEILE 'Ventilspiel (Inspektion mit Messfühler, LC 500, RC F) "
          "Alle 90.000 km / 6 Jahre' und den erfundenen Satz nicht",
          "Ventilspiel (Inspektion mit Messfühler, LC 500, RC F) Alle 90.000 km / 6 Jahre" in SERVICEHEFT_VOLLTEXT
          and "zusätzliche Inspektionen laut Lexus Care-Wartungsplan" not in SERVICEHEFT_VOLLTEXT)
    f_voll, r_voll = await lauf(True, serviceheft_text=SERVICEHEFT_VOLLTEXT)
    ins_v = wartung_insights(r_voll)
    a_v, a_0 = aktionen(r_voll), aktionen(r_nur_mit)
    check("D1) Treffer, Eingaben und Generation gleich: UXC1 bestätigt, dieselben Wartungsanfragen",
          r_voll.vehicle_identity.get("generation") == "UXC1" and wartung_qs(f_voll) == wartung_qs(f_mit))
    sichtbar = titel_liste(ins_v)
    print(f"   BEI FÜNFERLIMIT SICHTBAR: {sichtbar}")
    check("D2) höchstens fünf Hinweise (Limit unverändert), Ventilspiel mit Originalzeile dabei",
          len(ins_v) <= 5 and any(i.titel.startswith("Ventilspiel") and
                                  "Alle 90.000 km / 6 Jahre" in i.beschreibung
                                  and "laut Lexus Care-Wartungsplan" not in i.beschreibung for i in ins_v),
          str(sichtbar))
    oel_v = [i for i in ins_v if i.titel.startswith(("Motoröl", "Oel und filterwechsel"))]
    check("D3) die beiden Ölstellen ergeben GENAU EINEN Hinweis (keine Dublette)", len(oel_v) == 1, str(sichtbar))
    if oel_v:
        b = oel_v[0].beschreibung
        check("D4) der Ölhinweis führt beide Fundstellen mit ihren Bedingungen: Care-Zwischenservice "
              "(15.000 km oder einmal jährlich, je nachdem, was zuerst eintritt) UND die Tabellenzeile",
              "Lexus Care-Zwischenservice: alle 15.000 km oder einmal jährlich" in b
              and "je nachdem, was zuerst eintritt" in b and "Motoröl und -filter" in b, b[:300])
        stellen = b.split("Weitere Stelle im selben Dokument:")
        bedingt = [t for t in stellen if "Managementsystem" in t]
        unbedingt = [t for t in stellen if "Managementsystem" not in t]
        check("D5) die Hinweismeldung gilt nur 'Bei Fahrzeugen mit Ölwechsel-Managementsystem' und steht NUR "
              "in der Fundstelle, die diese Bedingung trägt -- die Care-Zwischenservice-Stelle bleibt davon frei",
              len(stellen) == 2 and len(bedingt) == 1 and len(unbedingt) == 1
              and "Bei Fahrzeugen mit Ölwechsel-Managementsystem" in bedingt[0]
              and "Lexus Care-Zwischenservice" in unbedingt[0], b[:400])
        check("D5b) N20: die Fußnotenbedingung bleibt in ihrer Stelle, davor und danach lesbare Satzzeichen "
              "(kein 'zutreffend Weitere Stelle')",
              "zutreffend Weitere Stelle" not in b
              and b.count("Weitere Stelle im selben Dokument:") == 1
              and bedingt[0].rstrip().endswith((".", "!", "?")), b[:420])
    frag_oel = [x for x in ids(r_voll.kaufaktionen.verkaeuferfragen.fahrzeugspezifisch) if "wartung-web" in x and
                ("motoroel" in x or "filterwechsel" in x)]
    dok_oel = [x for x in ids(r_voll.kaufaktionen.dokumente.fahrzeugspezifisch) if "wartung-web" in x and
               ("motoroel" in x or "filterwechsel" in x)]
    check("D6) Öl: genau EINE Verkäuferfrage und EIN Nachweisdokument", len(frag_oel) == 1 and len(dok_oel) == 1,
          f"{frag_oel} / {dok_oel}")
    check("D7) kein Inverter-Kühlmittel: weder Hinweis noch Frage noch Nachweis noch Bericht (Elektrifizierung "
          "unbekannt, Anwendbarkeit bleibt offen)",
          not any("nverter" in t for t in sichtbar)
          and not any("nverter" in x for x in ids(r_voll.kaufaktionen.verkaeuferfragen.fahrzeugspezifisch)
                      + ids(r_voll.kaufaktionen.dokumente.fahrzeugspezifisch))
          and "nverter" not in r_voll.bericht)
    check("D8) jeder sichtbare Hinweis behält Markt-Selbsteinschränkung der Quelle und 'nicht bestätigt'",
          bool(ins_v) and all('falls für Ihren Markt zutreffend' in i.beschreibung
                              and "nicht bestätigt" in i.beschreibung for i in ins_v))

    # ── Ausgabe: Tabellenreste und Herkunft im Nachweis ─────────────────────────
    if oel_v:
        b = oel_v[0].beschreibung
        print("   ÖLHINWEIS:", b)
        check("D9) keine Tabellenmarker im sichtbaren Ölhinweis ('*1', Spaltenmarker 'E E', Definitionskürzel '*1:')",
              "*" not in b and " E E" not in b and "*1:" not in b, b[:200])
        check("D10) Fußnotenbedingung, Tätigkeit, Intervall und Marktvorbehalt bleiben: 'Bei Fahrzeugen mit "
              "Ölwechsel-Managementsystem', 'wechseln', 'zumindest alle 15.000 km / 1 Jahr', 'falls für Ihr Land "
              "zutreffend', 'falls für Ihren Markt zutreffend'",
              all(x in b for x in ("Motoröl und -filter: Bei Fahrzeugen mit Ölwechsel-Managementsystem",
                                   "wechseln", "zumindest alle 15.000 km / 1 Jahr", "falls für Ihr Land zutreffend",
                                   "falls für Ihren Markt zutreffend")), b[:400])
    check("D11) in keinem Wartungshinweis steht ein Tabellenmarker ('*1', ' E E', ' I I')",
          not any("*" in i.beschreibung or " E E" in i.beschreibung or " I I" in i.beschreibung for i in ins_v))
    dok_v = r_voll.kaufaktionen.dokumente
    herkunft = "Die Intervallangabe stammt aus der Webrecherche, nicht aus der geprüften Fahrzeugdatenbank."
    check("D12) JEDER Web-Wartungsnachweis nennt die Herkunft -- auch wenn die Kilometerschwelle (bei 82.000 km) "
          "noch nicht erreicht ist",
          bool([a for a in dok_v.fahrzeugspezifisch if "wartung-web" in a.id])
          and all(herkunft in a.aktion for a in dok_v.fahrzeugspezifisch if "wartung-web" in a.id),
          str([a.id for a in dok_v.fahrzeugspezifisch if "wartung-web" in a.id and herkunft not in a.aktion]))
    kuehl = [a for a in dok_v.fahrzeugspezifisch if "antriebsriemen" in a.id]
    for a in kuehl:
        print("   NACHWEIS Antriebsriemen:", a.titel, "|", a.aktion)
    check("D13) Nebenaggregat-Antriebsriemen (105.000 km / 6 Jahre, bei 82.000 km km-seitig nicht erreicht): "
          "Nachweis mit Herkunft und der unveränderten Vorlage (Rechnung/Serviceheft, Datum, Kilometerstand). "
          "(Wartungsrangfolge-Paket: 'Motorkühlmittel' mit 150.000 km, nicht erreicht, steht nicht mehr im "
          "Fünferlimit; der Antriebsriemen belegt dieselbe Eigenschaft 'km-Schwelle nicht erreicht')",
          len(kuehl) == 1 and herkunft in kuehl[0].aktion and "Datum und Kilometerstand" in kuehl[0].aktion
          and "zeitliche Alternative" in kuehl[0].aktion
          # F3 (fachlich begründet): Baujahr 2017 -> Fahrzeugalter mindestens 8 Jahre >= 6 Jahre Zeitschwelle: die
          # Zeitschwelle ist BELEGBAR erreicht und wird so benannt (vorher "nicht ausschließen, dass ... erreicht
          # ist" -- zu vorsichtig, obwohl der ganze mögliche Altersbereich die Schwelle überschreitet). Aussage
          # bleibt: ob die Arbeit gemacht wurde, ist unbekannt; keine Fälligkeit, kein Versäumnis.
          and "nach dem Baujahr bereits erreicht" in kuehl[0].aktion
          and re.search(r"Fahrzeugalter mindestens \d+ Jahre", kuehl[0].aktion)
          and "geht aus den vorliegenden Daten nicht hervor" in kuehl[0].aktion
          and "überfällig" not in kuehl[0].aktion, str([a.aktion for a in kuehl]))
    check("D13b) Motorkühlmittel (150.000 km, nicht erreicht, kein Zeitintervall) verliert gegen den "
          "Antriebsriemen mit belegbar erreichter Zeitschwelle -- kein Nachweis dafür im Fünferlimit",
          not [a for a in dok_v.fahrzeugspezifisch if "motorkuehlmittel" in a.id])
    fr_oel = [a for a in r_voll.kaufaktionen.verkaeuferfragen.fahrzeugspezifisch if "motoroel" in a.id]
    dk_oel = [a for a in dok_v.fahrzeugspezifisch if "motoroel" in a.id]
    for a in fr_oel + dk_oel:
        print("   ÖL", a.bereich, "|", a.titel, "|", a.aktion)
    check("D14) Ölfrage und Ölnachweis ohne Tabellenmarker",
          len(fr_oel) == 1 and len(dk_oel) == 1
          and not any("*" in a.titel + a.aktion or " E E" in a.titel + a.aktion for a in fr_oel + dk_oel))

    print("\n--- 5) ALLE gültigen Kandidaten (Fünferlimit nur im Testprozess aufgehoben) ---")
    f_alle, r_alle = await lauf_alle_kandidaten(True, SERVICEHEFT_VOLLTEXT)
    ins_a = wartung_insights(r_alle)
    print(f"   ALLE KANDIDATEN: {titel_liste(ins_a)}")
    by = {i.titel.split(":")[0]: i for i in ins_a}
    check("E0) UXC1 bleibt bestätigt, Inverter weiterhin kein Kandidat",
          r_alle.vehicle_identity.get("generation") == "UXC1" and not any("nverter" in t for t in by))
    ag = by.get("Automatikgetriebeöl")
    check("E1) Automatikgetriebeöl ist eine PRÜFUNG (Abschnitt 'ZUSÄTZLICHE INSPEKTIONEN'): Frage 'bereits geprüft', "
          "Dokument 'Prüfnachweis', Intervall 60.000 km / 4 Jahre",
          ag is not None and "Alle 60.000 km / 4 Jahre" in ag.beschreibung
          and any("automatikgetriebeoel" in a.id and "bereits geprüft" in a.titel
                  for a in r_alle.kaufaktionen.verkaeuferfragen.fahrzeugspezifisch)
          and any("automatikgetriebeoel" in a.id and a.titel.startswith("Prüfnachweis")
                  for a in r_alle.kaufaktionen.dokumente.fahrzeugspezifisch),
          ag.beschreibung[:160] if ag else "fehlt")
    zk = by.get("Zündkerzen")
    check("E2) Zündkerzen: 90.000 km, ohne das fremde 60.000-km-Intervall (RX 300, LS 500), Tätigkeit 'ersetzen'",
          zk is not None and "90.000 km" in zk.beschreibung and "60,000" not in zk.beschreibung
          and "60.000" not in zk.beschreibung and "RX 300" not in zk.beschreibung
          and any("zuendkerzen" in a.id and "gewechselt" in a.titel
                  for a in r_alle.kaufaktionen.verkaeuferfragen.fahrzeugspezifisch),
          zk.beschreibung[:200] if zk else "fehlt")
    mk = by.get("Motorkühlmittel")
    check("E3) Motorkühlmittel ohne abgeschnittenes 'Inverter-'",
          mk is not None and "Inverter" not in mk.beschreibung and "150.000 km" in mk.beschreibung,
          mk.beschreibung[:200] if mk else "fehlt")
    df = by.get("Hinterachsdifferenzialöl")
    check("E4) Differenzialöl (Hinterachse): ersetzen, 30.000 km / 4 Jahre, 'außer …'-Ausschlussliste erhalten; "
          "RC F nicht ausgeschlossen, aber auch nicht ausdrücklich zugeordnet",
          df is not None and "Alle 30.000 km / 4 Jahre" in df.beschreibung and "außer ES 300h" in df.beschreibung
          and any("differenzialoel" in a.id and "gewechselt" in a.titel
                  for a in r_alle.kaufaktionen.verkaeuferfragen.fahrzeugspezifisch),
          df.beschreibung[:200] if df else "fehlt")
    rm = by.get("Nebenaggregat-Antriebsriemen")
    check("E5) Antriebsriemen: prüfen, 105.000 km / 6 Jahre, Folgeintervall 15.000 km / jährlich, Ausschlussliste erhalten",
          rm is not None and "Alle 105.000 km / 6 Jahre" in rm.beschreibung
          and "Danach alle 15.000 km / jährlich" in rm.beschreibung and "außer ES 300h" in rm.beschreibung,
          rm.beschreibung[:200] if rm else "fehlt")
    check("E6) alle Kandidaten behalten den Marktvorbehalt",
          all('falls für Ihren Markt zutreffend' in i.beschreibung for i in ins_a))
    gehalten = [t for t in titel_liste(ins_a) if t not in sichtbar]
    print(f"   BEI FÜNFERLIMIT ZURÜCKGEHALTEN: {gehalten}")
    check("E7) das Fünferlimit hält nur gültige, gleichrangige Kandidaten zurück (keine falschen oder fremden)",
          set(gehalten) <= {"Nebenaggregat-Antriebsriemen", "Zündkerzen", "Motorkühlmittel", "Automatikgetriebeöl",
                            "Hinterachsdifferenzialöl"} and len(sichtbar) + len(gehalten) == len(by), str(gehalten))

    print("\n--- 6) Inverter: unbekannt vs. belegte Anwendbarkeit ---")
    for bezeichnung, fuel, pt, erwartet in (("unbekannt", "benzin", None, False),
                                            ("Hybrid belegt (HEV)", "benzin", frozenset({"HEV"}), True),
                                            ("Plug-in belegt (PHEV)", "benzin", frozenset({"PHEV"}), True),
                                            ("Verbrenner belegt (ICE)", "benzin", frozenset({"ICE"}), False)):
        ab: list[dict] = []
        alt = tr.MAX_FAKTEN_JE_KATEGORIE
        tr.MAX_FAKTEN_JE_KATEGORIE = 99
        try:
            fk = tr._extrahiere_fakten([treffer(SERVICEHEFT, "Lexus Garantie- und Serviceheft", SERVICEHEFT_VOLLTEXT)],
                                       "wartung", marke="Lexus", modell="RC F", baujahr=2017, abgelehnt=ab,
                                       fuel_ziel=fuel, powertrains_ziel=pt)
        finally:
            tr.MAX_FAKTEN_JE_KATEGORIE = alt
        inv = [f for f in fk if "nverter" in (f.bauteil or "")]
        grund = sorted({a["grund"] for a in ab if "nverter" in a.get("satz", "")})
        check(f"F) {bezeichnung}: Inverter-Wartungspunkt vorhanden={bool(inv)} (erwartet {erwartet}); "
              f"Ablehnungsgrund={grund}",
              bool(inv) == erwartet and (erwartet or bool(grund)), "")
        if inv:
            check(f"F) {bezeichnung}: Bedingung aufgelöst (kein Status 'unresolved'), Tätigkeit 'ersetzen'",
                  inv[0].technische_bedingung_status is None and inv[0].taetigkeit == "ersetzen")
    from app.evidence import build_insights
    from app.kaufaktionen import build_kaufaktionen
    from app.models import TechnischeRecherche
    tr.MAX_FAKTEN_JE_KATEGORIE, alt = 99, tr.MAX_FAKTEN_JE_KATEGORIE
    try:
        fk = tr._extrahiere_fakten([treffer(SERVICEHEFT, "Lexus Garantie- und Serviceheft", SERVICEHEFT_VOLLTEXT)],
                                   "wartung", marke="Lexus", modell="RC F", baujahr=2017, abgelehnt=[],
                                   fuel_ziel="benzin", powertrains_ziel=frozenset({"HEV"}))
    finally:
        tr.MAX_FAKTEN_JE_KATEGORIE = alt
    web = TechnischeRecherche(ausgeloest_durch="db_miss", fakten=[f for f in fk if "nverter" in (f.bauteil or "")])
    hev_ins = build_insights(None, None, [], REQ, check_typ="kauf", web_recherche=web)
    hev_akt = build_kaufaktionen(REQ, None, None, hev_ins)
    check("F) belegter Hybrid: Inverter-Hinweis, Frage und Nachweis entstehen über den echten Pfad "
          "(Evidence -> Kaufaktionen), ohne Anwendbarkeits-Umleitung",
          any("nverter" in i.titel for i in hev_ins)
          and any("nverter" in a.titel and "bereits gewechselt" in a.titel
                  for a in hev_akt.verkaeuferfragen.fahrzeugspezifisch)
          and any("nverter" in a.titel for a in hev_akt.dokumente.fahrzeugspezifisch)
          and not any("trifft die bedingung" in a.titel.lower() for a in hev_akt.verkaeuferfragen.fahrzeugspezifisch),
          str([a.titel for a in hev_akt.verkaeuferfragen.fahrzeugspezifisch]))

    print("\n--- 7) Verkäuferfragen unter vollem Sechserlimit: Servicehistorie und Vorbesitz ---")
    kv = r_voll.kaufaktionen.verkaeuferfragen
    spez_ids = ids(kv.fahrzeugspezifisch)
    basis_ids = ids(kv.basis)
    print(f"   spezifisch ({len(spez_ids)}): {spez_ids}")
    print(f"   Basis neu: {[b for b in basis_ids if b.endswith(('scheckheft', 'vorbesitzer', 'unfall'))]}")
    check("G1) das Sechserlimit ist voll (6 fahrzeugspezifische Verkäuferfragen)", len(spez_ids) == 6, str(spez_ids))
    check("G2) Servicehistorie und Vorbesitz stehen NICHT spezifisch, aber als Basisfragen",
          "frage-scheckheft" not in spez_ids and "frage-vorbesitzer" not in spez_ids
          and "frage-basis-scheckheft" in basis_ids and "frage-basis-vorbesitzer" in basis_ids, str(basis_ids))
    titel_alle = [a.titel for a in kv.fahrzeugspezifisch + kv.basis]
    check("G3) keine doppelte Frage (Titel eindeutig)", len(titel_alle) == len(set(titel_alle)),
          str([t for t in titel_alle if titel_alle.count(t) > 1]))
    check("G4) die Basisfragen tragen Titel und Hinweis der verdrängten spezifischen Frage",
          any(a.id == "frage-basis-scheckheft" and "Scheckheft" in a.titel for a in kv.basis)
          and any(a.id == "frage-basis-vorbesitzer" and "Vorbesitzer" in a.titel for a in kv.basis))
    check("G5) Unfall bleibt als Basisfrage (wie bisher)", "frage-basis-unfall" in basis_ids)
    kv0 = r_nur_mit.kaufaktionen.verkaeuferfragen
    check("G6) ohne Verdrängung (Lauf ohne Wartungstreffer): spezifische Fragen stehen wie bisher und es "
          "entsteht KEINE zweite Basisfrage dazu",
          {"frage-scheckheft", "frage-vorbesitzer"} <= set(ids(kv0.fahrzeugspezifisch))
          and "frage-basis-scheckheft" not in ids(kv0.basis) and "frage-basis-vorbesitzer" not in ids(kv0.basis),
          str(ids(kv0.fahrzeugspezifisch)))
    dok = r_voll.kaufaktionen.dokumente
    web_ids = {i.id: i for i in ins_v}
    wartung_dok = [a for a in dok.fahrzeugspezifisch if "wartung-web" in a.id]
    check("G7) Dokumente getrennt: Serviceheft- und Zulassungsbescheinigungs-Basisdokument bleiben; jeder "
          "Wartungsnachweis ist an einen Hinweis mit Marktvorbehalt gebunden (evidence_id)",
          "dokument-basis-serviceheft" in ids(dok.basis) and "dokument-basis-zb2" in ids(dok.basis)
          and len(wartung_dok) == len(ins_v)
          and all(a.evidence_ids and a.evidence_ids[0] in web_ids
                  and "falls für Ihren Markt zutreffend" in web_ids[a.evidence_ids[0]].beschreibung
                  for a in wartung_dok), str([a.id for a in wartung_dok]))
    check("G8) Fahrzeugangaben unverändert (477 PS, 2017; keine 464 PS/Ultimate Edition)",
          r_voll.vehicle_identity.get("horsepower") == 477 and r_voll.vehicle_identity.get("year") == 2017
          and not any(x in r_voll.bericht for x in ("464", "Ultimate Edition")))

    if _FEHLER:
        print(f"\n{len(_FEHLER)} FEHLER:")
        for f in _FEHLER:
            print("  -", f)
        raise SystemExit(1)
    print("\nALLE N7-WARTUNGSANFRAGE-TESTS GRUEN")


asyncio.run(main())
