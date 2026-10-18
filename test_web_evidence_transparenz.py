"""
Web-Evidence-Transparenz im generierten Bericht (Package E, Forensik-
Folgeauftrag).

Forensik-Befund: `EvidenceQuelle` existiert strukturiert im API-Modell
(`Insight.quellen`), bereits korrekt auf user-sichtbare, freigegebene Claims
beschränkt -- fehlte aber bisher im GENERIERTEN Markdown-Risikotext
(Rendering-Gap, kein Backend-Datenverlust). Dieser Test prüft die neue
Darstellung (`app.kaufcheck_bericht._quellen_zeile`): Domain + Qualitätslabel
sichtbar, volle URL/Query-Parameter NICHT, keine Quellenwand.

    python test_web_evidence_transparenz.py
"""
from app.kaufcheck_bericht import _quellen_zeile
from app.models import EvidenceQuelle

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


print("=== 1) Domain + Qualitätslabel sichtbar, keine volle URL/Tracking-Query ===")
quelle_mit_query = EvidenceQuelle(
    typ="web_technik", url="https://www.adac.de/pfad?utm_source=secret&session=abc123",
    titel="ADAC-Testbericht", qualitaet="Amtlich/Prüforganisation")
zeile = _quellen_zeile([quelle_mit_query])
check("1a Zeile enthält die Domain", "adac.de" in zeile)
check("1b Zeile enthält das Qualitätslabel", "Amtlich/Prüforganisation" in zeile)
check("1c KEINE volle URL/Query-/Trackingparameter in der Zeile",
      "utm_source" not in zeile and "session=" not in zeile and "?" not in zeile)

print("\n=== 2) Mehrere Quellen derselben Domain erscheinen nur EINMAL "
      "(keine vorgetäuschte Unabhängigkeit) ===")
q1 = EvidenceQuelle(typ="web_technik", url="https://www.adac.de/a", qualitaet="Fachmedien")
q2 = EvidenceQuelle(typ="web_technik", url="https://www.adac.de/b", qualitaet="Fachmedien")
zeile2 = _quellen_zeile([q1, q2])
check("2a 'adac.de' erscheint nur einmal in der Zeile", zeile2.count("adac.de") == 1)

print("\n=== 3) Zwei unabhängige Domains erscheinen beide ===")
q3 = EvidenceQuelle(typ="web_technik", url="https://www.motor1.com/c", qualitaet="Fachmedien")
zeile3 = _quellen_zeile([q1, q3])
check("3a beide Domains sichtbar", "adac.de" in zeile3 and "motor1.com" in zeile3)

print("\n=== 4) Ohne Quellen entsteht keine (leere) Quellen-Zeile ===")
check("4a keine Quellen -> leerer String", _quellen_zeile([]) == "")
check("4b None -> leerer String (robust gegen fehlendes Feld)", _quellen_zeile(None) == "")

print("\n=== 5) Quelle ohne URL wird ignoriert, erzeugt keinen Fehler ===")
q_ohne_url = EvidenceQuelle(typ="inserat", url=None, qualitaet=None)
check("5a keine Exception, leere Zeile bei fehlender URL", _quellen_zeile([q_ohne_url]) == "")

print()
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE WEB-EVIDENCE-TRANSPARENZ-TESTS GRUEN")
