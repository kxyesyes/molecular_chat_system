"""Application wiring for the anonymous Agent session service."""
import os
from pathlib import Path

from .agent_session import AgentSessionMiddleware, AgentSessionStore
from .user_llm_config import _safe_path, user_llm_config_path


def agent_session_db_path() -> Path:
    explicit = os.environ.get("MEDCHAT_AGENT_SESSION_DB", "").strip()
    path = Path(explicit) if explicit else user_llm_config_path().parent / "agent_sessions.sqlite"
    try:
        return _safe_path(path)
    except ValueError:
        raise ValueError("Invalid Agent session database path") from None


def setup_agent_sessions(app) -> None:
    """Install once at the app boundary; standalone routers must supply identity."""
    if getattr(app.state, "agent_session_store", None) is None:
        store = AgentSessionStore(agent_session_db_path())
        app.state.agent_session_store = store
        app.add_middleware(AgentEntrySessionMiddleware, store=store)


class AgentEntrySessionMiddleware(AgentSessionMiddleware):
    """Limit session requirements to the home/Agent/task entry points."""

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        protected = path in {"/", "/ws", "/api/tasks"} or any(
            path.startswith(prefix) for prefix in ("/api/tasks/", "/api/agent/workflows/")
        )
        if not protected:
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)
