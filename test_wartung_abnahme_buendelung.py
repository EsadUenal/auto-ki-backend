"""
Abnahme Wartungspaket (010671c ff.): Bündelung gekappter Wartungspunkte bis zur sichtbaren Ausgabe.

  A  Sichtbare Ausgabe ohne Wartungsträger (sechs Rückrufe belegen alle Einzelplätze): Prüfplan UND Bericht.
  B  Nachweisweg je gebündeltem Punkt -- unterschiedliche Quellen, Tätigkeiten, Bedingungen, Marktvorbehalte in
     EINER Bündelung; nichts wird vom Träger auf den Restpunkt übertragen.
       B1 mit Wartungsträger: "Warum?" des Trägers (Frontend: insightsByIds(insights, aktion.evidence_ids))
       B2 ohne Wartungsträger: Basis-Sammelpunkt ohne Evidence-IDs; Nachweis über den Berichtsabschnitt
          "Relevante Risiken und Hinweise" (jedes web_wartung-Insight mit Titel, Beschreibung, Quellen-Zeile).
  C  F2 ordnet NUR Wartungen untereinander; Nicht-Wartungsaktionen gleichen Rangs behalten ihren Platz.

Netzsperre (extern; Loopback bleibt offen) + Temp-DB VOR dem App-Import. [SYNTH] = synthetische, neutrale Fakten
(Toyota Yaris), keine Fahrzeugsonderlogik.

    python test_wartung_abnahme_buendelung.py
"""
import json
import os
import socket
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="wartung_abnahme_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_TMP, "chroma")
_orig_connect = socket.socket.connect


def _lokal(addr):
    return isinstance(addr, tuple) and str(addr[0]) in ("127.0.0.1", "::1", "localhost")


def _connect(self, addr, *a, **k):
    if _lokal(addr):
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
from app.kaufaktionen import MAX_SPEZIFISCH_PRO_BEREICH, _Sammler, build_kaufaktionen  # noqa: E402
from app.kaufcheck_bericht import bericht  # noqa: E402
from app.laufleistung import build_laufleistungskontext  # noqa: E402
from app.models import EvidenceQuelle, KaufCheckRequest, TechnischeRecherche, WebFakt  # noqa: E402
from app.schreibstil import bereinige_nutzertexte  # noqa: E402
from app.vehicle_identity import VehicleIdentity  # noqa: E402

_orig_alter = ll.fahrzeugalter
ll.fahrzeugalter = lambda bj, heute_jahr=None: _orig_alter(bj, heute_jahr=2026)

fehler: list[str] = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        fehler.append(name)


# ── [SYNTH] sieben Wartungsfakten, je eigene Quelle; Varianten in den letzten drei (gekappten) Punkten ────────────
MARKT = 'Die Quelle schränkt sich selbst ein ("falls für Ihren Markt zutreffend")'


def fakt(bauteil, aussage, domain, taetigkeit, *, markt=False, bedingung=None):
    return WebFakt(kategorie="wartung", bauteil=bauteil, aussage=aussage, confidence="mittel",
                   quellen=[EvidenceQuelle(typ="web", url=f"https://www.{domain}/wartung.pdf", titel=f"Wartungsplan {domain}",
                                           qualitaet="Hersteller")],
                   taetigkeit=taetigkeit,
                   regionale_geltung="unresolved" if markt else None, herkunftsregion=MARKT if markt else None,
                   technische_bedingung=bedingung, technische_bedingung_status="unresolved" if bedingung else None)


# Produktionsnah: die Wartungsauswahl liefert höchstens FÜNF Punkte (technical_research), deshalb fünf Fakten.
FAKTEN = [
    fakt("Motoröl", "Motoröl wechseln: alle 15.000 km oder 1 Jahr", "quelle-a.de", "ersetzen"),
    fakt("Luftfilter", "Luftfilter wechseln: alle 30.000 km oder 2 Jahre", "quelle-b.de", "ersetzen"),
    # bewusst verschieden: Marktvorbehalt / ungeklärte technische Bedingung + Prüfung / Präsenz (Getriebe unbekannt)
    fakt("Bremsflüssigkeit", "Bremsflüssigkeit wechseln: alle 30.000 km oder 2 Jahre", "quelle-e.de", "ersetzen", markt=True),
    fakt("Kühlmittel", "Kühlmittel prüfen: alle 30.000 km oder 2 Jahre", "quelle-f.de", "pruefen",
         bedingung="nur Fahrzeuge mit Zusatzheizung"),
    fakt("Automatikgetriebeöl", "Automatikgetriebeöl prüfen: alle 60.000 km oder 4 Jahre", "quelle-g.de", "pruefen"),
]
REQ = KaufCheckRequest(marke="Toyota", modell="Yaris", baujahr=2017, kilometerstand=82000, kraftstoff="Benzin",
                       ausstattung=[], vorbesitzer=2, scheckheftgepflegt=True)        # Getriebe UNBEKANNT


def rueckruf(k):
    return {"id": 91000 + k, "baureihe_id": None, "datum": "2023-01-19", "betroffene_baujahre": "2014-2019",
            "mangel": f"[SYNTH] Rückruf {k}: Bauteil {k} kann ausfallen.", "abhilfe": f"Bauteil {k} wird ersetzt.",
            "kba_referenz": f"9904{k}", "canonical_make": "TOYOTA", "canonical_nameplate": "YARIS", "_trust": "verified",
            "eingrenzung_amtlich": "YARIS", "prod_von_amtlich": 2014, "prod_bis_amtlich": 2019}


def kette(rueckrufe):
    ins = build_insights(None, None, [], REQ, check_typ="kauf",
                         web_recherche=TechnischeRecherche(ausgeloest_durch="db_miss", fakten=FAKTEN),
                         canonical_rueckrufe=rueckrufe)
    lk = build_laufleistungskontext(REQ, ins, heute_jahr=2026)
    ka = build_kaufaktionen(REQ, None, None, ins, laufleistungskontext=lk)
    text = bericht(REQ, VehicleIdentity.from_check_context(None, None, REQ), None, None, ins, ka, [],
                   "kaufen_nach_besichtigung", type("PA", (), {"label": None})(), False, lk, None, ["Inserat"])
    # Wie app/kaufcheck.py: Schreibstil-Bereinigung über ALLE Nutzertexte, dann JSON (API-Antwort).
    antwort = bereinige_nutzertexte({"insights": [i.model_dump() for i in ins], "kaufaktionen": ka.model_dump(),
                                     "laufleistungskontext": lk.model_dump() if lk else None, "bericht": text})
    return ins, lk, ka, antwort


def insight_zu(ins, bauteil):
    """Das Wartungs-Insight zu einem Bauteil (Titel ggf. "Falls ... verbaut: ...")."""
    return next(i for i in ins if i.kategorie == "web_wartung" and getattr(i, "bauteil", None) == bauteil)


def zeile_zu(text, bauteil):
    """Die Restzeile eines Bauteils innerhalb der Bündelung (ab "„Bauteil“" bis zur schließenden Klammer)."""
    start = text.find(f"„{bauteil}“")
    if start < 0:
        return ""
    vor = text.rfind("; ", 0, start)
    vor = text.rfind(": ", 0, start) if vor < 0 or vor < text.find("Weitere ausgewählte") else vor
    ende = text.find(")", start)
    return text[(vor + 2 if vor >= 0 else start):ende + 1]


def punkt_pruefen(tag, zeile, ins_b):
    """B: Tätigkeit, Bedingung, Marktvorbehalt der Zeile kommen aus IHREM Insight -- nichts vom Träger/Nachbarn."""
    t = getattr(ins_b, "taetigkeit", None)
    erwartet_t = {"ersetzen": "(Wechsel;", "pruefen": "(Prüfung;"}[t]
    hat_markt = "Marktvorbehalt der Quelle" in zeile
    hat_bed = "Anwendbarkeit ungeklärt, zuerst klären" in zeile
    soll_markt = "Ob das für Ihr Fahrzeug in Deutschland" in ins_b.beschreibung
    soll_bed = getattr(ins_b, "technische_bedingung_status", None) == "unresolved"
    praesenz = ins_b.titel.split(": ", 1)[0] + ": " if ins_b.titel.startswith("Falls ") else ""
    check(f"{tag} Tätigkeit aus dem eigenen Insight ({t})", erwartet_t in zeile, zeile)
    check(f"{tag} Marktvorbehalt genau dann, wenn das eigene Insight ihn trägt ({soll_markt})", hat_markt == soll_markt, zeile)
    check(f"{tag} technische Bedingung genau dann, wenn das eigene Insight sie trägt ({soll_bed})", hat_bed == soll_bed
          and (not soll_bed or ins_b.technische_bedingung in zeile), zeile)
    check(f"{tag} Präsenz-Bedingung des eigenen Titels erhalten ('{praesenz.strip()}')",
          (praesenz in zeile) if praesenz else not zeile.startswith("Falls "), zeile)
    check(f"{tag} keine Erreicht-/Fälligkeitsaussage in der Zeile",
          not any(w in zeile.lower() for w in ("erreicht", "fällig", "versäumt")), zeile)


# ══ B1: zwei Rückrufe + sieben Wartungen -> Träger vorhanden ═════════════════════════════════════════════════════
print("== B1 mit Wartungsträger: 4 Rückrufe + 5 Wartungen (2 einzeln, 3 gebündelt) ==")
ins1, lk1, ka1, antwort1 = kette([rueckruf(k) for k in range(1, 5)])
for bereich in ("verkaeuferfragen", "dokumente"):
    pl = antwort1["kaufaktionen"][bereich]
    spez = pl["fahrzeugspezifisch"]
    check(f"B1 {bereich}: {MAX_SPEZIFISCH_PRO_BEREICH} Einzelaktionen, davon 4 Rückrufe", len(spez) == MAX_SPEZIFISCH_PRO_BEREICH
          and sum(1 for a in spez if a["kategorie"] == "rueckruf") == 4, str([a["id"] for a in spez]))
    # Welche Wartungen einzeln stehen, folgt aus der Ausgabe (Insight der ersten Evidenz-ID jeder Wartungsaktion).
    nach_id = {i["id"]: i for i in antwort1["insights"]}
    einzeln = [nach_id[a["evidence_ids"][0]]["bauteil"] for a in spez if a["kategorie"] in ("wartung", "web_wartung")]
    GEKAPPT = [f.bauteil for f in FAKTEN if f.bauteil not in einzeln]
    print(f"  {bereich}: einzeln {einzeln} | gebündelt {GEKAPPT}")
    check(f"B1 {bereich}: drei verschiedene Punkte gebündelt (Marktvorbehalt, Bedingung, Präsenz)",
          GEKAPPT == ["Bremsflüssigkeit", "Kühlmittel", "Automatikgetriebeöl"], str(GEKAPPT))
    traeger = [a for a in spez if a["kategorie"] in ("wartung", "web_wartung") and "Weitere ausgewählte" in a["aktion"]]
    check(f"B1 {bereich}: genau EIN Träger, kein Basis-Sammelpunkt", len(traeger) == 1
          and not [a for a in pl["basis"] if a["id"].endswith("wartungsrest")])
    if not traeger:
        continue
    t = traeger[0]
    print(f"  TRÄGER {bereich}: {t['titel']}\n    ...{t['aktion'][t['aktion'].find('Weitere ausgewählte'):]}")
    # "Warum?" exakt wie das Frontend: insightsByIds(insights, aktion.evidence_ids) -- Reihenfolge der Insights.
    warum = [i for i in antwort1["insights"] if i["id"] in set(t["evidence_ids"])]
    for b in GEKAPPT:
        ib = insight_zu(ins1, b)
        zeile = zeile_zu(t["aktion"], b)
        check(f"B1 {bereich} {b}: eigene Zeile im Trägertext", bool(zeile), t["aktion"][-500:])
        punkt_pruefen(f"B1 {bereich} {b}:", zeile, ib)
        karten = [k for k in warum if (k.get("bauteil") == b)]
        check(f"B1 {bereich} {b}: 'Warum?' des Trägers zeigt GENAU EINE Karte dieses Bauteils, mit SEINER Quelle",
              len(karten) == 1 and [q["url"] for q in karten[0]["quellen"]] == [q.url for q in ib.quellen]
              and karten[0]["titel"] == ib.titel, str([(k.get("bauteil"), [q["url"] for q in k["quellen"]]) for k in warum]))
    eigen = [i for i in warum if i.get("bauteil") not in GEKAPPT]
    check(f"B1 {bereich}: 'Warum?' enthält sonst nur das Insight des Trägers selbst",
          len(eigen) == 1 and eigen[0]["bauteil"].lower() in t["titel"].lower(), str([k.get("bauteil") for k in eigen]))
    quellen = [q["url"] for k in warum for q in k["quellen"]]
    check(f"B1 {bereich}: jede Quelle im 'Warum?' genau einmal (keine doppelte/fremde Zuordnung)",
          len(quellen) == len(set(quellen)) == len(warum))

# ══ A/B2: sechs Rückrufe + sieben Wartungen -> kein Träger ═══════════════════════════════════════════════════════
print("\n== A/B2 ohne Wartungsträger: 6 Rückrufe + 5 Wartungen (alle gebündelt) ==")
ins2, lk2, ka2, antwort2 = kette([rueckruf(k) for k in range(1, 7)])
alle = [f.bauteil for f in FAKTEN]
bericht2 = antwort2["bericht"]
for bereich, abschnitt in (("verkaeuferfragen", "## Verkäuferfragen"), ("dokumente", "## Dokumente und finale Checkliste")):
    pl = antwort2["kaufaktionen"][bereich]
    spez, basis = pl["fahrzeugspezifisch"], pl["basis"]
    check(f"A {bereich}: sechs Rückrufe, keine Wartungs-Einzelaktion, Limit unverändert",
          len(spez) == MAX_SPEZIFISCH_PRO_BEREICH and all(a["kategorie"] == "rueckruf" for a in spez))
    sammel = [a for a in basis if a["id"].endswith("basis-wartungsrest")]
    check(f"A {bereich}: genau EIN Basis-Sammelpunkt mit allen fünf Wartungspunkten", len(sammel) == 1
          and all(f"„{b}“" in sammel[0]["aktion"] for b in alle), str([a["id"] for a in basis]))
    if not sammel:
        continue
    s = sammel[0]
    print(f"  SAMMELPUNKT {bereich}: {s['titel']}\n    {s['aktion']}")
    check(f"A {bereich}: Sammelpunkt ohne Evidence-IDs (Basisvertrag) -> im Frontend kein 'Warum?'", s["evidence_ids"] == [])
    teil = bericht2.split(abschnitt, 1)[1].split("\n## ", 1)[0] if abschnitt in bericht2 else ""
    check(f"A {bereich}: Bericht, Abschnitt '{abschnitt[3:]}', enthält den Sammelpunkt mit Titel und allen Zeilen",
          f"**{s['titel']}** {s['aktion']}" in teil, teil[:300])
    for b in alle:
        punkt_pruefen(f"B2 {bereich} {b}:", zeile_zu(s["aktion"], b), insight_zu(ins2, b))
risiken = bericht2.split("## Relevante Risiken und Hinweise", 1)[1].split("\n## ", 1)[0]
for b in alle:
    ib = insight_zu(ins2, b)
    block = risiken.split(f"### {ib.titel}", 1)[1].split("\n### ", 1)[0] if f"### {ib.titel}" in risiken else ""
    domain = ib.quellen[0].url.split("//www.", 1)[1].split("/", 1)[0]
    check(f"B2 Bericht {b}: eigener Abschnitt '### {ib.titel[:40]}' mit Beschreibung und SEINER Quelle ({domain})",
          bool(block) and ib.beschreibung.split(" Ob ")[0][:40] in block and "Quellen:" in block and domain in block
          and all(f.quellen[0].url.split("//www.", 1)[1].split("/", 1)[0] not in block for f in FAKTEN if f.bauteil != b),
          block[:300])
lk_bauteile = {w["bauteil"] for w in (antwort2["laufleistungskontext"] or {}).get("wartungshinweise", [])}
print("  Laufleistungskarte (Bauteil + Hinweis + Intervall, ohne Quellenlink):", sorted(lk_bauteile))

# ══ C: F2 im neutralen Mischfall ═══════════════════════════════════════════════════════════════════════════════
print("\n== C F2 Mischfall: gleicher Rang 750, Wartungen und Nicht-Wartungen ==")
s = _Sammler()
for k in range(1, 6):
    s.add("verkaeuferfragen", f"a{k}-schwachstelle", f"[SYNTH] Schwachstelle {k}", "Text", 750,
          evidence_ids=[f"schwachstelle-{k}"], kategorie="schwachstelle")
s.add("verkaeuferfragen", "wartung-web-a-luftfilter", "Luftfilter", "Text", 750, evidence_ids=["web-wartung-2"],
      kategorie="web_wartung", ordnung=1, kompakt="„Luftfilter“ (Wechsel)")
s.add("verkaeuferfragen", "wartung-web-b-motoroel", "Motoröl", "Text", 750, evidence_ids=["web-wartung-1"],
      kategorie="web_wartung", ordnung=0, kompakt="„Motoröl“ (Wechsel)")
neu = [a.id for a in s._sortiert("verkaeuferfragen")]
alt_f2 = [a.id for a in sorted(s._pro_bereich["verkaeuferfragen"].values(),
                               key=lambda a: (-a.rang, 0 if s._ordnung.get(("verkaeuferfragen", a.id.split("-", 1)[1])) is not None else 1,
                                              s._ordnung.get(("verkaeuferfragen", a.id.split("-", 1)[1])) or 0, a.id))]
basis_445 = [a.id for a in sorted(s._pro_bereich["verkaeuferfragen"].values(), key=lambda a: (-a.rang, a.id))]
print("  445b34c (ID):      ", basis_445)
print("  010671c (F2 alt):  ", alt_f2)
print("  jetzt (F2 korr.):  ", neu)
check("C1 Nicht-Wartungsaktionen behalten exakt ihre Plätze aus 445b34c",
      [i for i in neu if "schwachstelle" in i] == [i for i in basis_445 if "schwachstelle" in i]
      and all(neu[n] == basis_445[n] for n in range(len(neu)) if "schwachstelle" in basis_445[n]))
check("C2 Wartungen untereinander in Auswahlreihenfolge (Motoröl Pos. 0 vor Luftfilter Pos. 1), nicht nach ID",
      [i for i in neu if "wartung" in i] == ["frage-wartung-web-b-motoroel", "frage-wartung-web-a-luftfilter"])
sichtbar = [a.id for a in s.liste("verkaeuferfragen")]
check("C3 sichtbare Auswirkung am Limit: dieselben Nicht-Wartungsaktionen und gleich viele Wartungen wie 445b34c; "
      "nur WELCHE Wartung bleibt, folgt der Auswahl (Motoröl statt Luftfilter)",
      [i for i in sichtbar if "wartung" not in i] == [i for i in basis_445[:MAX_SPEZIFISCH_PRO_BEREICH] if "wartung" not in i]
      and sum("wartung" in i for i in sichtbar) == sum("wartung" in i for i in basis_445[:MAX_SPEZIFISCH_PRO_BEREICH])
      and "frage-wartung-web-b-motoroel" in sichtbar, str(sichtbar))
check("C4 ... und der gekappte Wartungspunkt (Luftfilter, später ausgewählt) steht konkret im Träger (Motoröl)",
      "„Luftfilter“" in next(a.aktion for a in s.liste("verkaeuferfragen") if a.id.endswith("motoroel")))
check("C5 Gegenprobe 010671c: die alte F2-Regel hätte eine Schwachstelle aus den sechs verdrängt",
      sorted(alt_f2[:MAX_SPEZIFISCH_PRO_BEREICH]) != sorted(basis_445[:MAX_SPEZIFISCH_PRO_BEREICH]))

if os.environ.get("ABNAHME_EXPORT"):
    with open(os.environ["ABNAHME_EXPORT"], "w", encoding="utf-8") as fh:
        json.dump({"mit_traeger": antwort1, "ohne_traeger": antwort2}, fh, ensure_ascii=False, indent=1)
    print("Export:", os.environ["ABNAHME_EXPORT"])

print()
print("ALLE GRÜN" if not fehler else f"{len(fehler)} FEHLER: {fehler}")
sys.exit(1 if fehler else 0)
