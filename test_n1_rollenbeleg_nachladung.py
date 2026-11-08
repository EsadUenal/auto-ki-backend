"""
N1 -- gezielte Nachladung eines unabhängigen Rollenbelegs.

Nach der Hersteller-PDF ist die Kennung (UXC1) erkannt, aber ohne unabhängige
Rollenbezeichnung verworfen. Hat ein bereits gefundener Katalogtreffer nur einen
Auszug OHNE Baureihenzeile, wird GENAU EIN Treffer per Extract vollständig
nachgeladen und läuft durch die unveränderte Rollen-/Zeitraumprüfung.

Echte Daten: echte Rettungskarten-PDF (Bytes, pypdf), echter ADAC-Seitentext
(tests/fixtures/adac_lexus_rc_f_seitentext_2026-11-08.txt), echter Übersichtstext.
Echter run_kaufcheck()-Pfad; Suche/Extract/DB/Download gestubbt; Netz+DB isoliert.

    python scripts/test_isolated.py test_n1_rollenbeleg_nachladung.py
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import app.technical_research as tr
import test_n1_uebersicht_fallback as T  # noqa: E402  (läuft beim Import mit)
from test_n1_uebersicht_fallback import PDF, UEBERSICHT, UEBERSICHT_TEXT  # noqa: E402
from test_n1_generation_spec_gap_run_kaufcheck import _ADAC, _AMS, treffer  # noqa: E402

_FEHLER: list[str] = []


def check(name, bedingung, detail=""):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


ADAC_URL = "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/"
ADAC_TITEL = "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25): Technische Daten, Bilder, Preise | ADAC"
ADAC_VOLLTEXT = (Path(__file__).parent / "tests" / "fixtures"
                 / "adac_lexus_rc_f_seitentext_2026-11-08.txt").read_text(encoding="utf-8")
VORSCHAU = ("Lexus RC F Ultimate Edition Automatik 341 kW (464 PS) Grundpreis "
            "Technische Daten Bilder Preise Kraftstoffverbrauch")   # OHNE Baureihenzeile
ADAC_HIT = treffer(ADAC_URL, ADAC_TITEL, VORSCHAU)
AMS_OHNE_CODE = treffer(_AMS["url"], _AMS["title"],
                        "Lexus RC F Überblick Leistung bis 477 PS Hubraum 4969 cm3 V8-Sauger Supertest")


class _Fang(logging.Handler):
    def __init__(self):
        super().__init__()
        self.zeilen: list[str] = []

    def emit(self, record):
        m = record.getMessage()
        if m.startswith("research_phase=identitaet_rollenbeleg_nachladung"):
            self.zeilen.append(m)


async def lauf(technik_treffer, extract, uebersicht=True):
    """Routing: nur die Technik-Treffer variieren; Übersicht + PDF wie im Fallback-Test."""
    T.routing = lambda pdf_text_vorhanden=True, mit_adac=True: {
        "technische Daten": technik_treffer,
        "Generation Baureihe Bauzeitraum": [AMS_OHNE_CODE],
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


def alle_texte(r):
    k = r.kaufaktionen
    teile = [r.bericht]
    for liste in (k.besichtigung, k.probefahrt, k.verkaeuferfragen, k.dokumente):
        teile += [str(a) for a in liste.fahrzeugspezifisch + liste.basis]
    return " ".join(teile)


async def main():
    print("\n--- 1) Vorschau ohne Baureihenzeile -> EIN zusätzlicher Abruf -> UXC1 im Bericht ---")
    fake, r, log1 = await lauf([ADAC_HIT], {UEBERSICHT: UEBERSICHT_TEXT, ADAC_URL: ADAC_VOLLTEXT})
    print("   " + (log1[-1] if log1 else "(keine Logzeile)"))
    check("1a) Abrufe: erst Übersicht (Tavily), dann GENAU EIN Katalogtreffer; PDF per eigenem Download",
          fake.extract_calls == [[UEBERSICHT], [ADAC_URL]] and T.ANFRAGEN == [PDF],
          f"{fake.extract_calls} / {T.ANFRAGEN}")
    check("1b) Generation im Bericht = UXC1", r.vehicle_identity.get("generation") == "UXC1",
          str(r.vehicle_identity.get("generation")))
    check("1c) Logzeile: Kandidatenzahl, Host, Ausgang - ohne URL",
          bool(log1) and "kandidaten=1 hosts=adac.de grund=ausgewaehlt ausgang=nachgeladen" in log1[-1]
          and "://" not in log1[-1], str(log1))
    hinweis = r.bericht[r.bericht.find("Blockierende Identitätslücke"):][:100]
    check("1d) Generationssperre weg, Motorisierungssperre bleibt (N6 unberührt)",
          "Generation" not in hinweis and "Motorisierung" in hinweis, hinweis)

    print("\n--- 2) 464-PS-Ausführung verfälscht nichts für 2017 ---")
    vi = r.vehicle_identity
    text = alle_texte(r)
    check("2a) Leistung bleibt die Nutzerangabe 477 PS, nicht 464", vi.get("horsepower") == 477, str(vi.get("horsepower")))
    check("2b) Baujahr bleibt 2017", vi.get("year") == 2017, str(vi.get("year")))
    check("2c) weder '464', 'Ultimate Edition' noch '08/24' in Bericht, Fragen, Dokumenten, Checklisten",
          not any(x in text for x in ("464", "Ultimate Edition", "08/24")), "")
    _, r_ref, _ = await lauf([_ADAC], {UEBERSICHT: UEBERSICHT_TEXT})   # Referenz: Rollenzeile schon im Snippet
    check("2d) Fragen und Dokumente identisch zum Referenzlauf mit Rollenzeile im Snippet",
          r.kaufaktionen.verkaeuferfragen.model_dump() == r_ref.kaufaktionen.verkaeuferfragen.model_dump()
          and r.kaufaktionen.dokumente.model_dump() == r_ref.kaufaktionen.dokumente.model_dump())

    print("\n--- 3) Gegenfälle ---")
    fake, r, lg = await lauf([ADAC_HIT], {UEBERSICHT: UEBERSICHT_TEXT, ADAC_URL: VORSCHAU * 3})
    check("3a) nachgeladener Text OHNE passenden Rollenbeleg: keine Bestätigung (ein Abruf, kein weiterer)",
          fake.extract_calls == [[UEBERSICHT], [ADAC_URL]] and r.vehicle_identity.get("generation") is None,
          f"{fake.extract_calls} / {r.vehicle_identity.get('generation')!r}")

    falsch = ADAC_VOLLTEXT.replace("UXC1", "USC10")
    fake, r, lg = await lauf([ADAC_HIT], {UEBERSICHT: UEBERSICHT_TEXT, ADAC_URL: falsch})
    check("3b) nachgeladener Text mit ANDERER Kennung (USC10): UXC1 wird nicht bestätigt",
          r.vehicle_identity.get("generation") != "UXC1", str(r.vehicle_identity.get("generation")))

    schon_voll = dict(ADAC_HIT, raw_content=VORSCHAU * 20)   # Volltext schon da, aber ohne Baureihenzeile
    fake, r, lg = await lauf([schon_voll], {UEBERSICHT: UEBERSICHT_TEXT})
    check("3c) ohne geeigneten Katalogtreffer (Volltext schon vorhanden): KEIN zusätzlicher Abruf, Generation offen",
          fake.extract_calls == [[UEBERSICHT]] and r.vehicle_identity.get("generation") is None
          and any("grund=kein_geeigneter_treffer" in z and "ausgang=kein_abruf" in z for z in lg),
          f"{fake.extract_calls} / {lg}")

    fake, r, lg = await lauf([ADAC_HIT], {UEBERSICHT: "Seite ohne Links."})
    check("3d) ohne erkannte Hersteller-Kennung (kein PDF): kein zusätzlicher Abruf",
          fake.extract_calls == [[UEBERSICHT]] and r.vehicle_identity.get("generation") is None
          and any("grund=keine_dokumentkennung" in z for z in lg), f"{fake.extract_calls} / {lg}")

    fake, r, lg = await lauf([_ADAC], {UEBERSICHT: UEBERSICHT_TEXT})
    check("3e) Rollenbeleg schon im Suchauszug: kein zusätzlicher Abruf, UXC1 bestätigt",
          fake.extract_calls == [[UEBERSICHT]] and r.vehicle_identity.get("generation") == "UXC1"
          and lg == [], f"{fake.extract_calls} / {lg}")

    zweiter = treffer("https://www.auto-data.net/de/lexus-rc-f-xc10", "Lexus RC F | auto-data.net",
                      "Lexus RC F Technische Daten Leistung Verbrauch")
    fake, r, lg = await lauf([ADAC_HIT, zweiter], {UEBERSICHT: UEBERSICHT_TEXT, ADAC_URL: ADAC_VOLLTEXT})
    check("3f) mehrere geeignete Treffer: höchstens EIN zusätzlicher Abruf",
          len(fake.extract_calls) == 2 and len(fake.extract_calls[1]) == 1, str(fake.extract_calls))

    if _FEHLER:
        print(f"\n{len(_FEHLER)} FEHLER:")
        for f in _FEHLER:
            print("  -", f)
        raise SystemExit(1)
    print("\nALLE N1-ROLLENBELEG-NACHLADUNG-TESTS GRUEN")


asyncio.run(main())
