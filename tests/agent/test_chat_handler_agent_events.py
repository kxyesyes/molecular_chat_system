import asyncio
import json

from fastapi import WebSocketDisconnect

from src.web.chat_handler import ChatHandler


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send_text(self, payload):
        self.messages.append(json.loads(payload))


class ScriptedWebSocket(FakeWebSocket):
    def __init__(self, inbound_messages):
        super().__init__()
        self.inbound_messages = list(inbound_messages)
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def receive_text(self):
        if self.inbound_messages:
            return json.dumps(self.inbound_messages.pop(0))
        raise WebSocketDisconnect()


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
            "workflow_plan": {"workflow_name": "comprehensive_evaluation"},
        }


class FakeModel:
    def __init__(self):
        self.generate_calls = 0
        self.prompts = []

    async def generate(self, prompt, temperature=0.7, max_tokens=1500):
        self.generate_calls += 1
        self.prompts.append(prompt)
        return "assistant response"


class FakeRagService:
    is_initialized = False


class LiveEventAgentSystem(FakeAgentSystem):
    def execute(
        self,
        message,
        temperature=0.7,
        mol_count=5,
        active_skill=None,
        event_callback=None,
    ):
        assert event_callback is not None
        for event in (
            "task_started",
            "planning_started",
            "planning_completed",
            "tool_started",
            "tool_completed",
            "tool_failed",
            "task_completed",
            "task_failed",
        ):
            event_callback(
                {
                    "type": "agent_event",
                    "event": event,
                    "message": f"live {event}",
                    "trace_id": "live-1",
                    "tool": (
                        "property_calculator" if event.startswith("tool_") else None
                    ),
                }
            )
        return {
            "success": True,
            "final_answer": "live workflow completed",
            "tools_used": ["property_calculator"],
            "active_skill": "comprehensive_evaluation",
            "tool_results": {},
            "agent_events": [],
            "workflow_plan": {"workflow_name": "comprehensive_evaluation"},
        }


class NonWorkflowAgentSystem(FakeAgentSystem):
    def execute(self, message, temperature=0.7, mol_count=5, active_skill=None):
        return {
            "success": True,
            "final_answer": "raw react observation",
            "tools_used": ["property_calculator"],
            "active_skill": "ad_hoc",
            "tool_results": {},
            "agent_events": [],
        }


class RagSearchAgentSystem(FakeAgentSystem):
    def execute(self, message, temperature=0.7, mol_count=5, active_skill=None):
        return {
            "success": True,
            "final_answer": "rag workflow completed",
            "tools_used": ["rag_search"],
            "active_skill": "rag_search",
            "tool_results": {
                "rag_search": {
                    "success": True,
                    "data": [
                        {
                            "SMILES": "CCO",
                            "similarity_score": 0.91,
                            "source": "local-test",
                        }
                    ],
                }
            },
            "agent_events": [],
            "workflow_plan": {"workflow_name": "rag_search"},
        }


def test_chat_handler_forwards_agent_events_before_agent_result():
    model = FakeModel()
    handler = ChatHandler(
        model=model,
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
    assert websocket.messages[-1] == {
        "type": "complete",
        "content": "workflow completed",
    }
    assert model.generate_calls == 0


def test_chat_handler_forwards_events_while_agent_is_running():
    handler = ChatHandler(
        model=FakeModel(),
        rag_service=FakeRagService(),
        agent_system=LiveEventAgentSystem(),
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()

    asyncio.run(
        handler._process_message(
            websocket=websocket,
            message="全面评估 CCO 并展示进度",
            enable_rag=False,
            enable_tools=True,
        )
    )

    live_events = [
        item["event"]["event"]
        for item in websocket.messages
        if item["type"] == "agent_event"
    ]
    assert live_events == [
        "task_started",
        "planning_started",
        "planning_completed",
        "tool_started",
        "tool_completed",
        "tool_failed",
        "task_completed",
        "task_failed",
    ]
    result_index = next(
        index
        for index, item in enumerate(websocket.messages)
        if item["type"] == "agent_result"
    )
    assert all(
        index < result_index
        for index, item in enumerate(websocket.messages)
        if item["type"] == "agent_event"
    )


def test_non_workflow_agent_result_can_still_use_model_interpretation():
    model = FakeModel()
    handler = ChatHandler(
        model=model,
        rag_service=FakeRagService(),
        agent_system=NonWorkflowAgentSystem(),
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()

    asyncio.run(
        handler._process_message(
            websocket=websocket,
            message="临时分析 CCO",
            enable_rag=False,
            enable_tools=True,
        )
    )

    assert model.generate_calls == 1
    assert websocket.messages[-1] == {
        "type": "message",
        "message": "assistant response",
    }


def test_chat_handler_uses_canonical_rag_search_result_for_rag_cards():
    handler = ChatHandler(
        model=FakeModel(),
        rag_service=FakeRagService(),
        agent_system=RagSearchAgentSystem(),
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()

    asyncio.run(
        handler._process_message(
            websocket=websocket,
            message="search local knowledge base for CCO",
            enable_rag=False,
            enable_tools=True,
            rag_count=3,
        )
    )

    rag_messages = [item for item in websocket.messages if item["type"] == "rag_info"]
    assert len(rag_messages) == 1
    assert rag_messages[0]["molecules"][0]["smiles"] == "CCO"
    assert rag_messages[0]["molecules"][0]["similarity"] == 0.91


def test_prompt_history_can_be_scoped_to_one_websocket_connection():
    handler = ChatHandler(
        model=FakeModel(),
        rag_service=FakeRagService(),
        agent_system=None,
        config={"inference": {"stream": False}},
    )
    handler.conversation_history = [
        {"user": "other-user-secret", "assistant": "private-answer"}
    ]
    connection_history = [
        {"user": "same-session-message", "assistant": "same-session-answer"}
    ]

    prompt = handler._build_prompt(
        "current message",
        "",
        [],
        conversation_history=connection_history,
    )

    assert "same-session-message" in prompt
    assert "other-user-secret" not in prompt


def test_separate_websocket_connections_do_not_share_conversation_history():
    model = FakeModel()
    handler = ChatHandler(
        model=model,
        rag_service=FakeRagService(),
        agent_system=None,
        config={"inference": {"stream": False}},
    )
    first = ScriptedWebSocket([
        {
            "message": "first-user-private-molecule",
            "enable_rag": False,
            "enable_tools": False,
        }
    ])
    second = ScriptedWebSocket([
        {
            "message": "second-user-question",
            "enable_rag": False,
            "enable_tools": False,
        }
    ])

    asyncio.run(handler.handle_websocket(first))
    asyncio.run(handler.handle_websocket(second))

    assert first.accepted is True
    assert second.accepted is True
    assert len(model.prompts) == 2
    assert "first-user-private-molecule" in model.prompts[0]
    assert "first-user-private-molecule" not in model.prompts[1]
