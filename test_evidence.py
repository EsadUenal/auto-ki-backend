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


BAUREIHE = {
    "id": "bmw-3er-g20",
    "quellen": [{"quelle": "BMW-Servicedoku", "url": "https://example.com/bmw", "abrufdatum": "2026-01-01"}],
    "schwachstellen_baureihe": [
        {"bauteil": "AGR-Kühler", "beschreibung": "AGR-System kann verrußen.",
         "betroffene_baujahre": "2019-2021", "schweregrad": "hoch"},
        {"bauteil": "Steuerkette (früh)", "beschreibung": "Nur frühe Baujahre betroffen.",
         "betroffene_baujahre": "2012-2015", "schweregrad": "hoch"},
    ],
    "rueckrufe": [
        {"datum": "2020-05", "betroffene_baujahre": "2020", "mangel": "Bremskraftverstärker",
         "abhilfe": "Software-Update", "kba_referenz": "KBA-011234"},
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
check("2: KBA-Referenz als ref hinterlegt", bool(rk) and any(q.ref == "KBA-011234" for q in rk[0].quellen))
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

# ── 3) Webbasierter Marktpreis -> Web/Marktvergleich ────────────────────────
mv = [i for i in ins if i.kategorie == "marktvergleich"]
check("3: Marktvergleich-Insight vorhanden", len(mv) == 1)
check("3: quellen_typen web + marktvergleich",
      bool(mv) and "web" in mv[0].quellen_typen and "marktvergleich" in mv[0].quellen_typen)
check("3: Web-URL vorhanden", bool(mv) and any(q.url for q in mv[0].quellen))
# Punkt 3: Confidence NICHT allein aus Trefferzahl. Vergleichbarkeit ist aus der
# Web-Struktur (Titel+Snippet) nicht belegbar -> mehrere Marktplatzquellen = max. "mittel".
check("3: mehrere Marktplatz-Quellen -> maximal 'mittel' (nie 'hoch' aus Anzahl)",
      bool(mv) and mv[0].confidence == "mittel")
BELEGE_VIER = BELEGE_STARK + [{"typ": "web", "titel": "4. Angebot",
                               "url": "https://autoscout24.de/4", "qualitaet": "Marktplatz"}]
mv4 = [i for i in build_insights(None, None, BELEGE_VIER, req(2020)) if i.kategorie == "marktvergleich"]
check("3: 4 Marktplatz-URLs bedeuten NICHT automatisch 'hoch'", bool(mv4) and mv4[0].confidence != "hoch")
mv1 = [i for i in build_insights(None, None, [BELEGE_STARK[0]], req(2020)) if i.kategorie == "marktvergleich"]
check("3: einzelne Marktplatz-Quelle -> 'niedrig'", bool(mv1) and mv1[0].confidence == "niedrig")

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

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER: " + ", ".join(FEHLER))
    raise SystemExit(1)
print("Alle Evidence/Provenance-Tests bestanden.")
