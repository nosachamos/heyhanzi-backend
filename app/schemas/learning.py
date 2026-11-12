from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator


class EnrollmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trackId: str = Field(min_length=1, max_length=64)


class GrammarSeen(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=96)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class LexicalUseSeen(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=96)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class VocabularySeenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vocabularyIds: list[Annotated[str, Field(min_length=1, max_length=96)]] = Field(default_factory=list, max_length=40)
    grammar: list[GrammarSeen] = Field(default_factory=list, max_length=40)
    lexicalUses: list[LexicalUseSeen] = Field(default_factory=list, max_length=40)


class ReadAlongWord(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    start: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    end: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @model_serializer
    def public_word(self):
        return {'text': self.text, **({'start': self.start, 'end': self.end} if self.start is not None else {})}


class ReadAlong(BaseModel):
    audioSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    text: str = Field(min_length=1, max_length=4000)
    words: list[ReadAlongWord] = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def measured_words_match_text(self):
        if ''.join(word.text for word in self.words) != self.text:
            raise ValueError('Read-along words do not match visible text')
        previous = 0
        for word in self.words:
            if word.start is None and word.end is None:
                continue
            if (word.start is None or word.end is None
                    or not previous <= word.start < word.end):
                raise ValueError('Invalid measured read-along word')
            previous = word.end
        return self


class AudioReference(BaseModel):
    """Language-neutral reference to a particular reading, voice and audio version."""
    format: Literal["audio-reference-v1"] = "audio-reference-v1"
    languageTag: str = Field(min_length=2, max_length=32)
    id: str = Field(pattern=r"^[a-z0-9-]{1,96}$")
    version: str = Field(pattern=r"^[0-9a-f]{64}$")
    mimeType: Literal["audio/mpeg"]
    byteLength: int = Field(gt=0, le=2_000_000)
    url: str | None = None
    voiceId: str | None = None
    voices: dict[str, "AudioReference"] | None = None
    qualityWarning: Literal["red", "yellow", "too-fast"] | None = None
    readAlong: ReadAlong | None = None
    qualitySelectionPolicy: Literal[
        "owner-authorized-best-available",
        "owner-authorized-known-quality-exception-2026-11-01",
        "owner-authorized-fast-pace-review-2026-11-01",
    ] | None = None

    @model_validator(mode="after")
    def pace_warning_uses_its_recorded_authorization(self):
        if self.readAlong and self.readAlong.audioSha256 != self.version:
            raise ValueError('Read-along belongs to another recording')
        pace_policy = "owner-authorized-fast-pace-review-2026-11-01"
        if (self.qualityWarning == "too-fast") != (self.qualitySelectionPolicy == pace_policy):
            raise ValueError("Fast-pace review requires its distinct recording authorization")
        return self


class StrokeSample(BaseModel):
    """Illustrative SVG centerlines, not reviewed handwriting instruction."""
    character: str
    viewBox: str = "0 0 100 100"
    paths: list[str]
    illustrative: Literal[True] = True


class StrokeSource(BaseModel):
    dataUrl: str
    dataset: str
    revision: str
    url: str
    sha256: str
    license: str
    copyright: str
    licenseUrl: str
    notice: str


class StrokeStandard(BaseModel):
    name: str
    url: str
    page: int
    sequence: str


class StrokeAnimation(BaseModel):
    """Ordered filled outlines and pen trajectories in Make Me a Hanzi coordinates.

    Coordinates span x=0..1024, y=900..-124 (upward y); one median per outline.
    The format is reusable; ordering and provenance remain language-specific.
    """
    format: Literal["hanzi-writer-v1"]
    character: str = Field(min_length=1, max_length=1)
    strokes: list[str] = Field(min_length=1)
    medians: list[Annotated[list[tuple[float, float]], Field(min_length=2)]]
    source: StrokeSource
    standard: StrokeStandard | None = None

    @model_validator(mode="after")
    def matching_strokes(self):
        if len(self.strokes) != len(self.medians):
            raise ValueError("Each stroke must have a matching pen trajectory")
        return self


class StrokeReference(BaseModel):
    format: Literal["stroke-reference-v1"] = "stroke-reference-v1"
    languageTag: str
    character: str = Field(min_length=1, max_length=1)
    version: str = Field(pattern=r"^[0-9a-f]{64}$")


class MandarinWordData(BaseModel):
    kind: Literal["mandarin-simplified"] = "mandarin-simplified"
    pinyin: str
    characters: list[str]
    strokeOrder: list[StrokeReference] = Field(default_factory=list)


GuestId = Annotated[str, Field(min_length=1, max_length=96)]


class GuestProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    developmentPreview: bool = False
    trackId: str = Field(min_length=1, max_length=64)
    completed: list[GuestId] = Field(default_factory=list, max_length=6000)
    exposures: dict[GuestId, list[GuestId]] = Field(default_factory=dict, max_length=6000)
    pinyinCompleted: list[Literal["welcome", "sounds", "tones", "spelling"]] = Field(default_factory=list, max_length=4)
    grammarExposures: dict[GuestId, list[GrammarSeen]] = Field(default_factory=dict, max_length=6000)
    lexicalUseExposures: dict[GuestId, list[LexicalUseSeen]] = Field(default_factory=dict, max_length=6000)

    @model_validator(mode="after")
    def bounded_exposure(self):
        if sum(len(ids) for ids in self.exposures.values()) > 30000:
            raise ValueError("Too many vocabulary exposures")
        if sum(len(ids) for ids in self.grammarExposures.values()) > 4000:
            raise ValueError("Too many grammar displays")
        if sum(len(refs) for refs in self.lexicalUseExposures.values()) > 4000:
            raise ValueError("Too many lexical-use displays")
        return self


class GuestLessonRequest(GuestProgress):
    operation: Literal["journey", "vocabulary", "strokes"] = "journey"
    level: str | None = Field(default=None, max_length=32)
    around: GuestId | None = None
    before: int | None = Field(default=None, ge=1)
    after: int | None = Field(default=None, ge=1)
    blockId: GuestId | None = None
    vocabularyAfter: GuestId | None = None
    reference: StrokeReference | None = None


class GuestImportRequest(GuestProgress):
    accountId: str = Field(min_length=1, max_length=96)
