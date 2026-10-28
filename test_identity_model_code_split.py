"""
Lexus-RC-F-Forensik, Package A (Generic Identity) — Root Causes 2/3,
plus Package-A-Hardening (Folgeauftrag): Core/Auxiliary/Confirmed-Generation.

BEFUND (Root Cause 2/3): `ausgerichtet()`/`_identitaet_belegt()` verlangten
bisher ALLE Tokens des `modell`-Strings als zwingendes UND auf der Quelle.
Tippt ein Nutzer einen präzisierenden Generations-/Chassiscode direkt ins
Modellfeld ("RC F USC10"), scheitert praktisch jede reale Quelle (Code steht
fast nie in Fließtext) — Identity komplett blockiert, obwohl Marke+Kernmodell
eindeutig sind.

FIX (Root Cause 2/3): `parse_core_model()` trennt einen optional NACHGE-
STELLTEN Code generisch ab (Form-basiert: Ziffer+Buchstabe ODER reine 2-3-
stellige Ziffernfolge, kompakt, keine Leistungs-/Kraftstoff-Kürzel) — OHNE
Marken-/Modellkatalog. Nur das LETZTE Token eines MEHRTEILIGEN Strings kommt
in Frage, damit Einzeltoken-Modelle ("GT86", "C300", "A4", "M4") nie
fälschlich zerlegt werden.

HÄRTUNG (Folgeauftrag, BEFUND): der abgetrennte Kandidat wurde zu schnell als
bestätigte Generation behandelt — er landete direkt in `z["generation"]` und
genoss dort einen Freifahrschein (`_generationen_pruefen(bekannter_code=...)`
akzeptierte jede Quelle, die ihn nur ERWÄHNTE, ganz ohne Jahr/Kontext). Für
echte Chassiscodes ("USC10", "F82", "W205", "B9") meist harmlos — für reine
LEISTUNGS-/TRIM-Bezeichnungen ("GT3", "R32"), die der Nutzer ebenfalls ins
Modellfeld schreiben kann, hätte JEDE Quelle, die das Fahrzeug nur beim Namen
nennt ("911 GT3 im Test"), die Variante faelschlich als "bestätigte
Generation" ausgewiesen. Dieselbe Verwechslung liess echte
Schwachstellen-/Wartungs-Quellen mit der TATSÄCHLICHEN Generation im Titel
über `_fremde_generation()` als "andere Generation" verwerfen, weil der
unbestätigte Kandidat fälschlich als Vergleichswert diente.

Jetzt drei saubere Stufen (siehe `_ziel()`-Docstring in
app/technical_research.py): CORE MODEL (hartes Alignment), AUXILIARY
CANDIDATE (`z["auxiliary_code"]`, rein formbasiert, darf Queries anreichern,
NIE allein eine Generation bestätigen), CONFIRMED GENERATION (nur per
`_generationscodes()`/`_generationen_pruefen()`/`konsens()` — jeder Code
braucht WEITERHIN entweder eine strukturelle Generationszählung ("Modell IV
(CODE)") ODER eine eindeutige Bezeichnung ("Baureihe …", "Chassis-Code …",
"Werkscode …", "Codename …", "interne Bezeichnung …"), unabhängig davon, ob
er mit dem Kandidaten übereinstimmt. Ein bloß begleitendes Produktionsfenster
ODER ein vages Wort wie "Generation" in der Nähe reichen seit der
semantischen Generationslücke (Fortsetzungsauftrag, Runde 3) NICHT mehr
allein -- s. test_generation_kontext_falschbestaetigung_fix.py, Fall F/G/H).

    python test_identity_model_code_split.py
"""
import asyncio

from app.technical_research import _ziel, parse_core_model, werte_identitaet_aus

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


print("=" * 78)
print("REINE SPLIT-LOGIK: parse_core_model()")
print("=" * 78)

# A) Target "RC F" -- 2 Tokens, aber das zweite ("F", 1 Zeichen) ist zu kurz
# für einen plausiblen Code -> kein Split.
check("A) 'RC F' bleibt unzerlegt (zweites Token zu kurz fuer einen Code)",
      parse_core_model("RC F") == ("RC F", None), str(parse_core_model("RC F")))

# B) Target "RC F USC10" -- Code wird abgetrennt.
check("B) 'RC F USC10' -> Kern 'RC F' + Kandidat 'USC10'",
      parse_core_model("RC F USC10") == ("RC F", "USC10"), str(parse_core_model("RC F USC10")))

# D/E) BMW M4 F82
check("D/E) 'M4 F82' -> Kern 'M4' + Kandidat 'F82'",
      parse_core_model("M4 F82") == ("M4", "F82"), str(parse_core_model("M4 F82")))

# F) Mercedes C300 W205
check("F) 'C300 W205' -> Kern 'C300' + Kandidat 'W205'",
      parse_core_model("C300 W205") == ("C300", "W205"), str(parse_core_model("C300 W205")))

# G) Audi A4 B9
check("G) 'A4 B9' -> Kern 'A4' + Kandidat 'B9'",
      parse_core_model("A4 B9") == ("A4", "B9"), str(parse_core_model("A4 B9")))

# H) GT86 darf NICHT in "GT"+"86" zerlegt werden (ein einzelnes Token).
check("H) 'GT86' (1 Token) bleibt unzerlegt",
      parse_core_model("GT86") == ("GT86", None), str(parse_core_model("GT86")))

# I) C300 allein darf nicht zu "C"+"300" werden.
check("I) 'C300' (1 Token) bleibt unzerlegt",
      parse_core_model("C300") == ("C300", None), str(parse_core_model("C300")))

# K) normales Modell ohne Code bleibt unveraendert.
check("K) 'M4' (1 Token, kein Code) bleibt unzerlegt",
      parse_core_model("M4") == ("M4", None), str(parse_core_model("M4")))

# L) unbekanntes Zusatzwort, das NICHT wie ein Code aussieht (reines Wort,
# keine Ziffer) -- darf nicht als Generation behandelt werden.
for modell_ohne_code in ("Model S Plaid", "A4 Avant", "3er Touring", "Golf GTI"):
    kern, code = parse_core_model(modell_ohne_code)
    check(f"L) '{modell_ohne_code}' -- kein Ziffern-Token am Ende -> kein Split",
          code is None and kern == modell_ohne_code, f"-> ({kern!r}, {code!r})")

# Leistungs-/Kraftstoff-Kürzel werden trotz Ziffer+Buchstabe-Form nicht als Code akzeptiert.
check("Ausschluss: 'RC F 350PS' -> '350PS' zaehlt NICHT als Code (Leistungsangabe)",
      parse_core_model("RC F 350PS") == ("RC F 350PS", None), str(parse_core_model("RC F 350PS")))


print("\n" + "=" * 78)
print("END-TO-END: werte_identitaet_aus() -- Core-Model-Match + Code-Bestaetigung/Konflikt")
print("=" * 78)


async def _ident(marke, modell, baujahr, motor, treffer_liste):
    z = _ziel(marke, modell, baujahr, motor, None)
    ident, abgelehnt = werte_identitaet_aus(treffer_liste, z)
    return ident, abgelehnt, z


# B) Target "RC F USC10", Quelle nur "Lexus RC F" (ohne Code) -> Core-Match,
# Code unbestaetigt, aber KEIN kompletter Identity-Fail.
async def test_b_core_match_ohne_code():
    quelle = [
        treffer("https://www.lexus.de/rc-f", "Lexus RC F",
                "Der Lexus RC F ist das Performance-Coupe von Lexus mit 5.0-Liter-V8."),
        treffer("https://www.auto-motor-und-sport.de/lexus-rc-f", "Lexus RC F im Test",
                "Der Lexus RC F hat 477 PS und beschleunigt in 4,5 Sekunden auf 100 km/h."),
    ]
    ident, abgelehnt, _ = await _ident("Lexus", "RC F USC10", 2017, "5.0 V8", quelle)
    check("B) Identität trotz fehlendem Code BELEGT (kein kompletter Fail)", ident.belegt is True)
    check("B) Generation bleibt unbestaetigt (kein Treffer nennt USC10)", ident.generation is None)


# C) Target "RC F USC10", Quelle "Lexus RC F USC10" OHNE jeden Generations-/
# Jahreskontext -> Modell PASS, Code bleibt UNBESTAETIGT (Haertung: bloße
# Erwaehnung/Adjazenz allein reicht nicht mehr -- sonst wuerde ein reiner
# Trimname wie "GT3" bei jeder Quelle automatisch zur Generation, siehe M/N).
async def test_c_code_ohne_kontext_bleibt_unbestaetigt():
    quelle = [
        treffer("https://www.lexus.de/rc-f", "Lexus RC F USC10",
                "Der Lexus RC F USC10 ist das Performance-Coupe von Lexus."),
        treffer("https://www.auto-motor-und-sport.de/lexus-rc-f", "Lexus RC F USC10 im Test",
                "Der Lexus RC F USC10 hat 477 PS."),
    ]
    ident, abgelehnt, _ = await _ident("Lexus", "RC F USC10", 2017, "5.0 V8", quelle)
    check("C) Identität belegt (Core-Modell reicht)", ident.belegt is True)
    check("C) 'USC10' OHNE Kontext/Jahr bleibt unbestaetigt (Haertung)",
          ident.generation is None, str(ident.generation))


# C2) Dieselbe Quelle, aber MIT expliziter, eindeutiger Bezeichnung -> jetzt
# DARF die Generation bestaetigt werden (Regel 2/8 Auftrag). Fortsetzungs-
# auftrag (Runde 2, gemeinsamer Pfad): ein bloss benachbarter Generations-
# begriff OHNE Jahresfenster/strukturelle Generationszaehlung reicht NICHT
# MEHR (s. test_generation_kontext_falschbestaetigung_fix.py) -- auch fuer
# digit-tragende Codes nicht. Runde 3 (verbleibende semantische Generations-
# luecke): ein bloss BEGLEITENDES Jahresfenster reicht jetzt AUCH NICHT mehr
# (s. dort, Fall F "911 GT3 MIT Produktionsfenster") -- die Quelle muss den
# Code jetzt ausdruecklich als Baureihe/Chassiscode bezeichnen ("Chassiscode"
# statt des vagen "diese Generation"), um eine ECHTE Bestaetigung zu zeigen.
async def test_c2_code_mit_kontext_bestaetigt():
    quelle = [
        treffer("https://www.lexus.de/rc-f", "Lexus RC F USC10",
                "Der Lexus RC F USC10, Chassiscode dieser Baureihe, wird seit 2014 gebaut."),
        treffer("https://www.auto-motor-und-sport.de/lexus-rc-f", "Lexus RC F USC10 im Test",
                "Der Lexus RC F USC10, Chassiscode dieser Baureihe, wird seit 2014 gebaut und hat 477 PS."),
    ]
    ident, abgelehnt, _ = await _ident("Lexus", "RC F USC10", 2017, "5.0 V8", quelle)
    check("C2) Identität belegt", ident.belegt is True)
    check("C2) 'USC10' MIT eindeutiger Bezeichnung ('Chassiscode') bestaetigt",
          ident.generation == "USC10", str(ident.generation))


# C3) Dieselbe Mechanik, aber mit "Modellcode" statt "Chassiscode" --
# PRUEFT NUR DEN MECHANISMUS (Fortsetzungsauftrag, nach Gegenpruefung
# praezisiert): KEINE reale Quelle nennt USC10 tatsaechlich "Modellcode"
# (s. Kommentar bei `_ROLLE_BEZEICHNUNG` in app/technical_research.py fuer
# die real mit exakten URLs gegengeprueften Rollen -- rolling.hu fuehrt
# USC10 nur unter dem Feld "Type", ADAC/carwiki.de vergeben "Baureihe"
# ausschliesslich an den breiteren Code UXC1/XC1). Dieser Test (synthetisch,
# wie alle anderen hier) zeigt nur: WENN eine Quelle "Modellcode" so
# verwendet, wie es die VIN-Dokumentation fuer dieses Wort generisch belegt
# (Modell-/Baureihenebene, nicht Aufbauvariante/Motortyp), wird der
# Mechanismus das korrekt als Rollenbeleg anerkennen -- das ist eine Aussage
# ueber den CODE, nicht ueber USC10 im Speziellen. Ohne eine solche Quelle
# bleibt USC10 laut realer Lage weiterhin UNKNOWN (s. Test B/C oben).
async def test_c3_modellcode_als_rollenbeleg():
    quelle = [
        treffer("https://www.lexus.de/rc-f", "Lexus RC F USC10",
                "Der Lexus RC F USC10 traegt laut Teiledatenbank den Modellcode USC10."),
        treffer("https://www.auto-motor-und-sport.de/lexus-rc-f", "Lexus RC F USC10 im Test",
                "Der Lexus RC F USC10, Modellcode USC10, hat 477 PS."),
    ]
    ident, abgelehnt, _ = await _ident("Lexus", "RC F USC10", 2017, "5.0 V8", quelle)
    check("C3) Identitaet belegt", ident.belegt is True)
    check("C3) 'USC10' MIT 'Modellcode' als Rollenbeleg bestaetigt",
          ident.generation == "USC10", str(ident.generation))


# C4) Gegenfall zu C3 (Auftrag: "Modell-/Typcode einer Variante" darf nicht
# faelschlich den ZIELCODE bestaetigen): "Modellcode" wird im Text einem
# ANDEREN, eigenstaendigen Code in einem eigenen Teilsatz zugewiesen -- der
# eigentliche Zielcode (USC10) steht in einem GETRENNTEN Teilsatz OHNE jeden
# Rollenbeleg. Analog zum bestehenden GT3/991-Gegenfall, jetzt fuer das neue
# Wort: die Teilsatzgrenze muss "Modellcode" weiterhin an seinen EIGENEN Code
# binden, nicht blind an jeden Code im Dokument.
async def test_c4_modellcode_bindet_nur_eigenen_code():
    quelle = [
        treffer("https://www.lexus.de/rc-f", "Lexus RC F Modelle",
                "Die Sonderedition RC F Carbon trägt den Modellcode ABC123; "
                "der reguläre Lexus RC F USC10 hat damit nichts zu tun."),
        treffer("https://www.auto-motor-und-sport.de/lexus-rc-f", "Lexus RC F USC10 im Test",
                "Die Sonderedition RC F Carbon trägt den Modellcode ABC123; "
                "der reguläre Lexus RC F USC10 hat 477 PS und damit nichts zu tun."),
    ]
    ident, abgelehnt, _ = await _ident("Lexus", "RC F USC10", 2017, "5.0 V8", quelle)
    check("C4) Identitaet belegt (Core-Modell reicht)", ident.belegt is True)
    check("C4) 'USC10' bleibt unbestaetigt -- 'Modellcode' gehoert im Text zu "
          "ABC123, einem fremden Code in einem GETRENNTEN Teilsatz",
          ident.generation is None, str(ident.generation))


# C5) Gegenfall (Auftrag: "Motorcode neben 'interne Bezeichnung'" darf nicht
# faelschlich leaken): ein Motorcode steht als eigener, unbeteiligter Code im
# Text, "interne Bezeichnung" bezieht sich im eigenen Teilsatz auf einen
# ANDEREN Code als USC10 -- USC10 selbst bleibt ohne jeden Rollenbeleg in
# seinem eigenen Teilsatz und darf nicht ueber die Dokumentgrenze hinweg von
# der Bezeichnung eines fremden Codes "mitbestaetigt" werden.
async def test_c5_motorcode_neben_interne_bezeichnung_kein_leak():
    quelle = [
        treffer("https://www.lexus.de/rc-f", "Lexus RC F Technik",
                "Der Motorcode des 5.0-Liter-V8 lautet 2UR-GSE; die interne "
                "Bezeichnung ist XYZ99, nicht USC10. Der Lexus RC F USC10 "
                "ist die reine Marketingbezeichnung."),
        treffer("https://www.auto-motor-und-sport.de/lexus-rc-f", "Lexus RC F USC10 im Test",
                "Der Motorcode des 5.0-Liter-V8 lautet 2UR-GSE; die interne "
                "Bezeichnung ist XYZ99, nicht USC10. Der Lexus RC F USC10 "
                "hat 477 PS und ist die reine Marketingbezeichnung."),
    ]
    ident, abgelehnt, _ = await _ident("Lexus", "RC F USC10", 2017, "5.0 V8", quelle)
    check("C5) Identitaet belegt (Core-Modell reicht)", ident.belegt is True)
    check("C5) 'USC10' bleibt unbestaetigt -- 'interne Bezeichnung' ist im "
          "eigenen Teilsatz ausdruecklich XYZ99 zugewiesen und fuer USC10 "
          "sogar verneint ('nicht USC10')",
          ident.generation is None, str(ident.generation))


# C6) Real reproduzierter Gegenfall (Fortsetzungsauftrag, Marketingbezeichnungs-
# Fall, jetzt generisch geschlossen): "ist nur die Marketingbezeichnung
# dieser Baureihe" bestaetigte bisher faelschlich USC10 als Generation, weil
# das blosse Wort "Baureihe" im selben Teilsatz stand -- obwohl der Satz
# NICHT behauptet, dass USC10 die Baureihe IST, sondern nur, dass USC10 eine
# (Marketing-)Bezeichnung FUER eine anderswo etablierte Baureihe ist. Fix:
# "Baureihe" als Genitivobjekt eines vorangehenden "...bezeichnung"-Worts
# zaehlt nicht mehr als eigener Rollenbeleg (s. `_BAUREIHE_FREMDBEZUG`).
# Keine Fahrzeug-/Code-Ausnahme, keine Sonderfallliste -- derselbe generische
# Mechanismus gilt fuer jedes "X-bezeichnung dieser/der Baureihe".
async def test_c6_marketingbezeichnung_kein_rollenbeleg():
    quelle = [
        treffer("https://www.lexus.de/rc-f", "Lexus RC F USC10",
                "Der Lexus RC F USC10 ist nur die Marketingbezeichnung "
                "dieser Baureihe."),
        treffer("https://www.auto-motor-und-sport.de/lexus-rc-f", "Lexus RC F USC10 im Test",
                "Der Lexus RC F USC10 hat 477 PS und ist nur die "
                "Marketingbezeichnung dieser Baureihe."),
    ]
    ident, abgelehnt, _ = await _ident("Lexus", "RC F USC10", 2017, "5.0 V8", quelle)
    check("C6) Identitaet belegt (Core-Modell reicht)", ident.belegt is True)
    check("C6) 'USC10' bleibt unbestaetigt -- 'Baureihe' ist hier Genitivobjekt "
          "von 'Marketingbezeichnung', keine Rollenzuweisung AN den Code",
          ident.generation is None, str(ident.generation))


# D) Target "M4 F82", Quelle "BMW M4" (ohne Code) -> Core akzeptiert, F82 unbestaetigt.
async def test_d_core_akzeptiert_code_offen():
    quelle = [
        treffer("https://www.bmw.de/m4", "BMW M4",
                "Der BMW M4 ist das Performance-Coupe der 4er-Reihe."),
        treffer("https://www.auto-motor-und-sport.de/bmw-m4", "BMW M4 im Test",
                "Der BMW M4 hat 510 PS."),
    ]
    ident, abgelehnt, _ = await _ident("BMW", "M4 F82", 2017, "3.0 Biturbo", quelle)
    check("D) Core-Modell 'M4' akzeptiert (Identität belegt)", ident.belegt is True)
    check("D) 'F82' bleibt unbestaetigt (keine Quelle nennt ihn)", ident.generation is None)


# D2) Dieselbe Quelle wie D, aber ohne jeden Kontext fuer F82 -> bleibt
# unbestaetigt (Haertung, analog zu C).
async def test_d2_code_ohne_kontext_bmw():
    quelle = [
        treffer("https://www.bmw.de/m4", "BMW M4 F82",
                "Der BMW M4 F82 ist das Performance-Coupe der 4er-Reihe."),
        treffer("https://www.auto-motor-und-sport.de/bmw-m4", "BMW M4 F82 im Test",
                "Der BMW M4 F82 hat 510 PS."),
    ]
    ident, abgelehnt, _ = await _ident("BMW", "M4 F82", 2017, "3.0 Biturbo", quelle)
    check("D2) 'F82' OHNE Kontext bleibt unbestaetigt (Haertung)",
          ident.generation is None, str(ident.generation))


# E) Target "M4 F82", Quelle "BMW M4 F82" MIT eindeutiger Bezeichnung ──────
# -> bestaetigt (s. C2-Kommentar: weder Kontext noch Jahresfenster allein
# reichen seit Runde 2/3 fuer irgendeinen Code mehr, auch nicht digit-tragend).
async def test_e_code_bestaetigt_bmw():
    quelle = [
        treffer("https://www.bmw.de/m4", "BMW M4 F82",
                "Der BMW M4 F82, Chassiscode dieser Baureihe, wird seit 2014 gebaut."),
        treffer("https://www.auto-motor-und-sport.de/bmw-m4", "BMW M4 F82 im Test",
                "Der BMW M4 F82, Chassiscode dieser Baureihe, wird seit 2014 gebaut und hat 510 PS."),
    ]
    ident, abgelehnt, _ = await _ident("BMW", "M4 F82", 2017, "3.0 Biturbo", quelle)
    check("E) 'F82' MIT eindeutiger Bezeichnung ('Chassiscode') bestaetigt",
          ident.generation == "F82", str(ident.generation))


# F) Mercedes C300 W205, MIT eindeutiger Bezeichnung -> bestaetigt.
async def test_f_mercedes():
    quelle = [
        treffer("https://www.mercedes-benz.de/c300", "Mercedes C300 W205",
                "Der Mercedes C300 W205, Chassiscode dieser Baureihe, wird seit 2014 gebaut."),
        treffer("https://www.auto-motor-und-sport.de/mercedes-c300", "Mercedes C300 W205 im Test",
                "Der Mercedes C300 W205, Chassiscode dieser Baureihe, wird seit 2014 gebaut und hat 258 PS."),
    ]
    ident, abgelehnt, _ = await _ident("Mercedes-Benz", "C300 W205", 2017, "2.0 Turbo", quelle)
    check("F) 'W205' MIT eindeutiger Bezeichnung ('Chassiscode') bestaetigt",
          ident.generation == "W205", str(ident.generation))


# G) Audi A4 B9, MIT eindeutiger Bezeichnung -> bestaetigt.
async def test_g_audi():
    quelle = [
        treffer("https://www.audi.de/a4", "Audi A4 B9",
                "Der Audi A4 B9, Chassiscode dieser Baureihe, wird seit 2015 gebaut."),
        treffer("https://www.auto-motor-und-sport.de/audi-a4", "Audi A4 B9 im Test",
                "Der Audi A4 B9, Chassiscode dieser Baureihe, wird seit 2015 gebaut und hat 190 PS."),
    ]
    ident, abgelehnt, _ = await _ident("Audi", "A4 B9", 2017, "2.0 TDI", quelle)
    check("G) 'B9' MIT eindeutiger Bezeichnung ('Chassiscode') bestaetigt",
          ident.generation == "B9", str(ident.generation))


# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 78)
print("HAERTUNGS-MATRIX M-T: Trim-/Leistungsnamen duerfen NIE blind Generation werden")
print("=" * 78)


# M) "911 GT3" -- GT3 ist eine Leistungsvariante, keine Chassisgeneration.
# Quellen nennen das Fahrzeug nur beim Namen, OHNE jeden Generations-/
# Jahreskontext -> GT3 darf NICHT automatisch zur Generation werden.
async def test_m_911_gt3_kein_blind_confirm():
    quelle = [
        treffer("https://www.porsche.de/911-gt3", "Porsche 911 GT3",
                "Der Porsche 911 GT3 ist ein Hochleistungssportwagen mit Heckantrieb."),
        treffer("https://www.auto-motor-und-sport.de/porsche-911-gt3", "911 GT3 im Test",
                "Der Porsche 911 GT3 beschleunigt in 3,4 Sekunden auf 100 km/h."),
    ]
    kern, aux = parse_core_model("911 GT3")
    check("M) Core 'GT3' korrekt als Auxiliary-Kandidat abgetrennt",
          kern == "911" and aux == "GT3", f"-> ({kern!r}, {aux!r})")
    ident, abgelehnt, _ = await _ident("Porsche", "911 GT3", 2021, "4.0 Boxer", quelle)
    check("M) Core-Modell '911' akzeptiert (Identität belegt)", ident.belegt is True)
    check("M) 'GT3' NICHT blind als Generation bestaetigt",
          ident.generation is None, str(ident.generation))


# N) "Golf R32" -- R32 ist eine Motor-/Ausstattungsvariante (3.2L V6), keine
# Chassisgeneration. Analog zu M.
async def test_n_golf_r32_kein_blind_confirm():
    quelle = [
        treffer("https://www.vw.de/golf-r32", "VW Golf R32",
                "Der VW Golf R32 hat einen 3.2-Liter-V6-Motor mit Allradantrieb."),
        treffer("https://www.auto-motor-und-sport.de/vw-golf-r32", "Golf R32 im Test",
                "Der VW Golf R32 leistet 241 PS."),
    ]
    kern, aux = parse_core_model("Golf R32")
    check("N) Core 'R32' korrekt als Auxiliary-Kandidat abgetrennt",
          kern == "Golf" and aux == "R32", f"-> ({kern!r}, {aux!r})")
    ident, abgelehnt, _ = await _ident("VW", "Golf R32", 2004, "3.2 V6", quelle)
    check("N) Core-Modell 'Golf' akzeptiert (Identität belegt)", ident.belegt is True)
    check("N) 'R32' NICHT blind als Generation bestaetigt",
          ident.generation is None, str(ident.generation))


# O) "911 992" -- rein numerischer Chassiscode. Splitting entlastet das
# Alignment (Quelle muss "992" nicht zwingend nennen), aber 992 kann
# STRUKTURELL nie zur Generation werden (`_generationscodes()` verlangt
# mindestens einen Buchstaben) -- selbst wenn eine Quelle ihn nennt.
async def test_o_911_992_numerisch():
    kern, aux = parse_core_model("911 992")
    check("O) '911 992' -> Kern '911' + numerischer Kandidat '992'",
          kern == "911" and aux == "992", f"-> ({kern!r}, {aux!r})")
    # Alignment NICHT zerstoert: eine Quelle, die nur "911" nennt (kein "992"),
    # reicht fuer eine belegte Identitaet.
    quelle_nur_kern = [
        treffer("https://www.porsche.de/911", "Porsche 911",
                "Der Porsche 911 ist ein Sportwagen-Klassiker."),
        treffer("https://www.auto-motor-und-sport.de/porsche-911", "911 im Test",
                "Der Porsche 911 hat einen Heckmotor."),
    ]
    ident, abgelehnt, _ = await _ident("Porsche", "911 992", 2020, "3.0 Biturbo", quelle_nur_kern)
    check("O) Alignment ueber Kern-Modell gelingt, obwohl keine Quelle '992' nennt",
          ident.belegt is True)
    check("O) '992' bleibt unbestaetigt (keine Quelle nennt ihn)",
          ident.generation is None, str(ident.generation))
    # Selbst wenn eine Quelle "992" direkt nach "911" nennt, kann daraus NIE
    # eine bestaetigte Generation werden (fehlender Buchstabe im Code).
    quelle_mit_992 = quelle_nur_kern + [
        treffer("https://www.0-100.de/porsche-911-992", "Porsche 911 (992) Daten",
                "Der Porsche 911 (992) hat ein neues Interieur, diese Generation.")]
    ident2, _, _ = await _ident("Porsche", "911 992", 2020, "3.0 Biturbo", quelle_mit_992)
    check("O) '992' bleibt auch bei Nennung unbestaetigt (kein Buchstabe im Code)",
          ident2.generation is None, str(ident2.generation))


# S) Auxiliary-Kandidat fehlt komplett in jeder Quelle -> KEIN `modell_fehlt`,
# Core-Identitaet bleibt voll belegt (bereits durch B/D/M/N gezeigt, hier
# nochmal explizit über den Ablehnungsgrund).
async def test_s_fehlender_auxiliary_kein_modell_fehlt():
    quelle = [
        treffer("https://www.lexus.de/rc-f", "Lexus RC F",
                "Der Lexus RC F ist das Performance-Coupe von Lexus."),
        treffer("https://www.auto-motor-und-sport.de/lexus-rc-f", "Lexus RC F im Test",
                "Der Lexus RC F hat 477 PS."),
    ]
    ident, abgelehnt, _ = await _ident("Lexus", "RC F USC10", 2017, "5.0 V8", quelle)
    check("S) Core-Identitaet trotz fehlendem Auxiliary voll belegt", ident.belegt is True)
    check("S) kein Ablehnungsgrund 'modell_fehlt' auf den Quellen",
          all(a.get("grund") != "modell_fehlt" for a in abgelehnt), str(abgelehnt))


# T) normales mehrteiliges Modell ohne Codecharakter -> nicht auseinander-
# gerissen (bereits durch L abgedeckt, hier als End-to-End-Nachweis: das volle
# Modell bleibt Pflicht für das Alignment).
async def test_t_mehrteiliges_modell_nicht_zerlegt():
    kern, aux = parse_core_model("Model S Plaid")
    check("T) 'Model S Plaid' bleibt als Ganzes Kern-Modell, kein Auxiliary",
          kern == "Model S Plaid" and aux is None, f"-> ({kern!r}, {aux!r})")


# J) expliziter Widerspruch: zwei vergleichbar starke Quellen nennen
# UNTERSCHIEDLICHE Codes (einer davon der vom Nutzer erwartete) -> Konflikt,
# NICHT einfach akzeptiert.
async def test_j_expliziter_code_widerspruch():
    # Fortsetzungsauftrag (Runde 2/3): BEIDE Codes bekommen jetzt je eine
    # eigene eindeutige Bezeichnung ("Chassiscode"/"interne Bezeichnung")
    # -- sonst waeren (seit weder Kontext noch Jahresfenster allein genuegen)
    # BEIDE schon individuell unbestaetigt, und der Test belegte keinen
    # echten Konflikt zwischen zwei tatsaechlich bestaetigten Werten mehr.
    quelle = [
        treffer("https://www.auto-motor-und-sport.de/lexus-rc-f-a", "Lexus RC F USC10",
                "Der Lexus RC F USC10, Chassiscode dieser Baureihe, wird seit 2014 gebaut."),
        treffer("https://www.adac.de/lexus-rc-f-b", "Lexus RC F",
                "Der Lexus RC F (XYZ99), interne Bezeichnung der aktuellen Baureihe, wird "
                "seit 2014 gebaut und ersetzt das Vorgaengermodell."),
    ]
    ident, abgelehnt, _ = await _ident("Lexus", "RC F USC10", 2017, "5.0 V8", quelle)
    check("J) widersprechende, JEWEILS fuer sich belegte Codes (USC10 vs. XYZ99) "
          "-> KEIN einfach akzeptierter Wert (echter Konflikt, nicht nur beide "
          "unbestaetigt)", ident.generation is None, f"generation={ident.generation!r}")


async def _run_all():
    await test_b_core_match_ohne_code()
    await test_c_code_ohne_kontext_bleibt_unbestaetigt()
    await test_c2_code_mit_kontext_bestaetigt()
    await test_c3_modellcode_als_rollenbeleg()
    await test_c4_modellcode_bindet_nur_eigenen_code()
    await test_c5_motorcode_neben_interne_bezeichnung_kein_leak()
    await test_c6_marketingbezeichnung_kein_rollenbeleg()
    await test_d_core_akzeptiert_code_offen()
    await test_d2_code_ohne_kontext_bmw()
    await test_e_code_bestaetigt_bmw()
    await test_f_mercedes()
    await test_g_audi()
    await test_j_expliziter_code_widerspruch()
    await test_m_911_gt3_kein_blind_confirm()
    await test_n_golf_r32_kein_blind_confirm()
    await test_o_911_992_numerisch()
    await test_s_fehlender_auxiliary_kein_modell_fehlt()
    await test_t_mehrteiliges_modell_nicht_zerlegt()


asyncio.run(_run_all())

print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE IDENTITY-MODEL-CODE-SPLIT-TESTS GRUEN")
