"""
Folgeauftrag (2026-11-01, "Dokumentbezogene Marktprüfung statt Domain-
Sonderregel"): der F9-Fix (`_marke_eigene_domain()` erkennt ICANN-Dot-Brand-
Endungen wie ".toyota") hebt eine internationale Herstellerquelle
(global.toyota) auf Tier 1 für Identitäts-Detailclaims. Eine Tier-1-
Bewertung ist QuellenVERTRAUEN -- das beantwortet NICHT die getrennte Frage
der ZielGÜLTIGKEIT (gilt GENAU dieser Claim für das angefragte deutsche
Zielfahrzeug/-jahr 2017?).

RICHTIGSTELLUNG (dritte Runde) zu zwei vorherigen, jeweils FALSCH
GRANULIERTEN Fassungen dieses Mechanismus:
1) "Invarianz über die Serienlaufzeit" (Konfliktfreiheit mit späteren
   DE-Datenblättern) ist KEIN Zielübertragbarkeitsbeleg -- zurückgenommen.
2) Eine reine Inhaltsheuristik ("enthält der Text eine fremde Währung")
   ist trivial zu umgehen (Entfernen der Preiszeile änderte den Status) --
   zurückgenommen.
3) Ein curatiertes DOMAIN-Faktum ("diese ganze Domain ist fremder
   Absatzmarkt") generalisiert den Geltungsbereich EINES Artikels
   fälschlich auf die GESAMTE Domain -- ein synthetischer Gegentest zeigte
   real, dass dadurch auch ein ausdrücklich für Deutschland/EU gültiger
   Text auf DERSELBEN Domain mitgedeckelt worden wäre. Zurückgenommen;
   `_HERSTELLER_PRESSEDOMAINS` ist wieder ein reines Markenfaktum.

FIX (diese Runde): DOKUMENT-/SATZBEZOGENE Prüfung (`_fremdmarkt_spannen()`,
app/technical_research.py) -- erkennt eine ausdrückliche Markteinführungs-/
Verkaufsstart-Aussage ("went on sale ... in <Ort>", "Markteinführung in
<Ort>") für einen Ort, der NICHT Deutschland/EU ist (ein EINZIGER
Ziel-Vergleich, keine Länderkatalogisierung). Der Geltungsbereich bleibt
auf den Anker-Satz und den UNMITTELBAR folgenden Satz INNERHALB desselben
Segments/Absatzes beschränkt (wiederverwendet die bereits bestehende
Segmentgrenzen-Erkennung, `_saetze_mit_segmentgrenze()`/
`_segment_kontext_saetze()` -- kein Parallelbau). Claim-Positionen (`m.start()`)
werden jetzt pro Claim-Aufruf an `add()` übergeben, die Tier-Deckelung wirkt
dadurch NUR auf tatsächlich co-lokalisierte Claims, nicht mehr dokument-
oder domainweit.

  A) Expliziter Fremdmarktbezug MIT Preiszeile (unverändert erkannt).
  B) Dieselbe Aussage OHNE Preiszeile: GLEICHE Behandlung (das eigentliche
     Signal ist der Satz, nicht die Zahl).
  C) Ausdrücklich zum Zielmarkt passender Text auf DERSELBEN Herstellerdomain
     wird NICHT wegen des anderen Artikels gedeckelt.
  D) Bloße Orts-/Werksnennung (kein Verkaufsstart-Verb) löst KEINE
     pauschale Ablehnung aus.
  E) Getrennte Fahrzeug-/Marktabschnitte (Absatzgrenze): keine gegenseitige
     Freigabe -- ein späterer, unabhängiger DE-Abschnitt bleibt unberührt.
  F) Bestehende deutsche Herstellerquelle (toyota-media.de) bleibt bestätigt.
  G) Herstellervertrauen bleibt erhalten (Domain-Tier unverändert).
  H) Mehrquellen-Gegenfall (bereits in Runde 2 anerkannt, hier nur als
     Regressionsschutz, nicht neu untersucht).
  I) Vollständiger realer Replay: siehe
     test_rc_f_herstellerquelle_vollstaendiger_replay.py (Verweis, kein
     erneuter Breitlauf hier).

VERBLEIBENDE ERKENNUNGSGRENZEN (bewusst nicht geschlossen, keine
Vollständigkeit behauptet): nur dieses eine Satzmuster (Verkaufsstart-Verb +
"in <Ort>", Deutsch/Englisch); andere Formulierungen oder Sprachen bleiben
unerkannt. Segmentbindung beruht auf der bestehenden Absatz-/Überschriften-
Grenzerkennung -- ein Markt-/Abschnittswechsel OHNE eine solche Grenze
(z.B. mitten im Fließtext ohne Absatzumbruch) bleibt nicht unterscheidbar.

    python scripts/test_isolated.py test_identity_zielgueltigkeit_dot_brand.py
"""
from app.technical_research import (
    _ziel, _identitaets_claims, _tier_identitaet, konsens,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def t(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


Z = _ziel("Lexus", "RC F", 2017, "5.0 V8", None)


def _tiere(claims, feld):
    return [c["tier"] for c in claims if c["feld"] == feld]


# ── A) Mit Preiszeile ───────────────────────────────────────────────────────
QUELLE_MIT_PREIS = t(
    "https://global.toyota/en/detail/4101939",
    "Lexus Launches All-new 'RC F' High-performance Coupe in Japan | Toyota Motor "
    "Corporation Official Global Website",
    "Tokyo, October 23, 2014-Lexus' all-new high-performance coupe, the \"RC F\", "
    "went on sale today in Japan. Engine Transmission Driveline Price RC F Carbon "
    "Exterior package 2UR-GSE (V8 5.0-liter) 8-Speed SPDS Rear-wheel drive "
    "10,300,000 yen 9,530,000 yen.")
claims_a = _identitaets_claims(QUELLE_MIT_PREIS, Z)
check("A1: engine_code wird auf Tier 3 gedeckelt (explizite JP-Markteinführung)",
      _tiere(claims_a, "engine_code") == [3], str(claims_a))
check("A2: drivetrain ebenso", _tiere(claims_a, "drivetrain") == [3], str(claims_a))
check("A3: engine_code bleibt dadurch UNGEKLAERT",
      konsens([c for c in claims_a if c["feld"] == "engine_code"], "engine_code") is None)

# ── B) Dieselbe Aussage OHNE Preiszeile: gleiche Behandlung ────────────────
QUELLE_OHNE_PREIS = t(
    "https://global.toyota/en/detail/4101939",
    "Lexus Launches All-new 'RC F' High-performance Coupe in Japan | Toyota Motor "
    "Corporation Official Global Website",
    "Tokyo, October 23, 2014-Lexus' all-new high-performance coupe, the \"RC F\", "
    "went on sale today in Japan. Engine Transmission Driveline RC F Carbon "
    "Exterior package 2UR-GSE (V8 5.0-liter) 8-Speed SPDS Rear-wheel drive.")
claims_b = _identitaets_claims(QUELLE_OHNE_PREIS, Z)
check("B1: engine_code bleibt auf Tier 3 gedeckelt, auch ohne Preiszeile "
      "(Satz, nicht Zahl, ist das Signal)",
      _tiere(claims_b, "engine_code") == [3], str(claims_b))
check("B2: drivetrain ebenso", _tiere(claims_b, "drivetrain") == [3], str(claims_b))

# ── C) Ausdrücklich zum Zielmarkt passender Text auf DERSELBEN Domain ──────
# SYNTHETISCH: kein realer Artikel, prueft nur die Mechanik.
QUELLE_DE_AUF_GLOBAL_TOYOTA = t(
    "https://global.toyota/en/detail/9999999",
    "SYNTHETISCHER TESTFALL Lexus RC F -- keine reale Veroeffentlichung",
    "Lexus RC F. Available in Germany and across the European Union with "
    "identical specifications worldwide. Engine Transmission Driveline "
    "2UR-GSE (V8 5.0-liter) 8-Speed SPDS Rear-wheel drive.")
claims_c = _identitaets_claims(QUELLE_DE_AUF_GLOBAL_TOYOTA, Z)
check("C1: engine_code auf DERSELBEN Domain, aber OHNE fremden Marktbezug im "
      "eigenen Text, bleibt Tier 1",
      _tiere(claims_c, "engine_code") == [1], str(claims_c))
check("C2: drivetrain ebenso", _tiere(claims_c, "drivetrain") == [1], str(claims_c))

# ── D) Bloße Orts-/Werksnennung ohne Verkaufsstart-Verb ────────────────────
# SYNTHETISCH.
QUELLE_WERKSNENNUNG = t(
    "https://global.toyota/en/detail/8888888",
    "SYNTHETISCHER TESTFALL Lexus RC F -- keine reale Veroeffentlichung",
    "Lexus RC F. Assembly Plant: Tahara Plant, Japan. Engine Transmission "
    "Driveline 2UR-GSE (V8 5.0-liter) 8-Speed SPDS Rear-wheel drive.")
claims_d = _identitaets_claims(QUELLE_WERKSNENNUNG, Z)
check("D1: bloße Ortsnennung (Produktionswerk, kein Verkaufsstart-Verb) löst "
      "KEINE Deckelung aus", _tiere(claims_d, "engine_code") == [1], str(claims_d))

# ── E) Getrennte Fahrzeug-/Marktabschnitte: keine gegenseitige Freigabe ────
# SYNTHETISCH.
QUELLE_GETRENNTE_ABSCHNITTE = t(
    "https://global.toyota/en/detail/7777777",
    "SYNTHETISCHER TESTFALL Lexus RC F -- keine reale Veroeffentlichung",
    "Lexus RC F went on sale today in Japan. Price 10,300,000 yen.\n\n"
    "Separate section. Lexus RC F for the German market: Engine "
    "Transmission Driveline 2UR-GSE (V8 5.0-liter) 8-Speed SPDS Rear-wheel "
    "drive.")
claims_e = _identitaets_claims(QUELLE_GETRENNTE_ABSCHNITTE, Z)
check("E1: der getrennte, spätere DE-Abschnitt bleibt unberührt vom "
      "JP-Abschnitt davor -- Tier 1 (Absatzgrenze wirkt)",
      _tiere(claims_e, "engine_code") == [1], str(claims_e))
check("E2: drivetrain ebenso", _tiere(claims_e, "drivetrain") == [1], str(claims_e))

# ── F) Bestehende deutsche Herstellerquelle bleibt bestätigt ───────────────
QUELLE_TOYOTA_MEDIA_DE = t(
    "https://www.toyota-media.de/blog/lexus-modelle/artikel/neues-adaptiv-variables-fahrwerk-fuer-lexus-gs-f-und-rc-f/text",
    "NEUES ADAPTIV VARIABLES FAHRWERK FÜR LEXUS F-MODELLE",
    "Köln, 1. Dezember 2016. Der Lexus GS F und der RC F werden beide von einem "
    "5,0 Liter V8-Saugmotor angetrieben, der 351 kW / 477 PS leistet und an eine "
    "Achtstufen-Sport-Automatik gekoppelt ist.")
claims_f = _identitaets_claims(QUELLE_TOYOTA_MEDIA_DE, Z)
check("F1: toyota-media.de liefert horsepower=477 unverändert auf Tier 1",
      any(c["feld"] == "horsepower" and c["wert"] == 477 and c["tier"] == 1
          for c in claims_f), str(claims_f))

# ── G) Herstellervertrauen bleibt erhalten ─────────────────────────────────
check("G1: global.toyota bleibt Tier-1-Herstellerdomain für 'Toyota'",
      _tier_identitaet("https://global.toyota/en/detail/4101939", "Toyota") == 1)
check("G2: global.toyota bleibt Tier-1-Herstellerdomain für die Submarke 'Lexus'",
      _tier_identitaet("https://global.toyota/en/detail/4101939", "Lexus") == 1)

# ── H) Mehrquellen-Gegenfall (Regressionsschutz, bereits anerkannt) ────────
QUELLE_H2_SYNTHETISCH = t(
    "https://press.toyota.jp/releases/synthetischer-testfall-rc-f",
    "SYNTHETISCHER TESTFALL Lexus RC F Press Release Japan -- keine reale "
    "Veroeffentlichung",
    "Lexus RC F launches in Japan. Engine Transmission Driveline 2UR-GSE "
    "(V8 5.0-liter) 8-Speed SPDS Rear-wheel drive 9,800,000 yen.")
claims_h = claims_a + _identitaets_claims(QUELLE_H2_SYNTHETISCH, Z)
check("H1: zwei unabhängige, je fremdmarktgebundene Quellen mit gleichem Wert "
      "bestätigen engine_code weiterhin NICHT gemeinsam",
      konsens([c for c in claims_h if c["feld"] == "engine_code" and not c.get("verworfen")],
              "engine_code") is None, str(claims_h))

print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE ZIELGUELTIGKEITS-TESTS GRUEN")
