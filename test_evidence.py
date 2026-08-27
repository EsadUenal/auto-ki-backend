"""
Test: Provenance / Evidence (app/evidence.build_insights) — Phase 1.

Deterministisch, kein Gemini-Aufruf. Deckt die geforderten Punkte 1-8 ab
(Punkt 9 Analyse-Chat: unveraendert, siehe test_check_fragen).

Ausfuehren:  python test_evidence.py
"""
import os
import sys
import tempfile
from types import SimpleNamespace

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_test_"), "test.db")
sys.path.insert(0, ".")

from app.evidence import build_insights                       # noqa: E402
from app.models import KaufCheckResponse, VerkaufsCheckResponse  # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


def req(baujahr=2020):
    return SimpleNamespace(baujahr=baujahr)


# DATA-SAFETY-RUNTIME-GATE: diese Fixture ist ausdruecklich VERIFIED, damit die
# hier geprueften Aussagen (KBA-Referenz am Insight, Quellentitel) ueberhaupt
# beobachtbar bleiben. Das unverifizierte Verhalten pruefen
# test_data_trust_runtime.py und test_kaufcheck_motor_applicability.py.
BAUREIHE = {
    "id": "bmw-3er-g20",
    "verification": {f: {"status": "verified", "source": "https://example.test/nachweis",
                         "date": "2026-08-24"}
                     for f in ("schwachstellen", "motorprobleme", "rueckrufe", "wartung")},
    "quellen": [{"quelle": "BMW-Servicedoku", "url": "https://example.com/bmw", "abrufdatum": "2026-01-01"}],
    "schwachstellen_baureihe": [
        {"bauteil": "AGR-Kühler", "beschreibung": "AGR-System kann verrußen.",
         "betroffene_baujahre": "2019-2021", "schweregrad": "hoch"},
        {"bauteil": "Steuerkette (früh)", "beschreibung": "Nur frühe Baujahre betroffen.",
         "betroffene_baujahre": "2012-2015", "schweregrad": "hoch"},
    ],
    "rueckrufe": [
        {"datum": "2020-05", "betroffene_baujahre": "2020", "mangel": "Bremskraftverstärker",
         # Reales KBA-Referenzformat (rein numerisch) statt eines erfundenen
         # "KBA-..."-Präfixes — der DATA-TRUST-AUDIT hat kein einziges reales
         # Vorkommen eines Buchstaben-Präfixes mit mehr als einem Buchstaben
         # gefunden; das KBA-Trust-Gate (app/recall_filter.py) akzeptiert deshalb
         # nur Formate, die im Bestand tatsächlich vorkommen.
         "abhilfe": "Software-Update", "kba_referenz": "011234"},
    ],
}
MOTOR = {
    "bezeichnung": "320d", "variante_id": "bmw-3er-g20-320d",
    "schwachstellen_motor": [
        {"bauteil": "Injektoren", "beschreibung": "Undichte Injektoren möglich.",
         "baujahre": "2019-2021", "kosten_ca": "600-1200 €"},
    ],
}
BELEGE_STARK = [
    {"typ": "web", "titel": "BMW 320d — AutoScout24", "url": "https://autoscout24.de/1", "qualitaet": "Marktplatz"},
    {"typ": "web", "titel": "BMW 320d — mobile.de", "url": "https://mobile.de/2", "qualitaet": "Marktplatz"},
    {"typ": "web", "titel": "BMW 320d — kleinanzeigen", "url": "https://kleinanzeigen.de/3", "qualitaet": "Marktplatz"},
]
BELEGE_SCHWACH = [
    {"typ": "web", "titel": "Forum-Thread", "url": "https://forum.de/x", "qualitaet": "Community/Erfahrungsbericht"},
]

# ── 1) DB-basierte Schwachstelle -> Quelle korrekt als DB ───────────────────
ins = build_insights(BAUREIHE, MOTOR, BELEGE_STARK, req(2020), check_typ="kauf",
                     marktpreis_min=23000, marktpreis_max=27000)
schw = [i for i in ins if i.kategorie == "schwachstelle"]
agr = next((i for i in schw if "AGR" in i.titel), None)
check("1: AGR-Schwachstelle vorhanden", agr is not None)
check("1: Quelle als 'datenbank' gekennzeichnet", bool(agr) and "datenbank" in agr.quellen_typen)
check("1: NICHT faelschlich als 'web'", bool(agr) and "web" not in agr.quellen_typen)
# Punkt 1: allgemeine Baureihen-Quelle (Tabelle `quelle`, nur per baureihe_id) darf
# NICHT als konkreter Beleg für eine spezifische Schwachstelle ausgegeben werden.
check("1: allgemeine Baureihen-URL NICHT als konkreter Beleg am Insight",
      bool(agr) and all(q.url is None for q in agr.quellen))
check("1: Schwachstelle-Herkunft bleibt 'datenbank' (ohne Fremd-URL)",
      bool(agr) and [q.typ for q in agr.quellen] == ["datenbank"])
check("1: Baujahr-Deckung -> confidence hoch", bool(agr) and agr.confidence == "hoch")
check("1: nicht passende Schwachstelle (2012-2015) ausgelassen",
      all("Steuerkette" not in i.titel for i in schw))

# ── 2) KBA-Rückruf -> Rückrufquelle korrekt ─────────────────────────────────
rk = [i for i in ins if i.kategorie == "rueckruf"]
check("2: Rückruf-Insight vorhanden", len(rk) == 1)
check("2: quellen_typ 'rueckruf_kba'", bool(rk) and "rueckruf_kba" in rk[0].quellen_typen)
check("2: KBA-Referenz als ref hinterlegt", bool(rk) and any(q.ref == "011234" for q in rk[0].quellen))
check("2: confidence hoch (KBA + Baujahr)", bool(rk) and rk[0].confidence == "hoch")

# ── 2b) Severity und Confidence sind STRIKT unabhängig ──────────────────────
BAUREIHE_SEV = {
    "id": "sev", "quellen": [],
    "schwachstellen_baureihe": [
        {"bauteil": "A", "beschreibung": "gleiche Baujahre, hoher Schweregrad",
         "betroffene_baujahre": "2019-2021", "schweregrad": "hoch"},
        {"bauteil": "B", "beschreibung": "gleiche Baujahre, geringer Schweregrad",
         "betroffene_baujahre": "2019-2021", "schweregrad": "gering"},
    ],
    "rueckrufe": [],
}
sev = [i for i in build_insights(BAUREIHE_SEV, None, [], req(2020)) if i.kategorie == "schwachstelle"]
check("2b: schweregrad als EIGENES Feld erhalten", {i.schweregrad for i in sev} == {"hoch", "gering"})
check("2b: gleiche Baujahr-Deckung -> gleiche confidence trotz unterschiedl. schweregrad",
      len(sev) == 2 and len({i.confidence for i in sev}) == 1)
check("2b: confidence bleibt provenance-basiert (hoch), nicht vom schweregrad abhängig",
      all(i.confidence == "hoch" for i in sev))

# ── 2c) Rückruf-Applicability (Phase 1B): Varianten-/Antriebs-Zuordnung ──────
# severity / confidence / applicability sind DREI getrennte Konzepte.
BAUREIHE_HV = {
    "id": "bmw-3er-g20-g21", "quellen": [], "schwachstellen_baureihe": [],
    "verification": {f: {"status": "verified", "source": "https://example.test/nachweis",
                         "date": "2026-08-24"}
                     for f in ("schwachstellen", "motorprobleme", "rueckrufe", "wartung")},
    "rueckrufe": [
        {"datum": "2020-03", "betroffene_baujahre": "2019-2020",
         "mangel": "Möglicher Ausfall des Bremskraftverstärkers",
         "abhilfe": "Prüfung", "kba_referenz": "009696"},
        {"datum": "2020-10", "betroffene_baujahre": "2019-2020 (Plug-in-Hybrid)",
         "mangel": "Brandgefahr der Hochvoltbatterie",
         "abhilfe": "Austausch der Hochvoltbatterie-Module", "kba_referenz": "010078"},
    ],
}
MOTOR_DIESEL = {"bezeichnung": "320d", "kraftstoff": "Diesel", "schwachstellen_motor": []}
MOTOR_PHEV = {"bezeichnung": "330e", "kraftstoff": "Plug-in-Hybrid", "schwachstellen_motor": []}

_rk_diesel = {i.titel: i for i in build_insights(BAUREIHE_HV, MOTOR_DIESEL, [], req(2020)) if i.kategorie == "rueckruf"}
brems_d = next(i for t, i in _rk_diesel.items() if "Bremskraft" in t)
# §8 (Reliability-Sprint): Ein Hochvolt-/PHEV-Rückruf bei EINDEUTIG erkanntem Diesel
# wird VOLLSTÄNDIG aus den sichtbaren Findings entfernt — NICHT mehr als "unklare
# Betroffenheit" angezeigt.
check("11: Hochvolt-Rückruf beim 320d-Diesel -> vollständig entfernt (nicht sichtbar)",
      not any("Hochvolt" in t for t in _rk_diesel))
check("11: nicht betroffener Rückruf taucht auch nicht in 'Was jetzt?' auf (kein Insight)",
      all("Hochvolt" not in i.titel for i in _rk_diesel.values()))
# Reliability-Sprint 3 (§27/§28): "exakt"/"unklar" -> "variant_match"/"unclear".
# variant_match ist die stärkste OHNE VIN-Prüfung erreichbare Stufe (§27: niemals
# "confirmed_by_vin" ohne echte VIN-Prüfung, die es im System nicht gibt).
# FLOOR-SAFETY-AUDIT (Batch A): ein allgemeiner Baureihen-Rückruf ohne
# Antriebs-Scope bleibt "series_only", auch wenn das Baujahr exakt passt — die
# Baujahr-Deckung sagt nichts über die betroffene Variante. Die BELEGLAGE bleibt
# davon unberührt und ist weiterhin "hoch".
check("13: allgemeiner Bremsen-Rückruf (kein Antriebs-Scope) -> 'series_only', Beleglage hoch",
      brems_d.applicability == "series_only" and brems_d.confidence == "hoch")

_rk_phev = {i.titel: i for i in build_insights(BAUREIHE_HV, MOTOR_PHEV, [], req(2020)) if i.kategorie == "rueckruf"}
hv_p = next(i for t, i in _rk_phev.items() if "Hochvolt" in t)
check("13: Hochvolt-Rückruf beim 330e Plug-in-Hybrid -> applicability 'variant_match'",
      hv_p.applicability == "variant_match" and hv_p.confidence == "hoch")

_rk_nomotor = {i.titel: i for i in build_insights(BAUREIHE_HV, None, [], req(2020)) if i.kategorie == "rueckruf"}
hv_n = next(i for t, i in _rk_nomotor.items() if "Hochvolt" in t)
check("12: Motor nicht erkannt -> Hochvolt-Applicability 'unclear' (nicht raten)",
      hv_n.applicability == "unclear")

check("Sprint3: niemals 'confirmed_by_vin' (§27, keine VIN-Prüfung im System)",
      brems_d.applicability != "confirmed_by_vin" and hv_p.applicability != "confirmed_by_vin")

_rk_2023 = [i for i in build_insights(BAUREIHE_HV, MOTOR_DIESEL, [], req(2023)) if i.kategorie == "rueckruf"]
check("14: Baujahr 2023 außerhalb 2019-2020 -> Rückrufe ausgelassen", len(_rk_2023) == 0)
check("15: KBA-Referenz bleibt am (betroffenen) Rückruf erhalten",
      any(q.ref == "010078" for q in hv_p.quellen))
check("16: severity/confidence/applicability getrennt (Rückruf hat applicability-Feld, keine severity)",
      hv_p.applicability is not None and hv_p.schweregrad is None)

# ── 3) Webbasierter Marktpreis -> Web/Marktvergleich (Marktvergleich 2.0) ────
mv = [i for i in ins if i.kategorie == "marktvergleich"]
check("3: Marktvergleich-Insight vorhanden", len(mv) == 1)
check("3: quellen_typen web + marktvergleich",
      bool(mv) and "web" in mv[0].quellen_typen and "marktvergleich" in mv[0].quellen_typen)
check("3: Web-URL bleibt als Recherchequelle erhalten", bool(mv) and any(q.url for q in mv[0].quellen))
# Marktvergleich 2.0: OHNE belastbare deterministische Marktanalyse ist die
# Confidence bewusst konservativ 'niedrig' — Belege allein (Titel+URL) belegen
# KEINE Vergleichbarkeit. Die Confidence kommt NICHT mehr aus der Trefferzahl.
check("3: ohne deterministische Analyse -> Marktvergleich confidence 'niedrig'",
      bool(mv) and mv[0].confidence == "niedrig")
BELEGE_VIER = BELEGE_STARK + [{"typ": "web", "titel": "4. Angebot",
                               "url": "https://autoscout24.de/4", "qualitaet": "Marktplatz"}]
mv4 = [i for i in build_insights(None, None, BELEGE_VIER, req(2020)) if i.kategorie == "marktvergleich"]
check("3: 4 Marktplatz-URLs bedeuten NICHT automatisch 'hoch'", bool(mv4) and mv4[0].confidence != "hoch")
# Mit deterministischer Marktanalyse spiegelt die Confidence die Datenqualität.
from app.models import Marktanalyse  # noqa: E402
_ma = Marktanalyse(gefunden=8, verwendet=6, anzahl_sehr_aehnlich=4, anzahl_aehnlich=2,
                   median_eur=26200, spanne_min_eur=25600, spanne_max_eur=26900,
                   datenqualitaet="mittel")
mvA = [i for i in build_insights(None, None, BELEGE_STARK, req(2020), marktanalyse=_ma)
       if i.kategorie == "marktvergleich"]
check("3: deterministische Analyse -> Confidence = Datenqualität ('mittel')",
      bool(mvA) and mvA[0].confidence == "mittel")
check("3: Marktvergleich-Insight trägt strukturierte marktanalyse (Median/Spanne)",
      bool(mvA) and mvA[0].marktanalyse is not None and mvA[0].marktanalyse.median_eur == 26200)
check("3: Beschreibung nennt Median UND typischen Marktbereich",
      bool(mvA) and "Median" in mvA[0].beschreibung and "Typischer Marktbereich" in mvA[0].beschreibung)

# ── 4) Reine KI-Ableitung / fehlende Daten -> NICHT als DB/Web ──────────────
check("4: ohne DB & Web -> keine erfundenen Insights", build_insights(None, None, [], req(2020)) == [])
nur_web = build_insights(None, None, BELEGE_STARK, req(2020))
check("4: nur Web vorhanden -> ausschließlich Marktvergleich (kein DB/Rückruf)",
      all(i.kategorie == "marktvergleich" for i in nur_web) and len(nur_web) == 1)
nur_db = build_insights(BAUREIHE, None, [], req(2020))
check("4: nur DB vorhanden -> kein 'web'-Typ irgendwo",
      all("web" not in i.quellen_typen for i in nur_db) and all(i.kategorie != "marktvergleich" for i in nur_db))

# ── 5) Schwache Datenlage -> Confidence reduziert ───────────────────────────
schwach = build_insights(BAUREIHE, MOTOR, BELEGE_SCHWACH, req(None))
mv2 = [i for i in schwach if i.kategorie == "marktvergleich"]
check("5: einzelne Community-Quelle -> marktvergleich confidence niedrig",
      bool(mv2) and mv2[0].confidence == "niedrig")
check("5: Baujahr unbekannt -> Schwachstellen confidence mittel (nicht hoch)",
      all(i.confidence == "mittel" for i in schwach if i.kategorie == "schwachstelle"))
check("5: Motor nicht erkannt -> keine Motorproblem-Insights",
      all(i.kategorie != "motorproblem" for i in build_insights(BAUREIHE, None, [], req(2020))))
check("5: Motor erkannt -> Motorproblem-Insight mit motorvarianten-Quelle",
      any(i.kategorie == "motorproblem" and "motorvarianten" in i.quellen_typen for i in ins))

# ── 6/7) Response-Modelle: insights additiv, Kauf & Verkauf vollständig ─────
kauf = KaufCheckResponse(bericht="...", empfehlung="kaufen", preis_bewertung="guenstig",
                         quelle="gemischt", vertrauen="mittel", insights=ins)
check("6: KaufCheckResponse akzeptiert insights + alte Felder", len(kauf.insights) >= 3 and kauf.empfehlung == "kaufen")
verk_ins = build_insights(BAUREIHE, MOTOR, BELEGE_STARK, req(2020), check_typ="verkauf")
verk = VerkaufsCheckResponse(bericht="...", quelle="web", vertrauen="niedrig", insights=verk_ins)
check("7: VerkaufsCheckResponse akzeptiert insights", len(verk.insights) >= 1)
check("7: Verkaufs-Kontext -> Schwachstellen-Einfluss angepasst",
      any("Verkauf" in (i.einfluss or "") for i in verk.insights if i.kategorie == "schwachstelle"))

# ── 8) Alte gespeicherte Checks (ergebnis-Dict OHNE insights) weiter ladbar ──
altes_ergebnis = {
    "bericht": "x", "empfehlung": "kaufen", "preis_bewertung": "guenstig",
    "marktpreis_min": None, "marktpreis_max": None, "baureihe_erkannt": None,
    "motor_erkannt": None, "quelle": "web", "vertrauen": "niedrig", "belege": [],
}
check("8: altes ergebnis-Dict ohne insights -> default []", KaufCheckResponse(**altes_ergebnis).insights == [])

# ══ SCHICHT B: Evidence an LLM geben & referenzierte IDs validieren ══════════
from app.evidence import (format_evidence_for_prompt, filter_evidence_ids,  # noqa: E402
                          valid_evidence_ids, enrich_marktvergleich_spanne)

insB = build_insights(BAUREIHE, MOTOR, BELEGE_STARK, req(2020), check_typ="kauf")
ids_all = [i.id for i in insB]
gueltig = valid_evidence_ids(insB)

# Evidence-Block: kompakt, enthält alle IDs, kein aufgeblähter JSON-Blob
block = format_evidence_for_prompt(insB)
check("B: Evidence-Block enthält alle IDs", all(f"[{i.id}]" in block for i in insB))
check("B: Evidence-Block nennt Confidence", "Confidence:" in block)
check("B: Evidence-Block kompakt (kein quellen-JSON)", "quellen_typen" not in block and "EvidenceQuelle" not in block)
check("B: leere Evidence -> leerer Block", format_evidence_for_prompt([]) == "")

# 1/2/3: die jeweils passenden Evidence-Typen existieren und sind referenzierbar
rk_id = next(i.id for i in insB if i.kategorie == "rueckruf")
mv_id = next(i.id for i in insB if i.kategorie == "marktvergleich")
mp_id = next(i.id for i in insB if i.kategorie == "motorproblem")
check("1: gültige Rückruf-ID als Empfehlungs-Evidence referenzierbar", filter_evidence_ids([rk_id], gueltig) == [rk_id])
check("2: gültige Marktvergleich-ID als Preis-Evidence referenzierbar", filter_evidence_ids([mv_id], gueltig) == [mv_id])
check("3: gültige Motorproblem-ID als Risiko-Evidence referenzierbar", filter_evidence_ids([mp_id], gueltig) == [mp_id])

# 4: Fake-ID wird backendseitig entfernt, gültige bleiben (Reihenfolge erhalten)
mit_fake = filter_evidence_ids([ids_all[0], "ev_fake_123", ids_all[-1]], gueltig, feld="test")
check("4: Fake-ID entfernt, gültige in Reihenfolge erhalten", mit_fake == [ids_all[0], ids_all[-1]])
check("4: nur existierende IDs im Ergebnis", all(x in gueltig for x in mit_fake))
check("4: Duplikate dedupliziert + non-str/None ignoriert",
      filter_evidence_ids([ids_all[0], ids_all[0], 42, None, "ev_fake"], gueltig) == [ids_all[0]])

# 5: keine passende / keine Evidence -> []
check("5: ohne existierende Evidence -> []", filter_evidence_ids(["irgendwas"], set()) == [])
check("5: LLM liefert nichts -> []", filter_evidence_ids(None, gueltig) == [])

# 6: Confidence/Provenance wird durch Schicht B NICHT verändert
conf_vorher = [i.confidence for i in insB]
_ = filter_evidence_ids(ids_all + ["ev_fake"], gueltig)
enrich_marktvergleich_spanne(insB, 23000, 27000)
check("6: Confidence unverändert nach Validierung + Anreicherung",
      [i.confidence for i in insB] == conf_vorher)
check("6: Anreicherung ergänzt NUR Marktvergleich-Beschreibung (Spanne), keine Confidence",
      any(i.kategorie == "marktvergleich" and "23000" in i.beschreibung for i in insB))

# Response-Modelle: neue *_evidence_ids-Felder additiv + Default []
kaufB = KaufCheckResponse(bericht="...", empfehlung="kaufen", preis_bewertung="guenstig",
                          quelle="gemischt", vertrauen="mittel", insights=insB,
                          empfehlung_evidence_ids=[ids_all[0]])
check("B: KaufCheckResponse neue Felder (gesetzt + default [])",
      kaufB.empfehlung_evidence_ids == [ids_all[0]] and kaufB.preis_evidence_ids == [] and kaufB.risiko_evidence_ids == [])
verkB = VerkaufsCheckResponse(bericht="...", quelle="web", vertrauen="niedrig")
check("B: VerkaufsCheckResponse neue Felder default []",
      verkB.preis_evidence_ids == [] and verkB.strategie_evidence_ids == [] and verkB.argument_evidence_ids == [])
check("9: altes ergebnis-Dict ohne evidence-Felder -> default []",
      KaufCheckResponse(**altes_ergebnis).empfehlung_evidence_ids == [])

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER: " + ", ".join(FEHLER))
    raise SystemExit(1)
print("Alle Evidence/Provenance-Tests bestanden.")
