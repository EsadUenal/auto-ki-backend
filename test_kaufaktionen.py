"""
KaufCheck P1-3 — Deterministische Kaufaktionen. KEIN Netzwerk, KEIN LLM-Call.

Prüft die vier Handlungsbereiche (Besichtigung / Probefahrt / Verkäuferfragen /
Dokumente), die `app/kaufaktionen.py::build_kaufaktionen` aus bereits vorhandener
Evidence (Insights) und den Inserat-Angaben ableitet.

Kernzusicherungen (Testbuchstaben wie im Auftrag):
  A  Baureihen-Schwachstelle  -> Besichtigungsaktion
  B  Motorproblem             -> sinnvolle Verkäuferfrage
  C  beobachtbares Fahrsymptom-> Probefahrt-Aktion
  D  KEIN Fahrsymptom         -> KEINE erfundene Probefahrt-Aktion
  E  Rückruf                  -> konservative FIN-/Nachweis-Aktion (nie "betroffen")
  F  scheckheftgepflegt=True  -> nie die Behauptung, das Scheckheft fehle
  G  unklare Wartungslage     -> passende Dokument-/Frage-Aktion
  H  TÜV-Eingabe wird berücksichtigt
  I  falsches Baujahr (P0-2)  -> gefiltert -> keine Aktion
  J  mehrfache Evidence       -> keine Duplikate, Evidence-IDs zusammengeführt
  K  nur gültige Evidence-IDs
  L  stabile IDs über wiederholte Ausführung
  M  deterministische Priorisierung
  N  completed_no_market      -> technische Aktionen vollständig
  O  identischer Fall mit Marktpreis -> dieselben technischen Aktionen
  P  dünne DB / unbekanntes Fahrzeug -> nichts Fahrzeugspezifisches erfunden
  Q  alte Response ohne `kaufaktionen` -> weiterhin gültig

Ausbau zum Kaufbegleiter (Block R-Z): jeder Bereich hat zusätzlich eine
Basis-Checkliste (allgemeiner Prüfstandard). Die Fälle A-Q oben prüfen weiterhin
ausschließlich die FAHRZEUGSPEZIFISCHE Ebene — sie darf durch den Basis-Katalog
weder wachsen noch schrumpfen.

    python test_kaufaktionen.py
"""
from app.evidence import build_insights, valid_evidence_ids
from app.kaufaktionen import (
    build_kaufaktionen, MAX_SPEZIFISCH_PRO_BEREICH, _komponente, _fahrsymptom_aus_text,
    _KOMPONENTEN, _FAHRSYMPTOME, _norm, TYP_SPEZIFISCH, TYP_BASIS, PRIO_BASIS,
    EXPORT_TITEL, _wird_abgedeckt,
)
from app.models import (
    KaufCheckResponse, Kaufaktion, Kaufaktionen, Marktanalyse, Preisbeobachtung, Pruefliste,
)
import io as _io

# Quelltext der beiden P1-3-Module — für die Zusicherung, dass (noch) keine
# PDF-Bibliothek eingebunden wurde (§14).
io_open_src = (_io.open('app/kaufaktionen.py', encoding='utf-8').read()
               + _io.open('app/pruefplan_basis.py', encoding='utf-8').read())
from app.pruefplan_basis import (
    BASIS_BESICHTIGUNG, BASIS_PROBEFAHRT, BASIS_VERKAEUFERFRAGEN, BASIS_DOKUMENTE,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    status = "OK  " if bedingung else "FAIL"
    print(f"[{status}] {name}")
    if not bedingung:
        _FEHLER.append(name)


class Req:
    """Minimaler KaufCheckRequest-Ersatz — nur die Felder, die gelesen werden."""

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


def baureihe(schwachstellen=None, rueckrufe=None):
    return {
        "id": "test-baureihe", "marke": "TestMarke", "modell": "TestModell",
        "generation": "G1", "bauzeitraum_von": 2015, "bauzeitraum_bis": 2023,
        "karosserie": [], "tuev_maengelquote": None, "adac_pannenkennziffer": None,
        "ausstattungslinien": [], "motoren": [],
        "schwachstellen_baureihe": schwachstellen or [],
        "rueckrufe": rueckrufe or [],
    }


def motor(schwachstellen_motor=None, kritische_wartung=None, kraftstoff="Diesel"):
    return {
        "variante_id": "test-motor", "bezeichnung": "TestMotor 2.0",
        "motorcode": "T20", "kraftstoff": kraftstoff, "leistung_ps": 150,
        "leistung_kw": 110, "drehmoment_nm": 320,
        "schwachstellen_motor": schwachstellen_motor or [],
        "kritische_wartung": kritische_wartung or [],
    }


def aktionen(req=None, br=None, mo=None):
    """Insights wie im echten Check bauen, dann daraus die Aktionen."""
    req = req or Req(baujahr=2020)
    ins = build_insights(br, mo, [], req, check_typ="kauf")
    return build_kaufaktionen(req, br, mo, ins), ins


def alle(ka):
    return [*ka.besichtigung.fahrzeugspezifisch, *ka.probefahrt.fahrzeugspezifisch, *ka.verkaeuferfragen.fahrzeugspezifisch, *ka.dokumente.fahrzeugspezifisch]


def texte(liste):
    return " ".join(f"{a.titel} {a.aktion} {a.hinweis or ''}" for a in liste).lower()


BEREICHE = ("besichtigung", "probefahrt", "verkaeuferfragen", "dokumente")


def listen(ka):
    """Die vier Prüflisten als (name, Pruefliste)-Paare."""
    return [(b, getattr(ka, b)) for b in BEREICHE]


def basis(ka, bereich):
    return getattr(ka, bereich).basis


def alle_punkte(ka):
    """Beide Ebenen aller vier Bereiche — nur für Querschnitts-Prüfungen."""
    out = []
    for _, pl in listen(ka):
        out += [*pl.fahrzeugspezifisch, *pl.basis]
    return out


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 0) Invarianten der Wissenstabelle ===")

# Regressionsschutz fuer einen real aufgetretenen Fehler: die Suchmuster waren
# urspruenglich OHNE aufgeloeste Umlaute geschrieben ("zundspul"), `_norm` erzeugt
# aber "zuendspulen". Dadurch fiel JEDES Bauteil mit Umlaut auf den generischen
# Fallback zurueck — und weil der Dedup-Schluessel dann aus dem Rohtext statt aus
# dem Tabellenschluessel kam, entstanden zusaetzlich Duplikate ("Zuendspulen" +
# "Zuendspulen (Benziner)"). Jedes Muster muss deshalb bereits in normalisierter
# Form vorliegen.
_schief = [m for e in _KOMPONENTEN for m in e["muster"] if _norm(m) != m]
check(f"0.1 alle Bauteil-Muster liegen normalisiert vor (schief: {_schief})", _schief == [])
_schief_sym = [w for worte, _ in _FAHRSYMPTOME for w in worte if _norm(w) != w]
check(f"0.2 alle Symptom-Muster liegen normalisiert vor (schief: {_schief_sym})", _schief_sym == [])
check("0.3 Tabellenschluessel sind eindeutig",
      len({e["schluessel"] for e in _KOMPONENTEN}) == len(_KOMPONENTEN))
# Stichproben aus dem echten DB-Vokabular, die vor dem Fix falsch aufgeloest wurden
for _bauteil, _erwartet in (("Zündspulen", "zuendung"), ("Zündkerzen", "zuendung"),
                            ("Ölverbrauch", "oelverlust"), ("Ölpumpe", "oelverlust"),
                            ("Kühlsystem", "kuehlung"), ("Kühlmittelpumpe", "kuehlung"),
                            ("AGR-Kühler", "agr"), ("Einspritzdüsen", "einspritzung"),
                            ("Motorsteuergerät", "sensorik"), ("Krümmer", "abgasanlage"),
                            ("Kurbelgehäuseentlüftung (KGE)", "oelverlust")):
    _k = _komponente(_bauteil)
    check(f"0.4 '{_bauteil}' -> {_erwartet}", _k is not None and _k["schluessel"] == _erwartet)


# ============================================================================
print("\n=== A) Baureihen-Schwachstelle -> Besichtigungsaktion ===")

br_a = baureihe([{"bauteil": "Bremsen", "beschreibung": "Überdurchschnittlicher Verschleiß der Bremsbeläge.",
                  "betroffene_baujahre": "Alle", "schweregrad": "mittel"}])
ka_a, ins_a = aktionen(Req(baujahr=2020), br_a)
check("A1 mindestens eine Besichtigungsaktion", len(ka_a.besichtigung.fahrzeugspezifisch) >= 1)
b = [x for x in ka_a.besichtigung.fahrzeugspezifisch if x.kategorie == "schwachstelle"]
check("A2 Besichtigungsaktion stammt aus der Schwachstelle", len(b) == 1)
check("A3 nennt das konkrete Bauteil", "brems" in b[0].titel.lower())
check("A4 Aktion ist konkret (kein Platzhalter)", len(b[0].aktion) > 30)
check("A5 trägt die Evidence-ID der Schwachstelle", b[0].evidence_ids == [ins_a[0].id])
check("A6 Schweregrad durchgereicht", b[0].schweregrad == "mittel")
check("A7 Bereich korrekt gesetzt", b[0].bereich == "besichtigung")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== B) Motorproblem -> sinnvolle Verkäuferfrage ===")

mo_b = motor([{"bauteil": "Turbolader", "beschreibung": "Kann bei hoher Laufleistung verschleißen.",
               "baujahre": None, "kosten_ca": "1500-3000 EUR"}])
ka_b, ins_b = aktionen(Req(baujahr=2020), baureihe(), mo_b)
fragen_b = [x for x in ka_b.verkaeuferfragen.fahrzeugspezifisch if x.kategorie == "motorproblem"]
check("B1 genau eine Motorproblem-Frage", len(fragen_b) == 1)
check("B2 Frage nennt das Bauteil", "turbolader" in fragen_b[0].titel.lower())
check("B3 Frage ist eine Frage", fragen_b[0].titel.rstrip().endswith("?"))
check("B4 fragt nach Reparatur/Nachweis",
      any(w in fragen_b[0].aktion.lower() for w in ("rechnung", "beleg", "nachweis")))
check("B5 Kostenhinweis aus kosten_ca übernommen", fragen_b[0].kostenhinweis == "1500-3000 EUR")
check("B6 Evidence-ID vorhanden und gültig",
      fragen_b[0].evidence_ids and set(fragen_b[0].evidence_ids) <= valid_evidence_ids(ins_b))
check("B7 KEINE Smalltalk-Frage nach dem Verkaufsgrund",
      "warum verkauf" not in texte(ka_b.verkaeuferfragen.fahrzeugspezifisch))

# kosten_ca ohne echten Betrag ('—') darf keinen leeren Kostenhinweis erzeugen
mo_b2 = motor([{"bauteil": "Turbolader", "beschreibung": "x", "baujahre": None, "kosten_ca": "—"}])
ka_b2, _ = aktionen(Req(baujahr=2020), baureihe(), mo_b2)
check("B8 Platzhalter-Kosten ('—') werden verworfen",
      all(a.kostenhinweis is None for a in alle(ka_b2)))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== C) Beobachtbares Fahrsymptom -> Probefahrt-Aktion ===")

# C-I: über die Bauteil-Tabelle (Automatikgetriebe)
br_c1 = baureihe([{"bauteil": "Getriebe (Automatik)", "beschreibung": "Vereinzelt Berichte über Auffälligkeiten.",
                   "betroffene_baujahre": "Alle", "schweregrad": "gering"}])
ka_c1, _ = aktionen(Req(baujahr=2020), br_c1)
check("C1 Automatikgetriebe erzeugt Probefahrt-Aktion", len(ka_c1.probefahrt.fahrzeugspezifisch) == 1)
check("C2 Probefahrt-Aktion beschreibt Schaltverhalten",
      "schalt" in ka_c1.probefahrt.fahrzeugspezifisch[0].aktion.lower())

# C-II: über das Text-Tor (Bauteil ohne Tabellen-Symptom, aber Symptom im Text)
br_c2 = baureihe([{"bauteil": "AGR-Ventil", "beschreibung": "Verkokung führt zu Leistungsverlust und Notlauf.",
                   "betroffene_baujahre": "Alle", "schweregrad": "mittel"}])
ka_c2, _ = aktionen(Req(baujahr=2020), br_c2)
check("C3 AGR hat KEIN Tabellen-Fahrsymptom", _komponente("AGR-Ventil")["probefahrt"] is None)
check("C4 Symptom im Evidence-Text öffnet das zweite Tor", len(ka_c2.probefahrt.fahrzeugspezifisch) == 1)
check("C5 Probefahrt-Text nennt den Leistungsverlust",
      "leistungsverlust" in ka_c2.probefahrt.fahrzeugspezifisch[0].aktion.lower())
check("C6 Probefahrt-Aktion ist evidenzgebunden", bool(ka_c2.probefahrt.fahrzeugspezifisch[0].evidence_ids))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== D) Kein belastbares Fahrsymptom -> KEINE Probefahrt-Aktion ===")

br_d = baureihe([{"bauteil": "Wasserpumpe", "beschreibung": "Kann bei hoher Laufleistung ausfallen.",
                  "betroffene_baujahre": "Alle", "schweregrad": "mittel"}])
mo_d = motor([{"bauteil": "Ölverbrauch", "beschreibung": "Kann im Alter erhöht sein.",
               "baujahre": None, "kosten_ca": None},
              {"bauteil": "Steuerkette", "beschreibung": "Kettenspanner kann verschleißen.",
               "baujahre": None, "kosten_ca": None}])
ka_d, _ = aktionen(Req(baujahr=2020), baureihe(), mo_d)
check("D1 'Bauteil kann ausfallen' erzeugt KEINE Probefahrt-Aktion", ka_d.probefahrt.fahrzeugspezifisch == [])
check("D2 Besichtigung entsteht trotzdem", len(ka_d.besichtigung.fahrzeugspezifisch) == 2)
check("D3 Steuerkette bewusst ohne Fahrsymptom (Kaltstart-Phänomen)",
      _komponente("Steuerkette")["probefahrt"] is None)
check("D4 'kann ausfallen' ist kein Fahrsymptom im Text-Tor",
      _fahrsymptom_aus_text("Kann bei hoher Laufleistung ausfallen.") is None)
ka_d2, _ = aktionen(Req(baujahr=2020), br_d)
check("D5 Wasserpumpe: Kühlungs-Symptom aus der Tabelle ist zulässig und konkret",
      len(ka_d2.probefahrt.fahrzeugspezifisch) == 1 and "temperatur" in ka_d2.probefahrt.fahrzeugspezifisch[0].aktion.lower())


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== E) Rückruf -> konservative FIN-/Nachweis-Aktion ===")

br_e = baureihe(rueckrufe=[{"datum": "2020-03", "betroffene_baujahre": "2019-2020",
                            "mangel": "Möglicher Ausfall der Bremskraftunterstützung",
                            "abhilfe": "Software-Update", "kba_referenz": "009695"}])
ka_e, ins_e = aktionen(Req(baujahr=2020), br_e)
rr_frage = [x for x in ka_e.verkaeuferfragen.fahrzeugspezifisch if x.kategorie == "rueckruf"]
rr_dok = [x for x in ka_e.dokumente.fahrzeugspezifisch if x.kategorie == "rueckruf"]
check("E1 Rückruf erzeugt eine Verkäuferfrage", len(rr_frage) == 1)
check("E2 Rückruf erzeugt eine Dokumentaktion", len(rr_dok) == 1)
check("E3 Dokumentaktion verlangt die FIN-Prüfung", "fin" in rr_dok[0].aktion.lower())
check("E4 KBA-Referenz übernommen", "009695" in rr_dok[0].aktion)
rr_text = texte(alle(ka_e))
check("E5 behauptet NIE 'betrifft dein/dieses Fahrzeug'",
      "betrifft dein" not in rr_text and "betrifft dieses fahrzeug" not in rr_text)
check("E6 KEINE Rückruf-Besichtigungsaktion (vor Ort nicht prüfbar)",
      all(a.kategorie != "rueckruf" for a in ka_e.besichtigung.fahrzeugspezifisch))
check("E7 KEINE Rückruf-Probefahrtaktion",
      all(a.kategorie != "rueckruf" for a in ka_e.probefahrt.fahrzeugspezifisch))
check("E8 Rückruf hat höchste Priorität", rr_dok[0].prioritaet == "kritisch")

# Unklare Betroffenheit -> ausdrücklich offene Formulierung
br_e2 = baureihe(rueckrufe=[{"datum": "2019-01", "betroffene_baujahre": "Alle",
                             "mangel": "Möglicher Bruch der hinteren Federbeine",
                             "abhilfe": "Prüfen/Tausch", "kba_referenz": None}])
ka_e2, ins_e2 = aktionen(Req(baujahr=2020), br_e2)
frage_e2 = [x for x in ka_e2.verkaeuferfragen.fahrzeugspezifisch if x.kategorie == "rueckruf"][0]
check("E9 ohne Variantentreffer bleibt die Frage offen formuliert",
      "ist bekannt, ob" in frage_e2.titel.lower())
check("E10 Applicability der Insights unverändert konservativ",
      all(i.applicability != "confirmed_by_vin" for i in ins_e2 if i.kategorie == "rueckruf"))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== F) scheckheftgepflegt=True -> kein Mangel behaupten ===")

ka_f, _ = aktionen(Req(baujahr=2020, scheckheftgepflegt=True), baureihe())
sh = [a for a in ka_f.dokumente.fahrzeugspezifisch if a.id.endswith("scheckheft")]
check("F1 Scheckheft-Dokumentaktion vorhanden", len(sh) == 1)
f_text = f"{sh[0].titel} {sh[0].aktion}".lower()
check("F2 behauptet NICHT, das Scheckheft fehle",
      not any(w in f_text for w in ("fehlt", "fehlend", "nicht vorhanden", "ohne scheckheft",
                                    "kein scheckheft", "lückenhaft", "luckenhaft")))
check("F3 formuliert als Prüfung", "prüf" in f_text or "durchsehen" in f_text)
check("F4 keine Scheckheft-Frage, wenn die Angabe eindeutig ist",
      all(not a.id.endswith("scheckheft") for a in ka_f.verkaeuferfragen.fahrzeugspezifisch))

ka_f2, _ = aktionen(Req(baujahr=2020, scheckheftgepflegt=None), baureihe())
check("F5 fehlende Angabe wird zur FRAGE, nicht zur Feststellung",
      any(a.id.endswith("scheckheft") and a.titel.rstrip().endswith("?")
          for a in ka_f2.verkaeuferfragen.fahrzeugspezifisch))
check("F6 fehlende Angabe erzeugt keine Scheckheft-Mangelaussage",
      all(not a.id.endswith("scheckheft") for a in ka_f2.dokumente.fahrzeugspezifisch))

ka_f3, _ = aktionen(Req(baujahr=2020, scheckheftgepflegt=False), baureihe())
check("F7 scheckheftgepflegt=False verlangt Einzelnachweise",
      any("einzeln" in a.aktion.lower() for a in ka_f3.dokumente.fahrzeugspezifisch if a.id.endswith("scheckheft")))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== G) Wartung bei relevantem Problem -> Dokument-/Frage-Aktion ===")

mo_g = motor(kritische_wartung=[{"bauteil": "Zahnriemen", "intervall": "alle 120.000 km",
                                 "hinweis": "Reißt der Riemen, folgt ein Motorschaden."}])
ka_g, ins_g = aktionen(Req(baujahr=2020), baureihe(), mo_g)
w_frage = [a for a in ka_g.verkaeuferfragen.fahrzeugspezifisch if a.kategorie == "wartung"]
w_dok = [a for a in ka_g.dokumente.fahrzeugspezifisch if a.kategorie == "wartung"]
check("G1 Wartungspunkt erzeugt eine Verkäuferfrage", len(w_frage) == 1)
check("G2 Wartungspunkt erzeugt eine Dokumentaktion", len(w_dok) == 1)
check("G3 Frage zielt auf Zeitpunkt und Kilometerstand",
      "kilometerstand" in w_frage[0].titel.lower() or "kilometerstand" in w_frage[0].aktion.lower())
check("G4 Intervall aus der DB übernommen", "120.000" in w_frage[0].aktion)
check("G5 Wartung erzeugt KEINE Besichtigungsaktion (vor Ort nicht prüfbar)",
      all(a.kategorie != "wartung" for a in ka_g.besichtigung.fahrzeugspezifisch))
# Evidence-Integrity (§5): In der ersten P1-3-Fassung hatten Wartungsaktionen
# bewusst KEINE evidence_ids, weil `build_insights` die Tabelle `kritische_wartung`
# nicht ausgab. Diese Lücke ist geschlossen — jetzt gilt die stärkere Zusicherung.
check("G6 Wartungsaktion trägt eine valide Evidence-ID auf den konkreten Datensatz",
      all(len(a.evidence_ids) == 1 and set(a.evidence_ids) <= valid_evidence_ids(ins_g)
          for a in w_frage + w_dok))
check("G6b beide Aktionen zeigen auf denselben Wartungsdatensatz",
      {a.evidence_ids[0] for a in w_frage + w_dok}
      == {i.id for i in ins_g if i.kategorie == "wartung"})
check("G7 ohne kritische_wartung in der DB entsteht keine Wartungsaktion",
      all(a.kategorie != "wartung" for a in alle(aktionen(Req(baujahr=2020), baureihe(), motor())[0])))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== H) TÜV-Eingabe wird berücksichtigt ===")

ka_h, _ = aktionen(Req(baujahr=2020, tuev_bis="06/2027"), baureihe())
hu = [a for a in ka_h.dokumente.fahrzeugspezifisch if a.id.endswith("hu-bericht")]
check("H1 HU-Dokumentaktion vorhanden", len(hu) == 1)
check("H2 nennt den angegebenen Termin", "06/2027" in hu[0].titel)
check("H3 keine HU-Frage, wenn der Termin bekannt ist",
      all(not a.id.endswith("hu-bericht") for a in ka_h.verkaeuferfragen.fahrzeugspezifisch))

ka_h2, _ = aktionen(Req(baujahr=2020), baureihe())
check("H4 ohne TÜV-Angabe entsteht eine gezielte Nachfrage",
      any(a.id.endswith("hu-bericht") for a in ka_h2.verkaeuferfragen.fahrzeugspezifisch))
check("H5 ohne TÜV-Angabe keine Behauptung über den HU-Bericht",
      all(not a.id.endswith("hu-bericht") for a in ka_h2.dokumente.fahrzeugspezifisch))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== I) Falsches Baujahr (P0-2) -> gefiltert -> keine Aktion ===")

br_i = baureihe([{"bauteil": "Infotainmentsystem", "beschreibung": "Softwareprobleme.",
                  "betroffene_baujahre": "2017-2019", "schweregrad": "gering"}])
mo_i = motor([{"bauteil": "Injektoren", "beschreibung": "Undichtigkeiten.",
               "baujahre": "2016-2018", "kosten_ca": "800-1500 EUR"}])
ka_i_raus, ins_i_raus = aktionen(Req(baujahr=2022), br_i, mo_i)
check("I1 nicht zutreffendes Baujahr erzeugt keine Insights",
      not [i for i in ins_i_raus if i.kategorie in ("schwachstelle", "motorproblem")])
check("I2 und folglich keine Schwachstellen-/Motoraktion",
      all(a.kategorie not in ("schwachstelle", "motorproblem") for a in alle(ka_i_raus)))
ka_i_drin, ins_i_drin = aktionen(Req(baujahr=2018), br_i, mo_i)
check("I3 zutreffendes Baujahr erzeugt beide Aktionen",
      len([a for a in ka_i_drin.besichtigung.fahrzeugspezifisch
           if a.kategorie in ("schwachstelle", "motorproblem")]) == 2)
check("I4 keine zweite Baujahreslogik: Aktionen == Insight-Menge",
      len([i for i in ins_i_drin if i.kategorie in ("schwachstelle", "motorproblem")]) == 2)
# "Alle Baujahre" und unklare Angaben bleiben (konservative P0-2-Regel)
br_i2 = baureihe([{"bauteil": "Rost", "beschreibung": "Radläufe.",
                   "betroffene_baujahre": "Alle", "schweregrad": "hoch"}])
check("I5 'Alle Baujahre' bleibt erhalten",
      len(aktionen(Req(baujahr=2022), br_i2)[0].besichtigung.fahrzeugspezifisch) == 1)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== J) Mehrere Evidence-Einträge zum selben Problem -> keine Duplikate ===")

br_j = baureihe([{"bauteil": "AGR-System", "beschreibung": "Verkokung.",
                  "betroffene_baujahre": "Alle", "schweregrad": "mittel"}])
mo_j = motor([{"bauteil": "AGR-Ventil", "beschreibung": "Versottung.",
               "baujahre": None, "kosten_ca": "500-1500 EUR"},
              {"bauteil": "AGR-Kühler", "beschreibung": "Undichtigkeit.",
               "baujahre": None, "kosten_ca": None}])
ka_j, ins_j = aktionen(Req(baujahr=2020), br_j, mo_j)
agr_fragen = [a for a in ka_j.verkaeuferfragen.fahrzeugspezifisch if a.id.startswith("frage-agr")]
check("J1 drei AGR-Evidenzen ergeben EINE Verkäuferfrage", len(agr_fragen) == 1)
check("J2 die Frage führt alle drei Evidence-IDs zusammen", len(agr_fragen[0].evidence_ids) == 3)
agr_bes = [a for a in ka_j.besichtigung.fahrzeugspezifisch if a.id.startswith("besichtigung-agr")]
check("J3 auch die Besichtigung bleibt einmalig", len(agr_bes) == 1)
check("J4 IDs sind innerhalb eines Bereichs eindeutig",
      len({a.id for a in ka_j.besichtigung.fahrzeugspezifisch}) == len(ka_j.besichtigung.fahrzeugspezifisch))
check("J5 Besichtigung UND Frage zum selben Bauteil sind KEIN unerwünschtes Duplikat",
      len(agr_bes) == 1 and len(agr_fragen) == 1)
check("J6 Kostenhinweis der beitragenden Evidence bleibt erhalten",
      agr_fragen[0].kostenhinweis == "500-1500 EUR")

# Realfall aus dem Sanity-Lauf (Audi A4 B6): Baureihen-Schwachstelle
# "Zündspulen (Benziner)" und Motorproblem "Zündspulen" ergaben vor dem
# Umlaut-Fix ZWEI fast identische Probefahrt-Aktionen.
br_j2 = baureihe([{"bauteil": "Zündspulen (Benziner)", "beschreibung": "Zündaussetzer.",
                   "betroffene_baujahre": "Alle", "schweregrad": "mittel"}])
mo_j2 = motor([{"bauteil": "Zündspulen", "beschreibung": "Defekte Zündspulen führen zu Aussetzern.",
                "baujahre": None, "kosten_ca": None}])
ka_j2b, _ = aktionen(Req(baujahr=2020), br_j2, mo_j2)
check("J7 'Zündspulen' + 'Zündspulen (Benziner)' ergeben EINE Probefahrt-Aktion",
      len(ka_j2b.probefahrt.fahrzeugspezifisch) == 1)
check("J8 und EINE Besichtigungsaktion mit beiden Evidence-IDs",
      len(ka_j2b.besichtigung.fahrzeugspezifisch) == 1 and len(ka_j2b.besichtigung.fahrzeugspezifisch[0].evidence_ids) == 2)
check("J9 Umlaut-Bauteile nutzen den Tabellentext statt des Fallbacks",
      "volllast" in ka_j2b.probefahrt.fahrzeugspezifisch[0].aktion.lower())


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== K) Nur gültige Evidence-IDs ===")

br_k = baureihe([{"bauteil": "Bremsen", "beschreibung": "Verschleiß.", "betroffene_baujahre": "Alle",
                  "schweregrad": "hoch"},
                 {"bauteil": "Fahrwerk", "beschreibung": "Poltern von der Vorderachse.",
                  "betroffene_baujahre": "Alle", "schweregrad": "mittel"}],
                rueckrufe=[{"datum": "2020-07", "betroffene_baujahre": "2019-2020",
                            "mangel": "Ausfall der Lenkunterstützung", "abhilfe": "Update",
                            "kba_referenz": "009903"}])
mo_k = motor([{"bauteil": "Turbolader", "beschreibung": "Leistungsverlust.", "baujahre": None,
               "kosten_ca": "1500-3000 EUR"}],
             kritische_wartung=[{"bauteil": "Zahnriemen", "intervall": "120.000 km", "hinweis": "x"}])
req_k = Req(baujahr=2020, tuev_bis="06/2027", scheckheftgepflegt=True, unfallfrei="ja", vorbesitzer=2)
ka_k, ins_k = aktionen(req_k, br_k, mo_k)
gueltig = valid_evidence_ids(ins_k)
check("K1 jede Evidence-ID existiert wirklich",
      all(set(a.evidence_ids) <= gueltig for a in alle(ka_k)))
check("K2 keine leeren/doppelten IDs innerhalb einer Aktion",
      all(len(a.evidence_ids) == len(set(a.evidence_ids)) and all(a.evidence_ids)
          for a in alle(ka_k)))
check("K3 jede fahrzeugspezifische Aktion ist evidenzgebunden",
      all(a.evidence_ids for a in alle(ka_k)
          if a.kategorie in ("schwachstelle", "motorproblem", "rueckruf")))
check("K4 Inserat-Aktionen erfinden KEINE ID (reine Nutzereingabe)",
      all(a.evidence_ids == [] for a in alle(ka_k) if a.kategorie == "inserat"))
check("K4b Wartungsaktionen sind jetzt evidenzgebunden (§5)",
      all(a.evidence_ids and set(a.evidence_ids) <= gueltig
          for a in alle(ka_k) if a.kategorie == "wartung"))
check("K5 der Marktvergleich-Insight erzeugt keine Aktion",
      all(a.kategorie != "marktvergleich" for a in alle(ka_k)))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== L) Stabile IDs über wiederholte Ausführung ===")

laeufe = [aktionen(req_k, br_k, mo_k)[0] for _ in range(3)]
ids = [[a.id for a in alle(k)] for k in laeufe]
check("L1 IDs über drei Läufe identisch", ids[0] == ids[1] == ids[2])
check("L2 keine zufälligen IDs (kein UUID-Muster)",
      all("-" in i and len(i) < 60 for i in ids[0]))
check("L3 IDs sind inhaltsbasiert, nicht positionsbasiert",
      "besichtigung-bremsen" in ids[0] and "frage-turbolader" in ids[0])
# Reihenfolge der DB-Sätze ändern -> gleiche IDs (Insight-Nummern verschieben sich)
br_k_gedreht = baureihe(list(reversed(br_k["schwachstellen_baureihe"])), br_k["rueckrufe"])
ka_l, _ = aktionen(req_k, br_k_gedreht, mo_k)
check("L4 IDs bleiben stabil, wenn sich die Insight-Reihenfolge verschiebt",
      {a.id for a in ka_l.besichtigung.fahrzeugspezifisch} == {a.id for a in laeufe[0].besichtigung.fahrzeugspezifisch})
check("L5 vollständige Aktionsobjekte sind reproduzierbar",
      [a.model_dump() for a in alle(laeufe[0])] == [a.model_dump() for a in alle(laeufe[2])])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== M) Deterministische Priorisierung ===")

check("M1 höchste Relevanz zuerst (Besichtigung)",
      [a.rang for a in ka_k.besichtigung.fahrzeugspezifisch] == sorted([a.rang for a in ka_k.besichtigung.fahrzeugspezifisch], reverse=True))
check("M2 höchste Relevanz zuerst (alle Bereiche)",
      all([a.rang for a in liste] == sorted([a.rang for a in liste], reverse=True)
          for liste in (ka_k.besichtigung.fahrzeugspezifisch, ka_k.probefahrt.fahrzeugspezifisch, ka_k.verkaeuferfragen.fahrzeugspezifisch, ka_k.dokumente.fahrzeugspezifisch)))
check("M3 Rückruf rangiert über einer geringen Schwachstelle",
      max(a.rang for a in ka_k.dokumente.fahrzeugspezifisch if a.kategorie == "rueckruf")
      > max(a.rang for a in ka_k.dokumente.fahrzeugspezifisch if a.kategorie == "inserat"))
check("M4 hoher Schweregrad -> Priorität 'kritisch'",
      [a.prioritaet for a in ka_k.besichtigung.fahrzeugspezifisch if a.titel == "Bremsen"] == ["kritisch"])
check("M5 nur zulässige Prioritätswerte",
      {a.prioritaet for a in alle(ka_k)} <= {"kritisch", "hoch", "mittel"})
check("M6 Priorität folgt monoton dem Rang",
      all((a.prioritaet == "kritisch") == (a.rang >= 850) for a in alle(ka_k)))
# Ranggleichheit wird stabil nach ID gebrochen
gleichrangig = [a for a in ka_k.dokumente.fahrzeugspezifisch if a.rang == ka_k.dokumente.fahrzeugspezifisch[0].rang]
check("M7 Ranggleichstand deterministisch nach ID sortiert",
      [a.id for a in gleichrangig] == sorted(a.id for a in gleichrangig))
check("M8 Mengenbegrenzung pro Bereich eingehalten",
      all(len(l) <= MAX_SPEZIFISCH_PRO_BEREICH for l in (ka_k.besichtigung.fahrzeugspezifisch, ka_k.probefahrt.fahrzeugspezifisch,
                                              ka_k.verkaeuferfragen.fahrzeugspezifisch, ka_k.dokumente.fahrzeugspezifisch)))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== N/O) Marktpreis-Unabhängigkeit (completed_no_market == mit Marktpreis) ===")

# Identische technische Lage; einmal MIT deterministischer Marktanalyse in den
# Insights (PFAD A), einmal OHNE (PFAD B / completed_no_market).
markt = Marktanalyse(
    gefunden=12, verwendet=8, anzahl_sehr_aehnlich=4, anzahl_aehnlich=4, anzahl_bedingt=0,
    median_eur=24000, spanne_min_eur=22000, spanne_max_eur=26000,
    angebot_eur=21000, differenz_eur=-3000, differenz_pct=-12.5,
    datenqualitaet="hoch", methode="Median aus 8 Vergleichen.",
    beobachtungen=[Preisbeobachtung(preis_eur=24000, vergleichbarkeit="sehr_aehnlich",
                                    quelle_url="https://example.test/a")],
)
req_no = Req(baujahr=2020, preis_eur=21000, tuev_bis="06/2027", scheckheftgepflegt=True,
             unfallfrei="ja", vorbesitzer=2)
ins_ohne = build_insights(br_k, mo_k, [], req_no, check_typ="kauf")
ins_mit = build_insights(br_k, mo_k, [{"typ": "web", "titel": "T", "url": "https://example.test/a",
                                       "qualitaet": "Marktplatz"}],
                         req_no, check_typ="kauf", marktanalyse=markt)
ka_ohne = build_kaufaktionen(req_no, br_k, mo_k, ins_ohne)
ka_mit = build_kaufaktionen(req_no, br_k, mo_k, ins_mit)

check("N1 ohne Marktdaten entstehen technische Aktionen",
      len(ka_ohne.besichtigung.fahrzeugspezifisch) >= 1 and len(ka_ohne.verkaeuferfragen.fahrzeugspezifisch) >= 1
      and len(ka_ohne.dokumente.fahrzeugspezifisch) >= 1)
check("N2 auch die Probefahrt bleibt befüllt", len(ka_ohne.probefahrt.fahrzeugspezifisch) >= 1)
check("O1 mit Marktpreis exakt dieselben Aktionen",
      [a.model_dump() for a in alle(ka_ohne)] == [a.model_dump() for a in alle(ka_mit)])

# §15 verbietet die MARKTPREIS-Ebene: kein Preisurteil, keine Nachverhandlung,
# keine "günstig/teuer"-Aussage. Ein Reparaturkostenwert aus der DB-Spalte
# `kosten_ca` ist ausdrücklich etwas anderes (§2 erlaubt den Kostenhinweis) — er
# stammt aus der Fahrzeugdatenbank, nicht aus dem Marktvergleich. Der Test trennt
# beides: verbotene Marktvokabeln dürfen NIRGENDS auftauchen, und jede
# Währungsangabe muss nachweislich aus einem `kostenhinweis` stammen.
_MARKTWORTE = ("preis", "günstig", "guenstig", "teuer", "schnäppchen", "schnappchen",
               "nachverhandel", "verhandel", "marktwert", "median", "marktgerecht",
               "unter markt", "über markt", "angebotspreis", "handeln")
markt_text = texte(alle(ka_mit))
treffer = [w for w in _MARKTWORTE if w in markt_text]
check(f"O2 keinerlei Marktpreis-Aussage in den Aktionen (Treffer: {treffer})", treffer == [])
_kosten_werte = {(a.kostenhinweis or "").lower() for a in alle(ka_mit) if a.kostenhinweis}
_waehrung = [a for a in alle(ka_mit)
             if any(w in f"{a.titel} {a.aktion}".lower() for w in ("€", "eur"))]
check("O2b jede Währungsangabe stammt aus einem DB-Reparaturkostenwert",
      all(a.kostenhinweis and a.kostenhinweis.lower() in _kosten_werte
          and a.kostenhinweis.lower() in f"{a.titel} {a.aktion}".lower()
          for a in _waehrung))
check("O2c Kostenhinweise erscheinen nur bei Motorproblem-Aktionen",
      all(a.kategorie == "motorproblem" for a in alle(ka_mit) if a.kostenhinweis))
check("O3 build_kaufaktionen bekommt strukturell keinen Preisparameter",
      "price_assessment" not in build_kaufaktionen.__code__.co_varnames
      and "marktanalyse" not in build_kaufaktionen.__code__.co_varnames)
check("O4 keine Aktion aus der Kategorie 'preis'/'marktvergleich'",
      all(a.kategorie in (None, "schwachstelle", "motorproblem", "rueckruf", "wartung", "inserat")
          for a in alle(ka_mit)))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== P) Dünne DB / unbekanntes Fahrzeug -> nichts erfunden ===")

ka_p, ins_p = aktionen(Req(baujahr=2020, marke="Unbekannt", modell="Modell X"), None, None)
check("P1 ohne Baureihe keine Insights", ins_p == [])
check("P2 keine Besichtigungsaktion erfunden", ka_p.besichtigung.fahrzeugspezifisch == [])
check("P3 keine Probefahrt-Aktion erfunden", ka_p.probefahrt.fahrzeugspezifisch == [])
check("P4 keine fahrzeugspezifische Aktion überhaupt",
      all(a.kategorie == "inserat" for a in alle(ka_p)))
check("P5 nur Nachfragen zu tatsächlich fehlenden Inserat-Angaben",
      {a.id for a in ka_p.verkaeuferfragen.fahrzeugspezifisch}
      == {"frage-scheckheft", "frage-hu-bericht", "frage-unfall", "frage-vorbesitzer"})
check("P6 keine generische 30-Punkte-Checkliste", len(alle(ka_p)) <= 6)

# Baureihe erkannt, aber ohne jede Schwachstelle/Rückruf: ebenfalls nichts erfinden
ka_p2, _ = aktionen(Req(baujahr=2020, tuev_bis="06/2027", scheckheftgepflegt=True,
                        unfallfrei="ja", vorbesitzer=1), baureihe(), motor())
check("P7 leere Baureihe erzeugt keine technische Aktion",
      all(a.kategorie == "inserat" for a in alle(ka_p2)))
check("P8 leere Baureihe: Besichtigung bleibt leer", ka_p2.besichtigung.fahrzeugspezifisch == [])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== Q) Backward Compatibility ===")

alt = KaufCheckResponse(bericht="alter Check", empfehlung="kaufen", preis_bewertung="marktgerecht",
                        quelle="datenbank", vertrauen="hoch")
check("Q1 Response ohne kaufaktionen bleibt gültig", alt.kaufaktionen is not None)
check("Q2 Default sind vier leere Listen",
      alt.kaufaktionen.besichtigung.fahrzeugspezifisch == [] and alt.kaufaktionen.probefahrt.fahrzeugspezifisch == []
      and alt.kaufaktionen.verkaeuferfragen.fahrzeugspezifisch == [] and alt.kaufaktionen.dokumente.fahrzeugspezifisch == [])
roh = {"bericht": "x", "empfehlung": "kaufen", "preis_bewertung": "unbekannt",
       "quelle": "web", "vertrauen": "niedrig", "insights": [], "key_findings": []}
check("Q3 altes gespeichertes Dict lädt weiterhin",
      KaufCheckResponse(**roh).kaufaktionen.besichtigung.fahrzeugspezifisch == [])
check("Q4 kaufaktionen ist kein Pflichtfeld",
      KaufCheckResponse.model_fields["kaufaktionen"].is_required() is False)
neu = KaufCheckResponse(**{**roh, "kaufaktionen": ka_k})
check("Q5 neues Feld serialisiert und lädt wieder",
      KaufCheckResponse(**neu.model_dump()).kaufaktionen.besichtigung.fahrzeugspezifisch[0].id
      == ka_k.besichtigung.fahrzeugspezifisch[0].id)
check("Q6 bestehende Felder unverändert vorhanden",
      {"insights", "key_findings", "price_assessment", "research_status",
       "empfehlung_evidence_ids"} <= set(KaufCheckResponse.model_fields))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== R) Jeder Bereich besitzt eine Basis-Checkliste ===")

ka_r, _ = aktionen(req_k, br_k, mo_k)
for _b in BEREICHE:
    check(f"R1 {_b}: Basis-Punkte vorhanden", len(basis(ka_r, _b)) > 0)
check("R2 alle Basis-Punkte sind als 'basis' markiert",
      all(a.typ == TYP_BASIS for _, pl in listen(ka_r) for a in pl.basis))
check("R3 alle fahrzeugspezifischen Punkte sind als solche markiert",
      all(a.typ == TYP_SPEZIFISCH for _, pl in listen(ka_r) for a in pl.fahrzeugspezifisch))
check("R4 Basis-Punkte tragen die Priorität 'basis' (keine künstliche Dringlichkeit)",
      all(a.prioritaet == PRIO_BASIS for _, pl in listen(ka_r) for a in pl.basis))
check("R5 Basis-Punkte tragen keine Evidence-IDs",
      all(a.evidence_ids == [] for _, pl in listen(ka_r) for a in pl.basis))
check("R6 jeder Punkt hat Bereich, Titel und konkrete Aktion",
      all(a.bereich and a.titel and len(a.aktion) > 25 for a in alle_punkte(ka_r)))
check("R7 jeder Basis-Punkt hat eine Abschnittsgruppe",
      all(a.gruppe for _, pl in listen(ka_r) for a in pl.basis))
check("R8 IDs von Basis und fahrzeugspezifisch überschneiden sich nie",
      all(not ({a.id for a in pl.basis} & {a.id for a in pl.fahrzeugspezifisch})
          for _, pl in listen(ka_r)))
check("R9 Basis-IDs sind am Präfix erkennbar",
      all("-basis-" in a.id for _, pl in listen(ka_r) for a in pl.basis))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== S) Katalogumfang (Zielbereiche §19) ===")

check(f"S1 Besichtigung 12-20 Basis-Punkte ({len(BASIS_BESICHTIGUNG)})",
      12 <= len(BASIS_BESICHTIGUNG) <= 20)
check(f"S2 Probefahrt 15-25 Basis-Punkte ({len(BASIS_PROBEFAHRT)})",
      15 <= len(BASIS_PROBEFAHRT) <= 25)
check(f"S3 Verkäuferfragen 8-15 Basis-Punkte ({len(BASIS_VERKAEUFERFRAGEN)})",
      8 <= len(BASIS_VERKAEUFERFRAGEN) <= 15)
check(f"S4 Dokumente 8-15 Basis-Punkte ({len(BASIS_DOKUMENTE)})",
      8 <= len(BASIS_DOKUMENTE) <= 15)
check("S5 Probefahrt-Basis deutlich umfangreicher als die alte Obergrenze von 6",
      len(BASIS_PROBEFAHRT) >= 15)
check("S6 Probefahrt deckt alle sechs Phasen ab",
      {g for _, g, *_ in BASIS_PROBEFAHRT} ==
      {"Vor Fahrtbeginn", "Anfahren und Rangieren", "Normale Fahrt", "Bremsen",
       "Höhere Geschwindigkeit", "Nach der Fahrt"})
for _name, _kat in (("Besichtigung", BASIS_BESICHTIGUNG), ("Probefahrt", BASIS_PROBEFAHRT),
                    ("Verkäuferfragen", BASIS_VERKAEUFERFRAGEN), ("Dokumente", BASIS_DOKUMENTE)):
    check(f"S7 {_name}: Katalogschlüssel eindeutig",
          len({k for k, *_ in _kat}) == len(_kat))
check("S8 alle Verkäufer-Basispunkte sind Fragen",
      all(t.rstrip().endswith("?") for _, _, t, *_ in BASIS_VERKAEUFERFRAGEN))
check("S9 Basis-Texte sind eigenständig verständlich (Print, §16)",
      all(len(a) >= 40 for _kat in (BASIS_BESICHTIGUNG, BASIS_PROBEFAHRT,
                                    BASIS_VERKAEUFERFRAGEN, BASIS_DOKUMENTE)
          for _, _, _, a, _, _ in _kat))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== T) Keine riskanten oder unzulässigen Fahranweisungen (§7) ===")

_RISIKO = ("vollbremsung", "notbremsung", "grenzbereich", "driften", "drift ", "burnout",
           "so schnell wie möglich", "ausdrehen", "vmax", "höchstgeschwindigkeit erreichen",
           "abs auslösen", "abs-regelung provozieren", "handbremse ziehen während",
           "kickdown bis", "rote bereich", "überholen", "risiko eingehen")
_pf_text = texte(basis(ka_r, "probefahrt"))
_treffer_risiko = [w for w in _RISIKO if w in _pf_text]
check(f"T1 keine riskante Anweisung in der Probefahrt-Basis ({_treffer_risiko})",
      _treffer_risiko == [])
check("T2 auch die fahrzeugspezifischen Probefahrtpunkte bleiben unbedenklich",
      [w for w in _RISIKO if w in texte(ka_r.probefahrt.fahrzeugspezifisch)] == [])
# Bewusst ein Fahrzeug OHNE Bremsen-Evidence: bei ka_r verdrängt die spezifische
# Bremsenprüfung den Basis-Bremspunkt korrekt (siehe U6), er wäre hier gar nicht da.
ka_t, _ = aktionen(Req(baujahr=2020), baureihe(), motor())
_bremspunkt = [a for a in basis(ka_t, "probefahrt") if a.id.endswith("bremswirkung")]
check("T3 der Bremstest trägt einen Sicherheitshinweis",
      len(_bremspunkt) == 1 and _bremspunkt[0].hinweis
      and "sicher" in _bremspunkt[0].hinweis.lower())
check("T3b der Bremstest verlangt keine Vollbremsung",
      "kontrolliert" in _bremspunkt[0].aktion.lower())
_tempo = [a for a in basis(ka_t, "probefahrt") if a.gruppe == "Höhere Geschwindigkeit"]
check("T4 alle Punkte bei höherem Tempo verweisen auf die zulässige Geschwindigkeit",
      _tempo and all("zulässig" in (a.hinweis or "").lower() for a in _tempo))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== U) Dedup Basis <-> fahrzeugspezifisch (§18) ===")

check("U1 Präfix-Muster trifft", _wird_abgedeckt(("rueckruf-*",), {"rueckruf-009695"}))
check("U2 Präfix-Muster trifft nicht ohne Treffer", not _wird_abgedeckt(("rueckruf-*",), {"bremsen"}))
check("U3 exakter Schlüssel trifft", _wird_abgedeckt(("bremsen",), {"bremsen"}))
check("U4 leeres deckt blendet nie aus", not _wird_abgedeckt((), {"bremsen"}))

# br_k hat eine Bremsen-Schwachstelle -> der allgemeine Bremsen-Basispunkt entfällt
_bes_basis_ids = {a.id for a in basis(ka_r, "besichtigung")}
check("U5 spezifische Bremsenprüfung verdrängt den Basis-Bremsenpunkt",
      "besichtigung-bremsen" in {a.id for a in ka_r.besichtigung.fahrzeugspezifisch}
      and "besichtigung-basis-bremsen" not in _bes_basis_ids)
check("U6 der Basis-Bremspunkt der PROBEFAHRT entfällt ebenfalls",
      "probefahrt-basis-bremswirkung" not in {a.id for a in basis(ka_r, "probefahrt")})
# Ohne passende Evidence bleibt der Basis-Punkt erhalten
ka_u, _ = aktionen(Req(baujahr=2020), baureihe(), motor())
check("U7 ohne Fahrzeug-Evidence bleibt der Basis-Bremsenpunkt stehen",
      "besichtigung-basis-bremsen" in {a.id for a in basis(ka_u, "besichtigung")})
check("U8 Dedup entfernt nur explizit abgedeckte Punkte, nicht die ganze Gruppe",
      len(basis(ka_r, "besichtigung")) >= len(BASIS_BESICHTIGUNG) - 6)
check("U9 unterschiedliche Prüfungen bleiben nebeneinander bestehen",
      "besichtigung-basis-unterboden" in _bes_basis_ids
      and "besichtigung-basis-fin" in _bes_basis_ids)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== V) Evidence-Integrity: kritische Wartung (§5) ===")

mo_v = motor(kritische_wartung=[{"bauteil": "Zahnriemen", "intervall": "alle 120.000 km",
                                 "hinweis": "Reißt der Riemen, folgt ein Motorschaden."},
                                {"bauteil": "Getriebeöl", "intervall": "alle 60.000 km",
                                 "hinweis": "Wird oft versäumt."}])
ka_v, ins_v = aktionen(Req(baujahr=2020), baureihe(), mo_v)
_wartung_ins = [i for i in ins_v if i.kategorie == "wartung"]
check("V1 kritische Wartung erzeugt eigene Insights", len(_wartung_ins) == 2)
check("V2 Wartungs-Insights folgen den DB-Kategorien",
      [i.id for i in _wartung_ins] == ["wartung-1", "wartung-2"])
_w_aktionen = [a for a in alle_punkte(ka_v) if a.kategorie == "wartung"]
check("V3 Wartungsaktionen entstehen in Fragen und Dokumenten", len(_w_aktionen) == 4)
check("V4 JEDE Wartungsaktion trägt jetzt eine valide Evidence-ID",
      all(a.evidence_ids and set(a.evidence_ids) <= valid_evidence_ids(ins_v)
          for a in _w_aktionen))
check("V5 Evidence-ID zeigt auf den konkreten Wartungsdatensatz",
      {a.evidence_ids[0] for a in _w_aktionen} == {"wartung-1", "wartung-2"})
check("V6 Intervall aus der DB erreicht den Aktionstext",
      any("120.000" in a.aktion for a in _w_aktionen))
check("V7 Wartung erzeugt weiterhin keine Besichtigungsaktion",
      all(a.kategorie != "wartung" for a in ka_v.besichtigung.fahrzeugspezifisch))

# Bestehende IDs dürfen sich durch die neue Kategorie NICHT verschoben haben
ka_v2, ins_v2 = aktionen(Req(baujahr=2020), br_k, mo_k)
check("V8 Schwachstellen-/Rückruf-/Motor-IDs unverändert an erster Stelle",
      [i.id for i in ins_v2 if i.kategorie != "wartung"]
      == ["schwachstelle-1", "schwachstelle-2", "rueckruf-3", "motorproblem-4"])
check("V9 Wartungs-Insights folgen danach",
      [i.id for i in ins_v2 if i.kategorie == "wartung"] == ["wartung-5"])
# Kernzusicherung der Platzierung: die Wartungs-ID darf NICHT davon abhängen, ob
# eine Marktrecherche Ergebnisse geliefert hat — sonst wären die daraus abgeleiteten
# Kaufaktionen marktabhängig (P0-1-Invariante).
_w_ohne = [i.id for i in build_insights(br_k, mo_k, [], req_no, check_typ="kauf")
           if i.kategorie == "wartung"]
_w_mit = [i.id for i in build_insights(
    br_k, mo_k, [{"typ": "web", "titel": "T", "url": "https://example.test/a"}],
    req_no, check_typ="kauf", marktanalyse=markt) if i.kategorie == "wartung"]
check(f"V9b Wartungs-IDs identisch mit und ohne Marktdaten ({_w_ohne} / {_w_mit})",
      _w_ohne == _w_mit and _w_ohne != [])

# Ohne erkannte Motorvariante gibt es weder Insight noch Aktion
ka_v3, ins_v3 = aktionen(Req(baujahr=2020), br_k, None)
check("K/V10 ohne erkannten Motor keine Wartungs-Evidence",
      not [i for i in ins_v3 if i.kategorie == "wartung"])
check("K/V11 und folglich keine Wartungsaktion",
      all(a.kategorie != "wartung" for a in alle_punkte(ka_v3)))
# Baujahr ausserhalb der Generation -> in der echten Pipeline liefert find_baureihe
# eine andere/keine Baureihe; hier direkt geprueft: ohne Baureihe/Motor nichts.
ka_v4, ins_v4 = aktionen(Req(baujahr=1998), None, None)
check("K/V12 unbekanntes Fahrzeug erzeugt keinerlei Wartungs-Evidence",
      not [i for i in ins_v4 if i.kategorie == "wartung"]
      and all(a.kategorie != "wartung" for a in alle_punkte(ka_v4)))
check("V13 Wartungs-Insights nur im Kaufcheck, nicht im Verkaufscheck",
      not [i for i in build_insights(baureihe(), mo_v, [], Req(baujahr=2020),
                                     check_typ="verkauf") if i.kategorie == "wartung"])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== W) Print-/PDF-Bereitschaft (§13/§14/§15) ===")

for _b, _pl in listen(ka_r):
    check(f"W1 {_b}: eigenständig serialisierbar", isinstance(_pl.model_dump(), dict))
check("W2 jede Liste kennt ihren Bereich", all(pl.bereich == b for b, pl in listen(ka_r)))
check("W3 jede Liste hat eine Titelzeile für den Ausdruck",
      all(pl.export_title == EXPORT_TITEL[b] for b, pl in listen(ka_r)))
check("W4 jede Liste trägt die Fahrzeug-Kurzbezeichnung",
      all(pl.fahrzeug == "TestMarke TestModell G1 (2020)" for _, pl in listen(ka_r)))
check("W5 Titelzeilen sind unterscheidbar",
      len({pl.export_title for _, pl in listen(ka_r)}) == 4)
check("W6 KEIN Sammel-Export-Feld im Prüfplan",
      not any(f in Kaufaktionen.model_fields for f in
              ("alle", "kombiniert", "combined_pdf", "export_all", "export_all_checklists",
               "gesamt", "sammelblatt")))
check("W7 KEIN kombiniertes Feld in der Einzelliste",
      not any(f in Pruefliste.model_fields for f in ("alle", "punkte", "kombiniert")))
check("W8 Prüfplan besteht aus genau vier Prüflisten",
      set(Kaufaktionen.model_fields) == set(BEREICHE))
check("W9 keine PDF-Bibliothek eingebunden",
      not any(m in io_open_src for m in ("reportlab", "weasyprint", "fpdf", "pdfkit")))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== X) Marktpreis-Unabhängigkeit beider Ebenen ===")

ka_x_ohne = build_kaufaktionen(req_no, br_k, mo_k, ins_ohne)
ka_x_mit = build_kaufaktionen(req_no, br_k, mo_k, ins_mit)
check("X1 vollständige Prüfpläne identisch mit und ohne Marktdaten",
      ka_x_ohne.model_dump() == ka_x_mit.model_dump())
_alle_text = texte(alle_punkte(ka_x_mit))
_markt_treffer = [w for w in _MARKTWORTE if w in _alle_text]
check(f"X2 keine Marktpreis-Aussage in Basis oder Spezifisch ({_markt_treffer})",
      _markt_treffer == [])
check("X3 auch die Basis-Kataloge enthalten keine Preisfrage",
      not any(w in texte([Kaufaktion(id="x", bereich="b", titel=t, aktion=a,
                                     prioritaet="basis", hinweis=h)])
              for _kat in (BASIS_BESICHTIGUNG, BASIS_PROBEFAHRT, BASIS_VERKAEUFERFRAGEN,
                           BASIS_DOKUMENTE)
              for _, _, t, a, h, _ in _kat
              for w in ("verhandel", "schnäppchen", "marktwert", "zu teuer", "günstiger preis")))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== Y) Dünner DB-Fall: 0 spezifisch, aber vollständige Basis (§22-F) ===")

ka_y, ins_y = aktionen(Req(baujahr=2020, marke="Unbekannt", modell="Modell X"), None, None)
check("Y1 keine Insights", ins_y == [])
check("Y2 keine fahrzeugspezifische Besichtigung", ka_y.besichtigung.fahrzeugspezifisch == [])
check("Y3 keine fahrzeugspezifische Probefahrt", ka_y.probefahrt.fahrzeugspezifisch == [])
check("Y4 trotzdem vollständige Besichtigungs-Basis",
      len(basis(ka_y, "besichtigung")) == len(BASIS_BESICHTIGUNG))
check("Y5 trotzdem vollständige Probefahrt-Basis",
      len(basis(ka_y, "probefahrt")) == len(BASIS_PROBEFAHRT))
check("Y6 trotzdem vollständige Dokumenten-Basis",
      len(basis(ka_y, "dokumente")) == len(BASIS_DOKUMENTE))
check("Y7 alle vier Listen praktisch nutzbar", all(len(pl.basis) >= 8 for _, pl in listen(ka_y)))
check("Y8 Fahrzeugbezeichnung aus dem Inserat übernommen",
      ka_y.besichtigung.fahrzeug == "Unbekannt Modell X (2020)")
check("Y9 kein einziger Punkt behauptet etwas über dieses Fahrzeug",
      all(a.typ == TYP_BASIS or a.kategorie == "inserat" for a in alle_punkte(ka_y)))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== Z) Stabile IDs & Nutzereingaben über beide Ebenen ===")

_z1 = build_kaufaktionen(req_k, br_k, mo_k, build_insights(br_k, mo_k, [], req_k, check_typ="kauf"))
_z2 = build_kaufaktionen(req_k, br_k, mo_k, build_insights(br_k, mo_k, [], req_k, check_typ="kauf"))
check("Z1 vollständiger Prüfplan über zwei Läufe identisch", _z1.model_dump() == _z2.model_dump())
check("Z2 Basis-IDs sind inhaltsbasiert und stabil",
      [a.id for a in _z1.probefahrt.basis] == [a.id for a in _z2.probefahrt.basis])
check("Z3 IDs im gesamten Prüfplan eindeutig",
      len({a.id for a in alle_punkte(_z1)}) == len(alle_punkte(_z1)))

# §11: scheckheftgepflegt=True -> keine Mangelaussage, aber der allgemeine
# Dokumenten-Standard bleibt selbstverständlich bestehen.
ka_z, _ = aktionen(Req(baujahr=2020, scheckheftgepflegt=True), baureihe())
_dok_text = texte([*ka_z.dokumente.fahrzeugspezifisch, *basis(ka_z, "dokumente")])
check("Z4 keine Behauptung, das Scheckheft fehle",
      not any(w in _dok_text for w in ("scheckheft fehlt", "kein scheckheft",
                                       "scheckheft nicht vorhanden", "lückenhaft")))
check("Z5 allgemeine Dokumentenprüfung bleibt trotzdem vorhanden (§22-M)",
      len(basis(ka_z, "dokumente")) >= 8)
check("Z6 der spezifische Scheckheft-Punkt verdrängt nur den Serviceheft-Basispunkt",
      "dokument-scheckheft" in {a.id for a in ka_z.dokumente.fahrzeugspezifisch}
      and "dokument-basis-serviceheft" not in {a.id for a in basis(ka_z, "dokumente")}
      and "dokument-basis-wartungsrechnungen" in {a.id for a in basis(ka_z, "dokumente")})

# Rückwärtskompatibilität der neuen Struktur
_leer = KaufCheckResponse(bericht="x", empfehlung="kaufen", preis_bewertung="unbekannt",
                          quelle="web", vertrauen="niedrig")
check("Z7 Alt-Check ohne kaufaktionen liefert vier leere Prüflisten",
      all(getattr(_leer.kaufaktionen, b).fahrzeugspezifisch == []
          and getattr(_leer.kaufaktionen, b).basis == [] for b in BEREICHE))
check("Z8 Alt-Check kennt trotzdem Bereich und Titelzeile",
      _leer.kaufaktionen.probefahrt.export_title == "Probefahrt-Checkliste")
check("Z9 neuer Prüfplan überlebt einen Serialisierungs-Rundlauf",
      KaufCheckResponse(**KaufCheckResponse(
          bericht="x", empfehlung="kaufen", preis_bewertung="unbekannt", quelle="db",
          vertrauen="hoch", kaufaktionen=_z1).model_dump()).kaufaktionen.model_dump()
      == _z1.model_dump())


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("Alle P1-3-Zusicherungen erfüllt.")
