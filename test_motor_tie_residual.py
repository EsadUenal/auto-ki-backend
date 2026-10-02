"""
RESIDUAL MOTOR-CANDIDATE TIE — Zusicherungen (Release-Hardening, "Ambiguous
Motor Selection").

KEIN Netzwerk, KEIN LLM-Call, KEINE Tavily-Calls, KEINE DB-Mutation.

Fragestellung: kann die Reihenfolge der Motorvarianten in der Baureihe (also
reine DB-/Listenreihenfolge, keine fachliche Eigenschaft) das semantische
Ergebnis eines Checks verändern, wenn zwei Kandidaten auf allen bekannten
harten Achsen (Leistung, Kraftstoff, Antrieb, Getriebe, Modellname im
Freitext) GLEICH gut passen, sich aber in einer varianten-spezifischen
Eigenschaft (hier: `schwachstellen_motor`) unterscheiden?

  A) Legacy-Pfad (`find_motor` ohne `req=`): nachweisbar reihenfolgeabhängig
  B) Sicherer Pfad (`find_motor` MIT `req=`, der Pfad von app/kaufcheck.py UND
     jetzt auch app/verkaufscheck.py): liefert bei echter Mehrdeutigkeit
     None statt eine Zeile zu raten — unabhängig von der Reihenfolge
  C) Eindeutiger Fall bleibt eindeutig (keine Überkorrektur)
  D) app/verkaufscheck.py ruft find_motor jetzt ebenfalls MIT `req=` auf
     (Code-Fakt, schließt die Lücke generisch statt punktuell)

    python test_motor_tie_residual.py
"""
from app.car_lookup import find_motor
from app.models import KaufCheckRequest

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def _motor(vid, bez, code, **kw):
    m = {"variante_id": vid, "bezeichnung": bez, "motorcode": code, "kraftstoff": "Benzin",
         "leistung_ps": 150, "leistung_kw": 110, "getriebe": '["Automatik"]', "antrieb": "Front",
         "schwachstellen_motor": [{"id": vid, "bauteil": f"Bauteil {vid}", "schweregrad": "hoch"}]}
    m.update(kw)
    return m


def _baureihe(order: str) -> dict:
    """Zwei Motorvarianten, GLEICHE Leistung/Kraftstoff/Antrieb/Getriebe, aber
    unterschiedliche (synthetische) Bezeichnung/Motorcode/Schwachstelle —
    keine real existierende Baureihe, keine Marke mit fachlicher Bedeutung."""
    varianten = {
        "a": _motor("a", "Testmarke Alpha Sport", "QX1A"),
        "b": _motor("b", "Testmarke Alpha Comfort", "QX1B"),
    }
    return {"id": "testmarke-alpha-t1", "marke": "Testmarke", "modell": "Alpha",
           "generation": "T1", "motoren": [varianten[k] for k in order]}


# Freitext nennt NUR die Leistung — kein Modellname, kein Antrieb, kein
# Motorcode, der eine der beiden Varianten unterscheiden würde. Genau der
# Zustand, der laut Kommentar in app/car_lookup.py::find_motor zur
# Mehrdeutigkeit führt (Leistungstreffer, danach `_eingrenzen`).
REQ = KaufCheckRequest(marke="Testmarke", modell="Alpha", baujahr=2019,
                       motor="2.0 Turbo 150 PS", leistung_ps=150)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== A) Legacy-Pfad (ohne req=): reihenfolgeabhängig ===")
m_ab = find_motor(_baureihe("ab"), REQ.motor, REQ.modell)
m_ba = find_motor(_baureihe("ba"), REQ.motor, REQ.modell)
check("A1 Reihenfolge 'ab' liefert einen Treffer (Leistungstreffer existiert)",
      m_ab is not None)
check("A2 Reihenfolge 'ba' liefert einen Treffer (Leistungstreffer existiert)",
      m_ba is not None)
check("A3 BEWEIS: dieselbe Evidenz, unterschiedliche DB-Reihenfolge -> "
      "unterschiedlicher Treffer (genau das reale Risiko, das der Auftrag vermutet)",
      m_ab is not None and m_ba is not None and m_ab["variante_id"] != m_ba["variante_id"])
check("A4 Die beiden Treffer tragen EXKLUSIVE, unterschiedliche Schwachstellen "
      "(eine falsche Zuordnung landet wirklich im Bericht, nicht nur im internen Feld)",
      m_ab is not None and m_ba is not None
      and m_ab["schwachstellen_motor"][0]["bauteil"] != m_ba["schwachstellen_motor"][0]["bauteil"])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== B) Sicherer Pfad (mit req=): Mehrdeutigkeit bleibt Mehrdeutigkeit ===")
m_safe_ab = find_motor(_baureihe("ab"), REQ.motor, REQ.modell, req=REQ)
m_safe_ba = find_motor(_baureihe("ba"), REQ.motor, REQ.modell, req=REQ)
check("B1 Reihenfolge 'ab' -> KEIN Rateentscheid (None statt Zeile 1)",
      m_safe_ab is None)
check("B2 Reihenfolge 'ba' -> KEIN Rateentscheid (None statt Zeile 1)",
      m_safe_ba is None)
check("B3 INVARIANTE ERFÜLLT: dieselbe Evidenz liefert dasselbe (nicht-)Ergebnis, "
      "unabhängig von der DB-/Listenreihenfolge",
      m_safe_ab == m_safe_ba is None)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== C) Eindeutiger Fall bleibt eindeutig (keine Überkorrektur) ===")
REQ_EINDEUTIG = KaufCheckRequest(marke="Testmarke", modell="Alpha", baujahr=2019,
                                 motor="Testmarke Alpha Sport", leistung_ps=150)
m_eind_ab = find_motor(_baureihe("ab"), REQ_EINDEUTIG.motor, REQ_EINDEUTIG.modell, req=REQ_EINDEUTIG)
m_eind_ba = find_motor(_baureihe("ba"), REQ_EINDEUTIG.motor, REQ_EINDEUTIG.modell, req=REQ_EINDEUTIG)
check("C1 Freitext nennt die Bezeichnung explizit -> eindeutiger Treffer, nicht None",
      m_eind_ab is not None and m_eind_ab["variante_id"] == "a")
check("C2 Dasselbe Ergebnis unabhängig von der DB-Reihenfolge (kein Reihenfolge-Einfluss "
      "bei echter Eindeutigkeit)",
      m_eind_ab is not None and m_eind_ba is not None
      and m_eind_ab["variante_id"] == m_eind_ba["variante_id"] == "a")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== D) app/verkaufscheck.py nutzt jetzt denselben sicheren Pfad ===")
import inspect
import app.verkaufscheck as _vk_mod
quelltext = inspect.getsource(_vk_mod)
check("D1 motor_markt-Aufruf übergibt jetzt req= (schließt den Legacy-Rückfall "
      "generisch, nicht fahrzeugspezifisch)",
      "find_motor(baureihe_markt, req.motor, req.modell, req=req)" in quelltext)
check("D2 der alte, ungesicherte Aufruf ohne req= steht nicht mehr im Code",
      "find_motor(baureihe_markt, req.motor) if baureihe_markt" not in quelltext)


print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE MOTOR-TIE-RESIDUAL-TESTS GRUEN")
