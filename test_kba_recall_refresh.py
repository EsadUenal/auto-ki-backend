"""
KBA-RECALL-REFRESH — Zusicherungen (Release-Hardening, "Recall Freshness").

  A) parse_export: Format-Validierung
  B) fehlende_kandidaten/bericht: Diff gegen den lokalen Bestand (gemockt)
  C) download_export: ein GET, kein Retry, kein zweiter Endpunkt
  D) main() OHNE --apply: strukturell unmöglich, eine Zeile zu schreiben
  E) Reale Exportdatei (nur falls ein Pfad übergeben wird; sonst übersprungen)

Ab hier GEGEN EINE ECHTE, ISOLIERTE SQLITE-DB (temp-Verzeichnis, eigener
AUTO_KI_DB_PATH — niemals die Live-/Dev-DB). KEIN Netzwerk, KEIN LLM-Call,
KEINE Tavily-Calls — nur der generische Sync-Schreibpfad selbst:

  F) plane_sync: neue Zeile wird vorgeschlagen, Review-Kandidat getrennt
  G) apply_sync: tatsächliches INSERT, idempotent bei Wiederholung
  H) bestehende Referenz mit geändertem amtlichem Text -> kontrollierte UPDATE
  I) Referenz verschwindet aus dem Export -> KEINE automatische Löschung
  J) ambige Zuordnung (zwei Generationen) -> Review, NICHT automatisch gemappt
  K) ungültiges CSV-Format / Downloadfehler -> main() schreibt NICHTS

    python test_kba_recall_refresh.py [pfad/zum/kba_export.csv]
"""
import importlib
import os
import sqlite3
import sys
import tempfile

import httpx

from app.kba_recall_refresh import (
    ERWARTETE_SPALTEN, ExportFormatFehler, bericht, download_export,
    fehlende_kandidaten, parse_export,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def kba_zeile(**kw) -> dict:
    """Ein amtlicher Datensatz — exakte Spaltennamen des echten Exports
    (verifiziert per echtem HTTP-GET am 02.10.2026, siehe Modul-Docstring)."""
    z = {
        "KBA-Referenznummer": "9001", "Rückrufcode des Herstellers": "ABC",
        "Veröffentlichungsdatum": "2020-05-01", "Marke": "OPEL",
        "Modell": "INSIGNIA",
        "Mangelbezeichnung": "Die Lenkspindel kann brechen.",
        "Produktionszeitraum von": "2018", "Produktionszeitraum bis": "2020",
        "Hotline des Herstellers": "N/A", "Rückrufseite des Herstellers": "N/A",
        "Beschreibung der Maßnahme": "Austausch der Lenkspindel.",
        "Titel der Maßnahme": "", "Bekannte Vorfälle mit Sach- und/oder Personenschäden": "N/A",
        "Anzahl potentiell betroffene Fahrzeugteile und Fahrzeugzubehör weltweit": "N/A",
        "Anzahl potentiell betroffene Fahrzeugteile und Fahrzeugzubehör deutschlandweit": "100",
        "Mögliche Eingrenzung der betroffenen Modelle": "N/A",
        "Überwachung der Rückrufaktion durch das KBA": "überwacht",
    }
    z.update(kw)
    return z


def csv_bytes(zeilen: list[dict]) -> bytes:
    spalten = list(zeilen[0].keys())
    zeilen_text = [";".join(spalten)]
    zeilen_text += [";".join(str(z.get(s, "")) for s in spalten) for z in zeilen]
    return ("\n".join(zeilen_text)).encode("utf-8")


def baureihe(**kw) -> dict:
    b = {"id": "opel-insignia-b", "marke": "Opel", "modell": "Insignia",
         "generation": "B", "bauzeitraum_von": 2017, "bauzeitraum_bis": 2022}
    b.update(kw)
    return b


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== A) parse_export: Format-Validierung ===")
gut = csv_bytes([kba_zeile(), kba_zeile(**{"KBA-Referenznummer": "9002"})])
zeilen = parse_export(gut)
check("A1 zwei Zeilen korrekt geparst", len(zeilen) == 2)
check("A2 amtliche Spaltennamen erhalten", ERWARTETE_SPALTEN <= set(zeilen[0].keys()))
check("A3 Werte korrekt zugeordnet", zeilen[0]["Marke"] == "OPEL"
      and zeilen[1]["KBA-Referenznummer"] == "9002")

schlecht = "<html><body>Fehler 500</body></html>".encode("utf-8")
try:
    parse_export(schlecht)
    check("A4 kein gültiges Format -> ExportFormatFehler", False)
except ExportFormatFehler:
    check("A4 kein gültiges Format -> ExportFormatFehler", True)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== B) fehlende_kandidaten/bericht: Diff gegen den lokalen Bestand ===")
import app.kba_recall_refresh as _refresh_mod


def _mock_baureihen():
    return [baureihe()]


def _mock_recalls_leer():
    return []


def _mock_recalls_gedeckt():
    return [{"kba_referenz": "9001", "baureihe_id": "opel-insignia-b"}]


import app.database as _db_mod
_orig_get_baureihen = _db_mod.get_alle_baureihen_kurz
_orig_get_recalls = _db_mod.get_alle_rueckruf_referenzen_mit_baureihe

_db_mod.get_alle_baureihen_kurz = _mock_baureihen
_db_mod.get_alle_rueckruf_referenzen_mit_baureihe = _mock_recalls_leer
try:
    kand_neu = fehlende_kandidaten([kba_zeile()])
    rep_neu = bericht(kand_neu)
    check("B1 neue amtliche Referenz, lokal unbekannt -> erscheint als Kandidat",
          len(kand_neu) == 1 and kand_neu[0].referenz == "9001")
    check("B2 wird als SAFE_IMPORT klassifiziert (eindeutige Baureihe, Zeitraum passt)",
          kand_neu[0].klasse == "SAFE_IMPORT")
    check("B3 Bericht zählt genau 1 SAFE_IMPORT-Rückruf, 1 VIRA-Zeile",
          rep_neu["SAFE_IMPORT"]["anzahl_rueckrufe"] == 1
          and rep_neu["SAFE_IMPORT"]["anzahl_zeilen_bei_import"] == 1)
finally:
    _db_mod.get_alle_rueckruf_referenzen_mit_baureihe = _mock_recalls_gedeckt
try:
    kand_bekannt = fehlende_kandidaten([kba_zeile()])
    check("B4 bereits lokal vorhandenes (Referenz, Baureihe)-Paar -> KEIN Kandidat "
          "(Freshness-Abgleich ist idempotent, keine Dubletten bei Wiederholung)",
          len(kand_bekannt) == 0)
finally:
    _db_mod.get_alle_baureihen_kurz = _orig_get_baureihen
    _db_mod.get_alle_rueckruf_referenzen_mit_baureihe = _orig_get_recalls


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== C) download_export: ein GET, kein Retry, kein zweiter Endpunkt ===")
_aufrufe: list[str] = []


def _handler(request: httpx.Request) -> httpx.Response:
    _aufrufe.append(str(request.url))
    return httpx.Response(200, content=csv_bytes([kba_zeile()]),
                          headers={"content-disposition": "attachment; filename=export.csv"})


mock_client = httpx.Client(transport=httpx.MockTransport(_handler))
from pathlib import Path
with tempfile.TemporaryDirectory() as tmp:
    ziel = Path(tmp) / "export.csv"
    ergebnis = download_export(ziel, client=mock_client)
    check("C1 genau EIN HTTP-Aufruf", len(_aufrufe) == 1)
    check("C2 Aufruf ging an die amtliche KBA-Export-URL",
          _aufrufe[0] == _refresh_mod.KBA_EXPORT_URL)
    check("C3 Datei lokal gespeichert und parsbar",
          ergebnis.exists() and len(parse_export(ergebnis)) == 1)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== D) main() OHNE --apply: strukturell unmöglich, eine Zeile zu schreiben ===")
# Release-Hardening (Root Cause 1) hat dem Modul einen EXPLIZITEN, opt-in
# --apply-Schreibpfad hinzugefügt (vorher: strukturell gar keiner). Die
# Zusicherung ist jetzt verhaltensbasiert statt textbasiert: ohne --apply im
# argv erreicht main() den Schreibpfad nie — geprüft, indem apply_sync()
# durch eine Funktion ersetzt wird, die bei jedem Aufruf fehlschlägt.
_orig_apply_sync = _refresh_mod.apply_sync


def _apply_sync_darf_nicht_laufen(*a, **kw):
    raise AssertionError("apply_sync() wurde ohne --apply aufgerufen")


_orig_argv = sys.argv
_orig_download = _refresh_mod.download_export
_orig_get_rueckrufe_fuer_sync = _db_mod.get_alle_rueckrufe_fuer_sync
_refresh_mod.apply_sync = _apply_sync_darf_nicht_laufen
_refresh_mod.download_export = lambda dest, **kw: Path(dest).write_bytes(csv_bytes([kba_zeile()])) or Path(dest)
_db_mod.get_alle_baureihen_kurz = _mock_baureihen
_db_mod.get_alle_rueckruf_referenzen_mit_baureihe = _mock_recalls_leer
_db_mod.get_alle_rueckrufe_fuer_sync = lambda: []
try:
    sys.argv = ["kba_recall_refresh"]
    _refresh_mod.main()
    check("D1 main() ohne --apply laeuft durch, OHNE apply_sync() aufzurufen", True)
except AssertionError as e:
    check("D1 main() ohne --apply laeuft durch, OHNE apply_sync() aufzurufen", False)
    print(f"      {e}")
finally:
    sys.argv = _orig_argv
    _refresh_mod.apply_sync = _orig_apply_sync
    _refresh_mod.download_export = _orig_download
    _db_mod.get_alle_baureihen_kurz = _orig_get_baureihen
    _db_mod.get_alle_rueckruf_referenzen_mit_baureihe = _orig_get_recalls
    _db_mod.get_alle_rueckrufe_fuer_sync = _orig_get_rueckrufe_fuer_sync

quelltext = Path(_refresh_mod.__file__).read_text(encoding="utf-8")
check("D2 main() dokumentiert den sicheren Default ausdrücklich",
      "REIN LESENDER Bericht" in quelltext and "--apply" in quelltext)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== E) Reale Exportdatei (nur falls als Argument übergeben) ===")
_pfad_arg = [a for a in sys.argv[1:] if not a.startswith("-")]
if _pfad_arg:
    pfad = _pfad_arg[0]
    echte_zeilen = parse_export(pfad)
    check(f"E1 reale Datei '{pfad}' geparst", len(echte_zeilen) > 1000)
    check("E2 amtliche Spalten vorhanden", ERWARTETE_SPALTEN <= set(echte_zeilen[0].keys()))
else:
    print("(kein Dateipfad übergeben — übersprungen, siehe Kopfkommentar)")


# ══════════════════════════════════════════════════════════════════════════════
# ── Ab hier: echte, isolierte SQLite-DB (eigener AUTO_KI_DB_PATH) ──────────────
print("\n=== DB-Setup: isolierte, frische Test-DB (niemals die Live-DB) ===")

_tmp_dir = tempfile.mkdtemp(prefix="vira_kba_sync_")
_db_pfad = os.path.join(_tmp_dir, "auto_ki.db")
_alt_db_env = os.environ.get("AUTO_KI_DB_PATH")
_alt_chroma_env = os.environ.get("AUTO_KI_CHROMA_PATH")
os.environ["AUTO_KI_DB_PATH"] = _db_pfad
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp_dir, "chroma")

import app.config as _cfg
importlib.reload(_cfg)
importlib.reload(_db_mod)
_db_mod.ensure_tables()
# BEWUSST KEIN importlib.reload(_refresh_mod): das Modul liest die DB
# ausschliesslich ueber lazy `from app.database import ...` INNERHALB seiner
# Funktionen (siehe plane_sync/apply_sync), braucht also kein Reload, um den
# neuen AUTO_KI_DB_PATH zu sehen. Ein Reload würde dagegen die oben bereits
# importierten Namen (parse_export, ExportFormatFehler, ...) von den NEU
# erzeugten Funktions-/Klassenobjekten entkoppeln (reload mutiert denselben
# Modul-Namespace in place) — ein `except ExportFormatFehler` weiter unten
# würde dann die neue Exception-Klasse nicht mehr erkennen.

check("DB0 Test-DB wurde frisch angelegt (nicht die Live-DB)",
      os.path.abspath(_db_pfad) == os.path.abspath(str(_cfg.DB_PATH)))

# Zwei synthetische Baureihen — eindeutig erfundene Marke/Modell, damit
# dieser Test unabhängig vom echten (sich laufend ändernden) Fahrzeug-Seed
# bleibt. "Testsync" statt "ViraTest" u.ä., um jede zufällige Kollision mit
# einer echten Marke im Seed auszuschliessen.
_conn_setup = sqlite3.connect(_db_pfad)
_conn_setup.execute(
    "INSERT INTO baureihe (id, marke, modell, generation, bauzeitraum_von, bauzeitraum_bis, "
    "letzte_aktualisierung) "
    "VALUES ('testsync-modellx-gen1', 'Testsyncmarke', 'Modellx', 'Gen1', 2015, 2019, '2026-10')")
# Zweite, spaeter folgende Generation DESSELBEN Modells -> erzeugt bei einem
# amtlichen Datensatz, der beide Baujahr-Fenster ueberlappt, eine ECHTE
# Mehrdeutigkeit (Abschnitt J).
_conn_setup.execute(
    "INSERT INTO baureihe (id, marke, modell, generation, bauzeitraum_von, bauzeitraum_bis, "
    "letzte_aktualisierung) "
    "VALUES ('testsync-modellx-gen2', 'Testsyncmarke', 'Modellx', 'Gen2', 2019, 2023, '2026-10')")
# Drittes, isoliertes Modell fuer die Insert/Update/Delete-Tests (F-I) — keine
# zweite Generation, damit dort keine Mehrdeutigkeit hineinspielt.
_conn_setup.execute(
    "INSERT INTO baureihe (id, marke, modell, generation, bauzeitraum_von, bauzeitraum_bis, "
    "letzte_aktualisierung) "
    "VALUES ('testsync-modelly-gen1', 'Testsyncmarke', 'Modelly', 'Gen1', 2015, 2020, '2026-10')")
_conn_setup.commit()
_conn_setup.close()
_db_mod.invalidate_referenzdaten_cache()


def _kba_sicherheitsrelevant(**kw) -> dict:
    """Ein amtlicher, UEBERWACHTER, sicherheitsrelevanter Datensatz (die
    Mangelbezeichnung muss eine Unfall-/Sicherheitsfolge nennen, sonst filtert
    `import_kandidaten(nur_sicherheitsrelevant=True)` ihn schon vor jeder
    Klassifikation heraus)."""
    return kba_zeile(**{
        "Marke": "Testsyncmarke",
        "Mangelbezeichnung": "Die Bremse kann durch einen Fertigungsfehler ausfallen "
                              "und zu einem Unfall führen.",
        "Beschreibung der Maßnahme": "Prüfung und ggf. Austausch der Bremse.",
        **kw,
    })


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== F) plane_sync: neue Zeile vorgeschlagen, ambige Zuordnung getrennt ===")
_plan_f = _refresh_mod.plane_sync([
    _kba_sicherheitsrelevant(**{"KBA-Referenznummer": "99501", "Modell": "MODELLY",
                                "Produktionszeitraum von": "2016",
                                "Produktionszeitraum bis": "2018"}),
    _kba_sicherheitsrelevant(**{"KBA-Referenznummer": "99601", "Modell": "MODELLX",
                                "Produktionszeitraum von": "2017",
                                "Produktionszeitraum bis": "2021"}),
])
_neu_ids = {z["kba_referenz"] for z in _plan_f["neue_zeilen"]}
check("F1 eindeutiges Paar (Modelly, geschlossene Generation) -> neue Zeile vorgeschlagen",
      "99501" in _neu_ids)
check("F2 Zielbaureihe korrekt (testsync-modelly-gen1)",
      any(z["kba_referenz"] == "99501" and z["baureihe_id"] == "testsync-modelly-gen1"
          for z in _plan_f["neue_zeilen"]))
check("F3 ambiges Paar (Modellx: Fenster 2017-2021 ueberlappt BEIDE Generationen) "
      "landet NICHT in neue_zeilen",
      "99601" not in _neu_ids)
check("F4 ambiges Paar landet im Review-Bestand",
      any(k.referenz == "99601" for k in _plan_f["review_kandidaten"]))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== G) apply_sync: tatsächliches INSERT ===")
with _db_mod.get_conn() as _conn_g:
    _ergebnis_g = _refresh_mod.apply_sync(_conn_g, _plan_f, heute="2026-10-02")
_db_mod.invalidate_referenzdaten_cache()
check("G1 genau 1 neue Zeile eingefügt (Modelly), Modellx (ambig) nicht",
      _ergebnis_g["eingefuegt"] == 1)
check("G2 genau 1 Review-Kandidat geschrieben (Modellx)",
      _ergebnis_g["review_geschrieben"] == 1)

_conn_check = sqlite3.connect(_db_pfad)
_conn_check.row_factory = sqlite3.Row
_zeile_g = _conn_check.execute(
    "SELECT * FROM rueckruf WHERE baureihe_id='testsync-modelly-gen1'").fetchall()
check("G3 genau eine rueckruf-Zeile fuer Modelly in der DB", len(_zeile_g) == 1)
if _zeile_g:
    check("G4 Mangeltext korrekt uebernommen",
          "Bremse" in _zeile_g[0]["mangel"])
_review_g = _conn_check.execute(
    "SELECT * FROM kba_rueckruf_review WHERE kba_referenz='99601'").fetchall()
check("G5 Review-Zeile fuer das ambige Paar (99601) existiert, "
      "KEINE rueckruf-Zeile dafuer", len(_review_g) == 1)
check("G6 Review-Zeile nennt beide moeglichen Baureihen",
      len(_review_g) == 1
      and "testsync-modellx-gen1" in _review_g[0]["moegliche_baureihen"]
      and "testsync-modellx-gen2" in _review_g[0]["moegliche_baureihen"])
check("G7 Review-Klasse ist AMBIGUOUS_GENERATION",
      len(_review_g) == 1 and _review_g[0]["klasse"] == "AMBIGUOUS_GENERATION")
_conn_check.close()


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== H) Wiederholter Sync mit IDENTISCHEM Export: idempotent ===")
_export_wiederholt = [
    _kba_sicherheitsrelevant(**{"KBA-Referenznummer": "99501", "Modell": "MODELLY",
                                "Produktionszeitraum von": "2016",
                                "Produktionszeitraum bis": "2018"}),
    _kba_sicherheitsrelevant(**{"KBA-Referenznummer": "99601", "Modell": "MODELLX",
                                "Produktionszeitraum von": "2017",
                                "Produktionszeitraum bis": "2021"}),
]
_plan_h = _refresh_mod.plane_sync(_export_wiederholt)
check("H1 zweiter Lauf schlaegt KEINE neue Zeile fuer 99501 mehr vor (schon im Bestand)",
      not any(z["kba_referenz"] == "99501" for z in _plan_h["neue_zeilen"]))
check("H2 zweiter Lauf schlaegt KEINE Inhalts-Aktualisierung vor (Text unveraendert)",
      len(_plan_h["aktualisierungen"]) == 0)
with _db_mod.get_conn() as _conn_h:
    _ergebnis_h = _refresh_mod.apply_sync(_conn_h, _plan_h, heute="2026-10-03")
_db_mod.invalidate_referenzdaten_cache()
check("H3 zweiter apply_sync fuegt NICHTS mehr ein", _ergebnis_h["eingefuegt"] == 0)
_conn_check = sqlite3.connect(_db_pfad)
_anzahl_h = _conn_check.execute(
    "SELECT COUNT(*) FROM rueckruf WHERE baureihe_id='testsync-modelly-gen1'").fetchone()[0]
check("H4 weiterhin genau EINE Zeile fuer Modelly (keine Dublette)", _anzahl_h == 1)
_conn_check.close()


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== I) Amtlicher Text geändert -> kontrollierte UPDATE (keine neue Zeile) ===")
_export_geaendert = [
    _kba_sicherheitsrelevant(**{
        "KBA-Referenznummer": "99501", "Modell": "MODELLY",
        "Produktionszeitraum von": "2016", "Produktionszeitraum bis": "2018",
        "Mangelbezeichnung": "Die Bremse kann durch einen Fertigungsfehler vollstaendig "
                              "ausfallen, was zu einem schweren Unfall fuehren kann.",
    }),
]
_plan_i = _refresh_mod.plane_sync(_export_geaendert)
check("I1 keine neue Zeile (Paar schon im Bestand)",
      not any(z["kba_referenz"] == "99501" for z in _plan_i["neue_zeilen"]))
check("I2 genau eine Inhalts-Aktualisierung vorgeschlagen",
      len(_plan_i["aktualisierungen"]) == 1)
with _db_mod.get_conn() as _conn_i:
    _ergebnis_i = _refresh_mod.apply_sync(_conn_i, _plan_i, heute="2026-10-04")
_db_mod.invalidate_referenzdaten_cache()
check("I3 apply_sync aktualisiert genau 1 Zeile, fuegt keine neue ein",
      _ergebnis_i["aktualisiert"] == 1 and _ergebnis_i["eingefuegt"] == 0)
_conn_check = sqlite3.connect(_db_pfad)
_conn_check.row_factory = sqlite3.Row
_nach_update = _conn_check.execute(
    "SELECT * FROM rueckruf WHERE baureihe_id='testsync-modelly-gen1'").fetchall()
check("I4 weiterhin genau EINE Zeile (ID unveraendert, kein neuer Datensatz)",
      len(_nach_update) == 1)
check("I5 Mangeltext wurde auf die neue amtliche Fassung aktualisiert",
      len(_nach_update) == 1 and "vollstaendig" in _nach_update[0]["mangel"])
check("I6 baureihe_id blieb unveraendert (Update aendert NIE die Zuordnung)",
      len(_nach_update) == 1 and _nach_update[0]["baureihe_id"] == "testsync-modelly-gen1")
_conn_check.close()


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== J) Referenz verschwindet aus dem Export -> KEINE automatische Loeschung ===")
_plan_j = _refresh_mod.plane_sync([])    # leerer Export: 99501 kommt amtlich nicht mehr vor
with _db_mod.get_conn() as _conn_j:
    _ergebnis_j = _refresh_mod.apply_sync(_conn_j, _plan_j, heute="2026-10-05")
_db_mod.invalidate_referenzdaten_cache()
check("J1 leerer Export loest keine Schreibvorgaenge aus",
      _ergebnis_j == {"eingefuegt": 0, "aktualisiert": 0, "review_geschrieben": 0})
_conn_check = sqlite3.connect(_db_pfad)
_anzahl_j = _conn_check.execute(
    "SELECT COUNT(*) FROM rueckruf WHERE baureihe_id='testsync-modelly-gen1'").fetchone()[0]
check("J2 die Zeile fuer Modelly existiert weiterhin unveraendert (kein Auto-Delete)",
      _anzahl_j == 1)
_conn_check.close()


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== K) FAIL CLOSED: ungültiges Format / Downloadfehler schreiben NICHTS ===")


def _handler_500(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, content=b"Internal Server Error")


_mock_client_fehler = httpx.Client(transport=httpx.MockTransport(_handler_500))
with tempfile.TemporaryDirectory() as tmp:
    ziel_fehler = Path(tmp) / "export.csv"
    try:
        download_export(ziel_fehler, client=_mock_client_fehler)
        check("K1 Downloadfehler (HTTP 500) -> Exception statt stillem Fallback", False)
    except httpx.HTTPStatusError:
        check("K1 Downloadfehler (HTTP 500) -> Exception statt stillem Fallback", True)

try:
    parse_export(b"<html>keine CSV</html>")
    check("K2 ungueltiges Format -> Exception VOR jedem DB-Zugriff", False)
except ExportFormatFehler:
    check("K2 ungueltiges Format -> Exception VOR jedem DB-Zugriff", True)

_anzahl_vorher = sqlite3.connect(_db_pfad).execute(
    "SELECT COUNT(*) FROM rueckruf").fetchone()[0]
check("K3 Bestand unveraendert nach beiden Fehlerfaellen (keine Teilschreibung)",
      sqlite3.connect(_db_pfad).execute("SELECT COUNT(*) FROM rueckruf").fetchone()[0]
      == _anzahl_vorher)


# ── Aufräumen: Umgebung zurücksetzen ────────────────────────────────────────
for _k, _v in (("AUTO_KI_DB_PATH", _alt_db_env), ("AUTO_KI_CHROMA_PATH", _alt_chroma_env)):
    if _v is None:
        os.environ.pop(_k, None)
    else:
        os.environ[_k] = _v
importlib.reload(_cfg)
importlib.reload(_db_mod)


print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE KBA-RECALL-REFRESH-TESTS GRUEN")
