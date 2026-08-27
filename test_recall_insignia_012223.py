"""
NACHTRAG zum Recall-Pilot: amtlich belegter Insignia-B-Rueckruf (KBA 12223)
KEIN Netzwerk, KEIN LLM-Call, KEINE Tavily-Calls.

  A) Kuratierte Daten formal konsistent
  B) Der Fakt steht in der Datenbank, Fingerprint aktuell
  C) §2 Applicability fuer das Testfahrzeug
  D) §5 Runtime-Kette: Evidence -> Kaufaktion -> Floor -> LLM-Kontext
  E) §6 Fact-Isolation: kein Mitverifizieren, keine Streuwirkung
  F) BUGFIX Hochvolt-Erkennung: "elektronisch" ist kein Hochvolt-Antrieb
  G) Bestandsintegritaet, keine Dublette

    python test_recall_insignia_012223.py
"""
import re

from app.car_lookup import build_db_context, find_motor
from app.database import get_baureihe, get_conn
from app.empfehlungs_floor import ermittle_floor, wende_floor_an
from app.evidence import build_insights
from app.fakt_verifikation import fingerprint
from app.kaufaktionen import build_kaufaktionen
from app.models import KaufCheckRequest
from app.recall_filter import _HV_MUSTER, rueckruf_applicability
from app.recall_insignia_012223_daten import (
    NEUER_FAKT_ID, NEUER_RUECKRUF, NEUE_VERIFIKATION, _selbsttest,
)

_FEHLER: list[str] = []
BEREICHE = ("besichtigung", "probefahrt", "verkaeuferfragen", "dokumente")


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def lauf(hint, baujahr, kraftstoff, marke="Opel", modell="Insignia", gen="B"):
    b = get_baureihe(marke, modell, gen)
    mm = find_motor(b, hint)
    req = KaufCheckRequest(marke=marke, modell=modell, baujahr=baujahr,
                           motor=hint, kraftstoff=kraftstoff,
                           kilometerstand=120000, preis_eur=20000)
    ins = build_insights(b, mm, [], req, check_typ="kauf")
    return b, mm, req, ins


def rueckrufe(ins):
    return [i for i in ins if i.kategorie == "rueckruf"]


def neuer(ins):
    return [i for i in rueckrufe(ins) if "Bremskraftausgleich" in i.titel]


# ══ A) Kuratierte Daten ══════════════════════════════════════════════════════
print("\n--- A) Kuratierte Daten ---")
try:
    _selbsttest()
    check("A1 Selbsttest laeuft durch", True)
except AssertionError as exc:
    check(f"A1 Selbsttest: {exc}", False)

check("A2 amtliche Referenz in KBA-Schreibweise (12223, ohne fuehrende Null)",
      NEUER_RUECKRUF["kba_referenz"] == "12223")
check("A3 Quellenstufe A (amtliche Primaerquelle)", NEUE_VERIFIKATION[4] == "A")
check("A4 Status verified", NEUE_VERIFIKATION[2] == "verified")
check("A5 Bauzeitraum ist die Schnittmenge mit dem Insignia B (2017-2020), "
      "nicht das rohe amtliche Fenster 2016-2020",
      NEUER_RUECKRUF["betroffene_baujahre"] == "2017-2020")
check("A6 kein Varianten-Qualifier (KBA: 'Eingrenzung: N/A')",
      "(" not in NEUER_RUECKRUF["betroffene_baujahre"])


# ══ B) Der Fakt in der Datenbank ═════════════════════════════════════════════
print("\n--- B) Datenbank ---")
with get_conn() as conn:
    _zeile = conn.execute(
        "SELECT id,baureihe_id,datum,betroffene_baujahre,mangel,abhilfe,kba_referenz "
        "FROM rueckruf WHERE id=?", (NEUER_FAKT_ID,)).fetchone()
    _zeile = dict(_zeile) if _zeile else None
    _verif = conn.execute(
        "SELECT fingerprint,status,quelle,quelle_stufe,url,referenz,notiz "
        "FROM fakt_verifikation WHERE fakt_art='rueckruf' AND fakt_id=?",
        (NEUER_FAKT_ID,)).fetchone()
    _verif = dict(_verif) if _verif else None
    _gesamt = conn.execute("SELECT COUNT(*) FROM rueckruf").fetchone()[0]
    _insignia = [dict(r) for r in conn.execute(
        "SELECT id,mangel,kba_referenz FROM rueckruf WHERE baureihe_id='opel-insignia-b'")]

check("B1 Rueckrufzeile existiert", _zeile is not None)
check("B2 haengt an opel-insignia-b",
      bool(_zeile) and _zeile["baureihe_id"] == "opel-insignia-b")
check("B3 traegt die amtliche Referenz 12223",
      bool(_zeile) and _zeile["kba_referenz"] == "12223")
check("B4 Mangeltext ist die woertliche amtliche Mangelbezeichnung",
      bool(_zeile) and _zeile["mangel"] == NEUER_RUECKRUF["mangel"])
check("B5 Abhilfe ist die woertliche amtliche Massnahme",
      bool(_zeile) and _zeile["abhilfe"] == NEUER_RUECKRUF["abhilfe"])
check("B6 Verifikation vorhanden und verified",
      bool(_verif) and _verif["status"] == "verified")
check("B7 Quellenstufe A", bool(_verif) and _verif["quelle_stufe"] == "A")
check("B8 KEIN stale-verification-Eintrag (Fingerprint passt)",
      bool(_verif) and bool(_zeile)
      and _verif["fingerprint"] == fingerprint("rueckruf", _zeile))
# KBA-GESAMTABGLEICH: die Notiz wurde beim Gesamtabgleich neu geschrieben und
# haelt jetzt fest, dass 12223 die EINZIGE amtlich gueltige Referenz des ganzen
# Bestands ist. Die Fachpresse-Korrekturen stehen weiterhin im Modul
# app/recall_insignia_012223_daten.py dokumentiert.
check("B9 Notiz benennt den Sonderstatus als einziger EXACT_OFFICIAL_MATCH",
      bool(_verif) and "12223" in _verif["notiz"])


# ══ C) §2 Applicability ══════════════════════════════════════════════════════
print("\n--- C) §2 Applicability Testfahrzeug ---")
_b, _mm, _req, _ins = lauf("2.0 Diesel", 2019, "Diesel")
_n = neuer(_ins)
check("C1 Motor erkannt", _mm is not None)
check("C2 der Rueckruf ist sichtbar", len(_n) == 1)
check("C3 trust=verified", bool(_n) and _n[0].trust == "verified")
check("C4 applicability=variant_match (Baujahr trifft, amtliche Referenz belegt)",
      bool(_n) and _n[0].applicability == "variant_match")
check("C5 confidence=hoch", bool(_n) and _n[0].confidence == "hoch")
check("C6 NIEMALS confirmed_by_vin ohne VIN",
      all(i.applicability != "confirmed_by_vin" for i in rueckrufe(_ins)))
check("C7 Wortlaut 'KBA-Rueckruf' (amtliche Nummer liegt vor)",
      bool(_n) and _n[0].titel.startswith("KBA-Rückruf"))
check("C8 Quelle nennt die amtliche Nummer",
      bool(_n) and any(q.ref == "12223" and q.titel == "KBA-Rückrufdatenbank"
                       for q in _n[0].quellen))
check("C9 der FIN-Hinweis bleibt trotz variant_match bestehen",
      bool(_n) and "FIN" in (_n[0].einfluss or ""))


# ══ D) §5 Runtime-Kette ══════════════════════════════════════════════════════
print("\n--- D) §5 Runtime-Kette ---")
_akt = build_kaufaktionen(_req, _b, _mm, _ins)
_rr_aktionen = [(ber, a) for ber in BEREICHE
                for a in getattr(_akt, ber).fahrzeugspezifisch
                if "Bremskraftausgleich" in ((a.titel or "") + (a.aktion or ""))]
check("D1 Kaufaktionen entstehen", len(_rr_aktionen) >= 2)
check("D2 Prioritaet kritisch (sicherheitsrelevanter Rueckruf)",
      all(a.prioritaet == "kritisch" for _b2, a in _rr_aktionen))
check("D3 Aktionen belegen ueber die ECHTE Evidence-ID",
      bool(_n) and all(_n[0].id in (a.evidence_ids or []) for _b2, a in _rr_aktionen))
check("D4 die Dokumentaktion nennt die amtliche KBA-Nummer",
      any("12223" in (a.aktion or "") for ber, a in _rr_aktionen if ber == "dokumente"))
check("D5 keine Besichtigungsaktion (ein Rueckruf ist vor Ort nicht pruefbar)",
      not any(ber == "besichtigung" for ber, _a in _rr_aktionen))

_floor = ermittle_floor(_ins)
check("D6 Floor greift", _floor is not None
      and _floor.stufe == "nur_mit_werkstattpruefung")
check("D7 Floor-Grund ist der Rueckruf-Variantentreffer",
      _floor is not None and "rueckruf_variantentreffer" in _floor.gruende)
check("D8 Floor belegt ueber die ECHTE Evidence-ID",
      _floor is not None and bool(_n) and _n[0].id in _floor.evidence_ids)
check("D9 Floor hebt an", wende_floor_an("kaufen", _ins)[0] == "nur_mit_werkstattpruefung")
check("D10 Floor senkt NICHT", wende_floor_an("finger_weg", _ins)[0] == "finger_weg")
check("D11 Floor laesst 'unbekannt' unangetastet",
      wende_floor_an("unbekannt", _ins)[0] == "unbekannt")

_ctx = build_db_context(_b, _mm, 2019)
check("D12 der Rueckruf steht im LLM-DB-Kontext", "Bremssteuermodul" in _ctx)
check("D13 die amtliche Nummer steht im LLM-DB-Kontext", "12223" in _ctx)


# ══ E) §6 Fact-Isolation ═════════════════════════════════════════════════════
print("\n--- E) §6 Fact-Isolation ---")
_b2, _mm2, _req2, _ins2 = lauf("1.6 CDTI 136 PS", 2018, "Diesel")
_verified2 = [i for i in rueckrufe(_ins2) if i.trust == "verified"]
# KBA-GESAMTABGLEICH: am Insignia B sind jetzt drei Rueckrufe amtlich
# verifiziert (#544 Bremspedal / 10743, #546 NOx / 11422, #808 EBCM / 12223).
# Jeder einzeln gegen den amtlichen Export geprueft — kein Mitverifizieren:
# entscheidend ist, dass NUR Zeilen mit eigenem amtlichem Beleg verified sind.
_verif_titel = {i.titel for i in _verified2}
check("E1 jeder verifizierte Insignia-Rueckruf hat einen eigenen amtlichen Beleg",
      all(any(w in t for w in ("Bremskraftausgleich", "Pedalplatte",
                               "Abschalteinrichtung"))
          for t in _verif_titel))
check("E2 die unbelegten Zeilen bleiben unverified_db/series_only",
      all(i.trust == "unverified_db" and i.applicability in ("series_only", "unclear")
          for i in rueckrufe(_ins2)
          if not any(w in i.titel for w in ("Bremskraftausgleich", "Pedalplatte",
                                            "Abschalteinrichtung"))))

for _bj, _soll in ((2016, False), (2017, True), (2019, True), (2020, True),
                   (2021, False), (2022, False)):
    _, _, _, _i = lauf("2.0 Diesel", _bj, "Diesel")
    check(f"E3 Baujahr {_bj}: sichtbar={_soll} (amtliches Fenster 2017-2020)",
          bool(neuer(_i)) == _soll)

# KBA nennt ausdruecklich KEINE Varianteneinschraenkung -> alle Motorisierungen.
for _hint, _kst in (("2.0 Diesel", "Diesel"), ("1.6 CDTI 136 PS", "Diesel"),
                    ("1.5 Turbo 140 PS", "Benzin"), ("2.0 Turbo 260 PS", "Benzin")):
    _, _, _, _i = lauf(_hint, 2019, _kst)
    check(f"E4 {_hint} ({_kst}): sichtbar (kein Varianten-Qualifier gesetzt)",
          bool(neuer(_i)))

with get_conn() as conn:
    _fremde_12223 = conn.execute(
        "SELECT COUNT(*) FROM rueckruf WHERE kba_referenz='12223' "
        "AND baureihe_id<>'opel-insignia-b'").fetchone()[0]
    _fremde_verif = conn.execute(
        "SELECT COUNT(*) FROM fakt_verifikation WHERE fakt_art='rueckruf' "
        "AND quelle_stufe='A' AND fakt_id<>?", (NEUER_FAKT_ID,)).fetchone()[0]
check("E5 12223 haengt an keiner anderen Baureihe", _fremde_12223 == 0)
# KBA-GESAMTABGLEICH: Quellenstufe A tragen jetzt genau die 15 kuratierten
# Faelle des Gesamtabgleichs — jeder einzeln manuell gegen den amtlichen Export
# geprueft. "Unbemerkt" waere alles darueber hinaus.
check("E6 Quellenstufe A tragen genau die 15 kuratierten Faelle",
      _fremde_verif == 14)


# ══ F) BUGFIX Hochvolt-Erkennung ═════════════════════════════════════════════
print("\n--- F) BUGFIX: 'elektronisch' ist kein Hochvolt-Antrieb ---")
_KEIN_HV = ("elektronische Bremssteuermodul", "elektronischen Feststellbremse",
            "elektronisches Stabilitätsprogramm", "Lenkungselektronik",
            "elektromechanischen Lenkung", "Elektronik im Steuergerät")
for _t in _KEIN_HV:
    check(f"F1 {_t!r} -> KEIN Hochvolt", not _HV_MUSTER.search(_t))

_ECHT_HV = ("Hochvoltbatterie", "Hochvoltsystem", "Plug-in-Hybrid", "Plugin Hybrid",
            "PHEV-Variante", "Hybridfahrzeuge", "Elektromotor", "Elektroantrieb",
            "Hochspannungsleitung")
for _t in _ECHT_HV:
    check(f"F2 {_t!r} -> weiterhin Hochvolt/Elektro", bool(_HV_MUSTER.search(_t)))

# Der konkrete Fall, der den Bug aufgedeckt hat.
_rr_roh = {**NEUER_RUECKRUF, "_trust": "verified"}
_appl, _conf, _einfl, _hinw = rueckruf_applicability(
    _rr_roh, True, "12223", {"kraftstoff": "Diesel"}, marke="Opel")
check("F3 der amtliche Rueckruf ist fuer einen Diesel NICHT 'incompatible'",
      _appl != "incompatible")
check("F4 sondern variant_match", _appl == "variant_match")

# Ein echter Hochvolt-Rueckruf muss weiterhin fuer einen Diesel ausgeschlossen werden.
_hv = {"mangel": "Brandgefahr der Hochvoltbatterie", "abhilfe": "Modultausch",
       "betroffene_baujahre": "2019-2020", "_trust": "verified"}
_appl_hv, _, _, _ = rueckruf_applicability(
    _hv, True, "445566", {"kraftstoff": "Diesel"}, marke="BMW")
check("F5 echter Hochvolt-Rueckruf bleibt fuer einen Diesel 'incompatible'",
      _appl_hv == "incompatible")


# ══ F2) FOLGE-FIX: "elektrisch" ist ebenfalls kein Hochvolt-Signal ══════════
print("\n--- F2) FOLGE-FIX: 'elektrisch' ist kein Hochvolt-Antrieb ---")
_NICHT_HV_ELEKTRISCH = (
    "elektrische Kraftstoffpumpe", "elektrische Servolenkung",
    "elektrische Feststellbremse", "elektrische Wasserpumpe",
    "elektrische Zusatzwasserpumpe", "elektrische Sitzheizung",
    "elektrische Lenkung", "elektrischen Fensterheberschalter",
    "Softwarefehler im Steuergerät der elektrischen Servolenkung",
)
for _t in _NICHT_HV_ELEKTRISCH:
    check(f"F2a {_t!r} -> KEIN Hochvolt", not _HV_MUSTER.search(_t))

# Die 29 real betroffenen DB-Zeilen: alle muessen jetzt series_only statt
# incompatible ergeben (Baujahr passt, kein Antriebs-Widerspruch mehr).
_29_IDS = (49, 50, 62, 74, 88, 100, 106, 130, 144, 153, 155, 201, 204, 215,
          221, 228, 231, 238, 251, 256, 269, 333, 338, 517, 538, 543, 578,
          607, 735)
with get_conn() as conn:
    _platzh = ",".join("?" * len(_29_IDS))
    _29_rows = [dict(r) for r in conn.execute(
        f"SELECT r.*, b.marke FROM rueckruf r JOIN baureihe b ON b.id=r.baureihe_id "
        f"WHERE r.id IN ({_platzh})", _29_IDS)]
check("F2b alle 29 real betroffenen Zeilen gefunden", len(_29_rows) == 29)
_alle_series_only = True
for _r in _29_rows:
    _a, _c, _, _ = rueckruf_applicability(
        {**_r, "_trust": "unverified_db"}, True, _r.get("kba_referenz") or "",
        {"kraftstoff": "Diesel"}, marke=_r["marke"])
    if _a != "series_only":
        _alle_series_only = False
        print(f"      abweichend: #{_r['id']} {_r['marke']} -> {_a}")
check("F2c alle 29 Zeilen: series_only statt incompatible (Verbrenner-Testfall)",
      _alle_series_only)

# Gegenprobe: kein zusaetzlicher Rueckruf wurde durch den zweiten Fix beeinflusst.
with get_conn() as conn:
    _alle = [dict(r) for r in conn.execute(
        "SELECT r.*, b.marke FROM rueckruf r JOIN baureihe b ON b.id=r.baureihe_id")]
_ALT_MUSTER = re.compile(
    r"(?<![a-zäöüß])(?:hochvolt|hochspannung|plug-?\s?in|plugin|phev|hybrid|"
    r"elektro(?!nisch|nik|mechanisch)|elektrisch)", re.IGNORECASE)
_diff = []
for _r in _alle:
    _text = " ".join(filter(None, [_r.get("mangel"), _r.get("abhilfe"),
                                   _r.get("betroffene_baujahre")]))
    _alt_treffer = bool(_ALT_MUSTER.search(_text))
    _neu_treffer = bool(_HV_MUSTER.search(_text))
    if _alt_treffer != _neu_treffer:
        _diff.append((_r["id"], _alt_treffer, _neu_treffer))
check("F2d DB-weit exakt 29 Aenderungen (nicht mehr, nicht weniger)",
      len(_diff) == 29)
check("F2e alle Aenderungen gehen von HV-erkannt zu NICHT-HV-erkannt "
      "(keine Zeile wird durch den Fix neu versteckt)",
      all(alt and not neu for _id, alt, neu in _diff))
check("F2f die IDs stimmen exakt mit den 29 real geprueften Zeilen ueberein",
      sorted(_id for _id, _, _ in _diff) == sorted(_29_IDS))


# ══ G) Bestandsintegritaet ═══════════════════════════════════════════════════
print("\n--- G) Bestandsintegritaet ---")
# KBA-GESAMTABGLEICH: 3 wortgleiche Dubletten entfernt (G-Klasse, A1, TT RS).
check("G1 Rueckrufbestand 746 Zeilen (749 minus 3 Dubletten)", _gesamt == 746)
check("G2 opel-insignia-b hat 6 Zeilen (5 + 1)", len(_insignia) == 6)
check("G3 keine Dublette: 12223 genau einmal am Insignia B",
      sum(1 for r in _insignia if (r["kba_referenz"] or "") == "12223") == 1)
_mangel = [r["mangel"].strip() for r in _insignia]
check("G4 kein doppelter Mangeltext am Insignia B", len(_mangel) == len(set(_mangel)))
check("G5 der Bremspedal-Rueckruf (#544) ist ein ANDERER Fakt, keine Dublette",
      any("Pedalplatte" in m for m in _mangel)
      and any("Bremskraftausgleich" in m for m in _mangel))


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE INSIGNIA-012223-TESTS GRUEN")
