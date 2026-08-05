"""
Test: Phase 2 Key Findings (app/key_findings) — deterministisch, kein LLM.

Deckt die geforderten Fälle ab: Preisabweichung (unter/über), schwache Datenbasis,
exakter vs. unklarer Rückruf, geringe Schwachstelle, Inserat-Widerspruch, kein
Befund, Cap 5, Sortierung, nur gültige Evidence-IDs.

Ausfuehren:  python test_key_findings.py
"""
import os
import sys
import tempfile
from types import SimpleNamespace

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_kf_"), "test.db")
sys.path.insert(0, ".")

from app.key_findings import build_key_findings_kauf, build_key_findings_verkauf, MAX_FINDINGS  # noqa: E402
from app.models import Insight, Marktanalyse, KaufCheckResponse, VerkaufsCheckResponse  # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


def mv_insight(median, angebot, diff, pct, quali="mittel", iid="marktvergleich-9"):
    ma = Marktanalyse(gefunden=12, verwendet=8, anzahl_sehr_aehnlich=5, anzahl_aehnlich=3,
                      median_eur=median, spanne_min_eur=(median - 2000) if median else None,
                      spanne_max_eur=(median + 2000) if median else None,
                      angebot_eur=angebot, differenz_eur=diff, differenz_pct=pct,
                      datenqualitaet=quali,
                      methode=("Median…" if median else "Zu wenige vergleichbare Preisangaben — begrenzte Datenbasis."))
    return Insight(id=iid, kategorie="marktvergleich", titel="Marktvergleich", beschreibung="…",
                   confidence=quali, marktanalyse=ma)


def rueckruf(iid, titel, applic):
    return Insight(id=iid, kategorie="rueckruf", titel=titel, beschreibung="…",
                   confidence="hoch" if applic == "exakt" else "niedrig", applicability=applic)


def schwach(iid, titel, sev):
    return Insight(id=iid, kategorie="schwachstelle", titel=titel, beschreibung="…",
                   confidence="hoch", schweregrad=sev)


def kf_of(findings, kategorie):
    return [f for f in findings if f.kategorie == kategorie]


REQ_BMW = SimpleNamespace(marke="BMW", modell="320d", baujahr=2020, kilometerstand=78500,
                          motor="2.0 Diesel 190 PS Automatik", kraftstoff="Diesel", preis_eur=23490,
                          beschreibung=None, freitext=None, scheckheftgepflegt=None, ausstattung=[])
BAUREIHE_BMW = {"id": "bmw-3er-g20", "generation": "G20", "bauzeitraum_von": 2019, "bauzeitraum_bis": None}
MOTOR_BMW = {"bezeichnung": "320d", "kraftstoff": "Diesel", "leistung_ps": 190}

# ── 1) Angebot deutlich unter Median -> positives Preis-Finding ──────────────
f1 = build_key_findings_kauf(REQ_BMW, BAUREIHE_BMW, MOTOR_BMW,
                             [mv_insight(26500, 23490, -3010, -11.4, "mittel")])
preis = kf_of(f1, "preis")
check("1: Angebot -11,4% -> Preis-Finding 'chance'", bool(preis) and preis[0].stufe == "chance")
check("1: Preis-Finding referenziert Marktvergleich-Evidence-ID",
      bool(preis) and preis[0].evidence_ids == ["marktvergleich-9"])
check("1: Wert kompakt (gerundetes Prozent, kein Scheinpräzision >1 Nachkomma)",
      bool(preis) and "ca. 11,4 %" in (preis[0].wert or ""))

# ── 2) Angebot deutlich über Median -> Warn-Finding ─────────────────────────
f2 = build_key_findings_kauf(REQ_BMW, BAUREIHE_BMW, MOTOR_BMW,
                             [mv_insight(20000, 26000, 6000, 30.0, "hoch")])
preis2 = kf_of(f2, "preis")
check("2: Angebot +30% -> Preis-Finding 'warnung'", bool(preis2) and preis2[0].stufe == "warnung")

# ── 2b) Extrem niedrig + gute Daten -> 'Ungewöhnlich günstiger Preis' (KEIN Betrug) ─
f2b = build_key_findings_kauf(REQ_BMW, BAUREIHE_BMW, MOTOR_BMW,
                              [mv_insight(26000, 17000, -9000, -34.6, "hoch")])
p2b = kf_of(f2b, "preis")
check("H1: -34,6% & gute Daten -> 'Ungewöhnlich günstiger Preis'",
      bool(p2b) and "ungewöhnlich günstig" in p2b[0].titel.lower())
check("H1: Stufe 'warnung' (NICHT 'kritisch')", bool(p2b) and p2b[0].stufe == "warnung")
check("H1: kein Wort 'Betrug' irgendwo im Finding",
      bool(p2b) and all("betrug" not in (getattr(p2b[0], a) or "").lower()
                        for a in ("titel", "beschreibung", "aktion")))
check("H1: keine 'betrug'-Kategorie / kein 'kritisch' allein wegen Preis",
      not kf_of(f2b, "betrug") and all(f.stufe != "kritisch" for f in f2b))

# ── 3) Schwache Markt-Datenbasis (kein Median) -> keine Preisabweichung ──────
f3 = build_key_findings_kauf(REQ_BMW, BAUREIHE_BMW, MOTOR_BMW,
                             [mv_insight(None, 23490, None, None, "niedrig")])
check("3: kein Median -> KEIN Preis-/Betrugs-Finding",
      not kf_of(f3, "preis") and not kf_of(f3, "betrug"))

# ── 4) Exakter sicherheitsrelevanter Rückruf -> Finding ─────────────────────
# Reliability-Sprint 3 (§27/§28): "exakt"/"wahrscheinlich" -> "variant_match"/
# "series_only" (KANN betreffen statt "betrifft" — ohne VIN-Prüfung nie sicher).
f4 = build_key_findings_kauf(REQ_BMW, BAUREIHE_BMW, MOTOR_BMW, [
    mv_insight(26500, 23490, -3010, -11.4),
    rueckruf("rueckruf-1", "KBA-Rückruf: Bremskraftverstärker", "variant_match"),
    rueckruf("rueckruf-2", "KBA-Rückruf: Lenkung", "series_only"),
])
rk = kf_of(f4, "rueckruf")
check("4: zu prüfende Rückrufe -> ein Rückruf-Finding 'warnung'",
      len(rk) == 1 and rk[0].stufe == "warnung")
check("4: Rückruf-Finding zählt 2 und referenziert beide IDs",
      bool(rk) and "2" in rk[0].titel and set(rk[0].evidence_ids) == {"rueckruf-1", "rueckruf-2"})
check("4: Aktion nennt FIN-Prüfung", bool(rk) and "FIN" in (rk[0].aktion or ""))
check("4: Titel behauptet NICHT 'relevant' (ohne VIN nicht gesichert, §27)",
      bool(rk) and "relevant" not in rk[0].titel.lower())

# ── 5) applicability=unclear -> NIE als sicher betroffen ────────────────────
f5 = build_key_findings_kauf(REQ_BMW, BAUREIHE_BMW, MOTOR_BMW, [
    rueckruf("rueckruf-3", "KBA-Rückruf (Baureihe): Brandgefahr der Hochvoltbatterie", "unclear"),
])
rk5 = kf_of(f5, "rueckruf")
check("5: unklarer Rückruf -> Finding 'info' (nicht 'warnung'/'kritisch')",
      bool(rk5) and rk5[0].stufe == "info")
check("5: unklarer Rückruf -> Titel/Aktion sprechen von 'unklar'/FIN, nicht 'betrifft sicher'",
      bool(rk5) and ("unklar" in rk5[0].titel.lower()) and ("betrifft dein" not in rk5[0].beschreibung.lower()))

# ── 6) geringe Schwachstelle -> NICHT automatisch kritisches Finding ────────
f6 = build_key_findings_kauf(REQ_BMW, BAUREIHE_BMW, MOTOR_BMW, [
    schwach("schwachstelle-1", "Ablagefach — Klappergeräusch", "gering"),
])
check("6: geringe Schwachstelle erzeugt KEIN Schwachstellen-Finding (nur hoch)",
      not kf_of(f6, "schwachstelle"))
check("6: keine kritische Stufe für geringe Schwachstelle",
      all(f.stufe != "kritisch" for f in f6))

# ── 6b) hohe Schwachstelle -> Warn-Finding mit Evidence ─────────────────────
f6b = build_key_findings_kauf(REQ_BMW, BAUREIHE_BMW, MOTOR_BMW, [
    schwach("schwachstelle-2", "AGR-Kühler — Verrußung/Brandgefahr", "hoch"),
])
sw = kf_of(f6b, "schwachstelle")
check("6b: hohe Schwachstelle -> 'warnung' + Evidence-ID",
      bool(sw) and sw[0].stufe == "warnung" and sw[0].evidence_ids == ["schwachstelle-2"])

# ── H2) "Keine schweren bekannten Motorprobleme gefunden" — nur mit Motor-Daten ─
def motorproblem(iid, titel):
    return Insight(id=iid, kategorie="motorproblem", titel=titel, beschreibung="…", confidence="mittel")


MOTOR_MIT_DATEN = {"bezeichnung": "320d", "kraftstoff": "Diesel", "leistung_ps": 190,
                   "kritische_wartung": [{"bauteil": "Steuerkette", "intervall": "—"}],
                   "schwachstellen_motor": []}
MOTOR_OHNE_DATEN = {"bezeichnung": "320d", "kraftstoff": "Diesel", "leistung_ps": 190}  # keine Motor-Daten

# H2-3: keine schwere Motor-Schwachstelle + vorhandene Motor-Daten -> positives Finding
fH3 = build_key_findings_kauf(REQ_BMW, BAUREIHE_BMW, MOTOR_MIT_DATEN, [])
vH3 = [f for f in fH3 if f.kategorie == "vorteil" and "motorprobleme" in f.titel.lower()]
check("H2-3: Motor-Daten vorhanden & keine schwere Schwachstelle -> 'Keine schweren bekannten Motorprobleme gefunden'",
      bool(vH3) and "keine schweren bekannten motorprobleme" in vH3[0].titel.lower())
check("H2-3: Formulierung zurückhaltend (nicht 'unauffällig'/'keine Probleme')",
      bool(vH3) and "unauffällig" not in vH3[0].titel.lower()
      and "bedeutet nicht" in (vH3[0].beschreibung.lower()))

# H2-4: KEINE Motor-Daten -> KEIN positives Motor-Finding ("keine Daten" != "unauffällig")
fH4 = build_key_findings_kauf(REQ_BMW, BAUREIHE_BMW, MOTOR_OHNE_DATEN, [])
check("H2-4: keine Motor-Daten -> KEIN positives Motor-Finding",
      not [f for f in fH4 if f.kategorie == "vorteil" and "motorprobleme" in f.titel.lower()])

# H2-5: schwere Motor-Schwachstelle (motorproblem-Insight) -> KEIN positives Motor-Finding
fH5 = build_key_findings_kauf(REQ_BMW, BAUREIHE_BMW, MOTOR_MIT_DATEN,
                              [motorproblem("motorproblem-1", "Steuerkette — Längung")])
check("H2-5: schwere Motor-Schwachstelle -> KEIN positives Motor-Finding",
      not [f for f in fH5 if f.kategorie == "vorteil" and "motorprobleme" in f.titel.lower()])
check("H2-5: stattdessen Motorproblem-Finding vorhanden", bool(kf_of(fH5, "motorproblem")))

# ── 7) Inserat-Widerspruch (Kraftstoff) -> Finding ──────────────────────────
REQ_WIDER = SimpleNamespace(marke="BMW", modell="320d", baujahr=2020, kilometerstand=78500,
                            motor="320i", kraftstoff="Benzin", preis_eur=23490,
                            beschreibung=None, freitext=None, scheckheftgepflegt=None, ausstattung=[])
f7 = build_key_findings_kauf(REQ_WIDER, BAUREIHE_BMW, {"bezeichnung": "320d", "kraftstoff": "Diesel", "leistung_ps": 190}, [])
ws = kf_of(f7, "widerspruch")
check("7: Kraftstoff-Widerspruch (Benzin vs Diesel) -> Widerspruch-Finding", bool(ws))
check("7: Widerspruch hat keine Evidence-ID (rein aus Inserat)", bool(ws) and ws[0].evidence_ids == [])

# ── 7b) Baujahr außerhalb Bauzeitraum -> kritischer Widerspruch ─────────────
REQ_BJ = SimpleNamespace(marke="BMW", modell="320d", baujahr=2012, kilometerstand=78500,
                         motor="320d", kraftstoff="Diesel", preis_eur=23490,
                         beschreibung=None, freitext=None, scheckheftgepflegt=None, ausstattung=[])
f7b = build_key_findings_kauf(REQ_BJ, BAUREIHE_BMW, MOTOR_BMW, [])
wsb = kf_of(f7b, "widerspruch")
check("7b: Baujahr 2012 außerhalb G20 (ab 2019) -> Widerspruch 'kritisch'",
      bool(wsb) and any(f.stufe == "kritisch" for f in wsb))

# ── 8) Kein besonderer Befund -> wenige/keine Findings (kein Zwang) ──────────
REQ_NEUTRAL = SimpleNamespace(marke="BMW", modell="320d", baujahr=2021, kilometerstand=45000,
                              motor="320d", kraftstoff="Diesel", preis_eur=26400,
                              beschreibung="gepflegt", freitext=None, scheckheftgepflegt=None, ausstattung=[])
f8 = build_key_findings_kauf(REQ_NEUTRAL, BAUREIHE_BMW, MOTOR_BMW,
                             [mv_insight(26500, 26400, -100, -0.4, "mittel")])
check("8: neutraler Fall -> keine kritischen/warnenden Findings erzwungen",
      all(f.stufe in ("info", "chance") for f in f8))
check("8: kein künstlicher Inhalt -> überschaubar wenige Findings", len(f8) <= 3)

# ── 9/10) Cap 5 + deterministische Sortierung (Betrug > Widerspruch > Preis) ─
viele = [mv_insight(26000, 17000, -9000, -34.6, "hoch")] + \
        [rueckruf(f"rueckruf-{i}", f"KBA-Rückruf: Mangel {i}", "exakt") for i in range(3)] + \
        [schwach(f"schwachstelle-{i}", f"Bauteil {i} — Defekt", "hoch") for i in range(3)]
REQ_MULTI = SimpleNamespace(marke="BMW", modell="320d", baujahr=2012, kilometerstand=45000,
                            motor="320i", kraftstoff="Benzin", preis_eur=17000,
                            beschreibung=None, freitext=None, scheckheftgepflegt=True, ausstattung=[])
f9 = build_key_findings_kauf(REQ_MULTI, BAUREIHE_BMW, MOTOR_BMW, viele)
check("9: maximal 5 Findings", len(f9) <= MAX_FINDINGS)
check("10: absteigend nach Priorität sortiert",
      all(f9[i].prioritaet >= f9[i + 1].prioritaet for i in range(len(f9) - 1)))
check("10: höchste Priorität ist Betrug/Widerspruch (kritisch zuerst)",
      f9[0].stufe == "kritisch")
check("10: IDs fortlaufend finding-1..n", [f.id for f in f9] == [f"finding-{i}" for i in range(1, len(f9) + 1)])

# ── 11) Evidence-IDs nur gültige (aus insights) ─────────────────────────────
alle_ev = {e for f in f9 for e in f.evidence_ids}
gueltige = {"marktvergleich-9"} | {f"rueckruf-{i}" for i in range(3)} | {f"schwachstelle-{i}" for i in range(3)}
check("11: alle Evidence-IDs stammen aus existierenden Insights", alle_ev <= gueltige)

# ── VERKAUF ─────────────────────────────────────────────────────────────────
REQ_VERK = SimpleNamespace(marke="Mercedes-Benz", modell="C-Klasse", baujahr=2019, kilometerstand=64300,
                           motor="C 200", kraftstoff="Benzin", preis_vorstellung=24000,
                           beschreibung="gepflegt", freitext=None, unfallfrei="ja", vorbesitzer=1,
                           tuev_bis=None, scheckheftgepflegt=True,
                           ausstattung=["Leder", "Panoramadach", "Navi", "AHK"])
fv = build_key_findings_verkauf(REQ_VERK, {"id": "mb"}, {"bezeichnung": "C200"},
                                [mv_insight(22000, 24000, 2000, 9.1, "mittel", "marktvergleich-5")])
check("V1: Zielpreis +9,1% -> Marktposition 'warnung'",
      bool(kf_of(fv, "marktposition")) and kf_of(fv, "marktposition")[0].stufe == "warnung")
check("V2: fehlende Angabe (TÜV) -> angaben-Finding", bool(kf_of(fv, "angaben")))
check("V3: wertsteigernde Ausstattung erkannt (keine €-Aufschläge)",
      bool(kf_of(fv, "ausstattung")) and "€" not in kf_of(fv, "ausstattung")[0].beschreibung)
check("V: max 5 Findings", len(fv) <= MAX_FINDINGS)

# V3b: keine erfundene Ausstattung — 'LED' darf NICHT aus 'Lederausstattung' entstehen.
from app.key_findings import _ausstattung_treffer  # noqa: E402
treffer_leder = _ausstattung_treffer(["Lederausstattung", "Panoramadach"])
check("V3b: 'Lederausstattung' -> 'Lederausstattung' erkannt", "Lederausstattung" in treffer_leder)
check("V3b: 'Lederausstattung' erzeugt KEIN 'LED-Scheinwerfer'", "LED-Scheinwerfer" not in treffer_leder)
check("V3b: echtes 'LED-Scheinwerfer' wird weiterhin erkannt",
      "LED-Scheinwerfer" in _ausstattung_treffer(["LED-Scheinwerfer", "Navi"])
      and "Navigation" in _ausstattung_treffer(["LED-Scheinwerfer", "Navi"]))

# schwache Marktdaten -> Datenqualitäts-Finding, keine Preisabweichung
fv2 = build_key_findings_verkauf(REQ_VERK, {"id": "mb"}, {"bezeichnung": "C200"},
                                 [mv_insight(None, 24000, None, None, "niedrig", "marktvergleich-5")])
check("V4: schwache Marktdaten -> Datenqualitäts-Finding statt Preisposition",
      bool(kf_of(fv2, "datenqualitaet")) and not kf_of(fv2, "marktposition"))

# ── Response-Modelle: key_findings additiv + Default [] ─────────────────────
kf1 = f1[0]
kauf = KaufCheckResponse(bericht="x", empfehlung="kaufen", preis_bewertung="guenstig",
                         quelle="gemischt", vertrauen="mittel", key_findings=f1)
check("Modell: KaufCheckResponse akzeptiert key_findings", len(kauf.key_findings) == len(f1))
altes = KaufCheckResponse(bericht="x", empfehlung="kaufen", preis_bewertung="guenstig",
                          quelle="web", vertrauen="niedrig")
check("BC: altes Ergebnis ohne key_findings -> Default []", altes.key_findings == [])
verk = VerkaufsCheckResponse(bericht="x", quelle="web", vertrauen="niedrig")
check("BC: VerkaufsCheckResponse key_findings Default []", verk.key_findings == [])

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER: " + ", ".join(FEHLER))
    raise SystemExit(1)
print("Alle Key-Findings-Tests bestanden.")
