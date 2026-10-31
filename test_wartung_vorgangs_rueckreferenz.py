"""
F10-Folgeauftrag (2026-11-02, "Wartungsintervall bei eindeutiger Satz-
Rückreferenz erhalten"): real belegt am Lexus-Schweiz-Garantie-/Serviceheft
(tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf, Seite 15) --
"Lexus Care-Zwischenservice: alle 15.000 km oder einmal jährlich, je
nachdem, was zuerst eintritt. Dieser Service beinhaltet einen Öl- und
Filterwechsel ..." -- Intervall und Bauteil stehen in ZWEI aufeinander-
folgenden Sätzen, verbunden nur durch die generische Rückreferenz
"Dieser Service beinhaltet ...".

URSACHE (geprüft, nicht vermutet): `_extrahiere_fakten()` prüfte Intervall
(`_INTERVALL`) und Bauteil (`_wartung_vokabular()`) bisher nur INNERHALB
je eines einzelnen Satzes/Claim-Segments. Die bestehende F18-Rückreferenz-
Logik (`_rueckwaerts_erbfaehige_indizes()`/`_RUECKREFERENZ`) bindet einen
Wert an das FAHRZEUG (die Rückreferenz muss das Zielmodell nennen) -- "Dieser
Service beinhaltet ..." nennt aber KEIN Fahrzeug, sondern erläutert nur den
unmittelbar zuvor benannten VORGANG. Eine andere Achse, dieselbe Grundidee:
Distanz 1, echte Rückreferenz, kein Segmentbruch, keine Mehrdeutigkeit.

FIX: `_wartung_vorgangs_rueckreferenz_saetze()` (app/technical_research.py)
ergänzt -- NUR für kategorie=="wartung" -- zusätzliche, aus zwei
unmittelbar benachbarten Sätzen zusammengesetzte Kandidatensätze, wenn der
folgende Satz mit einer generischen Vorgangs-Rückreferenz beginnt UND der
vorherige Satz GENAU EIN Intervall trägt. Keine pauschale Satz-Zusammen-
führung, keine Lexus-/Fahrzeug-Sonderregel, keine Quellenaufwertung, keine
Schwellenlockerung -- die ORIGINAL-Sätze bleiben unverändert in der Liste
und werden bei Mehrdeutigkeit weiterhin korrekt abgelehnt.

    python scripts/test_isolated.py test_wartung_vorgangs_rueckreferenz.py
"""
from app.technical_research import (
    _extrahiere_fakten, _wartung_vorgangs_rueckreferenz_saetze, _RE_VORGANG_RUECKREFERENZ,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# ── A) Reales Lexus-Dokument: Gegenstand und Intervall werden zusammen erkannt ─
_LEXUS_CH_REAL = treffer(
    "https://tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf",
    "Lexus Garantie- und Serviceheft (Schweiz, Deutsch) -- Lexus Care-Wartungsplan",
    "LEXUS CARE-WARTUNGSPLAN Für ES 300h, IS 300h, LS 500, LS 500h, LC 500, "
    "LC 500h, NX 350h, NX 450h+, RX 300, RX 350, RX 350L, RX 450h, RX 450hL, "
    "RC 300h, RC F , UX 200 und UX 250h\n"
    "Lexus Care-Komplettservice: alle 30.000 km oder 2 Jahre, je nachdem, was "
    "zuerst eintritt. Lexus Care-Zwischenservice: alle 15.000 km oder einmal "
    "jährlich, je nachdem, was zuerst eintritt. Dieser Service beinhaltet "
    "einen Öl- und Filterwechsel sowie zusätzliche Inspektionen von Lenkung, "
    "Bremsen, Beleuchtung und Reifen.")
abgelehnt_a: list[dict] = []
fakten_a = _extrahiere_fakten([_LEXUS_CH_REAL], "wartung", marke="Lexus", modell="RC F",
                              baujahr=2017, abgelehnt=abgelehnt_a)
check("A1) genau ein Fakt erkannt (Öl- und Filterwechsel, 15.000 km)",
      len(fakten_a) == 1 and fakten_a[0].bauteil and "filterwechsel" in fakten_a[0].bauteil.lower(),
      str(fakten_a))
check("A2) das Intervall '15.000 km' steht in der Aussage",
      bool(fakten_a) and "15.000 km" in fakten_a[0].aussage, str(fakten_a))
check("A3) die ORIGINAL-Einzelsätze bleiben weiterhin korrekt abgelehnt "
      "(no_component/no_maintenance_interval) -- reine Ergänzung, kein Ersatz",
      any(a["grund"] == "no_component" for a in abgelehnt_a)
      and any(a["grund"] == "no_maintenance_interval" for a in abgelehnt_a), str(abgelehnt_a))

# ── B) Synthetische Kontrolle: anderes Fahrzeug, anderer Wartungsgegenstand ──
_SYNTHETISCH_BMW = treffer(
    "https://www.bmw.de/wartungsplan/3er", "BMW 3er Wartungsplan",
    "Beim BMW 3er gilt der Zwischenservice: alle 25.000 km oder einmal jährlich. "
    "Dieser Service umfasst einen Bremsflüssigkeitswechsel sowie eine Sichtprüfung.")
fakten_b = _extrahiere_fakten([_SYNTHETISCH_BMW], "wartung", marke="BMW", modell="3er", baujahr=2020)
check("B1) gleicher Aufbau (anderes Fahrzeug/Bauteil) wird ebenso generisch erkannt "
      "(kein Lexus-/RC-F-Spezialfall)",
      len(fakten_b) == 1 and fakten_b[0].bauteil and "bremsfl" in fakten_b[0].bauteil.lower(),
      str(fakten_b))

# ── C) Zwei mögliche Wartungsgegenstände im selben Satz: kein geratenes Intervall ─
_SYNTHETISCH_MEHRDEUTIG = treffer(
    "https://tools.lexus.ch/fileadmin/pdf_new/mehrdeutiger-testfall.pdf",
    "Lexus RC F Wartungsplan -- SYNTHETISCHER TESTFALL, keine reale Veroeffentlichung",
    "Lexus RC F: Komplettservice alle 30.000 km und Zwischenservice alle 15.000 km "
    "gelten beide. Dieser Service beinhaltet einen Öl- und Filterwechsel.")
abgelehnt_c: list[dict] = []
fakten_c = _extrahiere_fakten([_SYNTHETISCH_MEHRDEUTIG], "wartung", marke="Lexus", modell="RC F",
                              baujahr=2017, abgelehnt=abgelehnt_c)
check("C1) zwei Intervalle im selben vorherigen Satz -> KEIN geratenes Intervall "
      "(mehrdeutig, bleibt unbelegt)",
      len(fakten_c) == 0, str(fakten_c))
check("C2) der Bauteilsatz bleibt explizit als 'no_maintenance_interval' abgelehnt "
      "(kein stilles Verschwinden)",
      any(a["grund"] == "no_maintenance_interval" for a in abgelehnt_c), str(abgelehnt_c))

# ── D) Modell-/Abschnittswechsel: keine Übertragung ─────────────────────────
_SYNTHETISCH_ABSCHNITTSWECHSEL = treffer(
    "https://tools.lexus.ch/fileadmin/pdf_new/abschnittswechsel-testfall.pdf",
    "Lexus Wartungsplan -- SYNTHETISCHER TESTFALL, keine reale Veroeffentlichung",
    "Lexus RC F: Zwischenservice alle 15.000 km oder einmal jährlich.\n\n"
    "Mazda MX-5: Dieser Service beinhaltet einen Öl- und Filterwechsel.")
abgelehnt_d: list[dict] = []
fakten_d = _extrahiere_fakten([_SYNTHETISCH_ABSCHNITTSWECHSEL], "wartung", marke="Lexus",
                              modell="RC F", baujahr=2017, abgelehnt=abgelehnt_d)
check("D1) Absatzgrenze UND Modellwechsel (Mazda MX-5) verhindern die Übertragung "
      "auf das Zielmodell RC F -- kein Fakt",
      len(fakten_d) == 0, str(fakten_d))

# ── E) Direkter Funktionstest der Hilfsfunktion: Distanz-1, Segmentbruch ───
check("E1) echte Rückreferenz wird direkt erkannt",
      bool(_RE_VORGANG_RUECKREFERENZ.match(
          "Dieser Service beinhaltet einen Öl- und Filterwechsel.")))
check("E2) kein Treffer ohne erläuterndes Verb (nur Nennung, keine Rückreferenz)",
      not _RE_VORGANG_RUECKREFERENZ.match("Dieser Service ist beliebt."))
check("E3) _wartung_vorgangs_rueckreferenz_saetze() verlangt GENAU EIN Intervall "
      "im vorherigen Satz",
      _wartung_vorgangs_rueckreferenz_saetze(
          ["Komplettservice alle 30.000 km und Zwischenservice alle 15.000 km.",
           "Dieser Service beinhaltet einen Öl- und Filterwechsel."]) == [])
check("E4) bei genau einem Intervall wird korrekt zusammengefügt",
      len(_wartung_vorgangs_rueckreferenz_saetze(
          ["Zwischenservice alle 15.000 km.",
           "Dieser Service beinhaltet einen Öl- und Filterwechsel."])) == 1)

print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE WARTUNGS-VORGANGS-RUECKREFERENZ-TESTS GRUEN")
