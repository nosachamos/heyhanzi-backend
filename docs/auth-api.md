# HeyHanzi authentication API

The backend is ready for frontend integration. The API is not version-prefixed.
Use `https://local-api.heyhanzi.com:8002` locally with
`https://local.heyhanzi.com:3002` for the frontend. Run `make local-https` once to
add the API hostname and certificates, then `make dev-up`.

Production uses `https://api.heyhanzi.com` and `https://heyhanzi.com` (or `www`).
Google, GitHub and LinkedIn are available only after their client credentials
are configured. There is no Microsoft provider. `/auth/providers` is the source
of truth for which buttons to show. Swagger is at `/docs` in development.

## Browser contract

Every fetch must use `credentials: 'include'`. All POST/PUT requests need
`Content-Type: application/json` and an allowed `Origin`; browsers set Origin.
For curl/tests, set `Origin: https://local.heyhanzi.com:3002` yourself.

The opaque session is a host-only `__Host-heyhanzi_session` cookie:
Secure, HttpOnly, Path=/, SameSite=Lax, 30-day absolute expiry. There are no
browser bearer/JWT tokens to store. Account and authenticated auth mutations
also require `X-CSRF-Token`. Read it from the response header of signup/login,
`GET /auth/session`, verification, or a password update and keep it in memory.
The backend exposes this header through CORS. It also sets a host-only
`heyhanzi_csrf` cookie, but the frontend on a sibling hostname should use the
header because it cannot read the API host's cookies. CSRF tokens are bound to
the server-side session; a matching forged cookie/header pair is not enough.

**The frontend and backend must be on the same site.** Do not mix the
`local.heyhanzi.com` frontend with a `localhost` API for authentication. Health
checks on localhost still work. Both frontend/API can use localhost if the
frontend origin is configured accordingly. Third-party cookie exceptions are
not needed and SameSite is never weakened to support mismatched local names.

```ts
const API = import.meta.env.VITE_API_ORIGIN;
let csrfToken: string | null = null;

async function api(path: string, body?: unknown, method = body === undefined ? 'GET' : 'POST') {
  const response = await fetch(`${API}${path}`, {
    method,
    credentials: 'include',
    headers: {
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  const nextCsrf = response.headers.get('X-CSRF-Token');
  if (nextCsrf) csrfToken = nextCsrf;
  const result = await response.json();
  if (!response.ok) throw { status: response.status, ...result };
  return result;
}

// On app startup or after an OAuth redirect:
const user = await api('/auth/session'); // 401 means signed out.
// Empty authenticated POSTs still send JSON:
await api('/auth/logout', {});
```

Treat `401` as signed out and `403 csrf_failed` as a reason to refresh the session
before a user-initiated retry. Do not automatically replay mutations. Refresh
CSRF after a password change/set because those operations rotate the session.

## Endpoint map

| Method | Path | JSON body / query | Authentication |
| --- | --- | --- | --- |
| GET | `/auth/providers` | none | public |
| GET | `/auth/{provider}/login` | none | browser navigation |
| GET | `/auth/{provider}/callback` | provider-managed `code`, `state` | provider callback |
| POST | `/auth/email/signup` | `{email, password}` | public, allowed Origin |
| POST | `/auth/email/login` | `{email, password}` | public, allowed Origin |
| GET | `/auth/session` | none | session |
| POST | `/auth/logout` | `{}` | session + CSRF |
| POST | `/auth/logout-all` | `{}` | session + CSRF |
| GET | `/auth/email/verify` | `?token=...` | public, validation only |
| POST | `/auth/email/verify` | `{token}` | same account's session + CSRF |
| POST | `/account/email-verification/resend` | `{}` | session + CSRF |
| POST | `/auth/password/forgot` | `{email}` | public, allowed Origin |
| GET | `/auth/password/reset/validate` | `?token=...` | public, validation only |
| POST | `/auth/password/reset` | `{token, password}` | public, allowed Origin |
| GET | `/account/security` | none | session |
| PUT | `/account/profile` | `{name}` | session + CSRF |
| POST | `/account/password/change` | `{currentPassword, newPassword}` | verified session + CSRF |
| POST | `/account/password/set` | `{password}` | verified, recent social session + CSRF |
| POST | `/account/auth/{provider}/link` | `{}` | verified, recent session + CSRF |

`provider` is `google`, `github`, or `linkedin`. Unknown/unconfigured login
providers return 404. A successful OAuth callback redirects to the configured
frontend **`/loading?session=1`**. That page calls `/auth/session`, then routes to
onboarding, email verification, or the learning app.

## User and security responses

Signup success returns `{ok: true, message, user}` and starts an unverified
session. Duplicate signup returns `{ok: true, message}` without changing the
existing password or creating a session. The frontend should handle either
shape and offer sign-in when there is no newly created user. Repeating signup
does not resend verification; authenticated users use the resend endpoint.

Login, session lookup and profile updates return the user directly:

```json
{
  "id": "user-uuid",
  "createdAt": 1791475200000,
  "name": "小明",
  "email": "learner@example.com",
  "profilePictureUrl": null,
  "userType": "free",
  "isSuperAdmin": false,
  "emailVerifiedAt": 1791475200,
  "emailVerificationSource": "email",
  "hasPassword": true
}
```

`createdAt` is epoch **milliseconds**; other `*At` fields are epoch **seconds**.
`emailVerifiedAt` is null until verified. Google sign-in retains the HTTPS Google-hosted `picture` URL from the verified ID token.
`profilePictureUrl` is null when no supported avatar is available. The API never fetches
or mirrors external images; the browser displays the URL with an initials fallback. Email
addresses are normalized to lowercase without Gmail dot/plus rewriting.

`GET /account/security` returns:

```json
{
  "email": "learner@example.com",
  "emailVerifiedAt": 1791475200,
  "emailVerificationSource": "email",
  "hasPassword": true,
  "passwordAuthAllowed": true,
  "passwordUpdatedAt": 1791475200,
  "authMethods": ["password", "google"]
}
```

Password set/change returns `{ok: true, message, user, security}`. All previous
sessions and recovery tokens are revoked, remembered devices are cleared, and
a fresh session is issued to the current browser. Setting a password requires
no existing password and authentication within the last five minutes. Changing
one requires the current password and a different new password.

## Email signup and verification

1. POST email/password to `/auth/email/signup`.
2. Show a check-email screen for the new, unverified session. Do not treat it as
   authorization for learning data or other verified-account features.
3. Email opens the frontend at `/verify-email#token=...`.
4. Read the fragment into memory, then immediately remove it with
   `history.replaceState(null, '', location.pathname)`. Never send it to analytics,
   logs, error tracking, local storage or third-party scripts.
5. Call `/auth/session`. If signed out, ask the user to sign in to the same account.
   Preserve the token only in memory across that UI transition.
6. On explicit user confirmation, POST `{token}` to `/auth/email/verify` with CSRF.
7. The response is `{ok: true, message: "Email verified.", user}`. Refresh the user.

Verification lasts 24 hours, is single-use, and is bound to the correct account.
GET only checks validity, so mail link scanners cannot consume it. Possession of
a verification link does not create a session or give an attacker control of a
pre-created account. Successful verification revokes other sessions. A resend
invalidates earlier verification links.

## Password reset

1. POST `{email}` to `/auth/password/forgot`.
2. Always show: `If an account exists for that email, we will send password reset instructions.`
   Absent, disabled and social-only accounts return the same response and do not
   get a password-reset email. Social-only users sign in socially and set a password.
3. Email opens `/reset-password#token=...`. Read and remove the fragment as above.
4. Optionally GET `/auth/password/reset/validate?token=...` to check it. That request
   is no-store; do not log its URL. Submit `{token, password}` to the reset POST.
5. A successful reset returns `{ok: true, message: "Password reset complete."}`.
   Route to login: reset does **not** automatically authenticate the user.

Tokens expire after 30 minutes. A newer reset supersedes older links. Reset
consumption and password changes are serialized, so concurrent requests cannot
reuse one token. Reset revokes all sessions, remembered devices and other
recovery tokens. It also establishes email ownership if it was unverified.
A password-changed notification is queued in the same database transaction.

Passwords must be 12–128 characters. Spaces and Unicode are supported, and
passwords are never trimmed or truncated. Common passwords, HeyHanzi-related
terms and identifiable email/name terms are rejected. No arbitrary mix of
uppercase/digit/symbol rules is required. Passwords use Argon2id with 64 MiB,
three iterations, and a bounded hashing worker to fit the initial Fly machine.

## Social login and explicit linking

Navigate the whole browser to `${API}/auth/google/login`; do not fetch it as JSON.
The backend manages state, nonce, PKCE (Google/GitHub), token exchange and ID-token
validation. Provider credentials and access/ID tokens never go to browser storage.
OAuth state expires in ten minutes, is single-use and bound to the initiating
browser. Start a new flow after an expired/replayed state or if another tab
replaced the OAuth cookie.

Stable provider subjects identify users. An email match does not automatically
link identities. `login?why=account_link_required` means the user should sign in
using the existing method, then POST `/account/auth/google/link`. The response is
`{authorizationUrl}`; navigate there. Linking requires a verified account,
a matching provider email, a session less than five minutes old, and that same
session to remain active through the callback. Already-linked identities cannot
be transferred between users.

Google Gmail/Workspace addresses are accepted as verified when Google confirms
ownership. Google accounts with third-party email addresses, and LinkedIn
accounts, require HeyHanzi's own verification email before sensitive account
operations. GitHub must supply a verified address from its emails endpoint.

Callback errors redirect to `/login?why=error`, `oauth_state_invalid`,
`account_link_required`, or `auth_rate_limited`. Do not render untrusted query text
as HTML. Show a fixed message for each known reason. A callback never accepts
an arbitrary frontend redirect URL.

## Errors and throttling

Typical auth errors use:

```json
{"detail":{"code":"invalid_credentials","message":"Invalid email or password."}}
```

| Status | Typical codes / meaning |
| --- | --- |
| 400 | `invalid_password` (with `issues`), `invalid_current_password`, `password_reuse`, `invalid_or_expired_token` |
| 401 | missing/revoked/expired session; or `invalid_credentials` for login |
| 403 | `invalid_origin`, `csrf_failed`, `reauthentication_required`, `email_verification_required` |
| 404 | provider or email auth feature unavailable |
| 409 | `password_already_set`, `password_not_set`, `email_verification_required`, `credentials_changed`, rejected identity link |
| 413 | auth body exceeds 16 KiB |
| 415 | mutation did not use JSON |
| 422 | `invalid_request`, with invalid field paths; submitted secrets are never echoed |
| 429 | `auth_rate_limited` or `auth_busy`; respect `Retry-After` |

Missing-session errors may have a string `detail`; handle both error shapes.
Network and database failures must be shown as retryable service errors, never
as successful sign-in or a reason to bypass verification.

Limits are shared in PostgreSQL across processes/restarts. Each operation has a
30-per-15-minute network limit; additional 15-minute identity limits are signup,
forgot-password and resend (3), login (10), password changes (5), and verification
(10). IPv6 clients are grouped by /64. A single hashing operation runs at a time
per API process; excessive queued hashing work returns 429. Failed login alerts
start after five failures, at most once per 15-minute window. New-device alerts
also have a 15-minute cooldown. A remembered device suppresses duplicate alerts
for up to 180 days but never bypasses authentication.

## Emails and local inspection

Local Docker uses Mailpit at **http://localhost:8026** (SMTP 1026 on the host,
1025 within Compose). Account creation/verification, resets, password changes,
new-device logins and repeated failed logins have HeyHanzi HTML and text emails.
Provider email content uses escaped user-supplied names.

Mail is committed to an encrypted database outbox with the auth operation, then
sent asynchronously. Failed deliveries retry with backoff, up to eight attempts
and never beyond message expiry. Delivery is at-least-once: a provider timeout
after acceptance can cause a duplicate, but token consumption remains single-use.
Sent/expired payloads are erased. Failed-message metadata is retained briefly for
operations. One 768 MiB Fly machine stays running so retries do not wait for a
new visitor. Multiple replicas use leases and row locks to coordinate delivery.

The frontend should say an email was requested/queued; the API does not guarantee
that the recipient's mail server has delivered it. Offer a resend after cooldown.

## Backend extension rule

New learning endpoints should depend on `app.auth.sessions.get_verified_user`.
`get_current_user` deliberately permits unverified sessions for account setup,
verification, resend and logout. Never accept a user ID in place of those dependencies.
Business operations use request-scoped async sessions; credential mutations lock
the user and commit their audit/notification records atomically.

See [deployment setup](deployment.md) for provider apps, SES and production keys.

## Turnstile protection

Fetch `GET /auth/security` when starting a protected action. It returns
`{"required":true,"siteKey":"…"}` in production; explicit DEV/TEST can return
`{"required":false,"siteKey":null}`. Failure to load this policy must block the
action. The endpoint is `no-store` and never returns the secret.

Include `turnstileToken` in the JSON request for:

| Endpoint | Widget action |
| --- | --- |
| `/auth/email/signup` | `signup` |
| `/auth/email/login` | `login` |
| `/auth/password/forgot` | `forgot_password` |
| `/account/email-verification/resend` | `resend_verification` |

Resend now accepts a JSON object, including `{}` when local verification is
disabled. Existing origin, session, CSRF and rate limits still apply. Rate limits
run before Siteverify; verification runs before password hashing, account changes
or email delivery. The backend checks success, exact hostname/action and a
five-minute timestamp. Cloudflare enforces single use. Provider retries are
bounded and reuse one server-generated verification idempotency key; account
operations are never automatically replayed.

`403 turnstile_failed` means start a fresh challenge. `503 turnstile_unavailable`
means verification could not complete; offer a deliberate retry. Never log or
persist challenge tokens. OAuth navigation/callbacks, email-link verification,
password-reset completion, ordinary authenticated learning, and Stripe webhooks
retain their existing protections and do not require this challenge.
