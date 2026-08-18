"""
Vertrauensstufen für DB-Fakten im Marktvergleich — deterministisch, KEIN Netzwerk.

Hintergrund (DB-Trust-Audit): große Teile der Fahrzeug-DB wurden generativ erzeugt
und nie fachlich geprüft; nachweislich stecken Fehler darin (`bmw-8er-e63-e64`
führt Codes und Motoren der 6er-Reihe). Gleichzeitig stammten in einem einzigen
BMW-Lauf 163 Ablehnungen "anderes Modell", 123 "andere Generation" und 15 "andere
Motorvariante" ausschließlich aus diesen Daten.

Neue Regel: nur `verified` (Status UND gespeicherte Quelle) darf im Marktvergleich
hart ablehnen oder positiv inferieren. Nutzer- und Inserats-Evidenz bleiben davon
unberührt.

Fälle A-N aus der Aufgabenstellung.

    python test_db_trust.py
"""
import os
import sys
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_trust_"), "test.db")
sys.path.insert(0, ".")

from types import SimpleNamespace                                        # noqa: E402

from app.marktvergleich import (                                         # noqa: E402
    _bewerte, _eindeutige_karosserie, _extrahiere_aus_text, baue_ziel,
)
from app.verification import (                                           # noqa: E402
    REJECTED, REVIEWED, UNVERIFIED, VERIFIED, darf_als_wissen_gelten, is_verified,
    verification_source, verification_status,
)

_fails: list[str] = []


def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        _fails.append(name)


QUELLE = "BMW Group PressClub, Werkscode-Uebersicht (Abruf 2026-08-18)"


def baureihe(verification=None, chassis=True):
    r = {"id": "bmw-3er-g20-g21", "marke": "BMW", "modell": "3er",
         "generation": "G20/G21", "karosserie": '["Limousine", "Touring"]'}
    if chassis:
        r["chassis_codes"] = {"G20": "Limousine", "G21": "Touring"}
    if verification is not None:
        r["verification"] = verification
    return r


def geschwister(verification=None):
    """Andere DB-Zeile derselben Modellfamilie — Quelle der Fremdgenerationen."""
    r = {"id": "bmw-3er-f30", "marke": "BMW", "modell": "3er", "generation": "F30",
         "karosserie": '["Limousine"]'}
    if verification is not None:
        r["verification"] = verification
    return r


MOTOREN = [{"baureihe_id": "bmw-3er-g20-g21", "bezeichnung": "320d", "motorcode": "B47D20"},
           {"baureihe_id": "bmw-3er-g20-g21", "bezeichnung": "330d"},
           {"baureihe_id": "bmw-3er-f30", "bezeichnung": "318d"}]
MOTOR = {"bezeichnung": "320d", "kraftstoff": "Diesel", "leistung_ps": 190,
         "motorcode": "B47D20"}
URL = "https://www.kleinanzeigen.de/s-autos/bmw-320d-g20/k0c216+autos.typ_s:limousine"
TITEL = "BMW gebrauchte Fahrzeuge"


def ziel_fuer(br, sonstige=None, *, modell="3er G20", motor="320d 190 PS",
              kraftstoff="Diesel", motor_match=MOTOR):
    """`motor_match` ist in Produktion das Ergebnis von find_motor(baureihe, req.motor)
    — ohne Motorangabe des Nutzers gibt es keinen Match. Genau das bildet
    motor_match=None ab."""
    req = SimpleNamespace(marke="BMW", modell=modell, motor=motor,
                          kraftstoff=kraftstoff, baujahr=2019, kilometerstand=120_000)
    alle = [br] + (sonstige if sonstige is not None else [geschwister()])
    return baue_ziel(br, motor_match, req, alle, MOTOREN)


def karte(titel, lid, preis, km, ez, beschr):
    return ("* [![" + titel + " Vorschau](https://img.kleinanzeigen.de/x/" + lid + ".jpg)\n\n"
            "  20](/s-anzeige/x/" + lid + "-216-1111)\n\n  12307 Berlin\n\n  Heute\n\n"
            "  ## [" + titel + "](/s-anzeige/x/" + lid + "-216-1111)\n\n  " + beschr + "\n\n"
            "  " + preis + " €\n\n  " + km + " km   EZ " + ez + "\n")


FUELL = (karte("BMW 320d G20 Advantage", "3400000801", "24.100", "118.500", "05/2019", "Scheckheft")
         + karte("BMW 320d G20 Sport Line", "3400000802", "24.300", "118.700", "05/2019", "Scheckheft"))


def bewerte(ziel, titel, beschr, preis="25.100", ez="05/2019", km="118.000"):
    raw = "## Ergebnisse\n\n" + karte(titel, "3400009999", preis, km, ez, beschr) + FUELL
    roh = _extrahiere_aus_text(TITEL + "\n\n" + raw, URL, "market_category",
                               grenzen=(len(TITEL) + 1, len(TITEL) + 2),
                               seiten_body=_eindeutige_karosserie(URL))
    ziel_preis = int(preis.replace(".", ""))
    for b in roh:
        if b.preis_eur == ziel_preis:
            return _bewerte(b, ziel)
    return None


# ══ A-F — Statuslogik ══════════════════════════════════════════════════════
check("A: fehlender verification-Eintrag -> unverified",
      verification_status(baureihe(), "generation") == UNVERIFIED
      and not is_verified(baureihe(), "generation"))
check("A: Baureihe ohne jedes Feld -> unverified, kein Crash",
      verification_status({}, "generation") == UNVERIFIED
      and verification_status(None, "generation") == UNVERIFIED)
check("B: kaputtes JSON -> unverified, kein Crash",
      verification_status(baureihe('{"generation": '), "generation") == UNVERIFIED)
check("B: JSON-Array statt Objekt -> unverified",
      verification_status(baureihe('["generation"]'), "generation") == UNVERIFIED)
check("B: unbekannter Statuswert -> unverified",
      verification_status(baureihe('{"generation": {"status": "halbwegs"}}'),
                          "generation") == UNVERIFIED)
check("C: reviewed ist NICHT verified",
      verification_status(baureihe('{"generation": {"status": "reviewed"}}'),
                          "generation") == REVIEWED
      and not is_verified(baureihe('{"generation": {"status": "reviewed"}}'), "generation"))
check("D: verified OHNE source wird auf reviewed herabgestuft",
      verification_status(baureihe('{"generation": {"status": "verified"}}'),
                          "generation") == REVIEWED)
check("D: verified mit LEERER source zaehlt ebenfalls nicht",
      verification_status(baureihe('{"generation": {"status": "verified", "source": "  "}}'),
                          "generation") == REVIEWED)
V_GEN = '{"generation": {"status": "verified", "source": "%s", "date": "2026-08-18"}}' % QUELLE
check("E: verified + source gilt als verified",
      verification_status(baureihe(V_GEN), "generation") == VERIFIED
      and is_verified(baureihe(V_GEN), "generation"))
check("E: die Quelle ist auslesbar",
      verification_source(baureihe(V_GEN), "generation") == QUELLE)
check("F: rejected gilt nicht als Wissen",
      verification_status(baureihe('{"generation": {"status": "rejected"}}'),
                          "generation") == REJECTED
      and not darf_als_wissen_gelten(baureihe('{"generation": {"status": "rejected"}}'),
                                     "generation"))
check("F: unverified darf weiterhin als WEICHES Wissen gelten",
      darf_als_wissen_gelten(baureihe(), "generation"))
check("Kurzform {'fakt': 'verified'} ohne source -> reviewed",
      verification_status(baureihe('{"generation": "verified"}'), "generation") == REVIEWED)

# ══ G/H — Nutzer- und Listing-Evidenz wirken unabhaengig vom DB-Trust ══════
Z_UNVERIFIED = ziel_fuer(baureihe())
check("G: Nutzerangabe 320d traegt weiterhin harte Motorentscheidungen",
      Z_UNVERIFIED["motor_hart"] is True and "320d" in Z_UNVERIFIED["ziel_motor_tokens"])
check("G: Nutzer-Kraftstoff und -Leistung bleiben hart",
      Z_UNVERIFIED["kraftstoff_hart"] is True and Z_UNVERIFIED["leistung_hart"] is True)
check("G: die vom Nutzer genannte Zielgeneration bleibt erhalten",
      Z_UNVERIFIED["generation_tokens"] == {"g20"})
b_ok = bewerte(Z_UNVERIFIED, "BMW 320d G20 Sport Line", "Scheckheft, Diesel")
check("H: ein sauberes Inserat bleibt bei ungeprueftem DB-Stand verwertbar",
      b_ok.vergleichbarkeit == "sehr_aehnlich")
b_gen = bewerte(Z_UNVERIFIED, "BMW 320d G20 Limousine", "Explizit G20, Scheckheft")
check("H: explizite Generation aus dem Inserat wirkt weiterhin",
      b_gen.generation == "G20" and b_gen.generation_evidence == "explicit_card")

# ══ I/J — Chassis-Inference ════════════════════════════════════════════════
check("I: ungepruefte chassis_codes gelangen gar nicht erst ins Zielprofil",
      Z_UNVERIFIED["chassis_codes"] == {})
b_inf = bewerte(Z_UNVERIFIED, "BMW 320d Limousine Advantage", "Limousine, Scheckheft")
check("I: ohne verifizierte Codes KEINE positive Generations-Inference",
      b_inf.generation is None and b_inf.generation_evidence != "inferred_database")
check("I: die Beobachtung wird dadurch nicht hart verworfen",
      b_inf.vergleichbarkeit != "ungeeignet")
V_CHASSIS = ('{"chassis_codes": {"status": "verified", "source": "%s"}}' % QUELLE)
Z_CHASSIS = ziel_fuer(baureihe(V_CHASSIS))
check("J: verifizierte chassis_codes stehen im Zielprofil",
      Z_CHASSIS["chassis_codes"] == {"g20": "limousine", "g21": "kombi"})
b_inf2 = bewerte(Z_CHASSIS, "BMW 320d Limousine Advantage", "Limousine, Scheckheft")
check("J: mit Verifikation funktioniert die Inference wie zuvor",
      b_inf2.generation == "G20" and b_inf2.generation_evidence == "inferred_database")
b_tour = bewerte(Z_CHASSIS, "BMW 320d Touring Advantage", "Touring Kombi, Scheckheft")
check("J: und die daraus abgeleitete Fremdgeneration verwirft wieder hart",
      b_tour.vergleichbarkeit == "ungeeignet"
      and "G21" in b_tour.acceptance_reason)

# ══ K/L — Fremdgeneration aus anderen DB-Zeilen ════════════════════════════
check("K: ungepruefte Geschwisterzeile liefert keine Fremdgeneration",
      "f30" not in (Z_UNVERIFIED["fremd_generationen"] or set()))
b_f30 = bewerte(Z_UNVERIFIED, "BMW 320d F30 Sport Line", "Scheckheft, Diesel")
check("K: ein F30-Inserat wird NICHT mehr per DB hart verworfen",
      "andere Generation" not in (b_f30.acceptance_reason or ""))
Z_VGEN = ziel_fuer(baureihe(), [geschwister(V_GEN)])
check("L: verifizierte Geschwisterzeile liefert die Fremdgeneration",
      "f30" in Z_VGEN["fremd_generationen"])
b_f30v = bewerte(Z_VGEN, "BMW 320d F30 Sport Line", "Scheckheft, Diesel")
check("L: dann greift der harte DB-Reject wieder",
      b_f30v.vergleichbarkeit == "ungeeignet"
      and "andere Generation" in b_f30v.acceptance_reason)

# ══ M — Stoertoken aus ungepruefter Fremdmodell-Liste ══════════════════════
check("M: fremd_modelle bleibt ohne verifizierte Zeilen leer",
      Z_UNVERIFIED["fremd_modelle"] == set()
      and Z_UNVERIFIED["fremd_motor_tokens"] == set())
for wort in ("mit", "auto", "paket", "pro", "mercedes"):
    b_st = bewerte(Z_UNVERIFIED, "BMW 320d G20 Sport Line",
                   "Zum Verkauf steht ein gepflegtes " + wort + " Fahrzeug, Scheckheft")
    check("M: Stoertoken %r verwirft nicht mehr" % wort,
          "anderes Modell im Preisumfeld" not in (b_st.acceptance_reason or ""))

# ══ N — direkter Nutzer-vs-Listing-Motorwiderspruch ════════════════════════
b_330d = bewerte(Z_UNVERIFIED, "BMW 330d G20 M Sport", "Scheckheft, kraftvoller Diesel")
check("N: 330d-Inserat gegen Ziel 320d wird weiterhin hart verworfen",
      b_330d.vergleichbarkeit == "ungeeignet"
      and "andere Motorvariante (330d)" in b_330d.acceptance_reason)
b_m340 = bewerte(Z_UNVERIFIED, "BMW M340i G20 xDrive", "Scheckheft")
check("N: auch M340i wird ueber die direkte Bezeichnung erkannt",
      "andere Motorvariante" in (b_m340.acceptance_reason or ""))
b_360 = bewerte(Z_UNVERIFIED, "BMW 320d G20 M Sport", "360 Grad Kamera, 19 Zoll, Scheckheft")
check("N: Zahlen wie '360' oder '19 Zoll' sind KEINE Motorbezeichnung",
      b_360.vergleichbarkeit == "sehr_aehnlich")
b_bj = bewerte(Z_UNVERIFIED, "BMW 320d G20 Advantage", "Erstzulassung 2019, Scheckheft")
check("N: das Baujahr im Text wird nicht als Motorbezeichnung gelesen",
      b_bj.vergleichbarkeit == "sehr_aehnlich")

# ── Kraftstoff/Leistung ohne Nutzerangabe: weich statt hart ────────────────
Z_OHNE_USER = ziel_fuer(baureihe(), modell="3er", motor="", kraftstoff=None,
                        motor_match=None)
check("Kraftstoff/Leistung ohne Nutzerangabe und ohne Verifikation -> weich",
      Z_OHNE_USER["kraftstoff_hart"] is False and Z_OHNE_USER["leistung_hart"] is False)
check("Motorpruefung ohne Nutzerangabe und ohne Verifikation -> weich",
      Z_OHNE_USER["motor_hart"] is False)

print()
if _fails:
    print(str(len(_fails)) + " FEHLGESCHLAGEN:")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("Alle DB-Trust-Tests bestanden.")

# ══ Direkte Modellfamilien-Evidenz (§4/§10) ════════════════════════════════
# Hard-Reject NUR bei strukturell beweisbarem Familienwiderspruch. Ein unbekanntes
# Token hinter der Marke ist KEIN Fremdmodell — dafuer fehlt ohne verifiziertes
# Modelllexikon jede Grundlage.
from app.marken import marke_tokens as _alias                            # noqa: E402
from app.marktvergleich import (                                         # noqa: E402
    _marke_tokens, _modell_kennungen_user, _modell_widerspruch,
)


def _mz(marke, modell, motor="", generation=""):
    req = SimpleNamespace(marke=marke, modell=modell, motor=motor,
                          generation=generation, inserat_titel="")
    return {"modell_kennungen_user": _modell_kennungen_user(
                req, {t for t in generation.lower().split() if t}),
            "marke_tokens": _marke_tokens(marke) | _alias(marke)}


BMW = _mz("BMW", "3er G20", "320d 190 PS", "G20")
MB = _mz("Mercedes-Benz", "C-Klasse", "C 200 d")
AUDI = _mz("Audi", "A4", "40 TDI")
VW = _mz("Volkswagen", "Golf")
OPEL = _mz("Opel", "Insignia")

check("A: 'BMW 320d G20' loest keinen Modellwiderspruch aus",
      _modell_widerspruch("BMW 320d G20 Sport Line", BMW) is None)
check("C: 'BMW 3GT' ist KEIN Fremdmodell (Variante, nicht beweisbar)",
      _modell_widerspruch("BMW 3GT gepflegt", BMW) is None)
check("D: 'BMW Limousine Advantage' ist KEIN Fremdmodell (Karosserie)",
      _modell_widerspruch("BMW Limousine Advantage", BMW) is None)
check("D: 'BMW Limousine Sport Line' ebenso",
      _modell_widerspruch("BMW Limousine Sport Line", BMW) is None)
check("BMW: der Generationscode G20 bildet KEINE Modellfamilie",
      "praefix" not in BMW["modell_kennungen_user"])
check("BMW: ein fremder Code F30 loest keinen Modellwiderspruch aus",
      _modell_widerspruch("BMW 320d F30 Sport Line", BMW) is None)

check("E: Mercedes C 200 d vs GLC220d -> Familienwiderspruch",
      _modell_widerspruch("Mercedes GLC220d 38.900", MB) == "glc220d")
check("E: auch getrennt geschrieben (GLC 220 d)",
      _modell_widerspruch("Mercedes GLC 220 d", MB) == "glc220")
check("F: Mercedes C 200 d vs C200 -> zulaessig",
      _modell_widerspruch("Mercedes C200 W205 26.500", MB) is None)
check("F: der Generationscode W205 loest nichts aus",
      _modell_widerspruch("Mercedes C 200 d W205", MB) is None)

check("G: Audi A4 vs A6 -> Familienwiderspruch",
      _modell_widerspruch("Audi A6 40 TDI 30.000", AUDI) == "a6")
check("H: Audi A4 vs A4 -> zulaessig",
      _modell_widerspruch("Audi A4 40 TDI 28.000", AUDI) is None)
check("G: gemeinsame Motorteile (40 TDI) entscheiden nichts",
      AUDI["modell_kennungen_user"].get("familie") == ("a", "4"))

check("I: Volkswagen und VW gelten als dieselbe Marke",
      "vw" in VW["marke_tokens"] and "volkswagen" in VW["marke_tokens"])
check("J: VW Golf vs Passat bleibt BEWUSST unknown (kein verified Lexikon)",
      _modell_widerspruch("VW Passat B8 21.900", VW) is None)
check("K: Opel Insignia vs Mokka bleibt BEWUSST unknown",
      _modell_widerspruch("Opel Mokka 12.900", OPEL) is None)
check("L/M: ohne Nutzer-Kennung ist gar kein Modellwiderspruch moeglich",
      _modell_widerspruch("Opel Mokka 12.900",
                          {"marke_tokens": {"opel"}, "modell_kennungen_user": {}}) is None)

print()
if _fails:
    print(str(len(_fails)) + " FEHLGESCHLAGEN (Modellfamilie):")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("Alle Modellfamilien-Checks bestanden.")
