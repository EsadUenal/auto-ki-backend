from __future__ import annotations

"""
Kuratiertes Ergebnis von BATCH B1 — die primaerquellenbestaetigte Teilmenge der
Risikoklasse B. Erzeugt am 2026-08-27 aus dem amtlichen Gesamtexport der
KBA-Rueckrufdatenbank.

DER WEG VON 315 AUF 58
--------------------------
    Klasse B (offene Zielgeneration)            315 Zeilen / 239 Rueckrufe
    Fachquellen-Audit GENERATION_CONFIRMED      249 Zeilen / 207 Rueckrufe
    Primaerquellen-Pruefung SOURCE_CONFIRMED    100 Zeilen /  97 Rueckrufe
    nach den fuenf Batch-A-Toren                 58 Zeilen /  58 Rueckrufe

42 Zeilen sind an den Toren gescheitert; die Gruende stehen einzeln in
`AUSSCHLUESSE`. Zwei Muster dominieren: eine zweite plausible Generation
desselben Modelltokens (A1) und eine amtliche Eingrenzung, die VIRA nicht
abbilden kann (A2) — von "Rechtslenker" ueber "Plug-in-Hybrid" bis zu einem
Herstellungszeitraum von zwei Wochen.

BEWUSST KONSERVATIV: Tor A1 wirkt je RUECKRUF, nicht je Ziel. Nennt ein
amtlicher Rueckruf mehrere Modelle und ist auch nur eines davon
generationsmehrdeutig, faellt der ganze Rueckruf — samt seiner eindeutigen
Ziele. Genau so verhaelt sich Batch A; die Regel wird hier nicht aufgeweicht.

DIE GENERATIONSGRENZE IST HIER DER ZUSATZ GEGENUEBER BATCH A
-------------------------------------------------------------
Jede Zeile zielt auf eine Baureihe mit offenem `bauzeitraum_bis`. Dass das
amtliche Produktionsfenster trotzdem eindeutig in DIESE Generation faellt, ist
je Baureihe durch eine Herstellerquelle belegt (Quellenstufe 1, siehe
`app/kba_generation_quellen.py`). Der Beleg steht in jeder Zeile als
`generationsbeleg` und wandert in die Verifikationsnotiz — er ERSETZT die
KBA-Quelle des Rueckruf-Fakts nicht, sondern tritt daneben.

QUELLE UND LIZENZ
-----------------
Kraftfahrt-Bundesamt, Rueckrufdatenbank (Fahrzeuge), abgerufen 2026-08-27.
Datenlizenz Deutschland - Namensnennung - Version 2.0 (dl-de/by-2-0).
"""

GEPRUEFT_AM = "2026-08-27"

ZEILEN = (
    {'id': 3001,
     'baureihe_id': 'mercedes-benz-cls-c257',
     'datum': '2020-08-19',
     'betroffene_baujahre': '2018-2020',
     'mangel': 'Es besteht die Möglichkeit, dass die Verschraubung der Ölzu- und '
               'Ölrücklaufleitung des Abgasturboladers nicht korrekt ausgeführt wurde. In '
               'der Folge besteht bei Ölaustritt in Kontakt mit erwärmten Bauteilen '
               'erhöhte Brandgefahr.',
     'abhilfe': 'Die Verschraubungen der Ölzu- und Ölrücklaufleitung des Abgasturboladers '
                'werden überprüft und ggf. korrigiert.',
     'kba_referenz': '10174',
     'herstellercode': '0993103',
     'amtlicher_zeitraum': '2018-2020',
     'amtliches_datum': '2020-08-19',
     'amtliche_modelle': 'C-KLASSE, CLS, S-KLASSE, GLC, E-KLASSE',
     'generationsquelle': 'https://group.mercedes-benz.com/unternehmen/standorte/produktionsnetzwerk-sindelfingen.html',
     'generationsstufe': 1,
     'generationsbeleg': 'Werk Sindelfingen fuehrt den CLS in seiner Modellliste. Die '
                         'Fachquelle nennt das Produktionsende August 2023; beide Angaben '
                         'schliessen ein Fenster bis 2023 ein.'},
    {'id': 3002,
     'baureihe_id': 'bmw-x5-g05',
     'datum': '2020-09-07',
     'betroffene_baujahre': '2020',
     'mangel': 'Aufgrund nicht nach Vorgabe hergestellter Reifen kann es zum plötzlichen '
               'Luftverlust kommen.',
     'abhilfe': 'Die Reifen werden geprüft und ggf. ausgetauscht',
     'kba_referenz': '10189',
     'herstellercode': '0036170200',
     'amtlicher_zeitraum': '2020-2020',
     'amtliches_datum': '2020-09-07',
     'amtliche_modelle': 'X5, X6',
     'generationsquelle': 'https://www.bmwgroup.com/de/news/allgemein/2026/der-neue-bmw-x5.html',
     'generationsstufe': 1,
     'generationsbeleg': 'Die Fertigung des neuen BMW X5 beginnt im August 2026 im Werk '
                         'Spartanburg; Markteinfuehrung Ende November 2026.'},
    {'id': 3003,
     'baureihe_id': 'opel-grandland-a',
     'datum': '2020-12-02',
     'betroffene_baujahre': '2020',
     'mangel': 'Die betroffenen Fahrzeuge können möglicherweise von einem Riss der '
               'Hinterachsschenkel betroffen sein. Dies könnte zu einer Verschlechterung '
               'der Fahrstabilität und im schlimmsten Fall zu einem Kontrollverlust über '
               'das Fahrzeug führen.',
     'abhilfe': 'Die Hinterachsschenkel werden überprüft und ggf. ersetzt.',
     'kba_referenz': '10306',
     'herstellercode': 'E202007311 (20-C-156)',
     'amtlicher_zeitraum': '2020-2020',
     'amtliches_datum': '2020-12-02',
     'amtliche_modelle': 'GRANDLAND',
     'generationsquelle': 'https://www.media.stellantis.com/de-de/opel/press/produktionsstart-fuer-den-neuen-opel-grandland-in-eisenach',
     'generationsstufe': 1,
     'generationsbeleg': 'Produktionsstart des voellig neu entwickelten Opel Grandland in '
                         'Eisenach; Weltpremiere am Standort im Fruehjahr 2024.'},
    {'id': 3004,
     'baureihe_id': 'opel-grandland-a',
     'datum': '2020-12-04',
     'betroffene_baujahre': '2020',
     'mangel': 'Aufgrund einer unzureichenden Verschraubung an der hinteren Radnabe kann '
               'es zu einem Kontrollverlust über das Fahrzeug kommen.',
     'abhilfe': 'Die Radnabe wird mit dem richtigen Drehmoment wieder festgezogen - falls '
                'erforderlich, ersetzt.',
     'kba_referenz': '10339',
     'herstellercode': 'E202007970 (20-C-159)',
     'amtlicher_zeitraum': '2020-2020',
     'amtliches_datum': '2020-12-04',
     'amtliche_modelle': 'GRANDLAND',
     'generationsquelle': 'https://www.media.stellantis.com/de-de/opel/press/produktionsstart-fuer-den-neuen-opel-grandland-in-eisenach',
     'generationsstufe': 1,
     'generationsbeleg': 'Produktionsstart des voellig neu entwickelten Opel Grandland in '
                         'Eisenach; Weltpremiere am Standort im Fruehjahr 2024.'},
    {'id': 3005,
     'baureihe_id': 'mercedes-benz-a-klasse-w177',
     'datum': '2020-11-06',
     'betroffene_baujahre': '2018-2020',
     'mangel': 'Die Ölmenge des automatisierten Doppelkupplungsgetriebes entspricht nicht '
               'der Spezifikation (zu hoch). In der Folge könnte es zu einem '
               'Vortriebsverlust, erhöhten Schadstoffemissionen, Austritt von Öl in den '
               'Verkehrsraum sowie einer Brandgefahr kommen.',
     'abhilfe': 'Das Doppelkupplungsgetriebe wird überprüft und ggf. wird die Ölmenge '
                'korrigiert.',
     'kba_referenz': '10348',
     'herstellercode': '2790760',
     'amtlicher_zeitraum': '2018-2020',
     'amtliches_datum': '2020-11-06',
     'amtliche_modelle': 'A-KLASSE, GLB',
     'generationsquelle': 'https://group.mercedes-benz.com/company/locations/production-network-rastatt.html',
     'generationsstufe': 1,
     'generationsbeleg': "Werk Rastatt: 'The A-Class, the GLA, the all-electric EQA, and "
                         "the new CLA are built in Rastatt.'"},
    {'id': 3006,
     'baureihe_id': 'bmw-x5-g05',
     'datum': '2021-01-19',
     'betroffene_baujahre': '2020',
     'mangel': 'Aufgrund nicht nach Vorgabe hergestellter Reifen kann es zum plötzlichen '
               'Luftverlust kommen.',
     'abhilfe': 'Die Reifen werden geprüft und ggf. ausgetauscht.',
     'kba_referenz': '10358',
     'herstellercode': '0036180200',
     'amtlicher_zeitraum': '2020-2020',
     'amtliches_datum': '2021-01-19',
     'amtliche_modelle': 'X5, X6',
     'generationsquelle': 'https://www.bmwgroup.com/de/news/allgemein/2026/der-neue-bmw-x5.html',
     'generationsstufe': 1,
     'generationsbeleg': 'Die Fertigung des neuen BMW X5 beginnt im August 2026 im Werk '
                         'Spartanburg; Markteinfuehrung Ende November 2026.'},
    {'id': 3007,
     'baureihe_id': 'mercedes-benz-gla-h247',
     'datum': '2021-02-18',
     'betroffene_baujahre': '2020',
     'mangel': 'Aufgrund einer fehlerhaften Masseanbindung kann es zu einer unmotivierten '
               'Auslösung des Beifahrerairbags kommen.',
     'abhilfe': 'Die Masseanbindung des Beifahrerairbags wird überprüft und ggf. '
                'korrigiert.',
     'kba_referenz': '10595',
     'herstellercode': '9193003',
     'amtlicher_zeitraum': '2020-2020',
     'amtliches_datum': '2021-02-18',
     'amtliche_modelle': 'GLA',
     'generationsquelle': 'https://group.mercedes-benz.com/company/locations/production-network-rastatt.html',
     'generationsstufe': 1,
     'generationsbeleg': 'Werk Rastatt fuehrt den GLA in der Modellliste des Standorts.'},
    {'id': 3008,
     'baureihe_id': 'opel-corsa-f',
     'datum': '2021-03-30',
     'betroffene_baujahre': '2019-2021',
     'mangel': 'Aufgrund einer fehlerhaften Masseanbindung kann es zu einer unmotivierten '
               'Auslösung der Seitenairbags kommen.',
     'abhilfe': 'Die Massepunkte wird überprüft und ggf. abgeschmirgelt und gereinigt.',
     'kba_referenz': '10606',
     'herstellercode': 'E202008520 (20-C-192)',
     'amtlicher_zeitraum': '2019-2021',
     'amtliches_datum': '2021-03-30',
     'amtliche_modelle': 'CORSA',
     'generationsquelle': 'https://www.media.stellantis.com/em-en/opel/press/press-kit-opel-at-brussels-motor-show-2026',
     'generationsstufe': 1,
     'generationsbeleg': 'Der aktuelle Corsa ist seit mehr als fuenf Jahren Deutschlands '
                         'meistverkaufter Kleinwagen und fuehrt das 2026 fort; die '
                         'naechste Corsa-Generation zaehlt zu den bis 2030 angekuendigten '
                         'Modellen.'},
    {'id': 3009,
     'baureihe_id': 'opel-corsa-f',
     'datum': '2021-04-12',
     'betroffene_baujahre': '2020',
     'mangel': 'Angaben der Einpresstiefen bei den 16 Zoll-  und 17 Zoll-Felgen in der '
               'EG-Übereinstimmungsbescheinigung (CoC-Dokument) sind nicht korrekt.',
     'abhilfe': 'Ein neues CoC-Dokument mit den korrekten Einpresstiefen wird '
                'ausgehändigt.',
     'kba_referenz': '10633',
     'herstellercode': 'E202006770 (21-C-021)',
     'amtlicher_zeitraum': '2020-2020',
     'amtliches_datum': '2021-04-12',
     'amtliche_modelle': 'CORSA',
     'generationsquelle': 'https://www.media.stellantis.com/em-en/opel/press/press-kit-opel-at-brussels-motor-show-2026',
     'generationsstufe': 1,
     'generationsbeleg': 'Der aktuelle Corsa ist seit mehr als fuenf Jahren Deutschlands '
                         'meistverkaufter Kleinwagen und fuehrt das 2026 fort; die '
                         'naechste Corsa-Generation zaehlt zu den bis 2030 angekuendigten '
                         'Modellen.'},
    {'id': 3010,
     'baureihe_id': 'audi-q3-ii',
     'datum': '2021-04-26',
     'betroffene_baujahre': '2021',
     'mangel': 'Das Rückhaltesystem könnte bei einem starken Bremsvorgang oder einem '
               'seitlichen Kippen des Fahrzeugs nur eingeschränkt funktionieren, wodurch '
               'erhöhte Verletzungsgefahr besteht.',
     'abhilfe': 'Überprüfung des Gurtsystems und bei Bedarf Austausch des '
                'Sicherheitsgurtes.',
     'kba_referenz': '10721',
     'herstellercode': '69CA',
     'amtlicher_zeitraum': '2021-2021',
     'amtliches_datum': '2021-04-26',
     'amtliche_modelle': 'RS Q3, Q3',
     'generationsquelle': 'https://www.audi.com/en/press-releases/versatile-sporty-and-digitally-connected-the-new-audi-q3-16771',
     'generationsstufe': 1,
     'generationsbeleg': 'Nach der Weltpremiere im Juni begann die Serienproduktion der '
                         'dritten Generation in Gyoer; Markteinfuehrung im Oktober.'},
    {'id': 3011,
     'baureihe_id': 'volkswagen-t-roc-a1',
     'datum': '2021-04-12',
     'betroffene_baujahre': '2021',
     'mangel': 'Das Rückhaltesystem könnte bei einem starken Bremsvorgang oder einem '
               'seitlichen Kippen des Fahrzeugs nur eingeschränkt funktionieren, wodurch '
               'erhöhte Verletzungsgefahr besteht.',
     'abhilfe': 'Der Sicherheitsgurt für den rechten Vordersitz wird ersetzt.',
     'kba_referenz': '10722',
     'herstellercode': '69BX',
     'amtlicher_zeitraum': '2021-2021',
     'amtliches_datum': '2021-04-12',
     'amtliche_modelle': 'T-ROC',
     'generationsquelle': 'https://www.volkswagen-newsroom.com/en/press-releases/world-premiere-of-the-t-roc-new-generation-of-the-best-seller-is-high-quality-and-innovative-19769',
     'generationsstufe': 1,
     'generationsbeleg': 'Weltpremiere der zweiten T-Roc-Generation; Vorverkauf in '
                         'Deutschland ab 28. August, Markteinfuehrung im November.'},
    {'id': 3012,
     'baureihe_id': 'mercedes-benz-v-klasse-w447',
     'datum': '2021-07-12',
     'betroffene_baujahre': '2019',
     'mangel': 'Aufgrund einer fehlerhaften Schweißnaht kann es zu einem Bruch des '
               'Querlenkers kommen.',
     'abhilfe': 'Austausch des linken Querlenkers.',
     'kba_referenz': '10886',
     'herstellercode': 'VS2ALLENK (3391065)',
     'amtlicher_zeitraum': '2019-2019',
     'amtliches_datum': '2021-07-12',
     'amtliche_modelle': 'V-KLASSE, VITO',
     'generationsquelle': 'https://group.mercedes-benz.com/company/production/production-vle-vitoria.html',
     'generationsstufe': 1,
     'generationsbeleg': 'Serienproduktionsstart des VLE im Werk Vitoria am 12.06.2026; '
                         'Vitoria fertigt ab 2026 VLE, V-Klasse, Vito und eVito flexibel '
                         'auf derselben Linie — die V-Klasse laeuft also parallel weiter.'},
    {'id': 3013,
     'baureihe_id': 'opel-grandland-a',
     'datum': '2021-06-18',
     'betroffene_baujahre': '2020',
     'mangel': 'Unzureichende Befestigung der Radaufhängung kann zum Verlust des hinteren '
               'Rades führen.',
     'abhilfe': 'Das Anzugsdrehmoment der Schrauben wird geprüft und bei Bedarf werden die '
                'Schrauben und Radnaben ersetzt.',
     'kba_referenz': '10898',
     'herstellercode': 'E212103690 (21-C-106)',
     'amtlicher_zeitraum': '2020-2020',
     'amtliches_datum': '2021-06-18',
     'amtliche_modelle': 'GRANDLAND',
     'generationsquelle': 'https://www.media.stellantis.com/de-de/opel/press/produktionsstart-fuer-den-neuen-opel-grandland-in-eisenach',
     'generationsstufe': 1,
     'generationsbeleg': 'Produktionsstart des voellig neu entwickelten Opel Grandland in '
                         'Eisenach; Weltpremiere am Standort im Fruehjahr 2024.'},
    {'id': 3014,
     'baureihe_id': 'mercedes-benz-s-klasse-w223',
     'datum': '2021-07-30',
     'betroffene_baujahre': '2021',
     'mangel': 'Es kann zum Austritt von Kraftstoff kommen, welcher in den Verkehrsraum '
               'gelangen könnte.',
     'abhilfe': 'Kraftstoffbehälter erneuern',
     'kba_referenz': '11037',
     'herstellercode': '4790014',
     'amtlicher_zeitraum': '2021-2021',
     'amtliches_datum': '2021-07-30',
     'amtliche_modelle': 'S-KLASSE',
     'generationsquelle': 'https://group.mercedes-benz.com/company/production/news/',
     'generationsstufe': 1,
     'generationsbeleg': 'Am 30.04.2026 den Hochlauf von S-Klasse, Maybach S-Klasse und '
                         'EQS im Werk Sindelfingen gefeiert — dieselbe Generation, '
                         'Modellpflege statt Generationswechsel.'},
    {'id': 3015,
     'baureihe_id': 'volkswagen-tiguan-ii',
     'datum': '2022-02-23',
     'betroffene_baujahre': '2020',
     'mangel': 'Verbau von V-Reifen (240km/h) anstelle der freigegebenen Y-Reifen '
               '(300km/h).',
     'abhilfe': 'Austausch der Reifen',
     'kba_referenz': '11058',
     'herstellercode': '44R6',
     'amtlicher_zeitraum': '2020-2020',
     'amtliches_datum': '2022-02-23',
     'amtliche_modelle': 'TIGUAN',
     'generationsquelle': 'https://www.volkswagen-newsroom.com/en/press-releases/new-tiguan-generation-volkswagens-bestseller-celebrates-world-premiere-in-front-of-10000-employees-17655',
     'generationsstufe': 1,
     'generationsbeleg': 'Medieninformation Nr. 140/2023: die dritte Generation laeuft ab '
                         'Herbst 2023 in Wolfsburg vom Band und kommt im ersten Quartal '
                         '2024 auf den Markt.'},
    {'id': 3016,
     'baureihe_id': 'mercedes-benz-v-klasse-w447',
     'datum': '2021-09-28',
     'betroffene_baujahre': '2021',
     'mangel': 'Aufgrund eines Produktionsfehlers kann es zu einer eingeschränkten '
               'Funktion der Seitenairbags kommen.',
     'abhilfe': 'Das Airbagsteuergerät wird erneuert.',
     'kba_referenz': '11080',
     'herstellercode': 'VS2AIRHIGH (5494184)',
     'amtlicher_zeitraum': '2021-2021',
     'amtliches_datum': '2021-09-28',
     'amtliche_modelle': 'V-KLASSE, VITO, EQV',
     'generationsquelle': 'https://group.mercedes-benz.com/company/production/production-vle-vitoria.html',
     'generationsstufe': 1,
     'generationsbeleg': 'Serienproduktionsstart des VLE im Werk Vitoria am 12.06.2026; '
                         'Vitoria fertigt ab 2026 VLE, V-Klasse, Vito und eVito flexibel '
                         'auf derselben Linie — die V-Klasse laeuft also parallel weiter.'},
    {'id': 3017,
     'baureihe_id': 'mercedes-benz-a-klasse-w177',
     'datum': '2021-10-20',
     'betroffene_baujahre': '2019',
     'mangel': 'Aufgrund unzureichender Abdichtung des Hochvoltbatteriegehäuses kann es '
               'bei Korrosion zu einem Feuchtigkeitseintritt kommen, wodurch ein '
               'Wiederstart des Fahrzeuges nicht möglich wäre. Darüber hinaus besteht '
               'erhöhte Brandgefahr.',
     'abhilfe': 'Die Hochvoltbatterie wird überprüft und ggf. nachgearbeitet oder ersetzt.',
     'kba_referenz': '11271',
     'herstellercode': '5490203',
     'amtlicher_zeitraum': '2019-2019',
     'amtliches_datum': '2021-10-20',
     'amtliche_modelle': 'A-KLASSE, B-KLASSE',
     'generationsquelle': 'https://group.mercedes-benz.com/company/locations/production-network-rastatt.html',
     'generationsstufe': 1,
     'generationsbeleg': "Werk Rastatt: 'The A-Class, the GLA, the all-electric EQA, and "
                         "the new CLA are built in Rastatt.'"},
    {'id': 3018,
     'baureihe_id': 'mercedes-benz-v-klasse-w447',
     'datum': '2022-05-30',
     'betroffene_baujahre': '2020-2021',
     'mangel': 'Mangelhafte Abdichtung kann zum Austritt von Kühlmittel in den '
               'Unterdruckkreislauf führen. In der Folge können die Abgasemissionen '
               'ansteigen und es besteht Brandgefahr wegen Überhitzung (Fahrzeuge mit '
               'Failboost).',
     'abhilfe': 'An den betroffenen Fahrzeugen wird ein Softwareupdate durchgeführt sowie '
                'das elektrische Umschaltventil getauscht.',
     'kba_referenz': '11473',
     'herstellercode': 'VS2KU20MPE (2090009)',
     'amtlicher_zeitraum': '2020-2021',
     'amtliches_datum': '2022-05-30',
     'amtliche_modelle': 'V-KLASSE, VITO',
     'generationsquelle': 'https://group.mercedes-benz.com/company/production/production-vle-vitoria.html',
     'generationsstufe': 1,
     'generationsbeleg': 'Serienproduktionsstart des VLE im Werk Vitoria am 12.06.2026; '
                         'Vitoria fertigt ab 2026 VLE, V-Klasse, Vito und eVito flexibel '
                         'auf derselben Linie — die V-Klasse laeuft also parallel weiter.'},
    {'id': 3019,
     'baureihe_id': 'opel-corsa-f',
     'datum': '2022-02-25',
     'betroffene_baujahre': '2021',
     'mangel': 'Aufgrund fehlerhafter Radschrauben kann es zum Radverlust kommen.',
     'abhilfe': 'Die Radschrauben werden überprüft und gegebenenfalls ersetzt.',
     'kba_referenz': '11561',
     'herstellercode': 'E222200420 (22-C-012) O6V',
     'amtlicher_zeitraum': '2021-2021',
     'amtliches_datum': '2022-02-25',
     'amtliche_modelle': 'CORSA',
     'generationsquelle': 'https://www.media.stellantis.com/em-en/opel/press/press-kit-opel-at-brussels-motor-show-2026',
     'generationsstufe': 1,
     'generationsbeleg': 'Der aktuelle Corsa ist seit mehr als fuenf Jahren Deutschlands '
                         'meistverkaufter Kleinwagen und fuehrt das 2026 fort; die '
                         'naechste Corsa-Generation zaehlt zu den bis 2030 angekuendigten '
                         'Modellen.'},
    {'id': 3020,
     'baureihe_id': 'mercedes-benz-gla-h247',
     'datum': '2022-03-11',
     'betroffene_baujahre': '2020',
     'mangel': 'Fehlerhafte Verlegung des Leitungssatzes des linken Fondseitenairbags kann '
               'bei einem Unfall zur verzögerten oder gar keiner Auslösung des Airbags '
               'führen.',
     'abhilfe': 'Der Leitungssatz des linken Fondseitenairbags wird überprüft und ggf. '
                'nachgearbeitet.',
     'kba_referenz': '11609',
     'herstellercode': '9192109',
     'amtlicher_zeitraum': '2020-2020',
     'amtliches_datum': '2022-03-11',
     'amtliche_modelle': 'GLA, B-KLASSE',
     'generationsquelle': 'https://group.mercedes-benz.com/company/locations/production-network-rastatt.html',
     'generationsstufe': 1,
     'generationsbeleg': 'Werk Rastatt fuehrt den GLA in der Modellliste des Standorts.'},
    {'id': 3021,
     'baureihe_id': 'mercedes-benz-v-klasse-w447',
     'datum': '2022-04-06',
     'betroffene_baujahre': '2019-2021',
     'mangel': 'Mangelhafte Abdichtung kann zum Austritt von Kühlmittel in den '
               'Unterdruckkreislauf führen. In der Folge können die Abgasemissionen '
               'ansteigen und es besteht Brandgefahr wegen Überhitzung sowie reduzierte '
               'Bremsleistung (Fahrzeuge ohne Failboost)',
     'abhilfe': 'Bei den Fahrzeugen wird ein Softwareupdate durchgeführt sowie das '
                'elektrische Umschaltventil getauscht.',
     'kba_referenz': '11684',
     'herstellercode': 'VS2KUMIPU (2090011)',
     'amtlicher_zeitraum': '2019-2021',
     'amtliches_datum': '2022-04-06',
     'amtliche_modelle': 'V-KLASSE, VITO',
     'generationsquelle': 'https://group.mercedes-benz.com/company/production/production-vle-vitoria.html',
     'generationsstufe': 1,
     'generationsbeleg': 'Serienproduktionsstart des VLE im Werk Vitoria am 12.06.2026; '
                         'Vitoria fertigt ab 2026 VLE, V-Klasse, Vito und eVito flexibel '
                         'auf derselben Linie — die V-Klasse laeuft also parallel weiter.'},
    {'id': 3022,
     'baureihe_id': 'volkswagen-tiguan-ii',
     'datum': '2022-05-27',
     'betroffene_baujahre': '2021',
     'mangel': 'Oxideinschlüsse in den Radlagergehäusen können zum Bruch und instabilem '
               'Fahrverhalten führen.',
     'abhilfe': 'An den betroffenen Fahrzeugen wird das Fertigungsdatum der beiden '
                'hinteren Radlagergehäuse geprüftund diese gegebenenfalls ersetzt.',
     'kba_referenz': '11735',
     'herstellercode': '42L7',
     'amtlicher_zeitraum': '2021-2021',
     'amtliches_datum': '2022-05-27',
     'amtliche_modelle': 'TIGUAN',
     'generationsquelle': 'https://www.volkswagen-newsroom.com/en/press-releases/new-tiguan-generation-volkswagens-bestseller-celebrates-world-premiere-in-front-of-10000-employees-17655',
     'generationsstufe': 1,
     'generationsbeleg': 'Medieninformation Nr. 140/2023: die dritte Generation laeuft ab '
                         'Herbst 2023 in Wolfsburg vom Band und kommt im ersten Quartal '
                         '2024 auf den Markt.'},
    {'id': 3023,
     'baureihe_id': 'mercedes-benz-s-klasse-w223',
     'datum': '2023-06-22',
     'betroffene_baujahre': '2021-2023',
     'mangel': 'Nicht der Spezifikation entsprechende Überwachungssoftware des ESP kann zu '
               'eingeschränkter Funktion der Fahrdynamik-Regelsysteme führen. Zudem würde '
               'die Geschwindigkeit dauerhaft mit 0 km/h angezeigt werden.',
     'abhilfe': 'Aktualisierung der Software des ESP-Steuergerätes.',
     'kba_referenz': '12819',
     'herstellercode': '4290303',
     'amtlicher_zeitraum': '2021-2023',
     'amtliches_datum': '2023-06-22',
     'amtliche_modelle': 'EQS, S-KLASSE',
     'generationsquelle': 'https://group.mercedes-benz.com/company/production/news/',
     'generationsstufe': 1,
     'generationsbeleg': 'Am 30.04.2026 den Hochlauf von S-Klasse, Maybach S-Klasse und '
                         'EQS im Werk Sindelfingen gefeiert — dieselbe Generation, '
                         'Modellpflege statt Generationswechsel.'},
    {'id': 3024,
     'baureihe_id': 'mercedes-benz-c-klasse-w206',
     'datum': '2023-07-11',
     'betroffene_baujahre': '2023',
     'mangel': 'Verunreinigung der Bremsflüssigkeit kann zu Beeinträchtigungen des '
               'Bremsverhaltens sowie Ausfall des ESP führen.',
     'abhilfe': 'Die relevanten Komponenten der Bremsanlage werden ersetzt.',
     'kba_referenz': '12869',
     'herstellercode': '4290011',
     'amtlicher_zeitraum': '2023-2023',
     'amtliches_datum': '2023-07-11',
     'amtliche_modelle': 'C-KLASSE',
     'generationsquelle': 'https://group.mercedes-benz.com/unternehmen/produktion/produktionsnetzwerk/produktionsnetzwerk-bremen.html',
     'generationsstufe': 1,
     'generationsbeleg': "Werk Bremen: 'In Bremen laufen die C-Klasse mit Limousine, "
                         "T-Modell, Coupe und Cabriolet ... vom Band'; die elektrische "
                         'C-Klasse laeuft ab Q2/2026 separat in Kecskemet an.'},
    {'id': 3025,
     'baureihe_id': 'mercedes-benz-v-klasse-w447',
     'datum': '2023-07-04',
     'betroffene_baujahre': '2021-2022',
     'mangel': 'Nicht der Spezifikation entsprechender Impeller des Kraftstofffördermoduls '
               'kann sich verformen und am Gehäuse anliegen, sodass durch den Widerstand '
               'die Kraftstoffförderung abgestellt wird. In der Folge verliert das '
               'Fahrzeug seinen Antrieb.',
     'abhilfe': 'Das Kraftstofffördermodul wird ersetzt.',
     'kba_referenz': '12935',
     'herstellercode': '4790206, 4790207',
     'amtlicher_zeitraum': '2021-2022',
     'amtliches_datum': '2023-07-04',
     'amtliche_modelle': 'METRIS, SPRINTER, V-KLASSE, VITO',
     'generationsquelle': 'https://group.mercedes-benz.com/company/production/production-vle-vitoria.html',
     'generationsstufe': 1,
     'generationsbeleg': 'Serienproduktionsstart des VLE im Werk Vitoria am 12.06.2026; '
                         'Vitoria fertigt ab 2026 VLE, V-Klasse, Vito und eVito flexibel '
                         'auf derselben Linie — die V-Klasse laeuft also parallel weiter.'},
    {'id': 3026,
     'baureihe_id': 'mercedes-benz-c-klasse-w206',
     'datum': '2023-07-24',
     'betroffene_baujahre': '2021-2023',
     'mangel': 'Antriebsausfall/Brandgefahr',
     'abhilfe': 'Es werden die Masseverschraubungen überprüft und ggf. Reparaturen '
                'vorgenommen.',
     'kba_referenz': '12955',
     'herstellercode': '5491110, 5491109, 5491108',
     'amtlicher_zeitraum': '2021-2023',
     'amtliches_datum': '2023-07-24',
     'amtliche_modelle': 'C-KLASSE, C-KLASSE AMG',
     'generationsquelle': 'https://group.mercedes-benz.com/unternehmen/produktion/produktionsnetzwerk/produktionsnetzwerk-bremen.html',
     'generationsstufe': 1,
     'generationsbeleg': "Werk Bremen: 'In Bremen laufen die C-Klasse mit Limousine, "
                         "T-Modell, Coupe und Cabriolet ... vom Band'; die elektrische "
                         'C-Klasse laeuft ab Q2/2026 separat in Kecskemet an.'},
    {'id': 3027,
     'baureihe_id': 'volkswagen-t-roc-a1',
     'datum': '2023-07-11',
     'betroffene_baujahre': '2023',
     'mangel': 'Lunker im hinteren linken Radlagergehäuse kann zum Bruch führen, die '
               'Fahrstabilität könnte eingeschränkt sein.',
     'abhilfe': 'Prüfung des DMC Code am hinteren linken Radlagergehäuse und dieses '
                'gegebenenfalls ersetzen.',
     'kba_referenz': '12979',
     'herstellercode': '42M9',
     'amtlicher_zeitraum': '2023-2023',
     'amtliches_datum': '2023-07-11',
     'amtliche_modelle': 'T-ROC, TOURAN',
     'generationsquelle': 'https://www.volkswagen-newsroom.com/en/press-releases/world-premiere-of-the-t-roc-new-generation-of-the-best-seller-is-high-quality-and-innovative-19769',
     'generationsstufe': 1,
     'generationsbeleg': 'Weltpremiere der zweiten T-Roc-Generation; Vorverkauf in '
                         'Deutschland ab 28. August, Markteinfuehrung im November.'},
    {'id': 3028,
     'baureihe_id': 'bmw-x5-g05',
     'datum': '2023-08-03',
     'betroffene_baujahre': '2023',
     'mangel': 'Fehlerhaft eingesetzter Gasgenerator kann im Falle einer unfallbedingten '
               'Auslösung zu einer verminderten Schutzwirkung des Knieairbags führen, '
               'wodurch sich die Verletzungsgefahr erhöht.',
     'abhilfe': 'An betroffenen Fahrzeugen wird der Knieairbag der Fahrer- und/oder '
                'Beifahrerseite ersetzt.',
     'kba_referenz': '12984',
     'herstellercode': '0072510200',
     'amtlicher_zeitraum': '2023-2023',
     'amtliches_datum': '2023-08-03',
     'amtliche_modelle': 'X7, X5, XM, X5M, X6M, X6',
     'generationsquelle': 'https://www.bmwgroup.com/de/news/allgemein/2026/der-neue-bmw-x5.html',
     'generationsstufe': 1,
     'generationsbeleg': 'Die Fertigung des neuen BMW X5 beginnt im August 2026 im Werk '
                         'Spartanburg; Markteinfuehrung Ende November 2026.'},
    {'id': 3029,
     'baureihe_id': 'mercedes-benz-c-klasse-w206',
     'datum': '2023-11-29',
     'betroffene_baujahre': '2023',
     'mangel': 'Hinterer, linker Bremsschlauch kann durch Torsion oder Scheuern beschädigt '
               'werden, wodurch es zu einem Verlust von Bremsflüssigkeit und einer '
               'Beeinträchtigung der Bremsleistung kommen kann.',
     'abhilfe': 'Die Verlegung des Bremsschlauchs wird überprüft und ggf. wird der '
                'Bremsschlauch ersetzt.',
     'kba_referenz': '13276',
     'herstellercode': '4290101',
     'amtlicher_zeitraum': '2023-2023',
     'amtliches_datum': '2023-11-29',
     'amtliche_modelle': 'C-KLASSE',
     'generationsquelle': 'https://group.mercedes-benz.com/unternehmen/produktion/produktionsnetzwerk/produktionsnetzwerk-bremen.html',
     'generationsstufe': 1,
     'generationsbeleg': "Werk Bremen: 'In Bremen laufen die C-Klasse mit Limousine, "
                         "T-Modell, Coupe und Cabriolet ... vom Band'; die elektrische "
                         'C-Klasse laeuft ab Q2/2026 separat in Kecskemet an.'},
    {'id': 3030,
     'baureihe_id': 'mercedes-benz-s-klasse-w223',
     'datum': '2023-12-08',
     'betroffene_baujahre': '2022-2023',
     'mangel': 'Fehlerhafte Bauteile in der ESP-Einheit kann zu Fehlfunktionen und zum '
               'Eindringen von Feuchtigkeit führen. Dadurch können die ESP-Funktionen '
               '(Fahrdynamikregelung, ABS) ausfallen.',
     'abhilfe': 'Die ESP-Einheit wird ersetzt.',
     'kba_referenz': '13340',
     'herstellercode': '4296006',
     'amtlicher_zeitraum': '2022-2023',
     'amtliches_datum': '2023-12-08',
     'amtliche_modelle': 'S-KLASSE, GLC',
     'generationsquelle': 'https://group.mercedes-benz.com/company/production/news/',
     'generationsstufe': 1,
     'generationsbeleg': 'Am 30.04.2026 den Hochlauf von S-Klasse, Maybach S-Klasse und '
                         'EQS im Werk Sindelfingen gefeiert — dieselbe Generation, '
                         'Modellpflege statt Generationswechsel.'},
    {'id': 3031,
     'baureihe_id': 'volkswagen-tiguan-ii',
     'datum': '2023-12-15',
     'betroffene_baujahre': '2018-2019',
     'mangel': 'Bei Airbagauslösung kann das Gehäuse des Kopfairbags beschädigt werden. '
               'Dabei können sich Metallfragmente lösen, die die Insassen verletzen '
               'können.',
     'abhilfe': 'An den betroffenen Fahrzeugen wird der Kopfairbag ersetzt.',
     'kba_referenz': '13363',
     'herstellercode': '69FV',
     'amtlicher_zeitraum': '2018-2019',
     'amtliches_datum': '2023-12-15',
     'amtliche_modelle': 'TIGUAN',
     'generationsquelle': 'https://www.volkswagen-newsroom.com/en/press-releases/new-tiguan-generation-volkswagens-bestseller-celebrates-world-premiere-in-front-of-10000-employees-17655',
     'generationsstufe': 1,
     'generationsbeleg': 'Medieninformation Nr. 140/2023: die dritte Generation laeuft ab '
                         'Herbst 2023 in Wolfsburg vom Band und kommt im ersten Quartal '
                         '2024 auf den Markt.'},
    {'id': 3032,
     'baureihe_id': 'mercedes-benz-cls-c257',
     'datum': '2024-02-12',
     'betroffene_baujahre': '2021-2023',
     'mangel': 'Nicht der Spezifikation entsprechende Befestigung der 48V-Massestelle im '
               'Motorraum kann sich lösen und zu einer Brandgefahr führen.',
     'abhilfe': 'Die Verschraubung der Massestelle wird überprüft und ggf. repariert.',
     'kba_referenz': '13578',
     'herstellercode': '5491318',
     'amtlicher_zeitraum': '2021-2023',
     'amtliches_datum': '2024-02-12',
     'amtliche_modelle': 'CLS, E-KLASSE, AMG GT',
     'generationsquelle': 'https://group.mercedes-benz.com/unternehmen/standorte/produktionsnetzwerk-sindelfingen.html',
     'generationsstufe': 1,
     'generationsbeleg': 'Werk Sindelfingen fuehrt den CLS in seiner Modellliste. Die '
                         'Fachquelle nennt das Produktionsende August 2023; beide Angaben '
                         'schliessen ein Fenster bis 2023 ein.'},
    {'id': 3033,
     'baureihe_id': 'opel-corsa-f',
     'datum': '2024-09-17',
     'betroffene_baujahre': '2024',
     'mangel': 'Lenkungsausfall',
     'abhilfe': 'Austausch Lenkgetriebe',
     'kba_referenz': '14304R',
     'herstellercode': 'KMZ',
     'amtlicher_zeitraum': '2024-2024',
     'amtliches_datum': '2024-09-17',
     'amtliche_modelle': 'MOKKA, CORSA',
     'generationsquelle': 'https://www.media.stellantis.com/em-en/opel/press/press-kit-opel-at-brussels-motor-show-2026',
     'generationsstufe': 1,
     'generationsbeleg': 'Der aktuelle Corsa ist seit mehr als fuenf Jahren Deutschlands '
                         'meistverkaufter Kleinwagen und fuehrt das 2026 fort; die '
                         'naechste Corsa-Generation zaehlt zu den bis 2030 angekuendigten '
                         'Modellen.'},
    {'id': 3034,
     'baureihe_id': 'opel-corsa-f',
     'datum': '2025-06-16',
     'betroffene_baujahre': '2022-2024',
     'mangel': 'Brandgefahr',
     'abhilfe': 'Das Motoröl und der Ölfilter werden ausgetauscht.',
     'kba_referenz': '14857R',
     'herstellercode': 'KQ9',
     'amtlicher_zeitraum': '2022-2024',
     'amtliches_datum': '2025-06-16',
     'amtliche_modelle': 'CORSA, CROSSLAND X',
     'generationsquelle': 'https://www.media.stellantis.com/em-en/opel/press/press-kit-opel-at-brussels-motor-show-2026',
     'generationsstufe': 1,
     'generationsbeleg': 'Der aktuelle Corsa ist seit mehr als fuenf Jahren Deutschlands '
                         'meistverkaufter Kleinwagen und fuehrt das 2026 fort; die '
                         'naechste Corsa-Generation zaehlt zu den bis 2030 angekuendigten '
                         'Modellen.'},
    {'id': 3035,
     'baureihe_id': 'mercedes-benz-e-klasse-w214',
     'datum': '2025-07-17',
     'betroffene_baujahre': '2024',
     'mangel': 'Brandgefahr',
     'abhilfe': 'Die Masseverschraubungen werden überprüft, ggf. erneuert und mit dem '
                'vorgegebenem Drehmoment angezogen.',
     'kba_referenz': '15027R',
     'herstellercode': '5491321',
     'amtlicher_zeitraum': '2024-2024',
     'amtliches_datum': '2025-07-17',
     'amtliche_modelle': 'GLC, E-KLASSE',
     'generationsquelle': 'https://group.mercedes-benz.com/unternehmen/standorte/produktionsnetzwerk-sindelfingen.html',
     'generationsstufe': 1,
     'generationsbeleg': 'Werk Sindelfingen ist zustaendig fuer die Produktion der '
                         'E-Klasse als Limousine, T-Modell und All-Terrain.'},
    {'id': 3036,
     'baureihe_id': 'mercedes-benz-c-klasse-w206',
     'datum': '2025-04-30',
     'betroffene_baujahre': '2021-2024',
     'mangel': 'Verminderte Rückhaltewirkung Gurt',
     'abhilfe': 'Bei den betroffenen Fahrzeugen wird die Sicherungskappe der '
                'Gurtverankerung überprüft und ggf. nachgearbeitet.',
     'kba_referenz': '15054R',
     'herstellercode': '9192004',
     'amtlicher_zeitraum': '2021-2024',
     'amtliches_datum': '2025-04-30',
     'amtliche_modelle': 'C-KLASSE',
     'generationsquelle': 'https://group.mercedes-benz.com/unternehmen/produktion/produktionsnetzwerk/produktionsnetzwerk-bremen.html',
     'generationsstufe': 1,
     'generationsbeleg': "Werk Bremen: 'In Bremen laufen die C-Klasse mit Limousine, "
                         "T-Modell, Coupe und Cabriolet ... vom Band'; die elektrische "
                         'C-Klasse laeuft ab Q2/2026 separat in Kecskemet an.'},
    {'id': 3037,
     'baureihe_id': 'mercedes-benz-c-klasse-w206',
     'datum': '2025-08-20',
     'betroffene_baujahre': '2022-2025',
     'mangel': 'Lenkungsverlust',
     'abhilfe': 'Die Verschraubung der Lenkungskupplung wird nachgearbeitet.',
     'kba_referenz': '15473R',
     'herstellercode': '4691007',
     'amtlicher_zeitraum': '2022-2025',
     'amtliches_datum': '2025-08-20',
     'amtliche_modelle': 'C-KLASSE, EQE, GLC',
     'generationsquelle': 'https://group.mercedes-benz.com/unternehmen/produktion/produktionsnetzwerk/produktionsnetzwerk-bremen.html',
     'generationsstufe': 1,
     'generationsbeleg': "Werk Bremen: 'In Bremen laufen die C-Klasse mit Limousine, "
                         "T-Modell, Coupe und Cabriolet ... vom Band'; die elektrische "
                         'C-Klasse laeuft ab Q2/2026 separat in Kecskemet an.'},
    {'id': 3038,
     'baureihe_id': 'opel-corsa-f',
     'datum': '2026-01-19',
     'betroffene_baujahre': '2023-2025',
     'mangel': 'Fehlerhafte/Fehlende Warnungen Reifendruckkontrollsystem',
     'abhilfe': 'Die Codierung der Fahrdynamikregelung (ESC) wird aktualisiert.',
     'kba_referenz': '15756R',
     'herstellercode': 'KTL',
     'amtlicher_zeitraum': '2023-2025',
     'amtliches_datum': '2026-01-19',
     'amtliche_modelle': 'COMBO, CORSA, MOKKA',
     'generationsquelle': 'https://www.media.stellantis.com/em-en/opel/press/press-kit-opel-at-brussels-motor-show-2026',
     'generationsstufe': 1,
     'generationsbeleg': 'Der aktuelle Corsa ist seit mehr als fuenf Jahren Deutschlands '
                         'meistverkaufter Kleinwagen und fuehrt das 2026 fort; die '
                         'naechste Corsa-Generation zaehlt zu den bis 2030 angekuendigten '
                         'Modellen.'},
    {'id': 3039,
     'baureihe_id': 'opel-corsa-f',
     'datum': '2026-04-01',
     'betroffene_baujahre': '2023-2026',
     'mangel': 'Brandgefahr',
     'abhilfe': 'Austausch der Polschutzkappe des 48V-Riemen-Startergenerators  Darüber '
                'hinaus Überprüfung des Abstands zwischen dem Benzinpartikelfilterrohr und '
                'der Polschutzkappe des 48V-Riemenstartergenerators. Wenn der Abstand '
                'weniger als 10 mm beträgt, wird das Rohr neu positioniert. Wenn das Rohr '
                'beschädigt ist, wird es ausgetauscht.',
     'kba_referenz': '16283R',
     'herstellercode': 'KV7',
     'amtlicher_zeitraum': '2023-2026',
     'amtliches_datum': '2026-04-01',
     'amtliche_modelle': 'CORSA, FRONTERA, MOKKA',
     'generationsquelle': 'https://www.media.stellantis.com/em-en/opel/press/press-kit-opel-at-brussels-motor-show-2026',
     'generationsstufe': 1,
     'generationsbeleg': 'Der aktuelle Corsa ist seit mehr als fuenf Jahren Deutschlands '
                         'meistverkaufter Kleinwagen und fuehrt das 2026 fort; die '
                         'naechste Corsa-Generation zaehlt zu den bis 2030 angekuendigten '
                         'Modellen.'},
    {'id': 3040,
     'baureihe_id': 'mercedes-benz-v-klasse-w447',
     'datum': '2017-10-20',
     'betroffene_baujahre': '2015-2016',
     'mangel': 'Durch nicht ordnungsgemäß verschweißte Pins im Lenkungssteuergerät kann '
               'eine dauerhafte elektrische Verbindung nicht gewährleistet werden.',
     'abhilfe': 'Das Lenkgetriebe wird erneuert.',
     'kba_referenz': '7371',
     'herstellercode': 'VS2EPSLENK',
     'amtlicher_zeitraum': '2015-2016',
     'amtliches_datum': '2017-10-20',
     'amtliche_modelle': 'V-KLASSE, VITO',
     'generationsquelle': 'https://group.mercedes-benz.com/company/production/production-vle-vitoria.html',
     'generationsstufe': 1,
     'generationsbeleg': 'Serienproduktionsstart des VLE im Werk Vitoria am 12.06.2026; '
                         'Vitoria fertigt ab 2026 VLE, V-Klasse, Vito und eVito flexibel '
                         'auf derselben Linie — die V-Klasse laeuft also parallel weiter.'},
    {'id': 3041,
     'baureihe_id': 'mercedes-benz-v-klasse-w447',
     'datum': '2017-10-05',
     'betroffene_baujahre': '2017',
     'mangel': 'Nicht der Spezifikation entsprechende Bremsleitung kann zu verringerter '
               'Bremsleistung führen.',
     'abhilfe': 'Die Bremsleitung wird geprüft und ggf. erneuert.',
     'kba_referenz': '7372',
     'herstellercode': 'VS2ALLBRE (4290296)',
     'amtlicher_zeitraum': '2017-2017',
     'amtliches_datum': '2017-10-05',
     'amtliche_modelle': 'V-KLASSE, VITO',
     'generationsquelle': 'https://group.mercedes-benz.com/company/production/production-vle-vitoria.html',
     'generationsstufe': 1,
     'generationsbeleg': 'Serienproduktionsstart des VLE im Werk Vitoria am 12.06.2026; '
                         'Vitoria fertigt ab 2026 VLE, V-Klasse, Vito und eVito flexibel '
                         'auf derselben Linie — die V-Klasse laeuft also parallel weiter.'},
    {'id': 3042,
     'baureihe_id': 'mercedes-benz-v-klasse-w447',
     'datum': '2017-11-27',
     'betroffene_baujahre': '2014-2017',
     'mangel': 'Nicht ausreichende Erdung der Lenksäule in Verbindung mit einer '
               'Beschädigung der Leiterbahnen in der Wickelfederkassette kann  zum '
               'Auslösen des Fahrerairbags führen.',
     'abhilfe': 'Herstellung einer ausreichenden elektrische Erdung der Lenksäule.',
     'kba_referenz': '7399',
     'herstellercode': 'VS2UNMOTAB (5498856)',
     'amtlicher_zeitraum': '2014-2017',
     'amtliches_datum': '2017-11-27',
     'amtliche_modelle': 'V-KLASSE, VITO',
     'generationsquelle': 'https://group.mercedes-benz.com/company/production/production-vle-vitoria.html',
     'generationsstufe': 1,
     'generationsbeleg': 'Serienproduktionsstart des VLE im Werk Vitoria am 12.06.2026; '
                         'Vitoria fertigt ab 2026 VLE, V-Klasse, Vito und eVito flexibel '
                         'auf derselben Linie — die V-Klasse laeuft also parallel weiter.'},
    {'id': 3043,
     'baureihe_id': 'mercedes-benz-v-klasse-w447',
     'datum': '2017-11-01',
     'betroffene_baujahre': '2017',
     'mangel': 'Nicht korrekt verpresste Sicherungsringe am Radlager könnten zum '
               'Radverlust führen.',
     'abhilfe': 'Die Sicherungsringe an den Radlagern der Vorderachse werden auf eine '
                'korrekte Positionierung hin geprüft und gegebenenfalls korrigiert.',
     'kba_referenz': '7413',
     'herstellercode': 'VS2SIRIRAD (3392086)',
     'amtlicher_zeitraum': '2017-2017',
     'amtliches_datum': '2017-11-01',
     'amtliche_modelle': 'V-KLASSE, VITO',
     'generationsquelle': 'https://group.mercedes-benz.com/company/production/production-vle-vitoria.html',
     'generationsstufe': 1,
     'generationsbeleg': 'Serienproduktionsstart des VLE im Werk Vitoria am 12.06.2026; '
                         'Vitoria fertigt ab 2026 VLE, V-Klasse, Vito und eVito flexibel '
                         'auf derselben Linie — die V-Klasse laeuft also parallel weiter.'},
    {'id': 3044,
     'baureihe_id': 'volkswagen-tiguan-ii',
     'datum': '2018-08-09',
     'betroffene_baujahre': '2018',
     'mangel': 'Fehlerhaft produzierte Bremsscheiben können im Bereich der inneren '
               'Topfanbindung brechen.',
     'abhilfe': None,
     'kba_referenz': '7700',
     'herstellercode': '46H4',
     'amtlicher_zeitraum': '2018-2018',
     'amtliches_datum': '2018-08-09',
     'amtliche_modelle': 'PASSAT, GOLF, TIGUAN, ARTEON',
     'generationsquelle': 'https://www.volkswagen-newsroom.com/en/press-releases/new-tiguan-generation-volkswagens-bestseller-celebrates-world-premiere-in-front-of-10000-employees-17655',
     'generationsstufe': 1,
     'generationsbeleg': 'Medieninformation Nr. 140/2023: die dritte Generation laeuft ab '
                         'Herbst 2023 in Wolfsburg vom Band und kommt im ersten Quartal '
                         '2024 auf den Markt.'},
    {'id': 3045,
     'baureihe_id': 'opel-grandland-a',
     'datum': '2018-07-11',
     'betroffene_baujahre': '2017-2018',
     'mangel': 'Fehlerhafte Naht des Sitzbezuges kann im Falle eines Unfalls dazu führen, '
               'dass die Seitenairbags nicht wie vorgesehen auslösen.',
     'abhilfe': 'Ledersitzbezüge an den beiden Vordersitzrücken werden ersetzt',
     'kba_referenz': '7868',
     'herstellercode': 'E181801170 (18-C-045)',
     'amtlicher_zeitraum': '2017-2018',
     'amtliches_datum': '2018-07-11',
     'amtliche_modelle': 'GRANDLAND',
     'generationsquelle': 'https://www.media.stellantis.com/de-de/opel/press/produktionsstart-fuer-den-neuen-opel-grandland-in-eisenach',
     'generationsstufe': 1,
     'generationsbeleg': 'Produktionsstart des voellig neu entwickelten Opel Grandland in '
                         'Eisenach; Weltpremiere am Standort im Fruehjahr 2024.'},
    {'id': 3046,
     'baureihe_id': 'volkswagen-tiguan-ii',
     'datum': '2018-07-12',
     'betroffene_baujahre': '2018',
     'mangel': 'Nicht korrekt verpackter Luftsack des Beifahrerairbags entfaltet sich im '
               'Falle eines Unfalls nicht wie vorgesehen.',
     'abhilfe': None,
     'kba_referenz': '8029',
     'herstellercode': '69W9',
     'amtlicher_zeitraum': '2018-2018',
     'amtliches_datum': '2018-07-12',
     'amtliche_modelle': 'TIGUAN',
     'generationsquelle': 'https://www.volkswagen-newsroom.com/en/press-releases/new-tiguan-generation-volkswagens-bestseller-celebrates-world-premiere-in-front-of-10000-employees-17655',
     'generationsstufe': 1,
     'generationsbeleg': 'Medieninformation Nr. 140/2023: die dritte Generation laeuft ab '
                         'Herbst 2023 in Wolfsburg vom Band und kommt im ersten Quartal '
                         '2024 auf den Markt.'},
    {'id': 3047,
     'baureihe_id': 'volkswagen-tiguan-ii',
     'datum': '2018-07-12',
     'betroffene_baujahre': '2018',
     'mangel': 'Fehlerhafte Verschweißung des Stoßdämpfers kann sich lösen und in der '
               'Folge zu unsicheren Fahrzuständen führen.',
     'abhilfe': None,
     'kba_referenz': '8030',
     'herstellercode': '42i8',
     'amtlicher_zeitraum': '2018-2018',
     'amtliches_datum': '2018-07-12',
     'amtliche_modelle': 'TIGUAN',
     'generationsquelle': 'https://www.volkswagen-newsroom.com/en/press-releases/new-tiguan-generation-volkswagens-bestseller-celebrates-world-premiere-in-front-of-10000-employees-17655',
     'generationsstufe': 1,
     'generationsbeleg': 'Medieninformation Nr. 140/2023: die dritte Generation laeuft ab '
                         'Herbst 2023 in Wolfsburg vom Band und kommt im ersten Quartal '
                         '2024 auf den Markt.'},
    {'id': 3048,
     'baureihe_id': 'mercedes-benz-v-klasse-w447',
     'datum': '2019-03-27',
     'betroffene_baujahre': '2019',
     'mangel': 'Bei einem Unfall mit Auslösung des Beifahrerairbags können die '
               'Airbagklappe oder Splitter Verletzungen verursachen.',
     'abhilfe': 'Die Haut der Instrumententafel inklusive der Airbagklappe auf der '
                'Beifahrerseite wird erneuert.',
     'kba_referenz': '8633',
     'herstellercode': 'VS2KLAPSAB (6891078)',
     'amtlicher_zeitraum': '2019-2019',
     'amtliches_datum': '2019-03-27',
     'amtliche_modelle': 'V-KLASSE',
     'generationsquelle': 'https://group.mercedes-benz.com/company/production/production-vle-vitoria.html',
     'generationsstufe': 1,
     'generationsbeleg': 'Serienproduktionsstart des VLE im Werk Vitoria am 12.06.2026; '
                         'Vitoria fertigt ab 2026 VLE, V-Klasse, Vito und eVito flexibel '
                         'auf derselben Linie — die V-Klasse laeuft also parallel weiter.'},
    {'id': 3049,
     'baureihe_id': 'volkswagen-tiguan-ii',
     'datum': '2019-04-02',
     'betroffene_baujahre': '2017-2018',
     'mangel': 'Durch einen Materialfehler an den hinteren Schraubenfedern kann der Reifen '
               'unter Umständen beschädigt werden.',
     'abhilfe': 'Austausch der betroffenen Teile',
     'kba_referenz': '8727',
     'herstellercode': '42J4',
     'amtlicher_zeitraum': '2017-2018',
     'amtliches_datum': '2019-04-02',
     'amtliche_modelle': 'TIGUAN',
     'generationsquelle': 'https://www.volkswagen-newsroom.com/en/press-releases/new-tiguan-generation-volkswagens-bestseller-celebrates-world-premiere-in-front-of-10000-employees-17655',
     'generationsstufe': 1,
     'generationsbeleg': 'Medieninformation Nr. 140/2023: die dritte Generation laeuft ab '
                         'Herbst 2023 in Wolfsburg vom Band und kommt im ersten Quartal '
                         '2024 auf den Markt.'},
    {'id': 3050,
     'baureihe_id': 'mercedes-benz-v-klasse-w447',
     'datum': '2019-07-03',
     'betroffene_baujahre': '2018-2019',
     'mangel': 'Nicht geeignete verbaute Bremsleitungen führen zum Austritt von '
               'Bremsflüssigkeit und infolge zu einem verlängerten Bremsweg sowie '
               'Brandgefahr durch Kontakt mit erhitzten Bauteilen.',
     'abhilfe': 'Die verbauten Bremsleitungen werden überprüft und ggf. durch Nachbiegen '
                'angepasst. Zusätzlich wird ein Scheuerschutz angebracht.',
     'kba_referenz': '9005',
     'herstellercode': 'VS2BREMVAR (4296075)',
     'amtlicher_zeitraum': '2018-2019',
     'amtliches_datum': '2019-07-03',
     'amtliche_modelle': 'V-KLASSE, VITO',
     'generationsquelle': 'https://group.mercedes-benz.com/company/production/production-vle-vitoria.html',
     'generationsstufe': 1,
     'generationsbeleg': 'Serienproduktionsstart des VLE im Werk Vitoria am 12.06.2026; '
                         'Vitoria fertigt ab 2026 VLE, V-Klasse, Vito und eVito flexibel '
                         'auf derselben Linie — die V-Klasse laeuft also parallel weiter.'},
    {'id': 3051,
     'baureihe_id': 'volkswagen-tiguan-ii',
     'datum': '2019-08-23',
     'betroffene_baujahre': '2019',
     'mangel': 'Sicherungsschraube der Parksperre des automatisierten Getriebes ist nicht '
               'mit dem korrekten Drehmoment verschraubt, in der Folge kann die Parksperre '
               'versagen und das Fahrzeug wegrollen.',
     'abhilfe': 'Bei den betroffenen Fahrzeugen werden die Getriebe ersetzt.',
     'kba_referenz': '9076',
     'herstellercode': '34J3',
     'amtlicher_zeitraum': '2019-2019',
     'amtliches_datum': '2019-08-23',
     'amtliche_modelle': 'TIGUAN',
     'generationsquelle': 'https://www.volkswagen-newsroom.com/en/press-releases/new-tiguan-generation-volkswagens-bestseller-celebrates-world-premiere-in-front-of-10000-employees-17655',
     'generationsstufe': 1,
     'generationsbeleg': 'Medieninformation Nr. 140/2023: die dritte Generation laeuft ab '
                         'Herbst 2023 in Wolfsburg vom Band und kommt im ersten Quartal '
                         '2024 auf den Markt.'},
    {'id': 3052,
     'baureihe_id': 'mercedes-benz-v-klasse-w447',
     'datum': '2019-09-16',
     'betroffene_baujahre': '2019',
     'mangel': 'Fehlbedienung des Fahrzeugs aufgrund fehlerhafter Bedienungsanleitung '
               '(u.a. Warnhinweise Airbag).',
     'abhilfe': None,
     'kba_referenz': '9119',
     'herstellercode': 'VS2BAFV (5891163)',
     'amtlicher_zeitraum': '2019-2019',
     'amtliches_datum': '2019-09-16',
     'amtliche_modelle': 'V-KLASSE, VITO',
     'generationsquelle': 'https://group.mercedes-benz.com/company/production/production-vle-vitoria.html',
     'generationsstufe': 1,
     'generationsbeleg': 'Serienproduktionsstart des VLE im Werk Vitoria am 12.06.2026; '
                         'Vitoria fertigt ab 2026 VLE, V-Klasse, Vito und eVito flexibel '
                         'auf derselben Linie — die V-Klasse laeuft also parallel weiter.'},
    {'id': 3053,
     'baureihe_id': 'opel-grandland-a',
     'datum': '2019-10-30',
     'betroffene_baujahre': '2019',
     'mangel': 'Mangelhafte Verschraubung des hinteren mittleren Sicherheitsgurts kann zum '
               'Verlust der Schutzfunktion führen.',
     'abhilfe': 'Kontrolle und ggf. Ausbesserung der Verschraubung.',
     'kba_referenz': '9253',
     'herstellercode': 'E19-190348',
     'amtlicher_zeitraum': '2019-2019',
     'amtliches_datum': '2019-10-30',
     'amtliche_modelle': 'GRANDLAND',
     'generationsquelle': 'https://www.media.stellantis.com/de-de/opel/press/produktionsstart-fuer-den-neuen-opel-grandland-in-eisenach',
     'generationsstufe': 1,
     'generationsbeleg': 'Produktionsstart des voellig neu entwickelten Opel Grandland in '
                         'Eisenach; Weltpremiere am Standort im Fruehjahr 2024.'},
    {'id': 3054,
     'baureihe_id': 'mercedes-benz-v-klasse-w447',
     'datum': '2019-11-06',
     'betroffene_baujahre': '2018',
     'mangel': 'Fehlerhaftes Magnetventil innerhalb des Bremsflüssigkeitskreislaufes '
               'bedingt in bestimmten Fahrsituationen einen verlängerten Bremsweg.',
     'abhilfe': 'Die ESP Hydraulikeinheit wird erneuert.',
     'kba_referenz': '9372',
     'herstellercode': 'VS2BREPE (4294058)',
     'amtlicher_zeitraum': '2018-2018',
     'amtliches_datum': '2019-11-06',
     'amtliche_modelle': 'V-KLASSE, VITO',
     'generationsquelle': 'https://group.mercedes-benz.com/company/production/production-vle-vitoria.html',
     'generationsstufe': 1,
     'generationsbeleg': 'Serienproduktionsstart des VLE im Werk Vitoria am 12.06.2026; '
                         'Vitoria fertigt ab 2026 VLE, V-Klasse, Vito und eVito flexibel '
                         'auf derselben Linie — die V-Klasse laeuft also parallel weiter.'},
    {'id': 3055,
     'baureihe_id': 'mercedes-benz-v-klasse-w447',
     'datum': '2020-09-28',
     'betroffene_baujahre': '2019',
     'mangel': 'Bei einem Unfall mit Auslösung des Beifahrerairbags können die '
               'Airbagklappe oder SplitterVerletzungen verursachen.',
     'abhilfe': None,
     'kba_referenz': '9501',
     'herstellercode': 'VS2KLAPSA2 (6890055)',
     'amtlicher_zeitraum': '2019-2019',
     'amtliches_datum': '2020-09-28',
     'amtliche_modelle': 'V-KLASSE',
     'generationsquelle': 'https://group.mercedes-benz.com/company/production/production-vle-vitoria.html',
     'generationsstufe': 1,
     'generationsbeleg': 'Serienproduktionsstart des VLE im Werk Vitoria am 12.06.2026; '
                         'Vitoria fertigt ab 2026 VLE, V-Klasse, Vito und eVito flexibel '
                         'auf derselben Linie — die V-Klasse laeuft also parallel weiter.'},
    {'id': 3056,
     'baureihe_id': 'volkswagen-tiguan-ii',
     'datum': '2020-01-08',
     'betroffene_baujahre': '2019',
     'mangel': 'Zu geringe Wandstärke des Kraftstoffbehälters',
     'abhilfe': 'Austausch des Kraftstofftanks.',
     'kba_referenz': '9531',
     'herstellercode': '20BV',
     'amtlicher_zeitraum': '2019-2019',
     'amtliches_datum': '2020-01-08',
     'amtliche_modelle': 'TIGUAN, TOURAN',
     'generationsquelle': 'https://www.volkswagen-newsroom.com/en/press-releases/new-tiguan-generation-volkswagens-bestseller-celebrates-world-premiere-in-front-of-10000-employees-17655',
     'generationsstufe': 1,
     'generationsbeleg': 'Medieninformation Nr. 140/2023: die dritte Generation laeuft ab '
                         'Herbst 2023 in Wolfsburg vom Band und kommt im ersten Quartal '
                         '2024 auf den Markt.'},
    {'id': 3057,
     'baureihe_id': 'mercedes-benz-v-klasse-w447',
     'datum': '2020-05-13',
     'betroffene_baujahre': '2018-2019',
     'mangel': 'Die Kraftstoffrücklaufleitung kann durch Kontakt mit der '
               'Unterbodenverkleidung beschädigt werden.',
     'abhilfe': 'Kraftstoffrücklaufleitung wird überprüft und ggf. ersetzt. Anpassung der '
                'Verlegung der Kraftstoffrücklaufleitung und Einsetzung Abstandshalter.',
     'kba_referenz': '9883',
     'herstellercode': 'VS2KRAFLEI',
     'amtlicher_zeitraum': '2018-2019',
     'amtliches_datum': '2020-05-13',
     'amtliche_modelle': 'V-KLASSE, VITO',
     'generationsquelle': 'https://group.mercedes-benz.com/company/production/production-vle-vitoria.html',
     'generationsstufe': 1,
     'generationsbeleg': 'Serienproduktionsstart des VLE im Werk Vitoria am 12.06.2026; '
                         'Vitoria fertigt ab 2026 VLE, V-Klasse, Vito und eVito flexibel '
                         'auf derselben Linie — die V-Klasse laeuft also parallel weiter.'},
    {'id': 3058,
     'baureihe_id': 'audi-q3-ii',
     'datum': '2020-05-20',
     'betroffene_baujahre': '2019-2020',
     'mangel': 'Mangelhafte Sollbruchstelle für den Durchstoß des Beifahrerairbags in der '
               'Schalttafel',
     'abhilfe': 'Das Fertigungsdatum der Schalttafel wird geprüft und gegebenenfalls '
                'ausgetauscht.',
     'kba_referenz': '9943',
     'herstellercode': '70H7',
     'amtlicher_zeitraum': '2019-2020',
     'amtliches_datum': '2020-05-20',
     'amtliche_modelle': 'Q3',
     'generationsquelle': 'https://www.audi.com/en/press-releases/versatile-sporty-and-digitally-connected-the-new-audi-q3-16771',
     'generationsstufe': 1,
     'generationsbeleg': 'Nach der Weltpremiere im Juni begann die Serienproduktion der '
                         'dritten Generation in Gyoer; Markteinfuehrung im Oktober.'},
)


AUSSCHLUESSE = (
    ('10053',
     'opel-corsa-f',
     '2019-2020',
     'A1 zweites plausibles Generationsziel: opel-corsa-f 100% gegen opel-corsa-e 50%'),
    ('10383',
     'opel-corsa-f',
     '2019-2020',
     'A1 zweites plausibles Generationsziel: opel-zafira-life-1-generation 100% gegen '
     'opel-zafira-c 50%'),
    ('10383',
     'opel-grandland-a',
     '2019-2020',
     'A1 zweites plausibles Generationsziel: opel-zafira-life-1-generation 100% gegen '
     'opel-zafira-c 50%'),
    ('10448',
     'audi-q2-ga',
     '2020-2020',
     "A2 amtliche Eingrenzung nicht abbildbar: 'Automatikfahrzeuge'"),
    ('10448',
     'audi-q3-ii',
     '2020-2020',
     "A2 amtliche Eingrenzung nicht abbildbar: 'Automatikfahrzeuge'"),
    ('10539',
     'audi-q3-ii',
     '2019-2020',
     "A2 amtliche Eingrenzung nicht abbildbar: 'Fahrzeuge aus dem Produktionszeitraum Juni "
     "2019 bis Januar 2020.'"),
    ('10546',
     'bmw-x5-g05',
     '2020-2020',
     "A2 amtliche Eingrenzung nicht abbildbar: 'Herstellungszeitraum: 05. bis 18.09.2020'"),
    ('10579',
     'bmw-x5-g05',
     '2018-2021',
     'A1 zweites plausibles Generationsziel: bmw-x6-g06 75% gegen bmw-x6-f16 50%'),
    ('10594',
     'bmw-x5-g05',
     '2020-2020',
     "A2 amtliche Eingrenzung nicht abbildbar: 'Der Mangel betrifft die 22 Zoll Reifen von "
     "Continental Eco Contact 6. '"),
    ('11054',
     'mercedes-benz-s-klasse-w223',
     '2020-2021',
     'A1 zweites plausibles Generationsziel: mercedes-benz-s-klasse-w223 100% gegen '
     'mercedes-benz-s-klasse-w222 50%'),
    ('11119',
     'mercedes-benz-s-klasse-w223',
     '2020-2021',
     'A1 zweites plausibles Generationsziel: mercedes-benz-s-klasse-w223 100% gegen '
     'mercedes-benz-s-klasse-w222 50%'),
    ('11329',
     'volkswagen-tiguan-ii',
     '2021-2021',
     "A2 amtliche Eingrenzung nicht abbildbar: 'Allspace'"),
    ('11672',
     'mercedes-benz-s-klasse-w223',
     '2020-2021',
     'A1 zweites plausibles Generationsziel: mercedes-benz-s-klasse-w223 100% gegen '
     'mercedes-benz-s-klasse-w222 50%'),
    ('11696',
     'volkswagen-golf-viii',
     '2019-2022',
     'A1 zweites plausibles Generationsziel: volkswagen-golf-viii 100% gegen '
     'volkswagen-golf-vii 50%'),
    ('11696',
     'volkswagen-tiguan-ii',
     '2019-2022',
     'A1 zweites plausibles Generationsziel: volkswagen-golf-viii 100% gegen '
     'volkswagen-golf-vii 50%'),
    ('11697',
     'audi-q3-ii',
     '2020-2022',
     "A2 amtliche Eingrenzung nicht abbildbar: 'A3 e-tron, Q3 e-tron (Hybrid)'"),
    ('11760',
     'mercedes-benz-s-klasse-w223',
     '2020-2021',
     'A1 zweites plausibles Generationsziel: mercedes-benz-s-klasse-w223 100% gegen '
     'mercedes-benz-s-klasse-w222 50%'),
    ('11787',
     'volkswagen-golf-viii',
     '2022-2022',
     "A2 amtliche Eingrenzung nicht abbildbar: 'Golf und Golf Variant, Produktionszeitraum "
     "10.01.2022 - 22.01.2022'"),
    ('12127',
     'audi-q3-ii',
     '2020-2022',
     "A2 amtliche Eingrenzung nicht abbildbar: 'auch Q3 Sportback'"),
    ('12392',
     'mercedes-benz-s-klasse-w223',
     '2022-2022',
     "A2 amtliche Eingrenzung nicht abbildbar: 'Baureihe: 223'"),
    ('12418',
     'mercedes-benz-v-klasse-w447',
     '2020-2022',
     "A2 amtliche Eingrenzung nicht abbildbar: 'auch Vito Tourer'"),
    ('12419',
     'mercedes-benz-c-klasse-w206',
     '2021-2022',
     'A1 zweites plausibles Generationsziel: mercedes-benz-c-klasse-w206 100% gegen '
     'mercedes-benz-c-klasse-w205 50%'),
    ('12772',
     'mercedes-benz-s-klasse-w223',
     '2020-2021',
     'A1 zweites plausibles Generationsziel: mercedes-benz-s-klasse-w223 100% gegen '
     'mercedes-benz-s-klasse-w222 50%'),
    ('13015',
     'mercedes-benz-s-klasse-w223',
     '2020-2022',
     "A2 amtliche Eingrenzung nicht abbildbar: 'Rechtslenker'"),
    ('13099',
     'audi-q2-ga',
     '2019-2023',
     'A1 zweites plausibles Generationsziel: audi-a3-typ-8y 80% gegen '
     'audi-rs-3-sportback-8v 40%'),
    ('13228',
     'mercedes-benz-sl-r232',
     '2021-2023',
     "A2 amtliche Eingrenzung nicht abbildbar: 'SL mit M177'"),
    ('13456',
     'volkswagen-golf-viii',
     '2019-2023',
     "A2 amtliche Eingrenzung nicht abbildbar: 'Rechtslenker'"),
    ('13541',
     'mercedes-benz-c-klasse-w206',
     '2023-2023',
     "A2 amtliche Eingrenzung nicht abbildbar: 'Plug-in-Hybrid'"),
    ('13564',
     'mercedes-benz-c-klasse-w206',
     '2022-2023',
     "A2 amtliche Eingrenzung nicht abbildbar: 'Hybrid-Fahrzeuge'"),
    ('13579',
     'mercedes-benz-s-klasse-w223',
     '2022-2023',
     "A2 amtliche Eingrenzung nicht abbildbar: 'Mercedes-AMG S63E'"),
    ('13809',
     'mercedes-benz-c-klasse-w206',
     '2021-2022',
     'A1 zweites plausibles Generationsziel: mercedes-benz-c-klasse-w206 100% gegen '
     'mercedes-benz-c-klasse-w205 50%'),
    ('13990R',
     'mercedes-benz-c-klasse-w206',
     '2021-2022',
     'A1 zweites plausibles Generationsziel: mercedes-benz-c-klasse-w206 100% gegen '
     'mercedes-benz-c-klasse-w205 50%'),
    ('14115R',
     'mercedes-benz-c-klasse-w206',
     '2021-2023',
     "A2 amtliche Eingrenzung nicht abbildbar: 'BR206'"),
    ('14775R',
     'mercedes-benz-eqa-h243',
     '2021-2024',
     "A2 amtliche Eingrenzung nicht abbildbar: 'BR 243'"),
    ('15235R',
     'volkswagen-tiguan-ii',
     '2018-2019',
     "A2 amtliche Eingrenzung nicht abbildbar: 'Tiguan Allspace'"),
    ('15269R',
     'volkswagen-golf-viii',
     '2024-2024',
     "A2 amtliche Eingrenzung nicht abbildbar: 'Golf 8, Golf 8 Variant'"),
    ('15275R',
     'volkswagen-golf-viii',
     '2024-2024',
     "A2 amtliche Eingrenzung nicht abbildbar: 'Golf 8, Golf 8 Variant'"),
    ('15353R',
     'opel-corsa-f',
     '2022-2025',
     'A1 zweites plausibles Generationsziel: opel-grandland-a 100% gegen opel-grandland-b '
     '50%'),
    ('15630R',
     'bmw-x5-g05',
     '2018-2025',
     "A2 amtliche Eingrenzung nicht abbildbar: '8er: Coupe, Cabrio, Cran Coupe'"),
    ('16136R',
     'mercedes-benz-eqa-h243',
     '2021-2024',
     "A2 amtliche Eingrenzung nicht abbildbar: 'BR243'"),
    ('8852',
     'mercedes-benz-a-klasse-w177',
     '2018-2019',
     'A1 zweites plausibles Generationsziel: mercedes-benz-a-klasse-w177 100% gegen '
     'mercedes-benz-a-klasse-w176 50%'),
    ('9051',
     'bmw-x5-g05',
     '2018-2019',
     'A1 zweites plausibles Generationsziel: bmw-x5-g05 100% gegen bmw-x5-f15 50%'),
)


def zeilen_ids() -> set:
    """Die IDs, die diese Migration vergibt."""
    return {z['id'] for z in ZEILEN}
