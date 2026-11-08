"""
N1 -- Diagnosezeile der Generationsentscheidung (`research_phase=generation_entscheidung`).

Reine Beobachtbarkeit: die Bestätigungsregeln bleiben unverändert. Die Zeile muss
drei Fälle unterscheiden, hier am echten run_kaufcheck()-Pfad mit der ECHTEN
Rettungskarten-PDF (Übersichts-Fallback, eigener Download, pypdf):

  A) PDF-Treffer verworfen   (PDF eines anderen Modells -> Alignment scheitert)
  B) UXC1 ohne Rollenbeleg   (echte PDF, aber kein unabhängiger Rollenbeleg)
  C) UXC1 bestätigt          (echte PDF + unabhängiger Rollenbeleg)

    python scripts/test_isolated.py test_n1_generation_diagnose.py
"""
import asyncio
import logging
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import app.technical_research as tr
from test_n1_uebersicht_fallback import (  # noqa: E402  (läuft beim Import mit)
    PDF, PDF_BYTES, UEBERSICHT, UEBERSICHT_TEXT, lauf, mini_pdf,
)
from test_n1_generation_spec_gap_run_kaufcheck import _ADAC, _AMS  # noqa: E402

_FEHLER: list[str] = []


def check(name, bedingung, detail=""):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


class _Fang(logging.Handler):
    def __init__(self):
        super().__init__()
        self.zeilen: list[str] = []

    def emit(self, record):
        m = record.getMessage()
        if m.startswith("research_phase=generation_entscheidung"):
            self.zeilen.append(m)


async def mit_log(**kw):
    fang, logger = _Fang(), logging.getLogger("app.technical_research")
    alt = logger.level
    logger.addHandler(fang)
    logger.setLevel(logging.INFO)
    try:
        fake, r = await lauf({UEBERSICHT: UEBERSICHT_TEXT}, **kw)
    finally:
        logger.removeHandler(fang)
        logger.setLevel(alt)
    return fang.zeilen, r


async def main():
    print("\n--- A) PDF-Treffer verworfen ---")
    # A1: PDF eines anderen Modells -> Treffer ausgerichtet (Linkbeschriftung), aber keine Kennung
    zeilen_a, r_a = await mit_log(pdf={PDF: (200, {}, mini_pdf(
        "Lexus LC 500 (Z100 (EU, M), ab 03/2017) Stand: 02/2017 12V Spannungsversorgung trennen"))})
    a = zeilen_a[-1]
    print("   A1 " + a)
    check("A1) PDF geladen und ausgerichtet, aber KEIN Generationsclaim aus dem Dokument",
          "dok_treffer=1 (davon_verlinkt=1) dok_ausgerichtet=1" in a and "dok_generationsclaims=0" in a, a)
    check("A2) Ergebnis offen, die PDF steuerte keine Kennung bei", "offen" in a and "dok_domains=toyota.eu" not in a, a)
    check("A3) sichtbar unverändert: keine Generation", r_a.vehicle_identity.get("generation") is None)

    # A4: Dokumenttreffer, der das Alignment NICHT besteht (fremdes Modell in Titel und Text)
    ziel = tr._ziel("Lexus", "RC F", 2017, "5.0 V8", None)
    fremd = {"url": PDF, "title": "Lexus LC 500 Rettungskarte",
             "content": "Lexus LC 500 (Z100 (EU, M), ab 03/2017) Stand: 02/2017",
             "verlinkt_von_herstellerseite": UEBERSICHT}
    fang, logger = _Fang(), logging.getLogger("app.technical_research")
    alt = logger.level
    logger.addHandler(fang)
    logger.setLevel(logging.INFO)
    try:
        tr.werte_identitaet_aus([_ADAC, _AMS, fremd], ziel)
    finally:
        logger.removeHandler(fang)
        logger.setLevel(alt)
    a4 = fang.zeilen[-1] if fang.zeilen else ""
    print("   A4 " + a4)
    check("A4) Dokumenttreffer, der das Alignment nicht besteht, wird mit Grund als verloren gezählt",
          "dok_treffer=1" in a4 and "dok_ausgerichtet=0" in a4 and "dok_verloren=modell_fehlt" in a4, a4)

    print("\n--- B) UXC1 erkannt, aber ohne unabhängigen Rollenbeleg ---")
    zeilen_b, r_b = await mit_log(mit_adac="ohne_rolle")
    b = zeilen_b[-1]
    print("   " + b)
    check("B1) PDF-Treffer ausgerichtet, Kennung UXC1 erkannt (Dokumentdomain scene7.toyota.eu)",
          "dok_ausgerichtet=1" in b and "dok_generationsclaims=3" in b and "UXC1[claims=3 dok_domains=scene7.toyota.eu" in b, b)
    check("B2) kein unabhängiger Rollenbeleg, Claim verworfen: ohne_generationskontext",
          "unabhaengiger_rollenbeleg=False" in b and "verworfen=ohne_generationskontext=3" in b, b)
    check("B3) Ergebnis offen; die UXC1-Claims sind alle verworfen, übrig bleibt nur XC1 unter der Schwelle",
          "offen grund=konsensschwelle_nicht_erreicht" in b, b)
    check("B4) sichtbar unverändert: keine Generation", r_b.vehicle_identity.get("generation") is None)

    print("\n--- C) UXC1 bestätigt ---")
    zeilen_c, r_c = await mit_log()
    c = zeilen_c[-1]
    print("   " + c)
    check("C1) PDF-Treffer ausgerichtet, UXC1 mit unabhängigem Rollenbeleg, verknüpft",
          "dok_ausgerichtet=1" in c and "unabhaengiger_rollenbeleg=True" in c and "verknuepft=3" in c, c)
    check("C2) Ergebnis bestätigt: UXC1", "bestaetigt code=UXC1" in c, c)
    check("C3) sichtbar unverändert: UXC1 im Bericht, Motorisierungssperre bleibt",
          r_c.vehicle_identity.get("generation") == "UXC1" and "Blockierende Identitätslücke: Motorisierung" in r_c.bericht)

    print("\n--- Die drei Zeilen sind verschieden und enthalten keine URLs/Texte ---")
    check("D1) A, B, C unterscheiden sich im Ergebnisteil", len({a.split(" codes=")[1], b.split(" codes=")[1],
                                                                 c.split(" codes=")[1]}) == 3)
    alle = " ".join(zeilen_a + [a4] + zeilen_b + zeilen_c)
    check("D2) weder URL noch Dokumenttext in den Zeilen",
          "://" not in alle and "Spannungsversorgung" not in alle and "RCF_tcm" not in alle and "/" not in alle,
          alle[:200])

    if _FEHLER:
        print(f"\n{len(_FEHLER)} FEHLER:")
        for f in _FEHLER:
            print("  -", f)
        raise SystemExit(1)
    print("\nALLE N1-GENERATIONSDIAGNOSE-TESTS GRUEN")


asyncio.run(main())
