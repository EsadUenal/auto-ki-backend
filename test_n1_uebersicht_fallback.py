"""
N1 -- Herstellerübersicht -> verlinkte Rettungskarten-PDF (Fallback).

Replay mit dem ECHTEN Abruftext der Lexus-Übersicht (ein realer Tavily-Extract
vom 08.11.2026, tests/fixtures/lexus_rettungsdatenblaetter_extract_2026-11-08.txt)
und dem realen Rettungskarten-Text (UXC1 (EU, M), ab 09/2014). Echter
run_kaufcheck()-Pfad; Suche/Extract/DB-Lookup/HEAD gestubbt; Netz + DB über
scripts/test_isolated.py isoliert.

    python scripts/test_isolated.py test_n1_uebersicht_fallback.py
"""
import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx
import app.technical_research as tr
from app.models import KaufCheckRequest
from test_n1_generation_spec_gap_run_kaufcheck import (  # noqa: E402
    _ADAC, _AMS, _RETTUNGSKARTE_VOLLTEXT, _FakeSearchExtract, _lauf, treffer,
)

_FEHLER: list[str] = []


def check(name, bedingung, detail=""):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


UEBERSICHT = "https://www.lexus.de/lexus-besitzer/fahrzeuginformationen/rettungsdatenblaetter"
ANDERE_UEBERSICHT = "https://www.lexus.de/lexus-besitzer/fahrzeuginformationen"
PDF = ("https://scene7.toyota.eu/is/content/toyotaeurope/lexus/nmsc/germany/"
       "lexus-besitzer/fahrzeuginformationen/RCF_tcm-3150-1284184.pdf")
UEBERSICHT_TEXT = (Path(__file__).parent / "tests" / "fixtures"
                   / "lexus_rettungsdatenblaetter_extract_2026-11-08.txt").read_text(encoding="utf-8")
FREMDE_MODELLE = ("CT200h", "ES300h", "LC500", "LBX", "NX450h", "IS_300h")

# ── Einzelfunktionen ─────────────────────────────────────────────────────────
print("--- Linkextraktion aus dem echten Abruftext ---")
links = tr._uebersicht_pdf_links(UEBERSICHT_TEXT, uebersicht_url=UEBERSICHT, marke="Lexus", modell="RC F")
check("L1) genau die RC-F-PDF wird gefunden (Beschriftung + Dateiname)",
      [u for u, _ in links] == [PDF], str(links))
check("L2) Teilstring zählt nicht: Modell 'RC' wird nicht über 'RCF' im Dateinamen gefunden, "
      "Modell 'X' findet nichts",
      tr._uebersicht_pdf_links("[Datei](https://scene7.toyota.eu/is/content/toyotaeurope/lexus/a/RCF_1.pdf)",
                               uebersicht_url=UEBERSICHT, marke="Lexus", modell="RC") == []
      and tr._uebersicht_pdf_links(UEBERSICHT_TEXT, uebersicht_url=UEBERSICHT, marke="Lexus", modell="X") == [])
check("L3) fremde Marke: keine Links (Zielhost gehört nicht zu Toyota/Audi-Pfad)",
      tr._uebersicht_pdf_links(UEBERSICHT_TEXT, uebersicht_url=UEBERSICHT, marke="Audi", modell="RC F") == [])
check("L4) Link auf unlisteten fremden Host wird nicht übernommen",
      tr._uebersicht_pdf_links("[Lexus RC F](https://evil.example/lexus/RCF.pdf)",
                               uebersicht_url=UEBERSICHT, marke="Lexus", modell="RC F") == [])
check("L5) CDN-Host ohne Markennamen im Pfad wird nicht übernommen",
      tr._uebersicht_pdf_links("[Lexus RC F](https://scene7.toyota.eu/is/content/toyotaeurope/toyota/RCF.pdf)",
                               uebersicht_url=UEBERSICHT, marke="Lexus", modell="RC F") == [])
check("L6) http (unverschlüsselt) wird abgelehnt",
      not tr._link_ziel_vertrauenswuerdig(PDF.replace("https", "http"), "Lexus"))
check("K1) Übersichtskandidat: die spezifischste Seite zuerst, PDFs nie",
      tr._uebersicht_kandidaten([{"url": ANDERE_UEBERSICHT}, {"url": UEBERSICHT}, {"url": PDF},
                                 {"url": "https://www.carwiki.de/rettungskarte"}], marke="Lexus")
      == [UEBERSICHT, ANDERE_UEBERSICHT])


# ── Eigener PDF-Download (MockTransport statt Netz; jede Anfrage protokolliert) ──
PDF_BYTES = (Path(__file__).parent / "tests" / "fixtures"
             / "lexus_rcf_rettungskarte_2026-11-08.pdf").read_bytes()
ANFRAGEN: list[str] = []


def mini_pdf(text: str) -> bytes:
    """Kleine, gültige Ein-Seiten-PDF mit unkomprimiertem Text (für Gegenfälle)."""
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
    """antworten: {url: (status, headers, body)}; unbekannte URL -> Fehler (kein Netz)."""
    def handler(request):
        ANFRAGEN.append(str(request.url))
        status, headers, body = antworten[str(request.url)]
        return httpx.Response(status, headers=headers, content=body)
    return httpx.MockTransport(handler)


async def download_tests():
    tr._PDF_DOWNLOAD_TRANSPORT = transport({PDF: (200, {}, PDF_BYTES)})
    erg = await tr._lade_pdf_bytes(PDF, "Lexus")
    check("D1) echte PDF-Bytes werden geladen, finale URL = geprüfte URL",
          erg is not None and erg[0] == PDF_BYTES and erg[1] == PDF, "")
    text = tr._pdf_text(PDF_BYTES)
    check("D2) pypdf liest aus GENAU diesen Bytes Modell, Kennung und Zeitraum im selben Satz",
          "Lexus RC F" in text and "UXC1 (EU, M), ab 09/2014" in text, repr(text[:160]))

    ANFRAGEN.clear()
    tr._PDF_DOWNLOAD_TRANSPORT = transport({PDF: (302, {"location": "https://evil.example/RCF.pdf"}, b"")})
    check("D3) Redirect auf fremden Host -> None", await tr._lade_pdf_bytes(PDF, "Lexus") is None)
    check("D4) das fremde Redirect-Ziel wurde NIE angefragt", ANFRAGEN == [PDF], str(ANFRAGEN))

    ANFRAGEN.clear()
    inner = PDF.rsplit("/", 1)[0] + "/RCF_neu.pdf"
    tr._PDF_DOWNLOAD_TRANSPORT = transport({PDF: (302, {"location": inner}, b""), inner: (200, {}, PDF_BYTES)})
    erg = await tr._lade_pdf_bytes(PDF, "Lexus")
    check("D5) Redirect innerhalb des vertrauenswürdigen Hosts wird nach erneuter Prüfung gefolgt",
          erg is not None and erg[1] == inner and ANFRAGEN == [PDF, inner], str(ANFRAGEN))

    ANFRAGEN.clear()
    tr._PDF_DOWNLOAD_TRANSPORT = transport({PDF: (302, {"location": PDF.rsplit("/", 1)[0] + "/seite.html"}, b"")})
    check("D6) Redirect auf Nicht-PDF-Pfad (Pfadregel) -> None, Ziel nie angefragt",
          await tr._lade_pdf_bytes(PDF, "Lexus") is None and ANFRAGEN == [PDF], str(ANFRAGEN))

    tr._PDF_DOWNLOAD_TRANSPORT = transport({PDF: (302, {"location": "http://scene7.toyota.eu/is/content/toyotaeurope/lexus/a.pdf"}, b"")})
    check("D7) Redirect auf http -> None", await tr._lade_pdf_bytes(PDF, "Lexus") is None)

    tr._PDF_DOWNLOAD_TRANSPORT = transport({PDF: (302, {"location": PDF}, b"")})
    check("D8) Redirect-Schleife -> None (begrenzt)", await tr._lade_pdf_bytes(PDF, "Lexus") is None)

    tr._PDF_DOWNLOAD_TRANSPORT = transport({PDF: (404, {}, b"")})
    check("D9) 404 -> None", await tr._lade_pdf_bytes(PDF, "Lexus") is None)

    tr._PDF_DOWNLOAD_TRANSPORT = transport({PDF: (200, {}, b"<html>keine pdf</html>")})
    check("D10) Antwort ohne %PDF-Kopf -> None", await tr._lade_pdf_bytes(PDF, "Lexus") is None)

    tr._PDF_DOWNLOAD_TRANSPORT = transport({PDF: (200, {"content-length": str(tr._PDF_MAX_BYTES + 1)}, b"%PDF-x")})
    check("D11) deklarierte Größe über dem Limit -> None", await tr._lade_pdf_bytes(PDF, "Lexus") is None)

    tr._PDF_DOWNLOAD_TRANSPORT = transport({PDF: (200, {}, b"%PDF-" + b"x" * (tr._PDF_MAX_BYTES + 10))})
    check("D12) tatsächliche Größe über dem Limit (ohne Content-Length) -> None",
          await tr._lade_pdf_bytes(PDF, "Lexus") is None)

    check("D13a) Hilfs-PDF liest pypdf korrekt (Gegenfall-Werkzeug ist gültig)",
          "Lexus LC 500" in tr._pdf_text(mini_pdf("Lexus LC 500 (Z100)")))
    check("D13) kaputte PDF-Bytes -> leerer Text", tr._pdf_text(b"%PDF-1.4 kaputt") == "")
    tr._PDF_DOWNLOAD_TRANSPORT = None


# ── Vollpfad ─────────────────────────────────────────────────────────────────
def routing(pdf_text_vorhanden=True, mit_adac=True):
    return {
        "technische Daten": [_ADAC] if mit_adac else [],
        "Generation Baureihe Bauzeitraum": [_AMS],
        "Rettungskarte Rettungsdatenblatt": [
            treffer(UEBERSICHT, "Rettungsdatenblätter | Lexus", "Alle Rettungsdatenblätter auf einen Blick."),
            treffer(ANDERE_UEBERSICHT, "Fahrzeuginformationen | Lexus", "Fahrzeuginformationen.")],
    }


REQ = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000, motor="5.0 V8",
                       kraftstoff="Benzin", leistung_ps=477, getriebe="Automatik", antrieb="Heck",
                       ausstattung=[])


async def lauf(extract, mit_adac=True, pdf=None):
    """extract: nur die ÜBERSICHT (Tavily-Stub). pdf: {url: (status, headers, body)} für den Download."""
    ANFRAGEN.clear()
    tr._PDF_DOWNLOAD_TRANSPORT = transport(pdf if pdf is not None else {PDF: (200, {}, PDF_BYTES)})
    fake = _FakeSearchExtract(routing=routing(mit_adac=mit_adac), extract_text=extract)
    try:
        r = await _lauf(REQ, fake)
    finally:
        tr._PDF_DOWNLOAD_TRANSPORT = None
    return fake, r


def titel(r):
    k = r.kaufaktionen
    return sorted(str(a) for p in (k.verkaeuferfragen, k.dokumente) for a in p.fahrzeugspezifisch + p.basis)


async def main():
    await download_tests()

    print("\n--- Vollpfad: Übersicht -> echte PDF-Bytes (pypdf) -> Rollenbeleg -> UXC1 ---")
    fake, r = await lauf({UEBERSICHT: UEBERSICHT_TEXT})
    check("P1) Tavily nur für die Übersicht (1 Abruf), die PDF kam per eigenem Download genau einmal",
          fake.extract_calls == [[UEBERSICHT]] and ANFRAGEN == [PDF],
          f"{fake.extract_calls} / {ANFRAGEN}")
    check("P2) Generation im Bericht = UXC1 (Text aus den echten PDF-Bytes)",
          r.vehicle_identity.get("generation") == "UXC1", str(r.vehicle_identity.get("generation")))
    hinweis = r.bericht[r.bericht.find("Blockierende Identitätslücke"):][:120]
    check("P3) Generationssperre weg, Motorisierungssperre bleibt (N6 unberührt)",
          "Generation" not in hinweis and "Motorisierung" in hinweis, hinweis)
    print(f"   sichtbar: generation={r.vehicle_identity.get('generation')!r} state={r.recommendation_state!r}")
    text = r.bericht + " ".join(titel(r))
    check("P4) keine anderen Modelle der Übersicht tauchen in Bericht/Fragen/Dokumenten auf",
          not any(m.lower() in text.lower() for m in FREMDE_MODELLE))
    k = r.kaufaktionen
    check("P5) Fragen und Dokumente werden erzeugt",
          bool(k.verkaeuferfragen.basis) and bool(k.dokumente.basis),
          f"fragen={len(k.verkaeuferfragen.basis)} dokumente={len(k.dokumente.basis)}")
    _, r0 = await lauf({})
    check("P6) Fragen/Dokumente: gleiche Anzahl Basisfragen/-dokumente wie ohne Fallback",
          len(r0.kaufaktionen.verkaeuferfragen.basis) == len(k.verkaeuferfragen.basis)
          and len(r0.kaufaktionen.dokumente.basis) == len(k.dokumente.basis))

    print("\n--- Gegenfälle ---")
    fake, r = await lauf({UEBERSICHT: UEBERSICHT_TEXT.replace("scene7.toyota.eu", "evil.example")})
    check("G1) fremder Zielhost im Link: kein Download, Generation unbestätigt",
          ANFRAGEN == [] and r.vehicle_identity.get("generation") is None,
          f"{ANFRAGEN} / {r.vehicle_identity.get('generation')!r}")

    fake, r = await lauf({UEBERSICHT: UEBERSICHT_TEXT},
                         pdf={PDF: (302, {"location": "https://evil.example/RCF.pdf"}, b"")})
    check("G2) Redirect auf fremden Host: Ziel NIE angefragt, Generation unbestätigt",
          ANFRAGEN == [PDF] and r.vehicle_identity.get("generation") is None,
          f"{ANFRAGEN} / {r.vehicle_identity.get('generation')!r}")

    fake, r = await lauf({UEBERSICHT: UEBERSICHT_TEXT}, pdf={PDF: (200, {}, b"<html>keine pdf</html>")})
    check("G3) ungültige PDF (kein %PDF-Kopf): keine Generation", r.vehicle_identity.get("generation") is None)

    fake, r = await lauf({UEBERSICHT: UEBERSICHT_TEXT},
                         pdf={PDF: (200, {"content-length": str(tr._PDF_MAX_BYTES + 1)}, b"%PDF-x")})
    check("G4) zu große PDF: keine Generation", r.vehicle_identity.get("generation") is None)

    fake, r = await lauf({UEBERSICHT: UEBERSICHT_TEXT}, pdf={PDF: (200, {}, b"%PDF-1.4 kaputt")})
    check("G5) PDF mit unlesbarem Inhalt: keine Generation", r.vehicle_identity.get("generation") is None)

    fake, r = await lauf({UEBERSICHT: UEBERSICHT_TEXT}, mit_adac=False)
    check("G6) echte PDF, aber ohne unabhängigen Rollenbeleg: keine Generation",
          r.vehicle_identity.get("generation") is None, str(r.vehicle_identity.get("generation")))

    fake, r = await lauf({UEBERSICHT: "Seite ohne Links."})
    check("G7) Übersicht ohne passenden Link: kein Download, keine Generation",
          ANFRAGEN == [] and r.vehicle_identity.get("generation") is None)

    fake, r = await lauf({UEBERSICHT: UEBERSICHT_TEXT.replace("RC F - AB 09/2014", "Sonstiges")
                          .replace("RCF_tcm-3150-1284184", "Datei_1284184")})
    check("G8) Link ohne Modellbezug (Beschriftung/Dateiname): kein Download", ANFRAGEN == [], str(ANFRAGEN))

    fake, r = await lauf({UEBERSICHT: UEBERSICHT_TEXT}, pdf={PDF: (200, {}, mini_pdf(
        "Lexus LC 500 (Z100 (EU, M), ab 03/2017) Stand: 02/2017 12V Spannungsversorgung trennen"))})
    check("G9) gültige PDF eines anderen Modells (LC 500): keine Generation",
          r.vehicle_identity.get("generation") is None, str(r.vehicle_identity.get("generation")))

    if _FEHLER:
        print(f"\n{len(_FEHLER)} FEHLER:")
        for f in _FEHLER:
            print("  -", f)
        raise SystemExit(1)
    print("\nALLE N1-UEBERSICHT-FALLBACK-TESTS GRUEN")


asyncio.run(main())
