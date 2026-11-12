"""Bound the larger full-course guest envelope before JSON parsing."""

from starlette.responses import JSONResponse

COURSE_BODY_LIMIT = 40 * 1024 * 1024
COURSE_BODY_TIMEOUT_SECONDS = 30


def is_course_path(path):
    return path == "/learning/course" or path.startswith("/learning/course/")


class CourseBodyLimitMiddleware:
    def __init__(self, app, limit=COURSE_BODY_LIMIT):
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or not is_course_path(scope.get("path", ""))
            or scope.get("method") not in {"POST", "PUT"}
        ):
            return await self.app(scope, receive, send)
        chunks = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.limit:
                return await JSONResponse(
                    {"detail": {"code": "course_progress_too_large"}},
                    status_code=413,
                    headers={"Cache-Control": "no-store"},
                )(scope, receive, send)
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        del chunks
        delivered = False

        async def buffered_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, buffered_receive, send)
