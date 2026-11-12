"""
ENFAL -- Abnahme N5/N12/N13 (Wartungsregime, Motoröl, Zeitbegründung), Lexus RC F 2017, 82.000 km.

Befund (Offline-Reproduktion, KEIN Live-Nachweis -- der aktuelle Export und die Live-Rohtexte liegen nicht vor):
  U1 die Fußnote *1 ("Bei Fahrzeugen mit Ölwechsel-Managementsystem ... falls für Ihr Land zutreffend") ersetzt die
     Tabellenzeile "Motoröl und -filter*1 E E";
  U2 die unbedingte Grundregel ("Lexus Care-Zwischenservice: alle 15.000 km oder einmal jährlich ...") steht nur noch
     hinter dem Trennmarker "Weitere Stelle im selben Dokument:";
  U3 die Bedingung der Fußnote wirkte auf den GANZEN Text: Kilometer unbedingt "erreicht", Zeit dagegen wegen der
     Bedingung offen -- für beide Regeln zugleich;
  U4 als Grund der offenen Zeit stand immer "ohne den genauen Erstzulassungsmonat";
  U5 "einmal jährlich" wurde nicht als Zeitschwelle erkannt.
Behoben in app/laufleistung.py (regimegetrennte Auswertung, gemeinsame Schwellenlogik) und app/kaufaktionen.py
(Aktionsrang, kompakte Zeile). Der Erzeuger ändert sich nur um die gemeinsame Konstante `WEITERE_STELLE_MARKER`.

N13 (Tätigkeiten) braucht keinen fachlichen Fix und wird hier nur abgesichert.

Echte Quelle: lex-sw-00de.pdf (Lexus Schweiz, Seiten 4/15/16/17), lokal aus ~/Downloads. Synthetische Gegenproben
sind mit [SYNTH] gekennzeichnet. Netzsperre (extern) und Temp-DB stehen VOR den App-Importen.
"""
import ast
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="wartung_regime_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_TMP, "chroma")
_orig_connect = socket.socket.connect


def _sperre(self, addr, *a, **k):
    host = addr[0] if isinstance(addr, tuple) else str(addr)
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise RuntimeError("Netzzugriff im Test gesperrt")
    return _orig_connect(self, addr, *a, **k)


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


socket.socket.connect = _sperre
socket.create_connection = _gesperrt
print("Netzsperre extern:", socket.socket.connect is _sperre, "| Temp-DB:", os.environ["AUTO_KI_DB_PATH"])
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)

from app import database as _db  # noqa: E402
_db.ensure_tables()
import app.laufleistung as ll  # noqa: E402
import app.kaufaktionen as ka_mod  # noqa: E402
import app.technical_research as tr  # noqa: E402
from app.evidence import build_insights  # noqa: E402
from app.kaufaktionen import build_kaufaktionen  # noqa: E402
from app.laufleistung import build_laufleistungskontext  # noqa: E402
from app.models import Insight, KaufCheckRequest, TechnischeRecherche  # noqa: E402
from test_rc_f_lexus_care_wartungsplan_real import LEXUS_CH_MODELLUEBERSICHT  # noqa: E402

fehler: list[str] = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        fehler.append(name)


HIER = Path(__file__).parent
M = ll.WEITERE_STELLE_MARKER
HEUTE = 2026
_orig_alter = ll.fahrzeugalter
ll.fahrzeugalter = lambda bj, heute_jahr=None: _orig_alter(bj, heute_jahr=HEUTE)

# ── echte Quelle ─────────────────────────────────────────────────────────────────────────────
pdf = Path(os.path.expanduser("~/Downloads/lex-sw-00de.pdf"))
SEITEN: dict[int, str] = {}
if pdf.exists():
    from pypdf import PdfReader
    _r = PdfReader(str(pdf))
    SEITEN = {n: _r.pages[n - 1].extract_text() for n in (4, 15, 16, 17)}
URL = LEXUS_CH_MODELLUEBERSICHT["url"].split("#")[0]
FN_ECHT = ("Motoröl und -filter: Bei Fahrzeugen mit Ölwechsel-Managementsystem: Motoröl- und -ölfilter wechseln (mit Fitness- "
           "und Sicherheitskontrollen), wenn die Ölwechsel-Hinweismeldung erscheint (zumindest alle 15.000 km / 1 Jahr), "
           "falls für Ihr Land zutreffend.")
ZS_ECHT = ("Lexus Care-Zwischenservice: alle 15.000 km oder einmal jährlich, je nachdem, was zuerst eintritt. Dieser Service "
           "beinhaltet einen Öl- und Filterwechsel sowie zusätzliche Inspektionen von Lenkung, Bremsen, Beleuchtung und Reifen")
REQ = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000, motor="5.0 V8, Heckantrieb",
                       kraftstoff="Benzin", leistung_ps=477, getriebe="Automatik", ausstattung=[],
                       vorbesitzer=2, scheckheftgepflegt=True)


def extraktion(text, km=82000, bj=2017):
    ab: list[dict] = []
    f = tr._extrahiere_fakten([{"url": URL, "title": "WARTUNG UND GARANTIE", "content": text}], "wartung",
                              marke="Lexus", modell="RC F", baujahr=bj, abgelehnt=ab, fuel_ziel="benzin", kilometerstand=km)
    return f, ab


def kette(fakten, req=REQ):
    ins = build_insights(None, None, [], req, check_typ="kauf",
                         web_recherche=TechnischeRecherche(ausgeloest_durch="db_miss", fakten=fakten), canonical_rueckrufe=[])
    lk = build_laufleistungskontext(req, ins, heute_jahr=HEUTE)
    return ins, lk, build_kaufaktionen(req, None, None, ins, laufleistungskontext=lk)


def hinweis_aus(text, km=82000, alter=9, technik=None, titel="Motoröl: x"):
    i = Insight(id="x", kategorie="web_wartung", titel=titel, beschreibung=text, confidence="mittel",
                technische_bedingung=technik, technische_bedingung_status="unresolved" if technik else None)
    h = ll._wartungshinweise([i], km, alter)
    return h[0] if h else None


VERBOTEN = ("fällig", "überfällig", "versäumt", "nicht durchgeführt", " fehlt ")


def sauber(t):
    return not any(w in t.lower() for w in VERBOTEN)


# ══ A. Regelzerlegung ════════════════════════════════════════════════════════════════════════
print("\n== A Regelzerlegung (echte Wortlaute) ==")
fn = ll.WartungsRegel(FN_ECHT)
zs = ll.WartungsRegel(ZS_ECHT)
check("A1 Fußnote *1: bedingt, 15.000 km ODER 1 Jahr", fn.bedingt and fn.km == (15000, None) and fn.zeit == (1, "Jahre")
      and fn.verbinder == "oder", str((fn.bedingt, fn.km, fn.zeit, fn.verbinder)))
check("A2 Kopfzeile Zwischenservice: UNBEDINGT, 15.000 km ODER einmal jährlich (=1 Jahr); 'je nachdem' ist keine Bedingung",
      (not zs.bedingt) and zs.km == (15000, None) and zs.zeit == (1, "Jahre") and zs.verbinder == "oder",
      str((zs.bedingt, zs.km, zs.zeit, zs.verbinder)))
rs = ll.zerlege_regeln(FN_ECHT + " " + M + " " + ZS_ECHT)
check("A3 Trennmarker liefert beide Fundstellen samt Bedingungen zurück (Fußnote zuerst)",
      len(rs) == 2 and rs[0].bedingt and not rs[1].bedingt and "Ölwechsel-Managementsystem" in rs[0].text
      and "falls für Ihr Land zutreffend" in rs[0].text and rs[1].text.startswith("Lexus Care-Zwischenservice"))
rs2 = ll.zerlege_regeln(ZS_ECHT + ". " + M + " " + FN_ECHT)
check("A4 umgekehrte Reihenfolge: dieselben zwei Regeln mit denselben Bedingungen",
      len(rs2) == 2 and not rs2[0].bedingt and rs2[1].bedingt
      and {r.signatur() for r in rs} == {r.signatur() for r in rs2})
check("A5 gleiche Fundstellen sind EINE Regel (kein zweiter, unabhängiger Beleg)",
      len(ll.eindeutige_regeln(ll.zerlege_regeln(ZS_ECHT + ". " + M + " " + ZS_ECHT))) == 1
      and len(ll.eindeutige_regeln(ll.zerlege_regeln(
          "Motoröl alle 15.000 km oder einmal jährlich. " + M + " Motoröl alle 15.000 km / 1 Jahr."))) == 1)
check("A6 Marktvorbehalt-/Hüllentext der Auswertung gehört nicht zur letzten Fundstelle",
      len(ll.zerlege_regeln(FN_ECHT + " " + M + " " + ZS_ECHT + " Die Quelle schränkt sich selbst ein (\"falls für Ihren Markt "
                            "zutreffend\"). Ob das für Ihr Fahrzeug in Deutschland zutrifft, ist nicht bestätigt.")) == 2
      and not ll.zerlege_regeln(FN_ECHT + " " + M + " " + ZS_ECHT + " Die Quelle schränkt sich selbst ein (\"falls für Ihren Markt "
                                "zutreffend\").")[1].bedingt)

# ══ B. Zeitschwellen: jährlich, Erstintervall, Folgeintervall ═══════════════════════════════
print("\n== B Zeitformen und Erstintervall ==")
for t in ("alle 15.000 km oder einmal jährlich", "alle 15.000 km oder jährlich", "alle 15.000 km oder alle 12 Monate",
          "alle 15.000 km oder jedes Jahr", "alle 15.000 km oder 1 Jahr"):
    r = ll.WartungsRegel(t)
    check(f"B1 '{t}' -> 1 Jahr/12 Monate, ODER", r.zeit in ((1, "Jahre"), (12, "Monate")) and r.verbinder == "oder", str(r.zeit))
check("B2 'jährlichen Fitness-Check' (Wortteil) ist KEINE Zeitschwelle", ll.parse_zeitschwelle("Der jährlichen Fitness-Check") is None
      and ll.parse_zeitschwelle("jährliche Prüfung") is None)
rb = ll.WartungsRegel("Nebenaggregat-Antriebsriemen Alle 105.000 km / 6 Jahre Danach alle 15.000 km / jährlich")
check("B3 [ECHT] 'Danach alle 15.000 km / jährlich' wird NICHT Erstintervall: 105.000 km / 6 Jahre bleibt",
      rb.km == (105000, None) and rb.zeit == (6, "Jahre") and rb.verbinder == "oder")
check("B4 [SYNTH] 'alle 15.000 km. Danach jährlich': jährlich steht nur im Folgeintervall -> keine Zeitschwelle",
      ll.WartungsRegel("Motoröl alle 15.000 km. Danach jährlich").zeit is None)
check("B5 [SYNTH] 'alle 6 Jahre. Danach alle 15.000 km': der Kilometerwert des Folgeintervalls erzeugt keinen Hinweis",
      hinweis_aus("Bremsflüssigkeit alle 6 Jahre. Danach alle 15.000 km", km=82000, alter=9) is None)
check("B6 [SYNTH] UND-Verknüpfung bleibt: '30.000 km und 2 Jahre' -> beide müssen erreicht sein",
      ll.schwelle_belegbar_erreicht_alter("alle 30.000 km und 2 Jahre", 82000, 9)
      and not ll.schwelle_belegbar_erreicht_alter("alle 30.000 km und 2 Jahre", 82000, None)
      and not ll.schwelle_belegbar_erreicht_alter("alle 30.000 km und 2 Jahre", 5000, 9))
check("B7 [SYNTH] ODER bleibt: unbekanntes Alter hebt eine erreichte Kilometerschwelle nicht auf",
      ll.schwelle_belegbar_erreicht_alter("alle 15.000 km oder einmal jährlich", 82000, None)
      and ll.schwelle_belegbar_erreicht_alter("6 Jahre oder 105.000 km", 5000, 9))

# ══ C. Hinweis je Regime (Einzelobjekt) ═════════════════════════════════════════════════════
print("\n== C Hinweis je Regime ==")
voll = FN_ECHT + " " + M + " " + ZS_ECHT
h = hinweis_aus(voll)
check("C1 Grundregel + Fußnote: Grundregel eigenständig, Kilometer UND Zeit als erreicht benannt (Alter mindestens 8)",
      h is not None and "mindestens einmal erreicht" in h.hinweis and "(oder 1 Jahr) ist nach dem Baujahr bereits erreicht" in h.hinweis
      and "mindestens 8 Jahre" in h.hinweis, h.hinweis if h else "")
check("C2 ... die offene Zeit wird NICHT mit dem fehlenden Erstzulassungsmonat begründet", h is not None and "bleibt deshalb offen" not in h.hinweis
      and "nicht bestimmen" not in h.hinweis)
check("C3 ... die bedingte Zusatzregel samt ungeklärter Voraussetzung und Landesvorbehalt bleibt sichtbar",
      h is not None and "Zusätzlich nennt die Quelle eine bedingte Regel" in h.hinweis and "Ölwechsel-Managementsystem" in h.hinweis
      and "falls für Ihr Land zutreffend" in h.hinweis and "ungeklärt" in h.hinweis and "nicht ausgewertet" in h.hinweis, h.hinweis if h else "")
check("C4 ... letzte Durchführung/Wiederholung bleiben ausdrücklich unbekannt, keine Fälligkeit",
      h is not None and "nicht hervor" in h.hinweis and "Rückstand" in h.hinweis and sauber(h.hinweis))
h2 = hinweis_aus(ZS_ECHT + ". " + M + " " + FN_ECHT)
check("C5 umgekehrte Reihenfolge (Grundregel zuerst): gleiche Aussage, gleiche Zusatzregel",
      h2 is not None and h is not None and h2.status == h.status and h2.punkt_km == h.punkt_km
      and "(oder 1 Jahr) ist nach dem Baujahr bereits erreicht" in h2.hinweis
      and "Zusätzlich nennt die Quelle eine bedingte Regel" in h2.hinweis and "Ölwechsel-Managementsystem" in h2.hinweis)
h3 = hinweis_aus(ZS_ECHT)
check("C6 nur Grundregel: eigenständig, ohne Zusatzregel", h3 is not None and "bereits erreicht" in h3.hinweis
      and "bedingte Regel" not in h3.hinweis and "(oder 1 Jahr)" in h3.hinweis)
h4 = hinweis_aus(FN_ECHT)
check("C7 nur Fußnote: weder Kilometer noch Zeit als erreicht ausgegeben, Voraussetzung ungeklärt benannt",
      h4 is not None and "nur unter einer Voraussetzung" in h4.hinweis and "ungeklärt" in h4.hinweis
      and "weder als erreicht noch als nicht erreicht" in h4.hinweis and "Träfe die Voraussetzung zu" in h4.hinweis
      and "wurde bei dieser Laufleistung rein rechnerisch" not in h4.hinweis
      and "ist nach dem Baujahr bereits erreicht" not in h4.hinweis and sauber(h4.hinweis), h4.hinweis if h4 else "")
check("C8 nur Fußnote: Rang ohne belegte Schwelle (Auswahl- und Aktionsrang)",
      not ll.schwelle_belegbar_erreicht_alter(FN_ECHT, 82000, 9) and ll.ist_nur_bedingt(FN_ECHT)
      and not ll.ist_nur_bedingt(voll) and ll.schwelle_belegbar_erreicht_alter(voll, 82000, 9))
# Zeitbegründungen nach tatsächlichem Grund
hu = hinweis_aus(ZS_ECHT, alter=None)
check("C9 Baujahr unbekannt: Grund 'ohne bekanntes Baujahr', nicht der Erstzulassungsmonat",
      hu is not None and "ohne bekanntes Baujahr" in hu.hinweis and "Erstzulassungsmonat" not in hu.hinweis, hu.hinweis if hu else "")
hb = hinweis_aus("Ventilspiel alle 90.000 km / 6 Jahre", km=82000, alter=6)
check("C10 Altersgrenze im Unsicherheitsbereich (Alter 6, Schwelle 6): hier ist der Erstzulassungsmonat entscheidend",
      hb is not None and "Erstzulassungsmonat" in hb.hinweis and "bleibt deshalb offen" in hb.hinweis, hb.hinweis if hb else "")
hn = hinweis_aus("Ventilspiel alle 90.000 km / 6 Jahre", km=82000, alter=2)
check("C11 Alter 2, Schwelle 6: rechnerisch noch nicht erreicht, kein Monatsvorbehalt", hn is not None
      and "rechnerisch noch nicht erreicht" in hn.hinweis and "Erstzulassungsmonat" not in hn.hinweis, hn.hinweis if hn else "")
ht = hinweis_aus("Inverter-Kühlmittel Alle 90.000 km / 4 Jahre", km=82000, alter=9, technik="nur Hybrid- und Hybrid Plug-in-Modelle",
                 titel="Inverter-Kühlmittel: x")
check("C12 technische Voraussetzung ungeklärt: sie ist der Grund der offenen Zeitaussage",
      ht is not None and "ungeklärten technischen Voraussetzung" in ht.hinweis and "nur Hybrid- und Hybrid Plug-in-Modelle" in ht.hinweis
      and "bleibt deshalb offen" not in ht.hinweis, ht.hinweis if ht else "")
ht2 = hinweis_aus("Inverter-Kühlmittel Alle 90.000 km / 4 Jahre", km=5000, alter=9, technik="nur Hybrid- und Hybrid Plug-in-Modelle",
                  titel="Inverter-Kühlmittel: x")
check("C12b technische Voraussetzung ungeklärt, Kilometer entfernt: Zeit-Offenheit mit der Voraussetzung begründet, Kilometeranteil nicht einschlägig",
      ht2 is not None and "nicht einschlägig" in ht2.hinweis and "ungeklärten technischen Voraussetzung" in ht2.hinweis
      and "Erstzulassungsmonat" not in ht2.hinweis, ht2.hinweis if ht2 else "kein Hinweis")
hk = hinweis_aus("Motoröl alle 15.000 km")
check("C13 nur Kilometer bekannt: keine Zeitaussage erfunden", hk is not None and "zeitliche" not in hk.hinweis and "bereits erreicht" not in hk.hinweis.replace("mindestens einmal erreicht", ""),
      hk.hinweis if hk else "")
hk2 = hinweis_aus("Motoröl alle 15.000 km. Danach jährlich")
check("C14 [SYNTH] jährliches Folgeintervall erzeugt keine Zeitaussage zum Erstintervall", hk2 is not None and "zeitliche" not in hk2.hinweis)
check("C15 Marktvorbehalt (Dokumentebene) bleibt ungeklärt sichtbar, getrennt von Bedingung und Ausstattung",
      "Die Quelle schränkt sich selbst ein" in (voll + " Die Quelle schränkt sich selbst ein")
      and ll.hat_quellenvorbehalt(voll + " Die Quelle schränkt sich selbst ein (\"falls für Ihren Markt zutreffend\")."))

# Mehrdeutigkeit
print("\n== D Mehrere unterschiedliche Regeln ==")
hm = hinweis_aus("Motoröl alle 10.000 km. " + M + " Motoröl alle 15.000 km oder 1 Jahr.")
check("D1 [SYNTH] zwei verschiedene Grundregeln: keine Erstauswahl, beide genannt, Mehrdeutigkeit ausgewiesen",
      hm is not None and "mehrere unterschiedliche Intervalle" in hm.hinweis and "10.000 km" in hm.hinweis and "15.000 km oder 1 Jahr" in hm.hinweis
      and "nicht kombiniert" in hm.hinweis, hm.hinweis if hm else "")
check("D2 ... keine Kilometer-/Zeit-Kombination über Regeln hinweg und keine Zeitaussage",
      hm is not None and "ist nach dem Baujahr bereits erreicht" not in hm.hinweis and "10.000 km oder 1 Jahr" not in hm.hinweis)
check("D3 ... Rang nur, wenn JEDE Grundregel erreicht ist (keine willkürliche Auswahl)",
      ll.schwelle_belegbar_erreicht_alter("Motoröl alle 10.000 km. " + M + " Motoröl alle 15.000 km oder 1 Jahr.", 12000, 9)
      and not ll.schwelle_belegbar_erreicht_alter("Motoröl alle 10.000 km. " + M + " Motoröl alle 15.000 km und 5 Jahre.", 12000, 3)
      and not ll.schwelle_belegbar_erreicht_alter("Motoröl alle 10.000 km. " + M + " Motoröl alle 50.000 km.", 12000, 9))
hm2 = hinweis_aus("Motoröl alle 15.000 km. " + M + " Motoröl alle 15.000 km oder 1 Jahr.")
check("D4 [SYNTH] gleicher Kilometerwert, aber verschiedene Zeitangaben: ebenfalls mehrdeutig (Zeit nicht aus Regel B zu A)",
      hm2 is not None and "mehrere unterschiedliche Intervalle" in hm2.hinweis)

hb2 = hinweis_aus("Motoröl bei Hinweismeldung alle 10.000 km. " + M + " Motoröl falls verbaut alle 20.000 km oder 1 Jahr.")
check("D6 [SYNTH] mehrere verschiedene NUR bedingte Regeln: Mehrdeutigkeit und Voraussetzung ausgewiesen, nichts als erreicht",
      hb2 is not None and "mehrere unterschiedliche Intervalle" in hb2.hinweis and "an eine Voraussetzung geknüpft" in hb2.hinweis
      and "wurde bei dieser Laufleistung rein rechnerisch" not in hb2.hinweis and sauber(hb2.hinweis), hb2.hinweis if hb2 else "")

# Einsatzbedingungen
print("\n== E Einsatzbedingungen ==")
check("E1 [ECHT] 'Für normale Einsatzbedingungen' ist Geltungsbereich, keine Bedingung der Regel",
      not ll._BEDINGT.search("Für normale Einsatzbedingungen") and ll.WartungsRegel("Für normale Einsatzbedingungen Ölfilter alle 30.000 km").einsatz == "normal"
      and not ll.WartungsRegel("Für normale Einsatzbedingungen Ölfilter alle 30.000 km").bedingt)
check("E2 [ECHT S.2.1] 'Bei erschwerten Betriebsbedingungen ... kürzere Serviceintervalle' ist eine bedingte Regel",
      ll.WartungsRegel("Bei erschwerten Betriebsbedingungen kann Ihr Fahrzeug u. U. kürzere Serviceintervalle sowie einen "
                       "größeren Serviceumfang erfordern, als in diesem Heft angegeben.").bedingt)
he = hinweis_aus("Motoröl alle 15.000 km oder 1 Jahr (für normale Einsatzbedingungen). " + M +
                 " Motoröl bei erschwerten Einsatzbedingungen alle 7.500 km.")
check("E3 [SYNTH] normale Einsatzbedingungen: Grundregel mit benanntem Geltungsbereich; erschwerte Regel sichtbar, nicht ausgewertet",
      he is not None and "normale Einsatzbedingungen" in he.hinweis and "unbekannt" in he.hinweis
      and "bedingte Regel" in he.hinweis and "7.500 km" in he.hinweis and "nicht ausgewertet" in he.hinweis, he.hinweis if he else "")
check("E4 ... und es wird NICHT verallgemeinert, erschwerte Bedingungen beträfen nur kürzere Intervalle",
      he is not None and "kürzer" not in he.hinweis.lower() and "nur" not in he.hinweis.lower().replace("nur unter", ""), he.hinweis if he else "")

# Sortierung
i_cond = Insight(id="a", kategorie="web_wartung", titel="Aaa: x", beschreibung=FN_ECHT, confidence="mittel")
i_ok = Insight(id="b", kategorie="web_wartung", titel="Zzz: x", beschreibung="Zzz alle 80.000 km", confidence="mittel")
reihe = [w.bauteil for w in ll._wartungshinweise([i_cond, i_ok], 82000, 9)]
check("D5 eine nur bedingte Regel verdrängt keine belegte Grundregel im Rang", reihe[0] == "Zzz", str(reihe))

# ══ F. Kette mit echten PDF-Seiten (Erzeuger -> Insight -> Laufleistung -> Fragen/Dokumente) ═══
print("\n== F Echte PDF-Seiten, produktionsnaher Pfad ==")
if SEITEN:
    seiten = "\n\n".join(SEITEN[n] for n in (4, 15, 16, 17))
    fk, ab = extraktion(seiten)
    namen = [f.bauteil for f in fk]
    print("  Auswahl:", namen)
    check("F1 fünf Wartungen: Ventilspiel, Motoröl, Hinterachsdifferenzialöl, Antriebsriemen, Automatikgetriebeöl",
          sorted(namen) == sorted(["Ventilspiel", "Motoröl", "Hinterachsdifferenzialöl", "Nebenaggregat-Antriebsriemen",
                                   "Automatikgetriebeöl"]), str(namen))
    mo = next((f for f in fk if f.bauteil == "Motoröl"), None)
    check("F2 Erzeuger: Motoröl trägt BEIDE Fundstellen (Fußnote UND Kopfzeile) über die gemeinsame Marker-Konstante",
          mo is not None and tr.WEITERE_STELLE_MARKER is ll.WEITERE_STELLE_MARKER and mo.aussage.count(M) == 1
          and len(ll.zerlege_regeln(mo.aussage)) == 2, mo.aussage[:200] if mo else "")
    rm = ll.zerlege_regeln(mo.aussage) if mo else []
    check("F3 Rückgewinnung am Trennmarker: genau eine bedingte (Fußnote) und eine unbedingte Regel (Kopfzeile), Bedingungen unverändert",
          len(rm) == 2 and sum(r.bedingt for r in rm) == 1
          and any(r.bedingt and "Ölwechsel-Managementsystem" in r.text and "falls für Ihr Land zutreffend" in r.text for r in rm)
          and any((not r.bedingt) and r.text.startswith("Lexus Care-Zwischenservice") for r in rm))
    check("F4 Marktvorbehalt des Dokuments bleibt ungeklärt (regionale_geltung)", mo is not None and mo.regionale_geltung == "unresolved")
    # U1 nicht durch Zusatztext verdecken: ohne den Kopfzeilen-Satz bleibt NUR die Fußnote -> ehrlich bedingt
    seiten_ohne = seiten.replace("Lexus Care-Zwischenservice: alle 15.000 km oder einmal jährlich, je nachdem, was zuerst eintritt. ", "", 1)
    check("F5 [SYNTH-Variante der echten Seite] Kopfzeilen-Satz entfernt (Tabellenzeile 'Motoröl und -filter*1 E E' bleibt)",
          seiten_ohne != seiten and "Zwischenservice: alle 15.000 km oder einmal" not in seiten_ohne.split("Für normale Einsatzbedingungen")[0].split("2.9. LEXUS")[-1])
    fk_o, _ = extraktion(seiten_ohne)
    mo_o = next((f for f in fk_o if f.bauteil == "Motoröl"), None)
    rm_o = ll.zerlege_regeln(mo_o.aussage) if mo_o else []
    check("F6 ... dann trägt Motoröl nur noch die bedingte Fußnote: die Tabellenzeile liefert KEIN Intervall ('E E' ist Tätigkeits-"
          "marker); nichts wird als unbedingt erfunden",
          mo_o is not None and len(rm_o) == 1 and rm_o[0].bedingt and "E E" not in mo_o.aussage and M not in mo_o.aussage,
          mo_o.aussage[:160] if mo_o else "kein Motoröl")
    ins, lk, ka = kette(fk)
    hs = {w.bauteil: w for w in lk.wartungshinweise}
    mh = hs.get("Motoröl")
    check("F7 Hinweis Motoröl (echte Seiten): Grundregel eigenständig ausgewertet, Fußnote als bedingte Zusatzregel sichtbar",
          mh is not None and "(oder 1 Jahr) ist nach dem Baujahr bereits erreicht" in mh.hinweis
          and "Zusätzlich nennt die Quelle eine bedingte Regel" in mh.hinweis and "bleibt deshalb offen" not in mh.hinweis
          and "Die Quelle schränkt die Angabe selbst ein" in mh.hinweis, mh.hinweis[:300] if mh else "")
    check("F8 Insight: beide Fundstellen = EINE Quelle (keine unabhängigen Belege vorgetäuscht)",
          len([i for i in ins if i.titel.startswith("Motoröl")][0].quellen) == 1)
    for bereich in ("verkaeuferfragen", "dokumente"):
        spez = getattr(ka, bereich).fahrzeugspezifisch
        mo_a = next((a for a in spez if "motoröl" in (a.titel + a.aktion).lower() and a.kategorie in ("wartung", "web_wartung")), None)
        check(f"F9 {bereich}: Motoröl-Aktion trägt Grundregel-Zeit UND bedingte Zusatzregel; Wechsel-Wortlaut",
              mo_a is not None and "bereits erreicht" in mo_a.aktion and "bedingte Regel" in mo_a.aktion
              and ("gewechselt" in mo_a.titel or "Wechselnachweis" in mo_a.titel), (mo_a.aktion[:200] if mo_a else "fehlt"))
        check(f"F10 {bereich}: genau 6 Einzelaktionen (Limit), keine Evidenz-ID doppelt, Evidenz nur existierender Insights",
              len(spez) <= ka_mod.MAX_SPEZIFISCH_PRO_BEREICH and len([e for a in spez for e in a.evidence_ids]) ==
              len({e for a in spez for e in a.evidence_ids}) and {e for a in spez for e in a.evidence_ids} <= {i.id for i in ins})
        check(f"F11 {bereich}: alle fünf Wartungen konkret sichtbar, Evidenz ihrer Insights erhalten",
              all(any(b.lower() in (a.titel + " " + a.aktion).lower() for a in spez) for b in namen)
              and {i.id for i in ins if i.kategorie == "web_wartung"} <= {e for a in spez for e in a.evidence_ids})
        texte = " ".join(a.titel + " " + a.aktion for a in spez if a.kategorie in ("wartung", "web_wartung"))  # HU-Texte sind kein Wartungsbezug
        check(f"F12 {bereich}: keine Fälligkeit, kein Steuerzeichen (Wartungsaktionen)", sauber(texte) and all(ord(c) >= 32 or c in "\n\t" for c in texte),
              str([w for w in VERBOTEN if w in texte.lower()]) + str(sorted({hex(ord(c)) for c in texte if ord(c) < 32 and c not in "\n\t"}))
              + " | " + " ".join(texte.lower().split())[max(0, " ".join(texte.lower().split()).find("fällig") - 80):][:200])
    rang = {a.id: a.rang for a in ka.verkaeuferfragen.fahrzeugspezifisch}
    check("F13 Aktionsrang Motoröl wie die erreichten Punkte (belegte Grundregel)",
          any("motoroel" in k and v == 750 for k, v in rang.items()), str(rang))
    # N13 -- Tätigkeiten nur absichern
    tt = {f.bauteil: f.taetigkeit for f in fk}
    check("N13-1 Hinterachsdifferenzialöl = Wechsel (Abschnitt 'ZUSÄTZLICHER TEILEAUSTAUSCH' im Original, kein Bauteilname)",
          tt.get("Hinterachsdifferenzialöl") == "ersetzen"
          and "TEILEAUSTAUSCH" in SEITEN[16] and SEITEN[16].index("TEILEAUSTAUSCH") < SEITEN[16].index("Hinterachsdifferenzialöl (außer")
          and SEITEN[16].index("Hinterachsdifferenzialöl (außer") < SEITEN[16].index("ZUSÄTZLICHE INSPEKTIONEN"), str(tt))
    check("N13-2 Ventilspiel, Antriebsriemen und Automatikgetriebeöl bleiben Prüfung (Abschnitt 'ZUSÄTZLICHE INSPEKTIONEN')",
          all(tt.get(b) == "pruefen" for b in ("Ventilspiel", "Nebenaggregat-Antriebsriemen", "Automatikgetriebeöl")), str(tt))
    check("N13-3 Motoröl = Wechsel (E = Erneuern; 'wechseln' in Fußnote und Kopfzeile)", tt.get("Motoröl") == "ersetzen")
    check("N13-4 fremde NX/RX-Zeile (Hinterachsdifferenzialöl 45.000 km / 3 Jahre) ausgeschlossen",
          not any("45 000" in f.aussage or "45.000" in f.aussage for f in fk if f.bauteil == "Hinterachsdifferenzialöl"))
    titel = {a.id: a.titel for a in ka.dokumente.fahrzeugspezifisch}
    check("N13-5 Dokumenttitel passen zur Tätigkeit (Wechselnachweis/Prüfnachweis)",
          any(t.startswith("Wechselnachweis Hinterachsdifferenzialöl") for t in titel.values())
          and any(t.startswith("Prüfnachweis Ventilspiel") for t in titel.values())
          and any(t.startswith("Prüfnachweis Automatikgetriebeöl") for t in titel.values())
          and any(t.startswith("Prüfnachweis Nebenaggregat-Antriebsriemen") for t in titel.values()), str(titel.values()))
    # Riemen: Erst-/Folgeintervall
    rh = hs.get("Nebenaggregat-Antriebsriemen")
    check("F14 [ECHT] Riemen: Kilometeranteil entfernt, 6-Jahres-Zeit erreicht, Folgeintervall ('jährlich') nicht Erstintervall",
          rh is not None and rh.status == "entfernt" and "(oder 6 Jahre) ist nach dem Baujahr bereits erreicht" in rh.hinweis
          and "jährlich" not in rh.hinweis and "15.000" not in rh.hinweis, rh.hinweis[:260] if rh else "")
    for b in ("Hinterachsdifferenzialöl", "Automatikgetriebeöl", "Ventilspiel"):
        hh = hs.get(b)
        check(f"F15 {b}: Zeit sicher erreicht, Marktvorbehalt sichtbar, kein Monatsvorbehalt als Offenheit",
              hh is not None and "bereits erreicht" in hh.hinweis and "bleibt deshalb offen" not in hh.hinweis
              and "in Deutschland gilt, ist nicht bestätigt" in hh.hinweis)
    # kompakte Zeile (Sechserlimit-Überlauf) trennt Grundregel und bedingte Zusatzregel
    kompakt_voll = ka_mod._kompakt_wartung("Motoröl", "ersetzen", mo.aussage)
    kompakt_fn = ka_mod._kompakt_wartung("Motoröl", "ersetzen", FN_ECHT)
    check("F16 kompakte Zeile: Grundregel, Zusatzregel getrennt, KEIN 'Intervall bedingt' für die Grundregel",
          "laut Quelle: Lexus Care-Zwischenservice" in kompakt_voll and "zusätzlich bedingte Regel, Voraussetzung ungeklärt" in kompakt_voll
          and "Intervall bedingt" not in kompakt_voll, kompakt_voll)
    check("F17 kompakte Zeile bei NUR bedingter Regel: 'Intervall bedingt'", "Intervall bedingt" in kompakt_fn, kompakt_fn)
else:
    print("  HINWEIS: lex-sw-00de.pdf nicht lokal -- Prüfungen F1-F17 übersprungen")
    # Fallback: der Test zählt die fehlende Quelle als Fehler, statt still grün zu bleiben
    fehler.append("echte PDF-Quelle fehlt")

# ══ H. Einsatzbereich: Reichweite der Überschrift (echte PDF-Struktur, tatsächlicher Extraktionspfad) ═══════════
print("\n== H Einsatzbereich ==")


def extraktion_alle(text, modell="RC F", marke="Lexus", bj=2017, km=82000, fuel="benzin"):
    alt = tr.MAX_FAKTEN_JE_KATEGORIE
    tr.MAX_FAKTEN_JE_KATEGORIE = 99
    try:
        ab: list[dict] = []
        f = tr._extrahiere_fakten([{"url": URL, "title": "WARTUNG UND GARANTIE", "content": text}], "wartung",
                                  marke=marke, modell=modell, baujahr=bj, abgelehnt=ab, fuel_ziel=fuel, kilometerstand=km)
    finally:
        tr.MAX_FAKTEN_JE_KATEGORIE = alt
    return f


GELTUNG = "Geltungsbereich laut Quelle: Für normale Einsatzbedingungen"
if SEITEN:
    real = "WARTUNG UND GARANTIE. " + "\n\n".join(SEITEN[n] for n in (4, 15, 16, 17))
    ber = tr._einsatz_bereiche(real)

    def in_ber(pos, i=None):
        return [k for k, (a, b, _l) in enumerate(ber) if a <= pos < b] if i is None else (ber[i][0] <= pos < ber[i][1])

    p_kopf = real.index("Lexus Care-Zwischenservice: alle 15.000")
    p_tab = real.index("Motoröl und -filter*")
    p_fn = real.index("Bei Fahrzeugen mit Ölwechsel")
    p_s16 = real.index("Hinterachsdifferenzialöl (außer")
    p_ux = real.index("Für UX 300e")
    p_ux_tab = real.index("Heizgerät-Kühlmittel")
    check("H1 [ECHT] zwei Einsatz-Überschriften (RC-F-Abschnitt S.15, UX-300e-Abschnitt S.17), wörtlich erkannt",
          len(ber) == 2 and ber[0][2] == "Für normale Einsatzbedingungen" and ber[1][2] == "Für normale Einsatzbedingungen", str(ber))
    check("H2 [ECHT] Reichweite: Tabelle, Fußnote *1 und Fortsetzung auf S.16 gehören dazu",
          in_ber(p_tab, 0) and in_ber(p_fn, 0) and in_ber(p_s16, 0))
    check("H3 [ECHT] Reichweite: die KOPFZEILE davor (am Textrand, vor der Überschrift) gehört NICHT dazu -- nie rückwärts",
          in_ber(p_kopf) == [] and "Einsatzbedingungen" not in real[real.index("2.9. LEXUS"):real.index("Für normale Einsatzbedingungen")])
    check("H4 [ECHT] Nachbarabschnitt: der UX-300e-Abschnitt ('Für UX 300e') ist eine Grenze; seine Tabelle hat die EIGENE Überschrift",
          ber[0][1] <= p_ux and not in_ber(p_ux, 0) and in_ber(p_ux_tab, 1) and not in_ber(p_ux_tab, 0))
    # Die Überschrift steht AUSSERHALB des bisherigen Fakttexts (Produzentenlücke): kein Tabellenfenster trägt sie
    saetze = [s for s, _ in tr._saetze_mit_segmentgrenze(tr._entferne_fremde_modellbereiche(real, "RC F", url="u", abgelehnt=[]))]
    tabellensatz = next(s for s in saetze if s.startswith("Für normale Einsatzbedingungen"))
    fenster = tr._wartung_tabellen_fenster_ort(tabellensatz, tr._wartung_vokabular())
    check("H5 [ECHT] ohne Ergänzung trägt KEIN Tabellenfenster den Einsatzbereich (Überschrift liegt vor dem ersten Fenster)",
          len(fenster) >= 5 and all("Einsatzbedingungen" not in f for f, _t, _s in fenster)
          and tabellensatz.index("Einsatzbedingungen") < min(s for _f, _t, s in fenster))

    fk = extraktion_alle(real)
    nach = {f.bauteil: f for f in fk}
    print("  Fakten:", sorted(nach))
    for b in ("Ventilspiel", "Automatikgetriebeöl", "Hinterachsdifferenzialöl", "Nebenaggregat-Antriebsriemen"):
        check(f"H6 {b}: Fakttext trägt den belegten Einsatzbereich wörtlich (Tabellenzeile, auch Fortsetzung S.16)",
              b in nach and GELTUNG in " ".join(nach[b].aussage.split()), nach[b].aussage[-120:] if b in nach else "fehlt")
    mo = nach["Motoröl"]
    rs_mo = ll.zerlege_regeln(mo.aussage)
    check("H7 Motoröl: die FUSSNOTEN-Fundstelle (Tabelle) trägt den Einsatzbereich, die KOPFZEILEN-Fundstelle nicht",
          len(rs_mo) == 2 and rs_mo[0].bedingt and rs_mo[0].einsatz == "normal"
          and not rs_mo[1].bedingt and rs_mo[1].einsatz is None and "Einsatzbedingungen" not in rs_mo[1].text,
          str([(r.einsatz, r.text[:40]) for r in rs_mo]))
    check("H8 Fußnote: Landesvorbehalt der Regel, Ausstattungsbedingung und Einsatzbereich getrennt im Text",
          "falls für Ihr Land zutreffend" in rs_mo[0].text and "Ölwechsel-Managementsystem" in rs_mo[0].text and GELTUNG in rs_mo[0].text)
    check("H9 kein Fakt trägt einen erschwerten Bereich", not any("erschwert" in f.aussage.lower() for f in fk))
    # Nachbarabschnitt mit nummerierter Überschrift: die Überschrift wirkt nicht darüber hinaus
    fk_n = extraktion_alle(real + "\n\n2.10. WEITERE ARBEITEN\nBeim Lexus RC F Zahnriemen kontrollieren: alle 120.000 km oder 8 Jahre.")
    zr = [f for f in fk_n if f.bauteil == "Zahnriemen"]
    check("H10 [SYNTH-Nachbar] Abschnitt '2.10. ...' hinter der Tabelle: seine Zeile trägt KEINEN Einsatzbereich",
          len(zr) == 1 and "Geltungsbereich" not in zr[0].aussage and "Einsatzbedingungen" not in zr[0].aussage, str([f.aussage for f in zr]))
    # sichtbare Folgen bis Hinweis, Fragen, Dokumente, Evidenz
    ins_h, lk_h, ka_h = kette(fk)
    hs_h = {w.bauteil: w for w in lk_h.wartungshinweise}
    check("H11 Hinweis Ventilspiel: Einsatzbereich genannt, NICHT als am Fahrzeug bestätigt; Nutzungsprofil und Marktgeltung offen",
          "gilt laut Quelle für normale Einsatzbedingungen" in hs_h["Ventilspiel"].hinweis
          and "vorlagen, ist unbekannt" in hs_h["Ventilspiel"].hinweis
          and "in Deutschland gilt, ist nicht bestätigt" in hs_h["Ventilspiel"].hinweis, hs_h["Ventilspiel"].hinweis[-420:])
    check("H12 Hinweis Motoröl: Grundregel (Kopfzeile) ohne erfundenen Bereich; die bedingte Fußnote zeigt ihren Einsatzbereich",
          "gilt laut Quelle für normale Einsatzbedingungen" not in hs_h["Motoröl"].hinweis
          and GELTUNG in hs_h["Motoröl"].hinweis and "Zusätzlich nennt die Quelle eine bedingte Regel" in hs_h["Motoröl"].hinweis)
    for bereich in ("verkaeuferfragen", "dokumente"):
        spez = getattr(ka_h, bereich).fahrzeugspezifisch
        vs = next((a for a in spez if "ventilspiel" in (a.titel + a.aktion).lower() and a.kategorie in ("wartung", "web_wartung")), None)
        mo_a = next((a for a in spez if "motoröl" in (a.titel + a.aktion).lower() and a.kategorie in ("wartung", "web_wartung")), None)
        check(f"H13 {bereich}: Einsatzbereich erreicht die Aktion (Ventilspiel; bei Motoröl in der Zusatzregel)",
              vs is not None and "normale Einsatzbedingungen" in vs.aktion and mo_a is not None and GELTUNG in mo_a.aktion)
    ins_vs = next(i for i in ins_h if i.titel.startswith("Ventilspiel"))
    check("H14 Evidenz: der Einsatzbereich steht im Insight-Text; Evidenzverweis der Aktion unverändert",
          "normale Einsatzbedingungen" in ins_vs.beschreibung
          and any(ins_vs.id in a.evidence_ids for a in ka_h.dokumente.fahrzeugspezifisch))
    kompakt_vs = ka_mod._kompakt_wartung("Ventilspiel", "pruefen", nach["Ventilspiel"].aussage)
    check("H15 kompakte Zeile (Überlauf) trägt den Einsatzbereich", "Einsatzbedingungen" in kompakt_vs, kompakt_vs)

    # normale und erschwerte Regime getrennt (SYNTH, in der Struktur des echten Heftes: je eigene Überschrift)
    TAB = ("Erneuern - Inspektion ZUSÄTZLICHER TEILEAUSTAUSCH Zahnriemen Alle {a} Bremsflüssigkeit Alle {b} Zündkerzen Alle {c} "
           "Luftfilter Alle {d} ZUSÄTZLICHE INSPEKTIONEN Ventilspiel Alle {e} Motorkühlmittel Alle {f}")
    doc = (SEITEN[4] + "\n\n2.9. LEXUS CARE-WARTUNGSPLAN\nFür RC F und UX 200\nFür normale Einsatzbedingungen\n"
           + TAB.format(a="150.000 km / 8 Jahre", b="60.000 km / 4 Jahre", c="90.000 km / 6 Jahre", d="60.000 km / 4 Jahre",
                        e="90.000 km / 6 Jahre", f="150.000 km / 10 Jahre")
           + "\nFür erschwerte Einsatzbedingungen\n"
           + TAB.format(a="90.000 km / 5 Jahre", b="30.000 km / 2 Jahre", c="45.000 km / 3 Jahre", d="30.000 km / 2 Jahre",
                        e="45.000 km / 3 Jahre", f="75.000 km / 5 Jahre"))
    ber_s = tr._einsatz_bereiche(doc)
    check("H16 [SYNTH] zwei getrennte Bereiche (normal, erschwert), sich nicht überlappend",
          [b[2] for b in ber_s] == ["Für normale Einsatzbedingungen", "Für erschwerte Einsatzbedingungen"] and ber_s[0][1] <= ber_s[1][0], str(ber_s))
    fk_s = extraktion_alle(doc)
    regeln_zr = [r for f in fk_s if f.bauteil == "Zahnriemen" for r in ll.zerlege_regeln(f.aussage)]
    print("  Zahnriemen-Regeln:", [(r.km, r.einsatz, r.bedingt) for r in regeln_zr])
    check("H17 [SYNTH] 150.000 km nur NORMAL und unbedingt, 90.000 km nur ERSCHWERT und bedingt -- keine Vermischung",
          any(r.km == (150000, None) and r.einsatz == "normal" and not r.bedingt for r in regeln_zr)
          and any(r.km == (90000, None) and r.einsatz == "erschwert" and r.bedingt for r in regeln_zr)
          and not any(r.einsatz == "gemischt" for r in regeln_zr)
          and not any(r.km == (150000, None) and r.einsatz == "erschwert" for r in regeln_zr)
          and not any(r.km == (90000, None) and r.einsatz == "normal" for r in regeln_zr))
    ins_s, lk_s, ka_s = kette(fk_s)
    hz = [w for w in lk_s.wartungshinweise if w.bauteil == "Zahnriemen"]
    erschw = [w for w in hz if "nur unter einer Voraussetzung" in w.hinweis]
    check("H18 [SYNTH] erschwerter Bereich wird nie als erreicht ausgegeben (bedingte Regel, Voraussetzung ungeklärt)",
          all("wurde bei dieser Laufleistung rein rechnerisch" not in w.hinweis for w in erschw)
          and all("ist nach dem Baujahr bereits erreicht" not in w.hinweis for w in erschw)
          and all("ungeklärt" in w.hinweis for w in erschw), str([w.hinweis[:200] for w in hz]))
    check("H19 [SYNTH] die normale Grundregel geht neben der erschwerten nicht verloren (kein Bauteilplatz-Verbrauch)",
          any("gilt laut Quelle für normale Einsatzbedingungen" in w.hinweis and "nur unter einer Voraussetzung" not in w.hinweis for w in hz),
          str([(w.status, w.hinweis[:120]) for w in hz]))
    check("H20 [SYNTH] Bremsflüssigkeit/Luftfilter: Regel mit erschwertem Bereich ist bedingt, die normale nicht",
          all(r.bedingt for f in fk_s if f.bauteil in ("Luftfilter", "Bremsflüssigkeit") for r in ll.zerlege_regeln(f.aussage) if r.einsatz == "erschwert")
          and all(not r.bedingt for f in fk_s if f.bauteil in ("Luftfilter", "Bremsflüssigkeit") for r in ll.zerlege_regeln(f.aussage) if r.einsatz == "normal"))
else:
    fehler.append("H: echte PDF-Quelle fehlt")

# ══ I. Reine Zeitgrundregel (kein Kilometerpunkt) neben bedingter Kilometer-Zusatzregel ═════════════════════
print("\n== I Reine Zeitgrundregel ==")
ZEIT = "Zahnriemen kontrollieren: alle 6 Jahre"
KMB = ("Bei Fahrzeugen mit Wartungsanzeige: Zahnriemen kontrollieren, wenn die Hinweismeldung erscheint "
       "(zumindest alle 150.000 km), falls für Ihr Land zutreffend")
beide_a = ZEIT + ". " + M + " " + KMB
beide_b = KMB + ". " + M + " " + ZEIT


def zh(text, km=82000, alter=9, technik=None):
    i = Insight(id="z1", kategorie="web_wartung", titel="Zahnriemen: x", beschreibung=text, confidence="mittel", taetigkeit="pruefen",
                technische_bedingung=technik, technische_bedingung_status="unresolved" if technik else None)
    return ll.zeit_hinweise([i], km, alter), ll._wartungshinweise([i], km, alter)


for lab, t in (("A (Zeitregel zuerst)", beide_a), ("B (Zusatzregel zuerst)", beide_b)):
    zhs, kmh = zh(t)
    h = zhs[0].hinweis if zhs else ""
    check(f"I1 {lab}: die Zeit-Grundregel wird eigenständig ausgewertet (6 Jahre, Alter 9 -> bereits erreicht, mindestens 8 Jahre)",
          len(zhs) == 1 and "Zeitintervall (6 Jahre) ohne Kilometerangabe" in h and "bereits erreicht (Fahrzeugalter mindestens 8 Jahre" in h, h)
    check(f"I2 {lab}: KEIN Kilometer-Hinweis aus dem Kilometerwert der bedingten Zusatzregel (nicht ersatzweise verwendet)",
          kmh == [] and "Kilometerpunkt" not in h and "rechnerisch mindestens einmal erreicht" not in h)
    check(f"I3 {lab}: die bedingte Zusatzregel bleibt wörtlich, ungeklärt und wird nicht auf die Zeitregel übertragen",
          "Zusätzlich nennt die Quelle eine bedingte Regel" in h and "zumindest alle 150.000 km" in h
          and "nicht auf die Zeitregel übertragen" in h and "ungeklärt" in h)
    check(f"I4 {lab}: weder Fälligkeit noch unterlassene Wartung",
          sauber(h) and "seitdem gemacht wurde, geht aus den vorliegenden Daten nicht hervor" in h)
    zhs_hoch, kmh_hoch = zh(t, km=200000)
    check(f"I5 {lab}: auch bei Kilometerstand ÜBER dem Zusatzwert (200.000 km): kein Kilometer-Hinweis, Zeitregel unverändert",
          kmh_hoch == [] and len(zhs_hoch) == 1 and "Kilometerpunkt" not in zhs_hoch[0].hinweis)
    check(f"I6 {lab}: Rang aus DERSELBEN Schwellenlogik (Zeit sicher erreicht), unabhängig vom Zusatz-Kilometerwert",
          ll.schwelle_belegbar_erreicht_alter(t, 82000, 9) and not ll.schwelle_belegbar_erreicht_alter(t, 82000, 2)
          and ll.schwelle_belegbar_erreicht_alter(t, 1000, 9))
    zu, _ = zh(t, alter=None)
    check(f"I7 {lab}: Alter UNBEKANNT -> Grund 'ohne bekanntes Baujahr', kein Erstzulassungsmonat, Rang ohne belegte Schwelle",
          len(zu) == 1 and "ohne bekanntes Baujahr" in zu[0].hinweis and "Erstzulassungsmonat" not in zu[0].hinweis
          and not ll.schwelle_belegbar_erreicht_alter(t, 82000, None), zu[0].hinweis if zu else "")
    zb, _ = zh(t, alter=6)
    check(f"I8 {lab}: Alter im UNSICHERHEITSBEREICH (6 bei Schwelle 6) -> der Erstzulassungsmonat ist entscheidend, offen",
          len(zb) == 1 and "Erstzulassungsmonat" in zb[0].hinweis and "bleibt deshalb offen" in zb[0].hinweis
          and "bereits erreicht (Fahrzeugalter" not in zb[0].hinweis and not ll.schwelle_belegbar_erreicht_alter(t, 82000, 6))
    zn, _ = zh(t, alter=2)
    check(f"I9 {lab}: Zeitgrenze auch im ältesten Fall nicht erreicht -> kein Hinweis ohne Anlass", zn == [])
zt, _ = zh(beide_a, technik="nur Hybrid- und Hybrid Plug-in-Modelle")
check("I10 technische Voraussetzung ungeklärt: sie ist der Grund der offenen Zeitaussage",
      len(zt) == 1 and "ungeklärten technischen Voraussetzung" in zt[0].hinweis and "Sofern sie zutrifft" in zt[0].hinweis)
zonly, konly = zh(KMB, km=160000)
check("I11 NUR bedingte Regel: keine erfundene Grundregel, kein Zeit-Hinweis; der Kilometer-Hinweis bleibt ausdrücklich bedingt",
      zonly == [] and len(konly) == 1 and "nur unter einer Voraussetzung" in konly[0].hinweis
      and "weder als erreicht noch als nicht erreicht" in konly[0].hinweis and not ll.schwelle_belegbar_erreicht_alter(KMB, 82000, 9))
znone, knone = zh("Zahnriemen kontrollieren, wenn die Hinweismeldung erscheint (zumindest alle 6 Jahre)")
check("I12 NUR bedingte REINE ZEIT-Regel: weder Hinweis noch erfundene Grundregel", znone == [] and knone == [])
z2, _ = zh(ZEIT + ". " + M + " Zahnriemen kontrollieren: alle 8 Jahre")
check("I13 [SYNTH] zwei verschiedene Zeit-Grundregeln: keine Auswahl, Mehrdeutigkeit ausgewiesen",
      len(z2) == 1 and "mehrere unterschiedliche Zeitintervalle" in z2[0].hinweis and "keines wird ausgewählt" in z2[0].hinweis)
zk, kk = zh("Zahnriemen kontrollieren: alle 90.000 km. " + M + " Zahnriemen kontrollieren: alle 6 Jahre")
check("I14 Zeit-Grundregel NEBEN einer Kilometer-GRUNDregel: mehrdeutig statt Erstauswahl; kein zusätzlicher Zeit-Hinweis",
      zk == [] and len(kk) == 1 and "mehrere unterschiedliche Intervalle" in kk[0].hinweis)

# tatsächlicher Pfad: Erzeuger -> Zusammenführung gleicher Bauteile (app/risikothemen.py) -> Laufleistung -> Aktionen.
# Zeitregel und bedingte Kilometerregel kommen als zwei Fakten derselben Quelle; die Zusammenführung legt sie mit dem
# Marker "Weiterer Datensatz (Webrecherche):" in EINEN Insight (der Erzeuger-Marker greift hier nicht).
KM_T = 160000   # über dem Wert der bedingten Zusatzregel (150.000 km): sie darf trotzdem nicht auf die Zeitregel wirken
REQ_T = KaufCheckRequest(marke="Toyota", modell="Yaris", baujahr=2017, kilometerstand=KM_T, kraftstoff="Benzin", ausstattung=[])
QUELLE_T = "https://www.toyota.de/service/wartung.pdf"


def fakten_t(text, bj=2017):
    return tr._extrahiere_fakten([{"url": QUELLE_T, "title": "Toyota Yaris Wartungsplan Serviceheft", "content": text}], "wartung",
                                 marke="Toyota", modell="Yaris", baujahr=bj, abgelehnt=[], kilometerstand=KM_T)


for lab, text in (("A Zeitregel zuerst", ZEIT + ". " + KMB + "."), ("B Zusatzregel zuerst", KMB + ". " + ZEIT + ".")):
    fk_i = fakten_t(text)
    ins_i, lk_i, ka_i = kette(fk_i, REQ_T)
    print(f"  [{lab}] Fakten:", [(f.bauteil, f.aussage[:40]) for f in fk_i])
    zr_ins = [i for i in ins_i if i.titel.startswith("Zahnriemen")]
    check(f"I15 {lab} [Extraktionspfad] der Erzeuger liefert Zeitregel und bedingte Kilometerregel als zwei Fakten; die Zusammen-"
          f"führung macht EINEN Insight mit 'Weiterer Datensatz' daraus",
          len(fk_i) == 2 and sum(1 for f in fk_i if ll.WartungsRegel(f.aussage).km is None) == 1
          and len(zr_ins) == 1 and "Weiterer Datensatz (Webrecherche):" in zr_ins[0].beschreibung and len(zr_ins[0].nebenbelege) == 1,
          str([i.beschreibung[:120] for i in zr_ins]))
    rg = ll.zerlege_regeln(zr_ins[0].beschreibung)
    check(f"I16 {lab} [Extraktionspfad] die zusammengeführte Aussage wird an BEIDEN Markern zerlegt: eine Zeit-Grundregel, eine bedingte "
          f"Kilometerregel -- kein gemischter Einzelwert",
          len(rg) == 2 and sorted((r.km is None, r.bedingt) for r in rg) == [(False, True), (True, False)]
          and not any(r.km and r.zeit for r in rg))
    check(f"I17 {lab} [Extraktionspfad] KEIN Kilometer-Hinweis aus dem Wert der bedingten Regel (160.000 km >= 150.000 km)",
          not [w for w in lk_i.wartungshinweise if w.bauteil == "Zahnriemen"])
    for bereich in ("verkaeuferfragen", "dokumente"):
        spez = [a for a in getattr(ka_i, bereich).fahrzeugspezifisch if "zahnriemen" in (a.titel + a.aktion).lower()]
        za = spez[0] if spez else None
        check(f"I18 {lab} {bereich}: EINE Aktion; Zeit-Grundregel ausgewertet (erreicht), bedingte Kilometerregel wörtlich, ungeklärt, nicht übertragen",
              len(spez) == 1 and "Zeitintervall (6 Jahre) ohne Kilometerangabe" in za.aktion
              and "bereits erreicht (Fahrzeugalter mindestens 8 Jahre" in za.aktion
              and "Zusätzlich nennt die Quelle eine bedingte Regel" in za.aktion and "zumindest alle 150.000 km" in za.aktion
              and "nicht auf die Zeitregel übertragen" in za.aktion and "Kilometerpunkt" not in za.aktion,
              str([a.aktion[:160] for a in spez]))
        check(f"I19 {lab} {bereich}: Rang einlösbar (750) aus der Zeitschwelle, Evidenz = der zusammengeführte Insight",
              za is not None and za.rang == 750 and za.evidence_ids == [zr_ins[0].id], str((za.rang, za.evidence_ids) if za else None))
        check(f"I20 {lab} {bereich}: Prüfwortlaut (Tätigkeit 'prüfen'), keine Fälligkeit, keine unterlassene Wartung",
              za is not None and ("geprüft" in za.titel or "Prüfnachweis" in za.titel) and sauber(za.aktion))
# Alter unbekannt / unsicher durch den echten Pfad
for lab, bj, erwartet in (("Alter unbekannt", None, "ohne bekanntes Baujahr"), ("Unsicherheitsbereich", 2020, "Erstzulassungsmonat")):
    req_x = KaufCheckRequest(marke="Toyota", modell="Yaris", baujahr=bj, kilometerstand=KM_T, kraftstoff="Benzin", ausstattung=[])
    _i, _l, ka_x = kette(fakten_t(ZEIT + ". " + KMB + ".", bj=bj), req_x)
    za = [a for a in ka_x.verkaeuferfragen.fahrzeugspezifisch if "Zeitintervall (6 Jahre) ohne Kilometerangabe" in a.aktion]
    check(f"I21 [Extraktionspfad] {lab}: Zeitregel sichtbar mit dem richtigen Grund ('{erwartet}'), nicht als erreicht, kein Rang 750",
          len(za) == 1 and erwartet in za[0].aktion and "bereits erreicht (Fahrzeugalter" not in za[0].aktion
          and za[0].rang < 750, za[0].aktion[:260] if za else "keine Aktion")
# nur bedingte Kilometerregel durch den echten Pfad: keine erfundene Grundregel
fk_n = fakten_t(KMB + ".")
_i, lk_n, ka_n = kette(fk_n, REQ_T)
hn = [w for w in lk_n.wartungshinweise if w.bauteil == "Zahnriemen"]
an = [a for a in ka_n.verkaeuferfragen.fahrzeugspezifisch if "zahnriemen" in (a.titel + a.aktion).lower()]
check("I22 [Extraktionspfad] NUR bedingte Kilometerregel: Hinweis ausdrücklich bedingt, keine Zeit-Grundregel erfunden, Rang unter 750",
      len(hn) == 1 and "nur unter einer Voraussetzung" in hn[0].hinweis and len(an) == 1 and "Zeitintervall" not in an[0].aktion
      and an[0].rang < 750 and "wurde bei dieser Laufleistung rein rechnerisch" not in an[0].aktion)

# ══ J. Ausgabewege der reinen Zeitregel: Prompt-Block, Bericht, serialisierte Antwort, Fragen, Dokumente ═══════════
print("\n== J Ausgabewege der reinen Zeitregel ==")
from app.kaufcheck_bericht import bericht as _bericht  # noqa: E402
from app.models import Wartungshinweis  # noqa: E402
from app.schreibstil import bereinige_nutzertexte  # noqa: E402
from app.vehicle_identity import VehicleIdentity  # noqa: E402


def antwort_zu(req, fk):
    """Wie app/kaufcheck.py: Insights -> Laufleistungskontext -> Kaufaktionen -> Bericht -> Schreibstil -> JSON."""
    ins, lk, ka = kette(fk, req)
    text = _bericht(req, VehicleIdentity.from_check_context(None, None, req), None, None, ins, ka, [], "kaufen_nach_besichtigung",
                    type("PA", (), {"label": None})(), False, lk, None, ["Inserat"])
    zr = ll.zeitregel_hinweise(lk, ins)
    block = ll.prompt_block(lk, zr)
    antwort = bereinige_nutzertexte({"insights": [i.model_dump() for i in ins], "kaufaktionen": ka.model_dump(),
                                     "laufleistungskontext": lk.model_dump() if lk else None, "bericht": text})
    return ins, lk, ka, zr, block, json.loads(json.dumps(antwort, ensure_ascii=False, default=str))


def zeit_aktionen(antwort, bereich):
    return [a for a in antwort["kaufaktionen"][bereich]["fahrzeugspezifisch"]
            if "Zeitintervall (6 Jahre) ohne Kilometerangabe" in a["aktion"]]


FAELLE = (("A erreicht", 2017, ZEIT + ". " + KMB + ".", "bereits erreicht (Fahrzeugalter mindestens 8 Jahre"),
          ("B erreicht, Zusatzregel zuerst", 2017, KMB + ". " + ZEIT + ".", "bereits erreicht (Fahrzeugalter mindestens 8 Jahre"),
          ("Alter unbekannt", None, ZEIT + ". " + KMB + ".", "ohne bekanntes Baujahr"),
          ("Altersgrenze unsicher", 2020, ZEIT + ". " + KMB + ".", "ohne den genauen Erstzulassungsmonat nicht bestimmen"))
try:
    Wartungshinweis(bauteil="Zahnriemen", status="zeit", intervall_text="alle 6 Jahre", hinweis="x", herkunft="web_wartung", evidence_id="e")
    vertrag_verlangt_km = False
except Exception:
    vertrag_verlangt_km = True
check("J0 Beleg: das Wartungshinweis-Modell verlangt Kilometerfelder (punkt_km/differenz_km) -- eine Zeitregel kann dort ohne "
      "erfundene Werte nicht stehen", vertrag_verlangt_km)
for lab, bj, text, kern in FAELLE:
    req_j = KaufCheckRequest(marke="Toyota", modell="Yaris", baujahr=bj, kilometerstand=KM_T, kraftstoff="Benzin", ausstattung=[],
                             vorbesitzer=2, scheckheftgepflegt=True)
    ins_j, lk_j, ka_j, zr_j, block_j, ant = antwort_zu(req_j, fakten_t(text, bj=bj))
    zh_j = zr_j[0].hinweis if zr_j else ""
    check(f"J1 {lab}: genau EIN Zeitregel-Hinweis, der Kernsatz stimmt ('{kern[:40]}...')",
          len(zr_j) == 1 and kern in zh_j and "Zeitintervall (6 Jahre) ohne Kilometerangabe" in zh_j, zh_j[:200])
    check(f"J2 {lab}: Prompt-Block trägt den Hinweis EINMAL mit Beleg-ID und behält die Fälligkeits-Verbote",
          block_j.count(zh_j) == 1 and f"(Beleg {ins_j[0].id})" in block_j and "NIEMALS" in block_j
          and "Zeitpunkt des letzten Service ist NICHT bekannt" in block_j)
    check(f"J3 {lab}: serialisierte Antwort -- Laufleistungskontext trägt KEINEN erfundenen Kilometer-Hinweis zum Zahnriemen",
          [w for w in ant["laufleistungskontext"]["wartungshinweise"] if w["bauteil"] == "Zahnriemen"] == []
          and ant["laufleistungskontext"]["fahrzeugalter_jahre"] == (None if bj is None else 2026 - bj))
    za_v, za_d = zeit_aktionen(ant, "verkaeuferfragen"), zeit_aktionen(ant, "dokumente")
    check(f"J4 {lab}: serialisierte Antwort -- Verkäuferfrage UND Dokument tragen dieselbe Zeitregel samt Zusatzregel und Quelle",
          len(za_v) == 1 and len(za_d) == 1 and kern in za_v[0]["aktion"] and kern in za_d[0]["aktion"]
          and "zumindest alle 150.000 km" in za_v[0]["aktion"] and "nicht auf die Zeitregel übertragen" in za_d[0]["aktion"]
          and "aus der Webrecherche" in za_v[0]["aktion"], str(za_v)[:200])
    check(f"J5 {lab}: Evidenz -- jede Aktion verweist auf den zusammengeführten Insight (existiert in der Antwort)",
          za_v[0]["evidence_ids"] == [ins_j[0].id] == za_d[0]["evidence_ids"] and ins_j[0].id in [i["id"] for i in ant["insights"]])
    b = " ".join(ant["bericht"].split())
    satz = " ".join(za_v[0]["aktion"].split())
    check(f"J6 {lab}: ausführlicher Bericht -- der Hinweistext steht in Verkäuferfragen UND Dokumenten, nicht doppelt je Abschnitt",
          b.count(satz) == 1 and b.count(" ".join(za_d[0]["aktion"].split())) == 1
          and b.index("## Verkäuferfragen") < b.index(satz) < b.index("## Besichtigung")
          and b.index("## Dokumente und finale Checkliste") < b.index(" ".join(za_d[0]["aktion"].split())), str(b.count(satz)))
    check(f"J7 {lab}: Bericht -- die Quellaussage steht im Risiko-/Hinweisabschnitt, der Bericht widerspricht ihr nirgends",
          "## Relevante Risiken und Hinweise" in b and "Zahnriemen kontrollieren: alle 6 Jahre" in b
          and "Kilometerpunkt (150.000" not in b and "wurde bei dieser Laufleistung rein rechnerisch" not in b
          and sauber(" ".join(a["aktion"] for a in za_v + za_d)))
    check(f"J8 {lab}: die vier Kanäle (Prompt, Frage, Dokument, Bericht) tragen dieselben Kernsätze",
          all(k in block_j and k in za_v[0]["aktion"] and k in za_d[0]["aktion"] and k in b
              for k in (kern, "Zeitintervall (6 Jahre) ohne Kilometerangabe", "Zusätzlich nennt die Quelle eine bedingte Regel",
                        "zumindest alle 150.000 km")))
    check(f"J9 {lab}: unbekannte letzte Durchführung / keine Fälligkeit in jedem Kanal",
          all(sauber(t) for t in (block_j.split("Wartungspunkt")[-1], za_v[0]["aktion"], za_d[0]["aktion"])))
# Rang der Zeitregel in der Antwort
ant_a = antwort_zu(REQ_T, fakten_t(ZEIT + ". " + KMB + "."))[5]
ant_u = antwort_zu(KaufCheckRequest(marke="Toyota", modell="Yaris", baujahr=None, kilometerstand=KM_T, kraftstoff="Benzin", ausstattung=[],
                                    vorbesitzer=2, scheckheftgepflegt=True), fakten_t(ZEIT + ". " + KMB + ".", bj=None))[5]
check("J10 Rang in der Antwort: erreichte Zeitgrenze 750, unbekanntes Alter darunter",
      zeit_aktionen(ant_a, "verkaeuferfragen")[0]["rang"] == 750 and zeit_aktionen(ant_u, "verkaeuferfragen")[0]["rang"] < 750)
# reine Zeitregel OHNE bedingte Zusatzregel: dieselben Kanäle
ins_p, lk_p, ka_p, zr_p, block_p, ant_p = antwort_zu(REQ_T, fakten_t(ZEIT + "."))
check("J11 reine Zeitregel ohne Zusatzregel: Prompt, Frage, Dokument und Bericht tragen sie; kein Kilometer-Hinweis",
      len(zr_p) == 1 and zr_p[0].hinweis in block_p and len(zeit_aktionen(ant_p, "verkaeuferfragen")) == 1
      and len(zeit_aktionen(ant_p, "dokumente")) == 1 and "Zeitintervall (6 Jahre) ohne Kilometerangabe" in ant_p["bericht"]
      and "bedingte Regel" not in zr_p[0].hinweis and lk_p.wartungshinweise == [])
# Verdrahtung der produktiven Pipeline (app/kaufcheck.py) -- gleiche Quelle wie die Kaufaktionen
quelle_kc = (HIER / "app" / "kaufcheck.py").read_text(encoding="utf-8")
check("J12 app/kaufcheck.py übergibt die Zeitregeln an den Prompt-Block (dieselbe Funktion wie die Kaufaktionen)",
      "zeitregel_hinweise(laufleistungskontext, insights)" in quelle_kc and "laufleistung_prompt_block(laufleistungskontext," in quelle_kc
      and "zeitregel_hinweise(kontext, insights)" in (HIER / "app" / "kaufaktionen.py").read_text(encoding="utf-8"))
if SEITEN:
    zr_real = ll.zeitregel_hinweise(lk_h, ins_h)
    block_real = ll.prompt_block(lk_h, zr_real)
    check("J13 [ECHT] Lexus-Replay: keine Zeitregel (alle fünf haben Kilometer); Prompt-Block unverändert -- jeder Kilometer-Wartungspunkt genau einmal",
          zr_real == [] and len(lk_h.wartungshinweise) >= 5 and block_real.count("Wartungspunkt „") == len(lk_h.wartungshinweise)
          and all(block_real.count(f"Wartungspunkt „{w.bauteil}“") == 1 for w in lk_h.wartungshinweise)
          and block_real == ll.prompt_block(lk_h),
          str((len(zr_real), block_real.count("Wartungspunkt „"), [w.bauteil for w in lk_h.wartungshinweise])))

# ══ G. Keine schweren Importe für die gemeinsame Konstante ═══════════════════════════════════
print("\n== G Importe ==")
p = subprocess.run([sys.executable, "-I", "-c",
                    "import sys; sys.path.insert(0, r'%s'); import app.laufleistung as l; "
                    "print('app.technical_research' in sys.modules, 'app.web_search' in sys.modules, "
                    "'app.kaufaktionen' in sys.modules, l.WEITERE_STELLE_MARKER)" % str(HIER)],
                   capture_output=True, text=True, env={**os.environ, "PYTHONUTF8": "1"}, timeout=120)
print("   ", p.stdout.strip()[-120:], p.stderr.strip()[-200:])
check("G1 app.laufleistung zieht weder technical_research noch web_search noch kaufaktionen nach",
      p.returncode == 0 and p.stdout.strip().split()[:3] == ["False", "False", "False"], p.stdout + p.stderr)

print(f"\n{'ALLE GRÜN' if not fehler else str(len(fehler)) + ' FEHLER: ' + '; '.join(fehler[:12])}")
sys.exit(1 if fehler else 0)
