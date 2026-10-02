from __future__ import annotations

"""
Generischer, EXPLIZIT ausgelöster Abgleich gegen den amtlichen KBA-Rückruf-
export (Release-Hardening, "Recall Freshness").

ROOT CAUSE, DIE DIESES MODUL ADRESSIERT
----------------------------------------
Der lokale Rückruf-Bestand ist ein historischer, manuell kuratierter Import
(app/kba_batch_a/b1/c/d_daten.py, eingespielt über app/kba_import_batch_*.py).
Es gibt keinen automatischen Aktualisierungspfad: ein sicherer, vollständiger
DB-Treffer löst im KaufCheck NIEMALS eine Recherche aus (app/kaufcheck.py) —
das gilt für Rückrufe genauso wie für jede andere Fahrzeugangabe. Ein neuer
amtlicher Rückruf, der nach dem letzten manuellen Import veröffentlicht
wurde, taucht deshalb bei einem DB-Treffer nie auf, auch wenn das Fahrzeug
längst in der Datenbank steht.

WARUM KEIN WEB-FALLBACK PRO KAUFCHECK
--------------------------------------
Ein Tavily-Aufruf pro KaufCheck würde laufende Providerkosten für JEDEN
Check erzeugen, nur um etwas zu prüfen, das sich amtlich und kostenlos lösen
lässt. Das Kraftfahrt-Bundesamt bietet dafür selbst einen offiziellen,
öffentlichen CSV-Export (siehe `KBA_EXPORT_URL` unten, verifiziert am
02.10.2026 per echtem HTTP-GET: ~7.900 Zeilen, `Content-Disposition:
attachment; filename=export-fahrzeuge-<Datum>.csv`, aktualisiert laut
Pressemitteilung "unmittelbar nach Halterermittlung" — also praktisch
tagesaktuell). Dieser Export ist GENAU das Format, für das
`app/kba_import_kandidaten.py` bereits gebaut wurde (siehe dessen
Property-Namen: "KBA-Referenznummer", "Marke", "Modell",
"Produktionszeitraum von/bis", "Mögliche Eingrenzung der betroffenen
Modelle" — exakte Spaltenüberschriften des echten Exports). Dieses Modul
liefert nur die fehlende Klammer darum: herunterladen, parsen, gegen den
lokalen Bestand abgleichen, die VORHANDENE Klassifizierungslogik aufrufen.

WAS DIESES MODUL NICHT TUT
---------------------------
Es schreibt NIEMALS in die Datenbank. Es wird von KEINEM KaufCheck-/
VerkaufsCheck-/Request-Pfad importiert oder aufgerufen — ausschließlich als
eigenständiges Wartungskommando (`python -m app.kba_recall_refresh`),
manuell oder über einen externen Scheduler vor einem Release ausgeführt
(siehe Abschnitt "EINPLANUNG" im Docstring von `main()`).

Ein als SAFE_IMPORT klassifizierter Kandidat wird NICHT automatisch
übernommen. Ein Rückruf ist eine Sicherheitsaussage über ein reales
Fahrzeug; dieses Projekt hat an anderer Stelle (KBA-Referenz-Trust-Gate,
Recall-Verification-Pilot, app/recall_filter.py) wiederholt gezeigt, dass
automatisch übernommene, ungeprüfte Sicherheitsbehauptungen real falsch
waren. Die Übernahme bleibt ein bewusster, von einem Menschen geprüfter
Schritt — ein neuer `kba_import_batch_e.py` nach demselben Muster wie die
vorhandenen Chargen, nicht ein automatischer Nebeneffekt dieses Abgleichs.

ARCHITEKTUR
-----------
    amtlicher KBA-Export (CSV)
    -> download_export() / lokal gespeicherte Datei
    -> parse_export() -> list[dict] (exakt das Format von kba_import_kandidaten)
    -> fehlende_abgleich() lädt den LOKALEN Bestand (app/database.py)
    -> app.kba_import_kandidaten.import_kandidaten() klassifiziert
       (dieselbe Logik wie jede historische Charge — hier NICHT neu erfunden)
    -> bericht() fasst zusammen: Anzahl je Klasse + Beispiele, zur Prüfung
"""

import csv
import io
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Verifiziert per echtem HTTP-GET am 02.10.2026: liefert eine CSV-Datei
# (Content-Disposition: attachment), ohne Login, ohne JavaScript — der
# eigene "Datenbank herunterladen"-Link der öffentlichen Rückrufdatenbank
# (https://www.kba-online.de/rrdb/buerger/), kein inoffizieller/verdeckter
# Endpunkt. `type=cars` liefert Fahrzeuge; `type=parts` (nicht genutzt)
# Fahrzeugteile/-zubehör.
KBA_EXPORT_URL = "https://www.kba-online.de/rrdb/buerger/api/rueckruf/export?format=csv&type=cars"

# Spaltenüberschriften des echten Exports (Stand 02.10.2026) — nur zur
# Validierung, dass eine geladene Datei tatsächlich dieses Format hat und
# nicht z.B. eine HTML-Fehlerseite.
ERWARTETE_SPALTEN = {
    "KBA-Referenznummer", "Rückrufcode des Herstellers", "Veröffentlichungsdatum",
    "Marke", "Modell", "Mangelbezeichnung", "Produktionszeitraum von",
    "Produktionszeitraum bis", "Mögliche Eingrenzung der betroffenen Modelle",
    "Überwachung der Rückrufaktion durch das KBA",
}


class ExportFormatFehler(RuntimeError):
    """Die geladene Datei hat nicht die erwarteten Spalten des KBA-Exports."""


def download_export(dest_path: str | Path, *, url: str = KBA_EXPORT_URL,
                    timeout: float = 60.0, client=None) -> Path:
    """Lädt den amtlichen Export EINMAL herunter und speichert ihn lokal.

    Bewusst synchron und ohne Retry-Schleife: ein Wartungskommando, kein
    Laufzeitpfad. Bei einem Fehler (Timeout, 5xx, Netz) wirft diese Funktion
    — der Aufrufer entscheidet, ob der vorherige lokale Snapshot als Fallback
    weiterverwendet wird (siehe `main()`). Kein automatischer Retry, kein
    Loop über mehrere Endpunkte.

    `client` (optional, nur für Tests): ein bereits konstruierter
    `httpx.Client` (z.B. mit `transport=httpx.MockTransport(...)`), damit
    diese Funktion ohne echten Netzwerkzugriff geprüft werden kann. Ohne ihn
    wird ein gewöhnlicher `httpx.Client` verwendet.
    """
    import httpx
    dest = Path(dest_path)
    eigener_client = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        resp = client.get(url)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    finally:
        if eigener_client:
            client.close()
    return dest


def parse_export(quelle: str | Path | bytes) -> list[dict]:
    """CSV (Semikolon, UTF-8) -> list[dict] mit den amtlichen Spaltennamen.

    `quelle` ist ein Pfad ODER die schon gelesenen Bytes (z.B. direkt aus
    `download_export`'s Rückgabewert via `.read_bytes()`, oder in Tests ohne
    Festplatten-Umweg). Prüft die Kopfzeile gegen `ERWARTETE_SPALTEN`, damit
    eine HTML-Fehlerseite oder ein geändertes Format laut auffällt statt
    stillschweigend 0 Zeilen zu liefern.
    """
    if isinstance(quelle, (str, Path)):
        rohbytes = Path(quelle).read_bytes()
    else:
        rohbytes = quelle
    text = rohbytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    spalten = set(reader.fieldnames or [])
    if not ERWARTETE_SPALTEN <= spalten:
        fehlend = ERWARTETE_SPALTEN - spalten
        raise ExportFormatFehler(
            f"Export hat nicht die erwarteten Spalten (fehlen: {sorted(fehlend)}); "
            f"vermutlich kein gültiger KBA-CSV-Export oder das Format hat sich geändert.")
    return list(reader)


def fehlende_kandidaten(kba_zeilen: list[dict], *,
                        nur_ueberwacht: bool = True,
                        nur_sicherheitsrelevant: bool = True):
    """Lädt den lokalen Bestand (app/database.py) und klassifiziert, welche
    amtlichen Zeilen fehlen — über die VORHANDENE, battle-getestete Logik aus
    app/kba_import_kandidaten.py. Liest nur, schreibt nichts."""
    from app.database import get_alle_baureihen_kurz, get_alle_rueckruf_referenzen_mit_baureihe
    from app.kba_import_kandidaten import import_kandidaten

    baureihen = get_alle_baureihen_kurz()
    recalls = get_alle_rueckruf_referenzen_mit_baureihe()
    return import_kandidaten(kba_zeilen, recalls, baureihen,
                             nur_ueberwacht=nur_ueberwacht,
                             nur_sicherheitsrelevant=nur_sicherheitsrelevant)


def bericht(kandidaten) -> dict:
    """Menschlich prüfbare Zusammenfassung — Zählung je Klasse + bis zu 5
    Beispielreferenzen je Klasse. Trifft KEINE Übernahme-Entscheidung."""
    from app.kba_import_kandidaten import IMPORT_KLASSEN
    aus: dict[str, dict] = {}
    for klasse in IMPORT_KLASSEN:
        treffer = [k for k in kandidaten if k.klasse == klasse]
        aus[klasse] = {
            "anzahl_rueckrufe": len(treffer),
            "anzahl_zeilen_bei_import": sum(len(k.ziel_ids) for k in treffer),
            "beispiele": [k.referenz for k in treffer[:5]],
        }
    return aus


def main() -> None:
    """Wartungskommando: `python -m app.kba_recall_refresh [pfad-zu-export.csv]`.

    EINPLANUNG (für dieses Deployment noch NICHT eingerichtet — bewusst, s.
    Auftrag): als separater, zeitgesteuerter Job (z.B. Railway Cron Job oder
    ein GitHub-Actions-Schedule) VOR einem Daten-/Release-Zyklus, nicht bei
    jedem Deploy und nie im Request-Pfad. Läuft er ohne Dateipfad-Argument,
    lädt er den aktuellen Export frisch herunter; schlägt der Download fehl,
    bricht er ab (kein stiller Rückfall auf veraltete Daten OHNE Hinweis —
    wer das Kommando aufruft, sieht den Fehler und kann den letzten
    erfolgreichen Snapshot bewusst weiterverwenden).
    """
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    pfad = sys.argv[1] if len(sys.argv) > 1 else None
    if pfad:
        zeilen = parse_export(pfad)
        log.info("Export aus lokaler Datei gelesen: %s (%d Zeilen)", pfad, len(zeilen))
    else:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ziel = Path(tmp) / "kba_export.csv"
            download_export(ziel)
            zeilen = parse_export(ziel)
        log.info("Export frisch heruntergeladen (%d Zeilen)", len(zeilen))
    kandidaten = fehlende_kandidaten(zeilen)
    rep = bericht(kandidaten)
    log.info("=== KBA-Freshness-Abgleich: %d fehlende amtliche Rückrufe insgesamt ===",
             len(kandidaten))
    for klasse, info in rep.items():
        log.info("%-25s %4d Rückrufe (%4d VIRA-Zeilen) %s", klasse,
                 info["anzahl_rueckrufe"], info["anzahl_zeilen_bei_import"],
                 info["beispiele"])
    log.info("Dies ist ein REIN LESENDER Bericht — keine Zeile wurde geschrieben. "
             "Eine Übernahme erfolgt, wie bei allen bisherigen Chargen, als eigener, "
             "manuell geprüfter Schritt.")


if __name__ == "__main__":
    main()
