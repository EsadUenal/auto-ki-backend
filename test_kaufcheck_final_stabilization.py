"""
KaufCheck-Final-Stabilization — GENERISCHE semantische Invarianten.

Keine Fahrzeugbeispiele als Testgegenstand: fast alle Fälle laufen über
synthetische Marken/Modelle/Motorzeilen ("Testmarke Alpha", Motorcode "QX12"),
damit jede Prüfung eine REGEL absichert und nicht ein einzelnes Auto. Kein
Netzwerk, kein echter Gemini-/Tavily-Aufruf (Web über den Fixture-Provider,
Gemini gestubbt).

  A) Provenienz            H) Web-Identität (Phasen, Alignment, Konsens)
  B) Tri-State             I) Web-Risiko-Geltungsbereich
  C) Optionale Ausstattung J) Empfehlungs-Identitäts-Floor (echter Pfad)
  D) Kraftstoff/Antriebsart K) Anzeige/Dedup/Kosten
  E) Mehrdeutigkeit        L) Datenbasis
  F) Motorcode             M) Prüfklassen-Mapping (Probefahrt)
  G) Rückruf-Applicability

    python test_kaufcheck_final_stabilization.py
"""
import asyncio

import app.recall_filter as _rf

_rf.get_rueckruf_referenzen_kurz = lambda: []

from app import tristate as ts
from app.anzeige import (
    fahrzeug_titel, feldzeile, kosten_anzeige, liste_ohne_wiederholung, ohne_wiederholung,
)
from app.ausstattung_praesenz import ABSENT, PRESENT, UNKNOWN, praesenz
from app.bekannte_fakten import aus_request, basistexte, tuning_status, unfall_status
from app.car_lookup import _fehlende_angabe_none
from app.empfehlung_gruende import baue_empfehlung_gruende
from app.empfehlungs_policy import (
    ANZEIGE_LIMITED, INSUFFICIENT, PARTIAL, STATE_LIMITED, STATE_NORMAL, VERIFIED, entscheide,
)
from app.evidence import build_insights
from app.kaufaktionen import build_kaufaktionen, _komponente, _probefahrt_symptom
from app.kaufcheck_bericht import bericht, datenbasis_objekt
from app.key_findings import build_key_findings_kauf
from app.models import (
    Insight, KaufCheckRequest, TechnischeRecherche, WebVehicleIdentity,
)
from app.recall_filter import (
    RECALL_NOT_APPLICABLE, RECALL_SERIES_RELEVANT, RECALL_UNKNOWN, RECALL_VARIANT_POSSIBLE,
    rueckruf_applicability, rueckruf_scope,
)
from app.technical_research import (
    FixtureTechnicalResearchProvider, _extrahiere_fakten, ausgerichtet, konsens,
    recherchiere_technisch, werte_identitaet_aus,
)
from app.vehicle_identity import VehicleIdentity, anzeige_marke, motorcodes
from app.vergleichstabelle import als_markdown, baue_zeilen

_FEHLER: list[str] = []
_ANZAHL = {"n": 0}


def check(name: str, bedingung: bool) -> None:
    _ANZAHL["n"] += 1
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def t(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# ── Synthetische Fahrzeugdaten (keine realen Modelle) ──────────────────────────

def motor(vid, bez, code, kraftstoff, ps, getriebe='["6-Gang Manuell"]', antrieb="Front", **kw):
    return {"variante_id": vid, "bezeichnung": bez, "motorcode": code, "kraftstoff": kraftstoff,
            "leistung_ps": ps, "getriebe": getriebe, "antrieb": antrieb,
            "schwachstellen_motor": kw.get("probleme", []), "kritische_wartung": []}


def baureihe(motoren=None, schwach=None, rueckrufe=None):
    return {"id": "testmarke-alpha-t1", "marke": "Testmarke", "modell": "Alpha",
            "generation": "T1", "bauzeitraum_von": 2014, "bauzeitraum_bis": 2022,
            "motoren": motoren or [], "schwachstellen_baureihe": schwach or [],
            "rueckrufe": rueckrufe or []}


def req(**kw):
    basis = dict(marke="Testmarke", modell="Alpha", baujahr=2018)
    basis.update(kw)
    return KaufCheckRequest(**basis)


def ident(b, m, r):
    return VehicleIdentity.from_check_context(b, m, r)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== A) Provenienz ===")
m_a = motor("a-1", "2.0 T (150 PS)", "QX12B20", "Benzin", 150,
            getriebe='["6-Gang Manuell"]', antrieb="Front")
b_a = baureihe([m_a])
r_a = req(motor="QX12 2.0 Turbo", kraftstoff="Benzin", leistung_ps=150, getriebe="manuell",
          antrieb="Front")
i_a = ident(b_a, m_a, r_a)
fe = i_a.field_evidence
check("A1 Nutzer+DB gleich -> Primärquelle Nutzer, DB bestätigt (Kraftstoff)",
      fe["fuel"]["primary_source"] == "user" and fe["fuel"]["confirmed_by"] == ["enfal"]
      and fe["fuel"]["verification_state"] == "user_confirmed")
for feld in ("horsepower", "transmission", "drivetrain", "make", "model", "year"):
    check(f"A2 Nutzerangabe '{feld}' bleibt Primärquelle",
          fe[feld]["primary_source"] == "user" and "enfal" in fe[feld]["confirmed_by"])
check("A3 DB präzisiert Motorcode, Rohangabe bleibt erhalten",
      i_a.engine_code == "QX12B20" and fe["engine_code"]["raw_user_value"] == "QX12"
      and fe["engine_code"]["verification_state"] == "user_refined")
check("A4 Legacy-Status unterscheidet bestätigt von reiner Referenz",
      fe["fuel"]["status"] == "confirmed" and fe["generation"]["status"] == "identified")
zeile = feldzeile("Kraftstoff", "fuel", i_a.fuel, fe["fuel"])
check("A5 Bericht: 'laut Inserat/Nutzereingabe; bestätigt durch ENFAL-Fahrzeugdaten'",
      "laut Inserat/Nutzereingabe" in zeile and "bestätigt durch ENFAL-Fahrzeugdaten" in zeile)
check("A6 Bericht nennt KEIN reines 'ENFAL-Referenz' für eine Nutzerangabe",
      "Referenz" not in zeile)
i_konf = ident(b_a, m_a, req(motor="2.0 Turbo", kraftstoff="Diesel", leistung_ps=150))
check("A7 Widerspruch Nutzer/DB: Nutzerwert bleibt, Konflikt sichtbar, nie still überschrieben",
      i_konf.fuel == "diesel" and i_konf.field_evidence["fuel"]["verification_state"] == "conflict"
      and "abweichend" in feldzeile("Kraftstoff", "fuel", i_konf.fuel, i_konf.field_evidence["fuel"]))
# Web bestätigt Nutzer / füllt Lücke / widerspricht
i_web = ident(None, None, req(motor="2.0 X", kraftstoff="Benzin", leistung_ps=160, getriebe="manuell"))
web = TechnischeRecherche(ausgeloest_durch="db_miss", identitaet=WebVehicleIdentity(
    belegt=True, marke="Testmarke", modell="Alpha", confidence="mittel", belegende_domains=2,
    feldwerte={"horsepower": {"value": 160, "confidence": "mittel", "domains": 2},
               "generation": {"value": "T1", "confidence": "mittel", "domains": 2},
               "fuel": {"value": "diesel", "confidence": "mittel", "domains": 2}}))
i_web.apply_web_evidence(web)
fw = i_web.field_evidence
check("A8 Web bestätigt Nutzer -> Nutzer bleibt Primärquelle, Web in confirmed_by",
      fw["horsepower"]["primary_source"] == "user" and "web" in fw["horsepower"]["confirmed_by"])
check("A9 Web füllt Lücke -> Primärquelle Web",
      i_web.generation == "T1" and fw["generation"]["primary_source"] == "web"
      and fw["generation"]["verification_state"] == "web_only")
check("A10 Web widerspricht Nutzer -> Nutzerwert bleibt, Konflikt vermerkt",
      i_web.fuel == "benzin" and fw["fuel"].get("web_conflict") == "diesel")
i_nur_user = ident(None, None, req(motor="2.0 X"))
i_nur_user.apply_web_evidence(TechnischeRecherche(identitaet=WebVehicleIdentity(
    belegt=True, marke="Testmarke", modell="Alpha", motor="2.0 X", belegende_domains=2)))
check("A11 Nutzer-Motortext wird durch eine Web-Identität NIE zu einem Web-Primärwert",
      i_nur_user.field_evidence["engine_name"]["primary_source"] == "user")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== B) Tri-State ===")
UNKNOWN_PHRASEN = ["nicht angegeben", "keine Angabe", "keine eindeutige Angabe", "unbekannt",
                   "nicht bekannt", "Hierzu liegen keine Angaben vor", "keine Informationen vorhanden",
                   "Angabe fehlt: nicht angegeben", "unklar", "k.A."]
for feld, thema in ((ts.UNFALL, "Unfallstatus"), (ts.TUNING, "Tuning"),
                    (ts.NACHLACKIERUNG, "Nachlackierungen"), (ts.REPARATUREN, "Reparaturen"),
                    (ts.IMPORT, "Import"), (ts.MAENGEL, "Mängel"), (ts.SERVICE, "Scheckheft")):
    for p in UNKNOWN_PHRASEN:
        z = ts.bewerte(feld, None, f"{thema}: {p}.")
        check(f"B1 {feld.name}: '{thema}: {p}' -> UNKNOWN", z.state == ts.UNKNOWN)
    check(f"B2 {feld.name}: Auswahlfeld 'nicht angegeben' -> UNKNOWN",
          ts.bewerte(feld, "nicht angegeben", None).state == ts.UNKNOWN)
for text in ("Keine eindeutige Angabe zu früheren Unfallschäden oder Nachlackierungen.",
             "Unfallfrei: nicht angegeben", "Ob das Fahrzeug unfallfrei ist, ist unklar.",
             "Unfallfrei? Keine Angabe", "Zum Thema unfallfrei liegen keine Informationen vor."):
    r_b = req(beschreibung=text)
    check(f"B3 '{text[:45]}' -> Unfall UNKNOWN (Fassade)", unfall_status(r_b) == "unknown")
    alles = " ".join(f"{a.titel} {a.aktion}" for bereich in ("verkaeuferfragen", "dokumente")
                     for a in getattr(build_kaufaktionen(r_b, None, None, []), bereich).fahrzeugspezifisch
                     + getattr(build_kaufaktionen(r_b, None, None, []), bereich).basis)
    gruende = " ".join(baue_empfehlung_gruende(r_b, None, None, [], [], "unbekannt", False, None))
    check("B4 ... kein 'trotz der Angabe unfallfrei' / 'Unfallfreiheit schriftlich festhalten'",
          "trotz der Angabe" not in alles and "Unfallfreiheit schriftlich" not in alles)
    check("B5 ... kein 'Laut Inserat unfallfrei' in den Empfehlungsgründen",
          "Laut Inserat unfallfrei" not in gruende)
check("B6 'unfallfrei' -> behauptete Abwesenheit, nie verifiziert",
      ts.bewerte(ts.UNFALL, None, "Unfallfrei.").state == ts.CLAIMED_ABSENT
      and ts.bewerte(ts.UNFALL, None, "Unfallfrei.").as_dict()["verified"] is False)
z_bek = ts.bewerte(ts.UNFALL, None, "Keine Unfallschäden bekannt.")
check("B7 'keine Unfallschäden bekannt' -> behauptete Abwesenheit mit Einschränkung",
      z_bek.state == ts.CLAIMED_ABSENT and z_bek.eingeschraenkt)
check("B8 'Kein Tuning angegeben' -> UNKNOWN", tuning_status(req(tuning="Kein Tuning angegeben")) == "unknown")
check("B9 'Kein Tuning vorhanden' -> behauptete Abwesenheit",
      tuning_status(req(tuning="Kein Tuning vorhanden")) == "absent")
check("B10 'Stage 1' -> behauptetes Merkmal", tuning_status(req(tuning="Stage 1")) == "present")
check("B11 'nicht unfallfrei' -> behauptetes Merkmal, nicht Abwesenheit",
      ts.bewerte(ts.UNFALL, None, "nicht unfallfrei").state == ts.CLAIMED_PRESENT)
check("B12 Widersprüchliche Behauptungen -> UNKNOWN mit Konflikt",
      ts.bewerte(ts.UNFALL, None, "Unfallfrei. Leichter Unfallschaden hinten repariert.").konflikt)
check("B13 Auswahl 'ja' + Text 'Unfallstatus unbekannt' -> UNKNOWN (Unsicherheit überstimmt Häkchen)",
      ts.bewerte(ts.UNFALL, "ja", "Unfallstatus unbekannt").state == ts.UNKNOWN)
check("B14 'unfallfrei' + anderes Thema 'nicht angegeben' im selben Satz bleibt 'unfallfrei'",
      ts.bewerte(ts.UNFALL, None, "Unfallfrei, Tuning nicht angegeben.").state == ts.CLAIMED_ABSENT)
check("B15 Das Modell kennt keinen 'verified'-Zustand",
      not hasattr(ts, "VERIFIED") and set(ts.__dict__) & {"CLAIMED_PRESENT", "CLAIMED_ABSENT", "UNKNOWN"})
fragen = [a for a in build_kaufaktionen(req(tuning="nicht angegeben"), None, None, []).verkaeuferfragen.basis
          if "leistungsgesteigert" in a.titel or "Tuning" in a.titel]
check("B16 Tuning UNKNOWN -> genau EINE Tuning-Frage", len(fragen) == 1)
check("B17 Alle Tri-State-Themen im kanonischen Fakten-Objekt",
      set(aus_request(req()).angaben) == {"unfall", "tuning", "nachlackierung", "reparaturen",
                                          "import", "maengel", "service"})


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== C) Optionale Ausstattung (generische Komponente 'adaptive Dämpfer') ===")
schwach_c = [{"id": 901, "bauteil": "Adaptive Dämpfer", "beschreibung": "Regelventile der adaptiven "
              "Dämpfer können ausfallen.", "schweregrad": "hoch", "betroffene_baujahre": "2014-2022"}]
m_c = motor("c-1", "2.0 (190 PS)", "QX20", "Benzin", 190)
b_c = baureihe([m_c], schwach=schwach_c)


def lauf_c(**kw):
    r = req(motor="2.0", kraftstoff="Benzin", leistung_ps=190, getriebe="manuell", **kw)
    i = ident(b_c, m_c, r)
    ins = build_insights(b_c, m_c, [], r, check_typ="kauf", identity=i)
    ka = build_kaufaktionen(r, b_c, m_c, ins)
    kf = build_key_findings_kauf(r, b_c, m_c, ins)
    return r, i, ins, ka, kf


def aktionen_zu(ka, wort):
    return [a for b in ("besichtigung", "probefahrt", "verkaeuferfragen", "dokumente")
            for a in getattr(ka, b).fahrzeugspezifisch if wort in f"{a.titel} {a.aktion}"]


r_u, i_u, ins_u, ka_u, kf_u = lauf_c(ausstattung=[])
d_u = [i for i in ins_u if (i.bauteil or "").startswith("Adaptive")]
check("C1 UNKNOWN: Insight bleibt, presence_state=unknown", d_u and d_u[0].presence_state == UNKNOWN)
check("C2 UNKNOWN: Titel ist bedingt ('Falls … vorhanden')", d_u and d_u[0].titel.startswith("Falls "))
akt_u = aktionen_zu(ka_u, "Dämpfer")
check("C3 UNKNOWN: JEDE abgeleitete Aktion (alle Bereiche) ist bedingt",
      akt_u and all("Falls " in f"{a.titel} {a.aktion}" for a in akt_u))
check("C4 UNKNOWN: Aktionen in Besichtigung, Probefahrt, Fragen und Dokumenten",
      {a.bereich for a in akt_u} >= {"besichtigung", "probefahrt", "verkaeuferfragen", "dokumente"})
check("C5 UNKNOWN: Key Finding bleibt bedingt",
      all("falls" in f.beschreibung.lower() for f in kf_u if "Dämpfer" in f.beschreibung))
r_p, i_p, ins_p, ka_p, _ = lauf_c(ausstattung=["Adaptives Fahrwerk"])
d_p = [i for i in ins_p if (i.bauteil or "").startswith("Adaptive")]
check("C6 PRESENT: normaler Prüfpunkt ohne Bedingung",
      d_p and d_p[0].presence_state == PRESENT and not d_p[0].titel.startswith("Falls ")
      and all("Falls adaptive" not in f"{a.titel} {a.aktion}" for a in aktionen_zu(ka_p, "Dämpfer")))
r_x, i_x, ins_x, ka_x, _ = lauf_c(beschreibung="Fahrzeug ohne adaptive Dämpfer, Serienfahrwerk.")
check("C7 ABSENT: aus Risikomenge und allen Aktionen entfernt",
      not [i for i in ins_x if (i.bauteil or "").startswith("Adaptive")]
      and not aktionen_zu(ka_x, "Dämpfer"))
text_bericht = bericht(r_u, i_u, b_c, m_c, ins_u, ka_u, [], "kaufen_nach_besichtigung",
                       type("PA", (), {"label": None})(), False, None, None, ["Inserat"])
check("C8 UNKNOWN: auch der Vollbericht zeigt die Bedingung",
      "Falls adaptive Dämpfer vorhanden" in text_bericht)
# Fahrzeugeigenschaften
i_manuell = ident(b_c, m_c, req(getriebe="manuell", motor="2.0", leistung_ps=190))
i_auto_unb = ident(b_c, None, req())
check("C9 DKG-Komponente am Schaltwagen -> ABSENT",
      praesenz("Doppelkupplungsgetriebe (DKG)", i_manuell)[0] == ABSENT)
check("C10 DKG-Komponente, Getriebe unbekannt -> UNKNOWN (bedingt)",
      praesenz("Doppelkupplungsgetriebe (DKG)", i_auto_unb)[0] == UNKNOWN)
i_benzin = ident(b_c, m_c, req(kraftstoff="Benzin", leistung_ps=190, motor="2.0"))
check("C11 AdBlue am Benziner -> ABSENT", praesenz("AdBlue-Dosierung", i_benzin)[0] == ABSENT)
check("C12 Hochvolt am Verbrenner -> ABSENT", praesenz("Hochvoltbatterie", i_benzin)[0] == ABSENT)
check("C13 Anhängerkupplung ist Ausstattung, keine Kupplung (kein Getriebebezug)",
      praesenz("Anhängerkupplung", i_manuell)[1].art == "ausstattung")
check("C14 Komponente ohne Abhängigkeit bleibt PRESENT (z.B. Bremsen)",
      praesenz("Bremsscheiben", i_manuell)[0] == PRESENT)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== D) Kraftstoff vs. Antriebsart ===")
m_mhev_ohne = motor("d-1", "Basis 250", "", "Mild-Hybrid", 250, getriebe='["Automatik"]')
b_d = baureihe([m_mhev_ohne])
r_d = req(motor="2.0 Turbo", kraftstoff="Benzin", leistung_ps=250, getriebe="automatik")
i_d = ident(b_d, m_mhev_ohne, r_d)
zeilen = baue_zeilen(r_d, b_d, m_mhev_ohne, identity=i_d)
kz = [z for z in zeilen if z.kriterium == "Kraftstoff"][0]
check("D1 Kraftstoffzeile zeigt nie eine Antriebsart als Referenz",
      "Hybrid" not in kz.zelle_referenz() and "Hybrid" not in kz.angabe)
check("D2 Antriebsart in eigener Zeile", any(z.kriterium == "Antriebsart" for z in zeilen))
check("D3 identity.fuel ist nie ein Powertrain-Wert", i_d.fuel not in ("Mild-Hybrid", "Plug-in-Hybrid"))
for fuel_user, bez in (("Benzin", "2.0 TFSI"), ("Diesel", "2.0 TDI")):
    m_x = motor("d-x", bez, "QQ1", "Mild-Hybrid", 200)
    i_x = ident(baureihe([m_x]), m_x, req(kraftstoff=fuel_user, leistung_ps=200))
    check(f"D4 {fuel_user}+MHEV ist kein Kraftstoffkonflikt",
          i_x.field_evidence["fuel"]["verification_state"] == "user_confirmed")
check("D5 Kein Widerspruchs-Finding Benzin vs. Mild-Hybrid",
      not [f for f in build_key_findings_kauf(r_d, b_d, m_mhev_ohne, [])
           if f.kategorie == "widerspruch" and "Kraftstoff" in f.titel])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== E) Mehrdeutigkeit (Motor-/Antriebsvarianten) ===")
m_multi = motor("e-1", "2.0 (190 PS) Mild-Hybrid", "AA10, BB20, CC30", "Mild-Hybrid", 190)
i_e = ident(baureihe([m_multi]), m_multi, req(kraftstoff="Benzin", leistung_ps=190))
check("E1 MHEV-Zeile mit mehreren Codes -> Antriebsart mehrdeutig, Wert None",
      i_e.powertrain is None and i_e.field_evidence["powertrain"]["verification_state"] == "ambiguous"
      and set(i_e.field_evidence["powertrain"]["possible_values"]) == {"ICE", "MHEV"})
check("E2 Anzeige-Motorname trägt keinen Mild-Hybrid-Zusatz, solange unsicher",
      "Hybrid" not in (i_e.engine_name or ""))
m_single = motor("e-2", "2.0 MH", "DD40", "Mild-Hybrid", 190)
i_e2 = ident(baureihe([m_single]), m_single, req(kraftstoff="Benzin", leistung_ps=190, motor="DD40"))
check("E3 MHEV nur mit genau einem, vom Nutzer genannten Motorcode plausibel",
      i_e2.powertrain == "MHEV")
m_ice = motor("e-3", "2.0 ICE", "EEE5", "Benzin", 190)
m_mh = motor("e-4", "2.0 MH", "FFF6", "Mild-Hybrid", 190)
i_e3 = ident(baureihe([m_ice, m_mh]), m_ice, req(kraftstoff="Benzin", leistung_ps=190))
check("E4 Schwesterzeile gleicher Leistung mit anderer Antriebsart -> mehrdeutig",
      i_e3.powertrain is None and i_e3.field_evidence["powertrain"]["verification_state"] == "ambiguous")
m_d = motor("e-5", "2.0 D", "GGG7", "Diesel", 190)
i_e4 = ident(baureihe([m_ice, m_d]), m_ice, req(kraftstoff="Benzin", leistung_ps=190))
check("E5 Schwesterzeile mit anderem Kraftstoff erzeugt KEINE Mehrdeutigkeit",
      i_e4.powertrain == "ICE")
i_e5 = ident(baureihe([m_multi]), m_multi, req(kraftstoff="Benzin", powertrain="MHEV", leistung_ps=190))
check("E6 Nutzerangabe Antriebsart gewinnt und bleibt Primärquelle",
      i_e5.powertrain == "MHEV" and i_e5.field_evidence["powertrain"]["primary_source"] == "user")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== F) Motorcode-Aggregation ===")
check("F1 DB-Feld mit mehreren Codes wird zerlegt", motorcodes("AAA1, BBB2 / CCC3") == ["AAA1", "BBB2", "CCC3"])
check("F2 Klammer-Alias ist kein eigener Code", motorcodes("XY12 (ABC)") == ["XY12"])
check("F3 Mehrere Codes ohne Nutzercode -> engine_code None, mögliche Codes gelistet",
      i_e.engine_code is None and i_e.field_evidence["engine_code"]["possible_values"] == ["AA10", "BB20", "CC30"])
check("F4 Titel nennt keinen der möglichen Codes als Identität",
      not any(c in fahrzeug_titel(i_e) for c in ("AA10", "BB20", "CC30")))
check("F5 Bericht formuliert 'mögliche Motorcodes'",
      "mögliche Motorcodes" in feldzeile("Motorcode", "engine_code", None, i_e.field_evidence["engine_code"]))
i_f = ident(baureihe([m_multi]), m_multi, req(kraftstoff="Benzin", leistung_ps=190, motor="BB20"))
check("F6 Nutzercode wählt genau einen Code", i_f.engine_code == "BB20")
i_f2 = ident(baureihe([m_multi]), m_multi, req(kraftstoff="Benzin", leistung_ps=190, motor="ZZ90"))
check("F7 Nutzercode außerhalb der DB-Codes -> Konflikt, nicht still ersetzt",
      i_f2.engine_code == "ZZ90" and i_f2.field_evidence["engine_code"]["verification_state"] == "conflict")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== G) Rückruf-Applicability ===")
m_g = motor("g-1", "1.4 T (125 PS)", "QX14A", "Benzin", 125)
b_g = baureihe([m_g])
i_g = ident(b_g, m_g, req(kraftstoff="Benzin", leistung_ps=125, motor="1.4 QX14A",
                          getriebe="manuell"))
basis_r = {"betroffene_baujahre": "2018", "kba_referenz": None}
cases = {
    "A Motorcode anders": ({"mangel": "Betrifft Fahrzeuge mit Motorcode ZZ16T: Ölleitung kann undicht werden."},
                           RECALL_NOT_APPLICABLE),
    "B Leistung anders": ({"mangel": "Bei Fahrzeugen mit 150 PS kann die Ölleitung undicht werden."},
                          RECALL_NOT_APPLICABLE),
    "C Motor passt": ({"mangel": "Bei Fahrzeugen mit 125 PS kann die Ölleitung undicht werden."},
                      RECALL_VARIANT_POSSIBLE),
    "D ohne Varianteninfo": ({"mangel": "Die Ölleitung kann undicht werden."}, RECALL_SERIES_RELEVANT),
    "E Kraftstoff anders": ({"mangel": "Bei Dieselfahrzeugen kann die Einspritzleitung brechen."},
                            RECALL_NOT_APPLICABLE),
    "E2 Getriebe anders": ({"mangel": "Das Doppelkupplungsgetriebe kann ungewollt auskuppeln."},
                           RECALL_NOT_APPLICABLE),
    "E3 Hubraum anders": ({"mangel": "Beim 1.6 Turbo-Motor kann Öl austreten."}, RECALL_NOT_APPLICABLE),
}
for name, (r_extra, erwartet) in cases.items():
    zustand, grund = rueckruf_scope({**basis_r, **r_extra}, i_g)
    check(f"G-{name} -> {erwartet}", zustand == erwartet)
i_g_unb = ident(None, None, req())
check("G-U Scope vorhanden, Wert unbekannt -> UNKNOWN (nicht ausgeschlossen)",
      rueckruf_scope({**basis_r, "mangel": "Bei Fahrzeugen mit 150 PS kann ..."}, i_g_unb)[0] == RECALL_UNKNOWN)
app_na = rueckruf_applicability({**basis_r, "mangel": "Bei Fahrzeugen mit 150 PS: Ölleitung."},
                                True, "", {"kraftstoff": "Benzin"}, identity=i_g)
check("G-NA bekannter Ausschluss -> 'incompatible' (wird aus der Risikomenge entfernt)",
      app_na[0] == "incompatible")
b_g2 = baureihe([m_g], rueckrufe=[
    {"id": 1, "datum": "2018-06", "betroffene_baujahre": "2018", "kba_referenz": None,
     "mangel": "Bei Fahrzeugen mit 150 PS kann die Turboölleitung undicht werden."},
    {"id": 2, "datum": "2018-06", "betroffene_baujahre": "2018", "kba_referenz": None,
     "mangel": "Die Lenkspindel kann brechen."}])
r_g = req(kraftstoff="Benzin", leistung_ps=125, motor="1.4 QX14A")
ins_g = build_insights(b_g2, m_g, [], r_g, check_typ="kauf", identity=ident(b_g2, m_g, r_g))
rr = [i for i in ins_g if i.kategorie == "rueckruf"]
check("G-P1 Pipeline: ausgeschlossener Rückruf erscheint nicht", len(rr) == 1 and "Lenk" in rr[0].beschreibung)
check("G-P2 Serien-Rückruf ist SERIES_RELEVANT und FIN-first, nie 'betrifft'",
      rr and rr[0].recall_state == RECALL_SERIES_RELEVANT and "FIN" in (rr[0].einfluss or "")
      and "betrifft dein" not in (rr[0].einfluss or "").lower())


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== H) Web-Identität: Phasen, Alignment, Konsens ===")
ziel = {"marke": "Testmarke", "modell": "Alpha", "baujahr": 2019, "motor": "2.0 Q", "leistung_ps": 180}
FX_GUT = {
    "identitaet": [
        t("https://www.adac.de/testmarke-alpha", "Testmarke Alpha T2 (seit 2016): Daten",
          "Die Testmarke Alpha T2 (seit 2016) ist die 2. Generation. 2.0 Q mit 180 PS, Benziner, "
          "Hinterradantrieb, 6-Gang-Schaltgetriebe."),
        t("https://www.auto-motor-und-sport.de/testmarke-alpha", "Testmarke Alpha T2 im Test",
          "Die zweite Generation der Testmarke Alpha (T2, seit 2016): 2.0 Q, 180 PS, Benziner, "
          "Heckantrieb, 6-Gang-Schaltgetriebe."),
        t("https://www.autobild.de/testmarke-alpha-t1", "Testmarke Alpha T1 Gebrauchtwagen",
          "Die Testmarke Alpha T1 wurde von 2008 bis 2015 gebaut."),
    ],
    "rueckruf": [t("https://www.kba.de/rueckruf-alpha", "Rückruf Testmarke Alpha",
                   "Rückruf für die Testmarke Alpha: Fahrzeuge, gebaut zwischen 2017 und 2020, "
                   "die Kraftstoffpumpe kann ausfallen.")],
    "schwachstelle": [t("https://www.adac.de/alpha-probleme", "Testmarke Alpha T2 Schwachstellen",
                        "Bei der Testmarke Alpha ist der Turbolader ein bekanntes Problem.")],
    "wartung": [],
}
wi, abgelehnt = werte_identitaet_aus(FX_GUT["identitaet"], ziel)
check("H1 Generation aus Quellentexten mit passendem Baujahresfenster", wi.generation == "T2")
check("H2 Generation einer anderen Bauzeit (T1 2008-2015) verworfen",
      any(a.get("grund") == "baujahr_ausserhalb" for a in wi.abgelehnte_claims))
check("H3 Leistung nur bestätigt (Nutzerwert), nie gewählt", wi.leistung_ps == 180)
check("H4 Antrieb/Getriebe/Kraftstoff per Konsens", wi.antrieb == "Heck"
      and wi.getriebe == "manuell" and wi.kraftstoff == "benzin")
check("H5 Motorbezeichnung nur als BESTÄTIGUNG der Nutzerangabe", wi.motor == "2.0 Q")
wi_konf, _ = werte_identitaet_aus([
    t("https://www.adac.de/a", "Testmarke Alpha T2", "Testmarke Alpha T2 (seit 2016)."),
    t("https://www.autobild.de/b", "Testmarke Alpha T3", "Testmarke Alpha T3 (seit 2017)."),
], ziel)
check("H6 widersprechende gleich starke Generationsquellen -> UNKNOWN", wi_konf.generation is None)
wi_schwach, _ = werte_identitaet_aus([
    t("https://www.motor-talk.de/x", "Testmarke Alpha T9", "Testmarke Alpha T9 (seit 2018)."),
    t("https://www.reddit.com/y", "Testmarke Alpha T9", "Testmarke Alpha T9 (seit 2018)."),
], ziel)
check("H7 Nur TIER-3-Quellen -> keine Identitätsfelder", wi_schwach.generation is None)
check("H8 Fremdes Fahrzeug im Titel wird verworfen (Entity-Alignment)",
      ausgerichtet(t("https://www.autobild.de/z", "Ford Mustang als Gebrauchtwagen",
                     "Auch die Testmarke Alpha ist ein Sportwagen."), "Testmarke", "Alpha")[0] is False)
prov = FixtureTechnicalResearchProvider({"identitaet": [], "rueckruf": FX_GUT["rueckruf"],
                                         "schwachstelle": FX_GUT["schwachstelle"]})
res_leer = asyncio.run(recherchiere_technisch(req(modell="Alpha", baujahr=2019), None,
                                              {"belastbar": False}, None, None, provider=prov))
check("H9 Identität nicht belegt -> KEINE Rückruf-/Technikphase", prov.phasen_aufrufe == ["identitaet"]
      and res_leer.fakten == [])
prov2 = FixtureTechnicalResearchProvider(FX_GUT)
res_gut = asyncio.run(recherchiere_technisch(req(modell="Alpha", baujahr=2019, motor="2.0 Q",
                                                 leistung_ps=180), None, {"belastbar": False},
                                             None, None, provider=prov2))
check("H10 Reihenfolge Identität -> Rückruf -> Technik", res_gut.phasen == ["identitaet", "rueckruf", "technik"])
web_rr = [f for f in res_gut.fakten if f.kategorie == "rueckruf"]
check("H11 Web-Rückruf mit Produktionsfenster über dem Baujahr -> vehicle_possible",
      web_rr and web_rr[0].applicability == "vehicle_possible")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== I) Web-Risiko-Geltungsbereich ===")
fx_scope = [t("https://www.adac.de/s1", "Testmarke Alpha Schwachstellen",
              "Bei der Testmarke Alpha waren frühe Getriebe anfällig für Probleme."),
            t("https://www.autobild.de/s2", "Testmarke Alpha Mängel",
              "Bei der Testmarke Alpha ist der Turbolader bis 2016 ein bekanntes Problem."),
            t("https://www.adac.de/s3", "Testmarke Alpha Mängel 2",
              "Die Wasserpumpe der Testmarke Alpha ist von 2017 bis 2021 häufig defekt.")]
fk = _extrahiere_fakten(fx_scope, "schwachstelle", marke="Testmarke", modell="Alpha", baujahr=2019)
nach = {f.bauteil.lower(): f for f in fk}
check("I1 Vage Eingrenzung ('frühe Getriebe') -> unresolved, niedrig",
      any(k.startswith("getriebe") and f.geltung_fuer_fahrzeug == "unresolved" and f.confidence == "niedrig"
          for k, f in nach.items()))
check("I2 Jahresbereich schließt Baujahr aus -> Fakt verworfen",
      not any(k.startswith("turbolader") for k in nach))
check("I3 Jahresbereich deckt Baujahr -> covered",
      any(k.startswith("wasserpumpe") and f.geltung_fuer_fahrzeug == "covered" for k, f in nach.items()))
fk_rr = _extrahiere_fakten([t("https://www.kba.de/r", "Rückruf Testmarke Alpha",
                              "Rückruf: Fahrzeuge, gebaut von 2012 bis 2014, die Bremse kann ausfallen.")],
                           "rueckruf", marke="Testmarke", modell="Alpha", baujahr=2019)
check("I4 Web-Rückruf mit Fenster außerhalb des Baujahrs -> verworfen", fk_rr == [])
fk_forum = _extrahiere_fakten([t("https://www.motor-talk.de/r", "Rückruf Testmarke Alpha?",
                                 "Angeblich gibt es einen Rückruf wegen der Bremse bei der Testmarke Alpha.")],
                              "rueckruf", marke="Testmarke", modell="Alpha", baujahr=2019)
check("I5 Forum allein erzeugt keinen Rückruf-Fakt", fk_forum == [])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== J) Empfehlungs-Identitäts-Floor (Policy + echter Pfad) ===")
check("J1 Nur-Nutzer-Identität -> INSUFFICIENT, keine Freigabe",
      entscheide("kaufen_nach_besichtigung", ident(None, None, req())).empfehlung == "unbekannt")
i_teil = ident(None, None, req(motor="2.0 Q", leistung_ps=180))
i_teil.apply_web_evidence(TechnischeRecherche(identitaet=WebVehicleIdentity(
    belegt=True, marke="Testmarke", modell="Alpha", belegende_domains=2)))
e_teil = entscheide("kaufen_nach_besichtigung", i_teil)
check("J2 Marke/Modell per Web, Generation/Motor offen -> LIMITED_ANALYSIS",
      e_teil.state == STATE_LIMITED and e_teil.identitaet.stufe == PARTIAL
      and e_teil.empfehlung == "unbekannt" and e_teil.anzeige == ANZEIGE_LIMITED)
e_vorsicht = entscheide("nur_mit_werkstattpruefung", i_teil)
check("J3 Vorsichtigere Floor-Stufe bleibt, Zustand bleibt eingeschränkt",
      e_vorsicht.empfehlung == "nur_mit_werkstattpruefung" and e_vorsicht.state == STATE_LIMITED)
check("J4 DB-Variante vollständig -> NORMAL",
      entscheide("kaufen_nach_besichtigung", i_a).state == STATE_NORMAL
      and entscheide("kaufen_nach_besichtigung", i_a).identitaet.stufe == VERIFIED)


async def _j_pfad(fixtures, **kw):
    import app.kaufcheck as kc
    orig = (kc.recherchiere_technisch, kc.tavily_search_with_fallback, kc.call_gemini_json)
    prov = FixtureTechnicalResearchProvider(fixtures)

    async def rech(r, br, info, brg, mm, provider=None):
        return await recherchiere_technisch(r, br, info, brg, mm, provider=prov)

    async def leer(*a, **k):
        return []

    async def gem(system, user_msg):
        return {"risiko_evidence_ids": []}
    kc.recherchiere_technisch, kc.tavily_search_with_fallback, kc.call_gemini_json = rech, leer, gem
    try:
        return await kc.run_kaufcheck(req(modell="Alpha", baujahr=2019, **kw))
    finally:
        kc.recherchiere_technisch, kc.tavily_search_with_fallback, kc.call_gemini_json = orig


res_lim = asyncio.run(_j_pfad({"identitaet": [
    t("https://www.adac.de/a", "Testmarke Alpha im Test", "Die Testmarke Alpha ist ein Roadster."),
    t("https://www.autobild.de/b", "Testmarke Alpha gebraucht", "Testmarke Alpha: Roadster.")]},
    motor="2.0 Q", leistung_ps=180))
check("J5 ECHTER run_kaufcheck-Pfad: Web belegt nur Marke/Modell -> 'unbekannt' + LIMITED",
      res_lim["empfehlung"] == "unbekannt" and res_lim["recommendation_state"] == STATE_LIMITED)
check("J6 ... Bericht zeigt 'Analyse eingeschränkt' statt 'KAUFEN NACH BESICHTIGUNG'",
      ANZEIGE_LIMITED.upper() in res_lim["bericht"] and "KAUFEN NACH BESICHTIGUNG" not in res_lim["bericht"])
check("J7 ... Überschrift und Empfehlung aus derselben Entscheidung (kein 'Fahrzeug erkannt')",
      "## Fahrzeug erkannt" not in res_lim["bericht"])
res_ok = asyncio.run(_j_pfad(FX_GUT, motor="2.0 Q", leistung_ps=180, kraftstoff="Benzin",
                             getriebe="manuell"))
check("J8 ECHTER Pfad: Web belegt Generation + Motorisierung -> normale Empfehlung erlaubt",
      res_ok["recommendation_state"] == STATE_NORMAL and res_ok["empfehlung"] != "unbekannt")
check("J9 ... Web-Generation erreicht die kanonische Identität",
      res_ok["vehicle_identity"].get("generation") == "T2")
check("J10 ... Bericht: 'über Webquellen plausibilisiert', nicht ENFAL-Referenz",
      "über Webquellen plausibilisiert" in res_ok["bericht"]
      and "aus ENFAL-Fahrzeugdaten" not in res_ok["bericht"]
      and "ENFAL-Referenzvariante zugeordnet" not in res_ok["bericht"])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== K) Anzeige, Dedup, Kosten ===")
check("K1 Token-Dedup über Segmente", ohne_wiederholung("Alpha M4", "M4", "T1", "M4") == "Alpha M4 T1")
check("K2 Klammergruppe bleibt vollständig oder entfällt ganz",
      ohne_wiederholung("2.0 TX", "(40 TX)", "190 PS") == "2.0 TX (40 TX) 190 PS"
      and ohne_wiederholung("2.0 TX", "(TX)") == "2.0 TX")
check("K3 Leistung nicht doppelt ('2.0 T, 190 PS' + '190 PS')",
      liste_ohne_wiederholung("2.0 T, 190 PS, Front", "190 PS") == "2.0 T, 190 PS, Front")
check("K4 Marke ohne DB-Treffer: 'testmarke' -> 'Testmarke', 'ABC' bleibt", anzeige_marke("testmarke") == "Testmarke"
      and anzeige_marke("ABC") == "ABC")
satz = f"Für eine gezielte Analyse bitte {_fehlende_angabe_none('Testmarke', 'Alpha', 2019)} nachtragen."
check("K5 Fehlende-Angabe-Hinweis ist ein grammatischer Satz (Nominalphrase)",
      "konnten nicht" not in satz and "den Motorcode" in satz)
check("K6 Kosten 'ca. 300 €'", kosten_anzeige("300") == "ca. 300 €")
check("K7 Kosten-Spanne 'ca. 300–500 €'", kosten_anzeige("300-500 EUR") == "ca. 300–500 €")
check("K8 Fremdwährung -> nicht verifiziert", kosten_anzeige("$400") == "Kostenangabe nicht verifiziert")
check("K9 ohne Zahl -> kein Kostenhinweis", kosten_anzeige("Herstellergarantie") is None)
check("K10 Empfehlungsgründe nutzen denselben deduplizierten Titel",
      "Alpha Alpha" not in " ".join(baue_empfehlung_gruende(r_a, b_a, m_a, [], [], "x", False, None,
                                                            identity=i_a)))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== L) Datenbasis (eine Quelle für Liste, Chip und Vertrauen) ===")
db_leer = datenbasis_objekt(None, [], [], identity=ident(None, None, req()))
check("L1 Weder DB noch Web -> keine DB/Web-Kennzeichnung, Chip 'inserat'",
      db_leer["quelle"] == "inserat" and db_leer["labels"] == ["Inserat-/Nutzereingaben"])
check("L2 DB -> 'datenbank'", datenbasis_objekt(b_a, [], [], identity=i_a)["quelle"] == "datenbank")
check("L3 Web-Identität ohne DB -> 'web', nie 'gemischt'",
      datenbasis_objekt(None, [], [], identity=i_web)["quelle"] == "web")
check("L4 Echter Pfad: 'quelle' passt zu 'datenbasis'",
      res_ok["quelle"] == "web" and "ENFAL-Fahrzeugdatenbank" not in res_ok["datenbasis"]
      and "Webrecherche" in res_ok["datenbasis"])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== M) Prüfklassen-Mapping (Probefahrt) ===")
inf = _komponente("Infotainmentsystem (XYZ-Link)")
check("M1 Infotainment -> eigene Prüfklasse", inf and inf["schluessel"] == "infotainment")
sym = _probefahrt_symptom(inf, "Software-Aussetzer und Ruckeln der Anzeige")
check("M2 Infotainment mit 'Aussetzer' im Text -> Infotainment-Fahrtext, nie Beschleunigung/Ruckeln",
      sym and "Ruckeln" not in sym and "Beschleunig" not in sym and "GPS" in sym)
check("M3 Allgemeine Elektrik -> keine Probefahrtaktion aus Textsymptom",
      _probefahrt_symptom(_komponente("Kabelbaum"), "sporadische Aussetzer") is None)
check("M4 Antriebsklasse behält das Text-Tor",
      _probefahrt_symptom(None, "Ruckeln beim Beschleunigen") is not None)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== N) Resume-Audit: Achsen, Floor, Anzeige-Konsistenz ===")
from app.car_lookup import _motor_kraftstoff_kompatibel
from app.empfehlungs_floor import darf_floor_tragen
from app.kraftstoff_powertrain import fahrzeug_achsen, scope_passt
from app.motor_applicability import schwachstelle_applicability
from app.recall_filter import rueckruf_applicability

m_phev = motor("n-1", "Alpha e", "QP20 + E-Motor", "Plug-in-Hybrid", 290, getriebe='["Automatik"]')
m_ice = motor("n-2", "Alpha i", "QP20", "Benzin", 250, getriebe='["Automatik"]')
m_mh_t = motor("n-3", "2.0 TFSI (40)", "QA1, QB2", "Mild-Hybrid", 190)
r_hv = {"betroffene_baujahre": "2020 (Plug-in-Hybrid)", "kba_referenz": None,
        "mangel": "Zellen des Hochvoltspeichers können einen Kurzschluss auslösen."}
app_m = lambda m, user: {**m, "kraftstoff": user or m["kraftstoff"], "_kraftstoff_db": m["kraftstoff"],
                         "_kraftstoff_nutzer": user}
check("N1 HV-Rückruf, Benzin-PHEV, Nutzer sagt 'Benzin' -> bleibt (kein Achsen-Mix)",
      rueckruf_applicability(r_hv, True, "", app_m(m_phev, "Benzin"))[0] != "incompatible")
check("N2 HV-Rückruf, Verbrenner (DB 'Benzin') -> entfernt",
      rueckruf_applicability(r_hv, True, "", app_m(m_ice, "Benzin"))[0] == "incompatible")
check("N3 HV-Rückruf, nur Nutzerangabe 'Benzin' ohne Motor -> unklar, nicht ausgeschlossen",
      rueckruf_applicability(r_hv, True, "", {"kraftstoff": "Benzin"})[0] == "unclear")
i_n4 = ident(baureihe([m_phev]), m_phev, req(kraftstoff="Benzin", leistung_ps=290))
check("N4 HV-Rückruf mit kanonischer Identität (Benzin + PHEV) -> bleibt",
      rueckruf_applicability(r_hv, True, "", app_m(m_phev, "Benzin"), identity=i_n4)[0] != "incompatible")
check("N5 Schwachstelle '(Benzinmotoren)' an Benzin-Mild-Hybrid -> nicht ausgeschlossen",
      schwachstelle_applicability({"bauteil": "Steuerkette (Benzinmotoren)"}, m_mh_t,
                                  {"motoren": [m_mh_t]})[0] != "incompatible")
check("N6 Schwachstelle '(Dieselmotoren)' an Benzin-Mild-Hybrid -> ausgeschlossen",
      schwachstelle_applicability({"bauteil": "AGR-Ventil (Dieselmotoren)"}, m_mh_t,
                                  {"motoren": [m_mh_t]})[0] == "incompatible")
check("N7 Motorzuordnung: Nutzer 'Benzin' schließt die PHEV-Zeile nicht aus",
      _motor_kraftstoff_kompatibel(m_phev, "benzin"))
check("N8 Motorzuordnung: Nutzer 'Diesel' schließt die Benzinzeile aus",
      not _motor_kraftstoff_kompatibel(m_ice, "diesel"))
check("N9 Mild-Hybrid-Scope bei mehrdeutiger Antriebsart -> unklar",
      scope_passt("mild", "benzin", frozenset({"ICE", "MHEV"})) is None)
check("N10 Nutzertext 'Benzin' sagt nichts über die Elektrifizierung",
      fahrzeug_achsen(nutzer_text="Benzin") == ("benzin", None))
ins_bedingt = Insight(id="x", kategorie="schwachstelle", titel="Falls EDC vorhanden: EDC",
                      beschreibung="x", confidence="hoch", trust="verified", presence_state="unknown",
                      schweregrad="hoch")
check("N11 Risiko mit unbekannter Präsenz darf die Empfehlung nicht verschärfen",
      not darf_floor_tragen(ins_bedingt)
      and darf_floor_tragen(ins_bedingt.model_copy(update={"presence_state": None})))
check("N12 'Spannungswandler' ist keine Automatik-Komponente",
      praesenz("Spannungswandler", i_manuell)[1] is None
      and praesenz("Drehmomentwandler", i_manuell)[0] == ABSENT)
check("N13 Motor-Zeile: Variantenname nicht als 'präzisiert' der Nutzerangabe",
      "ENFAL-Referenzvariante zur Angabe im Inserat („2.0 Q“)" in feldzeile(
          "Motor", "engine_name", "Alpha i",
          ident(baureihe([m_ice]), m_ice, req(motor="2.0 Q", leistung_ps=250)).field_evidence["engine_name"]))
zeilen_web = baue_zeilen(req(kraftstoff="Benzin", getriebe="manuell"), None, None, identity=i_web)
kz_web = [z for z in zeilen_web if z.kriterium == "Kraftstoff"]
check("N14 Vergleichstabelle zeigt Web-Widerspruch zum Kraftstoff statt 'keine Vergleichsdaten'",
      kz_web and "Diesel" in (kz_web[0].zelle_referenz() or "") and "weicht" in kz_web[0].einordnung)
fk_t1 = _extrahiere_fakten([t("https://www.kba.de/r", "Rückruf Testmarke Alpha",
                              "Rückruf: Fahrzeuge, gebaut von 2017 bis 2021, die Bremse kann ausfallen.")],
                           "rueckruf", marke="Testmarke", modell="Alpha", baujahr=2019)
check("N15 Einzelne amtliche Quelle (TIER 1) -> mindestens 'mittel'",
      fk_t1 and fk_t1[0].confidence == "mittel")
from app.key_findings import _strukturiert_vs_inserat
check("N16 Auswahl 'Benzin' + Text 'Plug-in-Hybrid, Benziner' ist kein Kraftstoff-Widerspruch",
      not _strukturiert_vs_inserat(req(kraftstoff="Benzin", beschreibung="Plug-in-Hybrid, Benziner.")))
check("N17 Auswahl 'Benzin' + Text 'Diesel' bleibt ein Widerspruch",
      bool(_strukturiert_vs_inserat(req(kraftstoff="Benzin", beschreibung="Sparsamer Diesel."))))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== O) Web-Identität aus realistisch verrauschten Quellen ===")
from app.technical_research import _fremde_generation, _tier_identitaet
from app.web_search import score_domain as _score
ziel_o = {"marke": "Testmarke", "modell": "Alpha", "baujahr": 2019, "motor": "2.0 Q", "leistung_ps": 184}
FX_RAUSCH = [
    # Stufe 2: Trim-/Leistungskürzel direkt hinter dem Modell ("G 184") -> kein Generationscode.
    t("https://www.auto-motor-und-sport.de/test/alpha-g-184", "Test Testmarke Alpha G 184 (Technische Daten)",
      "Benzin Direkteinspritzung. Antriebsart Hinterradantrieb. Getriebe 6-Gang Schaltgetriebe."),
    # Stufe 2: Karosseriekürzel ohne Generationskontext/Fenster ("RF (2017)").
    t("https://www.autobild.de/artikel/alpha-rf", "Testmarke Alpha RF (2017) im Test",
      "Als Fastback kommt die Testmarke Alpha mit Hinterradantrieb."),
    # Stufe 2 (Identitätsliste): Vergleichsseite mit zwei Generationen im Kontext.
    t("https://www.autozeitung.de/alpha-vergleich", "Testmarke Alpha T1/Testmarke Alpha T4: Vergleich",
      "Aktuell gebaut in der vierten Generation (Testmarke Alpha T4). Von der ersten Generation "
      "Testmarke Alpha (T1) bis heute."),
    # Stufe 2 (Identitätsliste): Seite über GENAU diese Motorisierung, römische Nummer + Code.
    t("https://www.auto-data.net/de/alpha-iv-t4-2.0-184hp", "Testmarke Alpha IV (T4, Facelift 2018) 2.0 Q (184 PS)",
      "Leistung 184 PS, Benzin."),
    # Stufe 3: Fenster schließt T1 aus; Spezifikationsseiten nennen die Leistung.
    t("https://www.alpha-club.example/t1", "Die Testmarke Alpha T1 Kaufberatung",
      "Ich kenne eher die Angabe von 1989-1998 für den T1."),
    t("https://www.spec-a.example/alpha", "Testmarke Alpha 2.0 Q (184 PS) technische Daten", "Leistung 184 PS."),
    t("https://www.spec-b.example/alpha", "Testmarke Alpha 184 PS Datenblatt", "135 kW (184 PS), Benzin."),
    t("https://www.hersteller-testmarke.example/alpha", "Testmarke Alpha", "Die Testmarke Alpha."),
]
wi_o, abg_o = werte_identitaet_aus(FX_RAUSCH, ziel_o)
check("O1 Trim-/Karosseriekürzel ohne Generationskontext sind keine Generation",
      any(a.get("grund") == "ohne_generationskontext" and a.get("wert") in ("G", "RF") for a in abg_o))
check("O2 Generation per Kontext + variantengenauer Quelle trotz Vergleichsseite", wi_o.generation == "T4")
check("O3 'Modell IV (T4 …)': römische Zahl = Nummer, Klammercode = Generation",
      not any(a.get("wert") == "IV" for a in abg_o) and wi_o.generation == "T4")
check("O4 Stufe-3-Spezifikationsseiten bestätigen den Nutzerwert (mit einer Stufe-2-Quelle)",
      wi_o.leistung_ps == 184 and wi_o.feldwerte["horsepower"]["domains"] >= 3)
wi_nur3, _ = werte_identitaet_aus([FX_RAUSCH[0], FX_RAUSCH[1]] + [
    t(f"https://www.spec-{i}.example/a", "Testmarke Alpha T9 Daten", "Testmarke Alpha T9 (seit 2018).")
    for i in range(4)], ziel_o)
check("O5 Nur Stufe-3-Quellen belegen keine Generation (Mehrheit schwacher Seiten reicht nicht)",
      wi_nur3.generation is None)
check("O6 Einzelbuchstabe im Titel macht eine Seite nicht zur 'anderen Generation'",
      not _fremde_generation(FX_RAUSCH[0], "Alpha", "T4") and _fremde_generation(FX_RAUSCH[4], "Alpha", "T4"))
class _ZielProvider:
    def __init__(self):
        self.aufruf = None

    async def recherchiere(self, **kw):
        self.aufruf = kw
        return TechnischeRecherche(ausgeloest_durch=kw["ausgeloest_durch"])


zp = _ZielProvider()
asyncio.run(recherchiere_technisch(req(modell="Alpha T1", motor="9.9 Unbekannt"), baureihe([m_a]),
                                   {"belastbar": True}, baureihe([m_a]), None, provider=zp))
check("O8 Belastbare Baureihe, Motor fehlt: Web recherchiert das kanonische Modell + DB-Generation",
      zp.aufruf and zp.aufruf["modell"] == "Alpha" and zp.aufruf["ziel"].get("generation") == "T1")
check("O7 Identitäts-Stufenliste gilt nur für Phase 1 (geteilte Domainbewertung unverändert)",
      _tier_identitaet("https://www.autozeitung.de/x") == 2 and _score("https://www.autozeitung.de/x") == 0)


print("\n" + "=" * 60)
print(f"{_ANZAHL['n']} Prüfungen")
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE FINAL-STABILIZATION-TESTS GRUEN")
