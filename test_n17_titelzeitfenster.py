"""
N17 -- Zeitfenster der Ausführung aus Titel/Suchauszug.

Aufgezeichnet (tests/fixtures/live_rollenbeleg_treffer_2026-11-08.json): der Suchauszug
ADAC 339429 hat nur den Titel "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25)" und
eine Motor-Tabelle, aber NICHT die Felder Modellstart/Modellende. Der Volltext enthält sie
(Modellstart 08/24, Modellende 06/25) und trennt davon "Baureihe RC (UXC1) (12/14 - 06/25)".
Vorher verwarf nur der Volltext die Angaben für 2017, der Auszug trug Kraftstoff Benzin.

    python test_n17_titelzeitfenster.py
"""
import json
import os
import socket
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="n17z_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


socket.socket.connect = _gesperrt
socket.create_connection = _gesperrt

from app.technical_research import (  # noqa: E402
    _datenblatt_zeitraum, _identitaets_claims, _titel_zeitfenster, _ziel, werte_identitaet_aus,
)

_FEHLER: list[str] = []


def check(name, bedingung, detail=""):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def t(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


FIX = Path(__file__).parent / "tests" / "fixtures"
REC = json.loads((FIX / "live_rollenbeleg_treffer_2026-11-08.json").read_text(encoding="utf-8"))
SNIPPET = next(h for g in REC["treffer"] for h in g["hits"] if h["url"].endswith("/339429"))
VOLL_TXT = (FIX / "adac_lexus_rc_f_seitentext_2026-11-08.txt").read_text(encoding="utf-8")
VOLL = t(SNIPPET["url"], SNIPPET["title"], VOLL_TXT)
LEXUS_PDF = t("https://www.lexus.de/content/dam/lexus/nmsc/germany/lexus-besitzer/"
              "fahrzeuginformationen/RCF_tcm-3150-1284184.pdf", "Lexus RC F Rettungskarte",
              "Lexus RC F\n(UXC1 (EU, M), ab 09/2014)\nStand: 10/2014\nLexus RC F (UXC1 (EU, ab 09/2014)\n"
              "12V Spannungsversorgung trennen\nHinweise zum Transport\nZugang 12V Batterie\n"
              "Lexus RC F\n(UXC1 (EU, M), ab 09/2014)")
Z17 = _ziel("Lexus", "RC F", 2017, "5.0 V8", None)
Z17["kraftstoff"] = "Benzin"
Z17["leistung_ps"] = 477


def felder(r, ziel=Z17):
    return {(c["feld"], str(c["wert"])) for c in _identitaets_claims(r, ziel)
            if c["feld"] != "generation" and not c.get("verworfen")}


# ── A) Zuordnung der Zeitangaben im Text ────────────────────────────────────
check("A1) Auszug: Titel-Fenster 08/24-06/25 (Ausführung); kein Modellstart-Feld vorhanden",
      _titel_zeitfenster(SNIPPET["title"]) == (2024, 2025)
      and _datenblatt_zeitraum(SNIPPET["title"] + ". " + SNIPPET["content"]) is None)
check("A2) Volltext: Modellstart/Modellende 2024-2025 getrennt von 'Baureihe RC (UXC1) (12/14 - 06/25)'",
      _datenblatt_zeitraum(VOLL["title"] + ". " + VOLL["content"]) == (2024, 2025)
      and "Baureihe\nRC (UXC1) (12/14 - 06/25)" in VOLL_TXT.replace("\n\n", "\n"))

# ── B) Auszug: 2024/25-Daten tragen für 2017 nichts ──────────────────────────
alt = felder(SNIPPET)
print("   Auszug RC F 2017:", sorted(alt))
check("B1) Auszug 2024/25 liefert für 2017 keinen technischen Beleg (Kraftstoff, Antrieb, Leistung, ...)",
      alt == set(), str(sorted(alt)))
ident, _ = werte_identitaet_aus([SNIPPET, t("https://www.autobild.de/a.html", "Lexus RC F (2017) im Test",
                                            "Der Lexus RC F (2017) ist ein Benziner mit 477 PS.")], Z17)
check("B2) Kraftstoff wird nicht durch Auszug + eine weitere Quelle bestätigt",
      ident.kraftstoff is None, str(ident.feldwerte.get("fuel")))

# ── C) passende Ausführung als Gegenprobe ────────────────────────────────────
PASSEND = t("https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/111111",
            "Lexus RC F Automatik (12/14 - 12/17): Technische Daten | ADAC",
            "| Motorcode | 2UR-GSE | Leistung maximal 477 PS | Kraftstoffart | Super | Antriebsart | Hinterrad |")
pas = felder(PASSEND)
check("C) Ausführung 12/14-12/17 deckt 2017: Angaben bleiben (Benzin, 477 PS, Heck)",
      {("fuel", "benzin"), ("horsepower", "477"), ("drivetrain", "Heck")} <= pas, str(sorted(pas)))

# ── D) Auszug ohne eindeutiges Zeitfenster bleibt offen (nicht verworfen) ────
OHNE = t(PASSEND["url"], "Lexus RC F Automatik: Technische Daten | ADAC", PASSEND["content"])
ZWEI = t(PASSEND["url"], "Lexus RC F Automatik (12/14 - 12/17) und Ultimate (08/24 - 06/25) | ADAC", PASSEND["content"])
UNVOLL = t(PASSEND["url"], "Lexus RC F Automatik (12/14 - ) | ADAC", PASSEND["content"])
for name, r in (("kein Fenster", OHNE), ("zwei verschiedene Fenster im Titel", ZWEI), ("unvollständiges Fenster", UNVOLL)):
    check(f"D) {name}: keine Titel-Zeitprüfung (Verhalten wie bisher)",
          _titel_zeitfenster(r["title"]) is None and ("fuel", "benzin") in felder(r), str(sorted(felder(r))))

# ── E) UXC1 mit Volltext + Hersteller-PDF bleibt, 464 PS wandern nicht ───────
ident_e, _ = werte_identitaet_aus([SNIPPET, VOLL, LEXUS_PDF], Z17)
print("   Generation:", ident_e.generation, ident_e.generation_nachweisweg, "| Belege:", ident_e.generation_belege,
      "| PS:", ident_e.leistung_ps)
check("E1) UXC1 bleibt für 2017 (Hersteller-PDF + ADAC-Rollenbeleg)",
      ident_e.generation == "UXC1"
      and {("kennung_herstellerdokument", "lexus.de"), ("baureihe_rollenbeleg", "adac.de")}
      == {(b["rolle"], b["domain"]) for b in ident_e.generation_belege}, str(ident_e.generation_belege))
check("E2) die späteren 464 PS werden nicht übernommen; Kraftstoff/Elektrifizierung offen",
      ident_e.leistung_ps != 464 and ident_e.kraftstoff is None and ident_e.powertrain is None
      and not any(c["wert"] == "464" for c in _identitaets_claims(SNIPPET, Z17) + _identitaets_claims(VOLL, Z17)
                  if c["feld"] == "horsepower" and not c.get("verworfen")),
      f"ps={ident_e.leistung_ps} kraftstoff={ident_e.kraftstoff}")

print()
if _FEHLER:
    print("FEHLER:", _FEHLER)
    raise SystemExit(1)
print("ALLE OK")
