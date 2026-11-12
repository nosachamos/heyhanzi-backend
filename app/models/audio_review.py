"""Persistent listening decisions, independent of immutable audio releases."""
from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.database import Base
from app.models.auth import identifier


class AudioReviewDecision(Base):
    __tablename__ = 'audio_review_decisions'
    review_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    approved: Mapped[bool] = mapped_column(Boolean)
    revision: Mapped[int] = mapped_column(Integer)
    reviewed_at: Mapped[int] = mapped_column(BigInteger)


class AudioReviewEvent(Base):
    __tablename__ = 'audio_review_events'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    review_key: Mapped[str] = mapped_column(ForeignKey('audio_review_decisions.review_key'), index=True)
    reviewer_id: Mapped[str | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    approved: Mapped[bool] = mapped_column(Boolean)
    revision: Mapped[int] = mapped_column(Integer)
    reviewed_at: Mapped[int] = mapped_column(BigInteger)
    recording: Mapped[dict] = mapped_column(JSON)
