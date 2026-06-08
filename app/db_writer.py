"""
Schreibt ein vollständiges Fahrzeug-Dict (Ebene 1 + Motoren) in SQLite + ChromaDB.
Dieselbe Logik wie seed_data.py / seed_vectors.py, aber als wiederverwendbare Funktion.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import chromadb

from app.config import DB_PATH, CHROMA_PATH


# ---------- Hilfsfunktionen ----------

def _slug(text: str) -> str:
    """'BMW M4 G82' → 'bmw-m4-g82'"""
    import re
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _auto_id(marke: str, modell: str, generation: str) -> str:
    return _slug(f"{marke} {modell} {generation}")


def _variante_id(baureihe_id: str, bezeichnung: str) -> str:
    return _slug(f"{baureihe_id} {bezeichnung}")


def _j(value) -> str | None:
    """Liste → JSON-String für SQLite; None bleibt None."""
    if value is None:
        return None
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return value


# ---------- Haupt-Funktion ----------

def save_fahrzeug(data: dict) -> str:
    """
    Schreibt das Fahrzeug-Dict in SQLite und ChromaDB.
    Gibt die baureihe_id zurück.
    Idempotent: existierende Einträge werden überschrieben (INSERT OR REPLACE).
    """
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys=ON")

    marke      = data["marke"]
    modell     = data["modell"]
    generation = data["generation"]
    bid        = data.get("id") or _auto_id(marke, modell, generation)

    heute = date.today().strftime("%Y-%m")

    # ------------------------------------------------------------------
    # 1. Alte Daten löschen — richtige Reihenfolge wegen FKs
    #    kritische_wartung + schwachstelle_motor hängen an variante_id,
    #    nicht direkt an baureihe_id → erst Varianten-IDs holen, dann löschen
    # ------------------------------------------------------------------
    variante_ids = [r[0] for r in conn.execute(
        "SELECT variante_id FROM motorvariante WHERE baureihe_id=?", (bid,)
    ).fetchall()]
    if variante_ids:
        placeholders = ",".join("?" * len(variante_ids))
        conn.execute(f"DELETE FROM kritische_wartung WHERE variante_id IN ({placeholders})", variante_ids)
        conn.execute(f"DELETE FROM schwachstelle_motor WHERE variante_id IN ({placeholders})", variante_ids)
    conn.execute("DELETE FROM motorvariante          WHERE baureihe_id=?", (bid,))
    conn.execute("DELETE FROM rueckruf               WHERE baureihe_id=?", (bid,))
    conn.execute("DELETE FROM schwachstelle_baureihe WHERE baureihe_id=?", (bid,))
    conn.execute("DELETE FROM ausstattungslinie      WHERE baureihe_id=?", (bid,))
    conn.execute("DELETE FROM quelle                 WHERE baureihe_id=?", (bid,))
    conn.execute("DELETE FROM baureihe               WHERE id=?",          (bid,))

    # ------------------------------------------------------------------
    # 2. Baureihe (Ebene 1)
    # ------------------------------------------------------------------
    conn.execute(
        """INSERT INTO baureihe
           (id, marke, modell, generation, bauzeitraum_von, bauzeitraum_bis,
            karosserie, segment, vorgaenger, erkennung_generation, facelift_merkmale,
            adac_pannenkennziffer, tuev_maengelquote, dekra_urteil,
            euro_ncap_sterne, euro_ncap_jahr,
            wartung_oel_km, wartung_hu_intervall, kaufberatung, letzte_aktualisierung)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            bid,
            marke, modell, generation,
            data.get("bauzeitraum_von"),
            data.get("bauzeitraum_bis"),
            _j(data.get("karosserie")),
            data.get("segment"),
            data.get("vorgaenger"),
            data.get("erkennung_generation"),
            data.get("facelift_merkmale"),
            data.get("adac_pannenkennziffer"),
            data.get("tuev_maengelquote"),
            data.get("dekra_urteil"),
            data.get("euro_ncap_sterne"),
            data.get("euro_ncap_jahr"),
            data.get("wartung_oel_km"),
            data.get("wartung_hu_intervall"),
            data.get("kaufberatung"),
            data.get("letzte_aktualisierung") or heute,
        ),
    )

    # ------------------------------------------------------------------
    # 3. Ausstattungslinien
    # ------------------------------------------------------------------
    for l in data.get("ausstattungslinien", []):
        conn.execute(
            "INSERT INTO ausstattungslinie (baureihe_id,name,typ,optische_merkmale,abgrenzung) VALUES (?,?,?,?,?)",
            (bid, l.get("name"), l.get("typ"), l.get("optische_merkmale"), l.get("abgrenzung")),
        )

    # ------------------------------------------------------------------
    # 4. Schwachstellen Baureihe
    # ------------------------------------------------------------------
    for s in data.get("schwachstellen_baureihe", []):
        conn.execute(
            "INSERT INTO schwachstelle_baureihe (baureihe_id,bauteil,beschreibung,betroffene_baujahre,schweregrad) VALUES (?,?,?,?,?)",
            (bid, s.get("bauteil"), s.get("beschreibung"), s.get("betroffene_baujahre"), s.get("schweregrad", "gering")),
        )

    # ------------------------------------------------------------------
    # 5. Rückrufe
    # ------------------------------------------------------------------
    for r in data.get("rueckrufe", []):
        conn.execute(
            "INSERT INTO rueckruf (baureihe_id,datum,betroffene_baujahre,mangel,abhilfe,kba_referenz) VALUES (?,?,?,?,?,?)",
            (bid, r.get("datum"), r.get("betroffene_baujahre"), r.get("mangel"), r.get("abhilfe"), r.get("kba_referenz")),
        )

    # ------------------------------------------------------------------
    # 6. Quellen
    # ------------------------------------------------------------------
    for q in data.get("quellen", []):
        conn.execute(
            "INSERT INTO quelle (baureihe_id,quelle,url,abrufdatum) VALUES (?,?,?,?)",
            (bid, q.get("quelle"), q.get("url"), q.get("abrufdatum")),
        )

    # ------------------------------------------------------------------
    # 7. Motorvarianten (Ebene 2)
    # ------------------------------------------------------------------
    for m in data.get("motoren", []):
        vid = m.get("variante_id") or _variante_id(bid, m.get("bezeichnung", ""))
        conn.execute(
            """INSERT INTO motorvariante
               (variante_id, baureihe_id, bezeichnung, motorcode, kraftstoff,
                hubraum_ccm, zylinder, leistung_ps, leistung_kw, drehmoment_nm,
                getriebe, antrieb, beschleunigung_0_100, vmax_kmh,
                verbrauch_wltp, verbrauch_real, co2_g_km, neupreis_ca_eur,
                heck_emblem, optische_unterscheidung)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                vid, bid,
                m.get("bezeichnung"), m.get("motorcode"), m.get("kraftstoff"),
                m.get("hubraum_ccm"), m.get("zylinder"),
                m.get("leistung_ps"), m.get("leistung_kw"), m.get("drehmoment_nm"),
                _j(m.get("getriebe")), m.get("antrieb"),
                m.get("beschleunigung_0_100"), m.get("vmax_kmh"),
                m.get("verbrauch_wltp"), m.get("verbrauch_real"),
                m.get("co2_g_km"), m.get("neupreis_ca_eur"),
                m.get("heck_emblem"), m.get("optische_unterscheidung"),
            ),
        )

        for s in m.get("schwachstellen_motor", []):
            conn.execute(
                "INSERT INTO schwachstelle_motor (variante_id,bauteil,beschreibung,baujahre,kosten_ca) VALUES (?,?,?,?,?)",
                (vid, s.get("bauteil"), s.get("beschreibung"), s.get("baujahre"), s.get("kosten_ca")),
            )

        for w in m.get("kritische_wartung", []):
            conn.execute(
                "INSERT INTO kritische_wartung (variante_id,bauteil,intervall,hinweis) VALUES (?,?,?,?)",
                (vid, w.get("bauteil"), w.get("intervall"), w.get("hinweis")),
            )

    conn.commit()
    conn.close()

    # ------------------------------------------------------------------
    # 8. ChromaDB aktualisieren
    # ------------------------------------------------------------------
    _update_chroma(bid, data)

    return bid


# ---------- ChromaDB ----------

def _update_chroma(bid: str, data: dict):
    """
    Aktualisiert ChromaDB für eine Baureihe.

    ID-Schema: "{bid}__o_{n}" für optisches Wissen,
               "{bid}__t_{n}" für technisches Wissen.
    n ist ein globaler Laufindex — garantiert eindeutig innerhalb eines Aufrufs,
    unabhängig von Motorcode oder Inhalt.

    Ablauf:
      1. Alte Einträge dieser baureihe_id löschen (sauberer Ausgangspunkt)
      2. Neue Einträge via upsert schreiben (idempotent, nie Duplikatfehler)
    """
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    optik   = client.get_or_create_collection("optisches_wissen",   metadata={"hnsw:space": "cosine"})
    technik = client.get_or_create_collection("technisches_wissen", metadata={"hnsw:space": "cosine"})

    # Alte Einträge dieser Baureihe vollständig löschen
    for col in [optik, technik]:
        existing = col.get(where={"baureihe_id": bid})
        if existing["ids"]:
            col.delete(ids=existing["ids"])

    meta_base = {
        "baureihe_id": bid,
        "marke":       data["marke"],
        "modell":      data["modell"],
        "generation":  data["generation"],
    }

    # -- Optisches Wissen --
    o_docs, o_ids, o_metas = [], [], []
    o_idx = 0  # globaler Laufindex — verhindert ID-Kollisionen

    def _oadd(text: str, feld: str, extra: dict | None = None):
        nonlocal o_idx
        o_docs.append(text)
        o_ids.append(f"{bid}__o_{o_idx}")
        o_metas.append({**meta_base, "feld": feld, **(extra or {})})
        o_idx += 1

    if data.get("erkennung_generation"):
        _oadd(data["erkennung_generation"], "erkennung_generation")
    if data.get("facelift_merkmale"):
        _oadd(data["facelift_merkmale"], "facelift_merkmale")
    for l in data.get("ausstattungslinien", []):
        parts = [f"{l.get('name')} ({l.get('typ')})"]
        if l.get("optische_merkmale"):
            parts.append(l["optische_merkmale"])
        if l.get("abgrenzung"):
            parts.append(f"Abgrenzung: {l['abgrenzung']}")
        _oadd(" — ".join(parts), "ausstattungslinie", {"name": l.get("name", "")})

    if o_docs:
        # upsert statt add → kein Fehler bei erneutem Speichern derselben Baureihe
        optik.upsert(documents=o_docs, ids=o_ids, metadatas=o_metas)

    # -- Technisches Wissen --
    t_docs, t_ids, t_metas = [], [], []
    t_idx = 0       # globaler Laufindex
    seen_text: set[str] = set()  # identische Texte überspringen

    def _tadd(text: str, feld: str, extra: dict | None = None):
        nonlocal t_idx
        # Inhaltsbasierte Deduplizierung (gleiche Beschreibung mehrerer Motoren)
        if text in seen_text:
            return
        seen_text.add(text)
        t_docs.append(text)
        t_ids.append(f"{bid}__t_{t_idx}")
        t_metas.append({**meta_base, "feld": feld, **(extra or {})})
        t_idx += 1

    for s in data.get("schwachstellen_baureihe", []):
        _tadd(
            f"Schwachstelle: {s.get('bauteil')}. {s.get('beschreibung')} "
            f"(Baujahre: {s.get('betroffene_baujahre')}, Schweregrad: {s.get('schweregrad')})",
            "schwachstelle_baureihe", {"schweregrad": s.get("schweregrad", "")},
        )

    for r in data.get("rueckrufe", []):
        _tadd(
            f"Rückruf {r.get('datum')}: {r.get('mangel')} "
            f"(betroffen: {r.get('betroffene_baujahre')}, Abhilfe: {r.get('abhilfe')})",
            "rueckruf",
        )

    for m in data.get("motoren", []):
        for s in m.get("schwachstellen_motor", []):
            _tadd(
                f"Motorproblem ({m.get('motorcode')}): {s.get('bauteil')}. {s.get('beschreibung')} "
                f"(Baujahre: {s.get('baujahre')}, Kosten ca.: {s.get('kosten_ca')})",
                "schwachstelle_motor", {"motorcode": m.get("motorcode", "")},
            )

    if t_docs:
        # upsert statt add → idempotent, kein Duplikatfehler
        technik.upsert(documents=t_docs, ids=t_ids, metadatas=t_metas)
