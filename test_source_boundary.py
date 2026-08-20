"""
Source-Boundary: Production-Default und explizite Freigabe — deterministisch.

WICHTIG: Diese Datei importiert BEWUSST NICHT `_source_policy_testharness`.
Sie prueft genau den ungefilterten PRODUCTION-DEFAULT — also den Zustand, den ein
echter Produktionsprozess sieht.

Etappe-1-Abschlussentscheidung: Die Freigabe automatischer Marktpreis-Quellen ist
eine ALLOWLIST mit LEEREM Default (app/config.ALLOWED_MARKET_SOURCES). Kein realer
Marktplatz ist automatisch preisbildend — auch dann nicht, wenn er technisch
erreichbar ist und fachlich einwandfreie Inserate liefert. Welche Quellen ueber
offizielle Such-APIs bzw. Nutzungsrechte qualifiziert werden, klaert eine eigene
Etappe.

Zuvor war die Policy eine BLOCKLIST (mobile.de/autoscout24.de gesperrt, alles
andere implizit erlaubt). Damit haette jede neue oder unbekannte Marktplatz-Domain
automatisch den Marktpreis bestimmt, ohne je qualifiziert worden zu sein.

Der zweite Teil beweist, dass die ENGINE unabhaengig davon funktioniert, WELCHE
Quelle spaeter freigegeben wird: mit einer ausdruecklich freigegebenen
(synthetischen) Domain laeuft der volle Pfad bis zu Median und Marktabdeckung.

    python test_source_boundary.py
"""
import os
import sys
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_sb_"), "test.db")
sys.path.insert(0, ".")

from types import SimpleNamespace                                        # noqa: E402

from app.config import ALLOWED_MARKET_SOURCES                            # noqa: E402
from app.marktvergleich import analysiere_markt, baue_ziel               # noqa: E402
from app.web_search import (                                             # noqa: E402
    SOURCE_POLICY_GRUND, darf_preisbildend_sein, erlaubte_marktquellen,
    marktquellen_freigabe,
)

_fails: list[str] = []


def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        _fails.append(name)


# ══ 1) PRODUCTION-DEFAULT: nichts ist freigegeben ════════════════════════════
check("1: ALLOWED_MARKET_SOURCES ist im Production-Default LEER",
      len(ALLOWED_MARKET_SOURCES) == 0)
check("1b: die aktive Freigabeliste ist ebenfalls leer",
      len(erlaubte_marktquellen()) == 0)

REALE_MARKTPLAETZE = [
    "https://www.kleinanzeigen.de/s-anzeige/bmw-320d/1234567-216-1",
    "https://www.mobile.de/fahrzeuge/details.html?id=412345678",
    "https://suchen.mobile.de/auto/bmw-320d.html",
    "https://www.autoscout24.de/angebote/bmw-320d-12345",
    "https://autoscout24.de/angebote/bmw-320d-12345",
    "https://www.autouncle.de/de/gebrauchtwagen/bmw-320d",
    "https://www.pkw.de/bmw/320d",
    "https://ein-voellig-neuer-marktplatz.example/angebot/1",
]
for url in REALE_MARKTPLAETZE:
    check(f"2: nicht preisbildend im Production-Default — {url[:52]}",
          darf_preisbildend_sein(url) is False)

check("2b: auch eine leere/ungueltige URL ist nicht preisbildend",
      darf_preisbildend_sein("") is False and darf_preisbildend_sein("kein-url") is False)


# ══ 3) Kein Median/Spanne/Coverage aus nicht freigegebenen Quellen ═══════════
BAUREIHE = {"id": "bmw-3er-g20-g21", "marke": "BMW", "modell": "3er",
            "generation": "G20/G21"}
MOTOR = {"bezeichnung": "320d", "motorcode": "B47D20", "kraftstoff": "Diesel",
         "leistung_ps": 190}
REQ = SimpleNamespace(marke="BMW", modell="320d G20", baujahr=2019,
                      kilometerstand=120_000, motor="320d 190 PS",
                      kraftstoff="Diesel", getriebe="Automatik", preis_eur=24_900)
ZIEL = baue_ziel(BAUREIHE, MOTOR, REQ, [BAUREIHE], [
    {"baureihe_id": "bmw-3er-g20-g21", "bezeichnung": "320d", "motorcode": "B47D20"},
    {"baureihe_id": "bmw-3er-g20-g21", "bezeichnung": "330d", "motorcode": "B57D30"}])


def karte(titel, lid, preis, km, ez, beschr):
    bild = f"https://img.example/prod-ads/{lid}-uuid"
    return (f"* [![{titel} Vorschau]({bild})\n\n"
            f"  20](/s-anzeige/x/{lid}-216-1111)\n\n  12307 Berlin\n\n  Heute\n\n"
            f"  ## [{titel}](/s-anzeige/x/{lid}-216-1111)\n\n  {beschr}\n\n"
            f"  {preis} €\n\n  {km} km   EZ {ez}\n")


def bmw_karten(n, id_start, preis_start=24_500, km_start=118_000):
    return "".join(
        karte(f"BMW 320d G20 Advantage #{i}", str(id_start + i),
              str(preis_start + i * 200), str(km_start + i * 500), "05/2019",
              "BMW 320d G20 Limousine, Diesel, Scheckheft")
        for i in range(n))


def seite(url, titel, karten_text):
    return {"url": url, "title": titel, "content": "",
            "raw_content": "## Ergebnisse\n\n" + karten_text}


# Acht fachlich einwandfreie Inserate — aber auf nicht freigegebenen Quellen.
NICHT_FREIGEGEBEN = [
    seite("https://www.kleinanzeigen.de/s-autos/bmw-320d",
          "BMW 320d G20 gebraucht kaufen", bmw_karten(4, 900001)),
    seite("https://www.autoscout24.de/lst/bmw/3er",
          "BMW 320d G20 gebraucht kaufen", bmw_karten(4, 900100)),
]
_ma_nf = analysiere_markt(NICHT_FREIGEGEBEN, ZIEL, 24_900)
check("3: 8 fachlich saubere Inserate nicht freigegebener Quellen -> 0 preisbildend",
      _ma_nf.verwendet == 0)
check("3b: kein Median, keine Spanne",
      _ma_nf.median_eur is None and _ma_nf.spanne_min_eur is None)
check("3c: keine Marktabdeckung durch nicht freigegebene Quellen",
      _ma_nf.anzahl_domains == 0 and _ma_nf.marktabdeckung == "eingeschraenkt")
check("3d: keine verwendete Quell-Domain",
      _ma_nf.quellen_domains == [])


# ══ 4) EXPLIZITE FREIGABE: die Engine funktioniert quellenunabhaengig ════════
FREIGEGEBEN = [seite("https://test-market.example/suche/bmw-320d",
                     "BMW 320d G20 gebraucht kaufen", bmw_karten(8, 910001))]

with marktquellen_freigabe({"test-market.example"}):
    check("4: innerhalb der Freigabe ist die Quelle preisbildend",
          darf_preisbildend_sein("https://test-market.example/x") is True)
    _ma_ok = analysiere_markt(FREIGEGEBEN, ZIEL, 24_900)
    check("4b: saubere Listings der freigegebenen Quelle werden vergleichbar",
          _ma_ok.verwendet >= 6)
    check("4c: Median wird gebildet", _ma_ok.median_eur is not None)
    check("4d: Marktspanne wird gebildet",
          _ma_ok.spanne_min_eur is not None and _ma_ok.spanne_max_eur is not None)
    check("4e: die Quelle zaehlt fuer die Marktabdeckung",
          _ma_ok.anzahl_domains == 1
          and any("test-market.example" in d for d in _ma_ok.quellen_domains))
    check("4f: eine NICHT freigegebene Quelle bleibt auch im Freigabe-Kontext aussen",
          darf_preisbildend_sein("https://www.kleinanzeigen.de/x") is False)

check("5: nach dem Freigabe-Block ist der Production-Default wiederhergestellt",
      len(erlaubte_marktquellen()) == 0
      and darf_preisbildend_sein("https://test-market.example/x") is False)
_ma_danach = analysiere_markt(FREIGEGEBEN, ZIEL, 24_900)
check("5b: dieselben Daten liefern ohne Freigabe wieder 0 preisbildend",
      _ma_danach.verwendet == 0 and _ma_danach.median_eur is None)


# ══ 6) Der Ablehnungsgrund ist neutral formuliert ════════════════════════════
check("6: Policy-Grund nennt weder 'illegal' noch 'verboten' noch 'AGB'",
      not any(w in SOURCE_POLICY_GRUND.lower()
              for w in ("illegal", "verboten", "agb", "rechtswidrig", "verstoss",
                        "verstoß")))
check("6b: Policy-Grund benennt die Freigabe als Ursache",
      "freigegeben" in SOURCE_POLICY_GRUND.lower())

print()
if _fails:
    print(f"{len(_fails)} FEHLER: " + "; ".join(_fails))
    sys.exit(1)
print("Alle Pruefungen bestanden.")
