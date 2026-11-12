"""Authentication records. Times are UTC epoch seconds unless named otherwise."""
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.database import Base
from app.utils.time import utc_now_seconds


def identifier() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    created_at: Mapped[int] = mapped_column(BigInteger, default=utc_now_seconds)
    name: Mapped[str] = mapped_column(String(64))
    profile_picture_url: Mapped[str | None] = mapped_column(String(2048))
    email_normalized: Mapped[str] = mapped_column(String(254), unique=True)
    email_verified_at: Mapped[int | None] = mapped_column(BigInteger)
    email_verification_source: Mapped[str | None] = mapped_column(String(32))
    password_hash: Mapped[str | None] = mapped_column(String(512))
    password_updated_at: Mapped[int | None] = mapped_column(BigInteger)
    disabled_at: Mapped[int | None] = mapped_column(BigInteger)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    failure_window_at: Mapped[int] = mapped_column(BigInteger, default=0)
    failure_alert_at: Mapped[int] = mapped_column(BigInteger, default=0)
    new_device_alert_at: Mapped[int] = mapped_column(BigInteger, default=0)


class UserAuthIdentity(Base):
    __tablename__ = "user_auth_identities"
    __table_args__ = (UniqueConstraint("provider", "provider_subject"), UniqueConstraint("user_id", "provider"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    provider_subject: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[int] = mapped_column(BigInteger, default=utc_now_seconds)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[int] = mapped_column(BigInteger, default=utc_now_seconds)
    expires_at: Mapped[int] = mapped_column(BigInteger, index=True)
    revoked_at: Mapped[int | None] = mapped_column(BigInteger)


class AuthToken(Base):
    __tablename__ = "auth_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(16))
    email_normalized: Mapped[str] = mapped_column(String(254))
    created_at: Mapped[int] = mapped_column(BigInteger, default=utc_now_seconds)
    expires_at: Mapped[int] = mapped_column(BigInteger, index=True)
    consumed_at: Mapped[int | None] = mapped_column(BigInteger)


class TrustedDevice(Base):
    __tablename__ = "trusted_devices"
    __table_args__ = (UniqueConstraint("user_id", "token_hash"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[int] = mapped_column(BigInteger, index=True)


class AuthAuditEvent(Base):
    __tablename__ = "auth_audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    ip_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[int] = mapped_column(BigInteger, default=utc_now_seconds, index=True)


class AuthRateLimit(Base):
    __tablename__ = "auth_rate_limits"
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_at: Mapped[int] = mapped_column(BigInteger)
    count: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[int] = mapped_column(BigInteger, index=True)


class OAuthAttempt(Base):
    __tablename__ = "oauth_attempts"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    browser_hash: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(32))
    encrypted_payload: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[int] = mapped_column(BigInteger, index=True)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)


class EmailOutbox(Base):
    __tablename__ = "email_outbox"
    __table_args__ = (Index("ix_email_outbox_due", "status", "available_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    encrypted_payload: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[int] = mapped_column(BigInteger, default=utc_now_seconds)
    expires_at: Mapped[int] = mapped_column(BigInteger, index=True)
    available_at: Mapped[int] = mapped_column(BigInteger, default=utc_now_seconds)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    delivered_at: Mapped[int | None] = mapped_column(BigInteger)
