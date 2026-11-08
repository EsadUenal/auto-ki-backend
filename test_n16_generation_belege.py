"""
N16 -- Generation: bei "Herstellerdokument + unabhängiger Rollenbeleg" beide
tatsächlich verwendeten Belege getrennt zeigen (API + sichtbarer Bericht).

Ursache: `konsens(mit_jahr)` bevorzugt Claims mit Jahresfenster. Das Dokument
(Rettungskarte, "ab 09/2014") hat eines, der Rollenbeleg (z.B. ADAC "Baureihe
RC (UXC1)") nicht -> er trug die Verknüpfung, fiel aber aus `domain_namen`.

    python test_n16_generation_belege.py
"""
import os
import socket
import tempfile

_tmp = tempfile.mkdtemp(prefix="n16_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


socket.socket.connect = _gesperrt
socket.create_connection = _gesperrt

from app.kaufaktionen import build_kaufaktionen  # noqa: E402
from app.kaufcheck_bericht import bericht  # noqa: E402
from app.models import KaufCheckRequest, PriceAssessment, WebVehicleIdentity  # noqa: E402
from app.technical_research import _ziel, werte_identitaet_aus  # noqa: E402
from app.vehicle_identity import VehicleIdentity  # noqa: E402

_FEHLER: list[str] = []


def check(name, bedingung, detail=""):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def t(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


ADAC = t("https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/",
         "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25): Technische Daten | ADAC",
         "Marke Lexus Modell RC F Typ Ultimate Edition Automatik Baureihe RC (UXC1) "
         "(12/14 - 06/25) Herstellerinterne Baureihenbezeichnung UXC1 Motorcode 2UR-GSE "
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
        "477 PS. LEXUS RC F TRACK EDITION IM SUPERTEST: Was kann der V8-Sauger?")
PDF = t("https://www.lexus.de/content/dam/lexus/nmsc/germany/lexus-besitzer/"
        "fahrzeuginformationen/RCF_tcm-3150-1284184.pdf", "Lexus RC F Rettungskarte",
        "Lexus RC F\n(UXC1 (EU, M), ab 09/2014)\nStand: 10/2014\n"
        "Lexus RC F (UXC1 (EU, ab 09/2014)\n12V Spannungsversorgung trennen\n"
        "Hinweise zum Transport\nZugang 12V Batterie\nLexus RC F\n(UXC1 (EU, M), ab 09/2014)")
ADAC_XC1 = t(ADAC["url"], ADAC["title"], ADAC["content"].replace("UXC1", "XC1"))

Z = _ziel("Lexus", "RC F", 2017, "5.0 V8", None)


def ident(quellen):
    i, _ = werte_identitaet_aus(quellen, Z)
    return i


def bericht_text(web, *, entfernen=False):
    if entfernen:
        d = web.model_dump()
        d.pop("generation_belege", None)
        d["feldwerte"]["generation"]["domain_namen"] = {"lexus.de"}   # Stand vor N16
        d["feldwerte"]["generation"]["domains"] = 1
        web = WebVehicleIdentity.model_validate(d)
    v = VehicleIdentity(make="Lexus", model="RC F", year=2017)
    v.field_evidence = {}
    v.apply_web_evidence(type("W", (), {"identitaet": web})())
    req = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017)
    akt = build_kaufaktionen(req, None, None, [])
    text = bericht(req, v, None, None, [], akt, [], "kaufen_nach_besichtigung",
                   PriceAssessment(), False, None, None, ["Webquellen"])
    return v, "\n".join(x for x in text.splitlines()
                        if x.startswith("- Generation") or x.startswith("  Belege")
                        or x.startswith("  Quellen"))


# 1) Rettungskarte (Hersteller) + ADAC (Rolle) -> Nachweis über Verknüpfung
for name, q in (("1) Dokument+ADAC", [ADAC, PDF]), ("2) voller RC-F-Replay", [ADAC, CARWIKI, AMS, PDF])):
    w = ident(q)
    rollen = {(b["rolle"], b["domain"]) for b in w.generation_belege}
    check(f"{name}: API trennt Kennung (lexus.de) und Rolle (adac.de)",
          w.generation == "UXC1" and rollen == {("kennung_herstellerdokument", "lexus.de"),
                                                ("baureihe_rollenbeleg", "adac.de")}, str(rollen))
    check(f"{name}: Quellenzahl passt zur Anzeige (2 Domains)",
          w.feldwerte["generation"]["domains"] == 2
          and w.feldwerte["generation"]["domain_namen"] == {"lexus.de", "adac.de"})
    v, txt = bericht_text(w)
    print(txt)
    check(f"{name}: Bericht nennt Dokument und Rollenbeleg getrennt, '2 Webquelle(n)'",
          "Kennung UXC1 im Herstellerdokument (lexus.de)" in txt
          and "Rolle als Baureihe durch unabhängige Quelle belegt (adac.de)" in txt
          and "2 Webquelle(n)" in txt, txt)
    check(f"{name}: Bestätigung unverändert", w.generation_nachweisweg in ("quelle", "herstelleridentifikation"),
          str(w.generation_nachweisweg))

# 3) anderer Nachweisweg (Konsens ohne Dokument): keine Belege-Zeile erfunden
w3 = ident([ADAC_XC1, CARWIKI, AMS])
v3, t3 = bericht_text(w3)
print(t3)
check("3) anderer Nachweisweg: keine generation_belege, bisherige Quellenzeile",
      w3.generation == "XC1" and w3.generation_belege == [] and "Belege:" not in t3
      and "Quellen:" in t3, t3)

# 4) älteres Ergebnis ohne Rollenbeleg-Daten
w4 = ident([ADAC, PDF])
v4, t4 = bericht_text(w4, entfernen=True)
print(t4)
check("4) älteres Ergebnis (ohne generation_belege): keine erfundene Quelle/Rolle",
      "Belege:" not in t4 and "Quellen: lexus.de." in t4 and "1 Webquelle(n)" in t4, t4)

print()
if _FEHLER:
    print("FEHLER:", _FEHLER)
    raise SystemExit(1)
print("ALLE OK")
