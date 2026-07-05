"""
Regressionstests für app/postprocess.py (Final Polish: sicheres Postprocessing).

Kein Netzwerk-/Gemini-Aufruf nötig — reine Funktionstests der deterministischen
Textbereinigung. Ausführen: python test_postprocess.py
"""

from app.postprocess import postprocess_answer

FEHLER = []


def check(name: str, eingabe: str, erwartet: str):
    ergebnis = postprocess_answer(eingabe)
    if ergebnis != erwartet:
        FEHLER.append(f"[FEHLER] {name}\n  Eingabe:  {eingabe!r}\n  Erwartet: {erwartet!r}\n  Erhalten: {ergebnis!r}")
    else:
        print(f"[OK] {name}")


# ---------- 1) Einleitungsfloskeln ----------
check(
    "Einleitungsfloskel 'Gerne'",
    "Gerne, der Tank fasst 55 Liter.",
    "der Tank fasst 55 Liter.",
)
check(
    "Einleitungsfloskel 'Klar'",
    "Klar! Der 320d hat 190 PS.",
    "Der 320d hat 190 PS.",
)
check(
    "Einleitungsfloskel 'Hier ist deine Analyse'",
    "Hier ist deine Analyse:\n## Fahrzeug erkannt\nBMW 320d",
    "## Fahrzeug erkannt\nBMW 320d",
)
check(
    "Kein Floskel-Treffer mitten im Text bleibt erhalten",
    "Der Motor läuft gerne etwas heißer im Sommer.",
    "Der Motor läuft gerne etwas heißer im Sommer.",
)

# ---------- 2) Schlussfloskeln ----------
check(
    "Schlussfloskel 'Ich hoffe das hilft'",
    "Der Tank fasst 55 Liter. Ich hoffe, das hilft!",
    "Der Tank fasst 55 Liter.",
)
check(
    "Schlussfloskel 'Bei weiteren Fragen'",
    "Der Ölwechsel kostet ca. 150 €. Bei weiteren Fragen stehe ich dir gerne zur Verfügung!",
    "Der Ölwechsel kostet ca. 150 €.",
)
check(
    "Echte Rückfrage am Ende bleibt erhalten (kein Floskel-Fehltreffer)",
    "Welche Motorisierung genau? Front- oder Allradversion?",
    "Welche Motorisierung genau? Front- oder Allradversion?",
)

# ---------- 3) Doppelte Leerzeilen ----------
check(
    "Drei Leerzeilen -> eine Leerzeile",
    "Absatz 1.\n\n\n\nAbsatz 2.",
    "Absatz 1.\n\nAbsatz 2.",
)
check(
    "Bereits eine Leerzeile bleibt unverändert",
    "Absatz 1.\n\nAbsatz 2.",
    "Absatz 1.\n\nAbsatz 2.",
)

# ---------- 4) Uneinheitliche Listen ----------
check(
    "Sternchen-Bullets -> Bindestrich",
    "* Punkt eins\n* Punkt zwei",
    "- Punkt eins\n- Punkt zwei",
)
check(
    "Punkt-Bullets (•) -> Bindestrich",
    "• Punkt eins\n• Punkt zwei",
    "- Punkt eins\n- Punkt zwei",
)
check(
    "Bereits Bindestrich-Bullets bleiben unverändert",
    "- Punkt eins\n- Punkt zwei",
    "- Punkt eins\n- Punkt zwei",
)
check(
    "Eingerückte Bullets behalten Einrückung",
    "Übergeordneter Punkt\n  * Unterpunkt",
    "Übergeordneter Punkt\n  - Unterpunkt",
)

# ---------- 5) Doppelte Leerzeichen ----------
check(
    "Doppelte Leerzeichen im Fließtext kollabieren",
    "Der Motor hat  190  PS.",
    "Der Motor hat 190 PS.",
)
check(
    "Führende Einrückung einer Nicht-Anfangszeile bleibt erhalten",
    "Erste Zeile.\n  Eingerückter Text  mit doppeltem Leerzeichen",
    "Erste Zeile.\n  Eingerückter Text mit doppeltem Leerzeichen",
)
check(
    "Tabellenzeilen werden nicht angefasst",
    "| Kriterium | Wert |\n|---|---|\n| PS   | 190  |",
    "| Kriterium | Wert |\n|---|---|\n| PS   | 190  |",
)

# ---------- 6) Unnötige Wiederholungen (exakte Duplikat-Zeilen) ----------
check(
    "Exakt doppelte Zeile wird entfernt",
    "Der Tank fasst 55 Liter.\nDer Tank fasst 55 Liter.\nWeiterer Satz.",
    "Der Tank fasst 55 Liter.\nWeiterer Satz.",
)
check(
    "Nicht-identische, ähnliche Zeilen bleiben beide erhalten",
    "Der Tank fasst 55 Liter.\nDer Tank fasst ca. 55 Liter.",
    "Der Tank fasst 55 Liter.\nDer Tank fasst ca. 55 Liter.",
)

# ---------- 7) Markdown-Unsauberkeiten / Code-Fences ----------
check(
    "Code-Fence-Inhalt bleibt unverändert (auch bei doppelten Leerzeichen)",
    "Text davor.\n```\nA  B   C\n```\nText danach.",
    "Text davor.\n```\nA  B   C\n```\nText danach.",
)

# ---------- 8) Trailing Whitespace pro Zeile ----------
check(
    "Trailing Whitespace pro Zeile entfernt",
    "Erste Zeile.   \nZweite Zeile.\t",
    "Erste Zeile.\nZweite Zeile.",
)

# ---------- 9) Führende/nachfolgende Leerzeichen am Gesamttext ----------
check(
    "Gesamttext wird getrimmt",
    "\n\n  Der Tank fasst 55 Liter.  \n\n",
    "Der Tank fasst 55 Liter.",
)

# ---------- 10) Realistischer Kaufcheck-Bericht (Struktur bleibt erhalten) ----------
_KAUFCHECK_ROH = (
    "Gerne, hier ist deine Analyse:\n\n"
    "## Fahrzeug erkannt\n"
    "BMW 320d G20, Baujahr 2020\n\n\n\n"
    "## Kaufempfehlung\n"
    "**NUR MIT WERKSTATTPRÜFUNG**\n"
    "* Bekannte Kette-Problem beim Motor\n"
    "* Preis liegt  im Rahmen\n\n"
    "Ich hoffe, das hilft!"
)
_KAUFCHECK_ERWARTET = (
    "## Fahrzeug erkannt\n"
    "BMW 320d G20, Baujahr 2020\n\n"
    "## Kaufempfehlung\n"
    "**NUR MIT WERKSTATTPRÜFUNG**\n"
    "- Bekannte Kette-Problem beim Motor\n"
    "- Preis liegt im Rahmen"
)
check("Realistischer Kaufcheck-Bericht komplett bereinigt", _KAUFCHECK_ROH, _KAUFCHECK_ERWARTET)

# ---------- 11) Leerer/None-Text bricht nicht ----------
check("Leerer String bleibt leer", "", "")

# ---------- 12) Idempotenz: zweimaliges Anwenden ändert nichts mehr ----------
einmal = postprocess_answer(_KAUFCHECK_ROH)
zweimal = postprocess_answer(einmal)
if einmal != zweimal:
    FEHLER.append(f"[FEHLER] Idempotenz verletzt\n  1x: {einmal!r}\n  2x: {zweimal!r}")
else:
    print("[OK] Idempotenz (zweimaliges Anwenden liefert identisches Ergebnis)")

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER:\n")
    for f in FEHLER:
        print(f)
        print()
    raise SystemExit(1)
else:
    print("Alle Postprocessing-Regressionstests bestanden.")
