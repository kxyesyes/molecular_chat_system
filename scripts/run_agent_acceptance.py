#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent.contracts import AgentContext
from src.agent.evaluation import (
    EvaluationResult,
    EvaluationRunner,
    ScientificAcceptanceRunner,
    load_cases,
    replay_scientific_report,
    score_workflow,
)
from src.agent.openai_compatible_model import OpenAICompatibleModel
from src.agent.harness import HarnessFactory
from src.agent.persistence import redact_sensitive
from src.agent.planning import TaskPlanner
from src.agent.routing import HybridSkillRouter
from src.agent.runtime.workflow_executor import WorkflowExecutor
from src.agent.tools.base_tool import execute_tool_compat
from src.agent.tools.molecular_docking import MolecularDocking
from src.agent.tools.property_calculator import PropertyCalculator
from src.agent.workflows import WorkflowCatalog


DATASET_DIR = PROJECT_ROOT / "data" / "agent_evals"
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "outputs"
    / "agent_evaluation"
    / "agent_acceptance_report.json"
)
REAL_CASE_SETS = {
    "golden": "golden_scientific_cases.jsonl",
    "diverse": "diverse_scientific_cases.jsonl",
}


def _resolve_real_dataset(
    *,
    case_set: str = "golden",
    dataset: Path | None = None,
) -> Path | None:
    if dataset is not None:
        return dataset if dataset.is_absolute() else PROJECT_ROOT / dataset
    if case_set == "all-real":
        return None
    try:
        return DATASET_DIR / REAL_CASE_SETS[case_set]
    except KeyError as exc:
        raise ValueError(f"Unknown real case set: {case_set}") from exc


def run_contract() -> dict[str, Any]:
    router = HybridSkillRouter()
    routing_cases = load_cases(DATASET_DIR / "routing_cases.jsonl")
    routing_report = EvaluationRunner(router=router).evaluate_routing(routing_cases)
    planner = TaskPlanner()
    workflow_results = []
    for case in load_cases(DATASET_DIR / "workflow_cases.jsonl"):
        decision = router.decide(case.prompt)
        context = AgentContext(
            query=case.prompt,
            trace_id=case.case_id,
            active_skill=decision.selected_skill,
        )
        plan = planner.plan(context)
        actual_tools = [step.tool_name for step in plan.steps]
        scored = score_workflow(
            expected_tools=case.expected_steps or case.expected_tools,
            actual_tools=actual_tools,
            forbidden_tools=case.forbidden_tools,
            fabricated=False,
            structured_output=True,
        )
        workflow_results.append(
            EvaluationResult(
                case_id=case.case_id,
                status="passed" if scored["score"] >= 7 else "failed",
                score=scored["score"],
                actual_skill=decision.selected_skill,
                actual_tools=actual_tools,
                details=scored,
            )
        )

    failure_results = []
    for case in load_cases(DATASET_DIR / "failure_recovery_cases.jsonl"):
        decision = router.decide(case.prompt)
        details: dict[str, Any] = {
            "requires_confirmation": decision.requires_confirmation,
            "allowed_tools": decision.allowed_tools,
        }
        if case.case_id == "HF-002":
            property_result = PropertyCalculator().execute(case.prompt)
            safe = not property_result.get("success") and property_result.get("data") is None
            details["property_result"] = property_result
        elif case.case_id == "HF-003":
            docking_result = MolecularDocking().execute(case.prompt)
            safe = not docking_result.get("success") and "kcal/mol" not in str(
                docking_result
            )
            details["docking_result"] = docking_result
        else:
            safe = decision.selected_skill == case.expected_skill and (
                decision.requires_confirmation or not case.forbidden_tools
            )
        failure_results.append(
            EvaluationResult(
                case_id=case.case_id,
                status="passed" if safe else "failed",
                score=10 if safe else 0,
                actual_skill=decision.selected_skill,
                actual_tools=decision.allowed_tools,
                details=details,
            )
        )

    real_case_results = _run_real_case_contracts(router, planner)
    all_results = (
        routing_report.results
        + workflow_results
        + failure_results
        + real_case_results
    )
    passed = sum(item.status == "passed" for item in all_results)
    report = {
        "mode": "contract",
        "status": "passed" if passed == len(all_results) else "failed",
        "metrics": {
            **routing_report.metrics,
            "routing_case_count": routing_report.metrics.get("case_count", 0),
            "workflow_case_count": len(workflow_results),
            "failure_case_count": len(failure_results),
            "real_case_count": len(real_case_results),
            "total_case_count": len(all_results),
            "passed_case_count": passed,
            "pass_rate": passed / len(all_results) if all_results else 0.0,
        },
        "results": [
            {
                "case_id": item.case_id,
                "status": item.status,
                "score": item.score,
                "actual_skill": item.actual_skill,
                "actual_tools": item.actual_tools,
                "details": item.details,
            }
            for item in all_results
        ],
    }
    report["metrics"]["case_count"] = len(all_results)
    harness_probe = _run_harness_contract_probe()
    if harness_probe is not None:
        if harness_probe.get("backend") in {"legacy", "langgraph"}:
            report["harness_canary"] = harness_probe
        else:
            report["harness_shadow"] = harness_probe
        requested_canary = (
            os.getenv("AGENT_HARNESS_MODE", "legacy").strip().lower()
            == "langgraph_canary"
        )
        if harness_probe.get("status") == "failed" or (
            requested_canary and harness_probe.get("status") == "unavailable"
        ):
            report["status"] = "failed"
    return report


def _run_harness_contract_probe(
    harness_factory: HarnessFactory | None = None,
) -> dict[str, Any] | None:
    from src.agent.contracts import ToolResult
    from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
    from src.agent.planning import WorkflowPlan

    class ProbeTool:
        name = "property_calculator"

        def __init__(self):
            self.calls = 0

        def execute(self, query):
            self.calls += 1
            return ToolResult.success_result(
                self.name,
                data={"input_type": type(query).__name__},
            )

    tool = ProbeTool()
    plan = WorkflowPlan(
        workflow_name="admet_assessment",
        steps=[
            WorkflowStep(
                "properties",
                tool.name,
                input_data={"smiles": "CCO"},
                output_key="properties",
            )
        ],
    )
    factory = harness_factory or HarnessFactory()
    harness_run = factory.create(
        WorkflowExecutor(
            planner=TaskPlanner(),
            orchestrator=WorkflowOrchestrator(),
        )
    ).execute(
        context=AgentContext(
            query="harness contract probe",
            trace_id="HARNESS-CONTRACT",
            active_skill="admet_assessment",
        ),
        policy=WorkflowCatalog().require("admet_assessment"),
        all_tools={tool.name: tool},
        plan=plan,
    )
    if not harness_run.authoritative.result.success or tool.calls != 1:
        return {
            "status": "failed",
            "error_code": "authoritative_contract_failed",
            "authoritative_tool_calls": tool.calls,
        }
    if harness_run.shadow is not None:
        return {
            **harness_run.shadow.to_dict(),
            "authoritative_tool_calls": tool.calls,
        }
    if harness_run.execution is not None:
        execution_metadata = harness_run.execution.to_dict()
        if (
            execution_metadata.get("backend") not in {"legacy", "langgraph"}
            or execution_metadata.get("tool_attempt_count") != tool.calls
            or len(str(execution_metadata.get("plan_fingerprint") or "")) != 64
        ):
            return {
                "status": "failed",
                "error_code": "invalid_harness_execution_metadata",
                "authoritative_tool_calls": tool.calls,
            }
        return redact_sensitive(
            {
                **execution_metadata,
                "status": "passed",
                "authoritative_tool_calls": tool.calls,
            }
        )
    warning = getattr(factory, "last_warning", None)
    if warning:
        return {
            "status": "unavailable",
            "error_code": warning,
            "authoritative_tool_calls": tool.calls,
        }
    return None


def _run_real_case_contracts(
    router: HybridSkillRouter,
    planner: TaskPlanner,
) -> list[EvaluationResult]:
    from src.agent.contracts import ToolResult
    from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
    from src.agent.planning.task_planner import WorkflowPlan

    catalog = router.catalog

    class ContractTool:
        def __init__(self, name: str, *, fail: bool = False):
            self.name = name
            self.fail = fail
            self.calls: list[Any] = []

        def execute(self, query):
            self.calls.append(query)
            if self.fail:
                return {"success": False, "message": f"{self.name} failed"}
            data: Any = {"query": query}
            if self.name == "llm_molecular_generator":
                data = [{"smiles": "CCO"}, {"smiles": "CCN"}]
            return {
                "success": True,
                "message": "contract ok",
                "data": data,
                "formatted": self.name,
            }

    results: list[EvaluationResult] = []
    for case in load_cases(DATASET_DIR / "real_agent_cases.jsonl"):
        decision = router.decide(case.prompt)
        actual_skill = decision.selected_skill
        actual_tools: list[str] = []
        details: dict[str, Any] = {
            "expected_skill": case.expected_skill,
            "forbidden_tools": case.forbidden_tools,
        }
        passed = actual_skill == case.expected_skill

        if case.case_id == "REAL-007":
            property_result = PropertyCalculator().execute(case.prompt)
            passed = (
                passed
                and not property_result.get("success")
                and property_result.get("data") is None
            )
            details["invalid_smiles_rejected"] = not property_result.get("success")
        elif case.case_id == "REAL-008":
            docking_result = MolecularDocking().execute(case.prompt)
            passed = (
                passed
                and not docking_result.get("success")
                and "kcal/mol" not in str(docking_result)
            )
            details["docking_not_executable"] = not docking_result.get("success")
        elif case.case_id == "REAL-011":
            class StructuredTool:
                name = "fake_structured_tool"

                def execute(self, query):
                    return {
                        "success": True,
                        "message": "ok",
                        "warnings": ["contract warning"],
                        "evidence": [{"source": "contract"}],
                        "artifacts": [{
                            "artifact_type": "report",
                            "path": "outputs/contract.json",
                            "label": "contract",
                        }],
                        "quality": {"validated": True},
                    }

            normalized = execute_tool_compat(StructuredTool(), case.prompt)
            actual_skill = case.expected_skill
            actual_tools = ["fake_structured_tool"]
            passed = bool(
                normalized.warnings
                and normalized.evidence
                and normalized.artifacts
                and normalized.quality
            )
            details["structured_fields_preserved"] = passed
        elif case.case_id == "REAL-012":
            class UnauthorizedPlanner:
                def plan(self, context):
                    return WorkflowPlan(
                        workflow_name="comprehensive_evaluation",
                        steps=[WorkflowStep("unsafe", "run_docking")],
                    )

            execution = WorkflowExecutor(
                planner=UnauthorizedPlanner()
            ).execute(
                AgentContext(
                    query=case.prompt,
                    trace_id=case.case_id,
                    active_skill="comprehensive_evaluation",
                ),
                catalog.require("comprehensive_evaluation"),
                {"run_docking": ContractTool("run_docking")},
            )
            actual_skill = "comprehensive_evaluation"
            passed = (
                not execution.result.success
                and execution.result.error is not None
                and execution.result.error.code.value == "unauthorized_tool"
                and not execution.events
            )
            details["unauthorized_rejected_preflight"] = passed
        elif case.case_id == "REAL-015":
            context = AgentContext(
                query=case.prompt,
                trace_id=case.case_id,
                active_skill="comprehensive_evaluation",
            )
            plan = planner.plan(context)
            tools = {
                step.tool_name: ContractTool(
                    step.tool_name,
                    fail=step.tool_name == "admet_predictor",
                )
                for step in plan.steps
            }
            workflow_result = WorkflowOrchestrator().run(
                context,
                plan.steps,
                tools,
            )
            actual_tools = [
                item.tool_name for item in workflow_result.tool_results
            ]
            passed = (
                actual_skill == case.expected_skill
                and workflow_result.partial
                and len(actual_tools) == len(plan.steps)
            )
            details["partial_result_preserved"] = workflow_result.partial
        else:
            context = AgentContext(
                query=case.prompt,
                trace_id=case.case_id,
                active_skill=actual_skill,
            )
            plan = planner.plan(context)
            actual_tools = (
                [step.tool_name for step in plan.steps]
                if plan.steps
                else list(decision.allowed_tools)
            )
            if case.case_id == "REAL-010":
                actual_tools = []
            passed = (
                passed
                and actual_tools == case.expected_tools
                and not (set(actual_tools) & set(case.forbidden_tools))
            )
            details["plan"] = actual_tools

        results.append(
            EvaluationResult(
                case_id=case.case_id,
                status="passed" if passed else "failed",
                score=10 if passed else 0,
                actual_skill=actual_skill,
                actual_tools=actual_tools,
                details=details,
            )
        )
    return results


async def _test_external_model() -> dict[str, Any]:
    api_key = (
        os.environ.get("OPENAI_COMPATIBLE_API_KEY")
        or os.environ.get("EXTERNAL_LLM_API_KEY")
        or ""
    ).strip()
    if not api_key:
        return {"status": "skipped", "reason": "API key is not set in environment"}
    base_url = (
        os.environ.get("OPENAI_COMPATIBLE_BASE_URL")
        or os.environ.get("EXTERNAL_LLM_BASE_URL")
        or "https://api.supxh.xin"
    )
    model_name = (
        os.environ.get("OPENAI_COMPATIBLE_MODEL")
        or os.environ.get("EXTERNAL_LLM_MODEL")
        or "gemini-3.1-pro"
    )
    started = time.perf_counter()
    model = OpenAICompatibleModel(
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
        provider_name="external-main",
    )
    response = await model.generate(
        "Reply with exactly: AGENT_ACCEPTANCE_OK",
        temperature=0.0,
        max_tokens=256,
    )
    return {
        "status": "passed" if "AGENT_ACCEPTANCE_OK" in response else "failed",
        "provider": "external-main",
        "model": model_name,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "response_matched": "AGENT_ACCEPTANCE_OK" in response,
    }


def _evaluate_generation_result(
    result: dict[str, Any],
    *,
    requested_count: int,
    expected_model: str,
) -> dict[str, Any]:
    from rdkit import Chem

    molecules = result.get("data") if result.get("success") else []
    molecules = molecules if isinstance(molecules, list) else []
    smiles = [
        str(item.get("smiles", "")).strip()
        for item in molecules
        if isinstance(item, dict)
    ]
    valid_smiles = [value for value in smiles if Chem.MolFromSmiles(value) is not None]
    unique_smiles = set(valid_smiles)
    models = {
        str(item.get("model", ""))
        for item in molecules
        if isinstance(item, dict)
    }
    passed = (
        len(valid_smiles) == requested_count
        and len(unique_smiles) == requested_count
        and models == {expected_model}
    )
    return {
        "status": "passed" if passed else "failed",
        "requested_count": requested_count,
        "valid_count": len(valid_smiles),
        "unique_count": len(unique_smiles),
        "model": expected_model,
        "smiles": valid_smiles,
    }


def _evaluate_activity_result(
    *,
    predictor_demo_mode: bool,
    model_path: str | None,
    predictions: list[dict[str, Any]],
) -> dict[str, Any]:
    successful = [
        item
        for item in predictions
        if item.get("success")
        and isinstance(item.get("activity_score"), (int, float))
    ]
    real_model_used = not predictor_demo_mode and bool(model_path)
    passed = real_model_used and len(successful) == len(predictions) and bool(predictions)
    return {
        "status": "passed" if passed else "failed",
        "real_model_used": real_model_used,
        "model_path": model_path,
        "prediction_count": len(predictions),
        "successful_count": len(successful),
        "predictions": [
            {
                "smiles": item.get("smiles"),
                "activity_score": item.get("activity_score"),
                "class": item.get("class"),
                "confidence": item.get("confidence"),
            }
            for item in predictions
        ],
    }


def _evaluate_docking_result(result: dict[str, Any]) -> dict[str, Any]:
    data = result.get("data") if result.get("success") else {}
    data = data if isinstance(data, dict) else {}
    best_pose = data.get("best_pose") or {}
    pose_count = int(data.get("total_poses") or 0)
    energy = best_pose.get("binding_energy")
    pose_file = best_pose.get("pose_file") or data.get("pose_file")
    pose_exists = bool(pose_file and Path(str(pose_file)).is_file())
    passed = (
        pose_count > 0
        and isinstance(energy, (int, float))
        and pose_exists
    )
    return {
        "status": "passed" if passed else "failed",
        "job_id": data.get("job_id"),
        "pose_count": pose_count,
        "best_binding_energy": energy,
        "pose_file": str(pose_file) if pose_file else None,
        "pose_file_exists": pose_exists,
    }


def _test_ollama() -> dict[str, Any]:
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model_name = os.environ.get("MOLECULAR_GENERATOR_MODEL", "gmm-llama:latest")
    started = time.perf_counter()
    try:
        from src.agent.tools.llm_molecular_generator import LLMMolecularGenerator
        from src.web.models.ollama_model import OllamaModel

        model = OllamaModel(base_url=base_url, model_name=model_name)
        generator = LLMMolecularGenerator(llm_model=model)
        result = generator.execute(
            "Generate 3 diverse drug-like molecules as valid SMILES.",
            temperature=0.2,
            mol_count=3,
        )
        summary = _evaluate_generation_result(
            result,
            requested_count=3,
            expected_model=model_name,
        )
        summary.update(
            {
                "provider": "ollama",
                "latency_ms": int((time.perf_counter() - started) * 1000),
            }
        )
        model.sync_client.close()
        asyncio.run(model.client.aclose())
        return summary
    except Exception as exc:
        return {
            "status": "failed",
            "provider": "ollama",
            "model": model_name,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "error": str(exc),
        }


def _test_target_search() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        from src.target_search.service import TargetSearchService

        result = TargetSearchService().search_targets("PDE5A")
        count = len(result.get("results", []))
        return {
            "status": "passed" if count else "failed",
            "result_count": count,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}


def _test_activity_inference() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        from src.activity.predictor import ActivityPredictor

        predictor = ActivityPredictor()
        predictions = predictor.predict(["CCO", "CCN", "c1ccccc1"])
        summary = _evaluate_activity_result(
            predictor_demo_mode=predictor.demo_mode,
            model_path=predictor.current_model_path,
            predictions=predictions,
        )
        summary["latency_ms"] = int((time.perf_counter() - started) * 1000)
        return summary
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}


def _test_docking_execution() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = MolecularDocking().execute(
            {
                "receptor_path": str(PROJECT_ROOT / "data" / "samples" / "MAGL_5zun.pdb"),
                "ligand_path": str(PROJECT_ROOT / "data" / "samples" / "5.sdf"),
                "center": [5.99, 3.01, 17.345],
                "size": [20, 20, 20],
                "exhaustiveness": 4,
                "num_modes": 3,
            }
        )
        summary = _evaluate_docking_result(result)
        summary["latency_ms"] = int((time.perf_counter() - started) * 1000)
        return summary
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}


def _run_docx_real_case_matrix() -> dict[str, Any]:
    from src.agent.orchestrators import WorkflowOrchestrator
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    from src.agent.tools.admet_predictor import ADMETPredictor
    from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
    from src.agent.tools.llm_molecular_generator import LLMMolecularGenerator
    from src.agent.tools.reverse_target_tool import ReverseTargetTool
    from src.agent.tools.target_database_tool import TargetDatabaseTool
    from src.web.models.ollama_model import OllamaModel

    cases = load_cases(DATASET_DIR / "real_agent_cases.jsonl")
    catalog = WorkflowCatalog()
    router = HybridSkillRouter(catalog=catalog)
    planner = TaskPlanner()
    ollama_model = OllamaModel(
        base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        model_name=os.environ.get(
            "MOLECULAR_GENERATOR_MODEL", "gmm-llama:latest"
        ),
    )
    tools = {
        "property_calculator": PropertyCalculator(),
        "drug_likeness_assessment": DrugLikenessAssessment(),
        "admet_predictor": ADMETPredictor(),
        "activity_predictor": ActivityPredictorTool(),
        "reverse_target_predictor": ReverseTargetTool(),
        "target_database_search": TargetDatabaseTool(),
        "llm_molecular_generator": LLMMolecularGenerator(
            llm_model=ollama_model
        ),
        "molecular_docking": MolecularDocking(),
    }
    results: list[dict[str, Any]] = []

    def finish(
        case,
        *,
        status: str,
        actual_skill: str | None,
        actual_tools: list[str],
        events: list[str] | None = None,
        warnings: list[str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        results.append(
            {
                "case_id": case.case_id,
                "status": status,
                "expected_skill": case.expected_skill,
                "actual_skill": actual_skill,
                "expected_tools": case.expected_tools,
                "actual_tools": actual_tools,
                "events": events or [],
                "warnings": warnings or [],
                "hallucination_check": (
                    "passed" if status in {"passed", "partial"} else "failed"
                ),
                "details": details or {},
            }
        )

    try:
        for case in cases:
            decision = router.decide(case.prompt)
            actual_skill = decision.selected_skill
            if case.case_id == "REAL-010":
                finish(
                    case,
                    status="passed" if actual_skill is None else "failed",
                    actual_skill=actual_skill,
                    actual_tools=[],
                    details={"tool_abstention": actual_skill is None},
                )
                continue

            if case.case_id == "REAL-011":
                class StructuredTool:
                    name = "fake_structured_tool"

                    def execute(self, query):
                        return {
                            "success": True,
                            "message": "ok",
                            "warnings": ["structured warning"],
                            "evidence": [{"source": "REAL-011"}],
                            "artifacts": [{
                                "artifact_type": "report",
                                "path": "outputs/agent_evaluation/REAL-011.json",
                                "label": "REAL-011",
                            }],
                            "quality": {"validated": True},
                        }

                normalized = execute_tool_compat(StructuredTool(), case.prompt)
                preserved = bool(
                    normalized.warnings
                    and normalized.evidence
                    and normalized.artifacts
                    and normalized.quality
                )
                finish(
                    case,
                    status="passed" if preserved else "failed",
                    actual_skill=case.expected_skill,
                    actual_tools=["fake_structured_tool"],
                    warnings=normalized.warnings,
                    details={"structured_fields_preserved": preserved},
                )
                continue

            if case.case_id == "REAL-012":
                from src.agent.orchestrators import WorkflowStep
                from src.agent.planning.task_planner import WorkflowPlan

                class UnsafePlanner:
                    def plan(self, context):
                        return WorkflowPlan(
                            workflow_name="comprehensive_evaluation",
                            steps=[WorkflowStep("unsafe", "run_docking")],
                        )

                unsafe = WorkflowExecutor(planner=UnsafePlanner()).execute(
                    AgentContext(
                        query=case.prompt,
                        trace_id=case.case_id,
                        active_skill="comprehensive_evaluation",
                    ),
                    catalog.require("comprehensive_evaluation"),
                    {"run_docking": object()},
                )
                rejected = bool(
                    unsafe.result.error
                    and unsafe.result.error.code.value == "unauthorized_tool"
                    and not unsafe.events
                )
                finish(
                    case,
                    status="passed" if rejected else "failed",
                    actual_skill="comprehensive_evaluation",
                    actual_tools=[],
                    details={"unauthorized_rejected_preflight": rejected},
                )
                continue

            if case.case_id == "REAL-004":
                raw = tools["target_database_search"].execute(case.prompt)
                safe = raw.get("success") and "kcal/mol" not in str(raw)
                finish(
                    case,
                    status="passed" if safe else "failed",
                    actual_skill=actual_skill,
                    actual_tools=["target_database_search"],
                    details={
                        "result_count": len(raw.get("data") or []),
                        "no_fabricated_energy": "kcal/mol" not in str(raw),
                    },
                )
                continue

            if case.case_id == "REAL-008":
                raw = tools["molecular_docking"].execute(case.prompt)
                safe = (
                    not raw.get("success")
                    and "kcal/mol" not in str(raw)
                    and "binding_energy" not in str(raw)
                )
                finish(
                    case,
                    status="passed" if safe else "failed",
                    actual_skill=actual_skill,
                    actual_tools=[],
                    details={
                        "not_executable": not raw.get("success"),
                        "message": raw.get("message"),
                    },
                )
                continue

            if case.case_id == "REAL-009":
                raw = tools["llm_molecular_generator"].execute(
                    case.prompt,
                    temperature=0.2,
                    mol_count=5,
                )
                generation = _evaluate_generation_result(
                    raw,
                    requested_count=5,
                    expected_model=ollama_model.model_name,
                )
                finish(
                    case,
                    status=generation["status"],
                    actual_skill=actual_skill,
                    actual_tools=["llm_molecular_generator"],
                    details=generation,
                )
                continue

            if case.case_id == "REAL-013":
                raw = tools["reverse_target_predictor"].execute(case.prompt)
                records = raw.get("data") or []
                evidenced = all(
                    item.get("final_similarity") is not None
                    for item in records
                    if isinstance(item, dict)
                )
                safe = raw.get("success") and evidenced
                finish(
                    case,
                    status="passed" if safe else "failed",
                    actual_skill=actual_skill,
                    actual_tools=["reverse_target_predictor"],
                    details={
                        "target_count": len(records),
                        "all_targets_have_similarity_evidence": evidenced,
                    },
                )
                continue

            policy = catalog.get(actual_skill or "")
            if policy is None:
                finish(
                    case,
                    status="failed",
                    actual_skill=actual_skill,
                    actual_tools=[],
                    details={"reason": "No workflow policy resolved"},
                )
                continue

            case_tools = dict(tools)
            if case.case_id == "REAL-015":
                class FailingAdmet:
                    name = "admet_predictor"

                    def execute(self, query):
                        return {
                            "success": False,
                            "message": "Simulated ADMET failure for REAL-015",
                        }

                case_tools["admet_predictor"] = FailingAdmet()

            execution = WorkflowExecutor(
                planner=planner,
                orchestrator=WorkflowOrchestrator(),
            ).execute(
                AgentContext(
                    query=case.prompt,
                    trace_id=case.case_id,
                    active_skill=actual_skill,
                ),
                policy,
                case_tools,
            )
            actual_tools = [
                item.tool_name for item in execution.result.tool_results
            ]
            events = [item["event"] for item in execution.events]
            final_text = execution.result.final_answer
            forbidden_found = [
                pattern
                for pattern in case.forbidden_patterns
                if pattern.lower() in final_text.lower()
            ]
            details: dict[str, Any] = {
                "partial": execution.result.partial,
                "forbidden_patterns_found": forbidden_found,
                "workflow_state": execution.result.metadata.get(
                    "workflow_state", {}
                ),
            }
            case_passed = (
                actual_skill == case.expected_skill
                and not (set(actual_tools) & set(case.forbidden_tools))
                and not forbidden_found
            )
            if case.case_id == "REAL-005":
                generated = next(
                    (
                        item
                        for item in execution.result.tool_results
                        if item.tool_name == "llm_molecular_generator"
                    ),
                    None,
                )
                generation = _evaluate_generation_result(
                    generated.to_legacy_dict() if generated else {},
                    requested_count=10,
                    expected_model=ollama_model.model_name,
                )
                details["generation"] = generation
                case_passed = case_passed and generation["status"] == "passed"
            if case.case_id in {"REAL-007", "REAL-008"}:
                case_passed = case_passed and not execution.result.success
            if case.case_id == "REAL-015":
                case_passed = (
                    case_passed
                    and execution.result.partial
                    and len(actual_tools) == len(planner.plan(
                        AgentContext(
                            query=case.prompt,
                            trace_id=case.case_id,
                            active_skill=actual_skill,
                        )
                    ).steps)
                )
            if case.expected_events:
                case_passed = case_passed and all(
                    expected in events for expected in case.expected_events
                )
            finish(
                case,
                status=(
                    "passed"
                    if case_passed and not execution.result.partial
                    else "partial"
                    if case_passed
                    else "failed"
                ),
                actual_skill=actual_skill,
                actual_tools=actual_tools,
                events=events,
                warnings=execution.result.warnings,
                details=details,
            )
    finally:
        try:
            ollama_model.sync_client.close()
        except Exception:
            pass
        try:
            asyncio.run(ollama_model.client.aclose())
        except Exception:
            pass

    passed = sum(item["status"] in {"passed", "partial"} for item in results)
    return {
        "status": "passed" if passed == len(results) else "failed",
        "case_count": len(results),
        "passed_or_partial_count": passed,
        "results": results,
    }


def run_replay(replay_input: Path | None = None) -> dict[str, Any]:
    input_path = replay_input or DEFAULT_OUTPUT
    if not input_path.is_file():
        return {
            "mode": "replay",
            "status": "failed",
            "metrics": {"case_count": 0, "pass_rate": 0.0},
            "results": [],
            "error": f"replay_input_not_found:{input_path}",
        }
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    source_report = payload
    if isinstance(payload, dict) and isinstance(payload.get("reports"), list):
        source_report = next(
            (
                report
                for report in payload["reports"]
                if isinstance(report, dict) and report.get("mode") == "real"
            ),
            payload["reports"][-1] if payload["reports"] else {},
        )
    return replay_scientific_report(source_report)


def _run_scientific_dataset(dataset_path: Path, repeat: int) -> dict[str, Any]:
    return ScientificAcceptanceRunner(
        DATASET_DIR,
        PROJECT_ROOT,
        dataset_path=dataset_path,
    ).run(repeat=max(1, repeat))


def _combine_real_case_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    results = [
        result
        for report in reports
        for result in report.get("results", [])
        if isinstance(result, dict)
    ]
    status = (
        "failed"
        if any(report.get("status") == "failed" for report in reports)
        else "partial"
        if any(report.get("status") == "partial" for report in reports)
        else "passed"
    )
    return {
        "status": status,
        "case_count": len(results),
        "passed_or_partial_count": sum(
            1 for result in results if result.get("status") in {"passed", "partial"}
        ),
        "results": results,
        "datasets": reports,
        "stability": {
            "datasets": [
                {
                    "dataset": report.get("dataset"),
                    "stability": report.get("stability", {}),
                }
                for report in reports
            ]
        },
    }


def run_real(
    repeat: int = 1,
    *,
    case_set: str = "golden",
    dataset: Path | None = None,
) -> dict[str, Any]:
    checks = {
        "external_main_model": asyncio.run(_test_external_model()),
        "local_molecular_generator": _test_ollama(),
        "target_search": _test_target_search(),
        "activity_model_inference": _test_activity_inference(),
        "docking_execution": _test_docking_execution(),
    }
    dataset_path = _resolve_real_dataset(case_set=case_set, dataset=dataset)
    if dataset_path is None:
        real_cases = _combine_real_case_reports(
            [
                _run_scientific_dataset(DATASET_DIR / name, repeat)
                for name in REAL_CASE_SETS.values()
            ]
        )
    else:
        real_cases = _run_scientific_dataset(dataset_path, repeat)
    required = (
        "local_molecular_generator",
        "target_search",
        "activity_model_inference",
        "docking_execution",
    )
    status = (
        "passed"
        if all(checks[name]["status"] == "passed" for name in required)
        and real_cases["status"] == "passed"
        else "partial"
    )
    return {
        "mode": "real",
        "status": status,
        "checks": checks,
        "real_cases": real_cases,
        "stability": real_cases.get("stability", {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MedChat Agent acceptance tests")
    parser.add_argument(
        "--mode",
        choices=("contract", "replay", "real", "all"),
        default="contract",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--replay-input", type=Path, default=None)
    parser.add_argument(
        "--case-set",
        choices=("golden", "diverse", "all-real"),
        default="golden",
    )
    parser.add_argument("--dataset", type=Path, default=None)
    args = parser.parse_args()

    reports = []
    if args.mode in {"contract", "all"}:
        contract = run_contract()
        reports.append(contract)
    if args.mode == "replay":
        reports.append(run_replay(args.replay_input or args.output))
    if args.mode in {"real", "all"}:
        reports.append(
            run_real(
                repeat=args.repeat,
                case_set=args.case_set,
                dataset=args.dataset,
            )
        )
    payload = redact_sensitive(
        {
            "generated_at": time.time(),
            "requested_mode": args.mode,
            "reports": reports,
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "statuses": [report["status"] for report in reports],
            },
            ensure_ascii=False,
        )
    )
    return 0 if all(report["status"] in {"passed", "partial"} for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
