"""
N21-T / N20 -- Warum-Karte Wartung: `Insight.kurzbegruendung` aus der strukturierten Kurzform des zugehörigen Wartungshinweises
(Zuordnung über die evidence_id), voller Quellenwortlaut unverändert in `beschreibung`; Satzgrenze vor angehängten Zusätzen.

Reale Wortlaute: lex-sw-00de.pdf (Lexus Schweiz) via test_n21_wartung_kurzform; [SYNTH] = synthetisch. Nichts hier ist ein
Live-Nachweis.

Netzsperre (extern; Loopback bleibt offen) + Temp-DB VOR den App-Importen. Lauf:
    python scripts/test_isolated.py test_n21_warum_kurzbegruendung.py
"""
import datetime as dt
import os
import socket
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="n21_warum_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_TMP, "chroma")
_orig_connect = socket.socket.connect


def _connect(self, addr, *a, **k):
    if isinstance(addr, tuple) and str(addr[0]) in ("127.0.0.1", "::1", "localhost"):
        return _orig_connect(self, addr, *a, **k)
    raise RuntimeError(f"Netzzugriff im Test gesperrt: {addr!r}")


def _cc(addr, *a, **k):
    raise RuntimeError(f"Netzzugriff im Test gesperrt: {addr!r}")


socket.socket.connect = _connect
socket.create_connection = _cc
print("Netzsperre (extern):", socket.socket.connect is _connect, "| Temp-DB:", os.environ["AUTO_KI_DB_PATH"])
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)

from app import database as _db  # noqa: E402
_db.ensure_tables()
import app.laufleistung as ll  # noqa: E402
from app.evidence import build_insights  # noqa: E402
from app.kaufaktionen import MAX_SPEZIFISCH_PRO_BEREICH, _wartung_dokument_text, _wartung_frage_text, build_kaufaktionen  # noqa: E402
from app.kaufcheck_bericht import bericht  # noqa: E402
from app.vehicle_identity import VehicleIdentity  # noqa: E402
from app.laufleistung import build_laufleistungskontext  # noqa: E402
from app.models import (EvidenceQuelle, Insight, KaufCheckRequest, TechnischeRecherche, Wartungshinweis,  # noqa: E402
                        WebFakt)
from app.schreibstil import bereinige_nutzertexte  # noqa: E402
from app import vergleichstabelle as vt  # noqa: E402
from app.hu_termin import ABGELAUFEN, PLAUSIBEL, UNGEWOEHNLICH_WEIT, UNLESBAR, bewerte_hu  # noqa: E402

_orig_alter = ll.fahrzeugalter
ll.fahrzeugalter = lambda bj, heute_jahr=None: _orig_alter(bj, heute_jahr=2026)


from types import SimpleNamespace  # noqa: E402
from app.evidence import _satzgrenze, _web_beschreibung  # noqa: E402
from app.kaufaktionen import ergaenze_wartung_kurzbegruendungen, wartung_kurzbegruendung  # noqa: E402

fehler: list[str] = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        fehler.append(name)


M = ll.WEITERE_STELLE_MARKER
MARKT = 'Die Quelle schränkt sich selbst ein ("falls für Ihren Markt zutreffend")'
FN = ("Motoröl und -filter: Bei Fahrzeugen mit Ölwechsel-Managementsystem: Motoröl- und -ölfilter wechseln (mit Fitness- "
      "und Sicherheitskontrollen), wenn die Ölwechsel-Hinweismeldung erscheint (zumindest alle 15.000 km / 1 Jahr), "
      "falls für Ihr Land zutreffend.")
ZS = ("Lexus Care-Zwischenservice: alle 15.000 km oder einmal jährlich, je nachdem, was zuerst eintritt. Dieser Service "
      "beinhaltet einen Öl- und Filterwechsel sowie zusätzliche Inspektionen von Lenkung, Bremsen, Beleuchtung und Reifen")
VOLL = (FN + " " + M + " " + ZS + " Die Quelle schränkt sich selbst ein (\"falls für Ihren Markt zutreffend\"). "
        "Ob das für Ihr Fahrzeug in Deutschland zutrifft, ist nicht bestätigt.")
VERBOTEN = ("fällig", "überfällig", "versäumt", "nicht durchgeführt", "rückstand von")
REQ = KaufCheckRequest(marke="Toyota", modell="Yaris", baujahr=2017, kilometerstand=82000, kraftstoff="Benzin",
                       ausstattung=[], vorbesitzer=2, scheckheftgepflegt=True)


def ins(i, titel, text, taetigkeit, kat="web_wartung"):
    return Insight(id=i, kategorie=kat, titel=titel, beschreibung=text, confidence="mittel", taetigkeit=taetigkeit)


print("== A: Zuordnung und Inhalt (Motoröl bedingt+Marktvorbehalt, Ventilspiel wiederkehrend, Riemen Erst-/Folge) ==")
I = [ins("w-oel", "Motoröl: x", VOLL, "ersetzen"),
     ins("w-vs", "Ventilspiel: x", "Ventilspiel Alle 90.000 km / 6 Jahre", "pruefen"),
     ins("w-rb", "Antriebsriemen: x", "Nebenaggregat-Antriebsriemen Alle 105.000 km / 6 Jahre Danach alle 15.000 km / jährlich",
         "pruefen")]
beschr_vorher = {i.id: i.beschreibung for i in I}
quellen_vorher = {i.id: [q.model_dump() for q in i.quellen] for i in I}
kontext = build_laufleistungskontext(REQ, I)
ka_vorher = build_kaufaktionen(REQ, None, None, I, laufleistungskontext=kontext).model_dump()
hinweise_vorher = [w.model_dump() for w in kontext.wartungshinweise]
ergaenze_wartung_kurzbegruendungen(I, kontext)
kb = {i.id: i.kurzbegruendung for i in I}
check("A1 alle drei Wartungs-Insights tragen eine Kurzbegründung", all(kb.values()), str(kb))
oel, vs, rb = kb["w-oel"], kb["w-vs"], kb["w-rb"]
check("A2 Motoröl: Tätigkeit + wiederkehrendes Intervall ('alle', kein 'Erstintervall'), bedingte Zusatzregel, Marktvorbehalt, "
      "Webherkunft, Durchführung unbekannt",
      oel.startswith("Wechsel: alle 15.000 km oder 1 Jahr.") and "Erstintervall" not in oel
      and "Marktbezug: Geltung in Deutschland nicht bestätigt" in oel
      and "zusätzliche Regel" in oel and "Voraussetzung" in oel and "ungeklärt" in oel
      and "Webrecherche" in oel and "Ob und wann die Arbeit durchgeführt wurde, ist unbekannt" in oel, oel)
check("A3 Ventilspiel: Prüfung, wiederkehrendes Intervall; Riemen: Erst- UND Folgeintervall getrennt",
      vs.startswith("Prüfung: alle 90.000 km oder 6 Jahre.")
      and rb.startswith("Prüfung: Erstintervall 105.000 km oder 6 Jahre, danach 15.000 km / 1 Jahr."), f"{vs} | {rb}")
check("A4 keine Vermischung: jede Kurzbegründung enthält nur ihr eigenes Intervall",
      "90.000" not in oel and "105.000" not in oel and "105.000" not in vs
      and "90.000" not in rb and "15.000 km oder 1 Jahr" not in rb, f"{oel} | {vs} | {rb}")
check("A5 kein Quellenabsatz/Zitat in der Kurzform (Wortlaut bleibt der Detailansicht vorbehalten) und keine Fälligkeits-/"
      "Versäumnisaussage; Hinweis auf den Detailbereich",
      all("Zwischenservice" not in t and "Ölwechsel-Managementsystem" not in t and "Reifen" not in t for t in kb.values())
      and all(not any(w in t.replace("weder eine Fälligkeit noch ein Versäumnis", "").lower() for w in VERBOTEN)
              for t in kb.values())   # die ausdrückliche Verneinung selbst ist keine Behauptung
      and all("weder eine Fälligkeit noch ein Versäumnis" in t and t.endswith("stehen darunter.") for t in kb.values()))
check("A6 voller Detailtext unverändert (beschreibung), Quellen unverändert",
      {i.id: i.beschreibung for i in I} == beschr_vorher and {i.id: [q.model_dump() for q in i.quellen] for i in I} == quellen_vorher)
check("A7 keine Änderung fachlicher Status/Hinweise/abgeleiteter Aktionen",
      [w.model_dump() for w in kontext.wartungshinweise] == hinweise_vorher
      and build_kaufaktionen(REQ, None, None, I, laufleistungskontext=kontext).model_dump() == ka_vorher)
ergaenze_wartung_kurzbegruendungen(I, kontext)
check("A8 idempotent: zweiter Lauf ergibt dieselben Texte", {i.id: i.kurzbegruendung for i in I} == kb)

print("\n== B: Fehlend / leer / keine eindeutige Zuordnung -> keine fremde Kurzbegründung ==")
w0 = kontext.wartungshinweise[0]
check("B1 Hinweis ohne Kurzform (ältere Antwort) -> None",
      wartung_kurzbegruendung(w0.model_copy(update={"kurz": None})) is None
      and wartung_kurzbegruendung(w0.model_copy(update={"intervall_kurz": None})) is None)
fremd = Insight(id="w-unbekannt", kategorie="web_wartung", titel="Kupplung: x", beschreibung="Kupplung alle 60.000 km",
                confidence="mittel")
ohne = Insight(id="w-oel", kategorie="schwachstelle", titel="Motoröl", beschreibung="x", confidence="mittel")
ergaenze_wartung_kurzbegruendungen([fremd, ohne], kontext)
check("B2 Insight-ID ohne passende evidence_id bleibt None; eine andere Kategorie mit gleicher ID wird nicht beschriftet",
      fremd.kurzbegruendung is None and ohne.kurzbegruendung is None)
ergaenze_wartung_kurzbegruendungen([fremd], SimpleNamespace(wartungshinweise=None))
ergaenze_wartung_kurzbegruendungen([fremd], None)
check("B3 kein/leerer Wartungskontext: kein Fehler, kein Feld", fremd.kurzbegruendung is None)
leer = Insight(id="z", kategorie="wartung", titel="t", beschreibung="b", confidence="hoch")
check("B4 Standardwert des Feldes ist None (null/fehlend wie bisher)",
      leer.kurzbegruendung is None and "kurzbegruendung" in leer.model_dump())

print("\n== C: Kategorie-Aliase (wartung / web_wartung) ==")
db_i = Insight(id="db-1", kategorie="wartung", titel="Zahnriemen: x", beschreibung="x", confidence="mittel")
fake_w = w0.model_copy(update={"evidence_id": "db-1", "herkunft": "db_wartung"})
ergaenze_wartung_kurzbegruendungen([db_i], SimpleNamespace(wartungshinweise=[fake_w]))
check("C1 'wartung' (DB) wird wie 'web_wartung' behandelt; Webherkunftssatz nur bei web_wartung",
      db_i.kurzbegruendung is not None and "Webrecherche" not in db_i.kurzbegruendung and "Webrecherche" in oel)

print("\n== D: N20 Satzgrenze ==")
check("D1 Quellenwortlaut ohne Schlusszeichen -> genau EIN Punkt davor", _satzgrenze("... und Reifen") == "... und Reifen.")
check("D2 vorhandene Schlusszeichen werden respektiert (kein Doppelzeichen)",
      all(_satzgrenze(t) == t for t in ("Ende.", "Ende!", "Ende?", "Ende…", "Ende:", "Ende;", "„Ende.“", "Ende.\"")))
check("D3 Anführungszeichen OHNE Satzzeichen davor bekommt einen Punkt dahinter, Wortlaut unverändert",
      _satzgrenze("„Ende“") == "„Ende“." and _satzgrenze("") == "")
fakt = SimpleNamespace(aussage=ZS, kategorie="wartung", beleglage=None, applicability=None, geltung_fuer_fahrzeug=None,
                       geltungsbereich=None, regionale_geltung="unresolved", herkunftsregion=MARKT,
                       technische_bedingung_status=None)
besch = _web_beschreibung(fakt, 2017)
check("D4 [ECHT-Wortlaut] 'Reifen' + angehängter Marktvorbehalt: 'Reifen. Die Quelle schränkt sich selbst ein'; "
      "Wortlaut vollständig erhalten",
      "Reifen. Die Quelle schränkt sich selbst ein" in besch and besch.startswith(ZS) and ".." not in besch, besch)
fakt2 = SimpleNamespace(**{**fakt.__dict__, "aussage": ZS + "."})
besch2 = _web_beschreibung(fakt2, 2017)
check("D5 mit bereits vorhandenem Schlusspunkt: keine Doppelzeichen, derselbe Aufbau",
      "Reifen. Die Quelle schränkt sich selbst ein" in besch2 and ".." not in besch2 and besch2.count("Reifen.") == 1, besch2)

print("\n" + ("ALLE GRÜN" if not fehler else f"{len(fehler)} FEHLER: {fehler}"))
sys.exit(1 if fehler else 0)
