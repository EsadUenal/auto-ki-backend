"""
KBA-RECALL-REFRESH — Zusicherungen (Release-Hardening, "Recall Freshness").

KEIN Netzwerk, KEIN LLM-Call, KEINE Tavily-Calls, KEINE DB-Mutation.
Der Download wird gegen einen `httpx.MockTransport` geprüft (kein echter
HTTP-Call); der Klassifikator wird gegen feste Fixtures geprüft, nicht gegen
den Live-Export (der ändert sich täglich — siehe test_kba_import_dryrun.py,
dieselbe Konvention).

  A) parse_export: Format-Validierung
  B) fehlende_kandidaten/bericht: Diff gegen den lokalen Bestand (gemockt)
  C) download_export: ein GET, kein Retry, kein zweiter Endpunkt
  D) Strukturelle Schreib-Sperre: das Modul kann die DB nicht mutieren
  E) Reale Exportdatei (nur falls ein Pfad übergeben wird; sonst übersprungen)

    python test_kba_recall_refresh.py [pfad/zum/kba_export.csv]
"""
import sys

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


_orig_b = _refresh_mod.fehlende_kandidaten.__globals__
# Direkter Monkeypatch der importierten Namen in app.database (so wie
# test_kba_import_dryrun.py / test_kba_abgleich.py es für verwandte Module tun).
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
import tempfile
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
print("\n=== D) Strukturelle Schreib-Sperre: das Modul kann die DB nicht mutieren ===")
quelltext = Path(_refresh_mod.__file__).read_text(encoding="utf-8")
code_ohne_kommentare = "\n".join(
    zeile for zeile in quelltext.splitlines()
    if not zeile.strip().startswith("#"))
check("D1 kein INSERT/UPDATE/DELETE/REPLACE INTO im Code (nur in Docstrings/Kommentaren erlaubt)",
      not any(verb in code_ohne_kommentare.upper()
              for verb in ("INSERT INTO", "UPDATE RUECKRUF", "DELETE FROM", "REPLACE INTO")))
check("D2 kein Import eines Schreib-Pfads (db_writer, Admin-Router)",
      "db_writer" not in quelltext and "routers.admin" not in quelltext)
check("D3 main() dokumentiert ausdrücklich: rein lesender Bericht",
      "REIN LESENDER Bericht" in quelltext)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== E) Reale Exportdatei (nur falls als Argument übergeben) ===")
if len(sys.argv) > 1:
    pfad = sys.argv[1]
    echte_zeilen = parse_export(pfad)
    check(f"E1 reale Datei '{pfad}' geparst", len(echte_zeilen) > 1000)
    check("E2 amtliche Spalten vorhanden", ERWARTETE_SPALTEN <= set(echte_zeilen[0].keys()))
else:
    print("(kein Dateipfad übergeben — übersprungen, siehe Kopfkommentar)")


print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE KBA-RECALL-REFRESH-TESTS GRUEN")
