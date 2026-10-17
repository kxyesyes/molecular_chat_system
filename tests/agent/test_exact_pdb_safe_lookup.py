"""Synthetic service contracts: exact lookup must not authorize docking."""

import pytest

from src.agent.contracts import AgentContext
from src.agent.planning.task_planner import TaskPlanner
from src.agent.routing import HybridSkillRouter
from src.agent.tools.target_database_tool import TargetDatabaseTool


SAFE_LOOKUPS = (
    "查找 BCHE 的 PDB 6SAM，不要进行对接",
    "Find BCHE PDB 6SAM without docking",
)


class FakeTargetService:
    def __init__(self):
        self.calls = []

    def search_targets(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return {"status": "resolved", "lookup_path": ["fixture"], "results": [{
            "gene_symbol": "BCHE", "uniprot_id": "P06276",
            "recommended_structures": [{"structure_id": "6SAM", "source": "fixture"}],
        }]}


@pytest.fixture(autouse=True)
def no_real_service_or_transport(monkeypatch):
    import requests

    def unexpected(*args, **kwargs):
        pytest.fail("safe lookup contracts must not use a real service or network")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", unexpected)
    monkeypatch.setattr("src.target_search.service.TargetSearchService.__init__", unexpected)


@pytest.mark.parametrize("query", SAFE_LOOKUPS)
def test_actual_wrapper_resolves_exact_lookup_with_fake_service(query):
    service = FakeTargetService()
    tool = TargetDatabaseTool()
    tool._service = service

    result = tool.execute(query)

    assert result["success"], result
    assert result["status"] == "resolved"
    assert service.calls == [("BCHE", {"pdb_id": "6SAM"})]
    assert result["data"][0]["recommended_structures"][0]["structure_id"] == "6SAM"


@pytest.mark.parametrize("query", SAFE_LOOKUPS)
def test_planner_preserves_original_negative_instruction_and_only_plans_lookup(query):
    context = AgentContext(query=query, active_skill="target_database_search", trace_id="fixture")
    plan = TaskPlanner().plan(context)

    assert context.query == query
    assert [step.tool_name for step in plan.steps] == ["target_database_search"]
    assert plan.steps[0].input_data == query


@pytest.mark.parametrize("query", SAFE_LOOKUPS)
def test_router_and_resulting_plan_never_authorize_docking(query):
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill == "target_database_search"
    assert not decision.requires_confirmation
    assert not any("dock" in tool for tool in decision.allowed_tools)

    plan = TaskPlanner().plan(AgentContext(
        query=query, active_skill=decision.selected_skill, trace_id="fixture",
    ))
    assert [step.tool_name for step in plan.steps] == ["target_database_search"]
    assert plan.steps[0].input_data == query


@pytest.mark.parametrize("query", [
    "不要查找 BCHE PDB: 6SAM，不要进行对接",
    "Do not look up BCHE PDB: 6SAM without docking",
    "不要查PDB6SAM", "PDB6SAM不要查", "not PDB6SAM",
    "Find BCHE PDB: 6SAM or 1P0I without docking",
    "Find BCHE PDB: 6SAM or PDB1P0I without docking",
    "Find BCHE PDB: 6SAM or PDB ID1P0I without docking",
])
def test_actual_wrapper_rejects_negative_or_ambiguous_lookup_before_service(query):
    service = FakeTargetService()
    tool = TargetDatabaseTool()
    tool._service = service

    result = tool.execute(query)

    assert not result["success"]
    assert result["error"]["code"] == "validation_error"
    assert service.calls == []
