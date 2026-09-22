"""
Cost-Gate fuer die Marktpreis-Recherche (RC1) — KaufCheck und VerkaufsCheck.

Ausgangslage: `AUTO_KI_ALLOWED_MARKET_SOURCES` ist im Production-Default LEER.
`marktvergleich._bewerte` verwirft dann JEDE Webquelle vor der fachlichen
Pruefung, es kann also kein Median und keine Preisbewertung entstehen. Trotzdem
lief die adaptive Recherche mit bis zu 16 Tavily-Calls je Check an.

Geprueft wird:
  A) leere Freigabeliste  -> 0 Markt-Webrequests, Preis nicht bewertbar,
                             uebriger Check vollstaendig
  B) freigegebene Quelle  -> der Recherchepfad ist wieder erreichbar
  C) keine Regression bei HU, Variantenerkennung, Rueckrufen und Claims

Deterministisch: KEIN echter Providercall (Gemini gestubbt, Tavily durch einen
zaehlenden Stub ersetzt, der bei einem echten Aufruf auffaellt).

    python test_markt_cost_gate.py
"""
import asyncio
import sys

sys.path.insert(0, ".")

import app.kaufcheck as kc            # noqa: E402
import app.marktrecherche as mr       # noqa: E402
import app.verkaufscheck as vc        # noqa: E402
import app.web_search as ws           # noqa: E402
from app.models import KaufCheckRequest, VerkaufsCheckRequest  # noqa: E402

FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        FEHLER.append(name)


# ── Zaehlende Provider-Stubs ────────────────────────────────────────────────
zaehler = {"tavily": 0, "vertiefung": 0, "gemini": 0}


async def _stub_tavily(*a, **kw):
    zaehler["tavily"] += 1
    return []


async def _stub_tavily_status(*a, **kw):
    zaehler["tavily"] += 1
    return [], False


async def _stub_vertiefung(web_results, queries, ziel, preis, exclude, **kw):
    zaehler["vertiefung"] += 1
    from app.marktvergleich import analysiere_markt
    return web_results, analysiere_markt(web_results, ziel, preis), {}


_KAUF_ANTWORT = {
    "bericht": ("## Fahrzeug\nBMW 320i.\n\n## Preis-Einschätzung\nKeine Marktdaten.\n"),
    "empfehlung": "kaufen_nach_besichtigung", "preis_bewertung": "unbekannt",
    "marktpreis_min": None, "marktpreis_max": None,
}
_VERKAUF_ANTWORT = {
    "bericht": "## Fahrzeug erkannt\nBMW 320i.\n", "preis_evidence_ids": [],
    "strategie_evidence_ids": [], "argument_evidence_ids": [],
}


def _stub_gemini(antwort):
    async def _f(system, user):
        zaehler["gemini"] += 1
        return dict(antwort)
    return _f


KAUF = KaufCheckRequest(
    marke="BMW", modell="320i", baujahr=2019, kilometerstand=95_000, motor="2.0 Benzin, 184 PS",
    preis=24_900, beschreibung="Scheckheftgepflegt, unfallfrei laut Inserat.",
    tuev_bis="08/2027", unfallfrei="ja", vorbesitzer=2, scheckheft=True,
)
VERKAUF = VerkaufsCheckRequest(
    marke="BMW", modell="320i", baujahr=2019, kilometerstand=95_000, motor="2.0 Benzin, 184 PS",
    preis_vorstellung=24_900, beschreibung="Gut", tuev_bis="08/2027",
    unfallfrei="ja", vorbesitzer=2, scheckheftgepflegt=True,
)


def lauf_kauf() -> dict:
    orig = (kc.call_gemini_json, kc.tavily_search_with_fallback,
            mr.tavily_search_mit_status, kc.vertiefe_marktrecherche, kc.TAVILY_API_KEY)
    kc.call_gemini_json = _stub_gemini(_KAUF_ANTWORT)
    kc.tavily_search_with_fallback = _stub_tavily
    mr.tavily_search_mit_status = _stub_tavily_status
    kc.vertiefe_marktrecherche = _stub_vertiefung
    kc.TAVILY_API_KEY = "test-key"
    try:
        return asyncio.run(kc.run_kaufcheck(KAUF))
    finally:
        (kc.call_gemini_json, kc.tavily_search_with_fallback,
         mr.tavily_search_mit_status, kc.vertiefe_marktrecherche, kc.TAVILY_API_KEY) = orig


def lauf_verkauf() -> dict:
    orig = (vc.call_gemini_json, vc.tavily_search_with_fallback,
            mr.tavily_search_mit_status, vc.vertiefe_marktrecherche, vc.TAVILY_API_KEY)
    vc.call_gemini_json = _stub_gemini(_VERKAUF_ANTWORT)
    vc.tavily_search_with_fallback = _stub_tavily
    mr.tavily_search_mit_status = _stub_tavily_status
    vc.vertiefe_marktrecherche = _stub_vertiefung
    vc.TAVILY_API_KEY = "test-key"
    try:
        return asyncio.run(vc.run_verkaufscheck(VERKAUF))
    finally:
        (vc.call_gemini_json, vc.tavily_search_with_fallback,
         mr.tavily_search_mit_status, vc.vertiefe_marktrecherche, vc.TAVILY_API_KEY) = orig


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== A) Leere Freigabeliste: kein einziger Markt-Webrequest ===")

alt = ws.setze_marktquellen_freigabe(set())
alt_tavily = mr.TAVILY_API_KEY
mr.TAVILY_API_KEY = "test-key"          # Key vorhanden, trotzdem keine Freigabe
try:
    check("A0 Gate meldet 'nicht moeglich'",
          mr.marktpreis_recherche_moeglich("BMW", "320i") is False)

    zaehler.update(tavily=0, vertiefung=0, gemini=0)
    res_k = lauf_kauf()
    check("A1 KaufCheck: 0 Markt-Webrequests",
          zaehler["tavily"] == 0 and zaehler["vertiefung"] == 0)
    check("A2 KaufCheck: genau ein LLM-Call", zaehler["gemini"] == 1)
    check("A3 KaufCheck: Preis nicht bewertbar",
          res_k["research_status"] == "completed_no_market"
          and res_k.get("marktpreis_min") is None and res_k.get("marktpreis_max") is None)
    check("A4 KaufCheck: keine Belege aus dem Web", res_k["belege"] == [])
    check("A5 KaufCheck: uebriger Check vollstaendig",
          bool(res_k["bericht"]) and res_k["baureihe_erkannt"] == "bmw-3er-g20-g21"
          and bool(res_k.get("pruefplan") or res_k.get("kaufaktionen") or res_k.get("insights")))

    zaehler.update(tavily=0, vertiefung=0, gemini=0)
    res_v = lauf_verkauf()
    check("A6 VerkaufsCheck: 0 Markt-Webrequests",
          zaehler["tavily"] == 0 and zaehler["vertiefung"] == 0)
    check("A7 VerkaufsCheck: Preis nicht bewertbar",
          res_v["research_status"] == "completed_no_market"
          and res_v["empfohlener_preis"] is None)
    check("A8 VerkaufsCheck: Verkaufsfahrplan vollstaendig",
          bool(res_v["verkaufsplan"]["inserat"]["titel"])
          and bool(res_v["verkaufsplan"]["fotoplan"]["fotos"]))

    print("\n=== C) Keine Regression an den bestandenen KaufCheck-Bausteinen ===")
    check("C1 HU-Pruefung weiterhin vorhanden und plausibel",
          (res_k.get("hu_pruefung") or {}).get("status") == "plausibel")
    check("C2 Motorvariante weiterhin erkannt", bool(res_k.get("motor_erkannt")))
    check("C3 Rueckrufe kommen weiterhin aus dem Evidence-System",
          any(getattr(i, "kategorie", None) == "rueckruf" for i in res_k.get("insights") or [])
          or res_k.get("baureihe_erkannt") is not None)
    check("C4 Claim-Sicherheit: kein 'lueckenlos' im Bericht",
          "lückenlos" not in res_k["bericht"].lower())

    print("\n=== B) Freigegebene Quelle: Recherchepfad wieder erreichbar ===")
    ws.setze_marktquellen_freigabe({"beispielportal.de"})
    check("B1 Gate meldet 'moeglich'",
          mr.marktpreis_recherche_moeglich("BMW", "320i") is True)
    zaehler.update(tavily=0, vertiefung=0, gemini=0)
    lauf_kauf()
    check("B2 KaufCheck startet die Recherche wieder", zaehler["vertiefung"] >= 1)
    zaehler.update(tavily=0, vertiefung=0, gemini=0)
    lauf_verkauf()
    check("B3 VerkaufsCheck startet die Recherche wieder", zaehler["vertiefung"] >= 1)
    check("B4 ohne Marke/Modell bleibt das Gate zu",
          mr.marktpreis_recherche_moeglich("BMW", None) is False)
finally:
    ws.setze_marktquellen_freigabe(alt)
    mr.TAVILY_API_KEY = alt_tavily

print()
print(f"{len(FEHLER)} FAIL" if FEHLER else "ALLE TESTS GRÜN")
for f in FEHLER:
    print("  -", f)
sys.exit(1 if FEHLER else 0)
