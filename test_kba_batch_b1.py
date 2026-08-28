"""
BATCH B1 — Zusicherungen fuer den Import amtlicher Rueckrufe mit OFFENER,
aber PRIMAERQUELLENBESTAETIGTER Zielgeneration.

KEIN Netzwerk, KEIN LLM-Call, KEINE Tavily-Calls, KEINE DB-Mutation.

  A) Herkunft: jede Zeile stammt aus einer primaerquellenbestaetigten Baureihe
  B) Die fuenf Batch-A-Tore gelten unveraendert weiter
  C) Konsistenz der kuratierten Zeilen
  D) Bestand und Verifikation
  E) Applicability und Floor

    python test_kba_batch_b1.py
"""
import os
import sqlite3
import sys

from app.kba_batch_a_daten import zeilen_ids as batch_a_ids
from app.kba_batch_b1_daten import AUSSCHLUESSE, ZEILEN, zeilen_ids
from app.kba_generation_quellen import PRIMAERQUELLEN
from app.kba_import_batch_a import ID_BASIS, SAMMELSTEMPEL
from app.kba_import_batch_b1 import ID_BASIS_B1
from app.recall_filter import kba_referenz_format_plausibel

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, info: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f"   {info}" if info else ""))
    if not bedingung:
        _FEHLER.append(name)


# ══════════════════════════════════════════════════════════════════════════════
print("=" * 60)
print("A) HERKUNFT")
print("=" * 60)

_ziele = {z["baureihe_id"] for z in ZEILEN}
check("A1 jede Zielbaureihe hat eine Hersteller-/Primaerquelle",
      _ziele <= set(PRIMAERQUELLEN), str(sorted(_ziele - set(PRIMAERQUELLEN))))
check("A2 jede Zeile nennt die Generationsquelle als URL",
      all(z["generationsquelle"].startswith("https://") for z in ZEILEN))
check("A3 jede Generationsquelle traegt Stufe 1-3",
      all(z["generationsstufe"] in (1, 2, 3) for z in ZEILEN))
check("A4 jede Zeile nennt einen Generationsbeleg",
      all(len(z["generationsbeleg"].strip()) > 30 for z in ZEILEN))
check("A5 die Zielbaureihen sind genau die primaerquellenbestaetigten",
      len(_ziele) == 13, f"{len(_ziele)} Baureihen")

from app.config import DB_PATH  # noqa: E402

if os.path.exists(DB_PATH):
    _c = sqlite3.connect(DB_PATH)
    _offen = {r[0] for r in _c.execute(
        "select id from baureihe where bauzeitraum_bis is null")}
    _c.close()
    check("A6 jede Zielbaureihe hat wirklich ein OFFENES Generationsende "
          "(sonst gehoerte sie zu Batch A)",
          _ziele <= _offen, str(sorted(_ziele - _offen)))


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("B) DIE FUENF BATCH-A-TORE")
print("=" * 60)

_gruende = [g for *_r, g in AUSSCHLUESSE]
check("B1 jeder Ausschluss traegt eine benannte Torbegruendung",
      all(g[:2] in ("A1", "A2", "A3", "A4", "A5") for g in _gruende),
      f"{len(AUSSCHLUESSE)} Ausschluesse")
check("B2 Tor A1 hat gegriffen (zweites plausibles Generationsziel)",
      any(g.startswith("A1") for g in _gruende))
check("B3 Tor A2 hat gegriffen (nicht abbildbare amtliche Eingrenzung)",
      any(g.startswith("A2") for g in _gruende))
check("B4 keine Importzeile traegt einen Antriebs-Qualifier in Klammern",
      not any("(" in z["betroffene_baujahre"] for z in ZEILEN))
check("B5 jede Referenz besteht die Formatpruefung",
      all(kba_referenz_format_plausibel(z["kba_referenz"]) for z in ZEILEN))
check("B6 je Baureihe hoechstens eine Zeile pro amtlicher Referenz",
      len({(z["baureihe_id"], z["kba_referenz"]) for z in ZEILEN}) == len(ZEILEN))


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("C) KONSISTENZ DER KURATIERTEN ZEILEN")
print("=" * 60)

_ids = zeilen_ids()
check("C1 IDs eindeutig", len(_ids) == len(ZEILEN), f"{len(ZEILEN)} Zeilen")
check("C2 IDs liegen im eigenen Block ab ID_BASIS_B1",
      min(_ids) >= ID_BASIS_B1 and ID_BASIS_B1 > ID_BASIS)
check("C3 kein ID-Ueberlapp mit Batch A", not (_ids & batch_a_ids()))
check("C4 jede Zeile traegt Referenz und Mangeltext",
      all(z["kba_referenz"] and z["mangel"] for z in ZEILEN))
check("C5 kein Datum traegt den amtlichen Sammelstempel",
      not any((z["datum"] or "").startswith(SAMMELSTEMPEL) for z in ZEILEN))


def _jahre(text):
    teile = [int(t) for t in str(text).replace("-", " ").split() if t.isdigit()]
    return (min(teile), max(teile)) if teile else (None, None)


_ok = True
_verengt = 0
for z in ZEILEN:
    von, bis = _jahre(z["betroffene_baujahre"])
    a_von, a_bis = _jahre(z["amtlicher_zeitraum"])
    if (von, bis) != (a_von, a_bis):
        _verengt += 1
    if von < a_von or bis > a_bis:
        _ok = False
check("C6 betroffene_baujahre liegen IMMER im amtlichen Fenster (nur Verengung)",
      _ok, f"{_verengt} Zeilen verengt")


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("D) BESTAND UND VERIFIKATION")
print("=" * 60)

if not os.path.exists(DB_PATH):
    print("[SKIP] keine Datenbank unter", DB_PATH)
else:
    from app.fakt_verifikation import fingerprint

    _conn = sqlite3.connect(DB_PATH)
    _conn.row_factory = sqlite3.Row
    _ist = {r["id"]: dict(r) for r in _conn.execute(
        f"select * from rueckruf where id between {ID_BASIS_B1} and "
        f"{ID_BASIS_B1 + len(ZEILEN)}")}
    check("D1 alle kuratierten Zeilen liegen im Bestand",
          len(_ist) == len(ZEILEN), f"{len(_ist)}/{len(ZEILEN)}")
    _abw = [z["id"] for z in ZEILEN if z["id"] in _ist and any(
        _ist[z["id"]][s] != z[s] for s in
        ("baureihe_id", "datum", "betroffene_baujahre", "mangel", "abhilfe",
         "kba_referenz"))]
    check("D2 Bestand stimmt Feld fuer Feld mit der kuratierten Datei ueberein",
          not _abw, f"abweichend: {_abw[:5]}")

    _v = {r["fakt_id"]: dict(r) for r in _conn.execute(
        "select * from fakt_verifikation where fakt_art='rueckruf'")}
    check("D3 jede Zeile traegt eine Verifikation",
          all(z["id"] in _v for z in ZEILEN))
    check("D4 alle Verifikationen sind verified/Stufe A/Quelle KBA",
          all(_v[z["id"]]["status"] == "verified"
              and _v[z["id"]]["quelle_stufe"] == "A"
              and _v[z["id"]]["quelle"].startswith("KBA")
              for z in ZEILEN if z["id"] in _v))
    _stale = [z["id"] for z in ZEILEN if z["id"] in _v and z["id"] in _ist
              and _v[z["id"]]["fingerprint"] != fingerprint("rueckruf", _ist[z["id"]])]
    check("D5 kein Fingerprint ist stale", not _stale, f"stale: {_stale[:5]}")
    check("D6 die KBA-Quelle des Fakts bleibt die amtliche Rueckrufdatenbank",
          all("kba-online.de" in (_v[z["id"]]["url"] or "")
              for z in ZEILEN if z["id"] in _v))
    check("D7 die Notiz fuehrt ZUSAETZLICH die Herstellerquelle der "
          "Generationsgrenze",
          all(z["generationsquelle"] in (_v[z["id"]]["notiz"] or "")
              for z in ZEILEN if z["id"] in _v))
    check("D8 die Notiz traegt weiterhin den Quellenvermerk der Datenlizenz",
          all("by-2-0" in (_v[z["id"]]["notiz"] or "")
              for z in ZEILEN if z["id"] in _v))

    # ══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("E) APPLICABILITY UND FLOOR")
    print("=" * 60)

    from app.empfehlungs_floor import RUECKRUF_WERKSTATT_APPLICABILITY
    from app.fakt_verifikation import annotiere
    from app.recall_filter import _HV_MUSTER, rueckruf_applicability

    _marke = {r[0]: r[1] for r in _conn.execute("select id, marke from baureihe")}
    _rows = [dict(_ist[z["id"]]) for z in ZEILEN if z["id"] in _ist]
    annotiere(_conn, "rueckruf", _rows)
    check("E1 alle Zeilen tragen trust=verified",
          all(r.get("_trust") == "verified" for r in _rows))

    _mit, _ohne, _verstoss = [], [], []
    for r in _rows:
        m = _marke[r["baureihe_id"]]
        a_mit = rueckruf_applicability(r, True, r.get("kba_referenz"), None, marke=m)[0]
        a_ohne = rueckruf_applicability(r, False, r.get("kba_referenz"), None, marke=m)[0]
        _mit.append(a_mit)
        _ohne.append(a_ohne)
        text = " ".join(filter(None, [r.get("mangel"), r.get("abhilfe"),
                                      r.get("betroffene_baujahre")]))
        bedingung = "(" in (r.get("betroffene_baujahre") or "") or bool(
            _HV_MUSTER.search(text))
        if a_mit == "variant_match" and not bedingung:
            _verstoss.append(r["id"])
    check("E2 mit Baujahr-Deckung entsteht KEIN variant_match ohne amtliche "
          "Variantenbedingung", not _verstoss, f"{_verstoss[:5]}")
    check("E3 ohne Baujahr-Deckung entsteht NIE variant_match",
          "variant_match" not in _ohne, f"{sorted(set(_ohne))}")
    check("E4 series_only loest keinen Werkstatt-Floor aus",
          "series_only" not in RUECKRUF_WERKSTATT_APPLICABILITY)
    check("E5 die Stichprobe umfasst mindestens 30 Zeilen", len(_rows) >= 30,
          f"n={len(_rows)}")
    _conn.close()


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE BATCH-B1-TESTS GRUEN")
