"""Checkout and entitlements, serialized per learner and reconciled from Stripe."""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.models.billing import BillingAccount
from app.services.stripe_gateway import enabled, settings, unavailable
from app.utils.time import utc_now_seconds


async def account(db, user_id):
    await db.execute(insert(BillingAccount).values(user_id=user_id, status="free",
        cancel_at_period_end=False, checkout_attempt=0).on_conflict_do_nothing())
    return await db.scalar(select(BillingAccount).where(BillingAccount.user_id == user_id).with_for_update().execution_options(populate_existing=True))


def unlimited(row):
    return bool(row and row.status == "active" and row.period_end and row.period_end > utc_now_seconds())


async def has_access(db, user_id):
    return unlimited(await db.get(BillingAccount, user_id))


async def require_access(db, user_id):
    if not await has_access(db, user_id):
        raise HTTPException(402, detail={"code": "subscription_required"})


async def reconcile(row, gateway, config):
    if not row.customer_id:
        return
    result = await gateway.request("GET", "subscriptions", {"customer": row.customer_id, "status": "all", "limit": "100"})
    if result.get("has_more"):
        raise unavailable()
    subscriptions = []
    for sub in result["data"]:
        items = sub.get("items", {}).get("data", [])
        if len(items) == 1 and (items[0].get("price", {}).get("id") == settings(config).get("AnnualPriceId")
                or sub.get("id") == row.subscription_id):
            # A catalog price change must preserve an existing member's subscription.
            subscriptions.append(sub)
    # Active first; otherwise preserve attention/cancellation state for management.
    subscriptions.sort(key=lambda sub: (sub["status"] == "active", sub.get("created", 0)), reverse=True)
    sub = subscriptions[0] if subscriptions else None
    row.subscription_id = sub["id"] if sub else None
    row.status = sub["status"] if sub else "free"
    row.period_end = sub["items"]["data"][0].get("current_period_end") if sub else None
    row.cancel_at_period_end = bool(sub and (sub.get("cancel_at_period_end") or sub.get("cancel_at")))


def summary(row, config):
    return {"plan": "unlimited" if unlimited(row) else "free", "status": row.status if row else "free",
            "currentPeriodEnd": row.period_end if row else None,
            "cancelAtPeriodEnd": row.cancel_at_period_end if row else False,
            "canManage": bool(row and row.customer_id and row.subscription_id),
            "canSubscribe": not (row and row.subscription_id and row.status not in {"canceled", "incomplete_expired"}),
            "checkoutEnabled": enabled(config)}


async def status(db, user, gateway, config):
    row = await account(db, user.id)
    if row.customer_id:
        await reconcile(row, gateway, config)
    result = summary(row, config)
    await db.commit()
    return result


def return_url(config, locale):
    return config["URLS"]["FrontendUrl"] + ("/zh" if locale == "zh" else "") + "/account"


async def checkout(db, user, locale, gateway, config):
    if not enabled(config):
        raise unavailable()
    row = await account(db, user.id)
    if not row.customer_id:
        customer = await gateway.request("POST", "customers", {"email": user.email_normalized,
            "metadata[heyhanzi_user_id]": user.id}, key=f"heyhanzi-customer-{user.id}")
        row.customer_id = customer["id"]
        await db.commit()
        row = await account(db, user.id)
    await reconcile(row, gateway, config)
    if not summary(row, config)["canSubscribe"]:
        await db.commit()
        raise HTTPException(409, detail={"code": "subscription_exists"})
    if row.checkout_id:
        session = await gateway.request("GET", f"checkout/sessions/{row.checkout_id}")
        if session["status"] == "open":
            items = await gateway.request("GET", f"checkout/sessions/{row.checkout_id}/line_items", {"limit": "2"})
            if (not items.get("has_more") and len(items["data"]) == 1
                    and items["data"][0].get("price", {}).get("id") == settings(config)["AnnualPriceId"]):
                await db.commit()
                return {"url": session["url"]}
            # Replace a pending checkout for an older price, so the displayed total
            # and the actual charge cannot disagree after a catalog update.
            await gateway.request("POST", f"checkout/sessions/{row.checkout_id}/expire", {})
        if session["status"] == "complete" and not (
                session.get("subscription") == row.subscription_id
                and row.subscription_id and row.status in {"canceled", "incomplete_expired"}):
            # Never create another purchase while the first payment is settling.
            await db.commit()
            raise HTTPException(409, detail={"code": "subscription_exists"})
        row.checkout_id = None
        row.checkout_locale = None
        row.checkout_attempt += 1
    if row.checkout_locale is None:
        # Persist immutable parameters before the external request, so a timeout or
        # another tab changing language retries the same Stripe idempotency key.
        row.checkout_locale = locale
        await db.commit()
        return await checkout(db, user, locale, gateway, config)
    locale = row.checkout_locale
    price_id = settings(config)["AnnualPriceId"]
    price = await gateway.request("GET", f"prices/{price_id}")
    if (not price.get("active") or price.get("unit_amount") != 2388 or price.get("currency") != "usd"
            or price.get("recurring", {}).get("interval") != "year"
            or price.get("recurring", {}).get("interval_count") != 1 or price.get("tax_behavior") != "exclusive"):
        raise unavailable()
    url = return_url(config, locale)
    session = await gateway.request("POST", "checkout/sessions", {
        "mode": "subscription", "customer": row.customer_id, "client_reference_id": user.id,
        "line_items[0][price]": price_id, "line_items[0][quantity]": "1",
        "subscription_data[metadata][heyhanzi_user_id]": user.id,
        "success_url": url + "?billing=success#plan", "cancel_url": url + "?billing=cancelled#plan",
        "locale": "zh" if locale == "zh" else "en",
        "payment_method_types[0]": "card",
        "automatic_tax[enabled]": "true", "customer_update[address]": "auto",
        "billing_address_collection": "required",
        "branding_settings[display_name]": "HeyHanzi",
    }, key=f"heyhanzi-checkout-{user.id}-{price_id}-{row.checkout_attempt}")
    row.checkout_id = session["id"]
    await db.commit()
    return {"url": session["url"]}


async def portal(db, user, locale, gateway, config):
    row = await account(db, user.id)
    if not row.customer_id or not settings(config).get("PortalConfigurationId"):
        raise unavailable()
    session = await gateway.request("POST", "billing_portal/sessions", {
        "customer": row.customer_id, "configuration": settings(config)["PortalConfigurationId"],
        "return_url": return_url(config, locale) + "?billing=portal#plan",
        "locale": "zh" if locale == "zh" else "en",
    })
    await db.commit()
    return {"url": session["url"]}


async def webhook(db, event, gateway, config):
    if event.get("type") not in {"checkout.session.completed", "checkout.session.expired",
            "customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted",
            "invoice.paid", "invoice.payment_failed"}:
        return {"received": True}
    obj = event["data"]["object"]
    customer_id = obj.get("customer")
    if not isinstance(customer_id, str):
        return {"received": True}
    row = await db.scalar(select(BillingAccount).where(BillingAccount.customer_id == customer_id).with_for_update())
    if row:
        # Read current Stripe state under the account lock: duplicate and out-of-order
        # deliveries cannot regress access using an old event snapshot.
        await reconcile(row, gateway, config)
        await db.commit()
    return {"received": True}
