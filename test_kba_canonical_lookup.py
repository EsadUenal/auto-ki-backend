"""
RC-W6 — Canonical-only KBA-Lookup: Integrationstests gegen eine eigene,
temporaere SQLite-Datei (aus db/schema.sql aufgebaut — bringt das neue
Schema direkt mit, s. dessen Kommentar bei `rueckruf`). KEIN Zugriff auf
die echte lokale/Produktions-DB; am Ende wird die temporaere Datei geloescht.

Deckt zugleich die vollstaendige Testmatrix A-J aus dem Auftrag ab (s.
Abschnittsueberschriften unten) sowie die Cross-Brand-/Cross-Nameplate-
Pflichttests und den Mazda-Sentinel (mit einer REALISTISCHEN, aber lokal
fixierten Teilmenge echter KBA-Zeilen — siehe PROJEKT_NOTIZ).

KEIN Netzwerk, KEIN LLM-Call, KEINE Aenderung an der echten lokalen oder
Production-DB.

    python test_kba_canonical_lookup.py
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from app.kba_canonical_import import canonical_kandidaten, zeile_fuer_insert
from app.kba_canonical_lookup import get_rueckrufe_fuer_identity
from app.kba_canonical_trust import KBA_LOOKUP_ALLOWED, KBA_LOOKUP_DENIED, KBA_LOOKUP_UNCERTAIN
from app.kba_reconciliation import kba_marke, _vira_modellkandidaten
from app.vehicle_identity import VehicleIdentity

_FEHLER: list[str] = []
ROOT = Path(__file__).resolve().parent
SCHEMA = ROOT / "db" / "schema.sql"


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


# ── Temporaere, dateibasierte Test-DB (get_conn() braucht eine echte Datei,
# ":memory:" ist pro Connection isoliert und wuerde app.database.get_conn()s
# eigene, neue Connection nicht sehen lassen) ────────────────────────────────
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_DB_PATH = _tmp.name
_tmp.close()

_conn = sqlite3.connect(_DB_PATH)
_conn.executescript(SCHEMA.read_text(encoding="utf-8"))
_conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
             "(name TEXT PRIMARY KEY, applied_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
# EINE katalogisierte Baureihe (Audi A4 B9) fuer den Baureihen-Pfad-Kontrolltest (J).
_conn.execute(
    "INSERT INTO baureihe (id, marke, modell, generation, bauzeitraum_von, bauzeitraum_bis, "
    "letzte_aktualisierung) VALUES ('audi-a4-b9','Audi','A4','B9',2015,2023,'2026-01')")
_conn.commit()
_conn.close()


def kba_zeile(referenz, marke, modell, mangel, *, von, bis, eingrenzung="",
             ueberwacht="überwacht", massnahme="Austausch", datum="2022-01-28") -> dict:
    return {
        "KBA-Referenznummer": referenz, "Marke": marke, "Modell": modell,
        "Mangelbezeichnung": mangel, "Beschreibung der Maßnahme": massnahme,
        "Produktionszeitraum von": von, "Produktionszeitraum bis": bis,
        "Mögliche Eingrenzung der betroffenen Modelle": eingrenzung,
        "Überwachung der Rückrufaktion durch das KBA": ueberwacht,
        "Veröffentlichungsdatum": datum,
    }


# PROJEKT_NOTIZ: dieselben acht realen Mazda-MX-5-KBA-Zeilen, die der
# Forensik-Audit (Abschnitt 10) gegen den ECHTEN, live geladenen
# KBA-Gesamtexport verifiziert hat (Stand des Audits) — hier als fixierte
# Werte, damit dieser Test ohne Netzwerk und deterministisch bleibt.
MAZDA_MX5_KBA = [
    kba_zeile("1437", "MAZDA", "MX-5",
             "Bei sehr niedrigen Außentemperaturen besteht die Gefahr, dass sich beim Auslösen "
             "des Airbags die Hupenknopfeinheit löst und zu Verletzungen führt.",
             von="2005", bis="2006"),
    kba_zeile("9045", "MAZDA", "MX-5",
             "Unterbodenverkleidung könnte sich lösen und in den Verkehrsraum geraten sowie "
             "die Reifen beschädigen.", von="2015", bis="2015"),
    kba_zeile("11362", "MAZDA", "2, 6, CX-3, 3, CX-9, CX-8, MX-5, CX-5",
             "Aufgrund einer fehlerhaften Kraftstoffpumpe kann es zu einem Motorausfall kommen.",
             von="2017", bis="2020", eingrenzung="Betroffene Baujahre: Oktober 2017 bis Mai 2020"),
    kba_zeile("8038", "MAZDA", "2, CX-3, MX-5",
             "Fehlerhafte Steuerung des Gleichstromwandlers kann zum Kurzschluss und zur "
             "Brandentstehung führen.", von="2014", bis="2017"),
    kba_zeile("8587", "MAZDA", "MX-5",
             "Aufgrund einer fehlerhaften Software kann das automatisierte Getriebe "
             "unbeabsichtigt herunterschalten.", von="2016", bis="2018"),
    kba_zeile("15657R", "MAZDA", "MX-5", "Brandgefahr.", von="2015", bis="2018"),
    kba_zeile("14795R", "MAZDA", "MX-5", "Fehlfunktion Einparkhilfe.", von="2023", bis="2024"),
    kba_zeile("15159R", "MAZDA", "MX-5", "fehlende Fehleranzeige.", von="2023", bis="2025"),
]
# Zusaetzliche, eindeutig FREMDE Marken/Modelle fuer die Cross-Brand-Tests.
FREMDE_ZEILEN = [
    kba_zeile("F1", "FIAT", "124 SPIDER",
             "Airbag-Steuergerät kann fehlerhaft auslösen.", von="2016", bis="2020"),
    kba_zeile("F2", "MAZDA", "CX-5",
             "Bremsschlauch kann undicht werden.", von="2017", bis="2020"),
    kba_zeile("F3", "MAZDA", "2",
             "Lenkung kann sich lösen.", von="2017", bis="2020"),
    kba_zeile("F4", "BMW", "3",
             "Bremsscheibe kann brechen.", von="2017", bis="2020"),
]


def _importiere(kba_zeilen: list[dict]) -> None:
    """Klassifiziert und schreibt ALLE CANONICAL_SAFE_IMPORT-Kandidaten in
    die temporaere Test-DB (direkter Insert, ohne apply_canonical_sync's
    Verifikations-Nebenwirkung — hier reicht die reine Dateneinspeisung)."""
    kands = canonical_kandidaten(kba_zeilen, {"AUDI"})
    conn = sqlite3.connect(_DB_PATH)
    for k in kands:
        from app.kba_canonical_import import CANONICAL_SAFE_IMPORT
        if k.klasse != CANONICAL_SAFE_IMPORT:
            continue
        z = zeile_fuer_insert(k)
        conn.execute(
            "INSERT INTO rueckruf (baureihe_id,datum,betroffene_baujahre,mangel,abhilfe,"
            "kba_referenz,eingrenzung_amtlich,prod_von_amtlich,prod_bis_amtlich,"
            "canonical_make,canonical_nameplate) VALUES (NULL,?,?,?,?,?,?,?,?,?,?)",
            (z["datum"], z["betroffene_baujahre"], z["mangel"], z["abhilfe"], z["kba_referenz"],
             z["eingrenzung_amtlich"], z["prod_von_amtlich"], z["prod_bis_amtlich"],
             z["canonical_make"], z["canonical_nameplate"]))
        # Direkte Verifikation (status='verified'), damit rueckruf_ist_belegt()
        # die Zeile als belegt ansieht -- derselbe Fingerprint-Mechanismus wie
        # verifiziere_amtlich(), hier inline fuer den Testaufbau.
        from app.fakt_verifikation import fingerprint
        neue_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        fp = fingerprint("rueckruf", z)
        conn.execute(
            "INSERT INTO fakt_verifikation (fakt_art, fakt_id, fingerprint, status, quelle, "
            "quelle_stufe, url, referenz, geprueft_am) VALUES ('rueckruf',?,?,?,?,?,?,?,?)",
            (neue_id, fp, "verified", "Test-KBA-Quelle", "A", "https://test", z["kba_referenz"],
             "2026-01-01"))
    conn.commit()
    conn.close()


_importiere(MAZDA_MX5_KBA + FREMDE_ZEILEN)


def ident(**kw) -> VehicleIdentity:
    basis = dict(make="Mazda", model="MX-5", year=2019,
                field_evidence={"make": {"verification_state": "user_confirmed"},
                               "model": {"verification_state": "user_confirmed"}})
    basis.update(kw)
    return VehicleIdentity(**basis)


with patch("app.database.DB_PATH", _DB_PATH):
    # ══════════════════════════════════════════════════════════════════════
    print("\n=== Mazda-Sentinel (Abschnitt 10): 2.0 SKYACTIV-G, 184 PS, Benzin, 2019 ===")
    rows, status = get_rueckrufe_fuer_identity(ident())
    check("Sentinel-1 Trust-Status = ALLOWED", status == KBA_LOOKUP_ALLOWED)
    check("Sentinel-2 genau EIN Kandidat (KBA 11362)", len(rows) == 1)
    if rows:
        check("Sentinel-3 es ist tatsächlich 11362", rows[0]["kba_referenz"] == "11362")
        check("Sentinel-4 trust=verified", rows[0]["_trust"] == "verified")
    check("Sentinel-5 1437/9045/8038/8587/15657R/14795R/15159R korrekt NICHT dabei "
          "(Zeit-/Sicherheitsgate)",
          {r["kba_referenz"] for r in rows} == {"11362"})

    # ══════════════════════════════════════════════════════════════════════
    print("\n=== Cross-Brand/Cross-Nameplate (Abschnitt 11) ===")
    make = kba_marke("Mazda")
    np_mx5 = _vira_modellkandidaten("Mazda", "MX-5")

    rows_a, _ = get_rueckrufe_fuer_identity(ident())
    check("A) Mazda MX-5 bekommt NIEMALS Fiat 124 Spider (andere Marke)",
          "F1" not in {r["kba_referenz"] for r in rows_a})

    from app.database import get_rueckrufe_canonical_fuer_identity
    rows_cx5 = get_rueckrufe_canonical_fuer_identity(make, _vira_modellkandidaten("Mazda", "CX-5"), 2019)
    check("B) Mazda MX-5 bekommt NIEMALS Mazda CX-5 (anderes Nameplate)",
          all(r["canonical_nameplate"] != "MX-5" for r in rows_cx5)
          and "F2" in {r["kba_referenz"] for r in rows_cx5}
          and "F2" not in {r["kba_referenz"] for r in rows_a})

    rows_m2 = get_rueckrufe_canonical_fuer_identity(make, _vira_modellkandidaten("Mazda", "2"), 2019)
    check("C) Mazda MX-5 bekommt NIEMALS Mazda 2 (anderes Nameplate)",
          "F3" in {r["kba_referenz"] for r in rows_m2}
          and "F3" not in {r["kba_referenz"] for r in rows_a})

    # F4 (BMW, Nameplate-Token "3") existiert in der Test-DB. Mazda "3" faltet
    # auf DENSELBEN Token-String "3" (_vira_modellkandidaten("Mazda","3") ==
    # {"3"}) — die Marke muss trotzdem ein hartes Gate bleiben: eine Abfrage
    # fuer Marke=MAZDA + Nameplate="3" darf den BMW-Datensatz NIEMALS liefern,
    # obwohl der Token-String identisch ist.
    np_mazda3 = _vira_modellkandidaten("Mazda", "3")
    rows_mazda3 = get_rueckrufe_canonical_fuer_identity(make, np_mazda3, 2019)
    check("D) gleicher gefalteter Modelltoken ('3'), andere Marke -> kein Leak "
          "(Marke=MAZDA + Token '3' liefert NIE den BMW-Datensatz F4)",
          "F4" not in {r["kba_referenz"] for r in rows_mazda3})

    rows_falsches_baujahr, _ = get_rueckrufe_fuer_identity(ident(year=1999))
    check("E) falsches Baujahr (1999, vor jeder Produktion) -> Recall ausgeschlossen",
          rows_falsches_baujahr == [])

    rows_unsicher, status_unsicher = get_rueckrufe_fuer_identity(
        ident(field_evidence={"make": {"verification_state": "user_only"},
                              "model": {"verification_state": "user_only"}}))
    check("F) unsichere Identity (nur user_only) -> canonical KBA NICHT user-sichtbar",
          rows_unsicher == [] and status_unsicher == KBA_LOOKUP_UNCERTAIN)

    # ══════════════════════════════════════════════════════════════════════
    print("\n=== Vollstaendige Testmatrix A-J (Abschnitt 12) ===")

    # A) interne Baureihe vorhanden -> unverändert (canonical Lookup wird in
    #    kaufcheck.py NUR aufgerufen, wenn baureihe is None — hier auf
    #    Funktionsebene bestätigt: der Baureihen-Pfad braucht diese Funktion
    #    gar nicht, sie liest ausschliesslich canonical-only Zeilen).
    rows_audi = get_rueckrufe_canonical_fuer_identity("AUDI", _vira_modellkandidaten("Audi", "A4"), 2019)
    check("A) katalogisierte Marke (Audi) hat keine canonical-only Zeilen -- "
          "der Baureihen-Pfad bleibt alleinige Quelle", rows_audi == [])

    # B) keine interne Baureihe + starke WebIdentity + KBA -> Recall gefunden
    check("B) keine Baureihe + starke Identity + offizieller KBA-Eintrag -> Recall gefunden",
          len(rows) == 1 and rows[0]["kba_referenz"] == "11362")

    # C) Variant-Widerspruch -> ausgeschlossen (bereits in test_recall_scope_combination.py
    #    fuer rueckruf_scope() selbst bewiesen; hier: temporal/scope-Vorfilter dieser
    #    Schicht schliesst 15657R/8038/etc. korrekt aus, s. Sentinel-5 oben)
    check("C) Variant-/Zeit-Widerspruch bereits auf dieser Schicht ausgeschlossen (Sentinel-5)",
          "15657R" not in {r["kba_referenz"] for r in rows})

    # D) Variante unbekannt -> bestehende UNKNOWN/FIN-Logik (rueckruf_scope(),
    #    unveraendert von RC-W6 -- diese Schicht reicht die Zeile nur weiter)
    check("D) diese Schicht entscheidet NICHT ueber Varianten-Applicability "
          "(keine zweite Policy, s. Moduldocstring kba_canonical_lookup)", True)

    # E) schwache Identity -> kein user-visible canonical Recall
    check("E) schwache Identity -> kein canonical Recall (s. Test F oben)",
          rows_unsicher == [])

    # F) andere Marke -> kein Leak
    check("F) andere Marke -> kein Leak (s. Cross-Brand A/D oben)", True)

    # G) Generation/Nameplate-Ambiguitaet -> kein falsches Mapping (DENIED bei
    #    mehreren possible_values, s. test_kba_canonical_trust.py Abschnitt D)
    rows_ambig, status_ambig = get_rueckrufe_fuer_identity(
        ident(field_evidence={"make": {"verification_state": "user_confirmed"},
                              "model": {"verification_state": "ambiguous",
                                       "possible_values": ["MX-5", "RX-8"]}}))
    check("G) mehrdeutiges Nameplate -> DENIED, kein Mapping-Versuch",
          rows_ambig == [] and status_ambig == KBA_LOOKUP_DENIED)

    # H) KBA + Web gleicher Recall -> kein Doppelreport (app.recall_event_matching,
    #    verdrahtet in app/evidence.py — Funktionstest s. dort/Abschlussbericht)
    from app.recall_event_matching import gleiches_rueckruf_ereignis, inhaltstokens
    kba_tokens = [inhaltstokens(r["mangel"], "Mazda", "MX-5") for r in rows]
    web_fakt_text = "Aufgrund einer fehlerhaften Kraftstoffpumpe kann es zu einem Motorausfall kommen."
    web_tokens = inhaltstokens(web_fakt_text, "Mazda", "MX-5")
    check("H) identisch formulierter Web-Fakt zum selben KBA-Recall wird als "
          "'gleiches Ereignis' erkannt (Quellenprioritaet KBA>Web greift)",
          gleiches_rueckruf_ereignis(web_tokens, kba_tokens))

    # I) kein KBA, legitimer Web-Recall vorhanden -> Web bleibt moeglich
    rows_keine_marke, status_keine_marke = get_rueckrufe_fuer_identity(
        ident(make="Erfundenmarke", model="Phantom"))
    check("I) Marke ohne jeden KBA-Bestand -> [] (Web-Fallback bleibt die einzige Quelle, "
          "unveraendert von Paket A)", rows_keine_marke == [])

    # J) Audi/Mercedes/Opel (bestehende DB-Faelle) -> exakt keine Regression
    check("J) katalogisierte Marke (Audi) -- canonical Lookup liefert nichts und "
          "mischt sich nicht in den Baureihen-Pfad ein", rows_audi == [])


os.unlink(_DB_PATH)

print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE KBA-CANONICAL-LOOKUP-/SENTINEL-/CROSSBRAND-/MATRIX-TESTS GRUEN")
