"""Transactionally queued, encrypted, leased email delivery with bounded retries."""
import asyncio
import json
import uuid
from dataclasses import asdict

import structlog
from cryptography.fernet import Fernet
from sqlalchemy import delete, select, update

from app.email.messages import TransactionalEmailMessage
from app.models.auth import (
    AuthAuditEvent,
    AuthRateLimit,
    AuthSession,
    AuthToken,
    EmailOutbox,
    OAuthAttempt,
    TrustedDevice,
)
from app.utils.time import utc_now_seconds

logger = structlog.get_logger(__name__)


def cipher(config):
    return Fernet(config["AUTH"]["TokenEncryptionKey"].encode())


def enqueue_email(db, config, message, *, expires_at=None):
    db.add(EmailOutbox(
        encrypted_payload=cipher(config).encrypt(json.dumps(asdict(message)).encode()).decode(),
        expires_at=expires_at or utc_now_seconds() + 86400,
    ))


async def deliver_next(database, config, provider) -> bool:
    now = utc_now_seconds()
    async with database.SessionLocal() as db:
        row = await db.scalar(select(EmailOutbox).where(
            EmailOutbox.status.in_(["pending", "sending"]), EmailOutbox.available_at <= now,
        ).order_by(EmailOutbox.available_at).with_for_update(skip_locked=True).limit(1))
        if row is None:
            return False
        if row.expires_at <= now or row.attempts >= 8:
            row.status = "expired" if row.expires_at <= now else "failed"
            row.encrypted_payload = None
            await db.commit()
            return True
        row.status, row.lease_token = "sending", str(uuid.uuid4())
        row.available_at = now + 120
        row.attempts += 1
        row_id, lease, attempts, payload = row.id, row.lease_token, row.attempts, row.encrypted_payload
        await db.commit()
    try:
        message = TransactionalEmailMessage(**json.loads(cipher(config).decrypt(payload.encode())))
        async with asyncio.timeout(30):
            await provider.send(message)
    except Exception as error:
        # Never include provider error text, recipient, or secret-bearing URLs.
        logger.warning("email_delivery_failed", message_id=row_id, error_type=type(error).__name__)
        values = {"status": "pending", "available_at": now + min(3600, 2 ** attempts * 15)}
    else:
        values = {"status": "sent", "delivered_at": utc_now_seconds(), "encrypted_payload": None}
    async with database.SessionLocal() as db:
        await db.execute(update(EmailOutbox).where(EmailOutbox.id == row_id, EmailOutbox.lease_token == lease).values(**values))
        await db.commit()
    return True


async def cleanup_auth_records(database):
    now = utc_now_seconds()
    async with database.SessionLocal() as db:
        # Bounded batches avoid large cleanup transactions as the service grows.
        for model, key, condition in (
            (AuthRateLimit, AuthRateLimit.key_hash, AuthRateLimit.expires_at < now - 86400),
            (OAuthAttempt, OAuthAttempt.state_hash, OAuthAttempt.expires_at < now),
            (AuthToken, AuthToken.token_hash, AuthToken.expires_at < now - 86400),
            (AuthSession, AuthSession.id, AuthSession.expires_at < now - 86400),
            (TrustedDevice, TrustedDevice.id, TrustedDevice.expires_at < now),
            (EmailOutbox, EmailOutbox.id, EmailOutbox.expires_at < now - 7 * 86400),
            (AuthAuditEvent, AuthAuditEvent.id, AuthAuditEvent.created_at < now - 90 * 86400),
        ):
            await db.execute(delete(model).where(key.in_(select(key).where(condition).limit(500))))
        await db.commit()


async def delivery_loop(database, config, provider):
    next_cleanup = 0
    while True:
        try:
            if utc_now_seconds() >= next_cleanup:
                await cleanup_auth_records(database)
                next_cleanup = utc_now_seconds() + 3600
            if await deliver_next(database, config, provider):
                continue
        except Exception as error:
            logger.warning("email_worker_unavailable", error_type=type(error).__name__)
        await asyncio.sleep(2)
