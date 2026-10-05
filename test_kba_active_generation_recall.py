"""
ACTIVE-GENERATION RECALL ADMISSION — Zusicherungen (Release-Hardening,
Production Smoke BMW 330i G20 2020).

Beweist generisch (KEIN Marken-/Modell-/KBA-Referenz-Hardcoding):

  1) geschlossene Generation, eindeutiger Rueckruf -> unveraendertes Verhalten
  2) offene/aktuelle Generation, Fenster passt, eindeutig -> wird uebernommen
  3) offene Generation + konkurrierende plausible Generation -> REVIEW_REQUIRED
  4) offene Generation, amtliches Fenster vor Generationsstart -> NICHT zugewiesen
  5) Variantenbeschraenkung nicht abbildbar -> REVIEW_REQUIRED
  6) markenuebergreifende Referenzkollision -> kein Auto-Import
  7) Dublette bei wiederholtem Sync -> keine doppelte Zeile
  8) amtliche Referenz ueberlebt DB -> Verifikation -> Anzeige-Gate
  9) Baureihen-Rueckruf bleibt FIN-abhaengig (konservative Formulierung unveraendert)
 10) Reihenfolge-Unabhaengigkeit
 11) aufloesbare Mehrdeutigkeit (ein Ziel sicher, ein ANDERES Ziel desselben
     mehrmodelligen Datensatzes mehrdeutig) -> das sichere Paar wird uebernommen
 12) nicht aufloesbare Mehrdeutigkeit (dasselbe Fixture, das ANDERE Paar) -> Review
 13) ein an Tor A0-A5/A6 abgelehnter SAFE_IMPORT-Kandidat bleibt im Review-/Audit-
     Bestand sichtbar (kein stilles Verschwinden)

BMW-Regressionsfixtur (NACH den generischen Tests, s. Abschnitt BMW):
 reproduziert die Production-Diagnose (bmw-3er-g20-g21 offen ab 2019,
 10807/12198-aehnliches sicheres offenes Paar, 15630R-aehnliches
 Vor-Generationsstart-Fenster, 16131R/16790R-aehnlicher mehrmodelliger
 Mehrdeutigkeits-Drag-down) MIT SYNTHETISCHEN Referenzen -- keine fest
 erwartete Rueckrufanzahl, nur die STRUKTURELLE Erwartung aus der Diagnose.

    python test_kba_active_generation_recall.py
"""
import importlib
import os
import sqlite3
import tempfile

from app.kba_active_generation import ergaenzende_zeilen, paare_aktive_generation
from app.kba_import_batch_a import klasse_a, pruefe_batch_a
from app.kba_import_kandidaten import (
    AMBIGUOUS_GENERATION, SAFE_IMPORT, VARIANT_SCOPE_UNCLEAR, import_kandidaten,
)
from app.kba_reconciliation import normalisiere_referenz

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def kba_zeile(**kw) -> dict:
    z = {
        "KBA-Referenznummer": "90001", "Rückrufcode des Herstellers": "ABC",
        "Veröffentlichungsdatum": "2026-01-01", "Marke": "TESTAKTIV",
        "Modell": "3",
        "Mangelbezeichnung": "Die Bremse kann durch einen Fertigungsfehler ausfallen "
                              "und zu einem Unfall führen.",
        "Produktionszeitraum von": "2020", "Produktionszeitraum bis": "2021",
        "Beschreibung der Maßnahme": "Prüfung und ggf. Austausch der Bremse.",
        "Mögliche Eingrenzung der betroffenen Modelle": "N/A",
        "Überwachung der Rückrufaktion durch das KBA": "überwacht",
    }
    z.update(kw)
    return z


def baureihe(bid, marke="Testaktiv", modell="3er", von=2019, bis=None) -> dict:
    return {"id": bid, "marke": marke, "modell": modell, "generation": "Gen",
           "bauzeitraum_von": von, "bauzeitraum_bis": bis}


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 1) Geschlossene Generation, eindeutig -> unveraendertes Verhalten ===")
_b1 = [baureihe("ta-3er-closed", von=2015, bis=2022)]
_k1 = import_kandidaten([kba_zeile(**{"KBA-Referenznummer": "10001"})], [], _b1)
_zeilen1, _ = pruefe_batch_a(_k1, _b1, [])
_zusatz1, _ = ergaenzende_zeilen(_k1, _b1, [])
check("1.1 geschlossene Generation weiterhin ueber Batch A importiert",
      any(z["kba_referenz"] == "10001" and z["baureihe_id"] == "ta-3er-closed"
          for z in _zeilen1))
check("1.2 kein Zusatzpfad-Eingriff bei bereits durch Batch A abgedeckten Paaren",
      _zusatz1 == [])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 2) Offene Generation, Fenster passt, eindeutig -> wird uebernommen ===")
_b2 = [baureihe("ta-3er-open", von=2019, bis=None)]
_k2 = import_kandidaten([kba_zeile(**{"KBA-Referenznummer": "10002",
                                     "Produktionszeitraum von": "2020",
                                     "Produktionszeitraum bis": "2021"})], [], _b2)
check("2.1 Basisklassifikation SAFE_IMPORT (Baureihe eindeutig, Zeitraum passt)",
      _k2 and _k2[0].klasse == SAFE_IMPORT)
check("2.2 klasse_a() verwirft weiterhin JEDE offene Generation (Batch A unveraendert)",
      klasse_a(_k2, _b2) == [])
_zeilen2a, _ = pruefe_batch_a(_k2, _b2, [])
check("2.3 Batch A selbst erzeugt KEINE Zeile (unveraendert)", _zeilen2a == [])
_zusatz2, _aus2 = ergaenzende_zeilen(_k2, _b2, [])
check("2.4 Zusatzpfad uebernimmt die offene, zeitlich passende Generation",
      any(z["kba_referenz"] == "10002" and z["baureihe_id"] == "ta-3er-open"
          for z in _zusatz2))
check("2.5 keine Ausschluesse fuer dieses eindeutige Paar", _aus2 == [])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 3) Offene Generation + konkurrierende plausible Generation -> REVIEW ===")
_b3 = [baureihe("ta-3er-open-c", von=2019, bis=None),
      baureihe("ta-3er-konkurrenz", von=2017, bis=2020)]
_k3 = import_kandidaten([kba_zeile(**{"KBA-Referenznummer": "10003",
                                     "Produktionszeitraum von": "2019",
                                     "Produktionszeitraum bis": "2021"})], [], _b3)
check("3.1 beide Baureihen gelten als Ziel (Ueberdeckung >= 2/3 bei beiden)",
      _k3 and sorted(_k3[0].ziel_ids) == ["ta-3er-konkurrenz", "ta-3er-open-c"])
check("3.2 kandidatenweit AMBIGUOUS_GENERATION (keine der beiden exklusiv erreichbar)",
      _k3 and _k3[0].klasse == AMBIGUOUS_GENERATION)
_zusatz3, _ = ergaenzende_zeilen(_k3, _b3, [])
check("3.3 Zusatzpfad uebernimmt NICHTS (echte Mehrdeutigkeit bleibt REVIEW_REQUIRED)",
      _zusatz3 == [])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 4) Offene Generation, amtliches Fenster VOR Generationsstart -> NICHT zugewiesen ===")
_b4 = [baureihe("ta-modz-vorgaenger", von=2011, bis=2018),
      baureihe("ta-modz-open", von=2019, bis=None)]
_k4 = import_kandidaten([kba_zeile(**{"KBA-Referenznummer": "10004",
                                     "Produktionszeitraum von": "2018",
                                     "Produktionszeitraum bis": "2025"})], [], _b4)
check("4.1 einziges Ziel ist die offene Generation (Vorgaenger faellt durch Randlage)",
      _k4 and _k4[0].ziel_ids == ["ta-modz-open"] and _k4[0].klasse == SAFE_IMPORT)
_zusatz4, _aus4 = ergaenzende_zeilen(_k4, _b4, [])
check("4.2 amtliches Fenster beginnt VOR dem Generationsstart (2018 < 2019) -> "
      "KEINE automatische Uebernahme", _zusatz4 == [])
check("4.3 Kandidat erreicht noch nicht einmal die Vorauswahl (Tor A6 vorgelagert)",
      _k4[0] not in paare_aktive_generation(_k4, _b4))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 5) Variantenbeschraenkung nicht abbildbar -> REVIEW_REQUIRED ===")
_b5 = [baureihe("ta-3er-variant", von=2019, bis=None)]
_k5 = import_kandidaten([kba_zeile(**{
    "KBA-Referenznummer": "10005",
    "Mögliche Eingrenzung der betroffenen Modelle": "Nur Fahrzeuge mit Allradantrieb (xDrive)",
})], [], _b5)
check("5.1 Variantenbeschraenkung erkannt, nicht ueber Kraftstoff abbildbar",
      _k5 and _k5[0].klasse == VARIANT_SCOPE_UNCLEAR)
_zusatz5, _ = ergaenzende_zeilen(_k5, _b5, [])
check("5.2 Zusatzpfad uebernimmt KEINE variantenbeschraenkte Zeile", _zusatz5 == [])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 6) Markenuebergreifende Referenzkollision -> kein Auto-Import ===")
_b6 = [baureihe("ta-3er-kollision", von=2019, bis=None)]
_recalls6 = [{"id": 1, "baureihe_id": "fremd-marke-modell", "marke": "Fremdmarke",
             "kba_referenz": "10006", "mangel": "etwas anderes",
             "betroffene_baujahre": "2019"}]
_k6 = import_kandidaten([kba_zeile(**{"KBA-Referenznummer": "10006"})], [], _b6)
check("6.1 Basisklassifikation weiterhin SAFE_IMPORT (Kollision ist ein Batch-A-Tor, "
      "keine Basisklasse)", _k6 and _k6[0].klasse == SAFE_IMPORT)
_zusatz6, _aus6 = ergaenzende_zeilen(_k6, _b6, _recalls6)
check("6.2 markenuebergreifende Referenzkollision (A4) blockiert die Uebernahme",
      _zusatz6 == [] and any("A4" in a[-1] for a in _aus6))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 7) Dublette bei wiederholtem Sync -> keine doppelte Zeile ===")
_b7 = [baureihe("ta-3er-idempotent", von=2019, bis=None)]
_k7a = import_kandidaten([kba_zeile(**{"KBA-Referenznummer": "10007"})], [], _b7)
_zusatz7a, _ = ergaenzende_zeilen(_k7a, _b7, [])
check("7.1 erster Lauf: genau eine Zeile", len(_zusatz7a) == 1)
_recalls7b = [{"id": 1, "baureihe_id": "ta-3er-idempotent", "marke": "Testaktiv",
              "kba_referenz": "10007", "mangel": _zusatz7a[0]["mangel"],
              "betroffene_baujahre": "2020-2021"}]
_k7b = import_kandidaten([kba_zeile(**{"KBA-Referenznummer": "10007"})], _recalls7b, _b7)
check("7.2 zweiter Lauf: Referenz ist jetzt im Bestand -> kein Kandidat mehr",
      _k7b == [])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 8) Amtliche Referenz ueberlebt DB -> Verifikation -> Anzeige-Gate ===")
import app.config as _cfg
import app.database as _db_mod
from app.fakt_verifikation import fingerprint, trust_des_fakts, STATUS_VERIFIED
from app.recall_filter import kba_referenz_anzeige

_tmp_dir = tempfile.mkdtemp(prefix="vira_active_gen_")
_db_pfad = os.path.join(_tmp_dir, "auto_ki.db")
_alt_db_env = os.environ.get("AUTO_KI_DB_PATH")
_alt_chroma_env = os.environ.get("AUTO_KI_CHROMA_PATH")
os.environ["AUTO_KI_DB_PATH"] = _db_pfad
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp_dir, "chroma")
importlib.reload(_cfg)
importlib.reload(_db_mod)
_db_mod.ensure_tables()

_conn_setup = sqlite3.connect(_db_pfad)
_conn_setup.execute(
    "INSERT INTO baureihe (id, marke, modell, generation, bauzeitraum_von, bauzeitraum_bis, "
    "letzte_aktualisierung) "
    "VALUES ('ta-3er-db-open', 'Testaktiv', '3er', 'Gen2', 2019, NULL, '2026-10')")
_conn_setup.commit()
_conn_setup.close()
_db_mod.invalidate_referenzdaten_cache()

import app.kba_recall_refresh as _refresh_mod

_plan8 = _refresh_mod.plane_sync([kba_zeile(**{
    "KBA-Referenznummer": "10008", "Marke": "TESTAKTIV",
    "Produktionszeitraum von": "2020", "Produktionszeitraum bis": "2021",
})])
check("8.1 die offene Generation erscheint in neue_zeilen (Zusatzpfad gegriffen)",
      any(z["kba_referenz"] == "10008" for z in _plan8["neue_zeilen"]))
with _db_mod.get_conn() as _conn8:
    _erg8 = _refresh_mod.apply_sync(_conn8, _plan8, heute="2026-10-05")
_db_mod.invalidate_referenzdaten_cache()
check("8.2 genau 1 neue Zeile eingefuegt", _erg8["eingefuegt"] == 1)

_conn_check8 = sqlite3.connect(_db_pfad)
_conn_check8.row_factory = sqlite3.Row
_zeile8 = _conn_check8.execute(
    "SELECT * FROM rueckruf WHERE baureihe_id='ta-3er-db-open'").fetchone()
check("8.3 Zeile existiert mit der amtlichen Referenz", _zeile8 is not None
      and _zeile8["kba_referenz"] == "10008")
_fv8 = _conn_check8.execute(
    "SELECT * FROM fakt_verifikation WHERE fakt_art='rueckruf' AND fakt_id=?",
    (_zeile8["id"],)).fetchone()
check("8.4 fakt_verifikation wurde geschrieben, Status 'verified'",
      _fv8 is not None and _fv8["status"] == STATUS_VERIFIED)
check("8.5 gespeicherter Fingerprint passt zum TATSAECHLICHEN DB-Inhalt",
      _fv8 is not None and _fv8["fingerprint"] == fingerprint("rueckruf", dict(_zeile8)))
_trust8 = trust_des_fakts(dict(_fv8) if _fv8 else None, dict(_zeile8), "rueckruf")
check("8.6 trust_des_fakts() erkennt die Verifikation", _trust8 == STATUS_VERIFIED)
check("8.7 kba_referenz_anzeige() gibt die Referenz frei (Format plausibel, keine Kollision)",
      kba_referenz_anzeige("10008", "Testaktiv") == "10008")

# Regressionsbeweis fuer den PROPAGATIONS-BUG selbst: eine Inhalts-Auffrischung
# (wie beim naechsten KBA-Sync) darf die Verifikation NICHT stillschweigend
# invalidieren -- genau das war der Fehler hinter KBA 15632R (Referenz
# existiert amtlich, verschwand aber aus dem Bericht, weil die Verifikation
# nach einem spaeteren Text-Update nicht mitgezogen wurde).
_plan8b = _refresh_mod.plane_sync([kba_zeile(**{
    "KBA-Referenznummer": "10008", "Marke": "TESTAKTIV",
    "Produktionszeitraum von": "2020", "Produktionszeitraum bis": "2021",
    "Mangelbezeichnung": "Die Bremse kann durch einen Fertigungsfehler VOLLSTAENDIG "
                          "ausfallen und zu einem schweren Unfall führen.",
})])
check("8.8 geänderter amtlicher Text wird als Inhalts-Aktualisierung erkannt",
      any(u["kba_referenz"] == "10008" for u in _plan8b["aktualisierungen"]))
with _db_mod.get_conn() as _conn8b:
    _erg8b = _refresh_mod.apply_sync(_conn8b, _plan8b, heute="2026-10-06")
_db_mod.invalidate_referenzdaten_cache()
check("8.9 genau 1 Aktualisierung, keine neue Zeile", _erg8b["aktualisiert"] == 1
      and _erg8b["eingefuegt"] == 0)
_conn_check8b = sqlite3.connect(_db_pfad)
_conn_check8b.row_factory = sqlite3.Row
_zeile8b = _conn_check8b.execute(
    "SELECT * FROM rueckruf WHERE baureihe_id='ta-3er-db-open'").fetchone()
_fv8b = _conn_check8b.execute(
    "SELECT * FROM fakt_verifikation WHERE fakt_art='rueckruf' AND fakt_id=?",
    (_zeile8b["id"],)).fetchone()
check("8.10 Fingerprint wurde MIT der Inhalts-Aktualisierung mitgezogen (kein stilles "
      "Invalidieren der Verifikation)",
      _fv8b is not None and _fv8b["fingerprint"] == fingerprint("rueckruf", dict(_zeile8b)))
_trust8b = trust_des_fakts(dict(_fv8b) if _fv8b else None, dict(_zeile8b), "rueckruf")
check("8.11 Referenz bleibt nach der Auffrischung verifiziert und sichtbar",
      _trust8b == "verified"
      and kba_referenz_anzeige(_zeile8b["kba_referenz"], "Testaktiv") == "10008")
_conn_check8.close()
_conn_check8b.close()


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 9) Baureihen-Rueckruf bleibt FIN-abhaengig (unveraenderte Formulierung) ===")
import app.recall_filter as _rf_mod
_rf_quelltext = open(_rf_mod.__file__, encoding="utf-8").read()
check("9.1 dieses Release ruehrt die individuelle Betroffenheitspruefung "
      "(rueckruf_applicability) nicht an -- Funktion weiterhin vorhanden",
      "def rueckruf_applicability(" in _rf_quelltext)
check("9.2 neue kanonische Zeilen tragen weiterhin applicability='series_only' "
      "(Baureihen-Ebene, kein individueller Betroffenheitsanspruch)",
      _k2 and _k2[0].applicability == "series_only")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 10) Reihenfolge-Unabhaengigkeit ===")
_b10 = [baureihe("ta-3er-order-a", von=2019, bis=None),
       baureihe("ta-3er-order-b", von=2015, bis=2021)]
_rows10 = [kba_zeile(**{"KBA-Referenznummer": "10010",
                       "Produktionszeitraum von": "2020", "Produktionszeitraum bis": "2021"}),
          kba_zeile(**{"KBA-Referenznummer": "10011",
                       "Produktionszeitraum von": "2018", "Produktionszeitraum bis": "2019"})]
_k10_fwd = import_kandidaten(_rows10, [], _b10)
_k10_rev = import_kandidaten(list(reversed(_rows10)), [], list(reversed(_b10)))
_zus10_fwd, _ = ergaenzende_zeilen(_k10_fwd, _b10, [])
_zus10_rev, _ = ergaenzende_zeilen(_k10_rev, list(reversed(_b10)), [])
_paare_fwd = {(z["kba_referenz"], z["baureihe_id"]) for z in _zus10_fwd}
_paare_rev = {(z["kba_referenz"], z["baureihe_id"]) for z in _zus10_rev}
check("10.1 identisches Ergebnis unabhaengig von Export-/Baureihen-Reihenfolge",
      _paare_fwd == _paare_rev)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 11+12) Mehrmodelliger Datensatz: EIN Ziel sicher, ein ANDERES mehrdeutig ===")
# Mirrors der Production-Diagnose (KBA 16131R/16790R): ein herstellerweiter
# Rueckruf nennt "3" (eindeutig -> offene Generation) UND "2" (zwei
# gleichzeitig gefuehrte VIRA-Baureihen desselben Modells -> echte
# Mehrdeutigkeit). Erwartung: das "3"-Paar wird uebernommen, das "2"-Paar
# bleibt Review -- OHNE dass eines das andere beeinflusst.
_b11 = [
    baureihe("ta-3er-multi-open", modell="3er", von=2019, bis=None),
    baureihe("ta-2er-multi-a", modell="2er", von=2014, bis=2021),
    baureihe("ta-2er-multi-b", modell="2er", von=2019, bis=None),
]
_k11 = import_kandidaten([kba_zeile(**{
    "KBA-Referenznummer": "10012", "Modell": "3, 2",
    "Produktionszeitraum von": "2020", "Produktionszeitraum bis": "2021",
})], [], _b11)
check("11.1 kandidatenweite Klasse ist AMBIGUOUS_GENERATION (durch das '2er'-Ziel "
      "heruntergezogen)", _k11 and _k11[0].klasse == AMBIGUOUS_GENERATION)
_paare11 = {bid: kl for bid, kl, _g in _k11[0].paare}
check("11.2 PAAR-Klasse fuer das '3er'-Ziel ist fuer sich SAFE_IMPORT",
      _paare11.get("ta-3er-multi-open") == SAFE_IMPORT)
check("11.3 PAAR-Klasse fuer beide '2er'-Ziele ist AMBIGUOUS_GENERATION",
      _paare11.get("ta-2er-multi-a") == AMBIGUOUS_GENERATION
      and _paare11.get("ta-2er-multi-b") == AMBIGUOUS_GENERATION)
_zusatz11, _ = ergaenzende_zeilen(_k11, _b11, [])
check("11.4 (RESOLVABLE) das sichere '3er'-Paar wird trotz kandidatenweiter "
      "Mehrdeutigkeit uebernommen",
      any(z["kba_referenz"] == "10012" and z["baureihe_id"] == "ta-3er-multi-open"
          for z in _zusatz11))
check("11.5 (UNRESOLVED) keines der '2er'-Ziele wird automatisch zugewiesen",
      not any(z["baureihe_id"] in ("ta-2er-multi-a", "ta-2er-multi-b")
              for z in _zusatz11))
check("12.1 genau EIN Ziel pro Rueckruf wurde uebernommen (keine Verdopplung "
      "durch das mehrmodellige Record)", len(_zusatz11) == 1)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 13) Abgelehnter SAFE_IMPORT-Kandidat bleibt im Audit-/Review-Bestand ===")
_b13 = [baureihe("ta-modz-vorgaenger-13", von=2011, bis=2018),
       baureihe("ta-modz-open-13", von=2019, bis=None)]
_plan13 = None
_orig_baureihen_fn = _db_mod.get_alle_baureihen_kurz
_orig_recalls_fn = _db_mod.get_alle_rueckruf_referenzen_mit_baureihe
_orig_recalls_sync_fn = _db_mod.get_alle_rueckrufe_fuer_sync
_db_mod.get_alle_baureihen_kurz = lambda: _b13
_db_mod.get_alle_rueckruf_referenzen_mit_baureihe = lambda: []
_db_mod.get_alle_rueckrufe_fuer_sync = lambda: []
try:
    _plan13 = _refresh_mod.plane_sync([kba_zeile(**{
        "KBA-Referenznummer": "10013", "Marke": "TESTAKTIV",
        "Produktionszeitraum von": "2018", "Produktionszeitraum bis": "2025",
    })])
finally:
    _db_mod.get_alle_baureihen_kurz = _orig_baureihen_fn
    _db_mod.get_alle_rueckruf_referenzen_mit_baureihe = _orig_recalls_fn
    _db_mod.get_alle_rueckrufe_fuer_sync = _orig_recalls_sync_fn
check("13.1 Kandidat wird NICHT kanonisch (Fenster vor Generationsstart, wie Fall 4)",
      not any(z["kba_referenz"] == "10013" for z in _plan13["neue_zeilen"]))
check("13.2 Kandidat bleibt trotzdem im Review-/Audit-Bestand sichtbar "
      "(kein stilles Verschwinden)",
      any(k.referenz == "10013" for k in _plan13["review_kandidaten"]))


for _k, _v in (("AUTO_KI_DB_PATH", _alt_db_env), ("AUTO_KI_CHROMA_PATH", _alt_chroma_env)):
    if _v is None:
        os.environ.pop(_k, None)
    else:
        os.environ[_k] = _v
importlib.reload(_cfg)
importlib.reload(_db_mod)


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("BMW-REGRESSIONSFIXTUR (Production Smoke BMW 330i G20 2020)")
print("=" * 70)
# Synthetische Nachbildung der Production-Diagnose -- REALE Bauzeitraeume aus
# der Diagnose (F30 2011-2018, G20/G21 2019-NULL), SYNTHETISCHE Referenzen
# (keine echten KBA-Nummern hartkodiert). Erwartung ist STRUKTURELL, nicht
# eine feste Anzahl.
_bmw_b = [
    baureihe("bmw-3er-f30-sim", marke="BMW", modell="3er", von=2011, bis=2018),
    baureihe("bmw-3er-g20-sim", marke="BMW", modell="3er", von=2019, bis=None),
    baureihe("bmw-2er-a-sim", marke="BMW", modell="2er", von=2014, bis=2023),
    baureihe("bmw-2er-b-sim", marke="BMW", modell="2er", von=2019, bis=None),
]
_bmw_rows = [
    # entspricht 10807/12198: eindeutiges Ziel, nur durch offene Generation blockiert
    kba_zeile(**{"KBA-Referenznummer": "90810", "Marke": "BMW", "Modell": "3",
                "Produktionszeitraum von": "2020", "Produktionszeitraum bis": "2021"}),
    # entspricht 15630R: Fenster beginnt VOR dem G20/G21-Start (2018 < 2019)
    kba_zeile(**{"KBA-Referenznummer": "90630", "Marke": "BMW", "Modell": "3",
                "Produktionszeitraum von": "2018", "Produktionszeitraum bis": "2025"}),
    # entspricht 16131R/16790R: mehrmodellig, "3" eindeutig / "2" mehrdeutig
    kba_zeile(**{"KBA-Referenznummer": "90131", "Marke": "BMW", "Modell": "3, 2",
                "Produktionszeitraum von": "2020", "Produktionszeitraum bis": "2024",
                "Mangelbezeichnung": "Brandgefahr durch fehlerhaften Starter."}),
]
_bmw_kand = import_kandidaten(_bmw_rows, [], _bmw_b)
_bmw_zeilen_a, _ = pruefe_batch_a(_bmw_kand, _bmw_b, [])
_bmw_zusatz, _bmw_aus = ergaenzende_zeilen(_bmw_kand, _bmw_b, [])
_bmw_paare = {(z["kba_referenz"], z["baureihe_id"]) for z in _bmw_zusatz}

check("BMW.1 bestehende, geschlossene-Generation-Logik unveraendert (hier: keine "
      "geschlossene Zielbaureihe betroffen, Batch A liefert nichts)", _bmw_zeilen_a == [])
check("BMW.2 90810 (~10807/12198) wird fuer G20/G21 sicher uebernommen",
      ("90810", "bmw-3er-g20-sim") in _bmw_paare)
check("BMW.3 90630 (~15630R) wird NICHT zugewiesen (Fenster vor Generationsstart)",
      not any(z["kba_referenz"] == "90630" for z in _bmw_zusatz))
check("BMW.4 90131 (~16131R/16790R): das G20/G21-Paar wird trotz des mehrdeutigen "
      "'2er'-Ziels im selben Datensatz uebernommen",
      ("90131", "bmw-3er-g20-sim") in _bmw_paare)
check("BMW.5 90131: keines der beiden '2er'-Ziele wird automatisch zugewiesen",
      not any(ref == "90131" and bid in ("bmw-2er-a-sim", "bmw-2er-b-sim")
              for ref, bid in _bmw_paare))
check("BMW.6 keine doppelten Zeilen fuer dieselbe (Referenz, Baureihe)",
      len(_bmw_paare) == len(_bmw_zusatz))
check("BMW.7 jede uebernommene Zeile bleibt Baureihen-Ebene (applicability "
      "unveraendert series_only, FIN-Pruefung bleibt Aufgabe des KaufChecks)",
      all(k.applicability == "series_only" for k in _bmw_kand))


print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE ACTIVE-GENERATION-RECALL-TESTS GRUEN")
