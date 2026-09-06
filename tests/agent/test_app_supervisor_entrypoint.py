import asyncio
import json

from src.agent.supervisor import SupervisorAgent
from src.web.app import MolecularChatApp


class FakeMainLLM:
    model_name = "external-main"


class FakeGeneratorLLM:
    model_name = "gmm-llama:latest"


class FakeTool:
    def __init__(self, name):
        self.name = name


class FakeRejectWebSocket:
    def __init__(self):
        self.accepted = False
        self.closed = False
        self.close_code = None
        self.messages = []

    async def accept(self):
        self.accepted = True

    async def send_text(self, payload):
        self.messages.append(json.loads(payload))

    async def close(self, code=1000):
        self.closed = True
        self.close_code = code


def test_chat_agent_factory_returns_supervisor_with_local_generator(monkeypatch):
    app = MolecularChatApp.__new__(MolecularChatApp)
    app.model = FakeMainLLM()
    app.molecular_generator_model = FakeGeneratorLLM()
    app.agent_state_store = object()

    monkeypatch.setattr(
        "src.agent.tools.get_all_tools",
        lambda generator_llm: [FakeTool("llm_molecular_generator")],
    )

    agent = app._create_chat_agent()

    assert isinstance(agent, SupervisorAgent)
    assert agent.llm is app.model
    assert agent.molecular_generator_llm is app.molecular_generator_model
    assert agent.state_store is app.agent_state_store
    assert agent.orchestrator.state_store is app.agent_state_store
    assert list(agent.tools) == ["llm_molecular_generator"]


def test_websocket_without_chat_handler_fails_closed_instead_of_legacy_fallback():
    app = MolecularChatApp.__new__(MolecularChatApp)
    websocket = FakeRejectWebSocket()

    asyncio.run(app._reject_websocket_without_chat_handler(websocket))

    assert websocket.accepted is True
    assert websocket.closed is True
    assert websocket.close_code == 1011
    assert websocket.messages == [
        {
            "type": "error",
            "message": (
                "Chat service is unavailable because the modern Agent entrypoint "
                "was not initialized. Please check server startup logs."
            ),
        }
    ]
