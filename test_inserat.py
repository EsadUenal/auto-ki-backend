"""
Test: Phase 4 Inseratsanalyse & Fakten-Schutz (app/inserat) — deterministisch, kein LLM.

Deckt die geforderten Fälle ab:
  Analyse   1) TÜV fehlt  2) Vorbesitzer fehlt  3) Unfallstatus fehlt
            4) Scheckheft -> Vertrauensfaktor  5) starke Ausstattung -> Argument
            6) 'Leder' != 'LED' (Regression)  7) Widerspruch Kraftstoff
            8) Widerspruch Getriebe  9) Widerspruch Unfallstatus
           10) vollständiges Inserat nicht künstlich schlecht bewerten
  Fakten   11) Titel nur vorhandene Ausstattung  12) keine erfundene Unfallfreiheit
           13) kein erfundener TÜV  14) kein erfundenes Scheckheft
           15) kein erfundener Vorbesitzer  16) Mangel nicht verschwiegen
           17) belegte Fahrzeugdaten bleiben erhalten

Ausfuehren:  python test_inserat.py
"""
import os
import sys
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_ins_"), "test.db")
sys.path.insert(0, ".")

from app.inserat import (  # noqa: E402
    build_listing_analyse, finde_widersprueche, baue_titel_vorschlag, pruefe_fakten,
)
from app.models import VerkaufsCheckRequest, Insight, Marktanalyse  # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


def req(**kw) -> VerkaufsCheckRequest:
    return VerkaufsCheckRequest(**kw)


def fehlt(analyse, feld_teil):
    return any(feld_teil.lower() in f.feld.lower() for f in analyse.fehlende_angaben)


# ── Ausgangs-Basis: ein weitgehend vollständiges Mercedes-C200-Inserat ────────
VOLL = dict(
    marke="Mercedes-Benz", modell="C 200", baujahr=2019, kilometerstand=64300,
    motor="1.5 Benzin 184 PS", kraftstoff="Benzin", getriebe="Automatik", farbe="Obsidianschwarz",
    ausstattung=["LED", "Navigation", "Totwinkel-Assistent", "Park-Paket", "Sitzheizung", "Ambientebeleuchtung"],
    inserat_text="Sehr gepflegter Wagen aus zweiter Hand, Nichtraucher.",
    unfallfrei="ja", vorbesitzer=2, tuev_bis="06/2027", scheckheftgepflegt=True,
)

# ── 1–3) fehlende Angaben ─────────────────────────────────────────────────────
a_ohne_tuev = build_listing_analyse(req(**{**VOLL, "tuev_bis": None}), None, None, [])
check("1: TÜV fehlt -> erkannt", fehlt(a_ohne_tuev, "TÜV"))

a_ohne_vb = build_listing_analyse(req(**{**VOLL, "vorbesitzer": None}), None, None, [])
check("2: Vorbesitzer fehlt -> erkannt", fehlt(a_ohne_vb, "Vorbesitzer"))

a_ohne_uf = build_listing_analyse(req(**{**VOLL, "unfallfrei": None}), None, None, [])
check("3: Unfallstatus fehlt -> erkannt", fehlt(a_ohne_uf, "Unfall"))

# ── 4) Scheckheft -> Vertrauensfaktor ─────────────────────────────────────────
a_voll = build_listing_analyse(req(**VOLL), None, None, [])
check("4: Scheckheft -> Vertrauensfaktor (staerken)",
      any("scheckheft" in s.lower() for s in a_voll.staerken))
check("4b: Unfallfrei -> Vertrauensfaktor",
      any("unfallfrei" in s.lower() for s in a_voll.staerken))

# ── 5) starke Ausstattung -> Verkaufsargument ─────────────────────────────────
check("5: starke Ausstattung -> Verkaufsargument",
      any("led" in s.lower() for s in a_voll.verkaufsargumente))

# ── 6) 'Leder' darf NICHT 'LED' erkennen ──────────────────────────────────────
a_leder = build_listing_analyse(req(marke="BMW", modell="320d", baujahr=2020, kilometerstand=70000,
                                    ausstattung=["Lederausstattung"]), None, None, [])
check("6: 'Leder' -> 'Lederausstattung', NICHT 'LED-Scheinwerfer'",
      any("leder" in s.lower() for s in a_leder.verkaufsargumente)
      and not any("led-scheinwerfer" in s.lower() for s in a_leder.verkaufsargumente))

# ── 7) Widerspruch Kraftstoff ─────────────────────────────────────────────────
w7 = finde_widersprueche(req(marke="BMW", modell="320d", baujahr=2020, kilometerstand=70000,
                             kraftstoff="Benzin", inserat_text="Sparsamer Diesel, ideal für Vielfahrer."))
check("7: Beschreibung 'Diesel' vs. Kraftstoff Benzin -> Widerspruch", bool(w7))

# ── 8) Widerspruch Getriebe ───────────────────────────────────────────────────
w8 = finde_widersprueche(req(marke="VW", modell="Golf", baujahr=2019, kilometerstand=80000,
                             getriebe="Automatik", inserat_text="Sauberes 6-Gang Schaltgetriebe."))
check("8: Beschreibung 'Schaltgetriebe' vs. getriebe Automatik -> Widerspruch", bool(w8))

# ── 9) Widerspruch Unfallstatus ───────────────────────────────────────────────
w9 = finde_widersprueche(req(marke="Audi", modell="A4", baujahr=2018, kilometerstand=90000,
                             unfallfrei="nein", inserat_text="Fahrzeug ist 100% unfallfrei."))
check("9: Beschreibung 'unfallfrei' vs. Unfallschaden -> Widerspruch", bool(w9))
a9 = build_listing_analyse(req(marke="Audi", modell="A4", baujahr=2018, kilometerstand=90000,
                               unfallfrei="nein", inserat_text="Fahrzeug ist 100% unfallfrei."), None, None, [])
check("9b: Widerspruch landet in analyse.probleme", bool(a9.probleme))

# ── 10) vollständiges gutes Inserat -> nicht künstlich schlecht ───────────────
check("10: vollständiges Inserat -> Qualität 'sehr_gut'/'gut'",
      a_voll.qualitaet in ("sehr_gut", "gut"))
check("10b: vollständiges Inserat -> keine Widersprüche", not a_voll.probleme)
check("10c: Vollständigkeit hoch (>= 9 von 11)", a_voll.vorhanden >= 9)

# ── Titelvorschlag ────────────────────────────────────────────────────────────
titel = baue_titel_vorschlag(req(**VOLL), None, None) or ""
check("T: Titelvorschlag enthält Marke+Modell", "Mercedes" in titel and "C 200" in titel)
check("T2: Titelvorschlag erfindet keine fremde Ausstattung (kein Panorama)",
      "panorama" not in titel.lower())

# ══ FAKTEN-SCHUTZ (pruefe_fakten) ════════════════════════════════════════════

R_MIN = req(marke="BMW", modell="320d", baujahr=2020, kilometerstand=70000,
            motor="2.0 Diesel", kraftstoff="Diesel", getriebe="Automatik",
            ausstattung=["Navigation"])   # KEIN unfallfrei/scheckheft/tüv/vorbesitzer

# 11) Titel nur vorhandene Ausstattung
t11, _, ent11 = pruefe_fakten("BMW 320d | Panorama | LED | Navigation", "Solides Fahrzeug.", R_MIN)
check("11: erfundenes 'Panorama' aus Titel entfernt", "panorama" not in t11.lower())
check("11b: erfundenes 'LED' aus Titel entfernt (nicht angegeben)", " led" not in (" " + t11.lower()))
check("11c: angegebenes 'Navigation' bleibt im Titel", "navigation" in t11.lower())

# 12) keine erfundene Unfallfreiheit
_, b12, ent12 = pruefe_fakten("BMW 320d", "Das Fahrzeug ist unfallfrei. Sehr gepflegt.", R_MIN)
check("12: erfundene Unfallfreiheit entfernt", "unfallfrei" not in b12.lower())
check("12b: als entfernte Behauptung protokolliert", any("Unfall" in e for e in ent12))

# 13) kein erfundener TÜV
_, b13, _ = pruefe_fakten("BMW 320d", "TÜV neu bis 12/2026. Guter Zustand.", R_MIN)
check("13: erfundener TÜV entfernt (kein tuev_bis angegeben)", "tüv" not in b13.lower() and "tuv" not in b13.lower())

# 14) kein erfundenes Scheckheft
_, b14, _ = pruefe_fakten("BMW 320d", "Scheckheftgepflegt und lückenlose Servicehistorie.", R_MIN)
check("14: erfundenes Scheckheft entfernt", "scheckheft" not in b14.lower())

# 15) kein erfundener Vorbesitzer
_, b15, _ = pruefe_fakten("BMW 320d", "Aus erster Hand. Toller Wagen.", R_MIN)
check("15: erfundene 'erste Hand' entfernt (Vorbesitzer nicht 1)", "erste hand" not in b15.lower())

# 16) bekannter Mangel nicht verschwiegen
R_MANGEL = req(marke="BMW", modell="320d", baujahr=2020, kilometerstand=70000,
               maengel=["Steuerkette macht Geräusche", "kleiner Kratzer Heckstoßstange"])
_, b16, _ = pruefe_fakten("BMW 320d", "Sehr schönes Fahrzeug in Top-Zustand.", R_MANGEL)
check("16: bekannte Mängel erscheinen ehrlich in der Beschreibung",
      "bekannte mängel" in b16.lower() and "steuerkette" in b16.lower())

# 17) belegte Fahrzeugdaten/Angaben bleiben erhalten
R_BELEGT = req(marke="Mercedes-Benz", modell="C 200", baujahr=2019, kilometerstand=64300,
               kraftstoff="Benzin", getriebe="Automatik", ausstattung=["LED", "Navigation"],
               unfallfrei="ja", vorbesitzer=1, tuev_bis="06/2027", scheckheftgepflegt=True)
t17, b17, ent17 = pruefe_fakten(
    "Mercedes-Benz C 200 | LED | Navigation | Scheckheft",
    "Unfallfrei und scheckheftgepflegt. TÜV bis 06/2027. Aus erster Hand. Mit LED-Scheinwerfern.",
    R_BELEGT)
check("17: belegte Unfallfreiheit bleibt erhalten", "unfallfrei" in b17.lower())
check("17b: belegtes Scheckheft bleibt erhalten", "scheckheft" in b17.lower())
check("17c: belegter TÜV-Termin bleibt erhalten", "06/2027" in b17)
check("17d: belegte 'erste Hand' bleibt (vorbesitzer==1)", "erster hand" in b17.lower())
check("17e: belegte LED-Ausstattung bleibt", "led" in b17.lower())
check("17f: nichts fälschlich entfernt", ent17 == [])

# ── Zusatz: Marktanalyse-Preis-Hinweis ───────────────────────────────────────
ma = Marktanalyse(gefunden=10, verwendet=7, median_eur=26000, spanne_min_eur=24000,
                  spanne_max_eur=28000, angebot_eur=30000, differenz_eur=4000, differenz_pct=15.4,
                  datenqualitaet="mittel")
mv = Insight(id="marktvergleich-3", kategorie="marktvergleich", titel="Marktvergleich",
             beschreibung="…", confidence="mittel", marktanalyse=ma)
a_preis = build_listing_analyse(req(**{**VOLL, "preis_vorstellung": 30000}), None, None, [mv])
check("P: Preis über Median -> preis_hinweis + Evidence-ID",
      bool(a_preis.preis_hinweis) and a_preis.evidence_ids == ["marktvergleich-3"])


print()
if FEHLER:
    print(f"FEHLGESCHLAGEN ({len(FEHLER)}): " + "; ".join(FEHLER))
    sys.exit(1)
print("Alle Inserats-Tests bestanden.")
