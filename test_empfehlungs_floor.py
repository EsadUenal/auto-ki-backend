"""
Test: deterministischer Empfehlungs-Floor (app/empfehlungs_floor) — kein LLM,
keine Netzwerkaufrufe, kein Tavily.

Deckt die geforderten Faelle A-L ab:
  A  LLM mild + harter Werkstatt-Befund      -> angehoben
  B  LLM bereits nur_mit_werkstattpruefung   -> unveraendert
  C  LLM vorsichtiger als der Floor          -> NIE gelockert
  D  nur kleine/moderate Findings            -> keine Eskalation
  E  bloss series_only-Rueckruf              -> keine Eskalation
  F  variant_match-Rueckruf                  -> Floor greift
  G  P2-5 `darueber`-Wartungspunkt           -> loest NICHT aus
  H  No-Market                               -> Floor marktunabhaengig
  I  Fantasiefahrzeug / partial              -> keine erfundene Eskalation
  J  BMW-Bake-off-Fall (Realdaten)
  K  Insignia-Bake-off-Fall (Realdaten)
  L  Audi-Wartungs-Bake-off-Fall (Realdaten)

Ausfuehren:  python test_empfehlungs_floor.py
"""
import os
import sys
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_floor_"), "test.db")
sys.path.insert(0, ".")

from app.empfehlungs_floor import (  # noqa: E402
    wende_floor_an, ermittle_floor, ist_vorsichtiger,
    KAUFEN, KAUFEN_NACH_BESICHTIGUNG, NUR_MIT_WERKSTATTPRUEFUNG,
    PREIS_NACHVERHANDELN, HOHES_RISIKO, FINGER_WEG, UNBEKANNT,
    GRUND_MOTORPROBLEM, GRUND_SCHWACHSTELLE_HOCH, GRUND_RUECKRUF_VARIANTENTREFFER,
)
from app.models import Insight  # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


# ── Insight-Fabriken (minimal, nur die vom Floor gelesenen Felder) ───────────

def schwachstelle(iid, schweregrad, confidence="hoch"):
    return Insight(id=iid, kategorie="schwachstelle", titel="Bauteil — bekannte Schwachstelle",
                   beschreibung="", confidence=confidence, schweregrad=schweregrad)


def rueckruf(iid, applicability, confidence="mittel"):
    return Insight(id=iid, kategorie="rueckruf", titel="KBA-Rückruf: Beispiel",
                   beschreibung="", confidence=confidence, applicability=applicability)


def motorproblem(iid, confidence="hoch"):
    return Insight(id=iid, kategorie="motorproblem", titel="Steuerkette (2.0 TFSI)",
                   beschreibung="", confidence=confidence)


def wartung(iid):
    return Insight(id=iid, kategorie="wartung", titel="Zahnriemen — kritischer Wartungspunkt",
                   beschreibung="Intervall 120.000 km", confidence="hoch")


def marktvergleich(iid="marktvergleich-9"):
    return Insight(id=iid, kategorie="marktvergleich", titel="Marktvergleich",
                   beschreibung="", confidence="mittel")


# ── Rangfolge ────────────────────────────────────────────────────────────────
print("-- Rangfolge --")
check("Rang: werkstattpruefung vorsichtiger als nach_besichtigung",
      ist_vorsichtiger(NUR_MIT_WERKSTATTPRUEFUNG, KAUFEN_NACH_BESICHTIGUNG))
check("Rang: werkstattpruefung vorsichtiger als kaufen",
      ist_vorsichtiger(NUR_MIT_WERKSTATTPRUEFUNG, KAUFEN))
check("Rang: hohes_risiko vorsichtiger als werkstattpruefung",
      ist_vorsichtiger(HOHES_RISIKO, NUR_MIT_WERKSTATTPRUEFUNG))
check("Rang: finger_weg vorsichtiger als hohes_risiko",
      ist_vorsichtiger(FINGER_WEG, HOHES_RISIKO))
check("Rang: werkstattpruefung NICHT vorsichtiger als finger_weg",
      not ist_vorsichtiger(NUR_MIT_WERKSTATTPRUEFUNG, FINGER_WEG))
check("Rang: preis_nachverhandeln liegt technisch auf Hoehe nach_besichtigung",
      not ist_vorsichtiger(PREIS_NACHVERHANDELN, KAUFEN_NACH_BESICHTIGUNG)
      and not ist_vorsichtiger(KAUFEN_NACH_BESICHTIGUNG, PREIS_NACHVERHANDELN))
check("Rang: unbekannt ist mit nichts vergleichbar",
      not ist_vorsichtiger(UNBEKANNT, KAUFEN)
      and not ist_vorsichtiger(NUR_MIT_WERKSTATTPRUEFUNG, UNBEKANNT))

# ── A) LLM mild + harter Werkstatt-Befund -> angehoben ──────────────────────
print("\n-- A) Anhebung bei hartem Befund --")
ins_a = [schwachstelle("schwachstelle-1", "hoch")]
emp, bef = wende_floor_an(KAUFEN_NACH_BESICHTIGUNG, ins_a)
check("A: nach_besichtigung + hohe Schwachstelle -> nur_mit_werkstattpruefung",
      emp == NUR_MIT_WERKSTATTPRUEFUNG)
check("A: Befund gesetzt (Floor hat gegriffen)", bef is not None)
check("A: Befund nennt den Grund", bef is not None and GRUND_SCHWACHSTELLE_HOCH in bef.gruende)
check("A: Befund belegt ueber existierende Insight-ID",
      bef is not None and bef.evidence_ids == ["schwachstelle-1"])

emp, bef = wende_floor_an(KAUFEN, [motorproblem("motorproblem-8")])
check("A2: kaufen + Motorproblem -> nur_mit_werkstattpruefung",
      emp == NUR_MIT_WERKSTATTPRUEFUNG and bef is not None
      and GRUND_MOTORPROBLEM in bef.gruende)

emp, bef = wende_floor_an(PREIS_NACHVERHANDELN, [motorproblem("motorproblem-3")])
check("A3: preis_nachverhandeln + Motorproblem -> angehoben (Preis bleibt im eigenen Feld)",
      emp == NUR_MIT_WERKSTATTPRUEFUNG and bef is not None)

# ── B) LLM bereits auf der Floor-Stufe -> unveraendert ──────────────────────
print("\n-- B) Bereits auf Floor-Stufe --")
emp, bef = wende_floor_an(NUR_MIT_WERKSTATTPRUEFUNG, [schwachstelle("schwachstelle-1", "hoch")])
check("B: bereits nur_mit_werkstattpruefung -> unveraendert", emp == NUR_MIT_WERKSTATTPRUEFUNG)
check("B: kein Befund (nichts angehoben)", bef is None)

# ── C) LLM vorsichtiger als Floor -> NIEMALS lockern ────────────────────────
print("\n-- C) Nie lockern --")
for vorsichtiger in (HOHES_RISIKO, FINGER_WEG):
    emp, bef = wende_floor_an(vorsichtiger, [motorproblem("motorproblem-1"),
                                             schwachstelle("schwachstelle-2", "kritisch"),
                                             rueckruf("rueckruf-3", "variant_match")])
    check(f"C: {vorsichtiger} bleibt trotz Floor unveraendert", emp == vorsichtiger and bef is None)

emp, bef = wende_floor_an(UNBEKANNT, [motorproblem("motorproblem-1")])
check("C: unbekannt wird NICHT zu einer erfundenen Empfehlung",
      emp == UNBEKANNT and bef is None)
emp, bef = wende_floor_an("voellig_unerwarteter_wert", [motorproblem("motorproblem-1")])
check("C: unbekannter Enum-Wert bleibt unangetastet (kein versehentliches Senken)",
      emp == "voellig_unerwarteter_wert" and bef is None)

# ── D) Nur kleine/moderate Findings -> KEINE Eskalation ─────────────────────
print("\n-- D) Keine Ueber-Eskalation --")
ins_d = [schwachstelle("schwachstelle-1", "gering"),
         schwachstelle("schwachstelle-2", "mittel"),
         schwachstelle("schwachstelle-3", "gering"),
         schwachstelle("schwachstelle-4", "moderat"),
         marktvergleich()]
check("D: nur gering/mittel/moderat -> kein Floor", ermittle_floor(ins_d) is None)
emp, bef = wende_floor_an(KAUFEN_NACH_BESICHTIGUNG, ins_d)
check("D: Empfehlung bleibt kaufen_nach_besichtigung", emp == KAUFEN_NACH_BESICHTIGUNG and bef is None)
emp, bef = wende_floor_an(KAUFEN, ins_d)
check("D2: 'kaufen' wird nicht grundlos angehoben", emp == KAUFEN and bef is None)
check("D3: leere Insights -> kein Floor", ermittle_floor([]) is None and ermittle_floor(None) is None)

# ── E) Bloss series_only / unclear -> KEINE Eskalation ──────────────────────
print("\n-- E) Schwache Rueckruf-Stufen eskalieren nicht --")
check("E: series_only allein -> kein Floor",
      ermittle_floor([rueckruf("rueckruf-1", "series_only")]) is None)
check("E2: zwei series_only -> weiterhin kein Floor",
      ermittle_floor([rueckruf("rueckruf-1", "series_only"),
                      rueckruf("rueckruf-2", "series_only")]) is None)
check("E3: unclear -> kein Floor",
      ermittle_floor([rueckruf("rueckruf-1", "unclear")]) is None)
check("E4: incompatible -> kein Floor",
      ermittle_floor([rueckruf("rueckruf-1", "incompatible")]) is None)
emp, bef = wende_floor_an(KAUFEN_NACH_BESICHTIGUNG,
                          [rueckruf("rueckruf-1", "series_only"),
                           schwachstelle("schwachstelle-2", "mittel")])
check("E5: series_only + mittlere Schwachstelle -> keine Anhebung",
      emp == KAUFEN_NACH_BESICHTIGUNG and bef is None)

# ── F) variant_match -> Floor greift ───────────────────────────────────────
print("\n-- F) Sicherheitsrelevanter Variantentreffer --")
bef_f = ermittle_floor([rueckruf("rueckruf-7", "variant_match", confidence="hoch")])
check("F: variant_match -> Floor", bef_f is not None
      and bef_f.stufe == NUR_MIT_WERKSTATTPRUEFUNG
      and GRUND_RUECKRUF_VARIANTENTREFFER in bef_f.gruende)
check("F2: confirmed_by_vin -> Floor",
      ermittle_floor([rueckruf("rueckruf-1", "confirmed_by_vin")]) is not None)
check("F3: variant_match belegt ueber die echte Rueckruf-ID",
      bef_f is not None and bef_f.evidence_ids == ["rueckruf-7"])

# ── G) P2-5: Wartungspunkt loest NICHT aus ─────────────────────────────────
print("\n-- G) P2-5-Semantik bleibt unangetastet --")
check("G: Wartungs-Insight allein -> kein Floor",
      ermittle_floor([wartung("wartung-10")]) is None)
emp, bef = wende_floor_an(KAUFEN_NACH_BESICHTIGUNG,
                          [wartung("wartung-10"), schwachstelle("schwachstelle-1", "gering")])
check("G2: Wartungspunkt erzeugt keine 'faellig'-Eskalation",
      emp == KAUFEN_NACH_BESICHTIGUNG and bef is None)
bef_g = ermittle_floor([wartung("wartung-10"), motorproblem("motorproblem-8")])
check("G3: Floor durch Motorproblem nennt NICHT die Wartungs-ID als Grund",
      bef_g is not None and "wartung-10" not in bef_g.evidence_ids
      and bef_g.gruende == [GRUND_MOTORPROBLEM])

# ── H) Marktunabhaengigkeit ────────────────────────────────────────────────
print("\n-- H) No-Market --")
ohne_markt = [motorproblem("motorproblem-8")]
mit_markt = [motorproblem("motorproblem-8"), marktvergleich()]
b1, b2 = ermittle_floor(ohne_markt), ermittle_floor(mit_markt)
check("H: Floor identisch mit und ohne Marktvergleich-Insight",
      b1 is not None and b2 is not None
      and b1.stufe == b2.stufe and b1.evidence_ids == b2.evidence_ids)
check("H2: Marktvergleich allein loest nie einen Floor aus",
      ermittle_floor([marktvergleich()]) is None)

# ── I) Fantasiefahrzeug / gegatete Identitaet ──────────────────────────────
print("\n-- I) Fantasiefahrzeug --")
# Identity-Trust-Gate: baureihe=None -> keine DB-Insights -> keine Eskalation.
check("I: keine Insights (gegatete Identitaet) -> kein Floor", ermittle_floor([]) is None)
emp, bef = wende_floor_an(UNBEKANNT, [])
check("I2: 'unbekannt' bei Fantasiefahrzeug bleibt 'unbekannt'", emp == UNBEKANNT and bef is None)
emp, bef = wende_floor_an(KAUFEN_NACH_BESICHTIGUNG, [])
check("I3: ohne Evidence keine erfundene Eskalationsbegruendung",
      emp == KAUFEN_NACH_BESICHTIGUNG and bef is None)

# ── J/K/L) Reale Bake-off-Signaturen ───────────────────────────────────────
# Die Insight-Signaturen stammen 1:1 aus dem Bake-off-Lauf gegen die Live-DB.
print("\n-- J/K/L) Bake-off-Faelle (reale Insight-Signaturen) --")

# J) BMW 320d G20, Bj. 2020: 3 Schwachstellen (gering/mittel/gering),
#    2 Rueckrufe — beide NUR series_only, weil die KBA-Referenzen (009696,
#    010000) das KBA-Trust-Gate NICHT passieren.
bmw = [schwachstelle("schwachstelle-1", "gering"),
       schwachstelle("schwachstelle-2", "mittel", confidence="mittel"),
       schwachstelle("schwachstelle-3", "gering", confidence="mittel"),
       rueckruf("rueckruf-4", "series_only"),
       rueckruf("rueckruf-5", "series_only")]
check("J: BMW -> KEIN Floor (nur series_only + gering/mittel)", ermittle_floor(bmw) is None)
emp, bef = wende_floor_an(KAUFEN_NACH_BESICHTIGUNG, bmw)
check("J2: BMW bleibt kaufen_nach_besichtigung", emp == KAUFEN_NACH_BESICHTIGUNG and bef is None)

# K) Opel Insignia B, Bj. 2018: 5 Schwachstellen (max. mittel), 1 series_only
#    UND 1 variant_match (KBA 7698, Servolenkung — Trust-Gate bestanden).
insignia = [schwachstelle("schwachstelle-1", "gering"),
            schwachstelle("schwachstelle-2", "mittel", confidence="mittel"),
            schwachstelle("schwachstelle-3", "mittel"),
            schwachstelle("schwachstelle-4", "gering"),
            schwachstelle("schwachstelle-5", "gering", confidence="mittel"),
            rueckruf("rueckruf-6", "series_only"),
            rueckruf("rueckruf-7", "variant_match", confidence="hoch")]
bef_k = ermittle_floor(insignia)
check("K: Insignia -> Floor (variant_match)", bef_k is not None
      and bef_k.stufe == NUR_MIT_WERKSTATTPRUEFUNG)
check("K2: Insignia-Floor belegt NUR den variant_match, nicht den series_only",
      bef_k is not None and bef_k.evidence_ids == ["rueckruf-7"])
emp, bef = wende_floor_an(KAUFEN_NACH_BESICHTIGUNG, insignia)
check("K3: Insignia 3.7-Empfehlung wird angehoben", emp == NUR_MIT_WERKSTATTPRUEFUNG)

# L) Audi A3 8P 2.0 FSI, Bj. 2008, 160.000 km: Schwachstelle hoch,
#    2 Motorprobleme, Wartungspunkt Zahnriemen (Status "darueber"), 2 series_only.
audi = [schwachstelle("schwachstelle-1", "mittel", confidence="mittel"),
        schwachstelle("schwachstelle-2", "mittel", confidence="mittel"),
        schwachstelle("schwachstelle-3", "hoch", confidence="mittel"),
        schwachstelle("schwachstelle-4", "gering", confidence="mittel"),
        schwachstelle("schwachstelle-5", "gering", confidence="mittel"),
        rueckruf("rueckruf-6", "series_only"),
        rueckruf("rueckruf-7", "series_only"),
        motorproblem("motorproblem-8", confidence="mittel"),
        motorproblem("motorproblem-9", confidence="mittel"),
        wartung("wartung-10")]
bef_l = ermittle_floor(audi)
check("L: Audi -> Floor", bef_l is not None and bef_l.stufe == NUR_MIT_WERKSTATTPRUEFUNG)
check("L2: Audi-Floor aus Motorproblem UND hoher Schwachstelle",
      bef_l is not None and set(bef_l.gruende) == {GRUND_MOTORPROBLEM, GRUND_SCHWACHSTELLE_HOCH})
check("L3: Audi-Floor nennt weder Wartungs- noch series_only-IDs",
      bef_l is not None and "wartung-10" not in bef_l.evidence_ids
      and "rueckruf-6" not in bef_l.evidence_ids and "rueckruf-7" not in bef_l.evidence_ids)
emp, bef = wende_floor_an(KAUFEN_NACH_BESICHTIGUNG, audi)
check("L4: Audi 3.7-Empfehlung wird angehoben", emp == NUR_MIT_WERKSTATTPRUEFUNG)

# ── Idempotenz ──────────────────────────────────────────────────────────────
print("\n-- Idempotenz --")
e1, _ = wende_floor_an(KAUFEN_NACH_BESICHTIGUNG, audi)
e2, b2_ = wende_floor_an(e1, audi)
check("Idempotent: zweiter Durchlauf aendert nichts mehr", e1 == e2 and b2_ is None)

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER: " + ", ".join(FEHLER))
    raise SystemExit(1)
print("Alle Empfehlungs-Floor-Tests bestanden.")
