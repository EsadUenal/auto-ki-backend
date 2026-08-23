"""
Technischer Web-Fallback — "DB FIRST, aber niemals DB ONLY".
KEIN Netzwerk, KEIN LLM-Call. Alle Fälle laufen über den
FixtureTechnicalResearchProvider.

Hintergrund (DATA-TRUST-AUDIT): Web war im Kaufcheck kein strukturierter Fallback.
Tavily-Treffer hatten genau zwei Ziele — Preise für die Marktanalyse und Rohtext
für den Prompt. Fehlte das DB-Profil, fehlte die technische Analyse komplett.

  A) sichere DB-Identität              -> KEIN Fallback
  B) DB-Miss + Web belegt Fahrzeug     -> Web-Identität
  C) DB-Miss + Web-Schwachstellen      -> strukturierte Insights
  D) DB-Miss + Web-Rückruf             -> konservatives Recall-Insight
  E) DB-Miss + Web-Wartung             -> strukturierte Wartungs-Evidence
  F) Web-Evidence                      -> fahrzeugspezifische Kaufaktionen
  G) DB-Miss + Fantasiefahrzeug        -> keine fremden DB-Daten
  H) unsicherer DB-Match + anderes Fahrzeug -> falsche DB-Daten nicht verwenden
  I) Motor fehlt + Web bestätigt Motor -> temporärer Motor-Kontext
  J) Providerfehler + DB vorhanden     -> DB-Check funktioniert
  K) Providerfehler + DB fehlt         -> kein Totalabbruch / partial
  L) Web-Evidence hat Quelle + Confidence
  M) keine automatische DB-Mutation
  N) bekannter BMW/Insignia            -> bisheriger Output unverändert
  O) P1-3 Basislisten immer vorhanden
  P) No-Market-Preisstatus unabhängig

    python test_technical_fallback.py
"""
import asyncio
import sqlite3

import app.recall_filter as _rf

# Fixture-Isolation: die KBA-Kollisionsprüfung würde sonst die Live-DB lesen
# (siehe test_kaufaktionen.py) — diese Datei ist bewusst fixture-rein.
_rf.get_rueckruf_referenzen_kurz = lambda: []

from app.car_lookup import find_baureihe_mit_vertrauen, find_motor
from app.config import DB_PATH
from app.evidence import build_insights, valid_evidence_ids
from app.kaufaktionen import build_kaufaktionen
from app.models import KaufCheckRequest, KaufCheckResponse, TechnischeRecherche
from app.technical_research import (
    FixtureTechnicalResearchProvider, TRIGGER_DB_MISS, TRIGGER_IDENTITAET_UNSICHER,
    TRIGGER_MOTOR_FEHLT, TRIGGER_KONFLIKT, MIN_DOMAINS_IDENTITAET,
    fallback_trigger, recherchiere_technisch, technical_coverage,
)

_FEHLER: list[str] = []
BEREICHE = ("besichtigung", "probefahrt", "verkaeuferfragen", "dokumente")


def check(name: str, bedingung: bool) -> None:
    status = "OK  " if bedingung else "FAIL"
    print(f"[{status}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def treffer(url: str, titel: str, inhalt: str) -> dict:
    return {"url": url, "title": titel, "content": inhalt}


# Zwei unabhängige, hinreichend vertrauenswürdige Domains (Fachmedien/Hersteller)
# — die Mindestanforderung von `_identitaet_belegt`.
def fixtures_dacia_duster() -> dict:
    """Reales Fahrzeug, das NICHT in der VIRA-DB liegt."""
    return {
        "identitaet": [
            treffer("https://www.adac.de/dacia-duster-test",
                    "Dacia Duster im Test",
                    "Der Dacia Duster ist ein kompaktes SUV. Der 1.5 dCi Diesel leistet 115 PS."),
            treffer("https://www.autobild.de/dacia-duster",
                    "Dacia Duster Gebrauchtwagen",
                    "Dacia Duster: robustes SUV, verfügbar als Diesel mit 115 PS."),
        ],
        "schwachstelle": [
            treffer("https://www.adac.de/dacia-duster-probleme",
                    "Dacia Duster Schwachstellen",
                    "Beim Dacia Duster ist der Turbolader ein bekanntes Problem. "
                    "Auch die Kupplung zeigt häufig Verschleiss."),
            treffer("https://www.autobild.de/dacia-duster-maengel",
                    "Dacia Duster Mängel",
                    "Am Turbolader treten beim Dacia Duster häufig Defekte auf."),
        ],
        "rueckruf": [
            treffer("https://www.kba.de/rueckruf-dacia-duster",
                    "Rückruf Dacia Duster",
                    "Das KBA meldet einen Rückruf für den Dacia Duster: "
                    "die Bremsen können ausfallen."),
        ],
        "wartung": [
            treffer("https://www.adac.de/dacia-duster-wartung",
                    "Dacia Duster Wartung",
                    "Beim Dacia Duster ist der Zahnriemen alle 120000 km zu wechseln."),
            treffer("https://www.autobild.de/dacia-duster-service",
                    "Dacia Duster Serviceintervall",
                    "Der Zahnriemen sollte alle 120000 km getauscht werden."),
        ],
    }


def fixtures_fantasie() -> dict:
    """"BMW iX7" — die Suche liefert X7-Seiten. Deren Titel/Text enthält "x7",
    aber NICHT das Token "ix7". Genau daran muss die Identitätsprüfung scheitern."""
    return {
        "identitaet": [
            treffer("https://www.adac.de/bmw-x7-test", "BMW X7 im Test",
                    "Der BMW X7 ist ein grosses SUV mit Dieselmotor."),
            treffer("https://www.autobild.de/bmw-x7", "BMW X7 Gebrauchtwagen",
                    "BMW X7: Luxus-SUV der Oberklasse."),
        ],
        "schwachstelle": [
            treffer("https://www.adac.de/bmw-x7-probleme", "BMW X7 Schwachstellen",
                    "Beim BMW X7 ist die Luftfederung ein bekanntes Problem."),
        ],
        "rueckruf": [], "wartung": [],
    }


def lauf(marke, modell, baujahr, motor=None, fixtures=None, fehler=False, **kw):
    """Der deterministische Teil von run_kaufcheck inkl. Identity-Gate + Fallback."""
    req = KaufCheckRequest(marke=marke, modell=modell, baujahr=baujahr, motor=motor, **kw)
    br_markt, info = find_baureihe_mit_vertrauen(marke, modell, baujahr)
    mo_markt = find_motor(br_markt, motor) if br_markt else None
    br, mo = (br_markt, mo_markt) if info["belastbar"] else (None, None)
    provider = FixtureTechnicalResearchProvider(fixtures, fehler=fehler)
    web = asyncio.run(recherchiere_technisch(req, br_markt, info, br, mo, provider=provider))
    ins = build_insights(br, mo, [], req, check_typ="kauf", web_recherche=web)
    ka = build_kaufaktionen(req, br, mo, ins)
    return dict(req=req, info=info, br=br, mo=mo, web=web, ins=ins, ka=ka,
                coverage=technical_coverage(br, web))


def spez(ka):
    return [a for b in BEREICHE for a in getattr(ka, b).fahrzeugspezifisch]


def basis(ka):
    return [a for b in BEREICHE for a in getattr(ka, b).basis]


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== A) Sichere DB-Identität -> KEIN technischer Fallback ===")

_a = lauf("BMW", "320d", 2020, motor="320d", fixtures=fixtures_dacia_duster())
check("A1 kein Fallback ausgelöst", _a["web"] is None)
check("A2 DB-Baureihe verwendet", _a["br"] is not None and _a["br"]["id"] == "bmw-3er-g20-g21")
check("A3 coverage = 'db'", _a["coverage"] == "db")
check("A4 keine Web-Insights", not [i for i in _a["ins"] if i.kategorie.startswith("web_")])
check("A5 Trigger-Funktion liefert None",
      fallback_trigger(_a["req"], _a["br"], _a["info"], _a["br"], _a["mo"]) is None)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== B) DB-Miss + Web belegt reales Fahrzeug -> Web-Identität ===")

_b = lauf("Dacia", "Duster", 2018, motor="1.5 dCi 115 PS", fixtures=fixtures_dacia_duster())
check("B1 DB-Miss (keine Baureihe)", _b["br"] is None)
check("B2 Fallback ausgelöst mit Grund 'db_miss'",
      _b["web"] is not None and _b["web"].ausgeloest_durch == TRIGGER_DB_MISS)
check("B3 Identität als belegt markiert", _b["web"].identitaet.belegt is True)
check("B4 Marke/Modell übernommen",
      _b["web"].identitaet.marke == "Dacia" and _b["web"].identitaet.modell == "Duster")
check("B5 mindestens zwei unabhängige Domains",
      _b["web"].identitaet.belegende_domains >= MIN_DOMAINS_IDENTITAET)
check("B6 Quellen vorhanden", len(_b["web"].identitaet.quellen) >= 2)
check("B7 KEINE erfundene DB-ID", not hasattr(_b["web"].identitaet, "baureihe_id"))
check("B8 coverage = 'web'", _b["coverage"] == "web")
check("B9 Kraftstoff aus Web belegt", _b["web"].identitaet.kraftstoff == "diesel")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== C) DB-Miss + Web-Schwachstellen -> strukturierte Insights ===")

_web_schwach = [i for i in _b["ins"] if i.kategorie == "web_schwachstelle"]
check("C1 Web-Schwachstellen-Insights vorhanden", len(_web_schwach) >= 1)
check("C2 Turbolader erkannt", any("turbolader" in i.titel.lower() for i in _web_schwach))
check("C3 eigene Kategorie (nicht als DB-Schwachstelle getarnt)",
      all(i.kategorie != "schwachstelle" for i in _web_schwach))
check("C4 Quellen tragen typ 'web_technik'",
      all(q.typ == "web_technik" for i in _web_schwach for q in i.quellen))
check("C5 Titel macht die Herkunft sichtbar",
      all("webrecherche" in i.titel.lower() for i in _web_schwach))
check("C6 Einfluss grenzt gegen die geprüfte DB ab",
      all("nicht aus der geprüften" in (i.einfluss or "") for i in _web_schwach))
check("C7 keine DB-Insights aus einem fremden Fahrzeug",
      not [i for i in _b["ins"] if i.kategorie in ("schwachstelle", "motorproblem", "rueckruf")])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== D) DB-Miss + Web-Rückruf -> konservativ ===")

_web_rr = [i for i in _b["ins"] if i.kategorie == "web_rueckruf"]
check("D1 Rückruf-Insight vorhanden", len(_web_rr) == 1)
check("D2 applicability konservativ 'series_only'", _web_rr[0].applicability == "series_only")
check("D3 Quelle ist amtlich (KBA)", any("kba.de" in (q.url or "") for q in _web_rr[0].quellen))
check("D4 keine Betroffenheitsbehauptung",
      "betrifft dein" not in f"{_web_rr[0].titel} {_web_rr[0].beschreibung} {_web_rr[0].einfluss}".lower())
check("D5 Einfluss verlangt FIN-Prüfung", "FIN" in (_web_rr[0].einfluss or ""))

# Ein Rückruf-Treffer von einer NICHT-amtlichen Quelle darf keinen Fakt erzeugen
_fx_forum = fixtures_dacia_duster()
_fx_forum["rueckruf"] = [treffer("https://www.motor-talk.de/thread-123",
                                 "Rückruf beim Duster?",
                                 "Angeblich gibt es einen Rückruf wegen der Bremsen.")]
_d2 = lauf("Dacia", "Duster", 2018, fixtures=_fx_forum)
check("D6 Forum allein erzeugt KEINEN Rückruf-Fakt",
      not [i for i in _d2["ins"] if i.kategorie == "web_rueckruf"])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== E) DB-Miss + Web-Wartung -> strukturierte Evidence ===")

_web_w = [i for i in _b["ins"] if i.kategorie == "web_wartung"]
check("E1 Wartungs-Insight vorhanden", len(_web_w) >= 1)
check("E2 Zahnriemen erkannt", any("zahnriemen" in i.titel.lower() for i in _web_w))
check("E3 Intervall im Text erhalten", any("120000" in i.beschreibung for i in _web_w))
# §22: KEINE Fälligkeitsaussage (P2-5)
_alle_text = " ".join(f"{i.titel} {i.beschreibung} {i.einfluss}" for i in _b["ins"]).lower()
check("E4 keine Fälligkeitsbehauptung",
      not any(w in _alle_text for w in ("ist fällig", "überfällig", "service steht an")))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== F) Web-Evidence -> fahrzeugspezifische Kaufaktionen ===")

_sp_b = spez(_b["ka"])
check("F1 fahrzeugspezifische Aktionen entstehen", len(_sp_b) > 0)
_web_aktionen = [a for a in _sp_b if a.kategorie.startswith("web_")]
check("F2 Web-Aktionen vorhanden", len(_web_aktionen) >= 3)
check("F3 jede Web-Aktion trägt eine gültige Evidence-ID",
      all(a.evidence_ids and set(a.evidence_ids) <= valid_evidence_ids(_b["ins"])
          for a in _web_aktionen))
check("F4 Besichtigungsaktion aus Web-Schwachstelle",
      any(a.bereich == "besichtigung" and a.kategorie == "web_schwachstelle" for a in _sp_b))
check("F5 Verkäuferfrage aus Web-Schwachstelle",
      any(a.bereich == "verkaeuferfragen" and a.kategorie == "web_schwachstelle" for a in _sp_b))
check("F6 Rückruf -> FIN-/Nachweisaktion",
      any(a.kategorie == "web_rueckruf" and "FIN" in a.aktion for a in _sp_b))
check("F7 Rückruf erzeugt KEINE Besichtigungs-/Probefahrtaktion",
      not [a for a in _sp_b if a.kategorie == "web_rueckruf"
           and a.bereich in ("besichtigung", "probefahrt")])
check("F8 Wartung -> Dokument-/Frageaktion",
      {a.bereich for a in _sp_b if a.kategorie == "web_wartung"} == {"verkaeuferfragen", "dokumente"})
check("F9 Herkunft im Aktionstext sichtbar",
      all("webrecherche" in f"{a.titel} {a.aktion}".lower()
          for a in _web_aktionen if a.kategorie == "web_schwachstelle"))
# P1-3-Regel: Probefahrt nur über die bestehenden Tore
_pf = [a for a in _b["ka"].probefahrt.fahrzeugspezifisch]
check("F10 Probefahrt-Aktion nur für Bauteile mit belegtem Fahrsymptom",
      all(a.kategorie in ("web_schwachstelle",) for a in _pf))
check("F11 Turbolader (Tabellensymptom) erzeugt Probefahrt-Aktion",
      any("turbolader" in a.titel.lower() for a in _pf))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== G) Fantasiefahrzeug -> keine fremden DB-Daten ===")

_g = lauf("BMW", "iX7", 2024, fixtures=fixtures_fantasie())
check("G1 Identity-Gate hat den X7 bereits verworfen", _g["br"] is None)
check("G2 Fallback lief", _g["web"] is not None)
check("G3 Identität NICHT belegt", _g["web"].identitaet.belegt is False)
check("G4 keine Web-Fakten trotz X7-Schwachstellentreffern", _g["web"].fakten == [])
check("G5 keine Insights überhaupt", _g["ins"] == [])
check("G6 keine fahrzeugspezifische Technik-Aktion",
      all(a.kategorie == "inserat" for a in spez(_g["ka"])))
check("G7 kein X7-Bezug in irgendeiner Aktion",
      "x7" not in " ".join(f"{a.titel} {a.aktion}" for a in spez(_g["ka"]) + basis(_g["ka"])).lower())
check("G8 Basis-Checklisten vollständig vorhanden",
      all(len(getattr(_g["ka"], b).basis) >= 8 for b in BEREICHE))
check("G9 coverage = 'partial'", _g["coverage"] == "partial")
check("G10 kein Crash, Ergebnis ist ein TechnischeRecherche-Objekt",
      isinstance(_g["web"], TechnischeRecherche))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== H) Unsicherer DB-Match + Web belegt anderes Fahrzeug ===")

# "Golf XV" wird vom Identity-Gate als substring_only gegatet. Der Fixture-Provider
# belegt hier ein anderes, reales Fahrzeug — die (falschen) Golf-VIII-DB-Daten
# dürfen trotzdem nirgends auftauchen.
_fx_h = {
    "identitaet": [
        treffer("https://www.adac.de/dacia-duster-test", "Dacia Duster im Test",
                "Der Dacia Duster ist ein kompaktes SUV mit Diesel."),
        treffer("https://www.autobild.de/dacia-duster", "Dacia Duster",
                "Dacia Duster: robustes SUV."),
    ],
    "schwachstelle": [], "rueckruf": [], "wartung": [],
}
_h = lauf("Volkswagen", "Golf XV", 2022, fixtures=_fx_h)
check("H1 Identity-Gate hat gegatet", _h["br"] is None and not _h["info"]["belastbar"])
check("H2 Fallback-Grund = identitaet_unsicher",
      _h["web"].ausgeloest_durch == TRIGGER_IDENTITAET_UNSICHER)
check("H3 Golf-XV-Identität NICHT belegt (Token 'xv' fehlt in den Treffern)",
      _h["web"].identitaet.belegt is False)
check("H4 keine Golf-VIII-DB-Insights", _h["ins"] == [])
check("H5 keine Golf-Aktion aus falscher DB-Zuordnung",
      all(a.kategorie == "inserat" for a in spez(_h["ka"])))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== I) Motor fehlt + Web bestätigt Motor ===")

# Baureihe sicher (BMW 3er G20), Nutzer nennt einen Motor, den die DB nicht kennt.
_fx_i = {
    "identitaet": [
        treffer("https://www.adac.de/bmw-3er-g20", "BMW 3er G20 technische Daten",
                "Der BMW 3er ist als 318d mit 150 PS erhältlich."),
        treffer("https://www.autobild.de/bmw-3er", "BMW 3er",
                "BMW 3er: der 318d leistet 150 PS als Diesel."),
    ],
    "schwachstelle": [], "rueckruf": [], "wartung": [],
}
_i = lauf("BMW", "3er", 2020, motor="318d 150 PS Nutzfahrzeugvariante", fixtures=_fx_i)
check("I1 DB-Baureihe sicher erkannt", _i["br"] is not None)
check("I2 DB-Motor NICHT erkannt", _i["mo"] is None)
check("I3 Fallback-Grund = motor_fehlt", _i["web"].ausgeloest_durch == TRIGGER_MOTOR_FEHLT)
check("I4 Web-Identität belegt", _i["web"].identitaet.belegt is True)
check("I5 Motorangabe des Nutzers als temporärer Kontext übernommen",
      _i["web"].identitaet.motor is not None)
check("I6 Leistung nur übernommen, weil sie in der Nutzerangabe steht",
      _i["web"].identitaet.leistung_ps == 150)
check("I7 coverage = 'db_plus_web'", _i["coverage"] == "db_plus_web")
check("I8 DB-Schwachstellen der Baureihe bleiben erhalten",
      len([x for x in _i["ins"] if x.kategorie == "schwachstelle"]) > 0)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== J/K) Providerfehler ===")

_j = lauf("BMW", "3er", 2020, motor="318d 150 PS Nutzfahrzeugvariante", fehler=True)
check("J1 Providerfehler gemeldet, keine Exception", _j["web"].provider_fehler is True)
check("J2 DB-Pfad funktioniert weiter", _j["br"] is not None)
check("J3 DB-Insights vorhanden", len([i for i in _j["ins"] if i.kategorie == "schwachstelle"]) > 0)
check("J4 fahrzeugspezifische DB-Aktionen vorhanden",
      any(a.kategorie == "schwachstelle" for a in spez(_j["ka"])))
check("J5 coverage bleibt 'db' (kein Web-Beitrag)", _j["coverage"] == "db")

_k = lauf("Dacia", "Duster", 2018, fehler=True)
check("K1 kein Totalabbruch bei DB-Miss + Providerfehler", _k["web"].provider_fehler is True)
check("K2 keine Identität erfunden", _k["web"].identitaet is None)
check("K3 keine Insights", _k["ins"] == [])
check("K4 coverage = 'partial'", _k["coverage"] == "partial")
check("K5 Basis-Checklisten trotzdem vollständig",
      all(len(getattr(_k["ka"], b).basis) >= 8 for b in BEREICHE))
check("K6 Nutzerangaben bleiben sichtbar",
      any(a.kategorie == "inserat" for a in spez(_k["ka"])))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== L) Web-Evidence hat Quelle + Confidence ===")

_web_ins = [i for i in _b["ins"] if i.kategorie.startswith("web_")]
check("L1 jeder Web-Insight hat mindestens eine Quelle mit URL",
      all(i.quellen and all(q.url for q in i.quellen) for i in _web_ins))
check("L2 jeder Web-Insight hat eine Confidence",
      all(i.confidence in ("hoch", "mittel", "niedrig") for i in _web_ins))
check("L3 Confidence steigt mit unabhängigen Domains (Turbolader: 2 Domains)",
      any(i.confidence == "mittel" for i in _web_ins if "turbolader" in i.titel.lower()))
check("L4 Einzelquelle bleibt 'niedrig'",
      all(i.confidence == "niedrig" for i in _web_ins if len(i.quellen) == 1))
check("L5 Quellen tragen ein Qualitätslabel", all(q.qualitaet for i in _web_ins for q in i.quellen))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== M) Keine automatische DB-Mutation ===")


def _zaehle(tabelle: str) -> int:
    with sqlite3.connect(str(DB_PATH)) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {tabelle}").fetchone()[0]


_vorher = {t: _zaehle(t) for t in ("baureihe", "motorvariante", "schwachstelle_baureihe",
                                   "rueckruf", "kritische_wartung")}
lauf("Dacia", "Duster", 2018, motor="1.5 dCi 115 PS", fixtures=fixtures_dacia_duster())
_nachher = {t: _zaehle(t) for t in _vorher}
check(f"M1 keine Tabelle verändert ({_vorher} -> {_nachher})", _vorher == _nachher)
_quelltext = open("app/technical_research.py", encoding="utf-8").read()
check("M2 das Modul führt kein INSERT/UPDATE/DELETE aus",
      not any(w in _quelltext.upper() for w in ("INSERT INTO", "UPDATE ", "DELETE FROM")))
check("M3 das Modul importiert keinen DB-Writer",
      "db_writer" not in _quelltext and "get_conn" not in _quelltext)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== N) Bekannte Fahrzeuge unverändert ===")

# "Mercedes-Benz C 200" stand hier zunaechst als Sonderfall: `find_baureihe`
# normalisierte die Eingabe ("c 200" -> "c200") und fand die Baureihe, `find_motor`
# verglich dagegen rohe Strings und fand die DB-Variante "C200" NICHT — der Motor
# galt als unbekannt und loeste unnoetig `motor_fehlt` aus. Diese Luecke ist mit
# der Motor-Normalisierung geschlossen (`find_motor` prueft jetzt zuerst auf
# normalisierte GLEICHHEIT), weshalb der Fall hier in die regulaere Liste gehoert.
OHNE_FALLBACK = (
    ("BMW", "320d", 2020, "320d", "bmw-3er-g20-g21"),
    ("Opel", "Insignia", 2020, "2.0 Diesel 174 PS", "opel-insignia-b"),
    ("Volkswagen", "Golf", 2015, "1.4 TSI", "volkswagen-golf-vii"),
    ("Audi", "A4", 2018, "2.0 TDI", "audi-a4-b9"),
    ("Mercedes-Benz", "C 200", 2019, "C 200", "mercedes-benz-c-klasse-w205"),
)
for marke, modell, bj, mot, erwartet in OHNE_FALLBACK:
    _n = lauf(marke, modell, bj, motor=mot, fixtures=fixtures_dacia_duster())
    check(f"N1 {marke} {modell}: Baureihe unverändert", (_n["br"] or {}).get("id") == erwartet)
    check(f"N2 {marke} {modell}: KEIN Fallback (keine Zusatzlatenz)", _n["web"] is None)
    check(f"N3 {marke} {modell}: coverage 'db'", _n["coverage"] == "db")
    check(f"N4 {marke} {modell}: keine Web-Insights",
          not [i for i in _n["ins"] if i.kategorie.startswith("web_")])
    check(f"N5 {marke} {modell}: keine Web-Aktionen",
          not [a for a in spez(_n["ka"]) if a.kategorie.startswith("web_")])

# Mercedes W205 im Detail: der Motor wird jetzt erkannt, deshalb entfaellt der
# Fallback vollstaendig — und die DB-Daten stehen unveraendert zur Verfuegung.
_n_mb = lauf("Mercedes-Benz", "C 200", 2019, motor="C 200", fixtures=fixtures_dacia_duster())
check("N1b Mercedes W205: Motor jetzt erkannt",
      _n_mb["mo"] is not None and _n_mb["mo"]["bezeichnung"] == "C200")
check("N2b Mercedes W205: KEIN Fallback mehr (Normalisierungsluecke geschlossen)",
      _n_mb["web"] is None)
check("N3b Mercedes W205: DB-Schwachstellen bleiben erhalten",
      len([i for i in _n_mb["ins"] if i.kategorie == "schwachstelle"]) > 0)
check("N4b Mercedes W205: keine fremden Web-Insights",
      not [i for i in _n_mb["ins"] if i.kategorie.startswith("web_")])
check("N5b Mercedes W205: coverage 'db'", _n_mb["coverage"] == "db")

# Vergleich gegen den Lauf OHNE Fallback-Parameter: identisches Ergebnis
_n_ref = lauf("BMW", "320d", 2020, motor="320d")
_n_fx = lauf("BMW", "320d", 2020, motor="320d", fixtures=fixtures_dacia_duster())
check("N7 Ergebnis identisch mit und ohne verfügbare Fixtures",
      [i.model_dump() for i in _n_ref["ins"]] == [i.model_dump() for i in _n_fx["ins"]])
check("N8 Kaufaktionen identisch", _n_ref["ka"].model_dump() == _n_fx["ka"].model_dump())


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== O/P) Basislisten und Preisunabhängigkeit ===")

for name, ergebnis in (("DB-Treffer", _a), ("Web-Fahrzeug", _b), ("Fantasie", _g),
                       ("Providerfehler", _k)):
    check(f"O1 {name}: alle vier Basislisten vorhanden",
          all(len(getattr(ergebnis["ka"], b).basis) > 0 for b in BEREICHE))
check("O2 Basislisten im Fantasiefall vollständig",
      len(_g["ka"].probefahrt.basis) >= 15 and len(_g["ka"].besichtigung.basis) >= 12)

_MARKT = ("preis", "€", "eur", "günstig", "teuer", "schnäppchen", "median",
          "marktwert", "marktgerecht", "nachverhandel")
_web_text = " ".join(f"{i.titel} {i.beschreibung} {i.einfluss}" for i in _web_ins).lower()
_treffer_markt = [w for w in _MARKT if w in _web_text]
check(f"P1 keine Preisaussage in der Web-Evidence ({_treffer_markt})", _treffer_markt == [])
check("P2 die Recherche kennt keinen Preisparameter",
      "preis" not in FixtureTechnicalResearchProvider.recherchiere.__code__.co_varnames)
check("P3 TechnischeRecherche trägt kein Preisfeld",
      not any("preis" in f or "markt" in f for f in TechnischeRecherche.model_fields))
_ka_text = " ".join(f"{a.titel} {a.aktion}" for a in spez(_b["ka"])).lower()
check("P4 keine Preisaussage in den Web-Kaufaktionen",
      not any(w in _ka_text for w in ("günstig", "teuer", "schnäppchen", "nachverhandel")))
check("P5 Response-Defaults für Alt-Checks",
      KaufCheckResponse(bericht="x", empfehlung="kaufen", preis_bewertung="unbekannt",
                        quelle="db", vertrauen="hoch").technical_coverage == "db")
check("P6 web_identitaet ist optional",
      KaufCheckResponse.model_fields["web_identitaet"].is_required() is False)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== Q) Trigger-Matrix ===")

_req_konflikt = KaufCheckRequest(marke="BMW", modell="320d", baujahr=2020,
                                 motor="320d", kraftstoff="Benzin")
_br_q, _info_q = find_baureihe_mit_vertrauen("BMW", "320d", 2020)
_mo_q = find_motor(_br_q, "320d")
check("Q1 Kraftstoff-Widerspruch löst Fallback aus",
      fallback_trigger(_req_konflikt, _br_q, _info_q, _br_q, _mo_q) == TRIGGER_KONFLIKT)
check("Q2 ohne Marke/Modell kein Fallback",
      fallback_trigger(KaufCheckRequest(baujahr=2020), None, _info_q, None, None) is None)
check("Q3 vollständiger DB-Treffer ohne Konflikt -> kein Fallback",
      fallback_trigger(KaufCheckRequest(marke="BMW", modell="320d", baujahr=2020, motor="320d"),
                       _br_q, _info_q, _br_q, _mo_q) is None)


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE TECHNICAL-FALLBACK-TESTS GRUEN")
