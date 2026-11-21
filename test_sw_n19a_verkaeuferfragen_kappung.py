"""
SW-N19-A -- eine durch das Verkäuferfragen-Limit (MAX_SPEZIFISCH_PRO_BEREICH) verdrängte Frage zu einer Schwachstelle,
Web-Schwachstelle oder einem Motorproblem bleibt als konkrete Frage sichtbar (Basisliste, mit ihren Belegen).

Nachstellung des zugeordneten Laufs (Request WF8aSO62QOik2U8363j4Ww, 21.11.2026): DB-no_match, 2 Rückrufe, 5 Wartungen,
1 begrenzter Rostbericht (echter PistonHeads-Satz). Die Rückrufe und Wartungen sind SYNTHETISCHE Insights (nur ihre Zahl
und ihr Rang zählen); das ist eine Offline-Reproduktion, kein gespeicherter historischer Response.

    python scripts/test_isolated.py test_sw_n19a_verkaeuferfragen_kappung.py
"""
import os
import socket
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="swn19a_kappung_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


socket.socket.connect = lambda *a, **k: _gesperrt()
socket.create_connection = _gesperrt
print("Netzsperre:", True, "| Temp-DB:", os.environ["AUTO_KI_DB_PATH"])
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)

from app import database as _db  # noqa: E402
_db.ensure_tables()
import app.kaufaktionen as kfa  # noqa: E402
import app.technical_research as tr  # noqa: E402
from app.evidence import build_insights  # noqa: E402
from app.kaufaktionen import build_kaufaktionen, MAX_SPEZIFISCH_PRO_BEREICH  # noqa: E402
from app.kaufcheck_bericht import bericht  # noqa: E402
from app.models import EvidenceQuelle, Insight, KaufCheckRequest, TechnischeRecherche  # noqa: E402
from app.vehicle_identity import VehicleIdentity  # noqa: E402

FEHLER: list[str] = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        FEHLER.append(name)


REAL = "https://www.pistonheads.com/news/ph-buying-guides/lexus-rc-f-ph-used-buying-guide/47506"
S_SEALER = ("Some RC Fs had poorly-applied rear wheelarch sealer which could lead to water ingress and eventual rust, "
            "especially in high salt-use regions (step forward UK).")
ZIEL = dict(marke="Lexus", modell="RC F", baujahr=2017, generation="UXC1", hubraum="5.0", fuel_ziel="benzin",
            powertrains_ziel=frozenset({"ICE"}), motor="5.0 V8")
REQ = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000, kraftstoff="Benzin",
                       motor="5.0 V8, Heckantrieb", ausstattung=[], vorbesitzer=2, scheckheftgepflegt=True)
f_rost = tr._extrahiere_fakten(
    [{"url": REAL, "title": "Lexus RC F | PH Used Buying Guide | PistonHeads UK", "content": "BODYWORK\n\n" + S_SEALER}],
    "schwachstelle", abgelehnt=[], **ZIEL)


def _rueckruf(n):
    return Insight(id=f"RUECK-{n}", kategorie="rueckruf", kurztitel=f"Rückrufmangel {n}", titel=f"Rückruf: Mangel {n}",
                   beschreibung=f"Amtlicher Rückruf {n}.", confidence="hoch", applicability="variant_match",
                   trust="verified", quellen=[EvidenceQuelle(typ="rueckruf_kba", ref=f"0000{n}", label="KBA")])


WARTUNGEN = ("Motoröl", "Zahnriemen", "Ventilspiel", "Bremsflüssigkeit", "Getriebeöl")


def _wartung(n, bauteil):
    return Insight(id=f"WART-{n}", kategorie="wartung", titel=f"Wartung: {bauteil}", bauteil=bauteil,
                   beschreibung=f"{bauteil} laut Hersteller alle 30.000 km.", confidence="mittel",
                   quellen=[EvidenceQuelle(typ="motorvarianten", ref=bauteil, label="DB")])


def insights(rueckrufe=2, wartungen=5, rost=True):
    ins = build_insights(None, None, [], REQ, check_typ="kauf", canonical_rueckrufe=[],
                         web_recherche=TechnischeRecherche(ausgeloest_durch="db_miss", fakten=f_rost if rost else []))
    return ins + [_rueckruf(n) for n in range(1, rueckrufe + 1)] + [_wartung(n, b) for n, b in enumerate(WARTUNGEN[:wartungen], 1)]


def fragen(ka):
    l = ka.verkaeuferfragen
    return [*l.fahrzeugspezifisch, *l.basis]


def rost_frage(ka):
    return [a for a in fragen(ka) if "„Rost“" in a.titel]


print("== A: Nachstellung des Verlusts (Limit ist Ursache) ==")
ins = insights()
rost_ins = [i for i in ins if i.kategorie == "web_schwachstelle"]
check("A1 der begrenzte Rostbericht ist als EIN Insight vorhanden", len(rost_ins) == 1 and rost_ins[0].beleglage == "begrenzter_bericht"
      and "Rost" in (rost_ins[0].bauteil or ""), str([(i.bauteil, i.beleglage) for i in rost_ins]))
orig = kfa.MAX_SPEZIFISCH_PRO_BEREICH
kfa.MAX_SPEZIFISCH_PRO_BEREICH = 99
try:
    ka_offen = build_kaufaktionen(REQ, None, None, ins)
finally:
    kfa.MAX_SPEZIFISCH_PRO_BEREICH = orig
sp_offen = ka_offen.verkaeuferfragen.fahrzeugspezifisch
check("A2 VOR der Begrenzung existiert die Rostfrage (Limit aufgehoben), sie steht hinter den Rückrufen und Wartungen",
      len([a for a in sp_offen if "„Rost“" in a.titel]) == 1 and len(sp_offen) > MAX_SPEZIFISCH_PRO_BEREICH
      and [a.kategorie for a in sp_offen[:7]].count("web_schwachstelle") == 0, str([(a.kategorie, a.rang) for a in sp_offen]))
ka = build_kaufaktionen(REQ, None, None, ins)
check("A3 Limit 6 unverändert, fahrzeugspezifisch nie mehr als 6", MAX_SPEZIFISCH_PRO_BEREICH == 6
      and len(ka.verkaeuferfragen.fahrzeugspezifisch) <= 6)
check("A4 die Besichtigung trägt den Rost-Hinweis (Wartungen erzeugen keine Besichtigung): genau der Befund aus dem Lauf",
      any("Rost" in a.titel for a in ka.besichtigung.fahrzeugspezifisch))
sp = ka.verkaeuferfragen.fahrzeugspezifisch
check("A5 Rückrufe bleiben unter den Einzelaktionen erhalten", sum(1 for a in sp if a.kategorie == "rueckruf") == 2,
      str([a.kategorie for a in sp]))

print("\n== B: Fix -- die verdrängte Rostfrage bleibt konkret sichtbar ==")
rf = rost_frage(ka)
check("B1 die Rostfrage steht in der Ausgabe (Überlauffall)", len(rf) == 1, str([a.titel for a in fragen(ka)]))
r = rf[0] if rf else None
check("B2 sie steht in der Basisliste, NICHT als siebte Einzelaktion", r is not None and r in ka.verkaeuferfragen.basis
      and r not in sp)
check("B3 Fragetext, Rang-Band, evidence_id und Kategorie bleiben erhalten",
      r is not None and r.titel == "Wurde am Bauteil „Rost“ bereits gearbeitet oder etwas ersetzt?"
      and r.evidence_ids == [rost_ins[0].id] and r.kategorie == "web_schwachstelle",
      str((r.titel, r.evidence_ids, r.kategorie)) if r else "fehlt")
check("B4 begrenzter Bericht bleibt begrenzt: 'keine belegte Häufigkeit', 'nicht belegt', kein bekannter Schwachpunkt",
      r is not None and "Eine Fachquelle berichtet von einzelnen Fällen" in r.aktion and "keine belegte Häufigkeit" in r.aktion
      and "ob dieses Fahrzeug dazu gehört, ist nicht belegt" in r.aktion and "Schwachpunkt" not in r.aktion
      and "als Problem" not in r.aktion, r.aktion if r else "fehlt")
check("B5 keine Dokumentanforderung erfunden (Dokumente unverändert ohne web_schwachstelle)",
      not [a for l in (ka.dokumente.fahrzeugspezifisch, ka.dokumente.basis) for a in l if a.kategorie == "web_schwachstelle"])
check("B6 ein Basis-Prinzip bleibt: das Bauteil steht in Anführungszeichen, die Frage nennt keine bestätigte Betroffenheit",
      r is not None and "betroffen" not in r.aktion.lower().replace("nicht belegt", ""))
wr = [a for a in fragen(ka) if a.kategorie == "wartung"]
check("B7 Wartung: Einzelaktionen und Wartungsrest-Sammelpunkt bleiben wie ohne den Fix (gekappte Wartung im Sammelpunkt)",
      (any("Weitere ausgewählte Wartungspunkte" in a.aktion for a in sp if a.kategorie == "wartung")
       or any(a.id.endswith("wartungsrest") for a in ka.verkaeuferfragen.basis)) and len(wr) >= 3)
ids = [a.id for a in fragen(ka)]
check("B8 keine doppelten Aktions-IDs in der Verkäuferfragenliste", len(ids) == len(set(ids)), str(ids))
check("B9 sw_diagnose zählt die Rostfrage jetzt in den Verkäuferfragen (Basis und spezifisch)",
      sum(1 for a in fragen(ka) if a.kategorie == "web_schwachstelle") == 1)

print("\n== C: Ohne Kappung keine Änderung/Doppelung ==")
ka_c = build_kaufaktionen(REQ, None, None, insights(rueckrufe=0, wartungen=0))
rc = rost_frage(ka_c)
check("C1 ohne Überlauf steht die Rostfrage GENAU EINMAL, als Einzelaktion, nicht zusätzlich in der Basisliste",
      len(rc) == 1 and rc[0] in ka_c.verkaeuferfragen.fahrzeugspezifisch
      and not [a for a in ka_c.verkaeuferfragen.basis if a.kategorie == "web_schwachstelle"])
ka_c2 = build_kaufaktionen(REQ, None, None, insights(rueckrufe=1, wartungen=3))
check("C2 knapp unter dem Limit ebenfalls: keine Basis-Kopie", not [a for a in ka_c2.verkaeuferfragen.basis if a.kategorie == "web_schwachstelle"]
      and len(rost_frage(ka_c2)) == 1)
ka_c3 = build_kaufaktionen(REQ, None, None, insights(rost=False))
check("C3 Überlauf ohne Rostbericht: kein Problemeintrag in der Basisliste (nur Wartungsrest/Basis wie bisher)",
      not [a for a in ka_c3.verkaeuferfragen.basis if a.kategorie in ("schwachstelle", "web_schwachstelle", "motorproblem")])
ka_d = build_kaufaktionen(REQ, None, None, ins)
check("C4 deterministisch: zweiter Lauf liefert dieselben Verkäuferfragen",
      [(a.id, a.titel, a.aktion) for a in fragen(ka_d)] == [(a.id, a.titel, a.aktion) for a in fragen(ka)])

print("\n== D: Mehrere verdrängte Problemfragen, Duplikate, Belege ==")


def _problem(n, bauteil, kat="schwachstelle", sev="mittel"):
    return Insight(id=f"PROB-{n}", kategorie=kat, titel=f"{bauteil}: Befund", bauteil=bauteil, beschreibung=f"{bauteil} auffällig.",
                   confidence="mittel", schweregrad=sev, trust="unverified_db", fakt_ref="schwachstelle_motor#7" if kat == "motorproblem" else None,
                   quellen=[EvidenceQuelle(typ="schwachstellen_baureihe", ref=bauteil, label="DB")])


mehr = insights(rueckrufe=6, wartungen=5) + [_problem(1, "Wasserpumpe"), _problem(2, "Wasserpumpe"), _problem(3, "Kraftstoffpumpe", "motorproblem")]
MOTOR = {"variante_id": "t", "schwachstellen_motor": [{"id": 7, "bauteil": "Kraftstoffpumpe", "beschreibung": "Kraftstoffpumpe auffällig."}]}
ka_m = build_kaufaktionen(REQ, None, MOTOR, mehr)
basis_problem = [a for a in ka_m.verkaeuferfragen.basis if a.kategorie in ("schwachstelle", "web_schwachstelle", "motorproblem")]
titel_p = [a.titel for a in basis_problem]
check("D1 mehrere verdrängte Problemfragen bleiben einzeln unterscheidbar (jede mit eigenem Bauteil und eigener ID)",
      len(basis_problem) == len(set(a.id for a in basis_problem)) >= 1 and len(set(titel_p)) == len(titel_p), str(titel_p))
alle_titel = [a.titel for a in fragen(ka_m)]
check("D2 jede erzeugte Problemfrage steht genau einmal in der Gesamtliste (nichts geht verloren, nichts doppelt)",
      all(sum(1 for t in alle_titel if f"„{b}“" in t) == 1 for b in ("Rost", "Wasserpumpe", "Kraftstoffpumpe")), str(alle_titel))
wp = [a for a in fragen(ka_m) if "„Wasserpumpe“" in a.titel]
check("D3 echte Duplikate (zwei Insights, ein Bauteil) behalten BEIDE Belege, wo auch immer die Frage steht",
      len(wp) == 1 and set(wp[0].evidence_ids) == {"PROB-1", "PROB-2"}, str([(a.id, a.evidence_ids) for a in wp]))
check("D4 Rückrufe bleiben erhalten (alle sechs Rückruffragen als Einzelaktionen)",
      sum(1 for a in fragen(ka_m) if a.kategorie == "rueckruf") == 6)
check("D7 Wartungsrest ohne Träger bleibt: die verdrängten Wartungen stehen als Sammelpunkt in der Basisliste",
      any(a.id.endswith("wartungsrest") for a in ka_m.verkaeuferfragen.basis))
check("D6 Herkunftsarten bleiben erhalten: Rost (Web-Schwachstelle) und Kraftstoffpumpe (Motorproblem) stehen verdrängt in der Basisliste",
      {a.kategorie for a in basis_problem} >= {"web_schwachstelle", "motorproblem"}, str([(a.kategorie, a.titel) for a in basis_problem]))
check("D5 Limit unverändert: höchstens 6 Einzelaktionen",
      len(ka_m.verkaeuferfragen.fahrzeugspezifisch) <= 6)

print("\n== E: Ausgabe -- Response und Vollbericht ==")
ka_dump = ka.model_dump()
resp_basis = ka_dump["verkaeuferfragen"]["basis"]
check("E1 Response: die Rostfrage steht serialisiert in verkaeuferfragen.basis mit evidence_ids", any(
    "„Rost“" in a["titel"] and a["evidence_ids"] == [rost_ins[0].id] for a in resp_basis))
text = bericht(REQ, VehicleIdentity.from_check_context(None, None, REQ), None, None, ins, ka, [], "kaufen_nach_besichtigung",
               type("PA", (), {"label": None})(), False, None, None, ["Inserat"])
check("E2 Vollbericht: die Rostfrage steht in der Fragenliste", text.count("Wurde am Bauteil „Rost“ bereits gearbeitet") >= 1)
check("E3 Vollbericht: die Frage trägt weiterhin den begrenzten Status und den Vorbehalt",
      "keine belegte Häufigkeit" in text and "ob dieses Fahrzeug dazu gehört, ist nicht belegt" in text)

print("\n" + ("ALLE GRÜN" if not FEHLER else f"{len(FEHLER)} FEHLER: {FEHLER}"))
sys.exit(1 if FEHLER else 0)
