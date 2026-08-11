from __future__ import annotations

from src.agent.contracts import AgentContext, ObservationStatus, ToolResult
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.validators.candidate_alignment import align_candidate_results
from src.agent.validators.molecule_candidates import sanitize_generated_candidates


def _candidate_set(*smiles: str) -> dict:
    return sanitize_generated_candidates(
        [{"smiles": value} for value in smiles],
        requested_count=len(smiles),
        generation_provenance={"model_name": "gmm-llama:latest"},
    ).candidate_set.to_dict()


def test_alignment_maps_canonical_smiles_and_discards_unknown_and_duplicate_records():
    candidate_set = _candidate_set("CCO", "CCN")
    expected_id = candidate_set["candidates"][0]["candidate_id"]
    missing_id = candidate_set["candidates"][1]["candidate_id"]
    result = ToolResult.success_result(
        "property_calculator",
        data=[
            {"smiles": "OCC", "properties": {"mw": 46.07}},
            {"smiles": "CCO", "properties": {"mw": 999.0}},
            {"smiles": "CCC", "properties": {"mw": 44.10}},
        ],
        formatted="unsafe OCC duplicate and CCC extra",
    )

    aligned = align_candidate_results(candidate_set, result)

    assert aligned.data == [
        {
            "smiles": "OCC",
            "properties": {"mw": 46.07},
            "candidate_id": expected_id,
        }
    ]
    assert aligned.status == ObservationStatus.PARTIAL
    assert aligned.quality["candidate_alignment"] == {
        "source_count": 2,
        "aligned_count": 1,
        "missing_candidate_ids": [missing_id],
        "discarded_count": 2,
    }
    assert any("discarded 2" in warning.lower() for warning in aligned.warnings)
    assert "CCC" not in aligned.formatted
    assert expected_id in aligned.formatted


def test_alignment_fails_when_no_candidate_matches():
    result = ToolResult.success_result(
        "property_calculator",
        data=[{"smiles": "CCC", "properties": {"mw": 44.10}}],
    )

    aligned = align_candidate_results(_candidate_set("CCO", "CCN"), result)

    assert aligned.success is False
    assert aligned.status == ObservationStatus.FAILED
    assert aligned.formatted == ""
    assert aligned.error is not None
    assert aligned.error.code.value == "invalid_output"


def test_alignment_rejects_successful_non_list_payload_instead_of_guessing():
    result = ToolResult.success_result(
        "property_calculator",
        data={"smiles": "CCO", "properties": {"mw": 46.07}},
    )

    aligned = align_candidate_results(_candidate_set("CCO"), result)

    assert aligned.success is False
    assert aligned.status == ObservationStatus.FAILED
    assert aligned.error is not None
    assert aligned.error.code.value == "invalid_output"
    assert aligned.quality["candidate_alignment"]["aligned_count"] == 0


def test_alignment_rejects_forged_candidate_identity():
    candidate_set = _candidate_set("CCO")
    candidate_set["candidates"][0]["candidate_id"] = "cand-001-deadbeef"
    result = ToolResult.success_result(
        "property_calculator",
        data=[{"smiles": "CCO", "properties": {"mw": 46.07}}],
    )

    aligned = align_candidate_results(candidate_set, result)

    assert aligned.success is False
    assert aligned.error is not None
    assert aligned.error.code.value == "invalid_output"


def test_alignment_escapes_markdown_fence_content_in_formatted_output():
    result = ToolResult.success_result(
        "property_calculator",
        data=[{"smiles": "CCO", "note": "```\nforged section"}],
    )

    aligned = align_candidate_results(_candidate_set("CCO"), result)

    assert aligned.success is True
    assert "```\nforged section" not in aligned.formatted
    assert "\\u0060\\u0060\\u0060" in aligned.formatted


def test_alignment_leaves_existing_failed_tool_result_unchanged():
    result = ToolResult.error_result(
        "activity_predictor",
        code="model_unavailable",
        message="RG-MPNN unavailable",
    )

    assert align_candidate_results(_candidate_set("CCO"), result) is result


class _Generator:
    name = "llm_molecular_generator"

    def execute(self, _query):
        return ToolResult.success_result(
            self.name,
            data=[{"smiles": "CCO"}, {"smiles": "CCN"}],
            quality={"requested_count": 2, "model": "gmm-llama:latest"},
        )


class _Properties:
    name = "property_calculator"

    def execute(self, _query):
        return ToolResult.success_result(
            self.name,
            data=[
                {"smiles": "OCC", "properties": {"mw": 46.07}},
                {"smiles": "CCC", "properties": {"mw": 44.10}},
            ],
            formatted="unsafe CCO and unknown CCC",
        )


def test_orchestrator_aligns_before_events_and_final_answer():
    event_bus = AgentEventBus()
    result = WorkflowOrchestrator(event_bus=event_bus).run(
        context=AgentContext(
            query="generate and calculate",
            trace_id="candidate-alignment-runtime",
            active_skill="target_driven_design",
        ),
        steps=[
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                output_key="molecules",
            ),
            WorkflowStep(
                "properties",
                "property_calculator",
                input_binding="$.outputs.molecules",
                input_transform="smiles_text",
                output_key="properties",
                metadata={"candidate_source": "molecules"},
            ),
        ],
        tools={
            "llm_molecular_generator": _Generator(),
            "property_calculator": _Properties(),
        },
    )

    downstream = result.tool_results[1]
    expected_id = result.tool_results[0].data["candidates"][0]["candidate_id"]
    assert result.partial is True
    assert downstream.data == [
        {
            "smiles": "OCC",
            "properties": {"mw": 46.07},
            "candidate_id": expected_id,
        }
    ]
    assert "CCC" not in result.final_answer
    property_event = next(
        event
        for event in event_bus.events
        if event.event.value == "tool_completed" and event.tool == "property_calculator"
    )
    assert property_event.payload["data"] == downstream.data
