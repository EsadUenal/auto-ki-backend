"""
BATCH A — Zusicherungen fuer den Import amtlicher Rueckrufe mit
GESCHLOSSENER Zielgeneration.

KEIN Netzwerk, KEIN LLM-Call, KEINE Tavily-Calls, KEINE DB-Mutation.

  A) Determinismus der Auswahl
  B) Klasse A enthaelt ausschliesslich geschlossene Zielgenerationen
  C) Tor A1 — zweites plausibles Generationsziel
  D) Tor A2 — amtliche Eingrenzung, und: NIE ein erfundener Antriebs-Qualifier
  E) Tor A4 — amtliches Referenzformat (auch die Form mit Kennbuchstaben)
  F) Die kuratierten Zeilen sind in sich konsistent
  G) Der Bestand traegt die Zeilen samt gueltiger Verifikation
  H) Floor-Safety: variant_match nur bei Baujahr-Deckung

    python test_kba_batch_a.py
"""
import os
import sqlite3
import sys

from app.kba_batch_a_daten import AUSSCHLUESSE, ZEILEN, zeilen_ids
from app.kba_import_batch_a import (
    ALTERNATIV_ANTEIL, ID_BASIS, SAMMELSTEMPEL, klasse_a, pruefe_batch_a,
    ziel_index, zweite_generation,
)
from app.kba_import_kandidaten import ImportKandidat, SAFE_IMPORT, klassifiziere_kandidat
from app.recall_filter import kba_referenz_format_plausibel

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, info: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f"   {info}" if info else ""))
    if not bedingung:
        _FEHLER.append(name)


def kba_zeile(**kw) -> dict:
    z = {
        "KBA-Referenznummer": "9001", "Rückrufcode des Herstellers": "ABC",
        "Veröffentlichungsdatum": "2020-05-01", "Marke": "OPEL",
        "Modell": "INSIGNIA",
        "Mangelbezeichnung": "Die Lenkspindel kann brechen.",
        "Produktionszeitraum von": "2018", "Produktionszeitraum bis": "2020",
        "Beschreibung der Maßnahme": "Austausch der Lenkspindel.",
        "Mögliche Eingrenzung der betroffenen Modelle": "N/A",
        "Überwachung der Rückrufaktion durch das KBA": "überwacht",
    }
    z.update(kw)
    return z


def baureihe(bid, marke, modell, von, bis) -> dict:
    return {"id": bid, "marke": marke, "modell": modell, "generation": bid,
            "bauzeitraum_von": von, "bauzeitraum_bis": bis}


def kandidat(zeile, baureihen, recalls=()) -> ImportKandidat:
    from app.kba_import_kandidaten import _ziel_index
    import collections
    je = collections.defaultdict(list)
    for r in recalls:
        je[r["baureihe_id"]].append(r)
    return klassifiziere_kandidat(ImportKandidat(zeile), _ziel_index(baureihen), je)


# ══════════════════════════════════════════════════════════════════════════════
print("=" * 60)
print("A) DETERMINISMUS")
print("=" * 60)

_brs = [baureihe("opel-insignia-b", "Opel", "Insignia", 2017, 2022)]
_k = [kandidat(kba_zeile(), _brs)]
_z1, _a1 = pruefe_batch_a(_k, _brs, [])
_z2, _a2 = pruefe_batch_a(_k, _brs, [])
check("A1 gleiche Eingabe -> gleiche Zeilen", _z1 == _z2, f"{len(_z1)} Zeilen")
check("A2 gleiche Eingabe -> gleiche Ausschluesse", _a1 == _a2)
check("A3 IDs starten bei ID_BASIS und sind fortlaufend",
      [z["id"] for z in _z1] == list(range(ID_BASIS, ID_BASIS + len(_z1))))


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("B) NUR GESCHLOSSENE ZIELGENERATIONEN")
print("=" * 60)

_offen = [baureihe("opel-insignia-b", "Opel", "Insignia", 2017, None)]
_ko = [kandidat(kba_zeile(), _offen)]
check("B1 SAFE_IMPORT auf offener Generation ist NICHT Klasse A",
      _ko[0].klasse == SAFE_IMPORT and klasse_a(_ko, _offen) == [],
      f"klasse={_ko[0].klasse}")
check("B2 dieselbe Zeile mit geschlossener Generation IST Klasse A",
      len(klasse_a(_k, _brs)) == 1)
_zeilen_offen, _ = pruefe_batch_a(_ko, _offen, [])
check("B3 offene Generation erzeugt keine Importzeile", _zeilen_offen == [])


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("C) TOR A1 — ZWEITES PLAUSIBLES GENERATIONSZIEL")
print("=" * 60)

# Zwei Generationen desselben Modells; die zweite deckt die Haelfte des
# amtlichen Fensters ab und ist damit ebenso plausibel.
_zwei = [baureihe("mercedes-benz-s-klasse-w222", "Mercedes-Benz", "S-Klasse", 2013, 2020),
         baureihe("mercedes-benz-s-klasse-w223", "Mercedes-Benz", "S-Klasse", 2020, 2026)]
_kz = kandidat(kba_zeile(**{"Marke": "MERCEDES-BENZ", "Modell": "S-KLASSE",
                            "Produktionszeitraum von": "2018",
                            "Produktionszeitraum bis": "2021"}), _zwei)
_alt = zweite_generation(_kz, ziel_index(_zwei))
check("C1 zweite Generation wird erkannt", _alt is not None,
      f"{_alt}")
_zc, _ac = pruefe_batch_a([_kz], _zwei, [])
check("C2 der Rueckruf wird verworfen", _zc == [])
check("C3 der Ausschlussgrund benennt A1",
      len(_ac) == 1 and _ac[0][3].startswith("A1"), _ac[0][3] if _ac else "")

# Reine Randberuehrung darf NICHT blockieren: die Alternative deckt nur ein
# Jahr von sechs ab (17 %), der Gewinner 100 %.
_rand = [baureihe("ford-focus-mk1", "Ford", "Focus", 1998, 2004),
         baureihe("ford-focus-mk2", "Ford", "Focus", 2004, 2011)]
_kr = kandidat(kba_zeile(**{"Marke": "FORD", "Modell": "FOCUS",
                            "Produktionszeitraum von": "1999",
                            "Produktionszeitraum bis": "2004"}), _rand)
_zr, _ar = pruefe_batch_a([_kr], _rand, [])
check("C4 Randberuehrung unterhalb des Schwellenanteils blockiert nicht",
      len(_zr) == 1, f"{len(_zr)} Zeile(n), Anteil={ALTERNATIV_ANTEIL}")


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("D) TOR A2 — AMTLICHE EINGRENZUNG")
print("=" * 60)

_EINGR = "Mögliche Eingrenzung der betroffenen Modelle"
for text, erwartet_zeile in (("N/A", True), ("keine", True), ("-", True), ("", True),
                             ("Es sind nur Rechtslenker-Fahrzeuge betroffen.", False),
                             ("Grauimportierte Fahrzeuge aus den USA", False),
                             ("A5, Q5, Q5 Hybrid, SQ5 TDI, SQ5 plus TDI", False),
                             ("Nur Modelle ohne Smart Cruise Control (SCC).", False)):
    _kd = kandidat(kba_zeile(**{_EINGR: text}), _brs)
    _zd, _ad = pruefe_batch_a([_kd], _brs, [])
    check(f"D {'uebernommen' if erwartet_zeile else 'verworfen'}: {text[:44]!r}",
          bool(_zd) == erwartet_zeile,
          "" if bool(_zd) == erwartet_zeile else (_ad[0][3] if _ad else "keine Begruendung"))

check("D9 keine Importzeile traegt einen Antriebs-Qualifier in Klammern",
      not any("(" in z["betroffene_baujahre"] for z in ZEILEN))


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("E) TOR A4 — AMTLICHES REFERENZFORMAT")
print("=" * 60)

check("E1 vierstellig amtlich", kba_referenz_format_plausibel("9541"))
check("E2 fuenfstellig amtlich", kba_referenz_format_plausibel("12223"))
check("E3 fuenfstellig mit Kennbuchstaben amtlich (15 % des Exports)",
      kba_referenz_format_plausibel("14004R"))
check("E4 Mercedes-Schreibweise bleibt gueltig",
      kba_referenz_format_plausibel("8A800000"))
check("E5 sequenzieller Platzhalter faellt weiterhin",
      not kba_referenz_format_plausibel("1234567"))
check("E6 sequenzieller Platzhalter mit Kennbuchstaben faellt ebenfalls",
      not kba_referenz_format_plausibel("123456R"))
check("E7 Freitext/Hex-Block faellt weiterhin",
      not kba_referenz_format_plausibel("0" * 64))
check("E8 mehrere Buchstaben am Ende sind kein amtliches Format",
      not kba_referenz_format_plausibel("14004RX"))
check("E9 jede kuratierte Referenz besteht die Formatpruefung",
      all(kba_referenz_format_plausibel(z["kba_referenz"]) for z in ZEILEN))

# Untermarke gegen Hersteller: eine amtliche Aktion ueber AMG GT UND E-Klasse
# ist KEINE markenuebergreifende Kollision.
from app.recall_filter import _hersteller  # noqa: E402

check("E10 Mercedes-AMG zaehlt als Mercedes-Benz",
      _hersteller("Mercedes-AMG") == _hersteller("Mercedes-Benz"),
      f"{_hersteller('Mercedes-AMG')}")
check("E11 verschiedene Hersteller bleiben verschieden",
      _hersteller("BMW") != _hersteller("Opel"))


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("F) KONSISTENZ DER KURATIERTEN ZEILEN")
print("=" * 60)

check("F1 IDs eindeutig", len(zeilen_ids()) == len(ZEILEN), f"{len(ZEILEN)} Zeilen")
check("F2 IDs liegen oberhalb des gewachsenen Bestands", min(zeilen_ids()) >= ID_BASIS)
check("F3 jede Zeile traegt eine Referenz und einen Mangeltext",
      all(z["kba_referenz"] and z["mangel"] for z in ZEILEN))
check("F4 kein Datum traegt den amtlichen Sammelstempel",
      not any((z["datum"] or "").startswith(SAMMELSTEMPEL) for z in ZEILEN))
check("F5 wo das Datum fehlt, steht der Sammelstempel im Rohwert",
      all(z["amtliches_datum"].startswith(SAMMELSTEMPEL)
          for z in ZEILEN if z["datum"] is None))


def _jahre(text):
    teile = [int(t) for t in str(text).replace("-", " ").split() if t.isdigit()]
    return (min(teile), max(teile)) if teile else (None, None)


_verengt = 0
_ok_verengung = True
for z in ZEILEN:
    von, bis = _jahre(z["betroffene_baujahre"])
    a_von, a_bis = _jahre(z["amtlicher_zeitraum"])
    if (von, bis) != (a_von, a_bis):
        _verengt += 1
    if von < a_von or bis > a_bis:
        _ok_verengung = False
check("F6 betroffene_baujahre liegen IMMER im amtlichen Fenster (nur Verengung)",
      _ok_verengung, f"{_verengt} Zeilen verengt")

check("F7 je Baureihe hoechstens eine Zeile pro amtlicher Referenz",
      len({(z["baureihe_id"], z["kba_referenz"]) for z in ZEILEN}) == len(ZEILEN))
check("F8 jeder Ausschluss traegt eine benannte Begruendung",
      all(g and g[0] == "A" for *_r, g in AUSSCHLUESSE), f"{len(AUSSCHLUESSE)} Ausschluesse")


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("G) BESTAND UND VERIFIKATION")
print("=" * 60)

from app.config import DB_PATH  # noqa: E402

if not os.path.exists(DB_PATH):
    print("[SKIP] keine Datenbank unter", DB_PATH)
else:
    from app.fakt_verifikation import fingerprint

    _conn = sqlite3.connect(DB_PATH)
    _conn.row_factory = sqlite3.Row
    _ist = {r["id"]: dict(r) for r in _conn.execute(
        f"select * from rueckruf where id between {ID_BASIS} and {ID_BASIS + len(ZEILEN)}")}
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
    check("G3 jede Zeile traegt eine Verifikation", not _fehlt,
          f"fehlend: {_fehlt[:5]}")
    check("G4 alle Verifikationen sind verified/Stufe A/Quelle KBA",
          all(_v[z["id"]]["status"] == "verified" and _v[z["id"]]["quelle_stufe"] == "A"
              and _v[z["id"]]["quelle"].startswith("KBA")
              for z in ZEILEN if z["id"] in _v))
    _stale = [z["id"] for z in ZEILEN if z["id"] in _v and z["id"] in _ist
              and _v[z["id"]]["fingerprint"] != fingerprint("rueckruf", _ist[z["id"]])]
    check("G5 kein Fingerprint ist stale", not _stale, f"stale: {_stale[:5]}")
    check("G6 jede Verifikation nennt die amtliche Referenz",
          all(z["kba_referenz"] in (_v[z["id"]]["referenz"] or "")
              for z in ZEILEN if z["id"] in _v))
    check("G7 jede Verifikation traegt den Quellenvermerk der Datenlizenz",
          all("dl-de" in (_v[z["id"]]["notiz"] or "").lower()
              or "by-2-0" in (_v[z["id"]]["notiz"] or "")
              for z in ZEILEN if z["id"] in _v))

    # ══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("H) FLOOR-SAFETY")
    print("=" * 60)

    from app.fakt_verifikation import annotiere
    from app.recall_filter import rueckruf_applicability

    _marke = {r[0]: r[1] for r in _conn.execute("select id, marke from baureihe")}
    _proben = [z for z in ZEILEN][:60]
    _rows = [dict(_ist[z["id"]]) for z in _proben if z["id"] in _ist]
    annotiere(_conn, "rueckruf", _rows)
    check("H1 Stichprobe umfasst mindestens 30 neue Rueckrufe", len(_rows) >= 30,
          f"n={len(_rows)}")
    check("H2 alle geprueften Zeilen tragen trust=verified",
          all(r.get("_trust") == "verified" for r in _rows))

    _mit, _ohne = [], []
    for r in _rows:
        m = _marke[r["baureihe_id"]]
        _mit.append(rueckruf_applicability(r, True, r.get("kba_referenz"), None, marke=m)[0])
        _ohne.append(rueckruf_applicability(r, False, r.get("kba_referenz"), None, marke=m)[0])
    check("H3 mit Baujahr-Deckung: nur variant_match oder unclear (Variantenbezug)",
          set(_mit) <= {"variant_match", "unclear"}, f"{sorted(set(_mit))}")
    check("H4 OHNE Baujahr-Deckung entsteht NIE variant_match",
          "variant_match" not in _ohne, f"{sorted(set(_ohne))}")
    check("H5 OHNE Baujahr-Deckung bleibt es bei series_only/unclear",
          set(_ohne) <= {"series_only", "unclear"}, f"{sorted(set(_ohne))}")

    # Gegenprobe: ohne Verifikation faellt dieselbe Zeile auf series_only zurueck
    _roh = [dict(_ist[z["id"]]) for z in _proben[:10] if z["id"] in _ist]
    _ohne_trust = [rueckruf_applicability(
        r, True, r.get("kba_referenz"), None, marke=_marke[r["baureihe_id"]])[0]
        for r in _roh]
    check("H6 ohne trust=verified kein variant_match (Trust-Gate wirkt)",
          "variant_match" not in _ohne_trust, f"{sorted(set(_ohne_trust))}")
    _conn.close()


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE BATCH-A-TESTS GRUEN")
