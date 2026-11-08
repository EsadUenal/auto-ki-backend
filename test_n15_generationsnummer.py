"""
N15 -- "Generation UXC1 (2. Generation)": die Nummer darf nur erscheinen,
wenn sie in derselben Fundstelle strukturell an GENAU den belegten Code
gebunden ist. Eine frei im Text stehende "N. Generation" (kein Bezug zum Code)
belegt die Nummer der Kennung nicht.

    python test_n15_generationsnummer.py
"""
import os
import socket
import tempfile

_tmp = tempfile.mkdtemp(prefix="n15_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


socket.socket.connect = _gesperrt
socket.create_connection = _gesperrt

from app.models import KaufCheckRequest  # noqa: E402
from app.technical_research import _ziel, werte_identitaet_aus  # noqa: E402
from app.vehicle_identity import VehicleIdentity  # noqa: E402
from app.vergleichstabelle import als_markdown, baue_zeilen  # noqa: E402

_FEHLER: list[str] = []


def check(name, bedingung, detail=""):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def t(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


ADAC = t("https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/",
         "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25): Technische Daten | ADAC",
         "Marke Lexus Modell RC F Typ Ultimate Edition Automatik Baureihe RC (XC1) "
         "(12/14 - 06/25) Herstellerinterne Baureihenbezeichnung XC1 Motorcode 2UR-GSE "
         "Leistung maximal in PS 464 Antriebsart Hinterrad Getriebeart Automatikgetriebe")
CARWIKI = t("https://carwiki.de/lexus-rc-hsn-tsn-schluesselnummer/",
            "Lexus RC HSN & TSN | Tabelle aller Schluesselnummern",
            "Uebersicht: Alle Schluesselnummern (HSN/TSN) der Lexus RC Baureihen. "
            "Hersteller: Lexus Fahrzeug: RC Baureihe: RC XC1 Karosserie: Coupe "
            "MODELL HSN SCHLUESSELNUMMER TSN SCHLUESSELNUMMER KRAFTSTOFF HUBRAUM LEISTUNG BAUJAHR "
            "Lexus RC F Carbon Automatik 5013 AJM Benzin 4969 cm3 477 PS 2014 bis 2025")
AMS = t("https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
        "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
        "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Leistung 181 PS bis "
        "477 PS. LEXUS RC F TRACK EDITION IM SUPERTEST: Was kann der V8-Sauger auf der "
        "Rennstrecke?")
AMS_OHNE_NR = t("https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
                "Lexus RC (XC1) im Ueberblick (ab 2015)",
                "Lexus RC (XC1) im Ueberblick (ab 2015). Leistung 181 PS bis 477 PS. "
                "LEXUS RC F TRACK EDITION IM SUPERTEST: Was kann der V8-Sauger?")
BILD = t("https://www.autobild.de/marken-modelle/lexus/rc/",
         "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
         "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Leistung 181 PS bis "
         "477 PS. Der Lexus RC F bietet einen V8-Sauger.")
FREMD2 = t("https://www.zweites-magazin.de/lexus-gebraucht/",
           "Lexus Gebrauchtwagen-Ratgeber",
           "Lexus RC F Ratgeber. Die Lexus IS Limousine (2. Generation) ist beliebt. "
           "Der Lexus RC F ist ein V8-Coupe.")
# Frei stehende Nummer ohne Bezug zum Code (andere Modellreihe derselben Marke)
FREMD = t("https://www.autobild.de/ratgeber/lexus-rc-f/", "Lexus RC F Ratgeber",
          "Der Lexus RC F steht in der Nachfolge, der Lexus RC F (2. Generation des "
          "IS-Antriebs) ist ein V8-Coupe 2015.")
FREMD2 = t("https://www.adac.de/rund-ums-fahrzeug/test/lexus-rc-f/", "Lexus RC F Test",
           "Lexus RC F im Test: 2. Generation der Lexus Performance-Modelle, ein V8-Coupe.")


def lauf(quellen, modell="RC F"):
    z = _ziel("Lexus", modell, 2017, "5.0 V8", None)
    ident, _ = werte_identitaet_aus(quellen, z)
    return ident


class _W:
    def __init__(self, i):
        self.identitaet = i


def zeile(web):
    v = VehicleIdentity(make="Lexus", model="RC F", year=2017)
    v.field_evidence = {}
    v.apply_web_evidence(_W(web))
    req = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017)
    z = [x for x in baue_zeilen(req, None, None, identity=v) if x.kriterium == "Baujahr"]
    return als_markdown(z).splitlines()[-1] if z else ""


# A) belegte Kennung, KEINE Nummer
a = lauf([ADAC, CARWIKI, AMS_OHNE_NR])
za = zeile(a)
print("A:", a.generation, a.generation_nummer, "|", za)
check("A1) Kennung belegt, keine Nummer belegt -> keine Nummer",
      a.generation == "XC1" and not a.generation_nummer, f"{a.generation!r} {a.generation_nummer!r}")
check("A2) sichtbare Zeile nennt Kennung ohne Zusatz", "Generation XC1" in za and "Generation)" not in za
      and ". Generation" not in za, za)

# B) frei stehende fremde Nummer wird NICHT übernommen
b = lauf([ADAC, CARWIKI, AMS_OHNE_NR, FREMD, FREMD2])
zb = zeile(b)
print("B:", b.generation, b.generation_nummer, "|", zb)
check("B1) frei im Text stehende '2. Generation' (IS) wird nicht übernommen",
      b.generation == "XC1" and not b.generation_nummer, f"{b.generation_nummer!r}")
check("B2) sichtbare Zeile ohne unbelegten Zusatz", "2. Generation" not in zb, zb)

# C) separat belegte Nummer bleibt erhalten
c = lauf([ADAC, CARWIKI, AMS, BILD])
zc = zeile(c)
print("C:", c.generation, c.generation_nummer, "|", zc)
check("C1) strukturell an den Code gebundene Nummer bleibt",
      c.generation == "XC1" and c.generation_nummer == "1. Generation", f"{c.generation_nummer!r}")
check("C2) sichtbare Zeile zeigt Kennung UND Nummer", "XC1 (1. Generation)" in zc, zc)

# D) unbekannte Generation
d = lauf([FREMD, FREMD2])
zd = zeile(d)
print("D:", d.generation, d.generation_nummer, "|", zd)
check("D1) unbekannte Generation -> keine Nummer, keine Generationszeile aus Web",
      not d.generation and not d.generation_nummer and "Generation" not in zd, zd)

print()
if _FEHLER:
    print("FEHLER:", _FEHLER)
    raise SystemExit(1)
print("ALLE OK")
