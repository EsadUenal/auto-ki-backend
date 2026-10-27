"""
RC-F-Folgeauftrag (2026-10-25, inkl. fachlichem Abschluss vom selben Tag):
gebündeltes Reparaturpaket für die in der Ursachenanalyse "Schwachstellen &
Wartung" belegten Befunde F12/F13/F14 (technische Extraktion), F17
(Hersteller-/Publisher-Erkennung), F18 (bedingte Kontextzuordnung) und die
Generation/Motorisierung-Asymmetrie in app/empfehlungs_policy.py.

Jeder Block testet GENAU den real belegten Fall aus dem Analyse-Bericht
(carwiki.de, toyota-media.de, newsroom.lexus.eu, lexus.de, die reale
RC-F-SPORT-Geschwistertext-Formulierung) UND die im Umsetzungsauftrag
ausdrücklich geforderten Gegenbeispiele (Aufzählung mehrerer Varianten,
Fremdmodell dazwischen, Täuschungsdomains, PS-allein ohne Motoridentität,
rein alphabetische Motorcodes, verneinte Rückreferenzen).

    python test_rc_f_schwachstelle_wartung_reparaturpaket.py
"""
from app.technical_research import (
    _claim_widerspricht_fahrzeug_lokal, _identitaets_claims, _ist_motorcode_wert,
    _marke_eigene_domain, _rueckwaerts_erbfaehige_indizes, _score_domain_fuer_marke,
    _wartung_kandidat_saetze, _RE_MOTORCODE_CLAIM, ausgerichtet,
)
from app.empfehlungs_policy import identitaet_aufloesen, VERIFIED, PARTIAL
from app.web_search import (
    _qualitaets_label, score_domain, KATEGORIE_WARTUNG, KATEGORIE_SCHWACHSTELLEN, KATEGORIE_RUECKRUFE,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


_ZIEL = dict(marke="Lexus", modell="RC F", baujahr=2017)

# ── A) F12/F13/F14: technische Extraktion ───────────────────────────────────

_r_ccm = {"url": "https://carwiki.de/x", "title": "Lexus RC F Motor",
         "content": "Der Motor hat 4969 cm³ Hubraum und leistet 477 PS."}
_claims_ccm = _identitaets_claims(_r_ccm, _ZIEL)
check("F12: 4969 cm³ wird als Hubraum 5.0 erkannt",
      ("displacement", "5.0") in [(c["feld"], c["wert"]) for c in _claims_ccm])

_r_auto = {"url": "https://x.de/y", "title": "Lexus RC F",
          "content": ("Lexus RC F, 5.0 V8, Benzin, 477 PS, Automatik, Heckantrieb. "
                      "Serienmaessig mit Klimaautomatik und Start-Stopp-Automatik.")}
_claims_auto = [c for c in _identitaets_claims(_r_auto, _ZIEL) if c["feld"] == "transmission"]
check("F13: alleinstehendes 'Automatik' wird erkannt",
      any(c["wert"] == "automatik" for c in _claims_auto))
check("F13: Klimaautomatik/Start-Stopp-Automatik erzeugen KEINEN zusätzlichen Claim",
      len(_claims_auto) == 1, str(_claims_auto))

_r_neg = {"url": "https://x.de/z", "title": "Lexus RC F",
         "content": "Der Lexus RC F ist nicht als Automatik erhaeltlich, nur als Schaltgetriebe."}
_claims_neg = [c for c in _identitaets_claims(_r_neg, _ZIEL) if c["feld"] == "transmission"]
check("F13: Negation bleibt erhalten ('nicht als Automatik')", not any(
    c["wert"] == "automatik" for c in _claims_neg), str(_claims_neg))

def _motorcodes(text: str) -> list[str]:
    return [m.group(1) for m in _RE_MOTORCODE_CLAIM.finditer(text)
            if _ist_motorcode_wert(m.group(1), text[m.start(1):])]


# Abschlussauftrag Runde 2 (2026-10-25): Großschreibung allein war KEIN
# verlässlicher Unterschied -- eine in Großbuchstaben geschriebene/gerenderte
# Kopfzeile wäre davon nicht zu unterscheiden gewesen. Die jetzige Prüfung
# ist case-UNABHÄNGIG und rein strukturell (Länge bei rein alphabetischen
# Werten + Mehrfach-Kopfzeilen-Erkennung über das Pipe-Muster).
check("F14: Motorcode ueber Pipe-Trennzeichen ('Motorcode | 2UR-GSE')",
      _motorcodes("Motorcode | 2UR-GSE") == ["2UR-GSE"])
check("F14: rein ALPHABETISCHER Motorcode (klassisch VW/Audi, 'AHU'/'ALH'/'AFN') wird erkannt",
      _motorcodes("Motorcode: AHU") == ["AHU"]
      and _motorcodes("motorcode: ALH") == ["ALH"]
      and _motorcodes("MOTORCODE: AFN") == ["AFN"])
check("F14: kurzer alphanumerischer Code (N55, OM651, B47D20) bleibt erkannt",
      _motorcodes("motorcode: N55") == ["N55"]
      and _motorcodes("MOTORCODE: OM651") == ["OM651"]
      and _motorcodes("Motorcode: B47D20") == ["B47D20"])
check("F14: Mehrfach-Kopfzeile normal geschrieben ('Motorcode | Hubraum | Leistung') "
      "erzeugt KEINE Bestätigung", _motorcodes("Motorcode | Hubraum | Leistung") == [])
check("F14: Mehrfach-Kopfzeile VOLLSTÄNDIG GROSSGESCHRIEBEN ('MOTORCODE | GETRIEBE | "
      "KRAFTSTOFF') erzeugt ebenfalls KEINE Bestätigung -- Großschreibung allein war "
      "kein verlässlicher Unterschied",
      _motorcodes("MOTORCODE | GETRIEBE | KRAFTSTOFF") == [])
check("F14: Kopfzeile mit Motorcode-typischer Einheit ('Motorcode | PS | Leistung') "
      "erzeugt KEINE Bestätigung", _motorcodes("Motorcode | PS | Leistung") == [])
check("F14: fehlender Wert ('unbekannt', klein UND groß geschrieben) wird NICHT übernommen",
      _motorcodes("Motorcode: unbekannt") == [] and _motorcodes("Motorcode: UNBEKANNT") == [])
check("F14: eindeutiger Wert direkt vor einer Zahl/Einheit ('Motorcode | AHU | 1900 cm3') "
      "bleibt erkannt -- kein Folge-Label, keine Mehrdeutigkeit",
      _motorcodes("Motorcode | AHU | 1900 cm3") == ["AHU"])
check("F14: derselbe Wert gefolgt von einem WEITEREN Label ('Motorcode | AHU | Hubraum | "
      "1900 cm3') bleibt bei mehrdeutiger Kopf-/Wertstruktur UNBESTÄTIGT",
      _motorcodes("Motorcode | AHU | Hubraum | 1900 cm3") == [])

# ── B) F17: Hersteller-/Publisher-Erkennung ─────────────────────────────────

check("F17: echte Hersteller-Subdomain (media.bmw.com) -> Hersteller-Tier",
      score_domain("https://media.bmw.com/x", KATEGORIE_WARTUNG) == 48)
check("F17: belegte, eigenständig benannte Presse-Domain (toyota-media.de) -> Hersteller-Tier",
      score_domain("https://www.toyota-media.de/presse/x", KATEGORIE_SCHWACHSTELLEN) == 48)
check("F17: Täuschung -- Marke als Subdomain einer FREMDEN Domain bleibt KEIN Hersteller",
      score_domain("https://toyota.fake-seite.com/x") == 0)
check("F17: Drittanbieter mit Markenname im Domainnamen bleibt KEIN Hersteller",
      score_domain("https://mytoyotaworld.com/x") == 0)
check("F17: Markenname nur im URL-PFAD zaehlt nicht als Hersteller-Domain",
      score_domain("https://example.com/toyota-news/artikel") == 0)
check("F17: _marke_eigene_domain(lexus.de, Lexus) bleibt generisch ueber PSL erkannt (unveraendert)",
      _marke_eigene_domain("lexus.de", "Lexus") is True)
check("F17: _marke_eigene_domain(newsroom.lexus.eu, Lexus) -- reale Hersteller-Subdomain",
      _marke_eigene_domain("newsroom.lexus.eu", "Lexus") is True)
check("F17: 'lexus' bleibt bewusst NICHT in der score_domain()-Markenliste (siehe "
      "test_identity_manufacturer_source.py)", score_domain("https://www.lexus.de/x") == 0)

# Verbleibende Scoring-Asymmetrie (Abschlussauftrag 2026-10-25): score_domain()
# allein kennt lexus.de fuer schwachstelle/wartung weiterhin nicht -- erst
# _score_domain_fuer_marke() (im vollstaendigen Pfad von _extrahiere_fakten()/
# den Candidate-Funktionen genutzt) schliesst die Luecke generisch ueber die
# TATSAECHLICH angefragte Marke, ohne Markenlistenerweiterung.
check("F17-Folgefix: lexus.de wird fuer 'wartung' MIT Zielmarke 'Lexus' jetzt wie Tier 1 behandelt",
      _score_domain_fuer_marke("https://www.lexus.de/x", KATEGORIE_WARTUNG, "Lexus") == 48)
check("F17-Folgefix: lexus.de wird fuer 'schwachstelle' MIT Zielmarke 'Lexus' jetzt wie Tier 1 behandelt",
      _score_domain_fuer_marke("https://www.lexus.de/x", KATEGORIE_SCHWACHSTELLEN, "Lexus") == 48)
check("F17-Folgefix: dieselbe Domain mit FALSCHER Zielmarke bleibt 0 (kein Marken-Mixup)",
      _score_domain_fuer_marke("https://www.lexus.de/x", KATEGORIE_WARTUNG, "BMW") == 0)
check("F17-Folgefix: Taeuschungsdomain bleibt 0, auch mit der angefragten Marke als Kontext",
      _score_domain_fuer_marke("https://toyota.fake-seite.com/x", KATEGORIE_WARTUNG, "Toyota") == 0)
check("F17-Folgefix: Drittanbieter-Domain bleibt 0, auch mit der angefragten Marke als Kontext",
      _score_domain_fuer_marke("https://mytoyotaworld.com/x", KATEGORIE_WARTUNG, "Toyota") == 0)
check("F17-Folgefix: RUECKRUF bleibt bewusst unberuehrt (Recall/KBA nicht angefasst)",
      _score_domain_fuer_marke("https://www.lexus.de/x", KATEGORIE_RUECKRUFE, "Lexus") == 0)

# ── B2) F16: Schwestermodell-/Geschwistertext-Kontamination unter heutiger
# Herstellerbewertung (Fortsetzungsauftrag, real nachgeprueft) ──────────────
#
# Ursachenbericht (25.10.2026): newsroom.lexus.eu, Europapremiere 2014,
# "The RC F SPORT features an upgraded LDH system" -> ausgerichtet(marke=
# "Lexus", modell="RC F") ergab (True, "schwach"). Damals kein falscher
# sichtbarer Fakt, u.a. WEIL newsroom.lexus.eu unter der Hauptschwelle lag.
# Mit dem F17-Fix oben (PSL-generische `_marke_eigene_domain()`) gilt diese
# Domain jetzt als Hersteller-Tier-1 (s. Zeile 112f.) -- die fruehere
# Schutzbedingung "niedrige Quellenbewertung" entfaellt. Deshalb hier real
# nachgeprueft (nicht nur angenommen): die oeffentliche Pressemeldung
# (newsroom.lexus.eu, "European premiere of the new Lexus RC at the 2014
# Geneva Motor Show") beschreibt im selben Fliesstext DREI verschiedene
# Fahrzeuge -- RC 300h (Hybrid, 2.5L), RC 350 (3.5L V6, Achtstufen-SDS) UND
# RC 350 F SPORT (Fahrwerks-/Lenkungspaket, "RC F SPORT" im Text abgekuerzt)
# -- der eigentliche RC F (5.0 V8) kommt im Artikel gar nicht vor. Die
# einzige Stelle, an der ueberhaupt die Tokenfolge "RC"+"F" vorkommt, ist der
# LDH-Lenkungssatz zum F-SPORT-Fahrwerk, in einem eigenen Absatz OHNE jede
# Motor-/Kraftstoff-/Getriebeangabe -- die Hybrid-/V6-Antriebsangaben stehen
# in eigenen Saetzen, die "RC F" nicht nennen.
_r_newsroom_lexus = {
    "url": "https://newsroom.lexus.eu/2014-european-premiere-of-the-new-lexus-rc-at-the-2014-geneva-motor-show/",
    "title": "European premiere of the new Lexus RC at the 2014 Geneva Motor Show",
    "content": (
        "The full hybrid variant, the RC 300h, combines a 2.5 litre petrol "
        "engine with an electric motor for a full hybrid drivetrain. "
        "The RC 350 is equipped with a 3.5 litre V6 engine mated to an "
        "eight-speed Sport Direct Shift automatic transmission. "
        "The RC F SPORT features an upgraded LDH system, which integrates "
        "Dynamic Rear Steering, Variable Gear Ratio Steering and Electric "
        "Power Steering for sharper turn-in and stability."
    ),
}
check("F16: ausgerichtet() bleibt 'schwach' (Titel nennt nur 'RC', nicht 'RC F')",
      ausgerichtet(_r_newsroom_lexus, "Lexus", "RC F") == (True, "schwach"))
_claims_newsroom = _identitaets_claims(_r_newsroom_lexus, _ZIEL)
check("F16: KEIN Hybrid-/Kraftstoff-Claim aus dem RC-300h-Satz (nennt 'RC F' nicht, "
      "bleibt durch den 'schwach'-Satzfilter aussen vor)",
      not any(c["feld"] in ("fuel", "powertrain") for c in _claims_newsroom), str(_claims_newsroom))
check("F16: KEIN Getriebe-Claim aus dem RC-350-Satz (nennt 'RC F' nicht, "
      "bleibt durch den 'schwach'-Satzfilter aussen vor)",
      not any(c["feld"] == "transmission" for c in _claims_newsroom), str(_claims_newsroom))
check("F16: der einzige 'RC F'-tragende Satz (LDH/Lenkung) erzeugt UEBERHAUPT "
      "KEINEN Claim (keine Motor-/Kraftstoff-/Getriebe-/Motorcode-Vokabel darin) "
      "-> auch unter heutiger Hersteller-Tier-1-Bewertung kein falscher "
      "sichtbarer Fakt fuer 'RC F'", _claims_newsroom == [], str(_claims_newsroom))


# ── C) F18: bedingte Rückwärts-Kontextzuordnung ─────────────────────────────

_r_carwiki = {
    "url": "https://carwiki.de/lexus-rc-oelwechsel",
    "title": "Ölwechsel am Lexus RC | Kosten, Wechselintervall & Ölmengen",
    "content": ("Normal: Wechseln 15000 km / 12 Monate. Schwere Einsatzbedingungen: "
                "Wechseln 7500 km / 6 Monate. Modelle: RC 200t, RC 300 (ASC10), "
                "RC 300h (AVC10), RC 350 und RC F (USC10)."),
}
_saetze_carwiki = _wartung_kandidat_saetze(_r_carwiki, "Lexus", "RC F", [])
check("F18: carwiki.de (reale Mehrvarianten-Aufzählung) -- Intervallsätze bleiben UNGEKLÄRT",
      not any("15000 km" in s or "7500 km" in s for s in _saetze_carwiki), str(_saetze_carwiki))

_r_single = {"url": "https://beispiel.de/x", "title": "Wartung",
            "content": ("Alle 60.000 km wird das Getriebeoel gewechselt. Diese Angabe gilt "
                        "fuer den Lexus RC F.")}
_saetze_single = _wartung_kandidat_saetze(_r_single, "Lexus", "RC F", [])
check("F18: echte EINDEUTIGE Zuordnung (EIN Folgesatz, EIN Modell) wird jetzt übernommen",
      any("60.000 km" in s for s in _saetze_single), str(_saetze_single))

_r_fremd = {"url": "https://beispiel.de/y", "title": "Wartung",
           "content": ("Alle 60.000 km wird das Getriebeoel gewechselt. Das gilt fuer den "
                       "Mazda MX-5. Spaeter wird auch der Lexus RC F erwaehnt.")}
_saetze_fremd = _wartung_kandidat_saetze(_r_fremd, "Lexus", "RC F", [])
check("F18: Fremdmodell ZWISCHEN Aussage und Zielmodell bleibt blockiert",
      not any("60.000 km" in s for s in _saetze_fremd), str(_saetze_fremd))

check("F18: Aufzählungs-Erkennung greift generisch (RC-Präfix >1x = Aufzählung)",
      _rueckwaerts_erbfaehige_indizes(
          ["Normal: 15000 km.", "RC 200t, RC 300 und RC F genannt."], modell="RC F") == set())

# Fachliche Präzisierung (Abschlussauftrag 2026-10-25): "der folgende Satz
# nennt das Zielmodell" ALLEIN beweist KEINE Zuordnung -- erst eine echte
# Rückreferenz genügt. Alle fünf im Auftrag genannten Fälle real geprüft:

_r_fremd_dann_intro = {"url": "https://beispiel.de/a", "title": "Wartung",
    "content": ("Der Oelwechsel beim Mazda 3 erfolgt alle 30.000 Kilometer. "
                "Der Lexus RC F ist ein Hochleistungscoupe.")}
check("F18-1) Fremdmodell-Wert, danach allgemeiner Einführungssatz zum Zielmodell "
      "-- Wert bleibt NICHT zugeordnet",
      not any("30.000" in s for s in _wartung_kandidat_saetze(_r_fremd_dann_intro, "Lexus", "RC F", [])),
      str(_wartung_kandidat_saetze(_r_fremd_dann_intro, "Lexus", "RC F", [])))

_r_blosse_nennung = {"url": "https://beispiel.de/b", "title": "Wartung",
    "content": ("Alle 60.000 Kilometer wird das Getriebeoel generell gewechselt. "
                "Der Lexus RC F kam 2014 auf den Markt.")}
_erg_blosse_nennung = _wartung_kandidat_saetze(_r_blosse_nennung, "Lexus", "RC F", [])
check("F18-2) Allgemeine Intervallangabe, danach BLOSSE Zielmodellnennung (kein Rückbezug) "
      "-- Wert bleibt UNGEKLÄRT", not any("60.000" in s for s in _erg_blosse_nennung),
      str(_erg_blosse_nennung))

_r_echte_rueckref = {"url": "https://beispiel.de/c", "title": "Wartung",
    "content": ("Alle 60.000 Kilometer wird das Getriebeoel gewechselt. "
                "Diese Angabe gilt fuer den Lexus RC F.")}
check("F18-3) Explizite Rückreferenz ordnet den Wert tatsächlich zu",
      any("60.000" in s for s in _wartung_kandidat_saetze(_r_echte_rueckref, "Lexus", "RC F", [])))

_r_segmentbruch = {"url": "https://beispiel.de/d", "title": "Wartung",
    "content": "Alle 60.000 Kilometer wird das Getriebeoel gewechselt.\n\n## Innenraum\nDiese Angabe gilt fuer den Lexus RC F."}
_erg_segmentbruch = _wartung_kandidat_saetze(_r_segmentbruch, "Lexus", "RC F", [])
check("F18-4) Echter Segmentbruch (Absatz/Überschrift) zwischen Wert und Rückreferenz "
      "unterbindet die Zuordnung", not any("60.000" in s for s in _erg_segmentbruch),
      str(_erg_segmentbruch))

_r_normal_schwer = {"url": "https://beispiel.de/e", "title": "Wartung",
    "content": ("Alle 15.000 Kilometer wird das Oel unter Normalbedingungen gewechselt. "
                "Unter erschwerten Bedingungen gilt fuer den Lexus RC F ein kuerzeres "
                "Intervall von 7.500 Kilometern.")}
_erg_normal_schwer = _wartung_kandidat_saetze(_r_normal_schwer, "Lexus", "RC F", [])
check("F18-5) Normal-/erschwerte Intervalle bleiben unterscheidbar, keine Vermischung",
      any("7.500" in s for s in _erg_normal_schwer), str(_erg_normal_schwer))

# Reale, dokumentierte Fehlzuordnung: "Der Ölwechsel ..." wurde trotz eigener
# Zielmodellnennung als unsichere Satzanfangs-Nominalphrase verworfen.
check("F18-Ölwechsel-Fix: eigene Zielmodellnennung schlägt die Satzanfangs-Heuristik",
      _claim_widerspricht_fahrzeug_lokal(
          "Der Ölwechsel erfolgt alle 60.000 Kilometer beim Lexus RC F.", "Lexus", "RC F") is None)
check("F18-Ölwechsel-Fix: Fremdmodellschutz bleibt erhalten (echtes Fremdfahrzeug-Subjekt)",
      _claim_widerspricht_fahrzeug_lokal(
          "Der Ölwechsel erfolgt alle 30.000 Kilometer beim Mazda 3.", "Lexus", "RC F") is not None)
check("F18-Ölwechsel-Fix: Fremdmodellschutz bleibt erhalten (Zielmodell explizit ausgeschlossen)",
      _claim_widerspricht_fahrzeug_lokal(
          "Der Ölwechsel erfolgt beim Mazda 3, nicht beim Lexus RC F.", "Lexus", "RC F") is not None)

_r_verneinte_rueckref = {"url": "https://beispiel.de/f", "title": "Wartung",
    "content": ("Alle 60.000 Kilometer wird das Getriebeoel gewechselt. "
                "Das gilt NICHT fuer den Lexus RC F.")}
check("F18-Verneinung: eine VERNEINTE Rückreferenz ordnet den Wert NICHT zu",
      _rueckwaerts_erbfaehige_indizes(
          ["Alle 60.000 Kilometer wird das Getriebeoel gewechselt.",
           "Das gilt NICHT fuer den Lexus RC F."], modell="RC F") == set())

# ── D) Generation/Motorisierung-Asymmetrie (empfehlungs_policy.py) ─────────


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


# D1: bestätigte PS ALLEIN (web_only, kein Motor/Motorcode) darf NICHT mehr
# motor_belegt=True erzeugen -- genau der RC-F-Fall ("477 PS webbestätigt",
# Motor/Kraftstoff/Getriebe unbestätigt).
ident_ps_allein = _Identity(
    generation=None, horsepower=477,
    field_evidence={"make": _fe("user_confirmed"), "model": _fe("user_confirmed"),
                    "horsepower": _fe("web_only")})
aufl_ps_allein = identitaet_aufloesen(ident_ps_allein)
check("D1: PS allein (web_only) erzeugt KEIN motor_belegt -> 'Motorisierung' bleibt offen",
      "Motorisierung" in aufl_ps_allein.fehlend and aufl_ps_allein.stufe == PARTIAL,
      str(aufl_ps_allein))

# D2: ein ECHT bestätigter Motorcode (z.B. aus F14, web-bestätigt, nicht nur
# Nutzerangabe) zählt jetzt -- ENFAL- und Web-Provenienz gleich behandelt.
ident_motorcode_web = _Identity(
    generation="USC10", engine_code="2UR-GSE",
    field_evidence={"make": _fe("user_confirmed"), "model": _fe("user_confirmed"),
                    "generation": _fe("web_only"),
                    "engine_code": _fe("web_only")})
aufl_motorcode_web = identitaet_aufloesen(ident_motorcode_web)
check("D2: web-bestätigter Motorcode zählt als Motorisierungs-Beleg (keine ENFAL-Pflicht mehr)",
      "Motorisierung" not in aufl_motorcode_web.fehlend, str(aufl_motorcode_web))
check("D2: mit Generation UND Motorcode bestätigt -> VERIFIED",
      aufl_motorcode_web.stufe == VERIFIED, str(aufl_motorcode_web))

# D3: eine reine Nutzerangabe (user_only) bleibt unzureichend -- keine
# Lockerung in die andere Richtung.
ident_user_only = _Identity(
    generation=None, engine_name="5.0 V8",
    field_evidence={"make": _fe("user_confirmed"), "model": _fe("user_confirmed"),
                    "engine_name": _fe("user_only")})
aufl_user_only = identitaet_aufloesen(ident_user_only)
check("D3: reine Nutzerangabe (user_only) bleibt unzureichend fuer 'Motorisierung'",
      "Motorisierung" in aufl_user_only.fehlend, str(aufl_user_only))

# D4: Generation wird weiterhin NIE aus PS oder Motorcode automatisch
# bestätigt -- keine Lockerung des Generation-Gates.
ident_motorcode_ohne_gen = _Identity(
    generation=None, engine_code="2UR-GSE",
    field_evidence={"make": _fe("user_confirmed"), "model": _fe("user_confirmed"),
                    "engine_code": _fe("web_only")})
aufl_motorcode_ohne_gen = identitaet_aufloesen(ident_motorcode_ohne_gen)
check("D4: Motorcode bestätigt generation NICHT automatisch -- 'Generation' bleibt offen",
      "Generation" in aufl_motorcode_ohne_gen.fehlend and aufl_motorcode_ohne_gen.stufe == PARTIAL,
      str(aufl_motorcode_ohne_gen))

print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print(f"ALLE {_ZIEL and 'REPARATURPAKET-'}TESTS GRUEN")
