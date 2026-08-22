"""
KaufCheck P0-2 — Baujahres-Applicability im LLM-Prompt == in den strukturierten
Insights, deterministisch, KEIN Netzwerk, KEIN LLM-Call.

Hintergrund (Audit-Befund): `app/evidence.py::build_insights` filtert
Baureihen-/Motor-Schwachstellen bereits über `_baujahr_passt` — ein Baujahr,
das nachweislich NICHT in die angegebene Spanne fällt, wird für die
strukturierten Insights übersprungen. `app/car_lookup.py::build_db_context`
(der Text, der tatsächlich an das LLM geht) tat das bislang NICHT — dieselbe
Baureihe konnte im Bericht und in den Insights unterschiedliche Schwachstellen
zeigen. Exakt derselbe Bug-Musterfall wie der bereits behobene Rückruf-Leck
(app/recall_filter.py-Modulkopf).

Fix: beide Aufrufer nutzen jetzt dieselbe zentrale `_baujahr_passt`-Funktion
(app/recall_filter.py) mit identischer Regel: nur ein eindeutiges `False`
schließt aus. "Alle Baujahre", `True` und eine unklare/fehlende Angabe
(beides `None`) bleiben erhalten.

    python test_kaufcheck_baujahr_applicability.py
"""
from app.car_lookup import build_db_context
from app.evidence import build_insights
from app.recall_filter import _baujahr_passt

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    status = "OK  " if bedingung else "FAIL"
    print(f"[{status}] {name}")
    if not bedingung:
        _FEHLER.append(name)


class Req:
    """Minimaler KaufCheckRequest-Ersatz — nur was build_insights liest."""
    def __init__(self, baujahr=None, **kw):
        self.baujahr = baujahr
        self.marke = kw.get("marke")
        self.modell = kw.get("modell")
        self.motor = kw.get("motor")
        self.kraftstoff = kw.get("kraftstoff")
        self.kilometerstand = kw.get("kilometerstand")
        self.preis_eur = kw.get("preis_eur")
        self.beschreibung = kw.get("beschreibung")
        self.freitext = kw.get("freitext")
        self.scheckheftgepflegt = kw.get("scheckheftgepflegt")


def baureihe_mit_schwachstellen(schwachstellen_baureihe=None, rueckrufe=None):
    return {
        "id": "test-baureihe", "marke": "TestMarke", "modell": "TestModell",
        "generation": "G1", "bauzeitraum_von": 2015, "bauzeitraum_bis": 2023,
        "karosserie": [], "tuev_maengelquote": None, "adac_pannenkennziffer": None,
        "ausstattungslinien": [], "motoren": [],
        "schwachstellen_baureihe": schwachstellen_baureihe or [],
        "rueckrufe": rueckrufe or [],
    }


def motor_mit_problemen(schwachstellen_motor=None):
    return {
        "variante_id": "test-motor", "bezeichnung": "TestMotor 2.0",
        "motorcode": "T20", "kraftstoff": "Diesel", "leistung_ps": 150,
        "leistung_kw": 110, "drehmoment_nm": 320,
        "schwachstellen_motor": schwachstellen_motor or [],
        "kritische_wartung": [],
    }


def bauteile_im_prompt(ctx: str, bauteile: list[str]) -> set[str]:
    return {b for b in bauteile if b in ctx}


def bauteile_in_insights(insights, bauteile: list[str]) -> set[str]:
    text = " ".join(f"{i.titel} {i.beschreibung}" for i in insights)
    return {b for b in bauteile if b in text}


print("=== A. Problem gilt für das Fahrzeugbaujahr -> im Prompt vorhanden ===")

b = baureihe_mit_schwachstellen([
    {"bauteil": "Bremsen", "beschreibung": "Verschleiß", "betroffene_baujahre": "2019-2021",
     "schweregrad": "mittel"},
])
ctx = build_db_context(b, None, baujahr=2020)
check("A1: 'Bremsen' (2019-2021, Baujahr 2020) steht im Prompt", "Bremsen" in ctx)
ins = build_insights(b, None, [], Req(baujahr=2020), check_typ="kauf")
check("A2: 'Bremsen' auch als Insight vorhanden",
      any("Bremsen" in i.titel for i in ins))

print()
print("=== B. Problem gilt nur für früheres Baujahr -> NICHT im Prompt ===")

b = baureihe_mit_schwachstellen([
    {"bauteil": "Steuerkette", "beschreibung": "Frühe Baujahre betroffen",
     "betroffene_baujahre": "2015-2017", "schweregrad": "hoch"},
])
ctx = build_db_context(b, None, baujahr=2020)
check("B1: 'Steuerkette' (2015-2017, Baujahr 2020) NICHT im Prompt",
      "Steuerkette" not in ctx)
ins = build_insights(b, None, [], Req(baujahr=2020), check_typ="kauf")
check("B2: 'Steuerkette' auch NICHT als Insight",
      not any("Steuerkette" in i.titel for i in ins))

print()
print("=== C. Problem gilt nur für späteres Baujahr -> NICHT im Prompt ===")

b = baureihe_mit_schwachstellen([
    {"bauteil": "Infotainment", "beschreibung": "Spätere Software-Version betroffen",
     "betroffene_baujahre": "2022-2023", "schweregrad": "gering"},
])
ctx = build_db_context(b, None, baujahr=2016)
check("C1: 'Infotainment' (2022-2023, Baujahr 2016) NICHT im Prompt",
      "Infotainment" not in ctx)
ins = build_insights(b, None, [], Req(baujahr=2016), check_typ="kauf")
check("C2: 'Infotainment' auch NICHT als Insight",
      not any("Infotainment" in i.titel for i in ins))

print()
print("=== D. 'Alle Baujahre' -> weiterhin vorhanden ===")

for wert in ("Alle Baujahre", "alle", "Alle", "diverse", "unbekannt", "-", "n/a"):
    b = baureihe_mit_schwachstellen([
        {"bauteil": "Knarzgeräusche", "beschreibung": "Innenraum", "betroffene_baujahre": wert,
         "schweregrad": "gering"},
    ])
    ctx = build_db_context(b, None, baujahr=2020)
    check(f"D: betroffene_baujahre={wert!r} -> 'Knarzgeräusche' bleibt im Prompt",
          "Knarzgeräusche" in ctx)

print()
print("=== E. fehlende/unklare Baujahresangabe -> bestehende Semantik unverändert (None -> bleibt) ===")

b = baureihe_mit_schwachstellen([
    {"bauteil": "Klimaanlage", "beschreibung": "Gelegentlich undicht", "betroffene_baujahre": None,
     "schweregrad": "gering"},
])
ctx = build_db_context(b, None, baujahr=2020)
check("E1: betroffene_baujahre=None -> bleibt im Prompt (None ist kein False)",
      "Klimaanlage" in ctx)

b2 = baureihe_mit_schwachstellen([
    {"bauteil": "Auspuffanlage", "beschreibung": "Rost möglich", "betroffene_baujahre": "2019-2021",
     "schweregrad": "gering"},
])
ctx2 = build_db_context(b2, None, baujahr=None)  # Fahrzeugbaujahr selbst unbekannt
check("E2: Fahrzeugbaujahr unbekannt -> Schwachstelle bleibt im Prompt (konservativ)",
      "Auspuffanlage" in ctx2)
check("E3: _baujahr_passt('2019-2021', None) ist None (Referenzsemantik unverändert)",
      _baujahr_passt("2019-2021", None) is None)

print()
print("=== F. mehrere Schwachstellen gemischt -> nur passende erscheinen ===")

b = baureihe_mit_schwachstellen([
    {"bauteil": "Bremsen", "beschreibung": "x", "betroffene_baujahre": "2019-2021", "schweregrad": "mittel"},
    {"bauteil": "Steuerkette", "beschreibung": "x", "betroffene_baujahre": "2015-2017", "schweregrad": "hoch"},
    {"bauteil": "Infotainment", "beschreibung": "x", "betroffene_baujahre": "2022-2023", "schweregrad": "gering"},
    {"bauteil": "Knarzgeräusche", "beschreibung": "x", "betroffene_baujahre": "Alle Baujahre", "schweregrad": "gering"},
])
ctx = build_db_context(b, None, baujahr=2020)
erwartet_drin = {"Bremsen", "Knarzgeräusche"}
erwartet_raus = {"Steuerkette", "Infotainment"}
check("F1: passende Bauteile alle vorhanden",
      erwartet_drin <= bauteile_im_prompt(ctx, list(erwartet_drin)))
check("F2: nicht-passende Bauteile alle entfernt",
      bauteile_im_prompt(ctx, list(erwartet_raus)) == set())

ins = build_insights(b, None, [], Req(baujahr=2020), check_typ="kauf")
ins_drin = bauteile_in_insights(ins, list(erwartet_drin))
ins_raus = bauteile_in_insights(ins, list(erwartet_raus))
check("F3: Insights zeigen dieselben passenden Bauteile", ins_drin == erwartet_drin)
check("F4: Insights zeigen keines der nicht-passenden Bauteile", ins_raus == set())

print()
print("=== G. Structured Insight und Prompt-Kontext -> dieselbe Applicability (Kernziel) ===")

b = baureihe_mit_schwachstellen([
    {"bauteil": "Bremsen", "beschreibung": "x", "betroffene_baujahre": "2019-2021", "schweregrad": "mittel"},
    {"bauteil": "Steuerkette", "beschreibung": "x", "betroffene_baujahre": "2015-2017", "schweregrad": "hoch"},
    {"bauteil": "Knarzgeräusche", "beschreibung": "x", "betroffene_baujahre": "Alle", "schweregrad": "gering"},
    {"bauteil": "Klimaanlage", "beschreibung": "x", "betroffene_baujahre": None, "schweregrad": "gering"},
])
motor = motor_mit_problemen([
    {"bauteil": "Turbolader", "beschreibung": "x", "baujahre": "2019-2021", "kosten_ca": "800-1200 EUR"},
    {"bauteil": "AGR-Ventil", "beschreibung": "x", "baujahre": "2015-2017", "kosten_ca": "300 EUR"},
    {"bauteil": "Einspritzdüsen", "beschreibung": "x", "baujahre": None, "kosten_ca": None},
])
alle_bauteile = ["Bremsen", "Steuerkette", "Knarzgeräusche", "Klimaanlage",
                 "Turbolader", "AGR-Ventil", "Einspritzdüsen"]

for testjahr in (2015, 2016, 2020, 2022):
    ctx = build_db_context(b, motor, baujahr=testjahr)
    ins = build_insights(b, motor, [], Req(baujahr=testjahr), check_typ="kauf")
    prompt_set = bauteile_im_prompt(ctx, alle_bauteile)
    insight_set = bauteile_in_insights(ins, alle_bauteile)
    check(f"G (Baujahr {testjahr}): Prompt- und Insight-Applicability sind IDENTISCH "
          f"(Prompt={sorted(prompt_set)}, Insights={sorted(insight_set)})",
          prompt_set == insight_set)

print()
print("=== H. Rückrufe unverändert (Regressionsschutz, keine Änderung an recall_filter) ===")

b = baureihe_mit_schwachstellen(rueckrufe=[
    {"datum": "2021-05", "betroffene_baujahre": "2019-2021", "mangel": "Bremskraftverstärker",
     "abhilfe": "Softwareupdate", "kba_referenz": "123456"},
    {"datum": "2018-01", "betroffene_baujahre": "2015-2016", "mangel": "Airbag-Steuergerät",
     "abhilfe": "Austausch", "kba_referenz": "654321"},
])
ctx = build_db_context(b, None, baujahr=2020)
check("H1: zutreffender Rückruf (2019-2021, Baujahr 2020) weiterhin im Prompt",
      "Bremskraftverstärker" in ctx)
check("H2: nicht zutreffender Rückruf (2015-2016, Baujahr 2020) weiterhin ausgeschlossen",
      "Airbag-Steuergerät" not in ctx)

print()
if _FEHLER:
    print(f"FEHLGESCHLAGEN: {len(_FEHLER)}")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE KAUFCHECK-P0-2-TESTS GRUEN")
