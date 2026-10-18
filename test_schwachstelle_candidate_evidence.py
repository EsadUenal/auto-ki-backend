"""
Schwachstellen-Evidence-Schicht (Package B, Forensik-Folgeauftrag).

Production-Befund (Lexus RC F): 6 von 8 kuratierten "schwachstelle"-Treffern
scheiterten an `score_domain(...) < MIN_SCORE_FAKT` -- VOR jeder Claim-
Bewertung. Analog zur bereits bestehenden Wartungs-Evidence-Schicht
(`_wartung_candidate_fakten()`, app/technical_research.py) bewertet
`_schwachstelle_candidate_fakten()` solche Treffer jetzt WENIGSTENS, bevor sie
verworfen werden -- OHNE `MIN_SCORE_FAKT` zu senken und OHNE Domainlisten zu
erweitern.

Sicherheitsregel (identisch zur Wartungs-Schicht, bewusst NICHT aufgeweicht):
ein Low-Trust-Candidate kann NIEMALS allein -- auch nicht durch mehrere
uebereinstimmende Low-Trust-Quellen -- eine NEUE, unabhaengig sichtbare
"typische Schwachstelle" erzeugen. Er kann ausschliesslich einen bereits im
Hauptpfad (Score >= MIN_SCORE_FAKT) entstandenen WebFakt desselben Bauteils um
zusaetzliche Quellen anreichern.

GENERISCH getestet (erfundene Marke/Modell).

    python test_schwachstelle_candidate_evidence.py
"""
from app.models import EvidenceQuelle, WebFakt
from app.technical_research import _schwachstelle_candidate_fakten

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def treffer(url: str, titel: str, inhalt: str) -> dict:
    return {"url": url, "title": titel, "content": inhalt}


ZIEL = dict(marke="Testmarke", modell="Modellz", generation=None)

print("=== 1) Low-Trust-Candidate OHNE Hauptpfad-Anker -> bleibt Observability, "
      "wird NIE eigenstaendiger Fakt ===")
niedrige_quelle = [
    treffer("https://forum.beispiel-autos.de/thread/123", "Testmarke Modellz Erfahrungen",
            "Bei meiner Testmarke Modellz hatte ich Probleme mit der Wasserpumpe, die "
            "undicht wurde."),
]
fakten1, abgelehnt1, _esk1 = _schwachstelle_candidate_fakten(
    niedrige_quelle, marke=ZIEL["marke"], modell=ZIEL["modell"], generation=ZIEL["generation"],
    haupt_fakten=[])
check("1a keine neuen Fakten ohne Hauptpfad-Anker", fakten1 == [])
check("1b Candidate landet in abgelehnt mit eigenem, erkennbarem Grund",
      any(a.get("grund") == "schwachstelle_candidate_ohne_hauptpfad_anker" for a in abgelehnt1))

print("\n=== 2) Low-Trust-Candidate MIT passendem Hauptpfad-Anker -> reichert "
      "nur dessen Quellen an, erzeugt KEINEN zweiten Fakt ===")
anker = WebFakt(kategorie="schwachstelle", bauteil="Wasserpumpe",
                aussage="Die Wasserpumpe der Testmarke Modellz gilt als Schwachstelle.",
                confidence="mittel",
                quellen=[EvidenceQuelle(typ="web_technik", url="https://www.fachmedium.de/a",
                                        titel="Fachmedium", qualitaet="Fachmedien")])
fakten2, abgelehnt2, _esk2 = _schwachstelle_candidate_fakten(
    niedrige_quelle, marke=ZIEL["marke"], modell=ZIEL["modell"], generation=ZIEL["generation"],
    haupt_fakten=[anker])
check("2a weiterhin KEIN neuer, eigenstaendiger WebFakt", fakten2 == [])
check("2b der Hauptpfad-Anker wurde um die neue Quelle angereichert",
      any("forum.beispiel-autos.de" in q.url for q in anker.quellen))
check("2c die urspruengliche Hauptpfad-Quelle bleibt erhalten (keine Ersetzung)",
      any("fachmedium.de" in q.url for q in anker.quellen))

print("\n=== 3) Zwei unabhaengige Low-Trust-Quellen OHNE Anker -> trotz "
      "Uebereinstimmung weiterhin KEIN eigenstaendiger Fakt (Foren-Konsens "
      "allein genuegt nie) ===")
zwei_foren = [
    treffer("https://forum1.beispiel.de/t1", "Testmarke Modellz",
            "Die Testmarke Modellz hat oft Probleme mit der Wasserpumpe."),
    treffer("https://forum2.beispiel.de/t2", "Testmarke Modellz Forum",
            "Bei der Testmarke Modellz ist die Wasserpumpe eine bekannte Schwachstelle."),
]
fakten3, abgelehnt3, _esk3 = _schwachstelle_candidate_fakten(
    zwei_foren, marke=ZIEL["marke"], modell=ZIEL["modell"], generation=ZIEL["generation"],
    haupt_fakten=[])
check("3a kein eigenstaendiger Fakt trotz zwei uebereinstimmender Low-Trust-Quellen",
      fakten3 == [])

print("\n=== 4) Treffer >= MIN_SCORE_FAKT wird hier NICHT verarbeitet (bestehender "
      "Pfad bleibt unberuehrt) ===")
hohe_quelle = [
    treffer("https://www.adac.de/testmarke-modellz", "Testmarke Modellz",
            "Die Testmarke Modellz hat Probleme mit der Wasserpumpe."),
]
fakten4, abgelehnt4, _esk4 = _schwachstelle_candidate_fakten(
    hohe_quelle, marke=ZIEL["marke"], modell=ZIEL["modell"], generation=ZIEL["generation"],
    haupt_fakten=[])
check("4a High-Trust-Treffer wird von dieser Schicht komplett ignoriert "
      "(weder Fakt noch Reject von hier)", fakten4 == [] and abgelehnt4 == [])

print()
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE SCHWACHSTELLEN-CANDIDATE-EVIDENCE-TESTS GRUEN")
