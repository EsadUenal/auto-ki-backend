"""
N19 -- begrenztes Paket gegen FALSCHE Schwachstellen in der Webrecherche.

Drei Ursachen (Analyse 14.11.2026, echter ADAC-Seitentext vom 08.11.2026):
 A. Garantie-/Spezifikationsangabe als Defektsignal: "Durchrostung 12 Jahre" (Garantietabelle) und
    die Teilstring-Suche "rost" in "Frostschutz".
 B. Fremdes Bauteil aus benachbartem Tabelleninhalt: in einem flachen Tabellenblock wurde das ERSTE
    Bauteilwort des ganzen Blocks ("Spurhalteassistent") zur Schwachstelle.
 C. Einzelbericht als wiederkehrende Modellschwachstelle: Reichweite der Aussage != Vertrauen in die
    Quelle; auch auf einer hochwertigen Domain belegt "bei meinem ..." nur einen Fall.

ECHT: der ADAC-Volltext (tests/fixtures/adac_lexus_rc_f_seitentext_2026-11-08.txt, Fixture vom 08.11.;
NICHT als im neuesten Live-Lauf verarbeitet belegt -- der produktive Schwachstellenpfad liest nur
Suchsnippets). Alles mit "SYNTHETISCH" ist ein markierter Gegenfall.
Nicht geändert: Englisch-Auswertung, Originalabruf-Nachladung, Budget, Rückruf-/Wartungspfad.

    python test_n19_falsche_schwachstellen.py
"""
import asyncio
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="n19f_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


_paar = threading.local()
_orig_paar, _orig_connect = socket.socketpair, socket.socket.connect


def _socketpair(*a, **k):
    _paar.aktiv = True
    try:
        return _orig_paar(*a, **k)
    finally:
        _paar.aktiv = False


def _connect(sock, adresse):
    if getattr(_paar, "aktiv", False):
        return _orig_connect(sock, adresse)
    return _gesperrt()


socket.socketpair, socket.socket.connect = _socketpair, _connect
socket.create_connection = _gesperrt
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import database as _db  # noqa: E402

_db.ensure_tables()

import app.kaufcheck as kc  # noqa: E402
import app.technical_research as tr  # noqa: E402
from app.models import KaufCheckRequest, KaufCheckResponse  # noqa: E402
from app.technical_research import (  # noqa: E402
    _defekt_lokaler_text, _extrahiere_fakten, _hat_materialschaden, _hat_strukturelles_defektsignal,
    _ist_einzelbericht, _ist_tabellenblock, _reichweite, _schwachstelle_candidate_fakten)

_FEHLER: list[str] = []


def check(name, bedingung, detail=""):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


ADAC_TEXT = (Path(__file__).parent / "tests" / "fixtures"
             / "adac_lexus_rc_f_seitentext_2026-11-08.txt").read_text(encoding="utf-8")
ADAC_URL = "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/"
ADAC_TITEL = "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25): Technische Daten | ADAC"
HOCH = "https://www.auto-motor-und-sport.de/gebrauchtwagen/lexus-rc-f/"   # Domain über der Schwelle


def fakten(inhalt, url=HOCH, titel="Lexus RC F", modell="RC F"):
    ab: list[dict] = []
    f = _extrahiere_fakten([{"url": url, "title": titel, "content": inhalt}], "schwachstelle",
                           marke="Lexus", modell=modell, baujahr=2017, generation="UXC1", abgelehnt=ab)
    return f, ab


def bauteile(inhalt, **kw):
    return [x.bauteil for x in fakten(inhalt, **kw)[0]]


# ── A) Garantie/Spezifikation ───────────────────────────────────────────────────────────────────
print("=== A) Garantie-/Spezifikationsangaben und Wortgrenzen ===")
GARANTIE = ("Herstellergarantien\nKategorie\nHerstellerangabe\nGarantie (Fahrzeug)\n3 Jahre oder 100.000 km\n"
            "Lackgarantie\n3 Jahre\nDurchrostung\n12 Jahre\nZusätzliche Garantien\nMobilitätsgarantie: 3 Jahre "
            "europaweit;\nSonstiges\nim ersten Jahr ohne Km-Begrenzung")
check("A1 ECHT: Garantieabschnitt des ADAC (Snippet-Ausschnitt) ist kein Defektsignal",
      not _hat_strukturelles_defektsignal(tr._norm(GARANTIE), GARANTIE))
f_adac, ab_adac = fakten(ADAC_TEXT, url=ADAC_URL, titel=ADAC_TITEL)
check("A2 ECHT: ADAC-Volltext ergibt KEINE Schwachstelle (vorher: 'Spurhalteassistent')",
      f_adac == [], str([(x.bauteil, x.aussage[:50]) for x in f_adac]))
check("A3 ECHT: die Garantiezeile erscheint nicht mehr als angenommener Claim",
      not any("Durchrostung" in (x.aussage or "") for x in f_adac))
# SYNTHETISCH
check("A4 SYNTHETISCH: 'Durchrostung 12 Jahre' (Angabe) -> kein Schaden",
      not _hat_materialschaden("Garantie: Durchrostung 12 Jahre") and not _hat_materialschaden("Durchrostung (12 Jahre)"))
check("A5 SYNTHETISCH: 'Durchrostungsgarantie', 'Korrosionsschutz', 'rostfrei' -> kein Schaden",
      not any(_hat_materialschaden(t) for t in (
          "Durchrostungsgarantie 12 Jahre", "Mit Korrosionsschutzwachs versiegelt.", "Der Auspuff ist rostfrei.",
          "Garantie gegen Durchrostung.")))
check("A6 SYNTHETISCH: Frostschutz ist kein Rostproblem",
      not _hat_materialschaden("Frostschutz im Kühlsystem prüfen.") and not _hat_materialschaden("Frostschutzmittel"))
check("A7 SYNTHETISCH: echter Rost-/Durchrostungsschaden bleibt Schaden",
      all(_hat_materialschaden(t) for t in (
          "An den Radläufen tritt Rost auf.", "Durchrostung an den Schwellern festgestellt.",
          "Korrosion am Unterboden ist verbreitet.", "Durchrostung nach 12 Jahren.", "Verrostete Bremsleitungen.")))
check("A8 SYNTHETISCH: Schaden MIT Garantiekontext bleibt Schaden",
      _hat_materialschaden("Nach fünf Jahren Durchrostung festgestellt, Garantie abgelehnt.")
      and _hat_materialschaden("Die Garantie greift nicht: Durchrostung ist an den Schwellern aufgetreten."))
f_a8, _ = fakten("Beim Lexus RC F tritt nach fünf Jahren Durchrostung an der Karosserie auf, "
                 "die Garantie wird häufig abgelehnt.")
check("A9 SYNTHETISCH: allgemein formulierter Durchrostungsbefund mit Garantieangabe ergibt über den Pfad "
      "einen Fakt (Defektsignal trotz Garantiekontext)",
      len(f_a8) == 1, str([(x.bauteil, x.aussage[:60]) for x in f_a8]))
# Reichweite (N19-Abschluss): derselbe Schaden OHNE Aussage über Verbreitung ist ein echter Schaden (A8),
# aber keine belegte Modellschwachstelle -- unbestaetigt mit eigenem Grund. Nicht die Vergangenheitsform
# entscheidet das (vorher hier als 'einzelbericht' erwartet), sondern die fehlende Reichweitenevidenz.
f_a9b, ab_a9b = fakten("Beim Lexus RC F wurde nach fünf Jahren Durchrostung an der Karosserie festgestellt, "
                       "die Garantie wurde abgelehnt.")
check("A9b SYNTHETISCH: derselbe Schaden ohne Reichweitenevidenz -> kein Modell-Fakt, Grund 'reichweite_unbelegt'",
      f_a9b == [] and any(a.get("grund") == "reichweite_unbelegt" for a in ab_a9b))
check("A10 SYNTHETISCH: Frostschutz-Satz erzeugt keinen Fakt",
      bauteile("Der Frostschutz im Kühlsystem des Lexus RC F wurde geprüft.") == [])

# ── B) Fremdes Bauteil aus Nachbarzeilen ───────────────────────────────────────────────────────
print("\n=== B) Bauteil nur aus der Defektzeile ===")
# Die Defektzeile traegt "haeufig" (Reichweite); geprueft wird hier die Bauteilbindung.
TABELLE = ("Spurhalteassistent\nSerie\nSpurwechselassistent\nSerie\nVerkehrsschild-Erkennung\nSerie\n"
           "Wasserpumpe\nhäufig undicht\nFernlichtassistent\nSerie\nKlimaanlage\nSerie")
check("B0 Der Block wird als Tabelle erkannt, Fließtext nicht",
      _ist_tabellenblock(TABELLE) and not _ist_tabellenblock("Beim RC F wird die Wasserpumpe häufig undicht."))
b1 = bauteile(TABELLE)
check("B1 SYNTHETISCH: Defektzeile in fremder Ausstattungstabelle -> Bauteil der Defektzeile, kein 'Spurhalteassistent'",
      b1 == ["Wasserpumpe"], str(b1))
check("B1b Bauteilbindung (Funktion): lokaler Text der Defektzeile enthält Zeilenlabel, keine Fremdzeile",
      "Wasserpumpe" in _defekt_lokaler_text(TABELLE) and "Spurhalteassistent" not in _defekt_lokaler_text(TABELLE))
check("B2 SYNTHETISCH: Tabellenzeile ohne eigenes Bauteil in der Defektzeile erzeugt keinen Fremd-Fakt",
      bauteile("Spurhalteassistent\nSerie\nSpurwechselassistent\nSerie\nVerkehrsschild-Erkennung\nSerie\n"
               "Fernlichtassistent\nSerie\nDefekt möglich\nSerie\nKlimaanlage\nSerie") == [])
check("B3 SYNTHETISCH: Fließtext mit Bauteil und Defekt im selben Satz bleibt unverändert",
      bauteile("Beim Lexus RC F ist die Wasserpumpe häufig undicht.") == ["Wasserpumpe"])
check("B4 SYNTHETISCH: Tabellenzeile, die Bauteil UND Defekt selbst trägt, bleibt erhalten",
      "Wasserpumpe" in bauteile("Spurhalteassistent\nSerie\nSpurwechselassistent\nSerie\n"
                                "Wasserpumpe häufig undicht\nFernlichtassistent\nSerie\nKlimaanlage\nSerie"))

# ── C) Einzelbericht ────────────────────────────────────────────────────────────────────────────
print("\n=== C) Einzelbericht != wiederkehrende Modellschwachstelle (hochwertige Domain) ===")
check("C1 SYNTHETISCH: 'bei meinem RC F war die Wasserpumpe nach 90.000 km defekt' (hohe Domain) -> kein Fakt",
      bauteile("Bei meinem Lexus RC F war die Wasserpumpe nach 90.000 km defekt.") == [])
f_c1, ab_c1 = fakten("Bei meinem Lexus RC F war die Wasserpumpe nach 90.000 km defekt.")
check("C2 … und der Grund ist als 'einzelbericht' sichtbar protokolliert",
      any(a.get("grund") == "einzelbericht" for a in ab_c1))
check("C3 SYNTHETISCH: ausdrücklich wiederkehrende Aussage -> Fakt",
      bauteile("Beim Lexus RC F ist die Wasserpumpe häufig undicht.") == ["Wasserpumpe"]
      and bauteile("Beim Lexus RC F ist die undichte Wasserpumpe ein bekanntes Problem.") == ["Wasserpumpe"])
# Korrigiert (Abnahme): ein Sprecher-/Einzelfahrzeug-Bezug plus Wiederholungs- oder Bekanntheitswort
# ("bekanntes Problem", "häufig", "dreimal") belegt KEINE Eigenschaft der Modellpopulation. Erst eine
# ausdrückliche Reichweite ("bei vielen", "bei diesem Modell") hebt den Einzelfahrzeug-Bezug auf.
check("C4 SYNTHETISCH: Ich-Bericht + 'bekanntes Problem' / 'häufig' / 'dreimal' -> KEIN Fakt (Wiederholung "
      "am eigenen Auto != Modellproblem)",
      all(bauteile(t) == [] for t in (
          "Bei meinem Lexus RC F war die Wasserpumpe undicht, ein bekanntes Problem.",
          "Bei meinem Auto war die Wasserpumpe undicht, ein bekanntes Problem.",
          "Bei meinem Fahrzeug war die Wasserpumpe dreimal undicht.",
          "Bei meinem Lexus RC F war die Wasserpumpe schon wieder häufig undicht.",
          "Der Testwagen Lexus RC F hatte dreimal eine undichte Wasserpumpe.")))
check("C4b SYNTHETISCH: Ich-Bericht MIT ausdrücklicher Reichweite über die Modellpopulation -> Fakt",
      bauteile("Bei meinem Lexus RC F war die Wasserpumpe undicht; bei vielen RC F ist das ein bekanntes "
               "Problem.") == ["Wasserpumpe"]
      and bauteile("Beim Lexus RC F ist die Wasserpumpe bei diesem Modell häufig undicht.") == ["Wasserpumpe"])
check("C5 SYNTHETISCH: Erkennung",
      _ist_einzelbericht("Bei mir war die Pumpe defekt.") and _ist_einzelbericht("Ein Leser berichtet von einem Defekt.")
      and _ist_einzelbericht("Bei meinem Auto ist das ein bekanntes Problem.")
      and not _ist_einzelbericht("Bei meinem Auto war sie defekt, bei vielen Modellen ist das bekannt.")
      and not _ist_einzelbericht("Die Pumpe ist häufig defekt.")
      and not _ist_einzelbericht("Die Wasserpumpe des RC F ist undicht."))
check("C6 SYNTHETISCH: Negation -> kein Fakt",
      bauteile("Beim Lexus RC F gibt es keine bekannten Probleme mit der Wasserpumpe.") == [])
check("C7 SYNTHETISCH: fremdes Modell (GS F) -> kein Fakt",
      bauteile("Beim Lexus GS F ist die Wasserpumpe häufig undicht.", url=HOCH.replace("rc-f", "gs-f"),
               titel="Lexus GS F Gebrauchtwagen") == [])

# ── D) Abnahme-Gegenproben ──────────────────────────────────────────────────────────────────────
print("\n=== D) Abnahme: Dauerangaben, Bauteilbindung, Umbruch, reale Textproben ===")
check("D1 SYNTHETISCH: Schaden mit Zeit-/Dauerangabe wird NICHT wegen der Dauer verworfen",
      all(_hat_materialschaden(t) for t in (
          "Durchrostung nach zwölf Jahren festgestellt.", "Rost seit zwei Jahren an der Karosserie.",
          "Durchrostung nach 12 Jahren.", "Seit zwei Jahren zeigt sich Rost.")))
check("D2 SYNTHETISCH: Garantiedauer ohne Schaden (auch als Wort/Markdown-Tabelle) bleibt kein Fakt",
      not any(_hat_materialschaden(t) for t in (
          "Durchrostung zwölf Jahre", "| Durchrostung | 12 Jahre |", "Lackgarantie 3 Jahre Durchrostung 12 Jahre"))
      and bauteile("Der Lexus RC F hat zwölf Jahre Garantie gegen Durchrostung der Karosserie.") == [])
check("D3 SYNTHETISCH: Defektzeile nur 'undicht' neben fremder Ausstattung erfindet kein Bauteil "
      "(vorher: 'Undicht' bzw. Dach/Fenster)",
      bauteile("Spurhalteassistent\nSerie\nSpurwechselassistent\nSerie\nVerkehrsschild-Erkennung\nSerie\n"
               "undicht\nFernlichtassistent\nSerie") == []
      and bauteile("Der Lexus RC F ist häufig undicht.") == [])
check("D4 SYNTHETISCH: ein echtes Bauteil neben 'undicht' bleibt Bauteil (auch Schiebedach)",
      bauteile("Beim Lexus RC F ist das Schiebedach häufig undicht.") == ["Schiebedach"])
FLIESS = "Beim Lexus RC F ist die Wasserpumpe bei hoher Laufleistung häufig undicht und defekt."
UMBRUCH = "Beim Lexus RC F\nist die Wasserpumpe\nbei hoher\nLaufleistung\nhäufig\nundicht\nund defekt."
check("D5 SYNTHETISCH: derselbe Defekttext als Fließtext und mit kurzen Zeilenumbrüchen -> gleiches Bauteil",
      bauteile(FLIESS) == bauteile(UMBRUCH) == ["Wasserpumpe"], f"{bauteile(FLIESS)} vs {bauteile(UMBRUCH)}")
check("D6 Tabellenblock-Erkennung: umbrochener Fließtext mit Prädikat ist keine Tabelle",
      not _ist_tabellenblock(UMBRUCH) and _ist_tabellenblock(TABELLE))

# REAL: weitere vorhandene Texte (Fixtures vom 08.11.2026), jeweils Volltext UND Snippet-Ausschnitt
_rb = __import__("json").loads((Path(__file__).parent / "tests" / "fixtures"
                                / "live_rollenbeleg_treffer_2026-11-08.json").read_text(encoding="utf-8"))
_wj = __import__("json").loads((Path(__file__).parent / "tests" / "fixtures"
                                / "live_wartung_treffer_2026-11-08.json").read_text(encoding="utf-8"))
_rd = (Path(__file__).parent / "tests" / "fixtures"
       / "lexus_rettungsdatenblaetter_extract_2026-11-08.txt").read_text(encoding="utf-8")
check("D7 ECHT: ADAC-Seite im Markdown-Tabellenformat (Rollenbeleg-Extrakt) -> keine Schwachstelle "
      "(vorher: 'Durchrostung' aus '| Durchrostung | 12 Jahre |')",
      fakten(_rb["extrakt"], url=_rb["kandidat"], titel=ADAC_TITEL)[0] == [])
check("D8 ECHT: Rettungsdatenblatt-Extrakt -> keine Schwachstelle",
      fakten(_rd, url="https://www.lexus.de/rettungskarte.pdf", titel="Lexus RC F Rettungskarte")[0] == [])
check("D9 ECHT: alle 16 echten Tavily-Snippets der Wartungsanfragen -> keine Schwachstelle",
      all(fakten(h["content"], url=h["url"], titel=h.get("title") or "")[0] == []
          for h in _wj["ohne UXC1"] + _wj["mit UXC1"]))
_zeile = ADAC_TEXT.replace("Spurhalteassistent\nSerie\n", "Spurhalteassistent\nSerie\nWasserpumpe\nhäufig undicht\n", 1)
check("D10 ECHT+SYNTHETISCH: echte ADAC-Seite mit EINGEFÜGTER Defektzeile -> genau das eingefügte Bauteil, "
      "nicht 'Spurhalteassistent'/'Durchrostung'",
      bauteile(_zeile, url=ADAC_URL, titel=ADAC_TITEL) == ["Wasserpumpe"],
      str(bauteile(_zeile, url=ADAC_URL, titel=ADAC_TITEL)))
check("D11 ECHT+SYNTHETISCH: dieselbe Seite mit nur 'undicht' -> kein erfundenes Bauteil",
      bauteile(ADAC_TEXT.replace("Spurhalteassistent\nSerie\n", "Spurhalteassistent\nSerie\nundicht\n", 1),
               url=ADAC_URL, titel=ADAC_TITEL) == [])

# ── E) Reichweite ohne Ich-Marker: Einzelereignis != Modellbefund ───────────────────────────────
print("\n=== E) Einzelereignis ohne Ich-Marker, Einzelfahrzeug-Kontext, allgemeiner Modellbefund ===")
M = "des Lexus RC F"   # Satzlaenge: sehr kurze Saetze verwirft `_saetze` ohnehin (Altverhalten)


def bt(text, titel="Lexus RC F"):
    return bauteile(text, titel=titel)


check("E1 SYNTHETISCH: 'Die Wasserpumpe war dreimal undicht' ohne Reichweitenevidenz -> kein Modell-Fakt",
      bt(f"Die Wasserpumpe {M} war dreimal undicht.") == []
      and bt(f"Die Wasserpumpe {M} war nach 90.000 km undicht.") == []
      and bt(f"Die Wasserpumpe {M} war undicht.") == [])
check("E2 SYNTHETISCH: derselbe Satz im Testwagen-/Einzelfahrzeug-Kontext (Titel oder Nachbarsatz), "
      "auch im Praesens und mit 'haeufig' -> kein Fakt",
      bt(f"Die Wasserpumpe {M} ist undicht.", "Lexus RC F im Dauertest") == []
      and bt(f"Die Wasserpumpe {M} ist häufig undicht.", "Testwagen Lexus RC F") == []
      and bt(f"Bei unserem Lexus RC F gab es Ärger. Die Wasserpumpe {M} ist undicht.") == []
      and bt(f"Der Redaktionswagen Lexus RC F fiel auf. Die Wasserpumpe {M} ist undicht. "
             "Unser Testwagen stand danach zwei Tage in der Werkstatt.") == [])
check("E3 SYNTHETISCH: klarer allgemeiner Modellbefund OHNE 'ich' und OHNE 'viele' bleibt Fakt",
      bt(f"Die Wasserpumpe {M} neigt zu Undichtigkeit.") == ["Wasserpumpe"]
      and bt("Beim Lexus RC F ist die Wasserpumpe häufig undicht.") == ["Wasserpumpe"]
      and bt("Typische Schwachstelle des Lexus RC F: die Wasserpumpe wird undicht.") == ["Wasserpumpe"]
      and bt(f"Die Wasserpumpe {M} ist ein bekanntes Problem.") == ["Wasserpumpe"])
check("E4 SYNTHETISCH: tatsächlicher Einzelbericht (Zählung am Fahrzeug / Sprecher) mit bloß behauptetem "
      "'bekannten Problem' -> kein Fakt",
      bt(f"Die Wasserpumpe {M} war dreimal undicht, ein bekanntes Problem.") == []
      and bt("Bei meinem Lexus RC F war die Wasserpumpe undicht, ein bekanntes Problem.") == [])
# Korrigiert (N19-Abschluss): ohne Einzelfahrzeug-Bezug traegt "ein bekanntes Problem" die allgemeine
# Aussage im Satz selbst; die Vergangenheitsform ist kein Gegenbeleg (vorher hier als kein Fakt erwartet).
check("E4b SYNTHETISCH: 'war undicht, ein bekanntes Problem' ohne Einzelfahrzeug-Bezug -> Fakt",
      bt(f"Die Wasserpumpe {M} war undicht, ein bekanntes Problem.") == ["Wasserpumpe"])
check("E5 SYNTHETISCH: Vergangenheit MIT Haeufigkeit/Typik oder Populationsreichweite bleibt Fakt",
      bt("Beim Lexus RC F war die Wasserpumpe häufig undicht.") == ["Wasserpumpe"]
      and bt("Bei frühen Modellen des Lexus RC F war die Wasserpumpe undicht.") == ["Wasserpumpe"])
check("E6 Reichweite (Funktion): einzel / allgemein / unbelegt -- Zeitform und km-Anker entscheiden nicht",
      _reichweite(f"Die Wasserpumpe {M} war dreimal undicht.") == "einzel"
      and _reichweite(f"Die Wasserpumpe {M} ist undicht.", (True, False)) == "einzel"
      and _reichweite(f"Die Wasserpumpe {M} ist undicht.") == "unbelegt"
      and _reichweite("Die Pumpe wurde nach 80.000 km ersetzt und war undicht.") == "unbelegt"
      and _reichweite(f"Die Wasserpumpe {M} ist undicht.", (False, True)) == "allgemein"
      and _reichweite("Bei diesem Modell tritt der Defekt häufig nach 90.000 km auf.") == "allgemein"
      and _reichweite("Bei vielen Fahrzeugen war die Pumpe dreimal undicht.") == "allgemein")

# ── F) N19-Abschluss: tragende Reichweitenevidenz aus Satz ODER zugehörigem Kontext ──────────────
print("\n=== F) Reichweitenevidenz: bare Aussage, Einzelkontext, Modellkontext, km-Anker, historisch ===")
BARE = f"Die Wasserpumpe {M} ist undicht."
f_bare, ab_bare = fakten(BARE)
check("F1 SYNTHETISCH: unklare bare-Präsensaussage ohne Kontext (hohe Domain) -> kein bestätigter Fakt, "
      "Grund 'reichweite_unbelegt'",
      f_bare == [] and any(a.get("grund") == "reichweite_unbelegt" for a in ab_bare))
check("F2 SYNTHETISCH: derselbe Satz mit eindeutigem Einzelfahrzeug-Kontext -> kein Fakt ('einzelbericht')",
      fakten("Unser Testwagen Lexus RC F stand zwei Tage in der Werkstatt. " + BARE)[0] == []
      and any(a.get("grund") == "einzelbericht"
              for a in fakten("Unser Testwagen Lexus RC F stand zwei Tage in der Werkstatt. " + BARE)[1]))
check("F3 SYNTHETISCH: derselbe Satz mit tragendem Modellproblem-Kontext -> Fakt",
      bt("Typische Schwachstellen des Lexus RC F:\n\n" + BARE) == ["Wasserpumpe"]
      and bt("Beim Lexus RC F treten einige bekannte Probleme auf. " + BARE) == ["Wasserpumpe"]
      and bt(BARE + " Das ist ein bekanntes Problem dieses Modells.") == ["Wasserpumpe"])
check("F4 SYNTHETISCH: Kontext muss zu Defekt und Zielmodell gehören",
      bt("Typische Schwachstellen des Lexus GS F:\n\n" + BARE) == []
      and bt("Beim Lexus RC F treten einige bekannte Probleme auf. Der Lexus GS F ist anders aufgebaut. " + BARE) == []
      and bt("Beim Lexus RC F ist das häufig unproblematisch. " + BARE) == []
      and bt(BARE + " Das ist beim Lexus GS F ein bekanntes Problem.") == [])
check("F5 SYNTHETISCH: Seitenüberschrift oder Domainstufe allein tragen keine Reichweite",
      bt(BARE, "Typische Schwachstellen des Lexus RC F") == []
      and bauteile(BARE, url="https://www.adac.de/lexus-rc-f-maengel") == [])
check("F6 SYNTHETISCH: allgemeiner wiederkehrender Defekt MIT Kilometerangabe -> Fakt (Anker macht kein Einzelereignis)",
      bt("Beim Lexus RC F wird die Wasserpumpe bei diesem Modell häufig nach 90.000 km undicht.") == ["Wasserpumpe"]
      and bt(f"Die Wasserpumpe {M} wird typischerweise ab 80.000 km undicht.") == ["Wasserpumpe"])
check("F7 SYNTHETISCH: allgemeiner historischer Modellbefund -> Fakt",
      bt("Bei den ersten Baujahren des Lexus RC F war die Wasserpumpe undicht.") == ["Wasserpumpe"]
      and bt("Vor dem Facelift war beim Lexus RC F die Wasserpumpe undicht.") == ["Wasserpumpe"])
check("F8 SYNTHETISCH: Snippet ohne den Rahmen (nur der Satz) bleibt unbelegt, derselbe Satz MIT im Snippet "
      "enthaltenem Rahmen wird Fakt -- kein Kontext wird ergänzt, der nicht im Treffer steht",
      bt(BARE) == [] and bt("Typische Schwachstellen des Lexus RC F:\n\n" + BARE) == ["Wasserpumpe"])

# ── G) Candidate-Pfad: Reichweitenregel und echte Mehrquellen-Gegenprobe ──────────────────────────
print("\n=== G) Candidate-Pfad (Quellen unter der Schwelle), qualifizierende unabhängige Familien ===")
# Drei verschiedene Familien unter MIN_SCORE_FAKT, per Werkstatt-Titel als 'secondary' klassifiziert
# (NICHT community/unknown) -- zaehlen also tatsaechlich fuer MIN_FAMILIES_MULTI_SOURCE.
FAM = ["https://www.werkstatt-eins.example/rc-f", "https://www.reparatur-zwei.example/rc-f",
       "https://www.kfz-drei.example/rc-f"]
def cand(texte, anker=None):
    tr_ = [{"url": u, "title": "Lexus RC F Werkstatt-Ratgeber", "content": t} for u, t in zip(FAM, texte)]
    return _schwachstelle_candidate_fakten(tr_, marke="Lexus", modell="RC F", generation=None,
                                           haupt_fakten=anker or [], erlaube_multi_source=True)
check("G0 Gegenprobe ist echt: die drei Familien sind qualifizierend (secondary, verschiedene Familien, unter der Schwelle)",
      all(tr._wartung_source_type({"url": u, "title": "Lexus RC F Werkstatt-Ratgeber"}) == "secondary" for u in FAM)
      and len({tr._source_family(u) for u in FAM}) == 3
      and all(tr._score_domain_fuer_marke(u, tr.KATEGORIE_SCHWACHSTELLEN, "Lexus") < tr.MIN_SCORE_FAKT for u in FAM))
fg1, _, _ = cand(["Beim Lexus RC F ist die Wasserpumpe häufig undicht."] * 3)
check("G1 POSITIVKONTROLLE: drei qualifizierende Familien mit allgemeiner Aussage -> Mehrquellen-Fakt",
      len(fg1) == 1 and fg1[0].beleglage == "mehrfach_unabhaengig", str([(x.bauteil, x.beleglage) for x in fg1]))
fg2, ab2, _ = cand([BARE] * 3)
check("G2 drei qualifizierende Familien mit bare-Aussage ohne Reichweite -> KEIN Fakt, Grund 'reichweite_unbelegt'",
      fg2 == [] and sum(1 for a in ab2 if a.get("grund") == "reichweite_unbelegt") == 3)
fg3, ab3, _ = cand([f"Die Wasserpumpe {M} war dreimal undicht, ein bekanntes Problem."] * 3)
check("G3 drei qualifizierende Familien mit Einzelberichten -> KEIN Fakt, Grund 'einzelbericht'",
      fg3 == [] and sum(1 for a in ab3 if a.get("grund") == "einzelbericht") == 3)
_anker = tr.WebFakt(kategorie="schwachstelle", bauteil="Wasserpumpe",
                    aussage="Beim Lexus RC F ist die Wasserpumpe häufig undicht.", confidence="mittel",
                    quellen=[tr.EvidenceQuelle(typ="web_technik", url="https://www.fachmedium.de/a",
                                               titel="Fachmedium", qualitaet="Fachmedien")])
cand([BARE, f"Die Wasserpumpe {M} war dreimal undicht.", BARE], anker=[_anker])
check("G4 ein schwacher bare-/Einzel-Kandidat reichert einen Hauptpfad-Anker NICHT an",
      [q.url for q in _anker.quellen] == ["https://www.fachmedium.de/a"])

# ── Bis zum Bericht (echter run_kaufcheck-Pfad, gestubbte Suche/LLM) ────────────────────────────
print("\n=== Bericht, Verkäuferfragen und Dokumente (gestubbter run_kaufcheck) ===")
treffer_ = lambda u, t, c: {"url": u, "title": t, "content": c}   # noqa: E731
RK_URL = ("https://www.lexus.de/content/dam/lexus/nmsc/germany/lexus-besitzer/"
          "fahrzeuginformationen/RCF_tcm-3150-1284184.pdf")
RK_VOLL = ("Lexus RC F\n(UXC1 (EU, M), ab 09/2014)\nStand: 10/2014\n"
           "Lexus RC F (UXC1 (EU, ab 09/2014)\n12V Spannungsversorgung trennen\n"
           "Lexus RC F\n(UXC1 (EU, M), ab 09/2014)")
ADAC_ID = treffer_(ADAC_URL, ADAC_TITEL,
                   "Marke Lexus Modell RC F Typ Ultimate Edition Automatik Baureihe RC (UXC1) "
                   "(12/14 - 06/25) Herstellerinterne Baureihenbezeichnung UXC1 Motorcode 2UR-GSE "
                   "Leistung maximal in PS 464 Antriebsart Hinterrad Getriebeart Automatikgetriebe")
AMS = treffer_("https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
               "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
               "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Leistung 181 PS bis 477 PS. "
               "Hubraum 1998 cm3 bis 4969 cm3. LEXUS RC F TRACK EDITION IM SUPERTEST: Was kann der "
               "V8-Sauger auf der Rennstrecke? LEXUS LC 500 UND RC F: V8-Sportler auf Abschiedstour.")
REQ = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000, motor="5.0 V8",
                       kraftstoff="Benzin", leistung_ps=477, getriebe="Automatik", antrieb="Heck", ausstattung=[])


def db_miss(marke, modell, baujahr):
    return None, {"match_art": "no_match", "konfidenz": "niedrig", "belastbar": False,
                  "fehlende_angabe": "simulierter DB-Miss, Testdouble"}


async def lauf(schwachstellen_treffer):
    routing = {"Generation Modellcode": [treffer_(RK_URL, "Lexus RC F Rettungskarte", "12V trennen")],
               "technische Daten": [ADAC_ID], "Generation Baureihe Bauzeitraum": [AMS],
               "bekannte Probleme Schwachstellen": schwachstellen_treffer}

    async def search(query, *, count=8, exclude_domains=None, include_domains=None):
        for teil, liste in routing.items():
            if teil in query:
                return [dict(h) for h in liste]
        return []

    async def extract(urls):
        g = {u: RK_VOLL for u in urls if u == RK_URL}
        return g, {"angefragt": len(urls), "aus_cache": 0, "extract_calls": 1, "extract_urls": len(urls),
                   "erfolge": len(g), "fehler": len(urls) - len(g)}

    async def gemini(system, user):
        return {"bericht": "x", "empfehlung": "kaufen_nach_besichtigung",
                "risiko_evidence_ids": [], "empfehlung_evidence_ids": []}

    async def verboten(*a, **k):
        raise AssertionError("Marktprovider darf nicht laufen")

    orig = (kc.call_gemini_json, kc.tavily_search_with_fallback, kc.marktpreis_recherche_moeglich,
            kc.vertiefe_marktrecherche, kc.find_baureihe_mit_vertrauen, tr.tavily_search, tr.hole_raw_content)
    kc.call_gemini_json = gemini
    kc.tavily_search_with_fallback = kc.vertiefe_marktrecherche = verboten
    kc.marktpreis_recherche_moeglich = lambda *a: False
    kc.find_baureihe_mit_vertrauen = db_miss
    tr.tavily_search, tr.hole_raw_content = search, extract
    try:
        return KaufCheckResponse(**await kc.run_kaufcheck(REQ))
    finally:
        (kc.call_gemini_json, kc.tavily_search_with_fallback, kc.marktpreis_recherche_moeglich,
         kc.vertiefe_marktrecherche, kc.find_baureihe_mit_vertrauen, tr.tavily_search,
         tr.hole_raw_content) = orig


def sichtbar(r):
    akt = r.kaufaktionen
    teile = [r.bericht]
    for sek in (akt.verkaeuferfragen, akt.besichtigung, akt.probefahrt, akt.dokumente):
        teile += [f"{a.titel} {a.aktion}" for a in sek.fahrzeugspezifisch + sek.basis]
    return "\n".join(teile)


def sw_insights(r):
    return [i for i in r.insights if i.kategorie in ("web_schwachstelle", "schwachstelle")]


async def pfad():
    # ECHT: kompletter ADAC-Text als Schwachstellen-Treffer
    r0 = await lauf([treffer_(ADAC_URL, ADAC_TITEL, ADAC_TEXT)])
    check("P1 ECHT: kein Schwachstellen-Insight aus dem ADAC-Volltext", sw_insights(r0) == [],
          str([i.titel for i in sw_insights(r0)]))
    check("P2 ECHT: 'Spurhalteassistent' erscheint nirgends als Schwachstelle in Bericht, Fragen, Dokumenten",
          "Spurhalteassistent" not in sichtbar(r0) and "Schwachstelle (Webrecherche)" not in r0.bericht)
    check("P3 ECHT: Identität unverändert (UXC1)", r0.vehicle_identity.get("generation") == "UXC1")
    # SYNTHETISCH: Einzelbericht (hohe Domain) -> nichts Sichtbares
    r1 = await lauf([treffer_(HOCH, "Lexus RC F Gebrauchtwagen",
                              "Bei meinem Lexus RC F war die Wasserpumpe nach 90.000 km defekt.")])
    check("P4 SYNTHETISCH: Einzelbericht (hohe Domain) -> kein Insight, keine Wasserpumpen-Frage/-Nachweis",
          sw_insights(r1) == [] and "Wasserpumpe" not in sichtbar(r1), str([i.titel for i in sw_insights(r1)]))
    # SYNTHETISCH: ausdrücklich wiederkehrend -> sichtbar bis Fragen/Nachweise
    r2 = await lauf([treffer_(HOCH, "Lexus RC F Gebrauchtwagen",
                              "Beim Lexus RC F ist die Wasserpumpe häufig undicht.")])
    check("P5 SYNTHETISCH: wiederkehrende Aussage -> Insight 'Wasserpumpe' bleibt sichtbar",
          any("Wasserpumpe" in i.titel for i in sw_insights(r2)) and "Wasserpumpe" in r2.bericht,
          str([i.titel for i in sw_insights(r2)]))
    check("P6 … mit Frage/Nachweis zur Wasserpumpe", "Wasserpumpe" in sichtbar(r2))
    # Reichweiten-Nachtrag: Einzelereignis ohne Ich-Marker -> nichts Sichtbares; Modellbefund -> sichtbar
    r3 = await lauf([treffer_(HOCH, "Lexus RC F Gebrauchtwagen",
                              "Die Wasserpumpe des Lexus RC F war nach 90.000 km dreimal undicht.")])
    check("P8 SYNTHETISCH: Einzelereignis ohne Ich-Marker -> kein Insight, keine Frage, kein Dokument-Nachweis",
          sw_insights(r3) == [] and "Wasserpumpe" not in sichtbar(r3), str([i.titel for i in sw_insights(r3)]))
    r4 = await lauf([treffer_(HOCH, "Lexus RC F im Dauertest",
                              "Die Wasserpumpe des Lexus RC F ist häufig undicht.")])
    check("P9 SYNTHETISCH: dieselbe Aussage im Testwagen-Kontext (Titel) -> nichts Sichtbares",
          sw_insights(r4) == [] and "Wasserpumpe" not in sichtbar(r4))
    r5 = await lauf([treffer_(HOCH, "Lexus RC F Gebrauchtwagen",
                              "Die Wasserpumpe des Lexus RC F neigt zu Undichtigkeit.")])
    check("P10 SYNTHETISCH: allgemeiner Modellbefund ohne 'ich'/'viele' -> Insight, Bericht, Frage/Nachweis",
          any("Wasserpumpe" in i.titel for i in sw_insights(r5)) and "Wasserpumpe" in r5.bericht
          and "Wasserpumpe" in sichtbar(r5), str([i.titel for i in sw_insights(r5)]))
    r6 = await lauf([treffer_(HOCH, "Lexus RC F Gebrauchtwagen", "Die Wasserpumpe des Lexus RC F ist undicht.")])
    check("P11 SYNTHETISCH: bare-Aussage ohne Reichweitenevidenz -> kein Insight, keine Frage, kein Dokument-Nachweis",
          sw_insights(r6) == [] and "Wasserpumpe" not in sichtbar(r6), str([i.titel for i in sw_insights(r6)]))
    r7 = await lauf([treffer_(HOCH, "Lexus RC F Gebrauchtwagen",
                              "Typische Schwachstellen des Lexus RC F:\n\nDie Wasserpumpe des Lexus RC F ist undicht.")])
    check("P12 SYNTHETISCH: derselbe Satz unter tragendem Modellproblem-Rahmen -> Insight, Bericht, Frage/Nachweis",
          any("Wasserpumpe" in i.titel for i in sw_insights(r7)) and "Wasserpumpe" in r7.bericht
          and "Wasserpumpe" in sichtbar(r7), str([i.titel for i in sw_insights(r7)]))
    check("P7 Empfehlung/Status in allen Läufen unberührt von diesem Paket (gleiche Stufe ohne Fakt)",
          (r0.empfehlung, r0.recommendation_state) == (r1.empfehlung, r1.recommendation_state),
          f"{(r0.empfehlung, r0.recommendation_state)} vs {(r1.empfehlung, r1.recommendation_state)}")


asyncio.run(pfad())

print()
if _FEHLER:
    print("FEHLER:", _FEHLER)
    raise SystemExit(1)
print("ALLE OK")
