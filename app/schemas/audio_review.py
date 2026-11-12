from pydantic import BaseModel, ConfigDict, Field, StrictBool


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    approved: StrictBool
    expectedRevision: int = Field(ge=0, strict=True)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


