import asyncio
from types import SimpleNamespace


def test_production_app_shutdown_closes_and_clears_owned_agent_tools(
    tmp_path, monkeypatch
):
    from src.agent.tooling import build_tool_registry
    from src.agent.tools.target_database_tool import TargetDatabaseTool
    from src.target_search import service as service_module
    from src.web.app import MolecularChatApp

    monkeypatch.setenv("TARGET_DB_PATH", str(tmp_path / "targets.sqlite"))
    monkeypatch.setenv("TARGET_CACHE_DIR", str(tmp_path / "cache"))
    created = []

    class OwnedResolver:
        def __init__(self):
            self.close_calls = 0
            created.append(self)

        def close(self):
            self.close_calls += 1

    monkeypatch.setattr(service_module, "AuthoritativeTargetResolver", OwnedResolver)
    target_tool = TargetDatabaseTool()
    target_tool._get_service()._get_resolver()
    agent_system = SimpleNamespace(tools={target_tool.name: target_tool})
    application = MolecularChatApp.__new__(MolecularChatApp)
    application._llm_watch_task = None
    application.agent_system = agent_system
    application.agent_tool_registry = build_tool_registry(agent_system.tools.values())

    asyncio.run(application.shutdown())
    asyncio.run(application.shutdown())

    assert len(created) == 1
    assert created[0].close_calls == 1
    assert application.agent_tool_registry is None
    assert agent_system.tools == {}
