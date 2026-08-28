from __future__ import annotations

"""
Kuratiertes Ergebnis des Mixed-Target-Audits vom 2026-08-28.

Die 32 Zielpaare sind die vom Nutzer freigegebene sichere Teilmenge der
39 bisher ausgeschlossenen Mixed-Target-KBA-Zeilen. Ein KBA-Fall wird hier
je VIRA-Zielgeneration bewertet; die sieben unsicheren Zielpaare bleiben
als ausdrueckliche Negativliste versioniert und werden nie importiert.

Die amtlichen Inhalte stammen aus dem vorhandenen KBA-Gesamtexport vom
2026-08-27. Keine Webquelle wird zur Laufzeit gelesen. Alle Zielzeilen sind
allgemeine Baureihen-Rueckrufe ohne darstellbare Variantenbedingung und
muessen deshalb zur Laufzeit series_only bleiben.
"""

GEPRUEFT_AM = "2026-08-28"
ID_BASIS_MIXED = 4001

_ZIELZEILEN = (
    ('13433', 'bmw-5er-g30', '2023'),
    ('10579', 'bmw-7er-g11/g12', '2018-2021'),
    ('9051', 'bmw-7er-g11/g12', '2018-2019'),
    ('10579', 'bmw-m4-f82', '2018-2020'),
    ('10009', 'bmw-x3-g01', '2018-2019'),
    ('9839', 'bmw-x3-g01', '2019-2020'),
    ('13459', 'hyundai-tucson-dritte-generation', '2018-2020'),
    ('13136', 'kia-sportage-ql', '2018-2020'),
    ('10174', 'mercedes-benz-c-klasse-w205', '2018-2020'),
    ('11352', 'mercedes-benz-c-klasse-w205', '2017-2021'),
    ('10174', 'mercedes-benz-e-klasse-w213', '2018-2020'),
    ('11352', 'mercedes-benz-e-klasse-w213', '2017-2021'),
    ('13578', 'mercedes-benz-e-klasse-w213', '2021-2023'),
    ('10174', 'mercedes-benz-glc-x253', '2018-2020'),
    ('11352', 'mercedes-benz-glc-x253', '2017-2021'),
    ('10174', 'mercedes-benz-s-klasse-w222', '2018-2020'),
    ('10383', 'opel-astra-k', '2019-2020'),
    ('10383', 'opel-insignia-b', '2019-2020'),
    ('8961', 'skoda-fabia-dritte-generation', '2018'),
    ('8961', 'skoda-kodiaq-erste-generation', '2018'),
    ('8961', 'skoda-octavia-dritte-generation', '2018'),
    ('8961', 'skoda-superb-dritte-generation', '2018'),
    ('14643R', 'toyota-c-hr-i', '2018-2022'),
    ('8743', 'toyota-rav4-iv', '2015-2018'),
    ('8743', 'toyota-yaris-iii', '2015-2018'),
    ('7473', 'volkswagen-golf-vii', '2017'),
    ('7700', 'volkswagen-golf-vii', '2018'),
    ('10749', 'volkswagen-passat-b8', '2020-2021'),
    ('11162', 'volkswagen-passat-b8', '2019-2021'),
    ('7473', 'volkswagen-passat-b8', '2017'),
    ('7700', 'volkswagen-passat-b8', '2018'),
    ('9783', 'volkswagen-passat-b8', '2018-2019'),
)

SAFE_ZIELPAARE = tuple((ref, baureihe_id) for ref, baureihe_id, _ in _ZIELZEILEN)

AUSGESCHLOSSENE_ZIELPAARE = (
    ('13262', 'bmw-5er-g30'),
    ('13262', 'bmw-x3-g01'),
    ('8819', 'bmw-x3-g01'),
    ('11352', 'mercedes-benz-s-klasse-w222'),
    ('10540', 'volkswagen-passat-b8'),
    ('11696', 'volkswagen-passat-b8'),
    ('13456', 'volkswagen-passat-b8'),
)

_AMTLICHE_DATEN = {
    '13433': {
        'datum': '2024-01-15',
        'mangel': 'Fehlerhaft gefertigte Zylinderkopfhaube kann zum \xd6laustritt und in der Folge erh\xf6hter Brandgefahr f\xfchren.',
        'abhilfe': 'Kontrolle und ggf. Austausch der Zylinderkopfhaube.',
        'herstellercode': '0011590700',
        'amtlicher_zeitraum': '2023-2023',
        'amtliches_datum': '2024-01-15',
        'amtliche_modelle': '3, 4, 5',
    },
    '10579': {
        'datum': '2021-04-13',
        'mangel': 'Unter Umst\xe4nden kann es zu einer eingeschr\xe4nkten Funktion des Bremskraftverst\xe4rkers kommen.',
        'abhilfe': 'Die Hydraulikeinheit des Bremssystems wird ersetzt.',
        'herstellercode': '0034550200',
        'amtlicher_zeitraum': '2018-2021',
        'amtliches_datum': '2021-04-13',
        'amtliche_modelle': 'M4, X7, X5, IX3, 7, M3, 8, X6, M8',
    },
    '9051': {
        'datum': '2019-08-01',
        'mangel': 'Fehlerhaft verschraubte Kraftstoffpumpen bedingen, dass Kraftstoff austreten k\xf6nnte und es in der Folge zu einem Motorausfall und/oder Fahrzeugbrand kommt.',
        'abhilfe': 'Es werden beide Hochdruckpumpen ersetzt.',
        'herstellercode': '0013090300',
        'amtlicher_zeitraum': '2018-2019',
        'amtliches_datum': '2019-08-01',
        'amtliche_modelle': 'X7, M5, X5, 7, 8',
    },
    '10009': {
        'datum': '2020-08-03',
        'mangel': 'Aufgrund fehlerhafter Auslegung kann es bei hohen Belastungen zu einem Dauerschwingbruch in der Knicknut der Spurstange kommen, wodurch die Radf\xfchrung nicht mehr gew\xe4hrleistet ist und in der Folge erh\xf6hte Unfallgefahr besteht.',
        'abhilfe': 'Beide Spurstangen werden ersetzt.',
        'herstellercode': '0032140300',
        'amtlicher_zeitraum': '2018-2019',
        'amtliches_datum': '2020-08-03',
        'amtliche_modelle': '3, X3, X4, Z4',
    },
    '9839': {
        'datum': '2020-04-15',
        'mangel': 'Durch eine fehlerhaft montierte Sensorik innerhalb des Gurtschlosses kann es zu fehlerhaften Ausl\xf6sungen der f\xfcr den betroffenen Sitzplatz vorgesehenen Airbags und Gurtstraffern kommen.',
        'abhilfe': 'Pr\xfcfung und ggf. Austausch des betroffenen Sicherheitsgurtschlosses',
        'herstellercode': '0072130200',
        'amtlicher_zeitraum': '2019-2020',
        'amtliches_datum': '2020-04-15',
        'amtliche_modelle': '3, X3, X4, X3M, 8, M8, X4M',
    },
    '13459': {
        'datum': '2024-01-15',
        'mangel': 'Aufgrund der Unterbrechung der \xd6lversorgung kann es zu einem Ausfall des Bremskraftverst\xe4rkers sowie zu einem Motorschaden kommen.',
        'abhilfe': 'Das Metallsieb des \xd6lsystems wird entfernt und die Tandempumpe gepr\xfcft.',
        'herstellercode': '21DC03',
        'amtlicher_zeitraum': '2018-2020',
        'amtliches_datum': '2024-01-15',
        'amtliche_modelle': 'I30, TUCSON',
    },
    '13136': {
        'datum': '2023-10-10',
        'mangel': 'Konstruktionsfehler am Filtersieb der Tandempumpe f\xfcr \xd6l- und Unterdruckerzeugung kann zum Ausfall des Bremskraftverst\xe4rkers f\xfchren.',
        'abhilfe': 'Das Filtersieb wird entfernt und die Tandempumpe \xfcberpr\xfcft und ggf. ausgetauscht.',
        'herstellercode': '220S15 / 221048',
        'amtlicher_zeitraum': '2018-2020',
        'amtliches_datum': '2023-10-10',
        'amtliche_modelle': 'SPORTAGE, XCEED, PROCEED, CEED, STONIC, OPTIMA',
    },
    '10174': {
        'datum': '2020-08-19',
        'mangel': 'Es besteht die M\xf6glichkeit, dass die Verschraubung der \xd6lzu- und \xd6lr\xfccklaufleitung des Abgasturboladers nicht korrekt ausgef\xfchrt wurde. In der Folge besteht bei \xd6laustritt in Kontakt mit erw\xe4rmten Bauteilen erh\xf6hte Brandgefahr.',
        'abhilfe': 'Die Verschraubungen der \xd6lzu- und \xd6lr\xfccklaufleitung des Abgasturboladers werden \xfcberpr\xfcft und ggf. korrigiert.',
        'herstellercode': '0993103',
        'amtlicher_zeitraum': '2018-2020',
        'amtliches_datum': '2020-08-19',
        'amtliche_modelle': 'C-KLASSE, CLS, S-KLASSE, GLC, E-KLASSE',
    },
    '11352': {
        'datum': '2021-11-15',
        'mangel': 'Aufgrund einer Undichtigkeit innerhalb der K\xfchlmittelpumpe kann es zu erh\xf6hter Brandgefahr kommen.',
        'abhilfe': 'Softwareupdate und Austausch des elektrischen Umschaltventils.',
        'herstellercode': '2090008',
        'amtlicher_zeitraum': '2017-2021',
        'amtliches_datum': '2021-11-15',
        'amtliche_modelle': 'C-KLASSE, GLS, CLS, GLE, S-KLASSE, GLC, E-KLASSE, G-KLASSE',
    },
    '13578': {
        'datum': '2024-02-12',
        'mangel': 'Nicht der Spezifikation entsprechende Befestigung der 48V-Massestelle im Motorraum kann sich l\xf6sen und zu einer Brandgefahr f\xfchren.',
        'abhilfe': 'Die Verschraubung der Massestelle wird \xfcberpr\xfcft und ggf. repariert.',
        'herstellercode': '5491318',
        'amtlicher_zeitraum': '2021-2023',
        'amtliches_datum': '2024-02-12',
        'amtliche_modelle': 'CLS, E-KLASSE, AMG GT',
    },
    '10383': {
        'datum': '2021-01-20',
        'mangel': 'Unzureichendes Anzugsdrehmoment der Radverschraubungen. Es besteht die Gefahr, dass sich die R\xe4der l\xf6sen k\xf6nnen und es zum Kontrollverlust \xfcber das Fahrzeug kommt. Hierdurch besteht eine erh\xf6hte Unfall- und Verletzungsgefahr.',
        'abhilfe': 'Die Verschraubung aller R\xe4der wird \xfcberpr\xfcft und ggf. mit dem korrekten Anzugsdrehmoment befestigt.',
        'herstellercode': 'E202008530 (20-C-172) O14',
        'amtlicher_zeitraum': '2019-2020',
        'amtliches_datum': '2021-01-20',
        'amtliche_modelle': 'ASTRA, COMBO, INSIGNIA, ZAFIRA, CORSA, CROSSLAND, GRANDLAND, VIVARO',
    },
    '8961': {
        'datum': '2019-10-25',
        'mangel': 'Fehler im Gasgenerator f\xfchrt ggf. dazu, dass das Geh\xe4use bricht und sich der Fahrerairbag nicht vollst\xe4ndig entfaltet.',
        'abhilfe': 'Ersetzen des Fahrerairbags am Lenkrad.',
        'herstellercode': '69Y9',
        'amtlicher_zeitraum': '2018-2018',
        'amtliches_datum': '2019-10-25',
        'amtliche_modelle': 'OCTAVIA, KAROQ, FABIA, KODIAQ, SUPERB, RAPID',
    },
    '14643R': {
        'datum': '2025-03-06',
        'mangel': 'Brandgefahr',
        'abhilfe': 'Die Kraftstoffhochdruckpumpe wird erneut gepr\xfcft und ggf. ausgetauscht.',
        'herstellercode': '24SD-149',
        'amtlicher_zeitraum': '2018-2022',
        'amtliches_datum': '2025-03-06',
        'amtliche_modelle': 'AURIS, COROLLA, C-HR',
    },
    '8743': {
        'datum': '2019-05-03',
        'mangel': 'Fehler im Gasgenerator des Fahrerairbags kann bei Airbagausl\xf6sung zu unkontrollierter Entfaltung und zum L\xf6sen von Metallfragmenten f\xfchren, die die Insassen verletzen k\xf6nnen.',
        'abhilfe': None,
        'herstellercode': '5KET-040',
        'amtlicher_zeitraum': '2015-2018',
        'amtliches_datum': '2019-05-03',
        'amtliche_modelle': 'YARIS, HILUX, RAV4',
    },
    '7473': {
        'datum': '2017-11-30',
        'mangel': 'Nicht korrekt geh\xe4rtete Radlagergeh\xe4use der Hinterachse k\xf6nnen rei\xdfen. Dies kann zum Verlust der Fahrstabilit\xe4t f\xfchren.',
        'abhilfe': 'Austausch der Radlagergeh\xe4use',
        'herstellercode': '42I2',
        'amtlicher_zeitraum': '2017-2017',
        'amtliches_datum': '2017-11-30',
        'amtliche_modelle': 'PASSAT, GOLF, ARTEON',
    },
    '7700': {
        'datum': '2018-08-09',
        'mangel': 'Fehlerhaft produzierte Bremsscheiben k\xf6nnen im Bereich der inneren Topfanbindung brechen.',
        'abhilfe': None,
        'herstellercode': '46H4',
        'amtlicher_zeitraum': '2018-2018',
        'amtliches_datum': '2018-08-09',
        'amtliche_modelle': 'PASSAT, GOLF, TIGUAN, ARTEON',
    },
    '10749': {
        'datum': '2021-04-15',
        'mangel': 'Unter Umst\xe4nden kann es zu einer fehlerhaften Absicherung der 12V Batterieplusleitung kommen, wodurch bei Eintreten eines Unfalls erh\xf6hte Brandgefahr besteht.',
        'abhilfe': 'Pr\xfcfung, ob die Batterieplusleitung und die Leitung zum Steuerger\xe4t f\xfcr die Batterie\xfcberwachung an der richtigen Stelle der Hauptsicherungsbox angeschlossen sind. Bei Bedarf sind die Leitungen zu tauschen.',
        'herstellercode': '97FF',
        'amtlicher_zeitraum': '2020-2021',
        'amtliches_datum': '2021-04-15',
        'amtliche_modelle': 'PASSAT, ARTEON',
    },
    '11162': {
        'datum': '2021-09-15',
        'mangel': 'Aufgrund einer nicht ordnungsgem\xe4\xdf ausgef\xfchrten Verschraubung am Bremskraftverst\xe4rker, kann es zu Einschr\xe4nkungen bei der Betriebsbremse kommen.',
        'abhilfe': 'Die Verschraubung der Druckeingangsstange zwischen dem elektronischen Bremskraftverst\xe4rker (eBKV) und dem Bremspedal muss gepr\xfcft und gegebenenfalls nachgezogen werden.',
        'herstellercode': '47R3',
        'amtlicher_zeitraum': '2019-2021',
        'amtliches_datum': '2021-09-15',
        'amtliche_modelle': 'PASSAT, ARTEON',
    },
    '9783': {
        'datum': '2020-04-24',
        'mangel': 'Aufgrund eines fehlerhaften Bremspedal\xfcbersetzungsverh\xe4ltnisses kann es zu erh\xf6hten Bremskr\xe4ften und in der Folge zu einem verl\xe4ngerten Bremsweg kommen.',
        'abhilfe': '\xdcberpr\xfcfung der Verrastung der Druckeingangsstange im Bremspedal. Ggf. Austausch des Bremskraftverst\xe4rkers und der Aufnahme im Bremspedal.',
        'herstellercode': '47P8',
        'amtlicher_zeitraum': '2018-2019',
        'amtliches_datum': '2020-04-24',
        'amtliche_modelle': 'PASSAT, ARTEON',
    },
}

ZEILEN = tuple(
    {
        "id": ID_BASIS_MIXED + index,
        "baureihe_id": baureihe_id,
        "betroffene_baujahre": baujahre,
        "kba_referenz": referenz,
        **_AMTLICHE_DATEN[referenz],
    }
    for index, (referenz, baureihe_id, baujahre) in enumerate(_ZIELZEILEN)
)

def zeilen_ids() -> set[int]:
    return {z["id"] for z in ZEILEN}
