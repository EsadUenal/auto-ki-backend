"""
Geltungsbereich einzelner Motorvarianten (AF-03): belegte Bauzeit-Grenzen und
nicht bestätigte Getriebe-Angaben.

WARUM
-----
`motorvariante` kennt keine Bauzeit. Der AutoFinder musste die Generationsjahre
als Motorjahre ausgeben, und eine Zeile wie "116i, 136 PS, 8-Gang Automatik" galt
für die ganze Generation 2011-2019 — obwohl der 116i ab der Modellpflege 2015 ein
Dreizylinder mit 109 PS ohne Achtgang-Steptronic ist.

Die Tabelle `motorvariante_geltung` (db/schema.sql) trägt je Variante OPTIONAL

    bauzeit_von / bauzeit_bis   belegte Grenzen, NULL = nicht hinterlegt
    getriebe_unbestaetigt       JSON-Array der Getriebe-Einträge dieser Variante,
                                die NICHT bestätigt sind (sie zählen weder für den
                                Getriebefilter noch für "konkret")
    quelle / notiz              Beleg und Grenzen dieser Zeile (Pflicht)

Es gibt KEINE Fahrzeug-ID-Liste im Anwendungscode: der Code liest nur diese Tabelle.
Eine Variante ohne Zeile verhält sich wie bisher (Generationsjahre, Getriebe wie
hinterlegt, Motorzeitraum "nicht hinterlegt").

BELEGE (Primärquellen, selbst gelesen)
--------------------------------------
BMW Medieninformation 01/2015 "Die neue BMW 1er Reihe." (Technische Daten, S. 30/31
und 48/49): 116i = Dreizylinder 1499 cm³, 80 kW/109 PS, 180 Nm, 6-Gang manuell,
0-100 km/h 10,9 s, 195 km/h. 118i = 1598 cm³, 100 kW/136 PS, 220 Nm, 6-Gang manuell,
optional 8-Gang Steptronic.
BMW Medieninformation 5/2017 "BMW 1er" (Technische Daten, gültig ab 7/2017, S. 1/2 und
19/20): 116i = 1499 cm³, 80 kW/109 PS, 180 Nm, nur 6-Gang manuell (kein Steptronic).
Das 118i hat dort 100 kW/136 PS, 6-Gang manuell, optional 8-Gang Steptronic (S. 3/4).
Quelle: newsroom.bmwgroup.com (frühere URL press.bmwgroup.com/deutschland/article/
attachment/T0270405DE/387659 leitet dorthin um). Abgerufen 2026-11-23.

ADAC Autokatalog, abgerufen 2026-11-23:
  .../vw/golf/viii/313514/  "VW Golf 1.0 TSI UNITED (07/20 - 12/20)": 81 kW (110 PS),
      Getriebe Schaltgetriebe, 6 Gänge, Modellstart 07/20, Modellende 12/20.
  .../vw/golf/viii/314494/  "VW Golf 1.0 eTSI Life DSG (08/20 - 02/24)": Otto
      (Mild-Hybrid), 81 kW (110 PS), Automat. Schaltgetriebe (Doppelkupplung), 7 Gänge.
Beide Seiten beschreiben EINZELNE Ausführungen. Daraus wird bewusst KEIN allgemeiner
Motorzeitraum abgeleitet, sondern nur: für den 1.0 TSI ist eine Ausführung mit
Schaltgetriebe belegt, die DSG gehört in diesen Quellen zum eTSI.
"""

BMW_QUELLE_2015_2017 = (
    "BMW Medieninformation 01/2015 (Die neue BMW 1er Reihe, Technische Daten) und "
    "5/2017 (gültig ab 7/2017), newsroom.bmwgroup.com, abgerufen 2026-11-23")

# Neue, belegte Variante: der 116i der Modellpflege. Nur Felder, die die Quelle nennt;
# alles andere bleibt NULL (nichts geraten).
NEUE_VARIANTEN = [
    {
        "variante_id": "bmw-1er-f20-f21-116i-lci",
        "baureihe_id": "bmw-1er-f20-f21",
        "bezeichnung": "116i",
        "motorcode": None,            # in der Quelle nicht genannt
        "kraftstoff": "Benzin",
        "hubraum_ccm": 1499,
        "zylinder": 3,                # "Dreizylinder-Ottomotor" (01/2015), "R / 3 / 4" (Tabelle)
        "leistung_ps": 109,
        "leistung_kw": 80,
        "drehmoment_nm": 180,
        "getriebe": '["6-Gang manuell"]',
        "antrieb": "Heck",            # wie bei allen F20/F21-Varianten des Bestands
        "beschleunigung_0_100": 10.9,
        "vmax_kmh": 195,
        "heck_emblem": "116i",
    },
]

# variante_id -> (bauzeit_von, bauzeit_bis, getriebe_unbestaetigt, quelle, notiz)
GELTUNG = {
    "bmw-1er-f20-f21-116i-lci": (
        2015, None, None, BMW_QUELLE_2015_2017,
        "Ab der Modellpflege 2015 (Medieninformation 01/2015) ist der 116i ein "
        "Dreizylinder mit 109 PS und nur 6-Gang manuell; 2017 unverändert. Die Untergrenze "
        "ist das Jahr dieser Medieninformation. Ein Ende der Bauzeit ist nicht belegt "
        "(dann gilt das Generationsende)."),
    "bmw-1er-f20-f21-116i": (
        None, 2015, None, BMW_QUELLE_2015_2017,
        "Obergrenze aus der Quelle: ab der Modellpflege 2015 ist der 116i ein Dreizylinder mit "
        "109 PS ohne Achtgang-Steptronic. Eine Ausführung mit 136 PS und 8-Gang Automatik "
        "kann deshalb nicht für spätere Zulassungen gelten. Beginn der Bauzeit und die "
        "Getriebeangabe dieser Zeile sind durch die Quelle nicht belegt und bleiben unverändert."),
    "bmw-1er-f20-f21-118i": (
        None, 2015, None, BMW_QUELLE_2015_2017,
        "Obergrenze aus der Quelle: in der Modellpflege-Baureihe ab 2015 ist der 118i mit "
        "100 kW/136 PS angegeben, nicht mit 170 PS. Beginn der Bauzeit nicht belegt."),
    "volkswagen-golf-viii-1.0-tsi-81-kw": (
        None, None, ["7-Gang-DSG"],
        "ADAC Autokatalog Golf 1.0 TSI UNITED (313514) und Golf 1.0 eTSI Life DSG (314494), "
        "abgerufen 2026-11-23",
        "Für den 1.0 TSI (81 kW) ist in den Quellen eine Ausführung mit 6-Gang-Schaltgetriebe "
        "belegt; die 7-Gang-DSG gehört dort zum 1.0 eTSI (Mild-Hybrid). Ein 1.0 TSI mit DSG "
        "ist damit nicht belegt und zählt nicht als bestätigte Auswahl. Ein Motorzeitraum "
        "wird aus den beiden Einzelausführungen nicht abgeleitet."),
}


# ═══════════════════════════════════════════════════════════════════════════
# AF-10/12/07: Gasantrieb, Verbrauchseinheit/-zyklus und Golf-VII-1.2-TSI-Bauzeiten
# ═══════════════════════════════════════════════════════════════════════════
# Belege (gelesen, abgerufen 2026-11-24):
#   VW Sverige, Pressemitteilung 23.10.2020 "Gasdrivna Volkswagen Golf TGI öppen för beställning":
#     Golf TGI = 1,5 l Benzinmotor angepasst für Gasbetrieb, 130 PS, 200 Nm, "drivs främst med
#     fordonsgas (CNG), men kan också köras på bensin"; Verbrauch "från 4,1 kg/100 km" (WLTP),
#     Gastank 115 l bzw. 17,3 kg, Benzintank 9 l, der Benzintank ist im Wesentlichen eine Reserve.
#   VW Newsroom "Motorversionen Golf 7 Steckbrief" (Motorübersicht der Generation, keine
#     Ausstattungslinie): 1.2 TSI 63 kW/85 PS (2013–2015), 1.2 TSI BlueMotion 77 kW/105 PS (2013–2015),
#     1.2 TSI EVO 81 kW/110 PS (2015–2016). Die Seite führt die 105-PS-Ausführung als "BlueMotion".
#
# Die übrigen Gasfahrzeuge des Bestands tragen nur die Kennzeichnung "Gasfahrzeug laut
# Variantenbezeichnung" (TGI, CNG, LPG, g-tron, EcoFuel, BiFuel). Gasart, Primärkraftstoff,
# Verbrauchseinheit und Zyklus sind dort NICHT geprüft und bleiben unbekannt (keine kg-Vergabe).

VW_TGI_QUELLE = ("VW Sverige, Pressemitteilung 23.10.2020 (Golf TGI), vwgroup.se, abgerufen 2026-11-24")
VW_GOLF7_QUELLE = ("VW Newsroom, Motorversionen Golf 7 Steckbrief (volkswagen-newsroom.com/de/"
                   "motorversionen-golf-7-steckbrief-20040), abgerufen 2026-11-24")
GAS_UNGEPRUEFT_QUELLE = "Variantenbezeichnung im ENFAL-Datensatz (Gasantrieb laut Bezeichnung)"
GAS_UNGEPRUEFT_HINWEIS = "Gasfahrzeug laut Variantenbezeichnung (Gasart, Primärkraftstoff und Verbrauchseinheit nicht geprüft)"

GAS_UNGEPRUEFT = (
    "audi-a3-typ-8v-g-tron",
    "opel-astra-k-1.4-turbo-lpg-140-ps",
    "opel-karl-a-1.0-lpg",
    "opel-zafira-b-1.6-cng-turbo-a16xnt",
    "opel-zafira-b-1.6-cng-z16yng",
    "opel-zafira-c-1.6-cng-turbo-150ps",
    "seat-arona-erste-generation-1.0-tgi-90-ps",
    "seat-leon-dritte-generation-1.4-tgi-110ps",
    "volkswagen-caddy-iii-1.4-tgi-110ps",
    "volkswagen-caddy-iii-2.0-ecofuel-109ps",
    "volkswagen-golf-vi-1.6-bi-fuel",
    "volkswagen-passat-b7-1.4-tsi-ecofuel",
    "volkswagen-polo-vi-1.0-tgi-90-ps",
    "volkswagen-touran-i-2.0-ecofuel",
    "volkswagen-up-up-(facelift)-1.0-tgi-(68-ps)",
)

# variante_id -> Felder (nur gesetzte Schlüssel gelten, der Rest bleibt NULL)
GELTUNG_AF10 = {
    "volkswagen-golf-viii-1.5-tgi": dict(
        antriebsgas="CNG", verbrauch_einheit="kg", verbrauch_zyklus="WLTP",
        kraftstoff_hinweis="primär Erdgas (CNG), Benzin nur als Reserve",
        quelle=VW_TGI_QUELLE,
        notiz="Der Verbrauch 4,1 ist die VW-Angabe 4,1 kg/100 km (CNG, WLTP), keine Liter. Ein "
              "Benzinverbrauch in l/100 km ist in der Quelle nicht angegeben. Der Benzintank (9 l) ist "
              "eine Reserve und macht den Wagen nicht zum gewöhnlichen Benziner."),
    "volkswagen-golf-vii-1.2-tsi-77kw": dict(
        bauzeit_von=2013, bauzeit_bis=2015, quelle=VW_GOLF7_QUELLE,
        notiz="Motorübersicht der Generation: 1.2 TSI BlueMotion 77 kW/105 PS, Bauzeit 2013–2015. "
              "Die Seite führt die Ausführung als BlueMotion; die Zuordnung zur Datensatzzeile "
              "ergibt sich über Hubraum und Leistung. Eine spätere Erstzulassung ist nicht belegt."),
    "volkswagen-golf-vii-1.2-tsi-63kw": dict(
        bauzeit_von=2013, bauzeit_bis=2015, quelle=VW_GOLF7_QUELLE,
        notiz="Motorübersicht der Generation: 1.2 TSI 63 kW/85 PS, Bauzeit 2013–2015. Eine spätere "
              "Erstzulassung ist nicht belegt."),
}
for _vid in GAS_UNGEPRUEFT:
    GELTUNG_AF10[_vid] = dict(antriebsgas="Gas", kraftstoff_hinweis=GAS_UNGEPRUEFT_HINWEIS,
                              quelle=GAS_UNGEPRUEFT_QUELLE,
                              notiz="Nur die Kennzeichnung als Gasfahrzeug. Verbrauchseinheit und -zyklus unbekannt.")

GELTUNG_AF10_SPALTEN = ("bauzeit_von", "bauzeit_bis", "getriebe_unbestaetigt", "antriebsgas",
                        "verbrauch_einheit", "verbrauch_zyklus", "kraftstoff_hinweis", "quelle", "notiz")
