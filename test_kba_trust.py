"""
KBA-Referenz-Trust-Gate — Fortsetzung des DATA-TRUST-AUDIT.
KEIN Netzwerk, KEIN LLM-Call.

Hintergrund: 200 von 589 befüllten `kba_referenz`-Werten (76 unterschiedliche
Referenzen) sind markenübergreifend mehrfach vergeben — dieselbe Nummer steht bei
BMW, VW, Opel, Ford UND Seat. Zusätzlich enthält das Feld erkennbare Test-/
Platzhalterwerte (sequenzielle Ziffernfolgen, ein 64-stelliger Fast-Null-Block).
Bisher konnte die BLOSSE ANWESENHEIT einer Referenz einen Rückruf auf
"variant_match"/confidence "hoch" heben — unabhängig von ihrer Plausibilität.

  A) plausible eindeutige KBA-Referenz -> bestehendes Verhalten
  B) leere Referenz -> keine Vertrauenssteigerung
  C) offensichtlicher Platzhalter -> nicht anzeigen / nicht hochstufen
  D) markenübergreifend kollidierende Referenz -> nicht belastbar
  E) gleiche Referenz, mehrere Modelle DERSELBEN Marke -> nicht automatisch ablehnen
  F) unplausible Referenz -> Rückrufdatensatz bleibt (konservativ) erhalten
  G) konservativer FIN-Hinweis bleibt
  H) keine falsche Nummer in Kaufaktionen
  I) keine falsche Nummer im Evidence-Output
  J) gültiger Rückruf-Fall bleibt semantisch unverändert

    python test_kba_trust.py
"""
import app.recall_filter as rf
from app.car_lookup import build_db_context
from app.evidence import build_insights, valid_evidence_ids
from app.kaufaktionen import build_kaufaktionen
from app.recall_filter import (
    kba_referenz_format_plausibel, kba_referenz_kollidiert_markenuebergreifend,
    kba_referenz_vertrauenswuerdig, kba_referenz_anzeige,
    gefilterte_rueckrufe, ausgeschlossene_rueckrufe, rueckruf_applicability,
    RUECKRUF_APPLICABILITY_TEXT,
)

_FEHLER: list[str] = []
BEREICHE = ("besichtigung", "probefahrt", "verkaeuferfragen", "dokumente")


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


def baureihe(rueckrufe=None, marke="TestMarke", verified=True):
    """Testbaureihe.

    DATA-SAFETY-RUNTIME-GATE: `verified=True` ist hier der DEFAULT, weil dieses
    Modul das KBA-Referenz-Gate prüft — also die Frage "welche Nummer darf
    angezeigt werden", nicht die Frage "ist der Rückrufinhalt belegt". Ohne
    hinterlegte Verifikation blendet die Evidence die Nummer generell aus (§6),
    und das KBA-Gate wäre nicht mehr beobachtbar. Der unverifizierte Normalfall
    der Produktion wird in Abschnitt K eigens geprüft.
    """
    br = {
        "id": "test-baureihe", "marke": marke, "modell": "TestModell",
        "generation": "G1", "bauzeitraum_von": 2015, "bauzeitraum_bis": 2023,
        "karosserie": [], "tuev_maengelquote": None, "adac_pannenkennziffer": None,
        "ausstattungslinien": [], "motoren": [],
        "schwachstellen_baureihe": [], "rueckrufe": rueckrufe or [],
    }
    if verified:
        br["verification"] = {
            fakt: {"status": "verified", "source": "https://example.test/nachweis",
                   "date": "2026-08-24"}
            for fakt in ("schwachstellen", "motorprobleme", "rueckrufe", "wartung")
        }
    return br


def motor():
    return {
        "variante_id": "test-motor", "bezeichnung": "TestMotor 2.0",
        "motorcode": "T20", "kraftstoff": "Diesel", "leistung_ps": 150,
        "leistung_kw": 110, "drehmoment_nm": 320,
        "schwachstellen_motor": [], "kritische_wartung": [],
    }


def rueckruf(kba, mangel="Testmangel", baujahre="Alle", abhilfe="Prüfen/Tausch",
             trust="verified"):
    """Ein Rückruf-Dict, wie `app/database.py::get_baureihe` es liefert.

    RECALL-PILOT: `_trust` gehört zur Produktions-Form dieses Dicts (gesetzt von
    `app/fakt_verifikation.py::annotiere`) und ist hier aus demselben Grund auf
    "verified" vorbelegt wie `verified=True` bei `baureihe()`: dieses Modul prüft
    das KBA-REFERENZ-Gate ("welche Nummer darf angezeigt werden und die Stufe
    heben"), nicht das Verifikations-Gate ("ist der Rückrufinhalt belegt").
    Seit §9 des Recall-Piloten ist eine formatplausible Nummer allein nicht mehr
    genug — ohne belegten Fakt bliebe jeder Rückruf auf "series_only" und das
    Format-/Kollisionsgate wäre gar nicht mehr beobachtbar. Der unverifizierte
    Produktions-Normalfall wird in test_recall_pilot.py Abschnitt E geprüft.
    """
    return {"id": "rk-1", "datum": "2022-01", "betroffene_baujahre": baujahre,
            "mangel": mangel, "abhilfe": abhilfe, "kba_referenz": kba,
            "_trust": trust}


def index_mit(**marken_je_ref):
    """Baut einen injizierbaren Marken-Index: {'011400': {'BMW','VW'}, ...}."""
    return {k.upper(): set(v) for k, v in marken_je_ref.items()}


def _mit_index(monkeypatch_zeilen):
    """Ersetzt `get_rueckruf_referenzen_kurz`, damit die Kollisionsprüfung ohne
    echte DB testbar ist — genau das Monkeypatch-Muster aus test_car_lookup.py."""
    rf.get_rueckruf_referenzen_kurz = lambda: monkeypatch_zeilen


_ORIGINAL_GET = rf.get_rueckruf_referenzen_kurz


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== A) Plausible eindeutige KBA-Referenz -> bestehendes Verhalten ===")

_mit_index([{"kba_referenz": "9600", "marke": "TestMarke"}])
check("A1 Format plausibel", kba_referenz_format_plausibel("9600"))
check("A2 keine Kollision", not kba_referenz_kollidiert_markenuebergreifend("9600", "TestMarke"))
check("A3 vertrauenswürdig", kba_referenz_vertrauenswuerdig("9600", "TestMarke"))
check("A4 wird angezeigt", kba_referenz_anzeige("9600", "TestMarke") == "9600")

_ins_a = build_insights(baureihe([rueckruf("9600", baujahre="2019-2021")]), motor(), [],
                        Req(baujahr=2020), check_typ="kauf")
_rr_a = [i for i in _ins_a if i.kategorie == "rueckruf"][0]
check("A5 Applicability variant_match (Baujahr eindeutig getroffen, Referenz plausibel)",
      _rr_a.applicability == "variant_match")
check("A6 confidence hoch", _rr_a.confidence == "hoch")
check("A7 Evidence-Quelle trägt die Referenz", _rr_a.quellen[0].ref == "9600")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== B) Leere Referenz -> keine Vertrauenssteigerung ===")

_mit_index([])
check("B1 leerer String nicht plausibel", not kba_referenz_format_plausibel(""))
check("B2 None nicht plausibel", not kba_referenz_format_plausibel(None))
check("B3 Whitespace nicht plausibel", not kba_referenz_format_plausibel("   "))
check("B4 keine Anzeige", kba_referenz_anzeige("", "TestMarke") is None)

_ins_b = build_insights(baureihe([rueckruf(None, baujahre="Alle")]), motor(), [],
                        Req(baujahr=2020), check_typ="kauf")
_rr_b = [i for i in _ins_b if i.kategorie == "rueckruf"][0]
check("B5 ohne Referenz: series_only (Baujahr passt, aber keine Referenz)",
      _rr_b.applicability == "series_only")
check("B6 Quelle ohne Referenz", _rr_b.quellen[0].ref is None)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== C) Offensichtliche Platzhalter -> nicht anzeigen / nicht hochstufen ===")

# Nur STRIKT sequenzielle Ziffernfolgen gelten als erkannter Platzhalter (jede
# Ziffer exakt +1/-1 zur vorigen, ueber die gesamte Laenge) — das ist die einzige
# Klasse, die sich ohne Vermutung aus dem Bestand belegen laesst (12 Treffer, siehe
# Modulkopf recall_filter.py). Zwei im Bestand ebenfalls auffaellige, aber NICHT
# strikt sequenzielle Werte ("8987654", "9123456") gehoeren bewusst NICHT hierher:
# sie sind reale, doppelt vergebene Mercedes-Referenzen (GLE / GLE Coupé, §2 —
# Mehrfachnutzung derselben Marke ist kein Fehler) und werden separat unten (C10)
# als Positivfall gegen die Kollisionspruefung dokumentiert.
PLATZHALTER = [
    "1234567", "2345678", "9876543", "012345", "65432",
    "8A" + "0" * 66,
    "Herstelleraktion (auch Cabrio MY2017)",
]
for wert in PLATZHALTER:
    check(f"C1 {wert[:24]!r}: Format NICHT plausibel", not kba_referenz_format_plausibel(wert))
    check(f"C2 {wert[:24]!r}: keine Anzeige", kba_referenz_anzeige(wert, "TestMarke") is None)

_mit_index([])
_ins_c = build_insights(baureihe([rueckruf("1234567", baujahre="Alle")]), motor(), [],
                        Req(baujahr=2020), check_typ="kauf")
_rr_c = [i for i in _ins_c if i.kategorie == "rueckruf"][0]
check("C3 Platzhalter hebt NICHT auf variant_match", _rr_c.applicability == "series_only")
check("C4 Platzhalter erscheint nicht als Evidence-Referenz", _rr_c.quellen[0].ref is None)
check("C5 Titel signalisiert die fehlende Referenz statt der Platzhalterzahl",
      "keine kba-referenz hinterlegt" in _rr_c.quellen[0].titel.lower())

# Realistische, aber knapp zu lange / zu kurze Werte
check("C6 zu lang (>12 Zeichen) abgelehnt", not kba_referenz_format_plausibel("123456789012345"))
check("C7 zu kurz (<3 Ziffern) abgelehnt", not kba_referenz_format_plausibel("42"))
check("C8 reale Formate mit Trennzeichen bleiben plausibel",
      kba_referenz_format_plausibel("64-0034") and kba_referenz_format_plausibel("80 14 11"))
check("C9 Mercedes-Schreibweise (Buchstabe+Ziffern) bleibt plausibel",
      kba_referenz_format_plausibel("8A800000"))

_mit_index([{"kba_referenz": "9123456", "marke": "Mercedes-Benz"},
           {"kba_referenz": "9123456", "marke": "Mercedes-Benz"}])
check("C10 real doppelt vergebene, aber NICHT sequenzielle Referenz bleibt "
      "vertrauenswuerdig (gleiche Marke, zwei Modelle)",
      kba_referenz_vertrauenswuerdig("9123456", "Mercedes-Benz"))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== D) Markenübergreifend kollidierende Referenz -> nicht belastbar ===")

_mit_index([{"kba_referenz": "011400", "marke": "BMW"},
           {"kba_referenz": "011400", "marke": "Volkswagen"},
           {"kba_referenz": "011400", "marke": "Opel"}])
check("D1 Format der Referenz selbst ist plausibel",
      kba_referenz_format_plausibel("011400"))
check("D2 aber Kollision erkannt", kba_referenz_kollidiert_markenuebergreifend("011400", "BMW"))
check("D3 deshalb NICHT vertrauenswürdig", not kba_referenz_vertrauenswuerdig("011400", "BMW"))
check("D4 keine Anzeige trotz plausiblem Format", kba_referenz_anzeige("011400", "BMW") is None)
check("D5 andere beteiligte Marke ebenfalls nicht vertrauenswürdig",
      not kba_referenz_vertrauenswuerdig("011400", "Volkswagen"))

_ins_d = build_insights(baureihe([rueckruf("011400", baujahre="Alle")], marke="BMW"),
                        motor(), [], Req(baujahr=2020), check_typ="kauf")
_rr_d = [i for i in _ins_d if i.kategorie == "rueckruf"][0]
check("D6 Applicability faellt auf series_only zurueck (nicht variant_match)",
      _rr_d.applicability == "series_only")
check("D7 confidence entsprechend mittel statt hoch", _rr_d.confidence == "mittel")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== E) Gleiche Referenz, mehrere Modelle DERSELBEN Marke -> kein Fehler ===")

_mit_index([{"kba_referenz": "7600", "marke": "BMW"},
           {"kba_referenz": "7600", "marke": "BMW"}])
check("E1 keine Kollision bei identischer Marke",
      not kba_referenz_kollidiert_markenuebergreifend("7600", "BMW"))
check("E2 vertrauenswürdig", kba_referenz_vertrauenswuerdig("7600", "BMW"))
check("E3 wird angezeigt", kba_referenz_anzeige("7600", "BMW") == "7600")

_ins_e = build_insights(baureihe([rueckruf("7600", baujahre="2019-2021")], marke="BMW"),
                        motor(), [], Req(baujahr=2020), check_typ="kauf")
_rr_e = [i for i in _ins_e if i.kategorie == "rueckruf"][0]
check("E4 volle Vertrauensstufe trotz Mehrfachnutzung derselben Marke",
      _rr_e.applicability == "variant_match" and _rr_e.confidence == "hoch")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== F) Unplausible Referenz -> Rückrufdatensatz bleibt konservativ erhalten ===")

_mit_index([])
_ka_f, _ins_f = None, build_insights(
    baureihe([rueckruf("1234567", "Bremsschlauch porös", baujahre="Alle")]), motor(),
    [], Req(baujahr=2020), check_typ="kauf")
check("F1 der Rückruf erscheint weiterhin als Insight (nicht verworfen)",
      any(i.kategorie == "rueckruf" for i in _ins_f))
_rr_f = [i for i in _ins_f if i.kategorie == "rueckruf"][0]
check("F2 Mangeltext bleibt vollständig erhalten", "Bremsschlauch" in _rr_f.beschreibung)
_erlaubt_f = gefilterte_rueckrufe([rueckruf("1234567", "Bremsschlauch porös", baujahre="Alle")],
                                  motor(), 2020, marke="TestMarke")
check("F3 gefilterte_rueckrufe verwirft den Datensatz NICHT",
      len(_erlaubt_f) == 1 and "Bremsschlauch" in _erlaubt_f[0]["text"])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== G) Konservativer FIN-Hinweis bleibt ===")

check("G1 series_only-Wortlaut verlangt weiterhin FIN-Prüfung",
      "FIN" in RUECKRUF_APPLICABILITY_TEXT["series_only"])
check("G2 kein 'betrifft dein Fahrzeug' im Wortlaut irgendeiner Stufe",
      all("betrifft dein" not in v.lower() for v in RUECKRUF_APPLICABILITY_TEXT.values()))
_,_,einfluss_g,_ = rueckruf_applicability(
    rueckruf("1234567", baujahre="Alle"), True, "1234567", motor(), marke="TestMarke")
check("G3 Einfluss-Text der abgestuften Aktion enthält den FIN-Hinweis",
      "FIN" in einfluss_g)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== H) Keine falsche Nummer in Kaufaktionen ===")

_mit_index([{"kba_referenz": "011400", "marke": "BMW"},
           {"kba_referenz": "011400", "marke": "Ford"}])
_br_h = baureihe([rueckruf("011400", "Ausfall der Servolenkung", baujahre="Alle")], marke="BMW")
_ins_h = build_insights(_br_h, motor(), [], Req(baujahr=2020), check_typ="kauf")
_ka_h = build_kaufaktionen(Req(baujahr=2020), _br_h, motor(), _ins_h)
_alle_h = [a for b in BEREICHE for pl in [getattr(_ka_h, b)]
           for a in [*pl.fahrzeugspezifisch, *pl.basis]]
_text_h = " ".join(f"{a.titel} {a.aktion}" for a in _alle_h)
check("H1 die kollidierende Nummer '011400' erscheint in KEINER Kaufaktion",
      "011400" not in _text_h)
check("H2 trotzdem eine konservative Rückruf-Dokumentaktion vorhanden",
      any(a.kategorie == "rueckruf" for a in _alle_h if a.bereich == "dokumente"))
_rr_doc_h = [a for a in _alle_h if a.kategorie == "rueckruf" and a.bereich == "dokumente"][0]
check("H3 die Dokumentaktion verlangt die FIN-Prüfung", "FIN" in _rr_doc_h.aktion)
check("H4 keine KBA-Referenz-Erwähnung im Aktionstext (kein 'KBA-Referenz 011400')",
      "kba-referenz" not in _rr_doc_h.aktion.lower())


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== I) Keine falsche Nummer im Evidence-Output ===")

check("I1 EvidenceQuelle.ref ist None bei kollidierender Referenz",
      [i for i in _ins_h if i.kategorie == "rueckruf"][0].quellen[0].ref is None)
check("I2 EvidenceQuelle-Titel kennzeichnet die fehlende Referenz",
      "keine kba-referenz hinterlegt"
      in [i for i in _ins_h if i.kategorie == "rueckruf"][0].quellen[0].titel.lower())
_dbctx_h = build_db_context(_br_h, motor(), 2020)
check("I3 die Nummer '011400' erscheint NICHT im DB-Kontext-Prompt",
      "011400" not in _dbctx_h)
check("I4 der Rückruf-Mangeltext erscheint weiterhin im Prompt",
      "Servolenkung" in _dbctx_h)
check("I5 Evidence-IDs bleiben gültig referenzierbar",
      all(set(a.evidence_ids) <= valid_evidence_ids(_ins_h) for a in _alle_h))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== J) Gültiger Rückruf-Fall bleibt semantisch unverändert ===")

_mit_index([{"kba_referenz": "9600", "marke": "Opel"}])
_br_j = baureihe([rueckruf("9600", "Fehlerhafte Software im Motorsteuergerät",
                           baujahre="2019-2020")], marke="Opel")
_ins_j = build_insights(_br_j, motor(), [], Req(baujahr=2020), check_typ="kauf")
_rr_j = [i for i in _ins_j if i.kategorie == "rueckruf"][0]
check("J1 Applicability weiterhin variant_match", _rr_j.applicability == "variant_match")
check("J2 confidence weiterhin hoch", _rr_j.confidence == "hoch")
check("J3 Referenz weiterhin sichtbar", _rr_j.quellen[0].ref == "9600")
_ka_j = build_kaufaktionen(Req(baujahr=2020), _br_j, motor(), _ins_j)
_alle_j = [a for b in BEREICHE for pl in [getattr(_ka_j, b)]
           for a in [*pl.fahrzeugspezifisch, *pl.basis]]
check("J4 die Dokumentaktion nennt die Referenz weiterhin",
      any("9600" in a.aktion for a in _alle_j if a.kategorie == "rueckruf"))
check("J5 die kritische Priorität bleibt erhalten",
      any(a.prioritaet == "kritisch" for a in _alle_j if a.kategorie == "rueckruf"))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== K) DB-Fehler beim Kollisionsindex degradiert konservativ, stürzt nicht ===")


def _kaputt():
    raise RuntimeError("DB weg")


rf.get_rueckruf_referenzen_kurz = _kaputt
check("K1 Format-Check bleibt trotz DB-Fehler wirksam",
      not kba_referenz_vertrauenswuerdig("1234567", "BMW"))
check("K2 plausibler Wert bleibt ohne Kollisionsdaten vertrauenswürdig (kein Crash)",
      kba_referenz_vertrauenswuerdig("9600", "BMW"))
rf.get_rueckruf_referenzen_kurz = _ORIGINAL_GET


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE KBA-TRUST-TESTS GRUEN")
