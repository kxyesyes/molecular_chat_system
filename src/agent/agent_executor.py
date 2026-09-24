"""Legacy MolecularAgent API backed by the canonical scientific execution path."""
from threading import RLock
from typing import Any, Dict, List, Optional
import logging
from uuid import uuid4

from .capabilities.catalog import TOOL_ALIASES
from .contracts import AgentErrorCode, AgentExecutionError, AgentResult, ToolResult
from .contracts.scientific import RunOutcome
from .contracts.generation_request import (
    GenerationRequestError,
    build_generation_request,
    generation_request_error_details,
    has_generation_intent,
    preflight_generation_request,
)
from .supervisor import SupervisorAgent
from .tools import get_core_tools, get_optional_tool

logger = logging.getLogger(__name__)

# Constructor names are the existing optional-tool factory API, not routing rules.
_OPTIONAL_FACTORIES = {
    "molecular_docking": "MolecularDocking",
    "reverse_target_predictor": "ReverseTargetTool",
    "target_database_search": "TargetDatabaseTool",
    "activity_predictor": "ActivityPredictorTool",
    "rag_search": "RAGSearchTool",
}


class MolecularAgent:
    """Compatibility shape only; selection and execution belong to Supervisor."""

    def __init__(self, llm=None):
        self.llm = llm
        self.core_tools = []
        self.optional_tools = {}
        self._optional_lock = RLock()
        self._initialize_core_tools()

    def _initialize_core_tools(self):
        self.core_tools = get_core_tools()

    def _get_optional_tool(self, tool_name: str):
        with self._optional_lock:
            if tool_name not in self.optional_tools:
                try:
                    self.optional_tools[tool_name] = get_optional_tool(tool_name)
                except Exception:
                    # Missing tool is left absent for the canonical executor to reject.
                    logger.warning("Optional tool unavailable: %s", tool_name)
                    return None
            return self.optional_tools[tool_name]

    def get_all_tools(self) -> List:
        return [*self.core_tools, *self.optional_tools.copy().values()]

    def _supervisor(self):
        # Supplying a map, including an empty map, avoids eager default tool loading.
        tools = {tool.name: tool for tool in self.get_all_tools()}
        for alias, canonical in TOOL_ALIASES.items():
            if alias in tools:
                tools.setdefault(canonical, tools[alias])
        return SupervisorAgent(tools=tools, llm=self.llm)

    def should_use_tools(self, query: str, *, preflight: bool = True) -> bool:
        if preflight:
            preflight_generation_request(query)
        supervisor = self._supervisor()
        decision = supervisor.skill_router.decide(query, llm=self.llm)
        return bool(decision.selected_skill and not decision.requires_confirmation)

    def get_tool_descriptions(self) -> Dict[str, str]:
        descriptions = {
            tool.name: getattr(tool, "description", "No description available")
            for tool in self.core_tools
        }
        descriptions["RXNChemistryAgent"] = "Chemical reaction prediction and synthesis planning"
        descriptions["MolecularDocking"] = "Molecular docking and binding analysis"
        return descriptions

    def execute_tools(
        self, query: str, temperature: float = 0.7, mol_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        result = self.execute(query, temperature, mol_count)
        result["results"] = result.pop("tool_results")
        return result

    def execute(
        self, query: str, temperature: float = 0.7, mol_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        try:
            requested_count = preflight_generation_request(
                query, mol_count, count_supplied=mol_count is not None,
                field="mol_count", active_molecular_skill=True,
            )
            if has_generation_intent(query):
                build_generation_request(query, requested_count)
        except GenerationRequestError as exc:
            return self._invalid_generation_response(exc)

        supervisor = self._supervisor()
        decision = supervisor.skill_router.decide(query, llm=self.llm)
        if decision.requires_confirmation:
            message = "Input clarification required: " + "; ".join(decision.reasons)
            result = AgentResult(
                trace_id=f"agent-{uuid4().hex[:12]}", success=False,
                message=(message if decision.selected_skill else "No relevant tools found for this query"),
                final_answer=message, skill_name=decision.selected_skill,
                outcome=RunOutcome.REJECTED,
                error=AgentExecutionError(
                    code=AgentErrorCode.INVALID_INPUT, message=message,
                    details={"reason": "routing_confirmation_required", "reasons": decision.reasons},
                ),
            )
            return self._legacy_response(result, [], {"steps": []})
        policy = supervisor.catalog.get(decision.selected_skill) if decision.selected_skill else None
        if policy is None:
            return {
                "success": False, "partial": False, "status": "failed",
                "message": "No relevant tools found for this query",
                "response": "抱歉，我无法处理这个查询。请提供分子结构(SMILES)或相关的药物化学问题。",
                "tool_results": [], "used_tools": [], "error": None,
                "trace_id": f"agent-{uuid4().hex[:12]}", "agent_events": [],
                "warnings": [], "evidence": [], "artifacts": [],
            }

        count_kwargs = {} if mol_count is None else {"mol_count": mol_count}
        plan = supervisor.plan(query, skill_name=policy.name, **count_kwargs)
        # Only load tools named by the canonical plan, never all optional tools.
        request_tools = dict(supervisor.tools)
        for step in plan["steps"]:
            name = step["tool_name"]
            factory = _OPTIONAL_FACTORIES.get(name)
            if name not in request_tools and factory is not None:
                tool = self._get_optional_tool(factory)
                if tool is not None:
                    request_tools[name] = tool
        supervisor.tools = request_tools
        raw = supervisor.execute(
            query, temperature=temperature, active_skill=policy, **count_kwargs,
        )
        return self._legacy_response(raw["agent_result"], raw["agent_events"], raw.get("workflow_plan", plan))

    @staticmethod
    def _legacy_response(result, events, plan):
        canonical = result.to_legacy_dict()
        sequence = canonical["tool_result_sequence"]
        return {
            **canonical,
            "response": canonical["final_answer"] or canonical["message"],
            "used_tools": [event["tool"] for event in events if event.get("event") == "tool_started"],
            "tool_results": sequence,
            "trace_id": result.trace_id,
            "agent_events": events,
            "workflow_plan": plan,
        }

    @staticmethod
    def _invalid_generation_tool_result(exc: GenerationRequestError) -> Dict[str, Any]:
        message = f"Invalid molecular generation request: {exc}"
        return ToolResult.error_result(
            tool_name="llm_molecular_generator",
            code=AgentErrorCode.INVALID_INPUT,
            message=message,
            details=generation_request_error_details(exc),
        ).to_legacy_dict()

    @classmethod
    def _invalid_generation_response(cls, exc: GenerationRequestError) -> Dict[str, Any]:
        tool_result = cls._invalid_generation_tool_result(exc)
        return {
            "success": False, "partial": False, "status": "failed",
            "message": tool_result["error"]["message"],
            "response": "工具执行失败，请检查输入格式或稍后重试。",
            "used_tools": [], "tool_results": [tool_result],
            "error": tool_result["error"],
            "trace_id": f"agent-{uuid4().hex[:12]}", "agent_events": [],
            "warnings": [], "evidence": [], "artifacts": [],
        }
