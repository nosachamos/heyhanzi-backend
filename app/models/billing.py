"""Server-owned billing state. No card details are stored here."""
from sqlalchemy import BigInteger, Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.database import Base


class BillingAccount(Base):
    __tablename__ = "billing_accounts"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    customer_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    subscription_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="free")
    period_end: Mapped[int | None] = mapped_column(BigInteger)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    checkout_id: Mapped[str | None] = mapped_column(String(255))
    checkout_locale: Mapped[str | None] = mapped_column(String(2))
    checkout_attempt: Mapped[int] = mapped_column(BigInteger, default=0)
