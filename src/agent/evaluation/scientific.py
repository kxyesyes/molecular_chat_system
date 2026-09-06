from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import uuid4

from src.agent.contracts import AgentContext, ToolResult
from src.agent.harness import HarnessFactory
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.persistence import SQLiteAgentStateStore, redact_sensitive
from src.agent.planning import TaskPlanner
from src.agent.routing import HybridSkillRouter
from src.agent.runtime.workflow_executor import WorkflowExecutor
from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
from src.agent.tools.admet_predictor import ADMETPredictor
from src.agent.tools.candidate_ranker import CandidateRanker
from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
from src.agent.tools.llm_molecular_generator import LLMMolecularGenerator
from src.agent.tools.molecular_docking import MolecularDocking
from src.agent.tools.property_calculator import PropertyCalculator
from src.agent.tools.rag_search_tool import RAGSearchTool
from src.agent.tools.reverse_target_tool import ReverseTargetTool
from src.agent.tools.target_database_tool import TargetDatabaseTool
from src.agent.validators import SemanticInputValidator
from src.agent.workflows import WorkflowCatalog

from .models import EvaluationCase
from .runner import chemistry_metrics, latency_metrics, load_cases


GOLDEN_SCIENTIFIC_CASE_IDS = [f"GOLD-{index:03d}" for index in range(1, 13)]


def check_generated_candidate_truth(result: dict[str, Any]) -> dict[str, Any]:
    entries = result.get("tool_results") or []
    generator = next(
        (
            item
            for item in entries
            if item.get("tool_name") == "llm_molecular_generator"
        ),
        None,
    )
    if not generator:
        return {"passed": False, "reason": "generator_result_missing"}
    quality = generator.get("quality") or {}
    if quality.get("validation_method") != "RDKit":
        return {"passed": False, "reason": "rdkit_validation_missing"}
    if int(quality.get("unique_count") or 0) < int(
        quality.get("valid_count") or 0
    ):
        return {
            "passed": False,
            "reason": "generated_candidates_not_unique",
        }
    return {"passed": True, "reason": "validated_unique_candidates"}


def check_scientific_claim_evidence(result: dict[str, Any]) -> dict[str, Any]:
    for claim in result.get("claims") or []:
        if not claim.get("evidence_ids"):
            return {"passed": False, "reason": "claim_without_evidence"}
    return {"passed": True, "reason": "all_claims_have_evidence"}


def check_data_flow_evidence(result: dict[str, Any]) -> dict[str, Any]:
    steps = ((result.get("plan") or {}).get("steps") or [])
    generation_outputs = {
        step.get("output_key")
        for step in steps
        if step.get("output_key")
        and step.get("step_id") in {"generate", "molecule_generation"}
    }
    if not generation_outputs:
        return {"passed": True, "reason": "generation_not_requested"}
    bindings = {step.get("input_binding") for step in steps}
    if not any(
        f"$.outputs.{key}" in bindings for key in generation_outputs
    ):
        return {"passed": False, "reason": "generated_output_not_consumed"}
    return {"passed": True, "reason": "generated_output_consumed"}


def summarize_scientific_stability(iterations: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize repeated real scientific acceptance runs.

    `partial` counts as non-catastrophic completion for stability because the
    acceptance contract explicitly allows missing real dependencies to degrade
    to partial instead of fabricated success.
    """

    results = [
        result
        for iteration in iterations
        for result in iteration.get("results", [])
        if isinstance(result, dict)
    ]
    latencies = [
        float(result.get("latency_ms", 0))
        for result in results
        if isinstance(result.get("latency_ms", 0), (int, float))
    ]
    successful = sum(
        1 for result in results if result.get("status") in {"passed", "partial"}
    )
    failure_types = Counter(
        str(result.get("error"))
        for result in results
        if result.get("status") != "passed" and result.get("error")
    )
    return {
        "run_count": len(iterations),
        "case_count": len(results),
        "pass_rate": successful / len(results) if results else 0.0,
        "latency": latency_metrics(latencies),
        "failure_types": dict(failure_types),
    }


def replay_scientific_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Replay anti-hallucination/truth/provenance checks from a saved report."""

    source_cases = report.get("real_cases", {}).get("results", [])
    replayed: list[dict[str, Any]] = []
    for case_result in source_cases:
        result = dict(case_result)
        status, reasons = _replay_case_status(result)
        result["status"] = status
        result["replay_reasons"] = reasons
        replayed.append(result)

    counts = _status_counts(replayed)
    return {
        "mode": "replay",
        "status": _overall_status(replayed),
        "metrics": {
            "case_count": len(replayed),
            "passed_count": counts["passed"],
            "partial_count": counts["partial"],
            "failed_count": counts["failed"],
            "skipped_count": counts["skipped"],
            "pass_rate": (
                (counts["passed"] + counts["partial"]) / len(replayed)
                if replayed
                else 0.0
            ),
        },
        "results": redact_sensitive(replayed),
    }


class ScientificAcceptanceRunner:
    """Execute scientific acceptance cases with real tool outputs."""

    def __init__(
        self,
        dataset_dir: str | Path,
        project_root: str | Path,
        *,
        dataset_path: str | Path | None = None,
        router: HybridSkillRouter | None = None,
        planner: TaskPlanner | None = None,
        tools: Mapping[str, Any] | None = None,
        harness_factory: HarnessFactory | None = None,
    ):
        self.dataset_dir = Path(dataset_dir)
        self.project_root = Path(project_root)
        self.dataset_path = (
            Path(dataset_path)
            if dataset_path is not None
            else self.dataset_dir / "golden_scientific_cases.jsonl"
        )
        if not self.dataset_path.is_absolute():
            self.dataset_path = self.project_root / self.dataset_path
        self.catalog = getattr(router, "catalog", None) or WorkflowCatalog()
        self.router = router or HybridSkillRouter(catalog=self.catalog)
        self.planner = planner or TaskPlanner()
        self.tools = dict(tools or self._build_real_tools())
        self.harness_factory = harness_factory or HarnessFactory()

    def run(self, repeat: int = 1) -> dict[str, Any]:
        iterations = []
        for iteration_index in range(max(1, repeat)):
            iterations.append(self.run_once(iteration_index=iteration_index + 1))

        latest = iterations[-1] if iterations else {"results": []}
        stability = summarize_scientific_stability(iterations)
        results = latest.get("results", [])
        return redact_sensitive(
            {
                "status": _overall_status(results),
                "case_count": len(results),
                "passed_or_partial_count": sum(
                    1
                    for result in results
                    if result.get("status") in {"passed", "partial"}
                ),
                "results": results,
                "iterations": iterations,
                "stability": stability,
                "dataset": _repo_relative(str(self.dataset_path), self.project_root),
            }
        )

    def run_once(self, *, iteration_index: int = 1) -> dict[str, Any]:
        cases = load_cases(self.dataset_path)
        results = [self.run_case(case) for case in cases]
        return {
            "iteration": iteration_index,
            "case_count": len(results),
            "status": _overall_status(results),
            "results": results,
        }

    def run_case(self, case: EvaluationCase) -> dict[str, Any]:
        started = time.perf_counter()
        decision = self.router.decide(case.prompt)
        actual_skill = decision.selected_skill
        execution_error: str | None = None
        executions = []
        harness_shadows: list[dict[str, Any]] = []
        harness_executions: list[dict[str, Any] | None] = []
        execution_records: list[dict[str, Any]] = []
        plan_metadata: dict[str, Any] = {}

        if actual_skill != case.expected_skill:
            execution_error = (
                f"skill_mismatch:{actual_skill or 'none'}"
                f"!= {case.expected_skill or 'none'}"
            )

        policy = self.catalog.get(actual_skill or "")
        if actual_skill is not None and policy is None and execution_error is None:
            execution_error = "skill_not_found"

        if policy is not None:
            repeat_runs = int(case.metadata.get("repeat_runs", 1) or 1)
            with tempfile.TemporaryDirectory(
                prefix="medchat-scientific-acceptance-"
            ) as state_dir:
                state_store = SQLiteAgentStateStore(Path(state_dir) / "state.sqlite")
                for run_index in range(max(1, repeat_runs)):
                    trace_suffix = uuid4().hex[:12]
                    trace_id = f"{case.case_id}-{trace_suffix}"
                    if repeat_runs > 1:
                        trace_id = f"{trace_id}-run-{run_index + 1}"
                    context = AgentContext(
                        query=case.prompt,
                        trace_id=trace_id,
                        active_skill=actual_skill,
                        metadata=self._case_metadata(case),
                        mol_count=int(case.metadata.get("requested_count", 5) or 5),
                    )
                    try:
                        executor = WorkflowExecutor(
                            planner=self.planner,
                            orchestrator=WorkflowOrchestrator(
                                state_store=state_store
                            ),
                        )
                        harness_run = self.harness_factory.create(executor).execute(
                            context=context,
                            policy=policy,
                            all_tools=self.tools,
                            idempotency_key=context.metadata.get("idempotency_key"),
                        )
                        execution = harness_run.authoritative
                        runtime_metadata: dict[str, Any] = {}
                        execution_metadata_record: dict[str, Any] | None = None
                        if harness_run.execution is not None:
                            execution_metadata = redact_sensitive(
                                harness_run.execution.to_dict()
                            )
                            if _valid_harness_execution_metadata(
                                execution_metadata,
                                tool_result_count=len(execution.result.tool_results),
                            ):
                                execution.result.metadata[
                                    "harness_execution"
                                ] = execution_metadata
                                execution_metadata_record = execution_metadata
                                runtime_metadata[
                                    "harness_execution"
                                ] = execution_metadata
                            else:
                                execution_error = (
                                    "invalid_harness_execution_metadata"
                                )
                                execution_metadata_record = {
                                    "status": "invalid",
                                    "error_code": (
                                        "invalid_harness_execution_metadata"
                                    ),
                                }
                        harness_executions.append(execution_metadata_record)
                        shadow_metadata = (
                            harness_run.shadow.to_dict()
                            if harness_run.shadow is not None
                            else None
                        )
                        if shadow_metadata is not None:
                            execution.result.metadata["harness_shadow"] = shadow_metadata
                            harness_shadows.append(shadow_metadata)
                            runtime_metadata["harness_shadow"] = shadow_metadata
                        if (
                            runtime_metadata
                            and state_store.get_run(trace_id) is not None
                        ):
                            state_store.update_run_metadata(
                                trace_id,
                                runtime_metadata,
                            )
                        executions.append(execution)
                        for record in state_store.get_tool_executions(trace_id):
                            persisted = dict(record)
                            checkpoint = state_store.latest_checkpoint(
                                trace_id,
                                str(record.get("step_id") or ""),
                            )
                            persisted["input_hash"] = (
                                checkpoint.get("input_hash")
                                if checkpoint
                                else None
                            )
                            execution_records.append(persisted)
                    except Exception as exc:  # pragma: no cover - defensive real-run guard
                        execution_error = f"execution_exception:{type(exc).__name__}"
                        break
            if executions:
                plan_metadata = (
                    dict(executions[0].plan.metadata)
                    if len(executions) == 1
                    else {
                        "repeat_runs": len(executions),
                        "runs": [dict(item.plan.metadata) for item in executions],
                    }
                )

        tool_results = [
            result
            for execution in executions
            for result in execution.result.tool_results
        ]
        primary_execution = executions[0] if executions else None
        actual_tools = [item.tool_name for item in tool_results]
        if (
            any(item is not None for item in harness_executions)
            and any(item is None for item in harness_executions)
        ):
            execution_error = "harness_execution_metadata_missing"
        events = [
            event
            for execution in executions
            for event in execution.events
        ]
        final_text = _collect_case_text(tool_results, primary_execution)
        anti_hallucination = _anti_hallucination(case, final_text)
        tool_provenance = [
            _tool_provenance(
                item,
                self.project_root,
                execution_record=(
                    execution_records[index]
                    if index < len(execution_records)
                    else None
                ),
            )
            for index, item in enumerate(tool_results)
        ]
        truth_checks = self._truth_checks(
            case,
            tool_results,
            anti_hallucination,
            executions=executions,
        )
        automatic_truth_checks: list[str] = []
        if actual_skill == "target_driven_design":
            skipped_steps = [
                item
                for execution in executions
                for item in execution.result.metadata.get("skipped_steps", [])
                if isinstance(item, Mapping)
            ]
            generation_gate_check = _check_target_driven_generation_gate(
                tool_results,
                skipped_steps=skipped_steps,
            )
            truth_checks["target_driven_generation_gate"] = generation_gate_check
            truth_checks["candidate_identity_closed_loop"] = (
                _check_candidate_identity_closed_loop(tool_results)
            )
            if generation_gate_check.get("reason") == (
                "generation_blocked_without_target_evidence"
            ):
                _mark_safe_gate_dependents_skipped(truth_checks)
            automatic_truth_checks.extend(
                [
                    "target_driven_generation_gate",
                    "candidate_identity_closed_loop",
                ]
            )

        def as_truth_status(check: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": "passed" if check.get("passed") else "failed",
                "reason": check.get("reason", "automatic_truth_check"),
            }

        serialized_tool_results = [
            {"tool_name": item.tool_name, **item.to_legacy_dict()}
            for item in tool_results
        ]
        if any(
            item.tool_name == "llm_molecular_generator"
            for item in tool_results
        ):
            truth_checks["generated_candidate_contract"] = as_truth_status(
                check_generated_candidate_truth(
                    {"tool_results": serialized_tool_results}
                )
            )

        claims = (
            primary_execution.result.metadata.get("claims", [])
            if primary_execution
            else []
        )
        truth_checks["scientific_claim_evidence"] = as_truth_status(
            check_scientific_claim_evidence({"claims": claims})
        )

        if primary_execution and any(
            step.name in {"generate", "molecule_generation"}
            for step in primary_execution.plan.steps
        ):
            truth_checks["compiled_data_flow"] = as_truth_status(
                check_data_flow_evidence(
                    {
                        "plan": {
                            "steps": [
                                {
                                    "step_id": step.name,
                                    "output_key": step.output_key,
                                    "input_binding": step.input_binding,
                                }
                                for step in primary_execution.plan.steps
                            ]
                        }
                    }
                )
            )
        status, error = _case_status(
            case=case,
            actual_skill=actual_skill,
            actual_tools=actual_tools,
            anti_hallucination=anti_hallucination,
            truth_checks=truth_checks,
            execution_success=(
                all(
                    bool(item.result.success or item.result.partial)
                    for item in executions
                )
                if executions
                else False
            ),
            execution_error=execution_error,
        )
        scoring = _score_scientific_case(
            case=case,
            actual_skill=actual_skill,
            actual_tools=actual_tools,
            events=events,
            tool_provenance=tool_provenance,
            anti_hallucination=anti_hallucination,
            truth_checks=truth_checks,
            case_status=status,
            case_error=error,
        )
        report = {
                "case_id": case.case_id,
                "status": status,
                "error": error,
                "expected_skill": case.expected_skill,
                "actual_skill": actual_skill,
                "expected_tools": case.expected_tools,
                "actual_tools": actual_tools,
                "events": events,
                "event_sequence": [
                    item.get("event") for item in events if isinstance(item, dict)
                ],
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "warnings": (
                    [
                        warning
                        for execution in executions
                        for warning in execution.result.warnings
                    ]
                ),
                "tool_provenance": tool_provenance,
                "anti_hallucination": anti_hallucination,
                "truth_checks": truth_checks,
                "automatic_truth_checks": automatic_truth_checks,
                "plan_metadata": plan_metadata,
                **scoring,
            }
        if harness_shadows:
            report["harness_shadow"] = (
                harness_shadows[0]
                if len(harness_shadows) == 1
                else {"runs": harness_shadows}
            )
        if any(item is not None for item in harness_executions):
            aligned_harness_executions = [
                item
                if item is not None
                else {
                    "status": "invalid",
                    "error_code": "harness_execution_metadata_missing",
                }
                for item in harness_executions
            ]
            report["harness_execution"] = (
                aligned_harness_executions[0]
                if len(aligned_harness_executions) == 1
                else {"runs": aligned_harness_executions}
            )
        return _redact_case_literals(
            redact_sensitive(report),
            (
                case.prompt,
                (case.metadata or {}).get("idempotency_key"),
            ),
        )

    def _case_metadata(self, case: EvaluationCase) -> dict[str, Any]:
        metadata = dict(case.metadata or {})
        docking_input = metadata.get("docking_input")
        if isinstance(docking_input, dict):
            metadata["docking_input"] = _resolve_docking_input(
                docking_input,
                self.project_root,
            )
        return metadata

    def _truth_checks(
        self,
        case: EvaluationCase,
        tool_results: list[ToolResult],
        anti_hallucination: dict[str, Any],
        executions: list[Any] | None = None,
    ) -> dict[str, dict[str, Any]]:
        requested = case.scientific_acceptance.get("truth_checks", [])
        checks: dict[str, dict[str, Any]] = {}
        for name in requested:
            if name in {"rdkit_properties", "drug_likeness", "baseline_properties"}:
                checks[name] = _check_successful_tool(
                    tool_results,
                    "property_calculator"
                    if name != "drug_likeness"
                    else "drug_likeness_assessment",
                    evidence="RDKit-derived structured data",
                )
            elif name in {"candidate_properties"}:
                checks[name] = _check_successful_tool(
                    tool_results,
                    "property_calculator",
                    evidence="candidate property data",
                )
            elif name == "admet_method":
                checks[name] = _check_admet_method(tool_results)
            elif name == "rg_mpnn_provenance":
                checks[name] = _check_rg_mpnn(tool_results)
            elif name == "target_database_evidence":
                checks[name] = _check_target_database_evidence(tool_results)
            elif name == "reverse_target_evidence":
                checks[name] = _check_reverse_target_evidence(tool_results)
            elif name == "reverse_target_low_confidence":
                checks[name] = _check_reverse_target_low_confidence(tool_results)
            elif name in {
                "generation_count_valid_unique",
                "generation_validity",
            }:
                checks[name] = _check_generation(
                    tool_results,
                    requested_count=case.metadata.get("requested_count"),
                    expected_model=case.metadata.get(
                        "expected_generator_model",
                        "gmm-llama:latest",
                    ),
                    strict_count=name == "generation_count_valid_unique",
                )
            elif name in {"vina_pose", "binding_energy_numeric"}:
                checks[name] = _check_vina(tool_results)
            elif name == "invalid_smiles_rejected":
                checks[name] = _check_invalid_smiles_rejected(
                    tool_results,
                    anti_hallucination,
                )
            elif name == "docking_parameters_required":
                checks[name] = _check_docking_rejected(
                    tool_results,
                    anti_hallucination,
                )
            elif name == "rag_source_or_explicit_no_hit":
                checks[name] = _check_rag_source_or_explicit_no_hit(tool_results)
            elif name == "generation_repeat_stats":
                checks[name] = _check_generation_repeat_stats(
                    tool_results,
                    repeat_runs=case.metadata.get("repeat_runs", 1),
                    requested_count=case.metadata.get("requested_count"),
                    expected_model=case.metadata.get(
                        "expected_generator_model",
                        "gmm-llama:latest",
                    ),
                    executions=executions or [],
                )
            elif name == "workflow_repeat_consistency":
                checks[name] = _check_workflow_repeat_consistency(executions or [])
            elif name == "data_flow_generated_smiles_consumed":
                checks[name] = _check_data_flow_generated_smiles_consumed(
                    tool_results,
                    downstream_tools=case.metadata.get(
                        "downstream_tools",
                        ["property_calculator", "admet_predictor", "activity_predictor"],
                    ),
                    executions=executions or [],
                )
            elif name == "data_flow_reverse_targets_consumed":
                checks[name] = _check_data_flow_reverse_targets_consumed(
                    tool_results,
                    executions=executions or [],
                )
            elif name == "core_retention_aromatic_ring":
                checks[name] = _check_core_retention_aromatic_ring(
                    tool_results,
                    core_smiles=str(case.metadata.get("core_smiles", "c1ccccc1")),
                )
            elif name == "unauthorized_tool_rejected":
                checks[name] = _check_unauthorized_tool_rejected(
                    tool_results,
                    anti_hallucination,
                )
            else:
                checks[name] = {
                    "status": "partial",
                    "reason": f"truth_check_not_implemented:{name}",
                }
        return checks

    def _build_real_tools(self) -> dict[str, Any]:
        tools: dict[str, Any] = {
            "property_calculator": PropertyCalculator(),
            "drug_likeness_assessment": DrugLikenessAssessment(),
            "admet_predictor": ADMETPredictor(),
            "activity_predictor": ActivityPredictorTool(),
            "candidate_ranker": CandidateRanker(),
            "reverse_target_predictor": ReverseTargetTool(),
            "target_database_search": TargetDatabaseTool(),
            "molecular_docking": MolecularDocking(),
            "rag_search": RAGSearchTool(),
        }
        tools["llm_molecular_generator"] = LLMMolecularGenerator(
            _build_ollama_model()
        )
        return tools


def _build_ollama_model() -> Any:
    try:
        from src.web.models.ollama_model import OllamaModel

        return OllamaModel(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            model_name=os.getenv("OLLAMA_MODEL", "gmm-llama:latest"),
        )
    except Exception:
        return None


def _status_counts(results: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counter = Counter(str(item.get("status", "failed")) for item in results)
    return {
        "passed": counter.get("passed", 0),
        "partial": counter.get("partial", 0),
        "failed": counter.get("failed", 0),
        "skipped": counter.get("skipped", 0),
    }


def _overall_status(results: Iterable[Mapping[str, Any]]) -> str:
    result_list = list(results)
    if not result_list:
        return "failed"
    statuses = {str(item.get("status", "failed")) for item in result_list}
    if "failed" in statuses:
        return "failed"
    if statuses & {"partial", "skipped"}:
        return "partial"
    return "passed"


def _replay_case_status(case_result: Mapping[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    anti = case_result.get("anti_hallucination") or {}
    if anti.get("forbidden_found"):
        reasons.append("forbidden_patterns_found")
    if anti.get("status") == "failed":
        reasons.append("anti_hallucination_failed")
    if not case_result.get("tool_provenance") and case_result.get("expected_tools"):
        reasons.append("missing_tool_provenance")
    truth_checks = case_result.get("truth_checks") or {}
    requires_truth_checks = bool(case_result.get("expected_tools")) or (
        case_result.get("expected_skill") is not None
    )
    if not truth_checks and requires_truth_checks:
        reasons.append("missing_truth_checks")
    failed_truth = [
        name
        for name, check in truth_checks.items()
        if isinstance(check, Mapping) and check.get("status") == "failed"
    ]
    partial_truth = [
        name
        for name, check in truth_checks.items()
        if isinstance(check, Mapping) and check.get("status") == "partial"
    ]
    if failed_truth:
        reasons.append("truth_failed:" + ",".join(sorted(failed_truth)))
    if reasons:
        return "failed", reasons
    if partial_truth or case_result.get("status") in {"partial", "skipped"}:
        return "partial", ["truth_partial:" + ",".join(sorted(partial_truth))]
    return "passed", []


def _collect_case_text(
    tool_results: list[ToolResult],
    execution: Any | None,
) -> str:
    parts = []
    if execution is not None:
        parts.extend(
            [
                getattr(execution.result, "message", ""),
                getattr(execution.result, "final_answer", ""),
            ]
        )
    for result in tool_results:
        parts.extend(
            [
                result.message,
                result.formatted,
                json.dumps(result.data, ensure_ascii=False, default=str),
            ]
        )
    return "\n".join(str(part) for part in parts if part)


def _anti_hallucination(
    case: EvaluationCase,
    final_text: str,
) -> dict[str, Any]:
    lowered = final_text.lower()
    forbidden_found = [
        pattern
        for pattern in case.forbidden_patterns
        if pattern.lower() in lowered
    ]
    return {
        "status": "failed" if forbidden_found else "passed",
        "forbidden_found": forbidden_found,
        "checked_patterns": list(case.forbidden_patterns),
    }


def _tool_provenance(
    result: ToolResult,
    project_root: Path,
    *,
    execution_record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    persisted = dict(execution_record or {})
    data_summary = _summarize_value(result.data)
    model_name = (
        result.quality.get("model_name")
        or result.quality.get("model")
        or result.quality.get("model_provenance", {}).get("model_path")
        or result.quality.get("model_provenance", {}).get("model_type")
        or result.quality.get("engine")
    )
    return {
        "tool_name": result.tool_name,
        "model_name": model_name,
        "success": result.success,
        "trace_id": persisted.get("trace_id"),
        "step_id": persisted.get("step_id")
        or result.quality.get("step_id"),
        "input_hash": persisted.get("input_hash"),
        "input_summary": _summarize_value(persisted.get("input")),
        "output_summary": data_summary or result.message,
        "artifact_paths": _artifact_paths(result, project_root),
        "quality": result.quality,
        "evidence": result.evidence,
    }


def _artifact_paths(result: ToolResult, project_root: Path) -> list[str]:
    paths = []
    for artifact in result.artifacts:
        raw_path = artifact.path
        paths.append(_repo_relative(raw_path, project_root))
    if isinstance(result.data, Mapping):
        for key in ("pose_file", "output_pdbqt", "output_file", "result_file"):
            if result.data.get(key):
                paths.append(_repo_relative(str(result.data[key]), project_root))
    return sorted(set(paths))


def _repo_relative(path_value: str, project_root: Path) -> str:
    try:
        path = Path(path_value)
        if not path.is_absolute():
            path = (project_root / path).resolve()
        return str(path.resolve().relative_to(project_root.resolve()))
    except Exception:
        return path_value


def _summarize_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, Mapping):
        keys = ", ".join(str(key) for key in list(value.keys())[:8])
        return f"dict[{keys}]"
    text = str(value)
    return text[:300] + ("..." if len(text) > 300 else "")


def _resolve_docking_input(
    docking_input: Mapping[str, Any],
    project_root: Path,
) -> dict[str, Any]:
    resolved = dict(docking_input)
    for key in ("receptor_path", "ligand_path"):
        if resolved.get(key):
            path = Path(str(resolved[key]))
            if not path.is_absolute():
                path = (project_root / path).resolve()
            resolved[key] = str(path)
    return resolved


def _valid_harness_execution_metadata(
    metadata: Any,
    *,
    tool_result_count: int,
) -> bool:
    if not isinstance(metadata, Mapping):
        return False
    backend = metadata.get("backend")
    attempts = metadata.get("tool_attempt_count")
    fingerprint = metadata.get("plan_fingerprint")
    return (
        backend in {"legacy", "langgraph"}
        and type(attempts) is int
        and 0 <= attempts <= max(0, int(tool_result_count))
        and type(fingerprint) is str
        and len(fingerprint) == 64
    )


def _redact_case_literals(value: Any, literals: Iterable[Any]) -> Any:
    secrets = tuple(
        item for item in literals if type(item) is str and item
    )
    if isinstance(value, Mapping):
        return {
            _redact_case_literals(key, secrets): _redact_case_literals(
                item,
                secrets,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_case_literals(item, secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_case_literals(item, secrets) for item in value)
    if isinstance(value, str):
        redacted = value
        for secret in secrets:
            redacted = redacted.replace(secret, "[REDACTED]")
        return redacted
    return value


def _find_tool(
    tool_results: list[ToolResult],
    tool_name: str,
) -> ToolResult | None:
    return next((item for item in tool_results if item.tool_name == tool_name), None)


def _check_successful_tool(
    tool_results: list[ToolResult],
    tool_name: str,
    *,
    evidence: str,
) -> dict[str, Any]:
    result = _find_tool(tool_results, tool_name)
    if result is None:
        return {"status": "failed", "reason": f"tool_not_run:{tool_name}"}
    if not result.success:
        return {
            "status": "partial",
            "reason": result.message or f"tool_failed:{tool_name}",
        }
    return {"status": "passed", "evidence": evidence}


def _check_admet_method(tool_results: list[ToolResult]) -> dict[str, Any]:
    result = _find_tool(tool_results, "admet_predictor")
    if result is None:
        return {"status": "failed", "reason": "tool_not_run:admet_predictor"}
    if not result.success:
        return {"status": "partial", "reason": result.message}
    entries = result.data if isinstance(result.data, list) else []
    admet_records = [
        entry.get("admet")
        for entry in entries
        if isinstance(entry, Mapping) and isinstance(entry.get("admet"), Mapping)
    ]
    if not admet_records or any(
        not record.get("prediction_method") for record in admet_records
    ):
        return {"status": "failed", "reason": "missing_admet_prediction_method"}
    if any(
        str(record.get("prediction_method")).casefold() == "adme_py"
        and str(record.get("backend_version") or "").casefold()
        in {"", "unknown", "none"}
        for record in admet_records
    ):
        return {
            "status": "partial",
            "reason": "adme_py_package_version_unavailable",
        }
    return {
        "status": "passed",
        "prediction_methods": sorted(
            {str(record["prediction_method"]) for record in admet_records}
        ),
    }


def _check_rg_mpnn(tool_results: list[ToolResult]) -> dict[str, Any]:
    result = _find_tool(tool_results, "activity_predictor")
    if result is None:
        return {"status": "failed", "reason": "tool_not_run:activity_predictor"}
    provenance = result.quality.get("model_provenance", {})
    demo_mode = bool(provenance.get("demo_mode", True))
    if not result.success:
        return {"status": "partial", "reason": result.message}
    if demo_mode:
        return {
            "status": "partial",
            "reason": "rg_mpnn_demo_mode",
            "provenance": provenance,
        }
    return {"status": "passed", "provenance": provenance}


def _check_target_database_evidence(tool_results: list[ToolResult]) -> dict[str, Any]:
    result = _find_tool(tool_results, "target_database_search")
    if result is None:
        return {"status": "failed", "reason": "tool_not_run:target_database_search"}
    if not result.success:
        return {"status": "partial", "reason": result.message}
    records = result.data if isinstance(result.data, list) else []
    if not records:
        return {"status": "partial", "reason": "no_target_records"}
    structure_claims_without_records = [
        record
        for record in records
        if isinstance(record, Mapping)
        and (
            record.get("structure_count") not in (None, "", 0, "0")
            or bool(record.get("has_experimental_structure"))
            or bool(record.get("has_alphafold_structure"))
        )
        and not (
            isinstance(record.get("recommended_structures"), list)
            and record.get("recommended_structures")
        )
    ]
    if structure_claims_without_records:
        return {
            "status": "failed",
            "reason": "target_structure_counts_without_recommended_records",
        }
    evidence_count = sum(1 for record in records if _target_record_has_evidence(record))
    if evidence_count:
        recommended_structure_count = sum(
            len(record.get("recommended_structures", []))
            for record in records
            if isinstance(record, Mapping)
            and isinstance(record.get("recommended_structures"), list)
        )
        return {
            "status": "passed",
            "evidence_count": evidence_count,
            "record_count": len(records),
            "recommended_structure_count": recommended_structure_count,
        }
    return {"status": "failed", "reason": "target_records_without_evidence"}


def _check_target_driven_generation_gate(
    tool_results: list[ToolResult],
    skipped_steps: list[Mapping[str, Any]],
) -> dict[str, Any]:
    validator = SemanticInputValidator()
    gate_step = WorkflowStep(
        "molecule_generation",
        "llm_molecular_generator",
        preconditions=("target_evidence",),
    )
    target_results = [
        item
        for item in tool_results
        if item.tool_name == "target_database_search" and item.success
    ]
    usable_evidence = any(
        validator.validate(gate_step, item.data).allowed
        for item in target_results
    )
    generation_ran = any(
        item.tool_name == "llm_molecular_generator"
        for item in tool_results
    )

    if not usable_evidence:
        if generation_ran:
            return {
                "status": "failed",
                "reason": "generation_ran_without_usable_target_evidence",
            }
        documented_skip = any(
            item.get("status") == "skipped_precondition"
            and item.get("requirement") == "target_evidence"
            and item.get("step_id") in {"generate", "molecule_generation"}
            for item in skipped_steps
        )
        if documented_skip:
            return {
                "status": "passed",
                "reason": "generation_blocked_without_target_evidence",
            }
        return {
            "status": "failed",
            "reason": "missing_target_evidence_gate_record",
        }

    if not generation_ran:
        return {
            "status": "failed",
            "reason": "generation_not_run_with_usable_target_evidence",
        }
    return {"status": "passed", "reason": "usable_target_evidence_consumed"}


def _check_candidate_identity_closed_loop(
    tool_results: list[ToolResult],
) -> dict[str, Any]:
    generation_results = [
        item
        for item in tool_results
        if item.tool_name == "llm_molecular_generator"
    ]
    if not generation_results:
        return {"status": "passed", "reason": "generation_not_run"}
    if any(not item.success for item in generation_results):
        return {"status": "failed", "reason": "generation_not_successful"}

    try:
        from rdkit import Chem, rdBase
    except Exception:
        return {
            "status": "partial",
            "reason": "rdkit_unavailable_for_candidate_identity",
        }

    def canonical_smiles(record: Mapping[str, Any]) -> str | None:
        value = record.get("canonical_smiles") or record.get("smiles")
        if not isinstance(value, str) or not value.strip():
            return None
        with rdBase.BlockLogs():
            molecule = Chem.MolFromSmiles(value)
            return Chem.MolToSmiles(molecule) if molecule is not None else None

    generated_ids: list[str] = []
    generated_smiles_by_id: dict[str, str] = {}
    for result in generation_results:
        records = _candidate_records(result.data)
        if not records:
            return {"status": "failed", "reason": "generated_candidates_missing"}
        result_ids = [record.get("candidate_id") for record in records]
        if any(not isinstance(item, str) or not item.strip() for item in result_ids):
            return {"status": "failed", "reason": "generated_candidate_id_missing"}
        normalized_ids = [str(item).strip() for item in result_ids]
        if len(normalized_ids) != len(set(normalized_ids)):
            return {"status": "failed", "reason": "generated_candidate_id_duplicate"}
        for candidate_id, record in zip(normalized_ids, records):
            canonical = canonical_smiles(record)
            if canonical is None:
                return {
                    "status": "failed",
                    "reason": "generated_candidate_smiles_invalid",
                    "candidate_id": candidate_id,
                }
            previous = generated_smiles_by_id.get(candidate_id)
            if previous is not None and previous != canonical:
                return {
                    "status": "failed",
                    "reason": "generated_candidate_id_reassigned",
                    "candidate_id": candidate_id,
                }
            generated_smiles_by_id[candidate_id] = canonical
        generated_ids.extend(normalized_ids)

    generated_set = set(generated_ids)
    downstream_tools = {
        "property_calculator",
        "drug_likeness_assessment",
        "admet_predictor",
        "activity_predictor",
    }
    downstream_records: dict[str, list[Mapping[str, Any]]] = {}
    for result in tool_results:
        if result.tool_name not in downstream_tools or not result.success:
            continue
        records = _candidate_records(result.data)
        if records:
            downstream_records.setdefault(result.tool_name, []).extend(records)

    for tool_name, records in downstream_records.items():
        ids = [record.get("candidate_id") for record in records]
        if any(not isinstance(item, str) or not item.strip() for item in ids):
            return {
                "status": "failed",
                "reason": "downstream_candidate_id_missing",
                "tool_name": tool_name,
            }
        unknown = sorted(
            {
                str(item).strip()
                for item in ids
                if str(item).strip() not in generated_set
            }
        )
        if unknown:
            return {
                "status": "failed",
                "reason": "unknown_downstream_candidate_id",
                "tool_name": tool_name,
                "unknown_candidate_ids": unknown,
            }
        for record in records:
            candidate_id = str(record.get("candidate_id")).strip()
            canonical = canonical_smiles(record)
            if canonical is None:
                return {
                    "status": "failed",
                    "reason": "downstream_candidate_smiles_invalid",
                    "tool_name": tool_name,
                    "candidate_id": candidate_id,
                }
            if canonical != generated_smiles_by_id[candidate_id]:
                return {
                    "status": "failed",
                    "reason": "downstream_candidate_smiles_mismatch",
                    "tool_name": tool_name,
                    "candidate_id": candidate_id,
                }

    property_ids = {
        str(record.get("candidate_id")).strip()
        for record in downstream_records.get("property_calculator", [])
        if isinstance(record.get("candidate_id"), str)
    }
    aligned_property_ids = generated_set & property_ids
    if not aligned_property_ids:
        return {
            "status": "failed",
            "reason": "required_candidate_properties_missing",
        }
    missing = sorted(generated_set - aligned_property_ids)
    if missing:
        return {
            "status": "partial",
            "reason": "required_candidate_properties_incomplete",
            "missing_candidate_ids": missing,
        }
    return {
        "status": "passed",
        "generated_candidate_count": len(generated_set),
        "property_candidate_count": len(aligned_property_ids),
    }


def _mark_safe_gate_dependents_skipped(
    truth_checks: dict[str, dict[str, Any]],
) -> None:
    dependent_checks = {
        "generation_count_valid_unique": "llm_molecular_generator",
        "admet_method": "admet_predictor",
        "rg_mpnn_provenance": "activity_predictor",
        "candidate_properties": "property_calculator",
        "data_flow_generated_smiles_consumed": "llm_molecular_generator",
    }
    for name, tool_name in dependent_checks.items():
        check = truth_checks.get(name)
        if not isinstance(check, Mapping):
            continue
        if check.get("reason") != f"tool_not_run:{tool_name}":
            continue
        truth_checks[name] = {
            "status": "skipped",
            "reason": "blocked_by_target_evidence_gate",
        }


def _candidate_records(value: Any) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []

    def collect(item: Any) -> None:
        if isinstance(item, Mapping):
            if "candidate_id" in item or (
                "smiles" in item
                and not any(
                    key in item
                    for key in ("candidates", "records", "results", "data")
                )
            ):
                records.append(item)
                return
            for key in ("candidates", "records", "results", "data"):
                if key in item:
                    collect(item[key])
        elif isinstance(item, (list, tuple)):
            for child in item:
                collect(child)

    collect(value)
    return records


def _target_record_has_evidence(record: Any) -> bool:
    if not isinstance(record, Mapping):
        return False
    identity_keys = {
        "target_id",
        "gene_symbol",
        "uniprot_id",
        "chembl_target_id",
        "pdb_id",
        "pdb_ids",
    }
    if not any(record.get(key) for key in identity_keys):
        return False
    if record.get("source_url") or record.get("match_reason"):
        return True
    structures = record.get("recommended_structures") or record.get("structures")
    if not isinstance(structures, list):
        return False
    return any(
        isinstance(structure, Mapping)
        and bool(structure.get("structure_id") or structure.get("pdb_id"))
        and bool(structure.get("source") or structure.get("source_url"))
        for structure in structures
    )


def _check_reverse_target_evidence(tool_results: list[ToolResult]) -> dict[str, Any]:
    result = _find_tool(tool_results, "reverse_target_predictor")
    if result is None:
        return {
            "status": "failed",
            "reason": "tool_not_run:reverse_target_predictor",
        }
    if not result.success:
        return {"status": "partial", "reason": result.message}
    records = result.data if isinstance(result.data, list) else []
    if not records:
        return {"status": "partial", "reason": "no_reverse_target_records"}
    missing = [
        record
        for record in records
        if not isinstance(record, Mapping)
        or not record.get("target_identifier")
        or not any(
            record.get(key) is not None
            for key in (
                "final_similarity",
                "morgan_similarity",
                "maccs_similarity",
                "evidence",
                "source",
            )
        )
    ]
    if missing:
        return {"status": "failed", "reason": "reverse_targets_without_evidence"}
    return {"status": "passed", "evidence_count": len(records)}


def _check_reverse_target_low_confidence(
    tool_results: list[ToolResult],
) -> dict[str, Any]:
    evidence = _check_reverse_target_evidence(tool_results)
    if evidence["status"] == "failed":
        return evidence
    result = _find_tool(tool_results, "reverse_target_predictor")
    records = result.data if result is not None and isinstance(result.data, list) else []
    if not records:
        return {"status": "passed", "evidence": "no high-confidence targets"}
    similarities = [
        float(record.get("final_similarity", 0.0))
        for record in records
        if isinstance(record, Mapping)
        and isinstance(record.get("final_similarity", 0.0), (int, float))
    ]
    if not similarities or max(similarities) < 0.7:
        return {"status": "passed", "max_similarity": max(similarities or [0.0])}
    return {
        "status": "partial",
        "reason": "targets_not_low_confidence",
        "max_similarity": max(similarities),
    }


def _check_generation(
    tool_results: list[ToolResult],
    *,
    requested_count: Any,
    expected_model: str,
    strict_count: bool,
) -> dict[str, Any]:
    result = _find_tool(tool_results, "llm_molecular_generator")
    if result is None:
        return {"status": "failed", "reason": "tool_not_run:llm_molecular_generator"}
    if not result.success:
        return {"status": "partial", "reason": result.message}
    if (
        isinstance(result.data, Mapping)
        and isinstance(result.data.get("candidates"), list)
    ):
        records = result.data["candidates"]
    else:
        records = result.data if isinstance(result.data, list) else []
    smiles = [
        str(item.get("smiles") if isinstance(item, Mapping) else item)
        for item in records
        if item
    ]
    requested = int(requested_count or len(smiles) or 0)
    metrics = chemistry_metrics(smiles, requested_count=requested if strict_count else None)
    candidate_model_names: set[str] = set()
    for item in records:
        if not isinstance(item, Mapping):
            continue
        generation_provenance = item.get("generation_provenance")
        if isinstance(generation_provenance, Mapping):
            model_name = generation_provenance.get("model_name")
            if model_name:
                candidate_model_names.add(str(model_name))
        metadata = item.get("metadata")
        if isinstance(metadata, Mapping) and metadata.get("model"):
            candidate_model_names.add(str(metadata["model"]))
        if item.get("model"):
            candidate_model_names.add(str(item["model"]))
    top_level_model_names: set[str] = set()
    if result.provenance and result.provenance.model_name:
        top_level_model_names.add(str(result.provenance.model_name))
    if result.quality.get("model"):
        top_level_model_names.add(str(result.quality["model"]))
    model_names = candidate_model_names | top_level_model_names
    if not model_names:
        return {
            "status": "partial",
            "reason": "model_provenance_missing",
            "metrics": metrics,
            "models": [],
        }
    expected_models = {expected_model}
    if (
        candidate_model_names
        and candidate_model_names != expected_models
    ) or (
        top_level_model_names
        and top_level_model_names != expected_models
    ):
        return {
            "status": "partial",
            "reason": "conflicting_generation_model_provenance",
            "metrics": metrics,
            "models": sorted(model_names),
        }
    valid_unique_ok = metrics["valid_smiles_rate"] == 1.0 and (
        metrics["unique_valid_smiles"] >= requested if strict_count else bool(smiles)
    )
    count_ok = (
        metrics["requested_count_compliance"] if strict_count else bool(smiles)
    )
    if valid_unique_ok and count_ok:
        return {"status": "passed", "metrics": metrics, "models": sorted(model_names)}
    return {
        "status": "partial",
        "reason": "generation_acceptance_not_met",
        "metrics": metrics,
        "models": sorted(model_names),
    }


def _check_vina(tool_results: list[ToolResult]) -> dict[str, Any]:
    result = _find_tool(tool_results, "molecular_docking")
    if result is None:
        return {"status": "failed", "reason": "tool_not_run:molecular_docking"}
    if not result.success:
        return {"status": "partial", "reason": result.message, "data": result.data}
    data = result.data if isinstance(result.data, Mapping) else {}
    energy = _extract_binding_energy(data)
    pose_exists = _pose_file_exists(data, result)
    if isinstance(energy, (int, float)) and pose_exists:
        return {"status": "passed", "binding_energy": energy, "pose_file_exists": True}
    return {
        "status": "partial",
        "reason": "missing_numeric_energy_or_pose",
        "binding_energy": energy,
        "pose_file_exists": pose_exists,
    }


def _extract_binding_energy(data: Mapping[str, Any]) -> float | None:
    candidates = [
        data.get("best_energy"),
        data.get("binding_energy"),
        data.get("affinity"),
    ]
    best_pose = data.get("best_pose")
    if isinstance(best_pose, Mapping):
        candidates.extend(
            [
                best_pose.get("binding_energy"),
                best_pose.get("energy"),
                best_pose.get("affinity"),
            ]
        )
    poses = data.get("poses")
    if isinstance(poses, list) and poses:
        first = poses[0]
        if isinstance(first, Mapping):
            candidates.extend(
                [first.get("energy"), first.get("binding_energy"), first.get("affinity")]
            )
    results = data.get("results")
    if isinstance(results, list) and results:
        first_result = results[0]
        if isinstance(first_result, Mapping):
            candidates.extend(
                [
                    first_result.get("binding_energy"),
                    first_result.get("energy"),
                    first_result.get("affinity"),
                ]
            )
    for value in candidates:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            match = re.search(r"-?\d+(?:\.\d+)?", value)
            if match:
                return float(match.group(0))
    return None


def _pose_file_exists(data: Mapping[str, Any], result: ToolResult) -> bool:
    candidates: list[str] = []
    for key in ("pose_file", "output_pdbqt", "output_file", "result_file"):
        if data.get(key):
            candidates.append(str(data[key]))
    for artifact in result.artifacts:
        candidates.append(artifact.path)
    for value in candidates:
        try:
            if Path(value).is_file():
                return True
        except OSError:
            continue
    return False


def _check_invalid_smiles_rejected(
    tool_results: list[ToolResult],
    anti_hallucination: Mapping[str, Any],
) -> dict[str, Any]:
    successful_claim_tools = [
        result.tool_name for result in tool_results if result.success
    ]
    if anti_hallucination.get("forbidden_found"):
        return {
            "status": "failed",
            "reason": "forbidden_scientific_claim_for_invalid_smiles",
        }
    if not successful_claim_tools:
        return {"status": "passed", "evidence": "no successful scientific tool output"}
    return {
        "status": "failed",
        "reason": "invalid_smiles_produced_successful_tool_output",
        "successful_tools": successful_claim_tools,
    }


def _check_docking_rejected(
    tool_results: list[ToolResult],
    anti_hallucination: Mapping[str, Any],
) -> dict[str, Any]:
    if anti_hallucination.get("forbidden_found"):
        return {"status": "failed", "reason": "docking_energy_claim_without_inputs"}
    result = _find_tool(tool_results, "molecular_docking")
    if result is None:
        return {"status": "passed", "evidence": "docking tool not executed"}
    if not result.success:
        return {"status": "passed", "evidence": result.message}
    return {"status": "failed", "reason": "docking_succeeded_without_required_inputs"}


def _check_rag_source_or_explicit_no_hit(
    tool_results: list[ToolResult],
) -> dict[str, Any]:
    result = _find_tool_any(tool_results, {"rag_search", "rag_database_search"})
    if result is None:
        return {"status": "failed", "reason": "tool_not_run:rag_search"}
    if not result.success:
        return {"status": "partial", "reason": result.message}

    records = result.data if isinstance(result.data, list) else []
    if records:
        sourced = [
            record
            for record in records
            if isinstance(record, Mapping)
            and any(
                record.get(key)
                for key in (
                    "source",
                    "source_path",
                    "document",
                    "file",
                    "url",
                    "citation",
                    "similarity_score",
                )
            )
        ]
        if sourced or result.evidence:
            return {
                "status": "passed",
                "evidence_count": len(sourced) + len(result.evidence),
            }
        return {"status": "failed", "reason": "rag_records_without_source"}

    message = (result.message or result.formatted or "").lower()
    no_hit_markers = (
        "no matching",
        "no relevant",
        "no results",
        "not found",
        "未检索",
        "未找到",
        "没有检索",
        "没有找到",
        "无匹配",
    )
    if any(marker in message for marker in no_hit_markers):
        return {"status": "passed", "evidence": "explicit_no_hit"}
    return {"status": "failed", "reason": "empty_rag_result_without_no_hit_message"}


def _check_generation_repeat_stats(
    tool_results: list[ToolResult],
    *,
    repeat_runs: Any,
    requested_count: Any,
    expected_model: str,
    executions: list[Any] | None = None,
) -> dict[str, Any]:
    expected_runs = int(repeat_runs or 1)
    requested = int(requested_count or 0)
    groups = _generation_groups(tool_results, executions or [])
    if not groups:
        return {"status": "failed", "reason": "tool_not_run:llm_molecular_generator"}
    if len(groups) < expected_runs:
        return {
            "status": "partial",
            "reason": "repeat_generation_runs_missing",
            "actual_runs": len(groups),
            "expected_runs": expected_runs,
        }

    summaries = []
    failures = []
    for index, records in enumerate(groups[:expected_runs], 1):
        smiles = _extract_smiles_values(records)
        metrics = chemistry_metrics(
            smiles,
            requested_count=requested if requested else None,
        )
        model_names = {
            str(item.get("model"))
            for item in records
            if isinstance(item, Mapping) and item.get("model")
        }
        valid_count = int(round(metrics["valid_smiles_rate"] * len(smiles)))
        unique_count = int(metrics["unique_valid_smiles"])
        summary = {
            "run": index,
            "actual_count": len(smiles),
            "valid_count": valid_count,
            "unique_count": unique_count,
            "duplicate_count": max(0, valid_count - unique_count),
            "models": sorted(model_names),
        }
        summaries.append(summary)
        if requested and len(smiles) != requested:
            failures.append(f"run_{index}_count")
        if requested and unique_count < requested:
            failures.append(f"run_{index}_unique")
        if smiles and valid_count != len(smiles):
            failures.append(f"run_{index}_validity")
        if model_names and expected_model not in model_names:
            failures.append(f"run_{index}_model")

    if failures:
        return {
            "status": "partial",
            "reason": "generation_repeat_acceptance_not_met",
            "failures": failures,
            "runs": summaries,
        }
    return {"status": "passed", "runs": summaries}


def _check_workflow_repeat_consistency(executions: list[Any]) -> dict[str, Any]:
    if len(executions) < 2:
        return {"status": "partial", "reason": "repeat_workflow_runs_missing"}
    tool_sequences = [
        [result.tool_name for result in execution.result.tool_results]
        for execution in executions
    ]
    event_sequences = [
        [
            event.get("event")
            for event in execution.events
            if isinstance(event, Mapping)
            and event.get("event")
            in {
                "task_started",
                "planning_started",
                "planning_completed",
                "tool_started",
                "tool_completed",
                "tool_failed",
                "task_completed",
                "task_failed",
            }
        ]
        for execution in executions
    ]
    statuses = [
        "partial"
        if execution.result.partial
        else "passed"
        if execution.result.success
        else "failed"
        for execution in executions
    ]
    if len({tuple(sequence) for sequence in tool_sequences}) > 1:
        return {
            "status": "partial",
            "reason": "tool_order_changed",
            "tool_sequences": tool_sequences,
        }
    if len({tuple(sequence) for sequence in event_sequences}) > 1:
        return {
            "status": "partial",
            "reason": "event_sequence_changed",
            "event_sequences": event_sequences,
        }
    if len(set(statuses)) > 1:
        return {"status": "partial", "reason": "workflow_status_changed", "statuses": statuses}
    return {
        "status": "passed",
        "tool_sequence": tool_sequences[0],
        "event_sequence": event_sequences[0],
        "statuses": statuses,
    }


def _check_data_flow_generated_smiles_consumed(
    tool_results: list[ToolResult],
    *,
    downstream_tools: list[str],
    executions: list[Any] | None = None,
) -> dict[str, Any]:
    plan_evidence = (
        _plan_has_data_flow(
            executions or [],
            source_output_key="molecules",
            downstream_tools=set(downstream_tools),
        )
        or _plan_has_data_flow(
            executions or [],
            source_output_key="candidates",
            downstream_tools=set(downstream_tools),
        )
    )
    generator = _find_tool(tool_results, "llm_molecular_generator")
    if generator is None:
        if plan_evidence:
            return {
                "status": "partial",
                "reason": "plan_wiring_without_generator_execution",
                "plan_wiring": plan_evidence,
            }
        return {"status": "failed", "reason": "tool_not_run:llm_molecular_generator"}
    if not generator.success:
        return {
            "status": "partial",
            "reason": generator.message,
            "plan_wiring": plan_evidence,
        }
    generated = _canonical_smiles_set(generator.data)
    if not generated:
        return {
            "status": "partial",
            "reason": "no_generated_smiles",
            "plan_wiring": plan_evidence,
        }

    consumed_by = []
    observed_downstream = []
    for tool_name in downstream_tools:
        result = _find_tool(tool_results, tool_name)
        if result is None or not result.success:
            continue
        observed_downstream.append(tool_name)
        downstream_smiles = _canonical_smiles_set(result.data)
        if downstream_smiles & generated:
            consumed_by.append(tool_name)
    if consumed_by:
        return {
            "status": "passed",
            "consumed_by": consumed_by,
            "plan_wiring": plan_evidence,
            "generated_canonical_smiles": sorted(generated),
        }
    if not observed_downstream and plan_evidence:
        return {
            "status": "partial",
            "reason": "plan_wiring_without_observed_downstream_output",
            "plan_wiring": plan_evidence,
        }
    return {
        "status": "failed",
        "reason": "generated_smiles_not_consumed",
        "plan_wiring": plan_evidence,
        "observed_downstream_tools": observed_downstream,
    }


def _check_data_flow_reverse_targets_consumed(
    tool_results: list[ToolResult],
    *,
    executions: list[Any] | None = None,
) -> dict[str, Any]:
    plan_evidence = _plan_has_data_flow(
        executions or [],
        source_output_key="targets",
        downstream_tools={"target_database_search"},
    )
    reverse = _find_tool(tool_results, "reverse_target_predictor")
    target_search = _find_tool(tool_results, "target_database_search")
    if reverse is None:
        if plan_evidence:
            return {
                "status": "partial",
                "reason": "plan_wiring_without_reverse_target_execution",
                "plan_wiring": plan_evidence,
            }
        return {"status": "failed", "reason": "tool_not_run:reverse_target_predictor"}
    if target_search is None:
        if plan_evidence:
            return {
                "status": "partial",
                "reason": "plan_wiring_without_target_search_execution",
                "plan_wiring": plan_evidence,
            }
        return {"status": "failed", "reason": "tool_not_run:target_database_search"}
    if not reverse.success:
        return {"status": "partial", "reason": reverse.message}
    if not target_search.success:
        return {"status": "partial", "reason": target_search.message}
    reverse_targets = _target_identifier_set(reverse.data)
    searched_targets = _target_identifier_set(target_search.data)
    consumed = sorted(reverse_targets & searched_targets)
    if consumed:
        return {
            "status": "passed",
            "consumed_target_identifiers": consumed,
            "plan_wiring": plan_evidence,
        }
    if not reverse_targets or not searched_targets:
        return {
            "status": "partial",
            "reason": "target_dataflow_records_missing",
            "plan_wiring": plan_evidence,
        }
    return {
        "status": "failed",
        "reason": "reverse_targets_not_consumed",
        "plan_wiring": plan_evidence,
    }


def _check_core_retention_aromatic_ring(
    tool_results: list[ToolResult],
    *,
    core_smiles: str,
) -> dict[str, Any]:
    result = _find_tool(tool_results, "llm_molecular_generator")
    if result is None:
        return {"status": "failed", "reason": "tool_not_run:llm_molecular_generator"}
    if not result.success:
        return {"status": "partial", "reason": result.message}
    smiles = _extract_smiles_values(result.data)
    if not smiles:
        return {"status": "partial", "reason": "no_generated_smiles"}
    retained = [value for value in smiles if _has_aromatic_core(value, core_smiles)]
    if len(retained) == len(smiles):
        return {"status": "passed", "retained_count": len(retained)}
    return {
        "status": "partial",
        "reason": "aromatic_core_not_retained_by_all_candidates",
        "retained_count": len(retained),
        "candidate_count": len(smiles),
    }


def _check_unauthorized_tool_rejected(
    tool_results: list[ToolResult],
    anti_hallucination: Mapping[str, Any],
) -> dict[str, Any]:
    if anti_hallucination.get("forbidden_found"):
        return {"status": "failed", "reason": "forbidden_claim_after_injection"}
    unsafe_success = [
        result.tool_name
        for result in tool_results
        if result.success and result.tool_name in {"run_docking", "molecular_docking"}
    ]
    if unsafe_success:
        return {
            "status": "failed",
            "reason": "unauthorized_or_incomplete_docking_succeeded",
            "tools": unsafe_success,
        }
    return {"status": "passed", "evidence": "no unauthorized successful tool call"}


def _generation_groups(
    tool_results: list[ToolResult],
    executions: list[Any],
) -> list[list[Any]]:
    if executions:
        groups = []
        for execution in executions:
            result = _find_tool(execution.result.tool_results, "llm_molecular_generator")
            if result and result.success:
                data = result.data
                groups.append(data if isinstance(data, list) else [data])
        return groups

    result = _find_tool(tool_results, "llm_molecular_generator")
    if result is None or not result.success:
        return []
    data = result.data
    if isinstance(data, Mapping) and isinstance(data.get("repeat_runs"), list):
        return [
            run if isinstance(run, list) else [run]
            for run in data["repeat_runs"]
        ]
    return [data if isinstance(data, list) else [data]]


def _plan_has_data_flow(
    executions: list[Any],
    *,
    source_output_key: str,
    downstream_tools: set[str],
) -> str | None:
    for execution in executions:
        source_steps = {
            step.output_key
            for step in execution.plan.steps
            if step.output_key == source_output_key
        }
        if not source_steps:
            continue
        actual_tools = {result.tool_name for result in execution.result.tool_results}
        for step in execution.plan.steps:
            if (
                step.tool_name in downstream_tools
                and step.input_from == source_output_key
                and step.tool_name in actual_tools
            ):
                return f"{source_output_key}->{step.tool_name}"
    return None


def _extract_smiles_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, Mapping):
        if value.get("smiles"):
            values.append(str(value["smiles"]))
        for nested_key in ("data", "results", "molecules", "candidates"):
            nested = value.get(nested_key)
            if nested is not None:
                values.extend(_extract_smiles_values(nested))
    elif isinstance(value, list):
        for item in value:
            values.extend(_extract_smiles_values(item))
    elif isinstance(value, str):
        if re.search(r"[BCNOPSFI]|Cl|Br", value):
            values.append(value)
    return [item for item in values if item]


def _canonical_smiles_set(value: Any) -> set[str]:
    raw_values = _extract_smiles_values(value)
    if not raw_values:
        return set()
    try:
        from rdkit import Chem
    except ImportError:
        return {item.strip() for item in raw_values if item.strip()}

    canonical: set[str] = set()
    for smiles in raw_values:
        molecule = Chem.MolFromSmiles(smiles)
        if molecule is not None:
            canonical.add(
                Chem.MolToSmiles(
                    molecule,
                    canonical=True,
                    isomericSmiles=True,
                )
            )
    return canonical


def _target_identifier_set(value: Any) -> set[str]:
    identifiers: set[str] = set()

    def add(raw: Any) -> None:
        if raw in (None, ""):
            return
        normalized = re.sub(r"[^a-z0-9]+", "", str(raw).casefold())
        if normalized:
            identifiers.add(normalized)

    def collect(item: Any) -> None:
        if isinstance(item, Mapping):
            for key in (
                "target_identifier",
                "target_chembl_id",
                "uniprot_id",
                "target_id",
                "gene_symbol",
                "target_gene",
                "target_name",
                "protein_name",
                "source_query",
            ):
                add(item.get(key))
            for nested_key in ("data", "results", "targets"):
                if nested_key in item:
                    collect(item[nested_key])
        elif isinstance(item, (list, tuple)):
            for nested in item:
                collect(nested)

    collect(value)
    return identifiers


def _find_tool_any(
    tool_results: list[ToolResult],
    tool_names: set[str],
) -> ToolResult | None:
    return next((item for item in tool_results if item.tool_name in tool_names), None)


def _has_aromatic_core(smiles: str, core_smiles: str) -> bool:
    try:
        from rdkit import Chem

        mol = Chem.MolFromSmiles(smiles)
        core = Chem.MolFromSmiles(core_smiles)
        return bool(mol is not None and core is not None and mol.HasSubstructMatch(core))
    except Exception:
        normalized = smiles.lower()
        return "c1ccccc1" in normalized or "c1cccc" in normalized


def _case_status(
    *,
    case: EvaluationCase,
    actual_skill: str | None,
    actual_tools: list[str],
    anti_hallucination: Mapping[str, Any],
    truth_checks: Mapping[str, Mapping[str, Any]],
    execution_success: bool,
    execution_error: str | None,
) -> tuple[str, str | None]:
    if anti_hallucination.get("forbidden_found"):
        return "failed", "forbidden_patterns_found"
    if actual_skill != case.expected_skill:
        return "failed", execution_error or "skill_mismatch"
    forbidden_called = set(actual_tools) & set(case.forbidden_tools)
    if forbidden_called:
        return "failed", "forbidden_tools_called:" + ",".join(sorted(forbidden_called))
    if execution_error:
        return "failed", execution_error
    failed_truth = [
        name
        for name, check in truth_checks.items()
        if check.get("status") == "failed"
    ]
    if failed_truth:
        return "failed", "truth_failed:" + ",".join(sorted(failed_truth))
    partial_truth = [
        name
        for name, check in truth_checks.items()
        if check.get("status") == "partial"
    ]
    if partial_truth:
        return "partial", "truth_partial:" + ",".join(sorted(partial_truth))
    expected_missing = [
        name for name in case.expected_tools if name not in actual_tools
    ]
    if expected_missing:
        return "partial", "expected_tools_missing:" + ",".join(expected_missing)
    if not execution_success and case.expected_tools:
        return "partial", "workflow_not_successful"
    return "passed", None


def _score_scientific_case(
    *,
    case: EvaluationCase,
    actual_skill: str | None,
    actual_tools: list[str],
    events: list[Mapping[str, Any]],
    tool_provenance: list[Mapping[str, Any]],
    anti_hallucination: Mapping[str, Any],
    truth_checks: Mapping[str, Mapping[str, Any]],
    case_status: str,
    case_error: str | None,
) -> dict[str, Any]:
    """Score a real scientific case using the documented 100-point rubric."""

    routing = 15 if actual_skill == case.expected_skill else 0

    expected_counts = Counter(case.expected_tools)
    actual_counts = Counter(actual_tools)
    forbidden_called = set(actual_tools) & set(case.forbidden_tools)
    successful_tool_called = any(
        bool(item.get("success")) for item in tool_provenance
    )
    expected_tools_present = all(
        actual_counts[name] >= count for name, count in expected_counts.items()
    )
    if case.expected_tools:
        tool_selection_ok = expected_tools_present and not forbidden_called
    else:
        tool_selection_ok = not forbidden_called and not successful_tool_called
    tool_selection = 15 if tool_selection_ok else 0

    if case.expected_tools:
        order_ok = _is_ordered_subsequence(case.expected_tools, actual_tools)
    else:
        order_ok = not successful_tool_called
    workflow_order = 15 if order_ok else 0

    truth_statuses = [str(check.get("status", "failed")) for check in truth_checks.values()]
    required_provenance = list(
        case.scientific_acceptance.get("provenance_required", []) or []
    ) or list(dict.fromkeys(case.expected_tools))
    provenance_names = [str(item.get("tool_name", "")) for item in tool_provenance]
    provenance_present = all(name in provenance_names for name in required_provenance)
    required_provenance_records = [
        item
        for name in required_provenance
        for item in tool_provenance
        if str(item.get("tool_name", "")) == name
    ]
    provenance_complete = provenance_present and all(
        _provenance_record_complete(item)
        for item in required_provenance_records
    )
    if "failed" in truth_statuses:
        real_tool_execution = 0
    elif "partial" in truth_statuses:
        real_tool_execution = 10
    elif truth_statuses:
        real_tool_execution = 20
    elif not case.expected_tools and not successful_tool_called:
        real_tool_execution = 20
    elif provenance_present and case_status == "passed":
        real_tool_execution = 20
    else:
        real_tool_execution = 0

    data_flow_checks = [
        check
        for name, check in truth_checks.items()
        if name.startswith("data_flow_")
        or name in {"workflow_repeat_consistency", "generation_repeat_stats"}
    ]
    data_flow_statuses = [str(check.get("status", "failed")) for check in data_flow_checks]
    if "failed" in data_flow_statuses:
        upstream_data_consumption = 0
    elif "partial" in data_flow_statuses:
        upstream_data_consumption = 7
    else:
        upstream_data_consumption = 15

    anti_hallucination_score = (
        10
        if not anti_hallucination.get("forbidden_found")
        and anti_hallucination.get("status") != "failed"
        else 0
    )

    event_sequence = [
        str(item.get("event", "")) for item in events if isinstance(item, Mapping)
    ]
    expected_events_present = _is_ordered_subsequence(
        case.expected_events,
        event_sequence,
    )
    trace_present = not case.expected_events or all(
        bool(item.get("trace_id")) for item in events if isinstance(item, Mapping)
    )
    event_trace_present = (
        not case.expected_tools
        or bool(events)
        and expected_events_present
        and trace_present
    )
    structured_provenance = (
        10
        if provenance_complete and event_trace_present
        else 0
    )

    score_breakdown = {
        "routing": routing,
        "tool_selection": tool_selection,
        "workflow_order": workflow_order,
        "real_tool_execution": real_tool_execution,
        "upstream_data_consumption": upstream_data_consumption,
        "anti_hallucination": anti_hallucination_score,
        "structured_provenance": structured_provenance,
    }
    hard_failure_reasons = _hard_failure_reasons(
        case_error=case_error,
        anti_hallucination=anti_hallucination,
        truth_checks=truth_checks,
    )
    return {
        "score": 0 if hard_failure_reasons else sum(score_breakdown.values()),
        "score_breakdown": score_breakdown,
        "hard_failure": bool(hard_failure_reasons),
        "hard_failure_reasons": hard_failure_reasons,
    }


def _provenance_record_complete(record: Mapping[str, Any]) -> bool:
    input_hash = str(record.get("input_hash") or "")
    return bool(
        str(record.get("tool_name") or "").strip()
        and str(record.get("trace_id") or "").strip()
        and str(record.get("input_summary") or "").strip()
        and len(input_hash) == 64
        and all(character in "0123456789abcdefABCDEF" for character in input_hash)
        and str(record.get("output_summary") or "").strip()
    )


def _is_ordered_subsequence(expected: Iterable[str], actual: Iterable[str]) -> bool:
    remaining = iter(actual)
    return all(any(item == expected_item for item in remaining) for expected_item in expected)


def _hard_failure_reasons(
    *,
    case_error: str | None,
    anti_hallucination: Mapping[str, Any],
    truth_checks: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    if anti_hallucination.get("forbidden_found") or anti_hallucination.get("status") == "failed":
        reasons.append("forbidden_patterns_found")
    if case_error and case_error.startswith("forbidden_tools_called:"):
        reasons.append(case_error)

    unsafe_markers = (
        "fabricat",
        "simulat",
        "demo",
        "without_input",
        "without inputs",
        "scientific_claim",
        "energy_claim",
    )
    for name, check in truth_checks.items():
        if check.get("status") != "failed":
            continue
        reason = str(check.get("reason", "")).lower()
        if any(marker in reason for marker in unsafe_markers):
            reasons.append(f"truth_failed:{name}")
    return list(dict.fromkeys(reasons))
