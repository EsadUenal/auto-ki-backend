from __future__ import annotations

"""
Kuratiertes Ergebnis des RECALL-VERIFICATION-/CLEANUP-PILOTEN.

GEGENSTAND
----------
Genau die 14 `rueckruf`-Zeilen der vier KaufCheck-Pilotfahrzeuge:

    bmw-3er-g20-g21                 (Testfahrzeug 320d, Baujahr 2020)
    opel-insignia-b                 (Testfahrzeug 2.0 Diesel, Baujahr 2019/2020)
    audi-a3-typ-8p                  (Testfahrzeug 2.0 FSI 150 PS, Baujahr 2008)
    mercedes-benz-c-klasse-w205     (Testfahrzeug C220d, Baujahr 2016)

Kein Rueckruf einer anderen Baureihe wird beruehrt, keine andere Faktenart.

QUELLENHIERARCHIE (Vorgabe des Pilotauftrags)
---------------------------------------------
``A`` KBA / offizielle Behoerden
``B`` Hersteller-Rueckruf-/Serviceinformationen
``C`` EU Safety Gate / andere amtliche Rueckrufdatenbanken, sofern das Fahrzeug
      eindeutig zuordenbar ist
Fachmedien nur ergaenzend. Foren, SEO-Seiten, Aggregatoren, Blogs und
KI-Zusammenfassungen tragen NIEMALS ein `verified`.

WARUM SO WENIG `verified` HERAUSKOMMT
-------------------------------------
Die amtliche Primaerquelle war waehrend der Pruefung nur eingeschraenkt
zugaenglich: der Bulk-Export der KBA-Rueckrufdatenbank
(kba-online.de/rrdb/buerger/api/rueckruf/export) antwortete durchgehend mit
HTTP 503, und die interaktive Suche ist captcha-gesichert. Geprueft wurde
deshalb ueber das KBA-Presseachiv, die amtliche NHTSA-Rueckrufdatenbank und
Fachmedien, die amtliche Referenznummern mitfuehren. Wo das keinen Beleg ergab,
steht `unverified` — ausdruecklich NICHT `rejected`. "Nicht gefunden" ist keine
Widerlegung.

DIE REGEL, DIE UEBERALL GLEICH ANGEWANDT WURDE
----------------------------------------------
Ein DB-Rueckruf wird nur dann inhaltlich UMGESCHRIEBEN, wenn Modell UND
konkretes Fehlerbild mit einer belegten Aktion uebereinstimmen und lediglich
Randfelder (Datum, Bauzeitraum, Referenz) falsch sind — eine Identitaet also
feststeht. Aehnelt eine DB-Zeile einer realen Aktion nur im Themengebiet
(gleiches Teilsystem, anderer Mechanismus), bleibt sie inhaltlich unangetastet
und wird `unverified` gestellt; sie in die reale Aktion umzuschreiben waere eine
GERATENE Identitaet und ist genau das, was dieser Pilot verhindern soll.
"""

GEPRUEFT_AM = "2026-08-27"


# ─────────────────────────────────────────────────────────────────────────────
# 1) DATENKORREKTUREN
# ─────────────────────────────────────────────────────────────────────────────
#
# (fakt_id, baureihe_id, erwartet, neu, begruendung)
#
# `erwartet` ist eine PRECONDITION: nur wenn die Zeile exakt diese Werte traegt,
# wird sie geschrieben. Traegt sie bereits die Zielwerte, gilt die Korrektur als
# erledigt (Idempotenz). Jede andere Belegung wird uebersprungen und protokolliert
# — lieber keine Korrektur als eine an der falschen Zeile.
#
# `kba_referenz` wird an mehreren Stellen auf None gesetzt. Grundlage ist §9 des
# Auftrags: eine KBA-Referenz darf nur gespeichert werden, wenn sie exakt amtlich
# bestaetigt ist. Formatplausibel ist NICHT dasselbe wie inhaltlich belegt.

RECALL_KORREKTUREN: tuple[tuple, ...] = (

    # ══════════════════════════════════════════════════════════════════════
    # BMW 3er G20/G21
    # ══════════════════════════════════════════════════════════════════════

    (11, "bmw-3er-g20-g21",
     {"kba_referenz": "009696"},
     {"kba_referenz": None},
     "Unbelegte KBA-Referenz entfernt. 009696 ist in der Datenbank zusaetzlich "
     "an einem Audi A1 GB vergeben; eine amtliche Aktionsnummer ist je Aktion "
     "eindeutig. Der Rueckrufinhalt bleibt als Baureihen-Hinweis erhalten."),

    (12, "bmw-3er-g20-g21",
     {"kba_referenz": "010000"},
     {"kba_referenz": None},
     "Unbelegte KBA-Referenz entfernt. 010000 ist markenuebergreifend auch an "
     "einem Mercedes W213 und einem Audi Q5 II vergeben. Der Mangeltext bleibt "
     "unveraendert — siehe Verifikationsnotiz zur bewusst NICHT vorgenommenen "
     "Umschreibung auf den Spurstangen-Rueckruf."),

    (13, "bmw-3er-g20-g21",
     {"betroffene_baujahre": "2019-2020 (Plug-in-Hybrid)",
      "mangel": "Brandgefahr der Hochvoltbatterie",
      "abhilfe": "Prüfung und ggf. Austausch der Hochvoltbatterie-Module",
      "kba_referenz": "010078"},
     {"betroffene_baujahre": "2020 (Plug-in-Hybrid)",
      "mangel": "Verunreinigungen (Fremdpartikel) in Zellen des Hochvoltspeichers "
                "können einen Kurzschluss auslösen — dadurch Brandgefahr.",
      "abhilfe": "Prüfung des Hochvoltspeichers, bei Bedarf Austausch der "
                 "betroffenen Zellmodule.",
      "kba_referenz": None},
     "Auf den amtlichen Datensatz (NHTSA 20V-601) verengt und praezisiert: der "
     "Bauzeitraum wird von '2019-2020' auf '2020' zurueckgenommen, weil das "
     "belegte Produktionsfenster im Januar 2020 beginnt. Fehlerbild und Abhilfe "
     "folgen jetzt der amtlichen Formulierung. Die unbelegte Referenz 010078 "
     "entfaellt: fuer diese Massnahme ist keine KBA-Nummer auffindbar."),

    # ══════════════════════════════════════════════════════════════════════
    # Opel Insignia B
    # ══════════════════════════════════════════════════════════════════════

    (543, "opel-insignia-b",
     {"kba_referenz": "7698"},
     {"kba_referenz": None},
     "Unbelegte KBA-Referenz entfernt (§9). 7698 kommt in der Datenbank nur an "
     "dieser Zeile vor — die Entfernung wirkt sich auf keine andere Baureihe aus."),

    (544, "opel-insignia-b",
     {"datum": "2018-08",
      "betroffene_baujahre": "2017-2018",
      "mangel": "Fehlerhafte Befestigung des Bremspedals kann zum Lösen des Pedals führen.",
      "abhilfe": "Überprüfung und ggf. Nachbesserung der Befestigung des Bremspedals.",
      "kba_referenz": "7900"},
     {"datum": "2021-06",
      "betroffene_baujahre": "2021",
      "mangel": "Die Pedalplatte des Bremspedals kann sich vom Pedalträger lösen.",
      "abhilfe": "Prüfung des Bremspedals anhand der Seriennummer, bei Bedarf "
                 "Austausch der Pedalbaugruppe.",
      "kba_referenz": None},
     "Identitaet steht fest (Modell + identisches Fehlerbild), nur die Randfelder "
     "waren falsch: die reale Aktion E212103161 (21-C-077) wurde am 10.06.2021 "
     "veroeffentlicht und betrifft die Produktion Februar bis Mai 2021, nicht "
     "2017-2018. Datum und Bauzeitraum um drei Jahre korrigiert. Die Referenz "
     "7900 entfaellt: sie ist kein amtlicher Wert (und war zusaetzlich an einem "
     "BMW 5er G30 vergeben); der Herstellercode steht in der Verifikation."),

    (545, "opel-insignia-b",
     {"kba_referenz": "8064"},
     {"kba_referenz": None},
     "Unbelegte KBA-Referenz entfernt (§9). 8064 kommt nur an dieser Zeile vor."),

    # ── §4 des Auftrags: der bekannte Insignia-Fall 011422 ────────────────
    (546, "opel-insignia-b",
     {"datum": "2020-07",
      "betroffene_baujahre": "2019-2020",
      "mangel": "Fehlerhafte Software im Motorsteuergerät kann zu erhöhten NOx-Emissionen führen.",
      "abhilfe": "Software-Update für das Motorsteuergerät.",
      "kba_referenz": "9600"},
     {"datum": "2022-02",
      "betroffene_baujahre": "2017-2018 (1,6 l Diesel Euro 6)",
      "mangel": "Unzulässige Abschalteinrichtung: erhöhte NOx-Emissionen im realen "
                "Fahrbetrieb (vom KBA angeordneter Rückruf).",
      "abhilfe": "Software-Update des Motorsteuergeräts.",
      "kba_referenz": "011422"},
     "Der einzige Fall des Piloten, in dem eine amtliche Aktion vollstaendig "
     "belegt ist. Alle vier pruefbaren Felder waren falsch und wurden auf den "
     "amtlichen Stand gebracht: Datum 2020-07 -> 2022-02 (KBA-Veroeffentlichung "
     "17.02.2022), Referenz 9600 -> 011422, Bauzeitraum 2019-2020 -> 2017-2018, "
     "Mangeltext auf die amtliche Aussage. Der Bauzeitraum ist die Schnittmenge "
     "des amtlichen Fensters 2013-2018 mit dem Bauzeitraum des Insignia B (ab "
     "2017) — die Zeile behauptet damit nicht mehr, als die Quelle hergibt. Der "
     "Klammerzusatz grenzt die Aktion zusaetzlich auf Diesel ein, damit sie bei "
     "Benzinern gar nicht erst erscheint. KEINE Dublette: die vorhandene Zeile "
     "wurde korrigiert, keine zweite daneben angelegt."),

    (547, "opel-insignia-b",
     {"kba_referenz": "10000"},
     {"kba_referenz": None},
     "Unbelegte KBA-Referenz entfernt (§9). 10000 war zusaetzlich an einer "
     "Mercedes S-Klasse W222 vergeben."),

    # ══════════════════════════════════════════════════════════════════════
    # Audi A3 Typ 8P
    # ══════════════════════════════════════════════════════════════════════
    #
    # Beide Korrekturen betreffen ausschliesslich die APPLICABILITY, nicht die
    # Rueckrufaussage: die Zeilen grenzen sich im Mangeltext laengst auf eine
    # Motorisierung ein ("2.0 TDI", "1.4 TFSI"), aber app/recall_filter.py liest
    # als Antriebs-Qualifier nur einen Klammerzusatz in `betroffene_baujahre` und
    # kennt als Kraftstoffwoerter nur "Diesel"/"Benzin" — "TDI"/"TFSI" loest es
    # nicht auf. Ergebnis vor der Korrektur: ein ausdruecklich auf den 2.0 TDI
    # begrenzter Rueckruf wurde fuer das Testfahrzeug 2.0 FSI (Benziner) als
    # moeglicherweise zutreffend angezeigt. Der Klammerzusatz macht die bereits
    # im Text stehende Einschraenkung fuer die BESTEHENDE Semantik lesbar; es
    # wird keine neue Kategorie und keine neue Regel eingefuehrt.

    (283, "audi-a3-typ-8p",
     {"betroffene_baujahre": "2008-2011"},
     {"betroffene_baujahre": "2008-2011 (2.0 TDI Diesel)"},
     "Motorbezug maschinenlesbar gemacht. Der Rueckruf nennt im Mangeltext "
     "ausdruecklich den 2.0 TDI, wurde aber fuer Benziner derselben Baureihe "
     "angezeigt. Nach der Korrektur faellt er bei erkanntem Benzinmotor auf "
     "'incompatible' und verschwindet vollstaendig aus Findings und Prompt."),

    (284, "audi-a3-typ-8p",
     {"betroffene_baujahre": "2009-2012"},
     {"betroffene_baujahre": "2009-2012 (1.4 TFSI Benzin)"},
     "Dieselbe Korrektur in die andere Richtung: der Rueckruf nennt den 1.4 TFSI "
     "und darf bei Dieselfahrzeugen des A3 8P nicht mehr erscheinen. BEKANNTE "
     "GRENZE: eine Eingrenzung auf genau den 1.4-l-Motor ist mit der vorhandenen "
     "Semantik (nur Kraftstoff/Antrieb) nicht moeglich — bei anderen Benzinern "
     "des 8P bleibt die Zeile sichtbar."),

    # ══════════════════════════════════════════════════════════════════════
    # Mercedes-Benz C-Klasse W205
    # ══════════════════════════════════════════════════════════════════════

    (121, "mercedes-benz-c-klasse-w205",
     {"kba_referenz": "8789"},
     {"kba_referenz": None},
     "Unbelegte KBA-Referenz entfernt (§9). 8789 ist markenuebergreifend auch an "
     "BMW, Audi und Toyota vergeben."),

    (122, "mercedes-benz-c-klasse-w205",
     {"kba_referenz": "9201"},
     {"kba_referenz": None},
     "Unbelegte KBA-Referenz entfernt (§9). 9201 kommt nur an dieser Zeile vor."),

    (123, "mercedes-benz-c-klasse-w205",
     {"kba_referenz": "9876"},
     {"kba_referenz": None},
     "Unbelegte KBA-Referenz entfernt (§9). 9876 kommt nur an dieser Zeile vor. "
     "Datum und Bauzeitraum bleiben bewusst unveraendert — siehe Verifikationsnotiz."),
)


# ─────────────────────────────────────────────────────────────────────────────
# 2) FAKT-VERIFIKATIONEN
# ─────────────────────────────────────────────────────────────────────────────
#
# (fakt_art, fakt_id, status, quelle, stufe, url, referenz, notiz)
#
# Die Fingerprints werden von der Migration NACH den Datenkorrekturen gebildet,
# damit kein stale-verification-Eintrag entsteht.

RECALL_VERIFIKATIONEN: tuple[tuple, ...] = (

    # ══════════════════════════════════════════════════════════════════════
    # BMW 3er G20/G21 — Testfahrzeug 320d, Baujahr 2020
    # ══════════════════════════════════════════════════════════════════════

    ("rueckruf", 11, "unverified",
     "Recherche ohne Treffer: KBA-Rueckrufdatenbank (Bulk-Export HTTP 503, "
     "interaktive Suche captcha-gesichert), KBA-Presseachiv, autoservicepraxis.de, "
     "bimmertoday.de, autozeitung.de", "B",
     None, None,
     "KEIN Beleg fuer einen Bremskraftverstaerker-Rueckruf des G20/G21 im Maerz "
     "2020. WICHTIG: die bisher hier hinterlegte Quelle (autoservicepraxis.de, "
     "Artikel 2514430) belegt eine voellig ANDERE Aktion — veroeffentlicht am "
     "01.10.2013, Nockenwelle/Vakuumpumpe bei N20-Vierzylindern (125i, 320i, 328i, "
     "520i, 528i, X1, X3, Z4), Produktion 06/2012 bis 08/2013, rund 6.800 "
     "Fahrzeuge in Deutschland. Andere Fahrzeuggeneration, anderes Bauteil, "
     "sieben Jahre frueher. Die frueher vergebene Einstufung 'partially_verified' "
     "beruhte damit auf einer Fehlzitation und wird zurueckgenommen. Der Fakt "
     "bleibt sichtbar, traegt aber keine Vertrauensstufe. 'Nicht gefunden' ist "
     "keine Widerlegung — deshalb 'unverified' und nicht 'rejected'."),

    ("rueckruf", 12, "unverified",
     "Recherche ohne Treffer fuer das behauptete Fehlerbild: KBA-Rueckrufdatenbank "
     "(nicht abfragbar), bimmertoday.de, autozeitung.de, auto-motor-und-sport.de", "B",
     "https://www.bimmertoday.de/2020/08/14/ruckruf-fur-bmw-3er-z4-x3-m-x4-m-spurstange-kann-brechen/",
     None,
     "Im selben Monat existiert ein realer, KBA-ueberwachter Lenkungs-Rueckruf "
     "fuer BMW 3er G20/G21, Z4 G29, X3 M F97 und X4 M F98: KBA-Referenz 010009, "
     "BMW-Massnahme 0032140300, Spurstange kann im Kerbbereich durch "
     "Materialermuedung brechen, Produktion 09/2018 bis 08/2019, 1.304 Fahrzeuge "
     "in Deutschland, 20.880 weltweit. Das ist ein ANDERES Fehlerbild als die in "
     "der Datenbank behaupteten 'mangelhaften Schweissnaehte an der Lenkung' und "
     "ein anderer Bauzeitraum. Die Zeile wurde deshalb bewusst NICHT in diesen "
     "Rueckruf umgeschrieben: gleiches Teilsystem im gleichen Monat begruendet "
     "keine Identitaet. Die frueher hier vergebene Einstufung "
     "'partially_verified' mit dem Quellentext 'keine belastbare Quelle gefunden' "
     "war in sich widerspruechlich und wird auf 'unverified' korrigiert."),

    ("rueckruf", 13, "verified",
     "NHTSA Safety Recall 20V-601 (amtliche US-Rueckrufdatenbank, Eingang "
     "30.09.2020); fuer den deutschen Markt uebereinstimmend heise autos, "
     "auto-motor-und-sport, electrive.net und autoservicepraxis.de", "C",
     "https://api.nhtsa.gov/recalls/campaignNumber?campaignNumber=20V601000",
     "20V601000",
     "Der amtliche Datensatz bestaetigt Bauteil, Fehlerbild und Abhilfe woertlich: "
     "Fremdpartikel koennen bei der Zellfertigung in Zellen des Hochvoltspeichers "
     "gelangen, daraus kann ein Kurzschluss mit erhoehter Brandgefahr entstehen; "
     "Abhilfe ist Pruefung und noetigenfalls Austausch des Batteriemoduls. Als "
     "betroffenes Modell ist der 330e ausdruecklich genannt — das ist die "
     "Plug-in-Hybrid-Variante des G20. Fuer Deutschland berichten mehrere "
     "unabhaengige Fachmedien dieselbe Massnahme mit Produktionsfenster 20.01. "
     "bis 18.09.2020 und rund 1.800 betroffenen Kundenfahrzeugen. Der "
     "DB-Bauzeitraum wurde von '2019-2020' auf '2020' verengt, damit die Zeile "
     "nicht ueber die Quellenlage hinausgeht. EINE KBA-REFERENZNUMMER IST FUER "
     "DIESE MASSNAHME NICHT AUFFINDBAR; das Feld kba_referenz bleibt deshalb leer "
     "(§9), und die hoechste Ohne-VIN-Stufe 'variant_match' wird bewusst NICHT "
     "erreicht. Der Rueckruf betrifft ausschliesslich Plug-in-Hybride und ist "
     "fuer das Testfahrzeug 320d (Diesel) unveraendert 'incompatible'."),

    # ══════════════════════════════════════════════════════════════════════
    # Opel Insignia B — Testfahrzeug 2.0 Diesel, Baujahr 2019/2020
    # ══════════════════════════════════════════════════════════════════════

    ("rueckruf", 543, "unverified",
     "Recherche ohne Treffer: KBA-Presseachiv Abgasthematik, rueckrufe.net-Archiv "
     "Opel Insignia, auto-motor-und-sport.de, kfz-betrieb.vogel.de, heise autos", "B",
     None, None,
     "Kein amtlicher oder herstellerseitiger Beleg fuer einen Rueckruf des "
     "Insignia B wegen eines Softwarefehlers der elektrischen Servolenkung im "
     "Maerz 2018. Das durchsuchte Rueckruf-Archiv fuehrt fuer den Insignia in "
     "diesem Zeitraum andere Aktionen (Turbolader-Oelzulaufleitung 08/2018, "
     "Airbag-Fehlfunktion 04/2017), aber keine zur Servolenkung. Keine "
     "Widerlegung, nur kein Beleg."),

    ("rueckruf", 544, "partially_verified",
     "kfz-betrieb (Vogel Communications) zur Opel-Rueckrufaktion E212103161 "
     "(21-C-077)", "B",
     "https://www.kfz-betrieb.vogel.de/opel-checkt-beim-insignia-das-bremspedal-a-e987761a7b91be9ffb4d887b821ff84a/",
     "E212103161 (21-C-077)",
     "Fehlerbild und Modell stimmen mit einer realen Opel-Aktion ueberein: "
     "veroeffentlicht am 10.06.2021, rund 4.000 Fahrzeuge in Deutschland und "
     "ueber 5.700 weltweit, Produktionszeitraum Februar bis Mai 2021, die "
     "Pedalplatte kann sich vom Pedaltraeger loesen; Abhilfe ist eine Pruefung "
     "anhand der Seriennummer (rund 15 Minuten) und noetigenfalls der Austausch "
     "der Pedalbaugruppe (rund 3,5 Stunden). Datum und Bauzeitraum der DB-Zeile "
     "waren um drei Jahre falsch und wurden korrigiert. KEIN 'verified', weil "
     "E212103161 (21-C-077) ein HERSTELLERCODE ist und die zugehoerige "
     "KBA-Referenznummer nicht belegt werden konnte — das Feld kba_referenz "
     "bleibt deshalb leer. Fuer das Testfahrzeug (Baujahr 2019) ist der Rueckruf "
     "nach der Korrektur nicht mehr einschlaegig."),

    ("rueckruf", 545, "unverified",
     "Recherche ohne Treffer: heise autos, rueckrufe.net-Archiv Opel Insignia, "
     "kfz-betrieb.vogel.de, auto-motor-und-sport.de", "B",
     "https://www.heise.de/news/Rueckruf-Opel-Insignia-Rost-an-der-hinteren-Spurstange-6360360.html",
     None,
     "Kein Beleg fuer einen Rueckruf wegen Bruchs der hinteren Federbeine am "
     "Insignia B. Belegt ist eine ANDERE Fahrwerksaktion an der Hinterachse: Rost "
     "an der hinteren Spurstange, KBA-Referenz 011476, Opel-Code E202004350 "
     "(20-P-143) OYZ, rund 572.000 Fahrzeuge weltweit — anderes Bauteil, und nach "
     "Quellenlage der Insignia A. Keine Umschreibung, keine Uebernahme der "
     "fremden Referenz."),

    # ── §4 des Auftrags ───────────────────────────────────────────────────
    ("rueckruf", 546, "verified",
     "Vom KBA angeordneter Rueckruf 011422, Opel-Herstellercode E222115640 "
     "(22-C-013) O7A, veroeffentlicht 17.02.2022; KBA-Presseinformation zur "
     "Opel-Abgasthematik", "A",
     "https://www.kba.de/DE/Presse/Archiv/Abgasthematik/opel_inhalt.html",
     "011422",
     "Amtlich vollstaendig belegt. Das KBA hat den Rueckruf am 17.02.2022 unter "
     "der Referenznummer 011422 veroeffentlicht; Opel fuehrt ihn intern als "
     "E222115640 (22-C-013) O7A. Betroffen sind Astra, Corsa und Insignia mit "
     "1,3-l- und 1,6-l-Dieselmotoren der Abgasnorm Euro 6 mit AGR und "
     "NOx-Speicherkatalysator (NSK/LNT), Baujahre 2013 bis 2018; rund 75.000 "
     "Fahrzeuge in Deutschland, ueber 400.000 weltweit. Grund ist eine "
     "unzulaessige Abschalteinrichtung, Abhilfe ein Software-Update des "
     "Motorsteuergeraets. Da das KBA den Rueckruf ueberwacht, ist er verbindlich "
     "— bei Nichtdurchfuehrung droht die Stilllegung. Der Bauzeitraum der Zeile "
     "wurde auf 2017-2018 verengt: das ist die Schnittmenge des amtlichen "
     "Fensters mit dem Bauzeitraum des Insignia B. VERBLEIBENDE UNSICHERHEIT, "
     "bewusst festgehalten: das KBA benennt die Insignia-GENERATION nicht, und ob "
     "jeder Insignia B mit 1,6-l-Diesel den NOx-Speicherkatalysator statt eines "
     "SCR-Systems traegt, liess sich nicht abschliessend klaeren. Die konkrete "
     "Betroffenheit bleibt deshalb eine FIN-Frage — genau so wird sie dem Nutzer "
     "auch angezeigt. Das Testfahrzeug (2.0 Diesel, Baujahr 2019/2020) liegt "
     "ausserhalb des belegten Fensters und ist nicht betroffen."),

    ("rueckruf", 547, "unverified",
     "Recherche ohne Treffer: rueckrufe.net-Archiv Opel Insignia, "
     "kfz-betrieb.vogel.de, auto-motor-und-sport.de", "B",
     None, None,
     "Kein Beleg fuer einen Rueckruf wegen Kurzschlusses im Heizsystem der "
     "Vordersitze am Insignia B. Belegt ist eine andere Kurzschlussaktion: "
     "Wassereintritt in das Schaltrelais der Heckklappensteuerung, 3.548 "
     "Fahrzeuge der Modelljahre 2009 bis 2012 — Insignia A, anderes Bauteil, "
     "anderer Zeitraum. Keine Umschreibung."),

    # ══════════════════════════════════════════════════════════════════════
    # Audi A3 Typ 8P — Testfahrzeug 2.0 FSI 150 PS, Baujahr 2008
    # ══════════════════════════════════════════════════════════════════════

    ("rueckruf", 282, "unverified",
     "Recherche ohne Treffer: KBA-Rueckrufdatenbank (nicht abfragbar), "
     "rueden.de-Uebersicht Audi-Rueckrufe, autozeitung.de, "
     "auto-motor-und-sport.de, carwiki.de", "B",
     None, None,
     "Kein amtlicher oder herstellerseitiger Beleg fuer einen Rueckruf des A3 8P "
     "wegen Federbruchs im Bremspedal mit Ausfall der Bremsleuchten (September "
     "2010). Die Zeile traegt keine KBA-Referenz und bleibt inhaltlich "
     "unveraendert. Keine Widerlegung, nur kein Beleg."),

    ("rueckruf", 283, "unverified",
     "Recherche ohne Treffer fuer das behauptete Fehlerbild: rueden.de-Uebersicht "
     "Audi-Rueckrufe (EA189), auto-motor-und-sport.de, autozeitung.de", "B",
     None, None,
     "Fuer den A3 8P ist der grosse, amtlich angeordnete "
     "Motorsteuergeraete-Rueckruf der EA189-Diesel belegt (1.6 und 2.0 TDI, "
     "Baujahre 2008 bis 2015, Herstellercode 23Q7). Er betrifft jedoch "
     "unzulaessige Abschalteinrichtungen im Abgasskandal, wurde ab 2015/2016 "
     "durchgefuehrt und hat als Fehlerbild NICHT 'unrunder Motorlauf oder "
     "Leistungsverlust'. Die DB-Zeile wird deshalb nicht in den EA189-Rueckruf "
     "umgeschrieben. UNABHAENGIG davon wurde ein Applicability-Fehler behoben: "
     "die Zeile grenzt sich im Text ausdruecklich auf den 2.0 TDI ein, wurde aber "
     "fuer das Testfahrzeug 2.0 FSI (Benziner) als moeglicherweise zutreffend "
     "angezeigt, weil der Antriebs-Abgleich in app/recall_filter.py nur einen "
     "Klammerzusatz auswertet und die Kuerzel 'TDI'/'TFSI' nicht aufloest. Der "
     "ergaenzte Klammerzusatz macht die Einschraenkung maschinenlesbar."),

    ("rueckruf", 284, "unverified",
     "Recherche ohne Treffer: rueden.de-Uebersicht Audi-Rueckrufe, carwiki.de, "
     "gebrauchtwagenberater.de", "B",
     None, None,
     "Kein Beleg fuer einen Rueckruf des A3 8P wegen Risses im Ladeluftkuehler "
     "des 1.4 TFSI (Mai 2012). Wie bei Zeile 283 wurde nur die Applicability "
     "geschaerft: der Klammerzusatz '(1.4 TFSI Benzin)' verhindert, dass die "
     "Zeile bei Dieselfahrzeugen des 8P erscheint. Bekannte Grenze: eine "
     "Eingrenzung auf genau den 1.4-l-Motor ist mit der vorhandenen Semantik "
     "nicht moeglich."),

    # ══════════════════════════════════════════════════════════════════════
    # Mercedes-Benz C-Klasse W205 — Testfahrzeug C220d, Baujahr 2016
    # ══════════════════════════════════════════════════════════════════════

    ("rueckruf", 121, "unverified",
     "Recherche ohne Treffer fuer das behauptete Fehlerbild: KBA-Rueckrufdatenbank "
     "(nicht abfragbar), Stiftung Warentest (test.de), autoservicepraxis.de, "
     "mbpassion.de", "B",
     None, None,
     "Kein Beleg fuer einen Rueckruf wegen fehlerhafter BEFESTIGUNG des "
     "Airbag-Steuergeraets an der C-Klasse W205. Belegt sind zwei ANDERE "
     "Airbag-Aktionen: (1) KBA-Referenz 6918, veroeffentlicht 21.03.2017, "
     "fehlerhafte CODIERUNG von Steuergeraeten, C-Klasse und weitere Baureihen "
     "der Baujahre 2003 bis 2016 — anderes Fehlerbild, weit groesserer Zuschnitt; "
     "(2) Sitzbelegungserkennung, veroeffentlicht 10.07.2015, Produktion "
     "02.08.2013 bis 22.04.2014, eine nicht ordnungsgemaess montierte "
     "Steuereinheit der Sitzheizung stoert die Insassenerkennung — anderes "
     "Bauteil, anderer Zeitraum. Keine der beiden traegt die DB-Aussage; keine "
     "Umschreibung. Die frueher hier vergebene Einstufung 'partially_verified' "
     "mit dem Quellentext 'keine belastbare Quelle gefunden' war in sich "
     "widerspruechlich und wird auf 'unverified' korrigiert."),

    ("rueckruf", 122, "unverified",
     "Recherche ohne Treffer: autoservicepraxis.de, auto-motor-und-sport.de, "
     "mbpassion.de, rueckrufe.net", "B",
     None, None,
     "Kein Beleg fuer einen Rueckruf wegen Undichtigkeit im Bereich des "
     "Turboladers an der C-Klasse W205 (Mai 2018). Belegt sind fuer die Baureihe "
     "205 andere Aktionen, unter anderem Kabelbaumschaeden im Lenkungssteuergeraet "
     "(Baureihen 205/253/293, Produktion Dezember 2019 bis Mai 2020, "
     "Mercedes-Code 5491022, rund 10.122 Fahrzeuge in Deutschland) — anderes "
     "Bauteil, anderer Zeitraum. Keine Widerlegung, nur kein Beleg."),

    ("rueckruf", 123, "partially_verified",
     "autoservicepraxis.de und mbpassion.de zur Mercedes-Rueckrufaktion "
     "eCall-/Kommunikationsmodul", "B",
     "https://www.autoservicepraxis.de/rueckrufe/artikel/mercedes-benz-ecall-arbeitet-nicht-richtig-2513085",
     None,
     "Das Rueckrufthema ist real und die C-Klasse (Baureihe 205) ist ausdruecklich "
     "betroffen: veroeffentlicht am 21.01.2019, Software des Kommunikationsmoduls "
     "fuer das eCall-Notrufsystem, Produktionszeitraum Maerz 2017 bis September "
     "2018. Automatischer und manueller Notruf koennen ausfallen, wenn "
     "Mercedes-me-Dienste vor der Auslieferung im werksseitigen Transportmodus "
     "aktiviert wurden; Abhilfe ist ein Software-Update, das ueber die "
     "Mobilfunkverbindung eingespielt wird. Der Zuschnitt der DB-Zeile passt "
     "NICHT: sie datiert die Aktion auf Juli 2020 und nennt die Baujahre "
     "2019-2020, also ausserhalb des belegten Produktionsfensters. Deshalb nur "
     "'partially_verified' — Thema belegt, Zeitraum nicht. Datum und Baujahre "
     "wurden bewusst NICHT umgeschrieben: es ist offen, ob die Zeile eine andere, "
     "spaetere eCall-Aktion meint (belegt ist zusaetzlich eine Aktion zur "
     "Dachbedieneinheit mit demselben Symptom). Fuer das Testfahrzeug (Baujahr "
     "2016) ist die Zeile ohnehin nicht einschlaegig."),
)


def _selbsttest() -> None:
    """Formale Konsistenz der kuratierten Daten (wird vom Test aufgerufen)."""
    from app.fakt_verifikation import FAKT_ARTEN, STATUS_WERTE, QUELLENSTUFEN

    ids_korrektur = [e[0] for e in RECALL_KORREKTUREN]
    assert len(ids_korrektur) == len(set(ids_korrektur)), \
        "doppelte Korrektur fuer dieselbe Fakt-ID"

    for fakt_id, baureihe_id, erwartet, neu, begruendung in RECALL_KORREKTUREN:
        assert isinstance(fakt_id, int) and fakt_id > 0
        assert baureihe_id and isinstance(baureihe_id, str)
        assert erwartet and neu, f"leere Korrektur fuer #{fakt_id}"
        assert set(neu) == set(erwartet), \
            f"#{fakt_id}: erwartet/neu muessen dieselben Spalten nennen"
        assert erwartet != neu, f"#{fakt_id}: Korrektur ohne Wirkung"
        assert len(begruendung or "") >= 40, f"#{fakt_id}: Begruendung zu duenn"

    paare = [(e[0], e[1]) for e in RECALL_VERIFIKATIONEN]
    assert len(paare) == len(set(paare)), "doppelte Verifikation fuer denselben Fakt"

    for fakt_art, fakt_id, status, quelle, stufe, url, referenz, notiz in RECALL_VERIFIKATIONEN:
        assert fakt_art in FAKT_ARTEN, fakt_art
        assert fakt_art == "rueckruf", "Recall-Pilot: ausschliesslich Rueckrufe"
        assert status in STATUS_WERTE, f"#{fakt_id}: unbekannter Status {status!r}"
        assert stufe in QUELLENSTUFEN, f"#{fakt_id}: unbekannte Quellenstufe {stufe!r}"
        assert quelle and len(quelle) >= 20, f"#{fakt_id}: Quelle zu duenn"
        assert len(notiz or "") >= 80, f"#{fakt_id}: Notiz zu duenn"
        # §9: eine Referenz darf nur an einem belegten Fakt haengen.
        if status == "unverified":
            assert referenz is None, \
                f"#{fakt_id}: 'unverified' darf keine amtliche Referenz tragen"
