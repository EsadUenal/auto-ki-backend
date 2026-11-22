"""
VK-01 -- VerkaufCheck: technische DB-Hinweise erreichen den Gemini-Prompt nur über die zugelassene Evidenzauswahl.

    python scripts/test_isolated.py test_verkaufscheck_prompt_evidenz.py

Vorher: `build_db_context(baureihe, motor, baujahr)` ohne `risiken` fügte die ROHEN Listen "Motorprobleme", "Kritische
Wartung" und "Schwachstellen Baureihe" (mit Schweregrad, ohne Beleglage) in den Prompt ein, auch wenn `_fuer_llm_evidence`
die Aussage ausgeschlossen hatte, und der Systemprompt nannte das DB-Profil "geprüfte Fakten ... zuverlässig".

Der Test läuft den PRODUKTIVEN `run_verkaufscheck` (inkl. `find_motor(..., req=req)`) auf ORIGINAL-Seed-Daten der Temp-DB.
Gemini ist ein Prompt-erfassender Stub (es gibt KEINE echte Modellantwort); Tavily/Marktpfad sind verboten, CarAPI ist aus.
Der positive Gegenfall (Abschnitt E) setzt in der Temp-DB eine SYNTHETISCHE Verifikation. Sie ist ausdrücklich ein
Testaufbau und KEINE Eigenschaft des unverifizierten Seed-Bestands (dort ist keine der Zeilen verifiziert).
"""
import asyncio
import os
import re
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import app.verkaufscheck as vc
from app import carapi_provider as ca
from app import database
from app.car_lookup import build_db_context
from app.fakt_verifikation import fingerprint
from app.models import VerkaufsCheckRequest

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f"  -> {detail}" if not bedingung and detail else ""))
    if not bedingung:
        _FEHLER.append(name)


CAP: dict = {}


async def _gemini(system, user):
    CAP["system"], CAP["user"] = system, user
    return {"bericht": "STUB-Bericht (kein echtes Modell)", "preis_evidence_ids": [],
            "strategie_evidence_ids": [], "argument_evidence_ids": []}


async def _verboten(*a, **k):
    raise AssertionError("Tavily/Marktpfad ist verboten")


vc.call_gemini_json = _gemini
vc.tavily_search_with_fallback = _verboten
vc.vertiefe_marktrecherche = _verboten
_orig_filter = vc._fuer_llm_evidence


def _spy(insights):
    ausgewaehlt = _orig_filter(insights)
    CAP["alle"], CAP["auswahl"] = list(insights), list(ausgewaehlt)
    return ausgewaehlt


vc._fuer_llm_evidence = _spy


def lauf(**kw):
    kw.setdefault("kilometerstand", 180_000)
    res = asyncio.run(vc.run_verkaufscheck(VerkaufsCheckRequest(**kw)))
    return res, CAP["system"], CAP["user"]


def abschnitt(text: str, kopf: str) -> str:
    m = re.search(rf"({re.escape(kopf)}.*?)(?=\n\n|\Z)", text, re.S)
    return m.group(1) if m else ""


ROH_MARKER = ("Kritische Wartung:", "Motorprobleme:", "### Schwachstellen Baureihe:", "[hoch]", "[mittel]", "[gering]")
PASSAT = dict(marke="Volkswagen", modell="Passat", baujahr=2009, motor="2.0 TDI 140 PS", kraftstoff="Diesel",
              preis_vorstellung=6500)

print("=== Isolation ===")
check("Temp-DB", "enfal_offline_" in os.environ["AUTO_KI_DB_PATH"])
check("CarAPI aus, Marktrecherche nicht möglich", ca.ist_aktiv() is False and not vc.marktpreis_recherche_moeglich("Volkswagen", "Passat"))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== A) Originaler Passat-Seed-Fall: ausgeschlossene unverifizierte Aussagen fehlen im Prompt ===")
con = sqlite3.connect(os.environ["AUTO_KI_DB_PATH"])
con.row_factory = sqlite3.Row
n_verif = con.execute("select count(*) from fakt_verifikation where fakt_art in "
                      "('schwachstelle_baureihe','schwachstelle_motor','kritische_wartung') and fakt_id in "
                      "(851,852,853,854,855,856,857,1107,1982,1983,1984)").fetchone()[0]
check("A0 Ausgangslage: keine der Passat-Zeilen ist im Seed verifiziert", n_verif == 0, str(n_verif))
res, system, user = lauf(**PASSAT)
check("A1 produktive Zuordnung (req=): Baureihe und Motor erkannt",
      res["baureihe_erkannt"] == "volkswagen-passat-b6" and res["motor_erkannt"] == "volkswagen-passat-b6-2.0-tdi-140ps")
schwach = [i for i in CAP["alle"] if i.kategorie == "schwachstelle"]
check("A2 vier unverifizierte Schwachstellen-Insights, alle niedrig belegt, keiner in der LLM-Auswahl",
      len(schwach) == 4 and all(i.trust == "unverified_db" and i.confidence == "niedrig" for i in schwach)
      and CAP["auswahl"] == [])
check("A3 kein Evidence-Block im Prompt", "VERFÜGBARE EVIDENCE" not in user)
for bauteil in ("DSG-Getriebe", "Feststellbremse", "Turbolader", "Rost (Heckklappe", "Zahnriemen", "Wasserpumpe"):
    check(f"A4 '{bauteil}' erscheint nicht im Prompt (weder Evidence noch Rohliste)", bauteil not in user)
check("A5 keine Rohlisten-Überschriften, keine Schweregrad-Tags", not any(m in user for m in ROH_MARKER),
      str([m for m in ROH_MARKER if m in user]))
check("A6 kein Block 'Technische Hinweise' ohne zugelassene Evidenz", "Technische Hinweise" not in user)
check("A7 Neupreis, Specs und Motor bleiben", "## DB-Profil: Volkswagen Passat B6" in user and "### Motor: 2.0 TDI (140 PS)" in user
      and "Neupreis ca.:" in user and "Leistung: 140 PS" in user)
check("A8 Fahrzeugangaben des Nutzers bleiben getrennt davon im Prompt", "MEIN FAHRZEUG:" in user and "Preisvorstellung:   6.500 €" in user)
check("A9 completed_no_market, keine Preisfelder", res["research_status"] == "completed_no_market"
      and res["empfohlener_preis"] is None and res["marktpreis_min"] is None and res["schnellverkaufs_preis"] is None)
check("A10 Ausgabe-Insights bleiben für Prüfhinweise/UI erhalten (nur der Prompt ist gefiltert)",
      len(res["insights"]) == len(CAP["alle"]) and len(CAP["alle"]) == 4)

print("\n=== F) Systemprompt ohne pauschale Verifikationsbehauptung ===")
check("F1 'geprüfte Fakten' fehlt", "geprüfte Fakten" not in system)
check("F2 'zuverlässig' bezieht sich nicht mehr auf das DB-Profil",
      not re.search(r"DB-PROFIL[^\n]*zuverl", system))
check("F3 technische Hinweise nur mit Beleglage benannt", 'Abschnitt "Technische Hinweise"' in system and "Beleglage" in system)
check("F4 Neupreis/Specs-Regel bleibt", "Neupreis und Specs nur aus DB-Profil." in system)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== B) Semantik risiken=None gegen risiken=[] (build_db_context direkt, gleiche Datenbasis) ===")
br = database.get_baureihe("Volkswagen", "Passat", "B6")
mo = next(m for m in br["motoren"] if m["variante_id"] == "volkswagen-passat-b6-2.0-tdi-140ps")
ctx_none = build_db_context(br, mo, 2009)
ctx_leer = build_db_context(br, mo, 2009, risiken=[])
check("B1 Altverhalten (None): Rohlisten sind vorhanden (nur zur Abgrenzung)",
      "Kritische Wartung:" in ctx_none and "### Schwachstellen Baureihe:" in ctx_none)
check("B2 ausdrücklich leere Auswahl ([]): keine Rohlisten, kein Technik-Block",
      not any(m in ctx_leer for m in ROH_MARKER) and "Technische Hinweise" not in ctx_leer and "Zahnriemen" not in ctx_leer)
check("B3 Specs, Neupreis, Ausstattungslinien bleiben bei [] vollständig",
      "Neupreis ca.:" in ctx_leer and "## DB-Profil:" in ctx_leer
      and ("Ausstattungslinien:" in ctx_none) == ("Ausstattungslinien:" in ctx_leer)
      and ctx_leer.split("### Motor")[0] == ctx_none.split("### Motor")[0])
ctx_leer_ohne_motor = build_db_context(br, None, 2009, risiken=[])
check("B4 [] OHNE erkannten Motor: auch dann keine Rohlisten (früherer Rückfall ist weg)",
      not any(m in ctx_leer_ohne_motor for m in ROH_MARKER) and "Wartungshinweise" not in ctx_leer_ohne_motor
      and "ungeprüfte Datenbankhinweise" not in ctx_leer_ohne_motor)
check("B5 None OHNE Motor bleibt Altverhalten (Rohlisten), der Unterschied ist die Semantik von None/[]",
      "Motorprobleme" in build_db_context(br, None, 2008) or "Kritische Wartung:" in build_db_context(br, None, 2008))
res_ohne_motor, _s, user_ohne_motor = lauf(**{**PASSAT, "motor": None})
check("B6 produktiv ohne erkennbaren Motor: keine Rohlisten im Prompt",
      res_ohne_motor["motor_erkannt"] is None
      and not any(m in user_ohne_motor for m in ROH_MARKER) and "Wartungshinweise" not in user_ohne_motor,
      f"motor_erkannt={res_ohne_motor['motor_erkannt']}")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== C) Rückrufe: Abschnitt, Scope und FIN-Wortlaut bleiben unverändert und getrennt (Opel Astra K, Seed) ===")
res_a, _s, user_a = lauf(marke="Opel", modell="Astra K", baujahr=2018, motor="1.4 Turbo", kraftstoff="Benzin",
                         kilometerstand=92_300)
rueck_prompt = abschnitt(user_a, "### KBA-Rückrufe")
check("C1 Rückrufabschnitt bleibt im Prompt (Datenbasis: Seed)", bool(rueck_prompt) or res_a["baureihe_erkannt"] is None,
      f"baureihe={res_a['baureihe_erkannt']}")
if res_a["baureihe_erkannt"]:
    br_a = database.get_baureihe(*con.execute("select marke,modell,generation from baureihe where id=?",
                                              (res_a["baureihe_erkannt"],)).fetchone())
    mo_a = next((m for m in br_a["motoren"] if m["variante_id"] == res_a["motor_erkannt"]), None)
    alt = abschnitt(build_db_context(br_a, mo_a, 2018), "### KBA-Rückrufe")
    neu = abschnitt(build_db_context(br_a, mo_a, 2018, risiken=[]), "### KBA-Rückrufe")
    check("C2 Rückrufabschnitt identisch mit und ohne evidenzgebundenen Modus", alt == neu and alt == rueck_prompt)
    check("C3 Rückrufe behaupten keine bestätigte Betroffenheit/Durchführung",
          not re.search(r"betrifft dein|bestätigt betroffen|bereits durchgeführt|erledigt", rueck_prompt, re.I))
    check("C4 keine Rohlisten für Astra im Prompt", not any(m in user_a for m in ROH_MARKER))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== D) Unsichere Identität öffnet keinen DB-Kontext ===")
res_u, _s, user_u = lauf(marke="Volkswagen", modell="Golf XV", baujahr=2015, kraftstoff="Benzin")
check("D1 keine belastbare Zuordnung, keine Baureihe erkannt", res_u["baureihe_erkannt"] is None
      and res_u["identitaet_konfidenz"] != "hoch", str(res_u["identitaet_konfidenz"]))
check("D2 Prompt sagt 'Kein DB-Profil', ohne Specs, Rohlisten oder technische Hinweise",
      "Kein DB-Profil für dieses Fahrzeug vorhanden." in user_u and "### Motor" not in user_u
      and not any(m in user_u for m in ROH_MARKER) and "Technische Hinweise" not in user_u and "Neupreis ca." not in user_u)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== E) Positiver Gegenfall mit SYNTHETISCHER Verifikation (nur Testaufbau in der Temp-DB) ===")
print("   HINWEIS: Die Verifikation von schwachstelle_baureihe #852 (DSG) wird hier künstlich gesetzt. Der Seed-Bestand ist")
print("   unverifiziert; diese Zeile gilt nur in diesem Test.")
zeile = next(s for s in br["schwachstellen_baureihe"] if s["id"] == 852)
con.execute("insert into fakt_verifikation (fakt_art,fakt_id,fingerprint,status,quelle,quelle_stufe,url,referenz,geprueft_am,notiz)"
            " values (?,?,?,?,?,?,?,?,?,?)",
            ("schwachstelle_baureihe", 852, fingerprint("schwachstelle_baureihe", zeile), "verified",
             "SYNTHETISCH (nur Test)", "B", None, None, "2026-11-23", "synthetischer Testaufbau, keine echte Prüfung"))
con.commit()
res_p, _s, user_p = lauf(**PASSAT)
dsg = [i for i in CAP["alle"] if i.kategorie == "schwachstelle" and "DSG" in i.titel]
andere = [i for i in CAP["alle"] if i.kategorie == "schwachstelle" and "DSG" not in i.titel]
check("E1 DSG-Insight ist jetzt verifiziert und nicht niedrig belegt", len(dsg) == 1 and dsg[0].trust == "verified"
      and dsg[0].confidence in ("mittel", "hoch"), str([(i.trust, i.confidence) for i in dsg]))
check("E2 genau diese Aussage ist zugelassen, die drei unverifizierten bleiben ausgeschlossen",
      [i.id for i in CAP["auswahl"]] == [dsg[0].id] and all(i.id not in {j.id for j in CAP["auswahl"]} for i in andere))
check("E3 Evidence-Block trägt die Kennung der Aussage", "VERFÜGBARE EVIDENCE" in user_p and f"[{dsg[0].id}]" in user_p)
tech = abschnitt(user_p, "### Technische Hinweise")
check("E4 Technik-Block mit Beleglage 'belegt', Datenqualität und Schweregrad",
      "Beleglage: belegt" in tech and f"Datenqualität {dsg[0].confidence}" in tech and "Schweregrad: hoch" in tech and "DSG-Getriebe" in tech,
      tech[:300])
check("E5 Block enthält die Einschränkungsregeln zu gemeldeten Hinweisen",
      "gemeldeter Hinweis" in user_p and "keine festgestellte Tatsache" in user_p)
check("E6 die drei ungeprüften Aussagen fehlen weiterhin, auch als Rohzeile",
      all(b not in user_p for b in ("Feststellbremse", "Turbolader", "Rost (Heckklappe")) and "### Schwachstellen Baureihe:" not in user_p)
check("E7 Baujahresgrenze der Aussage bleibt als Einschränkung sichtbar",
      "Betroffene Baujahre laut Datenbank: 2007-2010." in tech, tech[:500])
check("E8 completed_no_market, keine Preisfelder (unverändert)", res_p["research_status"] == "completed_no_market"
      and res_p["empfohlener_preis"] is None and res_p["marktpreis_max"] is None)

print(f"\n{'ALLE GRUEN' if not _FEHLER else 'FEHLER: ' + ', '.join(_FEHLER)}")
raise SystemExit(1 if _FEHLER else 0)
