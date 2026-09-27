"""
BATCH C — KBA-Paar-Closing: Zusicherungen fuer den Import der durch den
historischen Rueckruf-Import-Bug verlorenen, fuer sich sicheren
(Rueckruf, Baureihe)-Paare mit GESCHLOSSENER Zielgeneration.

KEIN Netzwerk, KEIN LLM-Call, KEINE Tavily-Calls, KEINE DB-Mutation (Abschnitt
G liest nur, und nur wenn eine lokale DB existiert).

  A) Determinismus
  B) Nur GESCHLOSSENE Zielgenerationen, nur fuer sich sichere, verlorene Paare
  C) Tor C1 — versteckte zweite Generation (paarweise, nicht rueckrufweit)
  D) Tor C2 — amtliche Eingrenzung, die der Dry-Run selbst nicht erkennt
  E) Tor C3 — Dublette gegen den aktuellen Bestand (exakter Textabgleich)
  F) Die kuratierten Zeilen sind in sich konsistent
  G) Bestand und Verifikation (nur lesend, nur mit lokaler DB)
  H) F82-Kontrolle (Regressionsfall, der die Luecke aufgedeckt hat)
  I) Nicht-BMW-Kontrolle
  J) FIN-Safety

    python test_kba_batch_c.py
"""
import os
import sqlite3
import sys

from app.kba_batch_c_daten import AUSSCHLUESSE, ZEILEN, zeilen_ids
from app.kba_import_batch_a import SAMMELSTEMPEL
from app.kba_import_batch_c import ID_BASIS_C, alternative_fuer_ziel, pruefe_batch_c, ziel_index
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
    auftaucht (siehe Modul-Docstring: Grund (b))."""
    z = {
        "KBA-Referenznummer": "9001", "Rückrufcode des Herstellers": "ABC",
        "Veröffentlichungsdatum": "2020-05-01", "Marke": "OPEL",
        "Modell": "INSIGNIA",
        "Mangelbezeichnung": _FOLGE_MANGEL,
        "Produktionszeitraum von": "2018", "Produktionszeitraum bis": "2020",
        "Beschreibung der Maßnahme": "Software-Update.",
        "Mögliche Eingrenzung der betroffenen Modelle": "N/A",
        "Überwachung der Rückrufaktion durch das KBA": "überwacht",
    }
    z.update(kw)
    return z


def baureihe(bid, marke, modell, von, bis) -> dict:
    return {"id": bid, "marke": marke, "modell": modell, "generation": bid,
            "bauzeitraum_von": von, "bauzeitraum_bis": bis}


def rr(**kw) -> dict:
    r = {"id": 1, "baureihe_id": "opel-insignia-b", "datum": "2020-05",
         "betroffene_baujahre": "2018-2020", "mangel": "", "abhilfe": "",
         "kba_referenz": None}
    r.update(kw)
    return r


# ══════════════════════════════════════════════════════════════════════════════
print("=" * 60)
print("A) DETERMINISMUS")
print("=" * 60)

_brs = [baureihe("opel-insignia-b", "Opel", "Insignia", 2017, 2022)]
_kba = [kba_zeile()]
_z1, _a1, _o1 = pruefe_batch_c(_kba, [], _brs)
_z2, _a2, _o2 = pruefe_batch_c(_kba, [], _brs)
check("A1 gleiche Eingabe -> gleiche Zeilen", _z1 == _z2, f"{len(_z1)} Zeilen")
check("A2 gleiche Eingabe -> gleiche Ausschluesse", _a1 == _a2)
check("A3 gleiche Eingabe -> gleiche offenen Paare", _o1 == _o2)
check("A4 IDs starten bei ID_BASIS_C und sind fortlaufend",
      [z["id"] for z in _z1] == list(range(ID_BASIS_C, ID_BASIS_C + len(_z1))))
check("A5 ein einzelner, eindeutiger, nicht ueberwachter Fall braucht keine "
      "Ueberwachung, um verworfen zu werden",
      pruefe_batch_c([kba_zeile(**{"Überwachung der Rückrufaktion durch das KBA":
                                   "nicht überwacht"})], [], _brs)[0] == [])


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("B) NUR GESCHLOSSENE ZIELGENERATIONEN, NUR VERLORENE PAARE")
print("=" * 60)

_offen = [baureihe("opel-insignia-b", "Opel", "Insignia", 2017, None)]
_zo, _ao, _oo = pruefe_batch_c([kba_zeile()], [], _offen)
check("B1 offene Zielgeneration erzeugt keine Importzeile", _zo == [])
check("B2 sondern landet in den offenen Paaren", len(_oo) == 1, f"{_oo}")

# Ein Kandidat, der schon auf Dry-Run-Ebene eindeutig SAFE_IMPORT ist (kein
# Bug betroffen ihn), darf NICHT auftauchen: Batch C schliesst nur die
# nachweislich VERLORENEN Paare, keine neue Grundgesamtheit.
_bauteil_mangel = kba_zeile(Mangelbezeichnung="Die Lenkspindel kann brechen.",
                           **{"Beschreibung der Maßnahme": "Austausch der Lenkspindel."})
_z_plain, _, _ = pruefe_batch_c([_bauteil_mangel], [], _brs)
check("B3 ein bereits ueber ein Bauteil sicherheitsrelevanter, eindeutiger "
      "Kandidat gehoert NICHT zu den verlorenen Paaren",
      _z_plain == [])


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("C) TOR C1 — VERSTECKTE ZWEITE GENERATION (JE PAAR)")
print("=" * 60)

# Zwei Generationen; das amtliche Fenster deckt den Gewinner voll, die
# Alternative zur Haelfte — analog zum bereits dokumentierten S-Klasse-Fall
# in app/kba_import_batch_a.py, hier aber ueber die Sicherheitsfolge
# gefunden (Batch A haette diesen Fall nie gesehen, weil er dort schon vor
# der Verlust-Pruefung ausgeschlossen worden waere).
_zwei = [baureihe("mercedes-benz-s-klasse-w222", "Mercedes-Benz", "S-Klasse", 2013, 2020),
         baureihe("mercedes-benz-s-klasse-w223", "Mercedes-Benz", "S-Klasse", 2020, 2026)]
_kba_c1 = kba_zeile(Marke="MERCEDES-BENZ", Modell="S-KLASSE",
                    **{"Produktionszeitraum von": "2018",
                       "Produktionszeitraum bis": "2021"})
_kand_c1 = import_kandidaten([_kba_c1], [], _zwei)
check("C0 nur EIN Ziel wird als SAFE_IMPORT-Paar erreicht (W223 faellt schon "
      "im Dry-Run durch die Ueberdeckungsregel)",
      _kand_c1 and {b: kl for b, kl, _g in _kand_c1[0].paare} ==
      {"mercedes-benz-s-klasse-w222": "SAFE_IMPORT"},
      str(_kand_c1[0].paare) if _kand_c1 else "kein Kandidat")

_alt = alternative_fuer_ziel(_kand_c1[0], ziel_index(_zwei), "mercedes-benz-s-klasse-w222")
check("C1 die versteckte zweite Generation wird trotzdem gefunden", _alt is not None,
      f"{_alt}")

_zc, _ac, _ = pruefe_batch_c([_kba_c1], [], _zwei)
check("C2 das Paar wird verworfen, nicht importiert", _zc == [])
check("C3 der Ausschlussgrund benennt C1",
      len(_ac) == 1 and _ac[0][2].startswith("C1"), _ac[0] if _ac else "")

# Reine Randberuehrung darf NICHT blockieren (analog Batch A / D4).
_rand = [baureihe("ford-focus-mk1", "Ford", "Focus", 1998, 2004),
         baureihe("ford-focus-mk2", "Ford", "Focus", 2004, 2011)]
_kba_rand = kba_zeile(Marke="FORD", Modell="FOCUS",
                      **{"Produktionszeitraum von": "1999",
                         "Produktionszeitraum bis": "2004"})
_zr, _ar, _ = pruefe_batch_c([_kba_rand], [], _rand)
check("C4 reine Randberuehrung der Alternative blockiert nicht", len(_zr) == 1,
      f"{len(_zr)} Zeile(n)")


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("D) TOR C2 — AMTLICHE EINGRENZUNG (vom Dry-Run selbst nicht erkannt)")
print("=" * 60)

_EINGR = "Mögliche Eingrenzung der betroffenen Modelle"
for text, erwartet_zeile in (
        ("N/A", True), ("keine", True), ("-", True), ("", True),
        ("Es sind nur Rechtslenker-Fahrzeuge betroffen.", False),
        ("Grauimportierte Fahrzeuge aus den USA", False),
        ("Kriterien 46, 47", False)):
    _zd, _ad, _ = pruefe_batch_c([kba_zeile(**{_EINGR: text})], [], _brs)
    check(f"D {'uebernommen' if erwartet_zeile else 'verworfen'}: {text[:44]!r}",
          bool(_zd) == erwartet_zeile,
          "" if bool(_zd) == erwartet_zeile else (_ad[0][2] if _ad else "keine Begruendung"))


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("E) TOR C3 — DUBLETTE GEGEN DEN AKTUELLEN BESTAND")
print("=" * 60)

# Wortgleicher Mangeltext, aber OHNE gemeinsame Bauteilgruppe und ohne
# amtliche Referenz auf der vorhandenen Zeile: der unscharfe Dry-Run-Dublet-
# tentest (Bauteilgruppe + Zeitraum + Begriffe) greift hier NICHT, das exakte
# Textgate von Batch C schon.
_vorhandene_dublette = rr(mangel=_FOLGE_MANGEL, abhilfe="Software-Update.",
                          betroffene_baujahre="2018-2020", kba_referenz=None)
_z_dup, _a_dup, _ = pruefe_batch_c([kba_zeile()], [_vorhandene_dublette], _brs)
check("E1 der Dry-Run selbst haette dieses Paar noch als SAFE_IMPORT gesehen",
      bool(import_kandidaten([kba_zeile()], [_vorhandene_dublette], _brs)))
check("E2 Batch C verwirft es trotzdem als Dublette", _z_dup == [])
check("E3 der Ausschlussgrund benennt C3",
      len(_a_dup) == 1 and _a_dup[0][2].startswith("C3"), _a_dup[0] if _a_dup else "")


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("F) KONSISTENZ DER KURATIERTEN ZEILEN")
print("=" * 60)

check("F1 IDs eindeutig", len(zeilen_ids()) == len(ZEILEN), f"{len(ZEILEN)} Zeilen")
check("F2 IDs liegen oberhalb des gewachsenen Bestands und der Batches A/B1/"
      "Mixed-Target", min(zeilen_ids()) >= ID_BASIS_C)
check("F3 jede Zeile traegt eine Referenz und einen Mangeltext",
      all(z["kba_referenz"] and z["mangel"] for z in ZEILEN))
check("F4 kein Datum traegt den amtlichen Sammelstempel",
      not any((z["datum"] or "").startswith(SAMMELSTEMPEL) for z in ZEILEN))
check("F5 wo das Datum fehlt, steht der Sammelstempel im Rohwert",
      all(z["amtliches_datum"].startswith(SAMMELSTEMPEL)
          for z in ZEILEN if z["datum"] is None))
check("F6 je Baureihe hoechstens eine Zeile pro amtlicher Referenz",
      len({(z["baureihe_id"], z["kba_referenz"]) for z in ZEILEN}) == len(ZEILEN))
check("F7 jeder Ausschluss traegt eine benannte Begruendung (C1/C2/C3/C5)",
      all(g and g[0] == "C" for *_r, g in AUSSCHLUESSE), f"{len(AUSSCHLUESSE)} Ausschluesse")
check("F8 jede kuratierte Referenz besteht die Formatpruefung",
      all(kba_referenz_format_plausibel(z["kba_referenz"]) for z in ZEILEN))
check("F9 keine ID-Kollision mit Batch A/B1/Mixed-Target (2001-4035)",
      not (zeilen_ids() & set(range(2001, 4036))))


def _jahre(text):
    teile = [int(t) for t in str(text).replace("-", " ").split() if t.isdigit()]
    return (min(teile), max(teile)) if teile else (None, None)


_ok_verengung = True
for z in ZEILEN:
    von, bis = _jahre(z["betroffene_baujahre"])
    a_von, a_bis = _jahre(z["amtlicher_zeitraum"])
    if von < a_von or bis > a_bis:
        _ok_verengung = False
check("F10 betroffene_baujahre liegen IMMER im amtlichen Fenster (nur Verengung)",
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
        f"select * from rueckruf where id between {ID_BASIS_C} and "
        f"{ID_BASIS_C + len(ZEILEN)}")}
    if not _ist:
        print("[SKIP] Batch C ist auf dieser DB noch nicht migriert")
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
    _conn.close()


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("H) F82-KONTROLLE (der Fall, der die Luecke aufgedeckt hat)")
print("=" * 60)

_f82_referenzen = {z["kba_referenz"] for z in ZEILEN if z["baureihe_id"] == "bmw-m4-f82"}
check("H1 alle fuenf zuvor verlorenen F82-Paare sind jetzt im Batch",
      _f82_referenzen == {"14133R", "6404", "6676", "7289", "8902"}, _f82_referenzen)
check("H2 darunter der dokumentierte Fall 6404 (Hinterachstraeger-Verschraubung)",
      "6404" in _f82_referenzen)
check("H3 6404 ist nur ueber die Sicherheitsfolge sicherheitsrelevant, nicht "
      "ueber ein Bauteilwort — genau die Luecke aus Befund M",
      any("kritische" in (z["mangel"] or "").lower() and "fahrsituation" in (z["mangel"] or "").lower()
          for z in ZEILEN if z["kba_referenz"] == "6404"))


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("I) NICHT-BMW-KONTROLLE")
print("=" * 60)

_marken_prefixe = {z["baureihe_id"].split("-")[0] for z in ZEILEN}
check("I1 mindestens eine BMW-Baureihe", any(b.startswith("bmw-") for z in ZEILEN
                                             for b in [z["baureihe_id"]]))
check("I2 mindestens eine VAG-Baureihe (Audi/VW/Skoda/Seat)",
      any(z["baureihe_id"].split("-")[0] in ("audi", "volkswagen", "skoda", "seat")
          for z in ZEILEN))
check("I3 mindestens eine weitere Nicht-BMW-Marke (nicht BMW, nicht VAG)",
      any(z["baureihe_id"].split("-")[0] not in
          ("bmw", "audi", "volkswagen", "skoda", "seat") for z in ZEILEN))
check("I4 mindestens drei unterschiedliche Markenpraefixe insgesamt",
      len(_marken_prefixe) >= 3, _marken_prefixe)


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("J) FIN-SAFETY")
print("=" * 60)

check("J1 keine Zeile traegt eine individuelle FIN-Zuordnung (keine solche "
      "Spalte existiert in der Zieltabelle)",
      all("fin" not in z for z in ZEILEN))
check("J2 der Dry-Run sagt fuer jedes importierte Paar 'series_only' voraus, "
      "nie 'confirmed_by_vin'",
      all(k.applicability == "series_only"
          for z in ZEILEN
          for k in import_kandidaten([{"KBA-Referenznummer": z["kba_referenz"],
                                       "Marke": "BMW", "Modell": "M4",
                                       "Mangelbezeichnung": z["mangel"],
                                       "Produktionszeitraum von": "2014",
                                       "Produktionszeitraum bis": "2020",
                                       "Überwachung der Rückrufaktion durch das KBA":
                                           "überwacht"}], [],
                                     [baureihe("bmw-m4-f82", "BMW", "M4", 2014, 2020)])
          if z["baureihe_id"] == "bmw-m4-f82" and k.referenz == z["kba_referenz"]))


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE BATCH-C-TESTS GRUEN")
