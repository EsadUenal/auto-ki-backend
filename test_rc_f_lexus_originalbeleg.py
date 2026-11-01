"""
ENFAL-Folgeauftrag ("neu gefundenen Lexus-Originalbeleg pruefen und
verarbeiten", 2026-11): verarbeitet die echte Lexus-Herstellerquelle

    https://www.lexus.de/content/dam/lexus/nmsc/germany/lexus-besitzer/
    fahrzeuginformationen/RCF_tcm-3150-1284184.pdf

(zweiseitige Rettungskarte, Stand 10/2014) als nachvollziehbare Fixture
durch den bestehenden, unveraenderten Nachweisweg C (`konsens()`).

HERKUNFT DES FIXTURE-TEXTS
---------------------------
Das Original-PDF wurde selbst heruntergeladen (nicht nur visuell geprueft)
und mit `pypdf` textextrahiert. Der untenstehende Text ist die UNVERAENDERTE
Extraktion beider Seiten (Encoding-Artefakte der Schriftart wie "�" statt
Umlaut sind in der echten Extraktion enthalten und hier bewusst belassen,
sie beeinflussen die hier relevanten ASCII-Codes/Zahlen nicht).

FACHLICHE EINORDNUNG (Auftrag Punkt 3)
---------------------------------------
"UXC1 (EU, M), ab 09/2014" ist die interne Toyota/Lexus-Typkennung fuer die
EU-Ausfuehrung der Baureihe, direkt hinter dem Modellnamen "Lexus RC F"
genannt -- in einer echten Herstellerprimaerquelle (Rettungskarte fuer
Einsatzkraefte), nicht in einer Fachmedien-Sekundaerquelle. Das belegt:
Lexus selbst fuehrt "RC F" unter der Typkennung "UXC1" fuer den EU-Markt,
gueltig ab 09/2014 (Dokumentstand 10/2014).

Das belegt AUSDRUECKLICH NICHT:
  * dass "UXC1" dieselbe Generation wie die von carwiki.de/AMS verwendete
    Schreibweise "XC1" bezeichnet -- das Dokument nennt "XC1" an keiner
    Stelle und trifft keine Aussage zu dessen Verhaeltnis zu "UXC1"
    (Codeaequivalenz = Nachweisweg B, bleibt entfernt, s.
    test_generation_drei_nachweiswege.py);
  * was "M" in der Klammer "(EU, M)" bedeutet -- keine Rolle wird hier aus
    der Klammerform selbst erraten (Auftrag Punkt 3, letzter Satz).

ERGEBNIS DER VERARBEITUNG (Auftrag Punkt 4)
--------------------------------------------
Der reale Dokumenttext traegt zwar ein klares, von `zeitraum()` korrekt
erkanntes Zeitfenster ("ab 09/2014" -> (2014, None)), aber KEIN Rollenwort
aus `_ROLLE_BEZEICHNUNG` ("Baureihe", "Chassis-Code", "Werkscode",
"Codename", "Modellcode", "interne Bezeichnung") und KEIN Generations-
Kontextwort aus `_GEN_KONTEXT" in der Umgebung des Codes. `_generationscodes()`
liefert deshalb rollenbeleg=False UND kontext=False UND nummer=None fuer
ALLE DREI Fundstellen -- der Claim wird von `_generationen_pruefen()` als
"ohne_generationskontext" verworfen, BEVOR er `konsens()` ueberhaupt
erreicht (s. Check 1 unten, direkter Aufruf von `_generationscodes()`).

Das ist eine bewusste, bestehende Design-Entscheidung (s. Kommentarbloecke
vor `_generationscodes()`/`_rollenbeleg_feldlabel()` in
app/technical_research.py): eine blosse Klammer nach dem Modellnamen zaehlt
NICHT als Rollenbeleg, nur ein explizites Rollenwort tut das -- exakt die
Grenze, die Auftrag Punkt 3 ("keine Rolle allein aus einer beliebigen
Klammer oder Codeform erraten") fuer diese Pruefung selbst vorgibt. Ein
generischer Extraktionsfix (z.B. "Modell\\n(CODE (Markt, Kuerzel), ab
Datum)" als eigenstaendiges Rollenmuster erkennen) wurde ERWOGEN, aber NICHT
umgesetzt: er stuetzt sich auf genau EIN reales Dokument dieser Form -- zu
schmal, um "belastbar" im Sinne des Auftrags zu sein, und strukturell
dieselbe Art Annahme, vor der Punkt 3 ausdruecklich warnt. Das bleibt eine
offene Policy-Frage fuer den Nutzer (s. Abschlussbericht), keine in dieser
Runde automatisch getroffene Entscheidung.

Checks 1 zeigt: der Lexus-Claim erreicht `konsens()` ALLEIN schlicht nie,
weil er ohne unabhaengige Rollenstuetze bereits vorher verworfen wird.

NACHTRAG (ENFAL-Folgeauftrag "belegte Herstellerkennung gegenueber
abweichenden Drittbezeichnungen einordnen", 2026-11): Check 2 (voller
RC-F-Quellensatz PLUS Original-Dokument) wurde HIER urspruenglich als
"Ergebnis bleibt unveraendert, generation=None" dokumentiert. Das war zum
damaligen Stand korrekt -- eine spaetere, separat autorisierte Runde hat
_generation_herstelleridentifikation_vorrang() ergaenzt (s.
app/technical_research.py, test_rc_f_herstelleridentifikation_konfliktvorrang.py
fuer die volle Regression dieser Erweiterung). Seither LOEST der volle
Quellensatz zugunsten von "UXC1" auf (Nachweisweg "herstelleridentifikation"),
weil UXC1 sowohl durch die Lexus-Herstelleridentifikation ALS AUCH durch
ADACs unabhaengigen Baureihenrollenbeleg gestuetzt wird und keine
konkurrierende Herstellerquelle fuer "XC1" existiert. Check 2 ist unten
entsprechend aktualisiert -- keine Codegleichsetzung UXC1/XC1, "XC1" bleibt
als abweichende Drittbezeichnung mit Herkunft sichtbar
(`generation_abweichende_kennungen`), nie in `generation_cluster`.

Check 4 zeigt den tatsaechlichen Empfehlungsausgang (Auftrag Punkt 5, nicht
nur die Identitaets-Struktur) fuer den hier konstruierten `_Identity`-Stub:
OHNE eine passende Bestaetigung in `field_evidence["generation"]` bleibt das
bestehende, unveraenderte `app/empfehlungs_policy.py`-Gate (Zeile ~107)
unabhaengig vom blossen `identity.generation`-Wert blockiert -- ein Wert
allein, ohne Evidenzstatus, reicht diesem Gate nie. Der volle End-to-End-
Effekt der neuen Konfliktaufloesung auf die Empfehlung (inkl. korrekt
gesetztem `field_evidence`) wird separat in
test_rc_f_herstelleridentifikation_konfliktvorrang.py gezeigt.

    python scripts/test_isolated.py test_rc_f_lexus_originalbeleg.py
"""
from app.empfehlungs_policy import STATE_LIMITED, entscheide
from app.technical_research import _generationscodes, _ziel, werte_identitaet_aus

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# ── Echte Lexus-Originalquelle (woertliche pypdf-Extraktion, beide Seiten) ──
LEXUS_PDF_SEITE1 = (
    "Lexus RC F\n(UXC1 (EU, M), ab 09/2014)\nStand: 10/2014\n"
    "Lexus RC F (UXC1 (EU, ab 09/2014)")
LEXUS_PDF_SEITE2 = (
    "12V Spannungsversorgung trennen\nHinweise zum Transport\nZugang 12V Batterie\n"
    "Lexus RC F\n(UXC1 (EU, M), ab 09/2014)")
LEXUS_PDF = treffer(
    "https://www.lexus.de/content/dam/lexus/nmsc/germany/lexus-besitzer/"
    "fahrzeuginformationen/RCF_tcm-3150-1284184.pdf",
    "Lexus RC F Rettungskarte",
    LEXUS_PDF_SEITE1 + "\n" + LEXUS_PDF_SEITE2)

# ── Realer RC-F-Quellensatz (unveraendert zitiert, s.
# test_generation_drei_nachweiswege.py / test_identity_familie_zugehoerigkeit.py) ─
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

# ── 1) Originaldokument allein: tatsaechliche Claims und Nachweisstatus ────
claims_lexus = _generationscodes(LEXUS_PDF, "RC F")
check("1a) Original-PDF liefert genau die 3 erwarteten UXC1-Fundstellen "
      "(Seite 1 nennt den Kopf zweimal -- Haupttext + wiederholte Fusszeile "
      "ohne schliessende Klammer --, Seite 2 einmal)",
      len(claims_lexus) == 3 and all(c["code"] == "UXC1" for c in claims_lexus),
      f"claims={claims_lexus!r}")
check("1b) Zeitfenster wird korrekt aus 'ab 09/2014' erkannt -- (2014, None)",
      all(c["zeitraum"] == (2014, None) for c in claims_lexus),
      f"zeitraum={[c['zeitraum'] for c in claims_lexus]!r}")
check("1c) KEIN Rollenbeleg (kein 'Baureihe'/'Chassis-Code'/... in der "
      "Umgebung) UND KEIN Generations-Kontextwort -- der Claim wird deshalb "
      "von _generationen_pruefen() als 'ohne_generationskontext' verworfen, "
      "BEVOR er konsens() erreicht",
      all(c["rollenbeleg"] is False and c["kontext"] is False and c["nummer"] is None
          for c in claims_lexus),
      f"rollenbeleg/kontext/nummer={[(c['rollenbeleg'], c['kontext'], c['nummer']) for c in claims_lexus]!r}")

z = _ziel("Lexus", "RC F", 2017, "5.0 V8", None)
ident_lexus_allein, _ = werte_identitaet_aus([LEXUS_PDF], z)
check("1d) Originaldokument ALLEIN (ohne weitere Quelle) ergibt generation=None "
      "-- der eigene Claim ist rollenlos UND es gibt keine zweite Quelle fuer Konsens",
      ident_lexus_allein.generation is None
      and ident_lexus_allein.generation_cluster == []
      and ident_lexus_allein.generation_nachweisweg is None,
      f"generation={ident_lexus_allein.generation!r} "
      f"cluster={ident_lexus_allein.generation_cluster!r}")

# ── 2) Voller realer RC-F-Quellensatz PLUS Originaldokument ────────────────
# NACHTRAG (s. Moduldocstring): seit der Konfliktaufloesungs-Erweiterung
# loest dieser Quellensatz zugunsten von "UXC1" auf -- "XC1" bleibt als
# abweichende Drittbezeichnung mit Herkunft sichtbar, nie im Cluster.
ident_mit_lexus, _ = werte_identitaet_aus([ADAC, CARWIKI, AMS, LEXUS_PDF], z)
check("2) ADAC+carwiki.de+AMS+Lexus-Original zusammen: loest zugunsten der "
      "belegten Herstellerkennung 'UXC1' auf (Nachweisweg "
      "'herstelleridentifikation'), cluster bleibt auf ['UXC1'] begrenzt, "
      "'XC1' bleibt als abweichende Drittbezeichnung mit Herkunft sichtbar",
      ident_mit_lexus.generation == "UXC1"
      and ident_mit_lexus.generation_cluster == ["UXC1"]
      and ident_mit_lexus.generation_nachweisweg == "herstelleridentifikation"
      and ident_mit_lexus.generation_abweichende_kennungen == [
          {"code": "XC1", "domains": ["auto-motor-und-sport.de", "carwiki.de"]}],
      f"generation={ident_mit_lexus.generation!r} cluster={ident_mit_lexus.generation_cluster!r} "
      f"nachweisweg={ident_mit_lexus.generation_nachweisweg!r} "
      f"abweichend={ident_mit_lexus.generation_abweichende_kennungen!r}")

# ── 3) Keine Parseraenderung in dieser Runde -> keine Positiv-/Negativkontrollen
#      noetig (Auftrag Punkt 5: nur "bei Parseraenderung"). Check 1c oben
#      dokumentiert den unveraenderten, bewusst erhaltenen Gate-Zustand.

# ── 4) Tatsaechlicher Empfehlungsausgang (Auftrag Punkt 5, nicht nur die
#      Identitaets-Struktur) -- bestehender, unveraenderter Gate in
#      app/empfehlungs_policy.py, Konstruktion wie in
#      test_rc_f_schwachstelle_wartung_reparaturpaket.py ──


class _Identity:
    def __init__(self, **kw):
        self.make, self.model = "Lexus", "RC F"
        self.generation = None
        self.engine_name = None
        self.engine_code = None
        self.horsepower = None
        self.field_evidence: dict = {}
        for k, v in kw.items():
            setattr(self, k, v)


def _fe(state: str, confirmed_by: tuple[str, ...] = ()) -> dict:
    return {"verification_state": state, "confirmed_by": list(confirmed_by)}


ident_fuer_empfehlung = _Identity(
    generation=ident_mit_lexus.generation,  # = 'UXC1' seit dem Nachtrag, s. Check 2
    field_evidence={"make": _fe("user_confirmed"), "model": _fe("user_confirmed")})
entscheidung = entscheide("kaufen_nach_besichtigung", ident_fuer_empfehlung)
check("4) ein blosser generation-WERT ohne passende field_evidence["
      "'generation']-Bestaetigung reicht dem Gate nie (unveraendert) -- "
      "senkt weiterhin auf STATE_LIMITED, nimmt die milde Empfehlung zurueck "
      "('unbekannt'); 'Generation' bleibt die blockierende Luecke, OBWOHL "
      "identity.generation='UXC1' -- Wert und Evidenzstatus sind zwei "
      "verschiedene Dinge (s. Moduldocstring-Nachtrag fuer den vollen "
      "End-to-End-Effekt mit korrekt gesetztem field_evidence)",
      entscheidung.state == STATE_LIMITED and entscheidung.empfehlung == "unbekannt"
      and "Generation" in entscheidung.identitaet.fehlend,
      f"state={entscheidung.state!r} empfehlung={entscheidung.empfehlung!r} "
      f"fehlend={entscheidung.identitaet.fehlend!r}")

print()
if _FEHLER:
    print(f"FEHLER: {len(_FEHLER)} Check(s) fehlgeschlagen: {_FEHLER}")
    raise SystemExit(1)
print("ALLE LEXUS-ORIGINALBELEG-TESTS GRUEN")
