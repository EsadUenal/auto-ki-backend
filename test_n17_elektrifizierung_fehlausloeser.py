"""
N17 -- Elektrifizierungs-Claims aus Tabellenbeschriftung und Navigation.

Befund am aufgezeichneten ADAC-Text (tests/fixtures/..._2026-11-08):
  * Snippet "Einbauposition / Motorbauart (Verbrennungsmotor)" -> Claim ICE
    (Regel `verbrenn\\w*` in `_ICE_WORT`, app/kraftstoff_powertrain.py)
  * Volltext-Navigation "Elektromobilität" -> Claim BEV
    (Regel `elektro\\w*` in `_BEV_WORT`)
Beides beschreibt nicht die Elektrifizierung des Fahrzeugs. Fix: nur für die
Identitäts-Claims (`_identitaets_claims`) werden Bauteil-Qualifier in Klammern
und Themenbegriffe neutralisiert; Werte und Fließtextaussagen bleiben.

Teil D nutzt einen SYNTHETISCHEN Hochvolt-Rückruf (kein KBA 12358 -- dessen
Inhalt liegt lokal nicht vor).

    python test_n17_elektrifizierung_fehlausloeser.py
"""
import os
import socket
import tempfile

_tmp = tempfile.mkdtemp(prefix="n17_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


socket.socket.connect = _gesperrt
socket.create_connection = _gesperrt

import json  # noqa: E402
from pathlib import Path  # noqa: E402

from app.kraftstoff_powertrain import powertrain_aus_freitext  # noqa: E402
from app.recall_filter import rueckruf_applicability  # noqa: E402
from app.technical_research import (  # noqa: E402
    _extrahiere_fakten, _identitaets_claims, _ziel, werte_identitaet_aus,
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
HITS = {h["url"]: h for g in REC["treffer"] for h in g["hits"]}
SNIPPET = HITS["https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429"]
VOLL_TEXT = (FIX / "adac_lexus_rc_f_seitentext_2026-11-08.txt").read_text(encoding="utf-8")
VOLL = t(SNIPPET["url"], VOLL_TEXT.splitlines()[0], VOLL_TEXT)
NAVIGATION = "\n".join(VOLL_TEXT.splitlines()[:40])

Z = _ziel("Lexus", "RC F", 2017, "5.0 V8, Heckantrieb", None)
Z["kraftstoff"] = "Benzin"
Z["leistung_ps"] = 477


def pt_claims(r, ziel=Z):
    return [(c["wert"], c.get("verworfen")) for c in _identitaets_claims(r, ziel) if c["feld"] == "powertrain"]


# ── A) die beiden echten Fehlauslöser, getrennt ──────────────────────────────
check("A0) Reproduktion: Rohregel liest das Snippet als ICE",
      powertrain_aus_freitext(SNIPPET["title"] + " " + SNIPPET["content"]) == "ICE")
check("A1) Snippet 'Motorbauart (Verbrennungsmotor)' erzeugt keinen Elektrifizierungs-Claim mehr",
      pt_claims(SNIPPET) == [], str(pt_claims(SNIPPET)))
# N17-Folgefix (BEV durch Bauteilwoerter): das unbeschraenkte `elektro\w*` ist aus
# `_BEV_WORT` entfernt -- die Rohregel liest das Themenwort "Elektromobilität" nicht
# mehr als Fahrzeugklasse. Die frühere Reproduktions-Vorbedingung ist damit an der
# Ursache behoben; A3 sichert weiterhin den Claim-Pfad.
check("A2) Rohregel liest die ADAC-Navigation ('Elektromobilität') nicht mehr als BEV",
      "Elektromobilität" in NAVIGATION and powertrain_aus_freitext(NAVIGATION) != "BEV")
nav = t(SNIPPET["url"], VOLL["title"], NAVIGATION)
check("A3) Navigation allein erzeugt keinen Elektrifizierungs-Claim mehr",
      pt_claims(nav) == [], str(pt_claims(nav)))
check("A4) voller ADAC-Text: kein Elektrifizierungs-Claim (auch kein verworfener)",
      pt_claims(VOLL) == [], str(pt_claims(VOLL)))

# ── B) explizite Belege bleiben erkannt (Gegenprobe) ─────────────────────────
for name, inhalt, erwartet in (
        ("Verbrenner (Fließtext)", "Der Lexus RC F (2017) ist ein reiner Verbrenner mit 477 PS.", "ICE"),
        ("Verbrenner (Tabellenwert)", "Lexus RC F | Motorart | Verbrennungsmotor | Leistung 477 PS", "ICE"),
        ("Vollhybrid (Tabellenwert)", "Lexus RC F | Antriebsart | Vollhybrid | Leistung 477 PS", "HEV"),
        ("Plug-in-Hybrid (Fließtext)", "Der Lexus RC F (2017) ist ein Plug-in-Hybrid mit 477 PS.", "PHEV"),
        ("Elektro (Tabellenwert)", "Lexus RC F | Motorart | Elektro | Leistung 477 PS", "BEV")):
    r = t("https://www.autobild.de/artikel/lexus-rc-f-2017.html", "Lexus RC F (2017) Daten", inhalt)
    werte = [w for w, v in pt_claims(r) if not v]
    check(f"B) {name} -> {erwartet}", werte == [erwartet], str(werte))
for name, inhalt in (("Benzin", "Der Lexus RC F (2017) ist ein Benziner mit 477 PS."),
                     ("V8 + Motorcode", "Lexus RC F (2017): V8-Saugmotor 2UR-GSE, 477 PS.")):
    r = t("https://www.autobild.de/artikel/lexus-rc-f-2017.html", "Lexus RC F (2017) Daten", inhalt)
    check(f"B) {name} allein -> keine Elektrifizierung", pt_claims(r) == [], str(pt_claims(r)))

# ── C) ein Fehlauslöser wertet eine einzelne weitere Quelle nicht auf ────────
EINZEL_ICE = t("https://www.autobild.de/artikel/lexus-rc-f-2017.html", "Lexus RC F (2017) im Test",
               "Der Lexus RC F (2017) ist ein reiner Verbrenner mit 477 PS.")
ident_c, _ = werte_identitaet_aus([SNIPPET, EINZEL_ICE], Z)
check("C) Snippet + eine ICE-Quelle -> Elektrifizierung bleibt offen (vorher ICE über 2 Domains)",
      ident_c.powertrain is None and "powertrain" not in ident_c.feldwerte,
      f"powertrain={ident_c.powertrain!r} feldwerte={ident_c.feldwerte.get('powertrain')!r}")


# ── D) SYNTHETISCHER Hochvolt-Rückruf + Inverter-Wartungszeile ───────────────
def identitaet(web):
    v = VehicleIdentity(make="Lexus", model="RC F", year=2017, fuel="Benzin")
    v.field_evidence = {"fuel": {"verification_state": "user_only", "confirmed_by": [],
                                 "raw_user_value": "Benzin"}}
    v.apply_web_evidence(type("W", (), {"identitaet": web})())
    return v


HV_RUECKRUF = {"mangel": "Hochvolt-Inverter des Hybridsystems kann ausfallen (SYNTHETISCH).",
               "abhilfe": "Inverter prüfen und ggf. ersetzen.", "betroffene_baujahre": "2015-2018",
               "baureihe_id": None}
v_offen = identitaet(ident_c)
app_offen = rueckruf_applicability(HV_RUECKRUF, True, "", None, marke="Lexus", identity=v_offen)
print("D) Hochvolt-Rückruf bei offener Elektrifizierung:", app_offen[0], "|", app_offen[2])
check("D1) HV-Rückruf bleibt sichtbar zur FIN-Klärung ('unclear'), nicht entfernt",
      app_offen[0] == "unclear" and "FIN" in app_offen[2], str(app_offen))
v_ice = VehicleIdentity(make="Lexus", model="RC F", year=2017, fuel="Benzin", powertrain="ICE")
v_ice.field_evidence = {}
check("D2) Gegenprobe: mit (falschem) ICE-Konsens wäre er 'incompatible' und ausgeblendet",
      rueckruf_applicability(HV_RUECKRUF, True, "", None, marke="Lexus", identity=v_ice)[0] == "incompatible")

_SRC = (Path(__file__).parent / "test_wartung_technische_bedingung_vollpfad.py").read_text(encoding="utf-8")
_BLOCK_NS: dict = {"treffer": t}
exec(_SRC[_SRC.index("_SICHERHEIT_BLOCK = ("):_SRC.index("# ═══")], _BLOCK_NS)
WARTUNG = _BLOCK_NS["_SICHERHEIT_TREFFER"]   # echter Lexus-Serviceheft-Text (lex-sw-00de.pdf)
abgelehnt: list[dict] = []
fakten = _extrahiere_fakten([WARTUNG], "wartung", marke="Lexus", modell="RC F", baujahr=2017,
                            abgelehnt=abgelehnt, fuel_ziel="benzin",
                            powertrains_ziel=(frozenset({ident_c.powertrain}) if ident_c.powertrain else None))
check("D3) offene Elektrifizierung: kein Inverter-Wartungspunkt (also auch keine Frage/kein Nachweis)",
      not any("inverter" in (f.bauteil or "").lower() for f in fakten)
      and any(a.get("grund") == "wartung_technische_bedingung_offen"
              and "inverter" in (a.get("satz") or "").lower() for a in abgelehnt),
      f"bauteile={[f.bauteil for f in fakten]}")
check("D4) Kontrolle: derselbe Block liefert ohne Inverter weiterhin Wartungspunkte",
      len(fakten) > 0, str([f.bauteil for f in fakten]))

print()
if _FEHLER:
    print("FEHLER:", _FEHLER)
    raise SystemExit(1)
print("ALLE OK")
