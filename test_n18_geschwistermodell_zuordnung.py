"""
N18 -- Aussagen über ein ANDERES Modell derselben Familie (Geschwistermodell).

Ursache: `_ziel()` trennt eine Zusatzkennung ab ("RC 300h" -> Kern "RC" +
"300h", "RC 200t" -> "RC" + "200t"). Alle Zuordnungsprüfungen liefen am Kern
"RC" -- jeder Satz mit "RC F" galt als Zielnennung, jeder RC-Titel als stark.
  * 477 PS beim RC 300h/RC 200t: Satz "im RC F mit V8-Motor und 477 PS"
    (aufgezeichneter ADAC-Eintrag 252101) bzw. "Der Lexus RC F fährt mit 477 PS"
    (SYNTHETISCHES Mehrmodell-Dokument).
  * "F" als Generation beim RC 200t: `_generationscodes(r, "RC")` liest das
    Kürzel hinter dem Kern ("RC F") als Code. Wurde bisher nur mangels
    Generationskontext verworfen, nicht wegen der Zuordnung.
  * Das RC-200t-Datenblatt zählte beim Ziel RC 300h voll (245 PS, XC1, Benzin).
Fix: Prüfung gegen die VOLLE Bezeichnung (Kern + Zusatzkennung), nur in den
Identitäts-Claims (`_identitaets_claims`, `_geschwistermodell_lokal`).

    python test_n18_geschwistermodell_zuordnung.py
"""
import json
import os
import socket
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="n18_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


socket.socket.connect = _gesperrt
socket.create_connection = _gesperrt

from app.kaufaktionen import build_kaufaktionen  # noqa: E402
from app.kaufcheck_bericht import bericht  # noqa: E402
from app.models import KaufCheckRequest, PriceAssessment  # noqa: E402
from app.technical_research import (  # noqa: E402
    _generationen_pruefen, _geschwistermodell_lokal, _identitaets_claims, _ziel, werte_identitaet_aus,
)
from app.vehicle_identity import VehicleIdentity  # noqa: E402

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
ADAC_200T = t(SNIP_200T["url"], SNIP_200T["title"], REC["extrakt"])            # echter Volltext
MEHR = t("https://www.autobild.de/artikel/lexus-rc-vergleich.html",             # SYNTHETISCH
         "Lexus RC F und RC 300h im Vergleich",
         "Der Lexus RC F fährt mit 477 PS als Benziner, reiner Verbrenner mit Hinterradantrieb. "
         "Der Lexus RC 300h ist ein Vollhybrid mit 223 PS.")


def ziel(modell, ps=None):
    z = _ziel("Lexus", modell, 2017, None, None)
    z["leistung_ps"] = ps
    return z


def claims(r, z):
    cl = _identitaets_claims(r, z)
    _generationen_pruefen(cl, z.get("auxiliary_code"))
    return {(c["feld"], str(c["wert"])) for c in cl if not c.get("verworfen")}, cl


# ── Ursache sichtbar ─────────────────────────────────────────────────────────
z300 = ziel("RC 300h")
check("U) _ziel() zerlegt 'RC 300h' in Kern 'RC' + Zusatz '300h'",
      z300["modell"] == "RC" and z300.get("auxiliary_code") == "300h", str((z300["modell"], z300.get("auxiliary_code"))))
check("U) 'im RC F mit V8-Motor und 477 PS' ist für RC 300h/RC 200t ein Geschwistermodell-Satz",
      _geschwistermodell_lokal("Den gibt es in diesem Auto auch, im RC F mit V8-Motor und 477 PS.", "RC 300h")
      and _geschwistermodell_lokal("Den gibt es in diesem Auto auch, im RC F mit V8-Motor und 477 PS.", "RC 200t"))
check("U) für Ziel RC F ist derselbe Satz Zielnennung (kein Geschwister)",
      not _geschwistermodell_lokal("Den gibt es in diesem Auto auch, im RC F mit V8-Motor und 477 PS.", "RC F"))
check("U) Jahreszahl/Generationszählung/eigener Code sind keine Geschwister",
      not _geschwistermodell_lokal("Der Lexus RC 2017 im Test.", "RC 300h")
      and not _geschwistermodell_lokal("Der Golf VII GTI.", "Golf GTI")
      and not _geschwistermodell_lokal("Der Lexus RC F hat 477 PS.", "RC F USC10"))

# ── Ziel RC 300h ─────────────────────────────────────────────────────────────
c300_adac, _ = claims(ADAC_200T, z300)
c300_mehr, _ = claims(MEHR, z300)
check("300h-1) RC-200t-Datenblatt liefert für RC 300h nichts (vorher: 245/477 PS, XC1, Benzin)",
      not c300_adac, str(sorted(c300_adac)))
check("300h-2) Mehrmodell: keine fremden 477 PS, eigene 223 PS + Vollhybrid bleiben",
      ("horsepower", "477") not in c300_mehr
      and {("horsepower", "223"), ("powertrain", "HEV")} <= c300_mehr, str(sorted(c300_mehr)))

# ── Ziel RC 200t ─────────────────────────────────────────────────────────────
z200 = ziel("RC 200t")
c200, roh200 = claims(ADAC_200T, z200)
check("200t-1) keine fremden 477 PS aus dem RC-F-Satz",
      ("horsepower", "477") not in c200, str(sorted(c200)))
check("200t-2) 'F' entsteht gar nicht mehr als Generationskandidat (auch nicht verworfen)",
      not any(c["feld"] == "generation" and c["wert"] == "F" for c in roh200))
check("200t-3) eigene Daten bleiben: 245 PS, Benzin, Motorcode 8AR-FTS, XC1",
      {("horsepower", "245"), ("fuel", "benzin"), ("generation", "XC1")} <= c200
      and any(c["feld"] == "engine_code" and c["wert"] == "8AR-FTS" for c in roh200), str(sorted(c200)))
check("200t-4) Mehrmodell (RC F / RC 300h) liefert dem RC 200t nichts",
      not claims(MEHR, z200)[0], str(sorted(claims(MEHR, z200)[0])))

# ── Ziel RC F (unverändert) ──────────────────────────────────────────────────
zf = ziel("RC F", 477)
cf_mehr, _ = claims(MEHR, zf)
check("F-1) RC F behält seine Mehrmodell-Angaben (477 PS, Benzin, Verbrenner) ohne Hybrid",
      {("horsepower", "477"), ("fuel", "benzin"), ("powertrain", "ICE")} <= cf_mehr
      and ("powertrain", "HEV") not in cf_mehr, str(sorted(cf_mehr)))
check("F-2) die dem RC F zugeordnete ADAC-Aussage (477 PS) bleibt",
      ("horsepower", "477") in claims(ADAC_200T, zf)[0])


# ── bis zum sichtbaren Bericht (Ziel RC 300h, ohne PS-Vorgabe) ───────────────
def bericht_zeilen(z, quellen, req_modell, ps=None):
    web, _ = werte_identitaet_aus(quellen, z)
    v = VehicleIdentity(make="Lexus", model=req_modell, year=2017, horsepower=ps, fuel=None)
    v.field_evidence = ({"horsepower": {"verification_state": "user_only", "confirmed_by": [],
                                        "raw_user_value": ps}} if ps else {})
    v.apply_web_evidence(type("W", (), {"identitaet": web})())
    req = KaufCheckRequest(marke="Lexus", modell=req_modell, baujahr=2017, leistung_ps=ps)
    text = bericht(req, v, None, None, [], build_kaufaktionen(req, None, None, []), [],
                   "kaufen_nach_besichtigung", PriceAssessment(), False, None, None, ["Webquellen"])
    return web, [x for x in text.splitlines()
                 if x.startswith(("- Leistung", "- Kraftstoff", "- Generation", "- Antriebsart"))]


# Nutzer tippt beim RC 300h fälschlich 477 PS: die fremde RC-F-Angabe darf das
# nicht bestätigen (vorher: adac.de + autobild.de -> "bestätigt durch Webquellen").
web300, zeilen300 = bericht_zeilen(ziel("RC 300h", 477), [ADAC_200T, MEHR], "RC 300h", ps=477)
print("   Bericht RC 300h (Nutzerangabe 477 PS):", *zeilen300, sep="\n     ")
leistung300 = next(z for z in zeilen300 if z.startswith("- Leistung"))
check("B-300h) sichtbarer Bericht: 477 PS bleibt Nutzerangabe, nicht durch Webquellen bestätigt; "
      "kein Benzin aus RC-200t/RC-F-Angaben",
      "nicht unabhängig bestätigt" in leistung300 and "Webquellen" not in leistung300
      and web300.leistung_ps is None and web300.kraftstoff is None, str(zeilen300))
web200, zeilen200 = bericht_zeilen(ziel("RC 200t", 477), [ADAC_200T, MEHR], "RC 200t", ps=477)
print("   Bericht RC 200t (Nutzerangabe 477 PS):", *zeilen200, sep="\n     ")
check("B-200t) sichtbarer Bericht: keine Generation 'F', 477 PS nicht bestätigt",
      web200.generation != "F" and web200.leistung_ps is None
      and "nicht unabhängig bestätigt" in next(z for z in zeilen200 if z.startswith("- Leistung")),
      str(zeilen200))

print()
if _FEHLER:
    print("FEHLER:", _FEHLER)
    raise SystemExit(1)
print("ALLE OK")
