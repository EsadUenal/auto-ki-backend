"""
Test-/Replay-Harness fuer die Source-Policy — NUR fuer Tests, NIE fuer Production.

Ab dem Etappe-1-Abschluss ist die Freigabe automatischer Marktpreis-Quellen eine
ALLOWLIST mit LEEREM Production-Default (app/config.ALLOWED_MARKET_SOURCES). Keine
reale Marktplatz-Domain ist automatisch preisbildend.

Viele bestehende Regressionstests und die historischen BMW-Diagnose-Mitschnitte
enthalten aber reale Domainnamen (vor allem kleinanzeigen.de) — sie sollen die
ANALYSE-ENGINE weiterhin pruefen koennen. Dieses Modul gibt diese Domains
ausschliesslich im Testprozess frei, indem es beim Import
`setze_marktquellen_freigabe(...)` aufruft.

    import _source_policy_testharness  # noqa: F401

AUSDRUECKLICH:
  - Eine hier erteilte Freigabe belegt KEINE produktive Qualifikation der Quelle.
    Sie sagt nur: "dieser Testfall darf die Engine mit dieser Domain fahren".
  - Der Production-Code wird dadurch NICHT veraendert. Ein Prozess, der dieses
    Modul nicht importiert (also jeder Produktionsprozess), sieht weiterhin die
    leere Freigabeliste.
  - mobile.de und autoscout24.de sind hier BEWUSST NICHT enthalten: mehrere Tests
    pruefen ausdruecklich, dass diese Quellen NICHT preisbildend werden. Eine
    pauschale Freigabe wuerde genau diese Negativtests entwerten.
"""
from app.web_search import setze_marktquellen_freigabe

# Historische Engine-Test-Domains (reale Mitschnitte) + synthetische Testdomains,
# die in den Fixtures der Marktvergleichstests vorkommen.
TEST_MARKTQUELLEN = frozenset({
    # real, aus historischen Diagnose-Mitschnitten (BMW-Replays, Insignia-Lauf)
    "kleinanzeigen.de",
    "ebay-kleinanzeigen.de",
    "autouncle.de",
    # synthetische Fixture-Domains der bestehenden Testdateien
    "a.de", "b.de", "c.de", "d.de", "x.de",
    "beispiel.de", "beispielportal.de", "irgendein-haendler.de",
})

setze_marktquellen_freigabe(TEST_MARKTQUELLEN)
