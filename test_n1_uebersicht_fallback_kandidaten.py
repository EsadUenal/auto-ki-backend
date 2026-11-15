"""
N1/N16 -- Übersichts-Fallback prüft weitere zulässige Übersichtskandidaten (Budget im Fallback +1, höchstens
3 Abrufe), mit Diagnose je geprüfter Übersicht.

Material: ECHTER Tavily-Extract der Lexus-Rettungsdatenblatt-Übersicht (08.11.2026,
tests/fixtures/lexus_rettungsdatenblaetter_extract_2026-11-08.txt), ECHTE RC-F-Rettungskarten-PDF-Bytes
(tests/fixtures/lexus_rcf_rettungskarte_2026-11-08.pdf), ADAC-/AMS-Texte aus
test_n1_generation_spec_gap_run_kaufcheck.py. [SYNTH] = synthetische Gegenprobe (Seite ohne Links, Fremdmodell-PDF,
neutrale Toyota-GT86-Mechanik). UXC1 ist Testmaterial, kein Produkt-Abschlusskriterium.

Suche/Extract/DB-Lookup gestubbt, PDF-Download über httpx.MockTransport; Netz + Temp-DB über
scripts/test_isolated.py vor allen App-Importen.

    python scripts/test_isolated.py test_n1_uebersicht_fallback_kandidaten.py
"""
import asyncio
import logging
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402
import app.technical_research as tr  # noqa: E402
from app.models import KaufCheckRequest  # noqa: E402
from test_n1_generation_spec_gap_run_kaufcheck import (  # noqa: E402
    _ADAC, _AMS, _FakeSearchExtract, _lauf, treffer,
)

_FEHLER: list[str] = []


def check(name, bedingung, detail=""):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + ("" if bedingung else f" -- {detail}"))
    if not bedingung:
        _FEHLER.append(name)


FIX = Path(__file__).parent / "tests" / "fixtures"
U1 = "https://www.lexus.de/lexus-besitzer/fahrzeuginformationen/rettungsdatenblaetter"
U2 = "https://www.lexus.de/lexus-besitzer/fahrzeuginformationen/rettungskarten-uebersicht"
U3 = "https://www.lexus.de/lexus-besitzer/fahrzeuginformationen"
PDF = ("https://scene7.toyota.eu/is/content/toyotaeurope/lexus/nmsc/germany/"
       "lexus-besitzer/fahrzeuginformationen/RCF_tcm-3150-1284184.pdf")
ECHT = (FIX / "lexus_rettungsdatenblaetter_extract_2026-11-08.txt").read_text(encoding="utf-8")
PDF_BYTES = (FIX / "lexus_rcf_rettungskarte_2026-11-08.pdf").read_bytes()
OHNE_LINK = "Rettungsdatenblätter. Diese Seite enthält derzeit keine Downloads."     # [SYNTH]
ANFRAGEN: list[str] = []


def mini_pdf(text: str) -> bytes:
    """Kleine, gültige Ein-Seiten-PDF mit unkomprimiertem Text (Gegenfälle) -- wie in test_n1_uebersicht_fallback."""
    inhalt = f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(inhalt) + inhalt + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def transport(antworten):
    def handler(request):
        ANFRAGEN.append(str(request.url))
        status, headers, body = antworten[str(request.url)]
        return httpx.Response(status, headers=headers, content=body)
    return httpx.MockTransport(handler)


class Logs(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.zeilen: list[str] = []

    def emit(self, rec):
        m = rec.getMessage()
        if "identitaet_uebersicht_fallback" in m:
            self.zeilen.append(m)


def ende_von(zeilen):
    for z in reversed(zeilen):
        m = re.search(r"ende=(\w+) abrufe=(\d+)", z)
        if m:
            return m.group(1), int(m.group(2))
    return None, None


REQ = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000, motor="5.0 V8",
                       kraftstoff="Benzin", leistung_ps=477, getriebe="Automatik", antrieb="Heck", ausstattung=[])


async def vollpfad(uebersichten: list[tuple[str, str]], extract: dict[str, str], pdf=None):
    """uebersichten: (url, titel) der Suchtreffer in Suchreihenfolge; extract: Abruftexte je Übersicht."""
    ANFRAGEN.clear()
    tr._PDF_DOWNLOAD_TRANSPORT = transport(pdf if pdf is not None else {PDF: (200, {}, PDF_BYTES)})
    routing = {"technische Daten": [_ADAC], "Generation Baureihe Bauzeitraum": [_AMS],
               "Rettungskarte Rettungsdatenblatt": [treffer(u, t, "Lexus Fahrzeuginformationen.")
                                                   for u, t in uebersichten]}
    fake = _FakeSearchExtract(routing=routing, extract_text=extract)
    logs = Logs()
    lg = logging.getLogger("app.technical_research")
    alt = lg.level
    lg.addHandler(logs)
    lg.setLevel(logging.INFO)
    try:
        r = await _lauf(REQ, fake)
    finally:
        lg.removeHandler(logs)
        lg.setLevel(alt)
        tr._PDF_DOWNLOAD_TRANSPORT = None
    return fake, r, logs.zeilen


async def direkt(trefferliste, extract, *, marke="Lexus", modell="RC F", budget=2, pdf=None):
    """Direkter Aufruf des Fallbacks (gleiche Stubs), für Budget-/Duplikatfälle."""
    ANFRAGEN.clear()
    tr._PDF_DOWNLOAD_TRANSPORT = transport(pdf or {})
    fake = _FakeSearchExtract(routing={}, extract_text=extract)
    alt_hole = tr.hole_raw_content
    tr.hole_raw_content = fake.extract
    logs = Logs()
    lg = logging.getLogger("app.technical_research")
    alt = lg.level
    lg.addHandler(logs)
    lg.setLevel(logging.INFO)
    try:
        erg = await tr._uebersicht_fallback(trefferliste, marke=marke, modell=modell, budget=budget)
    finally:
        tr.hole_raw_content = alt_hole
        lg.removeHandler(logs)
        lg.setLevel(alt)
        tr._PDF_DOWNLOAD_TRANSPORT = None
    return erg, fake, logs.zeilen


def gen(r):
    return r.vehicle_identity.get("generation")


def sperre(r):
    return r.bericht[r.bericht.find("Blockierende Identitätslücke"):][:160]


async def main():
    print("--- Vorbedingung: Reihenfolge der Kandidaten (unveränderte Regel) ---")
    check("V1 Kandidatenreihenfolge: Rettungs-Pfade vor allgemeiner Seite, spezifischere zuerst",
          tr._uebersicht_kandidaten([{"url": U3}, {"url": U2}, {"url": U1}], marke="Lexus")[0] in (U1, U2)
          and tr._uebersicht_kandidaten([{"url": U3}, {"url": U1}], marke="Lexus") == [U1, U3])

    print("\n--- A) erster Kandidat ohne Link, zweiter mit gültigem Dokument (Vollpfad bis zur Ausgabe) ---")
    fake, r, z = await vollpfad([(U1, "Rettungsdatenblätter | Lexus"), (U3, "Fahrzeuginformationen | Lexus")],
                                {U1: OHNE_LINK, U3: ECHT})
    check("A1 zwei Übersichten in Kandidatenreihenfolge abgerufen, je einmal",
          fake.extract_calls == [[U1], [U3]], str(fake.extract_calls))
    check("A2 genau ein PDF-Download (die verlinkte RC-F-Rettungskarte)", ANFRAGEN == [PDF], str(ANFRAGEN))
    check("A3 bestehende Bestätigungskette: Generation in der sichtbaren Identität", gen(r) == "UXC1", str(gen(r)))
    check("A4 Bericht: Generation ist keine blockierende Identitätslücke mehr", "Generation" not in sperre(r),
          sperre(r))
    ende, abrufe = ende_von(z)
    check("A5 Diagnose: ende=erfolg, abrufe=3 = tatsächliche Abrufe (2 Übersichten + 1 PDF)",
          ende == "erfolg" and abrufe == 3 == len(fake.extract_calls) + len(ANFRAGEN), f"{ende} {abrufe}")
    pos = [x for x in z if "position=" in x and "pdf_links_passend" in x]
    check("A6 Diagnose je Übersicht: Position, Host, Pfad ohne Query, Abrufstatus, Textlänge, Linkzahl",
          len(pos) == 2 and "position=1/2" in pos[0] and "host=lexus.de" in pos[0]
          and "pfad=/lexus-besitzer/fahrzeuginformationen/rettungsdatenblaetter" in pos[0]
          and "pdf_links_passend=0" in pos[0] and "pdf_links_passend=1" in pos[1]
          and f"textlaenge={len(ECHT)}" in pos[1], str(pos))
    check("A7 Diagnose enthält keine Seitentexte", not any("Rettungsleitfaden" in x or "Hauptinhalt" in x for x in z))

    print("\n--- B) erster Kandidat erfolgreich: kein weiterer Abruf ---")
    fake, r, z = await vollpfad([(U1, "Rettungsdatenblätter | Lexus"), (U3, "Fahrzeuginformationen | Lexus")],
                                {U1: ECHT, U3: ECHT})
    check("B1 nur die erste Übersicht abgerufen, ein PDF", fake.extract_calls == [[U1]] and ANFRAGEN == [PDF],
          f"{fake.extract_calls} {ANFRAGEN}")
    check("B2 Generation bestätigt, Diagnose ende=erfolg abrufe=2", gen(r) == "UXC1" and ende_von(z) == ("erfolg", 2),
          f"{gen(r)} {ende_von(z)}")

    print("\n--- C) nur ein Kandidat: bisheriges Verhalten ---")
    fake, r, z = await vollpfad([(U1, "Rettungsdatenblätter | Lexus")], {U1: OHNE_LINK})
    check("C1 eine Übersicht, kein Download, Generation UNKNOWN",
          fake.extract_calls == [[U1]] and ANFRAGEN == [] and gen(r) is None, f"{fake.extract_calls} {ANFRAGEN}")
    check("C2 Diagnose: ende=kandidaten_erschoepft abrufe=1", ende_von(z) == ("kandidaten_erschoepft", 1), str(z))
    fake, r, z = await vollpfad([(U1, "Rettungsdatenblätter | Lexus")], {U1: ECHT})
    check("C3 ein Kandidat mit Dokument: wie bisher bestätigt (1 Übersicht + 1 PDF)",
          fake.extract_calls == [[U1]] and ANFRAGEN == [PDF] and gen(r) == "UXC1")

    print("\n--- D) Budget: nie mehr als 3 Abrufe, kein Überschreiten ---")
    t3 = [{"url": U1}, {"url": U2}, {"url": U3}]
    k1, k2, k3 = tr._uebersicht_kandidaten(t3, marke="Lexus")     # unveränderte Reihenfolge der bestehenden Regel
    erg, fake, z = await direkt(t3, {k1: OHNE_LINK, k2: OHNE_LINK, k3: ECHT}, pdf={PDF: (200, {}, PDF_BYTES)})
    check("D1 zwei Übersichten ohne Link: dritte NICHT abgerufen (kein Platz mehr für Übersicht + PDF)",
          fake.extract_calls == [[k1], [k2]] and ANFRAGEN == [] and erg == [], str(fake.extract_calls))
    check("D2 Diagnose: ende=budget_ende abrufe=2", ende_von(z) == ("budget_ende", 2), str(ende_von(z)))
    erg, fake, z = await direkt(t3, {k1: ECHT, k2: ECHT, k3: ECHT}, pdf={PDF: (404, {}, b"")})
    check("D3 Link gefunden, PDF scheitert: keine weitere Übersicht (Budget), ein PDF-Versuch",
          fake.extract_calls == [[k1]] and ANFRAGEN == [PDF] and erg == [] and ende_von(z) == ("budget_ende", 2),
          f"{fake.extract_calls} {ANFRAGEN} {ende_von(z)}")
    erg, fake, z = await direkt(t3, {k1: ECHT}, budget=1, pdf={PDF: (200, {}, PDF_BYTES)})
    check("D4 Restbudget des Aufrufers < 2: kein Abruf (unverändert), ende=budget_zu_klein",
          fake.extract_calls == [] and ANFRAGEN == [] and erg == [] and ende_von(z)[0] == "budget_zu_klein")
    erg, fake, z = await direkt(t3, {k1: OHNE_LINK, k2: ECHT}, pdf={PDF: (200, {}, PDF_BYTES)})
    check("D5 maximale Ausschöpfung: Übersicht 1 + Übersicht 2 + PDF = 3 Abrufe, Treffer geliefert",
          fake.extract_calls == [[k1], [k2]] and ANFRAGEN == [PDF] and len(erg) == 1
          and ende_von(z) == ("erfolg", 3), f"{fake.extract_calls} {ANFRAGEN} {ende_von(z)}")
    check("D6 der Treffer trägt nur die Herkunft (verlinkt_von_herstellerseite = zweite Übersicht)",
          erg and erg[0].get("verlinkt_von_herstellerseite") == k2 and erg[0]["url"] == PDF)

    print("\n--- E) kein geeignetes Dokument / ungeeigneter Link / unpassende Kennung ---")
    fake, r, z = await vollpfad([(U1, "Rettungsdatenblätter | Lexus"), (U3, "Fahrzeuginformationen | Lexus")],
                                {U1: OHNE_LINK, U3: OHNE_LINK})
    check("E1 keine Übersicht mit Link: Generation bleibt UNKNOWN", gen(r) is None and ANFRAGEN == [])
    fake, r, z = await vollpfad([(U1, "Rettungsdatenblätter | Lexus"), (U3, "Fahrzeuginformationen | Lexus")],
                                {U1: OHNE_LINK, U3: ECHT.replace("scene7.toyota.eu", "evil.example")})
    check("E2 zweite Übersicht verlinkt nur auf fremden Host: kein Download, UNKNOWN",
          ANFRAGEN == [] and gen(r) is None, f"{ANFRAGEN} {gen(r)}")
    fake, r, z = await vollpfad([(U1, "Rettungsdatenblätter | Lexus"), (U3, "Fahrzeuginformationen | Lexus")],
                                {U1: OHNE_LINK, U3: ECHT},
                                pdf={PDF: (200, {}, mini_pdf("Lexus LC 500 (Z100 (EU, M), ab 03/2017) Stand: 02/2017"))})
    check("E3 [SYNTH] verlinkte PDF trägt eine fremde Modellkennung: keine Bestätigung", gen(r) is None, str(gen(r)))
    fake, r, z = await vollpfad([(U1, "Rettungsdatenblätter | Lexus"), (U3, "Fahrzeuginformationen | Lexus")],
                                {U1: OHNE_LINK, U3: ECHT.replace("RC F - AB 09/2014", "Sonstiges")
                                 .replace("RCF_tcm-3150-1284184", "Datei_1284184")})
    check("E4 Link ohne Modellbezug in der zweiten Übersicht: kein Download, UNKNOWN",
          ANFRAGEN == [] and gen(r) is None)

    print("\n--- F) doppelte Übersichts-URL ---")
    varianten = [U1, U1 + "?utm_source=x", U1 + "/", U1 + "#a"]
    erg, fake, z = await direkt([{"url": v} for v in varianten], {v: OHNE_LINK for v in varianten})
    check("F1 Query/Fragment/Schrägstrich-Varianten derselben Übersicht: genau EIN Abruf",
          len(fake.extract_calls) == 1 and len(fake.extract_calls[0]) == 1
          and fake.extract_calls[0][0] in varianten, str(fake.extract_calls))
    check("F2 Diagnose zählt einen Kandidaten", any("kandidaten=1 " in x for x in z), str(z[:1]))
    erg, fake, z = await direkt([{"url": U1}, {"url": U2}], {U1: ECHT, U2: ECHT}, pdf={PDF: (404, {}, b"")})
    check("F3 dieselbe PDF wird nicht zweimal angefragt", ANFRAGEN.count(PDF) <= 1, str(ANFRAGEN))

    print("\n--- G) [SYNTH] neutrale Gegenprobe: andere Marke, gleiche Mechanik ---")
    T1 = "https://www.toyota.de/toyota-besitzer/fahrzeuginformationen/rettungskarten"
    T2 = "https://www.toyota.de/toyota-besitzer/fahrzeuginformationen"
    # Pfad wie bei der echten toyota.de-Rettungskarte (Pfadwort "fahrzeuginformationen", s. _pfad_grund)
    TPDF = ("https://scene7.toyota.eu/is/content/toyotaeurope/toyota/nmsc/germany/toyota-besitzer/"
            "fahrzeuginformationen/RLF_GT86_ZN_tcm-1.pdf")
    erg, fake, z = await direkt([{"url": T2}, {"url": T1}],
                                {T1: "Rettungskarten. Keine Downloads.", T2: f"[Toyota GT86 - ab 2012 .pdf]({TPDF})"},
                                marke="Toyota", modell="GT86",
                                pdf={TPDF: (200, {}, mini_pdf("Toyota GT86 (ZN) Stand: 01/2013"))})
    check("G1 Toyota GT86: erste Übersicht ohne Link, zweite liefert die GT86-PDF (keine Lexus-/Kennungsregel)",
          fake.extract_calls == [[T1], [T2]] and ANFRAGEN == [TPDF] and len(erg) == 1
          and "GT86" in erg[0]["content"], f"{fake.extract_calls} {ANFRAGEN}")
    erg, fake, z = await direkt([{"url": T2}, {"url": T1}],
                                {T1: "Rettungskarten.", T2: f"[Toyota Supra .pdf]({TPDF.replace('GT86', 'SUPRA')})"},
                                marke="Toyota", modell="GT86", pdf={})
    check("G2 Toyota: zweite Übersicht verlinkt nur ein anderes Modell: kein Download, kein Treffer",
          ANFRAGEN == [] and erg == [], str(ANFRAGEN))

    if _FEHLER:
        print(f"\n{len(_FEHLER)} FEHLER:")
        for f in _FEHLER:
            print("  -", f)
        raise SystemExit(1)
    print("\nALLE N1/N16-KANDIDATEN-FALLBACK-TESTS GRUEN")


asyncio.run(main())
