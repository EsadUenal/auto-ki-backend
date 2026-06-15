# Auto-KI — Projektstand

Stand: 2026-06-15

---

## Aktive Datenbank

```
%LOCALAPPDATA%\auto-ki-backend\auto_ki.db
→ C:\Users\anony\AppData\Local\auto-ki-backend\auto_ki.db
```

Bewusst außerhalb von OneDrive (kein WAL-Sync-Risiko). Backup-Kopien landen in `db/backups/`.

---

## DB-Tabellen

| Tabelle | Zeilen | Zweck |
|---|---|---|
| `baureihe` | 421 | Fahrzeug-Baureihen (Marke/Modell/Generation) |
| `motorvariante` | 3 243 | Motorvarianten je Baureihe |
| `schwachstelle_baureihe` | 1 464 | Bekannte Baureihen-Schwachstellen |
| `schwachstelle_motor` | 2 753 | Motorspezifische Schwachstellen |
| `kritische_wartung` | 1 527 | Wartungsintervalle je Motor |
| `rueckruf` | 759 | KBA-Rückrufe |
| `ausstattungslinie` | 1 698 | Ausstattungslinien je Baureihe |
| `users` | — | Nutzerkonten (email, bcrypt-Hash, abo_typ) |
| `conversations` | — | Chat-Konversationen je Nutzer |
| `messages` | — | Nachrichten je Konversation |
| `checks` | — | Gespeicherte Kauf-/Verkaufs-Checks je Nutzer |

Alle Tabellen werden beim App-Start per `ensure_tables()` automatisch angelegt (CREATE IF NOT EXISTS).

---

## Backend-Endpunkte

**Basis-URL:** `http://localhost:8000/api/v1`

| Methode | Pfad | Auth | Funktion |
|---|---|---|---|
| POST | `/auth/register` | — | Nutzer registrieren |
| POST | `/auth/login` | — | Einloggen (setzt httpOnly-Cookie) |
| GET | `/auth/me` | Cookie | Eingeloggten Nutzer abrufen |
| POST | `/auth/logout` | Cookie | Ausloggen (löscht Cookie) |
| GET | `/conversations` | Cookie | Alle Konversationen des Nutzers |
| POST | `/conversations` | Cookie | Neue Konversation anlegen |
| GET | `/conversations/{id}` | Cookie | Konversation mit Nachrichten |
| PATCH | `/conversations/{id}` | Cookie | Titel aktualisieren |
| DELETE | `/conversations/{id}` | Cookie | Konversation löschen |
| POST | `/conversations/{id}/messages` | Cookie | Nachricht anhängen |
| GET | `/checks` | Cookie | Alle gespeicherten Checks des Nutzers |
| POST | `/checks` | Cookie | Check speichern |
| GET | `/checks/{id}` | Cookie | Vollständigen Check laden |
| DELETE | `/checks/{id}` | Cookie | Check löschen |
| POST | `/chat` | API-Key | KI-Chat (Streaming SSE) |
| POST | `/kaufcheck` | API-Key | Kauf-Check analysieren |
| POST | `/verkaufscheck` | API-Key | Verkaufs-Check analysieren |
| POST | `/fahrzeug` | API-Key | Fahrzeug-Daten aus DB |
| GET | `/health` | — | Status + DB-Pfad + Tabellenliste |

Cookie-Auth: `auth_token` (JWT, httpOnly, SameSite=lax, 7 Tage).
API-Key-Auth: `Authorization: Bearer <AUTO_KI_API_KEY>`.

---

## Frontend-Bereiche

**URL:** `http://localhost:3000`  
**Stack:** React 18 + Vite + TypeScript + Tailwind CSS

| Route | Bereich | Status |
|---|---|---|
| `/login` | Login & Registrierung | fertig |
| `/chat` | KI-Chat mit Streaming | fertig, Verlauf persistent |
| `/kaufcheck` | Kauf-Check-Formular + Bericht | fertig, Ergebnis persistent |
| `/verkaufscheck` | Verkaufs-Check + Preisspanne | fertig, Ergebnis persistent |
| `/entdecken` | Auto-Karten mit KI-Fragen | fertig |

Sidebar: Navigationslinks · Chat-Verlauf (aus DB) · Meine Checks (Kauf/Verkauf) · User-Footer mit Logout.

---

## Offene TODOs (Phase 2c / 3)

- [ ] **Abonnements & Bezahlung (Phase 2c):** `abo_typ` + `checks_verbleibend` in `users` sind vorbereitet, aber noch inaktiv — Stripe-Integration fehlt
- [ ] **Passwort-Reset:** Kein „Passwort vergessen"-Flow
- [ ] **E-Mail-Verifikation:** Registrierung ohne Bestätigungs-Mail
- [ ] **HTTPS + sichere Cookie-Flags:** `secure=True` in Cookie-Opts, sobald TLS aktiv
- [ ] **JWT_SECRET in Produktion:** Aktuell `dev-jwt-secret-change-in-prod` — vor Go-Live durch langen Zufalls-String ersetzen (`openssl rand -hex 32`)
- [ ] **Check-Löschung im Frontend:** `DELETE /checks/{id}` ist implementiert, aber kein UI-Button
- [ ] **Konversation löschen im Frontend:** `DELETE /conversations/{id}` ist implementiert, aber kein UI-Button
- [ ] **Bilder/Screenshots in Checks:** Werden aktuell nicht mit gespeichert (bewusst, DB-Größe)
