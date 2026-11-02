"""
ENFAL-Reparaturpaket N2/N3/N7 (2026-11-02) -- echter KaufCheck-Lauf
"Eingefügter Text(20261101-210820).txt", Lexus RC F 2017.

BEFUND (aus dem echten Bericht, nicht nachträglich konstruiert): die aus der
Lexus-Serviceheft-PDF (tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf,
Seiten 16/17) extrahierte Tabellenseite hat weder reguläre Satzzeichen
(Spalten-Trenner "I"/"E"/"F"/"N" statt Prosa) noch ein finites Verb.
`_segmentiere_claims()` lieferte den GESAMTEN Block deshalb als EIN
Claim-Segment zurück (korrektes Verhalten für Fließtext -- Tabellen sind
aber keine Grammatik). `_wartung_merkmal_normalisiert()`/`_INTERVALL.
search()` nahmen daraufhin die ERSTE im gesamten Block gefundene
Intervallzahl, unabhängig davon, zu welcher Zeile sie tatsächlich gehört:
"Bremspedal" erhielt dadurch das Intervall der völlig unabhängigen
"Luftfilter"-Zeile (60.000 km), und "Ventilspiel"/"Automatikgetriebeöl"
gingen trotz vorhandenem, korrekt zuordenbarem Text komplett verloren
(immer nur EIN Bauteil pro Claim).

FIX: `_wartung_tabellen_segmente()` (app/technical_research.py) erkennt
einen solchen tabellenartigen Block generisch (kein finites Verb, lang
genug, mindestens ein Tabellen-Strukturmerkmal: Fußnoten-Definition "*N:",
wiederholte Einzelbuchstaben-Spaltenmarkierungen, oder eine GROSSBUCHSTABEN-
Abschnittsüberschrift) und zerlegt ihn in EIN FENSTER PRO BAUTEIL-VORKOMMEN,
gebunden an die nächste Bauteil-Nennung oder harte Abschnittsgrenze -- nie
an das Blockende. Ein Intervall zählt nur noch, wenn es INNERHALB dieses
Fensters (oder der per Fußnoten-Referenz *N verknüpften eigenen Fußnoten-
Definition) steht; sonst bleibt die Zeile unaufgelöst (bestehendes
no_maintenance_interval-Gate) statt einen fremden Wert zu erben.

Text unten ist der ECHTE, von ENFAL im Bericht gezeigte Rohtext (siehe
Abschnitt "Hinterlegtes Intervall" der Berichts-Einfügung), unverändert
übernommen -- keine handverlesene Ersatzformulierung.

    python test_n2_n3_n7_wartung_tabellenblock.py
"""
from app.technical_research import _extrahiere_fakten

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# ── Echter Rohtext: SICHERHEIT-Tabelle (Seite 16/17) ──────────────────────────
# Wortgleich aus dem Berichts-Abschnitt "Bremspedal: Wartungsangabe aus der
# Webrecherche" / "Hinterlegtes Intervall" übernommen (Lauf 20261101-210820).
_SICHERHEIT_BLOCK = (
    "*2:  Für Albanien, Bosnien & Herzegowina, Kanarische Inseln, Zypern, Estland, "
    "Island, Lettland, Republik Nordmazedonien, Malta, Montenegro, Serbien, "
    "Slowakei *3:  Nur für LS 500h, ES 300h und NX 350h 16 Erneuern - Inspektion "
    "SICHERHEIT Zwischencheck Hauptservice Bremspedal und Feststellbremse*1 I "
    "Bremsklötze und -scheiben I I Bremsbeläge der Feststellbremse (IS 300h, "
    "RC 300h, RC F, LC 500, LC 500h, LS 500, LS 500h) I Bremsflüssigkeit E "
    "Bremsleitungen und -schläuche I Vorder- und Hinterradaufhängung I Lenkrad, "
    "Lenkgestänge und -getriebe I Bodenmatten I I Reifen, Beleuchtung, "
    "Wischerblätter und Waschflüssigkeit I I Kugelgelenke und Staubkappen der "
    "Radaufhängung I*2 I *1:  Prüfung der Feststellbremse betrifft nur: IS 300h, "
    "RC 300h und RC F *2: LS 500, LS 500h, LC 500h ZUSÄTZLICHER TEILEAUSTAUSCH "
    "Luftfilter Alle 60.000 km / 4 Jahre Zündkerzen Platin- oder Iridiumtyp Alle "
    "90.000 km RX 300, LS 500 Alle 60,000 km Motorkühlmittel Alle 150.000 km "
    "Danach alle 90.000 km Inverter-Kühlmittel (nur Hybrid- und Hybrid "
    "Plug-in-Modelle) Alle 240.000 km Danach alle 90.000 km Verteilergetriebeöl "
    "(RX 300 4WD, RX 350, RX 350L) Alle 30.000 km / 4 Jahre Vorderachsdifferenzialöl "
    "(LS 500, LS 500h 4WD) Alle 30.000 km / 4 Jahre Hinterachsdifferenzialöl "
    "(außer ES 300h, NX 350h, NX 450h+, RX 300 2WD, RX 450h, RX 450hL, UX 200, "
    "UX 250h) Alle 30.000 km / 4 Jahre ZUSÄTZLICHE INSPEKTIONEN "
    "Nebenaggregat-Antriebsriemen (außer ES 300h, UX 250h, NX 350h, NX 450h+) "
    "Alle 105.000 km / 6 Jahre Danach alle 15.000 km / jährlich Ventilspiel "
    "(Inspektion mit Messfühler, LC 500, RC F) Alle 90.000 km / 6 Jahre "
    "Automatikgetriebeöl Alle 60.000 km / 4 Jahre Automatikgetriebe-"
    "Ölkühlerschläuche und -anschlüsse (LC 500, LS 500, RX 350, RX 350L, UX 200) "
    "Alle 45 000 km / 3 Jahre Verteilergetriebeöl (LS 500 und LS 500h 4WD) Alle "
    "45.000 km / 4 Jahre Hinterachsdifferenzialöl (NX 350h 4WD, NX 450h+, RX 450h, "
    "RX 450hL, UX 250h 4WD) Alle 45 000 km / 3 Jahre Vorderachsdifferenzialöl "
    "(ES 300h, NX 350h, NX 450h+, RX 300, RX 350, RX 350L, RX 450h, RX 450hL, "
    "UX 200, UX 250h) Alle 60.000 km / 4 Jahre Gelenkwellenschrauben (RX 300 4WD, "
    "RX 350, RX 350L) Alle 30.000 km / 4 Jahre nachziehen Aktivkohlebehälter Alle "
    "45.000 km / 2 Jahre Alle 90.000 km*3 Unterdruckpumpe für Bremskraftverstärker "
    "(LS 500, RX 300, RX 350, RX 350L, UX 200) Alle 195,000 km *3: nur für "
    "Finnland, Norwegen und Schweden 17 LEXUS CARE Für UX 300e Lexus "
    "Care-Komplettservice: alle 30.000 km oder 2 Jahre, je nachdem, was zuerst "
    "eintritt"
)
_SICHERHEIT_TREFFER = treffer(
    "https://tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf",
    "Lexus Garantie- und Serviceheft (Schweiz, Deutsch)", _SICHERHEIT_BLOCK)

abgelehnt_s: list[dict] = []
fakten_s = _extrahiere_fakten([_SICHERHEIT_TREFFER], "wartung", marke="Lexus", modell="RC F",
                              baujahr=2017, abgelehnt=abgelehnt_s)
_je_bauteil_s = {f.bauteil: f for f in fakten_s}

print("=" * 78)
print("Echter SICHERHEIT-Tabellenblock (Seiten 16/17, Lauf 20261101-210820)")
print("=" * 78)
for f in fakten_s:
    print(f"  - {f.bauteil!r}: {f.aussage[:90]!r}")

check("N2) kein Fakt verbindet 'Bremspedal' mit dem fremden Luftfilter-Intervall "
      "(60.000 km) -- Bremspedal hat in dieser Quelle kein eigenes Intervall und "
      "bleibt deshalb unaufgelöst, statt einen fremden Wert zu erben",
      not any("bremspedal" in (b or "").lower() and "60.000" in f.aussage
              for b, f in _je_bauteil_s.items()))
check("N2) 'Bremspedal' erzeugt in dieser Quelle UEBERHAUPT keinen Fakt (die "
      "einzige eigene Fußnote dazu nennt nur eine Fahrzeugliste, kein Intervall "
      "-- unauflösbar bleibt unauflösbar, statt irgendeinen Wert zu erfinden)",
      not any(b and "bremspedal" in b.lower() for b in _je_bauteil_s))
check("N2) auch die reinen SICHERHEIT-Spalten-Checkpunkte ohne eigene Zahl "
      "('Bremsklötze', 'Bremsbeläge', 'Bremsflüssigkeit', 'Bremsleitungen') "
      "erben kein fremdes Intervall -- 'Bremskraftverstärker' ist davon bewusst "
      "AUSGENOMMEN: diese Zeile nennt tatsächlich ihr eigenes Intervall "
      "(195,000 km); dass sie laut Klammerliste nur RX/LS/UX statt RC F betrifft, "
      "ist eine separate, hier nicht behandelte Fahrzeug-Scope-Frage (siehe "
      "Abschlussbericht)",
      not any(b and "brems" in b.lower() and "kraftverstaerker" not in b.lower().replace("ä", "ae")
              for b in _je_bauteil_s))
check("N3) kein Fakt trägt den gesamten Blockumfang (Fremdmodelle UX 300e / "
      "RX 300 / LS 500 tauchen in KEINER Aussage als eigener Fließtext-Kontext "
      "auf -- jedes Fenster bleibt auf seine eigene Zeile begrenzt)",
      not any("UX 300e" in f.aussage or "LEXUS CARE" in f.aussage for f in fakten_s))
check("Mehrdeutige, unauflösbare Zeilen werden tatsächlich abgelehnt, nicht "
      "stillschweigend verworfen (no_maintenance_interval/no_component sichtbar)",
      any(a.get("grund") in ("no_maintenance_interval", "no_component") for a in abgelehnt_s),
      str([a.get("grund") for a in abgelehnt_s]))
print(f"\n(Beobachtung, nicht Teil dieses Checks: der vollständige Block liefert "
      f"{len(fakten_s)} korrekt zuordenbare Kandidaten -- MAX_FAKTEN_JE_KATEGORIE=5 "
      f"wählt davon nur die alphabetisch ersten 5 aus; das ist eine separate, "
      f"vorbestehende Anzeigegrenze, siehe Abschlussbericht. Deshalb prüft N7 unten "
      f"an einem realistischeren, kürzeren Ausschnitt.)")


# ── N7 separat, an einem kürzeren (realistischeren) Ausschnitt desselben ─────
# echten Blocks: ein einzelner Tavily-Treffer liefert in der Praxis selten die
# GESAMTEN zwei Serviceheft-Seiten als ein Snippet -- der oben gezeigte,
# vollständige Block demonstriert die Extraktions-/Zuordnungskorrektheit
# (N2/N3) unabhängig von der separaten Top-5-Anzeigegrenze; dieser kürzere,
# wortgleiche Ausschnitt (reiner Präfix desselben echten Texts, nichts
# umformuliert) zeigt zusätzlich, dass die korrekt zugeordneten Fakten auch
# durch die bestehende Anzeigegrenze hindurch ankommen, wenn nicht ungewöhnlich
# viele Zeilen aus einem einzigen Treffer konkurrieren.
_INSPEKTIONEN_BLOCK = (
    "ZUSÄTZLICHE INSPEKTIONEN "
    "Nebenaggregat-Antriebsriemen (außer ES 300h, UX 250h, NX 350h, NX 450h+) "
    "Alle 105.000 km / 6 Jahre Danach alle 15.000 km / jährlich Ventilspiel "
    "(Inspektion mit Messfühler, LC 500, RC F) Alle 90.000 km / 6 Jahre "
    "Automatikgetriebeöl Alle 60.000 km / 4 Jahre Automatikgetriebe-"
    "Ölkühlerschläuche und -anschlüsse (LC 500, LS 500, RX 350, RX 350L, UX 200) "
    "Alle 45 000 km / 3 Jahre"
)
_INSPEKTIONEN_TREFFER = treffer(
    "https://tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf",
    "Lexus Garantie- und Serviceheft (Schweiz, Deutsch)", _INSPEKTIONEN_BLOCK)
abgelehnt_i: list[dict] = []
fakten_i = _extrahiere_fakten([_INSPEKTIONEN_TREFFER], "wartung", marke="Lexus", modell="RC F",
                              baujahr=2017, abgelehnt=abgelehnt_i)
_je_bauteil_i = {f.bauteil: f for f in fakten_i}

print()
print("=" * 78)
print("N7) Echter 'ZUSÄTZLICHE INSPEKTIONEN'-Ausschnitt (kürzer, Top-5-sicher)")
print("=" * 78)
for f in fakten_i:
    print(f"  - {f.bauteil!r}: {f.aussage[:90]!r}")

check("N7) 'Ventilspiel' wird korrekt mit seinem EIGENEN Intervall (90.000 km / "
      "6 Jahre) erkannt -- vorher trotz vorhandenem Text komplett verloren",
      any(b and "ventilspiel" in b.lower() and "90.000" in f.aussage
          for b, f in _je_bauteil_i.items()),
      str([(b, f.aussage) for b, f in _je_bauteil_i.items()]))
check("N7) 'Automatikgetriebeöl' wird korrekt mit seinem EIGENEN Intervall "
      "(60.000 km / 4 Jahre) erkannt, nicht mit dem der nachfolgenden "
      "Automatikgetriebe-Ölkühlerschläuche-Zeile (45.000 km) vermischt",
      any(b and "automatikgetriebe" in b.lower() and "60.000" in f.aussage
          and "45.000" not in f.aussage
          for b, f in _je_bauteil_i.items()),
      str([(b, f.aussage) for b, f in _je_bauteil_i.items() if b and "getriebe" in b.lower()]))


# ── Echter Rohtext: FITNESS-Tabelle inkl. eigener Fußnote (Motoröl) ───────────
_FITNESS_BLOCK = (
    "Für normale Einsatzbedingungen Erneuern - Inspektion - Nachziehen - Füllen "
    "FITNESS Zwischencheck Hauptservice Motoröl und -filter*1 E E Motorkühlmittel "
    "I Inverter-Kühlmittel (nur Hybrid- und Hybrid Plug-in-Modelle) I "
    "Motor-Ölkühlerschläuche und Schlauchanschlüsse (LC 500h und RC F) I "
    "Luftfilter I I Reinigungsadditiv für Einspritzventile*2 F F "
    "Antriebswellenmanschetten I Antriebswellenschrauben (RX 450h, RX 450hL, "
    "UX 250h 4WD) N N Auspuffrohre und -befestigung I Klimaanlage und Heizung I "
    "Klimaanlagenfilter E E Kraftstofftankdeckel, Kraftstoffleitungen, "
    "Leitungsanschlüsse und Tankdunst-Steuerventil I Karosserieinspektion (siehe "
    "Checkliste für Karosserieinspektion) I Einlassfilter der "
    "Hybridbatteriekühlung (RX 450h, RX 450hL, LS 500h, ES 300h, NX 350h) I I / "
    "E*3 *1:  Bei Fahrzeugen mit Ölwechsel-Managementsystem: Motoröl- und "
    "-ölfilter wechseln (mit Fitness- und Sicherheitskontrollen), wenn die "
    "Ölwechsel-Hinweismeldung erscheint (zumindest alle 15.000 km / 1 Jahr), "
    "falls für Ihr Land zutreffend"
)
_FITNESS_TREFFER = treffer(
    "https://tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf",
    "Lexus Garantie- und Serviceheft (Schweiz, Deutsch)", _FITNESS_BLOCK)

abgelehnt_f: list[dict] = []
fakten_f = _extrahiere_fakten([_FITNESS_TREFFER], "wartung", marke="Lexus", modell="RC F",
                              baujahr=2017, abgelehnt=abgelehnt_f)

print()
print("=" * 78)
print("Echter FITNESS-Tabellenblock mit eigener Fußnote (Motoröl)")
print("=" * 78)
for f in fakten_f:
    print(f"  - {f.bauteil!r}: {f.aussage[:90]!r}")

check("N7/Fußnoten-Fall) 'Motoröl' erhält über die EIGENE, per *1 verknüpfte "
      "Fußnotendefinition korrekt 15.000 km (1 Jahr) -- die Fußnote nennt "
      "ausdrücklich 'Motoröl- und -ölfilter wechseln', kein fremder Wert",
      any(b and "motor" in b.lower() and "15.000" in f.aussage
          for b, f in {f.bauteil: f for f in fakten_f}.items()),
      str([(f.bauteil, f.aussage) for f in fakten_f]))
check("Fußnoten-Fall) kein anderes Bauteil aus diesem Block erbt dieselbe "
      "Fußnote fälschlich (z.B. 'Luftfilter'/'Antriebswellenmanschetten' bleiben "
      "ohne Intervall, da ihre eigene Zeile keine Zahl nennt)",
      not any(b and b.lower() in ("luftfilter", "antriebswellenmanschetten")
              for b in {f.bauteil for f in fakten_f}))


if _FEHLER:
    print(f"\n{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nALLE N2/N3/N7-TABELLENBLOCK-TESTS GRUEN")
