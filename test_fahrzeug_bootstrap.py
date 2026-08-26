# -*- coding: utf-8 -*-
"""
FAHRZEUGDATEN-BOOTSTRAP — Regression.
KEIN Netzwerk, KEIN LLM-Call, KEIN Tavily.

  A) leere DB: kompletter Bootstrap aus dem Repo
  B) bestehende DB: nichts wird veraendert
  C) dreimal hintereinander: keine Mutation
  D) der Seed enthaelt keinerlei Nutzerdaten
  E) Fremdschluessel-Integritaet
  F) die P0-Korrekturen sind im frischen Bestand enthalten
  G) Runtime-Trust-Semantik greift auch auf frischer DB
  H) KaufCheck loest BMW G20 aus frischer DB auf
  I) KaufCheck loest Audi A3 8P aus frischer DB auf
  J) DB-Miss bleibt DB-Miss

    python test_fahrzeug_bootstrap.py
"""
import hashlib
import importlib
import os
import re
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, ".")

FEHLER: list[str] = []


def check(name, ok, info=""):
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}" + (f"   {info}" if info else ""))
    if not ok:
        FEHLER.append(name)


BASE = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.join(BASE, "db", "seed_fahrzeugdaten.sql")
SCHEMA = os.path.join(BASE, "db", "schema.sql")

FAHRZEUG = ("baureihe", "motorvariante", "schwachstelle_baureihe", "schwachstelle_motor",
            "kritische_wartung", "rueckruf", "ausstattungslinie", "quelle",
            "fakt_verifikation")
NUTZER = ("users", "checks", "check_frage", "conversations", "messages", "einwilligung",
          "gespeicherte_adresse", "dealer_vehicle", "ebook_bestellung", "poster_bestellung")

if not os.path.exists(SEED):
    print(f"[SKIP] Seed fehlt ({SEED}) — Suite uebersprungen")
    raise SystemExit(0)


# ══════════════════════════════════════════════════════════════════════════════
print("=== D) Der Seed enthaelt keine Nutzerdaten ===")
_seed_text = open(SEED, encoding="utf-8").read()
_ziele = sorted(set(re.findall(r"^INSERT INTO (\w+)", _seed_text, re.M)))
check("D1 nur Fahrzeugtabellen als INSERT-Ziel", set(_ziele) <= set(FAHRZEUG), str(_ziele))
check("D2 keine Nutzertabelle im Seed", not (set(_ziele) & set(NUTZER)))
# Der Seed ist Freitext ueber Autos; die folgenden Muster duerfen darin schlicht
# nicht vorkommen. Sie stehen fuer die vier Datenarten, die hier am meisten
# schaden koennten: Identitaet, Zugangsdaten, Zahlungsbezug, Sitzungen.
for muster, name in ((r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "E-Mail-Adressen"),
                     (r"\$2[aby]\$\d\d\$", "bcrypt-Hashes"),
                     (r"\b(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{6,}", "Stripe-Keys"),
                     (r"\bcus_[A-Za-z0-9]{8,}", "Stripe-Kundennummern"),
                     (r"\bsub_[A-Za-z0-9]{8,}", "Stripe-Abonummern"),
                     (r"\beyJ[A-Za-z0-9_-]{10,}", "JWT")):
    treffer = re.findall(muster, _seed_text)
    check(f"D3 keine {name} im Seed", not treffer, f"n={len(treffer)}")
check("D4 die Allowlist des Exporters stammt aus db/schema.sql",
      set(re.findall(r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+(\w+)",
                     open(SCHEMA, encoding="utf-8").read(), re.I)) == set(FAHRZEUG))


# ══════════════════════════════════════════════════════════════════════════════
def _counts(pfad, tabellen):
    c = sqlite3.connect(pfad)
    try:
        vorhanden = {r[0] for r in c.execute(
            "select name from sqlite_master where type='table'")}
        return {t: c.execute(f'select count(*) from "{t}"').fetchone()[0]
                for t in tabellen if t in vorhanden}
    finally:
        c.close()


def _snapshot(pfad):
    """Inhalts-Hash je Tabelle — erkennt auch Aenderungen ohne Zeilenzahl-Aenderung."""
    c = sqlite3.connect(pfad)
    try:
        out = {}
        for (t,) in c.execute("select name from sqlite_master where type='table' "
                              "and name not like 'sqlite_%' order by name"):
            rows = c.execute(f'select * from "{t}"').fetchall()
            out[t] = (len(rows),
                      hashlib.sha256(repr(sorted(map(repr, rows))).encode()).hexdigest()[:16])
        return out
    finally:
        c.close()


def _bootstrap(pfad, laeufe=1):
    """Startet den normalen App-Bootstrap gegen `pfad`."""
    alt_db = os.environ.get("AUTO_KI_DB_PATH")
    alt_chroma = os.environ.get("AUTO_KI_CHROMA_PATH")
    os.environ["AUTO_KI_DB_PATH"] = pfad
    os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(os.path.dirname(pfad), "chroma")
    try:
        import app.config as _cfg
        importlib.reload(_cfg)
        import app.database as _db
        importlib.reload(_db)
        for _ in range(laeufe):
            _db.ensure_tables()
    finally:
        for k, v in (("AUTO_KI_DB_PATH", alt_db), ("AUTO_KI_CHROMA_PATH", alt_chroma)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== A/C/E) Leere DB: Bootstrap, dreimal ===")
_tmp = tempfile.mkdtemp(prefix="vira_bs_")
_frisch = os.path.join(_tmp, "auto_ki.db")
_bootstrap(_frisch, laeufe=1)
_nach1 = _counts(_frisch, FAHRZEUG)
_bootstrap(_frisch, laeufe=1)
_nach2 = _counts(_frisch, FAHRZEUG)
_bootstrap(_frisch, laeufe=1)
_nach3 = _counts(_frisch, FAHRZEUG)

check("A1 Fahrzeugtabellen wurden automatisch angelegt", len(_nach1) == len(FAHRZEUG))
check("A2 Fahrzeugbestand wurde automatisch geladen", _nach1.get("baureihe", 0) > 400,
      f"baureihe={_nach1.get('baureihe')}")
check("A3 Bestand entspricht dem Seed-Kopf",
      all(_nach1[t] == int(re.search(rf"-- {t}: (\d+) Zeilen", _seed_text).group(1))
          for t in FAHRZEUG), str(_nach1))
check("C1 zweiter Bootstrap-Lauf aendert nichts", _nach1 == _nach2)
check("C2 dritter Bootstrap-Lauf aendert nichts", _nach2 == _nach3)

_c = sqlite3.connect(_frisch)
_marker = {r[0] for r in _c.execute("select name from schema_migrations")}
check("A4 Seed-Marker gesetzt", "fahrzeug_seed_v1" in _marker)
check("A5 P0-Datenmigration ausgefuehrt", "p0_fahrzeugdaten_korrekturen_v1" in _marker)
check("E1 integrity_check ok", _c.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
_fk = _c.execute("PRAGMA foreign_key_check").fetchall()
check("E2 keine Fremdschluesselverletzung", not _fk, str(sorted({r[0] for r in _fk})))
for _name, _sql in (
        ("motorvariante ohne baureihe", "select count(*) from motorvariante m where not exists"
                                        "(select 1 from baureihe b where b.id=m.baureihe_id)"),
        ("schwachstelle_motor ohne motorvariante",
         "select count(*) from schwachstelle_motor s where not exists"
         "(select 1 from motorvariante m where m.variante_id=s.variante_id)")):
    check(f"E3 {_name}: 0", _c.execute(_sql).fetchone()[0] == 0)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== F) P0-Korrekturen im frisch gebootstrappten Bestand ===")
_q = lambda s: _c.execute(s).fetchone()[0]
for _name, _ok in (
    ("F1 Phantom BMW 8er E63/E64 nicht vorhanden",
     _q("select count(*) from baureihe where id='bmw-8er-e63-e64'") == 0),
    ("F2 BMW G20 nur kanonisch",
     _q("select count(*) from baureihe where id='bmw-3er-g20'") == 0
     and _q("select count(*) from baureihe where id='bmw-3er-g20-g21'") == 1),
    ("F3 BMW F20/F21 nur kanonisch",
     _q("select count(*) from baureihe where id='bmw-1er-f2x'") == 0
     and _q("select count(*) from baureihe where id='bmw-1er-f20-f21'") == 1),
    ("F4 Golf VIII nur kanonisch",
     _q("select count(*) from baureihe where id='vw-golf-8'") == 0
     and _q("select count(*) from baureihe where id='volkswagen-golf-viii'") == 1),
    ("F5 AMG-GT-Dublette entfernt",
     _q("select count(*) from baureihe where id='mercedes-amg-gt-r192'") == 0),
    ("F6 W205 C200 PHEV entfernt",
     _q("select count(*) from motorvariante "
        "where variante_id='mercedes-benz-c-klasse-w205-c200-plug-in-hybrid'") == 0),
    ("F7 RAV4 II als 2.0 D-4D / 1CD-FTV",
     _q("select count(*) from motorvariante where variante_id='toyota-rav4-ii-2-0-vvt-i' "
        "and bezeichnung='2.0 D-4D' and motorcode='1CD-FTV'") == 1),
    ("F8 Insignia F20DVH mit 1995 ccm",
     _q("select count(*) from motorvariante where baureihe_id='opel-insignia-b' "
        "and leistung_ps=174 and motorcode='F20DVH' and hubraum_ccm=1995") == 2),
    ("F9 E23 '749' weg, 735i vorhanden",
     _q("select count(*) from motorvariante where bezeichnung='749'") == 0
     and _q("select count(*) from motorvariante where variante_id='bmw-7er-e23-735i'") == 1),
    ("F10 E23 745i ist Sechszylinder, kein 745iA",
     _q("select zylinder from motorvariante where variante_id='bmw-7er-e23-745i'") == 6
     and _q("select count(*) from motorvariante where bezeichnung='745iA'") == 0),
    ("F11 W205 C300e mit Systemleistung 320 PS",
     _q("select leistung_ps from motorvariante "
        "where variante_id='mercedes-benz-c-klasse-w205-c300-plug-in-hybrid'") == 320),
    ("F12 kein Zahnriemen an bekannten Kettenmotoren",
     _q("select count(*) from kritische_wartung w join motorvariante m "
        "on m.variante_id=w.variante_id where w.bauteil like '%Zahnriemen%' and m.motorcode in "
        "('M10B16','M10B18','S14B23','M30B30','M30B35','CDAA','CCZA','2AZ-FE')") == 0),
):
    check(_name, _ok)
_c.close()


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== G/H/I/J) KaufCheck auf frisch gebootstrappter DB ===")
_alt_db = os.environ.get("AUTO_KI_DB_PATH")
os.environ["AUTO_KI_DB_PATH"] = _frisch
try:
    import app.config as _cfg2
    importlib.reload(_cfg2)
    import app.database as _db2
    importlib.reload(_db2)
    import app.car_lookup as _cl
    importlib.reload(_cl)
    from app.models import KaufCheckRequest
    from app.evidence import build_insights
    from app.kaufaktionen import build_kaufaktionen
    from app.empfehlungs_floor import ermittle_floor

    BEREICHE = ("besichtigung", "probefahrt", "verkaeuferfragen", "dokumente")

    def _lauf(marke, modell, baujahr, motor):
        br, info = _cl.find_baureihe_mit_vertrauen(marke, modell, baujahr)
        mm = _cl.find_motor(br, motor) if br else None
        req = KaufCheckRequest(marke=marke, modell=modell, baujahr=baujahr, motor=motor)
        ins = build_insights(br, mm, [], req, check_typ="kauf") if br else []
        ka = build_kaufaktionen(req, br, mm, ins)
        aktionen = [a for b in BEREICHE for a in getattr(ka, b).fahrzeugspezifisch]
        basis = sum(len(getattr(ka, b).basis) for b in BEREICHE)
        return br, info, mm, ins, aktionen, basis

    _br, _i, _mm, _ins, _akt, _basis = _lauf("BMW", "3er", 2020, "320d")
    check("H1 BMW G20 wird aufgeloest",
          (_br or {}).get("id") == "bmw-3er-g20-g21" and _i["belastbar"],
          f"{_br and _br['id']} {_i['match_art']}/{_i['konfidenz']}")
    check("H2 Motor 320d erkannt", (_mm or {}).get("bezeichnung") == "320d")
    check("H3 fahrzeugspezifische Pruefpunkte entstehen", len(_akt) > 0, f"n={len(_akt)}")
    # VERIFICATION-PILOT: seit dem Pilot ist nicht mehr JEDER DB-Fakt
    # unverified_db — einzelne sind kuratiert geprueft und wandern ueber den Seed
    # mit in eine frische Datenbank. Geprueft wird deshalb die schaerfere Aussage:
    # verified gibt es NUR mit persistierter Verifikation, und das Attribut
    # "(geprueft)" steht ausschliesslich an genau diesen Quellen.
    _db_ins = [x for x in _ins
               if x.kategorie in ("schwachstelle", "rueckruf", "motorproblem", "wartung")]
    check("G1 jeder DB-Insight traegt entweder verified oder unverified_db",
          all(x.trust in ("verified", "unverified_db") for x in _db_ins))
    _c_v = sqlite3.connect(_frisch)
    _n_verifiziert = _c_v.execute(
        "select count(*) from fakt_verifikation where status='verified'").fetchone()[0]
    _c_v.close()
    check("G1b verifizierte Insights gibt es nur, wenn Verifikationen persistiert sind",
          (sum(1 for x in _db_ins if x.trust == "verified") == 0) or _n_verifiziert > 0,
          f"persistierte verified-Eintraege={_n_verifiziert}")
    check("G2 kein Empfehlungs-Floor aus unverifizierten Daten", ermittle_floor(_ins) is None)
    check("G3 '(geprueft)' steht ausschliesslich an verifizierten Quellen",
          all(x.trust == "verified"
              for x in _db_ins
              if any("geprüft" in (qq.titel or "").lower() for qq in x.quellen)))

    _br, _i, _mm, _ins, _akt, _basis = _lauf("Audi", "A3", 2008, "2.0 FSI 150 PS")
    check("I1 Audi A3 8P wird aufgeloest",
          (_br or {}).get("id") == "audi-a3-typ-8p" and _i["belastbar"],
          f"{_br and _br['id']} {_i['match_art']}/{_i['konfidenz']}")
    check("I2 Motor 2.0 FSI erkannt", "2.0 FSI" in ((_mm or {}).get("bezeichnung") or ""))
    check("I3 Motor-Applicability-Gate greift auch hier (kein TFSI-Turbolader)",
          not any("TFSI" in x.titel for x in _ins if x.kategorie == "schwachstelle"))

    _br, _i, _mm, _ins, _akt, _basis = _lauf("Fantasia", "Nebulon", 2020, "3.0 X")
    check("J1 DB-Miss bleibt DB-Miss", _br is None, f"-> {_br and _br['id']}")
    check("J2 keine Evidence aus der Fahrzeugdatenbank", not _ins)
    # Aktionen entstehen hier trotzdem — aber ausschliesslich aus dem, was der
    # Nutzer im INSERAT angegeben hat (fehlendes HU-Datum, unklare Unfallfreiheit).
    # Sie behaupten nichts ueber das Fahrzeug und tragen korrekterweise keine
    # Evidence-ID. Genau das muss bei einem DB-Miss uebrig bleiben.
    check("J2b verbleibende Punkte stammen nur aus dem Inserat",
          all(a.kategorie == "inserat" and not a.evidence_ids for a in _akt),
          str([(a.kategorie, a.evidence_ids) for a in _akt]))
    check("J3 die allgemeine Basischeckliste bleibt erhalten", _basis > 0, f"n={_basis}")
finally:
    if _alt_db is None:
        os.environ.pop("AUTO_KI_DB_PATH", None)
    else:
        os.environ["AUTO_KI_DB_PATH"] = _alt_db
    importlib.reload(_cfg2)
    importlib.reload(_db2)
    importlib.reload(_cl)
    shutil.rmtree(_tmp, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== B) Bestehende DB bleibt unangetastet ===")
_LIVE = os.environ.get("AUTO_KI_DB_PATH") or os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "auto-ki-backend", "auto_ki.db")
if not os.path.exists(_LIVE):
    print("[SKIP] keine bestehende Datenbank vorhanden — Abschnitt B uebersprungen")
else:
    _tmp2 = tempfile.mkdtemp(prefix="vira_exist_")
    _kopie = os.path.join(_tmp2, "auto_ki.db")
    shutil.copy(_LIVE, _kopie)
    _vor = _snapshot(_kopie)
    _bootstrap(_kopie, laeufe=3)
    _nach = _snapshot(_kopie)
    _diff = {t for t in set(_vor) | set(_nach) if _vor.get(t) != _nach.get(t)}
    check("B1 Fahrzeugtabellen inhaltlich unveraendert",
          all(_vor[t] == _nach[t] for t in FAHRZEUG if t in _vor))
    check("B2 Nutzertabellen inhaltlich unveraendert",
          all(_vor[t] == _nach[t] for t in NUTZER if t in _vor))
    check("B3 nur die Migrationsbuchhaltung darf sich aendern",
          _diff <= {"schema_migrations"}, str(sorted(_diff)))
    check("B4 der Seed wurde NICHT erneut importiert",
          _vor.get("baureihe") == _nach.get("baureihe"),
          f"{_vor.get('baureihe')} -> {_nach.get('baureihe')}")
    shutil.rmtree(_tmp2, ignore_errors=True)


print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER: " + ", ".join(FEHLER))
    raise SystemExit(1)
print("Alle Bootstrap-Regressionen bestanden.")