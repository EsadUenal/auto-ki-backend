"""
N17-Folgefix: ein Antriebs-BAUTEILWORT ("Elektromotor") belegt keine Fahrzeugklasse BEV
und keine Kraftstoffart Elektro. Prueft Wortebene, Identitaetsbewertung, sichtbare
Ausgabe und Rueckruf-Scope.

Alle Texte unten sind SYNTHETISCH (nur Erkennungslogik, keine Fahrzeugfakten).

    python scripts/test_isolated.py test_n17_bev_bauteilwort.py
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import app.recall_filter as _rf

_rf.get_rueckruf_referenzen_kurz = lambda: []

from app import kaufcheck as kc
from app.kraftstoff_powertrain import fuel_aus_freitext, powertrain_aus_freitext
from app.models import KaufCheckRequest, KaufCheckResponse, TechnischeRecherche
from app.recall_filter import RECALL_NOT_APPLICABLE, RECALL_UNKNOWN, RECALL_VARIANT_POSSIBLE, rueckruf_scope
from app.technical_research import (FixtureTechnicalResearchProvider, recherchiere_technisch,
                                    werte_identitaet_aus, _ziel)
from app.vehicle_identity import VehicleIdentity

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if (detail and not ok) else ""))
    if not ok:
        FAILS.append(name)


def t(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


print("== A: Wortebene (powertrain_aus_freitext / fuel_aus_freitext) ==")
for text, pt, fuel in (
        ("Vollhybrid mit Benzinmotor und Elektromotor", "HEV", "benzin"),
        ("Plug-in-Hybrid mit Elektromotor", "PHEV", None),
        ("Elektroauto", "BEV", "elektro"),
        ("batterieelektrisches Fahrzeug", "BEV", "elektro"),
        ("Das Fahrzeug faehrt vollelektrisch", "BEV", None),
        ("Elektro", "BEV", "elektro"),                                   # Formular-/Feldwert
        ("Elektromotor", None, None),
        ("Benzinmotor und Elektromotor", None, "benzin"),
        ("2.5 Hybrid mit Elektromotor", None, None),
        ("elektrische Servolenkung, Elektro-Fensterheber", None, None),
        ("Electric Power Steering", None, None),
        ("kein Elektroauto", None, None),
        ("nicht als Plug-in-Hybrid erhaeltlich", None, None),
        ("Vollhybrid, kann bis 2 km rein elektrisch fahren", "HEV", None),
        ("Hybrid, faehrt rein elektrisch", None, None),
        ("als Vollhybrid und als Elektroauto erhaeltlich", None, None),
        ("als Mild-Hybrid oder als Plug-in-Hybrid erhaeltlich", None, None),
        ("Mild-Hybrid mit 48V-Bordnetz", "MHEV", None),
        ("reiner Verbrenner", "ICE", None)):
    p, f = powertrain_aus_freitext(text), fuel_aus_freitext(text)
    check(f"A {text!r}: powertrain={pt}", p == pt, str(p))
    check(f"A {text!r}: fuel={fuel}", f == fuel, str(f))

print("\n== B: Identitaetsbewertung (werte_identitaet_aus) ==")
HYBRID_SATZ = t("https://www.toyota-media.de/a/text", "Lexus RC 300h Presseinformation",
                "Der Lexus RC 300h kombiniert einen 2,5 Liter Benzinmotor mit einem Elektromotor "
                "zum Vollhybrid-Antrieb.")
BAUTEIL_SATZ = t("https://www.toyota-media.de/b/text", "Lexus RC 300h Presseinformation",
                 "Im Lexus RC 300h arbeiten ein Benzinmotor und ein Elektromotor zusammen.")
ZWEI_VARIANTEN = t("https://www.toyota-media.de/c/text", "Lexus Modellpalette",
                   "Der Lexus RC 300h ist ein Vollhybrid. Der Lexus UX 300e ist ein Elektroauto.")
AMS = t("https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/", "Lexus RC 300h Test",
        "Der Lexus RC 300h ist ein Coupe.")
z = _ziel("Lexus", "RC 300h", 2017, "2.5", None)
w, _ = werte_identitaet_aus([HYBRID_SATZ, AMS], z)
check("B1 Vollhybrid mit Elektromotor -> powertrain HEV (vorher BEV)", w.powertrain == "HEV", str(w.powertrain))
check("B1b ... Kraftstoff Benzin", w.kraftstoff == "benzin", str(w.kraftstoff))
w, _ = werte_identitaet_aus([BAUTEIL_SATZ, AMS], z)
check("B2 nur 'Benzinmotor und Elektromotor' -> keine Klasse (UNKNOWN)", w.powertrain is None, str(w.powertrain))
w, _ = werte_identitaet_aus([ZWEI_VARIANTEN, AMS], z)
check("B3 zwei Varianten im Dokument: Elektroauto des anderen Modells nicht uebertragen",
      w.powertrain != "BEV", str(w.powertrain))


print("\n== C: sichtbare Ausgabe (run_kaufcheck, Provider gestubbt) ==")


async def _lauf(req, fixture):
    orig = (kc.call_gemini_json, kc.recherchiere_technisch, kc.tavily_search_with_fallback,
            kc.marktpreis_recherche_moeglich, kc.vertiefe_marktrecherche)

    async def gemini(system, user):
        return {"bericht": "Laut Inserat unfallfrei.", "empfehlung": "kaufen_nach_besichtigung",
                "risiko_evidence_ids": [], "empfehlung_evidence_ids": []}

    async def verboten(*a, **k):
        raise AssertionError("Marktprovider darf nicht laufen")

    prov = FixtureTechnicalResearchProvider(fixture)

    async def technik(r, br, info, brg, mm, provider=None):
        return await recherchiere_technisch(r, br, info, brg, mm, provider=prov)

    kc.call_gemini_json, kc.recherchiere_technisch = gemini, technik
    kc.tavily_search_with_fallback, kc.vertiefe_marktrecherche = verboten, verboten
    kc.marktpreis_recherche_moeglich = lambda *a: False
    try:
        return KaufCheckResponse(**await kc.run_kaufcheck(req))
    finally:
        (kc.call_gemini_json, kc.recherchiere_technisch, kc.tavily_search_with_fallback,
         kc.marktpreis_recherche_moeglich, kc.vertiefe_marktrecherche) = orig


def bericht_zeile(r, praefix):
    return next((z.strip() for z in r.bericht.splitlines() if z.strip().startswith(praefix)), "")


r = asyncio.run(_lauf(KaufCheckRequest(
    marke="Lexus", modell="RC 300h", baujahr=2017, kilometerstand=60000, motor="2.5",
    kraftstoff="Benzin", leistung_ps=223, getriebe="Automatik", ausstattung=[]),
    {"identitaet": [HYBRID_SATZ, AMS], "wartung": [], "schwachstelle": [], "rueckruf": []}))
zeile = bericht_zeile(r, "- Antriebsart (Elektrifizierung)")
check("C1 Bericht nennt keinen Elektroantrieb/BEV fuer den Hybrid", "BEV" not in zeile
      and "Elektro" not in zeile, zeile)
check("C1b identity.powertrain nicht BEV", r.vehicle_identity.get("powertrain") != "BEV",
      str(r.vehicle_identity.get("powertrain")))
r = asyncio.run(_lauf(KaufCheckRequest(
    marke="Lexus", modell="RC 300h", baujahr=2017, kilometerstand=60000, motor="2.5 Hybrid mit Elektromotor",
    kraftstoff="Benzin", leistung_ps=223, getriebe="Automatik", ausstattung=[]),
    {"identitaet": [AMS], "wartung": [], "schwachstelle": [], "rueckruf": []}))
check("C2 Nutzer-Motortext 'Hybrid mit Elektromotor' erzeugt kein BEV",
      r.vehicle_identity.get("powertrain") != "BEV", str(r.vehicle_identity.get("powertrain")))


print("\n== D: Rueckruf-Scope ==")


def ident(powertrain, fuel="benzin"):
    v = VehicleIdentity(make="Testmarke", model="Modellz")
    v.fuel = fuel
    v.powertrain = powertrain
    if powertrain:
        v.field_evidence["powertrain"] = {"verification_state": "user_confirmed", "value": powertrain}
    return v


def rueckruf(eingrenzung):
    return {"betroffene_baujahre": "2019-2023", "kba_referenz": None,
            "mangel": "Durch einen Fertigungsfehler kann es zu einem Ausfall kommen.",
            "eingrenzung_amtlich": eingrenzung}


R_BAUTEIL = rueckruf("Betroffen sind Fahrzeuge, deren Elektromotor der Servolenkung ausfallen kann.")
R_BEV = rueckruf("Betroffen sind ausschließlich Elektrofahrzeuge.")
R_NICHT_BEV = rueckruf("Betroffen sind Fahrzeuge mit Verbrennungsmotor, nicht Elektrofahrzeuge.")
R_HEV = rueckruf("Betroffen sind ausschließlich Fahrzeuge als Vollhybrid.")
s, b = rueckruf_scope(R_BAUTEIL, ident("ICE"))
check("D1 Bauteil 'Elektromotor' im Rueckruftext schliesst Verbrenner NICHT aus (vorher NOT_APPLICABLE)",
      s != RECALL_NOT_APPLICABLE, f"{s} {b}")
s, b = rueckruf_scope(R_BEV, ident("ICE"))
check("D2 ausdrueckliche Elektrofahrzeug-Eingrenzung schliesst Verbrenner weiter aus",
      s == RECALL_NOT_APPLICABLE, f"{s} {b}")
s, b = rueckruf_scope(R_NICHT_BEV, ident("ICE"))
check("D3 verneinte Klasse ('nicht Elektrofahrzeuge') schliesst Verbrenner nicht aus",
      s != RECALL_NOT_APPLICABLE, f"{s} {b}")
s, b = rueckruf_scope(R_HEV, ident("HEV"))
check("D4 Vollhybrid-Eingrenzung + HEV-Fahrzeug: weiter VARIANT_POSSIBLE", s == RECALL_VARIANT_POSSIBLE, f"{s} {b}")
s, b = rueckruf_scope(R_HEV, ident(None))
check("D5 Vollhybrid-Eingrenzung + unbekannte Antriebsart: UNKNOWN", s == RECALL_UNKNOWN, f"{s} {b}")
w, _ = werte_identitaet_aus([HYBRID_SATZ, AMS], z)
v = VehicleIdentity(make="Lexus", model="RC 300h")
v.apply_web_evidence(TechnischeRecherche(identitaet=w))
check("D6 Hybridtext mit Elektromotor fuehrt nicht zu BEV-Identitaet (Rueckrufeingang)", v.powertrain != "BEV",
      str(v.powertrain))
s, b = rueckruf_scope(R_HEV, v)
check("D6b ... Vollhybrid-Rueckruf passt zur korrekt erkannten HEV-Identitaet", s == RECALL_VARIANT_POSSIBLE,
      f"{s} {b}")
check("D7 rueckruf_scope liefert nie 'confirmed_by_vin' (FIN-Betroffenheit separat)",
      "confirmed_by_vin" not in {rueckruf_scope(x, ident(p))[0] for x in (R_BAUTEIL, R_BEV, R_HEV)
                                for p in ("ICE", "HEV", None)})

print("\n== E: Klassen-Scope-Matrix ueber den echten Rueckrufpfad (rueckruf_applicability / gefilterte_rueckrufe) ==")
from app.recall_filter import gefilterte_rueckrufe, rueckruf_applicability


def ident_kl(pt, state="web_only"):
    v = VehicleIdentity(make="Testmarke", model="Modellz")
    v.fuel = "elektro" if pt == "BEV" else "benzin"
    v.powertrain = pt
    if pt:
        v.field_evidence["powertrain"] = {"verification_state": state, "value": pt}
    return v


E_RUECKRUFE = {
    "BEV-exklusiv": dict(eingrenzung_amtlich="Betroffen sind ausschließlich batterieelektrische Fahrzeuge."),
    "Elektrofzg-unklar": dict(eingrenzung_amtlich="Betroffen sind ausschließlich Elektrofahrzeuge."),
    "HEV-exklusiv": dict(eingrenzung_amtlich="Betroffen sind ausschließlich Fahrzeuge als Vollhybrid."),
    "PHEV-exklusiv": dict(eingrenzung_amtlich="Betroffen sind ausschließlich Plug-in-Hybrid-Fahrzeuge."),
    "HEV+PHEV": dict(eingrenzung_amtlich="Betroffen sind Vollhybrid- und Plug-in-Hybrid-Fahrzeuge."),
    "PHEV+BEV": dict(eingrenzung_amtlich="Betroffen sind Plug-in-Hybrid-Fahrzeuge und batterieelektrische Fahrzeuge."),
    "Hybrid-generisch": dict(eingrenzung_amtlich="Betroffen sind ausschließlich Hybridfahrzeuge."),
    "BEV+Hybrid-generisch": dict(eingrenzung_amtlich="Betroffen sind batterieelektrische Fahrzeuge und Hybridfahrzeuge."),
    "Hochvolt-allg": dict(mangel="An der Hochvolt-Batterie kann ein Kurzschluss auftreten."),
    "Qualifier(Elektro)": dict(betroffene_baujahre="2020-2022 (Elektro)", mangel="Ein Steuergerät kann ausfallen."),
    "BEV-verneint": dict(eingrenzung_amtlich="Batterieelektrische Fahrzeuge sind nicht betroffen."),
    "PHEV-verneint": dict(eingrenzung_amtlich="Plug-in-Hybrid-Fahrzeuge sind nicht betroffen."),
    "HEV-excl+PHEV-Erlaeut": dict(eingrenzung_amtlich="Betroffen sind ausschließlich Vollhybrid-Fahrzeuge. "
                                                      "Plug-in-Hybride sind nicht betroffen."),
    "BEV-excl+Hybrid-Erlaeut": dict(eingrenzung_amtlich="Betroffen sind ausschließlich batterieelektrische Fahrzeuge. "
                                                        "Hybridfahrzeuge mit demselben Bauteil sind nicht betroffen."),
    "Bauteil-Elektromotor": dict(mangel="Der Elektromotor der Servolenkung kann ausfallen; die Lenkunterstützung entfällt."),
    "Bauteil-Elektromigration": dict(mangel="Durch Elektromigration auf der Leiterplatte kann die Zusatzpumpe ausfallen."),
}
S, U, I = "series_only", "unclear", "incompatible"
#                          BEV HEV PHEV MHEV ICE UNKN BEV/nur-Nutzer
E_KLASSEN = [("BEV", "web_only"), ("HEV", "web_only"), ("PHEV", "web_only"), ("MHEV", "web_only"),
             ("ICE", "web_only"), (None, None), ("BEV", "user_only")]
E_ERWARTET = {
    "BEV-exklusiv":            (S, I, I, I, I, U, U),
    "Elektrofzg-unklar":       (S, U, U, I, I, U, U),
    "HEV-exklusiv":            (I, S, I, I, I, U, U),
    "PHEV-exklusiv":           (I, I, S, I, I, U, U),
    "HEV+PHEV":                (I, S, S, I, I, U, U),
    "PHEV+BEV":                (S, I, S, I, I, U, U),
    "Hybrid-generisch":        (U, U, U, U, U, U, U),   # generischer Hybrid != HEV: offen (unveraendert)
    "BEV+Hybrid-generisch":    (S, U, U, U, I, U, U),
    "Hochvolt-allg":           (S, S, S, I, I, U, S),   # klassenuebergreifend, nicht auf BEV verengt
    "Qualifier(Elektro)":      (S, U, U, I, I, U, U),
    "BEV-verneint":            (S, S, S, S, S, S, S),   # Ausnahme-Satz ist kein Scope
    "PHEV-verneint":           (S, S, S, S, S, S, S),
    "HEV-excl+PHEV-Erlaeut":   (I, S, I, I, I, U, U),
    "BEV-excl+Hybrid-Erlaeut": (S, I, I, I, I, U, U),
    "Bauteil-Elektromotor":    (S, S, S, S, S, S, S),
    "Bauteil-Elektromigration": (S, S, S, S, S, S, S),
}
for name, felder in E_RUECKRUFE.items():
    r = {"betroffene_baujahre": "2020-2022", "kba_referenz": None, "mangel": "Ein Bauteil kann ausfallen.", **felder}
    for (kl, st), soll in zip(E_KLASSEN, E_ERWARTET[name]):
        idn = ident_kl(kl, st or "unknown")
        ist = rueckruf_applicability(r, True, "", None, marke="Testmarke", identity=idn)[0]
        label = f"{kl or 'UNKNOWN'}{'/nur Nutzer' if st == 'user_only' else ''}"
        check(f"E {name} x {label}: {soll}", ist == soll, ist)
        sichtbar = gefilterte_rueckrufe([r], None, 2021, marke="Testmarke", identity=idn)
        check(f"E {name} x {label}: sichtbar == (nicht incompatible)", bool(sichtbar) == (soll != I))
        if sichtbar:
            txt = sichtbar[0]["text"]
            check(f"E {name} x {label}: Ausgabe verlangt FIN-Pruefung, keine FIN-Bestaetigung",
                  "per FIN prüfen" in txt and "per FIN bestätigt" not in txt, txt)
            check(f"E {name} x {label}: nie confirmed_by_vin", sichtbar[0]["applicability"] != "confirmed_by_vin")
        elif soll == I:
            ausgeschl = rueckruf_scope(r, idn)
            check(f"E {name} x {label}: sichtbare Begruendung des Ausschlusses nennt die Klasse",
                  ausgeschl[0] == RECALL_NOT_APPLICABLE and bool(ausgeschl[1]) or
                  rueckruf_applicability(r, True, "", None, marke="Testmarke", identity=idn)[3] != "",
                  str(ausgeschl))

print("\n== F: Seed-Rueckrufe 5098-5101 (Elektromigration, Zusatzkuehlmittelpumpe) bleiben sichtbar ==")
import os
import sqlite3

_con = sqlite3.connect(os.environ["AUTO_KI_DB_PATH"])
_con.row_factory = sqlite3.Row
for _id in (5098, 5099, 5100, 5101):
    _row = _con.execute("select * from rueckruf where id=?", (_id,)).fetchone()
    check(f"F Seedzeile {_id} vorhanden", _row is not None)
    if _row is None:
        continue
    _r = dict(_row)
    for _pt in ("ICE", "HEV", "PHEV", "BEV", None):
        _sichtbar = gefilterte_rueckrufe([_r], None, None, marke=None, identity=ident_kl(_pt, "web_only" if _pt else "unknown"))
        check(f"F {_id} x {_pt or 'UNKNOWN'}: sichtbar (Bauteilwort/Elektromigration ist kein Hochvolt)",
              bool(_sichtbar) or rueckruf_applicability(_r, None, "", None, identity=ident_kl(_pt))[0] != "incompatible")

print("\nERGEBNIS:", "ALLE OK" if not FAILS else f"{len(FAILS)} FEHLER: {FAILS}")
raise SystemExit(1 if FAILS else 0)
