"""
SW-Diagnose Textuebernahme: je Schwachstellen-Treffer raw_len, eval_len, gekuerzt, snippet_im_raw, snippet_im_eval und
claims_bewertet -- reine Diagnose, ohne Wirkung auf Textauswahl, Kuerzung, Fakten oder Ablehnungen.

Gegenproben: R1 [REAL] gesicherter PistonHeads-Artikeltext vom 19.11.2026 (tests/fixtures/sw_quellen_rcf_abruf_2026-11-19.json,
eigene Umwandlung, NICHT der Providertext eines Laufs), R2 [REAL] nur Snippet (echter Satz derselben Seite),
S2 [SYNTH] vorangestellte Navigation mit Kuerzung. Die Messwerte erklaeren keinen historischen Request nachtraeglich.

    python scripts/test_isolated.py test_sw_diagnose_textuebernahme.py
"""
import collections
import json
import logging
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
    _netz_gesperrt = False
except Exception:
    _netz_gesperrt = True
_db = os.environ.get("AUTO_KI_DB_PATH", "")
print("Isolation: Netz gesperrt =", _netz_gesperrt, "| Temp-DB =", _db)
check("0 Isolation: Netz gesperrt, temporäre DB gesetzt (Runner)", _netz_gesperrt and "enfal_offline_" in _db)

import app.technical_research as tr  # noqa: E402

FX = json.load(open(os.path.join(HIER, "tests", "fixtures", "sw_quellen_rcf_abruf_2026-11-19.json"), encoding="utf-8"))
TEXT = FX["quellen"][6]["text"]
URL = "https://www.pistonheads.com/news/ph-buying-guides/lexus-rc-f--ph-used-buying-guide/47506"
ZIEL = dict(marke="Lexus", modell="RC F", baujahr=2017, generation="UXC1", hubraum="5.0", fuel_ziel="benzin",
            powertrains_ziel=frozenset({"ICE"}), motor="5.0 V8")
SNIP = ("Some RC Fs had poorly-applied rear wheelarch sealer which could lead to water ingress and eventual rust, "
        "especially in high salt-use regions (step forward UK).")
NAV = "Menu item. " * 4000


def treffer(raw, snippet=SNIP, url=URL):
    return {"url": url, "title": "Lexus RC F | PH Used Buying Guide | PistonHeads UK", "content": snippet, "raw_content": raw}


def extraktion(r):
    ab: list[dict] = []
    f = tr._extrahiere_fakten([r], "schwachstelle", abgelehnt=ab, **ZIEL)
    gr = collections.Counter(a["grund"] for a in ab if a.get("kategorie") == "schwachstelle")
    return f, ab, gr


print("== A: Werte aus der richtigen Stufe ==")
(r1,), _ = tr._mit_originaltext([treffer(TEXT)])
d1 = r1["_diag_text"]
check("A1 R1: raw_len = gelieferter raw_content (vor Verarbeitung), eval_len = an die Extraktion uebergebener Text",
      d1["raw_len"] == len(TEXT.strip()) and d1["eval_len"] == len(r1["content"]) and r1["_ot"] == "volltext", str(d1))
check("A2 R1: nicht gekuerzt, Snippet in raw und eval enthalten", d1["gekuerzt"] is False
      and d1["snippet_im_raw"] is True and d1["snippet_im_eval"] is True, str(d1))
(r2,), _ = tr._mit_originaltext([treffer(None)])
d2 = r2["_diag_text"]
check("A3 R2 nur Snippet: raw_len und snippet_im_raw NICHT PRUEFBAR (None, nicht False), eval = Snippet",
      d2["raw_len"] is None and d2["snippet_im_raw"] is None and d2["eval_len"] == len(SNIP)
      and d2["snippet_im_eval"] is True and r2["_ot"] == "snippet", str(d2))
(r3,), st3 = tr._mit_originaltext([treffer(NAV + TEXT)])
d3 = r3["_diag_text"]
check("A4 S2 [SYNTH]: gekuerzt, raw_len > Grenze >= eval_len; Snippet im raw JA, nach der Kuerzung NEIN",
      d3["gekuerzt"] is True and d3["raw_len"] > tr._ORIGINALTEXT_MAX_ZEICHEN >= d3["eval_len"]
      and d3["snippet_im_raw"] is True and d3["snippet_im_eval"] is False and st3["gekuerzt"] == 1, str(d3))

print("\n== B: Zuordnung der Kuerzung und Nicht-pruefbar ==")
pool, st = tr._mit_originaltext([treffer(TEXT, url="https://www.pistonheads.com/a"),
                                 treffer(NAV + TEXT, url="https://www.pistonheads.com/b")])
check("B1 zwei Treffer, nur der lange ist gekuerzt (Kuerzung je Treffer, nicht nur als Rundenzaehler)",
      [p["_diag_text"]["gekuerzt"] for p in pool] == [False, True] and st["gekuerzt"] == 1)
(r4,), _ = tr._mit_originaltext([treffer(TEXT, snippet="")])
check("B2 leeres Snippet: beide Vergleiche nicht pruefbar (None), nicht False",
      r4["_diag_text"]["snippet_im_raw"] is None and r4["_diag_text"]["snippet_im_eval"] is None)
(r5,), _ = tr._mit_originaltext([treffer(TEXT, snippet="**Some RC Fs** had  poorly-applied rear wheelarch ... "
                                                       "eventual rust, especially in high salt-use regions")])
check("B3 Formatnormalisierung: Markdown, Leerraum und Auslassung '...' veraendern das Ergebnis nicht",
      r5["_diag_text"]["snippet_im_raw"] is True and r5["_diag_text"]["snippet_im_eval"] is True)
(r6,), _ = tr._mit_originaltext([treffer(TEXT, snippet="Some RC Fs had a completely different gearbox failure on track days.")])
check("B4 nicht enthaltenes Snippet: False (Indikator, kein Verlustnachweis)",
      r6["_diag_text"]["snippet_im_raw"] is False and r6["_diag_text"]["snippet_im_eval"] is False)

print("\n== C: Fakten und Ablehnungen unveraendert ==")
f1, ab1, g1 = extraktion(r1)
ohne = {k: v for k, v in r1.items() if k != "_diag_text"}
f1o, ab1o, g1o = extraktion(ohne)
check("C1 R1: identisch mit und ohne Diagnosefeld", [(x.bauteil, x.beleglage) for x in f1] == [(x.bauteil, x.beleglage) for x in f1o]
      and g1 == g1o)
check("C2 R1: bisheriges Ergebnis (170 Ablehnungen, 3 begrenzte Berichte: Klimaanlage, Rost, Sitzheizung)",
      sum(g1.values()) == 170 and sorted(x.bauteil for x in f1) == ["Klimaanlage", "Rost", "Sitzheizung/-belüftung"]
      and all(x.beleglage == "begrenzter_bericht" for x in f1), str((sum(g1.values()), [x.bauteil for x in f1])))
f2, _, g2 = extraktion(r2)
check("C3 R2: bisheriges Ergebnis (1 Ablehnung no_problem_signal, Rost begrenzter Bericht)",
      dict(g2) == {"no_problem_signal": 1} and [(x.bauteil, x.beleglage) for x in f2] == [("Rost", "begrenzter_bericht")])
f3, _, g3 = extraktion(r3)
check("C4 S2: bisheriges Ergebnis (1 Ablehnung, keine Fakten)", sum(g3.values()) == 1 and f3 == [])

print("\n== D: Logzeile -- Werte vorhanden, keine Inhalte ==")
zeilen: list[str] = []


class _H(logging.Handler):
    def emit(self, rec):
        zeilen.append(rec.getMessage())


h = _H(level=logging.INFO)
tr.log.addHandler(h)
tr.log.setLevel(logging.INFO)
url_q = "https://www.pistonheads.com/c?utm_source=x&token=geheim123"
(r7,), _ = tr._mit_originaltext([treffer(NAV + TEXT, url=url_q)])
# Eigene URLs je Treffer: der bestehende Zaehlpfad (claim_gruende, fakten) fasst je URL zusammen.
(r2d,), _ = tr._mit_originaltext([treffer(None, url="https://www.pistonheads.com/d2")])
(r3d,), _ = tr._mit_originaltext([treffer(NAV + TEXT, url="https://www.pistonheads.com/d3")])
alle = [r1, r2d, r3d, r7]
ab_alle, f_alle = [], []
for r in alle:
    f, ab, _ = extraktion(r)
    ab_alle += ab
    f_alle += f
stats = {"schwachstelle": {"kuratiert_ids": {id(r) for r in alle}, "kuratierungs_ausschluss": [], "kuratierungs_limit": None}}
tr._sw_diagnose_extraktion("testlauf01", 2, alle, stats, ab_alle, f_alle)
tr.log.removeHandler(h)
tz = [z for z in zeilen if "stufe=treffer" in z]
check("D1 je Treffer eine Zeile mit Laufkennung, Runde und Nummer", len(tz) == 4
      and all("lauf=testlauf01 runde=2" in z for z in tz) and [z.split(" nr=")[1].split()[0] for z in tz] == ["1", "2", "3", "4"])
check("D2 R1-Zeile: Laengen, gekuerzt=nein, Snippet ja/ja, URL-Aggregationen getrennt (170 Gruende, 3 Fakten)",
      f"raw_len={len(TEXT.strip())} eval_len={len(r1['content'])} gekuerzt=nein snippet_im_raw=ja snippet_im_eval=ja "
      "claim_gruende_url=170 fakten_url=3" in tz[0], tz[0][-160:])
check("D3 R2-Zeile: raw_len und snippet_im_raw nicht_pruefbar", "raw_len=nicht_pruefbar" in tz[1]
      and "snippet_im_raw=nicht_pruefbar snippet_im_eval=ja" in tz[1], tz[1][-160:])
check("D4 S2-Zeile: gekuerzt=ja, snippet_im_raw=ja, snippet_im_eval=nein",
      "gekuerzt=ja snippet_im_raw=ja snippet_im_eval=nein" in tz[2], tz[2][-160:])
log_text = "\n".join(zeilen)
check("D5 keine Seiten- oder Snippettexte im Log", "wheelarch" not in log_text and "Menu item" not in log_text
      and "PH Used Buying Guide" not in log_text)
check("D6 keine unbereinigten URL-Parameter im Log (bestehende _diag_url)", "geheim123" not in log_text and "utm_source" not in log_text)

print("\n== E: zwei Treffer DERSELBEN URL mit unterschiedlichen Texten ==")
zeilen.clear()
URL_GLEICH = "https://www.pistonheads.com/news/ph-buying-guides/gleiche-url/1"   # redaktioneller Pfad wie die reale Quelle
# Treffer 1: nur Snippet (kein raw_content); Treffer 2: derselbe Artikel als Volltext, aber hinter Navigation gekuerzt.
# `_mit_originaltext` uebertraegt einen brauchbaren Originaltext auf ALLE Treffer derselben URL -- deshalb hier
# getrennt verarbeitet, wie in zwei Runden (Treffer aus Runde 1 traegt bereits `_ot`).
(e1,), _ = tr._mit_originaltext([treffer(None, url=URL_GLEICH)])
(e2,), _ = tr._mit_originaltext([treffer(NAV + TEXT, url=URL_GLEICH)])
pool_e = [e1, e2]
ab_e, f_e = [], []
for r in pool_e:
    f, ab, _ = extraktion(r)
    ab_e += ab
    f_e += f
gr_url = sum(1 for a in ab_e if a.get("kategorie") == "schwachstelle")
stats_e = {"schwachstelle": {"kuratiert_ids": {id(r) for r in pool_e}, "kuratierungs_ausschluss": [], "kuratierungs_limit": None}}
tr.log.addHandler(h)
tr._sw_diagnose_extraktion("testlauf02", 2, pool_e, stats_e, ab_e, f_e)
tr.log.removeHandler(h)
ez = [z for z in zeilen if "stufe=treffer" in z]
check("E1 zwei Zeilen fuer dieselbe URL", len(ez) == 2)
check("E2 Textdiagnose je Treffer: Zeile 1 = Snippet (raw nicht pruefbar, eval = Snippetlaenge), Zeile 2 = gekuerzter Volltext",
      len(ez) == 2 and f"raw_len=nicht_pruefbar eval_len={len(SNIP)} gekuerzt=nein snippet_im_raw=nicht_pruefbar "
      "snippet_im_eval=ja" in ez[0]
      and f"raw_len={len((NAV + TEXT).strip())} eval_len={len(e2['content'])} gekuerzt=ja snippet_im_raw=ja snippet_im_eval=nein" in ez[1],
      " | ".join(z[-170:] for z in ez))
check("E3 URL-Aggregation: beide Zeilen tragen DIESELBEN Werte, beschriftet als *_url (Summe beider Treffer)",
      len(ez) == 2 and all(f"claim_gruende_url={gr_url} fakten_url={len(f_e)}" in z for z in ez) and gr_url == 2 and len(f_e) == 1,
      str((gr_url, len(f_e))))
check("E4 das Feld claims_bewertet erscheint nicht mehr", all("claims_bewertet" not in z for z in zeilen))

print("\n" + ("ALLE GRÜN" if not FEHLER else f"{len(FEHLER)} FEHLER: {FEHLER}"))
sys.exit(1 if FEHLER else 0)
