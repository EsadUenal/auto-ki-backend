# Provider-Reliability und Kostenkontrolle

Stand: 2026-09-13. Diese Datei beschreibt Produktionsgrenzen, keine Preise.
API-Keys, Nutzertexte und Zahlungsdaten gehoeren weder hierhin noch in Logs.

## Provider-Bestand

| Feature | Endpoint/Entry | Provider | Modell/API | Normale logische Calls | Harte reale Call-Grenze |
|---|---|---|---|---:|---:|
| KI-Chat | `POST /api/v1/chat` | Gemini + optional Tavily | `LLM_MODEL`, Tavily Search | Gemini 1; Tavily nur bei Web-Trigger | Gemini 3, Tavily 9 |
| AutoFinder | `POST /api/v1/autofinder` | Gemini + optional Tavily | `LLM_MODEL`, Tavily Search | Gemini 0–3; Tavily 0–2 | Gemini 6, Tavily 6 |
| KaufCheck | `POST /api/v1/kaufcheck` | Gemini + optional Tavily | `LLM_MODEL`, Tavily Search/Extract | Gemini 1, bei abgeschnittenem JSON 2; Tavily adaptiv | Gemini 4, Tavily 16 |
| VerkaufsCheck | `POST /api/v1/verkaufscheck` | Gemini + optional Tavily | `LLM_MODEL`, Tavily Search/Extract | Gemini 1, bei abgeschnittenem JSON 2; Tavily adaptiv | Gemini 4, Tavily 16 |
| Inseratsoptimierung | `POST /api/v1/checks/{id}/inserat-optimierung` | Gemini | `LLM_MODEL` | 0 (Cache) oder 1 | Gemini 3 |
| Analyse-Rueckfrage | `POST /api/v1/analyse-frage` | Gemini | `LLM_MODEL` | 1 | Gemini 3 |
| Ersatzteile (geparkte Direkt-Route) | `POST /api/v1/ersatzteile/suche` | Gemini + optional Tavily | `LLM_MODEL`, Tavily Search | Gemini 0–1; Tavily gestuft | Gemini 3, Tavily 6 |
| Fahrzeug-Admin | `/api/v1/admin/entwurf`, `/batch`, `/luecken-entwurf` | Gemini | `LLM_MODEL`/`FAST_LLM_MODEL` | mehrstufige, validierte Fallbacks | Gemini 8 je Admin-Aktion |
| AutoFinder-Bildtool (offline) | `scripts/autofinder_generate_images.py` | Gemini Image | Job-Modell | 1 je Job, kein Retry | max. 20 Jobs je explizitem Batch |
| Marktprovider-Spike | `MobileDeSandboxProvider` | mobile.de Sandbox | Search API | Refdata + Search, kein Retry | kein Produktionsprovider; Timeout 20 s |

KBA-Bulk-URLs sind Datenimportquellen, keine Consumer-/Provider-Runtime. Stripe,
E-Mail und sonstige Infrastruktur sind nicht Teil der AI/Search-Kostenbudgets.

## Fehler- und Retry-Regeln

- Gemini: maximal 3 reale Versuche insgesamt. 429 ohne langfristigen Quota-Hinweis,
  Timeout, Netzwerkfehler sowie 500/502/503/504 sind kurz retrybar. 400, 401,
  403, andere permanente Fehler und erkannte Tages-/Langzeitquota werden nicht
  retryt. Unterschiedliche Fehlerklassen teilen dasselbe Budget.
- Tavily Search/Extract: maximal 3 reale Versuche pro Einzeloperation, zusaetzlich
  begrenzt vom Feature-Budget. Retry nur bei temporaerem 429, Netzwerk/Timeout
  und 5xx. Auth/400, ungueltige Antworten und erkennbare Quota-/Credit-Erschoepfung
  werden nicht retryt.
- Backoff ist exponentiell und gedeckelt. Gemini verwendet kleinen Jitter.
- Ein Aufrufbudget umfasst Originalaufrufe, Retries und Tavily Extract. Cache-
  Treffer verbrauchen keinen Provider-Call.

## Timeouts und Token-Grenzen

- Gemini SDK/Einzelcall: 45 s; gesamter Retry-Lauf: 75 s; Stream: 75 s.
- Tavily: Connect 5 s, Read 15 s, Gesamtcall 20 s.
- Gemini Output: Chat 2.048 Tokens, Analyse-Rueckfrage 2.048, Consumer-JSON-Bericht
  16.384, kleinere JSON-Helfer 4.096. Thinking ist bei JSON-Aufrufen deaktiviert.
- Gesamter Gemini-Eingabekontext: 120.000 Zeichen; Chat-Historie wird vom neuesten
  Ende her innerhalb dieses Budgets gehalten. Bestehende strengere Feldlimits
  bleiben unveraendert.
- Tavily `max_results`: maximal 20 pro Search-Call.

## Abuse-, Parallelitaets- und Credit-Schutz

- Hoechstens 32 Provider-Aktionen gleichzeitig global und 2 je pseudonymisiertem
  Nutzer-/IP-Key pro Worker. Der Key enthaelt nie das Cookie/JWT selbst. Er folgt
  derselben Identitaet wie die Kontingente: geprueftes Konto aus dem Cookie (mehrere
  Logins desselben Kontos teilen EINEN Key), sonst die Client-IP aus
  `app/client_ip.py` (nicht die rohe Proxy-Adresse).
- Eine wegen Kapazitaet abgelehnte Aktion endet als HTTP 503 `dienst_ausgelastet`
  bzw. als neutrale SSE-Meldung, bevor ein Kontingent, eine Demo-Suche oder ein
  Check-Anspruch entnommen wird.
- SlowAPI-Minutenlimits, atomare Monatskontingente und der atomare Check-
  Versuchszaehler bleiben zusaetzlich aktiv. Kauf- und VerkaufsCheck zusammen sind
  auf 8 technische Versuche pro UTC-Tag und Konto/IP gedeckelt (bisher 25). Gezaehlt
  wird JEDER Start, auch erfolgreiche und erstattete Laeufe; der Zaehler wird nie
  zurueckgenommen (Schutz gegen Refund-Schleifen). Anpassbar ueber
  `AUTO_KI_CHECK_VERSUCHE_PRO_TAG`.
- Kauf-/VerkaufsCheck entnehmen den Anspruch atomar. Kein verwertbares Ergebnis
  wegen Gemini-/internem Fehler fuehrt genau einmal durch das idempotente Refund-
  Gate zur Rueckerstattung. Ein erfolgreich gelieferter technischer KaufCheck
  ohne Marktmodul bleibt eine erbrachte Leistung; ein fehlgeschlagener
  VerkaufsCheck wird erstattet.
- Es gibt keinen alternativen kostenpflichtigen AI-/Search-Fallback. Bei Ausfall
  wird kontrolliert degradiert oder mit neutraler Meldung fehlgeschlagen.

## Quota und Streaming

- Gemini-429 mit `retryDelay` > 1 h oder Tages-/Billing-Hinweis gilt als
  `quota_exhausted` (`GeminiQuotaErschoepft`): genau ein Versuch, kein Retry.
  Nutzer sehen nur die generische Meldung `KI_UEBERLASTET_NACHRICHT`; bezahlte
  Checks werden genau einmal erstattet.
- Chat und Analyse-Rueckfrage (SSE): Fehler vor dem Stream-Start, Timeout waehrend
  des Streams (75 s), Kapazitaetsgrenze und jeder sonstige interne Fehler enden mit
  der neutralen Meldung und `[DONE]` — kein abgerissener Stream, keine
  Fehlerdetails im SSE-Text. Der Admin-Entwurfsstream verhaelt sich gleich.
- Ohne Scope (Skripte/Diagnose) greifen nur die Retry-Maxima je Einzeloperation;
  alle Consumer- und Admin-Routen laufen in einem Scope.
- Output-Grenze `GEMINI_AUX_MAX_OUTPUT_TOKENS` (4.096) gilt fuer JSON-Helfer
  ausserhalb von Kauf-/VerkaufsCheck (AutoFinder-Anreicherung/-Budget/-Web,
  Inseratsoptimierung, Ersatzteile).

## Konfiguration

Alle Werte liegen zentral in `app/config.py` und sind ohne Codeaenderung per ENV
konfigurierbar: `AUTO_KI_GEMINI_*`, `AUTO_KI_TAVILY_*`,
`AUTO_KI_PROVIDER_*`, `AUTO_KI_CHECK_VERSUCHE_PRO_TAG`, die bestehenden Chat-/
AutoFinder-Monatslimits sowie die bestehenden API-/Admin-/Rate-Limit-Werte.

## Logging

`provider_event` protokolliert Provider, Feature, Modell (wenn bekannt), Status,
Fehlerklasse, Versuch und Dauer. `provider_action` protokolliert aggregierte
Calls und Retries je Aktion. Prompts, Queries, vollstaendige Nutzer-/Inserattexte,
API-Keys, JWTs und Zahlungsdaten werden nicht protokolliert.
