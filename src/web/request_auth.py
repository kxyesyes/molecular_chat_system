"""Small route-level authentication boundary for browser-scoped APIs."""

from fastapi import HTTPException, Request


def require_browser_session(request: Request) -> str:
    session_id = request.scope.get("agent_session_id")
    if type(session_id) is not str or not session_id.strip():
        raise HTTPException(status_code=401, detail="Browser session required")
    return session_id
