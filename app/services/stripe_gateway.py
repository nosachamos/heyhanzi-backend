"""Async Stripe HTTP boundary. Keys and upstream error payloads never reach logs/clients."""
import hashlib
import hmac
import json
import time

import httpx
from fastapi import HTTPException

API_VERSION = "2025-06-30.basil"


def unavailable():
    return HTTPException(503, detail={"code": "billing_unavailable"})


def settings(config):
    return config.get("STRIPE", {})


def enabled(config):
    stripe = settings(config)
    return all(stripe.get(key) for key in ("SecretKey", "AnnualPriceId", "WebhookSecret", "PortalConfigurationId"))


class StripeGateway:
    def __init__(self, config, transport=None):
        self.config = config
        self.client = httpx.AsyncClient(base_url="https://api.stripe.com/v1/", timeout=8,
                                       follow_redirects=False, transport=transport)

    async def request(self, method, path, data=None, key=None):
        secret = settings(self.config).get("SecretKey")
        if not secret:
            raise unavailable()
        headers = {"Authorization": f"Bearer {secret}", "Stripe-Version": API_VERSION}
        if key:
            headers["Idempotency-Key"] = key
        try:
            response = await self.client.request(method, path, headers=headers,
                **({"params": data} if method == "GET" else {"data": data}))
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise unavailable() from None

    def verify_event(self, body, signature):
        secret = settings(self.config).get("WebhookSecret")
        if not secret:
            raise unavailable()
        try:
            parts = [item.split("=", 1) for item in signature.split(",")]
            timestamp = next(value for name, value in parts if name == "t")
            if abs(time.time() - int(timestamp)) > 300:
                raise ValueError()
            expected = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
            if not any(name == "v1" and hmac.compare_digest(value, expected) for name, value in parts):
                raise ValueError()
            event = json.loads(body)
            if not isinstance(event, dict) or not isinstance(event.get("data", {}).get("object"), dict):
                raise ValueError()
            return event
        except (ValueError, StopIteration, TypeError, AttributeError):
            raise HTTPException(400, detail={"code": "invalid_stripe_signature"}) from None
