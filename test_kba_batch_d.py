"""
BATCH D — KBA-Paar-Closing (offene Zielgeneration, herstellerquellen-
bestaetigt): Zusicherungen fuer die Fortsetzung von Batch B1 auf die neue,
paarweise Kandidatenmenge aus dem KaufCheck-Root-Cause-Closing.

KEIN Netzwerk, KEIN LLM-Call, KEINE Tavily-Calls, KEINE DB-Mutation (Abschnitt
G liest nur, und nur wenn eine lokale DB existiert).

  A) Determinismus
  B) Kaskade: Fachaudit -> Primaerquelle -> D-Tore
  C) Der dokumentierte BMW-iX3-Fall wird korrekt eingeordnet
  D) Ohne Herstellerquelle kein Import, auch bei bestaetigter Generation
  E) Bereits vorhandenes Paar erzeugt kein Duplikat
  F) Die kuratierten Zeilen sind in sich konsistent
  G) Bestand und Verifikation (nur lesend, nur mit lokaler DB)
  H) Nicht-BMW-Kontrolle (mindestens mehrere Marken)
  I) FIN-Safety

    python test_kba_batch_d.py
"""
import os
import sqlite3
import sys

from app.kba_batch_d_daten import REVIEW, ZEILEN, zeilen_ids
from app.kba_generation_audit import GENERATION_CONFIRMED, GENERATION_UNCLEAR
from app.kba_generation_quellen import SOURCE_CONFIRMED
from app.kba_import_batch_a import SAMMELSTEMPEL
from app.kba_import_batch_c import ID_BASIS_C
from app.kba_import_batch_d import (
    ID_BASIS_D, alle_offenen_entscheidungen, pruefe_batch_d,
)
from app.kba_import_kandidaten import import_kandidaten
from app.recall_filter import kba_referenz_format_plausibel

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, info: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f"   {info}" if info else ""))
    if not bedingung:
        _FEHLER.append(name)


_FOLGE_MANGEL = ("Ein Softwarefehler kann in bestimmten Situationen zu einem "
                 "Kontrollverlust über das Fahrzeug führen.")


def kba_zeile(**kw) -> dict:
    """Ein amtlicher Datensatz, der NUR ueber die Sicherheitsfolge (nicht ueber
    ein Bauteil) sicherheitsrelevant ist — damit der Kandidat unabhaengig von
    Ambiguitaet/Dublette ueberhaupt als 'verloren' in `verlorene_paare()`
    auftaucht (wie in test_kba_batch_c.py)."""
    z = {
        "KBA-Referenznummer": "9001", "Rückrufcode des Herstellers": "ABC",
        "Veröffentlichungsdatum": "2020-05-01", "Marke": "BMW",
        "Modell": "IX3",
        "Mangelbezeichnung": _FOLGE_MANGEL,
        "Produktionszeitraum von": "2020", "Produktionszeitraum bis": "2021",
        "Beschreibung der Maßnahme": "Software-Update.",
        "Mögliche Eingrenzung der betroffenen Modelle": "N/A",
        "Überwachung der Rückrufaktion durch das KBA": "überwacht",
    }
    z.update(kw)
    return z


def baureihe(bid, marke, modell, generation, von, bis=None) -> dict:
    return {"id": bid, "marke": marke, "modell": modell, "generation": generation,
            "bauzeitraum_von": von, "bauzeitraum_bis": bis}


_IX3 = [baureihe("bmw-ix3-g08", "BMW", "iX3", "G08", 2020)]


# ══════════════════════════════════════════════════════════════════════════════
print("=" * 60)
print("A) DETERMINISMUS")
print("=" * 60)

_kba = [kba_zeile()]
_z1, _r1 = pruefe_batch_d(_kba, [], _IX3)
_z2, _r2 = pruefe_batch_d(_kba, [], _IX3)
check("A1 gleiche Eingabe -> gleiche Zeilen", _z1 == _z2, f"{len(_z1)} Zeilen")
check("A2 gleiche Eingabe -> gleiches Review", _r1 == _r2)
check("A3 IDs starten bei ID_BASIS_D und sind fortlaufend",
      [z["id"] for z in _z1] == list(range(ID_BASIS_D, ID_BASIS_D + len(_z1))))
check("A4 ID-Bloecke von Batch C und Batch D ueberschneiden sich nie",
      ID_BASIS_D > ID_BASIS_C + 999)


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("B) KASKADE: FACHAUDIT -> PRIMAERQUELLE -> D-TORE")
print("=" * 60)

# BMW iX3 G08: Fachaudit UND Primaerquelle bestaetigen die Generation fuer ein
# Fenster, das lange vor dem 2025 begonnenen Nachfolgeanlauf liegt.
_z_ix3, _r_ix3 = pruefe_batch_d([kba_zeile()], [], _IX3)
check("B1 Fenster klar in der Generation -> Import", len(_z_ix3) == 1, f"{_z_ix3}")

# Eine Baureihe ohne jeden Audit-Eintrag bleibt Review, auch wenn alles
# andere passt.
_UNBEKANNT = [baureihe("bmw-erfundene-testbaureihe", "BMW", "Testmodell", "T1", 2020)]
_z_unb, _r_unb = pruefe_batch_d(
    [kba_zeile(Modell="TESTMODELL")], [], _UNBEKANNT)
check("B2 Baureihe ohne Fachaudit-Eintrag -> kein Import", _z_unb == [])
check("B3 ... sondern GENERATION_UNCLEAR im Review",
      _r_unb and _r_unb[0].get("fachaudit") == GENERATION_UNCLEAR
      or (_r_unb and "keine belastbare Quelle" in _r_unb[0]["grund"]), _r_unb)


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("C) DER DOKUMENTIERTE BMW-IX3-FALL")
print("=" * 60)

# Genau das im Code dokumentierte Risiko: ein Fenster, das erst NACH dem
# Nachfolgeanlauf (2025) beginnt, darf nicht dem G08 zugeschlagen werden.
_z_spaet, _r_spaet = pruefe_batch_d(
    [kba_zeile(**{"Produktionszeitraum von": "2025",
                 "Produktionszeitraum bis": "2026"})], [], _IX3)
check("C1 Fenster nach dem Nachfolgeanlauf 2025 -> kein Import auf G08",
      _z_spaet == [])
check("C2 ... landet im Review, nicht stillschweigend verworfen",
      len(_r_spaet) == 1, f"{_r_spaet}")


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("D) OHNE HERSTELLERQUELLE KEIN IMPORT")
print("=" * 60)

# Eine Baureihe, die im Fachaudit als GENERATION_CONFIRMED steht, aber KEINE
# Primaerquelle hat (z.B. bmw-x6-g06, nur ueber den X5-Zyklus indirekt
# hergeleitet), darf trotzdem nicht geschrieben werden.
_X6 = [baureihe("bmw-x6-g06", "BMW", "X6", "G06", 2019)]
_z_x6, _r_x6 = pruefe_batch_d(
    [kba_zeile(Modell="X6", **{"Produktionszeitraum von": "2020",
                               "Produktionszeitraum bis": "2021"})], [], _X6)
check("D1 Fachaudit bestaetigt, aber keine Herstellerquelle -> kein Import",
      _z_x6 == [], f"{_z_x6}")
check("D2 Grund verweist auf die fehlende Herstellerquelle",
      _r_x6 and "Hersteller" in _r_x6[0]["grund"], _r_x6)


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("E) BEREITS VORHANDENES PAAR ERZEUGT KEIN DUPLIKAT")
print("=" * 60)

_bestand_ix3 = [{"id": 1, "baureihe_id": "bmw-ix3-g08", "datum": "2020-05",
                "betroffene_baujahre": "2020-2021",
                "mangel": kba_zeile()["Mangelbezeichnung"],
                "abhilfe": "Verschraubung pruefen und ersetzen.",
                "kba_referenz": "9001"}]
_z_dup, _r_dup = pruefe_batch_d([kba_zeile()], _bestand_ix3, _IX3)
check("E1 gleiche Referenz und gleicher Mangel im Bestand -> kein Import",
      _z_dup == [])
check("E2 die bereits gedeckte Referenz wird gar nicht erst zum Kandidaten "
      "(fruehester Dublettenschutz in import_kandidaten selbst)",
      _r_dup == [], _r_dup)

# Eine ANDERE Referenz auf derselben Baureihe mit wortgleichem Mangel wird
# dagegen zum Kandidaten und muss am D3-Tor scheitern.
_z_dup2, _r_dup2 = pruefe_batch_d(
    [kba_zeile(**{"KBA-Referenznummer": "9099"})], _bestand_ix3, _IX3)
check("E3 andere Referenz, wortgleicher Mangel -> kein Import (D3)", _z_dup2 == [])
check("E4 Grund benennt D3 (Dublette)",
      _r_dup2 and "D3" in _r_dup2[0]["grund"], _r_dup2)


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("F) KONSISTENZ DER KURATIERTEN ZEILEN")
print("=" * 60)

check("F1 IDs eindeutig", len(zeilen_ids()) == len(ZEILEN), f"{len(ZEILEN)} Zeilen")
check("F2 IDs liegen oberhalb von Batch A/B1/Mixed-Target/Batch C",
      min(zeilen_ids()) >= ID_BASIS_D)
check("F3 keine Ueberschneidung mit dem Batch-C-Block (5001-5999)",
      not (zeilen_ids() & set(range(5001, 6000))))
check("F4 jede Zeile traegt eine Referenz, einen Mangeltext und einen Beleg",
      all(z["kba_referenz"] and z["mangel"] and z["generationsbeleg"] for z in ZEILEN))
check("F5 kein Datum traegt den amtlichen Sammelstempel",
      not any((z["datum"] or "").startswith(SAMMELSTEMPEL) for z in ZEILEN))
check("F6 je Baureihe hoechstens eine Zeile pro amtlicher Referenz",
      len({(z["baureihe_id"], z["kba_referenz"]) for z in ZEILEN}) == len(ZEILEN))
check("F7 jede kuratierte Referenz besteht die Formatpruefung",
      all(kba_referenz_format_plausibel(z["kba_referenz"]) for z in ZEILEN))
check("F8 jeder Beleg nennt eine Stufe und eine Quelle",
      all("Stufe" in z["generationsbeleg"] and "http" in z["generationsbeleg"]
          for z in ZEILEN))
check("F9 jeder Review-Eintrag traegt Referenz, Baureihe und einen Grund",
      all(r.get("referenz") and r.get("baureihe_id") and r.get("grund") for r in REVIEW))
check("F10 kein Review-Eintrag ist zugleich importiert",
      not ({(r["referenz"], r["baureihe_id"]) for r in REVIEW}
          & {(z["kba_referenz"], z["baureihe_id"]) for z in ZEILEN}))


def _jahre(text):
    teile = [int(t) for t in str(text).replace("-", " ").split() if t.isdigit()]
    return (min(teile), max(teile)) if teile else (None, None)


_ok_verengung = True
for z in ZEILEN:
    von, bis = _jahre(z["betroffene_baujahre"])
    a_von, a_bis = _jahre(z["amtlicher_zeitraum"])
    if von < a_von or bis > a_bis:
        _ok_verengung = False
check("F11 betroffene_baujahre liegen IMMER im amtlichen Fenster (nur Verengung)",
      _ok_verengung)


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("G) BESTAND UND VERIFIKATION (nur lesend)")
print("=" * 60)

from app.config import DB_PATH  # noqa: E402

if not os.path.exists(DB_PATH):
    print("[SKIP] keine Datenbank unter", DB_PATH)
else:
    from app.fakt_verifikation import fingerprint

    _conn = sqlite3.connect(DB_PATH)
    _conn.row_factory = sqlite3.Row
    _ist = {r["id"]: dict(r) for r in _conn.execute(
        f"select * from rueckruf where id between {ID_BASIS_D} and "
        f"{ID_BASIS_D + len(ZEILEN)}")}
    if not _ist:
        print("[SKIP] Batch D ist auf dieser DB noch nicht migriert")
    else:
        check("G1 alle kuratierten Zeilen liegen im Bestand",
              len(_ist) == len(ZEILEN), f"{len(_ist)}/{len(ZEILEN)}")
        _abw = [z["id"] for z in ZEILEN if z["id"] in _ist and any(
            _ist[z["id"]][s] != z[s] for s in
            ("baureihe_id", "datum", "betroffene_baujahre", "mangel", "abhilfe",
             "kba_referenz"))]
        check("G2 Bestand stimmt Feld fuer Feld mit der kuratierten Datei ueberein",
              not _abw, f"abweichend: {_abw[:5]}")
        _v = {r["fakt_id"]: dict(r) for r in _conn.execute(
            "select * from fakt_verifikation where fakt_art='rueckruf'")}
        _fehlt = [z["id"] for z in ZEILEN if z["id"] not in _v]
        check("G3 jede Zeile traegt eine Verifikation", not _fehlt, f"fehlend: {_fehlt[:5]}")
        check("G4 alle Verifikationen sind verified/Stufe A/Quelle KBA",
              all(_v[z["id"]]["status"] == "verified" and _v[z["id"]]["quelle_stufe"] == "A"
                  and _v[z["id"]]["quelle"].startswith("KBA")
                  for z in ZEILEN if z["id"] in _v))
        _stale = [z["id"] for z in ZEILEN if z["id"] in _v and z["id"] in _ist
                  and _v[z["id"]]["fingerprint"] != fingerprint("rueckruf", _ist[z["id"]])]
        check("G5 kein Fingerprint ist stale", not _stale, f"stale: {_stale[:5]}")
        check("G6 jede Verifikation nennt die Generationszuordnung als Beleg",
              all("Generationszuordnung" in (_v[z["id"]]["notiz"] or "")
                  for z in ZEILEN if z["id"] in _v))
    _conn.close()


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("H) NICHT-BMW-KONTROLLE")
print("=" * 60)

_marken_prefixe = {z["baureihe_id"].split("-")[0] for z in ZEILEN}
check("H1 mindestens eine BMW-Baureihe", any(z["baureihe_id"].startswith("bmw-") for z in ZEILEN))
check("H2 mindestens eine VAG-Baureihe (Audi/VW)",
      any(z["baureihe_id"].split("-")[0] in ("audi", "volkswagen") for z in ZEILEN))
check("H3 mindestens eine weitere Nicht-BMW-Marke (nicht BMW, nicht VAG)",
      any(z["baureihe_id"].split("-")[0] not in ("bmw", "audi", "volkswagen")
          for z in ZEILEN))
check("H4 mindestens drei unterschiedliche Markenpraefixe insgesamt",
      len(_marken_prefixe) >= 3, _marken_prefixe)


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("I) FIN-SAFETY")
print("=" * 60)

check("I1 keine Zeile traegt eine individuelle FIN-Zuordnung",
      all("fin" not in z for z in ZEILEN))
check("I2 der Dry-Run sagt fuer den dokumentierten iX3-Fall 'series_only' voraus",
      all(k.applicability == "series_only"
          for k in import_kandidaten([kba_zeile()], [], _IX3)))


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE BATCH-D-TESTS GRUEN")
