"""
Listing-Evidence-Provenance und saubere Card-Grenzen — deterministisch, KEIN Netzwerk.

Hintergrund (forensischer Audit der BMW-320d-G20-Läufe):

  1. Ein BMW 320d GT (3GT/F34) lag auf einer nach "Limousine" GEFILTERTEN
     Trefferliste. Aus dem URL-Filter "autos.typ_s:limousine" erbte das Inserat
     body=limousine, daraus wurde über die Chassiscode-Zuordnung G20 abgeleitet —
     und es war als einziger "sehr ähnlich"-Treffer der stärkste Anker des Medians.
     Ein Suchseiten-Kontext ist KEINE Listing-Identität.

  2. Die Kartensegmente endeten mit dem Vorschau-/Linkblock der FOLGENDEN Anzeige
     ("Gran Turismo" im Nachbarblock, GT im Nachbar-Alt-Text).

Fälle A-H aus der Aufgabenstellung.

    python test_listing_evidence_provenance.py
"""
import os
import sys
import tempfile

sys.path.insert(0, ".")
os.environ.setdefault("AUTO_KI_DB_PATH",
                      os.path.join(tempfile.mkdtemp(prefix="vira_prov_"), "test.db"))

from types import SimpleNamespace                                        # noqa: E402

from app.chassis_codes import VERIFIZIERTE_CHASSIS_CODES                 # noqa: E402
from app.market_card_segmenter import segmentiere                        # noqa: E402
from app.marktvergleich import (                                         # noqa: E402
    _bewerte, _eindeutige_karosserie, _extrahiere_aus_text, _identitaets_body,
    analysiere_markt, baue_ziel,
)

_fails: list[str] = []


def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        _fails.append(name)


# ── Zielprofil: G20 (Limousine) ist Ziel, G21 (Touring) ist Fremdgeneration ──
G20_FAMILIE = {"marke": "BMW", "modell": "3er", "generation": "G20/G21",
               "id": "bmw-3er-g20-g21", "karosserie": ["Limousine", "Touring"],
               # §DB-Trust: die Chassis-Inference ist nur mit VERIFIZIERTEM Fakt erlaubt.
               # Dieser Test prueft den Mechanismus, nicht die Vertrauensregel — die
               # Vorbedingung wird deshalb hier ausdruecklich gesetzt. Die Trust-Regel
               # selbst deckt test_db_trust.py ab (Faelle I und J).
               "verification": {"chassis_codes": {"status": "verified",
                                                 "source": "Testfixture"}},
               "chassis_codes": VERIFIZIERTE_CHASSIS_CODES["bmw-3er-g20-g21"]}
ALLE = [G20_FAMILIE, {"id": "bmw-3er-f30", "marke": "BMW", "modell": "3er",
                      "generation": "F30"}]
MOTOREN = [{"baureihe_id": "bmw-3er-g20-g21", "bezeichnung": "320d", "motorcode": "B47D20"}]
MOTOR = {"bezeichnung": "320d", "kraftstoff": "Diesel", "leistung_ps": 190,
         "motorcode": "B47D20"}
REQ = SimpleNamespace(marke="BMW", modell="3er G20", motor="320d", kraftstoff="Diesel",
                      baujahr=2019, kilometerstand=120_000)
ZIEL = baue_ziel(G20_FAMILIE, MOTOR, REQ, ALLE, MOTOREN)

# Die REALE Suchseiten-URL trägt den Karosserie-Filter im Pfad.
URL_LIMO = ("https://www.kleinanzeigen.de/s-autos/bmw-320d-2019/"
            "k0c216+autos.typ_s:limousine")
TITEL = "BMW 320d 2019 gebraucht kaufen"
NAV = "## Filter\n\n### Preis\n\n## Ergebnisse\n\n"

check("Vorbedingung: der URL-Filter wird als Seiten-Karosserie erkannt",
      _eindeutige_karosserie(URL_LIMO + " " + TITEL) == "limousine")


def _karte(titel, slug, lid, preis, km, ez, beschreibung):
    """Reale Kleinanzeigen-Struktur: Listenpunkt mit Vorschaublock, dann Heading."""
    return ("* [![" + titel + " Berlin - Mitte Vorschau]"
            "(https://img.kleinanzeigen.de/api/v1/prod-ads/images/" + lid + ".jpg)\n\n"
            "  20](/s-anzeige/" + slug + "/" + lid + "-216-3412)\n\n"
            "  12307 Berlin\n\n"
            "  Heute, 10:59\n\n"
            "  ## [" + titel + "](/s-anzeige/" + slug + "/" + lid + "-216-3412)\n\n"
            "  " + beschreibung + "\n\n"
            "  " + preis + " €\n\n"
            "  " + km + " km   EZ " + ez + "\n")


def _punkte(raw, url=URL_LIMO, titel=TITEL):
    """Fährt eine Seite durch die ECHTE Kette und gibt {preis: Beobachtung} zurück."""
    seite = {"url": url, "title": titel, "content": "", "raw_content": raw}
    ma = analysiere_markt([seite], ZIEL, None)
    text = titel + "\n\n" + raw
    roh = _extrahiere_aus_text(text, url, "market_category",
                               grenzen=(len(titel) + 1, len(titel) + 2),
                               seiten_body=_eindeutige_karosserie(url + " " + titel))
    return ma, {b.preis_eur: b for b in (_bewerte(x, ZIEL) for x in roh)}


def _median_preise(ma):
    return sorted(b.preis_eur for b in (ma.beobachtungen or []))


# ══ A — Seite sagt Limousine, Karte sagt nichts -> keine Inference ══════════
A_RAW = (NAV
         + _karte("BMW 320d Advantage Automatik", "bmw-320d-advantage-automatik",
                  "3470000001", "24.900", "118.000", "05/2019",
                  "Scheckheftgepflegt, zwei Vorbesitzer, TÜV neu...")
         + _karte("BMW 320d Sport Line", "bmw-320d-sport-line",
                  "3470000002", "25.400", "121.000", "06/2019",
                  "Sehr gepflegter Wagen aus erster Hand...")
         + _karte("BMW 320d Advantage", "bmw-320d-advantage",
                  "3470000003", "25.900", "117.000", "07/2019",
                  "Wird aus Altersgruenden verkauft..."))
ma_a, p_a = _punkte(A_RAW)
b_a = p_a[24900]
check("A: die Karte selbst nennt keine Karosserie -> Herkunft page_context",
      b_a.body == "limousine" and b_a.body_evidence == "page_context")
check("A: der Seiten-Kontext gilt NICHT als Listing-Identitaet",
      _identitaets_body(b_a) is None)
check("A: keine G20-Inference aus dem Suchseiten-Filter",
      b_a.generation is None and b_a.generation_evidence == "unknown")
check("A: ohne belegte Generation zaehlt die Karte hoechstens bedingt",
      b_a.vergleichbarkeit in ("bedingt", "ungeeignet"))
check("A: kein Datenpunkt der Seite wird als G20 ausgewiesen",
      all(b.generation is None for b in p_a.values()))

# ══ B — Seite sagt Limousine, Karte ist ein 3er GT (F34) ═══════════════════
# Der Gold-Regressionsfall 3484898357, nachgebaut mit seinem realen Wortlaut.
B_RAW = (NAV
         + _karte("BMW 320d GT M Sport 360 Ad.LED HUD AHK CarPlay",
                  "bmw-320d-gt-m-sport-360-ad-led-hud-ahk-carplay",
                  "3484898357", "16.900", "131.500", "05/2019",
                  "Zum Verkauf steht ein sehr gepflegter BMW 3GT mit einer "
                  "originalen und geringen Laufleistung von...")
         + _karte("BMW 320d Advantage", "bmw-320d-advantage",
                  "3470000009", "24.900", "118.000", "05/2019",
                  "Scheckheftgepflegt, zwei Vorbesitzer..."))
ma_b, p_b = _punkte(B_RAW)
b_gt = p_b[16900]
check("B: der GT erbt die Karosserie NICHT als Identitaet",
      _identitaets_body(b_gt) is None and b_gt.body_evidence == "page_context")
check("B: der GT wird NICHT als G20 inferiert",
      b_gt.generation is None and b_gt.generation_evidence != "inferred_database")
check("B: der GT ist kein sehr aehnlicher Vergleich mehr",
      b_gt.vergleichbarkeit != "sehr_aehnlich")
check("B: der GT traegt keinen Median mehr", 16900 not in _median_preise(ma_b))
# Dokumentation, KEINE Filterlogik in diesem Schritt:
check("B (nur dokumentiert): der eigene Text nennt GT und 3GT",
      "3GT" in B_RAW and "GT M Sport" in B_RAW)

# ══ C — Karte nennt selbst "Limousine" -> Inference erlaubt ════════════════
C_RAW = (NAV
         + _karte("BMW 320d Limousine Aut. Advantage", "bmw-320d-limousine-aut-advantage",
                  "3480000001", "24.900", "118.000", "05/2019",
                  "Limousine aus erster Hand, scheckheftgepflegt...")
         + _karte("BMW 320d Limousine Sport Line", "bmw-320d-limousine-sport-line",
                  "3480000002", "25.400", "121.000", "06/2019",
                  "Gepflegte Limousine, TÜV neu...")
         + _karte("BMW 320d Limousine Advantage", "bmw-320d-limousine-advantage",
                  "3480000003", "25.900", "117.000", "07/2019",
                  "Limousine, zweite Hand..."))
ma_c, p_c = _punkte(C_RAW)
b_c = p_c[24900]
check("C: die eigene Karte belegt die Karosserie -> Herkunft card",
      b_c.body == "limousine" and b_c.body_evidence == "card")
check("C: listing-eigene Evidence gilt als Identitaet",
      _identitaets_body(b_c) == "limousine")
check("C: G20-Inference bleibt erlaubt",
      b_c.generation == "G20" and b_c.generation_evidence == "inferred_database")
check("C: diese Karten tragen den Median",
      _median_preise(ma_c) == [24900, 25400, 25900])

# ══ D — Karte nennt Touring/Kombi -> G21 wie bisher ════════════════════════
D_RAW = (NAV
         + _karte("BMW 320d Touring M-Paket", "bmw-320d-touring-m-paket",
                  "3481000001", "27.900", "118.000", "05/2019",
                  "Verkauft wird ein gepflegter BMW 320d Touring...")
         + _karte("BMW 320d Limousine Advantage", "bmw-320d-limousine-advantage",
                  "3481000002", "24.900", "121.000", "06/2019",
                  "Limousine, scheckheftgepflegt..."))
ma_d, p_d = _punkte(D_RAW)
b_d = p_d[27900]
check("D: Touring wird aus der eigenen Karte belegt",
      b_d.body == "kombi" and b_d.body_evidence == "card")
check("D: daraus folgt G21 -> Fremdgeneration, verworfen",
      b_d.generation_evidence == "inferred_database"
      and b_d.vergleichbarkeit == "ungeeignet"
      and any("G21" in g for g in b_d.gruende))
check("D: der Touring traegt keinen Median", 27900 not in _median_preise(ma_d))

# ══ E/F/G — Kartengrenzen: der Vorschaublock gehoert zur FOLGENDEN Karte ═══
E_RAW = (NAV
         + _karte("BMW 320d Sport Line", "bmw-320d-sport-line",
                  "3486676725", "26.900", "92.000", "09/2019",
                  "TÜV neu gemacht. Batterie, Partikelfilter neu gemacht...")
         + _karte("BMW 320d Gran Turismo M Sport LED PDC Kamera ACC",
                  "bmw-320d-gran-turismo-m-sport-led-pdc-kamera-acc",
                  "3465934070", "20.500", "104.000", "03/2019",
                  "Gran Turismo aus zweiter Hand..."))
seg_e, verf_e = segmentiere(TITEL + "\n\n" + E_RAW, URL_LIMO,
                            titel_ende=len(TITEL) + 1)
check("E: Verfahren bleibt detail_link", verf_e == "detail_link")
check("E: genau zwei Karten", len(seg_e) == 2)
k1, k2 = (seg_e + seg_e)[0], (seg_e + seg_e)[1]
check("E: Karte N enthaelt KEIN 'Gran Turismo' mehr",
      "Gran Turismo" not in k1.text)
check("E: Karte N enthaelt weder Alt-Text noch ID der Nachbarkarte",
      "3465934070" not in k1.text and "20.500" not in k1.text)
check("F: Karte N+1 behaelt ihren vollstaendigen eigenen Titel",
      "## [BMW 320d Gran Turismo M Sport LED PDC Kamera ACC]" in k2.text)
check("F: Karte N+1 behaelt Beschreibung, Preis, km und EZ",
      "Gran Turismo aus zweiter Hand" in k2.text and "20.500 €" in k2.text
      and "104.000 km" in k2.text and "EZ 03/2019" in k2.text)
check("F: Karte N behaelt ihre eigenen Werte vollstaendig",
      "26.900 €" in k1.text and "92.000 km" in k1.text and "EZ 09/2019" in k1.text
      and "TÜV neu gemacht" in k1.text)
check("G: beide Karten behalten ihre eigene Listing-ID",
      k1.detected_listing_id == "3486676725" and k2.detected_listing_id == "3465934070")
check("G: beide Karten behalten ihren aufgeloesten Detail-Link",
      (k1.detected_detail_url or "").endswith("/bmw-320d-sport-line/3486676725-216-3412")
      and (k2.detected_detail_url or "").endswith(
          "/bmw-320d-gran-turismo-m-sport-led-pdc-kamera-acc/3465934070-216-3412"))
check("G: strukturelle Konfidenz bleibt hoch",
      all(s.structural_confidence == "high" for s in seg_e))
check("G (§5): das EIGENE Vorschaubild bleibt in der Karte",
      k1.text.lstrip().startswith("* [![BMW 320d Sport Line")
      and "3465934070.jpg" in k2.text)

# ── Der GT-Alt-Text darf nicht in die Vorgaengerkarte lecken ───────────────
GT_RAW = (NAV
          + _karte("BMW 320d Aut. HUD LED Digital Tacho Scheckheft",
                   "bmw-320d-aut-hud-led-digital-tacho-scheckheft",
                   "3485109364", "21.900", "150.000", "04/2019",
                   "BMW 320d Automatik, zwei Vorbesitzer...")
          + _karte("BMW 320d GT M Sport 360 Ad.LED HUD AHK CarPlay",
                   "bmw-320d-gt-m-sport-360-ad-led-hud-ahk-carplay",
                   "3484898357", "16.900", "131.500", "05/2019",
                   "Zum Verkauf steht ein sehr gepflegter BMW 3GT..."))
seg_gt, _verf_gt = segmentiere(TITEL + "\n\n" + GT_RAW, URL_LIMO,
                               titel_ende=len(TITEL) + 1)
check("E2: der GT-Alt-Text leckt nicht in die Vorgaengerkarte",
      len(seg_gt) == 2 and "GT M Sport" not in seg_gt[0].text
      and "3484898357" not in seg_gt[0].text)
check("E2: der GT behaelt seinen eigenen Alt-Text und Titel",
      len(seg_gt) == 2 and "GT M Sport" in seg_gt[1].text and "3GT" in seg_gt[1].text)

print()
if _fails:
    print(str(len(_fails)) + " FEHLGESCHLAGEN:")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("Alle Provenance-/Kartengrenzen-Tests bestanden.")
