"""
Motor- und Generations-Evidenz im Marktvergleich — deterministisch, KEIN Netzwerk.

Hintergrund (Live-Audit Opel Insignia B, 2026-08-19): Der Lauf endete mit
`research_failed`, obwohl echtes Insignia-B-Material im Korpus lag. Drei Ursachen,
alle in dieser Datei abgesichert:

  1. Der interne DB-MOTORCODE (F20DTH) floss in `ziel_motor_tokens` und aktivierte
     damit die harte Motorpruefung. Ein solcher Code steht in Kleinanzeigen
     praktisch nie -> "Motorisierung auf der Karte nicht belegt" auf 100 % der
     Karten. Er wird jetzt getrennt gefuehrt (`motorcode_tokens`) und wirkt nur
     noch BESTAETIGEND.
  2. `_RE_VERKAUFSBEZEICHNUNG` ist eine reine Formpruefung. Sie traf Hex-Gruppen
     aus den Bild-UUIDs der Vorschaubilder ("928c", "443d") und Messwerte
     ("174cv", "194ps", "125kw") — 57 von 95 harten Motor-Ablehnungen waren
     Rauschen. Jetzt: nur sichtbare Variantenzone, keine Messwerte, gleiche
     Ziffernzahl wie die Nutzerbezeichnung.
  3. Fehlte fuer die Baureihe ein Generationscode, kassierte JEDE Karte eine
     Strafe fuer eine Luecke UNSERER Datenbasis. Unknown bleibt jetzt unknown.

Dazu das Sicherheitsgate: ohne trennscharfe Nutzer-Verkaufsbezeichnung wurde im
Audit ein Ford Transit Werkstattwagen (29.999 EUR) zum "bedingten" Vergleich fuer
einen Opel Insignia. Das ist hier als Negativregression gesichert.

    python test_motor_evidence_trust.py
"""
import os
import sys
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_motor_"), "test.db")
sys.path.insert(0, ".")

# §Source-Policy: Der Production-Default gibt KEINE Marktquelle zum Preisbilden
# frei (app/config.ALLOWED_MARKET_SOURCES ist leer). Dieser Test prueft die
# ANALYSE-ENGINE und braucht dafuer die historischen/synthetischen Testdomains —
# die Freigabe gilt ausschliesslich in diesem Testprozess und ist KEINE
# produktive Qualifikation der Quelle. Siehe _source_policy_testharness.py.
import _source_policy_testharness  # noqa: E402,F401

from types import SimpleNamespace                                        # noqa: E402

from app.marktvergleich import (                                         # noqa: E402
    _bewerte, _eindeutige_karosserie, _extrahiere_aus_text,
    _fremde_bezeichnungen_im_text, _modell_anker, baue_ziel,
)

_fails: list[str] = []


def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        _fails.append(name)


QUELLE = "Opel Media Deutschland, Motorenuebersicht Insignia B (Abruf 2026-08-19)"
URL = "https://www.kleinanzeigen.de/s-autos/c216"
TITEL = "Gebrauchtwagen Angebote | kleinanzeigen.de"

# Genau die Bild-URL-Form, die im Live-Audit das Hex-Rauschen erzeugt hat:
# die UUID-Gruppe "928c" hat exakt die Form einer Verkaufsbezeichnung.
BILD = ("https://img.kleinanzeigen.de/api/v1/prod-ads/images/fe/"
        "fe8cb671-8899-41b5-928c-528785e40df")


def karte(titel, lid, preis, km, ez, beschr):
    """Eine Kleinanzeigen-Karte im Markdown-Format, wie Tavily sie liefert."""
    return ("* [![" + titel + " Vorschau](" + BILD + ")\n\n"
            "  20](/s-anzeige/x/" + lid + "-216-1111)\n\n  12307 Berlin\n\n  Heute\n\n"
            "  ## [" + titel + "](/s-anzeige/x/" + lid + "-216-1111)\n\n  " + beschr + "\n\n"
            "  " + preis + " EUR\n\n  " + km + " km   EZ " + ez + "\n")


def bewerte(ziel, titel, beschr, preis="18.500", ez="05/2020", km="112.000",
            fuell_titel="Opel Insignia 2.0 Diesel", fuell_beschr="Diesel Automatik"):
    """Bewertet EINE Karte im vollen Produktionspfad (Segmentierung + _bewerte)."""
    fuell = (karte(fuell_titel + " A", "3400000801", "18.100", "113.500", ez, fuell_beschr)
             + karte(fuell_titel + " B", "3400000802", "18.300", "113.700", ez, fuell_beschr))
    raw = "## Ergebnisse\n\n" + karte(titel, "3400009999", preis, km, ez, beschr) + fuell
    roh = _extrahiere_aus_text(TITEL + "\n\n" + raw, URL, "market_category",
                               grenzen=(len(TITEL) + 1, len(TITEL) + 2),
                               seiten_body=_eindeutige_karosserie(URL))
    ziel_preis = int(preis.replace(".", ""))
    for b in roh:
        if b.preis_eur == ziel_preis:
            return _bewerte(b, ziel)
    return None


def opel_ziel(verification=None, motor_user="2.0 Diesel 174 PS"):
    br = {"id": "opel-insignia-b", "marke": "Opel", "modell": "Insignia",
          "generation": "B"}
    if verification is not None:
        br["verification"] = verification
    mm = {"bezeichnung": "2.0 Diesel (174 PS) (Facelift)", "motorcode": "F20DTH",
          "kraftstoff": "Diesel", "leistung_ps": 174}
    req = SimpleNamespace(marke="Opel", modell="Insignia Grand Sport", baujahr=2020,
                          kilometerstand=115_000, motor=motor_user, kraftstoff="Diesel",
                          getriebe="Automatik", preis_eur=17_900)
    motoren = [{"baureihe_id": "opel-insignia-b", "bezeichnung": "1.5 Diesel (122 PS)",
                "motorcode": "D15DTH"},
               {"baureihe_id": "opel-insignia-b",
                "bezeichnung": "2.0 Diesel (174 PS) (Facelift)", "motorcode": "F20DTH"}]
    return baue_ziel(br, mm, req, [br], motoren)


def bmw_ziel():
    br = {"id": "bmw-3er-g20", "marke": "BMW", "modell": "3er", "generation": "G20/G21"}
    mm = {"bezeichnung": "320d", "motorcode": "B47D20", "kraftstoff": "Diesel",
          "leistung_ps": 190}
    req = SimpleNamespace(marke="BMW", modell="320d G20", baujahr=2019,
                          kilometerstand=120_000, motor="320d 190 PS",
                          kraftstoff="Diesel", getriebe="Automatik", preis_eur=24_900)
    return baue_ziel(br, mm, req, [br], [
        {"baureihe_id": "bmw-3er-g20", "bezeichnung": "320d", "motorcode": "B47D20"},
        {"baureihe_id": "bmw-3er-g20", "bezeichnung": "330d", "motorcode": "B57D30"}])


def bmw_karte(ziel, titel, preis="24.100"):
    return bewerte(ziel, titel, "Scheckheft, 190 PS, Diesel", preis=preis,
                   ez="05/2019", km="118.000", fuell_titel="BMW 320d G20",
                   fuell_beschr="Diesel Automatik")


# 1-3) DB-Motorcode aktiviert die harte Motorpruefung NICHT
Z_UNVER = opel_ziel()
check("1: unverified DB-Motorcode landet NICHT in ziel_motor_tokens",
      Z_UNVER["ziel_motor_tokens"] == set())
check("1: er steht getrennt in motorcode_tokens",
      Z_UNVER["motorcode_tokens"] == {"f20dth"})
check("1: unverified DB-Motorcode aktiviert motor_hart NICHT",
      Z_UNVER["motor_hart"] is False)

Z_REV = opel_ziel({"motorvarianten": {"status": "reviewed"}})
check("2: reviewed DB-Motorcode aktiviert motor_hart ebenfalls NICHT",
      Z_REV["motor_hart"] is False and Z_REV["ziel_motor_tokens"] == set())
check("2: reviewed erzeugt auch keine Fremdmotor-Liste",
      Z_REV["fremd_motor_tokens"] == set())

Z_VER = opel_ziel({"motorvarianten": {"status": "verified", "source": QUELLE}})
check("3: verified+Source bleibt wirkungslos, solange es keine trennscharfe "
      "Zielbezeichnung gibt (Opel '2.0 Diesel' ist keine)",
      Z_VER["ziel_motor_tokens"] == set() and Z_VER["fremd_motor_tokens"] == set())
check("3: der Motorcode bleibt auch verified nur bestaetigend",
      Z_VER["motorcode_tokens"] == {"f20dth"})

Z_BMW = bmw_ziel()
check("3b: Nutzerbezeichnung '320d' aktiviert motor_hart weiterhin",
      Z_BMW["motor_hart"] is True and "320d" in Z_BMW["ziel_motor_tokens"])
check("3b: der BMW-Motorcode B47D20 liegt ebenfalls getrennt",
      Z_BMW["motorcode_tokens"] == {"b47d20"})

# 4) Motorcode bestaetigt positiv, bestraft aber nicht bei Fehlen
b_ohne = bewerte(Z_UNVER, "Opel Insignia B 2.0 Diesel Automatik",
                 "Grand Sport, 174 PS, Diesel, Scheckheft")
check("4: ohne Motorcode auf der Karte KEINE Strafe 'Motorisierung nicht belegt'",
      b_ohne is not None and not any("Motorisierung" in g for g in b_ohne.gruende))
b_mit = bewerte(Z_UNVER, "Opel Insignia B 2.0 Diesel F20DTH",
                "Grand Sport, 174 PS, Diesel, Motorcode F20DTH")
check("4: nennt die Karte den Motorcode selbst, gilt der Motor als bestaetigt",
      b_mit is not None and b_mit.engine_variant == "f20dth")

# 5-8) Rauschen vs. echter Motorwiderspruch
ZM = {"320d"}
check("5: User 320d vs Listing '330d' -> harter Fremdmotor",
      _fremde_bezeichnungen_im_text("BMW 330d xDrive Sport Line", ZM) == {"330d"})
check("6: UUID-Hex aus der Bild-URL ist KEIN Motorwiderspruch",
      _fremde_bezeichnungen_im_text("![BMW Vorschau](" + BILD + ") BMW 320d", ZM) == set())
check("6b: auch ein Hex-Fragment im Linkziel bleibt wirkungslos",
      _fremde_bezeichnungen_im_text("[BMW](https://x.de/a/443d-b803) 320d", ZM) == set())
check("7: Messwert '174cv' ist KEIN Motorwiderspruch",
      _fremde_bezeichnungen_im_text("Opel Insignia 174cv Diesel", ZM) == set())
check("7b: '194ps', '125kw', '000km' ebenso wenig",
      _fremde_bezeichnungen_im_text("194ps 125kw 000km", ZM) == set())
check("8: Markdown-Bild liefert ueberhaupt keine Motorevidenz",
      _fremde_bezeichnungen_im_text("![330d Vorschau](https://img.x.de/330d.jpg)",
                                    ZM) == set())
check("8b: strukturell unvergleichbare Kennung (andere Ziffernzahl) zaehlt nicht",
      _fremde_bezeichnungen_im_text("Modell ab1234c", ZM) == set())
check("8c: ohne Zielbezeichnung ist gar kein Widerspruch moeglich",
      _fremde_bezeichnungen_im_text("BMW 330d", set()) == set())

b_uuid = bmw_karte(Z_BMW, "BMW 320d G20 Advantage")
check("8d: Karte mit Bild-UUID '928c' wird NICHT als andere Motorvariante verworfen",
      b_uuid is not None and "andere Motorvariante" not in b_uuid.acceptance_reason)

# 9-10) Generation
check("9: Ziel ohne vertrauenswuerdige Generation -> keine 'nicht bestaetigt'-Strafe",
      b_ohne is not None
      and not any("Generation nicht bestätigt" in g for g in b_ohne.gruende)
      and any("Zielgeneration unbekannt" in g for g in b_ohne.gruende))
check("9b: unbekannte Zielgeneration macht die Karte nicht ungeeignet",
      b_ohne is not None and b_ohne.vergleichbarkeit != "ungeeignet")

b_gen = bmw_karte(Z_BMW, "BMW 320d G20 Advantage")
check("10: explizite Zielgeneration wird weiterhin bestaetigt",
      b_gen is not None and b_gen.generation == "G20"
      and b_gen.generation_evidence == "explicit_card")
# Die BEKANNTE Geschwistergeneration (G21 aus "G20/G21") bleibt ein harter
# Ausschluss — sie steht in `fremd_generationen`.
b_g21 = bmw_karte(Z_BMW, "BMW 320d G21 Touring")
check("10b: bekannte fremde Generation G21 wird weiterhin hart verworfen",
      b_g21 is not None and b_g21.vergleichbarkeit == "ungeeignet"
      and "andere Generation" in b_g21.acceptance_reason)
# Ein UNBEKANNTER Code (F30) darf ohne verifiziertes Chassiswissen NICHT hart
# ablehnen (§DB-Trust) — er bleibt aber unbestaetigt und damit hoechstens
# "bedingt". Diese Deckelung ist die eigentliche Schutzwirkung.
b_f30 = bmw_karte(Z_BMW, "BMW 320d F30 Advantage")
check("10c: unbekannter Code F30 lehnt nicht hart ab (kein verified Chassiswissen)",
      b_f30 is not None and "andere Generation" not in b_f30.acceptance_reason)
check("10d: F30 bleibt aber unbestaetigt und damit hoechstens 'bedingt'",
      b_f30 is not None and b_f30.vergleichbarkeit == "bedingt"
      and any("Generation auf der Karte nicht belegt" in g for g in b_f30.gruende))

# 11-12) Sicherheitsgate: Modellanker
check("11: Modellanker aus 'Insignia Grand Sport' ist 'insignia'",
      _modell_anker("Opel", "Insignia Grand Sport") == {"insignia"})
check("11b: der Anker steht im Zielprofil",
      "insignia" in (Z_UNVER["modell_anker_user"] or set()))
check("11c: generische Zusaetze werden NICHT zum Anker",
      "grand" not in _modell_anker("Opel", "Insignia Grand Sport")
      and "sport" not in _modell_anker("Opel", "Insignia Grand Sport"))
check("11d: die Marke selbst ist kein Anker",
      _modell_anker("Opel", "Opel Insignia") == {"insignia"})
check("11e: Karte mit 'Insignia' passiert das Gate",
      b_ohne is not None and any("Zielmodell belegt" in g for g in b_ohne.gruende))

b_ford = bewerte(Z_UNVER, "Ford Transit Werkstattwagen",
                 "Diesel, Automatik, Werkstatteinrichtung", preis="29.999",
                 ez="05/2020", km="100.707")
check("12: Ford Transit wird beim Insignia-Ziel NICHT verwertbar",
      b_ford is not None and b_ford.vergleichbarkeit == "ungeeignet")
check("12b: Begruendung stammt aus fehlender Zielidentitaet, nicht aus der DB",
      b_ford is not None
      and "Zielmodell auf der Karte nicht belegt" in b_ford.acceptance_reason)

# 13) BMW-Regression bleibt korrekt
check("13: BMW 320d G20 bleibt ein vollwertiger Vergleich",
      b_gen is not None and b_gen.vergleichbarkeit in ("sehr_aehnlich", "aehnlich"))
b_330 = bmw_karte(Z_BMW, "BMW 330d G20 xDrive", preis="29.100")
check("13b: BMW 330d wird beim 320d-Ziel weiterhin hart verworfen",
      b_330 is not None and b_330.vergleichbarkeit == "ungeeignet")
check("13c: beim BMW-Ziel greift das Sicherheitsgate gar nicht "
      "(trennscharfe Nutzerbezeichnung vorhanden)",
      Z_BMW["motor_hart"] is True)

print()
if _fails:
    print(f"{len(_fails)} FEHLER: " + "; ".join(_fails))
    sys.exit(1)
print("Alle Pruefungen bestanden.")
