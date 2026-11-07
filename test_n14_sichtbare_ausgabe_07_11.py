"""
ENFAL-Reparaufolgeauftrag (N14, 07.11.2026) -- Abnahme des bisher offenen
Ausgabepfads: erreicht der N14-Hinweis (zeitliche Alternative trotz km-seitig
`entfernt`em Wartungspunkt, s. app/laufleistung.py und
test_n13_n14_rc_f_lauf_07_11.py Teil G/H) auch die tatsächliche, sichtbare
KaufCheck-Ausgabe -- oder geht er auf dem Weg dorthin verloren?

Verfolgt wird GENAU der Produktionspfad aus app/kaufcheck.py:

    build_laufleistungskontext(req, insights)
        -> build_kaufaktionen(req, baureihe, motor_match, insights,
                              laufleistungskontext=laufleistungskontext)
        -> kanonischer_bericht(...) == app.kaufcheck_bericht.bericht(...)
        -> result["bericht"]                      <- das ist die sichtbare Ausgabe

Bewusst OHNE jeden Gemini-/LLM-Aufruf: `bericht()` ist in diesem gesamten
Pfad (Verkäuferfragen, Dokumente, Inserat-im-Vergleich) vollständig
deterministisch aus Insights/Aktionen/Entscheidung gerendert -- der
Modultext in app/kaufcheck.py hält ausdrücklich fest: "der Berichtstext ist
ausdruecklich KEINE Quelle" für das Modell, sondern umgekehrt. Eine
vorbereitete Modellantwort würde hier NICHTS beweisen (§5 des Auftrags) --
deshalb wird das Modell hier gar nicht erst aufgerufen, auch nicht gestubbt.

Isolation: temporärer DB-Pfad + Netzwerksperre vor jedem App-Import.

    python test_n14_sichtbare_ausgabe_07_11.py
"""
import os
import socket
import sys
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_n14_"), "test.db")


def _netzwerk_blockiert(self, *a, **kw):
    raise RuntimeError("Netzwerkzugriff blockiert -- dieser Test darf nie live gehen")


socket.socket.connect = _netzwerk_blockiert
sys.path.insert(0, ".")

from app.evidence import build_insights
from app.kaufaktionen import build_kaufaktionen
from app.kaufcheck_bericht import bericht
from app.laufleistung import build_laufleistungskontext
from app.models import TechnischeRecherche
from app.technical_research import _extrahiere_fakten
from app.vehicle_identity import VehicleIdentity

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


class Req:
    def __init__(self, **kw):
        for k in ("marke", "modell", "baujahr", "kilometerstand", "motor", "kraftstoff",
                  "preis_eur", "beschreibung", "freitext", "unfallfrei", "vorbesitzer",
                  "tuev_bis", "scheckheftgepflegt"):
            setattr(self, k, kw.get(k))
        self.ausstattung = kw.get("ausstattung") or []


# Derselbe real gesicherte Satz wie in test_n13_n14_rc_f_lauf_07_11.py Teil C/G
# (wörtlich aus test_recherche_volltext_integration.py übernommen).
VENTILSPIEL_AUSSAGE = ("Lexus RC F zusätzliche Inspektionen laut Lexus Care-Wartungsplan: "
                       "Ventilspiel (Inspektion mit Messfühler, LC 500, RC F) alle 90.000 km "
                       "oder 6 Jahre.")


def _aufbauen(kilometerstand: int, baujahr: int = 2017):
    """Baut insights/identity/kontext/aktionen für den Repro-Fall -- GENAU der
    Produktionspfad aus app/kaufcheck.py (DB-Miss simuliert, kein echter DB-Lookup
    nötig: baureihe/motor_match bleiben None)."""
    fakten = _extrahiere_fakten(
        [{"url": "https://tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf",
          "title": "Lexus Garantie- und Serviceheft (Schweiz, Deutsch)",
          "content": VENTILSPIEL_AUSSAGE}],
        "wartung", marke="Lexus", modell="RC F", baujahr=baujahr, abgelehnt=[])
    ventilspiel = next((f for f in fakten if f.bauteil == "Ventilspiel"), None)
    req = Req(marke="Lexus", modell="RC F", baujahr=baujahr, kilometerstand=kilometerstand,
             kraftstoff="Benzin", scheckheftgepflegt=None)   # Servicehistorie unbekannt
    web = TechnischeRecherche(ausgeloest_durch="db_miss", fakten=[ventilspiel] if ventilspiel else [])
    insights = build_insights(None, None, [], req, check_typ="kauf", web_recherche=web)
    identity = VehicleIdentity.from_check_context(None, None, req)
    kontext = build_laufleistungskontext(req, insights, heute_jahr=2026)
    aktionen = build_kaufaktionen(req, None, None, insights, laufleistungskontext=kontext)
    return req, identity, insights, kontext, aktionen


# ══════════════════════════════════════════════════════════════════════════
# TEIL I -- Der bisher ausgeblendete Fall (Teil G): 2017er Fahrzeug, 20.000 km
# (km-Status 'entfernt' für den 90.000-km-Punkt), reicht der Hinweis bis in
# den tatsächlichen `bericht()`-Markdowntext -- die sichtbare KaufCheck-
# Ausgabe (`result["bericht"]` in app/kaufcheck.py)?
# ══════════════════════════════════════════════════════════════════════════
print("=" * 78)
print("TEIL I) N14 -- sichtbare Ausgabe: Zeitalternative trotz km-seitig entferntem Punkt")
print("=" * 78)

req_i, identity_i, insights_i, kontext_i, aktionen_i = _aufbauen(kilometerstand=20_000)

check("I0) Vorbedingung erhalten: der Laufleistungskontext markiert den Punkt "
      "weiterhin ehrlich als km-seitig 'entfernt'",
      any(w.bauteil == "Ventilspiel" and w.status == "entfernt"
          for w in (kontext_i.wartungshinweise if kontext_i else [])))

fragen_i = [a for a in aktionen_i.verkaeuferfragen.fahrzeugspezifisch if "Ventilspiel" in a.titel]
dok_i = [a for a in aktionen_i.dokumente.fahrzeugspezifisch if "Ventilspiel" in a.titel]
check("I1) Trotz 'entfernt' entsteht eine Verkäuferfrage zu Ventilspiel "
      "(build_kaufaktionen, Produktionsfunktion -- kein Testdouble)",
      len(fragen_i) == 1, str([a.titel for a in aktionen_i.verkaeuferfragen.fahrzeugspezifisch]))
check("I2) ...und eine Dokumentenanforderung dazu", len(dok_i) == 1)
if fragen_i:
    check("I3) Der Aktionstext der Verkäuferfrage enthält die Zeitalternative ('6 Jahre')",
          "6 Jahre" in fragen_i[0].aktion, fragen_i[0].aktion)
    check("I4) ...und benennt den km-Anteil ausdrücklich als nicht einschlägig",
          "nicht einschlägig" in fragen_i[0].aktion, fragen_i[0].aktion)
if dok_i:
    check("I5) Der Dokumententext trägt dieselbe Information",
          "6 Jahre" in dok_i[0].aktion and "nicht einschlägig" in dok_i[0].aktion, dok_i[0].aktion)

# I6-I9: jetzt der GANZE sichtbare Bericht -- exakt die Funktion, die in
# app/kaufcheck.py als `result["bericht"]` an den Nutzer geht. KEIN Gemini-
# Aufruf, KEIN gestubbter Modelltext -- bericht() rendert deterministisch aus
# `aktionen_i` (s. Moduldocstring).
bericht_text = bericht(
    req_i, identity_i, None, None, insights_i, aktionen_i, [], "kaufen_nach_besichtigung",
    type("PA", (), {"label": None})(), False, kontext_i, None, ["Inserat"])

check("I6) Der VOLLE, sichtbare Bericht enthält die Zeitalternative ('6 Jahre')",
      "6 Jahre" in bericht_text)
check("I7) ...und den Hinweis, dass der Kilometerstand dafür nicht einschlägig ist",
      "nicht einschlägig" in bericht_text)
check("I8) Der Hinweis steht im Abschnitt 'Verkäuferfragen' DES sichtbaren Berichts "
      "(nicht nur irgendwo im Text)",
      "## Verkäuferfragen" in bericht_text
      and "6 Jahre" in bericht_text.split("## Verkäuferfragen", 1)[1].split("## ", 1)[0])
import re as _re

_ventilspiel_zeile = next(
    (zeile for zeile in bericht_text.splitlines()
     if "Ventilspiel" in zeile and "zeitliche Alternative" in zeile),
    "")
check("I9) KEINE Fälligkeits-/Entwarnungs-/Versäumnis-Begriffe GENAU in der "
      "Ventilspiel-Zeile des sichtbaren Berichts (Wortgrenze, damit harmlose "
      "Basis-Katalogtexte wie 'auffällig' den Nachweis nicht verfälschen)",
      bool(_ventilspiel_zeile) and not any(
          _re.search(rf"(?<![a-zäöüß])({w})(?![a-zäöüß])", _ventilspiel_zeile, _re.IGNORECASE)
          for w in ("fällig", "überfällig", "versäumt", "nicht durchgeführt", "fehlt")),
      _ventilspiel_zeile)

if _FEHLER:
    print(f"\n{len(_FEHLER)} FEHLER (Teil I):")
    for f in _FEHLER[-10:]:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nTEIL I GRUEN")


# ══════════════════════════════════════════════════════════════════════════
# TEIL J -- Gegenprobe am selben Ausgabepfad: junges Fahrzeug (Teil H), wo die
# Zeitalternative NICHT erreichbar ist. Der Laufleistungskontext bleibt dafür
# leer (kein Wartungshinweis), weshalb die bereits VORHER bestehende,
# generische Web-Wartungsfrage (`_aus_web_evidence`, Kategorie "web_wartung",
# existiert unabhängig von der Laufleistung) unverändert und UNGESCHÄRFT
# bestehen bleibt. Das ist der korrekte Soll-Zustand -- Ventilspiel aus der
# Frageliste verschwinden zu lassen wäre KEIN N14-Verhalten (die Webrecherche
# selbst ist mileage-unabhängig), sondern die geprüfte Erwartung ist: die
# N14-Zeitalternativen-Schärfung darf hier NICHT greifen. Ohne diese
# Gegenprobe wäre nicht geprüft, ob Teil I nur "immer schärfen" statt
# "gezielt schärfen" ist.
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 78)
print("TEIL J) Gegenprobe -- junges Fahrzeug: am vollen Ausgabepfad bleibt die Schärfung aus")
print("=" * 78)

req_j, identity_j, insights_j, kontext_j, aktionen_j = _aufbauen(kilometerstand=20_000, baujahr=2024)
check("J0) Der Laufleistungskontext liefert für dieses Fahrzeug KEINEN Wartungshinweis "
      "(Zeitalternative nicht erreichbar, km-Status bleibt 'entfernt')",
      not (kontext_j.wartungshinweise if kontext_j else []),
      str([(w.bauteil, w.status) for w in (kontext_j.wartungshinweise if kontext_j else [])]))

fragen_j = [a for a in aktionen_j.verkaeuferfragen.fahrzeugspezifisch if "Ventilspiel" in a.titel]
check("J1) Die (vorbestehende, von der Laufleistung unabhängige) Web-Wartungsfrage zu "
      "Ventilspiel existiert weiterhin genau EINMAL -- sie verschwindet nicht einfach",
      len(fragen_j) == 1, str([a.titel for a in aktionen_j.verkaeuferfragen.fahrzeugspezifisch]))
if fragen_j:
    check("J2) ...bleibt aber UNGESCHÄRFT: keine Zeitalternative, kein "
          "'nicht einschlägig' -- die N14-Schärfung hat hier korrekt NICHT gegriffen",
          "zeitliche Alternative" not in fragen_j[0].aktion
          and "nicht einschlägig" not in fragen_j[0].aktion, fragen_j[0].aktion)

bericht_text_j = bericht(
    req_j, identity_j, None, None, insights_j, aktionen_j, [], "kaufen_nach_besichtigung",
    type("PA", (), {"label": None})(), False, kontext_j, None, ["Inserat"])
check("J3) ...und auch im vollen sichtbaren Bericht trägt die Ventilspiel-bezogene "
      "Verkäuferfrage keine N14-Zeitalternativen-Formulierung",
      "zeitliche Alternative" not in bericht_text_j.split("## Verkäuferfragen", 1)[-1]
      .split("## ", 1)[0])

if _FEHLER:
    print(f"\n{len(_FEHLER)} FEHLER (Teil J):")
    for f in _FEHLER[-10:]:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nTEIL J GRUEN")

print("\nALLE N14-AUSGABEPFAD-TESTS GRUEN (Netzwerk gesperrt, temporäre DB:",
      os.environ["AUTO_KI_DB_PATH"], ")")
