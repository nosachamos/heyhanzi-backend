import structlog
from fastapi import APIRouter, HTTPException, Request

from app.schemas.health import HealthResponse, ReadinessResponse

health_router = APIRouter(tags=["health"])
logger = structlog.get_logger(__name__)


@health_router.get("/", response_model=HealthResponse, include_in_schema=False)
@health_router.get("/health", response_model=HealthResponse)
async def health_check(request: Request):
    general = request.app.state.config["GENERAL"]
    return HealthResponse(
        deploymentTimestamp=general.get("DeploymentTimestamp"),
        releaseSha=general.get("ReleaseSha"),
    )


@health_router.get("/ready", response_model=ReadinessResponse)
async def readiness_check(request: Request):
    try:
        await request.app.state.database.verify_ready()
    except Exception as error:
        # Driver exceptions can contain credentials/SQL. Only log the error class.
        logger.warning("database_unavailable", error_type=type(error).__name__)
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    return ReadinessResponse()
