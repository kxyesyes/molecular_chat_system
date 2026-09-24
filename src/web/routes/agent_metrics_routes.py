"""Agent metrics route registration."""


def setup_agent_metrics_routes(app, *, _support):
    """Register the original endpoints with dynamically resolved compatibility support."""
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
            _support.logger.error(f"获取指标报表失败: {e}")
            return {"success": False, "error": str(e)}
