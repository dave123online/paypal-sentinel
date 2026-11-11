# 🛡️ PayPal Sentinel

**An AI protection and growth agent for small PayPal merchants.** Shield holds risky payments with deterministic rules, Gemini explains each decision in plain, reassuring language, and Ally acts as a virtual CFO that finds revenue opportunities and cash-flow risks.

**Live demo:** https://paypal-sentinel.onrender.com (dashboard at `/dashboard`, API docs at `/docs`). PayPal **Sandbox** only, no real money.

**Demo video:** https://youtu.be/Mrys1RFJr3Y?si=pp3aSpTvM04Cn-cV

## The problem
Small merchants face card-testing attacks, friendly fraud (false "item not received" disputes) and suspicious invoices, plus the daily stress of lost customers and unpaid invoices. Existing tools are opaque black boxes. Sentinel holds a risky payment, explains why, and leaves the final decision to the merchant.

## What it does
- **Shield (protection, transparency, control).** Deterministic rules decide; Gemini never does. Four outcomes, all tested:
  - `approved`: normal payment, captured automatically.
  - `held`: 6+ micro-payments followed by an amount of 300 EUR or more, or an invoice above 2.5x the average sent to a new payee. The PayPal authorization is kept open, not captured. The merchant clicks **Unlock and validate** to trigger a real capture.
  - `alerted`: PayPal declined the payment, or a dispute claims "not received" while tracking says delivered. Nothing to capture; the merchant acknowledges.
  - `blocked`: an automated burst of 15+ test payments before a large amount. The authorization is voided.
- **Ally (growth, decoupled from Shield, never talks about fraud).** Analyzes the last 30 transactions: churn of loyal customers, upsell trends, cash-flow "dry spells", unpaid invoices. Throttled (one analysis every 3 transactions) and de-duplicated. Each card has a **Mark as read** button.
- **Command Center dashboard.** KPI cards, Shield and Ally feeds, AG Grid tables with a **Source** column (PayPal Sandbox / PayPal Webhook / Simulated), and live status dots for Gemini and PayPal.

## How it uses PayPal (Sandbox)
Real flow with `intent=AUTHORIZE`, driven by webhooks:

```
POST /paypal/orders ──► buyer approves (Sandbox Personal account)
                                 │  webhook CHECKOUT.ORDER.APPROVED
                                 ▼
                    authorize order ──► Shield decides
       approved ─► real capture      held ─► authorization kept, "Unlock" = real capture
       blocked  ─► real void         PayPal decline ─► Shield alert
```
- **Orders v2** (create, authorize), **Payments v2** (capture, void), **OAuth2** client credentials.
- **Webhooks** with signature verification (`verify-webhook-signature`): `CHECKOUT.ORDER.APPROVED`, `PAYMENT.AUTHORIZATION.CREATED`, `PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.DENIED`, `CUSTOMER.DISPUTE.CREATED`, `INVOICING.INVOICE.PAID`. Duplicate deliveries and events already processed through the API are ignored.
- **Negative testing** with the `PayPal-Mock-Response` header (`INSTRUMENT_DECLINED`, `DUPLICATE_INVOICE_ID`), handled by Shield.
- Code: `app/paypal_client.py`, `app/webhooks/handlers.py`, `app/main.py`.

## How it uses AI (Google Gemini)
`app/ai_engine.py` calls Gemini (JSON output) for two things only: (1) rewriting a Shield decision as a short empathetic message to the merchant, (2) writing the Ally business suggestion from deterministic analytics. Amounts, types and decisions are computed and validated server-side. Gemini receives no buyer e-mail and no first name: customers are replaced by a non-reversible identifier (`Customer-xxxxxx`) and the name is restored locally for display. Without `GOOGLE_API_KEY` or if a call fails, deterministic fallbacks keep everything working.

## Tools
| Tool | How it is used |
|---|---|
| PayPal Sandbox APIs | Orders, authorization, capture, void, webhooks, negative testing |
| Google Gemini (AI Studio) | Empathetic explanations (Shield) and business suggestions (Ally) |
| FastAPI | API, webhook receiver, orchestration (`asyncio.gather` runs Shield and Ally in parallel) |
| AG Grid | Verdict and suggestion tables in the dashboard |
| Render | Hosting of the live demo |

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # fill in the keys
uvicorn app.main:app --reload
```
Dashboard: http://127.0.0.1:8000/dashboard · Docs: http://127.0.0.1:8000/docs

Full simulated demo (no PayPal keys needed):
```bash
bash scripts/demo.sh                  # normal traffic -> attacks -> cash-flow crisis
bash scripts/crisis.sh                # only the cash-flow crisis (3 unpaid invoices)
pytest
```
Real Sandbox demo (needs Sandbox Client ID/Secret and a Personal + Business Sandbox account):
```bash
python scripts/sandbox_checkout.py                       # normal payment, captured automatically
python scripts/sandbox_checkout.py --hold --amount 500   # Shield holds; Unlock = real capture
python scripts/sandbox_checkout.py --decline             # INSTRUMENT_DECLINED -> alert
python scripts/sandbox_checkout.py --block               # test-payment burst -> blocked, authorization voided
python scripts/sandbox_checkout.py --dup-invoice         # DUPLICATE_INVOICE_ID
```
Check the result in the Business account activity on sandbox.paypal.com. For local webhooks use `ngrok http 8000` and register `/webhooks/paypal` in PayPal Developer (`PAYPAL_WEBHOOK_ID`).

## Using the hosted demo
The dashboard is public and read-only. Actions that change state (simulation, order creation, Unlock, Acknowledge, Mark as read) require an admin token, provided to judges in the submission's testing instructions. In the browser, buttons ask for it once per session; in `/docs`, enter it in the `x-admin-token` header of the protected routes; in scripts: `export SENTINEL_ADMIN_TOKEN=...`.

Quick test without any PayPal account (simulated data), from `/docs`:
1. `POST /simulate/transaction` with `{"event_type":"PAYMENT.CAPTURE.COMPLETED","transaction_id":"TX_TEST_1","amount":500,"currency":"EUR","buyer_email":"test@example.com","raw_payload":{"recent_micro_payments":8}}`: Shield holds it (source: Simulated).
2. Click **Unlock and validate** on the dashboard.
3. `POST /simulate/stress` with `{"unpaid_invoices":3,"unpaid_amount":2400}`: Ally switches to cash-flow survival mode.

The full real-Sandbox flow (order, buyer approval, webhook, hold, real capture) is shown in the demo video and reproducible with `scripts/sandbox_checkout.py` (see above). The free Render plan may take 30-60 s to wake up, and state is in memory, so the dashboard is empty after a restart until a scenario runs.

## Endpoints
`GET /health` · `GET /merchant/state` · `POST /paypal/orders` · `POST /paypal/orders/{id}/authorize` · `POST /webhooks/paypal` · `POST /simulate/transaction` · `POST /simulate/stress` · `GET /shield/verdicts` · `POST /shield/approve/{tx}` · `POST /shield/acknowledge/{tx}` · `GET /ally/suggestions` · `POST /ally/dismiss/{id}` · `GET /dashboard`

## Deploy on Render
- Build: `bash build.sh` (or `pip install -r requirements.txt`) · Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Environment: `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_ENVIRONMENT=sandbox`, `PAYPAL_WEBHOOK_ID`, `GOOGLE_API_KEY`, `GOOGLE_MODEL`, `APP_BASE_URL` (public Render URL, used as PayPal `return_url`), `ADMIN_TOKEN` (ASCII characters only).

## Limitations (please read before judging)
- **Some risk signals are simulated.** PayPal does not provide `recent_micro_payments`, `tracking_delivered` or `average_invoice_amount`. Sentinel demonstrates a decision architecture that can use such signals; in the demo they come from scenarios (`tests/scenarios`) or from `signals` (Sandbox only). The Source badges show what is real.
- **KPIs are not measured losses.** "Amount held / blocked" is the sum of held or blocked payments (exposure handled, not proven losses avoided). Ally's "estimated impact" is a heuristic.
- **Demo-grade deployment.** In-memory storage (a server restart clears state), a single merchant, no user authentication. Admin actions are protected by one shared token. Not for production use with real merchants.

## License
MIT, see `LICENSE`. This is an independent hackathon project, not affiliated with or endorsed by PayPal.