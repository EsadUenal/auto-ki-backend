"""
Schreibt ein vollständiges Fahrzeug-Dict (Ebene 1 + Motoren) in SQLite + ChromaDB.
Dieselbe Logik wie seed_data.py / seed_vectors.py, aber als wiederverwendbare Funktion.
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import chromadb

from app.config import DB_PATH, DB_LEGACY_PATH, DB_BACKUP_DIR, CHROMA_PATH

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SQLite-Migration und Backup
# ---------------------------------------------------------------------------

def _ensure_db_migrated() -> None:
    """
    Stellt sicher, dass die Live-Datenbank am richtigen Ort (DB_PATH) liegt.

    Falls DB_PATH noch nicht existiert, aber DB_LEGACY_PATH (alter OneDrive-
    Pfad) vorhanden ist, wird die Datenbank automatisch migriert:
      1. DB_PATH-Verzeichnis anlegen
      2. Konsistente Kopie via sqlite3.Connection.backup()
      3. Integritätsprüfung der neuen Kopie
    """
    if DB_PATH.exists():
        return  # bereits migriert

    if not DB_LEGACY_PATH.exists():
        # Frische Installation — Verzeichnis anlegen, DB wird bei erster
        # Verbindung durch schema.sql initialisiert
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        log.info("Neues DB-Verzeichnis angelegt: %s", DB_PATH.parent)
        return

    # Legacy-Datenbank vorhanden → migrieren
    log.warning(
        "SQLite-Migration: %s → %s",
        DB_LEGACY_PATH, DB_PATH,
    )
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(DB_LEGACY_PATH)
    dst = sqlite3.connect(DB_PATH)
    src.backup(dst)
    dst.close()
    src.close()

    # Integritätsprüfung
    check = sqlite3.connect(DB_PATH)
    result = check.execute("PRAGMA integrity_check").fetchone()[0]
    n = check.execute("SELECT COUNT(*) FROM baureihe").fetchone()[0]
    check.close()

    if result != "ok":
        DB_PATH.unlink(missing_ok=True)
        raise RuntimeError(f"SQLite-Migration fehlgeschlagen: integrity_check={result!r}")

    log.info(
        "SQLite-Migration abgeschlossen: %d Baureihen, integrity=%s",
        n, result,
    )


_BACKUP_KEEP = 10  # Anzahl datierter Backups, die behalten werden


def _backup_sqlite() -> None:
    """
    Erstellt eine datierte Sicherungskopie in DB_BACKUP_DIR (OneDrive):
      auto_ki_backup_YYYY-MM-DD_HHMM.db

    Gleicher Zeitstempel innerhalb einer Minute → vorhandene Datei wird
    überschrieben (gewolltes Verhalten bei schnellen Mehrfach-Saves).
    Nach dem Schreiben werden ältere Dateien gelöscht, sodass nur die
    letzten _BACKUP_KEEP Versionen erhalten bleiben.

    Fehler werden nur geloggt, nie weitergeworfen.
    """
    try:
        from datetime import datetime
        DB_BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y-%m-%d_%H%M")
        dst_path = DB_BACKUP_DIR / f"auto_ki_backup_{ts}.db"

        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(dst_path)
        src.backup(dst)
        dst.close()
        src.close()

        sz = dst_path.stat().st_size
        log.info("SQLite-Backup erstellt: %s (%d KB)", dst_path.name, sz // 1024)

        # Rotation: nur letzte _BACKUP_KEEP Dateien behalten
        all_backups = sorted(DB_BACKUP_DIR.glob("auto_ki_backup_*.db"))
        for old in all_backups[:-_BACKUP_KEEP]:
            old.unlink(missing_ok=True)
            log.info("Altes Backup gelöscht: %s", old.name)

    except Exception as exc:
        log.warning("SQLite-Backup fehlgeschlagen (Daten in DB_PATH sicher): %s", exc)


def backup_sqlite_now() -> None:
    """Öffentlicher Einstiegspunkt für einen sofortigen Backup-Lauf.

    _backup_sqlite() sicherte bislang nur Fahrzeug-Admin-Schreibvorgänge
    (save_fahrzeug/patch_luecken) — Nutzerregistrierungen, Chats und Käufe
    lösten NIE ein Backup aus. Da die Sicherung die komplette SQLite-Datei
    kopiert (nicht nur Fahrzeugtabellen), deckt ein periodischer Aufruf
    dieser Funktion (siehe app.main) auch diese Daten ab.
    """
    _backup_sqlite()


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


# ---------- Lücken-Patch — nur fehlende Felder nachfüllen ----------

def patch_luecken(bid: str, daten: dict) -> None:
    """
    Füllt gezielt fehlende Felder einer bestehenden Baureihe nach.
    Berührt KEINE bestehenden Motoren, Ausstattungslinien o.ä.

    Unterstützte Felder in `daten`:
      "kaufberatung"            → UPDATE baureihe (Textfeld)
      "schwachstellen_baureihe" → DELETE + INSERT in schwachstelle_baureihe
      "rueckrufe"               → DELETE + INSERT in rueckruf

    Nur Felder, die in `daten` vorhanden UND nicht leer/null sind, werden geschrieben.
    """
    _ensure_db_migrated()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys=ON")
    heute = date.today().strftime("%Y-%m")

    # 1. Kaufberatung (einfaches UPDATE — kein Löschen nötig)
    if "kaufberatung" in daten and daten["kaufberatung"]:
        conn.execute(
            "UPDATE baureihe SET kaufberatung=?, letzte_aktualisierung=? WHERE id=?",
            (daten["kaufberatung"], heute, bid),
        )

    # 2. Schwachstellen Baureihe — nur wenn mindestens ein Eintrag vorhanden
    schw_neu = [s for s in daten.get("schwachstellen_baureihe", []) if s.get("bauteil")]
    if schw_neu:
        conn.execute("DELETE FROM schwachstelle_baureihe WHERE baureihe_id=?", (bid,))
        for s in schw_neu:
            conn.execute(
                "INSERT INTO schwachstelle_baureihe "
                "(baureihe_id,bauteil,beschreibung,betroffene_baujahre,schweregrad) "
                "VALUES (?,?,?,?,?)",
                (bid, s.get("bauteil"), s.get("beschreibung"),
                 s.get("betroffene_baujahre"), s.get("schweregrad", "gering")),
            )

    # 3. Rückrufe — nur wenn mindestens ein Eintrag vorhanden
    ruf_neu = [r for r in daten.get("rueckrufe", []) if r.get("mangel")]
    if ruf_neu:
        conn.execute("DELETE FROM rueckruf WHERE baureihe_id=?", (bid,))
        for r in ruf_neu:
            conn.execute(
                "INSERT INTO rueckruf "
                "(baureihe_id,datum,betroffene_baujahre,mangel,abhilfe,kba_referenz) "
                "VALUES (?,?,?,?,?,?)",
                (bid, r.get("datum"), r.get("betroffene_baujahre"),
                 r.get("mangel"), r.get("abhilfe"), r.get("kba_referenz")),
            )

    conn.commit()

    # Backup
    _backup_sqlite()

    # ChromaDB aktualisieren (nur wenn technische Felder geändert wurden)
    if schw_neu or ruf_neu:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM baureihe WHERE id=?", (bid,)).fetchone()
        if row:
            b = dict(row)
            schw_all = [dict(r) for r in conn.execute(
                "SELECT * FROM schwachstelle_baureihe WHERE baureihe_id=?", (bid,)
            ).fetchall()]
            ruf_all = [dict(r) for r in conn.execute(
                "SELECT * FROM rueckruf WHERE baureihe_id=?", (bid,)
            ).fetchall()]
            ausstatt_all = [dict(r) for r in conn.execute(
                "SELECT * FROM ausstattungslinie WHERE baureihe_id=?", (bid,)
            ).fetchall()]
            motoren_rows = [dict(r) for r in conn.execute(
                "SELECT * FROM motorvariante WHERE baureihe_id=?", (bid,)
            ).fetchall()]
            for m in motoren_rows:
                m["schwachstellen_motor"] = [dict(r) for r in conn.execute(
                    "SELECT * FROM schwachstelle_motor WHERE variante_id=?", (m["variante_id"],)
                ).fetchall()]
            chroma_data = {
                "marke": b["marke"], "modell": b["modell"], "generation": b["generation"],
                "erkennung_generation": b.get("erkennung_generation"),
                "facelift_merkmale":    b.get("facelift_merkmale"),
                "ausstattungslinien":   ausstatt_all,
                "schwachstellen_baureihe": schw_all,
                "rueckrufe":            ruf_all,
                "motoren":              motoren_rows,
            }
            _update_chroma(bid, chroma_data)

    conn.close()


# ---------- Haupt-Funktion ----------

def save_fahrzeug(data: dict) -> str:
    """
    Schreibt das Fahrzeug-Dict in SQLite und ChromaDB.
    Gibt die baureihe_id zurück.
    Idempotent: existierende Einträge werden überschrieben (INSERT OR REPLACE).

    Nach erfolgreichem Speichern wird automatisch ein konsistenter Backup
    der SQLite-Datenbank nach DB_BACKUP_PATH (OneDrive) geschrieben.
    """
    _ensure_db_migrated()   # transparente Migration aus Legacy-Pfad
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
                heck_emblem, optische_unterscheidung,
                tankgroesse_liter, kofferraum_liter, batteriekapazitaet_kwh,
                anhaengelast_gebremst_kg, anhaengelast_ungebremst_kg,
                abgasnorm, felgengroesse_serie)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                m.get("tankgroesse_liter"), m.get("kofferraum_liter"), m.get("batteriekapazitaet_kwh"),
                m.get("anhaengelast_gebremst_kg"), m.get("anhaengelast_ungebremst_kg"),
                m.get("abgasnorm"), m.get("felgengroesse_serie"),
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
    # 8. SQLite-Backup nach OneDrive (konsistente Cloud-Sicherung)
    # ------------------------------------------------------------------
    _backup_sqlite()

    # ------------------------------------------------------------------
    # 9. ChromaDB aktualisieren
    # ------------------------------------------------------------------
    _update_chroma(bid, data)

    return bid


# ---------- ChromaDB — Hilfsfunktionen ----------

def _is_chroma_index_error(exc: Exception) -> bool:
    """Erkennt korrupte HNSW-Index-Fehler von ChromaDB."""
    msg = str(exc).lower()
    return any(kw in msg for kw in [
        "hnsw", "segment reader", "backfill", "compactor",
        "error loading", "error constructing",
    ])


def _rebuild_chroma_from_sqlite() -> dict:
    """
    Vollständiger Neuaufbau der ChromaDB aus SQLite-Daten.

    Löscht das komplette chroma/-Verzeichnis und befüllt beide Collections
    (optisches_wissen, technisches_wissen) neu aus allen Baureihen in SQLite.
    Nutzt dasselbe ID-Schema wie _update_chroma: {bid}__o_{n} / {bid}__t_{n}.

    Gibt Statistik zurück: {"baureihen": K, "optisch": N, "technisch": M}
    Sicher bei laufendem Server — SQLite bleibt unangetastet.
    """
    log.warning("ChromaDB-Neuaufbau gestartet — lösche: %s", CHROMA_PATH)
    shutil.rmtree(CHROMA_PATH, ignore_errors=True)
    CHROMA_PATH.mkdir(parents=True, exist_ok=True)

    chroma  = chromadb.PersistentClient(path=str(CHROMA_PATH))
    optik   = chroma.get_or_create_collection("optisches_wissen",   metadata={"hnsw:space": "cosine"})
    technik = chroma.get_or_create_collection("technisches_wissen", metadata={"hnsw:space": "cosine"})

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    baureihen = conn.execute("SELECT * FROM baureihe ORDER BY id").fetchall()

    total_o = 0
    total_t = 0

    for b in baureihen:
        bid = b["id"]
        meta_base = {
            "baureihe_id": bid,
            "marke":       b["marke"],
            "modell":      b["modell"],
            "generation":  b["generation"],
        }

        # -- Optisches Wissen --
        o_docs, o_ids, o_metas = [], [], []
        o_idx = 0

        def _oadd_r(text: str, feld: str, extra: dict | None = None):
            nonlocal o_idx
            o_docs.append(text)
            o_ids.append(f"{bid}__o_{o_idx}")
            o_metas.append({**meta_base, "feld": feld, **(extra or {})})
            o_idx += 1

        if b["erkennung_generation"]:
            _oadd_r(b["erkennung_generation"], "erkennung_generation")
        if b["facelift_merkmale"]:
            _oadd_r(b["facelift_merkmale"], "facelift_merkmale")

        for l in conn.execute(
            "SELECT * FROM ausstattungslinie WHERE baureihe_id=?", (bid,)
        ).fetchall():
            parts = [f"{l['name']} ({l['typ']})"]
            if l["optische_merkmale"]:
                parts.append(l["optische_merkmale"])
            if l["abgrenzung"]:
                parts.append(f"Abgrenzung: {l['abgrenzung']}")
            _oadd_r(" — ".join(parts), "ausstattungslinie", {"name": l["name"] or ""})

        if o_docs:
            optik.upsert(documents=o_docs, ids=o_ids, metadatas=o_metas)
            total_o += len(o_docs)

        # -- Technisches Wissen --
        t_docs, t_ids, t_metas = [], [], []
        t_idx = 0
        seen_t: set[str] = set()

        def _tadd_r(text: str, feld: str, extra: dict | None = None):
            nonlocal t_idx
            if text in seen_t:
                return
            seen_t.add(text)
            t_docs.append(text)
            t_ids.append(f"{bid}__t_{t_idx}")
            t_metas.append({**meta_base, "feld": feld, **(extra or {})})
            t_idx += 1

        for s in conn.execute(
            "SELECT * FROM schwachstelle_baureihe WHERE baureihe_id=?", (bid,)
        ).fetchall():
            _tadd_r(
                f"Schwachstelle: {s['bauteil']}. {s['beschreibung']} "
                f"(Baujahre: {s['betroffene_baujahre']}, Schweregrad: {s['schweregrad']})",
                "schwachstelle_baureihe", {"schweregrad": s["schweregrad"] or ""},
            )

        for r in conn.execute(
            "SELECT * FROM rueckruf WHERE baureihe_id=?", (bid,)
        ).fetchall():
            _tadd_r(
                f"Rückruf {r['datum']}: {r['mangel']} "
                f"(betroffen: {r['betroffene_baujahre']}, Abhilfe: {r['abhilfe']})",
                "rueckruf",
            )

        for s in conn.execute(
            """SELECT sm.*, mv.bezeichnung, mv.motorcode
               FROM schwachstelle_motor sm
               JOIN motorvariante mv ON sm.variante_id = mv.variante_id
               WHERE mv.baureihe_id=?""",
            (bid,),
        ).fetchall():
            _tadd_r(
                f"Motorproblem ({s['motorcode']}): {s['bauteil']}. {s['beschreibung']} "
                f"(Baujahre: {s['baujahre']}, Kosten ca.: {s['kosten_ca']})",
                "schwachstelle_motor", {"motorcode": s["motorcode"] or ""},
            )

        if t_docs:
            technik.upsert(documents=t_docs, ids=t_ids, metadatas=t_metas)
            total_t += len(t_docs)

    conn.close()
    stats = {"baureihen": len(baureihen), "optisch": total_o, "technisch": total_t}
    log.info("ChromaDB-Neuaufbau abgeschlossen: %s", stats)
    return stats


# ---------- ChromaDB ----------

def _update_chroma(bid: str, data: dict):
    """
    Aktualisiert ChromaDB für eine Baureihe (mit automatischer HNSW-Reparatur).

    Bei korruptem HNSW-Index (InternalError) wird automatisch ein vollständiger
    Neuaufbau aus SQLite gestartet — kein manuelles Eingreifen nötig.
    SQLite ist bereits gespeichert, daher ist der Rebuild vollständig.
    """
    try:
        _update_chroma_inner(bid, data)
    except Exception as exc:
        if _is_chroma_index_error(exc):
            log.error(
                "ChromaDB HNSW-Fehler beim Schreiben von '%s' — starte automatischen "
                "Neuaufbau aus SQLite. Fehler: %s", bid, exc,
            )
            try:
                stats = _rebuild_chroma_from_sqlite()
                log.info("ChromaDB-Neuaufbau erfolgreich nach Fehler: %s", stats)
            except Exception as rebuild_exc:
                log.error("ChromaDB-Neuaufbau fehlgeschlagen: %s", rebuild_exc)
                raise RuntimeError(
                    f"ChromaDB-Index korrupt und Neuaufbau fehlgeschlagen. "
                    f"Bitte manuell: python rebuild_chroma.py\n({rebuild_exc})"
                ) from rebuild_exc
        else:
            raise


def _update_chroma_inner(bid: str, data: dict):
    """
    Interner ChromaDB-Schreibvorgang für eine Baureihe.

    ID-Schema: "{bid}__o_{n}" für optisches Wissen,
               "{bid}__t_{n}" für technisches Wissen.
    n ist ein globaler Laufindex — garantiert eindeutig innerhalb eines Aufrufs.

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
