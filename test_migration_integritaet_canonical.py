"""
AF-09 -- Integritaetspruefung der Datenmigrationen kennt canonical-only Rueckrufe (RC-W6).

    python scripts/test_isolated.py test_migration_integritaet_canonical.py

Die Regel "rueckruf ohne baureihe" zaehlte jede Zeile mit baureihe_id NULL als Waise. Seit RC-W6 sind
NULL-Zeilen mit vollstaendiger canonical-Zuordnung (canonical_make + canonical_nameplate) gewollt.
Geprueft ueber den TATSAECHLICHEN Migrationspfad (`fuehre_migration_aus`, echter Schritt
`schritt_motor_geltung`), auf der Temp-DB des Isolationsrunners (Seed + Migrationen).
"""
import hashlib
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import app.data_migrations as dm
from app.config import DB_PATH

FEHLER: list[str] = []


def check(name, ok, info=""):
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}" + (f"  -> {info}" if not ok and info else ""))
    if not ok:
        FEHLER.append(name)


MARKER = dm.MARKER_MOTOR_GELTUNG
SCHRITTE = dm.SCHRITTE_MOTOR_GELTUNG
c = sqlite3.connect(str(DB_PATH), isolation_level=None)
c.execute("pragma foreign_keys=ON")


def inhalt(tabelle, cols="*"):
    rows = c.execute(f'select {cols} from "{tabelle}" order by 1').fetchall()
    return len(rows), hashlib.sha256(repr(rows).encode()).hexdigest()


def zuruecksetzen():
    """Zustand wie in Produktion vor der Migration: Marker weg, Geltungsdaten und neue Variante weg."""
    c.execute("delete from schema_migrations where name=?", (MARKER,))
    c.execute("delete from motorvariante_geltung")
    c.execute("delete from motorvariante where variante_id='bmw-1er-f20-f21-116i-lci'")


def marker():
    return c.execute("select 1 from schema_migrations where name=?", (MARKER,)).fetchone() is not None


def canonical_zeile(make="MAZDA", plate="CX-5", kba="TEST-AF09-1"):
    c.execute("insert into rueckruf (baureihe_id, mangel, kba_referenz, canonical_make, canonical_nameplate) "
              "values (NULL, 'Testmangel', ?, ?, ?)", (kba, make, plate))


def lauf():
    dm.protokoll.clear()
    return dm.fuehre_migration_aus(c, MARKER, SCHRITTE)


print("=== A) gueltige canonical-only Rueckrufe blockieren die Migration nicht ===")
zuruecksetzen()
for i in range(25):
    canonical_zeile(kba=f"TEST-AF09-{i}")
rr_vorher = inhalt("rueckruf")
n_null = c.execute("select count(*) from rueckruf where baureihe_id is null").fetchone()[0]
check("A0 Vorbedingung: canonical-only Zeilen vorhanden (baureihe_id NULL)", n_null >= 25)
check("A1 die Integritaetspruefung meldet keinen Fehler", dm.pruefe_integritaet(c) == [], str(dm.pruefe_integritaet(c)))
ok = lauf()
check("A2 die Migration wird angewendet, Marker gesetzt", ok is True and marker())
check("A3 Geltungsdaten erzeugt: 4 Zeilen, neue 116i vorhanden",
      c.execute("select count(*) from motorvariante_geltung").fetchone()[0] == 4
      and c.execute("select count(*) from motorvariante where variante_id='bmw-1er-f20-f21-116i-lci'").fetchone()[0] == 1)
check("A4 Rueckrufbestand inhaltlich unveraendert (Anzahl UND Inhalts-Hash)", inhalt("rueckruf") == rr_vorher)
rr_nach = inhalt("rueckruf"); geltung_nach = inhalt("motorvariante_geltung"); mv_nach = inhalt("motorvariante")
check("A5 zweiter Lauf ist ein No-op", lauf() is False and inhalt("motorvariante_geltung") == geltung_nach
      and inhalt("motorvariante") == mv_nach and inhalt("rueckruf") == rr_nach)

print("\n=== B) echte unbekannte Baureihenreferenz blockiert weiterhin ===")
zuruecksetzen()
c.execute("pragma foreign_keys=OFF")
c.execute("insert into rueckruf (baureihe_id, mangel) values ('gibt-es-nicht', 'Testmangel')")
c.execute("pragma foreign_keys=ON")
vorher = (inhalt("rueckruf"), inhalt("motorvariante"), inhalt("motorvariante_geltung"))
fehler = dm.pruefe_integritaet(c)
check("B1 Prueffehler 'rueckruf mit unbekannter baureihe'", any(f.startswith("rueckruf mit unbekannter baureihe=1") for f in fehler), str(fehler))
ok = lauf()
check("B2 Migration abgebrochen, kein Marker", ok is False and not marker())
check("B3 keine teilweisen Migrationsdaten (Rueckruf, Motorvarianten, Geltung unveraendert)",
      (inhalt("rueckruf"), inhalt("motorvariante"), inhalt("motorvariante_geltung")) == vorher)
c.execute("delete from rueckruf where baureihe_id='gibt-es-nicht'")

print("\n=== C) fehlende erforderliche canonical-Zuordnung wird abgefangen ===")
# Das Tabellenschema (CHECK) verhindert das normalerweise; der Prueffehler ist der doppelte Boden
# (z.B. fuer eine per Tabellen-Rebuild entstandene Altdatenbank ohne CHECK). Fixture ohne CHECK:
zuruecksetzen()
c.execute("pragma foreign_keys=OFF")
c.execute("alter table rueckruf rename to rueckruf_orig")
c.execute("create table rueckruf as select * from rueckruf_orig")           # ohne CHECK, gleiche Spalten
c.execute("insert into rueckruf (baureihe_id, mangel, canonical_make, canonical_nameplate) values (NULL, 'x', 'MAZDA', NULL)")
c.execute("insert into rueckruf (baureihe_id, mangel, canonical_make, canonical_nameplate) values (NULL, 'x', '  ', 'CX-5')")
fehler = dm.pruefe_integritaet(c)
check("C1 NULL-Zeilen mit unvollstaendiger canonical-Zuordnung (fehlend/leer) werden gemeldet",
      any("ohne vollstaendige canonical-Zuordnung=2" in f for f in fehler), str(fehler))
vorher = (inhalt("motorvariante"), inhalt("motorvariante_geltung"))
ok = lauf()
check("C2 Migration abgebrochen, kein Marker, keine teilweisen Daten",
      ok is False and not marker() and (inhalt("motorvariante"), inhalt("motorvariante_geltung")) == vorher)
c.execute("drop table rueckruf"); c.execute("alter table rueckruf_orig rename to rueckruf")
c.execute("pragma foreign_keys=ON")

print("\n=== D) NULL-Zeile ohne Zuordnung ist keine pauschale Ausnahme: das Schema weist sie ab ===")
try:
    c.execute("insert into rueckruf (baureihe_id, mangel) values (NULL, 'x')")
    check("D1 NULL ohne canonical vom Schema abgewiesen", False)
except sqlite3.IntegrityError:
    check("D1 NULL ohne canonical vom Schema abgewiesen", True)

print("\n=== E) Aeltere Schemata ohne canonical-Spalten: alte Regel bleibt ===")
check("E1 Spaltenerkennung", dm._hat_canonical_spalten(c) is True)
c2 = sqlite3.connect(":memory:")
c2.execute("create table rueckruf (id integer primary key, baureihe_id text, mangel text)")
check("E2 ohne canonical-Spalten gilt eine NULL-Zeile weiterhin als Fehler",
      dm._hat_canonical_spalten(c2) is False)

print("\n=== F) Uebrige Integritaetspruefungen unveraendert ===")
zuruecksetzen()
check("F1 sauberer Bestand: keine Fehler", dm.pruefe_integritaet(c) == [])
c.execute("pragma foreign_keys=OFF")
c.execute("insert into motorvariante (variante_id, baureihe_id, bezeichnung, kraftstoff) values ('x-waise','gibt-es-nicht','x','Benzin')")
c.execute("pragma foreign_keys=ON")
check("F2 Motorvariante ohne Baureihe wird weiter gemeldet",
      any(f.startswith("motorvariante ohne baureihe=1") for f in dm.pruefe_integritaet(c)))
c.execute("delete from motorvariante where variante_id='x-waise'")
lauf()   # Endzustand: Migration angewendet

print()
if FEHLER:
    print(f"{len(FEHLER)} Pruefung(en) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle AF-09-Pruefungen bestanden.")
