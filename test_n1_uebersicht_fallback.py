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


# ── Redirect-Prüfung (HEAD gestubbt) ─────────────────────────────────────────
class _Resp:
    def __init__(self, status, loc=None):
        self.status_code, self.headers = status, ({"location": loc} if loc else {})


def head_stub(kette):
    async def head(self, url, *a, **k):
        return kette.pop(0)
    return head


async def redirect_tests():
    orig = httpx.AsyncClient.head
    try:
        httpx.AsyncClient.head = head_stub([_Resp(200)])
        check("R1) direkt 200 -> vertrauenswürdig", await tr._pruefe_redirects(PDF, "Lexus"))
        httpx.AsyncClient.head = head_stub([_Resp(302, "https://evil.example/x.pdf")])
        check("R2) Redirect auf fremden Host -> abgelehnt", not await tr._pruefe_redirects(PDF, "Lexus"))
        httpx.AsyncClient.head = head_stub([_Resp(302, "/is/content/toyotaeurope/lexus/a.pdf"), _Resp(200)])
        check("R3) Redirect innerhalb des vertrauenswürdigen Hosts -> ok", await tr._pruefe_redirects(PDF, "Lexus"))
        httpx.AsyncClient.head = head_stub([_Resp(404)])
        check("R4) 404 -> abgelehnt", not await tr._pruefe_redirects(PDF, "Lexus"))
        httpx.AsyncClient.head = head_stub([_Resp(302, "/a")] * 6)
        check("R5) Redirect-Schleife -> abgelehnt", not await tr._pruefe_redirects(PDF, "Lexus"))
    finally:
        httpx.AsyncClient.head = orig


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


async def lauf(extract, mit_adac=True, head=None):
    fake = _FakeSearchExtract(routing=routing(mit_adac=mit_adac), extract_text=extract)
    orig = httpx.AsyncClient.head
    httpx.AsyncClient.head = head_stub(list(head if head is not None else [_Resp(200)]))
    try:
        r = await _lauf(REQ, fake)
    finally:
        httpx.AsyncClient.head = orig
    return fake, r


def titel(r):
    k = r.kaufaktionen
    return sorted(str(a) for p in (k.verkaeuferfragen, k.dokumente) for a in p.fahrzeugspezifisch + p.basis)


async def main():
    await redirect_tests()

    print("\n--- Vollpfad: Übersicht -> aktuelle PDF -> Rollenbeleg -> UXC1 ---")
    fake, r = await lauf({UEBERSICHT: UEBERSICHT_TEXT, PDF: _RETTUNGSKARTE_VOLLTEXT})
    check("P1) genau zwei Abrufe: erst Übersicht, dann die verlinkte PDF (Budget 2 eingehalten)",
          fake.extract_calls == [[UEBERSICHT], [PDF]], str(fake.extract_calls))
    check("P2) Generation im Bericht = UXC1", r.vehicle_identity.get("generation") == "UXC1",
          str(r.vehicle_identity.get("generation")))
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
    check("P6) Fragen/Dokumente unterscheiden sich vom Lauf OHNE Fallback nicht durch fremde Zuordnung "
          "(gleiche Anzahl Basisfragen/-dokumente)",
          len(r0.kaufaktionen.verkaeuferfragen.basis) == len(k.verkaeuferfragen.basis)
          and len(r0.kaufaktionen.dokumente.basis) == len(k.dokumente.basis))

    print("\n--- Gegenfälle ---")
    fake, r = await lauf({UEBERSICHT: UEBERSICHT_TEXT.replace("scene7.toyota.eu", "evil.example")})
    check("G1) fremder Zielhost: PDF nie abgerufen, Generation unbestätigt",
          fake.extract_calls == [[UEBERSICHT]] and r.vehicle_identity.get("generation") is None,
          f"{fake.extract_calls} / {r.vehicle_identity.get('generation')!r}")

    fake, r = await lauf({UEBERSICHT: UEBERSICHT_TEXT, PDF: _RETTUNGSKARTE_VOLLTEXT},
                         head=[_Resp(302, "https://evil.example/RCF.pdf")])
    check("G2) Redirect auf fremden Host: PDF nie abgerufen, Generation unbestätigt",
          fake.extract_calls == [[UEBERSICHT]] and r.vehicle_identity.get("generation") is None,
          f"{fake.extract_calls} / {r.vehicle_identity.get('generation')!r}")

    falsch = ("Lexus LC 500\n(Z100 (EU, M), ab 03/2017)\nStand: 02/2017\n"
              "12V Spannungsversorgung trennen\nHinweise zum Transport")
    fake, r = await lauf({UEBERSICHT: UEBERSICHT_TEXT, PDF: falsch})
    check("G3) PDF eines anderen Modells bestätigt keine Generation",
          r.vehicle_identity.get("generation") is None, str(r.vehicle_identity.get("generation")))

    fake, r = await lauf({UEBERSICHT: UEBERSICHT_TEXT, PDF: _RETTUNGSKARTE_VOLLTEXT}, mit_adac=False)
    check("G4) ohne unabhängigen Rollenbeleg keine Generation",
          r.vehicle_identity.get("generation") is None, str(r.vehicle_identity.get("generation")))

    fake, r = await lauf({UEBERSICHT: "Seite ohne Links.", PDF: _RETTUNGSKARTE_VOLLTEXT})
    check("G5) Übersicht ohne passenden Link: keine PDF-Abfrage, keine Generation",
          fake.extract_calls == [[UEBERSICHT]] and r.vehicle_identity.get("generation") is None)

    fake, r = await lauf({UEBERSICHT: UEBERSICHT_TEXT.replace("RC F - AB 09/2014", "Sonstiges")
                          .replace("RCF_tcm-3150-1284184", "Datei_1284184"), PDF: _RETTUNGSKARTE_VOLLTEXT})
    check("G6) Link ohne Modellbezug (Beschriftung/Dateiname) wird nicht gefolgt",
          fake.extract_calls == [[UEBERSICHT]], str(fake.extract_calls))

    if _FEHLER:
        print(f"\n{len(_FEHLER)} FEHLER:")
        for f in _FEHLER:
            print("  -", f)
        raise SystemExit(1)
    print("\nALLE N1-UEBERSICHT-FALLBACK-TESTS GRUEN")


asyncio.run(main())
