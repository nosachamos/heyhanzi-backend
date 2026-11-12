import asyncio

from starlette.responses import JSONResponse

from app.environment import allowed_origins
from app.security.course_body_limit import (
    COURSE_BODY_LIMIT,
    COURSE_BODY_TIMEOUT_SECONDS,
    is_course_path,
)


class AuthRequestMiddleware:
    """Bound auth input before parsing or password hashing; enforce browser origins."""
    def __init__(self, app, config):
        self.app, self.origins = app, allowed_origins(config)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith(("/auth/", "/account/", "/learning/", "/billing/")):
            return await self.app(scope, receive, send)
        if scope["path"] == "/billing/webhook":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            if headers.get(b"origin", b"").decode("latin-1") not in self.origins:
                return await JSONResponse({"detail": {"code": "invalid_origin", "message": "Request origin is not allowed."}}, 403)(scope, receive, send)
            if headers.get(b"content-type", b"").split(b";", 1)[0].lower() != b"application/json":
                return await JSONResponse({"detail": "Content-Type must be application/json"}, 415)(scope, receive, send)
            body = bytearray()
            course = is_course_path(scope['path'])
            try:
                async with asyncio.timeout(COURSE_BODY_TIMEOUT_SECONDS if course else 5):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        body.extend(message.get("body", b""))
                        # Guest progress carries bounded curriculum IDs, never media.
                        # Auth/password payloads retain their smaller existing limit.
                        maximum = COURSE_BODY_LIMIT if course else 4_000_000 if scope['path'] in {'/learning/guest/lesson', '/learning/guest/import'} else 16384
                        if len(body) > maximum:
                            return await JSONResponse({"detail": "Request body too large"}, 413)(scope, receive, send)
                        if not message.get("more_body"):
                            break
            except TimeoutError:
                return await JSONResponse({"detail": "Request body timed out"}, 408)(scope, receive, send)
            delivered = False

            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            return await self.app(scope, bounded_receive, send)
        return await self.app(scope, receive, send)
