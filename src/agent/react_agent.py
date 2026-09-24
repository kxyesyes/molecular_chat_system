"""Public ReAct compatibility API backed by the canonical scientific runtime.

The class name remains import-compatible; there is no private action-text loop.
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional
from uuid import uuid4

from .capabilities.catalog import TOOL_ALIASES
from .contracts.generation_request import preflight_generation_request
from .router import SkillRouter
from .supervisor import SupervisorAgent

_MOL_COUNT_UNSET = object()


@dataclass
class ReActStep:
    """Compatibility record for previously produced reasoning traces."""
    thought: str
    action: Optional[str] = None
    action_input: Optional[str] = None
    observation: Optional[str] = None
    final_answer: Optional[str] = None


class ReActMolecularAgent:
    """Legacy signatures and presentation; Supervisor owns scientific execution."""

    def __init__(self, llm=None, molecular_generator_llm=None):
        from .tools import get_all_tools

        self.llm = llm
        self.molecular_generator_llm = molecular_generator_llm
        self.tools = {tool.name: tool for tool in get_all_tools(molecular_generator_llm)}
        self.skill_router = SkillRouter()
        # Kept for callers that inspect this attribute, not an execution budget.
        self.max_iterations = 5

    def _supervisor(self):
        tools = dict(self.tools)
        for alias, canonical in TOOL_ALIASES.items():
            if alias in tools:
                tools.setdefault(canonical, tools[alias])
        return SupervisorAgent(
            tools=tools, llm=self.llm,
            molecular_generator_llm=self.molecular_generator_llm,
            skill_router=self.skill_router,
        )

    def set_llm(self, llm) -> None:
        """Rebind main-model consumers without replacing the dedicated generator."""
        self.llm = llm
        generator = self.tools.get("llm_molecular_generator")
        for name, tool in self.tools.items():
            if TOOL_ALIASES.get(name, name) == "llm_molecular_generator" or tool is generator:
                continue
            if hasattr(tool, "llm"):
                tool.llm = llm

    def should_use_tools(self, query: str) -> bool:
        preflight_generation_request(query)
        decision = self.skill_router.decide(query, llm=self.llm)
        return bool(decision.selected_skill and not decision.requires_confirmation)

    def execute(
        self,
        query: str,
        temperature: float = 0.7,
        mol_count: Any = _MOL_COUNT_UNSET,
        active_skill=None,
        event_callback=None,
    ) -> Dict[str, Any]:
        count_kwargs = {} if mol_count is _MOL_COUNT_UNSET else {"mol_count": mol_count}
        raw = self._supervisor().execute(
            query, temperature=temperature, active_skill=active_skill,
            event_callback=event_callback, **count_kwargs,
        )
        result = raw.get("agent_result")
        if result is not None:
            # Do not inherit Supervisor's historical success-or-partial envelope.
            canonical = result.to_legacy_dict()
            output = {**raw, **canonical}
            output["final_answer"] = result.final_answer or raw.get("final_answer") or result.message
        else:
            # No-match/early non-execution envelopes have no scientific observations.
            output = {
                "status": "failed", "partial": False, "error": None,
                "trace_id": f"agent-{uuid4().hex[:12]}", "agent_result": None,
                "tool_results": {}, "tool_result_sequence": [], "tool_results_by_step": {},
                "warnings": [], "evidence": [], "artifacts": [], "metadata": {},
                **raw,
            }
        output.update({
            "query": query, "steps": [], "reasoning_trace": [],
            "tools_used": [
                event["tool"] for event in raw.get("agent_events", [])
                if event.get("event") == "tool_started"
            ],
        })
        return output

    def format_result(self, result: Dict[str, Any]) -> str:
        """Format new observations or previously stored ReActStep records."""
        if not result["success"]:
            return result["final_answer"]
        formatted_output = []
        if result["steps"] and len(result["steps"]) > 1:
            formatted_output.append("🤔 **推理过程:**")
            for i, step in enumerate(result["steps"], 1):
                if step.thought:
                    formatted_output.append(f"   {i}. {step.thought}")
                if step.action and step.observation:
                    formatted_output.append(f"      → 使用了 {step.action} 工具")
            formatted_output.append("")
        if result["tools_used"]:
            tools_text = "、".join(result["tools_used"])
            formatted_output.extend([f"🔧 **使用工具:** {tools_text}", ""])
        formatted_output.extend(["📋 **分析结果:**", result["final_answer"]])
        return "\n".join(formatted_output)
