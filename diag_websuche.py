"""
Diagnose: Warum löst die Websuche für manche Anfragen nicht aus?
Ruft die internen Funktionen direkt auf und loggt jeden Entscheidungsschritt.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app.llm import _detect_baureihe_ids, _needs_web_search, _sql_context, _vector_search
from app.config import TAVILY_API_KEY

ANFRAGEN = [
    "Schwachstellen Audi A4 B8?",
    "BMW 3er E36 Schwachstellen?",
    "Mercedes W204 vs W205?",
    "Motoren VW Tiguan 1. Generation?",
]

SEP = "─" * 60

for anfrage in ANFRAGEN:
    print(f"\n{SEP}")
    print(f"ANFRAGE: {anfrage!r}")
    print(SEP)

    # Schritt 1: Baureihe erkennen
    baureihe_ids = _detect_baureihe_ids(anfrage, [])
    print(f"[1] _detect_baureihe_ids → {baureihe_ids!r}")
    if baureihe_ids:
        print("    → Fahrzeug in _KNOWN-Map gefunden")
    else:
        print("    → NICHT in _KNOWN-Map → baureihe_ids = []")

    # Schritt 2: DB-Kontext laden (nur wenn IDs vorhanden)
    if baureihe_ids:
        sql_ctx = _sql_context(baureihe_ids)
        vec_docs = _vector_search(anfrage, baureihe_ids)
        print(f"[2] DB-Kontext: SQL={len(sql_ctx)} Zeichen, Vektoren={len(vec_docs)} Docs")
        if "Schwachstellen Baureihe:" in sql_ctx:
            print("    → Schwachstellen-Feld VORHANDEN in DB")
        else:
            print("    → Schwachstellen-Feld FEHLT oder LEER in DB")
    else:
        print("[2] DB-Kontext: übersprungen (keine baureihe_ids)")

    # Schritt 3: Web-Suche-Entscheidung
    # Zeige den aktuellen Stand von _needs_web_search
    from app.llm import _PREIS_KEYWORDS, _RECALL_KEYWORDS
    msg_lower = anfrage.lower()
    preis_treffer = [kw for kw in _PREIS_KEYWORDS if kw in msg_lower]
    recall_treffer = [kw for kw in _RECALL_KEYWORDS if kw in msg_lower]

    print(f"[3] Preis-Keywords getroffen:   {preis_treffer or 'keine'}")
    print(f"    Rückruf-Keywords getroffen: {recall_treffer or 'keine'}")
    print(f"    baureihe_ids leer?          {not baureihe_ids}")

    # Versuche _needs_web_search mit aktueller Signatur (ggf. mit oder ohne baureihe_ids-Param)
    import inspect
    sig = inspect.signature(_needs_web_search)
    params = list(sig.parameters.keys())
    print(f"    _needs_web_search Signatur: {params}")

    if len(params) == 1:
        result = _needs_web_search(anfrage)
        print(f"    _needs_web_search(message) → {result}")
        print("    HINWEIS: Alte Signatur — baureihe_ids wird NICHT geprüft!")
    else:
        result = _needs_web_search(anfrage, baureihe_ids)
        print(f"    _needs_web_search(message, baureihe_ids) → {result}")

    print(f"    TAVILY_API_KEY gesetzt?     {bool(TAVILY_API_KEY)}")

    if result and TAVILY_API_KEY:
        print("    ✓ WEB-SUCHE WIRD AUSGELÖST")
    elif result and not TAVILY_API_KEY:
        print("    ✗ Web-Suche BLOCKIERT: TAVILY_API_KEY fehlt")
    else:
        print("    ✗ Web-Suche NICHT ausgelöst (_needs_web_search = False)")

print(f"\n{SEP}")
