import asyncio
import json

from src.web.chat_handler import ChatHandler


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send_text(self, payload):
        self.messages.append(json.loads(payload))


class FakeAgentSystem:
    llm = None
    skill_router = None

    def should_use_tools(self, message):
        return True

    def execute(self, message, temperature=0.7, mol_count=5, active_skill=None):
        return {
            "success": True,
            "final_answer": "workflow completed",
            "tools_used": ["property_calculator"],
            "active_skill": "comprehensive_evaluation",
            "tool_results": {},
            "agent_events": [
                {
                    "type": "task_started",
                    "message": "workflow started",
                    "task_id": "agent-1",
                    "progress": 0.0,
                },
                {
                    "type": "tool_completed",
                    "message": "property completed",
                    "task_id": "agent-1",
                    "tool_name": "property_calculator",
                    "progress": 1.0,
                },
            ],
        }


class FakeModel:
    async def generate(self, prompt, temperature=0.7, max_tokens=1500):
        return "assistant response"


class FakeRagService:
    is_initialized = False


def test_chat_handler_forwards_agent_events_before_agent_result():
    handler = ChatHandler(
        model=FakeModel(),
        rag_service=FakeRagService(),
        agent_system=FakeAgentSystem(),
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()

    asyncio.run(
        handler._process_message(
            websocket=websocket,
            message="全面评估 CCO",
            enable_rag=False,
            enable_tools=True,
        )
    )

    message_types = [item["type"] for item in websocket.messages]
    assert message_types.count("agent_event") == 2
    assert message_types.index("agent_event") < message_types.index("agent_result")
    assert websocket.messages[1]["event"]["type"] == "task_started"
    assert websocket.messages[2]["event"]["tool_name"] == "property_calculator"
