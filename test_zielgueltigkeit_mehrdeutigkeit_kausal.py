"""
Jahresfilter-Mehrdeutigkeits-Folgeauftrag (29.10.2026, Fortsetzung von 272c423).

Befund, der zu diesem Nachweis fuehrte: `_datenblatt_zeitraum()` gab bei
ZWEI VERSCHIEDENEN Modellstart/Modellende-Fenstern in derselben Quelle
schlicht `None` zurueck -- GENAU denselben Wert wie bei einer Quelle OHNE
jede Zeitangabe. In `_identitaets_claims()` wertete

    _ausserhalb_datenblatt = (_datenblatt_fenster is not None and ...)

beide Faelle identisch aus: kein Ausschluss. Eine erkannte, aber nicht
aufloesbare Varianten-Zuordnung (Mehrdeutigkeit) wurde damit WIE eine
unbedenkliche fehlende Zeitangabe behandelt und konnte einen Claim, der
textlich an die FALSCHE (zeitlich nicht passende) Variante gebunden ist,
ungehindert bis zum Konsens durchreichen.

Codepfad (nachvollzogen):
  `_datenblatt_zeitraum()` -> `_ausserhalb_datenblatt`/`_verwurf_grund_
  datenblatt` in `_identitaets_claims()` -> `add()` setzt `verworfen` ->
  `konsens()` ueberspringt NUR Claims mit `verworfen` -> `werte_identitaet_
  aus()` -> `field_evidence` (sichtbarer `verification_state`).

Fix (Commit nach diesem Nachweis): `_datenblatt_zeitraum()` liefert bei
Mehrdeutigkeit das Sentinel `ZEITRAUM_MEHRDEUTIG` (nicht `None`);
`_identitaets_claims()` behandelt das EXPLIZIT wie einen Ausschluss
(eigener Grund `baujahr_datenblatt_mehrdeutig`, zur Diagnose unterscheidbar
vom eindeutigen `baujahr_ausserhalb_datenblatt`), nicht wie eine fehlende
Zeitangabe.

Konstruktion dieses Nachweises (synthetisch, GENERISCH mit erfundener
Marke/Modell, ausdruecklich gekennzeichnet -- keine reale Veroeffentlichung):
EIN Dokument nennt zwei Varianten mit unterschiedlichen Modellstart/
Modellende-Fenstern (01/16-12/17 vs. 08/24-06/25) -- das macht das Dokument
mehrdeutig. Der konkrete `engine_code`-Claim ("BBB222") steht darin NUR bei
der spaeteren, zum Zieljahr 2017 NICHT passenden Variante. Eine ZWEITE,
unabhaengige Quelle OHNE jede Zeitangabe nennt denselben Wert "BBB222" (legitim
unbedenklich, Fall 4 unten) -- NUR durch die Kombination (mehrdeutige Quelle +
unbedenkliche Zweitquelle) erreicht der Wert ueberhaupt das Mindestgewicht,
das zeigt, ob die Mehrdeutigkeit selbst stillschweigend die Freigabe erlaubt.

    python test_zielgueltigkeit_mehrdeutigkeit_kausal.py
"""
from app.technical_research import (
    _datenblatt_zeitraum, _identitaets_claims, _ziel, werte_identitaet_aus,
    ZEITRAUM_MEHRDEUTIG,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url: str, titel: str, inhalt: str) -> dict:
    return {"url": url, "title": titel, "content": inhalt}


ZIEL = dict(marke="Testmarke", modell="Modellz F", baujahr=2017, motor=None)
z = _ziel("Testmarke", "Modellz F", 2017, None, None)

print("=" * 70)
print("3) Mehrdeutige Datensatzzuordnung -- kein beliebiger Wert als "
      "zielgueltig bestaetigt")
print("=" * 70)

# EINE Quelle, ZWEI Varianten mit unterschiedlichen Fenstern. Der engine_code-
# Claim "BBB222" steht NUR bei der spaeteren (08/24-06/25), zum Zieljahr 2017
# nicht passenden Variante "Ultimate".
MEHRDEUTIGE_VERGLEICHSSEITE = treffer(
    "https://www.motor1.com/testmarke/modellz-f/varianten-vergleich",
    "Testmarke Modellz F Varianten im Vergleich: Basis vs. Ultimate (SYNTHETISCHE TEST-FIXTURE)",
    "[SYNTHETISCH] Testmarke Modellz F Basis, Modellstart 01/16 Modellende 12/17. "
    "Testmarke Modellz F Ultimate, Modellstart 08/24 Modellende 06/25, Motorcode "
    "BBB222, Allradantrieb.")
# Zweite, unabhaengige Quelle OHNE jede Zeitangabe -- fuer sich genommen
# unbedenklich (Fall 4), liefert hier NUR das zusaetzliche Gewicht, das noetig
# ist, um die Frage ueberhaupt sichtbar zu machen.
UNDATIERTE_ZWEITQUELLE = treffer(
    "https://www.autozeitung.de/testmarke/modellz-f",
    "Testmarke Modellz F im Datenblatt (SYNTHETISCHE TEST-FIXTURE)",
    "[SYNTHETISCH] Der Testmarke Modellz F hat den Motorcode BBB222.")

check("3.0 Vorbedingung: _datenblatt_zeitraum() erkennt die Vergleichsseite "
      "als MEHRDEUTIG (nicht None/kein Feld)",
      _datenblatt_zeitraum(
          f"{MEHRDEUTIGE_VERGLEICHSSEITE['title']}. {MEHRDEUTIGE_VERGLEICHSSEITE['content']}")
      == ZEITRAUM_MEHRDEUTIG)

claims_mehrdeutig = _identitaets_claims(MEHRDEUTIGE_VERGLEICHSSEITE, z)
engine_code_claims = [c for c in claims_mehrdeutig if c["feld"] == "engine_code"]
check("3.1 engine_code-Claim 'BBB222' aus der mehrdeutigen Quelle ist VERWORFEN "
      "(Grund unterscheidbar von 'kein Zeitraum')",
      bool(engine_code_claims) and all(
          c.get("verworfen") == "baujahr_datenblatt_mehrdeutig" for c in engine_code_claims),
      str(engine_code_claims))

treffer_gesamt = [MEHRDEUTIGE_VERGLEICHSSEITE, UNDATIERTE_ZWEITQUELLE]
identitaet, abgelehnt = werte_identitaet_aus(treffer_gesamt, z)
check("3.2 finaler Evidenzstatus: engine_code bleibt UNBESTAETIGT "
      "(motorcode ist None), obwohl die Kombination aus mehrdeutiger Quelle + "
      "unbedenklicher Zweitquelle OHNE den Fix genuegend Gewicht erreicht "
      "haette, um 'BBB222' faelschlich zu bestaetigen",
      identitaet.motorcode is None, f"motorcode={identitaet.motorcode!r}")
mehrdeutig_in_abgelehnt = [a for a in abgelehnt if a.get("grund") == "baujahr_datenblatt_mehrdeutig"]
check("3.3 der Ausschlussgrund ist in der Ablehnungsliste sichtbar "
      "(Diagnose/Nachvollziehbarkeit erhalten)", bool(mehrdeutig_in_abgelehnt),
      str(mehrdeutig_in_abgelehnt))

print("\n" + "=" * 70)
print("4) Quelle OHNE jede Zeitangabe wird NICHT allein deshalb pauschal "
      "abgelehnt (Kontrollprobe -- dieselbe Zweitquelle alleine)")
print("=" * 70)
check("4.0 Vorbedingung: UNDATIERTE_ZWEITQUELLE hat kein Modellstart/Modellende "
      "-> _datenblatt_zeitraum() liefert None (nicht mehrdeutig, nicht ausserhalb)",
      _datenblatt_zeitraum(
          f"{UNDATIERTE_ZWEITQUELLE['title']}. {UNDATIERTE_ZWEITQUELLE['content']}") is None)
claims_undatiert = _identitaets_claims(UNDATIERTE_ZWEITQUELLE, z)
engine_code_undatiert = [c for c in claims_undatiert if c["feld"] == "engine_code"]
check("4.1 engine_code-Claim der undatierten Quelle bleibt UNVERWORFEN "
      "(fehlende Zeitangabe ist kein Ausschlussgrund)",
      bool(engine_code_undatiert) and all(not c.get("verworfen") for c in engine_code_undatiert),
      str(engine_code_undatiert))

print()
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE MEHRDEUTIGKEITS-KAUSALITAETSTESTS GRUEN")
