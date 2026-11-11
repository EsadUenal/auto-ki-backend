"""
Wartungspaket F1-F5 (Variante a): Modellbereich -> Wartungsauswahl -> Laufleistung -> Aktionen -> sichtbare
Fragen und Dokumente.

  F1  "Für <Modellliste>"-Überschriften als Geltungsbereich (fremde Bereiche entfallen)
  F2  Gleichstand im Aktionslimit = Reihenfolge der Wartungsauswahl (statt Alphabet der Aktions-ID)
  (a) gekappte ausgewählte Wartungspunkte bleiben in Fragen UND Dokumenten konkret sichtbar; 6 Einzelaktionen
  F3  Zeittexte nur bei separat belegter Zeitschwelle; Aktionsrang aus derselben Schwellenlogik
  F4  strukturiertes Bauteil als Schlüssel (kein Doppelschlüssel bei "Falls ... verbaut")
  F5  Steuerzeichen (U+0007) am Eingang bereinigen, Struktur bleibt erhalten

Netzsperre + Temp-DB VOR dem App-Import. Produktionsnaher Aufruf: Insights -> Laufleistungskontext -> Kaufaktionen.
Echte PDF-Seiten: Downloads/lex-sw-00de.pdf (lokal, pypdf), sonst nur die Fixture-Teile. [REKON] = gekennzeichnete
Rekonstruktion der fließenden Darstellung (echter Seitentext 17 an das echte Tavily-Rendering gehängt), KEIN
aufgezeichneter Rohtext des Live-Requests. [SYNTH] = synthetische Gegenprobe.

    python test_wartung_aktionen_modellbereich.py
"""
import ast
import json
import os
import socket
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="wartung_aktionen_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_TMP, "chroma")


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


socket.create_connection = _gesperrt
socket.socket.connect = _gesperrt
print("Netzsperre:", socket.create_connection is _gesperrt, "| Temp-DB:", os.environ["AUTO_KI_DB_PATH"])
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)

from app import database as _db  # noqa: E402
_db.ensure_tables()
import app.laufleistung as ll  # noqa: E402
import app.technical_research as tr  # noqa: E402
from app.evidence import build_insights  # noqa: E402
from app.kaufaktionen import MAX_SPEZIFISCH_PRO_BEREICH, build_kaufaktionen  # noqa: E402
from app.laufleistung import build_laufleistungskontext  # noqa: E402
from app.models import KaufCheckRequest, TechnischeRecherche  # noqa: E402
from app.web_search import bereinige_steuerzeichen, bereinige_treffer  # noqa: E402
from test_rc_f_lexus_care_wartungsplan_real import LEXUS_CH_MODELLUEBERSICHT  # noqa: E402

fehler: list[str] = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        fehler.append(name)


# Alter pinnen (Stand 2026), damit das Ergebnis nicht vom Kalenderjahr abhängt.
_orig_alter = ll.fahrzeugalter
ll.fahrzeugalter = lambda bj, heute_jahr=None: _orig_alter(bj, heute_jahr=2026)
HIER = Path(__file__).parent

# ── F5: Steuerzeichen ─────────────────────────────────────────────────────────────────────
print("== F5 Steuerzeichen ==")
roh = "Motoröl und -filter:\x07 Bei Fahrzeugen\tmit\nÖlwechsel\x0cSystem\x00x"
sauber = bereinige_steuerzeichen(roh)
check("F5-1 U+0007 und weitere C0-Zeichen entfernt", "\x07" not in sauber and "\x00" not in sauber)
check("F5-2 Zeilenumbruch und Tab bleiben erhalten", "\n" in sauber and "\t" in sauber)
check("F5-3 Seitenvorschub wird zur Zeilengrenze (Wortgrenze bleibt)", "Ölwechsel\nSystem" in sauber)
check("F5-4 kein Textverlust: alle Wörter vorhanden",
      all(w in sauber for w in ("Motoröl", "-filter:", "Bei", "Fahrzeugen", "mit", "Ölwechsel", "System")))
check("F5-5 Wortgrenze statt Verkleben ('filter:\\x07 Bei' bleibt getrennt)", "filter: Bei" in sauber.replace("  ", " "))
check("F5-6 None/leer/Nicht-Text unverändert", bereinige_steuerzeichen(None) is None and bereinige_steuerzeichen("") == ""
      and bereinige_steuerzeichen(5) == 5)
check("F5-7 sauberer Text bleibt identisch (Tabellenstruktur: Pipes, Umlaute)",
      bereinige_steuerzeichen("| Bauteil | Intervall |\nBremsflüssigkeit — alle 2 Jahre") == "| Bauteil | Intervall |\nBremsflüssigkeit — alle 2 Jahre")
treffer_roh = [{"url": "u", "title": "T\x07", "content": "a\x07b", "raw_content": "c\x07d"}, {"url": "v"}]
treffer_sauber = bereinige_treffer(treffer_roh)
check("F5-8 Provider-Eingang: title/content/raw_content bereinigt, Original unverändert, Treffer ohne Text bleibt",
      "\x07" not in json.dumps(treffer_sauber, ensure_ascii=False) and "\x07" in treffer_roh[0]["title"]
      and treffer_sauber[1] == {"url": "v"})

# ── F1: Modellbereiche ────────────────────────────────────────────────────────────────────
print("\n== F1 Modellbereiche ==")
F = tr._entferne_fremde_modellbereiche


def f1(text, modell="RC F"):
    ab: list[dict] = []
    return F(text, modell, url="u", abgelehnt=ab), ab


o, ab = f1("Alle 90.000 km*3: nur für Finnland, Norwegen und Schweden 17 LEXUS CARE Für UX 300e Lexus Care-Komplettservice: "
           "alle 30.000 km oder 2 Jahre. Dieser Service beinhaltet Inspektionen von Bremsen.")
check("F1-1 fremder Bereich (Fließtext, 'Für UX 300e') entfällt samt Folgesätzen; Vorspann bleibt",
      "Inspektionen" not in o and "Zwischen" not in o and o.startswith("Alle 90.000 km*3") and len(ab) == 1
      and ab[0]["grund"] == "wartung_bereich_fremdes_modell", o)
o, ab = f1("LEXUS CARE-WARTUNGSPLAN Für ES 300h, IS 300h, RC F , UX 200 und UX 250h\nKomplettservice alle 30.000 km.")
check("F1-2 passender Bereich (Zielmodell in der Liste) bleibt unverändert", "Komplettservice" in o and not ab)
o, ab = f1("Für RC F und UX 300e gilt: Zwischenservice alle 15.000 km.")
check("F1-3 gemeinsamer Bereich (Ziel + Fremdmodell) bleibt", "Zwischenservice" in o and not ab)
o, ab = f1("Für UX 300e Zwischenservice alle 15.000 km. 2.10. TECHNISCHE DATEN Motoröl alle 10.000 km.")
check("F1-4 fremder Bereich endet an der nummerierten Abschnittsüberschrift (nicht über Folgeabschnitte getragen)",
      "Zwischenservice" not in o and "Motoröl alle 10.000 km" in o, o)
o, ab = f1("Für UX 300e Zwischenservice alle 15.000 km. Für RC F Ventilspiel alle 90.000 km.")
check("F1-5 fremder Bereich endet an der nächsten Modellüberschrift; der passende folgt", "Ventilspiel" in o
      and "Zwischenservice" not in o)
o, ab = f1("Für Hybrid-Modelle Zwischenservice alle 15.000 km. Für Ihr Land: Bremsen alle 30.000 km.")
check("F1-6 unklare Liste ('Hybrid-Modelle', 'Ihr Land') eröffnet keinen Bereich: nichts verworfen, nichts erfunden",
      "Zwischenservice" in o and "Bremsen" in o and not ab)
o, ab = f1("Zwischenservice alle 15.000 km oder einmal jährlich, je nachdem, was zuerst eintritt.")
check("F1-7 ohne Überschrift unverändert", "Zwischenservice" in o and not ab)
o, ab = f1("Für UX 300e Zwischenservice alle 15.000 km.", modell=None)
check("F1-8 ohne Zielmodell kein Eingriff", "Zwischenservice" in o and not ab)
o, ab = f1("Für RC F Ventilspiel alle 90.000 km. Für Mazda MX-5 Ölwechsel alle 10.000 km.", modell="Mazda MX-5")
check("F1-9 [SYNTH] neutral, anderes Ziel: fremder Bereich ('RC F') entfällt, der eigene Bereich ('Für Mazda MX-5', "
      "Markenname in der Überschrift) endet den fremden und bleibt erhalten", "Ventilspiel" not in o and "Ölwechsel" in o, o)
o, ab = f1("Für GR 86 und GT 86 gilt: Ölwechsel alle 10.000 km. Für YA 100 Bremsen alle 20.000 km.", modell="GT 86")
check("F1-10 [SYNTH] neutral, anderes Modell: passende Liste ('GT 86') bleibt, fremde ('YA 100') entfällt",
      "Ölwechsel" in o and "Bremsen" not in o, o)

pdf = Path(os.path.expanduser("~/Downloads/lex-sw-00de.pdf"))
SEITEN = {}
if pdf.exists():
    from pypdf import PdfReader
    _r = PdfReader(str(pdf))
    SEITEN = {n: _r.pages[n - 1].extract_text() for n in (4, 15, 16, 17)}
src = (HIER / "test_n2_n3_n7_wartung_tabellenblock.py").read_text(encoding="utf-8")
BL = {n.targets[0].id: ast.literal_eval(n.value) for n in ast.parse(src).body
      if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in ("_FITNESS_BLOCK", "_SICHERHEIT_BLOCK")}
P = LEXUS_CH_MODELLUEBERSICHT["content"].split("\n\n")
URL = LEXUS_CH_MODELLUEBERSICHT["url"].split("#")[0]


def extraktion(text, limit=None, km=82000, bj=2017, modell="RC F", marke="Lexus"):
    alt = tr.MAX_FAKTEN_JE_KATEGORIE
    if limit:
        tr.MAX_FAKTEN_JE_KATEGORIE = limit
    ab: list[dict] = []
    try:
        f = tr._extrahiere_fakten([{"url": URL, "title": "WARTUNG UND GARANTIE", "content": text}], "wartung",
                                  marke=marke, modell=modell, baujahr=bj, abgelehnt=ab, fuel_ziel="benzin",
                                  kilometerstand=km)
    finally:
        tr.MAX_FAKTEN_JE_KATEGORIE = alt
    return f, ab


if SEITEN:
    echt, ab_echt = extraktion("\n\n".join(SEITEN[n] for n in (4, 15, 16, 17)))
    check("F1-11 echte PDF-Seiten 4/15/16/17: Auswahl enthält Automatikgetriebeöl, keinen UX-300e-/Bremsen-Fakt",
          "Automatikgetriebeöl" in [f.bauteil for f in echt] and "Bremsen" not in [f.bauteil for f in echt],
          str([f.bauteil for f in echt]))
    s17 = " ".join(SEITEN[17].split())
    FOLGE = s17[s17.find("Lexus Care-Zwischenservice"):s17.find("Die Tabellen unten")].strip()
    REKON = P[0] + "\n\n" + P[1] + "\n\n" + BL["_FITNESS_BLOCK"] + "\n\n" + BL["_SICHERHEIT_BLOCK"] + ". " + FOLGE
    rekon, ab_rekon = extraktion(REKON)
    namen = [f.bauteil for f in rekon]
    print("  [REKON] Auswahl:", namen)
    check("F1-12 [REKON] fließende Darstellung: kein Bremsen-Hinweis aus dem UX-300e-Abschnitt", "Bremsen" not in namen, str(namen))
    check("F1-13 [REKON] Automatikgetriebeöl bleibt in der Fünferauswahl", "Automatikgetriebeöl" in namen and len(namen) == 5, str(namen))
    check("F1-14 [REKON] Diagnose 'wartung_bereich_fremdes_modell' (Für UX 300e) vorhanden",
          any(a["grund"] == "wartung_bereich_fremdes_modell" and "UX 300e" in a["satz"] for a in ab_rekon))
    check("F1-15 [REKON] der RC-F-Abschnitt (Seite 15) bleibt: Motoröl, Differenzial, Ventilspiel vorhanden",
          {"Ventilspiel", "Motoröl", "Hinterachsdifferenzialöl"} <= set(namen))
else:
    print("  HINWEIS: lex-sw-00de.pdf nicht lokal -- PDF-Prüfungen F1-11..15 übersprungen")
    REKON = None

# ── produktionsnaher Aufruf ───────────────────────────────────────────────────────────────
REQ = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000, motor="5.0 V8",
                       kraftstoff="Benzin", leistung_ps=477, getriebe="Automatik", ausstattung=[],
                       vorbesitzer=2, scheckheftgepflegt=True)
prod = json.load(open(HIER / "tests" / "fixtures" / "prod_rueckruf_7704_canonical_2026-11-11.json", encoding="utf-8"))
R7704 = dict(prod["zeilen"][2], id=11025, _trust="verified")
R12358 = {"id": 90002, "baureihe_id": None, "datum": "2023-01-19", "betroffene_baujahre": "2014-2017",   # [SYNTH] nach Berichtstext
          "mangel": "Kraftstoffaustritt kann unter Umständen zum Brand führen.",
          "abhilfe": "Das Kraftstofftankentlüftungsrohr des Kraftstofftanks wird ausgetauscht.", "kba_referenz": "12358",
          "canonical_make": "LEXUS", "canonical_nameplate": "RC-F", "_trust": "verified",
          "eingrenzung_amtlich": "RC-F", "prod_von_amtlich": 2014, "prod_bis_amtlich": 2017}


def kette(req, fakten, rueckrufe):
    ins = build_insights(None, None, [], req, check_typ="kauf",
                         web_recherche=TechnischeRecherche(ausgeloest_durch="db_miss", fakten=fakten),
                         canonical_rueckrufe=rueckrufe)
    lk = build_laufleistungskontext(req, ins, heute_jahr=2026)
    ka = build_kaufaktionen(req, None, None, ins, laufleistungskontext=lk)
    return ins, lk, ka


def sichtbar(aktionen, bauteil):
    return any(bauteil.lower() in (a.titel + " " + a.aktion).lower() for a in aktionen)


print("\n== Kette: 2 Rückrufe + 5 ausgewählte Wartungen (Rekonstruktion/echte Seiten, laufleistungskontext) ==")
quelle = REKON or "\n\n".join([P[0], P[1], BL["_FITNESS_BLOCK"], BL["_SICHERHEIT_BLOCK"]])
fuenf, _ = extraktion(quelle)
ins, lk, ka = kette(REQ, fuenf, [R7704, R12358])
bauteile = [f.bauteil for f in fuenf]
print("  Auswahl:", bauteile)
check("A1 fünf Wartungsinsights in Auswahlreihenfolge", [i.titel.split(":")[0] for i in ins if i.kategorie == "web_wartung"] == bauteile)
for bereich in ("verkaeuferfragen", "dokumente"):
    p = getattr(ka, bereich)
    spez = p.fahrzeugspezifisch
    check(f"A2 {bereich}: genau {MAX_SPEZIFISCH_PRO_BEREICH} Einzelaktionen (Limit unverändert)", len(spez) == MAX_SPEZIFISCH_PRO_BEREICH, str(len(spez)))
    check(f"A3 {bereich}: genau zwei Rückrufaktionen, keine Duplikate",
          len([a for a in spez if a.id.split("-", 1)[1].startswith("rueckruf")]) == 2
          and len({a.id for a in spez}) == len(spez))
    check(f"A4 {bereich}: ALLE fünf ausgewählten Wartungspunkte konkret sichtbar",
          all(sichtbar(spez, b) for b in bauteile), str([b for b in bauteile if not sichtbar(spez, b)]))
    ev = {e for a in spez for e in a.evidence_ids}
    wart_ids = {i.id for i in ins if i.kategorie == "web_wartung"}
    check(f"A5 {bereich}: Evidenzverweise aller fünf Wartungsinsights erhalten", wart_ids <= ev, str(wart_ids - ev))
    check(f"A6 {bereich}: Evidenzverweise nur auf existierende Insights", ev <= {i.id for i in ins})
    # A13: Evidenz bleibt dem RICHTIGEN Bauteil zugeordnet. Jede Evidenz-ID einer Wartungsaktion gehört zu einem
    # Insight, dessen Bauteil in DEMSELBEN sichtbaren Text dieser Aktion benannt ist (eigener Punkt oder
    # zusammengefasster Restpunkt) -- und keine ID steht in zwei verschiedenen Aktionen.
    nach_id = {i.id: i for i in ins}
    wa = [a for a in spez if a.kategorie in ("wartung", "web_wartung")]
    falsch = [(a.id, e) for a in wa for e in a.evidence_ids
              if e not in nach_id or (nach_id[e].titel.split(": ")[0].replace("Falls ", "").replace(" verbaut", "").lower()
                                      not in (a.titel + " " + a.aktion).lower())]
    check(f"A13 {bereich}: jede Wartungs-Evidenz-ID steht in einer Aktion, die IHR Bauteil sichtbar benennt", not falsch, str(falsch))
    alle_ev = [e for a in spez for e in a.evidence_ids]
    check(f"A14 {bereich}: keine Evidenz-ID doppelt über mehrere Aktionen", len(alle_ev) == len(set(alle_ev)), str(alle_ev))
    wartung = [a for a in spez if a.kategorie in ("wartung", "web_wartung")]
    check(f"A7 {bereich}: Wartungsaktionen in Wartungsauswahl-Reihenfolge (Gleichstand nicht alphabetisch)",
          [next(b for b in bauteile if b.lower() in a.titel.lower()) for a in wartung] == bauteile[:len(wartung)],
          str([a.id for a in wartung]))
    rest = [b for b in bauteile if b not in [next((x for x in bauteile if x.lower() in a.titel.lower()), None) for a in wartung]]
    host = wartung[-1]
    check(f"A8 {bereich}: gekappter Punkt steht im Text der letzten Wartungsaktion samt Tätigkeit, Intervall, Marktvorbehalt",
          all(f"„{b}“" in host.aktion for b in rest) and "Weitere ausgewählte Wartungspunkte" in host.aktion
          and "Marktvorbehalt der Quelle" in host.aktion and ("Prüfung" in host.aktion or "Wechsel" in host.aktion),
          host.aktion[-300:])
    # A9 (fachlich neu gefasst): die Basisliste dieses Fahrzeugs ist gegenüber 445b34c UNVERÄNDERT (Vergleichslauf mit
    # identischer Eingabe) und deckt die Servicehistorie ab. Scheckheft und Vorbesitzer sind im Inserat ANGEGEBEN
    # (vorbesitzer=2, scheckheft=True): dazu gibt es keine offene Basisfrage, und es darf auch keine entstehen. Die
    # Fahrzeugpapiere (ZB II) decken die Halter-/Vorbesitzerangabe in der Dokumentenliste.
    basis_ids = [a.id for a in p.basis]
    if bereich == "verkaeuferfragen":
        erwartet = ["frage-basis-eigentuemer", "frage-basis-finanzierung", "frage-basis-unfall", "frage-basis-maengel",
                    "frage-basis-reparaturen", "frage-basis-wartung", "frage-basis-fluessigkeit", "frage-basis-hu",
                    "frage-basis-werkstattpruefung", "frage-basis-nutzung", "frage-basis-standzeit",
                    "frage-basis-import", "frage-basis-schluessel", "frage-basis-raeder", "frage-basis-tuning"]
        servicehist = [a for a in p.basis if a.id == "frage-basis-wartung" and "Wartung" in a.titel]
    else:
        erwartet = ["dokument-basis-zb1", "dokument-basis-zb2", "dokument-basis-fin", "dokument-basis-ausweis",
                    "dokument-basis-hu", "dokument-basis-serviceheft", "dokument-basis-wartungsrechnungen",
                    "dokument-basis-reparaturrechnungen", "dokument-basis-km_plausibel", "dokument-basis-schluessel",
                    "dokument-basis-bordunterlagen", "dokument-basis-umbauten", "dokument-basis-kaufvertrag"]
        servicehist = [a for a in p.basis if a.id in ("dokument-basis-serviceheft", "dokument-basis-wartungsrechnungen")]
    check(f"A9 {bereich}: Basisliste identisch zu 445b34c (Reihenfolge und Inhalt der IDs)", basis_ids == erwartet, str(basis_ids))
    check(f"A9b {bereich}: Servicehistorie über die Basis abgedeckt", bool(servicehist), str(basis_ids))
    check(f"A9c {bereich}: kein Basis-/Einzelpunkt trägt Evidence-Verweise der Wartungsrest-Zusammenfassung",
          all(a.evidence_ids == [] for a in p.basis))
check("A10 Rückruf-Insights unverändert zwei (7704 und 12358), je mit KBA-Referenz",
      len([i for i in ins if i.kategorie == "rueckruf"]) == 2
      and {q.ref for i in ins if i.kategorie == "rueckruf" for q in i.quellen if q.ref} >= {"7704", "12358"})
texte = " ".join(a.titel + " " + a.aktion for b in ("verkaeuferfragen", "dokumente") for a in getattr(ka, b).fahrzeugspezifisch).lower()
check("A11 keine Fälligkeit/Versäumnis behauptet", not any(w in texte for w in ("überfällig", "versäumt", "nicht durchgeführt", " fehlt ")))
check("A12 kein U+0007 und kein Steuerzeichen in sichtbaren Texten", all(
    ord(c) >= 32 or c in "\n\t" for c in texte))

print("\n== F3 Zeittexte / Rang ==")
hint = {w.bauteil: w for w in lk.wartungshinweise}
for b in ("Hinterachsdifferenzialöl", "Ventilspiel", "Nebenaggregat-Antriebsriemen"):
    h = hint.get(b)
    check(f"F3-1 {b}: separat belegte Zeitschwelle -> Text sagt 'bereits erreicht' (Alter mindestens 8 Jahre)",
          h is not None and "nach dem Baujahr bereits erreicht" in h.hinweis and "mindestens 8 Jahre" in h.hinweis
          and "bleibt deshalb offen" not in h.hinweis and "nicht ausschließen" not in h.hinweis, h.hinweis[-260:] if h else "fehlt")
mo = hint.get("Motoröl")
check("F3-2 Motoröl (bedingtes Intervall: Hinweismeldung/Land): Zeit bleibt OFFEN, keine Erreicht-Behauptung",
      mo is not None and "bleibt deshalb offen" in mo.hinweis and "bereits erreicht (Fahrzeugalter" not in mo.hinweis)
atf = hint.get("Automatikgetriebeöl")
# F3-3 (fachlich neu gefasst): die Quelle nennt für das Automatikgetriebeöl "Alle 60.000 km / 4 Jahre" -- also EINE
# Zeitschwelle (4 Jahre) und ein Marktvorbehalt ("falls für Ihren Markt zutreffend") als ANGEHÄNGTER Satz. Bei
# Baujahr 2017 ist die Zeitschwelle belegbar erreicht (Alter mindestens 8 Jahre >= 4). Der Marktvorbehalt macht das
# Intervall nicht "bedingt", bleibt aber sichtbar und bestätigt keine Geltung für Deutschland.
check("F3-3a Automatikgetriebeöl: Erstintervall 60.000 km / 4 Jahre, Marktvorbehalt NICHT Teil des Intervalls",
      atf is not None and ll.erstintervall_text(atf.intervall_text).strip().endswith("4 Jahre")
      and not ll._BEDINGT.search(ll.erstintervall_text(atf.intervall_text)), atf.intervall_text if atf else "fehlt")
check("F3-3b Automatikgetriebeöl: 4-Jahres-Schwelle als erreicht benannt (mindestens 8 Jahre), ohne Fälligkeit/Versäumnis",
      atf is not None and "(oder 4 Jahre) ist nach dem Baujahr bereits erreicht" in atf.hinweis
      and "mindestens 8 Jahre" in atf.hinweis and "geht aus den vorliegenden Daten nicht hervor" in atf.hinweis
      and not any(w in atf.hinweis.lower() for w in ("überfällig", "versäumt", "fällig")), atf.hinweis[-300:] if atf else "")
check("F3-3c Automatikgetriebeöl: Marktvorbehalt bleibt sichtbar und bestätigt keine Geltung in Deutschland",
      atf is not None and "Die Quelle schränkt die Angabe selbst ein" in atf.hinweis
      and "in Deutschland gilt, ist nicht bestätigt" in atf.hinweis)
rang = {a.id: a.rang for a in ka.verkaeuferfragen.fahrzeugspezifisch}
check("F3-4 Aktionsrang: Antriebsriemen (km entfernt, Zeit belegt) = 750 wie die erreichten Punkte",
      any("antriebsriemen" in k and v == 750 for k, v in rang.items()), str(rang))

# neutral: Alter/Kilometer/Bedingung/Folgeintervall (echter Pfad: Extraktion -> Insight -> Laufleistung)
ZEILEN = {
    "Zahnriemen": "Zahnriemen wechseln: alle 150.000 km oder 6 Jahre",               # km nein, Zeit ja (Alter 9)
    "Bremsflüssigkeit": "Bremsflüssigkeit wechseln: alle 30.000 km oder 15 Jahre",    # km ja, Zeit nein
    "Zündkerzen": "Zündkerzen wechseln: alle 60.000 km oder 4 Jahre",                  # beides
    "Wasserpumpe": "Wasserpumpe wechseln, wenn die Wartungsanzeige erscheint (zumindest alle 150.000 km oder 1 Jahr)",  # bedingt
    "Keilriemen": "Keilriemen prüfen: alle 150.000 km oder 12 Jahre Danach alle 20.000 km oder 1 Jahr",  # Folge kleiner
}


def neutral(km, bj, zeilen=ZEILEN):
    treffer_n = [{"url": f"https://www.toyota.de/service/wartung-{i}.pdf", "title": "Toyota Yaris Wartungsplan Serviceheft",
                  "content": z} for i, z in enumerate(zeilen.values())]
    alt = tr.MAX_FAKTEN_JE_KATEGORIE
    tr.MAX_FAKTEN_JE_KATEGORIE = 99
    try:
        fk = tr._extrahiere_fakten(treffer_n, "wartung", marke="Toyota", modell="Yaris", baujahr=bj, abgelehnt=[],
                                   kilometerstand=km)
    finally:
        tr.MAX_FAKTEN_JE_KATEGORIE = alt
    req = KaufCheckRequest(marke="Toyota", modell="Yaris", baujahr=bj, kilometerstand=km, kraftstoff="Benzin", ausstattung=[])
    return fk, *kette(req, fk, [])


fk, ins_n, lk_n, ka_n = neutral(82000, 2017)
hn = {w.bauteil: w for w in lk_n.wartungshinweise}
check("F3-5 [SYNTH] 'km oder Jahre': nur km erreicht (Bremsflüssigkeit) -> Zeittext bleibt OFFEN (True wegen km beweist keine Zeit)",
      "bleibt deshalb offen" in hn["Bremsflüssigkeit"].hinweis and "bereits erreicht (Fahrzeugalter" not in hn["Bremsflüssigkeit"].hinweis)
check("F3-6 [SYNTH] nur Zeit erreicht (Zahnriemen, km entfernt) -> Text sagt Zeit erreicht, Kilometeranteil nicht einschlägig",
      "nach dem Baujahr bereits erreicht" in hn["Zahnriemen"].hinweis and "nicht einschlägig" in hn["Zahnriemen"].hinweis)
check("F3-7 [SYNTH] km UND Zeit erreicht (Zündkerzen) -> km-Status bleibt, Zeit als erreicht benannt",
      "mindestens einmal erreicht" in hn["Zündkerzen"].hinweis and "nach dem Baujahr bereits erreicht" in hn["Zündkerzen"].hinweis)
# F3-8 (fachlich neu gefasst): "wenn die Wartungsanzeige erscheint" macht das Intervall BEDINGT. Dann gibt es keine
# belegbar erreichte Zeitschwelle -- weder der Text "nach dem Baujahr bereits erreicht" noch ein Altersbeleg darf
# erscheinen. Der Kilometerpunkt (150.000 km) ist bei 82.000 km weit entfernt; der Hinweis ist deshalb die
# VORSICHTIGE Aussage des unveränderten Pfads (km nicht einschlägig, Zeit nicht ausschließbar, Beleg klären).
wp = hn.get("Wasserpumpe")
check("F3-8a [SYNTH] bedingt (Wasserpumpe): Erstintervall ist als bedingt erkannt, keine belegbar erreichte Schwelle",
      bool(ll._BEDINGT.search(ll.erstintervall_text(wp.intervall_text if wp else "")))
      and not ll.schwelle_belegbar_erreicht_alter(wp.intervall_text if wp else "", 82000, 9))
check("F3-8b [SYNTH] bedingt (Wasserpumpe): vorsichtige Aussage -- Zeit nicht ausschließbar, Beleg klären, KEINE Erreicht-Behauptung",
      wp is not None and "nicht ausschließen, dass die zeitliche Alternative bereits erreicht ist" in wp.hinweis
      and "Vor dem Kauf klären, wann diese Arbeit zuletzt gemacht wurde" in wp.hinweis
      and "nach dem Baujahr bereits erreicht" not in wp.hinweis and "Fahrzeugalter mindestens" not in wp.hinweis, wp.hinweis[-330:] if wp else "kein Hinweis")
check("F3-9 [SYNTH] Erstintervall nicht erreicht, kleineres Folgeintervall (Keilriemen): weder Zeit-Text noch Rangvorteil",
      "Keilriemen" not in hn or "nach dem Baujahr bereits erreicht" not in hn["Keilriemen"].hinweis)
fk_u, ins_u, lk_u, ka_u = neutral(82000, None)
hu = {w.bauteil: w for w in lk_u.wartungshinweise}
check("F3-10 [SYNTH] Baujahr unbekannt: keine Zeitaussage (Zahnriemen erscheint ggf. offen, nie 'erreicht')",
      all("bereits erreicht (Fahrzeugalter" not in w.hinweis for w in hu.values()))
check("F3-11 [SYNTH] Aktionsrang: Zahnriemen (Zeit belegt) 750, Wasserpumpe/Keilriemen ohne Vorrang <750",
      {a.id: a.rang for a in ka_n.verkaeuferfragen.fahrzeugspezifisch}.get(
          next((a.id for a in ka_n.verkaeuferfragen.fahrzeugspezifisch if "zahnriemen" in a.id), ""), 0) == 750
      and all(a.rang < 750 for a in ka_n.verkaeuferfragen.fahrzeugspezifisch
              if "keilriemen" in a.id or "wasserpumpe" in a.id))

print("\n== F4 Doppelschlüssel (unbekanntes Getriebe, mehrere Wartungen, zwei Rückrufe) ==")
Z4 = {"Bremsflüssigkeit": "Bremsflüssigkeit wechseln: alle 30.000 km oder 2 Jahre",
      "Motoröl": "Motoröl wechseln: alle 15.000 km oder 1 Jahr",
      "Automatikgetriebeöl": "Automatikgetriebeöl wechseln: alle 60.000 km oder 4 Jahre",
      "Zündkerzen": "Zündkerzen wechseln: alle 60.000 km",
      "Zahnriemen": "Zahnriemen wechseln: alle 150.000 km oder 6 Jahre"}
treffer4 = [{"url": f"https://www.toyota.de/service/wartung-{i}.pdf", "title": "Toyota Yaris Wartungsplan Serviceheft", "content": z}
            for i, z in enumerate(Z4.values())]
alt = tr.MAX_FAKTEN_JE_KATEGORIE
tr.MAX_FAKTEN_JE_KATEGORIE = 99
fk4 = tr._extrahiere_fakten(treffer4, "wartung", marke="Toyota", modell="Yaris", baujahr=2017, abgelehnt=[], kilometerstand=82000)
tr.MAX_FAKTEN_JE_KATEGORIE = alt
fk4 = fk4[:5]
RN = [dict(R12358, id=90010 + k, kba_referenz=f"9902{k}", canonical_make="TOYOTA", canonical_nameplate="YARIS",
           mangel=f"[SYNTH] Rückruf {k}: Bauteil {k} kann ausfallen.", eingrenzung_amtlich="YARIS") for k in (1, 2)]
REQ4 = KaufCheckRequest(marke="Toyota", modell="Yaris", baujahr=2017, kilometerstand=82000, kraftstoff="Benzin", ausstattung=[],
                        vorbesitzer=2, scheckheftgepflegt=True)         # Getriebe UNBEKANNT
ins4, lk4, ka4 = kette(REQ4, fk4, RN)
b4 = [f.bauteil for f in fk4]
print("  Auswahl:", b4, "| Insight-Titel:", [i.titel.split(":")[0] for i in ins4 if i.kategorie == "web_wartung"])
for bereich in ("verkaeuferfragen", "dokumente"):
    p = getattr(ka4, bereich)
    atf_akt = [a for a in p.fahrzeugspezifisch if "automatikgetriebeoel" in a.id]
    check(f"F4-1 {bereich}: genau EINE Aktion zum Automatikgetriebeöl (kein Doppelschlüssel)", len(atf_akt) <= 1, str([a.id for a in atf_akt]))
    check(f"F4-2 {bereich}: alle fünf Wartungspunkte sichtbar (auch der gekappte)",
          all(sichtbar(p.fahrzeugspezifisch, b) for b in b4), str([b for b in b4 if not sichtbar(p.fahrzeugspezifisch, b)]))
    check(f"F4-3 {bereich}: {MAX_SPEZIFISCH_PRO_BEREICH} Einzelaktionen, zwei Rückrufe", len(p.fahrzeugspezifisch) == MAX_SPEZIFISCH_PRO_BEREICH
          and len([a for a in p.fahrzeugspezifisch if a.id.split("-", 1)[1].startswith("rueckruf")]) == 2)
atf_ins = [i for i in ins4 if i.kategorie == "web_wartung" and "Automatikgetriebeöl" in i.titel]
if atf_ins and atf_ins[0].titel.startswith("Falls "):
    bed = atf_ins[0].titel.split(": ", 1)[0]
    alle_texte = " ".join(a.titel + " " + a.aktion for b in ("verkaeuferfragen", "dokumente")
                          for a in getattr(ka4, b).fahrzeugspezifisch)
    check("F4-4 die Präsenz-Bedingung ('Falls ... verbaut') bleibt in der Ausgabe erhalten", bed in alle_texte, bed)
    check("F4-5 Laufleistungskarte: strukturiertes Bauteil, Bedingung als Präfix im Hinweis",
          any(w.bauteil == "Automatikgetriebeöl" and w.hinweis.startswith(bed) for w in lk4.wartungshinweise),
          str([(w.bauteil, w.hinweis[:60]) for w in lk4.wartungshinweise]))
else:
    check("F4-4 (Präsenz-Bedingung entstand nicht -- Getriebe-Präsenz im Test bekannt)", True)
keys = [a.id for a in ka4.verkaeuferfragen.fahrzeugspezifisch]
check("F4-6 Schlüssel eindeutig", len(keys) == len(set(keys)))

print("\n== F2 Gleichstand ==")
einzeln = [a.id for a in ka.verkaeuferfragen.fahrzeugspezifisch if a.kategorie in ("wartung", "web_wartung")]
alphabetisch = sorted(einzeln)
check("F2-1 Gleichstand folgt der Wartungsauswahl, NICHT dem Alphabet der Aktions-ID", einzeln != alphabetisch or len(einzeln) < 2, str(einzeln))
check("F2-2 Rückrufe behalten Vorrang (Rang 900 vor 750)",
      [a.rang for a in ka.verkaeuferfragen.fahrzeugspezifisch][:2] == [900, 900])

print("\n== F3 Einheiten: Hülle, Marktvorbehalt, Bedingung, Folgeintervall ==")
QUELLE = "Zahnriemen wechseln: alle 150.000 km oder 6 Jahre"
HUELLE = ("Die ausgewerteten Quellen nennen übereinstimmend: „" + QUELLE + "“ Für dieses Fahrzeug ist diese Angabe "
          "nicht ausreichend bestätigt.")
MARKT = (" Die Quelle schränkt sich selbst ein (\"falls für Ihren Markt zutreffend\"). Ob das für Ihr Fahrzeug in "
         "Deutschland zutrifft, ist nicht bestätigt. Mit dem fahrzeugeigenen Wartungsplan oder einer Vertragswerkstatt abgleichen.")
BED = (" Ob diese Bedingung auf dieses Fahrzeug zutrifft, ist anhand der vorliegenden Angaben nicht geklärt: zuerst die "
       "technische Anwendbarkeit klären.")
check("F3-12 Übereinstimmungs-Hülle wird um das Zitat entfernt (nicht abgeschnitten): Aussage bleibt vollständig",
      ll.quellenaussage(HUELLE) == QUELLE and ll.erstintervall_text(HUELLE) == QUELLE, repr(ll.quellenaussage(HUELLE)))
check("F3-13 Hülle + Marktvorbehalt: Aussage unverändert, Marktvorbehalt als solcher erkannt, nicht als Bedingung",
      ll.quellenaussage(HUELLE + MARKT) == QUELLE and ll.hat_quellenvorbehalt(HUELLE + MARKT)
      and not ll._BEDINGT.search(ll.erstintervall_text(HUELLE + MARKT)))
check("F3-14 Schwelle bei Zeit erreicht (Alter 9 >= 6 Jahre) auch mit Hülle/Marktvorbehalt/ungeklärter Bedingung",
      all(ll.schwelle_belegbar_erreicht_alter(t, 82000, 9) for t in (QUELLE, HUELLE, QUELLE + MARKT, HUELLE + MARKT + BED)))
check("F3-15 nur Kilometer erreicht (30.000 km bei 82.000 km), Zeit (15 Jahre) nicht: Schwelle erreicht über den Kilometerpunkt",
      ll.schwelle_belegbar_erreicht_alter("Bremsflüssigkeit wechseln: alle 30.000 km oder 15 Jahre", 82000, 9) is True
      and ll.zeitalternative_belegbar_erreicht(9, (15, "Jahre")) is False)
check("F3-16 Alter unbekannt: Zeitschwelle nie belegbar erreicht, und ein reines Zeitintervall ohne Alter nie",
      ll.schwelle_belegbar_erreicht_alter("Zahnriemen wechseln: alle 6 Jahre", 82000, None) is False)
check("F3-17 technische Bedingung allein ist keine Intervallbedingung, bestätigt aber auch keine Anwendbarkeit (Text bleibt außerhalb)",
      ll.quellenaussage(QUELLE + BED) == QUELLE)
check("F3-18 echte Intervallbedingung ('wenn die Wartungsanzeige erscheint') bleibt bedingt, auch mit Marktvorbehalt",
      ll.schwelle_belegbar_erreicht_alter("Wasserpumpe wechseln, wenn die Wartungsanzeige erscheint (alle 30.000 km)" + MARKT, 82000, 9) is False)
check("F3-19 Folgeintervall zählt nicht: 'alle 150.000 km Danach alle 20.000 km' bei 82.000 km nicht erreicht",
      ll.schwelle_belegbar_erreicht_alter("Keilriemen prüfen: alle 150.000 km Danach alle 20.000 km", 82000, 9) is False)
from app.kaufaktionen import _kompakt_wartung  # noqa: E402
kz = _kompakt_wartung("Zahnriemen", "ersetzen", HUELLE + MARKT + BED, "Falls Zahnriemen verbaut: ", technische_bedingung="nur Hybridmodelle")
check("F3-20 kompakte Restzeile: Präsenz, Tätigkeit, Intervall, ungeklärte Bedingung, Marktvorbehalt GETRENNT benannt; keine Erreicht-/Fälligkeitsaussage",
      kz.startswith("Falls Zahnriemen verbaut: „Zahnriemen“ (Wechsel; laut Quelle: wechseln: alle 150.000 km oder 6 Jahre")
      and "Anwendbarkeit ungeklärt: nur Hybridmodelle" in kz and "Marktvorbehalt der Quelle, Geltung in Deutschland nicht bestätigt" in kz
      and "erreicht" not in kz and "fällig" not in kz.lower() and "übereinstimmend" not in kz, kz)

print("\n== F1 Bereichsende / allgemeine Abschnitte ==")
o, ab = f1("Für UX 300e Zwischenservice alle 15.000 km. Für alle Modelle Bremsflüssigkeit alle 2 Jahre.")
check("F1-16 ausdrücklich allgemeiner Abschnitt ('Für alle Modelle') beendet den fremden Bereich und bleibt erhalten",
      "Zwischenservice" not in o and "Bremsflüssigkeit alle 2 Jahre" in o, o)
o, ab = f1("Für UX 300e Zwischenservice alle 15.000 km. Allgemeine Hinweise: Motoröl alle 10.000 km.")
check("F1-17 'Allgemeine Hinweise' beendet den fremden Bereich", "Zwischenservice" not in o and "Motoröl alle 10.000 km" in o, o)
o, ab = f1("Vorspann Zahnriemen alle 90.000 km. Für UX 300e Zwischenservice alle 15.000 km. Für die Wartung gilt ein Intervall.")
check("F1-18 beliebiges 'Für ...' im Fließtext ist keine Grenze (kein Erfinden von Bereichsenden): Vorspann bleibt, fremder Bereich entfällt",
      "Vorspann Zahnriemen" in o and "Zwischenservice" not in o)
o, ab = f1("Für ES 300h und UX 300e gilt Zwischenservice alle 15.000 km. Für UX 300e Zusatzprüfung Bremsen alle 30.000 km.")
check("F1-19 gemeinsam (RC F nicht genannt, aber 'ES 300h' fremd) -> beide Bereiche fremd; ohne Zielnennung nichts erhalten",
      "Zwischenservice" not in o and "Bremsen" not in o)
o, ab = f1("Für RC F und UX 300e Zwischenservice alle 15.000 km. Für UX 300e Zusatzprüfung Bremsen alle 30.000 km. "
           "Für RC F Ventilspiel alle 90.000 km.")
check("F1-20 passend+gemeinsam bleibt, rein fremder Bereich dazwischen entfällt, späterer passender bleibt",
      "Zwischenservice" in o and "Zusatzprüfung" not in o and "Ventilspiel" in o, o)

print("\n== F5 Steuerzeichen in der Extraktion ==")
t5 = [{"url": "https://www.toyota.de/service/wartung-x.pdf", "title": "Toyota Yaris Wartungsplan Serviceheft",
       "content": "Bremsflüssigkeit\x07wechseln: alle 30.000 km oder 2 Jahre\x00\nZündkerzen wechseln: alle 60.000 km"}]
f5 = tr._extrahiere_fakten(t5, "wartung", marke="Toyota", modell="Yaris", baujahr=2017, abgelehnt=[], kilometerstand=82000)
check("F5-9 Faktenextraktion: kein Steuerzeichen in Aussage/Bauteil, Wortgrenze erhalten ('Bremsflüssigkeit wechseln')",
      bool(f5) and all(ord(c) >= 32 or c in "\n\t" for f in f5 for c in (f.aussage or "") + (f.bauteil or ""))
      and any("Bremsflüssigkeit wechseln" in (f.aussage or "") for f in f5), str([(f.bauteil, f.aussage) for f in f5]))

print("\n== Fall ohne Wartungsträger: sechs Rückrufe belegen alle Einzelplätze ==")
RN6 = [dict(R12358, id=90020 + k, kba_referenz=f"9903{k}", canonical_make="TOYOTA", canonical_nameplate="YARIS",
            mangel=f"[SYNTH] Rückruf {k}: Bauteil {k} kann ausfallen.", eingrenzung_amtlich="YARIS") for k in range(1, 7)]
ins6, lk6, ka6 = kette(REQ4, fk4[:2], RN6)
wart6 = [i for i in ins6 if i.kategorie == "web_wartung"]
for bereich in ("verkaeuferfragen", "dokumente"):
    pl6 = getattr(ka6, bereich)
    sp6 = pl6.fahrzeugspezifisch
    check(f"G0 {bereich}: sechs Rückrufe belegen alle Einzelplätze (Limit unverändert, keine Wartungsaktion darunter)",
          len(sp6) == MAX_SPEZIFISCH_PRO_BEREICH and not [a for a in sp6 if a.kategorie in ("wartung", "web_wartung")])
    sammel = [a for a in pl6.basis if a.id.endswith("basis-wartungsrest")]
    check(f"G1 {bereich}: genau EIN Sammelpunkt im Basiskatalog, beide ausgewählten Wartungspunkte konkret darin",
          len(sammel) == 1 and all(f"„{f.bauteil}“" in sammel[0].aktion for f in fk4[:2]),
          str([a.aktion for a in sammel]))
    check(f"G2 {bereich}: Sammelpunkt nennt je Bauteil Tätigkeit und Intervall ('laut Quelle'), ohne Fälligkeit/Versäumnis",
          len(sammel) == 1 and sammel[0].aktion.count("laut Quelle:") == 2
          and not any(w in sammel[0].aktion.lower() for w in ("überfällig", "versäumt", "fällig")), str(sammel[:1]))
    check(f"G3 {bereich}: Rückrufaktionen bleiben unvermischt (kein Wartungstext in ihnen), keine zusätzliche Einzelaktion",
          all("Wartungspunkte" not in a.aktion and "laut Quelle" not in a.aktion for a in sp6)
          and len({a.id for a in sp6}) == MAX_SPEZIFISCH_PRO_BEREICH)
    check(f"G4 {bereich}: Basisvertrag gewahrt (Basis ohne Evidence-IDs, typ basis); Evidenz bleibt in den Insights",
          all(a.evidence_ids == [] and a.typ == "basis" for a in pl6.basis) and len(wart6) == 2)
    anker = "frage-basis-wartung" if bereich == "verkaeuferfragen" else "dokument-basis-wartungsrechnungen"
    ids6 = [a.id for a in pl6.basis]
    check(f"G5 {bereich}: Sammelpunkt steht direkt hinter dem Wartungs-/Nachweis-Anker des Katalogs",
          len(sammel) == 1 and ids6.index(sammel[0].id) == ids6.index(anker) + 1, str(ids6))
# Mit Wartungsträger (Hauptfall) entsteht KEIN Basis-Sammelpunkt: keine Doppelung.
check("G6 mit Wartungsträger (Hauptfall): kein Basis-Sammelpunkt, der Restpunkt steht im Träger",
      not [a for b in ("verkaeuferfragen", "dokumente") for a in getattr(ka, b).basis if a.id.endswith("wartungsrest")])
offen: list[str] = []

print()
print("ALLE GRÜN" if not fehler else f"{len(fehler)} FEHLER: {fehler}")
if offen:
    print(f"OFFENE GRENZE ({len(offen)}): {offen}")
sys.exit(1 if fehler else (2 if offen else 0))
