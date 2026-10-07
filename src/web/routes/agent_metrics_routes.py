"""Agent metrics route registration."""
import logging

from .route_compat import lazy_dependency

_LOGGER = logging.getLogger(__name__)


def setup_agent_metrics_routes(app, *, logger=None, _support=None):
    """Register with a logger; retain dynamic support for legacy callers only."""
    get_logger = lazy_dependency(
        logger,
        _support,
        "logger",
        label="agent metrics logger",
        default=_LOGGER,
    )

    @app.get("/api/agent/metrics")
    async def get_agent_metrics():
        """获取 Agent 技能性能统计报表"""
        try:
            from src.agent.metrics import metrics_system
            return {
                "success": True,
                "report": metrics_system.get_report()
            }
        except Exception as e:
            get_logger().error(f"获取指标报表失败: {e}")
            return {"success": False, "error": str(e)}
