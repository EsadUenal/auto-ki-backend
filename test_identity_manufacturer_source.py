"""
Lexus-RC-F-Forensik, Package A (Generic Identity) — Root Cause 1.

BEFUND: `_ist_herstellerseite()` (app/web_search.py) prüft gegen die
GESCHLOSSENE Liste `_HERSTELLER_MARKEN`. Fehlt eine Marke dort (Lexus,
Infiniti, Genesis, …), erhält deren EIGENE offizielle Domain score_domain()==0
und zählt für `_identitaet_belegt()` nicht als Stützquelle — unabhängig von
Quellenqualität oder Suchstrategie.

FIX: `_marke_eigene_domain()` (app/technical_research.py) erkennt eine
offizielle Herstellerdomain GENERISCH, über die Ziel-Marke aus der Anfrage
selbst — kein Katalog. Die registrierbare Domain muss dem normalisierten
Markennamen EXAKT entsprechen ("lexus.de" passt zu "Lexus"); jede Domain mit
einem zusätzlichen Wortbestandteil ("lexus-forum.de") bleibt unerkannt.
Bewusst NUR additiv zur Identitätsstufe (`_tier_identitaet`) — `score_domain()`
selbst (app/web_search.py) ist UNVERÄNDERT, wirkt also nicht auf Markt-/
Rückruf-/Schwachstellen-/Wartungs-Bewertung.

    python test_identity_manufacturer_source.py
"""
from app.technical_research import _marke_eigene_domain, _tier, _tier_identitaet
from app.web_search import _HERSTELLER_MARKEN, score_domain

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


# Vorbedingung für B)/§7 Auftrag: "lexus" ist NICHT in der Produktionsliste --
# der Fix funktioniert OHNE sie dort einzutragen.
check("Vorbedingung: 'lexus' ist NICHT in _HERSTELLER_MARKEN (keine Listen-Erweiterung nötig)",
      "lexus" not in _HERSTELLER_MARKEN)

# A) offizielle Domain einer BEREITS gelisteten Marke -> Hersteller-Tier
# (unverändert, über die alte Liste in score_domain()).
check("A) bmw.de / Marke 'BMW' -> score_domain() bereits Hersteller-Tier (48)",
      score_domain("https://www.bmw.de/m4") == 48)
check("A) bmw.de -> _tier_identitaet Tier 1",
      _tier_identitaet("https://www.bmw.de/m4", "BMW") == 1)

# B) offizielle Domain einer BISHER NICHT gelisteten Marke -> Hersteller-Tier,
# OHNE Markenlisten-Erweiterung.
check("B) lexus.de / Marke 'Lexus' -> score_domain() bleibt 0 (Liste unverändert)",
      score_domain("https://www.lexus.de/rc-f") == 0)
check("B) lexus.de -> _marke_eigene_domain() erkennt sie generisch",
      _marke_eigene_domain("lexus.de", "Lexus") is True)
check("B) lexus.de -> _tier_identitaet(..., 'Lexus') liefert trotzdem Tier 1",
      _tier_identitaet("https://www.lexus.de/rc-f", "Lexus") == 1)
check("B) OHNE Ziel-Marke bleibt lexus.de bei der generischen Tier-Bestimmung (Tier 3)",
      _tier_identitaet("https://www.lexus.de/rc-f", None) == 3)

# C) <marke>-forum.* -> NICHT Hersteller.
check("C) lexus-forum.de -> KEIN Hersteller (zusätzlicher Wortbestandteil)",
      _marke_eigene_domain("lexus-forum.de", "Lexus") is False)

# D) <marke>-ersatzteile.* -> NICHT Hersteller.
check("D) lexus-ersatzteile24.de -> KEIN Hersteller",
      _marke_eigene_domain("lexus-ersatzteile24.de", "Lexus") is False)

# E) unabhängige Fachseite mit Markenbegriff im Domainnamen -> NICHT Hersteller.
for fake_domain in ("lexusnews.de", "lexusclub.de", "meinlexus.de"):
    check(f"E) '{fake_domain}' (Markenbegriff enthalten, aber kein exakter Kern) -> KEIN Hersteller",
          _marke_eigene_domain(fake_domain, "Lexus") is False)

# F) bestehende Fälle (BMW/Mercedes/Audi/Toyota/Mazda) bleiben unverändert --
# sowohl über den alten Listen-Pfad (score_domain) als auch über den neuen,
# generischen Pfad (beide müssen zum selben Ergebnis kommen).
_BEKANNTE_MARKEN = [
    ("BMW", "bmw.de"), ("Mercedes-Benz", "mercedes-benz.de"), ("Audi", "audi.de"),
    ("Toyota", "toyota.de"), ("Mazda", "mazda.de"),
]
for marke, domain in _BEKANNTE_MARKEN:
    url = f"https://www.{domain}/modelle"
    alt = score_domain(url) == 48
    neu = _marke_eigene_domain(domain, marke)
    check(f"F) {marke} ({domain}): alter Pfad Hersteller-Tier UND neuer generischer Pfad "
          f"erkennt dieselbe Domain", alt is True and neu is True,
          f"score_domain==48:{alt} marke_eigene_domain:{neu}")
    check(f"F) {marke} ({domain}): _tier_identitaet bleibt Tier 1",
          _tier_identitaet(url, marke) == 1)

# Zusatz: der generische Fix ändert `_tier()` (app/technical_research.py)
# NICHT -- der wirkt weiterhin ausschliesslich über `score_domain()` und ist
# damit für Markt-/Rückruf-/Schwachstellen-/Wartungs-Bewertung unberührt.
check("Zusatz: _tier('lexus.de'-URL) OHNE Markenkontext bleibt unveraendert (3, wie vor dem Fix)",
      _tier("https://www.lexus.de/rc-f") == 3)

print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE MANUFACTURER-SOURCE-TESTS GRUEN")
