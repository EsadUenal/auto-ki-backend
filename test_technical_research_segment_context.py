"""
RC-W4 Runde 2: Segment-Kontext statt fixer Distanz-1-TTL.

BEFUND
------
Die in Runde 1 eingeführte Distanz-1-TTL (`_RC_W4_MAX_KONTEXT_DISTANZ = 1`)
verlor Kontext bereits nach GENAU einem neutralen Brückensatz: "Beim Golf 8
treten typische Probleme auf. Viele Fahrer berichten ausführlich davon im Internet. Die Wasserpumpe
kann undicht werden." -- der dritte Satz galt als Distanz 2 und wurde
verworfen, obwohl der Absatz erkennbar durchgängig vom selben Fahrzeug
spricht. Eine fixe Satzanzahl kann einen echten Themenwechsel nicht von
einem gewöhnlich langen neutralen Absatz unterscheiden.

STRUKTURAUDIT
-------------
Echte Tavily-`content`-Snippets liefern praktisch nie zuverlässige
`\\n\\n`-Absatzgrenzen (an über 20 real gespeicherten Treffern verschiedener
Domains verifiziert). Eine Segmentierung auf Basis von Satzanzahl oder
Newline-Struktur ist deshalb beides keine verlässliche Grundlage.

FIX (app/technical_research.py)
--------------------------------
`_segment_kontext_saetze()`: Kontext bleibt bestehen, bis ein ECHTER
inhaltlicher Widerspruch auftritt (`_claim_widerspricht_fahrzeug_lokal` --
fremde Marke, "beim/für den <anderes Fahrzeug>", unsichere Satzanfangs-
Nominalphrase) -- unabhängig von der Anzahl neutraler Sätze dazwischen.
Zusätzlich gilt dieselbe Prüfung jetzt auch für titel-stark ausgerichtete
Treffer (ein expliziter Fremdfahrzeug-Satz schlägt auch einen starken
Titel-Kontext). Ein ECHTER Absatz-/Überschriften-Trenner im Originaltext
(`_saetze_mit_segmentgrenze()`, selten, aber falls vorhanden als
Verteidigung in der Tiefe genutzt) beendet das Segment zusätzlich immer.

SCOPE: NUR schwachstelle/wartung (Hauptpfad). "rueckruf" (Recall/KBA) und
der Wartungs-Evidence-Candidate-Pfad bleiben unverändert auf der alten
Distanz-1-Logik.

    python test_technical_research_segment_context.py
"""
import types

from app.technical_research import _ziel, phase_fakten

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def _treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


def _ident(generation=None):
    return types.SimpleNamespace(generation=generation)


def _fakten_und_gruende(treffer, marke="VW", modell="Golf", *, kategorie="schwachstelle",
                        generation=None):
    z = _ziel(marke, modell, 2020, None, {"generation": generation} if generation else None)
    abgelehnt = []
    fakten = phase_fakten({kategorie: [treffer]}, z, _ident(generation), (kategorie,), abgelehnt)
    return fakten, abgelehnt


def _bauteile(fakten):
    return {f.bauteil for f in fakten}


TITEL_SCHWACH = "VW Wartung und Pannenstatistik"
TITEL_STARK = "VW Golf 8 Gebrauchtwagen-Test"

print("=" * 78)
print("Testmatrix A-O (realistische Passagen)")
print("=" * 78)

# A) Golf-Satz -> direkter Problemclaim -> ACCEPT
f, a = _fakten_und_gruende(_treffer(
    "https://www.adac.de/a", TITEL_SCHWACH,
    "Beim Golf tritt die Wasserpumpe häufiger undicht auf."))
check("A) Golf-Satz -> direkter Problemclaim -> ACCEPT",
      "Wasserpumpe" in _bauteile(f), str(_bauteile(f)))

# B) Golf-Satz -> 1 neutraler Satz -> Problemclaim -> ACCEPT
f, a = _fakten_und_gruende(_treffer(
    "https://www.adac.de/b", TITEL_SCHWACH,
    "Beim Golf treten mehrere typische Alterungsprobleme auf. "
    "Viele Fahrer berichten ausführlich davon im Internet. "
    "Die Wasserpumpe kann undicht werden."))
check("B) Golf-Satz -> 1 neutraler Satz -> Problemclaim -> ACCEPT",
      "Wasserpumpe" in _bauteile(f), str(_bauteile(f)))

# C) Golf-Satz -> 3 neutrale Sätze -> Problemclaim -> ACCEPT (gleiches Segment)
f, a = _fakten_und_gruende(_treffer(
    "https://www.adac.de/c", TITEL_SCHWACH,
    "Beim Golf treten mehrere typische Alterungsprobleme auf. "
    "Viele Fahrer berichten ausführlich davon im Internet. "
    "Besonders bei höheren Laufleistungen ist Vorsicht sinnvoll. "
    "Im TÜV zeigt sich das ebenfalls. "
    "Die Wasserpumpe kann undicht werden."))
check("C) Golf-Satz -> 3 neutrale Sätze -> Problemclaim -> ACCEPT",
      "Wasserpumpe" in _bauteile(f), str(_bauteile(f)))

# D) Golf-Absatz -> neuer Absatz (echter Trenner) -> neutraler Problemclaim
# ohne Modell -> REJECT
f, a = _fakten_und_gruende(_treffer(
    "https://www.adac.de/d", TITEL_SCHWACH,
    "Beim Golf treten mehrere typische Alterungsprobleme auf.\n\n"
    "Die Wasserpumpe kann undicht werden."))
check("D) Golf-Absatz -> ECHTER Absatztrenner -> neutraler Claim ohne Modell -> REJECT",
      "Wasserpumpe" not in _bauteile(f), str(_bauteile(f)))

# E) Golf -> Passat -> neutraler Claim -> REJECT für Golf
f, a = _fakten_und_gruende(_treffer(
    "https://www.adac.de/e", TITEL_SCHWACH,
    "Beim Golf treten mehrere typische Alterungsprobleme auf. "
    "Der Passat zeigt dagegen andere Schwachstellen. "
    "Die Kupplung verschleißt bei Vielfahrern deutlich schneller als angegeben."))
check("E) Golf -> Passat -> neutraler Claim -> REJECT für Golf",
      "Kupplung" not in _bauteile(f), str(_bauteile(f)))

# F) Golf -> Passat -> später wieder Golf -> neuer Golf-Kontext aktiv
f, a = _fakten_und_gruende(_treffer(
    "https://www.adac.de/f", TITEL_SCHWACH,
    "Beim Golf treten mehrere typische Alterungsprobleme auf. "
    "Der Passat zeigt dagegen andere Schwachstellen. "
    "Beim Golf kann außerdem die Kupplung früh verschleißen."))
check("F) Golf -> Passat -> später wieder Golf -> neuer Golf-Kontext aktiv",
      "Kupplung" in _bauteile(f), str(_bauteile(f)))

# G) starker Golf-Titel -> neutraler Golf-Claim -> ACCEPT
f, a = _fakten_und_gruende(_treffer(
    "https://www.adac.de/g", TITEL_STARK,
    "Die Wasserpumpe kann undicht werden."))
check("G) starker Golf-Titel -> neutraler Claim -> ACCEPT",
      "Wasserpumpe" in _bauteile(f), str(_bauteile(f)))

# H) starker Golf-Titel -> expliziter Passat-Claim -> REJECT (§10 Auftrag:
# ein starker Titel darf NICHT jeden Vergleichssatz ungeprüft durchlassen)
f, a = _fakten_und_gruende(_treffer(
    "https://www.adac.de/h", TITEL_STARK,
    "Der Passat zeigt im Vergleich häufiger einen Kupplungsschaden."))
check("H) starker Golf-Titel -> expliziter Passat-Claim -> REJECT",
      "Kupplung" not in _bauteile(f), str(_bauteile(f)))

# I) Sammelartikel mit Golf-/Passat-/Tiguan-Passagen -> nur Golf
f, a = _fakten_und_gruende(_treffer(
    "https://www.adac.de/i", TITEL_SCHWACH,
    "Beim Golf kann die Wasserpumpe undicht werden. "
    "Der Passat zeigt dagegen häufiger einen Kupplungsschaden. "
    "Der Tiguan wiederum hat öfter Probleme mit dem Fensterheber."))
bauteile_i = _bauteile(f)
check("I) Sammelartikel -> Golf-Wasserpumpe bleibt",
      "Wasserpumpe" in bauteile_i, str(bauteile_i))
check("I) Sammelartikel -> Passat-Kupplung NICHT dem Golf zugeordnet",
      "Kupplung" not in bauteile_i, str(bauteile_i))
check("I) Sammelartikel -> Tiguan-Fensterheber NICHT dem Golf zugeordnet",
      "Fensterheber" not in bauteile_i, str(bauteile_i))

# J) neutraler Satz "Viele Fahrer berichten davon" -> Kontext bleibt
f, a = _fakten_und_gruende(_treffer(
    "https://www.adac.de/j", TITEL_SCHWACH,
    "Beim Golf treten typische Probleme auf. "
    "Viele Fahrer berichten ausführlich davon im Internet. "
    "Die Kupplung verschleißt bei Vielfahrern deutlich schneller als angegeben."))
check("J) 'Viele Fahrer berichten davon' -> Kontext bleibt -> ACCEPT",
      "Kupplung" in _bauteile(f), str(_bauteile(f)))

# K) neutraler Satz "Im TÜV zeigt sich das ebenfalls" -> Kontext bleibt
f, a = _fakten_und_gruende(_treffer(
    "https://www.adac.de/k", TITEL_SCHWACH,
    "Beim Golf treten typische Probleme auf. "
    "Im TÜV zeigt sich das ebenfalls. "
    "Die Kupplung verschleißt bei Vielfahrern deutlich schneller als angegeben."))
check("K) 'Im TÜV zeigt sich das ebenfalls' -> Kontext bleibt -> ACCEPT",
      "Kupplung" in _bauteile(f), str(_bauteile(f)))

# L) explizite fremde Generation -> Kontextbruch (Ergebnis-Ebene, über
# _fremde_generation -- hier am TITEL, da das die bestehende Prüfebene ist)
f, a = _fakten_und_gruende(
    _treffer("https://www.adac.de/l", "Mazda MX-5 NC Wartung",
             "Der Zahnriemen sollte alle 100.000 km gewechselt werden."),
    marke="Mazda", modell="MX-5", kategorie="wartung", generation="ND")
check("L) explizite fremde Generation (Titel) -> REJECT",
      len(f) == 0 and any(x.get("grund") == "andere_generation" for x in a))

# M) fremde Marke -> Kontextbruch
f, a = _fakten_und_gruende(_treffer(
    "https://www.adac.de/m", TITEL_SCHWACH,
    "Beim Golf treten typische Probleme auf. "
    "Beim Ford Focus tritt dagegen häufiger ein Kupplungsschaden auf."))
check("M) fremde Marke (Ford) -> Kontextbruch -> REJECT",
      "Kupplung" not in _bauteile(f), str(_bauteile(f)))

# N) Bullet-Liste innerhalb Golf-Segment -> Kontext bleibt (keine echte
# Absatzgrenze zwischen den Punkten, nur Satzgrenzen)
f, a = _fakten_und_gruende(_treffer(
    "https://www.adac.de/n", TITEL_SCHWACH,
    "Beim Golf sind folgende Punkte bekannt. "
    "Die Bremsen sollten regelmäßig geprüft werden. "
    "Die Kupplung verschleißt bei Vielfahrern deutlich schneller als angegeben."))
check("N) Bullet-artige Liste im Golf-Segment -> Kontext bleibt -> ACCEPT",
      "Kupplung" in _bauteile(f), str(_bauteile(f)))

# O) neue Bullet-/Absatzgruppe mit Passat -> Golf-Kontext endet
f, a = _fakten_und_gruende(_treffer(
    "https://www.adac.de/o", TITEL_SCHWACH,
    "Beim Golf sind folgende Punkte bekannt. "
    "Die Bremsen sollten regelmäßig geprüft werden.\n\n"
    "Der Passat zeigt andere Schwachstellen. "
    "Die Kupplung verschleißt bei Vielfahrern deutlich schneller als angegeben."))
check("O) Neue Absatzgruppe mit Passat -> Golf-Kontext endet -> REJECT",
      "Kupplung" not in _bauteile(f), str(_bauteile(f)))


print("\n" + "=" * 78)
print("Direkte Helfer-Checks: _saetze_mit_segmentgrenze / _distanz1_kontext_saetze")
print("=" * 78)
from app.technical_research import _saetze_mit_segmentgrenze, _distanz1_kontext_saetze  # noqa: E402

check("_saetze_mit_segmentgrenze markiert KEINEN Satz ohne echten Trenner",
      not any(m for _, m in _saetze_mit_segmentgrenze(
          "Beim Golf treten Probleme auf. Die Wasserpumpe kann undicht werden.")))
check("_saetze_mit_segmentgrenze markiert den ersten Satz NACH einem doppelten Newline",
      [m for _, m in _saetze_mit_segmentgrenze(
          "Beim Golf treten Probleme auf.\n\nDie Wasserpumpe kann undicht werden.")] == [False, True])

# rueckruf bleibt auf der alten Distanz-1-Logik (Recall/KBA unangetastet):
abgelehnt_rr: list[dict] = []
erlaubt_rr = _distanz1_kontext_saetze(
    ["Beim Golf gibt es einen Rückruf.", "Besonders betroffen sind ältere Baujahre.",
     "Die Kraftstoffleitung kann undicht werden."],
    marke="VW", modell="Golf", grund="schwach", abgelehnt=abgelehnt_rr,
    url="https://www.kba.de/x", kategorie="rueckruf")
check("rueckruf (Recall/KBA): Distanz-1-TTL bleibt unverändert -- Distanz-2-Satz wird verworfen",
      "Die Kraftstoffleitung kann undicht werden." not in erlaubt_rr, str(erlaubt_rr))


print("\n" + "=" * 78)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for fehler in _FEHLER:
        print(f"  - {fehler}")
    raise SystemExit(1)
print("ALLE RC-W4-SEGMENT-KONTEXT-TESTS GRUEN")
