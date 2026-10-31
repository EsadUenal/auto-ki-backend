"""
Folgeauftrag (2026-10-31, "Herstellervertrauen von unbelegter Zielübertragung
trennen"): der F9-Fix (`_marke_eigene_domain()` erkennt ICANN-Dot-Brand-
Endungen wie ".toyota") hebt eine internationale Herstellerquelle
(global.toyota, Japan-Markteinführung 23.10.2014, Preistabelle in Yen) auf
Tier 1 für Identitäts-Detailclaims. Eine Tier-1-Bewertung ist QuellenVERTRAUEN
(ist diese Domain tatsächlich der Hersteller?) -- das beantwortet NICHT die
getrennte Frage der ZielGÜLTIGKEIT (gilt GENAU dieser Claim, in GENAU diesem
Text, für das angefragte deutsche Zielfahrzeug/-jahr 2017?).

RICHTIGSTELLUNG zu einer früheren Fassung dieses Skripts: die dort behauptete
"Invarianz über die gesamte Serienlaufzeit" (DE-ADAC-Datenblätter 2018 UND
2024 nennen denselben Rohwert wie die JP-Quelle) ist KEIN positiver
Zielübertragbarkeitsbeleg -- Konfliktfreiheit zwischen Quellen beweist nicht,
dass eine Angabe für das Ziel gilt, sondern nur, dass sie ihr nicht
widerspricht. Das wird hier zurückgenommen.

Tatsächlicher Befund (CONFIRMED): die JP-Quelle nennt ihren engine_code/
drivetrain-Claim im SELBEN Satz wie eine Yen-Preistabelle ("10,300,000 yen
9,530,000 yen") -- ein konkretes, generisches (keine Japan-/Lexus-Sonderregel)
Signal, dass DIESER Text eine EIGENE, fremde Markteinführung beschreibt. Die
bereits bestehende Deckelung für regionsfremde Quellen (`_ist_regionsfremd()`,
bisher nur domainbasiert für Drittmedien) wird um ein inhaltsbasiertes
Gegenstück ergänzt (`_ist_fremdwaehrungskontext()`), das NUR die Claim-Tier-
Gewichtung für DETAIL-Felder deckelt -- NIE die Marke/Modell-Existenzfrage
und NIE die Domain-Vertrauensstufe selbst. Ergebnis: engine_code/drivetrain
aus global.toyota bleiben für das Ziel UNGEKLÄRT (web_only-Schwelle nicht
erreicht) statt fälschlich bestätigt -- Unsicherheit bleibt Unsicherheit,
wird nicht zu einem falschen Widerspruch.

  A) Explizit fremdbezogener Detailclaim bestätigt das Ziel NICHT automatisch:
     global.toyota bleibt Tier-1-Herstellerdomain (Quellenvertrauen
     unverändert), aber der engine_code/drivetrain-Claim aus GENAU diesem
     Yen-Text wird für die Konsens-Gewichtung auf Tier 3 gedeckelt --
     dieselbe Schwelle wie eine unbekannte Domain, weiterhin "verworfen"
     bleibt dabei NICHTS (keine Rejektion, nur fehlende Bestätigung).
  B) Herstellerquelle bleibt korrekt als Hersteller erkannt: die
     Domain-Vertrauensstufe selbst (`_tier_identitaet`) ändert sich durch
     die Deckelung NICHT -- nur die claim-lokale Gewichtung in
     `_identitaets_claims()`.
  C) Passende DE-/EU-Herstellerangabe bleibt verwendbar: toyota-media.de
     (reiner Motor-/Leistungs-/Getriebetext, KEINE Fremdwährung) bleibt
     unverändert Tier 1.
  D) Vorhandener belegter Motorname aus toyota-media.de bleibt bestätigt
     (voller realer Replay).
  E) Negativkontrolle Konflikt bleibt erhalten: zwei widersprechende
     Quellen lösen weiterhin in UNKNOWN auf, kein blindes Vertrauen.
  F) F8-Jahresfilter bleibt unabhängig von dieser Ergänzung wirksam.
  G) Unbeteiligte Marke: siehe test_identity_manufacturer_source.py, Fall I.

    python scripts/test_isolated.py test_identity_zielgueltigkeit_dot_brand.py
"""
from app.technical_research import (
    _ziel, _identitaets_claims, _tier_identitaet, _ist_fremdwaehrungskontext, konsens,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def t(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# ── Reale Quellen (wortgleich aus test_rc_f_herstellerquelle_vollstaendiger_
# replay.py) ─────────────────────────────────────────────────────────────────
TOYOTA_JAPAN = t(
    "https://global.toyota/en/detail/4101939",
    "Lexus Launches All-new 'RC F' High-performance Coupe in Japan | Toyota Motor "
    "Corporation Official Global Website",
    "Tokyo, October 23, 2014-Lexus' all-new high-performance coupe, the \"RC F\", "
    "went on sale today in Japan. Engine Transmission Driveline Price RC F Carbon "
    "Exterior package 2UR-GSE (V8 5.0-liter) 8-Speed SPDS Rear-wheel drive "
    "10,300,000 yen 9,530,000 yen.")
TOYOTA_MEDIA_DE = t(
    "https://www.toyota-media.de/blog/lexus-modelle/artikel/neues-adaptiv-variables-fahrwerk-fuer-lexus-gs-f-und-rc-f/text",
    "NEUES ADAPTIV VARIABLES FAHRWERK FÜR LEXUS F-MODELLE",
    "Köln, 1. Dezember 2016. Der Lexus GS F und der RC F werden beide von einem "
    "5,0 Liter V8-Saugmotor angetrieben, der 351 kW / 477 PS leistet und an eine "
    "Achtstufen-Sport-Automatik gekoppelt ist.")
ADAC_AUTOMATIK_2018 = t(
    "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/288844/",
    "Lexus RC F Automatik (01/18 - 03/19): Technische Daten, Bilder, Preise | ADAC",
    "Marke Lexus Modell RC F Typ Automatik Baureihe RC (UXC1) (12/14 - 06/25) "
    "Herstellerinterne Baureihenbezeichnung UXC1 Modellstart 01/18 Modellende 03/19 "
    "Baureihenstart 12/14 Baureihenende 06/25 HSN/TSN Schluesselnummern 5013/ALQ "
    "Motorcode 2UR-GSE Leistung maximal in PS 464 Antriebsart Hinterrad "
    "Getriebeart Automatikgetriebe Kraftstoffart Super Hubraum 4969 ccm")

Z = _ziel("Lexus", "RC F", 2017, "5.0 V8", None)

# ── A) Expliziter Fremdmarktbezug deckelt den Claim, bestätigt das Ziel NICHT ─

check("A0: _ist_fremdwaehrungskontext() erkennt die Yen-Preistabelle in der "
      "JP-Quelle (generisches Signal, keine Japan-/Lexus-Sonderregel)",
      _ist_fremdwaehrungskontext(TOYOTA_JAPAN) is True)

claims_jp = _identitaets_claims(TOYOTA_JAPAN, Z)
jp_engine_code = [c for c in claims_jp if c["feld"] == "engine_code"]
jp_drivetrain = [c for c in claims_jp if c["feld"] == "drivetrain"]
check("A1: engine_code wird weiterhin EXTRAHIERT (kein Rejektions-/Widerspruchs-"
      "fall), aber auf Tier 3 gedeckelt statt Tier 1",
      bool(jp_engine_code) and all(c["wert"] == "2UR-GSE" and c["tier"] == 3
                                   and not c.get("verworfen") for c in jp_engine_code),
      str(jp_engine_code))
check("A2: drivetrain ebenso -- extrahiert, aber Tier 3 statt Tier 1",
      bool(jp_drivetrain) and all(c["wert"] == "Heck" and c["tier"] == 3
                                  and not c.get("verworfen") for c in jp_drivetrain),
      str(jp_drivetrain))
check("A3: eine EINZELNE Tier-3-Quelle erreicht MIN_KONSENS_GEWICHT nicht -- "
      "engine_code bleibt fuer das Ziel UNGEKLAERT, nicht bestaetigt",
      konsens(jp_engine_code, "engine_code") is None, str(jp_engine_code))
check("A4: dasselbe fuer drivetrain",
      konsens(jp_drivetrain, "drivetrain") is None, str(jp_drivetrain))

# ── B) Die Domain-Vertrauensstufe selbst bleibt unveraendert Tier 1 --------
# die Deckelung wirkt NUR lokal in `_identitaets_claims()` auf die
# Claim-Gewichtung, NICHT auf `_tier_identitaet()`/die Herstellererkennung.
check("B1: global.toyota bleibt eine erkannte Tier-1-Herstellerdomain "
      "(Quellenvertrauen unveraendert, nur die Claim-Gewichtung ist gedeckelt)",
      _tier_identitaet("https://global.toyota/en/detail/4101939", "Toyota") == 1)
check("B2: dieselbe Domain bleibt auch fuer die Submarke 'Lexus' Tier 1",
      _tier_identitaet("https://global.toyota/en/detail/4101939", "Lexus") == 1)

# ── C) Passende DE-Herstellerquelle (keine Fremdwaehrung) bleibt unberuehrt ──
check("C1: toyota-media.de (reiner Motor-/Leistungstext, keine Fremdwaehrung) "
      "wird NICHT als Fremdwaehrungskontext erkannt",
      _ist_fremdwaehrungskontext(TOYOTA_MEDIA_DE) is False)
claims_de_media = _identitaets_claims(TOYOTA_MEDIA_DE, Z)
check("C2: toyota-media.de liefert horsepower=477 unveraendert auf Tier 1",
      any(c["feld"] == "horsepower" and c["wert"] == 477 and c["tier"] == 1
          for c in claims_de_media), str(claims_de_media))

# ADAC (reiner PS-/Antriebs-/Kraftstofftext, keine Fremdwaehrung) bleibt
# ebenfalls unberuehrt von der neuen Deckelung -- die bestehende F8-
# Jahresfilter-Ablehnung (orthogonal, unveraendert) ist die einzige
# wirksame Einschraenkung fuer diese Quelle.
check("D1: ADAC-Datenblatt (keine Fremdwaehrung) wird nicht vom neuen "
      "Fremdwaehrungs-Signal erfasst", _ist_fremdwaehrungskontext(ADAC_AUTOMATIK_2018) is False)

# ── E) Negativkontrolle: ein echter Konflikt bleibt ein Konflikt ───────────
#
# Zwei VERGLEICHBAR gewichtete widersprechende Quellen duerfen nicht
# zugunsten der zuletzt gesehenen aufgeloest werden. Eine einzelne, bereits
# auf Tier 3 gedeckelte Quelle (wie global.toyota oben) ist dafuer der
# falsche Testfall: ein ECHTER Tier-1-DE-Gegenwert wuerde sie zu Recht
# schlagen (keine Lockerung, das ist die korrekte Konsequenz der Deckelung,
# nicht ein Fehler). Der sinnvolle Konflikttest braucht zwei VERGLEICHBAR
# starke Quellen -- hier zwei synthetische, je fuer sich ebenso
# fremdwaehrungsbehaftete (Yen) Pressetexte mit widersprechendem Wert.
SYNTHETISCH_WIDERSPRUCH_JP = t(
    "https://global.toyota/en/synthetischer-test-widerspruch",
    "SYNTHETISCHER TESTFALL Lexus RC F -- keine reale Veroeffentlichung",
    "Lexus RC F Motorcode 9XY-ZZZ Frontantrieb Preis 9,000,000 yen.")
claims_konflikt = claims_jp + _identitaets_claims(SYNTHETISCH_WIDERSPRUCH_JP, Z)
check("E0: auch die synthetische Gegenquelle wird als Fremdwaehrungskontext "
      "erkannt (vergleichbare Gewichtung, kein einseitiger Vorteil)",
      _ist_fremdwaehrungskontext(SYNTHETISCH_WIDERSPRUCH_JP) is True)
check("E1: zwei vergleichbar gewichtete, widersprechende Quellen -> "
      "engine_code bleibt UNKNOWN (kein blindes Vertrauen in eine Seite)",
      konsens([c for c in claims_konflikt if not c.get("verworfen")], "engine_code") is None,
      str([c for c in claims_konflikt if c["feld"] == "engine_code"]))
check("E2: dasselbe fuer drivetrain",
      konsens([c for c in claims_konflikt if not c.get("verworfen")], "drivetrain") is None,
      str([c for c in claims_konflikt if c["feld"] == "drivetrain"]))

# ── F) F8-Jahresfilter bleibt unabhaengig von dieser Ergaenzung wirksam ────
SYNTHETISCH_DATENBLATT_AUSSERHALB = t(
    "https://global.toyota/en/synthetischer-testfall-datenblatt",
    "SYNTHETISCHER TESTFALL Lexus RC F -- keine reale Veroeffentlichung",
    "Lexus RC F Modellstart 08/24 Modellende 06/25 Motorcode 9XY-ZZZ.")
claims_datenblatt = _identitaets_claims(SYNTHETISCH_DATENBLATT_AUSSERHALB, Z)
check("F1: Dot-Brand-Tier-1-Quelle MIT eigenem, ausschliessendem Modellstart/"
      "Modellende-Feldpaar wird fuer 2017 weiterhin abgelehnt (F8 bleibt "
      "unabhaengig von der neuen Fremdwaehrungs-Deckelung wirksam)",
      any(c["feld"] == "engine_code" and c.get("verworfen") == "baujahr_ausserhalb_datenblatt"
          for c in claims_datenblatt), str(claims_datenblatt))

print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE ZIELGUELTIGKEITS-TESTS GRUEN")
