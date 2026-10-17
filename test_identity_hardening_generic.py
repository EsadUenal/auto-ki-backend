"""
Identity-Hardening (generische Web-Fahrzeugidentität) — Folgeauftrag nach der
Lexus-RC-F-Forensik (Production-Fail: Marke/Modell blieb trotz Package A
unbestätigt).

BEFUND (siehe Forensik-Bericht): drei generische Lücken, KEIN Lexus-/RC-F-
Spezialfall.
  1. `_tokens()` trennt nur an Nicht-Alphanumerisch — "RC F"/"RC-F" ergeben
     {"rc","f"}, aber "RCF" (ohne jedes Trennzeichen) ein einziges Token
     "rcf". Dieselbe Quelle, derselbe Wagen, unterschiedliches Tokenergebnis.
  2. `_marke_eigene_domain()` kannte nur exakte Single-Label-Domains — eine
     Media-/Press-Subdomain oder eine Länder-TLD-Variante der ECHTEN
     Herstellerdomain fiel durch.
  3. `US_QUELLEN_AUSSCHLUSS` galt pauschal auch in der Identitätsphase, wo die
     Frage "existiert dieses Fahrzeug" ist, nicht "welche US-Spezifikation
     gilt" — benachteiligt Nischen-/Importmodelle mit US-schwerer Presse.

FIX (rein generisch, kein Marken-/Modell-Hardcode):
  1. `_modell_signaturen()`/`_modell_ausgerichtet()` — zwei kontrollierte,
     deterministische Signaturen pro Modellname (vollständig getrennt /
     vollständig zusammengeschrieben), KEIN Teilstring-Matching, KEINE
     Ähnlichkeitssuche.
  2. `_marke_eigene_domain()` nutzt jetzt die Public-Suffix-bewusste
     registrierbare Domain (tldextract, offline) statt naivem `split(".")[0]`
     — Subdomains sind frei, die TLD-Grenze ist korrekt, ein Zusatzwort IM
     Markenlabel selbst bleibt weiterhin ausgeschlossen.
  3. Zusätzlicher, strenger Primärquellenpfad (`_primaerquelle()`/
     `_primaerquelle_widerspruch()`) für GENAU EINE zweifelsfreie Tier-1-
     Quelle, NUR für Marke+Modell — der bestehende Zwei-Domain-Pfad bleibt
     unverändert der primäre, breitere Weg.
  4. `US_QUELLEN_AUSSCHLUSS` gilt nicht mehr für die Identitätsphase selbst
     (`_exclude_domains_fuer()`), bleibt aber für Rückruf-/Technik-Phasen UND
     als Tier-Deckel für DETAIL-Claims (`_ist_regionsfremd()` in
     `_identitaets_claims()`) unverändert bestehen.

    python test_identity_hardening_generic.py
"""
from app.technical_research import (
    US_QUELLEN_AUSSCHLUSS, _exclude_domains_fuer, _fein_zerlegt,
    _identitaet_belegt, _identitaets_claims, _ist_regionsfremd,
    _marke_eigene_domain, _modell_signaturen, _tier_identitaet,
    ausgerichtet, werte_identitaet_aus,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


print("=" * 78)
print("A) MODELL-SIGNATUREN -- reine Zerlegung")
print("=" * 78)

check("_fein_zerlegt('gr86') -> ['gr','86']", _fein_zerlegt("gr86") == ["gr", "86"])
check("_fein_zerlegt('rc') -> ['rc']", _fein_zerlegt("rc") == ["rc"])
check("_modell_signaturen('RC F') enthält getrennte UND zusammengeschriebene Signatur",
      set(_modell_signaturen("RC F")) == {frozenset({"rc", "f"}), frozenset({"rcf"})},
      str(_modell_signaturen("RC F")))
check("_modell_signaturen('GR86') == _modell_signaturen('GR 86') (symmetrisch, unabhängig von Eingabe-Schreibweise)",
      set(_modell_signaturen("GR86")) == set(_modell_signaturen("GR 86")))

print("\n" + "=" * 78)
print("B) SCHREIBVARIANTEN -- Treffer MUSS trotz anderer Schreibweise ausrichten")
print("=" * 78)

# (marke, ziel_modell, quell_titel) -- Marke wird MITGEGEBEN (nicht None), weil
# der reale Fremdmarken-Schutz sonst JEDE bekannte Marke im Titel als
# "fremde Marke" werten würde (das ist das bestehende, unveränderte Verhalten
# aus `ausgerichtet()` -- kein Test-Artefakt dieses Fixes).
POSITIVE_VARIANTEN = [
    ("Lexus", "RC F", "Lexus RCF im Test"),
    ("Lexus", "RC-F", "Lexus RC F Fahrbericht"),
    ("Mazda", "MX-5", "Mazda MX 5 Test"),
    ("Mazda", "MX 5", "Mazda MX5 Roadster"),
    ("Mazda", "MX5", "Mazda MX-5 Cabrio"),
    ("Mazda", "CX-5", "Mazda CX 5 SUV"),
    ("Mazda", "CX 5", "Mazda CX5 im Vergleich"),
    ("Mazda", "CX5", "Mazda CX-5 Test"),
    ("Toyota", "GR 86", "Toyota GR86 Coupe"),
    ("Toyota", "GR86", "Toyota GR 86 Fahrbericht"),
    ("VW", "ID.4", "VW ID4 SUV"),
    ("VW", "ID4", "VW ID.4 Test"),
    ("Toyota", "C-HR", "Toyota CHR Crossover"),
    ("Jaguar", "E-Pace", "Jaguar EPace SUV"),
    ("Jaguar", "F-Type", "Jaguar F Type Roadster"),
    ("Jaguar", "F Type", "Jaguar F-Type Cabrio"),
]
for marke, modell, titel in POSITIVE_VARIANTEN:
    t = treffer("https://example.de/artikel", titel, titel)
    ok, grund = ausgerichtet(t, marke, modell)
    check(f"Ziel-Modell '{modell}' matcht Quelle '{titel}'",
          ok and grund in (None, "schwach"), f"ok={ok} grund={grund}")

print("\n" + "=" * 78)
print("B2) ALPHABETISCHE SCHREIBVARIANTEN OHNE ZIFFER-ANKER -- jetzt SYMMETRISCH")
print("=" * 78)
# Vorher eine dokumentierte Grenze: ein rein alphabetisches, bereits
# ZUSAMMENGESCHRIEBENES Ziel-Modell ("RCF", "CHR", "EPace", "FType") hat
# keinen Ziffer-Anker, an dem `_fein_zerlegt()` es wieder in seine
# Wortbestandteile zerlegen koennte. Fix: `_quelle_konkatenationen()` nutzt
# die Trennstruktur der QUELLE selbst (sie hat an der Stelle "C-HR"/"C HR"
# bereits ein EIGENES Trennzeichen gesetzt) -- KEIN Raten am Zielstring,
# KEINE Woerterbuch-/Fuzzy-Logik, nur bereits getrennte, UNMITTELBAR
# benachbarte Quell-Tokens wieder zusammenfuegen.
ALPHA_SYMMETRISCH = [
    ("Lexus", "RCF", "Lexus RC-F Fahrbericht"),
    ("Lexus", "RCF", "Lexus RC F Fahrbericht"),
    ("Toyota", "CHR", "Toyota C-HR im Test"),
    ("Jaguar", "EPace", "Jaguar E-Pace Fahrbericht"),
    ("Jaguar", "FType", "Jaguar F-Type Roadster"),
    ("Jaguar", "FType", "Jaguar F Type Roadster"),
]
for marke, modell, titel in ALPHA_SYMMETRISCH:
    t = treffer("https://example.de/artikel", titel, titel)
    ok, grund = ausgerichtet(t, marke, modell)
    check(f"Ziel '{modell}' (zusammengeschrieben, ohne Ziffer-Anker) matcht Quelle '{titel}' "
          f"ueber die Trennstruktur DER QUELLE",
          ok and grund in (None, "schwach"), f"ok={ok} grund={grund}")

print("\n" + "=" * 78)
print("B3) KOLLISIONSSICHERHEIT der Quell-Konkatenation")
print("=" * 78)
# Kurze/generische Woerter in der Naehe duerfen NICHT zufaellig ein voellig
# anderes, unverwandtes Ziel-Modell treffen -- Konkatenation verlangt exakte
# Gleichheit der GESAMTEN Zeichenkette, kein Teilstring/Praefix.
t_b3 = treffer("https://example.de/artikel", "Toyota C HR Life im Alltagstest",
              "Toyota C HR Life im Alltagstest")
ok_b3, grund_b3 = ausgerichtet(t_b3, "Toyota", "CHRLife")
check("B3) 'CHRLife' (3 Woerter 'c'+'hr'+'life') matcht NICHT -- Fenster bewusst auf "
      "2 benachbarte Tokens begrenzt, kein Drei-Wort-Zusammenschluss",
      not ok_b3, f"ok={ok_b3} grund={grund_b3}")
ok_b3b, grund_b3b = ausgerichtet(t_b3, "Toyota", "HRLife")
check("B3b) 'HRLife' (Fenster 2, 'hr'+'life') matcht dagegen korrekt -- "
      "zeigt, dass die Konkatenation selbst korrekt arbeitet und B3 echt an der Fenstergrenze scheitert",
      ok_b3b, f"ok={ok_b3b} grund={grund_b3b}")

print("\n" + "=" * 78)
print("C) NEGATIVFAELLE -- aggressive Normalisierung darf NICHT uebergreifen")
print("=" * 78)

NEGATIVE_FAELLE = [
    ("RC F", "Lexus RC im Test", "Lexus RC im Test", "RC != RC F (kein 'F' irgendwo im Text)"),
    ("ID4", "VW ID3 Fahrbericht", "VW ID3 Fahrbericht", "ID3 != ID4"),
    ("A4", "Audi A45 im Test", "Audi A45 im Test", "A4 != A45"),
    ("A45", "Audi A4 im Test", "Audi A4 im Test", "A45 != A4 (Gegenrichtung)"),
    ("CX5", "Mazda CX50 SUV", "Mazda CX50 SUV", "CX5 != CX50"),
]
for modell, titel, inhalt, label in NEGATIVE_FAELLE:
    t = treffer("https://example.de/artikel", titel, inhalt)
    ok, grund = ausgerichtet(t, None, modell)
    check(label, not ok and grund == "modell_fehlt", f"ok={ok} grund={grund}")

print("\n" + "=" * 78)
print("D) MANUFACTURER-DOMAIN-ERKENNUNG (PSL-basiert, kein Markenhardcode im Code)")
print("=" * 78)

DOMAIN_FAELLE = [
    ("https://www.lexus.de/rc-f", "Lexus", True, "echte Root-Domain"),
    ("https://www.lexus.co.uk/rc-f", "Lexus", True, "echte Regional-/Laenderdomain (PSL co.uk)"),
    ("https://media.lexus.com/presse", "Lexus", True, "echte Media-/Press-Subdomain"),
    ("https://pressroom.lexus.eu/news", "Lexus", True, "echte Press-Subdomain, andere Laender-TLD"),
    ("https://lexus.fanblog.de/forum", "Lexus", False,
     "fremde Subdomain -- 'lexus' ist nur Subdomain eines FREMDEN Hosts (registrable domain = fanblog.de)"),
    ("https://www.lexus-forum.de/thread", "Lexus", False, "Fanforum (Zusatzwort IM Markenlabel)"),
    ("https://www.lexus-ersatzteile24.de/shop", "Lexus", False, "Zubehoershop"),
    ("https://www.autohaus-mueller-lexus.de/", "Lexus", False, "Haendlerseite"),
]
for url, marke, erwartet, label in DOMAIN_FAELLE:
    ergebnis = _marke_eigene_domain(url, marke)
    check(f"{label}: {url}", ergebnis == erwartet, f"erwartet={erwartet} ist={ergebnis}")

print("\n" + "=" * 78)
print("E) PRIMAERQUELLENPFAD -- genau 1 zweifelsfreie Tier-1-Quelle")
print("=" * 78)


def ziel_einfach(marke, modell, baujahr=None, motor=None, leistung_ps=None):
    return {"marke": marke, "modell": modell, "baujahr": baujahr, "motor": motor,
            "leistung_ps": leistung_ps}


treffer_e1 = [treffer("https://www.lexus.de/rc-f", "Lexus RC F",
                      "Der Lexus RC F ist das Performance-Coupe mit 5.0-Liter-V8 und 477 PS.")]
belegt_e1, _, domains_e1, pfad_e1 = _identitaet_belegt("RC F", "Lexus", treffer_e1)
check("E1) 1 Herstellerquelle, Modell im Titel -> belegt via Primaerquellenpfad",
      belegt_e1 and pfad_e1 == "primaerquelle" and domains_e1 == 1,
      f"belegt={belegt_e1} pfad={pfad_e1} domains={domains_e1}")

ident_e1, _ = werte_identitaet_aus(treffer_e1, ziel_einfach("Lexus", "RC F", 2017, "5.0 V8", 477))
check("E1) WebVehicleIdentity: Marke+Modell bestaetigt, identitaets_pfad korrekt gesetzt",
      ident_e1.belegt and ident_e1.marke == "Lexus" and ident_e1.modell == "RC F"
      and ident_e1.identitaets_pfad == "primaerquelle")

# E2) dieselbe Einzelquelle NUR im Fliesstext (Modell nicht im Titel) ->
# Primaerquellenpfad greift NICHT (Auftrag 2C: "nur Fliesstext reicht NICHT").
treffer_e2 = [treffer("https://www.lexus.de/magazin/sportwagen",
                      "Unsere schnellsten Modelle",
                      "Zu unseren Performance-Modellen zaehlt u.a. der RC F mit 477 PS.")]
belegt_e2, _, _, pfad_e2 = _identitaet_belegt("RC F", "Lexus", treffer_e2)
check("E2) Nur Fliesstext-Nennung (nicht im Titel) -> Primaerquellenpfad greift NICHT",
      not belegt_e2 and pfad_e2 is None, f"belegt={belegt_e2} pfad={pfad_e2}")

# E3) widersprechende Tier-1-Quelle im selben Treffer-Satz -> Veto (Auftrag 2D).
treffer_e3 = [
    treffer("https://www.lexus.de/rc-f", "Lexus RC F",
            "Der Lexus RC F ist das Performance-Coupe mit 5.0-Liter-V8."),
    treffer("https://www.bmw.de/m4", "BMW M4 Competition",
            "Der BMW M4 Competition ist unser schnellstes Coupe."),
]
belegt_e3, _, _, pfad_e3 = _identitaet_belegt("RC F", "Lexus", treffer_e3)
check("E3) widersprechende Tier-1-Fremdmarken-Quelle im selben Satz -> Primaerquellenpfad entwertet",
      not belegt_e3 and pfad_e3 is None, f"belegt={belegt_e3} pfad={pfad_e3}")

# E4) dieselbe widersprechende Quelle darf den BESTEHENDEN Zwei-Domain-Pfad
# NICHT berühren -- Veto ist exklusiv für den neuen Primärquellenpfad.
treffer_e4 = treffer_e3 + [
    treffer("https://www.auto-motor-und-sport.de/lexus-rc-f", "Lexus RC F im Test",
            "Der Lexus RC F hat 477 PS und beschleunigt in 4,5 Sekunden auf 100 km/h."),
]
belegt_e4, _, domains_e4, pfad_e4 = _identitaet_belegt("RC F", "Lexus", treffer_e4)
check("E4) Zwei-Domain-Pfad bleibt vom Primaerquellen-Veto unberuehrt",
      belegt_e4 and pfad_e4 == "zwei_domain" and domains_e4 == 2,
      f"belegt={belegt_e4} pfad={pfad_e4} domains={domains_e4}")

print("\n" + "=" * 78)
print("E5) PRIMAERQUELLENPFAD MUSS WIRKLICH STRENG SEIN -- nur amtlich ODER Herstellerdomain")
print("=" * 78)
# Folgeauftrag: `_primaerquelle()` darf NICHT ueber einen allgemeinen Score-
# Schwellenwert laufen, der auch ein starkes Fachmedium/Marktplatz/Technik-DB
# durchliesse -- nur `ist_amtliche_quelle()` ODER `_marke_eigene_domain()`.
# Jede Quelle hier ist fuer sich ALLEIN genommen stark ausgerichtet (Modell
# im Titel) -- der einzige Unterschied ist der QUELLTYP.
EINZELQUELLEN_TYPEN = [
    ("https://www.auto-motor-und-sport.de/lexus-rc-f", "Lexus RC F im Test",
     "starkes Fachmedium allein", False),
    ("https://www.mobile.de/lexus-rc-f-angebot", "Lexus RC F Angebot",
     "Marktplatz allein", False),
    ("https://www.auto-data.net/lexus-rc-f", "Lexus RC F Daten",
     "technische Datenbank (Tier-2-Liste) allein", False),
    ("https://www.kba.de/lexus-rc-f-rueckruf", "Lexus RC F Rueckruf",
     "amtliche Quelle allein", True),
    ("https://www.lexus.de/rc-f", "Lexus RC F",
     "echte Herstellerseite allein", True),
]
for url, titel, label, erwartet_belegt in EINZELQUELLEN_TYPEN:
    t = treffer(url, titel, f"{titel}. Der Lexus RC F ist das Performance-Coupe mit 477 PS.")
    belegt, _, domains, pfad = _identitaet_belegt("RC F", "Lexus", [t])
    if erwartet_belegt:
        check(f"E5) {label} -> DARF belegen (Primaerquellenpfad)",
              belegt and pfad == "primaerquelle" and domains == 1,
              f"belegt={belegt} pfad={pfad} domains={domains}")
    else:
        check(f"E5) {label} -> NICHT belegt (kein Primaerquellenpfad, nur 1 Domain)",
              not belegt and pfad is None,
              f"belegt={belegt} pfad={pfad} domains={domains}")

# E6) derselbe brand-agnostische Veto-Mechanismus (E3) funktioniert weiterhin
# unveraendert -- die Verschaerfung betrifft NUR, welche Quelle SELBST zur
# Primaerquelle werden darf, nicht die Konflikt-Erkennung gegenueber anderen
# Marken.
check("E6) Primaerquellen-Veto (E3) funktioniert nach der Verschaerfung weiterhin",
      not belegt_e3 and pfad_e3 is None)

print("\n" + "=" * 78)
print("F) ZWEI-DOMAIN-PFAD -- bestehendes Verhalten bleibt erhalten")
print("=" * 78)

treffer_f = [
    treffer("https://www.lexus.de/rc-f", "Lexus RC F",
            "Der Lexus RC F ist das Performance-Coupe von Lexus mit 5.0-Liter-V8."),
    treffer("https://www.auto-motor-und-sport.de/lexus-rc-f", "Lexus RC F im Test",
            "Der Lexus RC F hat 477 PS und beschleunigt in 4,5 Sekunden auf 100 km/h."),
]
belegt_f, _, domains_f, pfad_f = _identitaet_belegt("RC F", "Lexus", treffer_f)
check("F) 2 unabhaengige, qualifizierte Domains -> weiterhin belegt via Zwei-Domain-Pfad",
      belegt_f and pfad_f == "zwei_domain" and domains_f == 2,
      f"belegt={belegt_f} pfad={pfad_f} domains={domains_f}")

print("\n" + "=" * 78)
print("G) REGION -- internationale Quelle stuetzt Existenz, nicht automatisch Detailwerte")
print("=" * 78)

check("G1) _exclude_domains_fuer('identitaet', ...) -> KEIN Ausschluss mehr",
      _exclude_domains_fuer("identitaet", None) is None)
check("G2) _exclude_domains_fuer('rueckruf', ...) -> Ausschluss UNVERAENDERT",
      _exclude_domains_fuer("rueckruf", None) == US_QUELLEN_AUSSCHLUSS)
check("G3) _exclude_domains_fuer('technik', ...) -> Ausschluss UNVERAENDERT",
      _exclude_domains_fuer("technik", None) == US_QUELLEN_AUSSCHLUSS)
check("G4) include_domains hat weiterhin Vorrang (kein Ausschluss, egal welche Phase)",
      _exclude_domains_fuer("rueckruf", ["kba.de"]) is None)

check("G5) _ist_regionsfremd erkennt eine bestehende US_QUELLEN_AUSSCHLUSS-Domain",
      _ist_regionsfremd("https://www.motortrend.com/lexus-rc-f"))
check("G6) _ist_regionsfremd laesst eine unbeteiligte Domain unberuehrt",
      not _ist_regionsfremd("https://www.lexus.de/rc-f"))
# G6b) explizit: KEINE ".com = US"-Heuristik -- eine globale Herstellerseite,
# eine neutrale .com-Domain und eine internationale technische Seite sind
# NICHT pauschal regionsfremd, nur weil sie .com/.org sind. Regionsfremd ist
# ausschliesslich Mitgliedschaft in der bestehenden, kuratierten
# US_QUELLEN_AUSSCHLUSS-Liste (10 konkret benannte Fachportale).
for neutrale_domain, label in [
    ("https://www.lexus.com/press", "globale Herstellerseite (.com)"),
    ("https://www.bosch.com/stories", "neutrale .com-Domain ohne Regionsbezug"),
    ("https://en.wikipedia.org/wiki/Lexus_RC_F", "internationale technische/Nachschlage-Seite (.org)"),
]:
    check(f"G6b) {label} ist NICHT regionsfremd (keine TLD-Heuristik)",
          not _ist_regionsfremd(neutrale_domain))

# G7) Tier-Deckel wirkt auch auf eine ansonsten Tier-1-faehige Domain, wenn sie
# regionsfremd ist. "Motortrend" ist hier NUR eine künstliche Test-Marke, um
# den Deckelungs-Mechanismus isoliert zu pruefen (kein Produkt-Hardcode: der
# echte Produktionsfall ist eine unbekannte Marke auf einer US-Fachmedien-
# Domain, nicht umgekehrt).
ziel_g7 = {"marke": "Motortrend", "modell": "RC F", "baujahr": 2017, "motor": "5.0 V8"}
treffer_g7 = treffer("https://www.motortrend.com/lexus-rc-f", "Motortrend RC F",
                     "Der RC F hat 477 PS.")
tiers_g7 = {c["tier"] for c in _identitaets_claims(treffer_g7, ziel_g7)}
check("G7) regionsfremde, sonst Tier-1-faehige Domain wird fuer Detail-Claims auf Tier 3 gedeckelt",
      tiers_g7 == {3}, f"tiers={tiers_g7}")
check("G7-Gegenprobe) dieselbe Domain-Struktur OHNE Regionsflag bleibt Tier 1",
      _tier_identitaet("https://www.motortrend.com/lexus-rc-f", "Motortrend") == 1)

print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE IDENTITY-HARDENING-TESTS GRUEN")
