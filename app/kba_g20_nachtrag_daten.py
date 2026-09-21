"""Nachtrag: amtlich belegte Rückrufe für BMW 3er G20/G21 (Baureihe bmw-3er-g20-g21).

ANLASS (KaufCheck RC1)
----------------------
Ein echter KaufCheck für einen BMW 330i G20, Baujahr 2019, zeigte zwei
Rückrufe ("Bremskraftverstärker 2020-03", "Schweißnähte an der Lenkung
2020-08") — beide ohne amtliche Referenz und in `fakt_verifikation` bereits als
`unverified` eingestuft (Fehlzitation bzw. anderes Fehlerbild). Gleichzeitig
fehlten amtliche Rückrufe, die für dieses Fahrzeug einschlägig sind, darunter
die große Starterrelais-Aktion vom Oktober 2025.

Die beiden unbelegten Zeilen werden nicht gelöscht, sondern durch die
generische Beleg-Sperre (app/recall_filter.py::rueckruf_ist_belegt) nicht mehr
angezeigt. Dieser Nachtrag ergänzt die belegten Aktionen.

QUELLE (Stufe A)
----------------
KBA-Rückrufdatenbank, amtlicher Gesamtexport
https://www.kba-online.de/rrdb/buerger/api/rueckruf/export?format=csv&type=cars
abgerufen am 2026-09-21 (7.860 Rückrufe). Mangel- und Abhilfetext Wort für Wort
aus dem Datensatz.

GENERATIONSZUORDNUNG — warum genau diese drei
---------------------------------------------
Das KBA nennt das Modell nur als "3" ohne Generation. Im Kalenderjahr 2019
wurden aber zwei 3er-Generationen gebaut (F3x auslaufend, G20 anlaufend). Eine
Aktion wird deshalb nur übernommen, wenn die G20-Zuordnung zusätzlich trägt:

  10009  Spurstange (Produktion 2018–2019). Modelle 3, X3, X4, Z4 — die
         Zweitquelle (bimmertoday.de, 14.08.2020) nennt ausdrücklich 3er
         G20/G21, Z4 G29, X3 M F97, X4 M F98. Beim X3 G01 steht dieselbe Aktion
         bereits amtlich im Bestand (#4005).
  9839   Gurtschloss-Sensorik (Produktion 2019–2020). Fachpresse
         (kfz-betrieb, auto-motor-und-sport): Limousine und Touring G20/G21,
         Bauzeitraum 11/2019–03/2020, 736 Fahrzeuge in Deutschland. Beim X3 G01
         bereits amtlich im Bestand (#4006).
  15632R Starterrelais, Wassereintritt, Brandgefahr (Produktion
         28.09.2015–07.09.2021, 136.489 Fahrzeuge in Deutschland, 1,15 Mio.
         weltweit). Das KBA grenzt das Modell "3" NICHT ein ("N/A") — jeder im
         Fenster gebaute 3er ist potenziell betroffen, also auch der G20. Die
         Zuordnung bleibt entsprechend "für Teile der Baureihe — per FIN
         prüfen"; eine Betroffenheit wird nie ohne FIN behauptet.

BEWUSST NICHT ÜBERNOMMEN (Zuordnung nicht belastbar)
----------------------------------------------------
  8902   Knieairbag (2019): Modellliste "M4, 3, M2, 4, 2" deutet auf F-Baureihen.
  9220   Kopfstützenverriegelung (2019): Modell "3", Generation nicht belegt.
  9398   Ausgleichswellen (2018–2019): Motor und Generation nicht belegt.
  12690  Sitzrahmen-Schweißnaht (2019–2021): Generation nicht belegt (6 Fzg. DE).
  16791R Starterrelais (Produktion 2014–11/2020, Modelle 3/5/7): Presse spricht
         von "älteren 3er, 5er und 7er", Generation nicht belegt.
  16790R/16131R Starterrelais (Produktion ab 05/2020 bzw. 07/2020): betreffen
         das Baujahr 2019 nicht.

DATENLIZENZ
-----------
Datenquelle: Kraftfahrt-Bundesamt, Rückrufdatenbank (Fahrzeuge); Datenlizenz
Deutschland – Namensnennung – Version 2.0 (dl-de/by-2-0). Daten verändert:
Baujahre auf den Bauzeitraum der Baureihe verengt.
"""
from __future__ import annotations

GEPRUEFT_AM = "2026-09-21"

KBA_QUELLE = ("KBA-Rueckrufdatenbank, amtlicher Gesamtexport (7.860 Rueckrufe), "
              "abgerufen 2026-09-21")
KBA_URL = "https://www.kba-online.de/rrdb/buerger/api/rueckruf/export?format=csv&type=cars"
LIZENZVERMERK = ("Datenquelle: Kraftfahrt-Bundesamt, Rueckrufdatenbank (Fahrzeuge), "
                 "Abrufdatum 2026-09-21; Datenlizenz by-2-0 "
                 "(https://www.govdata.de/dl-de/by-2-0); Daten veraendert: Baujahre auf "
                 "den Bauzeitraum der Baureihe verengt.")

BAUREIHE = "bmw-3er-g20-g21"

ZEILEN: tuple[dict, ...] = (
    dict(
        id=4033, baureihe_id=BAUREIHE, datum="2020-08-03", betroffene_baujahre="2019",
        mangel=("Aufgrund fehlerhafter Auslegung kann es bei hohen Belastungen zu einem "
                "Dauerschwingbruch in der Knicknut der Spurstange kommen, wodurch die "
                "Radführung nicht mehr gewährleistet ist und in der Folge erhöhte "
                "Unfallgefahr besteht."),
        abhilfe="Beide Spurstangen werden ersetzt.",
        kba_referenz="10009",
        herstellercode="0032140300",
        notiz=("Amtlicher Datensatz: Modelle '3, X3, X4, Z4', Produktionszeitraum 2018-2019, "
               "Veroeffentlichung 2020-08-03, 1.304 Fahrzeuge DE. Generationszuordnung "
               "G20/G21 durch bimmertoday.de (14.08.2020) belegt; beim X3 G01 bereits "
               "amtlich im Bestand (#4005). Baujahre auf den Bauzeitraum der Baureihe "
               "verengt (2019)."),
    ),
    dict(
        id=4034, baureihe_id=BAUREIHE, datum="2020-04-15", betroffene_baujahre="2019-2020",
        mangel=("Durch eine fehlerhaft montierte Sensorik innerhalb des Gurtschlosses kann "
                "es zu fehlerhaften Auslösungen der für den betroffenen Sitzplatz "
                "vorgesehenen Airbags und Gurtstraffern kommen."),
        abhilfe="Prüfung und ggf. Austausch des betroffenen Sicherheitsgurtschlosses",
        kba_referenz="9839",
        herstellercode="0072130200",
        notiz=("Amtlicher Datensatz: Modelle '3, X3, X4, X3M, 8, M8, X4M', "
               "Produktionszeitraum 2019-2020, Veroeffentlichung 2020-04-15, 736 Fahrzeuge "
               "DE. Fachpresse (kfz-betrieb, auto-motor-und-sport): Limousine und Touring "
               "G20/G21, Bauzeitraum 11/2019-03/2020. Beim X3 G01 bereits amtlich im "
               "Bestand (#4006)."),
    ),
    dict(
        id=4035, baureihe_id=BAUREIHE, datum="2025-10-10", betroffene_baujahre="2019-2021",
        mangel=("Das Starterrelais kann ungenügend gegen das Eindringen von Wasser sein. "
                "Im Falle von Korrosion kann es zu einem Kurzschluss und in der Folge zu "
                "einem Brand kommen."),
        abhilfe="Ersatz des Starters des Motors.",
        kba_referenz="15632R",
        herstellercode="0012550600, 0012560600, 0012570600, 0012620600",
        notiz=("Amtlicher Datensatz: Modelle '1, 2, 3, X3, X5, X6, 7, 4, Z4, 6, 5, I3, X4, "
               "X7', Eingrenzung 'N/A', Produktionszeitraum 28.09.2015-07.09.2021, "
               "Veroeffentlichung 2025-10-10, 136.489 Fahrzeuge DE / 1.145.385 weltweit, "
               "KBA-ueberwacht. Keine Generationseingrenzung: jeder im Fenster gebaute 3er "
               "ist potenziell betroffen — Zuordnung deshalb nur baureihenweit (FIN "
               "pruefen). Baujahre auf den G20-Bauzeitraum im Produktionsfenster verengt "
               "(2019-2021)."),
    ),
)
