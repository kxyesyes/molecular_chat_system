from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts.run_agent_acceptance import (
    _test_external_model,
    _run_harness_contract_probe,
    _evaluate_activity_result,
    _evaluate_docking_result,
    _evaluate_generation_result,
    _resolve_real_dataset,
    run_contract,
    run_replay,
    run_real,
)
from src.agent.harness import HarnessRun, LegacyHarness, ShadowComparison
from src.agent.evaluation import scientific
from src.agent.evaluation.scientific import (
    check_data_flow_evidence,
    check_generated_candidate_truth,
    check_scientific_claim_evidence,
)
from src.agent.evaluation.models import EvaluationCase
from src.agent.contracts import AgentResult, ToolResult
from src.agent.orchestrators import WorkflowStep
from src.agent.planning import WorkflowPlan


def test_generation_truth_check_rejects_invalid_or_duplicate_candidates():
    check = check_generated_candidate_truth(
        {
            "tool_results": [
                {
                    "tool_name": "llm_molecular_generator",
                    "success": True,
                    "data": [{"smiles": "CCO"}, {"smiles": "OCC"}],
                    "quality": {
                        "requested_count": 2,
                        "valid_count": 2,
                        "unique_count": 1,
                        "validation_method": "RDKit",
                    },
                }
            ]
        }
    )
    assert check["passed"] is False
    assert check["reason"] == "generated_candidates_not_unique"


def test_real_acceptance_tool_registry_includes_candidate_ranker(tmp_path):
    runner = scientific.ScientificAcceptanceRunner(
        dataset_dir=tmp_path,
        project_root=tmp_path,
    )

    assert "candidate_ranker" in runner.tools
    assert runner.tools["candidate_ranker"].name == "candidate_ranker"


def test_execution_exception_takes_precedence_over_dependent_truth_failures():
    case = EvaluationCase(
        case_id="EXECUTION-ERROR-001",
        version="1",
        category="target_design",
        prompt="Design candidates",
        expected_skill="target_driven_design",
        expected_tools=["target_database_search"],
    )

    status, error = scientific._case_status(
        case=case,
        actual_skill="target_driven_design",
        actual_tools=[],
        anti_hallucination={"status": "passed", "forbidden_found": []},
        truth_checks={
            "target_database_evidence": {
                "status": "failed",
                "reason": "tool_not_run:target_database_search",
            }
        },
        execution_success=False,
        execution_error="execution_exception:KeyError",
    )

    assert status == "failed"
    assert error == "execution_exception:KeyError"


def test_contract_harness_probe_records_shadow_without_double_execution():
    class Factory:
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
                            elapsed_ms=2,
                        ),
                    )

            return Backend()

    probe = _run_harness_contract_probe(Factory())

    assert probe["status"] == "matched"
    assert probe["matched"] is True
    assert probe["authoritative_tool_calls"] == 1


def test_contract_canary_probe_keeps_case_count_and_one_tool_call(monkeypatch):
    monkeypatch.setenv("AGENT_HARNESS_MODE", "langgraph_canary")
    monkeypatch.setenv("AGENT_LANGGRAPH_CANARY_PERCENT", "100")

    report = run_contract()

    assert report["metrics"]["case_count"] == 34
    assert report["harness_canary"]["authoritative_tool_calls"] == 1
    assert report["harness_canary"]["tool_attempt_count"] == 1
    assert report["harness_canary"]["backend"] == "langgraph"


def test_failed_canary_probe_fails_contract_release_gate(monkeypatch):
    monkeypatch.setattr(
        "scripts.run_agent_acceptance._run_harness_contract_probe",
        lambda: {
            "status": "failed",
            "error_code": "invalid_harness_execution_metadata",
            "authoritative_tool_calls": 1,
        },
    )

    report = run_contract()

    assert report["status"] == "failed"
    assert report["harness_shadow"]["status"] == "failed"


def test_unavailable_requested_canary_fails_contract_release_gate(monkeypatch):
    monkeypatch.setenv("AGENT_HARNESS_MODE", "langgraph_canary")
    monkeypatch.setattr(
        "scripts.run_agent_acceptance._run_harness_contract_probe",
        lambda: {
            "status": "unavailable",
            "error_code": "langgraph_optional_dependency_unavailable",
            "authoritative_tool_calls": 1,
        },
    )

    report = run_contract()

    assert report["status"] == "failed"
    assert report["harness_shadow"]["status"] == "unavailable"


def test_external_model_probe_allows_reasoning_model_output_budget(monkeypatch):
    captured = {}

    class Model:
        def __init__(self, **_kwargs):
            pass

        async def generate(self, _prompt, *, temperature, max_tokens):
            captured["temperature"] = temperature
            captured["max_tokens"] = max_tokens
            return "AGENT_ACCEPTANCE_OK" if max_tokens >= 256 else "budget_exhausted"

    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "runtime-test-key")
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("OPENAI_COMPATIBLE_MODEL", "reasoning-model")
    monkeypatch.setattr(
        "scripts.run_agent_acceptance.OpenAICompatibleModel",
        Model,
    )

    result = __import__("asyncio").run(_test_external_model())

    assert result["status"] == "passed"
    assert result["response_matched"] is True
    assert captured == {"temperature": 0.0, "max_tokens": 256}


def test_claim_truth_check_rejects_claim_without_evidence():
    check = check_scientific_claim_evidence(
        {"claims": [{"claim_id": "pic50", "value": 7.2, "evidence_ids": []}]}
    )
    assert check == {"passed": False, "reason": "claim_without_evidence"}


def test_data_flow_check_requires_bound_generation_output():
    check = check_data_flow_evidence(
        {
            "plan": {
                "steps": [
                    {"step_id": "generate", "output_key": "molecules"},
                    {
                        "step_id": "properties",
                        "input_binding": "$.request.query",
                    },
                ]
            }
        }
    )
    assert check["passed"] is False
    assert check["reason"] == "generated_output_not_consumed"


def test_generation_check_requires_valid_unique_requested_count() -> None:
    result = {
        "success": True,
        "data": [
            {"smiles": "CCO", "model": "gmm-llama:latest"},
            {"smiles": "CCO", "model": "gmm-llama:latest"},
            {"smiles": "CCN", "model": "gmm-llama:latest"},
        ],
    }

    summary = _evaluate_generation_result(
        result,
        requested_count=3,
        expected_model="gmm-llama:latest",
    )

    assert summary["status"] == "failed"
    assert summary["valid_count"] == 3
    assert summary["unique_count"] == 2


def test_scientific_case_score_reports_all_seven_dimensions() -> None:
    case = EvaluationCase(
        case_id="SCORE-001",
        version="1",
        category="workflow",
        prompt="analyze",
        expected_skill="comprehensive_evaluation",
        expected_tools=["property_calculator", "admet_predictor"],
        forbidden_tools=["molecular_docking"],
        expected_events=[
            "task_started",
            "planning_started",
            "planning_completed",
            "tool_started",
            "tool_completed",
            "task_completed",
        ],
        scientific_acceptance={
            "provenance_required": ["property_calculator", "admet_predictor"],
            "truth_checks": ["rdkit_properties", "data_flow_generated_smiles_consumed"],
        },
    )

    scored = scientific._score_scientific_case(
        case=case,
        actual_skill="comprehensive_evaluation",
        actual_tools=["property_calculator", "admet_predictor"],
        events=[
            {"event": "task_started", "trace_id": "score-trace"},
            {"event": "planning_started", "trace_id": "score-trace"},
            {"event": "planning_completed", "trace_id": "score-trace"},
            {"event": "tool_started", "trace_id": "score-trace"},
            {"event": "tool_completed", "trace_id": "score-trace"},
            {"event": "task_completed", "trace_id": "score-trace"},
        ],
        tool_provenance=[
            {
                "tool_name": "property_calculator",
                "success": True,
                "trace_id": "score-trace",
                "input_hash": "a" * 64,
                "input_summary": "aspirin",
                "output_summary": "properties",
            },
            {
                "tool_name": "admet_predictor",
                "success": True,
                "trace_id": "score-trace",
                "input_hash": "b" * 64,
                "input_summary": "aspirin",
                "output_summary": "admet",
            },
        ],
        anti_hallucination={"status": "passed", "forbidden_found": []},
        truth_checks={
            "rdkit_properties": {"status": "passed"},
            "data_flow_generated_smiles_consumed": {"status": "passed"},
        },
        case_status="passed",
        case_error=None,
    )

    assert scored["score"] == 100
    assert scored["score_breakdown"] == {
        "routing": 15,
        "tool_selection": 15,
        "workflow_order": 15,
        "real_tool_execution": 20,
        "upstream_data_consumption": 15,
        "anti_hallucination": 10,
        "structured_provenance": 10,
    }
    assert scored["hard_failure"] is False


def test_structured_provenance_score_requires_input_hash_summaries_and_event_trace() -> None:
    case = EvaluationCase(
        case_id="PROV-001",
        version="1",
        category="properties",
        prompt="calculate",
        expected_skill="admet_assessment",
        expected_tools=["property_calculator"],
        expected_events=["task_started", "tool_completed", "task_completed"],
        scientific_acceptance={"provenance_required": ["property_calculator"]},
    )

    scored = scientific._score_scientific_case(
        case=case,
        actual_skill="admet_assessment",
        actual_tools=["property_calculator"],
        events=[
            {"event": "task_started", "trace_id": "trace"},
            {"event": "tool_completed", "trace_id": "trace"},
            {"event": "task_completed", "trace_id": "trace"},
        ],
        tool_provenance=[
            {
                "tool_name": "property_calculator",
                "success": True,
                "input_summary": "",
                "input_hash": "",
                "output_summary": "properties",
            }
        ],
        anti_hallucination={"status": "passed", "forbidden_found": []},
        truth_checks={},
        case_status="passed",
        case_error=None,
    )

    assert scored["score_breakdown"]["structured_provenance"] == 0


def _wired_generation_execution(tool_results):
    return SimpleNamespace(
        plan=WorkflowPlan(
            workflow_name="generation-dataflow",
            steps=[
                WorkflowStep(
                    "generate",
                    "llm_molecular_generator",
                    output_key="molecules",
                ),
                WorkflowStep(
                    "properties",
                    "property_calculator",
                    input_from="molecules",
                    output_key="properties",
                ),
            ],
        ),
        result=AgentResult(
            trace_id="dataflow-trace",
            success=True,
            message="done",
            tool_results=tool_results,
        ),
    )


def test_generated_smiles_dataflow_requires_observed_canonical_intersection() -> None:
    generator = ToolResult.success_result(
        "llm_molecular_generator",
        data=[{"smiles": "OCC"}],
    )
    downstream = ToolResult.success_result(
        "property_calculator",
        data=[{"smiles": "CCO", "molecular_weight": 46.07}],
    )
    execution = _wired_generation_execution([generator, downstream])

    check = scientific._check_data_flow_generated_smiles_consumed(
        [generator, downstream],
        downstream_tools=["property_calculator"],
        executions=[execution],
    )

    assert check["status"] == "passed"
    assert check["consumed_by"] == ["property_calculator"]


def test_plan_wiring_cannot_pass_when_downstream_output_lacks_generated_smiles() -> None:
    generator = ToolResult.success_result(
        "llm_molecular_generator",
        data=[{"smiles": "CCO"}],
    )
    downstream = ToolResult.success_result(
        "property_calculator",
        data=[{"smiles": "CCC", "molecular_weight": 44.1}],
    )
    execution = _wired_generation_execution([generator, downstream])

    check = scientific._check_data_flow_generated_smiles_consumed(
        [generator, downstream],
        downstream_tools=["property_calculator"],
        executions=[execution],
    )

    assert check["status"] == "failed"
    assert check["reason"] == "generated_smiles_not_consumed"
    assert check["plan_wiring"] == "molecules->property_calculator"


def test_tool_provenance_uses_persisted_execution_input_and_hash() -> None:
    result = ToolResult.success_result(
        "property_calculator",
        data=[{"smiles": "CCO", "molecular_weight": 46.07}],
    )
    provenance = scientific._tool_provenance(
        result,
        Path.cwd(),
        execution_record={
            "trace_id": "trace-1",
            "step_id": "properties",
            "input": [{"smiles": "CCO"}],
            "input_hash": "c" * 64,
        },
    )

    assert provenance["trace_id"] == "trace-1"
    assert provenance["step_id"] == "properties"
    assert provenance["input_hash"] == "c" * 64
    assert provenance["input_summary"]
    assert provenance["output_summary"]


def test_scientific_runner_builds_report_provenance_from_persisted_execution(
    tmp_path,
) -> None:
    class FakeRouter:
        def decide(self, _prompt):
            return SimpleNamespace(selected_skill="admet_assessment")

    class FakePlanner:
        def plan(self, _context):
            return WorkflowPlan(
                workflow_name="properties-only",
                steps=[
                    WorkflowStep(
                        "properties",
                        "property_calculator",
                        input_data={"smiles": "CCO"},
                        output_key="properties",
                    )
                ],
            )

    class FakePropertyTool:
        name = "property_calculator"

        def execute(self, input_data):
            assert input_data == {"smiles": "CCO"}
            return {
                "success": True,
                "message": "calculated",
                "data": [{"smiles": "CCO", "molecular_weight": 46.07}],
            }

    case = EvaluationCase(
        case_id="PROV-RUN-001",
        version="1",
        category="properties",
        prompt="calculate CCO",
        expected_skill="admet_assessment",
        expected_tools=["property_calculator"],
        expected_events=[
            "task_started",
            "planning_started",
            "planning_completed",
            "tool_started",
            "tool_completed",
            "task_completed",
        ],
        scientific_acceptance={"provenance_required": ["property_calculator"]},
    )
    runner = scientific.ScientificAcceptanceRunner(
        dataset_dir=tmp_path,
        project_root=tmp_path,
        router=FakeRouter(),
        planner=FakePlanner(),
        tools={"property_calculator": FakePropertyTool()},
    )

    report = runner.run_case(case)

    assert report["status"] == "passed"
    provenance = report["tool_provenance"][0]
    assert provenance["trace_id"].startswith("PROV-RUN-001-")
    assert len(provenance["input_hash"]) == 64
    assert provenance["input_summary"]
    assert provenance["output_summary"]
    assert report["score_breakdown"]["structured_provenance"] == 10


def test_target_database_truth_check_rejects_structure_counts_without_records() -> None:
    result = ToolResult.success_result(
        "target_database_search",
        data=[
            {
                "gene_symbol": "EGFR",
                "uniprot_id": "P00533",
                "structure_count": 3,
                "has_experimental_structure": True,
            }
        ],
    )

    check = scientific._check_target_database_evidence([result])

    assert check == {
        "status": "failed",
        "reason": "target_structure_counts_without_recommended_records",
    }


def test_target_database_truth_check_rejects_source_label_without_identity_evidence() -> None:
    result = ToolResult.success_result(
        "target_database_search",
        data=[{"source": "UniProt"}],
    )

    check = scientific._check_target_database_evidence([result])

    assert check == {
        "status": "failed",
        "reason": "target_records_without_evidence",
    }


def test_target_database_truth_check_accepts_source_backed_pde5a_structure() -> None:
    result = ToolResult.success_result(
        "target_database_search",
        data=[
            {
                "gene_symbol": "PDE5A",
                "uniprot_id": "O76074",
                "source": "UniProt",
                "source_url": "https://www.uniprot.org/uniprotkb/O76074/entry",
                "target_stale": False,
                "recommended_structures": [
                    {
                        "structure_id": "1T9S",
                        "source": "RCSB_PDB",
                        "source_url": "https://www.rcsb.org/structure/1T9S",
                    }
                ],
            }
        ],
    )

    check = scientific._check_target_database_evidence([result])

    assert check["status"] == "passed"
    assert check["evidence_count"] == 1
    assert check["recommended_structure_count"] == 1


def test_target_design_truth_check_rejects_generation_after_empty_target_evidence():
    check = scientific._check_target_driven_generation_gate(
        tool_results=[
            ToolResult.success_result("target_database_search", data=[]),
            ToolResult.success_result(
                "llm_molecular_generator",
                data={"candidates": []},
            ),
        ],
        skipped_steps=[],
    )

    assert check == {
        "status": "failed",
        "reason": "generation_ran_without_usable_target_evidence",
    }


def test_target_design_truth_check_accepts_documented_precondition_skip():
    check = scientific._check_target_driven_generation_gate(
        tool_results=[ToolResult.success_result("target_database_search", data=[])],
        skipped_steps=[
            {
                "step_id": "molecule_generation",
                "status": "skipped_precondition",
                "requirement": "target_evidence",
            }
        ],
    )

    assert check["status"] == "passed"
    assert check["reason"] == "generation_blocked_without_target_evidence"


def test_safe_target_gate_marks_dependent_truth_checks_skipped_not_failed():
    checks = {
        "target_database_evidence": {"status": "partial", "reason": "no records"},
        "generation_count_valid_unique": {
            "status": "failed",
            "reason": "tool_not_run:llm_molecular_generator",
        },
        "admet_method": {
            "status": "failed",
            "reason": "tool_not_run:admet_predictor",
        },
        "rg_mpnn_provenance": {
            "status": "failed",
            "reason": "tool_not_run:activity_predictor",
        },
    }

    scientific._mark_safe_gate_dependents_skipped(checks)

    assert checks["target_database_evidence"]["status"] == "partial"
    assert checks["generation_count_valid_unique"] == {
        "status": "skipped",
        "reason": "blocked_by_target_evidence_gate",
    }
    assert checks["admet_method"]["status"] == "skipped"
    assert checks["rg_mpnn_provenance"]["status"] == "skipped"


def test_candidate_identity_truth_check_requires_exact_downstream_subset():
    generated = ToolResult.success_result(
        "llm_molecular_generator",
        data={
            "requested_count": 2,
            "candidates": [
                {"candidate_id": "cand-001-a", "smiles": "CCO"},
                {"candidate_id": "cand-002-b", "smiles": "CCN"},
            ],
        },
    )
    downstream = ToolResult.success_result(
        "property_calculator",
        data=[
            {"candidate_id": "cand-001-a", "smiles": "CCO"},
            {"candidate_id": "unknown", "smiles": "CCC"},
        ],
    )

    check = scientific._check_candidate_identity_closed_loop(
        [generated, downstream]
    )

    assert check["status"] == "failed"
    assert check["reason"] == "unknown_downstream_candidate_id"


def test_candidate_identity_truth_check_marks_missing_required_properties_partial():
    generated = ToolResult.success_result(
        "llm_molecular_generator",
        data={
            "candidates": [
                {"candidate_id": "cand-001-a", "smiles": "CCO"},
                {"candidate_id": "cand-002-b", "smiles": "CCN"},
            ]
        },
    )
    properties = ToolResult.success_result(
        "property_calculator",
        data=[{"candidate_id": "cand-001-a", "smiles": "CCO"}],
    )

    check = scientific._check_candidate_identity_closed_loop(
        [generated, properties]
    )

    assert check["status"] == "partial"
    assert check["reason"] == "required_candidate_properties_incomplete"
    assert check["missing_candidate_ids"] == ["cand-002-b"]


def test_candidate_identity_truth_check_rejects_smiles_reassigned_to_known_id():
    generated = ToolResult.success_result(
        "llm_molecular_generator",
        data={
            "candidates": [
                {
                    "candidate_id": "cand-001-a",
                    "smiles": "CCO",
                    "canonical_smiles": "CCO",
                }
            ]
        },
    )
    properties = ToolResult.success_result(
        "property_calculator",
        data=[{"candidate_id": "cand-001-a", "smiles": "CCN"}],
    )

    check = scientific._check_candidate_identity_closed_loop(
        [generated, properties]
    )

    assert check["status"] == "failed"
    assert check["reason"] == "downstream_candidate_smiles_mismatch"
    assert check["tool_name"] == "property_calculator"


def test_target_design_runner_registers_automatic_gates_and_skips_generator(tmp_path):
    class Router:
        def decide(self, _prompt):
            return SimpleNamespace(selected_skill="target_driven_design")

    class Planner:
        def plan(self, _context):
            return WorkflowPlan(
                workflow_name="target_driven_design",
                steps=[
                    WorkflowStep(
                        "target_search",
                        "target_database_search",
                        input_data="PDE5A",
                        output_key="target_info",
                    ),
                    WorkflowStep(
                        "molecule_generation",
                        "llm_molecular_generator",
                        input_from="target_info",
                        input_binding="$.outputs.target_info",
                        output_key="candidates",
                        preconditions=("target_evidence",),
                    ),
                ],
            )

    class TargetTool:
        name = "target_database_search"

        def execute(self, _query):
            return ToolResult.success_result(self.name, data=[])

    class GeneratorTool:
        name = "llm_molecular_generator"

        def __init__(self):
            self.calls = 0

        def execute(self, _query):
            self.calls += 1
            return ToolResult.success_result(self.name, data={"candidates": []})

    generator = GeneratorTool()
    runner = scientific.ScientificAcceptanceRunner(
        dataset_dir=tmp_path,
        project_root=tmp_path,
        router=Router(),
        planner=Planner(),
        tools={
            "target_database_search": TargetTool(),
            "llm_molecular_generator": generator,
        },
    )
    case = EvaluationCase(
        case_id="AUTO-GATE-001",
        version="1",
        category="target_design",
        prompt="Design PDE5A candidates",
        expected_skill="target_driven_design",
        expected_tools=["target_database_search"],
    )

    report = runner.run_case(case)

    assert generator.calls == 0
    assert report["automatic_truth_checks"] == [
        "target_driven_generation_gate",
        "candidate_identity_closed_loop",
    ]
    assert report["truth_checks"]["target_driven_generation_gate"]["status"] == (
        "passed"
    )
    assert report["truth_checks"]["candidate_identity_closed_loop"]["status"] == (
        "passed"
    )


def test_admet_truth_check_requires_adme_py_package_version() -> None:
    result = ToolResult.success_result(
        "admet_predictor",
        data=[
            {
                "smiles": "CCO",
                "admet": {
                    "prediction_method": "adme_py",
                    "backend_version": "unknown",
                },
            }
        ],
    )

    check = scientific._check_admet_method([result])

    assert check == {
        "status": "partial",
        "reason": "adme_py_package_version_unavailable",
    }


def test_reverse_target_dataflow_requires_observed_target_intersection() -> None:
    reverse = ToolResult.success_result(
        "reverse_target_predictor",
        data=[
            {
                "target_identifier": "CHEMBL203",
                "target_name": "Epidermal growth factor receptor",
                "final_similarity": 0.8,
            }
        ],
    )
    target_search = ToolResult.success_result(
        "target_database_search",
        data=[
            {
                "source_query": "Epidermal growth factor receptor",
                "gene_symbol": "EGFR",
                "uniprot_id": "P00533",
                "recommended_structures": [{"structure_id": "4WKQ"}],
            }
        ],
    )

    passed = scientific._check_data_flow_reverse_targets_consumed(
        [reverse, target_search]
    )
    assert passed["status"] == "passed"
    assert passed["consumed_target_identifiers"]

    target_search.data[0]["source_query"] = "PDE5A"
    target_search.data[0]["gene_symbol"] = "PDE5A"
    target_search.data[0]["uniprot_id"] = "O76074"
    failed = scientific._check_data_flow_reverse_targets_consumed(
        [reverse, target_search]
    )
    assert failed["status"] == "failed"
    assert failed["reason"] == "reverse_targets_not_consumed"


def test_scientific_case_score_zeros_hard_hallucination_failure() -> None:
    case = EvaluationCase(
        case_id="SCORE-002",
        version="1",
        category="docking",
        prompt="give binding energy without inputs",
        expected_skill="docking_simulation",
        expected_tools=[],
        forbidden_patterns=["kcal/mol"],
    )

    scored = scientific._score_scientific_case(
        case=case,
        actual_skill="docking_simulation",
        actual_tools=[],
        events=[],
        tool_provenance=[],
        anti_hallucination={
            "status": "failed",
            "forbidden_found": ["kcal/mol"],
        },
        truth_checks={},
        case_status="failed",
        case_error="forbidden_patterns_found",
    )

    assert scored["score"] == 0
    assert scored["hard_failure"] is True
    assert scored["hard_failure_reasons"] == ["forbidden_patterns_found"]


def test_activity_check_rejects_simulated_demo_predictions() -> None:
    summary = _evaluate_activity_result(
        predictor_demo_mode=True,
        model_path=None,
        predictions=[
            {
                "smiles": "CCO",
                "success": True,
                "activity_score": 6.2,
                "note": "Demo Mode (Simulated)",
            }
        ],
    )

    assert summary["status"] == "failed"
    assert summary["real_model_used"] is False


def test_docking_check_requires_actual_pose_and_binding_energy(tmp_path) -> None:
    pose_file = tmp_path / "result.pdbqt"
    pose_file.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
    summary = _evaluate_docking_result(
        {
            "success": True,
            "data": {
                "job_id": "vina-real",
                "total_poses": 2,
                "best_pose": {
                    "binding_energy": -6.7,
                    "pose_file": str(pose_file),
                },
            },
        }
    )

    assert summary == {
        "status": "passed",
        "job_id": "vina-real",
        "pose_count": 2,
        "best_binding_energy": -6.7,
        "pose_file": str(pose_file),
        "pose_file_exists": True,
    }


def test_replay_mode_reads_existing_structured_report_without_running_tools(tmp_path) -> None:
    report_path = tmp_path / "agent_report.json"
    report_path.write_text(
        """
{
  "requested_mode": "real",
  "reports": [
    {
      "mode": "real",
      "status": "passed",
      "real_cases": {
        "results": [
          {
            "case_id": "GOLD-001",
            "status": "passed",
            "anti_hallucination": {"status": "passed", "forbidden_found": []},
            "truth_checks": {"rdkit": {"status": "passed"}},
            "tool_provenance": [
              {"tool_name": "property_calculator", "input_summary": "aspirin", "output_summary": "props"}
            ]
          }
        ]
      }
    }
  ]
}
""",
        encoding="utf-8",
    )

    replay = run_replay(report_path)

    assert replay["mode"] == "replay"
    assert replay["status"] == "passed"
    assert replay["metrics"]["case_count"] == 1


def test_real_mode_resolves_diverse_case_set_without_touching_golden_dataset() -> None:
    dataset = _resolve_real_dataset(case_set="diverse", dataset=None)

    assert dataset.name == "diverse_scientific_cases.jsonl"


def test_run_real_accepts_case_set_argument(monkeypatch) -> None:
    captured = {}

    async def fake_external_model():
        return {"status": "skipped"}

    class FakeScientificRunner:
        def __init__(self, dataset_dir, project_root, dataset_path=None):
            captured["dataset_path"] = dataset_path

        def run(self, repeat=1):
            return {
                "status": "passed",
                "case_count": 0,
                "results": [],
                "stability": {"run_count": repeat},
            }

    monkeypatch.setattr(
        "scripts.run_agent_acceptance.ScientificAcceptanceRunner",
        FakeScientificRunner,
    )
    monkeypatch.setattr(
        "scripts.run_agent_acceptance._test_external_model",
        fake_external_model,
    )
    monkeypatch.setattr(
        "scripts.run_agent_acceptance._test_ollama",
        lambda: {"status": "passed"},
    )
    monkeypatch.setattr(
        "scripts.run_agent_acceptance._test_target_search",
        lambda: {"status": "passed"},
    )
    monkeypatch.setattr(
        "scripts.run_agent_acceptance._test_activity_inference",
        lambda: {"status": "passed"},
    )
    monkeypatch.setattr(
        "scripts.run_agent_acceptance._test_docking_execution",
        lambda: {"status": "passed"},
    )

    report = run_real(repeat=2, case_set="diverse", dataset=None)

    assert report["status"] == "passed"
    assert captured["dataset_path"].name == "diverse_scientific_cases.jsonl"
    assert report["stability"]["run_count"] == 2
