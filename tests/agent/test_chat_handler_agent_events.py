import asyncio
from copy import deepcopy
import json
from collections.abc import Mapping
from unittest.mock import Mock

import pytest
from fastapi import WebSocketDisconnect

from src.agent.contracts import (
    AgentContext,
    AgentResult,
    AgentErrorCode,
    AgentExecutionError,
    CandidateRecord,
    CandidateSet,
    ObservationStatus,
    ToolProvenance,
    ToolResult,
    RunOutcome,
    build_candidate_id,
)
from src.agent.orchestrators import WorkflowStep
from src.agent.planning import WorkflowPlan
from src.agent.router import SkillRouter
from src.agent.runtime.allocation import begin_tool_dispatch, current_allocation
from src.agent.supervisor import SupervisorAgent
from src.web.chat_handler import ChatHandler
from src.web.decision_chat import _deliver_result, _result_frame, _DeliveryError, _event_frame
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.runtime.task_state import TaskEvent, TaskEventType
from src.agent.tools.property_calculator import PropertyCalculator
from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
from src.agent.contracts.generation_request import GenerationRequestError
from src.agent.scientific_session.web_requirements import compile_web_requirements
from tests.agent.pi_web_support import PlannedWebModel
from tests.agent.test_target_driven_design_workflow import FakeTool
from tests.agent.pi_web_support import cleanup_web_handlers, make_web_handler, run_web_turn
from tests.agent.test_web_decision_runtime import (
    actual_app, ActualSocket, cookie_for, chat_decision, result_of,
)


KNOWLEDGE_BASE_PROMPT = "search the local knowledge base for molecules similar to CCO"


def _fake_openai_key(suffix: str) -> str:
    return "sk" + "-" + suffix


class FakeWebSocket:
    def __init__(self):
        self.messages = []
        self.scope = {}

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
        "status": "completed" if agent_success else "failed",
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


def _typed_presentation_fixture(raw, *, web=False):
    """Transcribe explicit fixture fields into the current typed input contract.

    No execution, legacy status normalization, exception recovery, history writes,
    accepted-audit construction or scientific validation happens in this helper.
    Malformed raw mappings belong in the individual raw-projection unit tests.
    """
    def error(value):
        if value is None:
            return None
        return AgentExecutionError(AgentErrorCode(value.get("code", "internal_error")),
            value.get("message", ""), deepcopy(value.get("details")))

    observations = []
    for index, row in enumerate(raw.get("tool_result_sequence", []), 1):
        quality = deepcopy(row.get("quality", {}))
        if "step_id" in row:
            quality["step_id"] = row["step_id"]
        observations.append(ToolResult(
            tool_name=row["tool_name"], success=row["success"],
            message=row.get("message", ""), data=deepcopy(row.get("data")),
            formatted=row.get("formatted", ""), error=error(row.get("error")),
            quality=quality, warnings=deepcopy(row.get("warnings", [])),
            status=ObservationStatus(row.get("status", "succeeded")),
            provenance=ToolProvenance.from_dict(row["provenance"])
                if row.get("provenance") is not None else None))
    metadata = deepcopy(raw.get("metadata", {}))
    if web:
        metadata["binding_profile"] = "medchat-web-v1"
    return AgentResult(trace_id=raw.get("trace_id", "presentation-fixture"),
        success=raw["success"], message=raw.get("message", ""),
        final_answer=raw.get("final_answer", ""), skill_name=raw.get("active_skill"),
        outcome=RunOutcome(raw.get("status", "completed")),
        partial=raw.get("partial", raw.get("status") == "partial"),
        tool_results=observations, error=error(raw.get("error")),
        warnings=deepcopy(raw.get("warnings", [])), metadata=metadata)


def _run_fixed_agent_result(result, *, model=None, handler=None):
    """Pure delivery of settled fixture data; never a replacement chat runner."""
    model = model or FakeModel()
    handler = handler or ChatHandler(model, None, None, {})
    websocket = FakeWebSocket()
    typed = result if type(result) is AgentResult else _typed_presentation_fixture(result)
    execution = {**typed.to_legacy_dict(), "trace_id": typed.trace_id}
    asyncio.run(_deliver_result(handler, websocket,
        _result_frame(handler, typed, False), execution))
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

    async def search_similar_molecules_with_status(self, query, count):
        return {
            "status": "unavailable",
            "records": [],
            "error": {"code": "rag_not_ready"},
        }


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


class AsyncRoutingModel:
    def __init__(self):
        self.calls = 0

    async def generate(self, prompt, **kwargs):
        self.calls += 1
        return json.dumps(
            {
                "selected_skill": "activity_prediction",
                "confidence": 0.99,
                "reasons": ["activity endpoint requested"],
            }
        )


class AsyncRoutingAgentSystem(FakeAgentSystem):
    def __init__(self):
        self.llm = AsyncRoutingModel()
        self.skill_router = SkillRouter()
        self.selected_skills = []

    def should_use_tools(self, message):
        raise AssertionError("the explainable route decision must be authoritative")

    def execute(self, message, temperature=0.7, mol_count=5, active_skill=None):
        self.selected_skills.append(active_skill.name if active_skill else None)
        return super().execute(
            message,
            temperature=temperature,
            mol_count=mol_count,
            active_skill=active_skill,
        )


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

    async def search_similar_molecules_with_status(self, query, count):
        records = await self.search_similar_molecules(query, count)
        return {
            "status": "succeeded" if records else "no_hits",
            "records": records,
        }


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
    def __init__(self, *, dispatch_tool=False):
        self.dispatch_tool = dispatch_tool
        self.phase_during_events = None

    def execute(
        self,
        message,
        temperature=0.7,
        mol_count=5,
        active_skill=None,
        event_callback=None,
    ):
        assert event_callback is not None
        scope = current_allocation()
        assert scope is not None and scope.phase == "allocating"
        if self.dispatch_tool:
            # A tool_started frame alone must not grant dispatch authority.
            assert begin_tool_dispatch()
        self.phase_during_events = scope.phase
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
    def __init__(self, *, dispatch_tool=False):
        self.dispatch_tool = dispatch_tool
        self.phase_during_events = None

    def execute(
        self,
        message,
        temperature=0.7,
        mol_count=5,
        active_skill=None,
        event_callback=None,
    ):
        assert event_callback is not None
        scope = current_allocation()
        assert scope is not None and scope.phase == "allocating"
        if self.dispatch_tool:
            assert begin_tool_dispatch()
        self.phase_during_events = scope.phase
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
                    "message": "pIC50=7.1; sk-liveevent123456789",
                    "debug_path": "D:\\MedChat\\private\\debug.json",
                },
            },
        })
        event_callback({
            "type": "agent_event",
            "event": "task_failed",
            "message": "Task failed pIC50=7.1 token=live-task-token-789",
            "trace_id": "live-sensitive-trace",
            "tool": None,
            "progress": 0.75,
            "payload": {
                "path": "C:\\Users\\reviewer\\private\\task.log",
                "error": {"message": "binding energy=-8.4 kcal/mol"},
            },
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
                        "Buffered tool failure pIC50=7.1 api_key=buffered-key-123 "
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
                            "message": (
                                "binding energy=-8.4 kcal/mol; "
                                + _fake_openai_key("bufferedevent123456789")
                            ),
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


def _sensitive_live_event_fixtures():
    """Untrusted payload fixtures, with no fake execution or allocation."""
    return [{
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
                    "message": "pIC50=7.1; sk-liveevent123456789",
                    "debug_path": "D:\\MedChat\\private\\debug.json",
                },
            },
        },
{
            "type": "agent_event",
            "event": "task_failed",
            "message": "Task failed pIC50=7.1 token=live-task-token-789",
            "trace_id": "live-sensitive-trace",
            "tool": None,
            "progress": 0.75,
            "payload": {
                "path": "C:\\Users\\reviewer\\private\\task.log",
                "error": {"message": "binding energy=-8.4 kcal/mol"},
            },
        },
{
            "type": "agent_event",
            "event": "tool_completed",
            "message": "property calculation completed normally",
            "trace_id": "live-sensitive-trace",
            "tool": "property_calculator",
            "progress": 1.0,
            "payload": {"status": "succeeded", "count": 1},
        }]


def _rag_web_fixture(tmp_path):
    from src.agent.tools.rag_search_tool import RAGSearchTool
    from tests.agent.test_rag_tool_contract import InjectedService, record
    row = record()
    row.update(SMILES="CCO", similarity_score=0.91)
    service = InjectedService([row])
    handler = make_web_handler([RAGSearchTool(service)],
                               SQLiteAgentStateStore(tmp_path / "rag.sqlite"))
    handler.rag_service = RecordingLegacyRagService()
    return handler, service


def test_chat_handler_forwards_agent_events_before_agent_result(tmp_path):
    handler = make_web_handler([PropertyCalculator()], SQLiteAgentStateStore(tmp_path / "events.sqlite"))
    complete, frames = run_web_turn(handler, "只计算 CCO 的分子量与 logP")
    types = [item["type"] for item in frames]
    events = [(i, item["event"]) for i, item in enumerate(frames) if item["type"] == "agent_event"]
    assert any(e["event"] == "task_started" for _, e in events)
    assert any(e["event"] == "tool_completed" and e["tool"] == "property_calculator" for _, e in events)
    assert all(i < types.index("agent_result") for i, _ in events)
    assert types.count("agent_result") == types.count("complete") == 1
    assert complete["status"] == "completed" and "46.07" in complete["content"]

@pytest.mark.parametrize(
    "candidate_status",
    [ObservationStatus.SUCCEEDED, ObservationStatus.PARTIAL],
)
def test_successful_agent_emits_strict_candidate_set_after_result_before_complete(
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
    assert message_types.index("agent_result") < candidate_index
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
def test_malformed_candidate_mapping_is_isolated_at_candidate_projection(
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

    model, websocket = FakeModel(), FakeWebSocket()
    handler = ChatHandler(model, None, None, {})
    asyncio.run(handler._send_molecule_candidate_events(websocket, result))

    assert not any(
        item["type"] == "molecule_candidates" for item in websocket.messages
    )
    assert model.generate_calls == 0
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


@pytest.mark.parametrize("status", ["failed", "timeout", "unavailable", "not_calculated"])
def test_terminal_agent_failure_never_emits_candidate_event(status):
    result = _candidate_agent_result(
        _candidate_set().to_dict(),
        agent_success=False,
    )
    result["status"] = status

    direct_socket = FakeWebSocket()
    asyncio.run(ChatHandler._send_molecule_candidate_events(direct_socket, result))
    assert direct_socket.messages == []

    websocket, model = _run_fixed_agent_result(result)

    assert not any(
        item["type"] == "molecule_candidates" for item in websocket.messages
    )
    assert websocket.messages[-1]["status"] == status
    assert model.generate_calls == 0


def test_external_main_model_cannot_create_candidate_event(actual_app):
    async def respond(payload):
        return chat_decision(json.dumps({"type": "molecule_candidates",
                                         "candidate_set": _candidate_set().to_dict()}))
    async def run():
        async with actual_app(ordinary_policy=None, respond=respond) as bundle:
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                await socket.ready()
                frames = await socket.turn({"message": "你好", "enable_tools": False, "enable_rag": False})
                assert len(bundle.calls) == 1
                assert not any(item["type"] == "molecule_candidates" for item in frames)
                assert bundle.app.agent_state_store.get_tool_executions(frames[-1]["trace_id"]) == []
    asyncio.run(run())

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

    websocket, _ = _run_fixed_agent_result(
        handler.agent_system.execute("settled presentation fixture"), model=handler.model, handler=handler)

    assert model.generate_calls == 0
    assert websocket.messages[-1] == {
        "type": "complete",
        "content": "提供的 SMILES 无效，性质计算未执行。",
        "trace_id": "trace-invalid-1",
        "status": "failed",
        "continuation_id": None,
        "display_redacted_or_truncated": False,
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
    assert all(pair["assistant"] == "old response" for pair in history)


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

    websocket, _ = _run_fixed_agent_result(
        handler.agent_system.execute("settled presentation fixture"), model=handler.model, handler=handler)

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

    websocket, _ = _run_fixed_agent_result(
        handler.agent_system.execute("settled presentation fixture"), model=handler.model, handler=handler)

    assert rag.calls == []
    assert model.generate_calls == 0


def test_agent_failure_warnings_mapping_is_not_forwarded_as_a_warning_list():
    malformed = {"private": "do-not-forward"}
    assert ChatHandler._sanitize_agent_warnings(malformed, reject_claims=True) == []
    typed = _typed_presentation_fixture(FailedScientificAgentSystem(warnings=malformed).execute("fixture"))
    with pytest.raises(_DeliveryError, match="result_display_invalid"):
        _result_frame(ChatHandler(FakeModel(), None, None, {}), typed, False)

def test_agent_execution_exception_uses_safe_terminal_fallback_without_secret(tmp_path):
    class FailingModel(PlannedWebModel):
        async def decide(self, *args, **kwargs):
            raise RuntimeError("private-agent-token internal exception repr")
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / "exception.sqlite"), FailingModel())
    terminal, frames = run_web_turn(handler, "你好", enable_tools=False)
    assert terminal["status"] == "failed"
    assert "private-agent-token" not in json.dumps(frames, ensure_ascii=False)
    assert "internal exception repr" not in json.dumps(frames, ensure_ascii=False)
    assert handler.web_results[-1].success is False

def test_failed_scientific_agent_cannot_publish_model_generated_activity_number():
    model = FakeModel()
    handler = ChatHandler(
        model=model,
        rag_service=FakeRagService(),
        agent_system=FailedScientificAgentSystem(
            final_answer="pIC50=7.1；binding energy=-8.4 kcal/mol",
        ),
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()

    websocket, _ = _run_fixed_agent_result(
        handler.agent_system.execute("settled presentation fixture"), model=handler.model, handler=handler)

    assert model.generate_calls == 0
    assert websocket.messages[-1]["type"] == "complete"
    assert websocket.messages[-1]["content"] == "SMILES 验证失败。"
    serialized = json.dumps(websocket.messages, ensure_ascii=False)
    assert "pIC50=7.1" not in serialized
    assert "binding energy=-8.4 kcal/mol" not in serialized


def test_projection_failure_preserves_settled_agent_record(monkeypatch):
    result = {
        "success": True,
        "status": "completed",
        "final_answer": "validated property observation",
        "tools_used": ["property_calculator"],
        "active_skill": "admet_assessment",
        "trace_id": "trace-preserve-after-projection-error",
        "tool_results": {},
        "tool_result_sequence": [{
            "success": True,
            "status": "succeeded",
            "tool_name": "property_calculator",
            "data": {"molecular_weight": 46.069},
        }],
        "agent_events": [],
    }
    handler = ChatHandler(
        model=FakeModel(),
        rag_service=FakeRagService(),
        agent_system=FixedResultAgentSystem(result),
        config={"inference": {"stream": False}},
    )

    async def fail_projection(*args, **kwargs):
        raise RuntimeError("synthetic projection failure")

    monkeypatch.setattr(handler, "_send_reference_candidate_events", fail_projection)
    websocket = FakeWebSocket()
    before = deepcopy(result)
    typed = _typed_presentation_fixture(result)
    with pytest.raises(RuntimeError, match="synthetic projection failure"):
        asyncio.run(_deliver_result(handler, websocket, _result_frame(handler, typed, False), result))
    assert result == before
    assert typed.tool_results[0].success is True

    envelope = next(item for item in websocket.messages if item["type"] == "agent_result")
    assert envelope["status"] == "completed"
    assert envelope["trace_id"] == "trace-preserve-after-projection-error"
    assert not envelope["error"]
    assert envelope["tool_result_sequence"][0]["success"] is True
    assert not any(item["type"] == "complete" for item in websocket.messages)


@pytest.mark.parametrize(
    "malformed_result",
    [None, [], "not-an-agent-result", InvalidAgentMapping()],
    ids=["none", "list", "string", "invalid-mapping"],
)
def test_malformed_agent_result_is_rejected_before_projection_or_fallback(malformed_result):
    model, rag = FakeModel(), RecordingLegacyRagService()
    handler = ChatHandler(model, rag, None, {})
    with pytest.raises(_DeliveryError, match="result_display_invalid") as failure:
        _result_frame(handler, malformed_result, False)
    assert model.generate_calls == 0 and rag.calls == []
    assert "private-invalid-mapping-token" not in str(failure.value)
    assert "not-an-agent-result" not in str(failure.value)

def test_post_agent_processing_exception_is_rejected_without_fallback():
    model, rag = FakeModel(), RecordingLegacyRagService()
    handler = ChatHandler(model, rag, None, {})
    typed = AgentResult("trace-post-processing", True, "must not publish",
                        outcome=RunOutcome.COMPLETED, metadata={"unsafe": object()})
    with pytest.raises(ValueError):
        _result_frame(handler, typed, False)
    assert model.generate_calls == 0 and rag.calls == []

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
            "binding energy=-8.4 kcal/mol",
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
            {"success": False, "status": "failed", "tool_name": "property_calculator", "message": sensitive_message}
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

    websocket, _ = _run_fixed_agent_result(
        handler.agent_system.execute("settled presentation fixture"), model=handler.model, handler=handler)

    assert model.generate_calls == 0
    assert websocket.messages[-1]["content"] == (
        "科学计算未成功完成，请检查输入或工具状态后重试。"
    )
    agent_message = next(
        item for item in websocket.messages if item["type"] == "agent_result"
    )
    assert agent_message["warnings"] == (["safe scientific warning"] + (
        ["部分显示内容已脱敏或省略，请查阅授权证据记录。"] if message_source != "final_answer" else []))
    assert agent_message["display_redacted_or_truncated"] is (message_source != "final_answer")
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
        "binding energy=-8.4 kcal/mol",
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

    websocket, _ = _run_fixed_agent_result(
        handler.agent_system.execute("settled presentation fixture"), model=handler.model, handler=handler)

    completion = websocket.messages[-1]
    agent_message = next(
        item for item in websocket.messages if item["type"] == "agent_result"
    )
    assert len(completion["content"]) <= 1024
    assert len(agent_message["warnings"]) == 1
    assert len(agent_message["warnings"][0]) <= 256
    assert history == []


@pytest.mark.parametrize("typed_event", [False, True], ids=["raw-projection", "typed-event-frame"])
def test_failure_agent_events_are_recursively_sanitized(typed_event):
    handler = ChatHandler(FakeModel(), None, None, {})
    raw = _sensitive_live_event_fixtures()
    if typed_event:  # Typed current event boundary.
        events = [json.loads(_event_frame(handler, TaskEvent(
            trace_id=e["trace_id"], event=TaskEventType(e["event"]), message=e["message"],
            tool=e["tool"], progress=e["progress"], payload=e["payload"]))[0])["event"] for e in raw]
    else:  # Retained raw projection boundary.
        events = [handler._sanitize_agent_event(e) for e in raw]
    assert [event["event"] for event in events] == ["tool_failed", "task_failed", "tool_completed"]
    task_failure = next(event for event in events if event["event"] == "task_failed")
    tool_completion = next(event for event in events if event["event"] == "tool_completed")
    assert events[0]["message"] == (
        "科学计算未成功完成，请检查输入或工具状态后重试。"
    )
    assert events[0]["tool"] == "property_calculator"
    assert events[0]["trace_id"] == "live-sensitive-trace"
    assert events[0]["progress"] == 0.5
    assert events[0]["payload"]["normal_key"] == "keep-live-normal-value"
    assert "normal_key\x00" not in events[0]["payload"]
    assert task_failure["message"] == (
        "科学计算未成功完成，请检查输入或工具状态后重试。"
    )
    assert {k: v for k, v in tool_completion.items() if k not in {"skill", "timestamp"}} == {
        "type": "agent_event",
        "event": "tool_completed",
        "message": "property calculation completed normally",
        "trace_id": "live-sensitive-trace",
        "tool": "property_calculator",
        "progress": 1.0,
        "payload": {"status": "succeeded", "count": 1},
    }
    serialized = json.dumps(events, ensure_ascii=False)
    for forbidden in (
        "live-event-key-123",
        "live-warning-token-456",
        "sk-liveevent123456789",
        "live-task-token-789",
        "sk-livekey123456789",
        "live-nested-key-123",
        "live-nested-token-456",
        "pIC50=7.1",
        "binding energy=-8.4 kcal/mol",
        "must-not-overwrite-live-normal-value",
        "C:\\Users\\",
        "D:\\MedChat\\private",
    ):
        assert forbidden not in serialized


def test_buffered_failure_agent_events_are_recursively_sanitized():
    handler = ChatHandler(FakeModel(), None, None, {})
    raw = SensitiveBufferedEventAgentSystem().execute("presentation fixture")["agent_events"]
    events = [handler._sanitize_agent_event(event) for event in raw]
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
    serialized = json.dumps(events, ensure_ascii=False)
    for forbidden in (
        "buffered-key-123",
        "buffered-warning-token-456",
        _fake_openai_key("bufferedevent123456789"),
        "buffered-task-token-789",
        "buffered-error-key-987",
        _fake_openai_key("bufferedkey123456789"),
        "buffered-nested-key-123",
        "buffered-nested-token-456",
        "pIC50=7.1",
        "binding energy=-8.4 kcal/mol",
        "must-not-overwrite-buffered-normal-value",
        "C:\\Users\\",
        "D:\\MedChat\\private",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize("dispatched", [False, True], ids=["before-dispatch", "after-dispatch"])
def test_chat_handler_forwards_events_while_pi_is_running(dispatched, tmp_path):
    import threading
    from src.web.decision_runtime import _Sender, _Turn
    from tests.agent.pi_web_support import WebSocket
    entered, release = threading.Event(), threading.Event()
    class Model(PlannedWebModel):
        async def decide(self, messages, **kwargs):
            if not dispatched and not self.messages:
                entered.set()
                assert await asyncio.to_thread(release.wait, 15)
            return await super().decide(messages, **kwargs)
    class Properties(PropertyCalculator):
        def execute(self, query):
            if dispatched:
                entered.set()
                assert release.wait(15)
            return super().execute(query)
    handler = make_web_handler([Properties(), DrugLikenessAssessment()], SQLiteAgentStateStore(tmp_path / "live.sqlite"), Model())
    runtime = handler.decision_runtime
    socket = WebSocket("live-owner")
    sender = _Sender(socket)
    sender._runtime = runtime
    turn = _Turn({"message": "计算 CCO 的理化性质和类药性，不要 ADMET 或活性", "enable_tools": True, "enable_rag": False})
    async def run():
        async def execute():
            turn.task = asyncio.current_task()
            runtime.active_owners.add(turn)
            await runtime._execute(handler, sender, turn)
        task = asyncio.create_task(execute())
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            await asyncio.sleep(0.02)
            assert not task.done()
            assert not any(m["type"] in {"agent_result", "complete"} for m in socket.messages)
            live = [m["event"]["event"] for m in socket.messages if m["type"] == "agent_event"]
            assert "task_started" in live
            assert ("tool_started" in live) is dispatched
        finally:
            release.set()
            await task
        types = [m["type"] for m in socket.messages]
        events = [(i, m["event"]) for i, m in enumerate(socket.messages) if m["type"] == "agent_event"]
        assert any(e["event"] == "tool_completed" for _, e in events)
        assert all(i < types.index("agent_result") for i, _ in events)
        assert types.count("agent_result") == types.count("complete") == 1
        assert socket.messages[-1]["status"] == "completed"
        assert turn.worker_owner.pending_roots == 0 and not runtime.active_owners
    asyncio.run(run())

def test_non_workflow_settled_result_is_not_reinterpreted_by_main_model():
    result = NonWorkflowAgentSystem().execute("fixture")
    socket, model = _run_fixed_agent_result(result)
    assert model.generate_calls == 0
    assert socket.messages[-1]["status"] == "completed"
    assert socket.messages[-1]["content"] == "raw react observation"

def test_chat_handler_uses_canonical_rag_search_result_for_rag_cards():
    handler = ChatHandler(FakeModel(), FakeRagService(), None, {})
    socket = FakeWebSocket()
    result = RagSearchAgentSystem().execute("fixture")
    molecules, warnings = asyncio.run(handler._send_agent_rag_results(socket, result, 3))
    assert not warnings and len(molecules) == 1
    assert len(socket.messages) == 1 and socket.messages[0]["type"] == "rag_info"
    assert socket.messages[0]["molecules"][0]["smiles"] == "CCO"
    assert socket.messages[0]["molecules"][0]["similarity"] == 0.91

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


def test_chat_handler_rag_disabled_blocks_agent_and_legacy_rag(tmp_path):
    handler, service = _rag_web_fixture(tmp_path)
    terminal, frames = run_web_turn(handler, KNOWLEDGE_BASE_PROMPT, enable_rag=False)
    assert terminal["status"] == "rejected"
    assert not service.calls and not handler.rag_service.calls
    assert not handler.model.messages
    assert not any(item["type"] == "rag_info" or (item["type"] == "agent_event"
        and item["event"].get("tool") in {"rag_search", "rag_database_search"}) for item in frames)

def test_chat_handler_rag_enabled_preserves_agent_rag_result_and_card(tmp_path):
    handler, service = _rag_web_fixture(tmp_path)
    terminal, frames = run_web_turn(handler, KNOWLEDGE_BASE_PROMPT, enable_rag=True)
    assert terminal["status"] == "completed"
    assert service.calls == [(KNOWLEDGE_BASE_PROMPT, 5)]
    assert not handler.rag_service.calls
    public = next(item for item in frames if item["type"] == "agent_result")
    assert [row["tool_name"] for row in public["tool_result_sequence"]] == ["rag_search"]
    assert public["tool_results"]["rag_search"]["success"] is True
    cards = [item for item in frames if item["type"] == "rag_info"]
    assert len(cards) == 1 and cards[0]["molecules"][0]["smiles"] == "CCO"
    assert cards[0]["molecules"][0]["source_index"] == 0
    assert cards[0]["molecules"][0]["provenance"]["source_sha256"] == "a" * 64
    types = [item["type"] for item in frames]
    assert types.index("agent_result") < types.index("rag_info") < types.index("complete")

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


def test_chat_handler_tools_disabled_rejects_scientific_execution_without_fallback(tmp_path):
    tool = FakeTool("llm_molecular_generator")
    handler = make_web_handler([tool], SQLiteAgentStateStore(tmp_path / "disabled.sqlite"))
    terminal, frames = run_web_turn(handler, "generate one molecule", enable_tools=False)
    assert terminal["status"] == "rejected"
    assert tool.inputs == [] and handler.model.messages == []
    assert not any(item["type"] == "molecule_candidates" for item in frames)

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
def test_contextual_generation_compile_rejects_invalid_count_before_execution(query):
    """Preserve the baseline's explicit server-selected MolecularDesignSkill.

    This is contextual compilation, not a claim that raw Web routing recognizes
    nounless phrases. Rejection occurs before any executable plan is returned.
    """
    context = AgentContext(query, "contextual-generation", active_skill="molecular_design")
    with pytest.raises(GenerationRequestError) as rejected:
        compile_web_requirements(context, allowed_tools={"llm_molecular_generator"})
    assert rejected.value.reason == (
        "requested_count_out_of_range" if query == "Generate 11 molecules"
        else "malformed_requested_count")

def test_chat_handler_rejects_malformed_generation_before_should_use_or_router(tmp_path):
    tool = FakeTool("llm_molecular_generator")
    handler = make_web_handler([tool], SQLiteAgentStateStore(tmp_path / "malformed.sqlite"))
    forbidden = Mock(side_effect=AssertionError("legacy routing/execution invoked"))
    handler.agent_system.should_use_tools = forbidden
    handler.agent_system.execute = forbidden
    terminal, _ = run_web_turn(handler, "Generate - 1 molecules")
    assert terminal["status"] == "rejected"
    assert not forbidden.called and not tool.inputs and not handler.model.messages

def test_chat_handler_rejects_oversized_numeric_token_before_router_or_model(tmp_path):
    tool = FakeTool("llm_molecular_generator")
    handler = make_web_handler([tool], SQLiteAgentStateStore(tmp_path / "oversized.sqlite"))
    terminal, _ = run_web_turn(handler, f"Generate {'9' * 5000} molecules")
    assert terminal["status"] == "rejected"
    assert not tool.inputs and not handler.model.messages

def test_chat_handler_authoritative_count_bypasses_malformed_generation_prose(tmp_path):
    tool = FakeTool("llm_molecular_generator")
    handler = make_web_handler([tool], SQLiteAgentStateStore(tmp_path / "generation.sqlite"))
    terminal, frames = run_web_turn(handler, "Generate 7.5 molecules", mol_count=5)
    assert tool.inputs, terminal
    assert tool.inputs[0]["metadata"]["requested_count"] == 5
    public = next(item for item in frames if item["type"] == "agent_result")
    assert public["tool_results"]["llm_molecular_generator"]["quality"]["actual_count"] == 5

def test_chat_handler_omitted_count_preserves_valid_text_count(tmp_path):
    tool = FakeTool("llm_molecular_generator")
    handler = make_web_handler([tool], SQLiteAgentStateStore(tmp_path / "generation.sqlite"))
    terminal, frames = run_web_turn(handler, "Generate 7 molecules")
    assert tool.inputs, terminal
    assert tool.inputs[0]["metadata"]["requested_count"] == 7
    public = next(item for item in frames if item["type"] == "agent_result")
    assert public["tool_results"]["llm_molecular_generator"]["quality"]["actual_count"] == 7

def test_chat_handler_explicit_count_remains_authoritative(tmp_path):
    tool = FakeTool("llm_molecular_generator")
    handler = make_web_handler([tool], SQLiteAgentStateStore(tmp_path / "generation.sqlite"))
    terminal, frames = run_web_turn(handler, "Generate 7 molecules", mol_count=3)
    assert tool.inputs, terminal
    assert tool.inputs[0]["metadata"]["requested_count"] == 3
    public = next(item for item in frames if item["type"] == "agent_result")
    assert public["tool_results"]["llm_molecular_generator"]["quality"]["actual_count"] == 3

def test_property_request_without_smiles_returns_clarification_without_rag_or_model(tmp_path):
    tools = [FakeTool(name) for name in ("property_calculator", "drug_likeness_assessment",
                                         "admet_predictor", "activity_predictor")]
    handler = make_web_handler(tools, SQLiteAgentStateStore(tmp_path / "clarify.sqlite"))
    rag = RecordingLegacyRagService()
    handler.rag_service = rag
    terminal, _ = run_web_turn(handler, "计算阿司匹林的分子性质", enable_rag=True)
    assert terminal["status"] == "rejected" and "SMILES" in terminal["content"]
    assert not handler.model.messages and not rag.calls
    assert not any(tool.inputs for tool in tools)

def test_conceptual_question_skips_molecular_rag_and_uses_main_model(tmp_path):
    handler, service = _rag_web_fixture(tmp_path)
    terminal, frames = run_web_turn(handler, "什么是药物设计", enable_rag=True)
    assert terminal["status"] == "completed"
    assert len(handler.model.messages) == 1
    assert not service.calls and not handler.rag_service.calls
    assert not any(item["type"] == "rag_info" for item in frames)

def test_greeting_uses_main_model_without_supervisor_or_rag(actual_app, monkeypatch):
    """Pi lifecycle events are allowed; scientific/Supervisor dispatch is not."""
    async def respond(payload):
        return chat_decision("assistant response")

    async def run():
        async with actual_app(respond=respond) as bundle:
            app = bundle.app
            forbidden = Mock(side_effect=AssertionError('greeting dispatched a legacy/scientific path'))
            monkeypatch.setattr(app.agent_system, 'execute', forbidden)
            assert not hasattr(app.chat_handler, '_process_message')
            for adapter in app.agent_tool_registry.as_mapping().values():
                monkeypatch.setattr(adapter, 'execute', forbidden)
            async with ActualSocket(app, await cookie_for(app)) as socket:
                assert (await socket.ready())['normal_chat_mode'] == 'pi'
                frames = await socket.turn({'type': 'chat', 'message': '你好', 'enable_rag': True})
                result = result_of(frames)
                assert result['success'] and result['status'] == 'completed'
                assert frames[-1]['type'] == 'complete'
                assert frames[-1]['status'] == 'completed'
                assert frames[-1]['content'] == 'assistant response'
                assert result['metadata']['rag_requested'] is True
                assert result['metadata']['retrieval_performed'] is False
                assert result['tool_result_sequence'] == []
                assert app.agent_state_store.get_tool_executions(result['trace_id']) == []
                assert not any(frame['type'] == 'rag_info' or (
                    frame['type'] == 'agent_event' and frame.get('event', {}).get('event') in {
                        'tool_started', 'tool_completed', 'tool_failed'}) for frame in frames)
            assert app.model is bundle.model
            assert len(bundle.calls) == 1 and bundle.calls[0]['model'] == 'protocol-only'
            assert not forbidden.called
            assert not bundle.rag.retrieve.called
            assert not bundle.rag.search_similar_molecules.called
            assert not bundle.rag.search_similar_molecules_with_status.called
    asyncio.run(run())


def test_external_model_uses_external_output_budget(tmp_path):
    class Model(PlannedWebModel):
        provider_name = "openai_compatible"
        def __init__(self):
            super().__init__()
            self.options = []
        async def decide(self, messages, **kwargs):
            self.options.append(kwargs)
            return await super().decide(messages, **kwargs)
    model = Model()
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / "budget.sqlite"), model)
    handler.config = {"inference": {"max_tokens": 600, "external_max_tokens": 4096}}
    complete, _ = run_web_turn(handler, "你好", enable_tools=False)
    assert complete["status"] == "completed"
    assert [options["max_tokens"] for options in model.options] == [4096]

def test_local_model_keeps_local_output_budget(tmp_path):
    class Model(PlannedWebModel):
        provider_name = None
        def __init__(self):
            super().__init__()
            self.options = []
        async def decide(self, messages, **kwargs):
            self.options.append(kwargs)
            return await super().decide(messages, **kwargs)
    model = Model()
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / "budget.sqlite"), model)
    handler.config = {"inference": {"max_tokens": 600, "external_max_tokens": 4096}}
    complete, _ = run_web_turn(handler, "你好", enable_tools=False)
    assert complete["status"] == "completed"
    assert [options["max_tokens"] for options in model.options] == [600]

def test_retained_stream_projection_marks_length_without_modifying_execution():
    model = TruncatedStreamingModel()
    model.last_response_metadata = {"finish_reason": "length"}
    handler = ChatHandler(model, None, None, {})
    content, warning, metadata = handler._finalize_model_response("药物分子设计是一个多目标优化过程。")
    assert metadata == {"finish_reason": "length", "truncated": True}
    assert "达到输出长度上限" in content and warning in content
    repeated, _, _ = handler._finalize_model_response(content)
    assert repeated == content

def test_bare_smiles_preserves_retrieval_through_pi_not_legacy_rag(tmp_path):
    handler, service = _rag_web_fixture(tmp_path)
    terminal, frames = run_web_turn(handler, "CCO", enable_rag=True)
    assert terminal["status"] == "completed"
    assert service.calls == [("CCO", 5)]
    assert not handler.rag_service.calls
    assert any(item["type"] == "rag_info" for item in frames)

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


def test_separate_websocket_connections_do_not_share_conversation_history(actual_app, monkeypatch):
    first_query, second_query = 'Explain logP', '解释分子生成的概念'
    private_answer = 'First connection private answer'

    async def respond(payload):
        return chat_decision(private_answer if payload['messages'][-1]['content'] == first_query
                             else 'Second connection answer')

    async def run():
        async with actual_app(respond=respond) as bundle:
            app = bundle.app
            forbidden = Mock(side_effect=AssertionError('history isolation dispatched a tool'))
            monkeypatch.setattr(app.agent_system, 'execute', forbidden)
            assert not hasattr(app.chat_handler, '_process_message')
            for adapter in app.agent_tool_registry.as_mapping().values():
                monkeypatch.setattr(adapter, 'execute', forbidden)
            first_cookie, second_cookie = await cookie_for(app), await cookie_for(app)
            assert first_cookie != second_cookie
            async with ActualSocket(app, first_cookie) as first, ActualSocket(app, second_cookie) as second:
                assert (await first.ready())['normal_chat_mode'] == 'pi'
                assert (await second.ready())['normal_chat_mode'] == 'pi'
                for socket, query in ((first, first_query), (second, second_query)):
                    result = result_of(await socket.turn({
                        'message': query, 'enable_rag': False, 'enable_tools': False}))
                    assert result['success'] and result['tool_result_sequence'] == []
                    assert app.agent_state_store.get_tool_executions(result['trace_id']) == []
            assert len(bundle.calls) == 2
            for call, query in zip(bundle.calls, (first_query, second_query)):
                assert [m for m in call['messages'] if m['role'] != 'system'] == [
                    {'role': 'user', 'content': query}]
            second_payload = json.dumps(bundle.calls[1], ensure_ascii=False)
            assert first_query not in second_payload and private_answer not in second_payload
            assert app.chat_handler.conversation_history == []
            assert not forbidden.called and not bundle.rag.retrieve.called
    asyncio.run(run())


def test_chat_handler_uses_compiled_routing_and_awaits_pi_decisions(tmp_path, monkeypatch):
    """The former routing-model call is retired; Pi owns async decisions."""
    import src.web.web_request as preparation
    calls = []
    original = preparation.compile_web_requirements
    def observe(*args, **kwargs):
        calls.append(args[0].query)
        return original(*args, **kwargs)
    monkeypatch.setattr(preparation, "compile_web_requirements", observe)
    handler = make_web_handler([PropertyCalculator(), DrugLikenessAssessment()], SQLiteAgentStateStore(tmp_path / "routing.sqlite"))
    query = "计算 CCO 的理化性质和类药性，不要 ADMET 或活性"
    terminal, _ = run_web_turn(handler, query)
    assert terminal["status"] == "completed"
    assert calls == [query]
    assert len(handler.model.messages) == 2
