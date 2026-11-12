import asyncio
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config_loader import env
from app.database.database import Database
from app.development_preview import DevelopmentPreviewMiddleware
from app.email.outbox import delivery_loop
from app.email.providers import create_transactional_email_provider
from app.environment import allowed_origins, is_development, validate_runtime_config
from app.observability.logging import RequestLoggingMiddleware, configure_logging
from app.routers.account_router import account_router
from app.routers.audio_review_router import router as audio_review_router
from app.routers.auth_router import auth_router
from app.routers.billing_router import billing_router
from app.routers.course_exercise_router import course_exercise_router
from app.routers.health_router import health_router
from app.routers.learning_router import learning_router
from app.routers.success_feedback_router import router as success_feedback_router
from app.security.course_body_limit import CourseBodyLimitMiddleware
from app.security.middleware import AuthRequestMiddleware
from app.security.password_service import PasswordService
from app.security.turnstile import TurnstileVerifier
from app.services.stripe_gateway import StripeGateway


def create_app(config: dict | None = None, *, email_provider=None, oauth_transport=None, stripe_transport=None, turnstile_transport=None) -> FastAPI:
    config = env.config if config is None else config
    validate_runtime_config(config)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        configure_logging()
        application.state.database = Database(config)
        application.state.stripe = StripeGateway(config, stripe_transport)
        application.state.passwords = PasswordService()
        application.state.turnstile = TurnstileVerifier(config, turnstile_transport)
        application.state.email_provider = email_provider or create_transactional_email_provider(config)
        application.state.oauth_http = httpx.AsyncClient(timeout=10, follow_redirects=False, transport=oauth_transport)
        worker = asyncio.create_task(delivery_loop(application.state.database, config, application.state.email_provider))
        try:
            yield
        finally:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
            await application.state.turnstile.client.aclose()
            await application.state.oauth_http.aclose()
            await application.state.stripe.client.aclose()
            await application.state.database.dispose()

    application = FastAPI(
        title="HeyHanzi Backend", version="0.1.0", lifespan=lifespan,
        docs_url="/docs" if is_development(config) else None,
        redoc_url=None,
        openapi_url="/openapi.json" if is_development(config) else None,
    )
    application.state.config = config
    application.include_router(audio_review_router)
    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(account_router)
    application.include_router(billing_router)
    application.include_router(learning_router)
    application.include_router(course_exercise_router)
    application.include_router(success_feedback_router)

    @application.exception_handler(RequestValidationError)
    async def invalid_payload(request, error):
        # Pydantic's default includes the offending input, which may be a password.
        return JSONResponse({"detail": {"code": "invalid_request", "message": "Request fields are invalid.",
                              "fields": [".".join(map(str, item["loc"])) for item in error.errors()[:10]]}}, 422)

    application.add_middleware(CourseBodyLimitMiddleware)
    application.add_middleware(AuthRequestMiddleware, config=config)
    application.add_middleware(DevelopmentPreviewMiddleware, config=config)
    application.add_middleware(
        CORSMiddleware, allow_origins=allowed_origins(config), allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Request-ID", "X-CSRF-Token", "Retry-After"],
    )
    application.add_middleware(RequestLoggingMiddleware)
    return application


app = create_app()
