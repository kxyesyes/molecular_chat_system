from __future__ import annotations

from scripts.run_agent_acceptance import (
    _evaluate_activity_result,
    _evaluate_docking_result,
    _evaluate_generation_result,
    _resolve_real_dataset,
    run_replay,
    run_real,
)
from src.agent.evaluation import scientific
from src.agent.evaluation.models import EvaluationCase


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
            {"tool_name": "property_calculator", "success": True},
            {"tool_name": "admet_predictor", "success": True},
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
