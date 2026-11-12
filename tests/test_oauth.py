import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from authlib.jose import JsonWebKey, JsonWebToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.email.providers import FakeEmailProvider
from app.main import create_app
from app.utils.time import utc_now_seconds
from tests.test_auth_flows import PASSWORD, browser, email_token, signup, wait_email

pytestmark = pytest.mark.integration


class OAuthServer:
    """Provider HTTP boundary; real RSA signing and real application JWT validation."""
    def __init__(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.private_key = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption())
        public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        self.public_key = {**JsonWebKey.import_key(public).as_dict(), "kid": "test-key"}
        self.authorization = {}
        self.overrides = {}
        self.email = "learner@gmail.com"
        self.provider = "google"
        self.token_calls = 0
        self.corrupt_signature = False

    def handle(self, request):
        if request.url.path.endswith("certs") or request.url.path.endswith("jwks"):
            return httpx.Response(200, json={"keys": [self.public_key]})
        if request.url.path.endswith("/user/emails"):
            return httpx.Response(200, json=[{"email": self.email, "verified": True, "primary": True}])
        if request.url.path.endswith("/user"):
            return httpx.Response(200, json={"id": 12345, "login": "learner", "name": "Learner"})
        if request.method == "POST":
            self.token_calls += 1
            form = parse_qs(request.content.decode())
            assert form["client_secret"] == ["test-provider-secret"]
            if self.provider != "linkedin":
                import base64
                import hashlib
                expected = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"][0].encode()).digest()).rstrip(b"=").decode()
                assert expected == self.authorization["code_challenge"][0]
            if self.provider == "github":
                return httpx.Response(200, json={"access_token": "external-provider-access-token"})
            claims = {"iss": "https://accounts.google.com" if self.provider == "google" else "https://www.linkedin.com/oauth",
                      "aud": self.provider + "-client", "sub": "subject-1", "iat": utc_now_seconds(),
                      "exp": utc_now_seconds() + 3600, "nonce": self.authorization["nonce"][0],
                      "email": self.email, "email_verified": True, "name": "Learner", **self.overrides}
            token = JsonWebToken(["RS256"]).encode({"alg": "RS256", "kid": "test-key"}, claims, self.private_key)
            if self.corrupt_signature:
                prefix, signature = token.rsplit(b".", 1)
                token = prefix + b"." + (b"A" if signature[:1] != b"A" else b"B") + signature[1:]
            return httpx.Response(200, json={"id_token": token.decode(), "access_token": "provider-access-token"})
        raise AssertionError(f"Unexpected external request: {request.method} {request.url.host}{request.url.path}")


@pytest.fixture
def oauth_app(postgres_config, run_cli):
    postgres_config["URLS"]["BackendUrl"] = "https://local-api.heyhanzi.com:8002"
    postgres_config["OAUTH"] = {name: {"ClientId": provider + "-client", "ClientSecret": "test-provider-secret"}
                                 for name, provider in (("Google", "google"), ("Github", "github"), ("LinkedIn", "linkedin"))}
    result = run_cli(postgres_config, "scripts/bootstrap_database.py")
    assert result.returncode == 0, result.stderr
    provider, mail = OAuthServer(), FakeEmailProvider()
    return create_app(postgres_config, email_provider=mail, oauth_transport=httpx.MockTransport(provider.handle)), provider, mail


def start(client, provider, *, link=False, track_id=None):
    if link:
        response = client.post(f"/account/auth/{provider.provider}/link", json={})
        assert response.status_code == 200, response.text
        location = response.json()["authorizationUrl"]
    else:
        response = client.get(f"/auth/{provider.provider}/login", params={"learning_track_id": track_id} if track_id else {}, follow_redirects=False)
        assert response.status_code == 302, response.text
        location = response.headers["Location"]
    provider.authorization = parse_qs(urlparse(location).query)
    return provider.authorization["state"][0]


def callback(client, provider, state):
    return client.get(f"/auth/{provider.provider}/callback", params={"state": state, "code": "provider-code"}, follow_redirects=False)


@pytest.mark.parametrize("provider_name", ["google", "github", "linkedin"])
def test_provider_signup_login_and_single_use_state(oauth_app, provider_name):
    app, provider, mail = oauth_app
    provider.provider = provider_name
    with browser(app) as client:
        assert provider_name in client.get("/auth/providers").json()["providers"]
        track_id = client.get("/learning/tracks").json()["tracks"][0]["id"]
        state = start(client, provider, track_id=track_id)
        response = callback(client, provider, state)
        assert response.headers["Location"].endswith("/loading?session=1")
        session = client.get("/auth/session")
        assert session.status_code == 200, session.text
        assert session.json()["email"] == "learner@gmail.com"
        assert session.json()["hasPassword"] is False
        if session.json()["emailVerifiedAt"]:
            assert client.get("/learning/enrollments").json()["trackIds"] == [track_id]
        assert client.get("/account/security").json()["authMethods"] == [provider_name]
        assert "oauth_state_invalid" in callback(client, provider, state).headers["Location"]
        assert provider.token_calls == 1
        state = start(client, provider)
        assert callback(client, provider, state).headers["Location"].endswith("/loading?session=1")
        assert client.get("/auth/session").json()["id"] == session.json()["id"]


@pytest.mark.parametrize("claim,value", [("iss", "https://attacker.example"), ("aud", "wrong-client"),
                                        ("nonce", "wrong-nonce"), ("exp", 1), ("email_verified", False)])
def test_oidc_rejects_invalid_claims(oauth_app, claim, value):
    app, provider, mail = oauth_app
    provider.overrides[claim] = value
    with browser(app) as client:
        response = callback(client, provider, start(client, provider))
        assert response.headers["Location"].endswith("/login?why=error")
        assert client.get("/auth/session").status_code == 401


def test_state_requires_initiating_browser(oauth_app):
    app, provider, mail = oauth_app
    with browser(app) as client:
        state = start(client, provider)
        client.cookies.clear()
        assert "oauth_state_invalid" in callback(client, provider, state).headers["Location"]
        assert provider.token_calls == 0


def test_oidc_rejects_invalid_signature(oauth_app):
    app, provider, mail = oauth_app
    provider.corrupt_signature = True
    with browser(app) as client:
        response = callback(client, provider, start(client, provider))
        assert response.headers["Location"].endswith("/login?why=error")
        assert client.get("/auth/session").status_code == 401


def test_google_password_set_and_explicit_linking(oauth_app):
    app, provider, mail = oauth_app
    with browser(app) as client:
        callback(client, provider, start(client, provider))
        session = client.get("/auth/session")
        client.headers["X-CSRF-Token"] = session.headers["X-CSRF-Token"]
        response = client.post("/account/password/set", json={"password": PASSWORD})
        assert response.status_code == 200, response.text
        assert response.json()["security"]["authMethods"] == ["password", "google"]
        client.headers["X-CSRF-Token"] = response.headers["X-CSRF-Token"]
        assert client.post("/auth/logout", json={}).status_code == 200
        assert client.post("/auth/email/login", json={"email": provider.email, "password": PASSWORD}).status_code == 200


def test_matching_email_does_not_automatically_link(oauth_app):
    app, provider, mail = oauth_app
    with browser(app) as client:
        signup(client, provider.email)
        token = email_token(wait_email(mail, "welcome_verify"))
        assert client.post("/auth/email/verify", json={"token": token}).status_code == 200
        response = callback(client, provider, start(client, provider))
        assert "account_link_required" in response.headers["Location"]
        assert client.get("/account/security").json()["authMethods"] == ["password"]
        state = start(client, provider, link=True)
        response = callback(client, provider, state)
        assert response.headers["Location"].endswith("/loading?session=1")
        assert client.get("/account/security").json()["authMethods"] == ["password", "google"]


def test_non_authoritative_google_email_requires_our_verification(oauth_app):
    app, provider, mail = oauth_app
    provider.email = "learner@example.com"
    with browser(app) as client:
        callback(client, provider, start(client, provider))
        response = client.get("/auth/session")
        assert response.json()["emailVerifiedAt"] is None
        client.headers["X-CSRF-Token"] = response.headers["X-CSRF-Token"]
        assert client.post("/account/password/set", json={"password": PASSWORD}).status_code == 409
        token = email_token(wait_email(mail, "welcome_verify"))
        assert client.post("/auth/email/verify", json={"token": token}).status_code == 200
        assert client.post("/account/password/set", json={"password": PASSWORD}).status_code == 200


def test_provider_tokens_never_enter_browser_responses(oauth_app):
    app, provider, mail = oauth_app
    with browser(app) as client:
        response = callback(client, provider, start(client, provider))
        assert "provider-access-token" not in json.dumps(dict(response.headers)) + response.text


@pytest.mark.parametrize("picture,expected", [
    ("https://lh3.googleusercontent.com/a/profile=s96-c", "https://lh3.googleusercontent.com/a/profile=s96-c"),
    ("https://googleusercontent.com.attacker.example/avatar", None),
    ("http://lh3.googleusercontent.com/avatar", None),
    ("https://user:password@lh3.googleusercontent.com/avatar", None),
    (None, None),
])
def test_google_avatar_is_exposed_by_session_and_refreshes_on_login(oauth_app, picture, expected):
    app, provider, mail = oauth_app
    provider.overrides["picture"] = picture
    with browser(app) as client:
        assert callback(client, provider, start(client, provider)).status_code == 302
        assert client.get("/auth/session").json()["profilePictureUrl"] == expected
        provider.overrides["picture"] = "https://lh3.googleusercontent.com/a/updated=s96-c"
        assert callback(client, provider, start(client, provider)).status_code == 302
        assert client.get("/auth/session").json()["profilePictureUrl"] == provider.overrides["picture"]
