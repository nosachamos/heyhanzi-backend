"""Named feedback studies and the explicitly activated app-wide snapshot."""
from sqlalchemy import JSON, BigInteger, CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.database import Base


class SuccessFeedbackPreset(Base):
    __tablename__ = 'success_feedback_presets'
    __table_args__ = (CheckConstraint('version > 0', name='positive_version'),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer)
    configuration: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[int] = mapped_column(BigInteger)
    updated_by: Mapped[str | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))


class SuccessFeedbackState(Base):
    __tablename__ = 'success_feedback_state'
    __table_args__ = (
        CheckConstraint("id = 'global'", name='singleton'),
        CheckConstraint('version > 0', name='positive_version'),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    # Deliberately a value snapshot, rather than a live reference to a preset.
    active_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[int] = mapped_column(BigInteger)
    activated_by: Mapped[str | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
