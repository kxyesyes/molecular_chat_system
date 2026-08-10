from __future__ import annotations

from dataclasses import dataclass
import re

from src.agent.capabilities import capability_for_tool, get_capability
from src.agent.workflows import WorkflowPolicy

from .task_planner import WorkflowPlan


class PlanCompilationError(ValueError):
    pass


@dataclass(frozen=True)
class CompiledPlan:
    plan: WorkflowPlan
    dependencies: dict[str, tuple[str, ...]]


class PlanCompiler:
    OUTPUT_SELECTOR = re.compile(r"^\$\.outputs\.([A-Za-z0-9_-]+)$")

    def compile(self, plan: WorkflowPlan, policy: WorkflowPolicy) -> CompiledPlan:
        seen_steps: set[str] = set()
        producers: dict[str, str] = {}
        dependencies: dict[str, tuple[str, ...]] = {}
        allowed = set(policy.allowed_tools)

        for step in plan.steps:
            if step.name in seen_steps:
                raise PlanCompilationError(f"Duplicate step name: {step.name}")
            seen_steps.add(step.name)
            if step.tool_name not in allowed:
                raise PlanCompilationError(
                    f"Tool {step.tool_name} is not allowed by {policy.name}"
                )

            expected = capability_for_tool(step.tool_name)
            requested = get_capability(step.capability) if step.capability else expected
            if step.tool_name not in requested.tool_names:
                raise PlanCompilationError(
                    f"Tool {step.tool_name} does not implement {requested.name}"
                )
            selector = step.input_binding
            if selector is None and step.input_from:
                selector = f"$.outputs.{step.input_from}"
            step_dependencies: tuple[str, ...] = ()
            if selector:
                match = self.OUTPUT_SELECTOR.fullmatch(selector)
                if not match:
                    raise PlanCompilationError(
                        f"Unsupported input binding: {selector}"
                    )
                output_key = match.group(1)
                if output_key not in producers:
                    raise PlanCompilationError(
                        f"Binding references missing output: {output_key}"
                    )
                step_dependencies = (producers[output_key],)
            dependencies[step.name] = step_dependencies

            if step.output_key:
                if step.output_key in producers:
                    raise PlanCompilationError(
                        f"Duplicate output key: {step.output_key}"
                    )
                producers[step.output_key] = step.name

        return CompiledPlan(plan=plan, dependencies=dependencies)
