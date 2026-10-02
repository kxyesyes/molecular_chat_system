import asyncio
import json

from fastapi import WebSocketDisconnect

from src.agent.contracts import AgentContext
from src.agent.orchestrators import WorkflowStep
from src.agent.planning import WorkflowPlan
from src.agent.supervisor import SupervisorAgent
from src.web.chat_handler import ChatHandler


KNOWLEDGE_BASE_PROMPT = "search the local knowledge base for molecules similar to CCO"


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


class RecordingLegacyRagService:
    is_initialized = True

    def __init__(self):
        self.calls = []

    async def search_similar_molecules(self, query, count):
        self.calls.append((query, count))
        return [
            {
                "SMILES": "legacy-result",
                "similarity_score": 0.5,
                "source": "legacy-rag",
            }
        ]


class RecordingTool:
    def __init__(self, name, aliases=None):
        self.name = name
        self.aliases = set(aliases or [])
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        data = {"query": query}
        if self.name in {"rag_search", "rag_database_search"}:
            data = [
                {
                    "SMILES": "CCO",
                    "similarity_score": 0.91,
                    "source": "agent-rag",
                }
            ]
        return {
            "success": True,
            "message": f"{self.name} ok",
            "data": data,
            "formatted": f"{self.name}: {query}",
        }


class FixedRouter:
    def __init__(self, skill):
        self.skill = skill

    def route(self, query, llm=None):
        return self.skill


class RagSkill:
    name = "rag_search"
    allowed_tools = ["rag_search"]
    is_workflow = True


class AliasRagSkill(RagSkill):
    allowed_tools = ["rag_database_search"]


class MolecularDesignSkill:
    name = "molecular_design"
    allowed_tools = ["llm_molecular_generator"]
    is_workflow = True


class AliasRagPlanner:
    def plan(self, context):
        return WorkflowPlan(
            workflow_name="rag_search",
            steps=[
                WorkflowStep(
                    name="rag_alias",
                    tool_name="rag_database_search",
                    input_data=context.query,
                    output_key="result",
                )
            ],
        )


class CapturingSupervisor(SupervisorAgent):
    def execute(
        self,
        query,
        temperature=0.7,
        mol_count=5,
        active_skill=None,
        event_callback=None,
        capabilities=None,
    ):
        self.last_result = super().execute(
            query,
            temperature=temperature,
            mol_count=mol_count,
            active_skill=active_skill,
            event_callback=event_callback,
            capabilities=capabilities,
        )
        return self.last_result


def build_rag_supervisor():
    canonical = RecordingTool("rag_search", aliases={"rag_database_search"})
    alias = RecordingTool("rag_database_search")
    supervisor = CapturingSupervisor(
        tools={"rag_search": canonical, "rag_database_search": alias},
        skill_router=FixedRouter(RagSkill()),
    )
    return supervisor, canonical, alias


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


class TargetDesignAgentSystem(FakeAgentSystem):
    def execute(self, message, temperature=0.7, mol_count=5, active_skill=None):
        return {
            "success": True,
            "final_answer": "## PDE5A 候选分子设计结果",
            "tools_used": [
                "target_database_search",
                "llm_molecular_generator",
                "property_calculator",
            ],
            "active_skill": "target_driven_design",
            "tool_results": {},
            "agent_events": [],
            "metadata": {
                "presentation": {
                    "kind": "molecule_candidates",
                    "molecules": [
                        {
                            "smiles": "CCO",
                            "model": "gmm-llama:latest",
                            "source": "llm",
                            "properties": {"qed": 0.41},
                            "validation": {
                                "valid": True,
                                "method": "RDKit",
                            },
                        },
                        {
                            "smiles": "CCN",
                            "model": "gmm-llama:latest",
                            "source": "llm",
                            "properties": {"qed": 0.42},
                            "validation": {
                                "valid": True,
                                "method": "RDKit",
                            },
                        },
                    ],
                }
            },
            "workflow_plan": {"workflow_name": "target_driven_design"},
        }


class EmptyTargetDesignAgentSystem(TargetDesignAgentSystem):
    def execute(self, message, temperature=0.7, mol_count=5, active_skill=None):
        result = super().execute(message, temperature, mol_count, active_skill)
        result["final_answer"] = "未获得有效候选。SMILES: C1CC"
        result["metadata"]["presentation"]["molecules"] = []
        return result


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
            enable_rag=True,
            enable_tools=True,
            rag_count=3,
        )
    )

    rag_messages = [item for item in websocket.messages if item["type"] == "rag_info"]
    assert len(rag_messages) == 1
    assert rag_messages[0]["molecules"][0]["smiles"] == "CCO"
    assert rag_messages[0]["molecules"][0]["similarity"] == 0.91


def test_chat_handler_sends_only_structured_target_design_candidates_on_complete():
    handler = ChatHandler(
        model=FakeModel(),
        rag_service=FakeRagService(),
        agent_system=TargetDesignAgentSystem(),
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()

    asyncio.run(
        handler._process_message(
            websocket=websocket,
            message="针对 PDE5A 设计 10 个候选分子",
            enable_rag=False,
            enable_tools=True,
            mol_count=10,
        )
    )

    assert websocket.messages[-1] == {
        "type": "complete",
        "content": "## PDE5A 候选分子设计结果",
        "molecules": [
            {
                "smiles": "CCO",
                "model": "gmm-llama:latest",
                "source": "llm",
                "properties": {"qed": 0.41},
                "validation": {"valid": True, "method": "RDKit"},
            },
            {
                "smiles": "CCN",
                "model": "gmm-llama:latest",
                "source": "llm",
                "properties": {"qed": 0.42},
                "validation": {"valid": True, "method": "RDKit"},
            },
        ],
    }


def test_chat_handler_preserves_empty_structured_candidate_contract_on_complete():
    handler = ChatHandler(
        model=FakeModel(),
        rag_service=FakeRagService(),
        agent_system=EmptyTargetDesignAgentSystem(),
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()

    asyncio.run(
        handler._process_message(
            websocket=websocket,
            message="针对 PDE5A 设计 10 个候选分子",
            enable_rag=False,
            enable_tools=True,
            mol_count=10,
        )
    )

    assert websocket.messages[-1] == {
        "type": "complete",
        "content": "未获得有效候选。SMILES: C1CC",
        "molecules": [],
    }


def test_agent_context_defaults_capabilities_and_preserves_overrides_with_skill():
    default_context = AgentContext(query="CCO", trace_id="default-capabilities")
    disabled_context = AgentContext(
        query="CCO",
        trace_id="disabled-rag",
        metadata={"capabilities": {"rag": False}},
    ).with_skill("rag_search")

    assert default_context.capabilities == {
        "rag": True,
        "scientific_tools": True,
    }
    assert disabled_context.capabilities == {
        "rag": False,
        "scientific_tools": True,
    }


def test_chat_handler_rag_disabled_blocks_agent_and_legacy_rag():
    supervisor, canonical, alias = build_rag_supervisor()
    legacy_rag = RecordingLegacyRagService()
    handler = ChatHandler(
        model=FakeModel(),
        rag_service=legacy_rag,
        agent_system=supervisor,
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()

    asyncio.run(
        handler._process_message(
            websocket=websocket,
            message=KNOWLEDGE_BASE_PROMPT,
            enable_rag=False,
            enable_tools=True,
        )
    )

    assert canonical.calls == []
    assert alias.calls == []
    assert legacy_rag.calls == []
    assert supervisor.last_result["tools_used"] == []
    assert "capability disabled" in supervisor.last_result["final_answer"].lower()
    assert supervisor.last_result["error"]["details"]["reason"] == (
        "capability_disabled"
    )
    assert not any(item["type"] == "rag_info" for item in websocket.messages)
    assert not any(
        item["type"] == "agent_event"
        and item["event"].get("tool") in {"rag_search", "rag_database_search"}
        for item in websocket.messages
    )
    assert any(
        item["type"] == "status"
        and "capability disabled" in item["message"].lower()
        for item in websocket.messages
    )


def test_chat_handler_rag_enabled_preserves_agent_rag_result_and_card():
    supervisor, canonical, alias = build_rag_supervisor()
    legacy_rag = RecordingLegacyRagService()
    handler = ChatHandler(
        model=FakeModel(),
        rag_service=legacy_rag,
        agent_system=supervisor,
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()

    asyncio.run(
        handler._process_message(
            websocket=websocket,
            message=KNOWLEDGE_BASE_PROMPT,
            enable_rag=True,
            enable_tools=True,
        )
    )

    assert canonical.calls == [KNOWLEDGE_BASE_PROMPT]
    assert alias.calls == []
    assert legacy_rag.calls == []
    assert supervisor.last_result["tools_used"] == ["rag_search"]
    assert supervisor.last_result["tool_results"]["rag_search"]["success"] is True
    rag_messages = [item for item in websocket.messages if item["type"] == "rag_info"]
    assert len(rag_messages) == 1
    assert rag_messages[0]["molecules"][0]["smiles"] == "CCO"


def test_supervisor_execute_blocks_disabled_rag_alias_before_tool_events():
    alias = RecordingTool("rag_database_search")
    supervisor = SupervisorAgent(
        tools={"rag_database_search": alias},
        planner=AliasRagPlanner(),
        skill_router=FixedRouter(AliasRagSkill()),
    )
    events = []

    result = supervisor.execute(
        KNOWLEDGE_BASE_PROMPT,
        active_skill=AliasRagSkill(),
        capabilities={"rag": False, "scientific_tools": True},
        event_callback=lambda event: events.append(event.to_dict()),
    )

    assert result["success"] is False
    assert result["tools_used"] == []
    assert "rag capability disabled" in result["final_answer"].lower()
    assert result["error"]["code"] == "tool_unavailable"
    assert result["error"]["details"]["disabled_tools"] == [
        "rag_database_search"
    ]
    assert alias.calls == []
    assert set(supervisor.tools) == {"rag_database_search"}
    assert not any(event["event"] == "tool_started" for event in events)


def test_supervisor_run_blocks_disabled_rag_from_metadata():
    canonical = RecordingTool("rag_search", aliases={"rag_database_search"})
    alias = RecordingTool("rag_database_search")
    supervisor = SupervisorAgent(
        tools={"rag_search": canonical, "rag_database_search": alias}
    )

    result = supervisor.run(
        KNOWLEDGE_BASE_PROMPT,
        skill_name="rag_search",
        metadata={
            "capabilities": {"rag": False, "scientific_tools": True}
        },
    )

    assert result["status"] == "failed"
    assert "rag capability disabled" in result["message"].lower()
    assert result["result"]["error"]["details"]["reason"] == (
        "capability_disabled"
    )
    assert canonical.calls == []
    assert alias.calls == []
    assert set(supervisor.tools) == {"rag_search", "rag_database_search"}


def test_supervisor_blocks_all_tools_when_scientific_tools_disabled():
    tool = RecordingTool("llm_molecular_generator")
    supervisor = SupervisorAgent(
        tools={"llm_molecular_generator": tool},
        skill_router=FixedRouter(MolecularDesignSkill()),
    )

    result = supervisor.execute(
        "generate one molecule",
        active_skill=MolecularDesignSkill(),
        capabilities={"rag": True, "scientific_tools": False},
    )

    assert result["success"] is False
    assert "scientific_tools capability disabled" in result["final_answer"].lower()
    assert result["tools_used"] == []
    assert tool.calls == []


def test_chat_handler_tools_disabled_uses_plain_chat_without_agent_execution():
    tool = RecordingTool("llm_molecular_generator")
    supervisor = SupervisorAgent(
        tools={"llm_molecular_generator": tool},
        skill_router=FixedRouter(MolecularDesignSkill()),
    )
    model = FakeModel()
    handler = ChatHandler(
        model=model,
        rag_service=FakeRagService(),
        agent_system=supervisor,
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()

    asyncio.run(
        handler._process_message(
            websocket=websocket,
            message="generate one molecule",
            enable_rag=False,
            enable_tools=False,
        )
    )

    assert tool.calls == []
    assert model.generate_calls == 1
    assert websocket.messages[-1] == {
        "type": "message",
        "message": "assistant response",
    }


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
