"""
Standalone-Skript: ChromaDB vollständig aus SQLite neu aufbauen.

Verwendung:
    python rebuild_chroma.py

Wann verwenden:
  - Nach "Error loading hnsw index" / "Error constructing hnsw segment reader"
  - Nach abgebrochenem Schreibvorgang
  - Nach OneDrive-Sync-Konflikt im db/chroma/-Verzeichnis
  - Zur Fehlerbehebung generell

Was passiert:
  1. db/chroma/ wird komplett gelöscht (HNSW-Dateien + Metadata-DB)
  2. Beide Collections (optisches_wissen, technisches_wissen) werden
     aus den aktuellen SQLite-Daten neu befüllt
  3. SQLite (db/auto_ki.db) wird NICHT verändert

Laufzeit: ca. 10–30 Sekunden für 100 Baureihen.
"""
import sys
import io
import logging
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)

from app.db_writer import _rebuild_chroma_from_sqlite
from app.config import DB_PATH, CHROMA_PATH
import sqlite3


def main():
    print("=" * 60)
    print("ChromaDB-Neuaufbau aus SQLite")
    print("=" * 60)

    # SQLite-Bestand vorab zeigen
    try:
        conn = sqlite3.connect(DB_PATH)
        n_baureihen = conn.execute("SELECT COUNT(*) FROM baureihe").fetchone()[0]
        n_motoren   = conn.execute("SELECT COUNT(*) FROM motorvariante").fetchone()[0]
        n_sw_b      = conn.execute("SELECT COUNT(*) FROM schwachstelle_baureihe").fetchone()[0]
        n_sw_m      = conn.execute("SELECT COUNT(*) FROM schwachstelle_motor").fetchone()[0]
        n_rueckrufe = conn.execute("SELECT COUNT(*) FROM rueckruf").fetchone()[0]
        conn.close()
    except Exception as e:
        print(f"FEHLER: SQLite nicht lesbar — {e}")
        sys.exit(1)

    print(f"\nSQLite-Bestand (wird NICHT veraendert):")
    print(f"  Baureihen            : {n_baureihen}")
    print(f"  Motorvarianten       : {n_motoren}")
    print(f"  Schwachst. Baureihe  : {n_sw_b}")
    print(f"  Schwachst. Motor     : {n_sw_m}")
    print(f"  Rueckrufe            : {n_rueckrufe}")

    print(f"\nChromaDB-Pfad        : {CHROMA_PATH}")
    print(f"\nStarte Neuaufbau...")
    t0 = time.time()

    try:
        stats = _rebuild_chroma_from_sqlite()
    except Exception as e:
        print(f"\nFEHLER beim Neuaufbau: {type(e).__name__}: {e}")
        sys.exit(1)

    dt = time.time() - t0
    print(f"\nNeuaufbau abgeschlossen in {dt:.1f}s")
    print(f"\nChromaDB-Ergebnis:")
    print(f"  Baureihen verarbeitet: {stats['baureihen']}")
    print(f"  optisches_wissen     : {stats['optisch']} Vektordokumente")
    print(f"  technisches_wissen   : {stats['technisch']} Vektordokumente")

    # Kurzer Suchtest
    print(f"\nSuchtest...")
    try:
        import chromadb as _chroma
        client = _chroma.PersistentClient(path=str(CHROMA_PATH))
        optik   = client.get_collection("optisches_wissen")
        technik = client.get_collection("technisches_wissen")

        r_o = optik.query(query_texts=["BMW M4 Scheinwerfer"], n_results=2)
        r_t = technik.query(query_texts=["Nockenwellen Verschleiß Motor"], n_results=2)

        print(f"  Optik-Query   : {r_o['ids'][0]}")
        print(f"  Technik-Query : {r_t['ids'][0]}")
        print(f"\nAlles OK — ChromaDB ist wieder einsatzbereit.")
    except Exception as e:
        print(f"\nWARNUNG: Suchtest fehlgeschlagen — {e}")
        print("Der Neuaufbau war moeglicherweise nicht vollstaendig.")
        sys.exit(1)


if __name__ == "__main__":
    main()
