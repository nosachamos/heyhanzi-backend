# HeyHanzi billing

HeyHanzi has two plans: Free includes every warm-up feature and HSK 1–6 characters, words and examples; Unlimited adds HSK stroke order and story lessons for **USD 1.99/month, billed yearly at USD 23.88 plus applicable tax**. There is no monthly plan, trial, metered usage, or Stripe subscription for free accounts. The annual price excludes tax; applicable tax is added at checkout.

## Required configuration

Production uses the dedicated HeyHanzi.com Stripe account (`acct_1UQXDQ6ftjRDf38Q`). Its live product, price, portal configuration and webhook have been recreated there, and the encrypted production configuration points to that account. Local development temporarily still uses the original NoCV account's test resources until the HeyHanzi test key is supplied. The server reads a `STRIPE` section from `APP_CONFIGS` / `.configs.json`, or these environment overrides:

| Environment variable | STRIPE field | Purpose |
| --- | --- | --- |
| `STRIPE_SECRET_KEY` | `SecretKey` | Server API credential, sandbox or live |
| `STRIPE_ANNUAL_PRICE_ID` | `AnnualPriceId` | Active USD 2388-cent recurring price, one year |
| `STRIPE_WEBHOOK_SECRET` | `WebhookSecret` | Signing secret for this endpoint and mode |
| `STRIPE_PORTAL_CONFIGURATION_ID` | `PortalConfigurationId` | HeyHanzi portal with cancellation at period end |

Keys stay in ignored local configuration or encrypted production configuration. Hosted Checkout and the hosted portal do not need a browser publishable key. Billing offers no checkout while required configuration is absent. Never put secret keys in `VITE_*` variables or send them in chat.

Local `.env` contains only test credentials. `DEV` and `TEST` reject live API keys; `PROD` rejects test API keys. Partial billing configuration fails startup. Production deployment also requires billing configuration. Tests remove local Stripe environment overrides before validating their isolated configurations.

Production configuration is the existing **SecureString `/HeyHanzi/Env/PROD/Configs` in `us-east-1`**, following NoCV's `/Service/Env/PROD/Configs` convention. Its `STRIPE` object contains the four fields above. The manual production workflow retrieves it with decryption and `scripts/stage_production_config.py` includes `STRIPE` in the `APP_CONFIGS` payload sent to Fly through stdin. No production secrets enter the frontend build, shell arguments or image. No extra GitHub Stripe secret is needed. The existing AWS deployment identity must retain permission to decrypt/read this parameter.

Provisioned resources:

| Mode | Product | Annual price | Portal configuration |
| --- | --- | --- | --- |
| Test | `heyhanzi_unlimited` | `price_1UQYZnIlMZXmo50STtvfbz49` | `bpc_1UQWp7IlMZXmo50SrogoMLXY` |
| Live | `heyhanzi_unlimited` | `price_1UQYZp6ftjRDf38Q23LpSMCa` | `bpc_1UQXPY6ftjRDf38QYdJlRcYG` |

The live webhook (`we_1UQXPa6ftjRDf38QlS6NqCte`) is registered at `https://api.heyhanzi.com/billing/webhook` in the HeyHanzi account. It becomes usable after the backend release. Checkout overrides its display name to HeyHanzi, and the live portal uses the HeyHanzi account's merchant branding with a HeyHanzi membership headline. Local test events use Stripe CLI forwarding; no test webhook is registered against the production API. The old account's unused HeyHanzi live price is archived and its HeyHanzi webhook is disabled. There were no live subscriptions to migrate. The ignored `.stripe-setup.nocv-live.json` retains the old resource identifiers and endpoint secret for recovery; it must never be committed.

To finish moving development to the new account, supply its test API key securely (for example, as `STRIPE_TEST_SECRET_KEY` in the ignored frontend `.env` used to hand over the live key). Provision its test resources, put only its test credential and test IDs in the backend `.env`, and regenerate the local CLI signing secret. Existing local billing references belong to the old account and need review during that switch; preserve learner accounts and progress. Never put the live key in the backend's development runtime.

The account must have its business details, payment capabilities, statement descriptor and customer support information configured. Checkout uses Stripe Tax and collects a billing address. Configure Stripe Tax's business location, product tax classification and applicable registrations before enabling sales. The price is tax-exclusive as requested. Stripe calculates and adds applicable tax at checkout. No legal/tax registration decisions are automated by this script.

## Provisioning

From this backend checkout, set `STRIPE_SECRET_KEY` securely in the process environment, then run:

```sh
environment/bin/python -m scripts.setup_stripe \
  --mode test
```

The setup command creates or reuses `heyhanzi_unlimited`, the lookup key `heyhanzi_unlimited_usd_annual_v1`, one recurring annual price, a HeyHanzi customer-portal configuration, and (when `--webhook-url` is supplied) a snapshot webhook. Live provisioning requires that URL. It refuses to silently change an existing price's amount, currency, interval or tax behavior. The portal allows address/payment-method updates, invoice history and cancellation at period end; plan switching is disabled. It writes IDs and any webhook signing secret to the ignored `.stripe-setup.test.json` or `.stripe-setup.live.json` with mode 0600, without printing secret values. Merge its `STRIPE` fields into your backend configuration and add `SecretKey` through your normal secret storage. Keep the file: Stripe only returns the signing secret when creating an endpoint. If an endpoint already exists without this file, recover its secret from Stripe Workbench and restore the corresponding `WebhookEndpointId` and `STRIPE.WebhookSecret` fields locally before rerunning.

Local test configuration has been saved to `.env`. On a new machine or after changing the test API key, run `make stripe-configure` to securely save the CLI signing secret, then `make dev-up ARGS=-d` to reload the API. Run **`make stripe-listen`** while exercising checkout. The wrapper reads the test key directly from `.env`, verifies the signing secret, forwards the required events to `https://local-api.heyhanzi.com:8002/billing/webhook`, and redacts credentials from CLI output. It uses an installed Stripe CLI or the pinned `@stripe/cli@1.53.1` npm package. Local mkcert trust must be installed; TLS verification stays enabled. The CLI signing secret differs from a registered endpoint's secret. A public sandbox API may instead receive the registered webhook directly. Never register a private localhost endpoint as a live destination.

To recreate the live configuration, use a live-mode API credential and repeat with `--mode live --webhook-url https://api.heyhanzi.com/billing/webhook`. Test and live IDs and signing secrets are separate. Provisioning resources does not deploy the application or charge a customer. The tool requires API access to Products, Prices, Billing Portal configurations and Webhook Endpoints; the running backend needs Customers, Checkout Sessions, Subscriptions (read), Prices (read) and Billing Portal Sessions. A full secret key supports these operations; suitably scoped restricted keys can be used instead.

## Lifecycle and access

Apply migration `0017_billing` through the existing migration/release command, never at API startup. It adds `billing_accounts`, preserving existing users and learning progress. Existing users with no billing record receive Free.

- Verified, authenticated browser requests use `GET /billing/status`, `POST /billing/checkout` and `POST /billing/portal`. Mutations require the existing allowed Origin and CSRF header; locale is the only accepted input. The server chooses customer, price and return URLs.
- Checkout validates an active annual USD 2388-cent price before creating a session. Per-user database locks, Stripe idempotency keys, and reuse of an open Checkout session prevent double purchases across tabs. Completed-but-unconfirmed sessions cannot start another purchase.
- Stripe is the subscription authority. Signed webhooks and account-status reads fetch current subscription state under the account lock instead of applying stale event snapshots. Duplicate and out-of-order deliveries converge to current state. Stripe failures return a retryable 503 and never grant access.
- Only an `active` subscription to the configured price with an unexpired paid period grants access. `incomplete`, `past_due`, `unpaid`, `paused`, `canceled` and `incomplete_expired` do not. Cancel-at-period-end remains active until the paid period ends. Paid access checks run for HSK journey reads, lesson completion, vocabulary and exposure mutations, including revisits after cancellation.
- A free learner automatically remains on warm-up after finishing it; level metadata marks paid levels for the UI. Warm-up lessons and progress remain available. Public curriculum reference data and shared stroke/audio assets are not treated as a content DRM boundary.
- All billing responses are uncached. The frontend polls status briefly after returning from Checkout and offers manual refresh. Neither a query string nor a browser plan claim grants access.

Required webhook events: `checkout.session.completed`, `checkout.session.expired`, `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.paid`, `invoice.payment_failed`. The API and endpoint use `2025-06-30.basil`; subscription item period ends are read from that version. Webhook signatures use the raw bounded request body and a five-minute timestamp tolerance. Delivery failures remain non-2xx so Stripe retries; monitor failed deliveries in Workbench. Account settings also reconciles state on load/refresh, but reliable webhook delivery is necessary for timely revocation and renewal for users who do not open settings.

## Verification and release

`make ci-core` runs lint and bounded core tests. `make release-schema` rehearses migrations and exercises real HTTP/auth/PostgreSQL with only Stripe and email external boundaries replaced. Billing tests cover Free/paid content access, duplicate checkout, customer isolation, forged/stale webhook signatures, duplicate/out-of-order events, renewal failure, period expiry, cancellation, and repurchase. Frontend browser checks cover pricing in both locales, no-JavaScript rendering, signup intent, checkout, portal, delayed confirmation, failures and responsive layouts.

Real Stripe sandbox verification uses the configured test credentials: complete a sandbox card checkout; confirm signed webhook delivery and HSK access; inspect invoices; cancel renewal and confirm access through the paid period; advance a test clock or expire/cancel the test subscription and confirm Free access. Exercise failed payment and webhook retry before live activation. These checks cannot be replaced by the local provider double.

The configured test key was checked against the real API: hosted Checkout creation uses the annual price and automatic tax; an API-created test subscription produced a paid invoice; real signed subscription/invoice events returned HTTP 200 and unlocked HSK 1 before a billing-status refresh; the app opened the hosted portal showing the annual subscription and paid invoice. Cancellation through that portal disabled renewal while preserving Unlimited access. Ending the disposable test subscription through Stripe's API delivered a signed deletion event, revoked HSK access, preserved warm-up access and returned settings to Free. Automated card submissions in hosted Checkout did not complete, with hCaptcha requests aborted in the browser, so a manual end-to-end hosted checkout is still required. These were test-mode transactions only.

At configuration time the production SSM document's `DATABASE.Url`, `DATABASE.MigrationUrl` and `DATABASE.DatabaseName` are empty. Populate them through the existing secure production configuration workflow before attempting deployment. Billing provisioning preserves all other SSM sections and does not deploy the app.

Deploy the migrated backend, securely configured Stripe resources, and the frontend/CloudFront route update through their existing manual master-only release workflows. Do not deploy as part of local tests. This change does not make decisions about business registration, refund policy or customer terms; those remain launch inputs.

## October 2026 price update

Unlimited is advertised at USD 1.99/month and charged once per year at USD 23.88, excluding tax. Setup uses lookup key `heyhanzi_unlimited_usd_annual_v2`; Stripe's recurring interval remains `year`, with `unit_amount=2388` and `tax_behavior=exclusive`. The previous price objects remain available for historical subscriptions. No live subscribers existed on the old price when the new price was provisioned.

Local test configuration and the encrypted production parameter reference the new prices above. Release the matching backend and frontend code together; changing the parameter does not deploy either app. Previously stored subscriptions retain their access and billing management. Old open checkout sessions are expired and replaced when the learner starts checkout again, and checkout idempotency includes the price ID.
