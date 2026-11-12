from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.learning import GuestProgress

TileId = Annotated[str, Field(min_length=1, max_length=128)]
SetField = Annotated[str, Field(min_length=1, max_length=100_000)]
# Spoken set-v1 practice carries an empty response field. The service validates
# protocol identifiers and keeps written answers and grading rules separate.
AnswerField = Annotated[str, Field(min_length=0, max_length=100_000)]
Support = Annotated[str, Field(min_length=1, max_length=64)]
Fingerprint = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
FacetId = Annotated[str, Field(min_length=1, max_length=384)]


class CourseSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instanceId: str = Field(min_length=1, max_length=96)
    revision: int = Field(gt=0)
    fingerprint: Fingerprint
    prepared: bool = False
    draft: list[SetField] = Field(default_factory=list, max_length=64)
    supports: list[Support] = Field(default_factory=list, max_length=20)


class CourseCheck(BaseModel):
    proof: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    model_config = ConfigDict(extra="forbid")
    attemptId: UUID
    revision: int = Field(gt=0)
    fingerprint: Fingerprint
    answer: list[AnswerField] = Field(default_factory=list, max_length=64)
    supports: list[Support] = Field(default_factory=list, max_length=20)


class GuestCourseAttempt(BaseModel):
    proof: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    outcome: str | None = Field(default=None, max_length=24)
    model_config = ConfigDict(extra="forbid")
    id: UUID
    answer: list[AnswerField] = Field(default_factory=list, max_length=64)
    supports: list[Support] = Field(default_factory=list, max_length=20)
    # These response fields may be stored locally, but are never trusted on import.
    correct: bool | None = None
    feedback: str | None = Field(default=None, max_length=2000)


class GuestCourseRun(CourseSave):
    id: UUID
    attempts: list[GuestCourseAttempt] = Field(default_factory=list, max_length=2048)
    status: Literal["draft", "retry", "passed", "failed", "completed"] | None = None
    completed: bool | None = None
    archived: bool | None = None
    observedCharacters: list[Annotated[str, Field(min_length=1,max_length=1)]] = Field(default_factory=list,max_length=256)
    setProgress: dict | None = None
    createdAt: int = Field(default=0, ge=0)
    updatedAt: int = Field(default=0, ge=0)


class GuestAssessmentCoverage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    instanceId: str = Field(min_length=1, max_length=96)
    fingerprint: Fingerprint
    runId: UUID | None = None
    observedFacets: list[Annotated[str, Field(min_length=1, max_length=384)]] = Field(default_factory=list, max_length=100_000)
    checkedFacets: list[Annotated[str, Field(min_length=1, max_length=384)]] = Field(default_factory=list, max_length=100_000)
    selfReviewedFacets: list[Annotated[str, Field(min_length=1, max_length=384)]] = Field(default_factory=list, max_length=100_000)
    initialCheckedFacets: list[FacetId] = Field(default_factory=list, max_length=100_000)
    supportedCheckedFacets: list[FacetId] = Field(default_factory=list, max_length=100_000)
    guidedWritingFacets: list[FacetId] = Field(default_factory=list, max_length=100_000)
    recalledWritingFacets: list[FacetId] = Field(default_factory=list, max_length=100_000)
    supportedWritingFacets: list[FacetId] = Field(default_factory=list, max_length=100_000)
    paperWritingFacets: list[FacetId] = Field(default_factory=list, max_length=100_000)
    completedForms: list[Annotated[int, Field(ge=0, le=100_000)]] = Field(default_factory=list, max_length=10_000)
    proof: Fingerprint


class GuestCourseRunSummary(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: UUID
    instanceId: str = Field(min_length=1, max_length=96)
    revision: int = Field(gt=0)
    fingerprint: Fingerprint
    prepared: bool
    supports: list[Support] = Field(default_factory=list, max_length=20)
    status: Literal['draft', 'retry', 'passed', 'failed', 'completed']
    completed: bool
    archived: bool
    createdAt: int = Field(default=0, ge=0)
    updatedAt: int = Field(default=0, ge=0)
    observedCharacters: list[Annotated[str, Field(min_length=1, max_length=1)]] = Field(default_factory=list, max_length=256)
    setProgress: dict | None = None


class GuestCourseRunReceipt(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run: GuestCourseRunSummary
    events: int = Field(ge=0, le=2048)
    proof: Fingerprint


class GuestCourseProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    developmentPreview: bool = False
    runs: list[GuestCourseRun] = Field(default_factory=list, max_length=12_000)
    coverage: list[GuestAssessmentCoverage] = Field(default_factory=list, max_length=128)
    receipts: list[GuestCourseRunReceipt] = Field(default_factory=list, max_length=12_000)


class GuestCourseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["state", "exercise", "save", "check", "advance"]
    progress: GuestCourseProgress = Field(default_factory=GuestCourseProgress)
    notebook: GuestProgress | None = None
    instanceId: str | None = Field(default=None, min_length=1, max_length=96)
    blockId: str | None = Field(default=None, min_length=1, max_length=128)
    runId: UUID | None = None
    save: CourseSave | None = None
    check: CourseCheck | None = None


class CourseImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accountId: str = Field(min_length=1, max_length=96)
    finalize: bool = True
    progress: GuestCourseProgress
