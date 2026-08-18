"""
Fahrzeugvarianten-Erkennung: Familie / Motor / Variante — deterministisch, KEIN Netzwerk.

Hintergrund (Audit von `_ist_fremdmodell`): `modell_tokens` mischte Modellfamilie
("3er"), Motorbezeichnung ("320d") und Variantenwörter ("gran", "turismo"). Das
Zielsignal-Veto

    if worte & ziel_model: return None

konnte deshalb durch einen MOTOR-Treffer ausgelöst werden und neutralisierte einen
gleichzeitigen Widerspruch auf VARIANTENebene: "BMW 320d Gran Turismo" galt als
Zielfahrzeug eines BMW 320d G20.

Die Fixtures unten sind WÖRTLICH aus der Produktions-DB übernommen (Stand dieses
Sprints), damit der Test hermetisch bleibt und trotzdem an echten Strukturen misst.
Erwartungswerte folgen ausschließlich diesen Daten — nicht dem Allgemeinwissen. Wo
die DB anders modelliert, als man erwarten würde, ist das im Test vermerkt.

    python test_modell_varianten.py
"""
import os
import re
import sys
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_var_"), "test.db")
sys.path.insert(0, ".")

from types import SimpleNamespace                                        # noqa: E402

from app.marktvergleich import (                                         # noqa: E402
    _ist_fremdmodell, _ist_fremdvariante, _varianten_zone, _variantenteil,
    _variantenvokabular, _wort_tokens, baue_ziel,
)

_fails: list[str] = []


def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        _fails.append(name)


# §DB-Trust: die Variantenpruefung verwirft hart und braucht deshalb einen
# VERIFIZIERTEN Karosserie-Fakt. Dieser Test prueft den Mechanismus, nicht die
# Vertrauensregel — die Vorbedingung wird hier gesetzt. Die Trust-Regel selbst
# deckt test_db_trust.py ab.
_VERIF = {"karosserie": {"status": "verified", "source": "Testfixture"}}


def b(bid, marke, modell, generation, karosserie):
    return {"id": bid, "marke": marke, "modell": modell, "generation": generation,
            "karosserie": karosserie, "verification": _VERIF}


# ── Baureihen, wörtlich aus der Produktions-DB ─────────────────────────────
B_3ER = b("bmw-3er-g20-g21", "BMW", "3er", "G20/G21", '["Limousine", "Touring"]')
B_2ER_COUPE = b("bmw-2er-coupe-g42", "BMW", "2er Coupé", "G42", '["Coupé"]')
B_2ER_GC = b("bmw-2er-gran-coupe-f44", "BMW", "2er Gran Coupé", "F44", '["Limousine"]')
B_4ER = b("bmw-4er-g22-g23-g26", "BMW", "4er", "G22/G23/G26",
          '["Coupé", "Cabrio", "Gran Coupé"]')
B_GLE = b("mercedes-benz-gle-w167", "Mercedes-Benz", "GLE", "W167", '["SUV"]')
# Die DB führt "Shooting Brake" im EIGENEN karosserie-Array der CLA C118 — und
# zusätzlich eine eigene Baureihe "CLA Shooting Brake". Diese Doppelmodellierung ist
# der Grund, warum die CLA unten NICHT abgelehnt wird (siehe Bericht/offene Punkte).
B_CLA = b("mercedes-benz-cla-c118", "Mercedes-Benz", "CLA", "C118",
          '["Coupé", "Shooting Brake"]')
B_A4 = b("audi-a4-b9", "Audi", "A4", "B9", '["Limousine", "Avant", "Allroad"]')
B_C_KLASSE = b("mercedes-benz-c-klasse-w205", "Mercedes-Benz", "C-Klasse", "W205",
               '["Limousine", "Kombi", "Coupé", "Cabriolet"]')
# vw-golf-8 hat in der DB KEINE Karosserieangabe (NULL) — bewusst so übernommen.
B_GOLF = b("vw-golf-8", "Volkswagen", "Golf", "8", None)

ALLE_B = [
    B_3ER, B_2ER_COUPE, B_2ER_GC, B_4ER, B_GLE, B_CLA, B_A4, B_C_KLASSE, B_GOLF,
    b("bmw-2er-active-tourer-u06", "BMW", "2er Active Tourer", "U06", '["Kompaktvan"]'),
    b("bmw-6er-f12-f13-f06", "BMW", "6er", "F12/F13/F06",
      '["Cabrio", "Coupé", "Gran Coupé"]'),
    b("bmw-6er-gran-turismo-g32", "BMW", "6er Gran Turismo", "G32", '["Fließheck"]'),
    b("mercedes-benz-gle-coupé-c167", "Mercedes-Benz", "GLE Coupé", "C167",
      '["SUV-Coupé"]'),
    b("mercedes-benz-cla-shooting-brake-x118", "Mercedes-Benz", "CLA Shooting Brake",
      "X118", '["Kombi"]'),
    b("audi-rs-3-8y", "Audi", "RS 3", "8Y", '["Sportback", "Limousine"]'),
    b("audi-rs-3-sportback-8v", "Audi", "RS 3 Sportback", "8V",
      '["Kompaktwagen", "Sportback"]'),
    b("audi-rs-4-b7", "Audi", "RS 4", "B7", '["Limousine", "Avant", "Cabriolet"]'),
    b("audi-rs-4-avant-b9", "Audi", "RS 4 Avant", "B9", '["Kombi"]'),
    # Familien, deren Suffix eine gefaehrliche KURZFORM waere:
    b("audi-e-tron-2019", "Audi", "e-tron", "2019", '["SUV", "SUV Coupé"]'),
    b("audi-e-tron-gt-typ-4j", "Audi", "e-tron GT", "Typ 4J",
      '["Sportlimousine", "Coupé"]'),
    b("audi-tt-typ-fv", "Audi", "TT", "FV", '["Coupé", "Roadster"]'),
    b("audi-tt-rs-8j", "Audi", "TT RS", "8J", '["Coupé", "Roadster"]'),
    b("toyota-yaris-i", "Toyota", "Yaris", "I", '["Kleinwagen"]'),
    b("toyota-yaris-cross-i", "Toyota", "Yaris Cross", "I", '["SUV"]'),
    b("opel-zafira-c", "Opel", "Zafira", "C", '["Kompaktvan"]'),
    b("opel-zafira-life-1", "Opel", "Zafira Life", "1. Generation",
      '["Großraumlimousine"]'),
    b("ford-mustang-sixth-generation", "Ford", "Mustang", "VI", '["Coupé", "Cabriolet"]'),
    b("ford-mustang-mach-e-first", "Ford", "Mustang Mach-E", "I",
      '["SUV", "Crossover"]'),
]

ALLE_M = [
    {"baureihe_id": "bmw-3er-g20-g21", "bezeichnung": "320d", "motorcode": "B47D20"},
    {"baureihe_id": "bmw-3er-g20-g21", "bezeichnung": "320i", "motorcode": "B48B20"},
    {"baureihe_id": "bmw-2er-coupe-g42", "bezeichnung": "220d", "motorcode": "B47D20"},
    {"baureihe_id": "bmw-2er-gran-coupe-f44", "bezeichnung": "220d", "motorcode": "B47D20"},
    {"baureihe_id": "bmw-4er-g22-g23-g26", "bezeichnung": "420d", "motorcode": "B47D20"},
    {"baureihe_id": "mercedes-benz-gle-w167", "bezeichnung": "GLE 350 d"},
    {"baureihe_id": "mercedes-benz-cla-c118", "bezeichnung": "CLA 200 d"},
    {"baureihe_id": "audi-a4-b9", "bezeichnung": "40 TDI"},
    {"baureihe_id": "vw-golf-8", "bezeichnung": "2.0 TDI"},
    {"baureihe_id": "mercedes-benz-c-klasse-w205", "bezeichnung": "C 220 d"},
]

VOKABULAR = _variantenvokabular(ALLE_B)


def ziel_fuer(baureihe, motor):
    req = SimpleNamespace(marke=baureihe["marke"], modell=baureihe["modell"],
                          baujahr=2020, kilometerstand=100_000, motor=motor,
                          kraftstoff="Diesel", getriebe="Automatik", preis_eur=25_000)
    mm = next((m for m in ALLE_M if m["baureihe_id"] == baureihe["id"]
               and m["bezeichnung"] == motor), None)
    return baue_ziel(baureihe, mm, req, ALLE_B, ALLE_M)


def fremd(text, ziel):
    """Wie in _bewerte: Wort-Tokens plus isolierte Kartenzone."""
    return _ist_fremdmodell(_wort_tokens(text), ziel, _varianten_zone(text))


# ══ Vokabular: belastbare Phrasen, keine Kurzformen ════════════════════════
check("Vokabular: 'Gran Turismo' aus der 6er-Familie", "gran turismo" in VOKABULAR)
check("Vokabular: 'Gran Coupé' aus der 2er-Familie", "gran coupé" in VOKABULAR)
check("Vokabular: 'Shooting Brake' aus der CLA-Familie", "shooting brake" in VOKABULAR)
check("Vokabular: 'Active Tourer' aus der 2er-Familie", "active tourer" in VOKABULAR)
check("Vokabular: einwortige Varianten nur mit Karosserie-Bestaetigung",
      {"coupé", "avant", "sportback"} <= VOKABULAR)
# Genau die gefaehrlichen Kurzformen fallen KONSTRUKTIV heraus: 'gt' entsteht aus
# "e-tron GT", 'rs' aus "TT RS" — beide sind zu kurz und keine Karosseriewerte.
for kurz in ("gt", "rs", "cross", "life", "e-tron", "mach-e"):
    check("Vokabular: Kurzform %r ist KEINE Variantenphrase" % kurz,
          kurz not in VOKABULAR)
check("Vokabular: keine reinen Zahlen", not any(p.isdigit() for p in VOKABULAR))
check("Variantenteil: fuehrende Zahl wird entfernt",
      _variantenteil("RS 3 Sportback") == "sportback"
      and _variantenteil("6er Gran Turismo") == "gran turismo")

# ══ A/B — BMW 2er Coupé (G42) vs 2er Gran Coupé (F44) ══════════════════════
# Zwei EIGENE Baureihen, die sich die Motorbezeichnung 220d teilen.
Z_G42 = ziel_fuer(B_2ER_COUPE, "220d")
Z_F44 = ziel_fuer(B_2ER_GC, "220d")
check("A: passender Motor 220d + Fremdvariante 'Gran Coupé' -> fremd",
      fremd("BMW 220d Gran Coupé", Z_G42) == "gran coupé")
check("A: die eigene Variante 'Coupé' bleibt zulaessig",
      fremd("BMW 220d Coupé M Sport", Z_G42) is None)
check("B: fuer das Gran Coupé ist 'Gran Coupé' die EIGENE Variante",
      fremd("BMW 220d Gran Coupé", Z_F44) is None)
check("B: derselbe Text wird je nach Ziel unterschiedlich entschieden",
      fremd("BMW 220d Gran Coupé", Z_G42) == "gran coupé"
      and fremd("BMW 220d Gran Coupé", Z_F44) is None)

# ══ C — BMW 4er: Gran Coupé gehoert zur eigenen Familie ════════════════════
Z_4ER = ziel_fuer(B_4ER, "420d")
check("C: 4er fuehrt 'Gran Coupé' im eigenen karosserie-Array",
      "gran coupé" in Z_4ER["ziel_varianten"])
check("C: 'BMW 420d Gran Coupé' wird NICHT abgelehnt",
      fremd("BMW 420d Gran Coupé", Z_4ER) is None)
check("C: 'Gran Turismo' bleibt auch fuer den 4er fremd",
      fremd("BMW 420d Gran Turismo", Z_4ER) == "gran turismo")

# ══ D — Mercedes GLE vs GLE Coupé ══════════════════════════════════════════
Z_GLE = ziel_fuer(B_GLE, "GLE 350 d")
check("D: GLE fuehrt nur 'SUV' -> 'Coupé' ist fremd",
      fremd("Mercedes GLE 350 d Coupé", Z_GLE) == "coupé")

# ══ E — CLA: die DB modelliert Shooting Brake DOPPELT ══════════════════════
# Eigene Baureihe "CLA Shooting Brake" UND "Shooting Brake" im karosserie-Array der
# CLA C118. Nach der Datenlage ist es damit die EIGENE Variante -> keine Ablehnung.
# Das ist kein Fehler dieser Logik, sondern eine Inkonsistenz der Daten (offener Punkt).
Z_CLA = ziel_fuer(B_CLA, "CLA 200 d")
check("E: 'Shooting Brake' steht im eigenen karosserie-Array der CLA",
      "shooting brake" in Z_CLA["ziel_varianten"])
check("E: daher wird 'CLA 200 d Shooting Brake' NICHT abgelehnt (DB-Lage)",
      fremd("Mercedes CLA 200 d Shooting Brake", Z_CLA) is None)

# ══ F/G/H — eigene Varianten duerfen NICHT ablehnen ════════════════════════
Z_A4 = ziel_fuer(B_A4, "40 TDI")
check("F: A4 fuehrt 'Avant' im eigenen karosserie-Array",
      "avant" in Z_A4["ziel_varianten"])
check("F: 'Audi A4 40 TDI Avant' wird NICHT abgelehnt",
      fremd("Audi A4 40 TDI Avant", Z_A4) is None)

Z_GOLF = ziel_fuer(B_GOLF, "2.0 TDI")
check("G: 'Variant' ist mangels Modellnamen-Beleg keine Variantenphrase",
      "variant" not in VOKABULAR)
check("G: 'VW Golf 2.0 TDI Variant' wird NICHT abgelehnt",
      fremd("VW Golf 2.0 TDI Variant", Z_GOLF) is None)

Z_C = ziel_fuer(B_C_KLASSE, "C 220 d")
check("H: 'T-Modell' ist mangels Modellnamen-Beleg keine Variantenphrase",
      "t-modell" not in VOKABULAR)
check("H: 'Mercedes C 220 d T-Modell' wird von der Variantenlogik NICHT abgelehnt",
      _ist_fremdvariante(_varianten_zone("Mercedes C 220 d T-Modell"), Z_C) is None)

# ══ BMW 3er G20 — der Ausloeserfall ════════════════════════════════════════
Z_G20 = ziel_fuer(B_3ER, "320d")
check("G20: Familie, Motor und Variante sind getrennt gefuehrt",
      Z_G20["familie_tokens"] == {"3er"}
      and "320d" in Z_G20["ziel_motor_tokens"]
      and "320d" not in Z_G20["familie_tokens"])
check("G20: eigene Varianten sind Limousine und Touring",
      Z_G20["ziel_varianten"] == {"limousine", "touring"})
check("G20: passender Motor 320d + 'Gran Turismo' -> fremd",
      fremd("BMW 320d Gran Turismo", Z_G20) == "gran turismo")
check("G20: 'Touring' wird von der Variantenlogik NICHT abgelehnt "
      "(dafuer ist die Generations-Inference zustaendig)",
      _ist_fremdvariante(_varianten_zone("BMW 320d Touring"), Z_G20) is None)
check("G20: ein sauberes Zielfahrzeug bleibt unberuehrt",
      fremd("BMW 320d Sport Line Limousine", Z_G20) is None)

# ── KEIN GT-Hack (§7) ──────────────────────────────────────────────────────
check("GT: 'BMW 320d GT M Sport' wird NICHT abgelehnt",
      fremd("BMW 320d GT M Sport 360 Ad.LED HUD AHK CarPlay", Z_G20) is None)
check("GT: 'BMW 3GT' wird NICHT abgelehnt",
      fremd("Zum Verkauf steht ein sehr gepflegter BMW 3GT mit geringer Laufleistung",
            Z_G20) is None)

# ── Unicode-sichere Grenzen (§8) ───────────────────────────────────────────
UMLAUT = "Die Front ist geprägt, das Fahrzeug verfügt über viel und trägt LED"
check("Unicode: 'geprägt/verfügt/trägt' erzeugen kein GT-Signal",
      _ist_fremdvariante(UMLAUT, Z_G20) is None)
check("Unicode: eine ASCII-Wortgrenze wuerde hier falsch anschlagen",
      bool(re.search(r"(?<![A-Za-z0-9])gt(?![A-Za-z0-9])", UMLAUT, re.IGNORECASE)))
check("Unicode: die verwendete \\w-Grenze schlaegt NICHT an",
      not re.search(r"(?<!\w)gt(?!\w)", UMLAUT, re.IGNORECASE))
check("Unicode: 'Coupé' mit Akzent wird als Phrase gefunden",
      _ist_fremdvariante("Mercedes GLE 350 d Coupé", Z_GLE) == "coupé")
check("Unicode: 'Coupéfahrer' ist kein Variantenbeleg (kein Wortende)",
      _ist_fremdvariante("Mercedes GLE 350 d Coupéfahrer gesucht", Z_GLE) is None)
check("Unicode: Bindestrich gilt als Wortgrenze ('Gran Turismo-Stil')",
      _ist_fremdvariante("BMW 320d im Gran Turismo-Stil", Z_G20) == "gran turismo")

# ── Zonen (§9): Heading und Beschreibung ja, Link-Ziel und Bild nein ───────
HEADING = "## [BMW 320d Gran Turismo M Sport](/s-anzeige/bmw-320d-gt/3487086246-216-2904)"
BESCHR = "Zum Verkauf steht ein BMW 320d Gran Turismo Sport Line mit 190 PS"
check("Zone: Variante im eigenen Heading zaehlt",
      _ist_fremdvariante(_varianten_zone(HEADING), Z_G20) == "gran turismo")
check("Zone: Variante in der eigenen Beschreibung zaehlt",
      _ist_fremdvariante(_varianten_zone(BESCHR), Z_G20) == "gran turismo")
SLUG = "## [BMW 320d Sport Line](/s-anzeige/bmw-320d-gran-turismo-xy/3486676725-216-5189)"
check("Zone: ein Link-Ziel allein ist kein Fahrzeugtext",
      _ist_fremdvariante(_varianten_zone(SLUG), Z_G20) is None)
BILD = "![BMW 320d Gran Turismo Vorschau](https://img.example/x.jpg) BMW 320d Sport Line"
check("Zone: Vorschaubild-Syntax wird entfernt",
      _ist_fremdvariante(_varianten_zone(BILD), Z_G20) is None)
check("Zone: ohne Text (Quellenanzeige) bleibt das alte Verhalten",
      _ist_fremdmodell(_wort_tokens("BMW 320d Gran Turismo"), Z_G20) is None)

# ── Determinismus ──────────────────────────────────────────────────────────
check("Determinismus: mehrfacher Aufruf liefert dasselbe Ergebnis",
      len({fremd("BMW 220d Gran Coupé", Z_G42) for _ in range(5)}) == 1)
check("Determinismus: Wortstellung aendert das Ergebnis nicht",
      fremd("Gran Coupé BMW 220d", Z_G42) == fremd("BMW 220d Gran Coupé", Z_G42))
check("Determinismus: Vokabular ist reihenfolgeunabhaengig",
      _variantenvokabular(ALLE_B) == _variantenvokabular(list(reversed(ALLE_B))))

print()
if _fails:
    print(str(len(_fails)) + " FEHLGESCHLAGEN:")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("Alle Modell-/Varianten-Tests bestanden.")
