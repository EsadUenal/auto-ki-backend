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

    python test_kaufaktionen.py
"""
from app.evidence import build_insights, valid_evidence_ids
from app.kaufaktionen import (
    build_kaufaktionen, MAX_PRO_BEREICH, _komponente, _fahrsymptom_aus_text,
    _KOMPONENTEN, _FAHRSYMPTOME, _norm,
)
from app.models import KaufCheckResponse, Marktanalyse, Preisbeobachtung

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
    return [*ka.besichtigung, *ka.probefahrt, *ka.verkaeuferfragen, *ka.dokumente]


def texte(liste):
    return " ".join(f"{a.titel} {a.aktion}" for a in liste).lower()


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
check("A1 mindestens eine Besichtigungsaktion", len(ka_a.besichtigung) >= 1)
b = [x for x in ka_a.besichtigung if x.kategorie == "schwachstelle"]
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
fragen_b = [x for x in ka_b.verkaeuferfragen if x.kategorie == "motorproblem"]
check("B1 genau eine Motorproblem-Frage", len(fragen_b) == 1)
check("B2 Frage nennt das Bauteil", "turbolader" in fragen_b[0].titel.lower())
check("B3 Frage ist eine Frage", fragen_b[0].titel.rstrip().endswith("?"))
check("B4 fragt nach Reparatur/Nachweis",
      any(w in fragen_b[0].aktion.lower() for w in ("rechnung", "beleg", "nachweis")))
check("B5 Kostenhinweis aus kosten_ca übernommen", fragen_b[0].kostenhinweis == "1500-3000 EUR")
check("B6 Evidence-ID vorhanden und gültig",
      fragen_b[0].evidence_ids and set(fragen_b[0].evidence_ids) <= valid_evidence_ids(ins_b))
check("B7 KEINE Smalltalk-Frage nach dem Verkaufsgrund",
      "warum verkauf" not in texte(ka_b.verkaeuferfragen))

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
check("C1 Automatikgetriebe erzeugt Probefahrt-Aktion", len(ka_c1.probefahrt) == 1)
check("C2 Probefahrt-Aktion beschreibt Schaltverhalten",
      "schalt" in ka_c1.probefahrt[0].aktion.lower())

# C-II: über das Text-Tor (Bauteil ohne Tabellen-Symptom, aber Symptom im Text)
br_c2 = baureihe([{"bauteil": "AGR-Ventil", "beschreibung": "Verkokung führt zu Leistungsverlust und Notlauf.",
                   "betroffene_baujahre": "Alle", "schweregrad": "mittel"}])
ka_c2, _ = aktionen(Req(baujahr=2020), br_c2)
check("C3 AGR hat KEIN Tabellen-Fahrsymptom", _komponente("AGR-Ventil")["probefahrt"] is None)
check("C4 Symptom im Evidence-Text öffnet das zweite Tor", len(ka_c2.probefahrt) == 1)
check("C5 Probefahrt-Text nennt den Leistungsverlust",
      "leistungsverlust" in ka_c2.probefahrt[0].aktion.lower())
check("C6 Probefahrt-Aktion ist evidenzgebunden", bool(ka_c2.probefahrt[0].evidence_ids))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== D) Kein belastbares Fahrsymptom -> KEINE Probefahrt-Aktion ===")

br_d = baureihe([{"bauteil": "Wasserpumpe", "beschreibung": "Kann bei hoher Laufleistung ausfallen.",
                  "betroffene_baujahre": "Alle", "schweregrad": "mittel"}])
mo_d = motor([{"bauteil": "Ölverbrauch", "beschreibung": "Kann im Alter erhöht sein.",
               "baujahre": None, "kosten_ca": None},
              {"bauteil": "Steuerkette", "beschreibung": "Kettenspanner kann verschleißen.",
               "baujahre": None, "kosten_ca": None}])
ka_d, _ = aktionen(Req(baujahr=2020), baureihe(), mo_d)
check("D1 'Bauteil kann ausfallen' erzeugt KEINE Probefahrt-Aktion", ka_d.probefahrt == [])
check("D2 Besichtigung entsteht trotzdem", len(ka_d.besichtigung) == 2)
check("D3 Steuerkette bewusst ohne Fahrsymptom (Kaltstart-Phänomen)",
      _komponente("Steuerkette")["probefahrt"] is None)
check("D4 'kann ausfallen' ist kein Fahrsymptom im Text-Tor",
      _fahrsymptom_aus_text("Kann bei hoher Laufleistung ausfallen.") is None)
ka_d2, _ = aktionen(Req(baujahr=2020), br_d)
check("D5 Wasserpumpe: Kühlungs-Symptom aus der Tabelle ist zulässig und konkret",
      len(ka_d2.probefahrt) == 1 and "temperatur" in ka_d2.probefahrt[0].aktion.lower())


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== E) Rückruf -> konservative FIN-/Nachweis-Aktion ===")

br_e = baureihe(rueckrufe=[{"datum": "2020-03", "betroffene_baujahre": "2019-2020",
                            "mangel": "Möglicher Ausfall der Bremskraftunterstützung",
                            "abhilfe": "Software-Update", "kba_referenz": "009695"}])
ka_e, ins_e = aktionen(Req(baujahr=2020), br_e)
rr_frage = [x for x in ka_e.verkaeuferfragen if x.kategorie == "rueckruf"]
rr_dok = [x for x in ka_e.dokumente if x.kategorie == "rueckruf"]
check("E1 Rückruf erzeugt eine Verkäuferfrage", len(rr_frage) == 1)
check("E2 Rückruf erzeugt eine Dokumentaktion", len(rr_dok) == 1)
check("E3 Dokumentaktion verlangt die FIN-Prüfung", "fin" in rr_dok[0].aktion.lower())
check("E4 KBA-Referenz übernommen", "009695" in rr_dok[0].aktion)
rr_text = texte(alle(ka_e))
check("E5 behauptet NIE 'betrifft dein/dieses Fahrzeug'",
      "betrifft dein" not in rr_text and "betrifft dieses fahrzeug" not in rr_text)
check("E6 KEINE Rückruf-Besichtigungsaktion (vor Ort nicht prüfbar)",
      all(a.kategorie != "rueckruf" for a in ka_e.besichtigung))
check("E7 KEINE Rückruf-Probefahrtaktion",
      all(a.kategorie != "rueckruf" for a in ka_e.probefahrt))
check("E8 Rückruf hat höchste Priorität", rr_dok[0].prioritaet == "kritisch")

# Unklare Betroffenheit -> ausdrücklich offene Formulierung
br_e2 = baureihe(rueckrufe=[{"datum": "2019-01", "betroffene_baujahre": "Alle",
                             "mangel": "Möglicher Bruch der hinteren Federbeine",
                             "abhilfe": "Prüfen/Tausch", "kba_referenz": None}])
ka_e2, ins_e2 = aktionen(Req(baujahr=2020), br_e2)
frage_e2 = [x for x in ka_e2.verkaeuferfragen if x.kategorie == "rueckruf"][0]
check("E9 ohne Variantentreffer bleibt die Frage offen formuliert",
      "ist bekannt, ob" in frage_e2.titel.lower())
check("E10 Applicability der Insights unverändert konservativ",
      all(i.applicability != "confirmed_by_vin" for i in ins_e2 if i.kategorie == "rueckruf"))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== F) scheckheftgepflegt=True -> kein Mangel behaupten ===")

ka_f, _ = aktionen(Req(baujahr=2020, scheckheftgepflegt=True), baureihe())
sh = [a for a in ka_f.dokumente if a.id.endswith("scheckheft")]
check("F1 Scheckheft-Dokumentaktion vorhanden", len(sh) == 1)
f_text = f"{sh[0].titel} {sh[0].aktion}".lower()
check("F2 behauptet NICHT, das Scheckheft fehle",
      not any(w in f_text for w in ("fehlt", "fehlend", "nicht vorhanden", "ohne scheckheft",
                                    "kein scheckheft", "lückenhaft", "luckenhaft")))
check("F3 formuliert als Prüfung", "prüf" in f_text or "durchsehen" in f_text)
check("F4 keine Scheckheft-Frage, wenn die Angabe eindeutig ist",
      all(not a.id.endswith("scheckheft") for a in ka_f.verkaeuferfragen))

ka_f2, _ = aktionen(Req(baujahr=2020, scheckheftgepflegt=None), baureihe())
check("F5 fehlende Angabe wird zur FRAGE, nicht zur Feststellung",
      any(a.id.endswith("scheckheft") and a.titel.rstrip().endswith("?")
          for a in ka_f2.verkaeuferfragen))
check("F6 fehlende Angabe erzeugt keine Scheckheft-Mangelaussage",
      all(not a.id.endswith("scheckheft") for a in ka_f2.dokumente))

ka_f3, _ = aktionen(Req(baujahr=2020, scheckheftgepflegt=False), baureihe())
check("F7 scheckheftgepflegt=False verlangt Einzelnachweise",
      any("einzeln" in a.aktion.lower() for a in ka_f3.dokumente if a.id.endswith("scheckheft")))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== G) Wartung bei relevantem Problem -> Dokument-/Frage-Aktion ===")

mo_g = motor(kritische_wartung=[{"bauteil": "Zahnriemen", "intervall": "alle 120.000 km",
                                 "hinweis": "Reißt der Riemen, folgt ein Motorschaden."}])
ka_g, ins_g = aktionen(Req(baujahr=2020), baureihe(), mo_g)
w_frage = [a for a in ka_g.verkaeuferfragen if a.kategorie == "wartung"]
w_dok = [a for a in ka_g.dokumente if a.kategorie == "wartung"]
check("G1 Wartungspunkt erzeugt eine Verkäuferfrage", len(w_frage) == 1)
check("G2 Wartungspunkt erzeugt eine Dokumentaktion", len(w_dok) == 1)
check("G3 Frage zielt auf Zeitpunkt und Kilometerstand",
      "kilometerstand" in w_frage[0].titel.lower() or "kilometerstand" in w_frage[0].aktion.lower())
check("G4 Intervall aus der DB übernommen", "120.000" in w_frage[0].aktion)
check("G5 Wartung erzeugt KEINE Besichtigungsaktion (vor Ort nicht prüfbar)",
      all(a.kategorie != "wartung" for a in ka_g.besichtigung))
check("G6 Wartung erzeugt KEINE erfundene Evidence-ID",
      all(a.evidence_ids == [] for a in w_frage + w_dok))
check("G7 ohne kritische_wartung in der DB entsteht keine Wartungsaktion",
      all(a.kategorie != "wartung" for a in alle(aktionen(Req(baujahr=2020), baureihe(), motor())[0])))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== H) TÜV-Eingabe wird berücksichtigt ===")

ka_h, _ = aktionen(Req(baujahr=2020, tuev_bis="06/2027"), baureihe())
hu = [a for a in ka_h.dokumente if a.id.endswith("hu-bericht")]
check("H1 HU-Dokumentaktion vorhanden", len(hu) == 1)
check("H2 nennt den angegebenen Termin", "06/2027" in hu[0].titel)
check("H3 keine HU-Frage, wenn der Termin bekannt ist",
      all(not a.id.endswith("hu-bericht") for a in ka_h.verkaeuferfragen))

ka_h2, _ = aktionen(Req(baujahr=2020), baureihe())
check("H4 ohne TÜV-Angabe entsteht eine gezielte Nachfrage",
      any(a.id.endswith("hu-bericht") for a in ka_h2.verkaeuferfragen))
check("H5 ohne TÜV-Angabe keine Behauptung über den HU-Bericht",
      all(not a.id.endswith("hu-bericht") for a in ka_h2.dokumente))


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
      len([a for a in ka_i_drin.besichtigung
           if a.kategorie in ("schwachstelle", "motorproblem")]) == 2)
check("I4 keine zweite Baujahreslogik: Aktionen == Insight-Menge",
      len([i for i in ins_i_drin if i.kategorie in ("schwachstelle", "motorproblem")]) == 2)
# "Alle Baujahre" und unklare Angaben bleiben (konservative P0-2-Regel)
br_i2 = baureihe([{"bauteil": "Rost", "beschreibung": "Radläufe.",
                   "betroffene_baujahre": "Alle", "schweregrad": "hoch"}])
check("I5 'Alle Baujahre' bleibt erhalten",
      len(aktionen(Req(baujahr=2022), br_i2)[0].besichtigung) == 1)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== J) Mehrere Evidence-Einträge zum selben Problem -> keine Duplikate ===")

br_j = baureihe([{"bauteil": "AGR-System", "beschreibung": "Verkokung.",
                  "betroffene_baujahre": "Alle", "schweregrad": "mittel"}])
mo_j = motor([{"bauteil": "AGR-Ventil", "beschreibung": "Versottung.",
               "baujahre": None, "kosten_ca": "500-1500 EUR"},
              {"bauteil": "AGR-Kühler", "beschreibung": "Undichtigkeit.",
               "baujahre": None, "kosten_ca": None}])
ka_j, ins_j = aktionen(Req(baujahr=2020), br_j, mo_j)
agr_fragen = [a for a in ka_j.verkaeuferfragen if a.id.startswith("frage-agr")]
check("J1 drei AGR-Evidenzen ergeben EINE Verkäuferfrage", len(agr_fragen) == 1)
check("J2 die Frage führt alle drei Evidence-IDs zusammen", len(agr_fragen[0].evidence_ids) == 3)
agr_bes = [a for a in ka_j.besichtigung if a.id.startswith("besichtigung-agr")]
check("J3 auch die Besichtigung bleibt einmalig", len(agr_bes) == 1)
check("J4 IDs sind innerhalb eines Bereichs eindeutig",
      len({a.id for a in ka_j.besichtigung}) == len(ka_j.besichtigung))
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
      len(ka_j2b.probefahrt) == 1)
check("J8 und EINE Besichtigungsaktion mit beiden Evidence-IDs",
      len(ka_j2b.besichtigung) == 1 and len(ka_j2b.besichtigung[0].evidence_ids) == 2)
check("J9 Umlaut-Bauteile nutzen den Tabellentext statt des Fallbacks",
      "volllast" in ka_j2b.probefahrt[0].aktion.lower())


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
check("K4 Inserat-/Wartungsaktionen erfinden KEINE ID",
      all(a.evidence_ids == [] for a in alle(ka_k) if a.kategorie in ("inserat", "wartung")))
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
      {a.id for a in ka_l.besichtigung} == {a.id for a in laeufe[0].besichtigung})
check("L5 vollständige Aktionsobjekte sind reproduzierbar",
      [a.model_dump() for a in alle(laeufe[0])] == [a.model_dump() for a in alle(laeufe[2])])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== M) Deterministische Priorisierung ===")

check("M1 höchste Relevanz zuerst (Besichtigung)",
      [a.rang for a in ka_k.besichtigung] == sorted([a.rang for a in ka_k.besichtigung], reverse=True))
check("M2 höchste Relevanz zuerst (alle Bereiche)",
      all([a.rang for a in liste] == sorted([a.rang for a in liste], reverse=True)
          for liste in (ka_k.besichtigung, ka_k.probefahrt, ka_k.verkaeuferfragen, ka_k.dokumente)))
check("M3 Rückruf rangiert über einer geringen Schwachstelle",
      max(a.rang for a in ka_k.dokumente if a.kategorie == "rueckruf")
      > max(a.rang for a in ka_k.dokumente if a.kategorie == "inserat"))
check("M4 hoher Schweregrad -> Priorität 'kritisch'",
      [a.prioritaet for a in ka_k.besichtigung if a.titel == "Bremsen"] == ["kritisch"])
check("M5 nur zulässige Prioritätswerte",
      {a.prioritaet for a in alle(ka_k)} <= {"kritisch", "hoch", "mittel"})
check("M6 Priorität folgt monoton dem Rang",
      all((a.prioritaet == "kritisch") == (a.rang >= 850) for a in alle(ka_k)))
# Ranggleichheit wird stabil nach ID gebrochen
gleichrangig = [a for a in ka_k.dokumente if a.rang == ka_k.dokumente[0].rang]
check("M7 Ranggleichstand deterministisch nach ID sortiert",
      [a.id for a in gleichrangig] == sorted(a.id for a in gleichrangig))
check("M8 Mengenbegrenzung pro Bereich eingehalten",
      all(len(l) <= MAX_PRO_BEREICH for l in (ka_k.besichtigung, ka_k.probefahrt,
                                              ka_k.verkaeuferfragen, ka_k.dokumente)))


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
      len(ka_ohne.besichtigung) >= 1 and len(ka_ohne.verkaeuferfragen) >= 1
      and len(ka_ohne.dokumente) >= 1)
check("N2 auch die Probefahrt bleibt befüllt", len(ka_ohne.probefahrt) >= 1)
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
check("P2 keine Besichtigungsaktion erfunden", ka_p.besichtigung == [])
check("P3 keine Probefahrt-Aktion erfunden", ka_p.probefahrt == [])
check("P4 keine fahrzeugspezifische Aktion überhaupt",
      all(a.kategorie == "inserat" for a in alle(ka_p)))
check("P5 nur Nachfragen zu tatsächlich fehlenden Inserat-Angaben",
      {a.id for a in ka_p.verkaeuferfragen}
      == {"frage-scheckheft", "frage-hu-bericht", "frage-unfall", "frage-vorbesitzer"})
check("P6 keine generische 30-Punkte-Checkliste", len(alle(ka_p)) <= 6)

# Baureihe erkannt, aber ohne jede Schwachstelle/Rückruf: ebenfalls nichts erfinden
ka_p2, _ = aktionen(Req(baujahr=2020, tuev_bis="06/2027", scheckheftgepflegt=True,
                        unfallfrei="ja", vorbesitzer=1), baureihe(), motor())
check("P7 leere Baureihe erzeugt keine technische Aktion",
      all(a.kategorie == "inserat" for a in alle(ka_p2)))
check("P8 leere Baureihe: Besichtigung bleibt leer", ka_p2.besichtigung == [])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== Q) Backward Compatibility ===")

alt = KaufCheckResponse(bericht="alter Check", empfehlung="kaufen", preis_bewertung="marktgerecht",
                        quelle="datenbank", vertrauen="hoch")
check("Q1 Response ohne kaufaktionen bleibt gültig", alt.kaufaktionen is not None)
check("Q2 Default sind vier leere Listen",
      alt.kaufaktionen.besichtigung == [] and alt.kaufaktionen.probefahrt == []
      and alt.kaufaktionen.verkaeuferfragen == [] and alt.kaufaktionen.dokumente == [])
roh = {"bericht": "x", "empfehlung": "kaufen", "preis_bewertung": "unbekannt",
       "quelle": "web", "vertrauen": "niedrig", "insights": [], "key_findings": []}
check("Q3 altes gespeichertes Dict lädt weiterhin",
      KaufCheckResponse(**roh).kaufaktionen.besichtigung == [])
check("Q4 kaufaktionen ist kein Pflichtfeld",
      KaufCheckResponse.model_fields["kaufaktionen"].is_required() is False)
neu = KaufCheckResponse(**{**roh, "kaufaktionen": ka_k})
check("Q5 neues Feld serialisiert und lädt wieder",
      KaufCheckResponse(**neu.model_dump()).kaufaktionen.besichtigung[0].id
      == ka_k.besichtigung[0].id)
check("Q6 bestehende Felder unverändert vorhanden",
      {"insights", "key_findings", "price_assessment", "research_status",
       "empfehlung_evidence_ids"} <= set(KaufCheckResponse.model_fields))


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("Alle P1-3-Zusicherungen erfüllt.")
