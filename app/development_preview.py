"""Explicit local preview, confined to read-only guest request handlers."""
from starlette.responses import JSONResponse

from app.environment import is_development


class DevelopmentPreviewMiddleware:
    def __init__(self, app, config):
        self.app = app
        self.enabled = is_development(config)

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            values = [value for name, value in scope['headers']
                      if name.lower() == b'x-heyhanzi-development-preview']
            if values:
                if (not self.enabled or values != [b'1'] or scope['method'] != 'POST'
                        or scope['path'] not in {'/learning/guest/lesson', '/learning/course/guest'}):
                    response = JSONResponse({'detail': {'code': 'development_preview_unavailable'}},
                                            status_code=403, headers={'Cache-Control': 'no-store'})
                    await response(scope, receive, send)
                    return
                scope.setdefault('state', {})['development_preview'] = True
        await self.app(scope, receive, send)
