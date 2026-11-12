"""Account-owned attempts, separate from legacy notebook completion history."""
from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.database import Base


class CourseExerciseRun(Base):
    __tablename__ = "learning_course_exercise_runs"
    enrollment_id: Mapped[str] = mapped_column(ForeignKey("learning_enrollments.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(96), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    prepared: Mapped[bool] = mapped_column(Boolean, default=False)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    draft: Mapped[list] = mapped_column(JSON, default=list)
    supports: Mapped[list] = mapped_column(JSON, default=list)
    attempts: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[int] = mapped_column(BigInteger)


class CourseAssessmentCoverage(Base):
    __tablename__ = 'learning_course_assessment_coverage'
    enrollment_id: Mapped[str] = mapped_column(ForeignKey('learning_enrollments.id', ondelete='CASCADE'), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[int] = mapped_column(BigInteger)
