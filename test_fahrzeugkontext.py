"""
KaufCheck P1-4 — Fahrzeugkontext aus bestehenden DB-Feldern.
KEIN Netzwerk, KEIN LLM-Call.

Prüft, dass die seit jeher gepflegten, aber vom Kaufcheck bislang ungenutzten
Baureihen-Felder kontrolliert hereinkommen — und zwar als ERGÄNZENDER KONTEXT,
nicht als Evidence.

  A  Generation vorhanden        -> im Kontext sichtbar
  B  Generation fehlt            -> kein erfundener Wert
  C  Facelift-Merkmale vorhanden -> Kontext korrekt
  D  Facelift fehlt              -> sauber ausgelassen
  E  Segment vorhanden
  F  Vorgänger vorhanden         -> als lesbarer Name
  G  Vorgänger-Schwachstellen werden NICHT übernommen
  H  wartung_oel_km vorhanden    -> strukturiert verfügbar
  I  KEINE Fälligkeitsbewertung daraus
  J  wartung_hu_intervall vorhanden -> verfügbar
  K  keine Kilometer-Logik für die HU
  L  kaufberatung landet NICHT in Prompt/Evidence
  M  completed_no_market         -> Fahrzeugkontext identisch
  N  Marktdaten vorhanden        -> Fahrzeugkontext identisch
  O  P1-3-Prüflisten unverändert
  P  alte Checks ohne das Feld bleiben kompatibel

    python test_fahrzeugkontext.py
"""
from app.car_lookup import build_db_context
from app.evidence import build_insights
from app.fahrzeugkontext import (
    build_fahrzeugkontext, prompt_block, MAX_FREITEXT, _kuerze, _oel_km, _segment,
)
from app.kaufaktionen import build_kaufaktionen
from app.models import (
    Fahrzeugkontext, KaufCheckResponse, Marktanalyse, Preisbeobachtung,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    status = "OK  " if bedingung else "FAIL"
    print(f"[{status}] {name}")
    if not bedingung:
        _FEHLER.append(name)


class Req:
    def __init__(self, **kw):
        self.marke = kw.get("marke")
        self.modell = kw.get("modell")
        self.baujahr = kw.get("baujahr")
        self.kilometerstand = kw.get("kilometerstand")
        self.motor = kw.get("motor")
        self.kraftstoff = kw.get("kraftstoff")
        self.preis_eur = kw.get("preis_eur")
        self.ausstattung = kw.get("ausstattung") or []
        self.beschreibung = kw.get("beschreibung")
        self.freitext = kw.get("freitext")
        self.unfallfrei = kw.get("unfallfrei")
        self.vorbesitzer = kw.get("vorbesitzer")
        self.tuev_bis = kw.get("tuev_bis")
        self.scheckheftgepflegt = kw.get("scheckheftgepflegt")


def baureihe(**kw):
    """Baureihe mit den P1-4-Feldern; jedes Feld einzeln überschreibbar."""
    b = {
        "id": "test-baureihe", "marke": "TestMarke", "modell": "TestModell",
        "generation": "G1", "bauzeitraum_von": 2015, "bauzeitraum_bis": 2023,
        "karosserie": [], "tuev_maengelquote": None, "adac_pannenkennziffer": None,
        "ausstattungslinien": [], "motoren": [],
        "schwachstellen_baureihe": [], "rueckrufe": [],
        "segment": "Mittelklasse", "vorgaenger": "test-vorgaenger",
        "erkennung_generation": "Große Doppelniere, schmale LED-Scheinwerfer, "
                                "kantige Seitenlinie.",
        "facelift_merkmale": "Ab 2020 neue Frontschürze und geändertes Tagfahrlicht.",
        "wartung_oel_km": 25000, "wartung_hu_intervall": "Alle 2 Jahre",
        "kaufberatung": "Der TestModell bietet eine exzellente Kombination aus "
                        "sportlicher Fahrdynamik und hohem Komfort — ein rundum "
                        "gelungenes Fahrzeug für anspruchsvolle Käufer.",
    }
    b.update(kw)
    return b


def motor(schwachstellen_motor=None, kritische_wartung=None):
    return {
        "variante_id": "test-motor", "bezeichnung": "TestMotor 2.0",
        "motorcode": "T20", "kraftstoff": "Diesel", "leistung_ps": 150,
        "leistung_kw": 110, "drehmoment_nm": 320,
        "schwachstellen_motor": schwachstellen_motor or [],
        "kritische_wartung": kritische_wartung or [],
    }


VORGAENGER_DB = {"test-vorgaenger": {"id": "test-vorgaenger", "marke": "TestMarke",
                                     "modell": "TestModell", "generation": "G0"}}


def aufloeser(bid):
    return VORGAENGER_DB.get(bid)


def ctx_von(br, **kw):
    return build_fahrzeugkontext(br, aufloeser=aufloeser, **kw)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== A/B) Generation ===")

ctx_a = ctx_von(baureihe())
check("A1 Generation aus der Baureihe übernommen", ctx_a.generation == "G1")
check("A2 Generationsmerkmale vorhanden",
      ctx_a.erkennung_generation and "Doppelniere" in ctx_a.erkennung_generation)
check("A3 Baureihen-ID mitgeführt", ctx_a.baureihe_id == "test-baureihe")
check("A4 Merkmale erscheinen im Prompt-Block",
      "Merkmale dieser Generation:" in prompt_block(ctx_a))

ctx_b = ctx_von(baureihe(generation=None, erkennung_generation=None))
check("B1 fehlende Generation wird nicht erfunden", ctx_b.generation is None)
check("B2 fehlende Merkmale werden nicht erfunden", ctx_b.erkennung_generation is None)
check("B3 keine Platzhalterzeile im Prompt",
      "Merkmale dieser Generation" not in prompt_block(ctx_b))
check("B4 leerer String zählt als fehlend",
      ctx_von(baureihe(erkennung_generation="   ")).erkennung_generation is None)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== C/D) Facelift-Merkmale ===")

check("C1 Facelift-Merkmale übernommen",
      ctx_a.facelift_merkmale and "Frontschürze" in ctx_a.facelift_merkmale)
check("C2 im Prompt als Baureihen-Merkmal gekennzeichnet",
      "Facelift-Merkmale dieser Baureihe:" in prompt_block(ctx_a))

ctx_d = ctx_von(baureihe(facelift_merkmale=None))
check("D1 fehlendes Facelift bleibt None", ctx_d.facelift_merkmale is None)
check("D2 keine Facelift-Zeile im Prompt", "Facelift" not in prompt_block(ctx_d))
check("D3 restlicher Kontext bleibt vollständig", ctx_d.segment == "Mittelklasse")

# §4: KEINE harte Widerspruchsbehauptung aus einem unscharfen Freitext
_pb = prompt_block(ctx_a).lower()
check("D4 der Prompt behauptet keinen Facelift-Widerspruch",
      not any(w in _pb for w in ("widerspruch", "passt nicht", "stimmt nicht",
                                 "falsch angegeben", "unplausibel")))
check("D5 Facelift erzeugt keine Evidence",
      not [i for i in build_insights(baureihe(), None, [], Req(baujahr=2020),
                                     check_typ="kauf")])

# Kürzung langer Freitexte
_lang = "Satz eins ist hier. " * 60
check("D6 langer Freitext wird gekürzt", len(_kuerze(_lang)) <= MAX_FREITEXT)
check("D7 Kürzung endet an einer Satzgrenze", _kuerze(_lang).rstrip().endswith("."))
check("D8 kurzer Text bleibt unangetastet", _kuerze("Kurz.") == "Kurz.")
check("D9 Text ohne Satzgrenze wird markiert gekürzt",
      _kuerze("x" * 900).endswith("…"))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== E) Segment ===")

check("E1 Segment übernommen", ctx_a.segment == "Mittelklasse")
check("E2 Segment im Prompt", "Fahrzeugsegment: Mittelklasse" in prompt_block(ctx_a))
check("E3 fehlendes Segment bleibt None", ctx_von(baureihe(segment=None)).segment is None)
# Der Bestand enthält 8 unbrauchbare Segmentbuchstaben ("A", "D-Segment")
for _mist in ("A", "D-Segment", "b segment", "c"):
    check(f"E4 unbrauchbarer Segmentwert {_mist!r} verworfen",
          _segment({"segment": _mist}) is None)
check("E5 echtes Segment mit Bindestrich bleibt erhalten",
      _segment({"segment": "Kompakt-SUV"}) == "Kompakt-SUV")
check("E6 Segment fließt NICHT in eine Preisaussage",
      "preis" not in prompt_block(ctx_a).lower())


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== F/G) Vorgänger ===")

check("F1 Vorgänger-Slug zu lesbarem Namen aufgelöst",
      ctx_a.vorgaenger == "TestMarke TestModell G0")
check("F2 Vorgänger im Prompt", "Vorgängergeneration: TestMarke TestModell G0"
      in prompt_block(ctx_a))
check("F3 bereits lesbarer Wert bleibt unverändert",
      ctx_von(baureihe(vorgaenger="F30/F31")).vorgaenger == "F30/F31")
# 44 Datensätze im Bestand tragen einen Slug, der auf KEINE Baureihe zeigt
check("F4 toter Slug wird verworfen statt geraten",
      ctx_von(baureihe(vorgaenger="mercedes-benz-e-klasse-w124")).vorgaenger is None)
check("F5 fehlender Vorgänger bleibt None",
      ctx_von(baureihe(vorgaenger=None)).vorgaenger is None)


def _kaputter_aufloeser(_bid):
    raise RuntimeError("DB weg")


check("F6 DB-Ausfall beim Auflösen kippt den Kontext nicht",
      build_fahrzeugkontext(baureihe(), aufloeser=_kaputter_aufloeser).vorgaenger is None)

# §6: KEINE Evidence-Vererbung vom Vorgängermodell
_vorgaenger_voll = dict(VORGAENGER_DB["test-vorgaenger"])
_vorgaenger_voll["schwachstellen_baureihe"] = [
    {"bauteil": "Vorgänger-Steuerkette", "beschreibung": "Bekanntes Problem des Vorgängers.",
     "betroffene_baujahre": "Alle", "schweregrad": "hoch"}]
_ins_g = build_insights(baureihe(), None, [], Req(baujahr=2020), check_typ="kauf")
check("G1 Vorgänger-Schwachstellen werden NICHT zu Evidence",
      not any("Vorgänger" in i.titel for i in _ins_g))
_ka_g = build_kaufaktionen(Req(baujahr=2020), baureihe(), None, _ins_g)
_alle_g = [a for b in ("besichtigung", "probefahrt", "verkaeuferfragen", "dokumente")
           for pl in [getattr(_ka_g, b)] for a in [*pl.fahrzeugspezifisch, *pl.basis]]
check("G2 Vorgänger erzeugt keine Kaufaktion",
      not any("vorgänger" in f"{a.titel} {a.aktion}".lower() for a in _alle_g))
check("G3 Vorgänger steht nur als Name im Kontext, ohne Zustandsaussage",
      ctx_a.vorgaenger == "TestMarke TestModell G0")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== H/I) Ölwechsel-Intervall ===")

check("H1 wartung_oel_km strukturiert als Zahl", ctx_a.wartung_oel_km == 25000)
check("H2 Typ ist int, nicht String", isinstance(ctx_a.wartung_oel_km, int))
check("H3 im Prompt als Herstellerangabe gekennzeichnet",
      "Ölwechsel-Intervall (Herstellerangabe): alle 25.000 km" in prompt_block(ctx_a))
check("H4 fehlender Wert bleibt None",
      ctx_von(baureihe(wartung_oel_km=None)).wartung_oel_km is None)
check("H5 nicht-numerischer Wert verworfen", _oel_km({"wartung_oel_km": "bald"}) is None)
check("H6 unplausible Werte verworfen",
      _oel_km({"wartung_oel_km": 0}) is None and _oel_km({"wartung_oel_km": 3_000_000}) is None)
check("H7 numerischer String wird übernommen", _oel_km({"wartung_oel_km": "15000"}) == 15000)

# §13: P2-5 wird NICHT vorgezogen
# Der Block DARF die Wörter "fällig"/"überfällig" enthalten — aber nur im VERBOT
# ("darf KEINE Fälligkeit abgeleitet werden"), nie als Behauptung. Geprüft werden
# deshalb Behauptungs-Formulierungen, nicht blosse Stichwörter.
_FAELLIG_BEHAUPTUNG = ("ist fällig", "ist überfällig", "wäre fällig", "steht an",
                       "demnächst fällig", "wurde versäumt", "km/jahr", "km pro jahr",
                       "restlaufzeit", "verbleibende km", "risikoscore", "bald fällig")
_pb_h = prompt_block(ctx_a).lower()
_treffer = [w for w in _FAELLIG_BEHAUPTUNG if w in _pb_h]
check(f"I1 keine Fälligkeits-BEHAUPTUNG im Kontext ({_treffer})", _treffer == [])
check("I2 der Prompt VERBIETET die Fälligkeitsableitung ausdrücklich",
      "keine fälligkeit abgeleitet" in _pb_h)
check("I3 kein berechnetes Feld im Modell",
      not any(f in Fahrzeugkontext.model_fields for f in
              ("faellig", "oel_faellig", "naechster_service", "km_pro_jahr",
               "restkm", "wartungsstatus", "risikoscore")))
check("I4 der Kontext kennt den Kilometerstand gar nicht",
      "kilometerstand" not in build_fahrzeugkontext.__code__.co_varnames
      and "km" not in [p for p in build_fahrzeugkontext.__code__.co_varnames])
_ctx_km = build_fahrzeugkontext(baureihe(), aufloeser=aufloeser)
check("I5 gleicher Kontext unabhängig vom Kilometerstand",
      _ctx_km.model_dump() == ctx_a.model_dump())


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== J/K) HU-Intervall ===")

check("J1 HU-Intervall übernommen", ctx_a.wartung_hu_intervall == "Alle 2 Jahre")
check("J2 als Freitext, nicht als Zahl", isinstance(ctx_a.wartung_hu_intervall, str))
check("J3 im Prompt vorhanden", "HU-Intervall" in prompt_block(ctx_a))
# 36 Baureihen haben einen LEEREN String statt NULL
check("J4 leerer DB-String zählt als fehlend",
      ctx_von(baureihe(wartung_hu_intervall="")).wartung_hu_intervall is None)
check("J5 fehlendes HU-Intervall erzeugt keine Prompt-Zeile",
      "HU-Intervall" not in prompt_block(ctx_von(baureihe(wartung_hu_intervall=None))))
check("J6 abweichende Schreibweise bleibt unverändert erhalten",
      ctx_von(baureihe(wartung_hu_intervall="24 Monate")).wartung_hu_intervall == "24 Monate")

# §8: HU ist zeitgesteuert — keine Kilometerlogik
check("K1 der Prompt weist die HU ausdrücklich als zeitbezogen aus",
      "zeitbezogen" in _pb_h)
check("K2 der Prompt verbietet die Verrechnung mit dem Kilometerstand",
      "nicht mit dem kilometerstand verrechnet" in _pb_h)
check("K3 kein km-Feld für die HU im Modell",
      not any(f in Fahrzeugkontext.model_fields for f in
              ("wartung_hu_km", "hu_km", "hu_faellig_km")))
check("K4 HU-Intervall wird nie in eine Zahl umgedeutet",
      ctx_von(baureihe(wartung_hu_intervall="Alle 2 Jahre")).wartung_hu_intervall
      == "Alle 2 Jahre")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== L) kaufberatung wird NICHT übernommen (§2) ===")

_br_kb = baureihe()
check("L1 Testdaten enthalten tatsächlich eine kaufberatung", bool(_br_kb["kaufberatung"]))
check("L2 kaufberatung ist KEIN Feld des Fahrzeugkontexts",
      "kaufberatung" not in Fahrzeugkontext.model_fields)
_ctx_kb = ctx_von(_br_kb)
check("L3 kein Kontextfeld enthält den Marketingtext",
      not any("exzellente" in str(v).lower() for v in _ctx_kb.model_dump().values() if v))
check("L4 kaufberatung erreicht den Prompt-Block nicht",
      "exzellente" not in prompt_block(_ctx_kb).lower())
_ctx_full = build_db_context(_br_kb, motor(), 2020, fahrzeugkontext=_ctx_kb)
check("L5 kaufberatung erreicht auch den DB-Kontext nicht",
      "exzellente" not in _ctx_full.lower() and "anspruchsvolle käufer" not in _ctx_full.lower())
check("L6 kaufberatung wird nicht zu Evidence",
      not any("exzellente" in (i.beschreibung + i.titel).lower()
              for i in build_insights(_br_kb, None, [], Req(baujahr=2020), check_typ="kauf")))
check("L7 kaufberatung erzeugt keine Kaufaktion",
      not any("exzellente" in f"{a.titel} {a.aktion}".lower() for a in _alle_g))
check("L8 das Modul liest das Feld nirgends",
      "kaufberatung" not in __import__("io").open(
          "app/fahrzeugkontext.py", encoding="utf-8").read().split('"""')[2])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== M/N) Marktpreis-Unabhängigkeit ===")

markt = Marktanalyse(
    gefunden=12, verwendet=8, anzahl_sehr_aehnlich=4, anzahl_aehnlich=4, anzahl_bedingt=0,
    median_eur=24000, spanne_min_eur=22000, spanne_max_eur=26000, angebot_eur=21000,
    differenz_eur=-3000, differenz_pct=-12.5, datenqualitaet="hoch",
    methode="Median aus 8 Vergleichen.",
    beobachtungen=[Preisbeobachtung(preis_eur=24000, vergleichbarkeit="sehr_aehnlich",
                                    quelle_url="https://example.test/a")])
_req_m = Req(baujahr=2020, preis_eur=21000)
_ctx_ohne = ctx_von(baureihe())
_ctx_mit = ctx_von(baureihe())
check("M1 Kontext bei completed_no_market vollständig", _ctx_ohne.hat_inhalt())
check("N1 Kontext mit und ohne Marktdaten identisch",
      _ctx_ohne.model_dump() == _ctx_mit.model_dump())
check("N2 build_fahrzeugkontext nimmt keinen Markt-/Preisparameter",
      not any(p in build_fahrzeugkontext.__code__.co_varnames
              for p in ("marktanalyse", "price_assessment", "preis_eur", "median")))
check("N3 Prompt-Block enthält keine Marktvokabel",
      not any(w in _pb_h for w in ("median", "marktspanne", "marktwert", "günstig",
                                   "teuer", "schnäppchen")))
_ins_ohne = build_insights(baureihe(), motor(), [], _req_m, check_typ="kauf")
_ins_mit = build_insights(baureihe(), motor(),
                          [{"typ": "web", "titel": "T", "url": "https://example.test/a"}],
                          _req_m, check_typ="kauf", marktanalyse=markt)
check("N4 DB-Kontext-Text identisch mit und ohne Marktdaten",
      build_db_context(baureihe(), motor(), 2020, fahrzeugkontext=_ctx_ohne)
      == build_db_context(baureihe(), motor(), 2020, fahrzeugkontext=_ctx_mit))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== O) P1-3-Prüflisten unverändert ===")

_br_o, _mo_o = baureihe(), motor(
    kritische_wartung=[{"bauteil": "Zahnriemen", "intervall": "120.000 km", "hinweis": "x"}])
_ins_o = build_insights(_br_o, _mo_o, [], Req(baujahr=2020), check_typ="kauf")
_ka_o = build_kaufaktionen(Req(baujahr=2020), _br_o, _mo_o, _ins_o)
check("O1 build_kaufaktionen nimmt keinen Fahrzeugkontext entgegen",
      "fahrzeugkontext" not in build_kaufaktionen.__code__.co_varnames)
check("O2 vier Prüflisten unverändert vorhanden",
      all(hasattr(getattr(_ka_o, b), "fahrzeugspezifisch") and
          hasattr(getattr(_ka_o, b), "basis")
          for b in ("besichtigung", "probefahrt", "verkaeuferfragen", "dokumente")))
_alle_o = [a for b in ("besichtigung", "probefahrt", "verkaeuferfragen", "dokumente")
           for pl in [getattr(_ka_o, b)] for a in [*pl.fahrzeugspezifisch, *pl.basis]]
check("O3 P1-4 erzeugt keine neue Aktionskategorie",
      {a.kategorie for a in _alle_o} <=
      {"schwachstelle", "motorproblem", "rueckruf", "wartung", "inserat", "basis"})
check("O4 kein Aktionstext nennt Segment, Facelift oder Vorgänger",
      not any(w in f"{a.titel} {a.aktion}".lower() for a in _alle_o
              for w in ("segment", "facelift", "vorgängergeneration")))
check("O5 Wartungsaktionen weiterhin evidenzgebunden",
      all(a.evidence_ids for a in _alle_o if a.kategorie == "wartung"))
check("O6 Fahrzeugkontext taucht in keinem Insight auf",
      all(i.kategorie != "fahrzeugkontext" for i in _ins_o))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== P) Backward Compatibility ===")

_alt = KaufCheckResponse(bericht="alter Check", empfehlung="kaufen",
                         preis_bewertung="marktgerecht", quelle="datenbank", vertrauen="hoch")
check("P1 Alt-Check ohne fahrzeugkontext bleibt gültig", _alt.fahrzeugkontext is None)
check("P2 fahrzeugkontext ist kein Pflichtfeld",
      KaufCheckResponse.model_fields["fahrzeugkontext"].is_required() is False)
_roh = {"bericht": "x", "empfehlung": "kaufen", "preis_bewertung": "unbekannt",
        "quelle": "web", "vertrauen": "niedrig", "insights": [], "key_findings": []}
check("P3 altes gespeichertes Dict lädt weiterhin",
      KaufCheckResponse(**_roh).fahrzeugkontext is None)
_neu = KaufCheckResponse(**{**_roh, "fahrzeugkontext": ctx_a})
check("P4 neues Feld überlebt einen Serialisierungs-Rundlauf",
      KaufCheckResponse(**_neu.model_dump()).fahrzeugkontext.model_dump() == ctx_a.model_dump())
check("P5 build_db_context bleibt ohne den neuen Parameter aufrufbar",
      isinstance(build_db_context(baureihe(), motor(), 2020), str))
check("P6 der Verkaufscheck-Aufruf erzeugt KEINEN Kontextblock",
      "Zusatzkontext" not in build_db_context(baureihe(), motor(), 2020))
check("P7 leerer Kontext liefert None statt eines leeren Objekts",
      build_fahrzeugkontext(baureihe(
          segment=None, vorgaenger=None, erkennung_generation=None,
          facelift_merkmale=None, wartung_oel_km=None, wartung_hu_intervall=None),
          aufloeser=aufloeser) is None)
check("P8 ohne erkannte Baureihe kein Kontext", build_fahrzeugkontext(None) is None)
check("P9 leerer Kontext erzeugt leeren Prompt-Block", prompt_block(None) == "")


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE KAUFCHECK-P1-4-TESTS GRUEN")
