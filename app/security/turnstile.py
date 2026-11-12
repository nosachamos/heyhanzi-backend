"""Server-owned Turnstile policy and bounded Siteverify boundary."""
import asyncio
import json
import re
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from fastapi import HTTPException

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TEST_SITE_KEYS = {"1x00000000000000000000AA", "2x00000000000000000000AB",
                  "1x00000000000000000000BB", "2x00000000000000000000BB", "3x00000000000000000000FF"}
TEST_SECRET_KEYS = {f"{n}x0000000000000000000000000000000AA" for n in (1, 2, 3)}


def required(config):
    # Missing/unknown environments never provide a development bypass.
    if config.get("GENERAL", {}).get("Environment") not in {"DEV", "TEST"}:
        return True
    return str(config.get("TURNSTILE", {}).get("Enabled", False)).lower() == "true"


def validate_turnstile_config(config):
    if not required(config):
        return
    values = config.get("TURNSTILE", {})
    site, secret, hosts = values.get("SiteKey"), values.get("SecretKey"), values.get("Hostnames")
    valid_keys = all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{10,256}", value)
                     for value in (site, secret))
    valid_hosts = isinstance(hosts, list) and 1 <= len(hosts) <= 10 and all(
        isinstance(host, str) and len(host) <= 253 and all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in host.split(".")) for host in hosts)
    if not valid_keys or not valid_hosts:
        raise ValueError("TURNSTILE requires SiteKey, SecretKey and exact Hostnames")
    if config.get("GENERAL", {}).get("Environment") not in {"DEV", "TEST"}:
        if site in TEST_SITE_KEYS or secret in TEST_SECRET_KEYS or any(
                host == "localhost" or host.endswith(".localhost") or host.startswith("local.")
                or host.replace(".", "").isdigit() for host in hosts):
            raise ValueError("Production TURNSTILE requires production keys and hostnames")


def public_policy(config):
    validate_turnstile_config(config)
    enabled = required(config)
    return {"required": enabled, "siteKey": config["TURNSTILE"]["SiteKey"] if enabled else None}


def rejected():
    return HTTPException(403, detail={"code": "turnstile_failed"})


def unavailable():
    return HTTPException(503, detail={"code": "turnstile_unavailable"}, headers={"Retry-After": "15"})


class TurnstileVerifier:
    def __init__(self, config, transport=None):
        self.config = config
        self.client = httpx.AsyncClient(timeout=4, follow_redirects=False, trust_env=False, transport=transport)

    async def verify(self, token, action):
        if not required(self.config):
            return
        try:
            validate_turnstile_config(self.config)
        except ValueError:
            raise unavailable() from None
        if not isinstance(token, str) or not 1 <= len(token) <= 2048 or not token.strip():
            raise rejected()
        settings = self.config["TURNSTILE"]
        # Only provider transport retries; never replay the account operation.
        payload = {"secret": settings["SecretKey"], "response": token, "idempotency_key": str(uuid4())}
        try:
            async with asyncio.timeout(9):
                for attempt in range(2):
                    try:
                        async with asyncio.timeout(4):
                            async with self.client.stream("POST", SITEVERIFY_URL, json=payload) as response:
                                if response.status_code != 200:
                                    raise unavailable()
                                body = bytearray()
                                async for chunk in response.aiter_bytes():
                                    body.extend(chunk)
                                    if len(body) > 16384:
                                        raise unavailable()
                            result = json.loads(body)
                        self.validate_response(result, action, settings)
                        return
                    except HTTPException as error:
                        if error.status_code != 503:
                            raise
                    except (httpx.HTTPError, ValueError, TimeoutError, RecursionError):
                        pass
                    if attempt == 0:
                        await asyncio.sleep(.1)
        except TimeoutError:
            pass
        raise unavailable() from None

    @staticmethod
    def validate_response(result, action, settings):
        if not isinstance(result, dict) or type(result.get("success")) is not bool:
            raise unavailable()
        if not result["success"]:
            codes = result.get("error-codes")
            if not isinstance(codes, list) or not codes or any(
                    not isinstance(code, str) or code not in {
                        "missing-input-response", "invalid-input-response", "timeout-or-duplicate"} for code in codes):
                raise unavailable()
            raise rejected()
        if result.get("hostname") not in settings["Hostnames"] or result.get("action") != action:
            raise rejected()
        try:
            timestamp = datetime.fromisoformat(result["challenge_ts"].replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - timestamp).total_seconds()
            if not 0 <= age <= 300:
                raise ValueError()
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
            raise rejected() from None
        if result.get("error-codes", []) != []:
            raise unavailable()
