from __future__ import annotations

import sqlite3
import json
from contextlib import contextmanager
from app.config import DB_PATH


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def _parse_json_field(value: str | None) -> list:
    if value is None:
        return []
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return [value]


def get_baureihe(marke: str, modell: str, generation: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM baureihe WHERE marke=? AND modell=? AND generation=?",
            (marke, modell, generation),
        ).fetchone()

        if row is None:
            return None

        result = dict(row)
        baureihe_id = result["id"]

        result["karosserie"] = _parse_json_field(result.get("karosserie"))

        result["ausstattungslinien"] = [
            dict(r) for r in conn.execute(
                "SELECT name,typ,optische_merkmale,abgrenzung FROM ausstattungslinie WHERE baureihe_id=?",
                (baureihe_id,),
            ).fetchall()
        ]

        result["schwachstellen_baureihe"] = [
            dict(r) for r in conn.execute(
                "SELECT bauteil,beschreibung,betroffene_baujahre,schweregrad "
                "FROM schwachstelle_baureihe WHERE baureihe_id=?",
                (baureihe_id,),
            ).fetchall()
        ]

        result["rueckrufe"] = [
            dict(r) for r in conn.execute(
                "SELECT datum,betroffene_baujahre,mangel,abhilfe,kba_referenz "
                "FROM rueckruf WHERE baureihe_id=?",
                (baureihe_id,),
            ).fetchall()
        ]

        result["quellen"] = [
            dict(r) for r in conn.execute(
                "SELECT quelle,url,abrufdatum FROM quelle WHERE baureihe_id=?",
                (baureihe_id,),
            ).fetchall()
        ]

        motoren_rows = conn.execute(
            "SELECT * FROM motorvariante WHERE baureihe_id=?",
            (baureihe_id,),
        ).fetchall()

        motoren = []
        for m in motoren_rows:
            motor = dict(m)
            motor["getriebe"] = _parse_json_field(motor.get("getriebe"))

            motor["schwachstellen_motor"] = [
                dict(r) for r in conn.execute(
                    "SELECT bauteil,beschreibung,baujahre,kosten_ca "
                    "FROM schwachstelle_motor WHERE variante_id=?",
                    (motor["variante_id"],),
                ).fetchall()
            ]

            motor["kritische_wartung"] = [
                dict(r) for r in conn.execute(
                    "SELECT bauteil,intervall,hinweis "
                    "FROM kritische_wartung WHERE variante_id=?",
                    (motor["variante_id"],),
                ).fetchall()
            ]

            motoren.append(motor)

        result["motoren"] = motoren
        return result


def search_baureihen(query_marke: str | None = None, query_modell: str | None = None) -> list[dict]:
    """Suche für Chat-Endpunkt: gibt kompakte Übersicht zurück."""
    with get_conn() as conn:
        sql = "SELECT id,marke,modell,generation,bauzeitraum_von,bauzeitraum_bis,segment FROM baureihe WHERE 1=1"
        params: list = []
        if query_marke:
            sql += " AND LOWER(marke)=LOWER(?)"
            params.append(query_marke)
        if query_modell:
            sql += " AND LOWER(modell)=LOWER(?)"
            params.append(query_modell)
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
