from __future__ import annotations

"""
Kuratiertes Ergebnis des KBA-GESAMTABGLEICHS.

WIE DIESE DATEN ENTSTANDEN SIND
-------------------------------
`app/kba_reconciliation.py` hat alle 749 VIRA-Rueckrufe deterministisch gegen
den amtlichen KBA-Gesamtexport (7.816 Rueckrufe, abgerufen 2026-08-27)
klassifiziert. Der Matcher ERZEUGT Kandidaten, er ENTSCHEIDET aber nichts: jede
der 17 stark belegten Zuordnungen wurde danach einzeln gelesen und beurteilt.
Zwei davon sind bei dieser Pruefung durchgefallen und stehen deshalb NICHT in
dieser Datei (siehe VERWORFEN unten). Genau so lief schon der Recall-Pilot —
Maschine schlaegt vor, Mensch entscheidet.

Die Klassenverteilung des Gesamtlaufs:

    EXACT_OFFICIAL_MATCH        1   (0,1 %)
    CORRECTABLE_MATCH          54   (7,2 %)
    PARTIAL_MATCH             383  (51,1 %)
    CONTRADICTED              286  (38,2 %)
    NO_MATCH                   25   (3,3 %)

Von den 55 EXACT/CORRECTABLE erreichten nur 17 die Belegstufe `stark`
(Referenztreffer ODER mindestens zwei trennscharfe Begriffe). Die uebrigen 38
haengen an einem einzigen gemeinsamen Wort und werden ausdruecklich NICHT
verifiziert — "aehnlicher Text gefunden" ist kein Beleg.

DER GROESSTE BEFUND: DIE REFERENZEN
-----------------------------------
570 VIRA-Zeilen tragen eine `kba_referenz`. Davon ist GENAU EINE amtlich
gueltig fuer ihr Fahrzeug und beschreibt denselben Mangel (#808 / 12223).

  * 286 Zeilen tragen eine Nummer, die amtlich zu einem ANDEREN Fahrzeug
    gehoert — haeufig sogar zu einem anderen Hersteller (ein BMW-Rueckruf mit
    der Nummer eines Renault Kangoo, eines Kawasaki- oder Yamaha-Motorrads).
  * 270 Nummern existieren im amtlichen Bestand ueberhaupt nicht.
  * 2 Nummern existieren fuer das richtige Fahrzeug, beschreiben aber einen
    voellig anderen Rueckruf (#131 Motorhaubenverriegelung gegen amtlich
    Kettenspanner; #449 Kraftstoffleitung gegen amtlich Bordnetzsteuergeraet).
    Zufallskollisionen.

Alle nicht amtlich bestaetigten Referenzen werden entfernt (§5 des Auftrags:
"Bei nicht bestaetigter Nummer: entfernen, nicht anzeigen"). Der Rueckrufinhalt
bleibt in jedem Fall vollstaendig erhalten — es verschwindet nur die
Scheingenauigkeit einer erfundenen Aktennummer.

VERWORFEN TROTZ STARKER MASCHINENBEWERTUNG
-------------------------------------------
#324 (Audi A6 C7) und #377 (Audi RS 6 Avant C7) wurden vom Matcher dem
amtlichen Rueckruf 6831 zugeordnet. Die VIRA-Zeilen behaupten "Fehlerhafte
Software im Airbag-Steuergeraet"; der amtliche Datensatz beschreibt ein "nicht
der Spezifikation entsprechendes Mischungsverhaeltnis der Zuendchemikalien" mit
der Abhilfe "Airbags oder Gurtstraffer ersetzen". Gleiches Bauteil, voellig
anderer Mechanismus — eine Software-Aussage laesst sich damit nicht belegen.
Beide bleiben unverifiziert; ihre erfundenen Referenzen werden wie ueberall
entfernt.
"""

GEPRUEFT_AM = "2026-08-27"

KBA_QUELLE = (
    "KBA-Rueckrufdatenbank, amtlicher Gesamtexport (7.816 Rueckrufe), "
    "abgerufen 2026-08-27"
)
KBA_URL = (
    "https://www.kba-online.de/rrdb/buerger/api/rueckruf/export?format=csv&type=cars"
)


# ─────────────────────────────────────────────────────────────────────────────
# 1) VERIFIZIERTE ZUORDNUNGEN
# ─────────────────────────────────────────────────────────────────────────────
#
# (fakt_id, baureihe_id, erwartet, neu, kba_referenz, herstellercode, notiz)
#
# `erwartet` ist die Precondition; geschrieben wird nur bei exakt diesem
# Ausgangszustand. Der Bauzeitraum in `neu` ist immer die SCHNITTMENGE des
# amtlichen Produktionszeitraums mit dem Bauzeitraum der Baureihe — die Zeile
# behauptet nie mehr, als die amtliche Quelle hergibt, und nie etwas, das der
# Baureihe widerspricht.

VERIFIZIERTE_ZUORDNUNGEN: tuple[tuple, ...] = (

    (13, "bmw-3er-g20-g21",
     {"datum": "2020-10", "kba_referenz": None},
     {"datum": "2020-09", "kba_referenz": "10176"},
     "10176", "0061540500, 0061560500",
     "DIE AMTLICHE ENTSPRECHUNG ZUM NHTSA-DATENSATZ AUS DEM RECALL-PILOT. Im "
     "Pilot war der Hochvoltspeicher-Rueckruf nur ueber die US-Behoerde NHTSA "
     "(20V-601) belegbar, weil der KBA-Export damals durchgehend HTTP 503 "
     "lieferte; die Zeile trug deshalb bewusst keine KBA-Nummer. Der amtliche "
     "deutsche Datensatz 10176 beschreibt exakt denselben Mangel — Kurzschluss "
     "zweier Zellen im Hochvoltspeicher mit erhoehter Brandgefahr — mit "
     "Produktionszeitraum 2020, Herstellercodes 0061540500/0061560500, "
     "KBA-ueberwacht, und listet das Modell '3'. Drei trennscharfe Begriffe "
     "gemeinsam (Hochvoltspeicher, Kurzschluss, Zellen). Der Bauzeitraum '2020 "
     "(Plug-in-Hybrid)' bleibt unveraendert: der Klammerzusatz haelt die "
     "Eingrenzung auf Plug-in-Hybride maschinenlesbar, die der amtliche "
     "Datensatz im Modellfeld nicht ausdruecken kann."),

    (24, "bmw-2er-active-tourer-f45",
     {"datum": "2018-08", "betroffene_baujahre": "2014-2017", "kba_referenz": "008064"},
     {"datum": "2019-11", "betroffene_baujahre": "2014-2021", "kba_referenz": "8124"},
     "8124", "diverse Codes - siehe amtliche Abhilfemassnahme",
     "Amtlich: Undichtigkeit am Abgasrueckfuehrungsmodul kann zum Fahrzeugbrand "
     "fuehren; der AGR-Kuehler wird geprueft und ggf. ersetzt. Produktion "
     "2010-2022, KBA-ueberwacht, Modellliste enthaelt '2'. Zwei trennscharfe "
     "Begriffe (Abgasrueckfuehrungsmodul, Kuehler). Die bisherige Referenz "
     "'008064' gehoert amtlich nicht hierher. Bauzeitraum auf die Schnittmenge "
     "mit dem F45 (2014-2021) gesetzt."),

    (93, "bmw-1er-f20-f21",
     {"datum": "2018-08", "betroffene_baujahre": "2011-2017", "kba_referenz": "7607"},
     {"datum": "2019-11", "betroffene_baujahre": "2011-2019", "kba_referenz": "8124"},
     "8124", "diverse Codes - siehe amtliche Abhilfemassnahme",
     "Derselbe amtliche Rueckruf wie bei #24 — die Aktion 8124 umfasst zwoelf "
     "BMW-Baureihen, die Modellliste enthaelt '1'. Das ist KEINE Dublette: zwei "
     "verschiedene VIRA-Baureihen sind von derselben herstellerweiten Aktion "
     "betroffen, und genau so bildet die Datenbank das ab (ein Rueckruf je "
     "Baureihe). Bauzeitraum auf die Schnittmenge mit dem F20/F21 gesetzt."),

    (114, "mercedes-benz-b-klasse-w247",
     {"datum": "2022-03-08", "betroffene_baujahre": "2020-2021", "kba_referenz": "011603"},
     {"datum": "2021-02", "betroffene_baujahre": "2019-2020", "kba_referenz": "10589"},
     "10589", None,
     "Amtlich: bei fehlender Netzabdeckung funktioniert der automatische Notruf "
     "wegen eines Softwarefehlers nicht; die Software des Kommunikationsmoduls "
     "wird aktualisiert. Produktion 2016-2020. Die vollstaendige amtliche "
     "Modellliste nennt B-KLASSE ausdruecklich (im gekuerzten Report war sie "
     "abgeschnitten — deshalb einzeln nachgeprueft). Zwei trennscharfe Begriffe "
     "(Kommunikationsmoduls, Softwarefehlers). Der bisherige VIRA-Bauzeitraum "
     "2020-2021 ragte ueber das amtliche Fenster hinaus und wird auf die "
     "Schnittmenge mit dem W247 (ab 2019) korrigiert."),

    (144, "mercedes-benz-s-klasse-w222",
     {"datum": "2018-03", "betroffene_baujahre": "2017-2018", "kba_referenz": "8079"},
     {"datum": "2017-12", "betroffene_baujahre": "2016-2017", "kba_referenz": "7465"},
     "7465", None,
     "Amtlich: Ausfall der elektrischen Lenkunterstuetzung, Abhilfe Erneuerung "
     "der elektrischen Servolenkung. Produktion 2016-2017, Modelle S-KLASSE und "
     "E-KLASSE. Zwei trennscharfe Begriffe. Der VIRA-Bauzeitraum 2017-2018 lag "
     "teilweise ausserhalb und wird auf das amtliche Fenster korrigiert."),

    (150, "mercedes-benz-s-klasse-w223",
     {"datum": "2022-03-01", "betroffene_baujahre": "2020-2022", "kba_referenz": "011603"},
     {"datum": "2021-07", "betroffene_baujahre": "2020-2021", "kba_referenz": "11075"},
     "11075", None,
     "Amtlich: eingeschraenkte Funktion des Notrufsystems, Abhilfe Software-"
     "Update inklusive korrekter Konfiguration des Kommunikationsmoduls. "
     "Produktion 2020-2021, Modell S-KLASSE. Drei trennscharfe Begriffe. Die "
     "VIRA-Zeile trug dieselbe erfundene Referenz '011603' wie #114 — ein "
     "deutlicher Hinweis darauf, wie die Nummern entstanden sind."),

    (196, "mercedes-benz-cla-c118",
     {"datum": "2020-03-10", "betroffene_baujahre": "2019-2020", "kba_referenz": "009699"},
     {"datum": "2021-02", "betroffene_baujahre": "2019-2020", "kba_referenz": "10589"},
     "10589", None,
     "Derselbe amtliche eCall-Rueckruf wie #114; die Modellliste nennt CLA "
     "ausdruecklich. Bauzeitraum passt bereits, nur Datum und Referenz waren "
     "falsch."),

    (221, "mercedes-benz-eqa-h243",
     {"datum": "2023-08", "betroffene_baujahre": "2021-2023", "kba_referenz": "012903"},
     {"datum": "2024-03", "betroffene_baujahre": "2021-2024", "kba_referenz": "13692"},
     "13692", None,
     "Amtlich: 'Lenkungssoftware', Abhilfe Aktualisierung der Software der "
     "elektrischen Lenkung. Produktion 2017-2024, Modellliste enthaelt EQA. "
     "Zwei trennscharfe Begriffe. Bauzeitraum auf die Schnittmenge mit dem H243 "
     "erweitert."),

    (264, "bmw-7er-f01/f02",
     {"datum": "2014-03", "betroffene_baujahre": "2008-2013", "kba_referenz": "80 14 11"},
     {"datum": "2016-11", "betroffene_baujahre": "2008-2012", "kba_referenz": "6565"},
     "6565", None,
     "Amtlich: fehlerhafte Programmierung des Airbag-Zentralsensors "
     "beeintraechtigt die Insassenschutzeinrichtungen; der Zentralsensor wird "
     "durch einen mit korrekter Software ersetzt. Produktion 2008-2012, Modelle "
     "'7, 5'. Das deckt sich mit der VIRA-Aussage (Airbag-Steuergeraet, "
     "Fehlfunktion der Airbags) im Mechanismus — Programmierung/Software — und "
     "nicht nur im Bauteil. Bauzeitraum auf das amtliche Fenster verengt."),

    (544, "opel-insignia-b",
     {"datum": "2021-06", "betroffene_baujahre": "2021", "kba_referenz": None},
     {"datum": "2021-05", "betroffene_baujahre": "2021", "kba_referenz": "10743"},
     "10743", "E212103163 (21-C-097) O2D (alt: E212103161 (21-C-077))",
     "BESTAETIGT DIE PILOT-KORREKTUR AMTLICH. Der Recall-Pilot hatte diese Zeile "
     "anhand eines Fachmediums von 2018-08/2017-2018 auf 2021-06/2021 "
     "korrigiert, konnte die KBA-Nummer aber nicht belegen und liess das Feld "
     "leer. Der amtliche Datensatz 10743 bestaetigt jetzt Bauzeitraum und "
     "Fehlerbild und liefert zusaetzlich den Herstellercode — inklusive des im "
     "Pilot notierten Vorgaengercodes E212103161 (21-C-077). Vier trennscharfe "
     "Begriffe. Nur das Datum wird noch um einen Monat auf die amtliche "
     "Veroeffentlichung korrigiert."),

    (546, "opel-insignia-b",
     {"datum": "2022-02", "kba_referenz": None},
     {"datum": "2022-02", "kba_referenz": "11422"},
     "11422", "E222115640 (22-C-013) O7A",
     "SCHLIESST DEN OFFENEN PUNKT AUS DEM SAFETY-CHECK. Diese Referenz war im "
     "Pilot auf 'partially_verified' zurueckgestuft und die Nummer entfernt "
     "worden, weil sich trotz gezielter Suche keine KBA-Primaerquelle finden "
     "liess — nur uebereinstimmende Anwaltskanzlei-Seiten. Der jetzt "
     "erreichbare amtliche Export bestaetigt jede damals berichtete Angabe: "
     "Referenz 11422, Herstellercode E222115640 (22-C-013) O7A, "
     "veroeffentlicht 2022-02-17, Produktion 2013-2018, KBA-ueberwacht, und im "
     "Feld 'Moegliche Eingrenzung der betroffenen Modelle' woertlich '1,3 l und "
     "1,6 l Dieselmotor Euro 6 mit AGR + NSK (LNT)'. Der im Pilot gesetzte "
     "Bauzeitraum '2017-2018 (1,6 l Diesel Euro 6)' bleibt deshalb unveraendert "
     "— er ist die Schnittmenge des amtlichen Fensters mit dem Insignia B und "
     "die amtliche Motoreingrenzung, maschinenlesbar gehalten."),

    (676, "skoda-citigo-erste-generation",
     {"datum": "2014-03", "betroffene_baujahre": "2011-2013", "kba_referenz": None},
     {"datum": "2013-05", "betroffene_baujahre": "2013", "kba_referenz": "4191"},
     "4191", None,
     "Amtlich: wegen eines Steuergeraetefehlers loesen die Seiten-Airbags nicht "
     "korrekt aus; Abhilfe ist die Parametrierung der Airbagsteuergeraete. "
     "Produktion 2013, Modell CITIGO. Mechanismus (Steuergeraet/Parametrierung) "
     "und Wirkung (Airbags loesen nicht korrekt aus) decken sich mit der "
     "VIRA-Aussage. Bauzeitraum auf das amtliche Jahr verengt."),

    (739, "toyota-prius-xw20",
     {"datum": "2010-02", "betroffene_baujahre": "2003-2009", "kba_referenz": None},
     {"datum": "2010-02", "betroffene_baujahre": "2009", "kba_referenz": "2811"},
     "2811", None,
     "Amtlich: bei leichtem Abbremsen auf glatter oder holpriger Fahrbahn kann "
     "sich der Bremsweg verlaengern; das ABS-Steuergeraet erhaelt eine neue "
     "Software. Produktion 2009-2010, Modell PRIUS, Veroeffentlichung "
     "2010-02-17. Das ist der bekannte Prius-Bremsenrueckruf. Der "
     "VIRA-Bauzeitraum 2003-2009 war viel zu weit und wird auf die Schnittmenge "
     "mit dem amtlichen Fenster verengt."),

    (779, "hyundai-santa-fe-zweite-generation",
     {"datum": "2010-02", "betroffene_baujahre": "2006-2009", "kba_referenz": None},
     {"datum": "2020-06", "betroffene_baujahre": "2006-2009", "kba_referenz": "9873"},
     "9873", None,
     "Amtlich: Eintritt von Schmutz und Feuchtigkeit kann im ABS-Modul einen "
     "Kurzschluss ausloesen, in der Folge droht Brand; Abhilfe ist der Einbau "
     "eines Relais-Kits. Produktion 2005-2009, Modell SANTA FE. Drei "
     "trennscharfe Begriffe. Das VIRA-Datum lag zehn Jahre daneben."),

    (808, "opel-insignia-b",
     {"datum": "2022-10", "kba_referenz": "12223"},
     {"datum": "2022-10", "kba_referenz": "12223"},
     "12223", "KBT",
     "Bereits im Insignia-Nachtrag Feld fuer Feld aus derselben amtlichen "
     "Quelle uebernommen und dort verifiziert. Der Gesamtabgleich bestaetigt "
     "ihn als einzigen EXACT_OFFICIAL_MATCH des ganzen Bestands: als einzige "
     "der 570 VIRA-Referenzen ist 12223 amtlich gueltig fuer ihr Fahrzeug UND "
     "beschreibt denselben Mangel. Hier steht er nur der Vollstaendigkeit "
     "halber; inhaltlich aendert sich nichts."),
)


# ─────────────────────────────────────────────────────────────────────────────
# 2) DUBLETTEN
# ─────────────────────────────────────────────────────────────────────────────
#
# (zu_loeschen, kanonisch, baureihe_id, begruendung)
#
# Kriterium: WORTGLEICHER Mangel UND wortgleiche Abhilfe an DERSELBEN Baureihe.
# Das ist eine VIRA-interne Feststellung, die kein Matching braucht. Zusaetzlich
# wurde fuer jede Gruppe amtlich geprueft, ob es sich nicht doch um zwei
# getrennte reale Aktionen handelt — in allen drei Faellen fuehrt der amtliche
# Export hoechstens EINEN Rueckruf zu diesem Thema. Behalten wird jeweils die
# niedrigere ID (die kanonische Seed-Zeile).

DUBLETTEN: tuple[tuple, ...] = (
    (211, 210, "mercedes-benz-g-klasse-w-463",
     "Wortgleich mit #210 in Mangel und Abhilfe (Befestigung der "
     "Lenkzwischenwelle). Der amtliche Export kennt fuer die G-Klasse genau "
     "EINEN Lenkungs-Rueckruf dieser Art (6986, Produktion 2012-2017, nicht "
     "korrekte Verschraubung der Lenkungskupplung) — nicht zwei. Die "
     "unterschiedlichen Daten und Referenzen der beiden VIRA-Zeilen sind "
     "erfunden und begruenden keine zweite Aktion."),

    (279, 275, "audi-a1-gb",
     "Wortgleich mit #275 in Mangel und Abhilfe (Bruch der hinteren Feder). Der "
     "amtliche Export enthaelt fuer den Audi A1 UEBERHAUPT KEINEN Federbruch-"
     "Rueckruf — es gibt also keinen Beleg fuer zwei getrennte Aktionen. Die "
     "verbleibende Zeile #275 bleibt als unverifizierter Hinweis erhalten."),

    (386, 385, "audi-tt-rs-fv/8s",
     "Wortgleich mit #385 in Mangel und Abhilfe (Befestigungsschrauben der "
     "Bremssattelhalter). Der amtliche Export enthaelt fuer den Audi TT keinen "
     "Bremssattel-Rueckruf. Die verbleibende Zeile #385 bleibt als "
     "unverifizierter Hinweis erhalten."),
)


def verifizierte_ids() -> set[int]:
    """Fakt-IDs, deren `kba_referenz` amtlich bestaetigt ist und bleiben darf."""
    return {e[0] for e in VERIFIZIERTE_ZUORDNUNGEN}


def _selbsttest() -> None:
    """Formale Konsistenz der kuratierten Daten (wird vom Test aufgerufen)."""
    ids = [e[0] for e in VERIFIZIERTE_ZUORDNUNGEN]
    assert len(ids) == len(set(ids)), "doppelte Zuordnung fuer dieselbe Fakt-ID"
    assert len(ids) == 15, f"erwartet 15 verifizierte Zuordnungen, sind {len(ids)}"

    for fakt_id, baureihe_id, erwartet, neu, ref, _code, notiz in VERIFIZIERTE_ZUORDNUNGEN:
        assert isinstance(fakt_id, int) and fakt_id > 0
        assert baureihe_id and isinstance(baureihe_id, str)
        assert set(neu) == set(erwartet), \
            f"#{fakt_id}: erwartet/neu muessen dieselben Spalten nennen"
        assert neu.get("kba_referenz") == ref, \
            f"#{fakt_id}: Zielreferenz und Verifikationsreferenz weichen ab"
        assert ref and not ref.startswith("0"), \
            f"#{fakt_id}: Referenz muss die amtliche Schreibweise haben"
        assert len(notiz or "") >= 120, f"#{fakt_id}: Notiz zu duenn"

    weg = [d[0] for d in DUBLETTEN]
    behalten = [d[1] for d in DUBLETTEN]
    assert len(weg) == len(set(weg)) == 3
    assert not (set(weg) & set(behalten)), "Dublette und Kanon duerfen nicht kollidieren"
    assert not (set(weg) & set(ids)), "eine verifizierte Zeile darf nicht geloescht werden"
    for _w, _k, _bid, begr in DUBLETTEN:
        assert len(begr or "") >= 120, "Dubletten-Begruendung zu duenn"
