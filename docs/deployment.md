# HeyHanzi production setup

The backend supports Google, GitHub, LinkedIn and email/password authentication.
Email/password works locally through Mailpit without external credentials.
Production configuration lives in encrypted AWS Parameter Store at
`/HeyHanzi/Env/PROD/Configs` in `us-east-1`; never commit the parameter's value.

## Google OAuth application

Create a Google Cloud project and OAuth consent screen for HeyHanzi. Create an
OAuth client of type **Web application**. Request only `openid email profile`.
Use separate development/production clients where practical.

Register exact redirect URIs (including the local port):

- `https://local-api.heyhanzi.com:8002/auth/google/callback`
- `https://api.heyhanzi.com/auth/google/callback`

If the first deployed API is tested on the Fly hostname, temporarily register
`https://heyhanzi-backend.fly.dev/auth/google/callback` and configure BackendUrl
accordingly, but browser sessions with the production frontend require the API's
same-site `api.heyhanzi.com` hostname. The backend derives callbacks from trusted
configuration, never request Host headers.

Configure the consent screen's authorized domain `heyhanzi.com`, homepage,
privacy policy, support contact and test users while in testing mode. The server
redirect flow does not require the frontend to receive the client secret.
Store local `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in ignored `.env`;
production uses `OAUTH.Google.ClientId` / `ClientSecret` in SSM.

Optional GitHub and LinkedIn applications use the same callback pattern with
`github` / `linkedin`. Configure `OAUTH.Github` or `OAUTH.LinkedIn` with ClientId
and ClientSecret. LinkedIn requires its **Sign In with LinkedIn using OpenID
Connect** product. Empty credentials disable a provider and hide it from
`/auth/providers` without breaking other login methods.

References: [Google OIDC](https://developers.google.com/identity/openid-connect/openid-connect),
[GitHub OAuth](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps),
[LinkedIn OIDC](https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/sign-in-with-linkedin-v2).

## SES

Production sends from **HeyHanzi <no-reply@heyhanzi.com>** through SES v2 in
`us-east-1`. The AWS account has production sending access. The HeyHanzi domain
identity and its DKIM CNAME records have been provisioned in the domain's public
Route 53 zone; both SES identity verification and DKIM are successful. The application IAM principal has an additional policy limited
to `ses:SendEmail` on `identity/heyhanzi.com` and that exact From address.
No real recipient emails were sent during setup.

Inspect readiness without sending mail:

```bash
aws sesv2 get-email-identity --email-identity heyhanzi.com --region us-east-1 \
  --query '{Verified:VerifiedForSendingStatus,DKIM:DkimAttributes.Status}'
aws sesv2 get-account --region us-east-1 \
  --query '{SendingEnabled:SendingEnabled,ProductionAccess:ProductionAccessEnabled}'
```

For future accounts, DKIM verification may take time after DNS propagation.
`scripts/configure_ses.py` can reconcile the identity, missing DKIM records and scoped sending policy. It
refuses to replace conflicting DNS. SES's default MAIL FROM is used; configure
DMARC and a custom MAIL FROM deliberately alongside any other domain senders.
An optional `EMAIL.SesConfigurationSet` may point to an existing SES configuration
set for bounce/complaint monitoring. Configure and monitor those events before
opening public signup at scale.

See [SES domain identity documentation](https://docs.aws.amazon.com/ses/latest/dg/creating-identities.html).

## Production parameter shape

`environment/bin/python scripts/configure_production.py` initializes missing
HeyHanzi auth keys and sections without replacing existing keys or credentials.
It does not invent OAuth client credentials. It intentionally permits an initial
incomplete parameter; actual deployment validation rejects a missing database,
missing application keys, non-SES production delivery or incomplete provider pairs.

```json
{
  "GENERAL": {"Environment": "PROD"},
  "URLS": {
    "FrontendUrl": "https://heyhanzi.com",
    "BackendUrl": "https://api.heyhanzi.com",
    "AllowedOrigins": ["https://www.heyhanzi.com"]
  },
  "DATABASE": {
    "Url": "<Render external URL with sslmode=verify-full>",
    "MigrationUrl": "<Render direct external URL with sslmode=verify-full>",
    "DatabaseName": "<exact name from the URL>"
  },
  "AUTH": {
    "SecretKey": "<unique random secret>",
    "AuditHashSecret": "<unique random secret>",
    "TokenEncryptionKey": "<unique Fernet key>",
    "EmailPasswordEnabled": true,
    "EmailSignupEnabled": true
  },
  "OAUTH": {"Google": {}, "Github": {}, "LinkedIn": {}},
  "EMAIL": {
    "Provider": "ses",
    "FromEmail": "no-reply@heyhanzi.com",
    "FromName": "HeyHanzi",
    "SupportUrl": "https://heyhanzi.com",
    "SesRegion": "us-east-1"
  },
  "AWS": {"Region": "us-east-1", "AccessKeyId": "<credential>", "SecretAccessKey": "<credential>"}
}
```

The runtime receives these sections as one Fly secret, `APP_CONFIGS`. The deploy
script uses stdin rather than shell arguments for secret transfer and does not
print the config. SES uses only the explicitly configured application identity,
never an unrelated ambient AWS identity.

Do not casually rotate TokenEncryptionKey: pending OAuth attempts and queued
emails require the old key to decrypt. Drain/expire those records before a planned
rotation, or implement a keyring migration. SecretKey rotation invalidates CSRF
reconstruction for existing sessions; pair it with deliberate session revocation.
Keep production keys unique and stable across replicas and releases.

## Render database

Provisioned on 2026-11-12: `heyhanzi-prod`
(`dpg-dbquonjrjlhs73cg10h0-a`), PostgreSQL 16 in Ohio, paid `0.1c-256mb`
plan with 5 GB storage. This is a dedicated HeyHanzi database. Its external
runtime and migration URLs are stored only in the encrypted
`/HeyHanzi/Env/PROD/Configs` parameter. The management key is stored under
`RENDER.ApiKey`; `stage_production_config.py` excludes the entire `RENDER`
section when transferring `APP_CONFIGS` to Fly.

Both connections use `sslmode=verify-full`. Fly sets `PGSSLROOTCERT` to
`/etc/ssl/certs/ca-certificates.crt`; the migration URL explicitly names the
same CA bundle. A real read-only connection succeeded with certificate
verification. No production schema migration has been applied yet.
External database access currently permits dynamic Fly/release egress using
Render's `0.0.0.0/0` allowlist, with database authentication and verified TLS.
Restricting this later requires fixed outbound addresses; the app's inbound
HTTP addresses are not its database egress addresses.

Create a dedicated PostgreSQL 16 instance and use its **external** connection
URL from Fly. Keep the assigned database name and enable certificate verification.
Choose a region near the API or update Fly's `primary_region`. If external access
is restricted, permit the app's outbound addresses, not its inbound HTTP IPs.

Put the URL in an untracked, mode-0600 file and run:

```bash
environment/bin/python scripts/configure_production.py \
  --database-url-file /private/path/render-database-url.txt
```

Remove the temporary file afterward. Migrations must retain a direct connection
if a transaction pooler is introduced later. Readiness requires the release's
schema heads; API startup never applies migrations.

## GitHub and Fly

The `production` GitHub environment permits only `master`. Its `AWS_ROLE_ARN`
variable identifies `heyhanzi-backend-github-deploy`, which uses short-lived GitHub
OIDC credentials and can read only `/HeyHanzi/Env/PROD/Configs`. Its immutable
repository/environment subject is distinct from the runtime SES sending identity.
No static AWS deployment keys are needed. Set the environment secret `FLY_API_KEY`
to a token scoped to this Fly app. GitHub cannot read secret values back; use the
original credential store or issue a new app-scoped token.

For first provisioning, authenticate to the intended organization:

```bash
fly auth login
fly apps create heyhanzi-backend --org YOUR_ORG
fly ips allocate-v6 --app heyhanzi-backend
fly ips allocate-v4 --shared --app heyhanzi-backend
fly certs add api.heyhanzi.com --app heyhanzi-backend
fly certs show api.heyhanzi.com --app heyhanzi-backend
```

Apply the exact DNS records returned by Fly. The API must have a same-site HTTPS
hostname before the production frontend's credentialed fetches can work.
Set the frontend's public `VITE_API_ORIGIN=https://api.heyhanzi.com` and rebuild it.

The manual **Deploy backend to prod** workflow requires master, runs core checks,
all database/auth integration tests, dependency audit and a 768 MiB container
rehearsal, then deploys the exact verified image. `--ha=false` avoids a standby.
One shared CPU / 768 MiB machine remains running to deliver queued email; the
release migration machine has the same allocation. TLS terminates at Fly.

The API ignores X-Forwarded-For. Only on an actual Fly machine, and only for a
socket peer in Fly's private IPv4 proxy network (172.16/12), does it accept the
single Fly-Client-IP header. Keep the API directly behind Fly, not CloudFront or
another HTTP proxy, unless that additional trust boundary is explicitly implemented
and tested. Direct/private ingress must not be exposed to untrusted peers.

## Operations and security checks

```bash
make test
make release-check
fly logs --app heyhanzi-backend
fly checks list --app heyhanzi-backend
curl --fail https://api.heyhanzi.com/ready
```

Audit and rate-limit records use keyed IP hashes; logs omit credentials, query
strings and raw provider failures. Auth mutations are bounded to 16 KiB JSON,
require approved Origins, and use session-bound CSRF. Password reset and identity
linking use row/advisory locks. Duplicate token consumption loses atomically.

Emails are encrypted in the database, claimed through leases, and retried with
bounded backoff. At-least-once delivery can produce duplicates after ambiguous
provider acknowledgments. Inspect outbox status counts and oldest pending age:

```sql
SELECT status, count(*), min(created_at) FROM email_outbox GROUP BY status;
```

Successful delivery clears payloads. Failed/expired messages have bounded lifetime;
users can request a new verification/reset link. Auth housekeeping runs hourly in
bounded batches: audit history 90 days; expired sessions/tokens/counters about one
additional day; outbox metadata one week past message expiry. Set external alerts
for SES failures/backlog and database connectivity before public launch.

Production data is permanent. Never reset it or automatically downgrade schema.
Use compatible rollouts, forward repair and tested backups. Increasing API replicas
also increases connection pools and concurrent password hashing; keep aggregate
capacity within database and memory budgets. The 768 MiB rehearsal includes the full authored course catalog, authenticated
and guest course state/detail, and API/cgroup peak-memory checks. It is a footprint
check, not proof of capacity for millions of users.

Security references: [OWASP authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html),
[password recovery](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html),
[session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).

## Turnstile

Production requires `TURNSTILE.SiteKey`, `TURNSTILE.SecretKey`, and an exact
`TURNSTILE.Hostnames` list in the encrypted `/HeyHanzi/Env/PROD/Configs` parameter.
HeyHanzi's public site key is `0x4AAAAAAFXDb-7GCpI0KVvd`; the canonical widget host
is `heyhanzi.com`. Configure the Cloudflare widget as Managed with that hostname.
The secret stays in SSM or the ignored backend `.env`, never in frontend variables.
The release staging script includes this section in `APP_CONFIGS`.
Production startup rejects missing keys, Cloudflare testing keys, and local or
wildcard hostnames; `Enabled=false` cannot disable production enforcement.

Local DEV/TEST defaults to disabled. To exercise the widget locally, set
`TURNSTILE_ENABLED=true`, `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET`, and
`TURNSTILE_HOSTNAMES` on the backend for a separate development widget with
`local.heyhanzi.com` allowed. Keep production hostnames separate. The automated
suite substitutes only the Cloudflare network boundary and validates all token
checks through public HTTP routes; it does not contact production Cloudflare.

Release the matching frontend and backend together. The frontend's regenerated
CloudFront response-header policy must allow `https://challenges.cloudflare.com`
in `script-src` and `frame-src`; apply the template with the normal infrastructure
release process before switching the frontend. Local Node and optional Nginx CSPs
include the same permissions. The policy endpoint is uncached; no token or user
state enters the anonymous pre-rendered HTML. This setup does not deploy anything.
After the manual production release, verify a real challenge on `heyhanzi.com`
and a protected action: local fixtures cannot qualify a real site/secret pairing.
