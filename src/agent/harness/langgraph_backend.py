from __future__ import annotations

import operator
import time
from importlib import metadata as importlib_metadata
from typing import Annotated, Any, TypedDict

from src.agent.planning import CompiledPlan, WorkflowPlan

from .base import ShadowComparison
from .shadow import compare_control_state


class ShadowState(TypedDict):
    visited: Annotated[list[str], operator.add]
    decisions: Annotated[list[dict[str, Any]], operator.add]


class LangGraphPlanSimulator:
    """Tool-free LangGraph simulation of an already compiled workflow plan."""

    def __init__(self):
        try:
            self.version = importlib_metadata.version("langgraph")
        except importlib_metadata.PackageNotFoundError:
            self.version = "unknown"

    def simulate(
        self,
        plan: WorkflowPlan,
        compiled: CompiledPlan,
        summary: dict[str, Any],
    ) -> ShadowComparison:
        started = time.perf_counter()
        decision_by_step = {
            step.name: {
                "step_id": step.name,
                "dependencies": list(compiled.dependencies.get(step.name, ())),
                "required": bool(step.required),
                "preconditions": list(step.preconditions),
            }
            for step in plan.steps
        }
        if not plan.steps:
            state: ShadowState = {"visited": [], "decisions": []}
        else:
            from langgraph.graph import END, START, StateGraph

            builder = StateGraph(ShadowState)
            for step in plan.steps:
                builder.add_node(
                    step.name,
                    lambda _state, step_id=step.name: {
                        "visited": [step_id],
                        "decisions": [decision_by_step[step_id]],
                    },
                )
            builder.add_edge(START, plan.steps[0].name)
            for left, right in zip(plan.steps, plan.steps[1:]):
                builder.add_edge(left.name, right.name)
            builder.add_edge(plan.steps[-1].name, END)
            graph = builder.compile()
            state = graph.invoke(
                {"visited": [], "decisions": []},
                {"recursion_limit": max(25, len(plan.steps) * 3)},
            )

        decisions = {
            item["step_id"]: item for item in state.get("decisions", [])
        }
        shadow_state = {
            "workflow": plan.workflow_name,
            "node_order": list(state.get("visited", [])),
            "dependencies": {
                step_id: list(item.get("dependencies", []))
                for step_id, item in decisions.items()
            },
            "required": {
                step_id: bool(item.get("required"))
                for step_id, item in decisions.items()
            },
            "risk_gates": {
                step_id: list(item.get("preconditions", []))
                for step_id, item in decisions.items()
            },
            "candidate_mapping": summary.get("candidate_mapping"),
            "terminal_outcome": summary.get("terminal_outcome"),
        }
        return compare_control_state(
            plan,
            compiled.dependencies,
            summary,
            shadow_state,
            backend_version=self.version,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )


__all__ = ["LangGraphPlanSimulator", "ShadowState"]
