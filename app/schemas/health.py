from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "heyhanzi-backend"
    deploymentTimestamp: str | None = None
    releaseSha: str | None = None


class ReadinessResponse(BaseModel):
    status: str = "ready"
