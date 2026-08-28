"""Safety- und Reproduzierbarkeitstests fuer den Mixed-Target-KBA-Import.

Nur lokale Daten, kein Netzwerk, kein Provider-Livetest.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import app.data_migrations as dm
from app.empfehlungs_floor import ermittle_floor
from app.fakt_verifikation import fingerprint
from app.fahrzeug_seed import _statements
from app.kba_batch_a_daten import zeilen_ids as batch_a_ids
from app.kba_batch_b1_daten import zeilen_ids as batch_b1_ids
from app.kba_import_batch_a import KBA_QUELLE, KBA_URL
from app.kba_mixed_target_daten import (
    AUSGESCHLOSSENE_ZIELPAARE,
    GEPRUEFT_AM,
    SAFE_ZIELPAARE,
    ZEILEN,
    zeilen_ids,
)
from app.models import Insight
from app.recall_filter import rueckruf_applicability


ROOT = Path(__file__).resolve().parent
SCHEMA = ROOT / "db" / "schema.sql"
SEED = ROOT / "db" / "seed_fahrzeugdaten.sql"

ERWARTETE_SAFE_PAARE = (
    ("13433", "bmw-5er-g30"),
    ("10579", "bmw-7er-g11/g12"),
    ("9051", "bmw-7er-g11/g12"),
    ("10579", "bmw-m4-f82"),
    ("10009", "bmw-x3-g01"),
    ("9839", "bmw-x3-g01"),
    ("13459", "hyundai-tucson-dritte-generation"),
    ("13136", "kia-sportage-ql"),
    ("10174", "mercedes-benz-c-klasse-w205"),
    ("11352", "mercedes-benz-c-klasse-w205"),
    ("10174", "mercedes-benz-e-klasse-w213"),
    ("11352", "mercedes-benz-e-klasse-w213"),
    ("13578", "mercedes-benz-e-klasse-w213"),
    ("10174", "mercedes-benz-glc-x253"),
    ("11352", "mercedes-benz-glc-x253"),
    ("10174", "mercedes-benz-s-klasse-w222"),
    ("10383", "opel-astra-k"),
    ("10383", "opel-insignia-b"),
    ("8961", "skoda-fabia-dritte-generation"),
    ("8961", "skoda-kodiaq-erste-generation"),
    ("8961", "skoda-octavia-dritte-generation"),
    ("8961", "skoda-superb-dritte-generation"),
    ("14643R", "toyota-c-hr-i"),
    ("8743", "toyota-rav4-iv"),
    ("8743", "toyota-yaris-iii"),
    ("7473", "volkswagen-golf-vii"),
    ("7700", "volkswagen-golf-vii"),
    ("10749", "volkswagen-passat-b8"),
    ("11162", "volkswagen-passat-b8"),
    ("7473", "volkswagen-passat-b8"),
    ("7700", "volkswagen-passat-b8"),
    ("9783", "volkswagen-passat-b8"),
)

ERWARTETE_AUSSCHLUESSE = (
    ("13262", "bmw-5er-g30"),
    ("13262", "bmw-x3-g01"),
    ("8819", "bmw-x3-g01"),
    ("11352", "mercedes-benz-s-klasse-w222"),
    ("10540", "volkswagen-passat-b8"),
    ("11696", "volkswagen-passat-b8"),
    ("13456", "volkswagen-passat-b8"),
)


def _seed_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    spalten = {r[1] for r in conn.execute("pragma table_info(baureihe)")}
    if "verification" not in spalten:
        conn.execute("alter table baureihe add column verification text")
    conn.execute(
        "create table schema_migrations ("
        "name text primary key, applied_at datetime default current_timestamp)"
    )
    for stmt in _statements(SEED.read_text(encoding="utf-8")):
        conn.execute(stmt)
    conn.commit()
    return conn


def _migration_result():
    conn = _seed_conn()
    ids = tuple(sorted(zeilen_ids()))
    platzhalter = ",".join("?" for _ in ids)

    # Der kanonische Seed enthaelt den Zielzustand. Fuer den Migrationsbeweis
    # wird nur die eigene 32er-Charge entfernt; Batch A/B1 bleiben als echter
    # Dublettenbestand erhalten.
    seed_paare = set(conn.execute(
        f"select kba_referenz, baureihe_id from rueckruf where id in ({platzhalter})",
        ids,
    ))
    conn.execute(
        f"delete from fakt_verifikation where fakt_art='rueckruf' "
        f"and fakt_id in ({platzhalter})", ids,
    )
    conn.execute(f"delete from rueckruf where id in ({platzhalter})", ids)
    conn.execute("delete from schema_migrations where name=?", (dm.MARKER_MIXED_TARGET,))
    conn.commit()

    vorher = conn.execute("select count(*) from rueckruf").fetchone()[0]
    erster_lauf = dm.fuehre_migration_aus(
        conn, dm.MARKER_MIXED_TARGET, dm.SCHRITTE_MIXED_TARGET)
    nachher = conn.execute("select count(*) from rueckruf").fetchone()[0]
    zweiter_lauf = dm.fuehre_migration_aus(
        conn, dm.MARKER_MIXED_TARGET, dm.SCHRITTE_MIXED_TARGET)
    nach_zweitem = conn.execute("select count(*) from rueckruf").fetchone()[0]
    return conn, seed_paare, vorher, nachher, nach_zweitem, erster_lauf, zweiter_lauf


MIGRATION_RESULT = _migration_result()


def test_a1_exakt_32_freigegebene_zielpaare():
    conn = MIGRATION_RESULT[0]
    assert SAFE_ZIELPAARE == ERWARTETE_SAFE_PAARE
    assert len(ZEILEN) == len(zeilen_ids()) == 32
    ist = set(conn.execute(
        "select kba_referenz, baureihe_id from rueckruf where id between 4001 and 4032"
    ))
    assert ist == set(ERWARTETE_SAFE_PAARE)


def test_a2_alle_sieben_ausschluesse_bleiben_draussen():
    conn = MIGRATION_RESULT[0]
    assert AUSGESCHLOSSENE_ZIELPAARE == ERWARTETE_AUSSCHLUESSE
    vorhanden = set(conn.execute(
        "select kba_referenz, baureihe_id from rueckruf where kba_referenz is not null"
    ))
    assert not (vorhanden & set(ERWARTETE_AUSSCHLUESSE))


def test_a3_migration_ist_idempotent():
    _conn, _seed, vorher, nachher, nach_zweitem, erster, zweiter = MIGRATION_RESULT
    assert erster is True
    assert nachher - vorher == 32
    assert zweiter is False
    assert nach_zweitem == nachher


def test_a4_keine_dubletten_zu_batch_a_oder_b1():
    conn = MIGRATION_RESULT[0]
    assert not (zeilen_ids() & batch_a_ids())
    assert not (zeilen_ids() & batch_b1_ids())
    for referenz, baureihe_id in SAFE_ZIELPAARE:
        assert conn.execute(
            "select count(*) from rueckruf where kba_referenz=? and baureihe_id=?",
            (referenz, baureihe_id),
        ).fetchone()[0] == 1


def test_a5_fact_level_verifikation_und_fingerprints():
    conn = MIGRATION_RESULT[0]
    conn.row_factory = sqlite3.Row
    for z in ZEILEN:
        recall = dict(conn.execute("select * from rueckruf where id=?", (z["id"],)).fetchone())
        verifikation = conn.execute(
            "select * from fakt_verifikation where fakt_art='rueckruf' and fakt_id=?",
            (z["id"],),
        ).fetchone()
        assert verifikation is not None
        assert verifikation["status"] == "verified"
        assert verifikation["quelle"] == KBA_QUELLE
        assert verifikation["quelle_stufe"] == "A"
        assert verifikation["url"] == KBA_URL
        assert verifikation["geprueft_am"] == GEPRUEFT_AM
        assert verifikation["fingerprint"] == fingerprint("rueckruf", recall)
        assert "by-2-0" in verifikation["notiz"]


def test_a6_alle_32_realfalle_bleiben_series_only():
    conn = MIGRATION_RESULT[0]
    conn.row_factory = sqlite3.Row
    referenz_index = [dict(r) for r in conn.execute(
        "select r.kba_referenz, b.marke from rueckruf r "
        "join baureihe b on b.id=r.baureihe_id where r.kba_referenz is not null"
    )]
    verteilung = {}
    with patch("app.recall_filter.get_rueckruf_referenzen_kurz",
               return_value=referenz_index):
        for z in ZEILEN:
            recall = dict(conn.execute("select * from rueckruf where id=?", (z["id"],)).fetchone())
            recall["_trust"] = "verified"
            marke = conn.execute(
                "select marke from baureihe where id=?", (z["baureihe_id"],)
            ).fetchone()[0]
            motor = conn.execute(
                "select kraftstoff from motorvariante where baureihe_id=? limit 1",
                (z["baureihe_id"],),
            ).fetchone()
            motor_match = {"kraftstoff": motor[0] if motor else "Benzin"}
            applicability = rueckruf_applicability(
                recall, True, recall["kba_referenz"], motor_match, marke=marke)[0]
            verteilung[applicability] = verteilung.get(applicability, 0) + 1
    assert verteilung == {"series_only": 32}


def test_a7_keine_neue_zeile_traegt_einen_recall_floor():
    insights = [
        Insight(
            id=f"db-rueckruf-{z['id']}",
            kategorie="rueckruf",
            titel=f"KBA {z['kba_referenz']}",
            beschreibung=z["mangel"],
            confidence="hoch",
            applicability="series_only",
            trust="verified",
        )
        for z in ZEILEN
    ]
    assert ermittle_floor(insights) is None


def test_a8_seed_bootstrap_und_migrationsreihenfolge_reproduzierbar():
    _conn, seed_paare, *_rest = MIGRATION_RESULT
    assert seed_paare == set(SAFE_ZIELPAARE)
    marker = [m for m, _s in dm.MIGRATIONEN]
    assert marker.count(dm.MARKER_MIXED_TARGET) == 1
    assert marker.index(dm.MARKER_MIXED_TARGET) > marker.index(dm.MARKER_BATCH_B1)


if __name__ == "__main__":
    tests = [
        test_a1_exakt_32_freigegebene_zielpaare,
        test_a2_alle_sieben_ausschluesse_bleiben_draussen,
        test_a3_migration_ist_idempotent,
        test_a4_keine_dubletten_zu_batch_a_oder_b1,
        test_a5_fact_level_verifikation_und_fingerprints,
        test_a6_alle_32_realfalle_bleiben_series_only,
        test_a7_keine_neue_zeile_traegt_einen_recall_floor,
        test_a8_seed_bootstrap_und_migrationsreihenfolge_reproduzierbar,
    ]
    fehler = []
    for test in tests:
        try:
            test()
            print(f"[OK  ] {test.__name__}")
        except Exception as exc:
            fehler.append((test.__name__, exc))
            print(f"[FAIL] {test.__name__}: {exc}")
    MIGRATION_RESULT[0].close()
    if fehler:
        raise SystemExit(1)
    print("Alle Mixed-Target-KBA-Tests bestanden.")
