"""
Toyota-Primaerquellen-Folgeauftrag (29.10.2026): reale, konkret nachgewiesene
Extraktionsverluste am echten Pressetext global.toyota/en/detail/4101939
(Lexus RC F, Toyota Motor Corporation, 23.10.2014) -- behoben in
app/technical_research.py:
  F9a: Motorcode in eigenem Klammer-Hubraumdeskriptor ("2UR-GSE
       (V8 5.0-liter)") ohne jedes Label-Wort davor.
  F9b: Leistung in der Konvention "<kW> [<PS>] / <rpm>", mit "kW[PS]/rpm"
       nur EINMAL in der Tabellenkopfzeile genannt ("351 [477] / 7,100").
  F9c: "0.1 seconds" (Schaltzeit) erzeugte einen falschen
       displacement-Kandidaten "0.1" (generische Zeit-/Beschleunigungs-/
       Verbrauchs-/Drehzahl-Ausschlussliste statt Allowlist -- eine
       Allowlist nach Zylinderform haette die zahlreichen bestehenden
       label-losen Schreibweisen ohne Ziffer im Folgewort gebrochen: "2.0
       TDI", "2.0 Diesel", "1.4 Turbo", "3.0 Biturbo", "2.0 SKYACTIV",
       "1.5 dCi" -- s. test_data_trust_runtime.py u.a.).

Deterministisch, kein Netzwerk/LLM/DB-Schreibzugriff. Konsensschwellen
unveraendert -- dieser Test prueft NUR die Claim-EXTRAKTION (`_identitaets_claims`),
nicht den Konsenspfad (der ist unveraendert und bereits durch
test_identity_*.py abgedeckt).

    python test_motorcode_kw_ps_hubraum_fremdeinheit.py
"""
import os
import sys
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_motorcode_"), "test.db")
sys.path.insert(0, ".")

from app.technical_research import _identitaets_claims, _ziel  # noqa: E402

FEHLER = []


def check(name, cond, detail=""):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}" + (f" -- {detail}" if detail else ""))
    if not cond:
        FEHLER.append(name)


def claims_fuer(text, marke="Lexus", modell="RC F", baujahr=2017, motor="5.0 V8"):
    z = _ziel(marke, modell, baujahr, motor, None)
    r = {"url": "https://example.test/x", "title": "x", "content": text}
    return _identitaets_claims(r, z)


def werte(claims, feld):
    return [c["wert"] for c in claims if c["feld"] == feld and not c.get("verworfen")]


# ── Realer, woertlicher Ausschnitt des echten Pressetexts (unveraendert, nur
# auf die identitaetsrelevanten Saetze gekuerzt) ────────────────────────────
TOYOTA_RC_F_REAL = (
    "the RC F powertrain has a new V8 5.0-liter engine using D-4S fuel "
    "injection technology and Dual VVT-i technology with VVT-iE, which "
    "delivers the response and exhilarating sense of engine speed of a "
    "naturally aspirated engine. The 8-Speed Sport Direct Shift provides a "
    "true sports driving experience through gear changes that take as "
    "little as 0.1 seconds from the 'M' position. "
    "RC F Engine Key Specifications: Total displacement (cc) Maximum output "
    "(kW[PS]/rpm) Maximum torque (N-m[kgf-m]/rpm) 2UR-GSE 4,968 351 [477] / "
    "7,100 530 [54.0] / 4,800-5,600. Manufacturer's Suggested Retail Price: "
    "Engine Transmission Driveline Price RC F Carbon Exterior package "
    "2UR-GSE (V8 5.0-liter) 8-Speed SPDS Rear-wheel drive 10,300,000 yen "
    "9,530,000 yen.")

print("=" * 70)
print("1) Realer Toyota-Pressetext (global.toyota, 23.10.2014) -- alle drei Fixes")
print("=" * 70)
c = claims_fuer(TOYOTA_RC_F_REAL)
check("1a Motorcode 2UR-GSE wird erkannt (Klammer-Hubraumdeskriptor)",
      "2UR-GSE" in werte(c, "engine_code"), str(werte(c, "engine_code")))
check("1b Leistung 477 PS wird erkannt (kW[PS]/rpm-Konvention)",
      477 in werte(c, "horsepower"), str(werte(c, "horsepower")))
check("1c Hubraum bleibt bei 5.0 -- KEIN falscher Kandidat '0.1' aus '0.1 seconds'",
      werte(c, "displacement") == ["5.0", "5.0"], str(werte(c, "displacement")))
check("1d Antrieb Heck weiterhin erkannt (unveraendert)",
      "Heck" in werte(c, "drivetrain"), str(werte(c, "drivetrain")))
check("1e Getriebe NICHT als 'automatik' behauptet -- 'Sport Direct Shift'/'SPDS' "
      "ist ein Markenname ohne belegte generische Ausloesung, keine Marketing-"
      "Hardcode-Uebersetzung (berechtigte Guetigkeitsgrenze, kein Fix)",
      werte(c, "transmission") == [], str(werte(c, "transmission")))

print("\n" + "=" * 70)
print("2) Motorcode-in-Klammer: generisch, marken-/modellunabhaengig")
print("=" * 70)
c2 = claims_fuer("Der Motor N47D20 (2.0-liter Diesel) leistet 163 PS.", motor="2.0 Diesel")
check("2a generischer Fall (BMW N47D20) wird ebenfalls erkannt",
      "N47D20" in werte(c2, "engine_code"), str(werte(c2, "engine_code")))

c3 = claims_fuer("Lexus RC (XC10) ist die Baureihe des RC F.")
check("2b Generations-/Typcode in Klammern ('RC (XC10)') wird NICHT als Motorcode "
      "uebernommen -- kein Hubraum-/Literbeleg in der Klammer",
      werte(c3, "engine_code") == [], str(werte(c3, "engine_code")))

c3b = claims_fuer("Der Lexus RC F (USC10) wird vom Lexus RC (XC10) abgeleitet.")
check("2c mehrere Typ-/Variantencodes in eigenen Klammern werden NICHT vermischt "
      "oder als Motorcode uebernommen",
      werte(c3b, "engine_code") == [], str(werte(c3b, "engine_code")))

print("\n" + "=" * 70)
print("3) kW[PS]-Klammerkonvention: nur mit Beleg, nur bei passendem Verhaeltnis")
print("=" * 70)
c4 = claims_fuer("Systemleistung 283 [385] / 6,600 (kW[PS]/rpm), Drehmoment "
                "600 [61.2] / 1,750-5,500 (N-m[kgf-m]/rpm).", motor="4.0 V8")
check("3a andere reale kW[PS]-Zahlen werden korrekt als PS erkannt (generisch)",
      385 in werte(c4, "horsepower"), str(werte(c4, "horsepower")))
check("3b Drehmoment-Klammer (Dezimalwert 61.2) erzeugt KEINE falsche Leistung",
      61 not in werte(c4, "horsepower") and 600 not in werte(c4, "horsepower"),
      str(werte(c4, "horsepower")))

c5 = claims_fuer("Bei 100 [100] U/min liegt kein sinnvoller Wert vor (kW[PS]/rpm).")
check("3c Klammerzahl ohne passendes kW->PS-Verhaeltnis (100/100) wird verworfen",
      werte(c5, "horsepower") == [], str(werte(c5, "horsepower")))

c6 = claims_fuer("Ohne jede Einheitenerklaerung: 351 [477] / 7,100.")
check("3d OHNE die Konvention 'kW[PS]' irgendwo im Text wird die Klammerzahl NICHT "
      "als PS vermutet (kein Rateschluss ohne Beleg)",
      werte(c6, "horsepower") == [], str(werte(c6, "horsepower")))

print("\n" + "=" * 70)
print("4) Hubraum: Zeit-/Beschleunigungs-/Verbrauchs-/Drehzahleinheiten erzeugen "
      "keinen Hubraum (generische Ausschlussliste, keine Allowlist)")
print("=" * 70)
# Jede Phrase in einem echten Satz MIT Zielmodellnennung (sonst filtert bereits
# die Satz-/Segmentbindung alles heraus, bevor die Hubraum-Regel ueberhaupt
# erreicht wird -- s. `_segment_kontext_saetze()`; reale Quellen sind nie ein
# nackter Zwei-Wort-Fragmenttext).
for satzteil, label in [
    ("beschleunigt in 4,5 Sekunden", "Sekunden"),
    ("accelerates in 4.1 seconds", "seconds"),
    ("verbraucht 6,5 l/100km", "l/100km (Verbrauch)"),
    ("erreicht eine Geschwindigkeit von 4.5 mph", "mph"),
    ("dreht bei 7.1 rpm am hoechsten", "rpm"),
]:
    text = f"Der Lexus RC F {satzteil}."
    cx = claims_fuer(text)
    check(f"4) {label!r} erzeugt KEINEN Hubraum-Kandidaten", werte(cx, "displacement") == [],
          f"{text!r} -> {werte(cx, 'displacement')}")

print("\n" + "=" * 70)
print("5) Bestehende Hubraum-Schreibweisen bleiben vollstaendig erhalten")
print("=" * 70)
for satzteil, erwartet in [
    ("hat 5.0 V8", "5.0"), ("hat einen 5.0-liter Motor", "5.0"),
    ("hat einen 5.0 litre Motor", "5.0"), ("hat 2.0 TDI", "2.0"),
    ("hat 2.0 Diesel", "2.0"), ("hat 1.4 Turbo", "1.4"),
    ("hat 3.0 Biturbo", "3.0"), ("hat 2.0 SKYACTIV", "2.0"),
    ("hat 1.5 dCi", "1.5"), ("hat 5.0 l Hubraum", "5.0"),
]:
    text = f"Der Lexus RC F {satzteil}."
    cx = claims_fuer(text)
    check(f"5) {text!r} liefert weiterhin Hubraum {erwartet!r}", erwartet in werte(cx, "displacement"),
          str(werte(cx, "displacement")))

print("\n" + "=" * 70)
if FEHLER:
    print(f"{len(FEHLER)} FEHLER:")
    for f in FEHLER:
        print(" -", f)
    sys.exit(1)
print("Alle Motorcode/kW-PS/Hubraum-Fremdeinheit-Tests bestanden.")
