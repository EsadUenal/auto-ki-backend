"""
Fahrzeugkarten-Segmentierung (app/market_card_segmenter.py) — deterministisch,
KEIN Netzwerk.

Hintergrund: der Offline-Nachweis der Diagnose-Persistenz hat belegt, dass das alte
Zeichenfenster Karten mitten durchschneidet ("05/2019 . BMW 320d … EZ " — Datumsrest
der Vorgängerkarte vorn, eigenes Baujahr abgeschnitten). Die Tests hier fahren
realistische synthetische Trefferlisten durch die ECHTE Extraktionskette und weisen
nach, dass keine Attribute mehr zwischen Karten wandern.

Fälle A-G aus der Aufgabenstellung:
  A  drei BMW-Karten hintereinander (320d, 320d, 320i)
  B  320d G20 direkt gefolgt von 330i G20
  C  Karte ohne Motorangabe
  D  Karte mit zwei Preisen (alter/neuer Preis)
  E  zwei Fahrzeuge mit gleichem Baujahr und ähnlichen Kilometern
  F  Markdown-Detail-Links je Karte
  G  Text ohne erkennbare Kartenstruktur

    python test_card_segmenter.py
"""
import os
import sys
import tempfile

sys.path.insert(0, ".")

# §Source-Policy: Der Production-Default gibt KEINE Marktquelle zum Preisbilden
# frei (app/config.ALLOWED_MARKET_SOURCES ist leer). Dieser Test prueft die
# ANALYSE-ENGINE und braucht dafuer die historischen/synthetischen Testdomains —
# die Freigabe gilt ausschliesslich in diesem Testprozess und ist KEINE
# produktive Qualifikation der Quelle. Siehe _source_policy_testharness.py.
import _source_policy_testharness  # noqa: E402,F401
os.environ.setdefault("AUTO_KI_DB_PATH",
                      os.path.join(tempfile.mkdtemp(prefix="vira_seg_"), "test.db"))

from types import SimpleNamespace                                        # noqa: E402

from app.market_card_segmenter import (                                  # noqa: E402
    CardSegment, _listing_id, aufgeloester_detail_link, segmentiere, validiere_karte,
)
from app.marktvergleich import (                                         # noqa: E402
    _bewerte, _eindeutige_karosserie, _extrahiere_aus_text, analysiere_markt, baue_ziel,
)

_fails: list[str] = []


def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        _fails.append(name)


# ══ Zielprofil: BMW 320d G20, 2019, 190 PS, Diesel, 120.000 km ══════════════
BAUREIHE = {"marke": "BMW", "modell": "3er", "generation": "G20", "id": "bmw-3er-g20"}
ALLE = [BAUREIHE, {"id": "bmw-3er-f30", "marke": "BMW", "modell": "3er", "generation": "F30"}]
MOTOREN = [
    {"baureihe_id": "bmw-3er-g20", "bezeichnung": "320d", "motorcode": "B47D20"},
    {"baureihe_id": "bmw-3er-g20", "bezeichnung": "320i"},
    {"baureihe_id": "bmw-3er-g20", "bezeichnung": "330i"},
]
REQ = SimpleNamespace(marke="BMW", modell="3er G20", motor="320d", kraftstoff="Diesel",
                      baujahr=2019, kilometerstand=120_000)
ZIEL = baue_ziel(BAUREIHE, {"bezeichnung": "320d", "kraftstoff": "Diesel",
                            "leistung_ps": 190, "motorcode": "B47D20"},
                 REQ, ALLE, MOTOREN)

URL = "https://www.kleinanzeigen.de/s-autos/bmw-320d-g20/k0c216"


def _seite(content, url=URL, titel="BMW 320d G20 gebraucht kaufen", raw=""):
    return {"url": url, "title": titel, "content": content, "raw_content": raw}


def _punkte(content, **kw):
    """(Marktanalyse, ALLE bewerteten Beobachtungen der Seite).

    Die zweite Rückgabe enthält bewusst auch die VERWORFENEN Punkte — nur so lässt
    sich prüfen, welche Attribute eine verworfene Nachbarkarte getragen hat und ob
    davon etwas übergesprungen ist. `analysiere_markt` gibt verworfene Punkte
    (richtigerweise) nicht heraus, deshalb wird die Extraktion hier mit denselben
    Bausteinen nachgebaut.
    """
    seite = _seite(content, **kw)
    ma = analysiere_markt([seite], ZIEL, None)
    titel, inhalt = seite["title"], seite["content"]
    raw = seite["raw_content"]
    text = f"{titel}\n{inhalt}\n{raw}"
    grenzen = (len(titel) + 1, len(titel) + 1 + len(inhalt) + 1)
    roh = _extrahiere_aus_text(text, seite["url"], "market_category", grenzen=grenzen,
                               seiten_body=_eindeutige_karosserie(f"{seite['url']} {titel}"))
    return ma, [_bewerte(b, ZIEL) for b in roh]


def _nach_preis(punkte):
    return {b.preis_eur: b for b in punkte}


def _preise_ma(ma):
    return [b.preis_eur for b in ma.beobachtungen]


# ══ A — drei BMW-Karten hintereinander ══════════════════════════════════════
A_TEXT = ("BMW 320d G20 Limousine 24.900 € 118.000 km EZ 05/2019\n"
          "BMW 320d G20 Limousine 25.400 € 121.000 km EZ 06/2019\n"
          "BMW 320i G20 Limousine 27.900 € 119.000 km EZ 04/2019")
ma_a, punkte_a = _punkte(A_TEXT)
p_a = _nach_preis(punkte_a)

check("A: alle drei Karten wurden getrennt erkannt", len(punkte_a) == 3)
check("A: strukturell segmentiert, kein Zeichenfenster",
      all(not b.window_fallback_used for b in punkte_a))
check("A: Karte 1 behält ihre eigenen Attribute (118.000 km / 2019)",
      p_a[24900].kilometerstand == 118_000 and p_a[24900].baujahr == 2019)
check("A: Karte 2 behält ihre eigenen Attribute (121.000 km / 2019)",
      p_a[25400].kilometerstand == 121_000 and p_a[25400].baujahr == 2019)
check("A: Karte 3 behält ihre eigenen Attribute (119.000 km / 2019)",
      p_a[27900].kilometerstand == 119_000 and p_a[27900].baujahr == 2019)
check("A: kein Kilometerstand wandert zwischen Karten",
      sorted(b.kilometerstand for b in punkte_a) == [118_000, 119_000, 121_000])
check("A: der 320i wird als andere Motorvariante verworfen",
      27900 not in [b.preis_eur for b in ma_a.beobachtungen])
check("A: die 320i-Motorangabe landet NICHT beim 320d-Nachbarn",
      p_a[25400].engine_variant == "320d" and p_a[24900].engine_variant == "320d")
check("A: kein Kartentext enthält den Preis einer Nachbarkarte",
      "25.400" not in p_a[24900].acceptance_reason
      and "27.900" not in p_a[25400].acceptance_reason)
check("A: die beiden echten 320d tragen den Median",
      sorted(b.preis_eur for b in ma_a.beobachtungen) == [24900, 25400])

# ══ B — 320d G20 direkt gefolgt von 330i G20 ════════════════════════════════
B_TEXT = ("BMW 320d G20 Diesel 24.900 € 118.000 km EZ 05/2019\n"
          "BMW 330i G20 Benzin 31.900 € 117.000 km EZ 05/2019")
ma_b, punkte_b = _punkte(B_TEXT)
p_b = _nach_preis(punkte_b)
check("B: der 330i beeinflusst den 320d-Median nicht",
      31900 not in [b.preis_eur for b in ma_b.beobachtungen])
check("B: der 320d bleibt vollwertig",
      p_b[24900].vergleichbarkeit == "sehr_aehnlich")
check("B: die Benzin-Angabe des Nachbarn färbt nicht ab",
      p_b[24900].fuel == "diesel")
check("B: die 330i-Bezeichnung färbt nicht ab", p_b[24900].engine_variant == "320d")

# ══ C — Karte ohne Motorangabe ══════════════════════════════════════════════
C_TEXT = ("BMW 320d G20 Diesel 24.900 € 118.000 km EZ 05/2019\n"
          "BMW 3er G20 25.100 € 119.000 km EZ 04/2019\n"
          "BMW 320d G20 Diesel 25.400 € 121.000 km EZ 06/2019")
ma_c, punkte_c = _punkte(C_TEXT)
p_c = _nach_preis(punkte_c)
check("C: die Karte ohne Motorangabe wird erkannt", 25100 in p_c)
check("C: sie ist höchstens conditional", p_c[25100].vergleichbarkeit == "bedingt")
check("C: sie erbt den Motor NICHT von den Nachbarkarten",
      p_c[25100].engine_variant is None)
check("C: die Karten mit Motorangabe bleiben vollwertig",
      p_c[24900].vergleichbarkeit == "sehr_aehnlich"
      and p_c[25400].vergleichbarkeit == "sehr_aehnlich")

# §2: die SEITENÜBERSCHRIFT ("BMW 320d G20 gebraucht kaufen") darf der ersten Karte
# weder Motor noch Generation vererben — sie beschreibt die Suche, nicht das Auto.
C2_TEXT = ("BMW 3er 25.100 € 119.000 km EZ 04/2019\n"
           "BMW 320d G20 Diesel 24.900 € 118.000 km EZ 05/2019\n"
           "BMW 320d G20 Diesel 25.400 € 121.000 km EZ 06/2019")
_, punkte_c2 = _punkte(C2_TEXT, titel="BMW 320d G20 gebraucht kaufen")
p_c2 = _nach_preis(punkte_c2)
check("C2: die erste Karte erbt den Motor NICHT aus der Seitenüberschrift",
      p_c2[25100].engine_variant is None)
check("C2: sie erbt auch die Generation nicht aus der Überschrift",
      p_c2[25100].generation is None)
check("C2: und ist damit höchstens conditional",
      p_c2[25100].vergleichbarkeit == "bedingt")
check("C2: der Titel steht nicht mehr im Kartentext der ersten Karte",
      "gebraucht kaufen" not in p_c2[25100].acceptance_reason)

# ══ D — Karte mit zwei Preisen (alter/neuer Preis) ══════════════════════════
D_TEXT = ("BMW 320d G20 Diesel statt 27.900 € jetzt 24.900 € 118.000 km EZ 05/2019\n"
          "BMW 320d G20 Diesel 25.400 € 121.000 km EZ 06/2019\n"
          "BMW 320d G20 Diesel 25.900 € 117.000 km EZ 07/2019\n"
          "BMW 320d G20 Diesel 25.100 € 124.000 km EZ 03/2019")
ma_d, punkte_d = _punkte(D_TEXT)
p_d = _nach_preis(punkte_d)
gueltig_d, gruende_d = validiere_karte(
    "BMW 320d G20 Diesel statt 27.900 € jetzt 24.900 € 118.000 km EZ 05/2019")
check("D: ein Abschnitt mit zwei Preisen ist KEINE bestätigte Karte", gueltig_d is False)
check("D: der Grund wird benannt", any("Preise" in g for g in gruende_d))
check("D: die mehrdeutigen Punkte fallen in den Zeichenfenster-Fallback",
      p_d[24900].window_fallback_used and p_d[27900].window_fallback_used)
check("D: und sind damit höchstens conditional",
      p_d[24900].vergleichbarkeit == "bedingt" and p_d[27900].vergleichbarkeit == "bedingt")
check("D: die eindeutigen Nachbarkarten bleiben strukturell und vollwertig",
      not p_d[25400].window_fallback_used
      and p_d[25400].vergleichbarkeit == "sehr_aehnlich")
# Der eigentliche Punkt: sobald genug EINDEUTIGE Karten vorliegen, bleiben die
# mehrdeutigen Fensterpunkte komplett draußen — weder der alte noch der neue Preis
# des doppelt ausgezeichneten Fahrzeugs verzerrt den Median.
check("D: der Median stützt sich ausschließlich auf die eindeutigen Karten",
      sorted(b.preis_eur for b in ma_d.beobachtungen) == [25100, 25400, 25900])
check("D: weder alter noch neuer Preis der mehrdeutigen Karte im Median",
      27900 not in _preise_ma(ma_d) and 24900 not in _preise_ma(ma_d))
check("D: kein Conditional-Fallback nötig", ma_d.fallback_bedingt is False)
check("D: die mehrdeutigen Punkte bleiben als Kontext erhalten",
      {27900, 24900} <= {b.preis_eur for b in ma_d.kontext_beobachtungen})

# ══ E — zwei Fahrzeuge mit gleichem Baujahr und ähnlichen km ════════════════
E_TEXT = ("BMW 320d G20 Diesel Muenchen 24.900 € 118.000 km EZ 05/2019\n"
          "BMW 320d G20 Diesel Hamburg 25.400 € 118.500 km EZ 05/2019")
ma_e, punkte_e = _punkte(E_TEXT)
check("E: beide Fahrzeuge bleiben getrennt (keine Verschmelzung)", len(punkte_e) == 2)
check("E: sie bekommen unterschiedliche listing_key",
      len({b.listing_key for b in punkte_e}) == 2)
check("E: beide sind strukturell abgegrenzt",
      all(not b.window_fallback_used for b in punkte_e))

# Card-Hash ist pro isolierter Karte STABIL (gleiche Karte -> gleicher Schlüssel).
ma_e2, punkte_e2 = _punkte(E_TEXT)
check("E: der Card-Hash ist über Läufe hinweg stabil",
      [b.listing_key for b in punkte_e] == [b.listing_key for b in punkte_e2])
check("E: der Card-Hash steckt nicht nur Preis/Baujahr/km",
      all(b.listing_key.startswith("card:") for b in punkte_e)
      and all("24900" not in b.listing_key for b in punkte_e))

# ══ F — Markdown-Detail-Links je Karte ══════════════════════════════════════
F_TEXT = (
    "[BMW 320d G20 Limousine](https://www.kleinanzeigen.de/s-anzeige/bmw-320d/"
    "2811111111-216-1) 24.900 € 118.000 km EZ 05/2019\n"
    "[BMW 320d G20 Limousine](https://www.kleinanzeigen.de/s-anzeige/bmw-320d/"
    "2822222222-216-1) 25.400 € 121.000 km EZ 06/2019\n"
    "[BMW 320d G20 Limousine](https://www.kleinanzeigen.de/s-anzeige/bmw-320d/"
    "2833333333-216-1) 25.900 € 117.000 km EZ 07/2019")
ma_f, punkte_f = _punkte(F_TEXT)
p_f = _nach_preis(punkte_f)
segmente_f, verfahren_f = segmentiere(f"BMW 320d G20 gebraucht kaufen\n{F_TEXT}\n", URL)
check("F: Detail-Links werden als Segmentierungsverfahren erkannt",
      verfahren_f == "detail_link")
check("F: höchste Konfidenz", all(s.structural_confidence == "high" for s in segmente_f))
check("F: jede Karte bekommt ihre eigene Anzeigen-ID",
      sorted(b.listing_id for b in punkte_f)
      == ["2811111111", "2822222222", "2833333333"])
check("F: die ID wird zum primären listing_key",
      all(b.listing_key.startswith("id:") for b in punkte_f))
check("F: die Detail-URL der Karte wird übernommen, nicht die Listen-URL",
      all(b.detail_url and "s-anzeige" in b.detail_url for b in punkte_f))
check("F: keine Karte erbt die ID der Nachbarkarte",
      p_f[24900].listing_id == "2811111111" and p_f[25400].listing_id == "2822222222")
check("F: alle drei tragen den Median",
      sorted(b.preis_eur for b in ma_f.beobachtungen) == [24900, 25400, 25900])

# ══ G — Text ohne erkennbare Kartenstruktur ═════════════════════════════════
# Fließtext eines Ratgebers: mehrere Preise, Kilometerangaben und Jahre ohne
# jede Trennstruktur — hier DARF nichts strukturell bestätigt werden.
G_TEXT = ("Der 3er ist gefragt: je nach Zustand zahlt man zwischen 22.500 € und "
          "28.900 € wobei Fahrzeuge mit 90.000 km bis 140.000 km und Baujahren "
          "von 2019 bis 2021 den Markt bestimmen und 24.700 € typisch sind")
ma_g, punkte_g = _punkte(G_TEXT, titel="BMW 3er Gebrauchtpreise Ratgeber")
segmente_g, verfahren_g = segmentiere(G_TEXT, URL)
check("G: keine strukturelle Segmentierung möglich", verfahren_g == "keine")
check("G: der Segmenter liefert keine bestätigten Karten", segmente_g == [])
check("G: alle Punkte sind als Zeichenfenster markiert",
      punkte_g and all(b.window_fallback_used for b in punkte_g))
check("G: extraction_source weist sie als window_fallback aus",
      all(b.extraction_source == "window_fallback" for b in punkte_g))
check("G: structural_confidence ist niedrig",
      all(b.structural_confidence == "low" for b in punkte_g))
check("G: KEIN Punkt wird 'sehr ähnlich' oder 'ähnlich'",
      all(b.vergleichbarkeit not in ("sehr_aehnlich", "aehnlich") for b in punkte_g))
check("G: sie tragen keine hochwertige Preisstatistik",
      ma_g.datenqualitaet != "hoch")

# ══ REALSTRUKTUR — Karten-Headings mit relativem Detail-Link ════════════════
# Nachgebildet aus den gespeicherten Live-Läufen (diagnose_runs/): jede Karte einer
# Trefferliste beginnt mit "## [Fahrzeugtitel](/s-anzeige/<slug>/<id>-216-<n>)",
# darunter Beschreibung, Preis, Kilometer, EZ. Der Link ist WURZEL-RELATIV.
NAV = ("## Filter\n\n### Preis\n\n### Außenausstattung\n\n### Anbieter\n\n## Ergebnisse\n\n")


def _karte(titel, slug, lid, preis, km, ez, beschreibung="Zum Verkauf steht mein..."):
    return (f"![{titel} Vorschau](https://img.kleinanzeigen.de/api/v1/x.jpg)\n\n"
            f"## [{titel}](/s-anzeige/{slug}/{lid}-216-4711)\n\n"
            f"{beschreibung}\n\n{preis} €\n\n{km} km\n\nEZ {ez}\n\n")


# ── A: drei aufeinanderfolgende Karten ──────────────────────────────────────
A2_RAW = (NAV
          + _karte("BMW 320d G20 M Sport", "bmw-320d-g20-m-sport", "3475100088",
                   "24.900", "118.000", "05/2019")
          + _karte("BMW 330i G20 Sport Line", "bmw-330i-g20-sport-line", "3475860834",
                   "31.900", "119.000", "05/2019")
          + _karte("BMW 320d Limousine Aut", "bmw-320d-limousine-aut", "3466149162",
                   "25.400", "121.000", "06/2019"))
seg_a2, verf_a2 = segmentiere(f"{'BMW 320d gebraucht kaufen'}\n\n{A2_RAW}", URL,
                              titel_ende=len("BMW 320d gebraucht kaufen") + 1)
check("A2: Verfahren ist detail_link", verf_a2 == "detail_link")
check("A2: genau drei Karten", len(seg_a2) == 3)
check("A2: alle mit hoher Konfidenz",
      all(s.structural_confidence == "high" for s in seg_a2))
check("A2: drei eigene Detail-URLs",
      len({s.detected_detail_url for s in seg_a2}) == 3
      and all(s.detected_detail_url and s.detected_detail_url.startswith("https://")
              for s in seg_a2))
check("A2: relativer Link wurde gegen die Quell-URL aufgelöst",
      all("kleinanzeigen.de/s-anzeige/" in s.detected_detail_url for s in seg_a2))
check("A2: drei eigene Listing-IDs",
      sorted(s.detected_listing_id for s in seg_a2)
      == ["3466149162", "3475100088", "3475860834"])
check("A2: keine Karte enthält den Preis einer Nachbarkarte",
      "31.900" not in seg_a2[0].text and "25.400" not in seg_a2[1].text
      and "24.900" not in seg_a2[2].text)
check("A2: keine Karte enthält die ID einer Nachbarkarte",
      "3475860834" not in seg_a2[0].text and "3466149162" not in seg_a2[1].text)

ma_a2, punkte_a2 = _punkte(A2_RAW, titel="BMW 320d gebraucht kaufen")
p_a2 = _nach_preis(punkte_a2)
check("A2: der 330i wird verworfen, die 320d bleiben",
      31900 not in _preise_ma(ma_a2)
      and sorted(_preise_ma(ma_a2)) == [24900, 25400])
check("A2: die Karten-IDs werden zum primären listing_key",
      all(b.listing_key.startswith("id:") for b in ma_a2.beobachtungen))
check("A2: Preis/km/Baujahr bleiben je Karte beisammen",
      p_a2[24900].kilometerstand == 118_000 and p_a2[25400].kilometerstand == 121_000)

# ── B: Navigation vor der ersten Karte ──────────────────────────────────────
# Die Karte beginnt an ihrem EIGENEN Vorschaubild — das steht real vor dem Heading
# und gehört zu dieser Karte — und enthält unmittelbar danach ihren eigenen Heading.
check("B2: die erste Karte beginnt an ihrem eigenen Vorschaubild + Heading",
      seg_a2[0].text.startswith("![BMW 320d G20 M Sport Vorschau]")
      and "## [BMW 320d G20 M Sport](" in seg_a2[0].text)
check("B2: keine Karte schleppt das Vorschaubild der Nachbarkarte mit",
      "330i" not in seg_a2[0].text and "Limousine Aut" not in seg_a2[1].text)
check("B2: der Navigations-/Filterblock gehört zu keiner Karte",
      all("### Außenausstattung" not in s.text and "## Filter" not in s.text
          for s in seg_a2))
check("B2: die Kartengrenze liegt hinter der Navigationszone",
      seg_a2[0].start > A2_RAW.index("## Ergebnisse"))

# ── C: Motor/Generation NUR im eigenen Karten-Heading ───────────────────────
C3_RAW = (NAV
          + _karte("BMW 320d G20 M Sport", "bmw-320d-g20-m-sport", "3475100081",
                   "24.900", "118.000", "05/2019",
                   beschreibung="Sehr gepflegt, scheckheftgepflegt, 2. Hand...")
          + _karte("BMW Limousine Advantage", "bmw-limousine-advantage", "3475100082",
                   "25.100", "119.000", "04/2019",
                   beschreibung="Guter Zustand, viele Extras...")
          + _karte("BMW 320d G20 Sport Line", "bmw-320d-g20-sport-line", "3475100083",
                   "25.400", "121.000", "06/2019",
                   beschreibung="Top gepflegt, unfallfrei..."))
ma_c3, punkte_c3 = _punkte(C3_RAW, titel="BMW gebrauchtwagen kaufen")
p_c3 = _nach_preis(punkte_c3)
check("C3: Motor aus dem EIGENEN Karten-Heading wird erkannt",
      p_c3[24900].engine_variant == "320d" and p_c3[25400].engine_variant == "320d")
check("C3: Generation aus dem EIGENEN Karten-Heading wird erkannt",
      p_c3[24900].generation == "G20" and p_c3[25400].generation == "G20")
check("C3: dadurch werden diese Karten vollwertige Vergleiche",
      p_c3[24900].vergleichbarkeit == "sehr_aehnlich"
      and p_c3[25400].vergleichbarkeit == "sehr_aehnlich")
check("C3: die Karte OHNE eigene Angaben erbt nichts vom Nachbarn",
      p_c3[25100].engine_variant is None and p_c3[25100].generation is None)
check("C3: und bleibt höchstens conditional", p_c3[25100].vergleichbarkeit == "bedingt")

# ── D: Motor/Generation NUR in der Seitenüberschrift ────────────────────────
D2_RAW = (NAV
          + _karte("BMW Limousine Advantage", "bmw-limousine-advantage", "3475100084",
                   "24.900", "118.000", "05/2019")
          + _karte("BMW Limousine Sport Line", "bmw-limousine-sport-line", "3475100085",
                   "25.400", "121.000", "06/2019"))
ma_d2, punkte_d2 = _punkte(D2_RAW, titel="BMW 320d G20 gebraucht kaufen")
check("D2: der Seitentitel vererbt den Motor NICHT an die Karten",
      all(b.engine_variant is None for b in punkte_d2))
check("D2: der Seitentitel vererbt die Generation NICHT an die Karten",
      all(b.generation is None for b in punkte_d2))
check("D2: die Karten bleiben dadurch höchstens conditional",
      all(b.vergleichbarkeit == "bedingt" for b in punkte_d2))

# ── E: dieselbe Anzeige auf zwei Suchseiten ─────────────────────────────────
GLEICHE = _karte("BMW 320d G20 M Sport", "bmw-320d-g20-m-sport", "3475100099",
                 "24.900", "118.000", "05/2019")
SEITE_1 = _seite(NAV + GLEICHE,
                 url="https://www.kleinanzeigen.de/s-autos/bmw-320d-2019/k0c216",
                 titel="Bmw 320d 2019 gebraucht kaufen")
SEITE_2 = _seite(NAV + GLEICHE,
                 url="https://www.kleinanzeigen.de/s-autos/bmw-320d-automatik/k0c216",
                 titel="Bmw 320d Automatik gebraucht kaufen")
ma_e2 = analysiere_markt([SEITE_1, SEITE_2], ZIEL, None)
alle_e2 = list(ma_e2.beobachtungen) + list(ma_e2.kontext_beobachtungen)
check("E2: dieselbe Anzeige auf zwei Suchseiten zählt nur einmal", len(alle_e2) == 1)
check("E2: der Listing-Key stammt aus der Anzeigen-ID",
      alle_e2 and alle_e2[0].listing_key == "id:kleinanzeigen.de:3475100099")
check("E2: er ist unabhängig von der Suchseite stabil",
      analysiere_markt([SEITE_2], ZIEL, None).beobachtungen[0].listing_key
      == analysiere_markt([SEITE_1], ZIEL, None).beobachtungen[0].listing_key)

# ── Detail-Link-Auflösung als Einzelfunktion ────────────────────────────────
check("Link: wurzel-relativer Detailpfad wird aufgelöst",
      aufgeloester_detail_link("/s-anzeige/bmw-320d/3475100088-216-1", URL)
      == "https://www.kleinanzeigen.de/s-anzeige/bmw-320d/3475100088-216-1")
check("Link: absoluter Detaillink bleibt erhalten",
      aufgeloester_detail_link(
          "https://www.kleinanzeigen.de/s-anzeige/bmw-320d/3475100088-216-1", URL)
      is not None)
check("Link: relativer NICHT-Detailpfad wird abgelehnt",
      aufgeloester_detail_link("/s-autos/bmw-320d/k0c216", URL) is None)
check("Link: Navigations-/Anmeldelinks werden abgelehnt",
      aufgeloester_detail_link("/m-einloggen.html", URL) is None
      and aufgeloester_detail_link("#main", URL) is None)
check("Link: ohne Basis-URL kein relativer Link",
      aufgeloester_detail_link("/s-anzeige/bmw-320d/3475100088-216-1", "") is None)
check("Link: protokollrelativer Link wird abgelehnt",
      aufgeloester_detail_link("//fremd.de/s-anzeige/x/123456-216-1", URL) is None)

# ── Listing-ID aus dem realen Pfadformat ────────────────────────────────────
check("ID: letztes Pfadsegment liefert die Anzeigen-ID",
      _listing_id("", "https://www.kleinanzeigen.de/s-anzeige/"
                      "bmw-320d-gran-turismo-xdrive/3475539155-216-2645") == "3475539155")
check("ID: ein Slug mit langer Zahl verdrängt die echte ID nicht",
      _listing_id("", "https://www.kleinanzeigen.de/s-anzeige/"
                      "bmw-320d-120000-km-scheckheft/3472060535-216-3033") == "3472060535")
check("ID: Anzeigen-ID im Fließtext hat weiterhin Vorrang",
      _listing_id("Anzeigen-ID: 2899887766",
                  "https://www.kleinanzeigen.de/s-anzeige/x/3472060535-216-1")
      == "2899887766")

# ══ Segmenter-Grundverhalten ════════════════════════════════════════════════
check("Leerer Text liefert nichts", segmentiere("", URL) == ([], "keine"))
check("Text ohne Preis liefert nichts", segmentiere("BMW 320d G20 Limousine", URL)[1] == "keine")
gueltig, gruende = validiere_karte("BMW 320d G20 24.900 € 118.000 km EZ 05/2019")
check("Eine vollständige Karte ist gültig", gueltig and not gruende)
gueltig2, gruende2 = validiere_karte("24.900 € 118.000 km EZ 05/2019")
check("Ohne Fahrzeugtitel/Modellhinweis ist es keine Karte", gueltig2 is False)
check("Der fehlende Fahrzeugbezug wird benannt",
      any("Fahrzeugtitel" in g for g in gruende2))
gueltig3, gruende3 = validiere_karte("BMW 320d G20 24.900 € EZ 05/2019")
check("Ohne Kilometerstand ist es keine Karte", gueltig3 is False)
check("CardSegment kennt seinen strukturellen Status",
      CardSegment("x", 0, 1, "low", "window_fallback").strukturell is False
      and CardSegment("x", 0, 1, "high", "detail_link").strukturell is True)

print()
if _fails:
    print(f"{len(_fails)} Test(s) fehlgeschlagen.")
    sys.exit(1)
print("Alle Card-Segmenter-Tests bestanden.")
