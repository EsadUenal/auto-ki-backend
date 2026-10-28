"""
RC-F-Identitaetsvertrag-Folgeauftrag (dritte Runde, 2026-10-28): echter,
POSITIVER Zugehoerigkeitsbeleg fuer Baureihen-/Generationscodes -- nicht nur
eine raeumliche-Naehe-Ausschlusspruefung.

BEFUND (erste Runde, Commit 6878a29): `_generationscodes()` verankerte
Generationscodes ausschliesslich auf dem vollen Core-Model-String ("RC F").
Reale Quellen schreiben den Baureihen-Code aber oft hinter dem BLOSSEN
Markennamen/der Modellfamilie ("RC (UXC1)", nicht "RC F (UXC1)").

GEGENPRUEFUNG RUNDE 2 (Commit 48306e7): ersetzte die reine Zeichen-
Fensterpruefung durch Ausschlusspruefungen (Absatzgrenze/fremde Marke/
Verneinung) -- IMMER NOCH nicht ausreichend: "Zielmodell steht irgendwo im
selben Block" ohne fremde Marke genuegte weiterhin als Fund, auch wenn ein
ANDERES Modell DERSELBEN Marke (z.B. eine Nachbar-Tabellenzeile "Modell RC
350, Baureihe RC (XYZ)") dem Code tatsaechlich naeher stand (real
reproduziert, Faelle K/L/M unten).

GEGENPRUEFUNG RUNDE 3 (dieser Auftrag): `_modellfamilie_zugehoerigkeit_
belegt()` (app/technical_research.py) prueft jetzt POSITIV, welchem Modell
ein Code zugeordnet ist, in drei Schritten:
  0. der EIGENE, durch Satzendezeichen begrenzte Satz des Codes ist
     autoritativ, wenn er selbst eine Fahrzeug-Aussage traegt (dieselbe
     Satzanfangs-Konstruktion wie der F16-Fix, aber mit TEILMENGEN-
     Kompatibilitaet statt strikter Gleichheit -- "RC" vs. Ziel "RC F" ist
     neutral, "RC 350"/"RC Convertible" vs. "RC F" ist ein Widerspruch);
  1. ohne eindeutigen eigenen Satz entscheidet die naechstgelegene
     Feldlabel-Nennung ("Modell:"/"Fahrzeug:", wie ADAC/carwiki.de sie
     selbst verwenden) -- RUECKWAERTS hat Vorrang, weil Identitaetsfelder in
     realen Specsheets VOR Klassifikationsfeldern wie "Baureihe" stehen;
  2. das Zielmodell muss SELBST (nicht nur ein kompatibler Praefix wie "RC")
     im Fenster vorkommen, ohne Verneinung und ohne fremde Marke dazwischen.
Zusaetzlich schliesst `_rollenbeleg_feldlabel()` die reale Luecke bei
Feldlabel:Wert-Quellen (Doppelpunkt/Zeilenumbruch trennt Rollenwort und Code
bisher in zwei Teilsaetze), und eine strukturelle Erkennung in
`_generationscodes()` deckt die arabische Ordnungszahl-Form "Modell (N.
Generation) (CODE)" ab (real bei auto-motor-und-sport.de gefunden, analog
zur bestehenden roemischen Form "Modell IV (CODE)").

XC1/UXC1 (Fall H): begrenzte oeffentliche Recherche (car.info, lexusclub.cz,
carspace.me, manuals.cz, what-breaks.com) zeigt fuer dieselbe RC-Baureihe
durchgehend einen DRITTEN Code "XC10" -- weder zu "XC1" (carwiki.de/AMS)
noch zu "UXC1" (ADAC) exakt passend. Die Beziehung bleibt UNGEKLAERT (nicht
als nachgewiesener fachlicher Widerspruch zu behandeln, aber auch nicht
gleichzusetzen) -- s. Kommentar bei Fall H.

Reale, unveraendert zitierte Quellen (per Browser-Tool nachgeprueft,
28.10.2026):
  * adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/
    Tabellenzeilen "Modell RC F" / "Baureihe RC (UXC1) (12/14 - 06/25)" /
    "Herstellerinterne Baureihenbezeichnung UXC1" in DERSELBEN Tabelle.
  * carwiki.de/lexus-rc-hsn-tsn-schluesselnummer/
    "Baureihe:\\nRC XC1" (Doppelpunkt+Zeilenumbruch) mit Tabellenzeile
    "Lexus RC F Carbon Automatik ... 4969 cm3 477 PS 2014 bis 2025".
  * auto-motor-und-sport.de/marken-modelle/lexus/rc/
    "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)" mit Artikel
    "LEXUS RC F TRACK EDITION IM SUPERTEST" auf derselben Seite.
  * car.info / lexusclub.cz / carspace.me / manuals.cz / what-breaks.com:
    unabhaengig von ADAC/carwiki.de, nennen "XC10" fuer RC 200t/300h/350/F
    gemeinsam, 2014/2015-2025 (s. Fall H).

Alle anderen Fahrzeuge/Quellen in diesem File (BMW M4, Vergleichsseiten,
Navigationsbloecke, Modellfamilien-Tabellen) sind SYNTHETISCHE Gegenfaelle,
ausdruecklich als solche benannt -- keine realen Veroeffentlichungen.

    python scripts/test_isolated.py test_identity_familie_zugehoerigkeit.py
"""
import asyncio

from app.technical_research import (
    _ziel, werte_identitaet_aus, _identitaets_claims, _generationen_pruefen,
    _modellfamilie_praefixe,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


def generation_fuer(marke, modell, baujahr, motor, url, titel, inhalt):
    """Generation-Claim EINER Quelle nach dem vollen Bestaetigungs-Gate
    (`_identitaets_claims()` + `_generationen_pruefen()`), unabhaengig von der
    Marke+Modell-Existenzschwelle (`_identitaet_belegt()`) -- s. `_ident()`
    fuer den Mehrquellen-/Existenz-Pfad weiter unten."""
    z = _ziel(marke, modell, baujahr, motor, None)
    claims = _identitaets_claims(treffer(url, titel, inhalt), z)
    _generationen_pruefen(claims, z.get("auxiliary_code"))
    gen = [c for c in claims if c["feld"] == "generation" and not c.get("verworfen")]
    return gen[0]["wert"] if gen else None


async def _ident(marke, modell, baujahr, motor, treffer_liste):
    z = _ziel(marke, modell, baujahr, motor, None)
    ident, abgelehnt = werte_identitaet_aus(treffer_liste, z)
    return ident, abgelehnt, z


print("=" * 78)
print("REALE POSITIVFAELLE -- Familien-Zugehoerigkeitsbeleg erreicht die Baureihe")
print("=" * 78)


async def test_a_adac_tabelle_modell_baureihe_getrennte_zeilen():
    # Real: adac.de/.../lexus/rc/xc1/339429/, Felder "Modell"/"Baureihe" als
    # getrennte Zeilen DERSELBEN Tabelle (28.10.2026 per Browser geprueft).
    g = generation_fuer(
        "Lexus", "RC F", 2017, "5.0 V8",
        "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/",
        "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25): Technische Daten | ADAC",
        "Marke Lexus Modell RC F Typ Ultimate Edition Automatik Baureihe RC (UXC1) "
        "(12/14 - 06/25) Herstellerinterne Baureihenbezeichnung UXC1 Modellstart 08/24")
    check("A) ADAC-Tabelle (Modell RC F / Baureihe RC (UXC1) in derselben Tabelle) "
          "-> generation=UXC1", g == "UXC1", f"generation={g!r}")


async def test_b_carwiki_feldlabel_doppelpunkt_zeilenumbruch():
    # Real: carwiki.de/lexus-rc-hsn-tsn-schluesselnummer/, "Baureihe:" gefolgt
    # von Zeilenumbruch, dann erst der Wert -- genau die vom Auftrag benannte
    # Feldlabel:Wert-Luecke.
    g = generation_fuer(
        "Lexus", "RC F", 2017, "5.0 V8",
        "https://carwiki.de/lexus-rc-hsn-tsn-schluesselnummer/",
        "Lexus RC HSN & TSN | Tabelle aller Schluesselnummern",
        "Uebersicht: Alle Schluesselnummern (HSN/TSN) der Lexus RC Baureihen. "
        "Hersteller: Lexus Fahrzeug: RC Baureihe: RC XC1 Karosserie: Coupe "
        "MODELL HSN SCHLUESSELNUMMER TSN SCHLUESSELNUMMER KRAFTSTOFF HUBRAUM LEISTUNG BAUJAHR "
        "Lexus RC F Carbon Automatik 5013 AJM Benzin 4969 cm3 477 PS 2014 bis 2025")
    check("B) carwiki.de (Baureihe:<Zeilenumbruch>RC XC1, Tabellenzeile 'RC F "
          "Carbon Automatik') -> generation=XC1", g == "XC1", f"generation={g!r}")


async def test_c_ams_arabische_ordnungszahl_eigene_klammer():
    # Real: auto-motor-und-sport.de/marken-modelle/lexus/rc/, "RC (1.
    # Generation) (XC1)" -- arabische Ordnungszahl in EIGENER Klammer vor der
    # Codeklammer (neue Struktur-Erkennung in _generationscodes()).
    g = generation_fuer(
        "Lexus", "RC F", 2017, "5.0 V8",
        "https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
        "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
        "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Leistung 181 PS bis "
        "477 PS. LEXUS RC F TRACK EDITION IM SUPERTEST: Was kann der V8-Sauger auf "
        "der Rennstrecke?")
    check("C) auto-motor-und-sport.de ('RC (1. Generation) (XC1)') -> generation=XC1",
          g == "XC1", f"generation={g!r}")


print("\n" + "=" * 78)
print("ADVERSARIALE GEGENFAELLE (synthetisch) -- Zugehoerigkeit statt Naehe")
print("=" * 78)


async def test_d_vergleichsseite_fremde_marke_verwirft():
    # SYNTHETISCH: Vergleichsartikel, Zielmodell "RC F" steht nahe am
    # Baureihencode eines ANDEREN Fahrzeugs (BMW M4, F82).
    g = generation_fuer(
        "Lexus", "RC F", 2017, "5.0 V8",
        "https://example.test/vergleich",
        "Vergleichstest: BMW M4 vs. Lexus RC F",
        "Der BMW M4, Chassiscode F82 dieser Baureihe, tritt gegen den Lexus RC F an.")
    check("D) Vergleichsseite: Zielmodell 'RC F' nahe fremdem Code 'F82' (BMW) "
          "-> KEIN Fund", g is None, f"generation={g!r}")


async def test_e_navigationsblock_absatzgrenze_verwirft():
    # SYNTHETISCH: Navigationsblock nennt das Zielmodell, der Hauptartikel
    # dahinter (durch eine echte Absatzgrenze getrennt) handelt von einem
    # ANDEREN Fahrzeug mit eigenem Baureihencode.
    g = generation_fuer(
        "Lexus", "RC F", 2017, "5.0 V8",
        "https://example.test/nav",
        "Navigation",
        "Navigation: Lexus, RC, RC F.\n\n"
        "Hauptartikel: Der BMW M4, Chassiscode F82 dieser Baureihe, ist ein "
        "Performance-Coupe.")
    check("E) Navigationsblock (Zielmodell) vs. Hauptartikel (fremder Code), "
          "echte Absatzgrenze dazwischen -> KEIN Fund", g is None, f"generation={g!r}")


async def test_f_explizite_verneinung_verwirft():
    # SYNTHETISCH: die Quelle verneint die Zuordnung ausdruecklich.
    g = generation_fuer(
        "Lexus", "RC F", 2017, "5.0 V8",
        "https://example.test/forum",
        "Lexus RC Forum",
        "Der Lexus RC F gehoert NICHT zur Baureihe RC (UXC1), auch wenn der Name "
        "es vermuten laesst.")
    check("F) explizit verneinte Zuordnung -> KEIN Fund", g is None, f"generation={g!r}")


print("\n" + "=" * 78)
print("ZWEITE GEGENPRUEFUNGSRUNDE -- positiver Beleg statt Ausschlusspruefung")
print("=" * 78)


async def test_k_zwei_modellfamilien_feldlabel_selber_absatz():
    # SYNTHETISCH, Feldlabel-Form (wie ADAC/carwiki.de): zwei Zeilen
    # DERSELBEN Marke im selben Absatz -- die naechstgelegene Zeile zum
    # jeweiligen Code muss entscheiden, nicht reine Zeichendistanz.
    g = generation_fuer(
        "Lexus", "RC F", 2017, "5.0 V8",
        "https://example.test/modelle",
        "Lexus RC Modelle",
        "Modell RC 350 Baureihe RC (UXB2). Modell RC F Baureihe RC (UXC1).")
    check("K) 'Modell RC 350 Baureihe RC (UXB2). Modell RC F Baureihe RC "
          "(UXC1).' -> nur UXC1, UXB2 bleibt unbestaetigt", g == "UXC1", f"generation={g!r}")


async def test_l_zwei_modellfamilien_fliesstext_selber_absatz():
    # SYNTHETISCH, Fliesstext-Form: dieselbe Situation ohne Feldlabel --
    # der EIGENE Satz des Codes ("Die Lexus RC 350, Chassiscode ...") nennt
    # explizit ein ANDERES, nicht kompatibles Modell.
    g = generation_fuer(
        "Lexus", "RC F", 2017, "5.0 V8",
        "https://example.test/modelle-fliesstext",
        "Lexus RC Modelle",
        "Die Lexus RC 350, Chassiscode RC (UXB2) dieser Baureihe, ist beliebt. "
        "Die Lexus RC F, Chassiscode RC (UXC1) dieser Baureihe, ist das Topmodell.")
    check("L) Fliesstext, zwei Modellfamilien im selben Absatz -> nur UXC1",
          g == "UXC1", f"generation={g!r}")


async def test_m_tabelle_ohne_leerzeilen_zielmodell_in_mittlerer_zeile():
    # SYNTHETISCH: Tabelle mit DREI Modell-/Baureihenzeilen ohne Leerzeilen
    # dazwischen -- das Zielmodell steht in der MITTLEREN Zeile, die
    # Nachbarzeilen tragen je einen EIGENEN, abweichenden Code.
    g = generation_fuer(
        "Lexus", "RC F", 2017, "5.0 V8",
        "https://example.test/tabelle",
        "Lexus RC Modelle",
        "Modell RC 350 Baureihe RC (UXA1). Modell RC F Baureihe RC (UXC1). "
        "Modell RC Convertible Baureihe RC (UXD1).")
    check("M) Zielmodell in mittlerer Tabellenzeile zwischen zwei fremden "
          "Zeilen -> nur UXC1 (nicht UXA1/UXD1 der Nachbarzeilen)",
          g == "UXC1", f"generation={g!r}")


async def test_n_ueberlappende_jahresfenster_beide_rollenbelegt():
    # GENERISCH (kein konkretes Fahrzeug): zwei Baureihen-Codes mit
    # UEBERLAPPENDEN Zeitfenstern, beide fuer das Baujahr zutreffend und
    # beide rollenbelegt -- ein echter Widerspruch, den `konsens()` als
    # solchen erkennen muss (nicht blind den ersten nehmen).
    z = _ziel("Lexus", "RC F", 2019, "5.0 V8", None)
    r = treffer(
        "https://example.test/ueberlappend", "Lexus RC Generationen Uebersicht",
        "Die Lexus RC (UXC1), Chassiscode dieser Baureihe, deckt 2014 bis 2022 ab. "
        "Die Lexus RC (UXC9), Chassiscode dieser Baureihe, deckt 2018 bis 2025 ab. "
        "Der Lexus RC F gehoert zu dieser Baureihe.")
    claims = _identitaets_claims(r, z)
    _generationen_pruefen(claims, z.get("auxiliary_code"))
    gen_claims = [c for c in claims if c["feld"] == "generation" and not c.get("verworfen")]
    werte = {c["wert"] for c in gen_claims}
    check("N) zwei ueberlappende, je rollenbelegte Zeitfenster (2014-2022 UND "
          "2018-2025, Baujahr 2019) -> BEIDE Claims bleiben erhalten (echter "
          "Widerspruch, keine blinde Auswahl)", werte == {"UXC1", "UXC9"}, f"werte={werte!r}")


async def test_o_fehlende_jahresangabe_kein_spontaner_zweitwert():
    # GENERISCH: ein zweiter, fehlender-Jahresangabe-Code darf NICHT
    # spontan neben dem belegten entstehen (Grundregel: keine zusaetzliche
    # Mehrdeutigkeit erfinden, wo die Quelle nur EINEN Code nennt).
    g = generation_fuer(
        "Lexus", "RC F", 2017, "5.0 V8",
        "https://example.test/ohne-jahr",
        "Lexus RC Generationen Uebersicht",
        "Die Lexus RC (UXC1), Chassiscode dieser Baureihe, wird gebaut. "
        "Der Lexus RC F gehoert zu dieser Baureihe.")
    check("O) ein Code ohne Jahresangabe, sonst rollenbelegt -> UXC1, keine "
          "erfundene Mehrdeutigkeit", g == "UXC1", f"generation={g!r}")


print("\n" + "=" * 78)
print("ROLLEN-/GENERATIONSWIDERSPRUCH -- echte Quellen widersprechen sich")
print("=" * 78)


async def test_g_mehrere_generationen_jahresfenster_entscheidet():
    # SYNTHETISCH, aber generisch (kein konkretes Fahrzeug): dieselbe
    # Familienquelle nennt ZWEI Baureihen-Codes mit unterschiedlichen
    # Zeitfenstern -- das bestehende Jahresfenster/`konsens()` muss den
    # passenden auswaehlen, nicht den Praefix-Pfad selbst.
    g = generation_fuer(
        "Lexus", "RC F", 2017, "5.0 V8",
        "https://example.test/generationen",
        "Lexus RC Generationen",
        "Lexus RC (UXC1), Chassiscode dieser Baureihe, wurde von 2014 bis 2020 "
        "gebaut. Lexus RC (UXC2), Chassiscode dieser Baureihe, wird seit 2024 "
        "gebaut. Der Lexus RC F gehoert zu dieser Baureihe.")
    check("G) zwei Baureihen-Codes mit unterschiedlichen Zeitfenstern, Baujahr "
          "2017 -> UXC1 (2014-2020), nicht UXC2 (seit 2024)", g == "UXC1", f"generation={g!r}")


async def test_h_xc1_uxc1_beziehung_ungeklaert_kein_konsens():
    # REALE Quellen (ADAC + carwiki.de + auto-motor-und-sport.de) zusammen:
    # ADAC nennt "UXC1", carwiki.de/AMS nennen "XC1" -- zwei UNTERSCHIEDLICHE
    # Codestrings. WICHTIG (Gegenpruefung, zweite Runde): das ist NICHT
    # dasselbe wie ein NACHGEWIESENER fachlicher Widerspruch (unterschiedliche
    # tatsaechliche Generationen) -- begrenzte oeffentliche Recherche
    # (car.info, lexusclub.cz, carspace.me, manuals.cz, what-breaks.com, alle
    # unabhaengig von ADAC/carwiki.de) nennt fuer dieselbe RC-Baureihe
    # (2014-2025, RC 200t/300h/350/F gemeinsam) durchgehend "XC10" -- ein
    # DRITTER Code, der weder zu "XC1" noch zu "UXC1" exakt passt. Eine
    # plausible Erklaerung (ADAC/carwiki.de teilen vermutlich eine deutsche
    # Fahrzeugdaten-Quelle, die die Endziffer "0" abschneidet; "U" waere dann
    # ein Antriebsvariante-Praefix wie bei rolling.hu "USC10") ist NICHT durch
    # eine Hersteller-/Homologationsquelle belegt -- bleibt also ebenso
    # unbewiesen wie ein echter Widerspruch. Die Beziehung ist UNGEKLAERT,
    # nicht nachgewiesen widerspruechlich. Das bestehende `konsens()` traegt
    # diese Unterscheidung nicht explizit (es erkennt nur "zwei verschiedene
    # Werte, vergleichbares Gewicht" -> Konflikt) -- das genuegt hier trotzdem:
    # UNKNOWN ist in BEIDEN Faellen (echter Widerspruch ODER ungeklaerte
    # Aequivalenz) die korrekte, konservative Ausgabe; keine neue Architektur
    # noetig, nur diese Praezisierung im Reporting/Kommentar.
    quelle = [
        treffer("https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/",
                "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25): Technische Daten | ADAC",
                "Marke Lexus Modell RC F Typ Ultimate Edition Automatik Baureihe RC (UXC1) "
                "(12/14 - 06/25) Herstellerinterne Baureihenbezeichnung UXC1 Motorcode 2UR-GSE "
                "Leistung maximal in PS 464 Antriebsart Hinterrad Getriebeart Automatikgetriebe"),
        treffer("https://carwiki.de/lexus-rc-hsn-tsn-schluesselnummer/",
                "Lexus RC HSN & TSN | Tabelle aller Schluesselnummern",
                "Uebersicht: Alle Schluesselnummern (HSN/TSN) der Lexus RC Baureihen. "
                "Hersteller: Lexus Fahrzeug: RC Baureihe: RC XC1 Karosserie: Coupe "
                "MODELL HSN SCHLUESSELNUMMER TSN SCHLUESSELNUMMER KRAFTSTOFF HUBRAUM LEISTUNG BAUJAHR "
                "Lexus RC F Carbon Automatik 5013 AJM Benzin 4969 cm3 477 PS 2014 bis 2025"),
        treffer("https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
                "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
                "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Leistung 181 PS bis "
                "477 PS. LEXUS RC F TRACK EDITION IM SUPERTEST: Was kann der V8-Sauger auf "
                "der Rennstrecke? LEXUS LC 500 UND RC F: V8-Sportler auf Abschiedstour."),
    ]
    ident, abgelehnt, _ = await _ident("Lexus", "RC F", 2017, "5.0 V8", quelle)
    check("H) ADAC (UXC1) vs. carwiki.de+AMS (XC1) zusammen -> Beziehung "
          "ungeklaert, generation bleibt UNKNOWN (kein erfundener Gleichsetzungswert)",
          ident.belegt is True and ident.generation is None,
          f"belegt={ident.belegt} generation={ident.generation!r}")


print("\n" + "=" * 78)
print("HAERTUNGSFAELLE BLEIBEN GESCHUETZT (GT3/R32 erreichen den Praefix-Pfad nie)")
print("=" * 78)


async def test_i_gt3_haertung_unberuehrt():
    # "911 GT3" wird von parse_core_model()/_ziel() VOR Erreichen dieses Fixes
    # bereits auf Kern-Modell "911" reduziert (GT3 wird auxiliary_code) -- ein
    # Einwort-Core-Model liefert laut `_modellfamilie_praefixe()` keine
    # Praefixe, der neue Pfad wird fuer diesen Fall also gar nicht erst
    # aktiv. Siehe test_identity_model_code_split.py::test_m_... fuer den
    # vollen End-to-End-Nachweis; hier nur die isolierte Praefix-Garantie.
    check("I) Core-Modell '911' liefert keine Praefixe (Einwortmodell)",
          _modellfamilie_praefixe("911") == [])


async def test_j_golf_r32_haertung_unberuehrt():
    check("J) Core-Modell 'Golf' liefert keine Praefixe (Einwortmodell)",
          _modellfamilie_praefixe("Golf") == [])


async def _run_all():
    await test_a_adac_tabelle_modell_baureihe_getrennte_zeilen()
    await test_b_carwiki_feldlabel_doppelpunkt_zeilenumbruch()
    await test_c_ams_arabische_ordnungszahl_eigene_klammer()
    await test_d_vergleichsseite_fremde_marke_verwirft()
    await test_e_navigationsblock_absatzgrenze_verwirft()
    await test_f_explizite_verneinung_verwirft()
    await test_g_mehrere_generationen_jahresfenster_entscheidet()
    await test_h_xc1_uxc1_beziehung_ungeklaert_kein_konsens()
    await test_i_gt3_haertung_unberuehrt()
    await test_j_golf_r32_haertung_unberuehrt()
    await test_k_zwei_modellfamilien_feldlabel_selber_absatz()
    await test_l_zwei_modellfamilien_fliesstext_selber_absatz()
    await test_m_tabelle_ohne_leerzeilen_zielmodell_in_mittlerer_zeile()
    await test_n_ueberlappende_jahresfenster_beide_rollenbelegt()
    await test_o_fehlende_jahresangabe_kein_spontaner_zweitwert()
    print("\n" + "=" * 78)
    if _FEHLER:
        print(f"{len(_FEHLER)} FEHLER:", _FEHLER)
        raise SystemExit(1)
    print("Alle Pruefungen bestanden.")


asyncio.run(_run_all())
