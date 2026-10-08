"""
Konjunktive Kombinationsregel in `app.recall_filter.rueckruf_scope()` —
Release-Gate-Fund (Audi-9831-Shadow-Proof).

Eine amtliche Eingrenzung wie "2.0 TFSI UND Mild-Hybrid-System" ist eine
KONJUNKTION mehrerer Bedingungen. Vorher genuegte IRGENDEIN passender
Teiltreffer (z.B. Kraftstoff), um VARIANT_POSSIBLE zurueckzugeben — auch wenn
eine ANDERE, gleichrangige Bedingung (Antriebsart/MHEV) noch unbekannt war.
Das war materiell falsch: `app.empfehlungs_floor.RUECKRUF_WERKSTATT_
APPLICABILITY` und mehrere weitere Konsumenten behandeln VARIANT_POSSIBLE
("variant_match") als staerker als UNKNOWN ("unclear").

Generische Regel (gilt fuer JEDE Kombination strukturierter Dimensionen —
Kraftstoff, Antriebsart, Leistung, Hubraum, Motorcode, Ausstattung — nicht
nur Kraftstoff+MHEV):

  mind. eine Dimension offen (unbekannt)      -> UNKNOWN
  keine offen, mind. eine passt explizit      -> VARIANT_POSSIBLE (Ceiling)
  eine Dimension widerspricht                 -> NOT_APPLICABLE (sofort, s.u.)
  gar keine Dimension geprueft                -> SERIES_RELEVANT

KEIN Netzwerk, KEIN LLM-Call, KEINE DB-Mutation.

    python test_recall_scope_combination.py
"""
from app.recall_filter import rueckruf_scope
from app.vehicle_identity import VehicleIdentity

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


# Zwei-dimensionale amtliche Eingrenzung, real (KBA 9831): Kraftstoff (via
# Motorcode-Kuerzel "TFSI") + Antriebsart (via "Mild-Hybrid-System").
_r_tfsi = {
    "mangel": "Durch Feuchtigkeitseintritt in den Riemenstartergenerator können "
             "Kurzschlussbrücken entstehen. Fahrzeugbrand möglich.",
    "abhilfe": "Austausch des Generators.",
    "betroffene_baujahre": "2017-2020",
    "eingrenzung_amtlich": "Es sind ausschließlich Fahrzeuge mit 2.0 TFSI und "
                          "Mild-Hybrid-System betroffen",
}
_r_tdi = {**_r_tfsi,
         "eingrenzung_amtlich": "Es sind ausschließlich Fahrzeuge mit 2.0 TDI "
                                "und Mild-Hybrid-System betroffen"}


def ident(**kw):
    basis = dict(make="Audi", model="A4", displacement="2.0", year=2018)
    basis.update(kw)
    return VehicleIdentity(**basis)


# ══ 1) MATCH + UNKNOWN => UNKNOWN ════════════════════════════════════════
print("\n--- 1) MATCH (Kraftstoff) + UNKNOWN (Antriebsart) => UNKNOWN ---")
_s1, _ = rueckruf_scope(_r_tfsi, ident(fuel="Benzin"))
check("1.1 Kraftstoff passt (Benzin=TFSI), Antriebsart (MHEV) unbekannt "
      "=> Gesamtergebnis UNKNOWN, nicht VARIANT_POSSIBLE",
      _s1 == "UNKNOWN")

# ══ 2) MATCH + MATCH => VARIANT_POSSIBLE (Ceiling) ═══════════════════════
print("\n--- 2) MATCH + MATCH => VARIANT_POSSIBLE (nie staerker) ---")
_s2, _ = rueckruf_scope(_r_tfsi, ident(fuel="Benzin", powertrain="MHEV"))
check("2.1 Kraftstoff passt UND Antriebsart passt => VARIANT_POSSIBLE",
      _s2 == "VARIANT_POSSIBLE")
check("2.2 niemals staerker als VARIANT_POSSIBLE (keine 'confirmed'-Stufe "
      "ohne VIN)", _s2 in ("VARIANT_POSSIBLE",))

# ══ 3) MATCH + CONTRADICTION => NOT_APPLICABLE ═══════════════════════════
print("\n--- 3) MATCH (Kraftstoff) + CONTRADICTION (Antriebsart) => NOT_APPLICABLE ---")
_s3, _ = rueckruf_scope(_r_tfsi, ident(fuel="Benzin", powertrain="ICE"))
check("3.1 Kraftstoff passt, Antriebsart widerspricht ausdruecklich (ICE "
      "statt MHEV) => NOT_APPLICABLE, trotz des passenden Kraftstoffs",
      _s3 == "NOT_APPLICABLE")

# ══ 4) UNKNOWN + UNKNOWN => UNKNOWN ═══════════════════════════════════════
print("\n--- 4) UNKNOWN + UNKNOWN => UNKNOWN ---")
_s4, _ = rueckruf_scope(_r_tfsi, ident())
check("4.1 weder Kraftstoff noch Antriebsart bekannt => UNKNOWN",
      _s4 == "UNKNOWN")

# ══ 5) bekannter Benzin vs. offizieller TDI => NOT_APPLICABLE ════════════
print("\n--- 5) bekanntes Benzin-Fahrzeug vs. amtliches TDI => NOT_APPLICABLE ---")
_s5, _ = rueckruf_scope(_r_tdi, ident(fuel="Benzin"))
check("5.1 TDI-Eingrenzung widerspricht einem Benzin-Fahrzeug (ueber das "
      "Motorcode-Kuerzel erkannt, nicht nur das ausgeschriebene Wort)",
      _s5 == "NOT_APPLICABLE")

# ══ 6) bekanntes MHEV + passendes TFSI => VARIANT_POSSIBLE ═══════════════
print("\n--- 6) bekanntes MHEV + passendes TFSI => VARIANT_POSSIBLE (= Fall 2) ---")
check("6.1 (identisch zu 2.1 — beide Dimensionen bekannt und passend)",
      _s2 == "VARIANT_POSSIBLE")

# ══ 7) unbekanntes MHEV + passendes TFSI => UNKNOWN ══════════════════════
print("\n--- 7) unbekanntes MHEV + passendes TFSI => UNKNOWN (= Fall 1) ---")
check("7.1 (identisch zu 1.1 — DAS war der urspruengliche Shadow-Proof-Fund: "
      "vorher faelschlich VARIANT_POSSIBLE, jetzt korrekt UNKNOWN)",
      _s1 == "UNKNOWN")

# ══ Generizitaet: dieselbe Regel mit einer VOLLSTAENDIG anderen "
# Dimensionskombination (Leistung + Motorcode statt Kraftstoff + Antriebsart) ═
print("\n--- generisch: dieselbe Regel bei Leistung+Motorcode statt Kraftstoff+Antriebsart ---")
_r_leistung_motorcode = {
    "mangel": "Software-Fehler im Steuergeraet.", "abhilfe": None,
    "betroffene_baujahre": "2018-2020",
    "scope_leistung_ps": [190], "scope_motorcodes": ["CVKB"],
}
_s8, _ = rueckruf_scope(_r_leistung_motorcode, ident(horsepower=190))
check("8.1 Leistung passt (190 PS), Motorcode unbekannt => UNKNOWN "
      "(dieselbe Konjunktionsregel, voellig andere Dimensionen)",
      _s8 == "UNKNOWN")
_s9, _ = rueckruf_scope(_r_leistung_motorcode,
                        ident(horsepower=190, engine_code="CVKB"))
check("8.2 Leistung passt UND Motorcode passt => VARIANT_POSSIBLE",
      _s9 == "VARIANT_POSSIBLE")
_s10, _ = rueckruf_scope(_r_leistung_motorcode, ident(horsepower=250))
check("8.3 Leistung widerspricht (250 statt 190 PS) => NOT_APPLICABLE",
      _s10 == "NOT_APPLICABLE")


print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE SCOPE-KOMBINATIONS-TESTS GRUEN")
