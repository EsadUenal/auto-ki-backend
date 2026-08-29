"""
VerkaufsCheck P1 #2 — kein Marktpreis ist ein OPTIONALES Zusatzmodul, kein
Totalabbruch.

Deterministisch: KEIN Netzwerk, KEIN echter Gemini-Call, KEIN Tavily. Sowohl die
Marktrecherche als auch der LLM-Aufruf in `app.verkaufscheck` werden gestubbt.

Vorher: research_failed -> RechercheUnzureichend -> gesamter VerkaufsCheck
        abgebrochen (Router erstattete das Kontingent), Fahrzeug-/Technik-Insights,
        Inseratsanalyse, Mängeltransparenz und Key-Findings verworfen.
Nachher: PFAD B — alles läuft vollständig, nur OHNE Preisaussage;
        research_status = "completed_no_market".

    python test_verkaufscheck_no_market.py
"""
import asyncio
import re

import app.verkaufscheck as vc
from app.marktrecherche import RechercheUnzureichend, research_status
from app.models import VerkaufsCheckRequest, Marktanalyse, Preisbeobachtung
from app.preisurteil import bewerte_preis, verkaufs_strategie, verkaufs_no_market_prompt_block

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


# ── Testfahrzeug: echte DB-Baureihe mit Schwachstellen UND Rückrufen ────────────
REQ = VerkaufsCheckRequest(
    marke="Volkswagen", modell="Passat", baujahr=2009,
    kilometerstand=180_000, motor="2.0 TDI", kraftstoff="Diesel",
    ausstattung=["Xenon", "Navi", "Sitzheizung"],
    beschreibung="Scheckheftgepflegt, zwei Vorbesitzer.",
    maengel=["Zweimassenschwungrad macht Geräusche"],
    preis_vorstellung=6_500,
)


def _leere_marktanalyse() -> Marktanalyse:
    return Marktanalyse(gefunden=3, verwendet=0, datenqualitaet="niedrig")


def _gute_marktanalyse() -> Marktanalyse:
    beob = [
        Preisbeobachtung(preis_eur=p, kilometerstand=180_000, baujahr=2009,
                         quelle_domain="beispiel.de", vergleichbarkeit="sehr_aehnlich")
        for p in (6_000, 6_500, 7_000, 7_200, 6_800)
    ]
    return Marktanalyse(
        gefunden=12, verwendet=5, anzahl_sehr_aehnlich=5,
        median_eur=6_800, spanne_min_eur=6_200, spanne_max_eur=7_200,
        datenqualitaet="hoch", marktabdeckung="gut", anzahl_domains=2,
        quellen_domains=["a.de", "b.de"], beobachtungen=beob,
    )


_letzter_prompt: dict = {}


def _stub_gemini(antwort: dict):
    async def _call(system_prompt: str, user_msg: str) -> dict:
        _letzter_prompt["system"] = system_prompt
        _letzter_prompt["user"] = user_msg
        return dict(antwort)
    return _call


def _stub_recherche(ma: Marktanalyse):
    async def _vertiefe(initial, deep, ziel, angebot, exclude, **kw):
        return [], ma, {"research_failure_grund": "data_exhausted"}
    return _vertiefe


async def _stub_tavily(*args, **kwargs) -> list[dict]:
    return []


def _stub_umgebung(ma: Marktanalyse, llm_antwort: dict):
    orig = (vc.vertiefe_marktrecherche, vc.call_gemini_json,
            vc.tavily_search_with_fallback, vc.TAVILY_API_KEY)
    vc.vertiefe_marktrecherche = _stub_recherche(ma)
    vc.call_gemini_json = _stub_gemini(llm_antwort)
    vc.tavily_search_with_fallback = _stub_tavily
    vc.TAVILY_API_KEY = "test-key"
    return orig


def _stub_zurueck(orig) -> None:
    (vc.vertiefe_marktrecherche, vc.call_gemini_json,
     vc.tavily_search_with_fallback, vc.TAVILY_API_KEY) = orig


def lauf(ma: Marktanalyse, llm_antwort: dict, req: VerkaufsCheckRequest = REQ) -> dict:
    orig = _stub_umgebung(ma, llm_antwort)
    try:
        return asyncio.run(vc.run_verkaufscheck(req))
    finally:
        _stub_zurueck(orig)


BERICHT_TECHNISCH = (
    "## Fahrzeug erkannt\nVW Passat B6 2.0 TDI, Baujahr 2009.\n\n"
    "## (a) Marktvergleich\nDerzeit liegen keine belastbaren Vergleichspreise vor.\n\n"
    "## (b) Empfohlene Preisspanne\nMangels Marktdaten bleibt die Preisempfehlung offen.\n\n"
    "## (c) Preis-Optimierungstipps\n**Betonen:** Scheckheft, Xenon, Navi.\n\n"
    "## (d) Verkaufsstrategie & Zeitplan\n- Inserieren auf den großen Portalen.\n"
)

LLM_OHNE_PREIS = {
    "bericht": BERICHT_TECHNISCH,
    "schnellverkaufs_preis": None, "empfohlener_preis": None, "maximal_preis": None,
    "marktpreis_min": None, "marktpreis_max": None,
    "preis_evidence_ids": [], "strategie_evidence_ids": [], "argument_evidence_ids": [],
}


print("=== 1/3. research_failed führt NICHT mehr zum Gesamtabbruch ===")

ma_leer = _leere_marktanalyse()
check("1.0 Vorbedingung: diese Marktanalyse ergibt research_failed",
      research_status(ma_leer) == "research_failed")

abgebrochen = False
try:
    ERG_NM = lauf(ma_leer, LLM_OHNE_PREIS)
except RechercheUnzureichend:
    abgebrochen = True
    ERG_NM = {}
check("3.1 KEIN RechercheUnzureichend mehr — VerkaufsCheck läuft durch", not abgebrochen)
check("1.x es kommt ein Ergebnis-Dict zurück", bool(ERG_NM))
check("2.1 Market unavailable -> VerkaufsCheck vollständig PASS",
      bool(ERG_NM.get("bericht")) and ERG_NM.get("research_status") == "completed_no_market")


print()
print("=== 5-8. Alle marktunabhängigen Bausteine bleiben vollständig ===")

check("6.1 Baureihe erkannt (belastbare Identität)",
      ERG_NM.get("baureihe_erkannt") == "volkswagen-passat-b6")
check("6.2 Insights vorhanden", len(ERG_NM.get("insights") or []) > 0)
check("6.3 technische Insight-Kategorien vorhanden",
      any(i.kategorie in ("schwachstelle", "rueckruf", "motorproblem")
          for i in ERG_NM.get("insights") or []))
check("7.1 key_findings vorhanden", len(ERG_NM.get("key_findings") or []) > 0)
check("5.1 listing_analyse vorhanden", ERG_NM.get("listing_analyse") is not None)
check("5.2 listing_analyse hat Verkaufsargumente/Stärken",
      bool((ERG_NM["listing_analyse"].verkaufsargumente
            or ERG_NM["listing_analyse"].staerken)))
lauf(ma_leer, LLM_OHNE_PREIS)   # frischer Lauf, um _letzter_prompt zu füllen
check("8.1 Mängeltransparenz: bekannter Mangel erreicht unverändert den LLM-Prompt",
      "schwungrad" in _letzter_prompt["user"].lower()
      and "Bekannte Mängel" in _letzter_prompt["user"])
check("x. Bericht substanziell (> 150 Zeichen)", len(ERG_NM.get("bericht") or "") > 150)


print()
print("=== 4/9. Keine erfundenen Preise ===")

check("4.1 marktpreis_min ist None", ERG_NM.get("marktpreis_min") is None)
check("4.2 marktpreis_max ist None", ERG_NM.get("marktpreis_max") is None)
check("4.3 schnellverkaufs_preis ist None", ERG_NM.get("schnellverkaufs_preis") is None)
check("4.4 empfohlener_preis ist None", ERG_NM.get("empfohlener_preis") is None)
check("4.5 maximal_preis ist None", ERG_NM.get("maximal_preis") is None)
check("4.6 KEIN 0-€-Ersatz irgendwo",
      all(ERG_NM.get(k) != 0 for k in ("marktpreis_min", "marktpreis_max",
          "schnellverkaufs_preis", "empfohlener_preis", "maximal_preis")))
check("4.7 verkaufsdauer-Kategorien None", ERG_NM.get("verkaufsdauer_empfohlen") is None)
pa = ERG_NM.get("price_assessment")
check("4.8 price_assessment.verdict == 'unbekannt'", pa is not None and pa.verdict == "unbekannt")
check("4.9 kein Median / keine Differenz im price_assessment",
      pa is not None and pa.median_eur is None and pa.difference_eur is None)
check("9.1 KEIN Preis-/Marktpositions-Finding in den key_findings",
      not any(f.kategorie in ("preis", "marktposition")
              for f in ERG_NM.get("key_findings") or []))


print()
print("=== 9. Keine Preis-Halluzination, auch wenn das Modell sie liefert ===")

LLM_MIT_ERFUNDENEM_PREIS = {
    "bericht": BERICHT_TECHNISCH.replace(
        "Mangels Marktdaten bleibt die Preisempfehlung offen.",
        "Der Marktpreis liegt bei 7.000 - 9.000 EUR, dein Preis ist marktgerecht."),
    "schnellverkaufs_preis": 6000, "empfohlener_preis": 8000, "maximal_preis": 9500,
    "marktpreis_min": 7000, "marktpreis_max": 9000,
    "preis_evidence_ids": [], "strategie_evidence_ids": [], "argument_evidence_ids": [],
}
erg_h = lauf(ma_leer, LLM_MIT_ERFUNDENEM_PREIS)
check("9.2 erfundene marktpreis_min/max verworfen",
      erg_h.get("marktpreis_min") is None and erg_h.get("marktpreis_max") is None)
check("9.3 erfundene Strategiepreise verworfen",
      erg_h.get("schnellverkaufs_preis") is None and erg_h.get("empfohlener_preis") is None
      and erg_h.get("maximal_preis") is None)
check("9.4 price_assessment bleibt 'unbekannt'",
      erg_h["price_assessment"].verdict == "unbekannt")
check("9.5 Preisurteil-Satz im Bericht neutralisiert (keine '7.000 - 9.000 EUR'-Spanne)",
      "7.000 - 9.000" not in erg_h.get("bericht", ""))
check("9.6 kein 'marktgerecht' mehr im Bericht",
      not re.search(r"\bmarktgerecht\b", erg_h.get("bericht", ""), re.IGNORECASE))


print()
print("=== Prompt-Vertrag: No-Market-Block nur in PFAD B ===")

lauf(ma_leer, LLM_OHNE_PREIS)
prompt_b = _letzter_prompt["user"]
check("P.1 PFAD B enthält den No-Market-Block",
      "KEINE BELASTBARE MARKTDATENBASIS" in prompt_b)
check("P.2 PFAD B enthält KEINE deterministische Preisstrategie",
      "DETERMINISTISCHE PREISSTRATEGIE" not in prompt_b)
check("P.3 PFAD B enthält KEIN kanonisches Preisurteil",
      "KANONISCHES PREISURTEIL" not in prompt_b)
check("P.4 PFAD B enthält weiterhin die Fahrzeugangaben",
      "MEIN FAHRZEUG" in prompt_b)


print()
print("=== 1. PFAD A: mit Marktdaten unverändertes Verhalten ===")

ma_gut = _gute_marktanalyse()
check("A.0 Vorbedingung: completed_high", research_status(ma_gut) == "completed_high")

LLM_MIT_PREIS = {
    "bericht": BERICHT_TECHNISCH.replace(
        "## (b) Empfohlene Preisspanne\nMangels Marktdaten bleibt die Preisempfehlung offen.",
        "## (b) Empfohlene Preisspanne\nMedian rund 6.800 €."),
    "schnellverkaufs_preis": 6200, "empfohlener_preis": 6800, "maximal_preis": 7200,
    "marktpreis_min": 6200, "marktpreis_max": 7200,
    "preis_evidence_ids": [], "strategie_evidence_ids": [], "argument_evidence_ids": [],
}
ERG_A = lauf(ma_gut, LLM_MIT_PREIS)
check("A.1 research_status == 'completed_high'", ERG_A.get("research_status") == "completed_high")
check("A.2 marktpreis-Spanne aus der Marktanalyse",
      ERG_A.get("marktpreis_min") == 6200 and ERG_A.get("marktpreis_max") == 7200)
check("A.3 deterministische Strategiepreise (Median = empfohlen)",
      ERG_A.get("empfohlener_preis") == 6800
      and ERG_A.get("schnellverkaufs_preis") == 6200
      and ERG_A.get("maximal_preis") == 7200)
check("A.4 Vermarktungs-Kategorie gesetzt", ERG_A.get("verkaufsdauer_empfohlen") is not None)
check("A.5 price_assessment identisch zum direkten bewerte_preis-Aufruf",
      ERG_A["price_assessment"].verdict
      == bewerte_preis(ma_gut, REQ.preis_vorstellung, check_typ="verkauf").verdict)
check("A.6 technische Insights weiterhin vorhanden",
      any(i.kategorie in ("schwachstelle", "rueckruf", "motorproblem")
          for i in ERG_A.get("insights") or []))

lauf(ma_gut, LLM_MIT_PREIS)
prompt_a = _letzter_prompt["user"]
check("A.7 PFAD A enthält KEINEN No-Market-Block",
      "KEINE BELASTBARE MARKTDATENBASIS" not in prompt_a)
check("A.8 PFAD A enthält die deterministische Preisstrategie",
      "DETERMINISTISCHE PREISSTRATEGIE" in prompt_a)


print()
print("=== Marktanalyse-Module + RechercheUnzureichend unverändert erreichbar ===")

check("M.1 research_status weiterhin research_failed ohne Median",
      research_status(_leere_marktanalyse()) == "research_failed")
check("M.2 bewerte_preis(None) ohne Dummy-Werte",
      bewerte_preis(None, 6500).verdict == "unbekannt"
      and bewerte_preis(None, 6500).median_eur is None)
check("M.3 verkaufs_strategie(schwach) -> None", verkaufs_strategie(_leere_marktanalyse()) is None)
check("M.4 verkaufs_no_market_prompt_block ist substanziell",
      len(verkaufs_no_market_prompt_block()) > 200)
klasse_ok = True
try:
    RechercheUnzureichend(_leere_marktanalyse(), "test", "data_exhausted")
except Exception:
    klasse_ok = False
check("M.5 RechercheUnzureichend existiert weiter (Router-Sicherheitsnetz)", klasse_ok)


print()
print("=== Bericht enthält keine Preiskategorie ohne Basis ===")
_PREISWORTE = re.compile(
    r"\b(marktgerecht|g[uü]nstiges? angebot|zu teuer|[üu]berteuert|fairer preis|"
    r"oberes marktsegment)\b", re.IGNORECASE)
check("B.1 No-Market-Bericht enthält keine Preiskategorie",
      _PREISWORTE.search(ERG_NM.get("bericht", "")) is None)


print()
if _FEHLER:
    print(f"FEHLGESCHLAGEN: {len(_FEHLER)}")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE VERKAUFSCHECK-P1-#2-TESTS GRUEN")
