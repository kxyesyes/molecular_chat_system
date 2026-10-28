"""Scheduling hints for foreground docking I/O; never an admission gate."""


class DockingRequestPriorityMiddleware:
    def __init__(self, app, *, foreground_io):
        self.app = app
        self.foreground_io = foreground_io

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        prioritized = scope['type'] == 'http' and (
            path.startswith('/api/docking/')
            or path == '/api/tasks' or path.startswith('/api/tasks/')
        )
        if not prioritized:
            return await self.app(scope, receive, send)
        # Wrap the full ASGI response (including session resolution and body
        # streaming), not just the endpoint's initial Response object.
        with self.foreground_io():
            return await self.app(scope, receive, send)
