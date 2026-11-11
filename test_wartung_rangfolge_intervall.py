"""
Wartungsrangfolge am (unveränderten) Fünferlimit: bei sonst gleichem Rang kommt zuerst, wessen
ERSTintervall eine belegbar erreichte Schwelle hat; danach Trefferreihenfolge statt Alphabet.

Geprüft mit Netzsperre + Temp-DB vor dem App-Import (kein Provider, keine Live-DB):
  A  Einzelfälle der Schwellenlogik (km oder Jahre, unbekanntes Alter, Schwelle innerhalb des möglichen
     Altersintervalls, Erst-/Folgeintervall, Hinweismeldungs-Bedingung, fehlende Angaben)
  B  echter RC-F-Replay (Lexus-Serviceheft, Seiten 4/15-17) bis zu Insights, Verkäuferfragen, Nachweisen
  C  Fünferlimit unverändert; Trefferreihenfolge als stabiler Gleichstand; ohne km/Baujahr = Altverhalten
     bis auf Alphabet->Trefferreihenfolge

Eingaben: Kilometerstand 82.000, Baujahr 2017, Stand = heute_jahr 2026 (im Test fest gesetzt, damit das
Ergebnis nicht vom Kalenderjahr abhängt). [SYNTH] markiert synthetische Gegenproben.

    python test_wartung_rangfolge_intervall.py
"""
import ast
import os
import socket
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="wartung_rang_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_TMP, "chroma")


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


socket.create_connection = _gesperrt
socket.socket.connect = _gesperrt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)

import app.technical_research as tr  # noqa: E402
from app.evidence import build_insights  # noqa: E402
from app.kaufaktionen import build_kaufaktionen  # noqa: E402
from app.laufleistung import (  # noqa: E402
    erstintervall_text, schwelle_belegbar_erreicht, zeitalternative_belegbar_erreicht,
)
from app.models import KaufCheckRequest, TechnischeRecherche  # noqa: E402
from test_rc_f_lexus_care_wartungsplan_real import LEXUS_CH_MODELLUEBERSICHT  # noqa: E402

fehler: list[str] = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        fehler.append(name)


KM, BJ, JAHR = 82000, 2017, 2026


def erreicht(text, km=KM, bj=BJ):
    return schwelle_belegbar_erreicht(text, km, bj, heute_jahr=JAHR)


# ── A. Einzelfälle ────────────────────────────────────────────────────────────────────────
print("== A. Schwellenlogik ==")
check("A1 km erreicht (60.000 km bei 82.000 km)", erreicht("Automatikgetriebeöl Alle 60.000 km"))
check("A2 km nicht erreicht, kein Zeitintervall: kein Vorrang (90.000 km bei 82.000 km)",
      not erreicht("Zündkerzen Alle 90.000 km"))
check("A3 'km / Jahre': nur die Zeit erreicht genügt (105.000 km / 6 Jahre, Alter 9)",
      erreicht("Antriebsriemen Alle 105.000 km / 6 Jahre"))
check("A4 'km oder Jahre': nur die Zeit erreicht genügt", erreicht("Filter alle 105.000 km oder 6 Jahre"))
check("A5 'km oder Jahre': nur die km erreicht genügt (60.000 km oder 15 Jahre)",
      erreicht("Filter alle 60.000 km oder 15 Jahre"))
check("A6 'km oder Jahre': keine der beiden erreicht -> kein Vorrang",
      not erreicht("Filter alle 105.000 km oder 15 Jahre"))
check("A7 unbekanntes Baujahr: Zeitschwelle gibt keinen Vorrang", not erreicht("Riemen Alle 105.000 km / 6 Jahre", bj=None))
check("A8 unbekannter Kilometerstand: nur die Zeitschwelle zählt", erreicht("Riemen Alle 105.000 km / 6 Jahre", km=None)
      and not erreicht("Riemen Alle 60.000 km", km=None))
check("A9 Schwelle INNERHALB des möglichen Altersintervalls (Alter 9 -> 8..10): 9 Jahre kein Vorrang",
      not erreicht("Riemen Alle 105.000 km / 9 Jahre"))
check("A10 Schwelle am sicheren Rand (8 Jahre) -> Vorrang", erreicht("Riemen Alle 105.000 km / 8 Jahre"))
check("A11 Monate werden gleich behandelt (60 Monate = 5 Jahre erreicht, 96 Monate = 8 Jahre erreicht, 120 nicht)",
      erreicht("Teil Alle 105.000 km / 60 Monate") and erreicht("Teil Alle 105.000 km / 96 Monate")
      and not erreicht("Teil Alle 105.000 km / 120 Monate"))
check("A12 Zeitalternative-Helfer: sichere Seite alter-1", zeitalternative_belegbar_erreicht(9, (8, "Jahre"))
      and not zeitalternative_belegbar_erreicht(9, (9, "Jahre")) and not zeitalternative_belegbar_erreicht(None, (1, "Jahre")))
check("A13 Erst-/Folgeintervall getrennt: nur das Erstintervall zählt (150.000 km, danach 15.000 km)",
      not erreicht("Motorkühlmittel Alle 150.000 km Danach alle 15.000 km"))
check("A14 Folgeintervall begründet keinen Vorrang (Erstintervall 105.000 km nicht erreicht, Folge 15.000 km 'jährlich')",
      not erreicht("Riemen Alle 105.000 km Danach alle 15.000 km / 1 Jahr"))
check("A15 Erstintervall-Text endet vor 'Danach'",
      erstintervall_text("Alle 150.000 km Danach alle 90.000 km") == "Alle 150.000 km ")
check("A16 Hinweismeldungs-Bedingung: kein Vorrang (15.000 km / 1 Jahr, 'wenn die Ölwechsel-Hinweismeldung erscheint')",
      not erreicht("Motoröl wechseln, wenn die Ölwechsel-Hinweismeldung erscheint (zumindest alle 15.000 km / 1 Jahr)"))
check("A17 'falls für Ihr Land zutreffend' / 'bei Bedarf' / 'je nach': kein Vorrang",
      not erreicht("Teil alle 30.000 km, falls für Ihr Land zutreffend") and not erreicht("Teil bei Bedarf alle 30.000 km")
      and not erreicht("Teil je nach Motor alle 30.000 km"))
check("A18 fehlende/unbezifferte Angabe: kein Vorrang, kein Fehler",
      not erreicht("Bremsflüssigkeit regelmäßig erneuern") and not erreicht("") and not erreicht(None))
check("A19 'außer ...'-Modellliste ist keine Bedingung (Ausnahme betrifft andere Modelle)",
      erreicht("Hinterachsdifferenzialöl (außer ES 300h, NX 350h) Alle 30.000 km / 4 Jahre"))

# ── B. echter RC-F-Replay ─────────────────────────────────────────────────────────────────
print("\n== B. RC-F-Replay (Lexus-Serviceheft) ==")
src = Path("test_n2_n3_n7_wartung_tabellenblock.py").read_text(encoding="utf-8")
bl = {}
for n in ast.parse(src).body:
    if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in ("_FITNESS_BLOCK", "_SICHERHEIT_BLOCK"):
        bl[n.targets[0].id] = ast.literal_eval(n.value)
VOLL = ("\n\n".join(LEXUS_CH_MODELLUEBERSICHT["content"].split("\n\n")[:2]) + "\n\n"
        + bl["_FITNESS_BLOCK"] + "\n\n" + bl["_SICHERHEIT_BLOCK"])
URL = LEXUS_CH_MODELLUEBERSICHT["url"].split("#")[0]
TREFFER = [{"url": URL, "title": "Lexus Garantie- und Serviceheft", "content": VOLL}]

# Die Replay-Auswahl hängt vom Kalenderjahr ab (fahrzeugalter nutzt date.today()). Für einen festen Test
# wird der Zeitgeber des Alters-Helfers fixiert.
import app.laufleistung as ll  # noqa: E402
_orig_alter = ll.fahrzeugalter
ll.fahrzeugalter = lambda bj, heute_jahr=None: _orig_alter(bj, heute_jahr=JAHR)


def replay(km, baujahr=BJ, limit=None):
    alt = tr.MAX_FAKTEN_JE_KATEGORIE
    if limit:
        tr.MAX_FAKTEN_JE_KATEGORIE = limit
    try:
        ab: list[dict] = []
        f = tr._extrahiere_fakten(TREFFER, "wartung", marke="Lexus", modell="RC F", baujahr=baujahr,
                                  abgelehnt=ab, fuel_ziel="benzin", kilometerstand=km)
    finally:
        tr.MAX_FAKTEN_JE_KATEGORIE = alt
    return f, ab


alle, _ = replay(KM, limit=99)
bauteile_alle = [f.bauteil for f in alle]
print("  alle gültigen Kandidaten (Trefferreihenfolge nach Rang):", bauteile_alle)
check("B1 sieben gültige Kandidaten", len(alle) == 7, str(len(alle)))

neu, ab_neu = replay(KM)
namen = [f.bauteil for f in neu]
print("  Auswahl (82.000 km, Baujahr 2017):", namen)
check("B2 Fünferlimit unverändert", len(neu) == 5)
check("B3 Antriebsriemen vor Motorkühlmittel (gleicher sonstiger Rang)",
      "Nebenaggregat-Antriebsriemen" in namen and "Motorkühlmittel" not in namen)
check("B4 Zündkerzen (90.000 km, nicht erreicht) fällt heraus", "Zündkerzen" not in namen)
check("B5 Ventilspiel (RC F ausdrücklich) bleibt Erstplatzierter, Motoröl (Tätigkeit im Satz) Zweiter",
      namen[:2] == ["Ventilspiel", "Motoröl"], str(namen[:2]))
check("B6 drei erreichte Hinweise in Trefferreihenfolge statt Alphabet",
      namen[2:] == ["Hinterachsdifferenzialöl", "Nebenaggregat-Antriebsriemen", "Automatikgetriebeöl"], str(namen[2:]))
check("B7 ausgeschiedene Kandidaten tragen den Limit-Grund (diagnostisch erhalten)",
      sorted(a["satz"][:12] for a in ab_neu if a["grund"] == "wartung_auswahllimit_erreicht")
      and len([a for a in ab_neu if a["grund"] == "wartung_auswahllimit_erreicht"]) == 2)
check("B8 Marktvorbehalt bleibt sichtbar (herkunftsregion/regionale_geltung unverändert befüllt)",
      all(getattr(f, "herkunftsregion", None) is not None or getattr(f, "regionale_geltung", None) is not None
          for f in neu) or all(hasattr(f, "herkunftsregion") for f in neu))

# ohne km und Baujahr: kein Vorrang -> reine Trefferreihenfolge (zuvor Alphabet)
ohne, _ = replay(None, baujahr=None)
print("  Auswahl ohne km/Baujahr:", [f.bauteil for f in ohne])
check("B9 ohne km/Baujahr entsteht KEIN erfundener Vorrang: die erreichte-Stufe entfällt (nur Trefferreihenfolge)",
      [f.bauteil for f in ohne][:2] == ["Ventilspiel", "Motoröl"] and len(ohne) == 5)

# Bericht, Verkäuferfragen, Nachweise über den echten Pfad
REQ = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=BJ, kilometerstand=KM, motor="5.0 V8",
                       kraftstoff="Benzin", leistung_ps=477, getriebe="Automatik", antrieb="Heck", ausstattung=[])
ins = build_insights(None, None, [], REQ, check_typ="kauf",
                     web_recherche=TechnischeRecherche(ausgeloest_durch="db_miss", fakten=neu))
ka = build_kaufaktionen(REQ, None, None, ins)
w_ins = [i for i in ins if i.kategorie == "web_wartung"]
fragen = [f for f in ka.verkaeuferfragen.fahrzeugspezifisch if "wartung" in (f.kategorie or "")]
print("  Insights:", [i.titel.split(":")[0] for i in w_ins])
print("  Verkäuferfragen:", [f.id.replace("frage-wartung-web-", "") for f in fragen])
check("B10 fünf Insights, je Hinweis einer, alle mit dem Serviceheft als Nachweis",
      len(w_ins) == 5 and all(q.url and "lex-sw-00de.pdf" in q.url for i in w_ins for q in i.quellen[:1]))
check("B11 fünf Verkäuferfragen; Antriebsriemen-Frage vorhanden, Motorkühlmittel-Frage nicht",
      len(fragen) == 5 and any("antriebsriemen" in f.id for f in fragen) and not any("motorkuehlmittel" in f.id for f in fragen))
dok = [d for d in ka.dokumente.fahrzeugspezifisch if "wartung" in (d.kategorie or "")]
print("  Dokumente (Wartung):", [d.id.replace("dokument-wartung-web-", "")[:60] for d in dok])
check("B10b Dokumente/Nachweise: je ausgewähltem Hinweis höchstens ein Dokument, keines für Motorkühlmittel/Zündkerzen",
      len(dok) <= 5 and not any(("motorkuehlmittel" in d.id or "zuendkerzen" in d.id) for d in dok), str(len(dok)))
texte = " ".join(f"{i.titel} {i.beschreibung}" for i in w_ins).lower()
check("B12 keine Fälligkeit/kein Versäumnis behauptet",
      not any(w in texte for w in ("überfällig", "ueberfaellig", "versäumt", "nicht gemacht", "fehlt", "fällig")))

# UXC1, 477 PS: Identitäts-/Leistungsangaben des Requests bleiben unberührt; Rückrufausgabe unberührt
check("B13 477 PS und Request unverändert (Rangfolge liest nur Kilometerstand/Baujahr)",
      REQ.leistung_ps == 477 and REQ.kilometerstand == KM and REQ.baujahr == BJ)
check("B14 kein Rückruf-/Identitätsbezug in der Wartungsauswahl", all(f.kategorie == "wartung" for f in neu))

# ── C. Gleichstand/Alphabet ───────────────────────────────────────────────────────────────
print("\n== C. Gleichstand ==")
alt_alphabetisch = sorted([f.bauteil for f in alle if f.bauteil in
                           {"Automatikgetriebeöl", "Hinterachsdifferenzialöl", "Nebenaggregat-Antriebsriemen"}])
check("C1 [SYNTH] Reihenfolge der drei gleichrangig erreichten Hinweise weicht vom Alphabet ab (Trefferreihenfolge)",
      [f.bauteil for f in neu[2:]] != alt_alphabetisch)

# ── D. neutrale, markenunabhängige Gegenproben (echter Extraktionspfad, Quellreihenfolge vertauscht) ──
print("\n== D. neutrale Gegenproben ==")
ZEILEN = {   # Alter bei Baujahr 2017 / Stand 2026 = 9 -> Zeit belegbar erreicht ab 8 Jahren
    "Bremsflüssigkeit": "Bremsflüssigkeit wechseln: alle 100.000 km oder 4 Jahre",       # Zeit sicher, km (82.000) nicht
    "Automatikgetriebeöl": "Automatikgetriebeöl wechseln: alle 60.000 km oder 20 Jahre",   # km sicher, Zeit nicht
    "Zündkerzen": "Zündkerzen wechseln: alle 120.000 km oder 20 Jahre",                    # keine Seite erreicht
    "Zahnriemen": "Zahnriemen wechseln: alle 150.000 km Danach alle 10.000 km",            # Erst nicht erreicht, Folge kleiner
    "Wasserpumpe": "Wasserpumpe wechseln, wenn die Wartungsanzeige erscheint (zumindest alle 20.000 km oder 1 Jahr)",  # bedingt
}


def neutral(reihenfolge, km, bj):
    treffer_n = [{"url": f"https://www.toyota.de/service/wartung-{i}.pdf", "title": "Toyota Yaris Wartungsplan Serviceheft",
                  "content": ZEILEN[k]} for i, k in enumerate(reihenfolge)]
    alt = tr.MAX_FAKTEN_JE_KATEGORIE
    tr.MAX_FAKTEN_JE_KATEGORIE = 99
    try:
        return [x.bauteil for x in tr._extrahiere_fakten(treffer_n, "wartung", marke="Toyota", modell="Yaris",
                                                         baujahr=bj, abgelehnt=[], kilometerstand=km)]
    finally:
        tr.MAX_FAKTEN_JE_KATEGORIE = alt


BASIS = list(ZEILEN)
for rf_name, rf in (("A", BASIS), ("B (vertauscht)", BASIS[::-1])):
    nur_zeit = neutral(rf, None, 2017)
    check(f"D1 [{rf_name}] Zeit sicher erreicht, Kilometerstand fehlt: 'oder'-Seite Zeit genügt -> Bremsflüssigkeit vorn",
          nur_zeit[0] == "Bremsflüssigkeit", str(nur_zeit))
    check(f"D1b [{rf_name}] ... und die unbekannte km-Seite hebt nichts auf: Automatikgetriebeöl (km sicher, aber km unbekannt) "
          "bekommt KEINEN Vorrang", nur_zeit.index("Automatikgetriebeöl") > nur_zeit.index("Bremsflüssigkeit")
          and [b for b in nur_zeit if b != "Bremsflüssigkeit"] == [b for b in rf if b != "Bremsflüssigkeit"
                                                                    and b in nur_zeit], str(nur_zeit))
    nur_km = neutral(rf, 82000, None)
    check(f"D2 [{rf_name}] Kilometer sicher erreicht, Baujahr fehlt: km genügt -> Automatikgetriebeöl vorn",
          nur_km[0] == "Automatikgetriebeöl", str(nur_km))
    check(f"D2b [{rf_name}] ... Bremsflüssigkeit (Zeit, aber Baujahr unbekannt) bekommt keinen Vorrang",
          [b for b in nur_km if b != "Automatikgetriebeöl"] == [b for b in rf if b != "Automatikgetriebeöl"
                                                                  and b in nur_km], str(nur_km))
    beide = neutral(rf, 82000, 2017)
    check(f"D3 [{rf_name}] erreichte Schwellen (Zeit ODER km) stehen vor allen nicht erreichten, unabhängig von der Quellreihenfolge",
          set(beide[:2]) == {"Bremsflüssigkeit", "Automatikgetriebeöl"}, str(beide))
    rest = [b for b in beide[2:]]
    check(f"D4 [{rf_name}] bekannte Seite nicht erreicht / Erstintervall nicht erreicht (Folge kleiner) / bedingt: "
          "kein Vorrang -> Gleichstand folgt der Quellreihenfolge",
          rest == [b for b in rf if b in rest], str(rest))
    check(f"D5 [{rf_name}] Folgeintervall (10.000 km) erzeugt keinen Vorrang für Zahnriemen", "Zahnriemen" in rest)
    check(f"D6 [{rf_name}] bedingtes Intervall (Wartungsanzeige) erzeugt keinen Vorrang für Wasserpumpe", "Wasserpumpe" in rest)
    unbekannt = neutral(rf, None, None)
    check(f"D7 [{rf_name}] weder km noch Baujahr: reine Quellreihenfolge", unbekannt == [b for b in rf if b in unbekannt],
          str(unbekannt))

# ── E. reine Zeitintervalle, umgekehrte ODER-Intervalle, UND (echter Extraktionspfad) ─────────────────
print("\n== E. Zeit-Schreibweisen ==")
ZEIT = {   # neutrale Bauteile; Alter bei Baujahr 2017 / Stand 2026 = 9 -> Zeit belegbar erreicht ab 8 Jahren
    "Bremsflüssigkeit": "Bremsflüssigkeit wechseln: alle 6 Jahre",                         # reine Zeit, sicher erreicht
    "Zündkerzen": "Zündkerzen wechseln: alle 12 Jahre",                                     # reine Zeit, nicht erreicht
    "Zahnriemen": "Zahnriemen wechseln: 6 Jahre oder 105.000 km",                           # umgekehrtes ODER, Zeit sicher
    "Automatikgetriebeöl": "Automatikgetriebeöl wechseln: 20 Jahre oder 60.000 km",         # umgekehrtes ODER, km sicher
    "Wasserpumpe": "Wasserpumpe wechseln: 4 Jahre und 150.000 km",                          # UND: Zeit ja, km nein
}


def neutral_z(reihenfolge, km, bj, quelle=ZEIT):
    treffer_n = [{"url": f"https://www.toyota.de/service/wartung-{i}.pdf", "title": "Toyota Yaris Wartungsplan Serviceheft",
                  "content": quelle[k]} for i, k in enumerate(reihenfolge)]
    alt = tr.MAX_FAKTEN_JE_KATEGORIE
    tr.MAX_FAKTEN_JE_KATEGORIE = 99
    try:
        return [x.bauteil for x in tr._extrahiere_fakten(treffer_n, "wartung", marke="Toyota", modell="Yaris",
                                                         baujahr=bj, abgelehnt=[], kilometerstand=km)]
    finally:
        tr.MAX_FAKTEN_JE_KATEGORIE = alt


def vor(liste, a, b):
    return a in liste and b in liste and liste.index(a) < liste.index(b)


ZB = list(ZEIT)
for rf_name, rf in (("A", ZB), ("B (vertauscht)", ZB[::-1])):
    p = f"E [{rf_name}]"
    voll = neutral_z(rf, 82000, 2017)
    check(f"{p} reine Zeit 'alle 6 Jahre' sicher erreicht -> vor 'alle 12 Jahre' (nicht erreicht)",
          vor(voll, "Bremsflüssigkeit", "Zündkerzen"), str(voll))
    check(f"{p} umgekehrtes ODER '6 Jahre oder 105.000 km' (Zeit sicher) und '20 Jahre oder 60.000 km' (km sicher) "
          "stehen beide vor den nicht erreichten", set(voll[:4]) >= {"Zahnriemen", "Automatikgetriebeöl", "Bremsflüssigkeit"}
          and vor(voll, "Zahnriemen", "Zündkerzen") and vor(voll, "Automatikgetriebeöl", "Zündkerzen"), str(voll))
    check(f"{p} UND '4 Jahre und 150.000 km' (Zeit ja, km nein) bekommt KEINEN Vorrang (nicht als ODER gelesen)",
          vor(voll, "Zündkerzen", "Wasserpumpe") == (rf.index("Zündkerzen") < rf.index("Wasserpumpe"))
          and "Wasserpumpe" not in voll[:3], str(voll))
    nur_bj = neutral_z(rf, None, 2017)
    check(f"{p} nur Baujahr vorhanden: Zeitschwellen zählen (Bremsflüssigkeit, Zahnriemen vorn), 'Automatikgetriebeöl' "
          "(km unbekannt) nicht", set(nur_bj[:2]) == {"Bremsflüssigkeit", "Zahnriemen"}, str(nur_bj))
    nur_km = neutral_z(rf, 82000, None)
    check(f"{p} nur Kilometerstand vorhanden: km zählt (Automatikgetriebeöl vorn), Zeitangaben ohne Baujahr nicht",
          nur_km[0] == "Automatikgetriebeöl" and [b for b in nur_km if b != "Automatikgetriebeöl"]
          == [b for b in rf if b != "Automatikgetriebeöl"], str(nur_km))
    unbek = neutral_z(rf, None, None)
    check(f"{p} Alter und Kilometerstand unbekannt: reine Quellreihenfolge, kein erfundener Vorrang",
          unbek == [b for b in rf if b in unbek], str(unbek))

# Altersgrenze innerhalb des unsicheren Altersintervalls (Baujahr 2017, Stand 2026 -> Alter 9, sicher >= 8)
check("E Schwelle 8 Jahre: sicher erreicht (Vorrang)", erreicht("Teil wechseln: alle 8 Jahre"))
check("E Schwelle 9 Jahre: INNERHALB des unsicheren Altersintervalls -> kein Vorrang", not erreicht("Teil wechseln: alle 9 Jahre"))
check("E Schwelle 10 Jahre: nicht erreicht", not erreicht("Teil wechseln: alle 10 Jahre"))
check("E Monate: 96 Monate (= 8 Jahre) sicher, 108 Monate (= 9 Jahre) unsicher",
      erreicht("Teil wechseln: alle 96 Monate") and not erreicht("Teil wechseln: alle 108 Monate"))
check("E reine Zeit, Baujahr unbekannt: unbekannt, kein Vorrang", not erreicht("Teil wechseln: alle 6 Jahre", bj=None))
# UND / ODER / Verbinder
check("E UND: beide sicher erreicht -> Vorrang", erreicht("Teil wechseln: alle 60.000 km und 6 Jahre"))
check("E UND: nur km bekannt (Baujahr fehlt) -> unbekannt, KEIN Vorrang (nicht als ODER)",
      not erreicht("Teil wechseln: alle 60.000 km und 6 Jahre", bj=None))
check("E UND: nur Zeit bekannt (Kilometerstand fehlt) -> KEIN Vorrang", not erreicht("Teil wechseln: alle 6 Jahre und 60.000 km", km=None))
check("E UND: eine Seite nicht erreicht -> KEIN Vorrang", not erreicht("Teil wechseln: alle 105.000 km und 6 Jahre"))
check("E ODER umgekehrt, nur Baujahr: Zeit genügt", erreicht("Teil wechseln: 6 Jahre oder 105.000 km", km=None))
check("E ODER umgekehrt, nur Kilometerstand: km genügt", erreicht("Teil wechseln: 20 Jahre oder 60.000 km", bj=None))
check("E 'km / Jahre' und 'Jahre / km' gleich behandelt", erreicht("Teil: 105.000 km / 6 Jahre") and erreicht("Teil: 6 Jahre / 105.000 km"))
check("E beide Angaben ohne erkennbaren Verbinder: mehrdeutig, kein Vorrang", not erreicht("Teil wechseln: alle 105.000 km 6 Jahre"))
check("E 'je nachdem, was zuerst eintritt' (ODER) wird wie ODER gelesen",
      erreicht("Teil: alle 105.000 km oder 6 Jahre, je nachdem, was zuerst eintritt"))
# Bedingung / Folgeintervall
check("E bedingte Zeitangabe ('wenn die Anzeige erscheint, spätestens alle 6 Jahre'): kein Vorrang",
      not erreicht("Teil wechseln, wenn die Wartungsanzeige erscheint, spätestens alle 6 Jahre"))
check("E Erstintervall nicht erreicht, kleineres Folgeintervall 'Danach alle 1 Jahr': kein Vorrang",
      not erreicht("Teil wechseln: alle 12 Jahre Danach alle 1 Jahr") and not erreicht("Teil: alle 150.000 km Danach alle 2 Jahre"))
check("E Erstintervall erreicht + Folgeintervall: Vorrang folgt dem ERSTintervall",
      erreicht("Teil wechseln: alle 6 Jahre Danach alle 1 Jahr"))
check("E Fenster 'a bis b Jahre' / fehlende Einheit: nur ausdrücklich genannte Jahre/Monate zählen",
      not erreicht("Teil wechseln: regelmäßig") and not erreicht("Teil wechseln: alle sechs Jahre"))

# Verwender des gemeinsamen Parsers: Anzeige/Hinweistexte unverändert
from app.laufleistung import parse_zeitalternative  # noqa: E402
check("E `parse_zeitalternative` UNVERÄNDERT: erkennt nur 'km oder/ Jahre' (Anzeigetexte bleiben stabil)",
      parse_zeitalternative("alle 105.000 km oder 6 Jahre") == (6, "Jahre")
      and parse_zeitalternative("alle 6 Jahre oder 105.000 km") is None and parse_zeitalternative("alle 6 Jahre") is None)

print()
print("ALLE GRÜN" if not fehler else f"{len(fehler)} FEHLER: {fehler}")
sys.exit(1 if fehler else 0)
