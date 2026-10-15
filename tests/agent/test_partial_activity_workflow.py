"""Synthetic contract fixtures: these are not real scientific predictions."""
from copy import deepcopy

import pytest

from src.agent.contracts import AgentContext, AgentResult, ObservationStatus, ToolResult
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.validators import AgentResultValidator
from src.agent.validators.candidate_alignment import align_candidate_results
from src.agent.validators.molecule_candidates import sanitize_generated_candidates
from tests.agent.test_family_activity_tool import family_row


def mixed_rows():
    return [
        family_row(smiles="CCO"),
        family_row(smiles="CCN", success=False, status="partial", execution_status="passed",
                   predicted_pIC50=6.4, activity_probability=0.2,
                   classification_regression_consistent=False),
        family_row(smiles="CCC", success=False, status="partial", execution_status="passed",
                   predicted_pIC50=4.3, activity_probability=0.8, activity_class="有活性",
                   classification_regression_consistent=False),
    ]


def partial_result(rows=None):
    return ToolResult("activity_predictor", False, "Synthetic mixed batch",
                      data=mixed_rows() if rows is None else rows,
                      status=ObservationStatus.PARTIAL,
                      formatted="untrusted formatted output must be regenerated")


def candidates():
    return sanitize_generated_candidates(
        [{"smiles": row["smiles"]} for row in mixed_rows()], requested_count=3,
        generation_provenance={"model_name": "synthetic-test-model"},
    ).candidate_set.to_dict()


class OutputTool:
    def __init__(self, name, output):
        self.name, self.output, self.calls = name, output, []

    def execute(self, value):
        self.calls.append(deepcopy(value))
        return deepcopy(self.output)


def test_partial_family_rows_are_aligned_without_promoting_success():
    source = candidates()
    result = align_candidate_results(source, partial_result())
    assert [row.get("candidate_id") for row in result.data] == [
        row["candidate_id"] for row in source["candidates"]]
    assert result.success is False
    assert result.status is ObservationStatus.PARTIAL
    assert [row["predicted_pIC50"] for row in result.data] == [4.2, 6.4, 4.3]
    assert "需复核" in result.formatted
    assert "untrusted formatted" not in result.formatted


def test_partial_only_run_is_partial_and_retains_all_raw_values():
    tool = OutputTool("activity_predictor", partial_result())
    bus = AgentEventBus()
    result = WorkflowOrchestrator(event_bus=bus).run(
        AgentContext(query="synthetic activity batch", trace_id="partial-only"),
        [WorkflowStep("activity", tool.name, input_data="CCO CCN CCC")],
        {tool.name: tool},
    )
    assert result.outcome.value == "partial"
    assert result.partial is True and result.success is False
    assert all(str(value) in result.final_answer for value in (4.2, 6.4, 4.3, 0.2, 0.8))
    assert "需复核" in result.final_answer
    assert "untrusted formatted" not in result.final_answer
    terminal = [event for event in bus.events if event.tool == tool.name
                and event.event.value in {"tool_completed", "tool_failed"}]
    assert len(terminal) == 1
    assert terminal[0].event.value == "tool_completed"
    assert terminal[0].payload["status"] == "partial"


def test_partial_family_rows_reach_next_binding_including_required_step():
    source = OutputTool("candidate_source", ToolResult.success_result("candidate_source", data=candidates()))
    activity = OutputTool("activity_predictor", partial_result())
    consumer = OutputTool("inspect_activity", ToolResult.success_result("inspect_activity", data={}))
    result = WorkflowOrchestrator().run(
        AgentContext(query="synthetic workflow", trace_id="partial-binding"),
        [WorkflowStep("candidates", source.name, output_key="candidates"),
         WorkflowStep("activity", activity.name, output_key="activity", required=True,
                      metadata={"candidate_source": "candidates"}),
         WorkflowStep("consumer", consumer.name, input_binding="$.outputs.activity")],
        {tool.name: tool for tool in (source, activity, consumer)},
    )
    assert len(consumer.calls) == 1
    assert [row["predicted_pIC50"] for row in consumer.calls[0]] == [4.2, 6.4, 4.3]
    assert all(row.get("candidate_id") for row in consumer.calls[0])
    assert result.outcome.value == "partial"
    assert result.metadata["workflow_state"]["errors"] == []
    assert not any("failed" in warning.lower() for warning in result.warnings)


@pytest.mark.parametrize("mutation", [
    lambda rows: rows[1].update(provenance={}),
    lambda rows: rows[1].update(classification_regression_consistent=True),
    lambda rows: rows[1]["provenance"]["models"]["regression"].update(demo_mode=True),
    lambda rows: rows.append({"unexpected": "unvalidated row"}),
    lambda rows: rows[1].update(predicted_pIC50=float("nan")),
])
def test_forged_partial_batch_is_rejected_before_publication(mutation):
    rows = mixed_rows()
    mutation(rows)
    result = AgentResultValidator().validate_tool_result(partial_result(rows))
    assert result.status is ObservationStatus.FAILED
    assert result.error.code.value == "invalid_output"
    assert result.data is None
    assert result.formatted == ""


def test_partial_tag_alone_is_not_usable_scientific_evidence():
    result = partial_result([{"unverified_number": 8.8}])
    aggregate = AgentResult.from_tool_results("synthetic", None, [result])
    assert not aggregate.partial
    assert not aggregate.success


def test_top_level_demo_provenance_cannot_publish_partial_rows():
    result = partial_result()
    result.quality["demo_mode"] = True
    validated = AgentResultValidator().validate_tool_result(result)
    assert validated.status is ObservationStatus.FAILED
    assert validated.data is None


def test_existing_successful_canonical_partial_keeps_its_binding():
    from src.agent.validators.partial_observations import has_usable_observation

    row = {"smiles": "CCO", "success": True, "task_type": "regression",
           "endpoint": "pIC50", "units": "pIC50", "value": 4.2,
           "model_provenance": {"model_id": "synthetic", "weights_sha256": "a" * 64,
                                "task_type": "regression", "endpoint": "pIC50",
                                "units": "pIC50", "demo_mode": False, "fallback_used": False}}
    result = ToolResult.success_result("activity_predictor", data=[row], evidence=[{"prediction": row}],
                                       status=ObservationStatus.PARTIAL)
    result = AgentResultValidator().validate_tool_result(result)
    assert has_usable_observation(result)
    assert result.error is None
    result = align_candidate_results(candidates(), result)
    assert result.data[0]["candidate_id"]
    assert "4.2" in result.formatted


def test_partial_family_checkpoint_does_not_promote_review_rows_or_change_evidence_id():
    from src.agent.evidence import EvidenceLedger

    result = AgentResultValidator().validate_tool_result(partial_result())
    ledger = EvidenceLedger("synthetic-checkpoint")
    original_id = ledger.register_tool_result("activity", "synthetic-input", result)
    restored = WorkflowOrchestrator._result_from_checkpoint(
        "activity_predictor", {"status": "partial", "output": result.to_legacy_dict()})
    assert restored.success is False
    restored_id = EvidenceLedger("synthetic-checkpoint").register_tool_result(
        "activity", "synthetic-input", restored)
    assert restored_id == original_id
    assert restored.data == result.data


@pytest.mark.parametrize("extra_row", [False, True])
def test_alignment_partial_family_checkpoint_keeps_evidence_identity(extra_row):
    from src.agent.evidence import EvidenceLedger

    rows = [family_row(smiles="CCO"), family_row(smiles="CCN")]
    if extra_row:
        rows += [family_row(smiles="CCC"), family_row(smiles="CCCC")]
    result = ToolResult.success_result("activity_predictor", data=rows)
    result = AgentResultValidator().validate_tool_result(result)
    assert result.success
    result = align_candidate_results(candidates(), result)
    assert result.status is ObservationStatus.PARTIAL
    before = deepcopy(result.data)
    original_id = EvidenceLedger("alignment-checkpoint").register_tool_result(
        "activity", "synthetic-input", result)
    restored = WorkflowOrchestrator._result_from_checkpoint(
        "activity_predictor", {"status": "partial", "output": result.to_legacy_dict()})
    restored = AgentResultValidator().validate_tool_result(restored)
    restored_id = EvidenceLedger("alignment-checkpoint").register_tool_result(
        "activity", "synthetic-input", restored)
    assert original_id == restored_id
    assert result.success is restored.success is False
    assert restored.data == before


def test_malformed_partial_checkpoint_cannot_hide_its_schema_and_revive_prose():
    result = partial_result()
    for row in result.data:
        for field in ("family_id", "predicted_pIC50", "activity_probability", "activity_class"):
            row.pop(field)
    restored = WorkflowOrchestrator._result_from_checkpoint(
        "activity_predictor", {"status": "partial", "output": result.to_legacy_dict()})
    restored = AgentResultValidator().validate_tool_result(restored)
    assert restored.success is False
    assert restored.error.code.value == "invalid_output"
    assert restored.data is None and restored.formatted == ""


def test_failed_canonical_shaped_checkpoint_is_not_an_observed_prediction():
    result = partial_result()
    for row in result.data:
        for field in ("family_id", "predicted_pIC50", "activity_probability", "activity_class"):
            row.pop(field)
        row.update(task_type="regression", success=False, error="synthetic stage failure")
    result.evidence = [{"unverified": "not a prediction"}]
    restored = WorkflowOrchestrator._result_from_checkpoint(
        "activity_predictor", {"status": "partial", "output": result.to_legacy_dict()})
    restored = AgentResultValidator().validate_tool_result(restored)
    assert restored.success is False
    assert restored.error.code.value == "invalid_output"
    assert restored.data is None and restored.formatted == ""


def test_valid_canonical_partial_checkpoint_still_revalidates_and_aligns():
    row = {"smiles": "CCO", "success": True, "task_type": "regression",
           "endpoint": "pIC50", "units": "pIC50", "value": 4.2,
           "model_provenance": {"model_id": "synthetic", "weights_sha256": "a" * 64,
                                "task_type": "regression", "endpoint": "pIC50",
                                "units": "pIC50", "demo_mode": False, "fallback_used": False}}
    result = ToolResult.success_result("activity_predictor", data=[row], evidence=[{"prediction": row}],
                                       status=ObservationStatus.PARTIAL)
    restored = WorkflowOrchestrator._result_from_checkpoint(
        "activity_predictor", {"status": "partial", "output": result.to_legacy_dict()})
    restored = AgentResultValidator().validate_tool_result(restored)
    assert restored.error is None
    assert align_candidate_results(candidates(), restored).data[0]["candidate_id"]


def test_real_activity_adapter_and_ranker_keep_mixed_batch_through_public_report(monkeypatch):
    from src.activity import prediction_service
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    from src.agent.tools.candidate_ranker import CandidateRanker
    from src.web.agent_result_presentation import partial_projection
    from tests.agent.test_candidate_ranker import _payload

    # Stub only model inference; execute real adapter, validation, binding and ranking.
    rows = mixed_rows()
    monkeypatch.setattr(prediction_service, "predict_activity", lambda *args, **kwargs: {
        "success": False, "status": "partial", "results": deepcopy(rows), "warnings": [],
    })
    source = OutputTool("candidate_source", ToolResult.success_result("candidate_source", data=candidates()))
    properties = OutputTool("property_calculator", ToolResult.success_result(
        "property_calculator", data=_payload()["outputs"]["properties"]))
    activity, ranker = ActivityPredictorTool(), CandidateRanker()
    metadata = {"docking_top_n": 3, "required_evidence": ["activity"], "target": "PDE5A"}
    result = WorkflowOrchestrator().run(
        AgentContext("synthetic mixed batch", "mixed-public-report", metadata=metadata),
        [WorkflowStep("candidates", source.name, output_key="molecules"),
         WorkflowStep("properties", properties.name, output_key="properties"),
         WorkflowStep("activity", activity.name, output_key="activity", required=False,
                      input_data={"smiles": ["CCO", "CCN", "CCC"], "target": "PDE5A"},
                      metadata={"candidate_source": "molecules"}),
         WorkflowStep("ranking", ranker.name, input_binding="$.workflow", metadata={
             "workflow_output_keys": ("molecules", "properties", "activity"),
             "workflow_metadata_keys": tuple(metadata),
         })],
        {tool.name: tool for tool in (source, properties, activity, ranker)},
    )
    assert result.outcome.value == "partial"
    ranking = result.tool_results[-1].data["ranked_candidates"]
    assert sum(row["ranking_evidence"]["activity"]["used"] for row in ranking) == 1
    assert [row["ranking_evidence"]["activity"]["exclusion_reason"] for row in ranking].count(
        "classification_regression_conflict") == 2
    assert result.tool_results[2].data[1]["predicted_pIC50"] == 6.4
    projected = partial_projection(result.to_legacy_dict())
    assert all(str(value) in projected["content"] for value in (4.2, 6.4, 4.3, 0.2, 0.8))
    assert "需复核" in projected["content"]
    assert projected["failed_steps"][0]["status"] == "partial"


def test_partial_observations_survive_a_later_semantic_precondition_rejection():
    activity = OutputTool("activity_predictor", partial_result())
    dependent = OutputTool("consumer", ToolResult.success_result("consumer", data={}))
    result = WorkflowOrchestrator().run(
        AgentContext("synthetic semantic guard", "partial-before-precondition"),
        [WorkflowStep("activity", activity.name, required=False),
         WorkflowStep("consumer", dependent.name, input_data=[], preconditions=("target_evidence",))],
        {tool.name: tool for tool in (activity, dependent)},
    )
    assert dependent.calls == []
    assert result.outcome.value == "partial" and result.partial is True
    assert "6.4" in result.final_answer
    assert "partial results" in result.message
