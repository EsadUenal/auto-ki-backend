"""
KaufCheck P0-1 — Marktpreis ist ein OPTIONALES ZUSATZMODUL, kein Totalabbruch.

Deterministisch: KEIN Netzwerk, KEIN echter Gemini-Call, KEIN Tavily. Sowohl die
Marktrecherche als auch der LLM-Aufruf werden im Modul `app.kaufcheck` durch
Stubs ersetzt, damit ausschliesslich die Entkopplungslogik geprueft wird.

Vorher: research_failed -> RechercheUnzureichend -> gesamter Kaufcheck abgebrochen,
        erkannte Baureihe/Motor/Rueckrufe/Schwachstellen/Insights verworfen.
Nachher: PFAD B — technische Analyse laeuft vollstaendig, aber ohne jede
        Preisaussage; research_status = "completed_no_market".

    python test_kaufcheck_no_market.py
"""
import asyncio
import re

import app.kaufcheck as kc
from app.marktrecherche import RechercheUnzureichend, research_status
from app.models import KaufCheckRequest, Marktanalyse, Preisbeobachtung
from app.preisurteil import bewerte_preis, no_market_prompt_block

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    status = "OK  " if bedingung else "FAIL"
    print(f"[{status}] {name}")
    if not bedingung:
        _FEHLER.append(name)


# ── Testfahrzeug: echte DB-Baureihe mit Schwachstellen UND Rueckruf ──────────
# VW Passat B6 (Baujahr 2009) hat in der Fahrzeug-DB reale Schwachstellen; damit
# ist der technische Teil des Checks belegbar vorhanden und nicht bloss leer.
REQ = KaufCheckRequest(
    marke="Volkswagen", modell="Passat", baujahr=2009,
    kilometerstand=180_000, motor="2.0 TDI", kraftstoff="Diesel", preis_eur=6_500,
)


def _leere_marktanalyse() -> Marktanalyse:
    """Marktanalyse ohne Median — exakt das, was research_failed ausloest."""
    return Marktanalyse(gefunden=3, verwendet=0, datenqualitaet="niedrig")


def _gute_marktanalyse() -> Marktanalyse:
    """Belastbare Marktanalyse (PFAD A) — Median + Spanne + hohe Qualitaet."""
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


# ── Stubs ────────────────────────────────────────────────────────────────────

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
    """Ersetzt den initialen Tavily-Aufruf in run_kaufcheck. Ohne diesen Stub
    wuerde der Test echte Netzwerk-Requests absetzen (und je nach API-Kontingent
    unterschiedlich laufen) — der Test soll ausschliesslich die Entkopplungslogik
    pruefen, nicht die Suche."""
    return []


def _stub_umgebung(ma: Marktanalyse, llm_antwort: dict):
    """Setzt ALLE Netzwerkpfade von run_kaufcheck auf Stubs. Gibt die
    Originalwerte zum Wiederherstellen zurueck."""
    orig = (kc.vertiefe_marktrecherche, kc.call_gemini_json,
            kc.tavily_search_with_fallback, kc.TAVILY_API_KEY)
    kc.vertiefe_marktrecherche = _stub_recherche(ma)
    kc.call_gemini_json = _stub_gemini(llm_antwort)
    kc.tavily_search_with_fallback = _stub_tavily
    kc.TAVILY_API_KEY = "test-key"   # damit der Recherchepfad ueberhaupt betreten wird
    return orig


def _stub_zurueck(orig) -> None:
    (kc.vertiefe_marktrecherche, kc.call_gemini_json,
     kc.tavily_search_with_fallback, kc.TAVILY_API_KEY) = orig


def lauf_kaufcheck(ma: Marktanalyse, llm_antwort: dict, req: KaufCheckRequest = REQ) -> dict:
    """Fuehrt run_kaufcheck mit gestubbtem Markt + LLM + Tavily aus und stellt
    danach den Originalzustand wieder her."""
    orig = _stub_umgebung(ma, llm_antwort)
    try:
        return asyncio.run(kc.run_kaufcheck(req))
    finally:
        _stub_zurueck(orig)


BERICHT_TECHNISCH = (
    "## Fahrzeug erkannt\nVW Passat B6 2.0 TDI, Baujahr 2009.\n\n"
    "## Kaufempfehlung\n**NUR MIT WERKSTATTPRUEFUNG**\nBekannte Schwachstellen der "
    "Baureihe erfordern eine Fachpruefung vor dem Kauf.\n\n"
    "## Kritische Risiken\n- Steuerkette\n- DSG-Getriebe\n\n"
    "## Preis-Einschaetzung\nFuer dieses Fahrzeug konnte aktuell keine belastbare "
    "Marktpreisbasis ermittelt werden. Die Preisbewertung bleibt daher offen.\n\n"
    "## Besichtigungs-Checkliste\n- [ ] Steuerkette auf Geraeusche pruefen\n"
)

LLM_OHNE_PREIS = {
    "bericht": BERICHT_TECHNISCH,
    "empfehlung": "nur_mit_werkstattpruefung",
    "preis_bewertung": "unbekannt",
    "marktpreis_min": None, "marktpreis_max": None,
    "empfehlung_evidence_ids": [], "preis_evidence_ids": [], "risiko_evidence_ids": [],
}


print("=== A. research_failed fuehrt NICHT mehr zum Gesamtabbruch ===")

ma_leer = _leere_marktanalyse()
check("A0: Vorbedingung — diese Marktanalyse ergibt research_failed",
      research_status(ma_leer) == "research_failed")

abgebrochen = False
try:
    ERG_NO_MARKET = lauf_kaufcheck(ma_leer, LLM_OHNE_PREIS)
except RechercheUnzureichend:
    abgebrochen = True
    ERG_NO_MARKET = {}
check("A1: KEIN RechercheUnzureichend mehr — Kaufcheck laeuft durch", not abgebrochen)
check("A2: es kommt ein Ergebnis-Dict zurueck", bool(ERG_NO_MARKET))

print()
print("=== B-E. Technische Analyse bleibt vollstaendig ===")

check("B1: Baureihe wurde erkannt und ausgegeben",
      ERG_NO_MARKET.get("baureihe_erkannt") == "volkswagen-passat-b6")
check("B2: Insights vorhanden (nicht leer)", len(ERG_NO_MARKET.get("insights") or []) > 0)
check("B3: Insights enthalten technische Kategorien (Schwachstelle/Rueckruf/Motorproblem)",
      any(i.kategorie in ("schwachstelle", "rueckruf", "motorproblem")
          for i in ERG_NO_MARKET.get("insights") or []))
check("C1: key_findings vorhanden (nicht leer)",
      len(ERG_NO_MARKET.get("key_findings") or []) > 0)
check("D1: Rueckruf- ODER Schwachstellen-Findings vorhanden",
      any(f.kategorie in ("rueckruf", "schwachstelle", "motorproblem", "widerspruch")
          for f in ERG_NO_MARKET.get("key_findings") or []))
check("E1: Empfehlung vorhanden und nicht 'unbekannt'",
      ERG_NO_MARKET.get("empfehlung") == "nur_mit_werkstattpruefung")
check("E2: Bericht vorhanden und substanziell",
      len(ERG_NO_MARKET.get("bericht") or "") > 200)

print()
print("=== F-H. Keine Preisaussage ohne Marktdaten ===")

check("F1: marktpreis_min ist None", ERG_NO_MARKET.get("marktpreis_min") is None)
check("F2: marktpreis_max ist None", ERG_NO_MARKET.get("marktpreis_max") is None)
pa = ERG_NO_MARKET.get("price_assessment")
check("G1: price_assessment.verdict == 'unbekannt'", pa is not None and pa.verdict == "unbekannt")
check("G2: kein Median im price_assessment", pa is not None and pa.median_eur is None)
check("G3: keine Differenz zum Markt", pa is not None and pa.difference_eur is None)
check("G4: keine Prozentabweichung", pa is not None and pa.difference_percent is None)
check("G5: preis_bewertung == 'unbekannt'", ERG_NO_MARKET.get("preis_bewertung") == "unbekannt")
check("H1: KEIN Preis-Finding in den key_findings",
      not any(f.kategorie == "preis" for f in ERG_NO_MARKET.get("key_findings") or []))

print()
print("=== I. Keine Preis-Halluzination, auch wenn das Modell sie liefert ===")

LLM_MIT_ERFUNDENEM_PREIS = {
    # Das Modell haelt sich NICHT an den No-Market-Block: es setzt Preisfelder und
    # schreibt eine Spanne in den Fliesstext (Angriff auf _notfall_extraktion).
    "bericht": BERICHT_TECHNISCH.replace(
        "Die Preisbewertung bleibt daher offen.",
        "Der Marktpreis liegt bei 7.000 - 9.000 EUR, das Angebot ist guenstig."),
    "empfehlung": "nur_mit_werkstattpruefung",
    "preis_bewertung": "guenstig",
    "marktpreis_min": 7000, "marktpreis_max": 9000,
    "empfehlung_evidence_ids": [], "preis_evidence_ids": [], "risiko_evidence_ids": [],
}
erg_halluz = lauf_kaufcheck(ma_leer, LLM_MIT_ERFUNDENEM_PREIS)
check("I1: erfundene marktpreis_min wird verworfen", erg_halluz.get("marktpreis_min") is None)
check("I2: erfundene marktpreis_max wird verworfen", erg_halluz.get("marktpreis_max") is None)
check("I3: erfundene preis_bewertung 'guenstig' wird auf 'unbekannt' zurueckgesetzt",
      erg_halluz.get("preis_bewertung") == "unbekannt")
check("I4: price_assessment bleibt 'unbekannt'",
      erg_halluz["price_assessment"].verdict == "unbekannt")
check("I5: technische Empfehlung bleibt erhalten",
      erg_halluz.get("empfehlung") == "nur_mit_werkstattpruefung")

LLM_PREIS_NACHVERHANDELN = dict(LLM_OHNE_PREIS, empfehlung="preis_nachverhandeln")
erg_pn = lauf_kaufcheck(ma_leer, LLM_PREIS_NACHVERHANDELN)
# Zwei Schritte greifen hier NACHEINANDER, beide bewusst:
#   1. Ohne Marktdaten ist die PREIS-Haelfte von "preis_nachverhandeln" nicht
#      belegbar -> Reduktion auf den technischen Teil (kaufen_nach_besichtigung).
#   2. Der deterministische Empfehlungs-Floor (app/empfehlungs_floor) hob frueher
#      zusaetzlich auf "nur_mit_werkstattpruefung", weil das Fixture-Fahrzeug
#      (VW Passat B6 2.0 TDI) ZWEI Schwachstellen mit Schweregrad "hoch" traegt.
#
#      DATA-SAFETY-RUNTIME-GATE (P0): das tut er nicht mehr. Beide Schwachstellen
#      stammen aus der Fahrzeugdatenbank, fuer die keinerlei Provenance gespeichert
#      ist (0 von 421 Baureihen mit `verification`, Tabelle `quelle` leer). Ein
#      unbelegter DB-Schweregrad darf die Kaufempfehlung nicht mehr allein
#      verschaerfen. Die Schwachstellen selbst bleiben vollstaendig sichtbar und
#      erzeugen unveraendert ihre Pruefpunkte — nur die harte Eskalation entfaellt.
#
# Die eigentliche Zusicherung dieses Falls ist unveraendert: die unbelegbare
# Preisaussage wird NICHT weitergetragen.
check("I6a: 'preis_nachverhandeln' ohne Marktdaten wird nicht weitergetragen",
      erg_pn.get("empfehlung") != "preis_nachverhandeln")
check("I6b: ohne Provenance keine Floor-Eskalation — es bleibt beim technischen "
      "Rest 'kaufen_nach_besichtigung'",
      erg_pn.get("empfehlung") == "kaufen_nach_besichtigung")

print()
print("=== J. Status eindeutig No-Market ===")

check("J1: research_status == 'completed_no_market'",
      ERG_NO_MARKET.get("research_status") == "completed_no_market")
check("J2: NICHT 'research_failed' (kein Fehlerstatus)",
      ERG_NO_MARKET.get("research_status") != "research_failed")

print()
print("=== K/L. Quota / Refund ===")
# Der Refund haengt allein daran, ob run_kaufcheck eine Exception wirft (siehe
# app/routers/kaufcheck.py). Kein Wurf == kein Refund == Check zaehlt als erfolgreich.
check("K1: erfolgreicher No-Market-Check wirft KEINE Exception -> kein Refund",
      not abgebrochen and ERG_NO_MARKET.get("research_status") == "completed_no_market")

klasse_erhalten = True
try:
    RechercheUnzureichend(_leere_marktanalyse(), "test", "data_exhausted")
except Exception:
    klasse_erhalten = False
check("L1: RechercheUnzureichend existiert weiterhin (Verkaufscheck-Pfad + "
      "Router-Sicherheitsnetz unveraendert)", klasse_erhalten)

gemini_fehler = False
async def _explodiert(system_prompt, user_msg):
    raise RuntimeError("Gemini-Totalausfall")
_orig = _stub_umgebung(ma_leer, {})
kc.call_gemini_json = _explodiert
try:
    asyncio.run(kc.run_kaufcheck(REQ))
except RuntimeError:
    gemini_fehler = True
finally:
    _stub_zurueck(_orig)
check("L2: echter Gesamtfehler (LLM-Ausfall) propagiert weiterhin — bestehende "
      "Fehler-/Refund-Logik unveraendert", gemini_fehler)

print()
print("=== M. PFAD A: mit Marktdaten unveraendertes Verhalten ===")

ma_gut = _gute_marktanalyse()
check("M0: Vorbedingung — diese Marktanalyse ergibt completed_high",
      research_status(ma_gut) == "completed_high")

LLM_MIT_PREIS = {
    "bericht": BERICHT_TECHNISCH.replace(
        "Fuer dieses Fahrzeug konnte aktuell keine belastbare Marktpreisbasis "
        "ermittelt werden. Die Preisbewertung bleibt daher offen.",
        "Marktgerecht. Median 6.800 EUR."),
    "empfehlung": "nur_mit_werkstattpruefung",
    "preis_bewertung": "marktgerecht",
    "marktpreis_min": 6200, "marktpreis_max": 7200,
    "empfehlung_evidence_ids": [], "preis_evidence_ids": [], "risiko_evidence_ids": [],
}
ERG_MARKT = lauf_kaufcheck(ma_gut, LLM_MIT_PREIS)

check("M1: research_status bleibt 'completed_high'",
      ERG_MARKT.get("research_status") == "completed_high")
check("M2: marktpreis_min stammt aus der Marktanalyse", ERG_MARKT.get("marktpreis_min") == 6200)
check("M3: marktpreis_max stammt aus der Marktanalyse", ERG_MARKT.get("marktpreis_max") == 7200)
pa_m = ERG_MARKT["price_assessment"]
check("M4: Median unveraendert aus der Marktanalyse", pa_m.median_eur == 6800)
check("M5: Preisurteil ist NICHT 'unbekannt'", pa_m.verdict != "unbekannt")
check("M6: preis_bewertung aus dem kanonischen Verdikt abgeleitet",
      ERG_MARKT.get("preis_bewertung") != "unbekannt")
check("M7: Preisurteil identisch zum direkten bewerte_preis-Aufruf (keine "
      "abweichende zweite Wahrheit)",
      pa_m.verdict == bewerte_preis(ma_gut, REQ.preis_eur, check_typ="kauf").verdict)
check("M8: Preis-Finding in den key_findings vorhanden",
      any(f.kategorie == "preis" for f in ERG_MARKT.get("key_findings") or []))
check("M9: technische Insights weiterhin vorhanden",
      any(i.kategorie in ("schwachstelle", "rueckruf", "motorproblem")
          for i in ERG_MARKT.get("insights") or []))

print()
print("=== N. Prompt-Vertrag: No-Market-Block nur in PFAD B ===")

lauf_kaufcheck(ma_leer, LLM_OHNE_PREIS)
prompt_b = _letzter_prompt["user"]
check("N1: PFAD B enthaelt den No-Market-Block",
      "KEINE BELASTBARE MARKTDATENBASIS" in prompt_b)
check("N2: PFAD B enthaelt KEINEN verbindlichen Marktvergleich-Block",
      "DETERMINISTISCHER MARKTVERGLEICH" not in prompt_b)
check("N3: PFAD B enthaelt KEIN kanonisches Preisurteil",
      "KANONISCHES PREISURTEIL" not in prompt_b)
check("N4: PFAD B verbietet Preiseinstufung ausdruecklich",
      "Stufe den Angebotspreis NICHT ein" in prompt_b)
check("N5: PFAD B fordert die technische Analyse weiterhin ein",
      "VOLLSTAENDIG durch" in prompt_b or "VOLLSTÄNDIG durch" in prompt_b)
check("N6: PFAD B enthaelt weiterhin das DB-Profil (technischer Kontext)",
      "DB-Profil" in prompt_b)

lauf_kaufcheck(ma_gut, LLM_MIT_PREIS)
prompt_a = _letzter_prompt["user"]
check("N7: PFAD A enthaelt KEINEN No-Market-Block",
      "KEINE BELASTBARE MARKTDATENBASIS" not in prompt_a)
check("N8: PFAD A enthaelt den verbindlichen Marktvergleich-Block",
      "DETERMINISTISCHER MARKTVERGLEICH" in prompt_a)
check("N9: PFAD A enthaelt das kanonische Preisurteil",
      "KANONISCHES PREISURTEIL" in prompt_a)

print()
print("=== O. Marktanalyse-Module unveraendert erreichbar ===")

check("O1: research_status weiterhin importierbar und liefert research_failed",
      research_status(_leere_marktanalyse()) == "research_failed")
check("O2: bewerte_preis liefert ohne Median weiterhin 'unbekannt' ohne Dummy-Werte",
      bewerte_preis(None, 6500).verdict == "unbekannt"
      and bewerte_preis(None, 6500).median_eur is None)
check("O3: no_market_prompt_block ist nicht leer", len(no_market_prompt_block()) > 100)
check("O4: PFAD-A-Preisurteil unveraendert berechenbar",
      bewerte_preis(_gute_marktanalyse(), 6500).median_eur == 6800)

print()
print("=== P. Bericht enthaelt keine Preiskategorie ohne Basis ===")

bericht_b = ERG_NO_MARKET.get("bericht", "")
_PREISWORTE = re.compile(
    r"\b(marktgerecht|g[uü]nstiges? angebot|zu teuer|[üu]berteuert|fairer preis)\b",
    re.IGNORECASE)
check("P1: No-Market-Bericht enthaelt keine Preiskategorie",
      _PREISWORTE.search(bericht_b) is None)

print()
if _FEHLER:
    print(f"FEHLGESCHLAGEN: {len(_FEHLER)}")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE KAUFCHECK-P0-1-TESTS GRUEN")
