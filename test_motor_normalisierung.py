"""
Motor-Normalisierung in `find_motor`. KEIN Netzwerk, KEIN LLM-Call.

Befund aus dem Technical-Web-Fallback-Test: Die Baureihenerkennung normalisiert
Bezeichnungen seit jeher ("C 200" -> "c200", `_norm_bezeichnung`), `find_motor`
verglich dagegen rohe Strings. Für "Mercedes-Benz C 200" wurde die Baureihe
gefunden, die real vorhandene Variante "C200" aber NICHT — der Motor galt als
unbekannt und löste unnötig den technischen Web-Fallback aus.

  A) Mercedes C 200        -> DB-Variante C200
  B) Mercedes C 220 d      -> DB-Variante C220 d
  C) Leerzeichenunterschied-> gleicher Motor
  D) Groß-/Kleinschreibung -> gleicher Motor
  E) C200 matcht NICHT C220 (und nicht C200 d)
  F) 320d matcht NICHT 320i
  G) Kraftstoffkonflikt bleibt geschützt
  H) Leistungskonflikt bleibt geschützt
  I) echter Motor-Miss     -> Technical Web Fallback weiterhin ausgelöst
  J) BMW 320d G20 unverändert
  K) Opel Insignia B unverändert

    python test_motor_normalisierung.py
"""
import app.recall_filter as _rf

# Fixture-Isolation (siehe test_kaufaktionen.py): die KBA-Kollisionsprüfung würde
# sonst die Live-DB lesen.
_rf.get_rueckruf_referenzen_kurz = lambda: []

from app.car_lookup import (
    find_baureihe, find_baureihe_mit_vertrauen, find_motor, _norm_bezeichnung,
)
from app.models import KaufCheckRequest
from app.technical_research import TRIGGER_MOTOR_FEHLT, fallback_trigger

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    status = "OK  " if bedingung else "FAIL"
    print(f"[{status}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def motor_von(marke, modell, baujahr, hint):
    br = find_baureihe(marke, modell, baujahr)
    return find_motor(br, hint) if br else None


def bez(m):
    return (m or {}).get("bezeichnung")


W205 = find_baureihe("Mercedes-Benz", "C 200", 2019)
G20 = find_baureihe("BMW", "320d", 2020)
INSIGNIA = find_baureihe("Opel", "Insignia", 2020)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 0) Normalisierungshelper wiederverwendet ===")

check("0.1 bestehender Helper, keine zweite Funktion",
      _norm_bezeichnung("C 200") == "c200")
check("0.2 Bindestrich wird ebenfalls entfernt", _norm_bezeichnung("C-200") == "c200")
check("0.3 Groß-/Kleinschreibung egal", _norm_bezeichnung("C 200") == _norm_bezeichnung("c200"))
check("0.4 None ist verkraftbar", _norm_bezeichnung(None) == "")
check("0.5 Punkte bleiben erhalten (1.8 T ist nicht 18 T)",
      _norm_bezeichnung("1.8 T") == "1.8t")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== A/B) Der gemeldete Fall ===")

_a = motor_von("Mercedes-Benz", "C 200", 2019, "C 200")
check("A1 'C 200' findet die DB-Variante C200", bez(_a) == "C200")
check("A2 und zwar den Benziner mit 184 PS",
      _a and _a["kraftstoff"] == "Benzin" and _a["leistung_ps"] == 184)
check("A3 NICHT die Diesel-Variante C200 d", bez(_a) != "C200 d")

_b = motor_von("Mercedes-Benz", "C 220 d", 2019, "C 220 d")
check("B1 'C 220 d' findet die DB-Variante C220 d", bez(_b) == "C220 d")
check("B2 und zwar den Diesel mit 170 PS",
      _b and _b["kraftstoff"] == "Diesel" and _b["leistung_ps"] == 170)
check("B3 'C220d' (ohne Leerzeichen) trifft denselben Motor",
      bez(motor_von("Mercedes-Benz", "C 220 d", 2019, "C220d")) == "C220 d")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== C/D) Schreibweisen-Äquivalenz ===")

_schreibweisen = ("C 200", "C200", "c 200", "c200", "C-200", "  C 200  ", "C  200")
_ergebnisse = {s: bez(find_motor(W205, s)) for s in _schreibweisen}
check(f"C1 alle Schreibweisen liefern denselben Motor ({set(_ergebnisse.values())})",
      set(_ergebnisse.values()) == {"C200"})
check("D1 Groß-/Kleinschreibung ändert nichts",
      bez(find_motor(W205, "C 200")) == bez(find_motor(W205, "c 200")))
check("D2 auch bei mehrteiligen Bezeichnungen",
      bez(find_motor(W205, "c400 4matic")) == bez(find_motor(W205, "C400 4MATIC")) == "C400 4MATIC")
check("D3 Originalbezeichnung bleibt unverändert (nur Vergleichsform normalisiert)",
      find_motor(W205, "c 200")["bezeichnung"] == "C200")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== E/F) Keine Verwechslung ähnlicher Bezeichnungen ===")

check("E1 'C 200' ist NICHT C220 d", bez(find_motor(W205, "C 200")) != "C220 d")
check("E2 'C 220 d' ist NICHT C200", bez(find_motor(W205, "C 220 d")) != "C200")
check("E3 'C 250' trifft C250, nicht C200/C300",
      bez(find_motor(W205, "C 250")) == "C250")
check("E4 'C 200 d' trifft den Diesel, nicht den Benziner",
      bez(find_motor(W205, "C 200 d")) == "C200 d")
check("E5 'C200' trifft den Benziner, nicht den Diesel",
      bez(find_motor(W205, "C200")) == "C200")
check("E6 'C 180' ist NICHT C180 d", bez(find_motor(W205, "C 180")) == "C180")

check("F1 '320d' trifft 320d", bez(find_motor(G20, "320d")) == "320d")
check("F2 '320i' trifft 320i", bez(find_motor(G20, "320i")) == "320i")
check("F3 '320d' ist NICHT 320i", bez(find_motor(G20, "320d")) != "320i")
check("F4 '320 d' (mit Leerzeichen) trifft ebenfalls 320d",
      bez(find_motor(G20, "320 d")) == "320d")

# Audi: 35 TDI vs 40 TDI dürfen nicht kollidieren
_a4 = find_baureihe("Audi", "A4", 2018)
_a4_bez = [m["bezeichnung"] for m in (_a4 or {}).get("motoren", [])]
_tdi = [b for b in _a4_bez if "TDI" in b]
if len(_tdi) >= 2:
    check(f"F5 Audi A4: jede TDI-Variante trifft sich selbst ({len(_tdi)} Varianten)",
          all(bez(find_motor(_a4, b)) == b for b in _tdi))
    check("F6 Audi A4: keine TDI-Variante trifft eine andere",
          all(bez(find_motor(_a4, b.replace(" ", ""))) == b for b in _tdi))
else:
    check("F5 Audi A4 TDI-Varianten vorhanden", False)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== G/H) Kraftstoff- und Leistungspfade unverändert ===")

# Diese Pfade laufen NACH der Bezeichnungssuche und wurden nicht angefasst.
check("G1 Kraftstoff-Hint grenzt weiterhin ein ('2.0 Diesel 174 PS')",
      (find_motor(INSIGNIA, "2.0 Diesel 174 PS") or {}).get("kraftstoff") == "Diesel")
_g20_diesel = find_motor(G20, "190 PS Diesel")
check("G2 PS+Kraftstoff trifft einen Diesel",
      _g20_diesel is None or _g20_diesel["kraftstoff"] == "Diesel")
check("G3 unpassender Kraftstoff erzwingt keinen falschen Treffer",
      (find_motor(W205, "C 200 Diesel") or {}).get("kraftstoff") in (None, "Diesel"))

_h = find_motor(G20, "150 PS")
check("H1 reine PS-Angabe trifft einen Motor mit genau dieser Leistung",
      _h is None or _h["leistung_ps"] == 150)
check("H2 PS-Einheit wird respektiert (kW-Zahl trifft nicht die PS-Zeile)",
      (find_motor(G20, "190 kW") or {}).get("leistung_kw") in (None, 190))
check("H3 unsinnige Leistung liefert keinen Treffer", find_motor(G20, "9999 PS") is None)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== I) Echter Motor-Miss löst weiterhin den Web-Fallback aus ===")

_br_mb, _info_mb = find_baureihe_mit_vertrauen("Mercedes-Benz", "C 200", 2019)
_mo_mb = find_motor(_br_mb, "C 200")
_req_mb = KaufCheckRequest(marke="Mercedes-Benz", modell="C 200", baujahr=2019, motor="C 200")
check("I1 'C 200' löst KEINEN motor_fehlt-Fallback mehr aus",
      fallback_trigger(_req_mb, _br_mb, _info_mb, _br_mb, _mo_mb) is None)

# Ein Motor, den es in dieser Baureihe wirklich nicht gibt
_req_miss = KaufCheckRequest(marke="Mercedes-Benz", modell="C 200", baujahr=2019,
                             motor="5.0 V10 Wankel Sonderserie")
_mo_miss = find_motor(_br_mb, "5.0 V10 Wankel Sonderserie")
check("I2 echter Motor-Miss liefert None", _mo_miss is None)
check("I3 und löst den Fallback weiterhin aus",
      fallback_trigger(_req_miss, _br_mb, _info_mb, _br_mb, _mo_miss) == TRIGGER_MOTOR_FEHLT)
check("I4 ohne Motorangabe kein motor_fehlt-Trigger",
      fallback_trigger(KaufCheckRequest(marke="Mercedes-Benz", modell="C 200", baujahr=2019),
                       _br_mb, _info_mb, _br_mb, None) is None)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== J/K) Bestehende Fälle unverändert ===")

check("J1 BMW 320d G20: Baureihe unverändert", (G20 or {}).get("id") == "bmw-3er-g20-g21")
check("J2 BMW 320d G20: Motor unverändert", bez(find_motor(G20, "320d")) == "320d")
_j_req = KaufCheckRequest(marke="BMW", modell="320d", baujahr=2020, motor="320d")
_j_br, _j_info = find_baureihe_mit_vertrauen("BMW", "320d", 2020)
check("J3 BMW 320d G20: kein Fallback",
      fallback_trigger(_j_req, _j_br, _j_info, _j_br, find_motor(_j_br, "320d")) is None)

check("K1 Insignia B: Baureihe unverändert", (INSIGNIA or {}).get("id") == "opel-insignia-b")
_k_mo = find_motor(INSIGNIA, "2.0 Diesel 174 PS")
check("K2 Insignia B: Motor unverändert", bez(_k_mo) == "2.0 Diesel (174 PS) (Facelift)")
_k_req = KaufCheckRequest(marke="Opel", modell="Insignia", baujahr=2020,
                          motor="2.0 Diesel 174 PS")
_k_br, _k_info = find_baureihe_mit_vertrauen("Opel", "Insignia", 2020)
check("K3 Insignia B: kein Fallback",
      fallback_trigger(_k_req, _k_br, _k_info, _k_br, _k_mo) is None)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== L) Breitentest über die gesamte Fahrzeugdatenbank ===")

from app.database import get_alle_baureihen_kurz, get_baureihe

_geprueft = _korrekt = 0
_fehler: list[str] = []
for _row in get_alle_baureihen_kurz():
    _daten = get_baureihe(_row["marke"], _row["modell"], _row["generation"])
    for _m in (_daten or {}).get("motoren", []):
        _b = _m["bezeichnung"]
        if not _b:
            continue
        for _hint in (_b, _b.lower(), _b.replace(" ", "")):
            _geprueft += 1
            if bez(find_motor(_daten, _hint)) == _b:
                _korrekt += 1
            elif len(_fehler) < 5:
                _fehler.append(f"{_row['id']}: {_hint!r} -> {bez(find_motor(_daten, _hint))!r}")

check(f"L1 jede Motorbezeichnung findet sich selbst — auch ohne Leerzeichen "
      f"({_korrekt}/{_geprueft}) {_fehler}", _korrekt == _geprueft)
check("L2 der Breitentest deckt die gesamte Motorentabelle ab", _geprueft >= 9000)


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE MOTOR-NORMALISIERUNGSTESTS GRUEN")
