"""
ENFAL-Folgeauftrag ("strukturierte Hersteller-Identifikation als Evidenz
nutzbar machen", 2026-11): begrenzte Erweiterung, die eine bereits positiv
zugeordnete, aber selbst rollenlose Codekennung aus einem echten
Hersteller-Identifikationsdokument MIT einer unabhängig belegten Rolle für
EXAKT denselben Codestring verknüpft -- kein Nachweisweg B (keine
Schreibweisen-Äquivalenz UXC1↔XC1), keine Lexus-Sonderregel.

ZWEI REALE STRUKTURBELEGE (selbst heruntergeladen, pypdf-Text unverändert)
---------------------------------------------------------------------------
  lexus.de  "RCF_tcm-3150-1284184.pdf"      Kopf: "Lexus RC F\n(UXC1 (EU, M), ab 09/2014)"
  toyota.de "RLF_GT86_ZN_tcm-17-172166.pdf" Kopf: "Toyota GT86\n(ZN)\nA\nStand: 01/2013"

Beide Hersteller führen diese Dokumente unter ihrer EIGENEN Rubrik
(lexus.de: "RETTUNGSDATENBLÄTTER"/"RETTUNGSKARTEN FÜR EINSATZKRÄFTE",
toyota.de: "Rettungsdatenblätter für Rettungskräfte" -- beide Übersichtsseiten
real abgerufen: lexus.de/toyota.de .../fahrzeuginformationen/
rettungsdatenblaetter). Der zweite Beleg (GT86/"ZN") zeigt eine ähnliche
Struktur, aber OHNE Markt-/Datumsklammer und OHNE irgendeine unabhängige
Rollenquelle in unserem Fixture-Bestand -- er bestätigt bewusst NICHT
pauschal, dass jede so gefundene Kennung eine Generation ist (s. Check 3/4).

MECHANISMUS (`app/technical_research.py`)
-------------------------------------------
  `_herstelleridentifikationsdokument(url, marke)`  NEU. Reine Dokumenttyp-
      Erkennung: eigene Herstellerdomain (`_marke_eigene_domain()`, unverändert)
      UND eines der Rubrik-eigenen Wörter ("rettungsdatenblatt"/"rettungskarte"/
      "rettungsleitfaden"/"fahrzeuginformationen") im URL-Pfad UND ".pdf".
      KEINE Rollenaussage.
  `_identitaets_claims()`                           ERGÄNZT. Setzt das Flag
      `herstelleridentifikation` an jedem generation-Claim der exakten
      Modell-Ankerung (nicht am Präfix-Pfad -- kein realer Beleg dafür).
  `_generationen_pruefen()`                         ERGÄNZT. VOR dem bisherigen
      Verwerfen ("ohne_generationskontext") wird geprüft: trägt EXAKT derselbe
      Codestring, von einer ANDEREN Domain, einen EIGENSTÄNDIGEN (nicht selbst
      verknüpften) Rollenbeleg/eine Generationszählung? Wenn ja UND das
      Identifikationsflag gesetzt ist: `rollenbeleg_verknuepft=True`, der
      Claim bleibt erhalten (lokales `rollenbeleg` bleibt unverändert False --
      Transparenz). Sonst unverändert verworfen. Das Jahresfenster-Veto
      (`_identitaets_claims()`, VOR dieser Funktion) bleibt uneingeschränkt
      wirksam -- ein durchs eigene Zeitfenster ausgeschlossener Claim erreicht
      diese Prüfung nie.

Diese Datei prüft AUSSCHLIESSLICH über den echten Produktpfad
(`werte_identitaet_aus()`/`_identitaets_claims()`/`_generationen_pruefen()`
mit echten `{"url","title","content"}`-Treffern) -- keine manuell
konstruierten Claim-Dicts.

    python scripts/test_isolated.py test_rc_f_herstelleridentifikation_verknuepfung.py
"""
from app.technical_research import (
    _generationscodes, _herstelleridentifikationsdokument, _identitaets_claims,
    _generationen_pruefen, _ziel, werte_identitaet_aus,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# ── Reale Quellen ───────────────────────────────────────────────────────────
ADAC = treffer(
    "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/",
    "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25): Technische Daten | ADAC",
    "Marke Lexus Modell RC F Typ Ultimate Edition Automatik Baureihe RC (UXC1) "
    "(12/14 - 06/25) Herstellerinterne Baureihenbezeichnung UXC1 Motorcode 2UR-GSE "
    "Leistung maximal in PS 464 Antriebsart Hinterrad Getriebeart Automatikgetriebe")
CARWIKI = treffer(
    "https://carwiki.de/lexus-rc-hsn-tsn-schluesselnummer/",
    "Lexus RC HSN & TSN | Tabelle aller Schluesselnummern",
    "Uebersicht: Alle Schluesselnummern (HSN/TSN) der Lexus RC Baureihen. "
    "Hersteller: Lexus Fahrzeug: RC Baureihe: RC XC1 Karosserie: Coupe "
    "MODELL HSN SCHLUESSELNUMMER TSN SCHLUESSELNUMMER KRAFTSTOFF HUBRAUM LEISTUNG BAUJAHR "
    "Lexus RC F Carbon Automatik 5013 AJM Benzin 4969 cm3 477 PS 2014 bis 2025")
AMS = treffer(
    "https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
    "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
    "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Leistung 181 PS bis "
    "477 PS. LEXUS RC F TRACK EDITION IM SUPERTEST: Was kann der V8-Sauger auf der "
    "Rennstrecke? LEXUS LC 500 UND RC F: V8-Sportler auf Abschiedstour.")
LEXUS_PDF_URL = ("https://www.lexus.de/content/dam/lexus/nmsc/germany/lexus-besitzer/"
                  "fahrzeuginformationen/RCF_tcm-3150-1284184.pdf")
LEXUS_PDF = treffer(
    LEXUS_PDF_URL, "Lexus RC F Rettungskarte",
    "Lexus RC F\n(UXC1 (EU, M), ab 09/2014)\nStand: 10/2014\n"
    "Lexus RC F (UXC1 (EU, ab 09/2014)\n"
    "12V Spannungsversorgung trennen\nHinweise zum Transport\nZugang 12V Batterie\n"
    "Lexus RC F\n(UXC1 (EU, M), ab 09/2014)")
TOYOTA_GT86_URL = ("https://www.toyota.de/content/dam/toyota/nmsc/germany/downloads/"
                    "rettungsdatenbl%C3%A4tter/RLF_GT86_ZN_tcm-17-172166.pdf")
TOYOTA_GT86 = treffer(
    TOYOTA_GT86_URL, "Toyota GT86 Rettungskarte",
    "Toyota GT86\n(ZN)\nA\nStand: 01/2013\nToyota GT86 (ZN)\nLegende\nAirbag")

# ── 1) Dokumenterkennung (reine Dokumenttyp-Pruefung, keine Rollenaussage) ──
check("1a) Lexus-Rettungskarte (eigene Domain, 'fahrzeuginformationen', .pdf) "
      "wird als Herstelleridentifikationsdokument erkannt",
      _herstelleridentifikationsdokument(LEXUS_PDF_URL, "Lexus") is True)
check("1b) Toyota-Rettungsdatenblatt (eigene Domain, prozentkodiertes "
      "'rettungsdatenbl%C3%A4tter', .pdf) wird ebenso erkannt -- generischer "
      "Pfad, keine Markenliste",
      _herstelleridentifikationsdokument(TOYOTA_GT86_URL, "Toyota") is True)
check("1c) dieselbe Toyota-URL wird NICHT erkannt, wenn die angefragte Marke "
      "nicht zur Domain passt (fremde Marke bleibt Tier<1, keine Privilegierung)",
      _herstelleridentifikationsdokument(TOYOTA_GT86_URL, "Lexus") is False)

# ── 2) Reales Lexus-Dokument + belegte Rolle aus vorhandener Quelle (ADAC) ──
z = _ziel("Lexus", "RC F", 2017, "5.0 V8", None)
claims_lexus_adac = [c for r in [ADAC, LEXUS_PDF] for c in _identitaets_claims(r, z)]
_generationen_pruefen(claims_lexus_adac, z.get("auxiliary_code"))
gen_lexus_adac = [c for c in claims_lexus_adac if c["feld"] == "generation"]
lexus_claims = [c for c in gen_lexus_adac if c["domain"] == "lexus.de"]
adac_claims = [c for c in gen_lexus_adac if c["domain"] == "adac.de"]
check("2a) ADAC liefert eigenstaendig rollenbelegtes UXC1 (unveraendert, "
      "wie bisher)",
      len(adac_claims) == 1 and adac_claims[0]["rollenbeleg"] is True
      and not adac_claims[0].get("verworfen"),
      f"adac_claims={adac_claims!r}")
check("2b) Lexus-Originalbeleg bleibt lokal rollenlos (Transparenz: "
      "rollenbeleg=False), wird aber ueber den exakt gleichen Codestring "
      "'UXC1' aus der UNABHAENGIGEN ADAC-Quelle verknuepft "
      "(rollenbeleg_verknuepft=True) und NICHT mehr verworfen",
      all(c["rollenbeleg"] is False and c.get("rollenbeleg_verknuepft") is True
          and not c.get("verworfen") for c in lexus_claims) and len(lexus_claims) == 3,
      f"lexus_claims={lexus_claims!r}")

ident_lexus_adac, _ = werte_identitaet_aus([ADAC, LEXUS_PDF], z)
check("2c) Tatsaechlicher Produktpfad (werte_identitaet_aus, ADAC+Lexus-Original "
      "allein): Nachweisweg C bestaetigt 'UXC1' direkt -- jetzt mit Tier-1-"
      "Herstellerquelle UND Tier-2-Fachmedium, kein zweiter Code im Spiel",
      ident_lexus_adac.generation == "UXC1" and ident_lexus_adac.generation_cluster == ["UXC1"],
      f"generation={ident_lexus_adac.generation!r} cluster={ident_lexus_adac.generation_cluster!r}")

# ── 3) Zweiter realer Strukturbeleg (Toyota GT86) prueft die Dokumenterkennung
#      generisch -- OHNE jede unabhaengige Rollenquelle in diesem Fixture-Satz ─
ztoyota = _ziel("Toyota", "GT86", 2015, "2.0", None)
claims_gt86 = [c for c in _identitaets_claims(TOYOTA_GT86, ztoyota)]
_generationen_pruefen(claims_gt86, None)
gen_gt86 = [c for c in claims_gt86 if c["feld"] == "generation"]
check("3) GT86-Rettungsdatenblatt wird als Identifikationsdokument erkannt "
      "UND liefert den Code 'ZN', bleibt aber OHNE jede unabhaengige "
      "Rollenquelle verworfen ('ohne_generationskontext') -- die "
      "Dokumenttyp-Erkennung bestaetigt fuer sich allein KEINE Generation",
      len(gen_gt86) == 2
      and all(c["wert"] == "ZN" and c.get("herstelleridentifikation") is True
              and c.get("verworfen") == "ohne_generationskontext" for c in gen_gt86),
      f"gen_gt86={gen_gt86!r}")

ident_gt86, _ = werte_identitaet_aus([TOYOTA_GT86], ztoyota)
check("4) Strukturierte Kennung ohne ausreichenden Rollenbeleg: GT86 allein "
      "ergibt KEINE erfundene Generation",
      ident_gt86.generation is None, f"generation={ident_gt86.generation!r}")

# ── 5) Motor-/Dokumentcode und Marketingname: keine Generationsbestaetigung ─
check("5a) ein Motorcode ('2UR-GSE', ziffernbeginnend) wird von "
      "_generationscodes() strukturell NIE als Kandidat erfasst -- betrifft "
      "nicht nur diese Erweiterung, bleibt unveraendert",
      _generationscodes({"title": "", "content": "Lexus RC F\n(2UR-GSE, ab 09/2014)"},
                        "RC F") == [])

LEXUS_MARKETING = treffer(
    "https://www.lexus.de/content/dam/lexus/nmsc/germany/lexus-besitzer/"
    "fahrzeuginformationen/RCF_marketing_tcm-1-2.pdf",
    "Lexus RC F Rettungskarte",
    "Lexus RC F\n(GTS (EU, M), ab 09/2014)")
ident_marketing, _ = werte_identitaet_aus([LEXUS_MARKETING], z)
check("5b) SYNTHETISCH: ein Ausstattungs-/Marketingname ('GTS') direkt im "
      "Identifikationsdokument, aber OHNE jede unabhaengige Rollenquelle fuer "
      "exakt 'GTS' -> keine Generationsbestaetigung (dieselbe Schutzbedingung "
      "wie bei GT86, nur mit einem Code, der real eine Ausstattungslinie waere)",
      ident_marketing.generation is None, f"generation={ident_marketing.generation!r}")

# ── 6) Fremdes Modell, unpassendes Jahr, mehrdeutiger Dokumentkopf ─────────
z_fremdmodell = _ziel("Lexus", "IS", 2017, "2.0", None)
ident_fremdmodell, _ = werte_identitaet_aus([ADAC, LEXUS_PDF], z_fremdmodell)
check("6a) fremdes Modell (Ziel 'IS', Quellen nennen nur 'RC F') -> Identitaet "
      "nicht einmal belegt, keine Generation",
      ident_fremdmodell.belegt is False and ident_fremdmodell.generation is None,
      f"belegt={ident_fremdmodell.belegt!r} generation={ident_fremdmodell.generation!r}")

z_jahr = _ziel("Lexus", "RC F", 2012, "5.0 V8", None)
claims_jahr = [c for r in [ADAC, LEXUS_PDF] for c in _identitaets_claims(r, z_jahr)]
_generationen_pruefen(claims_jahr, None)
lexus_claims_jahr = [c for c in claims_jahr if c["feld"] == "generation" and c["domain"] == "lexus.de"]
ident_jahr, _ = werte_identitaet_aus([ADAC, LEXUS_PDF], z_jahr)
check("6b) unpassendes Zieljahr (2012, vor 'ab 09/2014'): das eigene "
      "Zeitfenster-Veto verwirft den Lexus-Claim bereits VOR der "
      "Verknuepfungspruefung ('baujahr_ausserhalb', nicht 'rollenbeleg_verknuepft') "
      "-- die Verknuepfung kann das eigene Zeitfenster nie uebergehen",
      all(c.get("verworfen") == "baujahr_ausserhalb" and not c.get("rollenbeleg_verknuepft")
          for c in lexus_claims_jahr) and ident_jahr.generation is None,
      f"lexus_claims_jahr={lexus_claims_jahr!r} generation={ident_jahr.generation!r}")

LEXUS_AMBIG = treffer(
    "https://www.lexus.de/content/dam/lexus/nmsc/germany/lexus-besitzer/"
    "fahrzeuginformationen/RCF_ambig_tcm-1-1.pdf",
    "Lexus RC F Rettungskarte (Variantenuebersicht)",
    "Lexus RC F\n(AAB1 (EU, M), ab 09/2014)\nSeite 2\nLexus RC F\n(AAC1 (EU, M), ab 09/2014)")
ROLLE_AAB1 = treffer(
    "https://www.adac.de/x-aab1",
    "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25): Technische Daten | ADAC",
    "Marke Lexus Modell RC F Typ Ultimate Edition Automatik Baureihe RC (AAB1) "
    "(12/14 - 06/25) Herstellerinterne Baureihenbezeichnung AAB1 Motorcode 2UR-GSE")
ROLLE_AAC1 = treffer(
    "https://www.auto-motor-und-sport.de/x-aac1",
    "Lexus RC (1. Generation) (AAC1) im Ueberblick (ab 2015)",
    "Lexus RC (1. Generation) (AAC1) im Ueberblick (ab 2015). Der Lexus RC F leistet 477 PS.")
ident_ambig, _ = werte_identitaet_aus([LEXUS_AMBIG, ROLLE_AAB1, ROLLE_AAC1], z)
check("6c) SYNTHETISCH: mehrdeutiger Dokumentkopf -- EIN Identifikationsdokument "
      "nennt ZWEI Codes (AAB1/AAC1), BEIDE werden ueber je eine unabhaengige "
      "Rollenquelle verknuepft -> bestehende konsens()-Konfliktregel (unveraendert) "
      "erkennt den Widerspruch vergleichbar starker Gruppen weiterhin selbst, "
      "waehlt KEINEN blind aus",
      ident_ambig.generation is None and ident_ambig.generation_cluster == [],
      f"generation={ident_ambig.generation!r} cluster={ident_ambig.generation_cluster!r}")

# ── 7) Bestehende GT3-/Rollenbeleg-Haertung bleibt auch mit dem neuen Pfad
#      unberuehrt (kontext/Wortnachbarschaft allein rettet weiterhin nichts) ─
PORSCHE_PDF = treffer(
    "https://www.porsche.de/content/dam/porsche/germany/besitzer/"
    "fahrzeuginformationen/911_tcm-1-1.pdf",
    "Porsche 911 Rettungskarte",
    "Der Porsche 911 GT3, diese Generation, wird seit 2021 gebaut.")
FACHMEDIUM_GT3 = treffer(
    "https://www.auto-motor-und-sport.de/porsche-911-gt3", "Porsche 911 GT3 Test",
    "Der Porsche 911 GT3, diese Generation, beeindruckt auf der Rennstrecke.")
z_porsche = _ziel("Porsche", "911", 2022, "4.0", None)
ident_gt3, _ = werte_identitaet_aus([PORSCHE_PDF, FACHMEDIUM_GT3], z_porsche)
check("7) GT3-Haertung bleibt unberuehrt: eine zweite, unabhaengige Quelle "
      "nennt 'GT3' nur mit vagem Kontextwort ('diese Generation'), KEIN "
      "echter Rollenbeleg -- reicht nicht als Verknuepfungsgrundlage, "
      "generation bleibt None",
      ident_gt3.generation is None, f"generation={ident_gt3.generation!r}")

# ── 8) Vollstaendiger RC-F-Replay mit ALLEN bisherigen Quellen ─────────────
ident_voll, _ = werte_identitaet_aus([ADAC, CARWIKI, AMS, LEXUS_PDF], z)
check("8) ADAC+carwiki.de+AMS+Lexus-Original zusammen: generation bleibt "
      "None -- jetzt aber als ECHTER, transparenter Konflikt zwischen zwei "
      "belastbar belegten Gruppen (UXC1: lexus.de Tier1 + adac.de Tier2 = "
      "Gewicht 5; XC1: carwiki.de Tier3 + auto-motor-und-sport.de Tier2 = "
      "Gewicht 3) statt eines zuvor stillschweigend verworfenen Hersteller-"
      "belegs -- keine Drittquelle entfernt, keine Konfliktregel geaendert",
      ident_voll.generation is None and ident_voll.generation_cluster == [],
      f"generation={ident_voll.generation!r} cluster={ident_voll.generation_cluster!r}")

print()
if _FEHLER:
    print(f"FEHLER: {len(_FEHLER)} Check(s) fehlgeschlagen: {_FEHLER}")
    raise SystemExit(1)
print("ALLE HERSTELLERIDENTIFIKATION-VERKNUEPFUNG-TESTS GRUEN")
