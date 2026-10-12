"""
Package C / RC-W3: benigne/relativierte Aussagen duerfen keinen Defekt-Claim
erzeugen; Bauteilnamen verlieren ihre angehaengte Generations-/Revisions-/
Serienangabe.

BEFUND (echter, paid Mazda-MX-5-KaufCheck nach RC-W6 B4):

  FALL 1 (Lenkrad): die AUTO-BILD-Quelle relativiert einen moeglichen
  Unfallverdacht ("muss nicht auf einen Unfall hindeuten", "kann bereits so
  vom Band gekommen sein") - ENFAL erzeugte daraus trotzdem "bekannter
  Schwachpunkt" + Verkaeuferfrage, weil `_extrahiere_fakten()` nur auf
  `_PROBLEM_WORTE`-Substrings prueft ("schaden"), nie auf die Satzrichtung.

  FALL 2 (Getriebe-Generationen): "erste beiden Getriebe-Generationen" ist im
  Satz ein Bindestrich-Kompositum; `_anzeige_bauteil()` griff den kompletten
  Ausdruck als Bauteilnamen ab, statt nur "Getriebe" (die Generationsangabe
  gehoert in den Geltungsbereich, nicht in den Bauteilnamen).

GENERISCHE REGEL (app/technical_research.py::_RELATIVIERUNG,
`_ist_benign_relativiert`, `_BAUTEIL_SCOPE_ENDUNG`): kein Marken-/Modell-/
Motor-/Variantenwort, wirkt satzweise (nicht absatzweise) auf JEDE Quelle.

    python test_technical_research_benign_claims.py
"""
import asyncio
import re

from app.technical_research import (
    _anzeige_bauteil, _bauteil_vokabular, _extrahiere_fakten, _ist_benign_relativiert, _norm,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def _treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


print("\n=== 1) BENIGN / NEGATED: kein Defekt-Insight ===")
_BENIGNE_SAETZE = [
    "Ein schief stehendes Lenkrad muss nicht unbedingt auf einen Unfallschaden hindeuten.",
    "Dieses Geraeusch ist unproblematisch und kein bekanntes Problem.",
    "Das ist konstruktionsbedingt und voellig unbedenklich.",
    "Der Verschleiss an dieser Stelle gilt als unkritisch und harmlos.",
]
for satz in _BENIGNE_SAETZE:
    check(f"benign erkannt: {satz[:55]!r}...", _ist_benign_relativiert(satz))

print("\n=== 2) POSITIVE CLAIM: eindeutige Defektaussagen bleiben erkannt ===")
_POSITIVE_SAETZE = [
    "Die Wasserpumpe faellt bei diesem Modell haeufig aus und sollte geprueft werden.",
    "Das Getriebe neigt zu Defekten und macht sich durch Rucken bemerkbar.",
    "Korrosion tritt haeufig am Schweller auf, ein bekannter Schwachpunkt dieser Baureihe.",
]
for satz in _POSITIVE_SAETZE:
    check(f"NICHT benign: {satz[:55]!r}...", not _ist_benign_relativiert(satz))

print("\n=== 3) MIXED CONTEXT: echter Defekt bleibt, benigner Satz nicht ===")
treffer_mixed = [_treffer(
    "https://www.autobild.de/mazda-mx-5-ratgeber", "Mazda MX-5 ND Gebrauchtwagen-Ratgeber",
    "Ein schief stehendes Lenkrad muss nicht unbedingt auf einen Unfallschaden hindeuten. "
    "Manche Fahrzeuge kamen bereits werksseitig so vom Band. "
    "Bei den ersten beiden Getriebe-Generationen des MX-5 ND kann es zu Schaltproblemen "
    "kommen, eine Probefahrt ist dringend empfehlenswert.")]
fakten_mixed = _extrahiere_fakten(treffer_mixed, "schwachstelle", marke="Mazda", modell="MX-5",
                                  baujahr=2019)
check("genau 1 Fakt (nur Getriebe, kein Lenkrad)", len(fakten_mixed) == 1)
check("Bauteil ist 'Getriebe' (nicht 'Getriebe-Generationen')",
      bool(fakten_mixed) and fakten_mixed[0].bauteil == "Getriebe")
check("Geltungsbereich bewahrt den Generations-Hinweis separat",
      bool(fakten_mixed) and "generation" in (fakten_mixed[0].geltungsbereich or "").lower())
check("kein Fakt nennt 'Lenkrad'",
      not any("lenkrad" in (f.bauteil or "").lower() for f in fakten_mixed))

print("\n=== 4) COMPONENT: Normalisierung direkt an _anzeige_bauteil ===")
vok = _bauteil_vokabular()
satz_komponente = "Bei den ersten beiden Getriebe-Generationen des MX-5 ND kann es Probleme geben."
voc_treffer = [(m, s) for m, s in vok.items() if m in _norm(satz_komponente)][:1]
check("Vokabular-Muster 'getriebe' gefunden", bool(voc_treffer) and voc_treffer[0][1] == "getriebe")
bauteil_norm = _anzeige_bauteil(satz_komponente, voc_treffer[0][0]) if voc_treffer else None
check("Bauteilname normalisiert zu 'Getriebe'", bauteil_norm == "Getriebe")

print("\n=== 5) DOWNSTREAM: benigner Fakt erzeugt keine Besichtigungs-/Probefahrt-/"
      "Verkaeuferpunkte ===")
import app.recall_filter as _rf
_rf.get_rueckruf_referenzen_kurz = lambda: []

import app.kaufcheck as kc
from app.models import KaufCheckRequest
from app.technical_research import FixtureTechnicalResearchProvider, recherchiere_technisch


async def _stub_gemini(system, user_msg):
    return {"risiko_evidence_ids": []}


async def _no_market(*a, **kw):
    return []


def _fixtures_mazda_echtfall():
    return {
        "identitaet": [
            _treffer("https://www.adac.de/mazda-mx-5-nd", "Mazda MX-5 ND (seit 2015)",
                     "Die 4. Generation des Mazda MX-5 (ND, seit 2015): 2.0 SKYACTIV-G mit "
                     "184 PS, Benziner, Hinterradantrieb."),
            _treffer("https://www.auto-motor-und-sport.de/mazda-mx-5-nd", "Mazda MX-5 ND im Test",
                     "Mazda MX-5 ND (seit 2015) mit 2.0 SKYACTIV-G und 184 PS, Heckantrieb."),
        ],
        "schwachstelle": [
            _treffer("https://www.autobild.de/mazda-mx-5-ratgeber",
                     "Mazda MX-5 ND Gebrauchtwagen-Ratgeber",
                     "Ein schief stehendes Lenkrad muss nicht unbedingt auf einen "
                     "Unfallschaden hindeuten. Manche Fahrzeuge kamen bereits werksseitig "
                     "so vom Band. Bei den ersten beiden Getriebe-Generationen des MX-5 ND "
                     "kann es zu Schaltproblemen kommen, eine Probefahrt ist dringend "
                     "empfehlenswert."),
        ],
        "rueckruf": [], "wartung": [],
    }


async def _run_echtfall():
    orig_recherchiere = kc.recherchiere_technisch
    orig_tavily = kc.tavily_search_with_fallback
    orig_gemini = kc.call_gemini_json
    provider = FixtureTechnicalResearchProvider(_fixtures_mazda_echtfall())

    async def _recherchiere(req, baureihe_roh, identitaet, baureihe_gegatet, motor_match):
        return await recherchiere_technisch(req, baureihe_roh, identitaet, baureihe_gegatet,
                                            motor_match, provider=provider)

    kc.recherchiere_technisch = _recherchiere
    kc.tavily_search_with_fallback = _no_market
    kc.call_gemini_json = _stub_gemini
    try:
        req = KaufCheckRequest(
            marke="Mazda", modell="MX-5", baujahr=2019, kilometerstand=58_700,
            motor="2.0 SKYACTIV-G", kraftstoff="Benzin", leistung_ps=184,
            getriebe="Schaltgetriebe", preis_eur=22_900, unfallfrei="",
            vorbesitzer=2, tuev_bis="07/2027", verkaeuferart="haendler",
            servicehistorie="vollstaendig",
            beschreibung="Servicehistorie vollstaendig vorhanden. Tuning/Leistungssteigerung "
                        "nicht angegeben.")
        return await kc.run_kaufcheck(req)
    finally:
        kc.recherchiere_technisch = orig_recherchiere
        kc.tavily_search_with_fallback = orig_tavily
        kc.call_gemini_json = orig_gemini


_res = asyncio.run(_run_echtfall())
check("kein Insight mit Bauteil 'Lenkrad'",
      not any((getattr(i, "bauteil", None) or "").lower().startswith("lenkrad")
              for i in _res["insights"]))
check("kein Web-Hinweis-Titel/Satz nennt 'Lenkrad' als Schwachpunkt",
      not re.search(r"lenkrad[^\n]*schwachpunkt|schwachpunkt[^\n]*lenkrad",
                    _res["bericht"], re.IGNORECASE))
check("ein Insight mit Bauteil 'Getriebe' bleibt bestehen (echter Defekt erhalten)",
      any((getattr(i, "bauteil", None) or "").lower() == "getriebe" for i in _res["insights"]))
check("kein Bauteilname selbst lautet 'Getriebe-Generationen' (nur als Zitat/"
      "Geltungsbereich im Fliesstext erlaubt, nie als Bauteil-Label)",
      "„getriebe-generationen“" not in _res["bericht"].lower()
      and "**getriebe-generationen**" not in _res["bericht"].lower()
      and "### getriebe-generationen" not in _res["bericht"].lower())

print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE BENIGN-CLAIM-/KOMPONENTEN-NORMALISIERUNGS-TESTS GRUEN")
