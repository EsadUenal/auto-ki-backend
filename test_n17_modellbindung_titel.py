"""
N17 -- technische Angaben eines Datenblatts für ein ANDERES Modell.

Befund am aufgezeichneten ADAC-Eintrag 252101 ("Lexus RC 200t F Sport Automatik
(01/16 - 05/18)"): `_modell_im_titel()`/`_satz_nennt_modell()` prüften das
Zielmodell "RC F" per Mengen-Inklusion {rc, f} <= {rc, 200t, f, sport}. Damit
galt der Titel als "RC F" (ausgerichtet ohne Vorbehalt), und der volle Text
(Kraftstoff, Antrieb, Motorcode 8AR-FTS, 245 PS, ...) lief als RC-F-Claim in die
Konsensbildung.

Fix: mehrteilige Modellnamen müssen zusammenhängend und in Reihenfolge stehen
(`_modell_zusammenhaengend`). Der Treffer ist danach nur noch "schwach"
ausgerichtet; es zählen allein Sätze, die das Zielmodell ausdrücklich nennen.

    python test_n17_modellbindung_titel.py
"""
import json
import os
import socket
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="n17m_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


socket.socket.connect = _gesperrt
socket.create_connection = _gesperrt

from app.technical_research import (  # noqa: E402
    _identitaets_claims, _modell_im_titel, _satz_nennt_modell, _tokens, _ziel,
    ausgerichtet, werte_identitaet_aus,
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
SNIP_200T = next(h for g in REC["treffer"] for h in g["hits"] if "252101" in h["url"])
VOLL_200T = t(SNIP_200T["url"], SNIP_200T["title"], REC["extrakt"])

Z_RCF = _ziel("Lexus", "RC F", 2017, "5.0 V8", None)
Z_RCF["kraftstoff"] = "Benzin"
Z_RCF["leistung_ps"] = 477
Z_200T = _ziel("Lexus", "RC 200t", 2017, "2.0 Turbo", None)


def felder(r, ziel, *namen):
    return sorted({(c["feld"], str(c["wert"])) for c in _identitaets_claims(r, ziel)
                   if c["feld"] in namen and not c.get("verworfen")})


# ── A) RC-200t-Datenblatt gegen Ziel RC F ────────────────────────────────────
check("A0) Reproduktion: Titel-Tokens enthalten {rc, f} gestreut",
      {"rc", "f"} <= _tokens(SNIP_200T["title"]) and "200t" in _tokens(SNIP_200T["title"]))
check("A1) Titel nennt RC F NICHT (zusammenhängend)", not _modell_im_titel(
    _tokens(SNIP_200T["title"]), "RC F", SNIP_200T["title"]))
check("A2) Treffer ist nur noch 'schwach' für RC F (nicht mehr ohne Vorbehalt)",
      ausgerichtet(SNIP_200T, "Lexus", "RC F") == (True, "schwach"),
      str(ausgerichtet(SNIP_200T, "Lexus", "RC F")))
alle = felder(VOLL_200T, Z_RCF, "fuel", "powertrain", "drivetrain", "engine_code", "displacement",
              "generation", "transmission", "horsepower")
print("   RC-F-Claims aus dem RC-200t-Volltext:", alle)
check("A3) keine technischen Angaben des RC 200t für den RC F (Kraftstoff, Elektrifizierung, "
      "Antrieb, Motorcode, Hubraum, Generation, Getriebe)",
      not [x for x in alle if x[0] != "horsepower"], str(alle))
check("A4) die ausdrücklich dem RC F zugeordnete Aussage ('im RC F mit V8-Motor und 477 PS') "
      "bleibt erhalten, nur als niedrigste Stufe",
      alle == [("horsepower", "477")]
      and all(c["tier"] == 3 for c in _identitaets_claims(VOLL_200T, Z_RCF) if c["feld"] == "horsepower"))
ident, _ = werte_identitaet_aus([VOLL_200T], Z_RCF)
check("A5) Konsens: der RC-200t-Eintrag bestätigt für den RC F weder Kraftstoff noch "
      "Elektrifizierung noch Generation",
      ident.kraftstoff is None and ident.powertrain is None and ident.generation is None
      and ident.motorcode is None)

# ── B) RC 200t als korrektes Zielmodell ──────────────────────────────────────
check("B1) für Ziel RC 200t bleibt der Treffer voll ausgerichtet",
      ausgerichtet(SNIP_200T, "Lexus", "RC 200t") == (True, None))
b = felder(VOLL_200T, Z_200T, "fuel", "drivetrain", "engine_code", "transmission")
check("B2) RC 200t: Kraftstoff, Antrieb, Motorcode 8AR-FTS und Getriebe werden wie bisher gelesen",
      {("fuel", "benzin"), ("drivetrain", "Heck"), ("engine_code", "8AR-FTS"),
       ("transmission", "automatik")} <= set(b), str(b))

# ── C) Mehrmodell-Dokument mit sauber getrennten Angaben ─────────────────────
MEHR = t("https://www.autobild.de/artikel/lexus-rc-vergleich.html", "Lexus RC F und RC 300h im Vergleich",
         "Der Lexus RC F fährt mit 477 PS als Benziner, reiner Verbrenner mit Hinterradantrieb. "
         "Der Lexus RC 300h ist ein Vollhybrid mit 223 PS.")
rcf = felder(MEHR, Z_RCF, "horsepower", "fuel", "powertrain", "drivetrain")
print("   RC F aus Mehrmodell:", rcf)
check("C1) Mehrmodell: dem RC F ausdrücklich zugeordnete Angaben bleiben (477 PS, Benzin, Heck, ICE)",
      {("horsepower", "477"), ("fuel", "benzin"), ("drivetrain", "Heck"), ("powertrain", "ICE")} <= set(rcf),
      str(rcf))
check("C2) Mehrmodell: die Hybrid-Angabe des RC 300h wandert nicht in den RC F",
      ("powertrain", "HEV") not in rcf and ("horsepower", "223") not in rcf, str(rcf))
z300 = _ziel("Lexus", "RC 300h", 2017, "2.5 Hybrid", None)
h300 = felder(MEHR, z300, "horsepower", "powertrain")
check("C3) Gegenprobe: für den RC 300h gelten dessen Angaben (223 PS, Vollhybrid)",
      ("horsepower", "223") in h300 and ("powertrain", "HEV") in h300, str(h300))

# ── D) Schreibweisen des Zielmodells bleiben gleich zugeordnet ───────────────
for titel in ("Lexus RC F Ultimate Edition Automatik", "Lexus RC-F Test", "Lexus RCF Fahrbericht",
              "RC F (2014) Daten"):
    check(f"D) Titel '{titel}' nennt RC F", _modell_im_titel(_tokens(titel), "RC F", titel))
check("D) Satz mit 'RC 200t F Sport' nennt RC F nicht",
      not _satz_nennt_modell("Lexus RC 200t F Sport Automatik hat 245 PS.", "RC F"))
check("D) Satz mit 'Lexus RC F hat 477 PS' nennt RC F",
      _satz_nennt_modell("Der Lexus RC F hat 477 PS.", "RC F"))

print()
if _FEHLER:
    print("FEHLER:", _FEHLER)
    raise SystemExit(1)
print("ALLE OK")
