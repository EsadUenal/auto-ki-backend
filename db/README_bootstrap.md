# Fahrzeugdaten-Bootstrap

Wie ein VIRA-Backend zu seinem Fahrzeugdatenbestand kommt — und warum es zwei
getrennte Schemadateien gibt.

## Zwei Datenwelten, strikt getrennt

| | Tabellen | Schema | Herkunft |
|---|---|---|---|
| **App-/Nutzerdaten** | `users`, `checks`, `conversations`, `messages`, `einwilligung`, `dealer_vehicle`, Bestellungen … | `app/database.py::_SCHEMA_SQL` | entsteht im Betrieb |
| **Fahrzeugdaten** (produktneutral) | `baureihe`, `motorvariante`, `ausstattungslinie`, `quelle`, `rueckruf`, `schwachstelle_baureihe`, `schwachstelle_motor`, `kritische_wartung` | `db/schema.sql` | kommt aus dem Repo |

Die beiden Fremdschlüssel-Graphen berühren sich nicht: alles Fahrzeugbezogene
hängt an `baureihe`, alles Nutzerbezogene an `users`. Genau diese Trennung macht
einen Seed überhaupt verantwortbar.

## Startreihenfolge

`app/database.py::ensure_tables()` läuft beim App-Start und arbeitet in dieser
Reihenfolge ab:

```
1. App-Schema            _SCHEMA_SQL
2. Fahrzeug-Schema       db/schema.sql              (app/fahrzeug_seed.py)
3. Spalten-Migrationen   _migrate_schema            → baureihe.verification
4. App-Seeds             Poster / Ebook / chassis_codes
5. Fahrzeug-Seed         db/seed_fahrzeugdaten.sql  (nur wenn Bestand leer)
6. Datenmigrationen      app/data_migrations.py
7. App bereit
```

Die Reihenfolge ist nicht beliebig:

* **2 vor 3** — `_migrate_schema` hängt `verification` per `ALTER TABLE` an
  `baureihe`; die Tabelle muss dafür existieren.
* **5 nach 3** — der Seed liefert genau diese Spalte mit.
* **6 zuletzt** — die P0-Korrekturen sollen auf einem vorhandenen Bestand greifen.

## Bestehende Datenbank (Produktion)

Es wird **nichts** überschrieben. Der Seed läuft ausschließlich, wenn *alle acht*
Fahrzeugtabellen leer sind. Ein halb befüllter Bestand führt zum Überspringen mit
Logmeldung, nicht zum Vermischen zweier Stände. Schritte 2 und 3 sind auf einer
bestehenden Datenbank No-Ops (`CREATE ... IF NOT EXISTS`).

Auf einer bestehenden DB ändert der Bootstrap deshalb nur eine einzige Sache: er
trägt die Marker in `schema_migrations` nach.

## Frisches Deployment

Railway startet den Container mit `AUTO_KI_DB_PATH=/data/auto_ki.db` auf einem
persistenten Volume (siehe `Dockerfile`).

* **Bestehendes Volume** → Daten bleiben, nur Migrationen laufen.
* **Leeres Volume** → Schema, voller Fahrzeugbestand und P0-Korrekturen entstehen
  automatisch beim ersten Start. Kein manuelles `db/init_db.py` mehr nötig.

Wichtig: `.dockerignore` schließt die `*.db`-Dateien aus, **nicht** das
Verzeichnis `db/` — sonst fehlten dem Image Schema und Seed.

## Seed neu erzeugen

Nach Datenkorrekturen wird der kanonische Seed neu exportiert:

```bash
python db/export_fahrzeug_seed.py --pruefen
```

```bash
python db/export_fahrzeug_seed.py
```

Das Skript sichert dreifach ab:

1. **Allowlist aus dem Schema** — exportiert wird nur, was `db/schema.sql`
   definiert. Nutzertabellen stehen in einer anderen Datei und können nicht
   versehentlich hineinrutschen.
2. **Denylist-Gegenprobe** — bricht ab, falls doch eine Nutzertabelle auftaucht.
3. **Round-Trip-Beweis** — der erzeugte Seed wird in eine frische Datenbank
   geladen und Zeile für Zeile gegen die Quelle verglichen. Erst dann wird
   geschrieben.

Zusätzlich prüft es die Zeilenmengen gegen die erwartete Größenordnung und stoppt
bei grober Abweichung, damit nicht versehentlich eine falsche Quelldatenbank
exportiert wird.

## Datenkorrekturen nachziehen

Korrekturen an vorhandenen Daten gehören **nicht** in den Seed, sondern als
versionierte Migration nach `app/data_migrations.py` (Marker in
`schema_migrations`). Nur so erreichen sie auch Datenbanken, die den alten Stand
bereits geladen haben. Der Seed wird danach neu erzeugt, damit frische Installs
den korrigierten Stand direkt bekommen — beide Wege führen zum selben Ergebnis,
nachgewiesen in `test_fahrzeug_bootstrap.py`.

## Tests

```bash
python test_fahrzeug_bootstrap.py
```

Deckt ab: leere DB, bestehende DB, dreifache Idempotenz, Nachweis „keine
Nutzerdaten im Seed", Fremdschlüssel-Integrität, Vorhandensein der
P0-Korrekturen, und dass ein KaufCheck aus frisch gebootstrappter Datenbank
BMW G20 und Audi A3 8P auflöst, während ein DB-Miss ein DB-Miss bleibt.