"""
MX-5-NC-Alignment (Fortsetzungsauftrag): Core-Model/Auxiliary-Code-
Inkonsistenz reproduziert und generisch behoben.

BEFUND: identische Requests mit denselben realen Quellen (ADAC-Gebraucht-
wageninfo, auto-motor-und-sport.de -- beide real, siehe test_replay_
fortsetzung_abnahme.py) verhielten sich je nach `modell`-Schreibweise
unterschiedlich:
  modell="MX-5"    -> Identität belegt (zwei_domain-Pfad).
  modell="MX-5 NC" -> `modell_fehlt` auf der ADAC-Quelle, obwohl dieselbe
                      Quelle dasselbe Fahrzeug (Mazda MX-5, dritte
                      Generation, 2005-2015) beschreibt.

URSACHE: `_ist_modell_code_kandidat()` (app/technical_research.py) verlangte
bisher zwingend eine ZIFFER im abzutrennenden Code -- ein rein alphabetischer
Generationscode ("NC"/"ND"/"NA"/"NB") wurde deshalb NIE als Auxiliary Candidate
erkannt und blieb Teil des zwingenden Core-Model-Strings. Real existierende
Quellen, die die Generation nur als PROSA ("dritte Generation") statt als
Code nennen, verloren dadurch ihre sonst eindeutige Core-Model-Übereinstimmung
komplett -- obwohl laut `_ziel()`-Docstring ein fehlender Auxiliary Candidate
eine passende Core-Model-Quelle NIE ausschließen darf.

FIX: dieselbe, bereits bestehende und getestete Grenze wie bei
`_generationscodes()` (dort: ein ziffernloser Code bleibt auf 1-2 Buchstaben
begrenzt) gilt jetzt auch für `_ist_modell_code_kandidat()`. Generisch -- kein
Mazda-/NC-Sonderfall; betrifft jeden zweiteiligen `modell`-String mit einem
kurzen, ziffernlosen letzten Token.

    python scripts/test_isolated.py test_mx5_nc_alignment_fix.py
"""
import asyncio

from app.technical_research import _fremde_generation, _ziel, parse_core_model, werte_identitaet_aus

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# ── Reine Split-Logik ────────────────────────────────────────────────────────
check("'MX-5 NC' -> Kern 'MX-5' + ziffernloser Kandidat 'NC' (vorher: kein Split)",
      parse_core_model("MX-5 NC") == ("MX-5", "NC"), str(parse_core_model("MX-5 NC")))
check("'MX-5 ND' -> Kern 'MX-5' + Kandidat 'ND' (generisch, nicht NC-spezifisch)",
      parse_core_model("MX-5 ND") == ("MX-5", "ND"), str(parse_core_model("MX-5 ND")))

# Kein Zusammenführen fremder/echter Modellwörter: ein 3+-stelliges,
# ziffernloses Wort bleibt Teil des Kernmodells (Regressionsschutz, bereits
# in test_identity_model_code_split.py geprüft, hier als MX-5-naher
# Gegenbeweis: "GTI"/"Sport"/"Touring" dürfen NICHT wie "NC" behandelt werden).
for _modell_ohne_code in ("Golf GTI", "A4 Avant", "3er Touring"):
    _kern, _code = parse_core_model(_modell_ohne_code)
    check(f"'{_modell_ohne_code}' bleibt unzerlegt (3+ Buchstaben ohne Ziffer sind ein "
          f"echtes Wort, kein Code)", _code is None and _kern == _modell_ohne_code)

# Die generische Ausschlussliste (dieselbe wie bei _generationscodes()) bleibt
# auch für den kurzen, ziffernlosen Fall wirksam -- "GT" ist ein Trimname,
# kein Generationscode.
check("'MX-5 GT' bleibt unzerlegt (GT ist Trimname, in derselben Ausschlussliste "
      "wie bei _generationscodes())", parse_core_model("MX-5 GT") == ("MX-5 GT", None),
      str(parse_core_model("MX-5 GT")))


# ── End-to-End: dieselben realen Quellen, modell="MX-5 NC" vs. "MX-5" ───────
# Quellen: ADAC-Gebrauchtwageninfo (assets.adac.de, Fachmedien) + auto-motor-
# und-sport.de -- beide real, Text unverändert (siehe test_replay_
# fortsetzung_abnahme.py, WebFetch 2026-10-26). Keine Quelle nennt den
# Buchstabencode "NC" wörtlich, beide beschreiben die Generation als Prosa.
_ADAC = treffer(
    "https://assets.adac.de/image/upload/Autodatenbank/GWInfo/gw0441-mazda-mx-5-2005-2015-bericht.pdf",
    "Gebrauchtwageninfo: Mazda MX-5 (2005-2015)",
    "Der Mazda MX-5 der dritten Generation wurde von 2005 bis 2015 hergestellt.")
_AMS = treffer(
    "https://www.auto-motor-und-sport.de/test/gebrauchtwagenkauf-mazda-mx-5-nc-probleme-staerken-schwaechen/",
    "Mazda MX-5 (Typ NC; 2005-2015) Gebrauchtwagencheck",
    "Der MX-5 mit dem Kuerzel NC, also die dritte Generation, ist ein Spass-Roadster.")


async def _ident(modell: str, quellen: list[dict]):
    z = _ziel("Mazda", modell, 2008, "1.8 MZR", None)
    ident, abgelehnt = werte_identitaet_aus(quellen, z)
    return ident, abgelehnt, z


async def _run():
    ident_nc, abgelehnt_nc, z_nc = await _ident("MX-5 NC", [_ADAC, _AMS])
    check("modell='MX-5 NC': Identitaet jetzt belegt (vorher: modell_fehlt auf ADAC)",
          ident_nc.belegt is True, str(ident_nc))
    check("modell='MX-5 NC': auxiliary_code wurde korrekt als 'NC' abgetrennt",
          z_nc.get("auxiliary_code") == "NC", str(z_nc))
    check("modell='MX-5 NC': Generation wird NICHT automatisch 'NC' (keine Quelle "
          "nennt den Code woertlich -- reine Core-Model-Uebereinstimmung reicht "
          "fuer Alignment, bestaetigt aber nichts von selbst)",
          ident_nc.generation is None, str(ident_nc.generation))
    check("modell='MX-5 NC': kein 'modell_fehlt' mehr auf der ADAC-Quelle",
          not any(a.get("url") == _ADAC["url"] and a.get("grund") == "modell_fehlt"
                  for a in abgelehnt_nc), str(abgelehnt_nc))

    ident_plain, _, _ = await _ident("MX-5", [_ADAC, _AMS])
    check("modell='MX-5' (ohne Code): Identitaet weiterhin belegt (unveraendertes "
          "Verhalten, Referenzfall)", ident_plain.belegt is True)

    check("Beide Schreibweisen fuehren jetzt zum SELBEN Ergebnis (belegt) -- "
          "keine Inkonsistenz mehr zwischen Core-Model-only und Core+Code",
          ident_nc.belegt == ident_plain.belegt == True)

    # Expliziter Widerspruch bleibt erkennbar: Quellen, die explizit die
    # FALSCHE Generation (ND statt NC) nennen, duerfen trotz Core-Match
    # weiterhin nicht als "NC bestaetigt" durchgehen.
    nd_quelle = treffer(
        "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/mazda/mx-5/",
        "Mazda MX-5 (ND) seit 2015: Technische Daten",
        "Der Mazda MX-5 ND (seit 2015) ist die vierte Generation des Roadsters.")
    check("expliziter Widerspruch (Quelle nennt ND) wird weiterhin erkannt",
          _fremde_generation(nd_quelle, "MX-5", "NC") is True)
    ident_konflikt, abgelehnt_konflikt, _ = await _ident(
        "MX-5 NC", [nd_quelle, treffer(
            "https://www.auto-motor-und-sport.de/test/mazda-mx-5-2-0-skyactiv-g-184/",
            "Mazda MX-5 2.0 Skyactiv-G 184 im Test",
            "Die vierte Generation des Mazda MX-5 (ND, seit 2015): 2.0 Skyactiv-G mit 184 PS.")])
    check("Core-Modell bleibt trotzdem belegt (Marke+Modell sind real dieselben)",
          ident_konflikt.belegt is True)
    check("Generation wird NICHT faelschlich 'NC' (Quellen beschreiben nachweislich "
          "die ND, Baujahr 2008 liegt ausserhalb ihres Fensters)",
          ident_konflikt.generation is None, str(ident_konflikt.generation))


asyncio.run(_run())

print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE MX5-NC-ALIGNMENT-TESTS GRUEN")
