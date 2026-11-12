import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.learner_journey import complete_required_before
from tests.test_auth_flows import browser, signup, verified_signup, wait_email

pytestmark = pytest.mark.integration
TRACK = "mandarin-simplified-v1"
PATH = f"/learning/tracks/{TRACK}"


def test_checkout_entitlements_cancellation_and_repurchase(billing_app):
    app, mail, stripe = billing_app
    with browser(app) as client:
        verified_signup(client, mail)
        client.post("/learning/enrollments", json={"trackId": TRACK})
        response = client.get("/billing/status")
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert response.json()["plan"] == "free" and response.json()["checkoutEnabled"]
        assert client.get(PATH + "/journey?level=hsk-1").status_code == 200
        paid_window = client.get(PATH + '/journey?level=hsk-1').json()
        first_paid_id = next(block['id'] for stage in paid_window['stages'] for block in stage['blocks'] if block['kind'] == 'word')
        assert client.post(PATH + f"/blocks/{first_paid_id}/complete", json={}).status_code == 409
        assert client.get(PATH + f"/blocks/{first_paid_id}/vocabulary").status_code == 409
        assert client.post(PATH + f"/blocks/{first_paid_id}/vocabulary/seen", json={"vocabularyIds": ["example-word"]}).status_code == 409
        warmup = client.get(PATH + "/path").json()["stages"][0]["blocks"]
        for word in warmup:
            complete_required_before(client, word['id'])
            assert client.post(PATH + f"/blocks/{word['id']}/complete", json={}).status_code == 200
        complete_required_before(client, 'warmup-end')
        fallback = client.get(PATH + "/journey").json()
        assert fallback["selectedLevelId"] == "hsk-1" and not fallback["hasUnlimitedAccess"]
        complete_required_before(client, first_paid_id)
        assert client.post(PATH + f"/blocks/{first_paid_id}/complete", json={}).status_code == 200
        assert client.get(PATH + f"/blocks/{first_paid_id}/vocabulary").status_code == 200
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: client.post("/billing/checkout", json={"locale": "zh"}), range(2)))
        assert all(response.status_code == 200 for response in responses)
        assert responses[0].json() == responses[1].json()
        assert len(stripe.sessions) == 1 and len(stripe.customers) == 1
        session = next(iter(stripe.sessions.values()))
        assert session["mode"] == "subscription" and session["line_items[0][price]"] == "price_annual"
        assert session["success_url"].endswith("/zh/account?billing=success#plan")
        assert session["automatic_tax[enabled]"] == "true"
        assert client.get("/billing/status?billing=success").json()["plan"] == "free"
        stripe.pay()
        assert stripe.event(client).status_code == 200
        assert stripe.event(client).status_code == 200  # duplicate delivery
        result = client.get("/billing/status").json()
        assert result["plan"] == "unlimited" and result["canManage"]
        assert client.get(PATH + "/journey?level=hsk-1").status_code == 200
        assert client.get(PATH + "/journey?level=hsk-1").json()["hasUnlimitedAccess"]
        assert client.post("/billing/checkout", json={}).status_code == 409
        assert client.post("/billing/portal", json={"locale": "zh"}).status_code == 200
        portal = next(form for _, path, form in stripe.requests if path == "billing_portal/sessions")
        assert portal["customer"] == "cus_1" and portal["configuration"] == "bpc_test"
        assert portal["return_url"].endswith("/zh/account?billing=portal#plan")
        sub = stripe.subscriptions["cus_1"][0]
        sub["cancel_at_period_end"] = True
        assert stripe.event(client).status_code == 200
        result = client.get("/billing/status").json()
        assert result["plan"] == "unlimited" and result["cancelAtPeriodEnd"]
        sub["status"] = "canceled"
        assert stripe.event(client, "customer.subscription.deleted").status_code == 200
        assert stripe.event(client, "customer.subscription.created").status_code == 200  # old delivery cannot restore access
        assert client.get("/billing/status").json()["plan"] == "free"
        assert client.get("/billing/status").json()["canManage"]
        assert client.post("/billing/portal", json={}).status_code == 200
        assert client.get(PATH + f"/blocks/{first_paid_id}/vocabulary").status_code == 200
        assert not client.get(PATH + "/journey?level=hsk-1").json()["hasUnlimitedAccess"]
        assert client.get(PATH + "/path").json()["completedCount"] == len(warmup)
        assert client.get(PATH + "/journey?level=hsk-1").json()["completedCount"] == 1
        assert client.post("/billing/checkout", json={}).status_code == 200
        assert len(stripe.sessions) == 2
        # Historical cancelled subscriptions must not discard a new open checkout.
        assert client.get("/billing/status").json()["plan"] == "free"
        assert client.post("/billing/checkout", json={"locale": "zh"}).status_code == 200
        assert len(stripe.sessions) == 2
        latest = stripe.sessions["cs_2"]
        latest["status"] = "expired"
        assert client.post("/billing/checkout", json={"locale": "zh"}).status_code == 200
        assert len(stripe.sessions) == 3


def test_billing_auth_signatures_isolation_failures_and_invalid_price(billing_app):
    app, mail, stripe = billing_app
    with browser(app) as client:
        assert client.get("/billing/status").status_code == 401
        signup(client)
        wait_email(mail, "welcome_verify")
        assert client.post("/billing/checkout", json={}).status_code == 403
        # A second, verified account; no fixture bypasses auth.
        client.post("/auth/logout", json={})
        verified_signup(client, mail, "paid@example.com")
        assert client.post("/billing/checkout", json={}, headers={"X-CSRF-Token": ""}).status_code == 403
        assert client.post("/billing/checkout", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.post("/billing/checkout", json={"priceId": "price_fake"}).status_code == 422
        stripe.price["unit_amount"] = 2999
        assert client.post("/billing/checkout", json={}).status_code == 503
        assert not stripe.sessions
        stripe.price["unit_amount"] = 2388
        assert client.post("/billing/checkout", json={}).status_code == 200
        stripe.pay(status="incomplete")
        assert stripe.event(client).status_code == 200
        assert client.get("/billing/status").json()["plan"] == "free"
        assert client.post("/billing/checkout", json={}).status_code == 409
        stripe.pay()
        assert client.post("/billing/webhook", content=b'{}').status_code == 400
        assert stripe.event(client, timestamp=int(time.time()) - 301).status_code == 400
        assert stripe.event(client).status_code == 200
        stripe.fail = True
        response = client.get("/billing/status")
        assert response.status_code == 503 and "upstream secret" not in response.text
        stripe.fail = False
        stripe.subscriptions["cus_1"][0]["status"] = "past_due"
        assert stripe.event(client, "invoice.payment_failed").status_code == 200
        assert client.get("/billing/status").json()["plan"] == "free"
        stripe.pay()
        stripe.subscriptions["cus_1"][0]["items"]["data"][0]["current_period_end"] = int(time.time()) - 1
        assert stripe.event(client).status_code == 200
        assert client.get("/billing/status").json()["plan"] == "free"
        stripe.pay()
        stripe.event(client)
        client.post("/auth/logout", json={})
        verified_signup(client, mail, "other@example.com")
        assert client.get("/billing/status").json()["plan"] == "free"
        assert client.post("/billing/portal", json={}).status_code == 503
        assert client.post("/billing/checkout", json={}).status_code == 200
        assert len(stripe.customers) == 2


def test_price_change_replaces_old_checkout_and_preserves_existing_members(billing_app):
    app, mail, stripe = billing_app
    with browser(app) as client:
        verified_signup(client, mail)
        assert client.post("/billing/checkout", json={}).status_code == 200
        stripe.sessions["cs_1"]["line_items[0][price]"] = "price_previous_annual"
        result = client.post("/billing/checkout", json={})
        assert result.status_code == 200
        assert result.json()["url"].endswith("cs_2")
        assert stripe.sessions["cs_1"]["status"] == "expired"
        assert stripe.sessions["cs_2"]["line_items[0][price]"] == "price_annual"
        stripe.pay()
        assert stripe.event(client).status_code == 200
        stripe.subscriptions["cus_1"][0]["items"]["data"][0]["price"]["id"] = "price_previous_annual"
        result = client.get("/billing/status").json()
        assert result["plan"] == "unlimited" and result["canManage"] and not result["canSubscribe"]
