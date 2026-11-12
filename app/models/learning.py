"""Versioned tracks, ordered word blocks, and account-owned learning progress."""
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.database import Base
from app.models.auth import identifier
from app.utils.time import utc_now_seconds


class LearningTrack(Base):
    __tablename__ = "learning_tracks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    language_tag: Mapped[str] = mapped_column(String(32))
    names: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer)
    framework: Mapped[str | None] = mapped_column(String(32))
    curriculum: Mapped[dict | None] = mapped_column(JSON)


class LearningLevel(Base):
    __tablename__ = "learning_levels"
    __table_args__ = (UniqueConstraint("track_id", "key", name="uq_learning_levels_track_key"), UniqueConstraint("track_id", "position", name="uq_learning_levels_track_position"))
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    track_id: Mapped[str] = mapped_column(ForeignKey("learning_tracks.id"), index=True)
    key: Mapped[str] = mapped_column(String(32))
    names: Mapped[dict] = mapped_column(JSON)
    position: Mapped[int] = mapped_column(Integer)


class LearningStage(Base):
    __tablename__ = "learning_stages"
    __table_args__ = (UniqueConstraint("track_id", "position"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    track_id: Mapped[str] = mapped_column(ForeignKey("learning_tracks.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    names: Mapped[dict] = mapped_column(JSON)
    position: Mapped[int] = mapped_column(Integer)
    level: Mapped[str | None] = mapped_column(String(32))
    unit_number: Mapped[int | None] = mapped_column(Integer)


class WordBlock(Base):
    __tablename__ = "learning_word_blocks"
    __table_args__ = (UniqueConstraint("stage_id", "position"), CheckConstraint("position > 0", name="positive_position"))
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    stage_id: Mapped[str] = mapped_column(ForeignKey("learning_stages.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(String(128))
    translations: Mapped[dict] = mapped_column(JSON)
    pronunciation: Mapped[dict] = mapped_column(JSON)
    language_data: Mapped[dict] = mapped_column(JSON)
    kind: Mapped[str] = mapped_column(String(32), server_default="word", default="word")
    sequence: Mapped[int | None] = mapped_column(Integer, index=True)


class BlockCharacter(Base):
    __tablename__ = "learning_block_characters"
    block_id: Mapped[str] = mapped_column(ForeignKey("learning_word_blocks.id"), primary_key=True)
    character: Mapped[str] = mapped_column(String(8), primary_key=True)


class Vocabulary(Base):
    """A lexical reading/meaning, separate from character introduction and mastery."""
    __tablename__ = "learning_vocabulary"
    __table_args__ = (UniqueConstraint("language_tag", "text", "reading_key", "sense_key", name="uq_learning_vocabulary_reading_sense"),)
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    language_tag: Mapped[str] = mapped_column(String(32), index=True)
    text: Mapped[str] = mapped_column(String(128))
    translations: Mapped[dict] = mapped_column(JSON)
    pronunciation: Mapped[dict] = mapped_column(JSON)
    language_data: Mapped[dict] = mapped_column(JSON)
    level: Mapped[str | None] = mapped_column(String(32))
    rank: Mapped[int] = mapped_column(BigInteger)
    reading_key: Mapped[str] = mapped_column(String(128))
    sense_key: Mapped[str] = mapped_column(String(96), server_default="", default="")


class VocabularyCharacter(Base):
    __tablename__ = "learning_vocabulary_characters"
    vocabulary_id: Mapped[str] = mapped_column(ForeignKey("learning_vocabulary.id"), primary_key=True)
    character: Mapped[str] = mapped_column(String(8), primary_key=True, index=True)


class LearningEnrollment(Base):
    __tablename__ = "learning_enrollments"
    __table_args__ = (UniqueConstraint("user_id", "track_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    track_id: Mapped[str] = mapped_column(ForeignKey("learning_tracks.id"))
    pinyin_completed: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    created_at: Mapped[int] = mapped_column(BigInteger, default=utc_now_seconds)


class WordCompletion(Base):
    __tablename__ = "learning_word_completions"
    enrollment_id: Mapped[str] = mapped_column(ForeignKey("learning_enrollments.id", ondelete="CASCADE"), primary_key=True)
    block_id: Mapped[str] = mapped_column(ForeignKey("learning_word_blocks.id"), primary_key=True)
    completed_at: Mapped[int] = mapped_column(BigInteger, default=utc_now_seconds)


class CharacterStrokes(Base):
    __tablename__ = "learning_character_strokes"
    language_tag: Mapped[str] = mapped_column(String(32), primary_key=True)
    character: Mapped[str] = mapped_column(String(8), primary_key=True)
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class VocabularyExposure(Base):
    """A lexical reading displayed on a card, distinct from knowing its characters."""
    __tablename__ = "learning_vocabulary_exposures"
    enrollment_id: Mapped[str] = mapped_column(ForeignKey("learning_enrollments.id", ondelete="CASCADE"), primary_key=True)
    vocabulary_id: Mapped[str] = mapped_column(ForeignKey("learning_vocabulary.id"), primary_key=True)
    block_id: Mapped[str] = mapped_column(ForeignKey("learning_word_blocks.id"))
    displayed_at: Mapped[int] = mapped_column(BigInteger, default=utc_now_seconds)


class GrammarExposure(Base):
    """A specific notebook explanation was displayed; never grammar mastery."""
    __tablename__ = "learning_grammar_exposures"
    enrollment_id: Mapped[str] = mapped_column(ForeignKey("learning_enrollments.id", ondelete="CASCADE"), primary_key=True)
    grammar_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    block_id: Mapped[str] = mapped_column(ForeignKey("learning_word_blocks.id"))
    displayed_at: Mapped[int] = mapped_column(BigInteger, default=utc_now_seconds)


class LexicalUseExposure(Base):
    """A complete lexical-use explanation was displayed; never retrieval/mastery."""
    __tablename__ = "learning_lexical_use_exposures"
    enrollment_id: Mapped[str] = mapped_column(ForeignKey("learning_enrollments.id", ondelete="CASCADE"), primary_key=True)
    vocabulary_id: Mapped[str] = mapped_column(ForeignKey("learning_vocabulary.id"), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    block_id: Mapped[str] = mapped_column(ForeignKey("learning_word_blocks.id"))
    displayed_at: Mapped[int] = mapped_column(BigInteger, default=utc_now_seconds)
