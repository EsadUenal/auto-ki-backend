"""
RC-W4-Fix: lokale Modellkontext-Vererbung für schwache UND starke Ausrichtung.

BEFUND Runde 1 (Offline-Alignment-Matrix-Audit, Fälle C/D)
-------------------------------------------------------------
Nennt der TITEL eines Treffers das Modell nicht ("schwach" ausgerichtet),
musste bisher JEDER einzelne Satz das Modell selbst erneut nennen
(`_satz_nennt_modell`) — ein Folgesatz, der seinen Fahrzeugbezug nur aus dem
unmittelbar vorherigen Satz erbt ("Beim Golf ... . Die Wasserpumpe kann
undicht werden."), verlor diesen Bezug ersatzlos.

FIX Runde 1: ein Satz, der das Modell selbst nennt, etabliert lokalen Kontext
für nachfolgende Sätze DESSELBEN Treffers — zunächst nur für GENAU den
nächsten Satz (Distanz-1-TTL), zurückgesetzt durch einen Satz mit EXPLIZITEM
Widerspruch (fremde Marke, "beim <anderes Nomen>", unsichere Satzanfangs-
Nominalphrase).

BEFUND Runde 2 ("RC-W4 neu aufrollen")
---------------------------------------
Die Distanz-1-TTL war zu konservativ: ein zweiter neutraler Brückensatz
("Viele Fahrer berichten davon.") verwarf einen eigentlich klar zum selben
Fahrzeug gehörenden Folgesatz. FIX: die fixe Distanz entfällt für
schwachstelle/wartung (`_segment_kontext_saetze()`) — Kontext bleibt
bestehen, bis ein ECHTER inhaltlicher Widerspruch auftritt, unabhängig von
der Satzanzahl dazwischen. Zusätzlich gilt dieselbe Widerspruchsprüfung jetzt
auch für titel-stark ausgerichtete Treffer (ein expliziter Fremdfahrzeug-Satz
schlägt auch einen starken Titel-Kontext). "rueckruf" bleibt unverändert auf
der alten Distanz-1-TTL (`_distanz1_kontext_saetze()`, Recall/KBA nicht
angefasst).

    python test_technical_research_context_alignment.py
"""
import types

from app.technical_research import (_ziel, phase_fakten, ausgerichtet,
                                    _anderes_fahrzeug_lokal, _claim_widerspricht_fahrzeug_lokal,
                                    _unsichere_satzanfangs_nominalphrase)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def _treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


def _ident():
    return types.SimpleNamespace(generation=None)


def _fakten_und_gruende(treffer, marke, modell, *, generation=None, kategorie="schwachstelle"):
    z = _ziel(marke, modell, 2020, None, {"generation": generation} if generation else None)
    abgelehnt = []
    fakten = phase_fakten({kategorie: [treffer]}, z, _ident(), (kategorie,), abgelehnt)
    return fakten, abgelehnt


# Generischer "schwach" ausgerichteter Titel: nennt die Marke, nie ein
# konkretes Modell -- erzwingt grund="schwach" für alle folgenden Fixtures.
TITEL_SCHWACH = "VW Wartung und Pannenstatistik"

print("=" * 78)
print("RC-W4 Alignment-Matrix A-N (Ziel: VW Golf, falls nicht anders vermerkt)")
print("=" * 78)

# A) Modell im Claim selbst -> ACCEPT
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-wartung-a", TITEL_SCHWACH,
             "Beim Golf tritt die Wasserpumpe häufiger undicht auf."),
    "VW", "Golf")
check("A) Modell im Claim selbst -> ACCEPT",
      len(f) == 1 and f[0].bauteil == "Wasserpumpe", str([(x.bauteil, x.aussage) for x in f]))

# B) Modell NUR im Titel, Claim ohne Modell -> ACCEPT (unverändert, grund=None)
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-golf-b", "VW Golf Wartung",
             "Die Wasserpumpe kann undicht werden."),
    "VW", "Golf")
check("B) Modell nur im Titel -> ACCEPT (stark ausgerichtet, unverändert)",
      len(f) == 1 and f[0].bauteil == "Wasserpumpe")

# C) Modell unmittelbar vorher, Claim ohne Modell -> JETZT ACCEPT (Kern-Fix)
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-wartung-c", TITEL_SCHWACH,
             "Beim Golf gibt es einige bekannte Alterungserscheinungen. "
             "Die Wasserpumpe kann undicht werden."),
    "VW", "Golf")
check("C) Modell im vorherigen Satz, Claim selbst ohne Modell -> JETZT ACCEPT",
      any(x.bauteil == "Wasserpumpe" for x in f), str([(x.bauteil, x.aussage) for x in f]))

# D) Modell im vorherigen Satz, danach technischer Claim (3-Satz-Absatz) -> ACCEPT
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-wartung-d", TITEL_SCHWACH,
             "Es gibt verschiedene Fahrzeuge in der Kompaktklasse. "
             "Beim Golf wird ein Oelwechsel alle 30.000 km empfohlen. "
             "Der Zahnriemen sollte zusätzlich alle 60.000 km geprüft werden."),
    "VW", "Golf", kategorie="wartung")
check("D) Modell im vorherigen Satz -> Zahnriemen-Folgesatz wird akzeptiert",
      any(x.bauteil == "Zahnriemen" for x in f), str([(x.bauteil, x.aussage) for x in f]))

# E) nur Marke, kein Modell -> REJECT (modell_fehlt, Ergebnis-Ebene, unverändert)
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-e", "VW Wartung",
             "Volkswagen empfiehlt einen Ölwechsel alle 30.000 km."),
    "VW", "Golf")
check("E) nur Marke, kein Modell -> REJECT",
      len(f) == 0 and any(x.get("grund") == "modell_fehlt" for x in a))

# F) falsches Modell derselben Marke (Titel) -> REJECT (modell_fehlt, unverändert)
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-passat-f", "VW Passat Wartung",
             "Beim Passat wird ein Ölwechsel alle 30.000 km empfohlen."),
    "VW", "Golf")
check("F) falsches Modell derselben Marke -> REJECT",
      len(f) == 0 and any(x.get("grund") == "modell_fehlt" for x in a))

# G) Golf-Kontext -> Passat-Claim -> REJECT (lokaler Widerspruch, "beim Passat")
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-wartung-g", TITEL_SCHWACH,
             "Beim Golf ist der Ölstand regelmäßig zu prüfen. "
             "Beim Passat hingegen tritt häufiger ein Kupplungsschaden auf."),
    "VW", "Golf")
check("G) Golf-Kontext -> Passat-Claim -> REJECT",
      not any(x.bauteil == "Kupplung" for x in f),
      str([(x.bauteil, x.aussage) for x in f]))
check("G) Passat-Satz explizit als schwach_ausgerichtet_ohne_modellnennung abgelehnt",
      any("Kupplungsschaden" in (x.get("satz") or "") for x in a))

# H) Golf-Kontext -> Passat-Satz -> neutraler Folgesatz darf NICHT wieder Golf erben
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-wartung-h", TITEL_SCHWACH,
             "Beim Golf tritt eine undichte Wasserpumpe auf. "
             "Beim Passat ist hingegen die Steuerkette auffällig. "
             "Auch die Kupplung verschleißt früh."),
    "VW", "Golf")
bauteile = {x.bauteil for x in f}
check("H) Golf-Wasserpumpe bleibt erhalten",
      "Wasserpumpe" in bauteile, str(bauteile))
check("H) Passat-Steuerkette bleibt draußen (eigener Widerspruch)",
      "Steuerkette" not in bauteile, str(bauteile))
check("H) Kupplung (neutraler Folgesatz NACH dem Modellwechsel) erbt NICHT "
      "wieder den zurückgesetzten Golf-Kontext",
      "Kupplung" not in bauteile, str(bauteile))

# I) richtiger Titel, falsche Generation im Claim -> REJECT (andere_generation,
# Ergebnis-Ebene, unverändert -- RC-W6 nicht aufgeweicht)
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/mazda-mx5-nc-i", "Mazda MX-5 NC Wartung",
             "Der Zahnriemen sollte alle 100.000 km gewechselt werden."),
    "Mazda", "MX-5", generation="ND", kategorie="wartung")
check("I) richtiger Modellname, falsche Generation -> REJECT",
      len(f) == 0 and any(x.get("grund") == "andere_generation" for x in a))

# J) Herstellerseite allgemein, kein lokaler Modellkontext -> REJECT (modell_fehlt)
f, a = _fakten_und_gruende(
    _treffer("https://www.volkswagen.de/wartungsempfehlungen-j", "Volkswagen Wartungsempfehlungen",
             "Bei Elektrofahrzeugen sollte die Batterie regelmäßig geprüft werden."),
    "VW", "Golf")
check("J) generische Herstellerseite ohne lokalen Modellkontext -> REJECT",
      len(f) == 0 and any(x.get("grund") == "modell_fehlt" for x in a))

# K) Titel/Snippet mehrere Modelle -> Claim nur dem lokal genannten Modell zugeordnet
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-wartung-k", TITEL_SCHWACH,
             "Beim Golf kann die Wasserpumpe undicht werden. "
             "Beim Touran gibt es Probleme mit dem Fensterheber."),
    "VW", "Golf")
bauteile = {x.bauteil for x in f}
check("K) Golf-Wasserpumpe akzeptiert",
      "Wasserpumpe" in bauteile, str(bauteile))
check("K) Touran-Fensterheber NICHT dem Golf zugeordnet",
      "Fensterheber" not in bauteile, str(bauteile))

# L) fremde Marke lokal -> REJECT (bestehende _fremde_marke_satz-Logik)
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-wartung-l", TITEL_SCHWACH,
             "Beim Golf ist der Ölstand zu prüfen. "
             "Beim Ford Focus hingegen tritt ein Kupplungsschaden auf."),
    "VW", "Golf")
bauteile = {x.bauteil for x in f}
check("L) fremde Marke (Ford) lokal -> Kupplungsschaden NICHT dem Golf zugeordnet",
      "Kupplung" not in bauteile, str(bauteile))

# M) benigner Claim mit geerbtem Modellkontext -> weiterhin REJECT via RC-W3
f, a = _fakten_und_gruende(
    _treffer("https://www.autobild.de/vw-wartung-m", TITEL_SCHWACH,
             "Beim Golf zeigt sich gelegentlich folgendes Bild. "
             "Ein schief stehendes Lenkrad muss nicht unbedingt auf einen "
             "Unfallschaden hindeuten."),
    "VW", "Golf")
check("M) benigner Lenkrad-Claim mit geerbtem Golf-Kontext bleibt REJECT",
      not any(x.bauteil == "Lenkrad" for x in f))
check("M) explizit via benign_relativiert abgelehnt (RC-W3 greift NACH der "
      "RC-W4-Kontextvererbung weiterhin)",
      any(x.get("grund") == "benign_relativiert" for x in a))

# N) echter Problemclaim mit geerbtem Modellkontext -> ACCEPT
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-wartung-n", TITEL_SCHWACH,
             "Beim Golf treten mehrere typische Alterungsprobleme auf. "
             "Die Kupplung verschleißt bei Vielfahrern deutlich schneller als angegeben."),
    "VW", "Golf")
check("N) echter Problemclaim mit geerbtem Kontext -> ACCEPT",
      any(x.bauteil == "Kupplung" for x in f), str([(x.bauteil, x.aussage) for x in f]))

print("\n" + "=" * 78)
print("RC-W4 Runde 2: Segment-Kontext statt fixer Distanz-1-TTL (O-V)")
print("=" * 78)

# O) Golf-Satz -> DIREKT Wasserpumpen-Claim -> ACCEPT
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-wartung-o", TITEL_SCHWACH,
             "Beim Golf treten Alterungsprobleme auf. "
             "Die Wasserpumpe kann undicht werden."),
    "VW", "Golf")
check("O) direkt nächster Satz -> ACCEPT",
      any(x.bauteil == "Wasserpumpe" for x in f), str([(x.bauteil, x.aussage) for x in f]))

# P) Golf-Satz -> 1 neutraler Brückensatz -> Claim -> JETZT ACCEPT (RC-W4
# Runde 2: kein echter Kontextbruch, also bleibt das Segment aktiv -- die
# alte Distanz-1-TTL hätte das fälschlich verworfen, siehe Abschlussbericht)
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-wartung-p", TITEL_SCHWACH,
             "Beim Golf treten mehrere typische Alterungsprobleme auf. "
             "Besonders bei höheren Laufleistungen ist Vorsicht sinnvoll. "
             "Die Wasserpumpe kann undicht werden."),
    "VW", "Golf")
check("P) 1 neutraler Brückensatz, KEIN Kontextbruch -> ACCEPT (RC-W4 Runde 2)",
      any(x.bauteil == "Wasserpumpe" for x in f), str([(x.bauteil, x.aussage) for x in f]))

# Q) Golf-Satz -> 2+ neutrale Sätze, weiterhin KEIN echter Kontextbruch ->
# JETZT ACCEPT (RC-W4 Runde 2: Segment lebt beliebig lang, solange kein
# echter Widerspruch auftritt -- genau das war der Auftrag "keine fixe TTL")
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-wartung-q", TITEL_SCHWACH,
             "Beim Golf treten mehrere typische Alterungsprobleme auf. "
             "Besonders bei höheren Laufleistungen ist Vorsicht sinnvoll. "
             "Ein Gebrauchtwagenkauf sollte immer mit Probefahrt erfolgen. "
             "Die Wasserpumpe kann undicht werden."),
    "VW", "Golf")
check("Q) 3 neutrale Sätze, KEIN Kontextbruch -> ACCEPT (RC-W4 Runde 2)",
      any(x.bauteil == "Wasserpumpe" for x in f), str([(x.bauteil, x.aussage) for x in f]))

# R) Golf-Satz -> "Der Passat ..." -> neutraler Claim -> darf NICHT Golf erben
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-wartung-r", TITEL_SCHWACH,
             "Beim Golf treten mehrere typische Alterungsprobleme auf. "
             "Der Passat kostet in der Wartung tendenziell mehr. "
             "Die Wasserpumpe kann undicht werden."),
    "VW", "Golf")
check("R) 'Der Passat ...' als Brückensatz -> Wasserpumpe NICHT dem Golf zugeordnet "
      "(RC-W4 Runde 2: ohne TTL-Fallback -- einzig die Widerspruchserkennung selbst "
      "muss hier greifen, siehe _unsichere_satzanfangs_nominalphrase)",
      not any(x.bauteil == "Wasserpumpe" for x in f), str([(x.bauteil, x.aussage) for x in f]))

# S) Golf-Satz -> "Für den Passat ..." -> neutraler Claim -> darf NICHT Golf erben
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-wartung-s", TITEL_SCHWACH,
             "Beim Golf treten mehrere typische Alterungsprobleme auf. "
             "Für den Passat gilt eine abweichende Serviceempfehlung. "
             "Die Wasserpumpe kann undicht werden."),
    "VW", "Golf")
check("S) 'Für den Passat ...' als Brückensatz -> Wasserpumpe NICHT dem Golf zugeordnet",
      not any(x.bauteil == "Wasserpumpe" for x in f), str([(x.bauteil, x.aussage) for x in f]))
check("S) 'Für den Passat' wird direkt (Distanz 1) bereits aktiv als Widerspruch erkannt",
      _anderes_fahrzeug_lokal("Für den Passat gilt eine abweichende Serviceempfehlung.",
                             "Golf") == "passat")

# T) Golf-Satz -> fremde Marke -> neutraler Claim -> darf NICHT Golf erben
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-wartung-t", TITEL_SCHWACH,
             "Beim Golf treten mehrere typische Alterungsprobleme auf. "
             "Beim Ford Focus tritt dagegen häufiger ein Kupplungsschaden auf. "
             "Die Wasserpumpe kann undicht werden."),
    "VW", "Golf")
check("T) fremde Marke (Ford) direkt nach Golf-Satz (Distanz 1) -> Reset, "
      "Wasserpumpe NICHT dem Golf zugeordnet",
      not any(x.bauteil == "Wasserpumpe" for x in f), str([(x.bauteil, x.aussage) for x in f]))

# U) Golf-Satz -> mehrere neutrale Sätze, aber KEIN echter Themenwechsel
# (kein anderes Fahrzeug genannt) -> JETZT ACCEPT (RC-W4 Runde 2)
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-wartung-u", TITEL_SCHWACH,
             "Beim Golf treten mehrere typische Alterungsprobleme auf. "
             "Allgemein gilt: ein Gebrauchtwagenkauf will gut vorbereitet sein. "
             "Dazu gehört auch ein Blick ins Serviceheft. "
             "Die Wasserpumpe kann undicht werden."),
    "VW", "Golf")
check("U) mehrere neutrale Sätze, kein echter Kontextbruch -> ACCEPT (RC-W4 Runde 2)",
      any(x.bauteil == "Wasserpumpe" for x in f), str([(x.bauteil, x.aussage) for x in f]))

# V) Golf-Satz -> mehrere neutrale Sätze -> SPÄTER wieder explizit Golf ->
# neuer Kontext darf korrekt (fresh) starten
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-wartung-v", TITEL_SCHWACH,
             "Beim Golf treten mehrere typische Alterungsprobleme auf. "
             "Allgemein gilt: ein Gebrauchtwagenkauf will gut vorbereitet sein. "
             "Dazu gehört auch ein Blick ins Serviceheft. "
             "Beim Golf kann außerdem die Wasserpumpe undicht werden."),
    "VW", "Golf")
check("V) spätere erneute explizite Golf-Nennung startet frischen Kontext "
      "(Wasserpumpe jetzt im SELBEN Satz genannt -> ACCEPT)",
      any(x.bauteil == "Wasserpumpe" for x in f), str([(x.bauteil, x.aussage) for x in f]))

print("\n" + "=" * 78)
print("RC-W4 Review-Korrektur 2: bare 'Der/Die/Das X' an Distanz 1")
print("=" * 78)

# W) Golf-Kontext -> "Der Passat hat Probleme mit der Wasserpumpe." -> REJECT
# (exakt die geschlossene Distanz-1-Luecke: bare Konstruktion ohne "beim")
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-wartung-w", TITEL_SCHWACH,
             "Beim Golf treten typische Probleme auf. "
             "Der Passat hat häufig Probleme mit der Wasserpumpe."),
    "VW", "Golf")
check("W) 'Der Passat ...' an Distanz 1 -> REJECT, kein Golf-Erbe",
      len(f) == 0, str([(x.bauteil, x.aussage) for x in f]))
check("W) explizit via unsichere_nominalphrase_lokal (oder gleichwertig) abgelehnt",
      any("schwach_ausgerichtet_ohne_modellnennung" == x.get("grund") for x in a))

# X) Golf-Kontext -> "Die Wasserpumpe kann undicht werden." -> ACCEPT (Bauteil, kein Fahrzeug)
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-wartung-x", TITEL_SCHWACH,
             "Beim Golf treten typische Probleme auf. "
             "Die Wasserpumpe kann undicht werden."),
    "VW", "Golf")
check("X) 'Die Wasserpumpe kann undicht werden.' -> ACCEPT",
      any(x.bauteil == "Wasserpumpe" for x in f), str([(x.bauteil, x.aussage) for x in f]))

# Y) Golf-Kontext -> "Der Motor verschleißt ..." -> ACCEPT (Bauteil, kein Fahrzeug)
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-wartung-y", TITEL_SCHWACH,
             "Beim Golf treten typische Probleme auf. "
             "Der Motor verschleißt bei Vielfahrern deutlich schneller als angegeben."),
    "VW", "Golf")
check("Y) 'Der Motor verschleißt ...' -> ACCEPT",
      any(x.bauteil == "Motor" for x in f), str([(x.bauteil, x.aussage) for x in f]))

# Z) Golf-Kontext -> "Das Getriebe ruckelt." -> ACCEPT (Bauteil, kein Fahrzeug)
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-wartung-z", TITEL_SCHWACH,
             "Beim Golf treten typische Probleme auf. "
             "Das Getriebe neigt zu Defekten und macht sich durch Rucken bemerkbar."),
    "VW", "Golf")
check("Z) 'Das Getriebe ruckelt.' -> ACCEPT",
      any(x.bauteil == "Getriebe" for x in f), str([(x.bauteil, x.aussage) for x in f]))

# AA) Golf-Kontext -> "Der Tiguan zeigt erhöhten Verschleiß." -> REJECT
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-wartung-aa", TITEL_SCHWACH,
             "Beim Golf treten typische Probleme auf. "
             "Der Tiguan zeigt bei der Kupplung erhöhten Verschleiß."),
    "VW", "Golf")
check("AA) 'Der Tiguan ...' an Distanz 1 -> REJECT, kein Golf-Erbe",
      not any(x.bauteil == "Kupplung" for x in f), str([(x.bauteil, x.aussage) for x in f]))

# AB) Golf-Kontext -> "Der Fahrer bemerkt Vibrationen (Kupplung)." -> NICHT als
# Fahrzeugwechsel interpretiert (Fahrer ist bekannter Nicht-Fahrzeug-Akteur)
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-wartung-ab", TITEL_SCHWACH,
             "Beim Golf treten typische Probleme auf. "
             "Der Fahrer bemerkt dabei häufig ein ungewöhnliches Rupfen der Kupplung."),
    "VW", "Golf")
check("AB) 'Der Fahrer ...' wird NICHT als Fahrzeugwechsel interpretiert -> ACCEPT",
      any(x.bauteil == "Kupplung" for x in f), str([(x.bauteil, x.aussage) for x in f]))

# AC) Golf-Kontext -> "Die Werkstatt empfiehlt einen Wechsel (Zahnriemen)." ->
# NICHT als Fahrzeugwechsel interpretiert (Werkstatt ist bekannter Akteur)
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/vw-wartung-ac", TITEL_SCHWACH,
             "Beim Golf treten typische Probleme auf. "
             "Die Werkstatt empfiehlt dabei einen Zahnriemenwechsel alle 60.000 km."),
    "VW", "Golf", kategorie="wartung")
check("AC) 'Die Werkstatt ...' wird NICHT als Fahrzeugwechsel interpretiert -> ACCEPT",
      any(x.bauteil and "Zahnriemen" in x.bauteil for x in f), str([(x.bauteil, x.aussage) for x in f]))

# Direkte Helfer-Checks für die neue Nominalphrasen-Erkennung
check("_unsichere_satzanfangs_nominalphrase erkennt 'Der Passat' (Golf-Ziel)",
      _unsichere_satzanfangs_nominalphrase("Der Passat hat Probleme.", "Golf") == "passat")
check("_unsichere_satzanfangs_nominalphrase erkennt NICHT 'Die Wasserpumpe' (Bauteil)",
      _unsichere_satzanfangs_nominalphrase("Die Wasserpumpe ist undicht.", "Golf") is None)
check("_unsichere_satzanfangs_nominalphrase erkennt NICHT 'Der Motor' (Bauteil)",
      _unsichere_satzanfangs_nominalphrase("Der Motor verbraucht Öl.", "Golf") is None)
check("_unsichere_satzanfangs_nominalphrase erkennt NICHT 'Das Getriebe' (Bauteil)",
      _unsichere_satzanfangs_nominalphrase("Das Getriebe ruckelt.", "Golf") is None)
check("_unsichere_satzanfangs_nominalphrase erkennt NICHT 'Der Fahrer' (Akteur)",
      _unsichere_satzanfangs_nominalphrase("Der Fahrer bemerkt Vibrationen.", "Golf") is None)
check("_unsichere_satzanfangs_nominalphrase erkennt NICHT 'Die Werkstatt' (Akteur)",
      _unsichere_satzanfangs_nominalphrase("Die Werkstatt empfiehlt einen Wechsel.", "Golf") is None)
check("_unsichere_satzanfangs_nominalphrase erkennt NICHT 'Der Golf' (Zielmodell selbst)",
      _unsichere_satzanfangs_nominalphrase("Der Golf hat ein neues Problem.", "Golf") is None)


# ── Direkte Unit-Checks der neuen Helfer (zusätzlich zur Pipeline-Ebene) ────
print("\n" + "=" * 78)
print("Direkte Helfer-Checks")
print("=" * 78)
check("_anderes_fahrzeug_lokal erkennt 'Beim Passat' (Golf-Ziel)",
      _anderes_fahrzeug_lokal("Beim Passat tritt ein Defekt auf.", "Golf") == "passat")
check("_anderes_fahrzeug_lokal erkennt NICHT 'Beim Golf' (eigenes Modell)",
      _anderes_fahrzeug_lokal("Beim Golf tritt ein Defekt auf.", "Golf") is None)
check("_anderes_fahrzeug_lokal erkennt NICHT 'Beim Turbolader' (Bauteil, kein Fahrzeug)",
      _anderes_fahrzeug_lokal("Beim Turbolader tritt ein Defekt auf.", "Golf") is None)
check("_anderes_fahrzeug_lokal erkennt NICHT 'Beim Kauf' (Füllwort, kein Fahrzeug)",
      _anderes_fahrzeug_lokal("Beim Kauf sollte man auf Rost achten.", "Golf") is None)
check("_claim_widerspricht_fahrzeug_lokal erkennt fremde Marke (Ford)",
      _claim_widerspricht_fahrzeug_lokal("Beim Ford Focus ist die Kupplung betroffen.",
                                        "VW", "Golf") is not None)
check("_claim_widerspricht_fahrzeug_lokal: kein Widerspruch bei neutralem Satz",
      _claim_widerspricht_fahrzeug_lokal("Die Wasserpumpe kann undicht werden.",
                                        "VW", "Golf") is None)

# Regressions-Stichprobe: "stark" ausgerichteter Treffer bleibt unverändert
# (ausgerichtet() selbst nicht angefasst).
ok, grund = ausgerichtet(_treffer("https://www.adac.de/vw-golf-stark", "VW Golf Wartung",
                                  "Die Wasserpumpe kann undicht werden."), "VW", "Golf")
check("ausgerichtet() bleibt für titel-ausgerichtete Treffer unverändert (grund=None)",
      ok is True and grund is None)


print("\n" + "=" * 78)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for fehler in _FEHLER:
        print(f"  - {fehler}")
    raise SystemExit(1)
print("ALLE RC-W4-CONTEXT-ALIGNMENT-TESTS GRUEN")
