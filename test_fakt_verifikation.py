# -*- coding: utf-8 -*-
"""
VERIFICATION-PILOT — Einzelfakt-Verifikation und Trust-Isolation.
KEIN Netzwerk, KEIN LLM-Call, KEIN Tavily.

  A) Fingerprint: erkennt Inhaltsaenderungen, faellt fail-safe zurueck
  B) Statuslogik: nur `verified` ergibt trust=verified
  C) TRUST-ISOLATION (§3/§14) — der Kern des Pilots:
     Ein verifizierter Fakt zieht die unverifizierten derselben Baureihe und
     derselben Kategorie NICHT mit. Fuer jede der vier Faktenarten geprueft.
  D) Der unverifizierte Fakt bleibt trotzdem nutzbar: Hinweis und Kaufaktion
     entstehen weiter, nur der Floor nicht.
  E) Pilotdaten: Struktur, Quellenpflicht, Persistenz
  F) Kuratierung: kein Fakt ohne begruendete Notiz

    python test_fakt_verifikation.py
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, ".")

FEHLER: list[str] = []


def check(name, ok, info=""):
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}" + (f"   {info}" if info else ""))
    if not ok:
        FEHLER.append(name)


from app.evidence import build_insights                              # noqa: E402
from app.empfehlungs_floor import ermittle_floor                     # noqa: E402
from app.fakt_verifikation import (                                  # noqa: E402
    FAKT_ARTEN, QUELLENSTUFEN, STATUS_PARTIALLY, STATUS_REJECTED, STATUS_VERIFIED,
    fingerprint, trust_des_fakts,
)
from app.kaufaktionen import build_kaufaktionen                      # noqa: E402
from app.verifikation_pilot_daten import (                           # noqa: E402
    GEPRUEFT_AM, PILOT_VERIFIKATIONEN, zusammenfassung,
)

BEREICHE = ("besichtigung", "probefahrt", "verkaeuferfragen", "dokumente")


class Req:
    def __init__(self, **kw):
        for f in ("marke", "modell", "baujahr", "kilometerstand", "motor", "kraftstoff",
                  "preis_eur", "beschreibung", "freitext", "unfallfrei", "vorbesitzer",
                  "tuev_bis", "scheckheftgepflegt"):
            setattr(self, f, kw.get(f))
        self.ausstattung = []


def verifikation(status=STATUS_VERIFIED, fp="", fakt_id=1):
    return {"fakt_id": fakt_id, "fingerprint": fp, "status": status,
            "quelle": "Testquelle", "quelle_stufe": "B", "url": "https://example.test",
            "referenz": None, "geprueft_am": GEPRUEFT_AM, "notiz": "Testnotiz"}


# ══════════════════════════════════════════════════════════════════════════════
print("=== A) Fingerprint ===")
_sw = {"id": 1, "baureihe_id": "test-br", "bauteil": "Turbolader",
       "beschreibung": "Kann ausfallen.", "betroffene_baujahre": "2015-2018",
       "schweregrad": "hoch"}
_fp = fingerprint("schwachstelle_baureihe", _sw)
check("A1 Fingerprint ist deterministisch",
      _fp == fingerprint("schwachstelle_baureihe", dict(_sw)))
check("A2 anderer Inhalt -> anderer Fingerprint",
      _fp != fingerprint("schwachstelle_baureihe", {**_sw, "schweregrad": "gering"}))
check("A3 anderes Fahrzeug -> anderer Fingerprint (gleicher Text zaehlt nicht)",
      _fp != fingerprint("schwachstelle_baureihe", {**_sw, "baureihe_id": "andere-br"}))
check("A4 fehlende Spalte kollidiert nicht mit vollstaendigem Fakt",
      _fp != fingerprint("schwachstelle_baureihe", {"id": 1, "bauteil": "Turbolader"}))
check("A5 Faktenart geht in den Fingerprint ein",
      fingerprint("schwachstelle_motor", {"variante_id": "x", "bauteil": "a",
                                          "beschreibung": "b", "baujahre": None})
      != fingerprint("kritische_wartung", {"variante_id": "x", "bauteil": "a",
                                           "intervall": "b", "hinweis": None}))

print("\n=== B) Statuslogik ===")
check("B1 verified + passender Fingerprint -> verified",
      trust_des_fakts(verifikation(fp=_fp), _sw, "schwachstelle_baureihe") == "verified")
check("B2 verified, aber Inhalt geaendert -> faellt auf unverified_db zurueck",
      trust_des_fakts(verifikation(fp=_fp), {**_sw, "beschreibung": "anders"},
                      "schwachstelle_baureihe") == "unverified_db")
check("B3 partially_verified traegt KEIN verified",
      trust_des_fakts(verifikation(STATUS_PARTIALLY, fp=_fp), _sw,
                      "schwachstelle_baureihe") == "unverified_db")
check("B4 rejected traegt KEIN verified",
      trust_des_fakts(verifikation(STATUS_REJECTED, fp=_fp), _sw,
                      "schwachstelle_baureihe") == "unverified_db")
check("B5 gar keine Verifikation -> unverified_db",
      trust_des_fakts(None, _sw, "schwachstelle_baureihe") == "unverified_db")


# ══════════════════════════════════════════════════════════════════════════════
# C) TRUST-ISOLATION — der Kern. Pro Faktenart: EIN Fakt verified, ein zweiter
#    derselben Baureihe/Kategorie unverifiziert. Der zweite darf NICHT mitgezogen
#    werden.
# ══════════════════════════════════════════════════════════════════════════════
print("\n=== C) Trust-Isolation je Faktenart ===")


def _fakt(art, basis, verifiziert):
    """Fakt-Dict wie `get_baureihe` es liefert — inkl. `_trust`."""
    d = dict(basis)
    d["_trust"] = "verified" if verifiziert else "unverified_db"
    d["_verifikation"] = verifikation(fp=fingerprint(art, basis)) if verifiziert else None
    return d


SW_A = {"id": 101, "baureihe_id": "test-br", "bauteil": "Steuerkette",
        "beschreibung": "Kette kann sich laengen.", "betroffene_baujahre": "Alle",
        "schweregrad": "hoch"}
SW_B = {"id": 102, "baureihe_id": "test-br", "bauteil": "Wasserpumpe",
        "beschreibung": "Kann undicht werden.", "betroffene_baujahre": "Alle",
        "schweregrad": "hoch"}
MP_A = {"id": 201, "variante_id": "test-motor", "bauteil": "Injektoren",
        "beschreibung": "Koennen undicht werden.", "baujahre": None, "kosten_ca": "500"}
MP_B = {"id": 202, "variante_id": "test-motor", "bauteil": "Zuendspulen",
        "beschreibung": "Koennen ausfallen.", "baujahre": None, "kosten_ca": "200"}
RR_A = {"id": 301, "baureihe_id": "test-br", "datum": "2018-05",
        "betroffene_baujahre": "2017-2018", "mangel": "Bremsleitung undicht",
        "abhilfe": "Austausch", "kba_referenz": None}
RR_B = {"id": 302, "baureihe_id": "test-br", "datum": "2019-06",
        "betroffene_baujahre": "2017-2018", "mangel": "Softwarefehler Airbag",
        "abhilfe": "Update", "kba_referenz": None}
WA_A = {"id": 401, "variante_id": "test-motor", "bauteil": "Zahnriemen",
        "intervall": "120.000 km", "hinweis": "Unbedingt einhalten."}
WA_B = {"id": 402, "variante_id": "test-motor", "bauteil": "Zuendkerzen",
        "intervall": "60.000 km", "hinweis": None}


def baureihe(schwachstellen, rueckrufe):
    return {"id": "test-br", "marke": "TestMarke", "modell": "TestModell",
            "generation": "G1", "bauzeitraum_von": 2015, "bauzeitraum_bis": 2021,
            "karosserie": [], "tuev_maengelquote": None, "adac_pannenkennziffer": None,
            "ausstattungslinien": [], "motoren": [],
            "schwachstellen_baureihe": schwachstellen, "rueckrufe": rueckrufe}


def motor(motorprobleme, wartung):
    return {"variante_id": "test-motor", "bezeichnung": "2.0 Test", "motorcode": "T20",
            "kraftstoff": "Diesel", "zylinder": 4, "leistung_ps": 150, "leistung_kw": 110,
            "schwachstellen_motor": motorprobleme, "kritische_wartung": wartung}


_req = Req(baujahr=2018)

# -- Schwachstellen
_br = baureihe([_fakt("schwachstelle_baureihe", SW_A, True),
                _fakt("schwachstelle_baureihe", SW_B, False)], [])
_ins = build_insights(_br, motor([], []), [], _req, check_typ="kauf")
_sw_ins = {i.titel.split(" —")[0]: i for i in _ins if i.kategorie == "schwachstelle"}
check("C1 verifizierte Schwachstelle traegt trust=verified",
      _sw_ins["Steuerkette"].trust == "verified")
check("C2 unverifizierte Schwachstelle DERSELBEN Baureihe bleibt unverified_db",
      _sw_ins["Wasserpumpe"].trust == "unverified_db")
check("C3 nur die verifizierte Quelle nennt sich '(geprueft)'",
      "geprüft" in (_sw_ins["Steuerkette"].quellen[0].titel or "")
      and "geprüft" not in (_sw_ins["Wasserpumpe"].quellen[0].titel or ""))

# -- Motorprobleme
_ins_mp = build_insights(baureihe([], []),
                         motor([_fakt("schwachstelle_motor", MP_A, True),
                                _fakt("schwachstelle_motor", MP_B, False)], []),
                         [], _req, check_typ="kauf")
_mp = {i.titel.split(" (")[0]: i for i in _ins_mp if i.kategorie == "motorproblem"}
check("C4 verifiziertes Motorproblem traegt trust=verified",
      _mp["Injektoren"].trust == "verified")
check("C5 unverifiziertes Motorproblem desselben Motors bleibt unverified_db",
      _mp["Zuendspulen"].trust == "unverified_db")

# -- Rueckrufe
_ins_rr = build_insights(baureihe([], [_fakt("rueckruf", RR_A, True),
                                       _fakt("rueckruf", RR_B, False)]),
                         motor([], []), [], _req, check_typ="kauf")
_rr = [i for i in _ins_rr if i.kategorie == "rueckruf"]
_rr_v = [i for i in _rr if "Bremsleitung" in i.titel]
_rr_u = [i for i in _rr if "Airbag" in i.titel]
check("C6 verifizierter Rueckruf traegt trust=verified",
      bool(_rr_v) and _rr_v[0].trust == "verified")
check("C7 unverifizierter Rueckruf derselben Baureihe bleibt unverified_db",
      bool(_rr_u) and _rr_u[0].trust == "unverified_db")
check("C8 nur der verifizierte Rueckruf nennt sich KBA-Rueckruf",
      bool(_rr_v) and bool(_rr_u)
      and _rr_v[0].titel.startswith("KBA-Rückruf")
      and _rr_u[0].titel.startswith("Rückrufhinweis"))

# -- Wartung
_ins_wa = build_insights(baureihe([], []),
                         motor([], [_fakt("kritische_wartung", WA_A, True),
                                    _fakt("kritische_wartung", WA_B, False)]),
                         [], _req, check_typ="kauf")
_wa = {i.titel.split(" —")[0]: i for i in _ins_wa if i.kategorie == "wartung"}
check("C9 verifizierter Wartungspunkt traegt trust=verified",
      _wa["Zahnriemen"].trust == "verified")
check("C10 unverifizierter Wartungspunkt desselben Motors bleibt unverified_db",
      _wa["Zuendkerzen"].trust == "unverified_db")
check("C11 nur der verifizierte Eintrag heisst 'Vorgesehenes Intervall' (§10)",
      "Vorgesehenes Intervall" in _wa["Zahnriemen"].beschreibung
      and "Hinterlegter Wartungshinweis" in _wa["Zuendkerzen"].beschreibung)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== D) Der unverifizierte Fakt bleibt nutzbar, traegt aber keinen Floor ===")
_ka = build_kaufaktionen(_req, _br, motor([], []), _ins)
_akt = [a for b in BEREICHE for a in getattr(_ka, b).fahrzeugspezifisch]
_titel = " | ".join(a.titel for a in _akt)
check("D1 der UNverifizierte Fakt erzeugt weiterhin Kaufaktionen",
      "Wasserpumpe" in _titel, _titel[:90])
check("D2 der verifizierte Fakt ebenfalls", "Steuerkette" in _titel)

_floor = ermittle_floor(_ins)
check("D3 Floor greift ueber den VERIFIZIERTEN Fakt", _floor is not None)
_ids_v = {i.id for i in _ins if i.trust == "verified"}
check("D4 Floor nennt AUSSCHLIESSLICH verifizierte Evidence-IDs",
      _floor is not None and set(_floor.evidence_ids) <= _ids_v,
      str(_floor.evidence_ids if _floor else None))
_ids_u = {i.id for i in _ins if i.trust == "unverified_db"}
check("D5 der unverifizierte Fakt taucht NICHT in der Floor-Begruendung auf",
      _floor is not None and not (set(_floor.evidence_ids) & _ids_u))

# Gegenprobe: ohne den verifizierten Fakt darf trotz identischem Schweregrad
# kein Floor entstehen.
_br_nur_unver = baureihe([_fakt("schwachstelle_baureihe", SW_B, False)], [])
_ins_nur_unver = build_insights(_br_nur_unver, motor([], []), [], _req, check_typ="kauf")
check("D6 dieselbe Schwachstelle OHNE Verifikation loest keinen Floor aus",
      ermittle_floor(_ins_nur_unver) is None)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== E) Pilotdaten: Struktur und Persistenz ===")
_arten = {e[0] for e in PILOT_VERIFIKATIONEN}
check("E1 nur bekannte Faktenarten", _arten <= set(FAKT_ARTEN), str(sorted(_arten)))
check("E2 nur zulaessige Statuswerte",
      {e[2] for e in PILOT_VERIFIKATIONEN}
      <= {STATUS_VERIFIED, STATUS_PARTIALLY, STATUS_REJECTED})
check("E3 nur zulaessige Quellenstufen",
      {e[4] for e in PILOT_VERIFIKATIONEN} <= set(QUELLENSTUFEN))
check("E4 jeder Eintrag nennt eine Quelle",
      all((e[3] or "").strip() for e in PILOT_VERIFIKATIONEN))
check("E5 jeder Eintrag traegt eine Begruendung (§12: keine Auto-Verifikation)",
      all(len((e[7] or "").strip()) > 40 for e in PILOT_VERIFIKATIONEN))
check("E6 jeder VERIFIED-Eintrag nennt eine URL",
      all(e[5] for e in PILOT_VERIFIKATIONEN if e[2] == STATUS_VERIFIED))
_paare = [(e[0], e[1]) for e in PILOT_VERIFIKATIONEN]
check("E7 keine doppelte Fakt-Zuordnung", len(_paare) == len(set(_paare)))

# Persistenz: nach einem Neustart muss alles nachvollziehbar sein.
_LIVE = os.environ.get("AUTO_KI_DB_PATH") or os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "auto-ki-backend", "auto_ki.db")
if not os.path.exists(_LIVE):
    print("[SKIP] keine Datenbank vorhanden — Persistenzpruefung uebersprungen")
else:
    _c = sqlite3.connect(_LIVE)
    _hat = _c.execute("select 1 from sqlite_master where type='table' "
                      "and name='fakt_verifikation'").fetchone()
    if not _hat:
        print("[SKIP] fakt_verifikation noch nicht angelegt — Migration nicht gelaufen")
    else:
        _n = _c.execute("select count(*) from fakt_verifikation").fetchone()[0]
        check("E8 Verifikationen sind persistiert", _n >= len(PILOT_VERIFIKATIONEN),
              f"n={_n}")
        _ohne = _c.execute(
            "select count(*) from fakt_verifikation "
            "where quelle is null or trim(quelle)='' or geprueft_am is null "
            "or fingerprint is null or trim(fingerprint)=''").fetchone()[0]
        check("E9 keine persistierte Verifikation ohne Quelle, Datum und Fingerprint",
              _ohne == 0, f"n={_ohne}")
        _verified_ohne_url = _c.execute(
            "select count(*) from fakt_verifikation where status='verified' "
            "and (url is null or trim(url)='')").fetchone()[0]
        check("E10 kein VERIFIED ohne URL", _verified_ohne_url == 0)
    _c.close()


print("\n=== F) Kuratierungs-Bilanz ===")
_z = zusammenfassung()
print(f"    {_z}")
check("F1 mindestens ein Fakt wurde tatsaechlich verifiziert",
      _z.get(STATUS_VERIFIED, 0) > 0)
check("F2 der Pilot hat auch Nicht-Bestaetigungen festgehalten",
      _z.get(STATUS_PARTIALLY, 0) > 0)


print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER: " + ", ".join(FEHLER))
    raise SystemExit(1)
print("Alle Verifikations-Tests bestanden.")
