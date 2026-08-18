"""
Chassiscode -> Karosserie-Zuordnung und Generations-Ableitung.

Deterministisch, KEIN Netzwerk, KEINE Live-Datenbank (die Zielprofile werden aus
synthetischen Baureihen-Dicts gebaut — genau wie app/kaufcheck.py es tut).

Hintergrund: Baureihen wie "G20/G21" fassen zwei Werkscodes zusammen, die sich nur
in der Karosserie unterscheiden. Reale Inserate nennen den Code fast nie. Aus der
geprüften Zuordnung (app/chassis_codes.py) lässt er sich ableiten — aber nur, wenn
genau ein Code zur Karosserie passt.

Fälle A-I aus der Aufgabenstellung.

    python test_chassis_codes.py
"""
import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, ".")
os.environ.setdefault("AUTO_KI_DB_PATH",
                      os.path.join(tempfile.mkdtemp(prefix="vira_cc_"), "test.db"))

from types import SimpleNamespace                                        # noqa: E402

from app.chassis_codes import VERIFIZIERTE_CHASSIS_CODES                 # noqa: E402
from app.marktvergleich import (                                         # noqa: E402
    _bewerte, _extrahiere_aus_text, _inferiere_generation, _karosserie_im_text,
    analysiere_markt, baue_ziel,
)

_fails: list[str] = []


def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        _fails.append(name)


# ── Zielprofile ─────────────────────────────────────────────────────────────
G20_FAMILIE = {"marke": "BMW", "modell": "3er", "generation": "G20/G21",
               "id": "bmw-3er-g20-g21", "karosserie": ["Limousine", "Touring"],
               # §DB-Trust: die Chassis-Inference ist nur mit VERIFIZIERTEM Fakt erlaubt.
               # Dieser Test prueft den Mechanismus, nicht die Vertrauensregel — die
               # Vorbedingung wird deshalb hier ausdruecklich gesetzt. Die Trust-Regel
               # selbst deckt test_db_trust.py ab (Faelle I und J).
               "verification": {"chassis_codes": {"status": "verified",
                                                 "source": "Testfixture"}},
               "chassis_codes": VERIFIZIERTE_CHASSIS_CODES["bmw-3er-g20-g21"]}
ALLE = [G20_FAMILIE, {"id": "bmw-3er-f30", "marke": "BMW", "modell": "3er",
                      "generation": "F30"}]
MOTOREN = [{"baureihe_id": "bmw-3er-g20-g21", "bezeichnung": "320d", "motorcode": "B47D20"},
           {"baureihe_id": "bmw-3er-g20-g21", "bezeichnung": "330i"}]
MOTOR = {"bezeichnung": "320d", "kraftstoff": "Diesel", "leistung_ps": 190,
         "motorcode": "B47D20"}
# "G20" steht ausdrücklich in der Nutzerangabe -> G21 ist Fremdgeneration.
REQ = SimpleNamespace(marke="BMW", modell="3er G20", motor="320d", kraftstoff="Diesel",
                      baujahr=2019, kilometerstand=120_000)
ZIEL = baue_ziel(G20_FAMILIE, MOTOR, REQ, ALLE, MOTOREN)

check("Zielprofil: nur g20 ist Ziel", ZIEL["generation_tokens"] == {"g20"})
check("Zielprofil: g21 ist Fremdgeneration", "g21" in ZIEL["fremd_generationen"])
check("Zielprofil: Chassis-Zuordnung normalisiert übernommen",
      ZIEL["chassis_codes"] == {"g20": "limousine", "g21": "kombi"})

URL = "https://www.kleinanzeigen.de/s-autos/bmw-320d-g20/k0c216"


def _karte(titel, lid, preis, km, ez):
    return (f"![{titel} Vorschau](https://img.kleinanzeigen.de/x.jpg)\n\n"
            f"## [{titel}](/s-anzeige/bmw/{lid}-216-4711)\n\n"
            f"Gepflegtes Fahrzeug, scheckheftgepflegt...\n\n"
            f"{preis} €\n\n{km} km\n\nEZ {ez}\n\n")


NAV = "## Filter\n\n### Preis\n\n## Ergebnisse\n\n"


def _punkte(raw, ziel=ZIEL, titel="BMW 320d gebraucht kaufen"):
    """(Marktanalyse, {preis: Beobachtung}) — inklusive der VERWORFENEN Punkte.

    `analysiere_markt` gibt verworfene Beobachtungen (richtigerweise) nicht heraus;
    für die Prüfung, WARUM etwas verworfen wurde, werden sie hier mit denselben
    Bausteinen nachgebaut.
    """
    seite = {"url": URL, "title": titel, "content": "", "raw_content": raw}
    ma = analysiere_markt([seite], ziel, None)
    text = f"{titel}\n\n{raw}"
    roh = _extrahiere_aus_text(text, URL, "market_category",
                               grenzen=(len(titel) + 1, len(titel) + 2))
    return ma, {b.preis_eur: b for b in (_bewerte(x, ziel) for x in roh)}


# ══ A — Limousine ohne Code -> inferred G20 ═════════════════════════════════
A_RAW = (NAV
         + _karte("BMW 320d Limousine Diesel", "3470000001", "24.900", "118.000", "05/2019")
         + _karte("BMW 320d Limousine Diesel", "3470000002", "25.400", "121.000", "06/2019")
         + _karte("BMW 320d Limousine Diesel", "3470000003", "25.900", "117.000", "07/2019"))
ma_a, p_a = _punkte(A_RAW)
b_a = p_a[24900]
check("A: Generation aus der Karosserie abgeleitet", b_a.generation == "G20")
check("A: als inferred_database gekennzeichnet",
      b_a.generation_evidence == "inferred_database")
check("A: Begründung gespeichert",
      b_a.generation_inference_reason
      and "eindeutig G20" in b_a.generation_inference_reason
      and "G20/G21" in b_a.generation_inference_reason)
check("A: dadurch tragender Vergleich", b_a.vergleichbarkeit == "sehr_aehnlich")
check("A: alle drei Limousinen tragen den Median",
      sorted(x.preis_eur for x in ma_a.beobachtungen) == [24900, 25400, 25900])

# ══ B — Touring ohne Code -> inferred G21 -> rejected beim G20-Ziel ═════════
B_RAW = (NAV
         + _karte("BMW 320d Touring Diesel", "3470000011", "23.900", "118.000", "05/2019")
         + _karte("BMW 320d Limousine Diesel", "3470000012", "24.900", "119.000", "06/2019"))
ma_b, p_b = _punkte(B_RAW)
check("B: der Touring wird als G21 abgeleitet und verworfen",
      23900 not in [x.preis_eur for x in ma_b.beobachtungen])
check("B: die Ableitung ist als Grund dokumentiert",
      p_b[23900].generation_evidence == "inferred_database"
      and "G21" in (p_b[23900].acceptance_reason or ""))
check("B: die Limousine bleibt erhalten", 24900 in [x.preis_eur for x in ma_b.beobachtungen])

# ══ C — explizites G21 schlägt jede Ableitung ══════════════════════════════
C_RAW = (NAV
         + _karte("BMW 320d G21 Limousine Diesel", "3470000021", "23.500", "118.000", "05/2019")
         + _karte("BMW 320d Limousine Diesel", "3470000022", "24.900", "119.000", "06/2019"))
ma_c, p_c = _punkte(C_RAW)
check("C: explizites G21 wird verworfen, obwohl die Karosserie G20 nahelegt",
      23500 not in [x.preis_eur for x in ma_c.beobachtungen])
check("C: die Ableitung hat den expliziten Code NICHT überschrieben",
      p_c[23500].generation_evidence != "inferred_database")
check("C: der Grund nennt die andere Generation",
      "andere Generation" in (p_c[23500].acceptance_reason or ""))

# ══ D — keine Karosserie -> unknown ════════════════════════════════════════
D_RAW = (NAV
         + _karte("BMW 320d Diesel Automatik", "3470000031", "24.900", "118.000", "05/2019")
         + _karte("BMW 320d Diesel Automatik", "3470000032", "25.400", "119.000", "06/2019"))
ma_d, p_d = _punkte(D_RAW, titel="BMW 320d gebraucht")
check("D: ohne Karosserie wird nichts abgeleitet",
      all(x.generation is None for x in p_d.values()))
check("D: Evidence bleibt unknown",
      all(x.generation_evidence == "unknown" for x in p_d.values()))
check("D: die Karten bleiben höchstens conditional",
      all(x.vergleichbarkeit == "bedingt" for x in p_d.values()))

# ══ E — unbekannte/andere Karosserie -> unknown ════════════════════════════
E_RAW = NAV + _karte("BMW 320d Coupe Diesel", "3470000041", "24.900", "118.000", "05/2019")
_, p_e = _punkte(E_RAW, titel="BMW 320d gebraucht")
check("E: eine Karosserie außerhalb der Zuordnung leitet nichts ab",
      p_e[24900].generation is None and p_e[24900].generation_evidence == "unknown")

# ══ F — Familie mit drei Codes ═════════════════════════════════════════════
F32 = {code.lower(): _karosserie_im_text(karo)
       for code, karo in VERIFIZIERTE_CHASSIS_CODES["bmw-4er-f32-f33-f36"].items()}
check("F: Cabrio ist in der 4er-Familie eindeutig F33",
      _inferiere_generation(F32, "cabrio")[0] == "f33")
check("F: Coupé und Gran Coupé kollabieren auf dieselbe Karosserie -> keine Ableitung",
      _inferiere_generation(F32, "coupe")[0] is None)
check("F: die Rohzuordnung selbst unterscheidet die drei Karosserien",
      VERIFIZIERTE_CHASSIS_CODES["bmw-4er-f32-f33-f36"]
      == {"F32": "Coupé", "F33": "Cabrio", "F36": "Gran Coupé"})

# ══ G — nicht gemappte Familie ═════════════════════════════════════════════
check("G: F01/F02 hat keine Zuordnung", "bmw-7er-f01/f02" not in VERIFIZIERTE_CHASSIS_CODES)
check("G: G11/G12 hat keine Zuordnung", "bmw-7er-g11/g12" not in VERIFIZIERTE_CHASSIS_CODES)
check("G: E65/E66 hat keine Zuordnung (Radstand, nicht Karosserie)",
      "bmw-7er-e65/e66" not in VERIFIZIERTE_CHASSIS_CODES)
check("G: ohne Zuordnung wird nie abgeleitet",
      _inferiere_generation({}, "limousine") == (None, None))
F01_FAMILIE = {"marke": "BMW", "modell": "7er", "generation": "F01/F02",
               "id": "bmw-7er-f01/f02", "karosserie": ["Limousine"], "chassis_codes": {}}
ziel_f01 = baue_ziel(F01_FAMILIE, {"bezeichnung": "730d"},
                     SimpleNamespace(marke="BMW", modell="7er F01", baujahr=2012,
                                     kilometerstand=150_000),
                     [F01_FAMILIE], [])
check("G: Zielprofil einer ungemappten Familie hat leere Zuordnung",
      ziel_f01["chassis_codes"] == {})

# ══ H — der fachlich falsche 8er-Datensatz ═════════════════════════════════
check("H: bmw-8er-e63-e64 hat KEIN Mapping",
      "bmw-8er-e63-e64" not in VERIFIZIERTE_CHASSIS_CODES)
check("H: der korrekte 6er-Datensatz E63/E64 hat eines",
      VERIFIZIERTE_CHASSIS_CODES["bmw-6er-e63-e64"] == {"E63": "Coupé", "E64": "Cabrio"})
check("H: insgesamt genau 7 verifizierte Zuordnungen",
      len(VERIFIZIERTE_CHASSIS_CODES) == 7)

# ══ I — Karosserie-Synonyme über die ZENTRALE Normalisierung ═══════════════
check("I: Touring und Kombi sind dasselbe",
      _karosserie_im_text("Touring") == _karosserie_im_text("Kombi") == "kombi")
check("I: Cabrio und Cabriolet sind dasselbe",
      _karosserie_im_text("Cabrio") == _karosserie_im_text("Cabriolet") == "cabrio")
check("I: eine Karte mit 'Kombi' wird wie 'Touring' behandelt",
      _inferiere_generation(ZIEL["chassis_codes"], "kombi")[0] == "g21")
check("I: eine Karte mit 'Limousine' ergibt G20",
      _inferiere_generation(ZIEL["chassis_codes"], "limousine")[0] == "g20")

# ══ Migration: Schema + Seed idempotent, ohne Datenverlust ════════════════
from app.database import _migrate_schema, _parse_json_dict                # noqa: E402

tmp = os.path.join(tempfile.mkdtemp(prefix="vira_mig_"), "m.db")
conn = sqlite3.connect(tmp)
conn.execute("""CREATE TABLE baureihe (id TEXT PRIMARY KEY, marke TEXT, modell TEXT,
                generation TEXT, karosserie TEXT)""")
conn.execute("CREATE TABLE schema_migrations (name TEXT PRIMARY KEY)")
conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, abo_typ TEXT)")
conn.execute("INSERT INTO baureihe VALUES ('bmw-3er-g20-g21','BMW','3er','G20/G21',NULL)")
conn.execute("INSERT INTO baureihe VALUES ('bmw-8er-e63-e64','BMW','8er','E63/E64',NULL)")
conn.commit()
try:
    _migrate_schema(conn)
    _migrate_schema(conn)   # zweimal -> idempotent
    spalten = {r[1] for r in conn.execute("PRAGMA table_info(baureihe)")}
    check("M: Spalte chassis_codes angelegt", "chassis_codes" in spalten)
    g20 = conn.execute("SELECT chassis_codes FROM baureihe WHERE id='bmw-3er-g20-g21'").fetchone()[0]
    check("M: G20/G21 wurde befüllt",
          json.loads(g20) == {"G20": "Limousine", "G21": "Touring"})
    e63 = conn.execute("SELECT chassis_codes FROM baureihe WHERE id='bmw-8er-e63-e64'").fetchone()[0]
    check("M: der falsche 8er-Datensatz bleibt leer", e63 is None)
    check("M: Marker genau einmal gesetzt",
          conn.execute("SELECT COUNT(*) FROM schema_migrations "
                       "WHERE name='chassis_codes_seed_v1'").fetchone()[0] == 1)
    check("M: keine Zeile verloren",
          conn.execute("SELECT COUNT(*) FROM baureihe").fetchone()[0] == 2)
    check("M: generation-String unverändert",
          conn.execute("SELECT generation FROM baureihe "
                       "WHERE id='bmw-3er-g20-g21'").fetchone()[0] == "G20/G21")
    # Manuelle Pflege darf nicht überschrieben werden.
    conn.execute("DELETE FROM schema_migrations WHERE name='chassis_codes_seed_v1'")
    conn.execute("UPDATE baureihe SET chassis_codes='{\"G20\":\"Handpflege\"}' "
                 "WHERE id='bmw-3er-g20-g21'")
    conn.commit()
    _migrate_schema(conn)
    check("M: bereits gepflegte Zuordnung wird nicht überschrieben",
          json.loads(conn.execute("SELECT chassis_codes FROM baureihe "
                                  "WHERE id='bmw-3er-g20-g21'").fetchone()[0])
          == {"G20": "Handpflege"})
finally:
    conn.close()

check("P: defektes JSON ergibt {} statt Teiltreffer", _parse_json_dict("{kaputt") == {})
check("P: NULL ergibt {}", _parse_json_dict(None) == {})
check("P: ein JSON-Array ergibt {} (kein Objekt)", _parse_json_dict('["G20"]') == {})

print()
if _fails:
    print(f"{len(_fails)} Test(s) fehlgeschlagen.")
    sys.exit(1)
print("Alle Chassiscode-/Inference-Tests bestanden.")
