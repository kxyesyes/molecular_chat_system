from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from src.agent.evaluation import (
    EvaluationCase,
    EvaluationRunner,
    chemistry_metrics,
    latency_metrics,
    load_cases,
    score_workflow,
)
from src.agent.evaluation.scientific import (
    GOLDEN_SCIENTIFIC_CASE_IDS,
    ScientificAcceptanceRunner,
    _check_generation,
    _extract_binding_energy,
    _check_data_flow_generated_smiles_consumed,
    _check_generation_repeat_stats,
    _check_rag_source_or_explicit_no_hit,
    _redact_case_literals,
    replay_scientific_report,
    summarize_scientific_stability,
)
from src.agent.contracts import ToolProvenance, ToolResult
from src.agent.harness import (
    HarnessExecutionMetadata,
    HarnessRun,
    LegacyHarness,
    ShadowComparison,
)
from src.agent.routing import HybridSkillRouter
from src.agent.validators import AgentResultValidator
from src.agent.workflows import WorkflowCatalog, WorkflowPolicy


def test_acceptance_modules_import_without_legacy_skill_package():
    root = Path(__file__).resolve().parents[2]
    modules = (
        root / "src" / "agent" / "evaluation" / "scientific.py",
        root / "scripts" / "run_agent_acceptance.py",
        root / "scripts" / "health_check.py",
    )
    forbidden = (
        "src.agent." + "skills",
        "Skill" + "Registry",
        "ComprehensiveEvaluation" + "Skill",
    )

    offenders = {
        path.relative_to(root).as_posix(): [
            marker for marker in forbidden if marker in path.read_text(encoding="utf-8")
        ]
        for path in modules
    }

    assert {path: markers for path, markers in offenders.items() if markers} == {}
    assert importlib.import_module("scripts.run_agent_acceptance") is not None
    assert importlib.import_module("src.agent.evaluation.scientific") is not None
    assert importlib.import_module("scripts.health_check") is not None


def test_scientific_runner_reuses_injected_router_catalog(tmp_path):
    catalog = WorkflowCatalog(
        (
            WorkflowPolicy(
                name="custom_workflow",
                description="custom",
                allowed_tools=("custom_tool",),
            ),
        )
    )
    router = HybridSkillRouter(catalog=catalog)

    runner = ScientificAcceptanceRunner(
        dataset_dir=tmp_path,
        project_root=tmp_path,
        router=router,
        tools={"custom_tool": object()},
    )

    assert runner.catalog is catalog


def test_scientific_report_includes_redacted_harness_shadow(tmp_path):
    class Decision:
        selected_skill = "custom_workflow"

    class Router:
        def __init__(self, catalog):
            self.catalog = catalog

        def decide(self, _prompt):
            return Decision()

    class Tool:
        name = "custom_tool"

        def execute(self, query):
            return ToolResult.success_result(self.name, data={"query": query})

    class ShadowFactory:
        last_warning = None

        def create(self, executor):
            legacy = LegacyHarness(executor)

            class Backend:
                def execute(self, **kwargs):
                    execution = legacy.execute(**kwargs).authoritative
                    return HarnessRun(
                        authoritative=execution,
                        shadow=ShadowComparison(
                            backend="langgraph",
                            backend_version="test",
                            status="matched",
                            plan_fingerprint="a" * 64,
                            matched=True,
                            elapsed_ms=3,
                        ),
                    )

            return Backend()

    class Planner:
        def plan(self, context):
            from src.agent.orchestrators import WorkflowStep
            from src.agent.planning import WorkflowPlan

            return WorkflowPlan(
                workflow_name=context.active_skill,
                steps=[WorkflowStep("custom", "custom_tool", input_data=context.query)],
            )

    catalog = WorkflowCatalog(
        (
            WorkflowPolicy(
                name="custom_workflow",
                description="custom",
                allowed_tools=("custom_tool",),
            ),
        )
    )
    runner = ScientificAcceptanceRunner(
        dataset_dir=tmp_path,
        project_root=tmp_path,
        router=Router(catalog),
        planner=Planner(),
        tools={"custom_tool": Tool()},
        harness_factory=ShadowFactory(),
    )
    case = EvaluationCase(
        case_id="SHADOW-001",
        version="1",
        category="harness",
        prompt="secret prompt",
        expected_skill="custom_workflow",
        expected_tools=["custom_tool"],
    )

    report = runner.run_case(case)

    assert report["harness_shadow"]["status"] == "matched"
    assert "prompt" not in report["harness_shadow"]
    assert "secret prompt" not in json.dumps(report["harness_shadow"])


def test_scientific_report_records_canary_without_sensitive_inputs(tmp_path):
    class Decision:
        selected_skill = "custom_workflow"

    class Router:
        def __init__(self, catalog):
            self.catalog = catalog

        def decide(self, _prompt):
            return Decision()

    class Tool:
        name = "property_calculator"

        def execute(self, query):
            return ToolResult.success_result(self.name, data={"query": query})

    class CanaryFactory:
        last_warning = None

        def create(self, executor):
            legacy = LegacyHarness(executor)

            class Backend:
                def execute(self, **kwargs):
                    execution = legacy.execute(**kwargs).authoritative
                    return HarnessRun(
                        authoritative=execution,
                        execution=HarnessExecutionMetadata(
                            backend="langgraph",
                            backend_version="test",
                            selection_reason="canary_selected",
                            canary_bucket=7,
                            plan_fingerprint="a" * 64,
                            tool_attempt_count=1,
                            fallback_before_execution=False,
                            elapsed_ms=3,
                        ),
                    )

            return Backend()

    class Planner:
        def plan(self, context):
            from src.agent.orchestrators import WorkflowStep
            from src.agent.planning import WorkflowPlan

            return WorkflowPlan(
                workflow_name=context.active_skill,
                steps=[
                    WorkflowStep(
                        "properties",
                        "property_calculator",
                        input_data="CCO",
                    )
                ],
            )

    catalog = WorkflowCatalog(
        (
            WorkflowPolicy(
                name="custom_workflow",
                description="custom",
                allowed_tools=("property_calculator",),
            ),
        )
    )
    runner = ScientificAcceptanceRunner(
        dataset_dir=tmp_path,
        project_root=tmp_path,
        router=Router(catalog),
        planner=Planner(),
        tools={"property_calculator": Tool()},
        harness_factory=CanaryFactory(),
    )
    case = EvaluationCase(
        case_id="CANARY-001",
        version="1",
        category="harness",
        prompt="private prompt CCO",
        expected_skill="custom_workflow",
        expected_tools=["property_calculator"],
        metadata={"idempotency_key": "private-key"},
    )

    report = runner.run_case(case)

    assert "harness_execution" in report, report
    assert report["harness_execution"]["backend"] == "langgraph"
    assert report["harness_execution"]["tool_attempt_count"] == len(
        report["actual_tools"]
    )
    serialized = json.dumps(report)
    assert "private prompt CCO" not in serialized
    assert "private-key" not in serialized


def test_case_literal_redaction_covers_mapping_keys():
    redacted = _redact_case_literals(
        {
            "private prompt CCO": {"private-key": "safe"},
            "nested": ["prefix private prompt CCO suffix"],
        },
        ("private prompt CCO", "private-key"),
    )

    serialized = json.dumps(redacted)
    assert "private prompt CCO" not in serialized
    assert "private-key" not in serialized


@pytest.mark.parametrize(
    ("second_metadata", "expected_error"),
    [
        ("invalid", "invalid_harness_execution_metadata"),
        ("missing", "harness_execution_metadata_missing"),
    ],
)
def test_repeated_harness_metadata_keeps_invalid_run_alignment(
    tmp_path,
    second_metadata,
    expected_error,
):
    class Decision:
        selected_skill = "custom_workflow"

    class Router:
        def __init__(self, catalog):
            self.catalog = catalog

        def decide(self, _prompt):
            return Decision()

    class Tool:
        name = "property_calculator"

        def execute(self, query):
            return ToolResult.success_result(self.name, data={"query": query})

    class Factory:
        last_warning = None

        def __init__(self):
            self.calls = 0

        def create(self, executor):
            legacy = LegacyHarness(executor)
            parent = self

            class Backend:
                def execute(self, **kwargs):
                    parent.calls += 1
                    execution = legacy.execute(**kwargs).authoritative
                    if parent.calls == 2 and second_metadata == "missing":
                        return HarnessRun(authoritative=execution)
                    attempts = 1 if parent.calls == 1 else 99
                    return HarnessRun(
                        authoritative=execution,
                        execution=HarnessExecutionMetadata(
                            backend="langgraph",
                            backend_version="test",
                            selection_reason="canary_selected",
                            canary_bucket=7,
                            plan_fingerprint="a" * 64,
                            tool_attempt_count=attempts,
                            fallback_before_execution=False,
                            elapsed_ms=3,
                        ),
                    )

            return Backend()

    class Planner:
        def plan(self, context):
            from src.agent.orchestrators import WorkflowStep
            from src.agent.planning import WorkflowPlan

            return WorkflowPlan(
                workflow_name=context.active_skill,
                steps=[
                    WorkflowStep(
                        "properties",
                        "property_calculator",
                        input_data="CCO",
                    )
                ],
            )

    catalog = WorkflowCatalog(
        (
            WorkflowPolicy(
                name="custom_workflow",
                description="custom",
                allowed_tools=("property_calculator",),
            ),
        )
    )
    runner = ScientificAcceptanceRunner(
        dataset_dir=tmp_path,
        project_root=tmp_path,
        router=Router(catalog),
        planner=Planner(),
        tools={"property_calculator": Tool()},
        harness_factory=Factory(),
    )
    case = EvaluationCase(
        case_id="CANARY-REPEAT",
        version="1",
        category="harness",
        prompt="properties for CCO",
        expected_skill="custom_workflow",
        expected_tools=["property_calculator", "property_calculator"],
        metadata={"repeat_runs": 2},
    )

    report = runner.run_case(case)

    runs = report["harness_execution"]["runs"]
    assert len(runs) == 2
    assert runs[0]["backend"] == "langgraph"
    assert runs[1] == {
        "status": "invalid",
        "error_code": expected_error,
    }


def test_load_cases_reads_versioned_jsonl(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text(
        json.dumps(
            {
                "case_id": "R-001",
                "version": "1.0",
                "category": "routing",
                "prompt": "分析 CCO 的 ADMET",
                "expected_skill": "admet_assessment",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    cases = load_cases(path)

    assert cases == [
        EvaluationCase(
            case_id="R-001",
            version="1.0",
            category="routing",
            prompt="分析 CCO 的 ADMET",
            expected_skill="admet_assessment",
        )
    ]


def test_real_agent_dataset_contains_all_docx_cases():
    cases = load_cases("data/agent_evals/real_agent_cases.jsonl")

    assert [case.case_id for case in cases] == [
        f"REAL-{index:03d}" for index in range(1, 17)
    ]
    assert all(case.version == "1.0" for case in cases)
    assert cases[4].requires_external == [
        "target_database",
        "ollama",
        "rg_mpnn",
    ]
    assert cases[10].allow_mock is True


def test_golden_scientific_dataset_contains_12_traceable_cases():
    cases = load_cases("data/agent_evals/golden_scientific_cases.jsonl")

    assert [case.case_id for case in cases] == GOLDEN_SCIENTIFIC_CASE_IDS
    assert all(case.version == "1.0" for case in cases)
    assert all(case.allow_mock is False for case in cases)

    required_fields = {
        "real_tool_required",
        "truth_checks",
        "provenance_required",
        "acceptance",
    }
    for case in cases:
        assert case.expected_skill is not None
        assert isinstance(case.expected_tools, list)
        assert isinstance(case.forbidden_patterns, list)
        assert case.scientific_acceptance
        assert required_fields.issubset(case.scientific_acceptance)


def test_diverse_scientific_dataset_contains_20_traceable_cases():
    cases = load_cases("data/agent_evals/diverse_scientific_cases.jsonl")

    assert [case.case_id for case in cases] == [
        f"DIVERSE-{index:03d}" for index in range(1, 21)
    ]
    assert all(case.version == "1.0" for case in cases)
    assert all(case.allow_mock is False for case in cases)

    required_acceptance_fields = {
        "real_tool_required",
        "truth_checks",
        "provenance_required",
        "acceptance",
    }
    for case in cases:
        assert case.category
        assert case.prompt
        assert isinstance(case.expected_tools, list)
        assert isinstance(case.forbidden_tools, list)
        assert isinstance(case.forbidden_patterns, list)
        assert isinstance(case.requires_external, list)
        assert isinstance(case.expected_events, list)
        assert case.pass_criteria
        assert required_acceptance_fields.issubset(case.scientific_acceptance)


def test_generation_data_flow_check_requires_downstream_consumption():
    generated = ToolResult.success_result(
        "llm_molecular_generator",
        data=[{"smiles": "CCO"}, {"smiles": "CCN"}],
    )
    properties = ToolResult.success_result(
        "property_calculator",
        data=[{"smiles": "CCO"}, {"smiles": "CCN"}],
    )

    passed = _check_data_flow_generated_smiles_consumed(
        [generated, properties],
        downstream_tools=["property_calculator"],
    )
    failed = _check_data_flow_generated_smiles_consumed(
        [generated],
        downstream_tools=["property_calculator"],
    )

    assert passed["status"] == "passed"
    assert failed["status"] == "failed"
    assert failed["reason"] == "generated_smiles_not_consumed"


def test_generation_check_accepts_validated_candidate_set_provenance():
    expected_model = "gmm-llama:latest"
    generated = ToolResult.success_result(
        "llm_molecular_generator",
        data=[{"smiles": "CCO"}, {"smiles": "CCN"}],
        quality={"requested_count": 2, "model": expected_model},
        provenance=ToolProvenance(
            tool_name="llm_molecular_generator",
            model_name=expected_model,
            input_digest="input-digest",
        ),
    )
    validated = AgentResultValidator().validate_tool_result(generated)
    validated.provenance = None
    validated.quality = {}

    check = _check_generation(
        [validated],
        requested_count=2,
        expected_model=expected_model,
        strict_count=True,
    )

    assert check["status"] == "passed"
    assert check["models"] == [expected_model]


def test_generation_check_rejects_conflicting_candidate_models():
    expected_model = "gmm-llama:latest"
    generated = ToolResult.success_result(
        "llm_molecular_generator",
        data=[
            {
                "smiles": "CCO",
                "generation_provenance": {"model_name": expected_model},
            },
            {
                "smiles": "CCN",
                "generation_provenance": {"model_name": "conflicting-model"},
            },
        ],
        quality={"model": expected_model},
        provenance=ToolProvenance(
            tool_name="llm_molecular_generator",
            model_name=expected_model,
        ),
    )

    check = _check_generation(
        [generated],
        requested_count=2,
        expected_model=expected_model,
        strict_count=True,
    )

    assert check["status"] == "partial"
    assert check["reason"] == "conflicting_generation_model_provenance"


def test_generation_check_rejects_conflicting_top_level_model():
    expected_model = "gmm-llama:latest"
    generated = ToolResult.success_result(
        "llm_molecular_generator",
        data=[
            {
                "smiles": "CCO",
                "generation_provenance": {"model_name": expected_model},
            }
        ],
        quality={"model": expected_model},
        provenance=ToolProvenance(
            tool_name="llm_molecular_generator",
            model_name="conflicting-model",
        ),
    )

    check = _check_generation(
        [generated],
        requested_count=1,
        expected_model=expected_model,
        strict_count=True,
    )

    assert check["status"] == "partial"
    assert check["reason"] == "conflicting_generation_model_provenance"


def test_generation_check_passes_when_all_model_provenance_agrees():
    expected_model = "gmm-llama:latest"
    generated = ToolResult.success_result(
        "llm_molecular_generator",
        data=[
            {
                "smiles": "CCO",
                "generation_provenance": {"model_name": expected_model},
            }
        ],
        quality={"model": expected_model},
        provenance=ToolProvenance(
            tool_name="llm_molecular_generator",
            model_name=expected_model,
        ),
    )

    check = _check_generation(
        [generated],
        requested_count=1,
        expected_model=expected_model,
        strict_count=True,
    )

    assert check["status"] == "passed"


def test_generation_check_requires_model_provenance():
    generated = ToolResult.success_result(
        "llm_molecular_generator",
        data=[{"smiles": "CCO"}],
    )

    check = _check_generation(
        [generated],
        requested_count=1,
        expected_model="gmm-llama:latest",
        strict_count=True,
    )

    assert check["status"] == "partial"
    assert check["reason"] == "model_provenance_missing"


def test_generation_data_flow_check_accepts_hit_to_lead_candidates_plan():
    class Step:
        def __init__(self, tool_name, output_key=None, input_from=None):
            self.tool_name = tool_name
            self.output_key = output_key
            self.input_from = input_from

    class Plan:
        steps = [
            Step("llm_molecular_generator", output_key="candidates"),
            Step("property_calculator", input_from="candidates"),
        ]

    class Result:
        tool_results = [
            ToolResult.success_result("llm_molecular_generator", data=[{"smiles": "c1ccccc1O"}]),
            ToolResult.success_result(
                "property_calculator",
                data=[{"smiles": "Oc1ccccc1", "molecular_weight": 94.11}],
            ),
        ]

    class Execution:
        plan = Plan()
        result = Result()

    result = _check_data_flow_generated_smiles_consumed(
        Result.tool_results,
        downstream_tools=["property_calculator"],
        executions=[Execution()],
    )

    assert result["status"] == "passed"


def test_generation_repeat_stats_check_requires_each_requested_round():
    repeated = ToolResult.success_result(
        "llm_molecular_generator",
        data={
            "repeat_runs": [
                [{"smiles": "CCO"}, {"smiles": "CCN"}],
                [{"smiles": "CCC"}, {"smiles": "CCCl"}],
            ]
        },
    )

    result = _check_generation_repeat_stats(
        [repeated],
        repeat_runs=2,
        requested_count=2,
        expected_model="gmm-llama:latest",
    )

    assert result["status"] == "passed"
    assert result["runs"][0]["valid_count"] == 2
    assert result["runs"][1]["unique_count"] == 2


def test_rag_truth_check_allows_sources_or_explicit_no_hit():
    with_source = ToolResult.success_result(
        "rag_search",
        data=[{"source": "docs/vina.md", "content": "box center"}],
        evidence=[{"source": "docs/vina.md"}],
    )
    explicit_no_hit = ToolResult.success_result(
        "rag_search",
        data=[],
        message="No matching sources found in local knowledge base.",
    )
    fabricated = ToolResult.success_result(
        "rag_search",
        data=[],
        message="Here is the answer with no source.",
    )

    assert _check_rag_source_or_explicit_no_hit([with_source])["status"] == "passed"
    assert _check_rag_source_or_explicit_no_hit([explicit_no_hit])["status"] == "passed"
    assert _check_rag_source_or_explicit_no_hit([fabricated])["status"] == "failed"


def test_replay_scientific_report_checks_truth_provenance_and_forbidden_patterns():
    report = {
        "mode": "real",
        "status": "passed",
        "real_cases": {
            "case_count": 1,
            "results": [
                {
                    "case_id": "GOLD-001",
                    "status": "passed",
                    "expected_skill": "admet_assessment",
                    "actual_skill": "admet_assessment",
                    "expected_tools": ["property_calculator"],
                    "actual_tools": ["property_calculator"],
                    "forbidden_tools": [],
                    "anti_hallucination": {
                        "status": "passed",
                        "forbidden_found": [],
                    },
                    "truth_checks": {
                        "rdkit": {"status": "passed"},
                    },
                    "tool_provenance": [
                        {
                            "tool_name": "property_calculator",
                            "trace_id": "gold-001-trace",
                            "input_hash": "a" * 64,
                            "input_summary": "aspirin",
                            "output_summary": "mw/logp/qed",
                        }
                    ],
                }
            ],
        },
    }

    replayed = replay_scientific_report(report)

    assert replayed["mode"] == "replay"
    assert replayed["status"] == "passed"
    assert replayed["metrics"]["case_count"] == 1

    report["real_cases"]["results"][0]["anti_hallucination"]["forbidden_found"] = [
        "kcal/mol"
    ]
    replayed = replay_scientific_report(report)

    assert replayed["status"] == "failed"
    assert replayed["results"][0]["status"] == "failed"


def test_replay_allows_no_tool_general_chat_case_without_truth_checks():
    report = {
        "mode": "real",
        "status": "passed",
        "real_cases": {
            "results": [
                {
                    "case_id": "DIVERSE-015",
                    "status": "passed",
                    "expected_skill": None,
                    "actual_skill": None,
                    "expected_tools": [],
                    "actual_tools": [],
                    "forbidden_tools": [],
                    "anti_hallucination": {
                        "status": "passed",
                        "forbidden_found": [],
                    },
                    "truth_checks": {},
                    "tool_provenance": [],
                }
            ],
        },
    }

    replayed = replay_scientific_report(report)

    assert replayed["status"] == "passed"
    assert replayed["results"][0]["status"] == "passed"


def test_scientific_stability_summary_reports_latency_and_failure_types():
    iterations = [
        {
            "results": [
                {"case_id": "GOLD-001", "status": "passed", "latency_ms": 100},
                {"case_id": "GOLD-002", "status": "failed", "latency_ms": 300, "error": "rg_mpnn_demo"},
            ]
        },
        {
            "results": [
                {"case_id": "GOLD-001", "status": "partial", "latency_ms": 200, "error": "vina_missing"},
                {"case_id": "GOLD-002", "status": "passed", "latency_ms": 400},
            ]
        },
    ]

    stability = summarize_scientific_stability(iterations)

    assert stability["run_count"] == 2
    assert stability["case_count"] == 4
    assert stability["pass_rate"] == 0.75
    assert stability["completion_rate"] == 0.75
    assert stability["passed_rate"] == 0.5
    assert stability["partial_rate"] == 0.25
    assert stability["failed_rate"] == 0.25
    assert stability["skipped_rate"] == 0.0
    assert stability["latency"]["p50_ms"] == 250
    assert stability["failure_types"] == {"rg_mpnn_demo": 1, "vina_missing": 1}


def test_replay_never_promotes_original_failure_to_passed():
    result = _complete_replay_case(status="failed")

    replayed = replay_scientific_report(
        {"real_cases": {"results": [result]}}
    )

    assert replayed["results"][0]["status"] == "failed"
    assert "original_status_failed" in replayed["results"][0]["replay_reasons"]


def test_replay_fails_skill_tool_and_forbidden_tool_contract_violations():
    skill_mismatch = _complete_replay_case(actual_skill="activity_prediction")
    missing_expected = _complete_replay_case(actual_tools=[])
    forbidden_called = _complete_replay_case(
        actual_tools=["property_calculator", "molecular_docking"],
        forbidden_tools=["molecular_docking"],
    )

    replayed = replay_scientific_report(
        {
            "real_cases": {
                "results": [skill_mismatch, missing_expected, forbidden_called]
            }
        }
    )

    reasons = [item["replay_reasons"] for item in replayed["results"]]
    assert "skill_mismatch" in reasons[0]
    assert "expected_tools_missing_or_out_of_order:property_calculator" in reasons[1]
    assert "forbidden_tools_called:molecular_docking" in reasons[2]
    assert replayed["metrics"]["failed_count"] == 3


def test_replay_reports_strict_status_rates_separately_from_completion_rate():
    results = [
        _complete_replay_case(case_id="R-1", status="passed"),
        _complete_replay_case(case_id="R-2", status="partial"),
        _complete_replay_case(case_id="R-3", status="failed"),
        _complete_replay_case(case_id="R-4", status="skipped"),
    ]

    replayed = replay_scientific_report({"real_cases": {"results": results}})

    assert replayed["metrics"]["pass_rate"] == 0.5
    assert replayed["metrics"]["completion_rate"] == 0.5
    assert replayed["metrics"]["passed_rate"] == 0.25
    assert replayed["metrics"]["partial_rate"] == 0.25
    assert replayed["metrics"]["failed_rate"] == 0.25
    assert replayed["metrics"]["skipped_rate"] == 0.25


def _complete_replay_case(**overrides):
    case_id = overrides.pop("case_id", "GOLD-001")
    result = {
        "case_id": case_id,
        "status": "passed",
        "expected_skill": "admet_assessment",
        "actual_skill": "admet_assessment",
        "expected_tools": ["property_calculator"],
        "actual_tools": ["property_calculator"],
        "forbidden_tools": [],
        "anti_hallucination": {"status": "passed", "forbidden_found": []},
        "truth_checks": {"rdkit": {"status": "passed"}},
        "tool_provenance": [
            {
                "tool_name": "property_calculator",
                "trace_id": f"{case_id}-trace",
                "input_hash": "b" * 64,
                "input_summary": "input",
                "output_summary": "output",
            }
        ],
    }
    result.update(overrides)
    return result


def test_scientific_vina_energy_parser_reads_best_pose_and_results():
    assert (
        _extract_binding_energy(
            {
                "best_pose": {"binding_energy": -3.6},
                "results": [{"binding_energy": -3.7}],
            }
        )
        == -3.6
    )


def test_routing_evaluation_reports_top1_top3_and_abstention():
    cases = [
        EvaluationCase(
            case_id="R-001",
            version="1",
            category="routing",
            prompt="预测 CCO 的 pIC50",
            expected_skill="activity_prediction",
        ),
        EvaluationCase(
            case_id="R-002",
            version="1",
            category="routing",
            prompt="你好，今天心情不错。",
            expected_skill=None,
        ),
    ]

    report = EvaluationRunner(router=HybridSkillRouter()).evaluate_routing(cases)

    assert report.metrics["top1_accuracy"] == 1.0
    assert report.metrics["top3_accuracy"] == 1.0
    assert report.metrics["correct_abstention_rate"] == 1.0
    assert all(result.score == 10 for result in report.results)


def test_workflow_score_penalizes_order_and_fabrication():
    perfect = score_workflow(
        expected_tools=["property", "admet", "activity"],
        actual_tools=["property", "admet", "activity"],
        forbidden_tools=[],
        fabricated=False,
        structured_output=True,
    )
    unsafe = score_workflow(
        expected_tools=["property", "admet", "activity"],
        actual_tools=["activity", "property"],
        forbidden_tools=["docking"],
        fabricated=True,
        structured_output=False,
    )

    assert perfect["score"] == 10
    assert unsafe["score"] < 5
    assert unsafe["anti_hallucination"] == 0


def test_chemistry_metrics_measure_validity_uniqueness_and_count():
    metrics = chemistry_metrics(["CCO", "CCO", "not-a-smiles"], requested_count=3)

    assert metrics["valid_smiles_rate"] == 2 / 3
    assert metrics["unique_valid_smiles"] == 1
    assert metrics["requested_count_compliance"] is True


def test_latency_metrics_return_p50_and_p95():
    metrics = latency_metrics([10, 20, 30, 40, 50])

    assert metrics["p50_ms"] == 30
    assert metrics["p95_ms"] >= 40


def test_report_serialization_redacts_secrets(tmp_path):
    report = EvaluationRunner(router=HybridSkillRouter()).evaluate_routing(
        [
            EvaluationCase(
                case_id="R-001",
                version="1",
                category="routing",
                prompt="分析 CCO 的 ADMET",
                expected_skill="admet_assessment",
                metadata={"api_key": "secret-value"},
            )
        ]
    )
    path = tmp_path / "report.json"
    report.write_json(path)

    content = path.read_text(encoding="utf-8")
    assert "secret-value" not in content
    assert "[REDACTED]" in content
