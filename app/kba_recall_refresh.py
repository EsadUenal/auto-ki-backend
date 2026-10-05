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

WAS DIESES MODUL TUT — UND WAS NICHT (Release-Hardening, Root Cause 1)
------------------------------------------------------------------------
Ohne `--apply` (Default) schreibt dieses Modul NIEMALS in die Datenbank —
genau wie im letzten Pass. Es wird von KEINEM KaufCheck-/VerkaufsCheck-/
Request-Pfad importiert oder aufgerufen — ausschließlich als eigenständiges
Wartungskommando (`python -m app.kba_recall_refresh [--apply]`), manuell oder
über einen externen Scheduler vor einem Release ausgeführt (siehe Abschnitt
"EINPLANUNG" im Docstring von `main()` — für DIESES Deployment bewusst noch
nicht eingerichtet).

MIT `--apply` schreibt es — aber NICHT jeden SAFE_IMPORT-Kandidaten blind.
Automatisch übernommen wird NUR die Teilmenge, die bereits die vollen
Batch-A-Kriterien erfüllt (app/kba_import_batch_a.py: eindeutige, GESCHLOSSENE
Zielgeneration, kein zweites plausibles Generationsziel, keine unabbildbare
Variantenbedingung, keine Dublette, plausibles Referenzformat, keine
markenfremde Kollision) — dieselbe, bereits manuell geprüfte Logik, mit der
die historischen Chargen A/B1/C/D entschieden wurden, nicht neu erfunden.
ALLES andere (ambige Generation, unklare Variante, Verdachtsdublette, kein
belastbares Ziel, oder SAFE_IMPORT mit OFFENER Zielgeneration) landet in
`kba_rueckruf_review` (app/database.py) zur menschlichen Prüfung — ein
amtlicher Rückruf wird nie verworfen, aber auch nie einer Baureihe
zugeordnet, die sich nicht sicher belegen lässt (siehe `plane_sync()`,
`apply_sync()`). Löschungen gibt es nie: ein Datensatz, der im nächsten
Export fehlt, bleibt unverändert stehen.

ARCHITEKTUR
-----------
    amtlicher KBA-Export (CSV)
    -> download_export() / lokal gespeicherte Datei
    -> parse_export() -> list[dict] (exakt das Format von kba_import_kandidaten)
    -> fehlende_kandidaten() / plane_sync() lädt den LOKALEN Bestand (app/database.py)
    -> app.kba_import_kandidaten.import_kandidaten() klassifiziert
       (dieselbe Logik wie jede historische Charge — hier NICHT neu erfunden)
    -> bericht() fasst zusammen: Anzahl je Klasse + Beispiele, zur Prüfung
    -> NUR mit --apply: apply_sync() schreibt den Plan idempotent (siehe dort)
"""

import csv
import datetime
import io
import json
import logging
from pathlib import Path

from app.kba_reconciliation import normalisiere_referenz

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


# ══════════════════════════════════════════════════════════════════════════
# SYNC — Release-Hardening, Root Cause 1: "KBA Freshness muss jetzt
# produktionsfähig werden". Alles oberhalb dieser Linie bleibt unverändert
# REIN LESEND (der strukturelle Charakter des Moduls aus dem letzten Pass).
# Ab hier: eine explizite, opt-in `--apply`-Schreibfunktion — siehe `main()`.
#
# ARCHITEKTUR DER TRENNUNG (kleinste sichere Erweiterung, nichts neu erfunden):
#   - "automatisch sicher genug": GENAU dieselben Tore wie die historische
#     Charge A (app.kba_import_batch_a.pruefe_batch_a — SAFE_IMPORT,
#     GESCHLOSSENE Zielgeneration, kein zweites plausibles Generationsziel,
#     keine unabbildbare Variantenbedingung, keine Dublette, plausibles
#     Referenzformat, keine markenfremde Kollision, kein paralleler amtlicher
#     Datensatz). Batch A war ein einmaliger, auf einen eingefrorenen
#     Kandidatenschnappschuss angewendeter Lauf; hier ist es derselbe,
#     unveränderte Code, nur auf dem LIVE-Abgleich und mit dynamischer
#     ID-Vergabe (rueckruf.id ist AUTOINCREMENT — SQLite vergibt sie, keine
#     feste Basis wie ID_BASIS=2001 nötig).
#   - ALLES ANDERE (AMBIGUOUS_GENERATION, VARIANT_SCOPE_UNCLEAR,
#     POSSIBLE_DUPLICATE, UNSUPPORTED_MODEL_MAPPING, oder SAFE_IMPORT mit
#     OFFENER Zielgeneration): landet in `kba_rueckruf_review`
#     (app/database.py) — ein amtlicher Rückruf wird NIE verworfen, nur weil
#     das Fahrzeugmapping nicht sicher ist, aber auch NIE einer Baureihe
#     zugeordnet, die sich nicht belegen lässt.
#   - Bereits importierte Paare, deren amtlicher Text sich geändert hat:
#     `plane_aktualisierungen` — ändert NIE die Zuordnung (Baureihe/ID),
#     frischt nur Mangel-/Abhilfetext auf.

def plane_aktualisierungen(kba_zeilen: list[dict], recalls_voll: list[dict]) -> list[dict]:
    """Bestehende VIRA-Zeilen, deren amtlicher Mangel-/Abhilfetext sich seit
    dem letzten Import geändert hat.

    Arbeitet NUR auf Paaren, die SCHON im Bestand stehen — die Zuordnung
    Referenz->Baureihe wurde früher entschieden (Batch A/B1/C/D, G20-Nachtrag
    o.ä.) und wird hier NICHT neu bewertet, nur ihr Text aufgefrischt. Deshalb
    bewusst UNABHÄNGIG von `import_kandidaten()`, das genau solche Paare als
    "schon gedeckt" aus der Kandidatenliste ausschließt (siehe dessen
    `gedeckte_paare`-Filter).

    Bewusst NUR `mangel`/`abhilfe`: `betroffene_baujahre` ist beim Erstimport
    als Schnitt aus amtlichem Fenster und VIRA-Bauzeitraum berechnet
    (app/kba_import_batch_a._baujahre) — das hier blind neu zu berechnen,
    liefe demselben Risiko zuwider, das jene Funktion verhindert, falls sich
    der VIRA-Bauzeitraum einer Baureihe zwischenzeitlich geändert hat. Bleibt
    deshalb unangetastet; eine Korrektur dort ist ein eigener, informierter
    Schritt wie bisher."""
    kba_je_referenz: dict[str, dict] = {}
    for z in kba_zeilen:
        ref = normalisiere_referenz(z.get("KBA-Referenznummer"))
        if ref and ref not in kba_je_referenz:
            kba_je_referenz[ref] = z     # erste Zeile gewinnt bei Dubletten im Export

    updates = []
    for r in recalls_voll:
        ref = normalisiere_referenz(r.get("kba_referenz"))
        amtlich = kba_je_referenz.get(ref) if ref else None
        if not amtlich:
            continue
        mangel_amtlich = (amtlich.get("Mangelbezeichnung") or "").strip()
        if not mangel_amtlich:
            continue
        abhilfe_amtlich = (amtlich.get("Beschreibung der Maßnahme") or "").strip() or None
        aenderungen = {}
        if (r.get("mangel") or "").strip() != mangel_amtlich:
            aenderungen["mangel"] = mangel_amtlich
        if (r.get("abhilfe") or None) != abhilfe_amtlich:
            aenderungen["abhilfe"] = abhilfe_amtlich
        if aenderungen:
            updates.append({"id": r["id"], "baureihe_id": r["baureihe_id"],
                            "kba_referenz": r.get("kba_referenz"), "aenderungen": aenderungen})
    return updates


def plane_sync(kba_zeilen: list[dict]) -> dict:
    """Voller Sync-Plan — REIN LESEND (wie der Rest des Moduls; siehe
    `apply_sync()` für das tatsächliche Schreiben). Macht EIGENE, frische
    DB-Reads statt der gecachten `get_alle_*`-Funktionen, damit ein
    `--apply`-Lauf innerhalb DESSELBEN Prozesses sofort den eigenen
    Schreibstand sieht (Idempotenz: zweimal hintereinander anwenden darf
    beim zweiten Mal nichts mehr vorschlagen).

    Active-Generation-Fix (Production Smoke BMW G20/G21): zusätzlich zu den
    unveränderten Batch-A-Zeilen (`pruefe_batch_a`, nur geschlossene
    Generationen) berücksichtigt `app.kba_active_generation.ergaenzende_
    zeilen()` sicher belegte Paare, die NUR wegen einer offenen Zielgeneration
    oder wegen eines unabhängigen, mehrdeutigen ANDEREN Ziels im selben
    mehrmodelligen amtlichen Datensatz bisher nie kanonisch wurden (siehe
    Moduldocstring dort) — ohne die Batch-A-Tore selbst zu verändern."""
    from app.database import get_alle_baureihen_kurz, get_alle_rueckrufe_fuer_sync
    from app.kba_active_generation import ergaenzende_zeilen
    from app.kba_import_batch_a import klasse_a, pruefe_batch_a
    from app.kba_import_kandidaten import import_kandidaten
    from app.kba_reconciliation import normalisiere_referenz

    baureihen = get_alle_baureihen_kurz()
    # Die VOLLEN Zeilen (inkl. mangel/abhilfe/betroffene_baujahre), nicht die
    # schlanke (kba_referenz, baureihe_id)-Projektion, die `fehlende_
    # kandidaten()` für den reinen Lesebericht verwendet: `pruefe_batch_a`
    # braucht `mangel` zwingend (A3-Dublettenprüfung über den Freitext), und
    # `import_kandidaten`s eigene Dublettenerkennung (`_moegliche_dubletten`)
    # wird mit den vollen Feldern zuverlässiger statt nur über die Referenz.
    recalls_voll = get_alle_rueckrufe_fuer_sync()

    kandidaten = import_kandidaten(kba_zeilen, recalls_voll, baureihen)

    zeilen_a, ausschluesse_a = pruefe_batch_a(kandidaten, baureihen, recalls_voll)
    zeilen_akt, ausschluesse_akt = ergaenzende_zeilen(kandidaten, baureihen, recalls_voll)
    neue_zeilen = zeilen_a + zeilen_akt
    aktualisierungen = plane_aktualisierungen(kba_zeilen, recalls_voll)

    # Audit-Trail (Root-Cause-Closing, offener Befund): review_kandidaten wird
    # NICHT mehr allein aus der kandidatenweiten klasse_a()-Vorauswahl
    # abgeleitet, sondern aus dem TATSÄCHLICHEN Ergebnis von neue_zeilen — ein
    # Kandidat, dessen Ziel(e) aus irgendeinem Grund (Batch-A-Tor A0-A5, Tor
    # A6, oder gar nicht erst vorausgewählt) nicht ALLE kanonisch wurden,
    # bleibt sichtbar. Vorher verschwand ein geschlossener, kandidatenweit
    # SAFE_IMPORT-Kandidat, der an einem Batch-A-Tor scheiterte, ersatzlos
    # (weder rueckruf noch kba_rueckruf_review) — s. `ausschluesse`.
    importierte_paare = {(normalisiere_referenz(z["kba_referenz"]), z["baureihe_id"])
                         for z in neue_zeilen}
    review_kandidaten = [
        k for k in kandidaten
        if not k.ziel_ids or not all(
            (normalisiere_referenz(k.referenz), z) in importierte_paare
            for z in k.ziel_ids)
    ]

    return {
        "kandidaten": kandidaten,
        "neue_zeilen": neue_zeilen,
        "ausschluesse": ausschluesse_a + ausschluesse_akt,
        "review_kandidaten": review_kandidaten,
        "aktualisierungen": aktualisierungen,
        "baureihen": baureihen,
    }


KBA_RECALL_QUELLE = "KBA-Rueckrufdatenbank, amtlicher Gesamtexport (automatischer Freshness-Abgleich)"


def _verifiziere_amtlich(conn, fakt_id: int, zeile: dict, *, heute: str) -> None:
    """Markiert eine (neu eingefügte ODER inhaltlich aufgefrischte) kanonische
    `rueckruf`-Zeile als amtlich verifiziert, mit einem Fingerprint, der zum
    GESCHRIEBENEN Inhalt passt.

    GENERISCHER PROVENANZ-FIX (Production Smoke BMW G20/G21, KBA 15632R):
    `app.fakt_verifikation.trust_des_fakts()` verwirft eine bestehende
    Verifikation automatisch, sobald ihr gespeicherter Fingerprint nicht mehr
    zum aktuellen Zeileninhalt passt — by design, Inhalt hat sich seit der
    Prüfung geändert. `apply_sync()` aktualisierte `mangel`/`abhilfe` bisher
    aber OHNE die Verifikation mitzuziehen: jede Inhalts-Auffrischung durch
    DENSELBEN amtlichen Export, der die Zeile überhaupt erst (oder erneut)
    ins Haus bringt, hat damit stillschweigend eine vorhandene KBA-
    Referenzanzeige im Bericht gelöscht (die Referenz blieb in der DB, nur
    `kba_referenz_anzeige()` zeigte sie nicht mehr, weil `evidence.py` bei
    nicht-verifiziertem Trust `kba_anzeige` unbedingt auf None setzt — s.
    app/evidence.py). Diese Funktion schließt die Lücke: dieselbe amtliche
    Quelle, die die Zeile schreibt, verifiziert sie auch — exakt das gleiche
    Verfahren wie bei jedem historischen Batch-Import (Batch A, G20-Nachtrag),
    nur generisch für den laufenden Sync statt für eine einmalige Charge."""
    from app.fakt_verifikation import fingerprint

    fp = fingerprint("rueckruf", zeile)
    referenz = zeile.get("kba_referenz") or ""
    werte = (fp, "verified", KBA_RECALL_QUELLE, "A", KBA_EXPORT_URL, referenz, heute,
             "Automatischer KBA-Freshness-Abgleich (app.kba_recall_refresh)")
    bestand = conn.execute(
        "SELECT id FROM fakt_verifikation WHERE fakt_art='rueckruf' AND fakt_id=?",
        (fakt_id,)).fetchone()
    if bestand:
        conn.execute(
            "UPDATE fakt_verifikation SET fingerprint=?, status=?, quelle=?, "
            "quelle_stufe=?, url=?, referenz=?, geprueft_am=?, notiz=? "
            "WHERE fakt_art='rueckruf' AND fakt_id=?", (*werte, fakt_id))
    else:
        conn.execute(
            "INSERT INTO fakt_verifikation (fakt_art, fakt_id, fingerprint, status, "
            "quelle, quelle_stufe, url, referenz, geprueft_am, notiz) "
            "VALUES ('rueckruf',?,?,?,?,?,?,?,?,?)", (fakt_id, *werte))


def apply_sync(conn, plan: dict, *, heute: str | None = None) -> dict:
    """Schreibt EINEN von `plane_sync()` berechneten Plan idempotent.

    Nur mit einer offenen Transaktion aufrufen (`app.database.get_conn()` —
    commit bei Erfolg, rollback bei jedem Fehler: FAIL CLOSED). Diese
    Funktion committet/rollbackt selbst NICHT — das bleibt beim Aufrufer, wie
    bei jeder anderen Schreibfunktion dieses Projekts (vgl. app/db_writer.py).

    Löscht NIE eine Zeile — weder aus `rueckruf` noch aus
    `kba_rueckruf_review`. Ein amtlicher Datensatz, der im nächsten Export
    fehlt, bleibt unangetastet stehen (siehe Moduldocstring "KEINE
    AUTO-LÖSCHUNG" im Auftrag)."""
    from app.kba_import_kandidaten import SAFE_IMPORT

    heute = heute or datetime.date.today().isoformat()
    baureihen_bis = {b["id"]: b.get("bauzeitraum_bis") for b in plan.get("baureihen") or []}

    eingefuegt = 0
    for z in plan["neue_zeilen"]:
        # `z["id"]` (von pruefe_batch_a vergeben, Basis ID_BASIS=2001) wird
        # bewusst NICHT verwendet — rueckruf.id ist AUTOINCREMENT, SQLite
        # vergibt eine garantiert kollisionsfreie ID. Dieselbe Spaltenliste
        # wie app/db_writer.py's regulärer Rückruf-Insert.
        cur = conn.execute(
            "INSERT INTO rueckruf (baureihe_id,datum,betroffene_baujahre,mangel,abhilfe,"
            "kba_referenz) VALUES (?,?,?,?,?,?)",
            (z["baureihe_id"], z["datum"], z["betroffene_baujahre"], z["mangel"],
             z["abhilfe"], z["kba_referenz"]))
        _verifiziere_amtlich(conn, cur.lastrowid, z, heute=heute)
        eingefuegt += 1

    aktualisiert = 0
    for u in plan["aktualisierungen"]:
        felder = u["aenderungen"]
        conn.execute(
            f"UPDATE rueckruf SET {', '.join(f'{k}=?' for k in felder)} WHERE id=?",
            (*felder.values(), u["id"]))
        frisch = conn.execute(
            "SELECT baureihe_id, datum, betroffene_baujahre, mangel, abhilfe, kba_referenz "
            "FROM rueckruf WHERE id=?", (u["id"],)).fetchone()
        if frisch is not None:
            _verifiziere_amtlich(conn, u["id"], dict(zip(
                ("baureihe_id", "datum", "betroffene_baujahre", "mangel", "abhilfe",
                 "kba_referenz"), frisch)), heute=heute)
        aktualisiert += 1

    review_geschrieben = 0
    for kand in plan["review_kandidaten"]:
        klasse = kand.klasse
        if klasse == SAFE_IMPORT:
            # Der Kandidat ist kandidatenweit SAFE_IMPORT, aber (teilweise)
            # nicht kanonisch geworden — zwei unterscheidbare, generische
            # Gründe statt einer irreführenden Originalklasse:
            if kand.ziel_ids and all(baureihen_bis.get(z) is None for z in kand.ziel_ids):
                klasse = "SAFE_IMPORT_OFFENE_GENERATION"   # Tor A6, s. kba_active_generation
            else:
                klasse = "SAFE_IMPORT_NICHT_UEBERNOMMEN"   # Tor A0-A5, s. ausschluesse
        prod = (f"{kand.prod_von}-{kand.prod_bis}"
                if kand.prod_von is not None and kand.prod_bis is not None else None)
        conn.execute(
            "INSERT INTO kba_rueckruf_review (kba_referenz, klasse, begruendung, marke, "
            "modell, mangel, produktionszeitraum, veroeffentlichungsdatum, "
            "moegliche_baureihen, zuerst_gesehen_am, zuletzt_gesehen_am) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(kba_referenz) DO UPDATE SET klasse=excluded.klasse, "
            "begruendung=excluded.begruendung, marke=excluded.marke, "
            "modell=excluded.modell, mangel=excluded.mangel, "
            "produktionszeitraum=excluded.produktionszeitraum, "
            "veroeffentlichungsdatum=excluded.veroeffentlichungsdatum, "
            "moegliche_baureihen=excluded.moegliche_baureihen, "
            "zuletzt_gesehen_am=excluded.zuletzt_gesehen_am",
            (kand.referenz, klasse, kand.begruendung, kand.marke, kand.modell, kand.mangel,
             prod, kand.datum, json.dumps(kand.ziel_ids), heute, heute))
        review_geschrieben += 1

    return {"eingefuegt": eingefuegt, "aktualisiert": aktualisiert,
            "review_geschrieben": review_geschrieben}


def main() -> None:
    """Wartungskommando: `python -m app.kba_recall_refresh [pfad-zu-export.csv] [--apply]`.

    OHNE `--apply` (Default, sicher): lädt/parst den Export, berechnet den
    vollständigen Plan und druckt ihn — identisch zum bisherigen Verhalten,
    KEINE Zeile wird geschrieben.

    MIT `--apply`: schreibt denselben Plan idempotent (siehe `apply_sync()`)
    — neue, sicher klassifizierte Zeilen in `rueckruf`, Textauffrischungen auf
    bereits vorhandenen Paaren, alles andere zur menschlichen Prüfung in
    `kba_rueckruf_review`. Läuft NIE automatisch (kein Scheduler in diesem
    Deployment, bewusst — s. Auftrag); ein Fehler (Download, Format, DB)
    bricht VOR jedem Schreibzugriff ab oder rollt die gesamte Transaktion
    zurück (FAIL CLOSED) — nie ein halb angewendeter Sync.

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
    args = sys.argv[1:]
    apply_ = "--apply" in args
    pfad_args = [a for a in args if a != "--apply"]
    pfad = pfad_args[0] if pfad_args else None
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

    plan = plane_sync(zeilen)
    rep = bericht(plan["kandidaten"])
    log.info("=== KBA-Freshness-Abgleich: %d fehlende amtliche Rückrufe insgesamt ===",
             len(plan["kandidaten"]))
    for klasse, info in rep.items():
        log.info("%-25s %4d Rückrufe (%4d VIRA-Zeilen) %s", klasse,
                 info["anzahl_rueckrufe"], info["anzahl_zeilen_bei_import"],
                 info["beispiele"])
    log.info("--- Sync-Plan ---")
    log.info("Neue Zeilen (automatisch sicher, Batch-A-Kriterien): %d",
             len(plan["neue_zeilen"]))
    log.info("Inhalts-Aktualisierungen (bestehende Paare, geänderter Text): %d",
             len(plan["aktualisierungen"]))
    log.info("Zur manuellen Prüfung (ambige/offene Zuordnung) in kba_rueckruf_review: %d",
             len(plan["review_kandidaten"]))

    if not apply_:
        log.info("Dies ist ein REIN LESENDER Bericht — keine Zeile wurde geschrieben. "
                 "Mit --apply wird genau dieser Plan idempotent angewendet.")
        return

    from app.database import get_conn, invalidate_referenzdaten_cache
    with get_conn() as conn:
        ergebnis = apply_sync(conn, plan)
    invalidate_referenzdaten_cache()
    log.info("=== --apply abgeschlossen: %d neu, %d aktualisiert, %d im Review-Bestand "
             "vermerkt ===", ergebnis["eingefuegt"], ergebnis["aktualisiert"],
             ergebnis["review_geschrieben"])


if __name__ == "__main__":
    main()
