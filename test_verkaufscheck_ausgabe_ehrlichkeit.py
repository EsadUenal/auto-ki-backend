"""
VK-03/04/05/06/08 -- konsistente, wahrheitsgetreue VerkaufCheck-Ausgabe (offline, Isolationsrunner).

    python scripts/test_isolated.py test_verkaufscheck_ausgabe_ehrlichkeit.py

Herkunft der Eingaben:
  ORIGINAL (laut Auftrag): HU 08/2027, letzter Service 09/2026 bei 177.000 km, Ausstattung Navigation/Sitzheizung/
  Parksensoren hinten/Tempomat/Klimaanlage, zwei Vorbesitzer, Inseratssätze "Wartungsnachweise sind teilweise vorhanden."
  und "Zur Unfallfreiheit kann ich keine sichere Angabe machen.", Kotflügel vorne rechts nachlackiert (Ursache unbekannt),
  keine Fotos, Preis 6.900 EUR, private Untergrenze 6.000 EUR.
  REKONSTRUIERT (nicht aus dem Originalexport): Passat B6 2.0 TDI 140 PS, Diesel, Schalter, 180.000 km, die Mängel
  (Klimaanlage, Parksensoren, Kratzer, Steinschläge). GEGENFÄLLE sind als solche benannt.

Die Fälle laufen durch die PRODUKTIVEN Pfade (`run_verkaufscheck`, `run_inserat_optimierung`). Gemini ist ein Stub: die
konstruierte Rohantwort in Abschnitt D ist KEINE echte Modellantwort. Tavily/Marktpfad sind verboten.
"""
import asyncio
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import app.inserat as ins
import app.verkaufscheck as vc
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

INSERAT = ("Verkaufe meinen Passat B6 mit 2.0 TDI 140 PS, Schaltgetriebe, 180.000 km.\n"
           "Wartungsnachweise sind teilweise vorhanden. Letzter Service 09/2026 bei 177.000 km. HU 08/2027.\n"
           "Zur Unfallfreiheit kann ich keine sichere Angabe machen.\n"
           "Kotflügel vorne rechts wurde nachlackiert, die Ursache ist mir unbekannt.\n"
           "Die Klimaanlage kühlt schwach, die Parksensoren hinten fallen zeitweise aus.\n"
           "Kratzer am Heckstoßfänger, Steinschläge auf der Motorhaube.")
ORIGINAL = dict(
    marke="Volkswagen", modell="Passat", baujahr=2009, kilometerstand=180000, motor="2.0 TDI 140PS", kraftstoff="Diesel",
    getriebe="Schaltgetriebe", preis_vorstellung=6900, preis_untergrenze=6000, verkaufsziel="ausgewogen",
    tuev_bis="08/2027", letzter_service_datum="09/2026", letzter_service_km=177000, wartungsnachweise="teilweise",
    vorbesitzer=2, ausstattung=["Navigation", "Sitzheizung", "Parksensoren hinten", "Tempomat", "Klimaanlage"],
    beschreibung="Guter Zustand, normale Gebrauchsspuren", inserat_text=INSERAT,
    vorschaeden="Nachlackierung Kotflügel vorne rechts, Ursache unbekannt",
    technische_maengel=["Klimaanlage kühlt schwach", "Parksensoren hinten fallen zeitweise aus"],
    optische_maengel=["Kratzer am Heckstoßfänger", "Steinschläge auf der Motorhaube"])


def req(**kw) -> VerkaufsCheckRequest:
    return VerkaufsCheckRequest(**{**ORIGINAL, **kw})


def lauf(**kw):
    r = asyncio.run(vc.run_verkaufscheck(req(**kw)))
    return r, r["verkaufsplan"], r["listing_analyse"]


def fehlend(la) -> list[str]:
    return [f.feld for f in la.fehlende_angaben]


def titel_wertminderer(plan) -> list[str]:
    return [w["titel"] for w in plan["wertminderer"]]


def trans(plan) -> dict[str, bool]:
    return {k["kriterium"]: k["erfuellt"] for k in plan["inseratsqualitaet"]["transparenz"]["kriterien"]}


print("=== Isolation ===")
check("Temp-DB", "enfal_offline_" in os.environ["AUTO_KI_DB_PATH"])

# ══════════════════════════════════════════════════════════════════════════════
print("\n=== A) Originalfall: Teilnachweise + ausdrückliche Unfallunsicherheit aus dem Inserat (Formular: nicht angegeben) ===")
res, plan, la = lauf()
check("A1 Vollständigkeit: weder Unfallfreiheit noch Scheckheft/Wartung gelten als fehlend",
      "Unfallfreiheit" not in fehlend(la) and "Scheckheft / Wartung" not in fehlend(la) and la.vorhanden == la.gesamt,
      f"{la.vorhanden}/{la.gesamt} {fehlend(la)}")
check("A2 Transparenz: alle Kriterien konsistent erfüllt (Unfallunsicherheit ist eine Auskunft)",
      all(trans(plan).values()) and plan["inseratsqualitaet"]["transparenz"]["label"] == "sehr_gut", str(trans(plan)))
tw = titel_wertminderer(plan)
check("A3 Wertminderer: Unfallstatus unklar und lückenhafte Nachweise, KEINE Fehlend-/Keine-Nachweise-Meldung",
      "Unfallstatus unklar" in tw and "Wartungsnachweise lückenhaft" in tw
      and "Keine Wartungsnachweise" not in tw and "Fehlende Angaben" not in tw, str(tw))
check("A4 Widersprüche: Service neben HU erzeugt keinen HU-Konflikt (VK-08)", la.probleme == [], str(la.probleme))
beschr = plan["inserat"]["beschreibung"]
check("A5 Inseratspaket (deterministisch): Teilnachweise, Unfallunsicherheit, Service, Vorschaden bleiben",
      "Wartungsnachweise: teilweise vorhanden" in beschr and "Unfallstatus: keine sichere Angabe möglich" in beschr
      and "Letzter Service: 09/2026, 177.000 km" in beschr and "Nachlackierung Kotflügel vorne rechts, Ursache unbekannt" in beschr)
check("A6 Inseratspaket behauptet weder Unfallfreiheit noch Scheckheft noch vollständige Historie",
      "- Unfallfrei" not in beschr and "Scheckheft" not in beschr and "vollständig" not in beschr.lower())
check("A7 keine pauschale Unterlagenbehauptung; nur die vorhandenen Wartungsunterlagen werden genannt",
      "Unterlagen liegen zur Einsicht bereit" not in beschr
      and "Die vorhandenen Wartungsunterlagen liegen zur Einsicht bereit." in beschr)
check("A8 Faktenblock trägt Nachweisstatus, Service und Unfallunsicherheit; Unfallfrei-Zeile fehlt",
      any(z.startswith("Wartungsnachweise: teilweise") for z in plan["inserat"]["faktenblock"])
      and any(z.startswith("Letzter Service:") for z in plan["inserat"]["faktenblock"])
      and any(z.startswith("Unfallstatus: keine sichere Angabe") for z in plan["inserat"]["faktenblock"])
      and not any(z.startswith("Unfallfrei:") for z in plan["inserat"]["faktenblock"]))
kb = plan["inserat"]["kurzbeschreibung"]
check("A9 Kurzbeschreibung nennt Vorschaden, Unfallunsicherheit und Nachweisgrenze",
      "Vorschäden: Nachlackierung" in kb and "Unfallstatus: keine sichere Angabe möglich" in kb
      and "Wartungsnachweise: teilweise vorhanden" in kb, kb)
check("A10 completed_no_market, keine Preisfelder (unverändert)", res["research_status"] == "completed_no_market"
      and res["empfohlener_preis"] is None and res["marktpreis_min"] is None)
check("A11 VK-01 bleibt: keine ungeprüften technischen Rohlisten im Prompt",
      not any(m in CAP["user"] for m in ("Kritische Wartung:", "Motorprobleme:", "### Schwachstellen Baureihe:")))

# ══════════════════════════════════════════════════════════════════════════════
print("\n=== B) Gegenfall: nichts angegeben und nichts im Text (keine erfundene Unsicherheit) ===")
res_b, plan_b, la_b = lauf(inserat_text=None, wartungsnachweise=None)
check("B1 Unfallfreiheit und Scheckheft/Wartung sind als FEHLEND ausgewiesen",
      "Unfallfreiheit" in fehlend(la_b) and "Scheckheft / Wartung" in fehlend(la_b), str(fehlend(la_b)))
check("B2 Transparenz: Unfallstatus und Wartungsnachweis nicht erfüllt",
      trans(plan_b)["Unfallstatus angegeben oder ausdrücklich als unklar benannt"] is False
      and trans(plan_b)["Wartungsnachweis angegeben"] is False)
check("B3 Aus None wird kein 'unbekannt' erfunden: kein Unfallstatus, keine Nachweiszeile im Inserat",
      "Unfallstatus" not in plan_b["inserat"]["beschreibung"] and "Wartungsnachweise:" not in plan_b["inserat"]["beschreibung"]
      and "Unfallstatus unklar" not in titel_wertminderer(plan_b))
check("B4 'Fehlende Angaben' benennt beide", any(w["titel"] == "Fehlende Angaben" and "Unfallfreiheit" in w["text"]
                                              and "Scheckheft / Wartung" in w["text"] for w in plan_b["wertminderer"]))
check("B5 ohne Nachweise keine Unterlagenbehauptung", "Wartungsunterlagen liegen" not in plan_b["inserat"]["beschreibung"])

# ══════════════════════════════════════════════════════════════════════════════
print("\n=== C) Zustände getrennt: ausdrücklich unbekannt / verneint / widersprüchlich (klar markierte Gegenfälle) ===")
res_c, plan_c, la_c = lauf(inserat_text=None, wartungsnachweise=None, unfallfrei="unbekannt")
check("C1 Formular 'Unklar': als Auskunft gewertet, nicht als fehlend", "Unfallfreiheit" not in fehlend(la_c)
      and trans(plan_c)["Unfallstatus angegeben oder ausdrücklich als unklar benannt"] is True
      and "Unfallstatus unklar" in titel_wertminderer(plan_c))
check("C2 'Unklar' wird im Inserat ehrlich benannt, nicht als unfallfrei", "Unfallstatus: keine sichere Angabe möglich"
      in plan_c["inserat"]["beschreibung"] and "- Unfallfrei" not in plan_c["inserat"]["beschreibung"])
_, plan_ja, la_ja = lauf(unfallfrei="ja")
check("C3 Formular 'ja' + Inseratstext mit Unsicherheit: Widerspruch offengelegt, Formular bleibt kanonisch",
      any("Unsicherheit zum Unfallstatus" in p for p in la_ja.probleme)
      and "- Unfallfrei" in plan_ja["inserat"]["beschreibung"], str(la_ja.probleme))
_, plan_w, la_w = lauf(wartungsnachweise="vollstaendig")
check("C4 Formular 'vollständig' + Text 'teilweise': Widerspruch offengelegt, Formular bleibt kanonisch",
      any("Wartungsnachweise teilweise" in p and "vollständig" in p for p in la_w.probleme)
      and "Wartungsnachweise: laut Angabe vollständig vorhanden" in plan_w["inserat"]["beschreibung"], str(la_w.probleme))
_, plan_k, la_k = lauf(inserat_text=None, wartungsnachweise="keine")
check("C5 Nachweise 'keine': Wertminderer und Inserat sagen es, keine Unterlagenbehauptung",
      "Keine Wartungsnachweise" in titel_wertminderer(plan_k)
      and "Wartungsnachweise: keine vorhanden" in plan_k["inserat"]["beschreibung"]
      and "Wartungsunterlagen liegen" not in plan_k["inserat"]["beschreibung"])
_, plan_s, la_s = lauf(inserat_text=None, wartungsnachweise=None, scheckheftgepflegt=False)
check("C6 nur 'kein Scheckheft': das ist keine Aussage über alle Nachweise",
      "Kein Scheckheft" in titel_wertminderer(plan_s) and "Keine Wartungsnachweise" not in titel_wertminderer(plan_s))
_, plan_v, la_v = lauf(inserat_text=None, wartungsnachweise=None)
check("C7 Service ohne Nachweisangabe ist kein Beleg: Scheckheft/Wartung bleibt fehlend, keine Unterlagenbehauptung",
      "Scheckheft / Wartung" in fehlend(la_v) and "Letzter Service: 09/2026" in plan_v["inserat"]["beschreibung"]
      and "Wartungsunterlagen liegen" not in plan_v["inserat"]["beschreibung"])

# ══════════════════════════════════════════════════════════════════════════════
print("\n=== D) Optimierung: Faktenblock, Guard (Stub-Rohantwort, KEINE echte Modellantwort), Endausgabe ===")
fb = ins._fakten_block(req())
check("D1 Faktenblock: Unfallunsicherheit (aus dem Inserat), Nachweisstatus, Service als Angabe, Vorschaden, Mängel",
      "dem Verkäufer unbekannt" in fb and "Wartungsnachweise: nur teilweise vorhanden" in fb
      and "Letzter Service (Angabe des Verkäufers, kein Beleg): 09/2026, 177.000 km" in fb
      and "Vorschäden/Nachlackierungen: Nachlackierung Kotflügel vorne rechts" in fb
      and "Klimaanlage kühlt schwach" in fb and "Kratzer am Heckstoßfänger" in fb)
check("D2 Faktenblock enthält die private Untergrenze NICHT, die Preisvorstellung schon",
      "6.000" not in fb and "Untergrenze" not in fb and "6.900" in fb)
fb_b = ins._fakten_block(req(inserat_text=None, wartungsnachweise=None))
check("D3 ohne Angabe und ohne Text steht keine erfundene Unsicherheit im Faktenblock",
      "Unfallstatus" not in fb_b and "Wartungsnachweise" not in fb_b)

SAETZE = {
    "S1 ehrlicher Unsicherheitssatz": "Zur Unfallfreiheit kann ich keine sichere Angabe machen.",
    "S2 gemischt: Unsicherheit + Behauptung": "Zur Unfallfreiheit kann ich keine sichere Angabe machen, das Fahrzeug ist aber unfallfrei.",
    "S3 Negation + Behauptung": "Keine Mängel bekannt, unfallfrei.",
    "S4 Behauptung 'keine Unfallschäden'": "Keine Unfallschäden bekannt.",
    "S5 negative Tatsachenbehauptung": "Das Fahrzeug hatte einen Unfall.",
    "S6 Scheckheft/vollständige Historie": "Scheckheftgepflegt mit vollständiger Servicehistorie.",
    "S7 vollständige Nachweise behauptet": "Es gibt vollständige Wartungsnachweise.",
    "S8 ehrliche Nachweisgrenze (teilweise)": "Die Wartungsnachweise sind teilweise vorhanden.",
    "S9 ehrliche Nachweisgrenze (nicht vollständig)": "Die Wartungsnachweise sind nicht vollständig.",
    "S10 Nachlackierung": "Kotflügel vorne rechts nachlackiert, Ursache unbekannt.",
}
STUB = {"titel": "VW Passat B6 2.0 TDI 140 PS | Schaltgetriebe | 180.000 km",
        "beschreibung": "**Zustand und Wartung**\n" + "\n".join(f"- {s}" for s in SAETZE.values())}


async def _g2(system, user):
    CAP["opt_system"], CAP["opt_user"] = system, user
    return STUB


ins.call_gemini_json = _g2
o = asyncio.run(ins.run_inserat_optimierung(req()))
entfernt_txt = " | ".join(o.entfernte_behauptungen)
behalten = {k: s in o.beschreibung for k, s in SAETZE.items()}
print("   rohe Stub-Antwort: 10 Sätze; entfernt:", len(o.entfernte_behauptungen), "| Endausgabe-Abschnitt:")
for k, b in behalten.items():
    print(f"     {'BEHALTEN' if b else 'ENTFERNT '} {k}")
check("D4 Originalfall: ehrlicher Unsicherheitssatz bleibt (Unsicherheit ist vom Verkäufer angegeben)", behalten["S1 ehrlicher Unsicherheitssatz"])
check("D5 gemischter Satz und Negation+Behauptung umgehen den Guard NICHT",
      not behalten["S2 gemischt: Unsicherheit + Behauptung"] and not behalten["S3 Negation + Behauptung"])
check("D6 negative und positive Tatsachenbehauptung ohne Grundlage bleiben gesperrt",
      not behalten["S4 Behauptung 'keine Unfallschäden'"] and not behalten["S5 negative Tatsachenbehauptung"])
check("D7 Service/Scheckheft wird nicht zu vollständiger Historie",
      not behalten["S6 Scheckheft/vollständige Historie"] and not behalten["S7 vollständige Nachweise behauptet"])
check("D8 ehrliche Nachweisgrenzen bleiben",
      behalten["S8 ehrliche Nachweisgrenze (teilweise)"] and behalten["S9 ehrliche Nachweisgrenze (nicht vollständig)"])
check("D9 Nachlackierung bleibt", behalten["S10 Nachlackierung"])
check("D10 jede Entfernung ist mit Satz und Grund gemeldet", len(o.entfernte_behauptungen) == 6
      and all("„" in e for e in o.entfernte_behauptungen), entfernt_txt)
check("D11 Systemprompt: unbekannt/teilweise benennen, keine Unterlagen/Fotos erfinden",
      "genau so" in CAP["opt_system"] and "KEINE bereitliegenden Unterlagen oder Fotos" in CAP["opt_system"])
check("D12 Optimierungs-Prompt enthält die Untergrenze nicht", "6.000" not in CAP["opt_user"] and "Untergrenze" not in CAP["opt_user"])

# Gegenfälle zur Grundlage der Unsicherheit
o_b = asyncio.run(ins.run_inserat_optimierung(req(inserat_text=None, wartungsnachweise=None)))
check("D13 GEGENFALL ohne Angabe/Text: vom Modell erfundene Unsicherheit bleibt gesperrt (keine Grundlage)",
      "Zur Unfallfreiheit kann ich keine sichere Angabe machen." not in o_b.beschreibung)
o_ja = asyncio.run(ins.run_inserat_optimierung(req(unfallfrei="ja")))
check("D14 GEGENFALL Formular 'ja': Behauptung 'unfallfrei' bleibt, der Unsicherheitssatz (Widerspruch) entfällt",
      "ist aber unfallfrei" in o_ja.beschreibung and "Keine Mängel bekannt, unfallfrei." in o_ja.beschreibung
      and "Zur Unfallfreiheit kann ich keine sichere Angabe machen." not in o_ja.beschreibung.replace(
          "Zur Unfallfreiheit kann ich keine sichere Angabe machen, das Fahrzeug ist aber unfallfrei.", ""))
o_nein = asyncio.run(ins.run_inserat_optimierung(req(unfallfrei="nein")))
check("D15 GEGENFALL Formular 'nein': 'hatte einen Unfall' ist belegt und bleibt", "Das Fahrzeug hatte einen Unfall." in o_nein.beschreibung)
o_voll = asyncio.run(ins.run_inserat_optimierung(req(wartungsnachweise="vollstaendig")))
check("D16 GEGENFALL Formular 'vollständig': der Satz wird NICHT als unbelegt entfernt (Angabe des Verkäufers); die bestehende "
      "Verstärkungs-Entschärfung darf ihn abschwächen",
      not any("Vollständige Wartungshistorie" in e for e in o_voll.entfernte_behauptungen)
      and re.search(r"Es gibt (?:vollständige )?Wartungsnachweise\.", o_voll.beschreibung) is not None,
      o_voll.beschreibung[-300:])

# ══════════════════════════════════════════════════════════════════════════════
print("\n=== E) Verhandlung: Handlungsvorschläge statt erledigter Tatsachen ===")
arg = {a["einwand"]: a["antwort"] for a in plan["verhandlung"]["argumente"]}
alle = " ".join(arg.values())
check("E1 keine erfundenen Fotos, keine erledigte Offenlegung, keine berücksichtigten Abschläge",
      not re.search(r"genannt und fotografiert|im Preis berücksichtigt|Ist im Inserat genannt|fotografiert;", alle), alle[:300])
check("E2 Mängel: Vorschlag (nennen, fotografieren, beschreiben), Preisentscheidung bleibt beim Verkäufer",
      "Im Inserat nennen und fotografieren" in arg["Kratzer am Heckstoßfänger"]
      and "entscheidest du" in arg["Kratzer am Heckstoßfänger"]
      and "Im Inserat konkret beschreiben" in arg["Klimaanlage kühlt schwach"]
      and "entscheidest du" in arg["Klimaanlage kühlt schwach"])
check("E3 Laufleistung bei 'teilweise': nur teilweise belegbar, Service ist Angabe und kein Beleg",
      "nur teilweise" in arg["Laufleistung"] and "kein Beleg" in arg["Laufleistung"]
      and "Mit Serviceeinträgen und HU-Berichten belegbar" not in arg["Laufleistung"], arg["Laufleistung"])
_, plan_ev, _ = lauf(wartungsnachweise="vollstaendig")
arg_v = {a["einwand"]: a["antwort"] for a in plan_ev["verhandlung"]["argumente"]}
check("E4 'vollständig' ist die Angabe des Verkäufers, keine Dokumentprüfung",
      "Laut deiner Angabe" in arg_v["Laufleistung"] and "ENFAL prüft diese Unterlagen nicht" in arg_v["Laufleistung"])
_, plan_en, _ = lauf(inserat_text=None, wartungsnachweise=None, letzter_service_datum=None, letzter_service_km=None)
arg_n = {a["einwand"]: a["antwort"] for a in plan_en["verhandlung"]["argumente"]}
check("E5 ohne Nachweise keine Belegbehauptung", "Ohne bekannte Wartungsnachweise" in arg_n["Laufleistung"]
      and "belegbar:" not in arg_n["Laufleistung"])
check("E6 2 Vorbesitzer: keine Behauptung nachvollziehbarer Historie bei Teilnachweisen",
      "Historie ist über Serviceheft" not in arg["2 Vorbesitzer"] and "Lücken benennst du ehrlich" in arg["2 Vorbesitzer"])
check("E7 private Untergrenze steht nur im privaten Verhandlungsteil, Spielraum bleibt",
      plan["verhandlung"]["preise"]["untergrenze_eur"] == 6000 and plan["verhandlung"]["preise"]["spielraum_eur"] == 900)

# ══════════════════════════════════════════════════════════════════════════════
print("\n=== F) Foto-Plan: sichtbarer gegen funktionalen Mangel ===")
motive = {f["motiv"]: f["tipp"] for f in plan["fotoplan"]["fotos"]}
check("F1 optische Schäden werden gezeigt", "Mangel: Kratzer am Heckstoßfänger" in motive
      and "Mangel: Steinschläge auf der Motorhaube" in motive)
check("F2 Funktionsmängel ohne sichtbaren Beleg sind KEIN Nahaufnahme-Motiv",
      "Mangel: Klimaanlage kühlt schwach" not in motive and "Mangel: Parksensoren hinten fallen zeitweise aus" not in motive)
hin = " ".join(plan["fotoplan"]["hinweise"])
check("F3 sie werden durch konkrete Beschreibung/Befund behandelt, ohne Ursachendiagnose",
      "Klimaanlage kühlt schwach" in hin and "nicht erkennbar" in hin and "Werkstattbefund" in hin
      and "ohne Befund keine Ursache nennen" in hin)
_, plan_sb, _ = lauf(technische_maengel=["Ölverlust am Motor sichtbar", "Parksensoren hinten fallen zeitweise aus"])
motive_sb = {f["motiv"] for f in plan_sb["fotoplan"]["fotos"]}
check("F4 technischer Mangel MIT sichtbarem Beleg wird fotografiert, der Funktionsmangel nicht",
      "Mangel: Ölverlust am Motor sichtbar" in motive_sb and "Mangel: Parksensoren hinten fallen zeitweise aus" not in motive_sb)
cockpit = motive["Cockpit mit Lenkrad"]
check("F5 Cockpit-Tipp neutral: Anzeigen lesbar fotografieren, Störungsmeldungen nicht verdecken, wahrheitsgemäß beschreiben",
      cockpit == "Cockpit und Anzeigen gut lesbar fotografieren. Vorhandene Störungsmeldungen nicht verdecken und "
                 "wahrheitsgemäß beschreiben.", cockpit)
check("F6 keine Gleichsetzung jeder Kontrollanzeige mit einem Defekt, keine Aufforderung zum Ausschalten",
      not re.search(r"ohne\s+warnleuchten|abschalten|ausschalten|ausmachen|defekt", cockpit, re.IGNORECASE), cockpit)

# ══════════════════════════════════════════════════════════════════════════════
print("\n=== G) HU-/Serviceparser (VK-08): Datum an seine Bedeutung gebunden ===")
base = dict(inserat_text="x", beschreibung=None)


def probleme(text, **kw):
    """ALLE Widersprüche des Parsers (keine Filterung): ein Service-Kilometerstand darf keinen Konflikt erzeugen."""
    return ins.finde_widersprueche(req(inserat_text=text, **kw))


check("G1 Service neben HU: kein falscher Konflikt", probleme("Letzter Service 09/2026 bei 177.000 km. HU 08/2027.") == [])
check("G2 Service und HU in einer Klausel, HU zuletzt: HU-Datum zählt und stimmt", probleme("Service 09/2026, HU 08/2027.") == [])
check("G3 echter HU-Konflikt bleibt erkennbar",
      any("05/2027" in p and "08/2027" in p for p in probleme("HU bis 05/2027. Letzter Service 09/2026.")))
check("G4 'TÜV neu 08/2027' passt zur Angabe: kein Konflikt", probleme("TÜV neu 08/2027.") == [])
check("G5 Erstzulassung/Service ohne HU-Wort: kein Konflikt", probleme("Erstzulassung 09/2009, Service 09/2026.") == [])
check("G6 mehrdeutige Fundstelle (Datum ohne Bezug): keine Konfliktbehauptung", probleme("Termin 03/2028 steht an.") == [])
check("G7 'HU und Service 09/2026' (Service zuletzt) ist mehrdeutig: keine Konfliktbehauptung",
      probleme("HU und Service 09/2026 gemacht.") == [])
check("G8 Ohne HU-Angabe im Formular bleibt der Parser still", probleme("HU 05/2027.", tuev_bis=None) == [])

# ══════════════════════════════════════════════════════════════════════════════
print("\n=== K) Kilometerkonflikte: aktueller Stand gegen historische Wartungs-/Reparaturangabe (G1) ===")
K = ("Verkaufe meinen Passat mit 180.000 km. Letzter Service 09/2026 bei 177.000 km.")
check("K1 Originalfall: aktueller Stand 180.000 und Service 177.000 im selben Text: kein Konflikt", probleme(K) == [], str(probleme(K)))
check("K2 nur historischer Servicestand 177.000 im Text: kein erfundener aktueller Kilometerkonflikt (ursprüngliches G1)",
      probleme("Letzter Service 09/2026 bei 177.000 km. HU 08/2027.") == [],
      str(probleme("Letzter Service 09/2026 bei 177.000 km. HU 08/2027.")))
check("K3 ausdrücklich anderer AKTUELLER Stand bleibt ein echter Konflikt",
      any("Kilometerstand 150.000" in p and "180.000" in p for p in probleme("Aktueller Kilometerstand 150.000 km.")),
      str(probleme("Aktueller Kilometerstand 150.000 km.")))
gemischt = probleme("Kilometerstand 150.000 km. Letzter Service bei 177.000 km.")
check("K4 aktueller Stand und historische Servicezahl im selben Text bleiben getrennt: nur 150.000 ist der Konflikt",
      any("Kilometerstand 150.000" in p for p in gemischt) and not any("177.000" in p for p in gemischt), str(gemischt))
check("K5 'gelaufen' markiert den aktuellen Stand, 'Service bei' den historischen (Komma-Satz)",
      probleme("180.000 km gelaufen, Service bei 177.000 km.") == [])
check("K6 Reparatur-/Wartungsangabe: 'Zahnriemen bei 120.000 km gewechselt' und '120.000 km beim letzten Service' sind historisch",
      probleme("Zahnriemen bei 120.000 km gewechselt. Aktuell 180.000 km.") == []
      and probleme("120.000 km beim letzten Service. Kilometerstand 180.000 km.") == [])
check("K7 Zahl ohne Bedeutungswort bleibt eine Angabe zum Fahrzeug (bisheriges Verhalten): abweichend = Konflikt",
      any("150.000" in p for p in probleme("Passat mit 150.000 km.")), str(probleme("Passat mit 150.000 km.")))
check("K8 nur historische Zahlen erzeugen keinen Konflikt, auch wenn sie vom Formularstand abweichen",
      probleme("Inspektion bei 90.000 km, Ölwechsel bei 120.000 km.") == [])

# ══════════════════════════════════════════════════════════════════════════════
print("\n=== I) Unfall-Guard: Ereignis und konkrete Schadens-/Reparaturdetails getrennt belegt (Nachkorrektur) ===")


def bleibt(satz, **kw):
    r = req(**kw)
    return ins._satz_ok(satz, ins._claim_rules(r), ins._ausstattung_backed(r), [])


def titel_bleibt(seg, **kw):
    ent: list[str] = []
    return bool(ins._scrub_titel(f"VW Passat | {seg}", req(**kw), ent)) and not ent


ALLG = "Das Fahrzeug hatte einen Unfall."
SCHADEN = "Der Unfallschaden an der Stoßstange wurde repariert."
DET_VOLL = "Unfallschaden an der Stoßstange, fachgerecht repariert"
check("I1 allgemeine Unfallbehauptung: bei 'nein' zulässig, bei 'ja' und ohne Angabe gesperrt",
      bleibt(ALLG, unfallfrei="nein") and not bleibt(ALLG, unfallfrei="ja") and not bleibt(ALLG, unfallfrei=None))
check("I2 Schaden-/Reparatursatz ohne Verkäuferangabe zu Schaden/Reparatur: auch bei 'nein' gesperrt",
      not bleibt(SCHADEN, unfallfrei="nein") and not bleibt(SCHADEN, unfallfrei="ja") and not bleibt(SCHADEN, unfallfrei=None))
check("I3 ausdrückliche passende Angabe zu Schaden UND Reparatur + 'nein': Aussage bleibt",
      bleibt(SCHADEN, unfallfrei="nein", vorschaeden=DET_VOLL))
check("I4 GEGENFALL: Angabe nur zum Schaden (Stoßstange), nicht zur Reparatur: Reparaturaussage bleibt gesperrt",
      not bleibt(SCHADEN, unfallfrei="nein", vorschaeden="Unfallschaden an der Stoßstange"))
check("I5 GEGENFALL: Angabe zu Schaden UND Reparatur, aber Formular 'ja'/leer: bleibt gesperrt (Ereignis unbelegt)",
      not bleibt(SCHADEN, unfallfrei="ja", vorschaeden=DET_VOLL) and not bleibt(SCHADEN, unfallfrei=None, vorschaeden=DET_VOLL))
check("I6 GEGENFALL: anderes Bauteil als angegeben (Kotflügel statt Stoßstange): gesperrt",
      not bleibt(SCHADEN, unfallfrei="nein", vorschaeden="Unfallschaden am Kotflügel, fachgerecht repariert"))
check("I7 Nachlackierung allein beweist keinen Unfall: weder Formularstatus noch Detail daraus abgeleitet",
      ins.unfall_status(req(unfallfrei=None, inserat_text=None))[0] == ins.UNFALL_NICHT_ANGEGEBEN
      and not bleibt("Unfallschaden am Kotflügel vorne rechts.", unfallfrei=None)
      and not bleibt("Der Kotflügel wurde nach einem Unfallschaden repariert.", unfallfrei="nein"))
check("I8 Themenaussage ohne Details bei 'nein': 'Unfallschaden vorhanden.' bleibt; bei 'ja'/leer gesperrt",
      bleibt("Unfallschaden vorhanden.", unfallfrei="nein") and not bleibt("Unfallschaden vorhanden.", unfallfrei="ja")
      and not bleibt("Unfallschaden vorhanden.", unfallfrei=None))
check("I9 ehrlicher Unsicherheitssatz (Verkäufer 'unbekannt') und Mischsatz-Gegenproben bleiben wie bisher",
      bleibt("Zur Unfallfreiheit kann ich keine sichere Angabe machen.", unfallfrei="unbekannt")
      and not bleibt("Zur Unfallfreiheit kann ich keine sichere Angabe machen, das Fahrzeug ist aber unfallfrei.", unfallfrei="unbekannt")
      and not bleibt("Keine Mängel bekannt, unfallfrei.", unfallfrei="nein")
      and not bleibt("Nicht unfallfrei, Stoßstange fachgerecht repariert.", unfallfrei="nein")
      and bleibt("Nicht unfallfrei, Stoßstange fachgerecht repariert.", unfallfrei="nein", vorschaeden=DET_VOLL))
check("I10 Titel- und Beschreibungs-Guard wenden dieselbe Regel an",
      all(titel_bleibt(s, **kw) == bleibt(s, **kw) for s, kw in [
          (ALLG, dict(unfallfrei="nein")), (ALLG, dict(unfallfrei="ja")), (SCHADEN, dict(unfallfrei="nein")),
          (SCHADEN, dict(unfallfrei="nein", vorschaeden=DET_VOLL)), ("Unfallschaden vorhanden", dict(unfallfrei=None))]))
STUB_I = {"titel": "VW Passat B6 | " + SCHADEN.rstrip("."),
          "beschreibung": "**Zustand**\n- " + SCHADEN + "\n- " + ALLG}
for _f, _kw, _erw in [("ohne Detailangabe", dict(unfallfrei="nein"), (False, True)),
                      ("mit Detailangabe", dict(unfallfrei="nein", vorschaeden=DET_VOLL), (True, True))]:
    async def _gi(system, user, _s=STUB_I):
        return _s
    ins.call_gemini_json = _gi
    _o = asyncio.run(ins.run_inserat_optimierung(req(**_kw)))
    check(f"I11 Endausgabe ({_f}): Schadenssatz {'bleibt' if _erw[0] else 'entfällt'}, allgemeiner Unfallsatz bleibt",
          (SCHADEN in _o.beschreibung) == _erw[0] and (ALLG in _o.beschreibung) == _erw[1], _o.beschreibung)
ins.call_gemini_json = _g2

# ══════════════════════════════════════════════════════════════════════════════
print("\n=== H) Private Untergrenze bleibt aus den öffentlichen Inseraten und dem Optimierungs-Faktenblock heraus ===")
oeffentlich = json.dumps(plan["inserat"], ensure_ascii=False) + o.beschreibung + o.titel
check("H1 weder Inseratspaket noch Optimierungsergebnis nennen 6.000/6000/Untergrenze",
      not re.search(r"6\.000|6000|Untergrenze", oeffentlich), "")
check("H2 öffentliche Preisvorstellung (6.900) steht im Faktenblock, nicht die Untergrenze", "6.900" in fb and "6.000" not in fb)

print(f"\n{'ALLE GRUEN' if not _FEHLER else 'FEHLER: ' + ', '.join(_FEHLER)}")
raise SystemExit(1 if _FEHLER else 0)
