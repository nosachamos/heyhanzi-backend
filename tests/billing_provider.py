"""Stripe HTTP test double; application, auth and PostgreSQL remain real."""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs

import httpx


class StripeProvider:
    def __init__(self):
        self.customers = {}
        self.sessions = {}
        self.subscriptions = {}
        self.requests = []
        self.fail = False
        self.price = {"id": "price_annual", "active": True, "unit_amount": 2388, "currency": "usd", "tax_behavior": "exclusive",
                      "recurring": {"interval": "year", "interval_count": 1}}

    def handle(self, request):
        if self.fail:
            return httpx.Response(503, json={"error": {"message": "upstream secret diagnostic"}})
        path = request.url.path.removeprefix("/v1/")
        form = {key: value[0] for key, value in parse_qs(request.content.decode()).items()}
        self.requests.append((request.method, path, form))
        if path == "customers":
            identifier = "cus_" + str(len(self.customers) + 1)
            self.customers[identifier] = form
            return httpx.Response(200, json={"id": identifier})
        if path == "subscriptions":
            return httpx.Response(200, json={"data": self.subscriptions.get(request.url.params["customer"], []), "has_more": False})
        if path == "prices/price_annual":
            return httpx.Response(200, json=self.price)
        if path == "checkout/sessions":
            identifier = "cs_" + str(len(self.sessions) + 1)
            session = {"id": identifier, "status": "open", "url": f"https://checkout.stripe.com/c/pay/{identifier}", **form}
            self.sessions[identifier] = session
            return httpx.Response(200, json=session)
        if path.startswith("checkout/sessions/"):
            if path.endswith("/line_items"):
                session = self.sessions[path.split("/")[-2]]
                return httpx.Response(200, json={"data": [{"price": {"id": session["line_items[0][price]"]}}], "has_more": False})
            if path.endswith("/expire"):
                session = self.sessions[path.split("/")[-2]]
                session["status"] = "expired"
                return httpx.Response(200, json=session)
            return httpx.Response(200, json=self.sessions[path.split("/")[-1]])
        if path == "billing_portal/sessions":
            return httpx.Response(200, json={"url": "https://billing.stripe.com/p/session/test"})
        raise AssertionError(f"Unexpected Stripe HTTP request: {request.method} {path}")

    def pay(self, customer="cus_1", status="active"):
        for session in self.sessions.values():
            if session["customer"] == customer:
                session["status"] = "complete"
                session["subscription"] = "sub_" + customer
        self.subscriptions[customer] = [{"id": "sub_" + customer, "customer": customer,
            "status": status, "created": int(time.time()), "cancel_at_period_end": False,
            "items": {"data": [{"price": {"id": "price_annual"}, "current_period_end": int(time.time()) + 31536000}]}}]

    def event(self, client, event_type="customer.subscription.updated", customer="cus_1", timestamp=None):
        payload = json.dumps({"id": "evt_repeatable", "type": event_type,
                              "data": {"object": {"customer": customer, "status": "stale_snapshot"}}}).encode()
        timestamp = str(timestamp if timestamp is not None else int(time.time()))
        signature = hmac.new(b"whsec_test", timestamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
        return client.post("/billing/webhook", content=payload,
                           headers={"Stripe-Signature": f"t={timestamp},v1={signature}", "Origin": ""})
