from dataclasses import FrozenInstanceError

import pytest

from src.agent.workflows import WorkflowCatalog, WorkflowPolicy


EXPECTED_WORKFLOWS = {
    "admet_assessment",
    "activity_prediction",
    "reverse_target_prediction",
    "target_database_search",
    "molecular_design",
    "docking_simulation",
    "comprehensive_evaluation",
    "hit_to_lead_optimization",
    "target_driven_design",
    "rag_search",
}


def test_catalog_declares_each_supported_workflow_once():
    catalog = WorkflowCatalog()
    names = [policy.name for policy in catalog.policies]

    assert isinstance(catalog.policies, tuple)
    assert set(names) == EXPECTED_WORKFLOWS
    assert len(names) == len(EXPECTED_WORKFLOWS)
    assert len(names) == len(set(names))


def test_target_design_policy_preserves_tool_allowlist():
    policy = WorkflowCatalog().require("target_driven_design")

    assert policy.allowed_tools == (
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "candidate_ranker",
        "molecular_docking",
    )
    assert policy.is_multi_step is True


def test_policy_is_immutable():
    policy = WorkflowCatalog().require("molecular_design")

    with pytest.raises(FrozenInstanceError):
        policy.name = "changed"


def test_catalog_policy_collection_is_immutable():
    catalog = WorkflowCatalog()
    replacement = (WorkflowPolicy("replacement", "Replacement policy", ()),)

    with pytest.raises(AttributeError):
        catalog.policies = replacement


def test_catalog_rejects_duplicate_policy_names():
    first = WorkflowPolicy("duplicate", "First policy", ("first_tool",))
    second = WorkflowPolicy("duplicate", "Second policy", ("second_tool",))

    with pytest.raises(ValueError, match="Workflow policy names must be unique"):
        WorkflowCatalog((first, second))


def test_get_and_require_resolve_known_and_unknown_workflows():
    catalog = WorkflowCatalog()
    policy = catalog.require("molecular_design")

    assert catalog.get("molecular_design") is policy
    assert catalog.get("unknown") is None
    with pytest.raises(KeyError) as exc_info:
        catalog.require("unknown")
    assert exc_info.value.args == ("Unknown workflow: unknown",)


def test_llm_catalog_returns_numbered_name_description_lines():
    policies = (
        WorkflowPolicy("first", "First description.", ()),
        WorkflowPolicy("second", "Second description.", ("tool",), True),
    )

    assert WorkflowCatalog(policies).llm_catalog() == (
        "1. **first**: First description.\n"
        "2. **second**: Second description."
    )
