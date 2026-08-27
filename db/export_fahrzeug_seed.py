"""
Erzeugt den kanonischen Fahrzeug-Seed `db/seed_fahrzeugdaten.sql`.

    python db/export_fahrzeug_seed.py                 # aus der konfigurierten Live-DB
    python db/export_fahrzeug_seed.py --db pfad.db    # aus einer bestimmten DB
    python db/export_fahrzeug_seed.py --pruefen       # nur pruefen, nichts schreiben

WARUM ES DIESES SKRIPT GIBT
---------------------------
Der Fahrzeugdatenbestand lag bisher ausschliesslich in der lokalen bzw. der
Produktions-SQLite-Datei — weder im Repository noch im Docker-Image. Ein frischer
Server bekam damit ueberhaupt keine Fahrzeuge. Dieses Skript ueberfuehrt den
jeweils aktuellen, korrigierten Stand kontrolliert in eine versionierbare
Textdatei; `app/fahrzeug_seed.py` laedt sie beim ersten Start.

DREI SICHERUNGEN, DIE HIER FEST VERDRAHTET SIND
-----------------------------------------------
1. ALLOWLIST AUS DEM SCHEMA, NICHT AUS EINER LISTE IM KOPF.
   Exportiert wird ausschliesslich, was `db/schema.sql` als Tabelle definiert —
   und das sind per Konstruktion nur die acht produktneutralen Fahrzeugtabellen.
   Die App-/Nutzertabellen stehen in `app/database.py::_SCHEMA_SQL`, einer
   voellig getrennten Datei. Eine Nutzertabelle kann hier also nicht durch einen
   Tippfehler hineinrutschen.

2. HARTE DENYLIST-PRUEFUNG.
   Zusaetzlich wird gegen die tatsaechlich in der Quelldatenbank vorhandenen
   Tabellen geprueft: taucht irgendeine Tabelle mit Nutzerbezug in der Allowlist
   auf, bricht der Export ab. Doppelter Boden, absichtlich redundant.

3. ROUND-TRIP-BEWEIS.
   Die erzeugte Datei wird in eine frische Datenbank geladen und Zeile fuer Zeile
   gegen die Quelle verglichen. Erst wenn jede Zeile jeder Tabelle identisch
   zurueckkommt, wird geschrieben. Damit ist das Quoting nicht "vermutlich
   richtig", sondern nachgewiesen.
"""
import argparse
import os
import re
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(BASE, "db", "schema.sql")
SEED_PATH = os.path.join(BASE, "db", "seed_fahrzeugdaten.sql")

# Reihenfolge nach Fremdschluessel-Abhaengigkeit: Eltern vor Kindern. Ein rein
# alphabetischer Export scheitert sofort (ausstattungslinie vor baureihe) —
# genau daran ist der erste Testlauf gescheitert.
FK_REIHENFOLGE = [
    "baureihe",
    "motorvariante",
    "ausstattungslinie",
    "quelle",
    "rueckruf",
    "schwachstelle_baureihe",
    "schwachstelle_motor",
    "kritische_wartung",
    # Zuletzt: verweist per (fakt_art, fakt_id) auf die Faktentabellen oben. Kein
    # echter Fremdschluessel, aber fachlich abhaengig — deshalb ans Ende.
    "fakt_verifikation",
]

# Stabile Sortierung je Tabelle -> reproduzierbare Datei, minimale Diffs.
SORTIERUNG = {
    "baureihe": "id",
    "motorvariante": "variante_id",
    # Stabile fachliche Ordnung statt der AUTOINCREMENT-Reihenfolge: so bleibt der
    # Seed-Diff auch dann klein, wenn Verifikationen in anderer Reihenfolge
    # eingetragen wurden.
    "fakt_verifikation": "fakt_art, fakt_id",
}

# Tabellen, die niemals in einen Seed duerfen. Wird gegen die ERMITTELTE
# Allowlist geprueft, nicht gegen eine Wunschliste.
VERBOTEN = {
    "users", "checks", "check_frage", "conversations", "messages",
    "einwilligung", "gespeicherte_adresse", "dealer_vehicle",
    "ebook_bestellung", "poster_bestellung", "stripe_events",
}

# Erwartete Groessenordnung (Stand P0-Cleanup). Nicht als Sollwert erzwungen,
# aber eine grobe Abweichung ist ein Alarmsignal und stoppt den Export.
ERWARTET = {
    "baureihe": 416, "motorvariante": 3231, "schwachstelle_baureihe": 1448,
    # rueckruf: 746 gewachsener Bestand + 269 Zeilen aus BATCH A (amtliche
    # KBA-Rueckrufe mit geschlossener Zielgeneration, app/kba_batch_a_daten.py).
    "schwachstelle_motor": 2750, "kritische_wartung": 1476, "rueckruf": 1015,
    "ausstattungslinie": 1677, "quelle": 0,
}
# Die Verifikationstabelle waechst mit jeder Pruefrunde und hat noch keine
# stabile Groessenordnung — sie steht bewusst nicht in ERWARTET.
TOLERANZ = 0.25


def allowlist() -> list[str]:
    """Die exportierbaren Tabellen — abgeleitet aus db/schema.sql."""
    quelle = open(SCHEMA_PATH, encoding="utf-8").read()
    gefunden = set(re.findall(r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+(\w+)", quelle, re.I))
    verboten = gefunden & VERBOTEN
    if verboten:
        raise SystemExit(f"ABBRUCH: db/schema.sql enthaelt Nutzertabellen: {sorted(verboten)}")
    unbekannt = gefunden - set(FK_REIHENFOLGE)
    if unbekannt:
        raise SystemExit(f"ABBRUCH: neue Tabelle(n) in db/schema.sql ohne FK-Reihenfolge: "
                         f"{sorted(unbekannt)} — FK_REIHENFOLGE ergaenzen")
    return [t for t in FK_REIHENFOLGE if t in gefunden]


def _literal(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, bytes):
        return "X'" + v.hex() + "'"
    return "'" + str(v).replace("'", "''") + "'"


def _zeilen(conn, tabelle, spalten=None):
    """Zeilen einer Tabelle in stabiler Reihenfolge.

    `spalten` erzwingt eine bestimmte Spaltenreihenfolge. Das wird fuer den
    Round-Trip gebraucht: `chassis_codes` steht im frischen Schema an Position 8,
    in einer gewachsenen Datenbank aber am Ende, weil sie dort per ALTER TABLE
    nachgezogen wurde. Die Spaltenmenge ist identisch, nur die Reihenfolge nicht —
    ein positioneller Vergleich wuerde faelschlich Alarm schlagen. Die INSERTs
    benennen ihre Spalten ohnehin explizit, die Reihenfolge ist also fachlich
    ohne Bedeutung.
    """
    cols = spalten or [r[1] for r in conn.execute(f'PRAGMA table_info("{tabelle}")')]
    order = SORTIERUNG.get(tabelle, "id" if "id" in cols else cols[0])
    rows = conn.execute(f'SELECT {",".join(cols)} FROM "{tabelle}" ORDER BY {order}').fetchall()
    return cols, rows


def erzeuge(conn, tabellen) -> tuple[str, dict]:
    teile = [
        "-- KANONISCHER FAHRZEUG-SEED — GENERIERT, NICHT VON HAND BEARBEITEN.\n",
        "-- Erzeugt von db/export_fahrzeug_seed.py; geladen von app/fahrzeug_seed.py\n",
        "-- beim ersten Start, wenn die Fahrzeugtabellen leer sind.\n",
        "--\n",
        "-- Enthaelt ausschliesslich produktneutrale Fahrzeugdaten. Keine Nutzer-,\n",
        "-- Check-, Zahlungs- oder Einwilligungsdaten — siehe Allowlist-Herleitung\n",
        "-- und Denylist-Pruefung im Exportskript.\n--\n",
    ]
    mengen = {}
    for t in tabellen:
        cols, rows = _zeilen(conn, t)
        mengen[t] = len(rows)
        teile.append(f"-- {t}: {len(rows)} Zeilen\n")
    teile.append("--\n\n")
    for t in tabellen:
        cols, rows = _zeilen(conn, t)
        teile.append(f"-- ── {t} ──\n")
        spalten = ",".join(cols)
        for row in rows:
            teile.append(f"INSERT INTO {t} ({spalten}) VALUES "
                         f"({','.join(_literal(v) for v in row)});\n")
        teile.append("\n")
    return "".join(teile), mengen


def round_trip(sql: str, conn_quelle, tabellen) -> None:
    """Laedt den erzeugten Seed in eine frische DB und vergleicht jede Zeile."""
    tmp = os.path.join(tempfile.mkdtemp(prefix="seed_rt_"), "rt.db")
    ziel = sqlite3.connect(tmp)
    try:
        ziel.executescript(open(SCHEMA_PATH, encoding="utf-8").read())
        # `verification` wird erst per _migrate_schema ergaenzt — fuer den
        # Round-Trip hier nachziehen, sonst schlaegt der Vergleich an einer
        # Spalte fehl, die es im frischen Schema noch gar nicht gibt.
        spalten = {r[1] for r in ziel.execute("PRAGMA table_info(baureihe)")}
        if "verification" not in spalten:
            ziel.execute("ALTER TABLE baureihe ADD COLUMN verification TEXT")
        ziel.execute("PRAGMA foreign_keys=ON")
        ziel.executescript(sql)
        ziel.commit()
        for t in tabellen:
            c_a, r_a = _zeilen(conn_quelle, t)
            c_ziel = [r[1] for r in ziel.execute(f'PRAGMA table_info("{t}")')]
            if sorted(c_a) != sorted(c_ziel):
                raise SystemExit(f"ABBRUCH Round-Trip: SpaltenMENGE von {t} weicht ab\n"
                                 f"  Quelle: {sorted(c_a)}\n  Seed  : {sorted(c_ziel)}")
            # Gleiche Spaltenreihenfolge erzwingen -> echter Wertvergleich.
            _, r_b = _zeilen(ziel, t, spalten=c_a)
            if r_a != r_b:
                for i, (x, y) in enumerate(zip(r_a, r_b)):
                    if x != y:
                        raise SystemExit(f"ABBRUCH Round-Trip: {t} Zeile {i} weicht ab\n"
                                         f"  Quelle: {x}\n  Seed  : {y}")
                raise SystemExit(f"ABBRUCH Round-Trip: {t} hat {len(r_a)} vs {len(r_b)} Zeilen")
        fk = ziel.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            raise SystemExit(f"ABBRUCH Round-Trip: {len(fk)} FK-Verletzung(en) im Seed")
        if ziel.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SystemExit("ABBRUCH Round-Trip: integrity_check fehlgeschlagen")
    finally:
        ziel.close()


def main() -> int:
    from app.config import DB_PATH
    p = argparse.ArgumentParser(description="Kanonischen Fahrzeug-Seed erzeugen")
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--pruefen", action="store_true", help="nur pruefen, nichts schreiben")
    args = p.parse_args()

    tabellen = allowlist()
    print(f"Quelle    : {args.db}")
    print(f"Allowlist : {tabellen}")

    conn = sqlite3.connect(args.db)
    vorhanden = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    fehlend = set(tabellen) - vorhanden
    if fehlend:
        raise SystemExit(f"ABBRUCH: Quelldatenbank kennt {sorted(fehlend)} nicht")
    kollision = set(tabellen) & VERBOTEN
    if kollision:
        raise SystemExit(f"ABBRUCH: Allowlist enthaelt Nutzertabellen: {sorted(kollision)}")

    sql, mengen = erzeuge(conn, tabellen)
    print("\nZeilenmengen:")
    for t in tabellen:
        soll = ERWARTET.get(t)
        ist = mengen[t]
        hinweis = ""
        if soll:
            abw = abs(ist - soll) / soll
            hinweis = f" (erwartet ~{soll}, Abweichung {abw:.0%})"
            if abw > TOLERANZ:
                raise SystemExit(f"ABBRUCH: {t} weicht mit {ist} zu stark von {soll} ab "
                                 f"— bitte pruefen, ob die richtige Quelle verwendet wird")
        print(f"  {t:24s} {ist:6d}{hinweis}")

    print("\nRound-Trip-Pruefung ...")
    round_trip(sql, conn, tabellen)
    print("  jede Zeile jeder Tabelle identisch zurueckgelesen, FK und Integritaet ok")

    if args.pruefen:
        print("\n--pruefen: nichts geschrieben.")
        return 0
    with open(SEED_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(sql)
    print(f"\ngeschrieben: {SEED_PATH} ({os.path.getsize(SEED_PATH)/1024/1024:.2f} MB, "
          f"{sum(mengen.values())} Datenzeilen)")
    return 0


if __name__ == "__main__":
    sys.exit(main())