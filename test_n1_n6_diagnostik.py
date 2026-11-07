"""
ENFAL-Reparaturpaket (N1/N6), 2026-11 -- reine Diagnostik-Ergänzung, KEINE
Verhaltensänderung an Scope-Gates, Confirm-Schwellen oder Reporting.

N1 (Generation unbekannt): bisher feuerte `research_phase=identitaet_volltext`
NUR, wenn `_identitaet_nachlade_kandidaten()` mindestens eine URL auswählte --
blieb die Liste leer, verschwand die Information spurlos, ob (a) kein Treffer
überhaupt eine URL trug, (b) der Treffer bereits brauchbaren Volltext
mitbrachte, oder (c) kein Treffer das Dokumenttyp-Gate erfüllte. Dieser Test
prüft die neue, reine Zähl-Funktion `_identitaet_nachlade_diagnose()`, die
diese drei Fälle unterscheidbar macht, OHNE die bestehende Auswahl
(`_identitaet_nachlade_kandidaten()`) zu verändern.

N6 (Motor-Kern unbestätigt): bisher gab es KEINE Logzeile zur Motorkern-
Prüfung (`_motor_quelle_passt()` in `werte_identitaet_aus()`) -- ob
`engine_name` unbestätigt blieb, weil keine Quelle die Kern-Tokens nannte,
oder weil Belege vorlagen, aber keine Tier<=2-Quelle/kein zweites Vorkommen
erreicht wurde, war nicht unterscheidbar. Dieser Test prüft per
`caplog`-Äquivalent (Capture via `logging`-Handler), dass die neue
Diagnostik-Zeile beide Fälle (0 Kernbelege vs. Kernbeleg ohne ausreichenden
Tier/Domainzahl) korrekt unterschiedlich beziffert -- UND dass
`feldwerte.get("engine_name")` (das tatsächliche Verhalten) in beiden Fällen
unverändert `None`/fehlend bleibt (kein Behaviour-Shift durch das neue Log).

    python test_n1_n6_diagnostik.py
"""
import logging

from app.technical_research import (
    _identitaet_nachlade_diagnose,
    _identitaet_nachlade_kandidaten,
    _ziel,
    werte_identitaet_aus,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


# ── N1: a) kein Treffer trägt überhaupt eine URL ────────────────────────────
treffer_ohne_url = [{"title": "x", "content": "y"}]
diag_a = _identitaet_nachlade_diagnose(treffer_ohne_url, marke="Lexus")
check("N1a) Treffer ohne URL -> ohne_url=1, gate_erfuellt=0",
      diag_a["ohne_url"] == 1 and diag_a["gate_erfuellt"] == 0, str(diag_a))
check("N1a) Auswahl bleibt unverändert leer",
      _identitaet_nachlade_kandidaten(treffer_ohne_url, marke="Lexus") == [])

# ── N1: b) Treffer hat bereits brauchbaren Volltext ─────────────────────────
treffer_mit_volltext = [{
    "url": "https://www.lexus.de/rettungsdatenblatt-rc.pdf",
    "raw_content": "x" * 500,  # hat_brauchbaren_raw_content() prüft `raw_content`, nicht `content`
}]
diag_b = _identitaet_nachlade_diagnose(treffer_mit_volltext, marke="Lexus")
check("N1b) Treffer mit bereits ausreichendem Volltext -> bereits_volltext>=1, gate_erfuellt=0",
      diag_b["bereits_volltext"] >= 1 and diag_b["gate_erfuellt"] == 0, str(diag_b))

# ── N1: c) Treffer existiert, aber Dokumenttyp-Gate lehnt ab (falsche Domain/
# falscher Pfad) -- das ist der "abgelehnter Kandidat"-Fall aus dem Auftrag.
treffer_falsche_dokumentart = [{
    "url": "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/",
    "content": "kurz",
}]
diag_c = _identitaet_nachlade_diagnose(treffer_falsche_dokumentart, marke="Lexus")
check("N1c) Treffer mit falschem Dokumenttyp -> falsche_dokumentart=1, gate_erfuellt=0",
      diag_c["falsche_dokumentart"] == 1 and diag_c["gate_erfuellt"] == 0, str(diag_c))

# ── N1: d) ein echter Kandidat erfüllt das Gate -> gate_erfuellt=1, und die
# Auswahl (unverändertes Verhalten) wählt ihn tatsächlich aus.
treffer_kandidat = [{
    "url": "https://www.lexus.de/rettungsdatenblatt-rc.pdf",
    "content": "kurz",
}]
diag_d = _identitaet_nachlade_diagnose(treffer_kandidat, marke="Lexus")
check("N1d) echter Kandidat -> gate_erfuellt=1",
      diag_d["gate_erfuellt"] == 1, str(diag_d))
check("N1d) Auswahl (unverändert) wählt denselben Kandidaten",
      _identitaet_nachlade_kandidaten(treffer_kandidat, marke="Lexus") ==
      ["https://www.lexus.de/rettungsdatenblatt-rc.pdf"])

# Die drei leeren Fälle (a/b/c) sind jetzt an UNTERSCHIEDLICHEN Zählern
# erkennbar, nicht mehr an derselben, nicht aussagekräftigen "leer"-Lücke.
check("N1) a/b/c sind an unterschiedlichen Zählern unterscheidbar, nicht an derselben Leere",
      {k: v for k, v in diag_a.items() if k != "treffer" and v}
      != {k: v for k, v in diag_b.items() if k != "treffer" and v}
      != {k: v for k, v in diag_c.items() if k != "treffer" and v})


# ── N6: Motorkern-Prüfung -- Logdiagnostik unterscheidet "kein Kernbeleg"
# von "Kernbeleg vorhanden, aber Schwelle verfehlt", OHNE das Ergebnis
# (`engine_name` bleibt in beiden Fällen unbestätigt) zu verändern.
class _LogCatcher(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[str] = []

    def emit(self, record):
        self.records.append(record.getMessage())


def _motorkern_log(ziel: dict, treffer: list[dict]) -> tuple[str | None, dict]:
    catcher = _LogCatcher()
    logger = logging.getLogger("app.technical_research")
    alt_level = logger.level
    logger.addHandler(catcher)
    logger.setLevel(logging.INFO)
    try:
        identitaet, _ = werte_identitaet_aus(treffer, ziel)
    finally:
        logger.removeHandler(catcher)
        logger.setLevel(alt_level)
    zeile = next((r for r in catcher.records if r.startswith("Motorkern-Prüfung")), None)
    return zeile, identitaet.feldwerte


# Identitäts-Grundlage: derselbe, bereits an anderer Stelle geprüfte, reale
# RC-F-Quellensatz (ADAC+carwiki.de+AMS, s. test_rc_f_lexus_originalbeleg.py
# Zeile 177 -- dort mit `werte_identitaet_aus()` nachweislich `belegt=True`)
# -- dieselben drei Quellen in beiden Fällen, damit NUR der Motorbeleg selbst
# variiert.
def _treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


ADAC = _treffer(
    "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/",
    "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25): Technische Daten | ADAC",
    "Marke Lexus Modell RC F Typ Ultimate Edition Automatik Baureihe RC (UXC1) "
    "(12/14 - 06/25) Herstellerinterne Baureihenbezeichnung UXC1 Motorcode 2UR-GSE "
    "Leistung maximal in PS 464 Antriebsart Hinterrad Getriebeart Automatikgetriebe")
CARWIKI = _treffer(
    "https://carwiki.de/lexus-rc-hsn-tsn-schluesselnummer/",
    "Lexus RC HSN & TSN | Tabelle aller Schluesselnummern",
    "Uebersicht: Alle Schluesselnummern (HSN/TSN) der Lexus RC Baureihen. "
    "Hersteller: Lexus Fahrzeug: RC Baureihe: RC XC1 Karosserie: Coupe "
    "MODELL HSN SCHLUESSELNUMMER TSN SCHLUESSELNUMMER KRAFTSTOFF HUBRAUM LEISTUNG BAUJAHR "
    "Lexus RC F Carbon Automatik 5013 AJM Benzin 4969 cm3 477 PS 2014 bis 2025")
AMS = _treffer(
    "https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
    "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
    "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Leistung 181 PS bis "
    "477 PS. LEXUS RC F TRACK EDITION IM SUPERTEST: Was kann der V8-Sauger auf der "
    "Rennstrecke? LEXUS LC 500 UND RC F: V8-Sportler auf Abschiedstour.")

z_rc_f = _ziel("Lexus", "RC F", 2017, "5.0 V8", None)

# Fall a) KEINE der drei Quellen nennt die Motor-Kern-Tokens ("5.0"+"V8" IM
# SELBEN Text) -- ADAC nennt nur den Motorcode, CARWIKI/AMS nur Hubraum/PS.
zeile_ohne, feldwerte_ohne = _motorkern_log(z_rc_f, [ADAC, CARWIKI, AMS])
check("N6a) Motorkern-Log erscheint auch ohne jeden Kernbeleg",
      zeile_ohne is not None, str(zeile_ohne))
if zeile_ohne:
    check("N6a) quellen_mit_kernbeleg=0 bei fehlendem Beleg",
          "quellen_mit_kernbeleg=0" in zeile_ohne, zeile_ohne)
check("N6a) engine_name bleibt unbestätigt (Verhalten unverändert)",
      "engine_name" not in feldwerte_ohne)

# Fall b) Kernbeleg vorhanden ("5.0 V8" steht im AMS-Text), aber NUR auf EINER
# Domain (auto-motor-und-sport.de) -- ADAC/CARWIKI nennen den Kern weiterhin
# nicht, Schwelle "len(doms) >= 2 ODER Tier 1" bleibt knapp verfehlt (kein
# Tier-1-Hersteller in diesem Fixture).
AMS_MIT_KERN = dict(AMS, content=AMS["content"] + " Der Motor ist ein 5.0 V8.")
zeile_schwach, feldwerte_schwach = _motorkern_log(z_rc_f, [ADAC, CARWIKI, AMS_MIT_KERN])
check("N6b) Motorkern-Log erscheint bei vorhandenem, aber unzureichendem Kernbeleg",
      zeile_schwach is not None, str(zeile_schwach))
if zeile_schwach:
    check("N6b) quellen_mit_kernbeleg>=1, aber bestaetigt=False (Schwelle verfehlt)",
          "quellen_mit_kernbeleg=1" in zeile_schwach and "bestaetigt=False" in zeile_schwach,
          zeile_schwach)
check("N6b) engine_name bleibt unbestätigt (Verhalten unverändert, Schwelle verfehlt)",
      "engine_name" not in feldwerte_schwach)

check("N6) a) und b) sind am Log unterscheidbar (0 vs. >=1 Kernbelege), beide bestaetigt=False",
      zeile_ohne is not None and zeile_schwach is not None
      and "quellen_mit_kernbeleg=0" in zeile_ohne
      and "quellen_mit_kernbeleg=1" in zeile_schwach)

print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE N1/N6-DIAGNOSTIK-TESTS GRUEN")
