from __future__ import annotations

import pytest

from src.agent.contracts import AgentContext, ToolResult
from src.agent.contracts.generation_request import (
    MAX_EVIDENCE_CHARS,
    serialize_target_evidence,
)
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.planning import WorkflowPlan
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.workflow_executor import WorkflowExecutor
from src.agent.validators.semantic_inputs import SemanticInputValidator
from src.agent.workflows import WorkflowCatalog


class CountingTool:
    def __init__(self, name: str, data, *, quality=None):
        self.name = name
        self.data = data
        self.quality = quality
        self.calls = 0
        self.inputs = []

    def execute(self, query):
        self.calls += 1
        self.inputs.append(query)
        quality = self.quality if self.quality is not None else (
            {"requested_count": len(self.data), "model": "gmm-llama:latest"}
            if self.name == "llm_molecular_generator"
            else {}
        )
        return ToolResult.success_result(
            self.name,
            data=self.data,
            message=f"{self.name} completed",
            quality=quality,
        )


def _target_design_plan() -> WorkflowPlan:
    return WorkflowPlan(
        workflow_name="target_driven_design",
        steps=[
            WorkflowStep(
                "target_search",
                "target_database_search",
                input_data="PDE5A",
                output_key="target",
                capability="target.structure.search",
            ),
            WorkflowStep(
                "molecule_generation",
                "llm_molecular_generator",
                input_binding="$.workflow",
                input_transform="identity",
                output_key="molecules",
                capability="molecule.generate",
                preconditions=("target_evidence",),
            ),
        ],
    )


def _run_target_design(target: CountingTool, generator: CountingTool):
    event_bus = AgentEventBus()
    execution = WorkflowExecutor(
        orchestrator=WorkflowOrchestrator(event_bus=event_bus)
    ).execute(
        context=AgentContext(
            query="Design PDE5A candidates",
            trace_id="semantic-target-design",
            active_skill="target_driven_design",
        ),
        policy=WorkflowCatalog().require("target_driven_design"),
        all_tools={
            "target_database_search": target,
            "llm_molecular_generator": generator,
        },
        plan=_target_design_plan(),
    )
    return execution, event_bus


def test_empty_target_records_skip_generation_and_return_partial():
    target = CountingTool("target_database_search", data=[])
    generator = CountingTool(
        "llm_molecular_generator", data=[{"smiles": "CCO"}]
    )

    execution, _ = _run_target_design(target, generator)

    assert target.calls == 1
    assert generator.calls == 0
    assert execution.result.partial is True
    assert execution.result.success is False
    assert execution.result.error is not None
    assert execution.result.error.code.value == "validation_error"
    assert execution.result.metadata["skipped_steps"] == [
        {
            "step_id": "molecule_generation",
            "status": "skipped_precondition",
            "requirement": "target_evidence",
            "reason": "target_evidence_missing",
        }
    ]
    assert [item.tool_name for item in execution.result.tool_results] == [
        "target_database_search"
    ]
    assert not any(
        event["event"] == "tool_started"
        and event["tool"] == "llm_molecular_generator"
        for event in execution.events
    )
    assert execution.events[-1]["event"] == "task_partial"


def test_structured_target_evidence_is_consumed_by_generator():
    target_data = [
        {
            "gene_symbol": "PDE5A",
            "source": "local_target_db",
            "recommended_structures": [{"structure_id": "1UDT"}],
        }
    ]
    target = CountingTool("target_database_search", data=target_data)
    generator = CountingTool(
        "llm_molecular_generator", data=[{"smiles": "CCO"}]
    )

    execution, _ = _run_target_design(target, generator)

    assert generator.calls == 1
    assert "PDE5A" in str(generator.inputs[0])
    assert "1UDT" in str(generator.inputs[0])
    assert execution.result.metadata["semantic_evidence"][0]["requirement"] == (
        "target_evidence"
    )
    assert len(execution.result.metadata["semantic_evidence"][0]["evidence_digest"]) == 64


def test_nested_provider_fallback_quality_survives_runtime_and_blocks_generation():
    target = CountingTool(
        "target_database_search",
        data=[{"gene_symbol": "PDE5A", "source": "UniProt"}],
        quality={"provider": {"fallback_used": True}},
    )
    generator = CountingTool(
        "llm_molecular_generator", data=[{"smiles": "CCO"}]
    )

    execution, _ = _run_target_design(target, generator)

    assert target.calls == 1
    assert generator.calls == 0
    assert execution.result.error.code.value == "validation_error"


def _oversized_canonical_target():
    token = "A" * 80
    label = "A" * 120
    nested = {
        "source": "UniProt",
        "database": "UniProt",
        "id": token,
        "source_record_id": token,
        "structure_id": token,
        "uniprot_id": token,
        "status": token,
        "artifact_type": token,
        "label": label,
    }
    structure = {
        "structure_id": token,
        "source": "PDB",
        "database": "PDB",
        "source_record_id": token,
        "structure_type": token,
        "method": token,
    }
    return {
        "gene_symbol": token,
        "target_identifier": token,
        "uniprot_id": token,
        "target_name": label,
        "target_id": token,
        "source_record_id": token,
        "target_type": token,
        "structure_evidence_status": token,
        "source": "UniProt",
        "database": "UniProt",
        "recommended_structures": [structure, structure],
        "evidence": [nested, nested, nested],
        "artifacts": [nested, nested, nested],
    }


def test_semantic_gate_rejects_evidence_that_cannot_fit_final_prompt_bound():
    payload = {
        "query": "design",
        "metadata": {"requested_count": 2},
        "outputs": {"target": _oversized_canonical_target()},
    }

    assert serialize_target_evidence(payload) is None
    decision = SemanticInputValidator().validate(
        _target_design_plan().steps[1], payload
    )

    assert decision.allowed is False
    assert decision.reason == "target_evidence_missing"


@pytest.mark.parametrize(
    "name_length, expected",
    [(120, True), (121, False), (6295, False)],
)
def test_semantic_gate_and_serializer_agree_at_text_boundaries(
    name_length, expected
):
    payload = {
        "query": "design",
        "metadata": {},
        "outputs": {
            "target": {"target_name": "A" * name_length, "database": "UniProt"}
        },
    }

    serialized = serialize_target_evidence(payload)
    decision = SemanticInputValidator().validate(
        _target_design_plan().steps[1], payload
    )

    assert (serialized is not None) is expected
    assert decision.allowed is expected
    if serialized is not None:
        assert len(serialized) <= MAX_EVIDENCE_CHARS


def test_workflow_wrapper_validates_only_outputs_target_evidence():
    validator = SemanticInputValidator()
    step = _target_design_plan().steps[1]
    payload = {
        "query": "Design PDE5A candidates",
        "metadata": {"requested_count": 3},
        "outputs": {
            "target": [
                {
                    "gene_symbol": "PDE5A",
                    "source": "UniProt",
                    "recommended_structures": [{"structure_id": "1UDT"}],
                }
            ],
            "unrelated": [
                {"gene_symbol": "SHOULD_NOT_BE_USED", "source": "untrusted"}
            ],
        },
    }

    decision = validator.validate(step, payload)

    assert decision.allowed is True
    assert decision.evidence_digest is not None


@pytest.mark.parametrize(
    "target",
    [
        {"target_name": "PDE5A", "database": "UniProt"},
        {
            "target_identifier": "O76074",
            "evidence": [{"source": "UniProt", "id": "O76074"}],
        },
    ],
)
def test_canonical_target_evidence_schemas_are_valid(target):
    decision = SemanticInputValidator().validate(
        _target_design_plan().steps[1],
        {"query": "design", "metadata": {}, "outputs": {"target": target}},
    )

    assert decision.allowed is True
    assert decision.evidence_digest is not None


@pytest.mark.parametrize(
    "target",
    [
        {
            "fallback_used": True,
            "targets": [{"gene_symbol": "PDE5A", "source": "UniProt"}],
        },
        {
            "quality": {"demo_mode": True},
            "results": [{"gene_symbol": "PDE5A", "source": "UniProt"}],
        },
        {
            "targets": [
                {
                    "gene_symbol": "PDE5A",
                    "source": "UniProt",
                    "recommended_structures": [
                        {
                            "structure_id": "1UDT",
                            "quality": {"fallback_used": True},
                        }
                    ],
                }
            ]
        },
        {
            "target_identifier": "O76074",
            "evidence": [
                {
                    "source": "UniProt",
                    "id": "O76074",
                    "untrusted": True,
                }
            ],
        },
    ],
)
def test_target_evidence_rejects_untrusted_flags_on_every_container(target):
    payload = {
        "query": "design",
        "metadata": {},
        "outputs": {"target": target},
    }

    decision = SemanticInputValidator().validate(
        _target_design_plan().steps[1], payload
    )

    assert decision.allowed is False
    assert decision.reason == "target_evidence_missing"


def test_target_evidence_rejects_untrusted_workflow_and_outputs_wrappers():
    valid_target = {"gene_symbol": "PDE5A", "source": "UniProt"}
    payloads = (
        {
            "query": "design",
            "metadata": {},
            "outputs": {"target": valid_target},
            "quality": {"fallback_used": True},
        },
        {
            "query": "design",
            "metadata": {},
            "outputs": {
                "target": valid_target,
                "quality": {"demo_mode": True},
            },
        },
    )

    for payload in payloads:
        decision = SemanticInputValidator().validate(
            _target_design_plan().steps[1], payload
        )
        assert decision.allowed is False
        assert decision.reason == "target_evidence_missing"


@pytest.mark.parametrize(
    "trust_field, trust_value",
    [
        ("untrusted", 1),
        ("trusted", 0),
        ("fallback_used", "maybe"),
        ("demo_mode", None),
        ("authoritative", {"value": True}),
    ],
)
def test_target_evidence_rejects_non_boolean_or_unsafe_trust_flags(
    trust_field, trust_value
):
    payload = {
        "query": "design",
        "metadata": {},
        "outputs": {
            "target": {
                "target_identifier": "O76074",
                "evidence": [
                    {
                        "source": "UniProt",
                        "id": "O76074",
                        "quality": {trust_field: trust_value},
                    }
                ],
            }
        },
    }

    decision = SemanticInputValidator().validate(
        _target_design_plan().steps[1], payload
    )

    assert decision.allowed is False
    assert decision.reason == "target_evidence_missing"


def test_target_evidence_accepts_explicit_safe_trust_booleans():
    payload = {
        "query": "design",
        "metadata": {},
        "outputs": {
            "target": {
                "gene_symbol": "PDE5A",
                "source": "UniProt",
                "quality": {
                    "demo_mode": False,
                    "fallback_used": "false",
                    "untrusted": "no",
                    "trusted": True,
                    "authoritative": "true",
                },
            }
        },
    }

    decision = SemanticInputValidator().validate(
        _target_design_plan().steps[1], payload
    )

    assert decision.allowed is True


def test_workflow_wrapper_rejects_missing_or_untrusted_target_evidence():
    validator = SemanticInputValidator()
    step = _target_design_plan().steps[1]
    invalid_payloads = (
        {"query": "design", "metadata": {}, "outputs": {}},
        {
            "query": "design",
            "metadata": {},
            "outputs": {
                "unrelated": [{"gene_symbol": "EGFR", "source": "UniProt"}]
            },
        },
        {
            "query": "design",
            "metadata": {},
            "outputs": {
                "target": [
                    {"gene_symbol": "PDE5A", "source": "local", "demo_mode": True}
                ]
            },
        },
        {
            "query": "design",
            "metadata": {},
            "outputs": {
                "target": [
                    {
                        "gene_symbol": "PDE5A",
                        "source": "local",
                        "fallback_used": True,
                    }
                ]
            },
        },
        {
            "query": "design",
            "metadata": {},
            "outputs": {
                "target": [{"gene_symbol": "PDE5A", "source": "unknown"}]
            },
        },
    )

    for payload in invalid_payloads:
        decision = validator.validate(step, payload)
        assert decision.allowed is False
        assert decision.reason == "target_evidence_missing"


def test_demo_or_placeholder_target_evidence_is_rejected():
    validator = SemanticInputValidator()
    step = _target_design_plan().steps[1]

    demo = validator.validate(
        step,
        [{"gene_symbol": "PDE5A", "source": "local", "demo_mode": True}],
    )
    placeholder = validator.validate(
        step,
        [{"gene_symbol": "PDE5A", "source": "None"}],
    )
    fallback = validator.validate(
        step,
        [
            {
                "gene_symbol": "PDE5A",
                "source": "local",
                "fallback_used": "true",
            }
        ],
    )

    assert demo.allowed is False
    assert demo.reason == "target_evidence_missing"
    assert placeholder.allowed is False
    assert placeholder.reason == "target_evidence_missing"
    assert fallback.allowed is False
    assert fallback.reason == "target_evidence_missing"


def test_workflow_step_requires_immutable_unique_preconditions():
    try:
        WorkflowStep(
            "generate",
            "llm_molecular_generator",
            preconditions=["target_evidence"],
        )
    except ValueError as exc:
        assert "tuple" in str(exc)
    else:
        raise AssertionError("mutable preconditions must be rejected")

    try:
        WorkflowStep(
            "generate",
            "llm_molecular_generator",
            preconditions=("target_evidence", "target_evidence"),
        )
    except ValueError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("duplicate preconditions must be rejected")
