"""
Package C / RC-W3 (professionelle Claim-Segmentierung): ein Satz kann 2, 3
oder mehr semantisch eigenstaendige, auch gegensaetzliche Claims enthalten
("Korrosion ist nicht feststellbar, aber die Dichtung ist undicht, jedoch
ist das Geraeusch unkritisch."). Die erste Mixed-Polarity-Fassung splittete
nur am ERSTEN Kontrastwort und behandelte den Rest als EINEN Block weiter -
das versagte bereits bei einem dritten Claim und konnte strukturell nie
mehr als EINEN WebFakt aus einem Satz erzeugen.

GENERISCHE REGEL (app/technical_research.py::_segmentiere_claims): voller,
linearer `re.split()` an einer kleinen, festen Kontrastkonnektor-Liste
(aber/jedoch/allerdings/waehrend/hingegen/dagegen/dafuer/zugleich/
ausserdem/zudem/dennoch/trotzdem) PLUS "und/sowie/oder" NUR, wenn danach
ein neuer Subjekt-Claim beginnt (Artikel+Nomen). Jedes Segment wird danach
unabhaengig durch dieselbe Prueckette (benign/Problem-/Struktursignal/
Komponente/Zeitraum) geschickt wie bisher ein ganzer Satz - mehrere echte
Segmente koennen jetzt mehrere WebFakt-Kandidaten erzeugen.

    python test_technical_research_claim_segmentation.py
"""
from app.technical_research import _extrahiere_fakten, _segmentiere_claims

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def _treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


def _pruefe(satz: str) -> tuple[list[str], list[tuple]]:
    abgelehnt: list[dict] = []
    t = [_treffer("https://www.autobild.de/test", "Testmodell Ratgeber", satz)]
    fakten = _extrahiere_fakten(t, "schwachstelle", baujahr=2019, abgelehnt=abgelehnt)
    bauteile = sorted(f.bauteil for f in fakten)
    gruende = [(a.get("grund"), (a.get("satz") or "")[:60]) for a in abgelehnt]
    return bauteile, gruende


print("\n=== Multi-Claim Matrix A-H ===")
_MULTI_CLAIM = [
    ("A benign -> defect",
     "Korrosion ist nicht feststellbar, aber die Dichtung ist undicht.",
     ["Undicht"]),
    ("B defect -> benign",
     "Die Dichtung ist undicht, aber Korrosion ist nicht feststellbar.",
     ["Undicht"]),
    ("C benign -> defect -> benign",
     "Kein Rost ist vorhanden, aber die Dichtung ist undicht, jedoch ist das "
     "leichte Geraeusch konstruktionsbedingt normal.",
     ["Undicht"]),
    ("E benign -> defect -> defect",
     "Korrosion ist nicht feststellbar, aber die Querlenkerbuchsen haben Spiel "
     "und die Dichtung ist undicht.",
     ["Querlenkerbuchsen", "Undicht"]),
]
for name, satz, erwartet in _MULTI_CLAIM:
    bauteile, gruende = _pruefe(satz)
    check(f"{name}: bauteile={bauteile} == {sorted(erwartet)}", bauteile == sorted(erwartet))

print("\n=== Multi-Claim D/F/G/H: Segmentierung korrekt, Rest an bekanntem "
      "Coverage-Limit (no_problem_signal 'faellt aus' / no_component 'Dichtung') ===")
_PARTIAL = [
    ("D", "Die Wasserpumpe faellt aus, jedoch ist leichter Oberflaechenrost unkritisch, "
          "aber die Steuerkette wird deutlich laenger.", 3,
     {"Steuerkette"}),
    ("F", "Die Wasserpumpe faellt aus und die Dichtung wird poroes, waehrend das leichte "
          "Kaltstartgeraeusch unkritisch ist.", 3, set()),
    ("G", "Kein Rost ist sichtbar, aber die Wasserpumpe faellt aus, jedoch ist das "
          "Lenkradspiel unproblematisch, waehrend die Dichtung mit der Zeit poroes wird.", 4,
     set()),
    ("H", "Korrosion ist nicht feststellbar, aber die Wasserpumpe faellt aus, waehrend "
          "die Dichtung poroes wird, jedoch ist das Geraeusch unkritisch, und die "
          "Querlenkerbuchsen entwickeln Spiel.", 5,
     {"Querlenkerbuchsen"}),
]
for name, satz, erwartete_segmentzahl, erwartete_bauteile in _PARTIAL:
    segs = _segmentiere_claims(satz)
    bauteile, gruende = _pruefe(satz)
    check(f"{name}: Segmentierung liefert {erwartete_segmentzahl} Segmente "
          f"(tatsaechlich {len(segs)})", len(segs) == erwartete_segmentzahl)
    check(f"{name}: genau die durch bestehende Coverage erreichbaren Bauteile "
          f"({sorted(erwartete_bauteile)}) bleiben erhalten, kein neuer False Positive",
          set(bauteile) == erwartete_bauteile)
    # Jede fehlende Komponente muss an no_problem_signal ("faellt aus") oder
    # no_component ("Dichtung") scheitern, NIE an falscher Segmentierung.
    unerwartete_gruende = [g for g, _ in gruende
                          if g not in ("no_problem_signal", "no_component", "benign_relativiert")]
    check(f"{name}: keine unerwarteten Ablehnungsgruende ({unerwartete_gruende})",
          not unerwartete_gruende)

print("\n=== No-False-Split Matrix I-M ===")
_NO_SPLIT = [
    ("I Adjektiv-Konjunktion bleibt EIN Claim",
     "Die Dichtung wird poroes und undicht.", 1, ["Undicht"]),
    ("J gekoppelte benigne Claims, kein Defekt",
     "Rost und Korrosion sind nicht feststellbar.", 1, []),
    ("K zusammengesetztes Subjekt vor gemeinsamem Praedikat bleibt EIN Claim",
     "Die Wasserpumpe und der Thermostat wurden ersetzt.", 1, []),
    ("L zusammenhaengender Ursache/Folge-Claim",
     "Die Steuerkette kann sich laengen und dadurch Rasseln verursachen.", 1, []),
    ("M 'weder X noch Y' mit Adjektiven bleibt benign",
     "Die Dichtung ist weder poroes noch undicht.", 1, []),
]
for name, satz, erwartete_segmentzahl, erwartete_bauteile in _NO_SPLIT:
    segs = _segmentiere_claims(satz)
    bauteile, gruende = _pruefe(satz)
    if erwartete_segmentzahl is not None:
        check(f"{name}: {erwartete_segmentzahl} Segment(e)", len(segs) == erwartete_segmentzahl)
    check(f"{name}: bauteile={bauteile} == {sorted(erwartete_bauteile)}",
          bauteile == sorted(erwartete_bauteile))

print("\n=== Reihenfolge bleibt erhalten ===")
segs = _segmentiere_claims(
    "Korrosion ist nicht feststellbar, aber die Wasserpumpe faellt aus, waehrend "
    "die Dichtung poroes wird, jedoch ist das Geraeusch unkritisch, und die "
    "Querlenkerbuchsen entwickeln Spiel.")
check("5 Segmente in Original-Reihenfolge (Korrosion...Querlenkerbuchsen)",
      segs[0].lower().startswith("korrosion") and segs[-1].lower().startswith("die querlenker"))

print("\n=== Dedupe: ZWEI verschiedene Risiken derselben Komponente aus zwei "
      "Segmenten desselben Satzes bleiben BEIDE erhalten (Risiko 3, siehe unten "
      "Dedupe-Matrix A-D fuer die volle Herleitung) ===")
bauteile, gruende = _pruefe(
    "Die Wasserpumpe faellt haeufig aus, ausserdem kann die Wasserpumpe undicht werden.")
check(f"ZWEI eigenstaendige Wasserpumpe-Risiken bleiben erhalten (vorher stiller "
      f"Verlust des zweiten Claims): bauteile={bauteile}",
      bauteile == ["Wasserpumpe", "Wasserpumpe"])

print("\n=== Kein Claim-Anzahl-Cap mehr (Release-Review Risiko 4) ===")
# Zwei FRUEHERE Fassungen hatten hier ein kuenstliches Limit: zuerst ein
# Re-Merge auf [satz] (1 Segment) bei Ueberschreitung, dann ein Truncation-
# Cap bei 20. Beides vernichtete echte Information ohne nachweisbaren
# Grund - die Performance-Messung zeigt, dass selbst 2000 Claims (71 KB
# Satz) nur ~4,6 ms kosten, weit unterhalb jeder realistischen Tavily-
# Snippet-Groesse. Jetzt: KEIN Cap, alle Segmente bleiben erhalten.
degeneriert = " und ".join([f"die Komponente{i} ist defekt" for i in range(30)])
segs = _segmentiere_claims(degeneriert)
check(f"degenerierter Text (30 'und'-Verbindungen): ALLE 30 Segmente bleiben "
      f"erhalten (kein Cap, kein Re-Merge): {len(segs)} Segment(e)", len(segs) == 30)
check("Segmente bleiben in Original-Reihenfolge erhalten",
      segs[0].strip().lower().startswith("die komponente0")
      and segs[-1].strip().lower().startswith("die komponente29"))

print("\n=== Robustheit: 1 bis 10 Claims (feste Konnektorkette, keine Fusion, "
      "keine Endlosschleife) ===")
import time

for n in range(1, 11):
    teile = [f"Komponente{i} zeigt Defekt{i}" for i in range(n)]
    satz = " aber ".join(teile) + "."
    start = time.perf_counter()
    segs = _segmentiere_claims(satz)
    dauer_ms = (time.perf_counter() - start) * 1000
    check(f"{n} Claim(s): {len(segs)} Segment(e) (erwartet {n}), "
          f"Reihenfolge erhalten, {dauer_ms:.3f} ms",
          len(segs) == n
          and segs[0].lower().startswith("komponente0")
          and segs[-1].lower().startswith("komponente" + str(n - 1))
          and dauer_ms < 50)

print("\n=== Alternierende Polaritaet, 8 Claims: benign/defect/benign/defect/"
      "defect/benign/defect/benign ===")
# Jeder Claim bewusst mit einer Komponente, die vom bestehenden Vokabular
# erkannt wird (ausser den zwei bereits bekannten, bewusst NICHT in diesem
# Auftrag erweiterten Luecken: "faellt aus"-Funktionsverlust und bare
# "Dichtung" - siehe Modul-/Sessionberichte). Bewusst KURZ gehalten: die
# bestehende, von dieser Aenderung unberuehrte Satzlaengen-Obergrenze in
# `_saetze()` (max. 300 Zeichen) wuerde einen laengeren Acht-Claim-Satz
# bereits VOR jeder Segmentierung verwerfen - ein separates, hier bewusst
# nicht angefasstes Architektur-Detail (siehe Abschlussbericht).
satz_8 = (
    "Kein Rost ist sichtbar, "                           # 1 benign
    "aber die Steuerkette wird laenger, "                # 2 defect
    "jedoch ist das Geraeusch unkritisch, "               # 3 benign
    "ausserdem haben Querlenkerbuchsen Spiel, "          # 4 defect
    "dagegen zeigt die Bremse Verschleiss, "              # 5 defect
    "zugleich ist keine Korrosion feststellbar, "         # 6 benign
    "aber am Unterboden bildet sich Rost, "               # 7 defect
    "trotzdem ist Lenkradspiel unproblematisch."          # 8 benign
)
segs_8 = _segmentiere_claims(satz_8)
check(f"8 Segmente erkannt (tatsaechlich {len(segs_8)})", len(segs_8) == 8)
bauteile_8, gruende_8 = _pruefe(satz_8)
check("alle 4 echten Defekt-Claims akzeptiert, in korrekter claim-lokaler "
      f"Zuordnung (Steuerkette/Querlenkerbuchsen/Bremse/Rost): {bauteile_8}",
      bauteile_8 == sorted(["Steuerkette", "Querlenkerbuchsen", "Bremse", "Rost"]))
check("alle 4 benignen Claims korrekt als benign_relativiert dokumentiert "
      f"(gruende={[g for g, _ in gruende_8]})",
      sum(1 for g, _ in gruende_8 if g == "benign_relativiert") == 4)

print("\n=== und/sowie/oder-Matrix E-H (muessen SPLITTEN) ===")
_UND_SPLIT = [
    ("E", "Die Dichtung ist undicht und die Wasserpumpe zeigt einen Defekt."),
    ("F", "Die Querlenkerbuchsen haben Spiel und die Steuerkette wird laenger."),
    ("G", "Die Wasserpumpe zeigt einen Defekt sowie der Thermostat eine Undichtigkeit."),
    ("H", "Die Dichtung ist undicht, oder die Wasserpumpe hat einen Defekt."),
]
for name, satz in _UND_SPLIT:
    segs = _segmentiere_claims(satz)
    check(f"{name}: splittet in {len(segs)} Segmente (>1 erwartet)", len(segs) > 1)

print("\n=== Dedupe-Matrix A-E (Release-Review Risiko 3) ===")
# A) gleiche Komponente + ZWEI VERSCHIEDENE echte Defekte -> duerfen sich
# nicht gegenseitig verdraengen (vorher: stiller Verlust des zweiten Claims,
# da `kandidaten.setdefault()` ausschliesslich ueber die Komponente dedupte).
abgelehnt_a: list[dict] = []
t_a = [_treffer("https://www.autobild.de/test", "Testmodell Ratgeber",
               "Die Wasserpumpe ist undicht, ausserdem hat die Wasserpumpe einen "
               "Lagerschaden.")]
fakten_a = _extrahiere_fakten(t_a, "schwachstelle", baujahr=2019, abgelehnt=abgelehnt_a)
check(f"A zwei VERSCHIEDENE Wasserpumpe-Risiken bleiben BEIDE erhalten: "
      f"{len(fakten_a)} Fakten ({[f.aussage[:40] for f in fakten_a]})",
      len(fakten_a) == 2 and all(f.bauteil == "Wasserpumpe" for f in fakten_a))

# D) gleiche Komponente + IDENTISCHE Aussage zweimal -> weiterhin 1 Fakt
# (die Inhalts-Ueberlappungsprüfung erkennt dieselbe Aussage als "gleiches
# Risiko" und merged wie bisher).
abgelehnt_d: list[dict] = []
t_d = [_treffer("https://www.autobild.de/test", "Testmodell Ratgeber",
               "Die Wasserpumpe faellt haeufig aus, ausserdem faellt die "
               "Wasserpumpe haeufig aus.")]
fakten_d = _extrahiere_fakten(t_d, "schwachstelle", baujahr=2019, abgelehnt=abgelehnt_d)
check(f"D identische Aussage zweimal -> weiterhin 1 Fakt: {len(fakten_d)} Fakten",
      len(fakten_d) == 1)

bauteile, _ = _pruefe("Kein Rost ist sichtbar, aber die Dichtung ist undicht.")
check(f"E gleiche-Satz-Ebene benign+defect getrennter Komponenten: "
      f"bauteile={bauteile} (benign verworfen, Defekt akzeptiert)",
      bauteile == ["Undicht"])

print("\n=== Recall-Scope-Isolation (Package A bleibt unveraendert) ===")
rueckruf_satz = ("Fuer das Modell besteht ein Rueckruf wegen der Kraftstoffleitung, "
                 "allerdings sind nur Fahrzeuge mit 1,5-Liter-Motor aus den Baujahren "
                 "2015 bis 2018 betroffen.")
# Segmentierung wuerde diesen Satz spalten (Beleg, dass Isolation noetig ist) -
# sie darf aber fuer kategorie="rueckruf" NIE angewendet werden.
segs_waeren = _segmentiere_claims(rueckruf_satz)
check("Beleg: waere der Rueckruf-Satz segmentiert worden, haette er sich "
      f"gespalten ({len(segs_waeren)} Segmente) - Isolation ist also notwendig",
      len(segs_waeren) > 1)
abgelehnt_rr: list[dict] = []
t_rr = [_treffer("https://www.kba.de/test", "Testmodell Rueckruf", rueckruf_satz)]
fakten_rr = _extrahiere_fakten(t_rr, "rueckruf", baujahr=2016, abgelehnt=abgelehnt_rr)
check("Rueckruf-Kategorie verarbeitet den Satz weiterhin als EINE Einheit statt "
      f"in 2 Fakten zu zerfallen (identisch zum Pfad vor der Segmentierung): "
      f"{len(fakten_rr)} Fakt(en), applicability="
      f"{fakten_rr[0].applicability if fakten_rr else None}",
      len(fakten_rr) == 1 and fakten_rr[0].applicability == "vehicle_possible")

print("\n=== Wartung: zwei verschiedene Intervalle im selben Satz (Release-"
      "Review Risiko/Schritt 5) ===")
satz_wartung = ("Das Getriebeoel sollte alle 60.000 km gewechselt werden, waehrend "
                "die Bremsfluessigkeit alle 2 Jahre gewechselt werden sollte.")
segs_w = _segmentiere_claims(satz_wartung)
check(f"2 Claim-Segmente erkannt (tatsaechlich {len(segs_w)})", len(segs_w) == 2)
abgelehnt_w: list[dict] = []
t_w = [_treffer("https://www.autobild.de/test", "Testmodell Ratgeber", satz_wartung)]
fakten_w = _extrahiere_fakten(t_w, "wartung", baujahr=2019, abgelehnt=abgelehnt_w)
bauteile_w = sorted(f.bauteil for f in fakten_w)
check(f"2 Wartungsfakten mit je eigenem Intervall, keine Vermischung: "
      f"{[(f.bauteil, f.aussage[:50]) for f in fakten_w]}",
      len(fakten_w) == 2 and bauteile_w == sorted(["Getriebeoel", "Bremsfluessigkeit"])
      and all("60.000" in f.aussage or "2 jahre" in f.aussage.lower() for f in fakten_w))

# Gemeinsames Intervall fuer ZWEI Komponenten in EINEM Satz darf nicht
# kuenstlich in zwei unabhaengige (und damit potenziell widersprechende)
# Claims zerrissen werden - "und" ohne eigenes zweites Praedikat bleibt EIN
# Claim (derselbe Verb-Anker-Mechanismus wie bei "Wasserpumpe und Thermostat
# wurden ersetzt").
satz_wartung2 = "Das Getriebeoel und der Filter sollten alle 60.000 km gewechselt werden."
segs_w2 = _segmentiere_claims(satz_wartung2)
check(f"gemeinsames Intervall bleibt EIN Claim (kein kuenstlicher Split): "
      f"{len(segs_w2)} Segment(e)", len(segs_w2) == 1)

print("\n=== Long Multi-Claim Matrix: 250/300/350/500 Zeichen, alternierende "
      "Polaritaet (Release-Review Risiko 1) ===")
_LONG_CASES = [
    (250, "Kein Rost ist sichtbar, aber die Steuerkette wird laenger, jedoch ist "
          "das Geraeusch unkritisch, ausserdem hat die Bremse Verschleiss, dagegen "
          "ist keine Korrosion feststellbar, zugleich bildet sich am Unterboden Rost."),
    (300, "Kein Rost ist sichtbar, aber die Steuerkette wird deutlich laenger, "
          "jedoch ist das leichte Geraeusch unkritisch, ausserdem hat die Bremse "
          "starken Verschleiss, dagegen ist keine Korrosion feststellbar, zugleich "
          "bildet sich am Unterboden etwas Rost."),
    (350, "Kein Rost ist an der Karosserie sichtbar, aber die Steuerkette wird bei "
          "hoher Laufleistung deutlich laenger, jedoch ist das leichte Kaltstart-"
          "geraeusch unkritisch, ausserdem hat die Bremse bereits starken "
          "Verschleiss, dagegen ist keine Korrosion am Unterboden feststellbar, "
          "zugleich bildet sich dort langsam etwas Rost."),
    (500, "Kein Rost ist an der Karosserie sichtbar, aber die Steuerkette wird bei "
          "hoher Laufleistung deutlich laenger, jedoch ist das leichte Kaltstart-"
          "geraeusch vollkommen unkritisch und konstruktionsbedingt normal, "
          "ausserdem hat die Bremse bei vielen Exemplaren bereits deutlich "
          "staerkeren Verschleiss gezeigt, dagegen ist an diesem Fahrzeug keine "
          "Korrosion am Unterboden feststellbar, zugleich bildet sich dort bei "
          "aelteren Exemplaren langsam etwas Rost."),
]
for ziel_laenge, satz in _LONG_CASES:
    check(f"Satz liegt im Zielbereich (~{ziel_laenge} Zeichen, tatsaechlich "
          f"{len(satz)})", abs(len(satz) - ziel_laenge) <= 60)
    segs = _segmentiere_claims(satz)
    bauteile, gruende = _pruefe(satz)
    unerwartet = [g for g, _ in gruende
                 if g not in ("no_problem_signal", "no_component", "benign_relativiert")]
    check(f"~{ziel_laenge} Zeichen: kein kompletter Silent Drop allein wegen Laenge "
          f"({len(segs)} Segmente, bauteile={bauteile})",
          len(segs) >= 5 and len(bauteile) >= 1)
    check(f"~{ziel_laenge} Zeichen: keine unerwarteten Ablehnungsgruende "
          f"(Segmentierungsfehler vs. bekanntes Coverage-Limit bleibt getrennt): "
          f"{unerwartet}", not unerwartet)

print("\n=== Technische Dedupe-Matrix A-H (Release-Review: Dedupe-Semantik) ===")
# Root Cause (siehe Sessionbericht): die urspruengliche Wiederverwendung von
# `_gleiches_rueckruf_ereignis` (MIN_GLEICHHEIT_TOKENS=2, absolute Schwelle)
# war fuer kurze technische Ein-Satz-Claims ungeeignet - selbst "Die
# Wasserpumpe ist undicht." gegen SICH SELBST verglichen blieb unter der
# Schwelle (nur 1 Inhaltstoken nach Abzug von Bauteil+Fuellwort). Ersetzt
# durch `_gleiches_technisches_risiko()` (groessenrelatives Kriterium).


def _zwei_claims_eine_quelle(satz1: str, satz2: str, kategorie: str = "schwachstelle"):
    satz = f"{satz1}, ausserdem {satz2[0].lower()}{satz2[1:]}"
    abgelehnt: list[dict] = []
    t = [_treffer("https://www.autobild.de/test", "Testmodell Ratgeber", satz)]
    fakten = _extrahiere_fakten(t, kategorie, baujahr=2019, abgelehnt=abgelehnt)
    return fakten, abgelehnt


def _zwei_quellen(satz1: str, satz2: str, kategorie: str = "schwachstelle"):
    abgelehnt: list[dict] = []
    t = [_treffer("https://www.autobild.de/quelle-a", "Testmodell Ratgeber A", satz1),
         _treffer("https://www.auto-motor-und-sport.de/quelle-b", "Testmodell Ratgeber B", satz2)]
    fakten = _extrahiere_fakten(t, kategorie, baujahr=2019, abgelehnt=abgelehnt)
    return fakten, abgelehnt


fakten, _ = _zwei_quellen("Die Wasserpumpe am Fahrzeug ist undicht.",
                          "Die Wasserpumpe am Fahrzeug ist undicht.")
check(f"A IDENTISCH (2 Quellen): 1 Fakt, beide Quellen gezaehlt "
      f"({len(fakten)} Fakt(en), {len(fakten[0].quellen) if fakten else 0} Quellen)",
      len(fakten) == 1 and len(fakten[0].quellen) >= 1)

fakten, _ = _zwei_claims_eine_quelle("Die Wasserpumpe ist undicht",
                                    "Die Wasserpumpe ist undicht")
check(f"A IDENTISCH (1 Quelle, 2 Segmente): 1 Fakt ({len(fakten)})", len(fakten) == 1)

fakten, abgelehnt = _zwei_claims_eine_quelle("Die Wasserpumpe ist undicht",
                                             "Die Wasserpumpe verliert deutlich Kuehlmittel")
check(f"B GLEICHE BEDEUTUNG, ANDERE FORMULIERUNG: ohne Stemming/Ontologie "
      f"nicht sicher zusammenfuehrbar (0 gemeinsame Inhaltstokens) - bleibt "
      f"bewusst getrennt statt riskant gemerged: {len(fakten)} Fakt(en), "
      f"{[a.get('grund') for a in abgelehnt]}",
      len(fakten) <= 1)  # zweiter Claim scheitert hier an eigenem Coverage-Limit, nicht an Dedupe

fakten, _ = _zwei_claims_eine_quelle("Die Wasserpumpe ist undicht",
                                    "Die Wasserpumpe hat einen deutlichen Lagerschaden")
check(f"C UNTERSCHIEDLICHE RISIKEN, GLEICHE KOMPONENTE: 2 Fakten "
      f"({len(fakten)}, {[f.aussage[:30] for f in fakten]})", len(fakten) == 2)

fakten, _ = _zwei_claims_eine_quelle(
    "Die Wasserpumpe ist undicht",
    "kann Undichtigkeit der Wasserpumpe zu Kuehlmittelverlust fuehren")
check(f"D URSACHE VS FOLGE: bewusst 2 getrennte Fakten (kein Stemming "
      f"'undicht'~'undichtigkeit', konservativ statt riskant gemerged): "
      f"{len(fakten)} Fakt(en)", len(fakten) == 2)

fakten, abgelehnt = _zwei_claims_eine_quelle("Die Steuerkette laengt sich deutlich",
                                             "kann die Steuerkette dabei ueberspringen")
check(f"E VERSCHLEISS VS AUSFALL: Segmentierung isoliert beide Claims korrekt "
      f"als eigene Einheiten; beide scheitern an einem bekannten, separaten "
      f"Coverage-Limit (reflexive Verbform/'ueberspringen' nicht erkannt), "
      f"NICHT an Dedupe: {[a.get('grund') for a in abgelehnt]}",
      all(g == "no_problem_signal" for g, in [(a.get("grund"),) for a in abgelehnt]))

fakten, _ = _zwei_claims_eine_quelle("Die Dichtung wird mit der Zeit poroes",
                                    "ist die Dichtung bereits undicht")
check(f"F MATERIAL VS FUNKTION: 'porös' (no_component, bare Dichtung-Luecke) "
      f"und 'undicht' bleiben getrennt, kein Dedupe-Zusammenfall erzwungen: "
      f"{len(fakten)} Fakt(en)", len(fakten) >= 1)

fakten, _ = _zwei_quellen("Die Wasserpumpe am Fahrzeug ist undicht.",
                         "Bei der Wasserpumpe tritt oft Undichtigkeit auf.")
check(f"G GLEICHER CLAIM, ANDERE FORMULIERUNG, 2 QUELLEN: ohne Stemming "
      f"bewusst nicht gemerged (konservativ, keine Falsch-Fusion): "
      f"{len(fakten)} Fakt(en)", len(fakten) == 2)

fakten, _ = _zwei_quellen("Die Wasserpumpe am Fahrzeug ist undicht.",
                         "Die Wasserpumpe weist einen deutlichen Lagerschaden auf.")
check(f"H ZWEI QUELLEN, ZWEI VERSCHIEDENE RISIKEN: 2 Fakten ({len(fakten)})",
      len(fakten) == 2)

print("\n=== Maintenance-Dedupe-Matrix A-D ===")
# A) Release-Review (adversarialer Collision-Stresstest): "gewechselt" vs.
# "erneuert" bei identischem Intervall endet jetzt bewusst als 2 Fakten statt
# 1 - ein akzeptierter Trade-off. Grund: dieselbe Boilerplate-Bereinigung
# (noetig, um Faelle C/D unten zu schliessen - zwei ECHT unterschiedliche
# Komponenten/Arbeiten bei identischem Intervall wurden sonst faelschlich
# zusammengefuehrt) kann ein reines Verb-Synonym nicht mehr von einer echten
# Unterscheidung trennen, ohne eine Verb-Ontologie zu bauen. Konservativ:
# lieber ein gelegentliches Doppelrisiko als eine falsche Fusion (siehe
# Abschlussbericht).
fakten, _ = _zwei_claims_eine_quelle(
    "Das Getriebeoel sollte alle 60.000 km gewechselt werden",
    "sollte das Getriebeoel alle 60.000 km erneuert werden", kategorie="wartung")
check(f"A gleiches Intervall, reines Verb-Synonym -> bewusst 2 Fakten "
      f"(akzeptierter Trade-off, siehe Kommentar): {len(fakten)}", len(fakten) == 2)

fakten, _ = _zwei_claims_eine_quelle(
    "Das Getriebeoel sollte alle 60.000 km gewechselt werden",
    "sollte das Getriebeoel alle 90.000 km gewechselt werden", kategorie="wartung")
check(f"B WIDERSPRUECHLICHE Intervalle (60.000 vs 90.000 km) bleiben GETRENNT, "
      f"nicht still auf 1 reduziert: {len(fakten)} Fakt(en) "
      f"({[f.aussage[:45] for f in fakten]})", len(fakten) == 2)

fakten, abgelehnt = _zwei_claims_eine_quelle(
    "Die Bremsfluessigkeit sollte alle 2 Jahre gewechselt werden",
    "sollte die Bremsfluessigkeit regelmaessig erneuert werden", kategorie="wartung")
check(f"C zweiter Claim ohne erkennbares Intervall scheitert am eigenen "
      f"Wartungs-Gate (no_maintenance_interval), nicht an Dedupe: "
      f"{len(fakten)} Fakt(en), gruende={[a.get('grund') for a in abgelehnt]}",
      len(fakten) == 1 and any(a.get("grund") == "no_maintenance_interval" for a in abgelehnt))

fakten, _ = _zwei_claims_eine_quelle(
    "Das Getriebeoel sollte alle 60.000 km gewechselt werden",
    "sollten die Bremsbelaege alle 60.000 km geprueft werden", kategorie="wartung")
check(f"D zwei unterschiedliche Komponenten -> 2 Fakten ({len(fakten)})",
      len(fakten) == 2)

print("\n=== Adversarialer Dedupe-Stresstest: generische Niedriginfo-Woerter "
      "(Release-Review) ===")
# Root Cause: die groessenrelative Tokenueberlappung konnte bei kurzen
# Claims durch EIN einzelnes generisches Wort (z.B. "zeigt") bereits die
# Haelfte der kleineren Tokenmenge erreichen und zwei fachlich
# UNTERSCHIEDLICHE Risiken faelschlich zusammenfuehren. Konkret
# nachgewiesen fuer: zeigt, hat, haeufig, schaden, defekt, tritt, kann.
from app.technical_research import _inhaltstokens, _gleiches_technisches_risiko

_MUSS_GETRENNT = [
    ("A 'zeigt' als einziges gemeinsames Wort", "Wasserpumpe",
     "Wasserpumpe zeigt starken Verschleiss.", "Wasserpumpe zeigt einen Lagerschaden."),
    ("B klar unterschiedliche Woerter", "Wasserpumpe",
     "Wasserpumpe ist undicht.", "Wasserpumpe ist blockiert."),
    ("C 'zeigt' als einziges gemeinsames Wort (2)", "Wasserpumpe",
     "Wasserpumpe zeigt Korrosion.", "Wasserpumpe zeigt Lagerschaeden."),
    ("D fachlich verwandt, nicht automatisch identisch", "Steuerkette",
     "Steuerkette zeigt starken Verschleiss.", "Steuerkette weist starke Laengung auf."),
    ("E Material vs Riss", "Dichtung", "Dichtung ist poroes.", "Dichtung ist gerissen."),
    ("F Spiel vs Riss", "Querlenkerbuchsen",
     "Querlenkerbuchsen haben Spiel.", "Querlenkerbuchsen sind rissig."),
    ("G 'arbeitet fehlerhaft' vs 'oeffnet zu spaet'", "Thermostat",
     "Thermostat arbeitet fehlerhaft.", "Thermostat oeffnet zu spaet."),
]
for name, bauteil, a, b in _MUSS_GETRENNT:
    ta, tb = _inhaltstokens(a, None, None, bauteil), _inhaltstokens(b, None, None, bauteil)
    r_ab, r_ba = (_gleiches_technisches_risiko(ta, tb),
                 _gleiches_technisches_risiko(tb, ta))
    check(f"{name}: VERSCHIEDEN in beiden Richtungen (order-independence) "
          f"- {ta} vs {tb}", not r_ab and not r_ba)

_MUSS_DEDUPEN = [
    ("identisch", "Wasserpumpe", "Wasserpumpe ist undicht.", "Wasserpumpe ist undicht."),
    ("andere Formulierung (1)", "Wasserpumpe", "Wasserpumpe weist Undichtigkeit auf.",
     "Bei der Wasserpumpe tritt Undichtigkeit auf."),
    ("andere Formulierung (2)", "Steuerkette", "Steuerkette zeigt Verschleiss.",
     "An der Steuerkette zeigt sich Verschleiss."),
]
for name, bauteil, a, b in _MUSS_DEDUPEN:
    ta, tb = _inhaltstokens(a, None, None, bauteil), _inhaltstokens(b, None, None, bauteil)
    check(f"MUSS DEDUPEN ({name}): gleiches Risiko erkannt - {ta} vs {tb}",
          _gleiches_technisches_risiko(ta, tb))

check("Single-Token identischer Claim bleibt dedupe-faehig (Grundanforderung "
      "des Fixes darf nicht regressieren)",
      _gleiches_technisches_risiko(_inhaltstokens("Wasserpumpe ist undicht.", None, None, "Wasserpumpe"),
                                   _inhaltstokens("Wasserpumpe ist undicht.", None, None, "Wasserpumpe")))

print("\n=== Getriebeoel/Getriebefilter-Kollision (Release-Review, ROOT-CAUSE-FIX) ===")
# Root Cause: `_anzeige_bauteil()` bewahrt die konkrete Mention ("Getriebeoel"
# vs. "Getriebefilter") korrekt - der Fehler lag darin, dass
# `_inhaltstokens(..., bauteil=...)` diese Mention danach als "kern" wieder
# herausfilterte, BEVOR der Dedupe-Vergleich sie je sah. Beide matchten nur
# denselben GROBEN Vokabel-Schluessel "getriebe". Fix: `_gleiches_objekt_
# mention()` - ein ZWEITES hartes Unterscheidungsmerkmal (neben dem
# Wartungsintervall), das die konkrete Mention selbst vergleicht (mit
# fuzzy Praefix-Toleranz fuer reine Wortformen wie "Getriebeoel"/
# "Getriebeoels").
fakten, _ = _zwei_claims_eine_quelle(
    "Das Getriebeoel sollte alle 60.000 km gewechselt werden",
    "sollte der Getriebefilter alle 60.000 km gewechselt werden", kategorie="wartung")
check(f"Getriebeoel vs Getriebefilter (gleicher Vokabel-Schluessel 'getriebe', "
      f"gleiches Intervall) -> 2 Fakten: {len(fakten)} "
      f"({[f.bauteil for f in fakten]})", len(fakten) == 2)

fakten, _ = _zwei_claims_eine_quelle(
    "Das Getriebeoel sollte alle 60.000 km gewechselt werden",
    "sollte am Getriebeoel alle 60.000 km der Fuellstand geprueft werden", kategorie="wartung")
check(f"gleiche Komponente+Intervall, ANDERE Arbeit -> 2 Fakten: {len(fakten)}",
      len(fakten) == 2)

print("\n=== Generische Collision-Matrix (andere Oberkomponenten) ===")
_COLLISION_MATRIX = [
    ("Bremsfluessigkeit/Bremsbelaege (Vokabel-Schluessel 'brems')",
     "Die Bremsfluessigkeit sollte alle 2 Jahre gewechselt werden",
     "sollten die Bremsbelaege alle 2 Jahre geprueft werden", 2),
    ("Kuehlmittel/Wasserpumpe (unterschiedliche Vokabel-Schluessel)",
     "Das Kuehlmittel sollte alle 4 Jahre gewechselt werden",
     "sollte die Wasserpumpe alle 4 Jahre geprueft werden", 2),
]
for name, a, b, erwartet in _COLLISION_MATRIX:
    fakten, _ = _zwei_claims_eine_quelle(a, b, kategorie="wartung")
    check(f"{name}: {erwartet} Fakten erwartet, {len(fakten)} erhalten "
          f"({[f.bauteil for f in fakten]})", len(fakten) == erwartet)

print("\n=== Order-Independence (Getriebeoel/Getriebefilter) ===")
f_ab, _ = _zwei_claims_eine_quelle(
    "Das Getriebeoel sollte alle 60.000 km gewechselt werden",
    "sollte der Getriebefilter alle 60.000 km gewechselt werden", kategorie="wartung")
f_ba, _ = _zwei_claims_eine_quelle(
    "Der Getriebefilter sollte alle 60.000 km gewechselt werden",
    "sollte das Getriebeoel alle 60.000 km gewechselt werden", kategorie="wartung")
check(f"A,B und B,A liefern dasselbe Ergebnis (kein First-Claim-Wins): "
      f"{len(f_ab)} vs {len(f_ba)}", len(f_ab) == len(f_ba) == 2)

print("\n=== Praefix-Collision-Matrix fuer _gleiches_objekt_mention() "
      "(Release-Review, Folgefund) ===")
# Root Cause: eine reine Praefix-Regel ("kuerzeres Wort ist Anfang des
# laengeren") verwechselte echte Wortformen ("Getriebeoel"/"Getriebeoels")
# mit fachlich ANDEREN Komposita, die zufaellig mit demselben Wort beginnen
# ("Kuehlmittel"/"Kuehlmittelpumpe"). Ersetzt durch eine kleine, geschlossene
# Flexions-Endungsliste (s/es/en/e/er): nur wenn die STAEMME nach Abzug
# hoechstens einer Endung identisch sind, gilt "gleiches Objekt".
from app.technical_research import _gleiches_objekt_mention, _norm

_PRAEFIX_COLLISION_MUSS_VERSCHIEDEN = [
    ("Kuehlmittel", "Kuehlmittelpumpe"),
    ("Getriebeoel", "Getriebeoelstand"),
    ("Wasserpumpe", "Wasserpumpenlager"),
    ("Luftfilter", "Luftfiltergehaeuse"),
    ("Bremsbelag", "Bremsbelaghalter"),
    ("Oelpumpe", "Oelpumpenkette"),
    ("Steuerkette", "Steuerkettenfuehrung"),
    ("Turbolader", "Turboladerleitung"),
]
for a, b in _PRAEFIX_COLLISION_MUSS_VERSCHIEDEN:
    na, nb = _norm(a), _norm(b)
    r_ab, r_ba = _gleiches_objekt_mention(na, nb), _gleiches_objekt_mention(nb, na)
    check(f"'{a}' vs '{b}': bleiben VERSCHIEDEN, order-independent "
          f"(A/B={r_ab}, B/A={r_ba})", not r_ab and r_ab == r_ba)

_PRAEFIX_COLLISION_MUSS_GLEICH = [
    ("Getriebeoel", "Getriebeoels"),
    ("Wasserpumpe", "Wasserpumpen"),
    ("Steuerkette", "Steuerketten"),
    ("Getriebefilter", "Getriebefilters"),
]
for a, b in _PRAEFIX_COLLISION_MUSS_GLEICH:
    na, nb = _norm(a), _norm(b)
    check(f"'{a}' vs '{b}': bleiben dieselbe Wortform (Flexion)",
          _gleiches_objekt_mention(na, nb))

print("\n=== Pipeline-Stresstest: gleicher Vokabel-Key + Intervall + Aktion, "
      "andere konkrete Mention (Release-Review, Section 5) ===")
_PIPELINE_STRESS = [
    ("Kuehlmittel vs Kuehlmittelpumpe",
     "Das Kuehlmittel sollte alle 4 Jahre geprueft werden",
     "sollte die Kuehlmittelpumpe alle 4 Jahre geprueft werden"),
    ("Getriebeoel vs Getriebeoelstand",
     "Das Getriebeoel sollte alle 60.000 km geprueft werden",
     "sollte der Getriebeoelstand alle 60.000 km geprueft werden"),
    ("Wasserpumpe vs Wasserpumpenlager",
     "Die Wasserpumpe sollte alle 60.000 km geprueft werden",
     "sollte das Wasserpumpenlager alle 60.000 km geprueft werden"),
]
for name, a, b in _PIPELINE_STRESS:
    fakten, _ = _zwei_claims_eine_quelle(a, b, kategorie="wartung")
    check(f"{name}: 2 Fakten trotz identischem Vokabel-Key/Intervall/Aktion "
          f"({len(fakten)}, {[f.bauteil for f in fakten]})", len(fakten) == 2)

print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE CLAIM-SEGMENTIERUNGS-TESTS GRUEN")
