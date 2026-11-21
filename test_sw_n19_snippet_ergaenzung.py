"""
SW-N19/E1 -- eine bereits gelieferte Snippet-Textstelle geht nicht mehr allein dadurch verloren, dass ein unvollstaendiger
oder hinter Seitenelementen gekuerzter Volltext das Snippet ersetzt.

Live-Befund (Request bIUJZ4ABRji2xAPLw9P4nw, Lauf 72d0b16ea8, Deployment 7425323b auf 3e23d91): PistonHeads-Kaufberatung
mit raw_len=675 (gesicherter Artikeltext: rund 20.000 Zeichen), 0 Fakten. Der Snippet-Inhalt dieses Laufs ist NICHT
gespeichert -- ob er den Rostsatz enthielt, bleibt offen; hier wird nur der generische Mechanismus nachgestellt.

Texte: [REAL] gesicherter Artikel vom 19.11.2026 (tests/fixtures/sw_quellen_rcf_abruf_2026-11-19.json, eigene Umwandlung,
kein Providertext) und echte Saetze daraus; [SYNTH] gekennzeichnete Varianten sind erfunden und belegen nur die Logik.

    python scripts/test_isolated.py test_sw_n19_snippet_ergaenzung.py
"""
import collections
import json
import os
import socket
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HIER = os.path.dirname(os.path.abspath(__file__))
FEHLER: list[str] = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        FEHLER.append(name)


try:
    socket.create_connection(("example.com", 80), timeout=1)
    _gesperrt = False
except Exception:
    _gesperrt = True
_db = os.environ.get("AUTO_KI_DB_PATH", "")
print("Isolation: Netz gesperrt =", _gesperrt, "| Temp-DB =", _db)
check("0 Isolation: Netz gesperrt, temporäre DB (Runner)", _gesperrt and "enfal_offline_" in _db)

import logging  # noqa: E402
logging.disable(logging.CRITICAL)
import app.recall_filter as _rf  # noqa: E402
_rf.get_rueckruf_referenzen_kurz = lambda: []
import app.technical_research as tr  # noqa: E402
from app.evidence import build_insights  # noqa: E402
from app.kaufaktionen import build_kaufaktionen  # noqa: E402
from app.kaufcheck_bericht import bericht  # noqa: E402
from app.models import EvidenceQuelle, Insight, KaufCheckRequest, TechnischeRecherche  # noqa: E402
from app.vehicle_identity import VehicleIdentity  # noqa: E402

FX = json.load(open(os.path.join(HIER, "tests", "fixtures", "sw_quellen_rcf_abruf_2026-11-19.json"), encoding="utf-8"))
ARTIKEL = FX["quellen"][6]["text"]
URL = "https://www.pistonheads.com/news/ph-buying-guides/lexus-rc-f--ph-used-buying-guide/47506"
TITEL = "Lexus RC F | PH Used Buying Guide | PistonHeads UK"
ZIEL = dict(marke="Lexus", modell="RC F", baujahr=2017, generation="UXC1", hubraum="5.0", fuel_ziel="benzin",
            powertrains_ziel=frozenset({"ICE"}), motor="5.0 V8")
ROST = ("Some RC Fs had poorly-applied rear wheelarch sealer which could lead to water ingress and eventual rust, "
        "especially in high salt-use regions (step forward UK).")
# [SYNTH] unvollstaendiger Volltext in der Groessenordnung des Live-Befunds (675 Zeichen): nur Seitenelemente/Teaser
KURZ_VT = ("Lexus RC F | PH Used Buying Guide - PistonHeads UK. Toggle main menu. Saved. Sign in. Close. Auctions. "
           "Classifieds. News. Reviews. Buying Guides. Forums. The RC F was Lexus's answer to the M4 and C63, a big "
           "naturally aspirated V8 coupe with a lot of character. Read on for our full buyer's guide to the Lexus RC F, "
           "covering prices, specs and what to look for. Sponsored. Newsletter sign-up. Cookie settings. ") * 1
NAV = "Menu item. " * 4000


def lauf(raw, snippet=ROST, url=URL, ergaenzung=True):
    pool, _ = tr._mit_originaltext([{"url": url, "title": TITEL, "content": snippet, "raw_content": raw}])
    if not ergaenzung:                      # Zustand VOR dem Fix: Snippet wird nur ersetzt
        pool = [{**r, "_snippet_ergaenzung": ()} for r in pool]
    ab: list[dict] = []
    f = tr._extrahiere_fakten(pool, "schwachstelle", abgelehnt=ab, **ZIEL)
    return pool[0], f, collections.Counter(a["grund"] for a in ab if a.get("kategorie") == "schwachstelle")


def bt(f):
    return [(x.bauteil, x.beleglage) for x in f]


print("== A: belegte Verluststelle vorher/nachher ==")
r, f_vor, _ = lauf(KURZ_VT, ergaenzung=False)
check("A1 VOR dem Fix [SYNTH kurzer Volltext + REAL Rostsatz im Snippet]: Volltext ersetzt Snippet, kein Fakt",
      r["_ot"] == "volltext" and f_vor == [], str(bt(f_vor)))
r, f_nach, g = lauf(KURZ_VT)
check("A2 NACH dem Fix: Rost als begrenzter Bericht erhalten", bt(f_nach) == [("Rost", "begrenzter_bericht")], str(bt(f_nach)))
check("A3 Diagnose: Snippet nicht im ausgewerteten Text, genau ein ergaenztes Teilstueck",
      r["_diag_text"]["snippet_im_eval"] is False and r["_diag_text"]["snippet_ergaenzt"] == 1, str(r["_diag_text"]))
q = f_nach[0].quellen if f_nach else []
check("A4 eine Quelle, kein zweiter Beleg (Snippet und Volltext = Textstellen derselben Quelle)",
      len(q) == 1 and q[0].url == URL and f_nach[0].confidence == "niedrig", str([(x.url, x.qualitaet) for x in q]))
r, f_s2_vor, _ = lauf(NAV + ARTIKEL, ergaenzung=False)
r, f_s2, _ = lauf(NAV + ARTIKEL)
check("A5 [SYNTH Navigation vor REAL Artikel, Kuerzung]: vorher kein Fakt, nachher Rost begrenzt erhalten",
      f_s2_vor == [] and bt(f_s2) == [("Rost", "begrenzter_bericht")] and r["_diag_text"]["gekuerzt"] is True, str(bt(f_s2)))

print("\n== B: vollstaendiger Artikel und identische Evidenz -- unveraendert, keine Doppelung ==")
r, f_r1, g_r1 = lauf(ARTIKEL)
r0, f_r1_vor, g_r1_vor = lauf(ARTIKEL, ergaenzung=False)
check("B1 [REAL] vollstaendiger Artikel: Snippet enthalten, nichts ergaenzt, Ergebnis identisch zu vorher",
      r["_diag_text"]["snippet_im_eval"] is True and r["_diag_text"]["snippet_ergaenzt"] == 0
      and bt(f_r1) == bt(f_r1_vor) and g_r1 == g_r1_vor, str(r["_diag_text"]))
check("B2 [REAL] bisheriges Ergebnis: drei begrenzte Berichte, je genau einmal",
      sorted(x for x, _ in bt(f_r1)) == ["Klimaanlage", "Rost", "Sitzheizung/-belüftung"])
r, f_b3, _ = lauf(None)
check("B3 [REAL] nur Snippet (kein Volltext): unveraendert ein Rost-Bericht", bt(f_b3) == [("Rost", "begrenzter_bericht")])
MD_SNIP = "Some **RC Fs** had poorly-applied rear wheelarch sealer [...] eventual rust, especially in high salt-use regions"
r, f_b4, _ = lauf(ARTIKEL, snippet=MD_SNIP)
check("B4 Snippet mit Provider-Auslassung '[...]' und Markdown im vollstaendigen Text: abgedeckt, keine Ergaenzung, "
      "keine Doppelung", r["_diag_text"]["snippet_im_eval"] is True and r["_diag_text"]["snippet_ergaenzt"] == 0
      and [x for x, _ in bt(f_b4)].count("Rost") == 1)

print("\n== C: keine falsche Modell-/Markt-/Variantenuebertragung; Verneinung; Volltext zum selben Thema ==")
FAELLE = {
    "C1 fremdes Modell": "Some GS Fs had poorly-applied rear wheelarch sealer which could lead to eventual rust in salty regions.",
    "C2 Verneinung": "The RC F has no rust problems, the wheelarch sealer was applied properly on all cars.",
    "C3 fremder Markt": "American owners of the RC F have reported eventual rust after poorly-applied wheelarch sealer.",
    "C4 Einzelfahrzeug": "My RC F had poorly-applied rear wheelarch sealer and showed eventual rust after one winter.",
}
for name, snip in FAELLE.items():
    r, f, g = lauf(KURZ_VT, snippet=snip)
    check(f"{name} [SYNTH]: ergaenzt und geprueft, aber kein Fakt (bestehende Gates)",
          r["_diag_text"]["snippet_ergaenzt"] == 1 and f == [], str((r["_diag_text"]["snippet_ergaenzt"], bt(f), dict(g))))
REL_VT = KURZ_VT + " Rust is not a known problem on the RC F, the bodywork holds up well. "
r, f, _ = lauf(REL_VT)
check("C5 [SYNTH] kurzer Volltext aeussert sich selbst zu Rost (relativierend): keine Ergaenzung, kein Fakt "
      "(kein Snippet-Retry gegen den Volltext)", r["_diag_text"]["snippet_ergaenzt"] == 0 and f == [], str(bt(f)))
r, f, _ = lauf(KURZ_VT, snippet="Read on for prices, specs and what to look for when buying a used RC F coupe today.")
check("C6 Snippet ohne Bauteil: keine Ergaenzung", r["_diag_text"]["snippet_ergaenzt"] == 0 and f == [])

print("\n== D: Ausgabekette bei zwei Rueckrufen und fuenf Wartungen ==")
REQ = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000, kraftstoff="Benzin",
                       motor="5.0 V8, Heckantrieb", ausstattung=[], vorbesitzer=2, scheckheftgepflegt=True)
ins = build_insights(None, None, [], REQ, check_typ="kauf", canonical_rueckrufe=[],
                     web_recherche=TechnischeRecherche(ausgeloest_durch="db_miss", fakten=f_nach))
for n in (1, 2):
    ins.append(Insight(id=f"RUECK-{n}", kategorie="rueckruf", kurztitel=f"Rückrufmangel {n}", titel=f"Rückruf: Mangel {n}",
                       beschreibung=f"Amtlicher Rückruf {n}.", confidence="hoch", applicability="variant_match", trust="verified",
                       quellen=[EvidenceQuelle(typ="rueckruf_kba", ref=f"0000{n}", label="KBA")]))
for n, b in enumerate(("Motoröl", "Zahnriemen", "Ventilspiel", "Bremsflüssigkeit", "Getriebeöl"), 1):
    ins.append(Insight(id=f"WART-{n}", kategorie="wartung", titel=f"Wartung: {b}", bauteil=b,
                       beschreibung=f"{b} laut Hersteller alle 30.000 km.", confidence="mittel",
                       quellen=[EvidenceQuelle(typ="motorvarianten", ref=b, label="DB")]))
ka = build_kaufaktionen(REQ, None, None, ins)
rost_ins = [i for i in ins if i.kategorie == "web_schwachstelle"]
fragen = [*ka.verkaeuferfragen.fahrzeugspezifisch, *ka.verkaeuferfragen.basis]
rf = [a for a in fragen if "„Rost“" in a.titel]
check("D1 ein Rost-Insight mit begrenztem Bericht und Vorbehalten", len(rost_ins) == 1 and rost_ins[0].beleglage == "begrenzter_bericht"
      and "nicht als Mangel werten" in (rost_ins[0].einfluss or ""))
check("D2 spezifischer Besichtigungspunkt Rost mit Herkunftshinweis",
      any("Rost" in a.titel and "Webquelle" in a.aktion for a in ka.besichtigung.fahrzeugspezifisch))
check("D3 Rost-Verkaeuferfrage genau einmal, mit evidence_id und Vorbehalt 'keine belegte Häufigkeit'",
      len(rf) == 1 and rf[0].evidence_ids == [rost_ins[0].id] and "keine belegte Häufigkeit" in rf[0].aktion
      and "ob dieses Fahrzeug dazu gehört, ist nicht belegt" in rf[0].aktion)
check("D4 keine kuenstliche Dokumentenforderung fuer den Rosthinweis",
      not [a for a in (*ka.dokumente.fahrzeugspezifisch, *ka.dokumente.basis) if a.kategorie == "web_schwachstelle"])
rr = [a for a in fragen if a.kategorie == "rueckruf"]
check("D5 Rueckrufe getrennt und unveraendert (zwei Fragen, FIN-Pruefung statt behaupteter Betroffenheit)",
      len(rr) == 2 and all("Ist bekannt, ob dieses Fahrzeug von der Rückrufaktion" in a.titel for a in rr))
check("D6 Wartung unveraendert vorhanden (Fragen bzw. Sammelpunkt)",
      sum(1 for a in fragen if a.kategorie == "wartung") >= 3
      and any("Weitere ausgewählte Wartungspunkte" in a.aktion for a in fragen if a.kategorie in ("wartung", "basis")))
text = bericht(REQ, VehicleIdentity.from_check_context(None, None, REQ), None, None, ins, ka, [], "kaufen_nach_besichtigung",
               type("PA", (), {"label": None})(), False, None, None, ["Inserat"])
check("D7 Vollbericht: Rost-Abschnitt mit begrenztem Status und Frage in der Fragenliste",
      "### Rost" in text and "keine belegte Häufigkeit" in text and "Wurde am Bauteil „Rost“ bereits gearbeitet" in text)

print("\n" + ("ALLE GRÜN" if not FEHLER else f"{len(FEHLER)} FEHLER: {FEHLER}"))
sys.exit(1 if FEHLER else 0)
