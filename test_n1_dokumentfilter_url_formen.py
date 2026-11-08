"""
N1 -- Dokumentfilter (`_herstelleridentifikationsdokument_grund`): zwei belegte
Ablehnungen eines echten Rettungskarten-Treffers.

  1. PDF mit Query-Parametern ("...pdf?download=1") -> bisher `kein_pdf`, weil
     die Endung am GANZEN URL-String statt am Pfad geprueft wurde.
  2. englisches Rubrikwort im Pfad ("rescue-card") -> bisher `falsches_pfadwort`.

Prueft beide URL-Formen getrennt bis zum fertigen Bericht (echter
run_kaufcheck()-Pfad, Provider/Extract/DB-Lookup gestubbt, Netz/DB isoliert
ueber scripts/test_isolated.py), plus Gegenfaelle: fremde Domain, fachlich
unpassender Beleg, fehlender unabhaengiger Rollenbeleg.

    python scripts/test_isolated.py test_n1_dokumentfilter_url_formen.py
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import app.technical_research as tr
from app.models import KaufCheckRequest
from test_n1_generation_spec_gap_run_kaufcheck import (  # noqa: E402  (laeuft beim Import mit)
    _ADAC, _AMS, _RETTUNGSKARTE_SNIPPET_DUENN, _RETTUNGSKARTE_VOLLTEXT,
    _FakeSearchExtract, _lauf, treffer,
)

_FEHLER: list[str] = []


def check(name, bedingung, detail=""):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


BASIS = ("https://www.lexus.de/content/dam/lexus/nmsc/germany/lexus-besitzer/"
         "fahrzeuginformationen/RCF_tcm-3150-1284184.pdf")
MIT_QUERY = BASIS + "?download=1&token=abc"
RESCUE = "https://www.lexus.de/documents/rescue-card/RCF_en.pdf"

G = tr._herstelleridentifikationsdokument_grund
print("--- Filter-Einzelentscheide ---")
check("F1) Original-PDF erfuellt das Gate", G(BASIS, "Lexus") is None)
check("F2) PDF mit Query-Parametern erfuellt das Gate (Endung am Pfad)", G(MIT_QUERY, "Lexus") is None)
check("F3) PDF mit Fragment erfuellt das Gate", G(BASIS + "#page=2", "Lexus") is None)
check("F4) 'rescue-card' im Pfad erfuellt das Gate", G(RESCUE, "Lexus") is None)
check("F5) 'rescue_card' und 'rescue%20card' ebenso",
      G("https://www.lexus.de/x/rescue_card/a.pdf", "Lexus") is None
      and G("https://www.lexus.de/x/rescue%20card/a.pdf", "Lexus") is None)
check("F6) fremde Domain bleibt abgelehnt (auch mit Query/rescue-card)",
      G("https://www.carwiki.de/rescue-card/RCF.pdf?download=1", "Lexus") == "fremde_domain")
check("F7) Query allein macht keine HTML-Seite zum PDF",
      G("https://www.lexus.de/rettungskarte/seite.html?datei=x.pdf", "Lexus") == "kein_pdf")
check("F8) Pfadwort nur in der Query reicht nicht",
      G("https://www.lexus.de/documents/handbuch.pdf?q=rettungskarte", "Lexus") == "falsches_pfadwort")


async def lauf(routing, extract_text, req=None):
    fake = _FakeSearchExtract(routing=routing, extract_text=extract_text)
    r = await _lauf(req or KaufCheckRequest(
        marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000, motor="5.0 V8",
        kraftstoff="Benzin", leistung_ps=477, getriebe="Automatik", antrieb="Heck", ausstattung=[]),
        fake)
    return fake, r


def routing(hit_url, mit_adac=True):
    return {"technische Daten": [_ADAC] if mit_adac else [],
            "Generation Baureihe Bauzeitraum": [_AMS],
            "Generation Modellcode": [treffer(hit_url, "Lexus RC F Rettungskarte",
                                              _RETTUNGSKARTE_SNIPPET_DUENN)]}


def aktionen_text(r):
    k = r.kaufaktionen
    return " ".join(str(p) for p in (k.besichtigung, k.probefahrt, k.verkaeuferfragen))


async def main():
    for name, url in (("URL mit Query", MIT_QUERY), ("rescue-card-Pfad", RESCUE)):
        print(f"\n--- Vollpfad: {name} ---")
        fake, r = await lauf(routing(url), {url: _RETTUNGSKARTE_VOLLTEXT})
        check(f"{name}: genau ein Abruf mit der ORIGINAL-URL (Parameter unveraendert)",
              fake.extract_calls == [[url]], str(fake.extract_calls))
        check(f"{name}: Generation im Bericht = UXC1",
              r.vehicle_identity.get("generation") == "UXC1", str(r.vehicle_identity.get("generation")))
        hinweis = r.bericht[r.bericht.find("Blockierende Identitätslücke"):][:120]
        check(f"{name}: Generationssperre aus der Blockade-Meldung verschwunden",
              "Generation" not in hinweis, hinweis)
        k = r.kaufaktionen
        check(f"{name}: Fragen und Dokumente werden weiter erzeugt (nicht leer)",
              bool(k.verkaeuferfragen.basis) and bool(k.dokumente.basis),
              f"fragen={len(k.verkaeuferfragen.basis)} dokumente={len(k.dokumente.basis)}")
        print(f"   sichtbar: generation={r.vehicle_identity.get('generation')!r} "
              f"state={r.recommendation_state!r} hinweis={hinweis!r}")

    print("\n--- Gegenfaelle ---")
    fremd = "https://www.carwiki.de/lexus-rc/rescue-card/RCF.pdf?download=1"
    fake, r = await lauf(routing(fremd), {fremd: _RETTUNGSKARTE_VOLLTEXT})
    check("G1) fremde Domain: kein Abruf, Generation nicht bestaetigt",
          fake.extract_calls == [] and r.vehicle_identity.get("generation") is None,
          f"{fake.extract_calls} / {r.vehicle_identity.get('generation')!r}")
    check("G1b) Blockade 'Generation' steht weiter im Bericht",
          "Blockierende Identitätslücke: Generation" in r.bericht)

    falsch = ("Lexus LC 500\n(Z100 (EU, M), ab 03/2017)\nStand: 02/2017\n"
              "12V Spannungsversorgung trennen\nHinweise zum Transport")
    for url in (MIT_QUERY, RESCUE):
        fake, r = await lauf(routing(url), {url: falsch})
        check(f"G2) fachlich unpassender Beleg (anderes Modell) bestaetigt keine Generation [{url[-30:]}]",
              r.vehicle_identity.get("generation") is None, str(r.vehicle_identity.get("generation")))

    fake, r = await lauf(routing(MIT_QUERY, mit_adac=False), {MIT_QUERY: _RETTUNGSKARTE_VOLLTEXT})
    check("G3) Rettungskarte OHNE unabhaengigen Rollenbeleg bestaetigt keine Generation",
          r.vehicle_identity.get("generation") is None, str(r.vehicle_identity.get("generation")))

    if _FEHLER:
        print(f"\n{len(_FEHLER)} FEHLER:")
        for f in _FEHLER:
            print("  -", f)
        raise SystemExit(1)
    print("\nALLE N1-DOKUMENTFILTER-TESTS GRUEN")


asyncio.run(main())
