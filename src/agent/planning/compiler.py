from __future__ import annotations

from collections.abc import Mapping
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
    dependencies: Mapping[str, tuple[str, ...]]


class PlanCompiler:
    OUTPUT_SELECTOR = re.compile(r"^\$\.outputs\.([A-Za-z0-9_-]+)$")
    SUPPORTED_PRECONDITIONS = frozenset({"target_evidence"})

    def compile(self, plan: WorkflowPlan, policy: WorkflowPolicy) -> CompiledPlan:
        seen_steps: set[str] = set()
        producers: dict[str, str] = {}
        producer_capabilities: dict[str, str] = {}
        dependencies: dict[str, tuple[str, ...]] = {}
        allowed = set(policy.allowed_tools)

        for step in plan.steps:
            if step.name in seen_steps:
                raise PlanCompilationError(f"Duplicate step name: {step.name}")
            seen_steps.add(step.name)
            unknown_preconditions = (
                set(step.preconditions) - self.SUPPORTED_PRECONDITIONS
            )
            if unknown_preconditions:
                raise PlanCompilationError(
                    f"Unsupported semantic preconditions for {step.name}: "
                    f"{sorted(unknown_preconditions)}"
                )
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
                if selector == "$.workflow":
                    step_dependencies = tuple(
                        producer
                        for output_key, producer in producers.items()
                        if producer_capabilities[output_key]
                        == "target.structure.search"
                    )
                    if not step_dependencies:
                        raise PlanCompilationError(
                            "Workflow binding requires an upstream target "
                            "structure search output"
                        )
                else:
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
                producer_capabilities[step.output_key] = requested.name

        step_capabilities = {
            step.name: (
                step.capability or capability_for_tool(step.tool_name).name
            )
            for step in plan.steps
        }

        for step in plan.steps:
            if "target_evidence" not in step.preconditions:
                continue
            dependency_steps = dependencies.get(step.name, ())
            if not any(
                step_capabilities.get(dependency) == "target.structure.search"
                for dependency in dependency_steps
            ):
                raise PlanCompilationError(
                    f"Semantic precondition target_evidence for {step.name} "
                    "must consume a target structure search output"
                )

        if plan.workflow_name == "target_driven_design":
            target_step = next(
                (
                    step
                    for step in plan.steps
                    if step_capabilities[step.name] == "target.structure.search"
                ),
                None,
            )
            generation_step = next(
                (
                    step
                    for step in plan.steps
                    if step_capabilities[step.name] == "molecule.generate"
                ),
                None,
            )
            expected_binding = (
                f"$.outputs.{target_step.output_key}"
                if target_step and target_step.output_key
                else None
            )
            if (
                generation_step is None
                or not expected_binding
                or generation_step.input_binding
                not in {expected_binding, "$.workflow"}
            ):
                raise PlanCompilationError(
                    "Target-driven generation must consume target evidence"
                )
            if "target_evidence" not in generation_step.preconditions:
                raise PlanCompilationError(
                    "Target-driven generation must declare target evidence precondition"
                )

        if plan.workflow_name == "hit_to_lead_optimization":
            generation_index = next(
                (
                    index
                    for index, step in enumerate(plan.steps)
                    if step_capabilities[step.name] == "molecule.generate"
                ),
                None,
            )
            baseline_step = next(
                (
                    step
                    for index, step in enumerate(plan.steps)
                    if generation_index is not None
                    and index < generation_index
                    and step_capabilities[step.name] == "molecule.properties"
                    and step.output_key
                ),
                None,
            )
            generation_step = (
                plan.steps[generation_index]
                if generation_index is not None
                else None
            )
            expected_binding = (
                f"$.outputs.{baseline_step.output_key}"
                if baseline_step
                else None
            )
            if (
                generation_step is None
                or not expected_binding
                or generation_step.input_binding != expected_binding
            ):
                raise PlanCompilationError(
                    "Hit-to-lead generation must consume baseline properties"
                )

        return CompiledPlan(plan=plan, dependencies=dependencies)
