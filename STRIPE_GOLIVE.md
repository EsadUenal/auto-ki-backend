# Stripe / Bezahlfluss — Go-Live-Audit

Stand: 2026-07-08. Reiner Audit des Stripe-Bezahlflusses (Auth → Pricing →
Checkout → Stripe → Webhook → Credits → Subscription → Frontend). Ergänzt die
allgemeine `DEPLOYMENT.md` um den zahlungsspezifischen Live-Status.

## Ergebnis in einem Satz

**Die App kann heute NICHT live gehen** — nicht wegen eines Code-Defekts,
sondern weil Stripe aktuell im **Testmodus** läuft und die Live-Konfiguration
operativ noch nicht gesetzt ist. Der Code liest alle Stripe-Werte aus Env
(keine hartcodierten Keys) und ist für den Live-Betrieb vorbereitet.

## Ist-Zustand (verifiziert)

| Prüfpunkt | Ist-Zustand |
|---|---|
| Secret Key | `sk_test_…` → **Testmodus** |
| Webhook-Secret | `whsec_…` aus lokalem `stripe listen` (kein Prod-Endpoint-Secret) |
| Price-IDs LIGHT/PRO/MAX/EINZELKAUF | Test-Mode-Preise (`price_…`, im Testmodus erzeugt) |
| `FRONTEND_URL` | `http://localhost:3000` → Success/Cancel-Redirects zeigen auf localhost |
| Hartcodierte Keys im Code | **keine** — alles über `os.environ.get(...)` (siehe `app/config.py`) |
| `.env` committet | **nein** (gitignored — kein Secret-Leak) |
| Frontend-Hinweis „Testmodus — keine echten Zahlungen" | aktuell **wahr** → bewusst NICHT entfernt |

Weil Stripe im Testmodus ist, ist der Testmodus-Hinweis im Frontend
(`PricingView.tsx`) korrekt. **Erst entfernen, wenn Live-Keys aktiv sind** —
sonst glauben Nutzer, sie zahlen echt, obwohl es Testzahlungen sind.

## Was bereits korrekt ist (nicht anfassen)

- **Freischaltung ausschließlich per signaturgeprüftem Webhook** — nie über die
  Redirect-URL (`payments.py`, `checkout.session.completed`).
- **Webhook-Signatur** via `stripe.Webhook.construct_event` gegen
  `STRIPE_WEBHOOK_SECRET`.
- **Idempotenz race-sicher**: `INSERT OR IGNORE INTO stripe_events` beansprucht
  das Event atomar; bei Verarbeitungsfehler wird der Claim zurückgerollt, damit
  Stripes Retry das Event tatsächlich erneut verarbeitet (keine doppelten
  Credits, keine verlorene Freischaltung).
- **Body-Größen-Middleware** prüft nur `Content-Length` und liest den Body
  nicht → die Rohbody-Signaturprüfung des Webhooks bleibt intakt.
- **Monatsreset** über `invoice.paid` (nur `billing_reason=subscription_cycle`);
  Erstrechnung läuft über `checkout.session.completed` (keine Doppelvergabe).
- **Kündigung**: `cancel_at_period_end=True`; endgültige Umstellung auf
  `abo_typ='none'` erst per `customer.subscription.deleted`.

## Go-Live-Blocker (operativ — nur der Betreiber kann sie setzen)

1. **Live-Keys**: `STRIPE_SECRET_KEY=sk_live_…` + im Stripe-Live-Modus erzeugte
   **Live-Price-IDs** für LIGHT/PRO/MAX/EINZELKAUF.
2. **Prod-Webhook**: Endpoint im Live-Dashboard anlegen
   (`https://<domain>/api/v1/payments/webhook`), Events
   `checkout.session.completed`, `invoice.paid`,
   `customer.subscription.deleted`; das erzeugte `whsec_…` als
   `STRIPE_WEBHOOK_SECRET` setzen (siehe `DEPLOYMENT.md` §1.5).
3. **`FRONTEND_URL`** auf die echte Domain setzen (sonst brechen Success/Cancel-
   Redirects nach der Zahlung auf localhost).
4. **Frontend-Testhinweis** in `PricingView.tsx` entfernen — **erst nach** Punkt 1.

## Code-Level-Risiken (vor/mit Go-Live prüfen — bewusst NICHT im Rahmen dieses
Audits geändert, da Feature-/Verhaltensänderung mit Regressionsrisiko)

1. **Upgrade/Downgrade erzeugt eine zweite Subscription (Doppelabbuchung).**
   `create_checkout_session` legt für `typ="abo"` immer eine **neue**
   Subscription an (`mode="subscription"`) und kündigt eine bestehende **nicht**.
   Wechselt ein Nutzer mit aktivem Abo den Plan (die Pricing-UI lässt jeden
   Nicht-aktuellen Plan klicken), entstehen **zwei aktive Abos**; zusätzlich wird
   `stripe_subscription_id` mit der neuen ID überschrieben → das alte Abo ist
   „verwaist" und lässt sich in der App nicht mehr kündigen (läuft weiter).
   *Empfehlung vor Live:* entweder Plan-Wechsel per
   `stripe.Subscription.modify(..., items=[...], proration_behavior=...)`
   umsetzen, **oder** übergangsweise den Kauf blocken, solange ein aktives Abo
   besteht (Hinweis „erst kündigen"). Beides ist eine bewusste Produkt-/
   Zahlungslogik-Entscheidung und keine reine Fehlerbehebung.
2. **Kein Handling für Refund / Dispute / `invoice.payment_failed`.**
   Nach Rückerstattung/Chargeback behält der Nutzer Credits/Abo; eine
   fehlgeschlagene Verlängerung stuft nicht herab. Bewusste Business-Entscheidung
   nötig (welche Events abonnieren, welche Aktion auslösen).
3. **`invoice.paid` liest Top-Level `subscription`.** Neuere Stripe-API-Versionen
   (2024-09-30+) verlagern dieses Feld; ohne Fallback könnte der Monatsreset
   still ausbleiben. Bei Go-Live gegen die tatsächlich genutzte API-Version
   testen (der Periodenend-Timestamp hat bereits mehrstufige Fallbacks, das
   `subscription`-Feld in `invoice.paid` nicht).
4. **Webhook unterliegt dem globalen Rate-Limit** (`SlowAPIMiddleware`,
   Default 20/min über `get_remote_address`). Hinter einem Reverse-Proxy mit
   geteilter IP kann das Webhooks (und generell Traffic) drosseln → 429 →
   verzögerte (nicht verlorene, dank Idempotenz + Stripe-Retry) Freischaltung.
   Deployment-/Proxy-abhängig; ggf. Webhook vom Limit ausnehmen oder Proxy-IP-
   Auflösung (X-Forwarded-For) konfigurieren.

## Testtexte / Debug (Suche durchgeführt)

- Repo-weit **keine** hartcodierten `sk_live_`/`sk_test_`/`whsec_` im Code.
  Platzhalter nur in `.env.example` / `.env.stripe` (Doku, keine echten Werte).
- Frontend: einziger zahlungsbezogener Testtext ist „Testmodus — keine echten
  Zahlungen" (`PricingView.tsx`) — aktuell korrekt, siehe oben.
- Kommentare wie „Phase 2d (Stripe Testmodus)" in `payments.py`/`config.py` sind
  interne Code-Kommentare (nicht nutzersichtbar).

## Go-Live-Checkliste

| Go-Live-Check | Status |
|---|---|
| Stripe Live Keys (`sk_live_…`) | ❌ noch `sk_test_…` |
| Live-Price-IDs (LIGHT/PRO/MAX/EINZELKAUF) | ❌ Test-Preise |
| Prod-Webhook-Endpoint + `whsec_…` | ❌ lokales `stripe listen`-Secret |
| `FRONTEND_URL` = echte Domain | ❌ `http://localhost:3000` |
| Frontend „Testmodus"-Hinweis entfernt | ❌ (bewusst erst nach Live-Keys) |
| Checkout-Session (Abo + Einzelkauf) | ✅ korrekt |
| Webhook-Signaturprüfung | ✅ korrekt |
| Webhook-Idempotenz / keine Doppel-Credits | ✅ korrekt (race-sicher, retry-fähig) |
| Credits-Vergabe + Monatsreset | ✅ korrekt |
| Kündigung (period-end) | ✅ korrekt |
| Upgrade/Downgrade ohne Doppelabo | ❌ offen (siehe Risiko 1) |
| Refund/Dispute/Zahlungsausfall-Handling | ❌ nicht implementiert (bewusste Entscheidung) |
| **Live Ready** | **❌ NEIN — Testmodus, operative Live-Config offen** |
