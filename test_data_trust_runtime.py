# -*- coding: utf-8 -*-
"""
DATA-SAFETY-RUNTIME-GATE (P0) — Motor-Applicability + Trust-Semantik.
KEIN Netzwerk, KEIN LLM-Call, KEIN Tavily.

Grundlage: DATA-TRUTH-AUDIT. Geprüft werden die fünf Zusicherungen des Gates:

  A) Motor-Applicability: die drei reproduzierten False Positives verschwinden
  B) Motor-Applicability: echte Treffer bleiben erhalten (kein Kahlschlag)
  C) Trust: unverifizierte DB-Fakten erzeugen weiter Hinweise, aber keinen Floor
  D) Source-Labels: kein "(geprüft)" ohne hinterlegte Verifikation
  E) Wartungs-Wortlaut: kein "Vorgesehenes Intervall" ohne Verifikation

    python test_data_trust_runtime.py
"""
import os
import sys
import tempfile

os.environ.setdefault("AUTO_KI_DB_PATH_KEEP", "1")
sys.path.insert(0, ".")

from app.evidence import build_insights                    # noqa: E402
from app.kaufaktionen import build_kaufaktionen            # noqa: E402
from app.empfehlungs_floor import darf_floor_tragen, ermittle_floor  # noqa: E402
import app.recall_filter as _rf                            # noqa: E402
# Der Kollisionsindex des KBA-Trust-Gates liest sonst die LIVE-DB: die Referenz
# "009696" ist dort bei BMW vergeben und wuerde gegen die Testmarke als
# markenuebergreifende Kollision gelten. Der Index wird deshalb geleert -- die
# FORMATpruefung des Gates bleibt dabei voll wirksam.
_rf.get_rueckruf_referenzen_kurz = lambda: []

from app.motor_applicability import (                      # noqa: E402
    schwachstelle_applicability, gefilterte_schwachstellen,
    ausgeschlossene_schwachstellen, KOMPATIBEL, UNKLAR, INKOMPATIBEL,
)

FEHLER: list[str] = []
BEREICHE = ("besichtigung", "probefahrt", "verkaeuferfragen", "dokumente")


def check(name, bedingung):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        FEHLER.append(name)


class Req:
    def __init__(self, **kw):
        for f in ("marke", "modell", "baujahr", "kilometerstand", "motor", "kraftstoff",
                  "preis_eur", "beschreibung", "freitext", "unfallfrei", "vorbesitzer",
                  "tuev_bis", "scheckheftgepflegt"):
            setattr(self, f, kw.get(f))
        self.ausstattung = kw.get("ausstattung") or []


VERIFIKATION = {f: {"status": "verified", "source": "https://example.test/nachweis",
                    "date": "2026-08-24"}
                for f in ("schwachstellen", "motorprobleme", "rueckrufe", "wartung")}


def baureihe(schwachstellen=None, motoren=None, rueckrufe=None, verified=False):
    br = {
        "id": "test-baureihe", "marke": "TestMarke", "modell": "TestModell",
        "generation": "G1", "bauzeitraum_von": 2005, "bauzeitraum_bis": 2015,
        "karosserie": [], "tuev_maengelquote": None, "adac_pannenkennziffer": None,
        "ausstattungslinien": [], "motoren": motoren or [],
        "schwachstellen_baureihe": schwachstellen or [], "rueckrufe": rueckrufe or [],
    }
    if verified:
        br["verification"] = VERIFIKATION
    return br


def mot(variante_id, bezeichnung, kraftstoff="Benzin", zylinder=4, motorcode="",
        schwachstellen_motor=None, kritische_wartung=None):
    return {"variante_id": variante_id, "bezeichnung": bezeichnung, "motorcode": motorcode,
            "kraftstoff": kraftstoff, "zylinder": zylinder, "leistung_ps": 150,
            "leistung_kw": 110, "schwachstellen_motor": schwachstellen_motor or [],
            "kritische_wartung": kritische_wartung or []}


def sw(bauteil, schweregrad="hoch", beschreibung="Beschreibung.", baujahre="Alle"):
    return {"bauteil": bauteil, "beschreibung": beschreibung,
            "betroffene_baujahre": baujahre, "schweregrad": schweregrad}


# ══════════════════════════════════════════════════════════════════════════════
print("=== A) Die drei reproduzierten False Positives (synthetisch) ===")

# A) BMW 320i F30 — "Steuerkette (N47 Dieselmotoren)" an einem Benziner.
br_a = baureihe(
    [sw("Steuerkette (N47 Dieselmotoren)"), sw("Turbolader (N20/N26 Benzinmotoren)", "mittel")],
    motoren=[mot("320i", "320i", "Benzin"), mot("320d", "320d", "Diesel")])
m_320i = br_a["motoren"][0]
check("A1 N47-Dieselschwachstelle ist am Benziner incompatible",
      schwachstelle_applicability(br_a["schwachstellen_baureihe"][0], m_320i, br_a)[0] == INKOMPATIBEL)
check("A2 Ausschlussgrund ist der Kraftstoff-Widerspruch",
      schwachstelle_applicability(br_a["schwachstellen_baureihe"][0], m_320i, br_a)[1]
      == "kraftstoff_widerspruch")
check("A3 die Benzin-Schwachstelle derselben Baureihe bleibt",
      schwachstelle_applicability(br_a["schwachstellen_baureihe"][1], m_320i, br_a)[0] == KOMPATIBEL)
ins_a = build_insights(br_a, m_320i, [], Req(baujahr=2014), check_typ="kauf")
titel_a = [i.titel for i in ins_a]
check("A4 keine Evidence aus der Dieselschwachstelle",
      not any("N47" in t for t in titel_a))
check("A5 die passende Schwachstelle erzeugt weiterhin Evidence",
      any("N20/N26" in t for t in titel_a))
ka_a = build_kaufaktionen(Req(baujahr=2014), br_a, m_320i, ins_a)
alle_a = [a for b in BEREICHE for a in getattr(ka_a, b).fahrzeugspezifisch]
check("A6 keine fahrzeugspezifische Aktion aus der Dieselschwachstelle",
      not any("N47" in a.titel for a in alle_a))
check("A7 kein Floor aus der Dieselschwachstelle", ermittle_floor(ins_a) is None)

# B) BMW 525i E60 — "V10-Motor (M5)" an einem Sechszylinder.
br_b = baureihe([sw("V10-Motor (M5)")],
                motoren=[mot("525i", "525i", "Benzin", zylinder=6),
                         mot("m5", "M5", "Benzin", zylinder=10)])
m_525i = br_b["motoren"][0]
appl_b, grund_b = schwachstelle_applicability(br_b["schwachstellen_baureihe"][0], m_525i, br_b)
check("B1 V10-Schwachstelle ist am R6 incompatible", appl_b == INKOMPATIBEL)
check("B2 Ausschlussgrund ist der Zylinder-Widerspruch", grund_b == "zylinder_widerspruch")
check("B3 am echten V10 bleibt sie erhalten",
      schwachstelle_applicability(br_b["schwachstellen_baureihe"][0],
                                  br_b["motoren"][1], br_b)[0] == KOMPATIBEL)
ins_b = build_insights(br_b, m_525i, [], Req(baujahr=2006), check_typ="kauf")
check("B4 keine Evidence, keine Aktion, kein Floor",
      not any("V10" in i.titel for i in ins_b) and ermittle_floor(ins_b) is None)

# C) Audi A3 1.6 MPI — "Turbolader (TFSI-Motoren)" an einem Saugmotor.
br_c = baureihe([sw("Turbolader (TFSI-Motoren)")],
                motoren=[mot("1.6", "1.6 (102 PS)", "Benzin", motorcode="BGU, BSE, BSF"),
                         mot("1.8t", "1.8 TFSI (160 PS)", "Benzin", motorcode="BYT"),
                         mot("2.0fsi", "2.0 FSI (150 PS)", "Benzin", motorcode="AXW")])
m_16, m_18t, m_20fsi = br_c["motoren"]
appl_c, grund_c = schwachstelle_applicability(br_c["schwachstellen_baureihe"][0], m_16, br_c)
check("C1 TFSI-Schwachstelle ist am 1.6 MPI incompatible", appl_c == INKOMPATIBEL)
check("C2 Ausschlussgrund ist der Kuerzel-Kontrast in der Baureihe",
      grund_c == "kuerzel_kontrast")
check("C3 am echten 1.8 TFSI bleibt sie erhalten",
      schwachstelle_applicability(br_c["schwachstellen_baureihe"][0], m_18t, br_c)[0] == KOMPATIBEL)
check("C4 '2.0 FSI' gilt NICHT als TFSI (Wortgrenze, Audit-P0)",
      schwachstelle_applicability(br_c["schwachstellen_baureihe"][0], m_20fsi, br_c)[0] == INKOMPATIBEL)
ins_c = build_insights(br_c, m_16, [], Req(baujahr=2008), check_typ="kauf")
ka_c = build_kaufaktionen(Req(baujahr=2008), br_c, m_16, ins_c)
alle_c = [a for b in BEREICHE for a in getattr(ka_c, b).fahrzeugspezifisch]
check("C5 keine Evidence aus der TFSI-Schwachstelle",
      not any("TFSI" in i.titel for i in ins_c))
check("C6 keine kritisch-priorisierte Turbolader-Aktion",
      not any("Turbolader" in a.titel for a in alle_c))
check("C7 kein Floor", ermittle_floor(ins_c) is None)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== B) Kein Kahlschlag: erhaltende Faelle ===")

check("D1 Schwachstelle ohne jeden Motor-Scope bleibt",
      schwachstelle_applicability(sw("Bremsen"), m_320i, br_a)[0] == KOMPATIBEL)
check("D2 'Fahrwerk (Vorderachse)' ist kein Motor-Scope",
      schwachstelle_applicability(sw("Fahrwerk (Vorderachse)"), m_320i, br_a)[0] == KOMPATIBEL)
check("D3 'Getriebe (Automatik)' ist kein Motor-Scope",
      schwachstelle_applicability(sw("Getriebe (Automatik)"), m_320i, br_a)[0] == KOMPATIBEL)
# Der Smoke-Test-Fund: 'Elektronik' enthaelt den Teilstring 'elektro'.
check("D4 'Elektronik/Infotainment' wird NICHT als Elektro-Scope gelesen",
      schwachstelle_applicability(sw("Elektronik/Infotainment"), m_320i, br_a)[0] == KOMPATIBEL)
check("D5 'Elektronik (allgemein)' ebenso",
      schwachstelle_applicability(sw("Elektronik (allgemein)"), m_320i, br_a)[0] == KOMPATIBEL)
# Explizites Kraftstoffwort schlaegt den Kuerzel-Kontrast (Insignia-Fall).
br_e = baureihe([sw("Dieselmotoren (1.6 CDTI, 2.0 CDTI)")],
                motoren=[mot("d20", "2.0 Diesel (174 PS)", "Diesel"),
                         mot("d16", "1.6 CDTI (136 PS)", "Diesel")])
check("D6 explizites 'Dieselmotoren' belegt Zugehoerigkeit trotz fehlendem CDTI-Kuerzel",
      schwachstelle_applicability(br_e["schwachstellen_baureihe"][0],
                                  br_e["motoren"][0], br_e)[0] == KOMPATIBEL)
check("D7 ohne erkannten Motor wird NIE ausgeschlossen (nur unklar)",
      schwachstelle_applicability(sw("Steuerkette (N47 Dieselmotoren)"), None, br_a)[0] == UNKLAR)
check("D8 eine beilaeufige Fliesstext-Erwaehnung schliesst nicht aus",
      schwachstelle_applicability(
          sw("Zweimassenschwungrad", beschreibung="Besonders bei Dieselmotoren bekannt."),
          m_320i, br_a)[0] == KOMPATIBEL)
check("D9 Kuerzel-Kontrast braucht mindestens zwei Varianten in der Baureihe",
      schwachstelle_applicability(sw("Turbolader (TFSI-Motoren)"), m_16,
                                  baureihe([], motoren=[m_16]))[0] == KOMPATIBEL)
check("D10 gefilterte/ausgeschlossene Listen sind komplementaer",
      len(gefilterte_schwachstellen(br_c["schwachstellen_baureihe"], m_16, br_c))
      + len(ausgeschlossene_schwachstellen(br_c["schwachstellen_baureihe"], m_16, br_c))
      == len(br_c["schwachstellen_baureihe"]))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== C) Trust: Hinweise bleiben, harte Wirkung nicht ===")

MOTORPROBLEM = [{"bauteil": "Zuendspulen", "beschreibung": "Aussetzer moeglich.",
                 "baujahre": None, "kosten_ca": "400"}]
WARTUNG = [{"bauteil": "Steuerkette", "intervall": "Sichtpruefung ab 100.000 km",
            "hinweis": "Auf Rasseln beim Kaltstart achten."}]
RUECKRUF = [{"datum": "2010-03", "betroffene_baujahre": "2008-2012",
             "mangel": "Moeglicher Ausfall der Bremskraftunterstuetzung",
             "abhilfe": "Software-Update", "kba_referenz": "009696"}]

m_trust = mot("m1", "2.0 TFSI", "Benzin", motorcode="CCZ",
              schwachstellen_motor=MOTORPROBLEM, kritische_wartung=WARTUNG)
br_unver = baureihe([sw("Turbolader", "hoch")], motoren=[m_trust], rueckrufe=RUECKRUF)
br_ver = baureihe([sw("Turbolader", "hoch")], motoren=[m_trust], rueckrufe=RUECKRUF,
                  verified=True)
req_t = Req(baujahr=2010)

ins_u = build_insights(br_unver, m_trust, [], req_t, check_typ="kauf")
ins_v = build_insights(br_ver, m_trust, [], req_t, check_typ="kauf")

check("E1 unverifiziert: gleiche Anzahl Insights wie verifiziert (nichts geloescht)",
      len(ins_u) == len(ins_v) and len(ins_u) >= 4)
check("E2 unverifiziert: alle DB-Insights tragen trust=unverified_db",
      all(i.trust == "unverified_db" for i in ins_u
          if i.kategorie in ("schwachstelle", "motorproblem", "rueckruf", "wartung")))
check("E3 verifiziert: alle DB-Insights tragen trust=verified",
      all(i.trust == "verified" for i in ins_v
          if i.kategorie in ("schwachstelle", "motorproblem", "rueckruf", "wartung")))
check("E4 unverifiziert: KEIN Floor", ermittle_floor(ins_u) is None)
check("E5 verifiziert: Floor greift", ermittle_floor(ins_v) is not None)

ka_u = build_kaufaktionen(req_t, br_unver, m_trust, ins_u)
ka_v = build_kaufaktionen(req_t, br_ver, m_trust, ins_v)
spez_u = [a for b in BEREICHE for a in getattr(ka_u, b).fahrzeugspezifisch]
spez_v = [a for b in BEREICHE for a in getattr(ka_v, b).fahrzeugspezifisch]
check("E6 unverifiziert: die Pruefpunkte bleiben vollstaendig erhalten",
      len(spez_u) == len(spez_v) and len(spez_u) > 0)
check("E7 unverifiziert: die kritische Prioritaet der Schwachstelle bleibt",
      any(a.prioritaet == "kritisch" for a in spez_u))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== D) Source-Labels ===")

def titel_von(insights, kategorie):
    treffer = [i for i in insights if i.kategorie == kategorie]
    return treffer[0].quellen[0].titel if treffer and treffer[0].quellen else ""

check("F1 unverifiziert: Schwachstellen-Quelle ohne '(geprueft)'",
      titel_von(ins_u, "schwachstelle") == "VIRA-Fahrzeugdatenbank")
check("F2 unverifiziert: Motorvarianten-Quelle ohne '(geprueft)'",
      titel_von(ins_u, "motorproblem") == "VIRA-Motorvariantendaten")
check("F3 unverifiziert: Wartungs-Quelle ohne '(geprueft)'",
      titel_von(ins_u, "wartung") == "VIRA-Wartungsdaten")
check("F4 in KEINEM unverifizierten Quellentitel steht 'geprueft'",
      not any("geprüft" in (q.titel or "").lower()
              for i in ins_u for q in i.quellen))
check("F5 verifiziert: '(geprueft)' kommt zurueck",
      titel_von(ins_v, "schwachstelle") == "VIRA-Fahrzeugdatenbank (geprüft)")

rr_u = [i for i in ins_u if i.kategorie == "rueckruf"][0]
rr_v = [i for i in ins_v if i.kategorie == "rueckruf"][0]
check("G1 unverifiziert: Quelle nennt sich NICHT KBA-Rueckrufdatenbank",
      "KBA-Rückrufdatenbank" not in (rr_u.quellen[0].titel or ""))
check("G2 unverifiziert: Titel sagt 'Rueckrufhinweis', nicht 'KBA-Rueckruf'",
      rr_u.titel.startswith("Rückrufhinweis"))
check("G3 unverifiziert: keine scheinbar amtliche Nummer im Evidence-ref",
      rr_u.quellen[0].ref is None)
check("G4 unverifiziert: die Nummer taucht auch in keiner Kaufaktion auf",
      not any("009696" in (a.aktion or "") for a in spez_u))
check("G5 verifiziert: KBA-Bezeichnung und Nummer kommen zurueck",
      rr_v.titel.startswith("KBA-Rückruf") and rr_v.quellen[0].ref == "009696")
check("G6 der Rueckrufinhalt bleibt in beiden Faellen vollstaendig erhalten",
      "Bremskraftunterst" in rr_u.beschreibung and "Bremskraftunterst" in rr_v.beschreibung)
check("G7 unverifiziert: die FIN-Pruefung wird weiterhin empfohlen",
      any("FIN" in (a.aktion or "") for a in spez_u))
# RECALL-PILOT §9: Bis hierher galt "Applicability unabhaengig von der
# Verifikation" — eine bloss FORMATPLAUSIBLE Nummer hob jeden Rueckruf auf
# "variant_match". Genau diese Gleichsetzung von Plausibilitaet und Beleg ist
# aufgehoben. Der Rueckruf bleibt in BEIDEN Faellen vollstaendig sichtbar (G6);
# nur die Vertrauensstufe haengt jetzt am Beleg.
check("G8 unverifiziert: die formatplausible Nummer hebt die Stufe NICHT mehr",
      rr_u.applicability == "series_only")
check("G9 verifiziert: dieselbe Nummer hebt sehr wohl auf variant_match",
      rr_v.applicability == "variant_match")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== E) Wartungs-Wortlaut ===")

w_u = [i for i in ins_u if i.kategorie == "wartung"][0]
w_v = [i for i in ins_v if i.kategorie == "wartung"][0]
check("H1 unverifiziert: kein 'Vorgesehenes Intervall'",
      "Vorgesehenes Intervall" not in w_u.beschreibung)
check("H2 unverifiziert: neutraler Wortlaut 'Hinterlegter Wartungshinweis'",
      "Hinterlegter Wartungshinweis: Sichtpruefung ab 100.000 km." in w_u.beschreibung)
check("H3 verifiziert: praeziser Wortlaut kommt zurueck",
      "Vorgesehenes Intervall: Sichtpruefung ab 100.000 km." in w_v.beschreibung)
check("H4 P2-5 bleibt: keine Faelligkeits-Behauptung",
      not any(w in w_u.beschreibung.lower() for w in ("fällig", "überfällig", "faellig")))
check("H5 der Wartungshinweis selbst bleibt erhalten",
      "Rasseln beim Kaltstart" in w_u.beschreibung)


# ══════════════════════════════════════════════════════════════════════════════
# §3: dieselben drei Faelle noch einmal gegen die ECHTE Fahrzeugdatenbank. Die
# synthetischen Faelle oben pruefen die Gate-Logik; hier wird geprueft, dass die
# im Audit reproduzierten Befunde im realen Bestand tatsaechlich verschwunden
# sind. Fehlt die DB (frisches Setup, CI), wird der Abschnitt sauber uebersprungen.
print("\n=== F) Die drei Audit-Faelle gegen die echte DB ===")

try:
    from app.car_lookup import find_baureihe_mit_vertrauen, find_motor
    _probe = find_baureihe_mit_vertrauen("BMW", "3er", 2014)[0]
except Exception as exc:                                   # pragma: no cover
    _probe = None
    print(f"[SKIP] Fahrzeugdatenbank nicht verfuegbar ({exc.__class__.__name__})")

if not _probe:
    print("[SKIP] keine Baureihendaten - Abschnitt F uebersprungen")
else:
    REALFAELLE = [
        ("BMW 320i F30", "BMW", "3er", 2014, "320i", "N47"),
        ("BMW 525i E60", "BMW", "5er", 2006, "525i", "V10"),
        ("Audi A3 1.6 MPI", "Audi", "A3", 2008, "1.6 102 PS", "TFSI"),
        ("Audi A3 2.0 FSI", "Audi", "A3", 2008, "2.0 FSI 150 PS", "TFSI"),
    ]
    for name, marke, modell, bj, motor_hint, verbotenes_kuerzel in REALFAELLE:
        br, _info = find_baureihe_mit_vertrauen(marke, modell, bj)
        mm = find_motor(br, motor_hint) if br else None
        if not br or not mm:
            print(f"[SKIP] {name}: nicht aufloesbar")
            continue
        req = Req(marke=marke, modell=modell, baujahr=bj, motor=motor_hint)
        ins = build_insights(br, mm, [], req, check_typ="kauf")
        ka = build_kaufaktionen(req, br, mm, ins)
        spez = [a for b in BEREICHE for a in getattr(ka, b).fahrzeugspezifisch]
        check(f"F {name}: '{verbotenes_kuerzel}' erscheint in keiner Evidence",
              not any(verbotenes_kuerzel in i.titel for i in ins
                      if i.kategorie == "schwachstelle"))
        check(f"F {name}: '{verbotenes_kuerzel}' erscheint in keiner Kaufaktion",
              not any(verbotenes_kuerzel in a.titel for a in spez))
        # BATCH A: seit dem Import amtlicher KBA-Rueckrufe koennen BMW 3er F30
        # und 5er E60 sehr wohl einen Floor tragen — aber ausschliesslich ueber
        # VERIFIZIERTE Fakten (Quelle KBA, Stufe A). Die zu sichernde Aussage
        # ist deshalb nicht mehr "gar kein Floor", sondern die urspruengliche:
        # kein Floor aus UNVERIFIZIERTEN Daten. Geprueft wird das jetzt direkt
        # an den belegenden Insights statt indirekt an ihrer Abwesenheit.
        _floor = ermittle_floor(ins)
        _traeger = ([i for i in ins if i.id in set(_floor.evidence_ids)]
                    if _floor else [])
        if _floor is not None:
            print(f"       (Floor {_floor.stufe} getragen von "
                  f"{[(i.kategorie, getattr(i, 'trust', None)) for i in _traeger]})")
        check(f"F {name}: kein Floor aus unverifizierten DB-Daten",
              _floor is None or (bool(_traeger)
                                 and all(darf_floor_tragen(i) for i in _traeger)))
        check(f"F {name}: der Check liefert trotzdem Pruefpunkte (kein Kahlschlag)",
              len(spez) > 0)


# ══════════════════════════════════════════════════════════════════════════════
print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER: " + ", ".join(FEHLER))
    raise SystemExit(1)
print("Alle DATA-SAFETY-RUNTIME-GATE-Tests bestanden.")
