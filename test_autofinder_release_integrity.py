"""
Test: AutoFinder Release-Integrity — Daten-Dublette (Audi) + Exact-Image-Regel

Deckt die Testmatrix A-M der Release-Haertung:

  Audi-Datenkorrektur (app/data_migrations.py + db/seed_fahrzeugdaten.sql)
    A) audi-a4-b9 enthaelt die RS4 2.9 TFSI nach der Korrektur NICHT mehr
    B) audi-rs-4-avant-b9 traegt die RS-4-Variante weiterhin
    C) AutoFinder kann A4-RS4-Fake und echten RS4 nicht gleichzeitig ausgeben
    D) alle uebrigen A4-Motoren (inkl. beider S4) unveraendert
    + Quelle (Seed) und Migration sind konsistent, Migration ist idempotent

  Exact-Image-Regel (app/routers/autofinder.py::_ist_exaktes_asset)
    E) Mk4-Kandidat + nur Mk3-Asset -> Mk3 wird NICHT verwendet
    F) fehlendes Exact -> Kandidat geht als generic_fallback in den Ensure-Flow
    G) exaktes Asset -> Kandidat erscheint mit seinem Bild
    J) gleiche Generation, falsche Karosserie -> nicht verwendet
    L) resolved_visual_key == Key genau dieses Kandidaten
    M) image_confidence == exact
  (G/H/I History/Replacement: der bestehende Image-Guarantee-Flow im Frontend,
   hier strukturell geprueft — er bekommt durch die Regel nur noch exakte
   Assets bzw. generic_fallback vorgelegt.)

Kein Netz, kein Gemini. Ausfuehren:  python test_autofinder_release_integrity.py
"""
import sqlite3
import sys

sys.path.insert(0, ".")
FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


import app.data_migrations as dm  # noqa: E402
from app.autofinder_visual import (  # noqa: E402
    CONF_EXACT, CONF_GENERATION_MATCH, CONF_MODEL_MATCH, CONF_REPRESENTATIVE,
    ManifestEintrag, resolve_image, visual_key_v2,
)
from app.routers.autofinder import _bild_felder, _ist_exaktes_asset  # noqa: E402

A4 = "audi-a4-b9"
RS4_FAKE = "audi-a4-b9-rs4-2.9-tfsi"
RS4_ECHT = "audi-rs-4-avant-b9-rs-4-avant-2.9-tfsi"
RS4_BAUREIHE = "audi-rs-4-avant-b9"


# ══════════════════════════════════════════════════════════════════════════
# Quelle: der kanonische Seed darf die Dublette nicht mehr enthalten
# ══════════════════════════════════════════════════════════════════════════
_seed = open("db/seed_fahrzeugdaten.sql", encoding="utf-8").read()
check("Quelle: der kanonische Seed enthaelt die A4-RS4-Dublette nicht mehr",
      f"'{RS4_FAKE}'" not in _seed)
check("Quelle: die echte RS-4-Avant-Variante steht weiterhin im Seed",
      f"'{RS4_ECHT}'" in _seed)
check("Quelle: die Baureihe audi-rs-4-avant-b9 ist unveraendert vorhanden",
      f"'{RS4_BAUREIHE}','Audi','RS 4 Avant'" in _seed)
check("Quelle: beide S4-Varianten der A4 B9 bleiben erhalten",
      "'audi-a4-b9-s4-3.0-tdi'" in _seed and "'audi-a4-b9-s4-3.0-tfsi'" in _seed)


# ══════════════════════════════════════════════════════════════════════════
# Migration auf einer Datenbank, die die Dublette noch traegt
# ══════════════════════════════════════════════════════════════════════════
def _mini_db() -> sqlite3.Connection:
    """Minimale Fahrzeug-DB mit genau dem Ausgangszustand vor der Korrektur."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE schema_migrations (name TEXT PRIMARY KEY)")
    c.execute("CREATE TABLE baureihe (id TEXT PRIMARY KEY, marke TEXT, modell TEXT, "
              "generation TEXT, karosserie TEXT, vorgaenger TEXT)")
    c.execute("CREATE TABLE motorvariante (variante_id TEXT PRIMARY KEY, baureihe_id TEXT, "
              "bezeichnung TEXT, motorcode TEXT, kraftstoff TEXT, hubraum_ccm INT, "
              "leistung_ps INT, drehmoment_nm INT)")
    for t in ("schwachstelle_baureihe", "rueckruf", "ausstattungslinie", "quelle"):
        c.execute(f"CREATE TABLE {t} (id INTEGER PRIMARY KEY, baureihe_id TEXT)")
    # variante_id-gebunden wie im echten Schema (db/schema.sql)
    for t in ("schwachstelle_motor", "kritische_wartung"):
        c.execute(f"CREATE TABLE {t} (id INTEGER PRIMARY KEY, variante_id TEXT, bauteil TEXT)")
    c.execute("INSERT INTO baureihe VALUES (?,?,?,?,?,?)",
              (A4, "Audi", "A4", "B9", '["Limousine", "Avant", "Allroad"]', None))
    c.execute("INSERT INTO baureihe VALUES (?,?,?,?,?,?)",
              (RS4_BAUREIHE, "Audi", "RS 4 Avant", "B9", '["Kombi"]', None))
    motoren = [
        (RS4_FAKE, A4, "RS4 (2.9 TFSI)", "DECA", "Benzin", 2894, 450, 600),
        ("audi-a4-b9-s4-3.0-tdi", A4, "S4 (3.0 TDI)", "DEWA", "Mild-Hybrid", 2967, 347, 700),
        ("audi-a4-b9-s4-3.0-tfsi", A4, "S4 (3.0 TFSI)", "CWGD", "Benzin", 2995, 354, 500),
        ("audi-a4-b9-2.0-tfsi-45-tfsi", A4, "2.0 TFSI (45 TFSI)", "CYMC", "Mild-Hybrid", 1984, 245, 370),
        (RS4_ECHT, RS4_BAUREIHE, "RS 4 Avant 2.9 TFSI", "DECA", "Benzin", 2894, 450, 600),
    ]
    c.executemany("INSERT INTO motorvariante VALUES (?,?,?,?,?,?,?,?)", motoren)
    c.commit()
    return c


_db = _mini_db()
_a4_vorher = {r[0] for r in _db.execute(
    "SELECT bezeichnung FROM motorvariante WHERE baureihe_id=?", (A4,))}
_gesamt_vorher = _db.execute("SELECT COUNT(*) FROM motorvariante").fetchone()[0]
check("Ausgangszustand: A4 B9 traegt die RS4-Dublette", "RS4 (2.9 TFSI)" in _a4_vorher)

_angewendet = dm.fuehre_migration_aus(_db, dm.MARKER_A4_B9_RS4, dm.SCHRITTE_A4_B9_RS4)
check("Migration wird angewendet (Marker gesetzt)", _angewendet is True)

_a4_nachher = {r[0] for r in _db.execute(
    "SELECT bezeichnung FROM motorvariante WHERE baureihe_id=?", (A4,))}
_rs4_nachher = [r[0] for r in _db.execute(
    "SELECT bezeichnung FROM motorvariante WHERE baureihe_id=?", (RS4_BAUREIHE,))]
_gesamt_nachher = _db.execute("SELECT COUNT(*) FROM motorvariante").fetchone()[0]

# A) + B) + D)
check("A: audi-a4-b9 enthaelt die RS4 2.9 TFSI nicht mehr",
      "RS4 (2.9 TFSI)" not in _a4_nachher)
check("B: audi-rs-4-avant-b9 traegt die RS-4-Variante weiterhin",
      _rs4_nachher == ["RS 4 Avant 2.9 TFSI"])
check("D: alle uebrigen A4-Motoren sind unveraendert (inkl. beider S4)",
      _a4_nachher == _a4_vorher - {"RS4 (2.9 TFSI)"}
      and {"S4 (3.0 TDI)", "S4 (3.0 TFSI)"} <= _a4_nachher)
check("D: genau EINE Motorzeile entfernt, keine weitere verloren",
      _gesamt_nachher == _gesamt_vorher - 1)

# C) 450-PS-Benzin-Doppelung ist weg
_450 = [(r["baureihe_id"], r["bezeichnung"]) for r in _db.execute(
    "SELECT baureihe_id, bezeichnung FROM motorvariante WHERE leistung_ps=450 AND kraftstoff='Benzin'")]
check("C: es gibt nur noch EINEN 450-PS-Benziner (kein A4-RS4-Fake neben dem echten RS4)",
      len(_450) == 1 and _450[0][0] == RS4_BAUREIHE)

# Idempotenz + Precondition
_zweiter_lauf = dm.fuehre_migration_aus(_db, dm.MARKER_A4_B9_RS4, dm.SCHRITTE_A4_B9_RS4)
check("Migration ist idempotent (zweiter Lauf aendert nichts)", _zweiter_lauf is False)
_db2 = _mini_db()
_db2.execute("DELETE FROM motorvariante WHERE variante_id=?", (RS4_ECHT,))
_db2.commit()
check("Precondition: ohne kanonische RS-4-Variante wird NICHT geloescht",
      dm.fuehre_migration_aus(_db2, dm.MARKER_A4_B9_RS4, dm.SCHRITTE_A4_B9_RS4) is False
      and _db2.execute("SELECT COUNT(*) FROM motorvariante WHERE variante_id=?",
                       (RS4_FAKE,)).fetchone()[0] == 1)
_db3 = _mini_db()
_db3.execute("UPDATE motorvariante SET leistung_ps=470 WHERE variante_id=?", (RS4_FAKE,))
_db3.commit()
check("Precondition: technisch ABWEICHENDE Zeilen werden nicht als Dublette geloescht",
      dm.fuehre_migration_aus(_db3, dm.MARKER_A4_B9_RS4, dm.SCHRITTE_A4_B9_RS4) is False
      and _db3.execute("SELECT COUNT(*) FROM motorvariante WHERE variante_id=?",
                       (RS4_FAKE,)).fetchone()[0] == 1)
_db4 = _mini_db()
_db4.execute("INSERT INTO schwachstelle_motor (variante_id, bauteil) VALUES (?,?)",
             (RS4_FAKE, "Testbauteil"))
_db4.commit()
check("Precondition: eigene Motor-Schwachstelle verhindert das Loeschen",
      dm.fuehre_migration_aus(_db4, dm.MARKER_A4_B9_RS4, dm.SCHRITTE_A4_B9_RS4) is False)

check("Migration ist in der Registry verdrahtet (laeuft beim App-Start)",
      any(m == dm.MARKER_A4_B9_RS4 for m, _s in dm.MIGRATIONEN))


# ══════════════════════════════════════════════════════════════════════════
# EXACT-IMAGE-REGEL
# ══════════════════════════════════════════════════════════════════════════
class Kand:
    def __init__(self, marke, modell, generation, karosserie, visual_key):
        self.marke, self.modell, self.generation = marke, modell, generation
        self.karosserie_klassen = list(karosserie)
        self.visual_key = visual_key


def eintrag(key, marke, modell, generation, *, image_type="curated"):
    return ManifestEintrag(
        visual_key=key, image_url=f"/cars/autofinder/{key}.webp", image_type=image_type,
        image_confidence=CONF_EXACT, marke=marke, modell=modell, generation=generation,
        karosserie="kombi", ai_generated=True, reviewed=True, active=True)


_focus_mk4 = Kand("Ford", "Focus", "Mk4", ["kombi", "limousine"], "ford--focus--mk4")
_nur_mk3 = {"ford--focus--mk3--kombi": eintrag("ford--focus--mk3--kombi", "Ford", "Focus", "Mk3")}

# E) der reale Befund
_r_mk3 = resolve_image(_focus_mk4, bevorzugte_karosserie="kombi", manifest=_nur_mk3)
check("E: der Resolver liefert fuer Mk4 ueberhaupt nur ein Mk3-Asset (Reproduktion)",
      _r_mk3.resolved_visual_key == "ford--focus--mk3--kombi"
      and _r_mk3.image_confidence == CONF_MODEL_MATCH)
check("E: die Exact-Regel verwirft dieses Mk3-Asset",
      _ist_exaktes_asset(_focus_mk4, _r_mk3, "kombi") is False)

# F) fehlendes Exact -> generic_fallback (Eingang in den Ensure-Flow)
import app.autofinder_visual as av  # noqa: E402
_orig_lade = av.lade_manifest_datei
import app.routers.autofinder as router_mod  # noqa: E402


def _mit_manifest(manifest, kandidat, karo="kombi"):
    av.lade_manifest_datei = lambda: manifest
    router_mod.resolve_image = av.resolve_image
    try:
        return _bild_felder(kandidat, bevorzugte_karosserie=karo)
    finally:
        av.lade_manifest_datei = _orig_lade


_felder_mk3 = _mit_manifest(_nur_mk3, _focus_mk4)
check("F: ohne exaktes Asset meldet der Consumer-Pfad generic_fallback",
      _felder_mk3["image_type"] == "generic_fallback")
check("F: das falsche Mk3-Bild taucht in der Ausgabe NICHT auf",
      "mk3" not in _felder_mk3["image_url"])
check("F: die Ausgabe traegt dann kein exact-Siegel",
      _felder_mk3["image_confidence"] == CONF_REPRESENTATIVE)

# G) + L) + M) exaktes Asset -> wird verwendet
_mit_mk4 = dict(_nur_mk3)
_mit_mk4["ford--focus--mk4--kombi"] = eintrag("ford--focus--mk4--kombi", "Ford", "Focus", "Mk4")
_felder_mk4 = _mit_manifest(_mit_mk4, _focus_mk4)
check("G: mit exaktem Mk4-Asset erscheint der Kandidat mit seinem Bild",
      _felder_mk4["image_type"] == "curated" and "mk4" in _felder_mk4["image_url"])
check("M: die finale Karte traegt image_confidence == exact",
      _felder_mk4["image_confidence"] == CONF_EXACT)
_r_mk4 = resolve_image(_focus_mk4, bevorzugte_karosserie="kombi", manifest=_mit_mk4)
check("L: resolved_visual_key ist der Key genau dieses Kandidaten",
      _r_mk4.resolved_visual_key == visual_key_v2("Ford", "Focus", "Mk4",
                                                   ["kombi", "limousine"],
                                                   bevorzugte_karosserie="kombi"))

# On-Demand-Key (3-teilig) gilt ebenfalls als exakt — sonst liefe der
# Ensure-Flow endlos gegen sich selbst
_ondemand = {"ford--focus--mk4": eintrag("ford--focus--mk4", "Ford", "Focus", "Mk4",
                                          image_type="generated_cached")}
_r_od = resolve_image(_focus_mk4, bevorzugte_karosserie="kombi", manifest=_ondemand)
check("G: ein per Ensure erzeugtes On-Demand-Asset desselben Fahrzeugs gilt als exakt",
      _ist_exaktes_asset(_focus_mk4, _r_od, "kombi") is True)

# J) gleiche Generation, falsche Karosserie
_limo_asset = {"ford--focus--mk4--limousine": eintrag(
    "ford--focus--mk4--limousine", "Ford", "Focus", "Mk4")}
_r_limo = resolve_image(_focus_mk4, bevorzugte_karosserie="kombi", manifest=_limo_asset)
check("J: gleiche Generation, aber falsche Karosserie -> nicht als exakt akzeptiert",
      _r_limo.image_confidence == CONF_GENERATION_MATCH
      and _ist_exaktes_asset(_focus_mk4, _r_limo, "kombi") is False)
check("J: falsche Karosserie erscheint nicht in der Consumer-Ausgabe",
      _mit_manifest(_limo_asset, _focus_mk4)["image_type"] == "generic_fallback")

# leeres Manifest -> ebenfalls sauber neutral
check("F: leeres Manifest -> generic_fallback, kein Fehler",
      _mit_manifest({}, _focus_mk4)["image_type"] == "generic_fallback")

# H/I/K strukturell: der Ensure-/Replacement-/History-Flow bleibt zustaendig
_frontend = "../../../.claude/sessions/auto-ki-web/src/components/autofinder/logic.ts"
try:
    _logic = open(_frontend, encoding="utf-8").read()
except OSError:
    _logic = ""
if _logic:
    check("H/I: das Frontend entfernt Kandidaten ohne echtes Bild und laesst nachruecken",
          "waehleImageReady" in _logic and "hatEchtesBild" in _logic)
    check("K: History-Restore loest die Bilder frisch auf, statt einen Snapshot zu zeigen",
          "aktualisiereGespeicherteBilder" in _logic)
else:
    print("[INFO] Frontend-Logik nicht gefunden — H/I/K nur backendseitig geprueft")

check("Regel-Verdrahtung: _bild_felder nutzt die Exact-Pruefung",
      "_ist_exaktes_asset" in open("app/routers/autofinder.py", encoding="utf-8").read())


print()
if FEHLER:
    print(f"{len(FEHLER)} Test(s) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle AutoFinder-Release-Integrity-Tests bestanden.")
