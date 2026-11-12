"""Provision HeyHanzi's single annual product/price, portal and webhook. Never deploys."""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.services.stripe_gateway import API_VERSION

EVENTS = ["checkout.session.completed", "checkout.session.expired", "customer.subscription.created",
          "customer.subscription.updated", "customer.subscription.deleted", "invoice.paid", "invoice.payment_failed"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["test", "live"], default="test")
    parser.add_argument("--webhook-url", help="Required for live; omit for local Stripe CLI test forwarding.")
    args = parser.parse_args()
    secret = os.environ.get("STRIPE_SECRET_KEY", "")
    if not secret.startswith((f"sk_{args.mode}_", f"rk_{args.mode}_")):
        parser.error("Set STRIPE_SECRET_KEY for the selected mode in the environment.")
    if args.mode == "live" and not args.webhook_url:
        parser.error("Live mode requires --webhook-url.")
    url = urlsplit(args.webhook_url or "")
    if args.webhook_url and (url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment or url.path != "/billing/webhook"):
        parser.error("Webhook URL must be an HTTPS /billing/webhook endpoint without credentials or query parameters.")
    output = Path(f".stripe-setup.{args.mode}.json")
    previous = json.loads(output.read_text()) if output.exists() else {}
    with httpx.Client(base_url="https://api.stripe.com/v1/", timeout=20, follow_redirects=False,
                      headers={"Authorization": f"Bearer {secret}", "Stripe-Version": API_VERSION}) as client:
        def call(method, path, data=None, key=None):
            response = client.request(method, path,
                headers={"Idempotency-Key": key} if key else {},
                **({"params": data} if method == "GET" else {"data": data}))
            if response.is_error:
                raise RuntimeError(f"Stripe setup failed ({response.status_code}); inspect your Stripe request log.")
            return response.json()

        # A stable product id and lookup key prevent duplicate catalog objects on reruns.
        product_id = "heyhanzi_unlimited"
        product = client.get(f"products/{product_id}")
        if product.status_code == 404:
            call("POST", "products", {"id": product_id, "name": "HeyHanzi Unlimited",
                "description": "Guided stroke order and story lessons for HSK 1–6. USD 1.99/month, billed yearly at USD 23.88 plus applicable tax.",
                "metadata[app]": "heyhanzi"}, "heyhanzi-product-v1")
        elif product.is_error:
            raise RuntimeError("Could not verify the HeyHanzi product.")
        lookup = "heyhanzi_unlimited_usd_annual_v2"
        prices = call("GET", "prices", {"lookup_keys[]": lookup, "limit": "2"})["data"]
        price = prices[0] if prices else call("POST", "prices", {
            "product": product_id, "currency": "usd", "unit_amount": "2388",
            "recurring[interval]": "year", "recurring[interval_count]": "1", "lookup_key": lookup,
            "tax_behavior": "exclusive"}, "heyhanzi-price-v2")
        if (price["product"] != product_id or not price["active"] or price["unit_amount"] != 2388
                or price["currency"] != "usd" or price["recurring"]["interval"] != "year"
                or price["recurring"]["interval_count"] != 1 or price["tax_behavior"] != "exclusive"):
            raise RuntimeError("Existing price differs from the requested annual plan; no prices were changed.")
        call("POST", f"products/{product_id}", {"default_price": price["id"],
            "description": "Guided stroke order and story lessons for HSK 1–6. USD 1.99/month, billed yearly at USD 23.88 plus applicable tax."})
        configurations = call("GET", "billing_portal/configurations", {"limit": "100"})
        portal = next((item for item in configurations["data"] if item.get("metadata", {}).get("app") == "heyhanzi"), None)
        if configurations.get("has_more") and portal is None:
            raise RuntimeError("Portal configuration list needs manual inspection before provisioning.")
        features = {"features[customer_update][enabled]": "true", "features[customer_update][allowed_updates][0]": "address",
            "features[invoice_history][enabled]": "true", "features[payment_method_update][enabled]": "true",
            "features[subscription_cancel][enabled]": "true", "features[subscription_cancel][mode]": "at_period_end",
            "features[subscription_update][enabled]": "false", "metadata[app]": "heyhanzi",
            "business_profile[headline]": "Manage your HeyHanzi membership"}
        portal = call("POST", "billing_portal/configurations" + ("/" + portal["id"] if portal else ""), features,
                      None if portal else "heyhanzi-portal-v1")
        endpoints = call("GET", "webhook_endpoints", {"limit": "100"}) if args.webhook_url else {"data": []}
        endpoint = next((item for item in endpoints["data"] if item["url"] == args.webhook_url), None)
        if not args.webhook_url:
            signing_secret = None
        elif endpoint:
            signing_secret = previous.get("STRIPE", {}).get("WebhookSecret")
            if previous.get("WebhookEndpointId") != endpoint["id"] or not signing_secret:
                raise RuntimeError("Webhook already exists. Recover its signing secret from Stripe into the local setup file before rerunning.")
            if endpoint["status"] != "enabled" or not set(EVENTS).issubset(endpoint["enabled_events"]):
                raise RuntimeError("Existing webhook is disabled or missing required events; repair it in Stripe.")
        else:
            if endpoints.get("has_more"):
                raise RuntimeError("Webhook list needs manual inspection before provisioning.")
            endpoint = call("POST", "webhook_endpoints", {"url": args.webhook_url, "api_version": API_VERSION,
                **{f"enabled_events[{index}]": event for index, event in enumerate(EVENTS)}},
                "heyhanzi-webhook-" + args.mode + "-" + url.hostname)
            signing_secret = endpoint["secret"]
        document = {"ProductId": product_id, "STRIPE": {
            "AnnualPriceId": price["id"], "PortalConfigurationId": portal["id"]}}
        if endpoint:
            document["WebhookEndpointId"] = endpoint["id"]
            document["STRIPE"]["WebhookSecret"] = signing_secret
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(document, stream, indent=2)
            stream.write("\n")
        print(f"Saved product, price, portal and webhook configuration to {output}. Secret values were not printed.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, httpx.HTTPError) as error:
        # HTTP exception strings can contain request details; keep diagnostics bounded.
        raise SystemExit(str(error) if isinstance(error, RuntimeError) else "Stripe connection failed; setup can be retried.") from None
