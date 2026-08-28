# VIRA Working Rules — auto-ki-backend

Diese Datei gilt für alle Agenten (Codex, Claude Code, andere), die in diesem
Repository arbeiten. Sie beschreibt verbindliche Regeln, keine Empfehlungen.

## Git Safety
- Niemals direkt auf `master` entwickeln.
- Vor jeder Änderung `git fetch` und `git status` prüfen.
- Immer auf einem Feature-Branch arbeiten.
- Kein Force Push.
- Kein Rebase, kein Squash ohne ausdrückliche Anweisung des Nutzers.
- Niemals automatisch nach `master` mergen — Merges ordnet der Nutzer an.
- Working Tree respektieren; bestehende Nutzeränderungen nicht überschreiben.
- Keine Branches löschen. Alle Feature-Branches bleiben erhalten.

## Scope
- Nur die angeforderte Aufgabe bearbeiten.
- Kein ungefragtes Refactoring.
- Kein Scope Creep, keine Nebenfeatures.
- Fällt unterwegs etwas auf, das nicht zur Aufgabe gehört: melden, nicht bauen.

## Data Safety
- Keine Fahrzeugfakten erfinden. Nichtwissen wird als Nichtwissen ausgewiesen.
- Ungeprüfte Daten niemals als `verified` behandeln oder kennzeichnen.
- Das Trust-System respektieren (`app/fakt_verifikation.py`,
  `app/motor_applicability.py`, `app/empfehlungs_floor.py`).
- Als `rejected` markierte Fakten nicht wieder sichtbar machen
  (`ist_gesperrt` / `sichtbare_fakten` sind bindend).
- Bei Rückrufen ausschließlich belastbare Quellen (KBA-Rückrufdatenbank,
  Herstellerprimärquellen). Fachpresse begründet keinen `verified`-Status.
- Ein Baujahr-Treffer allein begründet niemals `variant_match` — nur eine
  echte Variantenbedingung tut das (siehe `rueckruf_applicability`).
- Keine Captcha-Umgehung. Keine unerlaubten Scraper bauen.
- Marktpreise dürfen bei fehlenden Live-Daten nicht erfunden werden;
  fehlende Daten führen zu `research_failed`, nicht zu einem Schein-Median.

## Secrets
- Niemals API-Keys ausgeben, in Logs schreiben oder in Testmeldungen einbetten.
- `.env`, `.env.stripe`, `wiederherstellungspasswort.txt` sind gitignored und
  bleiben es. Keine `.env`-Inhalte loggen.
- Secrets niemals committen.

## Testing
- Bestehende Tests vor und nach sicherheitskritischen Änderungen ausführen.
- Tests nicht abschwächen, nur damit sie grün werden. Wenn ein Test die falsche
  Aussage sichert, wird er auf die richtige Aussage umgestellt — nicht entschärft.
- Externe Ausfälle (Gemini 503, Tavily nicht erreichbar, Timeouts) getrennt von
  echten Assertion-Fehlern berichten.

## Database
- Vor jeder Datenmigration ein Backup der Live-DB anlegen.
- Migrationen müssen idempotent sein (explizite IDs, Selbstheilung).
- Seed, Bootstrap und Seed-Drift-Guard beachten: `app/fahrzeug_seed.py`,
  `db/seed_fahrzeugdaten.sql`, `app/data_migrations.py`, `test_seed_drift.py`.
- Die Live-DB liegt außerhalb des Repos unter `%LOCALAPPDATA%\auto-ki-backend`.
  Nicht direkt hineinschreiben, wenn ein Backend-Prozess läuft.
- Keine User-/PII-Tabellen anfassen, wenn Fahrzeugdaten geändert werden.

## Reporting
Nach größeren Aufgaben berichten:
- was geändert wurde
- Tests (grün / rot / extern blockiert)
- P0
- P1
- Git-Stand (Branch, Commit)
- genau ein empfohlener nächster Schritt

## Setup & Commands

```powershell
# Abhängigkeiten
python -m pip install -r requirements.txt

# Server starten (Port 8000)
python -m uvicorn app.main:app --port 8000

# Volle Testsuite
python -m pytest -q

# Einzelner Test
python -m pytest test_kaufcheck.py -q
```

Hinweis: Das README nennt `--reload`. Im laufenden Betrieb wird das Backend
bewusst **ohne** `--reload` gestartet; nach Code-Änderungen muss der Prozess
manuell neu gestartet werden.

Die Testdateien liegen als `test_*.py` im Repo-Wurzelverzeichnis. Es gibt keine
pytest-Konfigurationsdatei; pytest wird aus dem Repo-Root aufgerufen.

## Architektur — wichtige Dateien

| Bereich | Pfad |
|---|---|
| App Entry Point | `app/main.py` |
| Konfiguration / ENV | `app/config.py` |
| Router | `app/routers/` (u. a. `kaufcheck.py`, `verkaufscheck.py`, `checks.py`) |
| KaufCheck-Logik | `app/kaufcheck.py`, `app/check_gate.py`, `app/pruefplan_basis.py` |
| Evidence | `app/evidence.py` |
| Recommendation Floor | `app/empfehlungs_floor.py`, `app/kaufempfehlung_sync.py` |
| Trust-System | `app/motor_applicability.py`, `app/vehicle_identity.py` |
| Fakt-Verifikation | `app/fakt_verifikation.py` |
| Recall / KBA | `app/recall_filter.py`, `app/kba_reconciliation.py`, `app/kba_import_batch_a.py`, `app/kba_import_batch_b1.py`, `app/kba_generation_audit.py`, `app/kba_generation_quellen.py` |
| Data Migrations | `app/data_migrations.py` |
| Fahrzeugdaten-Seed | `app/fahrzeug_seed.py`, `db/seed_fahrzeugdaten.sql`, `db/README_bootstrap.md` |
| DB-Zugriff / Schema | `app/database.py`, `app/models.py`, `db/schema.sql` |
| Markt / Preis | `app/marktvergleich.py`, `app/marktrecherche.py`, `app/preisurteil.py`, `app/market_data_provider.py` |
| LLM | `app/llm.py`, `app/gemini_retry.py` |
| Auth / Abo | `app/auth.py`, `app/entitlements.py`, `app/dealer.py` |
| Tests | `test_*.py` im Repo-Root; `tests/golden_questions.py` |

## Environment

Erwartet wird `.env` im Repo-Root (gitignored), Vorlage: `.env.example`.
Zusätzlich optional `.env.stripe` (Vorlage `.env.stripe.example`).

Benötigte Variablennamen (Werte niemals ausgeben):
`GEMINI_API_KEY`, `TAVILY_API_KEY`, `AUTO_KI_JWT_SECRET`, `AUTO_KI_API_KEY`,
`AUTO_KI_DB_PATH`, `AUTO_KI_CHROMA_PATH`, `AUTO_KI_DB_BACKUP_DIR`,
`AUTO_KI_DB_BACKUP_INTERVAL_SECONDS`, `AUTO_KI_CORS_ORIGINS`,
`AUTO_KI_RATE_LIMIT`, `AUTO_KI_LOG_LEVEL`, `AUTO_KI_LLM_MODEL`,
`AUTO_KI_FAST_LLM_MODEL`, `AUTO_KI_JWT_EXPIRE_DAYS`,
`AUTO_KI_ALLOWED_MARKET_SOURCES`, `FRONTEND_URL`,
`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_LIGHT`,
`STRIPE_PRICE_PRO`, `STRIPE_PRICE_MAX`, `STRIPE_PRICE_EINZELKAUF`,
`MOBILE_DE_USERNAME`, `MOBILE_DE_PASSWORD`, `MOBILE_DE_BASE_URL`.

## Current Project State

Stand: 2026-08-28. Backend-`master` = `origin/master` = **`f7e4bef`**.

- KaufCheck-Backend ist weitgehend fertig; vier Prüfbereiche vorhanden.
- Produktivmodell: **Gemini 3.7 Flash** (`AUTO_KI_LLM_MODEL`, Default
  `gemini-3.7-flash`).
- KaufCheck-Frontend ist neu strukturiert (Repo `auto-ki-web`, `master` = `494e698`).
- Marktpreis: Bei fehlenden Live-Daten darf **kein** Preis erfunden werden.
  `ALLOWED_MARKET_SOURCES` ist im Produktionsdefault leer; Etappe 2
  (Marktquellen-Qualifikation) ist weiterhin PENDING.
- Fact-Level Trust vorhanden: `verified` / `partially_verified` / `unverified` /
  `rejected`. `rejected` sperrt den Fakt (`ist_gesperrt`).
- KBA-/Recall-Daten wurden umfangreich bereinigt (558 erfundene Referenzen
  entfernt). Batch A und Batch B1 sind auf `master`.
- Bestand: 1.073 Rückrufe, davon 342 amtlich verifiziert; 416 Baureihen.
- Offen: 149 SOURCE_UNCLEAR-Zeilen, 39 Mischziel-Zeilen, Etappe 2 PENDING,
  Frontend kennt `trust`/`fakt_verifikation`/Rejected-Sperre noch nicht.

**Die nächste Datenbankarbeit NICHT automatisch starten.**
Die nächste Aufgabe kommt immer ausdrücklich vom Nutzer.
