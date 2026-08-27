"""
RECALL-VERIFICATION-/CLEANUP-PILOT — Zusicherungen.
KEIN Netzwerk, KEIN LLM-Call, KEINE Tavily-Calls.

Geprueft werden die 14 `rueckruf`-Zeilen der vier KaufCheck-Pilotfahrzeuge und
die Regeln, die dieser Pilot eingefuehrt hat:

  A) Kuratierte Pilotdaten sind formal konsistent
  B) Jeder Pilot-Rueckruf traegt genau eine Verifikation, Fingerprint aktuell
  C) `unverified` verhaelt sich exakt wie ein fehlender Eintrag
  D) §9  — keine unbestaetigte KBA-Referenz mehr an den Pilotzeilen
  E) §9  — formatplausibel hebt NICHT mehr auf `variant_match` (Trust-Gate)
  F) §16 — kein Scope Creep: fremde Baureihen wurden NICHT mit angehoben
  G) §12 — Floor nur bei trust=verified UND variant_match
  H) §13 — Nutzerwortlaut je Vertrauensstufe, nie eine Schein-Amtlichkeit
  I) §14 — die vier echten Kaufchecks
  J) §11 — Applicability: Motorbezug wirkt jetzt in beide Richtungen
  K) §8  — keine Dubletten, keine geloeschten Zeilen
  L) Datenkorrekturen sind tatsaechlich in der DB angekommen

    python test_recall_pilot.py
"""
from app.car_lookup import find_motor
from app.database import get_baureihe, get_conn
from app.empfehlungs_floor import ermittle_floor, wende_floor_an
from app.evidence import build_insights
from app.fakt_verifikation import (
    STATUS_UNVERIFIED, STATUS_WERTE, fingerprint, ist_gesperrt, trust_des_fakts,
)
from app.kaufaktionen import build_kaufaktionen
from app.models import KaufCheckRequest
from app.recall_filter import referenz_ist_belegt, rueckruf_applicability
from app.recall_pilot_daten import (
    RECALL_KORREKTUREN, RECALL_VERIFIKATIONEN, _selbsttest,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    status = "OK  " if bedingung else "FAIL"
    print(f"[{status}] {name}")
    if not bedingung:
        _FEHLER.append(name)


PILOT_BAUREIHEN = ("bmw-3er-g20-g21", "opel-insignia-b", "audi-a3-typ-8p",
                   "mercedes-benz-c-klasse-w205")

# (marke, modell, generation, motor-hint, baujahr, kraftstoff)
PILOT_FAHRZEUGE = (
    ("BMW",           "3er",      "G20/G21", "320d",           2020, "Diesel"),
    ("Opel",          "Insignia", "B",       "2.0 Diesel",     2019, "Diesel"),
    ("Audi",          "A3",       "Typ 8P",  "2.0 FSI 150 PS", 2008, "Benzin"),
    ("Mercedes-Benz", "C-Klasse", "W205",    "C220d",          2016, "Diesel"),
)


def _check(marke, modell, gen, hint, baujahr, kraftstoff):
    """Ein vollstaendiger deterministischer Kaufcheck ueber den echten Pfad."""
    b = get_baureihe(marke, modell, gen)
    mm = find_motor(b, hint)
    req = KaufCheckRequest(marke=marke, modell=modell, baujahr=baujahr,
                           motor=hint, kraftstoff=kraftstoff,
                           kilometerstand=120000, preis_eur=20000)
    ins = build_insights(b, mm, [], req, check_typ="kauf")
    return b, mm, req, ins


def _rueckrufe(ins):
    return [i for i in ins if i.kategorie == "rueckruf"]


# ══ A) Kuratierte Pilotdaten ═════════════════════════════════════════════════
print("\n--- A) Kuratierte Pilotdaten ---")
try:
    _selbsttest()
    check("A1 Selbsttest der kuratierten Daten laeuft durch", True)
except AssertionError as exc:
    check(f"A1 Selbsttest der kuratierten Daten: {exc}", False)

check("A2 genau 14 Verifikationen (= alle Rueckrufe der vier Pilotfahrzeuge)",
      len(RECALL_VERIFIKATIONEN) == 14)
check("A3 13 Datenkorrekturen", len(RECALL_KORREKTUREN) == 13)
check("A4 ausschliesslich Faktenart 'rueckruf'",
      all(e[0] == "rueckruf" for e in RECALL_VERIFIKATIONEN))
check("A5 alle Korrekturen betreffen nur Pilot-Baureihen",
      all(e[1] in PILOT_BAUREIHEN for e in RECALL_KORREKTUREN))
check("A6 kein 'rejected' — nichts wurde positiv widerlegt",
      not any(e[2] == "rejected" for e in RECALL_VERIFIKATIONEN))
check("A7 genau ein Fakt mit amtlicher KBA-Referenz (011422)",
      [e[6] for e in RECALL_VERIFIKATIONEN].count("011422") == 1)


# ══ B) Verifikationen in der Datenbank ═══════════════════════════════════════
print("\n--- B) Verifikationen in der Datenbank ---")
with get_conn() as conn:
    _platzhalter = ",".join("?" * len(PILOT_BAUREIHEN))
    _pilot_rows = [dict(r) for r in conn.execute(
        f"SELECT id,baureihe_id,datum,betroffene_baujahre,mangel,abhilfe,kba_referenz "
        f"FROM rueckruf WHERE baureihe_id IN ({_platzhalter}) ORDER BY id",
        PILOT_BAUREIHEN)]
    _verifs = {r["fakt_id"]: dict(r) for r in conn.execute(
        "SELECT fakt_id,fingerprint,status,quelle,quelle_stufe,url,referenz,notiz "
        "FROM fakt_verifikation WHERE fakt_art='rueckruf'")}
    _gesamt_rueckrufe = conn.execute("SELECT COUNT(*) FROM rueckruf").fetchone()[0]

check("B1 14 Rueckrufzeilen an den vier Pilotfahrzeugen", len(_pilot_rows) == 14)
check("B2 jede Pilotzeile hat eine Verifikation",
      all(r["id"] in _verifs for r in _pilot_rows))
check("B3 alle Statuswerte sind bekannt",
      all(_verifs[r["id"]]["status"] in STATUS_WERTE for r in _pilot_rows))
check("B4 KEIN stale-verification-Eintrag (Fingerprint passt zum aktuellen Inhalt)",
      all(_verifs[r["id"]]["fingerprint"] == fingerprint("rueckruf", r)
          for r in _pilot_rows))

_verteilung = {}
for r in _pilot_rows:
    _verteilung[_verifs[r["id"]]["status"]] = _verteilung.get(
        _verifs[r["id"]]["status"], 0) + 1
print(f"       Verteilung: {_verteilung}")
check("B5 2 verified, 2 partially_verified, 10 unverified, 0 rejected",
      _verteilung == {"verified": 2, "partially_verified": 2, "unverified": 10})
check("B6 jede Verifikation nennt ihre durchsuchten Quellen",
      all(len(_verifs[r["id"]]["quelle"] or "") >= 20 for r in _pilot_rows))
check("B7 jede Verifikation traegt eine nachvollziehbare Notiz",
      all(len(_verifs[r["id"]]["notiz"] or "") >= 80 for r in _pilot_rows))


# ══ C) `unverified` verhaelt sich wie ein fehlender Eintrag ══════════════════
print("\n--- C) Semantik von 'unverified' ---")
_uv_zeile = next(r for r in _pilot_rows if _verifs[r["id"]]["status"] == STATUS_UNVERIFIED)
_uv = _verifs[_uv_zeile["id"]]
check("C1 'unverified' ergibt trust=unverified_db",
      trust_des_fakts(_uv, _uv_zeile, "rueckruf") == "unverified_db")
check("C2 'unverified' sperrt den Fakt NICHT (kein 'rejected')",
      ist_gesperrt(_uv, _uv_zeile, "rueckruf") is False)
check("C3 identisch zu 'gar keine Verifikation'",
      trust_des_fakts(None, _uv_zeile, "rueckruf")
      == trust_des_fakts(_uv, _uv_zeile, "rueckruf"))
check("C4 'unverified' traegt niemals eine amtliche Referenz",
      all(_verifs[r["id"]]["referenz"] is None for r in _pilot_rows
          if _verifs[r["id"]]["status"] == STATUS_UNVERIFIED))


# ══ D) §9 — keine unbestaetigte KBA-Referenz mehr ════════════════════════════
print("\n--- D) §9 KBA-Referenzen ---")
_mit_ref = [r for r in _pilot_rows if (r["kba_referenz"] or "").strip()]
check("D1 nur noch EINE Pilotzeile traegt eine KBA-Referenz", len(_mit_ref) == 1)
check("D2 und zwar die amtlich bestaetigte 011422 am Insignia-NOx-Rueckruf",
      len(_mit_ref) == 1 and _mit_ref[0]["id"] == 546
      and _mit_ref[0]["kba_referenz"] == "011422")
check("D3 die entfernten Nummern sind wirklich weg (009696/010000/9600/8789/...)",
      not any((r["kba_referenz"] or "") in
              ("009696", "010000", "010078", "7698", "7900", "8064", "9600",
               "10000", "8789", "9201", "9876") for r in _pilot_rows))
check("D4 jede Zeile MIT Referenz ist auch verified",
      all(_verifs[r["id"]]["status"] == "verified" for r in _mit_ref))


# ══ E) §9 — Trust-Gate auf der Referenz ══════════════════════════════════════
print("\n--- E) §9 formatplausibel != inhaltlich verified ---")
_roh = {"mangel": "Bremse", "abhilfe": "Tausch", "betroffene_baujahre": "2018"}
_ungeprueft = {**_roh}
_geprueft = {**_roh, "_trust": "verified"}
check("E1 referenz_ist_belegt: ohne _trust -> False", not referenz_ist_belegt(_ungeprueft))
check("E2 referenz_ist_belegt: mit _trust=verified -> True", referenz_ist_belegt(_geprueft))
check("E3 referenz_ist_belegt: partially_verified reicht NICHT",
      not referenz_ist_belegt({**_roh, "_trust": "partially_verified"}))
check("E4 referenz_ist_belegt: leeres Dict/None stuerzt nicht ab",
      not referenz_ist_belegt(None) and not referenz_ist_belegt({}))

_appl_un, _conf_un, _, _ = rueckruf_applicability(
    _ungeprueft, True, "011999", {"kraftstoff": "Diesel"}, marke="Opel")
_appl_ge, _conf_ge, _, _ = rueckruf_applicability(
    _geprueft, True, "011999", {"kraftstoff": "Diesel"}, marke="Opel")
check("E5 formatplausible Referenz OHNE Verifikation -> series_only/mittel",
      _appl_un == "series_only" and _conf_un == "mittel")
check("E6 dieselbe Referenz MIT Verifikation -> variant_match/hoch",
      _appl_ge == "variant_match" and _conf_ge == "hoch")
check("E7 Rueckruf bleibt in beiden Faellen vollstaendig sichtbar "
      "(kein Verwerfen, nur Herabstufung)",
      _appl_un != "incompatible" and _appl_ge != "incompatible")


# ══ F) §16 — kein Scope Creep auf fremde Baureihen ═══════════════════════════
print("\n--- F) §16 kein Scope Creep ---")
# Vor dem Cleanup kollidierten 7900 (Opel #544 <-> BMW 5er G30 #257) und 10000
# (Opel #547 <-> Mercedes W222 #147) markenuebergreifend. Nach dem Entfernen der
# Opel-Werte waeren beide fremden Referenzen "kollisionsfrei" — ohne das
# Trust-Gate aus E) haetten sie dadurch still variant_match/hoch erreicht.
with get_conn() as conn:
    _fremd = {r["id"]: dict(r) for r in conn.execute(
        "SELECT id,baureihe_id,datum,betroffene_baujahre,mangel,abhilfe,kba_referenz "
        "FROM rueckruf WHERE id IN (257,147)")}
check("F1 die beiden fremden Zeilen existieren unveraendert weiter",
      set(_fremd) == {257, 147})
check("F2 ihre Referenzen wurden NICHT angefasst",
      (_fremd[257]["kba_referenz"] or "").strip() == "7900"
      and (_fremd[147]["kba_referenz"] or "").strip() == "10000")
for _fid, _marke, _kraftst in ((257, "BMW", "Diesel"), (147, "Mercedes-Benz", "Diesel")):
    _a, _c, _, _ = rueckruf_applicability(
        _fremd[_fid], True, _fremd[_fid]["kba_referenz"],
        {"kraftstoff": _kraftst}, marke=_marke)
    check(f"F3 fremde Zeile #{_fid} bleibt series_only (nicht durch das Cleanup gehoben)",
          _a == "series_only")
check("F4 keine Verifikation an einer fremden Baureihe angelegt",
      not any(fid in _verifs for fid in (257, 147)))


# ══ G) §12 — Floor ═══════════════════════════════════════════════════════════
print("\n--- G) §12 Empfehlungs-Floor ---")
# Positivfall: Insignia B 1.6 CDTI, Baujahr 2018 — der amtlich belegte
# NOx-Rueckruf 011422 trifft Baujahr UND Kraftstoff.
_b_pos, _mm_pos, _req_pos, _ins_pos = _check(
    "Opel", "Insignia", "B", "1.6 CDTI 136 PS", 2018, "Diesel")
_nox = [i for i in _rueckrufe(_ins_pos) if "Abschalteinrichtung" in i.titel]
check("G1 der amtlich belegte Rueckruf erscheint", len(_nox) == 1)
check("G2 er traegt trust=verified", bool(_nox) and _nox[0].trust == "verified")
check("G3 und applicability=variant_match",
      bool(_nox) and _nox[0].applicability == "variant_match")
_floor_pos = ermittle_floor(_ins_pos)
check("G4 Floor greift", _floor_pos is not None
      and _floor_pos.stufe == "nur_mit_werkstattpruefung")
check("G5 Floor-Grund ist der Rueckruf-Variantentreffer",
      _floor_pos is not None and "rueckruf_variantentreffer" in _floor_pos.gruende)
check("G6 Floor belegt ueber die ECHTE Insight-ID dieses Checks",
      _floor_pos is not None and bool(_nox)
      and _nox[0].id in _floor_pos.evidence_ids)
check("G7 Floor hebt eine zu milde Empfehlung an",
      wende_floor_an("kaufen", _ins_pos)[0] == "nur_mit_werkstattpruefung")
check("G8 Floor senkt eine vorsichtigere Empfehlung NICHT",
      wende_floor_an("finger_weg", _ins_pos)[0] == "finger_weg")

# Negativfaelle
check("G9 partially_verified traegt keinen Floor",
      all(ermittle_floor([i]) is None for i in _rueckrufe(_ins_pos)
          if i.trust != "verified"))
for _m, _mo, _g, _h, _bj, _k in PILOT_FAHRZEUGE:
    _, _, _, _i = _check(_m, _mo, _g, _h, _bj, _k)
    _rr_floor = ermittle_floor(_rueckrufe(_i))
    check(f"G10 {_m} {_h} {_bj}: kein Rueckruf-Floor (kein verified Variantentreffer)",
          _rr_floor is None)


# ══ H) §13 — Nutzerwortlaut ══════════════════════════════════════════════════
print("\n--- H) §13 Nutzerwortlaut ---")
check("H1 verified MIT amtlicher Nummer -> 'KBA-Rückruf' + Nummer als Quelle",
      bool(_nox) and _nox[0].titel.startswith("KBA-Rückruf")
      and any(q.ref == "011422" and q.titel == "KBA-Rückrufdatenbank"
              for q in _nox[0].quellen))

# verified OHNE amtliche Nummer (BMW Hochvoltspeicher, NHTSA-belegt)
_b_hv, _mm_hv, _req_hv, _ins_hv = _check(
    "BMW", "3er", "G20/G21", "330e", 2020, "Plug-in-Hybrid")
_hv = [i for i in _rueckrufe(_ins_hv) if "Hochvoltspeicher" in i.titel]
check("H2 der NHTSA-belegte Rueckruf erscheint beim PHEV", len(_hv) == 1)
check("H3 er ist verified", bool(_hv) and _hv[0].trust == "verified")
check("H4 aber NICHT als 'KBA-Rückruf' betitelt (keine KBA-Nummer vorhanden)",
      bool(_hv) and not _hv[0].titel.startswith("KBA-Rückruf")
      and _hv[0].titel.startswith("Rückruf"))
check("H5 Quellentitel benennt die fehlende KBA-Referenz ausdruecklich",
      bool(_hv) and any("keine KBA-Referenz" in (q.titel or "") for q in _hv[0].quellen))
check("H6 verified ohne Nummer erreicht NICHT variant_match",
      bool(_hv) and _hv[0].applicability == "series_only")

# unverified -> nie eine amtlich klingende Nummer
_alle_unverified = []
for _m, _mo, _g, _h, _bj, _k in PILOT_FAHRZEUGE:
    _, _, _, _i = _check(_m, _mo, _g, _h, _bj, _k)
    _alle_unverified += [i for i in _rueckrufe(_i) if i.trust != "verified"]
check("H7 kein unverified Rueckruf zeigt eine Referenz",
      all(q.ref is None for i in _alle_unverified for q in i.quellen))
check("H8 jeder unverified Rueckruf heisst 'Rückrufhinweis'",
      all(i.titel.startswith("Rückrufhinweis") for i in _alle_unverified))
check("H9 jeder unverified Rueckruf nennt seine Quelle 'nicht amtlich bestätigt'",
      all(any("nicht amtlich bestätigt" in (q.titel or "") for q in i.quellen)
          for i in _alle_unverified))
check("H10 jeder sichtbare Rueckruf verweist auf die FIN-Pruefung",
      all("FIN" in (i.einfluss or "") for i in _alle_unverified))


# ══ I) §14 — die vier echten Kaufchecks ══════════════════════════════════════
print("\n--- I) §14 die vier Kaufchecks ---")
_ERWARTET = {
    # (marke, hint): (Anzahl sichtbarer Rueckruf-Insights, Floor erwartet)
    ("BMW", "320d"):           2,
    ("Opel", "2.0 Diesel"):    0,
    ("Audi", "2.0 FSI 150 PS"): 1,
    ("Mercedes-Benz", "C220d"): 1,
}
for _m, _mo, _g, _h, _bj, _k in PILOT_FAHRZEUGE:
    _b, _mm, _req, _i = _check(_m, _mo, _g, _h, _bj, _k)
    _rr = _rueckrufe(_i)
    check(f"I1 {_m} {_h} {_bj}: {_ERWARTET[(_m, _h)]} sichtbare Rueckruf-Insights",
          len(_rr) == _ERWARTET[(_m, _h)])
    check(f"I2 {_m} {_h} {_bj}: Motor wurde erkannt", _mm is not None)
    _akt = build_kaufaktionen(_req, _b, _mm, _i)
    _rueckruf_aktionen = [a for bereich in ("besichtigung", "probefahrt",
                                            "verkaeuferfragen", "dokumente")
                          for a in getattr(_akt, bereich).fahrzeugspezifisch
                          if "Rückruf" in (a.titel or "") + (a.aktion or "")]
    check(f"I3 {_m} {_h} {_bj}: Rueckruf-Aktionen genau dann, wenn Rueckrufe sichtbar",
          bool(_rueckruf_aktionen) == bool(_rr))
    check(f"I4 {_m} {_h} {_bj}: keine Kaufaktion nennt eine KBA-Nummer",
          not any(any(n in ((a.titel or "") + (a.aktion or ""))
                      for n in ("009696", "010000", "010078", "7698", "7900",
                                "8064", "9600", "10000", "8789", "9201", "9876"))
                  for bereich in ("besichtigung", "probefahrt", "verkaeuferfragen",
                                  "dokumente")
                  for a in getattr(_akt, bereich).fahrzeugspezifisch))

# Der Insignia-Testwagen (2.0 Diesel, 2019) liegt ausserhalb des amtlichen
# Fensters 2017-2018 — der NOx-Rueckruf darf ihn NICHT mehr betreffen.
_, _, _, _ins_ins = _check("Opel", "Insignia", "B", "2.0 Diesel", 2019, "Diesel")
check("I5 Insignia 2019: der NOx-Rueckruf ist korrekt NICHT mehr einschlaegig",
      not any("Abschalteinrichtung" in i.titel for i in _rueckrufe(_ins_ins)))


# ══ J) §11 — Applicability wirkt in beide Richtungen ═════════════════════════
print("\n--- J) §11 Motorbezug der Audi-Rueckrufe ---")
_, _, _, _ins_fsi = _check("Audi", "A3", "Typ 8P", "2.0 FSI 150 PS", 2008, "Benzin")
check("J1 Benziner 2008: der 2.0-TDI-Rueckruf ist ausgeblendet",
      not any("2.0 TDI" in i.titel for i in _rueckrufe(_ins_fsi)))
_, _, _, _ins_tdi = _check("Audi", "A3", "Typ 8P", "1.9 TDI", 2010, "Diesel")
check("J2 Diesel 2010: derselbe Rueckruf ist sichtbar",
      any("2.0 TDI" in i.titel for i in _rueckrufe(_ins_tdi)))
check("J3 Diesel 2010: der 1.4-TFSI-Rueckruf ist ausgeblendet",
      not any("1.4 TFSI" in i.titel for i in _rueckrufe(_ins_tdi)))
check("J4 keine neue Applicability-Kategorie erfunden",
      all(i.applicability in ("confirmed_by_vin", "variant_match", "series_only",
                              "unclear")
          for i in _rueckrufe(_ins_fsi) + _rueckrufe(_ins_tdi)))


# ══ K) §8 — keine Dubletten, keine Verluste ══════════════════════════════════
print("\n--- K) §8 Bestandsintegritaet ---")
check("K1 Rueckrufbestand insgesamt unveraendert (748 Zeilen)",
      _gesamt_rueckrufe == 748)
check("K2 keine Dublette an den Pilotfahrzeugen (14 Zeilen, 14 IDs)",
      len(_pilot_rows) == len({r["id"] for r in _pilot_rows}) == 14)
_paare = [(r["baureihe_id"], (r["mangel"] or "").strip()) for r in _pilot_rows]
check("K3 kein doppelter Mangeltext je Baureihe", len(_paare) == len(set(_paare)))
check("K4 keine Verifikation ohne zugehoerigen Fakt",
      all(any(r["id"] == fid for r in _pilot_rows)
          for fid in _verifs if fid in {e[1] for e in RECALL_VERIFIKATIONEN}))


# ══ L) Datenkorrekturen sind angekommen ══════════════════════════════════════
print("\n--- L) Datenkorrekturen ---")
_nach_id = {r["id"]: r for r in _pilot_rows}
check("L1 #546 Insignia NOx: Datum auf die KBA-Veroeffentlichung korrigiert",
      _nach_id[546]["datum"] == "2022-02")
check("L2 #546: Bauzeitraum auf die belegte Schnittmenge verengt",
      _nach_id[546]["betroffene_baujahre"] == "2017-2018 (1,6 l Diesel Euro 6)")
check("L3 #544 Bremspedal: Datum/Bauzeitraum auf die reale Aktion korrigiert",
      _nach_id[544]["datum"] == "2021-06"
      and _nach_id[544]["betroffene_baujahre"] == "2021")
check("L4 #13 Hochvoltspeicher: Bauzeitraum auf 2020 verengt",
      _nach_id[13]["betroffene_baujahre"] == "2020 (Plug-in-Hybrid)")
check("L5 #283/#284 Audi: Motorbezug maschinenlesbar",
      "(2.0 TDI Diesel)" in _nach_id[283]["betroffene_baujahre"]
      and "(1.4 TFSI Benzin)" in _nach_id[284]["betroffene_baujahre"])
check("L6 #12 BMW Lenkung: Mangeltext BEWUSST unveraendert "
      "(keine geratene Identitaet mit dem Spurstangen-Rueckruf)",
      _nach_id[12]["mangel"] == "Mangelhafte Schweißnähte an der Lenkung")
check("L7 #123 Mercedes eCall: Datum bewusst unveraendert",
      _nach_id[123]["datum"] == "2020-07")


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE RECALL-PILOT-TESTS GRUEN")
