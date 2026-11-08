"""
N1 -- Auswahl des einen Katalogtreffers für die Rollenbeleg-Nachladung.

Echte, aufgezeichnete Live-Treffer (08.11.2026, tests/fixtures/live_rollenbeleg_treffer_2026-11-08.json):
zwei ADAC-Einträge sind VOR dem Abruf unterscheidbar -- am Titel:
  * "Lexus RC 200t F Sport Automatik ..."  -> nur die Modellfamilie (das Snippet nennt den RC F bloß
                                              als großen Bruder; XC1-Eintrag, 245 PS)
  * "Lexus RC F Ultimate Edition ..."      -> das genaue Zielmodell (UXC1-Eintrag)
Gewählt wird nur, wenn die Modellwörter im Titel zusammenhängend stehen. Keine Abrufinformation,
keine Domain-/Modell-/URL-ID-Sonderlogik.

    python scripts/test_isolated.py test_n1_rollenbeleg_auswahl.py
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import app.technical_research as tr
import test_n1_uebersicht_fallback as T  # noqa: E402  (läuft beim Import mit)
from test_n1_uebersicht_fallback import PDF, UEBERSICHT, UEBERSICHT_TEXT  # noqa: E402
from test_n1_generation_spec_gap_run_kaufcheck import treffer  # noqa: E402

_FEHLER: list[str] = []


def check(name, bedingung, detail=""):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


FIX = Path(__file__).parent / "tests" / "fixtures"
REC = json.loads((FIX / "live_rollenbeleg_treffer_2026-11-08.json").read_text(encoding="utf-8"))
RUNDE1, FACH = REC["treffer"][0]["hits"], REC["treffer"][1]["hits"]
ALLE = RUNDE1 + FACH
XC1_URL = next(h["url"] for h in ALLE if "252101" in h["url"])        # RC 200t F Sport (Familie)
UXC1_URL = next(h["url"] for h in ALLE if "339429" in h["url"])       # RC F Ultimate Edition (Ziel)
XC1_VOLL = REC["extrakt"]
UXC1_VOLL = (FIX / "adac_lexus_rc_f_seitentext_2026-11-08.txt").read_text(encoding="utf-8")
TITEL = {h["url"]: h["title"] for h in ALLE}


def mk(hs):
    out = []
    for h in hs:
        t = treffer(h["url"], h["title"], h["content"] or "")
        if h.get("raw_content"):
            t["raw_content"] = h["raw_content"]
        out.append(t)
    return out


class _Fang(logging.Handler):
    def __init__(self):
        super().__init__()
        self.zeilen: list[str] = []

    def emit(self, record):
        m = record.getMessage()
        if m.startswith("research_phase=identitaet_rollenbeleg_nachladung"):
            self.zeilen.append(m)


async def lauf(runde1, fach, extract):
    T.routing = lambda pdf_text_vorhanden=True, mit_adac=True: {
        "technische Daten Herstellerangabe": mk(fach),
        "technische Daten": mk(runde1),
        "Generation Baureihe Bauzeitraum": [],
        "Rettungskarte Rettungsdatenblatt": [
            treffer(UEBERSICHT, "Rettungsdatenblätter | Lexus", "Alle Rettungsdatenblätter auf einen Blick.")],
    }
    fang, logger = _Fang(), logging.getLogger("app.technical_research")
    alt = logger.level
    logger.addHandler(fang)
    logger.setLevel(logging.INFO)
    try:
        fake, r = await T.lauf(extract)
    finally:
        logger.removeHandler(fang)
        logger.setLevel(alt)
    return fake, r, fang.zeilen


def texte(r):
    k = r.kaufaktionen
    t = [r.bericht]
    for liste in (k.besichtigung, k.probefahrt, k.verkaeuferfragen, k.dokumente):
        t += [str(a) for a in liste.fahrzeugspezifisch + liste.basis]
    return " ".join(t)


EXTRACT = {UEBERSICHT: UEBERSICHT_TEXT, UXC1_URL: UXC1_VOLL, XC1_URL: XC1_VOLL}


async def main():
    print("--- Signal VOR dem Abruf (nur Titel/Snippet der aufgezeichneten Treffer) ---")
    for url, titel in TITEL.items():
        if "adac.de" in url and "/lexus/rc/" in url:
            folge = tr._enthaelt_folge(tr._modell_woerter(titel), ["rc", "f"])
            print(f"   Titel {titel[:62]!r}: Modellfolge 'RC F' zusammenhängend = {folge}")
    check("S1) Titel des RC-F-Eintrags enthält 'RC F' zusammenhängend",
          tr._enthaelt_folge(tr._modell_woerter(TITEL[UXC1_URL]), ["rc", "f"]))
    check("S2) Titel des Familien-Eintrags (RC 200t F Sport) enthält 'RC F' NICHT zusammenhängend",
          not tr._enthaelt_folge(tr._modell_woerter(TITEL[XC1_URL]), ["rc", "f"]))
    snippet = next(h["content"] for h in ALLE if h["url"] == XC1_URL)
    check("S3) das Snippet des Familien-Eintrags nennt 'RC F' (nur als Schwestermodell) -> Snippet ist KEIN Signal",
          "RC F" in snippet and "RC 200t" in snippet)

    for name, (r1, f) in {
        "Reihenfolge A (wie aufgezeichnet)": (RUNDE1, FACH),
        "Reihenfolge B (Treffer und Anfragen vertauscht, jeweils umgekehrt)": (list(reversed(FACH)), list(reversed(RUNDE1))),
    }.items():
        print(f"\n--- {name} ---")
        fake, r, lg = await lauf(r1, f, EXTRACT)
        print("   " + (lg[-1] if lg else "(keine Logzeile)"))
        check("W1) genau ein zusätzlicher Abruf, und zwar der genaue Zieleintrag (nicht der Familien-Eintrag)",
              fake.extract_calls == [[UEBERSICHT], [UXC1_URL]], str([[u[-24:] for u in c] for c in fake.extract_calls]))
        check("W2) UXC1 im Bericht (echte PDF + nachgeladener Rollenbeleg)",
              r.vehicle_identity.get("generation") == "UXC1", str(r.vehicle_identity.get("generation")))
        check("W3) Logzeile ohne URL, Host adac.de, ausgewählt",
              bool(lg) and "grund=ausgewaehlt ausgang=nachgeladen" in lg[-1] and "://" not in lg[-1], str(lg))
        t = texte(r)
        vi = r.vehicle_identity
        check("W4) 464 PS und 'Ultimate Edition' verfälschen weder Fahrzeugangaben noch Fragen/Dokumente",
              vi.get("horsepower") == 477 and vi.get("year") == 2017
              and not any(x in t for x in ("464", "Ultimate Edition", "08/24", "245 PS", "200t")), "")

    print("\n--- fremder Eintrag allein ---")
    # Die übrigen Quellen tragen die Identität, bringen aber schon Volltext mit -> keine Kandidaten.
    voll_text = "Lexus RC F Technische Daten Leistung Verbrauch " * 20
    nur_fremd = [dict(h, raw_content=voll_text) if h["url"] != XC1_URL else h
                 for h in RUNDE1 if h["url"] != UXC1_URL]
    fach_ohne = [dict(h, raw_content=voll_text) for h in FACH if "auto-data" in h["url"]]
    fake, r, lg = await lauf(nur_fremd, fach_ohne, EXTRACT)
    print("   " + (lg[-1] if lg else "(keine Logzeile)"))
    check("F1) nur Familie vorhanden: KEIN zusätzlicher Abruf, Generation bleibt offen",
          fake.extract_calls == [[UEBERSICHT]] and r.vehicle_identity.get("generation") is None,
          f"{len(fake.extract_calls)} / {r.vehicle_identity.get('generation')!r}")
    check("F2) Logzeile nennt den Grund 'nur_modellfamilie'",
          any("grund=nur_modellfamilie" in z and "ausgang=kein_abruf" in z for z in lg), str(lg))

    voll = [dict(h, content=XC1_VOLL) if h["url"] == XC1_URL else h for h in nur_fremd]
    fake, r, lg = await lauf(voll, fach_ohne, {UEBERSICHT: UEBERSICHT_TEXT})
    check("F3) selbst mit dem VOLLEN Text des fremden Eintrags (XC1, 245 PS) wird UXC1 nicht bestätigt",
          r.vehicle_identity.get("generation") != "UXC1", str(r.vehicle_identity.get("generation")))

    if _FEHLER:
        print(f"\n{len(_FEHLER)} FEHLER:")
        for f in _FEHLER:
            print("  -", f)
        raise SystemExit(1)
    print("\nALLE N1-ROLLENBELEG-AUSWAHL-TESTS GRUEN")


asyncio.run(main())
