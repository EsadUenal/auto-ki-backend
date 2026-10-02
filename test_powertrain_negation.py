"""
Release-Hardening (Continuation, Blocker 2): negation-sichere
Powertrain-Erkennung. KEIN Netzwerk, KEIN LLM-Call.

BEFUND (selbst gefunden, letzte Runde): `app.kraftstoff_powertrain.
powertrain_aus_freitext` prüfte Hybrid-Vokabular per bloßer Teilzeichenketten-
Suche, ohne Verneinung zu berücksichtigen, UND vor dem ICE-Vokabular. "ohne
Hybridisierung" enthält die Teilzeichenkette "hybrid" und wurde deshalb als
"HEV" zurückgegeben — das genaue Gegenteil der Aussage.

FIX: jede Wortgruppe wird jetzt für sich mit einer Verneinungsprüfung im
selben Teilsatz abgeglichen (`_positiv_erwaehnt`). Eine Verneinung liefert
NIE automatisch das Gegenteil (z.B. ICE) — nur eine eigene, ausdrückliche
ICE-/Verbrenner-Formulierung tut das. Generisch: kein Test-spezifischer
Sonderfall, dieselbe Funktion für jede Wortgruppe.

  A) "ohne Hybridisierung"            -> None (nie HEV/MHEV/PHEV)
  B) "kein Hybrid"                    -> None (nie HEV)
  C) "kein Mildhybrid"                -> None (nie MHEV)
  D) "kein Plug-in-Hybrid"            -> None (nie PHEV)
  E) "reiner Verbrenner"              -> ICE
  F) "konventioneller Verbrennungsmotor" -> ICE
  G) "Mildhybrid"/"Mild-Hybrid"       -> MHEV
  H) "Plug-in-Hybrid"                 -> PHEV
  I) "vollelektrisch"                 -> BEV
  J) "nicht elektrifiziert"           -> None (nie MHEV/PHEV/HEV)
  K) Verneinung + spätere positive Erwähnung im SELBEN Text (anderer
     Teilsatz) -> die positive Erwähnung zählt weiterhin
  L) zwei starke, widersprechende Quellen -> Konflikt (None), keine Willkür
     (End-to-End über werte_identitaet_aus, dieselbe Infrastruktur wie
     test_phase_completeness_powertrain.py Abschnitt B)

    python test_powertrain_negation.py
"""
from app.kraftstoff_powertrain import powertrain_aus_freitext

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, info: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f"   {info}" if info else ""))
    if not bedingung:
        _FEHLER.append(name)


# ══════════════════════════════════════════════════════════════════════════
print("\n=== A-D) Verneinte Elektrifizierung wird NIE zum positiven Hybrid-Wert ===")
faelle_negation = [
    ("Der Antrieb erfolgt ohne Hybridisierung.", None, "A"),
    ("Es handelt sich um ein Fahrzeug ohne jede Hybridisierung.", None, "A2"),
    ("Das Fahrzeug hat keinen Hybrid.", None, "B"),
    ("Kein Mildhybrid verbaut.", None, "C"),
    ("Dieses Modell ist kein Mildhybrid.", None, "C2"),
    ("Es ist kein Plug-in-Hybrid.", None, "D"),
    ("Kein Plug-in-Hybrid erhältlich.", None, "D2"),
    ("Das Fahrzeug ist nicht elektrifiziert.", None, "J"),
]
for text, erwartet, label in faelle_negation:
    ergebnis = powertrain_aus_freitext(text)
    check(f"{label}: {text!r} -> {erwartet!r}", ergebnis == erwartet, f"erhalten={ergebnis!r}")


# ══════════════════════════════════════════════════════════════════════════
print("\n=== E-I) Positive, ausdrückliche Formulierungen lösen weiterhin korrekt auf ===")
faelle_positiv = [
    ("Es handelt sich um einen reinen Verbrenner.", "ICE", "E"),
    ("Das Fahrzeug nutzt einen konventionellen Verbrennungsmotor.", "ICE", "F"),
    ("Der Antrieb ist als Mildhybrid ausgeführt.", "MHEV", "G1"),
    ("Der Antrieb ist als Mild-Hybrid ausgeführt.", "MHEV", "G2"),
    ("Es ist als Plug-in-Hybrid erhältlich.", "PHEV", "H"),
    ("Das Fahrzeug fährt vollelektrisch.", "BEV", "I1"),
    ("Dies ist ein reines Elektroauto.", "BEV", "I2"),
]
for text, erwartet, label in faelle_positiv:
    ergebnis = powertrain_aus_freitext(text)
    check(f"{label}: {text!r} -> {erwartet!r}", ergebnis == erwartet, f"erhalten={ergebnis!r}")


# ══════════════════════════════════════════════════════════════════════════
print("\n=== K) Verneinung in einem Teilsatz blockiert NICHT eine andere, positive "
      "Erwähnung im selben Text ===")
# Generischer Kontexttest: die Verneinungsprüfung ist auf den EIGENEN
# Teilsatz begrenzt (Satzzeichen-Grenze), überschreibt also nicht den ganzen
# Text. Realistisches Beispiel: ein Artikel schließt zuerst einen Mildhybrid
# aus und nennt danach ausdrücklich, dass es als Plug-in-Hybrid angeboten wird.
text_gemischt = "Dieses Modell ist kein Mildhybrid. Es ist jedoch als Plug-in-Hybrid erhältlich."
check("K1 PHEV wird trotz vorangehender Mildhybrid-Verneinung im selben Text erkannt",
      powertrain_aus_freitext(text_gemischt) == "PHEV")
text_gemischt2 = "Dieses Modell ist kein Plug-in-Hybrid. Stattdessen ist es ein reiner Verbrenner."
check("K2 ICE wird trotz vorangehender Plug-in-Hybrid-Verneinung im selben Text erkannt",
      powertrain_aus_freitext(text_gemischt2) == "ICE")


# ══════════════════════════════════════════════════════════════════════════
print("\n=== L) Zwei starke, widersprechende Quellen -> Konflikt, keine Willkür ===")
# Dieselbe Infrastruktur wie test_phase_completeness_powertrain.py Abschnitt
# B6 — hier zusätzlich mit einer VERNEINTEN Gegenaussage, um zu beweisen,
# dass Verneinung und Konsens-Konflikt unabhängig und beide korrekt greifen.
from app.technical_research import werte_identitaet_aus

ZIEL = dict(marke="Testmarke", modell="Gamma", baujahr=2021, motor=None)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


treffer_widerspruch = [
    treffer("https://www.adac.de/testmarke-gamma", "Testmarke Gamma im Test",
            "Die Testmarke Gamma ist ein reiner Verbrenner ohne jede Hybridisierung."),
    treffer("https://www.auto-motor-und-sport.de/testmarke-gamma", "Testmarke Gamma Daten",
            "Die Testmarke Gamma ist als Mildhybrid ausgeführt."),
]
ident_widerspruch, _ = werte_identitaet_aus(treffer_widerspruch, ZIEL)
check("L1 powertrain bleibt None (ICE vs. MHEV, kein 'erstbestes Signal gewinnt')",
      ident_widerspruch.powertrain is None)
check("L2 'powertrain' erscheint NICHT in feldwerte (kein willkürlich akzeptierter Wert)",
      "powertrain" not in ident_widerspruch.feldwerte)


print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE POWERTRAIN-NEGATION-TESTS GRUEN")
