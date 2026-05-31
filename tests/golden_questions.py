"""
Automatisierte Golden-Questions-Tests gegen den laufenden Server.
Ausführen: python tests/golden_questions.py

Setzt voraus:
  - Server läuft auf localhost:8000
  - GEMINI_API_KEY gesetzt
  - AUTO_KI_API_KEY gesetzt (oder Default dev-key-change-in-prod)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error

API_BASE = os.environ.get("AUTO_KI_BASE_URL", "http://localhost:8000/api/v1")
API_KEY  = os.environ.get("AUTO_KI_API_KEY", "dev-key-change-in-prod")

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

GRUEN = "\033[92m"
ROT   = "\033[91m"
GELB  = "\033[93m"
RESET = "\033[0m"


def chat(message: str) -> dict:
    body = json.dumps({"message": message, "verlauf": [], "stream": False}).encode("utf-8")
    req = urllib.request.Request(f"{API_BASE}/chat", data=body, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def check(
    label: str,
    response: dict,
    must_contain: list[str],
    quelle: str | None = None,
    any_of: list[str] | None = None,  # mind. EINES dieser Wörter muss vorkommen (OR-Logik)
):
    answer = response.get("answer", "")
    got_quelle = response.get("quelle", "")
    got_vertrauen = response.get("vertrauen", "")
    passed = True
    issues = []

    for term in must_contain:
        if term.lower() not in answer.lower():
            issues.append(f"'{term}' fehlt in Antwort")
            passed = False

    if any_of:
        if not any(term.lower() in answer.lower() for term in any_of):
            issues.append(f"Keiner dieser Begriffe gefunden: {', '.join(repr(t) for t in any_of)}")
            passed = False

    if quelle and got_quelle != quelle:
        issues.append(f"quelle={got_quelle!r} (erwartet {quelle!r})")
        passed = False

    status = f"{GRUEN}OK{RESET}" if passed else f"{ROT}FAIL{RESET}"
    print(f"  [{status}] {label}")
    if issues:
        for issue in issues:
            print(f"         {GELB}!{RESET} {issue}")
    print(f"         quelle={got_quelle}, vertrauen={got_vertrauen}")
    print(f"         Antwort (Auszug): {answer[:120].replace(chr(10),' ')}…")
    print()
    return passed


def main():
    print(f"\nAuto-KI Golden Questions — {API_BASE}\n{'='*60}\n")

    results = []

    # 1. Generationsvergleich: Niere + Motor müssen genannt werden
    print("1. F82 vs G82 Unterschied")
    try:
        r = chat("Was ist der Unterschied zwischen einem F82 und einem G82 M4?")
        results.append(check(
            "Optischer Unterschied + Motor (S55/S58)",
            r,
            must_contain=["S55", "S58"],
            any_of=["Niere", "Doppelniere", "Frontpartie", "Grille", "Scheinwerfer", "Optik", "Front"],
            quelle="datenbank",
        ))
    except Exception as e:
        print(f"  [{ROT}ERROR{RESET}] {e}\n")
        results.append(False)

    # 2. Exakte PS-Zahl — darf NICHT aus dem Modell kommen
    print("2. M4 Competition G82 PS")
    try:
        r = chat("Wie viel PS hat der M4 Competition G82?")
        results.append(check(
            "Exakt 510 PS aus DB",
            r,
            must_contain=["510"],
            quelle="datenbank",
        ))
    except Exception as e:
        print(f"  [{ROT}ERROR{RESET}] {e}\n")
        results.append(False)

    # 3. Motorprobleme F82 — Kurbelnabe muss erscheinen
    print("3. Motorprobleme F82")
    try:
        r = chat("Welche bekannten Motorprobleme hat der F82?")
        results.append(check(
            "Kurbelnabe in Antwort",
            r,
            must_contain=["Kurbelnabe"],
            quelle="datenbank",
        ))
    except Exception as e:
        print(f"  [{ROT}ERROR{RESET}] {e}\n")
        results.append(False)

    # 4. NCAP G82 — KI muss ehrlich sagen: kein Ergebnis
    print("4. Euro NCAP G82 — ehrliche Antwort")
    try:
        r = chat("Hat der M4 G82 ein Euro-NCAP-Ergebnis?")
        # KI darf KEINE Sternzahl erfinden; muss irgendetwas wie "nicht", "kein", "separat" sagen
        results.append(check(
            "Kein erfundenes NCAP-Ergebnis",
            r,
            must_contain=["nicht"],   # "nicht separat getestet" o.Ä.
        ))
        # Zusatz: Antwort darf keine Sterneanzahl 1-5 enthalten
        import re
        if re.search(r"\b[1-5]\s*Stern", r.get("answer", ""), re.IGNORECASE):
            print(f"  [{ROT}FAIL{RESET}] KI hat eine Sternzahl erfunden!\n")
            results[-1] = False
    except Exception as e:
        print(f"  [{ROT}ERROR{RESET}] {e}\n")
        results.append(False)

    # Zusammenfassung
    passed = sum(results)
    total  = len(results)
    color  = GRUEN if passed == total else ROT
    print(f"{'='*60}")
    print(f"Ergebnis: {color}{passed}/{total} bestanden{RESET}\n")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
