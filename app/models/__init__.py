"""Register ORM metadata for migrations."""
from app.models.audio_review import AudioReviewDecision, AudioReviewEvent  # noqa: F401
from app.models.auth import (  # noqa: F401
    AuthAuditEvent,
    AuthRateLimit,
    AuthSession,
    AuthToken,
    EmailOutbox,
    OAuthAttempt,
    TrustedDevice,
    User,
    UserAuthIdentity,
)
from app.models.billing import BillingAccount  # noqa: F401
from app.models.course_exercise import CourseAssessmentCoverage, CourseExerciseRun  # noqa: F401
from app.models.learning import (  # noqa: F401
    BlockCharacter,
    CharacterStrokes,
    GrammarExposure,
    LearningEnrollment,
    LearningLevel,
    LearningStage,
    LearningTrack,
    LexicalUseExposure,
    Vocabulary,
    VocabularyCharacter,
    VocabularyExposure,
    WordBlock,
    WordCompletion,
)
from app.models.success_feedback import SuccessFeedbackPreset, SuccessFeedbackState  # noqa: F401
