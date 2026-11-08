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
SERVICEHEFT_TEXT = LEXUS_CH_MODELLUEBERSICHT["content"]   # bisheriger Abruftext (Seite 4, 15/16; ~1.000 Zeichen)


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

    async def search(self, query, *, count=8, exclude_domains=None, include_domains=None):
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

    print("\n--- 2) Replay mit aufgezeichneten Treffern: Serviceheft kommt an, UXC1 bleibt ---")
    check("B1) UXC1 bleibt im Bericht bestätigt", gen_mit == "UXC1", str(gen_mit))
    check("B2) das Serviceheft (Lexus Schweiz) wurde für die Wartung nachgeladen",
          any(SERVICEHEFT in c for c in f_mit.extract_calls), str(f_mit.extract_calls))
    ins = wartung_insights(r_mit)
    for i in ins:
        print(f"   [{i.kategorie}] trust={i.trust} | {i.titel} | {i.beschreibung[:140]}")
    check("B3) mindestens ein Wartungshinweis steht im Bericht", len(ins) >= 1, f"{len(ins)} Hinweise")
    text_ins = " ".join(f"{i.titel} {i.beschreibung}" for i in ins)
    check("B4) die Hinweise stammen aus dem Serviceheft (15.000 km bzw. 30.000 km / 90.000 km / 60.000 km)",
          any(x in text_ins.replace(".", "") for x in ("15000", "30000", "90000", "60000")), text_ins[:200])
    check("B5) Markt-/Geltungsvorbehalt bleibt erhalten: Hinweis jedes Punktes nennt die Selbsteinschränkung "
          "der Quelle und dass die Geltung in Deutschland nicht bestätigt ist",
          bool(ins) and all('falls für Ihren Markt zutreffend' in i.beschreibung
                            and "nicht bestätigt" in i.beschreibung for i in ins), text_ins[:300])
    check("B6) Bedingungen bleiben erhalten: 'je nachdem, was zuerst eintritt' (Zwischenservice), "
          "'oder 6 Jahre' (Ventilspiel), 'oder 4 Jahre' (Getriebeöl)",
          all(w in text_ins for w in ("je nachdem, was zuerst eintritt", "oder 6 Jahre", "oder 4 Jahre")), "")

    print("\n--- 3) Gegenprobe: ohne Serviceheft-Treffer gäbe es diese Hinweise nicht ---")
    f_nur_mit, r_nur_mit = await lauf(True, wartung_routing=False)
    check("C1) ohne Wartungstreffer: kein Serviceheft-Abruf und keine Serviceheft-Hinweise",
          not any(SERVICEHEFT in c for c in f_nur_mit.extract_calls)
          and not any("15000" in f"{i.titel} {i.beschreibung}".replace(".", "")
                      for i in wartung_insights(r_nur_mit)))

    print("\n--- 4) Fragen und Dokumente aus dem Replay ---")
    a_mit, a_nur = aktionen(r_mit), aktionen(r_nur_mit)
    for n, v in a_mit.items():
        print(f"   {n}: {len(v)} (ohne Wartungstreffer: {len(a_nur[n])})")
    check("D1) Verkäuferfragen und Dokumente werden erzeugt",
          bool(a_mit["verkaeuferfragen"]) and bool(a_mit["dokumente"]))
    fragen_text = " ".join(a_mit["verkaeuferfragen"] + a_mit["dokumente"]).lower()
    neu = {n: [x for x in a_mit[n] if x not in a_nur[n]] for n in ("verkaeuferfragen", "dokumente")}
    check("D2) Wartung fügt je Bereich genau die drei Serviceheft-Punkte hinzu (Frage und Nachweisdokument)",
          len(neu["verkaeuferfragen"]) == 3 and len(neu["dokumente"]) == 3
          and all("wartung-web-" in x for n in neu for x in neu[n]),
          f"{len(neu['verkaeuferfragen'])} Fragen / {len(neu['dokumente'])} Dokumente")
    check("D3) Fragen und Dokumente tragen den Vorbehalt: Webrecherche statt geprüfter Datenbank; "
          "zeitliche Alternative ohne Erstzulassungsmonat offen",
          all("nicht aus der geprüften Fahrzeugdatenbank" in x for n in neu for x in neu[n])
          and any("zeitliche Alternative" in x and "Erstzulassungsmonat" in x for x in neu["verkaeuferfragen"]),
          "")
    check("D4) Fahrzeugangaben nicht verfälscht (477 PS, 2017; keine 464 PS/Ultimate Edition)",
          r_mit.vehicle_identity.get("horsepower") == 477 and r_mit.vehicle_identity.get("year") == 2017
          and not any(x in r_mit.bericht + " ".join(a_mit["verkaeuferfragen"] + a_mit["dokumente"])
                      for x in ("Ultimate Edition", "464", "08/24", "245 PS")))
    k, k0 = r_mit.kaufaktionen, r_nur_mit.kaufaktionen
    check("D5) Basisfragen und Basisdokumente bleiben unverändert",
          len(k.verkaeuferfragen.basis) == len(k0.verkaeuferfragen.basis)
          and len(k.dokumente.basis) == len(k0.dokumente.basis))
    verdraengt = [x for x in a_nur["verkaeuferfragen"] + a_nur["dokumente"]
                  if x not in a_mit["verkaeuferfragen"] + a_mit["dokumente"]]
    print(f"   BEOBACHTUNG (kein Prüfpunkt): durch den bestehenden Deckel MAX_SPEZIFISCH_PRO_BEREICH "
          f"fallen {len(verdraengt)} fahrzeugspezifische Einträge heraus: {[v[:40] for v in verdraengt]}")

    print("\n--- 5) Abnahme mit dem Volltext desselben Servicehefts (nur der Quelltext getauscht) ---")
    f_voll, r_voll = await lauf(True, serviceheft_text=SERVICEHEFT_VOLLTEXT)
    ins_v, a_v = wartung_insights(r_voll), aktionen(r_voll)
    print(f"   Abruftext: Auszug {len(SERVICEHEFT_TEXT)} Zeichen -> Volltext {len(SERVICEHEFT_VOLLTEXT)} Zeichen")
    print(f"   Hinweise Auszug: {sorted(i.titel.split(':')[0] for i in ins)}")
    print(f"   Hinweise Volltext: {sorted(i.titel.split(':')[0] for i in ins_v)}")
    for i in ins_v:
        print(f"   [Volltext] {i.titel.split(':')[0]} | {i.beschreibung[:230]}")
    for n in ("verkaeuferfragen", "dokumente"):
        print(f"   {n} Volltext neu gg. Auszug:",
              [x.split("titel=")[1].split(" aktion=")[0][:70] for x in a_v[n] if x not in a_mit[n] and "wartung-web" in x])
    check("E0) Treffer, Eingaben und Generation gleich: UXC1 bestätigt, dieselben Wartungsanfragen",
          r_voll.vehicle_identity.get("generation") == "UXC1" and wartung_qs(f_voll) == wartung_qs(f_mit))
    # Hinweise des Auszugs, die im Volltext fehlen: nur wegen des bestehenden Auswahllimits (kein Datenverlust
    # durch die Anfrage); der Grund steht in der Ablehnungsliste der unveränderten Extraktion.
    abgelehnt_v: list[dict] = []
    fakten_v = tr._extrahiere_fakten([treffer(SERVICEHEFT, "Lexus Garantie- und Serviceheft", SERVICEHEFT_VOLLTEXT)],
                                     "wartung", marke="Lexus ", modell="RC F", baujahr=2017, abgelehnt=abgelehnt_v)
    limit_sätze = [a["satz"] for a in abgelehnt_v if a["grund"] == "wartung_auswahllimit_erreicht"]
    fehlend = sorted({i.titel.split(":")[0] for i in ins} - {i.titel.split(":")[0] for i in ins_v})
    print(f"   im Volltext fehlend gg. Auszug: {fehlend}; durch Auswahllimit zurückgehalten: {[x[:40] for x in limit_sätze]}")
    check("E1) jeder im Volltext fehlende Auszug-Hinweis ist durch das bestehende Auswahllimit "
          f"(MAX_FAKTEN_JE_KATEGORIE={tr.MAX_FAKTEN_JE_KATEGORIE}) zurückgehalten, nicht verworfen",
          len(fakten_v) == tr.MAX_FAKTEN_JE_KATEGORIE
          and all(any(f.lower() in sz.lower() for sz in limit_sätze) for f in fehlend), str(fehlend))
    check("E2) jeder Hinweis behält Markt-Selbsteinschränkung der Quelle und 'nicht bestätigt'",
          bool(ins_v) and all("nicht bestätigt" in i.beschreibung for i in ins_v), "")
    d = next((i for i in ins_v if i.titel.startswith("Hinterachsdifferenzialöl")), None)
    rm = next((i for i in ins_v if i.titel.startswith("Nebenaggregat-Antriebsriemen")), None)
    check("E3) Differenzialöl (Hinterachse) kommt an: 30.000 km / 4 Jahre, Ausschlussliste der Quelle erhalten (RC F nicht darin)",
          d is not None and "Alle 30.000 km / 4 Jahre" in d.beschreibung and "außer ES 300h" in d.beschreibung
          and "RC F" not in d.beschreibung.split("außer")[1].split(")")[0],
          d.beschreibung[:200] if d else "kein Hinweis")
    check("E4) Nebenaggregat-Antriebsriemen kommt an: 105.000 km / 6 Jahre, Folgeintervall und Ausschlussliste erhalten",
          rm is not None and "Alle 105.000 km / 6 Jahre" in rm.beschreibung
          and "Danach alle 15.000 km / jährlich" in rm.beschreibung and "außer ES 300h" in rm.beschreibung,
          rm.beschreibung[:200] if rm else "kein Hinweis")
    neu_v = {n: [x for x in a_v[n] if x not in a_nur[n]] for n in ("verkaeuferfragen", "dokumente")}
    for stichwort in ("Hinterachsdifferenzialöl", "Nebenaggregat-Antriebsriemen"):
        check(f"E5) {stichwort}: Verkäuferfrage und Nachweisdokument vorhanden, mit Datenbank-Vorbehalt",
              all(any(stichwort in x and "nicht aus der geprüften Fahrzeugdatenbank" in x for x in neu_v[n])
                  for n in neu_v), "")
    ids = lambda liste: {a.id for a in liste}
    kv, k0 = r_voll.kaufaktionen, r_nur_mit.kaufaktionen
    check("E6) Fahrzeugangaben unverändert (477 PS, 2017, keine 464 PS); keine Basisfrage/-dokument geht verloren",
          r_voll.vehicle_identity.get("horsepower") == 477 and r_voll.vehicle_identity.get("year") == 2017
          and not any(x in r_voll.bericht for x in ("464", "Ultimate Edition"))
          and ids(k0.verkaeuferfragen.basis) <= ids(kv.verkaeuferfragen.basis)
          and ids(k0.dokumente.basis) <= ids(kv.dokumente.basis), "")
    weg = sorted(ids(k0.verkaeuferfragen.fahrzeugspezifisch) - ids(kv.verkaeuferfragen.fahrzeugspezifisch))
    print(f"   BEOBACHTUNG (kein Prüfpunkt): bei fünf Wartungspunkten verdrängt der Deckel "
          f"(MAX_SPEZIFISCH_PRO_BEREICH=6) fahrzeugspezifische Verkäuferfragen: {weg}; "
          f"als Basisfrage bleiben davon: {sorted(ids(kv.verkaeuferfragen.basis) - ids(k0.verkaeuferfragen.basis))}")

    if _FEHLER:
        print(f"\n{len(_FEHLER)} FEHLER:")
        for f in _FEHLER:
            print("  -", f)
        raise SystemExit(1)
    print("\nALLE N7-WARTUNGSANFRAGE-TESTS GRUEN")


asyncio.run(main())
