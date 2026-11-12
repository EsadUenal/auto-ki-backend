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
