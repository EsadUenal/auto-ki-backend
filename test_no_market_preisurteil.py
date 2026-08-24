"""
Test: No-Market-Preisurteil-Guard (app/postprocess.neutralisiere_no_market_preisurteil)
-- KaufCheck-Backend-Freeze, P1-b. Kein LLM, keine Netzwerkaufrufe, kein Tavily.

Deckt die geforderten Faelle D-J ab:
  D  No-Market + erfundene "12.000-15.000 EUR" -> entfernt/neutralisiert
  E  No-Market + "Schnaeppchen" -> neutralisiert
  F  No-Market + "ueber Marktpreis" -> neutralisiert
  G  Market-Success mit echter Preisspanne -> bleibt unveraendert (Guard wird dort
     gar nicht aufgerufen — hier direkt an der Funktion demonstriert: andere
     Kostenangaben ohne Marktkontext-Wort bleiben unberuehrt)
  H  Wartungs-Wortlaut-Guard weiterhin gruen (test_kaufempfehlung_sync.py)
  I  Recommendation Floor weiterhin gruen (test_empfehlungs_floor.py)
  J  Langer Markdown-Bericht bleibt vollstaendig vorhanden (nur der Preissatz
     wird ersetzt, der Rest bleibt strukturell erhalten)

Ausfuehren:  python test_no_market_preisurteil.py
"""
import os
import sys
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_nomarket_"), "test.db")
sys.path.insert(0, ".")

from app.postprocess import neutralisiere_no_market_preisurteil, _NO_MARKET_NEUTRALSATZ  # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


# -- D) Konkrete erfundene Marktspanne --
print("-- D) Konkrete Marktspanne wird neutralisiert --")
satz_d = "Der Marktpreis liegt bei 12.000 - 15.000 EUR, das Angebot ist attraktiv."
neu_d = neutralisiere_no_market_preisurteil(satz_d)
check("D: konkrete Spanne verschwindet", "12.000" not in neu_d and "15.000" not in neu_d)
check("D2: Neutralsatz steht stattdessen da", _NO_MARKET_NEUTRALSATZ in neu_d)

satz_d2 = "Der geschaetzte Marktwert betraegt ca. 14.000 EUR."
neu_d2 = neutralisiere_no_market_preisurteil(satz_d2)
check("D3: 'Marktwert ca. 14.000 EUR' neutralisiert", "14.000" not in neu_d2)

# -- E) Qualitatives Urteil "Schnaeppchen" --
print("\n-- E) 'Schnaeppchen' wird neutralisiert --")
satz_e = "Bei diesem Preis handelt es sich um ein echtes Schnäppchen."
neu_e = neutralisiere_no_market_preisurteil(satz_e)
check("E: 'Schnäppchen' verschwindet", "schnäppchen" not in neu_e.lower())
check("E2: Neutralsatz ersetzt die Aussage", _NO_MARKET_NEUTRALSATZ in neu_e)

# Weitere Urteilswoerter aus dem im Prompt verbotenen Vokabular (no_market_prompt_block)
for wort, satz in [
    ("günstig", "Der Preis ist im Vergleich sehr günstig."),
    ("teuer", "Das Fahrzeug wirkt für diese Ausstattung eher teuer."),
    ("marktgerecht", "Der Angebotspreis erscheint marktgerecht."),
    ("angemessen", "Der Preis ist für den Zustand angemessen."),
    ("überteuert", "Das Angebot ist deutlich überteuert."),
    ("extrem günstig", "Der Preis ist extrem günstig für dieses Modell."),
]:
    neu = neutralisiere_no_market_preisurteil(satz)
    check(f"E3: Urteilswort {wort!r} neutralisiert", wort.split()[-1].lower() not in neu.lower()
          and _NO_MARKET_NEUTRALSATZ in neu)

# -- F) "über Marktpreis" / "unter Marktpreis" --
print("\n-- F) Richtungsangabe zum Marktpreis wird neutralisiert --")
satz_f1 = "Der Angebotspreis liegt über Marktpreis für vergleichbare Fahrzeuge."
neu_f1 = neutralisiere_no_market_preisurteil(satz_f1)
check("F: 'über Marktpreis' neutralisiert", "über marktpreis" not in neu_f1.lower())
check("F2: Neutralsatz steht da", _NO_MARKET_NEUTRALSATZ in neu_f1)

satz_f2 = "Das Fahrzeug liegt klar unter dem Marktwert."
neu_f2 = neutralisiere_no_market_preisurteil(satz_f2)
check("F3: 'unter dem Marktwert' neutralisiert", "marktwert" not in neu_f2.lower())

# -- G) Andere, NICHT marktpreisbezogene Euro-Angaben bleiben unberuehrt --
print("\n-- G) Grundierte Kostenangaben ohne Marktkontext bleiben unveraendert --")
satz_g1 = "Ein Zahnriemenwechsel kostet laut Fachwerkstatt ca. 800 EUR."
check("G: Reparaturkosten-Nennung (kein Marktkontext-Wort) bleibt unveraendert",
      neutralisiere_no_market_preisurteil(satz_g1) == satz_g1)

satz_g2 = "Der im Inserat genannte Preis beträgt 4.200 EUR."
check("G2: reine Inseratspreis-Nennung (kein Marktkontext-Wort) bleibt unveraendert",
      neutralisiere_no_market_preisurteil(satz_g2) == satz_g2)

zeile_tabelle = "| Preis | 4.200 EUR | nicht verfügbar | ✓ Plausibel |"
check("G3: Markdown-Tabellenzeile bleibt unangetastet (auch mit Euro-Zahl)",
      neutralisiere_no_market_preisurteil(zeile_tabelle) == zeile_tabelle)

# G4: Fair-Wort im FALSCHEN (nicht preisbezogenen) Kontext -- Grenzfall, bewusst
# dokumentiert: "fair" wird als Urteilswort IMMER neutralisiert (siehe Docstring
# in app/postprocess.py — bewusste Entscheidung, da im Preiskontext praktisch nie
# neutral gemeint und Teil des im Prompt verbotenen Vokabulars).
satz_g4 = "Der Verkäufer wirkte im Gespräch fair und transparent."
neu_g4 = neutralisiere_no_market_preisurteil(satz_g4)
check("G4 (dokumentierter Grenzfall): 'fair' wird unabhängig vom Kontext neutralisiert",
      _NO_MARKET_NEUTRALSATZ in neu_g4)

# -- Randfaelle --
print("\n-- Randfaelle --")
check("kein Text -> unveraendert", neutralisiere_no_market_preisurteil("") == "")
check("None -> None", neutralisiere_no_market_preisurteil(None) is None)
satz_ok = "Für dieses Fahrzeug konnte keine belastbare Marktpreisbasis ermittelt werden."
check("bereits neutraler Satz -> unveraendert (kein Urteilswort/keine Zahl+Kontext)",
      neutralisiere_no_market_preisurteil(satz_ok) == satz_ok)

# -- J) Langer Bericht bleibt strukturell vollstaendig --
print("\n-- J) Langer Bericht bleibt vollstaendig, nur der Preissatz wird ersetzt --")
BERICHT = (
    "## Fahrzeug erkannt\nBMW 3er G20, 320d, 2020.\n\n"
    "## Kaufempfehlung\n**KAUFEN NACH BESICHTIGUNG**\nTechnisch unauffällig.\n\n"
    "## Kritische Risiken\n- KBA-Rückrufe prüfen.\n\n"
    "## Preis-Einschätzung\nDer Marktpreis liegt bei 20.000 - 23.000 EUR, "
    "das Angebot ist günstig.\n\n"
    "## Inserat im Vergleich\n| Kriterium | Inserat | Erwartung | Plausibilität |\n"
    "| --- | --- | --- | --- |\n| Preis | 21.500 EUR | nicht verfügbar | ✓ |\n\n"
    "## Besichtigungs-Checkliste\n- [ ] Allgemeinzustand prüfen\n"
)
neu_j = neutralisiere_no_market_preisurteil(BERICHT)
check("J: alle Abschnittsüberschriften bleiben erhalten",
      all(h in neu_j for h in ("## Fahrzeug erkannt", "## Kaufempfehlung",
                                "## Kritische Risiken", "## Preis-Einschätzung",
                                "## Inserat im Vergleich", "## Besichtigungs-Checkliste")))
check("J2: unbeteiligte Abschnitte byte-identisch",
      "BMW 3er G20, 320d, 2020." in neu_j and "KBA-Rückrufe prüfen." in neu_j
      and "Allgemeinzustand prüfen" in neu_j)
check("J3: die erfundene Marktspanne ist weg", "20.000" not in neu_j and "23.000" not in neu_j)
check("J4: Tabellenzeile mit Inseratspreis bleibt exakt erhalten",
      "| Preis | 21.500 EUR | nicht verfügbar | ✓ |" in neu_j)
check("J5: Bericht insgesamt weiterhin lang/vollständig (kein Kahlschlag)",
      len(neu_j) > 300)

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER: " + ", ".join(FEHLER))
    raise SystemExit(1)
print("Alle No-Market-Preisurteil-Guard-Tests bestanden.")
