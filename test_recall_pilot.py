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
# SAFETY-CHECK VOR MERGE (§1): #546 (Insignia NOx) stand zunaechst auf
# `verified` mit kba_referenz="011422", zitierte dabei aber eine KBA-URL, die
# nachweislich eine ANDERE Aktion belegt. Eine echte Stufe-A-Quelle fuer diese
# Aktion war nicht auffindbar — nur uebereinstimmende Rechtsanwaltskanzlei-
# Seiten. Zurueckgestuft auf `partially_verified`; keine Pilotzeile traegt mehr
# eine gespeicherte KBA-Referenz.
# `referenz` in fakt_verifikation darf einen dokumentierten Herstellercode
# tragen (wie schon bei #544), auch wenn die DB-Spalte kba_referenz leer bleibt
# (§9) — das ist Nachvollziehbarkeit der Recherche, keine Anzeige. Geprueft wird
# hier ausschliesslich, dass "011422" selbst nirgends mehr als Referenz steht.
check("A7 keine Pilotzeile traegt mehr die Fehlzitation '011422' als Referenz",
      not any(e[6] == "011422" for e in RECALL_VERIFIKATIONEN))
check("A7b #546 ist nach dem Safety-Check partially_verified, nicht verified",
      next(e[2] for e in RECALL_VERIFIKATIONEN if e[1] == 546) == "partially_verified")


# ══ B) Verifikationen in der Datenbank ═══════════════════════════════════════
print("\n--- B) Verifikationen in der Datenbank ---")
with get_conn() as conn:
    _platzhalter = ",".join("?" * len(PILOT_BAUREIHEN))
    _alle_rows = [dict(r) for r in conn.execute(
        f"SELECT id,baureihe_id,datum,betroffene_baujahre,mangel,abhilfe,kba_referenz "
        f"FROM rueckruf WHERE baureihe_id IN ({_platzhalter}) ORDER BY id",
        PILOT_BAUREIHEN)]
    # BATCH A hat den Pilotfahrzeugen 20 weitere, amtlich belegte Rueckrufe
    # hinzugefuegt (app/kba_batch_a_daten.py). Dieser Abschnitt prueft
    # ausschliesslich den PILOTBESTAND — die 14 handgeprueften Zeilen plus den
    # Insignia-Nachtrag. Die Batch-A-Zeilen haben eigene Zusicherungen in
    # test_kba_batch_a.py; sie hier mitzuzaehlen wuerde die Pilotaussagen
    # verwaessern statt sie zu pruefen.
    from app.kba_batch_a_daten import zeilen_ids as _batch_a_ids
    _BATCH_A = _batch_a_ids()
    _pilot_rows = [r for r in _alle_rows if r["id"] not in _BATCH_A]
    _batch_a_rows = [r for r in _alle_rows if r["id"] in _BATCH_A]
    _verifs = {r["fakt_id"]: dict(r) for r in conn.execute(
        "SELECT fakt_id,fingerprint,status,quelle,quelle_stufe,url,referenz,notiz "
        "FROM fakt_verifikation WHERE fakt_art='rueckruf'")}
    _gesamt_rueckrufe = conn.execute("SELECT COUNT(*) FROM rueckruf").fetchone()[0]

# NACHTRAG (recall_insignia_012223_v1): +1 Zeile am Insignia B — der amtlich
# belegte Rueckruf KBA 12223. Der Pilotbestand selbst ist unveraendert.
check("B1 15 Rueckrufzeilen an den vier Pilotfahrzeugen (14 Pilot + 1 Nachtrag)",
      len(_pilot_rows) == 15)
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
# KBA-GESAMTABGLEICH: #13 (Hochvoltspeicher, jetzt amtlich 10176), #544
# (Bremspedal, 10743) und #546 (NOx, 11422) wurden gegen den amtlichen Export
# verifiziert und aus `partially_verified` hochgestuft. #123 (eCall) bleibt
# partially_verified. Der Pilot hatte diese drei nur deshalb nicht verifiziert,
# weil die KBA-Primaerquelle damals nicht erreichbar war.
check("B5 4 verified, 1 partially_verified, 10 unverified, 0 rejected",
      _verteilung == {"verified": 4, "partially_verified": 1, "unverified": 10})
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
# SAFETY-CHECK VOR MERGE (§1): #546 trug urspruenglich die einzige gespeicherte
# Referenz des Piloten (011422). Sie wurde entfernt, weil sich keine echte
# Stufe-A-Quelle dafuer auffinden liess — siehe Abschnitt A7/G. Damit traegt
# nach dem Safety-Check KEINE der 14 Pilotzeilen mehr eine KBA-Referenz.
_mit_ref = [r for r in _pilot_rows if (r["kba_referenz"] or "").strip()]
# NACHTRAG: genau EINE Zeile traegt wieder eine KBA-Referenz — die amtlich aus
# der KBA-Rueckrufdatenbank selbst gelesene 12223 des Nachtrags. Die 011422 des
# Piloten bleibt entfernt (nur Sekundaerquellen).
# KBA-GESAMTABGLEICH: vier Pilotzeilen tragen jetzt eine amtlich bestaetigte
# Referenz — jede davon direkt aus dem KBA-Gesamtexport gelesen.
check("D1 vier Pilotzeilen tragen eine amtlich bestaetigte KBA-Referenz",
      len(_mit_ref) == 4)
check("D1b und zwar genau 10176, 10743, 11422 und 12223",
      {r["kba_referenz"] for r in _mit_ref} == {"10176", "10743", "11422", "12223"})
check("D2 speziell 011422 ist NICHT gespeichert (Fehlzitation bleibt korrigiert)",
      not any((r["kba_referenz"] or "") == "011422" for r in _pilot_rows))
check("D3 die entfernten Nummern sind wirklich weg (009696/010000/9600/8789/...)",
      not any((r["kba_referenz"] or "") in
              ("009696", "010000", "010078", "7698", "7900", "8064", "9600",
               "10000", "8789", "9201", "9876") for r in _pilot_rows))
check("D4 jede Zeile MIT Referenz waere auch verified (aktuell vakuum wahr: "
      "keine Zeile hat eine Referenz)",
      all(_verifs[r["id"]]["status"] == "verified" for r in _mit_ref))


# ══ E) §9 — Trust-Gate auf der Referenz ══════════════════════════════════════
print("\n--- E) §9 formatplausibel != inhaltlich verified ---")
# FLOOR-SAFETY-AUDIT (Batch A): der Klammer-Qualifier ist noetig, damit dieser
# Abschnitt weiterhin das TRUST-Gate prueft und nicht versehentlich die neue
# Regel "ohne amtliche Variantenbedingung nie variant_match". Ohne ihn waeren
# beide Faelle series_only und der Unterschied waere nicht mehr sichtbar.
_roh = {"mangel": "Bremse", "abhilfe": "Tausch",
        "betroffene_baujahre": "2018 (Diesel)"}
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
# Gegenprobe zur neuen Semantik: derselbe verifizierte Rueckruf OHNE amtliche
# Antriebsbedingung bleibt series_only — die Verifikation hebt die Beleglage,
# nicht die Variantenreichweite.
_appl_ohne, _conf_ohne, _, _ = rueckruf_applicability(
    {**_geprueft, "betroffene_baujahre": "2018"}, True, "011999",
    {"kraftstoff": "Diesel"}, marke="Opel")
check("E6b verifiziert, aber OHNE Variantenbedingung -> series_only/hoch",
      _appl_ohne == "series_only" and _conf_ohne == "hoch")
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
# KBA-GESAMTABGLEICH: die beiden erfundenen Nummern sind inzwischen entfernt —
# nicht als Nebenwirkung des Piloten, sondern durch den bestandsweiten Abgleich,
# der 558 nicht amtlich bestaetigte Referenzen geloescht hat. Der urspruengliche
# Zweck dieser Pruefung bleibt: das Cleanup darf fremde Baureihen nicht
# ANHEBEN. Genau das sichert F3 weiterhin zu.
check("F2 ihre erfundenen Referenzen sind entfernt (Gesamtabgleich), "
      "die Rueckrufzeilen selbst bestehen weiter",
      (_fremd[257]["kba_referenz"] or "").strip() == ""
      and (_fremd[147]["kba_referenz"] or "").strip() == ""
      and _fremd[257]["mangel"] and _fremd[147]["mangel"])
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
# SAFETY-CHECK VOR MERGE (§1): #546 war der einzige Fall des Piloten, der
# trust=verified + applicability=variant_match erreichte, und damit der
# einzige, an dem der Floor je griff. Nach der Ruecksstufung auf
# partially_verified (keine echte Stufe-A-Quelle auffindbar) erreicht KEIN
# reales Pilotfahrzeug mehr diese Kombination — G1-G8 pruefen den Mechanismus
# deshalb ab hier SYNTHETISCH (eine selbst gebaute Baureihe, keine DB-Zeile),
# exakt nach dem Muster aus test_kba_trust.py. Das ist kein Rueckschritt: der
# Mechanismus selbst (referenz_ist_belegt, Applicability, Floor) ist unabhaengig
# von der Frage bewiesen, ob die vier Pilotfahrzeuge ihn gerade auslösen.
_syn_rr_verified = {
    "id": 9001, "baureihe_id": "syn-br", "datum": "2022-01",
    # Mit amtlicher Antriebsbedingung — nur so ist der Floor ueberhaupt
    # erreichbar (Floor-Safety-Audit). Der Gegenfall steht in G3b/G9c.
    "betroffene_baujahre": "2019-2021 (Diesel)",
    "mangel": "Synthetischer Testrueckruf fuer den Floor-Nachweis",
    "abhilfe": "Pruefung/Austausch", "kba_referenz": "445566",
    "_trust": "verified",
}
_syn_baureihe = {
    "id": "syn-br", "marke": "SynMarke", "modell": "SynModell", "generation": "G1",
    "karosserie": [], "ausstattungslinien": [], "motoren": [],
    "schwachstellen_baureihe": [], "rueckrufe": [_syn_rr_verified],
}
_syn_motor = {"variante_id": "syn-mo", "bezeichnung": "SynMotor", "kraftstoff": "Diesel",
             "schwachstellen_motor": [], "kritische_wartung": []}
_syn_req = KaufCheckRequest(marke="SynMarke", modell="SynModell", baujahr=2020,
                            motor="SynMotor", kraftstoff="Diesel")
_ins_syn = build_insights(_syn_baureihe, _syn_motor, [], _syn_req, check_typ="kauf")
_nox = _rueckrufe(_ins_syn)
check("G1 der verifizierte synthetische Rueckruf erscheint", len(_nox) == 1)
check("G2 er traegt trust=verified", bool(_nox) and _nox[0].trust == "verified")
check("G3 und applicability=variant_match (Baujahr+Kraftstoff passen, Referenz plausibel)",
      bool(_nox) and _nox[0].applicability == "variant_match")
_floor_pos = ermittle_floor(_ins_syn)
check("G4 Floor greift", _floor_pos is not None
      and _floor_pos.stufe == "nur_mit_werkstattpruefung")
check("G5 Floor-Grund ist der Rueckruf-Variantentreffer",
      _floor_pos is not None and "rueckruf_variantentreffer" in _floor_pos.gruende)
check("G6 Floor belegt ueber die ECHTE Insight-ID dieses Checks",
      _floor_pos is not None and bool(_nox)
      and _nox[0].id in _floor_pos.evidence_ids)
check("G7 Floor hebt eine zu milde Empfehlung an",
      wende_floor_an("kaufen", _ins_syn)[0] == "nur_mit_werkstattpruefung")
check("G8 Floor senkt eine vorsichtigere Empfehlung NICHT",
      wende_floor_an("finger_weg", _ins_syn)[0] == "finger_weg")
# FLOOR-SAFETY-AUDIT: derselbe verifizierte Rueckruf OHNE Antriebsbedingung
# bleibt series_only und traegt KEINEN Floor — auch wenn das Baujahr passt.
_syn_ohne = {**_syn_rr_verified, "id": 9002, "betroffene_baujahre": "2019-2021"}
_ins_syn_ohne = build_insights(
    {**_syn_baureihe, "rueckrufe": [_syn_ohne]}, _syn_motor, [], _syn_req,
    check_typ="kauf")
_rr_ohne = _rueckrufe(_ins_syn_ohne)
check("G8b ohne Antriebsbedingung: series_only trotz Baujahr-Treffer",
      bool(_rr_ohne) and _rr_ohne[0].applicability == "series_only")
check("G8c und damit KEIN Floor", ermittle_floor(_ins_syn_ohne) is None)
check("G8d der Rueckruf bleibt trotzdem vollstaendig sichtbar",
      bool(_rr_ohne) and _rr_ohne[0].trust == "verified"
      and _rr_ohne[0].confidence == "hoch")

# G3b/G4b: Gegenprobe (§3 des Safety-Checks) — identischer Rueckruf, aber
# unverifiziert -> series_only, kein Floor.
_syn_rr_unverified = {**_syn_rr_verified, "_trust": "unverified_db"}
_ins_syn_u = build_insights(
    {**_syn_baureihe, "rueckrufe": [_syn_rr_unverified]},
    _syn_motor, [], _syn_req, check_typ="kauf")
_nox_u = _rueckrufe(_ins_syn_u)
check("G3b identischer Rueckruf unverifiziert: applicability faellt auf series_only",
      bool(_nox_u) and _nox_u[0].applicability == "series_only")
check("G4b identischer Rueckruf unverifiziert: kein Floor",
      ermittle_floor(_ins_syn_u) is None)
check("G4c der Rueckruf selbst bleibt in beiden Faellen sichtbar (nur Stufe unterscheidet sich)",
      len(_nox) == len(_nox_u) == 1)

# Negativfaelle auf den echten Pilotdaten: KEIN reales Testfahrzeug erreicht
# nach der Korrektur noch trust=verified+variant_match — das ist das ehrliche
# Ergebnis dieses Safety-Checks, nicht ein Fehlschlag des Mechanismus.
_b_pos, _mm_pos, _req_pos, _ins_pos = _check(
    "Opel", "Insignia", "B", "1.6 CDTI 136 PS", 2018, "Diesel")
check("G9 partially_verified traegt keinen Floor "
      "(Insignia 1.6 CDTI 2018, realer Pilot-Fixture)",
      all(ermittle_floor([i]) is None for i in _rueckrufe(_ins_pos)
          if i.trust != "verified"))
# KBA-GESAMTABGLEICH: #546 ist nicht mehr zurueckgestuft — der amtliche Export
# bestaetigt Referenz 11422 samt Herstellercode und Motoreingrenzung. Er traegt
# jetzt trust=verified und damit auch wieder den Floor.
check("G9b #546 ist durch den amtlichen Export verifiziert",
      any("Abschalteinrichtung" in i.titel and i.trust == "verified"
          for i in _rueckrufe(_ins_pos)))
# NACHTRAG: der Insignia-Testwagen traegt jetzt als EINZIGES der vier
# Pilotfahrzeuge einen Floor — ausgeloest vom amtlich belegten Rueckruf
# KBA 12223. Die drei anderen bleiben unveraendert ohne Floor.
# BATCH A: der Mercedes W205 traegt jetzt ebenfalls einen Floor — ausgeloest von
# vier amtlich belegten KBA-Rueckrufen aus dem Import. BMW G20 und Audi 8P
# bleiben ohne Floor: ihre Rueckrufzeilen sind weiterhin unverifiziert.
# FLOOR-SAFETY-AUDIT (Batch A): KEIN Pilotfahrzeug traegt mehr einen
# Rueckruf-Floor. Weder KBA 12223 (Opel) noch die vier Mercedes-Rueckrufe nennen
# eine Motor-/Antriebsbedingung — sie gelten der ganzen Baureihe im gemeldeten
# Zeitraum. Der Floor davor stammte allein aus der Baujahr-Deckung und war damit
# eine Behauptung ueber die Variante, die die amtliche Quelle nicht deckt. Dass
# der Mechanismus selbst funktioniert, ist im synthetischen Abschnitt G
# (G4-G8) mit echter Antriebsbedingung nachgewiesen.
_FLOOR_ERWARTET = {}
for _m, _mo, _g, _h, _bj, _k in PILOT_FAHRZEUGE:
    _, _, _, _i = _check(_m, _mo, _g, _h, _bj, _k)
    _rr = _rueckrufe(_i)
    _rr_floor = ermittle_floor(_rr)
    _soll = _FLOOR_ERWARTET.get((_m, _h), False)
    check(f"G10 {_m} {_h} {_bj}: Rueckruf-Floor {'greift' if _soll else 'greift NICHT'}",
          (_rr_floor is not None) == _soll)
    # Die eigentliche Zusicherung, unabhaengig vom Bestand: ein Floor entsteht
    # ausschliesslich aus VERIFIZIERTEN Rueckrufen.
    _traeger = [i for i in _rr if i.id in set(_rr_floor.evidence_ids)] if _rr_floor else []
    check(f"G10b {_m} {_h} {_bj}: ein Floor wird nur von verifizierten Rueckrufen getragen",
          _rr_floor is None or (bool(_traeger)
                                and all(i.trust == "verified" for i in _traeger)))


# ══ H) §13 — Nutzerwortlaut ══════════════════════════════════════════════════
print("\n--- H) §13 Nutzerwortlaut ---")
check("H1 verified MIT amtlicher Nummer -> 'KBA-Rückruf' + Nummer als Quelle "
      "(synthetische Fixture aus Abschnitt G — kein reales Pilotfahrzeug "
      "erreicht diese Kombination mehr, siehe A7/D1)",
      bool(_nox) and _nox[0].titel.startswith("KBA-Rückruf")
      and any(q.ref == "445566" and q.titel == "KBA-Rückrufdatenbank"
              for q in _nox[0].quellen))

# verified OHNE amtliche Nummer (BMW Hochvoltspeicher, NHTSA-belegt)
_b_hv, _mm_hv, _req_hv, _ins_hv = _check(
    "BMW", "3er", "G20/G21", "330e", 2020, "Plug-in-Hybrid")
_hv = [i for i in _rueckrufe(_ins_hv) if "Hochvoltspeicher" in i.titel]
check("H2 der NHTSA-belegte Rueckruf erscheint beim PHEV", len(_hv) == 1)
check("H3 er ist verified", bool(_hv) and _hv[0].trust == "verified")
# KBA-GESAMTABGLEICH: dieser Rueckruf traegt jetzt die amtliche Nummer 10176 —
# der Pilot hatte ihn nur ueber die US-Behoerde NHTSA belegen koennen, weil der
# KBA-Export damals nicht erreichbar war. Damit heisst er zu Recht
# "KBA-Rueckruf" und erreicht die staerkste Ohne-VIN-Stufe.
# Der Fall "verified OHNE amtliche Nummer" kommt im Bestand nicht mehr vor; die
# Wortlaut-Regel dafuer wird synthetisch in test_kba_trust.py und
# test_fakt_verifikation.py (C8-C10) weiter zugesichert.
check("H4 mit amtlicher Nummer heisst er 'KBA-Rückruf'",
      bool(_hv) and _hv[0].titel.startswith("KBA-Rückruf"))
check("H5 Quellentitel ist die KBA-Rückrufdatenbank mit der Nummer 10176",
      bool(_hv) and any(q.ref == "10176" and q.titel == "KBA-Rückrufdatenbank"
                        for q in _hv[0].quellen))
check("H6 verified MIT amtlicher Nummer erreicht variant_match",
      bool(_hv) and _hv[0].applicability == "variant_match")

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
    ("BMW", "320d"):           2,   # unveraendert: Batch A traf den G20 nicht
    ("Opel", "2.0 Diesel"):    2,   # 1 Nachtrag (KBA 12223) + 1 aus Batch A
    ("Audi", "2.0 FSI 150 PS"): 1,  # unveraendert
    ("Mercedes-Benz", "C220d"): 5,  # 1 Altbestand + 4 aus Batch A
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
# KBA-GESAMTABGLEICH: 3 wortgleiche Dubletten entfernt (G-Klasse, A1, TT RS) —
# keine davon an einem Pilotfahrzeug.
# BATCH A: +271 amtliche Zeilen, davon 20 an Pilotfahrzeugen.
check(f"K1 Rueckrufbestand {746 + len(_BATCH_A)} Zeilen "
      f"(746 + {len(_BATCH_A)} aus Batch A)",
      _gesamt_rueckrufe == 746 + len(_BATCH_A))
check("K2 keine Dublette an den Pilotfahrzeugen (15 Zeilen, 15 IDs)",
      len(_pilot_rows) == len({r["id"] for r in _pilot_rows}) == 15)
_paare = [(r["baureihe_id"], (r["mangel"] or "").strip()) for r in _pilot_rows]
check("K3 kein doppelter Mangeltext je Baureihe", len(_paare) == len(set(_paare)))
# Die Batch-A-Zeilen duerfen den Pilotbestand nicht verdoppeln: keine von ihnen
# darf denselben Mangeltext auf derselben Baureihe tragen wie eine Pilotzeile.
_pilot_texte = set(_paare)
check("K4 keine Batch-A-Zeile wiederholt einen Pilot-Mangeltext",
      not [r for r in _batch_a_rows
           if (r["baureihe_id"], (r["mangel"] or "").strip()) in _pilot_texte],
      )
check("K5 je Baureihe traegt jede amtliche Referenz hoechstens eine Zeile",
      len({(r["baureihe_id"], (r["kba_referenz"] or "").strip())
           for r in _alle_rows if (r["kba_referenz"] or "").strip()})
      == len([r for r in _alle_rows if (r["kba_referenz"] or "").strip()]))
check("K4 keine Verifikation ohne zugehoerigen Fakt",
      all(any(r["id"] == fid for r in _pilot_rows)
          for fid in _verifs if fid in {e[1] for e in RECALL_VERIFIKATIONEN}))


# ══ L) Datenkorrekturen sind angekommen ══════════════════════════════════════
print("\n--- L) Datenkorrekturen ---")
_nach_id = {r["id"]: r for r in _pilot_rows}
check("L1 #546 Insignia NOx: Datum auf den durch Sekundaerquellen belegten Stand korrigiert",
      _nach_id[546]["datum"] == "2022-02")
check("L2 #546: Bauzeitraum auf das belegte Fenster verengt",
      _nach_id[546]["betroffene_baujahre"] == "2017-2018 (1,6 l Diesel Euro 6)")
# KBA-GESAMTABGLEICH: die im Safety-Check entfernte Nummer ist zurueck — jetzt
# aber aus der amtlichen Primaerquelle statt aus Sekundaerberichten. Der
# amtliche Datensatz bestaetigt 11422 samt Herstellercode E222115640 (22-C-013)
# O7A und der Motoreingrenzung "1,3 l und 1,6 l Dieselmotor Euro 6 mit AGR +
# NSK (LNT)".
check("L2b #546: amtlich bestaetigte KBA-Referenz 11422 gespeichert",
      _nach_id[546]["kba_referenz"] == "11422")
# KBA-GESAMTABGLEICH: Datum auf die amtliche Veroeffentlichung 2021-05
# praezisiert (der Pilot hatte 2021-06 aus einem Fachmedium); Bauzeitraum
# unveraendert und amtlich bestaetigt.
check("L3 #544 Bremspedal: Datum/Bauzeitraum amtlich bestaetigt",
      _nach_id[544]["datum"] == "2021-05"
      and _nach_id[544]["betroffene_baujahre"] == "2021"
      and _nach_id[544]["kba_referenz"] == "10743")
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
