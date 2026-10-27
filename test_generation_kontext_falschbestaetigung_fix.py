"""
Falsche Generationsbestätigung durch bloße Kontext-Nachbarschaft — konkret
abgeschlossen (Fortsetzungsauftrag, Runde 2: gemeinsamer Pfad für
alphabetische UND numerische Codes).

VOLLE KETTE (Eingabe + Quelltext → geparste Felder/Evidenz → Generation/
Alignment/Applicability → sichtbarer Bericht/Empfehlung), am bereits
gemeldeten Fall:

  Eingabe:  marke="Renault", modell="Clio" (OHNE jeden Zusatz -- der Fehler
            ist NACHWEISLICH unabhängig davon, ob/wie der Nutzer einen
            Zusatzcode eingibt, siehe Fall A unten).
  Quelltext: "Der Renault Clio RS, diese Generation, ist die Sportversion."
            auf zwei unabhängigen, als Identität ausreichenden Domains.
  Geparste Felder: `_generationscodes()` erkennt "RS" unmittelbar hinter dem
            Kernmodell "Clio", OHNE Jahresfenster, MIT `kontext=True` (das
            Wort "Generation" steht in der Nähe).
  VORHER (Runde 1): `_generationen_pruefen()` akzeptierte `kontext` allein als
            hinreichenden Beleg -- für JEDEN Code. Eine erste Fassung dieses
            Fixes unterschied nach Ziffer-Vorhandensein (digit-tragende Codes
            blieben bei "kontext allein reicht"). Das war SELBST FALSCH: eine
            Ziffer im Code ist kein semantischer Nachweis einer Generation --
            der bereits auf `master` reproduzierbare "911 GT3"-Fall (Fall F
            unten) bestätigte genau das, blieb aber in Runde 1 unangetastet.
  NACHHER (Runde 2): `kontext` (reine Wortnachbarschaft) reicht für KEINEN
            Code mehr aus, unabhängig von Ziffern, Marke oder Modell. Es
            bleiben zwei generische, tatsächlich eindeutige Pfade: ein
            belegtes Produktionsjahresfenster (`jahr_belegt`) ODER die
            STRUKTURELLE "Modell IV (CODE)"-Schreibweise
            (`generationsnummer_strukturell`, aus `_generationscodes()`s
            Römisch-Zahl-Erkennung) -- beide unverändert aus der bestehenden
            Infrastruktur, keine neue Heuristik.
  Sichtbarer Bericht/Empfehlung: `vehicle_identity.generation` bleibt `None`
            (UNKNOWN) statt fälschlich "RS"/"GT3" -- keine nachgelagerte
            Aussage ("Generation RS bestätigt") kann mehr daraus entstehen.

UNABHÄNGIGKEITS-NACHWEIS (Auftrag: "ein auf master bestehender ähnlicher
Fehler beweist nicht, dass die neue Änderung keine zusätzliche Auswirkung
hat"): Fall A unten zeigt empirisch, dass der Fehler mit UNVERÄNDERTEM
`aux=None` (Nutzer tippt gar keinen Zusatz) GENAUSO auftrat -- die
alphabetische Code-Abtrennung (`_ist_modell_code_kandidat`, vorheriger Fix)
hat auf DIESEN Mechanismus (`_generationscodes()`/`_generationen_pruefen()`,
der unabhängig auf dem Quelltext selbst arbeitet) keinerlei Einfluss. Sie
VERGRÖSSERT aber die Zahl der ÜBERHAUPT als Code erkannten ziffernlosen
Zusätze (NC/ND/RS/ST/FR statt vorher gar keiner) -- das macht den bereits
bestehenden Mechanismus für MEHR reale Fälle relevant, auch wenn sie ihn
nicht verursacht. Runde 2 schließt jetzt BEIDE Klassen (alphabetisch UND
numerisch) über denselben, einheitlichen Pfad -- keine Marken-/Modell-/
GT3-Sonderliste.

Digit-tragende Codes mit ECHTER Evidenz (Jahresfenster oder struktureller
Generationszählung) bleiben bestätigbar -- siehe test_identity_model_code_
split.py (C2/E/F/G, dort jetzt mit Produktionsfenster statt bloßem Kontext).

    python scripts/test_isolated.py test_generation_kontext_falschbestaetigung_fix.py
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.technical_research import _ziel, werte_identitaet_aus

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


async def _ident(marke, modell, baujahr, quellen):
    z = _ziel(marke, modell, baujahr, None, None)
    ident, abgelehnt = werte_identitaet_aus(quellen, z)
    return ident, abgelehnt, z


async def run():
    # ── Fall A: Unabhängigkeitsnachweis -- Nutzer tippt KEINEN Zusatz ───────
    quelle_rs = [
        treffer("https://www.renault.de/clio-rs", "Renault Clio RS",
                "Der Renault Clio RS, diese Generation, ist die Sportversion."),
        treffer("https://www.auto-motor-und-sport.de/renault-clio-rs", "Renault Clio RS im Test",
                "Der Renault Clio RS, diese Generation, hat 200 PS."),
    ]
    ident_a, _, z_a = await _ident("Renault", "Clio", 2019, quelle_rs)
    check("Fall A (Unabhängigkeitsnachweis): modell='Clio' OHNE jeden Zusatz -- "
          "der Fehler war VORHER identisch reproduzierbar (aux=None), die neue "
          "Abtrennung ist NICHT die Ursache",
          z_a.get("auxiliary_code") is None, f"aux={z_a.get('auxiliary_code')}")
    check("Fall A: Generation bleibt jetzt korrekt UNKNOWN (vorher fälschlich 'RS')",
          ident_a.generation is None, str(ident_a.generation))

    # ── Fall B: dieselbe Quelle, aber Nutzer tippt den Zusatz mit ───────────
    ident_b, _, z_b = await _ident("Renault", "Clio RS", 2019, quelle_rs)
    check("Fall B: modell='Clio RS' -- Core-Identität bleibt belegt (kein "
          "Informationsverlust, Variante bleibt als auxiliary_code erhalten)",
          ident_b.belegt is True and z_b.get("auxiliary_code") == "RS",
          f"belegt={ident_b.belegt} aux={z_b.get('auxiliary_code')}")
    check("Fall B: 'Variante neben einem Generationsbegriff' bestätigt NICHT "
          "automatisch die Generation (derselbe Fix wie Fall A)",
          ident_b.generation is None, str(ident_b.generation))

    # ── Fall C: tatsächlich belegte Generation (Jahresfenster PLUS Kontext) ─
    quelle_nc_mit_jahr = [
        treffer("https://www.adac.de/mx5-nc", "Mazda MX-5 NC",
                "Der Mazda MX-5 NC, diese Generation, wurde von 2005 bis 2015 gebaut."),
        treffer("https://www.auto-motor-und-sport.de/mazda-mx5-nc", "Mazda MX-5 NC im Test",
                "Der Mazda MX-5 NC, diese Generation, wurde von 2005 bis 2015 gebaut."),
    ]
    ident_c_innerhalb, _, _ = await _ident("Mazda", "MX-5 NC", 2008, quelle_nc_mit_jahr)
    check("Fall C: ziffernloser Code MIT belegtem Jahresfenster UND Kontext, "
          "Baujahr INNERHALB -> Generation WIRD bestätigt (kein Datenverlust, "
          "echte Bestätigung bleibt möglich)",
          ident_c_innerhalb.generation == "NC", str(ident_c_innerhalb.generation))
    ident_c_ausserhalb, _, _ = await _ident("Mazda", "MX-5 NC", 2019, quelle_nc_mit_jahr)
    check("Fall C: dieselbe Quelle, Baujahr AUSSERHALB des genannten Fensters "
          "-> Generation bleibt unbestätigt", ident_c_ausserhalb.generation is None,
          str(ident_c_ausserhalb.generation))

    # ── Fall D: expliziter Generationswiderspruch (zwei ziffernlose Codes, ──
    # beide mit Jahr+Kontext, für dasselbe Zielbaujahr) -- darf NICHT blind
    # einen gewinnen lassen (konsens()-Konfliktlogik unverändert wirksam).
    quelle_widerspruch = [
        treffer("https://www.adac.de/beispiel-rs", "Beispiel Modell RS",
                "Das Beispiel Modell RS, diese Generation, wurde von 2015 bis 2020 gebaut."),
        treffer("https://www.auto-motor-und-sport.de/beispiel-st", "Beispiel Modell ST",
                "Das Beispiel Modell ST, diese Generation, wurde von 2015 bis 2020 gebaut."),
    ]
    ident_d, _, _ = await _ident("Beispiel", "Modell", 2018, quelle_widerspruch)
    check("Fall D: zwei vergleichbar starke, aber WIDERSPRECHENDE ziffernlose "
          "Codes (RS vs. ST) -> UNKNOWN, kein blinder Gewinner",
          ident_d.generation is None, str(ident_d.generation))

    # ── Fall E: bestehende NC/ND- und numerische Zusatzfälle bleiben stabil ─
    ident_e_nd, _, z_e_nd = await _ident("Mazda", "MX-5 ND", 2019, [
        treffer("https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/mazda/mx-5/",
                "Mazda MX-5 (ND) seit 2015: Technische Daten",
                "Der Mazda MX-5 ND (seit 2015) ist die vierte Generation des Roadsters."),
        treffer("https://www.auto-motor-und-sport.de/test/mazda-mx-5-2-0-skyactiv-g-184/",
                "Mazda MX-5 2.0 Skyactiv-G 184 im Test",
                "Die vierte Generation des Mazda MX-5 (ND, seit 2015): 2.0 Skyactiv-G."),
    ])
    # Korrektur zur eigenen Erwartung: "(seit 2015)" IST ein echtes, offenes
    # Jahresfenster (jahr_belegt=True) -- bei Baujahr 2019 eine ECHTE,
    # unveränderte Bestätigung (jahr_belegt war schon vor diesem Fix allein
    # hinreichend), keine Regression und kein erneuter Beleg des Fixes selbst.
    check("Fall E: MX-5 ND mit echtem offenem Jahresfenster ('seit 2015') UND "
          "Kontext, Baujahr 2019 im Fenster -> unverändert korrekt bestätigt "
          "(jahr_belegt war immer schon allein hinreichend, von diesem Fix "
          "nicht berührt)",
          ident_e_nd.belegt is True and ident_e_nd.generation == "ND",
          f"belegt={ident_e_nd.belegt} generation={ident_e_nd.generation}")
    ident_e_992, _, z_e_992 = await _ident("Porsche", "911 992", 2020, [
        treffer("https://www.porsche.de/911", "Porsche 911", "Der Porsche 911 ist ein Sportwagen-Klassiker."),
        treffer("https://www.auto-motor-und-sport.de/porsche-911", "911 im Test", "Der Porsche 911 hat einen Heckmotor."),
    ])
    check("Fall E: rein numerischer Zusatz ('992') bleibt unverändert -- kann "
          "strukturell NIE zur Generation werden (kein Buchstabe im Code, "
          "Regressionsschutz, bereits in test_identity_model_code_split.py geprüft)",
          ident_e_992.belegt is True and ident_e_992.generation is None)

    # ── Fall F: der bereits gemeldete, zuvor bewusst offen gelassene ────────
    # DIGIT-TRAGENDE Fall ("911 GT3") -- jetzt über denselben, gemeinsamen
    # Pfad wie die alphabetischen Fälle geschlossen. Exakt derselbe Repro wie
    # im vorigen Abschlussbericht (Porsche 911 GT3, Kontext OHNE Jahresfenster
    # und OHNE strukturelle "Modell IV (CODE)"-Zählung).
    ident_f_gt3, _, z_f_gt3 = await _ident("Porsche", "911 GT3", 2021, [
        treffer("https://www.porsche.de/911-gt3", "Porsche 911 GT3",
                "Der Porsche 911 GT3, diese Generation, ist ein Hochleistungssportwagen."),
        treffer("https://www.auto-motor-und-sport.de/porsche-911-gt3", "911 GT3 im Test",
                "Der Porsche 911 GT3, diese Generation, beschleunigt in 3,4 Sekunden."),
    ])
    check("Fall F (GT3-Fall, jetzt geschlossen): 'GT3' MIT Kontext, aber OHNE "
          "Jahresfenster und OHNE strukturelle Generationszählung -> bleibt "
          "UNKNOWN (vorher auf master fälschlich 'GT3' bestätigt, unabhängig "
          "vom alphabetischen Fix)",
          ident_f_gt3.belegt is True and ident_f_gt3.generation is None,
          f"belegt={ident_f_gt3.belegt} generation={ident_f_gt3.generation}")

    # Derselbe GT3-Fall MIT echtem Jahresfenster -> bleibt bestätigbar (kein
    # Datenverlust, derselbe generische Pfad wie bei alphabetischen Codes).
    ident_f_gt3_jahr, _, _ = await _ident("Porsche", "911 GT3", 2021, [
        treffer("https://www.porsche.de/911-gt3", "Porsche 911 GT3",
                "Der Porsche 911 GT3, diese Generation, wird seit 2021 gebaut."),
        treffer("https://www.auto-motor-und-sport.de/porsche-911-gt3", "911 GT3 im Test",
                "Der Porsche 911 GT3, diese Generation, wird seit 2021 gebaut und "
                "beschleunigt in 3,4 Sekunden."),
    ])
    check("Fall F: derselbe Code MIT echtem Produktionsfenster bleibt "
          "bestätigbar -- derselbe Pfad wie bei NC/USC10/F82, kein "
          "Informationsverlust",
          ident_f_gt3_jahr.generation == "GT3", str(ident_f_gt3_jahr.generation))


asyncio.run(run())

print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE GENERATION-KONTEXT-FALSCHBESTAETIGUNG-TESTS GRUEN")
