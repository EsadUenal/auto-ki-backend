"""
Gezielte Abschlussprüfung (Fortsetzungsauftrag): die neue Abtrennung rein
alphabetischer, ziffernloser Generationscodes (`_ist_modell_code_kandidat()`,
siehe MX-5-NC-Fix) gegen ECHTE Modell-/Variantenbezeichnungen mit kurzen
alphabetischen Zusätzen geprüft — nicht nur den NC-Fall selbst.

Ein syntaktisch passender Buchstabenzusatz (2 Zeichen, keine Ziffer) ist KEIN
Generationsbeweis — das gilt für "RS"/"ST"/"FR" (Leistungs-/Ausstattungs-
varianten, keine Chassisgeneration) genauso wie für "NC"/"ND" (echte
Chassisgenerationen). Die Unterscheidung passiert NICHT bei der Abtrennung
selbst (dort bewusst "rein formbasiert, kein Katalog" — keine Fahrzeug-/Code-
Allowlist), sondern ausschließlich bei der SEPARATEN Generations-BESTÄTIGUNG
(`_generationscodes()`/`_generationen_pruefen()`), die ohnehin NIE allein aus
der Form, sondern nur aus Jahres-/Generations-Kontext bestätigt.

    python scripts/test_isolated.py test_alignment_alphabetische_zusaetze_gegenpruefung.py
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.technical_research import _ziel, parse_core_model, werte_identitaet_aus

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# ── Reine Split-Logik: echte, kurze alphabetische Varianten-/Trimnamen ──────
for modell, erwartet in (
    ("Clio RS", ("Clio", "RS")),       # Renault Clio RS -- Leistungsvariante
    ("Fiesta ST", ("Fiesta", "ST")),   # Ford Fiesta ST -- Leistungsvariante
    ("Ibiza FR", ("Ibiza", "FR")),     # Seat Ibiza FR -- Ausstattungsvariante
    ("Octavia RS", ("Octavia", "RS")), # Skoda Octavia RS -- Leistungsvariante
    ("MX-5 NC", ("MX-5", "NC")),       # Mazda MX-5 NC -- ECHTE Chassisgeneration
    ("MX-5 ND", ("MX-5", "ND")),       # Mazda MX-5 ND -- ECHTE Chassisgeneration
):
    ergebnis = parse_core_model(modell)
    check(f"'{modell}' -> Core '{erwartet[0]}' + Auxiliary-Kandidat '{erwartet[1]}'",
          ergebnis == erwartet, str(ergebnis))

# Core Model, Variante/Auxiliary Candidate und bestätigte Generation bleiben
# UNTERSCHIEDLICHE Dinge, unabhängig davon, ob der Zusatz eine echte
# Chassisgeneration (NC) oder eine reine Leistungsvariante (RS) ist -- die
# Abtrennung selbst trifft diese fachliche Unterscheidung bewusst NICHT
# (keine Fahrzeug-/Code-Allowlist); das bleibt Sache der SEPARATEN
# Generations-Bestätigung weiter unten.
check("Bereits bestehende Ausschlussliste (GT, Leistungs-/Kraftstoffkürzel) "
      "bleibt unverändert wirksam -- keine neue Allowlist nötig",
      parse_core_model("MX-5 GT") == ("MX-5 GT", None), str(parse_core_model("MX-5 GT")))


async def _ident(marke, modell, baujahr, motor, quellen):
    z = _ziel(marke, modell, baujahr, motor, None)
    ident, abgelehnt = werte_identitaet_aus(quellen, z)
    return ident, abgelehnt, z


async def run():
    # Keine Information geht still verloren: der Auxiliary Candidate landet
    # in z["auxiliary_code"] (reichert Queries an), auch wenn keine Quelle
    # ihn nennt -- UND die Core-Identität bleibt trotzdem belegt (fehlender
    # Zusatzcode schließt eine passende Core-Model-Quelle NICHT aus).
    quelle_ohne_trim = [
        treffer("https://www.renault.de/clio", "Renault Clio",
                "Der Renault Clio ist ein Kleinwagen aus Frankreich."),
        treffer("https://www.auto-motor-und-sport.de/renault-clio", "Renault Clio im Test",
                "Der Renault Clio hat verschiedene Motorisierungen zur Auswahl."),
    ]
    ident, abgelehnt, z = await _ident("Renault", "Clio RS", 2019, None, quelle_ohne_trim)
    check("'Clio RS': Core-Identität belegt, OBWOHL keine Quelle 'RS' nennt "
          "(kein Information-Loss: auxiliary_code bleibt im ziel-Dict erhalten)",
          ident.belegt is True and z.get("auxiliary_code") == "RS",
          f"belegt={ident.belegt} aux={z.get('auxiliary_code')}")
    check("'Clio RS': 'RS' wird NICHT automatisch zur bestätigten Generation, "
          "nur weil keine Quelle widerspricht (reine Abwesenheit beweist nichts)",
          ident.generation is None, str(ident.generation))
    check("'Clio RS': kein 'modell_fehlt' auf den Quellen (Core-Match allein genügt)",
          all(a.get("grund") != "modell_fehlt" for a in abgelehnt), str(abgelehnt))

    # Explizit widersprechende Angabe bleibt erkennbar: eine Quelle, die klar
    # ein ANDERES Modell beschreibt, darf nicht über unscharfes Matching
    # zusammengeführt werden.
    quelle_fremdmodell = [
        treffer("https://www.renault.de/megane", "Renault Megane",
                "Der Renault Megane ist ein Kompaktwagen aus Frankreich."),
        treffer("https://www.auto-motor-und-sport.de/renault-megane", "Renault Megane im Test",
                "Der Renault Megane hat verschiedene Motorisierungen zur Auswahl."),
    ]
    ident_fremd, _, _ = await _ident("Renault", "Clio RS", 2019, None, quelle_fremdmodell)
    check("'Clio RS' gegen reine Megane-Quellen: KEINE Zusammenführung fremder "
          "Modellnamen über unscharfes Matching -- Identität bleibt unbelegt",
          ident_fremd.belegt is False, str(ident_fremd))


asyncio.run(run())

print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE ALIGNMENT-ALPHABETISCHE-ZUSAETZE-GEGENPRUEFUNGEN GRUEN")

# INZWISCHEN GESCHLOSSEN (Fortsetzungsauftrag, Runde 3 -- "verbleibende
# semantische Generationslücke"): der hier dokumentierte offene Punkt bestand
# darin, dass eine kurze alphabetische Leistungsvariante (z.B. "RS") bei einer
# Quelle mit generischer Kontextformulierung ("..., diese Generation, ...",
# optional PLUS einem zufällig passenden Produktionsfenster) denselben
# Bestätigungspfad wie ein echter Chassiscode auslöste -- `_generationen_
# pruefen()` unterschied "echte Chassisgeneration" nicht von "Leistungs-/
# Ausstattungsvariante" (reproduziert am Porsche-"911 GT3"-Fall). Jetzt
# verlangt die Rollenbestätigung selbst entweder eine strukturelle
# Generationszählung ("Modell IV (CODE)") ODER eine eindeutige Bezeichnung
# ("Baureihe …", "Chassis-Code …", "Werkscode …", "Codename …", "interne
# Bezeichnung …") -- ein vages Wort wie "Generation" (mit oder ohne
# Jahresfenster) reicht für KEINEN Code mehr aus, unabhängig von Ziffern,
# Marke oder Modell. Keine Marken-/Modell-/GT3-Allowlist nötig: siehe
# `_ROLLE_BEZEICHNUNG` in app/technical_research.py und die volle Kette in
# test_generation_kontext_falschbestaetigung_fix.py (Fall F/G/H) sowie
# test_identity_model_code_split.py (C2/E/F/G/J).
