"""
Folgeauftrag (2026-10-31, "Zielgültigkeit darf nicht durch Quellengewicht
ersetzt werden"): der F9-Fix (`_marke_eigene_domain()` erkennt ICANN-Dot-
Brand-Endungen wie ".toyota") hebt eine internationale Herstellerquelle
(global.toyota, Japan-Markteinführung 23.10.2014) auf Tier 1 für Identitäts-
Detailclaims. Eine Tier-1-Bewertung ist QuellenVERTRAUEN (ist diese Domain
tatsächlich der Hersteller?) -- das beantwortet NICHT die getrennte Frage der
ZielGÜLTIGKEIT (gilt GENAU dieser Claim für das angefragte deutsche
Zielfahrzeug/-jahr 2017?).

RICHTIGSTELLUNG (zweite Runde) zu einer früheren Fassung dieses Skripts:
1) "Invarianz über die gesamte Serienlaufzeit" (Konfliktfreiheit mit
   späteren DE-ADAC-Datenblättern) ist KEIN Zielübertragbarkeitsbeleg --
   bereits zurückgenommen.
2) Die danach eingeführte reine Inhaltsheuristik ("enthält der Text eine
   fremde Währung", `_ist_fremdwaehrungskontext()`) senkt zwar das
   Claim-Gewicht, ist aber KEIN robuster Zielgültigkeitsbeleg: Gegenfall B
   unten zeigt real, dass das bloße Entfernen der Yen-Preistabelle --
   bei unverändert ausdrücklich japanischem Marktbezug im übrigen Text --
   den Claim wieder auf Tier 1 zurückfallen UND bestätigen lässt. Entfernen
   einer Zahl schafft keine neue Gültigkeit.

FIX (diese Runde): primäres, robustes Signal ist nicht mehr eine
Inhaltsheuristik, sondern dieselbe Art curatierter, einzeln belegter
Tatsache wie `_HERSTELLER_PRESSEDOMAINS`s `marken`-Zuordnung selbst --
`Pressedomain.fremder_absatzmarkt` (app/web_search.py), ausgewertet über
`pressedomain_fremder_absatzmarkt()`. Für "global.toyota" ist das Faktum
jetzt einzeln dokumentiert (Dateline "Tokyo", "went on sale today in
Japan", Yen-Preise -- unabhängig davon, ob EIN KONKRETES Dokument diese
Preiszeile zufällig enthält oder nicht). `_ist_fremdwaehrungskontext()`
bleibt als ZUSÄTZLICHES, schwächeres Signal für noch nicht curatierte
fremde Pressedomains erhalten -- keine Ersatzheuristik, kein Rückbau.

  A) Kausaler Mehrquellen-Gegenfall (WIDERLEGT): zwei unabhängige,
     explizit fremdmarktgebundene Quellen mit ÜBEREINSTIMMENDEM Wert
     können einen Claim NICHT gemeinsam bestätigen -- `konsens()`
     verlangt weiterhin mindestens eine "starke" (Tier<=2) Quelle
     (`stark = any(t<=2 ...)`); reines Aufsummieren vieler Tier-3-Gewichte
     genügt nicht, unabhängig von der Anzahl.
  B) Entfernte Preistabelle bei unverändert explizit fremdem Markt
     (vorher BESTÄTIGT, jetzt behoben): derselbe Pressetext OHNE die
     Yen-Zeile wird jetzt trotzdem über das curatierte Domain-Faktum
     gedeckelt -- Entfernen der Zahl ändert den Status nicht mehr.
  C) Passende deutsche Herstellerquelle bestätigt weiterhin: toyota-
     media.de bleibt unverändert Tier 1, unberührt von der Deckelung.
  D) Herstellervertrauen bleibt erhalten: `_tier_identitaet()` für
     global.toyota bleibt unverändert Tier 1 -- nur die Claim-Gewichtung
     ist gedeckelt, nicht die Domain-Erkennung selbst.
  E) Vollständiger realer Replay zeigt den korrekten finalen Feldstatus
     (test_rc_f_herstellerquelle_vollstaendiger_replay.py, hier nur als
     Verweis/Sanity, kein erneuter Breitlauf).

GRENZE (bewusst nicht geschlossen): die curatierte Domain-Zuordnung deckt
nur bereits einzeln geprüfte Presse-Domains ab. Eine BISHER UNDOKUMENTIERTE
fremde Herstellerdomain ohne Fremdwährungssignal im Text würde WEDER vom
Domain-Faktum NOCH von der Inhaltsheuristik erfasst -- das ist eine
offene, nicht automatisch schließbare Lücke (keine Länder-Katalogisierung
vorgesehen), kein stillschweigend als vollständig behaupteter Schutz.

    python scripts/test_isolated.py test_identity_zielgueltigkeit_dot_brand.py
"""
from app.technical_research import (
    _ziel, _identitaets_claims, _tier_identitaet, _ist_fremdwaehrungskontext, konsens,
)
from app.web_search import pressedomain_fremder_absatzmarkt

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def t(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


Z = _ziel("Lexus", "RC F", 2017, "5.0 V8", None)

TOYOTA_JAPAN_MIT_PREIS = t(
    "https://global.toyota/en/detail/4101939",
    "Lexus Launches All-new 'RC F' High-performance Coupe in Japan | Toyota Motor "
    "Corporation Official Global Website",
    "Tokyo, October 23, 2014-Lexus' all-new high-performance coupe, the \"RC F\", "
    "went on sale today in Japan. Engine Transmission Driveline Price RC F Carbon "
    "Exterior package 2UR-GSE (V8 5.0-liter) 8-Speed SPDS Rear-wheel drive "
    "10,300,000 yen 9,530,000 yen.")

check("Vorbedingung: global.toyota ist als fremder Absatzmarkt curatiert",
      pressedomain_fremder_absatzmarkt("https://global.toyota/en/detail/4101939") is True)

# ── A) Kausaler Mehrquellen-Gegenfall: widerlegt ────────────────────────────
# Zwei unabhaengige, je fuer sich explizit fremdmarktgebundene Quellen
# (SYNTHETISCH, zweite Domain erfunden) mit UEBEREINSTIMMENDEM Wert.
QUELLE_A2_SYNTHETISCH = t(
    "https://press.toyota.jp/releases/synthetischer-testfall-rc-f",
    "SYNTHETISCHER TESTFALL Lexus RC F Press Release Japan -- keine reale "
    "Veroeffentlichung",
    "Lexus RC F. Engine Transmission Driveline 2UR-GSE (V8 5.0-liter) "
    "8-Speed SPDS Rear-wheel drive 9,800,000 yen.")
claims_a = _identitaets_claims(TOYOTA_JAPAN_MIT_PREIS, Z) + _identitaets_claims(QUELLE_A2_SYNTHETISCH, Z)
ec_a = [c for c in claims_a if c["feld"] == "engine_code" and not c.get("verworfen")]
dt_a = [c for c in claims_a if c["feld"] == "drivetrain" and not c.get("verworfen")]
check("A1: zwei unabhaengige Quellen mit UEBEREINSTIMMENDEM Wert tragen "
      "beide Tier 3 (gedeckelt, kein Freifahrtschein durch Uebereinstimmung)",
      len(ec_a) == 2 and all(c["tier"] == 3 for c in ec_a), str(ec_a))
check("A2: trotz zwei uebereinstimmender Quellen bleibt engine_code UNGEKLAERT "
      "-- konsens() verlangt weiterhin mindestens eine starke (Tier<=2) Quelle, "
      "reines Aufsummieren von Tier-3-Gewichten genuegt nicht",
      konsens(ec_a, "engine_code") is None, str(ec_a))
check("A3: dasselbe fuer drivetrain", konsens(dt_a, "drivetrain") is None, str(dt_a))

# ── B) Entfernte Preistabelle: vorher Bestaetigung, jetzt weiterhin UNGEKLAERT ──
TOYOTA_JAPAN_OHNE_PREIS = t(
    "https://global.toyota/en/detail/4101939",
    "Lexus Launches All-new 'RC F' High-performance Coupe in Japan | Toyota Motor "
    "Corporation Official Global Website",
    "Tokyo, October 23, 2014-Lexus' all-new high-performance coupe, the \"RC F\", "
    "went on sale today in Japan. Engine Transmission Driveline RC F Carbon "
    "Exterior package 2UR-GSE (V8 5.0-liter) 8-Speed SPDS Rear-wheel drive.")
check("B1: ohne Preistabelle erkennt die reine Inhaltsheuristik nichts mehr "
      "(zeigt, warum sie allein kein robuster Beleg ist)",
      _ist_fremdwaehrungskontext(TOYOTA_JAPAN_OHNE_PREIS) is False)
claims_b = _identitaets_claims(TOYOTA_JAPAN_OHNE_PREIS, Z)
ec_b = [c for c in claims_b if c["feld"] == "engine_code"]
dt_b = [c for c in claims_b if c["feld"] == "drivetrain"]
check("B2: das curatierte Domain-Faktum deckelt trotzdem weiterhin auf Tier 3 "
      "-- Entfernen der Zahl aendert den Status NICHT",
      bool(ec_b) and all(c["tier"] == 3 and not c.get("verworfen") for c in ec_b), str(ec_b))
check("B3: engine_code bleibt dadurch UNGEKLAERT, nicht bestaetigt",
      konsens(ec_b, "engine_code") is None, str(ec_b))
check("B4: dasselbe fuer drivetrain",
      bool(dt_b) and konsens(dt_b, "drivetrain") is None, str(dt_b))

# ── C) Passende deutsche Herstellerquelle bestaetigt weiterhin ────────────
TOYOTA_MEDIA_DE = t(
    "https://www.toyota-media.de/blog/lexus-modelle/artikel/neues-adaptiv-variables-fahrwerk-fuer-lexus-gs-f-und-rc-f/text",
    "NEUES ADAPTIV VARIABLES FAHRWERK FÜR LEXUS F-MODELLE",
    "Köln, 1. Dezember 2016. Der Lexus GS F und der RC F werden beide von einem "
    "5,0 Liter V8-Saugmotor angetrieben, der 351 kW / 477 PS leistet und an eine "
    "Achtstufen-Sport-Automatik gekoppelt ist.")
check("C1: toyota-media.de ist NICHT als fremder Absatzmarkt curatiert",
      pressedomain_fremder_absatzmarkt(
          "https://www.toyota-media.de/blog/lexus-modelle/artikel/neues-adaptiv-"
          "variables-fahrwerk-fuer-lexus-gs-f-und-rc-f/text") is False)
claims_de = _identitaets_claims(TOYOTA_MEDIA_DE, Z)
check("C2: toyota-media.de liefert horsepower=477 unveraendert auf Tier 1",
      any(c["feld"] == "horsepower" and c["wert"] == 477 and c["tier"] == 1
          for c in claims_de), str(claims_de))

# ── D) Herstellervertrauen bleibt erhalten (nur Claim-Gewichtung gedeckelt) ─
check("D1: global.toyota bleibt eine erkannte Tier-1-Herstellerdomain fuer 'Toyota'",
      _tier_identitaet("https://global.toyota/en/detail/4101939", "Toyota") == 1)
check("D2: global.toyota bleibt eine erkannte Tier-1-Herstellerdomain fuer die "
      "Submarke 'Lexus'",
      _tier_identitaet("https://global.toyota/en/detail/4101939", "Lexus") == 1)

print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE ZIELGUELTIGKEITS-TESTS GRUEN")
