from __future__ import annotations

"""
Kuratierte Verifikationen der Pilotphase (Verification-Pilot).

WAS HIER STEHT
--------------
Das Ergebnis einer HANDGEPRUEFTEN Recherche zu vier Pilotfahrzeugen. Jeder
Eintrag ordnet EINEN konkreten Datenbankfakt genau EINER Quelle zu und haelt
fest, wie weit diese Quelle die Aussage traegt.

WAS HIER AUSDRUECKLICH NICHT PASSIERT
-------------------------------------
Kein Webtreffer setzt automatisch einen Fakt auf `verified`. Jede Zuordnung
unten ist einzeln begruendet; die Begruendung steht als `notiz` mit in der
Datenbank und ist nach einem Neustart nachvollziehbar. Wo die Quellenlage die
Aussage nur teilweise traegt, steht `partially_verified` — dieser Status bleibt
`unverified_db` und traegt keinen Floor.

KLASSIFIKATIONSREGEL (einheitlich angewandt)
--------------------------------------------
``verified``            Kernaussage (Bauteil + Fehlerbild + Zuordnung zu genau
                        dieser Generation) durch mindestens eine Stufe-A/B-Quelle
                        oder mehrere unabhaengige Stufe-C-Quellen bestaetigt, und
                        nichts in der DB-Aussage geht darueber hinaus.
``partially_verified``  Thema bestaetigt, aber der Zuschnitt in der Datenbank ist
                        weiter als die Quellenlage (zusaetzliche Motorisierung,
                        zu weiter Baujahresbereich, abweichendes Bauteil) — oder
                        es fehlt eine Quelle der Stufen A/B.
``rejected``            Die beste verfuegbare Quelle widerspricht der Aussage.
                        Die Zeile wird hier NICHT geloescht: Datenkorrekturen
                        laufen ueber die Cleanup-Migration, nicht ueber die
                        Verifikation.

FAKT-IDs
--------
Die IDs stammen aus dem kanonischen Seed (`db/seed_fahrzeugdaten.sql`) und sind
dort explizit ausgeschrieben, also ueber frische Installationen hinweg stabil.
Zusaetzlich sichert der Fingerprint in `app/fakt_verifikation.py` gegen stille
Fehlzuordnung ab, falls eine Zeile spaeter neu geschrieben wird.
"""

GEPRUEFT_AM = "2026-08-25"

# (fakt_art, fakt_id, status, quelle, stufe, url, referenz, notiz)
PILOT_VERIFIKATIONEN: tuple[tuple, ...] = (

    # ══════════════════════════════════════════════════════════════════════
    # BMW 3er G20/G21 — Testmotorisierung 320d, Baujahr 2020
    # ══════════════════════════════════════════════════════════════════════
    ("schwachstelle_baureihe", 14, "verified",
     "AUTO BILD TUEV-Check BMW 3er/4er; gebrauchtwagenberater.de (G20/G21)", "B",
     "https://www.autobild.de/artikel/bmw-3er-4er-im-tuev-check-2026--28158815.html",
     None,
     "Software-/Infotainment-Schwaeche der fruehen G20 bestaetigt: iDrive 7 gilt bis "
     "Modelljahr 2022 als fehleranfaellig, erst das LCI mit iDrive 8 als stabil. Der "
     "DB-Baujahresbereich 2019-2020 liegt vollstaendig innerhalb des belegten "
     "Zeitfensters, und die DB-Formulierung ('vereinzelt', 'meist durch Updates "
     "behoben') geht nicht ueber die Quelle hinaus."),

    ("schwachstelle_baureihe", 15, "rejected",
     "AUTO BILD TUEV-Check BMW 3er/4er (TUEV-Report-Auswertung)", "B",
     "https://www.autobild.de/artikel/bmw-3er-4er-im-tuev-check-2026--28158815.html",
     None,
     "WIDERSPRUCH: Die DB behauptet ueberdurchschnittlichen Bremsenverschleiss als "
     "Baureihen-Schwachstelle. Die TUEV-Auswertung stellt fuer den G20 das Gegenteil "
     "fest — Bremsscheiben aller drei Generationen zeigen relativ wenig Verschleiss, "
     "die Fussbremsfunktion ist beanstandungsfrei, Bremsleitungen und -schlaeuche "
     "sind unauffaellig. Bleibt als Hinweis erhalten, aber ohne Vertrauensstufe; "
     "eine Datenkorrektur waere Sache der Cleanup-Migration."),

    ("schwachstelle_baureihe", 16, "partially_verified",
     "AUTO BILD TUEV-Check BMW 3er/4er; gebrauchtwagenberater.de (G20/G21)", "B",
     "https://www.autobild.de/artikel/bmw-3er-4er-im-tuev-check-2026--28158815.html",
     None,
     "Klapper-/Knarzgeraeusche sind fuer den G20 belegt — aber am FAHRWERK (auffaellig "
     "ab ca. 80.000 km, 400-900 EUR), nicht im Innenraum. Fuer Armaturenbrett und "
     "Tuerverkleidungen liess sich keine Quelle finden. Thema plausibel, Bauteil-"
     "zuordnung der DB nicht belegt."),

    ("rueckruf", 11, "partially_verified",
     "autoservicepraxis.de / kfz-betrieb.vogel.de (BMW-Aktion Bremskraftverstaerker)", "B",
     "https://www.autoservicepraxis.de/rueckrufe/artikel/"
     "bmw-rueckruf-bremskraftverstaerker-kann-ausfallen-2514430",
     None,
     "Eine BMW-Aktion zum Bremskraftverstaerker ist real. Die DB datiert sie auf "
     "2020-03; die auffindbaren Quellen ordnen die Bremskraftverstaerker-Aktion dem "
     "Jahr 2021 zu, waehrend fuer 2020 eine Aktion zu Gurtschlosssensorik/Airbag-"
     "Steuergeraet dokumentiert ist. Die hinterlegte Referenz '009696' liess sich "
     "gegen keine amtliche Quelle bestaetigen und taucht im Bestand zusaetzlich bei "
     "einem anderen BMW-Modell mit abweichendem Mangeltext auf. Thema ja, Datum und "
     "Referenz nein."),

    ("rueckruf", 12, "partially_verified",
     "keine belastbare Quelle gefunden (Recherche AUTO BILD / autoservicepraxis / KBA)", "B",
     None, None,
     "Zu einem G20-Rueckruf wegen mangelhafter Schweissnaehte an der Lenkung im "
     "August 2020 liess sich keine Quelle finden. Die hinterlegte Referenz '010000' "
     "ist ein glatter Rundwert und im Bestand mehrfach vergeben. Bleibt als "
     "konservativer Rueckrufhinweis erhalten, ohne Vertrauensstufe und ohne "
     "angezeigte Nummer."),

    # ══════════════════════════════════════════════════════════════════════
    # Opel Insignia B — Testmotorisierung 2.0 Diesel 174 PS (F20DVH), Bj. 2019
    # ══════════════════════════════════════════════════════════════════════
    ("schwachstelle_baureihe", 1007, "partially_verified",
     "KBA-Rueckrufanordnung Opel Diesel (Astra/Corsa/Insignia)", "A",
     "https://www.kba.de/DE/Presse/Archiv/Abgasthematik/opel_inhalt.html",
     "011422",
     "Die Abgas-/NOx-Thematik bei Opel-Dieselmotoren ist amtlich belegt (KBA-"
     "Rueckrufanordnung, Referenz 011422, Herstellercode E222115640 / 22-C-013). "
     "Die DB-Zeile mischt jedoch mehrere Aussagen in einem Satz: AdBlue-System, "
     "NOx-Sensoren, erhoehter Oelverbrauch UND Steuerkette. Belegt ist davon nur "
     "der Abgas-/Softwareteil; fuer Oelverbrauch und Steuerkette am 2.0 CDTI wurde "
     "keine Quelle gefunden. Deshalb nur teilweise verifiziert."),

    ("rueckruf", 546, "partially_verified",
     "KBA-Rueckrufanordnung Opel Diesel (Astra/Corsa/Insignia), Februar 2022", "A",
     "https://www.kba.de/DE/Presse/Archiv/Abgasthematik/opel_inhalt.html",
     "011422",
     "Ein amtlicher KBA-Rueckruf wegen NOx-Software fuer den Insignia existiert — "
     "aber mit anderen Eckdaten als in der DB: real Februar 2022 unter KBA-Referenz "
     "011422 (Herstellercode E222115640 / 22-C-013), die DB nennt 2020-07 und die "
     "Referenz '9600'. Ein frueherer Opel-Abgasrueckruf von 2018 (Aktionscode "
     "E152025000 / 17-R-021) betraf den Insignia A der Baujahre 2013-2016, also die "
     "Vorgaengergeneration. Thema und Baureihe bestaetigt, Datum und Referenz nicht — "
     "die Nummer darf deshalb nicht als amtlich angezeigt werden."),

    # ══════════════════════════════════════════════════════════════════════
    # Audi A3 8P — Testmotorisierung 2.0 FSI 150 PS (AXW/BMB/BLR/BVY), Bj. 2008
    # ══════════════════════════════════════════════════════════════════════
    ("schwachstelle_baureihe", 543, "partially_verified",
     "KBA (Druckminderer/ZMS-Aktion) via carwiki.de; autozeitung.de Kaufberatung A3 8P", "B",
     "https://carwiki.de/audi-a3-probleme", None,
     "Vorzeitiger ZMS-Verschleiss am A3 8P ist belegt, die dokumentierte KBA-Aktion "
     "zu einem falsch ausgelegten Druckminderer betrifft jedoch die Baujahre "
     "2003-2005. Die DB spannt den Bereich auf 'Alle Baujahre' auf und geht damit "
     "ueber die Quellenlage hinaus. Fuer den 2.0 FSI mit 150 PS ist die Einordnung "
     "'leistungsstaerkere Benziner' zudem grenzwertig."),

    ("schwachstelle_baureihe", 544, "partially_verified",
     "carwiki.de (A3-Motorenuebersicht EA111); autodoc.de Technikblog A3", "B",
     "https://carwiki.de/audi-a3-probleme", None,
     "Die Steuerkettenproblematik ist belegt — aber fuer die EA111-Benziner "
     "(1.2 TSI, 1.4 TSI, 1.6 FSI, 1.8 TFSI) der Baujahre 2007-2013. Die DB nennt "
     "stattdessen '1.4 TFSI, 1.8 TFSI, 2.0 TFSI': der 2.0 TFSI (EA113) gehoert nicht "
     "zu dieser Familie, der betroffene 1.6 FSI fehlt. Motorliste teilweise falsch, "
     "Kernaussage richtig."),

    ("schwachstelle_baureihe", 545, "verified",
     "gebrauchtwagenberater.de (A3 8P); autozeitung.de Kaufberatung; autodoc.de", "C",
     "https://gebrauchtwagenberater.de/audi-a3-8p-schwachstellen-kaufberatung/", None,
     "Von drei unabhaengigen Fachquellen uebereinstimmend beschrieben: Anfahr-"
     "schwaechen und unwillige Gangwechsel am DSG/S tronic, mit ausdruecklichem "
     "Hinweis auf den turnusmaessigen Getriebeoelwechsel laut Serviceplan. Das deckt "
     "die DB-Aussage (Mechatronik/Kupplungen, Schaltprobleme, Ruckeln, 'insbesondere "
     "bei mangelnder Wartung') vollstaendig ab, ohne dass die DB darueber hinausgeht."),

    ("schwachstelle_baureihe", 547, "partially_verified",
     "AUTO BILD TUEV-Check Audi A3; autozeitung.de Kaufberatung A3 8P", "B",
     "https://www.autobild.de/artikel/audi-a3-im-tuev-check-2026--28123347.html", None,
     "Keine der ausgewerteten Quellen nennt Fensterheber als typische A3-8P-Schwaeche; "
     "die TUEV-Auswertung stellt den A3 insgesamt unter den Maengeldurchschnitt und "
     "hebt stattdessen Fahrwerk (gebrochene Federn, undichte Stossdaempfer) und "
     "Bremsanlage hervor. Kein Widerspruch, aber auch kein Beleg."),

    ("schwachstelle_baureihe", 548, "partially_verified",
     "AUTO BILD TUEV-Check Audi A3; autoscout24.de Kaufberatung A3 8P", "B",
     "https://www.autobild.de/artikel/audi-a3-im-tuev-check-2026--28123347.html", None,
     "Rost an Heckklappe/Kotfluegeln wird in den ausgewerteten Quellen nicht als "
     "typische Schwaeche des A3 8P gefuehrt. Die DB-Formulierung ist bereits "
     "vorsichtig ('vereinzelt'), bleibt aber unbelegt."),

    ("schwachstelle_motor", 1289, "partially_verified",
     "autodoc.de Teilekatalog (Steuerkette Audi A3 8P 2.0 FSI 150 PS); "
     "EOS-Forum und MOTOR-TALK Reparaturberichte zu AXW/BLR", "C",
     "https://www.autodoc.de/autoteile/steuerkette-10511/audi/a3/a3-sportback-8pa/18069-2-0-fsi",
     None,
     "Fehlerbild bestaetigt: Kettenlaengung und Verschleiss am Nockenwellenversteller, "
     "Rasseln beim Start bzw. bis ca. 2.000/min, typischerweise ab rund 150.000 km; "
     "die Motorcodes AXW und BLR werden dabei ausdruecklich genannt. Es fehlt "
     "allerdings eine Quelle der Stufe A oder B (Hersteller, ADAC, TUEV) zur "
     "Haeufigkeit — ein Teilekatalog belegt die Existenz des Bauteils, nicht die "
     "Ausfallrate. Deshalb bewusst nur teilweise verifiziert."),

    ("schwachstelle_motor", 1290, "partially_verified",
     "keine belastbare Einzelquelle fuer diese Motorisierung gefunden", "C",
     None, None,
     "Verkokung an Einlassventilen/Einspritzung ist bei Direkteinspritzern allgemein "
     "bekannt, es liess sich jedoch keine Quelle finden, die das speziell fuer den "
     "2.0 FSI (AXW/BLR) belegt. Bleibt als Pruefhinweis erhalten, ohne "
     "Vertrauensstufe."),

    ("kritische_wartung", 769, "partially_verified",
     "autodoc.de Technikangaben zum Audi A3 8P 2.0 FSI", "C",
     "https://www.autodoc.de/autoteile/steuerkette-10511/audi/a3/a3-sportback-8pa/18069-2-0-fsi",
     None,
     "Der 2.0 FSI (EA113) besitzt tatsaechlich einen Zahnriemen — die Angabe ist also "
     "nicht falsch. Fuer das genannte Intervall von 120.000 km fehlt aber eine "
     "Hersteller- oder Serviceplan-Quelle (Stufe A). Nach §10 darf ein Eintrag erst "
     "dann als HERSTELLERINTERVALL bezeichnet werden, wenn genau das belegt ist; "
     "bis dahin bleibt er ein hinterlegter Wartungshinweis."),

    # ══════════════════════════════════════════════════════════════════════
    # Mercedes C-Klasse W205 — Testmotorisierung C220d, Baujahr 2016
    # ══════════════════════════════════════════════════════════════════════
    ("schwachstelle_baureihe", 233, "partially_verified",
     "att24.de (9G-Tronic 725.0); gebrauchtwagenberater.de W205; kfz-dietrich.com", "C",
     "https://gebrauchtwagenberater.de/mercedes-c-klasse-w205-s205-probleme/", None,
     "Fuer die 9G-TRONIC (725.0) sind Schaltrucke in fruehen Exemplaren gut belegt, "
     "besonders im Stadtverkehr zwischen 20 und 40 km/h, ebenso die Empfehlung eines "
     "Getriebeoelwechsels alle 60.000 km trotz 'Lifetime'-Angabe — das deckt die "
     "DB-Aussage inklusive 'insbesondere bei mangelnder Wartung'. Die DB nennt "
     "zusaetzlich die 7G-TRONIC PLUS, fuer die keine entsprechende Quelle gefunden "
     "wurde. Deshalb nur teilweise verifiziert."),

    ("schwachstelle_baureihe", 234, "verified",
     "gebrauchtwagenberater.de W205; kfz-dietrich.com (XENTRY-Diagnose W205); autodoc.de", "C",
     "https://gebrauchtwagenberater.de/mercedes-c-klasse-w205-s205-probleme/", None,
     "Von drei unabhaengigen Fachquellen uebereinstimmend beschrieben: COMAND kann "
     "einfrieren bzw. Software-/Anzeigefehler zeigen, meist durch Software-Update "
     "dauerhaft behebbar. Die DB-Aussage ('gelegentliche Software-Probleme, Abstuerze "
     "oder Aussetzer des Displays', Schweregrad gering) entspricht dem genau und "
     "behauptet nicht mehr."),

    ("schwachstelle_baureihe", 235, "partially_verified",
     "gebrauchtwagenberater.de W205; autodoc.de Technikblog W205", "C",
     "https://gebrauchtwagenberater.de/mercedes-c-klasse-w205-s205-probleme/", None,
     "Kuehlmittelverlust am W205 ist belegt — als Ursache nennen die Quellen jedoch "
     "undichte WASSERPUMPEN ab etwa 80.000-120.000 km, waehrend die DB Kuehler und "
     "Schlaeuche nennt. Fehlerbild bestaetigt, Bauteilzuordnung nicht."),

    ("rueckruf", 121, "partially_verified",
     "keine belastbare Quelle gefunden (Recherche KBA / Fachmedien)", "B",
     None, None,
     "Zu einem W205-Rueckruf wegen fehlerhafter Befestigung des Airbag-Steuergeraets "
     "im Maerz 2017 liess sich keine amtliche oder fachmediale Bestaetigung finden. "
     "Die hinterlegte Referenz '8789' ist nicht verifizierbar. Bleibt als "
     "konservativer Rueckrufhinweis ohne angezeigte Nummer."),
)


def zusammenfassung() -> dict[str, int]:
    """Anzahl je Status — fuer Bericht und Tests."""
    out: dict[str, int] = {}
    for eintrag in PILOT_VERIFIKATIONEN:
        status = eintrag[2]
        out[status] = out.get(status, 0) + 1
    return out
