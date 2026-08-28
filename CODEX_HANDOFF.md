# VIRA — Codex Handoff

Kompakter Einstieg für Agenten. Verbindliche Regeln stehen in `AGENTS.md`
(je Repo). Diese Datei ist die Kurzreferenz, keine Projektdokumentation.

## Repositories (absolute Pfade, Windows)

| Repo | Pfad | Remote |
|---|---|---|
| Backend | `C:\Users\anony\OneDrive\Esad Ünal\03_VIRA\auto-ki-backend` | https://github.com/EsadUenal/auto-ki-backend.git |
| Frontend | `C:\Users\anony\.claude\sessions\auto-ki-web` | https://github.com/EsadUenal/auto-ki-web.git |

Stand 2026-08-28:
- Backend `master` = `origin/master` = **`f7e4bef`**, Working Tree clean.
- Frontend `master` = `origin/master` = **`494e698`**, Working Tree clean.

Feature-Branches (alle erhalten, alle gepusht):
- Backend: `recall-kba-b1-import`, `recall-kba-open-generation-audit`,
  `recall-kba-missing-import`, `recall-kba-full-reconciliation`,
  `recall-pilot-insignia-012223`, `recall-verification-pilot`,
  `vehicle-verification-pilot`, `data-trust-p0-cleanup`, `data-trust-runtime`,
  `kaufcheck-planb`, `etappe1-market-trust`, `etappe2-market-sources`,
  `etappe3-mobile-provider`
- Frontend: `kaufcheck-ui-final`

## Startbefehle

Backend (Port 8000):
```powershell
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --port 8000
```
Bewusst ohne `--reload` (das README nennt `--reload`; im Betrieb wird der
Prozess nach Code-Änderungen manuell neu gestartet).

Frontend (Port 3000):
```powershell
npm install
npm run dev
```

## Testbefehle

Backend (Testdateien liegen als `test_*.py` im Repo-Root, keine pytest-Konfig):
```powershell
python -m pytest -q              # volle Suite
python -m pytest test_kaufcheck.py -q
python tests/golden_questions.py # Golden Questions gegen laufenden Server
```

Frontend (kein test-/lint-Script; `build` ist die Typprüfung):
```powershell
npm run build
```

## Architekturübersicht

Backend — FastAPI + SQLite + ChromaDB. Harte Fakten kommen aus der DB, das LLM
formuliert nur.

- Entry: `app/main.py` · Config/ENV: `app/config.py`
- Router: `app/routers/` (`kaufcheck.py`, `verkaufscheck.py`, `checks.py`,
  `chat.py`, `fahrzeug.py`, `ersatzteile.py`, `dealer.py`, `payments.py`,
  `user_auth.py`, `admin.py`, …)
- KaufCheck: `app/kaufcheck.py`, `app/check_gate.py`, `app/pruefplan_basis.py`
- Evidence: `app/evidence.py` · Key Findings: `app/key_findings.py`
- Floor: `app/empfehlungs_floor.py`, `app/kaufempfehlung_sync.py`
- Trust: `app/motor_applicability.py`, `app/vehicle_identity.py`
- Fakt-Verifikation: `app/fakt_verifikation.py`
- Recall/KBA: `app/recall_filter.py`, `app/kba_reconciliation.py`,
  `app/kba_import_batch_a.py`, `app/kba_import_batch_b1.py`,
  `app/kba_generation_audit.py`, `app/kba_generation_quellen.py`
- Migrationen: `app/data_migrations.py`
- Seed: `app/fahrzeug_seed.py`, `db/seed_fahrzeugdaten.sql`, `db/README_bootstrap.md`
- DB: `app/database.py`, `app/models.py`, `db/schema.sql`
- Markt/Preis: `app/marktvergleich.py`, `app/marktrecherche.py`,
  `app/preisurteil.py`, `app/market_data_provider.py`, `app/mobile_de_provider.py`
- LLM: `app/llm.py`, `app/gemini_retry.py` — Produktivmodell Gemini 3.7 Flash

Live-Datenbank liegt **außerhalb** des Repos unter
`%LOCALAPPDATA%\auto-ki-backend\auto_ki.db` (dort auch die Backups).
Schema, Fahrzeugbestand und Migrationen legt `app.main` beim Start selbst an.

Frontend — React 18 + Vite + TypeScript + Tailwind, React Router.

- Entry: `src/main.tsx` · Shell/Routing: `src/App.tsx`, `src/components/Sidebar.tsx`
- KaufCheck: `src/components/KaufCheckView.tsx`, `KaufCheckDetails.tsx`
- Ergebnis: `ResultSummary.tsx`, `KeyFindings.tsx`, `EvidenceWhy.tsx`, `SourceBadge.tsx`
- API: `src/api/client.ts` · Auth/State: `src/context/AuthContext.tsx`, `PrivateRoute.tsx`
- Typen: `src/types.ts` · Styling: `src/index.css`, `tailwind.config.js`

## Environment

Backend: `.env` im Repo-Root (gitignored), Vorlage `.env.example`; optional
`.env.stripe`. Frontend: `.env.local` (gitignored), Vorlage `.env.local.example`.
Variablennamen siehe jeweilige `AGENTS.md`. **Werte niemals ausgeben oder loggen.**

## Safety-Regeln (Kurzfassung)

- Nie direkt auf `master`; Feature-Branch, kein Force Push, kein Auto-Merge.
- Nur die angeforderte Aufgabe; kein ungefragtes Refactoring.
- Keine Fahrzeugfakten erfinden; ungeprüft ≠ `verified`; `rejected` bleibt gesperrt.
- Baujahr-Deckung allein begründet nie `variant_match`.
- Kein Marktpreis ohne Live-Daten — lieber `research_failed` als Schein-Median.
- Rückrufe nur aus KBA/Herstellerprimärquellen; keine Captcha-Umgehung,
  keine unerlaubten Scraper.
- Vor Datenmigrationen Backup; Migrationen idempotent; PII-Tabellen nicht anfassen.
- Tests nicht abschwächen; externe Ausfälle getrennt von Assertion-Fehlern melden.
- Secrets nie ausgeben, nie committen.

## Typische Workflow-Reihenfolge

1. `git fetch` + `git status` in beiden Repos; Stand gegen `f7e4bef` /
   `494e698` abgleichen, Abweichungen melden statt korrigieren.
2. Aufgabe klären und eng abgrenzen. Bei Unklarheit fragen, nicht raten.
3. Feature-Branch von `master` anlegen.
4. Bei Datenarbeit: Backup der Live-DB, dann idempotente Migration.
5. Ändern — nur im vereinbarten Umfang.
6. Backend `python -m pytest -q`, Frontend `npm run build`.
7. `git diff` prüfen: nur die beabsichtigten Dateien, keine Secrets.
8. Berichten: Änderungen, Tests, P0, P1, Git-Stand, genau ein nächster Schritt.
9. Commit/Push/Merge nur auf ausdrückliche Anweisung des Nutzers.

## Bekannte offene Punkte

- Etappe 2 (Marktquellen-Qualifikation) PENDING, `ALLOWED_MARKET_SOURCES` leer.
- 149 SOURCE_UNCLEAR-Rückrufzeilen unbelegt; 39 Mischziel-Zeilen fehlen.
- Rückrufbestand 1.073 Zeilen, davon 342 amtlich belegt.
- Frontend wertet `trust`, `fakt_verifikation` und Rejected-Sperre noch nicht aus.

**Keine Datenbankarbeit automatisch starten. Die nächste Aufgabe kommt
ausdrücklich vom Nutzer.**
