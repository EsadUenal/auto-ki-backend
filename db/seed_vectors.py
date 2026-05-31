"""
Überträgt Fließtext-Felder aus SQLite in ChromaDB.
Muss nach seed_data.py ausgeführt werden.
Ausführen: python db/seed_vectors.py
"""

import sqlite3
from pathlib import Path
from vector_schema import get_client, get_collections

DB_PATH = Path(__file__).parent / "auto_ki.db"


def _doc_id(baureihe_id: str, feld: str, suffix: str = "") -> str:
    return f"{baureihe_id}__{feld}{suffix}"


def seed_vectors():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    client = get_client()
    cols = get_collections(client)
    optik = cols["optisches_wissen"]
    technik = cols["technisches_wissen"]

    # Collections leeren (idempotenter Re-Seed)
    for col in [optik, technik]:
        existing = col.get()["ids"]
        if existing:
            col.delete(ids=existing)

    baureihen = conn.execute("SELECT * FROM baureihe").fetchall()

    for b in baureihen:
        meta_base = {
            "baureihe_id": b["id"],
            "marke": b["marke"],
            "modell": b["modell"],
            "generation": b["generation"],
        }

        # --- Optisches Wissen ---
        optik_docs, optik_ids, optik_metas = [], [], []

        if b["erkennung_generation"]:
            optik_docs.append(b["erkennung_generation"])
            optik_ids.append(_doc_id(b["id"], "erkennung"))
            optik_metas.append({**meta_base, "feld": "erkennung_generation"})

        if b["facelift_merkmale"]:
            optik_docs.append(b["facelift_merkmale"])
            optik_ids.append(_doc_id(b["id"], "facelift"))
            optik_metas.append({**meta_base, "feld": "facelift_merkmale"})

        # Ausstattungslinien-Texte
        linien = conn.execute(
            "SELECT * FROM ausstattungslinie WHERE baureihe_id=?", (b["id"],)
        ).fetchall()
        for l in linien:
            text_parts = [f"{l['name']} ({l['typ']})"]
            if l["optische_merkmale"]:
                text_parts.append(l["optische_merkmale"])
            if l["abgrenzung"]:
                text_parts.append(f"Abgrenzung: {l['abgrenzung']}")
            optik_docs.append(" — ".join(text_parts))
            optik_ids.append(_doc_id(b["id"], "linie", f"_{l['id']}"))
            optik_metas.append({**meta_base, "feld": "ausstattungslinie", "name": l["name"]})

        if optik_docs:
            optik.add(documents=optik_docs, ids=optik_ids, metadatas=optik_metas)

        # --- Technisches Wissen (Schwachstellen, Rückrufe) ---
        tech_docs, tech_ids, tech_metas = [], [], []

        schwachstellen = conn.execute(
            "SELECT * FROM schwachstelle_baureihe WHERE baureihe_id=?", (b["id"],)
        ).fetchall()
        for s in schwachstellen:
            text = (
                f"Schwachstelle: {s['bauteil']}. {s['beschreibung']} "
                f"(Baujahre: {s['betroffene_baujahre']}, Schweregrad: {s['schweregrad']})"
            )
            tech_docs.append(text)
            tech_ids.append(_doc_id(b["id"], "sw_baureihe", f"_{s['id']}"))
            tech_metas.append({**meta_base, "feld": "schwachstelle_baureihe",
                               "schweregrad": s["schweregrad"]})

        rueckrufe = conn.execute(
            "SELECT * FROM rueckruf WHERE baureihe_id=?", (b["id"],)
        ).fetchall()
        for r in rueckrufe:
            text = (
                f"Rückruf {r['datum']}: {r['mangel']} "
                f"(betroffen: {r['betroffene_baujahre']}, Abhilfe: {r['abhilfe']})"
            )
            tech_docs.append(text)
            tech_ids.append(_doc_id(b["id"], "rueckruf", f"_{r['id']}"))
            tech_metas.append({**meta_base, "feld": "rueckruf"})

        # Motor-Schwachstellen
        motor_schwachstellen = conn.execute(
            """SELECT sm.*, mv.bezeichnung, mv.motorcode
               FROM schwachstelle_motor sm
               JOIN motorvariante mv ON sm.variante_id = mv.variante_id
               WHERE mv.baureihe_id=?""",
            (b["id"],),
        ).fetchall()
        seen_motor_sw = set()
        for s in motor_schwachstellen:
            key = (s["bauteil"], s["beschreibung"])
            if key in seen_motor_sw:
                continue  # S55-Duplikate (Basis+Competition) nicht doppelt
            seen_motor_sw.add(key)
            text = (
                f"Motorproblem ({s['motorcode']}): {s['bauteil']}. {s['beschreibung']} "
                f"(Baujahre: {s['baujahre']}, Kosten ca.: {s['kosten_ca']})"
            )
            tech_docs.append(text)
            tech_ids.append(_doc_id(b["id"], "sw_motor", f"_{s['id']}"))
            tech_metas.append({**meta_base, "feld": "schwachstelle_motor",
                               "motorcode": s["motorcode"]})

        if tech_docs:
            technik.add(documents=tech_docs, ids=tech_ids, metadatas=tech_metas)

    conn.close()

    print("Vektor-DB Zusammenfassung:")
    print(f"  optisches_wissen: {optik.count()} Dokumente")
    print(f"  technisches_wissen: {technik.count()} Dokumente")
    print("\nSeed Vektoren OK.")


if __name__ == "__main__":
    seed_vectors()
