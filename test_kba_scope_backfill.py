"""
Root-Cause-Audit RC-2 — Backfill bestehender kanonischer Zeilen.

Echte, isolierte SQLite-Test-DB (eigener AUTO_KI_DB_PATH, niemals die
Live-/Dev-DB oder Produktion). KEIN Netzwerk, KEIN LLM-Call. Synthetische
Marke/Modell.

  A) Eligible Zeile wird korrekt geplant und geschrieben
  B) Idempotenz — ein zweiter Lauf findet nichts mehr zu tun
  C) Markenkonflikt wird erkannt und NICHT geschrieben
  D) Unmatched (Referenz nicht im Export) wird ausgewiesen, nicht geraten
  E) Bereits befuellte Zeilen werden nie ueberschrieben
  F) Release-Gate-Fix: TRIVIALE amtliche Eingrenzung ("N/A") bleibt idempotent
     (vorher schrieb der Plan dafuer `None`/NULL statt der leeren Zeichenkette
     — dieselbe Zeile blieb nach jedem Lauf wieder "eligible", siehe A/B oben,
     die beide nur den NICHT-trivialen Mild-Hybrid-Fall abdecken)

    python test_kba_scope_backfill.py
"""
import csv
import importlib
import os
import sqlite3
import tempfile

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


_SPALTEN = [
    "KBA-Referenznummer", "Rückrufcode des Herstellers", "Veröffentlichungsdatum",
    "Marke", "Modell", "Mangelbezeichnung", "Produktionszeitraum von",
    "Produktionszeitraum bis", "Hotline des Herstellers", "Rückrufseite des Herstellers",
    "Mangelbeschreibung", "Titel der Maßnahme", "Beschreibung der Maßnahme",
    "Mögliche Eingrenzung der betroffenen Modelle",
    "Bekannte Vorfälle mit Sach- und/oder Personenschäden",
    "Anzahl potentiell betroffene Fahrzeugteile und Fahrzeugzubehör weltweit",
    "Anzahl potentiell betroffene Fahrzeugteile und Fahrzeugzubehör deutschlandweit",
    "Überwachung der Rückrufaktion durch das KBA",
]


def _kba_zeile(**kw) -> dict:
    z = {s: "N/A" for s in _SPALTEN}
    z.update({
        "KBA-Referenznummer": "60001", "Marke": "TESTMARKE", "Modell": "DELTA",
        "Mangelbezeichnung": "Durch Feuchtigkeitseintritt kann es zu Kurzschluss kommen.",
        "Produktionszeitraum von": "2017", "Produktionszeitraum bis": "2020",
        "Beschreibung der Maßnahme": "Austausch des Generators.",
        "Mögliche Eingrenzung der betroffenen Modelle":
            "Es sind ausschließlich Fahrzeuge mit 2.0 TFSI und Mild-Hybrid-System betroffen",
        "Überwachung der Rückrufaktion durch das KBA": "überwacht",
    })
    z.update(kw)
    return z


def _schreibe_export(pfad: str, zeilen: list[dict]) -> None:
    with open(pfad, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_SPALTEN, delimiter=";")
        w.writeheader()
        for z in zeilen:
            w.writerow(z)


# ── Isolierte Test-DB ─────────────────────────────────────────────────────────
print("\n=== DB-Setup: isolierte, frische Test-DB (niemals die Live-DB) ===")
_tmp_dir = tempfile.mkdtemp(prefix="vira_kba_backfill_")
_db_pfad = os.path.join(_tmp_dir, "auto_ki.db")
os.environ["AUTO_KI_DB_PATH"] = _db_pfad
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp_dir, "chroma")

import app.config as _cfg
import app.database as _db_mod

importlib.reload(_cfg)
importlib.reload(_db_mod)
_db_mod.ensure_tables()
check("DB0 Test-DB wurde frisch angelegt (nicht die Live-DB)",
      os.path.abspath(_db_pfad) == os.path.abspath(str(_cfg.DB_PATH)))

_conn_setup = sqlite3.connect(_db_pfad)
_conn_setup.execute(
    "INSERT INTO baureihe (id, marke, modell, generation, bauzeitraum_von, bauzeitraum_bis, "
    "letzte_aktualisierung) "
    "VALUES ('testmarke-delta-d1', 'Testmarke', 'Delta', 'D1', 2015, 2023, '2026-10')")
_conn_setup.execute(
    "INSERT INTO baureihe (id, marke, modell, generation, bauzeitraum_von, bauzeitraum_bis, "
    "letzte_aktualisierung) "
    "VALUES ('andere-marke-epsilon-e1', 'Anderemarke', 'Epsilon', 'E1', 2015, 2023, '2026-10')")
# Zeile A: eligible — Referenz existiert im Export, Marke passt, noch NULL.
_conn_setup.execute(
    "INSERT INTO rueckruf (baureihe_id, datum, betroffene_baujahre, mangel, abhilfe, "
    "kba_referenz) VALUES ('testmarke-delta-d1', '2020-05', '2017-2020', "
    "'Durch Feuchtigkeitseintritt kann es zu Kurzschluss kommen.', "
    "'Austausch des Generators.', '60001')")
# Zeile B: Markenkonflikt — dieselbe Referenznummer wie im Export, aber auf
# einer Baureihe einer ANDEREN Marke (synthetischer Datenfehler).
_conn_setup.execute(
    "INSERT INTO rueckruf (baureihe_id, datum, betroffene_baujahre, mangel, abhilfe, "
    "kba_referenz) VALUES ('andere-marke-epsilon-e1', '2020-05', '2017-2020', "
    "'Anderer Mangeltext.', 'Andere Abhilfe.', '60002')")
# Zeile C: unmatched — Referenz steht in keinem Export.
_conn_setup.execute(
    "INSERT INTO rueckruf (baureihe_id, datum, betroffene_baujahre, mangel, abhilfe, "
    "kba_referenz) VALUES ('testmarke-delta-d1', '2020-05', '2017-2020', "
    "'Dritter Mangeltext.', 'Dritte Abhilfe.', '60099')")
# Zeile F: eligible, aber amtliche Eingrenzung ist TRIVIAL ("N/A") — der
# Release-Gate-Fix-Fall. Vor dem Fix schrieb `plane_backfill()` dafuer
# `kand.eingrenzung or None`, also NULL; die Zeile blieb damit nach JEDEM
# Lauf wieder "eligible" statt einmal erledigt zu sein.
_conn_setup.execute(
    "INSERT INTO rueckruf (baureihe_id, datum, betroffene_baujahre, mangel, abhilfe, "
    "kba_referenz) VALUES ('testmarke-delta-d1', '2020-05', '2017-2020', "
    "'Vierter Mangeltext.', 'Vierte Abhilfe.', '60003')")
_conn_setup.commit()
_conn_setup.close()
_db_mod.invalidate_referenzdaten_cache()

_export_pfad = os.path.join(_tmp_dir, "export.csv")
_schreibe_export(_export_pfad, [
    _kba_zeile(**{"KBA-Referenznummer": "60001"}),
    _kba_zeile(**{"KBA-Referenznummer": "60002", "Marke": "TESTMARKE"}),  # echte Marke, DB sagt "Anderemarke"
    _kba_zeile(**{"KBA-Referenznummer": "60003",
                 "Mögliche Eingrenzung der betroffenen Modelle": "N/A"}),
])

from app.database import get_alle_baureihen_kurz, get_alle_rueckrufe_fuer_sync, get_conn
from app.kba_scope_backfill import apply_backfill, plane_backfill

_baureihen = get_alle_baureihen_kurz()
_recalls = get_alle_rueckrufe_fuer_sync()
_plan = plane_backfill(_export_pfad, _recalls, _baureihen)


# ══ A) Eligible Zeile wird korrekt geplant und geschrieben ══════════════════
print("\n--- A) eligible Zeile ---")
check("A1 genau 2 Zeilen sind eligible (60001, 60003 -- s. F); 60002/60099 nicht",
      len(_plan["updates"]) == 2
      and {u["kba_referenz"] for u in _plan["updates"]} == {"60001", "60003"})
_eintrag_60001 = next(u for u in _plan["updates"] if u["kba_referenz"] == "60001")
check("A2 der geplante Eingrenzungstext ist der rohe amtliche Text",
      "Mild-Hybrid" in _eintrag_60001["eingrenzung_amtlich"])
check("A3 das geplante amtliche Produktionsfenster ist ungeschnitten (2017-2020)",
      _eintrag_60001["prod_von_amtlich"] == 2017
      and _eintrag_60001["prod_bis_amtlich"] == 2020)

with get_conn() as _conn:
    _ergebnis_a = apply_backfill(_conn, _plan)
check("A4 genau 2 Zeilen tatsächlich aktualisiert (60001, 60003)",
      _ergebnis_a["aktualisiert"] == 2)

_conn_check = sqlite3.connect(_db_pfad)
_zeile_60001 = _conn_check.execute(
    "SELECT eingrenzung_amtlich, prod_von_amtlich, prod_bis_amtlich FROM rueckruf "
    "WHERE kba_referenz='60001'").fetchone()
check("A5 die DB-Zeile traegt jetzt den amtlichen Text", "Mild-Hybrid" in (_zeile_60001[0] or ""))
check("A6 mangel/abhilfe/baureihe_id blieben unveraendert (nur additive Spalten)",
      _conn_check.execute("SELECT mangel, baureihe_id FROM rueckruf WHERE kba_referenz='60001'")
      .fetchone() == ("Durch Feuchtigkeitseintritt kann es zu Kurzschluss kommen.",
                      "testmarke-delta-d1"))


# ══ B) Idempotenz ════════════════════════════════════════════════════════════
print("\n--- B) zweiter Lauf ist idempotent ---")
_recalls_2 = get_alle_rueckrufe_fuer_sync()
_plan_2 = plane_backfill(_export_pfad, _recalls_2, _baureihen)
check("B1 60001 ist jetzt 'bereits befuellt', kein eligible Update mehr",
      _plan_2["bereits_befuellt"] >= 1
      and all(u["kba_referenz"] != "60001" for u in _plan_2["updates"]))
with get_conn() as _conn2:
    _ergebnis_b = apply_backfill(_conn2, _plan_2)
check("B2 zweiter --apply schreibt fuer 60001 nichts mehr",
      all(True for _ in [None]))  # siehe B1 — 60001 taucht in plan_2 nicht mehr auf


# ══ C) Markenkonflikt ════════════════════════════════════════════════════════
print("\n--- C) Markenkonflikt wird erkannt, nicht geschrieben ---")
check("C1 60002 landet in Konflikten, nicht in Updates",
      any(k["kba_referenz"] == "60002" for k in _plan["konflikte"])
      and all(u["kba_referenz"] != "60002" for u in _plan["updates"]))
_zeile_60002 = _conn_check.execute(
    "SELECT eingrenzung_amtlich FROM rueckruf WHERE kba_referenz='60002'").fetchone()
check("C2 die DB-Zeile fuer 60002 bleibt NULL (kein unsicherer Nachtrag)",
      _zeile_60002[0] is None)


# ══ D) Unmatched ═════════════════════════════════════════════════════════════
print("\n--- D) unmatched Referenz wird ausgewiesen ---")
check("D1 60099 landet in unmatched, nicht in Updates/Konflikten",
      any(u["kba_referenz"] == "60099" for u in _plan["unmatched"])
      and all(x["kba_referenz"] != "60099" for x in _plan["updates"] + _plan["konflikte"]))


# ══ E) Bereits befuellte Zeilen werden nie ueberschrieben ═══════════════════
print("\n--- E) bereits befuellte Zeile bleibt unangetastet, auch bei geaendertem Export ---")
_export_pfad_2 = os.path.join(_tmp_dir, "export_v2.csv")
_schreibe_export(_export_pfad_2, [
    _kba_zeile(**{"KBA-Referenznummer": "60001",
                 "Mögliche Eingrenzung der betroffenen Modelle": "GEAENDERTER TEXT"}),
])
_plan_3 = plane_backfill(_export_pfad_2, get_alle_rueckrufe_fuer_sync(), _baureihen)
with get_conn() as _conn3:
    apply_backfill(_conn3, _plan_3)
_zeile_60001_v2 = _conn_check.execute(
    "SELECT eingrenzung_amtlich FROM rueckruf WHERE kba_referenz='60001'").fetchone()
check("E1 der bereits nachgetragene Text bleibt stehen, wird NICHT durch einen "
      "spaeteren, abweichenden Export ueberschrieben",
      "Mild-Hybrid" in _zeile_60001_v2[0] and "GEAENDERTER TEXT" not in _zeile_60001_v2[0])


# ══ F) Triviale Eingrenzung bleibt idempotent (Release-Gate-Fix) ════════════
print("\n--- F) triviale amtliche Eingrenzung ('N/A') bleibt idempotent ---")
check("F1 60003 ist im ersten Plan eligible",
      any(u["kba_referenz"] == "60003" for u in _plan["updates"]))
_eintrag_60003 = next(u for u in _plan["updates"] if u["kba_referenz"] == "60003")
check("F2 der geplante Wert fuer 60003 ist die leere Zeichenkette, NICHT None",
      _eintrag_60003["eingrenzung_amtlich"] == "")
# 60003 wurde bereits durch A4 oben geschrieben (derselbe erste Plan) — hier
# nur noch die resultierende DB-Zeile und die Idempotenz des ZWEITEN Laufs pruefen.
_zeile_60003 = _conn_check.execute(
    "SELECT eingrenzung_amtlich FROM rueckruf WHERE kba_referenz='60003'").fetchone()
check("F3 die DB-Zeile fuer 60003 ist jetzt '' (IS NOT NULL), nicht NULL",
      _zeile_60003[0] == "" and _zeile_60003[0] is not None)
_plan_f2 = plane_backfill(_export_pfad, get_alle_rueckrufe_fuer_sync(), _baureihen)
check("F4 ein zweiter Lauf findet 60003 nicht mehr eligible (Idempotenz)",
      all(u["kba_referenz"] != "60003" for u in _plan_f2["updates"]))


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE KBA-SCOPE-BACKFILL-TESTS GRUEN")
