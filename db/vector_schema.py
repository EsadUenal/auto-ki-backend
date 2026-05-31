"""
Vektor-Datenbank Setup (ChromaDB).
Speichert Fließtext-Felder, die nicht in SQL passen:
  - erkennung_generation, facelift_merkmale
  - kaufberatung
  - schwachstellen (Beschreibungen)
  - kritische_wartung (Hinweise)
"""

import chromadb
from pathlib import Path

CHROMA_PATH = Path(__file__).parent / "chroma"


def get_client() -> chromadb.PersistentClient:
    return chromadb.PersistentClient(path=str(CHROMA_PATH))


def get_collections(client: chromadb.PersistentClient) -> dict:
    """Gibt alle Collections zurück, legt sie an wenn nötig."""

    # Optisches Wissen: Erkennung + Facelift (für "Was ist das?"-Fragen)
    optik = client.get_or_create_collection(
        name="optisches_wissen",
        metadata={"hnsw:space": "cosine"},
    )

    # Schwachstellen + Wartung (für "Probleme?"-Fragen)
    technik = client.get_or_create_collection(
        name="technisches_wissen",
        metadata={"hnsw:space": "cosine"},
    )

    # Kaufberatungs-Texte (für spätere Phase, jetzt schon vorsehen)
    kauf = client.get_or_create_collection(
        name="kaufberatung",
        metadata={"hnsw:space": "cosine"},
    )

    return {"optisches_wissen": optik, "technisches_wissen": technik, "kaufberatung": kauf}


# Dokument-Format für alle Collections:
# {
#   "id":        "<baureihe_id>__<feld>",       z.B. "bmw-m4-f82__erkennung"
#   "document":  "<Fließtext>",
#   "metadata":  {
#       "baureihe_id": "bmw-m4-f82",
#       "marke":       "BMW",
#       "modell":      "M4",
#       "generation":  "F82",
#       "feld":        "erkennung_generation",   Herkunft des Textes
#       "variante_id": null | "bmw-m4-f82-basis" (bei motor-spez. Einträgen)
#   }
# }
