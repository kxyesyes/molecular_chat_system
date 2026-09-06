import asyncio
import json
from collections.abc import Mapping

import pytest
from fastapi import WebSocketDisconnect

from src.agent.contracts import (
    AgentContext,
    CandidateRecord,
    CandidateSet,
    ObservationStatus,
    ToolProvenance,
    build_candidate_id,
)
from src.agent.orchestrators import WorkflowStep
from src.agent.planning import WorkflowPlan
from src.agent.router import SkillRouter
from src.agent.supervisor import SupervisorAgent
from src.web.chat_handler import ChatHandler


KNOWLEDGE_BASE_PROMPT = "search the local knowledge base for molecules similar to CCO"


def _fake_openai_key(suffix: str) -> str:
    return "sk" + "-" + suffix


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


class FailedScientificAgentSystem:
    llm = None
    skill_router = None

    def __init__(self, *, status="failed", final_answer=None, **overrides):
        self.status = status
        self.final_answer = (
            "提供的 SMILES 无效，性质计算未执行。"
            if final_answer is None
            else final_answer
        )
        self.overrides = overrides

    def should_use_tools(self, message):
        return True

    def execute(self, message, temperature=0.7, mol_count=5, active_skill=None):
        result = {
            "success": False,
            "status": self.status,
            "final_answer": self.final_answer,
            "tools_used": ["property_calculator"],
            "active_skill": "comprehensive_evaluation",
            "trace_id": "trace-invalid-1",
            "warnings": ["invalid_smiles"],
            "error": {
                "code": "invalid_input",
                "message": "SMILES 验证失败。",
                "details": {"field": "smiles"},
            },
            "tool_results": {
                "property_calculator": {
                    "success": False,
                    "status": "failed",
                    "message": "性质计算未执行。",
                    "data": None,
                }
            },
            "tool_result_sequence": [
                {
                    "step_id": "properties",
                    "tool_name": "property_calculator",
                    "success": False,
                    "status": "failed",
                    "message": "性质计算未执行。",
                    "data": None,
                }
            ],
            "candidate_set": {
                "status": "failed",
                "requested_count": 0,
                "valid_count": 0,
                "candidates": [],
            },
            "agent_events": [],
            "workflow_plan": {"workflow_name": "comprehensive_evaluation"},
        }
        result.update(self.overrides)
        return result


class RaisingScientificAgentSystem(FailedScientificAgentSystem):
    def execute(self, message, temperature=0.7, mol_count=5, active_skill=None):
        raise RuntimeError("private-agent-token internal exception repr")


class FixedResultAgentSystem(FailedScientificAgentSystem):
    def __init__(self, result):
        self.result = result

    def execute(self, message, temperature=0.7, mol_count=5, active_skill=None):
        return self.result


def _candidate_set(status=ObservationStatus.SUCCEEDED):
    candidates = (
        CandidateRecord.from_smiles(
            candidate_index=1,
            source_index=1,
            original_smiles="OCC",
            canonical_smiles="CCO",
            generation_provenance={
                "model_name": "gmm-llama:latest",
                "validation": "RDKit",
            },
        ),
    )
    if status == ObservationStatus.SUCCEEDED:
        candidates += (
            CandidateRecord.from_smiles(
                candidate_index=2,
                source_index=2,
                original_smiles="NCC",
                canonical_smiles="CCN",
                generation_provenance={
                    "model_name": "gmm-llama:latest",
                    "validation": "RDKit",
                },
            ),
        )
        return CandidateSet(
            requested_count=2,
            candidates=candidates,
            status=status,
        )
    if status == ObservationStatus.PARTIAL:
        return CandidateSet(
            requested_count=2,
            candidates=candidates,
            invalid_count=1,
            rejected=(
                {
                    "source_index": 2,
                    "smiles": "not-a-smiles",
                    "reason": "invalid_smiles",
                },
            ),
            status=status,
        )
    return CandidateSet(
        requested_count=0,
        candidates=(),
        status=status,
    )


def _candidate_agent_result(
    candidate_set,
    *,
    agent_success=True,
    tool_success=True,
    tool_status=None,
    output_contract="CandidateSet@1",
    tool_result_sequence=None,
    tool_results=None,
    warnings=None,
    provenance=None,
):
    status = tool_status or (
        candidate_set.get("status", "succeeded")
        if isinstance(candidate_set, Mapping)
        else "succeeded"
    )
    observation = {
        "step_id": "generate_candidates",
        "tool_name": "llm_molecular_generator",
        "success": tool_success,
        "status": status,
        "data": candidate_set,
        "quality": (
            {"output_contract": output_contract}
            if output_contract is not None
            else {}
        ),
        "provenance": provenance
        or ToolProvenance(
            tool_name="llm_molecular_generator",
            model_name="gmm-llama:latest",
        ).to_dict(),
        "warnings": warnings or [],
    }
    return {
        "success": agent_success,
        "status": "succeeded" if agent_success else "failed",
        "final_answer": (
            "generated validated candidates"
            if agent_success
            else "candidate workflow failed"
        ),
        "tools_used": ["llm_molecular_generator"],
        "active_skill": "molecular_design",
        "trace_id": "trace-candidates-1",
        "tool_results": tool_results or {},
        "tool_result_sequence": (
            [observation]
            if tool_result_sequence is None
            else tool_result_sequence
        ),
        "agent_events": [],
        "workflow_plan": {"workflow_name": "molecular_design"},
    }


def _run_fixed_agent_result(result, *, model=None):
    model = model or FakeModel()
    handler = ChatHandler(
        model=model,
        rag_service=FakeRagService(),
        agent_system=FixedResultAgentSystem(result),
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()
    asyncio.run(
        handler._process_message(
            websocket=websocket,
            message="设计两个候选分子",
            enable_rag=False,
            enable_tools=True,
        )
    )
    return websocket, model


class InvalidAgentMapping(Mapping):
    def __getitem__(self, key):
        raise RuntimeError("private-invalid-mapping-token")

    def __iter__(self):
        raise RuntimeError("private-invalid-mapping-token")

    def __len__(self):
        return 1


class ExplodingCandidateGetMapping(Mapping):
    def __init__(self, level):
        self.level = level

    def __getitem__(self, key):
        raise KeyError(key)

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def get(self, key, default=None):
        raise RuntimeError(
            f"token=malformed-{self.level}-token-123 "
            "C:\\Users\\reviewer\\private\\candidate.json"
        )


class ExplodingCandidateItemsMapping(Mapping):
    def __getitem__(self, key):
        raise KeyError(key)

    def __iter__(self):
        return iter(("private-warning",))

    def __len__(self):
        return 1

    def items(self):
        raise RuntimeError(
            "api_key=malformed-warning-key-456 "
            "D:\\MedChat\\private\\warning.log"
        )


class ExplodingToolResults(dict):
    def get(self, key, default=None):
        raise RuntimeError(
            "private-post-processing-token at C:/Users/reviewer/private.txt"
        )


class FakeModel:
    def __init__(self):
        self.generate_calls = 0
        self.prompts = []
        self.max_tokens = []

    async def generate(self, prompt, temperature=0.7, max_tokens=1500):
        self.generate_calls += 1
        self.prompts.append(prompt)
        self.max_tokens.append(max_tokens)
        return "assistant response"


class ExternalFakeModel(FakeModel):
    provider_name = "deepseek"


class TruncatedStreamingModel:
    provider_name = "deepseek"

    def __init__(self):
        self.max_tokens = []
        self.last_response_metadata = {}

    async def stream_generate(self, prompt, temperature=0.7, max_tokens=1500):
        self.max_tokens.append(max_tokens)
        yield "药物分子设计是一个多目标优化过程。"
        self.last_response_metadata = {
            "model": "deepseek-v4-pro",
            "finish_reason": "length",
            "content_chars": 18,
        }


class FakeRagService:
    is_initialized = False


class ConfirmationAgentSystem:
    llm = None

    def __init__(self):
        self.skill_router = SkillRouter()
        self.execute_calls = []

    def should_use_tools(self, message):
        return self.skill_router.route(message) is not None

    def execute(self, message, **kwargs):
        self.execute_calls.append((message, kwargs))
        raise AssertionError("confirmation requests must not execute tools")


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


class SensitiveLiveEventAgentSystem(FakeAgentSystem):
    def execute(
        self,
        message,
        temperature=0.7,
        mol_count=5,
        active_skill=None,
        event_callback=None,
    ):
        assert event_callback is not None
        event_callback({
            "type": "agent_event",
            "event": "tool_failed",
            "message": (
                "Tool failed with api_key=live-event-key-123 at "
                "C:\\Users\\reviewer\\private\\tool.log"
            ),
            "trace_id": "live-sensitive-trace",
            "tool": "property_calculator",
            "progress": 0.5,
            "payload": {
                "warnings": ["token=live-warning-token-456"],
                "normal_key\x00": "must-not-overwrite-live-normal-value",
                "normal_key": "keep-live-normal-value",
                "sk-livekey123456789": "unsafe-live-sk-key",
                "api_key=live-nested-key-123": "unsafe-live-api-key",
                "token=live-nested-token-456": "unsafe-live-token-key",
                "C:\\Users\\reviewer\\private\\key.txt": "unsafe-live-user-path",
                "D:\\MedChat\\private\\key.json": "unsafe-live-drive-path",
                "error": {
                    "message": "sk-liveevent123456789",
                    "debug_path": "D:\\MedChat\\private\\debug.json",
                },
            },
        })
        event_callback({
            "type": "agent_event",
            "event": "task_failed",
            "message": "Task failed token=live-task-token-789",
            "trace_id": "live-sensitive-trace",
            "tool": None,
            "progress": 0.75,
            "payload": {"path": "C:\\Users\\reviewer\\private\\task.log"},
        })
        event_callback({
            "type": "agent_event",
            "event": "tool_completed",
            "message": "property calculation completed normally",
            "trace_id": "live-sensitive-trace",
            "tool": "property_calculator",
            "progress": 1.0,
            "payload": {"status": "succeeded", "count": 1},
        })
        return {
            "success": True,
            "final_answer": "live event sanitization completed",
            "tools_used": ["property_calculator"],
            "active_skill": "comprehensive_evaluation",
            "tool_results": {},
            "agent_events": [],
            "workflow_plan": {"workflow_name": "comprehensive_evaluation"},
        }


class SensitiveBufferedEventAgentSystem(FakeAgentSystem):
    def execute(self, message, temperature=0.7, mol_count=5, active_skill=None):
        return {
            "success": True,
            "final_answer": "buffered event sanitization completed",
            "tools_used": ["property_calculator"],
            "active_skill": "comprehensive_evaluation",
            "tool_results": {},
            "agent_events": [
                {
                    "type": "tool_failed",
                    "message": (
                        "Buffered tool failure api_key=buffered-key-123 "
                        "D:\\MedChat\\private\\buffered.log"
                    ),
                    "trace_id": "buffered-sensitive-trace",
                    "tool": "property_calculator",
                    "progress": 0.4,
                    "payload": {
                        "warnings": [
                            "token=buffered-warning-token-456",
                            "C:\\Users\\reviewer\\private\\warning.log",
                        ],
                        "normal_key\x00": (
                            "must-not-overwrite-buffered-normal-value"
                        ),
                        "normal_key": "keep-buffered-normal-value",
                        _fake_openai_key("bufferedkey123456789"): (
                            "unsafe-buffered-sk-key"
                        ),
                        "api_key=buffered-nested-key-123": (
                            "unsafe-buffered-api-key"
                        ),
                        "token=buffered-nested-token-456": (
                            "unsafe-buffered-token-key"
                        ),
                        "C:\\Users\\reviewer\\private\\key.txt": (
                            "unsafe-buffered-user-path"
                        ),
                        "D:\\MedChat\\private\\key.json": (
                            "unsafe-buffered-drive-path"
                        ),
                        "error": {
                            "message": _fake_openai_key("bufferedevent123456789"),
                            "details": {
                                "output_path": "D:\\MedChat\\private\\output.json"
                            },
                        },
                    },
                },
                {
                    "type": "task_failed",
                    "message": "Buffered task failed token=buffered-task-token-789",
                    "trace_id": "buffered-sensitive-trace",
                    "tool": None,
                    "progress": 0.8,
                    "error": {
                        "message": "api_key=buffered-error-key-987",
                        "path": "C:\\Users\\reviewer\\private\\error.log",
                    },
                },
                {
                    "type": "tool_completed",
                    "message": "buffered property calculation completed",
                    "trace_id": "buffered-sensitive-trace",
                    "tool": "property_calculator",
                    "progress": 1.0,
                    "payload": {"status": "succeeded", "count": 2},
                },
            ],
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


@pytest.mark.parametrize(
    "candidate_status",
    [ObservationStatus.SUCCEEDED, ObservationStatus.PARTIAL],
)
def test_successful_agent_emits_strict_candidate_set_before_result(
    candidate_status,
):
    candidate_set = _candidate_set(candidate_status)
    expected = candidate_set.to_dict()
    result = _candidate_agent_result(
        expected,
        warnings=["validated candidates", "token=candidate-warning-token-456"],
    )

    websocket, model = _run_fixed_agent_result(result)

    candidate_messages = [
        item
        for item in websocket.messages
        if item["type"] == "molecule_candidates"
    ]
    assert candidate_messages == [
        {
            "type": "molecule_candidates",
            "trace_id": "trace-candidates-1",
            "source": {
                "tool_name": "llm_molecular_generator",
                "model_name": "gmm-llama:latest",
                "status": candidate_status.value,
            },
            "candidate_set": expected,
            "warnings": ["validated candidates"],
        }
    ]
    restored = CandidateSet.from_dict(candidate_messages[0]["candidate_set"])
    canonical_smiles = [
        candidate.canonical_smiles for candidate in restored.candidates
    ]
    assert canonical_smiles == (
        ["CCO", "CCN"]
        if candidate_status == ObservationStatus.SUCCEEDED
        else ["CCO"]
    )
    assert len(canonical_smiles) == len(set(canonical_smiles))
    message_types = [item["type"] for item in websocket.messages]
    candidate_index = message_types.index("molecule_candidates")
    assert candidate_index < message_types.index("agent_result")
    assert candidate_index < message_types.index("complete")
    assert model.generate_calls == 0


def test_candidate_event_sanitizes_source_provenance_and_warnings():
    candidate = CandidateRecord.from_smiles(
        candidate_index=1,
        source_index=1,
        original_smiles="CCO",
        canonical_smiles="CCO",
        generation_provenance={
            "model_name": "api_key=candidate-provenance-key-123",
            "output_path": "C:\\Users\\reviewer\\private\\candidate.json",
            "safe_note": "x" * 1000,
        },
    )
    candidate_set = CandidateSet(
        requested_count=1,
        candidates=(candidate,),
    ).to_dict()
    provenance = ToolProvenance(
        tool_name="llm_molecular_generator",
        model_name="token=candidate-model-token-456",
    ).to_dict()
    result = _candidate_agent_result(
        candidate_set,
        provenance=provenance,
        warnings=[
            "safe candidate warning",
            _fake_openai_key("candidatewarning123456789"),
            "D:\\MedChat\\private\\warning.log",
            "w" * 1000,
        ],
    )
    result["trace_id"] = "C:\\Users\\reviewer\\private\\trace.json"
    observation = result["tool_result_sequence"][0]
    observation["tool_name"] = "api_key=candidate-tool-key-789"
    observation["input"] = "token=full-input-token-123"
    observation["formatted"] = "D:\\MedChat\\private\\full-output.log"

    websocket, _ = _run_fixed_agent_result(result)

    event = next(
        item
        for item in websocket.messages
        if item["type"] == "molecule_candidates"
    )
    assert event["trace_id"] == ""
    assert event["source"]["tool_name"] == ""
    assert event["source"]["model_name"] == ""
    assert event["warnings"] == ["safe candidate warning", "w" * 256]
    restored = CandidateSet.from_dict(event["candidate_set"])
    generation_provenance = restored.candidates[0].generation_provenance
    assert generation_provenance["model_name"] == "[redacted]"
    assert generation_provenance["output_path"] == "[redacted]"
    assert len(generation_provenance["safe_note"]) <= 512
    serialized = json.dumps(event, ensure_ascii=False)
    for forbidden in (
        "candidate-provenance-key-123",
        "candidate-model-token-456",
        "candidate-tool-key-789",
        "full-input-token-123",
        _fake_openai_key("candidatewarning123456789"),
        "C:\\Users\\",
        "D:\\MedChat\\private",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize("tool_status", ["failed", "rejected", "cancelled"])
def test_candidate_event_rejects_terminal_tool_status(tool_status):
    result = _candidate_agent_result(
        _candidate_set().to_dict(),
        tool_status=tool_status,
    )

    websocket, _ = _run_fixed_agent_result(result)

    assert not any(
        item["type"] == "molecule_candidates" for item in websocket.messages
    )


@pytest.mark.parametrize(
    "candidate_status",
    [
        ObservationStatus.FAILED,
        ObservationStatus.REJECTED,
        ObservationStatus.CANCELLED,
    ],
)
def test_candidate_event_rejects_terminal_candidate_set_status(
    candidate_status,
):
    candidate_set = _candidate_set(candidate_status).to_dict()
    result = _candidate_agent_result(
        candidate_set,
        tool_status="succeeded",
    )

    websocket, _ = _run_fixed_agent_result(result)

    assert not any(
        item["type"] == "molecule_candidates" for item in websocket.messages
    )


@pytest.mark.parametrize(
    ("result_overrides", "candidate_mutator"),
    [
        ({"tool_success": False}, None),
        ({"output_contract": None}, None),
        ({"output_contract": "CandidateSet@2"}, None),
        ({}, lambda _data: "api_key=malformed-candidate-key-123"),
        (
            {},
            lambda data: {
                **data,
                "candidates": [
                    {**data["candidates"][0], "candidate_id": "cand-001-deadbeef"},
                    data["candidates"][1],
                ],
            },
        ),
        (
            {},
            lambda data: {
                **data,
                "candidates": [
                    {**data["candidates"][0], "canonical_smiles": "CCCl"},
                    data["candidates"][1],
                ],
            },
        ),
        (
            {},
            lambda data: {
                **data,
                "candidates": [
                    data["candidates"][0],
                    {
                        **data["candidates"][1],
                        "candidate_id": build_candidate_id(2, "CCO"),
                        "canonical_smiles": "CCO",
                        "smiles": "CCO",
                    },
                ],
            },
        ),
        (
            {},
            lambda data: {
                **data,
                "requested_count": 0,
                "valid_count": 0,
                "unique_count": 0,
                "candidates": [],
            },
        ),
    ],
    ids=[
        "success-false",
        "missing-contract",
        "wrong-contract",
        "malformed-data",
        "forged-candidate-id",
        "canonical-mismatch",
        "duplicate-canonical-smiles",
        "empty-candidates",
    ],
)
def test_candidate_event_rejects_untrusted_observation_data(
    result_overrides,
    candidate_mutator,
    caplog,
):
    candidate_set = _candidate_set().to_dict()
    if candidate_mutator is not None:
        candidate_set = candidate_mutator(candidate_set)
    result = _candidate_agent_result(candidate_set, **result_overrides)

    websocket, _ = _run_fixed_agent_result(result)

    assert not any(
        item["type"] == "molecule_candidates" for item in websocket.messages
    )
    logs = caplog.text
    assert "api_key=malformed-candidate-key-123" not in logs
    if not isinstance(candidate_set, Mapping):
        assert "Skipped malformed molecule candidate observation" in logs


def test_candidate_event_uses_only_tool_result_sequence():
    candidate_set = _candidate_set().to_dict()
    result = _candidate_agent_result(
        candidate_set,
        tool_result_sequence=[],
        tool_results={
            "llm_molecular_generator": {
                "success": True,
                "status": "succeeded",
                "data": candidate_set,
                "quality": {"output_contract": "CandidateSet@1"},
            }
        },
    )

    websocket, _ = _run_fixed_agent_result(result)

    assert not any(
        item["type"] == "molecule_candidates" for item in websocket.messages
    )


@pytest.mark.parametrize(
    "malformed_level",
    ["observation", "quality", "provenance", "warnings"],
)
def test_malformed_candidate_mapping_is_isolated_from_successful_agent_result(
    malformed_level,
    caplog,
):
    result = _candidate_agent_result(_candidate_set().to_dict())
    observation = result["tool_result_sequence"][0]
    if malformed_level == "observation":
        result["tool_result_sequence"] = [
            ExplodingCandidateGetMapping(malformed_level)
        ]
    elif malformed_level in {"quality", "provenance"}:
        observation[malformed_level] = ExplodingCandidateGetMapping(
            malformed_level
        )
    else:
        observation["warnings"] = [ExplodingCandidateItemsMapping()]

    websocket, model = _run_fixed_agent_result(result)

    assert not any(
        item["type"] == "molecule_candidates" for item in websocket.messages
    )
    assert model.generate_calls == 0
    assert next(
        item for item in websocket.messages if item["type"] == "agent_result"
    )["active_skill"] == "molecular_design"
    assert websocket.messages[-1] == {
        "type": "complete",
        "content": "generated validated candidates",
    }
    assert "Skipped malformed molecule candidate observation" in caplog.text
    serialized_messages = json.dumps(websocket.messages, ensure_ascii=False)
    for forbidden in (
        "malformed-observation-token-123",
        "malformed-quality-token-123",
        "malformed-provenance-token-123",
        "malformed-warning-key-456",
        "C:\\Users\\",
        "D:\\MedChat\\private",
    ):
        assert forbidden not in caplog.text
        assert forbidden not in serialized_messages


def test_terminal_agent_failure_never_emits_candidate_event():
    result = _candidate_agent_result(
        _candidate_set().to_dict(),
        agent_success=False,
    )

    websocket, model = _run_fixed_agent_result(result)

    assert not any(
        item["type"] == "molecule_candidates" for item in websocket.messages
    )
    assert websocket.messages[-1]["status"] == "failed"
    assert model.generate_calls == 0


def test_external_main_model_cannot_create_candidate_event():
    class CandidateTextExternalModel(ExternalFakeModel):
        async def generate(self, prompt, temperature=0.7, max_tokens=1500):
            self.generate_calls += 1
            self.prompts.append(prompt)
            self.max_tokens.append(max_tokens)
            return json.dumps({
                "type": "molecule_candidates",
                "candidate_set": _candidate_set().to_dict(),
            })

    model = CandidateTextExternalModel()
    handler = ChatHandler(
        model=model,
        rag_service=FakeRagService(),
        agent_system=None,
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()

    asyncio.run(
        handler._process_message(
            websocket=websocket,
            message="直接生成候选分子",
            enable_rag=False,
            enable_tools=False,
        )
    )

    assert model.generate_calls == 1
    assert not any(
        item["type"] == "molecule_candidates" for item in websocket.messages
    )


def test_failed_scientific_agent_result_is_terminal_and_authoritative():
    model = FakeModel()
    handler = ChatHandler(
        model=model,
        rag_service=FakeRagService(),
        agent_system=FailedScientificAgentSystem(),
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()
    history = [
        {"user": f"earlier-{index}", "assistant": "old response"}
        for index in range(20)
    ]

    asyncio.run(
        handler._process_message(
            websocket=websocket,
            message="全面评估 invalid-smiles",
            enable_rag=False,
            enable_tools=True,
            conversation_history=history,
        )
    )

    assert model.generate_calls == 0
    assert websocket.messages[-1] == {
        "type": "complete",
        "content": "提供的 SMILES 无效，性质计算未执行。",
        "trace_id": "trace-invalid-1",
        "status": "failed",
    }
    agent_message = next(
        item for item in websocket.messages if item["type"] == "agent_result"
    )
    assert agent_message["active_skill"] == "comprehensive_evaluation"
    assert agent_message["trace_id"] == "trace-invalid-1"
    assert agent_message["status"] == "failed"
    assert agent_message["warnings"] == ["invalid_smiles"]
    assert not any(
        item["type"] == "molecule_candidates" for item in websocket.messages
    )
    assert len(history) == 20
    assert history[-1]["agent_used"] is True
    assert history[-1]["agent_response"] == "提供的 SMILES 无效，性质计算未执行。"
    assert history[-1]["assistant"] == "提供的 SMILES 无效，性质计算未执行。"


@pytest.mark.parametrize(
    ("agent_result", "expected"),
    [
        (
            {
                "final_answer": "   ",
                "error": {"message": "结构化输入错误。"},
                "tool_result_sequence": [
                    {"success": False, "message": "工具失败。"}
                ],
            },
            "结构化输入错误。",
        ),
        (
            {
                "final_answer": "",
                "error": {"message": ""},
                "tool_result_sequence": [
                    {"success": True, "message": "成功结果不可作为失败原因。"},
                    {"success": False, "message": "性质工具未完成。"},
                    {"success": False, "message": "后续失败。"},
                ],
            },
            "性质工具未完成。",
        ),
        (
            {
                "final_answer": "",
                "error": {
                    "message": ValueError("internal exception repr"),
                    "details": {"api_key": "private-key-do-not-expose"},
                },
                "tool_result_sequence": [
                    {
                        "success": False,
                        "message": None,
                        "exception": RuntimeError("private-host-token"),
                    }
                ],
            },
            "科学计算未成功完成，请检查输入或工具状态后重试。",
        ),
    ],
)
def test_agent_failure_content_uses_only_safe_authoritative_messages(
    agent_result,
    expected,
):
    content = ChatHandler._agent_failure_content(agent_result)

    assert content == expected
    assert "internal exception repr" not in content
    assert "private-key-do-not-expose" not in content
    assert "private-host-token" not in content


@pytest.mark.parametrize("status", ["rejected", "cancelled"])
def test_rejected_or_cancelled_scientific_agent_status_is_terminal(status):
    model = FakeModel()
    rag = RecordingLegacyRagService()
    handler = ChatHandler(
        model=model,
        rag_service=rag,
        agent_system=FailedScientificAgentSystem(status=status),
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

    assert model.generate_calls == 0
    assert rag.calls == []
    assert websocket.messages[-1]["status"] == status
    assert next(
        item for item in websocket.messages if item["type"] == "agent_result"
    )["status"] == status


def test_failed_scientific_agent_does_not_fall_through_to_initialized_legacy_rag():
    model = FakeModel()
    rag = RecordingLegacyRagService()
    handler = ChatHandler(
        model=model,
        rag_service=rag,
        agent_system=FailedScientificAgentSystem(),
        config={"inference": {"stream": False}},
    )

    asyncio.run(
        handler._process_message(
            websocket=FakeWebSocket(),
            message="全面评估 CCO",
            enable_rag=True,
            enable_tools=True,
        )
    )

    assert rag.calls == []
    assert model.generate_calls == 0


def test_agent_failure_warnings_mapping_is_not_forwarded_as_a_warning_list():
    handler = ChatHandler(
        model=FakeModel(),
        rag_service=FakeRagService(),
        agent_system=FailedScientificAgentSystem(
            warnings={"private": "do-not-forward"}
        ),
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()

    asyncio.run(
        handler._process_message(
            websocket=websocket,
            message="全面评估 invalid-smiles",
            enable_rag=False,
            enable_tools=True,
        )
    )

    agent_message = next(
        item for item in websocket.messages if item["type"] == "agent_result"
    )
    assert agent_message["warnings"] == []


def test_agent_execution_exception_uses_safe_terminal_fallback_without_secret():
    model = FakeModel()
    handler = ChatHandler(
        model=model,
        rag_service=FakeRagService(),
        agent_system=RaisingScientificAgentSystem(),
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

    assert model.generate_calls == 0
    assert websocket.messages[-1]["content"] == (
        "科学计算未成功完成，请检查输入或工具状态后重试。"
    )
    assert "private-agent-token" not in json.dumps(
        websocket.messages,
        ensure_ascii=False,
    )


@pytest.mark.parametrize(
    "malformed_result",
    [None, [], "not-an-agent-result", InvalidAgentMapping()],
    ids=["none", "list", "string", "invalid-mapping"],
)
def test_malformed_agent_result_is_safe_terminal_without_rag_or_model(
    malformed_result,
):
    model = FakeModel()
    rag = RecordingLegacyRagService()
    handler = ChatHandler(
        model=model,
        rag_service=rag,
        agent_system=FixedResultAgentSystem(malformed_result),
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()
    history = []

    asyncio.run(
        handler._process_message(
            websocket=websocket,
            message="全面评估 CCO",
            enable_rag=True,
            enable_tools=True,
            conversation_history=history,
        )
    )

    assert rag.calls == []
    assert model.generate_calls == 0
    assert websocket.messages[-1] == {
        "type": "complete",
        "content": "科学计算未成功完成，请检查输入或工具状态后重试。",
        "trace_id": "",
        "status": "failed",
    }
    assert history[-1]["agent_used"] is True
    serialized = json.dumps(
        {"messages": websocket.messages, "history": history},
        ensure_ascii=False,
    )
    assert "private-invalid-mapping-token" not in serialized
    assert "not-an-agent-result" not in serialized


def test_post_agent_processing_exception_is_safe_terminal_without_fallback():
    result = {
        "success": True,
        "status": "completed",
        "final_answer": "must not be used after post-processing failure",
        "tools_used": ["rag_search"],
        "active_skill": "rag_search",
        "trace_id": "trace-post-processing",
        "warnings": [],
        "tool_results": ExplodingToolResults(),
        "agent_events": [],
        "workflow_plan": {"workflow_name": "rag_search"},
    }
    model = FakeModel()
    rag = RecordingLegacyRagService()
    handler = ChatHandler(
        model=model,
        rag_service=rag,
        agent_system=FixedResultAgentSystem(result),
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()
    history = []

    asyncio.run(
        handler._process_message(
            websocket=websocket,
            message="search local knowledge base for CCO",
            enable_rag=True,
            enable_tools=True,
            conversation_history=history,
        )
    )

    assert rag.calls == []
    assert model.generate_calls == 0
    assert websocket.messages[-1]["type"] == "complete"
    assert websocket.messages[-1]["status"] == "failed"
    assert websocket.messages[-1]["content"] == (
        "科学计算未成功完成，请检查输入或工具状态后重试。"
    )
    serialized = json.dumps(
        {"messages": websocket.messages, "history": history},
        ensure_ascii=False,
    )
    assert "private-post-processing-token" not in serialized
    assert "C:/Users/" not in serialized


@pytest.mark.parametrize("message_source", ["final_answer", "error", "tool"])
def test_sensitive_agent_failure_content_uses_generic_terminal_message(
    message_source,
):
    sensitive_message = (
        "科学计算失败: api_key=chat-handler-test-key-123 "
        "token=chat-handler-test-token-456 "
        + _fake_openai_key("testfailure123456789")
        + " "
        "C:/Users/reviewer/private/result.json "
        "D:\\MedChat\\private\\trace.log"
    )
    overrides = {
        "final_answer": "",
        "error": {"message": ""},
        "tool_result_sequence": [],
        "warnings": [
            "safe scientific warning",
            "api_key=warning-test-key-123",
            "token=warning-test-token-456",
            _fake_openai_key("warningtest123456789"),
            "C:/Users/reviewer/private/warning.txt",
            "D:\\MedChat\\private\\warning.log",
        ],
    }
    if message_source == "final_answer":
        overrides["final_answer"] = sensitive_message
    elif message_source == "error":
        overrides["error"] = {"message": sensitive_message}
    else:
        overrides["tool_result_sequence"] = [
            {"success": False, "status": "failed", "message": sensitive_message}
        ]
    model = FakeModel()
    handler = ChatHandler(
        model=model,
        rag_service=FakeRagService(),
        agent_system=FailedScientificAgentSystem(**overrides),
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()
    history = []

    asyncio.run(
        handler._process_message(
            websocket=websocket,
            message="全面评估 CCO",
            enable_rag=False,
            enable_tools=True,
            conversation_history=history,
        )
    )

    assert model.generate_calls == 0
    assert websocket.messages[-1]["content"] == (
        "科学计算未成功完成，请检查输入或工具状态后重试。"
    )
    agent_message = next(
        item for item in websocket.messages if item["type"] == "agent_result"
    )
    assert agent_message["warnings"] == ["safe scientific warning"]
    serialized = json.dumps(
        {"messages": websocket.messages, "history": history},
        ensure_ascii=False,
    )
    for forbidden in (
        "chat-handler-test-key-123",
        "chat-handler-test-token-456",
        _fake_openai_key("testfailure123456789"),
        "warning-test-key-123",
        "warning-test-token-456",
        _fake_openai_key("warningtest123456789"),
        "C:/Users/",
        "D:\\MedChat\\private",
    ):
        assert forbidden not in serialized


def test_agent_failure_content_and_warnings_are_length_bounded():
    handler = ChatHandler(
        model=FakeModel(),
        rag_service=FakeRagService(),
        agent_system=FailedScientificAgentSystem(
            final_answer="x" * 5000,
            warnings=["w" * 1000],
        ),
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()
    history = []

    asyncio.run(
        handler._process_message(
            websocket=websocket,
            message="全面评估 CCO",
            enable_rag=False,
            enable_tools=True,
            conversation_history=history,
        )
    )

    completion = websocket.messages[-1]
    agent_message = next(
        item for item in websocket.messages if item["type"] == "agent_result"
    )
    assert len(completion["content"]) <= 1024
    assert len(agent_message["warnings"]) == 1
    assert len(agent_message["warnings"][0]) <= 256
    assert history[-1]["assistant"] == completion["content"]


def test_live_failure_agent_events_are_recursively_sanitized():
    handler = ChatHandler(
        model=FakeModel(),
        rag_service=FakeRagService(),
        agent_system=SensitiveLiveEventAgentSystem(),
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

    events = [
        item["event"]
        for item in websocket.messages
        if item["type"] == "agent_event"
    ]
    assert [event["event"] for event in events] == [
        "tool_failed",
        "task_failed",
        "tool_completed",
    ]
    assert events[0]["message"] == (
        "科学计算未成功完成，请检查输入或工具状态后重试。"
    )
    assert events[0]["tool"] == "property_calculator"
    assert events[0]["trace_id"] == "live-sensitive-trace"
    assert events[0]["progress"] == 0.5
    assert events[0]["payload"]["normal_key"] == "keep-live-normal-value"
    assert "normal_key\x00" not in events[0]["payload"]
    assert events[1]["message"] == (
        "科学计算未成功完成，请检查输入或工具状态后重试。"
    )
    assert events[2] == {
        "type": "agent_event",
        "event": "tool_completed",
        "message": "property calculation completed normally",
        "trace_id": "live-sensitive-trace",
        "tool": "property_calculator",
        "progress": 1.0,
        "payload": {"status": "succeeded", "count": 1},
    }
    serialized = json.dumps(websocket.messages, ensure_ascii=False)
    for forbidden in (
        "live-event-key-123",
        "live-warning-token-456",
        "sk-liveevent123456789",
        "live-task-token-789",
        "sk-livekey123456789",
        "live-nested-key-123",
        "live-nested-token-456",
        "must-not-overwrite-live-normal-value",
        "C:\\Users\\",
        "D:\\MedChat\\private",
    ):
        assert forbidden not in serialized


def test_buffered_failure_agent_events_are_recursively_sanitized():
    handler = ChatHandler(
        model=FakeModel(),
        rag_service=FakeRagService(),
        agent_system=SensitiveBufferedEventAgentSystem(),
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

    events = [
        item["event"]
        for item in websocket.messages
        if item["type"] == "agent_event"
    ]
    assert [event["type"] for event in events] == [
        "tool_failed",
        "task_failed",
        "tool_completed",
    ]
    assert events[0]["message"] == (
        "科学计算未成功完成，请检查输入或工具状态后重试。"
    )
    assert events[0]["tool"] == "property_calculator"
    assert events[0]["trace_id"] == "buffered-sensitive-trace"
    assert events[0]["progress"] == 0.4
    assert events[0]["payload"]["normal_key"] == (
        "keep-buffered-normal-value"
    )
    assert "normal_key\x00" not in events[0]["payload"]
    assert events[1]["message"] == (
        "科学计算未成功完成，请检查输入或工具状态后重试。"
    )
    assert events[2] == {
        "type": "tool_completed",
        "message": "buffered property calculation completed",
        "trace_id": "buffered-sensitive-trace",
        "tool": "property_calculator",
        "progress": 1.0,
        "payload": {"status": "succeeded", "count": 2},
    }
    serialized = json.dumps(websocket.messages, ensure_ascii=False)
    for forbidden in (
        "buffered-key-123",
        "buffered-warning-token-456",
        _fake_openai_key("bufferedevent123456789"),
        "buffered-task-token-789",
        "buffered-error-key-987",
        _fake_openai_key("bufferedkey123456789"),
        "buffered-nested-key-123",
        "buffered-nested-token-456",
        "must-not-overwrite-buffered-normal-value",
        "C:\\Users\\",
        "D:\\MedChat\\private",
    ):
        assert forbidden not in serialized


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


@pytest.mark.parametrize(
    "query",
    [
        "Generate 11 molecules",
        "Generate 7.5 molecules",
        "Generate 7.5 potent",
        "Generate 3 potent + synthesize 4 potent",
        "Generate 3 potent plus synthesize 4 potent",
        "Generate 3 potent and also synthesize 4 potent",
    ],
)
def test_chat_handler_omitted_count_rejects_invalid_text_before_tool(query):
    tool = RecordingTool("llm_molecular_generator")
    supervisor = SupervisorAgent(
        tools={"llm_molecular_generator": tool},
        skill_router=FixedRouter(MolecularDesignSkill()),
    )
    handler = ChatHandler(
        model=FakeModel(),
        rag_service=FakeRagService(),
        agent_system=supervisor,
        config={"inference": {"stream": False}},
    )

    result = asyncio.run(
        handler._execute_agent(query, active_skill=MolecularDesignSkill())
    )

    assert result["success"] is False
    assert result["error"]["code"] == "invalid_input"
    assert tool.calls == []


def test_chat_handler_rejects_malformed_generation_before_should_use_or_router():
    class AgentThatMustNotRoute:
        llm = None
        skill_router = None

        def __init__(self):
            self.should_use_calls = []
            self.execute_calls = []

        def should_use_tools(self, message):
            self.should_use_calls.append(message)
            raise AssertionError("should_use_tools ran before generation preflight")

        def execute(self, *args, **kwargs):
            self.execute_calls.append((args, kwargs))
            raise AssertionError("agent ran before generation preflight")

    agent = AgentThatMustNotRoute()
    model = FakeModel()
    handler = ChatHandler(
        model=model,
        rag_service=FakeRagService(),
        agent_system=agent,
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()

    asyncio.run(
        handler._process_message(
            websocket=websocket,
            message="Generate - 1 molecules",
            enable_rag=False,
            enable_tools=True,
        )
    )

    assert agent.should_use_calls == []
    assert agent.execute_calls == []
    assert model.generate_calls == 0
    assert websocket.messages[-1]["type"] == "complete"
    assert "Invalid molecular generation request" in websocket.messages[-1]["content"]


def test_chat_handler_rejects_oversized_numeric_token_before_router_or_model():
    class AgentThatMustNotRun:
        llm = None
        skill_router = None

        def should_use_tools(self, _message):
            raise AssertionError("router ran")

        def execute(self, *_args, **_kwargs):
            raise AssertionError("agent ran")

    model = FakeModel()
    handler = ChatHandler(
        model=model,
        rag_service=FakeRagService(),
        agent_system=AgentThatMustNotRun(),
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()

    asyncio.run(
        handler._process_message(
            websocket=websocket,
            message=f"Generate {'9' * 5000} molecules",
            enable_rag=False,
            enable_tools=True,
        )
    )

    assert model.generate_calls == 0
    assert websocket.messages[-1]["type"] == "complete"
    assert "Invalid molecular generation request" in websocket.messages[-1]["content"]


def test_chat_handler_authoritative_count_bypasses_malformed_generation_prose():
    class CapturingAgent:
        llm = None
        skill_router = None

        def __init__(self):
            self.calls = []

        def should_use_tools(self, message):
            return True

        def execute(self, message, **kwargs):
            self.calls.append((message, kwargs))
            return {
                "success": True,
                "final_answer": "generated",
                "tools_used": ["llm_molecular_generator"],
                "tool_results": {},
            }

    agent = CapturingAgent()
    handler = ChatHandler(
        model=FakeModel(),
        rag_service=FakeRagService(),
        agent_system=agent,
        config={"inference": {"stream": False}},
    )

    asyncio.run(
        handler._process_message(
            websocket=FakeWebSocket(),
            message="Generate 7.5 molecules",
            enable_rag=False,
            enable_tools=True,
            mol_count=5,
        )
    )

    assert agent.calls[0][1]["mol_count"] == 5


def test_chat_handler_omitted_count_preserves_valid_text_count():
    tool = RecordingTool("llm_molecular_generator")
    supervisor = SupervisorAgent(
        tools={"llm_molecular_generator": tool},
        skill_router=FixedRouter(MolecularDesignSkill()),
    )
    handler = ChatHandler(
        model=FakeModel(),
        rag_service=FakeRagService(),
        agent_system=supervisor,
        config={"inference": {"stream": False}},
    )

    result = asyncio.run(
        handler._execute_agent(
            "Generate 7 molecules", active_skill=MolecularDesignSkill()
        )
    )

    assert tool.calls
    assert tool.calls[0]["metadata"]["requested_count"] == 7


def test_chat_handler_explicit_count_remains_authoritative():
    tool = RecordingTool("llm_molecular_generator")
    supervisor = SupervisorAgent(
        tools={"llm_molecular_generator": tool},
        skill_router=FixedRouter(MolecularDesignSkill()),
    )
    handler = ChatHandler(
        model=FakeModel(),
        rag_service=FakeRagService(),
        agent_system=supervisor,
        config={"inference": {"stream": False}},
    )

    result = asyncio.run(
        handler._execute_agent(
            "Generate 7 molecules",
            mol_count=3,
            active_skill=MolecularDesignSkill(),
        )
    )

    assert tool.calls
    assert tool.calls[0]["metadata"]["requested_count"] == 3


def test_property_request_without_smiles_returns_clarification_without_rag_or_model():
    model = FakeModel()
    rag = RecordingLegacyRagService()
    agent = ConfirmationAgentSystem()
    handler = ChatHandler(
        model=model,
        rag_service=rag,
        agent_system=agent,
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()

    asyncio.run(
        handler._process_message(
            websocket=websocket,
            message="计算阿司匹林的分子性质",
            enable_rag=True,
            enable_tools=True,
        )
    )

    assert agent.execute_calls == []
    assert rag.calls == []
    assert model.generate_calls == 0
    assert websocket.messages[-1]["type"] == "complete"
    assert "SMILES" in websocket.messages[-1]["content"]


def test_conceptual_question_skips_molecular_rag_and_uses_main_model():
    model = FakeModel()
    rag = RecordingLegacyRagService()
    agent = ConfirmationAgentSystem()
    handler = ChatHandler(
        model=model,
        rag_service=rag,
        agent_system=agent,
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()

    asyncio.run(
        handler._process_message(
            websocket=websocket,
            message="什么是药物设计",
            enable_rag=True,
            enable_tools=True,
        )
    )

    assert agent.execute_calls == []
    assert rag.calls == []
    assert model.generate_calls == 1
    assert websocket.messages[-1] == {
        "type": "message",
        "message": "assistant response",
    }


def test_greeting_uses_main_model_without_agent_or_rag():
    model = FakeModel()
    rag = RecordingLegacyRagService()
    handler = ChatHandler(
        model=model,
        rag_service=rag,
        agent_system=None,
        config={"inference": {"stream": False}},
    )
    websocket = ScriptedWebSocket(
        [{"type": "chat", "message": "你好", "enable_rag": True}]
    )

    asyncio.run(handler.handle_websocket(websocket))

    assert model.generate_calls == 1
    assert rag.calls == []
    assert not any(item["type"] == "agent_event" for item in websocket.messages)
    assert websocket.messages[-1] == {
        "type": "message",
        "message": "assistant response",
    }


def test_external_model_uses_external_output_budget():
    model = ExternalFakeModel()
    handler = ChatHandler(
        model=model,
        rag_service=FakeRagService(),
        agent_system=None,
        config={
            "inference": {
                "stream": False,
                "max_tokens": 600,
                "external_max_tokens": 4096,
            }
        },
    )

    asyncio.run(
        handler._process_message(
            websocket=FakeWebSocket(),
            message="什么是药物分子设计",
            enable_rag=False,
            enable_tools=False,
        )
    )

    assert model.max_tokens == [4096]


def test_local_model_keeps_local_output_budget():
    model = FakeModel()
    handler = ChatHandler(
        model=model,
        rag_service=FakeRagService(),
        agent_system=None,
        config={
            "inference": {
                "stream": False,
                "max_tokens": 600,
                "external_max_tokens": 4096,
            }
        },
    )

    asyncio.run(
        handler._process_message(
            websocket=FakeWebSocket(),
            message="你好",
            enable_rag=False,
            enable_tools=False,
        )
    )

    assert model.max_tokens == [600]


def test_stream_length_finish_reason_is_visible_and_structured():
    model = TruncatedStreamingModel()
    handler = ChatHandler(
        model=model,
        rag_service=FakeRagService(),
        agent_system=None,
        config={
            "inference": {
                "stream": True,
                "max_tokens": 600,
                "external_max_tokens": 4096,
            }
        },
    )
    websocket = FakeWebSocket()

    asyncio.run(
        handler._process_message(
            websocket=websocket,
            message="什么是药物分子设计",
            enable_rag=False,
            enable_tools=False,
        )
    )

    completion = websocket.messages[-1]
    assert model.max_tokens == [4096]
    assert completion["type"] == "complete"
    assert completion["finish_reason"] == "length"
    assert completion["truncated"] is True
    assert "达到输出长度上限" in completion["content"]
    assert websocket.messages[-2]["type"] == "stream"
    assert "达到输出长度上限" in websocket.messages[-2]["content"]


def test_bare_smiles_preserves_legacy_molecular_rag_path():
    model = FakeModel()
    rag = RecordingLegacyRagService()
    agent = ConfirmationAgentSystem()
    handler = ChatHandler(
        model=model,
        rag_service=rag,
        agent_system=agent,
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()

    asyncio.run(
        handler._process_message(
            websocket=websocket,
            message="CCO",
            enable_rag=True,
            enable_tools=True,
        )
    )

    assert agent.execute_calls == []
    assert rag.calls == [("CCO", 5)]
    assert model.generate_calls == 1
    assert any(item["type"] == "rag_info" for item in websocket.messages)


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
