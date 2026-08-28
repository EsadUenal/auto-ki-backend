from __future__ import annotations

"""
RISIKOKLASSE B — Generationsaudit fuer amtliche Rueckrufe mit OFFENER
Zielgeneration.

WORUM ES GEHT
-------------
Der Import-Dry-Run hat 239 amtliche Rueckrufe (354 geplante VIRA-Zeilen) als
`SAFE_IMPORT` klassifiziert, deren Zielbaureihe `bauzeitraum_bis IS NULL`
traegt. Ein offenes Generationsende liest die Zeitraumlogik als "bis heute" —
damit schluckt die jeweils neueste bekannte Generation JEDEN Rueckruf aus
juengerer Produktion, auch wenn er in Wahrheit die NACHFOLGEgeneration betrifft.
Der dokumentierte Fall ist der BMW iX3 G08.

WAS DIESES MODUL IST — UND WAS NICHT
------------------------------------
Es ist ein reines AUDIT: es traegt fuer jede betroffene Baureihe die real
belegte Generationsgrenze zusammen und ordnet jeden Fall genau einer Klasse zu.
Es schreibt NICHTS: keine Rueckrufzeile, keinen Bauzeitraum, keine neue
Generation, keine Verifikation.

DIE ENTSCHEIDENDE ANGABE IST DER START DER NACHFOLGEGENERATION
--------------------------------------------------------------
Nicht das Produktionsende der alten Generation. Beide Zeitraeume ueberlappen
sich regelmaessig um ein bis zwei Jahre (Auslauf der alten, Anlauf der neuen
Baureihe). Ein amtliches Produktionsfenster, das VOR dem Anlauf der
Nachfolgegeneration endet, kann diese gar nicht meinen — unabhaengig davon, wie
lange die alte danach noch gebaut wurde.

AUSDRUECKLICH NICHT VERWENDET: die Median-Generationsdauer von 7 Jahren aus
`app/kba_import_kandidaten.py`. Sie ist eine Bestandsstatistik und taugt fuer
eine Vorsortierung, nicht fuer eine Einzelfallentscheidung. Jede Angabe unten
stammt aus einer benannten Quelle; wo keine Quelle vorliegt, steht die Baureihe
NICHT in dieser Tabelle und alle ihre Faelle fallen auf GENERATION_UNCLEAR.

QUELLENSTUFEN
-------------
    1  Hersteller (Newsroom/Presseinformation)
    2  amtliche oder technische Herstellerunterlage
    3  seriöse Fachquelle (Fachpresse, Wikipedia mit Belegen)

Der Bestand unten ist durchgaengig Stufe 3, in Einzelfaellen mit einer
Herstellerquelle in der Notiz. Das ist eine bewusste Offenlegung: fuer eine
Importentscheidung reicht Stufe 3 zur EINGRENZUNG, aber die schreibende
Uebernahme sollte je Fall gegen die Herstellerangabe gehen.
"""

GENERATION_CONFIRMED = "GENERATION_CONFIRMED"
SUCCESSOR_RECALL = "SUCCESSOR_RECALL"
CROSS_GENERATION = "CROSS_GENERATION"
GENERATION_UNCLEAR = "GENERATION_UNCLEAR"

KLASSEN = (GENERATION_CONFIRMED, SUCCESSOR_RECALL, CROSS_GENERATION,
           GENERATION_UNCLEAR)

RECHERCHIERT_AM = "2026-08-28"

# baureihe_id -> (produktionsende, nachfolger_ab, quelle, notiz)
#
#   produktionsende  Jahr, in dem die Generation auslief; None = laeuft noch
#   nachfolger_ab    Jahr, in dem die NACHFOLGEgeneration anlief;
#                    None = kein Nachfolger bekannt/angekuendigt
GENERATIONEN: dict[str, tuple] = {
    # ── Die drei ausdruecklich benannten kritischen Muster ──────────────────
    "bmw-ix3-g08": (
        2025, 2025, "de.wikipedia.org/wiki/BMW_NA5; bmwblog.com 2025-02-14",
        "G08-Produktion Q2/2025 beendet; Nachfolger NA5 laeuft seit Ende "
        "Oktober 2025 in Debrecen, Markteinfuehrung Europa Maerz 2026."),
    "volkswagen-t-roc-a1": (
        2025, 2025, "de.wikipedia.org/wiki/VW_T-Roc_II; autobild.de",
        "T-Roc II ab November 2025. Das Cabriolet der ersten Generation bleibt "
        "laut Wikipedia bis 2027 im Angebot — fuer Rueckrufe der geschlossenen "
        "Karosserie ist 2025 die Grenze."),
    "audi-q3-ii": (
        2025, 2025, "de.wikipedia.org/wiki/Audi_Q3_F3; de.wikipedia.org/wiki/Audi_Q3_FJ",
        "F3 zwischen 2018 und 2025 gebaut; Nachfolger FJ im Juni 2025 "
        "vorgestellt, Serienproduktion ab Sommer 2025 in Gyoer."),

    # ── Volkswagen ──────────────────────────────────────────────────────────
    "volkswagen-tiguan-ii": (
        2024, 2023, "de.wikipedia.org/wiki/VW_Tiguan_III; volkswagen-newsroom.com",
        "Tiguan II 2016-2024. Der Nachfolgeanlauf steht hier auf 2023, nicht "
        "auf 2024: die amtlichen KBA-Fenster sind PRODUKTIONSzeitraeume, und "
        "der Tiguan III laeuft laut VW-Medieninformation 140/2023 ab Herbst "
        "2023 in Wolfsburg vom Band — die Auslieferung begann erst im Februar "
        "2024. Der frueheste der beiden Zeitpunkte ist der konservative."),
    "volkswagen-arteon-typ-3h": (
        2025, None, "de.motor1.com/news/746885; motor1.com/news/707378",
        "Limousine 2023 vom Markt, Shooting Brake bis 2025; kein Nachfolger."),
    "volkswagen-touran-ii": (
        2026, None, "motor1.com/news/795449; fuhrpark.de",
        "Produktion im April 2026 nach 23 Jahren eingestellt, kein Nachfolger."),
    "volkswagen-touareg-iii": (
        None, None, "de.motor1.com (VW-Neuheiten)",
        "Nach rund acht Jahren waere ein Generationswechsel faellig; ob es "
        "einen Touareg-Nachfolger gibt, ist nicht entschieden."),
    "volkswagen-polo-vi": (
        None, None, "de.wikipedia.org/wiki/VW_Polo_VI; motor1.com/news/735524",
        "Polo VI seit Juli 2017, weiterhin in Produktion; der ID. Polo ergaenzt "
        "das Programm, ersetzt den Verbrenner-Polo nicht."),
    "volkswagen-golf-viii": (
        None, None, "de.motor1.com/news/800705 (VW-Neuheiten 2026)",
        "Golf VIII seit 2019, Nachfolger nicht in Sicht; 2026 Auffrischung."),
    "volkswagen-caddy-v": (
        None, None, "de.motor1.com/news/800705",
        "Caddy V seit 2020, weiterhin aktuelle Generation."),
    "volkswagen-taigo-2020": (
        None, None, "de.motor1.com/news/800705",
        "Wird bis zum Ende des Lebenszyklus (2026/2027) weitergebaut."),

    # ── Mercedes-Benz ───────────────────────────────────────────────────────
    "mercedes-benz-v-klasse-w447": (
        None, 2026, "auto-motor-und-sport.de (VLE); mt.de",
        "W447 laeuft weiter; Nachfolger VLE Premiere und Produktionsstart "
        "erstes Halbjahr 2026."),
    "mercedes-benz-cls-c257": (
        2023, None, "de.wikipedia.org/wiki/Mercedes-Benz_C_257; mercedes-fans.de",
        "Produktion Ende August 2023 ohne Nachfolger eingestellt."),
    "mercedes-benz-a-klasse-w177": (
        2025, None, "mbpassion.de 2025-07; jesmb.de",
        "Produktion Rastatt Ende 2025 eingestellt, kein Nachfolger auf MMA."),
    "mercedes-benz-b-klasse-w247": (
        2025, None, "mbpassion.de 2025-07",
        "Produktion Rastatt Ende 2025 eingestellt, kein Nachfolger auf MMA."),
    "mercedes-benz-c-klasse-w206": (
        None, None, "en.wikipedia.org/wiki/Mercedes-Benz_C-Class_(W206)",
        "Seit Maerz 2021 in Produktion; 2026 Modellpflege, keine neue Generation."),
    "mercedes-benz-s-klasse-w223": (
        None, None, "en.wikipedia.org/wiki/Mercedes-Benz_S-Class_(W223)",
        "Seit 2020 in Produktion; Facelift 2026, keine neue Generation."),
    "mercedes-benz-glb-x247": (
        None, 2026, "mercedesblog.com (neue Modelle 2026)",
        "Neue GLB-Generation fuer 2026 angekuendigt."),
    "mercedes-benz-eqa-h243": (
        2026, 2026, "mbpassion.de 2026-04; auto-motor-und-sport.de",
        "EQA-Produktion endet im Juli 2026; Nachfolger GLA EQ 2026."),
    "mercedes-benz-eqb-x243": (
        2026, 2026, "en.wikipedia.org/wiki/Mercedes-Benz_EQB; mercedesblog.com",
        "Produktion bis 2026; geht im GLB/GLA-EQ-Programm auf."),
    "mercedes-benz-eqe-v295": (
        2026, None, "autocar.co.uk; mbpassion.de 2025-08",
        "Produktion Limousine und SUV endet 2026; indirekte Ablösung durch "
        "C-Klasse EQ und GLC EQ."),
    "mercedes-benz-e-klasse-w214": (
        None, None, "en.wikipedia.org/wiki/Mercedes-Benz_E-Class_(W214)",
        "Seit Mai 2023 in Produktion, aktuelle Generation."),
    "mercedes-benz-sl-r232": (
        None, None, "en.wikipedia.org/wiki/Mercedes-Benz_SL-Class_(R231)",
        "R232 ist die aktuelle Generation (Nachfolger des 2020 ausgelaufenen R231)."),
    "mercedes-benz-gla-h247": (
        None, 2026, "auto-motor-und-sport.de (GLA EQ 2026)",
        "H247 laeuft weiter; GLA EQ auf MMA-Basis ab 2026."),

    # ── BMW ─────────────────────────────────────────────────────────────────
    "bmw-3er-g20-g21": (
        None, 2026, "bimmertoday.de 2025-01-27; autozeitung.de",
        "Nachfolger G50 kommt Ende 2026/Anfang 2027; Produktionsanlauf Neue "
        "Klasse 2026 parallel zur laufenden Fertigung."),
    "bmw-4er-g22-g23-g26": (
        None, None, "g20.bimmerpost.com (Produktionsplanung)",
        "G22/G23/G82/G83 bleiben bis Juni 2029 in Produktion."),
    "bmw-8er-g15-g14-g16": (
        2026, None, "auto-motor-und-sport.de (Produktionsende 8er)",
        "Produktion endet April 2026, kein direkter Nachfolger."),
    "bmw-z4-g29": (
        2026, None, "autozeitung.de; auto-motor-und-sport.de",
        "Produktion endet Maerz 2026, kein Nachfolger."),
    "bmw-x4-g02": (
        2025, 2027, "bimmertoday.de 2026-01-15",
        "G02-Produktion im November 2025 beendet; Nachfolger iX4 (NA7) ab 2027."),
    "bmw-x5-g05": (
        None, 2026, "bmwblog.com 2024-10-08 / 2025-08-25",
        "Nachfolger G65 startet die Produktion im August 2026."),
    "bmw-x6-g06": (
        None, 2026, "bmwblog.com (X5-G65-Zyklus)",
        "INDIREKT: der X6 teilt den Modellzyklus des X5 G05; eine eigene Quelle "
        "zum X6-Nachfolger liegt nicht vor. Frueheste Nachfolge damit 2026."),
    "bmw-x7-g07": (
        None, 2027, "bmwblog.com 2025-11-05",
        "Nachfolger G67 debuetiert 2027."),
    "bmw-x3-g45": (
        None, None, "en.wikipedia.org/wiki/BMW_X3_(G45)",
        "Produktion seit August 2024, aktuelle Generation."),
    "bmw-7er-g70": (
        None, None, "bmwblog.com 2025-11-05",
        "G70 aktuell; fuer 2026 ist ein Facelift angekuendigt, keine neue "
        "Generation."),

    # ── Audi ────────────────────────────────────────────────────────────────
    "audi-a6-c8": (
        2025, 2025, "de.wikipedia.org/wiki/Audi_A6_C8",
        "C8 2018 bis Maerz 2025; Nachfolger C9 im Maerz/April 2025 vorgestellt."),
    "audi-a7-typ-4k": (
        None, None, "de.wikipedia.org/wiki/Audi_A7_C8; motor1.com/news/750206",
        "A7 C8 erhaelt keinen Nachfolger."),
    "audi-a8-d5": (
        2026, None, "autoscout24.de (A8 Produktionsende 2026); gute-fahrt.de",
        "D5 seit Ende 2017, Produktionsende nach rund acht Jahren."),
    "audi-q7-typ-4m": (
        None, 2026, "firmenauto.de (Markenausblick Audi)",
        "Weltpremiere des neuen Q7 fuer Sommer 2026 angekuendigt."),
    "audi-a1-gb": (
        2026, 2026, "autocar.co.uk; carscoops.com 2026-04",
        "Produktion endet 2026, Ablösung durch A2 e-tron."),
    "audi-q2-ga": (
        2026, 2026, "autocar.co.uk; newmobility.news 2026-04-29",
        "Produktion Ingolstadt im April 2026 nach neun Jahren beendet; "
        "Ablösung durch A2 e-tron."),
    "audi-a3-typ-8y": (
        None, None, "autocar.co.uk (Audi Einstiegsprogramm)",
        "A3 wird in Ingolstadt weitergebaut, aktuelle Generation."),
    "audi-q4-e-tron-2021": (
        None, None, "autozeitung.de (Q4 e-tron Facelift 2026)",
        "Facelift ab Mitte 2026 — dieselbe Generation, kein Nachfolger."),
    "audi-e-tron-gt-typ-4j": (
        None, None, "autos.yahoo.com (Audi Sport Boellinger Hoefe)",
        "Facelift 2024 innerhalb derselben Generation; Auslauf angekuendigt, "
        "aber ohne Datum."),
    "audi-rs-e-tron-gt-typ-ge": (
        None, None, "autos.yahoo.com (Audi Sport Boellinger Hoefe)",
        "Wie e-tron GT: Facelift 2024 innerhalb derselben Generation."),

    # ── Opel ────────────────────────────────────────────────────────────────
    "opel-grandland-a": (
        2024, 2024, "insideevs.de/news/658480; electrive.net 2023-03-22",
        "Grandland A 2017-2024; Nachfolger Grandland B Produktionsstart zweite "
        "Jahreshaelfte 2024 in Eisenach, Auslieferung ab Januar 2025."),
    "opel-corsa-f": (
        None, 2027, "de.wikipedia.org/wiki/Opel_Corsa_F; autozeitung.de",
        "Corsa F auch 2026 in Produktion; Generation G Premiere ~2026, "
        "Marktstart 2027."),
    "opel-astra-l": (
        None, None, "media.stellantis.com (naechste Astra-Generation)",
        "Astra L seit 2021/2022 aktuell; naechste Generation angekuendigt, "
        "ohne Datum."),
    "opel-mokka-b": (
        None, 2030, "de.motor1.com/news/782651 (Opel-Neuheiten 2026)",
        "Neuauflage des Mokka erst bis 2030 erwartet."),

    # ── Ford ────────────────────────────────────────────────────────────────
    "ford-focus-mk4": (
        2025, None, "motor1.com/news/752666",
        "Produktion endet im November 2025; kein direkter Nachfolger, "
        "Luecke bis 2027."),
    "ford-kuga-mk3": (
        2026, None, "auto-motor-und-sport.de (Aus fuer Ford Kuga Ende 2025/2026)",
        "Europaeischer Kuga laeuft Ende 2026 aus; kein direkter Nachfolger."),
    "ford-puma-mk2": (
        None, None, "auto-motor-und-sport.de; motor1.com",
        "Puma weiterhin im Programm, der Gen-E ist eine Variante derselben "
        "Generation."),

    # ── Toyota ──────────────────────────────────────────────────────────────
    "toyota-rav4-v": (
        2026, 2026, "de.motor1.com/news/760112; global.toyota Newsroom",
        "Fuenfte Generation ab 2018; sechste Generation ab Juni 2026."),
    "toyota-hilux-achte-generation": (
        None, 2025, "de.motor1.com/news/777740; autohub.de",
        "Achte Generation seit 2015; neunte Generation im November 2025 "
        "vorgestellt."),
    "toyota-supra-fünfte-generation-(a90)": (
        2026, None, "motor1.com/news/776932; carbuzz.com",
        "Produktion endet im Maerz 2026; Nachfolger angekuendigt, ohne Datum."),

    # ── Hyundai / Kia ───────────────────────────────────────────────────────
    "hyundai-i30-dritte-generation": (
        None, None, "de.wikipedia.org/wiki/Hyundai_i30_(PD); de.motor1.com/news/767898",
        "PD seit 2017, Facelifts 2020, 2024 und Anfang 2026; kein "
        "Verbrenner-Nachfolger geplant."),
    "hyundai-tucson-vierte-generation": (
        None, None, "autozeitung.de (Hyundai-Neuheiten 2026)",
        "Facelift 2024; neue Version fruehestens 2026/2027."),
    "kia-ceed-cd": (
        None, None, "de.wikipedia.org/wiki/Kia_Ceed_(CD)",
        "Dritte Generation seit 2018, technischer Zwilling des i30 PD; kein "
        "Nachfolger angekuendigt."),
    "kia-sportage-nq5": (
        None, 2028, "meinauto.de 2026-01; carsdirect.com",
        "Modellpflege zum Modelljahr 2026; Generationswechsel erst 2028."),
    "kia-sorento-mq4": (
        None, 2027, "meinauto.de 2026-01",
        "Modellpflege zum Modelljahr 2026; Generationswechsel ~2027."),
    "kia-stonic-yb": (
        None, None, "meinauto.de / motor1.com (Kia-Neuheiten 2026)",
        "Kein Generationswechsel angekuendigt."),

    # ── Seat / Cupra / Skoda ────────────────────────────────────────────────
    "seat-arona-erste-generation": (
        None, None, "de.motor1.com/news/776633; autocar.co.uk",
        "Nach ueber acht Jahren Facelift 2026 — dieselbe Generation."),
    "seat-ibiza-fünfte-generation": (
        None, None, "t-online.de; autocar.co.uk",
        "Facelift 2026 — dieselbe Generation."),
    "seat-ateca-erste-generation": (
        2026, 2024, "en.wikipedia.org/wiki/SEAT_Ateca; motor1.com/news/789708",
        "Produktion laeuft bis 2026; als Nachfolger gilt der Cupra Terramar."),
    "seat-leon-vierte-generation": (
        None, None, "autonotizen.de (Seat/Cupra Modellprogramm)",
        "Leon IV erhaelt ein Facelift, keine neue Generation."),
    "cupra-formentor-erste-generation": (
        None, None, "autonotizen.de; autozeitung.de (Cupra-Neuheiten)",
        "Umfangreiches Facelift 2024 innerhalb derselben Generation."),
    "cupra-leon-erste-generation": (
        None, None, "autonotizen.de; autozeitung.de (Cupra-Neuheiten)",
        "Umfangreiches Facelift 2024 innerhalb derselben Generation."),
    "cupra-born-erste-generation": (
        None, None, "autozeitung.de (Cupra-Neuheiten 2026)",
        "Facelift im Sommer 2026 rund fuenf Jahre nach Einfuehrung — dieselbe "
        "Generation."),
    "skoda-karoq-erste-generation": (
        None, 2028, "autobild.de; autonotizen.de",
        "Erste Generation seit 2017, Facelift 2021; Nachfolger zum Jahreswechsel "
        "2027/2028."),
}


def klassifiziere(prod_von: int | None, prod_bis: int | None,
                  baureihe_id: str, start: int | None) -> tuple[str, str]:
    """Ordnet EINEN B-Fall genau einer Klasse zu. Rueckgabe: (klasse, grund)."""
    fakt = GENERATIONEN.get(baureihe_id)
    if fakt is None:
        return (GENERATION_UNCLEAR,
                "keine belastbare Quelle zur Generationsgrenze recherchiert")
    ende, nachfolger, quelle, _notiz = fakt

    if prod_von is None or prod_bis is None:
        return (GENERATION_UNCLEAR, "amtliches Produktionsfenster unvollstaendig")
    if prod_bis < prod_von:
        return (GENERATION_UNCLEAR,
                f"amtliches Fenster ist invers ({prod_von}-{prod_bis})")

    # Das Fenster beginnt VOR dem in VIRA hinterlegten Generationsstart.
    #
    # Dafuer gibt es zwei Erklaerungen, und beide sind aus den vorliegenden
    # Quellen NICHT unterscheidbar: entweder umfasst der amtliche Rueckruf
    # tatsaechlich auch Fahrzeuge der Vorgaengergeneration (KBA 11352 nennt fuer
    # den CLS das Fenster 2017-2021, der C257 loeste 2018 den C218 ab), oder das
    # VIRA-Startjahr ist um ein Jahr ungenau (der Audi e-tron GT lief bereits
    # Ende 2020 an, VIRA fuehrt 2021). Recherchiert wurde in diesem Audit das
    # ENDE der Generation und der Anlauf des Nachfolgers, nicht der exakte
    # Produktionsbeginn. Solange das offen ist, gehoert der Fall nicht in einen
    # automatischen Import — die Klasse sagt genau das und behauptet nicht mehr.
    if start is not None and prod_von < start:
        return (CROSS_GENERATION,
                f"amtliches Fenster beginnt {prod_von}, der hinterlegte "
                f"Generationsstart ist {start} — entweder umfasst der Rueckruf "
                f"die Vorgaengergeneration oder das VIRA-Startjahr ist ungenau; "
                f"der exakte Produktionsbeginn wurde hier nicht recherchiert")

    if nachfolger is None:
        if ende is not None and prod_bis > ende:
            return (CROSS_GENERATION,
                    f"amtliches Fenster reicht bis {prod_bis}, die Generation "
                    f"lief {ende} aus (kein Nachfolger bekannt) [{quelle}]")
        return (GENERATION_CONFIRMED,
                f"kein Nachfolger bekannt; Fenster {prod_von}-{prod_bis} liegt "
                f"in der laufenden Generation [{quelle}]")

    if prod_bis < nachfolger:
        return (GENERATION_CONFIRMED,
                f"Fenster endet {prod_bis}, die Nachfolgegeneration lief erst "
                f"{nachfolger} an [{quelle}]")
    if prod_von >= nachfolger:
        return (SUCCESSOR_RECALL,
                f"Fenster beginnt {prod_von}, also nach dem Anlauf der "
                f"Nachfolgegeneration {nachfolger} [{quelle}]")
    return (CROSS_GENERATION,
            f"Fenster {prod_von}-{prod_bis} ueberspannt den Generationswechsel "
            f"{nachfolger} [{quelle}]")
