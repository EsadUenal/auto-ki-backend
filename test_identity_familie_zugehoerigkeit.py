"""
RC-F-Identitaetsvertrag-Folgeauftrag (zweite Runde, 2026-10-28): belegter
Familien-Zugehoerigkeitsbeleg fuer Baureihen-/Generationscodes.

BEFUND (erste Runde, Commit 6878a29): `_generationscodes()` verankerte
Generationscodes ausschliesslich auf dem vollen Core-Model-String ("RC F").
Reale Quellen schreiben den Baureihen-Code aber oft hinter dem BLOSSEN
Markennamen/der Modellfamilie ("RC (UXC1)", nicht "RC F (UXC1)") -- die
erste Fassung machte das ueber eine reine ZEICHEN-Fenstersuche erreichbar.

GEGENPRUEFUNG (dieser Auftrag): reine raeumliche Naehe reicht NICHT als
Zugehoerigkeitsbeleg. `_modellfamilie_zugehoerigkeit_belegt()` (app/
technical_research.py) ersetzt die Fenstersuche durch drei generische,
bereits anderswo im Modul genutzte Pruefungen:
  * eine echte Absatz-/Ueberschriftengrenze begrenzt den Suchraum
    (Navigations-/Querverweis-Fall);
  * eine FREMDE Marke zwischen Codefundstelle und Zielmodell-Nennung
    verwirft den Fund (Vergleichsseiten-Fall);
  * eine Verneinung im Teilsatz der Zielmodell-Nennung verwirft den Fund.
Zusaetzlich schliesst `_rollenbeleg_feldlabel()` die reale Luecke bei
Feldlabel:Wert-Quellen (Doppelpunkt/Zeilenumbruch trennt Rollenwort und Code
bisher in zwei Teilsaetze), und eine neue, rein strukturelle Erkennung in
`_generationscodes()` deckt die arabische Ordnungszahl-Form "Modell (N.
Generation) (CODE)" ab (real bei auto-motor-und-sport.de gefunden, analog
zur bestehenden roemischen Form "Modell IV (CODE)").

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

Alle anderen Fahrzeuge/Quellen in diesem File (BMW M4, Vergleichsseiten,
Navigationsbloecke) sind SYNTHETISCHE Gegenfaelle, ausdruecklich als solche
benannt -- keine realen Veroeffentlichungen.

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


async def test_h_xc1_uxc1_echter_widerspruch_kein_konsens():
    # REALE Quellen (ADAC + carwiki.de + auto-motor-und-sport.de) zusammen:
    # ADAC nennt "UXC1", carwiki.de/AMS nennen "XC1" -- zwei UNTERSCHIEDLICHE
    # Codestrings fuer dieselbe Baureihe, laut Auftrag NICHT ohne eigene
    # Aequivalenzevidenz gleichzusetzen. Das bestehende `konsens()` erkennt
    # das bereits generisch als Konflikt vergleichbar starker Quellen ->
    # generation bleibt UNKNOWN, statt einen der beiden Werte zu erfinden.
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
    check("H) ADAC (UXC1) vs. carwiki.de+AMS (XC1) zusammen -> ECHTER Konflikt, "
          "generation bleibt UNKNOWN (kein erfundener Gleichsetzungswert)",
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
    await test_h_xc1_uxc1_echter_widerspruch_kein_konsens()
    await test_i_gt3_haertung_unberuehrt()
    await test_j_golf_r32_haertung_unberuehrt()
    print("\n" + "=" * 78)
    if _FEHLER:
        print(f"{len(_FEHLER)} FEHLER:", _FEHLER)
        raise SystemExit(1)
    print("Alle Pruefungen bestanden.")


asyncio.run(_run_all())
