"""
ENFAL-Folgeauftrag ("belegte Herstellerkennung gegenueber abweichenden
Drittbezeichnungen einordnen", 2026-11): eng auf das Feld `generation`
begrenzte Konfliktaufloesung, wenn `konsens()` wegen zwei exakt
verschiedenen, vergleichbar starken Codegruppen `None` liefert.

AUSLOESER (real, voriger Stand)
--------------------------------
Der volle RC-F-Quellensatz (ADAC+carwiki.de+AMS+Lexus-Original) blieb nach
der vorigen Runde bei `generation=None`: "UXC1" (lexus.de Tier1 + adac.de
Tier2 = Gewicht 5) und "XC1" (carwiki.de Tier3 + auto-motor-und-sport.de
Tier2 = Gewicht 3) sind fuer `konsens()`s eigene, UNVERAENDERTE
Konfliktschwelle (3*2>=5) vergleichbar stark.

MECHANISMUS (`app/technical_research.py::_generation_herstelleridentifikation_vorrang()`)
-------------------------------------------------------------------------------------------
NUR eingesetzt, wenn `konsens()` fuer "generation" bereits `None` liefert.
Loest NUR dann zugunsten eines Codes auf, wenn:
  1. GENAU EIN Codestring durch eine Tier-1-Herstellerquelle (eigene Domain)
     MIT eigenstaendigem Rollenbeleg/Generationszaehlung gestuetzt wird
     (lokal ODER ueber `rollenbeleg_verknuepft` aus der vorigen Runde);
  2. dieser Code auch FUER SICH ALLEIN (ohne die konkurrierende Gruppe)
     `konsens()`s eigene, unveraenderte Schwelle erreichen wuerde (Wieder-
     verwendung von `konsens()` auf die vorgefilterte Teilmenge, keine
     eigene Gewichtslogik, keine pauschale Tier-1-Bevorzugung);
  3. KEINE zweite Codegruppe ebenfalls eine solche Tier-1-Stuetze hat.
Das Zeitfenster-Veto (VOR dieser Funktion, unveraendert) bleibt vollstaendig
wirksam. Die unterlegene(n) Gruppe(n) werden NIE nach `generation_cluster`
uebernommen -- nur transparent, mit Domain-Herkunft, als
`generation_abweichende_kennungen` zurueckgegeben (NEUES Feld,
`WebVehicleIdentity`).

`konsens()` SELBST bleibt fuer ALLE Felder (auch generation, im
Normalfall) unveraendert -- diese Funktion ist ein eng begrenzter FALLBACK,
kein Ersatz.

    python scripts/test_isolated.py test_rc_f_herstelleridentifikation_konfliktvorrang.py
"""
from app.empfehlungs_policy import STATE_LIMITED, STATE_NORMAL, entscheide
from app.technical_research import (
    _fremde_generation, _generationen_pruefen, _identitaets_claims, _ziel, werte_identitaet_aus,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# ── Reale RC-F-Quellen (unveraendert zitiert, s. vorige Runden) ────────────
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
LEXUS_PDF = treffer(
    "https://www.lexus.de/content/dam/lexus/nmsc/germany/lexus-besitzer/"
    "fahrzeuginformationen/RCF_tcm-3150-1284184.pdf",
    "Lexus RC F Rettungskarte",
    "Lexus RC F\n(UXC1 (EU, M), ab 09/2014)\nStand: 10/2014\n"
    "Lexus RC F (UXC1 (EU, ab 09/2014)")
z = _ziel("Lexus", "RC F", 2017, "5.0 V8", None)

# ── 1) Passende belegte Herstellerkennung versus abweichende Drittbezeichnung ─
ident1, _ = werte_identitaet_aus([ADAC, CARWIKI, AMS, LEXUS_PDF], z)
check("1) UXC1 (Lexus-Identifikation + unabhaengiger ADAC-Rollenbeleg) setzt "
      "sich gegen die abweichende Drittbezeichnung XC1 (carwiki.de/AMS) durch",
      ident1.generation == "UXC1" and ident1.generation_cluster == ["UXC1"]
      and ident1.generation_nachweisweg == "herstelleridentifikation",
      f"generation={ident1.generation!r} cluster={ident1.generation_cluster!r} "
      f"nachweisweg={ident1.generation_nachweisweg!r}")

# ── 2) Herstellerunterlage fuer falsches Modell/Jahr/Gebiet: kein Vorrang ──
z_fremdmodell = _ziel("Lexus", "IS", 2017, "2.0", None)
ident2a, _ = werte_identitaet_aus([ADAC, CARWIKI, AMS, LEXUS_PDF], z_fremdmodell)
check("2a) falsches Modell (Ziel 'IS', Quellen nennen nur 'RC F') -> kein "
      "Vorrang, Identitaet nicht einmal belegt",
      ident2a.belegt is False and ident2a.generation is None,
      f"belegt={ident2a.belegt!r} generation={ident2a.generation!r}")

z_jahr = _ziel("Lexus", "RC F", 2012, "5.0 V8", None)
ident2b, _ = werte_identitaet_aus([ADAC, CARWIKI, AMS, LEXUS_PDF], z_jahr)
check("2b) falsches Zieljahr (2012, vor 'ab 09/2014') -> kein Vorrang, "
      "UXC1 bleibt durchs eigene Zeitfenster-Veto ausgeschlossen (greift vor "
      "jeder Konfliktaufloesung), generation bleibt None",
      ident2b.generation is None and ident2b.generation_cluster == [],
      f"generation={ident2b.generation!r} cluster={ident2b.generation_cluster!r}")

TOYOTA_GT86_URL = ("https://www.toyota.de/content/dam/toyota/nmsc/germany/downloads/"
                    "rettungsdatenbl%C3%A4tter/RLF_GT86_ZN_tcm-17-172166.pdf")
TOYOTA_GT86 = treffer(
    TOYOTA_GT86_URL, "Toyota GT86 Rettungskarte",
    "Toyota GT86\n(ZN)\nA\nStand: 01/2013\nToyota GT86 (ZN)\nLegende\nAirbag")
z_gebiet = _ziel("Toyota", "GT86", 2015, "2.0", None)
ident2c, _ = werte_identitaet_aus([TOYOTA_GT86], z_gebiet)
check("2c) Herstellerunterlage ohne jede unabhaengige Rollenquelle (GT86 "
      "allein) -> kein Vorrang moeglich, Code bleibt unbestaetigt",
      ident2c.generation is None, f"generation={ident2c.generation!r}")

# ── 3) Herstellerkennung ohne belegte Generationsrolle: keine Bestaetigung ─
ROLLENLOS = treffer(
    "https://www.lexus.de/content/dam/lexus/nmsc/germany/lexus-besitzer/"
    "fahrzeuginformationen/RCF_rollenlos_tcm-1-3.pdf",
    "Lexus RC F Rettungskarte",
    "Lexus RC F\n(UXC1 (EU, M), ab 09/2014)")
FACHMEDIUM_OHNE_ROLLE = treffer(
    "https://www.auto-motor-und-sport.de/lexus-rc-f-test",
    "Lexus RC F im Test", "Der Lexus RC F UXC1 beeindruckt im Test mit 477 PS.")
ident3, _ = werte_identitaet_aus([ROLLENLOS, FACHMEDIUM_OHNE_ROLLE], z)
check("3) Herstelleridentifikation allein, UNABHAENGIGE Quelle nennt den "
      "Code zwar ebenfalls, aber OHNE eigenstaendigen Rollenbeleg "
      "('Fachmedium' erwaehnt UXC1 nur beilaeufig, kein 'Baureihe'/"
      "'Chassis-Code'/...) -> keine erfundene Bestaetigung",
      ident3.generation is None, f"generation={ident3.generation!r}")

# ── 4) Zwei widersprechende, je passende Herstellerbelege: ungeklaert ──────
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
ident4, _ = werte_identitaet_aus([LEXUS_AMBIG, ROLLE_AAB1, ROLLE_AAC1], z)
check("4) ZWEI exakt verschiedene Codes (AAB1/AAC1), BEIDE mit Tier-1-"
      "Herstelleridentifikation UND unabhaengigem Rollenbeleg -> keine "
      "konkurrierende Hersteller-/amtliche Evidenz bleibt ungeklaert, "
      "kein blindes Auswaehlen",
      ident4.generation is None and ident4.generation_cluster == [],
      f"generation={ident4.generation!r} cluster={ident4.generation_cluster!r}")

# ── 5) Drittquelle mit konkretem sachlichem Zuordnungswiderspruch: NICHT
#      stillschweigend ignoriert, aber durch bestehende aus/ein-Gewichtung
#      (unveraendert) gegen staerkere, bestaetigende Evidenz abgewogen ───────
DRITT_WIDERSPRUCH = treffer(
    "https://www.kfz-betrieb.vogel.de/lexus-rc-f-uxc1-ende",
    "Lexus RC F: Produktionsende der Baureihe UXC1",
    "Marke Lexus Modell RC F Baureihe RC (UXC1) wurde nur bis 2016 gebaut, danach Nachfolger.")
claims_widerspruch = [c for r in [ADAC, CARWIKI, AMS, LEXUS_PDF, DRITT_WIDERSPRUCH]
                      for c in _identitaets_claims(r, z)]
_generationen_pruefen(claims_widerspruch, None)
dritt_claim = [c for c in claims_widerspruch if c["domain"] == "kfz-betrieb.vogel.de"]
check("5a) der Widerspruch wird NICHT stillschweigend fallengelassen -- er "
      "bleibt als eigener, einsehbarer Claim mit 'verworfen=baujahr_ausserhalb' "
      "sichtbar (fliesst ins 'aus'-Set der bestehenden Ausschlusspruefung ein)",
      len(dritt_claim) == 1 and dritt_claim[0].get("verworfen") == "baujahr_ausserhalb"
      and dritt_claim[0]["zeitraum"] == (None, 2016),
      f"dritt_claim={dritt_claim!r}")
ident5, _ = werte_identitaet_aus([ADAC, CARWIKI, AMS, LEXUS_PDF, DRITT_WIDERSPRUCH], z)
check("5b) der einzelne Widerspruch wird durch die bestehende, unveraenderte "
      "aus/ein-Regel gegen Lexus' UND ADACs fortbestehende Bestaetigung "
      "abgewogen (nicht automatisch uebernommen) -- UXC1 bleibt bestaetigt, "
      "dieselbe Regel wie bei jedem anderen Code (Fall G)",
      ident5.generation == "UXC1", f"generation={ident5.generation!r}")

# ── 6) Abweichende Werte/Quellen bleiben nachvollziehbar ───────────────────
check("6) 'XC1' bleibt mit Domain-Herkunft in generation_abweichende_kennungen "
      "sichtbar, OHNE je in generation_cluster zu erscheinen (keine "
      "Codegleichsetzung)",
      ident1.generation_cluster == ["UXC1"]
      and ident1.generation_abweichende_kennungen == [
          {"code": "XC1", "domains": ["auto-motor-und-sport.de", "carwiki.de"]}],
      f"cluster={ident1.generation_cluster!r} "
      f"abweichend={ident1.generation_abweichende_kennungen!r}")
check("6b) Fremdgenerationsfilter uebernimmt die abweichende Kennung NICHT "
      "als Alias -- eine Quelle, die direkt 'XC1' nennt, bleibt weiterhin "
      "als fremd erkannt",
      _fremde_generation(
          treffer("https://x.de/xc1", "Lexus RC XC1 Wartung", "Der Lexus RC XC1: Wartung."),
          "RC", ident1.generation, ident1.generation_cluster) is True)

# ── 7) Generische Kontrolle mit anderem synthetischem Fahrzeug (kein Lexus) ─
VW_HERSTELLER_PDF = treffer(
    "https://www.vw.de/content/dam/vw/de/besitzer/"
    "fahrzeuginformationen/golf_tcm-1-9.pdf",
    "VW Golf GTI Rettungskarte",
    "VW Golf GTI\n(UCD9 (EU, M), ab 09/2019)")
VW_ROLLE = treffer(
    "https://www.adac.de/vw-golf-ucd9",
    "VW Golf GTI (05/19): Technische Daten | ADAC",
    "Marke VW Modell Golf GTI Baureihe Golf (UCD9) Herstellerinterne "
    "Baureihenbezeichnung UCD9 Modellstart 05/19")
VW_DRITT = treffer(
    "https://carwiki.de/vw-golf-hsn-tsn", "VW Golf HSN & TSN Tabelle",
    "Hersteller: VW Fahrzeug: Golf Baureihe: Golf CD9 Karosserie: Schraegheck "
    "MODELL HSN SCHLUESSELNUMMER VW Golf GTI 0603 ABC Benzin 1984 cm3 245 PS")
# Zweite, tier-2-starke Drittquelle fuer "CD9" (wie AMS fuer "XC1" im realen
# RC-F-Fall) -- ohne sie waere "CD9" (nur carwiki.de, Tier 3) gar nicht
# "stark" genug, um ueberhaupt einen echten Konflikt mit konsens() auszuloesen.
VW_DRITT2 = treffer(
    "https://www.auto-motor-und-sport.de/vw-golf", "VW Golf (8. Generation) (CD9) im Ueberblick (ab 2020)",
    "VW Golf (8. Generation) (CD9) im Ueberblick (ab 2020). Der VW Golf GTI leistet 245 PS.")
z_vw = _ziel("VW", "Golf GTI", 2020, "2.0 TSI", None)
ident7, _ = werte_identitaet_aus([VW_HERSTELLER_PDF, VW_ROLLE, VW_DRITT, VW_DRITT2], z_vw)
check("7) SYNTHETISCH, generisch (kein Lexus, keine Markenliste): dieselbe "
      "Mechanik loest strukturell identisch zugunsten der VW-Herstellerkennung "
      "UCD9 auf, 'CD9' bleibt abweichende Drittbezeichnung",
      ident7.generation == "UCD9" and ident7.generation_cluster == ["UCD9"]
      and ident7.generation_nachweisweg == "herstelleridentifikation"
      and {d["code"] for d in ident7.generation_abweichende_kennungen} == {"CD9"},
      f"generation={ident7.generation!r} cluster={ident7.generation_cluster!r} "
      f"abweichend={ident7.generation_abweichende_kennungen!r}")

# ── 8) Vollstaendiger RC-F-Replay + tatsaechlicher Empfehlungsausgang ──────
# (Identitaetsstruktur bereits in Check 1 gezeigt; hier zusaetzlich der
# tatsaechliche Effekt auf die Empfehlung, Auftrag §7 "Was aendert sich an
# der Empfehlung?" -- realistisches field_evidence wie es
# app/vehicle_identity.py fuer einen web-only bestaetigten Wert setzt.)


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


def _fe(state: str) -> dict:
    return {"verification_state": state, "confirmed_by": []}


ident_ohne_evidenz = _Identity(
    generation=ident1.generation,
    field_evidence={"make": _fe("user_confirmed"), "model": _fe("user_confirmed")})
entscheidung_vorher = entscheide("kaufen_nach_besichtigung", ident_ohne_evidenz)
check("8a) ein blosser generation-Wert OHNE field_evidence-Bestaetigung "
      "aendert an der Empfehlung nichts -- bleibt STATE_LIMITED mit "
      "'Generation' als Luecke (Wert und Evidenzstatus sind getrennt)",
      entscheidung_vorher.state == STATE_LIMITED
      and "Generation" in entscheidung_vorher.identitaet.fehlend)

ident_mit_evidenz_und_motor = _Identity(
    generation=ident1.generation, engine_code="2UR-GSE",
    field_evidence={"make": _fe("user_confirmed"), "model": _fe("user_confirmed"),
                    "generation": _fe("web_only"), "engine_code": _fe("web_only")})
entscheidung_nachher = entscheide("kaufen_nach_besichtigung", ident_mit_evidenz_und_motor)
check("8b) MIT korrekt gesetztem field_evidence (so wie es "
      "vehicle_identity.py fuer einen ueber Nachweisweg "
      "'herstelleridentifikation' web-bestaetigten Wert setzt) UND "
      "bestaetigter Motorisierung aendert sich die Empfehlung tatsaechlich "
      "auf STATE_NORMAL -- 'UXC1' zaehlt wie jede andere bestaetigte "
      "Generation, keine Sonderbehandlung",
      entscheidung_nachher.state == STATE_NORMAL
      and entscheidung_nachher.empfehlung == "kaufen_nach_besichtigung",
      f"state={entscheidung_nachher.state!r} empfehlung={entscheidung_nachher.empfehlung!r}")

print()
if _FEHLER:
    print(f"FEHLER: {len(_FEHLER)} Check(s) fehlgeschlagen: {_FEHLER}")
    raise SystemExit(1)
print("ALLE HERSTELLERIDENTIFIKATION-KONFLIKTVORRANG-TESTS GRUEN")
