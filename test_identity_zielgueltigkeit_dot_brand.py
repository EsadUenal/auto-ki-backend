"""
Folgeauftrag (2026-10-31, "Herstellerquellenfix zielgültig abschließen"):
der F9-Fix (`_marke_eigene_domain()` erkennt ICANN-Dot-Brand-Endungen wie
".toyota") hebt eine internationale Herstellerquelle (global.toyota, Japan-
Markteinführung 23.10.2014) auf Tier 1 für Identitäts-Detailclaims. Eine
Tier-1-Bewertung allein ist aber QuellenVERTRAUEN, keine ZielGÜLTIGKEIT
(gilt diese konkrete Angabe für das angefragte Zielfahrzeug/-jahr/-markt?)
-- dieses Skript trennt beide Fragen explizit und prüft real:

  A) Positive Zielgültigkeit: die internationale Quelle selbst trägt KEIN
     Modellstart/Modellende-Feldpaar (F8-Mechanismus greift nicht, weil er
     nur auf dieses strukturierte Format reagiert) -- das beweist für sich
     allein NICHTS. Positiver Beleg stattdessen: zwei UNABHÄNGIGE, reale
     DE-Marktquellen (ADAC-Datenblätter an entgegengesetzten Enden der
     tatsächlichen Seriendauer, 01/18-03/19 UND 08/24-06/25) nennen
     übereinstimmend denselben Motorcode/dieselbe Antriebsart wie die
     JP-Quelle -- echte Invarianz über die gesamte reale Serienlaufzeit,
     kein bloßes Fehlen eines Ausschlussgrunds.
  B) Negativkontrolle Konflikt: eine zweite, klar als SYNTHETISCH
     gekennzeichnete internationale Herstellerquelle mit einem
     ABWEICHENDEN Wert für dasselbe Feld darf NICHT blind zugunsten der
     zuletzt gesehenen Tier-1-Quelle aufgelöst werden -- `konsens()` muss
     weiterhin `None` (Konflikt) liefern, keine Lockerung durch den Fix.
  C) Datenblatt-Format bleibt wirksam: trägt eine Dot-Brand-/Pressedomain-
     Quelle selbst ein eigenes, das Zieljahr ausschließendes Modellstart/
     Modellende-Feldpaar, bleibt der bestehende F8-Jahresfilter
     unverändert wirksam -- Tier 1 ist kein Freifahrtschein gegen F8.
  D) Unbeteiligte Marke: siehe test_identity_manufacturer_source.py, Fall
     I (dort bereits abgedeckt, hier nicht erneut).
  E) Bestehende DE-/EU-Quelle bleibt bevorzugt nutzbar: der volle reale
     Replay (test_rc_f_herstellerquelle_vollstaendiger_replay.py) zeigt
     engine_name/horsepower/transmission unverändert über toyota-media.de
     bestätigt -- hier nur als Verweis/Sanity, kein erneuter Breitlauf.

    python scripts/test_isolated.py test_identity_zielgueltigkeit_dot_brand.py
"""
from app.technical_research import _ziel, _identitaets_claims, ausgerichtet, konsens

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def t(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# ── Reale Quellen (wortgleich aus test_rc_f_herstellerquelle_vollstaendiger_
# replay.py / test_rc_f_evidenzluecken_vertieft.py) ─────────────────────────
TOYOTA_JAPAN = t(
    "https://global.toyota/en/detail/4101939",
    "Lexus Launches All-new 'RC F' High-performance Coupe in Japan | Toyota Motor "
    "Corporation Official Global Website",
    "Tokyo, October 23, 2014-Lexus' all-new high-performance coupe, the \"RC F\", "
    "went on sale today in Japan. Engine Transmission Driveline Price RC F Carbon "
    "Exterior package 2UR-GSE (V8 5.0-liter) 8-Speed SPDS Rear-wheel drive "
    "10,300,000 yen 9,530,000 yen.")
ADAC_ULTIMATE_2024 = t(
    "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/",
    "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25): Technische Daten, "
    "Bilder, Preise | ADAC",
    "Marke Lexus Modell RC F Typ Ultimate Edition Automatik Baureihe RC (UXC1) "
    "(12/14 - 06/25) Herstellerinterne Baureihenbezeichnung UXC1 Modellstart 08/24 "
    "Modellende 06/25 Baureihenstart 12/14 Baureihenende 06/25 Motorcode 2UR-GSE "
    "Leistung maximal in PS 464 Antriebsart Hinterrad Getriebeart Automatikgetriebe "
    "Kraftstoffart Super Hubraum 4969 ccm")
ADAC_AUTOMATIK_2018 = t(
    "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/288844/",
    "Lexus RC F Automatik (01/18 - 03/19): Technische Daten, Bilder, Preise | ADAC",
    "Marke Lexus Modell RC F Typ Automatik Baureihe RC (UXC1) (12/14 - 06/25) "
    "Herstellerinterne Baureihenbezeichnung UXC1 Modellstart 01/18 Modellende 03/19 "
    "Baureihenstart 12/14 Baureihenende 06/25 HSN/TSN Schluesselnummern 5013/ALQ "
    "Motorcode 2UR-GSE Leistung maximal in PS 464 Antriebsart Hinterrad "
    "Getriebeart Automatikgetriebe Kraftstoffart Super Hubraum 4969 ccm")

Z = _ziel("Lexus", "RC F", 2017, "5.0 V8", None)

# ── A) Positive Zielgültigkeit: reale Invarianz statt bloßer Nicht-Ablehnung ─

claims_jp = _identitaets_claims(TOYOTA_JAPAN, Z)
jp_engine_code = {c["wert"] for c in claims_jp if c["feld"] == "engine_code" and not c.get("verworfen")}
jp_drivetrain = {c["wert"] for c in claims_jp if c["feld"] == "drivetrain" and not c.get("verworfen")}
check("A1: global.toyota liefert engine_code='2UR-GSE' auf Tier 1 (ungeachtet verworfen-Status)",
      {c["wert"] for c in claims_jp if c["feld"] == "engine_code"} == {"2UR-GSE"}
      and all(c["tier"] == 1 for c in claims_jp if c["feld"] == "engine_code"), str(claims_jp))
check("A2: global.toyota liefert drivetrain='Heck' auf Tier 1 (ungeachtet verworfen-Status)",
      {c["wert"] for c in claims_jp if c["feld"] == "drivetrain"} == {"Heck"}
      and all(c["tier"] == 1 for c in claims_jp if c["feld"] == "drivetrain"), str(claims_jp))

# Die beiden DE-Datenblätter werden vom F8-Jahresfilter für 2017 korrekt
# ABGELEHNT (decken 01/18-03/19 bzw. 08/24-06/25) -- das ist bereits
# geprüftes, unverändertes F8-Verhalten und wird hier NICHT neu bewertet.
# Entscheidend für die Zielgültigkeitsfrage ist NICHT ihr verworfen-Status,
# sondern ob ihr ROHWERT (vor dem Verwerfen) der JP-Quelle widerspricht.
for label, quelle in (("2018er Datenblatt", ADAC_AUTOMATIK_2018),
                      ("2024er Datenblatt", ADAC_ULTIMATE_2024)):
    claims_de = _identitaets_claims(quelle, Z)
    de_engine_code = {c["wert"] for c in claims_de if c["feld"] == "engine_code"}
    de_drivetrain = {c["wert"] for c in claims_de if c["feld"] == "drivetrain"}
    check(f"A3 ({label}): wird vom F8-Jahresfilter für 2017 korrekt abgelehnt "
          f"(bestehendes, unveraendertes Verhalten)",
          all(c.get("verworfen") == "baujahr_ausserhalb_datenblatt"
              for c in claims_de if c["feld"] in ("engine_code", "drivetrain")),
          str(claims_de))
    check(f"A4 ({label}): ROHWERT widerspricht der JP-Quelle NICHT -- reale "
          f"Invarianz ueber die gesamte Seriendauer (2014-2025), nicht bloss "
          f"Abwesenheit eines Ausschlussgrunds",
          de_engine_code == jp_engine_code and de_drivetrain == jp_drivetrain,
          f"DE engine_code={de_engine_code} drivetrain={de_drivetrain} vs. "
          f"JP engine_code={jp_engine_code} drivetrain={jp_drivetrain}")

# ── B) Negativkontrolle: ein echter Konflikt bleibt ein Konflikt ───────────
#
# SYNTHETISCHE Zweitquelle (klar gekennzeichnet, keine reale Veroeffentlichung):
# dieselbe, bereits als Tier-1-Pressedomain gefuehrte toyota-media.de nennt
# hier einen ABWEICHENDEN Motorcode fuer dieselbe Anfrage. Der Fix darf
# NICHT dazu fuehren, dass die zuletzt gesehene oder zuerst gesehene Tier-1-
# Quelle blind gewinnt -- `konsens()` muss weiterhin None (Konflikt)
# liefern, exakt wie bei jedem anderen widersprochenen Feld.
SYNTHETISCH_WIDERSPRUCH = t(
    "https://www.toyota-media.de/presse/synthetischer-test-widerspruch",
    "SYNTHETISCHER TESTFALL -- keine reale Veroeffentlichung",
    "Der Lexus RC F hat laut dieser SYNTHETISCHEN Testquelle den Motorcode "
    "9XY-ZZZ und Frontantrieb.")
claims_konflikt = claims_jp + _identitaets_claims(SYNTHETISCH_WIDERSPRUCH, Z)
check("B1: zwei widersprechende Tier-1-Quellen -> engine_code bleibt UNKNOWN "
      "(konsens() liefert None, kein blindes Vertrauen in eine Seite)",
      konsens([c for c in claims_konflikt if not c.get("verworfen")], "engine_code") is None,
      str([c for c in claims_konflikt if c["feld"] == "engine_code"]))
check("B2: zwei widersprechende Tier-1-Quellen -> drivetrain bleibt UNKNOWN",
      konsens([c for c in claims_konflikt if not c.get("verworfen")], "drivetrain") is None,
      str([c for c in claims_konflikt if c["feld"] == "drivetrain"]))

# ── C) F8-Jahresfilter bleibt wirksam, auch fuer eine Dot-Brand-/
# Pressedomain-Tier-1-Quelle, wenn sie selbst ein ausschliessendes
# Modellstart/Modellende-Feldpaar traegt (SYNTHETISCH, reale globale
# Pressemitteilungen tragen dieses Feldpaar nicht -- genau deshalb greift
# F8 bei TOYOTA_JAPAN oben NICHT). Zeigt: Tier 1 ist kein Freifahrtschein. ──
SYNTHETISCH_DATENBLATT_AUSSERHALB = t(
    "https://global.toyota/en/synthetischer-testfall-datenblatt",
    "SYNTHETISCHER TESTFALL Lexus RC F -- keine reale Veroeffentlichung",
    "Lexus RC F Modellstart 08/24 Modellende 06/25 Motorcode 9XY-ZZZ.")
claims_datenblatt = _identitaets_claims(SYNTHETISCH_DATENBLATT_AUSSERHALB, Z)
check("C1: Dot-Brand-Tier-1-Quelle MIT eigenem, ausschliessendem Modellstart/"
      "Modellende-Feldpaar wird fuer 2017 trotzdem abgelehnt (F8 bleibt wirksam)",
      any(c["feld"] == "engine_code" and c.get("verworfen") == "baujahr_ausserhalb_datenblatt"
          for c in claims_datenblatt), str(claims_datenblatt))

print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE ZIELGUELTIGKEITS-TESTS GRUEN")
