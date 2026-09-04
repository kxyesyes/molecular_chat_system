from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
import re

from src.agent.capabilities import capability_for_tool, get_capability
from src.agent.contracts.generation_request import (
    DEFAULT_GENERATION_COUNT,
    GenerationRequestError,
    build_generation_request,
    parse_generation_count,
    validate_generation_count,
)
from src.agent.workflows import WorkflowPolicy

from .bindings import BindingResolutionError, BindingResolver
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
        plan = deepcopy(plan)
        plan_count: int | None = None
        if "requested_count" in plan.metadata:
            try:
                plan_count = validate_generation_count(
                    plan.metadata["requested_count"],
                    field="plan.metadata.requested_count",
                )
            except GenerationRequestError as exc:
                raise PlanCompilationError(str(exc)) from None
        seen_steps: set[str] = set()
        producers: dict[str, str] = {}
        producer_capabilities: dict[str, str] = {}
        dependencies: dict[str, tuple[str, ...]] = {}
        allowed = set(policy.allowed_tools)

        for step_index, step in enumerate(plan.steps):
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

            try:
                BindingResolver.validate_transform(step.input_transform)
                selector = BindingResolver.derive_selector(
                    step.input_binding,
                    step.input_from,
                )
            except BindingResolutionError as exc:
                raise PlanCompilationError(str(exc)) from None

            expected = capability_for_tool(step.tool_name)
            requested = get_capability(step.capability) if step.capability else expected
            if step.tool_name not in requested.tool_names:
                raise PlanCompilationError(
                    f"Tool {step.tool_name} does not implement {requested.name}"
                )
            if expected.name == "molecule.generate":
                input_count: int | None = None
                if isinstance(step.input_data, Mapping):
                    input_metadata = step.input_data.get("metadata")
                    if isinstance(input_metadata, Mapping) and (
                        "requested_count" in input_metadata
                    ):
                        try:
                            input_count = validate_generation_count(
                                input_metadata["requested_count"],
                                field=(
                                    f"steps.{step.name}.input_data.metadata."
                                    "requested_count"
                                ),
                            )
                        except GenerationRequestError as exc:
                            raise PlanCompilationError(str(exc)) from None
                    else:
                        query = str(step.input_data.get("query") or "")
                        try:
                            input_count = validate_generation_count(
                                parse_generation_count(
                                    query,
                                    default=(
                                        plan_count or DEFAULT_GENERATION_COUNT
                                    ),
                                ),
                                field=(
                                    f"steps.{step.name}.input_data."
                                    "requested_count"
                                ),
                            )
                        except GenerationRequestError as exc:
                            raise PlanCompilationError(str(exc)) from None
                elif isinstance(step.input_data, str):
                    try:
                        input_count = validate_generation_count(
                            parse_generation_count(
                                step.input_data,
                                default=plan_count or DEFAULT_GENERATION_COUNT,
                            ),
                            field=f"steps.{step.name}.input_data.requested_count",
                        )
                    except GenerationRequestError as exc:
                        raise PlanCompilationError(str(exc)) from None
                if plan_count is not None and input_count is not None:
                    if input_count != plan_count:
                        raise PlanCompilationError(
                            f"steps.{step.name} requested_count conflicts with "
                            "plan.metadata.requested_count"
                        )
                    if isinstance(step.input_data, str):
                        step = replace(
                            step,
                            input_data=build_generation_request(
                                step.input_data,
                                plan_count,
                            ),
                        )
                        plan.steps[step_index] = step
                    elif isinstance(step.input_data, Mapping) and not (
                        isinstance(step.input_data.get("metadata"), Mapping)
                        and "requested_count" in step.input_data["metadata"]
                    ):
                        step = replace(
                            step,
                            input_data=build_generation_request(
                                str(step.input_data.get("query") or ""),
                                plan_count,
                                outputs=step.input_data.get("outputs"),
                                trust_envelope=step.input_data,
                            ),
                        )
                        plan.steps[step_index] = step
            step_dependencies: tuple[str, ...] = ()
            if selector:
                if selector == "$.workflow":
                    if (
                        "target" not in producers
                        or producer_capabilities["target"]
                        != "target.structure.search"
                    ):
                        raise PlanCompilationError(
                            "Workflow binding requires an upstream target "
                            "structure search with exact output key: target"
                        )
                    step_dependencies = (producers["target"],)
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
