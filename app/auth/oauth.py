"""Authorization-code OAuth with server-side, single-use browser-bound state."""
import base64
import hashlib
import json
import secrets
from urllib.parse import urlsplit

from authlib.jose import JsonWebToken
from authlib.oidc.core import CodeIDToken
from fastapi import HTTPException
from sqlalchemy import select

from app.auth.audit import record_auth_event
from app.auth.email_flows import auth_error, issue_email_token, lock_email, locked_user
from app.auth.notifications import notify, remember_trusted_device_login
from app.auth.sessions import AUTH_SESSION_COOKIE_NAME, create_auth_session
from app.auth.tokens import create_opaque_token, hash_opaque_token
from app.email.outbox import cipher
from app.email.templates import AuthEmailTemplate
from app.models.auth import AuthSession, OAuthAttempt, User, UserAuthIdentity
from app.security.email import is_valid_auth_email, normalize_email
from app.security.rate_limit import enforce_rate_limit
from app.services.learning import ensure_enrollment, get_track
from app.utils.time import utc_now_seconds

PROVIDERS = {
    "google": {"config": "Google", "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
               "token": "https://oauth2.googleapis.com/token", "jwks": "https://www.googleapis.com/oauth2/v3/certs",
               "issuers": ["https://accounts.google.com", "accounts.google.com"], "scope": "openid email profile"},
    "github": {"config": "Github", "authorize": "https://github.com/login/oauth/authorize",
               "token": "https://github.com/login/oauth/access_token", "scope": "read:user user:email"},
    "linkedin": {"config": "LinkedIn", "authorize": "https://www.linkedin.com/oauth/v2/authorization",
                 "token": "https://www.linkedin.com/oauth/v2/accessToken", "jwks": "https://www.linkedin.com/oauth/openid/jwks",
                 "issuers": ["https://www.linkedin.com/oauth"], "scope": "openid profile email"},
}
OAUTH_COOKIE = "__Host-heyhanzi_oauth"


def provider_settings(config, provider):
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise HTTPException(404, detail="Unsupported provider")
    credentials = config.get("OAUTH", {}).get(spec["config"], {})
    if not credentials.get("ClientId") or not credentials.get("ClientSecret"):
        raise HTTPException(404, detail="Provider is not configured")
    return spec, credentials


async def begin_oauth(db, request, response, provider, *, user=None):
    from urllib.parse import urlencode
    config = request.app.state.config
    spec, credentials = provider_settings(config, provider)
    await enforce_rate_limit(db, request, "oauth_start")
    if user and (not user.email_verified_at or request.state.auth_session.created_at < utc_now_seconds() - 300):
        raise auth_error(403, "reauthentication_required", "Verify your email and sign in again before linking.")
    track_id = request.query_params.get("learning_track_id") if not user else None
    if track_id:
        await get_track(db, track_id)
    state, browser, verifier, nonce = [create_opaque_token() for _ in range(4)]
    redirect_uri = config["URLS"]["BackendUrl"] + f"/auth/{provider}/callback"
    payload = {"learning_track_id": track_id, "verifier": verifier, "nonce": nonce, "redirect_uri": redirect_uri,
               "link_user_id": user.id if user else None,
               "link_session_hash": hash_opaque_token(request.cookies.get(AUTH_SESSION_COOKIE_NAME, "")) if user else None}
    db.add(OAuthAttempt(state_hash=hash_opaque_token(state), browser_hash=hash_opaque_token(browser),
                        provider=provider, encrypted_payload=cipher(config).encrypt(json.dumps(payload).encode()).decode(),
                        expires_at=utc_now_seconds() + 600))
    await db.commit()
    response.set_cookie(OAUTH_COOKIE, browser, secure=True, httponly=True, samesite="lax", max_age=600, path="/")
    params = {"client_id": credentials["ClientId"], "redirect_uri": redirect_uri,
              "response_type": "code", "scope": spec["scope"], "state": state,
              "nonce": nonce, "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode(),
              "code_challenge_method": "S256"}
    # LinkedIn's confidential web flow uses state + nonce + client-secret authentication.
    # Its documented OIDC web endpoint does not advertise PKCE support.
    if provider == "linkedin":
        params.pop("code_challenge")
        params.pop("code_challenge_method")
    return spec["authorize"] + "?" + urlencode(params)


async def exchange_identity(request, provider, code, payload):
    spec, credentials = provider_settings(request.app.state.config, provider)
    client = request.app.state.oauth_http
    data = {"grant_type": "authorization_code", "code": code,
            "redirect_uri": payload["redirect_uri"], "client_id": credentials["ClientId"],
            "client_secret": credentials["ClientSecret"]}
    if provider != "linkedin":
        data["code_verifier"] = payload["verifier"]
    response = await client.post(spec["token"], data=data, headers={"Accept": "application/json"})
    response.raise_for_status()
    token = response.json()
    if provider == "github":
        headers = {"Authorization": "Bearer " + token["access_token"], "Accept": "application/vnd.github+json"}
        profile_response = await client.get("https://api.github.com/user", headers=headers)
        profile_response.raise_for_status()
        profile = profile_response.json()
        emails_response = await client.get("https://api.github.com/user/emails", headers=headers)
        emails_response.raise_for_status()
        verified = [row for row in emails_response.json() if row.get("verified") is True]
        verified.sort(key=lambda row: not row.get("primary"))
        if not verified:
            raise ValueError("Verified email required")
        return {"sub": str(profile["id"]), "email": verified[0]["email"],
                "name": profile.get("name") or profile["login"], "email_verified": True}
    keys_response = await client.get(spec["jwks"])
    keys_response.raise_for_status()
    claims = JsonWebToken(["RS256"]).decode(
        token["id_token"], keys_response.json(), claims_cls=CodeIDToken,
        claims_options={"iss": {"essential": True, "values": spec["issuers"]},
                        "aud": {"essential": True, "value": credentials["ClientId"]}},
        claims_params={"nonce": payload["nonce"], "client_id": credentials["ClientId"],
                       "access_token": token.get("access_token")},
    )
    claims.validate(leeway=30)
    if claims.get("email_verified") is not True:
        raise ValueError("Verified provider email required")
    # Google is authoritative for Gmail and Workspace; others still verify by email.
    verified = provider == "google" and (
        str(claims.get("email", "")).lower().endswith("@gmail.com") or bool(claims.get("hd"))
    )
    return {"sub": claims["sub"], "email": claims["email"], "name": claims.get("name", ""),
            "email_verified": verified, "picture": claims.get("picture") if provider == "google" else None}


async def complete_oauth(db, request, response, provider, state, code):
    config = request.app.state.config
    provider_settings(config, provider)
    await enforce_rate_limit(db, request, "oauth_callback")
    attempt = await db.scalar(select(OAuthAttempt).where(OAuthAttempt.state_hash == hash_opaque_token(state))
                              .with_for_update())
    browser = request.cookies.get(OAUTH_COOKIE, "")
    if (not attempt or attempt.provider != provider or attempt.consumed or attempt.expires_at <= utc_now_seconds()
        or not browser or not secrets.compare_digest(attempt.browser_hash, hash_opaque_token(browser))):
        raise auth_error(400, "oauth_state_invalid", "OAuth session is invalid or expired.")
    payload = json.loads(cipher(config).decrypt(attempt.encrypted_payload.encode()))
    attempt.consumed, attempt.encrypted_payload = True, ""
    await db.commit()
    # State is spent before any external request, including a rejected token response.
    identity_data = await exchange_identity(request, provider, code, payload)
    email, subject = normalize_email(identity_data["email"]), identity_data["sub"]
    if not is_valid_auth_email(email) or not isinstance(subject, str) or not subject or len(subject) > 255:
        raise ValueError("Invalid provider identity")
    await lock_email(db, email)
    # Serialize the stable provider subject even if its email changed concurrently.
    await lock_email(db, f"{provider}:{subject}")
    identity = await db.scalar(select(UserAuthIdentity).where(
        UserAuthIdentity.provider == provider, UserAuthIdentity.provider_subject == subject))
    if payload["link_user_id"]:
        user = await locked_user(db, payload["link_user_id"])
        active = await db.scalar(select(AuthSession).where(
            AuthSession.user_id == user.id, AuthSession.session_token_hash == payload["link_session_hash"],
            AuthSession.revoked_at.is_(None), AuthSession.expires_at > utc_now_seconds(),
        ))
        presented = hash_opaque_token(request.cookies.get(AUTH_SESSION_COOKIE_NAME, ""))
        if not active or active.created_at < utc_now_seconds() - 300 or presented != payload["link_session_hash"] or email != user.email_normalized or not user.email_verified_at:
            raise auth_error(403, "oauth_link_rejected", "Sign in again before linking.")
        if identity and identity.user_id != user.id:
            raise auth_error(409, "oauth_link_rejected", "This identity is already linked.")
    elif identity:
        user = await locked_user(db, identity.user_id)
    else:
        user = await db.scalar(select(User).where(User.email_normalized == email))
        if user:
            raise auth_error(409, "account_link_required", "Sign in to your existing account to link this provider.")
        user = User(email_normalized=email, name=(identity_data["name"] or email.split("@")[0])[:64],
                    email_verified_at=utc_now_seconds() if identity_data["email_verified"] else None,
                    email_verification_source=provider if identity_data["email_verified"] else None)
        db.add(user)
        await db.flush()
        if payload.get("learning_track_id"):
            await ensure_enrollment(db, user.id, payload["learning_track_id"])
        if not user.email_verified_at:
            await issue_email_token(db, request, user, "verify")
        else:
            notify(db, request, user, AuthEmailTemplate.WELCOME)
        record_auth_event(db, request, "signup_created", user)
    if identity is None:
        existing_provider = await db.scalar(select(UserAuthIdentity).where(
            UserAuthIdentity.user_id == user.id, UserAuthIdentity.provider == provider))
        if existing_provider:
            raise auth_error(409, "oauth_link_rejected", "A different identity is already linked.")
        db.add(UserAuthIdentity(user_id=user.id, provider=provider, provider_subject=subject))
        record_auth_event(db, request, "auth_method_linked", user)
    # Only retain Google-hosted HTTPS avatars from the already validated ID token.
    # Never fetch a provider-supplied URL on the API server.
    if provider == "google":
        picture = identity_data.get("picture")
        user.profile_picture_url = None
        if isinstance(picture, str) and len(picture) <= 2048:
            try:
                parsed = urlsplit(picture)
                host = parsed.hostname or ""
                if (parsed.scheme == "https" and parsed.port in (None, 443)
                    and not parsed.username and not parsed.password
                    and (host == "googleusercontent.com" or host.endswith(".googleusercontent.com"))):
                    user.profile_picture_url = picture
            except ValueError:
                pass
    await create_auth_session(db, user, request, response)
    await remember_trusted_device_login(db, request, response, user)
    record_auth_event(db, request, "social_login_success", user)
    await db.commit()
