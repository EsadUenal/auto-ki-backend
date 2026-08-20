"""
Marktanalyse-Sprint — Single-Source-Marktwert, strenge Fahrzeugvalidierung, best_so_far.

Deterministisch, KEIN Netzwerk (die Recherche-Integration nutzt einen Stub statt Tavily).

Deckt ab:
  §3  MarketObservation trägt Fahrzeug- und Herkunftsfelder
  §4  Dedup über listing_key (Inserats-ID / Detail-URL / Fahrzeugdaten)
  §5  harte Kriterien: Generation, Motorvariante, Kraftstoff, Leistung; weiche: Karosserie
  §6  Facelift-Grenze vor der ±1/±2-Baujahrsregel
  §7  relative Kilometer-Fenster statt starrer absoluter Schwellen
  §9  Single-Source-Modus: eine Plattform blockiert kein Ergebnis mehr
  §10 Marktabdeckung als eigene Achse
  §11 best_so_far: ein erreichter Stand kann nicht mehr verschlechtert werden

    python test_marktanalyse_single_source.py
"""
import asyncio
import io
import itertools
import os
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_ss_"), "test.db")
sys.path.insert(0, ".")

# §Source-Policy: Der Production-Default gibt KEINE Marktquelle zum Preisbilden
# frei (app/config.ALLOWED_MARKET_SOURCES ist leer). Dieser Test prueft die
# ANALYSE-ENGINE und braucht dafuer die historischen/synthetischen Testdomains —
# die Freigabe gilt ausschliesslich in diesem Testprozess und ist KEINE
# produktive Qualifikation der Quelle. Siehe _source_policy_testharness.py.
import _source_policy_testharness  # noqa: E402,F401

from types import SimpleNamespace                                       # noqa: E402

import app.marktrecherche as mr                                        # noqa: E402
import app.web_search as ws                                            # noqa: E402
from app.marktrecherche import (                                       # noqa: E402
    QueryStufe, bewertungsrang, genug, research_status, vertiefe_marktrecherche,
)
from app.marktvergleich import (                                       # noqa: E402
    _km_fenster, _listing_id_aus_url, _marktabdeckung, analysiere_markt, baue_ziel,
    ist_teile_suchseite,
)

_fails: list[str] = []


def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        _fails.append(name)


# ══ Zielprofil: BMW 320d G20, 2019, 190 PS, Diesel, Automatik, 120.000 km ════
BAUREIHE = {"marke": "BMW", "modell": "3er", "generation": "G20", "id": "bmw-3er-g20",
            "karosserie": ["Limousine", "Kombi"]}
ALLE_BAUREIHEN = [
    BAUREIHE,
    {"id": "bmw-3er-f30", "marke": "BMW", "modell": "3er", "generation": "F30"},
    {"id": "bmw-5er-g30", "marke": "BMW", "modell": "5er", "generation": "G30"},
]
ALLE_MOTOREN = [
    {"baureihe_id": "bmw-3er-g20", "bezeichnung": "320d"},
    {"baureihe_id": "bmw-3er-g20", "bezeichnung": "320i"},
    {"baureihe_id": "bmw-3er-g20", "bezeichnung": "330d"},
    {"baureihe_id": "bmw-5er-g30", "bezeichnung": "520d"},
]
MOTOR = {"bezeichnung": "320d", "kraftstoff": "Diesel", "leistung_ps": 190}
REQ = SimpleNamespace(marke="BMW", modell="3er", motor="320d", kraftstoff="Diesel",
                      getriebe="Automatik", baujahr=2019, kilometerstand=120_000)
ZIEL = baue_ziel(BAUREIHE, MOTOR, REQ, ALLE_BAUREIHEN, ALLE_MOTOREN)

check("Ziel: Zielmotor-Token 320d", ZIEL["ziel_motor_tokens"] == {"320d"})
# §DB-Trust: die Liste der uebrigen Motorvarianten stammt aus ungeprueften DB-Zeilen
# und traegt keine harte Ablehnung mehr. Die Abgrenzung leistet der direkte Abgleich
# der Verkaufsbezeichnung im Inserat gegen die Nutzerangabe.
check("Ziel: Fremdmotor-Token bleiben ohne Verifikation leer",
      ZIEL["fremd_motor_tokens"] == set())
check("Ziel: Zielleistung 190 PS aus der DB-Motorvariante", ZIEL["leistung_ps"] == 190)


def _seite(url, content, titel="BMW 320d"):
    return {"url": url, "title": titel, "content": content}


def _analyse(seiten, ziel=ZIEL, angebot=None):
    return analysiere_markt(seiten, ziel, angebot)


def _preise(ma):
    return [b.preis_eur for b in ma.beobachtungen]


# ══ §5 — harte Motorvarianten-Abgrenzung innerhalb derselben Baureihe ════════
MOTOR_MIX = [_seite("https://www.kleinanzeigen.de/s-autos/bmw/k0", (
    "BMW 320d G20 24.900 € 118.000 km EZ 05/2019 . "
    "BMW 320i G20 27.900 € 119.000 km EZ 04/2019 . "
    "BMW 330d G20 31.900 € 121.000 km EZ 06/2019"))]
ma_motor = _analyse(MOTOR_MIX)
alle_motor = [_seite("x", MOTOR_MIX[0]["content"])]
check("§5: 320i (andere Motorvariante derselben Baureihe) nicht im Vergleich",
      27900 not in _preise(ma_motor))
check("§5: 330d (andere Motorvariante derselben Baureihe) nicht im Vergleich",
      31900 not in _preise(ma_motor))
check("§5: der echte 320d bleibt erhalten",
      any(b.preis_eur == 24900 for b in ma_motor.beobachtungen))

# ══ §5 — harter Kraftstoff ═══════════════════════════════════════════════════
# Jede Karte nennt den Motor SELBST — seit der Kartensegmentierung vererbt die
# Seitenüberschrift ihn nicht mehr (§2), und ohne eigene Motorangabe wäre die Karte
# nur noch "bedingt". Hier soll aber der Kraftstoff geprüft werden.
KRAFTSTOFF = [_seite("https://www.kleinanzeigen.de/s-autos/bmw/k1", (
    "BMW 320d G20 Diesel 24.500 € 118.000 km EZ 05/2019 . "
    "BMW 320d G20 Benzin 28.900 € 119.000 km EZ 04/2019"))]
ma_kr = _analyse(KRAFTSTOFF)
check("§5: Benziner im Diesel-Check verworfen", 28900 not in _preise(ma_kr))
check("§5: Diesel bleibt", 24500 in _preise(ma_kr))

# ══ §5 — Leistung: kein Zwang zur exakten Zahl, aber kein Widerspruch ═══════
# Ohne eigene Motorbezeichnung auf der Karte greift die Leistungsprüfung (bei
# bestätigter Verkaufsbezeichnung gilt die Motorisierung als geklärt). Solche Karten
# sind nach §2 höchstens "bedingt" — geprüft wird hier deshalb die Einstufung selbst,
# nicht die Median-Mitgliedschaft.
LEISTUNG = [_seite("https://www.kleinanzeigen.de/s-autos/bmw/k2", (
    "BMW 3er G20 Diesel 184 PS 24.700 € 118.000 km EZ 05/2019 . "
    "BMW 3er G20 Diesel 150 PS 21.300 € 119.000 km EZ 04/2019"))]
ma_ps = _analyse(LEISTUNG)
_ps_stufen = {b.preis_eur: b.vergleichbarkeit
              for b in list(ma_ps.beobachtungen) + list(ma_ps.kontext_beobachtungen)}
check("§5: 184 PS gilt bei Zielleistung 190 PS als vergleichbar (nicht verworfen)",
      _ps_stufen.get(24700) == "bedingt")
check("§5: 150 PS widerspricht 190 PS -> verworfen",
      21300 not in _ps_stufen and 21300 not in _preise(ma_ps))

# ══ §5 — Karosserie: weiches Kriterium, nie 'sehr ähnlich' ══════════════════
REQ_KOMBI = SimpleNamespace(marke="BMW", modell="3er Touring", motor="320d",
                            kraftstoff="Diesel", baujahr=2019, kilometerstand=120_000)
ZIEL_KOMBI = baue_ziel(BAUREIHE, MOTOR, REQ_KOMBI, ALLE_BAUREIHEN, ALLE_MOTOREN)
check("§5: Zielkarosserie aus der Nutzerangabe erkannt (Touring -> kombi)",
      ZIEL_KOMBI["karosserie"] == "kombi")
KAROSSERIE = [_seite("https://www.kleinanzeigen.de/s-autos/bmw/k3", (
    "BMW 320d G20 Touring 25.100 € 118.000 km EZ 05/2019 . "
    "BMW 320d G20 Limousine 24.300 € 119.000 km EZ 04/2019"))]
ma_ka = _analyse(KAROSSERIE, ZIEL_KOMBI)
_touring = [b for b in ma_ka.beobachtungen if b.preis_eur == 25100]
_limo = [b for b in ma_ka.beobachtungen if b.preis_eur == 24300]
check("§5: gleiche Karosserie (Touring) -> sehr ähnlich",
      bool(_touring) and _touring[0].vergleichbarkeit == "sehr_aehnlich")
check("§5: andere Karosserie (Limousine) bleibt erhalten, aber NICHT sehr ähnlich",
      bool(_limo) and _limo[0].vergleichbarkeit != "sehr_aehnlich")

# ══ §7 — relative Kilometer-Fenster ═════════════════════════════════════════
f_sehr, f_aehn, f_bedingt = _km_fenster(120_000)
check("§7: 120.000 km -> sehr ähnlich ±18.000 (102.000-138.000)", f_sehr == 18_000)
check("§7: 120.000 km -> ähnlich ±30.000 (90.000-150.000)", f_aehn == 30_000)
check("§7: 120.000 km -> bedingt ±42.000", f_bedingt == 42_000)
check("§7: absolutes Mindestfenster greift bei geringer Laufleistung",
      _km_fenster(20_000) == (10_000, 15_000, 25_000))

KM = [_seite("https://www.kleinanzeigen.de/s-autos/bmw/k4", (
    "BMW 320d G20 25.900 € 118.000 km EZ 05/2019 . "
    "BMW 320d G20 23.100 € 145.000 km EZ 04/2019 . "
    "BMW 320d G20 21.700 € 158.000 km EZ 06/2019 . "
    "BMW 320d G20 17.200 € 205.000 km EZ 03/2019"))]
ma_km = _analyse(KM)
_stufe = {b.preis_eur: b.vergleichbarkeit for b in ma_km.beobachtungen}
check("§7: 118.000 km (Δ2.000) -> sehr ähnlich", _stufe.get(25900) == "sehr_aehnlich")
check("§7: 145.000 km (Δ25.000, innerhalb ±30.000) -> ähnlich", _stufe.get(23100) == "aehnlich")
check("§7: 158.000 km (Δ38.000, innerhalb ±42.000) -> bedingt", _stufe.get(21700) == "bedingt")
check("§7: 205.000 km (Δ85.000) -> verworfen", 17200 not in _stufe)

# ══ §6 — Facelift-Grenze hat Vorrang vor der ±1/±2-Regel ════════════════════
BAUREIHE_FL = dict(BAUREIHE, facelift_merkmale="Modellpflege ab 2022: neue Scheinwerfer.")
ZIEL_FL = baue_ziel(BAUREIHE_FL, MOTOR, SimpleNamespace(
    marke="BMW", modell="3er", motor="320d", kraftstoff="Diesel",
    baujahr=2021, kilometerstand=120_000), ALLE_BAUREIHEN, ALLE_MOTOREN)
check("§6: Facelift-Jahr aus dem freien DB-Text gelesen", ZIEL_FL["facelift_jahr"] == 2022)
FL = [_seite("https://www.kleinanzeigen.de/s-autos/bmw/k5", (
    "BMW 320d G20 29.900 € 118.000 km EZ 05/2021 . "
    "BMW 320d G20 31.400 € 119.000 km EZ 04/2022"))]
ma_fl = _analyse(FL, ZIEL_FL)
_fl = {b.preis_eur: b.vergleichbarkeit for b in ma_fl.beobachtungen}
check("§6: gleiche Modellpflege-Phase (2021) -> sehr ähnlich", _fl.get(29900) == "sehr_aehnlich")
check("§6: über die Facelift-Grenze (2022 vs 2021) -> nicht sehr ähnlich",
      _fl.get(31400) not in (None, "sehr_aehnlich"))

# ══ §4 — Dedup über die Inserats-ID ═════════════════════════════════════════
check("§4: Inserats-ID aus der URL gelesen",
      _listing_id_aus_url("https://www.kleinanzeigen.de/s-anzeige/bmw-320d/2812345678-216-1234")
      == "2812345678")
DOPPELT = [
    _seite("https://www.kleinanzeigen.de/s-anzeige/bmw-320d/2812345678-216-1234",
           "BMW 320d G20 24.900 € 118.000 km EZ 05/2019"),
    _seite("https://www.kleinanzeigen.de/s-anzeige/bmw-320d-g20-diesel/2812345678-216-9999",
           "BMW 320d G20 24.900 € 118.000 km EZ 05/2019"),
]
ma_dup = _analyse(DOPPELT)
check("§4: dasselbe Inserat über zwei URLs zählt nur einmal", ma_dup.gefunden == 1)

# Zwei WIRKLICH verschiedene Fahrzeuge derselben Domain dürfen nicht verschmelzen.
VERSCHIEDEN = [
    _seite("https://www.kleinanzeigen.de/s-anzeige/bmw-320d/2811111111-216-1",
           "BMW 320d G20 24.900 € 118.000 km EZ 05/2019"),
    _seite("https://www.kleinanzeigen.de/s-anzeige/bmw-320d/2822222222-216-2",
           "BMW 320d G20 25.400 € 121.000 km EZ 06/2019"),
]
check("§4: zwei verschiedene Inserate bleiben zwei Beobachtungen",
      _analyse(VERSCHIEDEN).gefunden == 2)

# ══ §3 — MarketObservation trägt die Fahrzeug-/Herkunftsfelder ══════════════
b0 = _analyse(VERSCHIEDEN).beobachtungen[0]
check("§3: listing_key gesetzt", bool(b0.listing_key))
check("§3: listing_id gesetzt", b0.listing_id == "2811111111")
check("§3: detail_url gesetzt (Einzelinserat)", bool(b0.detail_url))
check("§3: source_type = listing", b0.source_type == "listing")
check("§3: Generation aus dem Preisumfeld belegt", b0.generation == "G20")
check("§3: Motorvariante aus dem Preisumfeld belegt", b0.engine_variant == "320d")
check("§3: Marke/Modell aus dem Zielabgleich gesetzt", b0.make == "BMW" and b0.model == "3er")
check("§3: similarity als Zahl gesetzt", 0.0 < b0.similarity <= 1.0)
check("§3: extraction_source gesetzt", b0.extraction_source in ("title", "snippet", "raw_content"))
check("§3: acceptance_reason gesetzt", bool(b0.acceptance_reason))

# ══ §9/§10 — Single-Source: eine Plattform blockiert kein Ergebnis mehr ═════
SINGLE_SOURCE = [
    _seite(f"https://www.kleinanzeigen.de/s-anzeige/bmw-320d-g20/28{i}00000{i}-216-1",
           f"BMW 320d G20 Diesel {preis} € {km}.000 km EZ 0{m}/2019")
    for i, (preis, km, m) in enumerate([
        ("23.900", "126", 3), ("24.400", "118", 4), ("24.900", "121", 5),
        ("25.200", "115", 6), ("25.600", "112", 7), ("25.900", "108", 8),
    ], start=1)
]
ma_ss = _analyse(SINGLE_SOURCE)
check("§9: 6 validierte Angebote EINER Plattform -> 6 Beobachtungen", ma_ss.verwendet == 6)
check("§9: belastbarer Median vorhanden", ma_ss.median_eur is not None)
check("§9: Datenqualität hoch trotz nur einer Plattform", ma_ss.datenqualitaet == "hoch")
check("§10: Marktabdeckung eingeschraenkt", ma_ss.marktabdeckung == "eingeschraenkt")
check("§10: anzahl_domains = 1", ma_ss.anzahl_domains == 1)
check("§9: KEIN research_failed bei einer einzigen Plattform",
      research_status(ma_ss) == "completed_medium")
check("§9: Gesamtvertrauen bei einer Plattform auf MEDIUM gedeckelt (nicht high)",
      research_status(ma_ss) != "completed_high")
check("§11: completed_medium beendet die Recherche NICHT (HIGH bleibt anstrebbar)",
      genug(ma_ss) is False)

# Dieselben Fahrzeuge über zwei Plattformen -> Abdeckung 'gut', Vertrauen HIGH.
#
# §Source-Policy: Die zweite Plattform war früher mobile.de, dann autoscout24.de.
# Beide Quellen sind für die automatische Marktpreisbildung nicht freigegeben
# (keine Erlaubnis/API-Lizenz) und zählen deshalb bewusst NICHT mehr für die
# Marktabdeckung — siehe app/web_search.darf_preisbildend_sein und
# test_fuel_source_policy.py. Die AUSSAGE dieses Tests ("zwei Plattformen -> gut
# -> completed_high") ist unverändert; nur die Wahl der zweiten Plattform musste
# erneut auf eine freigegebene wechseln (autouncle.de — letzter verbleibender
# _MARKTPLATZ_DOMAINS-Eintrag neben kleinanzeigen.de).
MULTI = SINGLE_SOURCE[:3] + [
    _seite(f"https://www.autouncle.de/de/gebrauchtwagen/bmw-320d-{i}-41234567{i}",
           f"BMW 320d G20 Diesel {preis} € {km}.000 km EZ 0{m}/2019")
    for i, (preis, km, m) in enumerate([("25.200", "115", 6), ("25.600", "112", 7),
                                        ("25.900", "108", 8)], start=1)
]
ma_multi = _analyse(MULTI)
check("§10: zwei Plattformen -> Marktabdeckung gut", ma_multi.marktabdeckung == "gut")
check("§10: zwei Plattformen + hohe Datenqualität -> completed_high",
      research_status(ma_multi) == "completed_high")
check("§10: Abdeckungsstufen", (_marktabdeckung([]), _marktabdeckung(["a"]),
                                _marktabdeckung(["a", "b"]), _marktabdeckung(["a", "b", "c"]))
      == ("eingeschraenkt", "eingeschraenkt", "gut", "breit"))

# ══ §1 — explizite Zielgeneration: G20 ≠ G21 ════════════════════════════════
# Die DB fasst Limousine und Touring in EINER Baureihe zusammen ("G20/G21").
# Nennt der Nutzer den Code selbst, ist das die verbindliche Zielgeneration.
BAUREIHE_G2X = {"marke": "BMW", "modell": "3er", "generation": "G20/G21",
                "id": "bmw-3er-g20-g21"}
ALLE_G2X = [BAUREIHE_G2X, {"id": "bmw-3er-f30", "marke": "BMW", "modell": "3er",
                           "generation": "F30"}]
MOTOREN_G2X = [{"baureihe_id": "bmw-3er-g20-g21", "bezeichnung": "320d",
                "motorcode": "B47D20"},
               {"baureihe_id": "bmw-3er-g20-g21", "bezeichnung": "320i"}]
REQ_OHNE_GEN = SimpleNamespace(marke="BMW", modell="3er", motor="320d",
                               kraftstoff="Diesel", baujahr=2019, kilometerstand=120_000)
REQ_MIT_G20 = SimpleNamespace(marke="BMW", modell="3er G20", motor="320d",
                              kraftstoff="Diesel", baujahr=2019, kilometerstand=120_000)
ZIEL_OHNE_GEN = baue_ziel(BAUREIHE_G2X, MOTOR, REQ_OHNE_GEN, ALLE_G2X, MOTOREN_G2X)
ZIEL_G20 = baue_ziel(BAUREIHE_G2X, MOTOR, REQ_MIT_G20, ALLE_G2X, MOTOREN_G2X)

check("§1: ohne explizite Angabe bleibt die ganze Baureihe Ziel (g20+g21)",
      ZIEL_OHNE_GEN["generation_tokens"] == {"g20", "g21"}
      and "g21" not in ZIEL_OHNE_GEN["fremd_generationen"])
check("§1: mit expliziter Angabe ist NUR g20 Ziel",
      ZIEL_G20["generation_tokens"] == {"g20"})
check("§1: g21 wird dadurch zur Fremdgeneration",
      "g21" in ZIEL_G20["fremd_generationen"])
# §DB-Trust: f30 stammt aus einer ungeprueften Geschwisterzeile und darf nicht mehr
# hart verwerfen. Die vom NUTZER gesetzte Einschraenkung (G20 statt G20/G21) bleibt
# dagegen wirksam — sie ist Nutzerevidenz, keine DB-Ableitung.
check("§1: ungepruefte Fremdbaureihe f30 verwirft nicht mehr hart",
      "f30" not in ZIEL_G20["fremd_generationen"])
check("§1: die Nutzer-Einschraenkung auf G20 macht g21 weiterhin fremd",
      "g21" in ZIEL_G20["fremd_generationen"])

G2X = [_seite("https://www.kleinanzeigen.de/s-autos/bmw-320d/k0c216", (
    "BMW 320d G20 24.900 € 118.000 km EZ 05/2019 . "
    "BMW 320d G21 25.600 € 121.000 km EZ 06/2019"))]
ma_g20 = _analyse(G2X, ZIEL_G20)
check("§1: G21 beeinflusst den Median eines gesuchten G20 NICHT",
      25600 not in _preise(ma_g20))
check("§1: der G20 bleibt erhalten", 24900 in _preise(ma_g20))
check("§1: der G21 wird als andere Generation verworfen, nicht nur abgewertet",
      all(b.vergleichbarkeit != "bedingt" or b.preis_eur != 25600
          for b in ma_g20.kontext_beobachtungen))

# ══ §2 — Motor muss auf KARTENEBENE belegt sein ═════════════════════════════
MOTOR_KARTE = [_seite(
    "https://www.kleinanzeigen.de/s-autos/bmw-320d-2019/k0c216",
    ("BMW 320d G20 24.900 € 118.000 km EZ 05/2019 . "
     "BMW 3er G20 25.400 € 119.000 km EZ 04/2019 . "
     "BMW 320i G20 27.900 € 120.000 km EZ 06/2019"),
    titel="BMW 320d G20 gebraucht kaufen")]
ma_mk = _analyse(MOTOR_KARTE, ZIEL_G20)
_mk = {b.preis_eur: b.vergleichbarkeit for b in ma_mk.beobachtungen}
_mk.update({b.preis_eur: b.vergleichbarkeit for b in ma_mk.kontext_beobachtungen})
check("§2: Karte mit '320d' ist vollwertig", _mk.get(24900) == "sehr_aehnlich")
check("§2: Karte ohne Motorangabe ist höchstens conditional",
      _mk.get(25400) == "bedingt")
check("§2: der Motor im SEITENTITEL vererbt sich nicht auf die Karte",
      25400 not in [b.preis_eur for b in ma_mk.beobachtungen
                    if b.vergleichbarkeit in ("sehr_aehnlich", "aehnlich")])
check("§2: falsche Motorvariante bleibt verworfen", 27900 not in _preise(ma_mk))

MOTORCODE = [_seite("https://www.kleinanzeigen.de/s-autos/bmw-320d/k0c216",
                    "BMW 3er G20 B47D20 24.900 € 118.000 km EZ 05/2019")]
check("§2: der Motorcode bestätigt die Motorisierung ebenfalls",
      _analyse(MOTORCODE, ZIEL_G20).beobachtungen[0].vergleichbarkeit == "sehr_aehnlich")

# ══ §5 — Quellseiten mit Teile-/Zubehör-Intent ══════════════════════════════
check("§5: Scheinwerfer-Suchseite erkannt",
      ist_teile_suchseite("https://www.kleinanzeigen.de/s-autos/g20-scheinwerfer/k0c216"))
check("§5: weitere Bauteil-/Zubehörbegriffe generisch erkannt",
      all(ist_teile_suchseite(f"https://www.kleinanzeigen.de/s-autos/bmw-{w}/k0c216")
          for w in ("alufelge", "steuerkette", "turbolader", "ersatzteile", "auspuff")))
check("§5: echte Fahrzeug-Suchseite bleibt zulässig",
      not ist_teile_suchseite("https://www.kleinanzeigen.de/s-autos/bmw-320d-2019/k0c216"))
check("§5: Intent auch im Titel erkannt",
      ist_teile_suchseite("https://beispiel.de/x", "BMW G20 Scheinwerfer gebraucht"))
check("§5: die Domain allein löst nichts aus",
      not ist_teile_suchseite("https://autoteile-mueller.de/s-autos/bmw-320d/k0"))

TEILE = [
    _seite("https://www.kleinanzeigen.de/s-autos/g20-scheinwerfer/k0c216",
           "BMW 320d G20 27.500 € 120.000 km EZ 05/2019"),
    _seite("https://www.kleinanzeigen.de/s-autos/bmw-320d-2019/k0c216",
           "BMW 320d G20 24.900 € 118.000 km EZ 05/2019"),
]
ma_teile = _analyse(TEILE, ZIEL_G20)
check("§5: Preise der Teile-Suchseite fließen NICHT ein", 27500 not in _preise(ma_teile))
check("§5: die Fahrzeugseite bleibt erhalten", 24900 in _preise(ma_teile))

# ══ §3 — Kartenidentität: Link/ID aus dem Kartentext, sonst Card-Hash ═══════
MIT_LINK = [_seite("https://www.kleinanzeigen.de/s-autos/bmw-320d/k0c216", (
    "[BMW 320d G20 Limousine](https://www.kleinanzeigen.de/s-anzeige/bmw-320d/"
    "2812345678-216-1234) 24.900 € 118.000 km EZ 05/2019"))]
b_link = _analyse(MIT_LINK, ZIEL_G20).beobachtungen[0]
check("§3: Detail-Link aus dem Kartentext gelesen",
      b_link.detail_url is not None and "2812345678" in b_link.detail_url)
check("§3: Anzeigen-ID daraus abgeleitet", b_link.listing_id == "2812345678")
check("§3: listing_key nutzt die ID als primären Schlüssel",
      b_link.listing_key.startswith("id:"))

MIT_ID = [_seite("https://www.kleinanzeigen.de/s-autos/bmw-320d/k0c216",
                 "BMW 320d G20 24.900 € 118.000 km EZ 05/2019 Anzeigen-ID: 2899887766")]
check("§3: Anzeigen-ID im Freitext erkannt",
      _analyse(MIT_ID, ZIEL_G20).beobachtungen[0].listing_id == "2899887766")

OHNE_ID = [_seite("https://www.kleinanzeigen.de/s-autos/bmw-320d/k0c216",
                  "BMW 320d G20 24.900 € 118.000 km EZ 05/2019")]
b_hash = _analyse(OHNE_ID, ZIEL_G20).beobachtungen[0]
check("§3: ohne Link/ID greift der Kartentext-Hash", b_hash.listing_key.startswith("card:"))
check("§3: der Hash steckt nicht mehr nur Preis+Baujahr+km",
      "24900" not in b_hash.listing_key)
# Zwei Karten, die in Preis, Baujahr UND km übereinstimmen, aber verschiedene
# Fahrzeuge sind, bekommen verschiedene Identitäten.
ZWEI_GLEICH = [
    _seite("https://www.kleinanzeigen.de/s-autos/a/k0c216",
           "BMW 320d G20 Limousine Sportpaket Muenchen 24.900 € 118.000 km EZ 05/2019"),
    _seite("https://www.kleinanzeigen.de/s-autos/b/k0c216",
           "BMW 320d G20 Limousine Advantage Hamburg 24.900 € 118.000 km EZ 05/2019"),
]
check("§3: unterschiedliche Karten -> unterschiedliche listing_key",
      len({_analyse([ZWEI_GLEICH[0]], ZIEL_G20).beobachtungen[0].listing_key,
           _analyse([ZWEI_GLEICH[1]], ZIEL_G20).beobachtungen[0].listing_key}) == 2)
check("§3: der Fahrzeug-Fingerabdruck bremst die Dublette trotzdem weiterhin",
      _analyse(ZWEI_GLEICH, ZIEL_G20).gefunden == 1)

# ══ §A/§B — "bedingt" darf die Preisstatistik nicht verzerren ═══════════════
# A) Drei gute Beobachtungen sind vorhanden -> ein extremer "bedingt"-Punkt bleibt
#    reiner Kontext und verändert Median, Quartile und Marktspanne NICHT.
GUTE_DREI = [
    _seite("https://www.kleinanzeigen.de/s-anzeige/bmw-320d-g20/2810000001-216-1",
           "BMW 320d G20 Diesel 24.400 € 118.000 km EZ 04/2019"),
    _seite("https://www.kleinanzeigen.de/s-anzeige/bmw-320d-g20/2810000002-216-1",
           "BMW 320d G20 Diesel 24.900 € 121.000 km EZ 05/2019"),
    _seite("https://www.kleinanzeigen.de/s-anzeige/bmw-320d-g20/2810000003-216-1",
           "BMW 320d G20 Diesel 25.200 € 115.000 km EZ 06/2019"),
]
# "bedingt": passendes Fahrzeug, aber deutlich abweichende Laufleistung UND ein
# fachlich zweifelhaft niedriger Preis (der reale Insignia-Befund in Reinform).
EXTREM_BEDINGT = _seite(
    "https://www.kleinanzeigen.de/s-anzeige/bmw-320d-g20/2810000009-216-1",
    "BMW 320d G20 Diesel 9.999 € 158.000 km EZ 06/2019")

ma_ohne = _analyse(GUTE_DREI)
ma_mit = _analyse(GUTE_DREI + [EXTREM_BEDINGT])
check("§A: der extreme Punkt ist tatsächlich nur 'bedingt' passend",
      any(b.vergleichbarkeit == "bedingt"
          for b in _analyse([EXTREM_BEDINGT]).beobachtungen + [*ma_mit.kontext_beobachtungen]))
check("§A: Median unverändert", ma_mit.median_eur == ma_ohne.median_eur)
check("§A: Quartile/Marktspanne unverändert",
      (ma_mit.spanne_min_eur, ma_mit.spanne_max_eur)
      == (ma_ohne.spanne_min_eur, ma_ohne.spanne_max_eur))
check("§A: 9.999 € ist NICHT in den verwendeten Vergleichen",
      9999 not in _preise(ma_mit))
check("§A: Anzahl verwendeter Vergleiche unverändert",
      ma_mit.verwendet == ma_ohne.verwendet == 3)
check("§A: der Punkt bleibt als Kontext erhalten (nicht stillschweigend verschluckt)",
      any(b.preis_eur == 9999 for b in ma_mit.kontext_beobachtungen))
check("§A: kein Fallback-Flag, wenn genug gute Beobachtungen da sind",
      ma_mit.fallback_bedingt is False)

# B) Nur zwei gute Beobachtungen -> "bedingt" darf als Fallback einspringen,
#    aber nur plausible Werte, und das Ergebnis ist höchstens completed_medium.
GUTE_ZWEI = GUTE_DREI[:2]
PLAUSIBLE_BEDINGT = [
    _seite("https://www.kleinanzeigen.de/s-anzeige/bmw-320d-g20/2810000021-216-1",
           "BMW 320d G20 Diesel 22.500 € 158.000 km EZ 06/2019"),
    _seite("https://www.kleinanzeigen.de/s-anzeige/bmw-320d-g20/2810000022-216-1",
           "BMW 320d G20 Diesel 21.900 € 160.000 km EZ 05/2019"),
    _seite("https://www.kleinanzeigen.de/s-anzeige/bmw-320d-g20/2810000023-216-1",
           "BMW 320d G20 Diesel 23.100 € 155.000 km EZ 04/2019"),
]
ma_fb = _analyse(GUTE_ZWEI + PLAUSIBLE_BEDINGT)
check("§B: Fallback greift bei weniger als 3 guten Beobachtungen",
      ma_fb.fallback_bedingt is True)
check("§B: die plausiblen bedingten Vergleiche werden verwendet",
      ma_fb.verwendet >= 4 and ma_fb.median_eur is not None)
check("§B: Ergebnis höchstens completed_medium",
      research_status(ma_fb) in ("completed_medium", "research_failed"))
check("§B: auch bei formal hoher Datenqualität bleibt es completed_medium",
      research_status(ma_fb.model_copy(update={"datenqualitaet": "hoch",
                                               "marktabdeckung": "breit"}))
      == "completed_medium")

# B2) Im Fallback fliegen unplausible Werte vorher raus.
ma_fb_extrem = _analyse(GUTE_ZWEI + PLAUSIBLE_BEDINGT + [EXTREM_BEDINGT])
check("§B: unplausibler Ausreißer wird auch im Fallback nicht verwendet",
      9999 not in _preise(ma_fb_extrem))
check("§B: die plausiblen bedingten Vergleiche bleiben trotzdem erhalten",
      ma_fb_extrem.verwendet == ma_fb.verwendet)
check("§B: die Untergrenze der Marktspanne wird nicht heruntergezogen",
      ma_fb_extrem.spanne_min_eur == ma_fb.spanne_min_eur)

# B3) Ohne jede gute Beobachtung braucht der reine "bedingt"-Kern eigene Substanz.
check("§B: ein einzelner bedingter Punkt allein trägt keine Preisstatistik",
      _analyse([EXTREM_BEDINGT]).median_eur is None)

# ══ §11 — best_so_far ═══════════════════════════════════════════════════════
check("§11: besserer Status schlägt schlechteren",
      bewertungsrang(ma_multi) > bewertungsrang(ma_ss))
check("§11: gleicher Status -> höhere Datenqualität gewinnt",
      bewertungsrang(ma_ss) > bewertungsrang(ma_km))

# Rauschen, das eine gute Basis nachträglich verwässern würde: dieselbe Generation,
# derselbe Motor, plausible Attribute — aber weit gestreute Preise.
RAUSCHEN = [
    _seite(f"https://www.kleinanzeigen.de/s-anzeige/bmw-320d-alt/29{i}00000{i}-216-1",
           f"BMW 320d G20 Diesel {preis} € 119.000 km EZ 05/2019")
    for i, preis in enumerate(["13.900", "14.500", "15.200", "44.900", "45.500", "46.200"], start=1)
]


class _Stub:
    """Ersetzt Tavily: Stufe 1 liefert die saubere Single-Source-Basis, jede weitere
    Stufe nur noch verwässerndes Rauschen."""

    def __init__(self):
        self.zaehler = itertools.count()

    async def __call__(self, query, count=5, include_domains=None, exclude_domains=None,
                       include_raw_content=False, bypass_cache=False, **kw):
        i = next(self.zaehler)
        return (SINGLE_SOURCE if i == 0 else RAUSCHEN), False


async def _lauf():
    stufen = [QueryStufe(query=f"q{i}", include_domains=None, label=f"s{i}") for i in range(1, 5)]
    return await vertiefe_marktrecherche([], stufen, ZIEL, None, None, count=10,
                                         zweck="test-best-so-far")


async def _kein_extract(urls, *, advanced=False):
    """Neutralisiert die Extract-Nachladung. Dieser Test prüft best_so_far, nicht die
    Datenversorgung — und darf unter keinen Umständen echte API-Aufrufe auslösen."""
    return [{"url": u, "raw_content": None, "erfolg": False} for u in urls]


_orig = mr.tavily_search_mit_status
_orig_extract = ws.tavily_extract
mr.tavily_search_mit_status = _Stub()
ws.tavily_extract = _kein_extract
try:
    _res, ma_best, diag = asyncio.run(_lauf())
finally:
    mr.tavily_search_mit_status = _orig
    ws.tavily_extract = _orig_extract

# Gegenprobe: der ZULETZT erreichte Stand (alle Stufen zusammen) ist schlechter.
ma_verwaessert = _analyse(SINGLE_SOURCE + RAUSCHEN)
check("§11: Vorbedingung — das Rauschen würde die Basis tatsächlich verschlechtern",
      bewertungsrang(ma_verwaessert) < bewertungsrang(ma_ss))
check("§11: zurückgegeben wird der BESTE Stand, nicht der letzte",
      ma_best.datenqualitaet == "hoch" and ma_best.verwendet == 6)
check("§11: Status bleibt auf dem besten erreichten Stand",
      diag["research_status"] == "completed_medium")
check("§11: best_so_far-Verlauf dokumentiert", len(diag["best_so_far"]) >= 2)
check("§11: die Übernahme ist im Verlauf markiert",
      any(e["uebernommen"] for e in diag["best_so_far"][1:]))
check("§11: spätere Rausch-Stufen wurden NICHT übernommen",
      diag["bester_stand_stufe"] == "1")
check("§11: die Recherche lief über den mittleren Stand hinaus weiter (HIGH-Versuch)",
      len(diag["stufen"]) > 2)

print()
if _fails:
    print(f"{len(_fails)} Test(s) fehlgeschlagen.")
    sys.exit(1)
print("Alle Marktanalyse-Single-Source-Tests bestanden.")
