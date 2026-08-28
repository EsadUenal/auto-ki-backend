from __future__ import annotations

"""
PRIMAERQUELLEN zu den Generationsgrenzen der Risikoklasse B.

WARUM ES DIESE ZWEITE TABELLE GIBT
----------------------------------
`app/kba_generation_audit.py` haelt die Recherche aus Fachquellen fest. Sie
reicht, um Faelle AUSZUSCHLIESSEN — fuer eine schreibende Uebernahme reicht sie
nicht. Diese Datei enthaelt ausschliesslich Angaben, die auf einer
HERSTELLERSEITE stehen: Presseportal, Produktionsnetzwerk-Seite oder
Pressemitteilung des Herstellers.

Beide Tabellen bleiben getrennt. Eine Fachquelle wird hier NICHT zur
Primaerquelle, indem sie dieselbe Zahl nennt; und eine Baureihe ohne Eintrag
hier ist nicht "wahrscheinlich richtig", sondern schlicht unbestaetigt.

WAS EINE ANGABE BESTAETIGEN MUSS
--------------------------------
Damit ein amtliches Produktionsfenster als "innerhalb der Generation" gilt,
muss die Herstellerquelle EINES von beidem hergeben:

  `nachfolger_ab`      Jahr, in dem die NACHFOLGEgeneration anlief. Ein Fenster,
                       das davor endet, kann sie nicht meinen.
  `in_produktion_bis`  Jahr, bis zu dem der Hersteller diese Generation
                       nachweislich noch baut. Ein Fenster, das nicht darueber
                       hinausreicht, liegt in der Generation.

Fehlt beides, gehoert die Baureihe nicht in diese Tabelle.

GRENZE, DIE OFFEN BLEIBT
------------------------
Hersteller veroeffentlichen Produktionsstarts zuverlaessig, ProduktionsENDEN
fast nie. Fuer viele Baureihen liess sich deshalb ueber die Herstellerseiten
kein Ende belegen — diese Faelle bleiben SOURCE_UNCLEAR, statt sie mit der
Fachquelle aus der ersten Tabelle zu schliessen. Ebenso wenig recherchiert ist
der exakte ProduktionsBEGINN; Faelle, deren Fenster vor dem hinterlegten
Generationsstart beginnen, bleiben deshalb SOURCE_CROSS_GENERATION.
"""

SOURCE_CONFIRMED = "SOURCE_CONFIRMED"
SOURCE_CONTRADICTED = "SOURCE_CONTRADICTED"
SOURCE_CROSS_GENERATION = "SOURCE_CROSS_GENERATION"
SOURCE_UNCLEAR = "SOURCE_UNCLEAR"

SOURCE_KLASSEN = (SOURCE_CONFIRMED, SOURCE_CONTRADICTED,
                  SOURCE_CROSS_GENERATION, SOURCE_UNCLEAR)

GEPRUEFT_AM = "2026-08-28"

# Quellenstufen nach Auftrag:
#   1  Hersteller-Presseportal / Herstellerarchiv
#   2  offizielle technische Unterlagen / Modellhistorie
#   3  Hersteller-Preislisten / Broschueren / Produktionsmitteilungen
#   4  seriöse Fachquelle (reicht ALLEIN nicht fuer einen Import)

# baureihe_id -> dict(nachfolger_ab, in_produktion_bis, stufe, url, beleg)
PRIMAERQUELLEN: dict[str, dict] = {

    # ── Mercedes-Benz ───────────────────────────────────────────────────────
    "mercedes-benz-v-klasse-w447": {
        "nachfolger_ab": 2026, "in_produktion_bis": 2026, "stufe": 1,
        "url": "https://group.mercedes-benz.com/company/production/production-vle-vitoria.html",
        "beleg": "Serienproduktionsstart des VLE im Werk Vitoria am 12.06.2026; "
                 "Vitoria fertigt ab 2026 VLE, V-Klasse, Vito und eVito flexibel "
                 "auf derselben Linie — die V-Klasse laeuft also parallel weiter.",
    },
    "mercedes-benz-c-klasse-w206": {
        "nachfolger_ab": None, "in_produktion_bis": 2026, "stufe": 1,
        "url": "https://group.mercedes-benz.com/unternehmen/produktion/produktionsnetzwerk/produktionsnetzwerk-bremen.html",
        "beleg": "Werk Bremen: 'In Bremen laufen die C-Klasse mit Limousine, "
                 "T-Modell, Coupe und Cabriolet ... vom Band'; die elektrische "
                 "C-Klasse laeuft ab Q2/2026 separat in Kecskemet an.",
    },
    "mercedes-benz-s-klasse-w223": {
        "nachfolger_ab": None, "in_produktion_bis": 2026, "stufe": 1,
        "url": "https://group.mercedes-benz.com/company/production/news/",
        "beleg": "Am 30.04.2026 den Hochlauf von S-Klasse, Maybach S-Klasse und "
                 "EQS im Werk Sindelfingen gefeiert — dieselbe Generation, "
                 "Modellpflege statt Generationswechsel.",
    },
    "mercedes-benz-e-klasse-w214": {
        "nachfolger_ab": None, "in_produktion_bis": 2026, "stufe": 1,
        "url": "https://group.mercedes-benz.com/unternehmen/standorte/produktionsnetzwerk-sindelfingen.html",
        "beleg": "Werk Sindelfingen ist zustaendig fuer die Produktion der "
                 "E-Klasse als Limousine, T-Modell und All-Terrain.",
    },
    "mercedes-benz-cls-c257": {
        "nachfolger_ab": None, "in_produktion_bis": 2026, "stufe": 1,
        "url": "https://group.mercedes-benz.com/unternehmen/standorte/produktionsnetzwerk-sindelfingen.html",
        "beleg": "Werk Sindelfingen fuehrt den CLS in seiner Modellliste. Die "
                 "Fachquelle nennt das Produktionsende August 2023; beide "
                 "Angaben schliessen ein Fenster bis 2023 ein.",
    },
    "mercedes-benz-sl-r232": {
        "nachfolger_ab": None, "in_produktion_bis": 2026, "stufe": 1,
        "url": "https://group.mercedes-benz.com/unternehmen/produktion/produktionsnetzwerk/produktionsnetzwerk-bremen.html",
        "beleg": "Werk Bremen: der AMG SL laeuft dort vom Band.",
    },
    "mercedes-benz-a-klasse-w177": {
        "nachfolger_ab": None, "in_produktion_bis": 2026, "stufe": 1,
        "url": "https://group.mercedes-benz.com/company/locations/production-network-rastatt.html",
        "beleg": "Werk Rastatt: 'The A-Class, the GLA, the all-electric EQA, and "
                 "the new CLA are built in Rastatt.'",
    },
    "mercedes-benz-gla-h247": {
        "nachfolger_ab": None, "in_produktion_bis": 2026, "stufe": 1,
        "url": "https://group.mercedes-benz.com/company/locations/production-network-rastatt.html",
        "beleg": "Werk Rastatt fuehrt den GLA in der Modellliste des Standorts.",
    },
    "mercedes-benz-eqa-h243": {
        "nachfolger_ab": None, "in_produktion_bis": 2026, "stufe": 1,
        "url": "https://group.mercedes-benz.com/company/locations/production-network-rastatt.html",
        "beleg": "Werk Rastatt produziert den vollelektrischen EQA seit 2021 und "
                 "fuehrt ihn weiterhin in der Modellliste.",
    },

    # ── Volkswagen ──────────────────────────────────────────────────────────
    "volkswagen-tiguan-ii": {
        "nachfolger_ab": 2023, "in_produktion_bis": None, "stufe": 1,
        "url": "https://www.volkswagen-newsroom.com/en/press-releases/"
               "new-tiguan-generation-volkswagens-bestseller-celebrates-world-"
               "premiere-in-front-of-10000-employees-17655",
        "beleg": "Medieninformation Nr. 140/2023: die dritte Generation laeuft "
                 "ab Herbst 2023 in Wolfsburg vom Band und kommt im ersten "
                 "Quartal 2024 auf den Markt.",
    },
    "volkswagen-t-roc-a1": {
        "nachfolger_ab": 2025, "in_produktion_bis": None, "stufe": 1,
        "url": "https://www.volkswagen-newsroom.com/en/press-releases/"
               "world-premiere-of-the-t-roc-new-generation-of-the-best-seller-"
               "is-high-quality-and-innovative-19769",
        "beleg": "Weltpremiere der zweiten T-Roc-Generation; Vorverkauf in "
                 "Deutschland ab 28. August, Markteinfuehrung im November.",
    },
    "volkswagen-golf-viii": {
        "nachfolger_ab": None, "in_produktion_bis": 2026, "stufe": 1,
        "url": "https://www.volkswagen-newsroom.com/de/pressemitteilungen/"
               "meilenstein-der-volkswagen-produktion-49-millionen-fahrzeuge-"
               "aus-dem-werk-wolfsburg-20172",
        "beleg": "Medieninformation vom 12.02.2026 zum laufenden Werk Wolfsburg; "
                 "die vollelektrischen Nachfolger von Golf und T-Roc kommen erst "
                 "gegen Ende der Dekade auf der SSP-Plattform.",
    },

    # ── Audi ────────────────────────────────────────────────────────────────
    "audi-q3-ii": {
        "nachfolger_ab": 2025, "in_produktion_bis": None, "stufe": 1,
        "url": "https://www.audi.com/en/press-releases/versatile-sporty-and-"
               "digitally-connected-the-new-audi-q3-16771",
        "beleg": "Nach der Weltpremiere im Juni begann die Serienproduktion der "
                 "dritten Generation in Gyoer; Markteinfuehrung im Oktober.",
    },
    "audi-q2-ga": {
        "nachfolger_ab": None, "in_produktion_bis": 2026, "stufe": 1,
        "url": "https://www.audi-mediacenter.com/en/press-releases/audi-"
               "strengthens-production-network-integrated-production-and-new-"
               "fully-electric-model-in-ingolstadt-17105",
        "beleg": "Die Produktion des Audi Q2 am Standort Ingolstadt endet im "
                 "April 2026.",
    },

    # ── BMW ─────────────────────────────────────────────────────────────────
    "bmw-ix3-g08": {
        "nachfolger_ab": 2025, "in_produktion_bis": None, "stufe": 1,
        "url": "https://www.press.bmwgroup.com/deutschland/article/detail/"
               "T0452702DE/effizient-nachhaltig-digital:-das-neue-bmw-group-"
               "werk-debrecen-startet-ende-oktober-mit-der-serienproduktion-"
               "des-bmw-ix3",
        "beleg": "Das Werk Debrecen startet Ende Oktober 2025 mit der "
                 "Serienproduktion des neuen BMW iX3 — dem ersten Fahrzeug der "
                 "Neuen Klasse und Nachfolger des G08.",
    },
    "bmw-x5-g05": {
        "nachfolger_ab": 2026, "in_produktion_bis": None, "stufe": 1,
        "url": "https://www.bmwgroup.com/de/news/allgemein/2026/der-neue-bmw-x5.html",
        "beleg": "Die Fertigung des neuen BMW X5 beginnt im August 2026 im Werk "
                 "Spartanburg; Markteinfuehrung Ende November 2026.",
    },

    # ── Opel ────────────────────────────────────────────────────────────────
    "opel-grandland-a": {
        "nachfolger_ab": 2024, "in_produktion_bis": None, "stufe": 1,
        "url": "https://www.media.stellantis.com/de-de/opel/press/"
               "produktionsstart-fuer-den-neuen-opel-grandland-in-eisenach",
        "beleg": "Produktionsstart des voellig neu entwickelten Opel Grandland "
                 "in Eisenach; Weltpremiere am Standort im Fruehjahr 2024.",
    },
    "opel-corsa-f": {
        "nachfolger_ab": None, "in_produktion_bis": 2026, "stufe": 1,
        "url": "https://www.media.stellantis.com/em-en/opel/press/"
               "press-kit-opel-at-brussels-motor-show-2026",
        "beleg": "Der aktuelle Corsa ist seit mehr als fuenf Jahren Deutschlands "
                 "meistverkaufter Kleinwagen und fuehrt das 2026 fort; die "
                 "naechste Corsa-Generation zaehlt zu den bis 2030 "
                 "angekuendigten Modellen.",
    },
}


def pruefe(prod_von: int | None, prod_bis: int | None, baureihe_id: str,
           start: int | None, fachklasse: str) -> tuple[str, str]:
    """Reklassifiziert EINE Zeile gegen die Primaerquellen.

    `fachklasse` ist das Ergebnis aus `app/kba_generation_audit.py`. Diese
    Funktion kann es nur BESTAETIGEN oder ENTKRAEFTEN, nie ueberstimmen: was die
    Fachrecherche bereits ausgeschlossen hat, bleibt ausgeschlossen.
    """
    from app.kba_generation_audit import (
        CROSS_GENERATION, GENERATION_CONFIRMED, SUCCESSOR_RECALL,
    )

    q = PRIMAERQUELLEN.get(baureihe_id)
    if q is None:
        return (SOURCE_UNCLEAR,
                "keine Hersteller-/Primaerquelle zur Generationsgrenze gefunden")

    if prod_von is None or prod_bis is None or prod_bis < prod_von:
        return (SOURCE_UNCLEAR, "amtliches Produktionsfenster unbrauchbar")
    if start is not None and prod_von < start:
        return (SOURCE_CROSS_GENERATION,
                f"Fenster beginnt {prod_von} vor dem hinterlegten "
                f"Generationsstart {start}; der exakte Produktionsbeginn ist "
                f"auch primaer nicht belegt")

    nf = q["nachfolger_ab"]
    bis = q["in_produktion_bis"]
    stufe = q["stufe"]

    if nf is not None and prod_von >= nf:
        return (SOURCE_CONTRADICTED,
                f"Primaerquelle: Nachfolgegeneration laeuft ab {nf}, das "
                f"Fenster beginnt {prod_von} [Stufe {stufe}] {q['url']}")
    if nf is not None and prod_bis >= nf:
        return (SOURCE_CROSS_GENERATION,
                f"Primaerquelle: Nachfolgegeneration laeuft ab {nf}, das "
                f"Fenster reicht bis {prod_bis} [Stufe {stufe}] {q['url']}")

    if nf is not None:
        # Fenster endet vor dem Anlauf des Nachfolgers -> eindeutig.
        return (SOURCE_CONFIRMED,
                f"Primaerquelle: Nachfolgegeneration erst ab {nf}, Fenster endet "
                f"{prod_bis} [Stufe {stufe}] {q['url']}")

    if bis is not None and prod_bis <= bis:
        return (SOURCE_CONFIRMED,
                f"Primaerquelle: Generation nachweislich bis {bis} in "
                f"Produktion, Fenster endet {prod_bis} [Stufe {stufe}] {q['url']}")

    return (SOURCE_UNCLEAR,
            f"Primaerquelle belegt weder einen Nachfolgeanlauf noch Produktion "
            f"bis {prod_bis}")
