from __future__ import annotations

"""
Verifikation EINZELNER Fahrzeugfakten (Verification-Pilot).

WARUM NICHT `baureihe.verification`
-----------------------------------
Die bestehende Verifikations-Architektur (app/verification.py) arbeitet auf
BAUREIHEN-/KATEGORIEEBENE. Setzt man dort fuer den BMW G20 `schwachstellen` auf
`verified`, dann gilt in `app/evidence.py` JEDE Schwachstelle dieser Baureihe als
geprueft — der Trust wird dort einmal pro Kategorie berechnet und auf alle Zeilen
der Schleife angewendet. Fuer eine ehrliche Verifikation ist das unbrauchbar: es
gibt keine Baureihe, deren Fakten alle gleichzeitig geprueft wurden. Ein
gepruefter Fakt wuerde dutzende ungepruefte mit hochziehen — genau die stille
Fehl-Vertrauensbildung, gegen die das Runtime Trust Gate gebaut wurde.

Dieses Modul verifiziert deshalb den EINZELNEN Fakt. Es ERSETZT
`app/verification.py` nicht: das bleibt fuer die Marktvergleichs-Fakten
(generation, chassis_codes, karosserie, motorvarianten, facelift) zustaendig.
Hier geht es ausschliesslich um die vier Faktenarten, aus denen der Kaufcheck
seine fahrzeugspezifischen Aussagen bildet.

DIE ZENTRALE SICHERUNG: FINGERPRINT
-----------------------------------
Die numerischen Fakt-IDs sind AUTOINCREMENT. `app/db_writer.py::save_fahrzeug`
schreibt eine Baureihe per DELETE + INSERT neu — danach tragen die Zeilen ANDERE
IDs bei gleichem Inhalt, oder dieselbe ID bei anderem Inhalt. Eine Verifikation,
die nur an der ID haengt, wuerde dann still am falschen Fakt kleben.

Deshalb speichert jede Verifikation zusaetzlich einen Fingerprint ueber die
inhaltstragenden Felder zum Zeitpunkt der Pruefung. Stimmt er zur Laufzeit nicht
mehr ueberein, gilt der Fakt wieder als `unverified_db`. Fail-safe in die
vorsichtige Richtung: im Zweifel lieber ungeprueft als falsch geprueft.

STATUSWERTE
-----------
``verified``            Kernaussage des Fakts (Bauteil, Fehlerbild, Zuordnung zu
                        genau diesem Fahrzeug) durch eine belastbare Quelle
                        bestaetigt. NUR dieser Status ergibt `trust=verified`.
``partially_verified``  Thema belegt, aber der Zuschnitt in der Datenbank geht
                        ueber die Quellenlage hinaus (zu weiter Baujahresbereich,
                        zusaetzlich genannte Motorisierung/Getriebe ohne Beleg,
                        abweichendes Bauteil). Bleibt ausdruecklich
                        `unverified_db` und traegt keinen Floor.
``rejected``            Die Quellenlage widerspricht der Aussage. Bleibt
                        `unverified_db`; die Zeile wird hier NICHT geloescht —
                        Datenkorrekturen laufen ueber app/data_migrations.py.

QUELLENSTUFEN
-------------
``A`` Hersteller, KBA/Behoerden, offizielle technische Dokumente
``B`` ADAC, TUEV, DEKRA, etablierte Fachmedien, seriose technische Datenbanken
``C`` Marken-/Modellspezialisten, ergaenzend
"""

import hashlib
import logging
import sqlite3

log = logging.getLogger(__name__)

STATUS_VERIFIED = "verified"
STATUS_PARTIALLY = "partially_verified"
STATUS_REJECTED = "rejected"

QUELLENSTUFEN = ("A", "B", "C")

# Faktenart -> (Tabelle, ID-Spalte, inhaltstragende Spalten fuer den Fingerprint)
#
# Die Fingerprint-Spalten sind bewusst genau die Felder, die der Nutzer am Ende
# zu sehen bekommt. Aendert sich eines davon, ist es fachlich ein anderer Fakt und
# die alte Pruefung gilt nicht mehr. Fremdschluessel (baureihe_id/variante_id)
# gehoeren dazu: derselbe Text an einem anderen Fahrzeug ist eine andere Aussage.
FAKT_ARTEN: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "schwachstelle_baureihe": (
        "schwachstelle_baureihe", "id",
        ("baureihe_id", "bauteil", "beschreibung", "betroffene_baujahre", "schweregrad"),
    ),
    "schwachstelle_motor": (
        "schwachstelle_motor", "id",
        ("variante_id", "bauteil", "beschreibung", "baujahre"),
    ),
    "rueckruf": (
        "rueckruf", "id",
        ("baureihe_id", "datum", "betroffene_baujahre", "mangel", "abhilfe", "kba_referenz"),
    ),
    "kritische_wartung": (
        "kritische_wartung", "id",
        ("variante_id", "bauteil", "intervall", "hinweis"),
    ),
}


def fingerprint(fakt_art: str, zeile) -> str:
    """SHA-256 ueber die inhaltstragenden Felder eines Fakts.

    `zeile` ist ein Mapping (sqlite3.Row oder dict). Fehlende Felder zaehlen als
    None — so bleibt der Fingerprint auch dann berechenbar, wenn ein Aufrufer nur
    eine Teilauswahl der Spalten geladen hat, und ein unvollstaendig geladener
    Fakt bekommt garantiert NICHT denselben Fingerprint wie der vollstaendige.
    """
    if fakt_art not in FAKT_ARTEN:
        raise ValueError(f"unbekannte Faktenart: {fakt_art!r}")
    _tab, _idspalte, spalten = FAKT_ARTEN[fakt_art]
    werte = []
    for s in spalten:
        try:
            v = zeile[s]
        except (KeyError, IndexError, TypeError):
            v = None
        werte.append("" if v is None else str(v))
    roh = fakt_art + "\x1f" + "\x1f".join(werte)
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()


def _tabelle_vorhanden(conn: sqlite3.Connection) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fakt_verifikation'"
        ).fetchone() is not None
    except sqlite3.Error:
        return False


def lade_verifikationen(conn: sqlite3.Connection, fakt_art: str,
                        fakt_ids) -> dict[int, dict]:
    """Verifikationen zu den angegebenen Fakt-IDs — {fakt_id: eintrag}.

    Eine fehlende Tabelle (alte Datenbank, die die Migration noch nicht gesehen
    hat) ist kein Fehler: dann gibt es eben keine Verifikationen und alles bleibt
    `unverified_db`.
    """
    fakt_ids = [i for i in (fakt_ids or []) if i is not None]
    if not fakt_ids or not _tabelle_vorhanden(conn):
        return {}
    platzhalter = ",".join("?" * len(fakt_ids))
    zeilen = conn.execute(
        f"SELECT fakt_id, fingerprint, status, quelle, quelle_stufe, url, referenz, "
        f"geprueft_am, notiz FROM fakt_verifikation "
        f"WHERE fakt_art=? AND fakt_id IN ({platzhalter})",
        [fakt_art, *fakt_ids],
    ).fetchall()
    out: dict[int, dict] = {}
    for z in zeilen:
        d = dict(z) if not isinstance(z, dict) else z
        out[d["fakt_id"]] = d
    return out


def trust_des_fakts(verifikation: dict | None, zeile, fakt_art: str,
                    fallback: str = "unverified_db") -> str:
    """Trust-Stufe EINES Fakts.

    `verified` nur, wenn (1) eine Verifikation existiert, (2) ihr Status
    `verified` ist und (3) der Fingerprint noch zum aktuellen Inhalt passt.
    Sonst `fallback` — in der Praxis `unverified_db`.
    """
    if not verifikation:
        return fallback
    if (verifikation.get("status") or "").strip().lower() != STATUS_VERIFIED:
        return fallback
    erwartet = verifikation.get("fingerprint") or ""
    tatsaechlich = fingerprint(fakt_art, zeile)
    if erwartet != tatsaechlich:
        log.info("Verifikation fuer %s #%s verworfen: Inhalt hat sich seit der "
                 "Pruefung geaendert (Fingerprint passt nicht mehr).",
                 fakt_art, verifikation.get("fakt_id"))
        return fallback
    return "verified"


def annotiere(conn: sqlite3.Connection, fakt_art: str, zeilen: list[dict]) -> list[dict]:
    """Haengt jedem Fakt-Dict seine Verifikation an (`_verifikation`, `_trust`).

    Wird von `app/database.py::get_baureihe` benutzt, damit `app/evidence.py`
    ohne eigene Datenbankverbindung pro Fakt entscheiden kann.
    """
    if not zeilen:
        return zeilen
    _tab, idspalte, _spalten = FAKT_ARTEN[fakt_art]
    verifikationen = lade_verifikationen(conn, fakt_art,
                                         [z.get(idspalte) for z in zeilen])
    for z in zeilen:
        v = verifikationen.get(z.get(idspalte))
        z["_verifikation"] = v
        z["_trust"] = trust_des_fakts(v, z, fakt_art)
    return zeilen
